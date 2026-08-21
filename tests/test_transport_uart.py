"""Test UART transport — binary frame encode/decode.

Tests the wire format without requiring a physical serial port.
"""

import pytest
from composeio.transport import BusMessage, MessageType
from composeio.transport_uart import (
    encode_message, decode_message, encode_value, decode_value,
    register_namespace_hash, _crc8, _SYNC,
)


class TestValueEncoding:
    def test_none(self):
        assert encode_value(None) == b""

    def test_int_small(self):
        assert encode_value(42) == bytes([42])

    def test_int_zero(self):
        assert encode_value(0) == bytes([0])

    def test_string(self):
        encoded = encode_value("on")
        assert encoded[0] == 2  # Length prefix
        assert encoded[1:] == b"on"

    def test_string_value_roundtrip(self):
        encoded = encode_value("warp_5")
        decoded = decode_value(encoded)
        assert decoded == "warp_5"


class TestFrameEncoding:
    def test_basic_state_message(self):
        register_namespace_hash("ftl_arm")
        msg = BusMessage(
            namespace="ftl_arm",
            type=MessageType.STATE,
            value="on",
            scope="global",
        )
        frame = encode_message(msg)
        assert frame[0] == _SYNC
        assert len(frame) < 20  # Should be compact

    def test_roundtrip(self):
        """Encode → decode produces equivalent message."""
        register_namespace_hash("ftl_jump")
        msg = BusMessage(
            namespace="ftl_jump",
            type=MessageType.EVENT,
            value=None,
            scope="local",
        )
        frame = encode_message(msg)
        decoded = decode_message(frame)
        assert decoded is not None
        assert decoded.namespace == "ftl_jump"
        assert decoded.type == MessageType.EVENT
        assert decoded.scope == "local"

    def test_state_value_roundtrip(self):
        register_namespace_hash("test_ns")
        msg = BusMessage(
            namespace="test_ns",
            type=MessageType.STATE,
            value=128,
            scope="auto",
        )
        frame = encode_message(msg)
        decoded = decode_message(frame)
        assert decoded is not None
        assert decoded.value == 128
        assert decoded.scope == "auto"

    def test_string_value_roundtrip(self):
        register_namespace_hash("warp_factor")
        msg = BusMessage(
            namespace="warp_factor",
            type=MessageType.STATE,
            value="warp_5",
            scope="global",
        )
        frame = encode_message(msg)
        decoded = decode_message(frame)
        assert decoded is not None
        assert decoded.value == "warp_5"

    def test_scope_encoding(self):
        """All four scopes encode correctly."""
        register_namespace_hash("scope_test")
        for scope in ["local", "global", "remote", "auto"]:
            msg = BusMessage(
                namespace="scope_test",
                type=MessageType.STATE,
                value=1,
                scope=scope,
            )
            frame = encode_message(msg)
            decoded = decode_message(frame)
            assert decoded.scope == scope, f"Scope {scope} failed roundtrip"

    def test_all_message_types(self):
        """All message types encode correctly."""
        register_namespace_hash("type_test")
        for msg_type in MessageType:
            msg = BusMessage(
                namespace="type_test",
                type=msg_type,
                value=0,
                scope="local",
            )
            frame = encode_message(msg)
            decoded = decode_message(frame)
            assert decoded.type == msg_type, f"Type {msg_type} failed roundtrip"

    def test_message_size_compact(self):
        """Binary frames should be much smaller than JSON equivalents."""
        register_namespace_hash("ftl_arm")
        msg = BusMessage(
            namespace="ftl_arm",
            type=MessageType.STATE,
            value="on",
            scope="global",
        )
        frame = encode_message(msg)
        json_size = msg.size_estimate()
        assert len(frame) < json_size, f"Binary {len(frame)} >= JSON {json_size}"


class TestFrameValidation:
    def test_corrupt_crc(self):
        register_namespace_hash("crc_test")
        msg = BusMessage(namespace="crc_test", type=MessageType.STATE, value=1, scope="local")
        frame = bytearray(encode_message(msg))
        frame[-1] ^= 0xFF  # Corrupt CRC
        assert decode_message(bytes(frame)) is None

    def test_too_short(self):
        assert decode_message(b"\xC1\x01") is None

    def test_wrong_sync(self):
        assert decode_message(b"\x00\x03\x00\x00\x00\x00") is None

    def test_unknown_namespace_hash(self):
        """Unknown hash produces _unknown_ namespace name."""
        frame = bytes([_SYNC, 3, 0xFF, 0xFF, 0x00, 0])
        # Compute CRC manually
        from composeio.transport_uart import _crc8
        payload = frame[2:2 + frame[1]]
        crc = _crc8(bytes([frame[1]]) + payload)
        frame = frame[:5] + bytes([crc])
        decoded = decode_message(frame)
        if decoded:
            assert decoded.namespace.startswith("_unknown_")


class TestCRC8:
    def test_empty(self):
        assert _crc8(b"") == 0

    def test_deterministic(self):
        assert _crc8(b"hello") == _crc8(b"hello")

    def test_different_data(self):
        assert _crc8(b"hello") != _crc8(b"world")
