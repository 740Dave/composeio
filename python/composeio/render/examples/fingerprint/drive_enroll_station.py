"""Headless driver + proof for the enrollment-STATION capture CEREMONY.

Distinct from ``drive.py`` (drives the BINDING UX's single-shot "Capture
finger" button) and ``drive_monitor.py`` (drives the runtime auth MONITOR):
this scripts the RICH ceremony BEHIND that button — one person, template 1
then template 2, each running its ``captures_per_template`` captures with
the live feedback states, INCLUDING at least one CAPTURE_RETRY, through to
both templates captured and uploaded into the superset.

The ceremony runs entirely IN-PROCESS against the mock reference apparatus
(``EnrollmentStation`` — see its module docstring for why: it sidesteps
``DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F``, the live
``enroll_complete`` bus round-trip's raw-bytes-over-JSON bug, rather than
depending on it).

Run:
    SDL_VIDEODRIVER=dummy PYTHONPATH=python:services python3 -m \
        composeio.render.examples.fingerprint.drive_enroll_station
"""

from __future__ import annotations

import os
import sys

from composeio.bus import ComposeIOBus
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.runtime import RenderApp

# self-registers `enroll_ceremony`; also loaded via the scene's widget_modules:
from composeio.render.widgets import enroll_ceremony  # noqa: F401

from composeio.render.examples.fingerprint.enroll_station import (
    EnrollmentStation, VIEW_STATION)

_SCENE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fingerprint_enroll_station.compose.yaml")

PERSON = "starbuck"
DISPLAY_NAME = "Kara Thrace"


def build(bus=None):
    """Wire bus + station controller + app, return (app, station, bus)."""
    bus = bus or ComposeIOBus()
    station = EnrollmentStation(bus)
    comp = parse_compose_yaml(_SCENE)
    app = RenderApp(comp, bus=bus, windowed=False)
    app.start()
    return app, station, bus


def main(out_png="/tmp/fp_enroll_station.png"):
    app, station, bus = build()

    retry_png = out_png.replace(".png", "_retry.png") if out_png.endswith(".png") \
        else out_png + "_retry"

    # Capture a MID-ceremony frame the instant CAPTURE_RETRY is shown, so the
    # retry state is proven visually, not just asserted from the final tally.
    _retry_captured = {"done": False}

    def _on_step(payload):
        if payload.get("feedback") == "capture_retry" and not _retry_captured["done"]:
            app.drain_updates()
            app.render_headless(retry_png)
            _retry_captured["done"] = True

    station.on_step = _on_step

    # Full two-template ceremony for one person: template 1 captures clean
    # (2 captures, no retry); template 2 has a scripted CAPTURE_RETRY on its
    # FIRST placement attempt (a bad placement, lift, place again) so the
    # retry path is proven, not just declared.
    final = station.enroll_person(
        PERSON, DISPLAY_NAME,
        ordinals=(1, 2),
        retry_at={2: {1}},
    )

    app.drain_updates()
    app.render_headless(out_png)

    live = bus.get_state(VIEW_STATION) or {}
    t1 = next((t for t in live.get("templates", []) if t.get("ordinal") == 1), {})
    t2 = next((t for t in live.get("templates", []) if t.get("ordinal") == 2), {})

    caps = station.hal.capabilities()
    ordinals_captured = station.store.ordinals_for(PERSON)

    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    check("ceremony reports done", bool(live.get("done")))
    check("ceremony reports the right person",
          live.get("person") == PERSON and live.get("display_name") == DISPLAY_NAME)
    check("template_count == 2", live.get("template_count") == 2)
    check("template 1 uploaded, no retries",
          t1.get("status") == "uploaded" and t1.get("retries", -1) == 0)
    check("template 2 uploaded, at least one retry VISIBLE",
          t2.get("status") == "uploaded" and t2.get("retries", 0) >= 1)
    check("captures_per_template announced (not hard-coded) and both templates "
          "ran that many accepted captures",
          caps.captures_per_template == 2)
    check("superset gained BOTH templates keyed by (person, ordinal)",
          sorted(ordinals_captured) == [1, 2])
    check("enrolled_count reflects both templates at the end",
          live.get("enrolled_count") == 2)
    check("final feedback state is idle (ceremony at rest)",
          live.get("feedback") == "idle")
    check("NO slot anywhere in the published station state",
          "slot" not in str(live).lower())
    check("no slot in the superset template records either (TemplateRecord "
          "has no slot field at all)",
          all("slot" not in vars(rec) for rec in station.store.templates.values()))
    check("capture PNG written", os.path.exists(out_png) and
          os.path.getsize(out_png) > 0)
    check("mid-ceremony CAPTURE_RETRY PNG written (retry SEEN, not just tallied)",
          _retry_captured["done"] and os.path.exists(retry_png) and
          os.path.getsize(retry_png) > 0)

    print(f"\nPNG (final, completed ceremony): {out_png}")
    print(f"PNG (mid-ceremony, CAPTURE_RETRY): {retry_png}")
    print(f"final station state: {live}")
    print(f"superset ordinals for {PERSON}: {ordinals_captured}")
    print(f"HAL feedback_log ({len(station.hal.feedback_log)} entries):")
    for state, progress in station.hal.feedback_log:
        print(f"   {state.value:<18} {progress}")
    print("\n" + ("ALL GREEN" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fp_enroll_station.png"))
