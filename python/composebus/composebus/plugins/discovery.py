"""discovery — module discovery announce (`_bus/discovery`), SHED from the kernel.

The kernel used to send the `_bus/discovery` DISCOVERY message itself inside
``register_module`` ([DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23] extraction 4).
Module registration is legitimately kernel (module lifecycle); ANNOUNCING the
registration on the bus is a plugin concern. This engine observes registrations
through the sanctioned module-registration hook and emits the identical envelope,
so behavior is preserved for a bare ``ComposeIOBus()``.
"""
from __future__ import annotations

import logging

from composebus.plugin import ComposePlugin
from composebus.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.plugins.discovery")


class DiscoveryEngine:
    """Emits `_bus/discovery` on every module registration."""

    def __init__(self, bus):
        self._bus = bus

    def install(self) -> None:
        self._bus.register_module_hook(self.on_module_registered)

    def on_module_registered(self, module) -> None:
        self._bus.send_message(BusMessage(
            namespace="_bus/discovery",
            type=MessageType.DISCOVERY,
            source=module.module_id,
            data=module.discovery_info(),
            scope="local",
        ))


class DiscoveryPlugin(ComposePlugin):
    """Module discovery wearing the plugin contract ([CXC-CS-PLUGIN-CONTRACT])."""

    class Config(ComposePlugin.Config):
        pass

    name = "discovery"
    version = "1.0.0"
    default_loaded = True
    publishes = ["_bus/discovery"]

    def start(self, bus) -> None:
        self._bus = bus
        install_default(bus)


def install_default(bus) -> DiscoveryEngine:
    """Create + wire the default discovery engine on a bus, idempotently."""
    engine = getattr(bus, "_discovery_engine", None)
    if engine is None:
        engine = DiscoveryEngine(bus)
        bus._discovery_engine = engine
        engine.install()
    return engine
