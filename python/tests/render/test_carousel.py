"""
Carousel acceptance tests ([SPEC:CXC-PRIM] [CXC-PRIM-CAROUSEL]) — the machine
gate of [CXC-PRIM-CAROUSEL-VERIFY] (CAR-V1..CAR-V8).

Two seams under test:
  * the pure cover-flow geometry (widgets/carousel.py :: tile_geometry) — a
    pure function of ``position``;
  * the swipe controller (engine/swipe_carousel.py :: SwipeCarouselController)
    — the drag/tap/snap/elastic state machine, driven with a synthetic clock.

All offscreen (windowed=False); no display required.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.swipe_carousel import (
    SwipeCarouselController, ease_out_cubic)

SPACING = 425.0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _carousel_cfg(n=3, **car):
    tiles = []
    for i in range(n):
        t = {"image": f"bus://none/tile{i}.png", "label": f"Tile {i}"}
        if i == 1:
            t = {"widget": {"type": "led", "state": "on",
                            "color": [52, 195, 208]},
                 "target": "composer", "label": "Composer"}
        tiles.append(t)
    spec = {"type": "carousel", "id": "car", "x": 0, "y": 0,
            "spacing": SPACING, "tile_width": 260, "tile_height": 260,
            "tiles": tiles}
    spec.update(car)
    return {"design": {"width": 1080, "height": 620, "background": [12, 14, 20]},
            "widgets": [spec]}


def _app(n=3, **car):
    app = RenderApp.from_dict(_carousel_cfg(n, **car), windowed=False)
    app.start()
    app.drain_updates()
    app.render_frame()
    return app


def _car(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}["car"]


def _ctl(n=3, **kw):
    kw.setdefault("spacing", SPACING)
    return SwipeCarouselController(n=n, **kw)


def _run_snap(app, car, t0=0.0, step=0.05, tmax=1.5):
    """Drive render_frame_at until the controller settles (idle)."""
    t = t0
    while car._controller.state == "snapping" and t < t0 + tmax:
        t += step
        app.render_frame_at(t)
    return t


# ---------------------------------------------------------------------------
# CAR-V1 — geometry is a pure function of position ([CXC-PRIM-CAROUSEL-MODEL])
# ---------------------------------------------------------------------------

def test_car_v1_geometry_pure_function_of_position():
    app = _app(n=3, spacing=425, tile_width=500)
    car = _car(app)
    W = 1080.0
    for position in (0.0, 0.5, 1.0, 2.0):
        geoms = car.tile_geometry(position, W)
        for g in geoms:
            i = g.index
            exp_x = W / 2.0 + (i - position) * 425.0
            dist = min(1.0, abs(i - position))
            exp_scale = max(0.5, 1.0 - (1.0 - 0.5) * dist)
            assert g.x == pytest.approx(exp_x, abs=0.5), \
                f"x mismatch tile {i} @pos {position}"
            assert g.scale == pytest.approx(exp_scale, abs=0.001), \
                f"scale mismatch tile {i} @pos {position}"
    app.stop()


def test_car_v1_center_tile_is_largest_and_lifted():
    """At an integer position the centered tile is center_scale and every
    neighbor is strictly smaller (the lift is z-order; the SIZE proves the
    center dominates)."""
    app = _app(n=5, spacing=300, tile_width=260)
    car = _car(app)
    geoms = car.tile_geometry(2.0, 1080)
    assert geoms[2].scale == pytest.approx(1.0)
    for i in (0, 1, 3, 4):
        assert geoms[i].scale < geoms[2].scale
    # symmetric falloff
    assert geoms[1].scale == pytest.approx(geoms[3].scale)
    assert geoms[0].scale == pytest.approx(geoms[4].scale)
    app.stop()


def test_car_v1_offscreen_tiles_culled_without_changing_onscreen():
    app = _app(n=12, spacing=300, tile_width=260)
    car = _car(app)
    onscreen = car.tile_geometry(0.0, 1080)
    # far tiles are culled
    assert any(g.culled for g in onscreen)
    # culling never alters an on-screen tile's x/scale (compare tile 1)
    assert onscreen[1].culled is False
    assert onscreen[1].x == pytest.approx(1080 / 2 + 1 * 300)
    app.stop()


# ---------------------------------------------------------------------------
# CAR-V2 — center-tap fires the activate intent, nothing else
# ---------------------------------------------------------------------------

def test_car_v2_center_tap_fires_launch_intent_once():
    app = _app(n=3, initial_index=1)     # tile 1 carries target: composer
    car = _car(app)
    published = []
    orig = app.publish

    def spy(ns, value, **kw):
        published.append((ns, value))
        return orig(ns, value, **kw)
    app.publish = spy

    # a tap on the centered tile: down + up at the same point (no drift)
    car._clock = lambda: 0.0
    r = car.arranged_rect
    cx, cy = r.x + r.w / 2, r.y + r.h / 2
    car.on_pointer_down(cx, cy)
    car.on_pointer_up(cx, cy)

    opens = [p for p in published if p[0] == "_app/composer/open"]
    assert opens == [("_app/composer/open", "open")], \
        f"exactly one launch intent expected, got {published}"
    app.stop()


def test_car_v2_generic_activate_when_no_target():
    app = _app(n=3, initial_index=0, activate_ns="carousel/activate")
    car = _car(app)          # tile 0 has NO target
    published = []
    orig = app.publish
    app.publish = lambda ns, value, **kw: (published.append((ns, value)),
                                           orig(ns, value, **kw))[1]
    car._clock = lambda: 0.0
    r = car.arranged_rect
    car.on_pointer_down(r.x + r.w / 2, r.y + r.h / 2)
    car.on_pointer_up(r.x + r.w / 2, r.y + r.h / 2)
    assert ("carousel/activate", 0) in published
    assert not any(ns.startswith("_app/") and ns.endswith("/open")
                   for ns, _ in published)
    app.stop()


# ---------------------------------------------------------------------------
# CAR-V3 — swipe past threshold advances EXACTLY one tile (no momentum)
# ---------------------------------------------------------------------------

def test_car_v3_drift_advance_one():
    ctl = _ctl(n=5, initial_index=2)
    ctl.begin(x=500, y=0, t=0.0)
    ctl.update(dx=-100, dy=0, t=0.20)            # slow but past 0.15*S=63.75
    result = ctl.finish(x=400, y=0, t=0.30)      # drift 100, low velocity
    assert result == "advance"
    assert ctl.committed == 3                     # +1, exactly
    ctl.tick(0.0); ctl.tick(1.0)                  # settle
    assert ctl.state == "idle"
    assert ctl.position == pytest.approx(3.0)


def test_car_v3_flick_advance_one_never_two():
    ctl = _ctl(n=5, initial_index=2)
    ctl.begin(x=500, y=0, t=0.0)
    ctl.update(dx=-20, dy=0, t=0.02)             # tiny drift...
    result = ctl.finish(x=480, y=0, t=0.04)      # ...but 20px/0.04s = 500px/s
    assert result == "advance"
    assert ctl.committed == 3                     # ONE tile — velocity != distance
    assert abs(ctl.committed - 2) == 1


def test_car_v3_direction_follows_swipe_sign():
    ctl = _ctl(n=5, initial_index=2)
    ctl.begin(x=500, y=0, t=0.0)
    ctl.update(dx=+100, dy=0, t=0.20)            # finger RIGHT -> previous tile
    ctl.finish(x=600, y=0, t=0.30)
    assert ctl.committed == 1


def test_car_v3_clamps_at_edges():
    ctl = _ctl(n=3, initial_index=0)
    ctl.begin(x=500, y=0, t=0.0)
    ctl.update(dx=+200, dy=0, t=0.2)             # swipe toward index -1
    ctl.finish(x=700, y=0, t=0.3)
    assert ctl.committed == 0                     # clamped [0, n-1]


# ---------------------------------------------------------------------------
# CAR-V4 — sub-threshold swipe snaps back (index unchanged)
# ---------------------------------------------------------------------------

def test_car_v4_sub_threshold_snaps_back():
    ctl = _ctl(n=5, initial_index=2)
    ctl.begin(x=500, y=0, t=0.0)
    ctl.update(dx=-40, dy=0, t=0.15)             # |dx|=40 < 63.75
    result = ctl.finish(x=460, y=0, t=0.20)      # released at rest -> v~0
    assert result == "snap_back"
    assert ctl.committed == 2                     # UNCHANGED
    ctl.tick(0.0); ctl.tick(1.0)
    assert ctl.position == pytest.approx(2.0)     # eased back to the origin


# ---------------------------------------------------------------------------
# CAR-V5 — drag tracks 1:1; edge rubber-bands
# ---------------------------------------------------------------------------

def test_car_v5_drag_one_spacing_moves_position_by_one():
    ctl = _ctl(n=5, initial_index=1)
    ctl.begin(x=0, y=0, t=0.0)
    ctl.update(dx=-SPACING, dy=0, t=0.1)         # one full S left
    assert ctl.position == pytest.approx(2.0)     # +1.0
    ctl.update(dx=+SPACING, dy=0, t=0.2)         # back to origin
    assert ctl.position == pytest.approx(0.0) or \
        ctl.position == pytest.approx(0.0, abs=1e-6)


def test_car_v5_edge_overshoot_is_elastic():
    ctl = _ctl(n=3, initial_index=0, elastic_factor=0.30)
    ctl.begin(x=0, y=0, t=0.0)
    ctl.update(dx=+SPACING, dy=0, t=0.1)         # past the low edge by one pane
    assert ctl.position == pytest.approx(-0.30)   # overshoot * elastic
    # release snaps back inside [0, n-1]
    ctl.finish(x=SPACING, y=0, t=0.2)
    ctl.tick(0.0); ctl.tick(1.0)
    assert 0.0 <= ctl.position <= 2.0
    assert ctl.position == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# CAR-V6 — control-is-a-peer: goto_ns moves it identically to a swipe
# ---------------------------------------------------------------------------

def test_car_v6_goto_matches_swipe_outcome():
    # (a) a swipe from index 1 to 2
    app_s = _app(n=3, initial_index=1, index_ns="launcher/index")
    car_s = _car(app_s)
    car_s._clock = lambda: 0.0
    r = car_s.arranged_rect
    cx, cy = r.x + r.w / 2, r.y + r.h / 2
    car_s.on_pointer_down(cx, cy)
    car_s.on_pan(-100, 0)
    car_s.on_pointer_up(cx - 100, cy)
    _run_snap(app_s, car_s)
    swipe_index = app_s.bus.get_state("launcher/index")
    swipe_pos = car_s.position
    app_s.stop()

    # (b) an Intelligence publishes 2 to goto_ns
    app_g = _app(n=3, initial_index=1, index_ns="launcher/index",
                 goto_ns="launcher/goto")
    car_g = _car(app_g)
    app_g.publish("launcher/goto", 2)
    app_g.drain_updates()
    _run_snap(app_g, car_g)
    goto_index = app_g.bus.get_state("launcher/index")
    goto_pos = car_g.position
    app_g.stop()

    assert swipe_index == goto_index == 2
    assert swipe_pos == pytest.approx(goto_pos) == pytest.approx(2.0)


def test_car_v6_goto_clamps_out_of_range():
    ctl = _ctl(n=3, initial_index=0)
    ctl.goto(99)
    ctl.tick(0.0); ctl.tick(1.0)
    assert ctl.committed == 2
    assert ctl.position == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# CAR-V7 — a drag cancels an in-flight snap
# ---------------------------------------------------------------------------

def test_car_v7_pan_start_cancels_snap_takes_over_from_live():
    ctl = _ctl(n=5, initial_index=0)
    ctl.goto(4)                                   # long snap
    ctl.tick(0.0)                                 # stamp start
    ctl.tick(0.05)                                # partway
    mid = ctl.position
    assert ctl.state == "snapping"
    assert 0.0 < mid < 4.0
    # grab it mid-animation
    ctl.begin(x=0, y=0, t=1.0)
    assert ctl.state == "dragging"                # snapping -> dragging
    # the drag takes over from the LIVE position, not the target
    ctl.update(dx=0, dy=0, t=1.0)
    assert ctl.position == pytest.approx(mid, abs=1e-6)


# ---------------------------------------------------------------------------
# CAR-V8 — uniform scale for image AND live tiles (one transform path)
# ---------------------------------------------------------------------------

def test_car_v8_image_and_live_tile_scale_identically():
    # tile 0 = image, tile 1 = LIVE led widget, tile 2 = image.  At position 1
    # (live tile centered) the two image neighbors are equidistant, so their
    # scale MUST match; and a live tile at distance 1 scales like an image tile
    # at distance 1.
    app = _app(n=3, spacing=300, tile_width=260)
    car = _car(app)
    car._controller.set_position(1.0)
    car._reflow(car.arranged_rect)
    img_left, live_center, img_right = car.children
    assert img_left.scale == pytest.approx(img_right.scale)     # symmetric
    assert live_center.scale == pytest.approx(1.0)              # centered live
    # a live tile at distance 1 == an image tile at distance 1 (same falloff)
    car._controller.set_position(0.0)                           # img0 centered
    car._reflow(car.arranged_rect)
    image_at_dist1 = car.children[1].scale                      # live, dist 1
    assert image_at_dist1 == pytest.approx(car.side_scale)      # == image would be
    app.stop()


def test_car_v8_no_scale_step_cache():
    """Continuous scale, not the CTk 11-step swap: fractional positions yield
    distinct, non-quantized scales."""
    app = _app(n=3, spacing=300, tile_width=260)
    car = _car(app)
    scales = {round(car.tile_geometry(p, 1080)[0].scale, 4)
              for p in (0.0, 0.1, 0.2, 0.3)}
    assert len(scales) == 4          # every position gives a distinct scale
    app.stop()


# ---------------------------------------------------------------------------
# dot indicator tracks the centered index ([CXC-PRIM-CAROUSEL-VERIFY])
# ---------------------------------------------------------------------------

def test_dot_active_tracks_current_index():
    ctl = _ctl(n=4, initial_index=0)
    assert ctl.current_index() == 0
    ctl.goto(2)
    ctl.tick(0.0); ctl.tick(1.0)
    assert ctl.current_index() == 2


# ---------------------------------------------------------------------------
# controller math sanity ([CXC-PRIM-CAROUSEL-TOUCH])
# ---------------------------------------------------------------------------

def test_ease_out_cubic_endpoints():
    assert ease_out_cubic(0.0) == pytest.approx(0.0)
    assert ease_out_cubic(1.0) == pytest.approx(1.0)
    assert ease_out_cubic(0.5) == pytest.approx(1 - 0.5 ** 3)


def test_snap_settles_exactly_on_integer():
    ctl = _ctl(n=3, initial_index=0)
    settled = []
    ctl.on_settle = settled.append
    ctl.goto(1)
    t = 0.0
    while ctl.state == "snapping" and t < 2.0:
        t += 0.05
        ctl.tick(t)
    assert ctl.position == 1.0            # EXACT, not approx
    assert settled[-1] == 1


def test_empty_carousel_is_not_an_error():
    app = RenderApp.from_dict(
        {"design": {"width": 400, "height": 300},
         "widgets": [{"type": "carousel", "id": "car", "x": 0, "y": 0,
                      "tiles": []}]}, windowed=False)
    app.start()
    app.render_frame()               # renders an empty strip, no dots, no crash
    car = _car(app)
    assert car.n == 0
    app.stop()
