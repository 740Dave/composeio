"""
ComposeUX WidgetSelectWidget — Surface 4, Collaboration-tab widget selection
from Config ([SPEC:CXC-CONSOLE-PROD] [CXC-ICP-S4-WIDGETSEL]).

The Collaboration widgets become SHOW/HIDE + ARRANGE-able from a Configuration
section, built by REUSING the Agents/Skills nested list-detail idiom: a scrolling
LEFT-to-RIGHT list of one-line rows, each with a show/hide toggle + up/down order
controls.  The list is DRIVEN BY Foundation B — it renders
``list_widgets(category="collaboration")``, NEVER a hardcoded widget list — so a
new collaboration widget (its module + one registry descriptor) appears here on
the next launch (S6, modulo restart).

Selection rides STATE-ON-BUS: a toggle/arrange publishes Foundation A's
``.../config/console/tabs`` as ``{collaboration: {widgets:[...ordered visible
types...], hidden:[...]}}`` (the exact Intelligence-drivable seam pattern of
``_app/config/section``); ``config_persister`` folds it into ``meta.console.tabs``
and the Collaboration tab reads it at launch to decide which widgets to mount and
in what order (the READ side lives in ``console/personalization.py``).

DEFAULT (empty ``tabs``) = every Collaboration widget shown in registry order.  A
persisted type no longer in the registry is SKIPPED (a fork may have removed a
widget) — never a crash.

Damage discipline ([CXC-R-DAMAGE]): bus updates + taps call ONLY
``self.request_redraw()``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from ..engine.widget_registry import list_widgets, get_descriptor
from .deliverable_card import _coerce

DEFAULT_TABS_NS = "intelligence/architect/config/console/tabs"
DEFAULT_CATEGORY = "collaboration"
DEFAULT_REGION = "rail"
DEFAULT_REGION_W = 332.0        # the console rail column width
DEFAULT_REDIRECT_NS = "_app/console/tab"   # the console tab-switch seam

_ROW_H = 46.0
_ROW_GAP = 6.0
_BTN = 30.0            # toggle / arrow button size


def _base(canvas: Canvas, cy: float) -> float:
    m = canvas.measure_text("Ag")
    return cy + (m.ascent - m.descent) / 2.0


def collaboration_selection(tabs: Any, category: str = DEFAULT_CATEGORY,
                            ) -> Tuple[List[str], List[str]]:
    """Pure read of a persisted ``tabs`` block -> (visible_types, hidden_types)
    for *category*.  Tolerates the bare or ``{category: {...}}`` shapes; returns
    ([], []) for anything malformed (the caller then falls back to registry
    order = all shown)."""
    if not isinstance(tabs, dict):
        return [], []
    blk = tabs.get(category)
    if not isinstance(blk, dict):
        return [], []
    vis = [str(x) for x in blk.get("widgets", []) if isinstance(x, (str,))]
    hid = [str(x) for x in blk.get("hidden", []) if isinstance(x, (str,))]
    return vis, hid


class WidgetSelectWidget(Widget):
    """A show/hide/reorder editor for a category of widgets, registry-driven,
    persisting the selection to Foundation A's ``console.tabs``."""

    def __init__(self, widget_id: str, *,
                 tabs_ns: str = DEFAULT_TABS_NS,
                 category: str = DEFAULT_CATEGORY,
                 region: str = DEFAULT_REGION,
                 region_w: Optional[float] = DEFAULT_REGION_W,
                 region_h: Optional[float] = None,
                 redirect_ns: str = DEFAULT_REDIRECT_NS,
                 title: Optional[str] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "widget_select", **kwargs)
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        self.tabs_ns = str(tabs_ns or DEFAULT_TABS_NS)
        self.redirect_ns = str(redirect_ns or DEFAULT_REDIRECT_NS)
        self.category = str(category or DEFAULT_CATEGORY)
        # [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2 fit target: the region this
        # selector places INTO (default the 332px rail).  A catalog widget that
        # doesn't fit is shown disabled + redirected, not silently no-op'd.
        self.region = str(region or DEFAULT_REGION)
        self.region_w = region_w
        self.region_h = region_h
        self.title = str(title or f"{self.category.title()} Widgets")
        self.interactive = True
        # ordered [{type, display_name, required_namespaces, visible, fits,
        #           redirect}]
        self._rows: List[Dict[str, Any]] = []
        self._rebuild_from_registry()
        # hit geometry: (x0,y0,x1,y1, kind, index)
        self._zones: List[Tuple[float, float, float, float, str, int]] = []

    # -- model -----------------------------------------------------------------

    def _fit(self, wtype: str) -> Tuple[bool, str]:
        """(fits_region, redirect_home) for a catalog type against this
        selector's region — the generic min_size/region_affinity fit test
        ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2)."""
        d = get_descriptor(wtype)
        if d is None:
            return True, ""
        fits = d.fits_region(self.region, self.region_w, self.region_h)
        return fits, ("" if fits else d.redirect_region(self.region))

    def _registry_rows(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for d in list_widgets(category=self.category):
            fits, redirect = self._fit(d.type)
            out.append({"type": d.type, "display_name": d.display_name,
                        "required_namespaces": list(d.required_namespaces),
                        # a misfit starts NOT visible (it can't live here) and is
                        # not toggleable — so it never enters the region's set.
                        "visible": fits, "fits": fits, "redirect": redirect})
        return out

    def _rebuild_from_registry(self) -> None:
        self._rows = self._registry_rows()

    def _apply_selection(self, visible: List[str], hidden: List[str]) -> None:
        """Reconcile a persisted (visible, hidden) selection against the LIVE
        registry: order by the persisted visible list first (skipping types no
        longer registered), then any registry types not mentioned (shown by
        default), and mark hidden ones."""
        reg = {r["type"]: r for r in self._registry_rows()}
        ordered: List[Dict[str, Any]] = []
        seen = set()
        for typ in visible:
            r = reg.get(typ)
            if r is not None and typ not in seen:
                # a misfit can never be visible in this region — honor the fit
                # test over a stale persisted selection.
                r["visible"] = bool(r["fits"])
                ordered.append(r)
                seen.add(typ)
        for typ in hidden:
            r = reg.get(typ)
            if r is not None and typ not in seen:
                r["visible"] = False
                ordered.append(r)
                seen.add(typ)
        # any registry widget neither listed is appended: shown IFF it fits.
        for typ, r in reg.items():
            if typ not in seen:
                r["visible"] = bool(r["fits"])
                ordered.append(r)
        self._rows = ordered

    def selection_payload(self) -> Dict[str, Any]:
        # Only region-FITTING rows contribute to the region's selection; a
        # misfit is never mounted here, so it is neither shown nor a hide entry.
        vis = [r["type"] for r in self._rows if r["fits"] and r["visible"]]
        hid = [r["type"] for r in self._rows if r["fits"] and not r["visible"]]
        return {self.category: {"widgets": vis, "hidden": hid}}

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace != self.tabs_ns:
            return
        tabs = _coerce(value)
        vis, hid = collaboration_selection(tabs, self.category)
        if vis or hid:
            self._apply_selection(vis, hid)
            self.request_redraw()

    # -- lifecycle / measure ---------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._zones = []
        canvas.save()
        pad = ct.PAD_CARD_BODY
        n_vis = sum(1 for r in self._rows if r["visible"])
        card_h = min(rect.h, self._header_h(canvas) + 10.0
                     + len(self._rows) * (_ROW_H + _ROW_GAP) + 10.0)
        ct.draw_card(canvas, rect.x, rect.y, rect.w, card_h, ct.RADIUS_CARD, t)
        by = ct.draw_card_header(canvas, rect.x, rect.y, rect.w, self.title,
                                 ns_tag="console/tabs",
                                 count_text=f"{n_vis}/{len(self._rows)}",
                                 count_color=t.accent, t=t)
        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, card_h, ct.RADIUS_CARD)
        ry = by + 10.0
        for i, r in enumerate(self._rows):
            if ry + _ROW_H > rect.y + card_h - 4:
                break
            self._draw_row(canvas, rect.x + pad, ry, rect.w - 2 * pad, i, r, t)
            ry += _ROW_H + _ROW_GAP
        canvas.restore()
        canvas.restore()

    def _draw_row(self, canvas: Canvas, x: float, y: float, w: float,
                  idx: int, r: Dict[str, Any], t) -> None:
        fits = bool(r.get("fits", True))
        visible = bool(r["visible"]) and fits
        # A misfit row is DISABLED (greyed): it can't live in this region.  The
        # right side carries a "→ add on <home>" redirect instead of controls,
        # so a toggle is never a silent no-op
        # ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2).
        # row panel
        canvas.set_fill_color(*(t.panel3 if visible else t.panel))
        canvas.fill_round_rect(x, y, w, _ROW_H, 9.0)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, w - 1, _ROW_H - 1, 9.0)
        # status dot + display name
        cy1 = y + 15.0
        ct.draw_status_dot(canvas, x + 14.0, cy1, 3.5,
                           t.ok if visible else t.faint,
                           halo=t.ok_dim if visible else None)
        canvas.set_font(ct.FONT_UI, 13.0, "medium" if visible else "regular")
        canvas.set_fill_color(*(t.ink if fits else t.faint))
        canvas.draw_text(ct.ellipsize(canvas, str(r["display_name"]), w - 180.0),
                         x + 26.0, _base(canvas, cy1), "left")
        # sub-line: required namespaces when fitting; the fit reason when not.
        if fits:
            rns = r.get("required_namespaces") or []
            sub = ("needs " + ", ".join(rns)) if rns else "no data binding"
            sub_col = t.faint
        else:
            sub = "needs more room"
            sub_col = t.warn
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*sub_col)
        canvas.draw_text(ct.ellipsize(canvas, sub, w - 180.0),
                         x + 26.0, _base(canvas, y + 32.0), "left")
        if not fits:
            self._draw_redirect(canvas, x, w, y, idx, r, t)
            return
        # right controls: [up] [down] [show/hide]
        bx = x + w - 3 * (_BTN + 6.0)
        cyb = y + _ROW_H / 2.0
        # reorder controls drawn as Canvas-primitive triangles (NOT font glyphs:
        # ▴/▾ are tofu in the bundled face — the exact narrow-waist trap).
        self._icon_btn(canvas, bx, cyb, "up", idx > 0, t)
        self._zones.append((bx, cyb - _BTN / 2, bx + _BTN, cyb + _BTN / 2,
                            "up", idx))
        bx += _BTN + 6.0
        self._icon_btn(canvas, bx, cyb, "down", idx < len(self._rows) - 1, t)
        self._zones.append((bx, cyb - _BTN / 2, bx + _BTN, cyb + _BTN / 2,
                            "down", idx))
        bx += _BTN + 6.0
        self._toggle_btn(canvas, bx, cyb, visible, t)
        self._zones.append((bx, cyb - _BTN / 2, bx + _BTN, cyb + _BTN / 2,
                            "toggle", idx))

    def _draw_redirect(self, canvas: Canvas, x: float, w: float, y: float,
                       idx: int, r: Dict[str, Any], t) -> None:
        """The "→ add on <home>" affordance on a disabled (misfit) row: a
        bordered chip naming where the widget CAN go (its redirect region,
        Title-cased).  A tap publishes ``redirect_namespace`` = the home region
        (the console tab-switch seam), so the constraint is discoverable +
        ACTIONABLE, never a silent skip ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED])."""
        home = str(r.get("redirect") or "dashboard").title()
        label = f"→ add on {home}"
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        tw = canvas.measure_text(label).width
        ch_w, ch_h = tw + 18.0, 22.0
        ch_x = x + w - 10.0 - ch_w
        ch_y = y + _ROW_H / 2.0 - ch_h / 2.0
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(ch_x, ch_y, ch_w, ch_h, 7.0)
        canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 90)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(ch_x + 0.5, ch_y + 0.5, ch_w - 1, ch_h - 1, 7.0)
        m = canvas.measure_text(label)
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 210)
        canvas.draw_text(label, ch_x + 9.0,
                         ch_y + ch_h / 2.0 + (m.ascent - m.descent) / 2.0,
                         "left")
        self._zones.append((ch_x, ch_y, ch_x + ch_w, ch_y + ch_h,
                            "redirect", idx))

    def _icon_btn(self, canvas: Canvas, x: float, cy: float, direction: str,
                  enabled: bool, t) -> None:
        y = cy - _BTN / 2.0
        canvas.set_fill_color(*t.panel2)
        canvas.fill_round_rect(x, y, _BTN, _BTN, 7.0)
        col = t.accent if enabled else t.faint
        canvas.set_stroke_color(col[0], col[1], col[2], 140)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, _BTN - 1, _BTN - 1, 7.0)
        # Reorder chevron as a filled triangle through the Canvas contract — no
        # font glyph (▴/▾ are tofu in the bundled face), so it renders identically
        # on every backend incl. the embedded tier, zero atlas dependency.
        cx = x + _BTN / 2.0
        tw, th = 5.0, 3.5
        canvas.set_fill_color(*col)
        if direction == "up":
            canvas.fill_path([MoveTo(cx, cy - th), LineTo(cx + tw, cy + th),
                              LineTo(cx - tw, cy + th), Close()])
        else:
            canvas.fill_path([MoveTo(cx - tw, cy - th), LineTo(cx + tw, cy - th),
                              LineTo(cx, cy + th), Close()])

    def _toggle_btn(self, canvas: Canvas, x: float, cy: float, on: bool,
                    t) -> None:
        y = cy - _BTN / 2.0
        canvas.set_fill_color(*(t.accent_dim if on else t.panel2))
        canvas.fill_round_rect(x, y, _BTN, _BTN, 7.0)
        col = t.ok if on else t.faint
        canvas.set_stroke_color(col[0], col[1], col[2], 160)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, _BTN - 1, _BTN - 1, 7.0)
        # on = a drawn check, off = a hollow ring — both via the Canvas contract
        # (the ○ off-glyph was tofu; the ✓ happened to be in the atlas, but the
        # pair must render identically on every backend).
        cx = x + _BTN / 2.0
        if on:
            canvas.set_stroke_color(col[0], col[1], col[2], 255)
            canvas.set_stroke_width(1.8)
            canvas.draw_line(cx - 5.0, cy + 0.5, cx - 1.5, cy + 4.0)
            canvas.draw_line(cx - 1.5, cy + 4.0, cx + 5.0, cy - 4.0)
        else:
            canvas.set_stroke_color(col[0], col[1], col[2], 200)
            canvas.set_stroke_width(1.4)
            canvas.stroke_circle(cx, cy, 4.5)

    def _header_h(self, canvas: Canvas) -> float:
        m = canvas.measure_text("Ag")
        return ct.PAD_CARD_HEADER[0] * 2 + m.ascent + m.descent

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        for (x0, y0, x1, y1, kind, idx) in self._zones:
            if x0 <= x <= x1 and y0 <= y <= y1:
                self._activate(kind, idx)
                return

    def _activate(self, kind: str, idx: int) -> None:
        if not (0 <= idx < len(self._rows)):
            return
        r = self._rows[idx]
        if kind == "redirect":
            # a misfit's "→ add on <home>" — navigate to the home region so the
            # add is actionable there (the console tab-switch seam), never a
            # silent no-op ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2).
            self.publish(self.redirect_ns, str(r.get("redirect") or "dashboard"))
            return
        if not r.get("fits", True):
            return                    # disabled rows never toggle/reorder
        if kind == "toggle":
            r["visible"] = not r["visible"]
        elif kind == "up" and idx > 0:
            self._rows[idx - 1], self._rows[idx] = \
                self._rows[idx], self._rows[idx - 1]
        elif kind == "down" and idx < len(self._rows) - 1:
            self._rows[idx + 1], self._rows[idx] = \
                self._rows[idx], self._rows[idx + 1]
        else:
            return
        self.publish(self.tabs_ns, self.selection_payload())
        self.request_redraw()
