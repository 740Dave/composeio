"""
Compliance tests for the standalone Decisions viewer's resizable window +
persisted geometry ([SPEC:CXC-DVL] [CXC-DVL-WINDOW] / [CXC-DVL-GEOMETRY];
verify [CXC-DVL-VERIFY-WINDOW]).

Machine tier ONLY (the glass tier — reopen-restores-size, drag-to-resize — is a
human check listed in the final report).  Exercises the PURE geometry contract
with an injected store (no real SDL window): write-on-change + debounce,
restore-on-launch precedence, fail-safe on malformed/missing, off-screen clamp;
plus the scene default (1440x900) and that the runtime passes the resizable flag
+ min size through to the window.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.engine.geometry import (
    DEFAULT_ORIGIN_INSET, GeometryWriteThrottle, JsonFileGeometryStore,
    NullGeometryStore, WindowGeometry, clamp_onscreen, default_origin,
    resolve_geometry)

DECISIONS_SCENE = os.path.join(
    REPO_ROOT, "python", "composeio", "render", "examples",
    "decisions_tab.compose.yaml")


class MemStore:
    """An in-memory GeometryStore double (the store BACKEND is mocked so these
    tests are independent of [DD-DVL-GEOMETRY-STORE])."""

    def __init__(self):
        self.saved = {}

    def load(self, key):
        return self.saved.get(key)

    def save(self, key, geom):
        self.saved[key] = geom
        return True


# ---------------------------------------------------------------------------
# WindowGeometry record — validity + round-trip
# ---------------------------------------------------------------------------

def test_geometry_validity_and_roundtrip():
    g = WindowGeometry(200, 120, 1600, 1000)
    assert g.is_valid and g.has_pos
    assert WindowGeometry.from_dict(g.to_dict()) == g
    # zero/negative size is MALFORMED -> None (never a degenerate window)
    assert not WindowGeometry(0, 0, 0, 0).is_valid
    assert WindowGeometry.from_dict({"x": 1, "y": 2, "w": 0, "h": 10}) is None
    assert WindowGeometry.from_dict({"w": 800, "h": 600}) == \
        WindowGeometry(None, None, 800, 600)
    # a size-only record is valid (position falls back), pos absent
    assert not WindowGeometry(None, None, 800, 600).has_pos


def test_geometry_from_dict_is_fail_safe():
    for bad in (None, {}, {"x": 1}, {"w": "nope", "h": 5},
                {"w": None, "h": None}, "garbage", 42):
        assert WindowGeometry.from_dict(bad) is None


# ---------------------------------------------------------------------------
# Restore-on-launch PRECEDENCE ([CXC-DVL-GEOMETRY])
# ---------------------------------------------------------------------------

def test_precedence_persisted_wins_size_and_position():
    persisted = WindowGeometry(200, 120, 1600, 1000)
    geom, source = resolve_geometry(persisted, launcher_pos=(50, 60),
                                    default_size=(1440, 900))
    assert source == "persisted"
    # persisted OVERRIDES the launcher's beside-console placement AND the default
    assert (geom.x, geom.y, geom.w, geom.h) == (200, 120, 1600, 1000)


def test_precedence_no_persisted_falls_back_to_launcher_and_default_size():
    geom, source = resolve_geometry(None, launcher_pos=(50, 60),
                                    default_size=(1440, 900))
    assert source == "launcher"
    assert (geom.x, geom.y, geom.w, geom.h) == (50, 60, 1440, 900)


def test_precedence_neither_uses_default_size_centered():
    geom, source = resolve_geometry(None, launcher_pos=None,
                                    default_size=(1440, 900))
    assert source == "default"
    assert (geom.w, geom.h) == (1440, 900) and not geom.has_pos


def test_precedence_malformed_persisted_fails_safe_to_default():
    # a malformed record (size 0) is NOT valid -> the default applies, no raise
    bad = WindowGeometry(10, 10, 0, 0)
    geom, source = resolve_geometry(bad, launcher_pos=(5, 6),
                                    default_size=(1440, 900))
    assert source == "launcher" and (geom.w, geom.h) == (1440, 900)


# ---------------------------------------------------------------------------
# FIRST-LAUNCH policy origin ([CXC-DVL-GEOMETRY], David 2026-08-05)
#
# A window with nothing saved and no launcher placement opens at the top-left
# of the display's VISIBLE WORK AREA + a fixed inset — below the macOS menu
# bar, never OS-centered.  Shared by EVERY persist_geometry window (the fix
# came from the launch-bar ribbon, but nothing about it is ribbon-specific).
# ---------------------------------------------------------------------------

def test_default_origin_is_work_area_top_left_plus_inset():
    # macOS: 25px menu bar at the top, Dock at the bottom -> usable y=25
    work = [(0, 25, 3840, 2050)]
    assert default_origin(work, (96, 932)) == (20, 45)
    assert DEFAULT_ORIGIN_INSET == 20
    # the RAW display origin would tuck the window under the menu bar; the
    # work-area origin never does.
    assert default_origin(work, (96, 932))[1] > 25


def test_default_origin_uses_the_primary_display_and_honors_its_offset():
    # a work area that does not start at 0,0 (secondary display / left dock)
    work = [(1920, 40, 1920, 1000), (0, 25, 3840, 2050)]
    assert default_origin(work, (400, 300)) == (1940, 60)


def test_default_origin_degrades_and_never_pushes_a_window_off_screen():
    assert default_origin([], (800, 600)) is None
    assert default_origin(None, (800, 600)) is None
    assert default_origin([("bad", 0, 0, 0)], (800, 600)) is None
    # a window as large as (or larger than) the work area sticks to its corner
    assert default_origin([(0, 25, 800, 600)], (800, 600)) == (0, 25)
    assert default_origin([(0, 25, 800, 600)], (1200, 900)) == (0, 25)
    assert default_origin([(0, 25, 810, 610)], (800, 600)) == (10, 35)


def test_precedence_default_origin_is_tier_three():
    # persisted > launcher > default_pos > OS-centered
    persisted = WindowGeometry(200, 120, 1600, 1000)
    g, s = resolve_geometry(persisted, (50, 60), (1440, 900),
                            default_pos=(20, 45))
    assert s == "persisted" and (g.x, g.y) == (200, 120)
    g, s = resolve_geometry(None, (50, 60), (1440, 900), default_pos=(20, 45))
    assert s == "launcher" and (g.x, g.y) == (50, 60)
    g, s = resolve_geometry(None, None, (1440, 900), default_pos=(20, 45))
    assert s == "screen-origin" and (g.x, g.y, g.w, g.h) == (20, 45, 1440, 900)
    # no policy origin available (no display report) -> the pre-existing
    # OS-centered default, byte-for-byte the old behavior.
    g, s = resolve_geometry(None, None, (1440, 900))
    assert s == "default" and not g.has_pos


def test_fixed_size_scene_restores_position_but_not_a_stale_size():
    """A NON-resizable scene's size is a SCENE fact — a persisted size must
    never pin it to yesterday's dimensions after the scene grows.  (The ribbon
    grew 72x486 -> 96x932; a stale record would have re-shrunk it.)"""
    from composeio.render.engine.parser import parse_widget_tree
    from composeio.render.engine.runtime import RenderApp

    scene = {"app": {"id": "fixed-win"},
             "design": {"width": 300, "height": 400, "resizable": False,
                        "persist_geometry": True, "window_key": "fixed-win"},
             "widgets": [{"type": "label", "id": "l", "text": "x"}]}
    comp = parse_widget_tree(scene)
    app = RenderApp(comp, windowed=False)
    store = MemStore()
    store.saved["fixed-win"] = WindowGeometry(700, 250, 120, 90)
    app._geometry_store = store
    pos = app._restore_geometry(None)
    assert pos == (700, 250)                       # position honored
    assert (comp.width, comp.height) == (300, 400)  # scene size wins
    app.stop()


class _MacLikeCanvas:
    """A macOS-like WM stand-in for the frame-offset pass.

    Two behaviors that together broke the naive placement and are the reason
    the probe exists: (1) it NEVER reports border size (SDL's Cocoa backend
    returns -1), and (2) it refuses to place the window's CONTENT above
    ``menu_bottom + titlebar`` — i.e. it will not tuck a title bar under the
    menu bar."""

    def __init__(self, w=96, h=932, menu_bottom=25, titlebar=28):
        self.menu_bottom, self.titlebar = menu_bottom, titlebar
        self.w, self.h = w, h
        self.pos = (0, 0)
        self.moves = []

    def get_border_size(self):
        return None

    def set_position(self, x, y):
        self.pos = (int(x), max(int(y), self.menu_bottom + self.titlebar))
        self.moves.append(self.pos)

    def get_bounds(self):
        return (self.pos[0], self.pos[1], self.w, self.h)


def _app_with_policy_origin(source, origin, work=(0, 25, 3840, 2050)):
    from composeio.render.engine.parser import parse_widget_tree
    from composeio.render.engine.runtime import RenderApp
    scene = {"app": {"id": "w"},
             "design": {"width": 96, "height": 932, "window_key": "w"},
             "widgets": [{"type": "label", "id": "l", "text": "x"}]}
    app = RenderApp(parse_widget_tree(scene), windowed=False)
    app.canvas = _MacLikeCanvas()
    app.canvas.set_position(*origin)          # as SDL_CreateWindow would
    app._policy_work_area = work
    app._restored_geometry = WindowGeometry(origin[0], origin[1], 96, 932)
    app._geometry_source = source
    return app


def test_policy_origin_lands_the_FRAME_on_the_inset_not_the_content():
    """The measured macOS failure: asking for content y=45 (work 25 + 20) got
    y=53 — the frame FLUSH against the menu bar, 0px gap, not 20.  The probe
    recovers the 28px title bar and re-places so the FRAME is at y=45."""
    app = _app_with_policy_origin("screen-origin", (20, 45))
    assert app.canvas.get_bounds()[1] == 53          # the WM's clamp
    app._apply_policy_origin_frame_offset()
    x, y, _, _ = app.canvas.get_bounds()
    assert (x, y) == (20, 45 + 28)                   # content pushed down
    assert (x - 0, y - 28) == (20, 45)               # ...so the FRAME is at 20,45
    app.stop()


def test_frame_offset_never_touches_a_restored_position():
    """A restored position is already in SDL's content coordinates; offsetting
    it would drift the window one title bar per relaunch."""
    app = _app_with_policy_origin("persisted", (640, 285))
    before = app.canvas.get_bounds()
    app._apply_policy_origin_frame_offset()
    assert app.canvas.get_bounds() == before
    assert app.canvas.moves == [(640, 285)]          # no re-place, no probe
    app.stop()


def test_frame_offset_is_a_noop_on_a_borderless_surface():
    """A kiosk/borderless WM neither reports borders nor clamps -> offset
    (0,0), and the window still ends up exactly on the policy origin."""
    app = _app_with_policy_origin("screen-origin", (20, 45))
    app.canvas.titlebar = 0
    app.canvas.menu_bottom = 0
    app.canvas.set_position(20, 45)
    assert app._window_decoration_offset() == (0, 0)
    app._apply_policy_origin_frame_offset()
    assert app.canvas.get_bounds()[:2] == (20, 45)
    app.stop()


# ---------------------------------------------------------------------------
# Off-screen clamp ([CXC-DVL-GEOMETRY] FAIL-SAFE)
# ---------------------------------------------------------------------------

def test_offscreen_position_clamped_onto_a_visible_display():
    displays = [(0, 0, 1920, 1080)]
    # saved on a since-disconnected second monitor at x=3000
    off = WindowGeometry(3000, 200, 1200, 800)
    clamped = clamp_onscreen(off, displays)
    assert clamped.x is not None and 0 <= clamped.x < 1920
    assert clamped.w == 1200 and clamped.h == 800     # size preserved
    # a visible position is returned untouched
    ok = WindowGeometry(100, 80, 1200, 800)
    assert clamp_onscreen(ok, displays) == ok


def test_resolve_clamps_persisted_when_displays_given():
    displays = [(0, 0, 1920, 1080)]
    persisted = WindowGeometry(4000, 100, 1000, 700)
    geom, source = resolve_geometry(persisted, None, (1440, 900), displays)
    assert source == "persisted" and 0 <= geom.x < 1920


# ---------------------------------------------------------------------------
# Write-on-change debounce ([CXC-DVL-GEOMETRY] WRITE-ON-CHANGE)
# ---------------------------------------------------------------------------

def test_write_throttle_coalesces_a_drag_and_flushes_final():
    thr = GeometryWriteThrottle(min_interval=0.5)
    g1 = WindowGeometry(0, 0, 800, 600)
    # first submit writes immediately (last_write None)
    assert thr.submit(g1, now=100.0) == g1
    # a drag: many events inside the 500ms window collapse to zero writes
    for i in range(1, 10):
        assert thr.submit(WindowGeometry(i, i, 800 + i, 600),
                          now=100.0 + i * 0.02) is None
    # poll before the interval elapses: still nothing
    assert thr.poll(now=100.3) is None
    # poll AFTER the interval: the LATEST pending geometry writes (coalesced)
    due = thr.poll(now=100.6)
    assert due is not None and due.w == 809
    # a final flush on close writes any still-pending geometry unconditionally
    thr.submit(WindowGeometry(50, 50, 1600, 1000), now=100.61)   # within interval
    final = thr.flush()
    assert final is not None and (final.w, final.h) == (1600, 1000)


# ---------------------------------------------------------------------------
# JSON-file store = the meta-object seam (preserves sibling keys)
# ---------------------------------------------------------------------------

def test_json_store_roundtrip_preserves_sibling_meta_keys(tmp_path):
    import json
    meta = tmp_path / "architect.meta.json"
    meta.write_text(json.dumps({"id": "architect",
                                "lifecycle": {"compact_threshold": 0.93}}),
                    encoding="utf-8")
    store = JsonFileGeometryStore(str(meta))
    assert store.load("decisions-viewer") is None       # first launch
    g = WindowGeometry(200, 120, 1600, 1000)
    assert store.save("decisions-viewer", g) is True
    # geometry persisted AND the meta-object's other keys survived (the
    # config_persister meta-object seam, [DD-DVL-GEOMETRY-STORE])
    on_disk = json.loads(meta.read_text(encoding="utf-8"))
    assert on_disk["id"] == "architect"
    assert on_disk["lifecycle"]["compact_threshold"] == 0.93
    assert on_disk["window_geometry"]["decisions-viewer"]["w"] == 1600
    # a fresh store reads it back exactly (restore-on-launch)
    assert JsonFileGeometryStore(str(meta)).load("decisions-viewer") == g


def test_json_store_missing_or_corrupt_file_is_none_not_a_crash(tmp_path):
    assert JsonFileGeometryStore(str(tmp_path / "nope.json")).load("k") is None
    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{ not json", encoding="utf-8")
    assert JsonFileGeometryStore(str(corrupt)).load("k") is None


def test_null_store_never_restores_or_writes():
    s = NullGeometryStore()
    assert s.load("k") is None
    assert s.save("k", WindowGeometry(0, 0, 10, 10)) is False


# ---------------------------------------------------------------------------
# Scene default size + resizable/min-size wiring
# ---------------------------------------------------------------------------

def test_scene_default_is_1440x1080_resizable_and_persistent():
    # David 2026-07-29: the Decisions viewer opens too short at 1440x900 — bump
    # the default to >= 1280x1080 (kept 1440 wide, raised to 1080 tall).
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(DECISIONS_SCENE)
    assert (comp.width, comp.height) == (1440, 1080)    # [CXC-DVL-WINDOW]
    assert comp.width >= 1280 and comp.height >= 1080
    assert comp.resizable is True
    assert (comp.min_width, comp.min_height) == (720, 480)
    assert comp.persist_geometry is True
    assert comp.window_key == "decisions-viewer"


def test_runtime_restore_uses_injected_store_and_precedence(monkeypatch):
    # verify.machine (b)/(c): a launch with a persisted record RESTORES it and
    # it takes precedence; a launch with NO record uses the scene default +
    # launcher pos.  Exercised on the runtime with an injected store, no window.
    from composeio.render.engine.runtime import RenderApp
    from composeio.render.engine.parser import parse_compose_yaml

    comp = parse_compose_yaml(DECISIONS_SCENE)
    app = RenderApp(comp, windowed=False)
    store = MemStore()
    app._geometry_store = store

    # (c) no record -> default size (1440x1080), launcher pos honored
    pos = app._restore_geometry((50, 60))
    assert (comp.width, comp.height) == (1440, 1080) and pos == (50, 60)

    # (b) a persisted record -> restored size + pos, overriding launcher pos
    store.saved["decisions-viewer"] = WindowGeometry(300, 140, 1600, 1000)
    pos = app._restore_geometry((50, 60))
    assert (comp.width, comp.height) == (1600, 1000) and pos == (300, 140)
    app.stop()


def test_runtime_resize_writes_geometry_to_store():
    # verify.machine (a): a resize event WRITES {x,y,w,h} to the store.
    from composeio.render.engine.runtime import RenderApp
    from composeio.render.engine.parser import parse_compose_yaml

    class FakeCanvas:
        def __init__(self):
            self._bounds = (200, 120, 1440, 900)

        def resize_surface(self, w, h):
            self._bounds = (self._bounds[0], self._bounds[1], w, h)

        def get_bounds(self):
            return self._bounds

    comp = parse_compose_yaml(DECISIONS_SCENE)
    app = RenderApp(comp, windowed=False)
    store = MemStore()
    app._geometry_store = store
    app.canvas = FakeCanvas()
    app._handle_resize(1600, 1000)
    assert comp.width == 1600 and comp.height == 1000     # re-layout to new size
    saved = store.saved.get("decisions-viewer")
    assert saved is not None and (saved.w, saved.h) == (1600, 1000)
    assert (saved.x, saved.y) == (200, 120)
    app.stop()


def test_runtime_passes_resizable_and_min_size_to_window(monkeypatch):
    # The standalone/windowed path constructs the window with the resizable
    # flag set + the scene min size ([CXC-DVL-WINDOW]).  Captured via a
    # window stub that records ctor kwargs then aborts before any draw.
    import composeio.render.window as window_mod
    from composeio.render.engine.runtime import RenderApp
    from composeio.render.engine.parser import parse_compose_yaml

    captured = {}

    class _Sentinel(Exception):
        pass

    class RecordingWindow:
        def __init__(self, **kw):
            captured.update(kw)

        def create_surface(self, w, h):
            raise _Sentinel()

    monkeypatch.setattr(window_mod, "SkiaWindow", RecordingWindow)
    comp = parse_compose_yaml(DECISIONS_SCENE)
    app = RenderApp(comp, windowed=True)
    app._geometry_store = NullGeometryStore()       # keep the home dir untouched
    with pytest.raises(_Sentinel):
        app.start()
    assert captured.get("resizable") is True
    assert captured.get("min_size") == (720, 480)


# ---------------------------------------------------------------------------
# Designer-family viewer resize parity ([SPEC:CXC-RENDER] [CXC-R-WINDOW-RESIZE],
# CXC-R-C39): each console-helper viewer opens resizable AND its content
# REFLOWS with zero widget overlap / out-of-bounds clip across >= 3 client
# sizes.  The machine tier of the parity bar (the glass tier — drag-resize
# legibility — is a human check).
# ---------------------------------------------------------------------------

from composeio.render.engine.layout import Container, FlexContainer
from composeio.render.widget import Constraints, Rect

CONSOLE_DIR = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console")
DOC_SCENE = os.path.join(CONSOLE_DIR, "docviewer.compose.yaml")
DSD_SCENE = os.path.join(CONSOLE_DIR, "dsdviewer.compose.yaml")
WATCH_SCENE = os.path.join(CONSOLE_DIR, "nightwatch.compose.yaml")

_EPS = 1.0


def _rects_overlap(a, b):
    return not (a.x + a.w <= b.x + _EPS or b.x + b.w <= a.x + _EPS or
                a.y + a.h <= b.y + _EPS or b.y + b.h <= a.y + _EPS)


def _reflow_problems(node, comp_w, comp_h, path=""):
    """Walk the arranged tree; return a list of (clip / overlap) violations.

    Out-of-bounds clip is checked for every widget.  Mutual overlap is checked
    ONLY among a FlexContainer's simultaneously-visible flow siblings — a
    pixel_stack (z-order) and a tab pane (one page shown) stack BY DESIGN, so
    overlap there is not a violation.
    """
    problems = []
    r = node.arranged_rect
    if r is not None:
        if (r.x < -_EPS or r.y < -_EPS or
                r.x + r.w > comp_w + _EPS or r.y + r.h > comp_h + _EPS):
            problems.append(
                f"CLIP {path}{node.widget_id}: "
                f"({r.x:.1f},{r.y:.1f},{r.w:.1f},{r.h:.1f}) vs {comp_w}x{comp_h}")
    if isinstance(node, Container):
        if isinstance(node, FlexContainer):
            flow = [c for c in node._flow_children()
                    if getattr(c, "visible", True) and c.arranged_rect is not None]
            for i in range(len(flow)):
                for j in range(i + 1, len(flow)):
                    if _rects_overlap(flow[i].arranged_rect, flow[j].arranged_rect):
                        problems.append(
                            f"OVERLAP {path}{node.widget_id}: "
                            f"{flow[i].widget_id} <> {flow[j].widget_id}")
        for c in node.children:
            problems += _reflow_problems(c, comp_w, comp_h,
                                         path + node.widget_id + "/")
    return problems


def _layout_at(comp, w, h):
    comp.width, comp.height = w, h
    comp.root.measure(Constraints(w, h))
    comp.root.arrange(Rect(0, 0, w, h))


@pytest.mark.parametrize("scene,key,sizes", [
    (DOC_SCENE, "doc-viewer", [(560, 420), (720, 900), (1280, 1000)]),
    (DSD_SCENE, "dsd-viewer", [(640, 520), (780, 940), (1280, 1000)]),
    (WATCH_SCENE, "away-watch", [(480, 560), (540, 636), (1100, 900)]),
])
def test_viewer_opts_in_resizable_and_persistent(scene, key, sizes):
    """[CXC-R-WINDOW-RESIZE] each viewer opts into the resize + persist seam."""
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(scene)
    assert comp.resizable is True
    assert comp.persist_geometry is True
    assert comp.window_key == key
    assert comp.min_width > 0 and comp.min_height > 0
    # the min floor never exceeds the default (a window that can't open at its
    # own default size is a broken opt-in)
    assert comp.min_width <= comp.width and comp.min_height <= comp.height


@pytest.mark.parametrize("scene,sizes", [
    (DOC_SCENE, [(560, 420), (720, 900), (1280, 1000)]),
    (DSD_SCENE, [(640, 520), (780, 940), (1280, 1000)]),
    (WATCH_SCENE, [(480, 560), (540, 636), (1100, 900)]),
])
def test_viewer_reflows_without_overlap_or_clip(scene, sizes):
    """[CXC-R-C39] content REFLOWS across >= 3 client sizes: zero widget-rect
    overlap and zero out-of-bounds clip at each."""
    from composeio.render.engine.parser import parse_compose_yaml
    assert len(sizes) >= 3
    for (w, h) in sizes:
        comp = parse_compose_yaml(scene)
        _layout_at(comp, w, h)
        problems = _reflow_problems(comp.root, w, h)
        assert not problems, f"{os.path.basename(scene)} @ {w}x{h}:\n" + \
            "\n".join(problems)


def test_svg_doc_viewer_reflows_without_overlap_or_clip():
    """[CXC-R-C39] the code-built SVG doc scene (build_svg_scene) also reflows."""
    import importlib.util
    launcher = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools",
                            "doc_viewer_launcher.py")
    spec = importlib.util.spec_from_file_location("_dvl_test", launcher)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                      # type: ignore[union-attr]
    from composeio.render.engine.parser import parse_widget_tree
    scene = mod.build_svg_scene(os.path.join(REPO_ROOT, "README.md"))
    d = scene["design"]
    assert d["resizable"] is True and d["persist_geometry"] is True
    assert d["window_key"] == "doc-viewer"
    for (w, h) in [(560, 420), (900, 900), (1280, 1000)]:
        comp = parse_widget_tree(scene)
        _layout_at(comp, w, h)
        problems = _reflow_problems(comp.root, w, h)
        assert not problems, f"svg @ {w}x{h}:\n" + "\n".join(problems)
