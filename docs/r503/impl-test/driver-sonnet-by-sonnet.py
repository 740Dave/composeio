"""
R503UartDriver — UART command/response driver for the R503 fingerprint module.

Built ONLY from the excerpt at:
    ${DATASHEETS_HOME}/R503/docs/impl-test/sonnet-wire-excerpt.lts

That excerpt covers packet framing, an unsolicited-byte hazard, an
acknowledge-stream hazard, a data-packet-burst hazard, and an undefined
Busy-behavior hazard. It does NOT contain opcode bytes, confirmation-code
values, PID-type values, a checksum formula, or timing figures. Every one
of those had to be assumed to produce runnable code. Every assumption is
flagged both here and inline with `# ASSUMED (not in excerpt): ...`.

=====================================================================
ASSUMPTIONS (nothing below this point came from the excerpt)
=====================================================================
 1. PID byte values for packet TYPE: command=0x01, data=0x02,
    acknowledge=0x07, end-of-data=0x08. The excerpt names a "PID 1B"
    framing field and separately talks about "acknowledge packets",
    "data packets" and "the end-of-data PID" but never gives the
    numeric value tied to any of those names.
 2. Default/broadcast module address: 0xFFFFFFFF. The excerpt says a
    wrong address produces silence but never states a default.
 3. Checksum ALGORITHM: 16-bit unsigned sum, mod 0x10000, of every
    byte from PID through the end of Content (i.e. PID + Length +
    Content bytes), big-endian on the wire. The excerpt confirms a
    checksum rule EXISTS and was verified against 21 worked examples,
    but never states the formula — this is the single largest gap for
    a driver that must actually interoperate with real hardware, and
    if wrong, every packet this module sends will be rejected as a
    bad checksum by a real device.
 4. Confirmation-code convention: 0x00 == success, non-zero == a
    device-reported error. The excerpt only says "a non-zero
    confirmation code" is one of two ways AutoEnroll-stream
    termination is inferred; it never states that 0x00 means success,
    that inference is standard practice, not textual.
 5. AutoEnroll's opcode byte and its payload layout (finger ID /
    enroll-count / parameter bitmask) — not given anywhere. A
    placeholder opcode is defined and clearly marked; a real one from
    an actual command-set table would replace it.
 6. The config-bit INDEX that enables AutoEnroll's status-report
    stream, and the (different, per the excerpt) index for
    AutoIdentify — the excerpt states these two indices differ but
    gives neither value. Modeled as an opaque bitmask the caller
    supplies; this driver does not hardcode or guess either index,
    specifically to avoid the generalization mistake the excerpt
    warns against.
 7. The "specific terminal step value" that (with a non-zero
    confirmation code) can end the AutoEnroll ack stream — not
    enumerated. Its position in the payload is assumed to be
    payload[1] ("step"); its terminal values are caller-supplied.
 8. Busy status: which byte/bit of which response reports Busy — not
    given (the excerpt only says the bit exists). Not decoded by this
    driver; see the Busy-handling note below.
 9. Cancel and SoftRst command opcodes — not given (excerpt only says
    they exist).
10. Read / inter-byte timeouts — not given; conservative defaults are
    used and are all caller-overridable.
11. Transport semantics for `self.port.read(n)`: assumed to return
    immediately with whatever bytes are currently available (possibly
    b'', possibly fewer than n), i.e. a non-blocking or short-timeout
    stream — never a hard block until exactly n bytes arrive. This
    comes from the task's transport contract, not the excerpt, and is
    called out because the drain/read loops below are built on it.
12. The five template/image-transfer commands' opcodes, and the
    system parameter carrying "negotiated packet size" — not given.
    Consequently N (the data-packet count) is never computed from
    config; `read_data_burst` reads until the end-of-data PID, per
    the excerpt's own instruction ("terminate on the end-of-data PID,
    never by counting").
=====================================================================
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterator, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Wire constants
# ---------------------------------------------------------------------------

HEADER = b"\xEF\x01"  # [CXC-R503C-FRAMING]: given directly by the excerpt.

# ASSUMED (not in excerpt): PID byte values. The excerpt names these packet
# kinds ("acknowledge packets", "data packets", "the end-of-data PID") but
# never gives their numeric encoding.
PID_COMMAND = 0x01
PID_DATA = 0x02
PID_ACK = 0x07
PID_END_OF_DATA = 0x08

# ASSUMED (not in excerpt): default module address.
DEFAULT_ADDRESS = 0xFFFFFFFF

# ASSUMED (not in excerpt): 0x00 == success convention for confirmation codes.
CONFIRM_OK = 0x00

# ASSUMED (not in excerpt): opcode bytes. Placeholders only — a real
# command-set table would replace these. Present so the AutoEnroll flow and
# soft_reset() are runnable end to end rather than stubs.
AUTO_ENROLL_OPCODE = 0x31  # ASSUMED placeholder
SOFT_RESET_OPCODE = 0x3D  # ASSUMED placeholder
CANCEL_OPCODE = 0x30  # ASSUMED placeholder

# [CXC-R503C-ACK-STREAM]: "up to fifteen acknowledge packets" is given.
AUTO_ENROLL_MAX_ACK_PACKETS = 15


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class R503Error(Exception):
    """Base class for all driver-raised errors."""


class R503TimeoutError(R503Error):
    """No data (or not enough data) arrived within the read deadline."""


class R503ChecksumError(R503Error):
    """A framed packet's checksum did not match its content."""


class R503ProtocolError(R503Error):
    """A framed packet was structurally invalid (e.g. Length < 2)."""


class R503ProtocolDesyncError(R503Error):
    """
    [CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED]: the excerpt states a Busy status
    bit and a Cancel command both exist, but no revision says whether a
    command sent while Busy is queued, rejected, or dropped. Rather than
    invent a behavior, this driver follows the spec's own stated
    consequence and buckets ANY unexpected response — timeout, bad
    checksum, malformed frame, wrong packet type, mismatched address — into
    this single closed failure class. A caller that wants Busy-aware
    retries must supply that policy itself; this driver will not guess it.
    """


# ---------------------------------------------------------------------------
# Packet model
# ---------------------------------------------------------------------------

@dataclass
class R503Packet:
    address: int
    pid: int
    payload: bytes

    @property
    def confirmation_code(self) -> Optional[int]:
        # ASSUMED (not in excerpt): confirmation code is payload[0] on an
        # acknowledge packet.
        return self.payload[0] if self.payload else None

    @property
    def step_value(self) -> Optional[int]:
        # ASSUMED (not in excerpt): AutoEnroll's "terminal step value" lives
        # at payload[1]. Position and encoding are not given by the
        # excerpt; this is a placeholder a real command-set table would fix.
        return self.payload[1] if len(self.payload) > 1 else None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class R503UartDriver:
    """
    UART command/response driver for the R503, built from
    sonnet-wire-excerpt.lts alone.

    Transport contract (given by the task, not the excerpt):
        self.port.read(n)  -> bytes, up to n, possibly fewer, possibly b''
        self.port.write(b) -> None
    """

    def __init__(self, port, address: int = DEFAULT_ADDRESS,
                 enforce_address_match: bool = True):
        self.port = port
        self.address = address
        # ASSUMED design choice (not in excerpt): treat a response whose
        # address field doesn't match ours as an anomaly worth desyncing on,
        # rather than silently accepting it. The excerpt only documents the
        # opposite direction (host sends wrong address -> device stays
        # silent); it says nothing about how to treat a mismatched address
        # on an incoming frame.
        self.enforce_address_match = enforce_address_match

    # -- framing ------------------------------------------------------

    @staticmethod
    def _checksum(body: bytes) -> int:
        # ASSUMED (not in excerpt): checksum = 16-bit sum, mod 0x10000, of
        # PID + Length + Content. `body` here is exactly those bytes.
        return sum(body) & 0xFFFF

    def _build_packet(self, pid: int, payload: bytes) -> bytes:
        # [CXC-R503C-FRAMING]: Length counts the checksum -> content + 2.
        length = len(payload) + 2
        body = bytes([pid]) + length.to_bytes(2, "big") + payload
        checksum = self._checksum(body)
        return (
            HEADER
            + self.address.to_bytes(4, "big")
            + body
            + checksum.to_bytes(2, "big")
        )

    # -- low-level I/O --------------------------------------------------

    def _read_exact(self, n: int, deadline: float) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            if time.monotonic() > deadline:
                raise R503TimeoutError(
                    f"timed out waiting for {n} bytes (got {len(buf)})"
                )
            chunk = self.port.read(n - len(buf))
            if chunk:
                buf.extend(chunk)
        return bytes(buf)

    def _sync_to_header(self, deadline: float) -> None:
        """
        [CXC-R503C-UNSOLICITED-0X55] required-remedy, half 2 of 2: "parse
        with a sliding two-byte header window." A lone 0x55 (boot byte,
        post-SoftRst byte, or any other stray byte) can never match
        HEADER == 0xEF01 in a two-byte sliding window, so it is silently
        shifted out here instead of desyncing the parser.
        """
        window = bytearray(b"\x00\x00")
        while True:
            if time.monotonic() > deadline:
                raise R503TimeoutError("no packet header observed before deadline")
            b = self.port.read(1)
            if not b:
                continue
            window[0] = window[1]
            window[1] = b[0]
            if bytes(window) == HEADER:
                return

    def _drain_rx(self, max_drain_time: float = 0.05) -> None:
        """
        [CXC-R503C-UNSOLICITED-0X55] required-remedy, half 1 of 2: "drain
        RX immediately before every transmit." Absorbs the power-on byte
        (drained before the very first command any caller issues), the
        post-SoftRst byte, and any stale packet left over from an aborted
        transaction.
        """
        deadline = time.monotonic() + max_drain_time
        while time.monotonic() < deadline:
            chunk = self.port.read(64)
            if not chunk:
                break

    def _read_packet(self, timeout: float = 2.0) -> R503Packet:
        deadline = time.monotonic() + timeout
        self._sync_to_header(deadline)
        addr_b = self._read_exact(4, deadline)
        pid_b = self._read_exact(1, deadline)
        len_b = self._read_exact(2, deadline)
        length = int.from_bytes(len_b, "big")

        if length < 2:
            # [CXC-R503C-FRAMING]: Length counts the checksum, so the
            # minimum legal value is 2 (empty content).
            raise R503ProtocolError(
                f"Length field {length} < 2; cannot contain a checksum"
            )
        content_len = length - 2

        content = self._read_exact(content_len, deadline)
        checksum_b = self._read_exact(2, deadline)
        checksum_received = int.from_bytes(checksum_b, "big")

        body = pid_b + len_b + content
        checksum_expected = self._checksum(body)
        if checksum_received != checksum_expected:
            raise R503ChecksumError(
                f"checksum mismatch: got 0x{checksum_received:04x}, "
                f"expected 0x{checksum_expected:04x}"
            )

        address = int.from_bytes(addr_b, "big")
        if self.enforce_address_match and address != self.address:
            raise R503ProtocolError(
                f"response address 0x{address:08x} != configured "
                f"0x{self.address:08x}"
            )

        return R503Packet(address=address, pid=pid_b[0], payload=content)

    # -- command/response, with the Busy hazard bucketed -----------------

    def _send_command(self, payload: bytes) -> None:
        self._drain_rx()
        self.port.write(self._build_packet(PID_COMMAND, payload))

    def execute_command(self, payload: bytes, timeout: float = 2.0) -> R503Packet:
        """
        (a) Send a command packet and read+parse its response.

        [CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED]: since the excerpt documents
        no defined behavior for a command issued while Busy, this method
        does not attempt to detect or special-case Busy at all — it can't,
        the excerpt never says which bit that is. Instead, ANY response
        that fails to parse cleanly (timeout, bad checksum, malformed
        frame, wrong PID, wrong address) is raised as
        R503ProtocolDesyncError, which is exactly the closed-failure-
        taxonomy treatment the excerpt's own consequence note describes.
        """
        self._send_command(payload)
        try:
            pkt = self._read_packet(timeout=timeout)
        except (R503TimeoutError, R503ChecksumError, R503ProtocolError) as exc:
            raise R503ProtocolDesyncError(
                f"unexpected/absent response to command "
                f"(possibly issued while module was Busy — undefined per "
                f"[CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED]): {exc}"
            ) from exc
        if pkt.pid != PID_ACK:
            raise R503ProtocolDesyncError(
                f"expected an acknowledge packet (pid=0x{PID_ACK:02x}), "
                f"got pid=0x{pkt.pid:02x}"
            )
        return pkt

    # -- [CXC-R503C-DATA-PACKET-BURSTS] -----------------------------------

    def read_data_burst(self, timeout: float = 5.0) -> List[R503Packet]:
        """
        (part of (a)) Read the data-packet burst that follows an
        acknowledge for one of the template/image-transfer commands.

        [CXC-R503C-DATA-PACKET-BURSTS]: N is set by a negotiated
        packet-size system parameter, not derivable from the command
        itself, so this NEVER counts packets — it reads until it observes
        the end-of-data PID, exactly as the excerpt instructs.
        """
        packets: List[R503Packet] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            try:
                pkt = self._read_packet(timeout=remaining)
            except (R503TimeoutError, R503ChecksumError, R503ProtocolError) as exc:
                raise R503ProtocolDesyncError(
                    f"data burst interrupted after {len(packets)} packet(s): {exc}"
                ) from exc
            if pkt.pid not in (PID_DATA, PID_END_OF_DATA):
                raise R503ProtocolDesyncError(
                    f"expected a data or end-of-data packet, got pid=0x{pkt.pid:02x}"
                )
            packets.append(pkt)
            if pkt.pid == PID_END_OF_DATA:
                return packets

    # -- [CXC-R503C-ACK-STREAM] -------------------------------------------

    def auto_enroll(
        self,
        finger_id: int,
        enroll_count: int = 1,
        status_report_bitmask: int = 0x0000,
        terminal_step_values: Sequence[int] = (),
        ack_timeout: float = 5.0,
    ) -> Iterator[R503Packet]:
        """
        (b) Run the AutoEnroll enrollment flow.

        [CXC-R503C-ACK-STREAM]: with its status-reporting config bit set,
        AutoEnroll emits up to fifteen acknowledge packets from ONE
        command. There is no explicit end-of-stream marker — termination
        is inferred from EITHER a non-zero confirmation code OR the
        packet reaching a specific terminal step value.

        `status_report_bitmask` is caller-supplied and opaque to this
        driver on purpose: the excerpt states AutoEnroll's status-report
        bit sits at a DIFFERENT config index than AutoIdentify's sibling
        bit, but gives neither index's value. Hardcoding either would
        repeat exactly the generalization mistake the excerpt calls out.

        `terminal_step_values` is caller-supplied for the same reason —
        the "specific terminal step value" is named but never enumerated.

        Yields each ack packet as it arrives (useful for a progress UI)
        and stops on whichever terminal condition fires first. If neither
        condition fires within the documented ceiling of 15 packets, that
        is itself an undocumented state and is raised as
        R503ProtocolDesyncError rather than silently returning.
        """
        # ASSUMED (not in excerpt): AutoEnroll payload layout —
        # [opcode][finger_id][enroll_count][bitmask hi][bitmask lo].
        payload = bytes([AUTO_ENROLL_OPCODE, finger_id & 0xFF, enroll_count & 0xFF])
        payload += status_report_bitmask.to_bytes(2, "big")

        first = self.execute_command(payload, timeout=ack_timeout)
        yield first
        if self._is_autoenroll_terminal(first, terminal_step_values):
            return

        for _ in range(AUTO_ENROLL_MAX_ACK_PACKETS - 1):
            try:
                pkt = self._read_packet(timeout=ack_timeout)
            except (R503TimeoutError, R503ChecksumError, R503ProtocolError) as exc:
                raise R503ProtocolDesyncError(
                    f"AutoEnroll ack stream interrupted: {exc}"
                ) from exc
            if pkt.pid != PID_ACK:
                raise R503ProtocolDesyncError(
                    f"AutoEnroll ack stream: expected pid=0x{PID_ACK:02x}, "
                    f"got 0x{pkt.pid:02x}"
                )
            yield pkt
            if self._is_autoenroll_terminal(pkt, terminal_step_values):
                return

        # Hit the documented ceiling with no termination signal observed.
        raise R503ProtocolDesyncError(
            f"AutoEnroll produced {AUTO_ENROLL_MAX_ACK_PACKETS} acknowledge "
            f"packets with neither a non-zero confirmation code nor a "
            f"terminal step value observed — undocumented state per "
            f"[CXC-R503C-ACK-STREAM]'s 'up to fifteen' ceiling"
        )

    @staticmethod
    def _is_autoenroll_terminal(
        pkt: R503Packet, terminal_step_values: Sequence[int]
    ) -> bool:
        if pkt.confirmation_code is not None and pkt.confirmation_code != CONFIRM_OK:
            return True
        if pkt.step_value is not None and pkt.step_value in terminal_step_values:
            return True
        return False

    # -- housekeeping (opcodes ASSUMED; see module docstring) -------------

    def soft_reset(self, drain_timeout: float = 0.5) -> None:
        """
        Issue SoftRst. [CXC-R503C-UNSOLICITED-0X55] documents that a
        SoftRst is followed by another unframed 0x55 — this method does
        not attempt to parse a normal acknowledge for that byte (the
        excerpt never says SoftRst produces one); it fires the command and
        then explicitly drains, in addition to the automatic
        drain-before-next-transmit every other method already performs.
        """
        self._send_command(bytes([SOFT_RESET_OPCODE]))
        self._drain_rx(max_drain_time=drain_timeout)

    def cancel(self, timeout: float = 2.0) -> R503Packet:
        """
        Issue Cancel. The excerpt names Cancel only to say its interaction
        with Busy is part of the undocumented hazard — this method makes
        no Busy-specific claim and goes through the same
        parse-or-desync path as any other command.
        """
        return self.execute_command(bytes([CANCEL_OPCODE]), timeout=timeout)
