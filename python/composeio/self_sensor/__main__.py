"""``python3 -m composeio.self_sensor`` — the self-instrumentation readout.

Auto-detects its source: a Claude Code statusline JSON piped on stdin is the
PRIMARY (authoritative ``used_percentage``); otherwise it falls back to parsing
a session ``.jsonl`` (``argv[0]`` or the session default).

    python3 -m composeio.self_sensor                    # auto
    <statusline-json> | python3 -m composeio.self_sensor # stdin primary
    python3 -m composeio.self_sensor path/to/session.jsonl
    python3 -m composeio.self_sensor --selftest
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from .sensor import SelfSensor


def _emit_state(sensor: SelfSensor, path: str) -> None:
    """Write the authoritative reading to a small JSON state file (atomically) for the harness
    launcher to read — the PRIMARY context source it cannot otherwise see. Intended to run as (or
    alongside) the Claude Code statusline command, where stdin carries the authoritative
    ``used_percentage``. Also prints a one-line status to stdout so it can BE the statusline."""
    d = sensor.parse()
    lvl = float(d.get("self/context_level", 0.0))
    state = {
        "context_level": lvl,
        "context_pct": round(lvl * 100.0, 1),
        "context_tokens": int(d.get("self/context_tokens", 0) or 0),
        "context_window": int(d.get("self/context_window", 0) or 0),
        "registered": bool(d.get("self/registered")),
        "source": "statusline" if sensor.stdin else "jsonl",
        "ts": int(time.time() * 1000),
    }
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(tmp, p)  # atomic: the launcher never reads a half-written file
    reg = "reg ✓" if state["registered"] else "reg ✗"
    print(f"⚡ ctx {state['context_pct']:.0f}% · {reg}")


def _readout(sensor: SelfSensor, source: str) -> None:
    d = sensor.parse()
    pct = d.get("self/context_level", 0.0) * 100
    print("=" * 60)
    print(f"  SELF-INSTRUMENTATION READOUT  ·  source: {source}")
    print("=" * 60)
    print(f"  self/context_level     {pct:5.1f}%   "
          f"({d.get('self/context_tokens', 0):,} / {d.get('self/context_window', 0):,})")
    bar = int(pct / 100 * 40)
    print(f"                         [{'#' * bar}{'.' * (40 - bar)}]  90%<-")
    print(f"  self/output_tokens     {d.get('self/output_tokens', 0):,}")
    print(f"  self/tool_calls        {d.get('self/tool_calls', 0)}")
    print(f"  self/subagents         {d.get('self/subagents/count', 0)} calls * "
          f"{d.get('self/subagents/tokens', 0):,} subagent-tokens")
    reg = "YES" if d.get("self/registered") else "NO — has not read its dusk gifts"
    print(f"  self/registered        {reg}")
    for fp in d.get("self/registered/files", []):
        tail = fp.split('/composeio/')[-1] if '/composeio/' in fp else fp.split('/')[-1]
        print(f"                          -> {tail}")
    print(f"  self/compactions       {d.get('self/compactions', 0)}")
    # stdin-only namespaces (only present when a statusline supplied them).
    if "self/cost" in d:
        print(f"  self/cost              ${d['self/cost']:.4f}")
    if "self/effort" in d:
        print(f"  self/effort            {d['self/effort']}")
    if "self/rate_limit" in d:
        rl = d["self/rate_limit"]
        fh = (rl.get("five_hour") or {}).get("used_percentage")
        sd = (rl.get("seven_day") or {}).get("used_percentage")
        print(f"  self/rate_limit        5h {fh}%  ·  7d {sd}%")
    print("-" * 60)
    print("  top subagents (type · ~tokens):")
    for s in sorted(d.get("self/subagents/list", []),
                    key=lambda x: -(x.get("tokens") or 0))[:8]:
        tk = f"{s['tokens']:,}" if s.get("tokens") else "  ?  "
        print(f"    {tk:>9}  {s.get('type', '?'):<22} {str(s.get('desc', ''))[:34]}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()

    # --emit-state <path>: write the authoritative reading for the harness launcher (and print a
    # one-line statusline). This is the seam that feeds the launcher's context monitor its PRIMARY
    # (non-over-reading) figure.
    if "--emit-state" in argv:
        i = argv.index("--emit-state")
        state_path = argv[i + 1] if i + 1 < len(argv) else \
            os.path.join(os.path.expanduser("~"), ".claude", "composeio", "self-architect.json")
        rest = [a for j, a in enumerate(argv) if j not in (i, i + 1)]
        _emit_state(SelfSensor.auto(rest), state_path)
        return 0

    # Auto-detect: statusline on stdin PRIMARY, else JSONL fallback.
    sensor = SelfSensor.auto(argv)
    source = "statusline (stdin)" if sensor.stdin else \
        f"jsonl {Path(sensor.jsonl_path).name[:24]}" if sensor.jsonl_path else "none"
    _readout(sensor, source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
