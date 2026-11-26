"""Deploy / run entrypoint for time_service.

THE ONE LAYER ALLOWED TO NAME A SUBSTRATE — and it does so only as a DEFAULT
string routed through ``composeio.transport_registry.build`` (DP-CIO-6; the
red-gate ``test_substrate_choice_at_the_waist`` forbids a *library* module from
naming a concrete Transport class, which this package's library code never does).
The transport ``type:`` comes from ``--transport`` / ``COMPOSEIO_TRANSPORT`` /
default ``mqtt``; an unknown type RAISES rather than degrading to local-only, and
a known-but-unreachable backend degrades LOUDLY.

    python -m time_service
    python -m time_service --transport type=mqtt,broker=mqtt-broker.local
    python -m time_service --tick-hz 1 --profile /path/to/game_time_profile.json
    python -m time_service --transport type=synthetic   # bench, no broker

Requires ``composeio`` on the path (PYTHONPATH=composeio/python).

⛔ A LIVE deploy connects to whatever broker the spec resolves — Haven-Deployer
owns that step and must verify the live broker host first (the Buildhost cutover is
pending). Running this points the service at ``config.broker_host()`` by default.
"""

from __future__ import annotations

import argparse
import sys
import time

from . import config
from .profile import TimeProfile
from .service import TimeService


def _parse_transport_arg(arg: str | None) -> tuple[str | None, dict]:
    """``type=mqtt,broker=h,port=1883`` -> (``"mqtt"``, {...}). A bare word is a
    type. Values stay STRINGS — the registry factories coerce (mirrors
    ``fleet``/``cli._parse_transport_arg``, so ``baud=``/``baudrate=`` style
    divergences are not reintroduced here)."""
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
    ap = argparse.ArgumentParser(prog="time_service", description=__doc__)
    ap.add_argument("--transport", default=None,
                    help="type=<t>,k=v,... (default: $COMPOSEIO_TRANSPORT or mqtt)")
    ap.add_argument("--profile", default=None,
                    help="path to a game time profile (JSON); default = idle")
    ap.add_argument("--tick-hz", type=float, default=None,
                    help="override the publish cadence (default: profile or 1 Hz)")
    args = ap.parse_args(argv)

    # composeio is resolved lazily so `python -m time_service --help` works
    # without it, and so this module imports NO concrete transport.
    from composeio import ComposeIOBus
    from composeio.transport_registry import build

    profile = TimeProfile.load(args.profile) if args.profile else TimeProfile.idle_default()
    if args.tick_hz is not None:
        profile.tick_hz = args.tick_hz

    spec = _build_spec(args.transport)
    transport = build(spec, app_id=config.SOURCE)
    if transport is None:
        sys.stderr.write(
            f"[time_service] !! transport {spec.get('type')!r} is UNAVAILABLE "
            f"({spec!r}) — the bus would be LOCAL-ONLY and nothing it publishes "
            f"would leave the process. Refusing to run a silent clock.\n")
        return 2

    # announce_time=False: time_service is a SERVICE riding the bus, not the bus
    # MASTER. It owns `time/base`, its own richer clock fact; it must not also put
    # a second publisher on the master's `_bus/time` infrastructure tick (the
    # same leaf-vs-master reasoning source_bus records).
    bus = ComposeIOBus(bus_id=config.SOURCE, global_transport=transport)
    bus.start(announce_time=False)

    service = TimeService(profile=profile, source=config.SOURCE)
    service.attach(bus)
    service.start()

    sys.stderr.write(
        f"[time_service] LIVE — publishing {spec.get('type')!r}: time/base at "
        f"{profile.tick_hz} Hz (base_mode={profile.base_mode}), bodies="
        f"{sorted(profile.bodies)}\n")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        sys.stderr.write("\n[time_service] stopping.\n")
    finally:
        service.stop()
        bus.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
