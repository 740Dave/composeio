"""`unknown` on the wire — tag 0xFD, zero-length payload [BP-VALUE-CODEC].

[DEC-UNKNOWN-IS-A-TYPE-TAG-NOT-A-RESERVED-VALUE]: unknown is a TYPE TAG, never a
reserved value.  ComposeIO values are dynamically typed, so no reserved sentinel
is safe — a reserved float IS NaN, which derived values legitimately produce, and
a reserved int collides with a real reading.

⛔ THE RESIDUAL DAVID FLAGGED, AND IT BITES BOTH WAYS.  0xFE/0xFF are safe
because their discriminator is LENGTH: they are only ever read at 5 bytes, so the
one-byte values 254 and 255 pass through untouched.  0xFD HAS NO SUCH LENGTH.  A
zero-length payload makes the whole encoded value the single byte `fd` — which is
byte-identical to what `encode_value(253)` emits today, and which
`decode_value(b"\\xfd")` returns as integer 253 through the

    if len(data) == 1:
        return data[0]      # Could be bool, int, or enum index

branch.  So the tag is not merely mis-decoded; the codec stops being INJECTIVE:
two distinct values encode to the same byte.  The fix is a one-value carve-out
from the small-int fast path — 253 goes out TAGGED, costing four bytes for
exactly one integer in the whole domain — after which `fd` means unknown and
nothing else.  Both halves are asserted below; testing only the tag would have
shipped the collision.
"""
from __future__ import annotations

import pytest

from composeio.freshness import UNKNOWN
from composeio.transport_uart import (
    VALUE_TAG_FLOAT32, VALUE_TAG_INT32, VALUE_TAG_UNKNOWN,
    decode_value, encode_value,
)


def test_the_unknown_tag_is_0xFD():
    assert VALUE_TAG_UNKNOWN == 0xFD


def test_unknown_encodes_to_the_bare_tag_with_a_zero_length_payload():
    assert encode_value(UNKNOWN) == b"\xfd"


def test_unknown_round_trips():
    assert decode_value(encode_value(UNKNOWN)) is UNKNOWN


def test_unknown_is_distinguishable_from_false_and_from_zero():
    """The whole reason a reserved value was refused."""
    assert encode_value(False) != encode_value(UNKNOWN)
    assert encode_value(0) != encode_value(UNKNOWN)
    assert decode_value(encode_value(False)) is not UNKNOWN
    assert decode_value(encode_value(0)) is not UNKNOWN


def test_none_is_still_the_empty_payload_and_is_NOT_unknown():
    """`None` already meant "no value bytes"; 0xFD means "I know that I do not
    know".  Collapsing them would silently retire a distinction the codec has."""
    assert encode_value(None) == b""
    assert decode_value(b"") is None


# --------------------------------------------------------------------------- #
# THE COLLISION — integer 253
# --------------------------------------------------------------------------- #

def test_integer_253_does_NOT_encode_to_the_unknown_tag():
    """⛔ The carve-out.  Without it, `encode_value(253) == b"\\xfd"` and the
    codec is no longer injective."""
    assert encode_value(253) != b"\xfd"
    assert encode_value(253) == bytes((VALUE_TAG_INT32,)) + b"\xfd\x00\x00\x00"


def test_integer_253_still_round_trips_exactly():
    assert decode_value(encode_value(253)) == 253
    assert isinstance(decode_value(encode_value(253)), int)


def test_the_neighbours_are_untouched():
    """252, 254 and 255 keep the one-byte fast path: 0xFE/0xFF are only read as
    tags at five bytes, so they never collided in the first place and paying the
    four-byte cost for them would be cargo-culting the fix."""
    for n in (0, 1, 127, 252, 254, 255):
        assert encode_value(n) == bytes((n,)), f"{n} lost its fast path"
        assert decode_value(encode_value(n)) == n


def test_the_codec_is_injective_across_every_single_byte_integer():
    """The property the collision broke, asserted as a property."""
    seen: dict[bytes, object] = {}
    for n in range(0, 256):
        seen.setdefault(encode_value(n), n)
    encodings = {encode_value(n) for n in range(256)}
    assert len(encodings) == 256
    assert encode_value(UNKNOWN) not in encodings


def test_every_tag_is_distinct():
    assert len({VALUE_TAG_UNKNOWN, VALUE_TAG_INT32, VALUE_TAG_FLOAT32}) == 3
