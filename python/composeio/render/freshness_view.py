"""The STALENESS PRESENTATION vocabulary — what "I have not heard from this"
looks like on the glass.

[SPEC:CXC-PRIM] [CXC-PRIM-STALE], closing [CXC-FRESH-NO-PRESENTATION].

⛔ THE FAILURE THIS EXISTS TO PREVENT.  The freshness receiver
([SPEC:CXC-FRESHNESS]) correctly decides a value is no longer believable and
hands back `UNKNOWN` — and the glass kept rendering the old number anyway.
`GaugeWidget.on_bus_state` ends in `try: self.value = float(value) except
(TypeError, ValueError): pass`; `UnknownValueError` subclasses `TypeError`, so
the handler caught the refusal and PASSED.  The failure mode is not a blank and
not a zero.  It is SILENT RETENTION OF THE STALE NUMBER, which is strictly the
worst outcome available: the display is confidently wrong and nothing anywhere
reports it.  David's Dradis showed `contacts: 3` from a publisher that had been
dead for hours.

---------------------------------------------------------------------------
THE THREE DECISIONS, AND THE FAILURE EACH ONE PREVENTS
---------------------------------------------------------------------------

1. THE LAST VALUE STAYS VISIBLE.  We do NOT blank.

   Blanking is the intuitive fix and it is worse than the disease on exactly
   the widgets that matter most.  A blanked bar gauge reads as ZERO — a
   full-scale-deflection instrument at rest is indistinguishable from one
   showing a real zero, so blanking converts "confidently wrong" into
   "confidently wrong AND alarming".  A blanked LED reads as OFF.  A blanked
   seven-segment reads as a powered-down device.  In every one of those the
   viewer's default reading of "empty" is a MEASUREMENT, not an absence.

   Retention also serves the operator directly: "it was 3, forty seconds ago"
   is the single most useful thing a dead channel can tell you, and it is the
   thing a blank throws away.  Retention is only safe if the treatment is
   unmissable, which is why there are three cues and not one.

2. THREE CUES, NOT ONE — dim, hatch, badge.  Each covers the others' blind
   spot, and each alone was rejected for a named reason:

     DIM alone      — dimming is a cue of DEGREE, and a glance does not read
                      degree.  On a dark theme a 38%-opacity grey label is
                      still perfectly legible and reads as "styled quietly",
                      not as "dead".  A dimmed gauge needle still points at 3.
     HATCH alone    — a diagonal hatch is categorical and unmissable, but on a
                      dense widget it reads as decoration, and it does not say
                      WHY or HOW OLD.
     BADGE alone    — small, and on a 1280-px scene a 60-px pill is exactly
                      what a scanning eye skips.  It also cannot mark WHICH
                      number is dead inside a multi-value card.

   Together: the dim makes the widget RECEDE beneath its live neighbours, the
   hatch makes it categorically not-live AT ANY DISTANCE (no live widget in
   this engine ever draws a 45-degree hatch, so the texture is unambiguous),
   and the badge names the verdict and the age for the operator who then walks
   up to it.

3. THREE STATES, NOT TWO — and node-silent looks DIFFERENT from stale,
   because the REMEDIES DIFFER.

     STALE          amber, single hatch.  A publisher that declared a cadence
                    has stopped.  Remedy: go look at that one process.
     NODE_SILENT    red, CROSS hatch.  A whole board stopped beating.  Remedy:
                    go look at the hardware — and note that for an
                    EVENT_DRIVEN namespace this is the ONLY condemnation that
                    exists ([CXC-FRESH-QUIET]), so it is never a false alarm
                    about a stationary throttle.
     NEVER_RECEIVED slate, single hatch, no age.  Nothing ever arrived; this
                    may simply be early in boot.  Distinct from STALE because
                    there is no last value being disbelieved.
     WIRE_UNKNOWN   blue-grey, sparse hatch, and DELIBERATELY THE GENTLEST
                    treatment.  The publisher is ALIVE and is honestly saying
                    "I do not know" (an explicit 0xFD).  Nothing is broken.
                    Rendering this as an alarm would train the operator to
                    ignore the alarm colour.

⛔ AND THE INVERSE DEFECT, WHICH IS EXACTLY AS BAD.  `UNDECLARED` gets NO
TREATMENT AT ALL.  A namespace that declared no cadence was never judged, and
hatching it would put the treatment on every widget in every existing scene —
an instrument that condemns everything is precisely as useless as one that
condemns nothing, and it passes the same "did it go stale?" test.  Likewise a
quiet-by-construction `EVENT_DRIVEN` namespace reads FRESH from its own silence
and is therefore never treated; only its node's death can mark it.

---------------------------------------------------------------------------
NO NEW CANVAS PRIMITIVES
---------------------------------------------------------------------------

The whole treatment is `save`/`clip_rect`/`fill_rect`/`draw_line`/
`fill_round_rect`/`fill_path`/`draw_text`/`set_opacity`/`restore` — every one
already on the [CXC-R-DRAW-CONTRACT] narrow waist and already real on the
ESP32 band renderer.  Widening the waist is a platform decision and this change
did not need one ([PRINCIPLE-NARROW-WAIST]).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Tuple

from composeio.freshness import Verdict, verdict_is_believable

if TYPE_CHECKING:  # pragma: no cover
    from .canvas import Canvas
    from .widget import Rect


# --------------------------------------------------------------------------- #
# The visual language
# --------------------------------------------------------------------------- #

RGB = Tuple[int, int, int]

#: How a widget may be treated.  Declarable per widget and in `.compose.yaml`
#: as `staleness: auto|self|none`.
TREATMENT_AUTO = "auto"
TREATMENT_SELF = "self"
TREATMENT_NONE = "none"


@dataclass(frozen=True)
class Treatment:
    """The rendered appearance of ONE unbelievable verdict."""

    #: Opacity multiplier applied to the widget's own draw().  Never 0 —
    #: see decision 1: the last value stays readable.
    dim: float
    #: Marker / hatch / badge colour.
    color: RGB
    #: Hatch line spacing in logical px.  Larger = sparser = calmer.
    hatch_step: float
    #: Draw the mirrored hatch too (a cross-hatch), reserved for NODE_SILENT.
    cross: bool
    #: Badge caption, before the age suffix.
    label: str
    #: Whether the age is appended to the caption.  NEVER_RECEIVED has no
    #: age to report and a "0s" there would be a lie.
    show_age: bool


#: ⛔ The verdicts that get a treatment.  FRESH and UNDECLARED are absent ON
#: PURPOSE — see the inverse-defect note in the module docstring.
TREATMENTS: dict[Verdict, Treatment] = {
    Verdict.STALE: Treatment(
        dim=0.38, color=(0xF1, 0xC4, 0x0F), hatch_step=9.0, cross=False,
        label="STALE", show_age=True),
    # ⛔ show_age=False, and this was FOUND BY LOOKING AT THE CAPTURE, not by a
    # test.  The badge rendered "NODE DOWN 0s" — the age of the NAMESPACE, which
    # for a node-silent verdict is exactly the number [CXC-FRESH-QUIET] says
    # proves nothing: a quiet-by-construction namespace's own silence is the
    # design, and its age is meaningless.  The relevant age is the NODE's, which
    # the widget does not hold.  Printing an irrelevant number beside a
    # condemnation is a smaller version of the whole defect, so it prints none.
    Verdict.NODE_SILENT: Treatment(
        dim=0.30, color=(0xE7, 0x4C, 0x3C), hatch_step=7.0, cross=True,
        label="NODE DOWN", show_age=False),
    Verdict.NEVER_RECEIVED: Treatment(
        dim=0.30, color=(0x7A, 0x8A, 0x99), hatch_step=9.0, cross=False,
        label="NO SIGNAL", show_age=False),
    Verdict.WIRE_UNKNOWN: Treatment(
        dim=0.45, color=(0x5A, 0x8A, 0xB0), hatch_step=13.0, cross=False,
        label="NO DATA", show_age=False),
}

#: Alpha of the hatch strokes.  Low enough to read the retained value THROUGH
#: the hatch (decision 1), high enough that the texture is unmistakable.
HATCH_ALPHA = 90
HATCH_WIDTH = 1.0

BADGE_H = 14.0
BADGE_PAD_X = 5.0
BADGE_INSET = 2.0
BADGE_FONT = "monospace"
BADGE_FONT_SIZE = 9.0
BADGE_TEXT = (0x10, 0x10, 0x10)
#: Below this the pill does not fit and the corner WEDGE is drawn instead.  A
#: 4-px LED still has to be able to say it is dead.
WEDGE_SIZE = 7.0


def treatment_for(verdict: Optional[Verdict]) -> Optional[Treatment]:
    """The treatment for *verdict*, or None when nothing should be drawn.

    None for FRESH (believable) and for UNDECLARED (never judged — treating it
    would mark every un-declared namespace in the fleet, the inverse defect).
    """
    if verdict is None or verdict_is_believable(verdict):
        return None
    return TREATMENTS.get(verdict)


def format_age(age_s: Optional[float]) -> str:
    """A short, glanceable age.  '' when there is no age to report."""
    if age_s is None or age_s < 0:
        return ""
    if age_s < 60:
        return f"{int(age_s)}s"
    if age_s < 3600:
        return f"{int(age_s // 60)}m"
    if age_s < 86400:
        return f"{int(age_s // 3600)}h"
    return f"{int(age_s // 86400)}d"


def badge_caption(t: Treatment, age_s: Optional[float]) -> str:
    if not t.show_age:
        return t.label
    age = format_age(age_s)
    return f"{t.label} {age}" if age else t.label


# --------------------------------------------------------------------------- #
# The drawing
# --------------------------------------------------------------------------- #

def draw_treatment(canvas: "Canvas", rect: "Rect", t: Treatment,
                   age_s: Optional[float] = None) -> None:
    """Draw the hatch + badge over *rect*.  The DIM is applied by the caller
    around the widget's own draw() (see engine/layout.draw_child) — this
    function draws the two cues that must NOT themselves be dimmed, because a
    dimmed warning is the failure it is warning about.

    Everything is clipped to *rect*, so a treated widget can never paint over
    a healthy neighbour.
    """
    if rect is None or rect.w <= 0 or rect.h <= 0:
        return
    canvas.save()
    try:
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        _draw_hatch(canvas, rect, t)
        _draw_marker(canvas, rect, t, age_s)
    finally:
        canvas.restore()


def _draw_hatch(canvas: "Canvas", rect: "Rect", t: Treatment) -> None:
    """45-degree lines on a SURFACE-ANCHORED grid.

    The phase is computed from absolute surface coordinates, not from the
    widget's own origin, so the hatch is byte-identical whether the widget was
    painted in a full redraw or in a scoped partial one ([CXC-R-DAMAGE-C1]) —
    a widget-relative phase would shift the lines when the damage rect moved
    and would show up as a 1-px diff nobody could explain.
    """
    r, g, b = t.color
    canvas.set_stroke_color(r, g, b, HATCH_ALPHA)
    canvas.set_stroke_width(HATCH_WIDTH)
    step = t.hatch_step
    x0, y0, w, h = rect.x, rect.y, rect.w, rect.h

    # Down-right diagonals: x - y = n*step.
    n_lo = int(math.floor((x0 - y0 - h) / step))
    n_hi = int(math.ceil((x0 + w - y0) / step))
    for n in range(n_lo, n_hi + 1):
        sx = n * step + y0
        canvas.draw_line(sx, y0, sx + h, y0 + h)

    if not t.cross:
        return
    # Down-left diagonals: x + y = n*step.  Reserved for NODE_SILENT so a dead
    # BOARD is distinguishable from a dead PUBLISHER at a glance, without
    # relying on colour alone (which a colour-blind operator cannot use).
    m_lo = int(math.floor((x0 + y0) / step))
    m_hi = int(math.ceil((x0 + w + y0 + h) / step))
    for m in range(m_lo, m_hi + 1):
        sx = m * step - y0
        canvas.draw_line(sx, y0, sx - h, y0 + h)


def _draw_marker(canvas: "Canvas", rect: "Rect", t: Treatment,
                 age_s: Optional[float]) -> None:
    """The badge pill, degrading to a corner wedge when the widget is tiny.

    A 4-px live-dot cannot carry a 60-px pill, and "too small for the badge"
    must not become "silently untreated" — that is the whole defect class
    reappearing at small sizes.
    """
    caption = badge_caption(t, age_s)
    canvas.set_font(BADGE_FONT, BADGE_FONT_SIZE, "bold")
    tw = canvas.measure_text(caption).width
    bw = tw + 2 * BADGE_PAD_X
    r, g, b = t.color

    if rect.w < bw + 2 * BADGE_INSET or rect.h < BADGE_H + 2 * BADGE_INSET:
        # Corner wedge: a filled triangle in the top-right corner.  ⛔ The
        # command list uses the CONTRACT's PathCommand dataclasses, not raw
        # tuples: the Skia backend dispatches them with a structural `match`,
        # and a tuple matches NO case — it draws an empty path and returns
        # cleanly.  A marker that silently declines to paint is precisely the
        # class of failure this whole change exists to end, so it is worth the
        # import.
        from .canvas import Close, LineTo, MoveTo

        s = min(WEDGE_SIZE, rect.w, rect.h)
        canvas.set_fill_color(r, g, b, 255)
        canvas.fill_path([
            MoveTo(rect.x + rect.w - s, rect.y),
            LineTo(rect.x + rect.w, rect.y),
            LineTo(rect.x + rect.w, rect.y + s),
            Close(),
        ])
        return

    bx = rect.x + rect.w - bw - BADGE_INSET
    by = rect.y + BADGE_INSET
    canvas.set_fill_color(r, g, b, 255)
    canvas.fill_round_rect(bx, by, bw, BADGE_H, 3.0)
    canvas.set_fill_color(*BADGE_TEXT, 255)
    m = canvas.measure_text(caption)
    baseline = by + BADGE_H / 2 + (m.ascent - m.descent) / 2
    canvas.draw_text(caption, bx + BADGE_PAD_X, baseline, "left")


# --------------------------------------------------------------------------- #
# The per-widget freshness state
# --------------------------------------------------------------------------- #

@dataclass
class WidgetFreshness:
    """What the engine last measured about the namespaces feeding a widget.

    ⛔ THE WORST VERDICT WINS, and that is not a new rule — it is
    [CXC-FRESH-CLASS]'s ratified `max` applied to the widget's inputs.  A card
    fed by three namespaces is not believable because its newest input is
    fresh; it is as old as its oldest, and taking the max is what stops the
    freshest input from LAUNDERING the staleness of the others.
    """

    verdict: Verdict = Verdict.UNDECLARED
    age_s: Optional[float] = None
    #: Which namespace produced the verdict — the operator needs to know WHICH
    #: feed died, not merely that the card is bad ([CXC-FRESH-CLASS]).
    namespace: Optional[str] = None

    @property
    def believable(self) -> bool:
        return verdict_is_believable(self.verdict)

    def treatment(self) -> Optional[Treatment]:
        return treatment_for(self.verdict)

    def caption(self) -> str:
        """The badge text this state would render, '' when untreated.  Public
        so a widget rendering its OWN staleness (`staleness = "self"`) uses the
        SAME words as the engine's default — one vocabulary, not two."""
        t = self.treatment()
        return "" if t is None else badge_caption(t, self.age_s)


#: Verdict severity: which one survives when a widget has several inputs.
#: NODE_SILENT outranks STALE because a dead board subsumes a dead publisher;
#: NEVER_RECEIVED outranks WIRE_UNKNOWN because "never arrived" is a stronger
#: claim than "arrived saying it does not know".
_SEVERITY = {
    Verdict.FRESH: 0,
    Verdict.UNDECLARED: 0,
    Verdict.WIRE_UNKNOWN: 1,
    Verdict.NEVER_RECEIVED: 2,
    Verdict.STALE: 3,
    Verdict.NODE_SILENT: 4,
}


def worst(a: Optional[Verdict], b: Optional[Verdict]) -> Optional[Verdict]:
    """The more-condemning of two verdicts ([CXC-FRESH-CLASS] max rule)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _SEVERITY.get(a, 0) >= _SEVERITY.get(b, 0) else b
