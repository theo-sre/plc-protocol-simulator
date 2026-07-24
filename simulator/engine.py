"""Moteur de simulation : calcule les valeurs de tous les tags a chaque cycle
et les pousse vers les serveurs (Modbus, OPC UA)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from . import generators
from .config import (
    REGISTERS,
    AppConfig,
    TagConfig,
    allocate_one,
    check_free,
    coerce,
    save_config,
)

log = logging.getLogger("engine")


class Tag:
    def __init__(self, cfg: TagConfig):
        self.cfg = cfg
        self.generator = generators.build(cfg.generator)
        self.value: Any = False if cfg.is_bool else 0
        self.override: Any = None       # valeur forcee (prioritaire sur le generateur)
        self.forced: bool = False
        self.updated_at: float = 0.0

    @property
    def name(self) -> str:
        return self.cfg.name

    def compute(self, t: float, dt: float, ctx: Dict[str, Any]) -> Any:
        if self.forced:
            raw = self.override
        else:
            raw = self.generator.compute(t, dt, ctx)

        if self.cfg.is_bool:
            new = coerce("bool", raw)
        else:
            new = coerce(self.cfg.dtype, float(raw) * self.cfg.scale)
            if self.cfg.deadband > 0 and abs(new - self.value) < self.cfg.deadband:
                new = self.value

        if new != self.value:
            self.updated_at = time.time()
        self.value = new
        return new

    def as_dict(self) -> Dict[str, Any]:
        gen = self.generator
        return {
            "name": self.name,
            "dtype": self.cfg.dtype,
            "value": self.value,
            "unit": self.cfg.unit,
            "description": self.cfg.description,
            "generator": gen.describe(),
            "generator_type": gen.type_name,
            "generator_params": gen.to_spec(),
            "forced": self.forced,
            "writable": self.cfg.writable,
            "modbus": self.cfg.modbus,
            "opcua": self.cfg.opcua,
            "bacnet": self.cfg.bacnet,
            "bacnet_type": "binary-value" if self.cfg.is_bool else "analog-value",
            "bacnet_instance": self.cfg.bacnet_instance,
            "s7": self.cfg.s7,
            "s7_offset": self.cfg.s7_offset,
            "s7_bit": self.cfg.s7_bit,
            "address": self.cfg.address,
            "area": "coil" if self.cfg.is_bool else "holding",
            "words": self.cfg.word_count,
            "scale": self.cfg.scale,
            "deadband": self.cfg.deadband,
            "updated_at": self.updated_at,
        }

    def to_spec(self) -> Dict[str, Any]:
        """Serialisation vers le fichier de configuration."""
        spec: Dict[str, Any] = {
            "name": self.name,
            "dtype": self.cfg.dtype,
            "generator": self.generator.to_spec(),
        }
        if self.cfg.description:
            spec["description"] = self.cfg.description
        if self.cfg.unit:
            spec["unit"] = self.cfg.unit
        if self.cfg.scale != 1.0:
            spec["scale"] = self.cfg.scale
        if self.cfg.deadband:
            spec["deadband"] = self.cfg.deadband
        if not self.cfg.writable:
            spec["writable"] = False
        if not self.cfg.modbus:
            spec["modbus"] = False
        if not self.cfg.opcua:
            spec["opcua"] = False
        if not self.cfg.bacnet:
            spec["bacnet"] = False
        if not self.cfg.s7:
            spec["s7"] = False
        if self.cfg.address is not None:
            spec["address"] = self.cfg.address
        if self.cfg.bacnet_instance is not None:
            spec["bacnet_instance"] = self.cfg.bacnet_instance
        if self.cfg.s7_offset is not None:
            spec["s7_offset"] = self.cfg.s7_offset
        if self.cfg.s7_bit is not None:
            spec["s7_bit"] = self.cfg.s7_bit
        return spec


class Sink:
    """Interface commune aux serveurs de terrain."""

    name = "sink"

    async def start(self, tags: List[Tag]) -> None:  # pragma: no cover - interface
        pass

    async def publish(self, tags: List[Tag]) -> None:  # pragma: no cover
        pass

    async def poll_writes(self) -> Dict[str, Any]:  # pragma: no cover
        """Renvoie {nom_tag: valeur} pour les ecritures faites par un client."""
        return {}

    async def add_tag(self, tag: "Tag") -> None:  # pragma: no cover
        """Declare un tag ajoute pendant le fonctionnement."""

    async def remove_tag(self, tag: "Tag") -> None:  # pragma: no cover
        """Retire un tag pendant le fonctionnement."""

    def describe(self) -> Dict[str, Any]:  # pragma: no cover
        """Informations de connexion affichees par l'IHM."""
        return {}

    async def stop(self) -> None:  # pragma: no cover
        pass


class Engine:
    def __init__(self, cfg: AppConfig, config_path: str = "config.yaml"):
        self.cfg = cfg
        self.config_path = config_path
        self.tags: List[Tag] = [Tag(t) for t in cfg.tags]
        self.by_name: Dict[str, Tag] = {t.name: t for t in self.tags}
        self.sinks: List[Sink] = []
        self.lock = threading.Lock()
        self.started_at = time.monotonic()
        self.cycles = 0
        self._stop = asyncio.Event()
        self._pending: List[tuple] = []   # ('add'|'remove', Tag) a propager aux serveurs

    # -- ajout / suppression de variables ---------------------------------
    def add_tag(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Cree un tag pendant le fonctionnement.

        ``spec`` a la meme forme qu'une entree ``tags:`` du fichier YAML.
        Leve ValueError si la definition est invalide (message destine a l'IHM).
        """
        spec = {k: v for k, v in (spec or {}).items() if v is not None}
        unknown = set(spec) - set(TagConfig.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Champs inconnus : {', '.join(sorted(unknown))}")

        name = str(spec.get("name", "")).strip()
        if not name:
            raise ValueError("Le nom est obligatoire")
        if not name.isidentifier():
            raise ValueError(
                "Le nom doit etre un identifiant : lettres, chiffres et underscore, "
                "sans espace ni accent, et ne commencant pas par un chiffre"
            )
        spec["name"] = name

        with self.lock:
            if name in self.by_name:
                raise ValueError(f"Le tag '{name}' existe deja")
            cfg = TagConfig(**spec)
            existing = [t.cfg for t in self.tags]
            check_free(existing, cfg)
            allocate_one(existing, cfg)
            self._check_room(cfg)
            tag = Tag(cfg)               # leve si le generateur est inconnu
            self.tags.append(tag)
            self.by_name[name] = tag
            self.cfg.tags.append(cfg)
            self._pending.append(("add", tag))
        log.info("Tag ajoute : %s (%s, %s %s)", name, cfg.dtype,
                 "coil" if cfg.is_bool else "holding", cfg.address)
        return tag.as_dict()

    def _check_room(self, cfg: TagConfig) -> None:
        """Verifie que l'adresse tient dans la table Modbus declaree."""
        if not cfg.modbus or cfg.address is None:
            return
        end = cfg.address + max(1, REGISTERS[cfg.dtype])
        if end > self.cfg.modbus.size:
            raise ValueError(
                f"Adresse {cfg.address} hors de la table Modbus "
                f"(taille {self.cfg.modbus.size}) : augmenter 'modbus.size' "
                "dans config.yaml puis relancer"
            )

    def remove_tag(self, name: str) -> Dict[str, Any]:
        with self.lock:
            tag = self.by_name.pop(name, None)
            if tag is None:
                raise KeyError(f"Tag inconnu : {name}")
            self.tags = [t for t in self.tags if t.name != name]
            self.cfg.tags = [c for c in self.cfg.tags if c.name != name]
            self._pending.append(("remove", tag))
        log.info("Tag supprime : %s", name)
        return {"removed": name}

    def export_tags(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [t.to_spec() for t in self.tags]

    def save(self) -> str:
        """Reecrit config.yaml avec l'etat courant (sauvegarde .bak a cote)."""
        path = save_config(self.config_path, self.cfg, self.export_tags())
        log.info("Configuration enregistree dans %s", path)
        return path

    # -- pilotage ---------------------------------------------------------
    def set_value(self, name: str, value: Any, force: bool = True) -> Dict[str, Any]:
        """Ecrit une valeur. Si le tag est en mode ``manual`` la consigne du
        generateur est mise a jour ; sinon la valeur est forcee (override)."""
        tag = self.by_name.get(name)
        if tag is None:
            raise KeyError(f"Tag inconnu : {name}")
        value = coerce(tag.cfg.dtype, value)
        with self.lock:
            if isinstance(tag.generator, generators.Manual):
                tag.generator.set(value)
                tag.forced = False
                tag.override = None
            else:
                tag.override = value
                tag.forced = force
            tag.value = value
            tag.updated_at = time.time()
        return tag.as_dict()

    def release(self, name: str) -> Dict[str, Any]:
        """Rend la main au generateur (annule un forcage)."""
        tag = self.by_name.get(name)
        if tag is None:
            raise KeyError(f"Tag inconnu : {name}")
        with self.lock:
            tag.forced = False
            tag.override = None
        return tag.as_dict()

    def toggle(self, name: str) -> Dict[str, Any]:
        tag = self.by_name.get(name)
        if tag is None:
            raise KeyError(f"Tag inconnu : {name}")
        return self.set_value(name, not bool(tag.value))

    def snapshot(self) -> Dict[str, Any]:
        servers: Dict[str, Any] = {}
        for sink in self.sinks:
            servers[sink.name] = sink.describe()
        with self.lock:
            return {
                "uptime": round(time.monotonic() - self.started_at, 1),
                "cycles": self.cycles,
                "scan_ms": self.cfg.scan_ms,
                "config_path": self.config_path,
                "servers": servers,
                "tags": [t.as_dict() for t in self.tags],
            }

    # -- boucle principale ------------------------------------------------
    async def run(self) -> None:
        period = max(self.cfg.scan_ms, 10) / 1000.0
        last = time.monotonic()
        next_tick = last

        while not self._stop.is_set():
            now = time.monotonic()
            dt = max(now - last, 1e-6)
            last = now
            t = now - self.started_at

            # 0) Propagation des ajouts / suppressions demandes par l'IHM
            if self._pending:
                with self.lock:
                    ops, self._pending = self._pending, []
                for kind, tag in ops:
                    for sink in self.sinks:
                        try:
                            if kind == "add":
                                await sink.add_tag(tag)
                            else:
                                await sink.remove_tag(tag)
                        except Exception:
                            log.exception("%s de %s impossible sur %s",
                                          kind, tag.name, sink.name)

            # 1) Prise en compte des ecritures faites par les clients
            for sink in self.sinks:
                try:
                    for name, value in (await sink.poll_writes()).items():
                        tag = self.by_name.get(name)
                        if tag is None or not tag.cfg.writable:
                            continue
                        log.info("Ecriture %s : %s = %s", sink.name, name, value)
                        self.set_value(name, value)
                except Exception:
                    log.exception("Erreur de relecture sur %s", sink.name)

            # 2) Calcul des valeurs
            ctx: Dict[str, Any] = {}
            with self.lock:
                for tag in self.tags:
                    try:
                        ctx[tag.name] = tag.compute(t, dt, ctx)
                    except Exception:
                        log.exception("Erreur de calcul du tag %s", tag.name)
                        ctx[tag.name] = tag.value
                self.cycles += 1

            # 3) Publication
            for sink in self.sinks:
                try:
                    await sink.publish(self.tags)
                except Exception:
                    log.exception("Erreur de publication sur %s", sink.name)

            next_tick += period
            delay = next_tick - time.monotonic()
            if delay < -period:          # on a pris trop de retard : on resynchronise
                next_tick = time.monotonic()
                delay = 0
            await asyncio.sleep(max(delay, 0))

    def stop(self) -> None:
        self._stop.set()
