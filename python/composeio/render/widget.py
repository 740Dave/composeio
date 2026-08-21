"""
ComposeUX Render Engine - Widget Base Class (Layer 3)

Spec: [CXC-R-WIDGET-CONTRACT]

Every ComposeUX widget subclasses Widget and overrides measure() + draw().
Interaction and bus binding methods are stubs in Phase 1 — they become real
when the engine's hit-testing (Phase 3) and bus wiring (Phase 4) land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .canvas import Canvas, Size


@dataclass
class Rect:
    """Axis-aligned rectangle used for layout and drawing."""
    x: float
    y: float
    w: float
    h: float


@dataclass
class Constraints:
    """Max width/height available for a widget during measure pass."""
    max_width: float
    max_height: float


@dataclass
class LayoutProperties:
    """Flexbox-inspired layout hints (Phase 2 layout engine consumes these)."""
    direction: str = "row"
    align: str = "start"
    justify: str = "start"
    gap: float = 0.0
    padding: tuple = (0, 0, 0, 0)
    margin: tuple = (0, 0, 0, 0)
    width: Any = "auto"
    height: Any = "auto"
    # Flex main-axis grow factor (M4): free space in a FlexContainer is
    # distributed to children proportional to grow.  0 = natural size.
    grow: float = 0.0
    min_width: float = 0.0
    max_width: float = float("inf")
    min_height: float = 0.0
    max_height: float = float("inf")
    overflow: str = "visible"


class Widget:
    """Base class for all ComposeUX widgets.

    Subclasses MUST override ``measure()`` and ``draw()``.
    Everything else has sensible defaults or Phase-1 stubs.
    """

    def __init__(self, widget_id: str, widget_type: str, **kwargs: Any) -> None:
        self.widget_id = widget_id
        self.widget_type = widget_type
        self.layout = LayoutProperties()
        self.preferred_size: Optional[Size] = None

        # M1 engine state -------------------------------------------------
        # Rect assigned by the layout arrange pass (surface coordinates).
        # This doubles as the hit-test region per [CXC-R-ENGINE-HIT].
        self.arranged_rect: Optional[Rect] = None
        # [CXC-R-DAMAGE] Paint overflow: how far (logical px) this widget's
        # drawn pixels extend BEYOND its arranged rect — an ambient drop
        # shadow, a glow, a focus halo.  The engine's dirty-region pruning
        # keeps a widget in a partial redraw when its PAINT BOUNDS (rect +
        # this margin) intersect the damage, even if the tight rect misses;
        # this is what makes a card's soft shadow that lands on an earlier
        # sibling composite pixel-identically under a partial redraw.  Zero
        # for the common case (a widget that paints only inside its rect).
        self.paint_overflow: float = 0.0
        self.visible: bool = True
        # [SPEC:CXC-BIND] bindable render transforms ([CXC-BIND-ATTR]):
        # applied around draw() by the container draw pass (engine/layout
        # draw_child) — rotation in degrees about the rect center, uniform
        # scale about the rect center, opacity as a 0..1 paint multiplier.
        self.opacity: float = 1.0
        self.rotation: float = 0.0
        self.scale: float = 1.0
        # Widgets that respond to pointer events set interactive=True
        # (parser sets it when a publish binding or action is configured).
        self.interactive: bool = False
        # [CXC-R-ENGINE-HOVER] TEXT-content affordance.  A widget whose body is
        # selectable/editable TEXT — the user can drag-select and copy it, or
        # type into it (the prompt input, the chat/message thread, the
        # markdown/doc body) — sets this True so the engine shows the I-BEAM
        # over it instead of the clickable HAND.  It keys the cursor on the
        # KIND of interaction (edit/select text vs. tap-to-act) and WINS over
        # ``interactive`` in hover_cursor(): a text field is interactive AND
        # text_selectable, and must read as an I-beam, not a hand.  It is the
        # affordance CONTRACT the drag-select gesture honors ([CXC-ICV-SELECT]);
        # a read surface that shows selectable text before its selection gesture
        # is wired carries it as a documented promise (see markdown_viewer).
        # Click-activation widgets (buttons/tabs/toggles/list rows) leave it
        # False and keep the hand.
        self.text_selectable: bool = False
        # [CXC-R-ENGINE-HIT] opt-in RAW per-finger pointer feed.  The
        # synthesized hooks below (on_pan/on_tap) route through the runtime's
        # single _pointer_capture and carry NO pointer_id, so they collapse
        # simultaneous fingers.  A widget needing true multitouch (track N
        # fingers independently) sets wants_raw_pointers = True and receives
        # every PointerEvent — down/move/up — with its pointer_id intact,
        # routed to the widget the finger landed on.  See on_raw_pointer().
        self.wants_raw_pointers: bool = False
        self.accepts_focus: bool = False
        self.focused: bool = False
        # [CXC-R-ENGINE-FOCUS] the engine draws a focus ring around the
        # focused widget as an accessibility affordance.  A widget whose own
        # chrome already signals focus (e.g. a console composer's blinking
        # caret) opts OUT by setting focus_ring = False — focus itself is
        # unchanged (Tab nav + keyboard routing), only the RING draw is
        # suppressed.  Reusable via the scene key `focus_ring: false`.
        self.focus_ring: bool = True
        # Engine context (bus, assets, animator, request_redraw) — set at mount.
        self._ctx: Any = None
        # [CXC-PRIM-DRAGVAL] optional drag-to-value controller.  When set
        # (engine/drag_value.py), this widget is a drag surface: its
        # pointer hooks below map pointer position -> published value.
        self._drag_value: Any = None

        # -- STALENESS PRESENTATION ([CXC-PRIM-STALE]) ---------------------
        # ⛔ OPT-OUT, NOT OPT-IN, AND THAT IS THE WHOLE POINT.  The freshness
        # receiver ([SPEC:CXC-FRESHNESS]) decides a value is no longer
        # believable and the glass kept rendering the old number: 52 widgets
        # implement on_bus_state and 22 of them wrap a coercion in an
        # `except (TypeError, ValueError)` that swallows `UnknownValueError`
        # and silently RETAINS THE STALE NUMBER.  Twenty-two is the measured
        # cost of a mechanism each widget must remember to adopt, so this one
        # is adopted BY DEFAULT and a widget deliberately declines it.
        #
        #   "auto"  (default)  the engine draws the treatment centrally in
        #                      engine/layout.draw_child — the same choke point
        #                      the opacity/rotate/scale transforms compose
        #                      through, so EVERY widget is covered with zero
        #                      per-widget edits and a new widget is covered on
        #                      the day it is written.
        #   "self"             the widget renders its OWN staleness from
        #                      `self.freshness` and the engine draws nothing.
        #                      For a widget whose entire job IS liveness (the
        #                      header link-health dot).
        #   "none"             no treatment at all.  Chrome, decoration,
        #                      containers, and the deliberate exception.
        #
        # Reusable declaratively: `staleness: none` in `.compose.yaml`.
        self.staleness_treatment: str = "auto"
        # What the engine last measured about the namespaces feeding this
        # widget (worst-verdict-wins, [CXC-FRESH-CLASS]).  Set by the runtime's
        # freshness poll; read by draw_child and by "self"-treated widgets.
        from .freshness_view import WidgetFreshness
        self.freshness: WidgetFreshness = WidgetFreshness()
        # ⛔ UNKNOWN IS WITHHELD FROM on_bus_state BY DEFAULT.  A widget that
        # can render `unknown` NATIVELY (a label that shows "—", a monitor that
        # reports the verdict) opts in and receives the singleton; every other
        # widget keeps its last value on purpose and is marked by the central
        # treatment, instead of swallowing the refusal in a bare `except`.
        self.wants_unknown: bool = False

        # Allow subclasses to consume extra kwargs (config driven properties)
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)

    # -- Engine hooks (M1) -----------------------------------------------

    def paint_bounds(self) -> Optional[Rect]:
        """The screen region this widget's draw() can touch ([CXC-R-DAMAGE]):
        its arranged rect grown by ``paint_overflow`` on every side.

        This is a PRUNING-INCLUSION hint, not a self-damage rect: the engine
        keeps this widget in a partial redraw when its paint bounds intersect
        the damage (so a card's soft shadow that lands on an earlier-drawn
        sibling is repainted when that sibling is damaged).  A widget reports
        its own changed region as ``arranged_rect`` (see request_redraw) —
        tighter and cheaper — because a content/animation change repaints
        pixels INSIDE the rect while the drop shadow around it is static.
        Returns None before layout, which the engine treats as 'cannot
        scope' -> full redraw."""
        r = self.arranged_rect
        if r is None:
            return None
        m = self.paint_overflow
        if m <= 0.0:
            return r
        return Rect(r.x - m, r.y - m, r.w + 2 * m, r.h + 2 * m)

    def clock_damage_rect(self) -> Optional[Rect]:
        """The region to damage when this CLOCKED widget reports a repaint
        from on_clock_tick ([CXC-R-DAMAGE]).  Defaults to the arranged rect
        (the live-dot beat, Lottie playback repaint their whole rect).  A
        widget whose per-tick change is a small sliver — a focused
        text_input's blinking caret — overrides to return just that sliver,
        so the engine repaints the sliver instead of the whole widget."""
        return self.arranged_rect

    def request_redraw(self) -> None:
        """Request a redraw of THIS widget's region ([CXC-R-DAMAGE]).

        A widget calls this when its own painted content changed (new bus
        value, cursor blink, animation frame).  The engine damages only this
        widget's arranged rect — the changed region — and repaints, in
        z-order, everything whose paint bounds intersect it, so the composite
        is pixel-identical to a full redraw.  A widget whose change affects
        more than itself (rare) can escalate via the engine's full
        mark_dirty()."""
        if self._ctx is not None:
            self._ctx.request_redraw(self.arranged_rect)

    # -- Lifecycle ----------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        """Called when the widget is added to the tree.  ctx carries bus + assets."""
        self._ctx = ctx

    def on_unmount(self) -> None:
        """Called when the widget is removed from the tree."""
        pass

    # -- Measure / Draw -----------------------------------------------------

    def measure(self, constraints: Constraints) -> "Size":
        """Return preferred size given available space.  MUST be overridden."""
        from .canvas import Size
        return Size(0, 0)

    def draw(self, canvas: "Canvas", rect: Rect) -> None:
        """Draw the widget into *rect* using *canvas* primitives.  MUST be overridden."""
        pass

    # -- Interaction stubs (Phase 3+) ---------------------------------------

    def hit_test(self, x: float, y: float) -> bool:
        """Return True if (x, y) is inside this widget's hit region.

        Default: the arranged rect, when the widget is interactive.
        Non-rectangular widgets override with a custom test.
        """
        if not self.interactive or self.arranged_rect is None:
            return False
        r = self.arranged_rect
        return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

    def hover_cursor(self) -> str:
        """The semantic OS cursor to show while the pointer hovers this widget
        ([CXC-R-ENGINE-HOVER]).

        The cursor keys on the KIND of interaction, not just interactive
        yes/no:

          - selectable/editable TEXT (``text_selectable``) -> "text" (I-beam):
            content the user drags to select + copy, or types into — the prompt
            input, the chat/message thread, the markdown/doc body.  This WINS
            over ``interactive`` (a text field is both).
          - a click-ACTIVATION target (``interactive``, tap-to-act) -> "pointer"
            (hand): buttons, tabs, toggles, list rows (artifacts/decision/
            away-watch registries), doc-picker links, launch rails.  The SAME
            ``interactive`` flag ``hit_test_tree`` resolves a click target on, so
            these light up with ZERO per-widget edits.
          - static / non-interactive display -> "default" (arrow).

        A widget that is interactive only to CATCH scroll/wheel or pan/zoom
        (an svg pan-zoom canvas, a scroll-only checklist) — where a hand over
        its whole body would mislead and there is no text to select — overrides
        this to return "default".  The affordance is semantic; the backend
        lowers the name to a system cursor (SDL hand/I-beam/arrow; browser CSS
        ``cursor``; embedded/CYD: no-op).  Pure read — never mutates state."""
        if self.text_selectable:
            return "text"
        return "pointer" if self.interactive else "default"

    def on_tap(self, x: float, y: float) -> None:
        pass

    def on_double_click(self, x: float, y: float) -> None:
        """A fast second tap on this widget ([CXC-R-ENGINE-HIT] double-click),
        synthesized by the runtime from two taps within the double-click
        window.  No-op by default; a text field overrides to word-select."""
        pass

    def on_pointer_down(self, x: float, y: float) -> None:
        """Raw press (fires before tap/long-press synthesis).
        Needed by widgets with pressed-state visuals (Button while_pressed)."""
        if self._drag_value is not None:
            self._drag_value.begin(self, x, y)

    def on_pointer_up(self, x: float, y: float) -> None:
        """Raw release (fires before tap/long-press synthesis)."""
        if self._drag_value is not None:
            self._drag_value.finish(self, x, y)

    def on_long_press(self, x: float, y: float) -> None:
        pass

    def on_raw_pointer(self, ev: Any) -> None:
        """RAW per-finger pointer event — opt in via ``wants_raw_pointers``.

        ``ev.type`` is one of "down"/"move"/"up"; ``ev.x``/``ev.y`` are surface
        coordinates; ``ev.pointer_id`` is the stable per-finger id.  Fires
        ALONGSIDE the synthesized single-pointer hooks (on_pan/on_tap), which a
        raw consumer typically ignores.  This is the ONLY hook that preserves
        pointer_id — the synthesized hooks collapse fingers through the
        runtime's single _pointer_capture.  No-op by default."""
        pass

    def on_pan_start(self) -> None:
        pass

    def on_pan(self, dx: float, dy: float) -> None:
        if self._drag_value is not None:
            self._drag_value.update(self, dx, dy)

    def on_pan_end(self) -> None:
        # A no-op for drag-to-value: on_pointer_up (which fires first in the
        # runtime's up branch) already committed the value.  Kept guarded so
        # a synthesized pan_end without a preceding up is still safe.
        if self._drag_value is not None:
            self._drag_value.finish(self)

    def on_swipe(self, direction: str, x: float, y: float) -> None:
        """A recognized single-touch directional swipe ([SPEC:CXC-GESTURE]).
        direction is one of "up"/"down"/"left"/"right".  Fires ALONGSIDE
        on_pan_end (a qualifying flick is both a pan and a swipe) — no-op by
        default; a widget overrides, or binds declaratively via
        swipe_namespace ([CXC-GEST-BUS])."""
        pass

    def on_pinch(self, scale: float, cx: float, cy: float) -> None:
        pass

    def on_scroll(self, dx: float, dy: float) -> None:
        pass

    def on_key(self, key: str, modifiers: Any = None) -> None:
        pass

    def on_text(self, text: str) -> None:
        """Committed text input (platform text events, [CXC-R-ENGINE-TEXT]).
        Routed to the focused widget only."""
        pass

    def on_focus(self) -> None:
        pass

    def on_blur(self) -> None:
        pass

    # -- Bus binding stubs (Phase 4+) ---------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """Called when a bound bus namespace changes.

        ⛔ THIS IS NOT CALLED WITH `UNKNOWN` unless the widget sets
        ``wants_unknown = True`` ([CXC-PRIM-STALE]).  The runtime withholds the
        unknown singleton and records a freshness verdict instead, because 22
        of the 52 implementations of this method wrap a coercion in an
        `except (TypeError, ValueError)` — and `UnknownValueError` subclasses
        TypeError, so they caught the refusal, passed, and kept displaying the
        stale number.  Withholding at the seam means the widget RETAINS its
        last value deliberately (which is what an operator wants: "it was 3,
        forty seconds ago") and the engine marks it unmissably, instead of the
        retention being an accident nobody reports.
        """
        pass

    def note_freshness(self, verdict: Any, age_s: Optional[float] = None,
                       namespace: Optional[str] = None) -> bool:
        """Record the engine's freshness verdict for this widget
        ([CXC-PRIM-STALE]).  Returns True when the RENDERED state changed and
        the widget therefore needs a repaint.

        The change test is on the TREATED appearance, not on the raw verdict:
        an age ticking 12s -> 13s inside the same STALE verdict does change the
        badge caption, and a poll that reported no change there would leave a
        frozen age on a dead channel — the same lie one layer up.  A poll that
        reported a change on EVERY tick would damage the whole scene forever,
        so the test is the caption plus the verdict and nothing finer.
        """
        f = self.freshness
        before = (f.verdict, f.caption())
        f.verdict = verdict
        f.age_s = age_s
        f.namespace = namespace
        return before != (f.verdict, f.caption())

    def publish(self, namespace: str, value: Any) -> None:
        """Publish a value to the bus (routed through the engine context)."""
        if self._ctx is not None:
            self._ctx.publish(namespace, value, source=self.widget_id)

    # -- Animation (engine-backed as of M1) -----------------------------------

    def animate(self, prop: str, from_val: Any, to_val: Any,
                duration: float = 500, easing: str = "ease_out") -> None:
        if self._ctx is not None and getattr(self._ctx, "animator", None):
            self._ctx.animator.animate(self, prop, from_val, to_val,
                                       duration, easing)

    def on_animation_frame(self, prop: str, current_val: Any) -> None:
        pass
