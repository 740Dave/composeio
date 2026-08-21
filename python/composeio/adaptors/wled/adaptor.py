"""WLEDAdaptor — a bus-native DRIVEN output adaptor for WLED devices.

[CXC-HAL-ACTUATE], integration rung (1) "driven semantic adaptor": works with
the whole existing WLED fleet TODAY, zero firmware. Subscribes SEMANTIC
namespaces on a ComposeIOBus and, on every state change, maps the value to a
WLED JSON state fragment (via WLEDMapping) and ships it through a WLEDSender
(HTTP primary, MQTT optional).

This is the OUTPUT dual of a Tier-1/2 HAL sensor: sensor = read pin -> publish
namespace; adaptor = subscribe namespace -> drive device. "Define once, actuate
many": the same `lighting/wash` namespace can drive this WLED strip, an
on-screen preview, and a DMX fixture.

NOT the pixel-stream render surface ([CXC-RM-WLED]) -- this is the driven side.
"""

from __future__ import annotations

import logging
from typing import Any

from composeio.adaptors.wled.mapping import WLEDMapping
from composeio.adaptors.wled.senders import (
    WLEDSender,
    WLEDHTTPSender,
    WLEDMQTTSender,
)

logger = logging.getLogger("composeio.adaptors.wled")


class WLEDAdaptor:
    """Bind semantic ComposeBus namespaces to a WLED device.

    Args:
        bus:     a ComposeIOBus (anything exposing `subscribe(ns, cb)` where
                 cb is called `(namespace, new_value, old_value)`).
        sender:  a WLEDSender (HTTP or MQTT). If None, build one from `host`
                 (HTTP) -- see `from_config`.
        mapping: a WLEDMapping (declarative semantic->WLED). Defaults to the
                 generic DEFAULT_MAPPING.
        source:  publisher id used for any diagnostics.
    """

    def __init__(
        self,
        bus: Any,
        sender: WLEDSender,
        mapping: WLEDMapping | None = None,
        source: str = "wled-adaptor",
    ):
        self.bus = bus
        self.sender = sender
        self.mapping = mapping or WLEDMapping()
        self.source = source
        self._attached = False
        self._last_sent: dict | None = None

    # --- lifecycle --------------------------------------------------------

    def attach(self) -> "WLEDAdaptor":
        """Subscribe every mapped namespace on the bus. Idempotent."""
        if self._attached:
            return self
        for ns in self.mapping.namespaces():
            self.bus.subscribe(ns, self._on_change)
        self._attached = True
        logger.info(
            "WLEDAdaptor attached: %d namespaces -> %r",
            len(self.mapping.namespaces()),
            self.sender,
        )
        return self

    def detach(self) -> None:
        """Best-effort unsubscribe (bus permitting) and close the sender."""
        if self._attached:
            for ns in self.mapping.namespaces():
                nsobj = getattr(self.bus, "_namespaces", {}).get(ns) if hasattr(self.bus, "_namespaces") else None
                if nsobj is not None:
                    try:
                        nsobj.unsubscribe(self._on_change)
                    except Exception:
                        pass
            self._attached = False
        self.sender.close()

    # --- dispatch ---------------------------------------------------------

    def _on_change(self, namespace: str, new_value: Any, old_value: Any = None) -> None:
        """Bus subscriber callback: map the change and drive WLED."""
        state = self.render(namespace, new_value)
        if state:
            self._last_sent = state
            self.sender.send(state)

    def render(self, namespace: str, value: Any) -> dict:
        """Pure map: semantic (namespace, value) -> WLED state fragment."""
        return self.mapping.render(namespace, value)

    # --- construction helpers --------------------------------------------

    @classmethod
    def from_config(cls, bus: Any, config: dict) -> "WLEDAdaptor":
        """Build an adaptor from a plain config dict (e.g. loaded YAML).

        config = {
          "transport": "http" | "mqtt",
          "host": "192.0.2.60",              # http
          "device_topic": "wled/livingroom",   # mqtt
          "broker": "localhost", "port": 1883, # mqtt
          "mapping": { "<namespace>": {<spec>} }  # optional overrides
        }
        """
        transport = config.get("transport", "http")
        mapping = WLEDMapping.from_config(config)
        if transport == "mqtt":
            sender: WLEDSender = WLEDMQTTSender(
                device_topic=config["device_topic"],
                broker=config.get("broker", "localhost"),
                port=config.get("port", 1883),
                client_id=config.get("client_id", "composeio-wled"),
                username=config.get("username"),
                password=config.get("password"),
            )
        else:
            sender = WLEDHTTPSender(
                host=config["host"],
                timeout=config.get("timeout", 3.0),
            )
        return cls(bus, sender, mapping=mapping)

    def describe(self) -> dict:
        """Introspection: what this adaptor subscribes and where it sends."""
        return {
            "adaptor": "wled",
            "namespaces": self.mapping.namespaces(),
            "sender": repr(self.sender),
            "attached": self._attached,
        }
