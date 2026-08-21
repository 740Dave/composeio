"""Drag-drop an image onto the Intelligence Console -> send it to the Intelligence.

V1 slice (integration-dominated): an SDL_DROPFILE surfaces as a DropFileEvent; the
runtime publishes ``{"from":"david","image":<abs path>}`` on the composer's send
namespace ``compose_projects/intelligence/<id>/compose`` (scope:global -> MQTT -> channel_bridge).
Ephemeral by design: only the PATH travels; nothing is stored.

Live drag-drop can't run headless (SDL needs a real window + a human drop), so this
drives the runtime's event path with a fake canvas and asserts the publish. The
DropFileEvent decode + SDL_free happens in window.pump_events, exercised live only.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from tests.render.console_scene_ids import chat_ns
from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.parser import parse_widget_tree
from composeio.render.window import DropFileEvent

REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")


class _FakeCanvas:
    """Minimal canvas stand-in: pump_events yields the queued engine events
    once (the runtime's process_events drains one batch)."""

    def __init__(self, events):
        self._events = events

    def pump_events(self):
        evs, self._events = self._events, []
        return evs


def _app_with_events(app, events):
    app.canvas = _FakeCanvas(events)
    app._running = True
    app.windowed = True
    return app


def _spy_publish(app):
    calls = []
    app.publish = lambda ns, val, source="render": calls.append((ns, val, source))
    return calls


def test_drop_publishes_compose_image_on_console_scene():
    app = RenderApp.from_file(SCENE)
    calls = _spy_publish(app)
    _app_with_events(app, [DropFileEvent("/tmp/dropped.png")])

    assert app.process_events() is True          # window stays open
    assert len(calls) == 1, calls
    ns, val, source = calls[0]
    assert ns == chat_ns("compose")
    assert val == {"from": "david", "image": "/tmp/dropped.png"}
    assert source == "console-drop"


def test_drop_uses_absolute_path():
    app = RenderApp.from_file(SCENE)
    calls = _spy_publish(app)
    _app_with_events(app, [DropFileEvent("relative/pic.png")])

    app.process_events()
    assert len(calls) == 1
    _, val, _ = calls[0]
    assert val["image"] == os.path.abspath("relative/pic.png")
    assert os.path.isabs(val["image"])


def test_console_send_namespace_resolved():
    app = RenderApp.from_file(SCENE)
    assert app._compose_send_namespace() == chat_ns("compose")


def test_non_console_scene_does_not_publish_on_drop():
    """Guard ([drop V1]): a scene with no compose_projects/intelligence/<id>/compose namespace
    publishes nothing on a drop — no stray compose."""
    cfg = {
        "app": {"id": "plain"},
        "design": {"title": "plain", "width": 200, "height": 200},
        "namespaces": [{"name": "demo/value", "state_engine": "json"}],
        "widgets": [{"type": "label", "text": "hi"}],
    }
    app = RenderApp(parse_widget_tree(cfg, base_dir=os.path.dirname(SCENE)))
    assert app._compose_send_namespace() is None
    calls = _spy_publish(app)
    _app_with_events(app, [DropFileEvent("/tmp/dropped.png")])

    assert app.process_events() is True
    assert calls == []


if __name__ == "__main__":
    test_drop_publishes_compose_image_on_console_scene()
    test_drop_uses_absolute_path()
    test_console_send_namespace_resolved()
    test_non_console_scene_does_not_publish_on_drop()
    print("PASS: console drop -> compose image publish")
