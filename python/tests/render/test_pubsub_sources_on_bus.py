"""The PUBLISH+SUBSCRIBE sources, driven over the bus end to end.

[SPEC:CXC-SOURCES-ON-BUS] slice 5b — [CXC-SOB-SUBSCRIBE] + [CXC-SOB-WIRE-DELTAS]
item 4 (self-echo), acceptance criterion SOB-C9's machine half.

WHY THESE NEED A FILE OF THEIR OWN, SEPARATE FROM THE VIEWER LAUNCHERS. Slice 5a's
sources only LISTEN, so their whole risk surface is "does the intent still arrive,
and does it arrive the second time". These two also PUBLISH the namespaces they
listen on, and that closes a loop the raw-paho versions never had to think about:
a source's own publish comes back to its own handler. The capture-diff cannot see
it (it is an INBOUND concern, and the local leg never touches the transport at
all), so it has to be proved behaviourally, here.

THE TWO SOURCES SIT AT OPPOSITE ENDS OF THE DEDUP TRADE, DELIBERATELY:

  `away_watch_publisher` NEEDS repeat delivery — tapping the same roster row twice
  must relaunch the Night Watch window twice — so it subscribes the `_app/*`
  PATTERN, whose subscribers are notified unconditionally.

  `config_persister` MUST NOT act on repeats, and more importantly must not act on
  its OWN re-seed: `seed_values` is defaults-filled, so an unguarded self-callback
  would write coded defaults into the durable meta-object as though a human had
  chosen them. It subscribes EXACTLY (absorbing the broker round-trip echo for
  free) and carries an explicit echo map for the local leg.

Getting either one backwards produces no error — one is "the window sometimes
doesn't open", the other is "a `console` block appeared in my meta-object that I
never asked for". Both are exactly the silent class this arc exists to remove.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio import source_bus                            # noqa: E402
from composeio.bus import ComposeIOBus                      # noqa: E402
from composeio.transport import BusMessage, MessageType     # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in (_REPO_ROOT / "compose_projects" / "intelligence" / "tools",
           _REPO_ROOT / "compose_projects" / "intelligence" / "publisher"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


class _LoopbackTransport:
    """Records outbound + every subscription, and can INJECT inbound the way a
    broker would. Inbound is the path that matters for a source whose peer is a
    widget in ANOTHER process."""

    def __init__(self):
        self.sent = []
        self.patterns = []
        self.exact = []
        self._all = []

    def start(self): pass
    def stop(self): pass
    def send(self, m): self.sent.append(m)
    def subscribe(self, ns, cb): self.exact.append(ns)
    def subscribe_all(self, cb): self._all.append(cb)
    def subscribe_pattern(self, p): self.patterns.append(p)

    @property
    def is_connected(self): return True

    def arrive(self, ns, value, source="widget"):
        msg = BusMessage(namespace=ns, type=MessageType.STATE, value=value,
                         source=source, scope="global", sequence=1)
        for cb in list(self._all):
            cb(msg)

    def namespaces_sent(self):
        return [m.namespace for m in self.sent]


@pytest.fixture
def seam(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(tmp_path))
    source_bus.reset_for_tests()
    yield
    source_bus.reset_for_tests()


def _run_on_thread(target):
    stop = threading.Event()
    t = threading.Thread(target=lambda: target(stop), daemon=True)
    t.start()
    return stop, t


def _wait_for(pred, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------- #
# away_watch_publisher — publishes `self/away_watches`, listens `_app/*`
# --------------------------------------------------------------------------- #
@pytest.fixture
def away_watch(seam, tmp_path, monkeypatch):
    import away_watch_publisher as mod

    # A TEMP retrospective DB: driving this source must never touch the real one.
    monkeypatch.setenv("COMPOSEIO_RETRO_DB", str(tmp_path / "retro.db"))
    launches = []
    monkeypatch.setattr(
        mod, "relaunch",
        lambda windows, scene, origin=None, initials=None:
            (launches.append({"scene": scene, "origin": origin,
                              "initials": initials})
             or (type("P", (), {"pid": 1})(), "spawned")))

    transport = _LoopbackTransport()
    bus = ComposeIOBus(bus_id="away-probe", global_transport=transport)
    bus.start(announce_time=False)
    stop, thread = _run_on_thread(
        lambda ev: mod.run("mqtt://unused:1883", 60.0, False, bus=bus, stop=ev))
    assert _wait_for(lambda: transport.patterns), "the source never subscribed"
    yield mod, transport, launches
    stop.set()
    thread.join(timeout=3.0)


def test_away_watch_subscribes_the_app_FAMILY_not_two_literal_topics(away_watch):
    """[CXC-SOB-EXTERNALITY]'s family rule in code. The raw-paho version named two
    literal `composeio/global/_app/...` topics — a scope AND a substrate AND a
    per-leaf classification, all three of which this slice removes."""
    _mod, transport, _launches = away_watch
    assert transport.patterns == ["_app/*"], transport.patterns


def test_away_watch_publishes_self_away_watches_and_nothing_else(away_watch):
    """[CXC-SOB-FUN-IS-EXTERNAL] says this module publishes `fun/nightwatch/*`.
    It does not — those values reach the SPAWNED window through the
    `COMPOSEIO_SCENE_INITIAL` env var, never over the bus. Measured here so the
    correction cannot rot back into the code as a well-meaning "fix".
    """
    mod, transport, _launches = away_watch
    assert _wait_for(lambda: transport.sent), "nothing was published"
    sent = set(transport.namespaces_sent())
    assert sent == {mod.NS} == {"self/away_watches"}, sent
    assert not any(ns.startswith("fun/") for ns in sent), (
        f"a `fun/*` namespace reached the bus: {sent}. The Night Watch window is "
        f"seeded out-of-band; publishing here would put a SECOND producer on "
        f"namespaces the spawned window's own bus owns.")


def test_TAPPING_THE_SAME_ROSTER_ROW_TWICE_RELAUNCHES_TWICE(away_watch):
    """SOB-C9, and THE regression this slice could have shipped.

    `_app/away_watch/open` is an idempotent intent, and the SUPERSEDE tracker
    exists precisely so a re-click reaps and re-spawns. Under `bus.subscribe` the
    second identical intent is absorbed by the `new != old` rule: no error, no
    log, just a window that "sometimes" ignores you.
    """
    mod, transport, launches = away_watch
    intent = {"scene_path": "compose_projects/intelligence/console/nightwatch.compose.yaml",
              "session_id": "sid-1", "label": "L1"}
    for _ in range(3):
        transport.arrive(mod.INTENT_NS, intent)
    assert _wait_for(lambda: len(launches) == 3), (
        f"a repeated identical open intent was DEDUPED "
        f"({len(launches)} of 3 delivered) — use `subscribe_pattern`.")


def test_away_watch_handler_takes_a_DECODED_value_not_a_paho_message(away_watch):
    """[CXC-SOB-SUBSCRIBE]: the callback is `(ns, new, old)` and the value arrives
    DECODED. The old handler's `json.loads(msg.payload.decode())` would raise on
    every message; a bare string and an enveloped dict must both work."""
    mod, transport, launches = away_watch
    transport.arrive(mod.INTENT_NS,
                     "compose_projects/intelligence/console/nightwatch.compose.yaml")
    transport.arrive(mod.INTENT_NS,
                     {"val": {"scene_path": "a.compose.yaml", "label": "L"}})
    assert _wait_for(lambda: len(launches) == 2), launches
    assert launches[0]["scene"] == "compose_projects/intelligence/console/nightwatch.compose.yaml"
    assert launches[1]["scene"] == "a.compose.yaml"


def test_away_watch_bbox_leg_still_places_the_window(away_watch):
    """The bbox arrives on a SIBLING member of the same family, so it exercises
    the handler's filter as well as [DEC-LAUNCH-PLACEMENT]."""
    mod, transport, launches = away_watch
    transport.arrive(mod.BBOX_NS, {"x": 100, "y": 40, "w": 1280, "h": 800})
    transport.arrive(mod.INTENT_NS, {"scene_path": "x.compose.yaml"})
    assert _wait_for(lambda: launches), launches
    assert launches[0]["origin"] == (100 + 1280 + 10, 40), launches[0]


def test_an_unrelated_app_member_never_launches_a_night_watch(away_watch):
    mod, transport, launches = away_watch
    transport.arrive("_app/doc/open", "docs/a.md")
    transport.arrive("_app/dsd/open", {"path": "x.lts"})
    time.sleep(0.15)
    assert launches == [], f"a sibling `_app/*` member leaked through: {launches}"


def test_away_watch_declares_all_three_namespaces_global(away_watch):
    mod, _transport, _launches = away_watch
    declared = dict(source_bus.declared_namespaces(mod.SRC))
    assert declared == {mod.NS: "global", mod.INTENT_NS: "global",
                        mod.BBOX_NS: "global"}, declared


# --------------------------------------------------------------------------- #
# config_persister — publishes AND subscribes `compose_projects/intelligence/<id>/config/*`
# --------------------------------------------------------------------------- #
_META_BARE = {"id": "architect"}          # no `lifecycle`, no `console`


@pytest.fixture
def persister(seam, tmp_path):
    import config_persister as mod

    meta = tmp_path / "architect.meta.json"
    meta.write_text(json.dumps(_META_BARE))
    transport = _LoopbackTransport()
    bus = ComposeIOBus(bus_id="config-probe", global_transport=transport)
    bus.start(announce_time=False)
    stop, thread = _run_on_thread(
        lambda ev: mod.run(str(meta), "mqtt://unused:1883", "architect", 30.0,
                           bus=bus, stop=ev))
    assert _wait_for(lambda: len(transport.sent) >= 9), (
        f"the initial re-seed never landed: {transport.namespaces_sent()}")
    yield mod, transport, meta, bus
    stop.set()
    thread.join(timeout=3.0)


def test_config_persister_subscribes_EXACTLY_not_by_pattern(persister):
    """The mirror image of the launchers, and deliberately so.

    A pattern subscription here would (a) defeat the broker round-trip echo
    absorption that `new != old` gives an exact subscriber for free and (b) fire
    the persist path on every repeat of a value already on disk. Config is STATE,
    not an idempotent intent.
    """
    mod, transport, _meta, _bus = persister
    assert transport.patterns == [], (
        f"config_persister took a PATTERN subscription ({transport.patterns}); "
        f"that re-opens the self-echo loop the exact subscription closes.")
    assert set(transport.exact) == set(mod.config_namespaces("architect"))


def test_the_re_seed_covers_every_namespace_it_declared(persister):
    """SOB-C3's promise, checked at the source rather than in the boot log: what
    it declares and what it re-seeds are the SAME list."""
    mod, transport, _meta, _bus = persister
    assert set(transport.namespaces_sent()) == \
        set(mod.config_namespaces("architect"))
    declared = dict(source_bus.declared_namespaces(mod.SELF_SRC))
    assert set(declared) == set(mod.config_namespaces("architect"))
    assert set(declared.values()) == {"global"}, declared


def test_ITS_OWN_RE_SEED_NEVER_MUTATES_THE_META_OBJECT(persister):
    """THE slice-5b hazard, end to end on a real bus.

    The old loop-guard was `src == config_persister`; the bus callback contract
    ((ns, new, old)) has no `src`, so it is re-expressed as an echo map. Without
    it, the FIRST local self-callback — the one that fires because the namespace
    went from None to a value — would persist the DEFAULTS-FILLED re-seed into the
    durable file, materializing a `console` block `_persist_console` promises never
    to create. There would be no error, and the damage is on disk.
    """
    _mod, _transport, meta, _bus = persister
    time.sleep(0.2)
    assert json.loads(meta.read_text()) == _META_BARE, (
        f"the daemon wrote its own re-seed back to disk: {meta.read_text()}")


def test_the_same_echo_arriving_from_the_BROKER_is_also_absorbed(persister):
    """The second echo path. Our publish returns off the broker (MQTT has no
    no-local), lands in `_dispatch_incoming`, and must not write either."""
    mod, transport, meta, _bus = persister
    for m in list(transport.sent):
        transport.arrive(m.namespace, m.value, source=mod.SELF_SRC)
    time.sleep(0.2)
    assert json.loads(meta.read_text()) == _META_BARE, meta.read_text()


def test_a_GENUINE_edit_from_another_process_still_persists(persister):
    """The other half — an echo guard that swallows real edits is worse than none.
    This is the Configuration tab's whole reason for existing (BUG 2: a config edit
    that evaporated on restart)."""
    mod, transport, meta, _bus = persister
    transport.arrive(mod._heartbeat_ns("architect"),
                     {"base": 20, "backoff_after": 45, "then": 90})
    assert _wait_for(
        lambda: json.loads(meta.read_text()).get("lifecycle", {})
        .get("heartbeat") == {"base": 20, "backoff_after": 45, "then": 90}), \
        f"a real edit never reached disk: {meta.read_text()}"


def test_a_console_field_edit_lands_in_the_console_block(persister):
    """[CXC-ICP-FOUND-A]'s family used to be one MQTT single-level wildcard; it is
    now one exact subscription per `_CONSOLE_FIELDS` member. Equivalent only if
    every field is actually subscribed — so drive one that is NOT declared in
    `console.compose.yaml` either (`window`), which is the leaf a wildcard-to-list
    conversion is most likely to drop."""
    mod, transport, meta, _bus = persister
    transport.arrive(mod._console_ns("architect", "window"), {"w": 1024, "h": 768})
    assert _wait_for(
        lambda: json.loads(meta.read_text()).get("console", {})
        .get("window") == {"w": 1024, "h": 768}), \
        f"the console/window edit was dropped: {meta.read_text()}"
