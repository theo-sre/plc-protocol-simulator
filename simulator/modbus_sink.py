"""Serveur Modbus TCP : expose les tags en coils / holding registers.

Ecrit pour l'API SimData / SimDevice de pymodbus (>= 3.14), qui remplace
l'ancien ModbusSlaveContext / ModbusSequentialDataBlock.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

try:
    from pymodbus.server import ModbusTcpServer
    from pymodbus.simulator import DataType, SimData, SimDevice
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "pymodbus >= 3.14 est requis (API SimData/SimDevice).\n"
        "  python -m pip install --upgrade \"pymodbus>=3.14\"\n"
        f"Detail : {exc}"
    ) from exc

try:
    from pymodbus.pdu.device import ModbusDeviceIdentification
except ImportError:  # pragma: no cover - versions plus anciennes
    ModbusDeviceIdentification = None

from .config import ModbusConfig, decode, encode
from .engine import Sink, Tag

log = logging.getLogger("modbus")

# Codes fonction utilises pour acceder aux 4 blocs cote serveur
FC_READ_COILS = 1
FC_READ_DISCRETE = 2
FC_READ_HOLDING = 3
FC_READ_INPUT = 4
FC_WRITE_COILS = 15
FC_WRITE_HOLDING = 16


class ModbusSink(Sink):
    name = "modbus"

    def __init__(self, cfg: ModbusConfig):
        self.cfg = cfg
        self.server: ModbusTcpServer | None = None
        self._task: asyncio.Task | None = None
        self._last: Dict[str, Any] = {}      # derniere valeur poussee par le moteur
        self._expected: Dict[str, Any] = {}  # ce que doit relire le datastore
        self._tags: List[Tag] = []

    # -- construction du modele de donnees ---------------------------------
    def _build_device(self, tags: List[Tag], identity) -> SimDevice:
        """Cree un device a 4 blocs distincts (coils, discrete, holding, input).

        Avec des blocs distincts, chaque coil est adresse au bit, et les
        registres sont adresses au registre : c'est l'adressage attendu par les
        clients Modbus classiques.
        """
        n_bits = 16
        n_regs = 1
        for tag in tags:
            if not tag.cfg.modbus:
                continue
            if tag.cfg.is_bool:
                n_bits = max(n_bits, tag.cfg.address + 1)
            else:
                n_regs = max(n_regs, tag.cfg.address + tag.cfg.word_count)

        n_bits = max(n_bits, self.cfg.size)
        n_regs = max(n_regs, self.cfg.size)
        n_bits += (-n_bits) % 16      # multiple de 16 exige pour un bloc de bits

        coils = [SimData(address=0, count=n_bits, values=False, datatype=DataType.BITS)]
        discrete = [SimData(address=0, count=n_bits, values=False, datatype=DataType.BITS)]
        holding = [SimData(address=0, count=n_regs, values=0, datatype=DataType.REGISTERS)]
        inputs = [SimData(address=0, count=n_regs, values=0, datatype=DataType.REGISTERS)]

        self.n_bits, self.n_regs = n_bits, n_regs
        return SimDevice(
            id=self.cfg.unit_id,
            simdata=(coils, discrete, holding, inputs),
            identity=identity,
        )

    # -- cycle de vie -----------------------------------------------------
    async def start(self, tags: List[Tag]) -> None:
        self._tags = [t for t in tags if t.cfg.modbus]

        identity = None
        if ModbusDeviceIdentification is not None:
            identity = ModbusDeviceIdentification()
            identity.VendorName = "DataTest"
            identity.ProductCode = "SIM"
            identity.ProductName = "Simulateur de valeurs fictives"
            identity.ModelName = "DataTest Simulator"
            identity.MajorMinorRevision = "1.0.0"

        device = self._build_device(self._tags, identity)
        self.server = ModbusTcpServer(
            device,
            address=(self.cfg.host, self.cfg.port),
            identity=identity,
        )
        # background=True : lance l'ecoute puis rend la main immediatement.
        await self.server.serve_forever(background=True)
        log.info(
            "Serveur Modbus TCP en ecoute sur %s:%s (unit id %s, %s coils / %s registres)",
            self.cfg.host, self.cfg.port, self.cfg.unit_id, self.n_bits, self.n_regs,
        )

    async def _set(self, fc: int, address: int, values: list) -> None:
        await self.server.async_setValues(self.cfg.unit_id, fc, address, values)

    async def _get(self, fc: int, address: int, count: int) -> list:
        return await self.server.async_getValues(self.cfg.unit_id, fc, address, count)

    async def publish(self, tags: List[Tag]) -> None:
        order = self.cfg.word_order
        for tag in self._tags:
            value = tag.value
            if tag.name in self._last and self._last[tag.name] == value:
                continue
            self._last[tag.name] = value
            addr = tag.cfg.address
            try:
                if tag.cfg.is_bool:
                    await self._set(FC_WRITE_COILS, addr, [bool(value)])
                    if self.cfg.mirror_discrete_inputs:
                        await self._set(FC_READ_DISCRETE, addr, [bool(value)])
                    self._expected[tag.name] = bool(value)
                else:
                    words = encode(tag.cfg.dtype, value, order)
                    await self._set(FC_WRITE_HOLDING, addr, words)
                    if self.cfg.mirror_input_registers:
                        await self._set(FC_READ_INPUT, addr, words)
                    # On memorise la valeur telle qu'elle sera relue : un float32
                    # ne revient pas bit a bit identique a la valeur interne.
                    self._expected[tag.name] = decode(tag.cfg.dtype, words, order)
            except Exception:
                log.exception("Ecriture Modbus impossible pour %s (adresse %s)",
                              tag.name, addr)

    async def poll_writes(self) -> Dict[str, Any]:
        """Detecte les ecritures faites par un client (FC 5/6/15/16)."""
        changes: Dict[str, Any] = {}
        order = self.cfg.word_order
        for tag in self._tags:
            if not tag.cfg.writable or tag.name not in self._expected:
                continue
            addr = tag.cfg.address
            try:
                if tag.cfg.is_bool:
                    current = bool((await self._get(FC_READ_COILS, addr, 1))[0])
                else:
                    words = await self._get(FC_READ_HOLDING, addr, tag.cfg.word_count)
                    current = decode(tag.cfg.dtype, words, order)
            except Exception:
                continue
            if current != self._expected[tag.name]:
                changes[tag.name] = current
                self._expected[tag.name] = current
        return changes

    # -- ajout / suppression a chaud --------------------------------------
    async def add_tag(self, tag: Tag) -> None:
        if not tag.cfg.modbus or tag in self._tags:
            return
        limit = self.n_bits if tag.cfg.is_bool else self.n_regs
        end = tag.cfg.address + max(1, tag.cfg.word_count)
        if end > limit:
            raise ValueError(
                f"adresse {tag.cfg.address} hors de la table Modbus (taille {limit})"
            )
        self._tags.append(tag)

    async def remove_tag(self, tag: Tag) -> None:
        self._tags = [t for t in self._tags if t.name != tag.name]
        self._last.pop(tag.name, None)
        self._expected.pop(tag.name, None)
        if not tag.cfg.modbus:
            return
        addr = tag.cfg.address
        try:    # on remet la zone a zero pour ne pas laisser une valeur fantome
            if tag.cfg.is_bool:
                await self._set(FC_WRITE_COILS, addr, [False])
                if self.cfg.mirror_discrete_inputs:
                    await self._set(FC_READ_DISCRETE, addr, [False])
            else:
                zeros = [0] * tag.cfg.word_count
                await self._set(FC_WRITE_HOLDING, addr, zeros)
                if self.cfg.mirror_input_registers:
                    await self._set(FC_READ_INPUT, addr, zeros)
        except Exception:
            log.exception("Remise a zero impossible pour %s", tag.name)

    def describe(self) -> Dict[str, Any]:
        return {
            "host": self.cfg.host,
            "port": self.cfg.port,
            "unit_id": self.cfg.unit_id,
            "word_order": self.cfg.word_order,
            "coils": getattr(self, "n_bits", 0),
            "registers": getattr(self, "n_regs", 0),
            "mirror_input_registers": self.cfg.mirror_input_registers,
            "mirror_discrete_inputs": self.cfg.mirror_discrete_inputs,
        }

    async def stop(self) -> None:
        if self.server is not None:
            try:
                await self.server.shutdown()
            except Exception:  # pragma: no cover
                pass
