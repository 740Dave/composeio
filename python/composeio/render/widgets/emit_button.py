"""
ComposeUX EmitButtonWidget — a momentary button that PUBLISHES a configured
sequence of bus messages on press (not a state-engine toggle).

Where ButtonWidget advances a state engine and publishes one scalar to its
bound namespace, EmitButton fires a fixed `emits:` list — each an explicit
{namespace, value} — in order, on every tap.  This is what a *command*
button needs: emit a structured payload (a list, an object, a scalar) that
a peer consumes, rather than reflect a toggled state.

Motivating use (dual-throttle calibration): a "TEST DRADIS SOUND" button
that fires the two-frame contact-identify sequence the DY-SV5F audio cover
keys on — the cover's edge is baseline-guarded (a contact needs to be seen
`unknown` THEN `hostile` to fire), so the button publishes both frames:

    emits:
      - {namespace: "dradis/contacts", value: [{"id": "cal", "allegiance": "unknown"}]}
      - {namespace: "dradis/contacts", value: [{"id": "cal", "allegiance": "hostile"}]}

Each press re-arms (unknown resets the edge, hostile fires it), so it plays
every time.  Uses ONLY the abstract Canvas contract.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style
from .deliverable_card import _open_tri


def _binding_ns(v: Any) -> str:
    """The namespace of a declarative BINDING value ``{namespace, default}`` —
    the scene's OWN established binding vocabulary (the same shape a label's
    ``text:`` uses), applied to an emit value.  ``""`` when *v* is a plain
    literal.  A binding MUST be exactly a namespace(+default) mapping, so an
    ordinary payload dict that merely contains other keys is never mistaken for
    one."""
    if not isinstance(v, dict) or "namespace" not in v:
        return ""
    if not set(v.keys()) <= {"namespace", "default"}:
        return ""
    ns = v.get("namespace")
    return str(ns) if ns else ""


def _collect_bindings(v: Any, out: List[str]) -> None:
    """Every namespace referenced by a binding anywhere inside an emit value."""
    ns = _binding_ns(v)
    if ns:
        out.append(ns)
        return
    if isinstance(v, dict):
        for sub in v.values():
            _collect_bindings(sub, out)
    elif isinstance(v, list):
        for sub in v:
            _collect_bindings(sub, out)


class EmitButtonWidget(Widget):
    """A momentary button that publishes a fixed sequence of bus messages.

    An emit VALUE may embed declarative BINDINGS (``{namespace, default}``) at
    any depth; each is resolved from the LIVE bus value at publish time (falling
    back to its ``default``).  That keeps a command button a pure intent
    publisher while still carrying live context — the payload is authored
    declaratively in the scene, never assembled in code.
    """

    def __init__(self, widget_id: str, *,
                 label: str = "Emit",
                 emits: Any = None,
                 chrome: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "emit_button", **kwargs)
        self.label = label
        # `chrome: console` renders this window's own interactive language (the
        # accent ▸ + ink label of the console rows) instead of the dashboard
        # LED+pill.  OPT-IN: the default "" leaves every existing scene's pixels
        # untouched.
        self.chrome = str(chrome or "")
        # Normalize emits -> a list of (namespace, value) pairs.  Accept the
        # authored shape [{namespace, value}, ...]; ignore malformed entries.
        self._emits: List[tuple] = []
        binding_ns: List[str] = []
        for e in (emits or []):
            if not isinstance(e, dict):
                continue
            ns = e.get("namespace") or e.get("ns")
            if not ns:
                continue
            val = e.get("value")
            self._emits.append((str(ns), val))
            _collect_bindings(val, binding_ns)
        # the namespaces whose LIVE value this button's payload interpolates;
        # the parser subscribes these so on_bus_state keeps them current.
        self.value_namespaces: List[str] = list(dict.fromkeys(binding_ns))
        self._live: Dict[str, Any] = {}
        self.interactive = True
        self._pressed = False

    # -- bus (live payload context, NOT widget state) ----------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """Record a bound namespace's live value for payload interpolation.  A
        pure data read: the button's own appearance never changes, so this does
        NOT request a redraw ([CXC-R-DAMAGE] — no damage without a visual change)."""
        d = value
        if isinstance(d, dict) and "val" in d:
            d = d["val"]
        self._live[str(namespace)] = d

    def _resolve(self, v: Any) -> Any:
        """Substitute every embedded binding with its live value (else its
        authored ``default``)."""
        ns = _binding_ns(v)
        if ns:
            return self._live.get(ns, v.get("default"))
        if isinstance(v, dict):
            return {k: self._resolve(sub) for k, sub in v.items()}
        if isinstance(v, list):
            return [self._resolve(sub) for sub in v]
        return v

    # -- interaction -------------------------------------------------------
    # The depressed visual is tied to the physical pointer state ONLY, so it
    # reliably RELEASES on pointer-up (a timed post-tap hold gets stuck: the
    # engine is redraw-on-event, so the release frame never renders).  Both
    # transitions request a redraw so the press + pop-up both paint.

    def on_pointer_down(self, x: float, y: float) -> None:
        self._pressed = True
        self.request_redraw()

    def on_pointer_up(self, x: float, y: float) -> None:
        self._pressed = False
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        """Publish every configured emit, in order — each value resolved against
        the live bus so an authored binding carries its CURRENT value."""
        for ns, value in self._emits:
            self.publish(ns, self._resolve(value))

    # -- measure / draw ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        if self.chrome == "console":
            # a compact inline pill sized to its label (the console row idiom)
            w = min(20.0 + 8.0 * len(self.label) + 22.0, constraints.max_width)
            return Size(w, min(26.0, constraints.max_height))
        w = min(max(28 + 108, 20 + 8 + len(self.label) * 8 + 40),
                constraints.max_width)
        return Size(w, min(36.0, constraints.max_height))

    def _draw_console(self, canvas: Canvas, rect: Rect) -> None:
        """This window's own interactive language ([CXC-GV-SCOPE] chrome): a
        bordered pill with the leading accent ▸ and ink label the console rows
        use — NOT the dashboard LED+CTk-blue button."""
        t = ct.CONSOLE.active
        h = min(24.0, rect.h)
        y = rect.y + (rect.h - h) / 2.0
        w = rect.w
        sink = 1.0 if self._pressed else 0.0
        canvas.set_fill_color(*(t.panel2 if self._pressed else t.panel3))
        canvas.fill_round_rect(rect.x, y + sink, w, h, 5.0)
        canvas.set_stroke_color(*(t.accent if self._pressed else t.line))
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(rect.x + 0.5, y + sink + 0.5, w - 1.0,
                                 h - 1.0, 5.0)
        # leading ▸ open affordance (accent) — the SAME shared helper the
        # Artifacts rows use (reuse-by-reference: it draws the glyph as a PATH
        # because the bundled Plex faces do not cover U+25B8 and a system-font
        # fallback would fork pixels per platform, [CXC-R-DP-1]).
        cy = y + sink + h / 2.0
        _open_tri(canvas, rect.x + 9.0, cy, 6.0, 9.0, t.accent)
        canvas.set_font(ct.FONT_UI, 11.5, "bold")
        m = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(ct.ellipsize(canvas, self.label, w - 26.0),
                         rect.x + 19.0,
                         cy - (m.ascent + m.descent) / 2.0 + m.ascent, "left")

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.chrome == "console":
            canvas.save()
            self._draw_console(canvas, rect)
            canvas.restore()
            return
        canvas.save()
        cy = rect.y + rect.h / 2
        style.draw_dot(canvas, rect.x + 14, cy, 6,
                       style.LED_ACTIVE if self._pressed else style.LED_OFF,
                       outline=style.LED_OUTLINE)
        bx = rect.x + 28
        bw = max(40.0, rect.w - 28 - 4)
        bh = min(28.0, rect.h - 4)
        # DEPRESS cue: the body sinks 2px + uses the pressed fill while held,
        # and pops back up on release.
        sink = 2.0 if self._pressed else 0.0
        style.draw_button(canvas, bx, cy - bh / 2 + sink, bw, bh, self.label,
                          pressed=self._pressed)
        canvas.restore()
