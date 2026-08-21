#!/usr/bin/env python3
"""Bake the colonial_console EXT-mode screen art bundled with the render engine
([SPEC:CXC-CIC] [DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]).

The ``EXT`` radio mode shows a FULL external CAMERA / planet view in the console
screen-area — David's mockup
(``compose_projects/bsg/resources/screen_base_layout/ext_mode.svg``, a 0.75 MB
file that also carries a 0.49 MB reference-art photo and the whole chrome).  The
RENDER ENGINE needs only the EXT SCREEN CONTENT subtree (the star-field, the
targeting reticule, the planet-image viewport + its embedded sample image, and
the two data-readout rects) and must be self-contained at draw time (no
project-tree dependency — the bundled-chrome / bundled-DRADIS / font-atlas
pattern).  This script EXTRACTS the named subtrees VERBATIM (same path data, same
coordinates, same transforms — David aligned all artwork to the 0,0 upper-left
corner, so the SVG pixel placement is directly usable, IN THE SAME design-box
space as the bundled chrome) and writes a small trimmed SVG next to the widget.

The embedded ``sample-planet-image`` JPEG rides IN the extracted subtree (a
base64 ``data:`` href, ~6 KB), so the planet image DEPLOYS WITH THE PACKAGE — no
separate asset file, no menu-file-style deploy gap.  The 0.49 MB reference photo
lives in the ``*-for-inspiration-not-coding`` layer and is NOT extracted.

Reuse, not fork: the extraction rides the existing SVG model + the named-resource
walk (``composeio.maker.svg_resources``); serialization rides the model's own
``_serialize_node`` (the exact idiom of ``bake_cic_chrome_svg``).  The output is
BYTE-STABLE (same input -> same file), so it is a committed, regenerable asset.

Usage:
    python -m scripts.bake_cic_ext_svg           # regenerate the bundled EXT art
    python scripts/bake_cic_ext_svg.py --check    # verify it is up to date
"""
from __future__ import annotations

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# The tracked art source (project tree, relative to the repo root).
_REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from composeio.maker.svg_resources import load_svg  # noqa: E402
from composeio.render.svg.model import SvgNode, _serialize_node  # noqa: E402
from compose_projects.bsg.widgets.cic import svg_art  # noqa: E402

SOURCE_SVG = os.path.join(
    _REPO_ROOT, "compose_projects", "bsg", "resources",
    "screen_base_layout", "ext_mode.svg")

# David's EXT rotation image set ([CXC-CIC-RADIO-MODE] Fix 2): the ext-view
# <desc> says the artwork "should be shown for 15 seconds and then switched out
# for another image in bsg/resources/screens".  The rotation set is THAT
# directory (sorted, byte-stable), published to the bus store under
# ``svg_art.SCREENS_URI_PREFIX`` and referenced BY the baked SVG (never in-band).
SCREENS_DIR = os.path.join(
    _REPO_ROOT, "compose_projects", "bsg", "resources", "screens")
_IMG_EXTS = (".jpg", ".jpeg", ".png")

# The bundled output (lives beside the widget, shipped with the engine).
# [CXC-PROJECT-WIDGETS] the bundled offline-fallback art lives with the BSG
# project-local cic module now (moved out of the engine core).
OUT_SVG = os.path.join(_REPO_ROOT, "compose_projects", "bsg", "widgets", "cic",
                       "colonial_console_ext.svg")

# The EXT screen-content is David's ``ext-view`` group ([CXC-CIC-RADIO-MODE]) —
# the EXTERNAL-view artwork specific to the EXT mode (its own embedded image; the
# group's <desc> states "'EXT' stands for external view, and the artwork here
# should be shown ...").  It is NOT ``screen-scan-stars``: that group is a GENERIC
# screen-content wrapper that is BYTE-IDENTICAL across colonial_base_ux.svg and
# ext_mode.svg (same structure hash, same embedded planet), so baking/keying EXT on
# it rendered the STANDARD screen content, never the external view — the reported
# EXT-shows-base-stars defect.  ``standard_ux_shape_element`` is extracted too
# (never DRAWN by the EXT widget) so the bundle's DESIGN BOX derives IDENTICALLY to
# the bundled chrome — that is what makes the EXT content register pixel-exact with
# the amber frame + screen hole ([DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]).
EXT_LABELS = [
    "standard_ux_shape_element",   # design-box anchor (not drawn; keeps register)
    "ext-view",                    # THE external-view content (its own image)
]


def _find_by_label(node: SvgNode, label: str):
    if node.attrs.get("label") == label:
        return node
    for ch in node.children:
        found = _find_by_label(ch, label)
        if found is not None:
            return found
    return None


def _find_child(node: SvgNode, tag: str):
    if node.tag == tag:
        return node
    for ch in node.children:
        found = _find_child(ch, tag)
        if found is not None:
            return found
    return None


def _rotation_uris() -> list:
    """The ordered bus URIs for David's rotation set (the screens dir, sorted —
    byte-stable), or [] if the dir is absent."""
    if not os.path.isdir(SCREENS_DIR):
        return []
    names = sorted(f for f in os.listdir(SCREENS_DIR)
                   if os.path.splitext(f)[1].lower() in _IMG_EXTS)
    return [svg_art.SCREENS_URI_PREFIX + n for n in names]


def _interval_from_desc(ext_view: SvgNode) -> float:
    """Parse the rotation interval (seconds) from the ext-view <desc> — the
    source of truth David set ("shown for 15 seconds") — default 15s."""
    desc = _find_child(ext_view, "desc")
    if desc is not None and desc.text:
        m = re.search(r"(\d+(?:\.\d+)?)\s*second", desc.text, re.IGNORECASE)
        if m:
            return float(m.group(1))
    return svg_art.EXT_ROTATION_DEFAULT_INTERVAL


def _rewrite_ext_view(ext_view: SvgNode) -> None:
    """Make the EXT image a BUS REFERENCE, not in-band data ([CXC-CIC-RADIO-MODE]
    Fix 1), and bake the rotation manifest onto the group (Fix 2): strip the
    ~0.5 MB embedded data: href from the <image>, point it at the first rotation
    URI, and stamp data-rotation (the ordered set) + data-interval (from <desc>)."""
    uris = _rotation_uris()
    interval = _interval_from_desc(ext_view)
    ext_view.attrs["data-rotation"] = " ".join(uris)
    ext_view.attrs["data-interval"] = ("%g" % interval)
    img = _find_child(ext_view, "image")
    if img is not None and uris:
        img.attrs["href"] = uris[0]           # drops the in-band data: payload


def build_ext_svg(source: str = SOURCE_SVG) -> str:
    doc = load_svg(source)
    vb = doc.viewbox
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{doc.width}" height="{doc.height}" '
        f'viewBox="{vb[0]} {vb[1]} {vb[2]} {vb[3]}">',
        "<!-- GENERATED by python/scripts/bake_cic_ext_svg.py from "
        "compose_projects/bsg/resources/screen_base_layout/ext_mode.svg "
        "([SPEC:CXC-CIC]).  Do not hand-edit; re-bake after David re-exports. -->",
    ]
    missing = []
    for label in EXT_LABELS:
        node = _find_by_label(doc.root, label)
        if node is None:
            missing.append(label)
            continue
        if label == "ext-view":
            _rewrite_ext_view(node)
        out: list = []
        _serialize_node(node, "", out)
        parts.append("\n".join(out))
    parts.append("</svg>\n")
    if missing:
        raise SystemExit(f"EXT labels missing from source SVG: {missing}")
    return "\n".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Bake the CIC EXT-mode screen SVG.")
    ap.add_argument("--source", default=SOURCE_SVG)
    ap.add_argument("--out", default=OUT_SVG)
    ap.add_argument("--check", action="store_true",
                    help="fail if the bundled file is stale, do not write")
    args = ap.parse_args(argv)

    content = build_ext_svg(args.source)
    if args.check:
        if not os.path.exists(args.out):
            print(f"MISSING: {args.out}")
            return 1
        with open(args.out, "r", encoding="utf-8") as fh:
            current = fh.read()
        if current != content:
            print(f"STALE: {args.out} differs from a fresh bake")
            return 1
        print(f"OK: {args.out} is up to date")
        return 0
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"wrote {args.out} ({len(content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
