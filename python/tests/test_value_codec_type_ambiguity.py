"""The wire value carries a TYPE TAG. It did not, and could not be decoded.

[BP-VALUE-CODEC] / DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9 — found
2026-08-12 while reviewing a reported "missing float branch" in
`services/can_mqtt_bridge`. The report was accurate and the proposed fix (import
the canonical decoder) would have been WRONG: the canon was broken too, in the
complementary direction. Converging them would have moved which values corrupt,
not stopped the corruption.

THE DEFECT WAS IN THE FORMAT, NOT IN EITHER DECODER.

    encode_value(1000)  -> struct.pack("<i", 1000)  -> e8030000   (4 bytes)
    encode_value(42.0)  -> struct.pack("<f", 42.0)  -> 00002842   (4 bytes)

Four bytes, no discriminator. A decoder HAD to guess:

  * `transport_uart.decode_value` guessed FLOAT  -> ints  > 255 came back as
    denormal garbage (1000 -> 1.4e-42) and negatives came back as `nan`.
  * `can_mqtt_bridge.message_from_can` guessed INT -> floats came back as their
    IEEE-754 bit pattern (42.0 -> 1109917696).

Neither was fixable in isolation. The wire had to say which type it carries.

WHY IT SURVIVED SO LONG — it was invisible under exactly the conditions you
would test under. `0` and `0.0` both encode to a single zero byte and decode
correctly under both codecs. Every value in 0..255 takes the one-byte path.
Live throttle traffic is 0..255. A healthy bench bus never exercises the
ambiguous width at all.

CLOSED 2026-08-13, in workstream F's single coordinated wire change. TWO edits,
and it takes both:

  1. A TYPE TAG in the value encoding: `[0xFE][int32]`, `[0xFF][float32]`.
     In-band, not the "one spare bit in the UART type_scope byte" the plan
     costed — that works for UART and cannot work for CAN, whose ratified id
     ([BP-CAN-ID] v2) sums to exactly 29 bits with none spare. Two mechanisms on
     two substrates would be a fork, which is the defect class this codebase has
     already paid for twice. One in-band mechanism also makes
     `decode_value(encode_value(x)) == x` a property of the CODEC ALONE rather
     than of a codec plus its frame — which is what these tests actually assert.
  2. DELETING THE SECOND DECODER. `can_mqtt_bridge` now imports `decode_value`.
     Two decoders cannot disagree if there is one.

These tests were written to FAIL against the old format and were marked
`xfail(strict=True)` so that they would ERROR the moment they started passing
without being deliberately un-marked. They are un-marked now.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from composeio.transport_uart import encode_value, decode_value

_REPO = Path(__file__).resolve().parents[2]


def _bridge_decode_value():
    """The function `can_mqtt_bridge.message_from_can` ACTUALLY calls.

    THE POINT OF THIS INDIRECTION HAS INVERTED. It used to be a verbatim COPY of
    the bridge's inline decoder, duplicated here because importing the service
    drags in paho — and the duplication was itself the evidence: a codec with two
    implementations. Now it RESOLVES the real one, by reading the bridge module's
    source for its import, so this test fails if the bridge ever grows a private
    decoder again. A copy would pass. A resolution cannot.
    """
    src = (_REPO / "services" / "can_mqtt_bridge" / "bridge.py").read_text()
    assert "from composeio.transport_uart import decode_value" in src \
        or "decode_value, encode_value" in src, (
            "services/can_mqtt_bridge no longer imports the canonical "
            "decode_value — the second decoder is back, and with it the "
            "possibility of two readings of one wire value")
    assert "int.from_bytes(data, \"little\")" not in src, (
        "services/can_mqtt_bridge has an inline int-guessing decoder again")
    return decode_value


bridge_decode = _bridge_decode_value()


# Values that survive today, under BOTH codecs. These pass now and must keep
# passing — they are the regression floor, and they are also the reason the
# defect is invisible.
#
# `""` is deliberately NOT here: it encodes to a single 0x00 and decodes to
# int 0 under both codecs. See test_one_byte_width_is_ambiguous_too.
UNAMBIGUOUS = [0, 1, 200, 255, "ahead flank", True, False]

# Values whose type cannot be recovered from the wire.
AMBIGUOUS_INTS = [256, 1000, 70000, -1, -5, -70000]
AMBIGUOUS_FLOATS = [42.0, 3.14, -1.5, 1e6]


@pytest.mark.parametrize("value", UNAMBIGUOUS)
def test_unambiguous_values_round_trip_under_both_codecs(value):
    """The regression floor: what works today must not break when this is fixed."""
    wire = encode_value(value)
    canon = decode_value(wire)
    bridge = bridge_decode(wire)
    # bool encodes to 0x01/0x00 and decodes to int 1/0, so this is VALUE
    # equality, not type equality — `True == 1` is the point, not a slip.
    assert canon == value, f"canon lost {value!r}"
    assert bridge == value, f"bridge lost {value!r}"


def test_one_byte_width_is_ambiguous_too():
    """The 4-byte width is the expensive case, not the only one.

    An empty string encodes to a single 0x00 — indistinguishable from int 0,
    bool False, or an enum index of 0. Both decoders read it as `0`, so `""`
    cannot survive a round trip on any binary substrate.

    Recorded because a fix aimed only at int32-vs-float32 would leave this
    standing, and because it shows the gap is a missing type tag in general
    rather than one bad branch.
    """
    wire = encode_value("")
    assert wire == b"\x00"
    assert decode_value(wire) == 0
    assert bridge_decode(wire) == 0
    assert decode_value(encode_value(0)) == 0        # same bytes, same reading
    assert encode_value("") == encode_value(0) == encode_value(False)


# The `xfail(strict=True)` markers that stood here until 2026-08-13 are GONE,
# deliberately and not by accident: strict xfail ERRORS when a test starts
# passing, which is exactly the alarm that says "the wire changed under you,
# come and decide whether that was the fix you meant". It was. These ten cases
# are the acceptance criteria for [BP-VALUE-CODEC] and they are now the
# regression guard for it.

@pytest.mark.parametrize("value", AMBIGUOUS_INTS)
def test_canon_round_trips_integers(value):
    """Was xfail: the canon guessed float, so 1000 -> 1.4e-42 and -5 -> nan."""
    assert decode_value(encode_value(value)) == value
    assert isinstance(decode_value(encode_value(value)), int), (
        "an int must come back an INT, not a float that happens to compare "
        "equal — the tag carries the type, not just the magnitude")


@pytest.mark.parametrize("value", AMBIGUOUS_FLOATS)
def test_bridge_round_trips_floats(value):
    """Was xfail: the bridge guessed int, so 42.0 -> 1109917696."""
    assert bridge_decode(encode_value(value)) == pytest.approx(value)
    assert isinstance(bridge_decode(encode_value(value)), float)


def test_the_two_decoders_agree_on_every_byte_string():
    """The harm, inverted: one wire value, ONE reading.

    This test asserted the OPPOSITE until 2026-08-13 — that every ambiguous
    value was read differently by the two decoders — because that was the fact
    then and it is what made this a WIRE defect rather than a bug in either
    module. It is kept, inverted rather than deleted, so the record shows what
    changed. The numbers the field report named are spot-checked below so a
    future reader sees actual values and not just a count.
    """
    for value in AMBIGUOUS_INTS + AMBIGUOUS_FLOATS:
        wire = encode_value(value)
        canon, bridge = decode_value(wire), bridge_decode(wire)
        assert canon == bridge, (
            f"{value!r} encodes to {wire.hex()} and is still read two ways: "
            f"canon {canon!r}, bridge {bridge!r}")

    assert decode_value(encode_value(42.0)) == 42.0
    assert bridge_decode(encode_value(42.0)) == 42.0        # was 1109917696
    assert bridge_decode(encode_value(1000)) == 1000
    assert decode_value(encode_value(1000)) == 1000         # was 1.4e-42


def test_negative_integers_survive_now():
    """The sharpest single fact, inverted.

    -5 used to decode as `nan` under the canon and as 4294967291 under the
    bridge. Neither decoder was a safe convergence target, and a reviewer who
    read only one test was meant to read this one: the fix was NOT to pick a
    winner. It was a tag.
    """
    wire = encode_value(-5)
    assert wire == b"\xfe\xfb\xff\xff\xff", (
        "an int32 must go on the wire as [0xFE][little-endian two's "
        f"complement]; got {wire.hex()}")
    assert decode_value(wire) == -5
    assert bridge_decode(wire) == -5


def test_an_untagged_four_byte_value_is_refused_not_guessed():
    """A frame from a peer that has not been reflashed must be VISIBLY undecodable.

    This is the flag day, pinned. The old wire put a bare 4-byte float on the
    line; reading it as a float now would keep the guess alive for exactly the
    traffic the guess corrupts. Bare bytes come back as bytes — this codec's
    existing "I will not guess" return, and `BusValueKind::Raw` on the firmware.
    """
    bare = b"\x00\x00\xaf\x42"           # 87.5f under the OLD encoding
    assert decode_value(bare) == bare, (
        "an untagged 4-byte value must come back as raw bytes, not as a "
        "guessed float32")


def test_the_tag_bytes_cannot_collide_with_a_string_length_prefix():
    """Why 0xFE/0xFF are safe tag values, asserted rather than asserted-in-prose.

    A string encodes as [len][utf8] and `encode_value` truncates at 200 bytes,
    so no string this encoder emits can lead with 0xFE or 0xFF.
    """
    longest = encode_value("x" * 500)
    assert longest[0] == 200 and len(longest) == 201
    assert longest[0] < 0xFE
    # And a 5-byte string still decodes as a string, not as a tagged number.
    assert decode_value(encode_value("abcd")) == "abcd"
