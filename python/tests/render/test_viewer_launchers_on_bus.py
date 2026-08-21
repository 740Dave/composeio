"""The `_app/*` viewer launchers, driven over the bus end to end.

[SPEC:CXC-SOURCES-ON-BUS] slice 5 — [CXC-SOB-SUBSCRIBE], acceptance criterion
SOB-C9's machine half.

WHY THESE NEED THEIR OWN FILE. A capture-diff proves nothing about a
SUBSCRIBER: these sources publish nothing, so before and after are both empty.
The evidence that matters is BEHAVIOURAL — does an intent arriving on the bus
still open the window, and does it still open it the SECOND time?

THE SECOND TIME IS THE WHOLE POINT. `_app/doc/open` is an IDEMPOTENT INTENT:
tapping the same row twice must open the viewer twice. Under raw paho that was
free — every message was delivered. Under `bus.subscribe` it would break
silently, because `Namespace.publish_state` notifies exact subscribers only when
`new != old`. That is a REGRESSION with no error, on the glass, of exactly the
"sometimes the window doesn't open" shape [CXC-SOB-SUBSCRIBE] warns about.

`subscribe_pattern` is the fix (its subscribers are notified unconditionally),
and this file is what stops someone "simplifying" it back to `subscribe`.
"""
from __future__ import annotations

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
from composeio.namespace import Scope                       # noqa: E402
from composeio.transport import BusMessage, MessageType     # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in (_REPO_ROOT / "compose_projects" / "intelligence" / "tools",
           _REPO_ROOT / "compose_projects" / "intelligence" / "publisher"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


class _LoopbackTransport:
    """Records outbound, and can INJECT inbound the way a broker would.

    Inbound matters here: the launchers receive from a widget in ANOTHER
    process, so `bus.publish_state` on the same bus would exercise the wrong
    path. `_dispatch_incoming` is the real one.
    """

    def __init__(self):
        self.sent = []
        self.patterns = []
        self._all = []

    def start(self): pass
    def stop(self): pass
    def send(self, m): self.sent.append(m)
    def subscribe(self, ns, cb): pass
    def subscribe_all(self, cb): self._all.append(cb)
    def subscribe_pattern(self, p): self.patterns.append(p)

    @property
    def is_connected(self): return True

    def arrive(self, ns, value, source="widget"):
        msg = BusMessage(namespace=ns, type=MessageType.STATE, value=value,
                         source=source, scope="global", sequence=1)
        for cb in list(self._all):
            cb(msg)


@pytest.fixture
def seam(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(tmp_path))
    source_bus.reset_for_tests()
    yield
    source_bus.reset_for_tests()


def _start(module, opens, **run_kwargs):
    """Run `module.run(...)` on a thread with a loopback bus; return
    (transport, stop, thread)."""
    transport = _LoopbackTransport()
    bus = ComposeIOBus(bus_id="viewer-probe", global_transport=transport)
    bus.start(announce_time=False)
    stop = threading.Event()
    t = threading.Thread(
        target=lambda: module.run("mqtt://unused:1883", bus=bus, stop=stop,
                                  **run_kwargs),
        daemon=True)
    t.start()
    # wait for the subscription to be registered
    deadline = time.time() + 3.0
    while not transport.patterns and time.time() < deadline:
        time.sleep(0.01)
    return transport, stop, t


@pytest.fixture
def doc_launcher(seam, monkeypatch):
    import doc_viewer_launcher as mod
    opens = []
    monkeypatch.setattr(mod.DocViewerLauncher, "open",
                        lambda self, path: opens.append(path) or f"opened {path}")
    monkeypatch.setattr(mod.DocViewerLauncher, "set_console_bbox",
                        lambda self, bbox: opens.append(("bbox", bbox)))
    transport, stop, thread = _start(mod, opens)
    yield mod, transport, opens
    stop.set()
    thread.join(timeout=3.0)


def test_the_launcher_subscribes_the_family_not_two_literal_topics(doc_launcher):
    """[CXC-SOB-EXTERNALITY]: "a namespace family is classified WHOLE, never by a
    narrower glob. An earlier pass used `_app/*/open` and missed
    `_app/dsd/answered` and `_app/decisions/anchor`." The pattern subscription
    is that rule expressed in code."""
    _mod, transport, _opens = doc_launcher
    assert transport.patterns == ["_app/*"], (
        f"expected the whole `_app/*` family, got {transport.patterns}. Two "
        f"literal topics is one forgotten leaf away from a dark surface.")


def test_an_intent_arriving_over_the_transport_opens_the_viewer(doc_launcher):
    mod, transport, opens = doc_launcher
    transport.arrive(mod.INTENT_NS, "docs/a.md")
    assert opens == ["docs/a.md"]


def test_TAPPING_THE_SAME_ROW_TWICE_OPENS_IT_TWICE(doc_launcher):
    """SOB-C9. THE regression this slice could have shipped.

    Two identical `_app/doc/open` publishes. Under `bus.subscribe` the second is
    absorbed by the new != old rule and the window does not open — no error, no
    log, just a viewer that "sometimes" ignores you.
    """
    mod, transport, opens = doc_launcher
    transport.arrive(mod.INTENT_NS, "docs/a.md")
    transport.arrive(mod.INTENT_NS, "docs/a.md")
    transport.arrive(mod.INTENT_NS, "docs/a.md")
    assert opens == ["docs/a.md"] * 3, (
        f"a repeated identical intent was DEDUPED ({len(opens)} of 3 delivered). "
        f"`_app/doc/open` is idempotent — tapping the same row twice must open "
        f"the viewer twice. Use `subscribe_pattern`, not `subscribe`.")


def test_the_handler_takes_a_DECODED_value_not_a_paho_message(doc_launcher):
    """[CXC-SOB-SUBSCRIBE]: "the value arrives decoded... A handler that still
    calls `json.loads(msg.payload)` will raise on every message."

    A dict intent (`{path: ...}`) and a bare string must both work, because the
    `parse_*` helpers accept either and the widgets publish both shapes.
    """
    mod, transport, opens = doc_launcher
    transport.arrive(mod.INTENT_NS, {"path": "docs/b.md"})
    assert opens == ["docs/b.md"]


def test_a_falsy_intent_is_ignored_and_a_sibling_app_namespace_is_filtered(doc_launcher):
    """The family subscription hears the WHOLE family, so the handler must
    filter. A `_app/dsd/open` must not spawn a doc viewer."""
    mod, transport, opens = doc_launcher
    transport.arrive(mod.INTENT_NS, "")
    transport.arrive("_app/dsd/open", {"path": "specs/x.lts"})
    transport.arrive("_app/graph/open", True)
    assert opens == [], f"a sibling `_app/*` member leaked through: {opens}"


def test_the_bbox_leg_still_lands(doc_launcher):
    mod, transport, opens = doc_launcher
    transport.arrive(mod.BBOX_NS, {"x": 10, "y": 20, "w": 800, "h": 600})
    assert opens == [("bbox", {"x": 10, "y": 20, "w": 800, "h": 600})]


def test_both_namespaces_are_declared_global_before_the_subscription(doc_launcher):
    """Subscribe-only sources declare too ([DEC-COMPOSER-SEND-GLOBAL-SCOPE]): a
    namespace left AUTO with a local subscriber resolves LOCAL, which is how a
    message dies in-process LOOKING SENT."""
    mod, _transport, _opens = doc_launcher
    declared = dict(source_bus.declared_namespaces(mod.SRC))
    assert declared == {mod.INTENT_NS: "global", mod.BBOX_NS: "global"}, declared


def test_decisions_launcher_gets_the_same_treatment(seam, monkeypatch):
    """The second launcher, converted by reference to the first — asserted
    rather than assumed, because 'the same pattern' is how forks start."""
    import decisions_viewer_launcher as mod
    opens = []
    monkeypatch.setattr(mod.DecisionsViewerLauncher, "open",
                        lambda self, scope=None: opens.append(scope) or "opened")
    transport, stop, thread = _start(mod, opens, geometry_store=None)
    try:
        assert transport.patterns == ["_app/*"]
        transport.arrive(mod.INTENT_NS, True)
        transport.arrive(mod.INTENT_NS, True)
        assert len(opens) == 2, (
            f"the repeated Open intent was deduped: {opens}")
        declared = dict(source_bus.declared_namespaces(mod.SRC))
        assert declared == {mod.INTENT_NS: "global", mod.BBOX_NS: "global"}
    finally:
        stop.set()
        thread.join(timeout=3.0)
