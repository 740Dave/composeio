"""Typed model for the declarative HAL board profile ([CXC-HALD-DECLARE]).

A board profile is a ``peripherals:`` list (+ optional ``bridge:`` and
``rules:``).  This module is the pure DATA shape the parser produces and the
validator/runtime consume — no bus, no drivers, no I/O.  It mirrors the schema
in [CXC-HALD-DECLARE] one field at a time:

    { id, on?, role, interface:{<kind>:{params}}, driver, provides?,
      namespace|namespaces, config?, assurance? }

Design notes:
  * The interface is EXACTLY ONE kind ([CXC-HALD-KINDS]).  We keep the raw
    ``{kind: {params}}`` mapping and expose ``.kind`` / ``.params`` so the
    nine kinds + ``adaptor`` + the compound ``actuator_group`` all round-trip
    without a per-kind subclass explosion.
  * ``pin`` / ``bus`` params are ADDRESSING PATHS, not flat numbers
    ([CXC-HALD-TOPOLOGY]) — see :mod:`composeio.hal.topology` for the
    ``provider.channel`` grammar.
  * A port-provider ([CXC-HALD-EXPANDER]) is an ordinary entry carrying a
    ``provides:`` clause; it has no ``role`` (it is neither sensor nor
    actuator — it RE-EXPOSES channels).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# The closed set of interface kinds ([CXC-HALD-KINDS]) + the non-pin adaptor
# family + the compound actuator_group ([CXC-HALD-COMPOUND]).  The parser
# rejects anything outside this set ([CXC-HALD-VALIDATE] rule 4 territory).
NINE_KINDS = frozenset({
    "i2c", "analog", "digital_in", "pwm_out", "uart",
    "onewire", "single_wire", "ws281x", "spi",
})
ADAPTOR_KIND = "adaptor"
COMPOUND_KINDS = frozenset({"actuator_group"})
INTERFACE_KINDS = NINE_KINDS | {ADAPTOR_KIND} | COMPOUND_KINDS

# Bus-shared kinds need a SELECTOR ([CXC-HALD-TOPOLOGY]); the validator
# enforces selector-required (rule 4).  onewire's selector (rom) is optional
# in practice (a single device per pin is common), so it is advisory only.
SELECTOR_REQUIRED = {"i2c": "addr", "spi": "cs"}

# Valid roles ([CXC-HALD-ROLE]).  A port-provider entry has role=None.
ROLES = frozenset({"sensor", "actuator", "transactional"})

# Assurance classes ([CXC-HALD-ASSURANCE]).
ASSURANCE_CLASSES = frozenset({"experiential", "measurement", "control"})


@dataclass
class Interface:
    """The single interface kind + its parameter bag ([CXC-HALD-KINDS])."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)

    # -- convenient typed accessors for the addressing params -------------
    @property
    def bus(self) -> Optional[str]:
        """The (possibly hierarchical) bus path — ``qwiic0`` or ``mux0.ch2``."""
        b = self.params.get("bus")
        return str(b) if b is not None else None

    @property
    def pin(self) -> Optional[str]:
        """The (possibly hierarchical) pin path — ``d5`` or ``io.gpa3``."""
        p = self.params.get("pin")
        return str(p) if p is not None else None

    @property
    def addr(self) -> Optional[Any]:
        """The I2C selector, if any."""
        return self.params.get("addr")


@dataclass
class Peripheral:
    """One ``peripheral:`` entry ([CXC-HALD-DECLARE])."""

    id: str
    interface: Interface
    driver: str
    on: str = "host"
    role: Optional[str] = None          # None for a port-provider
    provides: dict[str, int] = field(default_factory=dict)
    # Exactly one of the two namespace forms is populated by the parser;
    # both are normalized into ``namespaces`` (logical-slot -> bus name).
    namespaces: dict[str, str] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)
    assurance: Optional[str] = None
    # The raw source dict, kept for diagnostics / round-trip.
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_provider(self) -> bool:
        """A port-provider re-exposes channels ([CXC-HALD-EXPANDER])."""
        return bool(self.provides)

    @property
    def bus_namespaces(self) -> list[str]:
        """Every ComposeBus namespace this entry publishes/subscribes."""
        return list(self.namespaces.values())


@dataclass
class Rule:
    """A leaf reaction rule ([CXC-HALD-RULES]): ``when <expr> -> set {..}``.

    The ``when`` condition is a ComposeUX expression-DSL string
    ([SPEC:CXC-DSL]); the runtime compiles it with the EXISTING interpreter
    (``composeio.render.engine.dsl``), never a HAL-specific engine.
    """

    when: str
    sets: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bridge:
    """The internal<->ComposeBus membrane declaration ([CXC-HALD-BRIDGE]).

    ``export`` / ``import_`` are lists of either a plain namespace name or a
    ``{internal: external}`` (export) / ``{external: internal}`` (import)
    single-key mapping.  ``transport`` names the fabric (mqtt/can/uart/...);
    host-side it is advisory metadata — the same export/import wiring runs on
    ComposeApp regardless of the transport ([CXC-HALD-BRIDGE] "pluggable").
    """

    transport: Optional[str] = None
    export: list[Any] = field(default_factory=list)
    import_: list[Any] = field(default_factory=list)


@dataclass
class BoardProfile:
    """A parsed board profile ([CXC-HALD-DECLARE]) — the parser's output and
    the validator/runtime's input."""

    peripherals: list[Peripheral] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    bridge: Optional[Bridge] = None

    def by_id(self, pid: str) -> Optional[Peripheral]:
        for p in self.peripherals:
            if p.id == pid:
                return p
        return None

    def providers(self) -> list[Peripheral]:
        return [p for p in self.peripherals if p.is_provider]

    def for_peer(self, peer: str) -> list[Peripheral]:
        """The subset owned by ``peer`` ([CXC-HALD-DECLARE] ``on:`` filter)."""
        return [p for p in self.peripherals if p.on == peer]
