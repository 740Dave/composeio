"""
R503 fingerprint sensor — UART command/response driver.

Built ONLY from the wire-protocol excerpt at:
    docs/impl-test/opus-wire-excerpt.lts
Nodes cited inline by id: [CXC-R503-FRAMING], [CXC-R503-CHECKSUM],
[CXC-R503-UNSOLICITED-0X55], [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP],
[CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED].

Transport contract (given by the task, not the excerpt):
    self.port.write(bytes) -> None
    self.port.read(n)      -> up to n bytes, b'' on timeout / nothing buffered.

=====================================================================
ASSUMPTIONS — everything below was NOT stated by the excerpt and had to
be supplied to make the driver runnable. Each is repeated as a
`# ASSUMED (not in excerpt): ...` comment at its point of use.
=====================================================================

A1. CONFIRMATION CODE. The excerpt only says a stream is "terminated by
    confirmation != 0x00". It never states the byte offset of the
    confirmation code, nor that 0x00 means OK/continue. ASSUMED: content[0]
    of every reply packet is a 1-byte confirmation code; 0x00 = success /
    continue, non-zero = terminal error.

A2. INSTRUCTION BYTE POSITION. The excerpt gives opcodes (0x31 AutoEnroll,
    0x32 AutoIdentify, 0x3D SoftRst) but never says where the instruction
    byte sits in a command packet. INFERRED from the [CXC-R503-CHECKSUM]
    worked example `... 01 0007 35 01 50 06 00`: content[0] == 0x35 is the
    instruction (AuraLedConfig). So content[0] of a command = opcode.

A3. ACK STEP-CODE POSITION. The excerpt says AutoEnroll acks run "step 0x01
    through 0x0F" but not where the step byte lives in the ack. ASSUMED:
    ack content == [confirmation(1B), step_code(1B), params...].

A4. MODEL-ID. "only 0x0F carrying the modelID" — length/offset of modelID
    not given. ASSUMED: it is content[2:] of the step-0x0F ack (2 bytes).

A5. CONFIG INDEX -> BYTE/BIT. The excerpt says the streaming-enable
    parameter is Config3 on AutoEnroll and Config1 on AutoIdentify, but not
    the byte OFFSET of those config words in the param block nor which bit.
    ASSUMED: caller passes the fully-formed param bytes; the per-opcode
    index below is carried only to AVOID the "Config3 always enables
    streaming" generalisation bug the excerpt warns about — it is NOT used
    to synthesise a param block from nothing.

A6. AUTOENROLL PARAM LAYOUT. Beyond opcode 0x31, the excerpt gives no
    parameter layout (finger id, enroll count, etc.). ASSUMED: caller
    supplies the raw parameter bytes that follow the opcode.

A7. DRAIN / TIMEOUT SEMANTICS. self.port.read(n) is ASSUMED to return
    fewer bytes (down to b'') when the buffer is empty / on timeout, so
    draining and silence-detection terminate.

A8. DATA-BURST TERMINATOR. The excerpt gives PID 0x08 = end-of-data.
    INFERRED: a data burst is [PID 0x02]* terminated by one [PID 0x08]
    packet; the burst COUNT is not read from the excerpt (it "depends on
    device config"), so the reader loops on the terminator, never a count.

A9. BUSY. The excerpt describes NO busy confirmation-code value. "Command
    while BUSY" is therefore handled purely driver-side: a reply stream /
    data burst holds an in-flight flag and a second command raises
    R503BusyError rather than interleaving on the wire.

A10. SAFETY CAPS. The 15 (AutoEnroll) and 3 (AutoIdentify) ack counts ARE
    from the excerpt. The absolute hard cap used to bound the
    provably-unbounded AutoIdentify stream ([CXC-R503-AUTOIDENTIFY-...]) is
    a driver choice, not an excerpt value.

A11. CHECKSUM WIDTH. Checksum is 2 bytes, big-endian (framing says
    big-endian throughout; width from the [Checksum 2B] shape).
"""

from __future__ import annotations


# --- confirmation code (A1) -----------------------------------------------
# ASSUMED (not in excerpt): 0x00 == OK/continue; the excerpt only asserts
# that a value != 0x00 terminates a stream.
CONFIRM_OK = 0x00


class R503Error(Exception):
    """Base for all driver errors."""


class R503SilenceError(R503Error):
    """No framed reply arrived. Per [CXC-R503-FRAMING] a wrong address makes
    the module reject with SILENCE (not an error packet), so a timeout here
    is a first-class, named outcome — not a hang."""


class R503FramingError(R503Error):
    """Header/length/checksum did not validate."""


class R503BusyError(R503Error):
    """A command was issued while a prior command's reply stream / data burst
    was still in flight (A9 — no busy code exists in the excerpt, so this is
    enforced driver-side)."""


class R503ProtocolDesync(R503Error):
    """A bounded stream exceeded its bound — e.g. AutoIdentify looping on a
    finger-present-never-matches ([CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED])."""


class R503CommandError(R503Error):
    """A reply carried a non-zero confirmation code (A1)."""

    def __init__(self, confirmation: int, packet: "Packet"):
        super().__init__(f"confirmation=0x{confirmation:02X}")
        self.confirmation = confirmation
        self.packet = packet


class Packet:
    """A parsed R503 frame. [CXC-R503-FRAMING]:
    [Header 2B=0xEF01][Address 4B][PID 1B][Length 2B][Content ...][Checksum 2B]
    """

    __slots__ = ("address", "pid", "content", "checksum")

    def __init__(self, address: int, pid: int, content: bytes, checksum: int):
        self.address = address
        self.pid = pid
        self.content = content
        self.checksum = checksum

    @property
    def confirmation(self) -> int:
        # A1: content[0] is the confirmation code on reply packets.
        return self.content[0] if self.content else -1

    def __repr__(self) -> str:
        return (f"Packet(addr=0x{self.address:08X}, pid=0x{self.pid:02X}, "
                f"content={self.content.hex()}, cksum=0x{self.checksum:04X})")


class R503UartDriver:
    # --- [CXC-R503-FRAMING] constants (all from the excerpt) --------------
    HEADER = b"\xEF\x01"                 # 0xEF01, high byte first (big-endian)
    DEFAULT_ADDRESS = 0xFFFFFFFF

    PID_COMMAND = 0x01
    PID_DATA = 0x02
    PID_ACK = 0x07
    PID_END = 0x08

    # --- opcodes explicitly named in the excerpt --------------------------
    OP_AUTOENROLL = 0x31
    OP_AUTOIDENTIFY = 0x32
    OP_SOFTRST = 0x3D

    # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP]: expected ack count per command,
    # and — critically — the config INDEX that enables the stream differs
    # per command (Config3 vs Config1). Kept per-opcode so the driver never
    # generalises "Config3 enables streaming" (the warned-against bug).
    _STREAM_SPEC = {
        # opcode: (expected_ack_count, stream_enable_config_index, final_step)
        OP_AUTOENROLL:   (15, 3, 0x0F),   # steps 0x01..0x0F, 0x0F carries modelID
        OP_AUTOIDENTIFY: (3,  1, None),   # unbounded in the pathological case
    }

    # A10: hard cap to bound the provably-unbounded AutoIdentify stream.
    # ASSUMED (not in excerpt): value chosen for safety, generously above the
    # excerpt's stated 3-ack nominal count.
    _ABSOLUTE_ACK_CAP = 64
    # A8/A10: same idea for a config-dependent data burst.
    _ABSOLUTE_BURST_CAP = 256

    def __init__(self, port, address: int = DEFAULT_ADDRESS):
        self.port = port
        self.address = address
        self._in_flight = False  # A9: driver-side busy guard

    # =====================================================================
    # Framing: build / checksum
    # =====================================================================
    @staticmethod
    def _checksum(pid: int, length: int, content: bytes) -> int:
        # [CXC-R503-CHECKSUM]: sum of PID + BOTH length bytes + every content
        # byte, mod 2^16. Header and address are NOT included.
        length_hi = (length >> 8) & 0xFF
        length_lo = length & 0xFF
        total = pid + length_hi + length_lo + sum(content)
        return total & 0xFFFF

    def _build_packet(self, pid: int, content: bytes) -> bytes:
        # [CXC-R503-FRAMING] length-semantics: Length = content bytes + 2
        # (it INCLUDES the 2-byte checksum). Reading it as content length
        # overruns by two bytes.
        length = len(content) + 2
        cksum = self._checksum(pid, length, content)
        return b"".join((
            self.HEADER,
            self.address.to_bytes(4, "big"),   # big-endian throughout
            bytes((pid,)),
            length.to_bytes(2, "big"),
            content,
            cksum.to_bytes(2, "big"),
        ))

    # =====================================================================
    # RX: drain + sliding-window read ([CXC-R503-UNSOLICITED-0X55])
    # =====================================================================
    def _drain_rx(self) -> None:
        # [CXC-R503-UNSOLICITED-0X55] remedy, half 1: drain RX immediately
        # before EVERY transmit. This absorbs the bare unframed 0x55 emitted
        # at power-on AND after every 0x3D SoftRst, plus any stale bytes from
        # an aborted transaction.
        # A7: read(n) returns b'' when nothing is buffered.
        while True:
            chunk = self.port.read(64)
            if not chunk:
                return

    def _read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.port.read(n - len(buf))
            if not chunk:
                # A7 + [CXC-R503-FRAMING]: silence == wrong-address rejection
                # or an absent module. Named outcome, never a hang.
                raise R503SilenceError(
                    f"expected {n} bytes, got {len(buf)} before silence")
            buf.extend(chunk)
        return bytes(buf)

    def _read_packet(self) -> Packet:
        # [CXC-R503-UNSOLICITED-0X55] remedy, half 2: parse with a sliding
        # two-byte header window. A fixed-length-frame parser is permanently
        # phase-shifted by the stray 0x55; the window re-syncs on 0xEF 0x01
        # regardless of how many junk bytes (0x55 or otherwise) precede it.
        window = bytearray(2)
        # prime the window
        first = self.port.read(1)
        if not first:
            raise R503SilenceError("no bytes before header")
        window[0] = first[0]
        while True:
            nxt = self.port.read(1)
            if not nxt:
                raise R503SilenceError("silence while hunting for 0xEF01 header")
            window[1] = nxt[0]
            if window[0] == self.HEADER[0] and window[1] == self.HEADER[1]:
                break
            # slide
            window[0] = window[1]

        address = int.from_bytes(self._read_exact(4), "big")
        pid = self._read_exact(1)[0]
        length = int.from_bytes(self._read_exact(2), "big")
        if length < 2:
            raise R503FramingError(f"length {length} < 2 (must include checksum)")
        # length-semantics: content is (length - 2) bytes; last 2 are checksum.
        content = self._read_exact(length - 2)
        checksum = int.from_bytes(self._read_exact(2), "big")

        expected = self._checksum(pid, length, content)
        if checksum != expected:
            raise R503FramingError(
                f"checksum 0x{checksum:04X} != computed 0x{expected:04X}")

        # A note on address: [CXC-R503-FRAMING] says a wrong TX address yields
        # silence, so a mismatched RX address is unexpected; we surface it
        # rather than silently trust the frame.
        if address != self.address and self.address != self.DEFAULT_ADDRESS:
            raise R503FramingError(
                f"reply address 0x{address:08X} != 0x{self.address:08X}")

        return Packet(address, pid, content, checksum)

    # =====================================================================
    # TX
    # =====================================================================
    def _send(self, pid: int, content: bytes) -> None:
        self._drain_rx()  # [CXC-R503-UNSOLICITED-0X55]: before EVERY transmit
        self.port.write(self._build_packet(pid, content))

    def send_command(self, content: bytes) -> Packet:
        """Send one command packet (content[0] must be the opcode, A2) and
        read exactly one reply. For simple one-reply commands only — the
        streaming commands use auto_enroll()/auto_identify()."""
        if self._in_flight:
            # A9: no busy code exists in the excerpt; refuse on the wire.
            raise R503BusyError("command issued while a stream/burst is in flight")
        self._in_flight = True
        try:
            self._send(self.PID_COMMAND, content)
            reply = self._read_packet()
            conf = reply.confirmation
            # ASSUMED (not in excerpt): non-zero confirmation == error (A1).
            if conf != CONFIRM_OK:
                raise R503CommandError(conf, reply)
            return reply
        finally:
            self._in_flight = False

    # =====================================================================
    # AutoEnroll — the acknowledge STREAM
    # [CXC-R503-ACK-STREAM-BREAKS-REQ-RESP]
    # =====================================================================
    def auto_enroll(self, params: bytes) -> bytes:
        """Run the AutoEnroll (0x31) flow.

        `params` is the raw parameter block that FOLLOWS the 0x31 opcode
        (A6 — the excerpt gives no AutoEnroll param layout; caller supplies
        it). The status-reporting bit must be set in the Config3 word of
        `params` for the 15-ack stream to be emitted; see A5 — the driver
        cannot synthesise that bit because its byte/bit position is not in
        the excerpt.

        Returns the modelID bytes carried by the final (step 0x0F) ack (A4).
        """
        return self._run_stream(self.OP_AUTOENROLL, params)

    def auto_identify(self, params: bytes) -> Packet:
        """Run AutoIdentify (0x32). Nominal stream is 3 acks; the excerpt
        ([CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED]) proves it can loop
        UNBOUNDED with a finger present that never matches and retry=0, so
        this imposes its own bound and raises R503ProtocolDesync rather than
        blocking."""
        return self._run_stream(self.OP_AUTOIDENTIFY, params, return_last=True)

    def _run_stream(self, opcode: int, params: bytes, return_last: bool = False):
        if opcode not in self._STREAM_SPEC:
            raise R503Error(f"opcode 0x{opcode:02X} is not a stream command")
        if self._in_flight:
            raise R503BusyError("command issued while a stream/burst is in flight")

        expected_count, enable_index, final_step = self._STREAM_SPEC[opcode]
        # A5 / hazard-config-index: the enable index is carried PER OPCODE so
        # we never mis-apply AutoEnroll's Config3 assumption to AutoIdentify's
        # Config1. We do not mutate `params` here (byte offset unknown, A5);
        # `enable_index` exists to keep the distinction explicit and testable.
        _ = enable_index

        self._in_flight = True
        try:
            content = bytes((opcode,)) + params
            self._send(self.PID_COMMAND, content)

            acks: list[Packet] = []
            model_id = b""
            # [CXC-R503-ACK-STREAM-...] rule: terminate on "confirmation != 0x00
            # OR the final step code, not a single reply." So the TERMINATOR is
            # a signal, never a fixed count. `expected_count` (15 / 3) is the
            # excerpt's NOMINAL length and is used ONLY to size the safety cap;
            # it never terminates the loop (that would stop AutoIdentify's
            # unbounded stream mid-flight). Cap surfaces PROTOCOL_DESYNC per
            # [CXC-R503-AUTOIDENTIFY-STREAM-UNBOUNDED]. A10.
            hard_cap = min(self._ABSOLUTE_ACK_CAP, max(expected_count * 4, 8))
            for _i in range(hard_cap):
                pkt = self._read_packet()
                if pkt.pid != self.PID_ACK:
                    raise R503FramingError(
                        f"expected ACK (0x07) in stream, got pid 0x{pkt.pid:02X}")
                acks.append(pkt)
                conf = pkt.confirmation

                if final_step is not None:
                    # AutoEnroll: terminated by the final step code (0x0F).
                    # A non-zero confirmation BEFORE that is a genuine failure.
                    if conf != CONFIRM_OK:
                        # A1: non-zero confirmation ends the stream in error.
                        raise R503CommandError(conf, pkt)
                    # A3: step code is ack content[1].
                    step = pkt.content[1] if len(pkt.content) > 1 else None
                    if step == final_step:
                        # A4: modelID is the remaining content of the 0x0F ack.
                        model_id = pkt.content[2:]
                        return acks[-1] if return_last else model_id
                    # else: an intermediate progress ack — keep reading.
                else:
                    # AutoIdentify: no final step code in the excerpt, so the
                    # ONLY terminator is confirmation != 0x00. A continue-ack
                    # (0x00) means keep reading; a non-zero confirmation is the
                    # terminal result (match / definitive no-match) — returned,
                    # NOT raised, because the excerpt does not say a non-zero
                    # AutoIdentify confirmation is an error (ambiguity, flagged).
                    if conf != CONFIRM_OK:
                        return acks[-1] if return_last else model_id
                    # else: continue-ack — keep reading toward the bound.

            # Fell off the cap without a terminator: exactly the unbounded
            # pathology the HAL must surface rather than block on.
            raise R503ProtocolDesync(
                f"stream for opcode 0x{opcode:02X} exceeded bound "
                f"{hard_cap} acks without terminating")
        finally:
            self._in_flight = False

    # =====================================================================
    # Data-packet BURST (RX-buffer-sized, config-dependent count)
    # [CXC-R503-FRAMING] pid semantics + A8
    # =====================================================================
    def read_data_burst(self) -> list[bytes]:
        """Read a burst of data packets (PID 0x02) terminated by an
        end-of-data packet (PID 0x08).

        Per [CXC-R503-FRAMING] a data packet must FOLLOW a command or ack —
        never appear alone — so this is only ever called mid-transaction,
        while _in_flight is already set by the caller. The number of data
        packets depends on device config (RX buffer sizing); A8 — we loop on
        the 0x08 terminator rather than a fixed count, so the burst size need
        not be known ahead of time.
        """
        chunks: list[bytes] = []
        for _ in range(self._ABSOLUTE_BURST_CAP):
            pkt = self._read_packet()
            if pkt.pid == self.PID_DATA:
                chunks.append(pkt.content)
            elif pkt.pid == self.PID_END:
                chunks.append(pkt.content)  # final segment
                return chunks
            else:
                raise R503FramingError(
                    f"expected DATA/END (0x02/0x08) in burst, "
                    f"got pid 0x{pkt.pid:02X}")
        raise R503ProtocolDesync(
            f"data burst exceeded {self._ABSOLUTE_BURST_CAP} packets "
            "without an end-of-data (0x08) terminator")

    # =====================================================================
    # SoftRst — note it RE-ARMS the unsolicited 0x55 ([CXC-R503-UNSOLICITED-0X55])
    # =====================================================================
    def soft_reset(self) -> Packet:
        """Issue 0x3D SoftRst. The module will emit a bare unframed 0x55 AFTER
        this; the next _send()'s _drain_rx() absorbs it, and the sliding
        header window re-syncs even if the drain misses it."""
        # A2: opcode is content[0]; SoftRst takes no params in the excerpt.
        return self.send_command(bytes((self.OP_SOFTRST,)))


# ---------------------------------------------------------------------------
# Self-check for the ONE machine-verifiable claim in the excerpt
# ([CXC-R503-CHECKSUM] verify-machine). Touches no hardware.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # AuraLedConfig example: EF01 FFFFFFFF 01 0007 35 01 50 06 00 -> cksum 0x0094
    content = bytes((0x35, 0x01, 0x50, 0x06, 0x00))
    length = len(content) + 2  # = 0x0007
    got = R503UartDriver._checksum(0x01, length, content)
    assert got == 0x0094, f"checksum self-check failed: 0x{got:04X} != 0x0094"
    print("checksum self-check OK: 0x%04X" % got)
