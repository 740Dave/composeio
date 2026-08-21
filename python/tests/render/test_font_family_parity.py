"""Cross-implementation font-FAMILY parity.

Spec: [CXC-CYD-FONT-FAMILY-REGISTRY] / [CXC-CYD-FONT-FAMILY-DEFAULT] /
[CXC-CYD-FONT-FAMILY-NO-SILENT-FALLBACK] (cyd/spec/cyd-font-unification.lts §2b).

The defect these tests exist to catch: a scene said ``font_family: commp``
and got the COMMP pixel atlas on the CYD firmware and IBM Plex Sans on the
desktop, because family routing is written twice and nothing compared the two
lists.  Every assertion below reads the CODE of both tiers -- never a
hand-copied list -- so a family added to one side and not the other fails
here instead of on the glass.

Headless by construction: nothing in this module renders.  No ``needs_glass``
marker is needed and none should be added.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_FONT_ATLAS_H = _REPO / "cyd" / "src" / "font_atlas.h"
_COMPOSE_SCENE_CPP = _REPO / "cyd" / "src" / "compose_scene.cpp"
_PARSER_PY = _REPO / "python" / "composeio" / "render" / "engine" / "parser.py"
_GENERATOR = _REPO / "cyd" / "fonts" / "generate_atlas.py"


# ---------------------------------------------------------------- firmware side

def _firmware_family_names() -> set[str]:
    """Every family NAME the generated firmware atlas header can select.

    Parsed out of the ``font_family_is_*`` selectors' string literals, e.g.
    ``return ieq(family, "commp") || ieq(family, "common pixel");``
    """
    if not _FONT_ATLAS_H.is_file():
        pytest.skip("cyd/src/font_atlas.h not present in this checkout")
    src = _FONT_ATLAS_H.read_text(errors="replace")
    names = set()
    for block in re.finditer(
            r"bool\s+font_family_is_\w+\s*\([^)]*\)\s*\{(.*?)\n\}", src, re.S):
        for lit in re.finditer(r'ieq\s*\(\s*family\s*,\s*"([^"]+)"', block.group(1)):
            names.add(lit.group(1).strip().lower())
    return names


def _firmware_default_family() -> str:
    """The default the firmware scene parser applies when the key is absent."""
    if not _COMPOSE_SCENE_CPP.is_file():
        pytest.skip("cyd/src/compose_scene.cpp not present in this checkout")
    src = _COMPOSE_SCENE_CPP.read_text(errors="replace")
    m = re.search(r'gets\s*\([^,]+,\s*\w+,\s*"font_family"\s*,\s*"([^"]*)"\s*\)', src)
    assert m, "could not locate the firmware font_family default in compose_scene.cpp"
    return m.group(1)


def _desktop_default_family() -> str:
    """The default the desktop scene parser applies when the key is absent."""
    src = _PARSER_PY.read_text(errors="replace")
    m = re.search(r'font_family\s*=\s*str\(\s*spec\.get\(\s*"font_family"\s*,\s*\n?\s*"([^"]*)"',
                  src)
    assert m, "could not locate the desktop font_family default in parser.py"
    return m.group(1)


# ------------------------------------------------------- the registry contract

def test_desktop_declares_a_family_registry():
    """[CXC-CYD-FONT-FAMILY-REGISTRY] -- one DECLARED set, not a hand table."""
    from composeio.render import fonts
    assert hasattr(fonts, "FAMILY_REGISTRY"), (
        "the desktop tier has no declared family set; family routing exists "
        "only as the classify_family() if-chain, which is the fork this spec "
        "node closes"
    )
    assert fonts.FAMILY_REGISTRY, "the declared family set is empty"


def test_every_firmware_family_is_declared_on_the_desktop():
    """[CXC-CYD-FONT-FAMILY-REGISTRY] -- the two enumerations must agree.

    THE defect: font_atlas.h selects "commp"/"common pixel"; fonts.py had no
    such branch, so the name fell through to sans and drew IBM Plex.
    """
    from composeio.render.fonts import declared_family_names

    firmware = _firmware_family_names()
    assert firmware, "parsed no families out of font_atlas.h -- fix the parser, not the assert"
    desktop = {n.lower() for n in declared_family_names()}
    missing = sorted(firmware - desktop)
    assert not missing, (
        f"families the FIRMWARE can select but the DESKTOP never declared: "
        f"{missing}. A scene naming one of these renders a different face on "
        f"each tier with no error on either."
    )

    # Declared is necessary but not sufficient -- a name the desktop knows
    # and cannot draw is still a divergence, just a reported one.
    from composeio.render.fonts import resolve_family
    unrealizable = sorted(n for n in firmware
                          if resolve_family(n).realized_by == "unrealized")
    assert not unrealizable, (
        f"families the FIRMWARE realizes and the DESKTOP cannot: {unrealizable}. "
        f"Declared-but-unrealizable is honest, but it is still the scene "
        f"rendering differently on the two tiers."
    )


def test_declared_families_the_firmware_lacks_are_deliberate():
    """The reverse direction of the same fork (Tiny5 went this way in a819a5f).

    Not an equality assert: a desktop-only family is legitimate (Tiny5 is
    desktop/SVG-only today).  What is NOT legitimate is it being undeclared.
    """
    from composeio.render.fonts import declared_family_names, resolve_family

    for name in declared_family_names():
        r = resolve_family(name)
        assert r.declared, f"{name!r} is in the registry but resolve_family says undeclared"


# --------------------------------------------------- the no-silent-fallback rule

def test_unrealizable_named_family_is_reported_not_silently_substituted():
    """[CXC-CYD-FONT-FAMILY-NO-SILENT-FALLBACK] -- the load-bearing assertion.

    Rendering must not stop, but the substitution must land somewhere a
    machine can read.  A silent substitution is indistinguishable from a
    correct render to every signal we have.
    """
    from composeio.render import fonts

    fonts.clear_unresolved_family_reports()
    fonts.classify_family("definitely-not-a-real-family-9d3f")
    reports = fonts.unresolved_family_reports()
    assert "definitely-not-a-real-family-9d3f" in reports, (
        "an unknown font family was substituted with NO report -- this is the "
        "exact failure class: the text draws, nothing errors, the output is wrong"
    )


def test_a_declared_realizable_family_produces_no_report():
    """The instrument must be able to say NO, or it certifies rather than verifies."""
    from composeio.render import fonts

    fonts.clear_unresolved_family_reports()
    fonts.classify_family("sans-serif")
    fonts.classify_family("monospace")
    assert not fonts.unresolved_family_reports(), (
        "the default families were reported as unresolved -- the report is "
        "firing on everything and therefore means nothing"
    )


def test_commp_resolves_to_a_DIFFERENT_face_than_sans_serif():
    """The reported symptom, reduced to one assertion.

    MEASURED before the fix: resolve_font_path("commp", "bold") and
    resolve_font_path("sans-serif", "bold") returned the SAME FILE, and
    ft_text measured "ENGINE THRUST" at 16px bold to the same 129.0 px
    advance on both.  The desktop could not tell the two names apart.
    AFTER: 142.0 px vs 129.0 px -- two faces, as the device has always had.
    """
    from composeio.render import fonts

    if "commp" not in {n.lower() for n in _firmware_family_names()}:
        pytest.skip("this firmware header has no COMMP family")

    fonts.clear_unresolved_family_reports()
    commp = fonts.resolve_font_path("commp", "bold")
    sans = fonts.resolve_font_path("sans-serif", "bold")
    assert commp is not None, (
        "the desktop cannot resolve a face for `commp` at all. The TTF is in "
        "the repo (compose_resources/fonts/COMMP___.TTF); if this fails, the font PATH is wrong, "
        "not the font."
    )
    assert commp != sans, (
        f"font_family: commp resolved to the sans-serif face ({sans}). On the "
        f"device the same scene draws the COMMP pixel atlas. This is the "
        f"bit-equivalency failure, and it is invisible to every machine signal."
    )
    assert "commp" not in fonts.unresolved_family_reports(), (
        "commp is realizable now, so it must NOT be reported as substituted -- "
        "a report that fires on a working family means nothing"
    )


def test_the_desktop_commp_face_IS_the_source_of_the_firmware_atlas():
    """[CXC-CYD-FONT-UNIFY] parity, proven rather than asserted.

    "The desktop and the device draw the same font" is only true if the TTF
    the desktop renders is the file the firmware atlas was generated FROM.
    This walks every COMMP glyph in cyd/src/font_atlas.h and re-renders it
    from compose_resources/fonts/COMMP___.TTF through the generator's own load flags.

    Measured 2026-08-16: 475 glyphs over sizes 12/14/16/20/24, zero metric
    mismatches -- and separately, 435 glyph BITMAPS byte-identical across a
    full regeneration.  COMMP is a pixel font on its own hints, so it is
    grid-exact and toolchain-stable; see
    test_regenerating_the_atlas_is_not_assumed_reproducible.
    """
    freetype = pytest.importorskip("freetype")

    ttf = _REPO / "compose_resources" / "fonts" / "COMMP___.TTF"
    if not ttf.is_file() or not _FONT_ATLAS_H.is_file():
        pytest.skip("COMMP TTF or firmware atlas not present")

    src = _FONT_ATLAS_H.read_text(errors="replace")
    face = freetype.Face(str(ttf), 0)
    checked = mismatches = 0

    for m in re.finditer(r"static const FontAtlas font_atlas_commp_(\d+) = \{(.*?)\n\};",
                         src, re.S):
        size = int(m.group(1))
        body = m.group(2)
        face.set_char_size(0, size * 64, 72, 72)
        for g in re.finditer(r"/\* (\d+) \*/ \{(?:\w+|nullptr),?\s*([^}]*)\}", body):
            cc = int(g.group(1))
            have = tuple(int(x) for x in g.group(2).split(",") if x.strip())
            if len(have) != 5:
                continue
            flags = (freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING
                     if size < 8 else
                     freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_AUTOHINT)
            face.load_char(chr(cc), flags)
            gl = face.glyph
            want = (gl.bitmap.width, gl.bitmap.rows, gl.bitmap_left,
                    gl.bitmap_top, gl.advance.x >> 6)
            checked += 1
            if have != want:
                mismatches += 1

    assert checked > 0, "parsed no COMMP glyphs out of the firmware atlas"
    assert mismatches == 0, (
        f"{mismatches} of {checked} COMMP glyphs differ between "
        f"compose_resources/fonts/COMMP___.TTF and the compiled firmware atlas. The desktop and "
        f"the device are drawing from DIFFERENT sources and the parity claim "
        f"is false."
    )


def test_the_commp_face_lives_in_the_platform_font_directory():
    """One font directory, not one per consumer.

    COMMP___.TTF used to live in cyd/fonts/ -- the FIRMWARE BUILD directory,
    which `resolve_font_path` has no reason to search.  The font was present
    the entire time and the desktop still could not find it, because "where
    fonts live" had two answers.  That is the distribution half of this
    defect and this asserts the de-fork.
    """
    assert (_REPO / "compose_resources" / "fonts" / "COMMP___.TTF").is_file(), (
        "COMMP___.TTF is not in the platform font directory (compose_resources/fonts/), which is "
        "the only directory composeio.render.fonts.resolve_font_path searches"
    )
    assert not (_REPO / "cyd" / "fonts" / "COMMP___.TTF").exists(), (
        "COMMP___.TTF is back in cyd/fonts/ -- if a second copy exists the two "
        "will drift and the desktop/device parity proof above becomes a "
        "coincidence"
    )


# ------------------------------------------------------------ the shared default

def test_omitted_font_family_resolves_to_the_same_default_on_both_tiers():
    """[CXC-CYD-FONT-FAMILY-DEFAULT] -- ratifies an agreement that was untested.

    Both parsers already say "sans-serif".  Nothing compared them, so a
    reimplementation of either side could have picked differently and no
    signal would have fired.
    """
    assert _desktop_default_family() == _firmware_default_family() == "sans-serif"


# ------------------------------------------------------ the generator fork

def test_generator_emits_every_family_group_to_both_outputs():
    """[CXC-CYD-FONT-EXPORT] requirement-every-family.

    generate_atlas.py passed commp_data to the C-header writer and NOT to the
    JSON writer, so one run produced two files with two different family sets
    -- the exact skew [CXC-CYD-FONT-EXPORT-REGEN] warns about, but quieter,
    because both files carry the same timestamp.
    """
    if not _GENERATOR.is_file():
        pytest.skip("cyd/fonts/generate_atlas.py not present")
    src = _GENERATOR.read_text(errors="replace")

    c_sig = re.search(r"def generate_c_header_dual\((.*?)\)\s*->", src, re.S)
    j_sig = re.search(r"def generate_json_atlas_dual\((.*?)\)\s*->", src, re.S)
    assert c_sig and j_sig, "generator emitters not found"

    def _params(sig: str) -> set[str]:
        return {p.split(":")[0].split("=")[0].strip()
                for p in sig.group(1).split(",") if p.strip()}

    c_families = {p for p in _params(c_sig) if p.endswith("_data")}
    j_families = {p for p in _params(j_sig) if p.endswith("_data")}
    assert c_families == j_families, (
        f"the C header writer takes {sorted(c_families)} but the JSON writer "
        f"takes {sorted(j_families)}; a single generator run therefore emits "
        f"different family sets to the two outputs"
    )


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN-RED, and NOT closable by regenerating: font_atlas_data.json has no "
    "commp group because the generator's JSON writer never received "
    "commp_data (fixed for FUTURE runs). Rebuilding it here would rewrite "
    "100% of the Plex glyphs on this machine's freetype -- see "
    "test_regenerating_the_atlas_is_not_assumed_reproducible. Needs the atlas "
    "rebuilt on the toolchain that produced the committed header: an "
    "architect call. strict=True so this fails as XPASS once that happens."))
def test_the_desktop_atlas_carries_every_family_the_firmware_header_does():
    """[CXC-CYD-FONT-EXPORT] requirement-every-family, checked on the ARTIFACTS.

    The signature test above catches the generator growing a fork.  This
    catches the fork that is already SHIPPED in the two committed files.

    KNOWN-RED (xfail): font_atlas_data.json has regular+bold and no commp,
    because the generator's JSON writer never received commp_data.  It cannot
    be closed by regenerating -- see
    test_regenerating_the_atlas_is_not_assumed_reproducible.  It needs the
    atlas rebuilt on the toolchain that produced the committed header, which
    is an architect call, not a test's.
    """
    import json
    atlas_json = _REPO / "python" / "composeio" / "render" / "font_atlas_data.json"
    if not atlas_json.is_file() or not _FONT_ATLAS_H.is_file():
        pytest.skip("atlas artifacts not present")

    header = _FONT_ATLAS_H.read_text(errors="replace")
    hdr_families = set()
    for m in re.finditer(r"static const FontAtlas font_atlas_(\w*?)_?\d+ = \{", header):
        hdr_families.add(m.group(1) or "regular")

    data = json.loads(atlas_json.read_text())
    json_families = {k for k, v in data.items()
                     if isinstance(v, dict) and "sizes" in v}

    assert hdr_families <= json_families, (
        f"the firmware header carries families {sorted(hdr_families)} but the "
        f"desktop atlas carries {sorted(json_families)}; one generator run "
        f"produced two artifacts with different family sets"
    )


def test_regenerating_the_atlas_is_not_assumed_reproducible():
    """[CXC-CYD-FONT-EXPORT-REGEN] has an unwritten precondition.

    The spec says the two outputs "can never be regenerated independently".
    It does not say that regeneration is only equivalent ON THE TOOLCHAIN THAT
    PRODUCED THEM -- and it is not.

    MEASURED 2026-08-16, regenerating with this machine's freetype and
    diffing glyph bitmaps against the committed cyd/src/font_atlas.h:

        COMMP  (pixel font, own hints, >=12px):  435 bitmaps,   0 differ
        Plex   (antialiased, 6..16px):           658 bitmaps, 658 differ

    100% of the Plex glyphs change.  So "just regenerate it" silently
    rewrites every text pixel on every device.  This test does not regenerate
    anything -- it exists so that the next person who reaches for the
    generator reads this first.
    """
    assert _GENERATOR.is_file()
    src = _GENERATOR.read_text(errors="replace")
    assert "COMMP___.TTF" in src, "the generator no longer names the COMMP TTF"
    # The generator must look in the PLATFORM font dir, not only beside itself.
    assert 'parents[2] / "compose_resources" / "fonts"' in src or '"fonts" / "COMMP___.TTF"' in src, (
        "the generator resolves COMMP only relative to its own directory, so "
        "it and the desktop can read different files"
    )


# ------------------------------------------------------------- the corpus lint

def _compose_scenes() -> list[Path]:
    out = []
    for p in _REPO.rglob("*.compose.yaml"):
        s = str(p)
        if "/.git/" in s or "/wt-" in s or "/node_modules/" in s:
            continue
        out.append(p)
    return sorted(out)


def test_every_font_family_named_in_the_corpus_is_a_declared_family():
    """A scene may only name a family the platform DECLARES.

    This is the lint half of [CXC-CYD-FONT-FAMILY-REGISTRY]: it is what makes
    a typo'd or tier-private family a finding at authoring time instead of a
    wrong face on somebody's glass.
    """
    from composeio.render.fonts import declared_family_names

    declared = {n.lower() for n in declared_family_names()}
    offenders: list[str] = []
    for scene in _compose_scenes():
        # `font_family:` appears both as a block key and inside YAML flow
        # mappings ({font_family: monospace, font_size: 10}), so the value
        # terminates at a comma or a closing brace as well as at EOL.
        for m in re.finditer(r"font_family:\s*[\"']?([^\"'#,}\n]+?)[\"']?\s*(?=[,}#\n]|$)",
                             scene.read_text(errors="replace")):
            fam = m.group(1).strip()
            if fam.lower() not in declared:
                offenders.append(f"{scene.relative_to(_REPO)}: {fam!r}")
    assert not offenders, (
        "scenes name font families the platform does not declare:\n  "
        + "\n  ".join(sorted(set(offenders)))
    )
