#!/usr/bin/env python3
"""permission_audit.py — deterministic audit of what would (or did) trigger a
permission prompt across Claude Code session transcripts.

WHY DETERMINISTIC (David, 2026-07-21): enumerating permission events is
mechanical grep + categorization over the session `.jsonl`; it must NOT burn a
reasoning model on tens of MB of transcript. This script does the whole
extraction for ZERO model tokens; a human (or a cheap model) then only makes the
allowlist JUDGMENT on the compact output. Same perceive/act split as the
deliverable sweep and the compact-summary extractor.

WHAT THE TRANSCRIPT ACTUALLY RECORDS (the load-bearing subtlety): a permission
prompt the user APPROVED leaves no distinct event — the approved tool call looks
identical to an auto-allowed one. Only DENIALS leave a marker (the canonical
tool_result "The user doesn't want to proceed with this tool use. The tool use
was rejected"). So "every circumstance where an approval was required" is not
directly recoverable; the best deterministic signals are:

  (1) explicit DENIALS — tools the user actively said no to (with the reason);
  (2) the TOOL INVENTORY cross-referenced against the allowlist — every distinct
      tool used, flagged covered/uncovered. An UNCOVERED tool under
      `defaultMode: bypassPermissions` is exactly a class that prompts. This is
      what yields the actionable `permissions.allow` delta.
  (3) `.claude/`-touching Write/Edit — these prompt EVEN under bypass, by design,
      so an allowlist cannot fix them (they motivate a different mechanism).

Usage:
  python3 -m intelligence.tools.permission_audit T1.jsonl [T2.jsonl ...] \
      [--settings .claude/settings.local.json] [--json]
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from collections import Counter, defaultdict

# The canonical Claude Code denial tool_result text (a rejected tool use).
_DENY_RE = re.compile(
    r"user doesn't want to proceed with this tool use|the tool use was rejected",
    re.IGNORECASE)


def _load_allow(settings_path: str) -> list[str]:
    try:
        with open(settings_path) as fh:
            return list(((json.load(fh).get("permissions") or {}).get("allow")) or [])
    except (OSError, ValueError):
        return []


def _rule_covers(rule: str, tool: str, arg: str | None) -> bool:
    """A settings allow rule matches a tool call. 'Read' or 'Read(*)' cover the
    Read tool; 'Bash(git *)' covers a Bash arg by glob; 'mcp__x__y' covers that
    MCP tool exactly."""
    m = re.match(r"^([^(]+)(?:\((.*)\))?$", rule.strip())
    if not m:
        return False
    rtool, rarg = m.group(1), m.group(2)
    if rtool != tool:
        return False
    if rarg in (None, "", "*"):
        return True
    if arg is None:
        return False
    return fnmatch.fnmatch(arg, rarg)


def _covered(tool: str, arg: str | None, allow: list[str]) -> bool:
    return any(_rule_covers(r, tool, arg) for r in allow)


def _tool_arg(name: str, inp: dict) -> str | None:
    """The representative arg an allow rule would match on: Bash->command,
    file tools->path. Others: None (tool-level match only)."""
    if not isinstance(inp, dict):
        return None
    if name == "Bash":
        return inp.get("command")
    return inp.get("file_path") or inp.get("path") or inp.get("notebook_path")


def audit(files: list[str], allow: list[str]) -> dict:
    tool_counts: Counter = Counter()
    uncovered: Counter = Counter()          # tool -> count of calls no rule covers
    uncovered_examples: dict[str, str] = {}
    mcp_tools: Counter = Counter()
    dotclaude_edits: list[dict] = []        # Write/Edit under .claude/ (prompt under bypass)
    denials: list[dict] = []
    pending_tool: dict[str, tuple] = {}     # tool_use_id -> (tool, arg) for denial join

    for path in files:
        base = os.path.basename(path)[:8]
        if not os.path.exists(path):
            print(f"!! missing: {path}", file=sys.stderr)
            continue
        with open(path, errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if '"tool_use"' not in line and not _DENY_RE.search(line):
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                msg = rec.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    # denial tool_result can be a bare-string user message
                    if isinstance(content, str) and _DENY_RE.search(content):
                        denials.append({"file": base, "line": lineno,
                                        "reason": content[:240].replace("\n", " ")})
                    continue
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "tool_use":
                        name = part.get("name", "?")
                        inp = part.get("input") or {}
                        arg = _tool_arg(name, inp)
                        tool_counts[name] += 1
                        pending_tool[part.get("id", "")] = (name, arg, base, lineno)
                        if name.startswith("mcp__"):
                            mcp_tools[name] += 1
                        if not _covered(name, arg, allow):
                            uncovered[name] += 1
                            uncovered_examples.setdefault(
                                name, f"{base}:{lineno} arg={str(arg)[:80]}")
                        if name in ("Write", "Edit", "MultiEdit") and arg \
                                and ".claude/" in str(arg):
                            dotclaude_edits.append(
                                {"file": base, "line": lineno,
                                 "tool": name, "path": str(arg)})
                    elif part.get("type") == "tool_result":
                        txt = part.get("content")
                        blob = txt if isinstance(txt, str) else json.dumps(txt)
                        if _DENY_RE.search(blob or ""):
                            tuid = part.get("tool_use_id", "")
                            t = pending_tool.get(tuid)
                            denials.append({
                                "file": base, "line": lineno,
                                "tool": t[0] if t else "?",
                                "arg": (str(t[1])[:80] if t and t[1] else None),
                                "reason": (blob or "")[:240].replace("\n", " ")})

    return {
        "tool_counts": tool_counts.most_common(),
        "uncovered": [(t, c, uncovered_examples.get(t, ""))
                      for t, c in uncovered.most_common()],
        "mcp_tools": mcp_tools.most_common(),
        "dotclaude_edits": dotclaude_edits,
        "denials": denials,
    }


def _proposed_allow(uncovered) -> list[str]:
    """Turn the uncovered-tool inventory into concrete allow rules. MCP tools by
    exact name; everything else tool-level. `.claude/` edits are intentionally
    NOT proposed (an allowlist can't preempt them)."""
    rules = []
    for tool, _c, _ex in uncovered:
        if tool.startswith("mcp__"):
            rules.append(tool)                      # exact MCP tool
        elif tool in ("Agent", "Task", "Skill", "TodoWrite", "WebFetch",
                      "WebSearch"):
            rules.append(f"{tool}(*)" if tool in ("WebFetch", "WebSearch")
                         else tool)
        else:
            rules.append(tool)
    return sorted(set(rules))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", help="session .jsonl transcript(s)")
    ap.add_argument("--settings", default=".claude/settings.local.json")
    ap.add_argument("--json", action="store_true", help="emit raw JSON")
    a = ap.parse_args(argv)

    allow = _load_allow(a.settings)
    r = audit(a.files, allow)
    r["proposed_allow_delta"] = _proposed_allow(r["uncovered"])

    if a.json:
        print(json.dumps(r, indent=2))
        return 0

    print(f"# Permission audit — {len(a.files)} transcript(s)")
    print(f"# allowlist in {a.settings}: {len(allow)} rules\n")
    print(f"## DENIALS (user actively said no) — {len(r['denials'])}")
    for d in r["denials"]:
        print(f"  {d['file']}:{d['line']} tool={d.get('tool')} "
              f"arg={d.get('arg')}\n     {d['reason']}")
    print(f"\n## UNCOVERED tools (would prompt under bypass) — {len(r['uncovered'])}")
    for t, c, ex in r["uncovered"]:
        print(f"  {c:>4}x  {t:<40} e.g. {ex}")
    print(f"\n## MCP tool usage — {len(r['mcp_tools'])} distinct")
    for t, c in r["mcp_tools"]:
        print(f"  {c:>4}x  {t}")
    print(f"\n## .claude/ edits (prompt EVEN under bypass — allowlist can't fix) "
          f"— {len(r['dotclaude_edits'])}")
    seen = set()
    for e in r["dotclaude_edits"]:
        if e["path"] in seen:
            continue
        seen.add(e["path"])
        print(f"  {e['tool']:<10} {e['path']}")
    print(f"\n## PROPOSED permissions.allow DELTA ({len(r['proposed_allow_delta'])})")
    for rule in r["proposed_allow_delta"]:
        print(f'  "{rule}",')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
