"""
Real Fritzing footprints — the compositing seam ([FLOW:MAKER-PROJECT] S3).

A catalog part MAY reference a cached Fritzing breadboard SVG (`fritzing:`)
plus a `fritzing_connectors:` map (catalog pin name -> `connectorN` id).
This module resolves that pair into a :class:`PartFootprint`: the parsed
:class:`~composeio.render.svg.SvgDocument` plus every catalog pin's
TERMINAL CENTER in viewBox user space — transform-RESOLVED via the engine's
own SVG model (``SvgDocument.element_center``), never by reading raw cx/cy
(the SuperMini's right-row connectors carry ``transform="scale(-1)"`` with
negative raw coordinates; trusting cx/cy would mirror the board).

Terminal lookup per connector id ``connectorN``:
    ``connector{N}terminal``  (Fritzing's explicit lead-tip element), else
    ``connector{N}pin``       (the pad itself).

The contract is strict so the fallback stays honest: a part composites ONLY
if the SVG is cached, its physical units are declared (width in in/mm — a
Fritzing invariant; it fixes the board-grid scale), and EVERY catalog pin
maps to a resolvable connector.  Anything less returns ``None`` and the
hookup renderer keeps its generated footprint — never a half-real part.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import PartDef

logger = logging.getLogger("composeio.render.parts")

_SVG_DIRNAME = "svg"
_SVG_DIR = Path(__file__).resolve().parent / _SVG_DIRNAME


@dataclass(frozen=True)
class PartFootprint:
    """A part's real breadboard art + its pin terminals, ready to place.

    ``terminals`` are in the SVG's viewBox user space; ``units_per_inch``
    converts them to physical (Fritzing breadboard SVGs are authored at a
    declared physical size — both cached assets are 1000 units/inch)."""
    part_id: str
    svg_path: str
    doc: "SvgDocument"                        # parsed once, reused
    units_per_inch: float
    terminals: Dict[str, Tuple[float, float]]  # catalog pin -> (x, y)

    def inline_markup(self, id_prefix: str) -> str:
        """The footprint's renderable content as an id-prefixed SVG
        fragment (see :func:`composeio.render.svg.serialize_svg_group`)."""
        from composeio.render.svg import serialize_svg_group
        return serialize_svg_group(self.doc, id_prefix=id_prefix)


# cache: (part id, fritzing filename) -> PartFootprint | None (miss cached
# too — a part without real art shouldn't re-stat the disk every layout)
_CACHE: Dict[Tuple[str, str], Optional[PartFootprint]] = {}


def _units_per_inch(doc) -> Optional[float]:
    """Physical scale from the root width attribute.  Fritzing authors
    part SVGs with a physical width (in/mm); without one the board-grid
    scale would be a guess, so we refuse (-> generated fallback)."""
    from composeio.render.svg.model import parse_length
    raw = str(doc.root.attrs.get("width", "")).strip().lower()
    val = parse_length(raw, 0.0)
    if val <= 0 or doc.vb_w <= 0:
        return None
    if raw.endswith("in"):
        return doc.vb_w / val
    if raw.endswith("mm"):
        return doc.vb_w / (val / 25.4)
    if raw.endswith("cm"):
        return doc.vb_w / (val / 2.54)
    return None


def load_part_footprint(pdef: Optional[PartDef]
                        ) -> Optional[PartFootprint]:
    """Resolve *pdef*'s real Fritzing footprint, or ``None`` (-> the caller
    keeps its generated art).  Cached per (part, asset)."""
    if pdef is None or not pdef.fritzing:
        return None
    key = (pdef.id, pdef.fritzing)
    if key in _CACHE:
        return _CACHE[key]
    fp = _load(pdef)
    _CACHE[key] = fp
    return fp


def _load(pdef: PartDef) -> Optional[PartFootprint]:
    from composeio.render.svg import SvgDocument, SvgError

    path = _SVG_DIR / pdef.fritzing
    if not path.is_file():
        return None                       # not cached yet -> generated art

    connectors = pdef.extra.get("fritzing_connectors")
    if not isinstance(connectors, dict):
        logger.info("part %r has cached art %s but no fritzing_connectors "
                    "map — keeping the generated footprint", pdef.id,
                    pdef.fritzing)
        return None

    try:
        doc = SvgDocument.from_bytes(path.read_bytes())
    except SvgError as exc:
        logger.warning("part %r: footprint %s failed to parse (%s) — "
                       "keeping the generated footprint", pdef.id,
                       pdef.fritzing, exc)
        return None

    upi = _units_per_inch(doc)
    if upi is None:
        logger.warning("part %r: footprint %s declares no physical width "
                       "(in/mm) — keeping the generated footprint",
                       pdef.id, pdef.fritzing)
        return None

    terminals: Dict[str, Tuple[float, float]] = {}
    for pin in pdef.pins:
        cid = connectors.get(pin)
        if not cid:
            logger.warning("part %r: pin %r missing from "
                           "fritzing_connectors — keeping the generated "
                           "footprint", pdef.id, pin)
            return None
        center = (doc.element_center(f"{cid}terminal")
                  or doc.element_center(f"{cid}pin"))
        if center is None:
            logger.warning("part %r: connector %r has no resolvable "
                           "%sterminal/%spin element in %s — keeping the "
                           "generated footprint", pdef.id, cid, cid, cid,
                           pdef.fritzing)
            return None
        terminals[pin] = center

    return PartFootprint(part_id=pdef.id, svg_path=str(path), doc=doc,
                         units_per_inch=upi, terminals=terminals)


__all__ = ["PartFootprint", "load_part_footprint"]
