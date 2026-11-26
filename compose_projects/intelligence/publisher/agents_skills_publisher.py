#!/usr/bin/env python3
"""agents_skills_publisher — project the session's REGISTERED agents + skills onto
the bus as `self/<id>/agents` and `self/<id>/skills`, so the console's
Configuration tab (master/detail) can render "what's LOADED with this session"
([CXC-IC-CONFIG-LOADED]).  Same pattern as `meta_publisher.py` /
`self_publisher.py`: state on the bus, behaviour in the widget.

WHAT IT READS (the registered file set — the pragmatic "loaded" source):
  * `.claude/agents/*.md`        — each file's front-matter `name` + `description`
  * `.claude/skills/*/SKILL.md`  — each skill's front-matter `name` + `description`
    (plus any loose `.claude/skills/*.md`)

WHAT "loaded" MEANS (honest scope, [CXC-IC-CONFIG-LOADED]): `loaded:true` here is
the registered-file-set APPROXIMATION of "loaded THIS session" — the definition is
AVAILABLE on disk, not proven-invoked.  The authoritative read of what was actually
consumed this session is `self/registered` ([CXC-IC-SELF]).  A truer per-session
`loaded` flag is DEFERRED to the launcher (which knows the actually-registered set
at bootstrap); until then this deterministic file-set projection is the surface.

WIRE CONTRACT: each namespace carries a JSON ARRAY of `{name, description, loaded}`,
published on the GLOBAL leg (`composeio/global/self/<id>/{agents,skills}`).

Run:   python3 agents_skills_publisher.py --id architect \
                                          --mqtt mqtt://mqtt-broker.local:1883
       python3 agents_skills_publisher.py --once      # print both arrays, no bus
       python3 agents_skills_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

import yaml

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "agents_skills"              # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])

# This file is compose_projects/intelligence/publisher/<me>.py, so repo root = .../composeio
# is THREE dirnames above _THIS. Two stops at compose_projects/.
_THIS = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS)))
_DEFAULT_CLAUDE_DIR = os.path.join(_REPO_ROOT, ".claude")

# The FULL front-matter description is carried ([CXC-IC-CONFIG-LOADED]): the
# nested detail sub-pane shows it in full (wrapped); the list rows ellipsize it.
# A generous bound keeps the payload sane without truncating a real paragraph.
_MAX_DESC = 4000


def _frontmatter(path: str) -> Dict[str, Any]:
    """Parse a markdown file's YAML front-matter block (between the first two
    `---` fences). Returns {} on any trouble — a bad file never kills the read."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _one_line(desc: Any) -> str:
    """Collapse a (possibly folded/multiline) description to one clean line."""
    s = " ".join(str(desc or "").split())
    return s[:_MAX_DESC].rstrip()


def _rel(path: str, claude_dir: str) -> str:
    """Repo-relative path (e.g. `.claude/agents/x.md`) for the detail widget to
    load the body — the parent of `claude_dir` is the repo root the console runs
    from ([CXC-IC-CONFIG-LOADED], file-load-for-body)."""
    return os.path.relpath(path, os.path.dirname(claude_dir.rstrip("/")))


def _name_from(fm: Dict[str, Any], fallback_path: str) -> str:
    name = fm.get("name")
    if name:
        return str(name).strip()
    # fall back to the file/dir stem
    base = os.path.basename(fallback_path)
    if base.lower() in ("skill.md",):
        return os.path.basename(os.path.dirname(fallback_path))
    return os.path.splitext(base)[0]


def read_agents(claude_dir: str) -> List[Dict[str, Any]]:
    """The `.claude/agents/*.md` set as [{name, description, loaded}], name-sorted."""
    out: List[Dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(claude_dir, "agents", "*.md"))):
        fm = _frontmatter(path)
        out.append({"name": _name_from(fm, path),
                    "description": _one_line(fm.get("description")),
                    "path": _rel(path, claude_dir),
                    "loaded": True})
    return sorted(out, key=lambda d: d["name"].lower())


def read_skills(claude_dir: str) -> List[Dict[str, Any]]:
    """The `.claude/skills/*/SKILL.md` (+ loose `.claude/skills/*.md`) set."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for path in sorted(glob.glob(os.path.join(claude_dir, "skills", "*",
                                             "SKILL.md"))):
        fm = _frontmatter(path)
        nm = _name_from(fm, path)
        seen.add(nm.lower())
        out.append({"name": nm,
                    "description": _one_line(fm.get("description")),
                    "path": _rel(path, claude_dir),
                    "loaded": True})
    for path in sorted(glob.glob(os.path.join(claude_dir, "skills", "*.md"))):
        fm = _frontmatter(path)
        nm = _name_from(fm, path)
        if nm.lower() in seen:
            continue
        out.append({"name": nm,
                    "description": _one_line(fm.get("description")),
                    "path": _rel(path, claude_dir),
                    "loaded": True})
    return sorted(out, key=lambda d: d["name"].lower())


def project(claude_dir: str) -> Dict[str, List[Dict[str, Any]]]:
    """Both arrays, deterministic (name-sorted). The single data contract."""
    return {"agents": read_agents(claude_dir), "skills": read_skills(claude_dir)}


def _parse_mqtt(url: str):
    u = url.replace("mqtt://", "").replace("tcp://", "")
    host, _, port = u.partition(":")
    return host or "localhost", int(port or "1883")


def run(claude_dir: str, mqtt_url: str, poll_s: float, ident: str,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 3 — publish-only, session-independent.

    TWO namespaces from one source; BOTH are declared before the first publish
    ([CXC-SOB-DECLARE]) and BOTH appear on the one startup manifest line, so
    SOB-C3's "no namespace silently goes dark" holds for the pair rather than
    just for whichever one happened to be published first.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe; an unchanged
    `.claude/` file set republishes identical arrays, deduped in-process and
    not on the wire.
    """
    # SINGLETON ([CXC-IC-SINGLETON]) FIRST, before the bus handle.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    label = f"agents-skills-publisher-{ident}"
    lock = acquire_singleton(label)
    if lock is None:
        sys.stderr.write("[agents_skills_publisher] another instance holds the "
                         "singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    agents_ns = f"self/{ident}/agents"
    skills_ns = f"self/{ident}/skills"
    b = source_bus.acquire_bus(mqtt_url, label=label, bus=bus)
    source_bus.declare(b, agents_ns, scope=Scope.GLOBAL, source=SRC,
                       label="registered agents")
    source_bus.declare(b, skills_ns, scope=Scope.GLOBAL, source=SRC,
                       label="registered skills")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[agents_skills_publisher] {claude_dir} -> {agents_ns} + "
                     f"{skills_ns} every {poll_s}s\n")

    def _tick() -> None:
        try:
            proj = project(claude_dir)
            for ns, arr in ((agents_ns, proj["agents"]),
                            (skills_ns, proj["skills"])):
                b.publish_state(ns, arr, source=SRC)
        except Exception as exc:   # a bad read must never kill the daemon
            sys.stderr.write(f"[agents_skills_publisher] read error "
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

    with tempfile.TemporaryDirectory() as _root:
        # root the fixture under a `.claude` dir so `_rel` yields repo-relative
        # `.claude/...` paths exactly as the real launch does.
        d = os.path.join(_root, ".claude")
        os.makedirs(os.path.join(d, "agents"))
        os.makedirs(os.path.join(d, "skills", "console-up"))
        os.makedirs(os.path.join(d, "skills", "handover"))
        with open(os.path.join(d, "agents", "zeta-expert.md"), "w") as f:
            f.write("---\nname: zeta-expert\ndescription: >\n  Line one of the\n"
                    "  folded description.\ntools: Read\n---\n# body\n")
        with open(os.path.join(d, "agents", "alpha-architect.md"), "w") as f:
            f.write("---\nname: alpha-architect\ndescription: One-liner.\n---\nx\n")
        # a malformed file must not crash the read (graceful skip of fm)
        with open(os.path.join(d, "agents", "broken.md"), "w") as f:
            f.write("no front matter here\n")
        with open(os.path.join(d, "skills", "console-up", "SKILL.md"), "w") as f:
            f.write("---\nname: console-up\ndescription: Bring up the console.\n"
                    "---\nbody\n")
        with open(os.path.join(d, "skills", "handover", "SKILL.md"), "w") as f:
            f.write("---\nname: handover\ndescription: The crossing ritual.\n---\n")

        agents = read_agents(d)
        skills = read_skills(d)

        check(len(agents) == 3, f"(1) three agents read, got {len(agents)}")
        check([a["name"] for a in agents]
              == ["alpha-architect", "broken", "zeta-expert"],
              "(2) agents name-sorted incl. the fm-less file by stem")
        check(agents[2]["description"] == "Line one of the folded description.",
              "(3) folded description collapsed to one line")
        check(all(a["loaded"] is True for a in agents), "(4) loaded=true")
        check([s["name"] for s in skills] == ["console-up", "handover"],
              "(5) skills read from */SKILL.md, name-sorted")
        check(skills[0]["description"] == "Bring up the console.",
              "(6) skill description read")
        # (path) repo-relative, points at the real file, for file-load-for-body
        check(agents[2]["path"] == ".claude/agents/zeta-expert.md",
              f"(6b) agent path repo-relative, got {agents[2].get('path')}")
        check(skills[1]["path"] == ".claude/skills/handover/SKILL.md",
              f"(6c) skill path repo-relative, got {skills[1].get('path')}")
        check(set(agents[0]) == {"name", "description", "path", "loaded"},
              f"(6d) item shape carries path, got {set(agents[0])}")

        proj = project(d)
        check(set(proj) == {"agents", "skills"}, "(7) project() shape")
        # JSON round-trip (the wire form) + determinism
        check(json.loads(json.dumps(proj)) == proj, "(8) JSON-round-trippable")
        check(project(d) == project(d), "(9) deterministic")
        # the REAL repo set is readable (non-empty agents) + real paths exist
        real = project(_DEFAULT_CLAUDE_DIR)
        check(len(real["agents"]) >= 1, "(10) real .claude/agents readable")
        check(all(os.path.isfile(os.path.join(_REPO_ROOT, a["path"]))
                  for a in real["agents"]),
              "(10b) real agent paths resolve from the repo root")

    if fails:
        for m in fails:
            sys.stderr.write(f"FAIL {m}\n")
        return 1
    print("PASS: agents_skills_publisher selftest (13/13)")
    return 0


def main(argv: Optional[List[str]] = None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Project the registered agents+skills to the bus as "
                    "self/<id>/{agents,skills}.")
    ap.add_argument("--claude-dir", default=_DEFAULT_CLAUDE_DIR)
    ap.add_argument("--id", default="architect")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--poll", type=float, default=30.0)
    ap.add_argument("--once", action="store_true",
                    help="print both arrays and exit (no bus)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.once:
        print(json.dumps(project(a.claude_dir), indent=2))
        return 0
    return run(a.claude_dir, a.mqtt, a.poll, a.id, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
