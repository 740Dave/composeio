"""Composer chrome — the M4 Composer's own UI, rendered by the engine.

Everything here draws through the abstract Canvas contract and interacts
through the engine's pointer/key pipeline: the Composer is itself a
render-engine app (the dogfooding gate of [CXC-RM-M4]).  Visual language
follows specs/ux/composer-workshop-previs.v1.html (dark theme).

Widgets:
    WorkshopWidget   pixel rulers + pixel-stack canvas + selection
                     handles + move/resize drag (the WW, 2/3)
    LayersPanel      z-order list: select / eye / reorder (WC Layers tab)
    CatalogPanel     add-a-widget list (WC Widgets tab)
    OverviewPanel    name/size/namespaces/bindings/purpose (WC Overview)
    StatusBarWidget  telemetry: selection · bus · MQTT · UART
    ActionButton     small chrome button firing a callback
    FormInput        TextInput that reports commits to a callback
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, List, Optional, Tuple

from ..render.canvas import Canvas, Close, LineTo, MoveTo, Size
from ..render.widget import Constraints, Rect, Widget
from ..render.engine.layout import Container, _contains
from ..render.engine.parser import Composition, build_node
from ..render.widgets import TabWidget, TextInputWidget
from ..render.widgets import style as wstyle

from .catalog import entries as catalog_entries
from .document import CompositeDocument, LayerDef

logger = logging.getLogger("composeio.composer.chrome")

# -- pre-vis dark palette ----------------------------------------------------

BG = (0x10, 0x13, 0x19)
PANEL = (0x19, 0x1D, 0x25)
PANEL2 = (0x20, 0x25, 0x2F)
PANEL3 = (0x27, 0x2D, 0x38)
BORDER = (0x2B, 0x32, 0x3D)
BORDER_STRONG = (0x39, 0x42, 0x4F)
TEXT = (0xE7, 0xEA, 0xF1)
DIM = (0x96, 0xA0, 0xB0)
FAINT = (0x69, 0x72, 0x7F)
ACCENT = (0x3F, 0xB6, 0xD4)
ACCENT_SOFT = (0x3F, 0xB6, 0xD4, 0x1F)
GOOD = (0x39, 0xC4, 0x8A)
WARN = (0xE0, 0xA6, 0x3A)
CRIT = (0xE5, 0x6D, 0x61)
CANVAS_BG = (0x08, 0x0A, 0x0F)
RULER_BG = (0x16, 0x1A, 0x21)
TICK = (0x39, 0x42, 0x4F)
TICK_STRONG = (0x5A, 0x64, 0x72)

FONT = wstyle.FONT_UI
MONO = wstyle.FONT_MONO


def _baseline(canvas: Canvas, y_center: float) -> float:
    m = canvas.measure_text("Ag")
    return y_center + (m.ascent - m.descent) / 2


# ---------------------------------------------------------------------------
# Small chrome controls
# ---------------------------------------------------------------------------

class ActionButton(Widget):
    """Compact chrome button: label + on_press callback."""

    def __init__(self, widget_id: str, *, label: str,
                 on_press: Optional[Callable[[], None]] = None,
                 accent: bool = False, width: float = 0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "chrome_button", **kwargs)
        self.label = label
        self.on_press = on_press
        self.accent = accent
        self.fixed_width = width
        self.interactive = True
        self._pressed = False

    def measure(self, constraints: Constraints) -> Size:
        w = self.fixed_width or (24 + len(self.label) * 7.2)
        return Size(min(w, constraints.max_width),
                    min(26.0, constraints.max_height))

    def on_pointer_down(self, x: float, y: float) -> None:
        self._pressed = True

    def on_pointer_up(self, x: float, y: float) -> None:
        self._pressed = False

    def on_tap(self, x: float, y: float) -> None:
        if self.on_press is not None:
            self.on_press()

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        if self.accent:
            canvas.set_fill_color(*ACCENT_SOFT)
            canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 6)
            canvas.set_stroke_color(*ACCENT)
        else:
            canvas.set_fill_color(*(PANEL3 if self._pressed else PANEL2))
            canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 6)
            canvas.set_stroke_color(*BORDER_STRONG)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(rect.x, rect.y, rect.w, rect.h, 6)
        canvas.set_fill_color(*(ACCENT if self.accent else TEXT))
        canvas.set_font(FONT, 12, "medium")
        canvas.draw_text(self.label, rect.x + rect.w / 2,
                         _baseline(canvas, rect.y + rect.h / 2), "center")
        canvas.restore()


class ChromeLabel(Widget):
    """Static chrome text."""

    def __init__(self, widget_id: str, *, text: str = "",
                 font_size: float = 13, weight: str = "regular",
                 color: Tuple[int, int, int] = TEXT,
                 mono: bool = False, **kwargs: Any) -> None:
        super().__init__(widget_id, "chrome_label", **kwargs)
        self.text = text
        self.font_size = font_size
        self.weight = weight
        self.color = color
        self.mono = mono

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(len(self.text) * self.font_size * 0.62 + 8,
                        constraints.max_width),
                    min(self.font_size + 8, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.set_fill_color(*self.color)
        canvas.set_font(MONO if self.mono else FONT, self.font_size,
                        self.weight)
        canvas.draw_text(self.text, rect.x + 4,
                         _baseline(canvas, rect.y + rect.h / 2), "left")
        canvas.restore()


class FormInput(TextInputWidget):
    """TextInput whose commit reports to a callback (form field mode)."""

    def __init__(self, widget_id: str, *, on_commit=None, **kwargs: Any) -> None:
        super().__init__(widget_id, **kwargs)
        self.on_commit = on_commit
        self._last_committed: Optional[str] = None

    def set_text(self, text: str) -> None:
        self.text = str(text)
        self.cursor = len(self.text)
        self._last_committed = self.text

    def commit(self) -> None:
        if not self.text or self.text == self._last_committed:
            return
        self._last_committed = self.text
        self._set_status("✓", wstyle.GREEN)
        if self.on_commit is not None:
            try:
                self.on_commit(self.text)
            except Exception:
                logger.exception("form commit failed")


class LiveInput(FormInput):
    """FormInput that reports EVERY edit (live search / filter fields)."""

    def __init__(self, widget_id: str, *, on_change=None, **kwargs: Any) -> None:
        super().__init__(widget_id, **kwargs)
        self.on_change = on_change

    def _fire(self) -> None:
        if self.on_change is not None:
            try:
                self.on_change(self.text)
            except Exception:
                logger.exception("live input change failed")

    def on_text(self, text: str) -> None:
        super().on_text(text)
        self._fire()

    def on_key(self, key: str, modifiers: Any = None) -> None:
        super().on_key(key, modifiers)
        if key in ("Backspace", "Delete", "Escape"):
            self._fire()


# ---------------------------------------------------------------------------
# Widget Workshop
# ---------------------------------------------------------------------------

_RULER = 26.0
_HANDLE = 7.0
_HANDLE_TOL = 6.0
# handle name -> (fx, fy) fractional position on the selection rect
_HANDLES = {"nw": (0, 0), "n": (0.5, 0), "ne": (1, 0), "e": (1, 0.5),
            "se": (1, 1), "s": (0.5, 1), "sw": (0, 1), "w": (0, 0.5)}


class _PreviewCtx:
    """EngineContext for previewed widgets: the DESIGN bus ([DP-CXC-4]),
    the host app's assets/animator, host-thread scheduling."""

    def __init__(self, host_ctx: Any, design_bus: Any) -> None:
        self._host = host_ctx
        self.bus = design_bus
        self.assets = host_ctx.assets
        self.animator = host_ctx.animator
        self.invoke = None
        self.launch = None

    def request_redraw(self, rect: Any = None) -> None:
        # [CXC-R-DAMAGE] forward the widget's scoped paint bounds to the host
        # engine so a previewed widget redraws only its own region.
        self._host.request_redraw(rect)

    def publish(self, namespace: str, value: Any,
                source: str = "preview") -> None:
        self.bus.ensure_namespace(namespace, engine_config="json")
        self.bus.publish_state(namespace, value, source=source)

    def schedule_update(self, fn: Callable[[], None]) -> None:
        self._host.schedule_update(fn)

    # Clock-ticked widgets ([SPEC:CXC-LOTTIE]): previewed Lottie players
    # tick on the HOST animation loop; unmounting the stale preview tree
    # unregisters them (rebuild hygiene, like _behavior_insts).
    def register_clocked(self, widget: Any) -> None:
        reg = getattr(self._host, "register_clocked", None)
        if reg is not None:
            reg(widget)

    def unregister_clocked(self, widget: Any) -> None:
        unreg = getattr(self._host, "unregister_clocked", None)
        if unreg is not None:
            unreg(widget)


class WorkshopWidget(Container):
    """The WW: rulers + live engine-rendered composite + selection/drag."""

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "workshop", **kwargs)
        self.core = core
        self.interactive = True
        self.selected_id: str = ""
        self._preview: Optional[Container] = None
        self._preview_ctx: Optional[_PreviewCtx] = None
        self._behavior_insts: list = []                  # host-engine handles
        self._origin: Tuple[float, float] = (0.0, 0.0)   # composite (0,0) in surface px
        self._viewport: Optional[Rect] = None
        self._gen = 0                                    # stale-subscription guard
        # drag state
        self._mode: str = ""            # "" | move | resize
        self._handle: str = ""
        self._orig: Tuple[float, float, float, float] = (0, 0, 0, 0)
        self._ghost: Optional[Tuple[float, float, float, float]] = None

    @property
    def doc(self) -> CompositeDocument:
        return self.core.document

    # -- preview construction ------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        self.rebuild()

    def on_unmount(self) -> None:
        super().on_unmount()
        if self._preview is not None:
            self._preview.on_unmount()               # clocked-widget hygiene

    def rebuild(self) -> None:
        """Rebuild the live preview tree from the document.  The preview is
        REAL engine widgets parsed by the production parser — the Composer
        previews exactly what the runtime renders."""
        if self._ctx is None:
            return
        self._gen += 1
        gen = self._gen
        doc = self.doc
        host_behaviors = getattr(self._ctx, "behaviors", None)
        if host_behaviors is not None and self._behavior_insts:
            host_behaviors.remove(self._behavior_insts)   # stale preview
        self._behavior_insts = []
        if self._preview is not None:
            # stale-instance hygiene: unmount the old tree so clocked
            # widgets (Lottie) deregister from the host loop.
            self._preview.on_unmount()
        comp = Composition(root=None)                     # subscription sink
        # SINGLE SOURCE OF TRUTH: the preview parses the exact pixel_stack
        # spec that export writes — preview == deployed, by construction.
        # Document-level `variables:` are substituted exactly as the
        # deployed runtime's parse would ([CXC-BIND-VARS]).
        from ..render.engine.bindings import substitute_variables
        stack_spec = substitute_variables(doc.to_dict())["widgets"][0]
        stack = build_node(stack_spec, comp)

        self._preview = stack
        self._preview_ctx = _PreviewCtx(self._ctx, self.core.design_bus)
        stack.on_mount(self._preview_ctx)

        # design-bus liveness: previewed widgets follow their namespaces
        from composeio.bus import is_pattern
        for ns_name, widget in comp.subscriptions:

            def _cb(ns: str, new_val: Any, old_val: Any, _w=widget,
                    _g=gen) -> None:
                if _g != self._gen:
                    return                                # stale preview
                self._ctx.schedule_update(
                    lambda: _w.on_bus_state(ns, new_val))

            if is_pattern(ns_name):
                # [CXC-FL-PATTERN]: preview a family subscription exactly
                # as the deployed runtime wires it.
                self.core.design_bus.subscribe_pattern(ns_name, _cb)
                continue
            self.core.design_bus.ensure_namespace(ns_name, engine_config="json")
            self.core.design_bus.subscribe(ns_name, _cb)
            current = self.core.design_bus.get_state(ns_name)
            if current is not None:
                widget.on_bus_state(ns_name, current)

        # [SPEC:CXC-BIND] attribute bindings — live on the DESIGN bus, so
        # the WW previews exactly what a deployed runtime would show
        # ([CXC-BE-C1]/[CXC-BE-C2]).
        for b in comp.attr_bindings:

            def _bcb(ns: str, new_val: Any, old_val: Any, _b=b,
                     _g=gen) -> None:
                if _g != self._gen:
                    return
                self._ctx.schedule_update(
                    lambda: self._apply_preview_binding(_b, new_val))

            if is_pattern(b.namespace):
                self.core.design_bus.subscribe_pattern(b.namespace, _bcb)
                continue
            self.core.design_bus.ensure_namespace(b.namespace,
                                                  engine_config="json")
            self.core.design_bus.subscribe(b.namespace, _bcb)
            current = self.core.design_bus.get_state(b.namespace)
            if current is not None:
                self._apply_preview_binding(b, current)

        # design-time visibility: while a visible-bound namespace is silent
        # the LAYER's eye decides what the designer sees (a deployed widget
        # would be hidden until the namespace speaks).
        for ns_name, widget, when, invert in comp.visible_bindings:
            if self.core.design_bus.get_state(ns_name) is None:
                layer = doc.get_layer(widget.widget_id)
                widget.visible = layer.visible if layer is not None else True

        # [CXC-BIND-BEHAVIOR] behaviors — registered on the HOST engine so
        # they tick on the Composer's animation loop; triggers ride the
        # design bus.
        if host_behaviors is not None:
            for decl in comp.behaviors:
                inst = host_behaviors.add(decl)
                self._behavior_insts.append(inst)
                trig = decl.trigger
                if trig.kind == "always" or trig.namespace is None:
                    host_behaviors.set_active(inst, True)
                    continue

                def _tcb(ns: str, new_val: Any, old_val: Any, _inst=inst,
                         _g=gen) -> None:
                    if _g != self._gen:
                        return
                    self._ctx.schedule_update(
                        lambda: host_behaviors.set_active(
                            _inst, _inst.decl.trigger.matches(new_val)))

                if is_pattern(trig.namespace):
                    self.core.design_bus.subscribe_pattern(trig.namespace,
                                                           _tcb)
                    continue
                self.core.design_bus.ensure_namespace(trig.namespace,
                                                      engine_config="json")
                self.core.design_bus.subscribe(trig.namespace, _tcb)
                current = self.core.design_bus.get_state(trig.namespace)
                if current is not None:
                    host_behaviors.set_active(inst, trig.matches(current))

        if self.arranged_rect is not None:
            self.arrange(self.arranged_rect)
        self.request_redraw()

    def _apply_preview_binding(self, b: Any, raw: Any) -> None:
        from ..render.engine.bindings import apply_binding
        if b.widget is None:
            return
        if apply_binding(b.widget, b, raw):
            host_behaviors = getattr(self._ctx, "behaviors", None)
            if host_behaviors is not None:
                host_behaviors.notify_base_change(
                    b.widget, b.attr, getattr(b.widget, b.attr))
            self.request_redraw()

    # -- layout ------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        vp = Rect(inner.x + _RULER, inner.y + _RULER,
                  max(0.0, inner.w - _RULER), max(0.0, inner.h - _RULER))
        self._viewport = vp
        doc = self.doc
        cx = vp.x + max(12.0, (vp.w - doc.width) / 2)
        cy = vp.y + max(12.0, (vp.h - doc.height) / 2)
        self._origin = (cx, cy)
        if self._preview is not None:
            self._preview.measure(Constraints(doc.width, doc.height))
            self._preview.arrange(Rect(cx, cy, doc.width, doc.height))
        self.children = [self._preview] if self._preview is not None else []

    # -- geometry helpers -------------------------------------------------------

    def _layer_rect(self, layer: LayerDef) -> Rect:
        """Layer bounds in SURFACE px (falls back to the previewed child's
        arranged rect when w/h are natural)."""
        ox, oy = self._origin
        w, h = layer.w, layer.h
        if (w is None or h is None) and self._preview is not None:
            for ch in self._preview.children:
                if ch.widget_id == layer.widget_id and ch.arranged_rect:
                    w = w if w is not None else ch.arranged_rect.w
                    h = h if h is not None else ch.arranged_rect.h
        return Rect(ox + layer.x, oy + layer.y,
                    w if w is not None else 40.0,
                    h if h is not None else 24.0)

    def _selected_rotation(self) -> float:
        """Live rotation of the selected previewed widget (deg, 0 if none)
        — reads the PREVIEW so behavior-driven rotation shows too."""
        if not self.selected_id or self._preview is None:
            return 0.0
        for ch in self._preview.children:
            if ch.widget_id == self.selected_id:
                try:
                    return float(getattr(ch, "rotation", 0.0) or 0.0) % 360.0
                except (TypeError, ValueError):
                    return 0.0
        return 0.0

    def _selection_rect(self) -> Optional[Rect]:
        if self._ghost is not None:
            ox, oy = self._origin
            gx, gy, gw, gh = self._ghost
            return Rect(ox + gx, oy + gy, gw, gh)
        layer = self.doc.get_layer(self.selected_id)
        if layer is None:
            return None
        return self._layer_rect(layer)

    def _handle_at(self, x: float, y: float) -> str:
        r = self._selection_rect()
        if r is None:
            return ""
        for name, (fx, fy) in _HANDLES.items():
            hx = r.x + fx * r.w
            hy = r.y + fy * r.h
            if abs(x - hx) <= _HANDLE_TOL and abs(y - hy) <= _HANDLE_TOL:
                return name
        return ""

    def _layer_at(self, x: float, y: float) -> Optional[LayerDef]:
        """Topmost visible layer under a surface point (z = list order)."""
        for layer in reversed(self.doc.layers):
            if not layer.visible:
                continue
            if _contains(self._layer_rect(layer), x, y):
                return layer
        return None

    # -- selection ----------------------------------------------------------

    def select(self, widget_id: str) -> None:
        if widget_id == self.selected_id:
            return
        self.selected_id = widget_id
        ib = self.core.internal_bus
        ib.publish_state("composer/selection/widget_id", widget_id or "",
                         source="workshop")
        ib.publish_state("composer/selection/type",
                         "widget" if widget_id else "none", source="workshop")
        self.request_redraw()

    # -- interaction (select tool) ---------------------------------------------

    def hit_test_tree(self, x: float, y: float) -> Optional[Widget]:
        """Select mode: the workshop captures everything over itself —
        previewed widgets are edited, not actuated."""
        r = self.arranged_rect
        if r is not None and _contains(r, x, y):
            return self
        return None

    def on_pointer_down(self, x: float, y: float) -> None:
        self._mode = ""
        self._ghost = None
        if self.selected_id:
            h = self._handle_at(x, y)
            if h:
                layer = self.doc.get_layer(self.selected_id)
                if layer is not None:
                    r = self._layer_rect(layer)
                    ox, oy = self._origin
                    self._mode, self._handle = "resize", h
                    self._orig = (r.x - ox, r.y - oy, r.w, r.h)
                    return
        layer = self._layer_at(x, y)
        if layer is not None:
            self.select(layer.widget_id)
            r = self._layer_rect(layer)
            ox, oy = self._origin
            self._mode = "move"
            self._orig = (r.x - ox, r.y - oy, r.w, r.h)
        else:
            self.select("")

    def on_pan(self, dx: float, dy: float) -> None:
        if not self._mode or not self.selected_id:
            return
        x0, y0, w0, h0 = self._orig
        if self._mode == "move":
            self._ghost = (x0 + dx, y0 + dy, w0, h0)
        else:
            fx, fy = _HANDLES[self._handle]
            nx, ny, nw, nh = x0, y0, w0, h0
            if fx == 0:
                nx, nw = x0 + dx, w0 - dx
            elif fx == 1:
                nw = w0 + dx
            if fy == 0:
                ny, nh = y0 + dy, h0 - dy
            elif fy == 1:
                nh = h0 + dy
            self._ghost = (nx, ny, max(8.0, nw), max(8.0, nh))
        self._apply_ghost_to_preview()
        self.request_redraw()

    def _apply_ghost_to_preview(self) -> None:
        """Live visual feedback during drag (document untouched)."""
        if self._ghost is None or self._preview is None:
            return
        gx, gy, gw, gh = self._ghost
        for ch in self._preview.children:
            if ch.widget_id == self.selected_id:
                ch.stack_x, ch.stack_y = gx, gy
                ch.stack_w, ch.stack_h = gw, gh
                self._preview.arrange(self._preview.arranged_rect)
                return

    def on_pan_end(self) -> None:
        if self._ghost is None or not self.selected_id:
            self._mode = ""
            return
        gx, gy, gw, gh = self._ghost
        data = {"widget_id": self.selected_id,
                "x": round(gx), "y": round(gy)}
        if self._mode == "resize":
            data.update({"w": round(gw), "h": round(gh)})
            ns = "composer/canvas/widget_resized"
        else:
            ns = "composer/canvas/widget_moved"
        self._ghost = None
        self._mode = ""
        self.core.internal_bus.publish_event(ns, "do", data=data,
                                             source="workshop")

    def on_key(self, key: str, modifiers: Any = None) -> None:
        if not self.selected_id:
            return
        layer = self.doc.get_layer(self.selected_id)
        if layer is None:
            return
        ib = self.core.internal_bus
        if key in ("Backspace", "Delete"):
            wid = self.selected_id
            self.select("")
            ib.publish_event("composer/canvas/widget_removed", "do",
                             data={"widget_id": wid}, source="workshop")
        elif key in ("Left", "Right", "Up", "Down"):
            dx = {"Left": -1, "Right": 1}.get(key, 0)
            dy = {"Up": -1, "Down": 1}.get(key, 0)
            ib.publish_event("composer/canvas/widget_moved", "do",
                             data={"widget_id": self.selected_id,
                                   "x": layer.x + dx, "y": layer.y + dy},
                             source="workshop")

    # -- draw --------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        vp = self._viewport or rect
        ox, oy = self._origin
        doc = self.doc

        # viewport
        canvas.set_fill_color(*CANVAS_BG)
        canvas.fill_rect(vp.x, vp.y, vp.w, vp.h)

        # rulers
        self._draw_rulers(canvas, rect, vp, ox, oy, doc)

        # composite frame shadow line
        canvas.set_stroke_color(0xFF, 0xFF, 0xFF, 0x22)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(ox - 1, oy - 1, doc.width + 2, doc.height + 2)

        # the real engine-rendered composite
        canvas.save()
        canvas.clip_rect(vp.x, vp.y, vp.w, vp.h)
        if self._preview is not None and self._preview.arranged_rect:
            self._preview.draw(canvas, self._preview.arranged_rect)

        # selection chrome
        sel = self._selection_rect()
        if sel is not None:
            # [CXC-BE-EDGES] rotated-selection hint: the selection/hit
            # target is the AXIS-ALIGNED bounds; when the widget is
            # rotated, show its actual orientation faintly + say so.
            rot = self._selected_rotation()
            if rot:
                cx, cy = sel.x + sel.w / 2, sel.y + sel.h / 2
                rad = math.radians(rot)
                cr, sr = math.cos(rad), math.sin(rad)
                pts = []
                for px, py in ((sel.x, sel.y), (sel.x + sel.w, sel.y),
                               (sel.x + sel.w, sel.y + sel.h),
                               (sel.x, sel.y + sel.h)):
                    dx, dy = px - cx, py - cy
                    pts.append((cx + dx * cr - dy * sr,
                                cy + dx * sr + dy * cr))
                canvas.set_stroke_color(*ACCENT, 0x66)
                canvas.set_stroke_width(1)
                for i in range(4):
                    x0, y0 = pts[i]
                    x1, y1 = pts[(i + 1) % 4]
                    canvas.draw_line(x0, y0, x1, y1)
                canvas.set_fill_color(*DIM)
                canvas.set_font(MONO, 9.5, "regular")
                canvas.draw_text(
                    f"rotated {rot:.0f}° — selects by upright bounds",
                    sel.x, sel.y + sel.h + 16, "left")
            canvas.set_stroke_color(*ACCENT)
            canvas.set_stroke_width(1.5)
            canvas.stroke_rect(sel.x - 2, sel.y - 2, sel.w + 4, sel.h + 4)
            for fx, fy in _HANDLES.values():
                hx = sel.x + fx * sel.w - _HANDLE / 2
                hy = sel.y + fy * sel.h - _HANDLE / 2
                canvas.set_fill_color(*PANEL)
                canvas.fill_rect(hx, hy, _HANDLE, _HANDLE)
                canvas.set_stroke_color(*ACCENT)
                canvas.set_stroke_width(1.5)
                canvas.stroke_rect(hx, hy, _HANDLE, _HANDLE)
        canvas.restore()

        # zoom chip
        chip_w, chip_h = 52.0, 20.0
        zx, zy = vp.x + vp.w - chip_w - 10, vp.y + vp.h - chip_h - 10
        canvas.set_fill_color(*PANEL)
        canvas.fill_round_rect(zx, zy, chip_w, chip_h, 6)
        canvas.set_stroke_color(*BORDER)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(zx, zy, chip_w, chip_h, 6)
        canvas.set_fill_color(*DIM)
        canvas.set_font(MONO, 11, "regular")
        canvas.draw_text("100 %", zx + chip_w / 2,
                         _baseline(canvas, zy + chip_h / 2), "center")
        canvas.restore()

    def _draw_rulers(self, canvas: Canvas, rect: Rect, vp: Rect,
                     ox: float, oy: float, doc: CompositeDocument) -> None:
        # ruler strips
        canvas.set_fill_color(*RULER_BG)
        canvas.fill_rect(rect.x, rect.y, rect.w, _RULER)               # horizontal
        canvas.fill_rect(rect.x, rect.y, _RULER, rect.h)               # vertical
        canvas.set_stroke_color(*BORDER)
        canvas.set_stroke_width(1)
        canvas.draw_line(rect.x, rect.y + _RULER, rect.x + rect.w,
                         rect.y + _RULER)
        canvas.draw_line(rect.x + _RULER, rect.y, rect.x + _RULER,
                         rect.y + rect.h)
        # corner
        canvas.set_fill_color(*FAINT)
        canvas.set_font(MONO, 9, "regular")
        canvas.draw_text("px", rect.x + _RULER / 2,
                         _baseline(canvas, rect.y + _RULER / 2), "center")

        # ticks: 0 at the composite corner ([CXC-CORPUS-UX] pre-vis)
        canvas.set_font(MONO, 9, "regular")
        for p in range(0, int(doc.width) + 1, 10):
            x = ox + p
            if x < vp.x or x > vp.x + vp.w:
                continue
            major = p % 100 == 0
            mid = p % 50 == 0
            tick_h = 9 if major else (7 if mid else 5)
            canvas.set_stroke_color(*(TICK_STRONG if major else TICK))
            canvas.draw_line(x, rect.y + _RULER - tick_h, x,
                             rect.y + _RULER - 1)
            if major:
                canvas.set_fill_color(*FAINT)
                canvas.draw_text(str(p), x + 3, rect.y + 11, "left")
        for p in range(0, int(doc.height) + 1, 10):
            y = oy + p
            if y < vp.y or y > vp.y + vp.h:
                continue
            major = p % 100 == 0
            mid = p % 50 == 0
            tick_w = 9 if major else (7 if mid else 5)
            canvas.set_stroke_color(*(TICK_STRONG if major else TICK))
            canvas.draw_line(rect.x + _RULER - tick_w, y,
                             rect.x + _RULER - 1, y)
            if major:
                canvas.set_fill_color(*FAINT)
                canvas.draw_text(str(p), rect.x + 3, y + 10, "left")


# ---------------------------------------------------------------------------
# WC panels
# ---------------------------------------------------------------------------

class _RowListWidget(Widget):
    """Base for scrollable row-list panels (layers, catalog, namespaces)."""

    ROW_H = 46.0

    def __init__(self, widget_id: str, widget_type: str, core: Any,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, widget_type, **kwargs)
        self.core = core
        self.interactive = True
        self.scroll = 0.0

    @property
    def doc(self) -> CompositeDocument:
        return self.core.document

    def row_count(self) -> int:
        return 0

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_scroll(self, dx: float, dy: float) -> None:
        max_scroll = max(0.0, self.row_count() * self.ROW_H -
                         (self.arranged_rect.h if self.arranged_rect else 200))
        self.scroll = max(0.0, min(max_scroll, self.scroll - dy * 24))
        self.request_redraw()

    def _row_at(self, y: float) -> int:
        r = self.arranged_rect
        if r is None:
            return -1
        i = int((y - r.y - self._header_h() + self.scroll) // self.ROW_H)
        return i if 0 <= i < self.row_count() else -1

    def _header_h(self) -> float:
        return 0.0


class LayersPanel(_RowListWidget):
    """The Layers tab: z-order list, front (drawn last) at the top.
    Tap = select · eye = visibility · arrows = reorder — all bus commands."""

    ROW_H = 46.0

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "layers_panel", core, **kwargs)

    def row_count(self) -> int:
        return len(self.doc.layers)

    def _header_h(self) -> float:
        return 22.0

    # rows display front->back: display index d -> layer index n-1-d
    def _layer_for_row(self, d: int) -> LayerDef:
        return self.doc.layers[len(self.doc.layers) - 1 - d]

    def on_tap(self, x: float, y: float) -> None:
        d = self._row_at(y)
        if d < 0:
            return
        layer = self._layer_for_row(d)
        r = self.arranged_rect
        z = self.doc.layer_index(layer.widget_id)
        ib = self.core.internal_bus
        # zone hits from the right edge: [up] [down] [eye]
        right = r.x + r.w
        if right - 30 <= x <= right - 6:                     # eye
            ib.publish_event("composer/config/widget_changed", "do",
                             data={"widget_id": layer.widget_id,
                                   "property": "visible",
                                   "value": not layer.visible},
                             source="layers_panel")
            return
        if right - 55 <= x <= right - 35:                    # down (backward)
            if z > 0:
                ib.publish_event("composer/config/widget_changed", "do",
                                 data={"widget_id": layer.widget_id,
                                       "property": "z_index", "value": z - 1},
                                 source="layers_panel")
            return
        if right - 76 <= x <= right - 56:                    # up (forward)
            if z < len(self.doc.layers) - 1:
                ib.publish_event("composer/config/widget_changed", "do",
                                 data={"widget_id": layer.widget_id,
                                       "property": "z_index", "value": z + 1},
                                 source="layers_panel")
            return
        # row body: select
        ib.publish_state("composer/selection/widget_id", layer.widget_id,
                         source="layers_panel")
        ib.publish_state("composer/selection/type", "widget",
                         source="layers_panel")

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*FAINT)
        canvas.set_font(MONO, 10, "regular")
        canvas.draw_text("front — drawn last · on top",
                         rect.x + rect.w / 2, rect.y + 14, "center")
        selected = self.core.internal_bus.get_state(
            "composer/selection/widget_id") or ""
        y = rect.y + self._header_h() - self.scroll
        for d in range(self.row_count()):
            layer = self._layer_for_row(d)
            ry = y + d * self.ROW_H
            if ry + self.ROW_H < rect.y or ry > rect.y + rect.h:
                continue
            self._draw_row(canvas, rect, ry, layer,
                           layer.widget_id == selected)
        fy = rect.y + self._header_h() - self.scroll + \
            self.row_count() * self.ROW_H + 12
        canvas.set_fill_color(*FAINT)
        canvas.set_font(MONO, 10, "regular")
        canvas.draw_text("back — drawn first · background",
                         rect.x + rect.w / 2, fy, "center")
        canvas.restore()

    def _draw_row(self, canvas: Canvas, rect: Rect, ry: float,
                  layer: LayerDef, selected: bool) -> None:
        rx, rw = rect.x + 4, rect.w - 8
        if selected:
            canvas.set_fill_color(*ACCENT_SOFT)
            canvas.fill_round_rect(rx, ry + 2, rw, self.ROW_H - 4, 7)
        canvas.set_stroke_color(*BORDER)
        canvas.set_stroke_width(1)
        canvas.draw_line(rx + 6, ry + self.ROW_H, rx + rw - 6,
                         ry + self.ROW_H)

        alpha_c = TEXT if layer.visible else FAINT
        # type chip
        canvas.set_fill_color(*PANEL3)
        canvas.fill_round_rect(rx + 8, ry + 11, 24, 24, 5)
        canvas.set_stroke_color(*BORDER)
        canvas.stroke_round_rect(rx + 8, ry + 11, 24, 24, 5)
        canvas.set_fill_color(*ACCENT)
        canvas.set_font(MONO, 11, "medium")
        canvas.draw_text(layer.widget_type[:1].upper(), rx + 20,
                         _baseline(canvas, ry + 23), "center")
        # name + caption
        canvas.set_fill_color(*alpha_c)
        canvas.set_font(FONT, 12.5, "medium")
        canvas.draw_text(layer.widget_id, rx + 40, ry + 20, "left")
        canvas.set_fill_color(*FAINT)
        canvas.set_font(MONO, 10, "regular")
        wtxt = f"{int(layer.w)}×{int(layer.h)}" \
            if layer.w is not None and layer.h is not None else "auto"
        cap = f"{layer.widget_type} · {wtxt} @ {int(layer.x)},{int(layer.y)}"
        if not layer.visible:
            cap += " · hidden"
        canvas.draw_text(cap, rx + 40, ry + 35, "left")

        # controls: up-triangle (forward) · down-triangle (backward) · eye
        right = rect.x + rect.w
        canvas.set_fill_color(*DIM)
        ty = ry + 23
        canvas.fill_path([MoveTo(right - 71, ty + 4), LineTo(right - 66, ty - 4),
                          LineTo(right - 61, ty + 4), Close()])
        canvas.fill_path([MoveTo(right - 50, ty - 4), LineTo(right - 45, ty + 4),
                          LineTo(right - 40, ty - 4), Close()])
        cx, cy = right - 18, ry + 23
        canvas.set_stroke_color(*(DIM if layer.visible else FAINT))
        canvas.set_stroke_width(1.4)
        canvas.stroke_circle(cx, cy, 5.5)
        if layer.visible:
            canvas.set_fill_color(*DIM)
            canvas.fill_circle(cx, cy, 2.2)
        else:
            canvas.draw_line(cx - 7, cy - 7, cx + 7, cy + 7)


class CatalogPanel(_RowListWidget):
    """The Widgets tab: the extensible catalog; tap adds to the composite.
    ``filter`` narrows by name/type/description ([CXC-BE-POLISH] search)."""

    ROW_H = 50.0

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "catalog_panel", core, **kwargs)
        self.filter = ""

    def set_filter(self, text: str) -> None:
        self.filter = str(text or "").strip().lower()
        self.scroll = 0.0
        self.request_redraw()

    def _entries(self) -> list:
        entries = catalog_entries()
        if not self.filter:
            return entries
        f = self.filter
        return [e for e in entries
                if f in e.name.lower() or f in e.type_key.lower()
                or f in e.description.lower()]

    def row_count(self) -> int:
        return len(self._entries())

    def on_tap(self, x: float, y: float) -> None:
        i = self._row_at(y)
        if i < 0:
            return
        entry = self._entries()[i]
        doc = self.doc
        w, h = entry.size
        data = {
            "widget_type": entry.type_key,
            "module_id": "composite",
            "properties": dict(entry.defaults),
            "x": max(0, round((doc.width - (w or 120)) / 2)),
            "y": max(0, round((doc.height - (h or 60)) / 2)),
        }
        if w:
            data["w"] = w
        if h:
            data["h"] = h
        self.core.internal_bus.publish_event(
            "composer/canvas/widget_added", "do", data=data,
            source="catalog_panel")

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        y = rect.y - self.scroll
        for i, entry in enumerate(self._entries()):
            ry = y + i * self.ROW_H
            if ry + self.ROW_H < rect.y or ry > rect.y + rect.h:
                continue
            rx, rw = rect.x + 4, rect.w - 8
            canvas.set_fill_color(*PANEL2)
            canvas.fill_round_rect(rx, ry + 3, rw, self.ROW_H - 7, 9)
            canvas.set_stroke_color(*BORDER)
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(rx, ry + 3, rw, self.ROW_H - 7, 9)
            # icon block
            canvas.set_fill_color(*PANEL)
            canvas.fill_round_rect(rx + 9, ry + 9, 30, 30, 7)
            canvas.set_stroke_color(*BORDER)
            canvas.stroke_round_rect(rx + 9, ry + 9, 30, 30, 7)
            canvas.set_fill_color(*ACCENT)
            canvas.set_font(MONO, 13, "medium")
            canvas.draw_text(entry.name[:1], rx + 24,
                             _baseline(canvas, ry + 24), "center")
            canvas.set_fill_color(*TEXT)
            canvas.set_font(FONT, 12.5, "medium")
            canvas.draw_text(entry.name, rx + 48, ry + 21, "left")
            canvas.set_fill_color(*DIM)
            canvas.set_font(FONT, 11, "regular")
            canvas.draw_text(entry.description, rx + 48, ry + 36, "left")
            canvas.set_fill_color(*FAINT)
            canvas.set_font(FONT, 15, "regular")
            canvas.draw_text("+", rx + rw - 16,
                             _baseline(canvas, ry + 24), "center")
        canvas.restore()


class OverviewPanel(Container):
    """The Overview tab: widget name, canvas W/H, namespaces (+ new),
    bindings summary, purpose.  Form fields are engine TextInputs; each
    commit is a bus command."""

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "overview_panel", **kwargs)
        self.core = core
        self.interactive = True

        def _design_prop(prop):
            def commit(value):
                core.internal_bus.publish_event(
                    "composer/config/widget_changed", "do",
                    data={"property": prop, "value": value},
                    source="overview_panel")
            return commit

        self.in_name = FormInput("ov_name", label="WIDGET NAME",
                                 on_commit=_design_prop("name"))
        self.in_w = FormInput("ov_w", label="WIDTH · PX",
                              on_commit=_design_prop("width"))
        self.in_h = FormInput("ov_h", label="HEIGHT · PX",
                              on_commit=_design_prop("height"))
        self.in_purpose = FormInput("ov_purpose", label="PURPOSE",
                                    on_commit=_design_prop("purpose"))
        self.add(self.in_name)
        self.add(self.in_w)
        self.add(self.in_h)
        self.add(self.in_purpose)
        self._ns_button_rect: Optional[Rect] = None
        self.refresh()

    @property
    def doc(self) -> CompositeDocument:
        return self.core.document

    def refresh(self) -> None:
        d = self.doc
        self.in_name.set_text(d.name)
        self.in_w.set_text(str(d.width))
        self.in_h.set_text(str(d.height))
        self.in_purpose.set_text(d.purpose)

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        x, y, w = inner.x + 4, inner.y + 6, inner.w - 8
        self.in_name.arranged_rect = Rect(x, y, w, 56)
        half = (w - 10) / 2
        self.in_w.arranged_rect = Rect(x, y + 62, half, 56)
        self.in_h.arranged_rect = Rect(x + half + 10, y + 62, half, 56)
        # namespaces + bindings drawn between; purpose parked at the bottom
        self.in_purpose.arranged_rect = Rect(x, inner.y + inner.h - 62, w, 56)
        self._lists_top = y + 128

    def on_tap(self, x: float, y: float) -> None:
        b = self._ns_button_rect
        if b is not None and _contains(b, x, y):
            n = len(self.doc.namespaces) + 1
            self.core.internal_bus.publish_event(
                "composer/config/namespace_changed", "do",
                data={"action": "created",
                      "name": f"{self.doc.slug}/ns_{n}",
                      "state_engine": "json"},
                source="overview_panel")

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        for ch in self.children:
            if ch.arranged_rect is not None:
                ch.draw(canvas, ch.arranged_rect)

        x = rect.x + 8
        w = rect.w - 16
        y = self._lists_top

        # -- namespaces section
        canvas.set_fill_color(*FAINT)
        canvas.set_font(FONT, 10.5, "medium")
        canvas.draw_text("NAMESPACES", x, y + 10, "left")
        bw, bh = 52.0, 20.0
        self._ns_button_rect = Rect(x + w - bw, y - 4, bw, bh)
        canvas.set_fill_color(*ACCENT_SOFT)
        canvas.fill_round_rect(x + w - bw, y - 4, bw, bh, 6)
        canvas.set_fill_color(*ACCENT)
        canvas.set_font(FONT, 11, "medium")
        canvas.draw_text("+ New", x + w - bw / 2,
                         _baseline(canvas, y - 4 + bh / 2), "center")
        y += 18
        canvas.set_stroke_color(*BORDER)
        canvas.set_stroke_width(1)
        for ns in self.doc.namespaces[:6]:
            canvas.set_fill_color(*PANEL2)
            canvas.fill_round_rect(x, y, w, 24, 6)
            canvas.set_fill_color(*TEXT)
            canvas.set_font(MONO, 11, "regular")
            canvas.draw_text(ns.name, x + 8, _baseline(canvas, y + 12), "left")
            eng = (ns.state_engine if isinstance(ns.state_engine, str)
                   else ns.state_engine.get("type", "enum"))
            canvas.set_fill_color(*DIM)
            canvas.set_font(MONO, 9.5, "regular")
            canvas.draw_text(str(eng), x + w - 8,
                             _baseline(canvas, y + 12), "right")
            y += 28
        if not self.doc.namespaces:
            canvas.set_fill_color(*FAINT)
            canvas.set_font(FONT, 11, "regular")
            canvas.draw_text("none yet", x + 4, y + 12, "left")
            y += 24

        # -- bindings section
        y += 10
        canvas.set_fill_color(*FAINT)
        canvas.set_font(FONT, 10.5, "medium")
        canvas.draw_text("BINDINGS", x, y + 10, "left")
        y += 18
        rows = self.doc.bindings()
        for b in rows[:6]:
            canvas.set_fill_color(*PANEL2)
            canvas.fill_round_rect(x, y, w, 22, 6)
            canvas.set_font(MONO, 10.5, "regular")
            canvas.set_fill_color(*TEXT)
            if b["kind"] == "subscribe":
                txt = f"{b['namespace']} → {b['widget']}"
            elif b["kind"] == "visible":
                txt = f"{b['namespace']} → {b['widget']}.visible"
            elif b["kind"] == "attr":
                txt = f"{b['namespace']} → {b['widget']}.{b['attr']}"
            elif b["kind"] == "behavior":
                txt = f"{b['namespace']} » {b['widget']} {b['behavior']}"
            else:
                txt = f"{b['widget']} → {b['namespace']}"
            canvas.draw_text(txt, x + 8, _baseline(canvas, y + 11), "left")
            y += 26
        if not rows:
            canvas.set_fill_color(*FAINT)
            canvas.set_font(FONT, 11, "regular")
            canvas.draw_text("none yet", x + 4, y + 12, "left")
        canvas.restore()


# ---------------------------------------------------------------------------
# WC tab host (bus-driven tab switching)
# ---------------------------------------------------------------------------

class BusTabWidget(TabWidget):
    """TabWidget whose active tab is BUS STATE (composer/wc/active_tab):
    a human tap and an Intelligence publish are the same operation."""

    TAB_KEYS = ["overview", "layers", "widgets", "bindings", "source",
                "flow"]

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, **kwargs)
        self.core = core

    def set_active(self, index: int) -> None:
        prev = self.active_index
        super().set_active(index)
        if self.active_index != prev:
            key = self.TAB_KEYS[self.active_index]
            self.core.internal_bus.publish_state(
                "composer/wc/active_tab", key, source="wc_tabs")

    def set_active_key(self, key: str) -> None:
        if key in self.TAB_KEYS:
            idx = self.TAB_KEYS.index(key)
            if idx != self.active_index:
                self.active_index = idx
                for i, ch in enumerate(self.children):
                    ch.visible = (i == idx)
                if self.arranged_rect is not None:
                    self.arrange(self.arranged_rect)
                self.request_redraw()


# ---------------------------------------------------------------------------
# Telemetry / status bar
# ---------------------------------------------------------------------------

class StatusBarWidget(Widget):
    """Spanning telemetry bar: selection status · Bus (opens the Bus
    Monitor) · MQTT · UART."""

    def __init__(self, widget_id: str, core: Any, *,
                 on_bus_click: Optional[Callable[[], None]] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "status_bar", **kwargs)
        self.core = core
        self.on_bus_click = on_bus_click
        self.interactive = True
        self._bus_zone: Optional[Rect] = None

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, min(34.0, constraints.max_height))

    def on_tap(self, x: float, y: float) -> None:
        z = self._bus_zone
        if z is not None and _contains(z, x, y) and self.on_bus_click:
            self.on_bus_click()
            self.core.internal_bus.publish_state(
                "composer/window/open", {"window": "bus_monitor"},
                source="status_bar")

    def _selection_text(self) -> str:
        wid = self.core.internal_bus.get_state("composer/selection/widget_id")
        if not wid:
            return "no selection"
        layer = self.core.document.get_layer(str(wid))
        if layer is None:
            return str(wid)
        z = self.core.document.layer_index(layer.widget_id)
        wtxt = (f"{int(layer.w)}×{int(layer.h)}"
                if layer.w is not None and layer.h is not None else "auto")
        return (f"{layer.widget_id} · {wtxt} px @ "
                f"{int(layer.x)},{int(layer.y)} · z {z}")

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.set_fill_color(*PANEL)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*BORDER)
        canvas.set_stroke_width(1)
        canvas.draw_line(rect.x, rect.y, rect.x + rect.w, rect.y)
        cy = rect.y + rect.h / 2

        # selection
        canvas.set_fill_color(*DIM)
        canvas.set_font(FONT, 11, "regular")
        canvas.draw_text("Selected", rect.x + 12, _baseline(canvas, cy), "left")
        canvas.set_fill_color(*TEXT)
        canvas.set_font(MONO, 11, "regular")
        canvas.draw_text(self._selection_text(), rect.x + 68,
                         _baseline(canvas, cy), "left")

        # clusters from the right: UART · MQTT · Bus
        x = rect.x + rect.w

        def cluster(label: str, led, extra: str = "") -> float:
            nonlocal x
            wtxt = 46 + len(label) * 7 + len(extra) * 6.8
            x -= wtxt
            canvas.set_stroke_color(*BORDER)
            canvas.draw_line(x, rect.y + 4, x, rect.y + rect.h - 4)
            canvas.set_fill_color(*FAINT)
            canvas.set_font(FONT, 10.5, "medium")
            canvas.draw_text(label, x + 12, _baseline(canvas, cy), "left")
            lx = x + 14 + len(label) * 7
            canvas.set_fill_color(*led)
            canvas.fill_circle(lx + 6, cy, 4)
            if extra:
                canvas.set_fill_color(*DIM)
                canvas.set_font(MONO, 10.5, "regular")
                canvas.draw_text(extra, lx + 16, _baseline(canvas, cy), "left")
            return x

        import os as _os
        broker = _os.environ.get("COMPOSEUX_MQTT_BROKER", "")
        uart_x = cluster("UART", WARN, "n/c")
        mqtt_ok = getattr(self.core, "mqtt_connected", False)
        mqtt_x = cluster("MQTT", GOOD if mqtt_ok else FAINT,
                         broker if broker else "offline")
        # live namespace count from bus discovery ([SPEC:CXC-FLOW] previs:
        # "live · 61 ns")
        disc = getattr(self.core, "discovery", None)
        bus_txt = "live"
        if disc is not None and len(disc):
            bus_txt = f"live · {len(disc)} ns"
        bus_x = cluster("Bus", GOOD, bus_txt)
        self._bus_zone = Rect(bus_x, rect.y, mqtt_x - bus_x, rect.h)
        canvas.restore()
