"""composeio.peripherals — platform peripheral drivers.

A peripheral is a board-profile fact ([SPEC:CXC-NFC] [CXC-NFC-TRANSPORT]):
an INPUT peripheral publishes a semantic namespace on the ComposeBus; an
OUTPUT peripheral actuates one. This package holds reusable, platform-pure
drivers (Tier-2 HAL citizens, [CXC-RM-HAL]) that turn hardware signals into
namespace publishes.

Currently shipped:
  - ``nfc`` — a PN532 NFC reader over an injectable I2C transport
    ([SPEC:CXC-NFC], roadmap [CXC-RM-NFC]).
"""
