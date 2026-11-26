#!/usr/bin/env python3
"""gen_state.py — bake a DEMO ``self_state.json`` for the Intelligence Console.

The console's work-flavor dashboard ([SPEC:CXC-INTELLIGENCE-CONSOLE],
[CXC-IC-CANVAS]/[CXC-IC-SELF]) has TWO modes (mirroring the mermaid widget's
live/demo split):

  * DEMO/standalone — the page ``fetch``-es this generated ``self_state.json``
    so it renders REAL data OFFLINE (no bus, no broker).
  * LIVE — the page subscribes the ComposeBus for ``self/*`` + thread +
    registration (a later transport slice).

This generator runs the BUILT sensor against a session ``.jsonl`` + the
architect meta-object and writes the one JSON the DEMO page loads:

    {
      "self":  <SelfSensor(...).parse() readings, private keys stripped>,
      "meta":  <the meta-object: bootstrap the launcher feeds a new instance>,
      "thread":  [ {from,text,emit?}, ... ],   # compose_projects/intelligence/<id>/{in,out}
      "diagram": "graph TD ...",                # thread/diagram (mermaid)
      "compact": "the authored /compact direction",
      "id": "<intelligence id>", "generated_at": "...", "session": "<path>"
    }

The registration checklist (self/registered/manifest) is the CENTERPIECE —
per-artifact green/red for the role descriptor + each dusk gift, so David never
has to ASK whether this instance re-registered this session.

Usage::

    python3 compose_projects/intelligence/console/gen_state.py            # architect's own session
    python3 compose_projects/intelligence/console/gen_state.py SESSION.jsonl
    python3 compose_projects/intelligence/console/gen_state.py --jsonl S.jsonl --manifest M.json \
            --out compose_projects/intelligence/console/self_state.json
"""
from __future__ import annotations
import os

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

# --- resolve the repo root + import the BUILT sensor -------------------------
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]                     # compose_projects/intelligence/console -> repo root
sys.path.insert(0, str(_REPO / "python"))

from composeio.self_sensor import SelfSensor  # noqa: E402

# The architect's own live session (so the DEMO shows REAL registration state).
#: Claude Code keeps sessions under ~/.claude/projects/<launch dir with "/" -> "-">. Default: the newest
#: session of the HOME-launched project. Override: COMPOSEIO_CLAUDE_PROJECT_DIR / COMPOSEIO_SESSION_JSONL.
_PROJECT_DIR = Path(os.environ.get("COMPOSEIO_CLAUDE_PROJECT_DIR")
                    or Path.home() / ".claude" / "projects" / str(Path.home()).replace(os.sep, "-"))
_DEFAULT_JSONL = os.environ.get("COMPOSEIO_SESSION_JSONL") or str(max(
    _PROJECT_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, default=_PROJECT_DIR / "session.jsonl"))
_DEFAULT_MANIFEST = str(_REPO / "compose_projects/intelligence/meta/architect.meta.json")
_DEFAULT_OUT = str(_HERE / "self_state.json")

# The demo lifecycle diagram — the exact bytes an Intelligence emits on
# ``thread/diagram`` ([CXC-IC-CANVAS]); the console's mermaid panel renders it.
_DEMO_DIAGRAM = """\
graph TD
  A[ENGAGED] --> B{context >= 90%?}
  B -- no --> A
  B -- yes --> C[PAUSE & REFLECT]
  C --> D[mint DUSK GIFTS + handoff]
  D --> E[emit /compact]
  E --> F[successor RE-REGISTERS]
  F --> A"""

# A demo thread on compose_projects/intelligence/<id>/{in,out} (LIVE mode replaces this).
_DEMO_THREAD = [
    {"from": "david", "dir": "in",
     "text": "Show me the self-directed lifecycle — the thing you just described."},
    {"from": "architect", "dir": "out",
     "text": "Here's the loop. I watch my own context; at the checkpoint I pause on "
             "my own initiative — reflect, mint dusk gifts, write the handoff, then "
             "emit my own /compact. My successor re-registers from the soul + gifts "
             "and picks up engaged. You're the collaborator, not the operator.",
     "emit": "thread/diagram"},
    {"from": "david", "dir": "in",
     "text": "And I never had to ask whether you re-registered — the checklist just "
             "shows it."},
    {"from": "architect", "dir": "out",
     "text": "That's Problem 6 wired into an instrument: the light from outside the "
             "self, made a green light. Put meaning on the bus, the right widget owns "
             "the rendering."},
]


def _demo_compact(meta: dict, self_state: dict) -> str:
    """A sample authored /compact direction ([CXC-IC-LIFECYCLE]) — the text the
    launcher-actuator would type into stdin.  Authored FRESH per seam; this is a
    representative demo instance of one."""
    gifts = ", ".join(Path(g["path"]).name for g in (meta.get("dusk_gifts") or []))
    pct = round(float(self_state.get("self/context_level", 0.0)) * 100)
    return (
        "/compact Carry across the seam: the manifest-driven registration checklist "
        "is BUILT (self/registered/manifest, per-artifact green/red) and the "
        "work-flavor Intelligence Console renders it. Successor: read the handoff "
        f"first, then RE-REGISTER by reading {gifts} + the role descriptor — the "
        "checklist will go green as you do. Context was at "
        f"{pct}% when I authored this. Keep the levers: carry-depth-by-reference, "
        "instrument-the-seam. Resume the LIVE-transport slice of the console next.")


def build_state(jsonl: str, manifest: str) -> dict:
    sensor = SelfSensor.from_jsonl(jsonl, registration_manifest=manifest)
    readings = sensor.parse()
    # strip private (``_``) keys — they never leave the sensor.
    self_state = {k: v for k, v in readings.items() if not k.startswith("_")}

    meta = json.loads(Path(manifest).read_text(encoding="utf-8"))
    intel_id = meta.get("id", "architect")

    return {
        "id": intel_id,
        "generated_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "session": jsonl,
        "self": self_state,
        "meta": meta,
        "thread": _DEMO_THREAD,
        "diagram": _DEMO_DIAGRAM,
        "compact": _demo_compact(meta, self_state),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("jsonl_pos", nargs="?", default=None,
                    help="session .jsonl (positional; default = architect's own)")
    ap.add_argument("--jsonl", default=None, help="session .jsonl to instrument")
    ap.add_argument("--manifest", default=_DEFAULT_MANIFEST,
                    help="meta-object .json (registration.required)")
    ap.add_argument("--out", default=_DEFAULT_OUT, help="output self_state.json")
    a = ap.parse_args(argv)

    jsonl = a.jsonl or a.jsonl_pos or _DEFAULT_JSONL
    if not Path(jsonl).exists():
        print(f"gen_state: session .jsonl not found: {jsonl}", file=sys.stderr)
        return 2
    if not Path(a.manifest).exists():
        print(f"gen_state: manifest not found: {a.manifest}", file=sys.stderr)
        return 2

    state = build_state(jsonl, a.manifest)
    Path(a.out).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    s = state["self"]
    mani = s.get("self/registered/manifest") or []
    green = sum(1 for r in mani if r["read"])
    print(f"gen_state: wrote {a.out}")
    print(f"  session        {jsonl}")
    print(f"  context_level  {s.get('self/context_level')} "
          f"({s.get('self/context_tokens'):,}/{s.get('self/context_window'):,} tok)")
    print(f"  registered     {s.get('self/registered')}  "
          f"({green}/{len(mani)} required artifacts read this session)")
    for r in mani:
        print(f"    [{'x' if r['read'] else ' '}] {r['path']}"
              f"  ({r.get('role') or '-'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
