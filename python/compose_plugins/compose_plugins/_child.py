"""The CHILD side of the subprocess runner. Run as
``python -m compose_plugins._child --fd N`` by the runner only.

It reads ``configure`` then ``start``, builds a PRIVATE in-process ComposeBus
(never started: no clock, no heartbeat, no connector), starts the plug-in on
it, and bridges: every message the plug-in emits on a declared publish becomes
a bus frame to the host; every bus frame from the host is replayed with source
``_host`` (never echoed back). It polls ``health_reason()`` and reports
``_ctl: health`` / ``_ctl: degraded`` on change. ``stop`` stops the plug-in and
exits 0. An exception in the plug-in exits non-zero with the reason on stderr.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import traceback

from composebus.transport import BusMessage

from .derive import resolve_entry
from .runner import CTL_TO_CHILD, HOST_SOURCE, apply_to_bus, ctl_frame, frame_from_message

HEALTH_POLL_S = 0.2


def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fd", type=int, required=True)
    args = ap.parse_args(argv)
    sock = socket.socket(fileno=args.fd)
    rfile = sock.makefile("rb")
    wlock = threading.Lock()

    def send(data: bytes) -> None:
        with wlock:
            sock.sendall(data)

    def read_obj() -> dict:
        raw = rfile.readline()
        if not raw:
            raise SystemExit(0)   # host closed the link
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("frame is not a JSON object")
        if "_ctl" in obj and obj["_ctl"] not in CTL_TO_CHILD:
            print(f"protocol: _ctl {obj['_ctl']!r} refused", file=sys.stderr)
            raise SystemExit(4)
        return obj

    cfg = read_obj()
    if cfg.get("_ctl") != "configure":
        print("protocol: first frame must be _ctl=configure", file=sys.stderr)
        return 4
    if read_obj().get("_ctl") != "start":
        print("protocol: second frame must be _ctl=start", file=sys.stderr)
        return 4
    for p in reversed(cfg["sys_path"]):
        sys.path.insert(0, p)
    publishes = {tuple(x) for x in cfg["publishes"]}

    from composebus.bus import ComposeIOBus
    bus = ComposeIOBus(bus_id=f"child-{cfg['domain']}")

    def forward(msg: BusMessage) -> None:
        if msg.source == HOST_SOURCE:
            return
        if (msg.namespace, msg.type.name) in publishes:
            send(frame_from_message(msg))

    bus.on_message(forward)
    try:
        cls = resolve_entry(cfg["entry"])
        plugin = cls(config=cfg["config"])
        plugin.start(bus)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        print(f"start raised {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    stop = threading.Event()
    last: "list[object]" = [object()]

    def report() -> None:
        probe = getattr(plugin, "health_reason", None)
        reason = probe() if callable(probe) else None
        if reason != last[0]:
            last[0] = reason
            send(ctl_frame("health", state="running") if reason is None
                 else ctl_frame("degraded", reason=reason))

    report()

    def poll() -> None:
        while not stop.wait(HEALTH_POLL_S):
            try:
                report()
            except Exception:
                traceback.print_exc(file=sys.stderr)
                stop.set()

    threading.Thread(target=poll, daemon=True).start()
    while True:
        obj = read_obj()
        if obj.get("_ctl") == "stop":
            stop.set()
            plugin.stop()
            return 0
        if "_ctl" in obj:
            continue   # configure/start again: ignored
        apply_to_bus(bus, BusMessage.from_json(obj), HOST_SOURCE)


if __name__ == "__main__":
    sys.exit(main())
