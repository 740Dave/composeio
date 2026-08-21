"""WLED DRIVEN adaptor — bring an existing WLED fleet onto ComposeBus, zero firmware.

[CXC-HAL-ACTUATE] (spec 27, [SPEC:CXC-HAL]): WLED is already MQTT-native and
speaks a JSON API (`/json/state`), so a WLED device is bus-adjacent. This
package is integration rung (1), the "driven semantic adaptor": a ComposeBus
output adaptor (Output Tier 2) that subscribes SEMANTIC namespaces
(`light/on`, `light/brightness`, `light/color`, `scene/mood`) and drives WLED
via HTTP (primary) or MQTT (optional). Pure client -- no WLED firmware fork.

This is NOT the pixel-stream render surface ([CXC-RM-WLED], rung 2).

    from composeio import ComposeIOBus
    from composeio.adaptors.wled import WLEDAdaptor, WLEDHTTPSender

    bus = ComposeIOBus()
    bus.register_namespace("light/brightness", "continuous")
    WLEDAdaptor(bus, WLEDHTTPSender("192.0.2.60")).attach()
    bus.publish_state("light/brightness", 0.5)   # -> POST /json/state {"bri":128}

Self-test (fully software, no real WLED needed):

    python -m composeio.adaptors.wled --selftest
"""

from composeio.adaptors.wled.mapping import (
    WLEDMapping,
    DEFAULT_MAPPING,
    register_kind,
    parse_color,
    to_bri,
    coerce_bool,
)
from composeio.adaptors.wled.senders import (
    WLEDSender,
    WLEDHTTPSender,
    WLEDMQTTSender,
)
from composeio.adaptors.wled.adaptor import WLEDAdaptor

__all__ = [
    "WLEDAdaptor",
    "WLEDMapping",
    "DEFAULT_MAPPING",
    "register_kind",
    "parse_color",
    "to_bri",
    "coerce_bool",
    "WLEDSender",
    "WLEDHTTPSender",
    "WLEDMQTTSender",
]
