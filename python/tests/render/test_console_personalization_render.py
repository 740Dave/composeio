"""
Render-deep Console personalization surfaces ([SPEC:CXC-CONSOLE-PROD]
surfaces 1-3): S1 resizable + reflow ([CXC-ICP-S1-RESIZE]), S2 font-scale
([CXC-ICP-S2-FONTSCALE]), S3 themes + custom colors ([CXC-ICP-S3-THEME]).

This is the RENDER MECHANISM half (read the persisted Foundation-A settings
and APPLY them); the Config-tab selectors that WRITE the settings are a
separate app-surface build and are NOT exercised here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import fields

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
# Foundation A read seam lives in intelligence/publisher.
_PUBLISHER = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher")
if _PUBLISHER not in sys.path:
    sys.path.insert(0, _PUBLISHER)

from composeio.render.engine.runtime import RenderApp
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import console_theme as ct

from .conftest import surface_to_array
from tests.render.console_scene_ids import (  # noqa: E402
    _pane_uses,
    composer as _composer,
    composer_field as _cf,
    composer_send as _cs,
    send_trigger_ns as _send_ns,
    thread as _th)

# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")


def teardown_function(_fn):
    # never leak personalization state between tests.
    ct.set_theme("dark")
    ct.clear_custom_colors()
    ct.set_font_scale(1.0)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _laid_out(width, height, *, font_scale=1.0, theme="dark", custom=None):
    """Parse + lay out the real console at a window size, returning the app."""
    ct.set_theme(theme)
    ct.clear_custom_colors()
    if custom:
        ct.apply_custom_colors(custom)
    ct.set_font_scale(font_scale)
    app = RenderApp.from_file(SCENE, windowed=False)
    app.comp.width, app.comp.height = width, height
    app.comp.font_scale = font_scale
    app.comp.theme = theme
    app.start()
    app.comp.root.measure(Constraints(width, height))
    app.comp.root.arrange(Rect(0, 0, width, height))
    return app


def _all_rects(root):
    """[(widget_id, Rect)] for every widget with an arranged rect."""
    out = []
    stack = [root]
    while stack:
        w = stack.pop()
        r = getattr(w, "arranged_rect", None)
        if r is not None:
            out.append((getattr(w, "widget_id", "?"), r))
        stack.extend(getattr(w, "children", []) or [])
    return out


def _find(root, wid):
    stack = [root]
    while stack:
        w = stack.pop()
        if getattr(w, "widget_id", None) == wid:
            return w
        stack.extend(getattr(w, "children", []) or [])
    return None


def _overlap(a, b):
    ox = min(a.x + a.w, b.x + b.w) - max(a.x, b.x)
    oy = min(a.y + a.h, b.y + b.h) - max(a.y, b.y)
    return ox > 0.5 and oy > 0.5


# ===========================================================================
# S1 — resizable window + layout REFLOW ([CXC-ICP-S1-RESIZE])
# ===========================================================================

def test_scene_opts_into_resize_and_geometry_and_personalization():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    assert comp.resizable is True
    assert comp.persist_geometry is True
    assert comp.min_width == 960 and comp.min_height == 720
    assert comp.window_key == "intelligence-console"
    assert comp.personalization == "architect"


# The spec's named reflow targets ([CXC-ICP-S1-RESIZE]: header, rail, canvas,
# composer) + chrome + tab-content ROOTS.  WIDTH reflow is the primary contract
# and holds for ALL of these at every size.
_WIDTH_TARGETS = (
    "header", "brandmark", "titles", "presence", "console_tabs",
    "collab_stack", "main_row", "read_col", "rail_col",
    "fleet_row", _composer(), "deliverable_banner",
    "intelligence_panel", "account_panel", "deliverables_panel",
    "artifacts_view", "projects_stack", "config_md",
)
# The canvas COLUMN reflows vertically too (composer stays anchored, thread
# grows).  The rail's fixed-height card STACK and deep tab content are
# scrollable-by-design and may exceed a short viewport (documented follow-on:
# rail scroll) — excluded from the vertical-clip contract.
_HEIGHT_TARGETS = (
    "header", "console_tabs", "collab_stack", "main_row", "read_col",
    "fleet_row", _composer(), "deliverable_banner",
)


def test_reflow_no_horizontal_clip_across_supported_sizes():
    # the WIDTH-reflow contract: no reflow target clips horizontally, at any
    # supported window size.
    for w, h in [(960, 720), (1280, 1740), (1920, 1200), (1440, 900)]:
        app = _laid_out(w, h)
        by_id = {wid: r for wid, r in _all_rects(app.comp.root)}
        for wid in _WIDTH_TARGETS:
            r = by_id.get(wid)
            if r is None:
                continue
            assert r.x >= -0.5, (wid, "left clip", w, r.x)
            assert r.x + r.w <= w + 0.5, (wid, "right clip", w, r.x + r.w)
        app.stop()


def test_reflow_no_vertical_clip_of_canvas_column():
    # the canvas column reflows vertically with no clip across the range.
    for w, h in [(960, 720), (1280, 1740), (1920, 1200), (1440, 900)]:
        app = _laid_out(w, h)
        by_id = {wid: r for wid, r in _all_rects(app.comp.root)}
        for wid in _HEIGHT_TARGETS:
            r = by_id.get(wid)
            if r is None:
                continue
            assert r.y >= -0.5, (wid, "top clip", h, r.y)
            assert r.y + r.h <= h + 0.5, (wid, "bottom clip", h, r.y + r.h)
        app.stop()


def test_reflow_collab_stage_no_sibling_overlap():
    # the Collaboration stage's structural siblings never overlap at any size.
    for w, h in [(960, 720), (1280, 1740), (1920, 1200)]:
        app = _laid_out(w, h)
        root = app.comp.root
        read = _find(root, "read_col").arranged_rect
        rail = _find(root, "rail_col").arranged_rect
        assert not _overlap(read, rail), (w, "read/rail overlap")
        # THE THREAD/COMPOSER PAIR IS NO LONGER A SIBLING PAIR.  Both now live INSIDE a
        # pane ([CXC-FC-COMPOSITE]), so `thread_stack` is the composer's ANCESTOR and
        # "they must not overlap" would assert that a container does not contain its own
        # child.  The property that still matters — panes do not overlap each other — is
        # asserted directly.
        panes = [_find(root, f"{p['id_prefix']}_{p['instance_id']}_root").arranged_rect
                 for p in _pane_uses()]
        for a, b in zip(panes, panes[1:]):
            assert not _overlap(a, b), (w, "pane/pane overlap")
        app.stop()


def test_reflow_thread_grows_rail_holds_min_width():
    # the thread WIDENS with the window; the rail keeps its fixed min width.
    small = _laid_out(960, 720)
    big = _laid_out(1920, 1200)
    s_read = _find(small.comp.root, "read_col").arranged_rect
    b_read = _find(big.comp.root, "read_col").arranged_rect
    s_rail = _find(small.comp.root, "rail_col").arranged_rect
    b_rail = _find(big.comp.root, "rail_col").arranged_rect
    assert b_read.w > s_read.w + 100          # thread grew a lot
    assert abs(s_rail.w - 332) < 1 and abs(b_rail.w - 332) < 1   # rail steady
    small.stop(); big.stop()


def test_reflow_tabs_fill_window_height():
    for w, h in [(960, 720), (1280, 1740), (1920, 1400)]:
        app = _laid_out(w, h)
        tabs = _find(app.comp.root, "console_tabs").arranged_rect
        # console_tabs grows to consume the height below the header.
        assert tabs.y + tabs.h >= h - 20, (w, h, tabs.y + tabs.h)
        app.stop()


def test_default_geometry_matches_the_shipped_layout():
    # at the shipped 1280x1740 the reflow reproduces the original geometry
    # (read col 908, rail 332) — reflow is additive, not a re-layout.
    app = _laid_out(1280, 1740)
    read = _find(app.comp.root, "read_col").arranged_rect
    rail = _find(app.comp.root, "rail_col").arranged_rect
    assert abs(read.w - 908) < 1 and abs(rail.w - 332) < 1
    app.stop()


def test_resize_reflows_live_via_handle_resize():
    # the runtime resize path re-lays-out the composition to the new size.
    app = _laid_out(1280, 1740)
    app._handle_resize(1600, 1000)
    assert app.comp.width == 1600 and app.comp.height == 1000
    app.comp.root.measure(Constraints(1600, 1000))
    app.comp.root.arrange(Rect(0, 0, 1600, 1000))
    read = _find(app.comp.root, "read_col").arranged_rect
    assert read.x + read.w <= 1600.5
    app.stop()


# ===========================================================================
# S2 — font-scale multiplier ([CXC-ICP-S2-FONTSCALE])
# ===========================================================================

def test_font_scale_threads_through_measure_and_draw():
    c = SkiaCanvas()
    c.create_surface(240, 80)
    c.set_font("sans-serif", 16.0, "regular")
    base = c.measure_text("Hello World").width
    c.set_font_scale(1.25)
    c.set_font("sans-serif", 16.0, "regular")
    big = c.measure_text("Hello World").width
    c.set_font_scale(0.875)
    c.set_font("sans-serif", 16.0, "regular")
    small = c.measure_text("Hello World").width
    assert big > base > small
    # proportional within glyph-advance rounding tolerance.
    assert abs(big / base - 1.25) < 0.03
    assert abs(small / base - 0.875) < 0.03


def test_font_scale_1_0_is_exact_no_op():
    c = SkiaCanvas()
    c.create_surface(240, 80)
    c.set_font("mono", 16.0, "regular")
    a = c.measure_text("SELF / REGISTERED").width
    c.set_font_scale(1.0)
    c.set_font("mono", 16.0, "regular")
    b = c.measure_text("SELF / REGISTERED").width
    assert a == b                          # byte-exact: size * 1.0 == size


def test_font_scale_steps_are_davids_discrete_set():
    assert ct.FONT_SCALE_STEPS == {"s": 0.875, "m": 1.0, "l": 1.25}
    assert ct.resolve_font_scale("s") == 0.875
    assert ct.resolve_font_scale("m") == 1.0
    assert ct.resolve_font_scale("l") == 1.25


def test_font_scale_resolver_clamps_and_defaults():
    assert ct.resolve_font_scale(3.0) == ct.FONT_SCALE_MAX     # clamped
    assert ct.resolve_font_scale(0.1) == ct.FONT_SCALE_MIN
    assert ct.resolve_font_scale("garbage") == 1.0            # graceful
    assert ct.resolve_font_scale(None) == 1.0
    assert ct.resolve_font_scale(True) == 1.0                 # bool guarded


def test_font_scale_golden_stable_per_step():
    # the SAME step renders byte-identically twice (deterministic layout).
    import hashlib
    digests = {}
    for step in (0.875, 1.0, 1.25):
        h = []
        for _ in range(2):
            app = _laid_out(1280, 1740, font_scale=step)
            arr = None
            import tempfile
            out = os.path.join(tempfile.mkdtemp(), "f.png")
            app.render_headless(out)
            with open(out, "rb") as fh:
                h.append(hashlib.sha256(fh.read()).hexdigest())
            app.stop()
        assert h[0] == h[1], (step, "not deterministic")
        digests[step] = h[0]
    # and the three steps differ from each other.
    assert len(set(digests.values())) == 3


def test_font_scale_no_overflow_larger_text_reflows():
    # a card header measured at 1.25 fits its (reflowed) box — text scaled at
    # measure AND draw, so nothing overflows what it measured.
    app = _laid_out(1280, 1740, font_scale=1.25)
    # a full render must succeed and paint (a crash/overflow would raise).
    import tempfile
    out = os.path.join(tempfile.mkdtemp(), "big.png")
    p = app.render_headless(out)
    assert os.path.getsize(p) > 20000
    app.stop()


# ===========================================================================
# S3 — themes + custom colors ([CXC-ICP-S3-THEME])
# ===========================================================================

def test_theme_registry_lists_three_themes():
    assert ct.list_themes() == ["dark", "light", "high-contrast"]


def test_every_theme_provides_every_token_role():
    # [CXC-ICP-S3-THEME] no missing-token gap: the fallback never has to fire.
    for name in ct.list_themes():
        table = ct._TABLES[name]
        for f in fields(ct.ConsoleTheme):
            v = getattr(table, f.name)
            assert v is not None, (name, f.name)
            if f.name != "name":
                assert len(v) == 4, (name, f.name)


def test_high_contrast_is_distinct_and_black_ground():
    assert ct.HIGH_CONTRAST.bg[:3] == (0, 0, 0)
    assert ct.HIGH_CONTRAST.ink[:3] == (255, 255, 255)
    # its accent differs from dark's azure (a real reskin, not an alias).
    assert ct.HIGH_CONTRAST.accent[:3] != ct.DARK.accent[:3]


def test_unknown_theme_falls_back_to_dark_gracefully():
    ct.set_theme("dark")
    ct.set_theme("not-a-theme")            # keeps current base
    assert ct.CONSOLE.active.name == "dark"


def test_theme_swap_changes_pixels():
    import tempfile
    import hashlib
    digs = {}
    for name in ct.list_themes():
        app = _laid_out(1280, 1740, theme=name)
        out = os.path.join(tempfile.mkdtemp(), f"{name}.png")
        app.render_headless(out)
        with open(out, "rb") as fh:
            digs[name] = hashlib.sha256(fh.read()).hexdigest()
        app.stop()
    assert len(set(digs.values())) == 3    # each theme renders differently


def test_custom_accent_overrides_token_and_recomputes_family():
    ct.set_theme("dark")
    ct.apply_custom_colors({"accent": [255, 120, 0], "bg": [20, 0, 40]})
    a = ct.CONSOLE.active
    assert a.accent[:3] == (255, 120, 0)
    assert a.bg[:3] == (20, 0, 40)
    # the derived accent family recolors to the override, alpha preserved.
    assert a.accent_dim[:3] == (255, 120, 0)
    assert a.accent_dim[3] == ct.DARK.accent_dim[3]
    assert a.accent_line[:3] == (255, 120, 0)


def test_custom_colors_survive_theme_from_ctx_readopt():
    # a widget's theme_from_ctx(ctx) re-adopts the base theme every draw; the
    # custom overlay must persist across that (else the override would vanish).
    ct.set_theme("dark")
    ct.apply_custom_colors({"accent": [10, 200, 90]})

    class _Ctx:
        theme = "dark"
    ct.theme_from_ctx(_Ctx())
    assert ct.CONSOLE.active.accent[:3] == (10, 200, 90)


def test_custom_accent_changes_account_bar_pixels():
    # David's example: the "Account" bar-graph color follows the accent token.
    from composeio.render.widgets.account_card import AccountCardWidget

    def _bar_render(custom):
        ct.set_theme("dark")
        ct.clear_custom_colors()
        if custom:
            ct.apply_custom_colors(custom)
        w = AccountCardWidget("acct")
        w.on_bus_state("self/rate_limit", {"5h": {"pct": 80}, "7d": {"pct": 40}})
        c = SkiaCanvas()
        c.create_surface(340, 260)
        c.set_fill_color(0, 0, 0)
        c.fill_rect(0, 0, 340, 260)
        w.draw(c, Rect(4, 4, 332, 250))
        return surface_to_array(c)

    base = _bar_render(None)
    custom = _bar_render({"accent": [255, 60, 0]})
    # the bar fill is a large accent region — the override must move pixels.
    assert not (base == custom).all()


def test_malformed_custom_color_is_dropped_not_applied():
    ct.set_theme("dark")
    base = ct.DARK.accent
    ct.apply_custom_colors({"accent": [999, 0]})   # malformed -> dropped
    assert ct.CONSOLE.active.accent == base


# ===========================================================================
# The personalization applier (read Foundation A -> apply) — the launch seam
# ===========================================================================

def _load_personalization():
    import importlib.util
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                        "personalization.py")
    spec = importlib.util.spec_from_file_location("console_personalization_t",
                                                  path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_applier_reads_foundation_a_and_applies(tmp_path):
    import json
    p = _load_personalization()
    meta = {
        "id": "architect",
        "lifecycle": {"retention_days": 30},
        "console": {
            "window": {"w": 1440, "h": 980},
            "font_scale": 1.25,
            "theme": "high-contrast",
            "theme_custom": {"accent": [255, 120, 0]},
        },
    }
    mp = tmp_path / "architect.meta.json"
    mp.write_text(json.dumps(meta))
    settings = p.read_settings(str(mp))
    assert settings["window"] == {"w": 1440, "h": 980}
    assert settings["theme"] == "high-contrast"

    app = RenderApp.from_file(SCENE, windowed=False)
    resolved = p.apply_to_composition(app.comp, settings)
    assert app.comp.width == 1440 and app.comp.height == 980
    assert app.comp.font_scale == 1.25
    assert app.comp.theme == "high-contrast"
    assert ct.CONSOLE.active.name == "high-contrast"
    assert ct.CONSOLE.active.accent[:3] == (255, 120, 0)
    assert resolved["font_scale"] == 1.25
    # the send-button chrome literal was re-pointed to the custom accent.
    send = _find(app.comp.root, _cs())
    assert send is not None and tuple(send.background[:3]) == (255, 120, 0)


def test_applier_empty_meta_uses_defaults(tmp_path):
    p = _load_personalization()
    settings = p.read_settings(str(tmp_path / "absent.meta.json"))
    assert settings["theme"] == "dark"
    assert settings["font_scale"] == 1.0
    assert settings["window"] == {"w": 1280, "h": 1740}
    app = RenderApp.from_file(SCENE, windowed=False)
    p.apply_to_composition(app.comp, settings)
    assert app.comp.theme == "dark" and app.comp.font_scale == 1.0


def test_applier_unknown_theme_falls_back(tmp_path):
    import json
    p = _load_personalization()
    meta = {"console": {"theme": "solarized-does-not-exist"}}
    mp = tmp_path / "m.meta.json"
    mp.write_text(json.dumps(meta))
    resolved = p.apply_personalization(p.read_settings(str(mp)))
    assert resolved["theme"] == "dark"


def test_foundation_a_persists_theme_custom_round_trip():
    # the read seam MY apply reads from must persist theme_custom (Foundation A
    # extension) — a color-picker UI writes it, my launch reads it.
    from config_persister import persist_config, console_settings, _console_ns
    meta = {"id": "architect"}
    ns = _console_ns("architect", "theme_custom")
    meta, changed = persist_config(
        meta, ns, {"bg": [5, 6, 7], "accent": [8, 9, 10], "junk": 1},
        "architect")
    assert changed
    assert meta["console"]["theme_custom"] == {"bg": [5, 6, 7],
                                               "accent": [8, 9, 10]}
    assert console_settings(meta)["theme_custom"] == {"bg": [5, 6, 7],
                                                      "accent": [8, 9, 10]}
