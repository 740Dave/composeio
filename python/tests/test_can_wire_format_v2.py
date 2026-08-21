"""[BP-CAN-ID] v2 — the acceptance criteria for workstream F's wire change.

    29-bit extended id:  prio(3) | type(4) | ns_id(16) | src(4) | scope(2)
    bit positions:       28..26  | 25..22  | 21..6     | 5..2   | 1..0

WHY THIS FILE EXISTS SEPARATELY FROM THE ENCODER'S OWN TESTS.

**A machine signal that agrees with you is not proof.** An encoder and its own
decoder agreeing is exactly the self-confirming shape this project has paid for
repeatedly — a `capture.bmp` that rendered perfectly over a byte-swapped panel
because it decoded its own assumption; a dropped-frame counter structurally
pinned at zero; a listen-only sweep that read identically for "wrong bitrate"
and "receive pin not connected".

So the ids below are computed THREE ways and all three must agree:

  1. `_reference_id()` — a SECOND, deliberately dumb implementation written
     from the layout line above. It imports no shift, no mask and no table from
     `transport_can`; it takes the type code and the namespace hash as inputs
     and does nothing but shift. If `transport_can` moves a field, this does
     not follow it.
  2. LITERAL CONSTANTS, arithmetic shown in the comment, computed by hand off
     namespace hashes that an INDEPENDENT witness published:
     `docs/can-binding-teardown-brief.md` §3 lists the CRC-16 of nine live
     namespaces, written before this change existed and by a different author.
     Those literals are the closest thing to an outside oracle available
     without a wire.
  3. The production encoders — `transport_can.encode_arbitration_id` and
     `services/can_mqtt_bridge.can_id_from_message`, which is what the deployed
     bridge and (via the parity corpus) the firmware actually speak.

THE THREE THINGS THIS CHANGE CARRIES, one test class each:

  * PRIORITY — arbitration was a pseudo-random function of the namespace NAME,
    so `throttle/left` won everything by luck and `shields` was the lowest
    priority thing on the bus.
  * SCOPE — silently LOST on every CAN round trip, because the layout had no
    scope field and the decoder reconstructed "global".
  * (the value type tag is `test_value_codec_type_ambiguity.py`, which holds
    the ten cases that were strict-xfail until this landed)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "python"))

from composeio.config import namespace_hash                      # noqa: E402
from composeio.transport import (                                # noqa: E402
    DEFAULT_PRIORITY, PRIORITY_LIVENESS, BusMessage, MessageType,
    clear_declared_priorities, priority_for, register_namespace_priority,
    wire_code_for_type,
)
from composeio.transport_can import (                            # noqa: E402
    CANTransport, _reset_loopbacks_for_tests, arbitration_priority,
    decode_arbitration_id, encode_arbitration_id,
)


# --------------------------------------------------------------------------- #
# 1. THE SECOND IMPLEMENTATION — written from the layout, importing nothing
# --------------------------------------------------------------------------- #

_SCOPE_BITS_BY_NAME = {"local": 0, "global": 1, "remote": 2, "auto": 3}


def _reference_id(prio: int, type_code: int, ns_hash: int, src: int,
                  scope: str) -> int:
    """prio(3)|type(4)|ns_id(16)|src(4)|scope(2), spelled out.

    Deliberately naive and deliberately duplicated. It takes RAW inputs — no
    priority rule, no type table, no hash function — so the only thing it can
    confirm is bit placement, which is the only thing it is here to confirm.
    """
    assert 0 <= prio <= 7 and 0 <= type_code <= 15
    assert 0 <= ns_hash <= 0xFFFF and 0 <= src <= 15
    return (prio * 2**26 + type_code * 2**22 + ns_hash * 2**6
            + src * 2**2 + _SCOPE_BITS_BY_NAME[scope])


def _crc16_ccitt_false(s: str) -> int:
    """CRC-16/CCITT-FALSE, written from the ALGORITHM, not imported.

    Poly 0x1021, init 0xFFFF, no input/output reflection, no final XOR — the
    parameters spec/01-composeio-bus-protocol.smd §1.5.3 [BP-CAN-HASH] states.
    Its whole job is to remove `composeio.config.namespace_hash` from the loop:
    with this here, an id below can be computed end-to-end without a single line
    of production code, which is the only form of "independent" that means
    anything.
    """
    crc = 0xFFFF
    for byte in s.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 \
                else (crc << 1) & 0xFFFF
    return crc


def test_the_hash_function_is_what_the_spec_says_it_is():
    """`namespace_hash` vs a from-the-algorithm CRC-16/CCITT-FALSE."""
    for ns in ("alert_status", "shields", "throttle/left", "_bus/ack",
               "_app/console/bbox", "_device/hosyond-d1afe8/config",
               "dradis/bodies", "", "a", "x" * 300):
        assert namespace_hash(ns) == _crc16_ccitt_false(ns), (
            f"namespace_hash({ns!r}) is not CRC-16/CCITT-FALSE")


def test_an_id_computed_with_no_production_code_at_all():
    """The end of the chain: hash, type code, tier and shifts all local.

    The type codes are transcribed from [BP-TYPE-CODES]'s table in
    `spec/01-composeio-bus-protocol.smd`, the tiers from [BP-WIRE-PRIORITY]'s
    table in the same document, and the shifts from [BP-CAN-ID] v2's diagram.
    Nothing here is imported. If this agrees with `encode_arbitration_id`, the
    agreement is not two copies of one assumption.
    """
    type_codes_from_the_spec = {
        "state": 0, "event": 1, "config_push": 2, "config_request": 3,
        "heartbeat": 4, "discovery": 5, "time_sync": 6, "binding": 7,
        "ack": 8, "log_entry": 9,
    }
    scope_bits_from_the_spec = {"local": 0, "global": 1, "remote": 2, "auto": 3}

    def independent(tier, type_name, ns, node, scope):
        return ((tier << 26)
                | (type_codes_from_the_spec[type_name] << 22)
                | (_crc16_ccitt_false(ns) << 6)
                | (node << 2)
                | scope_bits_from_the_spec[scope])

    # (tier read off the ratified table, type, namespace, node, scope, expected)
    cases = [
        (0, "state", "alert_status", 3, "global",
         MessageType.STATE, 0x0027F10D),
        (7, "config_push", "_device/hosyond-d1afe8/config", 3, "global",
         MessageType.CONFIG_PUSH, 0x1CB1AD8D),
        (1, "state", "throttle/left", 0, "local",
         MessageType.STATE, 0x0400FD80),
        (2, "ack", "_bus/ack", 1, "global", MessageType.ACK, 0x0A3279C5),
        (0, "state", "shields", 1, "global", MessageType.STATE, 0x00331405),
        (6, "state", "_app/console/bbox", 1, "global",
         MessageType.STATE, 0x180567C5),
    ]
    for tier, type_name, ns, node, scope, mtype, literal in cases:
        computed = independent(tier, type_name, ns, node, scope)
        assert computed == literal, (
            f"{ns}/{type_name}: independent 0x{computed:08X}, literal "
            f"0x{literal:08X} — one of the two was typed wrong")
        assert encode_arbitration_id(ns, mtype, scope, node) == literal, (
            f"{ns}/{type_name}: the production encoder disagrees with an id "
            f"computed from the spec with no production code in it")


def test_the_namespace_hashes_match_the_independent_brief():
    """The outside witness, checked before it is relied on.

    `docs/can-binding-teardown-brief.md` §3 lists these CRC-16 values. It was
    written 2026-08-12 by a different author, for a CAN specialist's review,
    before any of this existed. If `namespace_hash` had drifted, every hand
    computation below would be built on sand — so it is checked first, and out
    loud, rather than assumed.
    """
    from_the_brief = {
        "throttle/left": 0x03F6,
        "throttle/right": 0x0D9E,
        "_app/console/bbox": 0x159F,
        "dradis/bodies": 0x2A97,
        "_device/relay-000000/config": 0x6B92,
        "_device/wsc6-6f4ce4/config": 0x8921,
        "alert_status": 0x9FC4,
        "_device/hosyond-d1afe8/config": 0xC6B6,
        "shields": 0xCC50,
    }
    for ns, expected in from_the_brief.items():
        assert namespace_hash(ns) == expected, (
            f"{ns} hashes to {namespace_hash(ns):#06x}, but the teardown brief "
            f"recorded {expected:#06x}")


# --------------------------------------------------------------------------- #
# 2. HAND-COMPUTED LITERALS — the arithmetic is in the comment, not in a call
# --------------------------------------------------------------------------- #

def test_alert_status_state_frame_is_this_exact_number():
    """0x27F10D, computed by hand.

        alert_status is tier 0 (alarm) by the namespace rule  -> prio = 0b000
        STATE is wire code 0                                  -> type = 0b0000
        namespace_hash("alert_status") = 0x9FC4 (from the brief)
        node 3                                                -> src  = 0b0011
        scope global                                          -> scope= 0b01

        0x9FC4 << 6  = 0x9FC4 * 64 = 0x27F100
        src  3 << 2  =                    0x0C
        scope        =                    0x01
                                     -> 0x27F10D
    """
    assert encode_arbitration_id("alert_status", MessageType.STATE,
                                 "global", 3) == 0x27F10D


def test_config_push_frame_is_this_exact_number():
    """0x1CB1AD8D, computed by hand.

        CONFIG_PUSH is tier 7 (bulk) BY TYPE                  -> prio = 0b111
        CONFIG_PUSH is wire code 2                            -> type = 0b0010
        namespace_hash("_device/hosyond-d1afe8/config") = 0xC6B6 (from the brief)
        node 3, scope global

        prio 7 << 26 = 0x1C000000
        type 2 << 22 = 0x00800000
        0xC6B6 << 6  = 0xC6B6 * 64 = 0x0031AD80
        src + scope  =                     0x0D
                                     -> 0x1CB1AD8D
    """
    assert encode_arbitration_id("_device/hosyond-d1afe8/config",
                                 MessageType.CONFIG_PUSH, "global",
                                 3) == 0x1CB1AD8D


def test_throttle_left_frame_is_this_exact_number():
    """0x0400FD81, computed by hand.

        throttle/ is tier 1 (live control) by prefix rule     -> prio = 0b001
        STATE is wire code 0                                  -> type = 0b0000
        namespace_hash("throttle/left") = 0x03F6 (from the brief)
        node 0 (unassigned), scope local                      -> src=0, scope=0

        prio 1 << 26 = 0x04000000
        0x03F6 << 6  = 0x03F6 * 64 = 0x0000FD80
        src 0, scope local (0)
                                     -> 0x0400FD80
    """
    assert encode_arbitration_id("throttle/left", MessageType.STATE,
                                 "local", 0) == 0x0400FD80


@pytest.mark.parametrize(
    "ns,mtype,scope,src",
    [
        ("alert_status", MessageType.STATE, "global", 3),
        ("shields", MessageType.STATE, "auto", 15),
        ("throttle/left", MessageType.STATE, "local", 0),
        ("_app/console/bbox", MessageType.STATE, "remote", 1),
        ("dradis/bodies", MessageType.EVENT, "global", 7),
        ("_bus/ack", MessageType.ACK, "global", 2),
        ("_device/hosyond-d1afe8/config", MessageType.CONFIG_PUSH, "global", 3),
        ("_device/hosyond-d1afe8/config", MessageType.LOG_ENTRY, "auto", 15),
        ("sys/cpu", MessageType.TIME_SYNC, "local", 4),
        ("game/whatever", MessageType.HEARTBEAT, "remote", 9),
    ],
)
def test_production_encoder_agrees_with_the_second_implementation(
        ns, mtype, scope, src):
    """The three-way agreement, across every field-boundary shape."""
    reference = _reference_id(priority_for(ns, mtype), wire_code_for_type(mtype),
                              namespace_hash(ns), src, scope)
    assert encode_arbitration_id(ns, mtype, scope, src) == reference
    assert reference <= 0x1FFFFFFF, "must fit an extended CAN id"


def test_every_field_boundary_is_exercised_by_an_all_ones_id():
    """All ones in every field is still 29 bits, and each field reads back.

    Built through the reference implementation, decoded through the production
    one — so a shift error cannot cancel itself out.
    """
    all_ones = _reference_id(7, 0xF, 0xFFFF, 0xF, "auto")
    assert all_ones == 0x1FFFFFFF
    assert arbitration_priority(all_ones) == 7
    ns_hash, mtype, scope, node = decode_arbitration_id(all_ones)
    assert ns_hash == 0xFFFF
    assert mtype is None, "code 15 is reserved and must never decode to a type"
    assert scope == "auto"
    assert node == 15


# --------------------------------------------------------------------------- #
# 3. PRIORITY — the ordering property, asserted as ordering
# --------------------------------------------------------------------------- #

def _id(ns, mtype, scope="global", src=1):
    return encode_arbitration_id(ns, mtype, scope, src)


def test_an_alarm_state_frame_outranks_a_bulk_config_push():
    """ACCEPTANCE CRITERION 5a. On CAN a numerically LOWER id wins arbitration.

    Under v1 this went the WRONG WAY: `alert_status` hashed to 0x9FC4 and
    `_device/hosyond-d1afe8/config` to 0xC6B6, so the alarm did win — BY LUCK,
    on a hash comparison, and `shields` (0xCC50) lost to almost everything.
    """
    alarm = _id("alert_status", MessageType.STATE)
    bulk = _id("_device/hosyond-d1afe8/config", MessageType.CONFIG_PUSH)
    assert alarm < bulk, (
        f"alarm 0x{alarm:08X} must win against bulk 0x{bulk:08X}")
    assert arbitration_priority(alarm) == 0
    assert arbitration_priority(bulk) == 7


def test_an_ack_outranks_the_bulk_transfer_it_terminates():
    """ACCEPTANCE CRITERION 5b, and the sharpest single v1 defect.

    Type codes were allocated in DECLARATION order, so CONFIG_PUSH is 2 and ACK
    is 8. Under any layout that put type in the high bits, a 977-frame bulk
    transfer outranked the acknowledgements that end it — a transfer starving
    its own terminator. Renumbering the codes was the obvious fix and was
    REJECTED (it breaks UART for a property point-to-point UART cannot use, and
    cannot express within-class priority at all). The prio field fixes it
    without touching a single type code.
    """
    ack = _id("_bus/ack", MessageType.ACK)
    bulk = _id("_device/hosyond-d1afe8/config", MessageType.CONFIG_PUSH)
    assert ack < bulk, f"ack 0x{ack:08X} must win against bulk 0x{bulk:08X}"
    # And the type codes were NOT renumbered — the prohibition, pinned.
    assert wire_code_for_type(MessageType.CONFIG_PUSH) == 2
    assert wire_code_for_type(MessageType.ACK) == 8


def test_shields_is_no_longer_the_lowest_priority_thing_on_the_bus():
    """The teardown brief's headline example, inverted.

    "`shields` (0xCC50) is the lowest-priority thing sampled — a combat-critical
    value loses arbitration to console bounding boxes."
    """
    shields = _id("shields", MessageType.STATE)
    bbox = _id("_app/console/bbox", MessageType.STATE)
    assert shields < bbox
    # Under v1 the comparison ran on the raw hashes and went the other way.
    assert namespace_hash("shields") > namespace_hash("_app/console/bbox")


def test_renaming_a_signal_does_not_re_rank_it():
    """The v1 defect stated as a property rather than as an example.

    `_device/wsc6-6f4ce4/config` (0x8921) outranked
    `_device/hosyond-d1afe8/config` (0xC6B6) — two transfers of identical role
    and urgency, ordered by a MAC suffix.
    """
    a = _id("_device/wsc6-6f4ce4/config", MessageType.CONFIG_PUSH)
    b = _id("_device/hosyond-d1afe8/config", MessageType.CONFIG_PUSH)
    assert arbitration_priority(a) == arbitration_priority(b) == 7
    assert namespace_hash("_device/wsc6-6f4ce4/config") \
        < namespace_hash("_device/hosyond-d1afe8/config"), (
            "the hashes still differ — the point is that the TIER no longer does")
    assert _id("throttle/left", MessageType.STATE) >> 26 \
        == _id("throttle/right", MessageType.STATE) >> 26


def test_the_ratified_tier_table_is_what_got_implemented():
    """Every row of David's 2026-08-13 table, by its stated ASSIGNED-BY column."""
    # by NAMESPACE
    assert priority_for("alert_status", MessageType.STATE) == 0
    assert priority_for("shields", MessageType.STATE) == 0
    assert priority_for("throttle/left", MessageType.STATE) == 1
    assert priority_for("dradis/bodies", MessageType.STATE) == 5
    assert priority_for("_app/console/bbox", MessageType.STATE) == 6
    # by TYPE
    assert priority_for("anything", MessageType.ACK) == 2
    assert priority_for("anything", MessageType.CONFIG_REQUEST) == 2
    assert priority_for("anything", MessageType.TIME_SYNC) == 3
    assert priority_for("anything", MessageType.HEARTBEAT) == 4
    assert priority_for("anything", MessageType.DISCOVERY) == 4
    assert priority_for("anything", MessageType.BINDING) == 4
    assert priority_for("anything", MessageType.CONFIG_PUSH) == 7
    assert priority_for("anything", MessageType.LOG_ENTRY) == 7


def test_the_control_plane_beats_the_namespace_rule():
    """A CONFIG_PUSH of `alert_status` is BULK.

    The urgency belongs to the transfer, not to the name it happens to carry.
    This is the whole content of "control-plane messages take their tier from
    their TYPE" and it is the rule most likely to be ported backwards.
    """
    assert priority_for("alert_status", MessageType.CONFIG_PUSH) == 7
    assert priority_for("throttle/left", MessageType.LOG_ENTRY) == 7
    assert priority_for("alert_status", MessageType.STATE) == 0


def test_an_unlisted_namespace_gets_a_sane_default_not_an_error():
    """A RULE WITH A DEFAULT, not a list. A list is what goes stale.

    And the default must sit BELOW the control plane, so a namespace nobody has
    classified can never starve an ACK, a TIME_SYNC or a HEARTBEAT.
    """
    # `_device/brand-new/status` USED TO BE THE EXAMPLE HERE and is no longer
    # an honest one: `_device/` is now a listed prefix (liveness). Left as a
    # note rather than silently swapped, because the example changing IS the
    # behaviour change and a reader deserves to see which way it went.
    for ns in ("", "a", "totally/unheard/of", "sensors/brand-new/status",
               "shields_ui", "throttle", "alert_status_2"):
        assert priority_for(ns, MessageType.STATE) == DEFAULT_PRIORITY

    assert DEFAULT_PRIORITY > priority_for("x", MessageType.ACK)
    assert DEFAULT_PRIORITY > priority_for("x", MessageType.TIME_SYNC)
    assert DEFAULT_PRIORITY > priority_for("x", MessageType.HEARTBEAT)
    assert DEFAULT_PRIORITY < priority_for("x", MessageType.CONFIG_PUSH)


def test_device_control_plane_is_liveness_even_when_it_rides_as_STATE():
    """`_device/` is the control plane wearing a namespace.

    MESSAGE_TYPE_PRIORITY lifts a TYPED heartbeat to the liveness tier, but the
    C++ fleet publishes liveness as a STATE on `_device/<id>/heartbeat`
    (announce.cpp) — and STATE is deliberately absent from the type table
    because it is application data. So the one message class that says "this
    board still exists" was arbitrating in the TELEMETRY pool, behind every
    gauge reading on the segment. On a loaded CAN bus that is exactly the
    traffic that loses, which makes a healthy board look dead precisely when
    the bus is busy.

    ⚠ THIS IS WIRE-VISIBLE: it changes the priority field of the 29-bit CAN id
    for every `_device/` message. It does NOT change addressing — the namespace
    hash field is untouched — so a mixed fleet still delivers; only arbitration
    order differs between reflashed and un-reflashed nodes.
    """
    assert priority_for("_device/ctrl-01/heartbeat", MessageType.STATE) == \
        PRIORITY_LIVENESS
    assert priority_for("_device/ctrl-01/restart", MessageType.STATE) == \
        PRIORITY_LIVENESS
    # ...and it beats the telemetry it used to sit in.
    assert priority_for("_device/ctrl-01/heartbeat", MessageType.STATE) < \
        priority_for("sensors/temp", MessageType.STATE)
    # A CONFIG_PUSH on a `_device/` namespace is still BULK: the type table
    # wins, because the urgency belongs to the transfer and not to the name it
    # happens to carry.
    assert priority_for("_device/ctrl-01/config", MessageType.CONFIG_PUSH) == 7


def test_exact_rules_do_not_match_by_prefix_and_prefix_rules_do():
    """The matcher, which is where a port goes wrong (and the firmware has one).

    `shields` is an exact rule and must not capture `shields_ui`; `throttle/` is
    a prefix rule and must capture the whole family including one nobody has
    added yet.
    """
    assert priority_for("shields", MessageType.STATE) == 0
    assert priority_for("shields_ui", MessageType.STATE) == DEFAULT_PRIORITY
    assert priority_for("throttle/left", MessageType.STATE) == 1
    assert priority_for("throttle/nobody/added/this/yet",
                        MessageType.STATE) == 1
    assert priority_for("throttle", MessageType.STATE) == DEFAULT_PRIORITY


def test_a_device_can_declare_its_own_tier_and_the_control_plane_still_wins():
    """The workstream-0 seam: a tier travels with the thing it describes."""
    try:
        register_namespace_priority("game/reactor_scram", 0)
        assert priority_for("game/reactor_scram", MessageType.STATE) == 0
        # ...but a declared tier is about APPLICATION DATA. A bulk transfer on
        # that namespace is still bulk.
        assert priority_for("game/reactor_scram", MessageType.CONFIG_PUSH) == 7
        with pytest.raises(ValueError):
            register_namespace_priority("game/nope", 9)
        with pytest.raises(ValueError):
            register_namespace_priority("game/nope", -1)
    finally:
        clear_declared_priorities()
    assert priority_for("game/reactor_scram",
                        MessageType.STATE) == DEFAULT_PRIORITY


def test_priority_never_disturbs_any_other_field():
    """A tier is three bits at the top and must touch nothing below them."""
    for tier in range(8):
        arb = encode_arbitration_id("sys/cpu", MessageType.STATE, "remote", 6,
                                    priority=tier)
        ns_hash, mtype, scope, node = decode_arbitration_id(arb)
        assert (ns_hash, mtype, scope, node) == (
            namespace_hash("sys/cpu"), MessageType.STATE, "remote", 6)
        assert arbitration_priority(arb) == tier


# --------------------------------------------------------------------------- #
# 4. SCOPE — the field that did not survive a round trip at all
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("scope", ["local", "global", "remote", "auto"])
def test_scope_survives_a_can_round_trip_through_the_transport(scope):
    """ACCEPTANCE CRITERION 4. It did NOT survive before this change.

    v1's layout (`ns_hash(16)|type(4)|src(9)`) had no scope field, so
    `message_from_can` reconstructed a default and every `local` message
    arrived claiming to be `global`. That is load-bearing rather than cosmetic:
    an internal CAN trunk between a mothercard and its daughtercards is a
    LOCALITY, and a relay that cannot see scope leaks all local chatter
    upstream.

    Driven through a real (loopback) bus with TWO nodes, so it is a genuine
    frame path — encode, arbitration id, bus, decode — and not a function call
    round trip.
    """
    _reset_loopbacks_for_tests()
    tx = CANTransport(channel="loopback", node_id=4)
    rx = CANTransport(channel="loopback", node_id=9)
    tx.start()
    rx.start()
    got = []
    rx.subscribe("game/order", got.append)
    try:
        tx.send(BusMessage(namespace="game/order", type=MessageType.STATE,
                           value=200, scope=scope))
        assert len(got) == 1, "the frame did not cross the loopback bus"
        assert got[0].scope == scope, (
            f"scope {scope!r} arrived as {got[0].scope!r} — it is being lost "
            f"on the CAN round trip again")
        assert got[0].value == 200
        # `src` is diagnostic and must name the TRANSMITTER, not the receiver.
        assert "node4" in got[0].source
    finally:
        tx.stop()
        rx.stop()
        _reset_loopbacks_for_tests()


@pytest.mark.parametrize("scope", ["local", "global", "remote", "auto"])
def test_scope_survives_a_can_round_trip_through_the_bridge(scope):
    """The same criterion on the DEPLOYED path, which is a different codebase.

    `services/can_mqtt_bridge` hardcoded `scope="global"` on its decode side,
    with a comment saying the layout had nowhere to put it. That comment was
    accurate; it is now false.
    """
    mod = _load_bridge()
    mod.register_ns("game/order")
    msg = BusMessage(namespace="game/order", type=MessageType.STATE, value=7,
                     source="probe", scope=scope)
    decoded = mod.message_from_can(mod.can_id_from_message(msg), b"\x07")
    assert decoded is not None
    assert decoded.scope == scope, (
        f"the bridge lost scope {scope!r} -> {decoded.scope!r}")
    assert decoded.source == mod.CAN_SOURCE, (
        "the echo guard depends on `source == 'can'` matching EXACTLY; a node "
        "id must never be appended to it here")


def test_an_unset_scope_travels_as_auto_not_as_global():
    """`auto` is the bus's word for "the sender did not say".

    Substituting `global` is a claim about locality that nobody made — and on a
    mothercard trunk that claim is precisely what leaks.
    """
    _reset_loopbacks_for_tests()
    tx = CANTransport(channel="loopback")
    rx = CANTransport(channel="loopback")
    tx.start()
    rx.start()
    got = []
    rx.subscribe("game/unset", got.append)
    try:
        msg = BusMessage(namespace="game/unset", type=MessageType.STATE,
                         value=1)
        msg.scope = ""                      # explicitly unset
        tx.send(msg)
        assert got and got[0].scope == "auto"
    finally:
        tx.stop()
        rx.stop()
        _reset_loopbacks_for_tests()


# --------------------------------------------------------------------------- #
# 5. src — an ALLOCATED node id, not a hash slice
# --------------------------------------------------------------------------- #

def test_an_out_of_range_node_id_is_refused_not_masked():
    """`& 0xF` would make node 16 and node 0 the same node.

    Masking is the failure mode that is diagnosable only from a capture, so it
    is refused at every entry point instead.
    """
    with pytest.raises(ValueError):
        CANTransport(channel="loopback", node_id=16)
    with pytest.raises(ValueError):
        CANTransport(channel="loopback", node_id=-1)


def test_an_unallocated_node_is_visibly_unallocated():
    """0 means "nobody gave me an id" and must not look like node 1.

    v1 derived this field from a hash of the source string, so every node
    looked allocated and none of them were.
    """
    _reset_loopbacks_for_tests()
    tx = CANTransport(channel="loopback")            # no node_id given
    rx = CANTransport(channel="loopback")
    tx.start()
    rx.start()
    got = []
    rx.subscribe("game/anon", got.append)
    try:
        assert tx.node_id == 0
        tx.send(BusMessage(namespace="game/anon", type=MessageType.STATE,
                           value=1, scope="global"))
        assert got and got[0].source == "can:loopback", (
            "an unassigned node must NOT be reported as some node number")
    finally:
        tx.stop()
        rx.stop()
        _reset_loopbacks_for_tests()


def test_the_node_id_comes_from_the_environment_when_not_given():
    prev = os.environ.get("COMPOSEIO_CAN_NODE_ID")
    try:
        os.environ["COMPOSEIO_CAN_NODE_ID"] = "11"
        assert CANTransport(channel="loopback").node_id == 11
        # Out of range stays UNASSIGNED rather than being masked into a
        # collision with a real node.
        os.environ["COMPOSEIO_CAN_NODE_ID"] = "16"
        assert CANTransport(channel="loopback").node_id == 0
        os.environ["COMPOSEIO_CAN_NODE_ID"] = "not a number"
        assert CANTransport(channel="loopback").node_id == 0
    finally:
        if prev is None:
            os.environ.pop("COMPOSEIO_CAN_NODE_ID", None)
        else:
            os.environ["COMPOSEIO_CAN_NODE_ID"] = prev


# --------------------------------------------------------------------------- #
# 6. [BP-CAN-ID-DIVERGENCE] — there must be exactly ONE layout in this repo
# --------------------------------------------------------------------------- #

def _load_bridge():
    spec = importlib.util.spec_from_file_location(
        "_cmb_wire_v2", _REPO / "services" / "can_mqtt_bridge" / "bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("mtype", list(MessageType), ids=lambda t: t.name)
@pytest.mark.parametrize("scope", ["local", "global", "remote", "auto"])
def test_the_bridge_and_the_transport_emit_the_same_id(mtype, scope):
    """The reconciliation itself, asserted rather than asserted-in-a-comment.

    Two layouts in one repo that "cannot share a physical wire until
    reconciled" is what [BP-CAN-ID-DIVERGENCE] recorded. This is the test that
    fails if a third one appears.
    """
    mod = _load_bridge()
    ns = "dradis/contacts"
    mod.register_ns(ns)
    msg = BusMessage(namespace=ns, type=mtype, value=1, source="probe",
                     scope=scope)
    assert mod.can_id_from_message(msg) == encode_arbitration_id(
        ns, mtype, scope, mod.CAN_NODE_ID)


def test_no_file_restates_the_v1_shifts():
    """A textual guard, because this defect returns by copy-paste.

    `(ns << 13) | (type << 9)` was the v1 layout. It appeared in four places.
    Tests are allowed to hand-build ids — that is how they stay independent —
    but no PRODUCTION file may.
    """
    import re

    offenders = []
    for path in [_REPO / "python" / "composeio" / "transport_can.py",
                 _REPO / "services" / "can_mqtt_bridge" / "bridge.py",
                 _REPO / "cyd" / "src" / "bus_can.cpp",
                 _REPO / "cyd" / "src" / "bus_can_id.h"]:
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.lstrip()
            # A COMMENT MAY NAME THE RETIRED LAYOUT — describing what was
            # removed, and why, is how the next reader avoids putting it back.
            # `backtick-quoted` code inside prose is the repo's convention for
            # exactly that, so it is exempt too. CODE is not.
            if stripped.startswith(("#", "*", "//", "/*")):
                continue
            code = re.sub(r"`[^`]*`", "", line)
            for pattern in ("<< 13", ">> 13", "<< 9)", ">> 9)"):
                if pattern in code:
                    offenders.append(f"{path.name}:{lineno}: {pattern}")
    assert offenders == [], (
        f"the [BP-CAN-ID] v1 shifts are back in production code: {offenders}")


def test_the_corpus_header_is_current():
    """The generated firmware corpus must match what Python produces NOW.

    A stale corpus is a parity test that passes against last week's layout —
    the exact shape of a machine signal that agrees with you.
    """
    header = (_REPO / "cyd" / "test" / "can_id_corpus_generated.h").read_text()
    assert "prio(3)|type(4)|ns_id(16)|src(4)|scope(2)" in header, (
        "cyd/test/can_id_corpus_generated.h is stale — regenerate with "
        "python3 python/scripts/gen_can_id_corpus_header.py")
    # Spot-check one baked id against the encoder, at the corpus's node id (5).
    expected = encode_arbitration_id("alert_status", MessageType.STATE,
                                     "local", 5)
    assert f"0x{expected:08X}" in header
