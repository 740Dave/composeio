"""Transport substitution — an app must not care how ComposeBus is instantiated.

David's thesis, 2026-08-08: "ComposeBus might for some devices be instantiated as
MQTT, and for some it might ride on CANBUS. But in both cases the ComposeIO/UX app
shouldn't have to care how ComposeBus is instantiated — it should just listen to /
publish to ComposeBus. The ComposeBus API should be an abstraction layer that
makes MQTT calls, pushes onto CANBUS, or ties a message to a carrier pigeon."

WHAT THIS PINS, AND WHY IT DID NOT HOLD BEFORE. Everything above the bus was
already transport-agnostic — `Transport` is a proper ABC, seven implementations
exist, and [BPR-TRANSPORT-CONTRACT] already models a CAN far-side as
`MAPPED_DEVICE_BUS` with declared degradation. But `runtime._build_bus` hardcoded
the single substrate:

    if isinstance(t, dict) and t.get("type") == "mqtt":

so a scene declaring `type: can` / `uart` / `synthetic` had it DROPPED ON THE
FLOOR — `bus._global` stayed None and the app silently ran local-only. Measured:

    mqtt -> MQTTTransport | can -> None | synthetic -> None | uart -> None

The waist was sound and unreachable. `transport_registry` replaced the hardcoded
`if`, and these tests keep it reachable.

NO HARDWARE REQUIRED. CAN runs on an in-process LOOPBACK backend that exercises
the real frame path — arbitration-id encode, filter match, decode. Swapping to
real hardware is `{type: can, channel: can0, backend: socketcan}`, so this suite
keeps working when the CAN hardware is wired and does not depend on it now.
"""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from composeio.render.engine.parser import parse_compose_yaml  # noqa: E402
from composeio.render.engine.runtime import RenderApp  # noqa: E402
from composeio.transport import BusMessage, MessageType  # noqa: E402
from composeio.transport_can import (  # noqa: E402
    CANTransport, _reset_loopbacks_for_tests, decode_arbitration_id,
    encode_arbitration_id,
)
from composeio.transport_registry import (  # noqa: E402
    UnknownTransportError, build, known_types,
)

SCENE = """app: {{id: subst-probe, transports: [{entry}]}}
design: {{name: subst-probe, width: 200, height: 100}}
namespaces:
  - {{name: probe/value, state_engine: json, scope: global}}
widgets:
  - {{type: label, id: L, text: hi, row: 0, column: 0}}
"""


@pytest.fixture(autouse=True)
def _clean():
    _reset_loopbacks_for_tests()
    yield
    _reset_loopbacks_for_tests()


def _app_for(entry):
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.compose.yaml"
        p.write_text(SCENE.format(entry=entry))
        return RenderApp(parse_compose_yaml(str(p)), headless=True,
                         connect_transports=True)


# --------------------------------------------------------------------------- #
# the substitution itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry,expect", [
    ("{type: can, channel: loopback}", "CANTransport"),
    ("{type: mqtt, broker: 127.0.0.1, port: 1}", "MQTTTransport"),
])
def test_scene_gets_the_substrate_it_declares(entry, expect):
    """The same scene, unchanged, resolves to whichever transport it names."""
    app = _app_for(entry)
    assert type(app.bus._global).__name__ == expect


def test_unknown_transport_type_RAISES_never_silently_local_only():
    """A mis-typed substrate must be a startup error, not missing telemetry.

    This is the exact defect the registry replaced: `type: mqqt` used to leave
    bus._global = None with no error, and the app looked fine while nothing left
    the process.
    """
    for bad in ("{type: mqqt}", "{type: carrier_pigeon}"):
        with pytest.raises(UnknownTransportError):
            _app_for(bad)


def test_registry_knows_the_shipped_substrates():
    for t in ("mqtt", "can", "uart", "synthetic", "replay"):
        assert t in known_types()


def test_unknown_type_error_names_the_known_types():
    """The error has to be actionable, not just loud."""
    with pytest.raises(UnknownTransportError, match="Known types"):
        build({"type": "definitely_not_real"})


def test_known_type_with_dead_backend_degrades_rather_than_raising():
    """"We don't know what you asked for" (bug) vs "we know, it's unreachable"
    (environment) must be different outcomes."""
    from composeio import transport_registry as tr
    tr.register("_dead", lambda s, a: (_ for _ in ()).throw(OSError("no iface")))
    try:
        assert build({"type": "_dead"}) is None
    finally:
        tr._FACTORIES.pop("_dead", None)


# --------------------------------------------------------------------------- #
# THE THESIS TEST — app code names no transport, CAN carries the message
# --------------------------------------------------------------------------- #
def test_app_publishes_through_the_bus_and_CAN_carries_it():
    """The load-bearing proof, and it needs no hardware.

    The app calls `bus.publish_state(...)` — it never names MQTT or CAN — and a
    separate CAN peer receives it. That is the abstraction doing its job.
    """
    app = _app_for("{type: can, channel: loopback}")
    app.bus.start()
    assert app.bus._global.is_connected, "CAN loopback should be connected once started"

    peer = CANTransport(channel="loopback")
    peer.start()
    got = []
    peer.subscribe("probe/value", got.append)
    try:
        app.bus.publish_state("probe/value", 42)
        assert [(m.namespace, m.value) for m in got] == [("probe/value", 42)]
    finally:
        app.bus.stop()
        peer.stop()


# --------------------------------------------------------------------------- #
# CAN specifics
# --------------------------------------------------------------------------- #
def test_a_substrate_the_core_has_never_heard_of(monkeypatch):
    """The generality claim, stated as strongly as it can be.

    David, 2026-08-09, on the CAN proof: "same is true for UART or carrier
    pigeons!" — and he is right that CAN proved only the case I happened to have
    built. The real claim is that the bus does not know, or need to know, the set
    of possible substrates.

    So this registers a transport defined ENTIRELY OUT OF TREE — the composeio
    package contains no reference to it, no factory, no import — and publishes
    through the bus with app code identical to every other case. If this passes,
    the waist is genuinely a waist and not an mqtt-plus-special-cases.
    """
    from composeio import transport_registry as reg
    from composeio.bus import ComposeIOBus
    from composeio.transport import Transport

    class CarrierPigeonTransport(Transport):
        """Latency ~2h. Throughput 1 msg/pigeon. QoS: best-effort, weather-dependent."""

        def __init__(self, loft="home"):
            self.loft = loft
            self._started = False
            self.aviary = []

        def start(self):
            self._started = True

        def stop(self):
            self._started = False

        @property                       # enforced by Transport.__init_subclass__
        def is_connected(self):
            return self._started

        def send(self, message):
            self.aviary.append((message.namespace, message.value))

        def subscribe(self, ns, cb):
            pass

        def subscribe_all(self, cb):
            pass

        def unsubscribe(self, ns, cb):
            pass

    monkeypatch.setitem(reg._FACTORIES, "carrier_pigeon",
                        lambda spec, app_id: CarrierPigeonTransport(spec.get("loft", "home")))

    pigeon = reg.build({"type": "carrier_pigeon", "loft": "rooftop"})
    assert isinstance(pigeon, CarrierPigeonTransport)

    bus = ComposeIOBus(bus_id="app", global_transport=pigeon)
    # announce_time=False: a leaf bus must not put a second `_bus/time` publisher
    # on a namespace it does not own ([CXC-SOB-WIRE-DELTAS]).
    bus.start(announce_time=False)
    try:
        bus.ensure_namespace("self/context", "json")
        bus.publish_state("self/context", 0.42)          # app names no substrate
        bus.publish_state("self/deliverables", {"count": 3})
        assert pigeon.aviary == [("self/context", 0.42),
                                 ("self/deliverables", {"count": 3})], (
            "app-level publishes must reach whatever substrate is mounted, "
            "including one the core has never heard of")
    finally:
        bus.stop()


def test_arbitration_id_round_trips_and_fits_29_bits():
    # 7 is the ALLOCATED node id in `src` ([BP-CAN-ID] v2). It was `seq` until
    # 2026-08-13; seq had no consumer and its bits paid for `prio` and `scope`.
    arb = encode_arbitration_id("probe/value", MessageType.STATE, "global", 7)
    assert arb < (1 << 29), "must fit an extended CAN id"
    _h, mtype, scope, src = decode_arbitration_id(arb)
    assert (mtype, scope, src) == (MessageType.STATE, "global", 7)


def test_a_node_does_not_receive_its_own_transmission():
    """Real CAN behavior, and a regression guard for a bug this caught.

    The loopback excluded the sender via `node is not sender`, but
    `self._on_frame` yields a NEW bound-method object on every access, so the
    identity check never matched and a sender heard its own frame. Fixed by
    binding the callback once; pinned here.
    """
    a = CANTransport(channel="loopback"); a.start()
    mine = []
    a.subscribe("probe/self", mine.append)
    try:
        a.send(BusMessage(namespace="probe/self", type=MessageType.STATE,
                          value=1, scope="global"))
        assert mine == [], "a CAN node must not receive its own transmission"
    finally:
        a.stop()


def test_oversized_payload_is_dropped_loudly_not_truncated(caplog):
    """Classic CAN is 8 bytes. Fragmentation is not implemented, so an oversized
    payload must be REPORTED and dropped — never silently half-sent."""
    a = CANTransport(channel="loopback"); a.start()
    peer = CANTransport(channel="loopback"); peer.start()
    got = []
    peer.subscribe("probe/big", got.append)
    try:
        a.send(BusMessage(namespace="probe/big", type=MessageType.STATE,
                          value={"payload": "x" * 200}, scope="global"))
        assert got == [], "an untransmittable payload must not arrive half-formed"
    finally:
        a.stop()
        peer.stop()


def test_is_connected_is_a_property_on_every_transport():
    """`bus.status()` reads `transport.is_connected` WITHOUT parens, so a method
    would hand it an always-truthy bound object and a dead link would report
    CONNECTED. Caught in review of CANTransport; pinned for all of them."""
    import importlib
    import inspect
    for mod_name in ("transport_mqtt", "transport_local", "transport_uart",
                     "transport_can"):
        mod = importlib.import_module(f"composeio.{mod_name}")
        for name, cls in vars(mod).items():
            if (inspect.isclass(cls) and name.endswith("Transport")
                    and cls.__module__ == mod.__name__):
                assert isinstance(cls.__dict__.get("is_connected"), property), (
                    f"{name}.is_connected must be a property")


def test_every_shipped_transport_satisfies_the_ABC():
    """UARTTransport was ABSTRACT (missing `subscribe_all`) and so could not be
    instantiated at all — which is why UART was unreachable from a scene."""
    import importlib
    import inspect
    for mod_name in ("transport_mqtt", "transport_local", "transport_uart",
                     "transport_can"):
        mod = importlib.import_module(f"composeio.{mod_name}")
        for name, cls in vars(mod).items():
            if (inspect.isclass(cls) and name.endswith("Transport")
                    and cls.__module__ == mod.__name__):
                assert not getattr(cls, "__abstractmethods__", frozenset()), (
                    f"{name} is abstract: {sorted(cls.__abstractmethods__)}")
