"""R503 fingerprint-sensor UART command/response driver.

Built ONLY from the wire-protocol excerpt at ./sonnet-wire-excerpt.lts
([SPEC:CXC-R503C]). The excerpt specifies the FRAMING and the request/response
HAZARDS but deliberately withholds most concrete byte values (opcodes, PID
values, confirmation codes, the checksum algorithm). Everywhere the excerpt did
not give me a value I needed, I made an explicit assumption, flagged it inline
with `# ASSUMED (not in excerpt): ...`, and listed it here.

==============================================================================
ASSUMPTIONS (nothing below is stated in the excerpt; a real datasheet is needed)
==============================================================================
A1. CHECKSUM ALGORITHM. The excerpt says the checksum is 2 bytes, big-endian,
    and that "Length is content bytes plus two (the checksum)" — i.e. Length
    covers the checksum. It does NOT state which bytes the checksum sums, nor
    whether it is a sum or a CRC. ASSUMED: 16-bit arithmetic sum of every byte
    from the PID through the last content byte inclusive (PID + Length_hi +
    Length_lo + content), truncated to the low 16 bits. (This is the well-known
    R503/ZhianTec family rule; it is NOT derivable from the excerpt alone.)
A2. PID (packet-identifier) VALUES. The excerpt names the PID field but gives no
    values. ASSUMED: command=0x01, ack/response=0x07, data=0x02, end-of-data
    =0x08. The data / end-of-data pair is load-bearing for the burst terminator
    (CXC-R503C-DATA-PACKET-BURSTS says "terminate on the end-of-data PID").
A3. DEFAULT DEVICE ADDRESS = 0xFFFFFFFF. The excerpt gives the 4-byte address
    field but no value; a wrong address yields silence (a real hazard for this
    guess). Broadcast/default 0xFFFFFFFF is the conventional factory value.
A4. AutoEnroll OPCODE = 0x31. The excerpt names the "packaged AutoEnroll
    command" but gives no opcode byte.
A5. SoftRst / Cancel OPCODES = 0x3D / 0x30. Named in the excerpt
    (CXC-R503C-UNSOLICITED-0X55 references SoftRst; busy node references Cancel)
    but no opcode bytes are given.
A6. CONFIRMATION CODES. ASSUMED: 0x00 = OK/continue. The excerpt says stream
    termination is inferred from "a non-zero confirmation code" — so I treat any
    non-zero code as terminal, which needs no specific value. I ALSO assume a
    distinguished BUSY confirmation code exists but, since the excerpt does not
    give its value and explicitly says busy-behaviour is undocumented, I do NOT
    hardcode one — see A7.
A7. BUSY handling. The excerpt (CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED) says it is
    undocumented whether a command sent while Busy is queued/rejected/dropped,
    and directs that an unexpected response be classified `protocol_desync`.
    ASSUMED design choice (not a datasheet value): the driver serialises access
    with an in-driver busy flag so it never *originates* an overlapping command,
    and if it nonetheless sees a malformed/unexpected frame it raises
    ProtocolDesync rather than inventing a queue/reject/drop semantics.
A8. AutoEnroll TERMINAL STEP VALUE. The excerpt says termination may be inferred
    from "a specific terminal step value" but does not give it. ASSUMED: the
    stream also ends when the ack count reaches the stated ceiling of 15, and on
    any non-zero confirmation code; I do NOT hardcode a magic terminal step,
    because I don't know it. This means on a fully-successful run I rely on the
    15-packet ceiling / caller-supplied expected step. Flagged at use.
A9. TRANSPORT SEMANTICS. self.port.read(n) returns up to n bytes and returns
    b'' (not blocking forever) when no byte is currently available — i.e. it is
    timeout/non-blocking. Required to implement "drain RX" and bounded reads.
A10. Reset/boot settle: after issuing SoftRst the module re-emits the unsolicited
    0x55; I drain it. No timing value is given, so I bound waits by read timeouts
    only (A9), not by sleeps.
==============================================================================

AMBIGUITIES / CONTRADICTIONS noted in the excerpt (see final report for detail):
 - "up to fifteen acknowledge packets" gives a ceiling but no exact count and no
   explicit end marker; combined with A8 the success-path terminator is soft.
 - The checksum is asserted correct against 21 examples but the algorithm itself
   is never written down — the strongest single gap.
 - Busy behaviour is explicitly undefined; any handling is a choice, not spec.
"""

from __future__ import annotations

import struct
from typing import List, NamedTuple, Optional


# --- Framing constants (from the excerpt) -----------------------------------
HEADER = 0xEF01                      # given: [Header 2B = 0xEF01]
HEADER_BYTES = b"\xEF\x01"           # given
UNSOLICITED_BYTE = 0x55              # given: bare unframed byte at boot / post-reset

# --- PID values (ASSUMED — A2; excerpt names the field, gives no values) -----
PID_COMMAND = 0x01   # ASSUMED (not in excerpt): command packet id
PID_ACK = 0x07       # ASSUMED (not in excerpt): acknowledge / response packet id
PID_DATA = 0x02      # ASSUMED (not in excerpt): data packet id
PID_END = 0x08       # ASSUMED (not in excerpt): end-of-data packet id

# --- Opcodes (ASSUMED — A4/A5; excerpt names the commands, gives no bytes) ----
OP_AUTO_ENROLL = 0x31  # ASSUMED (not in excerpt): packaged AutoEnroll opcode
OP_SOFT_RST = 0x3D     # ASSUMED (not in excerpt): SoftRst opcode
OP_CANCEL = 0x30       # ASSUMED (not in excerpt): Cancel opcode

# --- Confirmation codes (ASSUMED — A6) ---------------------------------------
CC_OK = 0x00           # ASSUMED (not in excerpt): success / continue

DEFAULT_ADDRESS = 0xFFFFFFFF  # ASSUMED (not in excerpt): A3
MAX_ACK_PACKETS = 15          # given: "up to fifteen acknowledge packets"


class ProtocolDesync(Exception):
    """Raised for any unexpected/malformed frame.

    Per CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED, an unexpected response (including one
    that may have resulted from sending while Busy) is classified protocol_desync
    rather than assuming a queue/reject/drop behaviour the datasheet never states.
    """


class SilenceError(ProtocolDesync):
    """No framed reply arrived. Per CXC-R503C-FRAMING a wrong address produces
    silence, never an error packet — so silence is the observable failure."""


class Packet(NamedTuple):
    address: int
    pid: int
    content: bytes  # confirmation code (if any) is content[0] by convention


class R503UartDriver:
    """UART command/response driver for the R503.

    Transport contract: self.port.read(n)->bytes (up to n, b'' if none pending,
    per A9) and self.port.write(bytes)->None.
    """

    def __init__(self, port, address: int = DEFAULT_ADDRESS,
                 max_frame_content: int = 256):
        self.port = port
        self.address = address
        # RX-buffer sizing: the count of data packets in a burst depends on a
        # negotiated packet-size parameter (CXC-R503C-DATA-PACKET-BURSTS), so we
        # never size a fixed receive array to the burst; we stream per-packet and
        # bound only a single packet's content.
        self._max_frame_content = max_frame_content
        self._busy = False  # in-driver serialisation guard (A7)

    # ---- checksum (algorithm ASSUMED — A1) ---------------------------------
    @staticmethod
    def _checksum(pid: int, length: int, content: bytes) -> int:
        # ASSUMED (not in excerpt): 16-bit sum of PID + Length(2B) + content,
        # low 16 bits. The excerpt fixes only that Length INCLUDES the 2 checksum
        # bytes; it does not state the summed range or sum-vs-CRC.
        total = pid + ((length >> 8) & 0xFF) + (length & 0xFF)
        total += sum(content)
        return total & 0xFFFF

    # ---- framing: build ----------------------------------------------------
    def _build_packet(self, pid: int, content: bytes) -> bytes:
        # given: Length = len(content) + 2 (the +2 is the checksum). A parser
        # reading Length as content-only overruns by two bytes every packet.
        length = len(content) + 2
        chk = self._checksum(pid, length, content)
        # big-endian throughout (given)
        return (
            HEADER_BYTES
            + struct.pack(">I", self.address)   # Address 4B, big-endian
            + struct.pack(">B", pid)            # PID 1B
            + struct.pack(">H", length)         # Length 2B, big-endian
            + content
            + struct.pack(">H", chk)            # Checksum 2B, big-endian
        )

    # ---- RX drain (remedy half 1 of CXC-R503C-UNSOLICITED-0X55) -------------
    def _drain_rx(self) -> int:
        """Discard any bytes currently pending (boot/post-reset 0x55, stale
        packet from an aborted transaction). Required BEFORE every transmit."""
        drained = 0
        while True:
            b = self.port.read(256)  # A9: returns b'' when nothing pending
            if not b:
                break
            drained += len(b)
        return drained

    # ---- framing: read one packet via sliding 2-byte header window ----------
    # (remedy half 2 of CXC-R503C-UNSOLICITED-0X55). The window absorbs the bare
    # 0x55 and any leading garbage: only the 0xEF 0x01 pair starts a frame.
    def _read_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self.port.read(n - len(buf))
            if not chunk:
                break  # A9: transport idle → give up (bounded by its timeout)
            buf += chunk
        return bytes(buf)

    def _read_packet(self, max_header_scan: int = 4096) -> Packet:
        # Slide a two-byte window until it equals the header. Every non-matching
        # lead byte (including an unframed 0x55) is discarded harmlessly.
        window = bytearray(2)
        have = 0
        scanned = 0
        while True:
            b = self.port.read(1)
            if not b:
                raise SilenceError("no framed reply (silence — cf. wrong address)")
            scanned += 1
            if scanned > max_header_scan:
                raise ProtocolDesync("header not found within scan budget")
            # shift window left, append new byte
            window[0] = window[1]
            window[1] = b[0]
            have = min(have + 1, 2)
            if have >= 2 and window[0] == 0xEF and window[1] == 0x01:
                break

        # header found; read Address(4) + PID(1) + Length(2)
        head = self._read_exact(7)
        if len(head) != 7:
            raise ProtocolDesync("truncated frame header")
        address = struct.unpack(">I", head[0:4])[0]
        pid = head[4]
        length = struct.unpack(">H", head[5:7])[0]
        if length < 2 or length - 2 > self._max_frame_content:
            # Length includes the 2 checksum bytes (given). A length < 2 or an
            # absurd content size is a desync, not a valid frame.
            raise ProtocolDesync(f"implausible Length={length}")

        body = self._read_exact(length)  # content(length-2) + checksum(2)
        if len(body) != length:
            raise ProtocolDesync("truncated frame body")
        content = body[:-2]
        rx_chk = struct.unpack(">H", body[-2:])[0]
        if self._checksum(pid, length, content) != rx_chk:
            # ASSUMED checksum (A1); a mismatch here could be a real error OR a
            # wrong checksum assumption. Surfaced, not silently accepted.
            raise ProtocolDesync("checksum mismatch (or wrong checksum assumption A1)")

        return Packet(address=address, pid=pid, content=content)

    # ---- one command → one ack response ------------------------------------
    def send_command(self, opcode: int, params: bytes = b"") -> Packet:
        """Send a single command and read exactly one ack packet."""
        if self._busy:
            # A7: never originate an overlapping command; busy behaviour is
            # undocumented so we refuse to send rather than guess the device's
            # queue/reject/drop response.
            raise ProtocolDesync("driver busy: refusing to overlap (busy undefined)")
        self._busy = True
        try:
            self._drain_rx()  # absorb 0x55 / stale packet BEFORE transmit (given)
            self.port.write(self._build_packet(PID_COMMAND, bytes([opcode]) + params))
            pkt = self._read_packet()
            if pkt.pid != PID_ACK:
                raise ProtocolDesync(f"expected ack PID {PID_ACK:#x}, got {pkt.pid:#x}")
            return pkt
        finally:
            self._busy = False

    def _confirmation_code(self, pkt: Packet) -> int:
        # ASSUMED (A6): confirmation code is the first content byte of an ack.
        if not pkt.content:
            raise ProtocolDesync("ack had empty content (no confirmation code)")
        return pkt.content[0]

    # ---- HAZARD: AutoEnroll acknowledge STREAM (CXC-R503C-ACK-STREAM) -------
    def auto_enroll(self, finger_id: int, enroll_count: int = 4,
                    status_reporting: bool = True,
                    expected_terminal_step: Optional[int] = None) -> List[Packet]:
        """Run the packaged AutoEnroll flow: ONE command → up to fifteen ack
        packets (CXC-R503C-ACK-STREAM). There is no explicit end-of-stream
        marker; termination is inferred from a non-zero confirmation code or a
        specific terminal step value.

        NOTE (excerpt, CXC-R503C-ACK-STREAM consequence): the referenced build
        [SPEC:CXC-FPHALC] does NOT use this packaged command — it uses the
        general capture/generate/register/store sequence instead. This method is
        provided because the hazard is real; a future user of the packaged path
        must handle the stream exactly as below.
        """
        if self._busy:
            raise ProtocolDesync("driver busy: refusing to overlap (busy undefined)")
        self._busy = True
        acks: List[Packet] = []
        try:
            self._drain_rx()  # given remedy: drain BEFORE transmit

            # Parameter layout of AutoEnroll is NOT in the excerpt.
            # ASSUMED (not in excerpt): [finger_id(2B BE)][enroll_count(1B)]
            # [config(1B)] where the config's status-reporting bit enables the
            # ack stream. The excerpt WARNS that the enabling bit sits at a
            # DIFFERENT config index on the sibling AutoIdentify command, so we
            # do NOT generalise "one index enables streaming" — this constant is
            # local to AutoEnroll only.
            STATUS_BIT = 0x01  # ASSUMED (not in excerpt): AutoEnroll status bit
            config = STATUS_BIT if status_reporting else 0x00
            params = struct.pack(">H", finger_id) + bytes([enroll_count, config])
            self.port.write(self._build_packet(PID_COMMAND, bytes([OP_AUTO_ENROLL]) + params))

            # Consume the stream. Ceiling of 15 packets is GIVEN and used as the
            # hard upper bound so a missing terminator can never hang us.
            for _ in range(MAX_ACK_PACKETS):
                pkt = self._read_packet()
                if pkt.pid != PID_ACK:
                    raise ProtocolDesync(
                        f"AutoEnroll stream: expected ack PID {PID_ACK:#x}, got {pkt.pid:#x}")
                acks.append(pkt)

                cc = self._confirmation_code(pkt)
                if cc != CC_OK:
                    # given: a non-zero confirmation code terminates the stream
                    # (whether success-final or error). Stop here.
                    break

                # given: termination may also be a "specific terminal step
                # value". The excerpt does not give that value (A8). If the
                # caller supplied the expected terminal step, honour it; else we
                # rely on the non-zero-cc break above and the 15-packet ceiling.
                if expected_terminal_step is not None and len(pkt.content) >= 2:
                    # ASSUMED (not in excerpt): step indicator is content[1].
                    step = pkt.content[1]
                    if step == expected_terminal_step:
                        break

            return acks
        finally:
            self._busy = False

    # ---- HAZARD: data-packet BURSTS (CXC-R503C-DATA-PACKET-BURSTS) ----------
    def read_data_burst(self) -> bytes:
        """After a transfer command's ack, read the ack-then-N-data-packets
        burst. N is set by a negotiated packet-size system parameter, NOT by the
        command — so we MUST terminate on the end-of-data PID and NEVER by
        counting (given). Returns the concatenated payload.

        Caller flow: send the transfer command via send_command(...) to get the
        ack, then call this to collect the data packets.
        """
        chunks: List[bytes] = []
        # No fixed RX array sized to the burst — we stream one packet at a time,
        # bounded only per-packet, because the burst length is config-dependent.
        while True:
            pkt = self._read_packet()
            if pkt.pid == PID_DATA:
                chunks.append(pkt.content)
                continue
            if pkt.pid == PID_END:  # end-of-data PID → terminator (given)
                chunks.append(pkt.content)
                break
            raise ProtocolDesync(
                f"data burst: expected data/end PID, got {pkt.pid:#x}")
        return b"".join(chunks)

    # ---- reset: re-emits the unsolicited 0x55 (CXC-R503C-UNSOLICITED-0X55) --
    def soft_reset(self) -> None:
        """Issue SoftRst. The module re-emits a bare 0x55 afterwards; the next
        transmit's _drain_rx() (or the sliding-window read) absorbs it. We drain
        here too so a stray boot byte cannot desync a subsequent read."""
        self._drain_rx()
        self.port.write(self._build_packet(PID_COMMAND, bytes([OP_SOFT_RST])))
        # ASSUMED (A10): no post-reset timing given; the module's own ack (if
        # any) plus the re-emitted 0x55 are absorbed by the sliding-window read
        # and the next drain. We do not sleep on an invented delay.
        try:
            self._read_packet()  # ack, if the device sends one before the 0x55
        except SilenceError:
            pass  # some units may answer only with the bare 0x55; tolerate it
        self._drain_rx()  # sweep the post-reset unsolicited byte

    def cancel(self) -> Packet:
        """Issue Cancel (exists per the busy node, but busy semantics are
        undocumented — CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED). Sent through the
        normal single-command path."""
        return self.send_command(OP_CANCEL)
