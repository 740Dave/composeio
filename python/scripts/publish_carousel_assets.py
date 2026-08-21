#!/usr/bin/env python3
"""Publish the CAROUSEL launcher tile icons to the BUS ASSET STORE
([SPEC:CXC-PRIM-CAROUSEL]) — the DRADIS-console model (``dradis_console/
device.yaml``: "NO art is vendored here — only the references"), applied to the
launcher's own tile faces.

The launcher renders its three tile icons FROM the bus store, resolved BY
REFERENCE at draw time (``launcher.compose.yaml`` tile ``src: bus://…``).  This
is the ONE command that (re)publishes them from David's tracked authoring
sources (the migrated CTk-launcher icon copies, co-located with the manifest):

    compose_projects/carousel/resources/fiat-logo-2.png        (fiat tile face)
    compose_projects/carousel/resources/bsg_shield_scaled.png  (bsg tile face)
    compose_projects/carousel/resources/power_off.png          (power tile face)

Unlike the CIC art there is NO bake step — the icons are PUT verbatim (the task's
"bake-not-needed; just PUT the PNGs") to:

    bus://assets/icons/carousel/fiat_logo.png
    bus://assets/icons/carousel/bsg_shield.png
    bus://assets/icons/carousel/power_off.png

The store is the bus master.  Endpoint = $COMPOSEIO_STORAGE_URL or
http://mqtt-broker.local:8420 (resolve.py).  The authoring PNGs are
DELIBERATELY OMITTED from the deploy closure (deploy.manifest) — the kiosk is
always on the bus network, so the store serves them.  The manual equivalent, per
the dradis device.yaml curl-PUT precedent:

    curl -X PUT --data-binary @resources/fiat-logo-2.png \
      http://mqtt-broker.local:8420/api/v1/assets/icons/carousel/fiat_logo.png

Usage:
    python -m scripts.publish_carousel_assets           # PUT all three icons
    python scripts/publish_carousel_assets.py --dry-run  # report, do NOT PUT
    python scripts/publish_carousel_assets.py --verify   # PUT, then round-trip check
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))

from composeio import resolve                                     # noqa: E402

# authoring source PNG  ->  bus:// reference (mirrors carousel_assets.yaml).
_RES = os.path.join(REPO_ROOT, "compose_projects", "carousel", "resources")
ASSETS = [
    (os.path.join(_RES, "fiat-logo-2.png"),
     "bus://assets/icons/carousel/fiat_logo.png"),
    (os.path.join(_RES, "bsg_shield_scaled.png"),
     "bus://assets/icons/carousel/bsg_shield.png"),
    (os.path.join(_RES, "power_off.png"),
     "bus://assets/icons/carousel/power_off.png"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Publish carousel icons to the bus store.")
    ap.add_argument("--dry-run", action="store_true",
                    help="report, do NOT PUT to the store")
    ap.add_argument("--verify", action="store_true",
                    help="after PUT, resolve_bytes each ref and confirm the round-trip")
    args = ap.parse_args(argv)

    print(f"publish -> {resolve.BUS_STORAGE_URL}"
          f"{'  (DRY-RUN)' if args.dry_run else ''}:")
    for src, uri in ASSETS:
        if not os.path.exists(src):
            raise SystemExit(f"authoring source missing: {src}")
        with open(src, "rb") as fh:
            data = fh.read()
        if args.dry_run:
            print(f"  DRY-RUN would PUT {uri} ({len(data)} B) <- {src}")
            continue
        resolve.put_bytes(uri, data)
        print(f"  PUT {uri} ({len(data)} B) <- {os.path.basename(src)}")

    if args.verify and not args.dry_run:
        print("verify (resolve_bytes round-trip):")
        for src, uri in ASSETS:
            with open(src, "rb") as fh:
                want = len(fh.read())
            got = resolve.resolve_bytes(uri)
            ok = "ok" if len(got) == want else "MISMATCH"
            print(f"  {ok}  {uri} ({len(got)} B, want {want} B)")
            if len(got) != want:
                raise SystemExit(f"round-trip size mismatch for {uri}")

    print("done." if not args.dry_run else "dry-run complete (nothing PUT).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
