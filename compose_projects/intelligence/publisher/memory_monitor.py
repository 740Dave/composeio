#!/usr/bin/env python3
"""memory_monitor — a DETERMINISTIC sensor on the automemory store ([DEC-MEMORY-BLOAT-MONITOR]).

David, from experience with Intelligences instantiated across 40+ sessions: automemory fills up
and becomes a maintenance problem. This is the PERCEIVE half of the perceive/act split the decision
graph specifies: a pure-code monitor (no model — models are reserved for judgment) that stats the
automemory directory (`MEMORY.md` index + the `*.md` entry files it indexes) and publishes a health
reading to the bus. The ACT half is a separate concern (the decision-archivist, judgment-side,
NEVER auto-deletes — the owner disposes); this daemon only perceives and flags.

READ-ONLY, hard invariant: this module never writes, moves, or deletes anything under the memory
directory or anywhere under `.claude/`. Sizes come from `os.stat()`; the ONLY file ever opened for
read is `MEMORY.md`, and only to count index-entry lines.

Sibling pattern: this daemon mirrors `deliverable_watcher.py` / `meta_publisher.py` (poll loop,
`--once`, `--selftest`) and PUBLISHES through `ComposeIOBus` via the shared `composeio.source_bus`
seam ([SPEC:CXC-SOURCES-ON-BUS]) — it names a NAMESPACE and a SCOPE, never a transport. Same wire
shape (`{ns,type,src,ts,scope,seq,val}`, [DEC-CONSOLE-MQTT-STARTUP-RACE] queue-before-connect
semantics, which now live inside the MQTT transport where they belong). It previously built an
`MQTTTransport` itself: no paho, but still a substrate name, and just as dark on a CAN-only device.

Namespace: `self/memory`, scope `global` -> topic `composeio/global/self/memory` (the same
`self/*` -> `composeio/global/self/*` mapping self_publisher/meta_publisher use).

Payload:
    {"bytes": int, "files": int, "entries": int, "budget_bytes": int, "pct": float,
     "status": "ok|warn|bloated", "dir": str, "ts": <iso8601 utc>}

Budget/thresholds (env-overridable, see below): default budget 256 KiB of memory-entry files
(`*.md` under the directory, excluding the `MEMORY.md` index itself) — a soft ceiling picked
because a typical entry file here runs 1-8 KB (see the real dir: 15 files, ~96 KB today, i.e.
~37% of budget — comfortably `ok`), so 256 KiB gives headroom for ~30-50 entries before `warn`
and flags real bloat (100+ entries or a few oversized files) before it becomes a PITA. `warn`
at >=0.7 (~180 KiB), `bloated` at >=0.9 (~230 KiB) — matching the thresholds named in the task.

Run:   python3 memory_monitor.py --mqtt mqtt://mqtt-broker.local:1883 [--dir <path>] [--interval 30]
       python3 memory_monitor.py --once      # compute + print once; publish if broker reachable
       python3 memory_monitor.py --selftest  # compute-logic only, no broker, no network
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# repo-root import shim (sibling daemons do the same: compose_projects/intelligence/publisher/X.py -> <root>/python)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))

NS = "self/memory"

#: ~/.claude/projects/<HOME with "/" -> "-">/memory/ (the HOME-launched project). Override with --dir.
DEFAULT_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                           os.path.expanduser("~").replace(os.sep, "-"), "memory", "")
DEFAULT_BUDGET_BYTES = 256 * 1024       # 256 KiB soft ceiling — see module docstring
DEFAULT_WARN_THRESHOLD = 0.7
DEFAULT_BLOATED_THRESHOLD = 0.9
DEFAULT_INTERVAL_S = 30.0               # memory changes slowly; no need to poll fast
MEMORY_INDEX_NAME = "MEMORY.md"


def resolve_dir(cli_dir: Optional[str] = None) -> Path:
    """CLI arg > $MEMORY_MONITOR_DIR > the Architect's default path."""
    raw = cli_dir or os.environ.get("MEMORY_MONITOR_DIR") or DEFAULT_DIR
    return Path(raw).expanduser()


def resolve_budget(cli_budget: Optional[int] = None) -> int:
    if cli_budget is not None:
        return int(cli_budget)
    env = os.environ.get("MEMORY_MONITOR_BUDGET_BYTES")
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    return DEFAULT_BUDGET_BYTES


def resolve_thresholds(cli_warn: Optional[float] = None,
                        cli_bloated: Optional[float] = None) -> tuple[float, float]:
    def _env_float(name: str, default: float) -> float:
        v = os.environ.get(name)
        if v:
            try:
                return float(v)
            except ValueError:
                pass
        return default

    warn = cli_warn if cli_warn is not None else _env_float(
        "MEMORY_MONITOR_WARN_THRESHOLD", DEFAULT_WARN_THRESHOLD)
    bloated = cli_bloated if cli_bloated is not None else _env_float(
        "MEMORY_MONITOR_BLOATED_THRESHOLD", DEFAULT_BLOATED_THRESHOLD)
    return warn, bloated


def compute_stats(mem_dir: Path) -> dict:
    """READ-ONLY stat of the automemory directory. Never opens a file except
    `MEMORY.md` (to count index-entry lines) — every size comes from `os.stat()`.

    `bytes`   — total size of every file directly in the directory (the index +
                every entry file: the whole footprint on disk).
    `files`   — count of individual memory `*.md` entry files, i.e. every `.md`
                file EXCLUDING the `MEMORY.md` index itself.
    `entries` — count of index-entry lines in `MEMORY.md` (lines that open a
                bullet, `- [...`) — the number of memory topics the index
                actually advertises, which can drift from `files` (an entry
                file un-indexed, or an index line pointing at a since-removed
                file) — that drift is itself a useful bloat signal, left for
                the archivist/owner to judge, not silently reconciled here.

    A missing directory is not an error: it degrades to all-zero stats (a
    freshly-registered Intelligence with no automemory yet is `ok`, not a crash).
    """
    if not mem_dir.exists() or not mem_dir.is_dir():
        return {"bytes": 0, "files": 0, "entries": 0}

    total_bytes = 0
    file_count = 0
    try:
        children = list(mem_dir.iterdir())
    except OSError:
        children = []
    for entry in children:
        try:
            if not entry.is_file():
                continue
            total_bytes += entry.stat().st_size
        except OSError:
            continue
        if entry.suffix == ".md" and entry.name != MEMORY_INDEX_NAME:
            file_count += 1

    entries = 0
    index_path = mem_dir / MEMORY_INDEX_NAME
    if index_path.exists():
        try:
            with index_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    if line.lstrip().startswith("- ["):
                        entries += 1
        except OSError:
            pass

    return {"bytes": total_bytes, "files": file_count, "entries": entries}


def classify(total_bytes: int, budget_bytes: int, warn_threshold: float,
             bloated_threshold: float) -> tuple[float, str]:
    """`pct` (fraction of budget consumed, unclamped so >100% is visible) and
    `status` from the configurable thresholds. `budget_bytes<=0` degrades to
    pct=0.0/'ok' rather than dividing by zero (a misconfigured budget must
    never crash the daemon)."""
    pct = round(total_bytes / budget_bytes, 4) if budget_bytes > 0 else 0.0
    if pct >= bloated_threshold:
        status = "bloated"
    elif pct >= warn_threshold:
        status = "warn"
    else:
        status = "ok"
    return pct, status


def build_payload(mem_dir: Path, budget_bytes: int, warn_threshold: float,
                   bloated_threshold: float, now: Optional[datetime] = None) -> dict:
    stats = compute_stats(mem_dir)
    pct, status = classify(stats["bytes"], budget_bytes, warn_threshold, bloated_threshold)
    ts = (now or datetime.now(timezone.utc)).astimezone(timezone.utc) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "bytes": stats["bytes"],
        "files": stats["files"],
        "entries": stats["entries"],
        "budget_bytes": budget_bytes,
        "pct": pct,
        "status": status,
        "dir": str(mem_dir),
        "ts": ts,
    }


def _parse_mqtt_url(url: str) -> tuple[str, int]:
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883



# [SPEC:CXC-SOURCES-ON-BUS] slice 3 — the bus seam. This module used to build an
# `MQTTTransport` itself. That touched no paho and was still a substrate name:
# a module that imports ONE transport class is exactly as dark on a CAN-only
# device as one that hand-rolls a topic string. It now states a NAMESPACE and a
# SCOPE and lets `transport_registry` choose.
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "memory-monitor"             # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "memory-monitor"


def _make_bus(mqtt_url: str, bus=None):
    """The bus this daemon publishes on ([CXC-SOB-HANDLE]).

    Replaces the old `_make_transport`, which built an `MQTTTransport` directly
    and so pinned this module to ONE substrate.
    """
    return source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)


def _publish(bus, payload: dict, seq: int) -> None:
    """`seq` accepted and IGNORED — the bus owns sequencing
    ([DD-SOB-SEQ-SEMANTICS]); the parameter stays for the `--once` call site."""
    bus.publish_state(NS, payload, source=SRC)


def run(mem_dir: Path, mqtt_url: str, budget_bytes: int, warn_threshold: float,
        bloated_threshold: float, interval: float, _loops: Optional[int] = None,
        *, bus=None, stop=None) -> None:
    """Poll loop: compute + publish every `interval` seconds. `_loops` bounds
    the loop for tests (None = forever).

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe. A steady memory
    dir republishes an identical payload; in-process subscribers dedup it, the
    wire does not.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("memory-monitor")
    if lock is None:
        sys.stderr.write("[memory_monitor] another memory-monitor holds the singleton; yielding\n")
        return
    b = _make_bus(mqtt_url, bus)
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="automemory budget")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[memory_monitor] watching {mem_dir} -> {NS} every "
                     f"{interval}s; budget={budget_bytes}B "
                     f"warn>={warn_threshold} bloated>={bloated_threshold}\n")
    sys.stderr.flush()

    def _tick() -> None:
        try:
            payload = build_payload(mem_dir, budget_bytes, warn_threshold,
                                    bloated_threshold)
            _publish(b, payload, 0)
            sys.stderr.write(f"[memory_monitor] {payload['status']}: "
                             f"{payload['bytes']}B / {payload['budget_bytes']}B "
                             f"({payload['pct']*100:.1f}%), {payload['files']} files, "
                             f"{payload['entries']} entries\n")
            sys.stderr.flush()
        except Exception as exc:  # a bad read must never kill the daemon
            sys.stderr.write(f"[memory_monitor] read error (continuing): {exc!r}\n")
            sys.stderr.flush()

    if _loops is None:
        _tick()                        # publish NOW, then park — see slice 2
        source_bus.park(stop, poll_s=interval, on_tick=_tick)
    else:
        for _i in range(_loops):       # bounded, for tests only
            _tick()
            if _i + 1 < _loops:
                time.sleep(interval)


# ---------------------------------------------------------------------------
# selftest — COMPUTE LOGIC ONLY, no broker, no network ([CXC-DS] --selftest discipline)
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile

    fails: list[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)

        # (a) empty dir (no MEMORY.md yet) -> all-zero stats, 'ok'
        stats0 = compute_stats(root)
        check(stats0 == {"bytes": 0, "files": 0, "entries": 0},
              f"(a) empty dir should be all-zero, got {stats0}")
        pct0, status0 = classify(stats0["bytes"], DEFAULT_BUDGET_BYTES,
                                 DEFAULT_WARN_THRESHOLD, DEFAULT_BLOATED_THRESHOLD)
        check(pct0 == 0.0 and status0 == "ok", f"(a) empty dir should be ok/0.0, got {pct0}/{status0}")

        # (b) a crafted MEMORY.md (3 index lines, one heading, one blank) + 3 entry files
        #     of KNOWN sizes -> exact byte/file/entry counts.
        index_text = (
            "# Memory Index\n"
            "\n"
            "- [alpha](alpha.md) — first topic\n"
            "- [beta](beta.md) — second topic\n"
            "- [gamma](gamma.md) — third topic (not yet written — index/files can drift)\n"
        )
        (root / MEMORY_INDEX_NAME).write_text(index_text)
        (root / "alpha.md").write_text("a" * 100)
        (root / "beta.md").write_text("b" * 250)
        (root / "not-memory.txt").write_text("ignored, not a .md entry")  # must NOT count as a file

        stats1 = compute_stats(root)
        # byte lengths (not char lengths) — index_text has multi-byte em-dashes
        expected_bytes = (len(index_text.encode("utf-8")) + 100 + 250
                          + len("ignored, not a .md entry".encode("utf-8")))
        check(stats1["bytes"] == expected_bytes,
              f"(b) total bytes should include index+entries+other files, "
              f"got {stats1['bytes']} expected {expected_bytes}")
        check(stats1["files"] == 2,
              f"(b) files should count exactly the 2 real .md entries (alpha,beta; MEMORY.md "
              f"excluded, .txt excluded), got {stats1['files']}")
        check(stats1["entries"] == 3,
              f"(b) entries should count the 3 index bullet lines (alpha/beta/gamma — gamma "
              f"indexed but not yet a file, on purpose), got {stats1['entries']}")

        # (c) pct/status thresholds: budget tiny -> forces 'ok' -> 'warn' -> 'bloated'
        # as bytes cross 70%/90% of budget.
        budget = stats1["bytes"] * 2  # bytes is exactly 50% of budget -> 'ok'
        pct_ok, status_ok = classify(stats1["bytes"], budget, 0.7, 0.9)
        check(status_ok == "ok" and abs(pct_ok - 0.5) < 1e-9,
              f"(c) 50% of budget should be 'ok'/0.5, got {status_ok}/{pct_ok}")

        budget_warn = int(stats1["bytes"] / 0.75)  # bytes is 75% of budget -> 'warn'
        pct_warn, status_warn = classify(stats1["bytes"], budget_warn, 0.7, 0.9)
        check(status_warn == "warn",
              f"(c) 75% of budget should be 'warn' (>=0.7,<0.9), got {status_warn}/{pct_warn}")

        budget_bloated = int(stats1["bytes"] / 0.95)  # bytes is 95% of budget -> 'bloated'
        pct_bloated, status_bloated = classify(stats1["bytes"], budget_bloated, 0.7, 0.9)
        check(status_bloated == "bloated",
              f"(c) 95% of budget should be 'bloated' (>=0.9), got {status_bloated}/{pct_bloated}")

        # (c2) a zero/negative budget must degrade to 0.0/'ok', never raise (ZeroDivisionError guard)
        pct_zero, status_zero = classify(stats1["bytes"], 0, 0.7, 0.9)
        check(pct_zero == 0.0 and status_zero == "ok",
              f"(c2) budget_bytes<=0 must degrade to 0.0/'ok', got {pct_zero}/{status_zero}")

        # (d) build_payload wires stats+classify+dir+ts into the exact wire shape, with a
        # deterministic ts when `now` is supplied.
        fixed_now = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)
        payload = build_payload(root, budget, 0.7, 0.9, now=fixed_now)
        check(set(payload) == {"bytes", "files", "entries", "budget_bytes", "pct",
                               "status", "dir", "ts"},
              f"(d) payload shape must match the spec exactly, got {set(payload)}")
        check(payload["bytes"] == stats1["bytes"] and payload["files"] == 2
              and payload["entries"] == 3 and payload["budget_bytes"] == budget
              and payload["status"] == "ok" and payload["dir"] == str(root),
              f"(d) payload fields wrong: {payload}")
        check(payload["ts"] == "2026-07-22T12:00:00Z",
              f"(d) ts must be real UTC iso8601 (Z-suffixed), got {payload['ts']}")
        check(json.loads(json.dumps(payload)) == payload, "(d) payload must be JSON-round-trippable")

        # (e) a non-existent directory degrades to all-zero/'ok', never raises.
        ghost = root / "does-not-exist"
        payload_ghost = build_payload(ghost, budget, 0.7, 0.9, now=fixed_now)
        check(payload_ghost["bytes"] == 0 and payload_ghost["files"] == 0
              and payload_ghost["entries"] == 0 and payload_ghost["status"] == "ok",
              f"(e) a missing directory must degrade to all-zero/'ok', got {payload_ghost}")

        # (f) resolve_dir / resolve_budget / resolve_thresholds precedence: CLI > env > default.
        os.environ["MEMORY_MONITOR_DIR"] = str(root)
        os.environ["MEMORY_MONITOR_BUDGET_BYTES"] = "12345"
        os.environ["MEMORY_MONITOR_WARN_THRESHOLD"] = "0.5"
        os.environ["MEMORY_MONITOR_BLOATED_THRESHOLD"] = "0.8"
        try:
            check(resolve_dir(None) == root, "(f) env MEMORY_MONITOR_DIR should be honored")
            check(resolve_dir("/explicit") == Path("/explicit"),
                  "(f) an explicit CLI dir must win over env")
            check(resolve_budget(None) == 12345, "(f) env budget should be honored")
            check(resolve_budget(999) == 999, "(f) an explicit CLI budget must win over env")
            w, b = resolve_thresholds(None, None)
            check(w == 0.5 and b == 0.8, f"(f) env thresholds should be honored, got {w}/{b}")
            w2, b2 = resolve_thresholds(0.1, 0.2)
            check(w2 == 0.1 and b2 == 0.2, "(f) explicit CLI thresholds must win over env")
        finally:
            for k in ("MEMORY_MONITOR_DIR", "MEMORY_MONITOR_BUDGET_BYTES",
                      "MEMORY_MONITOR_WARN_THRESHOLD", "MEMORY_MONITOR_BLOATED_THRESHOLD"):
                os.environ.pop(k, None)

        # (g) READ-ONLY invariant: after every check above, the temp dir's files are byte-identical
        # to what this test wrote — the monitor must never have written/moved/deleted anything.
        check((root / "alpha.md").read_text() == "a" * 100,
              "(g) alpha.md must be unmodified (read-only invariant)")
        check((root / "beta.md").read_text() == "b" * 250,
              "(g) beta.md must be unmodified (read-only invariant)")
        check((root / MEMORY_INDEX_NAME).read_text() == index_text,
              "(g) MEMORY.md must be unmodified (read-only invariant)")
        listing = sorted(p.name for p in root.iterdir())
        check(listing == sorted(["MEMORY.md", "alpha.md", "beta.md", "not-memory.txt"]),
              f"(g) no extra files may have been created, got {listing}")

    if fails:
        for m in fails:
            sys.stderr.write("FAIL " + m + "\n")
        return 1
    sys.stderr.write("PASS: memory_monitor selftest (7/7 groups)\n")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None,
                    help="automemory directory (default: $MEMORY_MONITOR_DIR or the built-in default)")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--budget-bytes", type=int, default=None,
                    help="soft byte ceiling (default: $MEMORY_MONITOR_BUDGET_BYTES or 256 KiB)")
    ap.add_argument("--warn-threshold", type=float, default=None)
    ap.add_argument("--bloated-threshold", type=float, default=None)
    ap.add_argument("--interval", type=float,
                    default=float(os.environ.get("MEMORY_MONITOR_INTERVAL_S", DEFAULT_INTERVAL_S)),
                    help="poll interval in seconds (default: $MEMORY_MONITOR_INTERVAL_S or 30)")
    ap.add_argument("--once", action="store_true",
                    help="compute + print one payload; publish it too if the broker is reachable")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    mem_dir = resolve_dir(a.dir)
    budget = resolve_budget(a.budget_bytes)
    warn_t, bloated_t = resolve_thresholds(a.warn_threshold, a.bloated_threshold)

    if a.once:
        payload = build_payload(mem_dir, budget, warn_t, bloated_t)
        print(json.dumps(payload, indent=2))
        try:
            once_bus = _make_bus(a.mqtt)
            transport = once_bus.global_transport
            deadline = time.time() + 2.0
            while transport is not None and not transport.is_connected \
                    and time.time() < deadline:
                time.sleep(0.1)
            if transport is not None and transport.is_connected:
                _publish(once_bus, payload, 1)
                time.sleep(0.3)  # give paho's loop thread a beat to flush the publish
                sys.stderr.write(f"[memory_monitor] published {NS} @ {a.mqtt}\n")
            else:
                sys.stderr.write(f"[memory_monitor] broker {a.mqtt} not reachable "
                                 f"within 2s — printed only, not published\n")
            once_bus.stop()
        except Exception as exc:
            sys.stderr.write(f"[memory_monitor] publish attempt failed (printed only): {exc!r}\n")
        return 0

    run(mem_dir, a.mqtt, budget, warn_t, bloated_t, a.interval,
        bus=bus, stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
