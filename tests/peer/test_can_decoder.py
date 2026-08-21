"""Phase 1 decoder evaluator + map-schema loader tests.

Covers the structural rows of [CXC-CAN-VERIFY]:
  * CAN-C4 — Python decoder golden, big AND little byte_order
  * CAN-C6 — no SIGABRT on unmapped enum / out-of-domain init value
  * CAN-C7 — decoder-map schema validation cases

Plus: the synthetic Fiat map loads and drives fiat/* (the artifact the OQ-1
candump populates, [CXC-CAN-OQ-CANDUMP]).
"""

from pathlib import Path

import pytest

from composeio.bus import ComposeIOBus
from composeio.peer import (
    CanIngest, DecoderMap, DecoderMapError, extract_bits,
)

_SYNTH_MAP = (Path(__file__).resolve().parents[2]
              / "services" / "can_mqtt_bridge" / "decoders" / "fiat_synthetic.yaml")


# --- CAN-C4: golden decode, big AND little byte_order ------------------------

def test_extract_bits_big_vs_little_golden():
    data = bytes([0x12, 0x34, 0x56, 0x78])
    # A 16-bit field at start_bit 8 decodes DIFFERENTLY per byte order — the
    # whole reason byte_order is carried explicitly ([CXC-CAN-DECODER]).
    assert extract_bits(data, 8, 16, "big") == 0x3456     # bytes[1],bytes[2] MSB-first
    assert extract_bits(data, 8, 16, "little") == 0x5634  # Intel LSB-first
    assert extract_bits(data, 8, 16, "big") != extract_bits(data, 8, 16, "little")


def test_extract_bits_byte_aligned_and_nibble():
    frame = bytes([0xC8, 0x00, 0x30, 0x00, 0x00, 0x00, 0x00, 0x00])
    # byte 0 big => 200
    assert extract_bits(frame, 0, 8, "big") == 0xC8
    # top nibble of byte 0 big (MSB0 contiguous)
    assert extract_bits(bytes([0x30]), 0, 4, "big") == 0x3


def test_decoder_golden_signal_values():
    dm = DecoderMap.from_dict({
        "bus": "fiat",
        "signals": [
            {"frame_id": 0x100, "publish": "x/big", "start_bit": 8, "length": 16,
             "byte_order": "big"},
            {"frame_id": 0x101, "publish": "x/little", "start_bit": 8,
             "length": 16, "byte_order": "little"},
            {"frame_id": 0x102, "publish": "x/soc", "start_bit": 0, "length": 8,
             "byte_order": "big", "scale": 0.4},
        ],
    })
    bus = ComposeIOBus(bus_id="golden")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)

    ingest.feed(0x100, False, bytes([0x12, 0x34, 0x56, 0x78]))
    ingest.feed(0x101, False, bytes([0x12, 0x34, 0x56, 0x78]))
    ingest.feed(0x102, False, bytes([0xC8]))

    assert bus.get_state("x/big") == 0x3456
    assert bus.get_state("x/little") == 0x5634
    assert bus.get_state("x/soc") == pytest.approx(80.0)
    bus.stop()


def test_enum_decode_publishes_mapped_string():
    dm = DecoderMap.from_dict({
        "bus": "fiat",
        "signals": [{"frame_id": 0x11A, "publish": "fiat/gear", "start_bit": 0,
                     "length": 4, "engine": "enum",
                     "map": {0: "P", 1: "R", 2: "N", 3: "D"}}],
    })
    bus = ComposeIOBus(bus_id="enum-ok")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)
    ingest.feed(0x11A, False, bytes([0x30]))  # nibble 3 -> D
    assert bus.get_state("fiat/gear") == "D"
    bus.stop()


# --- CAN-C6: no SIGABRT on unexpected vehicle data ---------------------------

def test_unmapped_enum_does_not_raise_retains_last_value():
    dm = DecoderMap.from_dict({
        "bus": "fiat",
        "signals": [{"frame_id": 0x11A, "publish": "fiat/gear", "start_bit": 0,
                     "length": 4, "engine": "enum",
                     "map": {0: "P", 1: "R", 2: "N", 3: "D"}}],
    })
    bus = ComposeIOBus(bus_id="enum-miss")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)

    ingest.feed(0x11A, False, bytes([0x30]))   # nibble 3 -> "D" (valid)
    assert bus.get_state("fiat/gear") == "D"

    # nibble 5 is NOT in the map -> the SIGABRT trap. MUST NOT raise; the
    # process stays alive; fiat/gear RETAINS "D" ([CXC-CAN-JSON-RULE]).
    ingest.feed(0x11A, False, bytes([0x50]))
    assert bus.get_state("fiat/gear") == "D"      # retained last valid value
    assert ingest.enum_misses == 1
    bus.stop()


def test_empty_init_and_out_of_domain_strict_engine_does_not_abort():
    # A builder mis-typed a signal to a strict 'binary' engine; the decoded raw
    # value is out of {off,on}. The publish guard MUST catch it — no SIGABRT.
    dm = DecoderMap.from_dict({
        "bus": "fiat",
        "signals": [{"frame_id": 0x200, "publish": "fiat/contactor",
                     "start_bit": 0, "length": 8, "engine": "binary"}],
    })
    bus = ComposeIOBus(bus_id="strict-guard")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)

    # raw 5 -> not a valid binary value; would ValueError into a callback.
    ingest.feed(0x200, False, bytes([5]))
    assert ingest.decode_errors == 1   # caught, logged, NOT raised
    # Process is alive and the namespace rests at its default (empty init).
    assert bus.get_state("fiat/contactor") == "off"
    bus.stop()


def test_runtime_short_payload_drops_not_raises():
    dm = DecoderMap.from_dict({
        "bus": "fiat",
        "signals": [{"frame_id": 0x300, "publish": "fiat/range", "start_bit": 0,
                     "length": 16, "byte_order": "big"}],
    })
    bus = ComposeIOBus(bus_id="short-pay")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)
    ingest.feed(0x300, False, bytes([0x01]))   # only 8 bits, field needs 16
    assert ingest.decode_errors == 1           # dropped, not raised
    bus.stop()


# --- CAN-C7: decoder-map schema validation -----------------------------------

def test_missing_required_field_names_the_entry():
    with pytest.raises(DecoderMapError) as e:
        DecoderMap.from_dict({"signals": [
            {"frame_id": 0x1, "start_bit": 0, "length": 8}]})  # no 'publish'
    assert "publish" in str(e.value)


def test_both_scale_offset_and_map_rejected():
    with pytest.raises(DecoderMapError):
        DecoderMap.from_dict({"signals": [
            {"frame_id": 0x1, "publish": "a/b", "start_bit": 0, "length": 4,
             "scale": 2.0, "map": {0: "P"}}]})


def test_enum_without_map_rejected():
    with pytest.raises(DecoderMapError):
        DecoderMap.from_dict({"signals": [
            {"frame_id": 0x1, "publish": "a/b", "start_bit": 0, "length": 4,
             "engine": "enum"}]})


def test_overrun_payload_rejected_at_load():
    with pytest.raises(DecoderMapError):
        DecoderMap.from_dict({"signals": [
            {"frame_id": 0x1, "publish": "a/b", "start_bit": 56, "length": 16}]})


def test_empty_signals_loads_and_decodes_nothing():
    dm = DecoderMap.from_dict({"bus": "fiat", "signals": []})
    assert dm.signals == []
    bus = ComposeIOBus(bus_id="empty-map")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)
    ingest.feed(0x123, False, bytes([1, 2, 3]))  # no entries -> nothing happens
    assert ingest.acceptance_filters() == []
    bus.stop()


def test_frame_id_hex_string_accepted():
    dm = DecoderMap.from_dict({"signals": [
        {"frame_id": "0x3d5", "publish": "fiat/soc", "start_bit": 0,
         "length": 8}]})
    assert dm.signals[0].frame_id == 0x3D5


# --- The synthetic Fiat map loads and drives fiat/* --------------------------

def test_synthetic_fiat_map_loads_and_moves_gauges():
    dm = DecoderMap.from_yaml(_SYNTH_MAP)
    published = {s.publish for s in dm.signals}
    assert {"fiat/soc", "fiat/speed", "fiat/range", "fiat/gear"} <= published

    bus = ComposeIOBus(bus_id="synth")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)

    ingest.feed(0x3D5, False, bytes([0xC8]))            # SOC raw 200 * 0.4 = 80
    ingest.feed(0x11A, False, bytes([0x30]))            # gear nibble 3 -> D
    assert bus.get_state("fiat/soc") == pytest.approx(80.0)
    assert bus.get_state("fiat/gear") == "D"
    bus.stop()
