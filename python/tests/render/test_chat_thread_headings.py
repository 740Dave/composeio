"""
Thread ATX-heading rendering ([DEC-THREAD-MARKDOWN-HEADERS]).

A ``## Foo`` inside a thread MESSAGE renders as a bold, sized heading — using
the SAME heading grammar + size hierarchy the doc viewer uses
(``markdown_inline.parse_atx_heading`` / ``heading_size``), never a forked
second parser.  These tests prove: the thread's heading path is the shared
substrate; headings render bold at the scaled size; non-heading prose is
unchanged; and a real bubble paints a heading (headless PNG).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import ChatThreadWidget
from composeio.render.widgets import style as _style
from composeio.render.widgets import markdown_inline as mi


def _canvas(w=456, h=356):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x16)
    c.fill_rect(0, 0, w, h)
    return c


# -- shared parser (reuse-by-reference, R3) -----------------------------------

def test_thread_uses_shared_heading_parser():
    # the SAME detector the doc viewer imports resolves level + title
    assert mi.parse_atx_heading("## Deploy plan") == (2, "Deploy plan")
    assert mi.parse_atx_heading("# Title") == (1, "Title")
    assert mi.parse_atx_heading("###### deep") == (6, "deep")
    # not a heading: no space after the hashes, or no hashes
    assert mi.parse_atx_heading("#nospace") is None
    assert mi.parse_atx_heading("just prose") is None


def test_viewer_and_thread_share_the_size_hierarchy():
    # base == HEADING_BASE returns the tuned sizes verbatim (the viewer's path)
    assert mi.heading_size(1, mi.HEADING_BASE) == mi.HEADING_SIZES[0]
    assert mi.heading_size(3, mi.HEADING_BASE) == mi.HEADING_SIZES[2]
    # a smaller body keeps the hierarchy (thread body 12 < viewer 14)
    assert mi.heading_size(1, 12.0) > mi.heading_size(2, 12.0) \
        > mi.heading_size(3, 12.0)
    assert mi.heading_size(1, 12.0) < mi.heading_size(1, 14.0)


# -- heading layout in the message body ---------------------------------------

def test_heading_line_becomes_a_sized_bold_heading_vline():
    w = ChatThreadWidget("t")
    c = _canvas()
    vlines = w._layout_body(c, "## Deploy plan", 400.0, 12.0)
    assert len(vlines) == 1
    vl = vlines[0]
    assert vl.heading is True
    assert vl.size == mi.heading_size(2, 12.0)
    assert vl.para_start is True
    # the "## " marker is stripped; the title renders in the BOLD face
    assert "".join(r.text for r in vl.runs) == "Deploy plan"
    assert all(r.kind == "bold" for r in vl.runs)
    # a heading line carries no literal hashes
    assert not any("#" in r.text for r in vl.runs)


def test_heading_levels_scale_down_by_level():
    w = ChatThreadWidget("t")
    c = _canvas()
    body = "# h1\n\n## h2\n\n### h3"
    vlines = [v for v in w._layout_body(c, body, 400.0, 12.0) if v.heading]
    sizes = [v.size for v in vlines]
    assert sizes == [mi.heading_size(1, 12.0), mi.heading_size(2, 12.0),
                     mi.heading_size(3, 12.0)]
    assert sizes[0] > sizes[1] > sizes[2]


def test_heading_line_pitch_is_taller_than_body():
    # the (size, line-height) helper measure and draw both consult
    w = ChatThreadWidget("t")
    c = _canvas()
    hvl = w._layout_body(c, "# Big", 400.0, 12.0)[0]
    pvl = w._layout_body(c, "plain body line", 400.0, 12.0)[0]
    hsize, hlh = w._vline_layout(hvl, 12.0)
    psize, plh = w._vline_layout(pvl, 12.0)
    assert hsize == mi.heading_size(1, 12.0) and hlh > plh
    # a non-heading vline is byte-identical to the historical body metrics
    assert psize == 12.0 and plh == 12.0 * 1.5


def test_non_heading_prose_layout_is_unchanged():
    # a body with no heading must lay out exactly like the plain wrap path
    w = ChatThreadWidget("t")
    c = _canvas()
    text = "word " * 30
    c.set_font(_style.FONT_UI, 12.0, "regular")
    plain = w._wrap(c, text.strip(), 200.0)
    vlines = w._layout_body(c, text.strip(), 200.0, 12.0)
    assert ["".join(r.text for r in v.runs) for v in vlines] == plain
    assert all(v.heading is False and v.size is None for v in vlines)


# -- styled draw (fed via on_bus_state) ---------------------------------------

def _capture_draw(w, rect=Rect(0, 0, 456, 356)):
    """Draw the thread, returning (canvas, [(text, family, weight, size,
    color)]) the body painted."""
    c = _canvas()
    events = []
    font = {"family": None, "size": None, "weight": None}
    color = {"rgba": None}
    real_setf, real_setc, real_dt = (c.set_font, c.set_fill_color, c.draw_text)

    def set_font(family, size, weight="regular"):
        font.update(family=family, size=size, weight=weight)
        return real_setf(family, size, weight)

    def set_fill(*a):
        color["rgba"] = tuple(a)
        return real_setc(*a)

    def dt(s, *a, **k):
        events.append((s, font["family"], font["weight"], font["size"],
                       color["rgba"]))
        return real_dt(s, *a, **k)

    c.set_font, c.set_fill_color, c.draw_text = set_font, set_fill, dt
    w.draw(c, rect)
    return c, events


def test_heading_renders_bold_at_heading_size():
    from composeio.render.widgets import console_theme as ct
    t = ct.CONSOLE.active
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect",
                    "text": "## Deploy plan\n\nthen the body text follows"})
    _c, events = _capture_draw(w)
    hsize = mi.heading_size(2, w.font_size)
    # the heading title painted BOLD, in Plex Sans, at the (scaled) heading
    # size, in ink — and never a literal "#"
    assert any(s == "Deploy plan" and fam == _style.FONT_UI
               and weight == "bold" and abs(size - hsize) < 0.01
               and col == tuple(t.ink)
               for (s, fam, weight, size, col) in events)
    # the body text drew at the ordinary body size (regular)
    assert any("body" in s and abs(size - w.font_size) < 0.01
               for (s, fam, weight, size, col) in events)
    assert all("#" not in s for (s, *_r) in events)


def test_inline_code_inside_a_heading_keeps_mono_accent():
    from composeio.render.widgets import console_theme as ct
    t = ct.CONSOLE.active
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect", "text": "## run `power_status`"})
    _c, events = _capture_draw(w)
    # the code span keeps its mono/accent face even inside a heading
    assert any(s == "power_status" and fam == _style.FONT_MONO
               and col == tuple(t.accent)
               for (s, fam, weight, size, col) in events)


def test_headless_capture_of_a_heading_bubble(tmp_path):
    """A real thread bubble with a `##` heading renders to a PNG (David's
    glass): the capture is non-blank and the heading title painted."""
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect",
                    "text": "## Deploy checklist\n\n- power-cycle gilligan\n"
                            "- rerun the soak on `fleet-540`"})
    c, events = _capture_draw(w)
    out = os.environ.get("HEADING_CAPTURE_PNG") \
        or str(tmp_path / "thread_heading.png")
    c.save_png(out)
    assert os.path.getsize(out) > 0
    drawn = [s for (s, *_r) in events]
    assert "Deploy checklist" in drawn
    print("heading bubble capture:", out)


if __name__ == "__main__":
    test_thread_uses_shared_heading_parser()
    test_viewer_and_thread_share_the_size_hierarchy()
    test_heading_line_becomes_a_sized_bold_heading_vline()
    test_heading_levels_scale_down_by_level()
    test_heading_line_pitch_is_taller_than_body()
    test_non_heading_prose_layout_is_unchanged()
    test_heading_renders_bold_at_heading_size()
    test_inline_code_inside_a_heading_keeps_mono_accent()
    import tempfile
    test_headless_capture_of_a_heading_bubble(
        __import__("pathlib").Path(tempfile.mkdtemp()))
    print("PASS: thread ATX headings")
