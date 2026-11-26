#!/usr/bin/env python3
"""
Flow-mode bus-discovery smoke — a SEPARATE bus peer (the "jump-drive
rig") publishes namespaces the Composer never declared, over REAL MQTT
([SPEC:CXC-FLOW] [CXC-FL-C4]/[C8]/[C9] bus-native verification):

  1. the rig's undeclared namespaces appear in the Bus·Live discovery
     registry within one poll — including their engine via the rig's
     _bus/discovery announcement;
  2. the filter narrows the live rail;
  3. discover-and-wire authors a subscription binding (over the SAME
     command path an Intelligence uses);
  4. a PATTERN subscription (jumpdrive/*) receives a family member that
     did not exist when the binding was authored — over the broker;
  5. Layout/Bindings/Source stay in lockstep; the state snapshot exposes
     bus_live to a remote Intelligence.

    python3 tests/render/smoke_flow_discovery_mqtt.py [broker]

Default broker: mqtt-broker.local (the ecosystem bus).
"""

from __future__ import annotations

import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

BROKER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")
os.environ["COMPOSEUX_MQTT_BROKER"] = BROKER

from composeio.app import ComposeApp  # noqa: E402
from composeio.composer.app import ComposerApp  # noqa: E402

# unique run id so parallel/stale retained traffic can't fake a pass
RUN = f"{os.getpid()}{random.randint(100, 999)}"
NS_READY = f"jumpdrive{RUN}/readiness"
NS_CHARGE = f"jumpdrive{RUN}/charge"
NS_LATER = f"jumpdrive{RUN}/spool_temp"
PATTERN = f"jumpdrive{RUN}/*"


def _frames(app: ComposerApp, n: int) -> None:
    for _ in range(n):
        app.render.render_frame()
        time.sleep(0.02)


def _wait(predicate, app, timeout=8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _frames(app, 1)
        if predicate():
            return True
    return False


def main() -> int:
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    print(f"== Flow-mode bus discovery over MQTT @ {BROKER} (run {RUN}) ==")
    app = ComposerApp(windowed=False, connect_mqtt=True)
    app.start()
    _frames(app, 3)
    check("Composer MQTT bridge up", app.core.mqtt_connected)
    disc = app.core.discovery

    # -- the rig: a ComposeApp peer with its own exports ------------------------
    rig = ComposeApp(app_id=f"jumpdrive-rig-{RUN}")
    rig.register(NS_READY, engine="tri_level", export=True)
    rig.register(NS_CHARGE, engine="continuous_0_255", export=True)
    rig.connect_transport("mqtt", broker=BROKER,
                          client_id=f"jumpdrive-rig-{RUN}")
    rig.start()
    time.sleep(1.0)
    # start() announced discovery before paho finished connecting (the
    # message is dropped while offline) — re-announce, as a real device
    # does on reconnect (the ComposeApp docstring sanctions re-calling).
    rig._publish_discovery()
    rig.publish(NS_READY, "half")           # tri_level vocabulary
    rig.publish(NS_CHARGE, 142)

    # -- C4: undeclared external namespaces appear via discovery ---------------
    def _seen(name):
        return any(e.name == name and e.live for e in disc.snapshot())

    ok = _wait(lambda: _seen(NS_READY) and _seen(NS_CHARGE), app)
    check("undeclared external namespaces appear in Bus·Live", ok,
          f"{len(disc)} ns known")
    e = next((x for x in disc.snapshot() if x.name == NS_READY), None)
    check("engine legible via peer _bus/discovery",
          bool(e and e.engine == "tri_level" and e.engine_source == "peer"),
          f"engine={getattr(e, 'engine', None)} "
          f"src={getattr(e, 'engine_source', None)}")
    check("last value observed", bool(e and e.value == "half"),
          f"value={getattr(e, 'value', None)}")

    # -- C5: the filter narrows the live rail ------------------------------------
    snap = disc.snapshot()
    fam = disc.filter(snap, text=PATTERN)
    ok = {x.name for x in fam} == {NS_READY, NS_CHARGE} and len(snap) >= 2
    check("prefix-glob filter narrows the rail", ok,
          f"{len(fam)} of {len(snap)}")

    # -- C6: discover-and-wire (the drag's commit) --------------------------------
    ib = app.core.internal_bus
    ib.publish_event("composer/canvas/widget_added", "do",
                     data={"widget_type": "gauge",
                           "widget_id": "jump_readiness",
                           "x": 60, "y": 60, "w": 180, "h": 150,
                           "properties": {"label": "READINESS"}},
                     source="smoke")
    _frames(app, 2)
    app.ui.flow.author_wire(NS_READY, "jump_readiness", "value")
    _frames(app, 2)
    layer = app.core.document.get_layer("jump_readiness")
    nsdef = app.core.document.get_namespace(NS_READY)
    check("discover-and-wire authors the subscription",
          layer is not None and layer.namespace == NS_READY
          and nsdef is not None and nsdef.external,
          f"external={getattr(nsdef, 'external', None)}")
    check("kind is subscribed-external",
          disc.kind_of(NS_READY) == "subscribed-external")

    # -- C8: pattern subscription over the broker ---------------------------------
    ib.publish_event("composer/canvas/widget_added", "do",
                     data={"widget_type": "rect", "widget_id": "fam_tile",
                           "x": 300, "y": 60, "w": 100, "h": 60,
                           "properties": {"fill_color": "#10202C"}},
                     source="smoke")
    _frames(app, 2)
    ib.publish_event("composer/config/widget_changed", "do",
                     data={"widget_id": "fam_tile", "property": "opacity",
                           "value": PATTERN}, source="smoke")
    _frames(app, 3)

    def _tile():
        ww = app.ui.workshop
        if ww._preview is None:
            return None
        for ch in ww._preview.children:
            if ch.widget_id == "fam_tile":
                return ch
        return None

    rig.publish(NS_CHARGE, 51)   # existing member drives the family binding
    ok = _wait(lambda: _tile() is not None
               and abs(float(_tile().opacity) - 51.0) < 0.01, app)
    check("pattern binding receives an existing family member", ok,
          f"opacity={getattr(_tile(), 'opacity', None)}")

    # a member that DID NOT EXIST when the binding was authored.
    # (Published on the rig's external bus directly: ComposeApp wires
    # export forwarders at start(), so a post-start export would never
    # cross — a known late-registration limitation, noted for infra.)
    rig.external_bus.ensure_namespace(NS_LATER,
                                      engine_config="continuous_0_255")
    rig.external_bus.publish_state(NS_LATER, 7, source=f"rig-{RUN}")
    ok = _wait(lambda: _tile() is not None
               and abs(float(_tile().opacity) - 7.0) < 0.01, app)
    check("pattern binding receives a LATER family member", ok,
          f"opacity={getattr(_tile(), 'opacity', None)}")
    ok = _wait(lambda: _seen(NS_LATER), app)
    check("the later member also appears in Bus·Live", ok)

    # -- C2/C9: projections stay synced; snapshot exposes bus_live ---------------
    src_ok = NS_READY in app.ui.source.text() \
        and f"opacity: {PATTERN}" in app.ui.source.text()
    check("Source projects the Flow-authored wiring", src_ok)
    rows = app.core.document.bindings()
    check("Bindings summary carries both wires",
          any(r.get("namespace") == NS_READY for r in rows)
          and any(r.get("namespace") == PATTERN for r in rows))
    snap_state: dict = {}

    def _on_snap(ns, et, data):
        snap_state.update(data if isinstance(data, dict) else {})

    ib.subscribe_events("composer/intelligence/state_snapshot", _on_snap)
    ib.publish_event("composer/intelligence/request_state", "request",
                     data={"correlation_id": "flow-smoke"}, source="smoke")
    ok = _wait(lambda: any(r.get("namespace") == NS_READY
                           for r in snap_state.get("bus_live", [])), app)
    check("state snapshot exposes bus_live to an Intelligence", ok,
          f"{len(snap_state.get('bus_live', []))} rows")

    rig.stop()
    app.stop()
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
