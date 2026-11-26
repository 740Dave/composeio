"""
R503 UART command/response driver.

Built ONLY from the wire-protocol excerpt at
${DATASHEETS_HOME}/R503/docs/impl-test/fable-wire-excerpt.lts
(nodes CXC-R503-FRAMING, CXC-R503-CHECKSUM, CXC-R503-PACKET-256, CXC-R503-OPCODES,
CXC-R503-CODES, CXC-R503-UNSOLICITED-BYTE, CXC-R503-ACK-STREAM, CXC-R503-STREAM-BOUND,
CXC-R503-DATA-BURSTS, CXC-R503-BUSY). No other spec, datasheet, or driver was read.

============================================================================
ASSUMPTIONS NOT GIVEN BY THE EXCERPT (every one is also flagged inline with
`# ASSUMED (not in excerpt): ...` at its point of use):

  A1. Transport blocking semantics. The excerpt only gives the transport shape as
      self.port.read(n) / self.port.write(b). It does not say whether read(n) blocks
      indefinitely, blocks with an internal timeout, or returns immediately with
      whatever is available. This driver assumes read(n) can return FEWER than n
      bytes (including b'') without hanging forever, and builds its own timeout/
      polling loop on top of that assumption. If the real transport blocks
      indefinitely on read(n), every timeout path here is unreachable.

  A2. Confirmation-code position. The excerpt documents confirmation-code VALUES
      (CXC-R503-CODES) but never states where the confirmation code sits inside an
      acknowledge packet's content. This driver assumes content[0] of a PID=0x07
      packet is the confirmation code and content[1:] is the data payload — a
      conventional placement, not one the excerpt states.

  A3. AutoEnroll command payload layout. The excerpt gives the opcode (0x31) and the
      ack-stream behaviour, but never gives the byte layout of the AutoEnroll command
      packet's content (e.g. finger ID field width/position, parameter/status-bit
      field width/position). This driver assumes a 2-byte finger_id + 2-byte
      param_flags payload, big-endian, with bit 0 of param_flags as the "status bit"
      referenced in CXC-R503-ACK-STREAM. This is invented, not sourced.

  A4. Final-ack fingerID field position. CXC-R503-ACK-STREAM says "only the final
      packet's fingerID field is meaningful" but never says where in that packet's
      content the fingerID lives. This driver assumes the last 2 bytes of the final
      ack's data.

  A5. Per-step ack correlation. The excerpt says AutoEnroll emits 15 acks for "steps
      0x01 through 0x0F" but does not say whether/where a step number appears inside
      each ack's content. This driver does not attempt to parse a step number out of
      the ack content — it only counts packets 1..15 by arrival order — because
      inventing a byte offset for it would be a silent fabrication.

  A6. ReadSysPara response layout for the Busy flag. CXC-R503-BUSY states a Busy flag
      exists at "system status bit 0, via ReadSysPara" but the excerpt gives no field
      layout for ReadSysPara's ack content. This driver assumes ack.data[0:2] is a
      big-endian status register whose bit 0 is Busy. Every other field in that
      response is unknown and not parsed.

  A7. Drain-loop non-blocking read. _drain_rx() calls self.port.read(1) in a loop and
      treats an empty return as "nothing more pending" (see A1). If the transport
      instead blocks on an empty RX buffer, _drain_rx() will hang; the excerpt gives
      no way to tell the difference from the byte-transport shape alone.

  A8. Busy-while-command policy. CXC-R503-BUSY says explicitly that this is
      UNDEFINED in every revision of the spec ("addressed nowhere"). This driver
      therefore does NOT attempt to reproduce device behaviour for it (there is none
      documented to reproduce). Instead it enforces a conservative SOFTWARE-side
      policy: refuse to issue a new command while this driver instance believes a
      multi-packet exchange (an ack stream or a data burst) is still in flight,
      raising R503BusyError, unless the caller explicitly overrides. This is a
      driver-authored safety choice, not a spec-derived behaviour, and is called
      out again at the BUSY hazard's implementation site below.

  A9. Incoming-address handling. The excerpt states that a WRONG address on an
      OUTGOING command causes the device to go silent, but says nothing about what a
      driver should do if an INCOMING frame's address field doesn't match what was
      sent. This driver records the address it saw and does not reject frames on a
      mismatch — treating that policy question as outside the excerpt's scope.

  A10. AutoEnroll's Config3-vs-Config1 prerequisite (CXC-R503-OPCODES /
      CXC-R503-ACK-STREAM config-index-trap) is a DEVICE-SIDE system-parameter
      prerequisite (set via SetSysPara before calling AutoEnroll), not part of the
      AutoEnroll command payload itself, and the excerpt gives no SetSysPara payload
      layout either. This driver does not implement a config-setting helper; it only
      documents the trap at the AutoEnroll/AutoIdentify call sites so a caller does
      not generalize the index across the two commands.
============================================================================
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, List


# ---------------------------------------------------------------------------
# Constants drawn directly from the excerpt (CXC-R503-FRAMING, CXC-R503-OPCODES,
# CXC-R503-CODES, CXC-R503-UNSOLICITED-BYTE)
# ---------------------------------------------------------------------------

HEADER = b"\xEF\x01"                 # CXC-R503-FRAMING: header 2B 0xEF01
DEFAULT_ADDRESS = 0xFFFFFFFF         # CXC-R503-FRAMING: address-gating default

PID_COMMAND = 0x01
PID_DATA = 0x02
PID_ACK = 0x07
PID_END_OF_DATA = 0x08

UNSOLICITED_BYTE = 0x55              # CXC-R503-UNSOLICITED-BYTE

# CXC-R503-OPCODES core-set (only the ones this driver actually issues get named
# constants; the rest of the documented set is kept in OPCODES for completeness).
OPCODES = {
    "GetImg": 0x01,
    "GenChar": 0x02,
    "Match": 0x03,
    "Search": 0x04,
    "RegModel": 0x05,
    "Store": 0x06,
    "LoadChar": 0x07,
    "UpChar": 0x08,
    "DownChar": 0x09,
    "DeletChar": 0x0C,
    "Empty": 0x0D,
    "SetSysPara": 0x0E,
    "ReadSysPara": 0x0F,
    "TempleteNum": 0x1D,
    "ReadIndexTable": 0x1F,
    "GetImageEx": 0x28,
    "Cancel": 0x30,
    "AutoEnroll": 0x31,
    "AutoIdentify": 0x32,
    "AuraLedConfig": 0x35,
    "CheckSensor": 0x36,
    "GetAlgVer": 0x39,
    "GetFwVer": 0x3A,
    "ReadProdInfo": 0x3C,
    "SoftRst": 0x3D,
    "HandShake": 0x40,
    # correction-handshake (CXC-R503-OPCODES): HandShake is 0x40, NOT 0x53 — two of
    # six surveyed drivers ship 0x53 wrongly. 0x53 is deliberately absent from this
    # table (see correction-nonexistent: 0x1B, 0x33, 0x50-0x53 do not exist on R503).
}

# CXC-R503-CODES: documented confirmation codes, load-bearing subset only (the
# excerpt says "35 codes in V1.4.1" but lists only these by value).
CONFIRMATION_CODES = {
    0x00: "complete",
    0x01: "receive error",
    0x02: "no finger",
    0x09: "no match",
    0x1F: "library full",
    0x26: "timeout",
    0x27: "already exists",
    0xFC: "unsupported",
    0xFD: "hardware error",
    0xFE: "execution failure",
    # 0x28 is documented in the excerpt as undocumented-in-use: returned by the
    # vendor's own worked example but present in no revision's table. Included here
    # ONLY as a note, not as a resolved meaning — CXC-R503-CODES's rule is that an
    # unlisted code must be surfaced, never fatal, never remapped, so it is
    # deliberately NOT added as a key below; see _decode_confirmation_code().
}

# CXC-R503-ACK-STREAM: steps 0x01 through 0x0F -> 15 acknowledge packets.
AUTOENROLL_STEP_COUNT = 15

# CXC-R503-ACK-STREAM timeout-contradiction: 10s in prose, 15s in the flowchart, for
# both AutoEnroll and AutoIdentify's no-finger exit, across three revisions.
# Containment invariant inv-7 (named in the excerpt, not re-derived here): take the
# reading that fails later, and record the other value for audit.
NO_FINGER_TIMEOUT_S = 15.0            # the reading actually used (fails later)
NO_FINGER_TIMEOUT_PROSE_S = 10.0      # the reading NOT used; retained per inv-7

DEFAULT_TIMEOUT_S = 5.0               # ASSUMED (not in excerpt): a generic per-frame
                                       # read timeout for ordinary (non-no-finger)
                                       # command/ack exchanges. The excerpt gives no
                                       # general command-timeout value anywhere.


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class R503ProtocolError(Exception):
    """Raised for any framing/parsing violation of CXC-R503-FRAMING."""


class R503ChecksumError(R503ProtocolError):
    """CXC-R503-CHECKSUM: checksum mismatch. Per the excerpt's obligation, a packet
    whose checksum does not verify must never have its payload acted on."""


class R503Timeout(R503ProtocolError):
    """No complete frame arrived within the allotted window."""


class R503BusyError(R503ProtocolError):
    """Driver-side guard against issuing a command while this driver believes a
    multi-packet exchange is in flight. See ASSUMPTION A8 / CXC-R503-BUSY: the
    excerpt says device behaviour here is UNDEFINED, so this is a software policy,
    not a reproduction of documented device behaviour."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class R503Packet:
    pid: int
    address: int
    content: bytes  # payload only; checksum bytes are NOT included here


@dataclass
class R503Ack:
    code: int
    meaning: str
    data: bytes
    raw: R503Packet


@dataclass
class R503EnrollResult:
    steps: List[R503Ack]
    final_confirmation: int
    finger_id: Optional[bytes]  # ASSUMED field position — see ASSUMPTION A4


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class R503UartDriver:
    """
    UART command/response driver for the R503 fingerprint module, built strictly
    from fable-wire-excerpt.lts. See the module docstring for the full assumption
    list; every assumption is repeated inline at its point of use below.

    Transport contract (as given by the task, not the excerpt): self.port.read(n)
    and self.port.write(b) on a byte-oriented port object.
    """

    def __init__(self, port, address: int = DEFAULT_ADDRESS,
                 default_timeout: float = DEFAULT_TIMEOUT_S):
        self.port = port
        self.address = address
        self.default_timeout = default_timeout
        # Driver-side "an exchange is still open" guard — see ASSUMPTION A8.
        self._in_flight = False

    # -- low-level byte I/O ------------------------------------------------

    def _read_exact(self, n: int, timeout: float) -> bytes:
        """Read exactly n bytes or raise R503Timeout. See ASSUMPTION A1: assumes
        self.port.read(k) can return fewer than k bytes (including b'') without
        blocking forever, which is what makes a timeout loop possible at all."""
        if n == 0:
            return b""
        deadline = time.monotonic() + timeout
        buf = bytearray()
        while len(buf) < n:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise R503Timeout(
                    f"timed out reading {n} bytes (got {len(buf)}) after {timeout}s"
                )
            chunk = self.port.read(n - len(buf))
            if chunk:
                buf += chunk
            else:
                # ASSUMED (not in excerpt): a short sleep is safe/appropriate between
                # empty reads; the excerpt gives no polling-interval guidance.
                time.sleep(0.001)
        return bytes(buf)

    def _drain_rx(self) -> None:
        """CXC-R503-UNSOLICITED-BYTE rule: 'drain RX immediately before every
        transmit AND parse with a sliding two-byte header window. Either alone is
        partial.' This is the drain half; _read_frame() below is the sliding-window
        half. Together they implement containment invariant inv-4."""
        while True:
            # ASSUMED (not in excerpt): self.port.read(1) returns b'' promptly when
            # nothing is pending, rather than blocking — see ASSUMPTION A7.
            b = self.port.read(1)
            if not b:
                break

    # -- framing (CXC-R503-FRAMING / CXC-R503-CHECKSUM) ---------------------

    @staticmethod
    def _checksum(pid: int, length_bytes: bytes, content: bytes) -> int:
        """CXC-R503-CHECKSUM: 'arithmetic sum of PID, both length bytes and all
        content bytes, modulo 2^16. Header and address are NOT included.'"""
        total = pid + length_bytes[0] + length_bytes[1] + sum(content)
        return total % 0x10000

    def _build_packet(self, pid: int, content: bytes,
                       address: Optional[int] = None) -> bytes:
        if address is None:
            address = self.address
        # CXC-R503-FRAMING length-semantics: "length counts the content PLUS the two
        # checksum bytes" — NOT content length alone (that misreading overruns by
        # exactly two bytes on every packet, per the excerpt's own warning).
        length = len(content) + 2
        length_bytes = length.to_bytes(2, "big")
        checksum = self._checksum(pid, length_bytes, content)
        checksum_bytes = checksum.to_bytes(2, "big")
        return (
            HEADER
            + address.to_bytes(4, "big")
            + bytes([pid])
            + length_bytes
            + content
            + checksum_bytes
        )

    def _read_frame(self, timeout: float) -> R503Packet:
        """Sliding two-byte header window, per CXC-R503-UNSOLICITED-BYTE's rule.
        This is what lets the driver self-heal from the bare 0x55 (or any other
        stray byte) appearing ANYWHERE in the stream, not just at boot — the
        excerpt specifically warns that a driver handling the unsolicited byte
        'only at boot desyncs on its first soft reset'; scanning for the header on
        every read (rather than trusting fixed offsets) is how this driver avoids
        that failure mode."""
        deadline = time.monotonic() + timeout
        window = bytearray()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise R503Timeout("timed out scanning for 0xEF01 frame header")
            b = self._read_exact(1, remaining)
            window += b
            if len(window) > 2:
                del window[0]
            if len(window) == 2 and window[0] == 0xEF and window[1] == 0x01:
                break

        remaining = deadline - time.monotonic()
        address_bytes = self._read_exact(4, max(remaining, 0.0))
        remaining = deadline - time.monotonic()
        pid_byte = self._read_exact(1, max(remaining, 0.0))
        remaining = deadline - time.monotonic()
        length_bytes = self._read_exact(2, max(remaining, 0.0))
        length = int.from_bytes(length_bytes, "big")

        # CXC-R503-PACKET-256: "do not size a buffer or a state machine on the
        # assumption that 256-byte data packets are legal... the caller's buffer is
        # the binding check." This driver never pre-sizes a fixed buffer at all — it
        # reads exactly `length` bytes as declared by the wire, whatever that is —
        # so the 128-vs-256 contradiction cannot desync the parser either way.
        remaining = deadline - time.monotonic()
        body = self._read_exact(length, max(remaining, 0.0))

        content = body[:-2]
        checksum_recv = int.from_bytes(body[-2:], "big")
        checksum_calc = self._checksum(pid_byte[0], length_bytes, content)
        if checksum_calc != checksum_recv:
            # CXC-R503-CHECKSUM obligation: "every packet's checksum is verified
            # before its payload is acted on" — raise BEFORE returning the packet,
            # so no caller can act on unverified content.
            raise R503ChecksumError(
                f"checksum mismatch: got 0x{checksum_recv:04x}, "
                f"computed 0x{checksum_calc:04x}"
            )

        pkt_address = int.from_bytes(address_bytes, "big")
        # ASSUMPTION A9: excerpt does not define policy for an incoming address that
        # doesn't match self.address; we record it rather than rejecting the frame.
        return R503Packet(pid=pid_byte[0], address=pkt_address, content=bytes(content))

    # -- confirmation-code decoding (CXC-R503-CODES) ------------------------

    @staticmethod
    def _decode_confirmation_code(code: int) -> str:
        # CXC-R503-CODES rule: "an unlisted confirmation code is surfaced, never
        # fatal, and never mapped onto the nearest documented one." 0x28 in
        # particular is named in the excerpt as undocumented-in-use; it falls
        # through to the same UNKNOWN path as any other unlisted code below,
        # deliberately, rather than being hardcoded to a guessed meaning.
        if code in CONFIRMATION_CODES:
            return CONFIRMATION_CODES[code]
        return f"UNKNOWN(0x{code:02x})"

    # -- command/ack primitives ---------------------------------------------

    def send_command(self, opcode: int, payload: bytes = b"",
                      address: Optional[int] = None,
                      allow_while_busy: bool = False) -> None:
        """Send a PID=0x01 command packet: content = opcode byte + payload."""
        if self._in_flight and not allow_while_busy:
            # ASSUMPTION A8 / CXC-R503-BUSY: the excerpt says device behaviour on a
            # command-while-busy is undefined in every revision. We do not attempt
            # to test or reproduce undocumented device behaviour; we refuse on the
            # driver side instead.
            raise R503BusyError(
                "refusing to send: a prior multi-packet exchange (ack stream or "
                "data burst) is still in flight on this driver instance. "
                "CXC-R503-BUSY: the excerpt does not define device behaviour for "
                "a command arriving while Busy (queued/rejected/dropped is "
                "unspecified), so this driver will not exercise that undefined "
                "path silently. Pass allow_while_busy=True to override, or call "
                "cancel() first."
            )
        # CXC-R503-UNSOLICITED-BYTE rule, drain half: "drain RX immediately before
        # every transmit."
        self._drain_rx()
        content = bytes([opcode]) + payload
        packet = self._build_packet(PID_COMMAND, content, address)
        self.port.write(packet)

    def read_ack(self, timeout: Optional[float] = None) -> R503Ack:
        """Read one PID=0x07 acknowledge packet."""
        pkt = self._read_frame(timeout if timeout is not None else self.default_timeout)
        if pkt.pid != PID_ACK:
            raise R503ProtocolError(
                f"expected ack (pid 0x{PID_ACK:02x}), got pid 0x{pkt.pid:02x}"
            )
        if not pkt.content:
            raise R503ProtocolError("ack packet carries no content to read a "
                                     "confirmation code from")
        # ASSUMPTION A2: confirmation code assumed at content[0]; excerpt never
        # states the field's position within the ack packet.
        code = pkt.content[0]
        data = pkt.content[1:]
        meaning = self._decode_confirmation_code(code)
        return R503Ack(code=code, meaning=meaning, data=data, raw=pkt)

    # -- basic command wrappers ----------------------------------------------

    def handshake(self) -> R503Ack:
        self.send_command(OPCODES["HandShake"])
        return self.read_ack()

    def cancel(self) -> R503Ack:
        """Send Cancel (0x30). CXC-R503-BUSY names Cancel as the only documented
        tool that exists alongside the undefined busy behaviour, but the excerpt
        does not describe what Cancel actually does to an in-flight exchange in
        detail — used here only as an explicit abort path, clearing the driver's
        own in-flight guard, not as a proven fix for the busy hazard."""
        self.send_command(OPCODES["Cancel"], allow_while_busy=True)
        ack = self.read_ack()
        self._in_flight = False
        return ack

    def soft_reset(self) -> R503Ack:
        """CXC-R503-UNSOLICITED-BYTE: the module re-emits the bare 0x55 'again after
        every SoftRst.' We do not special-case where that byte lands relative to the
        SoftRst ack (the excerpt doesn't say) — the sliding-window frame reader in
        _read_frame() already tolerates a stray byte before or interleaved with a
        real frame, and we drain again afterward for whatever follows."""
        # ASSUMED (not in excerpt): SoftRst still yields a normal ack packet in
        # addition to the unsolicited 0x55; the excerpt describes the 0x55 emission
        # but not whether a conventional ack also accompanies this specific command.
        self.send_command(OPCODES["SoftRst"])
        ack = self.read_ack()
        self._drain_rx()  # discard the unsolicited 0x55 (and anything else pending)
        self._in_flight = False
        return ack

    def read_system_busy(self) -> bool:
        """CXC-R503-BUSY: 'a Busy flag (system status bit 0, via ReadSysPara)'."""
        self.send_command(OPCODES["ReadSysPara"])
        ack = self.read_ack()
        # ASSUMPTION A6: excerpt gives no field layout for ReadSysPara's response
        # beyond "system status bit 0"; we assume a 2-byte big-endian status
        # register as the first field after the confirmation code.
        if len(ack.data) < 2:
            raise R503ProtocolError(
                "ReadSysPara ack too short to read a status register from "
                "(ASSUMED 2-byte status register layout not met)"
            )
        status_register = int.from_bytes(ack.data[0:2], "big")
        return bool(status_register & 0x1)

    # -- AutoEnroll flow (CXC-R503-ACK-STREAM) --------------------------------

    def auto_enroll(self, finger_id: int, param_flags: int = 0x0001) -> R503EnrollResult:
        """
        Run the AutoEnroll (0x31) enrollment flow: one command, then a stream of
        15 acknowledge packets (steps 0x01-0x0F per CXC-R503-ACK-STREAM), of which
        only the final packet's fingerID field is meaningful.

        Prerequisite the caller must satisfy OUTSIDE this method (CXC-R503-ACK-
        STREAM config-index-trap, ASSUMPTION A10): AutoEnroll is enabled via
        device parameter Config3 — NOT Config1, which is AutoIdentify's enable
        parameter. This driver does not set that parameter (no SetSysPara payload
        layout is given in the excerpt); mixing the index up "silently
        misconfigures the sibling command" per the excerpt's own wording.

        fleet-status note (not a driver concern, kept for the record): the excerpt
        states the packaged AutoEnroll path is NOT USED in production by a separate
        ruling (its mandated on-module LED sequence is unimplementable under
        device-owned ring). Implemented here regardless, because this test is about
        the wire-protocol stream behaviour, which the excerpt says is retained
        precisely because the trap "survives for any future reader."

        param_flags default of 0x0001: ASSUMED (not in excerpt) to set the "status
        bit" CXC-R503-ACK-STREAM refers to ("with its status bit set, one AutoEnroll
        command emits FIFTEEN acknowledge packets") — bit position within the
        parameter word is invented (see ASSUMPTION A3), not sourced.
        """
        if self._in_flight:
            raise R503BusyError("cannot start auto_enroll: another exchange is "
                                 "already in flight on this driver instance")

        # ASSUMPTION A3: AutoEnroll command payload layout invented as
        # finger_id (2B BE) + param_flags (2B BE); the excerpt gives neither field
        # width nor position.
        payload = finger_id.to_bytes(2, "big") + param_flags.to_bytes(2, "big")
        self.send_command(OPCODES["AutoEnroll"], payload)
        self._in_flight = True

        acks: List[R503Ack] = []
        try:
            for _step in range(1, AUTOENROLL_STEP_COUNT + 1):
                # CXC-R503-ACK-STREAM timeout-contradiction: use the reading that
                # fails later (15s), record the other (10s) only in this comment/
                # the module-level constant, per inv-7. This applies per the
                # excerpt "for both commands" (AutoEnroll and AutoIdentify), and we
                # read it as applying per no-finger wait, i.e. per packet in the
                # stream, not once for the whole 15-packet exchange — the excerpt
                # does not disambiguate this either way; flagged as ambiguous
                # below in the final report, not silently resolved.
                ack = self.read_ack(timeout=NO_FINGER_TIMEOUT_S)
                acks.append(ack)
                # ASSUMPTION A5: no attempt is made to read a per-step number out
                # of ack.data; the excerpt names steps 0x01-0x0F but never gives
                # their position within each ack's content, and inventing one
                # would be a silent fabrication.

            final = acks[-1]
            # ASSUMPTION A4: fingerID field assumed to be the final ack's last 2
            # content bytes; excerpt never states the field's position.
            finger_id_out = final.data[-2:] if len(final.data) >= 2 else None
            return R503EnrollResult(
                steps=acks, final_confirmation=final.code, finger_id=finger_id_out
            )
        finally:
            self._in_flight = False

    def auto_identify(self, max_packets: int) -> List[R503Ack]:
        """
        Run AutoIdentify (0x32). Unlike auto_enroll(), this method REQUIRES an
        explicit max_packets cap with no default, because CXC-R503-STREAM-BOUND
        states (as an INFERENCE, not a directly-worked example) that with retries
        configured to 0 and a finger continuously present that never matches, the
        acknowledge stream is UNBOUNDED — "the no-finger timeout does not bound the
        finger-present-never-matches case." Silently defaulting a cap here would
        hide that hazard; the caller must decide it.

        config-index-trap reminder (CXC-R503-ACK-STREAM, ASSUMPTION A10):
        AutoIdentify's enable parameter is Config1 — NOT Config3, which is
        AutoEnroll's. This driver does not set that parameter either.
        """
        if self._in_flight:
            raise R503BusyError("cannot start auto_identify: another exchange is "
                                 "already in flight on this driver instance")
        if max_packets <= 0:
            raise ValueError("max_packets must be > 0; CXC-R503-STREAM-BOUND "
                              "documents this stream as potentially unbounded")

        # ASSUMED (not in excerpt): AutoIdentify's command payload layout. The
        # excerpt gives no field layout for AutoIdentify at all (only its opcode
        # and its enable-parameter index). Sent with an empty payload here rather
        # than guessing a shape with no basis whatsoever.
        self.send_command(OPCODES["AutoIdentify"], b"")
        self._in_flight = True
        acks: List[R503Ack] = []
        try:
            for _ in range(max_packets):
                ack = self.read_ack(timeout=NO_FINGER_TIMEOUT_S)
                acks.append(ack)
                # ASSUMED (not in excerpt): terminate early once a non-"no finger"
                # confirmation code is seen. The excerpt describes the manual as
                # illustrating "only the 3-packet success case" but does not give a
                # per-packet stop condition; stopping on any code other than
                # "no finger"/"timeout" is this driver's own inferred heuristic,
                # not a sourced rule.
                if ack.meaning not in ("no finger", "timeout"):
                    break
            return acks
        finally:
            self._in_flight = False

    # -- data-packet bursts (CXC-R503-DATA-BURSTS) ----------------------------

    def read_data_burst(self, expect_ack: bool = True,
                         timeout: Optional[float] = None):
        """
        Read an acknowledge (optional) followed by a burst of data packets for
        UpImage / DownImage / UpChar / DownChar / ReadInfPage.

        CXC-R503-DATA-BURSTS: "the packet COUNT depends on the negotiated
        packet-size parameter, not on the command; a driver terminates on the 0x08
        end-of-data PID or reads the configured size first." This driver takes the
        first option — terminate strictly on PID 0x08 — and deliberately does NOT
        scan a fixed buffer for 0xEF01 boundaries, which the excerpt names as a
        real driver hazard ("mis-splits on payload bytes that happen to be
        0xEF01"). Because _read_frame() reads exactly `length` bytes per the
        wire's own length field rather than scanning content for the next header,
        an 0xEF01 byte sequence inside a payload cannot be mistaken for a frame
        boundary here.

        Ambiguity carried forward, not resolved: the excerpt lists DownImage and
        DownChar in the SAME "return an acknowledge then several data packets"
        sentence as UpImage/UpChar, even though "Down*" conventionally means the
        host WRITES data to the device. The excerpt gives no host-to-device data
        packet write path; this method only reads, per what's actually stated.
        """
        if self._in_flight:
            raise R503BusyError("cannot start read_data_burst: another exchange "
                                 "is already in flight on this driver instance")
        self._in_flight = True
        try:
            t = timeout if timeout is not None else self.default_timeout
            ack = self.read_ack(t) if expect_ack else None
            packets: List[R503Packet] = []
            while True:
                pkt = self._read_frame(t)
                if pkt.pid == PID_END_OF_DATA:
                    packets.append(pkt)
                    break
                elif pkt.pid == PID_DATA:
                    packets.append(pkt)
                else:
                    raise R503ProtocolError(
                        f"unexpected pid 0x{pkt.pid:02x} during data burst "
                        f"(expected 0x{PID_DATA:02x} data or "
                        f"0x{PID_END_OF_DATA:02x} end-of-data)"
                    )
            return ack, packets
        finally:
            self._in_flight = False

    # -- boot / power-on handling (CXC-R503-UNSOLICITED-BYTE) -----------------

    def handle_power_on(self) -> None:
        """Call once after opening the port. CXC-R503-UNSOLICITED-BYTE: the module
        emits the bare 0x55 unsolicited at power-on. Since _drain_rx() is also run
        before every send_command(), this is a convenience/explicitness call, not
        the only place the byte is handled — 'a driver that handles it only at
        boot desyncs on its first soft reset' is exactly why soft_reset() above
        drains again too, and why _read_frame() uses a sliding window regardless."""
        self._drain_rx()
