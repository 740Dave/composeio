"""The root GUTTER is part of the contract, and the two tiers disagreed.

Spec: [CXC-R-SCENE-GEOMETRY-POLICY] (corpora/platform-specs/12-composeux-render-engine.lts).

THE DEFECT.  `python/composeio/render/engine/parser.py` wraps a nested-dialect
scene in a root FlexContainer whose padding defaults to 12 and whose gap
defaults to 12.  `cyd/src/compose_scene.cpp` builds the same scene with
`build_children(s, widgets, canvas, 0.0f, 0.0f)` and never reads `padding` or
`gap` at all.

So the SAME scene rendered at two different origins on the two tiers, with two
different usable widths, and nothing compared them.  Measured on
engine_thrust.compose.yaml (240x320):

    element        desktop (pad=12)   device (pad=0)   scene declares
    header         (12, 20)           ( 0,  8)         x=0  y=8
    eng_l          (52, 84)           (40, 72)         x=40 y=72
    flank_banner   (24,304) h=22      (12,292) h=22    x=12 y=292
                   bottom = 326       bottom = 314     glass height = 320

The desktop pushed every element +12,+12, cut the usable width 240 -> 216, and
clipped `flank_banner` 6px past the bottom of the glass.  That is the reported
"padding on the left and top" and the reported clipping, both, exactly.

This is the SAME ROOT-CAUSE CLASS as the font defect it was found alongside: a
default implemented on one tier and not the other, with no test comparing them.

Headless: parses and arranges, renders nothing.  No ``needs_glass`` marker.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_REPO = Path(__file__).resolve().parents[3]
_COMPOSE_SCENE_CPP = _REPO / "cyd" / "src" / "compose_scene.cpp"
_PANEL = (_REPO / "compose_projects" / "bsg" / "game_hardware"
          / "engine_thrust_panel" / "engine_thrust.compose.yaml")


def _firmware_root_offset() -> tuple:
    """The (x, y) the firmware builds a scene's top-level widgets at."""
    if not _COMPOSE_SCENE_CPP.is_file():
        pytest.skip("cyd/src/compose_scene.cpp not present")
    src = _COMPOSE_SCENE_CPP.read_text(errors="replace")
    m = re.search(r"build_children\(\s*s\s*,\s*widgets\s*,\s*canvas\s*,\s*"
                  r"([\d.]+)f\s*,\s*([\d.]+)f\s*\)", src)
    assert m, "could not find the firmware's root build_children call"
    return (float(m.group(1)), float(m.group(2)))


def _arrange(cfg: dict) -> dict:
    from composeio.render.engine.parser import parse_widget_tree
    from composeio.render.engine.layout import Constraints, Rect

    comp = parse_widget_tree(copy.deepcopy(cfg))
    root = comp.root
    root.measure(Constraints(comp.width, comp.height))
    root.arrange(Rect(0, 0, comp.width, comp.height))
    out = {}

    def walk(w):
        r = getattr(w, "arranged_rect", None)
        wid = getattr(w, "widget_id", None)
        if r is not None and wid:
            out[wid] = (r.x, r.y, r.w, r.h)
        for ch in (getattr(w, "children", None) or []):
            walk(ch)

    walk(root)
    return out, comp


def _panel_cfg() -> dict:
    if not _PANEL.is_file():
        pytest.skip("engine_thrust.compose.yaml not present")
    return yaml.safe_load(_PANEL.read_text())


def test_the_firmware_applies_no_root_gutter():
    """Pins the reference tier's behaviour so a firmware change is visible."""
    assert _firmware_root_offset() == (0.0, 0.0)


def test_a_fixed_size_scene_arranges_at_the_SAME_origin_as_the_firmware():
    """[CXC-R-SCENE-GEOMETRY-POLICY] -- the cross-tier assertion.

    A scene declaring `fixed-size:` states its coordinates are absolute
    against a named physical glass.  The desktop must therefore place its
    top-level widgets at the same origin the firmware does, which is (0, 0).
    """
    cfg = _panel_cfg()
    cfg.setdefault("design", {})["fixed-size"] = "240x320 test panel"
    arranged, comp = _arrange(cfg)

    fx, fy = _firmware_root_offset()
    declared = {w["id"]: (float(w.get("x", 0)), float(w.get("y", 0)))
                for w in cfg["widgets"][0]["children"] if "id" in w}

    bad = []
    for wid, (dx, dy) in declared.items():
        if wid not in arranged:
            continue
        x, y, _, _ = arranged[wid]
        if (x, y) != (dx + fx, dy + fy):
            bad.append(f"{wid}: arranged ({x:g},{y:g}) but declares "
                       f"({dx:g},{dy:g}) and the firmware origin is ({fx:g},{fy:g})")
    assert not bad, (
        "a fixed-size scene does not arrange where it declares:\n  "
        + "\n  ".join(bad))


def test_a_fixed_size_scene_keeps_its_FULL_declared_width():
    """The gutter did not only move content -- it shrank the coordinate space.

    240 -> 216 usable width means a child declared `w: 240` overflows and is
    clipped on the right, which is the other half of the reported symptom.
    """
    cfg = _panel_cfg()
    cfg.setdefault("design", {})["fixed-size"] = "240x320 test panel"
    arranged, comp = _arrange(cfg)
    scene = arranged["scene"]
    assert (scene[0], scene[1]) == (0.0, 0.0), (
        f"the scene container starts at ({scene[0]:g},{scene[1]:g}), not the origin")
    assert scene[2] == comp.width, (
        f"the scene container is {scene[2]:g}px wide but the glass is "
        f"{comp.width}px; the root gutter ate {comp.width - scene[2]:g}px of the "
        f"coordinate space the author addressed")


def test_nothing_in_a_fixed_size_scene_falls_off_the_declared_glass():
    """The clip, asserted directly.

    `flank_banner` is `visible:`-gated on throttle >= 90, so it is INVISIBLE at
    rest -- which is why the clip only appears when a throttle is pushed past
    90 and why a static screenshot at idle looks fine.
    """
    cfg = _panel_cfg()
    cfg.setdefault("design", {})["fixed-size"] = "240x320 test panel"
    arranged, comp = _arrange(cfg)

    over = []
    for wid, (x, y, w, h) in arranged.items():
        if wid in ("root", "scene"):
            continue
        if y + h > comp.height or x + w > comp.width:
            over.append(f"{wid}: rect ({x:g},{y:g},{w:g},{h:g}) exceeds "
                        f"{comp.width}x{comp.height}")
    assert not over, ("elements fall off the declared glass:\n  "
                      + "\n  ".join(over))


def test_the_12px_gutter_still_applies_to_scenes_that_did_not_opt_out():
    """The fix is ADDITIVE -- every existing desktop scene is untouched.

    The console family relies on the 12px chrome gutter.  Zeroing it globally
    would have been a much larger defect than the one being fixed.
    """
    cfg = _panel_cfg()
    cfg.get("design", {}).pop("fixed-size", None)
    cfg.get("design", {}).pop("fixed_size", None)
    arranged, _ = _arrange(cfg)
    x, y, _, _ = arranged["scene"]
    assert (x, y) == (12.0, 12.0), (
        f"a scene that did NOT declare fixed-size arranged at ({x:g},{y:g}); "
        f"the default gutter must be unchanged for it")


def test_an_explicit_padding_still_wins_over_the_fixed_size_default():
    """fixed-size supplies a DEFAULT, it does not seize the knob."""
    cfg = _panel_cfg()
    d = cfg.setdefault("design", {})
    d["fixed-size"] = "240x320 test panel"
    d["padding"] = 4
    d["gap"] = 0
    arranged, _ = _arrange(cfg)
    x, y, _, _ = arranged["scene"]
    assert (x, y) == (4.0, 4.0)
