"""Hand-maintained scene VARIANTS must not drift in semantics.

Spec: [CXC-VISION-SEMANTIC-REALIZATION] -- one semantic scene, many physical
realizations.  The engine thrust panel is currently THREE files that differ
only in geometry by intent:

    engine_thrust.compose.yaml           240x320   (Hosyond ESP32-S3 + Mac)
    engine_thrust_narrow.compose.yaml    172x320   (ESP32-C6-LCD-1.47)
    engine_thrust_480x320.compose.yaml   480x320   (C3 + ST7796)

Three files kept in sync BY HAND is the fork class [DX-NARROW-WAIST] exists to
prevent.  The correct end state is ONE screen-size-independent scene; that is
design work and is not what this module does.  What this module does is make
the fork's COST visible the moment it is paid, so the fork cannot quietly
diverge while the collapse is being designed.

Semantics = what the scene MEANS (namespaces, thresholds, gauge domain, the
face a given element is drawn in).  Geometry = x/y/w/h and font_size.  Only
the first is asserted here.

Headless: parses YAML, renders nothing.  No ``needs_glass`` marker by design.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parents[3]
_PANEL = (_REPO / "compose_projects" / "bsg" / "game_hardware"
          / "engine_thrust_panel")

VARIANTS = {
    "240x320": _PANEL / "engine_thrust.compose.yaml",
    "172x320": _PANEL / "engine_thrust_narrow.compose.yaml",
    "480x320": _PANEL / "engine_thrust_480x320.compose.yaml",
}

# Keys that are pure REALIZATION -- a variant is expected and allowed to
# differ here.  Everything else is semantic and must agree.
GEOMETRY_KEYS = {"x", "y", "w", "h", "width", "height", "font_size",
                 "corner_radius", "stroke_width"}


def _load(path: Path) -> dict:
    if not path.is_file():
        pytest.skip(f"{path.name} not present in this checkout")
    return yaml.safe_load(path.read_text())


def _walk(node, out: dict) -> dict:
    """id -> element spec, for every element carrying an ``id``."""
    if isinstance(node, dict):
        wid = node.get("id")
        if wid and "type" in node:
            out[wid] = node
        for v in node.values():
            _walk(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, out)
    return out


def _elements(path: Path) -> dict:
    return _walk(_load(path).get("widgets", []), {})


def _uncommented(path: Path) -> str:
    """Scene text with ``#`` comments stripped.

    These scenes carry long explanatory headers that mention `namespace:` and
    `expr:` in PROSE; matching those would make this module assert about
    documentation instead of about the scene.
    """
    return "\n".join(re.sub(r"#.*$", "", line)
                     for line in path.read_text().splitlines())


# ------------------------------------------------------------------ the forks

def test_variants_agree_on_the_namespaces_they_subscribe():
    """A variant that drifted onto a different namespace is a dark panel."""
    seen = {}
    for name, path in VARIANTS.items():
        ns = set()
        for m in re.finditer(r"namespace:\s*([\w/\-]+)", _uncommented(path)):
            ns.add(m.group(1))
        seen[name] = ns
    ref = seen["240x320"]
    for name, ns in seen.items():
        assert ns == ref, f"{name} subscribes {sorted(ns)}, 240x320 subscribes {sorted(ref)}"


def test_variants_agree_on_the_gauge_domain_and_the_flank_threshold():
    """min_val/max_val/segments and the AHEAD-FLANK expr are the SEMANTICS.

    A variant left on the pre-2026-08-15 0..255 domain would show a different
    thrust for the same wire value -- the semantic-realization claim failing
    on the numeric axis rather than the typographic one.
    """
    ref_domain = ref_expr = None
    for name, path in VARIANTS.items():
        txt = _uncommented(path)
        domain = sorted(set(re.findall(r"(?:min_val|max_val|segments):\s*(\d+)", txt)))
        exprs = sorted(set(re.findall(r'expr:\s*"([^"]+)"', txt)))
        exprs = [e for e in exprs if "throttle" in e]
        if ref_domain is None:
            ref_domain, ref_expr = domain, exprs
            continue
        assert domain == ref_domain, f"{name} gauge domain {domain} != {ref_domain}"
        assert exprs == ref_expr, f"{name} flank expr {exprs} != {ref_expr}"


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN FORK COST, not yet reconciled: eng_l_value/eng_r_value carry "
    "font_family: commp in the 480x320 variant and no family in the other "
    "two. strict=True so this FAILS as XPASS the moment the variants are "
    "collapsed -- the marker cannot outlive the defect."))
def test_the_same_element_is_drawn_in_the_same_FAMILY_in_every_variant():
    """THE JOIN between the font defect and the geometry fork.

    `eng_l_value` / `eng_r_value` carry `font_family: commp` in the 480x320
    variant and NO family at all in the 240x320 one.  Same logical element,
    same panel, two different faces -- decided by which file a device happened
    to boot.  This is the font defect and the fork defect being one defect.
    """
    per_variant = {name: _elements(path) for name, path in VARIANTS.items()}
    shared = set.intersection(*(set(e) for e in per_variant.values()))
    assert shared, "the variants share no element ids at all"

    disagreements = []
    for wid in sorted(shared):
        fams = {name: els[wid].get("font_family")
                for name, els in per_variant.items()
                if "font_size" in els[wid] or "font_family" in els[wid]}
        if len(set(fams.values())) > 1:
            disagreements.append(f"{wid}: {fams}")
    assert not disagreements, (
        "the same element is drawn in a different font family depending on "
        "which VARIANT of the scene a device booted:\n  "
        + "\n  ".join(disagreements))


@pytest.mark.xfail(strict=True, reason=(
    "KNOWN FORK COST, not yet reconciled: 172x320 lacks wiring+scale_hint; "
    "480x320 adds 5 tick labels + a footer and lacks scale_hint. strict=True "
    "so this FAILS as XPASS once the variants are collapsed."))
def test_variants_agree_on_which_elements_exist():
    """Content drift across the fork -- the cost, counted.

    Recorded so the collapse to one scene knows what it must reconcile, and
    so a NEW divergence fails here instead of being discovered on a device.
    """
    per_variant = {name: set(_elements(path)) for name, path in VARIANTS.items()}
    ref_name, ref = "240x320", per_variant["240x320"]
    diffs = []
    for name, ids in per_variant.items():
        if name == ref_name:
            continue
        only_here = sorted(ids - ref)
        only_ref = sorted(ref - ids)
        if only_here or only_ref:
            diffs.append(f"{name}: extra={only_here} missing={only_ref}")
    assert not diffs, (
        "hand-maintained variants have drifted in CONTENT, not just geometry:\n  "
        + "\n  ".join(diffs))
