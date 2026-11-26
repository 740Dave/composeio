#!/usr/bin/env python3
"""deliverable_watcher.py — the FILESYSTEM half of the realtime deliverable alerts.

The `deliverable_monitor` tails the Intelligence's transcript for MY tool-writes
(Write/Edit) — so it catches only changes *I* make. It is blind to a file David
edits in his own editor (that never enters the transcript). This watcher closes
that gap ([DEC-WATCHER-EXTERNAL-EDITS], David 2026-07-21: "I thought we had a
python script that checks for changes to files ... an update notice would be
emitted and you could listen for that to know when I've changed the file"):

  * it POLLS the tracked project roots (reusing the deliverable-sweep's own
    walk + filters, so it watches exactly what the ledger tracks),
  * content-hashes candidates and compares to an in-memory snapshot, and
  * on a real content change emits the SAME `deliverable/modified` (or
    `deliverable/new`) alert the monitor emits — so the console banner renders
    an external edit identically to one of mine.

Division of labor (the sweep-vs-realtime split, [DEC-DELIVERABLE-LEDGER]):
  - `deliverable_monitor` — transcript tail; instant on MY writes.
  - `deliverable_watcher` (this) — filesystem poll; catches ANY change incl.
    David's, at poll cadence.
  - `deliverable_sweep`  — batch reconciler; owns the LEDGER writes + removals.

PERSIST-THEN-EMIT ([CXC-DS-REALTIME-PERSISTS]): the durable Artifacts surfaces (rail
card + Artifacts tab) render `self/deliverables`, which `deliverable_publisher`
projects PURELY from the LEDGER. So a realtime detector that only EMITS a banner —
as this watcher originally did — flashes a transient alert while the deliverable stays
INVISIBLE on every durable surface until the next batch sweep. That is exactly how
David's externally-edited `docs/watch_reports/away-watch-2026-07-25-report.md` fired a
banner yet never reached the rail card or the Artifacts tab. So on a real content
change this watcher now PERSISTS via the SAME shared `run_sweep(write=True)` the
transcript monitor uses (node-keyed + mechanical-only + atomic co-writer,
[CXC-DS-MULTI-WRITER-SAFE]) BEFORE it emits — de-forked, never a second ad-hoc append.
Hash-gated, so a touched-but-unchanged mtime is silent: no persist, no churn, no alert.

Note (known, benign): for a file I write, the monitor fires instantly AND this
watcher fires on its next poll — a possible double-banner for MY OWN edits. The
banner auto-dismisses; coordination-free de-dup is a documented future refinement
(the watcher is the one that matters — it's the only path for David's edits).

Run:  python3 deliverable_watcher.py --repo-root . --mqtt mqtt://mqtt-broker.local:1883 --interval 3
      # --ledger defaults to bootstrap_paths.DELIVERABLE_LEDGER (the single source)
Selftest: python3 deliverable_watcher.py --selftest   (no broker, no network)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

# repo-root import shim (sibling daemons do the same)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))

from composeio.deliverable_sweep.ledger import Ledger          # noqa: E402
from composeio.deliverable_sweep.sweep import (                 # noqa: E402
    run_sweep, scan_project_root)
# ledger location comes from the ONE source of truth, never a literal
# ([CXC-DS-LEDGER-LOCATION-SINGLE-SOURCE]).
from composeio.bootstrap_paths import DELIVERABLE_LEDGER_REL    # noqa: E402

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

DEFAULT_INTERVAL_S = 3.0
SRC = "deliverable-watcher"        # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "deliverable-watcher"
#: the two namespaces this source can emit. Declared as a PAIR up front — the
#: alert kind is data-dependent, so declaring lazily would leave whichever kind
#: happened not to fire this session undeclared and absent from the SOB-C3 boot
#: manifest. A namespace you only declare when you happen to use it is a
#: namespace nobody can audit.
ALERT_NAMESPACES = ("deliverable/new", "deliverable/modified")


def persist_ledger(ledger_path: str, session: Optional[str] = None) -> None:
    """Persist detected filesystem changes to the ledger via the SHARED batch
    sweep ([CXC-DS-REALTIME-PERSISTS]) — the same node-keyed, mechanical-only,
    atomic co-writer path the transcript monitor uses (`_process_write` ->
    `run_sweep(write=True)`). Reuse-by-reference, never a second append impl:
    without this, an external edit only ever produces a transient banner and the
    deliverable never reaches the ledger -> never `self/deliverables` -> never the
    rail card or the Artifacts tab. Tolerant: a sweep hiccup must never stall the
    poll loop (the emit still fires)."""
    from pathlib import Path as _P
    run_sweep(ledger_path=_P(ledger_path), roots=None, write=True,
              session_key=session)


def snapshot(ledger: Ledger) -> Dict[str, Tuple[str, str]]:
    """{node_id: (relpath, content_hash)} over every tracked root, using the
    ledger's own roots + filters (so we watch exactly what the sweep tracks)."""
    roots = ledger.config.get("project-roots") or ["."]
    out: Dict[str, Tuple[str, str]] = {}
    for root_label in roots:
        entries = scan_project_root(
            root_label,
            ledger.config.get("include-globs", []),
            ledger.config.get("exclude-globs", []),
            ledger.config.get("do-not-track-dirs", []),
            ledger.config.get("do-not-track-suffixes", []),
        )
        for nid, e in entries.items():
            out[nid] = (e.path, e.content_hash)
    return out


def diff_snapshots(
    old: Dict[str, Tuple[str, str]],
    new: Dict[str, Tuple[str, str]],
) -> list[dict]:
    """Alerts for content changes. NEW node -> 'new'; SAME node, changed hash ->
    'modified'. Removals are the batch sweep's job (parity with the monitor,
    which also never emits 'removed')."""
    alerts: list[dict] = []
    for nid, (relpath, h) in new.items():
        prev = old.get(nid)
        if prev is None:
            change = "new"
        elif prev[1] != h:
            change = "modified"
        else:
            continue
        alerts.append({"path": relpath, "name": os.path.basename(relpath),
                       "node_id": nid, "change": change})
    return alerts


def _emit(bus, ns: str, val: dict, seq: int) -> None:
    """Match deliverable_monitor._emit byte-for-byte: a STATE message (the
    dispatch lane widgets' on_bus_state subscribes — the lane the monitor's
    first cut got wrong), global scope, the same `src`.

    STATE and not EVENT, deliberately ([CXC-SOB-WIRE-DELTAS] delta 4): a
    source's own global publish echoes back through the transport, and
    `_dispatch_incoming` ABSORBS an identical STATE echo but would DOUBLE-FIRE
    an EVENT one.

    `seq` is accepted and IGNORED — the bus owns sequencing now
    ([DD-SOB-SEQ-SEMANTICS]). The parameter stays so the injected-`emit` test
    doubles keep their signature.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]) — this source NEEDS IT AND DOES NOT
    HAVE IT. A PRE-EXISTING hazard, surfaced by this conversion, NOT caused by
    it; recorded here rather than fixed, because the fix is a payload change and
    Stage A is frozen wire ([CXC-SOB-CONTRACT-FROZEN]).

    The alert payload is `{path, name, node_id, change}` — no hash, no
    timestamp, no nonce. So editing the SAME file twice emits two BYTE-IDENTICAL
    values on `deliverable/modified`, and `Namespace.publish_state`'s new != old
    rule absorbs the second: the banner fires for the first edit and stays
    silent for the second.

    This is already true TODAY, before the conversion, and for the same reason —
    the receiving side deduped either way, because `_dispatch_incoming` also
    goes through `ns.publish_state`. Routing through the bus only makes it true
    in-process as well.

    THE FIX, when Stage A's freeze lifts: carry a nonce (the content hash the
    differ already computed, or the detection timestamp) in the payload so
    consecutive alerts differ. That is a wire change and needs its own slice.
    """
    bus.publish_state(ns, val, source=SRC)


def run(ledger_path: str, mqtt_url: str, interval: float,
        emit: Optional[Callable] = None, _loops: Optional[int] = None,
        session: Optional[str] = None,
        persist: Optional[Callable] = None,
        *, bus=None, stop=None) -> None:
    """Poll the tracked roots; on a real content change PERSIST it to the ledger
    ([CXC-DS-REALTIME-PERSISTS]) and THEN emit the alert. `emit`/`persist`
    injectable for tests; `_loops` bounds the loop for tests (None = forever)."""
    if persist is None:
        persist = persist_ledger
    lp = Path(ledger_path)
    prev = snapshot(Ledger.load(lp))
    sys.stderr.write(f"[deliverable_watcher] watching {len(prev)} tracked files; "
                     f"ledger={ledger_path}; interval={interval}s\n")
    sys.stderr.flush()

    if emit is None:
        # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global. Only the REAL
        # bus path (emit is None) contends for the lock; an injected-emit test
        # never touches it. A second instance yields without connecting.
        try:
            from singleton import acquire_singleton
        except ImportError:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            os.pardir, "publisher"))
            from singleton import acquire_singleton
        lock = acquire_singleton(LABEL)
        if lock is None:
            sys.stderr.write("[deliverable_watcher] another deliverable-watcher holds the singleton; yielding\n")
            return                     # the ONE legitimate early return
        b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
        for _ns in ALERT_NAMESPACES:
            source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                               label="deliverable alert")
        source_bus.manifest(SRC)

        def emit(ns, val, seq):  # noqa: E306
            _emit(b, ns, val, seq)

    state = {"prev": prev, "seq": 0}

    def _cycle() -> None:
        cur = snapshot(Ledger.load(lp))
        alerts = diff_snapshots(state["prev"], cur)
        if alerts:
            # PERSIST-THEN-EMIT ([CXC-DS-REALTIME-PERSISTS]): land the change
            # in the ledger via the shared sweep so the durable Artifacts
            # surfaces (rail card + tab) show it — not just the banner. One
            # sweep reconciles every change this poll saw. Tolerant: a sweep
            # error must not stall the loop or swallow the alert.
            try:
                persist(ledger_path, session)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write("[deliverable_watcher] persist error "
                                 f"(continuing): {exc!r}\n")
                sys.stderr.flush()
        for alert in alerts:
            state["seq"] += 1
            emit(f"deliverable/{alert['change']}", alert, state["seq"])
            sys.stderr.write(f"[deliverable_watcher] {alert['change']}: "
                             f"{alert['path']}\n")
            sys.stderr.flush()
        state["prev"] = cur

    if _loops is None:
        # THE REAL PATH: park through the ONE helper ([CXC-SOB-RUN-BLOCKS]).
        # `park` waits BEFORE its first tick, which preserves this watcher's
        # existing sleep-then-poll order exactly. (A tick before the first wait
        # would diff the snapshot against itself and emit nothing, so the order
        # is not load-bearing — but it is preserved deliberately, not by luck.)
        source_bus.park(stop, poll_s=interval, on_tick=_cycle)
    else:
        for _ in range(_loops):        # bounded, for tests only
            time.sleep(interval)
            _cycle()


# ---------------------------------------------------------------------------
# selftest — no broker, no network ([CXC-DS] --selftest discipline)
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile

    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # a real ledger over a temp root, a real file the watcher tracks
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "docs").mkdir()
        doc = root / "docs" / "note.md"
        doc.write_text("v1\n")
        led = root / "ledger.lts"
        # minimal ledger config: track docs/*.md under this root
        L = Ledger.load(led)
        L.config["project-roots"] = [str(root)]
        L.config["include-globs"] = ["docs/*.md"]
        L.save(led)

        s0 = snapshot(Ledger.load(led))
        check(len(s0) == 1, f"(a) snapshot should track 1 file, got {len(s0)}")

        # (b) NO change -> NO alert (hash-gated; a re-scan of an unchanged tree is silent)
        s_same = snapshot(Ledger.load(led))
        check(diff_snapshots(s0, s_same) == [],
              "(b) unchanged tree must emit nothing")

        # (c) an EXTERNAL content edit -> exactly one 'modified' alert (David's case)
        doc.write_text("v2 — edited in David's editor\n")
        s1 = snapshot(Ledger.load(led))
        alerts = diff_snapshots(s0, s1)
        check(len(alerts) == 1 and alerts[0]["change"] == "modified"
              and alerts[0]["name"] == "note.md",
              f"(c) external edit should raise ONE modified alert, got {alerts}")

        # (d) a touch WITHOUT a content change -> silent (mtime bumped, hash same)
        os.utime(doc, None)
        s2 = snapshot(Ledger.load(led))
        check(diff_snapshots(s1, s2) == [],
              "(d) touch-without-change must be silent (hash-gated, not mtime)")

        # (e) a NEW tracked file -> a 'new' alert
        (root / "docs" / "two.md").write_text("hello\n")
        s3 = snapshot(Ledger.load(led))
        new_alerts = diff_snapshots(s1, s3)
        check(any(a["change"] == "new" and a["name"] == "two.md"
                  for a in new_alerts),
              f"(e) a new tracked file should raise a 'new' alert, got {new_alerts}")

        # (f) emit shape matches the monitor's env (type:'state', global lane)
        captured = []
        run(str(led), "mqtt://unused", interval=0.0,
            emit=lambda ns, val, seq: captured.append((ns, val)), _loops=0)
        # _loops=0 does no polling; assert the env builder directly instead.
        # Post-[SPEC:CXC-SOURCES-ON-BUS] this goes through the BUS, so the check
        # moved down one layer: a real ComposeIOBus with a recording transport,
        # asserting the envelope the transport would frame. Same contract, and
        # now it also pins the SCOPE RESOLUTION (Scope.GLOBAL -> "global"), which
        # the hand-framed version could not get wrong and the bus path can.
        class _RecTransport:
            def __init__(self): self.sent = []
            def start(self): pass
            def stop(self): pass
            def send(self, msg): self.sent.append(msg)
            def subscribe(self, ns, cb): pass
            def subscribe_all(self, cb): pass
            def subscribe_pattern(self, p): pass
            @property
            def is_connected(self): return True

        from composeio.bus import ComposeIOBus
        rec = _RecTransport()
        probe_bus = ComposeIOBus(bus_id="watcher-selftest", global_transport=rec)
        probe_bus.start(announce_time=False)
        for _ns in ALERT_NAMESPACES:
            source_bus.declare(probe_bus, _ns, scope=Scope.GLOBAL, source=SRC)
        _emit(probe_bus, "deliverable/modified",
              {"path": "docs/note.md", "name": "note.md", "node_id": "N", "change": "modified"}, 1)
        check(len(rec.sent) == 1, f"(f0) exactly one message on the wire, got {len(rec.sent)}")
        env = rec.sent[0].to_json()
        check(env["type"] == "state" and env["scope"] == "global"
              and env["ns"] == "deliverable/modified" and env["src"] == SRC,
              f"(f) emit env must match the monitor (state/global lane), got {env}")
        check(list(env) == ["ns", "type", "src", "ts", "scope", "seq", "val"],
              f"(f2) wire key order changed: {list(env)}")
        # BOTH alert namespaces are declared up front, not lazily on first use —
        # otherwise the kind that never fired this session is absent from the
        # SOB-C3 boot manifest and nobody can audit it.
        declared = dict(source_bus.declared_namespaces(SRC))
        check(set(declared) == set(ALERT_NAMESPACES)
              and set(declared.values()) == {"global"},
              f"(f3) both alert namespaces must be declared global: {declared}")

        # (g) PERSIST-THEN-EMIT ([CXC-DS-REALTIME-PERSISTS]) — the DEFECT-A fix.
        # (g1) persist_ledger lands a freshly-created external file as a ledger
        # node (missing:false) so the publisher's projection would carry it —
        # this is the exact break: emit-only left the ledger empty.
        newdoc = root / "docs" / "away-watch-2026-07-25-report.md"
        newdoc.write_text("# external editor write\n")
        persist_ledger(str(led), session="2026-07-25")
        parsed = Ledger.load(led)
        nid = next((k for k, v in parsed.nodes.items()
                    if v.get("path", "").endswith("away-watch-2026-07-25-report.md")),
                   None)
        check(nid is not None and parsed.nodes[nid].get("missing") == "false",
              "(g1) persist_ledger must write the external file into the ledger")

        # (g2) the run() loop PERSISTS before it emits when a change is seen —
        # asserted without poll timing by driving snapshot() prev(empty)->cur(1).
        _mod = sys.modules[__name__]
        _orig = _mod.snapshot
        _snaps = [{}, {"DELIV-X": ("docs/x.md", "h1")}]
        _calls = {"i": 0}

        def _fake_snapshot(_ledger):
            i = min(_calls["i"], len(_snaps) - 1)
            _calls["i"] += 1
            return _snaps[i]

        persisted, emitted = [], []
        try:
            _mod.snapshot = _fake_snapshot
            run(str(led), "mqtt://unused", interval=0.0,
                emit=lambda ns, val, seq: emitted.append(ns),
                persist=lambda lp, sess: persisted.append((lp, sess)),
                session="2026-07-25", _loops=1)
        finally:
            _mod.snapshot = _orig
        check(len(persisted) == 1 and persisted[0][1] == "2026-07-25",
              f"(g2) run() must persist on a change, got {persisted}")
        check(emitted == ["deliverable/new"],
              f"(g2) run() must still emit after persisting, got {emitted}")

    if fails:
        for m in fails:
            sys.stderr.write("FAIL " + m + "\n")
        return 1
    sys.stderr.write("PASS: deliverable_watcher selftest (8/8)\n")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", default=DELIVERABLE_LEDGER_REL)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    ap.add_argument("--session", default=None,
                    help="session key stamped on newly-persisted nodes "
                         "(generated-in-session), matching deliverable_monitor")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    run(a.ledger, a.mqtt, a.interval, session=a.session,
        bus=bus, stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
