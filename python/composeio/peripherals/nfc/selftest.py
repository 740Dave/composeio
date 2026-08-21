"""Protocol self-test for the PN532 NFC peripheral (no hardware required).

Verifies the PROTOCOL/PARSE/PUBLISH logic against a mock I2C transport that
replays canned PN532 frames. This is the machine-green tier: a real PN532 read
on David's I2C rig (0x24) is glass-pending.

Covered:
  1. Successful ISO14443A read of UID 04:AB:CD:EF -> parses + publishes
     nfc/uid="04abcdef", nfc/present=True.
  2. No-tag poll -> publishes nfc/present=False.
  3. Frame integrity: LCS/DCS computed correctly; corrupt frame rejected.
  4. Debounce: a held tag publishes uid once; departure flips present.

Run standalone::

    python -m composeio.peripherals.nfc --selftest

or under pytest (the ``test_*`` functions below are collected automatically).
"""

from __future__ import annotations

from . import frames
from .pn532 import PN532
from .poller import NfcPoller
from .transport import MockI2CTransport

# --- A recording publish sink (matches ComposeIOBus.publish_state) ----------


class RecordingSink:
    """Captures publishes as (namespace, value) pairs, newest-last."""

    def __init__(self) -> None:
        self.published: list[tuple[str, object]] = []

    def publish_state(self, namespace: str, value: object, source: str = "") -> None:
        self.published.append((namespace, value))

    def last(self, namespace: str):
        for ns, val in reversed(self.published):
            if ns == namespace:
                return val
        raise KeyError(f"nothing published to {namespace!r}")

    def count(self, namespace: str) -> int:
        return sum(1 for ns, _ in self.published if ns == namespace)


# --- Canned response payloads (response code + data, TFI added by builder) --

# InListPassiveTarget reply for one type-A target, UID 04 AB CD EF:
#   4B  NbTg  Tg  ATQA(2)  SAK  UIDLen  UID...
_UID = bytes([0x04, 0xAB, 0xCD, 0xEF])
_TARGET_PAYLOAD = bytes([0x4B, 0x01, 0x01, 0x00, 0x44, 0x00, len(_UID)]) + _UID
# No-target reply: NbTg = 0
_NOTAG_PAYLOAD = bytes([0x4B, 0x00])
# SAMConfiguration reply
_SAM_PAYLOAD = bytes([0x15])


def _new_reader_with_tag(uid_payload: bytes) -> tuple[PN532, MockI2CTransport]:
    """Wire a PN532 to a mock scripted with SAM begin + one target read."""
    mock = MockI2CTransport()
    mock.queue_command_exchange(frames.build_response_frame(_SAM_PAYLOAD))
    mock.queue_command_exchange(frames.build_response_frame(uid_payload))
    return PN532(mock), mock


# --- Tests ------------------------------------------------------------------


def test_frame_checksums_known_good():
    """LCS/DCS match a hand-computed reference frame for the UID read."""
    frame = frames.build_response_frame(_TARGET_PAYLOAD)
    # Independent recomputation of the two checksums.
    length = len(_TARGET_PAYLOAD) + 1  # +TFI
    assert frame[3] == length
    assert (frame[3] + frame[4]) & 0xFF == 0, "LCS must zero the length sum"
    tfi = frames.PN532_TO_HOST
    dcs = frame[-2]
    assert (tfi + sum(_TARGET_PAYLOAD) + dcs) & 0xFF == 0, "DCS must zero data sum"
    # Round-trips back to the payload.
    assert frames.parse_response_frame(frame) == _TARGET_PAYLOAD


def test_frame_corruption_rejected():
    """A single-bit flip in the data field must fail the DCS check."""
    frame = bytearray(frames.build_response_frame(_TARGET_PAYLOAD))
    # Flip a UID byte without fixing the DCS.
    frame[10] ^= 0xFF
    try:
        frames.parse_response_frame(bytes(frame))
    except frames.FrameError:
        pass
    else:
        raise AssertionError("corrupt frame was NOT rejected")


def test_read_known_uid_and_publish():
    """Full path: begin + read UID 04ABCDEF -> present=True, uid=04abcdef."""
    reader, mock = _new_reader_with_tag(_TARGET_PAYLOAD)
    sink = RecordingSink()
    poller = NfcPoller(reader, sink, prefix="nfc", source="nfc-selftest")
    poller.begin()
    uid = poller.poll_once()

    assert uid == "04abcdef", f"got {uid!r}"
    assert sink.last("nfc/present") is True
    assert sink.last("nfc/uid") == "04abcdef"
    assert mock.drained, "driver left scripted reads unconsumed"
    # Verify a well-formed command frame went out for InListPassiveTarget.
    assert any(b"\x4a" in w for w in mock.writes), "no InListPassiveTarget write"


def test_no_tag_publishes_absent():
    """A no-tag poll (after a prior presence) publishes present=False."""
    # Script: SAM, then a tag read, then a no-tag read.
    mock = MockI2CTransport()
    mock.queue_command_exchange(frames.build_response_frame(_SAM_PAYLOAD))
    mock.queue_command_exchange(frames.build_response_frame(_TARGET_PAYLOAD))
    mock.queue_command_exchange(frames.build_response_frame(_NOTAG_PAYLOAD))
    poller = NfcPoller(PN532(mock), RecordingSink(), prefix="nfc")
    poller.begin()
    poller.poll_once()  # tag present
    assert poller.poll_once() is None  # departed
    assert poller.sink.last("nfc/present") is False


def test_no_tag_from_cold_is_false():
    """A no-tag poll with no prior tag reports absence cleanly."""
    mock = MockI2CTransport()
    mock.queue_command_exchange(frames.build_response_frame(_SAM_PAYLOAD))
    mock.queue_command_exchange(frames.build_response_frame(_NOTAG_PAYLOAD))
    sink = RecordingSink()
    poller = NfcPoller(PN532(mock), sink, prefix="nfc")
    poller.begin()
    assert poller.poll_once() is None
    # Cold start with no tag: nothing to transition, so no spurious publish.
    assert sink.count("nfc/present") == 0
    assert sink.count("nfc/uid") == 0


def test_debounce_held_tag_publishes_uid_once():
    """A held tag (same uid over two polls) publishes uid exactly once."""
    mock = MockI2CTransport()
    mock.queue_command_exchange(frames.build_response_frame(_SAM_PAYLOAD))
    mock.queue_command_exchange(frames.build_response_frame(_TARGET_PAYLOAD))
    mock.queue_command_exchange(frames.build_response_frame(_TARGET_PAYLOAD))
    sink = RecordingSink()
    poller = NfcPoller(PN532(mock), sink, prefix="nfc")
    poller.begin()
    poller.poll_once()
    poller.poll_once()
    assert sink.count("nfc/uid") == 1, "held tag re-published uid (debounce broke)"
    assert sink.count("nfc/present") == 1


# --- Runner -----------------------------------------------------------------

_TESTS = [
    test_frame_checksums_known_good,
    test_frame_corruption_rejected,
    test_read_known_uid_and_publish,
    test_no_tag_publishes_absent,
    test_no_tag_from_cold_is_false,
    test_debounce_held_tag_publishes_uid_once,
]


def run() -> int:
    """Run all protocol tests; return process exit code (0 == all green)."""
    failures = 0
    for test in _TESTS:
        name = test.__name__
        try:
            test()
        except Exception as exc:  # noqa: BLE001 - report, don't abort
            failures += 1
            print(f"  FAIL  {name}: {exc}")
        else:
            print(f"  ok    {name}")
    total = len(_TESTS)
    print()
    if failures:
        print(f"NFC protocol self-test: {total - failures}/{total} passed, "
              f"{failures} FAILED  [machine-red]")
    else:
        print(f"NFC protocol self-test: {total}/{total} passed  "
              f"[machine-green — protocol/parse/publish]")
        print("Real PN532 read on I2C 0x24: GLASS-PENDING (David's rig).")
    return 1 if failures else 0
