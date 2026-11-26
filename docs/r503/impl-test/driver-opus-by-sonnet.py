"""
R503UartDriver — UART command/response driver for the R503 fingerprint module.

Built ONLY from the wire-protocol excerpt at
    ${DATASHEETS_HOME}/R503/docs/impl-test/opus-wire-excerpt.lts
(nodes CXC-R503-FRAMING, CXC-R503-CHECKSUM, CXC-R503-UNSOLICITED-0X55,
 CXC-R503-ACK-STREAM-BREAKS-REQ-RESP, CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED).

Nothing outside that file was consulted. Anywhere the excerpt did not supply a
value or a rule, this module makes an explicit, narrow assumption and marks it
with a comment at the point of use:  # ASSUMED (not in excerpt): ...

======================================================================
ASSUMPTIONS INDEX (every one of these is also called out inline below)
======================================================================
A1. Transport timing. The excerpt gives no timeout/timing values at all (the
    "## Feedback and timing" section heading exists in the excerpt file but
    carries NO content beneath it — it is empty). self.port.read(n) is
    assumed to be a short-timeout call that returns whatever bytes are
    currently available (possibly b'', possibly fewer than n), i.e. a
    pyserial-style port pre-configured with its own read timeout. This
    driver has no clock of its own and cannot impose wall-clock timeouts;
    it bounds retries by ITERATION COUNT instead, not by time.

A2. Command content layout. The excerpt defines PID 01H = "command" but
    never states the byte layout of a command's content. This driver
    assumes content = [opcode:1 byte][opcode-specific parameters...], with
    the opcode as the first content byte. The three opcodes the excerpt
    actually gives are used (AutoEnroll 0x31, AutoIdentify 0x32,
    SoftRst 0x3D); no other opcode is invented.

A3. AutoEnroll/AutoIdentify parameter layout. The excerpt names "Config3"
    (AutoEnroll) and "Config1" (AutoIdentify) as the status-reporting
    enable bit's index, but never defines what a "Config" slot is in
    terms of byte offset, nor where an enrollment ID number goes. This
    driver ASSUMES: content = [opcode][ID:2 bytes BE][Config1][Config2]
    [Config3][Config4][...extra params], i.e. Config1..Config4 are four
    sequential 1-byte fields immediately following a 2-byte ID, numbered
    in the obvious order. This is inference from the names "Config1" /
    "Config3", not a stated layout.

A4. Config "enable" encoding. The excerpt says a config bit "enables"
    streaming but never gives the byte VALUE that means "set". Assumed:
    0x01 = enabled, 0x00 = disabled, in the relevant Config byte.

A5. Acknowledge-packet content layout. The excerpt says AutoEnroll's acks
    carry "step" codes 0x01-0x0F and that only 0x0F carries the modelID,
    and that streams terminate "by confirmation != 0x00 or by the final
    step code" — which tells us an ack's content carries BOTH a
    confirmation code and a step code, but never gives their byte order
    or width, nor the width/encoding of modelID. Assumed: content =
    [confirmation:1][step:1][payload:remaining bytes], and for step
    0x0F the entire remaining payload IS the modelID, returned as raw
    bytes (this driver does not guess a width for it).

A6. Address-echo on responses. The excerpt states a WRONG outbound address
    gets silence (line: "a wrong address makes the module reject the
    transfer with SILENCE"). It never states what address field the
    module puts in its OWN response frames. Assumed: the module echoes
    the host's configured address back in every response frame, and this
    driver validates that echo; a mismatch is treated as a protocol
    error (not modeled on any stated device behaviour, since none is
    given for the inbound direction).

A7. "Busy" behaviour / confirmation code. Nothing in the excerpt defines a
    device-reported "busy" status or confirmation-code value. The closest
    thing the excerpt gives is the ack-stream rule: while AutoEnroll or
    AutoIdentify's multi-packet stream is outstanding, the module is
    understood to be mid-operation, and "a one-response-per-command
    transaction assumption is wrong for these commands." This driver
    therefore implements busy-handling purely as a HOST-SIDE reentrancy
    guard (refuse to issue a new command while a stream is open) — this
    is a driver-side safety measure, not a decoding of any device signal,
    because the excerpt supplies none.

A8. Data-packet burst bound. The excerpt's framing node establishes the
    PIDs (02H data, 08H end-of-data) and the rule "a data packet must
    follow a command or acknowledge packet, never appear alone" — but
    there is no node in this excerpt describing an RX-buffer-sized data
    burst, and no numeric bound anywhere. MAX_DATA_PACKETS below is an
    arbitrary driver-side safety cap, not a spec value.

A9. AutoIdentify ack-stream bound. The excerpt states the nominal count is
    three acks, but ALSO states (CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED)
    that the stream can be unbounded and that "the HAL match operation
    must impose its own bound" — without giving a number. DEFAULT_MAX_
    AUTOIDENTIFY_ACKS below is an arbitrary driver-chosen cap, flagged
    as such at its definition.

A10. SoftRst's own response framing. The excerpt says SoftRst (0x3D)
    causes the unframed 0x55 to fire "again after 0x3D SoftRst" but never
    states whether 0x3D ALSO gets a normal framed acknowledge (PID 07H)
    packet before/after the 0x55. Assumed: no framed ack is expected for
    SoftRst — soft_reset() only re-syncs the receiver past the 0x55.

A11. Confirmation-code enumeration. Only 0x00 = success is given (via "!=
    0x00" language). No other confirmation-code values are enumerated
    anywhere in the excerpt. This driver never decodes a nonzero code's
    meaning; it is returned to the caller as a raw int.
======================================================================
"""

import struct


# ---------------------------------------------------------------------------
# Constants taken DIRECTLY from the excerpt (CXC-R503-FRAMING)
# ---------------------------------------------------------------------------

HEADER = b"\xEF\x01"                 # [CXC-R503-FRAMING] shape: Header 2B = 0xEF01
DEFAULT_ADDRESS = 0xFFFFFFFF         # [CXC-R503-FRAMING] address: default 0xFFFFFFFF

PID_COMMAND = 0x01                   # [CXC-R503-FRAMING] pid: 01H command
PID_DATA = 0x02                      # [CXC-R503-FRAMING] pid: 02H data
PID_ACKNOWLEDGE = 0x07               # [CXC-R503-FRAMING] pid: 07H acknowledge
PID_END_OF_DATA = 0x08               # [CXC-R503-FRAMING] pid: 08H end-of-data

OPCODE_AUTO_ENROLL = 0x31            # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] 0x31 AutoEnroll
OPCODE_AUTO_IDENTIFY = 0x32          # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] 0x32 AutoIdentify
OPCODE_SOFT_RESET = 0x3D             # [CXC-R503-UNSOLICITED-0X55] 0x3D SoftRst

UNSOLICITED_BYTE = 0x55              # [CXC-R503-UNSOLICITED-0X55]

AUTO_ENROLL_STEP_COUNT = 15          # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] steps 0x01..0x0F
AUTO_ENROLL_FINAL_STEP = 0x0F        # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] "only 0x0F carrying the modelID"
AUTO_IDENTIFY_NOMINAL_ACK_COUNT = 3  # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] "0x32 AutoIdentify emits three"

# ASSUMED (not in excerpt) — see A9: no numeric bound is given for the
# AutoIdentify stream even though the excerpt REQUIRES the driver to impose
# one. Chosen generously above the nominal count of 3 so normal operation
# never trips it, while still being finite.
DEFAULT_MAX_AUTOIDENTIFY_ACKS = 32

# ASSUMED (not in excerpt) — see A8: no data-burst count or RX-buffer-sizing
# number is given anywhere in this excerpt.
MAX_DATA_PACKETS = 256

# ASSUMED (not in excerpt) — see A1: no timing values are given; these are
# iteration bounds, not wall-clock timeouts, because no clock is available.
MAX_SYNC_ATTEMPTS = 4096   # bytes scanned looking for the 0xEF01 header
MAX_DRAIN_READS = 64       # loop iterations for a "drain whatever is pending" pass


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class R503Error(Exception):
    """Base class for all driver-raised errors."""


class R503TimeoutError(R503Error):
    """The transport stopped producing bytes before a full frame arrived.

    Per [CXC-R503-FRAMING] a wrong address gets SILENCE, not an error
    packet — so a timeout here may legitimately mean "wrong/unaddressed
    module", not just "no module". The excerpt does not distinguish the
    two cases at the wire level, so this driver cannot either.
    """


class R503ChecksumError(R503Error):
    """[CXC-R503-CHECKSUM] recomputed checksum did not match the frame's."""


class R503ProtocolDesyncError(R503Error):
    """A framing/PID/bound invariant was violated.

    Raised for: header not found within MAX_SYNC_ATTEMPTS, an unexpected
    PID where a specific one was required, a data packet arriving without
    a preceding command/acknowledge [CXC-R503-FRAMING], an ack stream
    that runs past its bound without terminating (the exact failure mode
    [CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED] instructs the driver to
    surface instead of blocking forever).
    """


class R503AddressMismatchError(R503Error):
    """A response frame's address did not match this driver's configured
    address. See A6 — the excerpt does not define inbound address-echo
    behaviour; this is a driver-side sanity check on top of an assumption.
    """


class R503BusyError(R503Error):
    """A new command was issued while a multi-packet ack stream (AutoEnroll
    / AutoIdentify) was still open. See A7 — this is a host-side
    reentrancy guard, not a decode of any device-reported busy status,
    because the excerpt defines no such status.
    """


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class R503UartDriver:
    """UART command/response driver for one R503 module.

    `port` is any object exposing:
        port.read(n)  -> bytes, up to n bytes, short-timeout / non-blocking-ish
                          (ASSUMED — see A1)
        port.write(b) -> None or int, writes bytes to the wire
    """

    def __init__(self, port, address=DEFAULT_ADDRESS):
        self.port = port
        self.address = address & 0xFFFFFFFF
        self._busy = False  # see A7 / R503BusyError

    # -- framing: build -----------------------------------------------------

    def _checksum(self, pid, length_bytes, content):
        """[CXC-R503-CHECKSUM] sum of PID, both Length bytes, and every
        content byte, modulo 2**16. Header and address are NOT included.
        Verified against the excerpt's worked example:
            EF01 FFFFFFFF 01 0007 35 01 50 06 00  ->  0x0094
        """
        total = pid + length_bytes[0] + length_bytes[1] + sum(content)
        return total & 0xFFFF

    def _build_frame(self, pid, content):
        # [CXC-R503-FRAMING] length-semantics: Length = content bytes + 2
        # (it includes the checksum).
        length = len(content) + 2
        length_bytes = struct.pack(">H", length)
        checksum = self._checksum(pid, length_bytes, content)
        return (
            HEADER
            + struct.pack(">I", self.address)
            + bytes([pid])
            + length_bytes
            + bytes(content)
            + struct.pack(">H", checksum)
        )

    # -- transport helpers ----------------------------------------------

    def _read_exact(self, n):
        """Accumulate exactly n bytes from the transport.

        # ASSUMED (not in excerpt) — see A1: no timeout value is given.
        Bounded by iteration count, not wall-clock time, since no clock
        primitive is available on this transport.
        """
        buf = b""
        attempts = 0
        max_attempts = max(MAX_DRAIN_READS, n)
        while len(buf) < n:
            chunk = self.port.read(n - len(buf))
            if chunk:
                buf += chunk
            else:
                attempts += 1
                if attempts > max_attempts:
                    raise R503TimeoutError(
                        "transport produced no bytes; wanted %d, got %d "
                        "(module silent — see [CXC-R503-FRAMING] wrong-"
                        "address-is-silence; this driver cannot distinguish "
                        "'no module' from 'wrong address' at the wire level)"
                        % (n, len(buf))
                    )
        return buf

    def _drain_rx(self):
        """[CXC-R503-UNSOLICITED-0X55] remedy: "drain RX immediately before
        every transmit ... to absorb the power-on byte, the post-SoftRst
        byte, and any stale packet from an aborted transaction."
        """
        for _ in range(MAX_DRAIN_READS):
            chunk = self.port.read(64)
            if not chunk:
                return
        # ASSUMED (not in excerpt): if the transport never goes quiet within
        # MAX_DRAIN_READS iterations we stop anyway rather than looping
        # forever, since no timing bound is given to decide "truly done".

    def _sync_to_header(self):
        """[CXC-R503-UNSOLICITED-0X55] remedy: "parse with a sliding two-byte
        header window" — together with _drain_rx(), this absorbs the
        power-on 0x55, the post-SoftRst 0x55, and any stale bytes, without
        requiring the parser to know in advance how many stray bytes
        precede a real frame.
        """
        window = self._read_exact(2)
        scanned = 0
        while window != HEADER:
            window = window[1:] + self._read_exact(1)
            scanned += 1
            if scanned > MAX_SYNC_ATTEMPTS:
                raise R503ProtocolDesyncError(
                    "no 0xEF01 header found within %d bytes scanned"
                    % MAX_SYNC_ATTEMPTS
                )
        return

    def _read_frame(self):
        """Read one complete, checksum-verified frame after the header.

        Returns (pid, content) — content is the payload EXCLUDING the
        checksum (per [CXC-R503-FRAMING] length-semantics, Length counts
        the checksum, so content length = Length - 2).
        """
        self._sync_to_header()
        addr_bytes = self._read_exact(4)
        addr = struct.unpack(">I", addr_bytes)[0]
        pid = self._read_exact(1)[0]
        length_bytes = self._read_exact(2)
        length = struct.unpack(">H", length_bytes)[0]
        content_length = length - 2  # Length includes the 2 checksum bytes
        if content_length < 0:
            raise R503ProtocolDesyncError(
                "Length field %d is smaller than the mandatory 2 checksum "
                "bytes" % length
            )
        content = self._read_exact(content_length)
        checksum_bytes = self._read_exact(2)
        received_checksum = struct.unpack(">H", checksum_bytes)[0]

        expected_checksum = self._checksum(pid, length_bytes, content)
        if received_checksum != expected_checksum:
            raise R503ChecksumError(
                "checksum mismatch: got 0x%04X, expected 0x%04X"
                % (received_checksum, expected_checksum)
            )

        # A6 (ASSUMED, not in excerpt): validate the response echoes our
        # configured address. The excerpt never states this for inbound
        # frames — only that a WRONG outbound address gets silence.
        if addr != self.address:
            raise R503AddressMismatchError(
                "response address 0x%08X != configured address 0x%08X"
                % (addr, self.address)
            )

        return pid, content

    # -- packet send ------------------------------------------------------

    def _send_command_packet(self, content):
        if self._busy:
            # A7 (ASSUMED, not in excerpt): host-side reentrancy guard —
            # no device "busy" status is defined in the excerpt to check
            # against instead.
            raise R503BusyError(
                "a command was issued while a multi-packet ack stream "
                "(AutoEnroll/AutoIdentify) was still open"
            )
        self._drain_rx()  # [CXC-R503-UNSOLICITED-0X55] remedy: drain before every transmit
        frame = self._build_frame(PID_COMMAND, content)
        self.port.write(frame)

    # -- low-level command/response ---------------------------------------

    def send_command(self, opcode, params=b""):
        """Send a single command packet: content = [opcode] + params.

        # ASSUMED (not in excerpt) — see A2: the excerpt never states that
        # the first content byte of a command packet IS an opcode byte;
        # only that PID 01H packets are "command" packets. This layout is
        # inferred from usage (there is no other place for AutoEnroll's
        # 0x31 / AutoIdentify's 0x32 / SoftRst's 0x3D to live).
        """
        content = bytes([opcode]) + bytes(params)
        self._send_command_packet(content)

    def read_ack(self):
        """Read one PID=07H acknowledge frame and split it per A5:

        # ASSUMED (not in excerpt) — see A5: content = [confirmation:1]
        # [step:1][payload:...]. The excerpt establishes THAT acks carry a
        # confirmation code and a step code, never their byte order/width.
        """
        pid, content = self._read_frame()
        if pid != PID_ACKNOWLEDGE:
            raise R503ProtocolDesyncError(
                "expected acknowledge (PID 0x07), got PID 0x%02X" % pid
            )
        if len(content) < 2:
            raise R503ProtocolDesyncError(
                "acknowledge content too short (%d bytes) to hold the "
                "assumed [confirmation][step] layout" % len(content)
            )
        confirmation = content[0]
        step = content[1]
        payload = content[2:]
        return confirmation, step, payload

    def read_data_burst(self):
        """Read a burst of PID=02H data packets terminated by a PID=08H
        end-of-data packet.

        [CXC-R503-FRAMING] gives the PIDs and the rule "a data packet must
        follow a command or acknowledge packet, never appear alone" — but
        this excerpt has NO node describing the RX-buffer-sized burst
        count the task description mentions. MAX_DATA_PACKETS (see A8) is
        an arbitrary driver-side safety cap, not a spec value.
        """
        chunks = []
        for _ in range(MAX_DATA_PACKETS):
            pid, content = self._read_frame()
            if pid == PID_END_OF_DATA:
                chunks.append(content)
                return b"".join(chunks)
            if pid != PID_DATA:
                raise R503ProtocolDesyncError(
                    "expected data (0x02) or end-of-data (0x08), got PID "
                    "0x%02X — a data packet may never appear without a "
                    "preceding command/acknowledge [CXC-R503-FRAMING]"
                    % pid
                )
            chunks.append(content)
        raise R503ProtocolDesyncError(
            "data burst exceeded MAX_DATA_PACKETS=%d without an "
            "end-of-data (0x08) packet — possible desync" % MAX_DATA_PACKETS
        )

    # -- SoftRst / unsolicited-byte handling -------------------------------

    def soft_reset(self):
        """[CXC-R503-UNSOLICITED-0X55]: send 0x3D SoftRst, then re-sync past
        the unframed 0x55 the module emits afterwards.

        # ASSUMED (not in excerpt) — see A10: no framed acknowledge is
        # expected for SoftRst; we only need to resynchronize the receiver.
        # The very next _read_frame() call (from any subsequent command)
        # will itself drain+sync past the 0x55 via _drain_rx()/
        # _sync_to_header(), which is the excerpt's own prescribed remedy
        # ("together they absorb ... the post-SoftRst byte").
        """
        self.send_command(OPCODE_SOFT_RESET)
        self._drain_rx()

    # -- AutoEnroll ---------------------------------------------------------

    def auto_enroll(self, enrollment_id, config1=0x00, config2=0x00,
                     config4=0x00, enable_status_stream=True, extra_params=b""):
        """Run the AutoEnroll (0x31) flow.

        [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP]: with the status-reporting
        config bit set, emits FIFTEEN acknowledge packets from one command
        (steps 0x01..0x0F, only 0x0F carrying the modelID); the enabling
        bit lives at "Config3" for this command specifically (NOT the
        same index AutoIdentify uses — the excerpt warns generalizing
        this is a driver bug).

        Returns a list of (confirmation, step, payload) tuples, one per
        ack received, in order. The final element's payload IS the
        modelID (raw bytes, A5) when the stream terminated via step
        0x0F with confirmation 0x00.

        Raises R503ProtocolDesyncError if the stream fails to terminate
        (by confirmation != 0x00 or by step 0x0F) within
        AUTO_ENROLL_STEP_COUNT (15) acks — the exact count the excerpt
        states, so this bound is NOT an assumption for AutoEnroll (unlike
        AutoIdentify, see auto_identify() below).
        """
        # A3/A4 (ASSUMED, not in excerpt): content layout and "set" encoding.
        config3 = 0x01 if enable_status_stream else 0x00
        content_params = (
            struct.pack(">H", enrollment_id)
            + bytes([config1, config2, config3, config4])
            + bytes(extra_params)
        )
        self._busy = True
        try:
            self.send_command(OPCODE_AUTO_ENROLL, content_params)
            acks = []
            for _ in range(AUTO_ENROLL_STEP_COUNT):
                confirmation, step, payload = self.read_ack()
                acks.append((confirmation, step, payload))
                # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] rule: terminated by
                # confirmation != 0x00 OR by the final step code.
                if confirmation != 0x00:
                    return acks
                if step == AUTO_ENROLL_FINAL_STEP:
                    return acks
            raise R503ProtocolDesyncError(
                "AutoEnroll ack stream did not terminate within the "
                "spec'd 15 packets (steps 0x01..0x0F) — see "
                "[CXC-R503-ACK-STREAM-BREAKS-REQ-RESP]"
            )
        finally:
            self._busy = False

    # -- AutoIdentify ---------------------------------------------------------

    def auto_identify(self, config1=0x00, config2=0x00, config3=0x00,
                       config4=0x00, enable_status_stream=True,
                       extra_params=b"", max_acks=DEFAULT_MAX_AUTOIDENTIFY_ACKS):
        """Run the AutoIdentify (0x32) flow.

        [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP]: 0x32 AutoIdentify emits
        THREE acknowledge packets nominally, and its status-reporting
        enable bit is at "Config1" — a DIFFERENT index than AutoEnroll's
        Config3. This method does NOT reuse AutoEnroll's config-byte
        wiring for that reason.

        [CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED]: with a finger
        continuously present that never matches and retry count zero,
        this command's ack stream is claimed to be unbounded (the
        excerpt is explicit this is an INFERENCE from stated retry
        semantics, not a shown trace). The 10-15s no-finger timeout does
        NOT bound this case. Per that node's rule, this driver imposes
        its OWN bound (max_acks, default DEFAULT_MAX_AUTOIDENTIFY_ACKS —
        see A9, an arbitrary driver-chosen cap, NOT a spec value) and
        raises R503ProtocolDesyncError rather than blocking forever when
        it is exceeded.

        Returns a list of (confirmation, step, payload) tuples, one per
        ack received, in order.
        """
        # A3/A4 (ASSUMED, not in excerpt): content layout and "set" encoding.
        # Config1 is the enable bit for THIS command (per excerpt); we still
        # send Config2-4 as caller-supplied/zeroed since their meaning is
        # entirely unspecified.
        config1 = 0x01 if enable_status_stream else config1
        content_params = bytes([config1, config2, config3, config4]) + bytes(extra_params)
        self._busy = True
        try:
            self.send_command(OPCODE_AUTO_IDENTIFY, content_params)
            acks = []
            for _ in range(max_acks):
                confirmation, step, payload = self.read_ack()
                acks.append((confirmation, step, payload))
                # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] rule: terminated by
                # confirmation != 0x00 OR by "the final step code". The
                # excerpt never gives AutoIdentify's final step-code VALUE
                # (only AutoEnroll's, 0x0F) — ASSUMED (not in excerpt): we
                # cannot check a numeric final-step for AutoIdentify, so
                # termination here relies on confirmation != 0x00 only,
                # plus the driver-imposed max_acks bound as the required
                # safety net for the unbounded case.
                if confirmation != 0x00:
                    return acks
            raise R503ProtocolDesyncError(
                "AutoIdentify ack stream exceeded max_acks=%d without "
                "terminating — treating as PROTOCOL_DESYNC per "
                "[CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED]'s explicit "
                "instruction not to block forever on an unbounded stream"
                % max_acks
            )
        finally:
            self._busy = False
