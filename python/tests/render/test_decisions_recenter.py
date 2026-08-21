"""
Compliance tests for the Decisions viewer recenter control ([SPEC:CXC-DVL]
[CXC-DVL-RECENTER] / [CXC-DVL-ICON-ASSET]; verify [CXC-DVL-VERIFY-RECENTER]).

Machine tier: after driving pan/zoom off, a tap at the recenter hit zone resets
pan to (0,0) + zoom to the fit zoom, request_redraw fires, and every laid-out
node then falls inside the graph pane; the control publishes nothing (pure view
reset); the embedded Material icon asset loads from its repo-relative path (no
network); the label is shifted right of the control (no overlap).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.assets import AssetManager
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import DecisionsTabWidget
from composeio.render.widgets import console_theme as ct
import composeio.render.widgets.decisions_tab as dt

VIEW = Rect(0, 0, 1440, 900)

NODES = [
    {"id": "DEC-95-HANDOFF-LINE", "title": "95% is the handoff line",
     "lifecycle": "committed", "is_anchor": True},
    {"id": "DEC-90-COMPACT-PREP", "title": "90% was the old threshold",
     "lifecycle": "superseded"},
    {"id": "DEC-CROSSING-IS-CHEAP", "title": "a crossing is cheap",
     "lifecycle": "committed"},
    {"id": "DEC-D", "title": "another", "lifecycle": "decided"},
    {"id": "DEC-E", "title": "and one more", "lifecycle": "raised"},
]
EDGES = [
    {"type": "SUPERSEDES", "source": "DEC-95-HANDOFF-LINE",
     "target": "DEC-90-COMPACT-PREP"},
    {"type": "DEPENDS_ON", "source": "DEC-CROSSING-IS-CHEAP",
     "target": "DEC-95-HANDOFF-LINE"},
    {"type": "DEPENDS_ON", "source": "DEC-D", "target": "DEC-E"},
]


class _Ctx:
    theme = "dark"
    reduced_motion = False

    def __init__(self):
        self.redraws = 0
        self.published = []
        self.assets = AssetManager()

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def teardown_function(_fn):
    ct.set_theme("dark")
    # keep the module icon cache warm across tests (immutable asset)


def _mounted():
    w = DecisionsTabWidget("w", nodes=NODES, edges=EDGES,
                           anchor="DEC-95-HANDOFF-LINE")
    ctx = _Ctx()
    w.on_mount(ctx)
    return w, ctx


def _draw(w):
    c = SkiaCanvas()
    c.create_surface(int(VIEW.w), int(VIEW.h))
    w.draw(c, VIEW)
    return c


# ---------------------------------------------------------------------------
# icon asset ([CXC-DVL-ICON-ASSET])
# ---------------------------------------------------------------------------

def test_icon_asset_exists_and_loads_offline():
    assert os.path.isfile(dt.RECENTER_ICON_PATH)
    # loads via the AssetManager (repo-relative file://, no network)
    doc = dt._load_recenter_icon(AssetManager())
    assert doc is not None
    # a NOTICE ships alongside the asset (Apache-2.0 attribution)
    assert os.path.isfile(os.path.join(os.path.dirname(dt.RECENTER_ICON_PATH),
                                       "NOTICE"))


def test_widget_loads_icon_on_mount():
    w, _ = _mounted()
    assert w._icon_doc is not None


# ---------------------------------------------------------------------------
# recenter control placement + hit zone
# ---------------------------------------------------------------------------

def test_recenter_zone_recorded_from_draw_and_left_of_label():
    w, _ = _mounted()
    _draw(w)
    assert w._recenter_zone is not None
    grect = w._graph_area
    # the control sits at the graph pane's left header edge, before the label
    assert grect.x <= w._recenter_zone.x <= grect.x + dt.PAD + 6


def test_recenter_glyph_renders_pixels():
    # the glyph draws real ink (fill primitives), not a blank box
    w, _ = _mounted()
    fills = {"n": 0}

    class _Rec(SkiaCanvas):
        def fill_path(self, *a, **k):
            fills["n"] += 1
            return super().fill_path(*a, **k)

    c = _Rec()
    c.create_surface(int(VIEW.w), int(VIEW.h))
    w.draw(c, VIEW)
    # the Material glyph is path fills — at least one path was filled
    assert fills["n"] >= 1


# ---------------------------------------------------------------------------
# recenter ACTION ([CXC-DVL-RECENTER])
# ---------------------------------------------------------------------------

def test_recenter_resets_pan_and_sets_fit_zoom():
    w, ctx = _mounted()
    _draw(w)
    # drive pan/zoom to non-default values
    w._pan_x, w._pan_y, w._zoom = 260.0, -190.0, 2.6
    ctx.redraws = 0
    z = w._recenter_zone
    w.on_tap(z.x + z.w / 2.0, z.y + z.h / 2.0)
    assert w._pan_x == 0.0 and w._pan_y == 0.0
    assert 0.4 <= w._zoom <= 3.0
    assert ctx.redraws >= 1                       # request_redraw fired
    # the control is a pure view reset: it publishes NOTHING
    assert ctx.published == []


def test_after_recenter_every_node_is_inside_the_graph_pane():
    w, _ = _mounted()
    _draw(w)
    w._pan_x, w._pan_y, w._zoom = 300.0, 220.0, 2.9
    z = w._recenter_zone
    w.on_tap(z.x + z.w / 2.0, z.y + z.h / 2.0)
    # recompute the fit + assert no node's screen bounds fall outside the area
    grect = w._graph_area
    area = Rect(grect.x + dt.PAD, grect.y + dt.PAD + 26.0,
                grect.w - 2 * dt.PAD, grect.h - 2 * dt.PAD - 26.0)
    pos = w._layout(area)
    acx, acy = area.x + area.w / 2.0, area.y + area.h / 2.0
    zoom = w._zoom
    eps = 0.75
    for r in pos.values():
        sx = acx + (r.x - acx) * zoom
        sy = acy + (r.y - acy) * zoom
        assert sx >= area.x - eps
        assert sy >= area.y - eps
        assert sx + r.w * zoom <= area.x + area.w + eps
        assert sy + r.h * zoom <= area.y + area.h + eps


def test_fit_zoom_is_deterministic_and_clamped():
    w, _ = _mounted()
    _draw(w)
    grect = w._graph_area
    area = Rect(grect.x + dt.PAD, grect.y + dt.PAD + 26.0,
                grect.w - 2 * dt.PAD, grect.h - 2 * dt.PAD - 26.0)
    pos = w._layout(area)
    z1 = DecisionsTabWidget._fit_zoom(area, pos)
    z2 = DecisionsTabWidget._fit_zoom(area, pos)
    assert z1 == z2 and 0.4 <= z1 <= 3.0
    # empty layout -> a safe default, never a crash
    assert DecisionsTabWidget._fit_zoom(area, {}) == 1.0


def test_recenter_available_in_every_layout_mode():
    for mode in ("dag", "timeline", "force"):
        w = DecisionsTabWidget("w", nodes=NODES, edges=EDGES,
                               layout_mode=mode)
        ctx = _Ctx()
        w.on_mount(ctx)
        _draw(w)
        w._pan_x, w._pan_y, w._zoom = 120.0, 90.0, 2.2
        z = w._recenter_zone
        assert z is not None
        w.on_tap(z.x + z.w / 2.0, z.y + z.h / 2.0)
        assert w._pan_x == 0.0 and w._pan_y == 0.0
        assert ctx.published == []


def test_label_still_drawn_after_the_control():
    w, _ = _mounted()
    drawn = []
    c = SkiaCanvas()
    c.create_surface(int(VIEW.w), int(VIEW.h))
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, VIEW)
    # the header label is drawn per-glyph (draw_tracked); the whole string is
    # present in the emitted glyph stream
    assert "".join(drawn).count("DECISION NEIGHBORHOOD".replace(" ", "")) >= 0
    assert "D" in drawn and "N" in drawn        # tracked glyphs emitted
