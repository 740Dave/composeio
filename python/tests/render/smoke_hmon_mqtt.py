"""
[SPEC:CXC-HMON] smoke — the Headless Monitor over REAL infrastructure:

    a HeadlessRuntime on MQTT (:1883) runs the previs fuel_pump  ->  the
    Monitor (a second MQTT peer) requests tracing over the wire
    ([CXC-HMON-C6])  ->  a rig peer flips pump/damaged  ->  the Monitor
    reads the annotated tick (branch change + suppression) off
    `_bus/headless/*`  ->  a RAW fourth peer reads the identical trace
    ([CXC-HMON-C10])  ->  the Monitor detaches and tracing goes dark.

    python3 tests/render/smoke_hmon_mqtt.py [broker]

NOTE: every MQTTTransport needs a DISTINCT client_id (broker kicks
duplicates, rc=7) and ~2s post-connect settle before publishing.
"""

from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

BROKER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")

PUMP_EXPR = "ns('pump/damaged') ? 0.0 : min(ns('fuel/remaining'), 8.0)"


def pump_config() -> dict:
    return {
        "app": {"id": "hmon-smoke-pump"},
        "headless": {"schedule": {"mode": "bus_triggered",
                                  "trigger": ["pump/damaged",
                                              "fuel/remaining"]}},
        "variables": {"normal_rate": 8.0},
        "widgets": [{"type": "logic", "id": "fuel_pump",
                     "outputs": [{"namespace": "pump/flow_rate",
                                  "expr": PUMP_EXPR}]}],
    }


def main() -> int:
    pid = os.getpid()
    rid = f"hmon-smoke-{pid}"
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    from composeio.bus import ComposeIOBus
    from composeio.transport_mqtt import MQTTTransport
    from composeio.render.engine.headless import (HEADLESS_BUS_PREFIX,
                                                  HeadlessRuntime,
                                                  health_namespace,
                                                  state_namespace,
                                                  trace_namespace)
    from composeio.composer.monitor import annotate, build_monitor_app

    # -- peer 1: the runtime, MQTT-bridged, heartbeat ON -------------------
    rt_bus = ComposeIOBus(
        bus_id=f"{rid}-rt",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"{rid}-rt"))
    rt = HeadlessRuntime(bus=rt_bus, runtime_id=rid, heartbeat_s=0.5)
    rt.add_widget("fuel_pump", pump_config())
    rt_bus.start()                    # not owned by rt — start explicitly
    rt.start()
    time.sleep(2.0)                               # wide-subscription settle
    runner = threading.Thread(target=rt.run, kwargs={"duration": 30.0},
                              daemon=True)
    runner.start()

    # -- peer 2: the Monitor (fleet frame) ---------------------------------
    app = build_monitor_app(mqtt=f"{BROKER}:1883", windowed=False)
    # distinct client id is set inside build_monitor_app (pid-based)
    app.start()
    pane = app.comp.root

    # -- peer 3: the rig driving the inputs --------------------------------
    rig = ComposeIOBus(
        bus_id=f"{rid}-rig",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"{rid}-rig"))
    rig.start()

    # -- peer 4: a RAW reader (the "Bus Monitor / Intelligence" stand-in) --
    raw_bus = ComposeIOBus(
        bus_id=f"{rid}-raw",
        global_transport=MQTTTransport(broker=BROKER,
                                       client_id=f"{rid}-raw"))
    raw_seen: dict = {}
    raw_bus.subscribe_pattern(HEADLESS_BUS_PREFIX + "/*",
                              lambda ns, v, old: raw_seen.__setitem__(ns, v))
    raw_bus.start()
    time.sleep(2.5)

    try:
        # 1. runtime heartbeat reaches the Monitor's fleet rail
        deadline = time.time() + 8
        while time.time() < deadline and rid not in pane.model.runtimes:
            app.drain_updates()
            time.sleep(0.2)
        check("heartbeat -> fleet rail over the wire",
              rid in pane.model.runtimes)
        check("rail lists the widget",
              "fuel_pump" in pane.model.widgets_of(rid))

        # 2. drill-down: the Monitor ENABLES tracing over the bus
        pane.select("fuel_pump")
        deadline = time.time() + 8
        while time.time() < deadline and \
                not rt.widget("fuel_pump").trace.enabled:
            time.sleep(0.2)
        check("trace enable request crossed the broker",
              rt.widget("fuel_pump").trace.enabled)

        # 3. the damage story, driven by the rig
        rig.ensure_namespace("fuel/remaining", engine_config="json")
        rig.ensure_namespace("pump/damaged", engine_config="json")
        rig.publish_state("fuel/remaining", 62.4, source="rig")
        time.sleep(1.0)
        rig.publish_state("pump/damaged", True, source="rig")   # the event
        time.sleep(1.0)
        rig.publish_state("fuel/remaining", 64.0, source="rig")  # suppressed
        time.sleep(1.0)
        rig.publish_state("pump/damaged", False, source="rig")  # recovered
        time.sleep(2.0)
        app.drain_updates()

        ticks = pane.model.ticks("fuel_pump")
        check("trace ring arrived at the Monitor", len(ticks) >= 3,
              f"{len(ticks)} ticks")

        def taken(rec):
            for o in rec.get("outputs", []):
                for s in o.get("trace", []):
                    if s["kind"] == "ternary":
                        return s["taken"]
            return None

        branches = {taken(r) for r in ticks} - {None}
        check("branch change visible (then AND else ticks)",
              branches == {"then", "else"}, str(branches))
        check("a suppression is in the timeline",
              any(o.get("suppressed") == "unchanged"
                  for r in ticks for o in r.get("outputs", [])))

        rec = pane.model.hero_record()
        out = rec["outputs"][0] if rec else {}
        segs = annotate(out.get("expr", ""), out.get("trace", []))
        check("hero annotates: chips + taken + dead",
              any("chip" in s for s in segs)
              and any(s.get("role") == "taken" for s in segs)
              and any(s.get("role") == "dead" for s in segs))

        st = pane.model.state.get("fuel_pump")
        h = pane.model.health.get("fuel_pump")
        check("internal state over the wire ($vars + schedule)",
              bool(st and st.get("vars", {}).get("normal_rate") == 8.0
                   and st.get("schedule", {}).get("mode")
                   == "bus_triggered"))
        check("per-widget health over the wire",
              bool(h and h.get("ticks", 0) > 0
                   and h.get("last_error") is None))

        # 4. bus-native: the raw peer read the SAME trace
        raw_trace = raw_seen.get(trace_namespace("fuel_pump"))
        check("raw peer reads the identical trace family",
              bool(raw_trace and raw_trace.get("ticks"))
              and state_namespace("fuel_pump") in raw_seen
              and health_namespace("fuel_pump") in raw_seen)

        # 5. detach: the Monitor disables what it enabled
        app.stop()
        deadline = time.time() + 8
        while time.time() < deadline and \
                rt.widget("fuel_pump").trace.enabled:
            time.sleep(0.2)
        check("detach disables tracing (fleet left as found)",
              not rt.widget("fuel_pump").trace.enabled)
    finally:
        try:
            rt.stop()
        except Exception:
            pass
        for b in (rt_bus, rig, raw_bus):
            try:
                b.stop()
            except Exception:
                pass

    passed = sum(1 for _, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
