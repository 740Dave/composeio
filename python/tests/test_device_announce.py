"""device_announce — the decode rules that a fleet report's honesty rests on.

[SPEC:CXC-BOARD-HW] workstream 0.2.

The PACKING arithmetic is checked against the firmware by
``pio run -e native_announce`` (247 checks over a generated corpus).  This file
checks the things that are Python-only and that decide whether a report LIES:

  * a value that cannot be decoded exactly must become ``None``, never 0 and
    never a plausible guess;
  * the announcement namespaces must be told apart from the OTHER ``_device/``
    control namespaces, so a restart command never becomes a fleet field;
  * ``link=0`` must stay "unknown" — it is the value every substrate with no
    error model reports, and calling it healthy rebuilds defect H2 in the
    reporting layer;
  * the transport CLAIMS-connected / link-DOWN contradiction must be surfaced,
    because that pairing is the only way the bus can show H2b at all.
"""

from __future__ import annotations

import json
import zlib
from pathlib import Path

import pytest

from composeio import device_announce as da


# --------------------------------------------------------------------------- #
# Namespace parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("leaf", list(da.ANNOUNCE_FIELDS))
def test_announcement_namespaces_round_trip(leaf: str) -> None:
    ns = da.announce_namespace("hosyond-d1afe8", leaf)
    assert ns == f"_device/hosyond-d1afe8/{leaf}"
    assert da.parse_namespace(ns) == ("hosyond-d1afe8", leaf)


@pytest.mark.parametrize("ns", [
    # The OTHER `_device/` namespaces. Every one is a real, shipping control
    # namespace, and a fleet report that turned one into a field would invent
    # a device state out of a command.
    "_device/hosyond-d1afe8/restart",
    "_device/hosyond-d1afe8/status",
    "_device/hosyond-d1afe8/config",
    "_device/hosyond-d1afe8/config_source",
    "_device/hosyond-d1afe8/announce",
    "_device/restart",
    "_device/broadcast/announce",
    "_device/x/twai/tx_error_counter",
    "throttle/left",
])
def test_non_announcement_namespaces_are_refused(ns: str) -> None:
    assert da.parse_namespace(ns) is None


# --------------------------------------------------------------------------- #
# decode_word — the rule that keeps a report from guessing
# --------------------------------------------------------------------------- #

def test_hex_string_is_the_wire_form_today() -> None:
    assert da.decode_word("12813962", width=32) == 0x12813962
    assert da.decode_word("6f62", width=16) == 0x6F62
    assert da.decode_word("0x12813962", width=32) == 0x12813962


def test_plain_integer_is_accepted_for_when_workstream_f_lands() -> None:
    """A real u32 on the wire must decode too.

    The hex-string form is a workaround for the framed substrates having no
    int32/float32 discriminator.  When workstream F puts a type tag on the wire
    and the firmware flips to a real integer, the tooling must keep working
    without a flag day.
    """
    assert da.decode_word(0x12813962, width=32) == 0x12813962
    assert da.decode_word(42, width=8) == 42


def test_a_lossy_float_is_refused_rather_than_rounded() -> None:
    """This is the check that matters most.

    If a 32-bit word ever goes through a float32 encoder, the low bits are
    GONE.  Rounding the result would report a plausible, WRONG build
    fingerprint — and "is this board running the build I just flashed?" is the
    entire question the field exists to answer.
    """
    # A float32-mangled u32 is STILL AN INTEGER, so nothing about the number
    # reveals the loss.  The only defence is to refuse the TYPE on the
    # namespaces where a float can never be legitimate.
    assert da.decode_word(1.5, width=32) is None
    assert da.decode_word(3.7320508e9, width=32) is None
    assert da.decode_word(4096.0, width=32) is None

    # ...except uptime, which is a COUNT and is exact in float32 to 194 days.
    # One call site, opted in explicitly.
    assert da.decode_word(4096.0, width=32, allow_float=True) == 4096
    assert da.decode_word(1.5, width=32, allow_float=True) is None


def test_garbage_is_none_never_zero() -> None:
    for bad in ("", "zz", "not-hex", None, [], {}, True, False,
                "123456789"):     # too wide for 32 bits
        assert da.decode_word(bad, width=32) is None, bad


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["mqtt", "uart", "can"])
@pytest.mark.parametrize("nvs", [False, True])
def test_transport_round_trip(name: str, nvs: bool) -> None:
    packed = da.pack_transport(da.TRANSPORT_CODE_BY_NAME[name], nvs)
    assert da.unpack_transport(packed) == (name, nvs)


def test_transport_zero_is_unknown_not_mqtt() -> None:
    """A MisconfiguredTransport carries nothing on any substrate.

    Reporting it as the compiled default would be a device lying about its own
    configuration — rule 1 of bus_transport_select.cpp.
    """
    assert da.unpack_transport(0) == ("unknown", False)


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #

def test_health_round_trip() -> None:
    v = da.pack_health(50 * 1024, 4, 3, 0x01 | 0x10)
    h = da.unpack_health(v)
    assert h.heap_kb == 50
    assert h.reset_reason_name == "panic"
    assert h.link_name == "degraded"
    assert h.flag_names == ["scene", "linkerr"]


def test_heap_saturates_rather_than_wrapping() -> None:
    """A wrapped heap field would report a STARVING board as a roomy one.

    That is the direction of error nobody investigates, so the field saturates
    instead.
    """
    assert da.unpack_health(da.pack_health(0xFFFFFFFF, 0, 0, 0)).heap_kb == 0xFFFF


def test_link_zero_is_unknown_and_the_report_prints_a_dash() -> None:
    """kAnnLinkUnknown is what BusTransport::link_health()'s default returns.

    Every substrate with no error model takes it.  If 0 ever meant "ok", they
    would all start reporting a clean bill of health they have no basis for —
    which is defect H2 exactly.
    """
    assert da.LINK_HEALTH[0] == "unknown"
    h = da.unpack_health(da.pack_health(1024, 1, 0, 0))
    assert h.link_name == "unknown"


def test_the_h2_contradiction_is_named() -> None:
    connected_but_down = da.pack_health(1024, 1, 4, 0x04)
    h = da.unpack_health(connected_but_down)
    assert h.claims_connected
    assert h.contradiction and "connected" in h.contradiction

    healthy = da.unpack_health(da.pack_health(1024, 1, 1, 0x04))
    assert healthy.contradiction is None


# --------------------------------------------------------------------------- #
# fingerprint resolution
# --------------------------------------------------------------------------- #

def test_fingerprint_matches_the_build_systems_crc() -> None:
    stamp = "hosyond_s3 Aug 13 2026 21:41:02"
    assert da.build_fingerprint(stamp) == zlib.crc32(stamp.encode()) & 0xFFFFFFFF


def test_committed_map_resolves_and_is_self_consistent() -> None:
    """Every entry in cyd/build_fingerprints.json must hash to its own key.

    An entry whose key does not match its stamp would make a fleet report name
    a build that never existed — and since the map is append-only, it would do
    so forever.
    """
    path = Path(__file__).resolve().parents[2] / "cyd" / "build_fingerprints.json"
    if not path.exists():
        pytest.skip("no build fingerprints recorded yet")
    raw = json.loads(path.read_text())
    for key, entry in raw.items():
        assert da.build_fingerprint(entry["stamp"]) == int(key, 16), (
            f"{key} does not hash from {entry['stamp']!r}")


def test_unknown_fingerprint_says_so_rather_than_inventing_a_name() -> None:
    assert "UNKNOWN" in da.describe_build(0xDEADBEEF, {})
    assert da.describe_build(None, {}) == "?"


def test_missing_map_degrades_rather_than_raising() -> None:
    """An unresolvable fingerprint is a DEGRADED report, not a failed one.

    The fingerprint itself is still the thing a reflash is checked against.
    """
    assert da.load_fingerprint_map(Path("/nonexistent/nope.json")) == {}
