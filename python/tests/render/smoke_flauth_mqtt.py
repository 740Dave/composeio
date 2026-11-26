"""
[SPEC:CXC-FLAUTH] smoke — the WHOLE Pass-3 loop over real infrastructure:

    author (Composer, Flow mode)  ->  Push (bus_storage :8420, the Pass-1
    BusStorageLoader write side)  ->  run (HeadlessRuntime on MQTT :1883,
    heartbeat on)  ->  CONFIRM back in the Composer (live port values via
    discovery + the _bus/headless/<rid>/health heartbeat on the deploy
    card) — [CXC-FLAUTH-C7]/[C9]/[C10] end to end.

    python3 tests/render/smoke_flauth_mqtt.py [broker]

Three peers on the ecosystem broker (default mqtt-broker.local):
  - the Composer (MQTT-bridged design bus) that authors + pushes + confirms
  - the headless runtime that fetches the roster from bus_storage and runs
  - a "rig" peer driving the logic node's input namespaces

NOTE: every MQTTTransport needs a DISTINCT client_id (broker kicks
duplicates, rc=7).  Artifacts land at bus://assets/headless/<run-id>*.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

BROKER = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")

PUMP_EXPR = "min(ns('pump/damaged') ? 2.5 : 10.0, 0.25 * ns('fuel/remaining'))"


def main() -> int:
    pid = os.getpid()
    rid = f"flauth-smoke-{pid}"
    checks = []

    def check(name, ok, detail=""):
        checks.append((name, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    # -- the Composer, MQTT-bridged --------------------------------------
    os.environ["COMPOSEUX_MQTT_BROKER"] = BROKER
    from composeio.composer.app import ComposerApp
    from composeio.render.engine.headless import (BusStorageLoader,
                                                  HeadlessRuntime,
                                                  health_namespace)
    from composeio.bus import ComposeIOBus
    from composeio.transport_mqtt import MQTTTransport
    from composeio.composer.flowpanel import build_graph

    a = ComposerApp("", windowed=False, connect_mqtt=True)
    a.start()
    rt = None
    try:
        check("Composer MQTT bridge up", a.core.mqtt_connected)
        time.sleep(2.0)                    # wide subscription settles

        # -- author: ＋Logic node, out-wire, expr, schedule ----------------
        flow, insp = a.ui.flow, a.ui.flow_inspector
        wid = flow.add_logic_node()
        flow.author_output_wire(wid, "pump/flow_rate")
        insp.refresh()
        ok_expr = insp.set_output_expr("pump/flow_rate", PUMP_EXPR)
        insp.set_schedule("bus_triggered",
                          triggers=["pump/damaged", "fuel/remaining"])
        check("authored a logic node (expr committed)", ok_expr, wid)

        # -- push to REAL bus_storage (Pass-1 loader write side) -----------
        insp.set_runtime_id(rid)
        insp.toggle_member(wid)
        insp.push()
        status = a.core.internal_bus.get_state("composer/deploy/status")
        check("Push wrote roster + widget to bus_storage",
              bool(status and status.get("ok")),
              status.get("roster", "?") if status else "no status")
        loader = BusStorageLoader()
        roster = loader.load_roster(rid)
        check("bus_storage serves the roster back", roster == [wid],
              str(roster))

        # -- run it: HeadlessRuntime over MQTT, heartbeat ON ----------------
        rt_bus = ComposeIOBus(
            bus_id=f"{rid}-rt",
            global_transport=MQTTTransport(broker=BROKER,
                                           client_id=f"{rid}-rt"))
        rt = HeadlessRuntime(bus=rt_bus, runtime_id=rid, heartbeat_s=0.5)
        rt.load(loader)
        rt_bus.start()
        rt.start()
        time.sleep(2.0)                    # subscriptions settle

        # -- rig drives the input over the wire -----------------------------
        rig = ComposeIOBus(
            bus_id=f"{rid}-rig",
            global_transport=MQTTTransport(broker=BROKER,
                                           client_id=f"{rid}-rig"))
        rig.start()
        time.sleep(2.0)                    # rig transport connects
        rig.ensure_namespace("fuel/remaining", engine_config="json")
        rig.publish_state("fuel/remaining", 62.4, source="rig")
        time.sleep(1.5)
        t = 0.5
        events = rt.advance_to(t)
        flow_evs = [v for _t, ns, v in events if ns == "pump/flow_rate"]
        check("deployed widget ran + published its expr output",
              bool(flow_evs) and flow_evs[-1] == 10.0, str(flow_evs))

        # -- CONFIRM in the Composer ([CXC-FLAUTH-CONFIRM]) ------------------
        # keep the runtime beating/re-publishing while MQTT delivers;
        # poll the Composer's projection until the readout goes live
        got_in = got_out = (False, None)
        hb = None
        deadline = time.time() + 10.0
        while time.time() < deadline:
            t += 0.5
            rt.advance_to(t)
            time.sleep(0.5)
            a.render.drain_updates()
            nodes, _ = build_graph(a.core)
            node = next(n for n in nodes if n.node_id == f"w:{wid}")
            got_in = node.port_values.get("fuel/remaining", (False, None))
            got_out = node.port_values.get("pump/flow_rate", (False, None))
            hb = a.core.discovery.headless_health(rid)
            if got_in[0] and got_out[0] and hb:
                break
        check("logic node's INPUT port live over the wire (C9)",
              got_in[0] and got_in[1] == 62.4, str(got_in))
        check("logic node's OUTPUT port live over the wire (C9)",
              got_out[0] and got_out[1] == 10.0, str(got_out))
        check("deploy card heartbeat from _bus/headless/<rid>/health (C10)",
              bool(hb) and hb.get("widgets") == 1,
              str(hb))
        text, running = insp._heartbeat_text(rid)
        check("heartbeat line reads 'running'", running, text)
        rig.stop()
    finally:
        if rt is not None:
            rt.stop()
        a.stop()
        os.environ.pop("COMPOSEUX_MQTT_BROKER", None)

    passed = sum(1 for _n, ok in checks if ok)
    print(f"\n{passed}/{len(checks)} checks passed")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
