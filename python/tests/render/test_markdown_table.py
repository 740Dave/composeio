"""
Markdown TABLE rendering ([CXC-ICV-CHAT] tables).

Two layers under test:

  * the REUSABLE ``markdown_table`` module (parse -> model, column-width
    layout, a rendered table on a recording canvas) — host-agnostic, so these
    exercise it directly, the way the coming LTS viewer will;
  * its INTEGRATION into ``ChatThreadWidget``: a table in a message body
    renders, and its cells plug into the SAME ``_sel_lines`` selection
    mechanism (pixel->char lands in the right cell; a cell selection copies
    the expected substring via the fake clipboard patched on the name the copy
    path actually calls).

Cells render through the SHARED ``markdown_inline`` surface, so a cell's
``**bold``/`` `code` `` is byte-identical to body markdown — pinned here too.

All headless.  A seeded full-scene console render (with a table) is written to
a PNG so a human can glass-check it.
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
from composeio.render.widgets import chat_thread as chat_mod
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets import markdown_table as mdt
from composeio.render.widgets import markdown_inline as mi

GUI = 0x0400            # KMOD_LGUI (macOS Cmd) — mirrors the widget literals

TABLE = (
    "| Unit | Status | Charge |\n"
    "|:-----|:------:|-------:|\n"
    "| gilligan | draining | 42 |\n"
    "| skipper | idle | 88 |\n"
)


def _canvas(w=520, h=420):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x16)
    c.fill_rect(0, 0, w, h)
    return c


def _reply(w, text):
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect", "text": text})


# ── parser ────────────────────────────────────────────────────────────────────

def test_parse_basic_table_rows_cols_alignment():
    m = mdt.parse_pipe_table(TABLE.strip().split("\n"))
    assert m is not None
    assert m.headers == ["Unit", "Status", "Charge"]
    assert m.ncols == 3
    assert m.aligns == ["left", "center", "right"]
    assert m.rows == [["gilligan", "draining", "42"],
                      ["skipper", "idle", "88"]]


def test_parse_tolerates_missing_outer_pipes():
    src = ["a | b", "--- | ---", "1 | 2"]
    m = mdt.parse_pipe_table(src)
    assert m is not None
    assert m.headers == ["a", "b"]
    assert m.rows == [["1", "2"]]


def test_parse_pads_and_truncates_ragged_rows():
    src = ["| a | b | c |", "|---|---|---|",
           "| only-one |",                 # short -> padded
           "| w | x | y | z |"]            # long  -> truncated
    m = mdt.parse_pipe_table(src)
    assert m.rows[0] == ["only-one", "", ""]
    assert m.rows[1] == ["w", "x", "y"]


def test_delimiter_alignment_variants():
    src = ["| l | c | r | d |", "| :--- | :---: | ---: | --- |"]
    m = mdt.parse_pipe_table(src)
    assert m.aligns == ["left", "center", "right", "left"]


def test_non_table_returns_none():
    assert mdt.parse_pipe_table(["just a line"]) is None
    assert mdt.parse_pipe_table(["a | b", "not a delimiter", "1 | 2"]) is None
    # a bare thematic break must NOT be read as a 1-col table (no pipe)
    assert mdt.parse_pipe_table(["Heading", "---"]) is None


def test_segment_splits_prose_and_table_in_order():
    body = ("Here is the fleet:\n"
            "\n" + TABLE + "\n"
            "Recommend a power-cycle.")
    blocks = mdt.segment_tables(body)
    kinds = [k for k, _ in blocks]
    assert kinds == ["text", "table", "text"]
    assert "fleet" in blocks[0][1]
    assert isinstance(blocks[1][1], mdt.TableModel)
    assert "power-cycle" in blocks[2][1]


def test_segment_no_table_is_single_text_block():
    body = "no tables here\njust two lines"
    assert mdt.segment_tables(body) == [("text", body)]


def test_has_table_predicate():
    assert mdt.has_table(TABLE)
    assert not mdt.has_table("plain **bold** and `code`")


def test_to_source_round_trips():
    m = mdt.parse_pipe_table(TABLE.strip().split("\n"))
    m2 = mdt.parse_pipe_table(m.to_source().split("\n"))
    assert m2.headers == m.headers
    assert m2.rows == m.rows
    assert m2.aligns == m.aligns


# ── layout ────────────────────────────────────────────────────────────────────

def test_column_widths_fit_widest_cell():
    m = mdt.parse_pipe_table(TABLE.strip().split("\n"))
    c = _canvas()
    style = mdt.TableStyle(font_size=12.0)
    layout = mdt.measure_table(c, m, style, max_width=10_000)
    assert len(layout.col_widths) == 3
    # column 0 holds "gilligan" (widest) — measure it bold-free and confirm the
    # column is at least that wide + padding
    c.set_font(mi.FAMILY_UI, 12.0, "regular")
    gill_w = c.measure_text("gilligan").width
    assert layout.col_widths[0] >= gill_w + 2 * style.cell_pad_x - 0.5
    # header row + 2 body rows
    assert abs(layout.total_height
               - (layout.header_h + 2 * layout.row_h)) < 1e-6


def test_overflow_shrinks_and_ellipsizes():
    m = mdt.parse_pipe_table(
        ["| description |", "|---|",
         "| a very long cell that will not fit into a tiny column at all |"]
        )
    c = _canvas()
    style = mdt.TableStyle(font_size=12.0)
    layout = mdt.measure_table(c, m, style, max_width=90)
    assert layout.total_width <= 90 + 0.5
    # the body cell was ellipsized (its drawn runs end in the ellipsis)
    body_runs = layout.cell_runs[1][0]
    assert body_runs[-1].text.endswith("…")


# ── rendered table (recording canvas) ─────────────────────────────────────────

def test_draw_table_paints_text_and_rules():
    m = mdt.parse_pipe_table(TABLE.strip().split("\n"))
    c = _canvas()
    style = mdt.TableStyle(font_size=12.0)
    layout = mdt.measure_table(c, m, style, max_width=480)

    drawn, lines = [], []
    real_dt, real_dl = c.draw_text, c.draw_line

    def dt(txt, x, y, align="left"):
        drawn.append(txt)
        return real_dt(txt, x, y, align)

    def dl(x1, y1, x2, y2):
        lines.append((x1, y1, x2, y2))
        return real_dl(x1, y1, x2, y2)

    c.draw_text, c.draw_line = dt, dl
    mdt.draw_table(c, m, layout, 10, 10, style)
    joined = "".join(drawn)
    for token in ("Unit", "Status", "Charge", "gilligan", "draining", "88"):
        assert token in joined
    # a header rule (a horizontal line) was drawn
    assert any(abs(y1 - y2) < 1e-6 for x1, y1, x2, y2 in lines)


def test_record_callback_fires_per_cell_row_major():
    m = mdt.parse_pipe_table(TABLE.strip().split("\n"))
    c = _canvas()
    style = mdt.TableStyle(font_size=12.0)
    layout = mdt.measure_table(c, m, style, max_width=480)
    seen = []

    def rec(text, bounds, y0, y1, row_start):
        seen.append((text, row_start, len(bounds) == len(text) + 1))
        return None

    mdt.draw_table(c, m, layout, 10, 10, style, record=rec)
    # 3 cols x 3 rows (header + 2 body) = 9 cells, row-major
    assert [s[0] for s in seen] == ["Unit", "Status", "Charge",
                                    "gilligan", "draining", "42",
                                    "skipper", "idle", "88"]
    # every 3rd cell (column 0) is a row start
    assert [s[1] for s in seen] == [True, False, False] * 3
    # bounds have len(text)+1 entries (the one-measurement-path invariant)
    assert all(s[2] for s in seen)


def test_cell_uses_shared_inline_markdown():
    # a cell's **bold** / `code` tokenizes through the SAME markdown_inline
    # surface as body prose — the drawn cell text drops the markers
    m = mdt.parse_pipe_table(
        ["| a | b |", "|---|---|", "| **loud** | `cmd` |"])
    c = _canvas()
    style = mdt.TableStyle(font_size=12.0)
    layout = mdt.measure_table(c, m, style, max_width=480)
    row = layout.cell_runs[1]
    assert row[0] == [mi.InlineRun("loud", "bold")]
    assert row[1] == [mi.InlineRun("cmd", "code")]


# ── chat_thread integration + selection over a cell ───────────────────────────

def _table_cells(w):
    """The recorded _SelLine cells (drawn table cells) in draw order."""
    texts = {"Unit", "Status", "Charge", "gilligan", "draining",
             "42", "skipper", "idle", "88"}
    return [sl for sl in w._sel_lines if sl.text in texts]


def test_table_message_records_selectable_cells():
    w = ChatThreadWidget("t")
    _reply(w, "Fleet snapshot:\n\n" + TABLE)
    c = _canvas()
    w.draw(c, Rect(0, 0, 520, 420))
    cells = _table_cells(w)
    got = {sl.text for sl in cells}
    # every cell is a selectable line — no silent non-selectable holes
    assert {"Unit", "gilligan", "draining", "88", "skipper"} <= got
    # each cell's bounds are len(text)+1 (one measurement path)
    for sl in cells:
        assert len(sl.bounds) == len(sl.text) + 1


def test_pixel_to_char_lands_in_the_right_cell():
    w = ChatThreadWidget("t")
    _reply(w, TABLE)
    c = _canvas()
    w.draw(c, Rect(0, 0, 520, 420))
    cells = {sl.text: sl for sl in _table_cells(w)}
    # cells in the SAME body row share a y-band; the x tie-break must pick the
    # column under the pointer, not the first cell in the band
    dr = cells["draining"]     # row 1, column 1 (center-aligned)
    y = (dr.y_top + dr.y_bot) * 0.5
    # aim at the middle of "draining" -> its own (msg, line)
    xmid = (dr.bounds[0] + dr.bounds[-1]) / 2.0
    pos = w._index_at(xmid, y)
    assert pos[:2] == (dr.msg_idx, dr.line_idx)
    # aim at column 0 on the same row -> the gilligan cell, not draining
    gl = cells["gilligan"]
    pos0 = w._index_at((gl.bounds[0] + gl.bounds[-1]) / 2.0, y)
    assert pos0[:2] == (gl.msg_idx, gl.line_idx)


def test_cell_selection_copies_the_expected_substring(monkeypatch):
    got = []
    monkeypatch.setattr(chat_mod, "_clipboard_set", got.append)
    w = ChatThreadWidget("t")
    _reply(w, TABLE)
    c = _canvas()
    w.draw(c, Rect(0, 0, 520, 420))
    gl = {sl.text: sl for sl in _table_cells(w)}["gilligan"]
    # select the whole cell via a drag, then Cmd+C
    y = (gl.y_top + gl.y_bot) * 0.5
    w.on_pointer_down(gl.bounds[0], y)
    w.on_pan(gl.bounds[-1] - gl.bounds[0], 0)
    assert w._selected_text() == "gilligan"
    w.on_key("C", GUI)
    assert got == ["gilligan"]


def test_cell_highlight_band_paints_accent_line():
    w = ChatThreadWidget("t")
    _reply(w, TABLE)
    c = _canvas()
    w.draw(c, Rect(0, 0, 520, 420))
    gl = {sl.text: sl for sl in _table_cells(w)}["gilligan"]
    w._sel_anchor = (gl.msg_idx, gl.line_idx, 0)
    w._sel_focus = (gl.msg_idx, gl.line_idx, len("gilligan"))
    w._sel_epoch = w._epoch()

    t = ct.CONSOLE.active
    bands = []
    cur = {"fill": None}
    real_sf, real_fr = c.set_fill_color, c.fill_rect

    def set_fill(*a):
        cur["fill"] = tuple(a)
        return real_sf(*a)

    def fill_rect(x, y, ww, hh):
        if cur["fill"] == tuple(t.accent_line):
            bands.append((x, ww))
        return real_fr(x, y, ww, hh)

    c.set_fill_color, c.fill_rect = set_fill, fill_rect
    w.draw(c, Rect(0, 0, 520, 420))
    assert bands, "a cell selection must paint the accent_line band"
    gl2 = {sl.text: sl for sl in _table_cells(w)}["gilligan"]
    expect_w = gl2.bounds[len("gilligan")] - gl2.bounds[0]
    assert any(abs(bw - expect_w) < 0.5 for _x, bw in bands)


def test_table_and_prose_coexist_in_one_bubble():
    w = ChatThreadWidget("t")
    _reply(w, "before\n\n" + TABLE + "\nafter")
    c = _canvas()
    w.draw(c, Rect(0, 0, 520, 420))
    got = {sl.text for sl in w._sel_lines}
    assert "before" in got and "after" in got     # prose still there
    assert "gilligan" in got                        # table cell too


# ── seeded full-scene render (glass-checkable PNG) ────────────────────────────

def test_seeded_console_table_render(tmp_path):
    w = ChatThreadWidget("console")
    w.on_bus_state("thread/narration",
                   {"from": "architect",
                    "text": "Pulling the fleet roster now."})
    _reply(w, "Here is the current fleet — **charge** is the gate:\n\n"
              + TABLE + "\nRecommend cycling `gilligan` before the soak.")
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "proceed"})
    c = _canvas(520, 420)
    w.draw(c, Rect(6, 6, 508, 408))
    out = tmp_path / "chat_thread_table.png"
    c.save_png(str(out))
    assert out.exists() and out.stat().st_size > 3000
    # also drop a copy into the repo captures dir for the human glass-check
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    c.save_png(os.path.join(cap_dir, "chat_thread_table.png"))
