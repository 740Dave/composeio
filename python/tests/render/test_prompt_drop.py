"""
Drop a file onto the MESSAGE PROMPT text area (5b).

Today a drop anywhere on the console publishes an image message to the thread.
David: "I should be able to drop into the message prompt text area as well."
The ONE drop path (runtime._handle_drop) is now position-aware: a drop that
lands on the composer text_input (``wants_file_drop``) inserts the path at the
caret; a drop anywhere else keeps the historical whole-window thread publish.
No second SDL drop pipeline — the same handler, re-targeted.
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
from composeio.render.widgets import TextInputWidget
from composeio.render.window import DropFileEvent

# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.
from tests.render.console_scene_ids import (composer_field as _cf,  # noqa: E402
                                              composer_send as _cs,
                                              send_trigger_ns as _send_ns,
                                              thread as _th)

REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")


class _FakeCanvas:
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


# -- the widget-level insert --------------------------------------------------

def test_text_input_on_file_drop_inserts_path_at_cursor():
    w = TextInputWidget("f", chrome="console", clear_on_commit=True,
                        namespace="intelligence/architect/compose")
    w.text = "look at "
    w.cursor = len(w.text)
    assert w.wants_file_drop is True
    accepted = w.on_file_drop("/tmp/report.png")
    assert accepted is True
    assert w.text == "look at /tmp/report.png "     # trailing space for a caption
    assert w.cursor == len(w.text)


def test_text_input_drop_replaces_selection():
    w = TextInputWidget("f", chrome="console")
    w.text = "REPLACE"
    w.cursor = 7
    w.select_anchor = 0                              # whole field selected
    w.on_file_drop("/tmp/a.png")
    assert w.text == "/tmp/a.png "


def test_secret_field_refuses_drop():
    w = TextInputWidget("pw", secret=True)
    assert w.on_file_drop("/tmp/secret.png") is False
    assert w.text == ""


def test_empty_path_refused():
    w = TextInputWidget("f", chrome="console")
    assert w.on_file_drop("") is False


# -- runtime routing (the ONE drop path, re-targeted) -------------------------

def _prompt_widget(app):
    for wdg in app.comp.root.iter_widgets():
        if getattr(wdg, "widget_id", None) == _cf():
            return wdg
    raise AssertionError("composer_field not found in the console scene")


def _prompt_center(app):
    app._layout()            # arrange so hit_test has rects
    pw = _prompt_widget(app)
    r = pw.arranged_rect
    assert r is not None, "prompt has no arranged rect after layout"
    return pw, (r.x + r.w / 2.0, r.y + r.h / 2.0)


def test_drop_on_prompt_inserts_and_does_not_publish():
    app = RenderApp.from_file(SCENE)
    calls = _spy_publish(app)
    pw, (cx, cy) = _prompt_center(app)
    _app_with_events(app, [DropFileEvent("/tmp/dropped.png", cx, cy)])

    assert app.process_events() is True
    # inserted into the PROMPT, and NO thread publish fired
    assert pw.text == os.path.abspath("/tmp/dropped.png") + " "
    assert calls == [], calls
    # the field is focused so an immediate Enter would send it
    assert app.focus.focused is pw


def test_drop_elsewhere_still_publishes_to_thread():
    # a drop NOT over the prompt keeps the historical whole-window behavior
    app = RenderApp.from_file(SCENE)
    calls = _spy_publish(app)
    app._layout()
    _app_with_events(app, [DropFileEvent("/tmp/dropped.png", 5.0, 5.0)])

    assert app.process_events() is True
    assert len(calls) == 1, calls
    ns, val, source = calls[0]
    assert ns == chat_ns("compose")
    assert val == {"from": "david", "image": os.path.abspath("/tmp/dropped.png")}
    assert source == "console-drop"


def test_drop_without_position_falls_back_to_publish():
    # a DropFileEvent with no coords (x,y = -1) is the pre-5b path: publish
    app = RenderApp.from_file(SCENE)
    calls = _spy_publish(app)
    app._layout()
    _app_with_events(app, [DropFileEvent("/tmp/dropped.png")])   # no x/y

    assert app.process_events() is True
    assert len(calls) == 1
    assert calls[0][1]["image"] == os.path.abspath("/tmp/dropped.png")


def test_non_console_scene_drop_on_prompt_is_still_safe():
    # a scene with no composer + no compose namespace: a positioned drop over a
    # non-drop widget publishes nothing (no crash, no stray compose)
    cfg = {
        "app": {"id": "plain"},
        "design": {"title": "plain", "width": 200, "height": 200},
        "namespaces": [{"name": "demo/value", "state_engine": "json"}],
        "widgets": [{"type": "label", "text": "hi"}],
    }
    app = RenderApp(parse_widget_tree(cfg, base_dir=os.path.dirname(SCENE)))
    calls = _spy_publish(app)
    app._layout()
    _app_with_events(app, [DropFileEvent("/tmp/x.png", 100.0, 100.0)])
    assert app.process_events() is True
    assert calls == []


if __name__ == "__main__":
    test_text_input_on_file_drop_inserts_path_at_cursor()
    test_text_input_drop_replaces_selection()
    test_secret_field_refuses_drop()
    test_empty_path_refused()
    test_drop_on_prompt_inserts_and_does_not_publish()
    test_drop_elsewhere_still_publishes_to_thread()
    test_drop_without_position_falls_back_to_publish()
    test_non_console_scene_drop_on_prompt_is_still_safe()
    print("PASS: prompt file-drop")
