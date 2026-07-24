"""Serveur S7comm (ISO-on-TCP / RFC1006, port 102) : le PC se fait passer pour
un automate Siemens et expose les tags dans un bloc de donnees (DB).

Organisation du DB :

    octets 0 .. bool_bytes-1   : les booleens, un bit chacun  -> DBn.X<octet>.<bit>
    octets bool_bytes ..       : les valeurs numeriques, alignees sur 2 octets
                                 -> DBn.I / W / DI / DW / R / LR <offset>

Tout est code en big-endian, comme sur un vrai automate S7.
"""

from __future__ import annotations

import logging
import struct
from typing import Any, Dict, List

try:
    import snap7
    from snap7.server import Server, ServerISOConnection
    from snap7.type import Area, SrvArea
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "python-snap7 est requis pour le serveur S7.\n"
        "  python -m pip install python-snap7\n"
        "(ou mettre s7.enabled a false dans config.yaml)\n"
        f"Detail : {exc}"
    ) from exc

from .config import S7Config
from .engine import Sink, Tag

log = logging.getLogger("s7")

# format struct (big-endian) et taille en octets par type
FORMATS = {
    "int16": (">h", 2),
    "uint16": (">H", 2),
    "int32": (">i", 4),
    "uint32": (">I", 4),
    "float32": (">f", 4),
    "float64": (">d", 8),
}

# code de type dans l'adressage S7 / Telegraf
S7_CODE = {
    "bool": "X",
    "int16": "I",     # INT
    "uint16": "W",    # WORD
    "int32": "DI",    # DINT
    "uint32": "DW",   # DWORD
    "float32": "R",   # REAL
    "float64": "LR",  # LREAL (S7-1200/1500 uniquement)
}


# ---------------------------------------------------------------------------
#  Correctif COTP : Connection Confirm conforme
#
#  Le serveur pur Python de python-snap7 (3.1.0) repond au Connection Request
#  par un CC nu de 11 octets : il ignore la partie variable du CR et n'emet
#  aucun parametre.
#
#      03 00 00 0b  06 d0 00 01 00 01 00
#
#  Un automate Siemens reel repond par un CC de 22 octets qui reprend la
#  taille de TPDU (C0) et les deux TSAP (C1 = appelant, C2 = appele) :
#
#      03 00 00 16  11 d0 <dst_ref> <src_ref> 00  c0 01 0a  c1 02 xx xx  c2 02 xx xx
#
#  snap7 (et gos7, son portage Go utilise par le plugin Telegraf s7comm)
#  verifient strictement la longueur du CC et rejettent le CC tronque avec
#  « ISO : Invalid PDU received ». Les clients laxistes, eux, passent — d'ou
#  un serveur qui repond correctement aux lectures mais reste inaccessible a
#  Telegraf.
#
#  On corrige a l'execution, sans modifier site-packages : le CR est reparse
#  pour recuperer ses parametres, et le CC les reprend comme le ferait un
#  vrai automate.
# ---------------------------------------------------------------------------

COTP_CC = 0xD0
P_TPDU_SIZE = 0xC0      # taille de TPDU (exposant de 2)
P_SRC_TSAP = 0xC1       # TSAP appelant
P_DST_TSAP = 0xC2       # TSAP appele


def parse_cotp_params(payload: bytes) -> Dict[int, bytes]:
    """Partie variable d'un COTP CR : suite de triplets (code, longueur, valeur)."""
    params: Dict[int, bytes] = {}
    end = min(len(payload), payload[0] + 1) if payload else 0
    i = 7                                   # apres LI, type, dst_ref, src_ref, classe
    while i + 2 <= end:
        code, length = payload[i], payload[i + 1]
        value = payload[i + 2:i + 2 + length]
        if len(value) < length:
            break
        params[code] = value
        i += 2 + length
    return params


def build_cotp_cc(dst_ref: int, src_ref: int, params: Dict[int, bytes]) -> bytes:
    """Connection Confirm conforme, parametres du CR repris a l'identique."""
    tpdu = params.get(P_TPDU_SIZE) or b"\x0a"          # 2^10 = 1024 par defaut
    src_tsap = params.get(P_SRC_TSAP) or b"\x01\x00"
    dst_tsap = params.get(P_DST_TSAP) or b"\x01\x02"

    variable = b"".join([
        bytes([P_TPDU_SIZE, len(tpdu)]) + tpdu,
        bytes([P_SRC_TSAP, len(src_tsap)]) + src_tsap,
        bytes([P_DST_TSAP, len(dst_tsap)]) + dst_tsap,
    ])
    fixed = struct.pack(">BHHB", COTP_CC, dst_ref, src_ref, 0x00)
    return bytes([len(fixed) + len(variable)]) + fixed + variable


def patch_cotp_handshake() -> None:
    """Applique le correctif au serveur ISO de python-snap7 (idempotent)."""
    if getattr(ServerISOConnection, "_cotp_patched", False):
        return

    original_parse = ServerISOConnection._parse_cotp_cr

    def _parse_cotp_cr(self, data: bytes) -> bool:
        ok = original_parse(self, data)
        self._cr_params = parse_cotp_params(data) if ok else {}
        return ok

    def _build_cotp_cc(self) -> bytes:
        return build_cotp_cc(self.dst_ref, self.src_ref,
                             getattr(self, "_cr_params", {}))

    ServerISOConnection._parse_cotp_cr = _parse_cotp_cr
    ServerISOConnection._build_cotp_cc = _build_cotp_cc
    ServerISOConnection._cotp_patched = True
    log.debug("Correctif COTP Connection Confirm applique a python-snap7")


class _DisconnectNoise(logging.Filter):
    """snap7 loggue une erreur a chaque deconnexion propre d'un client
    (fin de session COTP). Sans ce filtre, un client qui interroge en boucle
    remplit le journal."""

    def filter(self, record: logging.LogRecord) -> bool:
        return "Expected COTP DT" not in record.getMessage()


def s7_address(cfg, db_number: int) -> str:
    """Adresse lisible, au format attendu par Telegraf et la plupart des outils."""
    if cfg.s7_offset is None:
        return "-"
    code = S7_CODE[cfg.dtype]
    if cfg.dtype == "bool":
        return f"DB{db_number}.X{cfg.s7_offset}.{cfg.s7_bit}"
    return f"DB{db_number}.{code}{cfg.s7_offset}"


class S7Sink(Sink):
    name = "s7"

    def __init__(self, cfg: S7Config):
        self.cfg = cfg
        self.server: Server | None = None
        self.db = bytearray(max(cfg.size, 32))
        self._lock = None
        self._last: Dict[str, Any] = {}
        self._expected: Dict[str, Any] = {}
        self._tags: List[Tag] = []
        self._used_bits: set = set()     # (octet, bit)
        self._used_bytes: set = set()    # octets occupes par les numeriques

    # -- placement dans le DB ---------------------------------------------
    def _allocate(self, tag: Tag) -> None:
        cfg = tag.cfg
        if cfg.is_bool:
            if cfg.s7_offset is not None and cfg.s7_bit is not None:
                slot = (cfg.s7_offset, cfg.s7_bit)
                if slot in self._used_bits:
                    raise ValueError(
                        f"bit S7 DB{self.cfg.db_number}.X{slot[0]}.{slot[1]} deja occupe"
                    )
            else:
                slot = None
                for byte in range(self.cfg.bool_bytes):
                    for bit in range(8):
                        if (byte, bit) not in self._used_bits:
                            slot = (byte, bit)
                            break
                    if slot:
                        break
                if slot is None:
                    raise ValueError(
                        f"plus de bit libre dans le DB S7 "
                        f"(bool_bytes = {self.cfg.bool_bytes}, soit "
                        f"{self.cfg.bool_bytes * 8} booleens)"
                    )
                cfg.s7_offset, cfg.s7_bit = slot
            self._used_bits.add(slot)
            return

        size = FORMATS[cfg.dtype][1]
        if cfg.s7_offset is not None:
            start = cfg.s7_offset
            if any(b in self._used_bytes for b in range(start, start + size)):
                raise ValueError(f"offset S7 {start} deja occupe")
        else:
            start = self.cfg.bool_bytes + (-self.cfg.bool_bytes % 2)
            while any(b in self._used_bytes for b in range(start, start + size)):
                start += 2                       # alignement mot, comme sur un S7
            cfg.s7_offset = start
        if start + size > len(self.db):
            raise ValueError(
                f"offset S7 {start} hors du DB (taille {len(self.db)} octets) : "
                "augmenter 's7.size' dans config.yaml"
            )
        self._used_bytes.update(range(start, start + size))

    # -- cycle de vie -----------------------------------------------------
    async def start(self, tags: List[Tag]) -> None:
        self._tags = [t for t in tags if t.cfg.s7]
        for tag in self._tags:
            self._allocate(tag)

        patch_cotp_handshake()
        logging.getLogger("snap7").setLevel(logging.WARNING)
        logging.getLogger("snap7.server").addFilter(_DisconnectNoise())
        self.server = Server(log=False)
        self.server.register_area(SrvArea.DB, self.cfg.db_number, self.db)
        self.server.host = self.cfg.host
        self.server.start(self.cfg.port)
        self._lock = self.server.area_locks[(Area.DB, self.cfg.db_number)]

        log.info(
            "Serveur S7 en ecoute sur %s:%s (DB%s, %s octets, rack %s slot %s)",
            self.cfg.host, self.cfg.port, self.cfg.db_number, len(self.db),
            self.cfg.rack, self.cfg.slot,
        )

    # -- encodage / decodage ----------------------------------------------
    def _write(self, tag: Tag, value: Any) -> None:
        cfg = tag.cfg
        if cfg.is_bool:
            mask = 1 << cfg.s7_bit
            if value:
                self.db[cfg.s7_offset] |= mask
            else:
                self.db[cfg.s7_offset] &= ~mask & 0xFF
        else:
            fmt, size = FORMATS[cfg.dtype]
            struct.pack_into(fmt, self.db, cfg.s7_offset, value)

    def _read(self, tag: Tag) -> Any:
        cfg = tag.cfg
        if cfg.is_bool:
            return bool(self.db[cfg.s7_offset] & (1 << cfg.s7_bit))
        fmt, size = FORMATS[cfg.dtype]
        return struct.unpack_from(fmt, self.db, cfg.s7_offset)[0]

    async def publish(self, tags: List[Tag]) -> None:
        pending = [t for t in self._tags
                   if t.name not in self._last or self._last[t.name] != t.value]
        if not pending:
            return
        with self._lock:
            for tag in pending:
                try:
                    self._write(tag, tag.value)
                    self._last[tag.name] = tag.value
                    self._expected[tag.name] = self._read(tag)
                except Exception:
                    log.exception("Ecriture S7 impossible pour %s", tag.name)

    async def poll_writes(self) -> Dict[str, Any]:
        """Detecte les ecritures faites par un client S7."""
        changes: Dict[str, Any] = {}
        with self._lock:
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
        if not tag.cfg.s7 or tag in self._tags:
            return
        self._allocate(tag)
        self._tags.append(tag)

    async def remove_tag(self, tag: Tag) -> None:
        self._tags = [t for t in self._tags if t.name != tag.name]
        self._last.pop(tag.name, None)
        self._expected.pop(tag.name, None)
        cfg = tag.cfg
        if cfg.s7_offset is None:
            return
        with self._lock:
            if cfg.is_bool:
                self._used_bits.discard((cfg.s7_offset, cfg.s7_bit))
                self.db[cfg.s7_offset] &= ~(1 << cfg.s7_bit) & 0xFF
            else:
                size = FORMATS[cfg.dtype][1]
                self._used_bytes.difference_update(range(cfg.s7_offset, cfg.s7_offset + size))
                self.db[cfg.s7_offset:cfg.s7_offset + size] = bytes(size)

    def describe(self) -> Dict[str, Any]:
        return {
            "host": self.cfg.host,
            "port": self.cfg.port,
            "db_number": self.cfg.db_number,
            "rack": self.cfg.rack,
            "slot": self.cfg.slot,
            "size": len(self.db),
            "bool_bytes": self.cfg.bool_bytes,
        }

    async def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:  # pragma: no cover
                pass
