r"""PN532 normal-information-frame codec.

The PN532 speaks a single wire frame format regardless of the physical
transport (I2C / SPI / HSU). This module builds host->reader command frames
and parses reader->host response frames, computing/validating the two
checksums the protocol defines:

    LCS (length checksum)   : (LEN + LCS) & 0xFF == 0
    DCS (data checksum)     : (TFI + sum(DATA) + DCS) & 0xFF == 0

Frame layout (normal information frame, NXP UM0701-02 §6.2.1.1):

    00 00 FF  LEN  LCS  TFI  DATA...  DCS  00
    \______/                 \_____/
    preamble +               command/response
    start code               code + params

TFI is 0xD4 for host->PN532 and 0xD5 for PN532->host. LEN counts the data
field *including* the TFI byte, so LEN == len(DATA) + 1.

This module is pure and hardware-free — it is the unit under test for frame
integrity (LCS/DCS correctness + corrupt-frame rejection).
"""

from __future__ import annotations

# --- Wire constants ---------------------------------------------------------

PREAMBLE = 0x00
STARTCODE1 = 0x00
STARTCODE2 = 0xFF
POSTAMBLE = 0x00

HOST_TO_PN532 = 0xD4  # TFI for command frames
PN532_TO_HOST = 0xD5  # TFI for response frames

# The fixed 6-byte acknowledgement frame (UM0701-02 §6.2.1.3).
ACK_FRAME = bytes([0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00])
# The fixed 6-byte NACK frame.
NACK_FRAME = bytes([0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00])


class FrameError(ValueError):
    """A PN532 frame failed structural or checksum validation."""


def _checksum_complement(total: int) -> int:
    """Two's-complement checksum byte: value s.t. (total + result) & 0xFF == 0."""
    return (-total) & 0xFF


def length_checksum(length: int) -> int:
    """LCS for a given LEN. (LEN + LCS) & 0xFF == 0."""
    return _checksum_complement(length)


def data_checksum(tfi: int, data: bytes) -> int:
    """DCS over TFI + data field. (TFI + sum(data) + DCS) & 0xFF == 0."""
    return _checksum_complement(tfi + sum(data))


def build_command_frame(payload: bytes) -> bytes:
    """Build a host->PN532 command frame.

    *payload* is the command byte followed by its parameters (WITHOUT the TFI
    — the TFI is added here). Returns the full wire frame including preamble,
    checksums, and postamble.
    """
    payload = bytes(payload)
    length = len(payload) + 1  # +1 for the TFI byte
    lcs = length_checksum(length)
    dcs = data_checksum(HOST_TO_PN532, payload)
    return bytes(
        [PREAMBLE, STARTCODE1, STARTCODE2, length, lcs, HOST_TO_PN532]
        + list(payload)
        + [dcs, POSTAMBLE]
    )


def build_response_frame(payload: bytes) -> bytes:
    """Build a PN532->host response frame (TFI 0xD5).

    Used to synthesize canned reader responses for the mock transport / tests.
    *payload* is the response code byte followed by its data (WITHOUT the TFI).
    """
    payload = bytes(payload)
    length = len(payload) + 1
    lcs = length_checksum(length)
    dcs = data_checksum(PN532_TO_HOST, payload)
    return bytes(
        [PREAMBLE, STARTCODE1, STARTCODE2, length, lcs, PN532_TO_HOST]
        + list(payload)
        + [dcs, POSTAMBLE]
    )


def is_ack(raw: bytes) -> bool:
    """True if *raw* contains the PN532 ACK signature (allowing padding).

    Matches on the 5-byte core ``00 FF 00 FF 00`` (start code + ACK code +
    postamble) so leading I2C preamble/padding bytes are tolerated.
    """
    return bytes(raw).find(ACK_FRAME[1:]) >= 0


def parse_response_frame(raw: bytes) -> bytes:
    """Parse a PN532->host response frame and return its payload.

    The payload returned is the data field with the TFI stripped — i.e. the
    response-code byte followed by its data. Raises :class:`FrameError` on any
    structural or checksum violation.
    """
    raw = bytes(raw)

    # Locate the start code 00 FF. The frame is preceded by a preamble 0x00
    # and, over I2C, may be preceded by discardable padding bytes.
    idx = raw.find(b"\x00\xff")
    if idx < 0:
        raise FrameError("no start code (00 FF) found in frame")
    p = idx + 2  # points at LEN

    if p + 2 > len(raw):
        raise FrameError("frame truncated before length field")
    length = raw[p]
    lcs = raw[p + 1]
    if (length + lcs) & 0xFF != 0:
        raise FrameError(
            f"length checksum failed: LEN=0x{length:02X} LCS=0x{lcs:02X}"
        )

    # An empty-data frame (LEN 0) is an error frame; nothing to strip.
    if length == 0:
        raise FrameError("zero-length information frame (error frame)")

    data_start = p + 2
    data_end = data_start + length  # data field = TFI + payload, LEN bytes
    if data_end + 1 > len(raw):
        raise FrameError("frame truncated before data checksum")

    data = raw[data_start:data_end]  # includes TFI as data[0]
    dcs = raw[data_end]
    if (sum(data) + dcs) & 0xFF != 0:
        raise FrameError(
            f"data checksum failed: DCS=0x{dcs:02X} over {data.hex()}"
        )

    tfi = data[0]
    if tfi != PN532_TO_HOST:
        raise FrameError(f"unexpected TFI 0x{tfi:02X} (want 0x{PN532_TO_HOST:02X})")

    return bytes(data[1:])  # strip TFI, return response code + data
