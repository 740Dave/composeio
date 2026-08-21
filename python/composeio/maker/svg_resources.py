"""
Named-SVG-RESOURCE extractor — the foundation of the CIC SVG re-scan pipeline
([CXC-CIC-SVG-MAP], spec ``corpora/platform-specs/50-composeux-cic-shell.lts``).

David authors CIC/colonial-console artwork in Inkscape using a FIXED
layer/element-name vocabulary; every on-screen resource is an Inkscape group or
element carrying an ``inkscape:label``.  This module reads those named resources
back OUT of the tracked ``.svg`` so tooling (theme tables, the scene re-scan) can
consume them deterministically.  Its first consumer is the semantic-color-palette
extractor (:func:`extract_semantic_palettes`); it is deliberately GENERAL so a
new resource kind is a few lines, not a new parser.

Reuse, not fork
---------------
Parsing rides the existing zero-heavy-dependency SVG model
(:class:`composeio.render.svg.model.SvgDocument`, [CXC-SVG-PARSER]) — stdlib
``xml.etree`` only.  That model already strips XML namespaces (so
``inkscape:label`` folds to the ``label`` attr), indexes every ``id`` and, via
:meth:`SvgDocument.element_ctm` / :meth:`SvgDocument.element_center`, resolves an
element's ABSOLUTE transform.  We add ONLY the label-addressed walk + property
extraction; we do not re-implement XML, transforms, or viewBox handling.

How this extends to the two SVG file-kinds ([CXC-CIC-SVG-MAP])
--------------------------------------------------------------
The re-scan contract handles two file-kinds under ONE naming vocabulary, and in
BOTH an Inkscape LAYER (a labeled ``<g>``) maps to one on-screen element:

  * the full-screen SCREEN mockup (e.g. ``colonial_base_ux.svg``) — a scan
    regenerates a whole console/sub-tab content scene;
  * per-WIDGET files (e.g. ``bsg_widgets_1.svg``) — each layer is one widget.

Both reduce to the SAME primitive this module provides:
"given a labeled group, hand me its labeled children + their relevant properties
(fill, geometry, text)".  A screen scan walks the top-level layer stack; a
widget-file scan walks each widget layer; the palette extractor walks the
``semantic_color_palettes`` group.  Each is a thin consumer of
:func:`extract_named_resources`.  A future consumer for the reticule / planet
panels adds a function that names its group-label path and picks the child labels
it cares about — no change to the walk.  (Reference-only placeholder layers
labeled ``*-for-inspiration-not-coding`` are surfaced verbatim so a caller can
preserve-and-flag them per the drop-nothing discipline; this module does not
silently drop any labeled element.)

Public API
----------
    load_svg(source)                                 -> SvgDocument
    find_labeled_group(source, label_path)           -> ResourceElement | None
    extract_named_resources(source, label_path, ...) -> list[ResourceElement]
    extract_semantic_palettes(source, ...)           -> dict[theme, {base, highlight}]
    normalize_fill(value)                            -> "#RRGGBB" | "#RRGGBBAA" | None
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

from composeio.render.svg.model import SvgDocument, SvgNode, parse_length

# A source may be a filesystem path, raw bytes, or an already-parsed document.
SvgSource = Union[str, bytes, SvgDocument]

_STYLE_DECL = re.compile(r"\s*([\w-]+)\s*:\s*([^;]+?)\s*(?:;|$)")
_HEX = re.compile(r"^#([0-9a-fA-F]{3,8})$")


# ---------------------------------------------------------------------------
# fill / color normalization
# ---------------------------------------------------------------------------
def _style_map(node: SvgNode) -> Dict[str, str]:
    """Parse a ``style="a:b;c:d"`` attribute into a declaration dict (lower-keyed)."""
    style = node.attrs.get("style")
    if not style:
        return {}
    return {k.lower(): v for k, v in _STYLE_DECL.findall(style)}


def normalize_fill(value: Optional[str]) -> Optional[str]:
    """Normalize a fill color to an uppercase ``#RRGGBB`` (or ``#RRGGBBAA``).

    Handles 3-/4-/6-/8-digit hex (shorthand expanded).  Returns ``None`` for a
    missing value, ``none``, or a non-hex paint (``url(#grad)``, a named color) —
    the caller decides whether that is an error or a skip."""
    if value is None:
        return None
    v = value.strip()
    if not v or v.lower() == "none":
        return None
    m = _HEX.match(v)
    if not m:
        return None
    h = m.group(1)
    if len(h) in (3, 4):                      # #rgb / #rgba -> expand each nibble
        h = "".join(c * 2 for c in h)
    if len(h) in (6, 8):
        return "#" + h.upper()
    return None                               # 5-/7-digit hex is not a valid color


def _element_fill(node: SvgNode) -> Optional[str]:
    """The element's fill, normalized.  A ``fill:`` in the ``style`` attr wins
    over a presentation ``fill=`` attr (CSS specificity), matching how Inkscape
    writes it; both forms are accepted per the extractor contract."""
    styled = _style_map(node).get("fill")
    fill = normalize_fill(styled)
    if fill is not None:
        return fill
    return normalize_fill(node.attrs.get("fill"))


def _element_geometry(node: SvgNode) -> Optional[Tuple[float, float, float, float]]:
    """Raw (x, y, w, h) for a rect-like element (rect/image/use), in the
    element's OWN coordinate space.  For a transform-resolved position, resolve
    the id through :meth:`SvgDocument.element_ctm` / ``element_center`` — the
    shared transform seam this module intentionally does not duplicate."""
    a = node.attrs
    if "width" in a and "height" in a:
        return (parse_length(a.get("x"), 0.0), parse_length(a.get("y"), 0.0),
                parse_length(a.get("width"), 0.0), parse_length(a.get("height"), 0.0))
    return None


# ---------------------------------------------------------------------------
# the extracted-resource record
# ---------------------------------------------------------------------------
@dataclass
class ResourceElement:
    """One labeled SVG resource + its extracted properties.

    ``label`` is the ``inkscape:label`` (the vocabulary token); ``children`` are
    the resource's own labeled descendants (recursively), letting a caller walk a
    group -> subgroup -> leaf hierarchy such as
    ``semantic_color_palettes -> cic -> base``.  ``node`` is the raw
    :class:`SvgNode` for anything the extractor did not lift."""
    label: str
    tag: str
    id: Optional[str] = None
    fill: Optional[str] = None
    geometry: Optional[Tuple[float, float, float, float]] = None
    text: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List["ResourceElement"] = field(default_factory=list)
    node: Optional[SvgNode] = None

    def child(self, label: str) -> Optional["ResourceElement"]:
        """The first labeled child with ``label`` (direct children only)."""
        for c in self.children:
            if c.label == label:
                return c
        return None

    @property
    def is_reference_only(self) -> bool:
        """True for a ``*-for-inspiration-not-coding`` placeholder layer — a
        caller preserves-and-flags these rather than emitting a widget
        ([CXC-CIC-SVG-MAP] drop-nothing)."""
        return self.label.endswith("-for-inspiration-not-coding")


def _to_element(node: SvgNode, *, deep: bool) -> ResourceElement:
    """Build a :class:`ResourceElement` from a labeled node, recursing into its
    own labeled descendants when *deep*."""
    return ResourceElement(
        label=node.attrs.get("label", ""),
        tag=node.tag,
        id=node.attrs.get("id"),
        fill=_element_fill(node),
        geometry=_element_geometry(node),
        text=node.text,
        attrs=dict(node.attrs),
        children=_labeled_children(node, deep=deep) if deep else [],
        node=node,
    )


def _labeled_children(node: SvgNode, *, deep: bool) -> List[ResourceElement]:
    """Direct labeled children of *node* (a labeled node terminates the descent
    into that branch — its own subtree becomes that child's ``children``).  An
    unlabeled wrapper ``<g>`` is transparent: we descend THROUGH it so a labeled
    element nested under it is still found, matching Inkscape's habit of wrapping
    content in anonymous groups."""
    out: List[ResourceElement] = []
    for child in node.children:
        if child.attrs.get("label"):
            out.append(_to_element(child, deep=deep))
        else:
            out.extend(_labeled_children(child, deep=deep))
    return out


# ---------------------------------------------------------------------------
# loading + label-addressed lookup
# ---------------------------------------------------------------------------
def load_svg(source: SvgSource) -> SvgDocument:
    """Coerce *source* (path | bytes | SvgDocument) into a parsed document."""
    if isinstance(source, SvgDocument):
        return source
    if isinstance(source, bytes):
        return SvgDocument.from_bytes(source)
    if isinstance(source, str) and (os.path.sep in source or source.endswith(".svg")) \
            and os.path.exists(source):
        with open(source, "rb") as fh:
            return SvgDocument.from_bytes(fh.read())
    # a bare string that is not a file path is treated as SVG markup
    return SvgDocument.from_string(source)


def _find_by_label(node: SvgNode, label: str) -> Optional[SvgNode]:
    """First node in *node*'s subtree (inclusive) whose ``label`` == *label*."""
    if node.attrs.get("label") == label:
        return node
    for child in node.children:
        found = _find_by_label(child, label)
        if found is not None:
            return found
    return None


def find_labeled_group(source: SvgSource,
                       label_path: str) -> Optional[ResourceElement]:
    """Locate a labeled group by label, or by a ``/``-separated PATH that
    descends label-by-label (``"semantic_color_palettes/cic"``).  Returns the
    group as a :class:`ResourceElement` (with its labeled children), or ``None``.

    A single segment is searched anywhere in the tree; each subsequent segment is
    searched WITHIN the previously matched node — so a path disambiguates a label
    that repeats under different parents (``base`` appears once per theme)."""
    doc = load_svg(source)
    node: SvgNode = doc.root
    for seg in [s for s in label_path.split("/") if s]:
        found = _find_by_label(node, seg)
        if found is None:
            return None
        node = found
    return _to_element(node, deep=True)


def extract_named_resources(source: SvgSource, label_path: str, *,
                            deep: bool = True) -> List[ResourceElement]:
    """THE reusable primitive: given a group's label (or label PATH), return its
    labeled child elements as :class:`ResourceElement`\\ s, each with its fill,
    geometry, text and (when *deep*) its own labeled children.

    This is the single seam every re-scan consumer sits on
    ([CXC-CIC-SVG-MAP]).  Adding a resource kind = name the group-label path and
    read the child labels you need.  Returns ``[]`` if the group is absent."""
    group = find_labeled_group(source, label_path)
    if group is None:
        return []
    if not deep:                              # shallow: re-read children flat
        return [ResourceElement(label=c.label, tag=c.tag, id=c.id, fill=c.fill,
                                geometry=c.geometry, text=c.text, attrs=c.attrs,
                                node=c.node) for c in group.children]
    return group.children


# ---------------------------------------------------------------------------
# first consumer: the semantic color palettes
# ---------------------------------------------------------------------------
def _collect_theme_groups(node: SvgNode) -> List[SvgNode]:
    """Every labeled group in *node*'s subtree that holds BOTH a ``base`` and a
    ``highlight`` labeled child — i.e. a theme-swatch group — regardless of what
    wrapper it sits under.  This lets the palettes live either EMBEDDED (under a
    ``semantic_color_palettes`` group in the full mockup) OR in a DEDICATED file
    (themes directly under a layer, David 2026-07-31) with no code change."""
    out: List[SvgNode] = []

    def has_child(n: SvgNode, lbl: str) -> bool:
        return any(c.attrs.get("label") == lbl for c in n.children)

    def walk(n: SvgNode) -> None:
        if n.attrs.get("label") and has_child(n, "base") and has_child(n, "highlight"):
            out.append(n)             # a theme's children are swatches — don't recurse
            return
        for c in n.children:
            walk(c)

    walk(node)
    return out


def extract_semantic_palettes(source: SvgSource,
                              group_label: str = "semantic_color_palettes",
                              ) -> Dict[str, Dict[str, str]]:
    """Extract the CIC/colonial-console semantic THEME palettes.

    Each THEME is a labeled group (``cic``, ``sickbay``, ``flight_deck``,
    ``raptor``, and any David adds later) holding two labeled squares, ``base``
    and ``highlight``, whose fills are the theme's two tokens ([DD-CIC-THEME]:
    base_color / highlight_color).  The themes are found FIRST under the named
    ``group_label`` (the embedded-mockup layout); if that wrapper is absent — the
    palettes now live in a dedicated file, ``colonial_base_ux_semantic_colors.svg``,
    with the themes directly under a layer — we FALL BACK to scanning the whole
    document for theme-shaped groups, so the source-of-truth move needs no caller
    change ([DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]).

    Returns ``{theme: {"base": "#RRGGBB", "highlight": "#RRGGBB"}}`` for every
    theme that supplies BOTH swatches with a valid fill; a theme missing a swatch
    is skipped (the caller can compare against the requested set to flag gaps)."""
    palettes: Dict[str, Dict[str, str]] = {}
    themes = extract_named_resources(source, group_label)
    if not themes:                    # no wrapper group -> dedicated-file layout
        doc = load_svg(source)
        themes = [_to_element(n, deep=True)
                  for n in _collect_theme_groups(doc.root)]
    for theme in themes:
        base = theme.child("base")
        highlight = theme.child("highlight")
        if base and highlight and base.fill and highlight.fill:
            palettes[theme.label] = {"base": base.fill, "highlight": highlight.fill}
    return palettes


__all__ = [
    "ResourceElement",
    "SvgSource",
    "load_svg",
    "find_labeled_group",
    "extract_named_resources",
    "extract_semantic_palettes",
    "normalize_fill",
]


# ---------------------------------------------------------------------------
# CLI: dump a file's semantic palettes (a quick eyeball / theme-table source)
# ---------------------------------------------------------------------------
def _main(argv: Optional[List[str]] = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description="Extract named SVG resources (CIC).")
    p.add_argument("svg", help="path to the Inkscape .svg")
    p.add_argument("--group", default="semantic_color_palettes",
                   help="group label/path to extract (default: palettes)")
    p.add_argument("--palettes", action="store_true",
                   help="emit the semantic palette map instead of raw resources")
    args = p.parse_args(argv)

    if args.palettes or args.group == "semantic_color_palettes":
        print(json.dumps(extract_semantic_palettes(args.svg, args.group), indent=2))
    else:
        for el in extract_named_resources(args.svg, args.group):
            print(f"{el.label}\t{el.tag}\tfill={el.fill}\tgeom={el.geometry}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
