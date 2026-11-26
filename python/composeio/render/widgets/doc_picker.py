"""
ComposeUX DocPickerWidget — a pick list backed by a unified-store query.

M2 port of ux_composer DocPickerRenderer: queries a collection, renders
the matching docs as a selectable list, and on select publishes chosen
fields of the doc into namespaces (`load_map`) — or, in launch mode,
asks the shell to app-switch.  The pick list IS the query result set
(DP-CIO-23).

The `where` filter runs CLIENT-SIDE ([SPEC:CXC-STORE] [DD-STORE-QUERY-DISPOSITION] —
the server-side query engine is retired): `query_docs` lists the collection, gets
each blob and filters in Python.  Queries run on a background thread (blocking HTTP
must never stall the render loop); results marshal back via the engine update queue.
"""

from __future__ import annotations

import threading
from typing import Any, List, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

_ROW_H = 34.0
_ROW_GAP = 4.0
_HDR_H = 26.0
_LIST_H = 130.0
_STATUS_H = 18.0


def _nested(doc: Any, path: str, default: Any = None) -> Any:
    cur = doc
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default
    return cur


class DocPickerWidget(Widget):
    """Selectable doc-store query results; select loads fields into namespaces."""

    def __init__(self, widget_id: str, *,
                 label: str = "Restore from saved",
                 collection: str = "fleet_devices",
                 where: Optional[dict] = None,
                 title_field: str = "device",
                 subtitle_field: str = "role",
                 load_map: Optional[dict] = None,
                 select_action: str = "load",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "doc_picker", **kwargs)
        self.label = label
        self.collection = collection
        self.where = where
        self.title_field = title_field
        self.subtitle_field = subtitle_field
        self.load_map = dict(load_map or {})
        self.select_action = select_action
        self.interactive = True

        self._rows: List[dict] = []
        self._status_text = ""
        self._status_color = style.TEXT_DIM
        self._scroll = 0.0
        self._pressed_row: Optional[int] = None

    # -- lifecycle ------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        self.refresh()

    # -- query (background thread; results marshal via update queue) -----------

    def refresh(self) -> None:
        self._set_status("querying…", style.TEXT_DIM)
        threading.Thread(target=self._query_worker, daemon=True).start()

    def _query_worker(self) -> None:
        try:
            from composeio.resolve import query_docs
            rows = query_docs(self.collection, where=self.where,
                              order_by=self.title_field)
            self._post(lambda: self._apply_rows(rows))
        except Exception as exc:
            msg = f"query failed: {exc}"
            self._post(lambda: self._set_status(msg, (0xE0, 0x90, 0x8F)))

    def _post(self, fn) -> None:
        if self._ctx is not None and hasattr(self._ctx, "schedule_update"):
            self._ctx.schedule_update(fn)
        else:
            fn()

    def _apply_rows(self, rows: List[dict]) -> None:
        self._rows = rows or []
        if not rows:
            self._set_status("no saved templates match", style.TEXT_DIM)
        else:
            self._set_status(f"{len(rows)} saved — pick one to load",
                             style.TEXT_DIM)

    def _set_status(self, text: str, color) -> None:
        self._status_text = text
        self._status_color = color
        self.request_redraw()

    # -- selection -----------------------------------------------------------------

    def _select(self, doc_id: str) -> None:
        if self.select_action == "launch":
            launch = getattr(self._ctx, "launch", None) if self._ctx else None
            if callable(launch):
                self._set_status(f"launching '{doc_id}'…", style.LED_ACTIVE)
                launch(doc_id)
            else:
                self._set_status("no launcher host (launch unavailable)",
                                 (0xE0, 0x90, 0x8F))
            return
        self._set_status(f"loading '{doc_id}'…", style.LED_ACTIVE)
        threading.Thread(target=self._load_worker, args=(doc_id,),
                         daemon=True).start()

    def _load_worker(self, doc_id: str) -> None:
        try:
            from composeio.resolve import resolve_doc
            doc = resolve_doc(f"bus://{self.collection}/{doc_id}.json")
        except Exception as exc:
            msg = f"load failed: {exc}"
            self._post(lambda: self._set_status(msg, (0xE0, 0x90, 0x8F)))
            return
        self._post(lambda: self._apply_doc(doc_id, doc))

    def _apply_doc(self, doc_id: str, doc: dict) -> None:
        loaded = 0
        for path, ns in self.load_map.items():
            val = _nested(doc, path)
            if val is not None:
                self.publish(ns, val)
                loaded += 1
        self._set_status(
            f"loaded '{doc_id}' → {loaded} fields — review + Provision",
            style.GREEN)

    # -- interaction (rows + refresh + scroll) ---------------------------------------

    def _row_at(self, x: float, y: float) -> Optional[int]:
        r = self.arranged_rect
        if r is None:
            return None
        top = r.y + _HDR_H + 2
        ly = y - top + self._scroll
        if y < top or y > top + _LIST_H:
            return None
        idx = int(ly // (_ROW_H + _ROW_GAP))
        if 0 <= idx < len(self._rows) and (ly % (_ROW_H + _ROW_GAP)) <= _ROW_H:
            return idx
        return None

    def on_tap(self, x: float, y: float) -> None:
        r = self.arranged_rect
        if r is None:
            return
        # refresh glyph button, top-right of the header
        if (y <= r.y + _HDR_H and x >= r.x + r.w - 40):
            self.refresh()
            return
        idx = self._row_at(x, y)
        if idx is not None:
            row = self._rows[idx]
            did = row.get("doc_id") or _nested(row, self.title_field, "?")
            self._select(str(did))

    def on_scroll(self, dx: float, dy: float) -> None:
        content_h = len(self._rows) * (_ROW_H + _ROW_GAP)
        max_scroll = max(0.0, content_h - _LIST_H)
        self._scroll = max(0.0, min(max_scroll, self._scroll - dy * 20.0))
        self.request_redraw()

    # -- measure / draw ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(280.0, constraints.max_width),
                    min(_HDR_H + _LIST_H + _STATUS_H + 10, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        x = rect.x + 8
        w = max(80.0, rect.w - 16)

        # header: label + refresh button
        canvas.set_fill_color(*style.TEXT)
        canvas.set_font(style.FONT_UI, 13, "bold")
        m = canvas.measure_text(self.label)
        canvas.draw_text(self.label, x, rect.y + 4 + m.ascent, "left")
        style.draw_button(canvas, x + w - 28, rect.y + 2, 28, 24, "↻")

        # list area (scrollable, clipped)
        top = rect.y + _HDR_H + 2
        canvas.save()
        canvas.set_fill_color(*style.FRAME_TOP_BG)
        canvas.fill_round_rect(x, top, w, _LIST_H, 6)
        canvas.clip_round_rect(x, top, w, _LIST_H, 6)
        ry = top + 2 - self._scroll
        canvas.set_font(style.FONT_UI, 12, "regular")
        for row in self._rows:
            if ry + _ROW_H >= top and ry <= top + _LIST_H:
                did = row.get("doc_id") or _nested(row, self.title_field, "?")
                title = _nested(row, self.title_field, did)
                sub = _nested(row, self.subtitle_field, "")
                text = f"{title}  —  {sub}"[:56]
                canvas.set_fill_color(*style.BTN_FILL)
                canvas.fill_round_rect(x + 4, ry, w - 8, _ROW_H, 6)
                canvas.set_fill_color(*style.BTN_TEXT)
                canvas.draw_text(text, x + 12,
                                 style.baseline_for(canvas, ry + _ROW_H / 2),
                                 "left")
            ry += _ROW_H + _ROW_GAP
        canvas.restore()

        # status line
        if self._status_text:
            canvas.set_fill_color(*self._status_color)
            canvas.set_font(style.FONT_UI, 11, "regular")
            sm = canvas.measure_text(self._status_text)
            canvas.draw_text(self._status_text, x,
                             top + _LIST_H + 4 + sm.ascent, "left")
        canvas.restore()
