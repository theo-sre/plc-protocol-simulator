"""Serveur BACnet/IP : expose les tags comme objets Analog Value / Binary Value.

Les valeurs numeriques deviennent des objets ``analog-value`` (present-value
REAL), les booleens des objets ``binary-value`` (active / inactive). Le
serveur repond aux Who-Is, ReadProperty, ReadPropertyMultiple et WriteProperty.

BACnet MS/TP n'est pas gere : c'est un bus serie RS-485, pas de l'Ethernet.
"""

from __future__ import annotations

import logging
import socket
from typing import Any, Dict, List

try:
    from bacpypes3.app import Application
    from bacpypes3.local.analog import AnalogValueObject
    from bacpypes3.local.binary import BinaryValueObject
    from bacpypes3.local.device import DeviceObject
    from bacpypes3.local.networkport import NetworkPortObject
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "bacpypes3 est requis pour le serveur BACnet.\n"
        "  python -m pip install bacpypes3\n"
        "(ou mettre bacnet.enabled a false dans config.yaml)\n"
        f"Detail : {exc}"
    ) from exc

from .config import BacnetConfig
from .engine import Sink, Tag

log = logging.getLogger("bacnet")

# Correspondance unite du tag -> EngineeringUnits BACnet
UNITS = {
    "degc": "degrees-celsius", "°c": "degrees-celsius", "c": "degrees-celsius",
    "degf": "degrees-fahrenheit", "°f": "degrees-fahrenheit",
    "k": "degrees-kelvin",
    "%": "percent", "pct": "percent",
    "bar": "bars", "bars": "bars", "pa": "pascals", "kpa": "kilopascals",
    "psi": "pounds-force-per-square-inch",
    "m3/h": "cubic-meters-per-hour", "l/s": "liters-per-second",
    "l/min": "liters-per-minute",
    "kw": "kilowatts", "w": "watts", "mw": "megawatts",
    "kwh": "kilowatt-hours", "wh": "watt-hours",
    "v": "volts", "a": "amperes", "ma": "milliamperes", "hz": "hertz",
    "tr/min": "revolutions-per-minute", "rpm": "revolutions-per-minute",
    "s": "seconds", "ms": "milliseconds", "min": "minutes", "h": "hours",
    "m": "meters", "mm": "millimeters", "cm": "centimeters", "km": "kilometers",
    "kg": "kilograms", "g": "grams", "t": "tons",
    "ppm": "parts-per-million", "lux": "luxes",
}


def primary_ip() -> str:
    """IP de la carte reseau utilisee pour sortir du PC."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def bacnet_units(unit: str) -> str:
    return UNITS.get((unit or "").strip().lower(), "no-units")


class BacnetSink(Sink):
    name = "bacnet"

    def __init__(self, cfg: BacnetConfig):
        self.cfg = cfg
        self.app: Application | None = None
        self.objects: Dict[str, Any] = {}
        self._last: Dict[str, Any] = {}
        self._expected: Dict[str, Any] = {}
        self._tags: List[Tag] = []
        self.host = ""
        self._next = {"analog-value": 1, "binary-value": 1}

    # -- allocation des numeros d'instance --------------------------------
    def _assign_instance(self, tag: Tag) -> int:
        kind = "binary-value" if tag.cfg.is_bool else "analog-value"
        if tag.cfg.bacnet_instance is not None:
            return tag.cfg.bacnet_instance
        used = {t.cfg.bacnet_instance for t in self._tags
                if t.cfg.is_bool == tag.cfg.is_bool}
        inst = self._next[kind]
        while inst in used:
            inst += 1
        self._next[kind] = inst + 1
        tag.cfg.bacnet_instance = inst
        return inst

    def _make_object(self, tag: Tag):
        inst = self._assign_instance(tag)
        desc = tag.cfg.description or tag.name
        if tag.cfg.is_bool:
            obj = BinaryValueObject(
                objectIdentifier=("binary-value", inst),
                objectName=tag.name,
                presentValue="inactive",
                description=desc,
                statusFlags=[0, 0, 0, 0],
                eventState="normal",
                outOfService=False,
            )
        else:
            obj = AnalogValueObject(
                objectIdentifier=("analog-value", inst),
                objectName=tag.name,
                presentValue=0.0,
                description=desc,
                units=bacnet_units(tag.cfg.unit),
                statusFlags=[0, 0, 0, 0],
                eventState="normal",
                covIncrement=0.1,
                outOfService=False,
            )
        self.objects[tag.name] = obj
        return obj

    # -- cycle de vie -----------------------------------------------------
    async def start(self, tags: List[Tag]) -> None:
        self._tags = [t for t in tags if t.cfg.bacnet]

        # 0.0.0.0 : ecoute sur toutes les interfaces, y compris les VPN type
        # Tailscale. C'est le defaut, aligne sur Modbus et OPC UA.
        # auto : uniquement la carte qui porte la route par defaut -- pratique
        # pour n'exposer le BACnet que sur un reseau precis.
        raw = (self.cfg.host or "").strip().lower()
        if raw in ("", "0.0.0.0", "all", "toutes"):
            self.all_interfaces = True
            self.host = "0.0.0.0"
            bind = "0.0.0.0/0"
        else:
            self.all_interfaces = False
            self.host = primary_ip() if raw == "auto" else self.cfg.host
            bind = f"{self.host}/{self.cfg.prefix_length}"

        device = DeviceObject(
            objectIdentifier=("device", self.cfg.device_id),
            objectName=self.cfg.device_name,
            vendorIdentifier=self.cfg.vendor_id,
            vendorName=self.cfg.vendor_name,
            modelName=self.cfg.model_name,
            description=self.cfg.description,
        )
        port_object = NetworkPortObject(
            f"{bind}:{self.cfg.port}",
            objectIdentifier=("network-port", 1),
            objectName="NetworkPort-1",
        )

        # Les instances sont attribuees dans l'ordre de declaration : elles
        # restent stables tant que l'ordre du fichier ne change pas.
        objects = [device, port_object] + [self._make_object(t) for t in self._tags]
        self.app = Application.from_object_list(objects)

        log.info(
            "Serveur BACnet/IP en ecoute sur %s:%s%s (device %s '%s', %s objets)",
            self.host, self.cfg.port,
            " (toutes les interfaces)" if self.all_interfaces else "",
            self.cfg.device_id, self.cfg.device_name, len(self._tags),
        )

    async def publish(self, tags: List[Tag]) -> None:
        for tag in self._tags:
            value = tag.value
            if tag.name in self._last and self._last[tag.name] == value:
                continue
            obj = self.objects.get(tag.name)
            if obj is None:
                continue
            try:
                if tag.cfg.is_bool:
                    obj.presentValue = "active" if value else "inactive"
                else:
                    obj.presentValue = float(value)
                self._last[tag.name] = value
                self._expected[tag.name] = self._read(tag)
            except Exception:
                log.exception("Ecriture BACnet impossible pour %s", tag.name)

    def _read(self, tag: Tag) -> Any:
        obj = self.objects[tag.name]
        if tag.cfg.is_bool:
            return str(obj.presentValue) == "active"
        return float(obj.presentValue)

    async def poll_writes(self) -> Dict[str, Any]:
        """Detecte les WriteProperty faits par un client BACnet."""
        changes: Dict[str, Any] = {}
        for tag in self._tags:
            if not tag.cfg.writable or tag.name not in self._expected:
                continue
            try:
                current = self._read(tag)
            except Exception:
                continue
            if current != self._expected[tag.name]:
                changes[tag.name] = current
                self._expected[tag.name] = current
        return changes

    # -- ajout / suppression a chaud --------------------------------------
    async def add_tag(self, tag: Tag) -> None:
        if not tag.cfg.bacnet or tag.name in self.objects:
            return
        self._tags.append(tag)
        obj = self._make_object(tag)
        self.app.add_object(obj)

    async def remove_tag(self, tag: Tag) -> None:
        self._tags = [t for t in self._tags if t.name != tag.name]
        self._last.pop(tag.name, None)
        self._expected.pop(tag.name, None)
        obj = self.objects.pop(tag.name, None)
        if obj is not None and self.app is not None:
            try:
                self.app.delete_object(obj)
            except Exception:
                log.exception("Suppression de l'objet BACnet %s impossible", tag.name)

    def describe(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "all_interfaces": getattr(self, "all_interfaces", False),
            "port": self.cfg.port,
            "device_id": self.cfg.device_id,
            "device_name": self.cfg.device_name,
            "vendor_id": self.cfg.vendor_id,
        }

    async def stop(self) -> None:
        if self.app is not None:
            try:
                self.app.close()
            except Exception:  # pragma: no cover
                pass
