"""
Tests for the ChatThreadWidget ([SPEC:CXC-IC] [CXC-IC-CHAT]).

Covers the two load-bearing behaviors that static reading can't prove:
voice resolution from (topic, from-field), and Canvas-contract word-wrap
that actually paints without error.  A full-scene headless render proves
the parser registration + multi-namespace subscription wiring end-to-end.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import ChatThreadWidget
from composeio.render.widgets import style as _style
from composeio.render.widgets.chat_thread import DEFAULT_NAMESPACES

from .conftest import surface_to_array


def _canvas(w=456, h=356):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x16)
    c.fill_rect(0, 0, w, h)
    return c


# -- voice resolution ---------------------------------------------------------

def test_voice_narration_is_dim_left():
    w = ChatThreadWidget("t")
    w.on_bus_state("thread/narration",
                   {"from": "architect", "text": "thinking...", "turn": 1})
    m = w._messages[-1]
    assert m.color == w.dim
    assert m.align == "left"
    assert m.dim_sender is True
    assert m.sender == "architect"


def test_voice_architect_out_is_accent_left():
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect", "text": "reply"})
    m = w._messages[-1]
    assert m.color == w.accent
    assert m.align == "left"


def test_voice_harness_in_is_amber_centered():
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/in",
                   {"from": "harness", "text": "tool = ok", "kind": "tool_result"})
    m = w._messages[-1]
    assert m.color == w.harness
    assert m.align == "center"
    assert m.sender == "harness"


def test_voice_david_in_is_neutral_left():
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "proceed"})
    m = w._messages[-1]
    assert m.color == w.neutral
    assert m.align == "left"
    assert m.sender == "david"


def test_payload_accepts_json_string():
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   '{"from":"architect","text":"json arrived"}')
    assert w._messages[-1].text == "json arrived"


def test_missing_text_is_dropped():
    w = ChatThreadWidget("t")
    w.on_bus_state("thread/narration", {"from": "architect", "turn": 1})
    assert w._messages == []


def test_scrollback_ring_caps_at_max_messages():
    w = ChatThreadWidget("t", max_messages=5)
    for i in range(20):
        w.on_bus_state("intelligence/architect/out",
                       {"from": "architect", "text": f"m{i}"})
    assert len(w._messages) == 5
    assert w._messages[-1].text == "m19"


def test_default_namespaces_are_the_three_topics():
    w = ChatThreadWidget("t")
    assert w.namespaces == list(DEFAULT_NAMESPACES)
    assert len(w.namespaces) == 3


# -- wrapping -----------------------------------------------------------------

def test_wrap_breaks_long_line_to_column():
    w = ChatThreadWidget("t")
    c = _canvas()
    c.set_font("sans-serif", 12, "regular")
    text = "word " * 60
    lines = w._wrap(c, text.strip(), 200.0)
    assert len(lines) > 1
    for ln in lines:
        assert c.measure_text(ln).width <= 200.0 + 0.5


def test_wrap_hard_breaks_unbroken_token():
    w = ChatThreadWidget("t")
    c = _canvas()
    c.set_font("sans-serif", 12, "regular")
    token = "x" * 400   # no spaces, must break by character
    lines = w._wrap(c, token, 120.0)
    assert len(lines) > 1
    for ln in lines:
        assert c.measure_text(ln).width <= 120.0 + 0.5


def test_wrap_preserves_explicit_newlines():
    w = ChatThreadWidget("t")
    c = _canvas()
    c.set_font("sans-serif", 12, "regular")
    lines = w._wrap(c, "line one\nline two", 400.0)
    assert lines == ["line one", "line two"]


# -- BASIC inline markdown: tokenization --------------------------------------

def _kinds(runs):
    return [(r.text, r.kind) for r in runs]


def test_tokenize_bold_drops_asterisks():
    runs = ChatThreadWidget._tokenize_inline("say **hello** now")
    assert _kinds(runs) == [("say ", "plain"), ("hello", "bold"),
                            (" now", "plain")]


def test_tokenize_code_drops_backticks():
    runs = ChatThreadWidget._tokenize_inline("run `power_cycle` please")
    assert _kinds(runs) == [("run ", "plain"), ("power_cycle", "code"),
                            (" please", "plain")]


def test_tokenize_plain_text_is_unchanged():
    runs = ChatThreadWidget._tokenize_inline("just ordinary prose, nothing here")
    assert _kinds(runs) == [("just ordinary prose, nothing here", "plain")]


def test_tokenize_mixed_bold_and_code():
    runs = ChatThreadWidget._tokenize_inline("a **b** and `c` end")
    assert _kinds(runs) == [("a ", "plain"), ("b", "bold"),
                            (" and ", "plain"), ("c", "code"),
                            (" end", "plain")]


def test_tokenize_unclosed_bold_is_literal():
    # a ** with no closer must render as literal "**", never eat the rest
    runs = ChatThreadWidget._tokenize_inline("this is **not closed")
    assert _kinds(runs) == [("this is **not closed", "plain")]
    assert all(r.kind == "plain" for r in runs)


def test_tokenize_lone_backtick_is_literal():
    runs = ChatThreadWidget._tokenize_inline("a lone ` backtick")
    assert _kinds(runs) == [("a lone ` backtick", "plain")]


def test_tokenize_single_asterisk_is_not_italic():
    # single * stays literal — the bundled Plex face has no italic
    runs = ChatThreadWidget._tokenize_inline("use *stars* literally")
    assert _kinds(runs) == [("use *stars* literally", "plain")]


def test_tokenize_empty_and_missing_closer_never_crash():
    for s in ["", "`", "**", "***", "`unterminated", "**a`b", "` ` `"]:
        runs = ChatThreadWidget._tokenize_inline(s)
        # round-trips: styled runs drop their markers, literals keep them
        assert isinstance(runs, list)


# -- BASIC inline markdown: bullets + mixed-run wrapping -----------------------

def test_bullet_line_layout_marks_first_line_and_indents():
    w = ChatThreadWidget("t")
    c = _canvas()
    vlines = w._layout_body(c, "- do the thing", 400.0, 12.0)
    assert vlines[0].bullet is True
    assert vlines[0].indent > 0.0
    # the "- " marker is stripped from the content runs (replaced by "•")
    assert "".join(r.text for r in vlines[0].runs) == "do the thing"
    assert not any(r.text.startswith("- ") for r in vlines[0].runs)


def test_bullet_star_marker_also_recognized():
    w = ChatThreadWidget("t")
    c = _canvas()
    vlines = w._layout_body(c, "* starred item", 400.0, 12.0)
    assert vlines[0].bullet is True
    assert "".join(r.text for r in vlines[0].runs) == "starred item"


def test_bullet_continuation_lines_hang_indent():
    w = ChatThreadWidget("t")
    c = _canvas()
    # force a wrap: a long bullet in a narrow column
    vlines = w._layout_body(c, "- " + ("word " * 30).strip(), 160.0, 12.0)
    assert len(vlines) > 1
    assert vlines[0].bullet is True            # marker only on the first line
    assert all(v.bullet is False for v in vlines[1:])
    # every visual line shares the hanging indent
    assert all(v.indent == vlines[0].indent for v in vlines)


def test_mixed_run_line_wraps_across_bold_and_code():
    w = ChatThreadWidget("t")
    c = _canvas()
    text = ("lead in text then a **bold emphasized phrase here** and an "
            "inline `code_span_token` trailing off the end of the column")
    max_w = 180.0
    vlines = w._layout_body(c, text, max_w, 12.0)
    assert len(vlines) > 1, "the mixed-run line must wrap into several lines"
    # every wrapped line stays within the column (measured per-run font)
    for v in vlines:
        assert w._vline_width(c, v, 12.0) <= max_w + 0.5
    # both a bold and a code run survived the wrap somewhere
    kinds = {r.kind for v in vlines for r in v.runs}
    assert "bold" in kinds and "code" in kinds
    # and no literal markers leaked into any drawn run
    joined = "".join(r.text for v in vlines for r in v.runs)
    assert "**" not in joined and "`" not in joined


def test_plain_body_layout_matches_plain_wrap():
    # a body with no markdown must lay out exactly like the old plain wrap
    w = ChatThreadWidget("t")
    c = _canvas()
    text = "word " * 40
    c.set_font(_style.FONT_UI, 12.0, "regular")
    plain = w._wrap(c, text.strip(), 200.0)
    vlines = w._layout_body(c, text.strip(), 200.0, 12.0)
    assert ["".join(r.text for r in v.runs) for v in vlines] == plain


# -- BASIC inline markdown: styled draw (fed via on_bus_state) -----------------

def _capture_draw(w, rect=Rect(0, 0, 456, 356)):
    """Draw the thread, returning the list of (text, family, weight, color)
    the body actually painted."""
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
        events.append((s, font["family"], font["weight"], color["rgba"]))
        return real_dt(s, *a, **k)

    c.set_font, c.set_fill_color, c.draw_text = set_font, set_fill, dt
    w.draw(c, rect)
    return c, events


def test_markdown_reply_renders_styled_runs():
    from composeio.render.widgets import console_theme as ct
    t = ct.CONSOLE.active
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect",
                    "text": "cycle **fleetnode2** then run `power_status` twice"})
    _c, events = _capture_draw(w)
    # the bold inner drew in Plex Sans BOLD, ink color; markers gone
    assert any(s == "fleetnode2" and weight == "bold"
               and fam == _style.FONT_UI and col == tuple(t.ink)
               for (s, fam, weight, col) in events)
    # the code inner drew in Plex MONO, accent color
    assert any(s == "power_status" and fam == _style.FONT_MONO
               and col == tuple(t.accent)
               for (s, fam, weight, col) in events)
    # nothing painted still carries a literal marker
    assert all("**" not in s and "`" not in s for (s, *_r) in events)


def test_markdown_bullets_render_the_dot_marker():
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect",
                    "text": "plan:\n- power-cycle the node\n- rerun the soak"})
    _c, events = _capture_draw(w)
    drawn = [s for (s, *_r) in events]
    assert "•" in drawn                        # the bullet marker was painted
    # the literal "- " markers were replaced, never drawn as body text
    assert not any(s.startswith("- ") for s in drawn)


# -- dropped-image affordance (payload["image"]) ------------------------------

def _path_ink(events):
    """Reconstruct the drawn file path from the dim-mono 'path' run pieces
    (a long absolute path hard-breaks across visual lines, so no single
    draw_text holds the whole thing — join the pieces back in draw order)."""
    from composeio.render.widgets import console_theme as ct
    t = ct.CONSOLE.active
    return "".join(s for (s, fam, weight, col) in events
                   if fam == _style.FONT_MONO and col == tuple(t.dim))


def test_dropped_image_empty_text_draws_the_path():
    """V1 sends a dropped image bare: {from:'david', text:'', image:<path>}.
    The bubble must show the PATH (never render empty)."""
    w = ChatThreadWidget("t")
    path = "/Users/you/Maker/captures/roster-2026-07-19-1440.png"
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "", "image": path})
    # the message survived resolution (empty text is not None -> kept)
    assert w._messages[-1].payload.get("image") == path
    _c, events = _capture_draw(w)
    drawn = [s for (s, *_r) in events]
    # the full path string was painted (reassembled across wrap pieces)…
    assert path in _path_ink(events), "the image path must be drawn"
    # …and a marker affordance is present (no emoji — a text/vector marker)
    assert any("image" in s and "▸" in s for s in drawn)
    # no paperclip-emoji tofu box leaked in
    assert not any("\U0001F4CE" in s for s in drawn)


def test_dropped_image_path_is_mono_dim():
    """The path draws in Plex Mono at the dim ink (legible), its marker at
    the faint ink — both distinct from ordinary ink body text."""
    from composeio.render.widgets import console_theme as ct
    t = ct.CONSOLE.active
    w = ChatThreadWidget("t")
    path = "/abs/very/long/path/to/an/image/file/screenshot.png"
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "", "image": path})
    _c, events = _capture_draw(w)
    # the marker drew at the faint ink, mono
    assert any(fam == _style.FONT_MONO and col == tuple(t.faint) and "▸" in s
               for (s, fam, weight, col) in events), \
        "the marker must be Plex Mono at the faint ink"
    # the path drew at the dim ink, mono (reassembled)
    assert _path_ink(events) == path, \
        "the path must be Plex Mono at the dim ink"


def test_dropped_image_with_caption_draws_both():
    """A future caption: text renders first, the image affordance beneath."""
    w = ChatThreadWidget("t")
    path = "/Users/you/captures/thing.png"
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "look at this render",
                    "image": path})
    _c, events = _capture_draw(w)
    drawn = [s for (s, *_r) in events]
    # the caption text made it in (as wrapped run(s))…
    joined = "".join(drawn)
    assert "look at this render" in joined
    # …and so did the path affordance
    assert path in _path_ink(events)


def test_dropped_image_bubble_is_right_anchored_like_david():
    """A dropped image is a DAVID-voice turn: right-anchored, panel2 bubble —
    the image affordance does not change the voice treatment."""
    from composeio.render.widgets import console_theme as ct
    t = ct.CONSOLE.active

    def bubble_x(payload):
        w = ChatThreadWidget("t")
        w.on_bus_state("intelligence/architect/in", payload)
        c = _canvas()
        xs = []
        cur = {"fill": None}
        real_sf, real_frr = c.set_fill_color, c.fill_round_rect

        def set_fill(*a):
            cur["fill"] = tuple(a)
            return real_sf(*a)

        def frr(x, y, bw, bh, r):
            if r == ct.RADIUS_BUBBLE and cur["fill"] == tuple(t.panel2):
                xs.append(x)
            return real_frr(x, y, bw, bh, r)

        c.set_fill_color, c.fill_round_rect = set_fill, frr
        w.draw(c, Rect(0, 0, 456, 356))
        return xs

    img_xs = bubble_x({"from": "david", "text": "",
                       "image": "/abs/path/img.png"})
    txt_xs = bubble_x({"from": "david", "text": "plain words"})
    assert img_xs, "a dropped image must paint the david panel2 bubble"
    # right-anchored: bubble x is well past the left content edge
    assert img_xs[0] > 200.0
    assert txt_xs and abs(img_xs[0] - txt_xs[0]) < 200.0  # same voice family


def test_plain_text_message_has_no_image_affordance():
    """A message WITHOUT an image field is unchanged: no marker, no path
    run — the frozen text-only path stays exactly as before."""
    w = ChatThreadWidget("t")
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "just a normal line of text"})
    _c, events = _capture_draw(w)
    drawn = [s for (s, *_r) in events]
    assert not any("▸" in s for s in drawn), "no image marker for text"
    joined = "".join(drawn)
    assert "just a normal line of text" in joined


def test_dropped_image_via_on_bus_state_headless():
    """End-to-end headless: feed a dropped-image message through on_bus_state
    and confirm the path is painted (David's verify ask)."""
    w = ChatThreadWidget("console")
    path = "/Users/you/Maker/composeio/scratch/dropped-image.png"
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "", "image": path})
    c = _canvas()
    drawn = []
    real_dt = c.draw_text

    def dt(s, *a, **k):
        drawn.append(s)
        return real_dt(s, *a, **k)

    c.draw_text = dt
    w.draw(c, Rect(0, 0, 456, 356))
    # the path hard-breaks across lines; its pieces are contiguous in draw
    # order (who-line + marker, then the path pieces), so it reassembles here
    assert path in "".join(drawn), "headless draw must paint the path"


# -- draw does not raise (all four voices + empty) ----------------------------

def test_draw_all_four_voices():
    w = ChatThreadWidget("console")
    w.on_bus_state("thread/narration",
                   {"from": "architect",
                    "text": "Scanning the roster; holding until charge clears sixty. "
                            "This is a deliberately long narration line so wrapping runs.",
                    "turn": 1})
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect",
                    "text": "Recommend a power-cycle of fleetnode2 before the soak."})
    w.on_bus_state("intelligence/architect/in",
                   {"from": "harness", "text": "power_status(fleetnode2) = offline"})
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "proceed — cycle it."})
    c = _canvas()
    w.draw(c, Rect(0, 0, 456, 356))
    arr = surface_to_array(c)
    # non-trivial: something other than the flat background was painted
    assert arr[..., :3].std() > 5.0


def test_narration_draws_the_reply_bubble():
    """Narration renders with the SAME bubble as reply (accent-dim fill +
    accent-line border + RADIUS_BUBBLE, left anchor); its who-line label
    ("ARCHITECT (Narration) …", David's format) is the only remaining
    distinguisher and must survive verbatim."""
    from composeio.render.widgets import console_theme as ct

    w = ChatThreadWidget("t")
    w.on_bus_state("thread/narration",
                   {"from": "architect", "text": "auto-mirrored thinking"})
    c = _canvas()
    t = ct.CONSOLE.active

    fills, strokes, texts = [], [], []
    cur = {"fill": None, "stroke": None}
    real_sf, real_frr = c.set_fill_color, c.fill_round_rect
    real_ss, real_srr = c.set_stroke_color, c.stroke_round_rect
    real_dt = c.draw_text

    def set_fill(*a):
        cur["fill"] = tuple(a)
        return real_sf(*a)

    def frr(x, y, bw, bh, r):
        fills.append((cur["fill"], x, r))
        return real_frr(x, y, bw, bh, r)

    def set_stroke(*a):
        cur["stroke"] = tuple(a)
        return real_ss(*a)

    def srr(x, y, bw, bh, r):
        strokes.append((cur["stroke"], r))
        return real_srr(x, y, bw, bh, r)

    def dt(s, *a, **k):
        texts.append(s)
        return real_dt(s, *a, **k)

    c.set_fill_color, c.fill_round_rect = set_fill, frr
    c.set_stroke_color, c.stroke_round_rect = set_stroke, srr
    c.draw_text = dt
    w.draw(c, Rect(0, 0, 456, 356))

    # the reply bubble treatment: accent_dim fill at the bubble radius…
    accent_fills = [f for f in fills
                    if f[0] == tuple(t.accent_dim) and f[2] == ct.RADIUS_BUBBLE]
    assert accent_fills, "narration must paint the accent-dim reply bubble"
    # …with the accent-line border at the same radius
    assert any(s[0] == tuple(t.accent_line) and s[1] == ct.RADIUS_BUBBLE
               for s in strokes), "narration bubble must carry the accent border"
    # and the who-line label survives verbatim (the sole distinguisher)
    assert any("ARCHITECT (Narration)" in s for s in texts)


def test_narration_bubble_left_anchored_like_reply():
    """Narration and reply bubbles share the left anchor; david stays right."""
    from composeio.render.widgets import console_theme as ct

    def bubble_x(topic, payload):
        w = ChatThreadWidget("t")
        w.on_bus_state(topic, payload)
        c = _canvas()
        t = ct.CONSOLE.active
        xs = []
        cur = {"fill": None}
        real_sf, real_frr = c.set_fill_color, c.fill_round_rect

        def set_fill(*a):
            cur["fill"] = tuple(a)
            return real_sf(*a)

        def frr(x, y, bw, bh, r):
            if r == ct.RADIUS_BUBBLE:
                xs.append(x)
            return real_frr(x, y, bw, bh, r)

        c.set_fill_color, c.fill_round_rect = set_fill, frr
        w.draw(c, Rect(0, 0, 456, 356))
        assert xs, "expected a bubble"
        return xs[0]

    narr_x = bubble_x("thread/narration", {"from": "architect", "text": "hm"})
    reply_x = bubble_x("intelligence/architect/out",
                       {"from": "architect", "text": "hm"})
    david_x = bubble_x("intelligence/architect/in",
                       {"from": "david", "text": "hm"})
    assert narr_x == reply_x            # same left anchor
    assert david_x > narr_x             # david remains right-anchored


# -- David's header format + timestamps ([CXC-IC-CHAT]) -----------------------

def test_header_architect_narration_format_with_ts():
    """Architect narration header is David's exact format:
    'ARCHITECT (Narration) @HH:MM on Mon DD'."""
    from datetime import datetime
    w = ChatThreadWidget("t")
    # built from a LOCAL datetime so the epoch round-trips tz-independently
    ts = datetime(2026, 7, 22, 13, 15).timestamp()
    w.on_bus_state("thread/narration",
                   {"from": "architect", "text": "scanning", "ts": ts})
    texts = [s for (s, *_r) in _capture_draw(w)[1]]
    assert "ARCHITECT (Narration) @13:15 on Jul 22" in texts


def test_header_architect_reply_format_with_ts():
    """Architect reply header: 'ARCHITECT (Reply) @HH:MM on Mon DD' — the
    parenthesized voice label is the narration/reply distinguisher."""
    from datetime import datetime
    w = ChatThreadWidget("t")
    ts = datetime(2026, 7, 22, 9, 5).timestamp()   # 09:05 — zero-padded HH:MM
    w.on_bus_state("intelligence/architect/out",
                   {"from": "architect", "text": "recommend a cycle", "ts": ts})
    texts = [s for (s, *_r) in _capture_draw(w)[1]]
    assert "ARCHITECT (Reply) @09:05 on Jul 22" in texts


def test_header_david_inbound_format_with_ts():
    """David inbound header: '<name> @HH:MM on Mon DD' with a capitalized
    handle."""
    from datetime import datetime
    w = ChatThreadWidget("t")
    ts = datetime(2026, 7, 22, 13, 17).timestamp()
    w.on_bus_state("intelligence/architect/in",
                   {"from": "david", "text": "proceed", "ts": ts})
    texts = [s for (s, *_r) in _capture_draw(w)[1]]
    assert "David @13:17 on Jul 22" in texts


def test_header_stamps_receipt_time_when_ts_absent():
    """A ts-less inbound turn (esp. David's, via the composer/bridge) is stamped
    with receipt time at ingestion, so its bubble shows @time instead of degrading
    to a name-only header (David 2026-07-22). The FORMATTER still degrades a
    genuinely-unparseable ts to "" (see test_format_stamp_epoch_iso_and_bad)."""
    w = ChatThreadWidget("t")
    w.on_bus_state("thread/narration", {"from": "architect", "text": "hm"})
    w.on_bus_state("intelligence/architect/in", {"from": "david", "text": "hi"})
    texts = [s for (s, *_r) in _capture_draw(w)[1]]
    hdrs = [s for s in texts
            if s.startswith("ARCHITECT") or s.startswith("David")]
    assert any(s.startswith("ARCHITECT (Narration)") for s in hdrs)
    assert any(s.startswith("David") for s in hdrs)
    # both ts-less turns are now receipt-stamped -> every header carries @time
    assert all("@" in s for s in hdrs), f"expected @time on every header: {hdrs}"


def test_format_stamp_epoch_iso_and_bad():
    from datetime import datetime
    from composeio.render.widgets.chat_thread import _format_stamp
    epoch = datetime(2026, 7, 22, 13, 15).timestamp()
    assert _format_stamp(epoch) == "13:15 on Jul 22"
    assert _format_stamp(str(epoch)) == "13:15 on Jul 22"
    # naive ISO keeps its wall-clock fields (tz-independent)
    assert _format_stamp("2026-07-22T13:15:00") == "13:15 on Jul 22"
    assert _format_stamp(None) == ""
    assert _format_stamp("") == ""
    assert _format_stamp("not-a-time") == ""


def test_draw_empty_thread_shows_placeholder():
    w = ChatThreadWidget("console")
    c = _canvas()
    # must not raise on an empty thread
    w.draw(c, Rect(0, 0, 456, 356))


def test_measure_fits_constraints():
    w = ChatThreadWidget("console")
    s = w.measure(Constraints(300, 200))
    assert s.width <= 300 and s.height <= 200


# -- end-to-end: parser registration + multi-namespace wiring -----------------

def test_scene_parses_and_wires_three_subscriptions():
    from composeio.render.engine.parser import parse_compose_yaml
    scene = os.path.join(PY_ROOT, "composeio", "render", "examples",
                         "chat_thread_demo.compose.yaml")
    comp = parse_compose_yaml(scene)
    # the chat_thread widget subscribed all three topics
    subbed = [ns for ns, _w in comp.subscriptions]
    assert "thread/narration" in subbed
    assert "intelligence/architect/out" in subbed
    assert "intelligence/architect/in" in subbed


def test_headless_scene_render(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    scene = os.path.join(PY_ROOT, "composeio", "render", "examples",
                         "chat_thread_demo.compose.yaml")
    out = str(tmp_path / "chat_thread.png")
    app = RenderApp.from_file(scene, windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.exists(out)
    assert os.path.getsize(out) > 2000   # non-trivial PNG
