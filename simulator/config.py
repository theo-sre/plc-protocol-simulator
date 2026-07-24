"""Chargement et validation du fichier de configuration YAML."""

from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

# Nombre de registres Modbus (16 bits) occupes par chaque type
REGISTERS = {
    "bool": 0,
    "int16": 1,
    "uint16": 1,
    "int32": 2,
    "uint32": 2,
    "float32": 2,
    "float64": 4,
}

NUMERIC_TYPES = [t for t in REGISTERS if t != "bool"]
INTEGER_TYPES = ("int16", "uint16", "int32", "uint32")

RANGES = {
    "int16": (-32768, 32767),
    "uint16": (0, 65535),
    "int32": (-2147483648, 2147483647),
    "uint32": (0, 4294967295),
}


@dataclass
class TagConfig:
    name: str
    dtype: str = "float32"
    generator: Any = "constant"
    description: str = ""
    unit: str = ""
    scale: float = 1.0          # valeur transmise = valeur brute * scale
    deadband: float = 0.0       # variation minimale avant publication (0 = toujours)
    writable: bool = True       # autorise l'ecriture depuis un client
    address: Optional[int] = None   # adresse Modbus (coil si bool, holding sinon)
    modbus: bool = True
    opcua: bool = True
    bacnet: bool = True
    bacnet_instance: Optional[int] = None   # numero d'instance BACnet impose
    s7: bool = True
    s7_offset: Optional[int] = None         # offset en octets dans le DB S7
    s7_bit: Optional[int] = None            # numero de bit (booleens uniquement)

    def __post_init__(self):
        if self.dtype not in REGISTERS:
            raise ValueError(
                f"Tag '{self.name}' : type '{self.dtype}' inconnu. "
                f"Types valides : {', '.join(REGISTERS)}"
            )

    @property
    def is_bool(self) -> bool:
        return self.dtype == "bool"

    @property
    def word_count(self) -> int:
        return REGISTERS[self.dtype]


@dataclass
class ModbusConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 502
    unit_id: int = 1
    word_order: str = "big"     # big = mot de poids fort en premier
    mirror_input_registers: bool = True   # copie holding -> input registers (FC4)
    mirror_discrete_inputs: bool = True   # copie coils -> discrete inputs (FC2)
    size: int = 2000


@dataclass
class OpcuaConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 4840
    endpoint_path: str = "/freeopcua/server/"
    uri: str = "http://datatest.simulator"
    server_name: str = "DataTest Simulator"
    folder: str = "Simulation"
    allow_anonymous: bool = True


@dataclass
class BacnetConfig:
    """BACnet/IP (UDP). MS/TP n'est pas gere : c'est un bus serie RS-485."""

    enabled: bool = True
    host: str = "auto"          # auto = IP de la carte reseau principale
    prefix_length: int = 24     # masque du reseau local (24 = 255.255.255.0)
    port: int = 47808           # 0xBAC0, port BACnet/IP standard
    device_id: int = 599
    device_name: str = "DataTest-Simulator"
    vendor_id: int = 999
    vendor_name: str = "DataTest"
    model_name: str = "Simulateur de valeurs fictives"
    description: str = "Generateur de valeurs simulees"


@dataclass
class S7Config:
    """Serveur S7comm (ISO-on-TCP / RFC1006). Le PC joue le role d'automate."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 102             # port S7 standard
    db_number: int = 1          # numero du DB expose
    size: int = 1024            # taille du DB en octets
    bool_bytes: int = 16        # octets reserves aux booleens en debut de DB
    rack: int = 0               # informatif : a reporter dans le client
    slot: int = 1               # informatif : 1 pour S7-1200/1500, 2 pour S7-300/400


@dataclass
class WebConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 8080


@dataclass
class AppConfig:
    scan_ms: int = 200
    tags: List[TagConfig] = field(default_factory=list)
    modbus: ModbusConfig = field(default_factory=ModbusConfig)
    opcua: OpcuaConfig = field(default_factory=OpcuaConfig)
    bacnet: BacnetConfig = field(default_factory=BacnetConfig)
    s7: S7Config = field(default_factory=S7Config)
    web: WebConfig = field(default_factory=WebConfig)


def _section(raw: Dict[str, Any], key: str, cls):
    data = dict(raw.get(key) or {})
    unknown = set(data) - set(cls.__dataclass_fields__)
    if unknown:
        raise ValueError(f"Section '{key}' : parametres inconnus {sorted(unknown)}")
    return cls(**data)


def allocate_addresses(tags: List[TagConfig]) -> None:
    """Attribue automatiquement les adresses Modbus non renseignees.

    Les booleens sont ranges dans les coils (a partir de 0), les valeurs
    numeriques dans les holding registers (a partir de 0). Les adresses fixees
    manuellement dans la config sont respectees et evitees.
    """
    used_coils = {t.address for t in tags if t.is_bool and t.address is not None}
    used_regs = set()
    for t in tags:
        if not t.is_bool and t.address is not None:
            used_regs.update(range(t.address, t.address + t.word_count))

    next_coil, next_reg = 0, 0
    for tag in tags:
        if tag.address is not None or not tag.modbus:
            continue
        if tag.is_bool:
            while next_coil in used_coils:
                next_coil += 1
            tag.address = next_coil
            used_coils.add(next_coil)
            next_coil += 1
        else:
            n = tag.word_count
            while any((next_reg + i) in used_regs for i in range(n)):
                next_reg += 1
            tag.address = next_reg
            used_regs.update(range(next_reg, next_reg + n))
            next_reg += n

    # Detection de chevauchement
    seen: Dict[int, str] = {}
    for tag in tags:
        if not tag.modbus or tag.address is None:
            continue
        space = "coil" if tag.is_bool else "reg"
        for i in range(max(1, tag.word_count)):
            key = f"{space}:{tag.address + i}"
            if key in seen:
                raise ValueError(
                    f"Conflit d'adresse Modbus {key} entre '{seen[key]}' et '{tag.name}'"
                )
            seen[key] = tag.name


def allocate_one(existing: List[TagConfig], tag: TagConfig) -> None:
    """Attribue une adresse libre a un tag ajoute a chaud."""
    if tag.address is not None or not tag.modbus:
        return
    if tag.is_bool:
        used = {t.address for t in existing if t.modbus and t.is_bool}
        addr = 0
        while addr in used:
            addr += 1
        tag.address = addr
        return
    used = set()
    for t in existing:
        if t.modbus and not t.is_bool and t.address is not None:
            used.update(range(t.address, t.address + t.word_count))
    n = tag.word_count
    addr = 0
    while any((addr + i) in used for i in range(n)):
        addr += 1
    tag.address = addr


def check_free(existing: List[TagConfig], tag: TagConfig) -> None:
    """Verifie qu'une adresse imposee n'entre pas en conflit."""
    if tag.address is None or not tag.modbus:
        return
    for t in existing:
        if not t.modbus or t.address is None or t.is_bool != tag.is_bool:
            continue
        a1, n1 = t.address, max(1, t.word_count)
        a2, n2 = tag.address, max(1, tag.word_count)
        if a1 < a2 + n2 and a2 < a1 + n1:
            space = "coil" if tag.is_bool else "registre"
            raise ValueError(
                f"L'adresse {space} {tag.address} est deja occupee par '{t.name}'"
            )


def load_config(path: str) -> AppConfig:
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    tags: List[TagConfig] = []
    names = set()
    for entry in raw.get("tags") or []:
        entry = dict(entry)
        unknown = set(entry) - set(TagConfig.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Tag '{entry.get('name')}' : cles inconnues {sorted(unknown)}")
        tag = TagConfig(**entry)
        if not tag.name.isidentifier():
            raise ValueError(
                f"Le nom de tag '{tag.name}' doit etre un identifiant valide "
                "(lettres, chiffres, underscore, ne commence pas par un chiffre)"
            )
        if tag.name in names:
            raise ValueError(f"Nom de tag duplique : '{tag.name}'")
        names.add(tag.name)
        tags.append(tag)

    if not tags:
        raise ValueError("Aucun tag defini dans la configuration.")

    cfg = AppConfig(
        scan_ms=int(raw.get("scan_ms", 200)),
        tags=tags,
        modbus=_section(raw, "modbus", ModbusConfig),
        opcua=_section(raw, "opcua", OpcuaConfig),
        bacnet=_section(raw, "bacnet", BacnetConfig),
        s7=_section(raw, "s7", S7Config),
        web=_section(raw, "web", WebConfig),
    )
    allocate_addresses(cfg.tags)
    return cfg


HEADER = """\
# Configuration du simulateur de valeurs fictives.
#
# Fichier reecrit par l'IHM web : les commentaires de l'ancienne version ont
# ete perdus (une sauvegarde .bak est conservee a cote).
# La liste complete des generateurs et de leurs parametres est dans README.md.
"""


def save_config(path: str, cfg: AppConfig, tag_specs: List[Dict[str, Any]]) -> str:
    """Reecrit le fichier YAML a partir de l'etat courant.

    ``tag_specs`` est la liste des tags serialises (voir Engine.export_tags).
    L'ancien fichier est conserve en ``<path>.bak``.
    """
    from dataclasses import asdict

    data = {
        "scan_ms": cfg.scan_ms,
        "modbus": asdict(cfg.modbus),
        "opcua": asdict(cfg.opcua),
        "bacnet": asdict(cfg.bacnet),
        "s7": asdict(cfg.s7),
        "web": asdict(cfg.web),
        "tags": tag_specs,
    }
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(HEADER)
        yaml.safe_dump(data, fh, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    return path


# --------------------------------------------------------------------------
# Encodage / decodage des valeurs vers les registres 16 bits
# --------------------------------------------------------------------------


def _swap(words: List[int], word_order: str) -> List[int]:
    return list(reversed(words)) if str(word_order).lower().startswith("little") else words


def encode(dtype: str, value: Any, word_order: str = "big") -> List[int]:
    """Convertit une valeur en liste de registres 16 bits (big-endian interne)."""
    if dtype == "int16":
        return [int(value) & 0xFFFF]
    if dtype == "uint16":
        return [int(value) & 0xFFFF]
    if dtype in ("int32", "uint32"):
        fmt = ">i" if dtype == "int32" else ">I"
        lo, hi = RANGES[dtype]
        raw = struct.pack(fmt, int(min(max(value, lo), hi)))
    elif dtype == "float32":
        raw = struct.pack(">f", float(value))
    elif dtype == "float64":
        raw = struct.pack(">d", float(value))
    else:
        raise ValueError(f"Type non encodable en registres : {dtype}")
    words = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2)]
    return _swap(words, word_order)


def decode(dtype: str, words: List[int], word_order: str = "big") -> Any:
    """Operation inverse de :func:`encode` (lecture des ecritures clientes)."""
    words = _swap(list(words), word_order)
    if dtype == "uint16":
        return words[0] & 0xFFFF
    if dtype == "int16":
        v = words[0] & 0xFFFF
        return v - 0x10000 if v >= 0x8000 else v
    raw = b"".join(int(w & 0xFFFF).to_bytes(2, "big") for w in words)
    if dtype == "int32":
        return struct.unpack(">i", raw)[0]
    if dtype == "uint32":
        return struct.unpack(">I", raw)[0]
    if dtype == "float32":
        return struct.unpack(">f", raw)[0]
    if dtype == "float64":
        return struct.unpack(">d", raw)[0]
    raise ValueError(f"Type non decodable : {dtype}")


def coerce(dtype: str, value: Any) -> Any:
    """Force une valeur dans le domaine de son type (bornage des entiers)."""
    if dtype == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "on", "vrai", "yes")
        return bool(value)
    value = float(value)
    if dtype in INTEGER_TYPES:
        lo, hi = RANGES[dtype]
        return int(min(max(round(value), lo), hi))
    return value
