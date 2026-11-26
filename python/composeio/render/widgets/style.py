"""
ComposeUX M2 widget style — the production dashboard look.

These constants are the CTk "dark-blue" theme (dark mode) values the
production renderers actually rendered with, captured at the M2 port so
the engine widgets match the CTk dashboard ([CXC-RM-M2] parity gate).
Once CTk retires this file IS the ComposeUX widget identity; edits here
re-skin every backend at once.

All text renders in the bundled IBM Plex faces ([CXC-R-DRAW-FONT]).
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..canvas import Canvas

# -- palette (CTk dark-blue theme, dark mode) -------------------------------

WINDOW_BG = (0x1A, 0x1A, 0x1A)          # CTk gray10
FRAME_BG = (0x21, 0x21, 0x21)           # CTkFrame gray13
FRAME_TOP_BG = (0x29, 0x29, 0x29)       # CTkFrame top gray16

BTN_FILL = (0x1F, 0x53, 0x8D)           # CTkButton fg
BTN_PRESSED = (0x14, 0x37, 0x5E)        # CTkButton hover/pressed
BTN_TEXT = (0xDC, 0xE4, 0xEE)
BTN_TEXT_DISABLED = (0x99, 0x99, 0x99)  # gray60
BTN_WARM = (0xB8, 0x4A, 0x2E)           # destructive / confirm-gated
BTN_RADIUS = 6.0

ENTRY_BG = (0x34, 0x36, 0x38)
ENTRY_BORDER = (0x56, 0x5B, 0x5E)
ENTRY_BORDER_FOCUS = (0x1F, 0x53, 0x8D)
ENTRY_TEXT = (0xD6, 0xD6, 0xD6)         # gray84
ENTRY_PLACEHOLDER = (0x9E, 0x9E, 0x9E)  # gray62

TEXT = (0xD6, 0xD6, 0xD6)               # CTkLabel gray84
TEXT_DIM = (0x88, 0x88, 0x88)
TEXT_STATUS = (0xAA, 0xAA, 0xAA)

CHECK_BORDER = (0x94, 0x9A, 0x9F)
CHECK_FILL = (0x1F, 0x53, 0x8D)
CHECK_MARK = (0xDC, 0xE4, 0xEE)

TEXTBOX_BG = (0x33, 0x33, 0x33)         # CTkTextbox gray20

SEG_BG = (0x4A, 0x4A, 0x4A)             # CTkSegmentedButton gray29
SEG_SELECTED = (0x1F, 0x53, 0x8D)
SEG_TEXT = (0xDC, 0xE4, 0xEE)

# LED / status colors (verbatim from the CTk renderers)
LED_OFF = (0x55, 0x55, 0x55)
LED_ACTIVE = (0x3B, 0x8E, 0xD0)
LED_INTERMEDIATE = (0x2B, 0x6E, 0xA0)
LED_ALARM = (0xE0, 0x3B, 0x3B)
LED_OUTLINE = (0x33, 0x33, 0x33)

GREEN = (0x2E, 0xCC, 0x71)
YELLOW = (0xF1, 0xC4, 0x0F)
RED = (0xE7, 0x4C, 0x3C)
GRAY = (0x55, 0x55, 0x55)

STATUS_COLORS = {                        # CommandWidget lifecycle dot
    "idle": (0x88, 0x88, 0x88),
    "invoking": (0xF1, 0xC4, 0x0F),
    "running": (0x3B, 0x8E, 0xD0),
    "ok": (0x2E, 0xCC, 0x71),
    "fail": (0xE0, 0x3B, 0x3B),
    "blocked": (0xE6, 0x7E, 0x22),
    "not_configured": (0xE6, 0xB4, 0x50),   # amber: nothing was probed
}

FONT_UI = "sans-serif"        # -> IBM Plex Sans
FONT_MONO = "monospace"       # -> IBM Plex Mono
# The Colonial-OS pixel face ([SPEC:CXC-CIC], Tiny5 SIL OFL).  The BSG-project
# default for genuinely WIDGET-DRAWN dynamic text (tab labels, button captions,
# titles) — David's SVG <text> elements carry their OWN font and are rendered
# faithfully; this is only for text the ENGINE draws.  Routed only when named
# ([CXC-R-DRAW-FONT]); Plex stays the default everywhere else.
FONT_PIXEL = "Tiny5"


# -- shared drawing helpers ---------------------------------------------------

def draw_button(canvas: Canvas, x: float, y: float, w: float, h: float,
                text: str, *,
                fill: Tuple[int, int, int] = BTN_FILL,
                pressed: bool = False,
                disabled: bool = False,
                font_size: float = 13.0,
                radius: float = BTN_RADIUS) -> None:
    """A CTkButton-look button: rounded rect + centered label."""
    body = BTN_PRESSED if pressed else fill
    if disabled:
        body = tuple(int(c * 0.55) for c in fill)
    canvas.set_fill_color(*body)
    canvas.fill_round_rect(x, y, w, h, radius)
    canvas.set_fill_color(*(BTN_TEXT_DISABLED if disabled else BTN_TEXT))
    canvas.set_font(FONT_UI, font_size, "regular")
    m = canvas.measure_text(text)
    baseline = y + h / 2 + (m.ascent - m.descent) / 2
    canvas.draw_text(text, x + w / 2, baseline, "center")


def draw_dot(canvas: Canvas, cx: float, cy: float, r: float,
             color: Tuple[int, int, int],
             outline: Optional[Tuple[int, int, int]] = None) -> None:
    """A flat status dot (the CTk canvases drew flat ovals, not glowing LEDs)."""
    canvas.set_fill_color(*color)
    canvas.fill_circle(cx, cy, r)
    if outline is not None:
        canvas.set_stroke_color(*outline)
        canvas.set_stroke_width(1)
        canvas.stroke_circle(cx, cy, r)


def baseline_for(canvas: Canvas, y_center: float) -> float:
    """Baseline that vertically centers the current font at *y_center*."""
    m = canvas.measure_text("Ag")
    return y_center + (m.ascent - m.descent) / 2


def interpolate_color(c1: Tuple[int, int, int], c2: Tuple[int, int, int],
                      frac: float) -> Tuple[int, int, int]:
    return tuple(int(a + (b - a) * frac) for a, b in zip(c1, c2))
