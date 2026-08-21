"""Phase 1 SocketCAN reader + Layer-1 raw + acceptance-filter tests.

Covers the structural rows of [CXC-CAN-VERIFY]:
  * CAN-C1 — raw Layer-1 shape + eff-not-magnitude
  * CAN-C2 — RTR / error / over-DLC / short frames dropped at the reader
  * CAN-C3 — reader green under MockCanBus on macOS, NO python-can import
  * CAN-C10 — acceptance filter narrows to the decoder-map frame ids
"""

import sys

from composeio.bus import ComposeIOBus
from composeio.peer import (
    MockCanBus, MockCanReader, CanIngest, DecoderMap,
    make_can_id, parse_can_frame, pack_can_frame,
    CAN_EFF_MASK,
)


# --- CAN-C3: zero python-can, reader works behind the mockcan seam -----------

def test_no_python_can_imported():
    # The whole point of the migration: python-can is GONE ([CXC-CAN-LICENSE]).
    assert "can" not in sys.modules or getattr(
        sys.modules.get("can"), "__name__", "") != "can"
    import composeio.peer.socketcan as sc
    assert "can" not in sys.modules, "python-can must not be imported"
    # The reader module is stdlib-only.
    assert sc.CAN_FRAME_SIZE == 16


def test_socketcan_reader_decodes_standard_and_extended_under_mock():
    r = MockCanReader()
    r.inject(0x123, bytes([1, 2, 3]), eff=False)
    r.inject(0x18DAF110, bytes([0xAA, 0xBB]), eff=True)

    f1 = r.read_frame()
    assert f1 == (0x123, False, bytes([1, 2, 3]))
    f2 = r.read_frame()
    assert f2 == (0x18DAF110, True, bytes([0xAA, 0xBB]))
    assert r.read_frame() is None  # queue drained


def test_reader_over_mockcanbus_delivery():
    bus = MockCanBus()
    r = MockCanReader(bus=bus)
    # A flag-bearing can_id routed exactly as the wire carries it.
    bus.send(make_can_id(0x201, eff=True), bytes([9, 8, 7]))
    assert r.read_frame() == (0x201, True, bytes([9, 8, 7]))


# --- CAN-C1: raw Layer-1 shape + eff derived from flag, not magnitude --------

def test_raw_frame_namespace_shape_and_json_engine():
    bus = ComposeIOBus(bus_id="raw-test")
    bus.start()
    seen = []
    bus.on_message(lambda m: seen.append(m) if m.namespace.startswith("can/") else None)

    ingest = CanIngest(bus, logical_bus="fiat", decoder=None, raw_enabled=True)
    ingest.feed(0x3D5, False, bytes.fromhex("0a1b00ff0022ee01"))

    ns = "can/fiat/0x3d5"
    assert bus.get_namespace(ns) is not None
    assert bus.get_namespace(ns).state_engine.engine_type == "json"
    val = bus.get_state(ns)
    assert val["id"] == 0x3D5
    assert val["eff"] is False
    assert val["dlc"] == 8
    assert val["data"] == "0a1b00ff0022ee01"
    assert isinstance(val["t"], float)
    assert any(m.namespace == ns for m in seen)
    bus.stop()


def test_eff_disambiguates_same_id_not_merged_by_magnitude():
    bus = ComposeIOBus(bus_id="eff-test")
    bus.start()
    captured = []
    bus.on_message(lambda m: captured.append((m.namespace, m.value))
                   if m.namespace.startswith("can/") else None)

    ingest = CanIngest(bus, logical_bus="fiat", raw_enabled=True)
    # 0x123 fits 11 bits — magnitude alone would say "standard". The reader must
    # honor the transport EFF flag: an extended 0x00000123 stays eff=True.
    ingest.feed(0x123, False, bytes([1]))
    ingest.feed(0x123, True, bytes([2]))

    # Same namespace space (shared by 11- and 29-bit), disambiguated by eff.
    ns = "can/fiat/0x123"
    effs = {v["eff"] for (n, v) in captured if n == ns}
    assert effs == {False, True}, "eff must come from the flag, not id magnitude"
    bus.stop()


# --- CAN-C2: RTR / error / over-DLC / short dropped at the reader ------------

def test_rtr_error_overdlc_short_dropped_not_raised():
    r = MockCanReader()
    r.inject(0x100, bytes([1, 2]), rtr=True)                 # RTR -> drop
    r.inject(0x101, bytes([3, 4]), err=True)                 # error -> drop
    r.inject(0x102, bytes([5, 6, 7, 8]), dlc=12)             # over-DLC -> drop
    r.inject_short(b"\x00\x00\x00")                          # short -> drop
    r.inject(0x103, bytes([0xDE, 0xAD]))                     # the one good frame

    frames = []
    while True:
        f = r.read_frame()
        if f is None:
            break
        frames.append(f)

    assert frames == [(0x103, False, bytes([0xDE, 0xAD]))]
    assert r.stats.rtr_or_err == 2
    assert r.stats.over_dlc == 1
    assert r.stats.short == 1
    assert r.stats.total == 4


def test_dropped_frames_publish_on_neither_layer():
    bus = ComposeIOBus(bus_id="drop-test")
    bus.start()
    published = []
    bus.on_message(lambda m: published.append(m.namespace)
                   if m.namespace.startswith("can/") else None)

    r = MockCanReader()
    r.inject(0x100, bytes([1, 2]), rtr=True)
    r.inject(0x101, bytes([3, 4]), err=True)

    ingest = CanIngest(bus, logical_bus="fiat", raw_enabled=True)
    while ingest.run_once(r, timeout=0.0):
        pass

    assert published == []  # nothing published for dropped frames
    bus.stop()


def test_pure_parse_helpers_roundtrip():
    raw = pack_can_frame(make_can_id(0x1ABCDEF, eff=True), bytes([1, 2, 3, 4]))
    assert len(raw) == 16
    arb, eff, data = parse_can_frame(raw)
    assert eff is True
    assert arb == (0x1ABCDEF & CAN_EFF_MASK)
    assert data == bytes([1, 2, 3, 4])
    # RTR frame parses to None (a drop), never raises.
    assert parse_can_frame(pack_can_frame(make_can_id(0x5, rtr=True), b"")) is None


# --- CAN-C10: acceptance filter narrows to the map frame ids -----------------

def test_acceptance_filters_cover_exactly_map_frame_ids():
    dm = DecoderMap.from_dict({
        "bus": "fiat",
        "signals": [
            {"frame_id": 0x3D5, "publish": "fiat/soc", "start_bit": 0,
             "length": 8, "scale": 0.4},
            {"frame_id": 0x11A, "publish": "fiat/gear", "start_bit": 0,
             "length": 4, "engine": "enum", "map": {0: "P", 1: "R"}},
        ],
    })
    bus = ComposeIOBus(bus_id="filter-test")
    bus.start()
    ingest = CanIngest(bus, logical_bus="fiat", decoder=dm)
    filters = ingest.acceptance_filters()
    assert len(filters) == 2
    matched = {cid for (cid, _mask) in filters}
    assert 0x3D5 in matched and 0x11A in matched
    bus.stop()
