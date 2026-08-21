"""FleetStatusMonitor — the FOURTH fingerprint UX surface: a per-device
FLEET / DEVICE-STATUS view (troubleshooting-at-a-glance before game night).

Distinct from the other three fingerprint surfaces (the binding UI in
``binder.py``, the runtime auth-event monitor in ``auth_monitor.py``, and the
enroll station) — do not confuse this with any of them, and this module does
not modify them.

    device --publish_event(<dev>/fingerprint_ready|push_needed|inoperable)-->
    device --publish_event(<dev>/announce)-->                             BUS
                                                                              |
                                                                              v
                                          FleetStatusMonitor (bus.on_message,
                                          filtered on the closed FPBUS
                                          heartbeat + announce vocabulary)
                                                                              |
                                   fp/fleet/rows     (list[dict], one per
                                                       device, device_id-sorted)
                                   fp/fleet/summary  (text, one line/device)
                                                                              v
                                FleetStatusWidget (new) — the color-coded table

WHY ``bus.on_message`` (mirrors ``auth_monitor.py``'s own rationale): the
heartbeats and the announce all ride ``publish_event``
([CXC-FPBUS-REQ-READY-HEARTBEAT], [CXC-FPBUS-MSG-DEVICE-ANNOUNCE]), never
``publish_state`` — they do not fire ``on_bus_state``. Their topics are also
PER-DEVICE (``<dev>/<message>``), which a fleet monitor cannot enumerate
device ids for ahead of time (``subscribe_pattern`` is trailing-glob only, and
a device id may itself contain slashes — see below). ``bus.on_message`` is the
one bus-wide hook that sees every device without knowing device ids in
advance, exactly as ``widgets/bus_monitor.py`` already uses it.

ADDRESSING ([CXC-FPBUS-ADDRESSING] ``dev-address-shape``): a device id MAY be
path-shaped (``dev/s3-fp1``), so recovering ``(device_id, message)`` from a
topic is NEVER a first-slash split — that already produced
DEFECT-FINGERPRINT-READY-HEARTBEAT-NEVER-UPDATES-AD0D95 (``dev/s3-fp1``
parsed to device=``dev``). Every message this contract emits is published as
``f"{device_id}/{message}"`` with ``event_type == message`` (see
``composeio.peripherals.fingerprint.device.FingerprintDevice._publish`` and
``fingerprint_storage.topics.device_announce``), so this monitor recovers the
device id by stripping the trailing ``"/" + event_type`` — a match against the
CLOSED tail vocabulary of this contract, never a split on the first slash.

OFFLINE IS THE FOURTH STATE ([CXC-FPBUS-REQ-READY-HEARTBEAT] rationale):
"Silence is the fourth state and means offline." A device is silent past its
OWN declared cadence (ready=60s, push_needed/inoperable=15s) rather than
judged against one shared timeout — different states legitimately beat at
different rates and a fleet view must not flag a healthy 60s-cadence device
offline at a mark meant for the attention-seeking 15s states.

STALENESS — reuses ``composeio.freshness.HeartbeatReceiver`` rather than
hand-rolling a second staleness mechanism: ``note(device_id, interval_s)`` on
every HEARTBEAT (not on announce — announce is retained/on-change, not a
liveness cadence) records the declared interval that arrived; ``is_alive``
answers "silent past its OWN 3x window" — ``HEARTBEAT_MULTIPLIER = 3.0``, the
SAME constant ``[DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML]`` uses
platform-wide, applied here rather than inventing a second, surface-local
multiplier for this one view. A device the receiver has never heard from is
NOT alive (freshness.py's own rule — absence of evidence is not evidence of
liveness), so a device that only ever announced (no heartbeat yet) reads
OFFLINE, correctly.

This module owns no service, no store, no device — read-only, exactly like
``AuthEventMonitor``.
"""

from __future__ import annotations

import time as _time
from typing import Any, Callable, Dict, List, Optional

from composeio.freshness import HeartbeatReceiver

VIEW_ROWS = "fp/fleet/rows"
VIEW_SUMMARY = "fp/fleet/summary"

# [CXC-FPBUS-REQ-READY-HEARTBEAT] cadence-differs-deliberately: 60s for the
# healthy/configured state, 15s for the two attention-seeking states.
_HEARTBEAT_MESSAGE_INTERVAL: Dict[str, float] = {
    "fingerprint_ready": 60.0,
    "fingerprint_push_needed": 15.0,
    "fingerprint_inoperable": 15.0,
}

# The closed FPBUS tail vocabulary this monitor cares about
# ([CXC-FPBUS-ADDRESSING] dev-address-shape). ``announce`` is retained/
# on-change (not a heartbeat) and is handled separately below.
_HEARTBEAT_STATES = frozenset(_HEARTBEAT_MESSAGE_INTERVAL)
_TAIL_VOCAB = _HEARTBEAT_STATES | {"announce"}

# Short display state for each heartbeat message name.
_STATE_LABEL = {
    "fingerprint_ready": "ready",
    "fingerprint_push_needed": "push_needed",
    "fingerprint_inoperable": "inoperable",
}


def _device_id_from_tail(namespace: str, event_type: str) -> Optional[str]:
    """Recover the device id by matching the CLOSED tail vocabulary — never a
    first-slash split ([CXC-FPBUS-ADDRESSING]). Every message this contract
    emits is ``f"{device_id}/{event_type}"`` by construction (device.py /
    topics.py), so the device id is everything before the trailing
    ``"/" + event_type``."""
    suffix = "/" + event_type
    if not namespace.endswith(suffix):
        return None
    device_id = namespace[: -len(suffix)]
    return device_id or None


class FleetStatusMonitor:
    """Watches EVERY device's heartbeat + announce and republishes STATE for
    the render layer. Owns no service, no store, no device — read-only."""

    def __init__(self, bus: Any, *,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.bus = bus
        self._clock = clock or _time.time
        # HeartbeatReceiver.is_alive uses its OWN HEARTBEAT_MULTIPLIER (3x
        # the declared interval) — the platform's one staleness constant.
        self._heartbeats = HeartbeatReceiver(self._clock)
        # device_id -> mutable record (state/uuid/announce fields/last_seen)
        self.devices: Dict[str, Dict[str, Any]] = {}
        self.bus.ensure_namespace(VIEW_ROWS, engine_config="json")
        self.bus.ensure_namespace(VIEW_SUMMARY, engine_config="json")
        self.bus.on_message(self._on_message)
        self._publish()

    # -- bus wiring ----------------------------------------------------------

    def _on_message(self, msg: Any) -> None:
        event_type = getattr(msg, "event_type", "") or ""
        if event_type not in _TAIL_VOCAB:
            return
        namespace = getattr(msg, "namespace", "") or ""
        device_id = _device_id_from_tail(namespace, event_type)
        if not device_id:
            return
        data = dict(getattr(msg, "data", None) or {})
        rec = self.devices.setdefault(device_id, {"device_id": device_id})

        if event_type in _HEARTBEAT_STATES:
            now = self._clock()
            interval = _HEARTBEAT_MESSAGE_INTERVAL[event_type]
            self._heartbeats.note(device_id, interval)
            rec["_declared_state"] = _STATE_LABEL[event_type]
            rec["last_seen"] = now
            if event_type == "fingerprint_ready":
                rec["template_set_uuid"] = data.get("template_set_uuid")
                rec["count_authentication_attempts"] = data.get(
                    "count_authentication_attempts")
            elif event_type == "fingerprint_inoperable":
                rec["inop_code"] = data.get("inop_code")
                rec["human_readable_inop_string"] = data.get(
                    "human_readable_inop_string")
        else:  # "announce" — retained/on-change, NOT a liveness beat
            rec["gated_capability"] = data.get("gated_capability")
            rec["location"] = data.get("location")
            rec["capabilities"] = dict(data.get("capabilities") or {})

        self._publish()

    # -- offline evaluation ----------------------------------------------------

    def tick(self) -> None:
        """Re-evaluate every known device's liveness against its declared
        heartbeat cadence and republish. Silence past the timeout is the
        FOURTH state ([CXC-FPBUS-REQ-READY-HEARTBEAT]) — this is the seam
        that surfaces it even when no new message has arrived, so it must be
        called periodically. Liveness is read through the ``clock`` injected
        at construction (default ``time.time``); a live deployment calls
        ``tick()`` on a periodic timer, a headless driver/test advances its
        own injected clock and then calls ``tick()`` explicitly."""
        self._publish()

    # -- view formatting + publish ----------------------------------------

    def _row(self, device_id: str) -> Dict[str, Any]:
        rec = self.devices[device_id]
        alive = self._heartbeats.is_alive(device_id)
        age = self._heartbeats.age(device_id)
        state = rec.get("_declared_state") if alive else "offline"
        if state is None:
            # only ever announced, never heartbeat -> silence -> offline
            state = "offline"
        row = {
            "device_id": device_id,
            "state": state,
            "template_set_uuid": rec.get("template_set_uuid"),
            "gated_capability": rec.get("gated_capability"),
            "location": rec.get("location"),
            "capabilities": rec.get("capabilities") or {},
            "last_seen_age_s": age,
        }
        if state == "inoperable":
            row["inop_code"] = rec.get("inop_code")
            row["human_readable_inop_string"] = rec.get(
                "human_readable_inop_string")
        return row

    def rows(self) -> List[Dict[str, Any]]:
        return [self._row(d) for d in sorted(self.devices)]

    def _publish(self) -> None:
        rows = self.rows()
        self.bus.publish_state(VIEW_ROWS, rows, source="fp_fleet_monitor")
        self.bus.publish_state(VIEW_SUMMARY, self._fmt_summary(rows),
                                source="fp_fleet_monitor")

    def _fmt_summary(self, rows: List[Dict[str, Any]]) -> str:
        if not rows:
            return ""
        lines = []
        for r in rows:
            age = r["last_seen_age_s"]
            age_s = f"{age:.0f}s ago" if age is not None else "never"
            uuid = r.get("template_set_uuid")
            uuid_s = f" uuid:{str(uuid)[:8]}…" if uuid else ""
            lines.append(
                f"{r['device_id']:<16} {r['state']:<12} last:{age_s:<10}{uuid_s}")
        return "\n".join(lines)
