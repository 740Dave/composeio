"""
ComposeUX ChatThreadWidget — four-voice conversation surface ([SPEC:CXC-IC]).

The Intelligence Console's conversation panel: a scrolling, word-wrapping
thread of FOUR distinct voices, each arriving as a message on a DIFFERENT bus
namespace.  State on the bus, behavior in the widget (the mermaid/bus_monitor
idiom) — the model emits a message once and every subsequent redraw is pure
widget behavior, no model round-trip.

Voices (topic + `from`-field -> voice) [CXC-IC-CHAT]:

    thread/narration            -> "architect·narrate"  dim,    left
        payload {from:"architect", text, turn, ts} — the thinking-aloud voice.
    intelligence/architect/out  -> the `from` (usually "architect")  accent, left
        payload {from, text, ts} — the deliberate replies.
    intelligence/architect/in   -> from=="harness" -> "harness"  amber, CENTER
                                   from=="david"/other human -> that name  neutral, left
        payload {from, text, ts, kind?} — inbound turns.

The widget subscribes a LIST of namespaces (``namespaces:`` attr; the parser
appends each to ``comp.subscriptions`` so ``on_bus_state`` fires per topic).
Voice is derived from (topic, from-field), NOT from a per-namespace config, so
the same three defaults work for every console and a scene may override the
list without re-teaching the widget its voices.

Scrollback keeps the last ``max_messages`` turns (bus_monitor's ring idiom).
Newest renders at the BOTTOM; older turns scroll up and clip.  All drawing is
Canvas-contract only — ``measure_text`` drives the word wrap so text is
identical on every backend.

Wheel scrollback (interactivity pass, ADDITIVE to the frozen wiring): a
scroll offset in px above the bottom (0 = pinned to the bottom = the
original behavior).  ``on_scroll`` moves it (clamped); ``draw`` honors it
and paints a thin right-edge scrollbar when the thread overflows.  While
pinned, a new turn keeps the view pinned; while scrolled up, a new turn
preserves the read position (the offset grows by exactly the appended
height, reconciled at draw time where heights are measurable).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, GradientStop, Size
from ..widget import Constraints, Rect, Widget
from . import style
from . import console_theme as ct
from . import markdown_inline as mi
from . import markdown_table as mdt
# Clipboard by REFERENCE, never a fork ([CXC-ICV-SELECT], R3): the copy leg
# reuses text_input's stable module-level SDL seam (lazy sdl2 import; the
# parallel agent owns text_input's WIDGET class, NOT this helper).  Imported
# at module top — text_input loads before chat_thread in widgets/__init__ — so
# there is exactly ONE clipboard code path across the console.
from .text_input import _clipboard_set

# -- instrument palette defaults ([CXC-IC-CHAT]) ------------------------------
# RGB 3-tuples; a .compose.yaml overrides via accent/dim/harness/neutral attrs.
_ACCENT = (0x4E, 0xA8, 0xFF)    # architect — deliberate replies
_DIM = (0x6D, 0x7C, 0x92)       # architect·narrate — thinking aloud
_HARNESS = (0xF5, 0xA6, 0x23)   # harness — amber, semantic, centered
_NEUTRAL = (0xE0, 0xE4, 0xEC)   # david / human — bright

_PANEL_BG = (0x14, 0x18, 0x22)
_HEADER = (0x8F, 0xA0, 0xB4)

# Wheel-notch -> px step for scrollback.  The runtime hands on_scroll the
# RAW SDL wheel deltas (runtime._dispatch_pointer "scroll" -> on_scroll(
# ev.dx, ev.dy)): dy > 0 is wheel-up.  The engine's existing consumers
# (doc_picker, composer monitor) map a notch to ~20-24px on a TOP-anchored
# offset via `offset -= dy * step`; this widget's offset is BOTTOM-anchored
# (grows upward), so the equivalent is `offset += dy * step`.
_SCROLL_STEP = 40.0

# SDL keymod masks ([CXC-ICV-SELECT]) — literals so this module never imports
# sdl2 (mirrors text_input's _KMOD_* seam).  Cmd (macOS GUI) OR Ctrl both fire
# the copy chord, so the shortcut is identical cross-platform.
_KMOD_CTRL = 0x00C0     # LCTRL | RCTRL
_KMOD_GUI = 0x0C00      # LGUI | RGUI (macOS Cmd)

# Sentinel "to the end of this visual line" for a per-line selection span —
# clamped to len(text) at draw time (a line's char count isn't known until the
# glyphs are measured).
_SEL_LINE_END = 1 << 30

# The three namespaces an Intelligence Console speaks on by default.
DEFAULT_NAMESPACES = (
    "thread/narration",
    "intelligence/architect/out",
    "intelligence/architect/in",
)

# Voice -> parenthesized header label ([CXC-IC-CHAT], David's format):
# "ARCHITECT (Narration) @HH:MM on Mon DD".  An unmapped voice title-cases.
_VOICE_LABEL = {
    "narration": "Narration",
    "reply": "Reply",
}


def _parse_ts(ts: Any) -> Optional[datetime]:
    """Parse a message ``ts`` — a unix epoch (int/float or numeric string) OR
    an ISO-8601 string (``Z`` tolerated) — into a LOCAL naive ``datetime`` for
    display.  Returns None for missing/garbage so the header degrades to just
    the name/voice ([CXC-IC-CHAT])."""
    if ts is None or isinstance(ts, bool):
        return None
    # numeric epoch (int/float, or a bare numeric string like "1753...")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts))
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        try:
            return datetime.fromtimestamp(float(s))
        except (ValueError, OverflowError, OSError):
            pass
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is not None:
            # show in local time, then drop the tzinfo (naive local)
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    return None


def _format_stamp(ts: Any) -> str:
    """David's header stamp: ``13:15 on Jul 22`` — 24-hour ``HH:MM``, month
    ``Jul``, day with no leading zero.  "" when ``ts`` is missing/unparseable
    (the caller then omits the ``@time`` entirely)."""
    dt = _parse_ts(ts)
    if dt is None:
        return ""
    return f"{dt.strftime('%H:%M')} on {dt.strftime('%b')} {dt.day}"


def _display_name(name: str) -> str:
    """Header display of an inbound sender: a bare lowercase handle (the common
    ``david``) capitalizes to ``David``; a name that already carries case is
    shown verbatim."""
    n = (name or "").strip() or "david"
    return n.capitalize() if n.islower() else n


@dataclass
class _Message:
    """One turn in the thread, already resolved to a voice.

    [CXC-ICV-CHAT]: extended (ADDITIVELY) with the fidelity pass's voice
    tag + the retained payload, resolved from the SAME (topic, from) logic
    — color/align/dim_sender keep their original semantics for callers.
    """
    sender: str
    text: str
    color: Tuple[int, int, int]
    align: str          # "left" | "center"
    dim_sender: bool    # narrate voice draws its sender label dimmer
    voice: str = "david"        # david | reply | narration | harness
    kind: str = ""              # harness packet kind (payload.kind)
    payload: dict = field(default_factory=dict)
    # [CXC-R-ENGINE-TEXT-MEASURE] per-turn wrap/measure MEMO.  A turn is
    # append-only immutable (its .text never changes after ingestion), so its
    # word-wrap + width/height layout is a pure function of (content_w,
    # font_size) — invariant across the many redraws a busy thread takes (every
    # inbound bus turn damages + redraws the whole read column).  Cached keyed on
    # that geometry so a redraw of UNCHANGED turns re-measures NOTHING; only a
    # newly-arrived turn (or a width change) actually wraps.  Mirrors the
    # composer field's _prefix_widths cache — the SAME measure-once discipline
    # the read surface had forked WITHOUT (the recurrence of the 4bf091b
    # prompt-lag defect: the composer was made linear, the thread that redraws on
    # every narration turn was not).  compare=False so it never enters equality.
    _layout_memo: dict = field(default_factory=dict, compare=False, repr=False)


# A styled inline run ([CXC-ICV-CHAT]) — now the SHARED ``markdown_inline``
# atom (reuse-by-reference, R3): the body renderer AND markdown_table cells
# tokenize/draw the SAME ``InlineRun`` through the one inline surface.  Aliased
# here so historical ``_Run(...)`` call sites and ``List[_Run]`` annotations are
# unchanged.
_Run = mi.InlineRun


@dataclass
class _VLine:
    """One VISUAL line after wrapping: a list of styled runs plus the bullet
    hanging-indent metadata.  ``bullet`` marks the FIRST visual line of a
    "- "/"* " list item (draw the "•" marker); continuation lines carry the
    same ``indent`` with ``bullet=False`` so wrapped text hangs under the
    content, not the marker.  ``para_start`` marks the FIRST visual line of a
    source paragraph (an explicit ``\\n`` / bullet / image-affordance block) —
    a soft-wrap continuation is False.  Selection copy rejoins soft-wrapped
    lines with a space and paragraph starts with a newline ([CXC-ICV-SELECT])."""
    runs: List[_Run]
    indent: float = 0.0
    bullet: bool = False
    para_start: bool = False
    # ATX heading support ([DEC-THREAD-MARKDOWN-HEADERS]): a ``## Foo`` message
    # line becomes a heading vline — ``size`` overrides the body size (from the
    # SHARED ``markdown_inline.heading_size`` hierarchy) and ``heading`` marks
    # it for the taller line pitch.  ``size=None`` (every non-heading line) uses
    # the body size, so prose/bullet/table vlines are byte-identical to before.
    size: Optional[float] = None
    heading: bool = False


@dataclass
class _SelLine:
    """One DRAWN visual line, captured during draw() for pixel<->char mapping
    ([CXC-ICV-SELECT]).

    ``bounds[k]`` is the ABSOLUTE surface x of the k-th character boundary of
    ``text`` (len(text)+1 entries), measured with the SAME ``measure_text``
    advances the glyphs were drawn with — the text_input ``_prefix_widths``
    idiom, one measurement path — so a hit-test lands on exactly what was
    painted.  A selection endpoint is addressed by (msg_idx, line_idx, char):
    CONTENT coordinates that survive a re-render and the scroll-offset math,
    never raw pixels.  ``_sel_lines`` is rebuilt every draw."""
    msg_idx: int
    line_idx: int
    text: str
    bounds: List[float]
    y_top: float
    y_bot: float
    para_start: bool


class ChatThreadWidget(Widget):
    """Scrolling, wrapping four-voice conversation ([SPEC:CXC-IC])."""

    def __init__(self, widget_id: str, *,
                 label: str = "Intelligence Console",
                 namespaces: Optional[List[str]] = None,
                 max_messages: int = 200,
                 font_size: float = 12.0,
                 accent: Tuple[int, int, int] = _ACCENT,
                 dim: Tuple[int, int, int] = _DIM,
                 harness: Tuple[int, int, int] = _HARNESS,
                 neutral: Tuple[int, int, int] = _NEUTRAL,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "chat_thread", **kwargs)
        self.label = str(label)
        self.namespaces = list(namespaces) if namespaces else list(DEFAULT_NAMESPACES)
        self.max_messages = int(max_messages)
        self.font_size = float(font_size)
        self.accent = tuple(accent)
        self.dim = tuple(dim)
        self.harness = tuple(harness)
        self.neutral = tuple(neutral)
        self._messages: List[_Message] = []
        # -- scrollback state (interactivity pass; ADDITIVE) ------------------
        # px above the bottom (0 = pinned to bottom = original behavior)
        self._scroll_offset: float = 0.0
        # content-above-viewport as of the last draw (on_scroll's clamp)
        self._max_scroll: float = 0.0
        # last message present at the last draw — draw-time append detection
        # (on_bus_state is FROZEN; reconciliation lives in draw math)
        self._last_tail: Optional[_Message] = None
        # interactive so the runtime's hit test routes wheel events here
        # (runtime "scroll" -> root.hit_test_tree -> target.on_scroll)
        self.interactive = True
        # [CXC-R-ENGINE-HOVER] the messages are selectable text -> I-beam, not
        # the click-hand.  The thread has a real drag-select gesture + Cmd/Ctrl+C
        # copy + select-all ([CXC-ICV-SELECT] below), so the I-beam is honored
        # today.  These are David's turns (his messages to the human) — content
        # to read/select/copy, not a tap target.
        self.text_selectable = True
        # -- text selection ([CXC-ICV-SELECT]; ADDITIVE, READ-ONLY) -----------
        # Selection is a WITHIN-VIEWPORT gesture addressed in CONTENT coords
        # (msg_idx, line_idx, char) so it survives a re-render; it is DROPPED
        # when the view scrolls or a new turn arrives — the drawn line set (and
        # therefore the addresses) would shift.  ``_sel_lines`` is the per-draw
        # record of the DRAWN bubble lines (the hit-test + highlight table),
        # rebuilt every draw.  ``_sel_epoch`` pins the message set the live
        # selection was made against (count + tail identity) so draw() can drop
        # a stale selection WITHOUT touching the frozen on_bus_state.
        self._sel_lines: List[_SelLine] = []
        self._sel_anchor: Optional[Tuple[int, int, int]] = None
        self._sel_focus: Optional[Tuple[int, int, int]] = None
        self._sel_epoch: Optional[Tuple[int, Any]] = None
        self._press_x = 0.0
        self._press_y = 0.0
        # Focusable so a Cmd/Ctrl+C routes here (focus-on-tap); the thread is a
        # READ surface, so it wears no focus ring — the selection band is the
        # only affordance (the composer-caret opt-out idiom, [CXC-R-ENGINE-FOCUS]).
        self.accepts_focus = True
        self.focus_ring = False

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """A turn arrived on one of the subscribed namespaces.

        ``value`` is the payload dict (json state engine) or its JSON string
        (raw MQTT); either way we resolve (topic, from) -> voice and append.
        """
        payload = self._coerce_payload(value)
        if payload is None:
            return
        msg = self._resolve_voice(namespace, payload)
        if msg is None:
            return
        # [David 2026-07-22] inbound turns (esp. David's, via the composer/bridge)
        # can arrive WITHOUT a `ts`, so their bubble degraded to a name-only header.
        # Stamp receipt time at ingestion (once, live) so every bubble shows @time —
        # receipt == send for a live turn; architect turns already carry ts, so this
        # is a no-op for them.
        if _parse_ts(msg.payload.get("ts")) is None:
            try:
                msg.payload["ts"] = datetime.now().timestamp()
            except (TypeError, AttributeError):
                pass
        self._messages.append(msg)
        if len(self._messages) > self.max_messages:
            del self._messages[:len(self._messages) - self.max_messages]
        self.request_redraw()

    @staticmethod
    def _coerce_payload(value: Any) -> Optional[dict]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            if s[0] in "{[":
                try:
                    parsed = json.loads(s)
                    return parsed if isinstance(parsed, dict) else None
                except (ValueError, TypeError):
                    return None
            # a bare string on the topic == the message text, no sender
            return {"text": s}
        return None

    def _resolve_voice(self, namespace: str, payload: dict) -> Optional[_Message]:
        text = payload.get("text")
        if text is None:
            return None
        text = str(text)
        sender = str(payload.get("from") or "").strip()
        ns = namespace.lower()

        if ns.endswith("narration") or "narrat" in ns:
            # thinking-aloud: dim, left, sender defaults to architect
            return _Message(sender=sender or "architect", text=text,
                            color=self.dim, align="left", dim_sender=True,
                            voice="narration", payload=payload)

        if ns.endswith("/out") or ns.endswith("out"):
            # deliberate reply from the architect: accent, left
            return _Message(sender=sender or "architect", text=text,
                            color=self.accent, align="left", dim_sender=False,
                            voice="reply", payload=payload)

        # inbound (…/in): harness == amber centered; human == neutral left
        low = sender.lower()
        if low == "harness":
            return _Message(sender="harness", text=text,
                            color=self.harness, align="center",
                            dim_sender=False, voice="harness",
                            kind=str(payload.get("kind") or ""),
                            payload=payload)
        return _Message(sender=sender or "david", text=text,
                        color=self.neutral, align="left", dim_sender=False,
                        voice="david", payload=payload)

    # -- scrollback ([CXC-IC-CHAT] interactivity pass; ADDITIVE) ---------------

    def on_scroll(self, dx: float, dy: float) -> None:
        """Wheel scrollback.  dy is the raw SDL wheel delta the runtime
        routes (dy > 0 = wheel-up = toward OLDER turns); the offset is px
        above the bottom, so wheel-up grows it.  Clamped to
        [0, max_scroll] as measured at the last draw (draw re-clamps
        against fresh content, so a stale bound can never overshoot)."""
        if not dy:
            return
        before = self._scroll_offset
        self._scroll_offset = max(0.0, min(
            self._max_scroll, self._scroll_offset + dy * _SCROLL_STEP))
        if self._scroll_offset != before:
            # a wheel scrolls; a drag selects — they never fight (distinct
            # events).  Scrolling the view drops any live selection, since the
            # drawn line set it was addressed against is about to change.
            self._clear_selection()
            self.request_redraw()

    def _sync_scroll(self, heights: List[float], content_h: float,
                     avail: float) -> None:
        """Draw-time reconciliation of the scroll offset against freshly
        measured content (heights need a canvas, so this cannot live in
        on_bus_state — which is frozen anyway).

        Pinned (offset 0) stays pinned: a new turn renders at the bottom
        exactly as before.  Scrolled up (offset > 0): the offset grows by
        exactly the height appended since the last draw, so the turns the
        reader is looking at do not move.  Ring eviction needs no
        correction — the offset is bottom-anchored and eviction only
        removes content ABOVE the viewport's frame of reference."""
        n_new = 0
        if self._last_tail is not None:
            for m in reversed(self._messages):
                if m is self._last_tail:
                    break
                n_new += 1
            else:
                # the previous tail itself was evicted between draws (a
                # >max_messages burst) — no stable anchor; keep the offset
                # and let the clamp below resolve it.
                n_new = 0
        if self._scroll_offset > 0.0 and 0 < n_new <= len(heights):
            self._scroll_offset += (sum(heights[len(heights) - n_new:])
                                    + ct.GAP_TURN * n_new)
        self._last_tail = self._messages[-1] if self._messages else None
        self._max_scroll = max(0.0, content_h - avail)
        self._scroll_offset = max(0.0, min(self._max_scroll,
                                           self._scroll_offset))

    def _draw_scrollbar(self, canvas: Canvas, box_x: float, box_y: float,
                        box_w: float, box_h: float, pad_y: float,
                        avail: float, content_h: float, t) -> None:
        """Thin track + thumb on the right inner edge, shown only when the
        thread overflows.  Geometry is pure (viewport / content, offset);
        tokens: ``line`` track, ``faint`` thumb ([CXC-ICV-TOKENS])."""
        if content_h <= avail or avail <= 0.0:
            return
        track_w = 4.0
        tx = box_x + box_w - 10.0
        ty = box_y + pad_y
        th = box_h - 2 * pad_y
        canvas.set_fill_color(*t.line)
        canvas.fill_round_rect(tx, ty, track_w, th, track_w / 2)
        thumb_h = max(24.0, th * (avail / content_h))
        travel = max(0.0, th - thumb_h)
        frac_top = 0.0 if self._max_scroll <= 0.0 else \
            (self._max_scroll - self._scroll_offset) / self._max_scroll
        canvas.set_fill_color(*t.faint)
        canvas.fill_round_rect(tx, ty + travel * frac_top,
                               track_w, thumb_h, track_w / 2)

    # -- text selection + copy ([CXC-ICV-SELECT]; READ-ONLY) -------------------
    #
    # Pixel<->char mapping reuses the EXACT glyph advances the paint path drew
    # with (recorded per visual line into _sel_lines during draw, the text_input
    # _prefix_widths idiom — one measurement path, so a hit-test can never
    # disagree with what was painted).  A press anchors, a captured drag extends,
    # Cmd/Ctrl+C copies the rendered PLAIN text (markdown markers already
    # stripped by the tokenizer — you copy what you SEE).  No caret, no edit.

    def _epoch(self) -> Tuple[int, Any]:
        """The message set a selection is valid against: count + tail
        IDENTITY (``is``, the _sync_scroll idiom).  A changed epoch (new turn
        or ring eviction) invalidates a live selection at draw time."""
        return (len(self._messages),
                self._messages[-1] if self._messages else None)

    def _clear_selection(self) -> None:
        self._sel_anchor = None
        self._sel_focus = None
        self._sel_epoch = None

    def _sel_ordered(self) -> Optional[Tuple[Tuple[int, int, int],
                                             Tuple[int, int, int]]]:
        """(lo, hi) selection endpoints in document order, or None when nothing
        is selected (no anchor, or a zero-width caret).  Endpoints are
        (msg_idx, line_idx, char) tuples — plain tuple ordering IS document
        order."""
        a, f = self._sel_anchor, self._sel_focus
        if a is None or f is None or a == f:
            return None
        return (a, f) if a <= f else (f, a)

    def _line_sel_span(self, msg_idx: int,
                       line_idx: int) -> Optional[Tuple[int, int]]:
        """The selected (start, end) char span for one visual line, or None
        when the line is outside the selection.  ``end`` may be the
        _SEL_LINE_END sentinel (whole rest of the line) — _draw_vline clamps
        it to the measured length."""
        o = self._sel_ordered()
        if o is None:
            return None
        lo, hi = o
        key = (msg_idx, line_idx)
        if key < (lo[0], lo[1]) or key > (hi[0], hi[1]):
            return None
        a = lo[2] if key == (lo[0], lo[1]) else 0
        b = hi[2] if key == (hi[0], hi[1]) else _SEL_LINE_END
        return (a, b)

    def _index_at(self, x: float, y: float
                  ) -> Optional[Tuple[int, int, int]]:
        """Map a pointer (surface coords) to (msg_idx, line_idx, char) against
        the LAST draw's line records: pick the line whose vertical band holds
        *y* (else the nearest line), then the nearest char boundary to *x* on
        that line's recorded advances.  None before any draw / with no
        selectable lines drawn."""
        lines = self._sel_lines
        if not lines:
            return None
        # Lines whose vertical band holds y.  For a prose thread there is
        # exactly ONE per band (contiguous, non-overlapping bands), so this is
        # identical to the historical first-match pick.  A TABLE ROW puts
        # several cells in the SAME band — break that tie by x so a click lands
        # on the column under the pointer (the cell whose x-span contains, or
        # is nearest to, x).  This is an ADDITIVE tie-break, not a rewrite: the
        # single-line-per-band case is unchanged.
        band = [sl for sl in lines if sl.y_top <= y < sl.y_bot]
        if band:
            chosen = min(band, key=lambda s: _x_gap(s.bounds, x))
        else:                                 # above/below/beside a gap
            chosen = min(lines,
                         key=lambda s: abs((s.y_top + s.y_bot) * 0.5 - y))
        b = chosen.bounds
        idx = min(range(len(b)), key=lambda i: abs(b[i] - x))
        return (chosen.msg_idx, chosen.line_idx, idx)

    def on_pointer_down(self, x: float, y: float) -> None:
        """Anchor a new selection at the pressed glyph boundary."""
        self._press_x, self._press_y = x, y
        pos = self._index_at(x, y)
        self._sel_anchor = pos
        self._sel_focus = pos
        self._sel_epoch = self._epoch()
        self.request_redraw()

    def on_pan(self, dx: float, dy: float) -> None:
        """Extend the selection to the dragged-over boundary (cumulative delta
        from the press point, the [CXC-R-ENGINE-HIT] convention)."""
        if self._sel_anchor is None:
            return
        pos = self._index_at(self._press_x + dx, self._press_y + dy)
        if pos is None:
            return
        self._sel_focus = pos
        self.request_redraw()

    def on_key(self, key: str, modifiers: Any = None) -> None:
        """Cmd/Ctrl+C copies the selection; Cmd/Ctrl+A selects the drawn
        thread; Escape clears.  Nothing else — the thread is read-only."""
        mod = int(modifiers or 0)
        primary = bool(mod & (_KMOD_CTRL | _KMOD_GUI))
        k = key.upper() if isinstance(key, str) and len(key) == 1 else key
        if primary and k == "C":
            self._copy()
        elif primary and k == "A":
            self._select_all()
        elif key == "Escape" and self._sel_anchor is not None:
            self._clear_selection()
            self.request_redraw()

    def _select_all(self) -> None:
        """Select every DRAWN selectable line (start of the first to end of the
        last).  Bounded to what's on screen — the same within-viewport contract
        as a drag."""
        if not self._sel_lines:
            return
        first, last = self._sel_lines[0], self._sel_lines[-1]
        self._sel_anchor = (first.msg_idx, first.line_idx, 0)
        self._sel_focus = (last.msg_idx, last.line_idx, len(last.text))
        self._sel_epoch = self._epoch()
        self.request_redraw()

    def _copy(self) -> None:
        """Copy the selected rendered plain text to the OS clipboard (no-op
        when nothing is selected)."""
        text = self._selected_text()
        if text:
            _clipboard_set(text)

    def _selected_text(self) -> str:
        """Reconstruct the selected PLAIN text from the drawn line records.

        Rendered text (markdown markers already gone).  Rejoins: a soft-wrap
        break -> a single space (the space the wrapper consumed); a paragraph
        start or a bubble boundary -> a newline.  Bullet MARKERS are decorative
        (drawn in the gutter, not part of a line's text) and are omitted."""
        o = self._sel_ordered()
        if o is None:
            return ""
        lo, hi = o
        parts: List[str] = []
        prev_msg: Optional[int] = None
        for sl in self._sel_lines:            # already in (msg, line) order
            key = (sl.msg_idx, sl.line_idx)
            if key < (lo[0], lo[1]) or key > (hi[0], hi[1]):
                continue
            n = len(sl.text)
            a = lo[2] if key == (lo[0], lo[1]) else 0
            b = hi[2] if key == (hi[0], hi[1]) else n
            a = max(0, min(a, n))
            b = max(0, min(b, n))
            if parts:
                if sl.msg_idx != prev_msg or sl.para_start:
                    parts.append("\n")
                else:
                    parts.append(" ")
            parts.append(sl.text[a:b])
            prev_msg = sl.msg_idx
        return "".join(parts)

    # -- measure / draw --------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(440.0, constraints.max_width),
                    min(360.0, constraints.max_height))

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)      # adopt design.theme ([CXC-ICV-THEME])

    # -- draw ([CXC-ICV-CHAT]: the four-voice hero) ----------------------------
    #
    # Bubbles per voice: david right-anchored panel2 bubble; reply left
    # accent-edged; narration the SAME left accent bubble as reply — its
    # who-line label is the sole distinguisher (David's call); harness a
    # centered mono packet with report/beat/guidance variants.  Bottom-anchor + clip (the auto-
    # scroll) is the SAME newest-at-the-bottom logic as before.  Single-radius
    # bubbles + solid packet borders are the documented approximations
    # ([CXC-ICV-TOKENS]).

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()

        # Drop a live selection whose message set changed since it was made (a
        # new turn / ring eviction shifts the addresses) — done HERE, not in the
        # frozen on_bus_state.  Rebuild the per-draw hit-test/highlight table.
        if self._sel_anchor is not None:
            cnt, tail = self._epoch()
            ep = self._sel_epoch
            if ep is None or cnt != ep[0] or tail is not ep[1]:
                self._clear_selection()
        self._sel_lines = []

        # the read column ([CXC-ICV-TOKENS]: gradient fill, radius 16, line
        # border, ambient shadow)
        box_x, box_y = rect.x, rect.y
        box_w, box_h = rect.w, rect.h
        ct.draw_card(canvas, box_x, box_y, box_w, box_h, ct.RADIUS_READ, t)

        pad_x = max(18.0, min(40.0, box_w * 0.04))
        pad_y = ct.PAD_THREAD_Y
        content_x = box_x + pad_x
        content_w = max(60.0, box_w - 2 * pad_x)

        canvas.save()
        canvas.clip_round_rect(box_x, box_y, box_w, box_h, ct.RADIUS_READ)

        # measure pass: per-turn block heights
        heights = [self._paint_turn(canvas, m, content_x, content_w, 0.0, t,
                                    measure_only=True)
                   for m in self._messages]

        # scrollback anchoring: the stack bottom sits at the viewport
        # bottom + scroll offset (offset 0 == the original bottom-anchor);
        # under-full content still bottom-anchors.  Turns outside the
        # viewport are skipped; partial turns clip (the card clip above).
        avail = box_h - 2 * pad_y
        content_h = sum(heights) + ct.GAP_TURN * max(0, len(heights) - 1)
        self._sync_scroll(heights, content_h, avail)
        if content_h <= avail:
            y = box_y + pad_y + (avail - content_h)
        else:
            y = box_y + box_h - pad_y - content_h + self._scroll_offset
        for j, msg in enumerate(self._messages):
            if y > box_y + box_h:
                break
            if y + heights[j] >= box_y:
                self._paint_turn(canvas, msg, content_x, content_w, y, t,
                                 msg_idx=j)
            y += heights[j] + ct.GAP_TURN
        self._draw_scrollbar(canvas, box_x, box_y, box_w, box_h, pad_y,
                             avail, content_h, t)
        canvas.restore()

        if not self._messages:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_UI, 11, "regular")
            canvas.draw_text("awaiting the thread…",
                             box_x + box_w / 2, box_y + box_h / 2, "center")
        canvas.restore()

    # -- per-turn painter -------------------------------------------------------

    def _paint_turn(self, canvas: Canvas, msg: _Message, x0: float,
                    cw: float, y: float, t, measure_only: bool = False,
                    msg_idx: int = 0) -> float:
        if msg.voice == "harness":
            return self._paint_packet(canvas, msg, x0, cw, y, t, measure_only)
        # david, reply AND narration all share the bubble painter —
        # narration reuses the reply bubble path so the two stay in sync.
        return self._paint_bubble(canvas, msg, x0, cw, y, t, measure_only,
                                  msg_idx)

    def _bubble_layout(self, canvas: Canvas, msg: _Message, cw: float,
                       is_reply: bool) -> dict:
        """[CXC-R-ENGINE-TEXT-MEASURE] the memoized wrap+measure of ONE bubble.

        A turn is append-only immutable, so its wrapped block layout and the
        derived bubble box (bw/bh) + total height are a pure function of
        (content_w, font_size).  We cache the whole record ON THE TURN keyed on
        that geometry: a redraw of an unchanged turn — which the read column
        takes on EVERY inbound bus turn (narration, reply, heartbeats) — reuses
        it and re-measures nothing.  The expensive part (word-wrap in
        _build_body_blocks, plus the per-vline width re-measure) runs once per
        turn, not once per draw.  This is the read-surface analog of the
        composer field's _prefix_widths cache; forking the measure-per-draw path
        WITHOUT this cache is exactly why the prompt-lag defect recurred."""
        body_size = self.font_size
        pad_bx, pad_by = 14.0, 9.0
        cap = min(cw * 0.78, ct.THREAD_MEASURE)
        image = str(msg.payload.get("image") or "").strip()
        # geometry key: cap (derived from cw) + body_size + image + text.  text
        # is intrinsic to the turn, so in practice only cap changes (a window
        # resize) — the guard keeps the cache correct if a caller ever mutates.
        key = (round(cap, 2), round(body_size, 3), image, msg.text)
        memo = msg._layout_memo.get("bubble")
        if memo is not None and memo[0] == key:
            return memo[1]

        # BASIC inline markdown ([CXC-ICV-CHAT]): tokenize the body into styled
        # runs (plain/bold/code) + bullet lines, then wrap ACROSS runs so a
        # mid-sentence **bold** or `code` span breaks at the column like plain
        # prose.  Body is a list of BLOCKS: ("prose", vlines) | ("table", model,
        # layout); a pipe-table routes through the reusable markdown_table module.
        body_w = cap - 2 * pad_bx
        blocks = self._build_body_blocks(canvas, msg.text, image,
                                         body_w, body_size)
        line_h = body_size * 1.5
        block_gap = line_h * 0.35
        line_w = 0.0
        content_h = 0.0
        for bi, blk in enumerate(blocks):
            if bi:
                content_h += block_gap
            if blk[0] == "prose":
                for vl in blk[1]:
                    vs, lh = self._vline_layout(vl, body_size)
                    line_w = max(line_w, self._vline_width(canvas, vl, vs))
                    content_h += lh
            else:                               # ("table", model, layout)
                line_w = max(line_w, blk[2].total_width)
                content_h += blk[2].total_height
        bw = min(cap, line_w + 2 * pad_bx)
        bh = content_h + 2 * pad_by
        who_h = 14.0
        emit = msg.payload.get("emit") if is_reply else None
        emit_h = 13.0 if emit else 0.0
        total = who_h + bh + emit_h + (4.0 if emit else 0.0)
        record = {"blocks": blocks, "cap": cap, "block_gap": block_gap,
                  "bw": bw, "bh": bh, "who_h": who_h, "emit": emit,
                  "emit_h": emit_h, "total": total}
        msg._layout_memo["bubble"] = (key, record)
        return record

    def _paint_bubble(self, canvas: Canvas, msg: _Message, x0: float,
                      cw: float, y: float, t, measure_only: bool,
                      msg_idx: int = 0) -> float:
        """david (right-anchored panel bubble) + reply (accent-edged) +
        narration (the SAME accent bubble as reply; only its who-line label
        — "~ … · NARRATION · AUTO-MIRRORED MAIN-LOOP" — tells them apart,
        per David).  Italic is not attempted: the Canvas contract has no
        italic Plex face, and inside the shared bubble the reply's upright
        ink body is the point — the bubbles must read as the same."""
        is_reply = msg.voice == "reply"
        is_narration = msg.voice == "narration"
        accent_bubble = is_reply or is_narration    # shared reply treatment
        # [CXC-R-ENGINE-TEXT-MEASURE] the whole wrap/measure is memoized per
        # turn+geometry (see _bubble_layout) — a redraw of an unchanged turn
        # re-measures NOTHING.  Both the measure-only pass (heights of EVERY
        # turn, every draw) and the paint pass read the same cached record, so
        # the read surface's per-draw cost is O(turns that CHANGED), not
        # O(all turns) — the recurrence fix for the composer typing-lag defect.
        geom = self._bubble_layout(canvas, msg, cw, is_reply)
        blocks = geom["blocks"]
        body_size = self.font_size
        pad_bx, pad_by = 14.0, 9.0
        cap = geom["cap"]
        block_gap = geom["block_gap"]
        bw, bh, who_h = geom["bw"], geom["bh"], geom["who_h"]
        emit = geom["emit"]
        if measure_only:
            return geom["total"]

        bx = x0 if accent_bubble else x0 + cw - bw    # anchor by voice
        # who-line — David's exact format ([CXC-IC-CHAT]), one line, dim, small:
        #   architect voices: "ARCHITECT (Narration) @13:15 on Jul 22"
        #                     "ARCHITECT (Reply) @13:15 on Jul 22"
        #     the parenthesized Voice label (not color) is now the sole
        #     narration/reply distinguisher; the accent-EDGED bubble still
        #     separates architect from david.
        #   david / human:    "David @13:17 on Jul 22" (right-anchored)
        # A missing/garbage ``ts`` degrades gracefully (the @time is omitted).
        canvas.set_font(style.FONT_MONO, ct.LABEL_MONO, "regular")
        m = canvas.measure_text("Ag")
        base = y + m.ascent
        stamp = _format_stamp(msg.payload.get("ts"))
        canvas.set_fill_color(*t.faint)
        if accent_bubble:
            voice_label = _VOICE_LABEL.get(
                msg.voice, (msg.voice or "reply").capitalize())
            who = f"{(msg.sender or 'architect').upper()} ({voice_label})"
            if stamp:
                who += f" @{stamp}"
            canvas.draw_text(who, bx + 2, base, "left")
        else:
            who = _display_name(msg.sender or "david")
            if stamp:
                who += f" @{stamp}"
            ww = canvas.measure_text(who).width
            canvas.draw_text(who, bx + bw - ww - 2, base, "left")
        by = y + who_h

        # bubble (single 12px radius — documented asym-radii approximation)
        if accent_bubble:
            canvas.set_fill_color(*t.accent_dim)
        else:
            canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(bx, by, bw, bh, ct.RADIUS_BUBBLE)
        canvas.set_stroke_color(*(t.accent_line if accent_bubble else t.line2))
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(bx, by, bw, bh, ct.RADIUS_BUBBLE)

        # Draw each body block, assigning a running line_idx in DRAW (document)
        # order so a selection endpoint (msg_idx, line_idx, char) orders
        # correctly across prose lines AND table cells.  Every drawn line/cell
        # records into _sel_lines with the SAME advances it painted with — the
        # one measurement path — so table cells are as selectable/copyable as
        # prose (no non-selectable holes).
        ly = by + pad_by
        next_li = [0]
        for bi, blk in enumerate(blocks):
            if bi:
                ly += block_gap
            if blk[0] == "prose":
                for k, vl in enumerate(blk[1]):
                    vs, lh = self._vline_layout(vl, body_size)
                    li = next_li[0]
                    span = self._line_sel_span(msg_idx, li)
                    line_text, bounds = self._draw_vline(
                        canvas, vl, bx + pad_bx, ly, vs, lh, t, span)
                    self._sel_lines.append(_SelLine(
                        msg_idx=msg_idx, line_idx=li, text=line_text,
                        bounds=bounds, y_top=ly, y_bot=ly + lh,
                        para_start=(k == 0) or vl.para_start))
                    next_li[0] += 1
                    ly += lh
            else:
                model, layout = blk[1], blk[2]

                def _record(cell_text, cbounds, cy0, cy1, row_start,
                            _mi=msg_idx):
                    li = next_li[0]
                    next_li[0] += 1
                    span = self._line_sel_span(_mi, li)
                    self._sel_lines.append(_SelLine(
                        msg_idx=_mi, line_idx=li, text=cell_text,
                        bounds=cbounds, y_top=cy0, y_bot=cy1,
                        para_start=row_start))
                    return span

                mdt.draw_table(canvas, model, layout, bx + pad_bx, ly,
                               self._table_style(body_size), record=_record)
                ly += layout.total_height

        # optional emit line: "↳ emitted git/graph · thread/diagram"
        if emit:
            topics = emit if isinstance(emit, (list, tuple)) else [emit]
            ey = by + bh + 4
            canvas.set_font(style.FONT_MONO, ct.LABEL_MONO, "regular")
            em = canvas.measure_text("Ag")
            ebase = ey + em.ascent
            canvas.set_fill_color(*t.faint)
            prefix = "↳ emitted "
            canvas.draw_text(prefix, bx + 2, ebase, "left")
            ex = bx + 2 + canvas.measure_text(prefix).width
            for i, topic in enumerate(topics):
                if i:
                    canvas.set_fill_color(*t.faint)
                    canvas.draw_text(" · ", ex, ebase, "left")
                    ex += canvas.measure_text(" · ").width
                canvas.set_fill_color(*t.accent)
                canvas.draw_text(str(topic), ex, ebase, "left")
                ex += canvas.measure_text(str(topic)).width
        return geom["total"]

    # -- body blocks (prose + tables) ------------------------------------------

    def _build_body_blocks(self, canvas: Canvas, text: str, image: str,
                           body_w: float, body_size: float) -> List[tuple]:
        """Split a message body into ordered render blocks.

        A pipe table becomes a ``("table", model, layout)`` block laid out by
        the reusable ``markdown_table`` module (capped to the bubble column);
        every other run of text becomes a ``("prose", vlines)`` block wrapped
        by the existing inline path — so non-table messages render byte-for-byte
        as before.  A dropped-image affordance is appended as a final prose
        block (David's ask), unchanged."""
        blocks: List[tuple] = []
        if text.strip():
            for kind, obj in mdt.segment_tables(text):
                if kind == "table":
                    layout = mdt.measure_table(
                        canvas, obj, self._table_style(body_size), body_w)
                    blocks.append(("table", obj, layout))
                else:
                    blocks.append(("prose", self._layout_body(
                        canvas, obj, body_w, body_size)))
        if image:
            blocks.append(("prose", self._image_affordance_vlines(
                canvas, image, body_w, body_size)))
        if not blocks:
            blocks.append(("prose", [_VLine(runs=[], para_start=True)]))
        return blocks

    def _table_style(self, body_size: float) -> "mdt.TableStyle":
        """Build a markdown_table style from the ACTIVE console theme tokens
        ([CXC-ICV-TOKENS]) — the module stays console-free; the widget maps its
        inks/rules to it."""
        t = ct.CONSOLE.active
        return mdt.TableStyle(
            font_size=body_size,
            header_ink=t.ink, body_ink=t.ink, code_ink=t.accent,
            rule=t.line2, grid=t.line, sel=t.accent_line)

    # harness packet variants ([CXC-ICV-CHAT] .hpk) -----------------------------

    def _packet_variant(self, msg: _Message) -> str:
        k = (msg.kind or "").lower()
        if "guidance" in k:
            return "guidance"
        if "report" in k:
            return "report"
        if "beat" in k or "heartbeat" in k:
            return "beat"
        return "plain"

    @staticmethod
    def _packet_attrs(payload: dict) -> List[Tuple[str, str]]:
        """attrs line: an explicit payload["attrs"] dict, else the scalar
        keys beyond the frozen envelope fields (additive read)."""
        attrs = payload.get("attrs")
        if isinstance(attrs, dict):
            return [(str(k), str(v)) for k, v in attrs.items()]
        skip = {"from", "text", "ts", "kind", "turn", "items", "emit"}
        out = []
        for k, v in payload.items():
            if k in skip or isinstance(v, (dict, list)):
                continue
            out.append((str(k), str(v)))
        return out

    def _paint_packet(self, canvas: Canvas, msg: _Message, x0: float,
                      cw: float, y: float, t, measure_only: bool) -> float:
        variant = self._packet_variant(msg)
        pw = min(cw * 0.92, 620.0)
        px = x0 + (cw - pw) / 2.0
        pad_px, pad_py = 12.0, 9.0
        body_size = self.font_size * 0.82

        # split body into text lines + bullet items (guidance)
        items = msg.payload.get("items")
        bullets: List[str] = [str(i) for i in items] \
            if isinstance(items, (list, tuple)) else []
        body_text = msg.text
        if not bullets and variant == "guidance":
            plain, bl = [], []
            for ln in body_text.splitlines():
                s = ln.strip()
                if s.startswith(("- ", "• ", "* ")):
                    bl.append(s[2:].strip())
                else:
                    plain.append(ln)
            if bl:
                body_text, bullets = "\n".join(plain), bl

        canvas.set_font(style.FONT_MONO, body_size, "regular")
        inner_w = pw - 2 * pad_px
        body_lines = self._wrap(canvas, body_text, inner_w)
        bullet_lines: List[List[str]] = [
            self._wrap(canvas, b, inner_w - 12) for b in bullets]
        line_h = body_size * 1.45
        hrow_h = 16.0
        body_h = len(body_lines) * line_h
        bullets_h = sum(len(bl) * line_h for bl in bullet_lines) + \
            (3.0 if bullet_lines else 0.0)
        attrs = self._packet_attrs(msg.payload)
        attrs_h = 13.0 if attrs else 0.0
        glow_h = 6.0 if variant == "guidance" else 0.0
        ph = 2 * pad_py + hrow_h + body_h + bullets_h + attrs_h + glow_h
        if measure_only:
            return ph

        if variant == "beat":
            canvas.set_opacity(0.85)

        # packet card (dashed border unavailable -> the documented solid-
        # line-2 degrade; report/guidance use their solid variant borders)
        if variant == "guidance":
            canvas.set_fill_color(*t.crit_dim)
        else:
            canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(px, y, pw, ph, ct.RADIUS_PACKET)
        border = {"report": t.accent_line,
                  "guidance": t.crit_line}.get(variant, t.line2)
        canvas.set_stroke_color(*border)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(px, y, pw, ph, ct.RADIUS_PACKET)
        if variant == "guidance":
            # the 3px crit left border
            canvas.set_fill_color(*t.crit)
            canvas.fill_rect(px, y + 2, 3.0, ph - 4)

        # hrow: FROM HARNESS + the kind badge
        canvas.set_font(style.FONT_MONO, 8.0, "regular")
        m = canvas.measure_text("Ag")
        base = y + pad_py + m.ascent
        fw = ct.draw_tracked(canvas, "FROM HARNESS", px + pad_px, base,
                             1.0, t.faint)
        kind = (msg.kind or "packet").upper()
        kx = px + pad_px + fw + 10
        km = canvas.measure_text(kind)
        badge_col = {"report": t.accent, "guidance": t.crit}.get(
            variant, t.dim)
        if variant == "report":
            canvas.set_fill_color(*t.accent_dim)
            canvas.fill_round_rect(kx - 4, y + pad_py - 2,
                                   km.width + 8, 13, ct.RADIUS_BADGE)
        canvas.set_stroke_color(*({"report": t.accent_line,
                                   "guidance": t.crit_line}.get(
                                       variant, t.line2)))
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(kx - 4, y + pad_py - 2, km.width + 8, 13,
                                 ct.RADIUS_BADGE)
        canvas.set_fill_color(*badge_col)
        canvas.draw_text(kind, kx, base, "left")

        # body
        canvas.set_font(style.FONT_MONO, body_size, "regular")
        ly = y + pad_py + hrow_h
        for i, ln in enumerate(body_lines):
            lm = canvas.measure_text(ln)
            if variant == "guidance" and i == 0:
                canvas.set_font(style.FONT_MONO, body_size, "bold")
                canvas.set_fill_color(*t.crit)
                canvas.draw_text(ln, px + pad_px, ly + lm.ascent, "left")
                canvas.set_font(style.FONT_MONO, body_size, "regular")
            else:
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(ln, px + pad_px, ly + lm.ascent, "left")
            ly += line_h

        # guidance bullets (crit markers)
        if bullet_lines:
            ly += 3.0
            for bl in bullet_lines:
                first = True
                for ln in bl:
                    lm = canvas.measure_text(ln)
                    if first:
                        canvas.set_fill_color(*t.crit)
                        canvas.draw_text("›", px + pad_px, ly + lm.ascent,
                                         "left")
                        first = False
                    canvas.set_fill_color(*t.dim)
                    canvas.draw_text(ln, px + pad_px + 12, ly + lm.ascent,
                                     "left")
                    ly += line_h

        # attrs line (values accent)
        if attrs:
            canvas.set_font(style.FONT_MONO, 8.0, "regular")
            am = canvas.measure_text("Ag")
            abase = ly + am.ascent + 1
            ax = px + pad_px
            for i, (k, v) in enumerate(attrs):
                if i:
                    canvas.set_fill_color(*t.faint)
                    canvas.draw_text(" · ", ax, abase, "left")
                    ax += canvas.measure_text(" · ").width
                canvas.set_fill_color(*t.faint)
                canvas.draw_text(k + "=", ax, abase, "left")
                ax += canvas.measure_text(k + "=").width
                canvas.set_fill_color(*t.accent)
                canvas.draw_text(v, ax, abase, "left")
                ax += canvas.measure_text(v).width
            ly += attrs_h

        # guidance glow bar: transparent -> crit -> transparent
        if variant == "guidance":
            gy = y + ph - pad_py + 3
            canvas.set_linear_gradient(px + pad_px, gy,
                                       px + pw - pad_px, gy, [
                                           GradientStop(0.0, *t.crit[:3], 0),
                                           GradientStop(0.5, *t.crit),
                                           GradientStop(1.0, *t.crit[:3], 0)])
            canvas.fill_round_rect(px + pad_px, gy - 1,
                                   pw - 2 * pad_px, 2, 1)
            canvas.set_fill_color(*t.dim)   # clear gradient override

        if variant == "beat":
            canvas.set_opacity(1.0)
        return ph

    # -- BASIC inline markdown ([CXC-ICV-CHAT]) --------------------------------
    #
    # Deliberately NOT CommonMark: exactly **bold**, `code`, and "- "/"* "
    # bullets — nothing else (no italic: the bundled Plex face ships no
    # italic; no headers/links/tables/blockquotes/nesting).  Malformed or
    # unclosed markers render LITERAL — the tokenizer only opens a styled run
    # when it finds the matching closer, so a lone ``**`` or `` ` `` can never
    # crash or eat the rest of the message.

    # The inline tokenizer + face selection are the SHARED ``markdown_inline``
    # surface (reuse-by-reference); these thin delegations keep the historical
    # call sites (and ``ChatThreadWidget._tokenize_inline`` tests) intact.
    _tokenize_inline = staticmethod(mi.tokenize_inline)

    def _set_run_font(self, canvas: Canvas, kind: str,
                      base_size: float) -> None:
        mi.set_run_font(canvas, kind, base_size)

    def _layout_body(self, canvas: Canvas, text: str, max_w: float,
                     base_size: float) -> List[_VLine]:
        """Tokenize + wrap the whole body into VISUAL lines.

        Each paragraph (explicit ``\\n``) is checked for a "- "/"* " bullet
        marker (leading whitespace tolerated); a bullet's content wraps into a
        narrower column (``max_w`` minus the marker indent) with a hanging
        indent so continuation lines align under the text, not the marker.
        Plain paragraphs wrap the full width.  Blank lines are preserved.
        """
        canvas.set_font(style.FONT_UI, base_size, "regular")
        indent = canvas.measure_text("• ").width
        vlines: List[_VLine] = []
        for para in text.replace("\r", "").split("\n"):
            # ATX heading ([DEC-THREAD-MARKDOWN-HEADERS]): a ``#``..``######``
            # line renders bold at the SHARED heading-size hierarchy (the same
            # detector + sizing the doc viewer uses — never a second heading
            # grammar).  Headings render bold like the viewer: plain/bold runs
            # force to the bold face; a `code` span keeps its own face.  Wrap is
            # measured at the HEADING size so wrap/measure/draw agree.
            head = mi.parse_atx_heading(para)
            if head is not None:
                level, title = head
                hsize = mi.heading_size(level, base_size)
                hruns = [_Run(r.text,
                              "bold" if r.kind in ("plain", "bold") else r.kind)
                         for r in self._tokenize_inline(title)]
                for k, wl in enumerate(
                        self._wrap_runs(canvas, hruns, max_w, hsize)):
                    vlines.append(_VLine(runs=wl, para_start=(k == 0),
                                         size=hsize, heading=True))
                continue
            stripped = para.lstrip()
            is_bullet = stripped[:2] in ("- ", "* ")
            if is_bullet:
                content = stripped[2:]
                avail = max(1.0, max_w - indent)
                bindent = indent
            else:
                content = para
                avail = max_w
                bindent = 0.0
            runs = self._tokenize_inline(content)
            for k, wl in enumerate(self._wrap_runs(canvas, runs, avail,
                                                   base_size)):
                vlines.append(_VLine(runs=wl, indent=bindent,
                                     bullet=(is_bullet and k == 0),
                                     para_start=(k == 0)))
        return vlines or [_VLine(runs=[], para_start=True)]

    def _image_affordance_vlines(self, canvas: Canvas, path: str,
                                 max_w: float, base_size: float
                                 ) -> List[_VLine]:
        """Visual lines for a dropped-image affordance ([CXC-ICV-CHAT]).

        A quiet mono ``image ▸`` marker (faint) then the file PATH (mono, dim,
        legible), wrapped ACROSS the two runs to the bubble column so a long
        absolute path breaks cleanly instead of overflowing.  Emoji are
        avoided on purpose — the bundled Plex face ships no emoji glyphs (a
        ``📎`` would render as a tofu box), so the marker is a text/vector
        ``▸``.  Built as explicit runs (not re-tokenized) so a path containing
        markdown metacharacters (``*``/`` ` ``) can never be reinterpreted.
        """
        runs = [_Run("image ▸ ", "imark"), _Run(path, "path")]
        return [_VLine(runs=wl, para_start=(k == 0))
                for k, wl in enumerate(
                    self._wrap_runs(canvas, runs, max_w, base_size))]

    def _wrap_runs(self, canvas: Canvas, runs: List[_Run], max_w: float,
                   base_size: float) -> List[List[_Run]]:
        """Greedy word-wrap ACROSS styled runs: each run is split into word +
        space atoms (measured in its own font), packed onto visual lines up to
        *max_w*.  A single word wider than the column hard-breaks by character
        in its own style.  Returns a list of visual lines (each a list of
        merged runs); always at least one (possibly empty) line."""
        atoms: List[Tuple[str, str, bool]] = []
        for r in runs:
            words = r.text.split(" ")
            for wi, wd in enumerate(words):
                if wi:
                    atoms.append((" ", r.kind, True))
                if wd:
                    atoms.append((wd, r.kind, False))

        def measure(txt: str, kind: str) -> float:
            self._set_run_font(canvas, kind, base_size)
            return canvas.measure_text(txt).width

        lines: List[List[List]] = []
        cur: List[List] = []          # [text, kind, width]
        cur_w = 0.0
        for txt, kind, is_space in atoms:
            w = measure(txt, kind)
            if is_space:
                if cur:               # never a leading space
                    cur.append([txt, kind, w])
                    cur_w += w
                continue
            if cur and cur_w + w > max_w:
                while cur and cur[-1][0] == " ":
                    cur_w -= cur[-1][2]
                    cur.pop()
                lines.append(cur)
                cur, cur_w = [], 0.0
            if not cur and w > max_w:
                pieces = self._hard_break_piece(canvas, txt, kind, max_w,
                                                base_size)
                for ptext, pw in pieces[:-1]:
                    lines.append([[ptext, kind, pw]])
                ltext, lw = pieces[-1]
                cur, cur_w = [[ltext, kind, lw]], lw
            else:
                cur.append([txt, kind, w])
                cur_w += w
        if cur:
            while cur and cur[-1][0] == " ":
                cur.pop()
            lines.append(cur)
        if not lines:
            lines.append([])
        return [self._merge_pieces(pl) for pl in lines]

    def _hard_break_piece(self, canvas: Canvas, token: str, kind: str,
                          max_w: float, base_size: float
                          ) -> List[Tuple[str, float]]:
        """Break a single over-wide word by characters, in its own style."""
        self._set_run_font(canvas, kind, base_size)
        pieces: List[Tuple[str, float]] = []
        cur = ""
        for ch in token:
            trial = cur + ch
            if cur and canvas.measure_text(trial).width > max_w:
                pieces.append((cur, canvas.measure_text(cur).width))
                cur = ch
            else:
                cur = trial
        if cur:
            pieces.append((cur, canvas.measure_text(cur).width))
        return pieces or [(token, canvas.measure_text(token).width)]

    @staticmethod
    def _merge_pieces(pieces: List[List]) -> List[_Run]:
        """Coalesce adjacent same-kind atoms of one visual line into runs."""
        out: List[_Run] = []
        for txt, kind, _w in pieces:
            if out and out[-1].kind == kind:
                out[-1].text += txt
            else:
                out.append(_Run(txt, kind))
        return out

    @staticmethod
    def _vline_layout(vline: _VLine, body_size: float) -> Tuple[float, float]:
        """The (font size, line height) for a visual line — the ONE place
        measure and draw agree ([DEC-THREAD-MARKDOWN-HEADERS]).  A heading vline
        uses its own ``size`` and the tighter 1.35 heading pitch (the viewer's
        heading line ratio); every other line is the body size at the historical
        1.5 pitch, so non-heading measure/draw are byte-identical to before."""
        vs = vline.size if vline.size else body_size
        lh = vs * (1.35 if vline.heading else 1.5)
        return vs, lh

    def _vline_width(self, canvas: Canvas, vline: _VLine,
                     base_size: float) -> float:
        w = vline.indent
        for run in vline.runs:
            self._set_run_font(canvas, run.kind, base_size)
            w += canvas.measure_text(run.text).width
        return w

    def _draw_vline(self, canvas: Canvas, vline: _VLine, x0: float,
                    y: float, base_size: float, line_h: float, t,
                    sel_span: Optional[Tuple[int, int]] = None
                    ) -> Tuple[str, List[float]]:
        """Draw one visual line: an optional selection highlight band, an
        optional "•" marker in the indent gutter, then each styled run
        left-to-right.  code -> mono/accent; bold -> sans-bold/ink; plain ->
        sans/ink.  Runs share the plain baseline.

        Returns ``(line_text, bounds)`` — the drawn plain text and the ABSOLUTE
        x of each character boundary (len+1) — measured on the SAME
        ``measure_text`` advances the glyphs draw with, so the selection
        hit-test/highlight lands on exactly what was painted
        ([CXC-ICV-SELECT])."""
        canvas.set_font(style.FONT_UI, base_size, "regular")
        base = y + canvas.measure_text("Ag").ascent
        # per-char absolute boundaries across the styled runs — the ONE shared
        # inline-measurement path (markdown_inline.run_bounds), the same one
        # markdown_table cells record against.
        x0i = x0 + vline.indent
        line_text, bounds = mi.run_bounds(canvas, vline.runs, x0i, base_size)

        # selection band BEHIND the glyphs (above the bubble fill) ----------
        if sel_span is not None and line_text:
            a = max(0, sel_span[0])
            b = min(len(line_text), sel_span[1])
            if a < b:
                canvas.set_fill_color(*t.accent_line)
                canvas.fill_rect(bounds[a], y + 1.0,
                                 bounds[b] - bounds[a], max(1.0, line_h - 2.0))

        if vline.bullet:
            canvas.set_font(style.FONT_UI, base_size, "regular")
            canvas.set_fill_color(*t.ink)
            canvas.draw_text("•", x0, base, "left")
        # code -> accent; the image PATH -> dim (legible); its marker -> faint
        # (a quiet prefix); everything else -> ink.
        ink_map = {"code": t.accent, "path": t.dim, "imark": t.faint}
        mi.draw_run_glyphs(canvas, vline.runs, x0i, base, base_size,
                           lambda kind: ink_map.get(kind, t.ink))
        return line_text, bounds

    # -- text wrapping ---------------------------------------------------------

    def _wrap(self, canvas: Canvas, text: str, max_w: float) -> List[str]:
        return wrap_text(canvas, text, max_w)

    @staticmethod
    def _hard_break(canvas: Canvas, token: str, max_w: float) -> List[str]:
        return hard_break(canvas, token, max_w)

    @staticmethod
    def _mix(c: Tuple[int, int, int], bg: Tuple[int, int, int],
             frac: float) -> Tuple[int, int, int]:
        return tuple(int(a + (b - a) * frac) for a, b in zip(c, bg))


# ---------------------------------------------------------------------------
# Shared wrapping helpers — extracted so other console surfaces (the
# Artifacts summary column) wrap by REFERENCE to the same bytes, never a
# fork.  Behavior is byte-identical to the historical _wrap/_hard_break.
# ---------------------------------------------------------------------------

def _x_gap(bounds: List[float], x: float) -> float:
    """0 when *x* falls within a recorded line's x-span [bounds[0], bounds[-1]],
    else the distance to the nearest end.  The tie-break that lets same-row
    table cells hit-test by column ([CXC-ICV-SELECT]); degenerate to 0 for the
    single-line-per-band prose case."""
    if not bounds:
        return abs(x)
    lo, hi = bounds[0], bounds[-1]
    if x < lo:
        return lo - x
    if x > hi:
        return x - hi
    return 0.0


def wrap_text(canvas: Canvas, text: str, max_w: float) -> List[str]:
    """Greedy word-wrap to *max_w* using the CURRENT font's measure_text.

    Preserves explicit newlines; hard-breaks any single token wider than
    the column so a URL / unbroken string can never overflow the panel.
    """
    out: List[str] = []
    for para in text.replace("\r", "").split("\n"):
        if para == "":
            out.append("")
            continue
        words = para.split(" ")
        line = ""
        for word in words:
            candidate = word if not line else line + " " + word
            if canvas.measure_text(candidate).width <= max_w or not line:
                # fits, OR it's the first token on an empty line: accept it
                # then hard-break below if the lone token is still too wide.
                if not line and canvas.measure_text(word).width > max_w:
                    pieces = hard_break(canvas, word, max_w)
                    out.extend(pieces[:-1])
                    line = pieces[-1]
                else:
                    line = candidate
            else:
                out.append(line)
                if canvas.measure_text(word).width > max_w:
                    pieces = hard_break(canvas, word, max_w)
                    out.extend(pieces[:-1])
                    line = pieces[-1]
                else:
                    line = word
        out.append(line)
    return out or [""]


def hard_break(canvas: Canvas, token: str, max_w: float) -> List[str]:
    """Break a single over-wide token by characters (min one char/line)."""
    pieces: List[str] = []
    cur = ""
    for ch in token:
        trial = cur + ch
        if cur and canvas.measure_text(trial).width > max_w:
            pieces.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        pieces.append(cur)
    return pieces or [token]
