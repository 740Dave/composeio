"""
Named-SVG-RESOURCE extractor (`composeio.maker.svg_resources`).

Pins the [CXC-CIC-SVG-MAP] extraction foundation:

  * the semantic-palette extractor reads all FOUR theme palettes out of the real
    tracked ``colonial_base_ux.svg``, each yielding a base + highlight hex, and
    the CIC default is exactly the ratified ``#762400`` / ``#CD671D``
    ([DD-CIC-THEME]);
  * the GENERALIZED primitive (`extract_named_resources` on an arbitrary
    group-label path) returns the labeled children + their fill/geometry/text —
    the seam every re-scan consumer sits on — verified against a small fixture;
  * fill normalization handles both ``style="fill:#..."`` and a ``fill=`` attr
    and both hex shorthands.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.maker.svg_resources import (  # noqa: E402
    ResourceElement,
    extract_named_resources,
    extract_semantic_palettes,
    find_labeled_group,
    normalize_fill,
)

COLONIAL_SVG = os.path.join(
    REPO_ROOT, "compose_projects", "bsg", "resources",
    "screen_base_layout", "colonial_base_ux.svg")

# The semantic palettes MOVED to a dedicated file (David 2026-07-31) — the themes
# sit directly under a layer with NO `semantic_color_palettes` wrapper.  This is
# the canonical palette source now ([DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]).
SEMANTIC_COLORS_SVG = os.path.join(
    REPO_ROOT, "compose_projects", "bsg", "resources",
    "screen_base_layout", "colonial_base_ux_semantic_colors.svg")

EXPECTED_THEMES = {"cic", "sickbay", "flight_deck", "raptor"}


# ---------------------------------------------------------------------------
# the real artwork — the acceptance the task names
# ---------------------------------------------------------------------------
def test_colonial_svg_is_tracked():
    assert os.path.exists(COLONIAL_SVG), COLONIAL_SVG
    assert os.path.exists(SEMANTIC_COLORS_SVG), SEMANTIC_COLORS_SVG


def test_all_four_themes_yield_base_and_highlight():
    palettes = extract_semantic_palettes(SEMANTIC_COLORS_SVG)
    assert set(palettes) == EXPECTED_THEMES
    for theme, pair in palettes.items():
        assert set(pair) == {"base", "highlight"}, theme
        for role, hexv in pair.items():
            assert normalize_fill(hexv) == hexv       # already normalized
            assert len(hexv) in (7, 9) and hexv[0] == "#", (theme, role, hexv)


def test_cic_default_palette_is_ratified_amber():
    palettes = extract_semantic_palettes(SEMANTIC_COLORS_SVG)
    assert palettes["cic"] == {"base": "#762400", "highlight": "#CD671D"}


def test_dedicated_file_has_no_wrapper_group_but_still_extracts():
    """The dedicated palette file has the themes directly under a layer, with NO
    `semantic_color_palettes` wrapper — the fallback theme-shaped-group scan must
    still find all four (the source-of-truth move needs no caller change)."""
    assert find_labeled_group(SEMANTIC_COLORS_SVG, "semantic_color_palettes") is None
    assert set(extract_semantic_palettes(SEMANTIC_COLORS_SVG)) == EXPECTED_THEMES


# ---------------------------------------------------------------------------
# the generalized primitive — a small fixture (the load-bearing reuse)
# ---------------------------------------------------------------------------
FIXTURE = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     viewBox="0 0 100 100">
  <g inkscape:label="row3-tabs" id="grp">
    <g id="anon-wrapper">
      <rect inkscape:label="tab1" id="t1" x="1" y="2" width="10" height="20"
            style="display:inline;fill:#12ab34;stroke:none"/>
    </g>
    <rect inkscape:label="tab2" id="t2" x="30" y="2" width="10" height="20"
          fill="#f0c"/>
    <text inkscape:label="tab3-text" id="t3">HELM</text>
  </g>
</svg>"""


def test_generalized_extractor_returns_labeled_children_and_props():
    els = extract_named_resources(FIXTURE, "row3-tabs")
    by_label = {e.label: e for e in els}
    # anonymous wrapper <g> is transparent — tab1 is still found beneath it
    assert set(by_label) == {"tab1", "tab2", "tab3-text"}

    tab1 = by_label["tab1"]
    assert isinstance(tab1, ResourceElement)
    assert tab1.fill == "#12AB34"                     # from style="fill:#..."
    assert tab1.geometry == (1.0, 2.0, 10.0, 20.0)    # x, y, w, h

    assert by_label["tab2"].fill == "#FF00CC"         # fill= attr + 3-digit expand
    assert by_label["tab3-text"].text == "HELM"


# A wrapper-style fixture (the embedded-mockup layout) for the PATH feature —
# kept inline so this test doesn't depend on where David currently keeps the
# real palettes (they moved to a dedicated, wrapper-less file).
PALETTE_FIXTURE = """<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     viewBox="0 0 100 100">
  <g inkscape:label="semantic_color_palettes">
    <g inkscape:label="cic">
      <rect inkscape:label="base" fill="#762400"/>
      <rect inkscape:label="highlight" fill="#CD671D"/>
    </g>
    <g inkscape:label="sickbay">
      <rect inkscape:label="base" fill="#3E566B"/>
      <rect inkscape:label="highlight" fill="#56728D"/>
    </g>
  </g>
</svg>"""


def test_label_path_disambiguates_repeated_label():
    # 'base' repeats once per theme; a PATH selects the right one
    cic = find_labeled_group(PALETTE_FIXTURE, "semantic_color_palettes/cic")
    assert cic is not None and cic.label == "cic"
    assert cic.child("base").fill == "#762400"
    assert cic.child("highlight").fill == "#CD671D"
    # and the named-wrapper path still yields all themes (backward-compat)
    assert set(extract_semantic_palettes(PALETTE_FIXTURE)) == {"cic", "sickbay"}


def test_missing_group_returns_empty():
    assert extract_named_resources(FIXTURE, "no-such-layer") == []


@pytest.mark.parametrize("raw,expected", [
    ("#762400", "#762400"),
    ("#cd671d", "#CD671D"),
    ("#f0c", "#FF00CC"),
    ("#1234", "#11223344"),
    ("  #ABCDEF  ", "#ABCDEF"),
    ("none", None),
    ("url(#grad1)", None),
    (None, None),
])
def test_normalize_fill(raw, expected):
    assert normalize_fill(raw) == expected
