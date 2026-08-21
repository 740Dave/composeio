"""
ComposeUX SubagentLedgerWidget — the animating dispatch ledger ([SPEC:CXC-IC]).

A sub-widget of the Intelligence Console that lights up on subagent semantic
events.  Each dispatched subagent is a ROW keyed by ``id``; when its matching
``done`` arrives the SAME row updates in place (never a second line) — the
"animating ledger" the human described.

Namespace (ONE): ``self/subagents``.

Payload {type, model, desc, status:"dispatched"|"done", id, tokens, dur_s, ts}:
    status=="dispatched" -> a running row (accent, "›" caret):
        "› general-purpose [opus] · Pre-vis Instrument · running"
    status=="done"       -> the same row (by ``id``) resolves (ok green, "✓"),
        gaining its duration + token cost:
        "✓ general-purpose [opus] · Pre-vis Instrument · 4m48s · 72k"

(The mock's "▸" is notdef in the bundled IBM Plex face, so the caret is "›"
(U+203A); the done marker "✓" (U+2713) renders natively.)

Rows are held in a dict keyed by ``id`` (NOT an append-only log), capped at
``max_rows`` (default 8) dropping the oldest.  Newest activity renders at the
TOP.  A ``done`` with no prior ``dispatched`` still creates a resolved row.

All drawing is Canvas-contract only ([CXC-R-DRAW-FONT]); ``measure_text``
drives ellipsis truncation so text is identical on every backend.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style
from . import console_theme as ct
from .activity import _coerce_payload, _ellipsize, format_dur

# -- instrument palette defaults ([CXC-IC]) ----------------------------------
_ACCENT = (0x4E, 0xA8, 0xFF)    # running subagent
_DIM = (0x6D, 0x7C, 0x92)       # secondary text
_OK = (0x2F, 0xD0, 0x7B)        # done / resolved
_BG = (0x0D, 0x12, 0x1B)
_BORDER = (0x1B, 0x24, 0x32)
_HEADER = (0x8F, 0xA0, 0xB4)

DEFAULT_NAMESPACE = "self/subagents"

_DESC_MAX = 40   # chars before desc is truncated (measure_text does the fit)

# [CXC-IC-SUBAGENT-LIFECYCLE] linger-then-remove ([CXC-R-DAMAGE]): a row that
# goes ``done`` stays visibly resolved (✓ + duration/tokens) for LINGER_S
# seconds, then is REMOVED so the live ledger shows running rows + only very-
# recently-completed ones — never the unbounded pile a long session used to grow.
#
# PRUNE RULE — THREE bounds, the ONE shared implementation ``prune_subagent_rows``
# every widget rendering ``self/subagents`` reuses by reference ([DX-NARROW-WAIST];
# see [CXC-IC-SUBAGENT-LIFECYCLE]).  Constants below are its defaults:
#   time  — a done row removed once ``now - done_at`` exceeds ``linger_s`` (6s);
#   count — at most ``done_keep`` (2) done rows are kept, the most recent by
#           ``done_at``; a burst of completions can never stack;
#   stale — a row still ``running`` whose last update (``seen``) is older than
#           ``stale_s`` (30min — a dropped 'done' event or a scene seed) is
#           swept so a phantom running row can never wedge forever.
# Non-stale running rows are ALWAYS kept.
#
# The rule is driven from TWO seams so it fires without a busy loop:
#   * on_bus_state — every incoming event prunes first (correct even if the
#     clock were paused: a new dispatch/done re-evaluates the pile immediately);
#   * on_clock_tick — the widget is already a clocked widget (runpulse), and the
#     runtime's run() loop ticks clocked widgets EVERY frame, so a linger that
#     expires with no further bus traffic is still pruned + repainted on the
#     next tick.  Removing a row returns True so that frame renders.
_LINGER_S = 6.0
_DONE_KEEP = 2
_STALE_S = 1800.0   # 30 min — the running-staleness backstop


def prune_subagent_rows(rows: "OrderedDict[str, Any]", now: float, *,
                        linger_s: float, done_keep: int,
                        stale_s: float) -> set:
    """Bound a ``self/subagents`` chip ledger IN PLACE ([CXC-IC-SUBAGENT-LIFECYCLE]).

    The ONE prune every widget rendering ``self/subagents`` reuses by reference
    ([DX-NARROW-WAIST]) — ``SubagentLedgerWidget`` (the two-line ledger) and
    ``IntelligenceCardWidget`` (the consolidated rail chips) both CALL this, so
    the "done chips MUST disappear" invariant can never silently fork again (it
    recurred as a bug precisely because it was forked, and a fix to one copy
    missed the other).

    Each item must duck-type three attrs: ``running`` (bool), ``done_at``
    (Optional[float] — monotonic stamp of the running->done transition, None
    while running), and ``seen`` (float — monotonic stamp of the last update).

    Three bounds, applied in order:
      * stale — a still-``running`` item whose ``seen`` is older than
        ``stale_s`` is swept (a dropped 'done' event or a scene seed can't
        wedge a permanent phantom running item);
      * time (linger) — a done item is removed once ``now - done_at`` exceeds
        ``linger_s``;
      * count — at most ``done_keep`` done items survive, the most recent by
        ``done_at`` (a burst of completions can never stack).
    Non-stale running items are otherwise always kept.  Removed ids are popped
    from ``rows`` and returned (an empty set means nothing changed, so a caller
    can ``return bool(prune_subagent_rows(...))`` to damage only on a change.)
    """
    remove: set = set()
    # stale-running backstop
    for rid, r in rows.items():
        if r.running and (now - r.seen) > stale_s:
            remove.add(rid)
    done = [(rid, r) for rid, r in rows.items()
            if not r.running and rid not in remove]
    # time bound: linger elapsed
    for rid, r in done:
        if r.done_at is not None and (now - r.done_at) > linger_s:
            remove.add(rid)
    # count bound: keep only the most-recent ``done_keep`` survivors
    survivors = [(rid, r) for rid, r in done if rid not in remove]
    if len(survivors) > done_keep:
        survivors.sort(key=lambda kv: kv[1].done_at or 0.0)
        for rid, _r in survivors[:len(survivors) - done_keep]:
            remove.add(rid)
    for rid in remove:
        rows.pop(rid, None)
    return remove


def format_tokens(tokens: Any) -> str:
    """Compact token count: 72000 -> "72k", 1500 -> "1.5k", 800 -> "800".
    None/garbage -> ""."""
    try:
        n = float(tokens)
    except (TypeError, ValueError):
        return ""
    if n < 0:
        return ""
    if n >= 1000:
        k = n / 1000.0
        if k >= 10:
            return f"{k:.0f}k"
        # 1.5k but drop a trailing .0 (2.0k -> "2k")
        s = f"{k:.1f}"
        if s.endswith(".0"):
            s = s[:-2]
        return f"{s}k"
    return f"{int(round(n))}"


@dataclass
class _Row:
    """One subagent row, keyed by id, updated in place on done."""
    id: str
    type: str
    model: str
    desc: str
    running: bool          # True after dispatched, False after done
    dur: str               # formatted duration, set on done
    tokens: str            # formatted token count, set on done
    # [CXC-ICV-SUBAGENTS] additive: dispatch stamp so a running row shows
    # its elapsed time before the done payload delivers dur_s.
    ts: float = field(default_factory=time.monotonic)
    # monotonic stamp of the done transition — drives the linger-then-remove
    # prune ([CXC-IC-SUBAGENT-LIFECYCLE]).  None while running.
    done_at: Optional[float] = None
    # monotonic stamp of the last update; feeds the running-staleness backstop
    # in the shared ``prune_subagent_rows`` (duck-typed ``seen``, matching
    # ``intelligence_card._Chip``) so a dropped 'done'/seed can't wedge a
    # permanent phantom running row.
    seen: float = field(default_factory=time.monotonic)


class SubagentLedgerWidget(Widget):
    """In-place-updating ledger of dispatched subagents ([SPEC:CXC-IC])."""

    def __init__(self, widget_id: str, *,
                 label: str = "Subagents",
                 namespace: str = DEFAULT_NAMESPACE,
                 max_rows: int = 8,
                 font_size: float = 12.0,
                 accent: Tuple[int, int, int] = _ACCENT,
                 dim: Tuple[int, int, int] = _DIM,
                 ok: Tuple[int, int, int] = _OK,
                 background: Tuple[int, int, int] = _BG,
                 border: Tuple[int, int, int] = _BORDER,
                 corner_radius: float = 6.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "subagent_ledger", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.namespace = str(namespace)
        self.max_rows = max(1, int(max_rows))
        self.font_size = float(font_size)
        self.accent = tuple(accent)
        self.dim = tuple(dim)
        self.ok = tuple(ok)
        self.background = tuple(background)
        self.border = tuple(border)
        self.corner_radius = float(corner_radius)
        # linger-then-remove tuning ([CXC-IC-SUBAGENT-LIFECYCLE]); instance attrs
        # so a scene/test can override the window + count + staleness bounds.
        self.linger_s = float(_LINGER_S)
        self.done_keep = int(_DONE_KEEP)
        self.stale_s = float(_STALE_S)
        # id -> _Row; insertion order = dispatch order (newest last).
        self._rows: "OrderedDict[str, _Row]" = OrderedDict()
        self._auto = 0

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce_payload(value)
        if payload is None:
            return
        rtype = str(payload.get("type") or "").strip()
        model = str(payload.get("model") or "").strip()
        desc = str(payload.get("desc") or "").strip()
        status = str(payload.get("status") or "dispatched").strip().lower()
        rid = payload.get("id")
        if rid is None or str(rid) == "":
            self._auto += 1
            rid = f"_auto{self._auto}"
        rid = str(rid)

        now = time.monotonic()
        existing = self._rows.get(rid)
        if status == "done":
            dur = format_dur(payload.get("dur_s"))
            tokens = format_tokens(payload.get("tokens"))
            if existing is not None:
                # UPDATE THE SAME ROW in place (never append a second line)
                existing.running = False
                existing.done_at = now
                existing.dur = dur
                existing.tokens = tokens
                if rtype:
                    existing.type = rtype
                if model:
                    existing.model = model
                if desc:
                    existing.desc = desc
            else:
                self._rows[rid] = _Row(rid, rtype, model, desc, False,
                                       dur, tokens, done_at=now)
        else:
            # dispatched (default): a running row
            if existing is not None:
                existing.running = True
                existing.done_at = None    # re-running: cancel any linger
                existing.dur = ""
                existing.tokens = ""
                if rtype:
                    existing.type = rtype
                if model:
                    existing.model = model
                if desc:
                    existing.desc = desc
            else:
                self._rows[rid] = _Row(rid, rtype, model, desc, True, "", "")

        # stamp last-seen on the touched row so the shared prune's running-
        # staleness backstop measures from the latest event, not creation.
        self._rows[rid].seen = now
        # prune expired/surplus done rows on EACH incoming event (the seam
        # that keeps the ledger compact even if the clock never ticks again).
        self._prune(now)
        while len(self._rows) > self.max_rows:
            self._rows.popitem(last=False)
        self.request_redraw()

    def _prune(self, now: float) -> bool:
        """Bound the ledger via the ONE shared prune ([CXC-IC-SUBAGENT-LIFECYCLE],
        ``prune_subagent_rows``): done rows past the linger window OR beyond the
        most-recent ``done_keep``, plus a running-staleness backstop.  Non-stale
        running rows are always kept.  Returns True when any row was removed (so
        a clock tick can repaint that frame)."""
        return bool(prune_subagent_rows(
            self._rows, now, linger_s=self.linger_s,
            done_keep=self.done_keep, stale_s=self.stale_s))

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(360.0, constraints.max_width),
                    min(220.0, constraints.max_height))

    def _line_text(self, row: _Row) -> Tuple[str, tuple]:
        """(text, color) for a row.
        running: "▸ general-purpose [opus] · Pre-vis Instrument · running"
        done:    "✓ general-purpose [opus] · Pre-vis Instrument · 4m48s · 72k"
        """
        head = row.type or "subagent"
        if row.model:
            head = f"{head} [{row.model}]"
        parts = [head]
        desc = _truncate_chars(row.desc, _DESC_MAX)
        if desc:
            parts.append(desc)
        if row.running:
            parts.append("running")
            return "› " + " · ".join(parts), self.accent
        tail = [t for t in (row.dur, row.tokens) if t]
        parts.extend(tail)
        return "✓ " + " · ".join(parts), self.ok

    # -- motion ([CXC-ICV-MOTION] runpulse) -------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        if ctx is not None and hasattr(ctx, "register_clocked"):
            ctx.register_clocked(self)

    def on_unmount(self) -> None:
        if self._ctx is not None and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        """runpulse: the running dot pulses (period 1.6s, 1.0 -> 0.35);
        rest frame + no redraw under reduced motion or with no runner.

        Also the linger-expiry seam ([CXC-ICV-SUBAGENTS]): the run() loop ticks
        clocked widgets every frame, so a done row whose linger has elapsed with
        no further bus traffic is pruned HERE and the frame repaints."""
        pruned = self._prune(now)
        if pruned:
            self.request_redraw()
        any_running = any(r.running for r in self._rows.values())
        if ct.reduced_motion(self._ctx) or not any_running:
            changed = self._dot_opacity != 1.0
            self._dot_opacity = 1.0
            if changed:
                self.request_redraw()
            return pruned
        self._dot_opacity = ct.pulse(now, ct.RUNPULSE_PERIOD, ct.RUNPULSE_LOW)
        return True

    _dot_opacity: float = 1.0

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        """[CXC-ICV-SUBAGENTS] the pre-vis .subs TWO-LINE row: line 1 =
        status dot (ok + halo when running, faint when done) + type in
        accent + [model] faint; line 2 (indented 15px) = task in ink +
        dur dim + the flag word."""
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                     ct.RADIUS_CARD, t)
        body_y = ct.draw_card_header(canvas, rect.x, rect.y, rect.w,
                                     self.label or "self / subagents",
                                     self.namespace, t=t)

        pad = ct.PAD_CARD_BODY
        box_x = rect.x + pad
        content_w = max(20.0, rect.w - 2 * pad)
        fsize = max(9.0, self.font_size * 0.82)
        line_h = fsize * 1.55
        block_h = 2 * line_h + 8.0
        indent = 15.0

        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h,
                               ct.RADIUS_CARD)
        rows = list(self._rows.values())
        ly = body_y + 8.0
        for row in reversed(rows):     # newest at TOP
            if ly + block_h > rect.y + rect.h - 4:
                break
            # line 1: status dot + type [model]
            cy1 = ly + line_h / 2.0
            if row.running:
                canvas.set_opacity(self._dot_opacity)
                ct.draw_status_dot(canvas, box_x + 4, cy1, 4.0, t.ok,
                                   halo=t.ok_dim)
                canvas.set_opacity(1.0)
            else:
                ct.draw_status_dot(canvas, box_x + 4, cy1, 4.0, t.faint)
            canvas.set_font(style.FONT_MONO, fsize, "regular")
            base1 = style.baseline_for(canvas, cy1)
            tx = box_x + indent
            head = row.type or "subagent"
            canvas.set_fill_color(*t.accent)
            head = _ellipsize(canvas, head, content_w - indent)
            canvas.draw_text(head, tx, base1, "left")
            hw = canvas.measure_text(head).width
            if row.model and hw < content_w - indent - 20:
                canvas.set_fill_color(*t.faint)
                canvas.draw_text(f" [{row.model}]", tx + hw, base1, "left")
            # line 2 (indented): task · dur · flag
            cy2 = ly + line_h + line_h / 2.0
            base2 = style.baseline_for(canvas, cy2)
            x2 = box_x + indent
            flag = "· running" if row.running else "· done"
            dur = row.dur
            if row.running and not dur:
                dur = format_dur(time.monotonic() - row.ts)
            tail_w = canvas.measure_text(f" {dur} {flag}").width
            task = _truncate_chars(row.desc, _DESC_MAX)
            canvas.set_font(style.FONT_MONO, fsize, "medium")
            task = _ellipsize(canvas, task,
                              max(20.0, content_w - indent - tail_w - 8))
            canvas.set_fill_color(*t.ink)
            canvas.draw_text(task, x2, base2, "left")
            x2 += canvas.measure_text(task).width + 8
            canvas.set_font(style.FONT_MONO, fsize, "regular")
            if dur:
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(dur, x2, base2, "left")
                x2 += canvas.measure_text(dur).width + 8
            canvas.set_fill_color(*(t.ok if row.running else t.dim))
            canvas.draw_text(flag, x2, base2, "left")
            ly += block_h
        canvas.restore()

        if not self._rows:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_MONO, 9.5, "regular")
            canvas.draw_text("no subagents dispatched",
                             rect.x + rect.w / 2,
                             (body_y + rect.y + rect.h) / 2, "center")
        canvas.restore()


def _truncate_chars(text: str, limit: int) -> str:
    """Character-cap a desc before the pixel ellipsize; keeps the row terse."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"
