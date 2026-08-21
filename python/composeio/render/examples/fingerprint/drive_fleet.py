"""Headless driver + proof for the FLEET / DEVICE-STATUS view (the fourth
fingerprint UX surface — troubleshooting-at-a-glance before game night).

Scripts FOUR real reference devices (``composeio.peripherals.fingerprint.
device.FingerprintDevice`` + ``MockFingerprintHAL`` — the SAME firmware twin
every other fingerprint surface drives, not a hand-built fixture) into the
four distinct states a fleet operator must be able to tell apart at a glance:

  * ``hatch-01``   — REAL push receipt -> READY, holding a template_set_uuid,
                      plus a spec-shaped announce (gated_capability +
                      location + capabilities).
  * ``hatch-02``   — no push ever received -> PUSH_NEEDED.
  * ``hatch-03``   — hardware absent -> INOPERABLE.
  * ``dev/s3-fp1`` — PATH-SHAPED device id ([CXC-FPBUS-ADDRESSING]
                      dev-address-shape) — heartbeats once (PUSH_NEEDED, 15s
                      cadence) then goes SILENT; the virtual clock is
                      advanced past its 3x timeout (45s) and the monitor's
                      ``tick()`` must flip it to OFFLINE with no further
                      message ever arriving.

The NEW ``FleetStatusMonitor`` controller (``fleet_monitor.py``) republishes
the four rows as bus STATE for the ``fleet_status`` widget.

Run:
    SDL_VIDEODRIVER=dummy PYTHONPATH=python:services python3 -m \
        composeio.render.examples.fingerprint.drive_fleet
"""

from __future__ import annotations

import os
import sys

from composeio.bus import ComposeIOBus
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.runtime import RenderApp

# self-register `fleet_status`; also loaded via the scene's widget_modules:
from composeio.render.widgets import fleet_status  # noqa: F401

from composeio.render.examples.fingerprint.fleet_monitor import (
    FleetStatusMonitor, VIEW_ROWS, VIEW_SUMMARY)

from composeio.peripherals.fingerprint.device import FingerprintDevice
from composeio.peripherals.fingerprint.mock import MockFingerprintHAL

_SCENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fingerprint_fleet.compose.yaml")

GATED_CAP = "open_hatch"

DEV_READY = "hatch-01"
DEV_PUSH_NEEDED = "hatch-02"
DEV_INOPERABLE = "hatch-03"
DEV_OFFLINE = "dev/s3-fp1"          # path-shaped — routing proof


class _VirtualClock:
    """A settable clock injected into BOTH the reference devices and the
    monitor, so a driver advances ONE shared notion of time — no real
    sleeping, mirroring ``device.py``'s own ``run_heartbeat_window``."""

    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _announce(bus, device_id: str, hal: MockFingerprintHAL, *,
              gated_capability: str, location: str | None = None) -> None:
    """[CXC-FPBUS-MSG-DEVICE-ANNOUNCE] — the full spec-shaped payload:
    {device_id, gated_capability, location, capabilities{slot_count,
    template_size_bytes, supports_readback}}."""
    caps = hal.capabilities()
    payload = {
        "device_id": device_id,
        "gated_capability": gated_capability,
        "capabilities": {
            "slot_count": caps.slot_count,
            "template_size_bytes": caps.template_size_bytes,
            "supports_readback": caps.supports_readback,
        },
    }
    if location:
        payload["location"] = location
    bus.publish_event(f"{device_id}/announce", "announce", payload,
                       source=device_id)


def build(bus=None, clock: _VirtualClock | None = None):
    """Wire bus + fleet monitor + app, return (app, monitor, bus, clock,
    devices-by-id)."""
    bus = bus or ComposeIOBus()
    clock = clock or _VirtualClock()
    monitor = FleetStatusMonitor(bus, clock=clock)
    comp = parse_compose_yaml(_SCENE)
    app = RenderApp(comp, bus=bus, windowed=False)
    app.start()

    devices: dict[str, FingerprintDevice] = {}
    hals: dict[str, MockFingerprintHAL] = {}
    for device_id in (DEV_READY, DEV_PUSH_NEEDED, DEV_INOPERABLE, DEV_OFFLINE):
        hal = MockFingerprintHAL()
        hals[device_id] = hal
        devices[device_id] = FingerprintDevice(
            hal=hal, bus=bus, device_id=device_id,
            gated_capability=GATED_CAP, clock=clock)

    return app, monitor, bus, clock, devices, hals


def script_fleet(bus, clock, devices, hals):
    """Drive the four devices into their four distinct states, in virtual
    time. Every 15s-cadence device is re-beaten partway through EXCEPT
    ``DEV_OFFLINE`` — otherwise advancing the ONE shared clock far enough to
    silence it would silence the other 15s-cadence devices too, which would
    prove nothing about offline DETECTION specifically."""

    # -- t=0: every device's first heartbeat --------------------------------

    # READY: a REAL push receipt, then the announce, then the ready
    # heartbeat (60s cadence).
    ready_hal = hals[DEV_READY]
    ready_uuid = "11111111-2222-3333-4444-555555555555"
    push = {
        "template_set_uuid": ready_uuid,
        "entries": [{
            "person": "adama", "ordinal": 1,
            "character": "commander", "rbac": "command",
            "template": ready_hal._synth_template("adama-1"),
        }],
        "role_capability_table": {"command": [GATED_CAP]},
    }
    devices[DEV_READY].receive_push(push)
    _announce(bus, DEV_READY, ready_hal, gated_capability=GATED_CAP,
              location="Hangar Bay 3")
    devices[DEV_READY].emit_heartbeat()

    # PUSH_NEEDED: never pushed (15s cadence).
    devices[DEV_PUSH_NEEDED].emit_heartbeat()

    # INOPERABLE: hardware absent (15s cadence).
    devices[DEV_INOPERABLE].set_hardware_present(False)
    devices[DEV_INOPERABLE].emit_heartbeat()

    # the device that goes OFFLINE: one heartbeat (15s cadence), then
    # silence forever — no further call for this device_id, ever.
    devices[DEV_OFFLINE].emit_heartbeat()

    # -- t=20: re-beat every device EXCEPT DEV_OFFLINE ----------------------
    # (hatch-01's 60s cadence does not require a re-beat this soon, but
    # heartbeating it too proves the SAME clock advance that silences one
    # device does not disturb a healthy one.)
    clock.advance(20.0)
    devices[DEV_READY].emit_heartbeat()
    devices[DEV_PUSH_NEEDED].emit_heartbeat()
    devices[DEV_INOPERABLE].emit_heartbeat()

    # -- t=64: DEV_OFFLINE is now 64s silent — past 3x its own 15s cadence
    # (45s) — while the three re-beaten devices are only 44s since THEIR
    # last beat at t=20, i.e. still inside their windows (180s / 45s
    # respectively; 44 < 45 keeps them alive with margin to spare).
    clock.advance(44.0)


def main(out_png="/tmp/fp_fleet_status.png"):
    app, monitor, bus, clock, devices, hals = build()

    script_fleet(bus, clock, devices, hals)
    monitor.tick()                 # force the offline re-evaluation

    app.drain_updates()
    app.render_headless(out_png)

    rows = bus.get_state(VIEW_ROWS) or []
    summary = bus.get_state(VIEW_SUMMARY) or ""
    by_id = {r.get("device_id"): r for r in rows}

    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    check("all four devices present in the fleet rows", len(rows) == 4)

    states_seen = {r.get("state") for r in rows}
    check("all four states present and distinct",
          states_seen == {"ready", "push_needed", "inoperable", "offline"})

    ready_row = by_id.get(DEV_READY)
    check(f"{DEV_READY} is READY with the held template_set_uuid",
          bool(ready_row) and ready_row.get("state") == "ready"
          and ready_row.get("template_set_uuid") == "11111111-2222-3333-4444-555555555555")
    check(f"{DEV_READY} shows announced gated_capability + location",
          bool(ready_row) and ready_row.get("gated_capability") == GATED_CAP
          and ready_row.get("location") == "Hangar Bay 3")

    push_row = by_id.get(DEV_PUSH_NEEDED)
    check(f"{DEV_PUSH_NEEDED} is PUSH_NEEDED",
          bool(push_row) and push_row.get("state") == "push_needed")

    inop_row = by_id.get(DEV_INOPERABLE)
    check(f"{DEV_INOPERABLE} is INOPERABLE",
          bool(inop_row) and inop_row.get("state") == "inoperable")

    off_row = by_id.get(DEV_OFFLINE)
    check(f"{DEV_OFFLINE} (path-shaped id) is OFFLINE via the silence timeout "
          f"(routing did not misparse to device={DEV_OFFLINE.split('/')[0]!r})",
          bool(off_row) and off_row.get("state") == "offline")
    check("the offline device's own age reflects the silent window "
          "(past its 3x=45s timeout)",
          bool(off_row) and off_row.get("last_seen_age_s") is not None
          and off_row["last_seen_age_s"] >= 45.0)
    check("the healthy 15s-cadence devices were NOT swept offline by the "
          "same clock advance",
          bool(push_row) and push_row.get("state") == "push_needed"
          and bool(inop_row) and inop_row.get("state") == "inoperable")

    check("device summary published, all four device ids present",
          bool(summary) and all(d in summary for d in
                                 (DEV_READY, DEV_PUSH_NEEDED, DEV_INOPERABLE, DEV_OFFLINE)))
    check("capture PNG written", os.path.exists(out_png)
          and os.path.getsize(out_png) > 0)

    print(f"\nPNG: {out_png}")
    print(f"fleet rows ({len(rows)}):")
    for r in rows:
        print(f"   {r}")
    print(f"summary:\n{summary}")
    print("\n" + ("ALL GREEN" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fp_fleet_status.png"))
