"""
[FLOW:MAKER-PROJECT] S5 — regenerate the dual-throttle project's derived artifacts.

ONE artifact in (``render/examples/dual_throttle.compose.yaml``), the maker
project's derived consumers out — proving steps (c/d/e) of the flow FALL OUT of
the single YAML instead of being hand-authored:

  * (c/d) the hookup guide  — perfboard SVG (S3), written to a repo-relative doc
          path the doc-viewer opens (publish it to ``_app/doc/open``);
  * (e)   the MCU read/publish config (S4) — GPIO0->throttle/left,
          GPIO1->throttle/right, saved as YAML (mergeable device block) + JSON
          (doc_store pull shape).

Run (from the repo, PYTHONPATH=python)::

    python3 -m composeio.maker.projects.dual_throttle_build

Idempotent: rewrites the committed artifacts under ``docs/maker/dual-throttle/``.
"""

from __future__ import annotations

import os

from composeio.render.engine.parser import parse_compose_yaml
from composeio.maker import hookup_svg_to_file, hookup_svg_to_png, emit_mcu_config


# repo-relative layout -------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
# .../python/composeio/maker/projects -> repo root is 4 levels up
REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir, os.pardir, os.pardir))
SCENE = os.path.join(REPO_ROOT, "python", "composeio", "render", "examples",
                     "dual_throttle.compose.yaml")

# The committed artifact dir — repo-relative and git-tracked (NOT under the
# symlinked docs/ tree).  The generated `.svg` is doc-viewer openable: publish
# this repo-relative path to `_app/doc/open` and the `.svg` branch spawns a
# viewer that lowers it through the Canvas contract.
DOC_DIR = os.path.join("python", "composeio", "maker", "projects",
                       "dual_throttle", "artifacts")
HOOKUP_SVG_REL = os.path.join(DOC_DIR, "hookup-guide.perfboard.svg")
HOOKUP_PNG_REL = os.path.join(DOC_DIR, "hookup-guide.perfboard.png")
MCU_YAML_REL = os.path.join(DOC_DIR, "mcu-config.yaml")
MCU_JSON_REL = os.path.join(DOC_DIR, "mcu-config.json")


def build(repo_root: str = REPO_ROOT) -> dict:
    """Generate all derived artifacts; returns their repo-relative paths."""
    comp = parse_compose_yaml(SCENE)
    hookup = comp.hookup
    if hookup is None:
        raise SystemExit("dual_throttle.compose.yaml has no hookup: block")

    out_dir = os.path.join(repo_root, DOC_DIR)
    os.makedirs(out_dir, exist_ok=True)

    # (c/d) — the hookup guide (perfboard SVG), doc-viewer openable.
    svg_abs = os.path.join(repo_root, HOOKUP_SVG_REL)
    hookup_svg_to_file(hookup, svg_abs)

    # (c/d) — a headless PNG PROOF of the same guide (rides the compositing pass:
    # C3 + 2 pots + the DY-SV5F footprint art + the speaker terminus).  2x for a
    # crisp doc image.
    png_abs = os.path.join(repo_root, HOOKUP_PNG_REL)
    hookup_svg_to_png(hookup, png_abs, scale=2.0)

    # (e) — the MCU read/publish config, derived from the pin->slot seam.
    cfg = emit_mcu_config(comp)
    with open(os.path.join(repo_root, MCU_YAML_REL), "w", encoding="utf-8") as fh:
        fh.write(cfg.to_yaml())
    with open(os.path.join(repo_root, MCU_JSON_REL), "w", encoding="utf-8") as fh:
        fh.write(cfg.to_json())

    return {
        "scene": os.path.relpath(SCENE, repo_root),
        "hookup_guide": HOOKUP_SVG_REL,
        "hookup_guide_png": HOOKUP_PNG_REL,
        "mcu_config_yaml": MCU_YAML_REL,
        "mcu_config_json": MCU_JSON_REL,
        "sensors": [s.publish + " <- " + s.pin for s in cfg.sensors],
        "actuators": [
            f"{c.cue} -> {c.file} ({c.uart_frame})"
            for a in cfg.actuators for c in a.cues
        ],
        "warnings": list(cfg.warnings),
    }


if __name__ == "__main__":
    result = build()
    print("[S5] dual-throttle artifacts regenerated:")
    for k, v in result.items():
        print(f"  {k}: {v}")
    print()
    print("open the hookup guide in the doc-viewer by publishing to _app/doc/open:")
    print(f"    {{'path': '{HOOKUP_SVG_REL}'}}")
