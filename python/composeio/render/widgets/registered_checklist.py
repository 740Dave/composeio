"""
ComposeUX RegisteredChecklistWidget — the self/registered checklist
([SPEC:CXC-CONSOLE-VISUAL] [CXC-ICV-REGISTERED]).

"The blind spot un-blinded, made a green light" ([CXC-IC-SELF]): every
dusk-gift artifact the successor re-registered renders as an ok DOT (with
an ok-dim halo ring) + the filename (ink, medium) + its path (faint),
under a header carrying the n/total count.  All items ok -> the count is
ok green.  An item NOT ok goes dim/faint — NOT crit: an un-registered
artifact is "not yet", not an alarm (the calm rule, [CXC-ICV-TOKENS]).

Namespace (ONE): ``self/registered``.  Payload — the manifest-driven
per-artifact report ([CXC-ICD-D8]) in any of these graceful shapes:

    [{"name": "...", "path": "...", "ok": true}, ...]
    {"items": [ ... as above ... ]}
    {"<name>": true|false, ...}                 (map form)
    {"items": [...], "count": 4, "total": 4}    (count override)

All drawing is Canvas-contract only; ``measure_text`` drives truncation.
"""

from __future__ import annotations

import json
from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style

DEFAULT_NAMESPACE = "self/registered"


class RegisteredChecklistWidget(Widget):
    """Per-artifact registration checklist ([CXC-ICV-REGISTERED])."""

    def __init__(self, widget_id: str, *,
                 label: str = "self / registered",
                 namespace: str = DEFAULT_NAMESPACE,
                 font_size: float = 10.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "registered_checklist", **kwargs)
        # [CXC-R-DAMAGE] the card's ambient drop shadow paints past the rect.
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.label = str(label)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.font_size = float(font_size)
        # (name, path, ok) triples in arrival order
        self._items: List[Tuple[str, str, bool]] = []

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        items = self._coerce_items(value)
        if items is None:
            return
        self._items = items
        self.request_redraw()

    @staticmethod
    def _coerce_rows(value: Any) -> Optional[List[dict]]:
        """Parse a ``self/registered`` payload into per-artifact rows
        ``[{"name","path","ok","kind"}]`` (kind = the manifest KIND when the
        payload carries it, else ``None``).  The SINGLE shape-parser; both the
        checklist (3-tuples) and the IntelligenceCard caption (kinds) project
        from this — reuse-by-reference, never a forked parser."""
        if isinstance(value, str):
            s = value.strip()
            if not s or s[0] not in "{[":
                return None
            try:
                value = json.loads(s)
            except (ValueError, TypeError):
                return None
        if isinstance(value, dict):
            if isinstance(value.get("items"), list):
                value = value["items"]
            else:
                # map form: name -> ok (or name -> {path, ok})
                out = []
                for k, v in value.items():
                    if isinstance(v, dict):
                        out.append({"name": str(k), "path": str(v.get("path", "")),
                                    "ok": bool(v.get("ok", True)),
                                    "kind": v.get("kind")})
                    else:
                        out.append({"name": str(k), "path": "",
                                    "ok": bool(v), "kind": None})
                return out
        if isinstance(value, list):
            out = []
            for it in value:
                if isinstance(it, dict):
                    out.append({"name": str(it.get("name", it.get("file", "?"))),
                                "path": str(it.get("path", "")),
                                "ok": bool(it.get("ok", True)),
                                "kind": it.get("kind")})
                else:
                    out.append({"name": str(it), "path": "", "ok": True,
                                "kind": None})
            return out
        return None

    @staticmethod
    def _coerce_items(value: Any) -> Optional[List[Tuple[str, str, bool]]]:
        """The (name, path, ok) projection of :meth:`_coerce_rows` — the shape
        the checklist drawing loop consumes (kind dropped)."""
        rows = RegisteredChecklistWidget._coerce_rows(value)
        if rows is None:
            return None
        return [(r["name"], r["path"], r["ok"]) for r in rows]

    # -- rendering -------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        rows = max(1, len(self._items) or 4)
        return Size(min(320.0, constraints.max_width),
                    min(constraints.max_height, 46.0 + rows * 18.0))

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h,
                     ct.RADIUS_CARD, t)
        n_ok = sum(1 for _, _, ok in self._items if ok)
        total = len(self._items)
        count = f"{n_ok}/{total}" if total else ""
        all_ok = total > 0 and n_ok == total
        body_y = ct.draw_card_header(
            canvas, rect.x, rect.y, rect.w, self.label, self.namespace,
            count_text=count,
            count_color=t.ok if all_ok else t.faint, t=t)

        pad = ct.PAD_CARD_BODY
        box_x = rect.x + pad
        content_w = max(20.0, rect.w - 2 * pad)
        row_h = self.font_size * 1.8

        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h,
                               ct.RADIUS_CARD)
        ly = body_y + 7.0
        for name, path, ok in self._items:
            if ly + row_h > rect.y + rect.h - 4:
                break
            cy = ly + row_h / 2.0
            # ok dot + halo ring; a not-yet item is dim, never crit
            if ok:
                ct.draw_status_dot(canvas, box_x + 4, cy, 4.0, t.ok,
                                   halo=t.ok_dim)
            else:
                ct.draw_status_dot(canvas, box_x + 4, cy, 4.0, t.faint)
            canvas.set_font(style.FONT_MONO, self.font_size, "medium")
            base = style.baseline_for(canvas, cy)
            tx = box_x + 16.0
            nm = ct.ellipsize(canvas, name, content_w - 16.0)
            canvas.set_fill_color(*(t.ink if ok else t.dim))
            canvas.draw_text(nm, tx, base, "left")
            nw = canvas.measure_text(nm).width
            if path and nw + 8 < content_w - 16.0:
                canvas.set_font(style.FONT_MONO, self.font_size, "regular")
                pth = ct.ellipsize(canvas, path,
                                   content_w - 16.0 - nw - 8)
                canvas.set_fill_color(*t.faint)
                canvas.draw_text(pth, tx + nw + 8, base, "left")
            ly += row_h
        canvas.restore()

        if not self._items:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting registration…",
                             rect.x + rect.w / 2,
                             (body_y + rect.y + rect.h) / 2, "center")
        canvas.restore()
