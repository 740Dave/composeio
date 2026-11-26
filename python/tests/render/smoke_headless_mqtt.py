"""
[SPEC:CXC-HEADLESS] smoke — the jump-drive triad over REAL MQTT
([CXC-HW-C12] the bus seam, [CXC-HW-C6] decoupling): the IDENTICAL
widget configs that pass the in-process goldens run on a runtime whose
bus rides MQTTTransport (:1883), and a remote peer (standing in for an
Intelligence or a physical ESP32 sensor) drives pump/damaged and
ship/charge — zero widget changes.

    python3 tests/render/smoke_headless_mqtt.py [broker]

Two bus peers on the ecosystem broker (default mqtt-broker.local):
  - the headless runtime hosting fuel_tank + fuel_pump + jump_engine
  - a "damage control" peer: bare ComposeIOBus + MQTTTransport that
    flips the damage bit and the capacitor charge, and OBSERVES
    jumpdrive/readiness coming back over the wire.

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
EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples",
                        "headless")

from composeio.bus import ComposeIOBus
from composeio.transport_mqtt import MQTTTransport
from composeio.render.engine.headless import HeadlessRuntime, LocalFileLoader


def main() -> int:
    pid = os.getpid()
    rt_bus = ComposeIOBus(
        bus_id="headless-smoke-rt",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"headless-rt-{pid}"))
    rt = HeadlessRuntime(bus=rt_bus, runtime_id="jump-drive-01")
    rt.load(LocalFileLoader(EXAMPLES))
    rt_bus.start()            # externally-owned bus: start it ourselves
    rt.start()

    peer = ComposeIOBus(
        bus_id="damage-control",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"headless-peer-{pid}"))
    peer.start()
    peer.ensure_namespace("pump/damaged", engine_config="json")
    peer.ensure_namespace("ship/charge", engine_config="json")
    peer.ensure_namespace("jumpdrive/readiness", engine_config="json")
    seen_readiness = []
    peer.subscribe("jumpdrive/readiness",
                   lambda ns, new, old: seen_readiness.append(new))
    time.sleep(1.5)                          # both peers subscribed

    checks = []
    t = 0.5
    events = rt.advance_to(t)
    flow = [v for _, ns, v in events if ns == "pump/flow_rate"]
    checks.append(("triad ticking on MQTT-bridged bus (flow published)",
                   bool(flow) and flow[0] == 10.0))

    peer.publish_state("ship/charge", 85.0, source="damage-control")
    time.sleep(1.0)                          # MQTT round trip
    t += 0.5
    events = rt.advance_to(t)
    ready = [v for _, ns, v in events if ns == "jumpdrive/readiness"]
    checks.append(("charge 85 over MQTT -> readiness 1.0",
                   bool(ready) and ready[-1] == 1.0))

    peer.publish_state("pump/damaged", "on", source="damage-control")
    time.sleep(1.0)
    t += 0.5
    events = rt.advance_to(t)
    flow = [v for _, ns, v in events if ns == "pump/flow_rate"]
    checks.append(("damage bit over MQTT -> flow 2.5",
                   bool(flow) and flow[-1] == 2.5))

    peer.publish_state("ship/charge", 10.0, source="damage-control")
    time.sleep(1.0)
    t += 0.5
    events = rt.advance_to(t)
    ready = [v for _, ns, v in events if ns == "jumpdrive/readiness"]
    checks.append(("charge 10 over MQTT -> readiness 0.0",
                   bool(ready) and ready[-1] == 0.0))

    time.sleep(1.0)                          # let outbound publishes land
    checks.append(("peer OBSERVED readiness transitions over the wire",
                   1.0 in [float(v) for v in seen_readiness] and
                   0.0 in [float(v) for v in seen_readiness]))

    for label, passed in checks:
        print(("PASS " if passed else "FAIL ") + label)
    rt.stop()
    rt_bus.stop()
    peer.stop()
    return 0 if all(p for _, p in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
