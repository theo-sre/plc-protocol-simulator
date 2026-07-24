"""Serveur OPC UA : expose les tags comme variables sous un dossier."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from asyncua import Server, ua

from .config import OpcuaConfig
from .engine import Sink, Tag

log = logging.getLogger("opcua")

VARIANTS = {
    "bool": ua.VariantType.Boolean,
    "int16": ua.VariantType.Int16,
    "uint16": ua.VariantType.UInt16,
    "int32": ua.VariantType.Int32,
    "uint32": ua.VariantType.UInt32,
    "float32": ua.VariantType.Float,
    "float64": ua.VariantType.Double,
}


class OpcuaSink(Sink):
    name = "opcua"

    def __init__(self, cfg: OpcuaConfig):
        self.cfg = cfg
        self.server: Server | None = None
        self.idx: int = 2
        self.folder = None
        self.nodes: Dict[str, Any] = {}
        self._last: Dict[str, Any] = {}      # derniere valeur poussee par le moteur
        self._expected: Dict[str, Any] = {}  # valeur relue attendue cote serveur
        self._tags: List[Tag] = []
        self.endpoint = ""

    async def start(self, tags: List[Tag]) -> None:
        self._tags = [t for t in tags if t.cfg.opcua]
        self.server = Server()
        await self.server.init()

        path = self.cfg.endpoint_path if self.cfg.endpoint_path.startswith("/") else "/" + self.cfg.endpoint_path
        self.endpoint = f"opc.tcp://{self.cfg.host}:{self.cfg.port}{path}"
        self.server.set_endpoint(self.endpoint)
        self.server.set_server_name(self.cfg.server_name)
        if self.cfg.allow_anonymous:
            self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        try:
            # Reecrit l'URL d'endpoint avec l'IP vue par le client (utile quand
            # on ecoute sur 0.0.0.0), sinon certains clients refusent la session.
            self.server.set_match_discovery_client_ip(True)
        except Exception:  # pragma: no cover - selon version d'asyncua
            pass

        self.idx = await self.server.register_namespace(self.cfg.uri)
        self.folder = await self.server.nodes.objects.add_folder(
            ua.NodeId(self.cfg.folder, self.idx), self.cfg.folder
        )

        for tag in self._tags:
            await self._create_node(tag)

        await self.server.start()
        log.info("Serveur OPC UA en ecoute sur %s", self.endpoint)
        log.info("Namespace '%s' (index %s), dossier '%s'",
                 self.cfg.uri, self.idx, self.cfg.folder)

    async def _create_node(self, tag: Tag):
        """Cree la variable OPC UA correspondant a un tag."""
        vtype = VARIANTS[tag.cfg.dtype]
        if tag.cfg.is_bool:
            init: Any = False
        elif tag.cfg.dtype.startswith("float"):
            init = 0.0
        else:
            init = 0
        node = await self.folder.add_variable(
            ua.NodeId(f"{self.cfg.folder}.{tag.name}", self.idx),
            tag.name,
            init,
            varianttype=vtype,
        )
        if tag.cfg.writable:
            await node.set_writable(True)
        desc = tag.cfg.description or ""
        if tag.cfg.unit:
            desc = f"{desc} [{tag.cfg.unit}]".strip()
        if desc:
            await node.write_attribute(
                ua.AttributeIds.Description,
                ua.DataValue(ua.Variant(ua.LocalizedText(desc), ua.VariantType.LocalizedText)),
            )
        self.nodes[tag.name] = (node, vtype)
        return node

    # -- ajout / suppression a chaud --------------------------------------
    async def add_tag(self, tag: Tag) -> None:
        if not tag.cfg.opcua or tag.name in self.nodes:
            return
        await self._create_node(tag)
        self._tags.append(tag)

    async def remove_tag(self, tag: Tag) -> None:
        self._tags = [t for t in self._tags if t.name != tag.name]
        self._last.pop(tag.name, None)
        self._expected.pop(tag.name, None)
        node, _ = self.nodes.pop(tag.name, (None, None))
        if node is not None:
            try:
                await self.server.delete_nodes([node])
            except Exception:
                log.exception("Suppression du noeud %s impossible", tag.name)

    def describe(self) -> Dict[str, Any]:
        return {
            "endpoint": self.endpoint,
            "uri": self.cfg.uri,
            "namespace_index": getattr(self, "idx", None),
            "folder": self.cfg.folder,
            "anonymous": self.cfg.allow_anonymous,
        }

    async def publish(self, tags: List[Tag]) -> None:
        for tag in self._tags:
            value = tag.value
            if tag.name in self._last and self._last[tag.name] == value:
                continue
            node, vtype = self.nodes[tag.name]
            try:
                await node.write_value(ua.DataValue(ua.Variant(value, vtype)))
                self._last[tag.name] = value
                self._expected[tag.name] = await node.read_value()
            except Exception:
                log.exception("Ecriture OPC UA impossible pour %s", tag.name)

    async def poll_writes(self) -> Dict[str, Any]:
        changes: Dict[str, Any] = {}
        for tag in self._tags:
            if not tag.cfg.writable:
                continue
            node, _ = self.nodes.get(tag.name, (None, None))
            if node is None:
                continue
            try:
                current = await node.read_value()
            except Exception:
                continue
            if tag.name in self._expected and current != self._expected[tag.name]:
                changes[tag.name] = current
                self._expected[tag.name] = current
        return changes

    async def stop(self) -> None:
        if self.server is not None:
            try:
                await self.server.stop()
            except Exception:  # pragma: no cover
                pass
