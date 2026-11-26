#!/usr/bin/env python3
"""deliverable_publisher — project the deliverable-ledger onto the bus as
`self/deliverables`, so the console's compact card + Artifacts view render it
(state on the bus, behavior in the widget — [SPEC:CXC-IC-CANVAS]).

The mechanical/semantic split continues here: the sweep (perceive) maintains the
ledger; the Intelligence (enrich) fills the semantics; THIS daemon just projects
the current ledger state to the bus for the widget. It publishes a compact
payload matching the LOCKED compact-card UX ([DEC-DELIVERABLE-HISTORY-WIDGET]):

  self/deliverables = {
    "stats": {"needs_enrich": N, "new_session": N, "tracked": N},
    "recent": [ {name, path, kind, modified, unseen}, ... up to 5, newest first ],
    "session": "<current session key>"
  }

`unseen` (the bold-when-unseen signal, David's lock) is computed from a tiny
per-viewer opened-state side-file (path -> last-opened ISO) the Open actuation
stamps: unseen = never-opened OR last-opened < modified.

Run:   python3 deliverable_publisher.py --mqtt mqtt://mqtt-broker.local:1883 --session 2026-07-20
       # --ledger defaults to bootstrap_paths.DELIVERABLE_LEDGER (the single source)
       python3 deliverable_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# repo-root import shim (sibling daemons do the same) — the ledger-location
# constant is the ONE source of truth for the --ledger default, never a literal
# ([CXC-DS-LEDGER-LOCATION-SINGLE-SOURCE]).
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))
from composeio.bootstrap_paths import DELIVERABLE_LEDGER_REL  # noqa: E402

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

NS = "self/deliverables"
SRC = "deliverables"               # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "deliverable-publisher"
_NODE = re.compile(r"^### \[(DELIV-[^\]]+)\]\s*(.*)$")
_FIELD = re.compile(r"^- ([a-z-]+):\s*(.*)$")
_OPENED_DEFAULT = os.path.expanduser("~/.composeio/deliverable-opened.json")


def _parse_ledger(text: str) -> List[Dict[str, str]]:
    """Lightweight, tolerant reader of the ledger's ## Deliverables nodes —
    self-contained (no coupling to the sweep's internals); the format is stable
    (`### [DELIV-id] path` then `- field: value` lines)."""
    nodes: List[Dict[str, str]] = []
    cur: Optional[Dict[str, str]] = None
    in_deliverables = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_deliverables = line.strip() == "## Deliverables"
            continue
        if not in_deliverables:
            continue
        m = _NODE.match(line)
        if m:
            cur = {"id": m.group(1), "header_path": m.group(2).strip()}
            nodes.append(cur)
            continue
        if cur is not None:
            f = _FIELD.match(line)
            if f:
                cur[f.group(1)] = f.group(2).strip()
    return nodes


def _enriched(v: str) -> Optional[str]:
    return None if (not v or v == "NEEDS-ENRICHMENT") else v


def _load_opened(path: str) -> Dict[str, str]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def compute_payload(ledger_text: str, session: Optional[str],
                    opened: Dict[str, str]) -> Dict[str, Any]:
    nodes = [n for n in _parse_ledger(ledger_text) if n.get("missing") != "true"]
    needs = sum(1 for n in nodes
                if n.get("pending-enrichment", "[]").strip() not in ("[]", ""))
    new_session = sum(1 for n in nodes
                      if session and n.get("generated-in-session") == session)
    # recent: newest by modified (fallback created), up to 5
    def key(n):
        return (n.get("modified") or n.get("created") or "", n.get("id"))
    recent = sorted(nodes, key=key, reverse=True)[:5]
    rows = []
    for n in recent:
        path = n.get("path", n.get("header_path", ""))
        modified = n.get("modified", "")
        seen = opened.get(path)
        unseen = (seen is None) or (modified and seen < modified)
        rows.append({
            "name": os.path.basename(path),
            "path": path,
            "kind": _enriched(n.get("kind", "")) or "—",
            "modified": modified,
            "unseen": bool(unseen),
        })
    # FULL list for the Artifacts view ([DEC-DELIVERABLE-HISTORY-WIDGET]): every tracked
    # node with the fields the filter/sort list renders, incl. the first-256-chars summary
    # (David's one firm requirement) and the pending-enrichment state.
    def _row(n):
        path = n.get("path", n.get("header_path", ""))
        modified = n.get("modified", "")
        seen = opened.get(path)
        summary = _enriched(n.get("summary", "")) or ""
        pending = n.get("pending-enrichment", "[]").strip()
        pending_list = [] if pending in ("[]", "") else \
            [f.strip() for f in pending.strip("[]").split(",") if f.strip()]
        topics = _enriched(n.get("topics", "")) or ""
        return {
            "id": n.get("id", ""),
            "name": os.path.basename(path), "path": path,
            "kind": _enriched(n.get("kind", "")) or "—",
            "created": n.get("created", ""), "modified": modified,
            "confidentiality": _enriched(n.get("confidentiality", "")) or "",
            "target_audience": _enriched(n.get("target-audience", "")) or "",
            "summary": summary[:256], "summary_len": len(summary),
            "topics": [t.strip() for t in topics.strip("[]").split(",") if t.strip()],
            "unseen": bool((seen is None) or (modified and seen < modified)),
            "pending": pending_list,
        }

    all_rows = [_row(n) for n in sorted(nodes, key=key, reverse=True)]
    return {"stats": {"needs_enrich": needs, "new_session": new_session,
                      "tracked": len(nodes)},
            "recent": rows, "all": all_rows, "session": session or ""}


def _parse_mqtt(url: str):
    u = url.replace("mqtt://", "")
    host, _, port = u.partition(":")
    return host or "localhost", int(port or "1883")


def run(ledger_path: str, mqtt_url: str, session: Optional[str],
        opened_path: str, poll_s: float, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 3 — publish-only, session-independent.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe. An unchanged
    ledger republishes an identical payload; in-process subscribers dedup it and
    the wire does not. Correct — the Artifacts surfaces render the ledger's
    CONTENT, and a re-render of an unchanged list is exactly what nobody needs.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]) FIRST, before the bus handle.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton(LABEL)
    if lock is None:
        sys.stderr.write("[deliverable_publisher] another deliverable-publisher holds the singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="deliverable ledger")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[deliverable_publisher] {ledger_path} -> {NS} every "
                     f"{poll_s}s; opened={opened_path}\n")

    def _tick() -> None:
        try:
            with open(ledger_path, encoding="utf-8") as f:
                text = f.read()
            b.publish_state(NS, compute_payload(text, session,
                                                _load_opened(opened_path)),
                            source=SRC)
        except Exception as exc:  # a bad read must never kill the daemon
            sys.stderr.write(f"[deliverable_publisher] read error (continuing): {exc!r}\n")

    _tick()                            # publish NOW, then park — see slice 2
    source_bus.park(stop, poll_s=poll_s, on_tick=_tick)
    return 0


def _selftest() -> int:
    ledger = """---
machine-maintained: true
---
# Deliverable Ledger

## Config
```yaml
project-roots: [.]
```

## Deliverables

### [DELIV-alpha-1111] docs/a.md
- path: docs/a.md
- format: md
- created: 2026-07-18
- modified: 2026-07-20
- missing: false
- kind: primer
- summary: Alpha.
- pending-enrichment: []
- generated-in-session: 2026-07-20

### [DELIV-beta-2222] docs/b.html
- path: docs/b.html
- format: html
- created: 2026-07-10
- modified: 2026-07-10
- missing: false
- kind: NEEDS-ENRICHMENT
- pending-enrichment: [kind, summary]
- generated-in-session: 2026-07-19

### [DELIV-gone-3333] docs/gone.md
- path: docs/gone.md
- modified: 2026-07-20
- missing: true
- pending-enrichment: [kind]

## Run History
```jsonl
```
"""
    p = compute_payload(ledger, "2026-07-20", {"docs/a.md": "2026-07-21"})
    assert p["stats"]["tracked"] == 2, f"(1) missing excluded: {p['stats']}"
    assert p["stats"]["needs_enrich"] == 1, f"(2) only beta pending: {p['stats']}"
    assert p["stats"]["new_session"] == 1, f"(3) only alpha this session: {p['stats']}"
    names = [r["name"] for r in p["recent"]]
    assert names == ["a.md", "b.html"], f"(4) newest-first, missing gone: {names}"
    a = p["recent"][0]
    assert a["unseen"] is False, "(5) alpha opened AFTER modified -> seen"
    assert a["kind"] == "primer", "(6) enriched kind surfaces"
    b = p["recent"][1]
    assert b["unseen"] is True and b["kind"] == "—", "(7) never-opened + un-enriched"
    # determinism
    assert compute_payload(ledger, "2026-07-20", {}) == \
        compute_payload(ledger, "2026-07-20", {}), "(8) deterministic"
    print("PASS: deliverable_publisher selftest (8/8)")
    return 0


def main(argv: Optional[List[str]] = None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description="Project the deliverable-ledger to the bus as self/deliverables.")
    ap.add_argument("--ledger", default=DELIVERABLE_LEDGER_REL)
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--session", default=None)
    ap.add_argument("--opened", default=_OPENED_DEFAULT)
    ap.add_argument("--poll", type=float, default=4.0)
    ap.add_argument("--once", action="store_true", help="print one payload and exit (no bus)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.once:
        with open(a.ledger, encoding="utf-8") as f:
            print(json.dumps(compute_payload(f.read(), a.session, _load_opened(a.opened)), indent=2))
        return 0
    return run(a.ledger, a.mqtt, a.session, a.opened, a.poll,
               bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
