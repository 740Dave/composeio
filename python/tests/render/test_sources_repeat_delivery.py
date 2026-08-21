"""The identical-value dedup hazard, made executable.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-SUBSCRIBE], acceptance criterion SOB-C9.

THE HAZARD. `Namespace.publish_state` notifies local subscribers ONLY when
`new != old`. Confirmed directly: publishing an identical value three times
delivers it twice. Any subscriber that needs to act on every REPEAT of an
identical intent therefore MISSES the repeat — and misses it silently, which on
the glass reads as "sometimes the window doesn't open".

Routing the console's sources through the bus does not CREATE this (the receiving
side already deduped, because `_dispatch_incoming` calls `ns.publish_state` too),
but it makes it apply in-process as well, and it is the single most likely source
of a confusing report from this arc. So the two halves are pinned here:

  1. The BUS's behaviour itself — dedup local, never on the wire. If either half
     of that ever changes, every "this is safe because..." argument in a
     converted source's docstring needs re-reading.
  2. The one PAYLOAD SHAPE the console actually depends on for repeat delivery:
     `thread/narration` carries `turn` + `ts`, so two identical TEXTS are two
     distinct VALUES. `transcript_publisher`'s own selftest check (o) requires a
     genuinely repeated line to narrate BOTH times; that requirement is only met
     because of those two fields. An edit that trimmed the payload down to
     `{from, text}` for tidiness would silently swallow the second line, on the
     glass, with no error — and no existing test would have noticed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio import source_bus                            # noqa: E402
from composeio.bus import ComposeIOBus                      # noqa: E402
from composeio.namespace import Scope                       # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
for _d in (_REPO_ROOT / "compose_projects" / "intelligence" / "publisher",
           _REPO_ROOT / "compose_projects" / "intelligence" / "tools"):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


class _Rec:
    def __init__(self): self.sent = []
    def start(self): pass
    def stop(self): pass
    def send(self, m): self.sent.append(m)
    def subscribe(self, ns, cb): pass
    def subscribe_all(self, cb): pass
    def subscribe_pattern(self, p): pass

    @property
    def is_connected(self): return True


@pytest.fixture
def bus_and_sink():
    source_bus.reset_for_tests()
    sink = _Rec()
    bus = ComposeIOBus(bus_id="repeat-probe", global_transport=sink)
    bus.start(announce_time=False)
    yield bus, sink
    source_bus.reset_for_tests()


def test_the_wire_never_dedups_an_identical_repeat(bus_and_sink):
    """Every converted publisher's "this is safe" argument rests on this."""
    bus, sink = bus_and_sink
    source_bus.declare(bus, "probe/x", scope=Scope.GLOBAL, source="probe")
    value = {"path": "docs/a.md", "change": "modified"}
    for _ in range(3):
        bus.publish_state("probe/x", value, source="probe")
    assert len(sink.sent) == 3, (
        "the bus DROPPED an identical repeat before the transport — every "
        "converted source's repeat-delivery reasoning is now wrong")


def test_local_subscribers_DO_dedup_an_identical_repeat(bus_and_sink):
    """The other half. Pinned so the hazard is a documented property, not a
    surprise found on the glass."""
    bus, _sink = bus_and_sink
    source_bus.declare(bus, "probe/y", scope=Scope.GLOBAL, source="probe")
    hits = []
    bus.subscribe("probe/y", lambda n, new, old: hits.append(new))
    value = {"path": "docs/a.md"}
    for _ in range(3):
        bus.publish_state("probe/y", value, source="probe")
    assert hits == [value], (
        f"expected exactly ONE local delivery for three identical publishes "
        f"(new != old dedup), got {len(hits)}. If this now delivers three, the "
        f"hazard is gone and [CXC-SOB-SUBSCRIBE] can be relaxed — check before "
        f"celebrating, a subscriber somewhere may rely on the dedup.")
    # ...and a CHANGED value gets through immediately.
    bus.publish_state("probe/y", {"path": "docs/b.md"}, source="probe")
    assert len(hits) == 2


def test_narration_repeats_survive_because_the_payload_carries_turn_and_ts():
    """`transcript_publisher` selftest (o) requires a genuinely repeated line to
    narrate BOTH times. That only holds because the payload is distinguishable.

    This asserts the PROPERTY, at the real publisher, rather than trusting the
    docstring: two identical assistant TEXT blocks must produce two DIFFERENT
    payload values, so the dedup cannot absorb the second bubble.
    """
    import transcript_publisher as tp

    pub = tp.TranscriptPublisher()
    entry = {"type": "assistant", "timestamp": "2026-08-08T12:00:00Z",
             "message": {"content": [{"type": "text", "text": "the same line"}]}}
    first = [p for ns, p in pub.process_entry(dict(entry)) if ns == tp.NS_NARRATION]
    second = [p for ns, p in pub.process_entry(dict(entry)) if ns == tp.NS_NARRATION]
    assert first and second, "a repeated assistant text block must narrate twice"
    assert first[0] != second[0], (
        f"two identical narration lines produced the SAME payload "
        f"({first[0]!r}) — `Namespace.publish_state`'s new != old dedup will "
        f"swallow the second bubble on the glass, with no error. The `turn` and "
        f"`ts` fields are what make them distinct; do not trim them.")
    assert "turn" in first[0] and "ts" in first[0], (
        f"the narration payload lost `turn`/`ts`: {first[0]!r}")


def test_the_console_sources_that_dedup_say_so_in_their_docstring():
    """[CXC-SOB-SUBSCRIBE]: "Each converted subscriber MUST state in its
    docstring whether it needs repeat delivery."

    Extended here to every converted source, not just subscribers: a publisher's
    repeat behaviour is just as load-bearing (it is the publisher's payload shape
    that decides whether a subscriber CAN see a repeat), and a rule that only
    binds subscribers leaves the half that actually controls the outcome
    undocumented.
    """
    import re
    ds_text = (_REPO_ROOT / "compose_projects" / "intelligence" / "console" / "data_sources.py").read_text()
    registered = sorted(set(re.findall(r'_src\(\s*"([a-z_]+)"', ds_text)))

    missing = []
    for name in registered:
        for d in (_REPO_ROOT / "compose_projects" / "intelligence" / "publisher",
                  _REPO_ROOT / "compose_projects" / "intelligence" / "tools"):
            p = d / f"{name}.py"
            if p.is_file():
                break
        text = p.read_text()
        if "source_bus" not in text:
            continue                      # not converted yet — its slice will add it
        if "REPEAT DELIVERY" not in text:
            missing.append(name)
    assert not missing, (
        f"{missing} are converted but do not state their repeat-delivery "
        f"behaviour. [CXC-SOB-SUBSCRIBE] requires it in the docstring, because "
        f"'sometimes it doesn't fire' is the failure this hazard produces and "
        f"nobody can debug it from the code alone.")
