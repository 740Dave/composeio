"""
Console interactivity pass — chat_thread scrollback + composer wiring.

Covers the two additions layered on the FROZEN console wiring:

1. ChatThreadWidget scrollback: the bottom-anchored offset state,
   on_scroll clamp semantics (raw SDL wheel dy, dy>0 = wheel-up = older),
   the auto-scroll rule (pinned stays pinned; a scrolled-up reader keeps
   their read position when a new turn lands), and the overflow-only
   scrollbar.

2. TextInputWidget console composer mode: the chrome="console" draw, the
   clear_on_commit chat semantics (clear + one-shot bus-echo suppression,
   no blur-commit), and the commit_namespace trigger topic that the SEND
   button's declarative publish_namespace tap binding drives — proven
   end-to-end through the runtime's pointer dispatch.

The frozen seam stays frozen: on_bus_state message handling, subscribed
namespaces, the ring buffer, _resolve_voice, and the composer's publish
namespace `intelligence/architect/compose` are asserted intact elsewhere
(test_console_fidelity.test_console_scene_frozen_subscriptions_intact).
"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from tests.render.console_scene_ids import chat_ns, narration_ns as _narration_ns
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import ChatThreadWidget, TextInputWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.chat_thread import _SCROLL_STEP

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


def _canvas(w=456, h=300):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x16)
    c.fill_rect(0, 0, w, h)
    return c


def _thread(n_messages: int, h: float = 220.0) -> ChatThreadWidget:
    """A thread with *n* david turns drawn once into a small viewport so
    heights/max_scroll are measured (scroll math is draw-reconciled)."""
    w = ChatThreadWidget("t", font_size=12)
    for i in range(n_messages):
        w.on_bus_state("intelligence/architect/in",
                       {"from": "david", "text": f"turn number {i}"})
    c = _canvas()
    w.draw(c, Rect(8, 8, 440, h))
    return w


class _Ctx:
    def __init__(self):
        self.published = []
        self.theme = "dark"
        self.reduced_motion = False

    def request_redraw(self, rect=None):
        pass

    def publish(self, ns, value, source=""):
        self.published.append((ns, value))


# ── TASK 1: scroll offset math ────────────────────────────────────────────────

def test_scroll_offset_starts_pinned_at_zero():
    w = _thread(30)
    assert w._scroll_offset == 0.0
    assert w._max_scroll > 0.0          # 30 turns overflow the viewport


def test_on_scroll_wheel_up_scrolls_toward_older_and_clamps():
    w = _thread(30)
    w.on_scroll(0, 1)                    # one wheel-up notch
    assert w._scroll_offset == min(w._max_scroll, _SCROLL_STEP)
    w.on_scroll(0, 10_000)               # a huge flick clamps at the top
    assert w._scroll_offset == w._max_scroll
    w.on_scroll(0, -10_000)              # and back down clamps at zero
    assert w._scroll_offset == 0.0


def test_on_scroll_never_scrolls_when_content_fits():
    w = _thread(1)
    assert w._max_scroll == 0.0
    w.on_scroll(0, 500)
    assert w._scroll_offset == 0.0


def test_pinned_at_bottom_stays_pinned_on_new_message():
    w = _thread(30)
    assert w._scroll_offset == 0.0
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "a brand new turn"})
    w.draw(_canvas(), Rect(8, 8, 440, 220))
    assert w._scroll_offset == 0.0       # still pinned to the bottom


def test_scrolled_up_reader_keeps_their_place_on_new_message():
    w = _thread(30)
    w.on_scroll(0, 4)                    # scroll up a few notches
    before = w._scroll_offset
    assert before > 0.0

    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "another turn lands"})
    c = _canvas()
    w.draw(c, Rect(8, 8, 440, 220))

    # offset grew by EXACTLY the appended block (its height + the turn
    # gap): the turns on screen did not move.
    rect_w = 440.0
    pad_x = max(18.0, min(40.0, rect_w * 0.04))
    content_w = max(60.0, rect_w - 2 * pad_x)
    new_h = w._paint_turn(c, w._messages[-1], 8 + pad_x, content_w, 0.0,
                          ct.CONSOLE.active, measure_only=True)
    assert abs(w._scroll_offset - (before + new_h + ct.GAP_TURN)) < 0.01


def test_scrolled_offset_stable_across_redraws():
    w = _thread(30)
    w.on_scroll(0, 3)
    once = w._scroll_offset
    w.draw(_canvas(), Rect(8, 8, 440, 220))
    w.draw(_canvas(), Rect(8, 8, 440, 220))
    assert w._scroll_offset == once      # no drift without new content


def test_thread_is_interactive_for_scroll_hit_testing():
    # the runtime routes "scroll" via root.hit_test_tree, which only
    # returns interactive widgets — the thread must opt in.
    w = ChatThreadWidget("t")
    assert w.interactive is True
    w.arranged_rect = Rect(0, 0, 400, 300)
    assert w.hit_test(200, 150)


class _ScrollbarSpy:
    """Records the only two Canvas calls _draw_scrollbar makes."""

    def __init__(self):
        self.round_rects = 0

    def set_fill_color(self, *a):
        pass

    def fill_round_rect(self, *a):
        self.round_rects += 1


def test_scrollbar_drawn_only_on_overflow():
    w = _thread(30)
    t = ct.CONSOLE.active
    spy = _ScrollbarSpy()
    w._draw_scrollbar(spy, 8, 8, 440, 220, ct.PAD_THREAD_Y,
                      avail=100.0, content_h=500.0, t=t)
    assert spy.round_rects == 2          # track + thumb
    spy = _ScrollbarSpy()
    w._draw_scrollbar(spy, 8, 8, 440, 220, ct.PAD_THREAD_Y,
                      avail=500.0, content_h=100.0, t=t)
    assert spy.round_rects == 0          # fits -> no scrollbar


def test_scrolled_draw_paints_without_error_at_extremes():
    w = _thread(40)
    for dy in (2, 10_000, -10_000):
        w.on_scroll(0, dy)
        w.draw(_canvas(), Rect(8, 8, 440, 220))


# ── TASK 2: composer field (console chrome + chat commit semantics) ──────────

def test_console_chrome_draws_placeholder_text_and_caret():
    w = TextInputWidget("f", placeholder="type here…", chrome="console")
    c = _canvas(400, 48)
    w.draw(c, Rect(4, 4, 392, 32))       # empty -> placeholder path
    w.text, w.cursor = "hello architect", 5
    w.focused = True
    w._now = lambda: 0.2                 # caret-on half of the blink
    w.draw(c, Rect(4, 4, 392, 32))       # text + accent caret path
    w._now = lambda: 0.8                 # caret-off half
    w.draw(c, Rect(4, 4, 392, 32))


def test_default_chrome_untouched_by_console_params():
    w = TextInputWidget("f", label="Name", placeholder="…")
    assert w.chrome == "" and w.clear_on_commit is False
    w.draw(_canvas(220, 80), Rect(4, 4, 212, 72))   # legacy path still paints


def test_commit_trigger_topic_commits_like_enter():
    w = TextInputWidget("f", namespace="intelligence/architect/compose",
                        clear_on_commit=True,
                        commit_namespace=_send_ns())
    ctx = _Ctx()
    w.on_mount(ctx)
    w.text, w.cursor = "hello via send", 14
    w.on_bus_state(_send_ns(), 1)   # the SEND toggle
    assert ctx.published == [("intelligence/architect/compose",
                              "hello via send")]
    assert w.text == "" and w.cursor == 0             # cleared on send


def test_commit_trigger_with_empty_field_publishes_nothing():
    w = TextInputWidget("f", namespace="intelligence/architect/compose",
                        clear_on_commit=True,
                        commit_namespace=_send_ns())
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state(_send_ns(), 1)
    assert ctx.published == []


def test_bus_echo_of_sent_value_never_refills_the_field():
    w = TextInputWidget("f", namespace="intelligence/architect/compose",
                        clear_on_commit=True)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.text, w.cursor = "sent turn", 9
    w.commit()
    assert w.text == ""
    # the bus echoes the committed value back on the bound namespace
    w.on_bus_state("intelligence/architect/compose", "sent turn")
    assert w.text == ""                  # suppressed, stays cleared
    # suppression is ONE-shot: a later restore still reflects
    w.on_bus_state("intelligence/architect/compose", "restored value")
    assert w.text == "restored value"


def test_chat_mode_blur_does_not_send_a_half_typed_turn():
    w = TextInputWidget("f", namespace="intelligence/architect/compose",
                        clear_on_commit=True)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.text = "half a thought"
    w.on_blur()
    assert ctx.published == []           # no send
    assert w.text == "half a thought"    # draft intact


def test_form_mode_blur_still_commits_unchanged():
    w = TextInputWidget("f", namespace="settings/name")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.text = "gilligan"
    w.on_blur()
    assert ctx.published == [("settings/name", "gilligan")]
    assert w.text == "gilligan"          # form fields never clear


# ── scene wiring + end-to-end through the runtime ────────────────────────────

def _find(root, wid):
    for w in root.iter_widgets():
        if w.widget_id == wid:
            return w
    raise AssertionError(f"widget {wid} not in tree")


def test_scene_wires_send_button_and_commit_trigger():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    field = _find(comp.root, _cf())
    send = _find(comp.root, _cs())

    # FROZEN: the publish namespace the bridge depends on, exactly.
    assert field.bound_namespace == chat_ns("compose")
    assert field.chrome == "console" and field.clear_on_commit is True

    # SEND: declarative tap publish -> the field's commit trigger topic.
    assert send.interactive is True
    assert getattr(send, "_publish_namespace") == \
        _send_ns()
    assert field.commit_namespace == _send_ns()
    assert (_send_ns(), field) in comp.subscriptions


def _tap(app, x, y):
    app._dispatch_pointer(SimpleNamespace(type="down", x=x, y=y,
                                          pointer_id=0))
    app._dispatch_pointer(SimpleNamespace(type="up", x=x, y=y,
                                          pointer_id=0))


def test_send_tap_publishes_field_text_end_to_end():
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        field = _find(app.comp.root, _cf())
        send = _find(app.comp.root, _cs())
        field.text, field.cursor = "hello from the send button", 5

        r = send.arranged_rect
        _tap(app, r.x + r.w / 2, r.y + r.h / 2)
        app.drain_updates()              # toggle -> commit -> echo (all)

        assert app.bus.get_state(chat_ns("compose")) == \
            "hello from the send button"
        assert field.text == ""          # cleared, echo suppressed
    finally:
        app.stop()


def test_wheel_over_thread_scrolls_it_end_to_end():
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        thread = _find(app.comp.root, _th())
        for i in range(60):              # overflow even the 962px hero
            # the narration leg is PER-INSTANCE ([CXC-FC-UNIT]) — publishing the host's
            # topic would fill nobody's thread, which is exactly the mis-keying a pane
            # makes impossible.
            app.publish(_narration_ns(),
                        {"from": "architect", "text": f"turn {i}"})
        app.drain_updates()
        app.render_frame()               # measure -> max_scroll

        r = thread.arranged_rect
        cx, cy = r.x + r.w / 2, r.y + r.h / 2
        assert app.comp.root.hit_test_tree(cx, cy) is thread

        app._dispatch_pointer(SimpleNamespace(type="scroll", x=cx, y=cy,
                                              dx=0.0, dy=3.0))
        assert thread._scroll_offset == min(thread._max_scroll,
                                            3.0 * _SCROLL_STEP) > 0.0
        app.render_frame()               # scrolled draw + scrollbar paint

        # a new turn while scrolled up must NOT yank the reader down
        before = thread._scroll_offset
        app.publish(_narration_ns(),
                    {"from": "architect", "text": "one more turn"})
        app.drain_updates()
        app.render_frame()
        assert thread._scroll_offset > before
    finally:
        app.stop()
