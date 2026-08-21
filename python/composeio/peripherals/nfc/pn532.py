"""PN532 protocol driver over an injectable I2C transport.

Implements the subset of the PN532 command set needed to read an ISO14443A
tag UID (and, optionally, an NDEF text record):

    wakeup            -> nudge the reader out of low-power / power-on state
    SAMConfiguration  -> put the reader in normal mode (0x14)
    InListPassiveTarget (0x4A) -> detect one ISO14443A (106 kbps type-A)
                                  target and return its UID
    read_ndef_text    -> (optional) MIFARE-Ultralight page read + minimal
                         NDEF TLV / text-record decode

The driver is hardware-free: it depends only on the :class:`I2CTransport`
seam, so its protocol logic is verified against a mock on machines with no
PN532 attached. All framing/checksums live in :mod:`frames`.
"""

from __future__ import annotations

import logging

from . import frames
from .transport import I2CTransport

logger = logging.getLogger("composeio.peripherals.nfc.pn532")

# --- PN532 command bytes ----------------------------------------------------

_CMD_SAMCONFIGURATION = 0x14
_CMD_INLISTPASSIVETARGET = 0x4A
_CMD_INDATAEXCHANGE = 0x40

# ISO14443A @ 106 kbps type A
_BRTY_ISO14443A = 0x00

# Expected response codes (command byte + 1)
_RSP_SAMCONFIGURATION = 0x15
_RSP_INLISTPASSIVETARGET = 0x4B
_RSP_INDATAEXCHANGE = 0x41


class PN532Error(RuntimeError):
    """A PN532 command failed at the protocol level."""


class PN532:
    """Driver for an NXP PN532 over I2C.

    :param transport: any object satisfying :class:`I2CTransport`.
    :param read_retries: how many not-ready polls to tolerate per phase.
    """

    def __init__(self, transport: I2CTransport, read_retries: int = 20):
        self._i2c = transport
        self._read_retries = read_retries

    # --- low-level frame exchange ------------------------------------------

    def _wait_ready(self) -> bool:
        """Poll the I2C ready bit (bit0 of the leading status byte)."""
        for _ in range(self._read_retries):
            status = self._i2c.read(1)
            if status and (status[0] & 0x01):
                return True
        return False

    def _read_data(self, count: int) -> bytes:
        """Read *count* frame bytes, discarding the leading I2C status byte."""
        raw = self._i2c.read(count + 1)
        return bytes(raw[1:])

    def _call(self, payload: bytes, response_len: int) -> bytes:
        """Send a command payload and return the parsed response payload.

        *payload* is the command byte + params (no TFI). *response_len* is the
        full expected wire-frame length of the reply (generous is fine — the
        parser bounds itself by the LEN field).
        """
        self._i2c.write(frames.build_command_frame(payload))

        if not self._wait_ready():
            raise PN532Error("timed out waiting for ACK ready")
        ack = self._read_data(len(frames.ACK_FRAME))
        if not frames.is_ack(ack):
            raise PN532Error(f"expected ACK, got {ack.hex()}")

        if not self._wait_ready():
            raise PN532Error("timed out waiting for response ready")
        raw = self._read_data(response_len)
        return frames.parse_response_frame(raw)

    # --- high-level commands -----------------------------------------------

    def wakeup(self) -> None:
        """Nudge the reader awake.

        Over I2C the PN532 needs no HSU-style break; a benign SAMConfiguration
        both wakes it and leaves it in a known mode, so :meth:`begin` covers
        the wake path. This method is kept as an explicit no-op hook that a
        board profile can override if its wiring needs a hard reset toggle.
        """
        logger.debug("PN532 wakeup (I2C: handled by SAMConfiguration)")

    def sam_configuration(self) -> None:
        """Issue SAMConfiguration (normal mode, no SAM, ~1s timeout)."""
        # mode=0x01 normal, timeout=0x14 (50ms*20=1s), use_irq=0x01
        resp = self._call(
            bytes([_CMD_SAMCONFIGURATION, 0x01, 0x14, 0x01]),
            response_len=8,
        )
        if not resp or resp[0] != _RSP_SAMCONFIGURATION:
            raise PN532Error(f"SAMConfiguration failed: {resp.hex()}")

    def begin(self) -> None:
        """Bring the reader up: wakeup + SAMConfiguration."""
        self.wakeup()
        self.sam_configuration()

    def read_passive_target_uid(self) -> bytes | None:
        """Detect one ISO14443A target and return its UID bytes, or None.

        Returns the raw UID (e.g. ``b"\\x04\\xab\\xcd\\xef"``) when a tag is
        present; ``None`` when the reader reports zero targets.
        """
        resp = self._call(
            bytes([_CMD_INLISTPASSIVETARGET, 0x01, _BRTY_ISO14443A]),
            response_len=24,
        )
        # resp = 4B  NbTg  [Tg ATQA(2) SAK NFCIDLen NFCID...]
        if not resp or resp[0] != _RSP_INLISTPASSIVETARGET:
            raise PN532Error(f"InListPassiveTarget failed: {resp.hex()}")
        if len(resp) < 2:
            raise PN532Error("truncated InListPassiveTarget response")
        num_targets = resp[1]
        if num_targets == 0:
            return None
        # Single-target layout: resp[2]=Tg, [3:5]=ATQA, [5]=SAK, [6]=UIDLen
        if len(resp) < 7:
            raise PN532Error("target header truncated")
        uid_len = resp[6]
        uid = resp[7 : 7 + uid_len]
        if len(uid) != uid_len:
            raise PN532Error(
                f"UID truncated: want {uid_len} bytes, got {len(uid)}"
            )
        return bytes(uid)

    # --- optional NDEF text read (MIFARE Ultralight / NTAG) ----------------

    def _read_page(self, page: int) -> bytes:
        """InDataExchange READ (0x30) of a 4-byte page (16 bytes returned)."""
        resp = self._call(
            bytes([_CMD_INDATAEXCHANGE, 0x01, 0x30, page & 0xFF]),
            response_len=32,
        )
        if not resp or resp[0] != _RSP_INDATAEXCHANGE:
            raise PN532Error(f"InDataExchange failed: {resp.hex()}")
        # resp = 41 <status> <16 data bytes>
        status = resp[1] if len(resp) > 1 else 0xFF
        if status != 0x00:
            raise PN532Error(f"READ status 0x{status:02X}")
        return bytes(resp[2:18])

    def read_ndef_text(self, max_pages: int = 12) -> str | None:
        """Best-effort read of the first NDEF text record from a type-2 tag.

        Reads user memory starting at page 4, walks the NDEF TLV, and decodes a
        single Text record (TNF 0x01, type 'T'). Returns the UTF-8 text, or
        None if no text record is found. Optional — safe to skip if the board
        profile only needs uid+presence.
        """
        data = bytearray()
        # Type-2 user memory starts at page 4; each READ returns 4 pages.
        for page in range(4, 4 + max_pages, 4):
            try:
                data.extend(self._read_page(page))
            except PN532Error:
                break
        return _decode_ndef_text(bytes(data))


def _decode_ndef_text(mem: bytes) -> str | None:
    """Decode the first NDEF Text record from a type-2 TLV memory dump."""
    i = 0
    n = len(mem)
    while i < n:
        tlv_type = mem[i]
        if tlv_type == 0x00:  # NULL TLV, skip
            i += 1
            continue
        if tlv_type == 0xFE:  # Terminator TLV
            break
        if i + 1 >= n:
            break
        length = mem[i + 1]
        value = mem[i + 2 : i + 2 + length]
        if tlv_type == 0x03:  # NDEF Message TLV
            return _decode_ndef_message(value)
        i += 2 + length
    return None


def _decode_ndef_message(msg: bytes) -> str | None:
    """Decode the first short-record Text record in an NDEF message."""
    if len(msg) < 3:
        return None
    header = msg[0]
    tnf = header & 0x07
    type_len = msg[1]
    payload_len = msg[2]
    rec_type = msg[3 : 3 + type_len]
    payload = msg[3 + type_len : 3 + type_len + payload_len]
    if tnf == 0x01 and rec_type == b"T" and payload:
        status = payload[0]
        lang_len = status & 0x3F
        text = payload[1 + lang_len :]
        encoding = "utf-16" if (status & 0x80) else "utf-8"
        try:
            return text.decode(encoding)
        except UnicodeDecodeError:
            return None
    return None
