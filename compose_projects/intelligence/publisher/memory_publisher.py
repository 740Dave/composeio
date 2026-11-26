#!/usr/bin/env python3
"""memory_publisher — project the instance's LOCAL MEMORY (the automemory index +
its entry files) onto the bus as `self/<id>/memories`, so the console's
Configuration pane can render a "Local Memory" list -> read-only note detail
([DEC-LOCAL-MEMORY-CONFIG-SECTION]).  It is a DELTA on `agents_skills_publisher`:
same shape (state on the bus, behaviour in the widget), same file-load-for-body
discipline (the small INDEX rides the bus, the large note BODIES do not — the
detail widget loads each note's `.md` by `path`).

WHAT IT READS:
  * `<memory-dir>/MEMORY.md`  — the automemory INDEX, one bullet per note:
      `- [Title](note-file.md) — one-line hook`
    Each bullet becomes one list row.  The index (not a dir glob) is the source
    of truth for the LIST + its curated order.

WHERE THE MEMORY DIR IS (instance/cwd-scoped — NOT hardcoded): the automemory
store sits in the instance's Claude PROJECT dir, beside its session transcripts:
`~/.claude/projects/<slug>/memory`.  `data_sources.py` derives it per-instance
(the `memory/` sibling of the pinned session .jsonl's project dir) and passes it
as `--memory-dir`, exactly as it passes `--claude-dir` to agents_skills_publisher.
`resolve_dir()` here mirrors `memory_monitor.resolve_dir`: CLI arg > env > a
last-resort default.

WIRE CONTRACT: `self/<id>/memories` carries a JSON ARRAY of `{name, hook, path}`
(published on the GLOBAL leg, `composeio/global/self/<id>/memories`).  `path` is
the ABSOLUTE path to the note `.md` (the memory dir is outside the repo, so no
repo-relative form exists); the detail widget loads it read-only for the Note body.

NB the PLURAL `memories`: `self/<id>/memory` is ALREADY owned by the automemory
FOOTPRINT gauge ([DEC-MEMORY-BLOAT-MONITOR], a {pct,status,...} dict the
intelligence_card ingests).  The LIST projection rides `self/<id>/memories` so the
two never collide.

Run:   python3 memory_publisher.py --id architect \
                                   --memory-dir ~/.claude/projects/<slug>/memory \
                                   --mqtt mqtt://mqtt-broker.local:1883
       python3 memory_publisher.py --once        # print the array, no bus
       python3 memory_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "memory"                     # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])

# This file is compose_projects/intelligence/publisher/<me>.py, so repo root = .../composeio
# is THREE dirnames above _THIS. Two stops at compose_projects/.
_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS)))

MEMORY_INDEX_NAME = "MEMORY.md"
# Last-resort default (mirrors memory_monitor.DEFAULT_DIR) — only used when no
# --memory-dir/env is given.  The launch path (data_sources) ALWAYS passes an
# instance-derived --memory-dir, so this is a bare fallback, not the real source.
_DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                            os.path.expanduser("~").replace(os.sep, "-"), "memory")

_MAX_HOOK = 4000        # a generous bound; the detail shows the full hook

# One index bullet:  `- [Title](target.md) — hook`  (en/em dash, hyphen, or colon
# separator all tolerated; the separator + hook are optional).
_ROW_RE = re.compile(
    r"^\s*[-*]\s*\[(?P<name>[^\]]+)\]\((?P<target>[^)]+)\)"
    r"\s*(?:[—–\-:]\s*(?P<hook>.*))?$")


def resolve_dir(cli_dir: Optional[str] = None) -> str:
    """CLI arg > $COMPOSEIO_MEMORY_DIR > $MEMORY_MONITOR_DIR > the last-resort
    default.  Mirrors memory_monitor.resolve_dir (shared env for one store)."""
    raw = (cli_dir or os.environ.get("COMPOSEIO_MEMORY_DIR")
           or os.environ.get("MEMORY_MONITOR_DIR") or _DEFAULT_DIR)
    return os.path.expanduser(raw)


def _one_line(hook: Any) -> str:
    """Collapse a (possibly multiline) hook to one clean, bounded line."""
    return " ".join(str(hook or "").split())[:_MAX_HOOK].rstrip()


def read_memories(memory_dir: str) -> List[Dict[str, Any]]:
    """Parse `<memory-dir>/MEMORY.md` into [{name, hook, path}] in INDEX ORDER
    (the curated order is meaningful — unlike agents/skills we do NOT re-sort).
    `path` is the absolute path to the note `.md`.  A missing/garbled index or a
    non-`.md` link is skipped gracefully — a bad file never kills the read."""
    index = os.path.join(memory_dir, MEMORY_INDEX_NAME)
    try:
        with open(index, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    seen = set()
    for line in text.splitlines():
        m = _ROW_RE.match(line)
        if not m:
            continue
        target = m.group("target").strip()
        # only local .md notes (skip external links / anchors / the index itself)
        if ("://" in target or not target.lower().endswith(".md")
                or target == MEMORY_INDEX_NAME):
            continue
        # index links are relative to the memory dir; resolve to an absolute path.
        path = os.path.normpath(os.path.join(memory_dir, target))
        if path in seen:
            continue
        seen.add(path)
        out.append({"name": m.group("name").strip(),
                    "hook": _one_line(m.group("hook")),
                    "path": path})
    return out


def project(memory_dir: str) -> List[Dict[str, Any]]:
    """The single data contract: the memory index as [{name, hook, path}]."""
    return read_memories(memory_dir)


def _parse_mqtt(url: str):
    u = url.replace("mqtt://", "").replace("tcp://", "")
    host, _, port = u.partition(":")
    return host or "localhost", int(port or "1883")


def run(memory_dir: str, mqtt_url: str, poll_s: float, ident: str,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 3 — publish-only, session-independent.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe; an unchanged
    memory dir republishes an identical array, which in-process subscribers
    dedup and the wire does not. Correct here — the widget renders the value,
    not the arrival.
    """
    # SINGLETON ([CXC-IC-SINGLETON]) FIRST, before the bus handle.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    label = f"memory-publisher-{ident}"
    lock = acquire_singleton(label)
    if lock is None:
        sys.stderr.write("[memory_publisher] another instance holds the "
                         "singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    ns = f"self/{ident}/memories"
    b = source_bus.acquire_bus(mqtt_url, label=label, bus=bus)
    source_bus.declare(b, ns, scope=Scope.GLOBAL, source=SRC,
                       label="local memory index")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[memory_publisher] {memory_dir} -> {ns} every {poll_s}s\n")

    def _tick() -> None:
        try:
            b.publish_state(ns, project(memory_dir), source=SRC)
        except Exception as exc:   # a bad read must never kill the daemon
            sys.stderr.write(f"[memory_publisher] read error "
                             f"(continuing): {exc!r}\n")

    _tick()                            # publish NOW, then park — see slice 2
    source_bus.park(stop, poll_s=poll_s, on_tick=_tick)
    return 0


def _selftest() -> int:
    import tempfile
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as d:
        # a realistic index: em-dash hooks, a hyphen hook, an external link (skip),
        # a non-.md link (skip), a self-reference (skip), a malformed line (skip).
        with open(os.path.join(d, MEMORY_INDEX_NAME), "w", encoding="utf-8") as f:
            f.write(
                "# Memory Index\n\n"
                "- [Haven fork](haven-hud-fork.md) — David's fork of claude-hud\n"
                "- [rpi5 kiosk](rpi5-kiosk.md) - boots NVMe, launcher autostart\n"
                "- [No hook](loner.md)\n"
                "- [External](https://example.com/x) — not a local note\n"
                "- [Not markdown](notes.txt) — wrong extension\n"
                "- [Index](MEMORY.md) — the index itself\n"
                "just a stray prose line, not a bullet\n")
        for note in ("haven-hud-fork.md", "rpi5-kiosk.md", "loner.md"):
            with open(os.path.join(d, note), "w", encoding="utf-8") as f:
                f.write(f"# {note}\n\nBody of {note}.\n")

        mems = read_memories(d)
        names = [m["name"] for m in mems]
        check(names == ["Haven fork", "rpi5 kiosk", "No hook"],
              f"(1) local .md notes parsed in INDEX ORDER, got {names}")
        check(mems[0]["hook"] == "David's fork of claude-hud",
              f"(2) em-dash hook captured, got {mems[0]['hook']!r}")
        check(mems[1]["hook"] == "boots NVMe, launcher autostart",
              f"(3) hyphen-separated hook captured, got {mems[1]['hook']!r}")
        check(mems[2]["hook"] == "", "(4) missing hook -> empty string")
        check(mems[0]["path"] == os.path.join(d, "haven-hud-fork.md"),
              "(5) path is the ABSOLUTE note path")
        check(all(set(m) == {"name", "hook", "path"} for m in mems),
              f"(6) item shape is {{name,hook,path}}, got {set(mems[0])}")
        check(os.path.isfile(mems[0]["path"]), "(7) note path resolves on disk")
        # external + non-.md + self-reference + prose all skipped
        check("External" not in names and "Not markdown" not in names
              and "Index" not in names,
              "(8) external / non-md / self-ref / prose all skipped")
        # JSON round-trip (the wire form) + determinism
        check(json.loads(json.dumps(mems)) == mems, "(9) JSON-round-trippable")
        check(read_memories(d) == read_memories(d), "(10) deterministic")
        # a missing index yields [] (never raises)
        check(read_memories(os.path.join(d, "nope")) == [],
              "(11) missing memory dir -> [] (graceful)")

        # resolve_dir precedence: CLI wins, then env
        check(resolve_dir(d) == d, "(12) CLI --memory-dir wins")
        os.environ["COMPOSEIO_MEMORY_DIR"] = d
        try:
            check(resolve_dir(None) == d, "(13) $COMPOSEIO_MEMORY_DIR resolves")
        finally:
            del os.environ["COMPOSEIO_MEMORY_DIR"]

    if fails:
        for m in fails:
            sys.stderr.write(f"FAIL {m}\n")
        return 1
    print("PASS: memory_publisher selftest (13/13)")
    return 0


def main(argv: Optional[List[str]] = None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Project the instance's local memory index to the bus as "
                    "self/<id>/memories.")
    ap.add_argument("--memory-dir", default=None,
                    help="the automemory dir (index MEMORY.md + note *.md); "
                         "default: $COMPOSEIO_MEMORY_DIR / $MEMORY_MONITOR_DIR")
    ap.add_argument("--id", default="architect")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--poll", type=float, default=30.0)
    ap.add_argument("--once", action="store_true",
                    help="print the array and exit (no bus)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    memory_dir = resolve_dir(a.memory_dir)
    if a.once:
        print(json.dumps(project(memory_dir), indent=2))
        return 0
    return run(memory_dir, a.mqtt, a.poll, a.id, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
