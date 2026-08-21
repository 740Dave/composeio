#!/usr/bin/env python3
"""
gen_chat_wrap_corpus_header.py — chat_thread word-wrap PARITY corpus -> C++
header ([SPEC:CXC-IC], the CYD chat_thread port).

The load-bearing cross-tier claim for chat_thread is that each message
bubble's word-wrap on the CYD makes the IDENTICAL line-break decisions as the
DESKTOP widget's own wrapper (ChatThreadWidget._layout_body /
_wrap_runs), so a bubble reads byte-for-byte the same on a Mac and a $15
ESP32.  That transitively rests on two facts:

  1. the CYD wraps bubble bodies through the shared substrate text_wrap.cpp,
     already proven bit-identical to MultiLineEditMixin._wrap_lines
     (cyd/test/wrap_corpus_generated.h, env:native_wrap), and
  2. for PLAIN prose (the band tier renders plain prose only — no inline
     markdown), ChatThreadWidget._layout_body produces the SAME line breaks
     as _wrap_lines.

This script pins (2) AND (1) together END-TO-END: it makes the ACTUAL chat
widget wrapper the ORACLE — it runs ``ChatThreadWidget._layout_body`` over a
battery of real message bodies at the real bubble inner width and bakes the
resulting per-line TEXTS.  The host harness cyd/src/chat_wrap_selftest.cpp then
drives the SAME bodies + per-glyph advances through the FULL CYD path
(measure_prefix_advances -> wrap_lines, rstripping the trailing soft-wrap space
exactly as chat_thread.cpp draws) and matches each rendered line string
bit-for-bit.  If the two wrappers ever diverge, this fails.

Same corpus idiom as gen_wrap_corpus_header.py / gen_dsl_corpus_header.py: no
JSON parser and no filesystem on the MCU.

Parity domain: exact at a BAKED atlas size (scale == 1, integer advances).  The
chat body font is font_size 12 (a baked atlas size); the CYD atlas is generated
from the SAME IBM Plex faces/settings, so these advances ARE the CYD's at
scale == 1.  All chars are ASCII 32-126 (the atlas coverage).

REGENERATE after any change to _layout_body/_wrap_runs or the case battery:
    python3 python/scripts/gen_chat_wrap_corpus_header.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))            # composeio/
sys.path.insert(0, os.path.join(REPO, "python"))

from composeio.render.ft_text import get_engine                      # noqa: E402
from composeio.render.canvas import Size                             # noqa: E402
from composeio.render.widgets.chat_thread import ChatThreadWidget    # noqa: E402

OUT = os.path.join(REPO, "cyd", "test", "chat_wrap_corpus_generated.h")

# The chat body size == chat_thread.py default font_size (a baked atlas size,
# scale == 1 -> integer advances identical to the CYD atlas).
BODY_SIZE = 12
FAMILY = "sans-serif"
WEIGHT = "regular"


class _M:
    def __init__(self, w):
        self.width = w
        self.ascent = 11.0
        self.descent = 3.0


class MockCanvas:
    """A canvas whose measure_text sums the engine's FreeType per-glyph advances
    at the CURRENT font size — the exact advances the CYD atlas holds at the
    baked size (scale == 1).  chat_thread._layout_body / _wrap_runs read nothing
    else off the canvas for a plain-prose body."""

    def __init__(self):
        self._eng = get_engine()
        self._size = BODY_SIZE

    def set_font(self, family, size, weight="regular"):
        self._size = int(round(size))

    def measure_text(self, text):
        w = 0.0
        for ch in text:
            g = self._eng.glyph(FAMILY, WEIGHT, self._size, ch)
            w += (g.advance if g else 0)
        return _M(w)


def advances(shown: str) -> dict:
    eng = get_engine()
    cmap = {}
    for ch in shown:
        g = eng.glyph(FAMILY, WEIGHT, BODY_SIZE, ch)
        if g is None:
            raise SystemExit(f"no glyph for {ch!r}")
        a = int(g.advance)
        if ch in cmap and cmap[ch] != a:
            raise SystemExit(f"char {ch!r} has two advances")
        cmap[ch] = a
    return cmap


def oracle_lines(text: str, body_w: float) -> list:
    """The DESKTOP widget's OWN wrap for a plain body: _layout_body -> the
    rendered per-line plain text (runs joined), rstripped exactly as the CYD
    chat_thread.cpp draws each line."""
    w = ChatThreadWidget("corpus")
    c = MockCanvas()
    vlines = w._layout_body(c, text, body_w, float(BODY_SIZE))
    out = []
    for vl in vlines:
        s = "".join(r.text for r in vl.runs)
        out.append(s.rstrip(" "))
    return out


def build_cases() -> list:
    # (id, body_text, body_w).  Bodies are PLAIN prose (the band tier's
    # domain); the widths span a classic-CYD full bubble (body_w ~= 193) to a
    # wider panel.  These bodies are shared with the parity config
    # render/examples/chat_thread_parity.compose.yaml.
    raw = [
        ("reply_wide",
         "Both CYD substrates are landed and proven: text_wrap and scroll_list.",
         300.0),
        ("reply_narrow",
         "Both CYD substrates are landed and proven: text_wrap and scroll_list.",
         193.5),
        ("david_q",
         "Nice work. Does the wrap line-break match the desktop widget exactly?",
         193.5),
        ("narration",
         "checking the corpus: text_wrap equals wrap_lines, and layout_body "
         "equals wrap_lines for plain prose, so the break is transitive.",
         193.5),
        ("url",
         "see https://mqtt-broker.local:8420/assets/displays for the config",
         193.5),
        ("longword",
         "supercalifragilisticexpialidocious antidisestablishmentarianism ok",
         120.0),
        ("harness_report",
         "READY fuel=42 charge=60 systems nominal OK to launch on your mark",
         160.0),
        ("short",
         "ack", 193.5),
        ("exact_col",
         "one two three four five six seven eight nine ten", 100.0),
        ("exact_col_wide",
         "one two three four five six seven eight nine ten", 140.0),
        ("multi_space",
         "alpha   beta gamma delta epsilon zeta eta theta", 120.0),
        ("trailing_space",
         "commit and push the branch   ", 110.0),
        ("hardbreak_then_word",
         "verylongunbrokentokenxyz tail words follow here", 90.0),
        ("tight",
         "a bb ccc dddd eeeee ffffff ggggggg", 60.0),
    ]
    cases = []
    for cid, text, bw in raw:
        cases.append({
            "id": cid,
            "shown": text,
            "avail": float(bw),
            "cmap": advances(text),
            "lines": oracle_lines(text, bw),
        })
    return cases


def cstr(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif 32 <= o < 127:
            out.append(ch)
        else:
            out.append("\\%03o" % o)
    out.append('"')
    return "".join(out)


def fadv(a) -> str:
    return "%.1ff" % float(a)


def main() -> int:
    cases = build_cases()
    L = []
    w = L.append
    w("/*")
    w(" * chat_wrap_corpus_generated.h — AUTO-GENERATED. DO NOT HAND-EDIT.")
    w(" *")
    w(" * Generated by python/scripts/gen_chat_wrap_corpus_header.py.  The")
    w(" * ORACLE is the DESKTOP chat widget's OWN wrapper")
    w(" * ChatThreadWidget._layout_body (composeio/render/widgets/")
    w(" * chat_thread.py); the expected value is each RENDERED line's plain")
    w(" * text (rstripped as chat_thread.cpp draws it).  The host harness")
    w(" * cyd/src/chat_wrap_selftest.cpp drives the SAME bodies + advances")
    w(" * through the CYD text_wrap path and matches each line string — so a")
    w(" * bubble wraps byte-identically on the desktop and the ESP32.")
    w(" *")
    w(f" * {len(cases)} cases.  Advances are real IBM Plex advances at the body")
    w(f" * atlas size {BODY_SIZE}px (scale==1).  REGENERATE after any wrap")
    w(" * change:  python3 python/scripts/gen_chat_wrap_corpus_header.py")
    w(" *")
    w(" * All data is static const -> flash (.rodata); no JSON / filesystem.")
    w(" */")
    w("")
    w("#ifndef CHAT_WRAP_CORPUS_GENERATED_H")
    w("#define CHAT_WRAP_CORPUS_GENERATED_H")
    w("")
    w("struct ChatWrapGlyph { char ch; float advance; };")
    w("")
    w("struct ChatWrapCase {")
    w("    const char* id;")
    w("    const char* shown;")
    w("    int shown_len;")
    w("    float avail;")
    w("    const ChatWrapGlyph* glyphs;")
    w("    int glyph_count;")
    w("    const char* const* lines;")
    w("    int line_count;")
    w("};")
    w("")

    for i, c in enumerate(cases):
        w(f"static const ChatWrapGlyph k_cw{i}_glyphs[] = {{")
        for ch, a in c["cmap"].items():
            w(f"    {{{cstr(ch)}[0], {fadv(a)}}},")
        w("};")
        w(f"static const char* const k_cw{i}_lines[] = {{")
        for ln in c["lines"]:
            w(f"    {cstr(ln)},")
        w("};")
    w("")

    w("static const ChatWrapCase kChatWrapCases[] = {")
    for i, c in enumerate(cases):
        w("    {%s, %s, %d, %sf, k_cw%d_glyphs, %d, k_cw%d_lines, %d},"
          % (cstr(c["id"]), cstr(c["shown"]), len(c["shown"]),
             repr(c["avail"]), i, len(c["cmap"]), i, len(c["lines"])))
    w("};")
    w("")
    w(f"static const int kChatWrapCaseCount = {len(cases)};")
    w("")
    w("#endif /* CHAT_WRAP_CORPUS_GENERATED_H */")
    w("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"wrote {OUT} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
