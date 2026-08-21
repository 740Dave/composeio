"""AuthEventMonitor — the runtime auth-event MONITOR controller (the
"cylon detector" / GM view).

Distinct from ``binder.py`` (the BINDING UI's controller, setup-time): this
one is RUNTIME — it does not command anything, it only WATCHES.

    device --publish_event(<device_id>/auth_event)--> BUS
                                                          |
                                                          v
                                    AuthEventMonitor (bus.on_message,
                                    filtered event_type == "auth_event")
                                                          |
                       fp/monitor/feed     (list[dict], most-recent-first)
                       fp/monitor/summary  (text, per-device last outcome)
                                                          v
                AuthEventFeedWidget (new) + InfoListWidget (reused, unchanged)

Why ``bus.on_message`` and not ``comp.subscriptions``/``on_bus_state``:
``auth_event`` rides ``publish_event`` (a bus EVENT), never ``publish_state``
([CXC-FPBUS-MSG-AUTH-EVENT]) — it does not go through
``_notify_pattern_subs`` and never fires ``on_bus_state``. Its topic is also
PER-DEVICE (``<device_id>/auth_event``), which a privileged monitor cannot
enumerate ahead of time (``subscribe_pattern`` cannot glob a MIDDLE segment
either — trailing-glob only). ``bus.on_message`` is the one bus-wide hook
that sees every device without knowing device ids in advance — the exact
reason ``widgets/bus_monitor.py`` (BusMonitorWidget) already uses it for
its own all-messages log.

This module deliberately keeps the glue OFF the widget (unlike
BusMonitorWidget, which self-subscribes in ``on_mount``): the feed needs
STRUCTURED per-outcome data (for the color-coded rows), and republishing as
bus STATE is the same shape ``FingerprintCastingBinder`` already uses to feed
``info_list`` — one glue idiom for both example apps in this directory.
"""

from __future__ import annotations

from typing import Any, Dict, List

VIEW_FEED = "fp/monitor/feed"
VIEW_SUMMARY = "fp/monitor/summary"

MAX_FEED = 50


class AuthEventMonitor:
    """Watches EVERY device's auth_event and republishes STATE for the
    render layer. Owns no service, no store, no device — read-only."""

    def __init__(self, bus: Any, *, max_feed: int = MAX_FEED) -> None:
        self.bus = bus
        self.max_feed = int(max_feed)
        self.events: List[Dict[str, Any]] = []          # most-recent-first
        self.device_last: Dict[str, Dict[str, Any]] = {}  # device_id -> last event
        self.bus.ensure_namespace(VIEW_FEED, engine_config="json")
        self.bus.ensure_namespace(VIEW_SUMMARY, engine_config="json")
        self.bus.on_message(self._on_message)
        self._publish()

    # -- bus wiring ----------------------------------------------------------

    def _on_message(self, msg: Any) -> None:
        if getattr(msg, "event_type", "") != "auth_event":
            return
        data = dict(getattr(msg, "data", None) or {})
        if not data:
            return
        self.events.insert(0, data)
        del self.events[self.max_feed:]
        device_id = str(data.get("device_id", ""))
        if device_id:
            self.device_last[device_id] = data
        self._publish()

    def _publish(self) -> None:
        self.bus.publish_state(VIEW_FEED, list(self.events), source="fp_auth_monitor")
        self.bus.publish_state(VIEW_SUMMARY, self._fmt_summary(), source="fp_auth_monitor")

    # -- view formatting -------------------------------------------------------

    def _fmt_summary(self) -> str:
        if not self.device_last:
            return ""
        rows = []
        for device_id, ev in sorted(self.device_last.items()):
            outcome = str(ev.get("outcome", "?"))
            who = ""
            if "player" in ev or "character" in ev:
                who = f"  {ev.get('player', '?')} -> {ev.get('character', '?')}"
            rows.append(f"{device_id:<14} last: {outcome:<12}{who}")
        return "\n".join(rows)
