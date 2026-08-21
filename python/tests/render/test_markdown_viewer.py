"""
Tests for the MarkdownViewerWidget ([CXC-MDV]) — the read-only, scrolling
in-console markdown surface.

Covers what static reading can't prove: the block parser (fence-aware,
front-matter-skipping), the ONE measurement path (wrap + measure once, drawn
identically), read-only content/path swapping, scroll clamping, and a real
end-to-end render of the collaboration-and-design-principles doc — plus a
crafted sample that exercises EVERY block type (headings / lists / code /
tables / quote / rule).  All headless; a seeded full render is written to a
PNG for a human glass-check.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import MarkdownViewerWidget
from composeio.render.widgets import markdown_viewer as mdv
from composeio.render.widgets import markdown_inline as mi
from composeio.render.widgets import markdown_table as mdt


PRINCIPLES = os.path.join(REPO_ROOT, "bootstrap",
                          "collaboration-and-design-principles.md")

# A crafted doc exercising every supported block type.
SAMPLE = """---
title: sample
skip: this front-matter is metadata
---
# Heading One

Some **bold** intro prose with `inline_code` that wraps across the column when
the paragraph is long enough to exceed the available content width comfortably.

## Heading Two

- first bullet with **emphasis**
- second bullet
  with a continuation line
- third `code` bullet

1. ordered one
2. ordered two

> a blockquote line that carries a quieter voice

```python
def hello():
    return "world"
```

| Unit | Status | Charge |
|:-----|:------:|-------:|
| gilligan | draining | 42 |
| skipper | idle | 88 |

---

Closing paragraph after a rule.
"""


def _canvas(w=560, h=640):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x07, 0x0A, 0x0F)
    c.fill_rect(0, 0, w, h)
    return c


# ── block parser ───────────────────────────────────────────────────────────────

def test_parse_skips_yaml_front_matter():
    blocks = mdv.parse_markdown_blocks(SAMPLE)
    joined = " ".join(b.text for b in blocks)
    assert "this front-matter is metadata" not in joined
    # the first real block is Heading One, not the front-matter fence
    assert blocks[0].kind == "heading" and blocks[0].text == "Heading One"


def test_parse_identifies_every_block_kind():
    kinds = {b.kind for b in mdv.parse_markdown_blocks(SAMPLE)}
    assert {"heading", "para", "bullet", "ordered", "code", "table",
            "quote", "rule"} <= kinds


def test_parse_heading_levels():
    blocks = mdv.parse_markdown_blocks("# one\n\n## two\n\n### three")
    levels = [(b.text, b.level) for b in blocks if b.kind == "heading"]
    assert levels == [("one", 1), ("two", 2), ("three", 3)]


def test_parse_code_fence_is_verbatim_and_not_a_table():
    # a pipe-table-looking line INSIDE a fence must stay code, not parse as a
    # table (fence-awareness — the narrow-waist detector runs outside fences)
    md = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
    blocks = mdv.parse_markdown_blocks(md)
    assert len(blocks) == 1
    assert blocks[0].kind == "code"
    assert blocks[0].lines[0] == "| a | b |"
    assert all(b.kind != "table" for b in blocks)


def test_parse_table_uses_markdown_table_parser():
    blocks = mdv.parse_markdown_blocks(SAMPLE)
    tables = [b for b in blocks if b.kind == "table"]
    assert len(tables) == 1
    assert isinstance(tables[0].model, mdt.TableModel)
    assert tables[0].model.headers == ["Unit", "Status", "Charge"]


def test_parse_ordered_and_bullet_markers():
    blocks = mdv.parse_markdown_blocks(SAMPLE)
    ordered = [b for b in blocks if b.kind == "ordered"]
    bullets = [b for b in blocks if b.kind == "bullet"]
    assert [b.marker for b in ordered] == ["1.", "2."]
    assert bullets and all(b.marker == "•" for b in bullets)
    # the bullet continuation line was folded into the item text
    assert any("continuation line" in b.text for b in bullets)


def test_parse_paragraph_joins_soft_wrapped_source_lines():
    blocks = mdv.parse_markdown_blocks("alpha beta\ngamma delta\n\nnext")
    paras = [b for b in blocks if b.kind == "para"]
    assert paras[0].text == "alpha beta gamma delta"
    assert paras[1].text == "next"


# ── layout / measurement ───────────────────────────────────────────────────────

def test_layout_produces_nonzero_height_and_all_kinds():
    w = MarkdownViewerWidget("md", content=SAMPLE)
    c = _canvas()
    layout = w.build_layout(c, 500.0)
    assert layout.content_height > 0.0
    kinds = {b.kind for b in layout.blocks}
    assert {"heading", "para", "bullet", "ordered", "code", "table",
            "quote", "rule"} <= kinds
    # every laid-out block has a real height and a monotonic top offset
    ys = [b.y for b in layout.blocks]
    assert ys == sorted(ys)
    assert all(b.height >= 0.0 for b in layout.blocks)


def test_table_block_measured_via_markdown_table():
    w = MarkdownViewerWidget("md", content=SAMPLE)
    c = _canvas()
    layout = w.build_layout(c, 500.0)
    tbl = next(b for b in layout.blocks if b.kind == "table")
    assert isinstance(tbl.table_layout, mdt.TableLayout)
    assert tbl.height == tbl.table_layout.total_height


def test_prose_wraps_within_the_column():
    # a long paragraph must wrap to several visual lines, each within column
    long = "word " * 120
    w = MarkdownViewerWidget("md", content=long.strip())
    c = _canvas()
    max_w = 220.0
    layout = w.build_layout(c, max_w)
    para = next(b for b in layout.blocks if b.kind == "para")
    assert len(para.vlines) > 1
    for vl in para.vlines:
        assert mi.runs_width(c, vl.runs, para.size) + vl.indent <= max_w + 0.5


def test_wrap_runs_shared_helper_is_used():
    # the viewer wraps ACROSS styled runs via the shared markdown_inline
    # substrate (reuse-by-reference) — a mid-line bold + code both survive
    text = ("intro then a **bold emphasized phrase spanning several words** "
            "and an inline `code_span_token_here` trailing off the column end")
    w = MarkdownViewerWidget("md", content=text)
    c = _canvas()
    layout = w.build_layout(c, 200.0)
    para = next(b for b in layout.blocks if b.kind == "para")
    assert len(para.vlines) > 1
    kinds = {r.kind for vl in para.vlines for r in vl.runs}
    assert "bold" in kinds and "code" in kinds
    joined = "".join(r.text for vl in para.vlines for r in vl.runs)
    assert "**" not in joined and "`" not in joined


def test_heading_sizes_form_a_hierarchy():
    w = MarkdownViewerWidget("md", content="# h1\n\n## h2\n\n### h3")
    c = _canvas()
    layout = w.build_layout(c, 500.0)
    sizes = [b.size for b in layout.blocks if b.heading]
    assert sizes[0] > sizes[1] > sizes[2]


# ── read-only content/path swapping ────────────────────────────────────────────

def test_content_property_reswaps_and_relayouts():
    w = MarkdownViewerWidget("md", content="# first")
    c = _canvas()
    w.build_layout(c, 400.0)      # prime
    rev0 = w._rev
    w.content = "# second\n\ndifferent body"
    assert w._rev > rev0                        # source change bumped the rev
    layout = w.build_layout(c, 400.0)
    assert layout.blocks[0].vlines[0].runs[0].text == "second"


def test_path_load_is_read_only_and_does_not_mutate_file():
    before = open(PRINCIPLES, "r", encoding="utf-8").read()
    mtime_before = os.path.getmtime(PRINCIPLES)
    w = MarkdownViewerWidget("md", path=PRINCIPLES)
    assert w.content == before                  # loaded verbatim
    assert w.path == PRINCIPLES
    # laying out + drawing must never touch the source file
    c = _canvas()
    w.draw(c, Rect(0, 0, 560, 640))
    after = open(PRINCIPLES, "r", encoding="utf-8").read()
    assert after == before
    assert os.path.getmtime(PRINCIPLES) == mtime_before


def test_missing_file_shows_error_never_raises():
    w = MarkdownViewerWidget("md", path="/nonexistent/does/not/exist.md")
    assert "could not read" in w.content
    c = _canvas()
    w.draw(c, Rect(0, 0, 560, 640))             # must not raise


# ── scroll ──────────────────────────────────────────────────────────────────────

def test_scroll_clamps_within_content():
    w = MarkdownViewerWidget("md", content=("line\n\n" * 200))
    c = _canvas(400, 300)
    w.draw(c, Rect(0, 0, 400, 300))             # measures max_scroll
    assert w._max_scroll > 0.0
    # wheel-down (dy < 0) scrolls toward the bottom; over-scroll clamps
    w.on_scroll(0.0, -1000.0)
    assert w._scroll <= w._max_scroll
    w.draw(c, Rect(0, 0, 400, 300))
    assert w._scroll <= w._max_scroll
    # wheel-up past the top clamps to 0
    w.on_scroll(0.0, 1000.0)
    assert w._scroll == 0.0


def test_short_doc_has_no_scroll():
    w = MarkdownViewerWidget("md", content="# tiny\n\none line")
    c = _canvas(560, 640)
    w.draw(c, Rect(0, 0, 560, 640))
    assert w._max_scroll == 0.0


# ── real doc + draw does not raise + something was painted ──────────────────────

def test_principles_doc_lays_out_and_paints():
    w = MarkdownViewerWidget("md", path=PRINCIPLES)
    c = _canvas(560, 720)
    layout = w.build_layout(c, 520.0)
    kinds = {b.kind for b in layout.blocks}
    # the principles doc really has headings, lists (ordered), quote, prose
    assert "heading" in kinds
    assert "ordered" in kinds
    assert "para" in kinds
    assert layout.content_height > 0.0
    w.draw(c, Rect(0, 0, 560, 720))
    from .conftest import surface_to_array
    arr = surface_to_array(c)
    assert arr[..., :3].std() > 5.0             # not a flat background


def test_draw_every_block_type_paints_tokens():
    w = MarkdownViewerWidget("md", content=SAMPLE)
    c = _canvas(560, 900)
    drawn = []
    real_dt = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real_dt(s, *a, **k))[1]
    w.draw(c, Rect(0, 0, 560, 900))
    joined = "".join(drawn)
    # heading, code, table cell, list text all painted; markers stripped
    for token in ("Heading One", "def hello", "gilligan", "ordered one"):
        assert token in joined
    assert "•" in drawn                          # bullet marker glyph
    assert "1." in drawn                         # ordered marker
    assert not any("**" in s for s in drawn)     # no literal markdown markers


# ── seeded full render (glass-checkable PNG) ────────────────────────────────────

def test_seeded_markdown_render_png(tmp_path):
    w = MarkdownViewerWidget("md", content=SAMPLE)
    c = _canvas(560, 900)
    w.draw(c, Rect(8, 8, 544, 884))
    out = tmp_path / "markdown_viewer.png"
    c.save_png(str(out))
    assert out.exists() and out.stat().st_size > 3000
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    c.save_png(os.path.join(cap_dir, "markdown_viewer.png"))
