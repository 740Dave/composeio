"""The config-transfer ACK — the message that could not be sent, and the
check that was never made.

[SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-CHUNKS], workstream D of
docs/PLAN-fleet-baseline-and-bus-native.md.

THREE DEFECTS, ONE FILE, and they compound:

  D1  The ACK was `snprintf("ok %04x %u", ...)`. For the proven 5,856 B scene
      that is `"ok beef 5856"` — THIRTEEN bytes. A classic CAN frame carries
      EIGHT and `cyd/src/bus_can.cpp` refuses anything longer, so on CAN the
      ACK was PHYSICALLY UNSENDABLE. The leaf verified its scene, cleared its
      retry timer and rendered; the head learned nothing; and since there is no
      ACK timeout, nothing detected the silence. `ConfigServer.acks_ok` and
      `acks_bad` were STRUCTURALLY INCAPABLE of incrementing over CAN — the
      same class of non-instrument as the bridge's `filtered` counter pinned at
      zero by where it sat.

  D2  The head tested `text.startswith("ok")` and NEVER compared the returned
      hash against the one it sent. So "ok" plus the WRONG hash was counted a
      success — and that one is wrong on UART TODAY, where the string fit.

  D4  `BusMessage.to_json()` put raw `bytes` in `val`, so `json.dumps` raised a
      bare `TypeError` at whichever call site happened to serialise it, naming
      neither the message nor the reason.

WHAT THESE TESTS PROVE AND WHAT THEY DO NOT. They run the REAL codecs and the
REAL CANTransport frame path (loopback backend), so the byte counts and the
head's verdicts are the shipping ones. They prove NOTHING about a wire: no
transceiver, no arbitration, no bit timing. D5 — CAN host -> relay -> UART leaf
-> rendered scene — remains BLOCKED on working CAN hardware and is the only
thing that can close this out.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "python", _REPO / "services"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from composeio.config_chunk import (          # noqa: E402
    ACK_BYTES, ACK_HASH_MISMATCH, ACK_INCOMPLETE, ACK_NO_MEMORY, ACK_OK,
    CAN_CONTENT_BYTES, ConfigAckError, Reassembler, ack_ok, chunk_config,
    content_hash, decode_ack, encode_ack,
)
from composeio.transport import (             # noqa: E402
    BusMessage, MessageType, UnserialisableValue,
)
from composeio.transport_can import (         # noqa: E402
    CANTransport, _reset_loopbacks_for_tests, loopback_bus,
)
from composeio.transport_uart import decode_value, encode_value  # noqa: E402
from uart_mqtt_bridge.config_server import (  # noqa: E402
    BulkTransportRefused, ConfigServer, chunk_payload_for_transport,
    config_namespace,
)

#: The transfer the whole workstream is about: 5,856 bytes, hash 0xBEEF in the
#: original log line, 25 frames on UART and 977 on CAN.
SCENE_LEN = 5856


@pytest.fixture(autouse=True)
def _clean_loopbacks():
    _reset_loopbacks_for_tests()
    yield
    _reset_loopbacks_for_tests()


# --------------------------------------------------------------------------- #
# D1 — the ACK fits a classic CAN frame                                        #
# --------------------------------------------------------------------------- #

def test_the_string_ack_this_replaced_could_not_be_sent():
    """The defect, stated as an assertion so it cannot come back.

    Both retired forms are over the 8-byte limit `bus_can.cpp` enforces.
    """
    # +1 for the length byte the firmware's string encoder prepends — that is
    # the value as it reaches the transport, and it is what gets refused.
    assert 1 + len("ok %04x %u" % (0xBEEF, SCENE_LEN)) == 13 > 8
    assert 1 + len("mismatch %04x want %04x" % (0xBEEF, 0xCC50)) == 24 > 8


def test_the_string_ack_is_actually_DROPPED_by_the_can_transport(caplog):
    """Arithmetic is not evidence — make the transport refuse it, on the record.

    This is the defect reproduced through the shipping code path: the old
    firmware's 13-byte value reaches `CANTransport.send`, which drops it rather
    than truncating. Nothing downstream ever hears about it, which is why
    `acks_ok` could not increment.
    """
    import logging

    bus = loopback_bus("ack-drop")
    leaf = CANTransport(channel="ack-drop", backend="loopback", node_id=2)
    leaf.start()
    old_value = encode_value("ok %04x %u" % (0xBEEF, SCENE_LEN))   # 13 bytes
    assert len(old_value) == 13
    with caplog.at_level(logging.WARNING):
        leaf.send(BusMessage(namespace=config_namespace("hosyond-d1afe8"),
                             type=MessageType.ACK, value=old_value,
                             scope="global"))
    assert bus.sent == [], "the 13-byte ACK should have been DROPPED"
    assert "DROPPED, not truncated" in caplog.text


def test_ack_is_seven_bytes_with_one_spare_in_a_classic_frame():
    v = ack_ok(0xBEEF, SCENE_LEN, 976)
    assert len(v) == ACK_BYTES == 7
    assert len(v) < 8, "a classic CAN frame carries 8 data bytes"


@pytest.mark.parametrize("status", [ACK_OK, ACK_HASH_MISMATCH, ACK_INCOMPLETE,
                                    ACK_NO_MEMORY])
def test_every_status_round_trips_and_only_ok_is_ok(status):
    got = decode_ack(encode_ack(status, 0xBEEF, SCENE_LEN, 976))
    assert got.status == status
    assert got.hash == 0xBEEF and got.total_len == SCENE_LEN
    assert got.ackseq == 976
    assert got.ok is (status == ACK_OK)


@pytest.mark.parametrize("status", [ACK_OK, ACK_HASH_MISMATCH, ACK_INCOMPLETE,
                                    ACK_NO_MEMORY])
def test_no_status_is_mistakable_for_a_string_by_the_value_codec(status):
    """Why the tag nibble exists.

    `decode_value` reads a leading byte of N in an (N+1)-byte value as a
    length-prefixed string, so a BARE status byte of 6 in a 7-byte ACK would
    come back as a `str` — and the head would then be comparing text again. The
    0xA0 tag puts the first byte permanently above any length this codec emits.
    """
    v = encode_ack(status, 0x3031, 0x3233, 0x3435)   # ASCII-ish body on purpose
    assert isinstance(decode_value(v), (bytes, bytearray))


def test_the_ack_survives_the_real_can_frame_path_and_fits():
    """END TO END through the shipping codec — [BP-CAN-ID] v2 + the value codec.

    Not a hand-built frame: the value goes through `CANTransport.send`, which
    encodes it, assigns the v2 arbitration id and hands 7 bytes to the bus. If
    it did not fit, `send` would DROP it and `sent` would be empty — which is
    exactly what the string ACK did.
    """
    bus = loopback_bus("ack-fit")
    head = CANTransport(channel="ack-fit", backend="loopback", node_id=1)
    leaf = CANTransport(channel="ack-fit", backend="loopback", node_id=2)
    head.start()
    leaf.start()
    ns = config_namespace("hosyond-d1afe8")
    seen: list[BusMessage] = []
    head.subscribe(ns, seen.append)

    leaf.send(BusMessage(namespace=ns, type=MessageType.ACK,
                         value=ack_ok(0xBEEF, SCENE_LEN, 976), scope="global"))

    assert bus.sent, "the ACK was DROPPED by the transport — it did not fit"
    arb, data = bus.sent[-1]
    assert len(data) == 7 <= 8
    assert len(seen) == 1
    got = decode_ack(seen[0].value)
    assert (got.ok, got.hash, got.total_len, got.ackseq) == (
        True, 0xBEEF, SCENE_LEN, 976)


def test_a_whole_can_transfer_including_its_ack_fits_in_classic_frames():
    """Every frame of a real transfer — header, data, ACK — is <= 8 bytes.

    The transfer is short (60 chunks, not 977) because frame COUNT is not what
    is under test; frame SIZE is, and one ragged tail is where an off-by-one
    would live.
    """
    payload = bytes((i * 31 + 5) & 0xFF for i in range(359))
    bus = loopback_bus("ack-transfer")
    head = CANTransport(channel="ack-transfer", backend="loopback", node_id=1)
    head.start()
    ns = config_namespace("leaf-1")
    for value in chunk_config(payload, CAN_CONTENT_BYTES):
        head.send(BusMessage(namespace=ns, type=MessageType.CONFIG_PUSH,
                             value=value, scope="global"))
    head.send(BusMessage(namespace=ns, type=MessageType.ACK,
                         value=ack_ok(content_hash(payload), len(payload), 60),
                         scope="global"))

    assert len(bus.sent) == 62, "a frame was dropped for not fitting"
    assert max(len(d) for _, d in bus.sent) <= 8


def test_encode_ack_refuses_out_of_range_fields_rather_than_masking():
    for kwargs in ({"status": 16}, {"chash": 0x10000},
                   {"total_len": -1}, {"ackseq": 70000}):
        args = {"status": ACK_OK, "chash": 1, "total_len": 1, "ackseq": 1}
        args.update(kwargs)
        with pytest.raises(Exception) as exc:
            encode_ack(**args)
        assert "outside" in str(exc.value)


@pytest.mark.parametrize("bad", [b"", b"\xa0" * 6, b"\xa0" * 8, bytes(7),
                                 b"\x06abcdef", None, 42, "ok beef 5856"])
def test_seven_bytes_of_something_else_is_refused_not_parsed(bad):
    with pytest.raises(ConfigAckError):
        decode_ack(bad)


def test_the_retired_text_ack_names_its_own_diagnosis():
    with pytest.raises(ConfigAckError) as exc:
        decode_ack("ok beef 5856")
    assert "REFLASH" in str(exc.value)


# --------------------------------------------------------------------------- #
# ackseq — the field a repair would resume from                                #
# --------------------------------------------------------------------------- #

def test_ackseq_is_the_first_missing_chunk_and_zero_means_nothing_missing():
    payload = bytes(range(256)) * 4
    frames = chunk_config(payload, CAN_CONTENT_BYTES)

    r = Reassembler()
    for i, f in enumerate(frames):
        if i != 3:
            r.on_frame(f)
    assert r.first_missing() == 3
    assert r.ack_seq() == 3

    r = Reassembler()
    for f in frames:
        r.on_frame(f)
    assert r.payload == payload
    assert r.first_missing() == 0
    assert r.ack_seq() == r.count       # the LAST correctly received sequence


def test_a_corrupted_transfer_reports_ackseq_zero_because_nothing_was_lost():
    """0 is information, not absence: it separates CORRUPTION from LOSS.

    A repair that cannot tell them apart retransmits the wrong thing.
    """
    payload = bytes(range(200))
    frames = list(chunk_config(payload, CAN_CONTENT_BYTES))
    c = bytearray(frames[3])
    c[3] ^= 0xFF
    frames[3] = bytes(c)
    r = Reassembler()
    out = [r.on_frame(f) for f in frames]
    assert out[-1] == Reassembler.HASH_MISMATCH
    assert r.ack_seq() == 0


# --------------------------------------------------------------------------- #
# D2 — the head CHECKS the ACK                                                 #
# --------------------------------------------------------------------------- #

def _served_server(scene: bytes = b"title: engine thrust\n"):
    srv = ConfigServer(lambda m: None, lambda d: scene, ["leaf-1"], pace_ms=0)
    assert srv.serve("leaf-1")
    return srv, content_hash(scene), len(scene)


def _ack(srv: ConfigServer, value) -> None:
    srv.on_message(BusMessage(namespace=config_namespace("leaf-1"),
                              type=MessageType.ACK, value=value))


def test_a_correct_ack_counts_as_a_success():
    srv, chash, n = _served_server()
    _ack(srv, ack_ok(chash, n, 1))
    assert (srv.acks_ok, srv.acks_bad, srv.acks_unmatched) == (1, 0, 0)


def test_an_ok_ack_with_the_WRONG_HASH_is_rejected():
    """THE D2 TEST. This is the one that fails against the old code.

    The pre-fix handler was `if text.startswith("ok")`, so a leaf reporting
    success for a scene the head never sent was counted as `acks_ok`. There is
    no ambiguity about what that hides: the head believes a board is running
    the scene it served when it is running something else entirely.
    """
    srv, chash, n = _served_server()
    _ack(srv, ack_ok(chash ^ 0xFFFF, n, 1))
    assert srv.acks_ok == 0, "an ACK naming another transfer counted as success"
    assert srv.acks_unmatched == 1


def test_an_ok_ack_with_the_wrong_LENGTH_is_rejected():
    srv, chash, n = _served_server()
    _ack(srv, ack_ok(chash, n + 1, 1))
    assert srv.acks_ok == 0
    assert srv.acks_unmatched == 1


def test_an_ack_for_a_device_this_head_never_served_is_not_a_success():
    srv = ConfigServer(lambda m: None, lambda d: b"x", ["leaf-1"], pace_ms=0)
    _ack(srv, ack_ok(0xBEEF, 1, 1))
    assert srv.acks_ok == 0
    assert srv.acks_unmatched == 1


@pytest.mark.parametrize("status,seq", [(ACK_HASH_MISMATCH, 0),
                                        (ACK_INCOMPLETE, 13),
                                        (ACK_NO_MEMORY, 0)])
def test_every_failure_status_increments_acks_bad(status, seq):
    """THE COUNTER MUST BE ABLE TO REPORT THE FAILURE IT EXISTS TO CATCH.

    `acks_bad` could not increment at all over CAN before this workstream —
    the message that would have incremented it was too long to send.
    """
    srv, chash, n = _served_server()
    _ack(srv, encode_ack(status, chash, n, seq))
    assert srv.acks_bad == 1
    assert srv.acks_ok == 0


def test_an_unreadable_ack_is_counted_bad_never_ignored():
    srv, chash, n = _served_server()
    _ack(srv, b"\x00\x01\x02")
    _ack(srv, "ok beef 5856")             # an un-reflashed leaf's text ACK
    assert srv.acks_bad == 2
    assert srv.acks_ok == 0


def test_the_head_reports_a_failed_transfer_over_a_real_can_loopback(caplog):
    """The instrument, end to end: leaf FAILS, head hears it, counter moves.

    Nothing here is stubbed except the scene fetch — the ACK crosses a real
    CANTransport pair as 7 bytes on a v2 arbitration id.
    """
    scene = b"widgets:\n  - type: gauge\n"
    head = CANTransport(channel="ack-fail", backend="loopback", node_id=1)
    leaf = CANTransport(channel="ack-fail", backend="loopback", node_id=2)
    head.start()
    leaf.start()
    srv = ConfigServer.for_transport(head, lambda d: scene, ["leaf-1"])
    ns = config_namespace("leaf-1")

    # The leaf reassembles what the head sends, then reports a hash mismatch —
    # the failure the head has never once been told about over CAN.
    asm = Reassembler()

    def on_leaf(msg: BusMessage) -> None:
        if msg.type == MessageType.CONFIG_PUSH:
            asm.on_frame(msg.value)

    leaf.subscribe(ns, on_leaf)
    assert srv.serve("leaf-1")
    assert asm.payload == scene, "the transfer itself did not arrive"

    leaf.send(BusMessage(namespace=ns, type=MessageType.ACK, scope="global",
                         value=encode_ack(ACK_HASH_MISMATCH,
                                          content_hash(scene), len(scene), 0)))
    assert srv.acks_bad == 1
    assert srv.acks_ok == 0


# --------------------------------------------------------------------------- #
# D4 — to_json fails loudly, and bulk does not route through MQTT              #
# --------------------------------------------------------------------------- #

def test_to_json_raises_a_NAMED_error_that_names_the_field():
    msg = BusMessage(namespace="_device/hosyond-d1afe8/config",
                     type=MessageType.CONFIG_PUSH,
                     value=b"\x00\x01" + bytes(6))
    with pytest.raises(UnserialisableValue) as exc:
        msg.to_json()
    text = str(exc.value)
    assert "'val'" in text, "the error must NAME the field"
    assert "_device/hosyond-d1afe8/config" in text
    assert "config_push" in text
    assert "bus://" in text, "it must say what to do instead"


def test_the_named_error_is_still_a_TypeError():
    """Existing `except TypeError` sites keep working — this makes the failure
    legible, it does not move it."""
    assert issubclass(UnserialisableValue, TypeError)


def test_bytes_hidden_in_a_container_are_named_by_their_path():
    msg = BusMessage(namespace="throttle/left", type=MessageType.EVENT,
                     value=None, data={"frame": [b"\x00"]})
    with pytest.raises(UnserialisableValue) as exc:
        msg.to_json()
    assert "data['frame'][0]" in str(exc.value)


def test_ordinary_values_still_serialise():
    for v in (None, 1, 1.5, True, "on", [1, 2], {"a": [1, "b"]}):
        BusMessage(namespace="throttle/left", type=MessageType.STATE,
                   value=v).to_json()


def test_config_server_binds_to_a_can_transport():
    """D3/D4b: the seam existed; this is the caller that uses it."""
    can = CANTransport(channel="bind", backend="loopback", node_id=3)
    srv = ConfigServer.for_transport(can, lambda d: b"scene", ["leaf-1"])
    assert srv.chunk_payload == CAN_CONTENT_BYTES == 6
    assert srv._transport is can
    # Subscribing the device's namespace is not decoration on CAN: the wire
    # carries a 16-bit hash and nothing else, so an unsubscribed namespace is
    # unresolvable when the leaf's ACK arrives.
    assert config_namespace("leaf-1") in can._subs


def test_binding_to_can_imports_NO_mqtt_client():
    """`without MQTT in the path` asserted where it can actually be asserted.

    In-process this would be order-dependent — any earlier test that imported
    `transport_mqtt` would leave it in `sys.modules` and the check would pass
    or fail depending on collection order, which is not evidence. A clean
    interpreter is.
    """
    import subprocess
    src = (
        "import sys;"
        f"sys.path[:0]=[{str(_REPO / 'python')!r}, {str(_REPO / 'services')!r}];"
        "from composeio.transport_can import CANTransport;"
        "from uart_mqtt_bridge.config_server import ConfigServer;"
        "srv=ConfigServer.for_transport("
        "  CANTransport(channel='iso', backend='loopback'),"
        "  lambda d: b'scene', ['leaf-1']);"
        "assert srv.chunk_payload==6;"
        "bad=[m for m in sys.modules if 'paho' in m or 'transport_mqtt' in m];"
        "print('LOADED:'+','.join(bad) if bad else 'CLEAN')"
    )
    out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                         text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "CLEAN", out.stdout


def test_binding_bulk_to_an_mqtt_transport_is_REFUSED_with_a_reason():
    class MQTTTransport:                     # a stand-in; no broker, no paho
        pass

    with pytest.raises(BulkTransportRefused) as exc:
        chunk_payload_for_transport(MQTTTransport())
    assert "JSON" in str(exc.value) and "OPAQUE BYTES" in str(exc.value)


def test_an_unknown_transport_is_refused_rather_than_guessed():
    class SomethingElse:
        pass

    with pytest.raises(BulkTransportRefused):
        chunk_payload_for_transport(SomethingElse())


def test_a_config_chunk_still_goes_on_a_framed_wire_verbatim():
    """The counterpart to the refusal: opaque bytes ARE carriable — by the
    substrate the protocol was designed for."""
    frame = chunk_config(b"abcdef" * 3, CAN_CONTENT_BYTES)[1]
    assert encode_value(frame) == frame
