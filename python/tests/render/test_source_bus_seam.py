"""The bus-handle seam every converted console data source reaches through.

[SPEC:CXC-SOURCES-ON-BUS] slice 1 — [CXC-SOB-HANDLE], [CXC-SOB-DECLARE],
[CXC-SOB-RUN-BLOCKS]'s `park` primitive, and the `bus`/`stop` threading through
`compose_projects/intelligence/console/data_sources.py`.

WHAT IS ACTUALLY AT STAKE HERE. `source_bus` is a NARROW WAIST: 20 sources will
reach the bus through these three functions, so a defect in one of them is a
defect in all 20 at once, and two of the three fail SILENTLY when they are wrong.
`declare` that merges a scope conflict instead of raising produces a namespace
that looks published and is not; `park` that returns instead of blocking puts
`DataSourceSupervisor` into a reclaim retry loop that logs nothing alarming. So
this file tests the seam harder than any single source would justify.

No broker, no network, no real transport — everything is injected.
"""
from __future__ import annotations

import importlib.util
import io
import sys
import threading
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio import source_bus                            # noqa: E402
from composeio.bus import ComposeIOBus                      # noqa: E402
from composeio.namespace import Scope                       # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DS_PATH = _REPO_ROOT / "compose_projects" / "intelligence" / "console" / "data_sources.py"


@pytest.fixture(autouse=True)
def _clean_seam():
    """The self-created-bus memo and the declaration ledger are process-wide, so
    a leak between cases would make a later test pass on an earlier one's state."""
    source_bus.reset_for_tests()
    yield
    source_bus.reset_for_tests()


class _FakeTransport:
    """Transport-shaped, records everything, touches nothing."""

    def __init__(self, spec=None, app_id=""):
        self.spec = spec
        self.app_id = app_id
        self.sent = []
        self.subscribed = []
        self.started = False

    def start(self): self.started = True
    def stop(self): self.started = False
    def send(self, message): self.sent.append(message)
    def subscribe(self, namespace, callback): self.subscribed.append(namespace)
    def subscribe_all(self, callback): pass
    def subscribe_pattern(self, pattern): self.subscribed.append(pattern)

    @property
    def is_connected(self): return True


# --------------------------------------------------------------------------- #
# (a) main(argv) with no kwargs self-creates through the REGISTRY
# --------------------------------------------------------------------------- #
def test_self_create_goes_through_the_transport_registry():
    """A standalone source must NOT construct `MQTTTransport` directly.

    Hardcoding the transport class in the self-create path would reintroduce
    [CXC-SOB-THESIS]'s defect one level down: the in-process path would be
    substrate-free while every standalone debug run was pinned to MQTT.
    """
    seen = []

    def builder(spec, app_id=""):
        seen.append((spec, app_id))
        return _FakeTransport(spec, app_id)

    bus = source_bus.acquire_bus("mqtt://broker.example:1884", label="src",
                                 transport_builder=builder, log=io.StringIO())
    assert seen == [({"type": "mqtt", "broker": "broker.example", "port": 1884},
                     "src")]
    assert bus.global_transport.started, "the self-created bus must be started"


def test_self_create_uses_the_real_registry_by_default(monkeypatch):
    """Not just 'a builder' — the DEFAULT builder is `transport_registry.build`.

    Injecting a builder in every other test would happily pass even if the
    production default were a hardcoded `MQTTTransport(...)`.
    """
    from composeio import transport_registry
    calls = []
    monkeypatch.setattr(transport_registry, "build",
                        lambda spec, app_id="": calls.append((spec, app_id))
                        or _FakeTransport(spec, app_id))
    source_bus.acquire_bus("mqtt://h:1883", label="src", log=io.StringIO())
    assert calls and calls[0][0]["type"] == "mqtt"


def test_an_unknown_transport_type_raises_rather_than_running_local_only():
    """The registry's loud-on-unknown contract must survive the seam."""
    from composeio.transport_registry import UnknownTransportError
    with pytest.raises(UnknownTransportError):
        source_bus.acquire_bus(label="src", log=io.StringIO(),
                               transport_spec={"type": "carrier_pigeon"})


def test_an_unavailable_backend_degrades_loudly_but_still_returns_a_bus():
    """A down broker is an environment condition, not a broken app — the source
    must still run. But it must SAY SO: a bus silently running local-only is the
    exact defect `transport_registry` was added to remove."""
    log = io.StringIO()
    bus = source_bus.acquire_bus("mqtt://h:1883", label="src", log=log,
                                 transport_builder=lambda s, app_id="": None)
    assert bus is not None
    assert bus.global_transport is None
    assert "LOCAL-ONLY" in log.getvalue()


def test_self_creating_sources_share_one_transport():
    """The whole point of the arc: N sources, ONE socket.

    Without the memo, converting 18 sources would build 18 buses and therefore
    18 transports — the connection fan-out rebuilt one layer up.
    """
    built = []

    def builder(spec, app_id=""):
        t = _FakeTransport(spec, app_id)
        built.append(t)
        return t

    buses = [source_bus.acquire_bus("mqtt://h:1883", label=f"src{i}",
                                    transport_builder=builder, log=io.StringIO())
             for i in range(18)]
    assert len(set(id(b) for b in buses)) == 1, "18 sources must share ONE bus"
    assert len(built) == 1, f"18 sources built {len(built)} transports"


# --------------------------------------------------------------------------- #
# (b) main(argv, bus=fake) publishes ONLY on fake and constructs no transport
# --------------------------------------------------------------------------- #
def test_an_injected_bus_wins_and_builds_nothing():
    injected = ComposeIOBus(bus_id="console")

    def builder(spec, app_id=""):
        raise AssertionError("an injected bus must not construct a transport")

    got = source_bus.acquire_bus("mqtt://h:1883", label="src", bus=injected,
                                 transport_builder=builder, log=io.StringIO())
    assert got is injected


def test_an_injected_bus_is_not_added_to_the_self_create_memo():
    """Otherwise a test's console bus would leak into the next test's source."""
    injected = ComposeIOBus(bus_id="console")
    source_bus.acquire_bus("mqtt://h:1883", label="src", bus=injected,
                           log=io.StringIO())
    fresh = source_bus.acquire_bus(
        "mqtt://h:1883", label="src2", log=io.StringIO(),
        transport_builder=lambda s, app_id="": _FakeTransport(s, app_id))
    assert fresh is not injected


# --------------------------------------------------------------------------- #
# declare — explicit scope, conflict is LOUD
# --------------------------------------------------------------------------- #
def test_declare_pins_an_explicit_scope_and_a_permissive_engine():
    bus = ComposeIOBus(bus_id="t")
    ns = source_bus.declare(bus, "self/lts_engine/status", scope=Scope.GLOBAL,
                            source="lts_engine_status")
    assert ns.scope is Scope.GLOBAL
    assert ns.state_engine.engine_type == "json", (
        "a data source's payload is a dict/float/list; a validating default is "
        "what drove these sources off the bus in the first place "
        "([CXC-SOB-BYPASS-EXPIRED])")


def test_declare_refuses_scope_auto():
    """`_resolve_scope`'s 'local if local subscribers exist' heuristic already
    caused [DEC-COMPOSER-SEND-GLOBAL-SCOPE]. The console is full of
    self-subscribing widgets; AUTO here would reproduce it 30+ times at once."""
    bus = ComposeIOBus(bus_id="t")
    with pytest.raises(ValueError, match="FORBIDDEN"):
        source_bus.declare(bus, "self/x", scope=Scope.AUTO, source="s")


def test_a_conflicting_explicit_scope_raises_and_names_both_values():
    """THE load-bearing case, and the one confirmed by experiment 2026-08-08:
    `ensure_namespace` only upgrades an AUTO namespace, so a scene-declared
    `scope: global` SILENTLY beats a source's `Scope.LOCAL` with no warning."""
    bus = ComposeIOBus(bus_id="t")
    bus.register_namespace("self/contested", "json", scope=Scope.GLOBAL)
    with pytest.raises(source_bus.ScopeConflictError) as exc:
        source_bus.declare(bus, "self/contested", scope=Scope.LOCAL, source="s")
    text = str(exc.value)
    assert "'global'" in text and "'local'" in text, (
        f"the conflict must name BOTH scopes so the operator can tell which "
        f"side is wrong: {text}")
    assert "self/contested" in text


def test_ensure_namespace_really_does_swallow_a_conflict_without_declare():
    """Pin the BUS behaviour `declare` exists to compensate for.

    If `ensure_namespace` is ever changed to raise on its own, this test fails
    and `declare`'s conflict check becomes redundant rather than load-bearing —
    which is a fact the next builder needs, not one to rediscover.
    """
    bus = ComposeIOBus(bus_id="t")
    bus.register_namespace("self/x", "json", scope=Scope.LOCAL)
    ns = bus.ensure_namespace("self/x", scope=Scope.GLOBAL)
    assert ns.scope is Scope.LOCAL, (
        "ensure_namespace stopped swallowing the conflict — re-read "
        "[CXC-SOB-DECLARE]")


def test_an_auto_incumbent_is_upgraded_not_treated_as_a_conflict():
    """A widget self-subscription auto-creates the namespace at AUTO before the
    source declares it. That is the ordinary case, not a disagreement."""
    bus = ComposeIOBus(bus_id="t")
    bus.ensure_namespace("self/x")               # AUTO, as a widget would
    ns = source_bus.declare(bus, "self/x", scope=Scope.GLOBAL, source="s")
    assert ns.scope is Scope.GLOBAL


def test_the_startup_manifest_is_one_line_naming_every_namespace():
    """SOB-C3's mechanism: 'no namespace silently goes dark' must be checkable by
    reading one boot log, not by reasoning about 20 threads."""
    bus = ComposeIOBus(bus_id="t")
    for ns in ("fun/nightwatch/title", "fun/nightwatch/tokens",
               "self/away_watches"):
        source_bus.declare(bus, ns, scope=Scope.GLOBAL, source="away_watch")
    buf = io.StringIO()
    line = source_bus.manifest("away_watch", log=buf)
    assert line == buf.getvalue()
    assert line.count("\n") == 1
    assert line.startswith("[away_watch] declared:")
    for ns in ("fun/nightwatch/title", "fun/nightwatch/tokens",
               "self/away_watches"):
        assert f"{ns} scope=global" in line


def test_a_source_that_declared_nothing_says_so_loudly():
    assert "NOTHING" in source_bus.manifest("ghost", log=io.StringIO())


# --------------------------------------------------------------------------- #
# park — the primitive [CXC-SOB-RUN-BLOCKS] is built on
# --------------------------------------------------------------------------- #
def test_park_blocks_until_stop_is_set():
    stop = threading.Event()
    done = threading.Event()
    t = threading.Thread(target=lambda: (source_bus.park(stop, poll_s=0.01),
                                         done.set()), daemon=True)
    t.start()
    assert not done.wait(0.25), (
        "park() returned early — under DataSourceSupervisor a clean return is "
        "read as 'yielded my singleton' and starts a reclaim RETRY LOOP")
    stop.set()
    assert done.wait(2.0), "park() ignored its stop Event"


def test_park_with_no_stop_event_still_blocks():
    """The standalone case: the process is the lifetime."""
    done = threading.Event()
    threading.Thread(target=lambda: (source_bus.park(poll_s=0.01), done.set()),
                     daemon=True).start()
    assert not done.wait(0.25)


# --------------------------------------------------------------------------- #
# (c) build_console_sources(bus=fake) binds the SAME fake into every partial
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def ds():
    name = "ds_seam_under_test"
    spec = importlib.util.spec_from_file_location(name, _DS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                      # @dataclass resolves via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _partial_kwargs(src):
    return getattr(src.run, "keywords", {}) or {}


def test_build_console_sources_threads_the_same_bus_into_every_converted_source(ds):
    """Slice 1 converts ZERO sources, so `bound` is legitimately empty until
    slice 2 lands; the `is fake` identity check is the durable assertion and it
    starts biting the moment the first source is converted. The "at least one is
    converted" gate lives in `test_sources_have_no_paho.py`, where the
    [CXC-SOB-EXEMPT] allow-list makes it a shrinking, reviewable list rather than
    a count nobody maintains."""
    fake = ComposeIOBus(bus_id="the-console-bus")
    sources = ds.build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22",
        bus=fake)

    for s in sources:
        kw = _partial_kwargs(s)
        if "bus" in kw:
            assert kw["bus"] is fake, (
                f"{s.name} got a DIFFERENT bus object — the whole point is one bus")


def test_argv_stays_positional_and_first_for_every_source(ds):
    """[CXC-SOB-CONTRACT-FROZEN]: `DataSourceSupervisor` binds
    `functools.partial(main, argv, ...)` and the standalone `__main__` path must
    keep passing argv alone. A source whose argv moved into a kwarg breaks the
    per-source testability [DEC-INPROCESS-SOURCES] bought."""
    sources = ds.build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")
    for s in sources:
        args = getattr(s.run, "args", ())
        assert len(args) == 1 and isinstance(args[0], list), (
            f"{s.name}: argv is no longer the single positional arg: {args!r}")
        assert "argv" not in _partial_kwargs(s)


def test_every_source_that_takes_stop_gets_its_own_event(ds):
    """One shared Event would make `stop()` a broadcast that cannot stop a
    single misbehaving source, and would make the per-source blocking test
    ([CXC-SOB-RUN-BLOCKS] leg 3) untestable in isolation."""
    sources = ds.build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")
    with_stop = [s for s in sources if s.stop is not None]
    for s in with_stop:
        assert _partial_kwargs(s)["stop"] is s.stop, (
            f"{s.name}: DataSource.stop and the bound kwarg are different "
            "objects — the supervisor would set an Event nobody waits on")
    assert len(set(id(s.stop) for s in with_stop)) == len(with_stop), (
        "two sources share one stop Event")


def test_supervisor_stop_sets_every_sources_event_before_joining(ds):
    """This is what turns 'test doubles that honour a stop signal join cleanly'
    into the REAL path for converted sources."""
    events = [threading.Event() for _ in range(3)]
    joined = []

    def make(i):
        def _run():
            events[i].wait(10)
            joined.append(i)
        return ds.DataSource(name=f"s{i}", run=_run, stop=events[i])

    sup = ds.DataSourceSupervisor([make(i) for i in range(3)])
    sup.start()
    sup.stop(timeout=3.0)
    assert sorted(joined) == [0, 1, 2], (
        f"stop() did not release every parked source: joined={joined}")
    assert sup.alive_names() == []


def test_an_unconverted_source_is_never_handed_kwargs_it_cannot_take(ds):
    """The conversion is sliced, so the list is mixed for several commits. A
    `main(argv)` handed `bus=` would TypeError on the source thread — a crash
    that `_guarded` isolates and the operator never sees on the glass."""
    def legacy_main(argv):
        return 0

    assert ds._accepts(legacy_main, "bus") is False
    assert ds._accepts(legacy_main, "stop") is False

    def converted_main(argv, *, bus=None, stop=None):
        return 0

    assert ds._accepts(converted_main, "bus") is True
    assert ds._accepts(converted_main, "stop") is True

    def kwargs_main(argv, **kw):
        return 0

    assert ds._accepts(kwargs_main, "bus") is True
