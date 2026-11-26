"""
Tests for the "Local Memory" Configuration section
([DEC-LOCAL-MEMORY-CONFIG-SECTION]) — a DELTA on the SAME AgentsSkillsWidget
nested master/detail machinery (mode: memory), plus its memory_publisher's
MEMORY.md index projection.  Mirrors test_agents_skills.py.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

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
    DEFAULT_MEMORY_NS, DEFAULT_MEMORY_SELECT)
from composeio.render.engine.parser import parse_widget_tree

from .conftest import surface_to_array

RECT = Rect(0, 0, 974, 700)


class _Ctx:
    theme = "dark"
    reduced_motion = False

    def __init__(self):
        self.published = []

    def request_redraw(self, rect=None):
        pass

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def _mounted(ns=None):
    w = AgentsSkillsWidget("mem", mode="memory", namespace=ns)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.ctx = ctx
    w.arranged_rect = RECT
    return w


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# parser wiring — memory mode subscribes the memory data + selection seams
# =============================================================================

def test_parser_wires_memory_data_and_select_namespaces():
    comp = parse_widget_tree({"widgets": [
        {"type": "agents_skills", "id": "mem", "mode": "memory"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == [DEFAULT_MEMORY_NS, DEFAULT_MEMORY_SELECT]
    assert isinstance(comp.subscriptions[0][1], AgentsSkillsWidget)
    assert comp.subscriptions[0][1].mode == "memory"


def test_parser_explicit_memory_namespace_overrides_default():
    comp = parse_widget_tree({"widgets": [
        {"type": "agents_skills", "id": "mem", "mode": "memory",
         "namespace": "self/architect/memories",
         "select_namespace": "_app/config/memory"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed == ["self/architect/memories", "_app/config/memory"]


# =============================================================================
# bus payload coercion — {name, hook, path} -> the shared item shape
# =============================================================================

def test_bus_state_parses_hook_as_description():
    w = _mounted()
    w.on_bus_state(w.namespace, [
        {"name": "rpi5 kiosk", "hook": "boots NVMe, launcher autostart",
         "path": "/x/rpi5-kiosk.md"},
        {"name": "haven fork", "hook": "the claude-hud fork",
         "path": "/x/haven.md"}])
    assert [it["name"] for it in w._items] == ["rpi5 kiosk", "haven fork"]
    # hook is folded onto the description slot the detail/rows render
    assert w._items[0]["description"] == "boots NVMe, launcher autostart"
    assert w._items[0]["loaded"] is True     # defaults true (no loaded key)


def test_bus_state_tolerates_wrapped_and_json_string():
    w = _mounted()
    w.on_bus_state(w.namespace, {"memories": [
        {"name": "a", "hook": "ha", "path": "/x/a.md"}]})
    assert w._items[0]["name"] == "a"
    w.on_bus_state(w.namespace, json.dumps([
        {"name": "b", "hook": "hb", "path": "/x/b.md"}]))
    assert w._items[0]["name"] == "b"


# =============================================================================
# draw — header count + rows + empty state
# =============================================================================

def test_draw_paints_header_count_and_rows():
    w = _mounted()
    w.on_bus_state(w.namespace, [
        {"name": "alpha", "hook": "h1", "path": "/x/a.md"},
        {"name": "beta", "hook": "h2", "path": "/x/b.md"}])
    c = SkiaCanvas(); c.create_surface(974, 700)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    assert "2/2" in joined
    assert "alpha" in joined and "beta" in joined
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_draw_empty_state_shows_placeholder():
    w = _mounted()
    assert "Select an item" in w._viewer.content
    c = SkiaCanvas(); c.create_surface(974, 700)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    assert "no memory" in "".join(drawn)


# =============================================================================
# master/detail — list -> selected note detail, selection on the bus
# =============================================================================

def test_selection_defaults_to_first_and_reconciles_by_name():
    w = _mounted()
    w.on_bus_state(w.namespace, [
        {"name": "a", "hook": "ha", "path": "/x/a.md"},
        {"name": "b", "hook": "hb", "path": "/x/b.md"}])
    assert w._selected == 0 and w._selected_name == "a"
    w.on_bus_state(w.select_namespace, "b")
    assert w._selected == 1
    # a data refresh (order changed) keeps b selected by NAME
    w.on_bus_state(w.namespace, [
        {"name": "b", "hook": "hb2", "path": "/x/b.md"},
        {"name": "a", "hook": "ha2", "path": "/x/a.md"}])
    assert w._selected == 0 and w._selected_name == "b"


def test_list_tap_selects_and_publishes_memory_select_seam():
    w = _mounted()
    w.on_bus_state(w.namespace, [
        {"name": "alpha", "hook": "h1", "path": "/x/a.md"},
        {"name": "beta", "hook": "h2", "path": "/x/b.md"}])
    c = SkiaCanvas(); c.create_surface(974, 700)
    w.draw(c, RECT)
    assert len(w._nav_bounds) == 2
    y0, y1 = w._nav_bounds[1]
    w.on_tap(20.0, (y0 + y1) / 2.0)
    assert w._selected == 1
    assert w.ctx.published[-1] == (DEFAULT_MEMORY_SELECT, "beta")


# =============================================================================
# detail = Description / Note (no Tools) via the read-only markdown_viewer,
# loading the note .md by absolute path ([CXC-MDV], file-load-for-body)
# =============================================================================

_NOTE_MD = ("# rpi5 kiosk\n\nThe **note body** with `code` and a list:\n\n"
            "- boots NVMe\n- launcher autostart\n")


def test_memory_detail_loads_note_body_and_omits_tools():
    with tempfile.TemporaryDirectory() as d:
        note = os.path.join(d, "rpi5-kiosk.md")
        with open(note, "w", encoding="utf-8") as fh:
            fh.write(_NOTE_MD)
        w = _mounted()
        w.on_bus_state(w.namespace, [
            {"name": "rpi5 kiosk", "hook": "boots NVMe, launcher autostart",
             "path": note}])
        md = w._viewer.content
    # Description + Note sections, Note (not Implementation), no Tools
    i_desc = md.find("## Description")
    i_note = md.find("## Note")
    assert 0 <= i_desc < i_note, f"section order: {md[:120]!r}"
    assert "## Tools" not in md                       # memory omits Tools
    assert "## Implementation" not in md              # body label is Note
    assert "boots NVMe, launcher autostart" in md     # the hook == Description
    assert "The **note body** with `code`" in md      # the loaded note body


def test_memory_detail_guard_refuses_path_not_in_payload():
    # a path NOT enumerated in the current payload must NOT be read.
    with tempfile.TemporaryDirectory() as d:
        secret = os.path.join(d, "secret.md")
        with open(secret, "w", encoding="utf-8") as fh:
            fh.write("# secret\n\ntop secret body\n")
        w = _mounted()
        # the payload lists a DIFFERENT file; selecting it, the body-load guard
        # only permits paths the payload enumerated.
        w.on_bus_state(w.namespace, [
            {"name": "listed", "hook": "h", "path": os.path.join(d, "listed.md")}])
        # force the widget to attempt loading the secret path directly
        body, tools = w._read_body_tools(secret)
        assert body == "" and tools == []
        # and a traversal path is refused too
        assert w._read_body_tools("../../etc/passwd") == ("", [])


def test_memory_detail_missing_file_reports_no_note_body():
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "gone.md")
        w = _mounted()
        w.on_bus_state(w.namespace, [
            {"name": "gone", "hook": "h", "path": missing}])
        md = w._viewer.content
    assert "## Note" in md
    assert "no note body found" in md


def test_detail_scroll_routes_to_the_markdown_viewer():
    from composeio.render.engine.layout import Container
    w = _mounted()
    w.on_bus_state(w.namespace, [
        {"name": "a", "hook": "h", "path": "/x/a.md"}])
    w.arrange(RECT)
    assert isinstance(w, Container)
    assert w.hit_test_tree(RECT.w - 40.0, 200.0) is w._viewer
    assert w.hit_test_tree(40.0, RECT.y + 200.0) is w


# =============================================================================
# memory_publisher — the MEMORY.md index projection (the wire contract)
# =============================================================================

def _import_publisher():
    p = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher")
    if p not in sys.path:
        sys.path.insert(0, p)
    import memory_publisher as pub
    return pub


def test_publisher_parses_index_in_order_with_abs_paths():
    pub = _import_publisher()
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("# Memory Index\n\n"
                    "- [Haven fork](haven-hud-fork.md) — David's fork\n"
                    "- [rpi5 kiosk](rpi5-kiosk.md) - boots NVMe\n"
                    "- [External](https://example.com) — skip\n")
        for note in ("haven-hud-fork.md", "rpi5-kiosk.md"):
            open(os.path.join(d, note), "w").close()
        arr = pub.project(d)
    assert [m["name"] for m in arr] == ["Haven fork", "rpi5 kiosk"]  # index order
    assert arr[0]["hook"] == "David's fork"
    assert arr[1]["hook"] == "boots NVMe"
    for m in arr:
        assert set(m) == {"name", "hook", "path"}
        assert os.path.isabs(m["path"]) and m["path"].endswith(".md")


def test_publisher_missing_index_yields_empty():
    pub = _import_publisher()
    with tempfile.TemporaryDirectory() as d:
        assert pub.project(os.path.join(d, "nope")) == []


def test_publisher_resolve_dir_precedence():
    pub = _import_publisher()
    with tempfile.TemporaryDirectory() as d:
        assert pub.resolve_dir(d) == d          # CLI wins
        os.environ["COMPOSEIO_MEMORY_DIR"] = d
        try:
            assert pub.resolve_dir(None) == d    # env resolves
        finally:
            del os.environ["COMPOSEIO_MEMORY_DIR"]


def test_publisher_selftest_passes():
    pub = _import_publisher()
    assert pub._selftest() == 0
