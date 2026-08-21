"""The host-side per-peer instantiator + the two-bus membrane
([CXC-HALD-DECLARE] runtime / [CXC-HALD-BRIDGE]).

A :class:`HalRuntime` takes a parsed + validated board profile and brings the
``on: host`` subset to life on a :class:`~composeio.app.ComposeApp`:

  1. VALIDATE the whole profile ([CXC-HALD-VALIDATE]) — fail before wiring.
  2. Instantiate PORT-PROVIDERS first ([CXC-HALD-DECLARE] runtime order), so a
     peripheral addressed through one resolves its provider at wiring time.
  3. Instantiate each peripheral, wiring driver<->bus per role
     ([CXC-HALD-ROLE]): sensor -> publish; actuator -> subscribe;
     transactional -> cmd/status/result.
  4. Compile ``rules:`` on the internal bus ([CXC-HALD-RULES]).
  5. Wire ``bridge:`` export/import to ComposeApp ([CXC-HALD-BRIDGE]) — a
     derived local namespace promotes to the external bus; an imported
     external namespace surfaces internally to drive a local actuator.

The internal bus is the MCU's private bus; the external bus is the ComposeBus.
Using ComposeApp gives us the membrane for free (``export`` / ``import`` +
``Scope``), exactly as [CXC-HALD-BRIDGE] prescribes.  Everything is host-side
and hardware-free: the drivers record/mock, so the whole two-bus flow runs on a
laptop before any firmware or PCB.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from composeio.app import ComposeApp

from .model import BoardProfile, Peripheral
from .registry import DriverRegistry, default_registry
from .rules import LeafRuleEngine
from .topology import parse_path
from .validator import validate

logger = logging.getLogger("composeio.hal.runtime")


class _HostContext:
    """The duck-typed context handed to a driver's instantiator (see
    :mod:`composeio.hal.drivers`)."""

    def __init__(self, bus: Any, runtime: "HalRuntime"):
        self.bus = bus
        self._runtime = runtime
        self.log = logging.getLogger("composeio.hal.driver")

    def register_ns(self, name: str) -> None:
        """Ensure a permissive (json) namespace on the internal bus — a HAL
        namespace value may be a scalar OR a structured record
        ([CXC-HALD-NAMESPACE])."""
        self.bus.ensure_namespace(name, "json")

    def provider(self, pid: str) -> Any:
        return self._runtime._providers.get(pid)


class HalRuntime:
    """Instantiate a board profile's host peer + wire its two buses."""

    def __init__(self, profile: BoardProfile, peer: str = "host",
                 registry: DriverRegistry | None = None,
                 app: ComposeApp | None = None):
        self.profile = profile
        self.peer = peer
        self.registry = registry or default_registry()
        self.app = app or ComposeApp(app_id="hal-host")
        self._ctx = _HostContext(self.app._internal_bus, self)
        self._providers: dict[str, Any] = {}
        self._instances: dict[str, Any] = {}
        self.rules = LeafRuleEngine(self.app._internal_bus)
        self._built = False

    # -- build ---------------------------------------------------------------

    def build(self) -> "HalRuntime":
        """Validate, then instantiate providers, peripherals, rules, bridge."""
        # 1. Fail at parse time on any wiring conflict ([CXC-HALD-VALIDATE]).
        validate(self.profile, self.registry)

        owned = self.profile.for_peer(self.peer)

        # 2. Port-providers FIRST ([CXC-HALD-DECLARE] runtime order).
        for p in owned:
            if p.is_provider:
                self._instantiate(p)

        # 3. Peripherals addressed through providers.
        for p in owned:
            if not p.is_provider:
                self._resolve_provider_binding(p)
                self._instantiate(p)

        # 4. Leaf rules on the internal bus ([CXC-HALD-RULES]).
        for rule in self.profile.rules:
            self.rules.add(rule)

        # 5. The internal<->ComposeBus membrane ([CXC-HALD-BRIDGE]).
        self._wire_bridge()

        self._built = True
        return self

    def _resolve_provider_binding(self, p: Peripheral) -> None:
        """Attach the resolved provider instance + channel to a peripheral so
        its driver can route reads/writes ([CXC-HALD-EXPANDER])."""
        iface = p.interface
        # A hierarchical pin -> (provider, channel).
        if iface.pin:
            addr = parse_path(iface.pin)
            if addr.provider:
                p._provider_instance = self._providers.get(addr.provider)
                p._channel = addr.channel
        # A compound actuator_group -> its ``via`` provider.
        if iface.kind == "actuator_group":
            via = iface.params.get("via")
            if via:
                p._provider_instance = self._providers.get(str(via))

    def _instantiate(self, p: Peripheral) -> Any:
        spec = self.registry.get(p.driver)
        if spec is None:                       # validator already guards this
            raise ValueError(f"no driver {p.driver!r} for {p.id!r}")
        instance = spec.instantiate(p, self._ctx)
        self._instances[p.id] = instance
        if p.is_provider:
            self._providers[p.id] = instance
        logger.debug("instantiated %s (driver=%s, role=%s)",
                     p.id, p.driver, p.role or "provider")
        return instance

    def _wire_bridge(self) -> None:
        bridge = self.profile.bridge
        if bridge is None:
            return
        for item in bridge.export:
            internal, external = _bridge_pair(item)
            self.app._internal_bus.ensure_namespace(internal, "json")
            self.app.export(internal, external)
        for item in bridge.import_:
            external, internal = _bridge_pair(item)
            self.app._internal_bus.ensure_namespace(internal, "json")
            self.app.import_namespace(external, internal)
        if bridge.transport:
            logger.info("bridge transport=%s (host slice: advisory; export/"
                        "import wiring is transport-agnostic)", bridge.transport)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> "HalRuntime":
        """Start the ComposeApp (both buses + crossover forwarders)."""
        if not self._built:
            self.build()
        self.app.start()
        return self

    def stop(self) -> None:
        self.app.stop()

    # -- introspection -------------------------------------------------------

    @property
    def internal_bus(self) -> Any:
        return self.app._internal_bus

    @property
    def external_bus(self) -> Any:
        return self.app._external_bus

    def instance(self, pid: str) -> Any:
        return self._instances.get(pid)

    def get_internal(self, ns: str) -> Any:
        return self.app._internal_bus.get_state(ns)

    def get_external(self, ns: str) -> Any:
        return self.app._external_bus.get_state(ns)


def _bridge_pair(item: Any) -> tuple[str, str]:
    """A bridge list item is a name or a single-key ``{a: b}`` mapping."""
    if isinstance(item, dict):
        if len(item) != 1:
            raise ValueError(f"bridge entry must be a single mapping: {item!r}")
        k, v = next(iter(item.items()))
        return str(k), str(v)
    return str(item), str(item)
