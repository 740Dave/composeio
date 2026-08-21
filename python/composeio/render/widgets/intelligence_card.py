"""
ComposeUX IntelligenceCardWidget — the consolidated "Intelligence" rail card
([DEC-INTELLIGENCE-WIDGET-CONSOLIDATION], previs
``docs/intelligence-console/previs/intelligence-consolidated.html`` rev 5).

ONE card replaces five: it carries the context gauge (hero), a Model row with
a Change affordance, a collapsed Registered row, a Usage row, and per-subagent
chip cards.  A **presentation** consolidation — every value is already on the
bus; no new sensor.

Reuse discipline ([CXC-R-DP-1] decompose to the contract; R3 reuse-by-reference):
  * the gauge ARC is the shipping ``GaugeWidget`` (style=semicircle,
    chrome=bare) driven verbatim — David rejected redesigning the gauge twice,
    so bare only GATES the surrounding decoration, the arc is byte-identical;
  * registered parsing reuses ``RegisteredChecklistWidget._coerce_items``;
  * subagent payload parsing reuses ``activity._coerce_payload`` +
    ``format_dur`` and ``subagent_ledger.format_tokens``.

Namespaces (all pre-declared, all already live):
    self/context_pct    -> the gauge arc + big %
    self/compactions    -> the `· #N` in the title
    self/effort         -> the Model row effort word
    self/registered     -> the Registered row (collapsed ✓N/N; expands when
                           NOT all-ok, reusing the checklist item logic)
    self/output_tokens  -> the Usage row
    self/subagents      -> one chip per subagent (running pulses; done dims)
    self/memory         -> the slim automemory-footprint bar seated ABOVE the
                           context gauge + its % readout ([DEC-MEMORY-BLOAT-
                           MONITOR]; payload {bytes, files, entries,
                           budget_bytes, pct, status∈ok/warn/bloated})

The Change chip publishes to ``model_pick_namespace`` (opens the model/effort
picker); it does NOT actuate.  Damage discipline: ``self.request_redraw()``
(no args) only.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style
from .gauge import GaugeWidget
from .registered_checklist import RegisteredChecklistWidget
from .activity import _coerce_payload, format_dur
from .subagent_ledger import format_tokens, prune_subagent_rows

DEFAULT_MODEL_PICK_NS = "_app/model_picker"

PAD = 14.0
HEAD_H = 34.0
MEM_H = 26.0              # the slim automemory bar strip, seated between the
# header and the context-gauge hero so the footprint reads ABOVE the % readout
# ([DEC-MEMORY-BLOAT-MONITOR]).  One line: MEMORY label · track+fill · pct.
GAUGE_H = 200.0            # bare gauge block (arc + big %) from body top.
# 172 -> 200: the shipping readout draws the big % BELOW the arc (the look
# David asked to retain), and at 172 its baseline landed on the MODEL row —
# a text-over-text collision caught on the glass, not by the green suite.
# The extra 28px seats the full readout before the first KV row.
ROW_H = 30.0              # a KV row (Model / Registered / Usage)
KEY_W = 82.0             # the tracked-mono key column width (previs .kv .k)
REG_ITEM_H = 18.0        # an expanded registered item row
SUB_CAP_H = 22.0         # the "N running" caption
CHIP_H = 60.0
CHIP_GAP = 7.0
PAD_BOTTOM = 12.0

# [CXC-IC-SUBAGENT-LIFECYCLE] linger-then-remove for the CHIPS the console
# actually shows — the SHARED ``subagent_ledger.prune_subagent_rows`` runs on its
# chips (reuse by reference, [DX-NARROW-WAIST]), so done chips DISAPPEAR (they
# used to only dim, so a long session piled them until they buried the console)
# and the bound can never fork from the ledger's again.  Constants match
# subagent_ledger's defaults:
#   linger — a done chip stays visibly resolved for _CHIP_LINGER_S then is
#            removed once ``now - done_at`` exceeds it;
#   keep   — at most _CHIP_DONE_KEEP done chips survive, the most recent by
#            done_at (a burst of completions can never stack);
#   stale  — a running-staleness BACKSTOP: a chip still ``running`` whose last
#            update (``seen``) is older than _CHIP_STALE_S (well beyond any real
#            subagent) is removed so a dropped 'done' event or a seed can't wedge
#            a permanent phantom running chip.
_CHIP_LINGER_S = 6.0
_CHIP_DONE_KEEP = 2
_CHIP_STALE_S = 1800.0   # 30 min


def _fmt_tokens_out(value: Any) -> str:
    """63200 / {"total": 63200} -> "63.2k tokens out"; millions -> "2.381M
    tokens out"; missing -> ""."""
    if isinstance(value, dict):
        value = value.get("total", value.get("value", value.get("tokens")))
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.3f}M tokens out"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k tokens out"
    return f"{int(round(n))} tokens out"


def _pluralize_kind(kind: str) -> str:
    return kind if kind.endswith("s") else kind + "s"


def _registered_caption(kinds: List[Optional[str]]) -> str:
    """The REGISTERED row's collapsed caption — MANIFEST-DRIVEN, not hardcoded
    ([CXC-IC-SELF]).  Group the per-artifact KINDS in manifest order, collapsing
    a run of the same kind into "N <plural>":

        [role def, bootstrap-read, dusk gift, dusk gift, dusk gift]
            -> "role def + bootstrap-read + 3 dusk gifts"

    So the caption ADAPTS as the manifest grows (add toolbox -> it appears; add a
    4th gift -> "4 dusk gifts") with no code change.  This replaces the baked-in
    "role def + N dusk gifts" that silently misdescribed the manifest the moment
    it grew past role-descriptor-plus-gifts.  When no kinds are carried (a legacy
    payload), fall back to an honest, dynamic "N artifacts" — never the old
    false role-def-plus-gifts assumption."""
    labels = [k for k in (kinds or []) if k]
    if not labels:
        n = len(kinds or [])
        return f"{n} artifacts" if n else ""
    parts: List[str] = []
    i = 0
    while i < len(labels):
        j = i
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        run = j - i
        parts.append(labels[i] if run == 1
                     else f"{run} {_pluralize_kind(labels[i])}")
        i = j
    return " + ".join(parts)


class _Chip:
    """One subagent chip, keyed by id, updated in place on done."""

    __slots__ = ("id", "type", "model", "desc", "running", "elapsed",
                 "done_at", "seen")

    def __init__(self, cid: str, ctype: str, model: str, desc: str,
                 running: bool, elapsed: str,
                 done_at: Optional[float] = None,
                 seen: float = 0.0) -> None:
        self.id = cid
        self.type = ctype
        self.model = model
        self.desc = desc
        self.running = running
        # monotonic stamp of the running->done transition (None while
        # running); drives the linger-then-remove prune ([CXC-ICV-SUBAGENTS],
        # mirrored from subagent_ledger._prune).
        self.done_at = done_at
        # monotonic stamp of the last update; feeds the running-staleness
        # backstop so a dropped-'done'/seed can't wedge as a permanent phantom.
        self.seen = seen
        self.elapsed = elapsed


class IntelligenceCardWidget(Widget):
    """The consolidated Intelligence rail card ([DEC-INTELLIGENCE-WIDGET-
    CONSOLIDATION])."""

    def __init__(self, widget_id: str, *,
                 label: str = "ComposeIO-Architect",
                 model_name: str = "Opus 4.8",
                 context_namespace: str = "self/context_pct",
                 model_pick_namespace: str = DEFAULT_MODEL_PICK_NS,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "intelligence_card", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.model_name = str(model_name)
        self.context_namespace = str(context_namespace)
        self.model_pick_namespace = str(model_pick_namespace
                                        or DEFAULT_MODEL_PICK_NS)
        self.interactive = True

        # the shipping gauge, reused by reference (arc byte-identical);
        # chrome=bare so THIS card owns the chrome/header.  _ctx stays None so
        # its own request_redraw is a no-op — the card requests the redraw.
        self._gauge = GaugeWidget(
            widget_id + "__gauge", style="semicircle", chrome="bare",
            min_val=0, max_val=100, value=45, unit="%",
            warning_threshold=65, mid_threshold=80, danger_threshold=90)

        self._compactions = 0
        self._effort = ""
        # [DEC-MEMORY-BLOAT-MONITOR] automemory footprint off self/memory.
        self._mem_pct: Optional[float] = None
        self._mem_status: str = ""
        self._items: List[Tuple[str, str, bool]] = []
        # per-artifact KIND (role def / bootstrap-read / dusk gift…), parallel to
        # _items, feeding the manifest-driven caption ([CXC-IC-SELF]).
        self._item_kinds: List[Optional[str]] = []
        self._usage: str = ""
        self._chips: "OrderedDict[str, _Chip]" = OrderedDict()
        self._reg_expanded = False
        # hit rects recorded by draw()
        self._change_rect: Optional[Tuple[float, float, float, float]] = None
        self._reg_rect: Optional[Tuple[float, float, float, float]] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        tail = namespace.rsplit("/", 1)[-1].lower()
        if namespace == self.context_namespace or tail == "context_pct":
            self._gauge.on_bus_state(namespace, value)
        elif tail == "compactions":
            cnt = value.get("count") if isinstance(value, dict) else value
            try:
                self._compactions = int(cnt)
            except (TypeError, ValueError):
                pass
        elif tail == "effort":
            lvl = value.get("level", value.get("value")) \
                if isinstance(value, dict) else value
            if lvl is not None:
                self._effort = str(lvl)
        elif tail == "registered":
            rows = RegisteredChecklistWidget._coerce_rows(value)
            if rows is not None:
                self._items = [(r["name"], r["path"], r["ok"]) for r in rows]
                self._item_kinds = [r.get("kind") for r in rows]
        elif tail == "output_tokens":
            self._usage = _fmt_tokens_out(value)
        elif tail == "subagents":
            self._ingest_subagent(value)
        elif tail == "memory":
            self._ingest_memory(value)
        self.request_redraw()

    def _ingest_memory(self, value: Any) -> None:
        """self/memory -> pct + status.  Dict {pct, status, ...} is the daemon
        payload; a bare number takes the pct alone; a JSON string is coerced
        (self_ledger idiom).  Missing/garbage keeps the prior value."""
        payload = value
        if isinstance(payload, str):
            s = payload.strip()
            if s and s[0] in "{[":
                try:
                    payload = json.loads(s)
                except (ValueError, TypeError):
                    payload = value
        if isinstance(payload, dict):
            try:
                self._mem_pct = float(payload.get("pct"))
            except (TypeError, ValueError):
                pass
            st = payload.get("status")
            if st is not None:
                self._mem_status = str(st).strip().lower()
        else:
            try:
                self._mem_pct = float(payload)
            except (TypeError, ValueError):
                pass

    def _ingest_subagent(self, value: Any) -> None:
        payload = _coerce_payload(value)
        if not isinstance(payload, dict):
            return
        cid = str(payload.get("id") or f"_{len(self._chips)}")
        ctype = str(payload.get("type") or "subagent").strip()
        model = str(payload.get("model") or "").strip()
        desc = str(payload.get("desc") or "").strip()
        status = str(payload.get("status") or "dispatched").strip().lower()
        running = status != "done"
        elapsed = format_dur(payload.get("dur_s") or payload.get("el"))
        now = time.monotonic()
        existing = self._chips.get(cid)
        if existing is not None:
            # done_at stamps the FIRST running->done transition (linger counts
            # from first done); a re-dispatch clears it; a repeated done keeps
            # the original stamp.
            if not running and existing.running:
                existing.done_at = now
            elif running:
                existing.done_at = None
            existing.running = running
            existing.seen = now
            if ctype:
                existing.type = ctype
            if model:
                existing.model = model
            if desc:
                existing.desc = desc
            if elapsed:
                existing.elapsed = elapsed
        else:
            self._chips[cid] = _Chip(cid, ctype, model, desc, running,
                                     elapsed,
                                     done_at=None if running else now,
                                     seen=now)
        self._prune_chips(now)

    def _prune_chips(self, now: float) -> bool:
        """Bound the chip ledger via the ONE shared prune
        ([CXC-IC-SUBAGENT-LIFECYCLE], ``subagent_ledger.prune_subagent_rows`` —
        reused by reference, NOT a second copy): done chips past the linger
        window OR beyond the most-recent ``_CHIP_DONE_KEEP``, PLUS a running-
        staleness backstop for a chip whose last update is older than
        ``_CHIP_STALE_S`` (a dropped 'done'/seed).  Returns True when any chip
        was removed (so a clock tick can repaint that frame)."""
        return bool(prune_subagent_rows(
            self._chips, now, linger_s=_CHIP_LINGER_S,
            done_keep=_CHIP_DONE_KEEP, stale_s=_CHIP_STALE_S))

    # -- derivation --------------------------------------------------------------

    def _mem_color(self, t) -> Tuple[int, int, int, int]:
        """status -> hue: bloated=crit(red), warn=amber, ok/else=green
        ([DEC-MEMORY-BLOAT-MONITOR])."""
        if self._mem_status == "bloated":
            return t.crit
        if self._mem_status == "warn":
            return t.warn
        return t.ok

    def _reg_state(self):
        n_ok = sum(1 for _, _, ok in self._items if ok)
        total = len(self._items)
        all_ok = total > 0 and n_ok == total
        expanded = self._reg_expanded or (total > 0 and not all_ok)
        return n_ok, total, all_ok, expanded

    # -- lifecycle -------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        # clocked so the chip linger expires even when the bus is quiet: the
        # run() loop ticks clocked widgets every frame ([CXC-ICV-SUBAGENTS],
        # same seam subagent_ledger uses).
        if ctx is not None and hasattr(ctx, "register_clocked"):
            ctx.register_clocked(self)

    def on_unmount(self) -> None:
        if self._ctx is not None and hasattr(self._ctx, "unregister_clocked"):
            self._ctx.unregister_clocked(self)
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        """Linger-expiry seam: a done chip whose linger has elapsed with no
        further bus traffic is pruned HERE and the frame repaints.  Returns
        True only when a chip was removed (damages this card's own bounds)."""
        if self._prune_chips(now):
            self.request_redraw()
            return True
        return False

    # -- measure ---------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        _, _, _, expanded = self._reg_state()
        reg_extra = (len(self._items) * REG_ITEM_H) if expanded else 0.0
        n = len(self._chips)
        chips_h = n * CHIP_H + max(0, n - 1) * CHIP_GAP if n else 0.0
        need = (HEAD_H + MEM_H + GAUGE_H + 3 * ROW_H + reg_extra
                + SUB_CAP_H + chips_h + PAD_BOTTOM)
        return Size(min(332.0, constraints.max_width),
                    min(constraints.max_height, need))

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD, t)
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_CARD)

        # ── header: tracked UPPERCASE mono, ink, no dot, "· #N" count ──
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        hm = canvas.measure_text("Ag")
        base = rect.y + 13.0 + hm.ascent
        title = self.label.upper()
        if self._compactions:
            title = f"{title} · #{self._compactions}"
        ct.draw_tracked(canvas, title, rect.x + PAD, base, ct.TRACK_LABEL,
                        t.ink)

        # ── automemory footprint: slim horizontal bar, ABOVE the % readout ──
        self._draw_memory(canvas, rect, rect.y + HEAD_H, t)

        # ── the gauge hero (arc + big %, chrome=bare) ──
        gy = rect.y + HEAD_H + MEM_H - 10.0
        gauge_rect = Rect(rect.x, gy, rect.w, GAUGE_H)
        self._gauge.arranged_rect = gauge_rect
        self._gauge.draw(canvas, gauge_rect)

        y = rect.y + HEAD_H + MEM_H + GAUGE_H

        # ── KV rows: Model · Registered · Usage ──
        y = self._draw_model_row(canvas, rect, y, t)
        y = self._draw_registered_row(canvas, rect, y, t)
        y = self._draw_usage_row(canvas, rect, y, t)

        # ── subagent chips ──
        y = self._draw_subagents(canvas, rect, y, t)

        # (activity strip removed 2026-07-21, David: it duplicated what the thread
        # already shows.  self/activity is no longer consumed by this card.)

        canvas.restore()   # clip
        canvas.restore()

    def _draw_memory(self, canvas, rect, y, t) -> None:
        """The automemory-footprint bar ([DEC-MEMORY-BLOAT-MONITOR]): one slim
        horizontal line — MEMORY key (tracked faint, the card's key idiom) ·
        track+fill (status hue) · pct (status hue, bold), right-aligned.  Seated
        above the context gauge so the footprint is glanceable alongside the %
        context readout.  Mirrors the account_card rate-limit bar, compressed."""
        col = self._mem_color(t)
        has = self._mem_pct is not None
        cy = y + MEM_H / 2.0
        # MEMORY key — same tracked-faint mono the KV keys use.
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        lm = canvas.measure_text("Ag")
        lbase = cy + (lm.ascent - lm.descent) / 2.0
        lw = ct.draw_tracked(canvas, "MEMORY", rect.x + PAD, lbase, 0.7,
                             t.faint)
        # pct readout, right-aligned, status-colored bold ("—" until first pub).
        pct_txt = f"{self._mem_pct:.0f}%" if has else "—"
        canvas.set_font(ct.FONT_MONO, 11.0, "bold")
        pm = canvas.measure_text(pct_txt)
        px = rect.x + rect.w - PAD - pm.width
        canvas.set_fill_color(*(col if has else t.faint))
        canvas.draw_text(pct_txt, px, lbase, "left")
        # track + fill filling the gap between the key and the pct readout.
        bx = rect.x + PAD + lw + 10.0
        bw = px - 10.0 - bx
        if bw > 8.0:
            by = cy - 3.0
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(bx, by, bw, 6.0, 3.0)
            if has:
                frac = max(0.0, min(1.0, self._mem_pct / 100.0))
                canvas.set_fill_color(*col)
                canvas.fill_round_rect(bx, by, max(6.0, bw * frac), 6.0, 3.0)

    def _row_line(self, canvas, rect, y, t):
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(rect.x + PAD, y, rect.x + rect.w - PAD, y)

    def _key(self, canvas, rect, cy, text, t):
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        km = canvas.measure_text("Ag")
        ct.draw_tracked(canvas, text.upper(), rect.x + PAD,
                        cy + (km.ascent - km.descent) / 2.0, 0.7, t.faint)

    def _draw_model_row(self, canvas, rect, y, t) -> float:
        self._row_line(canvas, rect, y, t)
        cy = y + ROW_H / 2.0
        self._key(canvas, rect, cy, "MODEL", t)
        # [Change] chip, right-aligned (bordered mono, faint)
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        cm = canvas.measure_text("Change")
        ch_w, ch_h = cm.width + 16.0, 20.0
        ch_x = rect.x + rect.w - PAD - ch_w
        ch_y = cy - ch_h / 2.0
        canvas.set_stroke_color(*t.line2)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ch_x, ch_y, ch_w, ch_h, 6.0)
        canvas.set_fill_color(*t.dim)
        canvas.draw_text("Change", ch_x + 8,
                         cy + (cm.ascent - cm.descent) / 2.0, "left")
        self._change_rect = (ch_x - 4, ch_y - 4, ch_w + 8, ch_h + 8)
        # value: "Opus 4.8" ink + " · <effort> effort" dim
        val_x = rect.x + PAD + KEY_W
        canvas.set_font(style.FONT_UI, 13.0, "regular")
        vm = canvas.measure_text(self.model_name)
        vbase = cy + (vm.ascent - vm.descent) / 2.0
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(self.model_name, val_x, vbase, "left")
        if self._effort:
            suffix = f" · {self._effort} effort"
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(suffix, val_x + vm.width, vbase, "left")
        return y + ROW_H

    def _draw_registered_row(self, canvas, rect, y, t) -> float:
        self._row_line(canvas, rect, y, t)
        n_ok, total, all_ok, expanded = self._reg_state()
        cy = y + ROW_H / 2.0
        self._key(canvas, rect, cy, "REGISTERED", t)
        self._reg_rect = (rect.x + PAD, y, rect.w - 2 * PAD, ROW_H)
        val_x = rect.x + PAD + KEY_W
        # count segment: "✓ N / N" ok (all-ok) or "✗ N / N" crit (not)
        mark = "✓" if all_ok else "✗"
        col = t.ok if all_ok else t.crit
        count = f"{mark} {n_ok} / {total}" if total else "—"
        canvas.set_font(style.FONT_UI, 13.0, "regular")
        cm = canvas.measure_text(count)
        cbase = cy + (cm.ascent - cm.descent) / 2.0
        canvas.set_fill_color(*col)
        canvas.draw_text(count, val_x, cbase, "left")
        # collapsed caption (dim): MANIFEST-DRIVEN, composed from the per-artifact
        # kinds ([CXC-IC-SELF]) — e.g. "role def + bootstrap-read + 3 dusk gifts".
        # NOT a hardcoded "role def + N dusk gifts" (that misdescribed the manifest
        # the moment it grew past role-descriptor-plus-gifts).
        if not expanded and total:
            cap = _registered_caption(self._item_kinds)
            if cap:
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(" " + cap, val_x + cm.width, cbase, "left")
        y += ROW_H
        # expanded: the per-item checklist (ok dot + name), reuse item logic
        if expanded:
            for name, path, ok in self._items:
                iy = y
                icy = iy + REG_ITEM_H / 2.0
                if ok:
                    ct.draw_status_dot(canvas, rect.x + PAD + 4, icy, 3.5,
                                       t.ok, halo=t.ok_dim)
                else:
                    ct.draw_status_dot(canvas, rect.x + PAD + 4, icy, 3.5,
                                       t.faint)
                canvas.set_font(style.FONT_MONO, 9.5, "regular")
                nm_col = t.ink if ok else t.dim
                im = canvas.measure_text("Ag")
                nm = ct.ellipsize(canvas, name, rect.w - 2 * PAD - 18)
                canvas.set_fill_color(*nm_col)
                canvas.draw_text(nm, rect.x + PAD + 16,
                                 icy + (im.ascent - im.descent) / 2.0, "left")
                y += REG_ITEM_H
        return y

    def _draw_usage_row(self, canvas, rect, y, t) -> float:
        self._row_line(canvas, rect, y, t)
        cy = y + ROW_H / 2.0
        self._key(canvas, rect, cy, "USAGE", t)
        val_x = rect.x + PAD + KEY_W
        usage = self._usage or "—"
        # split "2.381M" (ink) from " tokens out" (dim) if present
        canvas.set_font(ct.FONT_MONO, 13.0, "regular")
        num, _, rest = usage.partition(" ")
        nm = canvas.measure_text(num)
        ubase = cy + (nm.ascent - nm.descent) / 2.0
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(num, val_x, ubase, "left")
        if rest:
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(" " + rest, val_x + nm.width, ubase, "left")
        return y + ROW_H

    def _draw_subagents(self, canvas, rect, y, t) -> float:
        n_run = sum(1 for c in self._chips.values() if c.running)
        # "N running" caption
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        capm = canvas.measure_text("Ag")
        cap = f"{n_run} running" if self._chips else "no subagents"
        ct.draw_tracked(canvas, cap.upper(), rect.x + PAD,
                        y + 6.0 + capm.ascent, 0.7, t.faint)
        y += SUB_CAP_H
        for chip in self._chips.values():
            self._draw_chip(canvas, rect, y, chip, t)
            y += CHIP_H + CHIP_GAP
        if self._chips:
            y -= CHIP_GAP
        return y

    def _draw_chip(self, canvas, rect, y, chip: _Chip, t) -> None:
        cx = rect.x + PAD
        cw = rect.w - 2 * PAD
        op = 0.55 if not chip.running else 1.0
        canvas.set_opacity(op)
        # chip panel
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(cx, y, cw, CHIP_H, ct.RADIUS_INNER)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(cx + 0.5, y + 0.5, cw - 1, CHIP_H - 1,
                                 ct.RADIUS_INNER)
        pad = 10.0
        ix = cx + pad
        # line 1: dot + type (azure) + elapsed (right)
        l1 = y + 9.0
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        tm = canvas.measure_text("Ag")
        cy1 = l1 + tm.ascent / 2.0
        if chip.running:
            ct.draw_status_dot(canvas, ix + 3.5, cy1, 3.5, t.accent,
                               halo=t.accent_dim)
        else:
            ct.draw_status_dot(canvas, ix + 3.5, cy1, 3.5, t.faint)
        el = chip.elapsed or ("running" if chip.running else "done")
        elm = canvas.measure_text(el)
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(el, cx + cw - pad - elm.width, l1 + tm.ascent, "left")
        ty = ix + 14.0
        canvas.set_fill_color(*t.accent)
        ctype = ct.ellipsize(canvas, chip.type,
                             cx + cw - pad - elm.width - 6 - ty)
        canvas.draw_text(ctype, ty, l1 + tm.ascent, "left")
        # line 2: description (ink-2/dim)
        canvas.set_font(style.FONT_UI, 12.5, "regular")
        dm = canvas.measure_text("Ag")
        dy = l1 + tm.ascent + tm.descent + 5.0
        canvas.set_fill_color(*t.dim)
        desc = ct.ellipsize(canvas, chip.desc, cw - 2 * pad)
        canvas.draw_text(desc, ix, dy + dm.ascent, "left")
        # line 3: model (uppercase mono faint); fable -> warn "· scarce"
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        mm = canvas.measure_text("Ag")
        my = dy + dm.ascent + dm.descent + 4.0
        model = chip.model.lower()
        if model == "fable":
            mx = ct.draw_tracked(canvas, "FABLE", ix, my + mm.ascent, 0.6,
                                 t.warn)
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(" · scarce", ix + mx, my + mm.ascent, "left")
        elif model:
            ct.draw_tracked(canvas, model.upper(), ix, my + mm.ascent, 0.6,
                            t.faint)
        canvas.set_opacity(1.0)

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        r = self._change_rect
        if r and r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
            self.publish(self.model_pick_namespace, "on")
            return
        rr = self._reg_rect
        if rr and rr[0] <= x <= rr[0] + rr[2] and rr[1] <= y <= rr[1] + rr[3]:
            self._reg_expanded = not self._reg_expanded
            self.request_redraw()
