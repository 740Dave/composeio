"""Compliance tests for the CIC composition DERIVATION + manifest + linter
([SPEC:CXC-COMP], [CXC-COMP-VERIFY]).

Covers the machine gates of the composition-derivation slice:

  COMP-C1  layer order is DERIVED from SVG group order, deterministically
  COMP-C2  re-ordering groups in the SVG re-derives composition, no code change
  COMP-C3  the manifest is derived + consumed; absent, in-memory derivation matches
  COMP-C4  live-widget layers interleave with art by SVG z-position
  COMP-C6  slot:<widget>@<binding> parses (first-@, hyphen-safe); unknown-kind ->
           placeholder + warning
  COMP-C7  slot styling read from inner <text> (size on <text>, not <tspan>)
  COMP-C8  a widget KIND names interaction, not appearance
  COMP-C9  ref: groups are skipped (prefix + the *-for-inspiration alias)
  COMP-C13 the linter IS the derivation in --check mode; ERROR vs WARNING classes;
           the bake gate refuses on ERROR
"""

from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from composeio.render.composition import (CompositionManifest, classify, derive,
                                          is_registered_kind, lint,
                                          manifest_path_for, parse_slot)

BASE_SVG = os.path.join(   # NEUTRAL base layout fixture (same slot grammar)
    REPO_ROOT, "python", "tests", "fixtures", "neutral", "base_ux.svg")


# ---------------------------------------------------------------------------
# fixtures — minimal hand-authored SVGs exercising exact behaviors
# ---------------------------------------------------------------------------
def _svg(groups: str, viewbox: str = "0 0 100 100") -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
            f'viewBox="{viewbox}">{groups}</svg>')


def _g(label: str, inner: str = "", gid: str = "") -> str:
    idattr = f' id="{gid}"' if gid else ""
    return f'<g{idattr} inkscape:label="{label}">{inner}</g>'


# an SVG mixing backdrop, a nested slot between backdrop and overlay, and a ref
INTERLEAVE_SVG = _svg(
    _g("black_background", '<rect id="bg" x="0" y="0" width="100" height="100"/>')
    + _g("standard-ux-layout",
         '<rect id="axis" x="10" y="10" width="80" height="5"/>'
         + _g("slot:gauge@velocity",
              '<rect id="ph" inkscape:label="button-shape" x="20" y="30" '
              'width="40" height="40" fill="#123456" ry="4"/>'
              '<text id="lbl" inkscape:label="button-text-label" x="20" y="35" '
              'style="font-size:6.5;fill:#ffcc00;font-family:Tiny5">V</text>'))
    + _g("optional-common-decorators",
         '<path id="ov" d="M0 0 L10 0 L10 10 Z"/>')
    + _g("cic-reference-art-for-inspiration-not-coding",
         '<rect id="refart" x="0" y="0" width="5" height="5"/>'))


# ---------------------------------------------------------------------------
# COMP-C1 — deterministic derivation, document-order z
# ---------------------------------------------------------------------------
def test_c1_deterministic_byte_identical():
    m1 = derive(INTERLEAVE_SVG)
    m2 = derive(INTERLEAVE_SVG)
    assert m1.to_json() == m2.to_json()


def test_c1_zorder_is_document_order_no_reordering():
    m = derive(INTERLEAVE_SVG)
    labels = [l.label for l in m.layers]
    # document order: bg, standard-ux-layout, its nested gauge slot, then overlay
    assert labels == ["black_background", "standard-ux-layout",
                      "slot:gauge@velocity", "optional-common-decorators"]
    assert [l.z for l in m.layers] == list(range(len(m.layers)))


def test_c1_real_base_svg_derives():
    m = derive(BASE_SVG)
    slots = {l.slot.widget + "@" + l.slot.binding
             for l in m.slot_layers() if l.slot and l.slot.well_formed}
    assert "radio@mode" in slots
    assert "button@reboot" in slots
    assert "button@shut-down" in slots     # hyphenated binding preserved
    assert "text@firmware" in slots


# ---------------------------------------------------------------------------
# COMP-C2 — re-ordering groups re-derives, no code change
# ---------------------------------------------------------------------------
def test_c2_reorder_groups_changes_derived_z():
    # screen-matte BELOW content (matte drawn first -> lower z, under content)
    below = _svg(_g("screen-area", '<path id="m" d="M0 0 L100 0 L100 100 L0 100 Z"/>')
                 + _g("ext-view", '<image id="v" x="0" y="0" width="100" height="80"/>'))
    # screen-matte ABOVE content (only the SVG changed — no code edit)
    above = _svg(_g("ext-view", '<image id="v" x="0" y="0" width="100" height="80"/>')
                 + _g("screen-area", '<path id="m" d="M0 0 L100 0 L100 100 L0 100 Z"/>'))
    zb = {l.label: l.z for l in derive(below).layers}
    za = {l.label: l.z for l in derive(above).layers}
    assert zb["screen-area"] < zb["ext-view"]        # matte under content
    assert za["screen-area"] > za["ext-view"]        # matte over content
    # same deterministic function, different SVG -> different order, no code path


# ---------------------------------------------------------------------------
# COMP-C3 — manifest derived + consumed; absent -> in-memory matches
# ---------------------------------------------------------------------------
def test_c3_manifest_roundtrip_json():
    m = derive(INTERLEAVE_SVG)
    text = m.to_json()
    back = CompositionManifest.from_json(text)
    assert back.to_json() == text
    # equals the in-memory derivation
    assert [l.label for l in back.layers] == [l.label for l in m.layers]


def test_c3_sidecar_equals_in_memory(tmp_path):
    svg = tmp_path / "screen.svg"
    svg.write_text(INTERLEAVE_SVG)
    m = derive(str(svg))
    sidecar = manifest_path_for(str(svg))
    assert sidecar.endswith("screen.composition.json")
    with open(sidecar, "w") as fh:
        fh.write(m.to_json())
    # consume the baked sidecar; delete it; derive in memory -> identical order
    baked = CompositionManifest.from_json(open(sidecar).read())
    os.remove(sidecar)
    in_mem = derive(str(svg))
    assert [l.label for l in baked.layers] == [l.label for l in in_mem.layers]
    assert [l.z for l in baked.layers] == [l.z for l in in_mem.layers]


# ---------------------------------------------------------------------------
# COMP-C4 — live-widget layers interleave with art by SVG z-position
# ---------------------------------------------------------------------------
def test_c4_slot_interleaves_between_backdrop_and_overlay():
    m = derive(INTERLEAVE_SVG)
    z = {l.label: l.z for l in m.layers}
    assert z["standard-ux-layout"] < z["slot:gauge@velocity"]      # above backdrop
    assert z["slot:gauge@velocity"] < z["optional-common-decorators"]  # below overlay


# ---------------------------------------------------------------------------
# COMP-C6 — slot grammar parse (first-@, hyphen-safe); unknown-kind -> warn
# ---------------------------------------------------------------------------
def test_c6_parse_first_at_hyphen_safe():
    b = parse_slot("slot:button@shut-down")
    assert b.widget == "button" and b.binding == "shut-down" and b.well_formed
    b2 = parse_slot("slot:graph@velocity-history")
    assert b2.widget == "graph" and b2.binding == "velocity-history"


def test_c6_missing_at_is_not_wellformed():
    b = parse_slot("slot:text:message")     # the committed-base colon typo
    assert not b.well_formed


def test_c6_unknown_kind_placeholder_and_warning():
    svg = _svg(_g("std-backdrop",
                  _g("slot:hologram@target",
                     '<rect id="p" x="1" y="1" width="9" height="9"/>')))
    m = derive(svg)
    slot = m.slot_layers()[0]
    assert slot.slot.widget == "hologram" and not slot.slot.registered
    assert slot.placeholder                                   # static child present
    assert any(d.code == "unregistered-kind" for d in m.warnings)


def test_c6_same_kind_serves_many_define_once():
    # two gauge slots across two screens both resolve the SAME registered kind
    a = derive(_svg(_g("s", _g("slot:gauge@velocity",
                               '<rect x="0" y="0" width="1" height="1"/>'))))
    b = derive(_svg(_g("s", _g("slot:gauge@altitude",
                               '<rect x="0" y="0" width="1" height="1"/>'))))
    assert a.slot_layers()[0].slot.widget == "gauge"
    assert b.slot_layers()[0].slot.widget == "gauge"


# ---------------------------------------------------------------------------
# COMP-C7 — styling from inner <text> (size on <text>, not <tspan>)
# ---------------------------------------------------------------------------
def test_c7_size_on_text_is_honored():
    m = derive(INTERLEAVE_SVG)
    slot = next(l for l in m.slot_layers() if l.slot.binding == "velocity")
    assert slot.text_style is not None
    assert slot.text_style.font_size == pytest.approx(6.5)
    assert slot.text_style.fill == "#ffcc00"
    assert slot.text_style.font_family == "Tiny5"


def test_c7_size_only_on_tspan_not_honored():
    svg = _svg(_g("s", _g("slot:text@firmware",
                          '<text id="t" x="0" y="0">'
                          '<tspan style="font-size:9">HI</tspan></text>')))
    slot = derive(svg).slot_layers()[0]
    assert slot.text_style.font_size is None       # size on <tspan> NOT honored
    assert slot.text_style.tspan_only_size is True


def test_c7_shape_fill_and_ry_from_inner_shape():
    m = derive(INTERLEAVE_SVG)
    slot = next(l for l in m.slot_layers() if l.slot.binding == "velocity")
    assert slot.shape_fill == "#123456"
    assert slot.shape_ry == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# COMP-C8 — a widget KIND names interaction, not appearance
# ---------------------------------------------------------------------------
def test_c8_two_shapes_same_button_kind():
    m = derive(BASE_SVG)
    buttons = [l for l in m.slot_layers()
               if l.slot and l.slot.widget == "button" and l.slot.well_formed]
    kinds = {l.slot.widget for l in buttons}
    assert kinds == {"button"}                     # differing shapes, one kind
    assert len(buttons) >= 2


def test_c8_appearance_kind_is_unregistered():
    assert not is_registered_kind("custom-button")
    assert not is_registered_kind("round-button")
    assert is_registered_kind("button")


# ---------------------------------------------------------------------------
# COMP-C9 — ref: groups skipped (prefix + alias)
# ---------------------------------------------------------------------------
def test_c9_ref_prefix_and_alias_skipped():
    m = derive(INTERLEAVE_SVG)
    labels = [l.label for l in m.layers]
    assert "cic-reference-art-for-inspiration-not-coding" not in labels
    assert "cic-reference-art-for-inspiration-not-coding" in m.skipped_refs


def test_c9_classify_categories():
    assert classify("slot:gauge@velocity") == "slot"
    assert classify("ref:base-chrome") == "ref"
    assert classify("cic-reference-art-for-inspiration-not-coding") == "ref"
    assert classify("something-for-inspiration-not-coding") == "ref"
    assert classify("black_background") == "backdrop"


def test_c9_ref_mints_no_layer_no_slot():
    # a ref group that (wrongly) contains a slot: its subtree is skipped entirely
    svg = _svg(_g("ref:base-chrome",
                  _g("slot:gauge@velocity",
                     '<rect x="0" y="0" width="1" height="1"/>')))
    m = derive(svg)
    assert m.layers == []                          # nothing rendered
    assert not m.slot_layers()


# ---------------------------------------------------------------------------
# COMP-C13 — the linter IS the derivation in --check mode
# ---------------------------------------------------------------------------
def test_c13_lint_clean_screen_passes():
    svg = _svg(_g("std-backdrop",
                  _g("slot:gauge@velocity",
                     '<rect x="0" y="0" width="1" height="1"/>')))
    r = lint(svg, context="screen")
    assert r.ok
    assert not r.errors


def test_c13_invalid_grammar_is_error():
    r = lint(_svg(_g("s", _g("slot:text:message", '<rect x="0" y="0" width="1" height="1"/>'))),
             context="screen")
    assert not r.ok
    assert any(d.code == "slot-grammar" for d in r.errors)


def test_c13_unregistered_kind_is_error():
    r = lint(_svg(_g("s", _g("slot:hologram@target",
                             '<rect x="0" y="0" width="1" height="1"/>'))),
             context="screen")
    assert not r.ok
    assert any(d.code == "unregistered-kind" for d in r.errors)


def test_c13_appearance_kind_error_has_hint():
    r = lint(_svg(_g("s", _g("slot:round-button@go",
                             '<rect x="0" y="0" width="1" height="1"/>'))),
             context="screen")
    assert not r.ok
    d = next(d for d in r.errors if d.code == "unregistered-kind")
    assert "INTERACTION" in d.message


def test_c13_missing_required_label_is_error():
    # a 'base' context requires slot:radio@mode ([DEC-CXC-COMP-SLOT-DELIMITER-AT]
    # retired the legacy radio-button-group-bounding-box label); its ABSENCE is an
    # ERROR.  (A base WITH slot:radio@mode is conformant — see the positive test.)
    r = lint(_svg(_g("standard-ux-layout",
                     _g("frame", '<rect x="0" y="0" width="1" height="1"/>'))),
             context="base")
    assert not r.ok
    d = next(d for d in r.errors if d.code == "missing-required-label")
    assert "slot:radio@mode" in d.message


def test_c13_unmapped_group_is_warning_not_error():
    r = lint(_svg(_g("mysterious-widget-group",
                     '<rect x="0" y="0" width="1" height="1"/>')),
             context="screen")
    assert r.ok                                    # WARNING does not block bake
    assert any(d.code == "unmapped-group" for d in r.warnings)


def test_c13_duplicate_binding_is_error():
    svg = _svg(_g("s",
                  _g("slot:gauge@velocity", '<rect x="0" y="0" width="1" height="1"/>')
                  + _g("slot:gauge@velocity", '<rect x="0" y="0" width="1" height="1"/>')))
    r = lint(svg, context="screen")
    assert not r.ok
    assert any(d.code == "duplicate-binding" for d in r.errors)


def test_c13_ref_with_slot_label_is_error():
    svg = _svg(_g("ref:base-chrome",
                  _g("slot:gauge@velocity", '<rect x="0" y="0" width="1" height="1"/>')))
    r = lint(svg, context="screen")
    assert not r.ok
    assert any(d.code == "ref-with-slot" for d in r.errors)


def test_c13_lint_shares_derivation_same_manifest():
    # lint IS the derivation in check mode: the report carries the SAME manifest
    r = lint(INTERLEAVE_SVG, context="screen")
    m = derive(INTERLEAVE_SVG)
    assert r.manifest is not None
    assert [l.label for l in r.manifest.layers] == [l.label for l in m.layers]


def test_c13_bake_gate_refuses_nonconformant_base():
    # the bake gate REFUSES a non-conformant base with READABLE diagnostics
    # rather than an opaque crash ([CXC-COMP-LINT]).  Synthetic broken base:
    # a slot-grammar colon typo (`slot:text:message`, not `@`) AND a missing
    # required base label (no `slot:radio@mode`) — both surfaced as ERRORs.
    broken = _svg(_g("slot:text:message", "<text>x</text>")
                  + _g("frame", '<rect x="0" y="0" width="1" height="1"/>'))
    r = lint(broken, context="base")
    assert not r.ok
    codes = {d.code for d in r.errors}
    assert "slot-grammar" in codes            # the colon-not-@ typo
    assert "missing-required-label" in codes  # slot:radio@mode absent

