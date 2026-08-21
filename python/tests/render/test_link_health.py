"""Tests for the Intelligence Console CONNECTION-HEALTH indicator
([SPEC:CXC-CONSOLE-VISUAL] link health).

The bug this fixes: David typed messages that "looked as though they went
through when they didn't" — a send echoes to the thread via channel_bridge but
that is decoupled from whether channel.mjs delivered it.  The live-dot now
makes the disconnection VISIBLE.

Covered here:
  * health computed from (heartbeat-fresh AND transport-connected), with the
    two offline states named apart (channel vs bus);
  * the indicator REFLECTS unhealthy — crit/warn colour, NOT beating, and the
    verdict published to console/link/state;
  * the indicator's damage region REPAINTS on a health flip ([CXC-R-DAMAGE]);
  * the scene wiring: live_dot is the LinkHealthWidget, it subscribes the
    heartbeat, and the header label + SEND button bind to console/link/state,
    whose DEFAULTS reproduce today's healthy static look.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.link_health import (BUS_OFFLINE, CHANNEL_OFFLINE,
                                                  ENGAGED, LinkHealthWidget)
from tests.render.console_scene_ids import (  # noqa: E402
    chat_id as _chat_id,
    chat_ns,
    composer_send_label as _csl)

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")


class _Transport:
    def __init__(self, connected=True):
        self.is_connected = connected


class _Bus:
    def __init__(self, transport):
        self.global_transport = transport


class _Ctx:
    """Mount context stub carrying a bus/transport + recording publishes."""
    def __init__(self, connected=True, has_transport=True,
                 reduced_motion=False):
        self.theme = "dark"
        self.reduced_motion = reduced_motion
        self.bus = _Bus(_Transport(connected) if has_transport else None)
        self.published = []

    def publish(self, ns, value, source=""):
        self.published.append((ns, value))

    def request_redraw(self, rect=None):
        pass

    def register_clocked(self, w):
        pass

    def unregister_clocked(self, w):
        pass


def teardown_function(_fn):
    ct.set_theme("dark")


# ===========================================================================
# health computation — (fresh AND connected), offline states named apart
# ===========================================================================

def test_compute_state_truth_table():
    w = LinkHealthWidget("d", fresh_threshold_s=12.0)
    w._mount_mono = 100.0
    # no heartbeat yet -> grace off mount time
    assert w.compute_state(105.0, True) == ENGAGED
    # 20s since mount, still no beat -> channel stale
    assert w.compute_state(120.0, True) == CHANNEL_OFFLINE
    # a fresh beat lands
    w._last_alive_mono = 118.0
    assert w.compute_state(120.0, True) == ENGAGED       # 2s old
    assert w.compute_state(130.0, True) == ENGAGED       # 12s: within threshold
    assert w.compute_state(130.1, True) == CHANNEL_OFFLINE  # 12.1s: stale
    # bus down wins over freshness (and names the failure differently)
    assert w.compute_state(120.0, False) == BUS_OFFLINE
    # no transport configured (headless/demo) -> not applicable, stay engaged
    assert w.compute_state(9e9, None) == ENGAGED


def test_heartbeat_arrival_stamps_freshness():
    w = LinkHealthWidget("d", namespace="intelligence/architect/channel/alive")
    assert w._last_alive_mono is None
    w.on_bus_state("intelligence/architect/channel/alive",
                   {"ts": 1, "id": "architect"})
    assert w._last_alive_mono is not None
    # an unrelated namespace does NOT count as a heartbeat
    prev = w._last_alive_mono
    w.on_bus_state("thread/narration", {"text": "hi"})
    assert w._last_alive_mono == prev


# ===========================================================================
# the indicator reflects health (colour + beat + published verdict)
# ===========================================================================

def test_engaged_beats_green_and_publishes():
    w = LinkHealthWidget("d")
    ctx = _Ctx(connected=True)
    w.on_mount(ctx)
    w._last_alive_mono = time.monotonic()        # fresh
    animating = w.on_clock_tick(0.0)             # phase 0 -> opacity 1.0
    assert w._state == ENGAGED
    assert w.fill_color == ct.DARK.ok
    assert animating is True                     # engaged -> beats every frame
    assert ("console/link/state", ENGAGED) in ctx.published
    # half-period -> dimmed (the beat is live)
    w.on_clock_tick(ct.BEAT_PERIOD / 2.0)
    assert w.opacity < 1.0


def test_channel_offline_reflects_red_and_still():
    w = LinkHealthWidget("d")
    ctx = _Ctx(connected=True)
    w.on_mount(ctx)
    w._last_alive_mono = time.monotonic() - 100  # stale heartbeat
    flipped = w.on_clock_tick(0.0)
    assert w._state == CHANNEL_OFFLINE
    assert w.fill_color == ct.DARK.crit
    assert w.opacity == 1.0                       # NOT beating
    assert flipped is True                        # repaint on the flip frame
    assert ("console/link/state", CHANNEL_OFFLINE) in ctx.published
    # steady offline -> no repaint, still not beating
    assert w.on_clock_tick(1.3) is False
    assert w.opacity == 1.0


def test_bus_offline_reflects_amber():
    w = LinkHealthWidget("d")
    ctx = _Ctx(connected=False)
    w.on_mount(ctx)
    w._last_alive_mono = time.monotonic()         # fresh, but bus is down
    w.on_clock_tick(0.0)
    assert w._state == BUS_OFFLINE
    assert w.fill_color == ct.DARK.warn
    assert w.opacity == 1.0
    assert ("console/link/state", BUS_OFFLINE) in ctx.published


def test_publish_only_on_change():
    w = LinkHealthWidget("d")
    ctx = _Ctx(connected=True)
    w.on_mount(ctx)
    w._last_alive_mono = time.monotonic()
    w.on_clock_tick(0.0)
    w.on_clock_tick(0.1)
    w.on_clock_tick(0.2)
    # engaged throughout -> exactly one publish (the initial verdict)
    assert ctx.published.count(("console/link/state", ENGAGED)) == 1


# ===========================================================================
# damage: a flip to a STATIC offline state must still repaint ([CXC-R-DAMAGE])
# ===========================================================================

def test_flip_marks_damage_even_when_not_beating():
    from composeio.render.engine.runtime import RenderApp
    from composeio.render.engine.damage import DamageRegion

    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        dot = None
        for w in app.comp.root.iter_widgets():
            if getattr(w, "widget_id", None) == "live_dot":
                dot = w
                break
        assert isinstance(dot, LinkHealthWidget)
        assert dot.arranged_rect is not None
        assert any(dot is c for c in app._clocked), "dot not registered clocked"

        # Force the healthy->offline flip into a STATIC state, and neutralize
        # beating (reduced motion) so the ONLY reason to repaint is the flip.
        dot._connected = lambda: True
        dot._last_alive_mono = time.monotonic() - 100   # stale -> channel off
        dot._state = ENGAGED
        dot._published_state = ENGAGED
        app.comp.reduced_motion = True
        dot._ctx.reduced_motion = True

        app._damage = DamageRegion()                    # clear
        app._tick_clocked(0.0)

        assert dot._state == CHANNEL_OFFLINE
        assert dot.opacity == 1.0                       # confirmed not beating
        assert app._damage.intersects(dot.paint_bounds()), \
            "a static offline flip must damage the dot's rect so it repaints"
    finally:
        app.stop()


# ===========================================================================
# scene wiring + healthy static defaults
# ===========================================================================

def _find(w, wid):
    if getattr(w, "widget_id", None) == wid:
        return w
    for ch in getattr(w, "children", []) or []:
        r = _find(ch, wid)
        if r is not None:
            return r
    return None


def test_scene_live_dot_is_link_health_and_subscribes_heartbeat():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    dot = _find(comp.root, "live_dot")
    assert isinstance(dot, LinkHealthWidget)
    subbed = [ns for ns, _ in comp.subscriptions]
    assert chat_ns("channel/alive") in subbed


def test_scene_label_and_send_bind_to_link_state_with_healthy_defaults():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)

    # THE VERDICT TOPIC IS NOW PER-SURFACE, and that is the point ([CXC-FC-SPLIT-RULE]).
    # The header label is CONSOLE-SELF chrome and keeps `console/link/state`.  The SEND
    # label is a PANE widget and binds `_app/<instance_id>/link_state`, because "is MY
    # channel up?" could differ between two Intelligences — and with one shared topic,
    # one instance going dark would grey out the other's SEND button.
    def bound(wid, attr, ns):
        return any(b.widget is not None
                   and getattr(b.widget, "widget_id", None) == wid
                   and b.attr == attr
                   and b.namespace == ns
                   for b in comp.attr_bindings)

    for wid, ns in (("link_label", "console/link/state"),
                    (_csl(), f"_app/{_chat_id()}/link_state")):
        assert bound(wid, "text", ns), f"{wid}.text not bound to {ns}"
        assert bound(wid, "color", ns), f"{wid}.color not bound to {ns}"

    # DEFAULTS reproduce today's healthy static look (console/link/state unset)
    label = _find(comp.root, "link_label")
    assert label.text == "ENGAGED"
    assert tuple(label.color) == (0x2F, 0xD0, 0x7B)
    send = _find(comp.root, _csl())
    assert send.text == "SEND"
    # the pane's SEND ink is the pane panel's, not the old console composer's
    assert tuple(send.color) == (0x0A, 0x0F, 0x16)


def test_scene_offline_publish_drives_label_text_and_colour():
    # end-to-end through the real engine: publishing the verdict flips the
    # bound header label's text + colour (the bus-driven repaint path).
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        label = _find(app.comp.root, "link_label")
        assert label.text == "ENGAGED"                  # default healthy
        app.publish("console/link/state", CHANNEL_OFFLINE, source="test")
        app.drain_updates()
        assert label.text == "CHANNEL OFFLINE"
        assert tuple(label.color) == (0xFF, 0x3B, 0x5C)
        assert app._damage.is_full                       # bus change repaints
    finally:
        app.stop()
