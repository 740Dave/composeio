#!/usr/bin/env python3
"""
Font Atlas Generator for ComposeUX Embedded Display

Renders a TTF font at specified sizes into a C header file containing:
- uint8_t glyph bitmaps (8-bit alpha, grayscale antialiased)
- Glyph metrics (advance, bearing_x, bearing_y, width, height)
- Character-to-glyph lookup table
- Font-level metrics (ascent, descent, line_height)

Deterministic settings: no autohinting, grayscale AA, 72 DPI.

Usage:
    python generate_atlas.py <ttf_path> [--sizes 12 14 16] [--output font_atlas.h]

Output is placed in the src/ directory of the CYD project so ESP-IDF
picks it up as an includable header.

Requirements:
    pip install freetype-py
"""

import argparse
import json
import struct
import sys
from pathlib import Path

try:
    import freetype
except ImportError:
    print("ERROR: freetype-py not installed. Run: pip install freetype-py")
    sys.exit(1)


# ASCII printable range
FIRST_CHAR = 32  # space
LAST_CHAR = 126  # tilde
NUM_CHARS = LAST_CHAR - FIRST_CHAR + 1

# DPI for rendering — standard screen DPI
DPI = 72


def render_font_size(face: freetype.Face, size: int) -> dict:
    """Render all glyphs at the given pixel size and return atlas data."""
    face.set_char_size(0, size * 64, DPI, DPI)

    # Get font-level metrics (in 26.6 fixed point)
    metrics = face.size
    ascent = metrics.ascender >> 6
    descent = -(metrics.descender >> 6)  # Make positive
    line_height = metrics.height >> 6

    glyphs = []

    for char_code in range(FIRST_CHAR, LAST_CHAR + 1):
        char = chr(char_code)

        # Load glyph with grayscale rendering.
        # At very small sizes (<8px), TrueType hinting can cause division-by-zero
        # in the bytecode interpreter, so we disable hinting entirely.
        # At normal sizes we disable only autohinting (use font's built-in hints).
        if size < 8:
            load_flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING
        else:
            load_flags = freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_AUTOHINT
        face.load_char(char, load_flags)

        glyph = face.glyph
        bitmap = glyph.bitmap

        width = bitmap.width
        height = bitmap.rows
        bearing_x = glyph.bitmap_left
        bearing_y = glyph.bitmap_top
        advance = glyph.advance.x >> 6  # 26.6 to integer

        # Copy bitmap data (8-bit alpha)
        if width > 0 and height > 0:
            # freetype bitmap buffer may have pitch != width
            pitch = bitmap.pitch
            pixel_data = []
            buf = bitmap.buffer
            for row in range(height):
                row_start = row * pitch
                for col in range(width):
                    pixel_data.append(buf[row_start + col])
        else:
            pixel_data = []

        glyphs.append({
            "char_code": char_code,
            "width": width,
            "height": height,
            "bearing_x": bearing_x,
            "bearing_y": bearing_y,
            "advance": advance,
            "bitmap": pixel_data,
        })

    return {
        "size": size,
        "ascent": ascent,
        "descent": descent,
        "line_height": line_height,
        "glyphs": glyphs,
    }


def find_default_font() -> str:
    """Try to find a bundled or system TTF font."""
    candidates = [
        # Local project font
        Path(__file__).parent / "Inter-Regular.ttf",
        Path(__file__).parent / "NotoSans-Regular.ttf",
        # System fonts (macOS)
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/System/Library/Fonts/SFNSDisplay.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        # System fonts (Linux)
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
        Path("/usr/share/fonts/TTF/DejaVuSans.ttf"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def find_bold_face_index(ttf_path: str) -> int:
    """Auto-detect the bold face index in a TTC collection.

    Iterates face indices looking for one with 'Bold' in style_name
    (but not 'Oblique' or 'Italic'). Returns -1 if not found.
    """
    for i in range(20):
        try:
            face = freetype.Face(ttf_path, i)
            style = face.style_name
            if isinstance(style, bytes):
                style = style.decode("utf-8", errors="replace")
            if "Bold" in style and "Oblique" not in style and "Italic" not in style:
                return i
        except Exception:
            break
    return -1


def generate_c_header_dual(regular_data: list, bold_data: list, output_path: Path,
                           commp_data: list = None) -> None:
    """Generate a C header with regular + bold (sans-serif default family)
    and, when provided, a named COMMP ("Common Pixel") family."""
    commp_data = commp_data or []
    lines = []
    lines.append("/*")
    lines.append(" * font_atlas.h — Generated font atlas for ComposeUX embedded display")
    lines.append(" *")
    lines.append(" * AUTO-GENERATED by compose_resources/fonts/generate_atlas.py — do not edit manually.")
    lines.append(" *")
    lines.append(f" * Regular sizes: {', '.join(str(d['size']) for d in regular_data)}px")
    lines.append(f" * Bold sizes: {', '.join(str(d['size']) for d in bold_data)}px")
    if commp_data:
        lines.append(f" * COMMP (Common Pixel) sizes: {', '.join(str(d['size']) for d in commp_data)}px")
    lines.append(f" * Characters: ASCII {FIRST_CHAR}-{LAST_CHAR} ({NUM_CHARS} glyphs per size)")
    lines.append(" * Format: 8-bit alpha bitmaps, grayscale antialiased, 72 DPI")
    lines.append(" */")
    lines.append("")
    lines.append("#ifndef FONT_ATLAS_H")
    lines.append("#define FONT_ATLAS_H")
    lines.append("")
    lines.append("#include <cstdint>")
    lines.append("")
    lines.append("/* Glyph descriptor */")
    lines.append("struct FontGlyph {")
    lines.append("    const uint8_t* bitmap;   /* 8-bit alpha bitmap (width * height bytes) */")
    lines.append("    int16_t width;            /* Bitmap width in pixels */")
    lines.append("    int16_t height;           /* Bitmap height in pixels */")
    lines.append("    int16_t bearing_x;        /* Left bearing */")
    lines.append("    int16_t bearing_y;        /* Top bearing (from baseline) */")
    lines.append("    int16_t advance;          /* Horizontal advance */")
    lines.append("};")
    lines.append("")
    lines.append("/* Font atlas for a specific size */")
    lines.append("struct FontAtlas {")
    lines.append("    int size;                 /* Font size in pixels */")
    lines.append("    int ascent;               /* Distance from baseline to top */")
    lines.append("    int descent;              /* Distance from baseline to bottom */")
    lines.append("    int line_height;          /* Total line height */")
    lines.append(f"    FontGlyph glyphs[{NUM_CHARS}];  /* ASCII {FIRST_CHAR}-{LAST_CHAR} */")
    lines.append("};")
    lines.append("")

    def emit_weight(font_data_list, prefix, label=None):
        """Emit bitmap arrays and atlas structs for a weight/family
        (prefix = '' | 'bold_' | 'commp_'). *label* is cosmetic."""
        if label is None:
            label = "bold" if prefix else "regular"
        for fdata in font_data_list:
            sz = fdata["size"]
            lines.append(f"/* ---- Font size {sz}px {label} ---- */")
            lines.append("")

            for glyph in fdata["glyphs"]:
                cc = glyph["char_code"]
                w = glyph["width"]
                h = glyph["height"]
                if w == 0 or h == 0:
                    continue

                char_repr = chr(cc) if 33 <= cc <= 126 else f"0x{cc:02X}"
                lines.append(f"static const uint8_t font_{prefix}{sz}_glyph_{cc}[] = {{ /* '{char_repr}' {w}x{h} */")

                bitmap = glyph["bitmap"]
                for row_start in range(0, len(bitmap), 16):
                    chunk = bitmap[row_start:row_start + 16]
                    hex_vals = ",".join(f"0x{v:02X}" for v in chunk)
                    comma = "," if row_start + 16 < len(bitmap) else ""
                    lines.append(f"    {hex_vals}{comma}")

                lines.append("};")
                lines.append("")

        for fdata in font_data_list:
            sz = fdata["size"]
            lines.append(f"static const FontAtlas font_atlas_{prefix}{sz} = {{")
            lines.append(f"    .size = {sz},")
            lines.append(f"    .ascent = {fdata['ascent']},")
            lines.append(f"    .descent = {fdata['descent']},")
            lines.append(f"    .line_height = {fdata['line_height']},")
            lines.append(f"    .glyphs = {{")

            for glyph in fdata["glyphs"]:
                cc = glyph["char_code"]
                w = glyph["width"]
                h = glyph["height"]
                bx = glyph["bearing_x"]
                by = glyph["bearing_y"]
                adv = glyph["advance"]

                if w == 0 or h == 0:
                    lines.append(f"        /* {cc} */ {{nullptr, 0, 0, {bx}, {by}, {adv}}},")
                else:
                    lines.append(f"        /* {cc} */ {{font_{prefix}{sz}_glyph_{cc}, {w}, {h}, {bx}, {by}, {adv}}},")

            lines.append("    }")
            lines.append("};")
            lines.append("")

    # Emit regular weight (no prefix for backwards compat)
    emit_weight(regular_data, "")

    # Regular lookup table
    num_regular = len(regular_data)
    lines.append(f"static const FontAtlas* const font_atlases[] = {{")
    for fdata in regular_data:
        lines.append(f"    &font_atlas_{fdata['size']},")
    lines.append("};")
    lines.append(f"static constexpr int kNumFontAtlases = {num_regular};")
    lines.append("")

    lines.append("/* Find the best-matching font atlas for the requested size */")
    lines.append("static inline const FontAtlas* font_atlas_find(int target_size) {")
    lines.append("    if (kNumFontAtlases == 0) return nullptr;")
    lines.append("    const FontAtlas* best = font_atlases[0];")
    lines.append("    int best_diff = target_size > best->size ? target_size - best->size : best->size - target_size;")
    lines.append("    for (int i = 1; i < kNumFontAtlases; i++) {")
    lines.append("        int diff = target_size > font_atlases[i]->size ? target_size - font_atlases[i]->size : font_atlases[i]->size - target_size;")
    lines.append("        if (diff < best_diff) { best = font_atlases[i]; best_diff = diff; }")
    lines.append("    }")
    lines.append("    return best;")
    lines.append("}")
    lines.append("")

    # Emit bold weight (prefix = "bold_")
    emit_weight(bold_data, "bold_")

    # Bold lookup table
    num_bold = len(bold_data)
    lines.append(f"static const FontAtlas* const font_atlases_bold[] = {{")
    for fdata in bold_data:
        lines.append(f"    &font_atlas_bold_{fdata['size']},")
    lines.append("};")
    lines.append(f"static constexpr int kNumFontAtlasesBold = {num_bold};")
    lines.append("")

    lines.append("/* Find the best-matching bold font atlas for the requested size */")
    lines.append("static inline const FontAtlas* font_atlas_find_bold(int target_size) {")
    lines.append("    if (kNumFontAtlasesBold == 0) return nullptr;")
    lines.append("    const FontAtlas* best = font_atlases_bold[0];")
    lines.append("    int best_diff = target_size > best->size ? target_size - best->size : best->size - target_size;")
    lines.append("    for (int i = 1; i < kNumFontAtlasesBold; i++) {")
    lines.append("        int diff = target_size > font_atlases_bold[i]->size ? target_size - font_atlases_bold[i]->size : font_atlases_bold[i]->size - target_size;")
    lines.append("        if (diff < best_diff) { best = font_atlases_bold[i]; best_diff = diff; }")
    lines.append("    }")
    lines.append("    return best;")
    lines.append("}")
    lines.append("")

    # -------- COMMP ("Common Pixel") named family --------
    # A pixel font: single weight (bold selection maps to the same face).
    if commp_data:
        emit_weight(commp_data, "commp_", label="COMMP")
        num_commp = len(commp_data)
        lines.append(f"static const FontAtlas* const font_atlases_commp[] = {{")
        for fdata in commp_data:
            lines.append(f"    &font_atlas_commp_{fdata['size']},")
        lines.append("};")
        lines.append(f"static constexpr int kNumFontAtlasesCommp = {num_commp};")
        lines.append("")
        lines.append("/* Find the best-matching COMMP atlas for the requested size */")
        lines.append("static inline const FontAtlas* font_atlas_find_commp(int target_size) {")
        lines.append("    if (kNumFontAtlasesCommp == 0) return nullptr;")
        lines.append("    const FontAtlas* best = font_atlases_commp[0];")
        lines.append("    int best_diff = target_size > best->size ? target_size - best->size : best->size - target_size;")
        lines.append("    for (int i = 1; i < kNumFontAtlasesCommp; i++) {")
        lines.append("        int diff = target_size > font_atlases_commp[i]->size ? target_size - font_atlases_commp[i]->size : font_atlases_commp[i]->size - target_size;")
        lines.append("        if (diff < best_diff) { best = font_atlases_commp[i]; best_diff = diff; }")
        lines.append("    }")
        lines.append("    return best;")
        lines.append("}")
        lines.append("")
    else:
        # No COMMP data — emit a stub so callers compile (falls back to sans).
        lines.append("static constexpr int kNumFontAtlasesCommp = 0;")
        lines.append("static inline const FontAtlas* font_atlas_find_commp(int) { return nullptr; }")
        lines.append("")

    # -------- Family dispatcher --------
    lines.append("/* Case-insensitive family match: 'commp' / 'common pixel' -> the")
    lines.append(" * COMMP pixel family; anything else -> the default sans-serif family. */")
    lines.append("static inline bool font_family_is_commp(const char* family) {")
    lines.append("    if (!family) return false;")
    lines.append("    auto lc = [](char c) -> char {")
    lines.append("        return (c >= 'A' && c <= 'Z') ? (char)(c - 'A' + 'a') : c; };")
    lines.append("    auto ieq = [&](const char* s, const char* t) -> bool {")
    lines.append("        while (*s && *t) { if (lc(*s) != lc(*t)) return false; s++; t++; }")
    lines.append("        return *s == *t; };")
    lines.append("    return ieq(family, \"commp\") || ieq(family, \"common pixel\");")
    lines.append("}")
    lines.append("")
    lines.append("/* Family + size finder (regular weight of the named family). */")
    lines.append("static inline const FontAtlas* font_atlas_find_family(const char* family, int target_size) {")
    lines.append("    if (font_family_is_commp(family)) {")
    lines.append("        const FontAtlas* a = font_atlas_find_commp(target_size);")
    lines.append("        if (a) return a;   /* fall through to sans if COMMP absent */")
    lines.append("    }")
    lines.append("    return font_atlas_find(target_size);")
    lines.append("}")
    lines.append("")
    lines.append("/* Family + weight + size selector (the canvas seam): COMMP is a")
    lines.append(" * single-weight pixel family (bold maps to the same face); the")
    lines.append(" * default family honours the bold weight. */")
    lines.append("static inline const FontAtlas* font_atlas_find_styled(bool commp, bool bold, int target_size) {")
    lines.append("    if (commp) { const FontAtlas* a = font_atlas_find_commp(target_size); if (a) return a; }")
    lines.append("    return bold ? font_atlas_find_bold(target_size) : font_atlas_find(target_size);")
    lines.append("}")
    lines.append("")

    lines.append("/* Get glyph for a character from an atlas */")
    lines.append("static inline const FontGlyph* font_atlas_glyph(const FontAtlas* atlas, int char_code) {")
    lines.append(f"    if (char_code < {FIRST_CHAR} || char_code > {LAST_CHAR}) return nullptr;")
    lines.append(f"    return &atlas->glyphs[char_code - {FIRST_CHAR}];")
    lines.append("}")
    lines.append("")
    lines.append("#endif /* FONT_ATLAS_H */")

    output_path.write_text("\n".join(lines) + "\n")
    total_bytes = sum(
        sum(len(g["bitmap"]) for g in fd["glyphs"])
        for fd in regular_data + bold_data + commp_data
    )
    print(f"Generated {output_path} ({len(lines)} lines, ~{total_bytes} bytes of bitmap data)")


def generate_json_atlas_dual(regular_data: list, bold_data: list, font_source: str, output_path: Path,
                             commp_data: list = None) -> None:
    """Generate a JSON font atlas with regular, bold and COMMP groups.

    [CXC-CYD-FONT-EXPORT] requirement-every-family: every group this run emits
    to the C header MUST also reach the JSON.  Before this parameter existed
    the caller passed `commp_data` to `generate_c_header_dual` and NOT here,
    so one invocation wrote two files with the same timestamp and DIFFERENT
    family sets -- a quieter version of the two-run skew
    [CXC-CYD-FONT-EXPORT-REGEN] warns about, and the reason `font_family:
    commp` had no desktop realization to fall back to.
    """
    commp_data = commp_data or []

    def build_sizes_list(font_data_list):
        sizes = []
        for fdata in font_data_list:
            size_entry = {
                "size": fdata["size"],
                "ascent": fdata["ascent"],
                "descent": fdata["descent"],
                "line_height": fdata["line_height"],
                "glyphs": [],
            }
            for glyph in fdata["glyphs"]:
                bitmap_hex = ""
                if glyph["bitmap"]:
                    bitmap_hex = "".join(f"{b:02X}" for b in glyph["bitmap"])
                size_entry["glyphs"].append({
                    "char_code": glyph["char_code"],
                    "width": glyph["width"],
                    "height": glyph["height"],
                    "bearing_x": glyph["bearing_x"],
                    "bearing_y": glyph["bearing_y"],
                    "advance": glyph["advance"],
                    "bitmap_hex": bitmap_hex,
                })
            sizes.append(size_entry)
        return sizes

    atlas = {
        "generator": "generate_atlas.py",
        "font_source": font_source,
        "dpi": DPI,
        "char_range": [FIRST_CHAR, LAST_CHAR],
        "regular": {"sizes": build_sizes_list(regular_data)},
        "bold": {"sizes": build_sizes_list(bold_data)},
    }
    if commp_data:
        atlas["commp"] = {"sizes": build_sizes_list(commp_data)}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(atlas, indent=2) + "\n")
    total_glyphs = sum(
        len(s["glyphs"])
        for group in (atlas["regular"], atlas["bold"],
                      atlas.get("commp", {"sizes": []}))
        for s in group["sizes"]
    )
    print(f"Generated {output_path} ({total_glyphs} total glyphs)")


def main():
    parser = argparse.ArgumentParser(
        description="Generate font atlas C header and JSON for ComposeUX"
    )
    parser.add_argument(
        "ttf", nargs="?", default=None,
        help="Path to TTF font file (auto-detected if omitted)"
    )
    parser.add_argument(
        "--bold-ttf", default=None,
        help="Path to bold TTF font file. If omitted, auto-detect bold face in TTC collections."
    )
    parser.add_argument(
        "--sizes", nargs="+", type=int, default=[6, 7, 8, 10, 12, 14, 16],
        help="Font sizes to generate (default: 6 7 8 10 12 14 16)"
    )
    parser.add_argument(
        "--commp-ttf", default=None,
        help="Path to the COMMP (Common Pixel) TTF (default: compose_resources/fonts/COMMP___.TTF)"
    )
    parser.add_argument(
        "--commp-sizes", nargs="+", type=int, default=[12, 14, 16, 20, 24],
        help="COMMP family sizes (default: 12 14 16 20 24)"
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output header path (default: ../src/font_atlas.h)"
    )
    parser.add_argument(
        "--json-output", default=None,
        help="Output JSON path (default: ../../python/composeio/render/font_atlas_data.json)"
    )
    args = parser.parse_args()

    ttf_path = args.ttf
    if ttf_path is None:
        ttf_path = find_default_font()
        if ttf_path is None:
            print("ERROR: No TTF font found. Provide one as argument or place")
            print("       Inter-Regular.ttf in the compose_resources/fonts/ directory.")
            sys.exit(1)
    print(f"Using font: {ttf_path}")

    output_path = args.output
    if output_path is None:
        output_path = Path(__file__).parent.parent / "src" / "font_atlas.h"
    else:
        output_path = Path(output_path)

    json_output_path = args.json_output
    if json_output_path is None:
        json_output_path = (
            Path(__file__).parent.parent.parent
            / "python" / "composeio" / "render" / "font_atlas_data.json"
        )
    else:
        json_output_path = Path(json_output_path)

    # Load regular face (index 0 for TTC, or the only face for TTF)
    regular_face = freetype.Face(ttf_path, 0)
    style = regular_face.style_name
    if isinstance(style, bytes):
        style = style.decode("utf-8", errors="replace")
    print(f"  Regular face: {style}")

    # Load bold face
    bold_face = None
    if args.bold_ttf:
        bold_face = freetype.Face(args.bold_ttf)
        bold_style = bold_face.style_name
        if isinstance(bold_style, bytes):
            bold_style = bold_style.decode("utf-8", errors="replace")
        print(f"  Bold face (explicit): {bold_style}")
    else:
        # Auto-detect bold face in TTC collection
        bold_index = find_bold_face_index(ttf_path)
        if bold_index >= 0:
            bold_face = freetype.Face(ttf_path, bold_index)
            bold_style = bold_face.style_name
            if isinstance(bold_style, bytes):
                bold_style = bold_style.decode("utf-8", errors="replace")
            print(f"  Bold face (auto-detected index {bold_index}): {bold_style}")
        else:
            print("  WARNING: No bold face found. Bold atlas will use regular face.")
            bold_face = regular_face

    # Render regular weight
    regular_data = []
    for size in sorted(args.sizes):
        print(f"Rendering regular {size}px...")
        data = render_font_size(regular_face, size)
        regular_data.append(data)
        total_glyphs = sum(1 for g in data["glyphs"] if g["width"] > 0)
        print(f"  {total_glyphs} non-empty glyphs, "
              f"ascent={data['ascent']}, descent={data['descent']}, "
              f"line_height={data['line_height']}")

    # Render bold weight
    bold_data = []
    for size in sorted(args.sizes):
        print(f"Rendering bold {size}px...")
        data = render_font_size(bold_face, size)
        bold_data.append(data)
        total_glyphs = sum(1 for g in data["glyphs"] if g["width"] > 0)
        print(f"  {total_glyphs} non-empty glyphs, "
              f"ascent={data['ascent']}, descent={data['descent']}, "
              f"line_height={data['line_height']}")

    # Render the COMMP ("Common Pixel") named family — a pixel font.
    # CRISP: pixel fonts must NOT be antialiased/hinted into mush, so use
    # FT_LOAD_NO_HINTING (render_font_size already does for <8px; the
    # NO_AUTOHINT path preserves the font's own hints for these sizes).
    commp_data = []
    commp_ttf = args.commp_ttf
    if commp_ttf is None:
        # The PLATFORM font directory (<repo>/fonts/) is the one place font
        # files live -- it is what `composeio.render.fonts.resolve_font_path`
        # already searches, so the desktop and this generator read the SAME
        # file by construction rather than by coincidence.  COMMP___.TTF used
        # to sit HERE, in the firmware build directory, where the desktop font
        # resolver had no reason to look; that was the distribution half of
        # the commp font-parity defect.  (This module's own --commp-ttf help
        # has said "compose_resources/fonts/COMMP___.TTF" since it was written.)  The legacy
        # location is still accepted so an old checkout still builds.
        for cand in (Path(__file__).resolve().parents[2] / "compose_resources" / "fonts" / "COMMP___.TTF",
                     Path(__file__).parent / "COMMP___.TTF"):
            if cand.exists():
                commp_ttf = str(cand)
                break
    if commp_ttf:
        print(f"Using COMMP font: {commp_ttf}")
        commp_face = freetype.Face(commp_ttf, 0)
        for size in sorted(args.commp_sizes):
            print(f"Rendering COMMP {size}px...")
            data = render_font_size(commp_face, size)
            commp_data.append(data)
            total_glyphs = sum(1 for g in data["glyphs"] if g["width"] > 0)
            print(f"  {total_glyphs} non-empty glyphs, ascent={data['ascent']}, "
                  f"descent={data['descent']}, line_height={data['line_height']}")
    else:
        print("  WARNING: COMMP___.TTF not found — no COMMP family generated.")

    generate_c_header_dual(regular_data, bold_data, output_path, commp_data)
    generate_json_atlas_dual(regular_data, bold_data, ttf_path, json_output_path,
                             commp_data)
    print("Done.")


if __name__ == "__main__":
    main()
