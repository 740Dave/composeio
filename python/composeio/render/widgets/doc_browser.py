"""
ComposeUX DocBrowserWidget — the in-app DOC-BROWSER overlay
([DEC-INAPP-MARKDOWN-VIEWER]; the "I read, you edit" popup).

A reusable two-pane document browser rendered as a modal OVERLAY: a scrim + a
centred panel with a LEFT pane = a scannable doc LIST and a RIGHT pane = the
``MarkdownViewerWidget`` rendering the selected doc, READ-ONLY.  It is the
surface the bootstrap-manifest wake steps (D11) and the doc-body pointers (D12)
open into — and, being a generic list-left/render-right browser, the shape the
coming LTS-viewer app reuses.

Why an overlay, not an external editor: the old Open seam popped a doc in an OS
editing app where a stray keystroke could mutate the source (David 2026-07-22).
This viewer ENFORCES "David reads in-app, the Architect edits the files":
there is no caret, no write path — only scroll + doc selection.

Wiring (two subscriptions):

  * ``open_namespace`` (default ``_app/doc/open``) — the OPEN seam.  A publish
    with a doc PATH opens that doc (the panel shows, the viewer loads it,
    read-only); a falsy publish ("", ``off``, ``none``) closes it.  The
    bootstrap manifest's wake-step / gift taps publish HERE now (its
    ``open_namespace`` is pointed at this seam) instead of the OS-open daemon.
  * ``docs_namespace`` (default ``self/meta``) — the meta-object projection,
    used to build the standing LEFT-pane list (role descriptor · principles ·
    each dusk gift).  An opened path not already in that list is prepended, so
    ANY doc the console surfaces opens here with a coherent navigator.

Reuse-by-reference (R3 / the narrow waist): the RIGHT pane is a real
``MarkdownViewerWidget`` instance (NOT a re-implementation) — one block/inline/
table measurement path, shared with every other markdown surface.  Chrome +
tokens come from ``console_theme``; the modal scrim/hit-capture idiom mirrors
``DialogWidget``.

Idle (nothing open): draws NOTHING and ``hit_test`` returns False, so taps fall
through to the Configuration tab underneath (the model_picker idle idiom).

Damage discipline ([CXC-R-DAMAGE]): bus updates + edits call ONLY
``self.request_redraw()`` — never ``mark_dirty`` / ``add_full`` / a rect.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .deliverable_card import _coerce   # reuse-by-reference: the same reader
from .markdown_viewer import MarkdownViewerWidget

DEFAULT_OPEN_NAMESPACE = "_app/doc/open"
DEFAULT_DOCS_NAMESPACE = "self/meta"

_PANEL_MAX_W = 1120.0
_PANEL_MAX_H = 1240.0
_MARGIN = 40.0
_HEADER_H = 48.0
_LIST_W = 316.0
_ROW_H = 52.0


def _base(canvas: Canvas, cy: float) -> float:
    m = canvas.measure_text("Ag")
    return cy + (m.ascent - m.descent) / 2.0


class DocBrowserWidget(Widget):
    """The two-pane in-app doc browser overlay ([DEC-INAPP-MARKDOWN-VIEWER])."""

    def __init__(self, widget_id: str, *,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 docs_namespace: str = DEFAULT_DOCS_NAMESPACE,
                 doc_root: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "doc_browser", **kwargs)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.docs_namespace = str(docs_namespace or DEFAULT_DOCS_NAMESPACE)
        # paths are meta-object-relative (repo root); the loader resolves them.
        self.doc_root = str(doc_root or "")
        self.interactive = True
        self.visible = False
        # standing doc list built from the meta-object projection
        self._docs: List[Dict[str, str]] = []
        self._selected: Optional[str] = None
        # the RIGHT pane — a real read-only viewer (composition, not a fork)
        self._viewer = MarkdownViewerWidget(widget_id + "__viewer", pad=22.0)
        # hit geometry recorded by draw()
        self._panel_rect: Optional[Rect] = None
        self._right_rect: Optional[Rect] = None
        self._close_rect: Optional[Rect] = None
        self._row_zones: List[Tuple[float, float, float, float, str]] = []

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.docs_namespace:
            self._rebuild_list(_coerce(value))
            self.request_redraw()
            return
        if namespace == self.open_namespace:
            self._handle_open(_coerce(value))
            return

    def _rebuild_list(self, meta: Any) -> None:
        """Build the standing left-pane list from the meta-object: role
        descriptor · principles · each dusk gift (pointer-with-open, D12)."""
        docs: List[Dict[str, str]] = []
        if isinstance(meta, dict):
            rd = meta.get("role_descriptor")
            if isinstance(rd, dict) and rd.get("path"):
                docs.append({"path": str(rd["path"]), "label": "Role descriptor",
                             "role": "identity"})
            pr = meta.get("principles")
            if isinstance(pr, dict) and pr.get("path"):
                docs.append({"path": str(pr["path"]), "label": "Principles",
                             "role": "principles"})
            for g in meta.get("dusk_gifts") or []:
                if isinstance(g, dict) and g.get("path"):
                    p = str(g["path"])
                    docs.append({"path": p,
                                 "label": os.path.basename(p) or p,
                                 "role": str(g.get("role", "gift"))})
        # preserve a currently-open path that isn't in the manifest
        if self._selected and not any(d["path"] == self._selected
                                      for d in docs):
            docs.insert(0, {"path": self._selected,
                            "label": os.path.basename(self._selected)
                            or self._selected, "role": "doc"})
        self._docs = docs

    def _handle_open(self, value: Any) -> None:
        path = value.get("path") if isinstance(value, dict) else value
        text = str(path or "").strip()
        if text == "" or text.lower() in ("off", "none", "false", "0"):
            self._close()
            return
        # an explicit doc list may accompany the open (generic browser mode)
        if isinstance(value, dict) and isinstance(value.get("docs"), list):
            self._docs = [{"path": str(d.get("path", "")),
                           "label": str(d.get("label")
                                        or os.path.basename(str(d.get("path",
                                                                      "")))),
                           "role": str(d.get("role", "doc"))}
                          for d in value["docs"] if isinstance(d, dict)
                          and d.get("path")]
        self._select(text)
        self.visible = True
        self.request_redraw()

    def _close(self) -> None:
        if self.visible:
            self.visible = False
            self.request_redraw()

    def _select(self, path: str) -> None:
        """Load *path* into the viewer (read-only), ensuring it's in the list."""
        self._selected = path
        if not any(d["path"] == path for d in self._docs):
            self._docs.insert(0, {"path": path,
                                  "label": os.path.basename(path) or path,
                                  "role": "doc"})
        self._viewer.load_path(self._resolve(path))

    def _resolve(self, path: str) -> str:
        if self.doc_root and not os.path.isabs(path):
            return os.path.join(self.doc_root, path)
        return path

    # -- lifecycle / measure ---------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        self._viewer.on_mount(ctx)          # share the ctx (redraw bubbles up)

    def measure(self, constraints: Constraints) -> Size:
        # the overlay claims the whole surface (the scrim).
        return Size(constraints.max_width, constraints.max_height)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        self._panel_rect = None
        self._right_rect = None
        self._close_rect = None
        self._row_zones = []
        if not self.visible:
            return
        t = ct.CONSOLE.active
        canvas.save()
        # scrim
        canvas.set_fill_color(4, 8, 14, 190)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        pw = min(_PANEL_MAX_W, rect.w - 2 * _MARGIN)
        ph = min(_PANEL_MAX_H, rect.h - 2 * _MARGIN)
        px = rect.x + (rect.w - pw) / 2.0
        py = rect.y + (rect.h - ph) / 2.0
        self._panel_rect = Rect(px, py, pw, ph)
        ct.draw_card(canvas, px, py, pw, ph, 14.0, t)
        canvas.save()
        canvas.clip_round_rect(px, py, pw, ph, 14.0)

        # header row: title + close ×
        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO + 1.0, "regular")
        hbase = _base(canvas, py + _HEADER_H / 2.0)
        ct.draw_tracked(canvas, "DOC BROWSER · READ-ONLY", px + 16.0, hbase,
                        ct.TRACK_LABEL, t.ink)
        canvas.set_font(ct.FONT_UI, 16.0, "regular")
        xm = canvas.measure_text("✕")
        cxx = px + pw - 16.0 - xm.width
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("✕", cxx, hbase, "left")
        self._close_rect = Rect(cxx - 8, py + 8, xm.width + 20, _HEADER_H - 12)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(px, py + _HEADER_H, px + pw, py + _HEADER_H)

        # split: left list | right viewer
        body_y = py + _HEADER_H
        body_h = ph - _HEADER_H
        list_w = min(_LIST_W, pw * 0.34)
        self._draw_list(canvas, px, body_y, list_w, body_h, t)
        canvas.set_stroke_color(*t.line)
        canvas.draw_line(px + list_w, body_y, px + list_w, py + ph)

        right = Rect(px + list_w + 1.0, body_y + 10.0,
                     pw - list_w - 1.0 - 10.0, body_h - 20.0)
        self._right_rect = right
        self._viewer.arranged_rect = right
        self._viewer.draw(canvas, right)

        canvas.restore()
        canvas.restore()

    def _draw_list(self, canvas: Canvas, x: float, y: float, w: float,
                   h: float, t) -> None:
        pad = 12.0
        ry = y + 10.0
        for d in self._docs:
            selected = d["path"] == self._selected
            row_x = x + pad
            row_w = w - 2 * pad
            if selected:
                canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 26)
                canvas.fill_round_rect(row_x, ry, row_w, _ROW_H - 6, 8.0)
                canvas.set_stroke_color(*t.accent_line)
                canvas.set_stroke_width(1.0)
                canvas.stroke_round_rect(row_x + 0.5, ry + 0.5, row_w - 1,
                                         _ROW_H - 7, 8.0)
            # label (ink; accent when selected)
            canvas.set_font(ct.FONT_UI, 13.0, "bold")
            canvas.set_fill_color(*(t.accent if selected else t.ink))
            canvas.draw_text(ct.ellipsize(canvas, d["label"], row_w - 12.0),
                             row_x + 10.0, _base(canvas, ry + 17.0), "left")
            # role pill / path hint (faint mono)
            canvas.set_font(ct.FONT_MONO, 9.0, "regular")
            ct.draw_tracked(canvas, str(d.get("role", "")).upper(),
                            row_x + 10.0, _base(canvas, ry + 36.0), 0.6, t.faint)
            self._row_zones.append((row_x, ry, row_x + row_w, ry + _ROW_H - 6,
                                    d["path"]))
            ry += _ROW_H
        if not self._docs:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting self/meta…", x + w / 2, y + 40.0,
                             "center")

    # -- modal hit capture -----------------------------------------------------

    def hit_test(self, x: float, y: float) -> bool:
        # while idle, taps fall through (model_picker idle idiom)
        if not self.visible or self.arranged_rect is None:
            return False
        r = self.arranged_rect
        return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

    def on_tap(self, x: float, y: float) -> None:
        if not self.visible:
            return
        if self._close_rect and _in(self._close_rect, x, y):
            self._close()
            return
        for (x0, y0, x1, y1, path) in self._row_zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                if path != self._selected:
                    self._select(path)
                    self.request_redraw()
                return
        # a tap outside the panel dismisses; inside the panel body is swallowed
        if self._panel_rect and not _in(self._panel_rect, x, y):
            self._close()

    def on_scroll(self, dx: float, dy: float) -> None:
        # forward wheel to the viewer when the pointer is over the right pane
        if self.visible and self._right_rect is not None:
            self._viewer.on_scroll(dx, dy)
            self.request_redraw()


def _in(r: Rect, x: float, y: float) -> bool:
    return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h
