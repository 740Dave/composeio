"""Composition DERIVATION — the SVG is the composition source, the render lowers
it ([SPEC:CXC-COMP], [CXC-COMP-LAYER-ORDER], [CXC-COMP-CREDO]).

This is the single deterministic function the whole composition contract sits on.
It introspects an ``.svg`` and:

  * sorts each group into the THREE categories ([CXC-COMP-REF]) — rendered
    backdrop / ``slot:`` live widget / ``ref:`` (SKIPPED);
  * reads the render z-ORDER directly from the SVG's own group document order
    ([CXC-COMP-LAYER-ORDER]) — the group's POSITION in the SVG IS its z-index,
    with NO code-side reordering;
  * parses each ``slot:<widget>@<binding>`` (the ``@`` delimiter, hyphen-safe,
    [CXC-COMP-SLOT]) and resolves ``<widget>`` against the registered kind set
    ([CXC-COMP-WIDGET-KIND]);
  * reads each slot's text/shape STYLING from its inner elements
    ([CXC-COMP-TEXT-STYLE]) — font-size from the inner ``<text>`` (NOT its
    ``<tspan>``);
  * records each retained layer's bounding box (for slot placement);
  * emits a :class:`CompositionManifest` — the DERIVED JSON sidecar
    ([CXC-COMP-MANIFEST]) the render picks up automatically, with the in-memory
    derivation as the fallback ([CXC-COMP-INTERLEAVE]).

Reuse, not fork: parsing rides :class:`SvgDocument` ([CXC-SVG-PARSER]) and the
labeled-resource idiom of ``maker.svg_resources`` ([CXC-CIC-SVG-MAP]); geometry
rides the engine's ``element_ctm`` + ``parse_path`` / ``_cmds_bbox``.  We add ONLY
the three-category sort + the order/slot/style derivation.

Determinism: the same SVG yields a byte-identical manifest ([CXC-COMP-LAYER-ORDER]
requirement; verified COMP-C1).  No ``dict`` iteration order dependence, no id()
in output, no wall-clock.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from composeio.render.svg import parse_path
from composeio.render.svg.canvas_renderer import _cmds_bbox
from composeio.render.svg.model import SvgDocument, SvgNode, mat_apply, parse_length

from .kinds import is_registered_kind

Box = Tuple[float, float, float, float]

# --- the authoring vocabulary ([CXC-COMP-REF]) -----------------------------
_SLOT_PREFIX = "slot:"
_REF_PREFIX = "ref:"
# David's legacy alias for a ``ref:`` skip group ([CXC-COMP-REF]).
_REF_ALIAS = "cic-reference-art-for-inspiration-not-coding"
_REF_ALIAS_SUFFIX = "-for-inspiration-not-coding"

_HEX_TOKEN = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_STYLE_DECL = re.compile(r"\s*([\w-]+)\s*:\s*([^;]+?)\s*(?:;|$)")

# Coarse role-map for the KNOWN CIC labels ([CXC-CIC-SVG-TABLE], reused by
# reference as the derivation's input contract).  An unrecognized backdrop group
# resolves to the ambient/content z-slot and is preserve-and-flagged
# ([CXC-COMP-LAYER-ORDER] drop-nothing) — this map is a convenience, NOT a second
# source of z-order (order is always document order).
_ROLE_MAP: Dict[str, str] = {
    "black_background": "background",
    "optional-common-decorators": "decorator",
    "standard-ux-layout": "frame",
    "standard_ux_shape_element": "frame-mask",
    "screen-area": "screen-matte",
    "right-edge-gauge": "edge-gauge",
    "bottom-edge-gauge": "edge-gauge",
    "left-edge-gauge": "edge-gauge",
    "row1-button-bar": "button-bar",
    "row2-tabs": "nav",
    "row3-tabs": "nav",
    "overlay-dialog-box": "dialog",
    "ext-view": "mode-content",
    "semantic_color_palettes": "palette",
    "in-show-non-interactive-animations": "animation",
}


# ---------------------------------------------------------------------------
# the derived records
# ---------------------------------------------------------------------------
@dataclass
class SlotBinding:
    """A parsed ``slot:<widget>@<binding>`` ([CXC-COMP-SLOT])."""
    widget: str            # the interaction KIND (before the first ``@``)
    binding: str           # the bus binding key (remainder; hyphens preserved)
    registered: bool       # <widget> resolves to a registered kind
    well_formed: bool      # a ``@`` was present with a non-empty widget+binding


@dataclass
class TextStyle:
    """Styling read from a slot's inner ``<text>`` ([CXC-COMP-TEXT-STYLE]).

    ``font_size`` is read from the ``<text>`` element ITSELF (design-space); a size
    present only on a ``<tspan>`` is NOT honored (``tspan_only_size`` records that
    for lint/diagnostics)."""
    font_family: Optional[str] = None
    font_size: Optional[float] = None
    fill: Optional[str] = None
    tspan_only_size: bool = False


@dataclass
class CompositionLayer:
    """One derived render layer (bottom->top by ``z``)."""
    z: int
    category: str                       # 'backdrop' | 'slot'
    label: str
    id: Optional[str]
    role: str                           # resolved role or 'unmapped'
    artwork_kind: str                   # P/R/RSVD/GRP/TXT (best-effort, tag-derived)
    bbox: Optional[Box] = None          # viewBox-space (x, y, w, h)
    slot: Optional[SlotBinding] = None
    text_style: Optional[TextStyle] = None
    shape_fill: Optional[str] = None    # inner shape fill ([CXC-COMP-TEXT-STYLE])
    shape_ry: Optional[float] = None    # inner rect rounding, if any
    placeholder: bool = False           # slot has static children (the placeholder)
    unmapped: bool = False              # unrecognized backdrop role (preserve+flag)


@dataclass
class Diagnostic:
    """A derivation/lint finding.  ``level`` is 'error' or 'warning'
    ([CXC-COMP-LINT])."""
    level: str
    code: str
    message: str
    label: Optional[str] = None


@dataclass
class CompositionManifest:
    """The DERIVED composition manifest ([CXC-COMP-MANIFEST]) — the canonical
    layer order the render composites in, plus the ``ref:`` skips + diagnostics."""
    source: Optional[str]
    viewbox: Box
    layers: List[CompositionLayer] = field(default_factory=list)
    skipped_refs: List[str] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)

    # -- serialization (the JSON sidecar) -------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Plain-dict form for the ``.composition.json`` sidecar.  Deterministic:
        no id()s, no set iteration — order is the derived layer order."""
        return {
            "version": 1,
            "source": self.source,
            "viewbox": list(self.viewbox),
            "layers": [_layer_to_dict(l) for l in self.layers],
            "skipped_refs": list(self.skipped_refs),
            "diagnostics": [asdict(d) for d in self.diagnostics],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CompositionManifest":
        layers = [_layer_from_dict(x) for x in d.get("layers", [])]
        diags = [Diagnostic(**x) for x in d.get("diagnostics", [])]
        vb = tuple(d.get("viewbox", (0.0, 0.0, 0.0, 0.0)))  # type: ignore
        return cls(source=d.get("source"), viewbox=vb, layers=layers,
                   skipped_refs=list(d.get("skipped_refs", [])), diagnostics=diags)

    @classmethod
    def from_json(cls, text: str) -> "CompositionManifest":
        return cls.from_dict(json.loads(text))

    # -- convenience ----------------------------------------------------------
    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "error"]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "warning"]

    def slot_layers(self) -> List[CompositionLayer]:
        return [l for l in self.layers if l.category == "slot"]


def _layer_to_dict(l: CompositionLayer) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "z": l.z, "category": l.category, "label": l.label, "id": l.id,
        "role": l.role, "artwork_kind": l.artwork_kind,
        "bbox": list(l.bbox) if l.bbox is not None else None,
    }
    if l.slot is not None:
        out["slot"] = asdict(l.slot)
    if l.text_style is not None:
        out["text_style"] = asdict(l.text_style)
    if l.shape_fill is not None:
        out["shape_fill"] = l.shape_fill
    if l.shape_ry is not None:
        out["shape_ry"] = l.shape_ry
    if l.placeholder:
        out["placeholder"] = True
    if l.unmapped:
        out["unmapped"] = True
    return out


def _layer_from_dict(d: Dict[str, Any]) -> CompositionLayer:
    slot = SlotBinding(**d["slot"]) if d.get("slot") else None
    ts = TextStyle(**d["text_style"]) if d.get("text_style") else None
    bbox = tuple(d["bbox"]) if d.get("bbox") is not None else None  # type: ignore
    return CompositionLayer(
        z=d["z"], category=d["category"], label=d["label"], id=d.get("id"),
        role=d.get("role", "unmapped"), artwork_kind=d.get("artwork_kind", "GRP"),
        bbox=bbox, slot=slot, text_style=ts, shape_fill=d.get("shape_fill"),
        shape_ry=d.get("shape_ry"), placeholder=bool(d.get("placeholder")),
        unmapped=bool(d.get("unmapped")))


# ---------------------------------------------------------------------------
# the three-category sort ([CXC-COMP-REF])
# ---------------------------------------------------------------------------
def classify(label: str) -> str:
    """Sort a group by its label/id into one of the three categories
    ([CXC-COMP-REF]): 'ref' (skipped), 'slot' (live widget), or 'backdrop'."""
    lbl = (label or "").strip()
    if lbl.startswith(_SLOT_PREFIX):
        return "slot"
    if lbl.startswith(_REF_PREFIX) or lbl == _REF_ALIAS or lbl.endswith(_REF_ALIAS_SUFFIX):
        return "ref"
    return "backdrop"


def parse_slot(label: str) -> SlotBinding:
    """Parse a ``slot:<widget>@<binding>`` label ([CXC-COMP-SLOT], [DD-COMP-SLOT-
    NAMING]).  Split on the FIRST ``@`` (widget before, binding the remainder —
    hyphens preserved).  A missing ``@`` or an empty widget/binding is NOT
    well-formed (an ERROR at lint time, preserve-and-flag at derive time)."""
    body = (label or "").strip()[len(_SLOT_PREFIX):] if label.strip().startswith(
        _SLOT_PREFIX) else (label or "").strip()
    if "@" in body:
        widget, binding = body.split("@", 1)
        widget = widget.strip()
        binding = binding.strip()
        well_formed = bool(widget) and bool(binding)
    else:
        widget = body.strip()
        binding = ""
        well_formed = False
    return SlotBinding(widget=widget, binding=binding,
                       registered=is_registered_kind(widget),
                       well_formed=well_formed)


# ---------------------------------------------------------------------------
# geometry + styling helpers (reuse-by-reference of the engine seams)
# ---------------------------------------------------------------------------
def _style_map(node: SvgNode) -> Dict[str, str]:
    style = node.attrs.get("style")
    if not style:
        return {}
    return {k.lower(): v for k, v in _STYLE_DECL.findall(style)}


def _prop(node: SvgNode, key: str) -> Optional[str]:
    """A CSS property from the node's ``style=`` (wins) or presentation attr."""
    v = _style_map(node).get(key)
    if v is not None:
        return v.strip()
    v = node.attrs.get(key)
    return v.strip() if v is not None else None


def _norm_hex(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    m = _HEX_TOKEN.search(value)
    if not m:
        return None
    h = m.group(0)[1:]
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) in (6, 8):
        return "#" + h.lower()
    return None


def _local_bbox(node: SvgNode) -> Optional[Box]:
    """Geometry bbox in the node's OWN coordinate space (rect/path/circle/image)."""
    if node.tag in ("rect", "image", "use"):
        w = parse_length(node.attrs.get("width"), 0.0)
        h = parse_length(node.attrs.get("height"), 0.0)
        if w > 0 and h > 0:
            return (parse_length(node.attrs.get("x"), 0.0),
                    parse_length(node.attrs.get("y"), 0.0), w, h)
        return None
    if node.tag == "path":
        d = node.attrs.get("d")
        if d:
            return _cmds_bbox(parse_path(d))
        return None
    if node.tag in ("circle", "ellipse"):
        cx = parse_length(node.attrs.get("cx"), 0.0)
        cy = parse_length(node.attrs.get("cy"), 0.0)
        if node.tag == "circle":
            r = parse_length(node.attrs.get("r"), 0.0)
            rx = ry = r
        else:
            rx = parse_length(node.attrs.get("rx"), 0.0)
            ry = parse_length(node.attrs.get("ry"), 0.0)
        if rx > 0 and ry > 0:
            return (cx - rx, cy - ry, 2 * rx, 2 * ry)
    return None


def _vb_bbox(doc: SvgDocument, node: SvgNode) -> Optional[Box]:
    """Absolute viewBox-space bbox of *node* — the UNION of its rect/path/circle
    descendants mapped through each element CTM ([CXC-COMP-SLOT] slot rect).  The
    same idiom as ``svg_art.element_vb_bbox`` (reused here, not forked, to keep the
    engine module free of a project-layer import)."""
    corners: List[Tuple[float, float]] = []

    def walk(n: SvgNode) -> None:
        bb = _local_bbox(n)
        if bb is not None and bb[2] > 0 and bb[3] > 0:
            ctm = doc.element_ctm(n.attrs.get("id") or "") or (1, 0, 0, 1, 0, 0)
            for (lx, ly) in ((bb[0], bb[1]), (bb[0] + bb[2], bb[1] + bb[3]),
                             (bb[0] + bb[2], bb[1]), (bb[0], bb[1] + bb[3])):
                corners.append(mat_apply(ctm, lx, ly))
        for ch in n.children:
            walk(ch)

    walk(node)
    if not corners:
        return None
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _first_text(node: SvgNode) -> Optional[SvgNode]:
    if node.tag == "text":
        return node
    for ch in node.children:
        f = _first_text(ch)
        if f is not None:
            return f
    return None


def _first_shape(node: SvgNode) -> Optional[SvgNode]:
    for ch in node.children:
        if ch.tag in ("rect", "path", "circle", "ellipse"):
            return ch
        f = _first_shape(ch)
        if f is not None:
            return f
    return None


def _text_style(node: SvgNode) -> Optional[TextStyle]:
    """Text styling from a slot's inner ``<text>`` ([CXC-COMP-TEXT-STYLE]).

    font-size is read from the ``<text>`` element ITSELF, NOT its ``<tspan>``; a
    size present only on a ``<tspan>`` is recorded (``tspan_only_size``) but NOT
    honored (COMP-C7)."""
    text = _first_text(node)
    if text is None:
        return None
    size_str = _prop(text, "font-size")
    tspan_only = False
    if size_str is None:
        # detect a size on a tspan child (which we intentionally do NOT honor)
        for ch in text.children:
            if ch.tag == "tspan" and _prop(ch, "font-size") is not None:
                tspan_only = True
                break
    fill = _norm_hex(_prop(text, "fill"))
    return TextStyle(
        font_family=_prop(text, "font-family"),
        font_size=parse_length(size_str, None) if size_str is not None else None,  # type: ignore
        fill=fill,
        tspan_only_size=tspan_only)


def _artwork_kind(node: SvgNode) -> str:
    """Best-effort artwork-kind ([CXC-CIC-SVG-MAP] P/R/RSVD/GRP/TXT), tag-derived.
    A precise kind comes from the [CXC-CIC-SVG-TABLE] role; this is the structural
    fallback so the manifest field is always populated."""
    return {"path": "P", "rect": "R", "image": "R", "text": "TXT",
            "g": "GRP"}.get(node.tag, "GRP")


def _role_for(label: str) -> str:
    return _ROLE_MAP.get(label, "unmapped")


# ---------------------------------------------------------------------------
# the derivation ([CXC-COMP-LAYER-ORDER])
# ---------------------------------------------------------------------------
def _top_level_groups(root: SvgNode) -> List[SvgNode]:
    """Direct ``<g>`` children of the SVG root, in document order.  These are the
    primary layer units; nested ``slot:``/``ref:`` groups are discovered within
    each backdrop group (below)."""
    return [c for c in root.children if c.tag == "g"]


def _scan_nested(node: SvgNode):
    """Yield (descendant, category) for every ``slot:``/``ref:`` group at any depth
    under *node*, in document order, PRUNING each match's subtree (a slot/ref does
    not contain further layers).  Backdrop descendants are descended THROUGH (they
    render as part of their owning backdrop layer, not a separate layer)."""
    for child in node.children:
        if child.tag not in ("g", "rect", "path", "text", "image", "circle"):
            continue
        cat = classify(child.attrs.get("label") or child.attrs.get("id") or "")
        if cat in ("slot", "ref"):
            yield child, cat
        else:
            yield from _scan_nested(child)


def _build_slot_layer(doc: SvgDocument, node: SvgNode, z: int,
                      diags: List[Diagnostic]) -> CompositionLayer:
    label = node.attrs.get("label") or node.attrs.get("id") or ""
    binding = parse_slot(label)
    shape = _first_shape(node)
    shape_fill = _norm_hex(_prop(shape, "fill")) if shape is not None else None
    shape_ry = (parse_length(shape.attrs.get("ry"), None)  # type: ignore
                if shape is not None and shape.tag == "rect"
                and shape.attrs.get("ry") is not None else None)
    # placeholder = the slot's own static children ([CXC-COMP-SLOT])
    placeholder = any(ch.tag in ("g", "rect", "path", "text", "image", "circle")
                      for ch in node.children)
    layer = CompositionLayer(
        z=z, category="slot", label=label, id=node.attrs.get("id"),
        role="slot", artwork_kind=_artwork_kind(node),
        bbox=_vb_bbox(doc, node), slot=binding,
        text_style=_text_style(node), shape_fill=shape_fill, shape_ry=shape_ry,
        placeholder=placeholder)
    # preserve-and-flag: an unregistered kind is a WARNING at derive time (an
    # ERROR only at lint/bake time, [CXC-COMP-SLOT] / [CXC-COMP-LINT]).
    if not binding.registered:
        diags.append(Diagnostic(
            "warning", "unregistered-kind",
            f"slot '{label}' names unregistered widget kind "
            f"'{binding.widget}' -> placeholder rendered", label))
    return layer


def _build_backdrop_layer(node: SvgNode, z: int,
                          diags: List[Diagnostic]) -> CompositionLayer:
    label = node.attrs.get("label") or node.attrs.get("id") or ""
    role = _role_for(label)
    unmapped = role == "unmapped"
    if unmapped:
        diags.append(Diagnostic(
            "warning", "unmapped-group",
            f"group '{label}' is not in the CIC vocabulary; placed in the "
            f"ambient/content z-slot (preserve-and-flag)", label))
    return CompositionLayer(
        z=z, category="backdrop", label=label, id=node.attrs.get("id"),
        role=role, artwork_kind=_artwork_kind(node), bbox=None, unmapped=unmapped)


def derive(source: Any, *, source_name: Optional[str] = None) -> CompositionManifest:
    """Derive the composition manifest from an SVG ([CXC-COMP-LAYER-ORDER]).

    *source* is a path, bytes, markup string, or a parsed :class:`SvgDocument`.
    The result is DETERMINISTIC — the same SVG yields a byte-identical manifest
    (COMP-C1).  Layer z-order is the SVG's own document order with NO code-side
    reordering; ``ref:`` groups are skipped; ``slot:`` groups become live-widget
    layers interleaved at their document position ([CXC-COMP-INTERLEAVE])."""
    from composeio.maker.svg_resources import load_svg  # reuse the loader
    doc = source if isinstance(source, SvgDocument) else load_svg(source)
    name = source_name if source_name is not None else (
        source if isinstance(source, str) and source.endswith(".svg") else None)

    layers: List[CompositionLayer] = []
    skipped: List[str] = []
    diags: List[Diagnostic] = []
    z = 0

    for group in _top_level_groups(doc.root):
        label = group.attrs.get("label") or group.attrs.get("id") or ""
        cat = classify(label)
        if cat == "ref":
            skipped.append(label)
            continue
        if cat == "slot":
            layers.append(_build_slot_layer(doc, group, z, diags))
            z += 1
            continue
        # backdrop: emit the group as a layer, then interleave its nested slots
        # just above it in document order ([CXC-COMP-INTERLEAVE]).
        layers.append(_build_backdrop_layer(group, z, diags))
        z += 1
        for desc, dcat in _scan_nested(group):
            dlabel = desc.attrs.get("label") or desc.attrs.get("id") or ""
            if dcat == "ref":
                skipped.append(dlabel)
            else:  # slot
                layers.append(_build_slot_layer(doc, desc, z, diags))
                z += 1

    vb = doc.viewbox if doc.viewbox else (0.0, 0.0, doc.width, doc.height)
    return CompositionManifest(source=name, viewbox=tuple(vb), layers=layers,
                               skipped_refs=skipped, diagnostics=diags)


# ---------------------------------------------------------------------------
# manifest sidecar path helper ([CXC-COMP-MANIFEST])
# ---------------------------------------------------------------------------
def manifest_path_for(svg_path: str) -> str:
    """The ``.composition.json`` sidecar path co-located with a baked SVG
    ([CXC-COMP-MANIFEST]) — ``foo.svg`` -> ``foo.composition.json``."""
    if svg_path.endswith(".svg"):
        return svg_path[: -len(".svg")] + ".composition.json"
    return svg_path + ".composition.json"
