"""
ComposeUX Drag-to-Value interaction seam ([SPEC:CXC-PRIM] [CXC-PRIM-DRAGVAL]).

The slider's *visual* is a pure pixel-stack composite (track + fill + thumb,
thumb.x / fill.w are ordinary bound expressions on the published value).
What is genuinely new is the INPUT: mapping a pointer drag along a widget's
axis to a ``[min, max]`` value (respecting ``step``) and publishing it to a
namespace.  That is this module.

Design ([CXC-PRIM-DRAGVAL]):

  * State on the bus, behavior in the widget.  The controller holds NO
    visual state of its own — it only PUBLISHES the value.  The thumb is
    bound to that published value, so an Intelligence that publishes the
    value directly moves the thumb byte-identically to a finger drag that
    ends on the same value ([CXC-PRIM-C6], the determinism/peer-parity
    property).

  * Reusable + backend-agnostic.  Any widget can become a drag surface by
    carrying a controller (``widget._drag_value``); the Widget base class
    delegates its pointer/pan hooks to it (widget.py).  The runtime's
    existing pan dispatch ([CXC-RM-M1], runtime ``_dispatch_pointer``)
    already delivers on_pointer_down / on_pan / on_pointer_up, so nothing
    new is needed in the event pipeline.

  * Two modes:
      live       — publish on pointer-down and on every on_pan (the thumb
                   follows the finger, because it is bound to the value).
      on_release — publish once, on pointer-up (the value commits at the
                   end of the gesture; the thumb jumps there).

Orientation: ``horizontal`` maps left->min, right->max; ``vertical`` maps
bottom->min, top->max (screen y grows downward).  ``inset`` (px) shrinks
the mapped span on both ends so a pointer at the track edge maps to the
thumb *centre* at the same edge, keeping pointer<->thumb registration exact.
"""

from __future__ import annotations

from typing import Any, Optional


def _quantize(v: float, vmin: float, vmax: float, step: float) -> float:
    """Snap *v* to the nearest ``vmin + k*step`` and clamp to [vmin, vmax]."""
    if step and step > 0:
        steps = round((v - vmin) / step)
        v = vmin + steps * step
    if vmin <= vmax:
        return min(vmax, max(vmin, v))
    return max(vmax, min(vmin, v))


class DragValueController:
    """Maps pointer position along a widget's axis to a published value.

    Attach to a widget as ``widget._drag_value``; the Widget base class
    forwards on_pointer_down / on_pan / on_pointer_up here.
    """

    def __init__(self, namespace: str, *,
                 vmin: float = 0.0, vmax: float = 1.0, step: float = 0.0,
                 orient: str = "horizontal", mode: str = "live",
                 inset: float = 0.0) -> None:
        self.namespace = str(namespace)
        self.vmin = float(vmin)
        self.vmax = float(vmax)
        self.step = float(step or 0.0)
        self.orient = str(orient or "horizontal").lower()
        # accept live / on_release (and a few friendly spellings)
        m = str(mode or "live").lower()
        self.mode = "on_release" if m in ("on_release", "release",
                                          "on-release") else "live"
        self.inset = float(inset or 0.0)
        self._down: Optional[tuple] = None
        self._pending: Optional[float] = None

    # -- widget-delegated pointer hooks --------------------------------------

    def begin(self, widget: Any, x: float, y: float) -> None:
        self._down = (x, y)
        v = self.value_at(widget, x, y)
        self._pending = v
        if self.mode == "live":
            self._publish(widget, v)

    def update(self, widget: Any, dx: float, dy: float) -> None:
        if self._down is None:
            return
        x = self._down[0] + dx
        y = self._down[1] + dy
        v = self.value_at(widget, x, y)
        self._pending = v
        if self.mode == "live":
            self._publish(widget, v)

    def finish(self, widget: Any, x: Optional[float] = None,
               y: Optional[float] = None) -> None:
        # Recompute at the release point when we have it (most accurate),
        # else use the last pending value.
        if x is not None and y is not None and self._down is not None:
            self._pending = self.value_at(widget, x, y)
        if self.mode == "on_release" and self._pending is not None:
            self._publish(widget, self._pending)
        self._down = None

    # -- mapping -------------------------------------------------------------

    def value_at(self, widget: Any, x: float, y: float) -> float:
        r = getattr(widget, "arranged_rect", None)
        if r is None:
            return self.vmin
        ins = self.inset
        if self.orient.startswith("v"):
            span = (r.h - 2 * ins)
            norm = 0.0 if span <= 1e-6 else \
                ((r.y + r.h - ins) - y) / span
        else:
            span = (r.w - 2 * ins)
            norm = 0.0 if span <= 1e-6 else (x - r.x - ins) / span
        norm = min(1.0, max(0.0, norm))
        v = self.vmin + norm * (self.vmax - self.vmin)
        return _quantize(v, self.vmin, self.vmax, self.step)

    def _publish(self, widget: Any, v: float) -> None:
        widget.publish(self.namespace, v)


def attach_drag_value(widget: Any, spec: Any) -> Optional[DragValueController]:
    """Parse a ``drag_value:`` block and attach a controller to *widget*.

    Accepted spec (all but ``namespace`` optional)::

        drag_value:
          namespace: slider/value
          min: 0
          max: 1
          step: 0.1
          orient: horizontal        # or vertical
          mode: live                # or on_release
          inset: 9                  # px; half the thumb width

    Returns the controller (also stored as ``widget._drag_value`` and marks
    the widget interactive), or None when the block is malformed.
    """
    if not isinstance(spec, dict):
        return None
    ns = spec.get("namespace") or spec.get("value_ns")
    if not ns:
        return None
    ctl = DragValueController(
        str(ns),
        vmin=float(spec.get("min", 0)),
        vmax=float(spec.get("max", 1)),
        step=float(spec.get("step", 0) or 0),
        orient=str(spec.get("orient", "horizontal")),
        mode=str(spec.get("mode", spec.get("live_or_on_release", "live"))),
        inset=float(spec.get("inset", 0) or 0))
    widget._drag_value = ctl
    widget.interactive = True
    return ctl
