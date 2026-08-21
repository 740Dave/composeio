"""
ComposeUX DeliverableHistoryWidget — the full ARTIFACTS view (the LOCKED
previs ``docs/intelligence-console/previs/deliverable-history.html``): an
OS-like filter/sort list over the deliverable ledger's bus projection.

  * head row — azure presence dot, "Deliverable History", the mono
    sub-line ``bus://deliverable-ledger · self/deliverables``,
  * toolbar — kind filter CHIPS with per-kind counts (previs .chip) + a
    LIVE search field (name/path/topic/summary, previs #q) — the search
    is a real console-chrome TextInput child reporting every edit (the
    composer LiveInput idiom by reference, never a re-implementation),
  * the count line — N shown · M total · enriched · pending,
  * the table — Kind badge · sortable Document (name + path, sorts by
    artifact NAME) · sortable Created / Modified (asc/desc, drawn-arrow
    indicator) · Access
    (confidentiality) · Summary (FIRST 256 CHARS — David's firm
    requirement — with the "N chars · showing 256" readout; rows the
    sweep stamped but the Intelligence hasn't enriched show the
    NEEDS-ENRICHMENT pill + their pending-field list) · Open ▸,
  * wheel scroll over the row region (41+ rows) with the thin
    chat_thread-idiom scrollbar.

DATA-DRIVEN off ONE namespace: ``self/deliverables`` (the
deliverable_publisher projection) — the view renders the payload's
``all`` list and NEVER reads the ledger.  A row's **Open ▸** publishes
``_app/deliverable/open`` = the row's path (the opener daemon actuates
it and stamps seen).  Every glyph-less affordance (▸, sort arrows) is a
drawn ``fill_path`` (bundled Plex has no U+25B8/25B2/25BC — no font
fallback forks, [CXC-R-DP-1]).

Damage discipline ([CXC-R-DAMAGE]): bus updates and every filter/sort/
search/scroll interaction call ``self.request_redraw()`` only — damage
stays this widget's own rect.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from ..engine.layout import Container
from . import console_theme as ct
from .chat_thread import wrap_text
from .deliverable_card import VIOLET, _coerce, _open_tri
from .text_input import TextInputWidget

DEFAULT_NAMESPACE = "self/deliverables"
DEFAULT_OPEN_NAMESPACE = "_app/deliverable/open"

SUMMARY_CLIP = 256          # David's one firm requirement

# previs chip order; kinds beyond these (schema-tolerant) append after.
KIND_ORDER = ("primer", "previs", "report", "brief", "research",
              "presentation")

# category hues beyond the compact card's four (previs .k-research /
# .k-presentation literals — category, not state).
RESEARCH = (0xFF, 0xB4, 0x54, 255)
PRESENTATION = (0xFF, 0x8F, 0xA8, 255)

# ── geometry (px) ──
HEAD_H = 36.0               # dot · title · sub-line
BAR_H = 46.0                # chips + search
COUNT_H = 22.0
THEAD_H = 38.0
PAD_X = 4.0                 # widget-edge inset
CELL_PAD = 12.0
ROW_MIN_H = 56.0
SUMMARY_LINE = 17.0         # 12.5px font line pitch (previs 1.4)
_SCROLL_STEP = 40.0

# fixed column widths (previs th widths, roomier dates for the mono face)
KIND_W = 104.0
CREATED_W = 108.0
MODIFIED_W = 108.0
ACCESS_W = 116.0
OPEN_W = 86.0


def _tri(canvas: Canvas, cx: float, cy: float, w: float, h: float,
         up: bool, color) -> None:
    """Sort-direction arrow as a drawn path (Plex has no U+25B2/25BC)."""
    canvas.set_fill_color(*color)
    if up:
        canvas.fill_path([MoveTo(cx - w / 2, cy + h / 2),
                          LineTo(cx + w / 2, cy + h / 2),
                          LineTo(cx, cy - h / 2), Close()])
    else:
        canvas.fill_path([MoveTo(cx - w / 2, cy - h / 2),
                          LineTo(cx + w / 2, cy - h / 2),
                          LineTo(cx, cy + h / 2), Close()])


class _LiveSearch(TextInputWidget):
    """Console-chrome TextInput reporting EVERY edit — the composer
    LiveInput idiom (chrome.py) carried onto the console theme by
    subclassing the SAME editing machinery, never re-implementing it."""

    def __init__(self, widget_id: str, *, on_change=None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, chrome="console", max_lines=1, **kwargs)
        self.on_change = on_change

    def _fire(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self.text)
            except Exception:
                pass

    def on_text(self, text: str) -> None:
        super().on_text(text)
        self._fire()

    def on_key(self, key: str, modifiers: Any = None) -> None:
        super().on_key(key, modifiers)
        if key in ("Backspace", "Delete", "Escape"):
            self._fire()


class DeliverableHistoryWidget(Container):
    """The full Artifacts filter/sort view (LOCKED previs)."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "deliverable_history", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.interactive = True
        # payload state (the publisher contract)
        self._all: List[Dict[str, Any]] = []
        self._stats: Dict[str, Any] = {}
        self._session: str = ""
        # view state (previs: activeKind / sortKey / sortDir / query)
        self.active_kind = "all"
        self.sort_key = "modified"
        self.sort_dir = -1           # -1 desc (previs initial Modified ▼)
        self.query = ""
        # scroll state (top-anchored; clamp reconciled at draw)
        self._scroll = 0.0
        self._max_scroll = 0.0
        # hit rects recorded at draw
        self._chip_rects: List[Tuple[Rect, str]] = []
        self._sort_rects: List[Tuple[Rect, str]] = []
        self._open_rects: List[Tuple[Rect, str]] = []
        self._body_rect: Optional[Rect] = None
        # the live search child (the FlowPane embedded-input idiom)
        self.search = _LiveSearch(f"{widget_id}_search",
                                  placeholder="filter by name, path, topic…",
                                  on_change=self._set_query)
        self.add(self.search)

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        rows = payload.get("all")
        if isinstance(rows, list):
            self._all = [r for r in rows if isinstance(r, dict)]
        stats = payload.get("stats")
        if isinstance(stats, dict):
            self._stats = stats
        self._session = str(payload.get("session", self._session))
        # [CXC-R-DAMAGE] scoped damage only.
        self.request_redraw()

    # -- view-state mutations (each damages only this widget) -------------------

    def _set_query(self, text: str) -> None:
        q = str(text or "").lower().strip()
        if q != self.query:
            self.query = q
            self._scroll = 0.0
            self.request_redraw()

    def set_kind(self, kind: str) -> None:
        k = str(kind or "all").lower()
        if k != self.active_kind:
            self.active_kind = k
            self._scroll = 0.0
            self.request_redraw()

    # the sortable columns. "doc" (the Document column — sorts by artifact
    # NAME) was a design oversight: the previs shipped Created/Modified
    # sortable only, so the Artifacts view could not be ordered by name at
    # all. Added here so a name-sort control exists and works.
    SORT_KEYS = ("doc", "created", "modified")

    def toggle_sort(self, key: str) -> None:
        """Previs th.sortable semantics: same column flips direction, a
        new column starts descending. "doc" sorts by artifact name."""
        if key not in self.SORT_KEYS:
            return
        if self.sort_key == key:
            self.sort_dir *= -1
        else:
            self.sort_key = key
            # name defaults ASCENDING (A→Z, the natural name order); the
            # date columns keep their newest-first default.
            self.sort_dir = 1 if key == "doc" else -1
        self.request_redraw()

    # -- derivation (pure — the previs render() pipeline) -----------------------

    def kind_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {"all": len(self._all)}
        for r in self._all:
            k = str(r.get("kind", "") or "—").lower()
            counts[k] = counts.get(k, 0) + 1
        return counts

    def chip_kinds(self) -> List[str]:
        """previs chip order + any extra kinds present in the data."""
        extras = sorted(k for k in self.kind_counts()
                        if k not in KIND_ORDER and k != "all")
        return ["all", *KIND_ORDER, *extras]

    def visible_rows(self) -> List[Dict[str, Any]]:
        """filter by kind -> live search over name/path/topics/summary ->
        sort by name|created|modified asc/desc (the previs pipeline, plus the
        name sort the previs omitted)."""
        rows = self._all
        if self.active_kind != "all":
            rows = [r for r in rows
                    if str(r.get("kind", "")).lower() == self.active_kind]
        if self.query:
            q = self.query

            def hay(r: Dict[str, Any]) -> str:
                topics = r.get("topics") or []
                return " ".join([str(r.get("name", "")),
                                 str(r.get("path", "")),
                                 " ".join(str(t) for t in topics),
                                 str(r.get("summary", "") or "")]).lower()
            rows = [r for r in rows if q in hay(r)]
        key, direction = self.sort_key, self.sort_dir
        # the Document column sorts on the artifact NAME, case-folded so
        # the order is the human-legible A→Z (not ASCII, where caps sort
        # before lowercase); the date columns sort on their own field.
        field = "name" if key == "doc" else key

        def sk(r: Dict[str, Any]) -> str:
            v = str(r.get(field, "") or "")
            return v.lower() if key == "doc" else v
        return sorted(rows, key=sk, reverse=(direction < 0))

    @staticmethod
    def summary_cell(row: Dict[str, Any]) -> Tuple[str, str]:
        """(clipped-256 text, len readout) for an ENRICHED row — the firm
        requirement: first 256 chars + 'N chars · showing min(N,256)'."""
        s = str(row.get("summary", "") or "")
        n = row.get("summary_len")
        n = int(n) if isinstance(n, (int, float)) \
            and not isinstance(n, bool) else len(s)
        # the publisher may PRE-clip summary to 256 while summary_len keeps
        # the true length — the ellipsis keys off the TRUE length.
        clipped = s[:SUMMARY_CLIP]
        if max(n, len(s)) > SUMMARY_CLIP:
            clipped += "…"
        return clipped, f"{n} chars · showing {min(n, SUMMARY_CLIP)}"

    @staticmethod
    def _kind_hue(kind: str, t) -> Tuple[int, int, int, int]:
        k = str(kind or "").strip().lower()
        if k == "previs":
            return VIOLET
        if k == "brief":
            return t.ok
        if k == "primer":
            return t.accent
        if k == "research":
            return RESEARCH
        if k == "presentation":
            return PRESENTATION
        return t.dim                # report / unknown (previs --ink-2)

    @staticmethod
    def _access_color(conf: str, t) -> Tuple[int, int, int, int]:
        c = str(conf or "").lower()
        if c.startswith("public"):
            return t.ok
        if c.startswith("nda"):
            return t.crit
        if c.startswith("confidential"):
            return t.warn
        return t.faint              # internal-only / unknown

    # -- layout ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        sw = min(280.0, max(160.0, inner.w * 0.24))
        self.search.arranged_rect = Rect(
            inner.x + inner.w - PAD_X - sw,
            inner.y + HEAD_H + (BAR_H - 32.0) / 2.0, sw, 32.0)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- interaction ---------------------------------------------------------------

    def on_scroll(self, dx: float, dy: float) -> None:
        """Wheel over the row region: top-anchored offset (the doc_picker
        mapping — wheel-up rewinds toward the top), clamped to the content
        overflow measured at the last draw."""
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        def _in(r: Rect) -> bool:
            return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

        for r, kind in self._chip_rects:
            if _in(r):
                self.set_kind(kind)
                return
        for r, key in self._sort_rects:
            if _in(r):
                self.toggle_sort(key)
                return
        for r, path in self._open_rects:
            if _in(r) and path:
                # the opener daemon subscribes this, opens the file, and
                # stamps seen -> `unseen` flips on the next publish.
                self.publish(self.open_namespace, path)
                return

    # -- draw ------------------------------------------------------------------

    def _columns(self, w: float) -> List[Tuple[str, float]]:
        """[(column key, width)] left-to-right for total width *w*."""
        fixed = KIND_W + CREATED_W + MODIFIED_W + ACCESS_W + OPEN_W
        remaining = max(240.0, w - fixed)
        doc_w = remaining * 0.40
        return [("kind", KIND_W), ("doc", doc_w), ("created", CREATED_W),
                ("modified", MODIFIED_W), ("access", ACCESS_W),
                ("summary", remaining - doc_w), ("open", OPEN_W)]

    def _row_height(self, canvas: Canvas, row: Dict[str, Any],
                    summary_w: float) -> Tuple[float, List[str]]:
        """(block height, wrapped summary lines) — pending rows carry no
        summary block and sit at the minimum height."""
        if row.get("pending"):
            return ROW_MIN_H, []
        text, _ = self.summary_cell(row)
        if not text:
            return ROW_MIN_H, []
        canvas.set_font(ct.FONT_UI, 12.5, "regular")
        lines = wrap_text(canvas, text, max(80.0, summary_w - 2 * CELL_PAD))
        h = 11.0 + len(lines) * SUMMARY_LINE + 4.0 + 14.0 + 11.0
        return max(ROW_MIN_H, h), lines

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        x0, y0, W = rect.x + PAD_X, rect.y, rect.w - 2 * PAD_X

        # ── head: dot · Deliverable History · sub-line ──
        cy = y0 + HEAD_H / 2.0 - 2.0
        ct.draw_status_dot(canvas, x0 + 5, cy, 4.5, t.accent,
                           halo=t.accent_dim, halo_w=4.0)
        canvas.set_font(ct.FONT_UI, 17.0, "bold")
        m = canvas.measure_text("Ag")
        base = cy + (m.ascent - m.descent) / 2.0
        canvas.set_fill_color(*t.ink)
        canvas.draw_text("Deliverable ", x0 + 18, base, "left")
        hx = x0 + 18 + canvas.measure_text("Deliverable ").width
        canvas.set_fill_color(*t.accent)
        canvas.draw_text("History", hx, base, "left")
        hx += canvas.measure_text("History").width
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("BUS://DELIVERABLE-LEDGER · SELF/DELIVERABLES",
                         hx + 14, base - 1.0, "left")

        # ── toolbar: chips (search child draws itself, right) ──
        counts = self.kind_counts()
        self._chip_rects = []
        cx = x0
        chip_cy = y0 + HEAD_H + BAR_H / 2.0
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        for kind in self.chip_kinds():
            label = "All" if kind == "all" else kind
            n = counts.get(kind, 0)
            text = f"{label} · {n}"
            tw = canvas.measure_text(text).width
            ch_w, ch_h = tw + 22.0, 26.0
            ch_x, ch_y = cx, chip_cy - ch_h / 2.0
            pressed = (kind == self.active_kind)
            if pressed:
                canvas.set_fill_color(*t.accent)
                canvas.fill_round_rect(ch_x, ch_y, ch_w, ch_h, 13.0)
            else:
                canvas.set_fill_color(*t.panel2)
                canvas.fill_round_rect(ch_x, ch_y, ch_w, ch_h, 13.0)
                canvas.set_stroke_color(*t.line)
                canvas.set_stroke_width(1.0)
                canvas.stroke_round_rect(ch_x + 0.5, ch_y + 0.5,
                                         ch_w - 1, ch_h - 1, 13.0)
            cm = canvas.measure_text(text)
            canvas.set_fill_color(*(t.send_ink if pressed else t.dim))
            canvas.draw_text(text, ch_x + 11,
                             chip_cy + (cm.ascent - cm.descent) / 2.0,
                             "left")
            self._chip_rects.append((Rect(ch_x, ch_y - 3, ch_w, ch_h + 6),
                                     kind))
            cx += ch_w + 6.0

        # ── count line ──
        shown = self.visible_rows()
        total = len(self._all)
        enriched = sum(1 for r in self._all if not r.get("pending"))
        count_base_y = y0 + HEAD_H + BAR_H + COUNT_H / 2.0
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        cm = canvas.measure_text("Ag")
        cb = count_base_y + (cm.ascent - cm.descent) / 2.0
        seg_x = x0

        def _seg(txt: str, color) -> None:
            nonlocal seg_x
            canvas.set_fill_color(*color)
            canvas.draw_text(txt, seg_x, cb, "left")
            seg_x += canvas.measure_text(txt).width
        _seg(f"{len(shown)} shown", t.dim)
        _seg(f" · {total} total · ", t.faint)
        _seg(f"{enriched} enriched", t.ok)
        _seg(" · ", t.faint)
        _seg(f"{total - enriched} pending", t.warn)

        # ── table panel ──
        tx = x0
        ty = y0 + HEAD_H + BAR_H + COUNT_H + 6.0
        tw_ = W
        th_ = max(80.0, rect.h - (ty - y0) - 8.0)
        canvas.set_fill_color(*t.panel)
        canvas.fill_round_rect(tx, ty, tw_, th_, 12.0)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(tx + 0.5, ty + 0.5, tw_ - 1, th_ - 1, 12.0)

        cols = self._columns(tw_)
        col_x: Dict[str, float] = {}
        acc = tx
        for key, cw in cols:
            col_x[key] = acc
            acc += cw
        col_w = dict(cols)

        # thead
        self._sort_rects = []
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        hm = canvas.measure_text("Ag")
        hb = ty + THEAD_H / 2.0 + (hm.ascent - hm.descent) / 2.0
        titles = {"kind": "KIND", "doc": "DOCUMENT", "created": "CREATED",
                  "modified": "MODIFIED", "access": "ACCESS",
                  "summary": "SUMMARY · FIRST 256 CHARS", "open": ""}
        for key, cw in cols:
            label = titles[key]
            if not label:
                continue
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(label, col_x[key] + CELL_PAD, hb, "left")
            if key in self.SORT_KEYS:
                lw = canvas.measure_text(label).width
                if self.sort_key == key:
                    _tri(canvas, col_x[key] + CELL_PAD + lw + 9,
                         ty + THEAD_H / 2.0, 7.0, 5.0,
                         self.sort_dir > 0, t.accent)
                self._sort_rects.append(
                    (Rect(col_x[key], ty, cw, THEAD_H), key))
        canvas.set_stroke_color(*t.line)
        canvas.draw_line(tx, ty + THEAD_H, tx + tw_, ty + THEAD_H)

        # ── body (scrollable) ──
        body = Rect(tx, ty + THEAD_H, tw_, th_ - THEAD_H)
        self._body_rect = body
        self._open_rects = []
        canvas.save()
        canvas.clip_round_rect(tx, ty, tw_, th_, 12.0)
        canvas.clip_rect(body.x, body.y, body.w, body.h)

        if not self._all:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.5, "regular")
            canvas.draw_text("awaiting deliverable sweep…",
                             body.x + body.w / 2, body.y + 44, "center")
        elif not shown:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_UI, 13.0, "regular")
            canvas.draw_text("No deliverables match this filter.",
                             body.x + body.w / 2, body.y + 44, "center")

        # measure pass (row heights depend on wrapped summaries)
        heights: List[float] = []
        lines_by_row: List[List[str]] = []
        for row in shown:
            h, lines = self._row_height(canvas, row, col_w["summary"])
            heights.append(h)
            lines_by_row.append(lines)
        content_h = sum(heights)
        self._max_scroll = max(0.0, content_h - body.h)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        ry = body.y - self._scroll
        for i, row in enumerate(shown):
            rh = heights[i]
            if ry + rh < body.y:            # above the viewport
                ry += rh
                continue
            if ry > body.y + body.h:        # below it — done
                break
            self._draw_row(canvas, row, lines_by_row[i], col_x, col_w,
                           ry, rh, t, body)
            canvas.set_stroke_color(t.line[0], t.line[1], t.line[2], 153)
            canvas.set_stroke_width(1.0)
            canvas.draw_line(tx, ry + rh, tx + tw_, ry + rh)
            ry += rh
        canvas.restore()

        # scrollbar (chat_thread idiom, top-anchored)
        if content_h > body.h:
            track_w = 4.0
            sx = tx + tw_ - 10.0
            sy = body.y + 6.0
            sh = body.h - 12.0
            canvas.set_fill_color(*t.line)
            canvas.fill_round_rect(sx, sy, track_w, sh, 2.0)
            thumb_h = max(24.0, sh * (body.h / content_h))
            travel = max(0.0, sh - thumb_h)
            frac = 0.0 if self._max_scroll <= 0 \
                else self._scroll / self._max_scroll
            canvas.set_fill_color(*t.faint)
            canvas.fill_round_rect(sx, sy + travel * frac, track_w,
                                   thumb_h, 2.0)

        # the live search child (drawn last — FlowPane idiom)
        if self.search.arranged_rect is not None:
            self.search.draw(canvas, self.search.arranged_rect)
        canvas.restore()

    def _draw_row(self, canvas: Canvas, row: Dict[str, Any],
                  sum_lines: List[str], col_x: Dict[str, float],
                  col_w: Dict[str, float], ry: float, rh: float, t,
                  body: Rect) -> None:
        top = ry + 11.0

        # kind badge (tinted round-rect, category hue)
        kind = str(row.get("kind", "") or "—").lower()
        hue = self._kind_hue(kind, t)
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        km = canvas.measure_text(kind.upper())
        bw, bh = km.width + 16.0, 20.0
        bx, by = col_x["kind"] + CELL_PAD, top
        canvas.set_fill_color(hue[0], hue[1], hue[2], 26)
        canvas.fill_round_rect(bx, by, bw, bh, 6.0)
        canvas.set_stroke_color(hue[0], hue[1], hue[2], 64)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(bx + 0.5, by + 0.5, bw - 1, bh - 1, 6.0)
        canvas.set_fill_color(*hue)
        canvas.draw_text(kind.upper(), bx + 8.0,
                         by + bh / 2.0 + (km.ascent - km.descent) / 2.0,
                         "left")

        # document: name (semibold ink) + path (mono faint)
        dx = col_x["doc"] + CELL_PAD
        dw = col_w["doc"] - 2 * CELL_PAD
        canvas.set_font(ct.FONT_UI, 13.0, "bold")
        nm = canvas.measure_text("Ag")
        name = ct.ellipsize(canvas, str(row.get("name", "?")), dw)
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(name, dx, top + nm.ascent, "left")
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        pm = canvas.measure_text("Ag")
        path = ct.ellipsize(canvas, str(row.get("path", "")), dw)
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(path, dx,
                         top + nm.ascent + nm.descent + 4.0 + pm.ascent,
                         "left")

        # created / modified (modified in accent when it's THIS session —
        # the previs mod-new signal derived from the payload's session)
        canvas.set_font(ct.FONT_MONO, 12.0, "regular")
        dm = canvas.measure_text("Ag")
        dbase = top + dm.ascent
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(str(row.get("created", "") or "—"),
                         col_x["created"] + CELL_PAD, dbase, "left")
        modified = str(row.get("modified", "") or "—")
        mod_new = bool(self._session) and modified == self._session
        canvas.set_fill_color(*(t.accent if mod_new else t.dim))
        canvas.draw_text(modified, col_x["modified"] + CELL_PAD, dbase,
                         "left")

        # access (confidentiality)
        conf = str(row.get("confidentiality", "") or "—")
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        am = canvas.measure_text("Ag")
        canvas.set_fill_color(*self._access_color(conf, t))
        canvas.draw_text(
            ct.ellipsize(canvas, conf.upper(), ACCESS_W - 2 * CELL_PAD),
            col_x["access"] + CELL_PAD, top + am.ascent, "left")

        # summary — enriched: first-256 + readout; else NEEDS-ENRICHMENT
        sx = col_x["summary"] + CELL_PAD
        sw = col_w["summary"] - 2 * CELL_PAD
        pending = row.get("pending") or []
        if pending:
            canvas.set_font(ct.FONT_MONO, 10.5, "regular")
            pill = "NEEDS-ENRICHMENT"
            tail = " · pending: " + ", ".join(str(p) for p in pending)
            pw = canvas.measure_text(pill).width
            tl = ct.ellipsize(canvas, tail, max(20.0, sw - pw - 18.0))
            tw2 = canvas.measure_text(tl).width
            ph, py = 22.0, top
            canvas.set_fill_color(t.warn[0], t.warn[1], t.warn[2], 20)
            canvas.fill_round_rect(sx, py, pw + tw2 + 18.0, ph, 6.0)
            # dashed border approximation: solid warn_line at width 1
            # (console_theme documented degradation)
            canvas.set_stroke_color(*t.warn_line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(sx + 0.5, py + 0.5,
                                     pw + tw2 + 17.0, ph - 1, 6.0)
            pmm = canvas.measure_text("Ag")
            pb = py + ph / 2.0 + (pmm.ascent - pmm.descent) / 2.0
            canvas.set_fill_color(*t.warn)
            canvas.draw_text(pill, sx + 9.0, pb, "left")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(tl, sx + 9.0 + pw, pb, "left")
        elif sum_lines:
            canvas.set_font(ct.FONT_UI, 12.5, "regular")
            sm = canvas.measure_text("Ag")
            ly = top + sm.ascent
            canvas.set_fill_color(*t.dim)
            for line in sum_lines:
                canvas.draw_text(line, sx, ly, "left")
                ly += SUMMARY_LINE
            _, readout = self.summary_cell(row)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            rm = canvas.measure_text("Ag")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(readout, sx, ly - SUMMARY_LINE
                             + sm.descent + 4.0 + rm.ascent, "left")

        # Open ▸ (previs .open — azure chip, drawn triangle)
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        op_label = "Open"
        om = canvas.measure_text(op_label)
        tri_w, tri_gap = 4.5, 5.0
        ow = om.width + tri_gap + tri_w + 24.0
        oh = 24.0
        ox = col_x["open"] + (col_w["open"] - ow) / 2.0
        oy = top
        ocy = oy + oh / 2.0
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 18)
        canvas.fill_round_rect(ox, oy, ow, oh, 7.0)
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 71)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ox + 0.5, oy + 0.5, ow - 1, oh - 1, 7.0)
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(op_label, ox + 12.0,
                         ocy + (om.ascent - om.descent) / 2.0, "left")
        _open_tri(canvas, ox + 12.0 + om.width + tri_gap, ocy, tri_w, 7.0,
                  t.accent)
        # tappable only where actually visible inside the body viewport
        vis_top = max(oy, body.y)
        vis_bot = min(oy + oh, body.y + body.h)
        if vis_bot > vis_top:
            self._open_rects.append(
                (Rect(ox - 4, vis_top - 4, ow + 8, vis_bot - vis_top + 8),
                 str(row.get("path", ""))))
