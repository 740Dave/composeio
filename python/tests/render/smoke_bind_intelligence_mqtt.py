"""
[SPEC:CXC-BIND] smoke — an Intelligence peer over REAL MQTT drives a
behavior identically to any other publisher ([CXC-BIND-PRINCIPLE] /
[CXC-CREDO]): it flips ONE bit (ship/red_alert = on) and the render
app's engine-owned blink runs; frames are never streamed over the bus.

    python3 tests/render/smoke_bind_intelligence_mqtt.py [broker]

Two bus peers on the ecosystem broker (default mqtt-broker.local):
  - the render app (headless), its bus bridged via MQTTTransport
  - an "Intelligence" peer: a bare ComposeIOBus + MQTTTransport that
    publishes the alarm bit, exactly as intelligence_mcp does.

Verified 2026-07-06: 4/4 against mqtt-broker.local.
NOTE: each MQTTTransport needs a DISTINCT client_id — the broker kicks
duplicate sessions (rc=7 reconnect loop) and delivery silently fails.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

BROKER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")

from composeio.bus import ComposeIOBus
from composeio.transport_mqtt import MQTTTransport
from composeio.render.engine.parser import parse_widget_tree
from composeio.render.engine.runtime import RenderApp

CONFIG = {
    "design": {"width": 320, "height": 240, "title": "red-alert-mqtt"},
    "namespaces": [{"name": "ship/red_alert", "state_engine": "binary"}],
    "widgets": [{
        "type": "pixel_stack", "id": "scene", "width": 320, "height": 240,
        "background": "#101318",
        "children": [
            {"type": "rect", "id": "badge", "x": 100, "y": 90, "w": 120,
             "h": 60, "fill_color": "#E03B3B", "corner_radius": 8,
             "visible": "ship/red_alert",
             "behaviors": [
                 {"when": {"namespace": "ship/red_alert", "is": "on"},
                  "do": {"behavior": "blink", "target": "visible",
                         "period_ms": 400}},
             ]},
        ],
    }],
}


def main() -> int:
    render_bus = ComposeIOBus(
        bus_id="bind-render",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id="bind-render-peer"))
    app = RenderApp(parse_widget_tree(CONFIG), windowed=False,
                    bus=render_bus)
    render_bus.start()        # externally-owned bus: start it ourselves
    app.start()

    intel = ComposeIOBus(
        bus_id="bind-intelligence",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id="bind-intel-peer"))
    intel.start()
    intel.ensure_namespace("ship/red_alert", engine_config="binary")
    time.sleep(1.0)                          # both peers subscribed

    badge = next(w for w in app.comp.root.iter_widgets()
                 if w.widget_id == "badge")
    checks = []

    intel.publish_state("ship/red_alert", "on", source="intelligence-peer")
    time.sleep(1.0)                          # MQTT round trip
    app.render_frame_at(0.0)
    checks.append(("alarm ON over MQTT -> badge visible",
                   badge.visible is True))
    app.render_frame_at(0.3)                 # off-phase of the 400ms blink
    checks.append(("blink off-phase at t=0.3", badge.visible is False))
    app.render_frame_at(0.5)                 # next on-phase
    checks.append(("blink on-phase at t=0.5", badge.visible is True))

    intel.publish_state("ship/red_alert", "off", source="intelligence-peer")
    time.sleep(1.0)
    app.render_frame_at(0.6)
    checks.append(("alarm OFF over MQTT -> reverts hidden ([CXC-BIND-C5])",
                   badge.visible is False))

    for label, passed in checks:
        print(("PASS " if passed else "FAIL ") + label)
    app.stop()
    intel.stop()
    return 0 if all(p for _, p in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
