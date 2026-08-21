"""
Tests for the AgentsSkillsWidget ([CXC-IC-CONFIG-LOADED]) — the Configuration
tab's Agents / Skills loaded-per-session list, and its publisher's file-set
projection.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import AgentsSkillsWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.agents_skills import (
    DEFAULT_AGENTS_NS, DEFAULT_SKILLS_NS,
    DEFAULT_AGENT_SELECT, DEFAULT_SKILL_SELECT, DEFAULT_OPEN_NS,
    DEFAULT_AGENT_DIR, DEFAULT_SKILL_DIR)
from composeio.render.engine.parser import parse_widget_tree

import contextlib
import tempfile

from .conftest import surface_to_array

RECT = Rect(0, 0, 974, 700)


@contextlib.contextmanager
def _temp_repo(files):
    """chdir into a temp repo root with the given {rel_path: text} files, so the
    widget's file-load-for-body (relative to cwd) reads them."""
    cwd = os.getcwd()
    d = tempfile.mkdtemp()
    try:
        for rel, text in files.items():
            ap = os.path.join(d, rel)
            os.makedirs(os.path.dirname(ap), exist_ok=True)
            with open(ap, "w", encoding="utf-8") as fh:
                fh.write(text)
        os.chdir(d)
        yield d
    finally:
        os.chdir(cwd)


class _Ctx:
    theme = "dark"
    reduced_motion = False

    def __init__(self):
        self.published = []

    def request_redraw(self, rect=None):
        pass

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def _mounted(mode="agents", ns=None):
    w = AgentsSkillsWidget("aw", mode=mode, namespace=ns)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.ctx = ctx                 # convenience handle for assertions
    w.arranged_rect = RECT
    return w


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# parser wiring — one subscribed namespace, mode-driven default
# =============================================================================

def test_parser_wires_agents_data_and_select_namespaces():
    comp = parse_widget_tree({"widgets": [
        {"type": "agents_skills", "id": "ag", "mode": "agents"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    # DATA + SELECTION seams both wired
    assert subbed == [DEFAULT_AGENTS_NS, DEFAULT_AGENT_SELECT]
    assert isinstance(comp.subscriptions[0][1], AgentsSkillsWidget)


def test_parser_wires_skills_namespaces_and_mode():
    comp = parse_widget_tree({"widgets": [
        {"type": "agents_skills", "id": "sk", "mode": "skills"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == [DEFAULT_SKILLS_NS, DEFAULT_SKILL_SELECT]
    assert comp.subscriptions[0][1].mode == "skills"


def test_parser_explicit_namespace_overrides_default():
    comp = parse_widget_tree({"widgets": [
        {"type": "agents_skills", "id": "ag", "mode": "agents",
         "namespace": "self/architect/agents",
         "select_namespace": "_app/config/agent"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == ["self/architect/agents", "_app/config/agent"]


# =============================================================================
# bus payload coercion
# =============================================================================

def test_bus_state_parses_array_payload():
    w = _mounted("agents")
    w.on_bus_state(w.namespace, [
        {"name": "composeio-expert", "description": "stack expert",
         "loaded": True},
        {"name": "defect-investigator", "description": "digs defects",
         "loaded": False}])
    assert [it["name"] for it in w._items] == ["composeio-expert",
                                               "defect-investigator"]
    assert w._items[1]["loaded"] is False


def test_bus_state_tolerates_wrapped_and_json_string():
    w = _mounted("skills")
    # {"skills":[...]} wrapper
    w.on_bus_state(w.namespace, {"skills": [{"name": "console-up",
                                             "description": "bring up"}]})
    assert w._items[0]["name"] == "console-up"
    assert w._items[0]["loaded"] is True     # defaults true
    # a JSON string round-trips through _coerce
    import json
    w.on_bus_state(w.namespace, json.dumps([{"name": "handover",
                                             "description": "ritual"}]))
    assert w._items[0]["name"] == "handover"


def test_bus_state_ignores_other_namespace_and_garbage():
    w = _mounted("agents")
    w.on_bus_state("some/other/ns", [{"name": "x"}])
    assert w._items == []
    w.on_bus_state(w.namespace, "not-json-garbage")
    assert w._items == []


# =============================================================================
# draw — header count, rows, empty state
# =============================================================================

def test_draw_paints_header_count_and_rows():
    w = _mounted("agents")
    w.on_bus_state(w.namespace, [
        {"name": "a", "description": "d1", "loaded": True},
        {"name": "b", "description": "d2", "loaded": True}])
    c = SkiaCanvas(); c.create_surface(974, 700)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    assert "2/2" in joined
    assert "a" in joined and "b" in joined
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_draw_empty_state():
    w = _mounted("skills")
    # the detail viewer shows the placeholder (never blank)
    assert "Select an item" in w._viewer.content
    c = SkiaCanvas(); c.create_surface(974, 700)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    assert "no skills" in "".join(drawn)      # empty list pane


# =============================================================================
# nested master/detail — list -> selected-item detail, selection on the bus
# =============================================================================

def test_selection_defaults_to_first_and_reconciles_by_name():
    w = _mounted("agents")
    w.on_bus_state(w.namespace, [
        {"name": "a", "description": "da", "loaded": True},
        {"name": "b", "description": "db", "loaded": True}])
    assert w._selected == 0 and w._selected_name == "a"
    # select b, then a data refresh keeps b selected by NAME (order changed)
    w.on_bus_state(w.select_namespace, "b")
    assert w._selected == 1
    w.on_bus_state(w.namespace, [
        {"name": "b", "description": "db2", "loaded": True},
        {"name": "a", "description": "da2", "loaded": True}])
    assert w._selected == 0 and w._selected_name == "b"


def test_list_tap_selects_and_publishes():
    w = _mounted("agents")
    w.on_bus_state(w.namespace, [
        {"name": "alpha", "description": "d1", "loaded": True},
        {"name": "beta", "description": "d2", "loaded": True}])
    c = SkiaCanvas(); c.create_surface(974, 700)
    w.draw(c, RECT)                          # records the row hit zones
    assert len(w._nav_bounds) == 2
    y0, y1 = w._nav_bounds[1]
    w.on_tap(20.0, (y0 + y1) / 2.0)
    assert w._selected == 1
    assert w.ctx.published[-1] == (DEFAULT_AGENT_SELECT, "beta")


# =============================================================================
# detail = Description / Tools / Implementation via markdown_viewer ([CXC-MDV])
# =============================================================================

_AGENT_MD = ("---\nname: tester\ndescription: front-matter description here\n"
             "tools: Read, Grep, Bash\n---\n"
             "# Tester\n\nThe **body** with `code` and a list:\n\n- one\n- two\n")
_SKILL_MD = ("---\nname: sk-one\ndescription: skill description here\n"
             "allowed-tools: Read\n---\n"
             "# Skill One\n\nThe skill BODY paragraph.\n")


def test_agent_detail_has_description_tools_implementation():
    files = {".claude/agents/tester.md": _AGENT_MD}
    with _temp_repo(files):
        w = _mounted("agents")
        w.on_bus_state(w.namespace, [
            {"name": "tester", "path": ".claude/agents/tester.md",
             "description": "front-matter description here", "loaded": True}])
        md = w._viewer.content
    # the three labelled sections, in order
    i_desc = md.find("## Description")
    i_tools = md.find("## Tools")
    i_impl = md.find("## Implementation")
    assert 0 <= i_desc < i_tools < i_impl, f"section order: {md[:120]!r}"
    assert "front-matter description here" in md
    # Tools parsed from the file front-matter (accurate)
    assert "Read, Grep, Bash" in md
    # Implementation = the body (front-matter stripped), body markdown present
    assert "The **body** with `code`" in md
    assert "name: tester" not in md            # front-matter NOT in the body


def test_skill_detail_omits_tools_but_has_implementation():
    files = {".claude/skills/sk-one/SKILL.md": _SKILL_MD}
    with _temp_repo(files):
        w = _mounted("skills")
        w.on_bus_state(w.namespace, [
            {"name": "sk-one", "path": ".claude/skills/sk-one/SKILL.md",
             "description": "skill description here", "loaded": True}])
        md = w._viewer.content
    assert "## Description" in md and "## Implementation" in md
    assert "## Tools" not in md                 # skills omit Tools
    assert "The skill BODY paragraph." in md
    assert "skill description here" in md


def test_detail_body_load_is_guarded_to_dotclaude():
    # a path escaping .claude/ must NOT be read (no arbitrary-path open)
    w = _mounted("agents")
    w.on_bus_state(w.namespace, [
        {"name": "evil", "path": "../../etc/passwd",
         "description": "x", "loaded": True}])
    md = w._viewer.content
    assert "## Implementation" in md
    assert "no implementation body found" in md  # guard refused the read


def test_detail_falls_back_to_conventional_path_when_missing():
    files = {".claude/agents/tester.md": _AGENT_MD}
    with _temp_repo(files):
        w = _mounted("agents")
        # NO path in the payload -> the widget builds .claude/agents/<name>.md
        w.on_bus_state(w.namespace, [
            {"name": "tester", "description": "d", "loaded": True}])
        md = w._viewer.content
    assert "The **body** with `code`" in md


# =============================================================================
# the (Open) button reveals the source directory via the reveal seam
# =============================================================================

def test_open_button_publishes_reveal_seam_agents():
    w = _mounted("agents")
    assert w.open_namespace == DEFAULT_OPEN_NS
    assert w.open_dir == DEFAULT_AGENT_DIR
    c = SkiaCanvas(); c.create_surface(974, 700)
    w.draw(c, RECT)                              # records the (Open) hit rect
    assert w._open_btn is not None
    x0, y0, x1, y1 = w._open_btn
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w.ctx.published[-1] == (DEFAULT_OPEN_NS, DEFAULT_AGENT_DIR)


def test_detail_scroll_routes_to_the_markdown_viewer():
    """The viewer is a REAL child arranged over the detail pane, so the engine's
    hit-test (which drives wheel-scroll dispatch) lands on it for detail-area
    points and on the widget itself for list-area points."""
    from composeio.render.engine.layout import Container
    w = _mounted("agents")
    w.on_bus_state(w.namespace, [
        {"name": "a", "description": "d", "loaded": True}])
    w.arrange(RECT)                              # sets the viewer's arranged_rect
    assert isinstance(w, Container)
    # a point in the RIGHT detail pane -> the markdown viewer (scroll target)
    hit = w.hit_test_tree(RECT.w - 40.0, 200.0)
    assert hit is w._viewer
    # a point in the LEFT list pane -> the widget itself (taps select)
    assert w.hit_test_tree(40.0, RECT.y + 200.0) is w


def test_open_button_reveals_skills_dir():
    w = _mounted("skills")
    assert w.open_dir == DEFAULT_SKILL_DIR
    c = SkiaCanvas(); c.create_surface(974, 700)
    w.draw(c, RECT)
    x0, y0, x1, y1 = w._open_btn
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w.ctx.published[-1] == (DEFAULT_OPEN_NS, DEFAULT_SKILL_DIR)


# =============================================================================
# publisher — the file-set projection (the wire contract)
# =============================================================================

def test_publisher_reads_real_repo_set():
    sys.path.insert(0, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher"))
    import agents_skills_publisher as pub
    proj = pub.project(os.path.join(REPO_ROOT, ".claude"))
    # the real repo set — names match the files on disk
    agent_names = {a["name"] for a in proj["agents"]}
    assert "composeio-expert" in agent_names
    assert "composeux-architect" in agent_names
    skill_names = {s["name"] for s in proj["skills"]}
    assert "bounce-console" in skill_names
    # the payload is the declared shape (now carries `path` for file-load-for-body)
    for arr in (proj["agents"], proj["skills"]):
        for it in arr:
            assert set(it) == {"name", "description", "path", "loaded"}
            assert it["loaded"] is True
            assert os.path.isfile(os.path.join(REPO_ROOT, it["path"]))
