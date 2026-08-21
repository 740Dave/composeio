"""
Tests for the Intelligence Console VISUAL-FIDELITY pass
([SPEC:CXC-CONSOLE-VISUAL]).

Additive to the existing suite ([CXC-ICV-ACCEPT] ICV-C9/C10): the design
system (token tables + theme swap), the semicircle gauge style MODE (dial
untouched), the four-voice chat emission + harness packet variants, the
NEW RegisteredChecklistWidget / SelfLedgerWidget / AvatarWidget, the
reduced-motion gate, engine plumbing (design.reduced_motion + theme on
the context), and the real console scene: frozen subscriptions intact
(ICV-C2) + a non-trivial headless render (ICV-C1).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from tests.render.console_scene_ids import chat_ns
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import (AvatarWidget, ChatThreadWidget,
                                      GaugeWidget, MermaidWidget,
                                      RegisteredChecklistWidget,
                                      SelfLedgerWidget)
from composeio.render.widgets import console_theme as ct

from .conftest import surface_to_array

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")


def _canvas(w=420, h=340):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x07, 0x0A, 0x0F)
    c.fill_rect(0, 0, w, h)
    return c


class _Ctx:
    """Minimal mount context stub (theme + reduced_motion plumbing)."""
    def __init__(self, theme="dark", reduced_motion=False):
        self.theme = theme
        self.reduced_motion = reduced_motion

    def request_redraw(self, rect=None):
        pass

    def register_clocked(self, w):
        pass

    def unregister_clocked(self, w):
        pass


def teardown_function(_fn):
    ct.set_theme("dark")     # tests never leak a theme swap


# =============================================================================
# [CXC-ICV-TOKENS] / [CXC-ICV-THEME] — the design system
# =============================================================================

def test_both_token_tables_carry_every_role():
    from dataclasses import fields
    for table in (ct.DARK, ct.LIGHT):
        for f in fields(ct.ConsoleTheme):
            v = getattr(table, f.name)
            assert v is not None
            if f.name != "name":
                assert len(v) == 4          # RGBA 0-255 form


def test_dark_tokens_verbatim_from_previs():
    assert ct.DARK.bg[:3] == (0x07, 0x0A, 0x0F)
    assert ct.DARK.accent[:3] == (0x4E, 0xA8, 0xFF)
    assert ct.DARK.accent_dim == (78, 168, 255, 33)     # rgba(.13)
    assert ct.DARK.crit[:3] == (0xFF, 0x3B, 0x5C)
    assert ct.LIGHT.accent[:3] == (0x1F, 0x7F, 0xE0)


def test_theme_is_a_token_swap():
    assert ct.CONSOLE.active is ct.DARK
    ct.set_theme("light")
    assert ct.CONSOLE.active is ct.LIGHT
    ct.set_theme("unknown-name")            # graceful: keeps current
    assert ct.CONSOLE.active is ct.LIGHT
    assert ct.toggle_theme() == "dark"
    assert ct.CONSOLE.active is ct.DARK


def test_semantic_rule_tokens_are_distinct_roles():
    # accent is the identity; ok/warn/crit are STATE — all four distinct
    hues = {ct.DARK.accent[:3], ct.DARK.ok[:3], ct.DARK.warn[:3],
            ct.DARK.crit[:3]}
    assert len(hues) == 4


def test_motion_timings_lifted_from_previs():
    assert ct.BEAT_PERIOD == 2.6 and ct.BEAT_LOW == 0.35
    assert ct.BREATHE_PERIOD == 5.5
    assert ct.BLINK_PERIOD == 6.4
    assert ct.HOTPULSE_PERIOD == 2.4 and ct.HOTPULSE_LOW == 0.45
    assert ct.RUNPULSE_PERIOD == 1.6
    # rest frame at phase 0; the dip at the half period
    assert abs(ct.pulse(0.0, 2.4, 0.45) - 1.0) < 1e-9
    assert abs(ct.pulse(1.2, 2.4, 0.45) - 0.45) < 1e-9


# =============================================================================
# [CXC-ICV-GAUGE] — the semicircle style MODE (generalize, don't fork)
# =============================================================================

def test_gauge_default_style_is_the_dial():
    g = GaugeWidget("g")
    assert g.style == "dial"


def test_gauge_float_payload_path_unchanged():
    g = GaugeWidget("g", style="semicircle")
    g.on_bus_state("self/context_pct", 42.5)
    assert g.value == 42.5
    g.on_bus_state("self/context_pct", "not-a-number")
    assert g.value == 42.5                     # ignored, as before


def test_gauge_dict_payload_additive_read():
    g = GaugeWidget("g", style="semicircle")
    g.on_bus_state("self/context_pct",
                   {"pct": 90.0, "tokens": 180000, "max_tokens": 200000,
                    "source": "stdin · used_percentage"})
    assert g.value == 90.0
    assert g._tokens == 180000 and g._max_tokens == 200000
    assert "stdin" in g._source


def test_gauge_state_color_only_widget_that_warms():
    g = GaugeWidget("g", style="semicircle", warning_threshold=65,
                    mid_threshold=80, danger_threshold=90)
    g.value = 40
    assert g._state_color(ct.DARK) == ct.DARK.ok
    g.value = 72
    assert g._state_color(ct.DARK) == ct.DARK.warn
    g.value = 90
    assert g._state_color(ct.DARK) == ct.DARK.crit


def test_gauge_semicircle_draws_at_40_and_90():
    for val in (40.0, 90.0):
        g = GaugeWidget("g", style="semicircle", value=val,
                        warning_threshold=65, danger_threshold=90,
                        label="self / context_level",
                        ns_tag="self/context_pct")
        c = _canvas(340, 340)
        g.draw(c, Rect(4, 4, 332, 330))
        arr = surface_to_array(c)
        assert arr[..., :3].std() > 3.0


def test_gauge_hotpulse_gates():
    g = GaugeWidget("g", style="semicircle", value=95,
                    warning_threshold=65, danger_threshold=90)
    g._ctx = _Ctx(reduced_motion=False)
    assert g.on_clock_tick(1.2) is True        # crit zone -> animating
    assert g._lead_opacity < 1.0
    g._ctx = _Ctx(reduced_motion=True)          # ICV-C7: rest frame
    assert g.on_clock_tick(1.2) is False
    assert g._lead_opacity == 1.0
    g._ctx = _Ctx(reduced_motion=False)
    g.value = 40                                # below crit: no animation
    assert g.on_clock_tick(1.2) is False
    assert g._lead_opacity == 1.0


def test_gauge_dial_still_draws():
    g = GaugeWidget("g", value=65, warning_threshold=50,
                    danger_threshold=80)
    c = _canvas(220, 220)
    g.draw(c, Rect(10, 10, 200, 200))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 3.0


# =============================================================================
# [CXC-ICV-CHAT] — voice tags + packet variants
# =============================================================================

def _thread_with_all_voices():
    w = ChatThreadWidget("t", label="")
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "Go on — T3."})
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect", "text": "T3 settled.",
                    "emit": ["git/graph", "thread/diagram"]})
    w.on_bus_state("thread/narration",
                   {"from": "architect",
                    "text": "Externalizing the structure to LTS."})
    w.on_bus_state("intelligence/architect/in",
                   {"from": "harness", "kind": "context-report",
                    "text": "context ~19.4%", "pct": 19.4})
    w.on_bus_state("intelligence/architect/in",
                   {"from": "harness", "kind": "harness-heartbeat",
                    "text": "quiescence — still engaged."})
    w.on_bus_state("intelligence/architect/in",
                   {"from": "harness", "kind": "context-compact-guidance",
                    "text": "context 90% — prepare to compact.",
                    "items": ["land the WIP to LTS",
                              "refine the Dusk Gifts",
                              "author & emit the /compact"],
                    "threshold": 90, "source": "stdin"})
    return w


def test_voice_tags_resolved_from_same_logic():
    w = _thread_with_all_voices()
    voices = [m.voice for m in w._messages]
    assert voices == ["david", "reply", "narration",
                      "harness", "harness", "harness"]
    assert w._messages[3].kind == "context-report"


def test_packet_variant_selection():
    w = _thread_with_all_voices()
    assert w._packet_variant(w._messages[3]) == "report"
    assert w._packet_variant(w._messages[4]) == "beat"
    assert w._packet_variant(w._messages[5]) == "guidance"


def test_packet_attrs_additive_read():
    w = _thread_with_all_voices()
    attrs = dict(w._packet_attrs(w._messages[5].payload))
    assert attrs.get("threshold") == "90"
    assert attrs.get("source") == "stdin"
    assert "text" not in attrs and "items" not in attrs


def test_chat_draws_all_four_voices_with_packets():
    w = _thread_with_all_voices()
    c = _canvas(900, 700)
    w.draw(c, Rect(0, 0, 900, 700))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 5.0


def test_chat_original_voice_colors_intact():
    # the pre-fidelity semantics ([CXC-ICV-INTENT]: wiring frozen)
    w = ChatThreadWidget("t")
    w.on_bus_state("thread/narration", {"text": "hm"})
    assert w._messages[0].color == w.dim
    w.on_bus_state("intelligence/architect/in",
                   {"from": "harness", "text": "beat"})
    assert w._messages[1].align == "center"


# =============================================================================
# [CXC-ICV-REGISTERED] — the checklist (NEW)
# =============================================================================

def test_registered_payload_shapes():
    w = RegisteredChecklistWidget("r")
    w.on_bus_state("self/registered", [
        {"name": "a.md", "path": "docs/", "ok": True},
        {"name": "b.md", "path": "docs/", "ok": False}])
    assert w._items == [("a.md", "docs/", True), ("b.md", "docs/", False)]
    w.on_bus_state("self/registered",
                   {"items": [{"name": "c.md", "ok": True}]})
    assert w._items[0][0] == "c.md"
    w.on_bus_state("self/registered", {"d.md": True, "e.md": False})
    assert ("d.md", "", True) in w._items and ("e.md", "", False) in w._items
    w.on_bus_state("self/registered", '[{"name":"f.md","ok":true}]')
    assert w._items == [("f.md", "", True)]


def test_registered_draw_counts_and_paints():
    w = RegisteredChecklistWidget("r")
    w.on_bus_state("self/registered", [
        {"name": "composeux-architect.md", "path": ".claude/agents/",
         "ok": True}] * 4)
    c = _canvas(340, 160)
    w.draw(c, Rect(4, 4, 332, 150))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 3.0


def test_registered_parser_registration():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "registered_checklist", "id": "reg"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "self/registered" in subbed


# =============================================================================
# [CXC-ICV-LEDGER] — the telemetry grid (NEW)
# =============================================================================

def test_ledger_cells_graceful_then_populated():
    w = SelfLedgerWidget("l")
    cells = w._cell_values()
    assert all(v == "—" for _, v, _, _ in cells)
    # cost cell removed (KISS): three small cells remain
    assert [k for k, *_ in cells] == ["subagents", "compactions", "effort"]
    w.on_bus_state("self/effort", {"level": "high"})
    w.on_bus_state("self/compactions",
                   {"count": 3, "ids": ["c9", "c10", "c11"]})
    w.on_bus_state("self/subagents",
                   {"id": "s1", "status": "dispatched", "type": "gp"})
    w.on_bus_state("self/subagents",
                   {"id": "s2", "status": "done", "tokens": 63200})
    cells = dict((k, (v, s)) for k, v, s, _ in w._cell_values())
    assert "cost · session" not in cells
    assert cells["effort"][0] == "high"
    assert cells["compactions"] == ("3", "· c9·c10·c11")
    assert cells["subagents"][0] == "1"        # one still running
    assert "63k" in cells["subagents"][1]


def test_ledger_rate_limit_shapes():
    w = SelfLedgerWidget("l")
    w.on_bus_state("self/rate_limit",
                   {"5h": {"pct": 38, "resets": "14:22"}, "7d": 12})
    (k5, p5, s5, c5), (k7, p7, s7, c7) = w._rate_cells()
    assert p5 == 38 and "resets 14:22" in s5 and c5 == "accent"
    assert p7 == 12 and c7 == "ok"             # fixed colors: calm rule


def test_ledger_draw_paints():
    w = SelfLedgerWidget("l")
    w.on_bus_state("self/effort", {"level": "high"})
    w.on_bus_state("self/rate_limit", {"5h": 38, "7d": 12})
    c = _canvas(340, 260)
    w.draw(c, Rect(4, 4, 332, 250))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 3.0


def test_ledger_parser_wires_namespace_list():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "self_ledger", "id": "led",
         "namespaces": ["self/effort", "self/rate_limit"]}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "self/effort" in subbed and "self/rate_limit" in subbed


# =============================================================================
# [CXC-ICV-AVATAR] — presence (NEW)
# =============================================================================

def test_avatar_never_blank():
    a = AvatarWidget("a")
    assert a.drawn_state() == "grounded"
    a.on_bus_state("self/avatar/expression", "focused")
    assert a.drawn_state() == "focused"
    a.on_bus_state("self/avatar/expression", {"expression": "thinking"})
    assert a.drawn_state() == "contemplative"
    a.on_bus_state("self/avatar/expression", "some-unknown-enum-member")
    assert a.drawn_state() == "grounded"       # ICV-C6: never blank
    assert a.expression == "some-unknown-enum-member"  # label keeps the enum


def test_avatar_draws_every_state():
    for expr in ("grounded", "focused", "contemplative", "spark", "unknown-x"):
        a = AvatarWidget("a", expression=expr)
        c = _canvas(340, 220)
        a.draw(c, Rect(4, 4, 332, 210))
        arr = surface_to_array(c)
        assert arr[..., :3].std() > 3.0, expr


class _RecCanvas(SkiaCanvas):
    """Records the face draw calls that matter for the spark distinction:
    fill_path (only the spark sparkle uses it) and accent-colored
    fill_circle (the eye glints + spark micro-glints)."""
    def __init__(self):
        super().__init__()
        self.create_surface(140, 140)
        self._cur_fill = (0, 0, 0, 255)
        self.fill_paths = 0
        self.accent_circles = 0
        self._accent = tuple(ct.CONSOLE.active.accent)

    def set_fill_color(self, r, g, b, a=255):
        self._cur_fill = (r, g, b, a)
        super().set_fill_color(r, g, b, a)

    def fill_path(self, commands):
        self.fill_paths += 1
        super().fill_path(commands)

    def fill_circle(self, cx, cy, r):
        if self._cur_fill == self._accent:
            self.accent_circles += 1
        super().fill_circle(cx, cy, r)


def _face_calls(expr):
    a = AvatarWidget("a", expression=expr)
    c = _RecCanvas()
    a._draw_face(c, ct.CONSOLE.active)
    return c


def test_avatar_spark_is_first_class_not_grounded_fallback():
    # spark selects its OWN drawn path set — not the grounded fallback.
    a = AvatarWidget("a")
    a.on_bus_state("self/avatar/expression", "spark")
    assert a.drawn_state() == "spark"
    assert a.expression == "spark"
    # its path set is distinct: has the signature spark + eye_glint keys.
    from composeio.render.widgets.avatar import _paths
    assert _paths("spark").get("spark"), "spark path set missing sparkle"
    assert _paths("spark").get("eye_glints"), "spark missing eye glints"
    for other in ("grounded", "focused", "contemplative"):
        assert "spark" not in _paths(other)
        assert "eye_glints" not in _paths(other)


def test_avatar_spark_draws_glint_and_accent_others_do_not():
    spark = _face_calls("spark")
    # the temple sparkle is a filled accent path — unique to spark.
    assert spark.fill_paths >= 1, "spark drew no fill_path (sparkle)"
    # 2 eye glints + 2 spark micro-glints, all accent-colored.
    assert spark.accent_circles == 4, spark.accent_circles
    # the other three drawn faces do NEITHER — the distinction is real.
    for other in ("grounded", "focused", "contemplative"):
        rec = _face_calls(other)
        assert rec.fill_paths == 0, f"{other} drew a fill_path"
        assert rec.accent_circles == 0, f"{other} drew an accent glint"


def test_avatar_spark_explicit_only_unknown_still_grounded():
    # spark is explicit-only: only the literal value routes to it; every
    # other/unknown enum member still falls back to grounded (never blank).
    a = AvatarWidget("a", expression="some-unknown-enum-member")
    assert a.drawn_state() == "grounded"
    a.on_bus_state("self/avatar/expression", "sparkle")   # near-miss, not spark
    assert a.drawn_state() == "grounded"


def test_avatar_motion_and_reduced_motion_gate():
    a = AvatarWidget("a")
    a._ctx = _Ctx(reduced_motion=False)
    assert a.on_clock_tick(2.75) is True        # mid-breathe
    assert a._halo_scale > 1.0
    # blink window: 5.5%-6.5% of the 6.4s period
    assert a.on_clock_tick(6.4 * 0.06) is True
    assert a._blink == ct.BLINK_SCALE
    a._ctx = _Ctx(reduced_motion=True)          # ICV-C7: rest frame
    assert a.on_clock_tick(2.75) is False
    assert (a._halo_scale, a._halo_op, a._blink) == (1.0, 1.0, 1.0)


def test_avatar_parser_registration():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "avatar", "id": "p"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert "self/avatar/expression" in subbed


# =============================================================================
# [CXC-ICV-SUBAGENTS] runpulse gate
# =============================================================================

def test_subagent_runpulse_gates():
    from composeio.render.widgets import SubagentLedgerWidget
    w = SubagentLedgerWidget("s")
    w.on_bus_state("self/subagents",
                   {"id": "x", "status": "dispatched", "type": "gp"})
    w._ctx = _Ctx(reduced_motion=False)
    assert w.on_clock_tick(0.8) is True
    assert w._dot_opacity < 1.0
    w._ctx = _Ctx(reduced_motion=True)
    assert w.on_clock_tick(0.8) is False
    assert w._dot_opacity == 1.0
    w._ctx = _Ctx(reduced_motion=False)
    w.on_bus_state("self/subagents", {"id": "x", "status": "done"})
    assert w.on_clock_tick(0.8) is False        # nothing running


# =============================================================================
# [CXC-ICV-CANVAS] — option B chrome
# =============================================================================

def test_mermaid_console_chrome_draws():
    m = MermaidWidget("m", chrome="console",
                      source="graph LR\n  A --> B")
    c = _canvas(900, 180)
    m.draw(c, Rect(0, 0, 900, 170))
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 2.0


def test_mermaid_default_chrome_unchanged():
    m = MermaidWidget("m", source="graph LR\n  A --> B")
    assert m.chrome == ""
    c = _canvas(440, 320)
    m.draw(c, Rect(0, 0, 440, 320))            # the original panel


# =============================================================================
# [CXC-ICV-THEME] the live toggle (ICV-C8)
# =============================================================================

def test_theme_toggle_swaps_tokens_and_background():
    from composeio.render.widgets import ThemeToggleWidget

    class _BgCtx(_Ctx):
        def __init__(self):
            super().__init__()
            self.bg = None

        def set_background(self, color, grid=None, wash=None):
            self.bg = tuple(color)

    w = ThemeToggleWidget("tb")
    ctx = _BgCtx()
    w.on_mount(ctx)
    assert ct.CONSOLE.active is ct.DARK
    w.on_tap(0, 0)
    assert ct.CONSOLE.active is ct.LIGHT
    assert ctx.bg == ct.LIGHT.bg               # ground re-synced
    w.on_tap(0, 0)
    assert ct.CONSOLE.active is ct.DARK
    assert ctx.bg == ct.DARK.bg
    # draws in either table
    c = _canvas(100, 40)
    w.draw(c, Rect(4, 4, 78, 30))


def test_theme_toggle_parser_registration():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "theme_toggle",
                                           "id": "tb"}]})
    assert comp.root.children[0].widget_type == "theme_toggle"
    assert comp.root.children[0].interactive is True


# =============================================================================
# Engine plumbing + the real scene ([CXC-ICV-ACCEPT] ICV-C1/C2/C7/C8)
# =============================================================================

def test_design_reduced_motion_parsed():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"design": {"reduced_motion": True},
                              "widgets": []})
    assert comp.reduced_motion is True
    comp = parse_widget_tree({"widgets": []})
    assert comp.reduced_motion is False


def test_engine_context_exposes_theme_and_reduced_motion():
    from composeio.render.engine.runtime import EngineContext, RenderApp
    app = RenderApp.from_dict({"design": {"theme": "light",
                                          "reduced_motion": True},
                               "widgets": []}, windowed=False)
    ctx = EngineContext(app)
    assert ctx.theme == "light"
    assert ctx.reduced_motion is True


def test_console_scene_frozen_subscriptions_intact():
    # ICV-C2: the proven MVP wiring is UNTOUCHED — additions only.
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    subbed = [ns for ns, _ in comp.subscriptions]
    # [DEC-CONSOLE-CHAT-ID-PARAM] the chat leg derives from the scene's ONE
    # chat_id line; the host-pinned telemetry legs stay literal (they do NOT follow it).
    for frozen in ("thread/architect/narration", chat_ns("out"),
                   chat_ns("in"), "self/architect/context_pct",
                   "self/architect/subagents"):
        assert frozen in subbed, frozen
    # self/activity dropped 2026-07-21 with the Intelligence-card activity strip
    # (David): it duplicated the thread, so nothing consumes it anymore.
    # the fidelity pass's ADDED bindings (self/cost removed for KISS)
    # [CXC-IH-5A] id-scoped to self/architect/* + thread/architect/*
    # thread/architect/diagram dropped with the retired Canvas tab
    # ([DEC-CANVAS-TAB-RETIRED], 2026-07-24) — its only consumer is gone.
    for added in ("self/architect/registered", "self/architect/avatar/expression",
                  "self/architect/rate_limit"):
        assert added in subbed, added
    # cost cell removed: the scene no longer seeds/subscribes self/cost
    assert "self/cost" not in subbed


def test_console_scene_headless_render(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    out = str(tmp_path / "console-fidelity.png")
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.exists(out)
    assert os.path.getsize(out) > 20000        # a non-trivial console frame
