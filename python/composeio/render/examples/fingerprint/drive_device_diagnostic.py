"""Headless driver + proof for the per-device DIAGNOSTIC view ("R503
internals" panel) — the deeper, single-device sibling of the FLEET view
(``fleet_status.py`` / ``drive_fleet.py``).

Scripts ONE real reference device (``composeio.peripherals.fingerprint.
device.FingerprintDevice`` + ``MockFingerprintHAL`` — the SAME firmware twin
every other fingerprint surface drives, not a hand-built fixture) through the
exact scripted sequence the deliverable calls for, all published on a REAL
in-process bus so the widget observes REAL FPBUS traffic, not injected rows:

  1. push_needed        — unconfigured, first heartbeat.
  2. a REAL push receipt (``receive_push``) -> template_set_loaded, holding
     a template_set_uuid, bridging TWO people to two roles (one WITH the
     gated capability, one WITHOUT — set up for the granted/denied pair
     below).
  3. ready heartbeat     — now configured.
  4. a GRANTED  auth_event — adama/commander, role HAS open_hatch.
  5. a DENIED   auth_event — roslin/president, role LACKS open_hatch.
  6. an UNRECOGNIZED auth_event — a NO_MATCH poll, no bridge entry.
  7. a template_set_failed, axis=transport — a SECOND push (different uuid)
     with an injected store failure on the first install, so the device
     reports, HOLDS the stamp (the widget's header must still show the
     FIRST uuid, never the failed one), and empties itself
     ([CXC-FPBUS-REQ-LOAD-FAILURE]).

The widget wires itself directly to this one device_id via
``bus.on_message`` (no controller module needed — see
``widgets/fingerprint_device_diagnostic.py``); this driver only plays the
device side of the wire.

Run:
    SDL_VIDEODRIVER=dummy PYTHONPATH=python:services python3 -m \
        composeio.render.examples.fingerprint.drive_device_diagnostic
"""

from __future__ import annotations

import os
import sys
import time

from composeio.bus import ComposeIOBus
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.runtime import RenderApp

# self-register `fingerprint_device_diagnostic`; also loaded via the scene's
# widget_modules:
from composeio.render.widgets import fingerprint_device_diagnostic  # noqa: F401

from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.hal import FailureAxis, MatchOutcome, MatchResult, Score
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL

_SCENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fingerprint", "device_diagnostic.compose.yaml")

GATED_CAP = "open_hatch"
DEVICE_ID = "dev/s3-fp1"          # path-shaped, per [CXC-FPBUS-ADDRESSING]

UUID_1 = "11111111-2222-3333-4444-555555555555"
UUID_2 = "99999999-8888-7777-6666-555555555555"


def build(bus=None):
    """Wire bus + app + one reference device, return (app, bus, device, hal)."""
    bus = bus or ComposeIOBus()
    comp = parse_compose_yaml(_SCENE)
    app = RenderApp(comp, bus=bus, windowed=False)
    app.start()

    hal = MockFingerprintHAL()
    device = FingerprintDevice(
        hal=hal, bus=bus, device_id=DEVICE_ID,
        gated_capability=GATED_CAP, clock=time.time)
    return app, bus, device, hal


def script_device(device: FingerprintDevice, hal: MockFingerprintHAL) -> None:
    """Play the exact scripted sequence the deliverable calls for, through
    the REAL device logic (receive_push / emit_heartbeat / poll_match) —
    every published event is device-decided, not a hand-built payload."""

    # 1) push_needed — unconfigured, first heartbeat.
    device.emit_heartbeat()

    # 2) a REAL push: two people, one role WITH the gated capability, one
    #    WITHOUT (sets up the granted/denied pair below).
    push = {
        "template_set_uuid": UUID_1,
        "entries": [
            {"person": "adama", "ordinal": 1, "character": "commander",
             "rbac": "command", "template": hal._synth_template("adama-1")},
            {"person": "roslin", "ordinal": 1, "character": "president",
             "rbac": "civilian", "template": hal._synth_template("roslin-1")},
        ],
        "role_capability_table": {"command": [GATED_CAP], "civilian": []},
    }
    status = device.receive_push(push)          # -> publishes template_set_loaded
    assert status == "loaded", f"expected a clean load, got {status!r}"
    # bridge is keyed by allocated Slot; recover each person's slot by
    # lookup rather than assuming allocation order.
    slot_by_person = {v.person: k for k, v in device.bridge.items()}
    adama_slot = slot_by_person["adama"]
    roslin_slot = slot_by_person["roslin"]

    # 3) ready heartbeat — now configured.
    device.emit_heartbeat()

    # 4) GRANTED — adama/commander, role HAS open_hatch.
    hal.script_match(MatchResult(MatchOutcome.MATCHED, slot=adama_slot, score=Score(220)))
    outcome = device.poll_match()
    assert outcome is not None and outcome.value == "granted"

    # 5) DENIED — roslin/president, role LACKS open_hatch.
    hal.script_match(MatchResult(MatchOutcome.MATCHED, slot=roslin_slot, score=Score(220)))
    outcome = device.poll_match()
    assert outcome is not None and outcome.value == "denied"

    # 6) UNRECOGNIZED — a NO_MATCH poll, no bridge entry.
    hal.script_match(MatchResult(MatchOutcome.NO_MATCH))
    outcome = device.poll_match()
    assert outcome is not None and outcome.value == "unrecognized"

    # 7) template_set_failed, axis=transport — a second push, different
    #    uuid, injected store failure on the FIRST install of this cycle.
    #    The device HOLDS the stamp at UUID_1 (never advances to UUID_2) and
    #    empties itself on the successful cleanup delete.
    hal.inject_store_failure(1, FailureAxis.TRANSPORT)
    push2 = {
        "template_set_uuid": UUID_2,
        "entries": [
            {"person": "tigh", "ordinal": 1, "character": "xo",
             "rbac": "command", "template": hal._synth_template("tigh-1")},
        ],
        "role_capability_table": {"command": [GATED_CAP]},
    }
    status2 = device.receive_push(push2)         # -> publishes template_set_failed
    assert status2 == "load_failed", f"expected a load failure, got {status2!r}"


def main(out_png="/tmp/fp_device_diagnostic.png"):
    app, bus, device, hal = build()

    script_device(device, hal)

    app.drain_updates()
    app.render_headless(out_png)

    w = _find_widget(app, "v_diag")

    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    check("widget mounted and found", w is not None)
    if w is not None:
        check("6 events observed (push_needed, loaded, ready, granted, "
              "denied, unrecognized, failed = 7 rows)", len(w.rows) == 7)
        kinds = [r["label"] for r in w.rows]
        check("most-recent-first: TEMPLATE SET FAILED is row 0",
              kinds and kinds[0] == "TEMPLATE SET FAILED")
        check("row 0 is flagged alert", w.rows and w.rows[0]["alert"] is True)
        check("row 0 detail carries AXIS=transport",
              w.rows and "AXIS=transport" in w.rows[0]["detail"])
        check("row 0 detail carries a DETAIL= field",
              w.rows and "DETAIL=" in w.rows[0]["detail"])
        check("row 0 detail shows SENSOR_CODE=— (None on transport, "
              "per [CXC-FPHAL-FAILURE-AXES])",
              w.rows and "SENSOR_CODE=—" in w.rows[0]["detail"])
        check("the held template_set_uuid is STILL uuid1 (HELD, never "
              "advanced to the failed uuid2)",
              w.template_set_uuid == UUID_1)
        labels = {r["label"] for r in w.rows}
        check("all six other event kinds are represented",
              labels == {"HEARTBEAT: PUSH NEEDED", "TEMPLATE SET LOADED",
                        "HEARTBEAT: READY", "AUTH: GRANTED", "AUTH: DENIED",
                        "AUTH: UNRECOGNIZED", "TEMPLATE SET FAILED"})
        check("header device_id is the path-shaped id, unmangled",
              w.device_id == DEVICE_ID)

    check("capture PNG written", os.path.exists(out_png)
          and os.path.getsize(out_png) > 0)

    print(f"\nPNG: {out_png}")
    if w is not None:
        print(f"device_id={w.device_id} heartbeat_state={w.heartbeat_state} "
              f"template_set_uuid={w.template_set_uuid} "
              f"attempts={w.count_authentication_attempts}")
        print(f"rows ({len(w.rows)}, most-recent-first):")
        for r in w.rows:
            print(f"   {r['label']:<24} {r['detail']}")
    print("\n" + ("ALL GREEN" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


def _find_widget(app, widget_id: str):
    """RenderApp exposes no widget-by-id lookup; walk the tree from
    ``app.comp.root`` (the parsed Composition's Container tree)."""
    root = getattr(getattr(app, "comp", None), "root", None)
    if root is None:
        return None
    stack = [root]
    while stack:
        node = stack.pop()
        if getattr(node, "widget_id", None) == widget_id:
            return node
        stack.extend(getattr(node, "children", None) or [])
    return None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fp_device_diagnostic.png"))
