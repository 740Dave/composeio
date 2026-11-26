"""
R503 fingerprint sensor — UART command/response driver.

Built ONLY from the wire-protocol excerpt fable-wire-excerpt.lts. Every place the
excerpt did not give me a value I needed is marked `# ASSUMED (not in excerpt): ...`
at the point of use and collected in ASSUMPTIONS below. A spec that forced me outside
itself is recorded as data, not smuggled in as fact.

Transport contract (given by the task): self.port.read(n) -> bytes (up to n),
self.port.write(b) -> None.

====================================================================================
ASSUMPTIONS (none of these are in the excerpt; listed at point of use too)
------------------------------------------------------------------------------------
A1. Confirmation code is the FIRST content byte of an acknowledge (PID 0x07) packet.
    The excerpt lists confirmation-code VALUES but never states WHERE in the packet the
    code sits. This is the universal R503 convention but the excerpt does not assert it.
A2. port.read(n) returns fewer than n bytes (possibly b'') when the requested bytes do
    not arrive before the transport's own timeout — i.e. it is not infinitely blocking.
    Draining RX (excerpt: inv-4) is impossible to bound otherwise.
A3. AutoEnroll (0x31) command argument bytes: the excerpt gives the opcode and the
    Config3-enables-the-stream fact, but NOT the argument layout (finger id, enroll
    count, parameter/config word). I send a documented-shape best guess and flag it;
    the caller must supply the real argument bytes for a live module.
A4. In each AutoEnroll ack packet, byte[0] is the confirmation code and byte[1] is the
    "step" field (0x01..0x0F). The excerpt says there are 15 packets "steps 0x01
    through 0x0F" but does not give the field OFFSET.
A5. The final AutoEnroll ack's fingerID field is a 2-byte big-endian value immediately
    after the step byte. The excerpt says "only the final packet's fingerID field is
    meaningful" but gives neither offset nor width.
A6. ReadSysPara (0x0F) returns an ack whose content contains a 16-bit system-status
    register, and Busy is bit 0 of that register (the excerpt says "system status bit 0
    via ReadSysPara" but gives no byte offset for the register within the content). I
    read the register as the first 2 content bytes after the confirmation code.
A7. SetSysPara (0x0E) is the way Config3 gets set before AutoEnroll; the excerpt names
    the opcode but not the parameter-index / value encoding, so set_config() only frames
    the bytes the caller hands it.
A8. Read timeout defaults (per-packet 2 s; ack-stream wall clock 15 s) are engineering
    choices. Only the AutoEnroll no-finger EXIT value is discussed in the excerpt, and
    it is contradictory (10 s prose / 15 s flowchart); per inv-7 I USE 15 s (fails later)
    and RECORD 10 s. All other timeouts are mine.
A9. The address used for TX and expected on RX is the module default 0xFFFFFFFF unless
    the caller overrides it. (Default value IS in the excerpt; that it is also the RX
    address to match against is my inference.)
====================================================================================
"""

import time


# --- Framing constants (all from the excerpt) --------------------------------------
HEADER = b"\xEF\x01"                       # excerpt: header 2B 0xEF01
DEFAULT_ADDRESS = 0xFFFFFFFF               # excerpt: address-gating default 0xFFFFFFFF

PID_COMMAND = 0x01                         # excerpt: pid-values
PID_DATA = 0x02
PID_ACK = 0x07
PID_END_OF_DATA = 0x08

UNSOLICITED_BYTE = 0x55                    # excerpt: bare 0x55 at power-on / after SoftRst

# Opcodes (excerpt core-set). Only the ones this driver uses are named; the rest exist.
OP_READ_SYS_PARA = 0x0F                    # excerpt core-set
OP_SET_SYS_PARA = 0x0E                     # excerpt core-set
OP_CANCEL = 0x30                           # excerpt: Cancel 0x30
OP_AUTO_ENROLL = 0x31                      # excerpt: AutoEnroll 0x31
OP_SOFT_RST = 0x3D                         # excerpt: SoftRst 0x3D
OP_HANDSHAKE = 0x40                        # excerpt correction-handshake: 0x40 (NOT 0x53)

# Confirmation codes the excerpt enumerates. This map is for HUMAN-READABLE surfacing
# only; per [CXC-R503-CODES] an unlisted code is surfaced, never fatal, never remapped
# onto the nearest documented one — so lookups fall back to "unknown", they don't raise.
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
}

# AutoEnroll no-finger exit — the excerpt gives BOTH; inv-7 says take the later-failing
# reading and record the other.
AUTOENROLL_NOFINGER_TIMEOUT_S = 15.0       # excerpt timeout-contradiction: flowchart value (fails later) — USED
AUTOENROLL_NOFINGER_TIMEOUT_RECORDED_S = 10.0  # excerpt: prose value — RECORDED, not used

AUTOENROLL_ACK_COUNT = 15                  # excerpt: FIFTEEN acknowledge packets
AUTOENROLL_LAST_STEP = 0x0F               # excerpt: steps 0x01 through 0x0F


class R503Packet:
    """A parsed, checksum-verified packet."""

    __slots__ = ("address", "pid", "content", "confirmation_code")

    def __init__(self, address, pid, content):
        self.address = address
        self.pid = pid
        self.content = content
        # ASSUMED (not in excerpt) A1: confirmation code is content[0] on an ack packet.
        self.confirmation_code = content[0] if content else None

    def code_name(self):
        # [CXC-R503-CODES]: unlisted code is surfaced, never mapped onto nearest documented.
        if self.confirmation_code is None:
            return None
        return CONFIRMATION_CODES.get(
            self.confirmation_code,
            "UNDOCUMENTED(0x%02X)" % self.confirmation_code,
        )

    def __repr__(self):
        return "R503Packet(pid=0x%02X, code=%s, content=%s)" % (
            self.pid, self.code_name(), self.content.hex()
        )


class R503Error(Exception):
    pass


class R503ChecksumError(R503Error):
    pass


class R503TimeoutError(R503Error):
    pass


class R503BusyError(R503Error):
    """Raised by the *policy* guard, not by the protocol: the excerpt states plainly
    that behaviour while Busy is UNDEFINED, so the driver refuses to send rather than
    guess whether the module queues/rejects/drops ([CXC-R503-BUSY])."""


class R503UartDriver:

    # Guard against the 256-byte packet trap ([CXC-R503-PACKET-256]): never size a buffer
    # on the assumption that 256-byte data payloads are legal. The caller's buffer is the
    # binding check; this is a sanity ceiling only, comfortably above the 128-byte case
    # that IS worked in every revision, and it prevents a corrupt length field from
    # making us read forever.
    MAX_CONTENT_LEN = 512

    def __init__(self, port, address=DEFAULT_ADDRESS, packet_timeout_s=2.0):
        self.port = port
        self.address = address
        # ASSUMED (not in excerpt) A8: per-packet read timeout default.
        self.packet_timeout_s = packet_timeout_s
        self._rx = bytearray()  # sliding parse buffer (excerpt inv-4: two-byte header window)

    # ---------------------------------------------------------------------------------
    # Low-level TX
    # ---------------------------------------------------------------------------------
    def _checksum(self, pid, length_bytes, content):
        # [CXC-R503-CHECKSUM]: sum of PID, both length bytes, all content, mod 2^16.
        # Header and address are NOT included.
        total = pid + length_bytes[0] + length_bytes[1] + sum(content)
        return total & 0xFFFF

    def _build_packet(self, pid, content):
        # [CXC-R503-FRAMING] length-semantics: length counts content PLUS the two
        # checksum bytes. Reading it as content-length overruns by two on every packet.
        length = len(content) + 2
        length_bytes = bytes(((length >> 8) & 0xFF, length & 0xFF))  # big-endian
        checksum = self._checksum(pid, length_bytes, content)
        pkt = bytearray()
        pkt += HEADER
        pkt += self.address.to_bytes(4, "big")  # big-endian throughout
        pkt.append(pid)
        pkt += length_bytes
        pkt += content
        pkt += bytes(((checksum >> 8) & 0xFF, checksum & 0xFF))
        return bytes(pkt)

    def _drain_rx(self):
        # [CXC-R503-UNSOLICITED-BYTE] inv-4: drain RX immediately before every transmit.
        # This clears a stray 0x55 (power-on / post-SoftRst) or a leftover packet so it
        # cannot phase-shift the next parse. Draining BOTH here AND parsing with a sliding
        # header window (below) is the pair the excerpt insists on — either alone is partial.
        # ASSUMED (not in excerpt) A2: read() returns b'' promptly when nothing is waiting.
        while True:
            chunk = self.port.read(256)
            if not chunk:
                break
        self._rx.clear()

    def _send(self, pid, content):
        self._drain_rx()
        self.port.write(self._build_packet(pid, content))

    def _send_command(self, opcode, args=b""):
        # A command's content is the opcode byte followed by its argument bytes.
        return self._send(PID_COMMAND, bytes([opcode]) + args)

    # ---------------------------------------------------------------------------------
    # Low-level RX — sliding two-byte header window + checksum-before-act
    # ---------------------------------------------------------------------------------
    def _read_into_buffer(self, n, deadline):
        while len(self._rx) < n:
            if time.monotonic() > deadline:
                raise R503TimeoutError(
                    "timed out waiting for %d bytes (have %d)" % (n, len(self._rx))
                )
            chunk = self.port.read(n - len(self._rx))
            if chunk:
                self._rx += chunk
            else:
                # brief idle; keep looping until deadline (A2: read may return b'')
                pass

    def read_packet(self, timeout_s=None):
        """Read exactly one framed, checksum-verified packet.

        Handles the unsolicited/unframed 0x55 and any other pre-header garbage by a
        SLIDING two-byte header window: bytes are discarded one at a time until 0xEF01
        aligns ([CXC-R503-UNSOLICITED-BYTE] inv-4). A wrong module address yields SILENCE,
        which surfaces here as R503TimeoutError, never a spurious packet
        ([CXC-R503-FRAMING] address-gating).
        """
        if timeout_s is None:
            timeout_s = self.packet_timeout_s
        deadline = time.monotonic() + timeout_s

        # 1) Slide to header alignment.
        while True:
            self._read_into_buffer(2, deadline)
            if self._rx[0] == HEADER[0] and self._rx[1] == HEADER[1]:
                break
            # Discard one byte (e.g. a lone 0x55) and re-try the window.
            del self._rx[0]

        # 2) Header + address(4) + pid(1) + length(2) = 9 bytes before content.
        self._read_into_buffer(9, deadline)
        address = int.from_bytes(self._rx[2:6], "big")
        pid = self._rx[6]
        length = (self._rx[7] << 8) | self._rx[8]  # big-endian

        # [CXC-R503-FRAMING] length-semantics: length = content + 2 checksum bytes.
        content_len = length - 2
        if content_len < 0 or content_len > self.MAX_CONTENT_LEN:
            # Corrupt/absurd length (or the illegal 256-byte case, [CXC-R503-PACKET-256]).
            # Resync rather than trust it: drop the header and slide on.
            del self._rx[0:2]
            return self.read_packet(timeout_s=max(0.0, deadline - time.monotonic()))

        total = 9 + content_len + 2
        self._read_into_buffer(total, deadline)

        content = bytes(self._rx[9:9 + content_len])
        rx_checksum = (self._rx[9 + content_len] << 8) | self._rx[9 + content_len + 1]

        # [CXC-R503-CHECKSUM] obligation (inv-5): verify BEFORE the payload is acted on.
        length_bytes = bytes((self._rx[7], self._rx[8]))
        calc = self._checksum(pid, length_bytes, content)

        # consume this packet from the buffer
        del self._rx[0:total]

        if calc != rx_checksum:
            raise R503ChecksumError(
                "checksum mismatch: got 0x%04X, computed 0x%04X" % (rx_checksum, calc)
            )

        if address != self.address:
            # Not our address. The excerpt says a wrong address produces silence FROM the
            # module; a mismatched address on a well-formed packet is unexpected, so we
            # surface it rather than silently accept it.
            raise R503Error(
                "address mismatch: packet 0x%08X vs expected 0x%08X" % (address, self.address)
            )

        return R503Packet(address, pid, content)

    def read_ack(self, timeout_s=None):
        """Read one acknowledge (PID 0x07) packet. Non-ack PIDs are surfaced as errors."""
        pkt = self.read_packet(timeout_s=timeout_s)
        if pkt.pid != PID_ACK:
            raise R503Error("expected ack (0x07), got pid 0x%02X" % pkt.pid)
        return pkt

    # ---------------------------------------------------------------------------------
    # Busy handling — [CXC-R503-BUSY]: behaviour while Busy is UNDEFINED
    # ---------------------------------------------------------------------------------
    def read_sys_para(self):
        """Send ReadSysPara (0x0F) and return the parsed ack."""
        self._send_command(OP_READ_SYS_PARA)
        return self.read_ack()

    def is_busy(self):
        """Return True if the module's Busy flag (system status bit 0) is set.

        The excerpt states the flag EXISTS (system status bit 0, via ReadSysPara) but
        does NOT give the byte layout of the status register within the ack content.
        """
        ack = self.read_sys_para()
        c = ack.content
        # ASSUMED (not in excerpt) A6: content[0] is the confirmation code, and the 16-bit
        # system-status register is the next two bytes, big-endian; Busy is bit 0.
        if len(c) < 3:
            raise R503Error("ReadSysPara ack too short to locate status register")
        status_register = (c[1] << 8) | c[2]
        return bool(status_register & 0x0001)

    def _guard_not_busy(self):
        # The excerpt: what happens to a command issued while Busy is addressed NOWHERE
        # in any revision — queued, rejected or dropped is unknown. Rather than pick one
        # blindly, the driver's POLICY is to check-then-refuse: it will not issue a new
        # transaction while Busy. This is a driver decision, NOT a protocol fact.
        if self.is_busy():
            raise R503BusyError(
                "module is Busy; behaviour of a command issued while Busy is undefined "
                "in the spec ([CXC-R503-BUSY]) — refusing to send. Use cancel() first."
            )

    def cancel(self):
        """Send Cancel (0x30). This is the one documented lever against a Busy module."""
        self._send_command(OP_CANCEL)
        return self.read_ack()

    # ---------------------------------------------------------------------------------
    # Reset / handshake — exercise the unsolicited-0x55 path
    # ---------------------------------------------------------------------------------
    def soft_reset(self):
        """Send SoftRst (0x3D). The module re-emits the unframed 0x55 after this; the
        next _send() drains it and read_packet()'s sliding window tolerates it, so a
        soft reset does NOT permanently phase-shift the parser ([CXC-R503-UNSOLICITED-BYTE])."""
        self._send_command(OP_SOFT_RST)
        try:
            ack = self.read_ack()
        except R503TimeoutError:
            ack = None
        # Absorb the post-reset 0x55 proactively so it does not lead the next transaction.
        self._drain_rx()
        return ack

    def handshake(self):
        # correction-handshake: HandShake is 0x40 (two of six surveyed drivers ship 0x53).
        self._send_command(OP_HANDSHAKE)
        return self.read_ack()

    # ---------------------------------------------------------------------------------
    # SetSysPara — used to arm Config3 before AutoEnroll
    # ---------------------------------------------------------------------------------
    def set_config(self, param_bytes):
        """Frame a SetSysPara (0x0E). The excerpt names the opcode and the fact that the
        AutoEnroll stream is enabled by *Config3* (Config1 on AutoIdentify — do NOT
        generalise the index), but gives no parameter-index/value encoding, so the caller
        supplies the raw argument bytes."""
        # ASSUMED (not in excerpt) A7: caller-supplied param_bytes carry index+value.
        self._guard_not_busy()
        self._send_command(OP_SET_SYS_PARA, bytes(param_bytes))
        return self.read_ack()

    # ---------------------------------------------------------------------------------
    # AutoEnroll — the 15-packet acknowledge STREAM ([CXC-R503-ACK-STREAM])
    # ---------------------------------------------------------------------------------
    def auto_enroll(self, args=None, on_step=None):
        """Run the AutoEnroll (0x31) flow.

        With its status bit set (Config3), ONE AutoEnroll command emits FIFTEEN ack
        packets, steps 0x01..0x0F; only the FINAL packet's fingerID field is meaningful.
        This reads the whole stream, invokes on_step(step, packet) for progress, and
        returns (final_packet, fingerID).

        NOTE — fleet-status: the excerpt records that the packaged AutoEnroll path is NOT
        USED by ruling (its mandated on-module LED sequence is unimplementable under the
        device-owned ring, [CXC-FPID-ENROLLMENT-PATH]). This method is implemented anyway
        because the excerpt retains the stream facts "for any future reader who reaches
        for the packaged command" — and it is the hazard the task asks the driver to handle.

        NO-PRIOR-ART: the excerpt warns that not one of six surveyed drivers implements
        AutoEnroll, so the stream behaviour is untested by every available implementation.
        Treat this path as unvalidated against real hardware.
        """
        # ASSUMED (not in excerpt) A3: AutoEnroll argument bytes. The excerpt gives the
        # opcode and that Config3 arms the stream, but not the argument layout. Default to
        # empty; a live caller MUST pass the real (finger-id, count, param) bytes.
        if args is None:
            args = b""

        self._guard_not_busy()
        self._send_command(OP_AUTO_ENROLL, bytes(args))

        packets = []
        final = None
        finger_id = None
        # inv-7: use the later-failing 15 s wall clock; 10 s is recorded, not used (A8).
        deadline = time.monotonic() + AUTOENROLL_NOFINGER_TIMEOUT_S

        # Loop on the stream. We DO NOT assume all 15 arrive — an early error code or the
        # no-finger timeout can end it. We stop on: final step 0x0F, count reached, a
        # non-success/non-progress terminal code, or the wall-clock no-finger timeout.
        while len(packets) < AUTOENROLL_ACK_COUNT:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # no-finger exit (excerpt): the stream did not complete in time.
                raise R503TimeoutError(
                    "AutoEnroll no-finger timeout after %.0fs (spec also records %.0fs)"
                    % (AUTOENROLL_NOFINGER_TIMEOUT_S, AUTOENROLL_NOFINGER_TIMEOUT_RECORDED_S)
                )
            pkt = self.read_ack(timeout_s=min(self.packet_timeout_s, remaining))
            packets.append(pkt)

            # ASSUMED (not in excerpt) A4: byte[0]=confirmation code, byte[1]=step field.
            code = pkt.confirmation_code
            step = pkt.content[1] if len(pkt.content) > 1 else None
            if on_step is not None:
                on_step(step, pkt)

            # A confirmation code that is neither success (0x00) nor a documented "still
            # working" progress state ends the stream. Per [CXC-R503-CODES] we do NOT remap
            # an undocumented code; we surface it and stop, treating non-zero as terminal
            # for the flow (the module has reported it cannot proceed).
            if code not in (0x00,):
                final = pkt
                break

            if step == AUTOENROLL_LAST_STEP:
                final = pkt
                break

        if final is None and packets:
            final = packets[-1]

        # ASSUMED (not in excerpt) A5: fingerID is a 2-byte big-endian field right after
        # the step byte in the FINAL packet only.
        if final is not None and len(final.content) >= 4:
            finger_id = (final.content[2] << 8) | final.content[3]

        return final, finger_id, packets

    # ---------------------------------------------------------------------------------
    # Data-packet BURSTS ([CXC-R503-DATA-BURSTS])
    # ---------------------------------------------------------------------------------
    def read_data_stream(self, expected_bytes=None, timeout_s=None):
        """After a command that returns image/template data, read the ACK then the data
        packets and return the concatenated payload.

        The packet COUNT depends on the negotiated packet-size parameter, NOT on the
        command. Termination is by the 0x08 end-of-data PID (the excerpt's first,
        PID-based option); if the caller knows the configured total it may pass
        expected_bytes as a secondary bound. We deliberately do NOT scan the byte stream
        for 0xEF01 boundaries — the excerpt names that as the bug that mis-splits on
        payload bytes that happen to equal 0xEF01.
        """
        # First packet is the acknowledge.
        ack = self.read_ack(timeout_s=timeout_s)
        payload = bytearray()

        while True:
            pkt = self.read_packet(timeout_s=timeout_s)
            if pkt.pid == PID_DATA:
                payload += pkt.content
            elif pkt.pid == PID_END_OF_DATA:
                # Final chunk rides on the end-of-data packet too.
                payload += pkt.content
                break
            else:
                raise R503Error(
                    "unexpected pid 0x%02X mid data-stream" % pkt.pid
                )
            # Secondary bound (caller's buffer is the binding check per PACKET-256).
            if expected_bytes is not None and len(payload) >= expected_bytes:
                break

        return ack, bytes(payload)
