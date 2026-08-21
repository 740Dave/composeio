"""
Tests for the LOCKED compact Deliverables rail card
([DEC-DELIVERABLE-HISTORY-WIDGET]) — the previs
docs/intelligence-console/previs/deliverable-compact.html on the engine.

Focused per the slice acceptance: given a `self/deliverables` payload
(the deliverable_publisher projection), the card renders the 3-cell
gauge + the last-5 rows, filenames BOLD when unseen / plain when seen;
a row tap publishes `_app/deliverable/open` = the row's path; the scene
wiring is additive (frozen subscriptions intact).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from tests.render.console_scene_ids import chat_ns
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import DeliverableCardWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.deliverable_card import (
    HEADER_H, ROW_H, ROWS_TOP, VIOLET)

from .conftest import surface_to_array

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")

PAYLOAD = {
    "stats": {"needs_enrich": 31, "new_session": 5, "tracked": 37},
    "recent": [
        {"name": "deliverable-compact.html", "path": "docs/previs/a.html",
         "kind": "previs", "modified": "2026-07-20", "unseen": True},
        {"name": "GUIDELINES.md", "path": "docs/sweep/GUIDELINES.md",
         "kind": "brief", "modified": "2026-07-20", "unseen": False},
        {"name": "decision-graph.md", "path": "docs/dg.md",
         "kind": "report", "modified": "2026-07-19", "unseen": True},
    ],
    "session": "2026-07-20",
}


def _canvas(w=360, h=320):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x07, 0x0A, 0x0F)
    c.fill_rect(0, 0, w, h)
    return c


class _Ctx:
    """Minimal mount context: theme plumbing + a publish recorder."""
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []
        self.redraws = 0

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


class _RecCanvas(SkiaCanvas):
    """Records (text, font_weight, fill_color) at draw_text time — the
    direct probe for 'BOLD ink when unseen, plain dim when seen'."""

    def __init__(self, w=360, h=320):
        super().__init__()
        self.create_surface(w, h)
        self._weight = "regular"
        self._fill = (0, 0, 0, 255)
        self.texts = []
        self.fill_paths = 0     # the drawn ▸ affordances (chip + per row)

    def fill_path(self, commands):
        self.fill_paths += 1
        super().fill_path(commands)

    def set_font(self, family, size, weight="regular"):
        self._weight = weight
        super().set_font(family, size, weight)

    def set_fill_color(self, r, g, b, a=255):
        self._fill = (r, g, b, a)
        super().set_fill_color(r, g, b, a)

    def draw_text(self, text, x, y, align="left"):
        self.texts.append((text, self._weight, self._fill))
        super().draw_text(text, x, y, align)


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# payload -> state (the publisher contract)
# =============================================================================

def test_payload_coercion_dict_and_json_string():
    import json
    for value in (PAYLOAD, json.dumps(PAYLOAD)):
        w = DeliverableCardWidget("d")
        w.on_bus_state("self/deliverables", value)
        assert w._stats["needs_enrich"] == 31
        assert [r["name"] for r in w._rows] == [
            "deliverable-compact.html", "GUIDELINES.md",
            "decision-graph.md"]
        assert w._session == "2026-07-20"


def test_bus_update_requests_scoped_redraw_only():
    w = DeliverableCardWidget("d")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state("self/deliverables", PAYLOAD)
    assert ctx.redraws == 1          # request_redraw(), nothing else
    # garbage payloads never clobber state (graceful)
    w.on_bus_state("self/deliverables", "not-json")
    w.on_bus_state("self/deliverables", 42)
    assert w._stats["tracked"] == 37


def test_recent_capped_at_max_rows():
    w = DeliverableCardWidget("d", max_rows=5)
    rows = [{"name": f"f{i}.md", "path": f"p/{i}", "kind": "report",
             "unseen": True} for i in range(9)]
    w.on_bus_state("self/deliverables", {"stats": {}, "recent": rows})
    assert len(w._rows) == 5


# =============================================================================
# the 3-cell gauge — warn / azure / muted
# =============================================================================

def test_stat_cells_values_and_semantic_colors():
    w = DeliverableCardWidget("d")
    w.on_bus_state("self/deliverables", PAYLOAD)
    t = ct.CONSOLE.active
    cells = w._stat_cells()
    assert [(v, l) for v, l, *_ in cells] == [
        ("31", "NEEDS ENRICH"), ("5", "NEW · SESSION"), ("37", "TRACKED")]
    assert cells[0][2] == t.warn
    assert cells[1][2] == t.accent
    assert cells[2][2] == t.dim
    # warn border only when the backlog is real
    assert cells[0][3][:3] == t.warn[:3]
    empty = DeliverableCardWidget("e")
    assert all(v == "—" for v, *_ in empty._stat_cells())
    assert empty._stat_cells()[0][3] == t.line


def test_gauge_values_drawn_in_semantic_ink():
    w = DeliverableCardWidget("d")
    w.on_bus_state("self/deliverables", PAYLOAD)
    c = _RecCanvas()
    w.draw(c, Rect(4, 4, 332, 300))
    t = ct.CONSOLE.active
    by_text = {txt: fill for txt, wt, fill in c.texts}
    assert by_text["31"] == tuple(t.warn)
    assert by_text["5"] == tuple(t.accent)
    assert by_text["37"] == tuple(t.dim)


# =============================================================================
# rows — BOLD when unseen, plain when seen; kind hues
# =============================================================================

def test_row_names_bold_when_unseen_plain_when_seen():
    w = DeliverableCardWidget("d")
    w.on_bus_state("self/deliverables", PAYLOAD)
    c = _RecCanvas()
    w.draw(c, Rect(4, 4, 332, 300))
    t = ct.CONSOLE.active
    drawn = {txt: (wt, fill) for txt, wt, fill in c.texts}
    # unseen -> bold ink
    assert drawn["deliverable-compact.html"] == ("bold", tuple(t.ink))
    assert drawn["decision-graph.md"] == ("bold", tuple(t.ink))
    # seen -> regular dim
    assert drawn["GUIDELINES.md"] == ("regular", tuple(t.dim))
    # the ▸ open affordances are DRAWN PATHS (Plex has no U+25B8):
    # one on the Artifacts chip + one per row
    assert c.fill_paths == 1 + len(w._rows)


def test_kind_hues_match_previs():
    t = ct.CONSOLE.active
    k = DeliverableCardWidget._kind_color
    assert k("previs", t) == VIOLET
    assert k("brief", t) == t.ok
    assert k("primer", t) == t.accent
    assert k("report", t) == t.faint
    assert k("—", t) == t.faint          # un-enriched -> muted, never state


def test_draw_paints_and_empty_state_graceful():
    w = DeliverableCardWidget("d")
    w.on_bus_state("self/deliverables", PAYLOAD)
    c = _canvas()
    w.draw(c, Rect(4, 4, 332, 300))
    assert surface_to_array(c)[..., :3].std() > 3.0
    # never published, still paints (awaiting sweep)
    e = DeliverableCardWidget("e")
    c2 = _canvas()
    e.draw(c2, Rect(4, 4, 332, 300))
    assert surface_to_array(c2)[..., :3].std() > 3.0


# =============================================================================
# the Open tap seam ([DEC] this slice: emit the namespace only)
# =============================================================================

def test_row_tap_publishes_open_with_the_rows_path():
    w = DeliverableCardWidget("d")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state("self/deliverables", PAYLOAD)
    w.arranged_rect = Rect(0, 0, 332, 300)
    # centre of row 1 (GUIDELINES.md)
    w.on_tap(100, ROWS_TOP + 1 * ROW_H + ROW_H / 2)
    assert ctx.published == [("_app/deliverable/open",
                              "docs/sweep/GUIDELINES.md")]
    # below the last row: no publish
    w.on_tap(100, ROWS_TOP + 7 * ROW_H)
    assert len(ctx.published) == 1


def test_artifacts_chip_tap_publishes_tab_stub():
    w = DeliverableCardWidget("d")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state("self/deliverables", PAYLOAD)
    w.arranged_rect = Rect(0, 0, 332, 300)
    c = _canvas()
    w.draw(c, Rect(0, 0, 332, 300))       # records the chip rect
    assert w._chip_rect is not None
    cx = w._chip_rect[0] + w._chip_rect[2] / 2
    cy = w._chip_rect[1] + w._chip_rect[3] / 2
    w.on_tap(cx, cy)
    assert ctx.published == [("_app/console/tab", "artifacts")]
    # header tap OUTSIDE the chip is a no-op
    w.on_tap(40, HEADER_H / 2)
    assert len(ctx.published) == 1


def test_widget_is_interactive_with_shadow_overflow():
    w = DeliverableCardWidget("d")
    assert w.interactive is True
    assert w.paint_overflow == ct.SHADOW_PAINT_OVERFLOW


# =============================================================================
# wiring — parser + the console scene (additive, frozen seam intact)
# =============================================================================

def test_parser_wires_single_subscription():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "deliverable_card", "id": "dlv",
         "namespace": "self/deliverables"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed.count("self/deliverables") == 1
    w = comp.subscriptions[0][1]
    assert isinstance(w, DeliverableCardWidget)
    assert w.open_namespace == "_app/deliverable/open"
    assert w.tab_namespace == "_app/console/tab"


def test_console_scene_carries_card_and_frozen_wiring():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "self/deliverables" in subbed
    # FROZEN-WIRING DISCIPLINE: the proven seam is untouched by this slice
    # [DEC-CONSOLE-CHAT-ID-PARAM] the chat leg derives from the scene's ONE
    # chat_id line; the host-pinned telemetry legs stay literal (they do NOT follow it).
    for frozen in ("thread/architect/narration", chat_ns("out"),
                   chat_ns("in"), "self/architect/context_pct",
                   "self/architect/subagents", "self/architect/registered",
                   "self/architect/rate_limit"):
        assert frozen in subbed, frozen
    declared = [n.get("name") for n in comp.namespaces]
    assert "self/deliverables" in declared
    assert "_app/deliverable/open" in declared
    ns_def = next(n for n in comp.namespaces
                  if n.get("name") == "self/deliverables")
    assert ns_def.get("state_engine") == "json"
    assert ns_def.get("scope") == "global"
    # the seed shows BOTH row looks in the static render
    seeded = ns_def.get("initial", {}).get("recent", [])
    assert any(r.get("unseen") for r in seeded)
    assert any(not r.get("unseen") for r in seeded)
