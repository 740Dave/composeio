#!/usr/bin/env python3
"""
gen_wrap_corpus_header.py — text word-wrap parity corpus -> C++ header
([CXC-R-ENGINE-TEXT], the CYD text-wrap port).

The Python engine's canonical soft-wrap substrate is
``MultiLineEditMixin._wrap_lines`` (composeio/render/widgets/multiline_text.py):
a greedy word-wrap computed over ``xs`` — the cumulative per-glyph advances
the SAME ``measure_text`` path produces (composeio/render/widgets/
text_input.py ``_prefix_widths``).  Wrap, caret placement and hit-testing all
read that ONE advance table, so a line break can never disagree with the
rendered advance.

The CYD band renderer must make the IDENTICAL line-break decisions or text
forks per tier (a wrap that disagrees with the glyph advance the CYD actually
draws with — the exact hazard).  This script makes the Python ``_wrap_lines``
the ORACLE: it runs a battery of (shown string, per-glyph advances, box width)
cases through it and emits the expected line spans as a self-contained C++
header

    cyd/test/wrap_corpus_generated.h

which the host harness ``cyd/src/text_wrap_selftest.cpp`` matches bit-for-bit
against the C++ port ``cyd/src/text_wrap.cpp``.  Same corpus idiom as the DSL
(gen_dsl_corpus_header.py): no JSON parser and no filesystem on the MCU.

Two advance sources are exercised:
  * REALISTIC — the real IBM Plex advances from the engine's own FreeType
    rasterizer (composeio/render/ft_text.py) at a BAKED atlas size.  Because
    the CYD atlas is generated from the SAME faces with the SAME settings
    (cyd/fonts/generate_atlas.py), these integer advances ARE the advances the
    CYD holds at scale == 1.  So the corpus is measurement-real, not just
    algorithm-real.
  * SYNTHETIC — uniform / hand-picked advances that pin the edge rules
    (exact-fit boundary, over-wide single word hard-break, trailing / multiple
    spaces, empty string, avail <= 0, single char).

Parity domain: exact when the CYD renders at a baked atlas size (scale == 1,
integer advances).  At a non-baked size the CYD scales atlas advances by a
float and the break decisions are an approximation of Skia's — documented, not
claimed.  All corpus chars are ASCII 32-126 (the CYD atlas coverage), so the
missing-glyph fallback path is never taken.

REGENERATE after any change to _wrap_lines or the case battery:
    python3 python/scripts/gen_wrap_corpus_header.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))            # composeio/
sys.path.insert(0, os.path.join(REPO, "python"))

from composeio.render.ft_text import get_engine                      # noqa: E402
from composeio.render.widgets.multiline_text import (                # noqa: E402
    MultiLineEditMixin,
)

OUT = os.path.join(REPO, "cyd", "test", "wrap_corpus_generated.h")

# The baked atlas size the parity is exact at (scale == 1).  Any of the atlas
# regular sizes {6,7,8,10,12,14,16} works; 14 is the body size.
BAKED_SIZE = 14
FAMILY = "sans-serif"
WEIGHT = "regular"


def real_advances(shown: str) -> list:
    """Per-glyph integer advances from the engine's FreeType rasterizer at the
    baked size — identical to the CYD atlas advances (same faces/settings)."""
    eng = get_engine()
    out = []
    for ch in shown:
        g = eng.glyph(FAMILY, WEIGHT, BAKED_SIZE, ch)
        if g is None:
            raise SystemExit(f"no glyph for {ch!r} (family {FAMILY})")
        out.append(int(g.advance))
    return out


def uniform_advances(shown: str, adv: int) -> list:
    return [adv] * len(shown)


def cumulative(advances: list) -> list:
    """xs table: len == len(advances)+1, xs[0] == 0 (matches _prefix_widths)."""
    xs = [0.0]
    acc = 0.0
    for a in advances:
        acc += float(a)
        xs.append(acc)
    return xs


def make_case(cid: str, shown: str, advances: list, avail: float) -> dict:
    """Run the Python ORACLE (_wrap_lines) and capture the expected spans."""
    if len(advances) != len(shown):
        raise SystemExit(f"{cid}: advance count != string length")
    # Per-char advance map for the C++ MockCanvas measure path.  A real font
    # (and a uniform synthetic) maps each char to ONE advance; assert it.
    cmap: dict = {}
    for ch, a in zip(shown, advances):
        if ch in cmap and cmap[ch] != a:
            raise SystemExit(f"{cid}: char {ch!r} has two advances "
                             f"{cmap[ch]} vs {a} — not MockCanvas-testable")
        cmap[ch] = a
    xs = cumulative(advances)
    spans = MultiLineEditMixin._wrap_lines(xs, shown, avail)
    return {"id": cid, "shown": shown, "avail": float(avail),
            "cmap": cmap, "spans": spans}


def build_cases() -> list:
    cases = []

    # -- realistic (real IBM Plex advances at the baked size) ---------------
    realistic = [
        ("real_pangram_60", "The quick brown fox jumps over the lazy dog", 60),
        ("real_pangram_100", "The quick brown fox jumps over the lazy dog", 100),
        ("real_pangram_37", "The quick brown fox jumps over the lazy dog", 37),
        ("real_sentence", "Bus offline; retrying connection now.", 80),
        ("real_longword", "supercalifragilisticexpialidocious", 70),
        ("real_url", "https://mqtt-broker.local:8420/assets", 90),
        ("real_mixed", "READY fuel=42 charge=60 OK to launch", 64),
        ("real_narrow", "alerts pending review", 24),
        ("real_wide", "alerts pending review", 400),
        ("real_trailing_spaces", "alpha beta   gamma", 44),
    ]
    for cid, shown, avail in realistic:
        cases.append(make_case(cid, shown, real_advances(shown), avail))

    # -- synthetic (uniform advance pins the edge rules) --------------------
    # advance 8 per glyph -> boundaries land on multiples of 8.
    u = uniform_advances
    cases.append(make_case("syn_empty", "", [], 100))
    cases.append(make_case("syn_avail_zero", "abc", u("abc", 8), 0))
    cases.append(make_case("syn_avail_neg", "abc", u("abc", 8), -5))
    cases.append(make_case("syn_one_char", "a", u("a", 8), 100))
    # exact fit: "ab cd" widths 8 each incl space (space=8 here) -> avail 24
    # is exactly "ab " (24) then break.
    cases.append(make_case("syn_exact_fit", "ab cd ef", u("ab cd ef", 8), 24))
    cases.append(make_case("syn_off_by_one", "ab cd ef", u("ab cd ef", 8), 23))
    # over-wide single word -> per-glyph hard break (no space to back to).
    cases.append(make_case("syn_hardbreak", "abcdefghij", u("abcdefghij", 8), 30))
    # word longer than line AFTER a fitting word.
    cases.append(make_case("syn_word_then_long", "hi abcdefghij",
                           u("hi abcdefghij", 8), 30))
    # multiple consecutive spaces (break backs to the LAST space).
    cases.append(make_case("syn_multi_space", "aa   bb", u("aa   bb", 8), 40))
    cases.append(make_case("syn_all_spaces", "     ", u("     ", 8), 24))
    cases.append(make_case("syn_trailing_space_kept", "aa bb ",
                           u("aa bb ", 8), 24))
    # leading space.
    cases.append(make_case("syn_leading_space", " ab cd", u(" ab cd", 8), 24))
    # every char over-wide (single-glyph line each).
    cases.append(make_case("syn_all_overwide", "abcd", u("abcd", 20), 10))
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
    """Advance as an exact float literal (integers -> N.0f)."""
    return "%.1ff" % float(a)


def main() -> int:
    cases = build_cases()
    L = []
    w = L.append
    w("/*")
    w(" * wrap_corpus_generated.h — AUTO-GENERATED. DO NOT HAND-EDIT.")
    w(" *")
    w(" * Generated by python/scripts/gen_wrap_corpus_header.py from the")
    w(" * Python word-wrap ORACLE MultiLineEditMixin._wrap_lines")
    w(" * (composeio/render/widgets/multiline_text.py).")
    w(" *")
    w(f" * {len(cases)} cases.  Advances are real IBM Plex advances at the")
    w(f" * baked atlas size {BAKED_SIZE}px (scale==1) plus synthetic edge")
    w(" * cases.  REGENERATE after any wrap change:")
    w(" *   python3 python/scripts/gen_wrap_corpus_header.py")
    w(" *")
    w(" * All data is static const -> flash (.rodata) on the ESP32; no JSON")
    w(" * parser and no filesystem needed on the MCU.")
    w(" */")
    w("")
    w("#ifndef WRAP_CORPUS_GENERATED_H")
    w("#define WRAP_CORPUS_GENERATED_H")
    w("")
    w("struct WrapSpan { int start; int end; };")
    w("")
    w("/* Per-char advance map: the MockCanvas returns g->advance for a single")
    w(" * char, exactly as ESP32Canvas::measure_text does at scale==1. */")
    w("struct WrapGlyph { char ch; float advance; };")
    w("")
    w("struct WrapCase {")
    w("    const char* id;")
    w("    const char* shown;")
    w("    int shown_len;")
    w("    float avail;")
    w("    const WrapGlyph* glyphs;")
    w("    int glyph_count;")
    w("    const WrapSpan* expected;")
    w("    int expected_count;")
    w("};")
    w("")

    for i, c in enumerate(cases):
        # glyph (char->advance) table
        if c["cmap"]:
            w(f"static const WrapGlyph k_wrap{i}_glyphs[] = {{")
            for ch, a in c["cmap"].items():
                w(f"    {{{cstr(ch)}[0], {fadv(a)}}},")
            w("};")
        # expected spans
        w(f"static const WrapSpan k_wrap{i}_expected[] = {{")
        for (s, e) in c["spans"]:
            w(f"    {{{s}, {e}}},")
        w("};")
    w("")

    w("static const WrapCase kWrapCases[] = {")
    for i, c in enumerate(cases):
        g_ref = f"k_wrap{i}_glyphs" if c["cmap"] else "nullptr"
        w("    {%s, %s, %d, %sf,\n"
          "     %s, %d, %s, %d},"
          % (cstr(c["id"]), cstr(c["shown"]), len(c["shown"]),
             repr(c["avail"]),
             g_ref, len(c["cmap"]),
             f"k_wrap{i}_expected", len(c["spans"])))
    w("};")
    w("")
    w(f"static const int kWrapCaseCount = {len(cases)};")
    w("")
    w("#endif /* WRAP_CORPUS_GENERATED_H */")
    w("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"wrote {OUT} ({len(cases)} cases)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
