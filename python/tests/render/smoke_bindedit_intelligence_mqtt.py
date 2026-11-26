#!/usr/bin/env python3
"""
Binding-editor Intelligence smoke — a REMOTE peer authors [SPEC:CXC-BIND]
forms through the Composer over MQTT ([SPEC:CXC-BINDEDIT] bus-native
verification): binds an attribute, configures a behavior, replaces the
document through the Source path, switches the WC to the Bindings tab,
and finally flips the ONE SEMANTIC BIT (ship/red_alert) on the design
bus — the engine-owned blink follows in the Composer preview.

    python3 tests/render/smoke_bindedit_intelligence_mqtt.py [broker]

Default broker: mqtt-broker.local (the ecosystem bus).
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


def _wait(predicate, app, timeout=6.0) -> bool:
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

    print(f"== Composer + binding-authoring Intelligence over MQTT @ {BROKER} ==")
    app = ComposerApp(windowed=False, connect_mqtt=True)
    app.start()
    _frames(app, 3)
    check("Composer MQTT bridge up", app.core.mqtt_connected)

    peer = ComposeIOBus(
        bus_id="bindedit-intelligence",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id="bindedit-smoke-peer"))
    for ns in ("composer/command/execute", "composer/wc/active_tab",
               "composer/source/replace", "ship/red_alert"):
        peer.ensure_namespace(ns, engine_config="json")
    peer.start()
    time.sleep(1.0)

    doc = app.core.document
    ib = app.core.internal_bus

    def cmd(cid: str, target: str, do: dict) -> None:
        peer.publish_event(
            "composer/command/execute", "execute",
            data={"command_id": cid, "type": target, "do": do, "undo": {},
                  "description": "", "source": "bindedit-intelligence"},
            source="bindedit-intelligence")

    # -- build a label remotely -------------------------------------------------
    cmd("b1", "composer/canvas/widget_added",
        {"widget_id": "alert", "widget_type": "label",
         "module_id": "composite",
         "properties": {"text": "RED ALERT", "font_size": 24,
                        "color": "#E5524A"},
         "x": 120, "y": 120, "w": 220, "h": 30})
    ok = _wait(lambda: doc.get_layer("alert") is not None, app)
    check("remote add_widget", ok)

    # -- bind an attribute (the binding editor's Save payload) --------------
    cmd("b2", "composer/config/widget_changed",
        {"widget_id": "alert", "property": "visible",
         "value": {"namespace": "ship/red_alert", "when": "on",
                   "default": True}})
    ok = _wait(lambda: isinstance(
        (doc.get_layer("alert") or type("x", (), {"properties": {}})())
        .properties.get("visible"), dict), app)
    check("remote attribute binding over MQTT", ok)

    # -- configure a behavior ------------------------------------------------------
    cmd("b3", "composer/config/widget_changed",
        {"widget_id": "alert", "property": "behaviors",
         "value": [{"when": {"namespace": "ship/red_alert", "is": "on"},
                    "do": {"behavior": "blink", "target": "visible",
                           "period_ms": 600}}]})
    ok = _wait(lambda: bool(
        (doc.get_layer("alert") or type("x", (), {"properties": {}})())
        .properties.get("behaviors")), app)
    check("remote behavior config over MQTT", ok)

    # -- switch the WC to the Bindings tab -----------------------------------------
    peer.publish_state("composer/wc/active_tab", "bindings",
                       source="bindedit-intelligence")
    ok = _wait(lambda: app.ui.tabs.active_index == 3, app)
    check("remote WC tab-switch to Bindings", ok,
          f"active={app.ui.tabs.active_index}")

    # -- Source-path document replace over MQTT -----------------------------------
    text = doc.to_yaml().replace("RED ALERT", "CONDITION RED")
    peer.publish_event("composer/source/replace", "do",
                       data={"yaml": text}, source="bindedit-intelligence")
    ok = _wait(lambda: (doc.get_layer("alert") or
                        type("x", (), {"properties": {}})())
               .properties.get("text") == "CONDITION RED", app)
    check("remote source/replace over MQTT", ok)
    ok = _wait(lambda: "CONDITION RED" in app.ui.source.text(), app)
    check("Source tab projects the remote edit", ok)

    # -- flip THE ONE BIT: the engine-owned blink follows in the preview ------
    peer.publish_state("ship/red_alert", "on",
                       source="bindedit-intelligence")

    def _preview_alert():
        ww = app.ui.workshop
        if ww._preview is None:
            return None
        for ch in ww._preview.children:
            if ch.widget_id == "alert":
                return ch
        return None

    seen: set = set()

    def _blinking() -> bool:
        w = _preview_alert()
        if w is None:
            return False
        app.render.behaviors.tick(time.monotonic())
        seen.add(bool(w.visible))
        return seen == {True, False}

    ok = _wait(_blinking, app, timeout=8)
    check("one published bit drives the previewed blink", ok,
          f"states seen={sorted(seen)}")

    peer.stop()
    app.stop()
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
