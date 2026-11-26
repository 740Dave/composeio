#!/usr/bin/env python3
"""deliverable_monitor — the LIVE complement to the batch deliverable_sweep.

Where the sweep is the boundary reconciler (session-start/end; catches
out-of-band changes + removals), THIS is the realtime cadence ([DEC-DELIVERABLE-
LIVE-MONITOR]): it tails the Intelligence's transcript `.jsonl` for a qualifying
document write, updates the ONE affected ledger node via the shared sweep, and
emits a `deliverable/new` (or `deliverable/modified`) semantic alert on the bus —
which the console's live-alert banner surfaces with an Open button.

Two cadences, ONE ledger. The sweep's ledger writes are idempotent + node-keyed
(node id = f(root, path)) and never clobber semantic fields, so the monitor's
single-node updates and the sweep's full reconcile compose safely.

The tailer is a thin sibling of `transcript_publisher.py` (seek-to-end, follow,
tolerate a mid-write partial line). A qualifying event = a `Write` (or a doc-
writing MCP tool) tool_use whose target path matches the ledger's include/exclude
filters. It runs the sweep in --write mode scoped to that path's root, reads back
what the sweep classified (new vs modified), and emits the matching alert.

[SPEC:CXC-SOURCES-ON-BUS] slice 5b.  Two things a reader arriving from the spec
needs, because the spec is wrong about the first:

  IT DOES NOT SUBSCRIBE.  [CXC-SOB-INVENTORY] row 3 classifies this source
  "pub+sub".  Measured 2026-08-10: it subscribes NOTHING.  Its input is the
  TRANSCRIPT (a file it tails), not a namespace; the only bus traffic it ever
  produced was the two outbound alerts.  Its slice-5b membership is real for a
  different reason — it is transcript-based, so it needs the pinned session and
  the [CXC-SOB-RUN-BLOCKS] stop-plumbing that `transcript_publisher` needed in
  slice 4 — but no callback rewrite was required.

  REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]) is UNCHANGED by the conversion, and this
  is worth stating because it looks like it should have changed.  Two writes to
  the same file produce two DIFFERENT alerts (`new` then `modified`), so the
  common case never dedups.  Where the payload IS identical — the same file
  modified twice with no intervening sweep change — the console already deduped
  it before this slice: an alert arriving over MQTT is dispatched through
  `_dispatch_incoming` -> `ns.publish_state`, which notifies local subscribers
  only when `new != old`.  In-process publication takes the same path.  Same
  behaviour on the glass, and the wire still carries every message.

Run:   python3 deliverable_monitor.py --jsonl <session.jsonl> --repo-root . \
             --mqtt mqtt://mqtt-broker.local:1883   # --ledger defaults to bootstrap_paths.DELIVERABLE_LEDGER
       python3 deliverable_monitor.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Set

# repo-root import shim (sibling daemons do the same) — so the ledger-location
# constant resolves at MODULE LOAD, and the `--ledger` default below is the ONE
# source of truth (bootstrap_paths), never a stale path literal ([CXC-DS-LEDGER-
# LOCATION-SINGLE-SOURCE]).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
from composeio.bootstrap_paths import DELIVERABLE_LEDGER_REL  # noqa: E402

# [SPEC:CXC-SOURCES-ON-BUS] slice 5b — the ONE bus seam. This module used to
# hand-frame onto a paho client through `composeio.bus_envelope`; it now states a
# NAMESPACE and a SCOPE and lets `transport_registry` choose the substrate
# ([CXC-SOB-THESIS]).
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "deliverable-monitor"        # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "deliverable-monitor"      # = the old paho client-id ([CXC-SOB-HANDLE])
# The two alert namespaces, known STATICALLY — unlike the transcript publishers'
# dynamic sets, so both are declared up front and one manifest line covers them.
NS_NEW = "deliverable/new"
NS_MODIFIED = "deliverable/modified"


class _Stopped(Exception):
    """Internal: the supervisor set our `stop` Event while we were parked inside
    `follow_lines`. Raised from the idle hook so the infinite tail generator
    unwinds instead of waiting for a transcript line that may never come."""

# Doc-writing tools whose target is a candidate deliverable.
# `Edit` is ESSENTIAL and was missed in the first cut: creating a doc uses Write, but
# MODIFYING one — by far the commoner event — uses Edit, so a monitor without it silently
# ignores most real changes. The selftest passed anyway because it only exercised Write:
# machine-green over a hole the live glass found in seconds ([FIXTURE-registration-protocol-P6]).
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
_WRITE_MCP_HINTS = ("write", "doc", "publish", "save")   # mcp__*: substring match on the tool leaf


def _target_path(tool_name: str, tool_input: dict) -> Optional[str]:
    """The file path a write-class tool_use targets, or None if it isn't a doc write."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name in _WRITE_TOOLS:
        return tool_input.get("file_path") or None
    if tool_name.startswith("mcp__"):
        leaf = tool_name.split("__", 2)[-1].lower()
        if any(h in leaf for h in _WRITE_MCP_HINTS):
            for k in ("path", "file_path", "file", "target"):
                if isinstance(tool_input.get(k), str):
                    return tool_input[k]
    return None


def qualifying_writes(entry: dict) -> List[str]:
    """The deliverable-candidate file paths written by an assistant entry's
    tool_use blocks (pure; used by the tailer + the selftest)."""
    out: List[str] = []
    if not isinstance(entry, dict) or entry.get("type") != "assistant":
        return out
    for b in (entry.get("message") or {}).get("content") or []:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            p = _target_path(b.get("name", ""), b.get("input") or {})
            if p:
                out.append(p)
    return out


def _rel(repo_root: str, path: str) -> Optional[str]:
    """Repo-relative form of a written path, or None if it's outside the root."""
    root = os.path.realpath(repo_root)
    full = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
    try:
        if os.path.commonpath([root, full]) != root:
            return None
    except ValueError:
        return None
    return os.path.relpath(full, root)


def _emit(bus, ns: str, val: dict) -> None:
    """Publish an alert as a STATE message — NOT an event.

    The bus dispatches the two on different lanes (`bus.py` `_dispatch_incoming`):
    a STATE message calls `ns.publish_state()`, which is what notifies the
    `bus.subscribe(ns, cb)` subscribers the render engine wires for every widget
    (`on_bus_state`). An EVENT message routes to `ns.publish_event()` — a separate
    subscriber set the widgets never listen on, and it carries `event_type`/`data`
    rather than `val`. Emitting `type: "event"` here (the first cut) meant the alert
    reached the broker, reached the console, and was then dispatched down a lane with
    no listener: everything "worked" and no banner ever appeared.

    STATE is also what [CXC-SOB-WIRE-DELTAS] requires of every converted source:
    an EVENT self-echo is NOT deduplicated and would double-fire every in-process
    handler. `seq` is gone — the bus owns sequencing ([DD-SOB-SEQ-SEMANTICS]).
    """
    bus.publish_state(ns, val, source=SRC)


def _process_write(rel_path: str, ledger: str, repo_root: str,
                   session: Optional[str], seen: Set[str]):
    """Run the shared sweep in --write mode, classify rel_path (new/modified) by
    its node id, return an alert dict or None. Reuses the deterministic tool — no
    reimplementation; the delta's node id is the same f(root, path) the sweep uses."""
    sys.path.insert(0, os.path.join(repo_root, "python"))
    from composeio.deliverable_sweep.sweep import run_sweep, node_id_for
    from pathlib import Path
    # Pass NO roots: the ledger already carries its configured project-roots, and
    # handing it this daemon's (absolute) repo_root would append a second root for
    # the same tree — doubling every node, since a node id is f(project-root, path).
    _, result = run_sweep(ledger_path=Path(ledger), roots=None,
                          write=True, session_key=session)
    nid = node_id_for(repo_root, rel_path)
    kind = None
    if nid in result.delta.new:
        kind = "new"
    elif nid in result.delta.modified:
        kind = "modified"
    if kind is None:
        return None
    seen.add(rel_path)
    return {"path": rel_path, "name": os.path.basename(rel_path),
            "node_id": nid, "change": kind}


def run(jsonl: str, ledger: str, repo_root: str, mqtt_url: str,
        session: Optional[str], poll_s: float, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 5b — publish-only, TRANSCRIPT-based.

    See the module docstring for the two spec corrections (this source does NOT
    subscribe; repeat delivery is unchanged by the conversion).

    ``run()`` MUST NOT RETURN while the source is alive ([CXC-SOB-RUN-BLOCKS]).
    Under raw paho the blocking was incidental — `follow_lines` is an INFINITE
    generator, so the tail loop never ended. That still holds, which means the
    hazard here is the OTHER leg of the invariant: a source that blocks forever
    and cannot be STOPPED hangs console shutdown and leaves an orphan holding the
    flock across a `/bounce-console`. `follow_lines`' idle hook is the only code
    that runs on a QUIET transcript, so — exactly as `transcript_publisher` does —
    it is where the stop signal is noticed, by raising `_Stopped` to unwind the
    generator rather than waiting for a line that may never come.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from tail_follow import follow_lines  # the ONE partial-line-safe tail reader (de-forked)
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("deliverable-monitor")
    if lock is None:
        sys.stderr.write("[deliverable_monitor] another deliverable-monitor holds the singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    # EXPLICIT scope, never Scope.AUTO ([CXC-SOB-DECLARE]). `deliverable/*` is an
    # intra-app CANDIDATE, census-gated ([CXC-SOB-INVENTORY] family rules); Stage A
    # pins GLOBAL, its current effective scope, so the wire does not move. The
    # console scene declares BOTH `scope: global` too, so a disagreement here
    # would raise at startup rather than let one side silently win.
    for ns in (NS_NEW, NS_MODIFIED):
        source_bus.declare(b, ns, scope=Scope.GLOBAL, source=SRC,
                           label="deliverable alert")
    source_bus.manifest(SRC)

    sys.stderr.write(f"[deliverable_monitor] tailing {jsonl}; ledger={ledger}; "
                     f"emit {NS_NEW} + {NS_MODIFIED}\n")
    seen: Set[str] = set()

    def _idle() -> None:
        # The ONLY code that runs on a quiet transcript — so the ONLY place a stop
        # signal can be noticed without waiting for the next line.
        if stop is not None and stop.is_set():
            raise _Stopped()

    # seek to end — the history is never re-scanned (sibling of transcript_publisher).
    # follow_lines yields only COMPLETE lines: a partial mid-write line (e.g. a large Write/Edit
    # tool_use still being flushed) is BUFFERED and completed on a later poll, never dropped.
    with open(jsonl, encoding="utf-8", errors="replace") as f:
        f.seek(0, os.SEEK_END)
        try:
            for line in follow_lines(f, poll_s=poll_s, on_idle=_idle):
                if stop is not None and stop.is_set():
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for path in qualifying_writes(entry):
                    rel = _rel(repo_root, path)
                    if rel is None:
                        continue
                    try:
                        alert = _process_write(rel, ledger, repo_root, session, seen)
                    except Exception as exc:  # never let one write kill the tailer
                        sys.stderr.write(f"[deliverable_monitor] sweep error (continuing): {exc!r}\n")
                        continue
                    if alert and alert["change"] in ("new", "modified"):
                        _emit(b, f"deliverable/{alert['change']}", alert)
                        sys.stderr.write(f"[deliverable_monitor] {alert['change']}: {rel}\n")
        except _Stopped:
            pass                       # the supervisor asked us to stand down
    return 0


def _selftest() -> int:
    # qualifying_writes: a Write to a doc is caught; a Bash/Read is not
    e_write = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": "docs/x.md"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
    ]}}
    assert qualifying_writes(e_write) == ["docs/x.md"], "(1) Write caught, Bash ignored"
    e_read = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "docs/y.md"}}]}}
    assert qualifying_writes(e_read) == [], "(2) Read is not a write"
    # REGRESSION: Edit is how a doc is MODIFIED — the commonest event. Missing it made the
    # monitor silently ignore most real changes while the selftest still passed.
    e_edit = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": "decisions/graph.lts"}}]}}
    assert qualifying_writes(e_edit) == ["decisions/graph.lts"], "(2b) Edit MUST qualify"
    e_multi = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "MultiEdit", "input": {"file_path": "docs/m.md"}}]}}
    assert qualifying_writes(e_multi) == ["docs/m.md"], "(2c) MultiEdit qualifies"
    e_mcp = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "mcp__docstore__doc_publish",
         "input": {"path": "docs/z.md"}}]}}
    assert qualifying_writes(e_mcp) == ["docs/z.md"], "(3) doc-writing MCP tool caught"
    e_user = {"type": "user", "message": {"content": []}}
    assert qualifying_writes(e_user) == [], "(4) non-assistant ignored"
    # _rel: escape returns None
    import tempfile
    root = tempfile.mkdtemp()
    assert _rel(root, "docs/a.md") == "docs/a.md", "(5) rel within root"
    assert _rel(root, "/etc/passwd") is None, "(6) rel escape -> None"
    # (7) the two alert namespaces are declared STATICALLY and are exactly the two
    #     the emit path can name — so the SOB-C3 manifest cannot lie by omission
    #     ([SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-DECLARE]).
    assert {NS_NEW, NS_MODIFIED} == {f"deliverable/{k}"
                                     for k in ("new", "modified")}, \
        "(7) the declared namespace set drifted from the emitted one"
    print("PASS: deliverable_monitor selftest (7/7)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description="Live deliverable monitor: tail the transcript, alert on doc writes.")
    ap.add_argument("--jsonl", help="the session transcript to tail")
    ap.add_argument("--ledger", default=DELIVERABLE_LEDGER_REL)
    ap.add_argument("--repo-root", default=os.getcwd())
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--session", default=None)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.jsonl:
        ap.error("--jsonl is required (unless --selftest)")
    return run(a.jsonl, a.ledger, a.repo_root, a.mqtt, a.session, a.poll,
               bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
