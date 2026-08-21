"""
ComposeUX MarkdownViewerWidget — a READ-ONLY, scrolling markdown surface
([CXC-MDV]; the "I read, you edit" enforcement window).

David reviews markdown docs (role defs, the collaboration-and-design
principles, decision files) INSIDE the Intelligence Console instead of popping
them open in an external editor where a stray keystroke could mutate the
source.  This widget is that read surface — and the foundational substrate the
doc-browser popup, the Configuration wake-sequence live-links, and the coming
LTS viewer all consume, so it is built for reuse, not one screen.

READ-ONLY by construction: no caret, no text entry, no write path.  Reading a
``path`` opens the file 'r' and never writes it back; ``content`` is a plain
string.  The only interaction is vertical scroll.

BLOCK-LEVEL markdown over the ONE shared inline surface (reuse-by-reference,
R3 / the narrow waist): this widget mirrors ``chat_thread``'s block model —
prose vs. table — and EXTENDS it with the document block types (headings,
lists, fenced code, blockquotes, horizontal rules).  It re-implements NONE of
the inline or table machinery:

  * inline spans (``**bold**`` / `` `code` ``) tokenize + draw through
    ``markdown_inline`` (``tokenize_inline`` / ``wrap_runs`` / ``run_bounds``
    / ``draw_run_glyphs``);
  * pipe tables parse / measure / render through ``markdown_table``
    (``parse_pipe_table`` / ``measure_table`` / ``draw_table``).

ONE MEASUREMENT PATH ([CXC-R-DRAW-FONT]): every block is wrapped + measured
once in ``_build_layout`` (which needs a canvas for ``measure_text``) and the
SAME visual lines / glyph advances are what ``draw`` paints — layout height and
drawn pixels can never disagree, so scroll clamping is exact.

Damage discipline ([CXC-R-DAMAGE]): a repaint is requested with a bare
``self.request_redraw()`` — never ``mark_dirty`` / ``add_full`` / a rect.

Deliberately NOT CommonMark.  Supported blocks: ATX headings (``#``..``######``),
paragraphs, ``-``/``*``/``+`` bullets, ``N.``/``N)`` ordered items (nested by
leading indent), fenced code (``` ``` ``` / ``~~~``), pipe tables, ``>``
blockquotes, ``---``/``***``/``___`` horizontal rules, and a leading YAML
``---`` front-matter block (skipped as metadata).  Inline is exactly what
``markdown_inline`` supports — ``**bold**`` and `` `code` `` — so a link
``[t](u)`` renders literal, matching the shared surface's deliberate scope
(no italic: the bundled Plex face ships none).  Setext headings are not
supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style
from . import console_theme as ct
from . import markdown_inline as mi
from . import markdown_table as mdt
from . import mermaid_flowchart as mf

RGBA = Tuple[int, ...]

# Wheel-notch -> px step.  Same idiom as the console's other TOP-anchored
# scrolling surfaces (doc_picker, composer monitor): ``offset -= dy * step``,
# clamped to [0, max_scroll].  dy > 0 is wheel-up (toward the top of the doc).
_SCROLL_STEP = 48.0

# Heading grammar + size hierarchy are the ONE shared substrate
# ([DEC-THREAD-MARKDOWN-HEADERS]; reuse-by-reference, R3) — the doc viewer and
# the chat thread detect + size headings through ``markdown_inline`` so a
# ``## Foo`` renders identically in both.  base=HEADING_BASE (the viewer's
# default 14px body) returns the tuned sizes verbatim, so this is a no-op
# refactor of the viewer's prior private copies.
_ATX_RE = mi.ATX_HEADING_RE

# A leading YAML front-matter fence.
_FM_FENCE = "---"

# A horizontal rule: a line of only -, *, or _ (>=3), spaces tolerated.
_HRULE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")

# An ordered-list marker: "N." or "N)" then a space.
_ORDERED_RE = re.compile(r"^(\d+)([.)])\s+(.*)$")


# ---------------------------------------------------------------------------
# Semantic block model (canvas-free — testable without a surface)
# ---------------------------------------------------------------------------

@dataclass
class MdBlock:
    """One parsed semantic block, BEFORE wrapping.

    ``kind`` is one of: ``heading`` | ``para`` | ``bullet`` | ``ordered`` |
    ``code`` | ``table`` | ``quote`` | ``rule``.  Fields are populated per
    kind (``text`` for prose kinds, ``level`` for headings, ``marker`` +
    ``indent`` for list items, ``lines`` for code, ``model`` for a table)."""
    kind: str
    text: str = ""
    level: int = 0                 # heading level 1..6
    marker: str = ""               # list marker glyph/number ("•", "1.")
    indent: int = 0                # list nesting level (0 = flush)
    lines: List[str] = field(default_factory=list)   # code block source lines
    lang: str = ""                 # code fence language tag
    model: Optional[mdt.TableModel] = None


def parse_markdown_blocks(text: str) -> List[MdBlock]:
    """Parse *text* into an ordered list of :class:`MdBlock` — the canvas-free
    block structure (headings, paragraphs, lists, code, tables, quotes,
    rules).  Fence-aware (a pipe table or a ``#`` inside a ``` fence stays
    verbatim) and drops a leading YAML ``---`` front-matter block as
    metadata."""
    src = text.replace("\r", "")
    lines = src.split("\n")
    n = len(lines)
    blocks: List[MdBlock] = []
    i = 0

    # -- leading YAML front-matter: skip everything to the closing fence -----
    if n and lines[0].strip() == _FM_FENCE:
        j = 1
        while j < n and lines[j].strip() != _FM_FENCE:
            j += 1
        if j < n:                              # a real, closed front-matter
            i = j + 1

    def is_blank(s: str) -> bool:
        return s.strip() == ""

    def is_structural(idx: int) -> bool:
        """Does the line at *idx* START a NON-paragraph block (so a paragraph
        run must end before it)?  Table-aware (a header + delimiter pair with
        no blank separator still breaks the paragraph)."""
        s = lines[idx]
        st = s.strip()
        if st == "":
            return True
        if st.startswith(("```", "~~~")):
            return True
        if _ATX_RE.match(s):
            return True
        if _HRULE_RE.match(s):
            return True
        if st.startswith(">"):
            return True
        if _bullet_marker(s) is not None:
            return True
        if _ORDERED_RE.match(s.lstrip()):
            return True
        if idx + 1 < n and mdt.parse_pipe_table([s, lines[idx + 1]]) is not None:
            return True
        return False

    while i < n:
        line = lines[i]
        st = line.strip()

        # blank -> block separator (no explicit block; spacing is layout's job)
        if st == "":
            i += 1
            continue

        # fenced code -------------------------------------------------------
        if st.startswith("```") or st.startswith("~~~"):
            fence = st[:3]
            lang = st[3:].strip()
            body: List[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            i += 1                             # consume the closing fence
            # a ```mermaid fence becomes a DRAWN diagram block (the mermaid-
            # subset renderer), not a code block ([CXC-R-MERMAID]); every other
            # language stays verbatim code.
            kind = "mermaid" if lang.strip().lower() == "mermaid" else "code"
            blocks.append(MdBlock(kind, lines=body, lang=lang))
            continue

        # pipe table: this line + a delimiter row directly beneath (reuse the
        # markdown_table parser as the detector — no second pipe grammar) ----
        if i + 1 < n and mdt.parse_pipe_table([line, lines[i + 1]]) is not None:
            block = [line, lines[i + 1]]
            j = i + 2
            while j < n and lines[j].strip() != "" and "|" in lines[j]:
                block.append(lines[j])
                j += 1
            model = mdt.parse_pipe_table(block)
            if model is not None:
                blocks.append(MdBlock("table", model=model))
                i = j
                continue

        # ATX heading -------------------------------------------------------
        m = _ATX_RE.match(line)
        if m:
            blocks.append(MdBlock("heading", text=m.group(2),
                                  level=len(m.group(1))))
            i += 1
            continue

        # horizontal rule ---------------------------------------------------
        if _HRULE_RE.match(line):
            blocks.append(MdBlock("rule"))
            i += 1
            continue

        # blockquote: gather consecutive '>' lines --------------------------
        if st.startswith(">"):
            q: List[str] = []
            while i < n and lines[i].strip().startswith(">"):
                q.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            blocks.append(MdBlock("quote", text=" ".join(
                s.strip() for s in q if s.strip())))
            continue

        # list item (bullet or ordered), with indented continuation lines ---
        bullet = _bullet_marker(line)
        ordered = _ORDERED_RE.match(line.lstrip())
        if bullet is not None or ordered is not None:
            indent = _leading_spaces(line) // 2
            if bullet is not None:
                marker = "•"
                content = line.lstrip()[2:]
            else:
                marker = ordered.group(1) + ordered.group(2)
                content = ordered.group(3)
            cont: List[str] = [content]
            i += 1
            while i < n and not is_blank(lines[i]) \
                    and _leading_spaces(lines[i]) > _leading_spaces(line) \
                    and _bullet_marker(lines[i]) is None \
                    and _ORDERED_RE.match(lines[i].lstrip()) is None:
                cont.append(lines[i].strip())
                i += 1
            blocks.append(MdBlock(
                "ordered" if ordered is not None else "bullet",
                text=" ".join(cont), marker=marker, indent=indent))
            continue

        # paragraph: gather consecutive plain lines, joined with a space ----
        para: List[str] = [st]
        i += 1
        while i < n and not is_structural(i):
            para.append(lines[i].strip())
            i += 1
        blocks.append(MdBlock("para", text=" ".join(para)))

    return blocks


def _bullet_marker(line: str) -> Optional[str]:
    """The bullet marker of *line* ('- '/'* '/'+ ', leading spaces tolerated),
    or None.  A ``**bold`` opener is NOT a bullet (needs the trailing space)."""
    s = line.lstrip()
    if s[:2] in ("- ", "* ", "+ "):
        return s[:1]
    return None


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


# ---------------------------------------------------------------------------
# Laid-out (wrapped, measured) blocks — the one measurement path
# ---------------------------------------------------------------------------

@dataclass
class _VLine:
    """One wrapped visual line: styled runs + a hanging indent + an optional
    gutter marker (bullet '•' / '1.')."""
    runs: List[mi.InlineRun]
    indent: float = 0.0
    marker: str = ""


@dataclass
class _LBlock:
    """A measured, positioned-relative block ready to draw.  ``y`` is the top
    offset within the content column (content origin = 0); ``height`` its own
    height (excluding the gap that precedes it)."""
    kind: str
    y: float = 0.0
    height: float = 0.0
    # prose-ish (heading / para / bullet / ordered / quote)
    vlines: List[_VLine] = field(default_factory=list)
    size: float = 0.0
    bold_all: bool = False
    quote: bool = False
    heading: bool = False
    # code
    code_lines: List[str] = field(default_factory=list)
    code_size: float = 0.0
    # table
    model: Optional[mdt.TableModel] = None
    table_layout: Optional[mdt.TableLayout] = None
    # mermaid diagram ([CXC-R-MERMAID]) — a drawn flowchart, or (when the
    # subset can't render it) a degrade-to-source panel
    mermaid_layout: Optional["mf.MermaidLayout"] = None
    mermaid_ok: bool = False
    mermaid_type: str = ""


@dataclass
class MdLayout:
    """The full measured layout for a given width + theme: the positioned
    blocks and the total content height."""
    blocks: List[_LBlock]
    content_height: float


class MarkdownViewerWidget(Widget):
    """Read-only, vertically-scrolling markdown viewer ([CXC-MDV])."""

    def __init__(self, widget_id: str, *,
                 content: Optional[str] = None,
                 path: Optional[str] = None,
                 label: str = "",
                 font_size: float = 14.0,
                 pad: float = 20.0,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "markdown_viewer", **kwargs)
        self.label = str(label)
        self.font_size = float(font_size)
        self.pad = float(pad)
        # source ------------------------------------------------------------
        self._text: str = ""
        self._path: Optional[str] = None
        self._rev: int = 0             # bumped on every source change (cache key)
        # scroll (TOP-anchored: 0 = top of the doc) -------------------------
        self._scroll: float = 0.0
        self._max_scroll: float = 0.0
        # laid-out cache: recomputed when (rev, width, theme) changes --------
        self._layout: Optional[MdLayout] = None
        self._layout_key: Optional[Tuple[int, float, str]] = None
        # interactive so the runtime routes wheel events here; focusable but
        # ringless (a read surface, the chat_thread idiom) — never editable.
        self.interactive = True
        self.accepts_focus = True
        self.focus_ring = False
        # [CXC-R-ENGINE-HOVER] the doc/markdown BODY is selectable text -> I-beam,
        # not the click-hand.  NOTE (promise): unlike chat_thread/text_input, this
        # viewer does NOT yet implement the drag-select gesture (scroll-only
        # today).  The I-beam is therefore an affordance PROMISE the follow-on
        # selection feature must honor — flagged, a separate build, not wired here.
        self.text_selectable = True

        if path is not None:
            self.load_path(path)
        if content is not None:
            self.set_content(content)

    # -- content source (settable; re-lays out on change) ---------------------

    @property
    def content(self) -> str:
        """The raw markdown currently displayed."""
        return self._text

    @content.setter
    def content(self, value: Optional[str]) -> None:
        self.set_content(value)

    @property
    def path(self) -> Optional[str]:
        return self._path

    @path.setter
    def path(self, value: Optional[str]) -> None:
        if value is None:
            self.set_content("")
        else:
            self.load_path(value)

    def set_content(self, value: Optional[str]) -> None:
        """Set the raw markdown string; re-layout + redraw."""
        self._text = "" if value is None else str(value)
        self._path = None
        self._invalidate()

    def load_path(self, path: str) -> None:
        """Read a .md file (READ-ONLY, mode 'r') into the viewer; re-layout +
        redraw.  A missing/unreadable file shows an error line, never raises."""
        self._path = str(path)
        try:
            with open(self._path, "r", encoding="utf-8") as fh:  # READ-ONLY
                self._text = fh.read()
        except OSError as exc:
            self._text = f"`{self._path}`\n\ncould not read: {exc}"
        self._invalidate()

    def _invalidate(self) -> None:
        """A source change: drop the layout cache, scroll to the top, request
        a repaint (bare — the ONLY damage call)."""
        self._rev += 1
        self._scroll = 0.0
        self._layout = None
        self._layout_key = None
        self.request_redraw()

    # -- scroll ---------------------------------------------------------------

    def on_scroll(self, dx: float, dy: float) -> None:
        """Wheel scroll (TOP-anchored, the doc_picker/composer idiom): dy > 0
        is wheel-up = toward the top, so the offset shrinks.  Clamped to
        [0, max_scroll] measured at the last draw (draw re-clamps against
        fresh content, so a stale bound can never overshoot)."""
        if not dy:
            return
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll, self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()

    def scroll_to_top(self) -> None:
        self._scroll = 0.0
        self.request_redraw()

    # -- lifecycle / measure --------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)         # adopt design.theme ([CXC-ICV-THEME])

    def measure(self, constraints: Constraints) -> Size:
        # A document viewer fills its pane; vertical overflow scrolls.
        return Size(constraints.max_width, constraints.max_height)

    # -- layout (one measurement path) ----------------------------------------

    def build_layout(self, canvas: Canvas, content_w: float) -> MdLayout:
        """Wrap + measure every block into positioned :class:`_LBlock`s within
        *content_w*.  This is the SINGLE measurement pass: draw() paints the
        exact visual lines / advances produced here, so measured height and
        painted pixels agree by construction."""
        t = ct.CONSOLE.active
        base = self.font_size
        blocks: List[_LBlock] = []
        y = 0.0
        prev_kind = ""
        for blk in parse_markdown_blocks(self._text):
            gap = self._gap_before(blk.kind, prev_kind, base)
            y += gap
            lb = self._measure_block(canvas, blk, content_w, base, t)
            lb.y = y
            y += lb.height
            blocks.append(lb)
            prev_kind = blk.kind
        return MdLayout(blocks=blocks, content_height=y)

    def _gap_before(self, kind: str, prev: str, base: float) -> float:
        if prev == "":
            return 0.0
        if kind == "heading":
            return base * 1.1
        if kind in ("bullet", "ordered") and prev in ("bullet", "ordered"):
            return base * 0.25                 # tight within a list
        if kind == "rule":
            return base * 0.8
        return base * 0.7

    def _measure_block(self, canvas: Canvas, blk: MdBlock, content_w: float,
                       base: float, t) -> _LBlock:
        if blk.kind == "rule":
            return _LBlock("rule", height=max(1.0, base * 0.9))

        if blk.kind == "code":
            csize = base * 0.92
            line_h = csize * 1.42
            pad_y = csize * 0.6
            n = max(1, len(blk.lines))
            return _LBlock("code", height=n * line_h + 2 * pad_y,
                           code_lines=blk.lines, code_size=csize)

        if blk.kind == "mermaid":
            return self._measure_mermaid(canvas, blk, content_w, base)

        if blk.kind == "table":
            layout = mdt.measure_table(canvas, blk.model,
                                       self._table_style(base), content_w)
            return _LBlock("table", height=layout.total_height,
                           model=blk.model, table_layout=layout)

        # prose kinds: heading / para / bullet / ordered / quote
        heading = blk.kind == "heading"
        quote = blk.kind == "quote"
        size = mi.heading_size(blk.level, base) if heading else base
        gutter = 0.0
        text_indent = 0.0
        marker = ""
        if blk.kind in ("bullet", "ordered"):
            mi.set_run_font(canvas, "plain", size)
            gutter = canvas.measure_text((blk.marker or "•") + " ").width
            text_indent = blk.indent * base * 1.4 + gutter
            marker = blk.marker or "•"
        if quote:
            text_indent = base * 0.9           # room for the left rule

        avail = max(1.0, content_w - text_indent)
        runs = mi.tokenize_inline(blk.text)
        wrapped = mi.wrap_runs(canvas, runs, avail, size)
        vlines: List[_VLine] = []
        for k, wl in enumerate(wrapped):
            vlines.append(_VLine(
                runs=wl, indent=text_indent,
                marker=marker if (k == 0 and marker) else ""))
        line_h = size * (1.35 if heading else 1.5)
        return _LBlock(blk.kind, height=len(vlines) * line_h,
                       vlines=vlines, size=size, bold_all=heading,
                       quote=quote, heading=heading)

    def _measure_mermaid(self, canvas: Canvas, blk: MdBlock, content_w: float,
                         base: float) -> _LBlock:
        """Parse + measure a ```mermaid block.  A supported flowchart/swimlane
        lays out via the shared layered-DAG core; anything else degrades to a
        source panel sized like a code block (with a one-line header)."""
        graph = mf.parse_mermaid("\n".join(blk.lines))
        if graph.ok:
            layout = mf.measure_mermaid(canvas, graph, content_w)
            return _LBlock("mermaid", height=layout.height,
                           mermaid_layout=layout, mermaid_ok=True,
                           mermaid_type=graph.diagram_type)
        csize = base * 0.92
        line_h = csize * 1.42
        pad_y = csize * 0.6
        header_h = base * 1.5
        n = max(1, len(blk.lines))
        return _LBlock("mermaid", height=header_h + n * line_h + 2 * pad_y,
                       code_lines=blk.lines, code_size=csize,
                       mermaid_ok=False, mermaid_type=graph.diagram_type)

    def _draw_mermaid(self, canvas: Canvas, lb: _LBlock, x: float, y: float,
                      content_w: float, t) -> None:
        if lb.mermaid_ok and lb.mermaid_layout is not None:
            canvas.set_fill_color(*t.panel)
            canvas.fill_round_rect(x, y, content_w, lb.height, ct.RADIUS_INNER)
            canvas.set_stroke_color(*t.line)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(x + 0.5, y + 0.5, content_w - 1,
                                     lb.height - 1, ct.RADIUS_INNER)
            canvas.save()
            canvas.clip_round_rect(x, y, content_w, lb.height, ct.RADIUS_INNER)
            mf.draw_mermaid(canvas, lb.mermaid_layout, x, y, t)
            canvas.restore()
            return
        self._draw_mermaid_source(canvas, lb, x, y, content_w, t)

    def _draw_mermaid_source(self, canvas: Canvas, lb: _LBlock, x: float,
                             y: float, content_w: float, t) -> None:
        """The graceful degrade: a styled source panel for a mermaid diagram
        type the Canvas subset does not render ([CXC-R-MERMAID])."""
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(x, y, content_w, lb.height, ct.RADIUS_INNER)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, content_w - 1,
                                 lb.height - 1, ct.RADIUS_INNER)
        csize = lb.code_size
        line_h = csize * 1.42
        pad_x = csize * 0.7
        pad_y = csize * 0.6
        header_h = self.font_size * 1.5
        canvas.save()
        canvas.clip_round_rect(x, y, content_w, lb.height, ct.RADIUS_INNER)
        canvas.set_font(style.FONT_MONO, ct.LABEL_MONO, "regular")
        hm = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.accent)
        canvas.draw_text("mermaid", x + pad_x, y + pad_y + hm.ascent, "left")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(f"{lb.mermaid_type} — shown as source",
                         x + content_w - pad_x, y + pad_y + hm.ascent, "right")
        canvas.set_font(style.FONT_MONO, csize, "regular")
        canvas.set_fill_color(*t.dim)
        ly = y + header_h
        for src in (lb.code_lines or [""]):
            am = canvas.measure_text("Ag")
            canvas.draw_text(src, x + pad_x, ly + am.ascent, "left")
            ly += line_h
        canvas.restore()

    def _table_style(self, base: float) -> mdt.TableStyle:
        """A markdown_table style from the ACTIVE console theme (the module
        stays console-free; the widget maps inks/rules)."""
        t = ct.CONSOLE.active
        return mdt.TableStyle(
            font_size=base * 0.95,
            header_ink=t.ink, body_ink=t.ink, code_ink=t.accent,
            rule=t.line2, grid=t.line, sel=t.accent_line)

    def _ensure_layout(self, canvas: Canvas, content_w: float) -> MdLayout:
        key = (self._rev, round(content_w, 1), ct.CONSOLE.active.name)
        if self._layout is None or self._layout_key != key:
            self._layout = self.build_layout(canvas, content_w)
            self._layout_key = key
        return self._layout

    @property
    def content_height(self) -> float:
        """The last measured total content height (0 before the first draw /
        build_layout)."""
        return self._layout.content_height if self._layout else 0.0

    # -- draw -----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()

        # the read column (same card chrome as the thread) ------------------
        ct.draw_card(canvas, rect.x, rect.y, rect.w, rect.h, ct.RADIUS_READ, t)

        pad = self.pad
        content_x = rect.x + pad
        content_w = max(40.0, rect.w - 2 * pad)
        top = rect.y + pad
        avail = max(0.0, rect.h - 2 * pad)

        layout = self._ensure_layout(canvas, content_w)
        content_h = layout.content_height
        self._max_scroll = max(0.0, content_h - avail)
        self._scroll = max(0.0, min(self._max_scroll, self._scroll))

        canvas.save()
        canvas.clip_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_READ)

        origin_y = top - self._scroll
        vp_top, vp_bot = rect.y, rect.y + rect.h
        for lb in layout.blocks:
            by = origin_y + lb.y
            if by + lb.height < vp_top:         # fully above the viewport
                continue
            if by > vp_bot:                      # below — the rest is too
                break
            self._draw_block(canvas, lb, content_x, by, content_w, t)

        canvas.restore()

        self._draw_scrollbar(canvas, rect, pad, avail, content_h, t)

        if not layout.blocks:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_UI, 11, "regular")
            canvas.draw_text("(empty document)",
                             rect.x + rect.w / 2, rect.y + rect.h / 2, "center")
        canvas.restore()

    def _draw_block(self, canvas: Canvas, lb: _LBlock, x: float, y: float,
                    content_w: float, t) -> None:
        if lb.kind == "rule":
            canvas.set_stroke_color(*t.line2)
            canvas.set_stroke_width(1.0)
            ry = y + lb.height / 2
            canvas.draw_line(x, ry, x + content_w, ry)
            return

        if lb.kind == "code":
            self._draw_code(canvas, lb, x, y, content_w, t)
            return

        if lb.kind == "mermaid":
            self._draw_mermaid(canvas, lb, x, y, content_w, t)
            return

        if lb.kind == "table":
            mdt.draw_table(canvas, lb.model, lb.table_layout, x, y,
                           self._table_style(self.font_size))
            return

        self._draw_prose(canvas, lb, x, y, t)

    def _draw_code(self, canvas: Canvas, lb: _LBlock, x: float, y: float,
                   content_w: float, t) -> None:
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(x, y, content_w, lb.height, ct.RADIUS_INNER)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, content_w - 1,
                                 lb.height - 1, ct.RADIUS_INNER)
        csize = lb.code_size
        line_h = csize * 1.42
        pad_x = csize * 0.7
        pad_y = csize * 0.6
        canvas.save()
        canvas.clip_round_rect(x, y, content_w, lb.height, ct.RADIUS_INNER)
        canvas.set_font(style.FONT_MONO, csize, "regular")
        canvas.set_fill_color(*t.dim)
        ly = y + pad_y
        for src in (lb.code_lines or [""]):
            base = ly + canvas.measure_text("Ag").ascent
            canvas.draw_text(src, x + pad_x, base, "left")
            ly += line_h
        canvas.restore()

    def _draw_prose(self, canvas: Canvas, lb: _LBlock, x: float, y: float,
                    t) -> None:
        size = lb.size
        line_h = size * (1.35 if lb.heading else 1.5)
        # ink policy per block kind
        if lb.heading:
            def ink_for(kind: str) -> RGBA:
                return t.accent if kind == "code" else t.ink
        elif lb.quote:
            def ink_for(kind: str) -> RGBA:
                return t.accent if kind == "code" else t.dim
        else:
            def ink_for(kind: str) -> RGBA:
                return t.accent if kind == "code" else t.ink

        # blockquote left rule spanning the block
        if lb.quote:
            canvas.set_fill_color(*t.line2)
            canvas.fill_rect(x, y + 1.0, 2.0, max(1.0, lb.height - 2.0))

        ly = y
        for vl in lb.vlines:
            canvas.set_font(style.FONT_UI, size, "regular")
            base = ly + canvas.measure_text("Ag").ascent
            # gutter marker (bullet / number)
            if vl.marker:
                canvas.set_font(style.FONT_UI, size, "regular")
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(vl.marker, x + vl.indent
                                 - canvas.measure_text(vl.marker + " ").width,
                                 base, "left")
            # heading forces plain/bold runs to the bold face (like a table
            # header); body/quote draw runs in their own face
            if lb.bold_all:
                draw_runs = [mi.InlineRun(r.text,
                                          "bold" if r.kind in ("plain", "bold")
                                          else r.kind) for r in vl.runs]
            else:
                draw_runs = vl.runs
            mi.draw_run_glyphs(canvas, draw_runs, x + vl.indent, base,
                               size, ink_for)
            ly += line_h

    def _draw_scrollbar(self, canvas: Canvas, rect: Rect, pad: float,
                        avail: float, content_h: float, t) -> None:
        """Thin track + thumb on the right inner edge, only when the document
        overflows (the chat_thread scrollbar idiom, TOP-anchored)."""
        if content_h <= avail or avail <= 0.0:
            return
        track_w = 4.0
        tx = rect.x + rect.w - 10.0
        ty = rect.y + pad
        th = rect.h - 2 * pad
        canvas.set_fill_color(*t.line)
        canvas.fill_round_rect(tx, ty, track_w, th, track_w / 2)
        thumb_h = max(24.0, th * (avail / content_h))
        travel = max(0.0, th - thumb_h)
        frac = 0.0 if self._max_scroll <= 0.0 else self._scroll / self._max_scroll
        canvas.set_fill_color(*t.faint)
        canvas.fill_round_rect(tx, ty + travel * frac, track_w, thumb_h,
                               track_w / 2)
