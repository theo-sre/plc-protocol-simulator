"""Client de verification : lit quelques tags en Modbus TCP et en OPC UA.

    python test_client.py                 # teste localhost
    python test_client.py 192.168.1.42    # teste une machine distante
"""

from __future__ import annotations

import asyncio
import sys

from simulator.config import decode, load_config


async def test_modbus(host: str, cfg) -> None:
    from pymodbus.client import AsyncModbusTcpClient

    print(f"\n--- Modbus TCP {host}:{cfg.modbus.port} ---")
    client = AsyncModbusTcpClient(host, port=cfg.modbus.port)
    await client.connect()
    if not client.connected:
        print("  connexion impossible")
        return

    unit = cfg.modbus.unit_id
    for tag in cfg.tags[:24]:
        if not tag.modbus:
            continue
        try:
            if tag.dtype == "bool":
                rr = await _call(client, "read_coils", tag.address, 1, unit)
                value = None if rr.isError() else rr.bits[0]
            else:
                rr = await _call(client, "read_holding_registers", tag.address,
                                 tag.word_count, unit)
                value = None if rr.isError() else decode(
                    tag.dtype, rr.registers, cfg.modbus.word_order)
        except Exception as exc:
            value = f"erreur: {exc}"
        area = "coil" if tag.dtype == "bool" else "hr"
        print(f"  {tag.name:<24} {area}{tag.address:<5} = {value}")
    client.close()


async def _call(client, method: str, address: int, count: int, unit: int):
    """Compatibilite pymodbus : le mot-cle a change (unit -> slave -> device_id)."""
    fn = getattr(client, method)
    for kwargs in ({"device_id": unit}, {"slave": unit}, {"unit": unit}, {}):
        try:
            return await fn(address, count=count, **kwargs)
        except TypeError:
            continue
    raise RuntimeError(f"appel {method} impossible")


async def test_opcua(host: str, cfg) -> None:
    from asyncua import Client

    url = f"opc.tcp://{host}:{cfg.opcua.port}{cfg.opcua.endpoint_path}"
    print(f"\n--- OPC UA {url} ---")
    try:
        async with Client(url=url) as client:
            ns = await client.get_namespace_index(cfg.opcua.uri)
            for tag in cfg.tags[:24]:
                if not tag.opcua:
                    continue
                node = client.get_node(f"ns={ns};s={cfg.opcua.folder}.{tag.name}")
                try:
                    value = await node.read_value()
                except Exception as exc:
                    value = f"erreur: {exc}"
                print(f"  {tag.name:<24} ns={ns};s={cfg.opcua.folder}.{tag.name:<20} = {value}")
    except Exception as exc:
        print(f"  connexion impossible : {exc}")


async def main() -> None:
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    cfg = load_config("config.yaml")
    if cfg.modbus.enabled:
        await test_modbus(host, cfg)
    if cfg.opcua.enabled:
        await test_opcua(host, cfg)


if __name__ == "__main__":
    asyncio.run(main())
