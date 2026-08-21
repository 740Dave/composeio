"""Headless render proof for the Configuration master/detail ([CXC-IC-CONFIG-
MASTERDETAIL] / [CXC-IC-CONFIG-LOADED]): drive the real console scene to the
Configuration tab, switch _app/config/section (and the nested _app/config/agent
/ _app/config/skill), and capture each right-pane with the REAL loaded set."""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
sys.path.insert(0, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher"))

from composeio.render.engine.runtime import RenderApp
import agents_skills_publisher as pub

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")
CAP = os.path.join(HERE, "captures")
_PROJ = pub.project(os.path.join(REPO_ROOT, ".claude"))   # the REAL full set


def _shot(section: str, tag: str, *, agent=None, skill=None) -> None:
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    # inject the REAL projection (full descriptions) so the detail pane is real
    app.publish("self/architect/agents", _PROJ["agents"])
    app.publish("self/architect/skills", _PROJ["skills"])
    app.publish("_app/console/tab", "configuration")
    app.publish("_app/config/section", section)
    if agent is not None:
        app.publish("_app/config/agent", agent)
    if skill is not None:
        app.publish("_app/config/skill", skill)
    os.makedirs(CAP, exist_ok=True)
    out = os.path.join(CAP, f"config_md_{tag}.png")
    app.render_headless(out)
    print(f"  {section:20s} -> {out} ({os.path.getsize(out)} bytes)")
    assert app.bus.get_state("_app/console/tab") == "configuration"
    assert app.bus.get_state("_app/config/section") == section


def main() -> int:
    print("Configuration master/detail headless capture:")
    _shot("bootstrap manifest", "manifest")
    _shot("identity", "identity")
    _shot("dusk gifts", "dusk_gifts")
    _shot("escalation filter", "escalation")
    _shot("agents", "agents", agent="composeux-architect")
    _shot("skills", "skills", skill="console-up")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
