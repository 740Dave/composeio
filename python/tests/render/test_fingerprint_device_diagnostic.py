"""
Headless compliance tests for ``FingerprintDeviceDiagnosticWidget`` (the
"R503 internals" per-device diagnostic panel — the deeper, single-device
sibling of ``fleet_status``'s roster-wide table).

Drives the widget through the SAME reference apparatus the driver uses
(``FingerprintDevice`` + ``MockFingerprintHAL`` — the AUTHORITATIVE firmware
twin every other fingerprint surface is verified against, never a hand-built
fixture) so a passing test proves the widget renders REAL FPBUS traffic, not
injected rows. Reuses ``drive_device_diagnostic.script_device`` as the single
source of truth for the scripted sequence (push_needed -> loaded -> ready ->
granted -> denied -> unrecognized -> a template_set_failed axis=transport)
rather than re-deriving it here.

No canvas backend is required for the structural assertions (pure widget
state); the pixel assertions render one real SkiaCanvas frame, mirroring
``test_gauge_led_bar.py``'s idiom (mount -> draw -> sample pixels), which is
the render-test idiom this deliverable was told to follow.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.bus import ComposeIOBus
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets.fingerprint_device_diagnostic import (
    FingerprintDeviceDiagnosticWidget, _ALERT_ROW_BG)

from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL

from composeio.render.examples.fingerprint.drive_device_diagnostic import (
    DEVICE_ID, GATED_CAP, UUID_1, script_device)

from .conftest import surface_to_array


class _FakeCtx:
    """Minimal engine-context stand-in: a bus + an IMMEDIATE schedule_update
    (the widget must not depend on a real render-thread queue to be
    testable) + a request_redraw() call counter, so the damage discipline
    ([CXC-R-DAMAGE] — ``request_redraw()`` with NO args, only) is
    observable without mounting a full RenderApp."""

    def __init__(self, bus):
        self.bus = bus
        self.redraw_calls: list = []

    def schedule_update(self, fn):
        fn()

    def request_redraw(self, rect=None):
        self.redraw_calls.append(rect)


def _build_wired_widget():
    """One widget, mounted on a REAL bus, bound to DEVICE_ID — returns
    (widget, ctx, bus, device, hal)."""
    bus = ComposeIOBus()
    widget = FingerprintDeviceDiagnosticWidget(
        "v_diag", title="R503 INTERNALS", device_id=DEVICE_ID, max_rows=30)
    ctx = _FakeCtx(bus)
    widget.on_mount(ctx)

    hal = MockFingerprintHAL()
    device = FingerprintDevice(
        hal=hal, bus=bus, device_id=DEVICE_ID,
        gated_capability=GATED_CAP, clock=lambda: 0.0)
    return widget, ctx, bus, device, hal


# ---------------------------------------------------------------------------
# Structural: every event kind lands as a row, most-recent-first
# ---------------------------------------------------------------------------

def test_all_seven_scripted_events_render_as_rows_most_recent_first():
    widget, ctx, bus, device, hal = _build_wired_widget()

    script_device(device, hal)

    assert len(widget.rows) == 7
    labels = [r["label"] for r in widget.rows]
    assert labels == [
        "TEMPLATE SET FAILED",     # most recent
        "AUTH: UNRECOGNIZED",
        "AUTH: DENIED",
        "AUTH: GRANTED",
        "HEARTBEAT: READY",
        "TEMPLATE SET LOADED",
        "HEARTBEAT: PUSH NEEDED",  # oldest
    ]
    # request_redraw() fired once per applied event, with NO args from the
    # widget's own call site (Widget.request_redraw() supplies the rect
    # internally; the widget itself never passes one) — the damage
    # discipline this deliverable is held to.
    assert len(ctx.redraw_calls) == 7


def test_header_health_strip_reflects_latest_observed_state():
    widget, ctx, bus, device, hal = _build_wired_widget()
    script_device(device, hal)

    assert widget.device_id == DEVICE_ID
    # a heartbeat was never re-emitted after the failed push, so the header
    # still reads the LAST OBSERVED heartbeat (ready) — this widget reflects
    # what it has SEEN, never a polled/inferred current state (that is
    # fleet_status's job, not this widget's).
    assert widget.heartbeat_state == "ready"
    # the held uuid must be UUID_1: a load failure HOLDS the stamp
    # ([CXC-FPBUS-REQ-LOAD-FAILURE]) — the header must never advance to the
    # uuid that FAILED.
    assert widget.template_set_uuid == UUID_1
    assert widget.count_authentication_attempts == 0


def test_auth_event_rows_carry_who_or_unrecognized():
    widget, ctx, bus, device, hal = _build_wired_widget()
    script_device(device, hal)

    by_label = {r["label"]: r for r in widget.rows}
    assert by_label["AUTH: GRANTED"]["detail"] == "adama -> commander"
    assert by_label["AUTH: DENIED"]["detail"] == "roslin -> president"
    assert by_label["AUTH: UNRECOGNIZED"]["detail"] == "unrecognized"


# ---------------------------------------------------------------------------
# The template_set_failed ALERT row — axis + detail + sensor_code visible
# ---------------------------------------------------------------------------

def test_template_set_failed_is_the_alert_row_with_axis_detail_sensor_code():
    widget, ctx, bus, device, hal = _build_wired_widget()
    script_device(device, hal)

    alert_row = widget.rows[0]
    assert alert_row["label"] == "TEMPLATE SET FAILED"
    assert alert_row["alert"] is True
    assert alert_row["color"] == (0xE7, 0x4C, 0x3C)  # style.RED — reads as ALERT

    detail = alert_row["detail"]
    assert "AXIS=transport" in detail
    assert "DETAIL=" in detail and "injected store failure" in detail
    # sensor_code is None on the transport axis ([CXC-FPHAL-FAILURE-AXES] —
    # "MUST be None on transport"); the row renders that as "—", never the
    # literal string "None".
    assert "SENSOR_CODE=—" in detail
    assert "None" not in detail


def test_sensor_code_present_axis_renders_the_code_not_a_dash():
    """A different axis (sensor_refusal) DOES carry a sensor_code — pin that
    the row renders the actual code, not just the transport-axis dash, by
    exercising the pure formatter directly (no bus/device apparatus needed
    for this one — it is a formatting-logic pin, not a wiring pin)."""
    widget = FingerprintDeviceDiagnosticWidget(
        "v", title="", device_id=DEVICE_ID)
    row = widget._format_row(
        "template_set_failed",
        {"template_set_uuid": "abc", "axis": "sensor_refusal",
         "detail": "sensor refused the read", "sensor_code": 0x28},
        ts=0.0)
    assert row["alert"] is True
    assert "AXIS=sensor_refusal" in row["detail"]
    assert "SENSOR_CODE=40" in row["detail"]  # 0x28 == 40, rendered as int


def test_sensor_code_absent_from_payload_also_renders_a_dash():
    """The contract note says sensor_code may be ABSENT (not present as a
    key at all) on axes that carry none — distinct from device.py's
    TRANSPORT path where it is present-but-None. Both must read as "—"."""
    widget = FingerprintDeviceDiagnosticWidget(
        "v", title="", device_id=DEVICE_ID)
    row = widget._format_row(
        "template_set_failed",
        {"template_set_uuid": "abc", "axis": "capacity",
         "detail": "store is full"},  # no "sensor_code" key at all
        ts=0.0)
    assert "SENSOR_CODE=—" in row["detail"]


# ---------------------------------------------------------------------------
# Addressing: a path-shaped device_id is never mis-split
# ---------------------------------------------------------------------------

def test_path_shaped_device_id_is_matched_whole_not_first_slash_split():
    """[CXC-FPBUS-ADDRESSING] dev-address-shape — DEVICE_ID is
    'dev/s3-fp1'; a naive first-slash split would try to match namespace
    'dev' and misroute every event. Publish on a namespace that WOULD match
    under a wrong split and confirm it is correctly ignored, then publish
    the real one and confirm it lands."""
    widget, ctx, bus, device, hal = _build_wired_widget()

    # a decoy on the wrongly-split prefix must NOT be picked up.
    bus.publish_event("dev/fingerprint_ready", "fingerprint_ready",
                      {"template_set_uuid": "decoy",
                       "count_authentication_attempts": 999})
    assert widget.rows == []

    bus.publish_event(f"{DEVICE_ID}/fingerprint_push_needed",
                      "fingerprint_push_needed", None)
    assert len(widget.rows) == 1
    assert widget.rows[0]["label"] == "HEARTBEAT: PUSH NEEDED"


# ---------------------------------------------------------------------------
# Pixel proof: the alert row actually paints the red wash
# ---------------------------------------------------------------------------

def test_alert_row_paints_the_red_wash_pixel_check():
    widget, ctx, bus, device, hal = _build_wired_widget()
    script_device(device, hal)

    w, h = 1040, 420
    canvas = SkiaCanvas()
    canvas.create_surface(w, h)
    canvas.set_fill_color(0x0D, 0x0D, 0x12)
    canvas.fill_rect(0, 0, w, h)

    size = widget.measure(Constraints(max_width=w, max_height=h))
    rect = Rect(0, 0, size.width, size.height)
    widget.draw(canvas, rect)
    arr = surface_to_array(canvas)

    # Rather than hand-computing the alert band's y offset (brittle against
    # any layout-constant tweak), SCAN a column inside the card for the row
    # with the strongest red dominance — that is the alert wash, which is
    # the only red-dominant fill this widget ever paints (everything else
    # is near-black/charcoal panel or colored TEXT on a thin baseline, not a
    # full-width fill band).
    col = arr[:, 20:40, :3].astype(int)
    redness = col[:, :, 0].mean(axis=1) - np.maximum(
        col[:, :, 1].mean(axis=1), col[:, :, 2].mean(axis=1))
    alert_y = int(np.argmax(redness))
    assert redness[alert_y] > 20, (
        f"expected SOME row with a red-dominant alert wash, "
        f"max redness={redness[alert_y]:.0f} at y={alert_y}")

    mean_r, mean_g, mean_b = col[alert_y].mean(axis=0)
    assert mean_r > mean_g + 20 and mean_r > mean_b + 20, (
        f"expected a red-dominant alert wash at y={alert_y}, "
        f"got mean RGB=({mean_r:.0f},{mean_g:.0f},{mean_b:.0f})")

    # a row well clear of the alert band (three rows below it) must NOT
    # carry that same red wash — otherwise every row would look alerted.
    safe_y = alert_y + 3 * 20
    assert safe_y < arr.shape[0]
    patch2 = arr[safe_y:safe_y + 8, 20:40, :3].astype(int)
    mean_r2, mean_g2, mean_b2 = patch2.mean(axis=(0, 1))
    assert not (mean_r2 > mean_g2 + 20 and mean_r2 > mean_b2 + 20), (
        f"a non-alert row should not carry the alert wash, "
        f"got mean RGB=({mean_r2:.0f},{mean_g2:.0f},{mean_b2:.0f})")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
