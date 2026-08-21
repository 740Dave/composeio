"""
Tests for the Deliverables render slice ([DEC-DELIVERABLE-HISTORY-WIDGET],
the FULL remaining previs surface):

  A. DeliverableAlertWidget — the deliverable/new live-alert banner
     (shows on a monitor alert payload, Open publishes
     _app/deliverable/open, Dismiss/TTL hides, idle slot is inert).
  B. DeliverableHistoryWidget — the Artifacts filter/sort view (kind
     chips, live search, Created/Modified sort, the FIRST-256-CHARS
     summary readout, NEEDS-ENRICHMENT pills, per-row Open ▸, scroll).
  C. ConsoleTabWidget — [Collaboration | Artifacts] on TabWidget routing
     (bus-driven tab state, tap publishes the seam, the needs-enrich
     badge).

Plus the scene wiring: additive namespaces, the moved-inside-a-tab
collaboration content with its FROZEN subscriptions intact, and a
headless render of BOTH tabs.
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
from composeio.render.widgets import (ConsoleTabWidget,
                                      DeliverableAlertWidget,
                                      DeliverableHistoryWidget)
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.deliverable_history import (

    KIND_ORDER, SUMMARY_CLIP, THEAD_H)
# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.
from tests.render.console_scene_ids import (composer_field as _cf,  # noqa: E402
                                              composer_send as _cs,
                                              send_trigger_ns as _send_ns,
                                              thread as _th)

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")

ALERT = {"path": "docs/previs/deliverable-history.html",
         "name": "deliverable-history.html",
         "node_id": "DELIV-x", "change": "new"}

ROWS = [
    {"id": "d1", "name": "alpha.md", "path": "docs/a/alpha.md",
     "kind": "report", "created": "2026-07-18", "modified": "2026-07-20",
     "confidentiality": "internal-only", "summary": "S" * 300,
     "summary_len": 300, "topics": ["continuity"], "unseen": True,
     "pending": []},
    {"id": "d2", "name": "beta.html", "path": "docs/previs/beta.html",
     "kind": "previs", "created": "2026-07-20", "modified": "2026-07-21",
     "confidentiality": "public", "summary": "short summary",
     "summary_len": 13, "topics": ["console"], "unseen": True,
     "pending": []},
    {"id": "d3", "name": "gamma.md", "path": "docs/g/gamma.md",
     "kind": "brief", "created": "2026-07-19", "modified": "2026-07-19",
     "confidentiality": "internal-only", "summary": "",
     "summary_len": 0, "topics": [], "unseen": False,
     "pending": ["summary", "topics"]},
]
PAYLOAD = {"stats": {"needs_enrich": 1, "new_session": 3, "tracked": 3},
           "recent": [], "all": ROWS, "session": "2026-07-21"}


class _Ctx:
    """Mount context: theme + publish recorder + clock registry."""

    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []
        self.redraws = 0
        self.clocked = []

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))

    def register_clocked(self, w):
        if w not in self.clocked:
            self.clocked.append(w)

    def unregister_clocked(self, w):
        if w in self.clocked:
            self.clocked.remove(w)


class _RecCanvas(SkiaCanvas):
    """Records draw_text calls (text, weight, fill)."""

    def __init__(self, w=1280, h=1560):
        super().__init__()
        self.create_surface(w, h)
        self._weight = "regular"
        self._fill = (0, 0, 0, 255)
        self.texts = []
        self.fill_paths = 0

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
# A. the live-alert banner
# =============================================================================

def _mounted_alert(ttl=25.0):
    w = DeliverableAlertWidget("al", ttl_s=ttl)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = Rect(0, 0, 908, 46)
    return w, ctx


def test_alert_shows_on_new_payload_and_draws_banner():
    w, ctx = _mounted_alert()
    now = [100.0]
    w._now = lambda: now[0]
    w.on_bus_state("deliverable/new", ALERT)
    assert ctx.redraws == 1                      # scoped redraw only
    assert w in ctx.clocked                      # pulse + TTL clock armed
    c = _RecCanvas(940, 60)
    w.draw(c, Rect(0, 0, 908, 46))
    drawn = {t for t, _, _ in c.texts}
    assert "deliverable/new" in drawn
    assert "New document created — " in drawn
    assert "Open" in drawn and "Dismiss" in drawn
    by_text = {t: (wt, f) for t, wt, f in c.texts}
    assert by_text["deliverable-history.html"][0] == "bold"
    assert c.fill_paths >= 1                     # the drawn ▸

    # modified variant flips the lead line
    w.on_bus_state("deliverable/modified", dict(ALERT, change="modified"))
    c2 = _RecCanvas(940, 60)
    w.draw(c2, Rect(0, 0, 908, 46))
    assert "Document modified — " in {t for t, _, _ in c2.texts}


def test_alert_idle_slot_draws_nothing_and_is_inert():
    w, _ = _mounted_alert()
    c = _RecCanvas(940, 60)
    w.draw(c, Rect(0, 0, 908, 46))
    assert c.texts == [] and c.fill_paths == 0
    assert w.hit_test(10, 10) is False           # never swallows idle taps
    # garbage payloads never raise the banner
    w.on_bus_state("deliverable/new", "not-json")
    w.on_bus_state("deliverable/new", {"change": "new"})   # no name
    assert w._alert is None


def test_alert_open_publishes_path_and_dismisses():
    w, ctx = _mounted_alert()
    w.on_bus_state("deliverable/new", ALERT)
    c = _RecCanvas(940, 60)
    w.draw(c, Rect(0, 0, 908, 46))               # records button rects
    ox, oy, ow, oh = w._open_rect
    w.on_tap(ox + ow / 2, oy + oh / 2)
    assert ctx.published == [("_app/deliverable/open", ALERT["path"])]
    assert w._alert is None and w not in ctx.clocked


def test_alert_dismiss_and_ttl_expiry():
    w, ctx = _mounted_alert(ttl=25.0)
    now = [50.0]
    w._now = lambda: now[0]
    w.on_bus_state("deliverable/new", ALERT)
    c = _RecCanvas(940, 60)
    w.draw(c, Rect(0, 0, 908, 46))
    dx, dy, dw, dh = w._dismiss_rect
    w.on_tap(dx + dw / 2, dy + dh / 2)
    assert w._alert is None and ctx.published == []
    # TTL: re-raise, then advance the clock past ttl_s
    w.on_bus_state("deliverable/new", ALERT)
    assert w._alert is not None
    now[0] += 24.9
    w.on_clock_tick(now[0])
    assert w._alert is not None                  # still inside the TTL
    now[0] += 0.2
    w.on_clock_tick(now[0])
    assert w._alert is None                      # auto-dismissed
    assert w not in ctx.clocked


# =============================================================================
# B. the Artifacts view
# =============================================================================

def _hist(payload=PAYLOAD):
    w = DeliverableHistoryWidget("h")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = Rect(0, 0, 1256, 1500)
    w.arrange(Rect(0, 0, 1256, 1500))
    if payload is not None:
        w.on_bus_state("self/deliverables", payload)
    return w, ctx


def test_history_payload_ingest_and_scoped_redraw():
    w, ctx = _hist()
    assert [r["id"] for r in w._all] == ["d1", "d2", "d3"]
    assert w._session == "2026-07-21"
    assert ctx.redraws == 1
    w.on_bus_state("self/deliverables", 42)      # garbage never clobbers
    assert len(w._all) == 3


def test_history_filters_by_kind_with_counts():
    w, _ = _hist()
    assert w.kind_counts() == {"all": 3, "report": 1, "previs": 1,
                               "brief": 1}
    assert w.chip_kinds() == ["all", *KIND_ORDER]
    w.set_kind("previs")
    assert [r["id"] for r in w.visible_rows()] == ["d2"]
    w.set_kind("all")
    assert len(w.visible_rows()) == 3


def test_history_live_search_over_name_path_topic():
    w, _ = _hist()
    w._set_query("beta")                          # name
    assert [r["id"] for r in w.visible_rows()] == ["d2"]
    w._set_query("docs/g/")                       # path
    assert [r["id"] for r in w.visible_rows()] == ["d3"]
    w._set_query("continuity")                    # topic
    assert [r["id"] for r in w.visible_rows()] == ["d1"]
    w._set_query("zzz-nothing")
    assert w.visible_rows() == []
    # the embedded search child feeds the same seam
    w._set_query("")
    w.search.on_text("beta")
    assert w.query == "beta"
    assert [r["id"] for r in w.visible_rows()] == ["d2"]


def test_history_sorts_created_and_modified_asc_desc():
    w, _ = _hist()
    # default: modified desc (previs initial Modified ▼)
    assert w.sort_key == "modified" and w.sort_dir == -1
    assert [r["id"] for r in w.visible_rows()] == ["d2", "d1", "d3"]
    w.toggle_sort("modified")                     # same column flips -> asc
    assert [r["id"] for r in w.visible_rows()] == ["d3", "d1", "d2"]
    w.toggle_sort("created")                      # new column starts desc
    assert w.sort_dir == -1
    assert [r["id"] for r in w.visible_rows()] == ["d2", "d3", "d1"]
    w.toggle_sort("created")
    assert [r["id"] for r in w.visible_rows()] == ["d1", "d3", "d2"]


def test_history_sorts_by_document_name():
    """DEFECT: the Artifacts view could not sort by artifact NAME — the previs
    shipped Created/Modified sortable only (a design oversight: no name-sort
    control, `toggle_sort` rejected every other key). The Document column is now
    sortable and sorts on the name, case-folded, default A→Z."""
    w, _ = _hist()
    # names: d1 alpha.md, d2 beta.html, d3 gamma.md
    w.toggle_sort("doc")                          # new column -> name ASC (A→Z)
    assert w.sort_key == "doc" and w.sort_dir == 1
    assert [r["id"] for r in w.visible_rows()] == ["d1", "d2", "d3"]
    w.toggle_sort("doc")                          # same column flips -> Z→A
    assert w.sort_dir == -1
    assert [r["id"] for r in w.visible_rows()] == ["d3", "d2", "d1"]
    # case-fold: an Uppercase name orders by letter, not ASCII (caps-first)
    rows = [dict(ROWS[0], id="A", name="Apple.md"),
            dict(ROWS[0], id="b", name="banana.md"),
            dict(ROWS[0], id="C", name="Cherry.md")]
    w2, _ = _hist(dict(PAYLOAD, all=rows))
    w2.toggle_sort("doc")
    assert [r["id"] for r in w2.visible_rows()] == ["A", "b", "C"]


def test_history_document_header_is_sortable_and_taps_route():
    """The DOCUMENT header registers a sort hit-rect and a tap on it toggles the
    name sort — the missing control, now present and wired."""
    w, _ = _hist()
    c = _RecCanvas()
    w.draw(c, Rect(0, 0, 1256, 1500))             # records _sort_rects
    keys = {k for _, k in w._sort_rects}
    assert "doc" in keys                          # the name-sort control exists
    sort = next(r for r, k in w._sort_rects if k == "doc")
    w.on_tap(sort.x + 10, sort.y + THEAD_H / 2)
    assert w.sort_key == "doc" and w.sort_dir == 1


def test_history_summary_truncates_at_256_with_readout():
    clipped, readout = DeliverableHistoryWidget.summary_cell(ROWS[0])
    assert clipped == "S" * SUMMARY_CLIP + "…"
    assert readout == "300 chars · showing 256"
    clipped2, readout2 = DeliverableHistoryWidget.summary_cell(ROWS[1])
    assert clipped2 == "short summary"            # under the clip: no …
    assert readout2 == "13 chars · showing 13"
    # summary_len missing -> falls back to len(summary)
    _, r3 = DeliverableHistoryWidget.summary_cell(
        {"summary": "ab", "pending": []})
    assert r3 == "2 chars · showing 2"
    # the PUBLISHER pre-clips summary to 256 while summary_len keeps the
    # true length — the ellipsis must key off the TRUE length (found
    # against the live --once projection)
    c4, r4 = DeliverableHistoryWidget.summary_cell(
        {"summary": "x" * 256, "summary_len": 334, "pending": []})
    assert c4 == "x" * 256 + "…"
    assert r4 == "334 chars · showing 256"


def test_history_draw_shows_pill_readout_and_chips():
    w, _ = _hist()
    c = _RecCanvas()
    w.draw(c, Rect(0, 0, 1256, 1500))
    drawn = {t for t, _, _ in c.texts}
    assert "NEEDS-ENRICHMENT" in drawn            # the un-enriched row
    assert "300 chars · showing 256" in drawn     # the firm requirement
    assert "All · 3" in drawn and "previs · 1" in drawn
    assert "3 shown" in drawn
    t = ct.CONSOLE.active
    by_text = {txt: fill for txt, _, fill in c.texts}
    assert by_text["NEEDS-ENRICHMENT"] == tuple(t.warn)
    assert by_text["2026-07-21"] == tuple(t.accent)   # mod == session
    # pending row lists its pending fields
    assert any("pending: summary, topics" in txt for txt in drawn)


def test_history_open_tap_publishes_row_path():
    w, ctx = _hist()
    c = _RecCanvas()
    w.draw(c, Rect(0, 0, 1256, 1500))             # records open rects
    assert len(w._open_rects) == 3
    r, path = w._open_rects[0]
    assert path == "docs/previs/beta.html"        # modified-desc first row
    w.on_tap(r.x + r.w / 2, r.y + r.h / 2)
    assert ctx.published == [("_app/deliverable/open",
                              "docs/previs/beta.html")]


def test_history_chip_and_sort_taps_route():
    w, _ = _hist()
    c = _RecCanvas()
    w.draw(c, Rect(0, 0, 1256, 1500))
    chip = next(r for r, k in w._chip_rects if k == "brief")
    w.on_tap(chip.x + chip.w / 2, chip.y + chip.h / 2)
    assert w.active_kind == "brief"
    sort = next(r for r, k in w._sort_rects if k == "created")
    w.on_tap(sort.x + 10, sort.y + THEAD_H / 2)
    assert w.sort_key == "created" and w.sort_dir == -1


def test_history_scroll_clamps_to_content():
    rows = [dict(ROWS[0], id=f"r{i}", name=f"f{i}.md",
                 path=f"docs/{i}.md") for i in range(41)]
    w, ctx = _hist(dict(PAYLOAD, all=rows))
    c = _RecCanvas(1280, 900)
    w.arranged_rect = Rect(0, 0, 1256, 860)
    w.arrange(Rect(0, 0, 1256, 860))
    w.draw(c, Rect(0, 0, 1256, 860))              # measures max_scroll
    assert w._max_scroll > 0.0                    # 41 rows overflow
    w.on_scroll(0, -3)                            # wheel-down -> forward
    assert w._scroll == 120.0
    w.on_scroll(0, 1000)                          # clamp at the top
    assert w._scroll == 0.0
    w.on_scroll(0, -1e9)                          # clamp at the bottom
    assert w._scroll == w._max_scroll


def test_history_hues_and_access_colors():
    t = ct.CONSOLE.active
    k = DeliverableHistoryWidget._kind_hue
    from composeio.render.widgets.deliverable_card import VIOLET
    from composeio.render.widgets.deliverable_history import (
        PRESENTATION, RESEARCH)
    assert k("previs", t) == VIOLET
    assert k("brief", t) == t.ok
    assert k("primer", t) == t.accent
    assert k("research", t) == RESEARCH
    assert k("presentation", t) == PRESENTATION
    a = DeliverableHistoryWidget._access_color
    assert a("public", t) == t.ok
    assert a("internal-only", t) == t.faint
    assert a("nda", t) == t.crit
    assert a("confidential", t) == t.warn


def test_history_empty_states():
    w, _ = _hist(payload=None)
    c = _RecCanvas()
    w.draw(c, Rect(0, 0, 1256, 1500))
    assert "awaiting deliverable sweep…" in {t for t, _, _ in c.texts}
    w2, _ = _hist()
    w2._set_query("zzz-none")
    c2 = _RecCanvas()
    w2.draw(c2, Rect(0, 0, 1256, 1500))
    assert "No deliverables match this filter." in \
        {t for t, _, _ in c2.texts}


def test_history_parser_wires_single_subscription():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "deliverable_history", "id": "hv",
         "namespace": "self/deliverables"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed.count("self/deliverables") == 1
    w = comp.subscriptions[0][1]
    assert isinstance(w, DeliverableHistoryWidget)
    assert w.open_namespace == "_app/deliverable/open"
    assert w.search in w.children                 # the live search child


# =============================================================================
# C. the console-level tabs
# =============================================================================

def _tabs():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "console_tabs", "id": "ctabs", "tabs": [
            {"label": "Collaboration", "children": [
                {"type": "label", "id": "collab_l", "text": "C"}]},
            {"label": "Artifacts", "children": [
                {"type": "label", "id": "art_l", "text": "A"}]},
        ]}]})
    w = next(x for x in comp.root.iter_widgets()
             if isinstance(x, ConsoleTabWidget))
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arrange(Rect(0, 0, 1256, 600))
    return w, ctx, comp


def test_tabs_bus_value_switches_routes_content():
    w, ctx, _ = _tabs()
    assert w.active_index == 0
    assert w.children[0].visible and not w.children[1].visible
    w.on_bus_state("_app/console/tab", "artifacts")
    assert w.active_index == 1
    assert w.children[1].visible and not w.children[0].visible
    w.on_bus_state("_app/console/tab", "collaboration")
    assert w.active_index == 0
    w.on_bus_state("_app/console/tab", 1)          # index form
    assert w.active_index == 1
    w.on_bus_state("_app/console/tab", "bogus")    # unknown -> no-op
    assert w.active_index == 1
    # only the ACTIVE tab's content is drawn (the routing contract)
    c = _RecCanvas(1280, 620)
    w.draw(c, Rect(0, 0, 1256, 600))
    drawn = {t for t, _, _ in c.texts}
    assert "A" in drawn and "C" not in drawn


def test_tabs_tap_publishes_the_seam():
    w, ctx, _ = _tabs()
    c = _RecCanvas(1280, 620)
    w.draw(c, Rect(0, 0, 1256, 600))               # records _seg_bounds
    x0, x1 = w._seg_bounds[1]
    w.on_tap((x0 + x1) / 2, 20)
    assert w.active_index == 1
    assert ("_app/console/tab", "artifacts") in ctx.published


def test_tabs_badge_is_needs_enrich_count():
    w, ctx, _ = _tabs()
    w.on_bus_state("self/deliverables",
                   {"stats": {"needs_enrich": 31, "tracked": 40}})
    assert w._badge_count == 31
    c = _RecCanvas(1280, 620)
    w.draw(c, Rect(0, 0, 1256, 600))
    t = ct.CONSOLE.active
    badge = [f for txt, _, f in c.texts if txt == "31"]
    assert badge == [tuple(t.send_ink)]            # ground ink on warn pill
    # zero backlog -> no pill
    w.on_bus_state("self/deliverables", {"stats": {"needs_enrich": 0}})
    c2 = _RecCanvas(1280, 620)
    w.draw(c2, Rect(0, 0, 1256, 600))
    assert "0" not in {txt for txt, _, _ in c2.texts}


def test_tabs_parser_subscribes_both_seams():
    _, _, comp = _tabs()
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "_app/console/tab" in subbed
    assert "self/deliverables" in subbed


def test_tabs_reuse_tabwidget_routing_not_a_fork():
    from composeio.render.widgets.tab import TabWidget
    assert issubclass(ConsoleTabWidget, TabWidget)
    # base default chrome is untouched (the m2 goldens' contract)
    base = TabWidget("t", labels=["a"])
    assert base.bar_h == 32.0


# =============================================================================
# the scene — additive wiring, frozen seam intact, both tabs render
# =============================================================================

def test_scene_declares_alert_and_tab_namespaces():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    declared = [n.get("name") for n in comp.namespaces]
    for ns in ("deliverable/new", "deliverable/modified",
               "_app/console/tab", "_app/deliverable/open"):
        assert ns in declared, ns
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "deliverable/new" in subbed
    assert "deliverable/modified" in subbed
    assert "_app/console/tab" in subbed
    # FROZEN WIRING: moving collaboration INSIDE the tab changed no binding
    # [DEC-CONSOLE-CHAT-ID-PARAM] the chat leg derives from the scene's ONE
    # chat_id line; the host-pinned telemetry legs stay literal (they do NOT follow it).
    for frozen in ("thread/architect/narration", chat_ns("out"),
                   chat_ns("in"), "self/architect/context_pct",
                   "self/architect/subagents", "self/architect/registered",
                   "self/architect/rate_limit", "self/deliverables"):
        assert frozen in subbed, frozen
    # the seed is a REAL projection: the full `all` list rides the scene
    ns_def = next(n for n in comp.namespaces
                  if n.get("name") == "self/deliverables")
    seeded = ns_def.get("initial", {})
    assert len(seeded.get("all", [])) >= 41
    assert any(r.get("pending") for r in seeded["all"])


def test_scene_tab_tree_hosts_both_surfaces():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    tabs = [w for w in comp.root.iter_widgets()
            if isinstance(w, ConsoleTabWidget)]
    assert len(tabs) == 1
    # [DEC-CANVAS-TAB-RETIRED, David 2026-07-24] the Canvas tab was REMOVED —
    # "basically replaced by doc-viewer".  Collaboration + Artifacts remain
    # pages 0-1; Projects ([DEC-PROJECT-STRUCTURE]) + Configuration
    # ([CXC-IC-META]) slide up.  [CXC-ICP-S5-DASHBOARD] inserts a Dashboard tab.
    assert tabs[0].labels == ["Collaboration", "Artifacts",
                              "Projects", "Dashboard", "Configuration"]
    ids = {w.widget_id for w in comp.root.iter_widgets()}
    # collaboration content + banner on page 0, history on page 1
    assert {"main_row", _th(), _cf(), "deliverable_banner",
            "artifacts_view", "deliverables_panel"} <= ids
    page0_ids = {w.widget_id for w in tabs[0].children[0].iter_widgets()}
    page1_ids = {w.widget_id for w in tabs[0].children[1].iter_widgets()}
    assert "main_row" in page0_ids and "deliverable_banner" in page0_ids
    assert "artifacts_view" in page1_ids
    # the canvas is GONE — no tab hosts it and the widget id is absent entirely
    assert "canvas_panel" not in ids
    assert "canvas_panel" not in page0_ids


def test_scene_headless_render_both_tabs(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    collab = str(tmp_path / "collab.png")
    app.render_headless(collab)
    # the bus seam switches the surface; a re-render shows the other tab
    app.publish("_app/console/tab", "artifacts")
    art = str(tmp_path / "artifacts.png")
    app.render_frame()
    app.canvas.save_png(art)
    app.stop()
    assert os.path.getsize(collab) > 20000
    assert os.path.getsize(art) > 20000
    with open(collab, "rb") as f1, open(art, "rb") as f2:
        assert f1.read() != f2.read()             # genuinely different tabs


def test_banner_overlays_the_thread_and_stays_clickable():
    """[DEC-BANNER-OVERLAY-NOT-SLOT] the banner holds no column space, and
    Open/Dismiss still hit-test.

    This asserts the thing that actually broke: the reverted zero-height
    measure PAINTED fine but went unclickable, because `hit_test_tree` walks
    `arranged_rect`. So test the real scene through a real layout and a real
    hit test — not the widget in isolation, which passed either way.
    """
    from composeio.render.engine.runtime import RenderApp

    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        app.render_frame()
        by_id = {w.widget_id: w for w in app.comp.root.iter_widgets()}
        banner, stage = by_id["deliverable_banner"], by_id["fleet_row"]
        br, tr = banner.arranged_rect, stage.arranged_rect
        assert br is not None and tr is not None

        # OVERLAY, not a slot: the thread's own rect spans the banner's band.
        # The banner overlays the STAGE (the fleet row), not one pane's thread: it is
        # console-self, about the shared ledger, so it cannot belong to a pane
        # ([CXC-FC-SPLIT-RULE]).  It still overlays rather than reserving a slot —
        # [DEC-BANNER-OVERLAY-NOT-SLOT], David on glass: "reserving a 46px by 908px wide
        # space for an occasionally appearing banner doesn't seem like a good use of
        # screen real-estate".
        assert tr.y <= br.y and tr.y + tr.h >= br.y + br.h
        assert br.h > 0                       # a real rect (the revert's lesson)

        cx, cy = br.x + br.w / 2, br.y + br.h / 2

        # idle: taps fall THROUGH to the thread underneath
        assert app.comp.root.hit_test_tree(cx, cy) is not banner

        # live alert: the banner is topmost and takes the tap
        app.publish("deliverable/new",
                    {"path": "docs/x.md", "name": "x.md",
                     "node_id": "DELIV-x", "change": "created"})
        app.render_frame()
        assert banner._alert is not None
        assert app.comp.root.hit_test_tree(cx, cy) is banner
    finally:
        app.stop()


# =============================================================================
# [DEC-INTELLIGENCE-WIDGET-CONSOLIDATION] the consolidated rail — three cards
# + the header avatar replace six rail cards; the picker publishes
# self/model/select; the frozen thread/composer/tab/banner wiring is intact.
# =============================================================================

def test_scene_consolidates_rail_into_three_cards_plus_header_avatar():
    from composeio.render.engine.parser import parse_compose_yaml
    from composeio.render.widgets import (IntelligenceCardWidget,
                                          AccountCardWidget,
                                          DeliverableCardWidget, AvatarWidget)
    comp = parse_compose_yaml(SCENE)
    by_id = {w.widget_id: w for w in comp.root.iter_widgets()}
    # the three consolidated cards, correct types
    assert isinstance(by_id["intelligence_panel"], IntelligenceCardWidget)
    assert isinstance(by_id["account_panel"], AccountCardWidget)
    assert isinstance(by_id["deliverables_panel"], DeliverableCardWidget)
    # Artifacts card: relabelled + jump chip "All" + no leading dot
    art = by_id["deliverables_panel"]
    assert art.label == "Artifacts" and art.chip_label == "All"
    assert art.header_dot is False
    # the avatar STAYS in the header stack (id presence), compact, NOT the rail
    presence = by_id["presence"]
    assert isinstance(presence, AvatarWidget) and presence.compact is True
    rail = by_id["rail_col"]
    rail_ids = {w.widget_id for w in rail.iter_widgets()}
    assert "presence" not in rail_ids
    # the retired cards are ABSENT from rail_col
    for retired in ("context_gauge", "registered_panel", "ledger_panel",
                    "activity_panel", "subagent_panel"):
        assert retired not in rail_ids, retired
    assert {"intelligence_panel", "account_panel",
            "deliverables_panel"} <= rail_ids


def test_scene_frozen_wiring_intact_after_consolidation():
    # assert the binding SET, not the diff ([CXC-ICV-INTENT] frozen seam).
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    subbed = [ns for ns, _ in comp.subscriptions]
    # [CXC-IH-5A] per-instance feeds are id-scoped to self/architect/* + thread/architect/*;
    # intent channels (_app/*), other-daemon feeds (deliverable/*, self/deliverables) + the
    # already-scoped intelligence/architect/* stay bare.
    for frozen in ("thread/architect/narration", chat_ns("out"),
                   chat_ns("in"),
                   _send_ns(), "_app/console/tab",
                   "deliverable/new", "deliverable/modified",
                   "self/architect/context_pct", "self/architect/registered", "self/architect/subagents",
                   "self/architect/rate_limit", "self/architect/compactions",
                   "self/architect/effort", "self/architect/output_tokens", "self/deliverables",
                   "self/architect/avatar/expression"):
        # thread/architect/diagram dropped with the retired Canvas tab
        # ([DEC-CANVAS-TAB-RETIRED], 2026-07-24) — its only consumer is gone.
        assert frozen in subbed, frozen
    # the three consolidation namespaces are declared
    declared = [n.get("name") for n in comp.namespaces]
    for ns in ("self/model/select", "self/account/signin", "_app/model_picker"):
        assert ns in declared, ns
    # the composer field's frozen outbound publish is byte-exact
    field = next(w for w in comp.root.iter_widgets()
                 if w.widget_id == _cf())
    assert field.bound_namespace == chat_ns("compose")
    # the model picker subscribes its open seam (Change publishes it)
    picker = next(w for w in comp.root.iter_widgets()
                  if w.widget_id == "model_picker")
    assert ("_app/model_picker", picker) in comp.subscriptions


def test_intelligence_card_change_opens_picker():
    from composeio.render.widgets import IntelligenceCardWidget
    card = IntelligenceCardWidget("ic")
    ctx = _Ctx()
    card.on_mount(ctx)
    card.arranged_rect = Rect(0, 0, 332, 640)
    card.on_bus_state("self/effort", {"level": "high"})
    c = _RecCanvas(360, 700)
    card.draw(c, Rect(0, 0, 332, 640))           # records the Change rect
    cr = card._change_rect
    assert cr is not None
    card.on_tap(cr[0] + cr[2] / 2, cr[1] + cr[3] / 2)
    assert ("_app/model_picker", "on") in ctx.published


def test_model_picker_apply_publishes_select_and_closes():
    from composeio.render.widgets import ModelPickerWidget
    pk = ModelPickerWidget("pk")
    ctx = _Ctx()
    pk.on_mount(ctx)
    pk.arrange(Rect(0, 0, 1256, 1540))
    pk.on_bus_state("_app/model_picker", "on")   # the Change chip opens it
    assert pk.visible
    c = _RecCanvas(1300, 1560)
    pk.draw(c, Rect(0, 0, 1256, 1540))           # records option/apply rects
    fr = next(r for r, cmd in pk._model_rects if cmd == "fable")
    pk.on_tap(fr.x + fr.w / 2, fr.y + fr.h / 2)
    er = next(r for r, lvl in pk._effort_rects if lvl == "max")
    pk.on_tap(er.x + er.w / 2, er.y + er.h / 2)
    pk.draw(c, Rect(0, 0, 1256, 1540))           # re-record after selection
    ar = pk._apply_rect
    pk.on_tap(ar.x + ar.w / 2, ar.y + ar.h / 2)
    sel = [v for ns, v in ctx.published if ns == "self/model/select"]
    assert len(sel) == 1
    assert sel[0]["model"] == "Fable 5" and sel[0]["effort"] == "max"
    assert pk.visible is False                    # applied + closed
    # Apply publishes the intent and STOPS — no launcher/actuation namespace
    assert all(ns in ("self/model/select", "_app/model_picker")
               for ns, _ in ctx.published)


def test_account_card_signin_publishes_stub_intent():
    from composeio.render.widgets import AccountCardWidget
    w = AccountCardWidget("ac")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = Rect(0, 0, 332, 180)
    w.on_bus_state("self/rate_limit",
                   {"5h": {"pct": 38, "resets": "14:22"}, "7d": {"pct": 12}})
    c = _RecCanvas(360, 220)
    w.draw(c, Rect(0, 0, 332, 180))
    drawn = {t for t, _, _ in c.texts}
    assert "38%" in drawn and "12%" in drawn      # both windowed bars
    br = w._btn_rect
    w.on_tap(br[0] + br[2] / 2, br[1] + br[3] / 2)
    assert ("self/account/signin", "on") in ctx.published


def test_intelligence_card_registered_collapses_and_gauge_is_reused():
    from composeio.render.widgets import IntelligenceCardWidget
    w = IntelligenceCardWidget("ic")
    ctx = _Ctx()
    w.on_mount(ctx)
    # all-ok -> collapsed single row
    w.on_bus_state("self/registered", {"items": [
        {"name": "a", "ok": True}, {"name": "b", "ok": True},
        {"name": "c", "ok": True}, {"name": "d", "ok": True}]})
    assert w._reg_state() == (4, 4, True, False)
    # NOT all-ok -> auto-expands to the per-item checklist
    w.on_bus_state("self/registered", {"items": [
        {"name": "a", "ok": True}, {"name": "b", "ok": False}]})
    _, _, all_ok, expanded = w._reg_state()
    assert all_ok is False and expanded is True
    # the gauge is the shipping widget, reused by reference (bare chrome)
    w.on_bus_state("self/context_pct", {"pct": 72})
    assert w._gauge.value == 72 and w._gauge.chrome == "bare"
    assert w._gauge.style == "semicircle"
    # compaction count rides the title
    w.on_bus_state("self/compactions", {"count": 14})
    assert w._compactions == 14


def test_intelligence_card_usage_formatter():
    from composeio.render.widgets.intelligence_card import _fmt_tokens_out
    assert _fmt_tokens_out(2381000) == "2.381M tokens out"
    assert _fmt_tokens_out({"total": 63200}) == "63.2k tokens out"
    assert _fmt_tokens_out(800) == "800 tokens out"
    assert _fmt_tokens_out(None) == ""


def test_intelligence_card_memory_gauge_renders_and_colors_by_status():
    # [DEC-MEMORY-BLOAT-MONITOR] the automemory bar seated above the % readout:
    # it ingests self/memory, draws the pct, and warms by status.
    from composeio.render.widgets import IntelligenceCardWidget
    w = IntelligenceCardWidget("ic")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = Rect(0, 0, 332, 700)

    # ── ok/calm: pct ingested, green hue ──
    w.on_bus_state("self/memory",
                   {"bytes": 12000, "files": 8, "entries": 24,
                    "budget_bytes": 50000, "pct": 24, "status": "ok"})
    assert w._mem_pct == 24 and w._mem_status == "ok"
    assert w._mem_color(ct.CONSOLE.active) == ct.CONSOLE.active.ok
    c = _RecCanvas(360, 760)
    w.draw(c, Rect(0, 0, 332, 700))
    drawn = {txt: fill for txt, _, fill in c.texts}
    assert "24%" in drawn                              # pct label rendered
    assert drawn["24%"] == ct.CONSOLE.active.ok        # calm/green

    # ── bloated: the pct readout warms to crit (red) ──
    w.on_bus_state("self/memory", {"pct": 92, "status": "bloated"})
    assert w._mem_color(ct.CONSOLE.active) == ct.CONSOLE.active.crit
    c2 = _RecCanvas(360, 760)
    w.draw(c2, Rect(0, 0, 332, 700))
    drawn2 = {txt: fill for txt, _, fill in c2.texts}
    assert "92%" in drawn2
    assert drawn2["92%"] == ct.CONSOLE.active.crit     # red

    # ── warn: amber ──
    w.on_bus_state("self/memory", {"pct": 70, "status": "warn"})
    assert w._mem_color(ct.CONSOLE.active) == ct.CONSOLE.active.warn

    # ── the memory strip does NOT displace the gauge/rows off the card:
    #    the context gauge still ingests + draws (frozen sibling intact) ──
    w.on_bus_state("self/context_pct", {"pct": 55})
    assert w._gauge.value == 55 and w._gauge.chrome == "bare"

    # ── before any publish: renders a placeholder ("—"), never raises ──
    fresh = IntelligenceCardWidget("ic2")
    fresh.on_mount(_Ctx())
    fresh.arranged_rect = Rect(0, 0, 332, 700)
    c3 = _RecCanvas(360, 760)
    fresh.draw(c3, Rect(0, 0, 332, 700))
    assert "—" in {txt for txt, _, _ in c3.texts}


# =============================================================================
# [CXC-ICV-SUBAGENTS] the CHIP pile-up fix: the console renders subagent CHIPS
# via intelligence_card (_ingest_subagent / _chips), NOT subagent_ledger, so the
# proven linger-then-remove prune had to be ported here too.  These pin (a) the
# count bound, (b) linger-on-clock-tick removal, (c) the running-staleness
# backstop, (d) the scene no longer seeding a permanent running phantom chip.
# =============================================================================

def test_intelligence_card_done_chips_bounded_to_keep():
    """(a) A burst of done chips never piles: at most _CHIP_DONE_KEEP (2)
    survive on ingest, the most recent — the pile-up David photographed."""
    from composeio.render.widgets import IntelligenceCardWidget
    from composeio.render.widgets.intelligence_card import _CHIP_DONE_KEEP
    w = IntelligenceCardWidget("ic")
    w.on_mount(_Ctx())
    for i in range(12):
        w.on_bus_state("self/subagents",
                       {"type": "gp", "status": "done", "id": f"d{i}",
                        "dur_s": 3, "tokens": 5000})
    done = [cid for cid, c in w._chips.items() if not c.running]
    assert len(w._chips) <= _CHIP_DONE_KEEP == 2
    assert set(done) == {"d10", "d11"}          # the two most recent survive


def test_intelligence_card_done_chip_pruned_after_linger_on_tick():
    """(b) A running->done chip lingers (visibly resolved) then is REMOVED on a
    clock tick once the linger elapses, even with no further bus traffic."""
    from composeio.render.widgets import IntelligenceCardWidget
    from composeio.render.widgets.intelligence_card import _CHIP_LINGER_S
    w = IntelligenceCardWidget("ic")
    w.on_mount(_Ctx())
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "a"})
    assert w._chips["a"].running is True and w._chips["a"].done_at is None
    w.on_bus_state("self/subagents",
                   {"status": "done", "id": "a", "dur_s": 12, "tokens": 8000})
    chip = w._chips["a"]
    assert chip.running is False and chip.done_at is not None
    # still lingering: kept, and the tick demands no repaint
    assert w.on_clock_tick(chip.done_at + 1.0) is False
    assert "a" in w._chips
    # linger elapsed: pruned AND the tick returns True so the frame repaints
    assert w.on_clock_tick(chip.done_at + _CHIP_LINGER_S + 0.01) is True
    assert "a" not in w._chips


def test_intelligence_card_stale_running_chip_pruned():
    """(c) A chip stuck ``running`` (a dropped 'done' or a seed) whose last
    update is older than the stale backstop is removed — no permanent phantom."""
    from composeio.render.widgets import IntelligenceCardWidget
    from composeio.render.widgets.intelligence_card import _CHIP_STALE_S
    w = IntelligenceCardWidget("ic")
    w.on_mount(_Ctx())
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "phantom"})
    chip = w._chips["phantom"]
    assert chip.running is True
    # a fresh running chip is kept forever within the window
    assert w.on_clock_tick(chip.seen + 60.0) is False
    assert "phantom" in w._chips
    # beyond the stale window: the phantom is swept
    assert w.on_clock_tick(chip.seen + _CHIP_STALE_S + 1.0) is True
    assert "phantom" not in w._chips


def test_intelligence_card_redispatch_cancels_linger():
    """A chip that goes done then is re-dispatched clears its linger and is a
    running chip again (never pruned by the time bound)."""
    from composeio.render.widgets import IntelligenceCardWidget
    w = IntelligenceCardWidget("ic")
    w.on_mount(_Ctx())
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "done", "id": "z", "dur_s": 2})
    w.on_bus_state("self/subagents",
                   {"type": "gp", "status": "dispatched", "id": "z"})
    chip = w._chips["z"]
    assert chip.running is True and chip.done_at is None
    assert w.on_clock_tick(chip.seen + 100.0) is False
    assert "z" in w._chips


def test_console_scene_subagents_seed_is_not_running_phantom():
    """(d) The console scene MUST NOT seed a live running subagent: such a seed
    has no matching 'done' and wedged as a permanent phantom chip.  The seed is
    ingested as a resolved (done) chip that the linger prune can sweep."""
    import yaml
    from composeio.render.widgets import IntelligenceCardWidget
    scene = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                         "console.compose.yaml")
    with open(scene) as fh:
        doc = yaml.safe_load(fh)
    seed = None
    for ns in doc.get("namespaces", []):
        if ns.get("name") == "self/architect/subagents":
            seed = ns.get("initial")
    assert seed is not None, "self/subagents namespace missing"
    assert str(seed.get("status", "")).lower() != "dispatched"
    # ingested, it is a NON-running chip (never a permanent phantom)
    w = IntelligenceCardWidget("ic")
    w.on_mount(_Ctx())
    w.on_bus_state("self/subagents", seed)
    chips = list(w._chips.values())
    assert len(chips) == 1 and chips[0].running is False
