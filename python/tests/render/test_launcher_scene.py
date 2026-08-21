"""
Acceptance test for the ComposePlatform carousel LAUNCHER scene
(compose_projects/carousel/launcher.compose.yaml) — the bus-native replacement
for the CTk Maker launcher.

The launcher adds NO new widget: every tile composes shipped primitives
(carousel + pixel_stack + gauge/label/circle/rect), so this guards the SCENE
contract, not a class:

  * the carousel builds exactly the three settled tiles (fiat, bsg, power) in
    order, each carrying its launch target ([CXC-PRIM-CAROUSEL-TILES]);
  * the LIVE fiat tile subscribes fiat/* by reference — a published
    fiat/speed reaches the hosted gauge while it rides the carousel
    ([CXC-PRIM-CAROUSEL-SCALE] live-widget tile);
  * the bus surface round-trips: launcher/goto snaps and launcher/index (+
    /target) publishes the settled tile ([CXC-PRIM-CAROUSEL-BIND]).

Offscreen (windowed=False); no display required.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# python/ -> repo root -> compose_projects/carousel/launcher.compose.yaml
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
SCENE = os.path.join(REPO_ROOT, "compose_projects", "carousel",
                     "launcher.compose.yaml")

from composeio.render.engine.runtime import RenderApp


def _app():
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    app.drain_updates()
    app.render_frame()
    return app


def _by_id(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


def test_scene_file_exists():
    assert os.path.isfile(SCENE), SCENE


def test_launcher_builds_three_tiles_in_order():
    app = _app()
    car = _by_id(app)["launcher"]
    assert car.n == 3
    assert car._targets == ["fiat", "bsg", "power"]
    app.stop()


def test_fiat_tile_is_live_speed_subscribes_by_reference():
    """The hosted speed gauge updates from a published fiat/speed — proving the
    live-widget tile's subscription was collected by reference."""
    app = _app()
    gauge = _by_id(app)["fiat_speed"]
    assert gauge.value == 0
    app.publish("fiat/speed", 52)
    app.drain_updates()
    assert gauge.value == 52
    app.stop()


def test_center_tap_fires_launch_intent():
    app = _app()
    car = _by_id(app)["launcher"]
    published = []
    orig = app.publish
    app.publish = lambda ns, v, **k: (published.append((ns, v)),
                                       orig(ns, v, **k))[1]
    car._clock = lambda: 0.0
    r = car.arranged_rect
    cx, cy = r.x + r.w / 2, r.y + r.h / 2
    car.on_pointer_down(cx, cy)
    car.on_pointer_up(cx, cy)
    assert ("_app/fiat/open", "open") in published
    app.stop()


def test_goto_snaps_and_publishes_index_and_target():
    app = _app()
    car = _by_id(app)["launcher"]
    app.publish("launcher/goto", 2)
    app.drain_updates()
    t = 0.0
    while car._controller.state == "snapping" and t < 3.0:
        t += 0.05
        app.render_frame_at(t)
    assert app.bus.get_state("launcher/index") == 2
    assert app.bus.get_state("launcher/index/target") == "power"
    app.stop()
