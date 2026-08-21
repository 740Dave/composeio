"""
Colonial-console CIC SHELL compliance ([SPEC:CXC-CIC] [CXC-CIC-VERIFY]).

Covers the machine gates for the SHELL build (chrome + nav + theme + the DRADIS
drop-in + placeholder content):

  CIC-C1   the layer stack composites in z-order; the amber frame MASKS loose
           content (a pixel probe outside the screen-area cutout is frame-amber,
           not content-green)
  CIC-C2   selecting a console swaps BOTH the sub-tab row AND the content, and
           resets the sub-tab to 0
  CIC-C3   touch, rotary, and Intelligence drive selection IDENTICALLY (a direct
           index publish == a tab tap outcome)
  CIC-C4   CIC state is station-scoped; two stations never cross-wire
  CIC-C11  ONE generalized tab-row, instanced twice with the ratified fills
           (row2 pill-overlay @0.70, row3 filled-background @1.00), tap publishes
           the station-scoped index, indicator derives from the published index
  CIC-C12  `theme` is a SEMANTIC binding resolving to a base/highlight palette;
           unbound -> cic amber; a direct color override still wins

Plus: the menu file is the SOURCE OF TRUTH — a renamed/added label in a fixture
menu re-drives the tabs with no code change ([CXC-CIC-NAV]); out-of-range
selection clamps ([CXC-CIC-NAV]); the DRADIS console drops in / placeholders
elsewhere ([CXC-CIC-CONTENT]).
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
# [CXC-PROJECT-WIDGETS] the colonial_console cluster is now project-local to BSG;
# the repo root must be importable to reach compose_projects.bsg.widgets.
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from compose_projects.bsg.widgets.cic.menu import parse_menu
from compose_projects.bsg.widgets.cic.theme import resolve_theme, BUILTIN_THEMES
from composeio.render.engine.parser import parse_widget_tree
from composeio.render.engine.runtime import RenderApp
from composeio.render.widgets.segmented_tab_row import (
    SegmentedTabRowWidget, FILLED_BACKGROUND, PILL_OVERLAY)
from compose_projects.bsg.widgets.colonial_console import (
    ColonialConsoleWidget, compute_geometry)
from composeio.render.widget import Rect

FIXTURE_MENU = """\
* HELM
    * WAYPOINT
    * HEADING
    * TRACK
    * VELOCITY
    * FUEL
* NAVCON
    * CARTOGRAPHY
    * PLOT
    * TAC
* FTL
    * PRM OUTPUT
    * GENERATOR
    * PROXIMITY
    * ALERTS
    * LOG
"""


def _scene(station="cic.1.helm", theme="cic", **extra):
    console = {"type": "colonial_console", "id": "cic",
               "station": station, "theme": theme, "menu": FIXTURE_MENU}
    console.update(extra)
    return {"app": {"id": "t"},
            "design": {"title": "T", "width": 1280, "height": 960,
                       "padding": 0, "gap": 0},
            "widgets": [console]}


def _app(scene):
    comp = parse_widget_tree(scene)
    app = RenderApp(comp, headless=False, windowed=False,
                    connect_transports=False)
    app.start()
    return app, comp, comp.root.children[0]


# ---------------------------------------------------------------------------
# menu parser — the source of truth for the tab labels
# ---------------------------------------------------------------------------

def test_menu_parses_the_tracked_bsg_file():
    m = parse_menu()   # the tracked default
    assert m.console_keys == ["HELM", "NAVCON", "FTL", "DC",
                              "FIRE CONTROL", "EMSO", "SECURITY"]
    assert m.subtabs_for(0) == ["WAYPOINT", "HEADING", "TRACK",
                                "VELOCITY", "FUEL"]
    assert m.subtabs_for(2) == ["PRM OUTPUT", "GENERATOR", "PROXIMITY",
                                "ALERTS", "LOG"]
    assert not m.warnings


def _force_store_offline(monkeypatch):
    """Point the bus store at a fast-fail endpoint so resolve_* raises promptly —
    simulates a store-unreachable surface (the 'bad COMPOSEIO_STORAGE_URL' gate).
    resolve_bytes reads the module-level URL at call time, so setattr is enough."""
    import composeio.resolve as _r
    monkeypatch.setattr(_r, "BUS_STORAGE_URL", "http://127.0.0.1:9")


def _serve_screens_locally(monkeypatch):
    """Point resolve_bytes at the local screens dir for the EXT rotation images
    (bus://assets/cic/screens/*), and OFFLINE for everything else — so the SVG
    docs fall back to their committed bundles while the referenced image resolves
    deterministically (the store may not carry the screens yet).  Mirrors the
    render-time bus-asset resolve ([CXC-CIC-RADIO-MODE] Fix 1)."""
    import composeio.resolve as _r
    from compose_projects.bsg.widgets.cic import svg_art
    from composeio.render.svg import canvas_renderer
    canvas_renderer._IMAGE_HANDLE_CACHE.clear()
    screens = os.path.join(REPO_ROOT, "compose_projects", "bsg", "resources",
                           "screens")

    def _fake(uri, **kw):
        if uri.startswith(svg_art.SCREENS_URI_PREFIX):
            name = uri[len(svg_art.SCREENS_URI_PREFIX):]
            with open(os.path.join(screens, name), "rb") as fh:
                return fh.read()
        raise ConnectionError(f"offline for {uri}")
    monkeypatch.setattr(_r, "resolve_bytes", _fake)


def test_menu_falls_back_to_bundled_when_authoring_file_absent(monkeypatch):
    """Deploy-gap regression ([CXC-CIC-NAV], rpi5 2026-07-31): when the store is
    unreachable AND the authoring source-of-truth is not present at runtime (a
    file-copy deploy that shipped the python package but not compose_projects/),
    parse_menu(None) must still populate the nav from the package-BUNDLED fallback
    — never an empty menu that blanks the row2/row3 tab rows."""
    from compose_projects.bsg.widgets.cic import menu as menu_mod
    _force_store_offline(monkeypatch)   # bus first, but store is down
    # simulate the authoring file being un-deployed (point it at a missing path)
    monkeypatch.setattr(menu_mod, "DEFAULT_MENU_PATH",
                        "/nonexistent/cic_menu_structure.md")
    m = parse_menu()   # None -> store down -> authoring absent -> bundled fallback
    assert m.console_keys == ["HELM", "NAVCON", "FTL", "DC",
                              "FIRE CONTROL", "EMSO", "SECURITY"]
    assert m.subtabs_for(0) == ["WAYPOINT", "HEADING", "TRACK",
                                "VELOCITY", "FUEL"]
    assert not m.warnings
    assert os.path.exists(menu_mod.BUNDLED_MENU_PATH)


def test_menu_resolves_from_bus_when_store_reachable(monkeypatch):
    """Bus-native: parse_menu(None) resolves the nav from the store (MENU_URI)
    and it WINS over the local authoring file — the store is the source of truth
    (the DRADIS-console model)."""
    import composeio.resolve as _r
    fixture = "* BUSHELM\n    * BUSWAY\n* BUSNAV\n    * BUSPLOT\n"
    monkeypatch.setattr(_r, "resolve_text", lambda uri, **kw: fixture)
    m = parse_menu()   # None -> bus
    assert m.console_keys == ["BUSHELM", "BUSNAV"]
    assert m.subtabs_for(0) == ["BUSWAY"]


def test_menu_explicit_source_wins_over_bus(monkeypatch):
    """An explicitly-passed menu (a scene's ``menu:``) still wins — the bus is
    NOT consulted (resolve is wired to raise; no exception surfaces)."""
    import composeio.resolve as _r

    def _boom(*a, **k):
        raise AssertionError("bus must not be consulted for an explicit menu")
    monkeypatch.setattr(_r, "resolve_text", _boom)
    m = parse_menu(FIXTURE_MENU)
    assert m.console_keys == ["HELM", "NAVCON", "FTL"]


def test_menu_edit_redrives_labels_no_code_change():
    """Rename/add in a fixture menu -> the tab RENDERS the new label
    ([CXC-CIC-NAV] the menu file is the source of truth)."""
    edited = FIXTURE_MENU.replace("HEADING", "PLANETARY SURVEY")
    edited += "* DRADIS\n    * SCAN\n    * CONTACTS\n"
    m = parse_menu(edited)
    assert "PLANETARY SURVEY" in m.subtabs_for(0)
    assert m.console_keys[-1] == "DRADIS"
    assert m.subtabs_for(3) == ["SCAN", "CONTACTS"]
    # and the SHELL renders those labels with no code change (the menu string
    # drives the tab widgets directly)
    scene = _scene()
    scene["widgets"][0]["menu"] = edited
    app, _, cic = _app(scene)
    assert "PLANETARY SURVEY" in cic.row3.labels
    assert cic.row2.labels[-1] == "DRADIS"
    app.stop()


def test_menu_clamps_out_of_range():
    m = parse_menu(FIXTURE_MENU)
    assert m.clamp_console(99) == 2          # 3 consoles -> max index 2
    assert m.clamp_console(-4) == 0
    assert m.clamp_subtab(0, 12) == 4        # 5 sub-tabs -> max index 4


def test_menu_unparented_subtab_preserved_and_flagged():
    m = parse_menu("    * ORPHAN\n* REAL\n    * A\n")
    assert m.console_keys == ["REAL"]
    assert any("ORPHAN" in w for w in m.warnings)   # drop-nothing


# ---------------------------------------------------------------------------
# CIC-C12 — semantic theme resolves to a palette; unbound -> cic; override wins
# ---------------------------------------------------------------------------

def test_theme_cic_default_amber():
    base, hi, name = resolve_theme("cic")
    assert base == (0x76, 0x24, 0x00, 0xFF)
    assert hi == (0xCD, 0x67, 0x1D, 0xFF)
    assert name == "cic"


def test_theme_unbound_defaults_to_cic():
    base, hi, name = resolve_theme(None)
    assert name == "cic" and base == (0x76, 0x24, 0x00, 0xFF)


def test_theme_semantic_sickbay_resolves_through_one_path():
    base, hi, name = resolve_theme("sickbay")
    assert name == "sickbay"
    assert base == (0x3E, 0x56, 0x6B, 0xFF)          # David's ratified sickbay (not amber)
    # SAME class renders it — the shell does not fork per theme
    app, comp, cic = _app(_scene(theme="sickbay"))
    assert isinstance(cic, ColonialConsoleWidget)
    assert cic.base_color == (0x3E, 0x56, 0x6B, 0xFF)
    assert cic.theme_name == "sickbay"
    app.stop()


def test_theme_unknown_falls_back_to_cic():
    _, _, name = resolve_theme("zzz-nope")
    assert name == "cic"


def test_theme_raw_override_wins():
    base, hi, name = resolve_theme("cic", base_override="#112233",
                                   highlight_override="#445566")
    assert base == (0x11, 0x22, 0x33, 0xFF)
    assert hi == (0x44, 0x55, 0x66, 0xFF)
    app, comp, cic = _app(_scene(base_color="#112233",
                                 highlight_color="#445566"))
    assert cic.base_color == (0x11, 0x22, 0x33, 0xFF)
    app.stop()


# ---------------------------------------------------------------------------
# CIC-C11 — ONE tab-row, instanced twice with the ratified fills
# ---------------------------------------------------------------------------

def test_tab_rows_are_one_class_two_indicator_styles():
    app, comp, cic = _app(_scene())
    assert type(cic.row2) is SegmentedTabRowWidget   # SAME class (no fork)
    assert type(cic.row3) is SegmentedTabRowWidget
    assert cic.row2.selected_indicator == PILL_OVERLAY
    assert cic.row3.selected_indicator == FILLED_BACKGROUND
    assert cic.row2.fill_opacity == pytest.approx(0.70)   # #333 @70%
    assert cic.row3.fill_opacity == pytest.approx(1.00)   # #333 @100%
    assert cic.row2.fill_color[:3] == (0x33, 0x33, 0x33)
    assert cic.row3.fill_color[:3] == (0x33, 0x33, 0x33)
    app.stop()


def test_tab_tap_publishes_station_scoped_index():
    app, comp, cic = _app(_scene(station="cic.1.nav"))
    got = []
    cic.row2.publish = lambda ns, v: got.append((ns, v))   # capture publishes
    # simulate a tap on segment 2 (FTL) — the row must have laid out its segs
    cic.row2._seg_bounds = [(i * 100.0, (i + 1) * 100.0)
                            for i in range(len(cic.row2.labels))]
    cic.row2.on_tap(250.0, 10.0)
    assert got == [("colonial_console/cic.1.nav/console",
                    {"index": 2, "key": "FTL"})]
    app.stop()


def test_indicator_derives_from_published_index_not_tap_state():
    """A rotary/Intelligence publish (no tap) moves the indicator identically
    ([CXC-CIC-CREDO])."""
    row = SegmentedTabRowWidget("r", labels=["A", "B", "C"],
                                selected_indicator=PILL_OVERLAY,
                                index_namespace="ns/x")
    row.on_bus_state("ns/x", {"index": 2})
    assert row.active_index == 2
    row.on_bus_state("ns/x", 1)           # bare int
    assert row.active_index == 1
    row.on_bus_state("ns/x", {"key": "A"})   # by key
    assert row.active_index == 0


# ---------------------------------------------------------------------------
# CIC-C2 — selecting a console swaps sub-tab row + content + resets sub-tab
# ---------------------------------------------------------------------------

def test_console_select_swaps_subtabs_content_and_resets():
    app, comp, cic = _app(_scene())
    # pick sub-tab 3
    app.publish("colonial_console/cic.1.helm/subtab", {"index": 3}, source="t")
    app.drain_updates()
    assert cic.subtab_index == 3
    # select FTL (index 2)
    app.publish("colonial_console/cic.1.helm/console", {"index": 2}, source="t")
    app.drain_updates()
    assert cic.console_index == 2
    assert cic.row3.labels == ["PRM OUTPUT", "GENERATOR", "PROXIMITY",
                               "ALERTS", "LOG"]         # sub-tab row swapped
    assert cic.subtab_index == 0                        # reset to default
    assert cic.row2.active_index == 2                   # row2 pill on FTL
    app.stop()


def test_console_select_clamps_out_of_range():
    app, comp, cic = _app(_scene())
    app.publish("colonial_console/cic.1.helm/console", {"index": 99}, source="t")
    app.drain_updates()
    assert cic.console_index == 2      # 3-console fixture -> clamps to 2
    app.stop()


# ---------------------------------------------------------------------------
# CIC-C3 — touch / rotary / Intelligence drive selection IDENTICALLY
# ---------------------------------------------------------------------------

def test_direct_publish_matches_tap_outcome():
    ns = "colonial_console/cic.1.helm/console"
    # (a) direct index publish (rotary / Intelligence)
    app_a, _, cic_a = _app(_scene())
    app_a.publish(ns, {"index": 1}, source="intelligence")
    app_a.drain_updates()
    # (b) a tab tap on row2 segment 1 (NAVCON)
    app_b, _, cic_b = _app(_scene())
    cic_b.row2._seg_bounds = [(i * 100.0, (i + 1) * 100.0)
                              for i in range(len(cic_b.row2.labels))]
    cic_b.row2.on_tap(150.0, 10.0)          # publishes index 1
    app_b.drain_updates()
    # byte-identical shell state
    assert cic_a.console_index == cic_b.console_index == 1
    assert cic_a.row3.labels == cic_b.row3.labels == \
        ["CARTOGRAPHY", "PLOT", "TAC"]
    assert cic_a.content_router.active_mode == cic_b.content_router.active_mode
    app_a.stop(); app_b.stop()


# ---------------------------------------------------------------------------
# CIC-C4 — station-scoped; two stations never cross-wire
# ---------------------------------------------------------------------------

def test_two_stations_do_not_cross_wire():
    scene = {"app": {"id": "t"},
             "design": {"title": "T", "width": 1280, "height": 960,
                        "padding": 0, "gap": 0},
             "widgets": [
                 {"type": "colonial_console", "id": "helm",
                  "station": "cic.1.helm", "menu": FIXTURE_MENU},
                 {"type": "colonial_console", "id": "nav",
                  "station": "cic.1.nav", "menu": FIXTURE_MENU}]}
    comp = parse_widget_tree(scene)
    app = RenderApp(comp, headless=False, windowed=False,
                    connect_transports=False)
    app.start()
    helm = next(w for w in comp.root.iter_widgets()
                if getattr(w, "widget_id", "") == "helm")
    nav = next(w for w in comp.root.iter_widgets()
               if getattr(w, "widget_id", "") == "nav")
    app.publish("colonial_console/cic.1.helm/console", {"index": 2}, source="t")
    app.drain_updates()
    assert helm.console_index == 2      # helm reacted
    assert nav.console_index == 0       # nav untouched (its ns did not match)
    app.stop()


# ---------------------------------------------------------------------------
# CIC-C1 — layer stack z-order + the amber frame MASKS loose content
# ---------------------------------------------------------------------------

def test_layer_stack_z_order():
    app, comp, cic = _app(_scene())
    order = [c.widget_type for c in cic.children]
    # [CXC-CIC-WIDGET] z-order — each SVG group at its OWN SVG z: screen-area is
    # the black MATTE drawn OVER the content (David's ext-view-under-screen-area
    # ordering), so its opaque plate occludes content overflow by LAYERING; the
    # amber frame mask paints last over that, the dialog is topmost
    # ([DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]).
    assert order == ["rectangle", "cic_content", "cic_edge_gauges",
                     "cic_screen_area", "cic_frame", "segmented_tab_row",
                     "segmented_tab_row", "cic_status_bar", "cic_button_bar",
                     "radio_button_group", "cic_dialog"]
    # screen-area now renders OVER the content (the matte occludes overflow)
    assert order.index("cic_screen_area") > order.index("cic_content")
    # the amber frame mask stays ABOVE the content (and above the screen-area matte)
    assert order.index("cic_frame") > order.index("cic_content")
    assert order.index("cic_frame") > order.index("cic_screen_area")
    # the dialog is hidden until a power press (drawn only when active)
    assert cic.dialog.visible is False
    app.stop()


def test_edge_gauges_reserved_present():
    app, comp, cic = _app(_scene())
    # the three reserved edge gauges render as an (empty) layer so a later
    # screen that fills one does not shift the stack ([CXC-CIC-WIDGET]).
    assert cic.edge_gauges in cic.children
    app.stop()


def test_frame_masks_loose_content():
    """A pixel probe in the amber frame ring is FRAME-amber, not content-green,
    even though the content layer overspills the screen-area cutout
    ([CXC-CIC-VERIFY] CIC-C1)."""
    from PIL import Image
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "shell.png")
        app.render_headless(out)
        img = Image.open(out).convert("RGB")
        g = compute_geometry(1280, 960)
        # a point inside the LEFT amber band (outside the screen-area cutout)
        px = img.getpixel((int(g.frame_t / 2), int(g.chrome_h + 200)))
        # amber base ~ (0x76,0x24,0x00): red dominant, green/blue low
        assert px[0] > 90 and px[1] < 90 and px[2] < 60, px
        # a point in the CENTER of the screen-area is content (dark green /
        # dradis), NOT amber (red is not dominant)
        cx = int(g.screen_area.x + g.screen_area.w / 2)
        cy = int(g.screen_area.y + g.screen_area.h / 2)
        cpx = img.getpixel((cx, cy))
        assert not (cpx[0] > 90 and cpx[1] < 90 and cpx[2] < 60), cpx
    app.stop()


# ---------------------------------------------------------------------------
# EXACT master-mask artwork ([CXC-CIC-SVG-MAP]: standard_ux_shape_element) —
# the frame is David's SVG shape, not a hand-drawn approximation
# ---------------------------------------------------------------------------

def test_frame_renders_exact_svg_artwork():
    """The frame layer loads the EXACT chrome SVG and renders the
    ``standard_ux_shape_element`` master shape + ``decorators``, with a design box
    derived from the frame's own 4:3 bbox ([CXC-CIC-SVG-MAP]).  ``screen-area`` is
    NO LONGER in the frame — it renders at its TRUE z as the back layer
    (:class:`_ScreenArtWidget`)."""
    from compose_projects.bsg.widgets.cic import svg_art
    app, comp, cic = _app(_scene())
    frame = cic.frame
    assert frame._doc is not None                 # artwork loaded (not fallback)
    assert len(frame._nodes) >= 1                  # standard_ux (+ decorators)
    # design box is the frame's own bbox, wherever David places it (his latest
    # export insets the artwork, so it is NOT 0,0-origin — the mapping subtracts
    # the origin, so any placement is fine); the frame stays ~4:3.
    dx, dy, dw, dh = frame._design
    assert abs((dw / dh) - (4.0 / 3.0)) < 0.02     # ~4:3
    # the extracted frame element is present in the source document
    assert svg_art.find_element(frame._doc,
                                "standard_ux_shape_element") is not None
    # screen-area is the SEPARATE back layer now (behind the content)
    assert cic.screen_area._node is not None
    app.stop()


def test_frame_master_mask_transparency_probe():
    """CIC-C1 with the EXACT art: content drawn UNDER the frame shows through the
    transparent screen hole; the amber shape occludes content that bleeds into
    the frame ring (the master-mask transparency, proven by a pixel probe)."""
    from PIL import Image
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "shell.png")
        app.render_headless(out)
        img = Image.open(out).convert("RGB")

        def is_amber(px):
            return px[0] > 90 and px[1] < 90 and px[2] < 60

        # left frame edge (design x~3 of 338 -> the amber outer border): AMBER
        assert is_amber(img.getpixel((6, 480))), img.getpixel((6, 480))
        # the screen center is content (dradis/green), NOT amber
        assert not is_amber(img.getpixel((640, 560))), img.getpixel((640, 560))
        # bottom-left BEVELED corner is amber (the exact shape bevels the
        # corner; a plain rect frame would too, but the bevel proves the art
        # renders): probe just inside the bottom-left
        assert is_amber(img.getpixel((20, 900))), img.getpixel((20, 900))
    app.stop()


def test_frame_theme_recolors_exact_shape_one_path():
    """A re-theme recolors the SAME exact SVG shape through ONE path — the amber
    frame renders in the theme's base color, no per-theme fork ([CXC-CIC-WIDGET],
    CIC-C12)."""
    from PIL import Image
    app, comp, cic = _app(_scene(theme="sickbay"))   # base #3E566B (blue-grey)
    assert cic.base_color == (0x3E, 0x56, 0x6B, 0xFF)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "sick.png")
        app.render_headless(out)
        img = Image.open(out).convert("RGB")
        # the left frame edge is now the sickbay base (blue-dominant), NOT amber
        px = img.getpixel((6, 480))
        assert px[2] > px[0] and px[2] > 40, px       # blue channel dominant
    app.stop()


# ---------------------------------------------------------------------------
# EXACT tab artwork ([CXC-CIC-TABROW] [DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]):
# row3 tiles ``row3-tab1-artwork`` per tab (#cccccc text, from the SVG), the
# SELECTED tab's artwork recolored #333333; row2's selected pill =
# ``row2-tab-selected-artwork`` @70%; the status band =
# ``row3-tab-status-bar-artwork`` + dynamic text.
# ---------------------------------------------------------------------------

def _has_fill_333(node) -> bool:
    found = []

    def walk(n):
        st = (n.attrs.get("style") or "").replace(" ", "")
        if "fill:#333333" in st or n.attrs.get("fill", "").lower() == "#333333":
            found.append(1)
        for c in n.children:
            walk(c)
    walk(node)
    return bool(found)


def test_row3_tiles_tab_artwork_white_text():
    app, comp, cic = _app(_scene())
    row3 = cic.row3
    assert row3.selected_indicator == FILLED_BACKGROUND
    assert row3._has_tab_art()                    # renders David's art, not rects
    # the SELECTED-tab template is recolored to #333333; the base is NOT
    assert _has_fill_333(row3._art_template_selected)
    assert not _has_fill_333(row3._art_template_base)
    app.stop()


def test_row3_selected_333_artwork_follows_published_index():
    """The #333333-recolored tab artwork is rendered for exactly the ACTIVE slot,
    and follows the station-scoped published sub-tab index ([CXC-CIC-TABROW])."""
    # the promoted tab-row widget renders injected artwork through the ENGINE's
    # render_element (bound as a module global in segmented_tab_row); spy there.
    # tab rows are STD-owned ([CXC-COMP-MODE-SURFACE]) -> select STD so they show.
    import composeio.render.widgets.segmented_tab_row as _seg
    app, comp, cic = _app(_scene(radio_selected=1))
    row3 = cic.row3
    calls = []
    orig = _seg.render_element

    def spy(canvas, doc, node, rect, design, fit="stretch"):
        calls.append(node)
        return orig(canvas, doc, node, rect, design, fit)

    _seg.render_element = spy
    try:
        with tempfile.TemporaryDirectory() as d:
            app.render_headless(os.path.join(d, "a.png"))
        # exactly one slot uses the selected (#333) artwork; the rest use base
        assert sum(1 for n in calls if n is row3._art_template_selected) == 1
        assert sum(1 for n in calls if n is row3._art_template_base) == \
            len(row3.labels) - 1
        # move the selection -> the selected artwork follows (still exactly one)
        calls.clear()
        app.publish("colonial_console/cic.1.helm/subtab", {"index": 3},
                    source="t")
        app.drain_updates()
        with tempfile.TemporaryDirectory() as d:
            app.render_headless(os.path.join(d, "b.png"))
        assert row3.active_index == 3
        assert sum(1 for n in calls if n is row3._art_template_selected) == 1
    finally:
        _seg.render_element = orig
    app.stop()


def test_row2_selected_pill_uses_svg_artwork_333_at_70():
    app, comp, cic = _app(_scene())
    row2 = cic.row2
    assert row2.selected_indicator == PILL_OVERLAY
    assert row2._has_pill_art()                   # David's pill, not a round-rect
    assert _has_fill_333(row2._art_pill_node)     # #333333 fill
    assert row2._art_pill_node.attrs.get("opacity") == "0.7000"   # @70%
    app.stop()


def test_row2_tab_background_renders_behind_labels_and_pill():
    """Part A: the row2 console-selector row draws its ``row2-tab-background``
    tab-cell band (the amber ridged backing) at its SVG z, BEHIND the labels +
    the selected pill ([CXC-CIC-TABROW] [DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-
    TRUTH]).  Extracted verbatim into the bundle as a descendant of ``row2-tabs``;
    rendered once, mapped from its own bbox into the full bar rect."""
    from compose_projects.bsg.widgets.cic import svg_art
    import composeio.render.widgets.segmented_tab_row as _seg
    # the tab rows are STD-owned ([CXC-COMP-MODE-SURFACE]); select STD so they show
    app, comp, cic = _app(_scene(radio_selected=1))
    row2 = cic.row2
    # the background band comes from the STD mode surface (std_mode.svg)
    assert svg_art.find_element(row2._art_doc, "row2-tab-background") is not None
    assert row2._has_bg_art()
    # rendering the console emits the background node THROUGH the SVG contract
    # (the tab row calls the engine's render_element), BEFORE the pill (behind it)
    calls = []
    orig = _seg.render_element

    def spy(canvas, doc, node, rect, design, fit="stretch"):
        calls.append(node)
        return orig(canvas, doc, node, rect, design, fit)

    _seg.render_element = spy
    try:
        with tempfile.TemporaryDirectory() as d:
            app.render_headless(os.path.join(d, "a.png"))
        assert row2._art_bg_node in calls                 # the band was rendered
        # z-order: the background is emitted before the selected pill
        assert calls.index(row2._art_bg_node) < calls.index(row2._art_pill_node)
    finally:
        _seg.render_element = orig
    app.stop()


def test_radio_group_placed_at_bounding_box_dradis_highlighted():
    """Part B: the radio-button group is placed at the base's DERIVED
    ``slot:radio@mode`` bbox ([CXC-COMP-SLOT], consumed via the router) in the row1
    bar with the initial four (DRADIS/STD/EXT/CHRONO), DRADIS selected in the theme
    highlight."""
    app, comp, cic = _app(_scene())
    assert cic.radio in cic.children                       # in the layer stack
    assert cic.radio.labels == ["DRADIS", "STD", "EXT", "CHRONO"]
    assert cic.radio.selected_index == 0                   # DRADIS
    assert cic.radio.highlight_color == (0xCD, 0x67, 0x1D, 0xFF)
    # placement geometry comes from the DERIVED slot:radio@mode bbox (never
    # hand-coded) — the router is the adoption seam ([CXC-COMP-SLOT])
    from compose_projects.bsg.widgets.cic.composite import slot_bbox
    assert slot_bbox(cic._base_plan, "radio", "mode") is not None
    assert cic._radio_vb is not None
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "a.png"))       # force layout
    ar = cic.radio.arranged_rect
    assert ar is not None
    # scaled-to-fit the bounding box: the group's natural size is centered on
    # the mapped device rect (the DRADIS drop-in scale idiom)
    box = cic._band_rect(cic._radio_vb, Rect(0, 0, 1280, 960))
    assert box is not None
    assert abs((ar.x + ar.w / 2.0) - (box.x + box.w / 2.0)) < 1.0
    assert 0.0 < cic.radio.scale <= 1.05
    app.stop()


def test_radio_buttons_configurable_from_scene():
    """The radio set is configurable-N from the scene (a bound list later)."""
    app, comp, cic = _app(_scene(radio_buttons=["DRADIS", "STD"],
                                 radio_selected=1))
    assert cic.radio.labels == ["DRADIS", "STD"]
    assert cic.radio.selected_index == 1
    app.stop()


def test_row2_row3_label_styling_matches_svg():
    """Row2 (console selectors) labels are WHITE; row3 (sub-tabs) are #cccccc;
    both sizes are scaled from David's SVG text so row3 renders slightly LARGER
    than row2 (the row2-tiny / row3-huge flip is corrected) ([CXC-CIC-TABROW],
    [DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH])."""
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "a.png"))    # force layout
    # colors read from the SVG text (NOT hand-coded highlight/muted)
    assert cic.row2.text_color[:3] == (0xFF, 0xFF, 0xFF)         # row2 white
    assert cic.row3.text_color[:3] == (0xCC, 0xCC, 0xCC)         # row3 #ccc
    # sizes scaled to device from David's design-space text sizes; row3 > row2
    assert cic.row3.font_size > cic.row2.font_size
    assert cic.row2.font_size > 0 and cic.row3.font_size > 0
    app.stop()


def _device_font_sizes(cic):
    """The DEVICE-space font sizes of the four CIC chrome text families after
    layout: row2 / row3 tab labels (already device), the reboot/shut-down button
    text, and the radio caption (widget size × its scale-to-fit).  All four MUST
    derive from the SVG per-element ``font-size`` ([DEC-SVG-COMPOSITION-SOURCE-OF-
    TRUTH] text styling)."""
    design_h = cic._art_design[3]
    dscale = 960.0 / design_h
    return {
        "row2": cic.row2.font_size,
        "row3": cic.row3.font_size,
        "reboot": cic.buttons._label_size * dscale,
        "radio": cic.radio.font_size * cic.radio.scale,
    }


def test_row2_row3_and_reboot_sizes_are_svg_derived():
    """The row2/row3 tab-label sizes (from the STD mode surface) and the reboot/
    shut-down button-text size (from the base fixture) are all SVG-derived, not
    hardcoded ([DEC-SVG-COMPOSITION-SOURCE-OF-TRUTH]).

    NOTE (flagged for David): the radio caption size is no longer SVG-derived under
    the mode-surface model — the base fixture's ``slot:radio@mode`` is a bare
    placement rect with NO caption ``<text>``, so the radio group falls back to its
    own default caption size.  When David authors a caption ``<text>`` inside
    ``slot:radio@mode`` it will be honored (the wiring reads it); until then the
    reboot==radio-size parity is deferred."""
    app, comp, cic = _app(_scene(radio_selected=1))      # STD -> tab rows shown
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "a.png"))    # force layout
    sizes = _device_font_sizes(cic)
    assert sizes["row2"] > 0 and sizes["row3"] > 0       # from std_mode.svg <text>
    assert sizes["reboot"] > 0                            # from the base fixture
    assert sizes["radio"] > 0                             # widget default (see NOTE)
    app.stop()


def test_button_text_size_tracks_the_base_svg_font_size():
    """Editing the reboot/shut-down button-text ``font-size`` in the BASE svg
    re-drives the render with NO code change ([DEC-SVG-COMPOSITION-SOURCE-OF-
    TRUTH]).  (row2/row3 tab-text tracking is via std_mode.svg, exercised
    separately; the radio caption is deferred — see the NOTE above.)"""
    from compose_projects.bsg.widgets.cic import svg_art
    from compose_projects.bsg.widgets.cic.composite import resource_svg, BASE_FIXTURE
    _app_obj, _comp, base_cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        _app_obj.render_headless(os.path.join(d, "a.png"))
    base_reboot = base_cic.buttons._label_size
    _app_obj.stop()

    # a modified copy of the REAL base fixture with the button-text size bumped
    src = open(resource_svg(BASE_FIXTURE), encoding="utf-8").read()
    assert "font-size:4.23333px" in src                  # the base button-text size
    mod = src.replace("font-size:4.23333px", "font-size:8px")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "base_bigger.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mod)
        svg_art._doc_cache.clear()
        svg_art._designbox_cache.clear()
        app, comp, cic = _app(_scene(art_svg=path))
        app.render_headless(os.path.join(d, "b.png"))    # force layout
        big_reboot = cic.buttons._label_size
        app.stop()
    svg_art._doc_cache.clear()
    svg_art._designbox_cache.clear()
    # the button-text size grew — the base SVG font-size drove it, nothing hardcoded
    assert big_reboot > base_reboot + 1.0


def test_button_bar_renders_exact_svg_shapes_at_svg_positions():
    """REBOOT / SHUT DOWN are David's EXACT ``button-reboot`` / ``button-shut-down``
    SVG shapes placed from their SVG geometry (both on the LEFT in his final
    export), NOT a hand-drawn approximation ([CXC-CIC-SVG-MAP]).  The full-rect
    button widget only catches taps on the button shapes, so taps elsewhere pass
    through to the tabs/content beneath it."""
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "a.png"))    # force layout
    b = cic.buttons
    assert b._has_art()                                   # SVG shapes, not fallback
    rects = dict((a, r) for r, a in b._device_rects(b.arranged_rect))
    assert set(rects) == {"reboot", "shutdown"}
    # David's final SVG puts both buttons on the LEFT, adjacent, reboot first
    assert rects["reboot"].x < rects["shutdown"].x
    assert rects["shutdown"].x < 640                      # both left of center
    # hit_test catches ONLY the button shapes, not the screen/content area
    rb = rects["reboot"]
    assert b.hit_test(rb.x + rb.w / 2.0, rb.y + rb.h / 2.0) is True
    assert b.hit_test(640, 560) is False                  # a screen-center tap
    # a tap on the SHUT DOWN shape raises its confirmation dialog
    sd = rects["shutdown"]
    b.on_tap(sd.x + sd.w / 2.0, sd.y + sd.h / 2.0)
    assert cic.dialog.visible is True
    assert cic.dialog._pending == "shutdown"
    app.stop()


def test_status_band_lands_in_chrome_not_the_screen():
    """The status band artwork + its dynamic text render in the CHROME (directly
    below row3), NOT down in the screen area — David carries the band's placement
    on a PARENT group transform that isolated rendering had dropped
    ([CXC-CIC-WIDGET], the status-mislocation fix)."""
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "a.png"))    # force layout
    st = cic.status
    g = compute_geometry(1280, 960)
    # map the id-number slot (design space) into device y and assert it sits in
    # the chrome band, ABOVE the screen-area cutout (before the fix it landed
    # ~90px lower, inside the screen)
    _dx, _dy, _dw, dh = st._design
    slot_dev_y = st.arranged_rect.y + (st._id_slot["y"] - _dy) * (
        st.arranged_rect.h / dh)
    assert slot_dev_y < g.screen_area.y                   # in the chrome, not screen
    app.stop()


def test_radio_tap_publishes_and_console_subscribes():
    """A radio tap publishes the station-scoped index, and the console registers
    the subscription so a rotary / Intelligence publish moves the selection
    identically ([CXC-CIC-CREDO])."""
    app, comp, cic = _app(_scene(station="cic.1.helm"))
    # the subscription is registered (the shell reacts to the radio namespace)
    assert ("colonial_console/cic.1.helm/radio", cic.radio) in comp.subscriptions
    # a direct publish (rotary / Intelligence) moves the selection
    app.publish("colonial_console/cic.1.helm/radio", {"index": 2}, source="t")
    app.drain_updates()
    assert cic.radio.selected_index == 2
    # and a TAP publishes the station-scoped index (touch == the same peer)
    got = []
    cic.radio.publish = lambda ns, v: got.append((ns, v))
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "a.png"))    # lay out the cells
    cell = cic.radio._button_rects[1]                     # STD
    cx = cic.radio.arranged_rect.x + cic.radio.arranged_rect.w / 2.0
    cy = cic.radio.arranged_rect.y + cic.radio.arranged_rect.h / 2.0
    scl = cic.radio.scale
    tx = cx + (cell.x + cell.w / 2.0 - cx) * scl
    ty = cy + (cell.y + cell.h / 2.0 - cy) * scl
    cic.radio.on_tap(tx, ty)
    assert got == [("colonial_console/cic.1.helm/radio",
                    {"index": 1, "key": "STD"})]
    app.stop()


def test_status_band_renders_svg_artwork_with_dynamic_text():
    """The status band renders ``row3-tab-status-bar-artwork`` and stamps the live
    id-number + status label at David's placeholder spots ([CXC-CIC-WIDGET])."""
    from compose_projects.bsg.widgets.cic import svg_art
    app, comp, cic = _app(_scene())
    st = cic.status
    assert st._node is not None                   # status artwork loaded
    assert svg_art.find_element(st._doc,
                                "row3-tab-status-bar-artwork") is not None
    # the dynamic-text placeholders are located from the SVG
    assert st._id_slot is not None and st._label_slot is not None
    assert st._label_slot["align"] == "center"    # David centers the label
    # a bus status publish updates the live strings drawn into those spots
    app.publish("colonial_console/cic.1.helm/status",
                {"id_number": "04", "label": "PLANETARY SURVEY ACTIVE"},
                source="t")
    app.drain_updates()
    assert st.id_number == "04"
    assert st.label == "PLANETARY SURVEY ACTIVE"
    app.stop()


# ---------------------------------------------------------------------------
# REBOOT / SHUT DOWN modal dialogs ([CXC-CIC-WIDGET] overlay-dialog-box)
# ---------------------------------------------------------------------------

def test_reboot_press_shows_dialog_with_label():
    app, comp, cic = _app(_scene())
    assert cic.dialog.visible is False
    cic.buttons.on_power_intent("reboot")       # the wired button callback
    assert cic.dialog.visible is True
    assert cic.dialog._label == "REBOOT CONSOLE?"
    assert cic.dialog._pending == "reboot"
    app.stop()


def test_shutdown_press_shows_dialog_with_label():
    app, comp, cic = _app(_scene())
    cic.buttons.on_power_intent("shutdown")
    assert cic.dialog.visible is True
    assert cic.dialog._label == "CONSOLE SHUTDOWN?"
    assert cic.dialog._pending == "shutdown"
    app.stop()


def test_dialog_confirm_publishes_power_and_dismisses():
    app, comp, cic = _app(_scene())
    got = []
    cic.publish = lambda ns, v: got.append((ns, v))
    cic.buttons.on_power_intent("reboot")
    cic._on_dialog_confirm(cic.dialog._pending)
    assert got == [("colonial_console/cic.1.helm/power", {"action": "reboot"})]
    assert cic.dialog.visible is False           # dismissed after confirm
    app.stop()


def test_dialog_abort_dismisses_without_publish():
    app, comp, cic = _app(_scene())
    got = []
    cic.publish = lambda ns, v: got.append((ns, v))
    cic.buttons.on_power_intent("shutdown")
    assert cic.dialog.visible is True
    cic._on_dialog_dismiss()
    assert cic.dialog.visible is False
    assert got == []                             # abort fires no power intent
    app.stop()


def test_dialog_tap_routes_confirm_and_abort_by_svg_geometry():
    """David's CURRENT ``colonial_base_ux.svg`` re-added the modal-dialog
    affordances: ``confirm-button`` / ``abort-button`` / ``dialog-close-button``
    carry real geometry again, so tap routing keys off David's EXACT SVG placement
    ([DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]).  A tap inside the confirm box fires
    the pending power intent; a tap inside the abort box dismisses without
    publishing; a tap outside every affordance is swallowed (modal).

    FLAG for David: ``dialog-box-dynamic-label`` is STILL absent from the source
    SVG, so the confirm prompt's dynamic label text (REBOOT CONSOLE? / CONSOLE
    SHUTDOWN?) is not stamped into the artwork yet — the label lives on the widget
    (``dlg._label``) and drives the callback path, but is not painted until the
    label element returns."""
    from compose_projects.bsg.widgets.cic import svg_art
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "x.png"))   # force layout
    dlg = cic.dialog
    # geometry is back -> the affordance boxes resolve (not the degradation path)
    assert dlg._confirm_box is not None and dlg._abort_box is not None
    assert dlg._node is not None
    assert dlg.arranged_rect is not None and dlg._design is not None

    def _center(box):
        r = svg_art.vb_box_to_rect(box, dlg.arranged_rect, dlg._design)
        return (r.x + r.w / 2.0, r.y + r.h / 2.0)

    got = []
    cic.publish = lambda ns, v: got.append((ns, v))
    # a tap OUTSIDE every affordance is swallowed (modal) ...
    cic.buttons.on_power_intent("reboot")
    assert dlg.visible is True
    cx, cy = _center(dlg._confirm_box)
    # pick a point clear of all three affordance boxes but inside the rect
    dlg.on_tap(dlg.arranged_rect.x + 4.0, dlg.arranged_rect.y + 4.0)
    assert got == [] and dlg.visible is True     # swallowed, still modal
    # ... a tap INSIDE the confirm box routes the pending power intent
    dlg.on_tap(cx, cy)
    assert got == [("colonial_console/cic.1.helm/power", {"action": "reboot"})]
    assert dlg.visible is False

    # a tap inside the ABORT box dismisses WITHOUT publishing
    got.clear()
    cic.buttons.on_power_intent("shutdown")
    ax, ay = _center(dlg._abort_box)
    dlg.on_tap(ax, ay)
    assert got == []                             # abort fires no power intent
    assert dlg.visible is False                  # dismissed
    app.stop()


def test_dialog_is_modal_swallows_outside_taps():
    """While shown the dialog claims the full rect (topmost, interactive) so a
    tap outside the affordances is swallowed and never reaches the tabs."""
    app, comp, cic = _app(_scene())
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "x.png"))
    cic.buttons.on_power_intent("reboot")
    # the dialog is the topmost hit target over the whole rect
    hit = cic.hit_test_tree(640, 850)            # a point over a tab/content area
    assert hit is cic.dialog
    got = []
    cic.publish = lambda ns, v: got.append((ns, v))
    cic.dialog.on_tap(640, 850)                  # outside any affordance
    assert got == []                             # swallowed
    assert cic.dialog.visible is True            # still modal
    app.stop()


# ---------------------------------------------------------------------------
# DRADIS drop-in vs placeholder ([CXC-CIC-CONTENT])
# ---------------------------------------------------------------------------

def test_dradis_mode_is_the_static_surface_no_game_dropin():
    """DRADIS mode is the STATIC ``dradis_mode.svg`` surface composited under the
    base ([CXC-COMP-MODE-SURFACE]); there is NO live DRADIS game-scene drop-in
    (HARD constraint 2026-08-05).  Selecting DRADIS shows its derived surface (which
    HAS content — its own scope + nav art) and hides the STD tab rows (DRADIS owns
    its own nav)."""
    app, comp, cic = _app(_scene())
    # no game drop-in attribute exists anymore
    assert not hasattr(cic.content_router, "dradis")
    assert not hasattr(cic, "set_dradis_content")
    # DRADIS radio (index 0) -> the static dradis_mode surface, with content
    app.publish("colonial_console/cic.1.helm/radio", {"index": 0}, source="t")
    app.drain_updates()
    dradis = cic.mode_widgets["DRADIS"]
    assert cic.content_router.active_mode == "DRADIS"
    assert dradis.visible is True
    assert dradis.has_content() is True               # the static surface, derived
    # DRADIS owns its own nav -> the STD tab rows are hidden
    assert cic.row2.visible is False and cic.row3.visible is False
    # STD (index 1) -> the STD surface, tab rows shown
    app.publish("colonial_console/cic.1.helm/radio", {"index": 1}, source="t")
    app.drain_updates()
    assert cic.content_router.active_mode == "STD"
    assert cic.mode_widgets["STD"].visible is True
    assert cic.row2.visible is True and cic.row3.visible is True
    app.stop()


def test_status_bar_binds_and_falls_back_to_selection():
    app, comp, cic = _app(_scene())
    # default: reflects the selection (never blank)
    assert cic.status.label == "HELM / WAYPOINT"
    # an explicit status publish overrides
    app.publish("colonial_console/cic.1.helm/status",
                {"id_number": "04", "label": "PLANETARY SURVEY ACTIVE"},
                source="t")
    app.drain_updates()
    assert cic.status.id_number == "04"
    assert cic.status.label == "PLANETARY SURVEY ACTIVE"
    app.stop()


# ---------------------------------------------------------------------------
# Radio-mode content-source map ([CXC-CIC-RADIO-MODE]): the row1 radio row selects
# a screen-area MODE, each rendering CONTENT FROM ITS OWN svg (bus-asset).  EXT ->
# David's ``ext-view`` external-view art from ext_mode.svg (NOT the generic
# ``screen-scan-stars`` group, byte-identical to the base screen — the reported
# defect).  DRADIS / CHRONO have no art yet -> a clean placeholder (never a
# fall-through to the base stars).  STD -> the standard console-tab content.  The
# chrome (frame / tabs / radios / buttons / status) stays throughout.
# ---------------------------------------------------------------------------

def test_ext_view_derived_from_ext_mode_fixture():
    """The EXT screen content is DERIVED from David's ``ext_mode.svg`` via the
    composition derivation ([SPEC:CXC-COMP], the ADOPTION SEAM) — its ``ref:`` base
    chrome copies SKIPPED, its own ``ext-view`` rendered.  Its design box is
    IDENTICAL to the base frame's, so the external view registers with the frame +
    screen hole ([DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH])."""
    from compose_projects.bsg.widgets.cic import svg_art
    app, comp, cic = _app(_scene())
    ext = cic.ext_view
    assert ext is not None
    assert ext.has_content()                          # derived content (not placeholder)
    assert ext._doc is not None and ext._layers        # backdrop layers present
    # design box IDENTICAL to the base frame -> pixel-exact registration
    assert ext._design == cic.frame._design
    # the derived layers include David's OWN ext-view group; the ref: base chrome
    # copies were SKIPPED by the derivation ([CXC-COMP-REF])
    labels = [lbl for lbl, _ in ext._layers]
    assert "ext-view" in labels
    assert not any(lbl.startswith("ref:") for lbl in labels)
    assert svg_art.find_element(ext._doc, "ext-view") is not None
    app.stop()


def test_chrome_art_resolves_from_bus(monkeypatch):
    """Bus-native ([SPEC:CXC-CIC], the DRADIS-console model): load_art(None)
    resolves the chrome from the store (CHROME_URI).  Byte-equivalent to the
    bundled render — we feed the store the bundled bytes and prove the SAME doc
    (the master-mask frame element) comes back through the bus path."""
    from compose_projects.bsg.widgets.cic import svg_art
    import composeio.resolve as _r
    with open(svg_art.CHROME_SVG, "rb") as fh:
        bundled = fh.read()
    seen = {}

    def _fake(uri, **kw):
        seen["uri"] = uri
        return bundled
    monkeypatch.setattr(_r, "resolve_bytes", _fake)
    monkeypatch.setattr(svg_art, "_doc_cache", {})   # bypass the process cache
    doc = svg_art.load_art()                          # None -> CHROME_URI
    assert seen["uri"] == svg_art.CHROME_URI
    assert doc is not None
    assert svg_art.find_element(doc, "standard_ux_shape_element") is not None


def test_chrome_art_offline_fallback_to_bundled(monkeypatch):
    """Store-unreachable gate: with a bad store, load_art(None) falls back to the
    package-bundled chrome — no crash, the frame still resolves so the tab rows +
    chrome render (the fonts.py bus-first-then-bundled pattern)."""
    from compose_projects.bsg.widgets.cic import svg_art
    _force_store_offline(monkeypatch)
    monkeypatch.setattr(svg_art, "_doc_cache", {})
    doc = svg_art.load_art()                          # None -> CHROME_URI -> down -> bundled
    assert doc is not None
    assert svg_art.find_element(doc, "standard_ux_shape_element") is not None


def test_ext_art_offline_fallback_to_bundled(monkeypatch):
    """The EXT view is bus-first too (EXT_URI); a store-unreachable surface falls
    back to the bundled EXT SVG (which carries David's ext-view external-view art),
    so EXT still draws offline ([CXC-CIC-RADIO-MODE])."""
    from compose_projects.bsg.widgets.cic import svg_art
    _force_store_offline(monkeypatch)
    monkeypatch.setattr(svg_art, "_doc_cache", {})
    doc = svg_art.load_art(svg_art.EXT_URI)
    assert doc is not None
    assert svg_art.find_element(doc, "ext-view") is not None


def test_ext_view_image_is_bus_referenced_not_bundled():
    """Fix 1 ([CXC-CIC-RADIO-MODE]): David's ext-view image is REFERENCED from the
    bus store, NOT baked in-band.  The trimmed EXT bundle carries the ext-view
    STRUCTURE + a bus:// href on the <image> + the baked rotation manifest
    (data-rotation / data-interval), but NO ~0.5 MB data: payload — the bundle
    is tiny (bloat fixed)."""
    from compose_projects.bsg.widgets.cic import svg_art
    assert os.path.exists(svg_art.EXT_SVG)             # bundled beside the widget
    with open(svg_art.EXT_SVG, "r", encoding="utf-8") as fh:
        art = fh.read()
    assert "ext-view" in art
    assert "data:image" not in art                     # NO in-band image payload
    assert svg_art.SCREENS_URI_PREFIX in art           # referenced by bus URI
    assert len(art) < 50_000                            # bloat fixed (was ~0.66 MB)
    # the <image> href points at the bus store; the rotation manifest rides the group
    doc = svg_art.load_art(svg_art.EXT_SVG)
    ev = svg_art.find_element(doc, "ext-view")
    assert ev is not None
    img = next((c for c in ev.children if c.tag == "image"), None)
    assert img is not None and img.attrs["href"].startswith(svg_art.SCREENS_URI_PREFIX)
    assert ev.attrs.get("data-rotation")               # ordered rotation set
    assert float(ev.attrs.get("data-interval")) == 15.0   # from the ext-view <desc>


def test_ext_bundle_is_up_to_date_with_source():
    """The bundled EXT art is a regenerable bake of David's tracked source; the
    committed file must match a fresh bake (byte-stable, the chrome-bake idiom).
    Skips if the authoring source is not present in this checkout."""
    import subprocess
    src = os.path.join(
        PY_ROOT, os.pardir, "compose_projects", "bsg", "resources",
        "screen_base_layout", "ext_mode.svg")
    if not os.path.exists(src):
        pytest.skip("ext_mode.svg authoring source not present in this checkout")
    script = os.path.join(PY_ROOT, "scripts", "bake_cic_ext_svg.py")
    r = subprocess.run([sys.executable, script, "--check"],
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": PY_ROOT})
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------------
# Fix 3 — the SVG renderer honors visibility:hidden / display:none
# ([CXC-CIC-RADIO-MODE]): David's ext_mode hidden groups (row2-tabs / row3-tabs /
# in-show-non-interactive-animations) must NOT render.  Tested at the SEAM THAT
# BROKE: the generic SVG renderer.
# ---------------------------------------------------------------------------

def _recording_canvas():
    """A Canvas over the abstract contract that records (method, args) — the
    test-double idiom from test_svg.py."""
    from composeio.render.canvas import Canvas, Size, TextMetrics
    calls: list = []

    class RecordingCanvas(Canvas):
        pass

    def _mk(name):
        def f(self, *a, **k):
            calls.append((name, a))
            if name == "measure_text":
                return TextMetrics(0, 0, 0, 0)
            if name == "get_surface_size":
                return Size(400, 300)
            if name in ("decode_image", "load_image"):
                return object()          # a non-None image handle
            return None
        return f

    for m in {m for m in dir(Canvas) if not m.startswith("_")}:
        setattr(RecordingCanvas, m, _mk(m))
    RecordingCanvas.__abstractmethods__ = frozenset()
    return RecordingCanvas(), calls


_VIS_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"
  viewBox="0 0 100 100">
  <rect id="shown" x="0" y="0" width="10" height="10" fill="#ff0000"/>
  <rect id="gone" x="10" y="0" width="10" height="10" fill="#00ff00"
        style="display:none"/>
  <rect id="hidden" x="20" y="0" width="10" height="10" fill="#0000ff"
        visibility="hidden"/>
  <g style="display:none">
    <rect id="nested" x="30" y="0" width="10" height="10" fill="#ffff00"/>
  </g>
</svg>"""


def test_svg_renderer_honors_display_none_and_visibility_hidden():
    """Fix 3 seam: display:none and visibility:hidden elements — and the whole
    subtree under a hidden group — do NOT emit any draw call ([CXC-CIC-RADIO-
    MODE])."""
    from composeio.render.svg import SvgDocument, render_svg
    doc = SvgDocument.from_string(_VIS_SVG)
    c, calls = _recording_canvas()
    render_svg(c, doc, Rect(0, 0, 100, 100), fit="stretch")
    # exactly ONE rect painted (the visible one); the 3 hidden ones are skipped
    fills = [a for (n, a) in calls if n == "fill_path"]
    assert len(fills) == 1, calls


def test_render_element_draws_explicitly_extracted_hidden_top_node():
    """The visibility rule hides DESCENDANTS, but an EXPLICITLY-extracted node is
    neutralized so it still draws — this is why the modal overlay-dialog-box
    layer (display:none in David's SVG) renders on demand ([CXC-CIC])."""
    from composeio.render.svg import SvgDocument, render_element
    doc = SvgDocument.from_string(_VIS_SVG)
    gone = doc.ids["gone"]
    c, calls = _recording_canvas()
    render_element(c, doc, gone, Rect(0, 0, 100, 100), (0, 0, 100, 100))
    # the display:none top node is neutralized -> it paints
    assert any(n == "fill_path" for (n, a) in calls), calls


# ---------------------------------------------------------------------------
# Fix 4 — the EXT image is matted to the screen bounds by LAYER OCCLUSION (David's
# approved approach): the opaque screen-area black plate draws OVER the content, so
# the image cannot bleed through the amber frame's decorative cutouts.  NO active
# clip_rect — the clip is an artifact of z-order ([CXC-CIC-RADIO-MODE] Fix 4).
# ---------------------------------------------------------------------------

def test_ext_image_matted_by_screen_area_layer_not_active_clip():
    """The EXT surface itself does NOT clip (no active aperture clip_rect); the
    matting is pure layering by the base screen-area plate above it
    ([CXC-COMP-MISSING] master mask)."""
    app, comp, cic = _app(_scene())
    ext = cic.ext_view
    assert ext.has_content()
    assert not hasattr(ext, "_window_box")             # no clip geometry
    rect = Rect(0, 0, 1280, 960)
    c, calls = _recording_canvas()
    ext.draw(c, rect)
    # the widget draws its content loosely — the ONLY clip is render_svg's own
    # clip-to-target-rect (the FULL widget rect), never an inset aperture clip of
    # the widget's own (that was the removed active clip).
    clips = [a for (n, a) in calls if n == "clip_rect"]
    assert clips, calls
    for (cx, cy, cw, ch) in clips:
        assert (round(cx), round(cy), round(cw), round(ch)) == \
            (round(rect.x), round(rect.y), round(rect.w), round(rect.h)), clips
    assert any(n == "draw_image" for (n, a) in calls), calls
    app.stop()


def test_ext_image_no_bleed_through_amber_cutouts_by_layering(monkeypatch):
    """PIXEL proof of the layer-only matte ([CXC-CIC-RADIO-MODE] Fix 4): with EXT
    selected, a probe in the image's left-bleed strip — design (10,120), which is
    OUTSIDE the screen aperture and falls inside the amber frame's LEFT decorative
    cutout — reads the black screen-area plate (occluded), NOT the planet image
    bleeding through the cutout; a probe INSIDE the aperture still shows the image.
    Before the screen-area matte moved on top, the image bled through the cutout."""
    from PIL import Image
    from compose_projects.bsg.widgets.cic import svg_art
    _serve_screens_locally(monkeypatch)
    monkeypatch.setattr(svg_art, "_doc_cache", {})
    app, comp, cic = _app(_scene())
    app.publish("colonial_console/cic.1.helm/radio", {"index": 2}, source="t")
    app.drain_updates()
    # design box is 338.67 x 254 mapped into 1280 x 960
    sx, sy = 1280 / 338.67, 960 / 254.0
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "ext.png")
        app.render_headless(out)
        img = Image.open(out).convert("RGB")

        def is_amber(px):
            return px[0] > 90 and px[1] < 90 and px[2] < 60

        # left-bleed strip, inside the amber left decorative cutout -> BLACK plate
        left = img.getpixel((int(10 * sx), int(120 * sy)))
        assert sum(left) < 60 and not is_amber(left), left      # occluded by matte
        # inside the aperture -> the external-view image (lit, non-black, non-amber)
        inside = img.getpixel((int(160 * sx), int(120 * sy)))
        assert sum(inside) > 90 and not is_amber(inside), inside
    app.stop()


# ---------------------------------------------------------------------------
# Fix 2 — the EXT image rotates through the bus-storage set on the interval
# David specified in the ext-view <desc> (~15s) ([CXC-CIC-RADIO-MODE]).
# ---------------------------------------------------------------------------

def test_ext_rotation_active_from_authored_manifest():
    """David AUTHORED the rotation manifest onto ``ext_mode.svg``'s ext-view
    (``data-rotation`` + ``data-interval``, 2026-08-05 — SVG-source-of-truth,
    superseding the old baked-only manifest).  So the derived ext surface reads it
    and the rotation is ACTIVE: >=2 image URIs, ``_rotates()`` True, and the
    surface registers as a clocked widget that cycles the external-view images."""
    app, comp, cic = _app(_scene())
    ext = cic.ext_view
    assert len(ext._rotation_uris) >= 2                # authored rotation set
    assert ext._rotates() is True                      # active, not dormant
    assert any(w is ext for w in app._clocked)         # registered as clocked
    app.stop()


def test_ext_rotation_swaps_image_href_deterministically():
    """When a rotation manifest IS present, the playhead is a PURE FUNCTION of the
    clock (the Lottie determinism seam): the same tick times yield the same href,
    and the swap mutates the <image> href the renderer resolves at draw time.  We
    inject a synthetic manifest onto the derived ext surface to exercise the
    mechanism independent of the bake step."""
    app, comp, cic = _app(_scene())
    ext = cic.ext_view
    ext.visible = True
    # synthetic rotation manifest (the bake would set these on the ext-view group)
    uris = ["bus://assets/cic/screens/a.jpg", "bus://assets/cic/screens/b.jpg",
            "bus://assets/cic/screens/c.jpg"]
    ext._rotation_uris = uris
    ext._rotation_interval = 15.0
    ext._rotation_index = -1                            # force the first swap
    assert ext._image_node is not None                 # the ext-view <image>
    assert ext._rotates() is True
    n = len(uris)
    ext.on_clock_tick(0.0)
    assert ext._image_node.attrs["href"] == uris[0]
    ext.on_clock_tick(15.0)
    assert ext._image_node.attrs["href"] == uris[1]
    ext.on_clock_tick(20.0)                             # mid-interval, no advance
    assert ext._image_node.attrs["href"] == uris[1]
    ext.on_clock_tick(15.0 * n)                         # wraps
    assert ext._image_node.attrs["href"] == uris[0]
    app.stop()


def test_radio_ext_shows_external_view_hides_content():
    """Selecting EXT on the radio swaps to the EXT surface and hides every other
    mode surface + the STD tab rows (EXT is fullscreen, no nav) — the screen-area
    MODE is the radio index ([CXC-COMP-MODE-SURFACE])."""
    app, comp, cic = _app(_scene())
    # publish EXT (index 2) — rotary / Intelligence / touch, identical path
    app.publish("colonial_console/cic.1.helm/radio", {"index": 2, "key": "EXT"},
                source="t")
    app.drain_updates()
    assert cic.content_router.active_mode == "EXT"
    assert cic.ext_view.visible is True                # external view shown
    assert cic.ext_view.has_content()                  # the ext-view art, not placeholder
    # every other mode surface hidden; STD tab rows hidden (EXT has no nav)
    assert cic.mode_widgets["STD"].visible is False
    assert cic.mode_widgets["DRADIS"].visible is False
    assert cic.row2.visible is False and cic.row3.visible is False
    app.stop()


def test_radio_swaps_mode_surface_chrono_placeholder():
    """The radio row swaps the whole mode surface over the base ([CXC-COMP-MODE-
    SURFACE]): STD/EXT/DRADIS each composite their OWN authored fixture (all present
    -> real content); CHRONO — with NO mapped fixture — shows a clean labeled
    PLACEHOLDER, NEVER a fall-through to another mode ([CXC-COMP-MISSING])."""
    app, comp, cic = _app(_scene())
    # EXT (index 2): its own fixture, has content
    app.publish("colonial_console/cic.1.helm/radio", {"index": 2}, source="t")
    app.drain_updates()
    assert cic.ext_view.visible is True and cic.ext_view.has_content()
    # STD (index 1): the STD surface + the live tab rows
    app.publish("colonial_console/cic.1.helm/radio", {"index": 1}, source="t")
    app.drain_updates()
    assert cic.content_router.active_mode == "STD"
    assert cic.ext_view.visible is False
    assert cic.mode_widgets["STD"].visible is True
    assert cic.row2.visible is True and cic.row3.visible is True
    # DRADIS radio (index 0): the STATIC dradis_mode surface (HAS content), tabs off
    app.publish("colonial_console/cic.1.helm/radio", {"index": 0}, source="t")
    app.drain_updates()
    dradis_mode = cic.mode_widgets["DRADIS"]
    assert cic.content_router.active_mode == "DRADIS"
    assert dradis_mode.visible is True
    assert dradis_mode.has_content() is True           # static surface (authored)
    assert cic.row2.visible is False
    # CHRONO radio (index 3): NO mapped fixture -> clean placeholder
    app.publish("colonial_console/cic.1.helm/radio", {"index": 3}, source="t")
    app.drain_updates()
    chrono_mode = cic.mode_widgets["CHRONO"]
    assert cic.content_router.active_mode == "CHRONO"
    assert chrono_mode.visible is True and chrono_mode.has_content() is False
    app.stop()


def test_ext_mode_driven_identically_and_shell_subscribes(monkeypatch):
    """A direct index publish (rotary / Intelligence) selects EXT identically to a
    tap; the SHELL registers the radio subscription so the mode signal reaches it
    ([CXC-CIC-CREDO])."""
    from compose_projects.bsg.widgets.cic import svg_art
    _force_store_offline(monkeypatch)
    monkeypatch.setattr(svg_art, "_doc_cache", {})
    app, comp, cic = _app(_scene(station="cic.1.helm"))
    # the shell (not just the radio widget) subscribes the radio namespace
    assert ("colonial_console/cic.1.helm/radio", cic) in comp.subscriptions
    # a bare-key payload also selects EXT (control-is-a-peer)
    app.publish("colonial_console/cic.1.helm/radio", {"key": "EXT"}, source="t")
    app.drain_updates()
    assert cic.content_router.active_mode == "EXT"
    assert cic.radio_index == 2
    assert cic.ext_view.visible is True
    app.stop()


def test_ext_mode_renders_own_ext_view_not_shared_base_stars(monkeypatch):
    """REGRESSION for the reported defect ([CXC-CIC-RADIO-MODE]): selecting EXT
    renders David's OWN ``ext-view`` external-view art from ext_mode.svg, and NOT
    the generic ``screen-scan-stars`` group.  ``screen-scan-stars`` is BYTE-
    IDENTICAL across colonial_base_ux.svg and ext_mode.svg (same structure + same
    embedded image), so keying EXT on it rendered the STANDARD screen content ("the
    screen-scan-stars content from colonial_base_ux.svg") — machine-green while
    glass-wrong.  This asserts the SEAM THAT BROKE: the EXT mode targets + draws its
    own ext-view group, never the shared wrapper.  Forces the bundle so it is
    deterministic + store-independent (the bus may still serve a stale ext.svg)."""
    from compose_projects.bsg.widgets.cic import svg_art
    app, comp, cic = _app(_scene())
    ext = cic.ext_view
    # the EXT surface renders its OWN derived ext-view group, never a shared wrapper
    assert ext.has_content()
    labels = [lbl for lbl, _ in ext._layers]
    assert "ext-view" in labels
    assert "screen-scan-stars" not in labels
    assert svg_art.find_element(ext._doc, "ext-view") is not None
    # the ext-view derived node is what actually gets DRAWN
    ext_node = next(node for lbl, node in ext._layers if lbl == "ext-view")
    app.publish("colonial_console/cic.1.helm/radio", {"index": 2}, source="t")
    app.drain_updates()
    drawn = []
    orig = svg_art.render_element

    def spy(canvas, doc, node, rect, design, fit="stretch"):
        drawn.append(node)
        return orig(canvas, doc, node, rect, design, fit)
    monkeypatch.setattr(svg_art, "render_element", spy)
    with tempfile.TemporaryDirectory() as d:
        app.render_headless(os.path.join(d, "ext.png"))
    assert ext.visible is True
    assert any(n is ext_node for n in drawn)           # David's ext-view art drawn
    app.stop()


def test_ext_view_in_router_leaves_shell_z_order_unchanged():
    """The EXT view lives INSIDE the content router (like the DRADIS drop-in +
    placeholder), so the shell's layer stack / z-order is unchanged and the
    master-mask frame still bounds it ([CXC-CIC-WIDGET])."""
    app, comp, cic = _app(_scene())
    order = [c.widget_type for c in cic.children]
    assert order == ["rectangle", "cic_content", "cic_edge_gauges",
                     "cic_screen_area", "cic_frame", "segmented_tab_row",
                     "segmented_tab_row", "cic_status_bar", "cic_button_bar",
                     "radio_button_group", "cic_dialog"]
    # the EXT view is a child of the content router, not a new shell layer
    assert cic.ext_view in cic.content_router.children
    assert cic.ext_view not in cic.children
    app.stop()


def test_ext_content_renders_in_screen_area_chrome_intact(monkeypatch):
    """CIC-EXT pixel proof ([CXC-CIC-RADIO-MODE]): with EXT selected David's ext-view
    external-view image renders inside the screen hole (distinct from the STANDARD
    content + carrying lit image pixels), while the amber frame ring stays amber —
    the external view lands in the screen, the chrome is untouched.  Forces the
    bundle so it is deterministic + store-independent."""
    from PIL import Image
    from compose_projects.bsg.widgets.cic import svg_art
    from compose_projects.bsg.widgets.colonial_console import compute_geometry
    _serve_screens_locally(monkeypatch)                # bus-referenced image, local
    monkeypatch.setattr(svg_art, "_doc_cache", {})
    app, comp, cic = _app(_scene())
    g = compute_geometry(1280, 960)
    sa = g.screen_area
    pts = [(int(sa.x + sa.w * fx), int(sa.y + sa.h * fy))
           for fx in (0.3, 0.4, 0.5, 0.6, 0.7) for fy in (0.3, 0.45, 0.6)]

    def _render(idx):
        app.publish("colonial_console/cic.1.helm/radio", {"index": idx},
                    source="t")
        app.drain_updates()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "x.png")
            app.render_headless(out)
            return Image.open(out).convert("RGB")

    ext_img = _render(2)                                # EXT
    std_img = _render(1)                                # STD (standard nav content)

    def is_amber(px):
        return px[0] > 90 and px[1] < 90 and px[2] < 60

    # the amber frame ring is still amber (chrome intact under EXT)
    assert is_amber(ext_img.getpixel((6, 480))), ext_img.getpixel((6, 480))
    # the EXT screen content DIFFERS from the STD content across the screen area —
    # EXT is showing its own external view, not the standard screen (the defect)
    differing = sum(1 for p in pts
                    if ext_img.getpixel(p) != std_img.getpixel(p))
    assert differing >= len(pts) // 2, differing
    # and the ext-view image carries lit (non-black, non-amber) pixels
    lit = [p for p in pts
           if sum(ext_img.getpixel(p)) > 120 and not is_amber(ext_img.getpixel(p))]
    assert lit, [ext_img.getpixel(p) for p in pts]
    app.stop()
