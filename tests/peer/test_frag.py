"""[BPR-B-FRAG] / [SCEN-BPR-B-REASSEMBLE] — fragmentation / reassembly over CAN.

Thresholds realized here (pure ISO-TP layer, no bus, no hardware):
  * a payload larger than one CAN frame fragments and reassembles BYTE-IDENTICAL;
  * a lost MIDDLE fragment is caught as a sequence gap -> discard + fault;
  * a lost TAIL fragment is caught by the reassembly timeout -> discard + fault;
  * in NO case is a partial payload delivered.
"""

import pytest

from composeio.peer.frag import fragment, Reassembler, PCI_SF, PCI_FF, PCI_CF


def _feed_all(reasm, can_id, frames, start=0.0):
    out = None
    for fr in frames:
        r = reasm.feed(can_id, fr, start)
        if r is not None:
            out = r
    return out


def test_single_frame_payload_roundtrips():
    payload = b"\x01\x02\x03"
    frames = fragment(payload, frame_size=8)
    assert len(frames) == 1
    assert frames[0][0] & 0xF0 == PCI_SF
    reasm = Reassembler(frame_size=8)
    assert reasm.feed(0x123, frames[0], 0.0) == payload


def test_large_payload_reassembles_byte_identical():
    payload = bytes((i * 7) & 0xFF for i in range(200))   # 200B >> one 8B frame
    frames = fragment(payload, frame_size=8)
    assert len(frames) > 1
    assert frames[0][0] & 0xF0 == PCI_FF
    assert all(f[0] & 0xF0 == PCI_CF for f in frames[1:])

    reasm = Reassembler(frame_size=8)
    assert _feed_all(reasm, 0x18ABCDEF, frames) == payload
    assert reasm.pending_ids() == []   # completed transfer left nothing pending


def test_lost_middle_fragment_faults_and_delivers_no_partial():
    payload = bytes(range(60))
    frames = fragment(payload, frame_size=8)
    faults = []
    reasm = Reassembler(frame_size=8, on_fault=lambda cid, why: faults.append((cid, why)))

    # Drop the first CF (index 1) — a MIDDLE gap the reassembler must catch.
    kept = [frames[0]] + frames[2:]
    result = _feed_all(reasm, 0x321, kept)

    assert result is None                                  # NO partial delivery
    assert any(why == "reassembly_sequence_gap" for _, why in faults)
    assert reasm.pending_ids() == []                       # partial discarded


def test_lost_tail_fragment_times_out_and_delivers_no_partial():
    payload = bytes(range(40))
    frames = fragment(payload, frame_size=8)
    faults = []
    reasm = Reassembler(frame_size=8, timeout=1.0,
                        on_fault=lambda cid, why: faults.append((cid, why)))

    # Feed everything EXCEPT the last frame, at t=0.
    result = _feed_all(reasm, 0x777, frames[:-1], start=0.0)
    assert result is None
    assert reasm.pending_ids() == [0x777]                  # stalled, not yet faulted

    # Before the timeout: still pending, no fault.
    assert reasm.poll_timeouts(now=0.5) == []
    assert faults == []

    # Past the 1000ms timeout: discarded + faulted, still no partial delivered.
    assert reasm.poll_timeouts(now=1.5) == [0x777]
    assert faults == [(0x777, "reassembly_timeout")]
    assert reasm.pending_ids() == []


def test_orphan_consecutive_frame_is_ignored_not_partial():
    # A CF with no preceding FF (its FF was lost) must not synthesize a transfer.
    reasm = Reassembler(frame_size=8)
    assert reasm.feed(0x999, bytes([PCI_CF | 1]) + b"abcdefg", 0.0) is None
    assert reasm.pending_ids() == []


def test_payload_exceeding_isotp_length_is_rejected():
    with pytest.raises(ValueError):
        fragment(b"x" * 5000, frame_size=8)
