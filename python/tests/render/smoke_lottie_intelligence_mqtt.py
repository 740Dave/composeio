"""
[SPEC:CXC-LOTTIE] smoke — an Intelligence peer over REAL MQTT drives the
dradis-as-Lottie transport exactly like any bus publisher
([CXC-LOT-PLAYBACK] / [CXC-CREDO]): it publishes STATE (speed, segment,
hold) and the widget owns the motion; frames never cross the bus.

    python3 tests/render/smoke_lottie_intelligence_mqtt.py [broker]

Two bus peers on the ecosystem broker (default mqtt-broker.local):
  - the render app (headless), playing the bus-hosted dradis Lottie
    (bus://assets/animations/dradis_lottie.json — [CXC-LOT-C7] live)
  - an "Intelligence" peer: bare ComposeIOBus + MQTTTransport publishing
    transport state, exactly as intelligence_mcp does.

NOTE: each MQTTTransport needs a DISTINCT client_id — the broker kicks
duplicate sessions (rc=7 reconnect loop) and delivery silently fails.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

BROKER = sys.argv[1] if len(sys.argv) > 1 else "mqtt-broker.local"

from composeio.bus import ComposeIOBus
from composeio.transport_mqtt import MQTTTransport
from composeio.render.engine.parser import parse_widget_tree
from composeio.render.engine.runtime import RenderApp

CONFIG = {
    "design": {"width": 256, "height": 256, "title": "dradis-lottie-mqtt"},
    "widgets": [{
        "type": "pixel_stack", "id": "scene", "width": 256, "height": 256,
        "background": "#0A0F0A",
        "children": [{
            "type": "lottie", "id": "dradis",
            "src": "bus://assets/animations/dradis_lottie.json",
            "x": 0, "y": 0, "w": 256, "h": 256,
            "speed": {"namespace": "dradis/speed", "default": 1.0},
            "play_segments": {"namespace": "dradis/segment"},
            "go_to_and_stop": {"namespace": "dradis/hold"},
        }],
    }],
}


def main() -> int:
    render_bus = ComposeIOBus(
        bus_id="lottie-render",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"lottie-render-{os.getpid()}"))
    app = RenderApp(parse_widget_tree(CONFIG), windowed=False,
                    bus=render_bus)
    render_bus.start()
    app.start()

    intel = ComposeIOBus(
        bus_id="lottie-intelligence",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"lottie-intel-{os.getpid()}"))
    intel.start()
    for ns in ("dradis/speed", "dradis/segment", "dradis/hold"):
        intel.ensure_namespace(ns, engine_config="json")
    time.sleep(1.0)                          # both peers subscribed

    w = next(x for x in app.comp.root.iter_widgets()
             if x.widget_id == "dradis")
    checks = []

    app.render_frame_at(0.0)
    checks.append(("Lottie fetched from bus:// over the asset manager",
                   w._anim is not None))
    app.render_frame_at(1.0)                 # fr 60 -> frame 60 at 1x
    f_1x = w._frame

    intel.publish_state("dradis/speed", 2.0, source="intelligence-peer")
    time.sleep(1.0)                          # MQTT round trip
    app.render_frame_at(1.5)                 # +0.5s at 2x = +60 frames
    checks.append(("Intelligence speed=2.0 doubles the rate next frame",
                   w.speed == 2.0 and abs(w._frame - (f_1x + 60.0)) < 0.01))

    intel.publish_state("dradis/segment", "calibrate",
                        source="intelligence-peer")
    time.sleep(1.0)
    app.render_frame_at(2.0)                 # restarts at marker start 120
    checks.append(("Intelligence playSegments('calibrate') enters [120,180)",
                   120.0 <= (w._frame or -1) < 180.0))

    intel.publish_state("dradis/hold", 0.25, source="intelligence-peer")
    time.sleep(1.0)
    app.render_frame_at(3.0)
    app.render_frame_at(9.0)
    checks.append(("Intelligence goToAndStop(0.25) holds mid-calibrate",
                   w._frame == 135.0))       # 120 + 0.25 * 60

    intel.publish_state("dradis/hold", "", source="intelligence-peer")
    intel.publish_state("dradis/segment", "sweep", source="intelligence-peer")
    time.sleep(1.0)
    app.render_frame_at(10.0)
    app.render_frame_at(10.5)                # +0.5s at 2x = 60 frames in
    checks.append(("release + playSegments('sweep') resumes the loop",
                   0.0 <= (w._frame or -1) < 120.0))

    app.stop()
    intel.stop()

    ok = True
    for label, passed in checks:
        print(("PASS  " if passed else "FAIL  ") + label)
        ok = ok and passed
    print(f"{sum(1 for _, p in checks if p)}/{len(checks)} checks passed "
          f"(broker: {BROKER})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
