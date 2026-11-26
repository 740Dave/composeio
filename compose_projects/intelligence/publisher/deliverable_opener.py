#!/usr/bin/env python3
"""deliverable_opener — the Open actuation for the console Deliverables card.

The compact card's row-tap publishes `_app/deliverable/open = <path>` (a bus
COMMAND, not a state — [DEC-DELIVERABLE-HISTORY-WIDGET]). This daemon is the
actuator that closes the loop:
  1. STAMP the per-viewer opened-state (`~/.composeio/deliverable-opened.json`,
     path -> last-opened ISO) — which flips the card's `unseen` to seen on the
     deliverable_publisher's next cycle (bold -> plain, David's locked UX).
  2. OPEN the file locally (`open` on macOS / `xdg-open` on Linux), resolved
     against the repo root.

Actuator discipline (the launcher-actuator idiom): it DECIDES nothing — it opens
the path the widget named and records that it did. Paths come from the ledger
(the sweep populated them from the local tree); it refuses anything that escapes
the repo root (no arbitrary-path open from a stray bus publisher).

Run:   python3 deliverable_opener.py --mqtt mqtt://mqtt-broker.local:1883 --repo-root /path/to/composeio
       python3 deliverable_opener.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# [SPEC:CXC-SOURCES-ON-BUS] slice 6 — the ONE bus seam.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

NS = "_app/deliverable/open"
# ADDITIVE reveal seam ([CXC-IC-CONFIG-LOADED]): a general "reveal this repo path
# in the OS file browser" command. The Agents/Skills list panes publish their
# source DIRECTORY here (.claude/agents / .claude/skills) so an (Open) button
# reveals it in Finder. Distinct from NS: it opens DIRS too and does NOT touch the
# deliverable seen-state (a dir is not a deliverable). Same repo-root guard.
REVEAL_NS = "_app/fs/open"
SRC = "deliverable-opener"          # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
_OPENED_DEFAULT = os.path.expanduser("~/.composeio/deliverable-opened.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp_opened(opened_path: str, rel_path: str, now: Optional[str] = None) -> None:
    """Record that *rel_path* was opened now (atomic-ish write)."""
    try:
        with open(opened_path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        state = {}
    state[rel_path] = now or _now_iso()
    os.makedirs(os.path.dirname(opened_path) or ".", exist_ok=True)
    tmp = opened_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, opened_path)


def safe_abspath(repo_root: str, rel_path: str) -> Optional[str]:
    """Resolve *rel_path* under *repo_root*, refusing any escape (`..`, absolute
    outside the root). Returns the absolute path or None if it escapes/doesn't exist."""
    root = os.path.realpath(repo_root)
    cand = os.path.realpath(os.path.join(root, rel_path))
    if os.path.commonpath([root, cand]) != root:
        return None
    return cand if os.path.exists(cand) else None


def _open_cmd() -> Optional[list]:
    if sys.platform == "darwin":
        return ["open"]
    if sys.platform.startswith("linux"):
        return ["xdg-open"]
    return None


def handle_open(rel_path: str, repo_root: str, opened_path: str,
                do_open: bool = True) -> str:
    """Stamp opened-state + (optionally) open the file. Returns a status string."""
    rel_path = str(rel_path).strip()
    if not rel_path:
        return "empty path — ignored"
    abspath = safe_abspath(repo_root, rel_path)
    if abspath is None:
        return f"refused (escapes repo root or missing): {rel_path!r}"
    stamp_opened(opened_path, rel_path)          # mark seen regardless of the open backend
    if do_open:
        cmd = _open_cmd()
        if cmd is None:
            return f"stamped seen; no opener for platform {sys.platform}: {rel_path}"
        try:
            subprocess.Popen(cmd + [abspath],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            return f"stamped seen; open failed ({exc!r}): {rel_path}"
    return f"opened + stamped seen: {rel_path}"


def handle_reveal(rel_path: str, repo_root: str, do_open: bool = True) -> str:
    """Reveal a repo PATH (file OR directory) in the OS file browser — the
    additive REVEAL_NS actuation ([CXC-IC-CONFIG-LOADED]).  Repo-root guarded like
    handle_open, but allows DIRECTORIES and does NOT stamp deliverable seen-state
    (a source dir is not a deliverable).  On macOS `open <dir>` reveals it in
    Finder."""
    rel_path = str(rel_path).strip()
    if not rel_path:
        return "empty path — ignored"
    abspath = safe_abspath(repo_root, rel_path)     # exists-check covers dirs too
    if abspath is None:
        return f"refused (escapes repo root or missing): {rel_path!r}"
    if do_open:
        cmd = _open_cmd()
        if cmd is None:
            return f"no opener for platform {sys.platform}: {rel_path}"
        try:
            subprocess.Popen(cmd + [abspath],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as exc:
            return f"reveal failed ({exc!r}): {rel_path}"
    return f"revealed: {rel_path}"


def run(mqtt_url: str, repo_root: str, opened_path: str, *,
        bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 6 — the FIRST `loop_forever` source.

    THE PARKING CHANGE IS THE POINT OF THIS SLICE ([CXC-SOB-RUN-BLOCKS]). This
    daemon used to stay alive inside `client.loop_forever()`. Through the bus
    there is no loop to block on — the transport runs its own — and a `run()`
    that simply RETURNS does not error: `DataSourceSupervisor._guarded` reads a
    clean return as "this source yielded its host singleton" and re-invokes it
    every `reclaim_poll_s` forever, leaking a duplicate subscription each time.
    No crash, no red gauge. So it parks on `source_bus.park(stop)`, which is
    also what makes `stop()` finish instead of hanging a console bounce.

    REPEAT DELIVERY: REQUIRED, and obtained via `subscribe_pattern`
    ([CXC-SOB-SUBSCRIBE]). This is an IDEMPOTENT INTENT — "open this path" — and
    the human taps the same row twice on purpose. `Namespace.publish_state`
    notifies EXACT subscribers only when `new != old`, so a plain
    `bus.subscribe(NS, ...)` would deliver the first tap and silently swallow
    every identical one after it: the file would simply not re-open, with no
    error anywhere. Pattern subscribers are notified UNCONDITIONALLY
    (`_notify_pattern_subs` fires on every publish, local and inbound alike),
    which closes the regression with NO payload change — the alternative was a
    nonce in the widget's publish, a wire change Stage A forbids.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance
    # yields. Stays ABOVE the bus acquisition so a yielding instance never
    # opens a transport it is about to abandon ([CXC-SOB-CONTRACT-FROZEN]).
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("deliverable-opener")
    if lock is None:
        sys.stderr.write("[deliverable_opener] another deliverable-opener holds the singleton; yielding\n")
        return 0

    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    # DECLARED even though this source only SUBSCRIBES: a namespace left at
    # AUTO with a local subscriber resolves to LOCAL, which is exactly
    # [DEC-COMPOSER-SEND-GLOBAL-SCOPE] — the widget's publish would die
    # in-process LOOKING SENT once this daemon shares the console's bus.
    for _ns in (NS, REVEAL_NS):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="app intent")
    source_bus.manifest(SRC)

    def _on_app(ns: str, value, _old) -> None:
        # The callback contract is `(namespace, new_value, old_value)` and the
        # value arrives DECODED — no `json.loads(msg.payload)` here; that would
        # raise on every message.
        try:
            rel = value.get("val", value) if isinstance(value, dict) else value
            if ns == REVEAL_NS:
                status = handle_reveal(str(rel), repo_root)
            elif ns == NS:
                status = handle_open(str(rel), repo_root, opened_path)
            else:
                return                    # another `_app/*` member; not ours
            sys.stderr.write(f"[deliverable_opener] {status}\n")
        except Exception as exc:  # a bad command must never kill the daemon
            sys.stderr.write(f"[deliverable_opener] error (continuing): {exc!r}\n")

    b.subscribe_pattern("_app/*", _on_app)
    sys.stderr.write(f"[deliverable_opener] listening _app/* (filtering {NS} + "
                     f"{REVEAL_NS}); repo={repo_root}; opened={opened_path}\n")
    source_bus.park(stop)
    return 0


def _selftest() -> int:
    import tempfile
    root = tempfile.mkdtemp()
    # a real file under root + an escape attempt
    os.makedirs(os.path.join(root, "docs"), exist_ok=True)
    open(os.path.join(root, "docs", "a.md"), "w").close()
    opened = os.path.join(root, "opened.json")

    s = handle_open("docs/a.md", root, opened, do_open=False)
    assert "stamped seen" in s or "opened + stamped" in s, f"(1) {s}"
    state = json.load(open(opened))
    assert "docs/a.md" in state, "(2) opened-state stamped"

    # escape refused
    assert "refused" in handle_open("../../etc/passwd", root, opened, do_open=False), "(3) escape refused"
    assert "refused" in handle_open("/etc/passwd", root, opened, do_open=False), "(4) abs escape refused"
    # missing refused
    assert "refused" in handle_open("docs/nope.md", root, opened, do_open=False), "(5) missing refused"
    # empty ignored
    assert "ignored" in handle_open("  ", root, opened, do_open=False), "(6) empty ignored"

    # the stamp flips unseen in the publisher's view: last-opened AFTER modified => seen
    sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
    import deliverable_publisher as dp
    ledger = ("---\nmachine-maintained: true\n---\n## Deliverables\n\n"
              "### [DELIV-a-1] docs/a.md\n- path: docs/a.md\n- modified: 2026-07-01\n"
              "- missing: false\n- kind: primer\n- pending-enrichment: []\n\n## Run History\n```jsonl\n```\n")
    payload = dp.compute_payload(ledger, None, json.load(open(opened)))
    row = payload["recent"][0]
    assert row["unseen"] is False, f"(7) opened => seen: {row}"

    # REVEAL seam: a DIRECTORY reveals (no stamp), an escape is refused, and the
    # deliverable seen-state is NOT touched by a reveal.
    os.makedirs(os.path.join(root, ".claude", "agents"), exist_ok=True)
    before = json.load(open(opened))
    r = handle_reveal(".claude/agents", root, do_open=False)
    assert "revealed" in r, f"(8) dir revealed: {r}"
    assert json.load(open(opened)) == before, "(9) reveal does NOT stamp seen-state"
    assert "refused" in handle_reveal("../../etc", root, do_open=False), "(10) reveal escape refused"
    assert "ignored" in handle_reveal("  ", root, do_open=False), "(11) reveal empty ignored"
    print("PASS: deliverable_opener selftest (11/11)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description="Open actuation for the console Deliverables card.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--repo-root", default=os.getcwd())
    ap.add_argument("--opened", default=_OPENED_DEFAULT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, a.repo_root, a.opened, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
