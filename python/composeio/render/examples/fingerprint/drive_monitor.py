"""Headless driver + proof for the runtime auth-event MONITOR (the "cylon
detector").

Reuses the SAME shared-bus + FingerprintCastingBinder + reference-device
apparatus as ``drive.py`` (the binding UI's driver) to run a REAL cast +
roster push, then drives THREE real authentication attempts through the
REAL device match/resolve path (``MockFingerprintHAL.script_match`` +
``FingerprintDevice.poll_match``) — granted, denied, unrecognized — exactly
the [CXC-FPBUS-MSG-AUTH-EVENT] flow a physical R503 would produce. The NEW
``AuthEventMonitor`` controller (``auth_monitor.py``) republishes them as
bus STATE for the ``auth_event_feed`` + reused ``info_list`` widgets.

Run:
    SDL_VIDEODRIVER=dummy PYTHONPATH=python:services python3 -m \
        composeio.render.examples.fingerprint.drive_monitor
"""

from __future__ import annotations

import os
import sys

from composeio.bus import ComposeIOBus
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.runtime import RenderApp

# self-register `info_list` and `auth_event_feed`; also loaded via the
# scene's widget_modules:
from composeio.render.widgets import fingerprint_panel  # noqa: F401
from composeio.render.widgets import auth_event_feed  # noqa: F401

from composeio.render.examples.fingerprint.binder import (
    FingerprintCastingBinder, DEFAULT_MODULE)
from composeio.render.examples.fingerprint.auth_monitor import (
    AuthEventMonitor, VIEW_FEED, VIEW_SUMMARY)

from composeio.peripherals.fingerprint.hal import MatchOutcome, MatchResult, Score

_SCENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fingerprint_monitor.compose.yaml")

GATED_CAP = "open_hatch"


def _cmd(bus, ns, payload):
    """Publish a command exactly as the casting scene's emit_button would."""
    bus.ensure_namespace(ns, engine_config="json")
    bus.publish_state(ns, payload, source="drive")


def build(bus=None):
    """Wire bus + binder (setup) + monitor (runtime, the surface under test)
    + app, return (app, binder, monitor, bus)."""
    bus = bus or ComposeIOBus()
    binder = FingerprintCastingBinder(bus, gated_capability=GATED_CAP)
    binder.wire()
    monitor = AuthEventMonitor(bus)
    comp = parse_compose_yaml(_SCENE)
    app = RenderApp(comp, bus=bus, windowed=False)
    app.start()
    return app, binder, monitor, bus


def cast_and_push(bus):
    """Cast one person with the gated capability (command role) and one
    without (civilian role); push the roster to the reference device — the
    REAL enroll + push ceremony, mirroring drive.py's run_scenario."""
    _cmd(bus, "fp/cmd/add_person", {"person_id": "adama", "display_name": "William Adama"})
    _cmd(bus, "fp/cmd/add_person", {"person_id": "roslin", "display_name": "Laura Roslin"})
    for pid in ("adama", "roslin"):
        _cmd(bus, "fp/cmd/capture_template", {"person": pid, "ordinal": 1})
    _cmd(bus, "fp/cmd/define_role", {"role": "command", "capabilities": GATED_CAP})
    _cmd(bus, "fp/cmd/define_role", {"role": "civilian", "capabilities": ""})
    _cmd(bus, "fp/cmd/define_character", {"character": "commander", "role": "command"})
    _cmd(bus, "fp/cmd/define_character", {"character": "president", "role": "civilian"})
    _cmd(bus, "fp/cmd/cast", {"person": "adama", "character": "commander"})
    _cmd(bus, "fp/cmd/cast", {"person": "roslin", "character": "president"})
    _cmd(bus, "fp/cmd/start_game", {"module": DEFAULT_MODULE})


def drive_auth_events(binder):
    """Script THREE real match/resolve passes through the reference device:

      1) GRANTED      — adama / commander / 'command' role HAS open_hatch
      2) DENIED       — roslin / president / 'civilian' role LACKS open_hatch
      3) UNRECOGNIZED — no bridge entry matches (a NO_MATCH poll)

    Each call runs the same ``FingerprintDevice.resolve_match`` path the
    firmware twin uses, so the auth_event published on the bus is the real
    device-decided outcome, not a hand-built payload.
    """
    device = binder.device.device
    hal = binder.device.hal

    slot_by_person = {e.person: slot for slot, e in device.bridge.items()}
    assert "adama" in slot_by_person and "roslin" in slot_by_person, \
        "roster push did not install the expected bridge entries"

    hal.script_match(MatchResult(MatchOutcome.MATCHED,
                                  slot=slot_by_person["adama"], score=Score(90)))
    out_granted = device.poll_match()

    hal.script_match(MatchResult(MatchOutcome.MATCHED,
                                  slot=slot_by_person["roslin"], score=Score(90)))
    out_denied = device.poll_match()

    hal.script_match(MatchResult(MatchOutcome.NO_MATCH))
    out_unrecognized = device.poll_match()

    return out_granted, out_denied, out_unrecognized


def main(out_png="/tmp/fp_auth_monitor.png"):
    app, binder, monitor, bus = build()

    # 1) real cast + roster push (setup, via the existing binder)
    cast_and_push(bus)

    # 2) drive three REAL auth attempts through the reference device
    outcomes = drive_auth_events(binder)

    # 3) let the monitor's republished state reach the widgets, then capture
    app.drain_updates()
    app.render_headless(out_png)

    feed = bus.get_state(VIEW_FEED) or []
    summary = bus.get_state(VIEW_SUMMARY) or ""

    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    check("device resolved granted -> denied -> unrecognized in order",
          [o.value for o in outcomes] == ["granted", "denied", "unrecognized"])
    check("monitor feed has exactly 3 events", len(feed) == 3)

    outcomes_seen = {e.get("outcome") for e in feed}
    check("all three distinct outcomes present in the feed",
          outcomes_seen == {"granted", "denied", "unrecognized"})

    granted_ev = next((e for e in feed if e.get("outcome") == "granted"), None)
    denied_ev = next((e for e in feed if e.get("outcome") == "denied"), None)
    unrecog_ev = next((e for e in feed if e.get("outcome") == "unrecognized"), None)

    check("granted event carries player=adama character=commander",
          bool(granted_ev) and granted_ev.get("player") == "adama"
          and granted_ev.get("character") == "commander")
    check("denied event carries player=roslin character=president",
          bool(denied_ev) and denied_ev.get("player") == "roslin"
          and denied_ev.get("character") == "president")
    check("unrecognized event carries NO player/character (absent, not null)",
          bool(unrecog_ev) and "player" not in unrecog_ev
          and "character" not in unrecog_ev)

    check("device summary published for the device",
          bool(summary) and binder.device_id in summary)
    check("capture PNG written", os.path.exists(out_png)
          and os.path.getsize(out_png) > 0)

    print(f"\nPNG: {out_png}")
    print(f"feed ({len(feed)} events):")
    for e in feed:
        print(f"   {e}")
    print(f"summary:\n{summary}")
    print("\n" + ("ALL GREEN" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fp_auth_monitor.png"))
