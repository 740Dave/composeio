#!/usr/bin/env python3
"""[CXC-IC-MONITOR] Cross-implementation recurrence guard: the console gauge and the harness
monitor perceive the SAME, FRESHEST context level — and the 95% pencils-down GUIDANCE fires on
the TRUE level even when the statusline state file has gone STALE.

The defect (live 2026-07-26): the console's context widget showed ~45% while the true context was
~85%. Root cause = a FORK-DIVERGENCE. Two context readers with DIFFERENT source priorities:

  * the console publisher (`compose_projects/intelligence/publisher/self_publisher.py`) read haven-hud's per-session
    context-cache — the native `used_percentage`, persisted every ~300ms INDEPENDENT of the CLI
    redraw — so it saw a FRESH figure.
  * the harness monitor (`compose_projects/intelligence/launcher/launcher.py` build_context_reader) read ONLY the
    `self_sensor --emit-state` statusline state file, which refreshes ONLY on a CLI redraw and so
    FREEZES between redraws.

On the glass the state file was frozen 150 min at 60% while the hud-cache read 85%. The monitor's
crossing check runs against `read_context`, so a FROZEN 60% meant the 90% and — safety-critical —
the 95% GUIDANCE (the pencils-down self-/compact prompt) would fire LATE or NEVER while the true
context sailed past. The prior fix (2026-07-XX, commit 09b80e9) tee'd hud→state-file to un-blind
the monitor, but only WHILE self_publisher was running to do the tee; when it wasn't, the monitor
went blind again — a symptom patch, so it recurred.

The fix DE-FORKS to ONE shared reader (`composeio.self_sensor.context_source.read_context`) that
BOTH consumers read through, whose precedence is the FRESHEST AUTHORITATIVE source (hud-cache vs
statusline state-file, by timestamp), JSONL estimate only when neither exists.

Parametrized over the invariant at each layer, so any future reader that re-forks the precedence —
or that lets the monitor read a stale figure — fails loudly:

  A. context_source.read_context — the FRESHEST authoritative source wins (by ts); jsonl only when
     no authoritative source exists.
  B. cross-implementation — the monitor's reader (build_context_reader) and the publisher's hud
     read resolve to the SAME value from the SAME on-disk hud-cache (no fork, no drift).
  C. SAFETY (Part 2) — a HarnessMonitor wired to build_context_reader FIRES the 95% GUIDANCE when
     the hud-cache reads 96% even though the statusline state file is FROZEN at a stale 60%.
     (FAILS before the fix: the monitor read only the frozen 60% → no 95 crossing.)

Run:  python3 compose_projects/intelligence/launcher/test_context_source_freshness.py --selftest
      pytest compose_projects/intelligence/launcher/test_context_source_freshness.py
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))          # compose_projects/intelligence/launcher
_INTEL = os.path.dirname(_HERE)                             # intelligence
_REPO = os.path.dirname(os.path.dirname(_INTEL))                   # repo root (compose_projects is in between)
_PY = os.path.join(_REPO, "python")
_PUB = os.path.join(_INTEL, "publisher")
for _d in (_HERE, _PY, _PUB):
    if _d not in sys.path:
        sys.path.insert(0, _d)

KIND_GUIDANCE = "context-compact-guidance"


def _write(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


def _hud_snapshot(pct: float, saved_at_ms: int, tokens: int = 500_000,
                  window: int = 1_000_000) -> dict:
    return {"used_percentage": pct, "context_window_size": window,
            "current_usage": {"input_tokens": 0, "output_tokens": 0,
                              "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": tokens},
            "saved_at": saved_at_ms}


def _state_file_obj(pct: float, ts_ms: int, window: int = 1_000_000) -> dict:
    return {"context_level": pct / 100.0, "context_pct": pct,
            "context_tokens": int(pct / 100.0 * window), "context_window": window,
            "source": "statusline", "ts": ts_ms}


def _fresh_hud_env(tmp: str, jsonl: str, hud_pct: float, hud_saved_ms: int):
    """Point CLAUDE_CONFIG_DIR at a temp tree and write a hud-cache snapshot at the EXACT path the
    shared scheme resolves to. Returns the context_source module (imported under the temp env)."""
    os.environ["CLAUDE_CONFIG_DIR"] = tmp
    import composeio.self_sensor.context_source as cs
    importlib.reload(cs)
    hud_path = cs.hud_cache_path(jsonl)
    os.makedirs(os.path.dirname(hud_path), exist_ok=True)
    _write(hud_path, _hud_snapshot(hud_pct, hud_saved_ms))
    return cs


# ── A. read_context returns the FRESHEST authoritative source (by timestamp) ──────────────────
def check_freshest_authoritative_wins(check) -> None:
    tmp = tempfile.mkdtemp()
    jsonl = os.path.join(tmp, "session", "s.jsonl")
    os.makedirs(os.path.dirname(jsonl), exist_ok=True)
    open(jsonl, "w").close()
    state = os.path.join(tmp, "self-architect.json")
    now_ms = int(time.time() * 1000)
    try:
        # hud is NEWER than the state file -> hud (85) wins over a stale state file (60).
        cs = _fresh_hud_env(tmp, jsonl, hud_pct=85, hud_saved_ms=now_ms)
        _write(state, _state_file_obj(60, now_ms - 150 * 60 * 1000))   # 150 min stale
        r = cs.read_context(jsonl_path=jsonl, state_file=state)
        check(r is not None and r["source"] == "hud-cache" and round(r["pct"]) == 85,
              f"(A) a FRESH hud-cache must win over a STALE statusline file: {r}")

        # state file is NEWER than hud -> the statusline file wins (freshest-wins, not fixed order).
        _write(state, _state_file_obj(50, now_ms))
        cs2 = _fresh_hud_env(tmp, jsonl, hud_pct=85, hud_saved_ms=now_ms - 200 * 60 * 1000)
        r2 = cs2.read_context(jsonl_path=jsonl, state_file=state)
        check(r2 is not None and r2["source"] == "statusline-file" and round(r2["pct"]) == 50,
              f"(A) a FRESHER statusline file must win over a STALE hud-cache: {r2}")

        # neither authoritative source present -> the jsonl estimate (last resort), source-labeled.
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(tmp, "empty")  # no hud-cache here
        cs3 = importlib.reload(cs2)
        r3 = cs3.read_context(jsonl_path=jsonl, state_file=os.path.join(tmp, "nope.json"))
        check(r3 is None or r3["source"] == "jsonl-estimate",
              f"(A) with no authoritative source the reading must be the jsonl estimate or None: {r3}")
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


# ── B. cross-implementation: monitor reader == publisher hud read (no fork) ────────────────────
def check_monitor_and_publisher_agree(check) -> None:
    tmp = tempfile.mkdtemp()
    jsonl = os.path.join(tmp, "session", "s.jsonl")
    os.makedirs(os.path.dirname(jsonl), exist_ok=True)
    open(jsonl, "w").close()
    state = os.path.join(tmp, "self-architect.json")
    now_ms = int(time.time() * 1000)
    try:
        cs = _fresh_hud_env(tmp, jsonl, hud_pct=77, hud_saved_ms=now_ms)
        _write(state, _state_file_obj(60, now_ms - 90 * 60 * 1000))   # stale

        import launcher  # the monitor's reader
        importlib.reload(launcher)
        monitor_read = launcher.build_context_reader(state, jsonl)
        m = monitor_read()

        import self_publisher  # the console publisher's hud read (de-forked adapter)
        importlib.reload(self_publisher)
        p = self_publisher._read_hud_cache(jsonl)

        check(m is not None and m["source"] == "hud-cache" and round(m["pct"]) == 77,
              f"(B) the MONITOR reader must resolve the fresh hud-cache (77), not the stale 60: {m}")
        check(p is not None and round(p["pct"]) == 77,
              f"(B) the PUBLISHER hud read must resolve the same hud-cache (77): {p}")
        check(round(m["pct"]) == round(p["pct"]),
              f"(B) monitor and publisher DIVERGED on the same on-disk hud-cache (the fork): "
              f"monitor={m}, publisher={p}")
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


# ── C. SAFETY: the 95% GUIDANCE fires on the TRUE level despite a frozen state file ────────────
def check_guidance_fires_past_stale_statusline(check) -> None:
    tmp = tempfile.mkdtemp()
    jsonl = os.path.join(tmp, "session", "s.jsonl")
    os.makedirs(os.path.dirname(jsonl), exist_ok=True)
    open(jsonl, "w").close()
    state = os.path.join(tmp, "self-architect.json")
    now_ms = int(time.time() * 1000)
    try:
        import launcher
        importlib.reload(launcher)
        from monitor import HarnessMonitor

        published: list[dict] = []

        def publish_in(text, kind, extra=None):
            published.append({"text": text, "kind": kind, "extra": extra or {}})

        # The statusline state file is FROZEN at a stale 60% (150 min old). Prime the monitor there.
        _fresh_hud_env(tmp, jsonl, hud_pct=60, hud_saved_ms=now_ms - 150 * 60 * 1000)  # also stale hud
        _write(state, _state_file_obj(60, now_ms - 150 * 60 * 1000))
        # But wait — for the PRIME we want the monitor to start below 95. Use a genuinely-stale hud
        # AND state so the first reading is ~60. (freshest-wins picks whichever; both are 60 here.)
        mon = HarnessMonitor(
            read_context=launcher.build_context_reader(state, jsonl),
            publish_in=publish_in, thresholds=(65, 80, 90, 95), poll_s=0)
        mon.tick()   # prime at ~60% -> nothing fires
        check(not any(p["kind"] == KIND_GUIDANCE for p in published),
              f"(C) guidance fired on the priming tick at 60%: {published!r}")

        # The TRUE context is now 96% — haven-hud records it FRESH; the statusline file stays FROZEN
        # at 60% (no CLI redraw). Freshest-wins => the monitor reads 96 and MUST fire the guidance.
        _fresh_hud_env(tmp, jsonl, hud_pct=96, hud_saved_ms=now_ms)   # fresh hud at the true level
        # leave `state` frozen at the stale 60%
        mon.tick()
        guid = [p for p in published if p["kind"] == KIND_GUIDANCE]
        check(len(guid) == 1,
              f"(C) the 95% GUIDANCE did NOT fire though the TRUE context is 96% — the monitor read "
              f"the FROZEN statusline file (the safety defect): {published!r}")
        check(guid and "96" in guid[0]["text"] and "compact" in guid[0]["text"].lower(),
              f"(C) guidance fired but not at the true 96%/without the compact prompt: {guid!r}")
    finally:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


_CASES = [
    ("A: read_context freshest-authoritative-wins", check_freshest_authoritative_wins),
    ("B: monitor reader == publisher hud read (no fork)", check_monitor_and_publisher_agree),
    ("C: 95% guidance fires past a frozen statusline file", check_guidance_fires_past_stale_statusline),
]


# ── pytest entry points ────────────────────────────────────────────────────────────────────────
def test_read_context_freshest_authoritative_wins():
    _assert_case(check_freshest_authoritative_wins)


def test_monitor_and_publisher_agree():
    _assert_case(check_monitor_and_publisher_agree)


def test_guidance_fires_past_stale_statusline():
    _assert_case(check_guidance_fires_past_stale_statusline)


def _assert_case(fn):
    fails: list[str] = []
    fn(lambda cond, msg: None if cond else fails.append(msg))
    assert not fails, "\n".join(fails)


# ── selftest entry point (repo idiom) ──────────────────────────────────────────────────────────
def _selftest() -> int:
    fails: list[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    for name, fn in _CASES:
        fn(check)
        sys.stderr.write(f"  ran {name}\n")

    if fails:
        sys.stderr.write(f"\n{len(fails)} FAILURE(S)\n")
        return 1
    print(f"PASS: one shared context source across {len(_CASES)} sites "
          f"(freshest-wins; monitor==publisher; 95% guidance fires past a frozen statusline)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
