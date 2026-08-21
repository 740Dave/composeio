"""
Acceptance test for the BSG "2-up" scene
(compose_projects/carousel/bsg_app.compose.yaml) — the app the carousel BSG
tile launches ([SPEC:CXC-CIC] [CXC-CIC-TWO-UP]).

The 2-up adds NO new widget: it COMPOSES the landed `colonial_console` twice in
a pixel_stack, so this guards the SCENE contract, not a class:

  * TWO `colonial_console` instances, stacked vertically in portrait 1080x1920,
    EDGE-ALIGNED — top flush to the screen top, bottom flush to the bottom, the
    slack as a center gap (David 2026-08-04) ([CXC-CIC-TWO-UP]);
  * initial modes: top=EXT, bottom=STD (via `radio_selected`);
  * they are DISTINCT DUTY STATIONS (cic.1.helm / cic.2.helm), so a publish to
    one station moves only that console — they never cross-wire
    ([CXC-CIC-DUTY-STATION]);
  * each 4:3 console is sized 1080x810 (no distortion); top at y=0, bottom at
    y=1110 (=1920-810), so a 300px center gap.

Offscreen (windowed=False); no display required.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# python/ -> repo root -> compose_projects/carousel/bsg_app.compose.yaml
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
# [CXC-PROJECT-WIDGETS] the colonial_console widget is now project-local to BSG;
# the repo root must be importable to reach compose_projects.bsg.widgets.
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
SCENE = os.path.join(REPO_ROOT, "compose_projects", "carousel",
                     "bsg_app.compose.yaml")

from composeio.render.engine.runtime import RenderApp
from compose_projects.bsg.widgets.colonial_console import ColonialConsoleWidget

_GAP = 20.0
_CONSOLE_W = 1080.0
_CONSOLE_H = 810.0     # 1080 * 3/4 (exact 4:3)


def _app():
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    app.drain_updates()
    app.render_frame()          # arranges the tree
    return app


def _by_id(app):
    return {w.widget_id: w for w in app.comp.root.iter_widgets()}


def test_scene_file_exists():
    assert os.path.isfile(SCENE), SCENE


def test_two_colonial_consoles_distinct_stations():
    """Exactly two `colonial_console` widgets, each a real ColonialConsoleWidget
    with a DISTINCT duty station ([CXC-CIC-DUTY-STATION])."""
    app = _app()
    consoles = [w for w in app.comp.root.iter_widgets()
                if isinstance(w, ColonialConsoleWidget)]
    assert len(consoles) == 2
    by = _by_id(app)
    top, bot = by["cic_top"], by["cic_bottom"]
    assert isinstance(top, ColonialConsoleWidget)
    assert isinstance(bot, ColonialConsoleWidget)
    assert top.station == "cic.1.helm"
    assert bot.station == "cic.2.helm"
    assert top.station != bot.station
    app.stop()


def test_stacked_edge_flushed_top_and_bottom():
    """Two 4:3 consoles (1080x810), EDGE-ALIGNED: the top console flush to the
    screen top and the bottom console flush to the bottom, the slack falling as
    a center gap — David's confirmed layout (2026-08-04: "glad to have lots of
    spacing between the two CIC screens")."""
    app = _app()
    by = _by_id(app)
    top = by["cic_top"].arranged_rect
    bot = by["cic_bottom"].arranged_rect
    # each console fills the full 1080 width at a 4:3 height (no distortion)
    assert top.w == _CONSOLE_W and bot.w == _CONSOLE_W
    assert top.h == _CONSOLE_H and bot.h == _CONSOLE_H
    # same x (stacked, not side-by-side)
    assert top.x == bot.x == 0
    # top console flush to the screen TOP
    assert abs(top.y - 0.0) < 1e-6, f"top.y={top.y}"
    # bottom console flush to the screen BOTTOM
    assert abs((bot.y + bot.h) - 1920.0) < 1e-6, f"bot bottom={bot.y + bot.h}"
    # the vertical slack falls as the center gap (1920 - 2*810 = 300px)
    gap = bot.y - (top.y + top.h)
    assert abs(gap - (1920.0 - 2 * _CONSOLE_H)) < 1e-6, f"gap={gap}"
    app.stop()


def test_initial_radio_modes_top_ext_bottom_std():
    """The 2-up boots top=EXT, bottom=STD via `radio_selected` (David 2026-08-04).
    Radio order is [DRADIS=0, STD=1, EXT=2, CHRONO=3]."""
    app = _app()
    by = _by_id(app)
    assert by["cic_top"].radio_index == 2     # EXT
    assert by["cic_bottom"].radio_index == 1  # STD
    app.stop()


def test_stations_do_not_cross_wire():
    """Drive ONLY the bottom station (cic.2.helm) — only the bottom console
    reacts; the top (cic.1.helm) is untouched ([CXC-CIC-DUTY-STATION])."""
    app = _app()
    by = _by_id(app)
    top, bot = by["cic_top"], by["cic_bottom"]
    assert (top.console_index, top.subtab_index) == (0, 0)
    assert (bot.console_index, bot.subtab_index) == (0, 0)

    app.publish("colonial_console/cic.2.helm/console", {"index": 2})   # FTL
    app.publish("colonial_console/cic.2.helm/subtab", {"index": 1})    # GENERATOR
    app.drain_updates()
    assert (top.console_index, top.subtab_index) == (0, 0)   # untouched
    assert (bot.console_index, bot.subtab_index) == (2, 1)   # reacted

    # reverse: drive ONLY the top station — bottom stays put
    app.publish("colonial_console/cic.1.helm/console", {"index": 5})   # EMSO
    app.drain_updates()
    assert top.console_index == 5
    assert bot.console_index == 2
    app.stop()


def test_two_up_is_composed_not_forked():
    """The 2-up invents no widget: both stacked widgets are the shipped
    ColonialConsoleWidget class (reuse-by-reference, [CXC-CIC-TWO-UP])."""
    app = _app()
    by = _by_id(app)
    assert type(by["cic_top"]) is ColonialConsoleWidget
    assert type(by["cic_bottom"]) is ColonialConsoleWidget
    app.stop()
