"""Launch-initials seeding ([SPEC:CXC-RETRO §5c]) — the wrong-entry defect.

A launcher that spawns a scene for ONE specific entity (the Away-Watch
relaunch → the CLICKED watch) parametrizes the launched window by handing it a
per-launch ``{namespace: value}`` map in the ``COMPOSEIO_SCENE_INITIAL`` env
var. The runtime seeds those namespaces at start() so the window renders THAT
entity — not the scene's baked-in static default.

This is the engine half of the fix for: "clicking TODAY's away entry opened a
Night Watch window showing YESTERDAY's (the seeded default) data." Before the
fix the runtime ignored the env, so every relaunch fell back to the scene
default. These tests FAIL on the pre-fix runtime (namespace stays None / the
default) and PASS after.
"""

from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import (RenderApp, SCENE_INITIAL_ENV,
                                             _launch_scene_initials)


# A minimal stand-in for nightwatch.compose.yaml: labels bound to
# fun/nightwatch/* namespaces WITH static defaults (the "default entry").
_SCENE = {
    "design": {"width": 200, "height": 120, "title": "watch"},
    "namespaces": [
        {"name": "fun/nightwatch/title", "state_engine": "json"},
        {"name": "fun/nightwatch/tokens", "state_engine": "json"},
    ],
    "widgets": [{
        "type": "pixel_stack", "id": "root",
        "width": 200, "height": 120, "background": "#0A0E17",
        "children": [
            {"type": "label", "id": "title", "x": 8, "y": 8,
             "text": {"namespace": "fun/nightwatch/title",
                      "default": "Away Watch: DEFAULT"}},
            {"type": "label", "id": "tokens", "x": 8, "y": 40,
             "text": {"namespace": "fun/nightwatch/tokens",
                      "default": "23.7M"}},
        ],
    }],
}


def _run_headless():
    app = RenderApp.from_dict(_SCENE, windowed=False)
    app.start()
    return app


def test_no_env_leaves_scene_default(monkeypatch):
    """With no COMPOSEIO_SCENE_INITIAL, the launch-initials are empty and the
    namespaces are unseeded (the window shows its static defaults)."""
    monkeypatch.delenv(SCENE_INITIAL_ENV, raising=False)
    assert _launch_scene_initials() == {}
    app = _run_headless()
    try:
        assert app.bus.get_state("fun/nightwatch/title") is None
        assert app.bus.get_state("fun/nightwatch/tokens") is None
    finally:
        app.stop()


def test_env_seeds_the_clicked_entry(monkeypatch):
    """The launcher's per-window map seeds the namespaces at start() — the
    window renders the CLICKED entry, NOT the scene default. This is the
    regression guard for the away-watch wrong-entry defect."""
    monkeypatch.setenv(SCENE_INITIAL_ENV, json.dumps({
        "fun/nightwatch/title": "Away Watch: Wed, Jul 22 @ 13:10",
        "fun/nightwatch/tokens": "1.2M",
    }))
    assert _launch_scene_initials()["fun/nightwatch/tokens"] == "1.2M"
    app = _run_headless()
    try:
        # the bus now carries the clicked entry's values (not the None/default)
        assert str(app.bus.get_state("fun/nightwatch/title")) == \
            "Away Watch: Wed, Jul 22 @ 13:10"
        assert str(app.bus.get_state("fun/nightwatch/tokens")) == "1.2M"
    finally:
        app.stop()


def test_malformed_env_degrades_to_no_seed(monkeypatch):
    """A malformed/non-object env never raises and seeds nothing (the window
    still opens on its scene default)."""
    for bad in ("not json", "[1,2,3]", "42", ""):
        monkeypatch.setenv(SCENE_INITIAL_ENV, bad)
        assert _launch_scene_initials() == {}


def test_live_bus_value_wins_over_launch_seed(monkeypatch):
    """A value already on the bus (a live/retained publish) wins over the
    launch seed — same precedence as declared initial: values."""
    monkeypatch.setenv(SCENE_INITIAL_ENV, json.dumps(
        {"fun/nightwatch/tokens": "1.2M"}))
    app = RenderApp.from_dict(_SCENE, windowed=False)
    # pre-seed the bus BEFORE start() (a retained/live value)
    app.bus.ensure_namespace("fun/nightwatch/tokens", engine_config="json")
    app.bus.publish_state("fun/nightwatch/tokens", "9.9M", source="live")
    app.start()
    try:
        assert str(app.bus.get_state("fun/nightwatch/tokens")) == "9.9M"
    finally:
        app.stop()
