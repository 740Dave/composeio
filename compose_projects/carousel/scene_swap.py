#!/usr/bin/env python3
"""scene_swap — publish carousel launch intents on the shared bus.

The bus-native clean-restart primitive for the rpi5 kiosk.  It publishes one or
more ``_app/<target>/open`` intents to the MQTT broker — EXACTLY the message a
carousel center-tap, an MCP publish, or an Intelligence would send (the credo:
an Intelligence drives identically to a human).  The running ``kiosk.py``
supervisor consumes the intent and swaps the visible scene IN-PLACE (kill +
respawn the render child, a fresh process that re-reads the scene files and the
freshly-deployed python package + assets).

Why this exists: a deploy must be able to force the kiosk to pick up new assets
WITHOUT killing the supervisor.  Two agents this session took the kiosk down by
SIGTERM-ing the render child directly (it cascaded to ``kiosk.py``).  The robust
pattern is to NOT kill anything — publish the scene-swap intent and let the
supervisor own the child lifecycle.  ``deploy_rpi5.sh`` calls this after a sync.

Usage:
    python3 scene_swap.py bsg                 # show the BSG 2-up
    python3 scene_swap.py launcher            # back to the carousel
    python3 scene_swap.py launcher bsg        # full round-trip reload (default
                                              #   restart: forces a fresh bsg
                                              #   child so new assets take)
    python3 scene_swap.py --broker mqtt-broker.local launcher bsg

Targets are the same ones ``kiosk.py`` subscribes: launcher, bsg, fiat, power.
Publishing `power` will trigger the guarded poweroff on the Pi — do not.
"""
from __future__ import annotations
import os

import argparse
import sys
import time

DEFAULT_BROKER = os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")   # == mqtt-broker.local, the bus master
DEFAULT_PORT = 1883


def publish_intents(targets, broker=DEFAULT_BROKER, port=DEFAULT_PORT,
                    settle_s=1.5, gap_s=1.5, log=print):
    """Publish `_app/<t>/open`="open" for each target in order, on the same
    global (MQTT) transport ``kiosk.py`` listens on.  A short settle lets the
    supervisor kill+respawn the render child between intents so a
    ``launcher bsg`` pair yields a genuinely fresh target child."""
    from composeio.bus import ComposeIOBus
    from composeio.transport_mqtt import MQTTTransport
    import os

    transport = MQTTTransport(broker=broker, port=port,
                              client_id=f"scene-swap-{os.getpid()}")
    bus = ComposeIOBus(bus_id="scene-swap", global_transport=transport)
    namespaces = [f"_app/{t}/open" for t in targets]
    for ns in namespaces:
        # `json` engine — free-form intent value ("open"); NEVER `binary`
        # (an empty/odd value on a strict engine SIGABRTs the process).
        bus.ensure_namespace(ns, engine_config="json")
    bus.start()
    try:
        time.sleep(0.4)                      # let the transport connect
        for i, ns in enumerate(namespaces):
            # A UNIQUE value each time: the bus notifies subscribers only on
            # `new != old` (namespace.py change-detection), and `_app/*/open`
            # is a retained state that may already hold "open" — a same-value
            # republish would be SUPPRESSED and the supervisor would never see
            # it.  kiosk.py's on_intent routes purely on the namespace and
            # ignores the value, so a monotonic nonce is safe and guarantees
            # the swap fires.
            val = f"open:{time.time():.3f}"
            bus.publish_state(ns, val, source="scene_swap")
            log(f"[scene_swap] published {ns} = {val}")
            # settle between a launcher->bsg pair so the supervisor completes
            # the first swap before the second intent lands.
            if i < len(namespaces) - 1:
                time.sleep(gap_s)
        time.sleep(settle_s)                 # let the last publish flush
    finally:
        try:
            bus.stop()
        except Exception:
            pass
    return namespaces


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Publish carousel `_app/<target>/open` scene-swap intents.")
    ap.add_argument("targets", nargs="+",
                    help="one or more of: launcher bsg fiat power "
                         "(order matters; 'launcher bsg' = full reload)")
    ap.add_argument("--broker", default=DEFAULT_BROKER)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--gap", type=float, default=1.5,
                    help="seconds between intents (supervisor swap time)")
    a = ap.parse_args(argv)
    if "power" in a.targets:
        print("[scene_swap] refusing to publish 'power' (guarded poweroff) — "
              "remove it from the targets", file=sys.stderr)
        return 2
    publish_intents(a.targets, broker=a.broker, port=a.port, gap_s=a.gap)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
