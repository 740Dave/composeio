"""
S3 — hookup SVG generator (`composeio.maker.hookup_svg`).

ONE netlist spec (`comp.hookup`, S2), TWO substrate renderers (breadboard |
perfboard).  These tests pin the S3 contract from
`docs/maker/maker-project-flow.md` §5:

  * the generator emits a VALID SVG for the dual-throttle example on BOTH
    substrates (parses back through the engine's own SVG model — the same model
    the doc-viewer lowers to the Canvas contract);
  * a wire's endpoints map to the RIGHT pin coordinates (the netlist -> geometry
    contract: ``seg.frm_xy == layout.pin_xy(part, pin)``);
  * the two substrates share parts + wires but differ in board background +
    connection semantics (jumper leads vs point-to-point solder traces);
  * the doc-viewer SVG scene (the `.svg` open branch) parses + renders headless
    — the SVG reaches a real window through the `svg` widget, no external app.
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.hookup import Hookup
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.svg import SvgDocument
from composeio.maker.hookup_svg import (
    PITCH, BoardLayout, WireSeg, hookup_to_svg, hookup_svg_to_file, layout_hookup,
    wire_segments, hookup_viewer_scene,
)

EXAMPLE = os.path.join(PY_ROOT, "composeio", "render", "examples",
                       "dual_throttle.compose.yaml")


def _example_hookup(board: str = "perfboard") -> Hookup:
    comp = parse_compose_yaml(EXAMPLE)
    h = comp.hookup
    assert h is not None, "the example must carry a hookup block (S2)"
    if h.board != board:
        h = Hookup(board=board, parts=h.parts, wires=h.wires)
    return h


# ---------------------------------------------------------------------------
# valid SVG on both substrates
# ---------------------------------------------------------------------------
def test_emits_valid_svg_both_substrates():
    for board in ("breadboard", "perfboard"):
        h = _example_hookup(board)
        svg = hookup_to_svg(h)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        # parses through the ENGINE's own SVG model (what the doc-viewer lowers)
        doc = SvgDocument.from_bytes(svg.encode("utf-8"))
        assert doc.width > 0 and doc.height > 0
        vb = doc.viewbox
        assert vb[2] == doc.width and vb[3] == doc.height
        # every part id + slot is drawn as text
        assert "mcu" in svg and "potL" in svg and "potR" in svg
        assert "throttle/left" in svg and "throttle/right" in svg
        # substrate label present
        assert board in svg


def test_svg_uses_only_canvas_lowerable_primitives():
    # no <style>, <filter>, <image> — the doc-viewer's SVG subset ([CXC-SVG])
    svg = hookup_to_svg(_example_hookup("perfboard"))
    for banned in ("<style", "<filter", "<image", "<mask", "<pattern"):
        assert banned not in svg


# ---------------------------------------------------------------------------
# wire endpoints map to the right pin coordinates (the load-bearing contract)
# ---------------------------------------------------------------------------
def test_wire_endpoints_map_to_pin_coords():
    h = _example_hookup("perfboard")
    lay = layout_hookup(h)
    segs = wire_segments(h, lay)
    assert len(segs) == len(h.wires)
    # every segment endpoint is exactly the layout's pin coordinate
    for seg in segs:
        fp, fpin = seg.frm.split(".", 1)
        tp, tpin = seg.to.split(".", 1)
        assert seg.frm_xy == lay.pin_xy(fp, fpin)
        assert seg.to_xy == lay.pin_xy(tp, tpin)
    # the loop-closer wire specifically: potL.wiper -> mcu.GPIO0 = throttle/left
    seg0 = next(s for s in segs if s.frm == "potL.wiper")
    assert seg0.to == "mcu.GPIO0"
    assert seg0.publishes == "throttle/left"
    assert seg0.role == "signal"
    assert seg0.frm_xy == lay.pins["potL.wiper"]
    assert seg0.to_xy == lay.pins["mcu.GPIO0"]


def test_wire_polyline_starts_and_ends_on_the_real_pins():
    """The routing changed the PATH to orthogonal, never the ENDPOINTS: the
    polyline's first/last vertex is STILL the exact resolved pin terminal."""
    h = _example_hookup("perfboard")
    lay = layout_hookup(h)
    for seg in wire_segments(h, lay):
        assert len(seg.points) >= 2
        # first / last vertex == the real pads (the anchoring contract)
        assert seg.points[0] == seg.frm_xy
        assert seg.points[-1] == seg.to_xy


def test_wires_are_orthogonal_polylines():
    """Every drawn segment of every wire is axis-aligned (Manhattan routing) —
    no diagonal bundle that collapses which pin lands where."""
    for board in ("perfboard", "breadboard"):
        h = _example_hookup(board)
        for seg in wire_segments(h):
            for (ax, ay), (bx, by) in zip(seg.points, seg.points[1:]):
                horizontal = abs(ay - by) < 1e-6
                vertical = abs(ax - bx) < 1e-6
                assert horizontal or vertical, (board, seg.frm, seg.to,
                                                (ax, ay), (bx, by))
        # a multi-pin corridor actually BENDS (proves lane routing, not a
        # straight diagonal): the pot->mcu leads have interior vertices.
        segs = {s.frm: s for s in wire_segments(h)}
        assert len(segs["potL.wiper"].points) >= 3
        assert len(segs["potR.wiper"].points) >= 3


def test_wire_roles_classified():
    h = _example_hookup("perfboard")
    segs = {(_s.frm, _s.to): _s for _s in wire_segments(h)}
    # power lead (potL.vcc -> mcu.3V3) and ground lead (potL.gnd -> mcu.GND)
    assert segs[("potL.vcc", "mcu.3V3")].role == "power"
    assert segs[("potL.gnd", "mcu.GND")].role == "ground"
    # signal leads carry a distinct-per-signal color; the two throttle wipers (the
    # publishing slots) stay distinct even as the scene grows (the DY-SV5F audio wires
    # add more signal-role leads — UART/BUSY/speaker).
    tl = segs[("potL.wiper", "mcu.GPIO0")]
    tr = segs[("potR.wiper", "mcu.GPIO1")]
    assert tl.role == "signal" and tr.role == "signal" and tl.color != tr.color
    sigs = [s for s in segs.values() if s.role == "signal"]
    assert len(sigs) >= 2 and all(s.color for s in sigs)


def test_layout_is_deterministic():
    h = _example_hookup("perfboard")
    a, b = layout_hookup(h), layout_hookup(h)
    assert a.pins == b.pins and (a.width, a.height) == (b.width, b.height)
    # every catalog pin of every part is placed
    for hp in h.parts:
        for pin in hp.definition.pins:
            assert f"{hp.id}.{pin}" in a.pins


# ---------------------------------------------------------------------------
# ONE spec, TWO substrates — same geometry, different board + wire semantics
# ---------------------------------------------------------------------------
def test_substrates_share_geometry_differ_in_chrome():
    hb = _example_hookup("breadboard")
    hp = _example_hookup("perfboard")
    # identical parts + wires => identical pin geometry
    assert layout_hookup(hb).pins == layout_hookup(hp).pins
    svg_bb = hookup_to_svg(hb)
    svg_pf = hookup_to_svg(hp)
    # the two documents are NOT identical (different substrate background)
    assert svg_bb != svg_pf
    # breadboard draws power rails (the +/- glyphs); perfboard does not
    assert ">+<" in svg_bb or "+</text>" in svg_bb
    # perfboard draws an orthogonal solder TRACE (a <path>) + a solder pad per
    # wire — one wire-path apiece, with the copper-pad fill breadboard omits.
    assert svg_pf.count("<path") >= len(hp.wires)
    assert svg_pf.count('fill="#C9CDD4"') >= len(hp.wires)


# ---------------------------------------------------------------------------
# lane separation — the fix: converging leads stay individually traceable
# ---------------------------------------------------------------------------
def _parallel_gap(s1, s2, eps=1e-6):
    """If two axis-aligned segments run PARALLEL (same orientation) with
    overlapping extent along the run, return the perpendicular gap between them;
    else return None.  This catches near-parallel smears (David 2026-07-26),
    not just exactly-colinear overlays — a gap of 0 is a colinear overlap, a gap
    below PITCH is a sub-grid lane pair."""
    (a1, b1), (a2, b2) = s1, s2
    h1 = abs(a1[1] - b1[1]) < eps and abs(a1[0] - b1[0]) > eps
    h2 = abs(a2[1] - b2[1]) < eps and abs(a2[0] - b2[0]) > eps
    v1 = abs(a1[0] - b1[0]) < eps and abs(a1[1] - b1[1]) > eps
    v2 = abs(a2[0] - b2[0]) < eps and abs(a2[1] - b2[1]) > eps
    if h1 and h2:                                   # both horizontal
        lo1, hi1 = sorted((a1[0], b1[0]))
        lo2, hi2 = sorted((a2[0], b2[0]))
        if min(hi1, hi2) - max(lo1, lo2) > eps:     # real x-overlap
            return abs(a1[1] - a2[1])
    if v1 and v2:                                   # both vertical
        lo1, hi1 = sorted((a1[1], b1[1]))
        lo2, hi2 = sorted((a2[1], b2[1]))
        if min(hi1, hi2) - max(lo1, lo2) > eps:     # real y-overlap
            return abs(a1[0] - a2[0])
    return None


def test_distinct_nets_never_run_parallel_closer_than_a_grid_unit():
    """The TIGHTENED lane-separation invariant (David 2026-07-26): two wires that
    do NOT share a pin (so are genuinely different nets) never draw two parallel
    runs closer than ONE grid unit (PITCH) — not merely 'not colinear'.  Every
    adjacent parallel lane is >= a full hole apart, so converging leads (the
    DY-SV5F's five, packed sub-grid before) stay individually traceable.  Wires
    that share a pin (a common node, e.g. both pots' vcc -> mcu.3V3) are exempt
    because converging AT the shared pad is correct."""
    for board in ("perfboard", "breadboard"):
        segs = wire_segments(_example_hookup(board))

        def endpoints(s):
            return {s.frm, s.to}

        def pieces(s):
            return list(zip(s.points, s.points[1:]))

        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                if endpoints(segs[i]) & endpoints(segs[j]):
                    continue                   # shared node -> convergence ok
                for p1 in pieces(segs[i]):
                    for p2 in pieces(segs[j]):
                        gap = _parallel_gap(p1, p2)
                        assert gap is None or gap >= PITCH - 1e-3, (
                            board, round(gap, 2), "px apart:",
                            segs[i].frm, segs[i].to, "||",
                            segs[j].frm, segs[j].to)


# ---------------------------------------------------------------------------
# body avoidance — no wire draws over a part body except at its endpoint pad
# ---------------------------------------------------------------------------
def _seg_body_overlap(a, b, body, eps=1e-6):
    """Length by which an axis-aligned segment ``a-b`` runs through the INTERIOR
    of a part body ``(x, y, w, h)`` (0.0 if it only touches an edge / misses)."""
    bx, by, bw, bh = body
    (x1, y1), (x2, y2) = a, b
    if abs(y1 - y2) < eps and abs(x1 - x2) > eps:              # horizontal
        if not (by + eps < y1 < by + bh - eps):
            return 0.0
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return max(0.0, min(hi, bx + bw) - max(lo, bx))
    if abs(x1 - x2) < eps and abs(y1 - y2) > eps:              # vertical
        if not (bx + eps < x1 < bx + bw - eps):
            return 0.0
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return max(0.0, min(hi, by + bh) - max(lo, by))
    return 0.0


def test_no_wire_segment_crosses_a_part_body():
    """Body-avoidance invariant (David 2026-07-26): a wire must NEVER draw over a
    part's body rectangle EXCEPT at its own endpoint pads — the first segment
    (the escape out of pin A) may overlap A's own body and the last segment (into
    pin B) may overlap B's own body; every OTHER segment must clear every part
    body entirely.  This is what stops leads routing straight across the ESP32-C3
    and the DY-SV5F board surfaces.  Holds on BOTH substrates (they share the
    geometry the router produces)."""
    for board in ("perfboard", "breadboard"):
        h = _example_hookup(board)
        lay = layout_hookup(h)
        for seg in wire_segments(h, lay):
            own_a = seg.frm.split(".", 1)[0]
            own_b = seg.to.split(".", 1)[0]
            n = len(seg.points)
            assert n >= 2
            for k, (p, q) in enumerate(zip(seg.points, seg.points[1:])):
                first, last = (k == 0), (k == n - 2)
                for pid, box in lay.parts.items():
                    ov = _seg_body_overlap(p, q, box.body)
                    if ov <= 1e-6:
                        continue
                    if first and pid == own_a:
                        continue          # escape out of own pad — allowed
                    if last and pid == own_b:
                        continue          # escape into own pad — allowed
                    raise AssertionError(
                        f"[{board}] wire {seg.frm}->{seg.to} segment #{k} "
                        f"{p}->{q} crosses body of {pid} by {ov:.1f}px")


def test_wire_escape_points_exit_their_own_body():
    """The endpoint exemption is only ever a SHORT escape, never a traversal: the
    second vertex (escape of pin A) and the second-to-last (escape of pin B) sit
    OUTSIDE their own part body, so the exempted first/last segments only ever
    cover the pin-to-edge exit — a wire can never hide a full-body crossing under
    the endpoint-pad exemption."""
    h = _example_hookup("perfboard")
    lay = layout_hookup(h)
    for seg in wire_segments(h, lay):
        box_a = lay.parts[seg.frm.split(".", 1)[0]]
        box_b = lay.parts[seg.to.split(".", 1)[0]]
        for box, pt in ((box_a, seg.points[1]), (box_b, seg.points[-2])):
            bx, by, bw, bh = box.body
            outside = (pt[0] <= bx + 1e-6 or pt[0] >= bx + bw - 1e-6
                       or pt[1] <= by + 1e-6 or pt[1] >= by + bh - 1e-6)
            assert outside, (seg.frm, seg.to, pt, box.body)


# ---------------------------------------------------------------------------
# doc-viewer wiring — the SVG reaches a window through the `svg` widget
# ---------------------------------------------------------------------------
def test_hookup_svg_to_file_and_viewer_scene(tmp_path):
    h = _example_hookup("perfboard")
    out = str(tmp_path / "hookup.svg")
    written = hookup_svg_to_file(h, out)
    assert os.path.isfile(written)
    with open(written, encoding="utf-8") as fh:
        assert fh.read().startswith("<svg")
    scene = hookup_viewer_scene(written, title="Dual Throttle")
    # the scene hosts an svg widget pointed at the file:// SVG
    frame = scene["widgets"][0]
    svgw = next(c for c in frame["children"] if c["type"] == "svg")
    assert svgw["src"].startswith("file://") and svgw["src"].endswith(".svg")


def test_docviewer_svg_scene_renders_headless(tmp_path):
    # the SVG-open branch produces a scene that a real runtime renders (the SVG
    # lowered through the Canvas contract), proving the doc-viewer seam.
    from composeio.render.engine.runtime import RenderApp
    h = _example_hookup("breadboard")
    svg_path = hookup_svg_to_file(h, str(tmp_path / "hookup.svg"))
    scene = hookup_viewer_scene(svg_path, title="Dual Throttle")
    import yaml
    scene_file = tmp_path / "hookup.compose.yaml"
    scene_file.write_text(yaml.safe_dump(scene, sort_keys=False),
                          encoding="utf-8")
    out = str(tmp_path / "hookup.png")
    app = RenderApp.from_file(str(scene_file), windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.exists(out) and os.path.getsize(out) > 5000


# ---------------------------------------------------------------------------
# the doc-viewer LAUNCHER opens a .svg via the svg widget (not markdown)
# ---------------------------------------------------------------------------
def _load_launcher():
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools",
                        "doc_viewer_launcher.py")
    spec = importlib.util.spec_from_file_location("doc_viewer_launcher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launcher_opens_svg_as_svg_widget():
    L = _load_launcher()
    scene = L.build_open_scene("/repo/artifacts/hookup.svg")
    frame = scene["widgets"][0]
    svgw = next(c for c in frame["children"] if c["type"] == "svg")
    assert svgw["src"] == "file:///repo/artifacts/hookup.svg"
    # a .md still routes to the markdown template (unchanged behavior)
    md = L.build_open_scene("/repo/docs/a.md",
                            template={"variables": {"doc_path": "x",
                                                    "doc_name": "x"}})
    assert md["variables"]["doc_path"] == "/repo/docs/a.md"


def test_launcher_selftest_still_green():
    L = _load_launcher()
    assert L._selftest() == 0


# ---------------------------------------------------------------------------
# REAL Fritzing footprint compositing (connector-terminal alignment pass)
# ---------------------------------------------------------------------------

def test_supermini_pins_anchor_to_real_terminals():
    """The load-bearing bar: a composited part's layout pins are the REAL
    connectorN terminal centers (transform-resolved), NOT generated stubs."""
    from composeio.render.engine.parts import resolve_part
    from composeio.render.engine.parts.footprint import load_part_footprint
    from composeio.maker.hookup_svg import PITCH

    h = _example_hookup("perfboard")
    lay = layout_hookup(h)
    mcu = lay.parts["mcu"]
    assert mcu.footprint is not None, "SuperMini must composite real art"
    fpp = mcu.footprint

    # independent resolution straight from the asset through the SVG model
    fp = load_part_footprint(resolve_part("esp32-c3-supermini"))
    assert fp is not None
    # the transformed right-row connector: raw cx/cy are NEGATIVE under
    # scale(-1); the resolved center must be the positive mirror
    c8 = fp.doc.element_center("connector8pin")
    assert c8 is not None
    assert abs(c8[0] - 654.66101) < 1e-3 and abs(c8[1] - 832.07953) < 1e-3

    # every catalog pin's layout coordinate == placement affine * terminal
    connectors = resolve_part("esp32-c3-supermini").extra[
        "fritzing_connectors"]
    for pin, cid in connectors.items():
        center = (fp.doc.element_center(f"{cid}terminal")
                  or fp.doc.element_center(f"{cid}pin"))
        expected = (round(fpp.tx + center[0] * fpp.scale, 2),
                    round(fpp.ty + center[1] * fpp.scale, 2))
        assert lay.pin_xy("mcu", pin) == expected, pin

    # the real footprint is TWO columns — the generated stub was one
    xs = {round(xy[0], 2) for xy in mcu.pins.values()}
    assert len(xs) == 2
    # and the row pitch lands on the board grid (0.1" == PITCH)
    left = sorted(y for (x, y) in mcu.pins.values() if x == min(xs))
    assert abs((left[2] - left[1]) - PITCH) < 0.05


def test_wires_land_on_real_pads():
    """Seam wire endpoints ARE the real pads of BOTH composited parts."""
    h = _example_hookup("perfboard")
    lay = layout_hookup(h)
    segs = {(s.frm, s.to): s for s in wire_segments(h, lay)}
    seg = segs[("potL.wiper", "mcu.GPIO0")]
    pot = lay.parts["potL"]
    assert pot.footprint is not None, "the pot must composite real art"
    assert seg.frm_xy == pot.pins["wiper"]
    assert seg.to_xy == lay.parts["mcu"].pins["GPIO0"]
    # the pot wiper is the CENTER leg of the three
    px = sorted(x for (x, y) in pot.pins.values())
    assert pot.pins["wiper"][0] == px[1]


def test_footprint_art_inlined_and_id_prefixed():
    svg = hookup_to_svg(_example_hookup("perfboard"))
    # real art present, id-prefixed per instance (two pots stay unique)
    assert 'id="mcu-connector8pin"' in svg
    assert 'id="potL-connector1terminal"' in svg
    assert 'id="potR-connector1terminal"' in svg
    # still parses through the engine's own SVG model
    doc = SvgDocument.from_bytes(svg.encode("utf-8"))
    assert doc.width > 0 and doc.height > 0
    # labeled affordance survives over the real art — for the pads the netlist
    # WIRES.  `GPIO21` is deliberately absent: it is a pad this hookup never
    # connects, and an unwired pad no longer competes for label space
    # (`hookup.LABEL_PINS`, David 2026-08-15).  The pad itself is still drawn.
    for label in ("GPIO0", "wiper", "5V"):
        assert f">{label}</text>" in svg
    assert ">GPIO21</text>" not in svg


def test_unwired_pads_lose_their_label_but_never_their_pad():
    """The suppression removes the OVERLAY, not the affordance: every pad keeps
    its terminal ring, so a builder counting positions along a header still
    sees all of them.  `label_pins: all` restores the names verbatim."""
    from dataclasses import replace
    h = _example_hookup("perfboard")
    wired = hookup_to_svg(h)
    every = hookup_to_svg(replace(h, label_pins="all"))
    assert ">GPIO21</text>" not in wired
    assert ">GPIO21</text>" in every
    # the PAD is present in both — same ring count, only the text differs
    assert wired.count("<circle") == every.count("<circle")
    assert every.count("<text") > wired.count("<text")


def test_part_without_cached_art_keeps_generated_fallback():
    from composeio.render.engine.hookup import parse_hookup
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-c3", "at": [1, 2]}],
        "wires": [],
    })
    lay = layout_hookup(hk)
    box = lay.parts["mcu"]
    assert box.footprint is None          # esp32-c3.svg is not cached
    assert box.orient == "vertical"       # generated DIP path unchanged
    svg = hookup_to_svg(hk)
    assert "connector" not in svg
