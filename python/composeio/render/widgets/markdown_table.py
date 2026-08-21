"""
Reusable GitHub pipe-table parse / layout / render substrate
([CXC-ICV-CHAT] tables; built for reuse by the LTS-viewer app).

This is a HOST-AGNOSTIC block-level component in the
``multiline_text.MultiLineEditMixin`` tradition (R3, reuse-by-reference): it
knows nothing about ``chat_thread`` or the console — it turns a GitHub
pipe-table block into a structure, measures column widths with ``measure_text``,
and renders header + rule + body cells into a caller-supplied ``(canvas, x, y)``
against the Canvas contract ONLY.  Any widget can compose it — the
ChatThreadWidget today, the LTS viewer later.

Cells are NOT a second markdown path: cell content is tokenized + drawn through
the SHARED ``markdown_inline`` layer, so a cell's ``**bold**`` / `` `code` ``
looks byte-identical to body markdown and — critically — records selection
bounds against the SAME per-glyph advances.  Markdown here is purely the
BLOCK-level extension (paragraph vs table) over that one inline surface.

Three seams:

  * ``segment_tables(text)`` / ``parse_pipe_table(lines)`` — DETECT + PARSE a
    pipe table (``| a | b |`` rows + a ``|---|:--:|`` alignment/rule row) to a
    :class:`TableModel`.  Robust to leading/trailing pipes and ragged rows.
    ``segment_tables`` splits a whole message body into an ordered list of
    ``("text", str)`` / ``("table", TableModel)`` blocks so the non-table prose
    renders however the host wants.

  * ``measure_table(canvas, model, style, max_width)`` -> :class:`TableLayout`
    — column widths = max cell inline-run width (header measured bold), capped
    to *max_width* (shrink-to-fit + per-cell ELLIPSIZE on overflow), plus the
    header/row heights.  One measurement path.

  * ``draw_table(canvas, model, layout, x, y, style, record=None)`` — paints a
    bold header row, a thin header rule, and body cells (each a shared
    inline-run line) with per-column alignment + light row/column separators.
    ``record`` (optional) is the SELECTION seam: called once per cell in
    row-major order with the cell's plain text + ABSOLUTE per-char x
    boundaries, returning an optional selected ``(start, end)`` span which is
    painted as a highlight band behind that cell's glyphs.

Desktop / Skia tier only.  The embedded (CYD) tier has a standing "no tables"
design note (16-line band renderer, no wrap/measure budget) and is deliberately
out of scope — a table block degrades to its source text on that tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Tuple

from . import markdown_inline as mi

RGBA = Tuple[int, ...]

# A cell recorder: (cell_text, char_bounds, y_top, y_bot, is_row_start) ->
# optional selected (start, end) char span for that cell.  char_bounds has
# len(cell_text)+1 entries: the absolute surface x of every char boundary.
CellRecorder = Callable[[str, List[float], float, float, bool],
                        Optional[Tuple[int, int]]]


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class TableModel:
    """A parsed GitHub pipe table.

    ``headers`` is the header cell texts; ``rows`` the body rows (each a list
    of cell texts); ``aligns`` the per-column alignment ('left'|'center'|
    'right'), one entry per header column.  Ragged rows are normalized to the
    header column count at parse time.  Cell strings keep their inline markdown
    (``**``/`` ` ``) verbatim — it is tokenized at draw time."""
    headers: List[str]
    rows: List[List[str]]
    aligns: List[str] = field(default_factory=list)

    @property
    def ncols(self) -> int:
        return len(self.headers)

    def to_source(self) -> str:
        """Reconstruct a canonical pipe-table source string (round-trips the
        parse; a whole-table copy / export format)."""
        def row(cells: List[str]) -> str:
            return "| " + " | ".join(cells) + " |"
        marks = []
        for a in self.aligns:
            if a == "center":
                marks.append(":---:")
            elif a == "right":
                marks.append("---:")
            elif a == "left":
                marks.append(":---")
            else:
                marks.append("---")
        out = [row(self.headers), "| " + " | ".join(marks) + " |"]
        out += [row(r) for r in self.rows]
        return "\n".join(out)


@dataclass
class TableStyle:
    """Pure visual tokens for a rendered table — NO console_theme import, so
    the LTS viewer supplies its own.  ``chat_thread`` builds one per draw from
    the active console theme (see ``ChatThreadWidget._table_style``).  Inks are
    resolved per run-kind: plain/bold -> header_ink or body_ink, code ->
    code_ink."""
    font_size: float = 12.0
    header_ink: RGBA = (0xE7, 0xEC, 0xF3, 255)
    body_ink: RGBA = (0xE7, 0xEC, 0xF3, 255)
    code_ink: RGBA = (0x4E, 0xA8, 0xFF, 255)
    rule: RGBA = (0x26, 0x31, 0x3F, 255)      # header underline (stronger)
    grid: RGBA = (0x1B, 0x24, 0x32, 255)      # light row/column separators
    sel: RGBA = (78, 168, 255, 107)           # selection highlight band
    cell_pad_x: float = 8.0
    cell_pad_y: float = 4.0
    line_scale: float = 1.4                    # text line height / font size

    def ink_for(self, is_header: bool) -> mi.InkResolver:
        base = self.header_ink if is_header else self.body_ink

        def resolve(kind: str) -> RGBA:
            return self.code_ink if kind == "code" else base
        return resolve


@dataclass
class TableLayout:
    """The measured geometry of a table for a given style + max width.

    ``col_widths`` are OUTER cell widths (inner text width + 2*cell_pad_x).
    ``cell_runs`` is the possibly-ellipsized styled runs actually drawn — row 0
    the header, 1.. the body — so the draw pass never re-decides truncation or
    re-tokenizes (measure and draw agree by construction)."""
    col_widths: List[float]
    header_h: float
    row_h: float
    total_width: float
    total_height: float
    cell_runs: List[List[List[mi.InlineRun]]]


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# A delimiter cell: optional leading/trailing colon around one-or-more dashes.
_DELIM_CELL = re.compile(r"^\s*:?-{1,}:?\s*$")


def _split_cells(line: str) -> List[str]:
    """Split one pipe-table row into cell texts, tolerating one optional
    leading and one optional trailing pipe.  Interior empty cells are kept
    (so ``| a || b |`` -> ['a', '', 'b'])."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_delimiter_row(line: str) -> bool:
    """True when *line* is a table alignment/rule row (every cell is dashes
    with optional alignment colons) AND it carries at least one pipe — the
    pipe requirement stops a bare ``---`` thematic break from starting a
    table."""
    if "|" not in line:
        return False
    cells = _split_cells(line)
    if not cells:
        return False
    return all(_DELIM_CELL.match(c) for c in cells)


def _align_of(cell: str) -> str:
    c = cell.strip()
    left = c.startswith(":")
    right = c.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left"


def parse_pipe_table(lines: List[str]) -> Optional[TableModel]:
    """Parse a block of lines as a GitHub pipe table, or return None.

    Requires a header line, a delimiter row directly beneath it, and zero or
    more body rows.  Ragged rows are padded / truncated to the header column
    count; alignment comes from the delimiter row (default left)."""
    if len(lines) < 2:
        return None
    if not _is_delimiter_row(lines[1]):
        return None
    header = _split_cells(lines[0])
    if not header:
        return None
    delim = _split_cells(lines[1])
    ncols = len(header)
    aligns = [_align_of(delim[c]) if c < len(delim) else "left"
              for c in range(ncols)]

    def norm(cells: List[str]) -> List[str]:
        if len(cells) < ncols:
            cells = cells + [""] * (ncols - len(cells))
        elif len(cells) > ncols:
            cells = cells[:ncols]
        return cells

    rows = [norm(_split_cells(ln)) for ln in lines[2:]]
    return TableModel(headers=header, rows=rows, aligns=aligns)


def segment_tables(text: str) -> List[Tuple[str, Any]]:
    """Split a message body into ordered blocks: ``("text", str)`` for prose
    runs and ``("table", TableModel)`` for pipe tables.

    A table starts where a non-blank line is directly followed by a delimiter
    row; it runs until the first blank line or a line without a pipe.  Prose
    between tables is returned intact (newlines preserved) so the host's own
    paragraph / wrap logic renders it unchanged.  No table -> a single
    ``[("text", text)]`` block (the caller's non-table path is untouched)."""
    src = text.replace("\r", "")
    lines = src.split("\n")
    blocks: List[Tuple[str, Any]] = []
    buf: List[str] = []
    i, n = 0, len(lines)

    def flush_text() -> None:
        if buf:
            blocks.append(("text", "\n".join(buf)))
            buf.clear()

    while i < n:
        line = lines[i]
        starts = (line.strip() != "" and i + 1 < n
                  and _is_delimiter_row(lines[i + 1]))
        if not starts:
            buf.append(line)
            i += 1
            continue
        block = [lines[i], lines[i + 1]]
        j = i + 2
        while j < n and lines[j].strip() != "" and "|" in lines[j]:
            block.append(lines[j])
            j += 1
        model = parse_pipe_table(block)
        if model is None:                       # defensive: treat as text
            buf.append(line)
            i += 1
            continue
        flush_text()
        blocks.append(("table", model))
        i = j
    flush_text()
    return blocks or [("text", text)]


def has_table(text: str) -> bool:
    """Fast predicate: does *text* contain at least one pipe-table block?"""
    return any(k == "table" for k, _ in segment_tables(text))


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def measure_table(canvas: Any, model: TableModel, style: TableStyle,
                  max_width: float) -> TableLayout:
    """Measure column widths + row heights for *model* within *max_width*.

    Natural column width = max inline-run cell width (header tokenized + drawn
    bold), + 2*cell_pad_x.  If the natural total exceeds *max_width* the
    columns shrink proportionally (floored at an ``…``-plus-padding minimum)
    and each cell's runs are ellipsized to its column — all measured on the
    SAME faces the draw pass uses via ``markdown_inline``, so no second
    measurement can disagree."""
    ncols = model.ncols
    pad = style.cell_pad_x
    size = style.font_size
    if ncols == 0:
        return TableLayout([], 0.0, 0.0, 0.0, 0.0, [])

    all_rows: List[List[str]] = [model.headers] + model.rows
    # tokenize every cell once (header bold is a face choice at draw, not a
    # token choice — the same runs, drawn bold for the header row)
    tok: List[List[List[mi.InlineRun]]] = [
        [mi.tokenize_inline(cell) for cell in row] for row in all_rows]

    nat = [0.0] * ncols
    for r_i, row in enumerate(tok):
        for c in range(ncols):
            runs = row[c] if c < len(row) else []
            w = _runs_width_as(canvas, runs, size, header=(r_i == 0))
            if w > nat[c]:
                nat[c] = w
    outer = [w + 2 * pad for w in nat]
    total = sum(outer)

    if total <= max_width or total <= 0:
        col_w = outer
    else:
        mi.set_run_font(canvas, "plain", size)
        floor = canvas.measure_text("…").width + 2 * pad
        scale = max_width / total
        col_w = [max(floor, w * scale) for w in outer]

    cell_runs: List[List[List[mi.InlineRun]]] = []
    for r_i, row in enumerate(tok):
        out_row = []
        for c in range(ncols):
            runs = row[c] if c < len(row) else []
            inner = col_w[c] - 2 * pad
            out_row.append(_ellipsize_as(canvas, runs, inner, size,
                                         header=(r_i == 0)))
        cell_runs.append(out_row)

    line_h = size * style.line_scale
    row_h = line_h + 2 * style.cell_pad_y
    header_h = row_h
    total_h = header_h + row_h * len(model.rows)
    return TableLayout(col_widths=col_w, header_h=header_h, row_h=row_h,
                       total_width=sum(col_w), total_height=total_h,
                       cell_runs=cell_runs)


def _runs_width_as(canvas: Any, runs: List[mi.InlineRun], size: float,
                   header: bool) -> float:
    """Width of a cell's runs, forcing bold for a header cell's plain/bold runs
    (the header row is drawn bold)."""
    if header:
        w = 0.0
        for r in runs:
            kind = "bold" if r.kind in ("plain", "bold") else r.kind
            mi.set_run_font(canvas, kind, size)
            w += canvas.measure_text(r.text).width
        return w
    return mi.runs_width(canvas, runs, size)


def _ellipsize_as(canvas: Any, runs: List[mi.InlineRun], max_w: float,
                  size: float, header: bool) -> List[mi.InlineRun]:
    """Ellipsize a cell's runs to *max_w*, measuring header runs bold."""
    if _runs_width_as(canvas, runs, size, header) <= max_w:
        return runs
    view = [mi.InlineRun(r.text, "bold" if r.kind in ("plain", "bold")
                         else r.kind) for r in runs] if header else runs
    trimmed = mi.ellipsize_runs(canvas, view, max_w, size)
    if not header:
        return trimmed
    # map the bold view back to the original kinds (draw restores bold anyway)
    out = []
    for r in trimmed:
        out.append(mi.InlineRun(r.text,
                                "plain" if r.kind == "bold" else r.kind))
    return out


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def draw_table(canvas: Any, model: TableModel, layout: TableLayout,
               x: float, y: float, style: TableStyle,
               record: Optional[CellRecorder] = None) -> None:
    """Paint the table at (x, y): bold header, thin header rule, aligned body
    cells (shared inline runs), light row/column separators.

    ``record`` (the selection seam) is called once per cell in row-major order
    (header cells first) with ``(cell_text, char_bounds, y_top, y_bot,
    is_row_start)`` — ``char_bounds[k]`` the ABSOLUTE surface x of the k-th
    char boundary, measured on the SAME advances the glyphs draw with — and may
    return a selected ``(start, end)`` char span, painted as a highlight band
    behind that cell's glyphs.  ``record=None`` = a plain, non-interactive
    table (the LTS viewer's read-only default)."""
    ncols = model.ncols
    if ncols == 0:
        return
    pad_x = style.cell_pad_x
    size = style.font_size
    col_w = layout.col_widths
    xs = [x]
    for w in col_w:
        xs.append(xs[-1] + w)
    total_w = xs[-1] - x
    aligns = model.aligns

    ry = y
    for r_i, row in enumerate(layout.cell_runs):
        is_header = (r_i == 0)
        rh = layout.header_h if is_header else layout.row_h
        ink_for = style.ink_for(is_header)
        # header runs draw bold: force plain/bold runs to the bold face
        def face(kind: str) -> str:
            return "bold" if (is_header and kind in ("plain", "bold")) \
                else kind
        mi.set_run_font(canvas, "bold" if is_header else "plain", size)
        baseline = ry + style.cell_pad_y + canvas.measure_text("Ag").ascent
        for c in range(ncols):
            runs = row[c] if c < len(row) else []
            draw_runs = [mi.InlineRun(r.text, face(r.kind)) for r in runs]
            cell_x = xs[c]
            colw = col_w[c]
            align = aligns[c] if c < len(aligns) else "left"
            tw = mi.runs_width(canvas, draw_runs, size)
            if align == "right":
                tx0 = cell_x + colw - pad_x - tw
            elif align == "center":
                tx0 = cell_x + (colw - tw) / 2.0
            else:
                tx0 = cell_x + pad_x
            cell_text, bounds = mi.run_bounds(canvas, draw_runs, tx0, size)
            # selection seam (before glyphs so the band sits behind them)
            span = record(cell_text, bounds, ry, ry + rh, c == 0) \
                if record is not None else None
            if span is not None and cell_text:
                a = max(0, span[0])
                b = min(len(cell_text), span[1])
                if a < b:
                    canvas.set_fill_color(*style.sel)
                    canvas.fill_rect(bounds[a], ry + 1.0,
                                     bounds[b] - bounds[a],
                                     max(1.0, rh - 2.0))
            mi.draw_run_glyphs(canvas, draw_runs, tx0, baseline, size, ink_for)
        ry += rh

    # header rule (stronger) + light row separators
    canvas.set_stroke_width(1.0)
    canvas.set_stroke_color(*style.rule)
    hy = y + layout.header_h
    canvas.draw_line(x, hy, x + total_w, hy)
    canvas.set_stroke_color(*style.grid)
    sy = hy
    for _r in range(len(model.rows) - 1):
        sy += layout.row_h
        canvas.draw_line(x, sy, x + total_w, sy)
    # light column separators (in the padding gutters, never across glyphs)
    for c in range(1, ncols):
        cx = xs[c]
        canvas.draw_line(cx, y, cx, y + layout.total_height)
