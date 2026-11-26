#!/usr/bin/env python3
"""
M4 Intelligence-drive smoke — a REMOTE peer drives the new Composer over
MQTT, exactly as the intelligence_mcp bridge does ([CXC-CREDO] proof).

    python3 tests/render/smoke_m4_intelligence_mqtt.py [broker]

Two bus peers on the ecosystem broker (default mqtt-broker.local):

  - the M4 Composer (headless window, ComposeApp import/export crossover)
  - an "Intelligence" peer: a bare ComposeIOBus + MQTTTransport that
    publishes composer/command/execute commands with the same payload
    shapes as intelligence_mcp/tools.py, switches the WC tab, and
    requests a state snapshot.

Checks: add-widget + set-property arrive over the wire and mutate the
document; the WC tab follows the bus; request_state returns a snapshot
containing the remotely-built widget.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

BROKER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")
os.environ["COMPOSEUX_MQTT_BROKER"] = BROKER

from composeio.bus import ComposeIOBus
from composeio.transport_mqtt import MQTTTransport
from composeio.composer.app import ComposerApp


def _frames(app: ComposerApp, n: int) -> None:
    for _ in range(n):
        app.render.render_frame()
        time.sleep(0.02)


def _wait(predicate, app, timeout=5.0) -> bool:
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

    print(f"== Composer (headless) + Intelligence peer over MQTT @ {BROKER} ==")
    app = ComposerApp(windowed=False, connect_mqtt=True)
    app.start()
    _frames(app, 3)
    check("Composer MQTT bridge up", app.core.mqtt_connected)

    # the Intelligence peer — its own bus + transport (as the MCP server has)
    peer = ComposeIOBus(bus_id="intelligence-peer",
                        global_transport=MQTTTransport(broker=BROKER))
    for ns in ("composer/command/execute", "composer/wc/active_tab",
               "composer/intelligence/request_state",
               "composer/intelligence/state_snapshot"):
        peer.ensure_namespace(ns, engine_config="json")
    peer.start()
    time.sleep(1.0)

    doc = app.core.document

    # -- add a widget (payload shape == composeio_add_widget) ---------------
    peer.publish_event(
        "composer/command/execute", "execute",
        data={"command_id": "mqtt_1",
              "type": "composer/canvas/widget_added",
              "do": {"widget_id": "remote_gauge", "widget_type": "gauge",
                     "module_id": "composite", "namespace": "eng/temp",
                     "properties": {"label": "Core Temp"},
                     "x": 60, "y": 80, "w": 140, "h": 120},
              "undo": {}, "description": "remote add",
              "source": "smoke-intelligence"},
        source="smoke-intelligence")
    ok = _wait(lambda: doc.get_layer("remote_gauge") is not None, app)
    check("remote add_widget over MQTT mutates the document", ok,
          f"layers={[l.widget_id for l in doc.layers]}")

    # -- set a property (payload shape == composeio_set_property) -----------
    peer.publish_event(
        "composer/command/execute", "execute",
        data={"command_id": "mqtt_2",
              "type": "composer/config/widget_changed",
              "do": {"widget_id": "remote_gauge", "target_id": "remote_gauge",
                     "property_name": "label", "value": "Reactor Temp"},
              "undo": {}, "description": "remote prop",
              "source": "smoke-intelligence"},
        source="smoke-intelligence")
    ok = _wait(lambda: (doc.get_layer("remote_gauge") or
                        type("x", (), {"properties": {}})).properties.get(
                            "label") == "Reactor Temp", app)
    check("remote set_property over MQTT", ok)

    # -- switch the WC tab (M4 addition: tab state is bus state) -------------
    peer.publish_state("composer/wc/active_tab", "widgets",
                       source="smoke-intelligence")
    ok = _wait(lambda: app.ui.tabs.active_index == 2, app)
    check("remote WC tab-switch over MQTT", ok,
          f"active={app.ui.tabs.active_index}")

    # -- request a state snapshot -------------------------------------------------
    got: dict = {}
    peer.subscribe_events("composer/intelligence/state_snapshot",
                          lambda ns, et, d: got.update(d))
    peer.publish_event(
        "composer/intelligence/request_state", "request",
        data={"agent_id": "smoke-intelligence", "correlation_id": "mq1"},
        source="smoke-intelligence")
    ok = _wait(lambda: got.get("correlation_id") == "mq1", app, timeout=6)
    widgets = []
    if ok:
        widgets = [w["id"] for m in got["design"]["modules"]
                   for w in m["widgets"]]
    check("state snapshot returned over MQTT", ok and
          "remote_gauge" in widgets, f"widgets={widgets}")

    peer.stop()
    app.stop()
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
