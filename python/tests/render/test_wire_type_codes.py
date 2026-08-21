"""Message-type wire codes — EVERY type survives EVERY binary substrate.

THE DEFECT THIS PINS (2026-08-10). `BusMessage(type=ACK)` encoded to a UART
frame decoded back as `STATE`. Not a UART bug: the `MessageType -> 4-bit wire
code` table had been COPY-FORKED into four implementations, each holding a
different subset, each ending in `.get(type, 0)`:

    transport_uart.py         8 of 10 types   (no ack, no log_entry)
    transport_can.py          2 of 10 types   (state, event — and only 2 bits
                                               of id field, so the rest could
                                               not have fitted anyway)
    can_mqtt_bridge/bridge.py 5 of 10 types
    firmware .../main.cpp     2 of 10 (C, cannot import — cites the spec)

So the SAME message meant different things on different wires, and the
mismatch was silent: an ack, a heartbeat or a log entry arrived as a spurious
state change, indistinguishable from a real one. `ack`=8 and `log_entry`=9 had
been spec'd for months ([BP-TYPE-ACK], [BP-EXT-LOG]); they reached the
`MessageType` enum and no fork ever got them.

WHY THIS TEST IS PARAMETRIZED ACROSS IMPLEMENTATIONS rather than being one more
UART assertion: the old suite DID exercise UART types (and caught this — in a
tree nobody gates) while CAN was only ever tested with STATE, so CAN's worse
version of the same bug, 8 of 10 types broken, went unseen entirely. A
per-transport test cannot see a forked invariant. Every binary substrate is
registered in SUBSTRATES below; one that drops a type, or that quietly
substitutes a default, fails here by construction — including one written
later, provided its author adds three lines to the registry.

Spec: [BP-TYPE-CODES] in spec/01-composeio-bus-protocol.smd — total round-trip,
no silent fallback, one implementation reused by reference.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "python"))

from composeio.transport import (  # noqa: E402
    MESSAGE_TYPE_WIRE_CODES, WIRE_CODE_MESSAGE_TYPES, WIRE_TYPE_CODE_BITS,
    BusMessage, MessageType, UnmappedMessageType, message_type_for_wire_code,
    wire_code_for_type,
)
from composeio.transport_can import (  # noqa: E402
    decode_arbitration_id, encode_arbitration_id,
)
from composeio.transport_uart import (  # noqa: E402
    _crc8, decode_message, encode_message, register_namespace_hash,
)

NS = "wire_type_probe"


# The CAN/MQTT bridge is a THIRD implementation, outside `python/`, loaded by
# path because `services/` is not an importable package. It is in this suite
# deliberately: it held its own five-entry copy of the table, and a fork that no
# test can reach is a fork that drifts. `_bridge()` is defined below, with the
# other bridge adapters.

# --- substrate registry: (name, encode(type)->wire, decode(wire)->type|None,
#                          force_reserved_code(wire)->wire) ---

def _uart_encode(mtype):
    register_namespace_hash(NS)
    return encode_message(BusMessage(namespace=NS, type=mtype, value=0,
                                     scope="local"))


def _uart_decode(frame):
    decoded = decode_message(frame)
    return None if decoded is None else decoded.type


def _uart_force_reserved(frame):
    """Rewrite the type nibble to a reserved code (15) and re-CRC the frame.

    Hand-built at the bit level because no encoder will emit a reserved code —
    which is precisely the frame a newer peer WILL put on the wire.
    """
    frame = bytearray(frame)
    frame[4] = (0xF << 4) | (frame[4] & 0x0F)
    frame[-1] = _crc8(bytes(frame[1:-1]))
    return bytes(frame)


def _can_encode(mtype):
    return encode_arbitration_id(NS, mtype, "global", 7)


def _can_decode(arb):
    return decode_arbitration_id(arb)[1]


def _can_force_reserved(arb):
    return (arb & ~(0xF << 22)) | (0xF << 22)     # [BP-CAN-ID] v2 type at 25..22


#: ONE bridge module, not one per call. `_bridge()` used to exec the module
#: afresh inside `_bridge_encode` AND inside `_bridge_decode`, so the decode ran
#: against an EMPTY namespace registry that the encode had populated in a
#: different module object — the round trip could not succeed for any type, and
#: all ten cases failed for a reason that had nothing to do with type codes.
#: A test that fails for the wrong reason is a test nobody can read.
_BRIDGE_MOD = None


def _bridge():
    """The CAN/MQTT bridge module, loaded ONCE by path (see above)."""
    global _BRIDGE_MOD
    if _BRIDGE_MOD is None:
        spec = importlib.util.spec_from_file_location(
            "_cmb_bridge", _REPO / "services" / "can_mqtt_bridge" / "bridge.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.register_ns(NS)          # CAN-only namespace: never seen on MQTT
        _BRIDGE_MOD = mod
    return _BRIDGE_MOD


def _bridge_encode(mtype):
    return _bridge().can_id_from_message(
        BusMessage(namespace=NS, type=mtype, value=0, source="probe"))


def _bridge_decode(can_id):
    decoded = _bridge().message_from_can(can_id, b"\x01")
    return None if decoded is None else decoded.type


def _bridge_force_reserved(can_id):
    return (can_id & ~(0xF << 22)) | (0xF << 22)  # [BP-CAN-ID] v2 type at 25..22


SUBSTRATES = [
    ("uart", _uart_encode, _uart_decode, _uart_force_reserved),
    ("can", _can_encode, _can_decode, _can_force_reserved),
    ("can_mqtt_bridge", _bridge_encode, _bridge_decode, _bridge_force_reserved),
]


@pytest.mark.parametrize("mtype", list(MessageType), ids=lambda t: t.name)
@pytest.mark.parametrize("substrate", SUBSTRATES, ids=lambda s: s[0])
def test_every_message_type_round_trips_on_every_binary_substrate(substrate, mtype):
    """[BP-TYPE-CODES] total round-trip: encode(t), decode, get t back.

    RED before the fix: uart failed ACK + LOG_ENTRY; can failed all eight
    non-{STATE,EVENT} types; the bridge failed five. Every one of them decoded
    as STATE.
    """
    name, encode, decode, _ = substrate
    assert decode(encode(mtype)) is mtype, (
        f"{name}: {mtype.name} did not survive the wire — a type that does not "
        f"round-trip is a message the bus silently relabels ([BP-TYPE-CODES])")


@pytest.mark.parametrize("substrate", SUBSTRATES, ids=lambda s: s[0])
def test_a_reserved_code_on_the_wire_is_dropped_not_relabelled(substrate):
    """A frame from a newer peer must be DROPPED, not read as the default type."""
    name, encode, decode, force_reserved = substrate
    wire = force_reserved(encode(MessageType.STATE))
    assert decode(wire) is None, (
        f"{name}: an unallocated type code must drop the frame, never resolve "
        f"to a default — that is how an ack became a state change")


def test_the_table_is_exhaustive_over_MessageType():
    """Adding a MessageType without allocating its wire code IS the bug.

    ACK and LOG_ENTRY were added to the enum and to no wire table. This is the
    guard that would have failed on that commit.
    """
    missing = set(MessageType) - set(MESSAGE_TYPE_WIRE_CODES)
    assert not missing, (
        f"{sorted(t.name for t in missing)} have no wire code. Allocate them in "
        f"[BP-TYPE-CODES] (spec/01-composeio-bus-protocol.smd) AND in "
        f"MESSAGE_TYPE_WIRE_CODES — spec first, then the one table.")


def test_wire_codes_are_unique_and_fit_the_4_bit_field():
    codes = list(MESSAGE_TYPE_WIRE_CODES.values())
    assert len(set(codes)) == len(codes), "two types sharing a code is a collision"
    assert all(0 <= c < (1 << WIRE_TYPE_CODE_BITS) for c in codes), (
        "every allocated code must fit the 4-bit type field of every substrate")
    assert len(WIRE_CODE_MESSAGE_TYPES) == len(MESSAGE_TYPE_WIRE_CODES)


def test_the_allocation_matches_the_spec_table():
    """These numbers are a WIRE CONTRACT with firmware that cannot import them.

    Pinned literally: changing a code is a protocol break for every deployed
    MCU, so it must break this test and force the spec edit first.
    """
    assert {t.value: c for t, c in MESSAGE_TYPE_WIRE_CODES.items()} == {
        "state": 0, "event": 1, "config_push": 2, "config_request": 3,
        "heartbeat": 4, "discovery": 5, "time_sync": 6, "binding": 7,
        "ack": 8, "log_entry": 9,
    }


def test_unallocated_codes_decode_to_None_never_to_a_default_type():
    for code in range(10, 16):
        assert message_type_for_wire_code(code) is None, (
            f"code {code} is unallocated; returning a type invents traffic")


def test_encoding_a_type_with_no_code_RAISES_rather_than_substituting():
    class RogueType:
        """Stands in for a MessageType member added without a code."""

    with pytest.raises(UnmappedMessageType):
        wire_code_for_type(RogueType())


def test_can_carries_all_four_scopes_including_auto():
    """`auto` used to collapse to `global` in the id — a second silent relabel.

    UART round-trips all four ([BP-UART-FRAME]); CAN mapped auto->global on
    encode and had no code 3 on decode, so a default-scope BusMessage arrived
    claiming a resolution the publisher never made.
    """
    for scope in ("local", "global", "remote", "auto"):
        _h, _t, got, _s = decode_arbitration_id(
            encode_arbitration_id(NS, MessageType.STATE, scope, 7))
        assert got == scope, f"CAN dropped scope {scope} -> {got}"


def test_arbitration_id_still_fits_29_bits_with_the_widened_type_field():
    """The 2 extra type bits came out of seq; the id must not grow past 29.

    LOG_ENTRY at node 15 is the WIDEST id this layout can produce: type code 9
    is not the largest field value, but LOG_ENTRY is a tier-7 (bulk) type, so
    it sets all three prio bits — and `auto` sets both scope bits. If 29 bits
    were going to be exceeded anywhere, it would be here.
    """
    arb = encode_arbitration_id(NS, MessageType.LOG_ENTRY, "auto", 15)
    assert arb < (1 << 29), "must fit an extended CAN id"
    _h, mtype, scope, src = decode_arbitration_id(arb)
    assert (mtype, scope, src) == (MessageType.LOG_ENTRY, "auto", 15)
