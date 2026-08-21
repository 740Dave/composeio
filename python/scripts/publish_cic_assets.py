#!/usr/bin/env python3
"""Publish the CIC (colonial_console) art to the BUS ASSET STORE
([SPEC:CXC-CIC]) — the DRADIS-console model (``dradis_console/device.yaml``:
"NO art is vendored here — only the references").

The colonial_console chrome + EXT screen art + two-level nav are BUS ASSETS the
render engine resolves BY REFERENCE at draw time (``svg_art.CHROME_URI`` /
``EXT_URI`` / ``menu.MENU_URI``).  This is the ONE command that (re)publishes
them after David edits his tracked authoring sources:

    compose_projects/bsg/resources/screen_base_layout/colonial_base_ux.svg   (chrome)
    compose_projects/bsg/resources/screen_base_layout/ext_mode.svg           (EXT view)
    compose_projects/bsg/resources/screen_base_layout/cic_menu_structure.md  (nav)

It BAKES the chrome/EXT trimmed SVGs (the ``bake_cic_{chrome,ext}_svg`` scripts —
this also refreshes the package-bundled OFFLINE fallback copies so they stay in
sync), then PUTs each asset to the store via ``composeio.resolve.put_*``:

    bus://assets/cic/chrome.svg        <- baked chrome SVG
    bus://assets/cic/ext.svg           <- baked EXT SVG (embeds the planet JPEG)
    bus://assets/cic/menu.md           <- the tracked nav (cic_menu_structure.md)
    bus://assets/icons/cic/planet.jpg  <- the planet image, EXTRACTED from the
                                          EXT SVG's embedded data: href (a
                                          standalone icon asset, dradis's model)

The store is the bus master.  Endpoint = $COMPOSEIO_STORAGE_URL or
http://mqtt-broker.local:8420 (resolve.py).  The manual equivalent, per the
dradis device.yaml curl-PUT precedent:

    curl -X PUT --data-binary @colonial_console_chrome.svg \
      http://mqtt-broker.local:8420/api/v1/assets/cic/chrome.svg

Usage:
    python -m scripts.publish_cic_assets            # bake + PUT all CIC assets
    python scripts/publish_cic_assets.py --dry-run  # bake + report, do NOT PUT
    python scripts/publish_cic_assets.py --skip-bake  # PUT the committed bundles as-is
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
# [CXC-PROJECT-WIDGETS] the cic asset helpers moved to the BSG project; reach
# them via the repo root (parent of python/).
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from composeio import resolve                                     # noqa: E402
from compose_projects.bsg.widgets.cic import svg_art              # noqa: E402
from compose_projects.bsg.widgets.cic import menu as menu_mod     # noqa: E402

# The EXT external-view image ROTATION set ([CXC-CIC-RADIO-MODE] Fix 1/2): David's
# screens live in the store, REFERENCED by the baked EXT SVG (never in-band).  We
# publish each one under svg_art.SCREENS_URI_PREFIX so the render-time bus resolve
# finds it and the 15s rotation cycles through them.
SCREENS_DIR = os.path.join(
    REPO_ROOT, "compose_projects", "bsg", "resources", "screens")
_IMG_EXTS = (".jpg", ".jpeg", ".png")

_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def _bake_or_read(bake_fn, source_svg: str, out_path: str, kind: str,
                  skip_bake: bool) -> str:
    """Return the asset text: a fresh bake from David's authoring source (also
    (re)writing the bundled offline copy), or — if --skip-bake or the source is
    absent — the committed bundled copy as-is."""
    if not skip_bake and os.path.exists(source_svg):
        content = bake_fn(source_svg)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"  baked {kind}: {source_svg} -> {out_path} ({len(content)} B)")
        return content
    with open(out_path, "r", encoding="utf-8") as fh:
        content = fh.read()
    why = "skip-bake" if skip_bake else "authoring source absent"
    print(f"  {kind}: using committed bundle ({why}): {out_path} ({len(content)} B)")
    return content


def _screen_assets() -> list:
    """(uri, local_path) for each rotation image in David's screens dir, sorted
    (byte-stable; MUST match the bake's ordering)."""
    if not os.path.isdir(SCREENS_DIR):
        return []
    names = sorted(f for f in os.listdir(SCREENS_DIR)
                   if os.path.splitext(f)[1].lower() in _IMG_EXTS)
    return [(svg_art.SCREENS_URI_PREFIX + n, os.path.join(SCREENS_DIR, n))
            for n in names]


def _put_text(uri: str, text: str, dry: bool) -> None:
    if dry:
        print(f"  DRY-RUN would PUT {uri} ({len(text.encode())} B)")
        return
    resolve.put_text(uri, text)
    print(f"  PUT {uri} ({len(text.encode())} B) -> {resolve.BUS_STORAGE_URL}")


def _put_bytes(uri: str, data: bytes, dry: bool) -> None:
    if dry:
        print(f"  DRY-RUN would PUT {uri} ({len(data)} B)")
        return
    resolve.put_bytes(uri, data)
    print(f"  PUT {uri} ({len(data)} B) -> {resolve.BUS_STORAGE_URL}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish CIC art to the bus store.")
    ap.add_argument("--dry-run", action="store_true",
                    help="bake + report, do NOT PUT to the store")
    ap.add_argument("--skip-bake", action="store_true",
                    help="PUT the committed bundled copies as-is (no re-bake)")
    args = ap.parse_args(argv)

    # imported here so --help works without the authoring tree on sys.path
    from scripts.bake_cic_chrome_svg import build_chrome_svg, SOURCE_SVG as CHROME_SRC
    from scripts.bake_cic_ext_svg import build_ext_svg, SOURCE_SVG as EXT_SRC

    print("bake:")
    chrome = _bake_or_read(build_chrome_svg, CHROME_SRC, svg_art.CHROME_SVG,
                           "chrome", args.skip_bake)
    ext = _bake_or_read(build_ext_svg, EXT_SRC, svg_art.EXT_SVG,
                        "ext", args.skip_bake)

    # the nav: the tracked authoring file if present, else the bundled fallback
    menu_src = (menu_mod.DEFAULT_MENU_PATH
                if os.path.exists(menu_mod.DEFAULT_MENU_PATH)
                else menu_mod.BUNDLED_MENU_PATH)
    with open(menu_src, "r", encoding="utf-8") as fh:
        menu_text = fh.read()
    print(f"  menu: {menu_src} ({len(menu_text.encode())} B)")

    screens = _screen_assets()
    print(f"  screens: {len(screens)} rotation image(s) from {SCREENS_DIR}")

    print(f"\npublish -> {resolve.BUS_STORAGE_URL}"
          f"{'  (DRY-RUN)' if args.dry_run else ''}:")
    _put_text(svg_art.CHROME_URI, chrome, args.dry_run)
    _put_text(svg_art.EXT_URI, ext, args.dry_run)
    _put_text(menu_mod.MENU_URI, menu_text, args.dry_run)
    # the EXT rotation set — referenced by ext.svg, resolved at render time
    for uri, path in screens:
        with open(path, "rb") as fh:
            _put_bytes(uri, fh.read(), args.dry_run)
    print("done." if not args.dry_run else "dry-run complete (nothing PUT).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
