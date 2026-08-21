"""
Shared INLINE-markdown substrate ([CXC-ICV-CHAT]; reuse-by-reference, R3).

This is the ONE inline-markdown surface for the whole console (and the coming
LTS viewer): the tokenizer, the per-run face selection, and the run-drawing
loop that records per-glyph ``measure_text`` advances — extracted verbatim out
of ``chat_thread`` so BOTH the message-body renderer AND ``markdown_table``
cells draw the same ``**bold**`` / `` `code` `` byte-identically, with the same
faces, and — critically — against the SAME per-char advances the selection
bounds are recorded from.  Markdown is now block-level (paragraph vs table)
layered OVER this shared inline layer; a second inline path would be a second
surface and would break selection consistency.

Deliberately NOT CommonMark: exactly ``**bold**``, `` `code` ``, and (in the
body) "- "/"* " bullets — nothing else.  No italic: the bundled IBM Plex face
ships no italic.  Malformed / unclosed markers render LITERAL — the tokenizer
only opens a styled run when it finds the matching closer, so a lone ``**`` or
`` ` `` can never crash or eat the rest of the text.

The unifying discipline ([CXC-R-DRAW-FONT]): a run's glyph advances are
measured with the SAME ``set_font`` the glyphs draw with — one measurement
path — so wrap, hit-test, highlight and copy can never disagree.  Ink is a
POLICY the host injects (``ink_for(kind) -> RGBA``): the console maps it to its
theme tokens, a table row maps it to its cell inks — the drawing loop itself is
theme-free and shared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Tuple

RGBA = Tuple[int, ...]
InkResolver = Callable[[str], RGBA]

# Canvas-contract font family aliases (identical to widgets.style.FONT_*):
# these are the contract's family names, the same bytes on every backend.
FAMILY_UI = "sans-serif"        # -> bundled IBM Plex Sans
FAMILY_MONO = "monospace"       # -> bundled IBM Plex Mono


@dataclass
class InlineRun:
    """A styled inline run of text.

    ``kind`` selects the face at draw: ``plain`` = Plex Sans / ink, ``bold`` =
    Plex Sans bold / ink, ``code`` = Plex Mono / accent.  ``path`` (a file
    path) + ``imark`` (a quiet marker) are the image-affordance kinds — Plex
    Mono, differing only in ink.  Runs are the atom the renderers tokenize to,
    wrap across, and draw."""
    text: str
    kind: str = "plain"     # plain | bold | code | path | imark


# ---------------------------------------------------------------------------
# Shared ATX-heading substrate ([DEC-THREAD-MARKDOWN-HEADERS]; reuse-by-
# reference, R3).  ONE heading parser + size hierarchy for every markdown
# surface: the block-level ``markdown_viewer`` (the doc reader) AND the
# ``chat_thread`` message body both detect ``#``..``######`` headings HERE, so
# a ``## Foo`` renders with the same sizing/weight in a thread bubble as in the
# doc viewer — never a second, drifting heading grammar.  Deliberately ATX
# only (no setext), matching the viewer's documented scope.
# ---------------------------------------------------------------------------

# ATX heading: 1-6 leading '#', a space, then the title (trailing '#'s dropped).
ATX_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# Heading size hierarchy (px) for h1..h6, tuned for a HEADING_BASE-px body;
# ``heading_size`` scales them proportionally for any body size so a smaller-
# body surface (the 12px thread) keeps the SAME visual hierarchy as the 14px
# doc viewer.  Weight is bold at every level; size carries the hierarchy.
HEADING_SIZES = (22.0, 18.0, 16.0, 14.5, 13.5, 13.0)
HEADING_BASE = 14.0


def parse_atx_heading(text: str) -> Optional[Tuple[int, str]]:
    """``(level, title)`` for an ATX heading line (``# ``..``###### ``), else
    None.  The ONE heading detector shared by every markdown surface."""
    m = ATX_HEADING_RE.match(text)
    if m is None:
        return None
    return len(m.group(1)), m.group(2)


def heading_size(level: int, base_size: float = HEADING_BASE) -> float:
    """The px size for a heading *level* (1..6) at a given body *base_size*,
    scaled from :data:`HEADING_SIZES` (anchored at :data:`HEADING_BASE`) so the
    same hierarchy holds at any body size.  ``base_size == HEADING_BASE``
    returns the tuned sizes verbatim (the doc viewer's byte-identical path)."""
    lv = min(max(int(level), 1), 6)
    return HEADING_SIZES[lv - 1] * (float(base_size) / HEADING_BASE)


def tokenize_inline(text: str) -> List[InlineRun]:
    """Split one line of text into styled runs.

    ``**x**`` -> a bold run, `` `x` `` -> a code run (markers dropped),
    everything else -> plain.  A single ``*`` is left literal (no italic).  An
    opener with no closer stays literal.  ``code`` and ``bold`` are peers (not
    nested): a code span's inner text is verbatim."""
    runs: List[InlineRun] = []
    buf: List[str] = []
    i, n = 0, len(text)

    def flush() -> None:
        if buf:
            runs.append(InlineRun("".join(buf), "plain"))
            buf.clear()

    while i < n:
        ch = text[i]
        if ch == "`":
            j = text.find("`", i + 1)
            if j == -1:                       # unclosed -> literal
                buf.append(ch)
                i += 1
            else:
                inner = text[i + 1:j]
                flush()
                if inner:
                    runs.append(InlineRun(inner, "code"))
                i = j + 1
        elif ch == "*" and i + 1 < n and text[i + 1] == "*":
            j = text.find("**", i + 2)
            if j == -1:                       # unclosed -> literal "**"
                buf.append("*")
                buf.append("*")
                i += 2
            else:
                inner = text[i + 2:j]
                flush()
                if inner:
                    runs.append(InlineRun(inner, "bold"))
                i = j + 2
        else:
            buf.append(ch)
            i += 1
    flush()
    return runs


def set_run_font(canvas: Any, kind: str, base_size: float) -> None:
    """Select the face/weight for a run kind (color is set by the drawer).
    Code / path / imark are Plex Mono; bold is Plex Sans bold; plain is Plex
    Sans.  The image-affordance kinds share the mono face — their INK differs,
    set by the caller's ``ink_for``."""
    if kind in ("code", "path", "imark"):
        canvas.set_font(FAMILY_MONO, base_size, "regular")
    elif kind == "bold":
        canvas.set_font(FAMILY_UI, base_size, "bold")
    else:
        canvas.set_font(FAMILY_UI, base_size, "regular")


def runs_width(canvas: Any, runs: List[InlineRun], base_size: float) -> float:
    """Total drawn width of a run sequence on one line (each run measured in
    its own face — one measurement path)."""
    w = 0.0
    for r in runs:
        set_run_font(canvas, r.kind, base_size)
        w += canvas.measure_text(r.text).width
    return w


def run_bounds(canvas: Any, runs: List[InlineRun], x0: float,
               base_size: float) -> Tuple[str, List[float]]:
    """The plain text of a run sequence plus the ABSOLUTE x of every char
    boundary (len(text)+1 entries), starting at *x0*, measured on the SAME
    advances ``draw_run_glyphs`` draws with.  This is the selection substrate:
    hit-test / highlight / copy all index into these bounds."""
    text_parts: List[str] = []
    bounds: List[float] = [x0]
    run_x = x0
    for r in runs:
        set_run_font(canvas, r.kind, base_size)
        for k in range(1, len(r.text) + 1):
            bounds.append(run_x + canvas.measure_text(r.text[:k]).width)
        run_x = bounds[-1]
        text_parts.append(r.text)
    return "".join(text_parts), bounds


def draw_run_glyphs(canvas: Any, runs: List[InlineRun], x0: float,
                    baseline: float, base_size: float,
                    ink_for: InkResolver) -> float:
    """Draw each run left-to-right from *x0* at *baseline*, its face from
    ``set_run_font`` and its ink from ``ink_for(kind)``.  Returns the ending x.
    Advances are re-measured in the SAME face — identical to ``run_bounds`` —
    so a highlight band drawn from ``run_bounds`` lands exactly under the
    glyphs."""
    x = x0
    for r in runs:
        set_run_font(canvas, r.kind, base_size)
        canvas.set_fill_color(*ink_for(r.kind))
        canvas.draw_text(r.text, x, baseline, "left")
        x += canvas.measure_text(r.text).width
    return x


def ellipsize_runs(canvas: Any, runs: List[InlineRun], max_w: float,
                   base_size: float) -> List[InlineRun]:
    """Truncate a run sequence to *max_w* with a trailing ``…`` (a plain run),
    preserving the styling of the surviving prefix — the styled analog of
    ``console_theme.ellipsize``.  Used for table cells that overflow their
    column; the truncation is measured per-run in its own face (one path)."""
    if runs_width(canvas, runs, base_size) <= max_w:
        return runs
    set_run_font(canvas, "plain", base_size)
    ell_w = canvas.measure_text("…").width
    budget = max_w - ell_w
    if budget <= 0:
        return [InlineRun("…", "plain")]
    out: List[InlineRun] = []
    used = 0.0
    for r in runs:
        set_run_font(canvas, r.kind, base_size)
        w = canvas.measure_text(r.text).width
        if used + w <= budget:
            out.append(r)
            used += w
            continue
        # hard-break this run char-by-char to fill the remaining budget
        cur = ""
        for ch in r.text:
            if used + canvas.measure_text(cur + ch).width > budget:
                break
            cur += ch
        if cur:
            out.append(InlineRun(cur, r.kind))
        break
    out.append(InlineRun("…", "plain"))
    return out


# ---------------------------------------------------------------------------
# Run-aware word wrap — the shared substrate for any block-level markdown
# surface that wraps prose ACROSS styled runs (the markdown_viewer / the
# coming LTS viewer).  This is the module-level, host-agnostic extraction of
# the greedy wrap-across-runs algorithm (reuse-by-reference, R3): a mid-line
# ``**bold**`` / `` `code` `` span breaks at the column exactly like plain
# prose, each atom measured in its OWN face — the SAME advances the drawer
# (``run_bounds`` / ``draw_run_glyphs``) uses, so wrap, hit-test, highlight
# and copy can never disagree (one measurement path).
# ---------------------------------------------------------------------------

def wrap_runs(canvas: Any, runs: List[InlineRun], max_w: float,
              base_size: float) -> List[List[InlineRun]]:
    """Greedy word-wrap a styled run sequence to *max_w*.

    Each run is split into word + space atoms (measured in its own font),
    packed onto visual lines up to *max_w*.  A single word wider than the
    column hard-breaks by character in its own style.  Returns a list of
    visual lines (each a list of merged :class:`InlineRun`); always at least
    one (possibly empty) line so a caller can index ``[0]`` safely.
    """
    atoms: List[Tuple[str, str, bool]] = []
    for r in runs:
        words = r.text.split(" ")
        for wi, wd in enumerate(words):
            if wi:
                atoms.append((" ", r.kind, True))
            if wd:
                atoms.append((wd, r.kind, False))

    def measure(txt: str, kind: str) -> float:
        set_run_font(canvas, kind, base_size)
        return canvas.measure_text(txt).width

    lines: List[List[List[Any]]] = []
    cur: List[List[Any]] = []          # [text, kind, width]
    cur_w = 0.0
    for txt, kind, is_space in atoms:
        w = measure(txt, kind)
        if is_space:
            if cur:                    # never a leading space
                cur.append([txt, kind, w])
                cur_w += w
            continue
        if cur and cur_w + w > max_w:
            while cur and cur[-1][0] == " ":
                cur_w -= cur[-1][2]
                cur.pop()
            lines.append(cur)
            cur, cur_w = [], 0.0
        if not cur and w > max_w:
            pieces = _hard_break_run(canvas, txt, kind, max_w, base_size)
            for ptext, pw in pieces[:-1]:
                lines.append([[ptext, kind, pw]])
            ltext, lw = pieces[-1]
            cur, cur_w = [[ltext, kind, lw]], lw
        else:
            cur.append([txt, kind, w])
            cur_w += w
    if cur:
        while cur and cur[-1][0] == " ":
            cur.pop()
        lines.append(cur)
    if not lines:
        lines.append([])
    return [_merge_run_pieces(pl) for pl in lines]


def _hard_break_run(canvas: Any, token: str, kind: str, max_w: float,
                    base_size: float) -> List[Tuple[str, float]]:
    """Break a single over-wide word by characters, in its own style."""
    set_run_font(canvas, kind, base_size)
    pieces: List[Tuple[str, float]] = []
    cur = ""
    for ch in token:
        trial = cur + ch
        if cur and canvas.measure_text(trial).width > max_w:
            pieces.append((cur, canvas.measure_text(cur).width))
            cur = ch
        else:
            cur = trial
    if cur:
        pieces.append((cur, canvas.measure_text(cur).width))
    return pieces or [(token, canvas.measure_text(token).width)]


def _merge_run_pieces(pieces: List[List[Any]]) -> List[InlineRun]:
    """Coalesce adjacent same-kind atoms of one visual line into runs."""
    out: List[InlineRun] = []
    for txt, kind, _w in pieces:
        if out and out[-1].kind == kind:
            out[-1].text += txt
        else:
            out.append(InlineRun(txt, kind))
    return out
