"""
ComposeUX LaunchTileWidget ([SPEC:CXC-LAUNCHBAR] [CXC-LB-TILE] [CXC-LB-ACTIVE])

One tile of the ComposePlatform Launch Bar ribbon: a monochrome icon (an SVG
asset from ``apps/compose_platform_launch_bar/artwork/``) with a text label BELOW
it, carrying exactly one launch intent.  A press PUBLISHES the tile's
``_app/<target>/open`` intent and NOTHING else ([CXC-LB-INTENT-BINDING]) — the
tile never spawns; the in-process launcher owns the runtime
([CXC-LB-CREDO] widget-owns-intent / launcher-owns-runtime split).

Why a dedicated widget and not ``emit_button`` (the reuse-first default):
``emit_button.draw`` renders command-button chrome (an LED dot + a beveled
button body) — visually wrong for a flat icon+label ribbon tile.  A tile also
carries state ``emit_button`` has no notion of: the bus-driven ACTIVE state
([CXC-LB-ACTIVE], at-most-one, derived from ``_app/current`` — never guessed),
a disabled/dimmed state for a not-yet-built target (so it never publishes into
the void, [CXC-LB-INTENT-TABLE]), and press feedback.  The one drawing path it
DOES reuse — the icon — is the SVG-to-Canvas pipeline (``render_svg``), the
SAME contract decomposition ``SvgWidget`` uses, by reference, not a fork.

Three visual states, all distinct ([CXC-LB-TILE]):
  * ``idle``   — icon on the bare ribbon.
  * ``active`` — this tile's app is the current app: a near-black inset chip is
    drawn behind the icon (the mockup's pressed treatment — the chip is DARKER
    than the ribbon, the icon is unchanged).
  * pressed    — a faint highlight while the pointer is held (tactile feedback).
A ``disabled`` tile (target not built yet) is dimmed and non-interactive.

BOUND STATUS AFFORDANCE ([DX-NARROW-WAIST], [DX-STATE-ON-BUS]) — GENERAL and
OPTIONAL, so ANY tile can show a bus-driven status without a line of tile-
specific logic (the thresholds/policy live in the SCENE expression, never
here):
  * ``icon_tint`` — a bound COLOUR.  When set, the monochrome SVG icon is
    recoloured to it wholesale (via ``render_svg(..., tint=...)``).  Absent
    (None) => today's #e3e3e3 asset colour, unchanged.
  * ``blink``     — a bound BOOL.  While true, the icon's VISIBILITY toggles
    ON/OFF at ``_BLINK_PERIOD_S`` (0.5 s) — a temporal behaviour the leaf owns
    (state on the bus, animation in the leaf).  The blink phase is a pure
    function of the logical clock (on_clock_tick), so it is deterministic under
    render_frame_at(t) the same way Lottie playback is.  Absent/false => steady.
Both are declared in the scene as ordinary bindings ({expr}/{namespace}); the
tile that declares neither is byte-for-byte its old self.

Uses ONLY the abstract Canvas contract + the SVG pipeline.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..svg import SvgDocument, SvgError, render_svg
from ..widget import Constraints, Rect, Widget

# Tokens lifted from the mockup (apps/compose_platform_launch_bar/mockup.svg) —
# ribbon #1c1c28, active chip #000000@~0.95, icon #e3e3e3, label #ffffff.
CHIP_PRESSED = (0x00, 0x00, 0x00, 242)    # near-black inset — the click-and-hold
                                          # dark box (David's previs pressed state)
LABEL_INK = (0xFF, 0xFF, 0xFF, 255)
LABEL_DIM = (0x8B, 0x98, 0xAA, 255)       # console_theme dim — disabled label
DISABLED_OPACITY = 0.38

# Bound-BLINK cadence ([DX-STATE-ON-BUS] animation-in-the-leaf).  The icon is
# visible for the first half of each period and hidden for the second, so the
# on/off duty is 0.5 s each — a full 1.0 s cycle.  Phase is a pure function of
# the logical clock, so it is byte-deterministic under render_frame_at(t).
_BLINK_PERIOD_S = 0.5

# ---------------------------------------------------------------------------
# SIZING — the two numbers a resize edits ([CXC-LB-TILE])
#
# David 2026-08-05 (DEFECT-LAUNCH-BAR-ILLEGIBLE, "simply can't read them at
# all"): the mockup-derived 28 px icon / 7.5 pt label are microscopic on the
# console's 4K glass (SDL reports 3840x2160 LOGICAL px there — 1 pt == 1 device
# px, so nothing is scaled up for Retina).  Icons go to 64 px, labels to 12 pt,
# and the ribbon GROWS to fit (David explicitly accepted the reflow).
#
# EVERYTHING below is DERIVED from ICON_BOX + LABEL_FONT — a future resize is
# these two edits plus re-materializing the scene's absolute tile offsets (the
# `RIBBON GEOMETRY` block below states them; test_launch_bar asserts the
# .compose.yaml still matches, so drift is a RED test, not a hunt).
# ---------------------------------------------------------------------------
ICON_BOX = 64.0                 # was 28.0 — the monochrome SVG's square box
LABEL_FONT = 12.0               # was 7.5 — bold sans, the label below the icon

ICON_PAD_TOP = 8.0              # tile top -> icon top
LABEL_GAP = 4.0                 # icon bottom -> label block top
LABEL_PAD = 6.0                 # per-side inset the label wraps within
TILE_PAD_BOTTOM = 6.0           # last label baseline's descent -> tile bottom
# Reserved label lines.  Longest labels at 12 pt bold measure: "ComposeBus" 74
# (ONE word — it cannot wrap, so it sets the width floor), "Document Storage"
# 106 -> wraps to "Document"(58) / "Storage"(45).  So: 2 lines, and the label
# column must be >= 74 px wide.
LABEL_LINES = 2
# measure_text("Ag") at 12 pt bold: ascent 13, descent 4 -> line advance 18.
# Engine-rasterized FreeType glyphs are platform-identical ([CXC-R-C1]), so
# this is a MEASURED constant, not a guess; test_launch_bar re-measures it.
LABEL_LINE_H = 18.0

# The label column floor drives the tile (and therefore ribbon) WIDTH.
DEFAULT_TILE_W = 96.0           # was 72.0 — 96 - 2*6 = 84 >= 74 ("ComposeBus")
DEFAULT_TILE_H = (ICON_PAD_TOP + ICON_BOX + LABEL_GAP
                  + LABEL_LINES * LABEL_LINE_H + TILE_PAD_BOTTOM)   # 118.0

# pressed inset chip — wraps the icon box with a little air.
CHIP_H = ICON_BOX + 12.0        # was 42.0
CHIP_W_MAX = ICON_BOX + 16.0    # was 60.0
CHIP_INSET_TOP = 6.0            # chip top sits this far above the icon
CHIP_RADIUS = 12.0              # was 7.0 (kept ~0.16 x chip height)

# ---------------------------------------------------------------------------
# RIBBON GEOMETRY — the column that stacks these tiles ([CXC-LB-RIBBON]).
#
# `pixel_stack` is ABSOLUTE layout, so launch_bar.compose.yaml carries these as
# literal numbers; they live here (beside the tile metrics they derive from) so
# one edit above regenerates the whole column.  Brand-logo box + gaps are the
# pre-resize proportions scaled by RIBBON_W/72 (David: the logo scales WITH the
# wider ribbon), so the ribbon keeps its mockup rhythm.
# ---------------------------------------------------------------------------
RIBBON_W = DEFAULT_TILE_W                       # 96 (spec default was 72)
TILE_COUNT = 7                                  # [CXC-LB-ROSTER]
LOGO_BOX = 58.0                                 # 44 * 96/72
LOGO_Y = 16.0                                   # 12 * 96/72
LOGO_X = (RIBBON_W - LOGO_BOX) / 2.0            # 19.0 — centered in the ribbon
TILE_TOP = 96.0                                 # 72 * 96/72 — first tile's y
TILE_PITCH = DEFAULT_TILE_H                     # tiles are contiguous
RIBBON_PAD_BOTTOM = 10.0                        # 8 * 96/72, rounded
RIBBON_H = TILE_TOP + TILE_COUNT * TILE_PITCH + RIBBON_PAD_BOTTOM   # 932.0


def tile_y(index: int) -> float:
    """Absolute y of tile *index* (0-based) in the ribbon's pixel_stack — the
    generator for the scene's literal offsets ([CXC-LB-RIBBON])."""
    return TILE_TOP + index * TILE_PITCH


class LaunchTileWidget(Widget):
    """A launch-bar tile: a monochrome icon + label + one launch intent.

    No persistent "current app" highlight is drawn (David 2026-07-29: the
    previs dark box was the PRESSED/held treatment, not an active-app
    indicator) — the only chip is the click-and-hold press feedback.
    """

    def __init__(self, widget_id: str, *,
                 label: str = "",
                 target: str = "",
                 icon: str = "",
                 intent_namespace: Optional[str] = None,
                 enabled: Any = True,
                 icon_tint: Any = None,
                 blink: Any = False,
                 width: float = DEFAULT_TILE_W,
                 height: float = DEFAULT_TILE_H,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "launch_tile", **kwargs)
        self.label = str(label)
        self.target = str(target)
        self.icon = str(icon)
        self.intent_namespace = str(intent_namespace or f"_app/{self.target}/open")
        self.enabled = bool(enabled)
        self.tile_w = float(width)
        self.tile_h = float(height)
        # only an enabled tile is a click-activation target — a not-built tile
        # is dimmed AND inert, so it can never publish into the void.
        self.interactive = self.enabled

        # -- bound status affordance ([DX-NARROW-WAIST], both OPTIONAL) -------
        # ``icon_tint``: an RGB(A) tuple (set by the color binding via setattr)
        # or None (asset colour).  ``blink``: a bool the bool binding drives.
        self.icon_tint: Any = icon_tint
        self.blink: bool = bool(blink)
        # icon-visible flag the clock maintains while blinking (steady => True).
        self._blink_on = True

        self._pressed = False
        # icon SVG, decomposed to the Canvas contract (render_svg), loaded lazily
        self._doc: Optional[SvgDocument] = None
        self._load_tried = False

    # -- interaction ([CXC-LB-INTENT-BINDING]) ------------------------------
    def on_pointer_down(self, x: float, y: float) -> None:
        if self.enabled:
            self._pressed = True
            self.request_redraw()

    def on_pointer_up(self, x: float, y: float) -> None:
        if self._pressed:
            self._pressed = False
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        """Publish the launch intent and nothing else — the tile does not
        spawn, focus, or mutate app state ([CXC-LB-TILE] press behavior)."""
        if self.enabled:
            self.publish(self.intent_namespace, "open")

    # -- bound BLINK ([DX-STATE-ON-BUS] animation-in-the-leaf) --------------
    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        # register for the shared logical clock so the blink phase is sampled
        # identically to Lottie playback ([CXC-LOT-DETERMINISM]); harmless when
        # the tile never blinks (on_clock_tick early-returns steady).
        reg = getattr(ctx, "register_clocked", None)
        if reg is not None:
            reg(self)

    def on_unmount(self) -> None:
        unreg = getattr(self._ctx, "unregister_clocked", None) \
            if self._ctx is not None else None
        if unreg is not None:
            unreg(self)                              # stale-instance hygiene
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        """Advance the blink phase at logical clock *now*.  Returns True while
        the tile is actively blinking (keeps the composition dirty), False when
        steady.  Phase is pure arithmetic on *now*, so a sampled frame is
        byte-identical across backends and under render_frame_at(t)."""
        if not self.blink:
            if not self._blink_on:                   # recovered -> show steady
                self._blink_on = True
                return True                          # one repaint to un-hide
            return False
        phase_on = (int(now / _BLINK_PERIOD_S) % 2) == 0
        self._blink_on = phase_on
        return True                                  # still animating

    # -- icon load (SVG -> Canvas contract, reused by reference) ------------
    def _ensure_icon(self) -> Optional[SvgDocument]:
        if self._doc is not None or self._load_tried or not self.icon:
            return self._doc
        self._load_tried = True
        data: Optional[bytes] = None
        assets = getattr(self._ctx, "assets", None) if self._ctx else None
        if assets is not None:
            data = assets.get_bytes(self.icon)
        else:
            try:
                from composeio import resolve as _resolve
                data = _resolve.resolve_bytes(self.icon)
            except Exception:
                data = None
        if data is not None:
            try:
                self._doc = SvgDocument.from_bytes(data)
            except SvgError:
                self._doc = None
        return self._doc

    # -- measure / draw -----------------------------------------------------
    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.tile_w, constraints.max_width),
                    min(self.tile_h, constraints.max_height))

    def _wrap_label(self, canvas: Canvas, max_w: float) -> List[str]:
        """Greedy word-wrap the label to fit *max_w* (measure-driven, so the
        break is backend-identical).  A single over-wide word is kept whole."""
        words = self.label.split()
        if not words:
            return []
        lines: List[str] = []
        cur = words[0]
        for w in words[1:]:
            cand = cur + " " + w
            if canvas.measure_text(cand).width <= max_w:
                cur = cand
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        cx = rect.x + rect.w / 2.0

        # PRESSED chip: the near-black inset behind the icon shown ONLY while the
        # tile is clicked-and-held.  (David 2026-07-29: the previs dark box was
        # illustrating the pressed/held treatment, NOT a persistent active-app
        # highlight.)  No persistent chip for the current app — the active-tile
        # indicator, if any, is a separate treatment still TBD ([CXC-LB-ACTIVE]).
        if self._pressed:
            chip_w = min(rect.w - 8.0, CHIP_W_MAX)
            chip_x = cx - chip_w / 2.0
            chip_y = rect.y + ICON_PAD_TOP - CHIP_INSET_TOP
            canvas.set_fill_color(*CHIP_PRESSED)
            canvas.fill_round_rect(chip_x, chip_y, chip_w, CHIP_H, CHIP_RADIUS)

        # dim the icon+label together for a disabled (not-built) tile.
        dim = not self.enabled
        if dim:
            canvas.save()
            canvas.set_opacity(DISABLED_OPACITY)

        # icon — SVG decomposed to Canvas primitives (render_svg), the same
        # contract path SvgWidget uses ([CXC-LB-RIBBON] constructed-from).
        doc = self._ensure_icon()
        icon_y = rect.y + ICON_PAD_TOP
        icon_rect = Rect(cx - ICON_BOX / 2.0, icon_y, ICON_BOX, ICON_BOX)
        # bound BLINK ([DX-STATE-ON-BUS]) hides the icon on the off-phase — a
        # visibility toggle, so the label + chrome are unaffected.  Steady tiles
        # keep _blink_on True.
        icon_hidden = self.blink and not self._blink_on
        # bound TINT ([DX-NARROW-WAIST]) recolours the monochrome icon wholesale;
        # None keeps the asset's own #e3e3e3.
        tint = tuple(self.icon_tint[:3]) if self.icon_tint else None
        if not icon_hidden:
            if doc is not None:
                render_svg(canvas, doc, icon_rect, "contain", tint=tint)
            else:
                # missing/unresolved icon -> label text on a neutral swatch,
                # never a blank tile ([CXC-LB-TILE] icon-source fallback; logged
                # by the parser).  The swatch is chrome, not the icon, so it is
                # not tinted.
                canvas.set_fill_color(0x28, 0x28, 0x3C, 255)
                canvas.fill_round_rect(icon_rect.x, icon_rect.y, ICON_BOX,
                                       ICON_BOX, CHIP_RADIUS * 0.5)

        # label BELOW the icon, centered, wrapped ([CXC-LB-TILE]).
        canvas.set_font("sans-serif", LABEL_FONT, "bold")
        canvas.set_fill_color(*(LABEL_DIM if dim else LABEL_INK))
        m = canvas.measure_text("Ag")
        line_h = m.ascent + m.descent + 1.0
        ly = icon_y + ICON_BOX + LABEL_GAP + m.ascent
        for line in self._wrap_label(canvas, rect.w - 2 * LABEL_PAD):
            canvas.draw_text(line, cx, ly, "center")
            ly += line_h

        if dim:
            canvas.restore()
