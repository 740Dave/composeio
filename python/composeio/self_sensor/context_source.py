#!/usr/bin/env python3
"""composeio.self_sensor.context_source — the ONE authoritative context-source reader.

This is the NARROW WAIST ([DX-NARROW-WAIST], reuse-by-reference-never-fork) that BOTH the
harness monitor (``compose_projects/intelligence/launcher/monitor.py`` via ``build_context_reader``) AND the
console publisher (``compose_projects/intelligence/publisher/self_publisher.py``) read the Intelligence's context
level through. Before this module the two readers were FORKED with DIFFERENT source priorities —
the console read nexus-hud's fresh context-cache while the monitor read ONLY the staleness-prone
statusline state file — so the console gauge and the 65/80/90/95 threshold crossings could (and
did, 2026-07-26) perceive DIFFERENT levels. The console displayed a lagging figure AND, worse,
the monitor's crossing check ran against a FROZEN state file, so the 95% pencils-down guidance
could fire late (or never) while the true context sailed past. See [CXC-IC-MONITOR].

SOURCE PRECEDENCE — the FRESHEST AUTHORITATIVE source wins (by timestamp), never a fixed order,
because "less than 100% accurate 100% of the time" is not acceptable for the fuel gauge:

  * hud-cache  — nexus-hud's per-session context-cache snapshot. nexus-hud reads Claude Code's
                 native ``used_percentage`` every ~300ms and PERSISTS it (throttled) to disk,
                 INDEPENDENT of whether the CLI redraws. AUTHORITATIVE and typically the freshest.
                 ``source="hud-cache"``.
  * statusline-file — the ``self_sensor --emit-state`` state file, written by the statusline
                 command. AUTHORITATIVE when fresh, but a statusline only re-runs when the CLI
                 REDRAWS it, so between redraws (an idle prompt, a long tool run) the file FREEZES.
                 ``source="statusline-file"``.
  * jsonl-estimate — the session ``.jsonl`` parsed directly. An OVER-READING estimate that also
                 goes stale across a ``/compact`` (reads the last pre-summary frame). Last resort,
                 used ONLY when NO authoritative source is available. ``source="jsonl-estimate"``.

``read_context`` compares the two authoritative sources by their embedded timestamp and returns
the NEWER one, so a stale hud-cache (nexus-hud not running) never wins over a fresh statusline
file and vice-versa. The JSONL estimate is used only when neither authoritative source exists.

Every reading is normalized to::

    {"pct": float 0-100, "level": float 0-1, "tokens": int, "window": int,
     "source": "hud-cache"|"statusline-file"|"jsonl-estimate", "ts_ms": int|None, "age_s": float|None}

Defensive by design (a bad/absent/partial source degrades to None, never raises) so a single
missing file can never blind or crash either consumer.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional


# ── hud-cache (nexus-hud's per-session context snapshot — the freshest authoritative figure) ──
def _claude_config_dir() -> str:
    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if cfg:
        return os.path.abspath(os.path.expanduser(cfg))
    return os.path.join(os.path.expanduser("~"), ".claude")


def hud_cache_path(jsonl_path: str) -> str:
    """The nexus-hud context-cache file for this session: keyed by sha256(abspath(transcript))
    (matching Node's ``path.resolve``), under <claude-config>/plugins/nexus-hud/context-cache/.
    Single-sourced here so the monitor and the publisher can NEVER fork the path scheme."""
    h = hashlib.sha256(os.path.abspath(jsonl_path).encode("utf-8")).hexdigest()
    return os.path.join(_claude_config_dir(), "plugins", "nexus-hud", "context-cache", f"{h}.json")


def read_hud_cache(jsonl_path: Optional[str]) -> Optional[dict]:
    """The AUTHORITATIVE reading from nexus-hud's snapshot, or None when absent/malformed. Every
    field is guarded (this couples to hud's private cache format) so a bad read degrades to None."""
    if not jsonl_path:
        return None
    try:
        with open(hud_cache_path(jsonl_path), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    up = data.get("used_percentage")
    if not isinstance(up, (int, float)) or isinstance(up, bool):
        return None
    usage = data.get("current_usage") or {}
    tokens = 0
    if isinstance(usage, dict):
        for k in ("input_tokens", "output_tokens",
                  "cache_creation_input_tokens", "cache_read_input_tokens"):
            try:
                tokens += int(usage.get(k, 0) or 0)
            except (TypeError, ValueError):
                pass
    try:
        window = int(data.get("context_window_size") or 0)
    except (TypeError, ValueError):
        window = 0
    pct = float(up)
    saved_at = data.get("saved_at")
    ts_ms = int(saved_at) if isinstance(saved_at, (int, float)) and not isinstance(saved_at, bool) else None
    age_s = round(max(0.0, time.time() - ts_ms / 1000.0), 1) if ts_ms is not None else None
    return {"pct": round(pct, 1), "level": round(pct / 100.0, 4), "tokens": tokens,
            "window": window, "source": "hud-cache", "ts_ms": ts_ms, "age_s": age_s}


# ── statusline state file (self_sensor --emit-state; authoritative but redraw-gated) ──
def read_state_file(state_file: Optional[str]) -> Optional[dict]:
    """The reading from the statusline-written state file, or None. Accepts ``context_level`` as a
    fraction (0-1) or a percentage. Carries the file's ``ts`` (ms) so ``read_context`` can compare
    freshness against the hud-cache. (Historically the monitor labeled this ``statusline-file`` and
    did not check the file's own ``source`` field — preserved: any emit-state writer is trusted.)"""
    if not state_file:
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    lvl = data.get("context_level", data.get("level"))
    if lvl is None:
        return None
    lvl = float(lvl)
    pct = lvl * 100.0 if lvl <= 1.0 else lvl
    window = int(data.get("context_window", data.get("window", 0)) or 0)
    tokens = int(data.get("context_tokens", data.get("tokens", 0)) or 0)
    ts = data.get("ts")
    ts_ms = int(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
    age_s = round(max(0.0, time.time() - ts_ms / 1000.0), 1) if ts_ms is not None else None
    return {"pct": pct, "level": pct / 100.0, "tokens": tokens, "window": window,
            "source": "statusline-file", "ts_ms": ts_ms, "age_s": age_s}


# ── jsonl estimate (over-reading; last resort) ──
def read_jsonl_estimate(jsonl_path: Optional[str]) -> Optional[dict]:
    """Fallback: parse the session .jsonl via the SelfSensor engine. An OVER-READING estimate,
    returned only when no authoritative source exists. None when the file/engine is unavailable."""
    if not jsonl_path or not os.path.exists(jsonl_path):
        return None
    try:
        from composeio.self_sensor.sensor import SelfSensor  # lazy: only when the fallback is hit
    except Exception:
        return None
    try:
        d = SelfSensor.from_jsonl(jsonl_path).parse()
    except Exception:
        return None
    pct = float(d.get("self/context_level", 0.0) or 0.0) * 100.0
    return {"pct": pct, "level": pct / 100.0,
            "tokens": int(d.get("self/context_tokens", 0) or 0),
            "window": int(d.get("self/context_window", 0) or 0),
            "source": "jsonl-estimate", "ts_ms": None, "age_s": None}


def _ts_or_neg(reading: dict) -> float:
    """Sort key: a reading WITH a timestamp always outranks one without; among timestamped
    readings the NEWER (larger ts) wins. A source-less reading gets -inf so a timestamped
    authoritative source always beats an untimestamped one."""
    ts = reading.get("ts_ms")
    return float(ts) if ts is not None else float("-inf")


def read_context(jsonl_path: Optional[str] = None,
                 state_file: Optional[str] = None) -> Optional[dict]:
    """The single context reading BOTH the monitor and the publisher read through. Returns the
    FRESHEST AUTHORITATIVE source (hud-cache vs statusline state-file, by embedded timestamp),
    else the JSONL over-reading estimate, else None. The freshest-wins rule is what un-freezes the
    monitor: when the statusline state file has gone stale between CLI redraws, the fresh hud-cache
    wins, so the 65/80/90/95 crossings fire on the TRUE level ([CXC-IC-MONITOR])."""
    authoritative = [r for r in (read_hud_cache(jsonl_path), read_state_file(state_file))
                     if r is not None]
    if authoritative:
        return max(authoritative, key=_ts_or_neg)
    return read_jsonl_estimate(jsonl_path)
