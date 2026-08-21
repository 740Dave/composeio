"""
ComposeUX SevenSegmentDisplay — a generic seven-segment digital readout.

A publishable, project-neutral widget (peer of the carousel / radio-button-
group / segmented-tab-row): the WIDGET owns the segment-lighting LOGIC — the
value->segments truth table, the multi-glyph layout, the colon handling, the
bus bindings, and the clock substrate — while the SEGMENT SHAPES come from a
project SVG whose seven segment groups are named the standard a..g
([DEC-SVG-COMPOSITION-SOURCE-OF-TRUTH]).  No project artwork or IP lives here;
a BSG "seven_segment.svg" is merely one styling instance handed in via ``src``.

Own-every-pixel ([CXC-R-DP-1]): each glyph is drawn by rendering the SVG's
seven named segments through the engine's generic
:func:`~composeio.render.svg.render_element` (the SAME Canvas decomposition as
any SVG — crisp on every backend), recoloring each segment LIT or DIM per the
truth table.  There is no hardcoded segment geometry: the shapes, and their
relative placement, are read from the SVG.

Decomposition ([DEC-SEVEN-SEG-STRING]): this is a STRING display, not a tree
of single-digit child widgets.  ``value`` is a string ("12:34:56", "8",
"1234"); the widget lays out one glyph cell per character and draws each in its
own ``draw()`` — the same one-widget-draws-N-cells idiom as the tab-row /
radio-group / carousel.  A single digit is simply ``value="8"`` (N=1), so the
single-digit "primitive" is not lost — the reusable core (:func:`lit_segments`,
:func:`render_glyph`) is module-level and independently reusable.  A string
keeps colon kerning tight and the bus surface to ONE subscription per binding;
composing N child widgets through the layout engine would add arrangement
overhead and could not express the ``:`` cell.

Bus bindings ([SPEC:CXC-BIND], control-is-a-peer):
    color_namespace   the LIT segment color — publishing a color re-colors the
                      display reactively (hex "#RRGGBB" | [r,g,b(,a)] | {r,g,b}).
                      The DIM/off color is derived from it unless set explicitly.
    value_namespace   the displayed string (ignored while ``clock`` mode owns it).

Clock (first use, [CXC-SEVEN-SEG-CLOCK]): ``clock: true`` drives the value from
the wall clock via the engine's clocked-widget substrate
(``register_clocked`` / ``on_clock_tick`` — the same deterministic tick seam the
EXT rotation and Lottie playback ride).  The tick is the cadence pulse; the
widget recomputes the formatted time and damages ONLY itself when the displayed
string changes (``request_redraw()``; no full ``mark_dirty``).  NOTE the
displayed CONTENT is wall-clock-derived and therefore intentionally
non-deterministic across machines, but the RENDERING is a pure function of
``value`` + colors, so a given value renders byte-identically macOS<->RPi.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..svg import SvgDocument, SvgError, parse_path, render_element
from ..svg.model import MAT_IDENTITY, SvgNode, mat_apply, parse_length
from ..widget import Constraints, Rect, Widget

_SHAPE_TAGS = ("rect", "path", "circle", "ellipse", "polygon", "polyline")

RGBA = Tuple[int, int, int, int]
Box = Tuple[float, float, float, float]

# ---------------------------------------------------------------------------
# The seven segments (standard a..g) -> the SVG group labels David named.
# The generic letters are the contract; the labels are how THIS art template
# spells them.  A different art template with the same seven labels drops in.
# ---------------------------------------------------------------------------
SEGMENT_LABELS: Dict[str, str] = {
    "a": "Top",
    "b": "top-right",
    "c": "bottom-right",
    "d": "bottom",
    "e": "bottom-left",
    "f": "left-top",
    "g": "center",
}
SEGMENT_ORDER = ("a", "b", "c", "d", "e", "f", "g")

# ---------------------------------------------------------------------------
# Value -> lit segments (the standard seven-segment truth table).  Digits are
# the floor; ':' / '.' / ' ' are layout characters (own cells, no segments);
# hex A-F is included so the same widget drives a hex readout.
# ---------------------------------------------------------------------------
TRUTH_TABLE: Dict[str, str] = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgedc",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcfgd",
    "a": "abcefg",
    "b": "cdefg",
    "c": "adef",
    "d": "bcdeg",
    "e": "adefg",
    "f": "aefg",
    "-": "g",
    "_": "d",
    "°": "abfg",   # degree glyph
    " ": "",            # blank digit: nothing lit
}

# Characters that get their OWN narrow cell and are NOT seven-segment glyphs.
_PUNCT_WEIGHT = {":": 0.45, ".": 0.35}
_DIGIT_WEIGHT = 1.0


def lit_segments(char: str) -> frozenset:
    """The set of lit segment letters (subset of a..g) for a displayed
    character — the seven-segment truth table.  Case-insensitive; an unknown
    character lights nothing (a blank glyph), so the display never breaks."""
    return frozenset(TRUTH_TABLE.get(str(char).lower(), ""))


# ---------------------------------------------------------------------------
# SVG art helpers — the segment shapes are READ from the SVG, never hardcoded.
# ---------------------------------------------------------------------------

def find_segments(doc: SvgDocument) -> Dict[str, SvgNode]:
    """Map each standard segment letter (a..g) to its SvgNode in *doc*, matched
    by the ``inkscape:label`` (parsed to ``label``) David named on each group.
    Missing labels are simply absent from the map (that segment won't draw)."""
    by_label: Dict[str, SvgNode] = {}

    def walk(node: SvgNode) -> None:
        lbl = node.attrs.get("label")
        if lbl:
            by_label[lbl.strip().lower()] = node
        for ch in node.children:
            walk(ch)

    walk(doc.root)
    out: Dict[str, SvgNode] = {}
    for letter, label in SEGMENT_LABELS.items():
        node = by_label.get(label.lower())
        if node is not None:
            out[letter] = node
    return out


def _node_bbox(node: SvgNode) -> Optional[Box]:
    """Design-space bbox of a single segment node from its path/geometry."""
    d = node.attrs.get("d")
    if not d:
        return None
    xs: List[float] = []
    ys: List[float] = []
    for cmd in parse_path(d):
        for attr in ("x", "y", "cp1x", "cp1y", "cp2x", "cp2y",
                     "x1", "y1", "x2", "y2"):
            v = getattr(cmd, attr, None)
            if v is None:
                continue
            (xs if attr.endswith("x") or attr in ("x", "x1", "x2") else ys
             ).append(float(v))
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def segments_bbox(segs: Dict[str, SvgNode]) -> Optional[Box]:
    """Union design-space bbox over all found segments — the glyph design box
    that maps into each cell.  Pure geometry read from the SVG."""
    boxes = [b for b in (_node_bbox(n) for n in segs.values()) if b is not None]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def segment_top_y(segs: Dict[str, SvgNode], letter: str) -> Optional[float]:
    """The design-space y of the TOP edge of a named segment (a..g), read from
    the SVG geometry.  Used to align the colon dots to the digit's ``center``
    (g) and ``bottom`` (d) segment tops ([CXC-SEVEN-SEG-COLON])."""
    node = segs.get(letter)
    if node is None:
        return None
    b = _node_bbox(node)
    return b[1] if b is not None else None


def _recolored(node: SvgNode, color: RGBA) -> SvgNode:
    """A shallow copy of *node* whose fill is *color* (rgba).  The id is kept so
    the engine's ``render_element`` still resolves the element CTM; the segment
    geometry is untouched."""
    r, g, b, a = color
    style = f"fill:#{r:02x}{g:02x}{b:02x};stroke:none"
    if a < 255:
        style += f";fill-opacity:{a / 255.0:.4f}"
    attrs = dict(node.attrs)
    attrs["style"] = style
    attrs.pop("fill", None)
    return SvgNode(node.tag, attrs, node.children, node.text)


def render_glyph(canvas: Canvas, doc: SvgDocument, segs: Dict[str, SvgNode],
                 design: Box, char: str, rect: Rect,
                 lit_color: RGBA, dim_color: RGBA) -> None:
    """Render ONE seven-segment glyph into *rect*: every segment is drawn (lit
    segments in *lit_color*, the rest in *dim_color* — the classic ghosted
    off-segment look), mapping the *design* box into *rect* preserving aspect.
    This is the reusable single-digit primitive (the widget is N of these)."""
    on = lit_segments(char)
    for letter in SEGMENT_ORDER:
        node = segs.get(letter)
        if node is None:
            continue
        color = lit_color if letter in on else dim_color
        render_element(canvas, doc, _recolored(node, color), rect, design,
                       fit="contain")


# ---------------------------------------------------------------------------
# Colon separator art ([CXC-SEVEN-SEG-COLON]) — read from a project colon SVG
# (seven_segment_colon.svg = two dots) and recolored with the LIT segment color,
# so the ':' between HH/MM/SS follows the same SVG-source-of-truth + color
# binding as the digit segments (never programmatic geometry when art is given).
# ---------------------------------------------------------------------------

def find_colon_shapes(doc: SvgDocument) -> List[SvgNode]:
    """Every drawable shape in the colon art (the two dots), in document order.
    No labels are required — the colon SVG is a plain pair of glyphs."""
    out: List[SvgNode] = []

    def walk(node: SvgNode) -> None:
        if node.tag in _SHAPE_TAGS:
            out.append(node)
        for ch in node.children:
            walk(ch)

    walk(doc.root)
    return out


def _shape_design_bbox(doc: SvgDocument, node: SvgNode) -> Optional[Box]:
    """Design-space (post-CTM) bbox of a colon shape, so the union maps into the
    cell exactly the way ``render_element`` will place each recolored shape."""
    ctm = doc.element_ctm(node.attrs.get("id") or "") or MAT_IDENTITY
    pts: List[Tuple[float, float]] = []
    if node.tag == "rect":
        x = parse_length(node.attrs.get("x"), 0.0)
        y = parse_length(node.attrs.get("y"), 0.0)
        w = parse_length(node.attrs.get("width"), 0.0)
        h = parse_length(node.attrs.get("height"), 0.0)
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    elif node.attrs.get("d"):
        corners = []
        for cmd in parse_path(node.attrs["d"]):
            for ax, ay in (("x", "y"), ("cp1x", "cp1y"), ("cp2x", "cp2y"),
                           ("x1", "y1"), ("x2", "y2")):
                vx = getattr(cmd, ax, None)
                vy = getattr(cmd, ay, None)
                if vx is not None and vy is not None:
                    corners.append((float(vx), float(vy)))
    else:
        return None
    for px, py in corners:
        pts.append(mat_apply(ctm, px, py))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def colon_bbox(doc: SvgDocument, shapes: List[SvgNode]) -> Optional[Box]:
    """Union design-space bbox over the colon shapes."""
    boxes = [b for b in (_shape_design_bbox(doc, s) for s in shapes)
             if b is not None]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def render_colon(canvas: Canvas, doc: SvgDocument, shapes: List[SvgNode],
                 design: Box, rect: Rect, color: RGBA) -> None:
    """Render the colon art into *rect*, every shape recolored to *color* —
    the same recolor-and-``render_element`` path the digit segments use."""
    for shape in shapes:
        render_element(canvas, doc, _recolored(shape, color), rect, design,
                       fit="contain")


# ---------------------------------------------------------------------------
# Bus color payload parsing
# ---------------------------------------------------------------------------

def _coerce_rgba(value: Any) -> Optional[RGBA]:
    """Parse a bus color payload -> (r,g,b,a): "#RRGGBB"/"#RGB", [r,g,b(,a)],
    or {r,g,b(,a)}.  None for anything unparseable (the color is left as-is)."""
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip().lstrip("#")
        if len(s) == 3:
            s = "".join(c * 2 for c in s)
        if len(s) >= 6:
            try:
                return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16),
                        int(s[6:8], 16) if len(s) >= 8 else 255)
            except ValueError:
                return None
        return None
    if isinstance(value, dict):
        try:
            return (int(value["r"]), int(value["g"]), int(value["b"]),
                    int(value.get("a", 255)))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 3:
        try:
            v = [int(x) for x in value[:4]]
        except (TypeError, ValueError):
            return None
        return (v[0], v[1], v[2], v[3] if len(v) > 3 else 255)
    return None


def _derive_dim(lit: RGBA, factor: float = 0.16) -> RGBA:
    """A sensible OFF color derived from the lit color — a dark, same-hue
    ghost, so a display styled with only a lit color still reads correctly."""
    return (int(lit[0] * factor), int(lit[1] * factor), int(lit[2] * factor),
            lit[3])


# ---------------------------------------------------------------------------
# Widget
# ---------------------------------------------------------------------------

_PLACEHOLDER_BG = (0x1A, 0x1A, 0x22)
_PLACEHOLDER_INK = (0x66, 0x66, 0x78)
_DEFAULT_LIT: RGBA = (0xD9, 0x67, 0x19, 0xFF)   # the art's amber accent


class SevenSegmentDisplay(Widget):
    """A generic seven-segment digital readout driven by a project SVG."""

    def __init__(self, widget_id: str, *,
                 value: str = "",
                 src: str = "",
                 colon_src: str = "",
                 lit_color: Optional[RGBA] = None,
                 dim_color: Optional[RGBA] = None,
                 color_namespace: str = "",
                 value_namespace: str = "",
                 digits: int = 0,
                 spacing: float = 0.18,
                 clock: Any = False,
                 clock_format: str = "%H:%M:%S",
                 width: float = 0.0,
                 height: float = 120.0,
                 time_source: Any = None,
                 **kwargs: Any) -> None:
        # fields exist BEFORE super().__init__ so base-class kwargs that hit
        # setters find them (the Lottie/Svg convention).
        self._doc: Optional[SvgDocument] = None
        self._segs: Dict[str, SvgNode] = {}
        self._design: Optional[Box] = None
        self._load_tried = False
        self._src = ""
        self._value = ""
        self._dim_explicit = dim_color is not None
        # colon separator art (optional; falls back to programmatic dots)
        self._colon_doc: Optional[SvgDocument] = None
        self._colon_shapes: List[SvgNode] = []
        self._colon_design: Optional[Box] = None
        self._colon_load_tried = False
        self._colon_src = ""

        super().__init__(widget_id, "seven_segment", **kwargs)

        lit = _coerce_rgba(lit_color) if lit_color is not None else None
        self._lit: RGBA = lit or _DEFAULT_LIT
        self._dim: RGBA = (_coerce_rgba(dim_color) or _derive_dim(self._lit)) \
            if dim_color is not None else _derive_dim(self._lit)

        self.color_namespace = str(color_namespace or "")
        self.value_namespace = str(value_namespace or "")
        self.digits = int(digits)
        self.spacing = float(spacing)
        self.clock_format = str(clock_format or "%H:%M:%S")
        self.clock = bool(clock) if isinstance(clock, bool) \
            else str(clock).lower() not in ("", "0", "off", "false", "no")
        self.disp_width = float(width)
        self.disp_height = float(height)
        self._time_source = time_source or time.time
        self._last_second: Optional[int] = None

        self._src = str(src)
        self._colon_src = str(colon_src or "")
        self._value = "" if self.clock else str(value)

    # -- lit / dim color ---------------------------------------------------

    @property
    def lit_color(self) -> RGBA:
        return self._lit

    @lit_color.setter
    def lit_color(self, value: Any) -> None:
        col = _coerce_rgba(value)
        if col is None or col == self._lit:
            return
        self._lit = col
        if not self._dim_explicit:
            self._dim = _derive_dim(col)
        self.request_redraw()

    @property
    def dim_color(self) -> RGBA:
        return self._dim

    @dim_color.setter
    def dim_color(self, value: Any) -> None:
        col = _coerce_rgba(value)
        if col is None or col == self._dim:
            return
        self._dim = col
        self._dim_explicit = True
        self.request_redraw()

    # -- value -------------------------------------------------------------

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, new: Any) -> None:
        s = str(new)
        if s == self._value:
            return
        self._value = s
        self.request_redraw()

    # -- bindable src ------------------------------------------------------

    @property
    def src(self) -> str:
        return self._src

    @src.setter
    def src(self, value: Any) -> None:
        value = str(value or "")
        if value == self._src:
            return
        self._src = value
        self._doc = None
        self._segs = {}
        self._design = None
        self._load_tried = False
        self.request_redraw()

    def set_document(self, doc: SvgDocument) -> None:
        """Directly install a parsed art template (tests / embedders)."""
        self._doc = doc
        self._load_tried = True
        self._segs = find_segments(doc)
        self._design = segments_bbox(self._segs)

    @property
    def colon_src(self) -> str:
        return self._colon_src

    @colon_src.setter
    def colon_src(self, value: Any) -> None:
        value = str(value or "")
        if value == self._colon_src:
            return
        self._colon_src = value
        self._colon_doc = None
        self._colon_shapes = []
        self._colon_design = None
        self._colon_load_tried = False
        self.request_redraw()

    def set_colon_document(self, doc: SvgDocument) -> None:
        """Directly install the parsed colon art (tests / embedders)."""
        self._colon_doc = doc
        self._colon_load_tried = True
        self._colon_shapes = find_colon_shapes(doc)
        self._colon_design = colon_bbox(doc, self._colon_shapes)

    # -- bus wiring --------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace and namespace == self.color_namespace:
            self.lit_color = value            # setter coerces + redraws
        elif namespace and namespace == self.value_namespace and not self.clock:
            self.value = value

    # -- clock substrate ([CXC-SEVEN-SEG-CLOCK]) ---------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        if self.clock:
            reg = getattr(ctx, "register_clocked", None)
            if reg is not None:
                reg(self)

    def on_unmount(self) -> None:
        unreg = getattr(self._ctx, "unregister_clocked", None) \
            if self._ctx is not None else None
        if unreg is not None:
            unreg(self)
        super().on_unmount()

    def on_clock_tick(self, now: float) -> bool:
        """Cadence pulse from the clocked substrate.  Recompute the formatted
        time; damage ONLY this widget when the displayed second changes.
        Returns False otherwise so an idle clock adds no per-frame damage."""
        if not self.clock:
            return False
        epoch = self._time_source()
        second = int(epoch)
        if second == self._last_second:
            return False
        self._last_second = second
        formatted = time.strftime(self.clock_format, time.localtime(epoch))
        if formatted != self._value:
            self._value = formatted
            self.request_redraw()
            return True
        return False

    # -- loading -----------------------------------------------------------

    def _ensure_loaded(self) -> Optional[SvgDocument]:
        if self._doc is not None or self._load_tried or not self._src:
            return self._doc
        self._load_tried = True
        data: Optional[bytes] = None
        assets = getattr(self._ctx, "assets", None) if self._ctx else None
        if assets is not None:
            data = assets.get_bytes(self._src)
        else:
            try:
                from composeio import resolve as _resolve
                data = _resolve.resolve_bytes(self._src)
            except Exception:
                data = None
        if data is not None:
            try:
                self._doc = SvgDocument.from_bytes(data)
                self._segs = find_segments(self._doc)
                self._design = segments_bbox(self._segs)
            except SvgError:
                self._doc = None
        return self._doc

    def _ensure_colon_loaded(self) -> Optional[SvgDocument]:
        if (self._colon_doc is not None or self._colon_load_tried
                or not self._colon_src):
            return self._colon_doc
        self._colon_load_tried = True
        data: Optional[bytes] = None
        assets = getattr(self._ctx, "assets", None) if self._ctx else None
        if assets is not None:
            data = assets.get_bytes(self._colon_src)
        else:
            try:
                from composeio import resolve as _resolve
                data = _resolve.resolve_bytes(self._colon_src)
            except Exception:
                data = None
        if data is not None:
            try:
                self._colon_doc = SvgDocument.from_bytes(data)
                self._colon_shapes = find_colon_shapes(self._colon_doc)
                self._colon_design = colon_bbox(self._colon_doc,
                                                self._colon_shapes)
            except SvgError:
                self._colon_doc = None
        return self._colon_doc

    # -- layout ------------------------------------------------------------

    def _display_value(self) -> str:
        """The string to actually draw, applying a fixed ``digits`` width by
        right-justifying with blank glyphs / truncating to the rightmost."""
        v = self._value
        if self.digits > 0:
            if len(v) > self.digits:
                v = v[-self.digits:]
            elif len(v) < self.digits:
                v = " " * (self.digits - len(v)) + v
        return v

    def _cell_weight(self, char: str) -> float:
        return _PUNCT_WEIGHT.get(char, _DIGIT_WEIGHT)

    def _layout_cells(self, rect: Rect, aspect: float
                      ) -> List[Tuple[str, Rect]]:
        """Place one cell per character across *rect*, digits sized to the SVG
        glyph aspect, punctuation narrower, gaps between, the whole row centered
        and scaled down to fit the width when needed."""
        chars = self._display_value()
        if not chars:
            return []
        digit_h = rect.h
        digit_w = digit_h * aspect
        widths = [self._cell_weight(c) * digit_w for c in chars]
        gap = digit_w * self.spacing
        total = sum(widths) + gap * (len(chars) - 1)
        scale = min(1.0, rect.w / total) if total > 0 else 1.0
        if scale < 1.0:
            widths = [w * scale for w in widths]
            gap *= scale
            digit_h *= scale
            total *= scale
        x = rect.x + (rect.w - total) / 2.0
        y = rect.y + (rect.h - digit_h) / 2.0
        cells: List[Tuple[str, Rect]] = []
        for c, w in zip(chars, widths):
            cells.append((c, Rect(x, y, w, digit_h)))
            x += w + gap
        return cells

    def measure(self, constraints: Constraints) -> Size:
        h = self.disp_height if self.disp_height > 0 else 120.0
        if self.disp_width > 0:
            w = self.disp_width
        else:
            # estimate width from the value + a nominal digit aspect
            aspect = 0.6
            if self._design and self._design[3] > 0:
                aspect = self._design[2] / self._design[3]
            n = len(self._display_value()) or 1
            w = n * h * aspect * (1.0 + self.spacing)
        return Size(min(w, constraints.max_width),
                    min(h, constraints.max_height))

    # -- draw --------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        doc = self._ensure_loaded()
        if doc is None or not self._segs or self._design is None:
            self._draw_placeholder(canvas, rect)
            return
        aspect = (self._design[2] / self._design[3]
                  if self._design[3] > 0 else 0.6)
        for char, cell in self._layout_cells(rect, aspect):
            if char in _PUNCT_WEIGHT:
                self._draw_punct(canvas, char, cell)
            else:
                render_glyph(canvas, doc, self._segs, self._design, char, cell,
                             self._lit, self._dim)

    def _segment_top_cell_y(self, cell: Rect, letter: str) -> Optional[float]:
        """The cell-space y of the TOP of a named segment, using the SAME
        design->cell mapping the digit glyphs use (contain; a digit cell's
        aspect equals the design aspect, so the map is linear and fills the
        cell height exactly).  Shared across the row -> the colon's target
        y-lines coincide with the adjacent digits' segment tops."""
        if self._design is None or self._design[3] <= 0:
            return None
        top = segment_top_y(self._segs, letter)
        if top is None:
            return None
        _dx, dy, _dw, dh = self._design
        return cell.y + (top - dy) / dh * cell.h

    def _aligned_colon_rect(self, cell: Rect) -> Optional[Rect]:
        """Position + scale the colon art so its two dots' BOTTOM edges land on
        the digit's center('g')-top and bottom('d')-top y-lines (David's glass
        note).  A single affine y-map fits the two dot-bottoms to the two target
        lines; x scales uniformly (round dots) and centers.  None if it cannot
        be derived (missing g/d segment or <2 dots) -> caller falls back."""
        if not self._colon_shapes or self._colon_design is None:
            return None
        g_top = self._segment_top_cell_y(cell, "g")
        d_top = self._segment_top_cell_y(cell, "d")
        if g_top is None or d_top is None:
            return None
        boxes = [b for b in (_shape_design_bbox(self._colon_doc, s)
                             for s in self._colon_shapes) if b is not None]
        if len(boxes) < 2:
            return None
        boxes.sort(key=lambda b: b[1] + b[3] / 2.0)      # top dot first
        upper_bottom = boxes[0][1] + boxes[0][3]
        lower_bottom = boxes[-1][1] + boxes[-1][3]
        span = lower_bottom - upper_bottom
        if span <= 0:
            return None
        scale = (d_top - g_top) / span                    # design-y -> cell-y
        cx0, cy0, cw, ch = self._colon_design
        rw = cw * scale
        rh = ch * scale
        ry = (g_top - scale * upper_bottom) + cy0 * scale
        rx = cell.x + (cell.w - rw) / 2.0                 # centered
        return Rect(rx, ry, rw, rh)

    def _draw_punct(self, canvas: Canvas, char: str, cell: Rect) -> None:
        """Draw ':' / '.' in the lit color.  A ':' prefers the colon SVG art
        (recolored like the segments), vertically aligned so its two dot bottoms
        sit on the center('g')-top and bottom('d')-top segment y-lines; absent
        art falls back to two dots placed on those same y-lines."""
        if char == ":":
            self._ensure_colon_loaded()
            if (self._colon_doc is not None and self._colon_shapes
                    and self._colon_design is not None):
                target = self._aligned_colon_rect(cell) or cell
                render_colon(canvas, self._colon_doc, self._colon_shapes,
                             self._colon_design, target, self._lit)
                return
        r, g, b, a = self._lit
        canvas.set_fill_color(r, g, b, a)
        dot = min(cell.w * 0.7, cell.h * 0.09)
        cx = cell.x + cell.w / 2.0
        if char == ":":
            g_top = self._segment_top_cell_y(cell, "g")
            d_top = self._segment_top_cell_y(cell, "d")
            if g_top is not None and d_top is not None:
                # dot BOTTOM edges on the two segment-top y-lines
                for bottom in (g_top, d_top):
                    canvas.fill_circle(cx, bottom - dot / 2.0, dot / 2.0)
            else:
                for frac in (0.34, 0.66):
                    canvas.fill_circle(cx, cell.y + cell.h * frac, dot / 2.0)
        else:  # "."
            cy = cell.y + cell.h * 0.85
            canvas.fill_circle(cx, cy, dot / 2.0)

    def _draw_placeholder(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.set_fill_color(*_PLACEHOLDER_BG)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*_PLACEHOLDER_INK)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*_PLACEHOLDER_INK)
        canvas.set_font("sans-serif", 10, "regular")
        canvas.draw_text(self._src or "no seven-segment art",
                         rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
        canvas.restore()
