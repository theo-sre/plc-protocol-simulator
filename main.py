"""Simulateur de valeurs fictives -> Modbus TCP + OPC UA.

Usage :
    python main.py                      # utilise config.yaml
    python main.py -c autre.yaml        # autre configuration
    python main.py --list               # affiche la table d'adressage et sort
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import sys

from simulator.config import load_config
from simulator.engine import Engine
from simulator.webui import start_web


def local_ips() -> list[str]:
    ips = set()
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        ips.update(a for a in addrs if not a.startswith("127."))
    except OSError:
        pass
    try:  # IP de l'interface qui sort par defaut
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def print_mapping(cfg, engine=None) -> None:
    from simulator.s7_sink import s7_address

    print()
    print(f"{'TAG':<24} {'TYPE':<9} {'MODBUS':<22} {'S7':<16} {'BACNET':<12} GENERATEUR")
    print("-" * 118)
    for t in cfg.tags:
        if not t.modbus:
            mb = "-"
        elif t.dtype == "bool":
            mb = f"coil {t.address} (FC1/FC5)"
        else:
            end = t.address + t.word_count - 1
            span = f"{t.address}" if t.word_count == 1 else f"{t.address}-{end}"
            mb = f"holding {span} (FC3/FC16)"

        if not (t.s7 and cfg.s7.enabled):
            s7 = "-"
        else:
            s7 = s7_address(t, cfg.s7.db_number)
            if s7 == "-":
                s7 = "(auto)"

        if not (t.bacnet and cfg.bacnet.enabled):
            bn = "-"
        elif t.bacnet_instance is None:
            bn = "(auto)"
        else:
            bn = ("BV" if t.dtype == "bool" else "AV") + f" {t.bacnet_instance}"

        gen = t.generator if isinstance(t.generator, str) else t.generator.get("type", "?")
        print(f"{t.name:<24} {t.dtype:<9} {mb:<22} {s7:<16} {bn:<12} {gen}")
    print()
    if cfg.opcua.enabled:
        print(f"OPC UA : ns=2;s={cfg.opcua.folder}.<nom du tag> pour chaque tag\n")


async def run(args) -> int:
    cfg = load_config(args.config)

    if args.scan_ms:
        cfg.scan_ms = args.scan_ms
    if args.host:
        cfg.modbus.host = cfg.opcua.host = cfg.web.host = args.host
    if args.modbus_port:
        cfg.modbus.port = args.modbus_port
    if args.opcua_port:
        cfg.opcua.port = args.opcua_port
    if args.web_port:
        cfg.web.port = args.web_port
    if args.bacnet_port:
        cfg.bacnet.port = args.bacnet_port
    if args.bacnet_host:
        cfg.bacnet.host = args.bacnet_host
    if args.no_modbus:
        cfg.modbus.enabled = False
    if args.no_opcua:
        cfg.opcua.enabled = False
    if args.no_bacnet:
        cfg.bacnet.enabled = False
    if args.s7_port:
        cfg.s7.port = args.s7_port
    if args.no_s7:
        cfg.s7.enabled = False
    if args.no_web:
        cfg.web.enabled = False

    if args.list:
        print_mapping(cfg)
        return 0

    engine = Engine(cfg, config_path=args.config)

    if cfg.modbus.enabled:
        from simulator.modbus_sink import ModbusSink
        engine.sinks.append(ModbusSink(cfg.modbus))
    if cfg.opcua.enabled:
        from simulator.opcua_sink import OpcuaSink
        engine.sinks.append(OpcuaSink(cfg.opcua))
    if cfg.bacnet.enabled:
        from simulator.bacnet_sink import BacnetSink
        engine.sinks.append(BacnetSink(cfg.bacnet))
    if cfg.s7.enabled:
        from simulator.s7_sink import S7Sink
        engine.sinks.append(S7Sink(cfg.s7))

    for sink in engine.sinks:
        try:
            await sink.start(engine.tags)
        except (OSError, RuntimeError) as exc:
            logging.error(
                "Impossible de demarrer le serveur %s : %s "
                "(port deja utilise ? droits insuffisants ?)", sink.name, exc,
            )
            return 1

    httpd = start_web(engine, cfg.web.host, cfg.web.port) if cfg.web.enabled else None

    print_mapping(cfg, engine)
    bn = next((s for s in engine.sinks if s.name == "bacnet"), None)
    bacnet_info = bn.describe() if bn else {}
    ips = local_ips() or ["<ip-de-cette-machine>"]
    print("Accessible depuis le reseau aux adresses :")
    for ip in ips:
        if cfg.modbus.enabled:
            print(f"  Modbus TCP  {ip}:{cfg.modbus.port}  (unit id {cfg.modbus.unit_id})")
        if cfg.opcua.enabled:
            print(f"  OPC UA      opc.tcp://{ip}:{cfg.opcua.port}{cfg.opcua.endpoint_path}")
        if cfg.s7.enabled:
            print(f"  S7comm      {ip}:{cfg.s7.port}  "
                  f"(rack {cfg.s7.rack}, slot {cfg.s7.slot}, DB{cfg.s7.db_number})")
        if bacnet_info.get("all_interfaces"):
            print(f"  BACnet/IP   {ip}:{cfg.bacnet.port} UDP  "
                  f"(device id {cfg.bacnet.device_id})")
        if cfg.web.enabled:
            print(f"  IHM web     http://{ip}:{cfg.web.port}")
    if cfg.bacnet.enabled and not bacnet_info.get("all_interfaces"):
        print(f"\n  BACnet/IP   {bacnet_info.get('host')}:{bacnet_info.get('port')} UDP "
              f"(device id {cfg.bacnet.device_id})")
        print("              une seule interface : mettre bacnet.host a 0.0.0.0 "
              "pour ecouter partout")
    print("\nCtrl+C pour arreter.\n")

    try:
        await engine.run()
    except asyncio.CancelledError:
        pass
    finally:
        if httpd:
            httpd.shutdown()
        for sink in engine.sinks:
            await sink.stop()
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Simulateur de donnees Modbus TCP / OPC UA")
    p.add_argument("-c", "--config", default="config.yaml", help="fichier de configuration YAML")
    p.add_argument("--host", help="interface d'ecoute (defaut 0.0.0.0 = toutes)")
    p.add_argument("--modbus-port", type=int)
    p.add_argument("--opcua-port", type=int)
    p.add_argument("--bacnet-port", type=int)
    p.add_argument("--bacnet-host", help="IP de la carte reseau pour BACnet/IP")
    p.add_argument("--s7-port", type=int)
    p.add_argument("--web-port", type=int)
    p.add_argument("--scan-ms", type=int, help="periode de rafraichissement en ms")
    p.add_argument("--no-modbus", action="store_true")
    p.add_argument("--no-opcua", action="store_true")
    p.add_argument("--no-bacnet", action="store_true")
    p.add_argument("--no-s7", action="store_true")
    p.add_argument("--no-web", action="store_true")
    p.add_argument("--list", action="store_true", help="affiche la table d'adressage puis quitte")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("asyncua").setLevel(logging.WARNING)
    logging.getLogger("pymodbus").setLevel(logging.WARNING)
    logging.getLogger("bacpypes3").setLevel(logging.WARNING)

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nArret demande.")
        return 0
    except (ValueError, FileNotFoundError) as exc:
        print(f"Erreur de configuration : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
