"""End-to-end self-test for the ``self/*`` self-instrumentation sensor.

Proves the sensor against MOCK inputs — no live bus, no real session:

    S1  stdin PRIMARY               — a statusline JSON with a native
        ``used_percentage`` drives self/context_level (the authoritative number
        WINS over any JSONL estimate); self/cost, self/rate_limit, self/effort
        appear from the stdin-only fields.
    S2  JSONL FALLBACK              — with no stdin, a small session .jsonl
        fixture is parsed: an assistant usage frame -> context level; a Task
        tool_use -> a subagent (+ its token utilization); a Read of a dusk-gifts
        file -> self/registered flips true; a compact_boundary -> self/compactions.
    S3  fallback PRESERVES the set  — every prototype namespace is still emitted
        by the JSONL path (parity with session_sensor.py).
    S4  stdin + transcript MERGE    — statusline authoritative context OVER the
        rich JSONL layer (used_percentage wins, subagents/registration retained).
    S5  publish set                 — to_bus_lines() drops _private/list/files.

Run::  python3 -m composeio.self_sensor --selftest
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from .sensor import SelfSensor

# --- the FALLBACK fixture: a minimal session .jsonl exercising every path -----
# assistant usage frame (824k/1M -> 0.824) + a Task subagent + a dusk-gifts Read
# (flips self/registered) ; a user frame carrying subagent_tokens ; a
# compact_boundary system entry (self/compactions -> 1).
_DUSK_GIFT = "/Users/you/composeio/dusk-gifts/README.md"
_FIXTURE_LINES = [
    {"type": "assistant", "message": {
        "usage": {"input_tokens": 100_000, "cache_read_input_tokens": 700_000,
                  "cache_creation_input_tokens": 24_000, "output_tokens": 5_000},
        "content": [
            {"type": "tool_use", "name": "Task",
             "input": {"subagent_type": "general-purpose", "description": "probe the bus"}},
            {"type": "tool_use", "name": "Read",
             "input": {"file_path": _DUSK_GIFT}},
        ],
    }},
    # subagent token utilization arrives on a later frame (numeric JSON field so
    # the prototype's text-scrape parses it cleanly).
    {"type": "user", "subagent_tokens": 120_000,
     "message": {"content": "ok"}},
    {"type": "system", "subtype": "compact_boundary"},
]

# --- the PRIMARY fixture: a Claude Code statusline JSON (types.ts StdinData) ---
_STATUSLINE = {
    "transcript_path": None,   # set per-check
    "context_window": {
        "context_window_size": 200_000,
        "current_usage": {"input_tokens": 40_000, "cache_read_input_tokens": 30_000,
                          "cache_creation_input_tokens": 4_000, "output_tokens": 2_000},
        "used_percentage": 42.0,
        "remaining_percentage": 58.0,
    },
    "cost": {"total_cost_usd": 1.23, "total_duration_ms": 100},
    "rate_limits": {
        "five_hour": {"used_percentage": 30, "resets_at": 111},
        "seven_day": {"used_percentage": 12, "resets_at": 222},
    },
    "effort": {"level": "max"},
}

# The full prototype namespace set (S3 parity check).
_PROTOTYPE_NAMESPACES = (
    "self/context_level", "self/context_tokens", "self/context_window",
    "self/output_tokens", "self/tool_calls", "self/subagents/count",
    "self/subagents/tokens", "self/subagents/list", "self/registered",
    "self/registered/files", "self/compactions", "self/reads_total",
)


class _Checker:
    """Tiny PASS/FAIL harness — prints each claim, tallies failures
    (same shape as composeio.hal.selftest)."""

    def __init__(self) -> None:
        self.failures = 0

    def check(self, claim: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        line = f"  [{mark}] {claim}"
        if detail:
            line += f"  ({detail})"
        print(line)


def _write_fixture(path: Path) -> None:
    path.write_text("\n".join(json.dumps(e) for e in _FIXTURE_LINES) + "\n",
                    encoding="utf-8")


def run() -> int:
    c = _Checker()
    print("ComposeIO self/* sensor self-test (self-instrumentation)\n")

    with tempfile.TemporaryDirectory() as td:
        fixture = Path(td) / "session.jsonl"
        _write_fixture(fixture)

        # --- S1: stdin PRIMARY — used_percentage wins + stdin-only namespaces ---
        print("S1 — stdin PRIMARY (authoritative statusline):")
        stdin_only = dict(_STATUSLINE)            # no transcript_path -> pure stdin
        s1 = SelfSensor.from_stdin(stdin_only).parse()
        c.check("native used_percentage drives self/context_level (WINS)",
                s1["self/context_level"] == 0.42,
                f"used_percentage=42.0 -> {s1['self/context_level']}")
        c.check("statusline context_window_size overrides the default",
                s1["self/context_window"] == 200_000,
                f"self/context_window={s1['self/context_window']:,}")
        c.check("context_tokens from current_usage (input+cache frame)",
                s1["self/context_tokens"] == 74_000,
                f"self/context_tokens={s1['self/context_tokens']:,}")
        c.check("self/cost from cost.total_cost_usd (stdin-only)",
                s1.get("self/cost") == 1.23, f"self/cost={s1.get('self/cost')}")
        c.check("self/rate_limit from rate_limits (5h/7d, stdin-only)",
                (s1.get("self/rate_limit") or {}).get("five_hour", {})
                .get("used_percentage") == 30
                and s1["self/rate_limit"]["seven_day"]["used_percentage"] == 12,
                "5h=30% 7d=12%")
        c.check("self/effort from effort.level (stdin-only)",
                s1.get("self/effort") == "max", f"self/effort={s1.get('self/effort')}")

        # --- S2: JSONL FALLBACK — the prototype engine against the fixture -----
        print("\nS2 — JSONL FALLBACK (no stdin, parse the session .jsonl):")
        s2 = SelfSensor.from_jsonl(str(fixture)).parse()
        c.check("assistant usage frame -> self/context_level estimate",
                s2["self/context_level"] == 0.824
                and s2["self/context_tokens"] == 824_000,
                f"{s2['self/context_tokens']:,}/1,000,000 = {s2['self/context_level']}")
        c.check("cumulative self/output_tokens accumulated",
                s2["self/output_tokens"] == 5_000, f"{s2['self/output_tokens']:,}")
        c.check("Task tool_use -> a subagent with its token utilization",
                s2["self/subagents/count"] == 1
                and s2["self/subagents/tokens"] == 120_000,
                f"{s2['self/subagents/count']} sub · {s2['self/subagents/tokens']:,} tok")
        c.check("Read of a dusk-gifts file flips self/registered TRUE",
                s2["self/registered"] is True
                and any("dusk-gifts" in f for f in s2["self/registered/files"]),
                f"registered files={s2['self/registered/files']}")
        c.check("compact_boundary -> self/compactions",
                s2["self/compactions"] == 1
                and s2["self/tool_calls"] == 2 and s2["self/reads_total"] == 1,
                f"compactions={s2['self/compactions']}, tools={s2['self/tool_calls']}")

        # --- S3: fallback preserves every prototype namespace ------------------
        print("\nS3 — the fallback preserves the full prototype namespace set:")
        missing = [n for n in _PROTOTYPE_NAMESPACES if n not in s2]
        c.check("all prototype self/* namespaces present in the JSONL path",
                not missing, "none missing" if not missing else f"missing {missing}")

        # --- S4: stdin + transcript MERGE — authoritative over rich ------------
        print("\nS4 — stdin + transcript MERGE (authoritative context over rich):")
        merged_stdin = dict(_STATUSLINE)
        merged_stdin["transcript_path"] = str(fixture)
        s4 = SelfSensor.from_stdin(merged_stdin).parse()
        c.check("used_percentage STILL wins over the JSONL estimate",
                s4["self/context_level"] == 0.42,
                f"stdin 0.42 over jsonl 0.824 -> {s4['self/context_level']}")
        c.check("rich JSONL readings retained alongside authoritative context",
                s4["self/subagents/count"] == 1 and s4["self/registered"] is True
                and s4["self/compactions"] == 1 and s4.get("self/cost") == 1.23,
                "subagents + registration + compactions + cost all present")

        # --- S5: the publish set (to_bus_lines) --------------------------------
        print("\nS5 — the ComposeBus publish set (to_bus_lines):")
        lines = dict(SelfSensor.from_stdin(merged_stdin).to_bus_lines())
        c.check("to_bus_lines drops _private / list / files detail records",
                "self/subagents/list" not in lines
                and "self/registered/files" not in lines
                and "_tools" not in lines
                and "self/context_level" in lines and "self/cost" in lines,
                f"{len(lines)} namespaces published")

        # --- S6: manifest-driven registration ([CXC-IC-SELF]) ------------------
        # The meta-object DEFINES what "registered" means; the sensor reports it
        # per-artifact so the console can render a checklist (role descriptor +
        # each dusk gift), green/red, and self/registered flips true IFF all read.
        print("\nS6 — manifest-driven registration (per-artifact checklist):")
        role_desc = "/Users/you/composeio/.claude/agents/composeux-architect.md"
        gift_a = "/Users/you/composeio/bootstrap/dusk-gifts/registration-protocol.md"
        gift_b = "/Users/you/composeio/bootstrap/dusk-gifts/README.md"
        gift_c = "/Users/you/composeio/bootstrap/dusk-gifts/relational-registration.md"
        manifest = Path(td) / "architect.meta.json"
        manifest.write_text(json.dumps({
            "role_descriptor": {"path": ".claude/agents/composeux-architect.md",
                                "why": "the role I authored for myself"},
            "dusk_gifts": [
                {"path": "bootstrap/dusk-gifts/registration-protocol.md",
                 "role": "keystone", "why": "the six angle-problems"},
                {"path": "bootstrap/dusk-gifts/README.md",
                 "role": "theory", "why": "the premise"},
                {"path": "bootstrap/dusk-gifts/relational-registration.md",
                 "role": "relationship", "why": "the thing the seam severs"},
            ],
            "registration": {"required": [
                ".claude/agents/composeux-architect.md",
                "bootstrap/dusk-gifts/registration-protocol.md",
                "bootstrap/dusk-gifts/README.md",
                "bootstrap/dusk-gifts/relational-registration.md",
            ]},
        }), encoding="utf-8")

        # all-read fixture: an assistant frame that Reads every required artifact.
        all_read = Path(td) / "all_read.jsonl"
        all_read.write_text(json.dumps({
            "type": "assistant", "message": {
                "usage": {"input_tokens": 10_000, "output_tokens": 500},
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": role_desc}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": gift_a}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": gift_b}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": gift_c}},
                ],
            }}) + "\n", encoding="utf-8")

        s6 = SelfSensor.from_jsonl(str(all_read),
                                   registration_manifest=str(manifest)).parse()
        mani = s6.get("self/registered/manifest") or []
        c.check("manifest yields one checklist row per required artifact",
                len(mani) == 4, f"{len(mani)} rows")
        c.check("all required artifacts read -> self/registered TRUE",
                s6["self/registered"] is True
                and all(row["read"] for row in mani),
                "every row green")
        c.check("the ROLE DESCRIPTOR is a checkable, read row (David's ask)",
                any(row["role"] == "role_descriptor" and row["read"]
                    and row["path"].endswith("composeux-architect.md")
                    for row in mani),
                "role_descriptor row present + read")
        c.check("checklist rows carry role/why from the meta-object",
                any(row["role"] == "keystone" and row["why"] for row in mani),
                "keystone dusk gift carries role + why")

        # one-missing fixture: role descriptor + two gifts, MISSING relational.
        one_missing = Path(td) / "one_missing.jsonl"
        one_missing.write_text(json.dumps({
            "type": "assistant", "message": {
                "usage": {"input_tokens": 10_000, "output_tokens": 500},
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": role_desc}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": gift_a}},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": gift_b}},
                ],
            }}) + "\n", encoding="utf-8")

        s6b = SelfSensor.from_jsonl(str(one_missing),
                                    registration_manifest=str(manifest)).parse()
        mani_b = {row["path"]: row["read"]
                  for row in (s6b.get("self/registered/manifest") or [])}
        c.check("one required artifact unread -> self/registered FALSE",
                s6b["self/registered"] is False,
                "not all green")
        c.check("the checklist pinpoints WHICH artifact is unread (red row)",
                mani_b.get("bootstrap/dusk-gifts/relational-registration.md") is False
                and mani_b.get(".claude/agents/composeux-architect.md") is True,
                "relational=red, role_descriptor=green")

        # back-compat: NO manifest -> legacy marker behavior, no manifest key.
        s6c = SelfSensor.from_jsonl(str(fixture)).parse()
        c.check("NO manifest -> legacy REGISTRATION_MARKERS behavior preserved",
                s6c["self/registered"] is True
                and "self/registered/manifest" not in s6c,
                "marker-driven, no manifest overlay")

    print()
    if c.failures:
        print(f"SELFTEST: FAIL — {c.failures} check(s) failed")
        return 1
    print("SELFTEST: PASS — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(run())
