"""
ComposeUX Intelligence Console design system ([SPEC:CXC-CONSOLE-VISUAL]
[CXC-ICV-TOKENS] / [CXC-ICV-THEME]).

The single token source of truth for the console's visual-fidelity pass:
every hex (BOTH themes), typography role, spacing step, radius, elevation
and motion timing lifted verbatim from the approved pre-vis
(docs/intelligence-console/previs/console-blended.html).  This file is to
the console what ``style.py`` is to the CTk look: the file IS the widget
identity — editing it re-skins every console surface at once.

Theme is a TOKEN SWAP, not a cascade ([CXC-ICV-THEME]): two token tables
(``DARK``, ``LIGHT``) and a single ``active`` selector.  Widgets read
``CONSOLE.active.<token>`` by ROLE — no widget hardcodes a hex or a theme
test.  ``set_theme("light")`` + a redraw re-skins the scene.

All colors are RGBA 0-255 4-tuples (the Canvas contract's form); the
pre-vis ``rgba(r,g,b,frac)`` tokens convert as ``(r,g,b,round(frac*255))``.

Documented approximations (no Canvas primitive — [CXC-ICV-TOKENS]):
  * letter-spacing: no ``set_font`` tracking param — ``draw_tracked``
    draws per-glyph with an advance nudge for the few short UPPERCASE
    labels where tracking is load-bearing; everything else accepts the
    face's default tracking.
  * text-transform: uppercase the STRING in the widget (``.upper()``).
  * asymmetric bubble radii (14/14/4/14): ``fill_round_rect`` takes one
    radius — bubbles use a single 12px radius (documented in
    [CXC-ICV-TOKENS]).
  * dashed borders: no dash primitive — ``draw_dashed_line`` composes
    dashes from ``draw_line`` segments for horizontal rules; dashed
    ROUND-RECT borders degrade to a solid ``line2`` stroke at width 1.
  * italic (narration): the bundled Plex faces ship no italic — the
    narration voice carries its mutedness via the dim ink + left rule.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, GradientStop

RGBA = Tuple[int, int, int, int]


# ---------------------------------------------------------------------------
# Token tables — DARK (the identity) + LIGHT (still legible)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConsoleTheme:
    """One theme's full token table.  Roles, never decoration."""
    name: str
    # ground
    bg: RGBA
    bg_grid: RGBA
    # panels
    panel: RGBA
    panel2: RGBA
    panel3: RGBA
    # lines
    line: RGBA
    line2: RGBA
    # ink
    ink: RGBA
    dim: RGBA
    faint: RGBA
    # the ONE azure accent (identity / interactive)
    accent: RGBA
    accent_dim: RGBA
    accent_line: RGBA
    # semantic — STATE only, never decoration ([CXC-ICV-TOKENS] semantic rule)
    ok: RGBA
    ok_dim: RGBA
    ok_line: RGBA
    warn: RGBA
    warn_dim: RGBA
    warn_line: RGBA
    crit: RGBA
    crit_dim: RGBA
    crit_line: RGBA
    # elevation (the single ambient shadow stop, [CXC-ICV-TOKENS])
    shadow: RGBA
    # dark ink on the accent send button (pre-vis .composer .send color)
    send_ink: RGBA


DARK = ConsoleTheme(
    name="dark",
    bg=(0x07, 0x0A, 0x0F, 255),
    bg_grid=(120, 150, 190, 9),
    panel=(0x0D, 0x12, 0x1B, 255),
    panel2=(0x10, 0x17, 0x24, 255),
    panel3=(0x0A, 0x0F, 0x17, 255),
    line=(0x1B, 0x24, 0x32, 255),
    line2=(0x26, 0x31, 0x3F, 255),
    ink=(0xE7, 0xEC, 0xF3, 255),
    dim=(0x8B, 0x98, 0xAA, 255),
    faint=(0x55, 0x61, 0x6F, 255),
    accent=(0x4E, 0xA8, 0xFF, 255),
    accent_dim=(78, 168, 255, 33),
    accent_line=(78, 168, 255, 107),
    ok=(0x2F, 0xD0, 0x7B, 255),
    ok_dim=(47, 208, 123, 33),
    ok_line=(47, 208, 123, 128),
    warn=(0xF5, 0xA6, 0x23, 255),
    warn_dim=(245, 166, 35, 33),
    warn_line=(245, 166, 35, 128),
    crit=(0xFF, 0x3B, 0x5C, 255),
    crit_dim=(255, 59, 92, 36),
    crit_line=(255, 59, 92, 140),
    shadow=(0, 0, 0, 128),
    send_ink=(0x04, 0x12, 0x1F, 255),
)

LIGHT = ConsoleTheme(
    name="light",
    bg=(0xE9, 0xED, 0xF2, 255),
    bg_grid=(40, 70, 110, 13),
    panel=(0xFF, 0xFF, 0xFF, 255),
    panel2=(0xF4, 0xF7, 0xFB, 255),
    panel3=(0xEE, 0xF2, 0xF7, 255),
    line=(0xD3, 0xDB, 0xE4, 255),
    line2=(0xC2, 0xCC, 0xD8, 255),
    ink=(0x10, 0x17, 0x20, 255),
    dim=(0x55, 0x61, 0x73, 255),
    faint=(0x85, 0x92, 0xA2, 255),
    accent=(0x1F, 0x7F, 0xE0, 255),
    accent_dim=(31, 127, 224, 26),
    accent_line=(31, 127, 224, 102),
    ok=(0x13, 0xA4, 0x5C, 255),
    ok_dim=(19, 164, 92, 26),
    ok_line=(19, 164, 92, 115),
    warn=(0xBD, 0x79, 0x08, 255),
    warn_dim=(189, 121, 8, 28),
    warn_line=(189, 121, 8, 115),
    crit=(0xD6, 0x1F, 0x3C, 255),
    crit_dim=(214, 31, 60, 26),
    crit_line=(214, 31, 60, 115),
    shadow=(30, 45, 70, 20),
    send_ink=(0xFF, 0xFF, 0xFF, 255),
)

# HIGH-CONTRAST — the a11y theme ([SPEC:CXC-CONSOLE-PROD] [CXC-ICP-S3-THEME],
# David's D2 ruling: ship dark + light + high-contrast).  A pure-black ground
# with maximum-contrast white ink and a vivid amber accent, WCAG-AAA-leaning:
# every ink role is >= 7:1 on the black ground, borders are opaque and heavy so
# panel edges are unmissable, and the ambient shadow is dropped (a soft shadow
# reads as mud at high contrast).  Same role set as DARK/LIGHT — a token swap,
# never a new role ([CXC-ICV-THEME]); the S3 missing-token fallback never fires
# because this table is complete.
HIGH_CONTRAST = ConsoleTheme(
    name="high-contrast",
    bg=(0x00, 0x00, 0x00, 255),
    bg_grid=(255, 255, 255, 16),
    panel=(0x00, 0x00, 0x00, 255),
    panel2=(0x0C, 0x0C, 0x0C, 255),
    panel3=(0x1A, 0x1A, 0x1A, 255),
    line=(255, 255, 255, 150),
    line2=(255, 255, 255, 220),
    ink=(0xFF, 0xFF, 0xFF, 255),
    dim=(0xE4, 0xE4, 0xE4, 255),
    faint=(0xBE, 0xBE, 0xBE, 255),
    accent=(0xFF, 0xD6, 0x0A, 255),
    accent_dim=(255, 214, 10, 48),
    accent_line=(255, 214, 10, 190),
    ok=(0x3B, 0xE8, 0x6B, 255),
    ok_dim=(59, 232, 107, 48),
    ok_line=(59, 232, 107, 190),
    warn=(0xFF, 0xC1, 0x07, 255),
    warn_dim=(255, 193, 7, 48),
    warn_line=(255, 193, 7, 190),
    crit=(0xFF, 0x5A, 0x5A, 255),
    crit_dim=(255, 90, 90, 56),
    crit_line=(255, 90, 90, 200),
    shadow=(0, 0, 0, 0),
    send_ink=(0x00, 0x00, 0x00, 255),
)

# The theme registry ([CXC-ICP-S3-THEME] enumerable set).  A selector renders
# from list_themes(), never a literal.  Insertion order = the offered order.
_TABLES: Dict[str, ConsoleTheme] = {
    "dark": DARK,
    "light": LIGHT,
    "high-contrast": HIGH_CONTRAST,
}


def list_themes() -> List[str]:
    """The registered theme names, in offered order ([CXC-ICP-S3-THEME]).  A
    Config selector reads THIS — it never hardcodes a theme literal."""
    return list(_TABLES.keys())


# ---------------------------------------------------------------------------
# Per-instance custom colors ([CXC-ICP-S3-THEME], David's D2 addition): the
# DARK theme's BACKGROUND + HIGHLIGHT/ACCENT are user-changeable (his examples:
# the send-button color, the "Account" bar-graph color — both accent-driven).
# The override is read from Foundation A's `console.theme_custom` sub-field and
# APPLIED as a token overlay on the active table, so every widget that reads
# CONSOLE.active.accent / .bg (the account bars, the gauge, the cards, the send
# button once tokenized) reflects it with NO draw-fn change.  Shape:
#     {"bg": [r,g,b(,a)], "accent": [r,g,b(,a)]}
# an unknown key is ignored; a malformed color drops that one override.
# ---------------------------------------------------------------------------
_CUSTOM_KEYS = ("bg", "accent")


def _as_rgba(value: Any) -> Optional[RGBA]:
    """Coerce a scene/Foundation-A color (3- or 4-list) to an RGBA 4-tuple, or
    None if malformed (the caller then drops just that override)."""
    if not isinstance(value, (list, tuple)) or not (3 <= len(value) <= 4):
        return None
    try:
        ch = [int(c) for c in value]
    except (TypeError, ValueError):
        return None
    if any(c < 0 or c > 255 for c in ch):
        return None
    if len(ch) == 3:
        ch.append(255)
    return (ch[0], ch[1], ch[2], ch[3])


def normalize_custom(overrides: Any) -> Dict[str, RGBA]:
    """Validate a raw theme_custom dict to {key: rgba} for known keys only."""
    out: Dict[str, RGBA] = {}
    if not isinstance(overrides, dict):
        return out
    for key in _CUSTOM_KEYS:
        if key in overrides:
            c = _as_rgba(overrides[key])
            if c is not None:
                out[key] = c
    return out


def _overlay(table: ConsoleTheme, custom: Dict[str, RGBA]) -> ConsoleTheme:
    """Return *table* with the custom bg/accent applied, recomputing the
    accent's derived dim/line tokens from the override so the whole accent
    family stays coherent (a bar fill, a focus ring, a chip border)."""
    if not custom:
        return table
    changes: Dict[str, Any] = {}
    if "bg" in custom:
        changes["bg"] = custom["bg"]
    if "accent" in custom:
        a = custom["accent"]
        changes["accent"] = a
        changes["accent_dim"] = (a[0], a[1], a[2], table.accent_dim[3])
        changes["accent_line"] = (a[0], a[1], a[2], table.accent_line[3])
    return replace(table, **changes) if changes else table


class _ConsoleHolder:
    """The single ``active`` selector ([CXC-ICV-THEME]).  Switching theme =
    re-point ``active`` + request a redraw; no widget code changes.

    Extends the shipped holder with the S2 font-scale token
    ([CXC-ICP-S2-FONTSCALE]) and the S3 per-instance custom-color overlay
    ([CXC-ICP-S3-THEME]).  ``active`` is rebuilt from a base table + the
    overlay so identity holds (active IS DARK when base=dark and no custom),
    keeping the shipped ``active is DARK`` fidelity checks green."""

    def __init__(self) -> None:
        self._base: ConsoleTheme = DARK
        self._custom: Dict[str, RGBA] = {}
        self.active: ConsoleTheme = DARK
        # [CXC-ICP-S2-FONTSCALE] the font-scale MULTIPLIER token (default 1.0).
        # Threaded through every text draw+measure at the Canvas set_font seam
        # (SkiaCanvas._font_scale); this is the console-side source of truth,
        # seeded from Foundation A's console.font_scale.
        self.font_scale: float = 1.0

    def _rebuild(self) -> None:
        self.active = _overlay(self._base, self._custom)

    def set_theme(self, name: Optional[str]) -> None:
        if name:
            base = _TABLES.get(str(name).lower())
            if base is not None:
                self._base = base
        self._rebuild()

    def apply_custom(self, overrides: Any) -> None:
        self._custom = normalize_custom(overrides)
        self._rebuild()

    def clear_custom(self) -> None:
        self._custom = {}
        self._rebuild()

    def set_font_scale(self, value: Any) -> None:
        self.font_scale = resolve_font_scale(value)

    def toggle(self) -> str:
        self._base = LIGHT if self._base is DARK else DARK
        self._rebuild()
        return self.active.name


CONSOLE = _ConsoleHolder()


def set_theme(name: Optional[str]) -> None:
    """Pick the active token table by name ('dark'|'light'|'high-contrast');
    unknown/None keeps the current base table (graceful, [CXC-ICP-S3-THEME]
    fallback)."""
    CONSOLE.set_theme(name)


def apply_custom_colors(overrides: Any) -> None:
    """Apply the per-instance theme_custom overlay (bg/accent) on the active
    table ([CXC-ICP-S3-THEME])."""
    CONSOLE.apply_custom(overrides)


def clear_custom_colors() -> None:
    CONSOLE.clear_custom()


def toggle_theme() -> str:
    return CONSOLE.toggle()


# ---------------------------------------------------------------------------
# Font scale ([CXC-ICP-S2-FONTSCALE]) — a single multiplier threaded through
# every text draw+measure at the Canvas set_font seam.
# ---------------------------------------------------------------------------

# David's D3 ruling: DISCRETE S / M / L, not a continuous slider.
FONT_SCALE_STEPS: Dict[str, float] = {"s": 0.875, "m": 1.0, "l": 1.25}
# The spec-stated supported multiplier bound ([CXC-ICP-S2-FONTSCALE]); a value
# outside is clamped, never rejected (reflow is verified across this range).
FONT_SCALE_MIN, FONT_SCALE_MAX = 0.75, 2.0


def resolve_font_scale(value: Any) -> float:
    """Resolve a font-scale spec to a clamped multiplier.  Accepts an S/M/L
    step name (David's discrete control) OR a raw float; anything malformed
    falls back to 1.0 (never raises — a bad persisted value must not crash a
    launch)."""
    if isinstance(value, bool):
        return 1.0
    if isinstance(value, str):
        v = value.strip().lower()
        if v in FONT_SCALE_STEPS:
            return FONT_SCALE_STEPS[v]
        try:
            value = float(v)
        except ValueError:
            return 1.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 1.0
    if f <= 0:
        return 1.0
    return max(FONT_SCALE_MIN, min(FONT_SCALE_MAX, f))


def set_font_scale(value: Any) -> float:
    """Set the console font-scale token and return the resolved multiplier."""
    CONSOLE.set_font_scale(value)
    return CONSOLE.font_scale


# ---------------------------------------------------------------------------
# Typography roles (px at the pre-vis 16px root; [CXC-ICV-TOKENS])
# ---------------------------------------------------------------------------

FONT_UI = "sans-serif"      # -> bundled IBM Plex Sans   ([CXC-R-DRAW-FONT])
FONT_MONO = "monospace"     # -> bundled IBM Plex Mono

DISPLAY = 20.0        # header H1 (weight 600 ~ "bold")
BODY = 16.0           # thread prose
REPLY_WHO = 15.0      # "architect · reply" (1.05em of body)
NARRATION = 14.0      # mirrored thinking-aloud (.86rem; italic -> dim approx)
LABEL_MONO = 9.6      # card headers / who-lines (.6rem, uppercase, tracked)
NS_TAG = 9.3          # namespace tag on a card (.58rem, accent @85%)
READOUT_XL = 34.0     # gauge big number (2.1rem, state-colored)
READOUT_XL_SUFFIX = 18.0
READOUT = 15.0        # ledger cell value (.92rem)
READOUT_SMALL = 9.6   # ledger cell dim suffix (.6rem)
TELEMETRY = 10.0      # reg/activity/subagents rows (.6-.72rem mono)
CODE_EM = 0.84        # inline code, relative to context

TRACK_LABEL = 1.4     # px advance nudge for tracked UPPERCASE mono labels
NS_TAG_ALPHA = 217    # accent @ .85 for ns tags


# ---------------------------------------------------------------------------
# Spacing / radii / elevation ([CXC-ICV-TOKENS])
# ---------------------------------------------------------------------------

SPACE = (2, 4, 6, 8, 10, 12, 13, 16, 18, 20, 22)

PAD_FRAME = 20.0          # frame outer padding
GAP_SECTION = 16.0        # inter-section gap
GAP_RAIL = 13.0           # rail inter-card gap
PAD_CARD_HEADER = (8.0, 13.0)
PAD_CARD_BODY = 13.0
PAD_THREAD_Y = 22.0
GAP_TURN = 20.0
THREAD_MEASURE = 560.0    # ~65ch cap at console width (documented approx)

RADIUS_BRANDMARK = 11.0
RADIUS_READ = 16.0
RADIUS_CARD = 14.0
RADIUS_INNER = 9.0        # activity/subagents/gauge inner panels (8-10)
RADIUS_COMPOSER = 10.0
RADIUS_BUBBLE = 12.0      # single-radius approximation of 14/14/4/14
RADIUS_BADGE = 4.0
RADIUS_PACKET = 9.0

SHADOW_AMBIENT = (0.0, 12.0, 34.0)   # dx, dy, blur — the second pre-vis stop

# [CXC-R-DAMAGE] Paint overflow (logical px) for any card drawn via
# draw_card: its ambient drop shadow (blur 34, offset dy 12) softly paints
# OUTSIDE the card's arranged rect — and, because rail cards are stacked, a
# lower card's shadow lands visibly on the card ABOVE it.  A card widget sets
# Widget.paint_overflow to this so the engine's dirty-region pruning keeps the
# card in a partial redraw whenever the region it shadows is damaged, which is
# what makes an ambient-animation partial redraw composite pixel-identically.
# Sized to the shadow's visible reach (blur + offset + AA slack).
SHADOW_PAINT_OVERFLOW = 60.0


# ---------------------------------------------------------------------------
# Motion inventory ([CXC-ICV-MOTION]) — periods (s) + dip values
# ---------------------------------------------------------------------------

BEAT_PERIOD, BEAT_LOW = 2.6, 0.35            # header live-dot
BREATHE_PERIOD = 5.5                          # avatar halo (scale 1->1.06,
BREATHE_SCALE_HI, BREATHE_OP_LO = 1.06, 0.85  #  opacity .85->1)
BLINK_PERIOD, BLINK_WINDOW, BLINK_SCALE = 6.4, (0.055, 0.065), 0.12
HOTPULSE_PERIOD, HOTPULSE_LOW = 2.4, 0.45     # gauge lead arc, crit zone only
RUNPULSE_PERIOD, RUNPULSE_LOW = 1.6, 0.35     # subagent running dot


def pulse(now: float, period: float, low: float) -> float:
    """Ease-in-out opacity loop: 1.0 at phase 0, dipping to *low* at the
    half-period — the CSS ``0%,100%{1} 50%{low}`` keyframe as a cosine."""
    if period <= 0:
        return 1.0
    return low + (1.0 - low) * (0.5 + 0.5 * math.cos(
        2.0 * math.pi * (now % period) / period))


def breathe(now: float) -> Tuple[float, float]:
    """(scale, opacity) of the avatar halo at clock *now*."""
    ph = 0.5 - 0.5 * math.cos(2.0 * math.pi * (now % BREATHE_PERIOD)
                              / BREATHE_PERIOD)
    return (1.0 + (BREATHE_SCALE_HI - 1.0) * ph,
            BREATHE_OP_LO + (1.0 - BREATHE_OP_LO) * ph)


def blink_scale(now: float) -> float:
    """Eye scaleY at clock *now*: 1.0 for ~96% of the cycle, a ~60ms
    step-collapse to BLINK_SCALE inside the blink window."""
    frac = (now % BLINK_PERIOD) / BLINK_PERIOD
    lo, hi = BLINK_WINDOW
    return BLINK_SCALE if lo <= frac <= hi else 1.0


def reduced_motion(ctx) -> bool:
    """The scene-level reduced-motion gate ([CXC-ICV-MOTION]): plumbed from
    ``design.reduced_motion`` onto the RenderContext; absent -> False."""
    return bool(getattr(ctx, "reduced_motion", False))


# ---------------------------------------------------------------------------
# Shared console chrome — the header + bordered-panel idiom, built ONCE
# ([CXC-ICV-BUILD] step 3) and drawn by every rail card.
# ---------------------------------------------------------------------------

def theme_from_ctx(ctx) -> ConsoleTheme:
    """Adopt the scene's design.theme at mount ([CXC-ICV-THEME]) and return
    the active table.  Call from on_mount / draw; None ctx keeps current."""
    if ctx is not None:
        CONSOLE.set_theme(getattr(ctx, "theme", None))
    return CONSOLE.active


def draw_card(canvas: Canvas, x: float, y: float, w: float, h: float,
              radius: float = RADIUS_CARD,
              t: Optional[ConsoleTheme] = None) -> None:
    """Rail-card / read-column chrome: ambient shadow, panel2->panel
    vertical gradient fill, 1px ``line`` border ([CXC-ICV-TOKENS])."""
    t = t or CONSOLE.active
    dx, dy, blur = SHADOW_AMBIENT
    canvas.set_shadow(dx, dy, blur, t.shadow)
    canvas.set_linear_gradient(x, y, x, y + h, [
        GradientStop(0.0, *t.panel2),
        GradientStop(1.0, *t.panel)])
    canvas.fill_round_rect(x, y, w, h, radius)
    canvas.clear_shadow()
    canvas.set_fill_color(*t.panel)          # clears the gradient override
    canvas.set_stroke_color(*t.line)
    canvas.set_stroke_width(1.0)
    canvas.stroke_round_rect(x + 0.5, y + 0.5, w - 1, h - 1, radius)


def draw_card_header(canvas: Canvas, x: float, y: float, w: float,
                     title: str, ns_tag: str = "",
                     count_text: str = "",
                     count_color: Optional[RGBA] = None,
                     t: Optional[ConsoleTheme] = None) -> float:
    """The pre-vis ``.ph`` header row: tracked UPPERCASE mono title (faint),
    optional count (e.g. "4/4" in ok), right-aligned ns-tag (accent @.85),
    1px bottom border.  Returns the y where the card BODY starts."""
    t = t or CONSOLE.active
    pv, ph = PAD_CARD_HEADER
    canvas.set_font(FONT_MONO, LABEL_MONO, "regular")
    m = canvas.measure_text("Ag")
    baseline = y + pv + m.ascent
    # Card titles render in INK (near-white), not `faint`: at LABEL_MONO size,
    # tracked + uppercase, faint (#55616F) is genuinely hard to read against the
    # dark ground — David's glass call, 2026-07-21 ("SELF / REGISTERED ... suggest
    # the font color be changed to white"). The mutedness that keeps the rail calm
    # comes from the small tracked-uppercase TREATMENT, not from dimming the ink.
    tw = draw_tracked(canvas, title.upper(), x + ph, baseline, TRACK_LABEL,
                      t.ink)
    if count_text:
        canvas.set_font(FONT_MONO, LABEL_MONO + 1.5, "bold")
        canvas.set_fill_color(*(count_color or t.ok))
        canvas.draw_text(count_text, x + ph + tw + 8, baseline, "left")
    if ns_tag:
        canvas.set_font(FONT_MONO, NS_TAG, "regular")
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                              NS_TAG_ALPHA)
        canvas.draw_text(ns_tag, x + w - ph, baseline, "right")
    header_h = pv * 2 + m.ascent + m.descent
    canvas.set_stroke_color(*t.line)
    canvas.set_stroke_width(1.0)
    canvas.draw_line(x, y + header_h, x + w, y + header_h)
    return y + header_h


def draw_tracked(canvas: Canvas, text: str, x: float, baseline: float,
                 tracking: float, color: RGBA) -> float:
    """Letter-spacing approximation ([CXC-ICV-TOKENS]): draw per-glyph with
    an advance nudge.  For SHORT labels only.  Returns the total width."""
    canvas.set_fill_color(*color)
    cx = x
    for ch in text:
        canvas.draw_text(ch, cx, baseline, "left")
        cx += canvas.measure_text(ch).width + tracking
    return cx - x - (tracking if text else 0.0)


def measure_tracked(canvas: Canvas, text: str, tracking: float) -> float:
    w = sum(canvas.measure_text(ch).width + tracking for ch in text)
    return w - (tracking if text else 0.0)


def draw_dashed_line(canvas: Canvas, x1: float, y: float, x2: float,
                     dash: float = 4.0, gap: float = 4.0) -> None:
    """Horizontal dashed rule composed from draw_line segments (the
    documented dashed-border approximation)."""
    x = x1
    while x < x2:
        canvas.draw_line(x, y, min(x + dash, x2), y)
        x += dash + gap


def draw_status_dot(canvas: Canvas, cx: float, cy: float, r: float,
                    color: RGBA, halo: Optional[RGBA] = None,
                    halo_w: float = 3.0) -> None:
    """A status dot with an optional dim halo ring (the pre-vis
    ``box-shadow: 0 0 0 3px <dim>`` look, composed from two circles)."""
    if halo is not None:
        canvas.set_fill_color(*halo)
        canvas.fill_circle(cx, cy, r + halo_w)
    canvas.set_fill_color(*color)
    canvas.fill_circle(cx, cy, r)


def ellipsize(canvas: Canvas, text: str, max_w: float) -> str:
    """measure_text-driven ellipsis truncation (backend-identical)."""
    if canvas.measure_text(text).width <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if canvas.measure_text(cand).width <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def mix(c1: RGBA, c2: RGBA, frac: float) -> RGBA:
    """Linear blend of two RGBA tokens."""
    return tuple(int(a + (b - a) * frac) for a, b in zip(c1, c2))  # type: ignore
