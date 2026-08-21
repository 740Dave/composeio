"""
ComposeUX Render Engine - Skia Desktop Backend (Layer 1)

Implements the Canvas drawing contract using skia-python.
Phase 1: raster (bitmap-backed) surface that saves to PNG.
GPU surface and live windowing can be added in later phases.

Text ([CXC-R-DRAW-FONT], M2): three modes, in precedence order —
  1. bitmap atlas (opt-in via enable_bitmap_atlas) — CYD-parity checks;
  2. FreeType engine raster (DEFAULT) — the engine rasterizes bundled
     IBM Plex glyphs itself (ft_text.py) and Skia only blits them, which
     is what makes text byte-identical across macOS and Linux/aarch64
     (skia's own text path delegates to CoreText/FreeType per-OS and
     their AA coverage differs);
  3. native Skia text — fallback when the bundled fonts are unreachable,
     or forced with COMPOSEIO_NATIVE_TEXT=1 (output is then no longer
     cross-platform-stable).
"""

from __future__ import annotations

import math
import os
from typing import Any, List, Optional, Tuple, TYPE_CHECKING

import numpy as np
import skia

from .canvas import (
    ArcTo,
    Canvas,
    Close,
    CurveTo,
    GradientStop,
    LineTo,
    MoveTo,
    PathCommand,
    QuadTo,
    Size,
    TextMetrics,
)

if TYPE_CHECKING:
    from .bitmap_font import BitmapFontAtlas


def _color(r: int, g: int, b: int, a: int = 255) -> int:
    """Convert RGBA 0-255 to a Skia Color value."""
    return skia.ColorSetARGB(a, r, g, b)


def _build_skia_path(commands: List[PathCommand]) -> skia.Path:
    """Translate our PathCommand list into a Skia Path object."""
    path = skia.Path()
    for cmd in commands:
        match cmd:
            case MoveTo(x, y):
                path.moveTo(x, y)
            case LineTo(x, y):
                path.lineTo(x, y)
            case CurveTo(cp1x, cp1y, cp2x, cp2y, x, y):
                path.cubicTo(cp1x, cp1y, cp2x, cp2y, x, y)
            case QuadTo(x1, y1, x, y):
                path.quadTo(x1, y1, x, y)
            case ArcTo(rx, ry, rotation, large_arc, sweep, x, y):
                arc_size = skia.Path.ArcSize.kLarge_ArcSize if large_arc else skia.Path.ArcSize.kSmall_ArcSize
                sweep_dir = skia.PathDirection.kCW if sweep else skia.PathDirection.kCCW
                path.arcTo(rx, ry, rotation, arc_size, sweep_dir, x, y)
            case Close():
                path.close()
    return path


# Process-wide typeface cache: (family, weight) -> skia.Typeface
_TYPEFACE_CACHE: dict = {}

# Process-wide colorized FreeType glyph images:
# (face_file, size26.6, char, r, g, b, a) -> skia.Image
_FT_IMAGE_CACHE: dict = {}

# Weight string -> Skia font weight constant
_WEIGHT_MAP = {
    "regular": skia.FontStyle.kNormal_Weight,
    "medium": skia.FontStyle.kMedium_Weight,
    "bold": skia.FontStyle.kBold_Weight,
}


def _make_typeface(family: str, weight: str) -> skia.Typeface:
    """Resolve a typeface for (family, weight).

    Per [CXC-R-DRAW-FONT] (2026-07-06): ComposeUX bundles IBM Plex Sans +
    IBM Plex Mono and loads them via Typeface.MakeFromFile so every
    platform rasterizes the SAME glyph outlines — this is what makes text
    pixel-identical across macOS and Linux/aarch64 (fontconfig/CoreText
    never picks a face).  System-font lookup remains only as a last-ditch
    fallback when the bundled files are unreachable (no repo, no bus).
    """
    from .fonts import resolve_font_path
    path = resolve_font_path(family, weight)
    if path is not None:
        tf = skia.Typeface.MakeFromFile(path)
        if tf is not None:
            return tf
    # Fallback: system font stack (output no longer cross-platform-stable)
    w = _WEIGHT_MAP.get(weight, skia.FontStyle.kNormal_Weight)
    style = skia.FontStyle(w, skia.FontStyle.kNormal_Width,
                           skia.FontStyle.kUpright_Slant)
    tf = skia.Typeface.MakeFromName(family, style)
    return tf if tf is not None else skia.Typeface.MakeDefault()


class SkiaCanvas(Canvas):
    """Skia raster backend.

    Creates a bitmap-backed surface.  Call ``save_png(path)`` after
    drawing to persist the result.  ``present()`` is a no-op for the
    raster backend (it exists for API symmetry so widgets don't need
    to know what backend they're running on).
    """

    def __init__(self) -> None:
        self._surface: Optional[skia.Surface] = None
        self._canvas: Optional[skia.Canvas] = None
        self._width = 0
        self._height = 0

        # Current style state
        self._fill_paint = skia.Paint()
        self._fill_paint.setAntiAlias(True)
        self._fill_paint.setStyle(skia.Paint.kFill_Style)

        self._stroke_paint = skia.Paint()
        self._stroke_paint.setAntiAlias(True)
        self._stroke_paint.setStyle(skia.Paint.kStroke_Style)
        self._stroke_paint.setStrokeWidth(1.0)

        self._opacity: float = 1.0

        # Shadow state (None when disabled)
        self._shadow: Optional[Tuple[float, float, float, Tuple[int, int, int, int]]] = None

        # Font state — default face is the bundled Plex Sans ([CXC-R-DRAW-FONT])
        typeface = _TYPEFACE_CACHE.get(("sans-serif", "regular"))
        if typeface is None:
            typeface = _make_typeface("sans-serif", "regular")
            _TYPEFACE_CACHE[("sans-serif", "regular")] = typeface
        self._typeface: skia.Typeface = typeface
        self._font: skia.Font = skia.Font(self._typeface, 14)
        self._font.setEdging(skia.Font.Edging.kSubpixelAntiAlias)

        # Gradient paint (replaces fill paint when set)
        self._gradient_paint: Optional[skia.Paint] = None

        # Bitmap font atlas (opt-in via enable_bitmap_atlas)
        self._bitmap_atlas: Optional[BitmapFontAtlas] = None
        self._font_size: float = 14.0  # tracked for atlas mode
        self._bitmap_font_bold: bool = False  # tracked for atlas bold selection

        # FreeType engine-raster text (the M2 default; see module docstring)
        self._font_family: str = "sans-serif"
        self._font_weight: str = "regular"
        self._ft_text: bool = os.environ.get("COMPOSEIO_NATIVE_TEXT") != "1"

        # [CXC-ICP-S2-FONTSCALE] the console font-scale MULTIPLIER, applied at
        # the ONE set_font chokepoint so it threads through every text DRAW and
        # every measure_text uniformly (all three text paths read _font_size /
        # _font, which set_font scales here).  Default 1.0 -> exact byte parity
        # (size * 1.0 == size), so every non-console scene + every existing
        # golden is untouched.
        self._font_scale: float = 1.0

    # -- helpers ------------------------------------------------------------

    def _active_fill(self) -> skia.Paint:
        """Return the fill paint, applying opacity and gradient overrides.

        For gradient paints, opacity is applied via the paint alpha which
        Skia composites with the shader's stop alphas.  This avoids having
        to rebuild the gradient shader when opacity changes.
        """
        paint = self._gradient_paint if self._gradient_paint else self._fill_paint
        if self._opacity < 1.0:
            paint = skia.Paint(paint)
            # For both solid and gradient paints, setting the paint alpha
            # causes Skia to composite it with the shader output, effectively
            # scaling all stop alphas by the global opacity.
            paint.setAlpha(int(255 * self._opacity))
        return paint

    def _active_stroke(self) -> skia.Paint:
        paint = self._stroke_paint
        if self._opacity < 1.0:
            paint = skia.Paint(paint)
            paint.setAlpha(int(paint.getAlpha() * self._opacity))
        return paint

    def _apply_shadow(self, paint: skia.Paint) -> skia.Paint:
        """If a shadow is configured, return a new paint with a DropShadow ImageFilter.

        Applies global opacity to the shadow color alpha so that
        set_opacity() affects shadows the same way it affects fills/strokes.
        """
        if self._shadow is None:
            return paint
        ox, oy, blur, (sr, sg, sb, sa) = self._shadow
        # Apply global opacity to shadow alpha
        if self._opacity < 1.0:
            sa = int(sa * self._opacity)
        shadow_filter = skia.ImageFilters.DropShadow(
            ox, oy, blur, blur, _color(sr, sg, sb, sa)
        )
        paint = skia.Paint(paint)
        paint.setImageFilter(shadow_filter)
        return paint

    # -- Surface ------------------------------------------------------------

    def create_surface(self, width: int, height: int) -> None:
        self._width = width
        self._height = height
        info = skia.ImageInfo.MakeN32Premul(width, height)
        self._surface = skia.Surface.MakeRaster(info)
        self._canvas = self._surface.getCanvas()

    def present(self) -> None:
        # Raster backend: no-op.  Use save_png() to persist.
        pass

    def get_surface_size(self) -> Size:
        return Size(float(self._width), float(self._height))

    def save_png(self, path: str) -> None:
        """Write the current surface contents to a PNG file."""
        assert self._surface is not None, "create_surface() not called"
        image = self._surface.makeImageSnapshot()
        image.save(path, skia.kPNG)

    # -- Geometry -----------------------------------------------------------

    def fill_rect(self, x: float, y: float, w: float, h: float) -> None:
        paint = self._apply_shadow(self._active_fill())
        self._canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint)

    def stroke_rect(self, x: float, y: float, w: float, h: float) -> None:
        paint = self._apply_shadow(self._active_stroke())
        self._canvas.drawRect(skia.Rect.MakeXYWH(x, y, w, h), paint)

    def fill_round_rect(self, x: float, y: float, w: float, h: float,
                        radius: float) -> None:
        paint = self._apply_shadow(self._active_fill())
        rrect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), radius, radius)
        self._canvas.drawRRect(rrect, paint)

    def stroke_round_rect(self, x: float, y: float, w: float, h: float,
                          radius: float) -> None:
        paint = self._apply_shadow(self._active_stroke())
        rrect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), radius, radius)
        self._canvas.drawRRect(rrect, paint)

    def fill_circle(self, cx: float, cy: float, r: float) -> None:
        paint = self._apply_shadow(self._active_fill())
        self._canvas.drawCircle(cx, cy, r, paint)

    def stroke_circle(self, cx: float, cy: float, r: float) -> None:
        paint = self._apply_shadow(self._active_stroke())
        self._canvas.drawCircle(cx, cy, r, paint)

    def fill_arc(self, cx: float, cy: float, r: float,
                 start_angle: float, end_angle: float) -> None:
        paint = self._apply_shadow(self._active_fill())
        # Skia uses degrees and a bounding oval rect
        start_deg = math.degrees(start_angle)
        sweep_deg = math.degrees(end_angle - start_angle)
        rect = skia.Rect.MakeXYWH(cx - r, cy - r, 2 * r, 2 * r)
        self._canvas.drawArc(rect, start_deg, sweep_deg, True, paint)

    def stroke_arc(self, cx: float, cy: float, r: float,
                   start_angle: float, end_angle: float) -> None:
        paint = self._apply_shadow(self._active_stroke())
        start_deg = math.degrees(start_angle)
        sweep_deg = math.degrees(end_angle - start_angle)
        rect = skia.Rect.MakeXYWH(cx - r, cy - r, 2 * r, 2 * r)
        self._canvas.drawArc(rect, start_deg, sweep_deg, False, paint)

    def draw_line(self, x1: float, y1: float,
                  x2: float, y2: float) -> None:
        paint = self._apply_shadow(self._active_stroke())
        self._canvas.drawLine(x1, y1, x2, y2, paint)

    def draw_path(self, commands: List[PathCommand]) -> None:
        paint = self._apply_shadow(self._active_stroke())
        self._canvas.drawPath(_build_skia_path(commands), paint)

    def fill_path(self, commands: List[PathCommand]) -> None:
        paint = self._apply_shadow(self._active_fill())
        self._canvas.drawPath(_build_skia_path(commands), paint)

    # -- Style State --------------------------------------------------------

    def set_fill_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        self._fill_paint.setColor(_color(r, g, b, a))
        # Clear gradient override when an explicit fill color is set
        self._gradient_paint = None

    def set_stroke_color(self, r: int, g: int, b: int, a: int = 255) -> None:
        self._stroke_paint.setColor(_color(r, g, b, a))

    def set_stroke_width(self, w: float) -> None:
        self._stroke_paint.setStrokeWidth(w)

    def set_linear_gradient(self, x1: float, y1: float,
                            x2: float, y2: float,
                            stops: List[GradientStop]) -> None:
        colors = [_color(s.r, s.g, s.b, s.a) for s in stops]
        positions = [s.offset for s in stops]
        shader = skia.GradientShader.MakeLinear(
            points=[skia.Point(x1, y1), skia.Point(x2, y2)],
            colors=colors,
            positions=positions,
        )
        paint = skia.Paint()
        paint.setAntiAlias(True)
        paint.setStyle(skia.Paint.kFill_Style)
        paint.setShader(shader)
        self._gradient_paint = paint

    def set_radial_gradient(self, cx: float, cy: float, r: float,
                            stops: List[GradientStop]) -> None:
        colors = [_color(s.r, s.g, s.b, s.a) for s in stops]
        positions = [s.offset for s in stops]
        shader = skia.GradientShader.MakeRadial(
            center=skia.Point(cx, cy),
            radius=r,
            colors=colors,
            positions=positions,
        )
        paint = skia.Paint()
        paint.setAntiAlias(True)
        paint.setStyle(skia.Paint.kFill_Style)
        paint.setShader(shader)
        self._gradient_paint = paint

    def set_shadow(self, offset_x: float, offset_y: float,
                   blur: float,
                   color: Tuple[int, int, int, int]) -> None:
        """Configure drop shadow for subsequent draws.

        To clear a shadow, call ``clear_shadow()`` or pass all-zero
        parameters: ``set_shadow(0, 0, 0, (0, 0, 0, 0))``.
        A shadow with color alpha == 0 is treated as "no shadow".
        """
        # Clear when: all params zero, OR color alpha is 0
        r, g, b, a = color
        if (blur <= 0 and offset_x == 0 and offset_y == 0) or a == 0:
            self._shadow = None
        else:
            self._shadow = (offset_x, offset_y, max(blur, 0.0), color)

    def clear_shadow(self) -> None:
        """Remove any active drop shadow."""
        self._shadow = None

    def set_opacity(self, a: float) -> None:
        self._opacity = max(0.0, min(1.0, a))

    # -- Transform & Clip ---------------------------------------------------

    def save(self) -> None:
        self._canvas.save()

    def restore(self) -> None:
        self._canvas.restore()

    def translate(self, dx: float, dy: float) -> None:
        self._canvas.translate(dx, dy)

    def rotate(self, angle: float) -> None:
        self._canvas.rotate(math.degrees(angle))

    def scale(self, sx: float, sy: float) -> None:
        self._canvas.scale(sx, sy)

    def clip_rect(self, x: float, y: float, w: float, h: float) -> None:
        self._canvas.clipRect(skia.Rect.MakeXYWH(x, y, w, h))

    def clip_round_rect(self, x: float, y: float, w: float, h: float,
                        radius: float) -> None:
        rrect = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(x, y, w, h), radius, radius)
        self._canvas.clipRRect(rrect)

    # -- Bitmap Font Atlas (opt-in) -----------------------------------------

    def enable_bitmap_atlas(self, atlas_path: str) -> None:
        """Switch text rendering to bitmap font atlas mode.

        When enabled, draw_text() and measure_text() use the atlas
        instead of Skia native text. set_font() still records size
        and weight but does not create a Skia Typeface.

        Spec: [FONT-UNIFY-RENDERER-INTEGRATE]
        """
        from .bitmap_font import BitmapFontAtlas
        self._bitmap_atlas = BitmapFontAtlas(atlas_path)

    def disable_bitmap_atlas(self) -> None:
        """Revert to native Skia text rendering."""
        self._bitmap_atlas = None

    # -- Text ---------------------------------------------------------------

    def set_font_scale(self, scale: float) -> None:
        """Set the console font-scale multiplier ([CXC-ICP-S2-FONTSCALE]).
        Applied to every subsequent set_font size (draw AND measure)."""
        try:
            self._font_scale = float(scale)
        except (TypeError, ValueError):
            self._font_scale = 1.0

    def set_font(self, family: str, size: float,
                 weight: str = "regular") -> None:
        # [CXC-ICP-S2-FONTSCALE] scale here (the single chokepoint) so DRAW and
        # MEASURE stay consistent — no text overflows a box measured at 1.0.
        if self._font_scale != 1.0:
            size = size * self._font_scale
        self._font_size = size
        self._font_family = family
        self._font_weight = weight if weight in ("regular", "medium", "bold") \
            else "regular"
        self._bitmap_font_bold = (weight == "bold")
        if self._bitmap_atlas is not None or self._ft_text:
            # Atlas / FreeType modes: record the state; no Skia typeface
            return
        # Cache typefaces per (family, weight): file load / fontconfig costs
        # ~1-2ms PER CALL, which alone pushed a 20-text-call frame past the
        # 30fps budget on Raspberry Pi.
        cache_key = (family, weight)
        typeface = _TYPEFACE_CACHE.get(cache_key)
        if typeface is None:
            typeface = _make_typeface(family, weight)
            _TYPEFACE_CACHE[cache_key] = typeface
        self._typeface = typeface
        self._font = skia.Font(self._typeface, size)
        self._font.setEdging(skia.Font.Edging.kSubpixelAntiAlias)

    def draw_text(self, text: str, x: float, y: float,
                  align: str = "left") -> None:
        from .fonts import normalize_text
        text = normalize_text(text)
        if self._bitmap_atlas is not None:
            self._draw_text_atlas(text, x, y, align)
            return
        if self._ft_text and self._draw_text_freetype(text, x, y, align):
            return
        # Text always uses the solid fill color, never the gradient paint.
        # This prevents a prior set_linear_gradient() from bleeding into text.
        paint = self._fill_paint
        if self._opacity < 1.0:
            paint = skia.Paint(paint)
            paint.setAlpha(int(paint.getAlpha() * self._opacity))
        paint = self._apply_shadow(paint)
        blob = skia.TextBlob.MakeFromString(text, self._font)
        if blob is None:
            return
        metrics = self.measure_text(text)
        if align == "center":
            x -= metrics.width / 2
        elif align == "right":
            x -= metrics.width
        self._canvas.drawTextBlob(blob, x, y, paint)

    def measure_text(self, text: str) -> TextMetrics:
        from .fonts import normalize_text
        text = normalize_text(text)
        if self._bitmap_atlas is not None:
            return self._bitmap_atlas.measure_text(text, self._font_size, bold=self._bitmap_font_bold)
        if self._ft_text:
            try:
                from .ft_text import get_engine
                m = get_engine().measure(self._font_family, self._font_weight,
                                         self._font_size, text)
                if m is not None:
                    return m
            except Exception:
                pass
        bounds = skia.Rect()
        width = self._font.measureText(text, bounds=bounds)
        fm = self._font.getMetrics()
        return TextMetrics(
            width=width,
            height=fm.fDescent - fm.fAscent,
            ascent=-fm.fAscent,
            descent=fm.fDescent,
        )

    def _draw_text_freetype(self, text: str, x: float, y: float,
                            align: str) -> bool:
        """Engine-rasterized text: FreeType coverage bitmaps colorized with
        the fill color and blitted 1:1 at integer positions.

        Returns False when the bundled faces can't be resolved (caller
        falls back to native Skia text).  Spec: [CXC-R-DRAW-FONT] M2.
        """
        if not text:
            return True
        try:
            from .ft_text import get_engine
        except Exception:
            return False
        eng = get_engine()
        family, weight, size = (self._font_family, self._font_weight,
                                self._font_size)
        # Resolve the face ONCE for the whole run ([CXC-R-DAMAGE] frame budget) —
        # the per-character path re-derived the face file + 26.6 size for every
        # glyph, ~15 500 times a frame on a full terminal grid.
        run = eng.run(family, weight, size)
        if run is None:
            return False

        draw_x = x
        # LEFT-aligned text needs no width, so it needs no measuring pass.  The
        # old code measured unconditionally, which walked every character TWICE
        # (once to sum advances, once to blit) for the ~99% of engine text that
        # is left-aligned.  Centre/right still measure — from the same run.
        if align and align[0] == "c":
            draw_x = x - run.text_width(text) / 2.0
        elif align and align[0] == "r":
            draw_x = x - run.text_width(text)

        fill_color = self._fill_paint.getColor()
        fa = (fill_color >> 24) & 0xFF
        fr = (fill_color >> 16) & 0xFF
        fg = (fill_color >> 8) & 0xFF
        fb = fill_color & 0xFF
        if self._opacity < 1.0:
            fa = int(fa * self._opacity)

        pen = int(round(draw_x))
        base = int(round(y))
        face_key = run.key
        glyph = run.glyph
        fallback_advance = int(size * 0.5)
        for ch in text:
            g = glyph(ch)
            if g is None:
                pen += fallback_advance
                continue
            if g.width > 0 and g.height > 0:
                img_key = (*face_key, ch, fr, fg, fb, fa)
                image = _FT_IMAGE_CACHE.get(img_key)
                if image is None:
                    rgba = np.zeros((g.height, g.width, 4), dtype=np.uint8)
                    rgba[..., 0] = fr
                    rgba[..., 1] = fg
                    rgba[..., 2] = fb
                    # CYD alpha-modulation formula: (cov * fa + 127) // 255
                    rgba[..., 3] = ((g.bitmap.astype(np.uint16) * fa + 127)
                                    // 255).astype(np.uint8)
                    image = skia.Image.fromarray(
                        rgba, colorType=skia.ColorType.kRGBA_8888_ColorType)
                    _FT_IMAGE_CACHE[img_key] = image
                if image is not None:
                    self._canvas.drawImage(image,
                                           float(pen + g.bearing_x),
                                           float(base - g.bearing_y))
            pen += g.advance
        return True

    def _draw_text_atlas(self, text: str, x: float, y: float,
                         align: str) -> None:
        """Render text using the bitmap font atlas.

        Matches CYD ESP32Canvas::draw_text (canvas_esp32.cpp lines 1372-1431):
        same atlas lookup, scale computation, nearest-neighbor glyph blitting,
        and alpha blending with fill color.

        Spec: [FONT-UNIFY-RENDERER-BLIT]
        """
        if not text:
            return

        atlas_obj = self._bitmap_atlas
        font_size = self._font_size
        bold = self._bitmap_font_bold

        target = int(font_size + 0.5)
        atlas = atlas_obj.find_atlas(target, bold=bold)
        if atlas is None:
            return

        scale = font_size / float(atlas.size)
        m = self.measure_text(text)

        draw_x = x
        if align and align[0] == "c":
            draw_x = x - m.width / 2.0
        elif align and align[0] == "r":
            draw_x = x - m.width

        draw_y = y - m.ascent

        # Extract fill color components from the paint
        fill_color = self._fill_paint.getColor()
        fa = (fill_color >> 24) & 0xFF
        fr = (fill_color >> 16) & 0xFF
        fg = (fill_color >> 8) & 0xFF
        fb = fill_color & 0xFF

        if self._opacity < 1.0:
            fa = int(fa * self._opacity)

        for ch in text:
            char_code = ord(ch)
            glyph = atlas_obj.get_glyph(char_code, atlas)
            if glyph is None or not glyph.bitmap:
                draw_x += atlas.size * 0.5 * scale
                continue

            glyph_w = glyph.width
            glyph_h = glyph.height
            gx = draw_x + glyph.bearing_x * scale
            gy = draw_y + (atlas.ascent - glyph.bearing_y) * scale

            # Nearest-neighbor scaling matching CYD integer arithmetic
            sw = int(glyph_w * scale + 0.5)
            sh = int(glyph_h * scale + 0.5)
            igx = int(gx + 0.5)
            igy = int(gy + 0.5)

            if sw <= 0 or sh <= 0:
                draw_x += glyph.advance * scale
                continue

            bitmap_data = glyph.bitmap

            # Build scaled RGBA image with fill color and modulated alpha
            # using the same nearest-neighbor as CYD: src_row = py * glyph_h / sh
            rgba = np.zeros((sh, sw, 4), dtype=np.uint8)
            for py in range(sh):
                src_row = py * glyph_h // sh
                for px in range(sw):
                    src_col = px * glyph_w // sw
                    alpha = bitmap_data[src_row * glyph_w + src_col]
                    if alpha == 0:
                        continue
                    # Modulate glyph alpha with fill alpha (CYD formula)
                    final_a = (alpha * fa + 127) // 255
                    if final_a == 0:
                        continue
                    rgba[py, px] = [fr, fg, fb, final_a]

            # Create Skia image from the RGBA array and draw it
            glyph_image = skia.Image.fromarray(
                rgba, colorType=skia.ColorType.kRGBA_8888_ColorType
            )
            if glyph_image is not None:
                dest = skia.Rect.MakeXYWH(float(igx), float(igy), float(sw), float(sh))
                sampling = skia.SamplingOptions(skia.FilterMode.kNearest)
                self._canvas.drawImageRect(glyph_image, dest, sampling)

            draw_x += glyph.advance * scale

    # -- Image --------------------------------------------------------------

    def draw_image(self, image_handle: Any, x: float, y: float,
                   w: float, h: float) -> None:
        if image_handle is None:
            return
        dest = skia.Rect.MakeXYWH(x, y, w, h)
        self._canvas.drawImageRect(image_handle, dest)

    def decode_image(self, data: bytes) -> Any:
        """Decode raw image bytes (PNG/JPEG) to a backend handle.

        Backend codec helper used by the engine AssetManager for doc://
        payloads (Layer 1 'asset decoding' per [CXC-R-ARCH]).  Returns
        None if the bytes are not a decodable image.
        """
        try:
            import io
            return skia.Image.open(io.BytesIO(data))
        except Exception:
            return None

    def load_image(self, uri: str) -> Any:
        """Load an image from a local path, file://, or bus://assets/… (fetched from bus storage).

        `bus://assets/…` resolves via composeio.resolve against the bus master's storage
        service (DP-CIO-23). `doc://` is a *document* reference, not an image source, and
        still raises. Returns None if the image cannot be opened/fetched.
        """
        if uri.startswith("doc://"):
            raise NotImplementedError(
                f"doc:// is a document reference, not an image source: {uri}"
            )
        if uri.startswith("bus://"):
            try:
                import io
                from composeio.resolve import resolve_bytes
                return skia.Image.open(io.BytesIO(resolve_bytes(uri)))
            except Exception:
                return None
        path = uri.replace("file://", "")
        try:
            return skia.Image.open(path)
        except Exception:
            return None
