"""I2C transport seam for the PN532 driver.

[SPEC:CXC-NFC] [CXC-NFC-TRANSPORT]: which reader a device has, and how it is
wired, is a board-profile fact. The driver therefore talks to an *injectable*
transport interface — never directly to a bus library — so the same protocol
logic runs against real silicon on a Pi/MinnowBoard and against canned frames
in a test on macOS.

Two implementations ship:
  - :class:`Smbus2I2CTransport` — the real RPi/Linux transport, wrapping
    ``smbus2`` via a LAZY import (this module imports fine on macOS with no
    smbus2 and no I2C hardware present).
  - :class:`MockI2CTransport` — a scripted, hardware-free transport that
    replays canned PN532 read frames for the self-test.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# The PN532's fixed 7-bit I2C slave address (David's rig).
PN532_I2C_ADDRESS = 0x24


@runtime_checkable
class I2CTransport(Protocol):
    """Minimal byte-level I2C interface the PN532 driver depends on.

    Both methods act on the device address bound at construction time. This is
    the whole hardware seam: swap the implementation, keep the protocol logic.
    """

    def write(self, data: bytes) -> None:
        """Write *data* to the device in a single I2C transaction."""
        ...

    def read(self, length: int) -> bytes:
        """Read exactly *length* bytes from the device in one transaction.

        Over PN532 I2C every read transaction is prefixed by a status/ready
        byte (bit0 == 1 => ready); callers decide whether to inspect or drop it.
        """
        ...


class Smbus2I2CTransport:
    """Real I2C transport backed by ``smbus2`` (RPi / Linux).

    smbus2 is imported LAZILY inside ``__init__`` so importing this module
    (and the whole ``composeio.peripherals.nfc`` package) never requires the
    dependency or any hardware — critical for authoring/testing on macOS.

    Uses ``i2c_rdwr`` with raw ``i2c_msg`` blocks rather than the SMBus
    block-read primitives, because the PN532 frame is an opaque byte stream,
    not a register-addressed SMBus payload.
    """

    def __init__(self, address: int = PN532_I2C_ADDRESS, bus: int = 1):
        try:
            from smbus2 import SMBus, i2c_msg  # lazy/optional (RPi only)
        except ImportError as exc:  # pragma: no cover - exercised only off-Pi
            raise RuntimeError(
                "Smbus2I2CTransport requires the 'smbus2' package and a Linux "
                "I2C bus. Install it on the target (pip install smbus2) and "
                "enable I2C. On macOS use MockI2CTransport for protocol tests."
            ) from exc
        self._address = address
        self._i2c_msg = i2c_msg
        self._smbus = SMBus(bus)

    def write(self, data: bytes) -> None:
        msg = self._i2c_msg.write(self._address, bytes(data))
        self._smbus.i2c_rdwr(msg)

    def read(self, length: int) -> bytes:
        msg = self._i2c_msg.read(self._address, length)
        self._smbus.i2c_rdwr(msg)
        return bytes(msg)

    def close(self) -> None:
        self._smbus.close()


class MockI2CTransport:
    """Scripted, hardware-free I2C transport for protocol self-tests.

    The PN532 I2C read contract is: every read transaction returns a leading
    status byte (0x01 == ready) followed by frame bytes. A driver command
    therefore reads, in order:

        1. read(1)              -> status (ready poll)
        2. read(len(ACK)+1)     -> status + 6-byte ACK
        3. read(1)              -> status (ready poll)
        4. read(frame_len+1)    -> status + response frame

    :meth:`queue_command_exchange` enqueues exactly that four-read sequence for
    one command/response round-trip, so tests script at the *frame* level and
    never hand-count reads. :meth:`queue_not_ready` injects a not-ready poll.
    Writes are recorded in :attr:`writes` for assertions.
    """

    READY = b"\x01"
    NOT_READY = b"\x00"
    ACK = bytes([0x00, 0x00, 0xFF, 0x00, 0xFF, 0x00])

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self._reads: list[bytes] = []

    # --- scripting API (test-facing) ---

    def queue_read(self, data: bytes) -> None:
        """Enqueue one raw response for the next ``read`` call."""
        self._reads.append(bytes(data))

    def queue_not_ready(self, times: int = 1) -> None:
        """Enqueue *times* not-ready status polls before the next ready."""
        for _ in range(times):
            self.queue_read(self.NOT_READY)

    def queue_command_exchange(self, response_frame: bytes) -> None:
        """Enqueue a full ack + response round-trip for one command.

        *response_frame* is the complete PN532->host wire frame (as built by
        :func:`frames.build_response_frame`).
        """
        self.queue_read(self.READY)                       # ack ready poll
        self.queue_read(self.READY + self.ACK)            # status + ACK
        self.queue_read(self.READY)                       # response ready poll
        self.queue_read(self.READY + bytes(response_frame))  # status + frame

    # --- I2CTransport implementation ---

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    def read(self, length: int) -> bytes:
        if not self._reads:
            raise AssertionError(
                "MockI2CTransport read underflow: driver read more than was "
                "scripted (check queue_command_exchange calls)"
            )
        return self._reads.pop(0)

    @property
    def drained(self) -> bool:
        """True once every scripted read has been consumed."""
        return not self._reads
