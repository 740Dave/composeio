"""A wire `type` the protocol never allocated must be refused AT THE PUBLISHER.

[SPEC:CIO-BUS-PROTOCOL] [BP-TYPE-CODES] — measured incident, 2026-08-15.

WHAT HAPPENED.  Six ComposeIO devices were sent a restart.  Every payload
carried ``"type": "command"``.  `command` is not one of the ten wire types
[BP-TYPE-CODES] allocates, so:

  * `BusMessage.from_json` raised `ValueError: 'command' is not a valid
    MessageType` — a bare enum error naming no remedy;
  * the CAN bridge caught it, logged one line per topic and counted it as
    `encode_error` (9 by 18:42 on the rpi5 census);
  * `cyd/src/bus_mqtt.cpp` `bus_message_type_from_name()` walks the same ten
    entries and returns early, so the FIRMWARE rejected it identically.

So the restart was a NO-OP on every device on every substrate, and the only
signal was six WARNING lines on one host nobody was watching.  Six receivers
each independently discovered the defect and none of them could tell the
publisher.

WHY `command` AND NOT SOMETHING RANDOM.  This is the load-bearing detail.
`state` vs `command` is REAL ComposeBus vocabulary — the prose distinction
between a retained current value and an imperative — and it is used throughout
the specs and the agent definitions.  It was simply never allocated a wire
code.  The publisher reached for the CONCEPT and typed it where the PROTOCOL
VALUE goes.  A guard that only says "not a valid MessageType" invites the
obvious wrong fix (add COMMAND to the enum), which would require a wire-code
allocation across Python, firmware and three substrates to express what `state`
already expresses.  So the refusal has to say WHY the word exists and WHAT to
use instead, or someone re-introduces it.

⛔ The enum is NOT to be extended.  These tests fail if `command` ever becomes
a MessageType.
"""

from __future__ import annotations

import json

import pytest

from composeio import bus_envelope
from composeio.transport import (
    MESSAGE_TYPE_WIRE_CODES,
    BusMessage,
    MessageType,
    UnknownMessageTypeName,
    message_type_for_name,
)


# --------------------------------------------------------------------------- #
# 1. The word itself
# --------------------------------------------------------------------------- #

def test_command_is_not_and_must_never_become_a_message_type():
    """The fix that must NOT be made, pinned as a test."""
    assert "command" not in {t.value for t in MessageType}
    assert len(MESSAGE_TYPE_WIRE_CODES) == 10, (
        "[BP-TYPE-CODES] allocates exactly ten codes; growing this table is a "
        "SPEC change plus a firmware change plus three substrates.")


def test_message_type_for_name_refuses_command_and_names_the_remedy():
    with pytest.raises(UnknownMessageTypeName) as ei:
        message_type_for_name("command")
    msg = str(ei.value)
    # It must name the substitute...
    assert "state" in msg
    # ...explain that the word is real PROSE, not a protocol value, or the
    # next reader "fixes" it by extending the enum...
    assert "prose" in msg.lower()
    # ...and cite the allocation site so the rule is checkable.
    assert "BP-TYPE-CODES" in msg


def test_every_allocated_name_still_resolves():
    """The negative control's positive twin: the guard must not refuse the ten."""
    for t in MessageType:
        assert message_type_for_name(t.value) is t
        assert message_type_for_name(t) is t


def test_an_unrelated_typo_is_refused_too_and_lists_what_is_legal():
    with pytest.raises(UnknownMessageTypeName) as ei:
        message_type_for_name("statee")
    msg = str(ei.value)
    assert "statee" in msg
    assert "heartbeat" in msg, "the refusal should enumerate the legal set"


# --------------------------------------------------------------------------- #
# 2. Receiver side — still a ValueError, so existing handlers keep catching it
# --------------------------------------------------------------------------- #

def test_from_json_refusal_is_still_a_valueerror():
    """The CAN bridge catches `ValueError` around `BusMessage.from_json`.

    Sharpening the message must not change the EXCEPTION CLASS out from under
    it — a bridge that stopped catching this would crash on a malformed
    payload instead of counting it.
    """
    payload = {"ns": "_device/cio-ctrl-db1c/restart", "type": "command",
               "val": 1, "src": "x", "scope": "global"}
    with pytest.raises(ValueError):
        BusMessage.from_json(payload)
    with pytest.raises(UnknownMessageTypeName) as ei:
        BusMessage.from_json(payload)
    # and it must name the NAMESPACE, because the incident's six log lines each
    # named a topic and the operator had to correlate them by hand.
    assert "_device/cio-ctrl-db1c/restart" in str(ei.value)


# --------------------------------------------------------------------------- #
# 3. PUBLISHER side — the point of the whole exercise
# --------------------------------------------------------------------------- #

class _RecordingClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.sent.append((topic, payload, qos, retain))


def test_envelope_refuses_an_unallocated_type_before_anything_is_sent():
    c = _RecordingClient()
    with pytest.raises(UnknownMessageTypeName):
        bus_envelope.publish(c, "_device/cio-ctrl-db1c/restart", 1, "test",
                             mtype="command")
    assert c.sent == [], (
        "REFUSAL MUST WRITE NOTHING. A publisher that emits and then raises "
        "leaves the wire in the state the raise says it avoided.")


def test_a_well_formed_state_publish_is_not_refused():
    """NEGATIVE CONTROL for the guard itself.

    A guard that refuses everything would pass every test above and break the
    bus.  [CXC-VISION-EPISTEMICS]: give every check a negative control.
    """
    c = _RecordingClient()
    bus_envelope.publish(c, "_device/cio-ctrl-db1c/restart", 1, "test")
    assert len(c.sent) == 1
    topic, payload, _, _ = c.sent[0]
    assert topic == "composeio/global/_device/cio-ctrl-db1c/restart"
    assert json.loads(payload)["type"] == "state"


# --------------------------------------------------------------------------- #
# 4. The restart publisher — the thing that did not exist, which is WHY the
#    payload was hand-typed
# --------------------------------------------------------------------------- #

def test_restart_message_is_a_state_with_a_truthy_value():
    from composeio import device_control

    msg = device_control.restart_message("cio-ctrl-db1c", source="test")
    assert msg.type is MessageType.STATE
    assert msg.namespace == "_device/cio-ctrl-db1c/restart"
    # `cyd/src/restart.h` accepts a bare truthy scalar via the `dsl_truthy`
    # canon; 1 is the value every committed recipe already uses.
    assert msg.value == 1
    assert msg.to_json()["type"] == "state"


def test_broadcast_restart_targets_the_broadcast_namespace():
    from composeio import device_control

    msg = device_control.restart_message(None, source="test")
    assert msg.namespace == "_device/restart"


def test_restart_message_cannot_be_built_with_a_non_wire_type():
    """Even the dedicated publisher routes through the one validator."""
    from composeio import device_control

    with pytest.raises(UnknownMessageTypeName):
        device_control.restart_message("cio-ctrl-db1c", source="test",
                                       mtype="command")


def test_a_device_id_with_a_slash_is_refused():
    """`_device/<id>/restart` has exactly three segments.

    An id carrying a slash silently produces a four-segment namespace nobody
    subscribes — which is the same class of failure as the wrong type: a
    publish that looks successful and reaches no one.
    """
    from composeio import device_control

    with pytest.raises(ValueError):
        device_control.restart_message("_device/dual-throttle", source="test")
