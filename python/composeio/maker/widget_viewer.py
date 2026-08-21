"""
Maker WIDGET VIEWER — a 3-tab ComposeUX app over one maker `.compose.yaml`
([FLOW:MAKER-PROJECT], native pass 2026-07-26).

Point it at any maker widget's `.compose.yaml` and it opens a generous window
(David 2026-07-26: TALL + WIDE by default — the whole hookup guide visible
without scrolling) with three tabs:

  * **YAML**          — the widget's `.compose.yaml` source, read-only +
                        scrollable (the markdown viewer hosting a fenced
                        ``yaml`` block — reuse, not a new text widget);
  * **Hookup Guide**  — the NATIVE `hookup` widget rendering the scene's
                        `hookup:` block from the shared layout intermediate
                        (real Fritzing art via the lowered-cache, big labels);
  * **Preview**       — the widget's OWN UX (its `widgets:` scene embedded
                        verbatim, namespaces included — preview == deployed by
                        construction).  A HEADLESS widget shows the centered
                        placeholder "This Widget has No UX" instead.

HEADLESS classification (`scene_preview_kind`): a scene with no `widgets:` is
headless outright; a scene that carries a `hookup:` block whose widgets are
ONLY console monitors of the hookup's published slot namespaces (every bound
namespace ⊆ the hookup's `publishes:` slots — the dual-throttle case, whose
gauges just watch throttle/left+right) is ALSO headless: those widgets watch
the device, they are not the device's UX.  Any widget bound beyond the slots
(or any widget with no hookup at all) makes the scene a real UX.

Launch::

    PYTHONPATH=python python3 -m composeio.maker.widget_viewer \\
        python/composeio/render/examples/dual_throttle.compose.yaml

    # headless proofs (one PNG per tab):
    ... widget_viewer <scene.yaml> --proof <out_dir>
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, List, Optional, Set

import yaml

# generous default window (David 2026-07-26: the whole guide without scroll)
DEFAULT_WIDTH = 1560
DEFAULT_HEIGHT = 1120

NO_UX_TEXT = "This Widget has No UX"

TAB_LABELS = ["YAML", "Hookup Guide", "Preview"]


# ---------------------------------------------------------------------------
# headless classification
# ---------------------------------------------------------------------------

def _collect_namespaces(node: Any, out: Set[str]) -> None:
    """Every namespace a widget spec binds: `namespace:` strings and
    `namespaces:` lists, recursively through children/tabs/sections."""
    if isinstance(node, dict):
        ns = node.get("namespace")
        if isinstance(ns, str) and ns:
            out.add(ns)
        nss = node.get("namespaces")
        if isinstance(nss, list):
            out.update(str(n) for n in nss if isinstance(n, str))
        for key in ("children", "tabs", "sections"):
            _collect_namespaces(node.get(key), out)
    elif isinstance(node, list):
        for item in node:
            _collect_namespaces(item, out)


def scene_preview_kind(cfg: Dict[str, Any]) -> str:
    """"ux" when the scene carries its own renderable UX; "headless" when
    there is nothing to preview (no widgets, or hookup-slot monitors only —
    see the module docstring for the rule)."""
    widgets = cfg.get("widgets") or []
    if not isinstance(widgets, list) or not widgets:
        return "headless"
    hookup = cfg.get("hookup")
    if isinstance(hookup, dict):
        slots = {w.get("publishes") for w in (hookup.get("wires") or [])
                 if isinstance(w, dict) and w.get("publishes")}
        used: Set[str] = set()
        _collect_namespaces(widgets, used)
        if used and used <= slots:
            return "headless"          # console monitors of the device only
    return "ux"


# ---------------------------------------------------------------------------
# scene builder
# ---------------------------------------------------------------------------

def _placeholder_children(width: int, height: int) -> List[dict]:
    """The centered no-UX placeholder (a fixed frame so the text truly
    centers in the tab pane)."""
    fw = width - 32
    fh = height - 120
    return [{
        "type": "pixel_stack", "id": "no_ux_frame",
        "width": fw, "height": fh,
        "background": "#10141C", "border": "#232B38",
        "corner_radius": 8, "clip": True,
        "children": [
            {"type": "label", "id": "no_ux_label", "x": 0,
             "y": fh // 2 - 32, "width": fw, "height": 36,
             "text": NO_UX_TEXT, "align": "center",
             "font_size": 22, "font_weight": "bold", "color": "#8A97A8"},
            {"type": "label", "id": "no_ux_sub", "x": 0,
             "y": fh // 2 + 6, "width": fw, "height": 20, "align": "center",
             "text": "headless device — see the Hookup Guide tab",
             "font_size": 12, "color": "#5C6E82",
             "font_family": "monospace"},
        ],
    }]


def build_viewer_scene(target_path: str, *,
                       width: int = DEFAULT_WIDTH,
                       height: int = DEFAULT_HEIGHT,
                       initial_tab: int = 1) -> dict:
    """The 3-tab viewer scene dict for the maker widget at *target_path*.

    Pure function of the target file's bytes — the YAML tab shows exactly the
    source, the Hookup tab points the native widget at the SAME file, and a
    UX preview embeds the target's own `widgets:` specs verbatim (plus its
    `namespaces:`), so preview == deployed by construction."""
    target_abs = os.path.abspath(target_path)
    with open(target_abs, "r", encoding="utf-8") as fh:
        source = fh.read()
    cfg = yaml.safe_load(source) or {}
    if not isinstance(cfg, dict):
        cfg = {}
    name = os.path.basename(target_abs).replace(".compose.yaml", "")

    kind = scene_preview_kind(cfg)
    if kind == "ux":
        preview_children = list(cfg.get("widgets") or [])
    else:
        preview_children = _placeholder_children(width, height)

    scene: Dict[str, Any] = {
        "app": {"id": f"widget-viewer-{name}", "transports": []},
        "design": {"title": f"Widget Viewer · {name}",
                   "width": int(width), "height": int(height),
                   "theme": "dark"},
        "widgets": [{
            "type": "tab", "id": "viewer_tabs",
            "initial_tab": int(initial_tab),
            "tabs": [
                {"label": TAB_LABELS[0], "children": [
                    {"type": "markdown_viewer", "id": "yaml_source",
                     "content": f"```yaml\n{source}\n```",
                     "font_size": 13}]},
                {"label": TAB_LABELS[1], "children": [
                    {"type": "hookup", "id": "hookup_guide",
                     "src": target_abs,
                     "width": int(width) - 32,
                     "height": int(height) - 120}]},
                {"label": TAB_LABELS[2], "children": preview_children},
            ],
        }],
    }
    # a live UX preview needs the target's state topology on the design bus
    if kind == "ux" and isinstance(cfg.get("namespaces"), list):
        scene["namespaces"] = cfg["namespaces"]
    if kind == "ux" and isinstance(cfg.get("variables"), dict):
        scene["variables"] = cfg["variables"]
    return scene


# ---------------------------------------------------------------------------
# launch
# ---------------------------------------------------------------------------

def _render_tab_proofs(app: Any, out_dir: str, stem: str) -> List[str]:
    """One headless PNG per tab (the acceptance proof set)."""
    from composeio.render.widgets.tab import TabWidget
    tab = next(w for w in app.comp.root.iter_widgets()
               if isinstance(w, TabWidget))
    os.makedirs(out_dir, exist_ok=True)
    outs: List[str] = []
    for i, label in enumerate(TAB_LABELS):
        tab.set_active(i)
        slug = label.lower().replace(" ", "-")
        out = os.path.join(out_dir, f"{stem}.{slug}.png")
        app.render_headless(out)
        outs.append(out)
    return outs


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="composeio.maker.widget_viewer",
        description="3-tab viewer (YAML / Hookup Guide / Preview) for a "
                    "maker widget .compose.yaml")
    ap.add_argument("target", help="path to the widget's .compose.yaml")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    ap.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    ap.add_argument("--tab", type=int, default=1,
                    help="initial tab (0=YAML, 1=Hookup Guide, 2=Preview)")
    ap.add_argument("--proof", metavar="DIR", default=None,
                    help="headless: render one PNG per tab into DIR and exit")
    args = ap.parse_args(argv)

    scene = build_viewer_scene(args.target, width=args.width,
                               height=args.height, initial_tab=args.tab)

    from composeio.render.engine.runtime import RenderApp
    if args.proof:
        app = RenderApp.from_dict(scene, windowed=False)
        app.start()
        stem = os.path.basename(os.path.abspath(args.target)) \
            .replace(".compose.yaml", "")
        outs = _render_tab_proofs(app, args.proof, f"widget-viewer.{stem}")
        app.stop()
        for out in outs:
            print(out)
        return 0

    app = RenderApp.from_dict(scene, windowed=True)
    app.start()
    try:
        app.run()
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_viewer_scene", "scene_preview_kind", "main",
           "NO_UX_TEXT", "TAB_LABELS", "DEFAULT_WIDTH", "DEFAULT_HEIGHT"]
