"""
Maker WIDGET VIEWER — the 3-tab app over one `.compose.yaml`
(`composeio.maker.widget_viewer`, native pass 2026-07-26).

Pins:
  * scene structure: exactly the three tabs (YAML / Hookup Guide / Preview),
    YAML read-only source, Hookup = the native `hookup` widget at the SAME
    target file, generous default window;
  * headless classification: no `widgets:` -> headless; hookup-slot-monitor
    widgets only (the dual-throttle console gauges) -> headless; a widget
    bound beyond the slots -> a real UX preview embedding the target's specs
    verbatim (preview == deployed by construction);
  * the whole viewer renders headless to PNGs on every tab.
"""

from __future__ import annotations

import copy
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.maker.widget_viewer import (NO_UX_TEXT, TAB_LABELS,
                                           DEFAULT_HEIGHT, DEFAULT_WIDTH,
                                           _render_tab_proofs,
                                           build_viewer_scene,
                                           scene_preview_kind)

EXAMPLE = os.path.join(PY_ROOT, "composeio", "render", "examples",
                       "dual_throttle.compose.yaml")


def _example_cfg() -> dict:
    with open(EXAMPLE, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# headless classification
# ---------------------------------------------------------------------------

def test_no_widgets_is_headless():
    assert scene_preview_kind({"app": {"id": "x"}}) == "headless"
    assert scene_preview_kind({"widgets": []}) == "headless"


def test_dual_throttle_is_headless():
    """The dual-throttle controller: its gauges only MONITOR the hookup's
    published slots (throttle/left+right), so the device has no UX of its
    own — the Preview tab shows the placeholder."""
    assert scene_preview_kind(_example_cfg()) == "headless"


def test_widget_beyond_the_slots_is_a_real_ux():
    cfg = copy.deepcopy(_example_cfg())
    cfg["widgets"].append({"type": "button", "id": "b",
                           "namespace": "cabin/lights"})
    assert scene_preview_kind(cfg) == "ux"


def test_scene_without_hookup_but_with_widgets_is_a_ux():
    assert scene_preview_kind(
        {"widgets": [{"type": "label", "text": "hi"}]}) == "ux"


# ---------------------------------------------------------------------------
# scene structure
# ---------------------------------------------------------------------------

def test_viewer_scene_three_tabs_and_wiring():
    scene = build_viewer_scene(EXAMPLE)
    assert scene["design"]["width"] == DEFAULT_WIDTH >= 1400
    assert scene["design"]["height"] == DEFAULT_HEIGHT >= 1000
    tab = scene["widgets"][0]
    assert tab["type"] == "tab"
    labels = [t["label"] for t in tab["tabs"]]
    assert labels == TAB_LABELS == ["YAML", "Hookup Guide", "Preview"]

    # YAML tab: the read-only source (fenced) — byte-for-byte the file
    md = tab["tabs"][0]["children"][0]
    assert md["type"] == "markdown_viewer"
    with open(EXAMPLE, encoding="utf-8") as fh:
        src = fh.read()
    assert src in md["content"] and md["content"].startswith("```yaml")

    # Hookup tab: the NATIVE widget pointed at the SAME file
    hk = tab["tabs"][1]["children"][0]
    assert hk["type"] == "hookup"
    assert hk["src"] == os.path.abspath(EXAMPLE)

    # Preview tab (headless target): the exact placeholder text
    texts = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "label":
                texts.append(node.get("text"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(tab["tabs"][2])
    assert NO_UX_TEXT in texts


def test_viewer_scene_embeds_ux_widgets_verbatim(tmp_path):
    cfg = copy.deepcopy(_example_cfg())
    cfg["widgets"].append({"type": "button", "id": "b",
                           "namespace": "cabin/lights"})
    target = tmp_path / "ux.compose.yaml"
    target.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    scene = build_viewer_scene(str(target))
    preview = scene["widgets"][0]["tabs"][2]["children"]
    assert preview == cfg["widgets"]          # verbatim: preview == deployed
    assert scene["namespaces"] == cfg["namespaces"]


# ---------------------------------------------------------------------------
# headless render — every tab reaches a raster
# ---------------------------------------------------------------------------

def test_viewer_renders_all_three_tabs_headless(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    scene = build_viewer_scene(EXAMPLE, width=1200, height=900)
    app = RenderApp.from_dict(scene, windowed=False)
    app.start()
    outs = _render_tab_proofs(app, str(tmp_path), "viewer")
    app.stop()
    assert len(outs) == 3
    for out in outs:
        assert os.path.exists(out) and os.path.getsize(out) > 5000
