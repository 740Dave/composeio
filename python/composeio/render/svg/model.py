"""
SVG document model ([SPEC:CXC-SVG] [CXC-SVG-PARSER]).

Parses an SVG document into a lightweight, backend-independent tree using
ONLY the Python standard library (`xml.etree.ElementTree`) — the same
zero-heavy-dependency discipline the Lottie subset evaluator applied
(render/lottie/model.py uses stdlib json; python-lottie's AGPLv3 was
rejected).  No external SVG library is imported; the renderer
(canvas_renderer.py) decomposes this tree to Canvas primitives.

The model is a pure parse: it resolves the viewBox, width/height,
preserveAspectRatio, and indexes `<defs>` (gradients, clipPaths, and any
id-addressable element for `<use>`).  It performs NO drawing and NO style
inheritance — that is the renderer's job, so a single parsed document can
be drawn into any rect on any backend.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"

_LEN = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_TRANSFORM_FN = re.compile(r"(\w+)\s*\(([^)]*)\)")


class SvgError(ValueError):
    """Raised when a document cannot be parsed as SVG."""


def _local(tag: str) -> str:
    """Strip an XML namespace: '{http://…}rect' -> 'rect'."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def parse_length(value: Optional[str], default: float = 0.0) -> float:
    """Parse an SVG length, ignoring units (px/pt/…).  Percentages return
    the raw number (the renderer resolves them against the viewport)."""
    if value is None:
        return default
    m = _LEN.search(str(value))
    return float(m.group()) if m else default


# ---------------------------------------------------------------------------
# Affine transform math (THE shared implementation — canvas_renderer and any
# geometry consumer reuse these; do not fork transform parsing elsewhere).
# A matrix is the SVG 2x3 affine (a, b, c, d, e, f):
#     x' = a*x + c*y + e        y' = b*x + d*y + f
# ---------------------------------------------------------------------------

Matrix = Tuple[float, float, float, float, float, float]
MAT_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def mat_mul(m1: Matrix, m2: Matrix) -> Matrix:
    """m1 · m2 (apply m2 first, then m1 — SVG left-to-right composition)."""
    a, b, c, d, e, f = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a * a2 + c * b2, b * a2 + d * b2,
            a * c2 + c * d2, b * c2 + d * d2,
            a * e2 + c * f2 + e, b * e2 + d * f2 + f)


def mat_apply(m: Matrix, x: float, y: float) -> Tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def mat_inverse(m: Matrix) -> Optional[Matrix]:
    """Invert a 2x3 affine.  None when singular (det == 0).  Used to peel the
    canvas-representable factor off a sheared matrix() so the residual shear
    can be baked into geometry points (the Canvas contract has no shear op)."""
    a, b, c, d, e, f = m
    det = a * d - b * c
    if det == 0.0:
        return None
    ia = d / det
    ib = -b / det
    ic = -c / det
    id_ = a / det
    return (ia, ib, ic, id_,
            -(ia * e + ic * f), -(ib * e + id_ * f))


def _fn_matrix(name: str, vals: List[float]) -> Optional[Matrix]:
    """One transform function -> matrix (None for unsupported, e.g. skew)."""
    if name == "translate":
        return (1.0, 0.0, 0.0, 1.0, vals[0] if vals else 0.0,
                vals[1] if len(vals) > 1 else 0.0)
    if name == "scale":
        sx = vals[0] if vals else 1.0
        return (sx, 0.0, 0.0, vals[1] if len(vals) > 1 else sx, 0.0, 0.0)
    if name == "rotate":
        ang = math.radians(vals[0] if vals else 0.0)
        rot: Matrix = (math.cos(ang), math.sin(ang),
                       -math.sin(ang), math.cos(ang), 0.0, 0.0)
        if len(vals) >= 3:                       # rotate about (cx, cy)
            cx, cy = vals[1], vals[2]
            pre: Matrix = (1.0, 0.0, 0.0, 1.0, cx, cy)
            post: Matrix = (1.0, 0.0, 0.0, 1.0, -cx, -cy)
            return mat_mul(mat_mul(pre, rot), post)
        return rot
    if name == "matrix" and len(vals) == 6:
        return (vals[0], vals[1], vals[2], vals[3], vals[4], vals[5])
    return None


def parse_transform(transform: Optional[str]) -> Matrix:
    """Compose an SVG transform list into ONE affine matrix.  Unsupported
    functions (skewX/skewY) are skipped — the renderer already warns on
    those; geometry lookups stay exact for the supported set."""
    if not transform:
        return MAT_IDENTITY
    m = MAT_IDENTITY
    for name, arg in _TRANSFORM_FN.findall(transform):
        vals = [float(mm.group()) for mm in _LEN.finditer(arg)]
        fm = _fn_matrix(name, vals)
        if fm is not None:
            m = mat_mul(m, fm)
    return m


@dataclass
class SvgNode:
    """One SVG element: its local tag, attributes (namespaces stripped,
    xlink:href folded to 'href'), and child nodes.  `text` holds character
    data for <text>/<tspan>."""
    tag: str
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List["SvgNode"] = field(default_factory=list)
    text: str = ""


@dataclass
class SvgDocument:
    """A parsed SVG ready to render.  `width`/`height` are the intrinsic
    pixel size; `viewbox` is (min_x, min_y, w, h) user-space; `ids` maps
    every id-addressable element for <use>/gradient/clip lookup."""
    root: SvgNode
    width: float
    height: float
    viewbox: Tuple[float, float, float, float]
    preserve_aspect_ratio: str
    ids: Dict[str, SvgNode] = field(default_factory=dict)

    # -- viewport ----------------------------------------------------------

    @property
    def vb_x(self) -> float:
        return self.viewbox[0]

    @property
    def vb_y(self) -> float:
        return self.viewbox[1]

    @property
    def vb_w(self) -> float:
        return self.viewbox[2]

    @property
    def vb_h(self) -> float:
        return self.viewbox[3]

    # -- construction ------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> "SvgDocument":
        try:
            text = data.decode("utf-8") if isinstance(data, bytes) else data
            elem = ET.fromstring(text)
        except (ET.ParseError, UnicodeDecodeError, ValueError) as exc:
            raise SvgError(f"not a parseable SVG document: {exc}") from exc
        if _local(elem.tag) != "svg":
            raise SvgError(f"root element is <{_local(elem.tag)}>, not <svg>")
        return cls.from_element(elem)

    @classmethod
    def from_string(cls, text: str) -> "SvgDocument":
        return cls.from_bytes(text.encode("utf-8"))

    @classmethod
    def from_element(cls, elem: ET.Element) -> "SvgDocument":
        ids: Dict[str, SvgNode] = {}
        root = _convert(elem, ids)

        vb_attr = root.attrs.get("viewBox") or root.attrs.get("viewbox")
        if vb_attr:
            nums = [float(m.group()) for m in _LEN.finditer(vb_attr)]
            if len(nums) == 4:
                viewbox = (nums[0], nums[1], nums[2], nums[3])
            else:
                viewbox = (0.0, 0.0, 100.0, 100.0)
        else:
            viewbox = None  # type: ignore

        width = parse_length(root.attrs.get("width"),
                             viewbox[2] if viewbox else 100.0)
        height = parse_length(root.attrs.get("height"),
                              viewbox[3] if viewbox else 100.0)
        if viewbox is None:
            viewbox = (0.0, 0.0, width or 100.0, height or 100.0)
        if not width:
            width = viewbox[2]
        if not height:
            height = viewbox[3]

        par = (root.attrs.get("preserveAspectRatio")
               or root.attrs.get("preserveaspectratio")
               or "xMidYMid meet")
        return cls(root=root, width=width, height=height, viewbox=viewbox,
                   preserve_aspect_ratio=par.strip(), ids=ids)

    # -- resolved element geometry ([CXC-SVG] id -> viewBox-space lookup) ----

    def element_ctm(self, elem_id: str) -> Optional[Matrix]:
        """The accumulated transform (root -> element, element's own
        transform INCLUDED) for the element with ``id=elem_id``, in viewBox
        user space.  None if the id does not exist.

        This is the transform-resolution seam: consumers that need an
        element's ABSOLUTE position (e.g. a Fritzing footprint's
        `connectorNpin` terminal, which may carry `transform="scale(-1)"`
        with raw negative coordinates) go through here — never through the
        raw cx/cy attributes."""
        target = self.ids.get(elem_id)
        if target is None:
            return None

        def dfs(node: SvgNode, acc: Matrix) -> Optional[Matrix]:
            t = node.attrs.get("transform")
            m = mat_mul(acc, parse_transform(t)) if t else acc
            if node is target:
                return m
            for child in node.children:
                found = dfs(child, m)
                if found is not None:
                    return found
            return None

        return dfs(self.root, MAT_IDENTITY)

    def element_center(self, elem_id: str) -> Optional[Tuple[float, float]]:
        """The transform-resolved CENTER of an id-addressed circle/ellipse/
        rect, in viewBox user space.  None for a missing id or a tag whose
        center is not statically derivable (paths need the renderer)."""
        node = self.ids.get(elem_id)
        if node is None:
            return None
        if node.tag in ("circle", "ellipse"):
            local = (parse_length(node.attrs.get("cx"), 0.0),
                     parse_length(node.attrs.get("cy"), 0.0))
        elif node.tag == "rect":
            local = (parse_length(node.attrs.get("x"), 0.0)
                     + parse_length(node.attrs.get("width"), 0.0) / 2.0,
                     parse_length(node.attrs.get("y"), 0.0)
                     + parse_length(node.attrs.get("height"), 0.0) / 2.0)
        else:
            return None
        ctm = self.element_ctm(elem_id)
        if ctm is None:
            return None
        return mat_apply(ctm, local[0], local[1])


# ---------------------------------------------------------------------------
# Inline serialization — composite a parsed document into another SVG
# ---------------------------------------------------------------------------

_NON_RENDER_TAGS = ("metadata", "title", "desc")


def _escape_attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_URL_REF = re.compile(r"url\(\s*['\"]?#([^)'\"]+)['\"]?\s*\)")


def _rewrite_refs(value: str, prefix: str) -> str:
    """Prefix url(#id) references inside an attribute value."""
    return _URL_REF.sub(lambda m: f"url(#{prefix}{m.group(1)})", value)


def _serialize_node(node: SvgNode, prefix: str, out: List[str]) -> None:
    if node.tag in _NON_RENDER_TAGS:
        return
    parts = [f"<{node.tag}"]
    for k, v in node.attrs.items():
        if k == "id":
            v = prefix + v
        elif k == "href" and v.startswith("#"):
            v = "#" + prefix + v[1:]
        elif "url(" in v:
            v = _rewrite_refs(v, prefix)
        parts.append(f' {k}="{_escape_attr(v)}"')
    if not node.children and not node.text:
        parts.append("/>")
        out.append("".join(parts))
        return
    parts.append(">")
    if node.text:
        parts.append(_escape_attr(node.text))
    out.append("".join(parts))
    for child in node.children:
        _serialize_node(child, prefix, out)
    out.append(f"</{node.tag}>")


def serialize_svg_group(doc: SvgDocument, id_prefix: str = "") -> str:
    """Serialize *doc*'s renderable content (root children, minus metadata/
    title/desc) as an SVG fragment for inlining into ANOTHER document.
    Every ``id`` and ``url(#…)``/``href="#…"`` reference is prefixed with
    *id_prefix* so multiple instances of the same asset stay id-unique in
    the composite.  Attribute values pass through verbatim (byte-stable);
    the caller wraps the fragment in its own placing ``<g transform=…>``."""
    out: List[str] = []
    for child in doc.root.children:
        _serialize_node(child, id_prefix, out)
    return "\n".join(out)


def _convert(elem: ET.Element, ids: Dict[str, SvgNode]) -> SvgNode:
    """Recursively convert an ElementTree element into an SvgNode, stripping
    namespaces and indexing ids."""
    attrs: Dict[str, str] = {}
    for k, v in elem.attrib.items():
        key = _local(k)
        if k == f"{{{_XLINK_NS}}}href" or key == "href":
            attrs["href"] = v
        else:
            attrs[key] = v
    node = SvgNode(tag=_local(elem.tag), attrs=attrs,
                   text=(elem.text or "").strip())
    nid = attrs.get("id")
    if nid:
        ids[nid] = node
    for child in list(elem):
        node.children.append(_convert(child, ids))
        # capture tail text (tspan siblings) onto the parent text run
        tail = (child.tail or "").strip()
        if tail:
            node.text = (node.text + " " + tail).strip()
    return node
