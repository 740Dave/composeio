"""
ComposeUX Font Registry — the bundled IBM Plex faces.

Spec: [CXC-R-DRAW-FONT] (decision 2026-07-06, David): ComposeUX ships ONE
font family pair — IBM Plex Sans (UI text) + IBM Plex Mono (monospace) —
under the SIL OFL 1.1.  The TTFs are bus assets (bus://assets/fonts/…)
and also live in the repo's compose_resources/fonts/ directory for development.  Every
desktop backend loads the SAME files via Typeface.MakeFromFile, which is
what makes text pixel-identical across macOS and Linux/aarch64: the
system font stack (CoreText / fontconfig) never chooses a face.

The CYD bitmap atlas (cyd/fonts/generate_atlas.py) is generated from the
same IBMPlexSans files, so embedded glyphs are the same design.

Resolution order for a font file:
    1. $COMPOSEIO_FONT_DIR/<file>
    2. <repo>/fonts/<file>            (walk up from this package)
    3. ~/.cache/composeio/fonts/<file> (previously fetched)
    4. fetch bus://assets/fonts/<file> -> cache dir  (bus-native install)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# Family classification -> Plex face.  Any unrecognized family falls back
# to Plex Sans: the engine owns every pixel, so there is no "system font".
_MONO_HINTS = ("mono", "courier", "consolas", "menlo")

# Additive family classes beyond the default Plex pair.  Tiny5 (SIL OFL 1.1,
# Tiny5-OFL.txt) is the Colonial-OS pixel font — routed ONLY when text
# explicitly names font-family:Tiny5 ([SPEC:CXC-CIC]); Plex stays the default
# for everything else (the Intelligence Console deliberately uses Plex).  Tiny5
# ships one weight ("Regular"), so medium/bold map to the same file (see NOTE
# in classify_family): synthetic bolding would break the pixel grid.
_FILES = {
    ("sans", "regular"): "IBMPlexSans-Regular.ttf",
    ("sans", "medium"): "IBMPlexSans-Medium.ttf",
    ("sans", "bold"): "IBMPlexSans-Bold.ttf",
    ("mono", "regular"): "IBMPlexMono-Regular.ttf",
    ("mono", "medium"): "IBMPlexMono-Regular.ttf",   # Plex Mono ships reg+bold
    ("mono", "bold"): "IBMPlexMono-Bold.ttf",
    ("tiny5", "regular"): "Tiny5-Regular.ttf",
    ("tiny5", "medium"): "Tiny5-Regular.ttf",
    ("tiny5", "bold"): "Tiny5-Regular.ttf",          # only Regular exists
    # COMMP ("Common Pixel") — the BSG/DRADIS pixel font.  ONE weight, so
    # medium/bold map to the same file, exactly as Tiny5 does and exactly as
    # the firmware does: font_atlas_find_styled(commp=true, bold, size)
    # ignores `bold` and returns the COMMP atlas.  Synthetic bolding would
    # break the pixel grid AND break tier parity in the same stroke.
    ("commp", "regular"): "COMMP___.TTF",
    ("commp", "medium"): "COMMP___.TTF",
    ("commp", "bold"): "COMMP___.TTF",
}

BUS_FONT_PREFIX = "bus://assets/fonts/"

# ---------------------------------------------------------------------------
# THE DECLARED FAMILY SET  [CXC-CYD-FONT-FAMILY-REGISTRY]
#
# One closed set of family names, read by reference.  Before this existed the
# set lived as TWO hand-maintained enumerations that nothing compared:
#
#   cyd/src/font_atlas.h::font_family_is_commp()   (generated, firmware)
#   this module's classify_family() if-chain       (desktop)
#
# Tiny5 was hand-added to the desktop one (a819a5f) and never to the firmware;
# COMMP was hand-added to the firmware one and never here.  Same fork, two
# families, opposite directions.  `commp` on the desktop therefore fell through
# to "sans" and drew IBM Plex, byte-identical to an unstyled label, while the
# device drew the COMMP pixel atlas.  Nothing logged.  That is the defect.
#
# `realized_by` records HOW a tier makes the family real, so a family that is
# declared but not yet realizable here is VISIBLE rather than invisible
# ([CXC-CYD-FONT-FAMILY-NO-SILENT-FALLBACK]).
# ---------------------------------------------------------------------------

DEFAULT_FAMILY = "sans-serif"
"""[CXC-CYD-FONT-FAMILY-DEFAULT] -- what an omitted ``font_family:`` means.

Both scene parsers already default to this string (``parser.py`` and
``cyd/src/compose_scene.cpp``).  Naming it here is what lets a test assert the
two agree instead of hoping they do.
"""


@dataclass(frozen=True)
class DeclaredFamily:
    name: str                 # canonical spelling
    aliases: tuple            # other accepted spellings (compared lowercased)
    klass: str                # routing key into _FILES
    realized_by: str          # "vector-face" | "bitmap-atlas" | "unrealized"
    note: str = ""


FAMILY_REGISTRY: tuple = (
    DeclaredFamily("sans-serif", ("sans", "ibm plex sans"), "sans", "vector-face",
                   "the platform default; regular/medium/bold"),
    DeclaredFamily("monospace", ("mono", "courier", "consolas", "menlo",
                                 "ibm plex mono"), "mono", "vector-face",
                   "Plex Mono ships regular+bold; medium maps to regular"),
    DeclaredFamily("Tiny5", (), "tiny5", "vector-face",
                   "Colonial-OS pixel font, SIL OFL 1.1; single weight"),
    # COMMP ("Common Pixel"), the BSG/DRADIS pixel font.  CORRECTED
    # 2026-08-16: an earlier revision of this file declared commp
    # `unrealized` on the claim that the TTF was absent.  That claim came
    # from a `find /` that was KILLED at a timeout, not from an absent file.
    # COMMP___.TTF was in the repo the whole time, in cyd/fonts/ -- the
    # firmware build directory, which is the one place the desktop font
    # resolver had no reason to look.  It has been moved to compose_resources/fonts/ (the
    # platform font directory this module already searches) and is verified
    # byte-for-byte to be the source of the compiled firmware atlas: 475
    # glyphs across sizes 12/14/16/20/24, zero metric mismatches against
    # cyd/src/font_atlas.h.  Desktop and device now draw COMMP from ONE file.
    DeclaredFamily("commp", ("common pixel",), "commp", "vector-face",
                   "COMMP pixel font, single weight; same TTF the firmware "
                   "atlas is generated from. NOT yet served from "
                   "bus://assets/fonts/ -- a deployed surface with no repo "
                   "checkout still cannot resolve it (see resolve_font_path)"),
)

_BY_NAME: dict = {}
for _f in FAMILY_REGISTRY:
    _BY_NAME[_f.name.lower()] = _f
    for _a in _f.aliases:
        _BY_NAME[_a.lower()] = _f


def declared_family_names() -> tuple:
    """Every name the platform declares, canonical spellings AND aliases.

    The firmware-vs-desktop parity test compares against this, so an alias the
    firmware matches (``"common pixel"``) counts as declared here too.
    """
    return tuple(sorted({f.name for f in FAMILY_REGISTRY}
                        | set(_BY_NAME.keys())))


@dataclass(frozen=True)
class FamilyResolution:
    requested: str
    declared: bool
    canonical: Optional[str]
    klass: str
    realized_by: str
    reason: str


def resolve_family(family: str) -> FamilyResolution:
    """Resolve a requested family name against the declared set.

    Never raises and never returns "no face" -- rendering must survive an
    unknown family.  The honesty lives in ``declared``/``realized_by``, which
    is what [CXC-CYD-FONT-FAMILY-NO-SILENT-FALLBACK] requires be inspectable.
    """
    key = (family or "").strip().lower()
    if not key:
        key = DEFAULT_FAMILY
    hit = _BY_NAME.get(key)
    if hit is not None:
        if hit.realized_by == "unrealized":
            return FamilyResolution(family, True, hit.name, hit.klass,
                                    hit.realized_by,
                                    f"declared but not realizable on this tier: {hit.note}")
        return FamilyResolution(family, True, hit.name, hit.klass,
                                hit.realized_by, "")
    # Undeclared.  _classify() still yields a survivable class (substring
    # hints), but the name is unknown and that must be said out loud.
    return FamilyResolution(family, False, None, _classify(key), "unrealized",
                            "not a declared platform font family")


# Names already reported, so a 15 500-calls-per-frame text path logs once.
_unresolved: dict = {}


def unresolved_family_reports() -> dict:
    """{requested_family: reason} for every family this process substituted.

    The machine-readable half of [CXC-CYD-FONT-FAMILY-NO-SILENT-FALLBACK]:
    a harness, a lint or a conformance run can ask whether the render it just
    took was drawn in the faces the scene asked for.
    """
    return dict(_unresolved)


def clear_unresolved_family_reports() -> None:
    _unresolved.clear()
    classify_family.cache_clear()


def _record_unresolved(family: str, reason: str) -> None:
    if family in _unresolved:
        return
    _unresolved[family] = reason
    _log.warning("font family %r substituted: %s [CXC-CYD-FONT-FAMILY-"
                 "NO-SILENT-FALLBACK]", family, reason)

# Symbol substitutions for glyphs IBM Plex does not cover.  Applied at
# the backend text seam so the SAME normalization happens on every
# platform (there is deliberately NO system-font fallback chain — a
# fallback face would break cross-platform pixel identity).
GLYPH_SUBSTITUTIONS = {
    "⚠": "!",    # ⚠ warning sign
    "✗": "×",  # ✗ ballot X -> ×
    "⛔": "Ø",  # ⛔ no entry -> Ø
}


def normalize_text(text: str) -> str:
    """Replace glyphs the bundled Plex faces don't cover."""
    for src, dst in GLYPH_SUBSTITUTIONS.items():
        if src in text:
            text = text.replace(src, dst)
    return text


_path_cache: dict = {}


# Both of these are PURE functions of a tiny string domain, and both sit on the
# per-GLYPH path of the FreeType text engine.  Measured on the Terminal Widget's
# 151x46 grid: 620 100 calls across 40 frames (~15 500 per frame) doing `.lower()`,
# a generator `any()` and two dict lookups each — 0.47 s of the 0.36 s/frame budget.
# The result set is a handful of filenames, so memoizing is exact, not approximate.
def _classify(f: str) -> str:
    """The substring routing that predates the registry -- kept EXACTLY as it
    was so no existing family name changes face.

    Only reached for names the registry does NOT declare; declared names route
    through FAMILY_REGISTRY, which is why `commp` no longer lands on "sans".
    """
    if "tiny5" in f:
        return "tiny5"
    return "mono" if any(h in f for h in _MONO_HINTS) else "sans"


@lru_cache(maxsize=256)
def classify_family(family: str) -> str:
    # lru_cache is what makes the reporting free: the body runs ONCE per
    # distinct family string, so "report each substitution once" and "do not
    # touch the per-glyph hot path" are the same statement.
    r = resolve_family(family)
    if r.realized_by == "unrealized" and (family or "").strip():
        _record_unresolved(family, r.reason)
    return r.klass


@lru_cache(maxsize=256)
def font_file_name(family: str, weight: str = "regular") -> str:
    w = weight if weight in ("regular", "medium", "bold") else "regular"
    return _FILES[(classify_family(family), w)]


def _cache_dir() -> Path:
    d = Path(os.environ.get("COMPOSEIO_FONT_CACHE",
                            Path.home() / ".cache" / "composeio" / "fonts"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_fonts_dir() -> Optional[Path]:
    """Walk up from the package looking for the repo-level compose_resources/fonts/ dir."""
    here = Path(__file__).resolve()
    # ⛔ 2026-08-17: the platform fonts moved to compose_resources/fonts/. Both
    #    layouts are probed so a checkout at either revision resolves — the walk
    #    is the seam, and a bare `parent / "fonts"` was a DEPTH assumption that
    #    the move silently invalidated ([DX-LOCATION-IS-A-SEAM-NOT-A-LITERAL]).
    for parent in here.parents:
        for rel in (("compose_resources", "fonts"), ("fonts",)):
            cand = parent.joinpath(*rel, "IBMPlexSans-Regular.ttf")
            if cand.exists():
                return cand.parent
    return None


def resolve_font_path(family: str, weight: str = "regular") -> Optional[str]:
    """Return a local filesystem path to the bundled Plex TTF, or None.

    Fetches from bus://assets/fonts/ (and caches) when the repo copy is
    not present — a deployed widget surface needs only bus access.
    """
    fname = font_file_name(family, weight)
    if fname in _path_cache:
        return _path_cache[fname]

    env_dir = os.environ.get("COMPOSEIO_FONT_DIR")
    candidates = []
    if env_dir:
        candidates.append(Path(env_dir) / fname)
    repo = _repo_fonts_dir()
    if repo is not None:
        candidates.append(repo / fname)
    candidates.append(_cache_dir() / fname)

    for cand in candidates:
        if cand.exists():
            _path_cache[fname] = str(cand)
            return str(cand)

    # Bus-native fetch + cache
    try:
        from composeio.resolve import resolve_bytes
        data = resolve_bytes(BUS_FONT_PREFIX + fname)
        target = _cache_dir() / fname
        target.write_bytes(data)
        _path_cache[fname] = str(target)
        return str(target)
    except Exception:
        return None
