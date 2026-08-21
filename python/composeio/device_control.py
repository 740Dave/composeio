"""device_control — RESTART A DEVICE OVER THE BUS, correctly, from a tool.

[SPEC:CXC-EMBED-REACTIVE] [CXC-ER-REMOTE-RESTART] [CXC-ER-RESTART-BUS]
[SPEC:CIO-BUS-PROTOCOL] [BP-TYPE-CODES] — DP-CIO-6.

WHY THIS MODULE EXISTS, STATED AS THE MEASUREMENT IT CAME FROM
--------------------------------------------------------------
On 2026-08-15 six ComposeIO devices were sent a restart.  Every payload carried
``"type": "command"``, which [BP-TYPE-CODES] does not allocate.  Every receiver
refused it — `BusMessage.from_json` on the Python tier, and
`bus_message_type_from_name()` in `cyd/src/bus_mqtt.cpp`, which walks the same
ten-entry `kBusWireCodes` table and returns early on the firmware tier.  The
restart was a NO-OP on every device on every substrate.

A SEARCH OF THE REPOSITORY FOUND NO PUBLISHER TO FIX.  Not one committed site
frames a restart; every hand-built envelope in the tree already says
``"type": "state"``, and both documented recipes
(`compose_projects/office_devices/README.md`, `docs/board-bringup/hosyond-s3.md`)
are correct.  The defect was not a wrong line of code — IT WAS THE ABSENCE OF A
LINE OF CODE.  The only way to restart a device was to hand-type a JSON
envelope into `mosquitto_pub`, and a hand-typed envelope is where the prose
word `command` gets typed into the slot the protocol value goes.

So the remedy is a publisher, not a patch.  This module is it.

WHAT THE FIRMWARE ACCEPTS (`cyd/src/restart.h`, the `dsl_truthy` canon)
-----------------------------------------------------------------------
A bare truthy scalar (``true`` / ``1`` / ``"restart"``), or the canonical
object ``{"restart": true}``.  We send ``1``: it is what both committed recipes
use, it is one byte on a framed substrate, and it is unambiguous under every
value codec.

⛔ A BOOT-GUARD WINDOW EXISTS AND IS NOT A BUG.  `restart_init(..., boot_window_ms)`
ignores any restart arriving in the first ~3 s after boot, so a retained or
doubled command cannot loop a device.  A restart sent to a device that JUST
booted is therefore correctly ignored — which is indistinguishable, from the
publisher, from a restart that never arrived.  `confirm_restarts()` below reads
UPTIME rather than assuming.

IT NAMES NO SUBSTRATE
---------------------
Everything here builds `BusMessage` objects and hands them to a `Transport`
the caller supplies or `transport_registry.build` constructs from a spec.  A
restart is an ordinary bus message and works identically on MQTT, UART and CAN
(DP-CIO-6) — which is the whole reason `_device/<id>/restart` is namespaced and
not an HTTP endpoint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from composeio import device_announce as da
from composeio.transport import (BusMessage, MessageType, Transport,
                                 message_type_for_name)

#: The per-device and broadcast restart control namespaces, computed the same
#: way `restart.h` computes them so the two tiers cannot disagree about the
#: string.
BROADCAST_RESTART_NS = "_device/restart"

#: The firmware's pre-restart acknowledgement — `_device/<id>/status` =
#: "restarting", published immediately before `esp_restart()`.
RESTART_ACK_VALUE = "restarting"

#: The truthy scalar this tool sends.  See the module docstring.
RESTART_VALUE = 1

#: How long to watch for evidence of an actual reboot, by default.  Long enough
#: for WiFi + config fetch on the slowest board observed (hosyond ~6 s to first
#: heartbeat) plus a 5 s heartbeat period.
DEFAULT_CONFIRM_S = 20.0


def restart_namespace(device_id: str | None) -> str:
    """``_device/<id>/restart``, or the broadcast namespace for ``None``.

    ⛔ REFUSES AN ID CONTAINING A SLASH.  `_device/<id>/restart` has exactly
    three segments; an id like ``_device/dual-throttle`` produces a
    four-segment namespace that no device subscribes and no error reports —
    the same class of failure as the wrong wire type, a publish that looks
    successful and reaches no one.  Measured on this bench: the throttle
    controller was addressed as `dual-throttle` while the device answering on
    the bus is `cio-ctrl-db1c`.
    """
    if device_id is None:
        return BROADCAST_RESTART_NS
    device_id = device_id.strip()
    if not device_id:
        raise ValueError("device id is empty — pass None for the broadcast "
                         "restart, explicitly, rather than an empty string")
    if "/" in device_id:
        raise ValueError(
            f"device id {device_id!r} contains a '/'. `_device/<id>/restart` "
            f"has exactly three segments; this would publish to a namespace "
            f"nothing subscribes and nothing would report the miss. Pass the "
            f"DEVICE ID that `composeio fleet` shows (e.g. 'cio-ctrl-db1c'), "
            f"not a namespace fragment.")
    return f"_device/{device_id}/restart"


def restart_message(device_id: str | None, *, source: str,
                    mtype: "str | MessageType" = MessageType.STATE,
                    scope: str = "global") -> BusMessage:
    """The ONE restart message builder.

    `mtype` is a parameter only so that it is routed through
    `message_type_for_name` — i.e. so that a caller who reaches for the word
    `command` is refused HERE, at the publisher, with an explanation, instead
    of at six receivers with none.  There is no legitimate reason to pass it.
    """
    return BusMessage(
        namespace=restart_namespace(device_id),
        type=message_type_for_name(mtype),
        value=RESTART_VALUE,
        source=source,
        scope=scope,
    )


# --------------------------------------------------------------------------- #
# Confirmation — an instrument that can report FAILURE
# --------------------------------------------------------------------------- #

@dataclass
class RestartEvidence:
    """What was OBSERVED for one device, never what was assumed.

    ⛔ `sent` IS NOT EVIDENCE OF ANYTHING.  A publish that crossed a broker
    proves the broker accepted bytes.  [CXC-VISION-EPISTEMICS]: ask what a
    success message actually exercised.  The two facts that mean something are
    below, and either may be absent.
    """
    device_id: str
    sent: bool = False
    #: The firmware's pre-restart ack, `_device/<id>/status` == "restarting".
    #: Proves the command was PARSED AND ACCEPTED — not that the board came
    #: back.
    ack_seen: bool = False
    #: Uptime samples (seconds) in arrival order.  A DECREASE is the reboot:
    #: the board's own counter reset.  This is the closest bus-visible analogue
    #: of a serial boot banner and it is the one an operator can read without a
    #: cable.
    uptimes: list[float] = field(default_factory=list)
    #: Uptime observed BEFORE the restart was published, if any.
    uptime_before: float | None = None

    @property
    def uptime_reset(self) -> bool:
        """True iff an uptime sample came back LOWER than an earlier one."""
        baseline = self.uptime_before
        for u in self.uptimes:
            if baseline is not None and u < baseline:
                return True
            if baseline is None or u > baseline:
                baseline = u
        return False

    @property
    def rebooted(self) -> bool:
        return self.uptime_reset

    def verdict(self) -> str:
        if self.uptime_reset:
            return "REBOOTED (uptime counter reset — the device's own evidence)"
        if self.ack_seen:
            return ("ACKED but no uptime reset observed within the window — the "
                    "command was parsed and accepted; the board coming back is "
                    "NOT proven here")
        if self.sent:
            return ("NO EVIDENCE — published, but neither the pre-restart ack "
                    "nor an uptime reset was seen. The device may be on another "
                    "substrate, silent, or inside its ~3 s boot-guard window")
        return "NOT SENT"


def _uptime_from(msg: BusMessage) -> float | None:
    """Seconds of uptime carried by a `_device/<id>/heartbeat`, or None.

    `device_announce.decode_word` owns the encoding; this only asks it.
    """
    return da.decode_word(msg.value, width=32, allow_float=True)


def restart_devices(device_ids: list[str | None], *, source: str,
                    transport: Transport,
                    confirm_s: float = DEFAULT_CONFIRM_S,
                    baseline_s: float = 6.0,
                    scope: str = "global",
                    gap_s: float = 0.4) -> dict[str, RestartEvidence]:
    """Publish a restart to each device and WATCH FOR EVIDENCE that it landed.

    The transport must be started by the caller — this module neither builds
    nor names one ([CXC-SOB-THESIS]).

    Sequence, and every step of it is load-bearing:

      1. listen `baseline_s` for `_device/*` so a PRE-restart uptime exists.
        Without a baseline an uptime of 4 s proves nothing: it could be a board
        that rebooted or a board that has been up 4 s since something else.
      2. publish one restart per device, `gap_s` apart, so a shared segment is
        not asked to carry six control frames in one burst.
      3. listen `confirm_s` for the ack and for uptime samples.

    Returns evidence per device id.  A device that answers NOTHING gets an
    entry too — an absent row would read as "not asked", and the difference
    between "asked and silent" and "never asked" is the whole report.
    """
    evidence: dict[str, RestartEvidence] = {
        (d or "broadcast"): RestartEvidence(device_id=(d or "broadcast"))
        for d in device_ids
    }
    baseline: dict[str, float] = {}
    phase = {"live": False}

    def on_message(msg: BusMessage) -> None:
        parts = msg.namespace.split("/")
        if len(parts) != 3 or parts[0] != "_device":
            return
        dev, leaf = parts[1], parts[2]
        if leaf == "heartbeat":
            up = _uptime_from(msg)
            if up is None:
                return
            if not phase["live"]:
                baseline[dev] = up
            elif dev in evidence:
                evidence[dev].uptimes.append(up)
        elif leaf == "status" and dev in evidence:
            if isinstance(msg.value, str) and msg.value == RESTART_ACK_VALUE:
                evidence[dev].ack_seen = True

    transport.subscribe_pattern("_device/*")
    transport.subscribe_all(on_message)

    time.sleep(baseline_s)
    for dev, ev in evidence.items():
        ev.uptime_before = baseline.get(dev)
    phase["live"] = True

    for target in device_ids:
        key = target or "broadcast"
        transport.send(restart_message(target, source=source, scope=scope))
        evidence[key].sent = True
        time.sleep(gap_s)

    time.sleep(confirm_s)
    return evidence


def format_evidence(evidence: dict[str, RestartEvidence]) -> str:
    lines = [f"{'DEVICE':<20} {'SENT':<5} {'ACK':<5} {'UPTIME BEFORE/AFTER':<24} VERDICT"]
    for key in sorted(evidence):
        ev = evidence[key]
        before = "—" if ev.uptime_before is None else f"{ev.uptime_before:.0f}s"
        after = "—" if not ev.uptimes else f"{min(ev.uptimes):.0f}s"
        lines.append(
            f"{ev.device_id:<20} {'yes' if ev.sent else 'no':<5} "
            f"{'yes' if ev.ack_seen else 'no':<5} {before + ' -> ' + after:<24} "
            f"{ev.verdict()}")
    return "\n".join(lines)
