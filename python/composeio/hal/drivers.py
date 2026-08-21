"""The seeded driver set ([CXC-HALD-DRIVER] / [CXC-HALD-BUILT]).

Each driver is a host-side instantiator + a live instance class.  The three
BUILT drivers are REUSED BY IMPORT — this module never forks
``composeio.peripherals.nfc``, ``composeio.adaptors.wled`` or
``composeio.audio``; it wraps their public constructors so a ``peripheral:``
line becomes a running instance ([CXC-HALD-BUILT] "the integration work").

Instantiators receive a duck-typed host context ``ctx`` exposing:
    ctx.bus              the internal ComposeIOBus (per [CXC-HALD-BRIDGE])
    ctx.register_ns(n)   ensure a permissive (json) namespace on that bus
    ctx.provider(pid)    the already-instantiated port-provider instance
    ctx.log              a logger

Port-providers instantiate FIRST ([CXC-HALD-DECLARE] runtime), so a peripheral
addressed through one can resolve its provider at wiring time.

Tier honesty ([CXC-HALD-DRIVER-DIST]): ``bme280`` is a genuine tier-(a)
data-descriptor (register map + DSL decode, no code); ``mcp23017`` / ``tca9548a``
are real port-providers; ``pca9685`` is a deliberate STUB (enough to carry the
provider-level shared-frequency config the validator checks — rule 5); the
built three are tier-(b)/adaptor code reused as-is.
"""

from __future__ import annotations

import logging
from typing import Any

from composeio.render.engine import dsl

from .registry import DriverRegistry, DriverSpec

logger = logging.getLogger("composeio.hal.drivers")


# ---------------------------------------------------------------------------
# Port-providers ([CXC-HALD-EXPANDER]) — one bus device -> N typed channels.
# ---------------------------------------------------------------------------

class Mcp23017Provider:
    """A GPIO port-provider (``provides: {gpio: 16}``).  Host-side it holds a
    virtual pin-state map and routes reads/writes for peripherals addressed
    ``<id>.gpaN`` ([CXC-HALD-EXPANDER] "transparently routed by the runtime").
    On real silicon the driver would do MCP23017 register writes; here it
    records, which is all the host slice needs."""

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.provides = dict(peripheral.provides)
        self.addr = peripheral.interface.addr
        self._pins: dict[str, Any] = {}
        ctx.log.debug("mcp23017 %s provides %s @ addr %s",
                      self.id, self.provides, self.addr)

    def read_channel(self, channel: str) -> Any:
        return self._pins.get(channel)

    def write_channel(self, channel: str, value: Any) -> None:
        self._pins[channel] = value


class Tca9548aProvider:
    """An I2C-mux port-provider (``provides: {i2c_channel: 8}``).  Downstream
    ``i2c`` peripherals name ``<id>.chN`` as their bus; the mux RESOLVES the
    naive same-address collision ([CXC-HALD-TOPOLOGY])."""

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.provides = dict(peripheral.provides)
        self.addr = peripheral.interface.addr
        self._selected: str | None = None

    def select(self, channel: str) -> None:
        self._selected = channel


class Pca9685Provider:
    """A PWM port-provider STUB (``provides: {pwm_ch: 16}``).

    Its PWM FREQUENCY is a single prescaler SHARED across all channels
    ([CXC-HALD-EXPANDER] / [CXC-HALD-CONFIG]) — carried here as
    ``self.freq_hz`` so :mod:`composeio.hal.validator` can enforce channel
    agreement (rule 5).  Stubbed: it records writes but drives no hardware."""

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.provides = dict(peripheral.provides)
        self.freq_hz = peripheral.config.get("freq_hz")
        self._channels: dict[str, Any] = {}

    def write_channel(self, channel: str, value: Any) -> None:
        self._channels[channel] = value


# ---------------------------------------------------------------------------
# bme280 — a DATA-DESCRIPTOR driver ([CXC-HALD-DRIVER-DIST] tier a).
# ---------------------------------------------------------------------------

class Bme280Descriptor:
    """Register map + a decode EXPRESSION (the existing DSL) — data, not code.

    A generic per-interface engine (here, a tiny host loop) reads the named
    registers and evaluates the decode expression against them.  The decode is
    a [SPEC:CXC-DSL] sentence, so it is portable, sandboxed and fetchable as
    pure data — the common-case driver tier ([CXC-HALD-DRIVER-DIST]).

    Host slice: :meth:`read` takes raw register values directly (no I2C wire)
    and publishes the decoded physical quantity.  A real deployment would fill
    ``raw`` from the register map over the bus behind the addressing path.
    """

    # The declarative register map (address + length), data only.
    REGISTERS = {"t_raw": {"reg": 0xFA, "len": 3}}
    # The decode expression: a compensated BME280 divides its raw count; the
    # exact polynomial lives in a real descriptor — this proves the DSL seam.
    DECODE = "ns('t_raw') / 100.0"

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.ctx = ctx
        # The bus namespace this sensor publishes (single-quantity sensor).
        self.out_ns = (peripheral.namespaces.get("value")
                       or peripheral.namespaces.get("temperature"))
        if self.out_ns:
            ctx.register_ns(self.out_ns)
        # Compile the decode expression ONCE with the existing interpreter.
        self._decode = dsl.parse(self.DECODE)

    def read(self, t_raw: int = 2200) -> float:
        """Decode a raw register reading via the DSL and publish it.

        Default ``t_raw`` decodes to 22.0 C; the selftest passes a hotter raw
        to trip the leaf rule ([CXC-HALD-RULES])."""
        value = self._decode.evaluate(dsl.EvalContext(ns={"t_raw": t_raw}))
        if self.out_ns:
            self.ctx.bus.publish_state(self.out_ns, value, source=self.id)
        return value


# ---------------------------------------------------------------------------
# Generic host drivers — a digital_in sensor, actuators, a compound group.
# ---------------------------------------------------------------------------

class GpioInput:
    """A generic ``digital_in`` sensor.  Its pin may be a root pin or an
    expander sub-pin (``io.gpb0``) — the runtime resolved the provider and
    handed it to us ([CXC-HALD-EXPANDER] routing).  Host slice: :meth:`press`
    / :meth:`release` simulate the edge and publish the namespace."""

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.ctx = ctx
        self.out_ns = peripheral.namespaces.get("value")
        self.provider = getattr(peripheral, "_provider_instance", None)
        self.channel = getattr(peripheral, "_channel", None)
        if self.out_ns:
            ctx.register_ns(self.out_ns)

    def _emit(self, pressed: bool) -> None:
        if self.provider is not None and self.channel is not None:
            self.provider.write_channel(self.channel, pressed)   # routed read-back
        if self.out_ns:
            self.ctx.bus.publish_state(self.out_ns, "on" if pressed else "off",
                                       source=self.id)

    def press(self) -> None:
        self._emit(True)

    def release(self) -> None:
        self._emit(False)


class GenericActuator:
    """A generic actuator (relay / status display / ...).  SUBSCRIBES its
    namespace(s) and records the latest value in ``.last`` ([CXC-HALD-ROLE]
    actuator).  A ``control`` assurance actuator boots to a fail-safe default
    ([CXC-HALD-ASSURANCE]) — ``relay`` -> off."""

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.ctx = ctx
        self.assurance = peripheral.assurance
        self.last: dict[str, Any] = {}
        for slot, ns in peripheral.namespaces.items():
            ctx.register_ns(ns)
            ctx.bus.subscribe(ns, self._on_change)
        # Fail-safe default for a control-class actuator (boot known-state).
        if self.assurance == "control":
            for ns in peripheral.namespaces.values():
                if ns.startswith("relay"):
                    ctx.bus.publish_state(ns, "off", source=self.id)

    def _on_change(self, namespace: str, new_value: Any, old_value: Any = None):
        self.last[namespace] = new_value


class ActuatorGroup:
    """A COMPOUND actuator pin-group ([CXC-HALD-COMPOUND]) — a stepper/gauge
    is ONE semantic actuator built from a group of coil pins driven through a
    provider.  ``interface: {actuator_group: {via: <provider>, coils: [...]}}``.
    Subscribes its single semantic namespace (``gauge/value``); on change the
    driver would run the micro-step sequence — host-side we record position and
    stroke the coils through the provider ([CXC-HALD-NAMESPACE] gauge/value)."""

    def __init__(self, peripheral, ctx):
        self.id = peripheral.id
        self.ctx = ctx
        params = peripheral.interface.params
        self.coils = list(params.get("coils", []))
        self.provider = getattr(peripheral, "_provider_instance", None)
        self.out_ns = peripheral.namespaces.get("value")
        self.position = 0
        self.sweep_deg = peripheral.config.get("sweep_deg")
        if self.out_ns:
            ctx.register_ns(self.out_ns)
            ctx.bus.subscribe(self.out_ns, self._on_value)
        # home_on_boot / control fail-safe: park the needle at home.
        if peripheral.config.get("home_on_boot") or peripheral.assurance == "control":
            self._drive(0)

    def _drive(self, position: int) -> None:
        self.position = position
        if self.provider is not None:
            for i, coil in enumerate(self.coils):
                self.provider.write_channel(coil, (position >> i) & 1)

    def _on_value(self, namespace: str, new_value: Any, old_value: Any = None):
        try:
            self._drive(int(float(new_value)))
        except (TypeError, ValueError):
            self.ctx.log.warning("gauge %s: non-numeric value %r",
                                 self.id, new_value)


# ---------------------------------------------------------------------------
# The three BUILT drivers, reused by import ([CXC-HALD-BUILT]).
# ---------------------------------------------------------------------------

class _RecordingWLEDSender:
    """A host-safe WLED sender — records instead of hitting the network, so
    the built adaptor instantiates + attaches offline ([CXC-HALD-BUILT])."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, state: dict) -> None:
        self.sent.append(state)

    def close(self) -> None:
        pass

    def __repr__(self) -> str:
        return "<RecordingWLEDSender>"


def _instantiate_wled(peripheral, ctx):
    """WLED family ([CXC-HALD-BUILT]): reuse ``composeio.adaptors.wled``.

    Covers both ``interface: {adaptor: {wled: {host}}}`` (a WLED fleet member)
    and a locally-wired ``ws281x`` strip — the same WLED family per the spec."""
    from composeio.adaptors.wled import WLEDAdaptor, WLEDMapping
    for ns in peripheral.namespaces.values():
        ctx.register_ns(ns)
    adaptor = WLEDAdaptor(ctx.bus, _RecordingWLEDSender(), mapping=WLEDMapping())
    adaptor.attach()
    return adaptor


class _NullAudioSink:
    """A no-op :class:`AudioSink` so the built audio actuator wires up without
    the optional ``miniaudio`` backend ([CXC-HALD-BUILT]; audio dep is lazy).
    The real device write is the ``MiniaudioSink`` optional-dep path."""

    def load(self, source): return object()
    def play(self, sound, gain: float = 1.0, loop: bool = False) -> int: return 0
    def stop(self, voice_id: int) -> None: pass
    def stop_all(self) -> None: pass
    def set_gain(self, voice_id: int, gain: float) -> None: pass
    def close(self) -> None: pass


def _instantiate_audio(peripheral, ctx):
    """Audio actuator ([CXC-HALD-BUILT]): reuse ``composeio.audio``.

    A null sink keeps the host slice backend-free; the semantic binding
    (subscribe a namespace -> play) is the same code path a real speaker uses."""
    from composeio.audio import AudioActuator
    for ns in peripheral.namespaces.values():
        ctx.register_ns(ns)
    config = {"bindings": []}   # wiring proof; a real profile carries clips+bindings
    return AudioActuator(ctx.bus, _NullAudioSink(), config)


class Pn532Transactional:
    """The built PN532/NFC driver ([CXC-HALD-BUILT]) framed as the
    TRANSACTIONAL role ([CXC-HALD-ROLE]): ``cmd/*`` in, ``status`` + ``result``
    out.  Reuses ``composeio.peripherals.nfc`` (PN532 over a mock transport) —
    NOT forked.  ``assurance: experiential`` ([CXC-HALD-ASSURANCE]): theatrical,
    never access control.

    First-slice scope: a minimal request/response.  The resumable multi-tick
    enroll FSM ([CXC-HALD-DRIVER-DIST] tier b, verification C7) is OUT of the
    host slice — TODO, flagged."""

    def __init__(self, peripheral, ctx):
        from composeio.peripherals.nfc import PN532, MockI2CTransport
        self.id = peripheral.id
        self.ctx = ctx
        self.reader = PN532(MockI2CTransport())      # proves import + construct
        self.cmd_ns = peripheral.namespaces.get("cmd")
        self.status_ns = peripheral.namespaces.get("status")
        self.result_ns = peripheral.namespaces.get("result")
        for ns in (self.cmd_ns, self.status_ns, self.result_ns):
            if ns:
                ctx.register_ns(ns)
        if self.cmd_ns:
            ctx.bus.subscribe(self.cmd_ns, self._on_cmd)

    def _on_cmd(self, namespace: str, new_value: Any, old_value: Any = None):
        # Minimal transaction: acknowledge, then emit a (theatrical) result.
        if self.status_ns:
            self.ctx.bus.publish_state(self.status_ns, "scanning", source=self.id)
        if self.result_ns:
            self.ctx.bus.publish_state(self.result_ns,
                                       {"id": 1, "score": 0}, source=self.id)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def seed(registry: DriverRegistry) -> None:
    """Register the first-slice driver set into *registry* ([CXC-HALD-DRIVER]
    seed; spec status-block "Suggested first slice")."""

    # Port-providers ([CXC-HALD-EXPANDER]).
    registry.register(DriverSpec(
        "mcp23017", "provider", Mcp23017Provider, tier="provider",
        provides_template={"gpio": 16}, interface_kinds=frozenset({"i2c"}),
        summary="16-channel GPIO expander (port-provider)"))
    registry.register(DriverSpec(
        "tca9548a", "provider", Tca9548aProvider, tier="provider",
        provides_template={"i2c_channel": 8}, interface_kinds=frozenset({"i2c"}),
        summary="8-channel I2C mux (port-provider)"))
    registry.register(DriverSpec(
        "pca9685", "provider", Pca9685Provider, tier="provider",
        provides_template={"pwm_ch": 16}, interface_kinds=frozenset({"i2c"}),
        summary="16-channel 12-bit PWM (shared-frequency port-provider, STUB)"))

    # Data-descriptor sensor ([CXC-HALD-DRIVER-DIST] tier a).
    registry.register(DriverSpec(
        "bme280", "sensor", Bme280Descriptor, tier="descriptor",
        interface_kinds=frozenset({"i2c"}),
        summary="env temp/humidity/pressure (register-map data-descriptor)"))

    # Generic host drivers.
    registry.register(DriverSpec(
        "gpio", "sensor", GpioInput, tier="generic",
        interface_kinds=frozenset({"digital_in"}),
        summary="generic digital input (button/PIR/reed)"))
    registry.register(DriverSpec(
        "relay", "actuator", GenericActuator, tier="generic",
        interface_kinds=frozenset({"pwm_out", "digital_in"}),
        summary="generic relay/switch actuator"))
    registry.register(DriverSpec(
        "display", "actuator", GenericActuator, tier="generic",
        interface_kinds=frozenset({"i2c"}),
        summary="generic status/text display actuator (display/text)"))
    registry.register(DriverSpec(
        "x27168", "actuator", ActuatorGroup, tier="generic",
        interface_kinds=frozenset({"actuator_group"}),
        summary="x27.168 gauge stepper — compound actuator pin-group"))

    # Built drivers reused by import ([CXC-HALD-BUILT]).
    registry.register(DriverSpec(
        "wled", "actuator", _instantiate_wled, tier="builtin",
        interface_kinds=frozenset({"adaptor", "ws281x"}),
        summary="WLED family actuator (composeio.adaptors.wled)"))
    registry.register(DriverSpec(
        "ws281x", "actuator", _instantiate_wled, tier="builtin",
        interface_kinds=frozenset({"ws281x", "adaptor"}),
        summary="ws281x/WLED LED strip actuator"))
    registry.register(DriverSpec(
        "audio", "actuator", _instantiate_audio, tier="builtin",
        interface_kinds=frozenset({"adaptor", "pwm_out", "uart"}),
        summary="audio actuator (composeio.audio)"))
    registry.register(DriverSpec(
        "pn532", "transactional", Pn532Transactional, tier="builtin",
        interface_kinds=frozenset({"i2c", "uart"}),
        summary="PN532 NFC — transactional (composeio.peripherals.nfc)"))
