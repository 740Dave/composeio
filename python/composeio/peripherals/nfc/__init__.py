"""composeio.peripherals.nfc — PN532 NFC input peripheral.

An NFC reader is an INPUT peripheral ([SPEC:CXC-NFC], roadmap [CXC-RM-NFC]):
it sees a tag and publishes a semantic namespace (presence + uid + optional
NDEF) onto the ComposeBus. This package ships a hardware-free PN532 protocol
driver over an injectable I2C transport, plus a debounced poller that binds
tag events to namespace publishes.

Quick start (real hardware, RPi/MinnowBoard with I2C enabled)::

    from composeio import ComposeIOBus
    from composeio.peripherals.nfc import PN532, NfcPoller
    from composeio.peripherals.nfc.transport import Smbus2I2CTransport

    bus = ComposeIOBus(); bus.start()
    reader = PN532(Smbus2I2CTransport(address=0x24, bus=1))
    poller = NfcPoller(reader, bus, prefix="nfc")
    poller.begin()
    poller.run(interval=0.2)   # publishes nfc/present, nfc/uid

Protocol self-test (no hardware; macOS-safe)::

    python -m composeio.peripherals.nfc --selftest
"""

from __future__ import annotations

from .frames import (
    ACK_FRAME,
    FrameError,
    build_command_frame,
    build_response_frame,
    parse_response_frame,
)
from .pn532 import PN532, PN532Error
from .poller import NfcPoller, PublishSink, uid_to_hex
from .transport import (
    PN532_I2C_ADDRESS,
    I2CTransport,
    MockI2CTransport,
    Smbus2I2CTransport,
)

__all__ = [
    "PN532",
    "PN532Error",
    "NfcPoller",
    "PublishSink",
    "uid_to_hex",
    "I2CTransport",
    "MockI2CTransport",
    "Smbus2I2CTransport",
    "PN532_I2C_ADDRESS",
    "FrameError",
    "ACK_FRAME",
    "build_command_frame",
    "build_response_frame",
    "parse_response_frame",
]
