"""[SPEC:CXC-TERM] — the terminal widget.

Organised by the spec's acceptance criteria, because those are the definition of done.
TERM-C1 and TERM-C9's HUMAN halves cannot be met here and are not claimed: "David starts
a session in the widget and works in it" and "typing feels snappy while output streams"
are glass gates, and [CXC-TERM-ACCEPT] says in terms that a green machine run cannot
waive them.  What IS machine-checkable is asserted, and the recorded-byte-stream path
([CXC-TERM-SLICES] slice 2) is what makes the visual half deterministic without a live
`claude`.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.parser import parse_widget_tree            # noqa: E402
from composeio.render.engine.widget_registry import get_descriptor      # noqa: E402
from composeio.render.widget import Rect                                # noqa: E402
from composeio.render.widgets import terminal as term                   # noqa: E402
from composeio.render.widgets.terminal import (                         # noqa: E402
    MIN_COLS, MIN_ROWS, PASTE_END, PASTE_START, SHIFT_TAB, TARGET_MODE_AX,
    TerminalWidget, key_to_bytes,
)

_KMOD_SHIFT, _KMOD_CTRL, _KMOD_ALT = 0x0001, 0x0040, 0x0100


# ── harness ────────────────────────────────────────────────────────────────
class _Canvas:
    """Canvas-contract recorder — no skia.  `measure_text` returns a MEASURED cell so
    the grid metrics test can prove cols is derived, not hardcoded."""

    def __init__(self, cell_w: float = 7.0) -> None:
        self.calls: list = []
        self.cell_w = cell_w
        self.font = None

    # geometry / paint
    def save(self): self.calls.append(("save",))
    def restore(self): self.calls.append(("restore",))
    def set_fill_color(self, *c): self.calls.append(("fill_color", c))
    def set_stroke_color(self, *c): self.calls.append(("stroke_color", c))
    def set_stroke_width(self, w): self.calls.append(("stroke_width", w))
    def set_opacity(self, a): self.calls.append(("opacity", a))
    def fill_rect(self, *a): self.calls.append(("fill_rect", *a))
    def fill_round_rect(self, *a): self.calls.append(("fill_round_rect", *a))
    def stroke_round_rect(self, *a): self.calls.append(("stroke_round_rect", *a))
    def clip_rect(self, *a): self.calls.append(("clip_rect", *a))

    def set_font(self, family, size, weight="regular"):
        self.font = (family, size, weight)
        self.calls.append(("font", family, size, weight))

    def draw_text(self, text, x, y, align="left"):
        self.calls.append(("text", text, x, y, align))

    def measure_text(self, text):
        class M:
            width = self.cell_w * len(text)
            height = 12.0
            ascent = 10.0
            descent = 2.0
        return M()

    # helpers
    def texts(self) -> str:
        return "\n".join(c[1] for c in self.calls if c[0] == "text")


class _Ctx:
    """Engine-context stand-in that RECORDS damage, so damage scoping is assertable."""

    def __init__(self, headless: bool = False) -> None:
        self.bus = None
        self.headless = headless
        self.reduced_motion = False
        self.theme = "dark"
        self.rects: list = []
        self.full_redraws = 0
        self.updates: list = []
        self.published: list = []
        self.clocked: list = []

    def request_redraw(self, rect=None):
        if rect is None:
            self.full_redraws += 1
        else:
            self.rects.append(rect)

    def mark_dirty(self):                       # must never be called by this widget
        self.full_redraws += 1

    def schedule_update(self, fn, target=None):
        self.updates.append((fn, target))

    def run_updates(self):
        while self.updates:
            fn, _t = self.updates.pop(0)
            fn()

    def publish(self, ns, value, source=None):
        self.published.append((ns, value))

    def register_clocked(self, w): self.clocked.append(w)
    def unregister_clocked(self, w): self.clocked = [x for x in self.clocked if x is not w]



def _code_only(path: str) -> str:
    """`path` with every COMMENT and STRING literal removed.  A deletion guard must be able
    to forbid a NAME in code while the tree keeps explaining, at length, why it was deleted —
    and these deletions are ones a future edit will be tempted to undo."""
    import io
    import tokenize as _tok
    out = []
    with open(path, "rb") as fh:
        for tok in _tok.tokenize(io.BytesIO(fh.read()).readline):
            if tok.type in (_tok.COMMENT, _tok.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


def _widget(**kw) -> TerminalWidget:
    """A widget under test.  The identity defaults here are DELIBERATE and are the fix
    itself: a hosted child carries a `role` (which manifest orients it) plus a DISTINCT
    `instance_id` (the id it wears).  There is no `intelligence_id` scalar and no default id
    in the widget — a test that wants the host-collision path passes `instance_id` equal to
    the host's on purpose ([CXC-TERM-CHILD-ID])."""
    kw.setdefault("role", "architect")
    kw.setdefault("instance_id", "architect-hosted")
    w = TerminalWidget("term", **kw)
    w.arranged_rect = Rect(0, 0, 800, 500)
    return w


def _live(ctx=None, cols: int = 80, rows: int = 24, **kw) -> tuple:
    """A widget with a measured grid + an emulator, driven by RECORDED bytes."""
    ctx = ctx or _Ctx()
    w = _widget(**kw)
    w.on_mount(ctx)
    w.set_cell_metrics(7.0, 10.0)
    w.attach_emulator(cols, rows)
    return w, ctx


# ══ registry + placement ([CXC-TERM-WIDGET]) ═══════════════════════════════
def test_descriptor_is_registered_with_the_stated_placement():
    d = get_descriptor("terminal")
    assert d is not None and d.cls is TerminalWidget
    assert d.min_size == (560, 380)
    assert d.region_affinity == frozenset({"hero", "tab", "dashboard"})
    assert d.build is not None, "must use the [CXC-ICP-FOUND-B] build seam"


def test_it_structurally_cannot_be_a_rail_card():
    """The console's left rail is 332 px; the widget's floor is 560.  The descriptor's
    own fit test steers it away — no surface-specific special case."""
    d = get_descriptor("terminal")
    assert d.fits_region("rail", 332, None) is False
    assert d.fits_region("hero", 900, 600) is True


def test_no_build_leaf_branch_was_added():
    """[CXC-TERM-WIDGET]: 'a NEW widget = its module + one descriptor with a build hook,
    no build_leaf edit'.  Adding a branch is a review reject."""
    src = open(os.path.join(PY_ROOT, "composeio", "render", "engine", "parser.py")).read()
    head = src.index("def build_leaf")
    body = src[head:head + 40000]
    assert '"terminal"' not in body and "'terminal'" not in body


def test_scene_defaults():
    comp = parse_widget_tree({"widgets": [
        {"type": "terminal", "id": "t1", "role": "architect",
         "instance_id": "architect-hosted"}]})
    w = [x for x in comp.root.iter_widgets() if getattr(x, "widget_id", "") == "t1"][0]
    assert isinstance(w, TerminalWidget)
    assert w.autostart is False, "a widget that spawned by appearing would fork per capture"
    assert w.captures_tab is True and w.focus_ring is False
    assert w.target_mode == TARGET_MODE_AX
    # the subscription follows the CHILD's id, not the role ([CXC-TERM-CHILD-ID]: "one id,
    # one instance, everywhere — a mixed keying is symptom 2 in a new costume")
    assert ("intelligence/architect-hosted/out", w) in comp.subscriptions


def test_a_scene_with_NO_instance_id_gets_a_widget_that_refuses():
    """There is no default id any more.  The old default was `"architect"`, which is
    precisely how a hosted child came to wear its host's identity ([CXC-TERM-CHILD-ID])."""
    comp = parse_widget_tree({"widgets": [{"type": "terminal", "id": "t2"}]})
    w = [x for x in comp.root.iter_widgets() if getattr(x, "widget_id", "") == "t2"][0]
    assert w.instance_id == ""
    assert "no instance_id" in w.refusal()
    assert w.start_session() is False


# ══ TERM-C2's consequence: the target mode is the CENSUS's ═════════════════
@pytest.mark.requires_repo_mcp_config
def test_target_mode_is_the_ratified_ax_flag_and_writes_no_settings_file():
    """[CXC-TERM-AXMODE] / [DD-TERM-TARGET-MODE]: a per-invocation FLAG, so
    [CXC-TERM-PIN]'s settings composition disappears — nothing touches the user's
    ~/.claude/settings.json, and David's global `tui: fullscreen` is untouched."""
    w = _widget()
    argv = w.child_argv()
    assert "--ax-screen-reader" in argv
    assert "--settings" not in argv, "the ax path must compose NO settings file"
    assert w.child_env()["CLAUDE_AX_STARTUP_QUIET_MS"] == "0"
    assert w.child_env()["TERM"] == "xterm-256color"


# ══ TERM-C6 — an out-of-scope mode is refused HONESTLY ═════════════════════
def test_refuses_to_spawn_with_target_mode_unset():
    """The whole point: with TARGET_MODE unset the widget REFUSES rather than guessing.
    A guessed mode is a render of something that was never measured."""
    w = _widget(target_mode="")
    w.on_mount(_Ctx())
    w.set_cell_metrics(7.0, 10.0)
    why = w.refusal()
    assert "TARGET_MODE unset" in why
    assert w.start_session() is False
    assert w.child is None


def test_out_of_scope_mode_degrades_instead_of_rendering_badly(monkeypatch):
    w, ctx = _live(target_mode="tui-fullscreen",
                   claude_bin=sys.executable,
                   claude_args=["-c", "import time; time.sleep(0.5)"])
    assert w.start_session() is True
    try:
        assert w.degraded is True
        assert "tui-fullscreen" in w._degrade_reason
        c = _Canvas()
        w.draw(c, w.arranged_rect)
        assert "DEGRADED" in c.texts()
    finally:
        w.stop_session()


def test_alt_screen_raises_the_sticky_banner_and_publishes():
    """[CXC-TERM-SCOPE] scenario: `?1049h` at ANY point means the line model is wrong."""
    w, ctx = _live()
    w.feed(b"hello\r\n\x1b[?1049h")
    assert w.degraded is True
    assert "alt_screen_1049" in w._degrade_reason
    ns, payload = ctx.published[-1]
    assert ns == "intelligence/architect-hosted/terminal/degraded"
    assert payload["degraded"] is True and payload["counts"]["alt_screen_1049"] == 1
    # sticky: a clean frame afterwards does not clear it (a flickering failure is a lie)
    w.feed(b"more output\r\n")
    assert w.degraded is True


def test_alt_screen_is_never_silently_consumed():
    w, _ = _live()
    w.feed(b"\x1b[?1049h")
    assert w.unsupported_counts.get("alt_screen_1049") == 1


@pytest.mark.parametrize("seq,name", [
    (b"\x1b[?1049h", "alt_screen_1049"),
    (b"\x1b[2;35r", "scroll_region"),
    (b"\x1b[2J", "clear_screen"),
])
def test_each_model_breaking_sequence_banners(seq, name):
    w, ctx = _live()
    w.feed(b"prefix" + seq)
    assert w.degraded is True, name
    assert name in w._degrade_reason
    assert ctx.published[-1][1]["counts"][name] == 1


# ══ [CXC-TERM-SEQTABLE] — the pre-scan, upstream of the emulator ═══════════
def test_split_sequence_counts_exactly_once_at_every_offset():
    """A sequence SPLIT across two PTY reads must be matched once — not missed, not
    double-counted.  The tail carry is the mechanism."""
    seq = b"\x1b[?1049h"
    for cut in range(1, len(seq)):
        w, _ = _live()
        w.feed(b"abc" + seq[:cut])
        w.feed(seq[cut:] + b"def")
        assert w.unsupported_counts.get("alt_screen_1049") == 1, f"cut at {cut}"


def test_decdsr_does_not_crash_the_byte_path():
    """MEASURED pyte hazard 1: `\\x1b[?6n` RAISES inside pyte 0.8.2, and Claude Code
    emits it at startup in EVERY profile.  The pre-scan withholds it, so the widget
    survives its own child's first burst."""
    w, _ = _live()
    w.feed(b"before\x1b[?6nafter\r\n")
    assert w.unsupported_counts.get("dsr_query") == 1
    assert "before" in w._screen.display[0] and "after" in w._screen.display[0]


def test_misapplied_private_sequence_is_withheld_from_the_emulator():
    """MEASURED pyte hazard 2: `CSI < u` dispatches to restore_cursor and MOVES THE
    CURSOR.  Withheld -> the cursor stays where the child put it."""
    w, _ = _live()
    w.feed(b"\x1b7")                     # DECSC: save cursor at 0,0
    w.feed(b"line one\r\nline two")
    y_before, x_before = w._screen.cursor.y, w._screen.cursor.x
    w.feed(b"\x1b[<u")
    assert (w._screen.cursor.y, w._screen.cursor.x) == (y_before, x_before)


def test_osc_133_is_withheld_because_pyte_reads_it_as_osc_1():
    w, _ = _live()
    w.feed(b"\x1b]133;A;redraw=0\x07visible")
    assert "visible" in w._screen.display[0]
    assert "133" not in w._screen.display[0]


def test_a_supported_only_stream_leaves_the_counter_clean():
    w, _ = _live()
    w.feed(b"\x1b[2mdim\x1b[0m normal\r\n\x1b[1A\x1b[K\x1b[6Gcolumn\r\n")
    assert w.unsupported_counts == {}
    assert w.degraded is False
    assert "unsupported" not in w.status_line()


def test_mouse_tracking_is_a_named_gap_not_a_banner():
    """[CXC-TERM-DEGRADE]: V1 never sends mouse reports.  The human is TOLD rather than
    left wondering why clicking does nothing; keyboard is unaffected."""
    w, _ = _live()
    w.feed(b"\x1b[?1000h\x1b[?1006h")
    assert w.degraded is False
    assert "mouse tracking not forwarded" in w.status_line()


def test_bracketed_paste_mode_is_tracked_from_the_byte_path():
    w, _ = _live()
    assert w._bracketed_paste is False
    w.feed(b"\x1b[?2004h")
    assert w._bracketed_paste is True
    w.feed(b"\x1b[?2004l")
    assert w._bracketed_paste is False


# ══ [CXC-TERM-EMULATOR] — encoding ═════════════════════════════════════════
def test_multibyte_utf8_split_across_reads_is_one_glyph():
    w, _ = _live()
    raw = "日".encode("utf-8")
    w.feed(raw[:1])
    w.feed(raw[1:])
    assert w._screen.display[0][0] == "日"


def test_malformed_utf8_replaces_rather_than_crashing():
    w, _ = _live()
    w.feed(b"ok\xff\xfemore\r\n")
    assert "ok" in w._screen.display[0]


# ══ [CXC-TERM-GRID] — the renderer ═════════════════════════════════════════
def test_cols_is_derived_from_measure_text_not_a_constant():
    """Hardcoding a cell width makes the grid drift the moment the bundled font
    changes.  Two different measured cells MUST give two different column counts."""
    w = _widget()
    w.on_mount(_Ctx())
    narrow, wide = _Canvas(cell_w=7.0), _Canvas(cell_w=14.0)
    w.draw(narrow, w.arranged_rect)
    cols_narrow = w.grid_size()[0]
    w._metrics_key = None                # force a re-measure (a font/theme change)
    w.draw(wide, w.arranged_rect)
    cols_wide = w.grid_size()[0]
    assert cols_narrow == pytest.approx(cols_wide * 2, abs=1)


def test_too_small_draws_the_plate_and_spawns_nothing():
    w = _widget()
    w.arranged_rect = Rect(0, 0, 300, 500)      # [CXC-TERM-GRID] scenario
    w.on_mount(_Ctx())
    c = _Canvas(cell_w=8.0)                     # draw() MEASURES; that is the point
    w.draw(c, w.arranged_rect)
    w.set_cell_metrics(8.0, 10.0)
    assert "TERMINAL TOO SMALL" in c.texts()
    assert f"needs {MIN_COLS}x{MIN_ROWS}" in c.texts()
    assert w.start_session() is False and w.child is None


def test_idle_plate_before_any_session():
    w = _widget()
    w.on_mount(_Ctx())
    c = _Canvas()
    w.draw(c, w.arranged_rect)
    assert "no session" in c.texts()
    assert "press Return to start" in c.texts(), "the start affordance must be visible"


def test_return_on_an_idle_widget_starts_a_session():
    w, _ctx = _live(claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(0.4)"])
    assert w.child is None
    w.on_key("Return", 0)
    try:
        assert w.child is not None
    finally:
        w.stop_session()


def test_the_example_scene_parses_and_declares_the_ratified_target():
    """David's [DD-TERM-HOST-SURFACE] ruling: standalone window first (the Graph Viewer
    pattern), then a Terminal tab in the console.  This is the standalone scene."""
    import yaml
    path = os.path.join(PY_ROOT, "composeio", "render", "examples",
                        "terminal_session.compose.yaml")
    spec = yaml.safe_load(open(path))
    comp = parse_widget_tree(spec)
    w = [x for x in comp.root.iter_widgets() if getattr(x, "widget_id", "") == "session"][0]
    assert isinstance(w, TerminalWidget)
    assert w.target_mode == TARGET_MODE_AX
    assert w.autostart is False


def test_grid_text_and_status_strip_render():
    w, _ = _live()
    w.feed(b"hello terminal\r\n")
    c = _Canvas()
    w.draw(c, w.arranged_rect)
    body = c.texts()
    assert "hello terminal" in body
    assert "80x24" in body or f"{w.grid_size()[0]}x{w.grid_size()[1]}" in body
    assert "release Ctrl-Shift-Esc" in body


def test_cursor_comes_from_the_emulator_so_25l_is_honoured():
    w, _ = _live()
    w.feed(b"x")
    assert w._screen.cursor.hidden is False
    w.feed(b"\x1b[?25l")
    assert w._screen.cursor.hidden is True   # honoured for free — no second cursor model


def test_scrollback_sticks_to_the_bottom_until_the_human_scrolls():
    w, _ = _live(rows=5)
    for i in range(30):
        w.feed(("line%02d\r\n" % i).encode())
    assert w.sticking_to_bottom is True
    assert "line29" in "\n".join(w._screen.display)
    w.scroll_lines(10)
    assert w.sticking_to_bottom is False
    top = "".join(w.visible_rows()[0][x].data for x in range(7))
    assert top.startswith("line"), top
    assert top != "line25"                    # we are looking at OLDER output
    w.scroll_lines(-10)
    assert w.sticking_to_bottom is True


def test_typing_resumes_bottom_stick():
    w, _ = _live(rows=5)
    for i in range(30):
        w.feed(("line%02d\r\n" % i).encode())
    w.scroll_lines(10)
    w.on_text("a")                       # no child: writes nothing, but re-follows
    assert w.sticking_to_bottom is True


# ══ [CXC-TERM-DAMAGE] — dirty rows, never the scene ════════════════════════
def test_one_line_update_damages_one_band_not_the_widget():
    w, ctx = _live()
    w.feed(b"seed\r\n")
    ctx.rects.clear()
    ctx.full_redraws = 0
    w.feed(b"one more line\r\n")
    assert ctx.full_redraws == 0, "a terminal must NEVER escalate to a full redraw"
    assert ctx.rects, "a content change must damage something"
    assert all(r.h <= 2 * w.cell_h for r in ctx.rects), [r.h for r in ctx.rects]
    assert len(ctx.rects) <= 2


def test_a_wide_change_escalates_to_the_widget_rect_only():
    w, ctx = _live(rows=10)
    ctx.rects.clear()
    ctx.full_redraws = 0
    w.feed(b"\r\n".join(b"row %d" % i for i in range(10)))
    assert ctx.full_redraws == 0
    assert len(ctx.rects) == 1 and ctx.rects[0].h == w.arranged_rect.h


def test_a_burst_is_consumed_across_frames_inside_a_TIME_budget():
    """[CXC-TERM-DAMAGE]: 1 MiB in one burst is consumed over multiple frames, and each
    frame's drain is bounded by DRAIN_BUDGET_S — an uncapped drain turns a burst into a
    visible freeze, and so did the old flat 256 KiB byte cap (a byte's cost is not a
    constant: pyte measured 437-662 B/ms, so 256 KiB was a ~390 ms frame)."""
    w, ctx = _live()
    burst = b"x" * (term.MAX_BYTES_PER_TICK * 2 + 1000)
    w._on_child_bytes(burst)             # as if from the reader thread
    assert len(ctx.updates) == 1, "the reader thread must COALESCE, not queue per chunk"

    t0 = time.monotonic()
    first = ctx.updates[0][0]()
    elapsed = time.monotonic() - t0

    assert 0 < first < len(burst), "the whole burst was swallowed in one tick"
    assert first % term.DRAIN_SLICE_BYTES == 0, "the budget is checked per slice"
    assert first <= term.MAX_BYTES_PER_TICK, "the absolute ceiling still holds"
    # The budget bounds the STALL.  Generous slack (a slice may straddle the deadline,
    # and a loaded CI box is slow) — the point is that it is bounded at all.
    assert elapsed < term.DRAIN_BUDGET_S + 0.25, f"drain stalled {elapsed*1000:.0f} ms"
    # ...and the rest is scheduled, not dropped.
    assert len(ctx.updates) == 2
    assert ctx.full_redraws == 0


def test_the_drain_ceiling_still_bounds_a_tick_when_the_clock_does_not_advance(monkeypatch):
    """The time budget must not become an UNBOUNDED drain if the clock misbehaves —
    MAX_BYTES_PER_TICK survives as the absolute ceiling."""
    w, ctx = _live()
    monkeypatch.setattr(term.time, "monotonic", lambda: 0.0)   # never reaches the deadline
    w._on_child_bytes(b"x" * (term.MAX_BYTES_PER_TICK + 4096))
    assert ctx.updates[0][0]() == term.MAX_BYTES_PER_TICK


def test_reader_thread_never_touches_the_emulator_directly():
    """The handoff is `schedule_update(fn, target=self)` — whose `target` argument
    exists precisely to scope the resulting damage to one widget."""
    w, ctx = _live()
    w._on_child_bytes(b"deferred\r\n")
    assert w._screen.display[0].strip() == "", "fed on the reader thread!"
    assert ctx.updates[0][1] is w, "damage must be scoped to this widget"
    ctx.run_updates()
    assert "deferred" in w._screen.display[0]


# ══ [CXC-TERM-INPUT] — the keymap ══════════════════════════════════════════
@pytest.mark.parametrize("key,mod,expect", [
    ("Return", 0, b"\r"),                 # NOT \n — the TUI submits on \r
    ("Escape", 0, b"\x1b"),
    ("Backspace", 0, b"\x7f"),
    ("Tab", 0, b"\t"),
    ("Tab", _KMOD_SHIFT, SHIFT_TAB),
    ("Up", 0, b"\x1b[A"),
    ("Down", 0, b"\x1b[B"),
    ("Right", 0, b"\x1b[C"),
    ("Left", 0, b"\x1b[D"),
    ("Home", 0, b"\x1b[H"),
    ("End", 0, b"\x1b[F"),
    ("PageUp", 0, b"\x1b[5~"),
    ("PageDown", 0, b"\x1b[6~"),
    ("Delete", 0, b"\x1b[3~"),
    ("C", _KMOD_CTRL, b"\x03"),
    ("D", _KMOD_CTRL, b"\x04"),
    ("L", _KMOD_CTRL, b"\x0c"),
    ("a", _KMOD_ALT, b"\x1ba"),
])
def test_keymap_table(key, mod, expect):
    assert key_to_bytes(key, mod) == expect


def test_printable_keys_are_never_sent_from_on_key():
    """The double-delivery rule: SDL fires KEYDOWN *and* TEXTINPUT for a printable
    character.  A widget that sends from both channels types everything twice."""
    for key in ("a", "Z", "7", "/", " "):
        assert key_to_bytes(key, 0) is None


class _FakeChild:
    def __init__(self):
        self.writes: list = []
        self.injection_log: list = []
        self.pid = 4242

    def write_keys(self, data):
        self.writes.append(data)

    def inject_line(self, line, kind="inject"):
        from composeio.ptycore import COMPACT_CLEAR, COMPACT_SUBMIT
        for phase, keys in (("-clear", COMPACT_CLEAR), ("", line), ("-submit", COMPACT_SUBMIT)):
            self.injection_log.append({"kind": kind + phase, "keys": keys})
            self.writes.append(keys.encode())

    # NO `inject_prompt` — `PtyChild` has none ([CXC-TERM-BOOTSTRAP-ARGV]).  A fake with a
    # WIDER surface than the thing it stands in for is exactly how a deleted path keeps
    # passing its tests.

    def set_term_size(self, r, c): pass
    def apply_winsize(self, r, c): pass
    def shutdown(self): pass
    def child_exited(self): return False
    def exit_description(self): return ""


def _with_fake_child(**kw):
    w, ctx = _live(**kw)
    w._child = _FakeChild()
    return w, ctx, w._child


def test_ctrl_c_reaches_the_child_while_output_streams():
    w, _ctx, child = _with_fake_child()
    w.feed(b"streaming output...")
    w.on_key("C", _KMOD_CTRL)
    assert child.writes == [b"\x03"]


def test_tab_is_sent_when_the_widget_has_focus():
    w, _ctx, child = _with_fake_child()
    w.on_key("Tab", 0)
    assert child.writes == [b"\t"]


def test_tab_reaches_a_real_child_through_the_focus_manager():
    """TERM-C10 END TO END, with no fakes anywhere in the path: the engine's
    FocusManager routes Tab to the focused widget (because `captures_tab` is true), the
    widget maps it to \\t, and a REAL child process on a REAL pty reads that byte — while
    focus stays put.  The unit tests either side of this one each prove half of it; this
    is the join, and the join is where a widget usually turns out not to receive the key
    at all."""
    from composeio.render.engine.focus import FocusManager
    from composeio.render.widget import Widget as _W
    # The child reads a LINE, not a byte: a pty starts in CANONICAL mode, so the tty
    # holds input until a newline no matter what the widget wrote.  Tab then Return is
    # what a human does anyway, and the line the child receives still proves the \t
    # traversed the whole path.
    src = ("import sys\n"
           "d = sys.stdin.buffer.readline()\n"
           "sys.stdout.write('SAW:%r\\n' % d); sys.stdout.flush()\n")
    w, ctx = _live(claude_bin=sys.executable, claude_args=["-c", src])
    assert w.start_session() is True
    try:
        other = _W("other", "rectangle")
        other.accepts_focus = True
        fm = FocusManager()
        fm._focusables = [w, other]
        fm.focus(w)
        assert fm.handle_key("Tab", 0) is True
        assert fm.focused is w, "focus MUST NOT move out of the terminal"
        fm.handle_key("Return", 0)          # flush the canonical-mode line buffer
        # Read the child's answer THROUGH the widget's own path — its reader thread owns
        # the fd, and a second reader here would race it for the same bytes.
        screen = ""
        for _ in range(150):
            ctx.run_updates()
            screen = "\n".join(w._screen.display)
            if "SAW:" in screen:
                break
            time.sleep(0.02)
        assert "SAW:" in screen and "\\t" in screen, screen
    finally:
        w.stop_session()


def test_release_chord_writes_zero_bytes_and_blurs():
    """The escape hatch is MANDATORY: captures_tab + Escape forwarded would otherwise
    let a human become unable to leave the widget by keyboard."""
    w, _ctx, child = _with_fake_child()
    w.focused = True
    w.on_key("Escape", _KMOD_CTRL | _KMOD_SHIFT)
    assert child.writes == [], "the release chord must not reach the child"
    assert w.focused is False
    assert "release Ctrl-Shift-Esc" in w.status_line()


def test_plain_escape_still_reaches_the_child():
    w, _ctx, child = _with_fake_child()
    w.on_key("Escape", 0)
    assert child.writes == [b"\x1b"]


def test_text_is_sent_verbatim():
    w, _ctx, child = _with_fake_child()
    w.on_text("hello")
    assert child.writes == [b"hello"]


# ══ [CXC-TERM-PASTE] ═══════════════════════════════════════════════════════
def test_multiline_paste_is_wrapped_when_the_child_enabled_bracketed_paste():
    """A multi-line paste delivered RAW submits on every embedded newline — the
    documented historical failure of the re-register bootstrap."""
    w, _ctx, child = _with_fake_child()
    w.feed(b"\x1b[?2004h")
    w.on_text("one\ntwo\nthree")
    assert child.writes == [PASTE_START + b"one\ntwo\nthree" + PASTE_END]


def test_paste_is_verbatim_when_the_child_did_not_enable_it():
    """The same behaviour a real terminal has, and therefore not a regression."""
    w, _ctx, child = _with_fake_child()
    w.on_text("one\ntwo")
    assert child.writes == [b"one\ntwo"]


def test_a_single_character_is_never_bracketed():
    w, _ctx, child = _with_fake_child()
    w.feed(b"\x1b[?2004h")
    w.on_text("x")
    assert child.writes == [b"x"]


# ══ TERM-C3 / TERM-C8 — the relay and the convergence ══════════════════════
def test_bus_actuation_types_the_three_phase_line():
    w, _ctx, child = _with_fake_child()
    w.on_out_message(json.dumps({"cmd": "compact", "direction": "hold the seam"}))
    assert [r["kind"] for r in child.injection_log] == [
        "compact-clear", "compact", "compact-submit"]
    assert child.injection_log[1]["keys"] == "/compact hold the seam"


def test_nested_reply_envelope_is_understood():
    w, _ctx, child = _with_fake_child()
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect",
                    "text": json.dumps({"cmd": "inject", "text": "hi"})})
    assert child.injection_log[1]["keys"] == "hi"


def test_an_ordinary_reply_injects_nothing():
    w, _ctx, child = _with_fake_child()
    w.on_out_message({"from": "architect", "text": "just a reply"})
    assert child.writes == []


def test_the_widget_and_the_launcher_write_identical_bytes(tmp_path):
    """TERM-C8, satisfied BY CONSTRUCTION: both hosts drive the SAME core method, so the
    child cannot distinguish a bus keystroke from a keyboard keystroke."""
    from composeio.ptycore import PtyChild
    src = ("import sys\n"
           "line = sys.stdin.readline()\n"
           "sys.stdout.write('GOT:' + line.strip() + '\\n')\n"
           "sys.stdout.flush()\n")

    def _capture(fn) -> bytes:
        child = PtyChild([sys.executable, "-c", src], term_size=(24, 80),
                         log=lambda _l: None)
        child.start()
        child.compact_phase_delay = 0
        seen: list = []
        try:
            fn(child)
            for _ in range(80):
                if not child.pump(seen.append, timeout=0.02):
                    break
                if b"GOT:" in b"".join(seen):
                    break
        finally:
            child.shutdown()
        return b"".join(seen)

    typed = _capture(lambda c: c.write_keys(b"hello\r"))
    bussed = _capture(lambda c: c.inject("hello\r", kind="inject"))
    assert b"GOT:hello" in typed, typed
    assert typed == bussed, (typed, bussed)


def test_keyboard_input_never_lands_in_the_injection_log():
    w, _ctx, child = _with_fake_child()
    w.on_text("secret typing")
    w.on_key("Return", 0)
    assert child.injection_log == []


# ══ TERM-C5 / [CXC-TERM-DIALOGS] — zero bytes before the human ═════════════
def test_no_bytes_are_written_between_spawn_and_the_first_keypress(monkeypatch):
    from composeio import ptycore
    writes: list = []
    real = ptycore.PtyChild._write
    monkeypatch.setattr(ptycore.PtyChild, "_write",
                        lambda self, data: (writes.append(data), real(self, data))[1])
    w, _ctx = _live(claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(0.6)"])
    assert w.start_session() is True
    try:
        time.sleep(0.2)
        assert writes == [], f"the widget wrote {writes!r} before the human typed"
        w.on_text("x")
        assert writes == [b"x"]
    finally:
        w.stop_session()


def test_no_dialog_detector_exists():
    """[CXC-TERM-DIALOGS] grep guard: keystroke injection into startup dialogs collided
    with the human and HUNG.  It is out of scope permanently, not deferred.

    Re-asserted after the bootstrap landed, because that is exactly when the two get
    conflated: a bootstrap is a prompt typed after a fixed SETTLE DELAY, never a reply to
    something the widget recognised in the child's output."""
    src = open(os.path.join(PY_ROOT, "composeio", "render", "widgets", "terminal.py")).read()
    body = src.split('"""', 2)[2]        # skip the module docstring, which DISCUSSES it
    for banned in ("Do you trust", "trust this folder", "startswith('Yes", "dialog_pattern"):
        assert banned not in body


# ══ [CXC-ICL-COLD] + [CXC-TERM-BOOTSTRAP-ARGV] — an ORIENTED spawn, WITHOUT a keystroke ══
# What used to live here armed a `threading.Timer`, waited out `BOOTSTRAP_DELAY`, and asserted
# the three-phase clear/type/submit landed on the master fd.  The whole seam is DELETED.  On
# its first live firing it typed into a blocking `Enter y/n:` gate and `^[[200~` echoed as
# literal text; a fixed timer standing in for a state check is unsound for every value, so
# there was nothing to tune ([CXC-TERM-BOOTSTRAP-SETTLE-RETIRED]).  These are its replacements
# and they assert the STRONGER property: not "nothing before the delay" but NOTHING, EVER.

def _bootstrapping_widget(monkeypatch, tmp_path, env_bootstrap=None, **kw):
    """A widget whose bootstrap resolves off a REAL manifest."""
    manifest = tmp_path / "arch.meta.json"
    manifest.write_text(json.dumps({
        "id": "architect",
        "role_descriptor": {"path": "intelligence/roles/architect.md"},
        "dusk_gifts": [{"path": "bootstrap/dusk-gifts/registration-protocol.md"}],
    }))
    if env_bootstrap is None:
        monkeypatch.delenv("BOOTSTRAP", raising=False)   # unset MUST mean ON
    else:
        monkeypatch.setenv("BOOTSTRAP", env_bootstrap)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "prompts"))
    w, ctx, child = _with_fake_child(meta_manifest=str(manifest), **kw)
    return w, ctx, child


def test_START_SESSION_bootstraps_a_cold_spawn_with_ZERO_BYTES_written(monkeypatch, tmp_path):
    """TERM-C13, the machine half.  A REAL `pty.fork()`, a real master fd, and the assertion
    that NOT ONE BYTE crosses it for the whole session — unconditionally, with no timer
    carve-out — while the child's argv carries the orientation prompt.

    It drives the real `start_session()` for the same reason the old test did: the original
    defect was that nothing CALLED the bootstrap, and a test that invoked the delivery helper
    itself would have passed against the broken build."""
    from composeio import ptycore
    manifest = tmp_path / "arch.meta.json"
    manifest.write_text(json.dumps({
        "id": "architect",
        "role_descriptor": {"path": "intelligence/roles/architect.md"},
        "dusk_gifts": [{"path": "bootstrap/dusk-gifts/registration-protocol.md"}],
    }))
    monkeypatch.delenv("BOOTSTRAP", raising=False)          # unset MUST mean ON
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "prompts"))
    writes: list = []
    real = ptycore.PtyChild._write
    monkeypatch.setattr(ptycore.PtyChild, "_write",
                        lambda self, data: (writes.append(data), real(self, data))[1])

    w, _ctx = _live(claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(1.5)"],
                    meta_manifest=str(manifest))
    w._child = None
    assert w.start_session() is True
    try:
        # the prompt is on ARGV: a short instruction naming the prompt FILE by absolute path
        argv_prompt = w.child.argv[-1]
        assert argv_prompt.startswith("Read /"), argv_prompt
        prompt_file = argv_prompt.split()[1]
        assert os.path.isfile(prompt_file)
        body = open(prompt_file, encoding="utf-8").read()
        assert body.splitlines()[0] == (
            f"Read {manifest} and complete the bootstrap it describes.")
        assert "READ intelligence/roles/architect.md" in body
        # ...and NOTHING is typed.  Not before a delay — at all.
        time.sleep(0.6)                       # well past the retired 3.0s mechanism's 0.3
        assert writes == [], f"the widget wrote {writes!r} to a starting child"
        assert w.child.injection_log == []
    finally:
        w.stop_session()


@pytest.mark.requires_repo_mcp_config
def test_the_bootstrap_file_is_written_and_argv_stays_SHORT(monkeypatch, tmp_path):
    """The ratified form: the generator still runs (so [CXC-TERM-BOOTSTRAP-DERIVED] is
    intact), its 6 KB output goes to a FILE, and argv carries a pointer.  Inlining the body
    is the form David rejected; a bare "complete bootstrap" assumes context a cold instance
    does not have ([DD-TERM-BOOTSTRAP-ARGV-FORM])."""
    w, _ctx, child = _bootstrapping_widget(monkeypatch, tmp_path)
    instruction = w._bootstrap_argv()
    assert len(instruction) < 400 and "RE-REGISTERING" not in instruction
    body = open(instruction.split()[1], encoding="utf-8").read()
    assert len(body) > 1000 and "RE-REGISTERING" in body
    assert w.child_argv(instruction)[-1] == instruction
    assert child.writes == []


@pytest.mark.requires_repo_mcp_config
@pytest.mark.parametrize("label,kw", [
    ("BOOTSTRAP=0 — the explicit opt-OUT, in BOTH hosts ([CXC-ICL-COLD] (c))",
     {"env_bootstrap": "0"}),
    ("a scene property pins it off — `bootstrap=None` defers to the env, whose default is ON, "
     "so a scene wanting a blank instance must say so OUT LOUD",
     {"bootstrap": False}),
    ("RESUME — a re-host already HOLDS its context; no seam, nothing to re-register from",
     {"resume_session": "sess-9"}),
])
def test_the_bootstrap_is_not_delivered_at_all(monkeypatch, tmp_path, label, kw):
    w, _ctx, child = _bootstrapping_widget(monkeypatch, tmp_path, **kw)
    try:
        assert w._bootstrap_argv() == "", label
        assert w.child_argv("")[-1] != "", label      # nothing appended
        assert child.writes == [], label
    finally:
        w.stop_session()


def test_BOOTSTRAP_0_is_LABELLED_unoriented_on_the_glass(monkeypatch, tmp_path):
    """[CXC-TERM-MANIFEST-CHAIN] scenario 3: a deliberately unoriented child is a CHOICE, and
    a choice must still be VISIBLE — otherwise it is indistinguishable from the failure."""
    monkeypatch.setenv("BOOTSTRAP", "0")
    w = _widget()
    w.set_cell_metrics(7.0, 10.0)
    assert w.bootstrap_on() is False
    assert "unoriented (BOOTSTRAP=0)" in w._idle_plate()


def test_there_is_NO_startup_injection_path_left_to_call():
    """TERM-C13's "assert the injection path is GONE, not merely unused".  A dormant
    `_arm_bootstrap` is one edit away from typing into a dialog again, so the ATTRIBUTES must
    not exist — and neither may a timer anywhere in the widget."""
    import inspect

    from composeio.render.widgets.terminal import TerminalWidget
    for gone in ("_arm_bootstrap", "_type_bootstrap", "_bootstrap_timer", "bootstrap_delay"):
        assert not hasattr(TerminalWidget, gone), f"{gone} survived"
    w = _widget()
    for gone in ("_arm_bootstrap", "_type_bootstrap", "_bootstrap_timer", "bootstrap_delay"):
        assert not hasattr(w, gone), f"{gone} survived on the instance"
    assert "threading.Timer" not in _code_only(inspect.getsourcefile(TerminalWidget))
    # the byte path still cannot reach a bootstrap at all — the DETECTOR guard, kept
    for fn in (TerminalWidget.feed, TerminalWidget.drain_once, TerminalWidget._on_child_bytes):
        assert "bootstrap" not in inspect.getsource(fn)


@pytest.mark.requires_repo_mcp_config
def test_the_widget_resolves_the_binary_through_EXECPATH(monkeypatch):
    """The live spawn failure: `claude` is a shell ALIAS on this machine and NOT on PATH, so
    `execvp` exhausted PATH and died naming a stale plugin dir.  `CLAUDE_CODE_EXECPATH` is
    set by the harness and names THIS claude — more correct than a PATH search."""
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/opt/claude/bin/claude.exe")
    assert _widget().claude_bin == "/opt/claude/bin/claude.exe"
    assert _widget().child_argv()[0] == "/opt/claude/bin/claude.exe"
    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    assert _widget().claude_bin == "/usr/local/bin/claude"
    assert _widget(claude_bin="/explicit/claude").claude_bin == "/explicit/claude"


def test_the_child_env_carries_a_RESUME_pin_but_never_an_inherited_one():
    """[CXC-IC-SELF-IDENTITY]: a pin naming the child's OWN transcript is PASSED; the host's
    is stripped by the core.  `child_env` is the passing seam, so it must only add the pin in
    the mode where the pin is the child's."""
    assert "COMPOSEIO_SESSION_JSONL" not in _widget().child_env("/some/host.jsonl")
    resumed = _widget(resume_session="sess-9").child_env("/child/own.jsonl")
    assert resumed["COMPOSEIO_SESSION_JSONL"] == "/child/own.jsonl"


# ══ [CXC-TERM-WINSIZE] ═════════════════════════════════════════════════════
def test_resize_updates_the_emulator_and_is_debounced():
    w, _ = _live(cols=80, rows=24)
    t0 = 1000.0
    assert w.apply_grid(100, 30, now=t0) is True
    assert (w._screen.columns, w._screen.lines) == (100, 30)
    # inside the debounce window: coalesced, not issued
    assert w.apply_grid(101, 30, now=t0 + 0.05) is False
    assert (w._screen.columns, w._screen.lines) == (100, 30)
    # ...and issued once motion stops
    assert w.flush_pending_resize(now=t0 + 0.5) is True
    assert (w._screen.columns, w._screen.lines) == (101, 30)


def test_resize_below_the_minimum_clamps_and_never_sends_zero():
    """[CXC-TERM-BOUNDARIES] row 10: the child is NOT killed; TIOCSWINSZ is clamped."""
    w, _ = _live()
    w.apply_grid(5, 2, now=1000.0)
    assert (w._cols, w._rows) == (MIN_COLS, MIN_ROWS)


# ══ lifecycle ══════════════════════════════════════════════════════════════
def test_headless_render_never_spawns():
    """[CXC-TERM-WIDGET] hard gate: forking a `claude` per reference image is how a
    capture suite becomes a fork bomb."""
    ctx = _Ctx(headless=True)
    w = _widget(autostart=True)
    w.on_mount(ctx)
    w.set_cell_metrics(7.0, 10.0)
    assert w.child is None
    assert w.start_session() is False


def test_unmount_tears_the_child_down():
    """A tab switch that leaves an orphaned `claude` holding the singleton is a defect
    that only shows up an hour later."""
    w, _ctx = _live(claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(30)"])
    assert w.start_session() is True
    pid = w.child.pid
    w.on_unmount()
    assert w.child is None
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except OSError:
            break
        time.sleep(0.02)
    else:                                       # pragma: no cover
        os.kill(pid, 9)
        pytest.fail("child survived on_unmount")


def test_exit_is_frozen_not_cleared_and_relaunchable():
    """[CXC-TERM-BOUNDARIES] row 3: the grid keeps its final content and the strip reads
    `exited <code>`; rows 2-6 and 12 all leave the widget RE-LAUNCHABLE."""
    w, ctx = _live(claude_bin=sys.executable,
                   claude_args=["-c", "print('LASTLINE'); raise SystemExit(7)"])
    assert w.start_session() is True
    for _ in range(200):
        ctx.run_updates()
        if "exited" in w.state:
            break
        time.sleep(0.02)
    ctx.run_updates()
    assert w.state == "exited 7", w.state
    assert "LASTLINE" in "\n".join(w._screen.display)
    assert w.refusal() == "", "an exited session must leave the widget re-launchable"


def test_session_pin_is_exported_before_fork(tmp_path, monkeypatch):
    """TERM-C7: the console data layer must instrument THIS transcript, by a passed
    value, never an `ls -t` guess."""
    monkeypatch.delenv("COMPOSEIO_SESSION_JSONL", raising=False)
    monkeypatch.delenv("SESSION_JSONL", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sess-xyz.jsonl").write_text("{}\n")
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(proj))
    w, _ctx = _live(resume_session="sess-xyz", claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(0.3)"])
    assert w.start_session() is True
    try:
        assert os.environ["COMPOSEIO_SESSION_JSONL"] == str(proj / "sess-xyz.jsonl")
    finally:
        w.stop_session()


def test_singleton_is_the_same_lock_the_launcher_takes(monkeypatch):
    """[CXC-TERM-SINGLETON]: the console IS a launcher now.  Same helper, same lock
    name, no console-specific variant."""
    taken: list = []
    sys.path.insert(0, os.path.join(REPO, "compose_projects", "intelligence", "publisher"))
    import singleton as sg
    monkeypatch.setattr(sg, "acquire_singleton_superseding",
                        lambda name, *a, **k: taken.append(name) or None)
    w, _ctx = _live(claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(0.3)"])
    assert w.start_session() is True
    try:
        assert taken == ["intelligence-launcher-architect-hosted"], (
            "the singleton lock must key on the CHILD's id, not the role or the host's")
    finally:
        w.stop_session()


def test_supersession_stops_relaying_and_says_so():
    w, _ctx = _live(claude_bin=sys.executable,
                    claude_args=["-c", "import time; time.sleep(5)"])
    assert w.start_session() is True
    w.note_superseded()
    assert w.degraded is True
    assert "superseded by another launcher for architect" in w._degrade_reason
    assert w.child is None, "a half-dead child plus a second relay is double-injection"


def test_emulator_unavailable_refuses_and_the_console_keeps_running(monkeypatch):
    """[CXC-TERM-BOUNDARIES] row 12."""
    monkeypatch.setattr(term, "pyte", None)
    w = _widget()
    w.on_mount(_Ctx())
    w.set_cell_metrics(7.0, 10.0)
    assert "terminal emulator unavailable" in w.refusal()
    assert w.start_session() is False
    c = _Canvas()
    w.draw(c, w.arranged_rect)             # still renders; the console keeps running
    assert "emulator unavailable" in c.texts()


def test_bus_down_does_not_break_typing_or_rendering():
    """[CXC-TERM-BOUNDARIES] row 13, the honesty check: the PTY is in-process, so a bus
    outage costs only the relay and the degraded publish."""
    w, _ctx, child = _with_fake_child()
    w._ctx = None                          # no bus, no engine context at all
    w.on_text("still typing")
    assert child.writes == [b"still typing"]
    w._enter_degraded("no bus here")       # publish must not raise
    c = _Canvas()
    w.draw(c, w.arranged_rect)
    assert "DEGRADED" in c.texts()
