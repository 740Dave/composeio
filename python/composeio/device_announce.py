"""device_announce — the ORACLE for device self-announcement.

[SPEC:CXC-BOARD-HW] — workstream 0.2/0.5 of
``docs/PLAN-fleet-baseline-and-bus-native.md``.

A ComposeBus that cannot be polled for what is on it is a bus you have to visit
with a screwdriver.  A 25-second census of ``composeio/#`` on the production
broker on 2026-08-12 found ZERO ``_device/*`` namespaces: nothing announced
itself, nothing said what firmware it ran, and nothing distinguished "offline"
from "idle".  This module is the host half of the fix — the decoder for what
the firmware publishes, and the resolver that turns a 32-bit build fingerprint
back into ``"hosyond_s3 Aug 13 2026 21:41:02"``.

THE NAMESPACES
--------------
==============================  ========================================
``_device/<id>/heartbeat``      uptime seconds; PRESENCE is the liveness
``_device/<id>/build``          32-bit fingerprint (8 hex digits)
``_device/<id>/transport``      packed u8: substrate + default-or-NVS
``_device/<id>/scene``          16-bit scene content hash (4 hex digits)
``_device/<id>/health``         packed u32: heap KB | reset | link | flags
==============================  ========================================

Poll with ``_device/broadcast/announce`` (whole fleet) or
``_device/<id>/announce`` (one device).

ANNOUNCEMENT IS PERIODIC, NOT ON-CHANGE
---------------------------------------
A MISSING publication is itself the fault report.  A query cannot provide that:
silence in response to a query is ambiguous between "the device is absent" and
"the device did not hear me".  So a consumer decides a device is gone by
counting MISSED heartbeats, never by asking.

THIS FILE IS ONE HALF OF A TWO-IMPLEMENTATION TABLE, AND THAT IS DELIBERATE
---------------------------------------------------------------------------
The firmware cannot import Python, so ``cyd/src/announce_pack.h`` is a second
implementation of everything below.  Two copies of a table is EXACTLY the shape
that produced the [BP-TYPE-CODES] defect — three forks of the message-type map,
each ending in ``.get(t, 0)``, so an ACK arrived on the wire as a STATE.  Drift
is therefore a FAILING TEST rather than a wrong number in a fleet report:
``python/scripts/gen_device_announce_header.py`` bakes THIS module into
``cyd/test/device_announce_generated.h`` and ``pio run -e native_announce``
compares the two field-for-field.

WHY 32-BIT WORDS ARRIVE AS HEX STRINGS
--------------------------------------
The framed substrates (UART, CAN) have no u32 encoding — ``bus_frame.h`` offers
1-byte, int32, float32 and a length-prefixed string, and int32/float32 are
INDISTINGUISHABLE on the wire (``DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9``,
deliberately unfixed; the fix is a wire bit landing in workstream F).  float32's
24-bit mantissa cannot hold a CRC-32, and MQTT's ``%g`` formatting was reducing
integers to six significant digits.  So a 32-bit word rides as its 8 lowercase
hex digits: exact on MQTT and UART, readable in a log, comparable by a machine.

The cost, stated plainly: 9 bytes on a framed substrate, so it does NOT fit a
classic CAN frame, and the CAN transport REFUSES it loudly rather than
truncating.  A truncated fingerprint would decode cleanly as a DIFFERENT build,
which is the worst possible failure for the one field a reflash is checked
against.  ``decode_word`` below therefore accepts BOTH forms, so the day
workstream F flips the firmware to a real integer, tooling keeps working.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- #
# Namespaces
# --------------------------------------------------------------------------- #

#: Leaf name -> what it carries.  Order is the order a fleet report prints.
ANNOUNCE_FIELDS = ("heartbeat", "build", "transport", "scene", "health")

#: The whole-fleet poll.  One message; every device answers.
POLL_BROADCAST = "_device/broadcast/announce"


def poll_namespace(device_id: str) -> str:
    """The single-device poll — re-read one board without waking the fleet."""
    return f"_device/{device_id}/announce"


def announce_namespace(device_id: str, field_name: str) -> str:
    if field_name not in ANNOUNCE_FIELDS:
        raise ValueError(f"unknown announcement field {field_name!r}")
    return f"_device/{device_id}/{field_name}"


def parse_namespace(ns: str) -> tuple[str, str] | None:
    """``_device/<id>/<leaf>`` -> ``(id, leaf)``, or None if it is not one.

    Returns None for the poll namespaces and for the other ``_device/`` control
    namespaces (``restart``, ``config``, ``config_source``, ``status``) — a
    fleet report must not invent a field out of a control message.
    """
    parts = ns.split("/")
    if len(parts) != 3 or parts[0] != "_device":
        return None
    if parts[2] not in ANNOUNCE_FIELDS:
        return None
    return parts[1], parts[2]


# --------------------------------------------------------------------------- #
# transport (u8)
# --------------------------------------------------------------------------- #

#: bit 7 — the selection came from NVS rather than the compiled default.
#:
#: Not decoration.  "This board is on UART" and "somebody told this board to be
#: on UART and it remembered" are different facts, and the second is what a
#: reflash campaign must reconcile: a device whose NVS says ``can`` after being
#: flashed with an ``mqtt`` default is not running what you think it is.
TRANSPORT_NVS_BIT = 0x80
TRANSPORT_CODE_MASK = 0x0F

#: 0 is UNKNOWN, never "mqtt".  A MisconfiguredTransport carries nothing on any
#: substrate; reporting it as the default would be a device lying about its own
#: configuration — the defect ``bus_transport_select.cpp``'s rule 1 prevents.
TRANSPORT_CODES = {
    0: "unknown",
    1: "mqtt",
    2: "uart",
    3: "can",
}
TRANSPORT_CODE_BY_NAME = {v: k for k, v in TRANSPORT_CODES.items()}


def pack_transport(substrate_code: int, from_nvs: bool) -> int:
    v = substrate_code & TRANSPORT_CODE_MASK
    return v | TRANSPORT_NVS_BIT if from_nvs else v


def unpack_transport(packed: int) -> tuple[str, bool]:
    """-> (substrate name, came-from-NVS)."""
    return (TRANSPORT_CODES.get(packed & TRANSPORT_CODE_MASK, "unknown"),
            bool(packed & TRANSPORT_NVS_BIT))


# --------------------------------------------------------------------------- #
# link health
# --------------------------------------------------------------------------- #

#: UNKNOWN IS 0 AND IS NOT "HEALTHY".
#:
#: This is the whole lesson of [SPEC:CXC-BOARD-HW] H2: a bus-off ESP32 reported
#: CONNECTED forever because ``connected()`` returned ``s_started``.  Ask of any
#: instrument "what would it show if the thing were broken?" — the same.  It was
#: not an instrument.  A substrate with no error model (a UART port being open
#: says nothing about the far end) answers UNKNOWN, a fleet report prints a
#: dash, and nobody reads a dash as a clean bill of health.
LINK_HEALTH = {
    0: "unknown",
    1: "ok",
    2: "warning",
    3: "degraded",
    4: "down",
    5: "stopped",
    6: "recovering",
}

# --------------------------------------------------------------------------- #
# health (u32)
# --------------------------------------------------------------------------- #

HEALTH_HEAP_SHIFT = 16
HEALTH_RESET_SHIFT = 12
HEALTH_LINK_SHIFT = 8

#: ``esp_reset_reason_t`` verbatim.  ESP-IDF allocates 0..15, which is exactly
#: the four bits reserved for it.  It is in the PERIODIC announcement because a
#: board that panics and reboots every 40 s presents, from the bus, as a board
#: with a small uptime — indistinguishable from one somebody just power-cycled.
RESET_REASONS = {
    0: "unknown",
    1: "poweron",
    2: "external",
    3: "sw",
    4: "panic",
    5: "int_wdt",
    6: "task_wdt",
    7: "wdt",
    8: "deepsleep",
    9: "brownout",
    10: "sdio",
    11: "usb",
    12: "jtag",
    13: "efuse",
    14: "pwr_glitch",
    15: "cpu_lockup",
}

#: bit -> short name.  0x40/0x80 are RESERVED; do not reuse without
#: regenerating the C++ header.  A flag that means two things is worse than a
#: missing flag.
HEALTH_FLAGS = {
    0x01: "scene",        # a scene is built and rendering
    0x02: "stale",        # rendering the CACHED scene, of unknown age
    0x04: "connected",    # what BusTransport::connected() CLAIMS
    0x08: "net",          # the radio came up (so /capture.bmp exists)
    0x10: "linkerr",      # substrate reports non-zero error counters
    0x20: "listen",       # receive-only substrate — a WORKAROUND, not a design
}


def pack_health(free_heap_bytes: int, reset_reason: int, link_health: int,
                flags: int) -> int:
    kb = min(free_heap_bytes // 1024, 0xFFFF)
    rr = min(reset_reason, 15)
    lh = min(link_health, 15)
    return ((kb << HEALTH_HEAP_SHIFT) | (rr << HEALTH_RESET_SHIFT)
            | (lh << HEALTH_LINK_SHIFT) | (flags & 0xFF))


@dataclass(frozen=True)
class Health:
    heap_kb: int
    reset_reason: int
    link: int
    flags: int

    @property
    def reset_reason_name(self) -> str:
        return RESET_REASONS.get(self.reset_reason, "?")

    @property
    def link_name(self) -> str:
        return LINK_HEALTH.get(self.link, "?")

    @property
    def flag_names(self) -> list[str]:
        return [n for bit, n in sorted(HEALTH_FLAGS.items())
                if self.flags & bit]

    @property
    def claims_connected(self) -> bool:
        return bool(self.flags & 0x04)

    @property
    def contradiction(self) -> str | None:
        """The H2 defect, said out loud.

        ``connected()`` returns ``s_started`` on the CAN transport, so a
        BUS-OFF node reports CONNECTED forever.  That is a recorded,
        deliberately-unfixed defect (H2b) — but with both facts on the bus the
        fleet report can at least NAME it instead of showing a green light over
        a deaf and mute board.
        """
        if self.claims_connected and self.link_name in ("down", "stopped"):
            return (f"transport claims connected but the link is "
                    f"{self.link_name}")
        return None


def unpack_health(v: int) -> Health:
    return Health(
        heap_kb=(v >> HEALTH_HEAP_SHIFT) & 0xFFFF,
        reset_reason=(v >> HEALTH_RESET_SHIFT) & 0x0F,
        link=(v >> HEALTH_LINK_SHIFT) & 0x0F,
        flags=v & 0xFF,
    )


# --------------------------------------------------------------------------- #
# Value decoding
# --------------------------------------------------------------------------- #

def decode_word(val, *, width: int, allow_float: bool = False) -> int | None:
    """Decode an announcement WORD from whatever the wire delivered.

    Accepts BOTH forms on purpose:

      * a hex STRING (``"12813962"``) — what the firmware publishes today, for
        the reasons in this module's docstring;
      * a plain INTEGER — what it will publish once workstream F puts a value
        type tag on the wire and a real u32 fits again.

    Returns None (never 0, never a guess) for anything else.  A build
    fingerprint that quietly decodes to 0 would make every device look like it
    is running the same firmware, which is precisely the question this field
    exists to answer.

    ⚠ ``allow_float`` IS THE HONEST LINE BETWEEN A QUANTITY AND A WORD, and it
    defaults to False.

    A float32 with a 24-bit mantissa mangles a 32-bit word into a value that is
    still an *integer* — so nothing about the number itself reveals the loss.
    There is no way to detect it after the fact; the only defence is to refuse
    the TYPE on the namespaces where it can never be legitimate.  ``build``,
    ``scene`` and ``health`` are opaque bit patterns: a float arriving on one
    of them means the value went through a float encoder somewhere and its low
    bits are gone, so it is refused.

    ``heartbeat`` is different in kind — it is a COUNT OF SECONDS, and a
    float32 carries it exactly up to 2**24 (194 days) which is the whole useful
    range of an uptime.  So that one call site passes ``allow_float=True``,
    deliberately and in one place.
    """
    if isinstance(val, bool):
        return None
    if isinstance(val, int):
        return val & ((1 << width) - 1)
    if isinstance(val, float):
        if not allow_float or not val.is_integer():
            return None
        return int(val) & ((1 << width) - 1)
    if isinstance(val, str):
        s = val.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        if not s or len(s) > width // 4:
            return None
        try:
            return int(s, 16)
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# Build fingerprint <-> build string
# --------------------------------------------------------------------------- #

def build_fingerprint(stamp: str) -> int:
    """CRC-32 of ``"<env> <date> <time>"`` — the same value the build bakes in.

    Kept here as well as in ``cyd/tools/gen_build_stamp.py`` so a fingerprint
    seen on the bus can be checked against a candidate string without running
    the build system.
    """
    return zlib.crc32(stamp.encode("utf-8")) & 0xFFFFFFFF


def _default_map_path() -> Path:
    """``<repo>/cyd/build_fingerprints.json``.

    Resolved from THIS FILE rather than the cwd — a fleet report run from a
    device directory must still resolve fingerprints.
    """
    return Path(__file__).resolve().parents[2] / "cyd" / "build_fingerprints.json"


def load_fingerprint_map(path: Path | str | None = None) -> dict[int, dict]:
    """fingerprint -> ``{env, stamp, generated_utc}``.

    Missing or unreadable map -> ``{}``, never an exception: an unresolvable
    fingerprint is a DEGRADED report ("this board runs 12813962, and I cannot
    name it"), not a failed one.  The fingerprint itself is still the thing a
    reflash is checked against.
    """
    p = Path(path) if path else _default_map_path()
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[int, dict] = {}
    for k, v in (raw or {}).items():
        try:
            out[int(k, 16)] = v
        except (TypeError, ValueError):
            continue
    return out


def describe_build(fp: int | None, fp_map: dict[int, dict] | None = None) -> str:
    if fp is None:
        return "?"
    entry = (fp_map or {}).get(fp)
    if not entry:
        return f"{fp:08x} (UNKNOWN — not in build_fingerprints.json)"
    return f"{fp:08x} {entry.get('stamp', '?')}"


# --------------------------------------------------------------------------- #
# One device's collected announcement
# --------------------------------------------------------------------------- #

@dataclass
class DeviceReport:
    """Everything one device said during a poll window.

    Every field is Optional and defaults to None — NOT to a plausible value.
    "This board did not publish its scene hash" and "this board is running
    scene 0000" are different facts and a report that conflates them is lying
    about the one thing it exists to tell you.
    """

    device_id: str
    uptime_s: int | None = None
    build: int | None = None
    transport: int | None = None
    scene: int | None = None
    health: int | None = None
    #: leaf -> monotonic timestamps, so the report can state the OBSERVED
    #: cadence rather than the configured one.
    arrivals: dict[str, list[float]] = field(default_factory=dict)

    def note(self, leaf: str, when: float) -> None:
        self.arrivals.setdefault(leaf, []).append(when)

    @property
    def heartbeats(self) -> int:
        return len(self.arrivals.get("heartbeat", []))

    @property
    def heartbeat_interval_s(self) -> float | None:
        """Mean OBSERVED interval, or None with fewer than two samples.

        Measured from arrivals rather than assumed from
        ``kAnnHeartbeatIntervalMs``: the configured cadence is what the
        firmware intends, and the whole reason this mechanism exists is that
        intent and reality had been diverging unobserved.
        """
        t = self.arrivals.get("heartbeat", [])
        if len(t) < 2:
            return None
        return (t[-1] - t[0]) / (len(t) - 1)

    @property
    def health_decoded(self) -> Health | None:
        return unpack_health(self.health) if self.health is not None else None

    def apply(self, leaf: str, value, when: float) -> None:
        self.note(leaf, when)
        if leaf == "heartbeat":
            # allow_float: uptime is a COUNT OF SECONDS, and a float32 carries
            # it exactly to 2**24 (194 days).  The other four fields are opaque
            # WORDS and refuse floats — see decode_word.
            v = decode_word(value, width=32, allow_float=True)
            if v is not None:
                self.uptime_s = v
        elif leaf == "build":
            self.build = decode_word(value, width=32)
        elif leaf == "transport":
            self.transport = decode_word(value, width=8)
        elif leaf == "scene":
            self.scene = decode_word(value, width=16)
        elif leaf == "health":
            self.health = decode_word(value, width=32)


def format_uptime(seconds: int | None) -> str:
    if seconds is None:
        return "?"
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d{h:02d}h{m:02d}m"
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"
