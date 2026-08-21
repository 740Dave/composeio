"""
ComposeUX HookupWidget — the NATIVE hookup wiring guide ([FLOW:MAKER-PROJECT]
S3, render-target-agnostic pass 2026-07-26).

Renders a maker `.compose.yaml`'s `hookup:` block straight onto the Canvas
contract from the SHARED layout intermediate
(`composeio.render.engine.hookup_layout`) — the SAME part placements, pin
coordinates, and routed orthogonal wire polylines the SVG exporter
(`composeio.maker.hookup_svg`) serializes, so the in-app guide and the
portable artifact can never disagree about geometry (define-once).

What is native here (vs. the SVG artifact):

  * **Real Fritzing footprint art via a LOWERED-CACHE**: each part's cached
    footprint SVG is decomposed to Canvas calls ONCE per (part, size) through
    a :class:`~composeio.render.record.RecordingCanvas` display list and
    REPLAYED per redraw — no per-frame SVG re-walk (the compositing pass at
    interactive cost).
  * **Big, screen-sized labels** (David 2026-07-26): part ids / pin names /
    net labels are drawn in SCREEN pixels with readability floors, not board
    units — the guide is readable without zooming even when the board is
    scaled down to fit its pane.
  * **Fit-to-pane**: the board geometry contain-fits the widget rect (capped
    at ``max_scale``), so a generous host window shows the WHOLE guide.

`src:` points at the maker `.compose.yaml` (file path, file://, doc://,
bus:// — the AssetManager schemes); the widget reads the `hookup:` block via
the S2 parser (`engine.hookup.parse_hookup`).  Embedders/tests can also
install a parsed :class:`Hookup` directly via :meth:`set_hookup`.
"""

from __future__ import annotations

import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, LineTo, MoveTo, Size
from ..record import RecordingCanvas
from ..svg import render_svg
from ..widget import Constraints, Rect, Widget
from ..engine import hookup_layout as hl
from ..engine.hookup import Hookup, HookupError, parse_hookup

logger = logging.getLogger("composeio.render.widgets.hookup")

_DIM = (0x66, 0x66, 0x78)

# ---------------------------------------------------------------------------
# per-footprint lowered cache: (part id, fritzing asset, screen scale) ->
# display list.  Module-level so every HookupWidget instance (and every
# redraw) shares one lowering per footprint at a given size.
# ---------------------------------------------------------------------------
_DL_CACHE: Dict[Tuple[str, str, float], RecordingCanvas] = {}
# observability for tests + the cache-is-hit acceptance ([lowered] must not
# grow with redraws, only with new (part, size) keys)
_DL_STATS = {"lowered": 0, "replayed": 0}


def footprint_cache_stats() -> Dict[str, int]:
    """Copy of the lowered-cache counters (tests read this)."""
    return dict(_DL_STATS)


def clear_footprint_cache() -> None:
    """Drop every cached display list + zero the counters (tests)."""
    _DL_CACHE.clear()
    _DL_STATS["lowered"] = 0
    _DL_STATS["replayed"] = 0


def _hex(color: str) -> Tuple[int, int, int]:
    """'#RRGGBB' -> (r, g, b) for the shared palette's hex strings."""
    h = color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


class HookupWidget(Widget):
    """Native wiring-guide renderer over the shared hookup layout."""

    def __init__(self, widget_id: str, *,
                 src: str = "",
                 width: float = 1200.0,
                 height: float = 900.0,
                 max_scale: float = 2.0,
                 **kwargs: Any) -> None:
        # state exists BEFORE super().__init__ (the Lottie/Svg convention)
        self._src = ""
        self._hookup: Optional[Hookup] = None
        self._layout: Optional[hl.BoardLayout] = None
        self._segs: Optional[List[hl.WireSeg]] = None
        self._load_tried = False
        self._error: Optional[str] = None

        super().__init__(widget_id, "hookup", **kwargs)
        self.pref_width = float(width)
        self.pref_height = float(height)
        self.max_scale = float(max_scale)
        self._src = str(src)

    # -- source ----------------------------------------------------------------

    @property
    def src(self) -> str:
        return self._src

    @src.setter
    def src(self, value: Any) -> None:
        value = str(value or "")
        if value == self._src:
            return
        self._src = value
        self._hookup = None
        self._layout = None
        self._segs = None
        self._load_tried = False
        self._error = None
        self.request_redraw()

    def set_hookup(self, hookup: Optional[Hookup]) -> None:
        """Directly install a parsed hookup (tests / embedders)."""
        self._hookup = hookup
        self._layout = None
        self._segs = None
        self._load_tried = True
        self._error = None

    # -- loading ---------------------------------------------------------------

    def _read_bytes(self) -> Optional[bytes]:
        assets = getattr(self._ctx, "assets", None) if self._ctx else None
        uri = self._src
        if assets is not None and "://" in uri:
            data = assets.get_bytes(uri)
            if data is not None:
                return data
        path = uri[7:] if uri.startswith("file://") else uri
        if os.path.isfile(path):
            try:
                with open(path, "rb") as fh:
                    return fh.read()
            except OSError:
                return None
        if "://" in uri:                      # unmounted fallback (dev)
            try:
                from composeio import resolve as _resolve
                return _resolve.resolve_bytes(uri)
            except Exception:
                return None
        return None

    def _ensure_loaded(self) -> Optional[Hookup]:
        if self._hookup is not None or self._load_tried or not self._src:
            return self._hookup
        self._load_tried = True
        data = self._read_bytes()
        if data is None:
            self._error = f"unreadable: {self._src}"
            return None
        try:
            import yaml
            cfg = yaml.safe_load(data.decode("utf-8"))
        except Exception as exc:
            self._error = f"bad yaml: {exc}"
            return None
        block = cfg.get("hookup") if isinstance(cfg, dict) else None
        if block is None:
            self._error = "no hookup: block"
            return None
        try:
            self._hookup = parse_hookup(block)
        except HookupError as exc:
            self._error = str(exc)
            return None
        return self._hookup

    def _geometry(self) -> Tuple[hl.BoardLayout, List[hl.WireSeg]]:
        if self._layout is None:
            assert self._hookup is not None
            self._layout = hl.layout_hookup(self._hookup)
            self._segs = hl.wire_segments(self._hookup, self._layout)
        return self._layout, self._segs or []

    # -- measure -----------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        # The guide fills its pane (the markdown_viewer idiom); an unbounded
        # axis falls back to the declared preferred size.
        w = (constraints.max_width if math.isfinite(constraints.max_width)
             else self.pref_width)
        h = (constraints.max_height if math.isfinite(constraints.max_height)
             else self.pref_height)
        return Size(w, h)

    # -- draw --------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        hookup = self._ensure_loaded()
        if hookup is None:
            self._draw_placeholder(canvas, rect)
            return
        lay, segs = self._geometry()
        if lay.width <= 0 or lay.height <= 0:
            self._draw_placeholder(canvas, rect)
            return

        f = min(rect.w / lay.width, rect.h / lay.height, self.max_scale)
        ox = rect.x + (rect.w - lay.width * f) / 2.0
        oy = rect.y + (rect.h - lay.height * f) / 2.0

        def tx(x: float) -> float:
            return ox + x * f

        def ty(y: float) -> float:
            return oy + y * f

        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        try:
            # PAGE backdrop: the SVG artifact rasterizes on a white page, and
            # the title/legend ink needs it — the guide is a light document
            # whatever the host scene's theme.
            canvas.set_fill_color(0xF2, 0xF4, 0xF8)
            canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
            if hookup.board == "breadboard":
                self._draw_breadboard_bg(canvas, lay, f, tx, ty)
            else:
                self._draw_perfboard_bg(canvas, lay, f, tx, ty)
            self._draw_title(canvas, hookup, f, tx, ty)
            # z-order (native pass): part ART first, wires OVER the art (they
            # land on pads), and every LABEL last — the big screen-space text
            # must never be buried under a trunk run.
            for box in lay.parts.values():
                self._draw_part_art(canvas, box, f, tx, ty)
            self._draw_wires(canvas, hookup.board, segs, f, tx, ty)
            for box in lay.parts.values():
                self._draw_part_labels(canvas, box, f, tx, ty)
            self._draw_net_labels(canvas, segs, f, tx, ty)
        finally:
            canvas.restore()

    # -- pieces ----------------------------------------------------------------

    def _draw_placeholder(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.set_fill_color(0x28, 0x28, 0x3C)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*_DIM)
        canvas.set_font("sans-serif", 12, "regular")
        canvas.draw_text(self._error or self._src or "no hookup",
                         rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
        canvas.restore()

    def _draw_perfboard_bg(self, canvas, lay, f, tx, ty) -> None:
        # plain WHITE slab (the shared substrate decision — hookup_svg parity)
        x = tx(hl.ORIGIN_X - 66)
        y = ty(hl.ORIGIN_Y - 34)
        w = (lay.width - (hl.ORIGIN_X - 66) - 12) * f
        h = (lay.height - (hl.ORIGIN_Y - 34) - 12) * f
        canvas.set_fill_color(*_hex(hl.BOARD_PERFBOARD))
        canvas.fill_round_rect(x, y, w, h, 6 * f)
        canvas.set_stroke_color(*_hex("#D0D5DD"))
        canvas.set_stroke_width(max(1.0, 1.5 * f))
        canvas.stroke_round_rect(x, y, w, h, 6 * f)

    def _draw_breadboard_bg(self, canvas, lay, f, tx, ty) -> None:
        # board slab
        x = tx(hl.ORIGIN_X - 66)
        y = ty(hl.ORIGIN_Y - 34)
        w = (lay.width - (hl.ORIGIN_X - 66) - 12) * f
        h = (lay.height - (hl.ORIGIN_Y - 34) - 12) * f
        canvas.set_fill_color(*_hex(hl.BOARD_BREADBOARD))
        canvas.fill_round_rect(x, y, w, h, 8 * f)
        canvas.set_stroke_color(*_hex("#B9A86E"))
        canvas.set_stroke_width(max(1.0, 1.5 * f))
        canvas.stroke_round_rect(x, y, w, h, 8 * f)

        field_left = hl.ORIGIN_X - 44
        field_right = lay.width - 44
        field_top = hl.ORIGIN_Y - 12
        field_bot = lay.height - 30
        center_y = (field_top + field_bot) / 2.0
        gap = hl.PITCH * 1.4

        # center-gap trough FIRST, holes over it (SVG draws holes then trough;
        # visually equivalent because holes never land inside the gap)
        canvas.set_fill_color(*_hex("#DDCB92"))
        canvas.fill_rect(tx(field_left - 6), ty(center_y - gap / 2.0),
                         ((field_right - field_left) + 12) * f, gap * f)

        canvas.set_stroke_color(*_hex("#B4A163"))
        canvas.set_stroke_width(max(0.5, 0.6 * f))
        hole_r = max(1.2, (hl.HOLE_R - 0.4) * f)
        bx = field_left
        while bx <= field_right:
            by = field_top
            while by <= field_bot:
                if abs(by - center_y) > gap / 2.0:
                    canvas.set_fill_color(*_hex(hl.HOLE_FILL_BB))
                    canvas.fill_circle(tx(bx), ty(by), hole_r)
                    canvas.stroke_circle(tx(bx), ty(by), hole_r)
                by += hl.PITCH
            bx += hl.PITCH

        # power rails down both sides
        rail_font = max(11.0, 11 * f)
        for side, xr in (("L", hl.ORIGIN_X - 60), ("R", lay.width - 26)):
            pos_x = xr
            neg_x = xr + 12 if side == "L" else xr - 12
            canvas.set_stroke_color(*_hex(hl.COLOR_RAIL_POS))
            canvas.set_stroke_width(max(1.0, 1.6 * f))
            canvas.draw_line(tx(pos_x), ty(field_top), tx(pos_x), ty(field_bot))
            canvas.set_stroke_color(*_hex(hl.COLOR_RAIL_NEG))
            canvas.draw_line(tx(neg_x), ty(field_top), tx(neg_x), ty(field_bot))
            canvas.set_font("sans-serif", rail_font, "bold")
            canvas.set_fill_color(*_hex(hl.COLOR_RAIL_POS))
            canvas.draw_text("+", tx(pos_x), ty(field_top - 6), "center")
            canvas.set_fill_color(*_hex(hl.COLOR_RAIL_NEG))
            canvas.draw_text("−", tx(neg_x), ty(field_top - 6), "center")

    def _draw_title(self, canvas, hookup, f, tx, ty) -> None:
        ink = _hex(hl.INK)
        dim = _hex(hl.DIM)
        n_parts = len(hookup.parts)
        n_wires = len(hookup.wires)
        slots = ", ".join(hookup.slots) if hookup.slots else "-"
        lx = tx(hl.ORIGIN_X - 60)
        canvas.set_fill_color(*ink)
        canvas.set_font("sans-serif", max(16.0, 18 * f), "bold")
        canvas.draw_text(f"Hookup guide — {hookup.board}", lx, ty(30))
        canvas.set_fill_color(*dim)
        canvas.set_font("monospace", max(11.0, 11 * f), "regular")
        canvas.draw_text(f"{n_parts} parts · {n_wires} wires · "
                         f"slots: {slots}", lx, ty(48))
        # legend chips
        ly = ty(62)
        lfont = max(10.0, 10 * f)
        for label, color in (("power", hl.COLOR_POWER),
                             ("ground", hl.COLOR_GROUND),
                             ("signal", hl.COLOR_SIGNAL[0])):
            canvas.set_stroke_color(*_hex(color))
            canvas.set_stroke_width(max(3.0, 3 * f))
            canvas.draw_line(lx, ly, lx + 16 * f, ly)
            canvas.set_fill_color(*dim)
            canvas.set_font("sans-serif", lfont, "regular")
            canvas.draw_text(label, lx + 16 * f + 5, ly + lfont * 0.35)
            lx += max(84.0, 78 * f)

    # BIG-label sizes ([glass] David 2026-07-26: readable without zooming).
    # Screen px with floors — a scaled-down board keeps readable labels.
    def _font_id(self, f: float) -> float:
        return max(17.0, 15 * f)

    def _font_ref(self, f: float) -> float:
        return max(12.0, 10.5 * f)

    def _font_pin(self, f: float) -> float:
        return max(11.0, 10 * f)

    def _font_net(self, f: float) -> float:
        return max(12.0, 11 * f)

    # -- part ART (phase 1: under the wires) ----------------------------------

    def _draw_part_art(self, canvas, box, f, tx, ty) -> None:
        if box.footprint is not None:
            self._draw_footprint_art(canvas, box, f, tx, ty)
        else:
            self._draw_generated_art(canvas, box, f, tx, ty)

    def _draw_footprint_art(self, canvas, box, f, tx, ty) -> None:
        """Real Fritzing art via the lowered-cache + terminal rings."""
        fpp = box.footprint
        bx, by, bw, bh = box.body
        screen_scale = round(fpp.scale * f, 4)
        key = (box.part, fpp.fp.svg_path, screen_scale)
        dl = _DL_CACHE.get(key)
        if dl is None:
            dl = RecordingCanvas(delegate=canvas)
            doc = fpp.fp.doc
            # lower ONCE at the final on-screen scale, anchored at the origin
            # (stretch => translate(0,0) + scale + viewBox offset), replayed
            # below under a per-instance translate.
            render_svg(dl, doc,
                       Rect(0.0, 0.0, doc.vb_w * screen_scale,
                            doc.vb_h * screen_scale),
                       "stretch")
            _DL_CACHE[key] = dl
            _DL_STATS["lowered"] += 1
        canvas.save()
        canvas.translate(tx(bx), ty(by))
        dl.replay(canvas)
        canvas.restore()
        _DL_STATS["replayed"] += 1

        # terminal ring at every real pad (the wire-landing affordance)
        ring_r = max(3.0, (hl.HOLE_R + 1.0) * f)
        canvas.set_stroke_color(*_hex(hl.INK))
        canvas.set_stroke_width(max(1.0, 0.9 * f))
        for name, (px, py) in box.pins.items():
            canvas.stroke_circle(tx(px), ty(py), ring_r)

    def _draw_generated_art(self, canvas, box, f, tx, ty) -> None:
        bx, by, bw, bh = box.body
        canvas.set_fill_color(*_hex(hl.BODY_FILL))
        canvas.fill_round_rect(tx(bx), ty(by), bw * f, bh * f, 4 * f)
        canvas.set_stroke_color(*_hex("#0F1622"))
        canvas.set_stroke_width(max(1.0, 1.2 * f))
        canvas.stroke_round_rect(tx(bx), ty(by), bw * f, bh * f, 4 * f)

        hole_r = max(2.5, (hl.HOLE_R + 0.8) * f)
        for name, (px, py) in box.pins.items():
            canvas.set_stroke_color(*_hex("#8894A6"))
            canvas.set_stroke_width(max(1.0, 1.4 * f))
            if box.orient == "vertical":
                canvas.draw_line(tx(bx), ty(py), tx(px), ty(py))
            else:
                canvas.draw_line(tx(px), ty(by + bh), tx(px), ty(py))
            canvas.set_fill_color(*_hex("#E9EEF5"))
            canvas.fill_circle(tx(px), ty(py), hole_r)
            canvas.set_stroke_color(*_hex("#4A5568"))
            canvas.set_stroke_width(max(1.0, 1.0 * f))
            canvas.stroke_circle(tx(px), ty(py), hole_r)

    # -- part LABELS (phase 3: over the wires — BIG text never buried) --------

    def _draw_part_labels(self, canvas, box, f, tx, ty) -> None:
        bx, by, bw, bh = box.body
        id_font = self._font_id(f)
        ref_font = self._font_ref(f)
        pin_font = self._font_pin(f)
        ring_r = max(3.0, (hl.HOLE_R + 1.0) * f)
        cx = tx(bx + bw / 2)

        if box.footprint is not None:
            # instance id + catalog ref stacked ABOVE the art
            canvas.set_fill_color(*_hex(hl.INK))
            canvas.set_font("monospace", id_font, "bold")
            canvas.draw_text(box.id, cx, ty(by) - ref_font - 8, "center")
            canvas.set_fill_color(*_hex(hl.DIM))
            canvas.set_font("monospace", ref_font, "regular")
            canvas.draw_text(box.part, cx, ty(by) - 4, "center")
        else:
            # id + ref inside the generated body
            canvas.set_fill_color(*_hex(hl.BODY_TEXT))
            canvas.set_font("monospace", id_font, "bold")
            canvas.draw_text(box.id, cx, ty(by) + id_font + 2, "center")
            canvas.set_fill_color(*_hex("#9AA7B8"))
            canvas.set_font("monospace", ref_font, "regular")
            canvas.draw_text(box.part, cx, ty(by) + id_font + ref_font + 6,
                             "center")

        # ⚠ THIS SURFACE STILL CARRIES ITS OWN COPY OF THE PLACEMENT RULE, AND
        # IT IS THE ONE THAT SHIPPED THE DEFECT (2026-08-16).  The `min(...)` /
        # `max(...)` edge clamp below is correct for a COLUMN of pads and
        # stacks a whole ROW on one point — six `esp32-s3-supermini` labels
        # landed on one spot in the SVG guide, which is what made a delivered
        # hookup unreadable with every gate green.  The SVG exporter no longer
        # decides this itself: the decision moved to
        # `hookup_layout.pin_label_plan(box, wired)` (row/column classification,
        # turned labels, unwired-pad suppression), which is renderer-agnostic
        # and returns KINDS, not coordinates, precisely so this widget can adopt
        # it and keep its own screen-space offsets.
        # ⛔ UNTIL IT DOES, THE TWO SURFACES DISAGREE — this one will still
        # stack an S3's rows and still label pads nobody wires.  It is a FORK
        # with a name and a landing place, not a hidden one; whoever touches
        # this next should route it through `pin_label_plan` rather than patch
        # the clamp. Not done here because the widget's turned labels need a
        # canvas.rotate pass and its own goldens re-shot, and the delivered
        # artifact is the SVG.
        #
        # pin names.  Bottom-edge leads (a pot's three legs) STAGGER onto two
        # rows — big screen-space labels on PITCH-spaced legs would otherwise
        # run together ("vccwipergnd").
        bottom = sorted([(px, name) for name, (px, py) in box.pins.items()
                         if py > by + bh - hl.PITCH])
        bottom_row = {name: (i % 2) for i, (px, name) in enumerate(bottom)}
        canvas.set_font("monospace", pin_font, "regular")
        for name, (px, py) in box.pins.items():
            if box.footprint is None and box.orient == "vertical":
                canvas.set_fill_color(*_hex(hl.BODY_TEXT))
                canvas.draw_text(name, tx(bx) + 6, ty(py) + pin_font * 0.35,
                                 "left")
                continue
            canvas.set_fill_color(*_hex(hl.INK))
            if name in bottom_row:
                dy = ring_r + pin_font * (1.0 + 1.15 * bottom_row[name])
                canvas.draw_text(name, tx(px), ty(py) + dy, "center")
            elif box.footprint is None:
                canvas.draw_text(name, tx(px), ty(py) + ring_r + pin_font,
                                 "center")
            elif px < bx + bw / 2:
                canvas.draw_text(name, min(tx(px) - 7, tx(bx) - 4),
                                 ty(py) + pin_font * 0.35, "right")
            else:
                canvas.draw_text(name, max(tx(px) + 7, tx(bx + bw) + 4),
                                 ty(py) + pin_font * 0.35, "left")

    def _draw_wires(self, canvas, board, segs, f, tx, ty) -> None:
        wire_w = max(3.0, 4.8 * f)
        pad_r = max(4.0, hl.PAD_R * f)
        for seg in segs:
            rgb = _hex(seg.color)
            canvas.set_stroke_color(*rgb)
            canvas.set_stroke_width(wire_w)
            cmds: List[Any] = [MoveTo(tx(seg.points[0][0]),
                                      ty(seg.points[0][1]))]
            for (px, py) in seg.points[1:]:
                cmds.append(LineTo(tx(px), ty(py)))
            canvas.draw_path(cmds)
            # wire ends land exactly on the real pins
            for (ex, ey) in (seg.frm_xy, seg.to_xy):
                if board == "perfboard":
                    # solder pad: steel donut + wire-colored rim + core
                    canvas.set_fill_color(*_hex("#C9CDD4"))
                    canvas.fill_circle(tx(ex), ty(ey), pad_r)
                    canvas.set_stroke_color(*rgb)
                    canvas.set_stroke_width(max(1.2, 1.2 * f))
                    canvas.stroke_circle(tx(ex), ty(ey), pad_r)
                    canvas.set_fill_color(*_hex("#2A2E36"))
                    canvas.fill_circle(tx(ex), ty(ey), max(1.1, 1.1 * f))
                else:
                    canvas.set_fill_color(*rgb)
                    canvas.fill_circle(tx(ex), ty(ey), max(2.4, 2.4 * f))

    def _draw_net_labels(self, canvas, segs, f, tx, ty) -> None:
        net_font = self._font_net(f)
        canvas.set_font("monospace", net_font, "regular")
        for seg in segs:
            if not seg.publishes:
                continue
            lx, ly = hl.label_anchor(seg.points)
            canvas.set_fill_color(*_hex(seg.color))
            canvas.draw_text(seg.publishes, tx(lx), ty(ly) - 5, "center")


__all__ = ["HookupWidget", "footprint_cache_stats", "clear_footprint_cache"]
