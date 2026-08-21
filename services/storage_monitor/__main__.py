"""Deploy / run entrypoint for storage_monitor.

THE ONE LAYER ALLOWED TO NAME A SUBSTRATE — and it does so only as a DEFAULT string
routed through ``composeio.transport_registry.build`` (DP-CIO-6). The transport ``type:``
comes from ``--transport`` / ``COMPOSEIO_TRANSPORT`` / default ``mqtt``; an unknown type
RAISES rather than degrading to local-only, and a known-but-unreachable backend degrades
LOUDLY.

    python -m storage_monitor
    python -m storage_monitor --transport type=mqtt,broker=mqtt-broker.local
    python -m storage_monitor --path /mnt/spinny --interval 60
    python -m storage_monitor --transport type=synthetic   # bench, no broker

Requires ``composeio`` on the path (PYTHONPATH=composeio/python).

⛔ A LIVE deploy connects to whatever broker the config resolves — the architect owns that
step and must verify the live broker host first (the Skipper cutover is pending). Running
this points the service at ``config.broker_host()`` by default.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config, topics
from .service import StorageMonitor


def _parse_transport_arg(arg: str | None) -> tuple[str | None, dict]:
    """``type=mqtt,broker=h,port=1883`` -> (``"mqtt"``, {...}). A bare word is a type.
    Values stay STRINGS — the registry factories coerce (mirrors
    ``time_service.__main__._parse_transport_arg``)."""
    if not arg:
        return None, {}
    opts: dict[str, str] = {}
    ttype: str | None = None
    for i, part in enumerate(arg.split(",")):
        if "=" not in part:
            if i == 0:
                ttype = part.strip()
            continue
        k, _, v = part.partition("=")
        k = k.strip()
        if k == "type":
            ttype = v.strip()
        else:
            opts[k] = v
    return ttype, opts


def _build_spec(transport_arg: str | None) -> dict:
    ttype, opts = _parse_transport_arg(transport_arg)
    ttype = ttype or config.DEFAULT_TRANSPORT_TYPE
    spec: dict = {"type": ttype, "client_id": config.SOURCE}
    if ttype == "mqtt":
        spec.setdefault("broker", config.broker_host())
        spec.setdefault("port", config.broker_port())
    spec.update(opts)
    return spec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="storage_monitor", description=__doc__)
    ap.add_argument("--transport", default=None,
                    help="type=<t>,k=v,... (default: $COMPOSEIO_TRANSPORT or mqtt)")
    ap.add_argument("--path", default=None,
                    help="filesystem to measure (default: $COMPOSEIO_STORAGE_MONITOR_PATH or /)")
    ap.add_argument("--interval", type=float, default=None,
                    help="seconds between measurements (default: env or 30 s)")
    args = ap.parse_args(argv)

    # composeio is resolved lazily so `python -m storage_monitor --help` works without it,
    # and so this module imports NO concrete transport.
    from composeio import ComposeIOBus
    from composeio.transport_registry import build

    spec = _build_spec(args.transport)
    transport = build(spec, app_id=config.SOURCE)
    if transport is None:
        sys.stderr.write(
            f"[storage_monitor] !! transport {spec.get('type')!r} is UNAVAILABLE "
            f"({spec!r}) — the bus would be LOCAL-ONLY and nothing it publishes would "
            f"leave the process. Refusing to run a silent sensor.\n")
        return 2

    # announce_time=False: storage_monitor is a SERVICE riding the bus, not the bus
    # MASTER — it must not put a second publisher on the master's time infrastructure.
    bus = ComposeIOBus(bus_id=config.SOURCE, global_transport=transport)
    bus.start(announce_time=False)

    service = StorageMonitor(path=args.path, interval_s=args.interval, source=config.SOURCE)
    service.attach(bus)
    service.start()

    sys.stderr.write(
        f"[storage_monitor] LIVE — publishing {spec.get('type')!r}: "
        f"{topics.FREE_BYTES} for path {service.path!r} every {service.interval_s}s\n")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        sys.stderr.write("\n[storage_monitor] stopping.\n")
    finally:
        service.stop()
        bus.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
