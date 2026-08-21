"""
S3 hookup renderer — the three RENDERING DEFECTS, as gates.

A hookup diagram is READ AS FACT and soldered from.  These tests exist because
`docs/board-bringup/tm1637-quad-panel.hookup.svg` shipped with a wire that
appeared to exist and did not, and the only assertion standing between it and
the repo was "an SVG was produced".

  D1  CORRECTNESS — the drawn topology must equal the netlist EXACTLY: one
      drawn conductor per declared wire, every conductor terminating on a
      declared pad, no interior vertex that reads as a free wire end, and one
      electrical net drawn in ONE color (the TM1637 shared-CLK chain was drawn
      green / purple / orange, so one net read as three signals).
  D2  LINE HOPS — a crossing must READ as a crossing.  An un-hopped crossing is
      the same class of ambiguity as D1: it looks like a junction.
  D3  SILENT TEXT LOSS — `hookup_svg_to_png` returned a valid, label-free PNG
      with no error.  An instrument that can only report success is not an
      instrument, so the text gate here is paired with a NEGATIVE CONTROL that
      proves the gate can fail.

The fixtures are the committed dual-throttle example plus a synthetic CHAINED
BUS (one net daisy-chained across four parts, the TM1637 shared-CLK topology in
miniature) — the topology that produced every defect above.
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.engine.hookup import Hookup, parse_hookup
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.hookup_layout import (
    COLOR_SIGNAL, PAD_R, PITCH, layout_hookup, plan_hops, wire_nets,
    wire_segments,
)
from composeio.maker.hookup_svg import (
    HOP_R, hookup_svg_to_png, hookup_to_svg,
)

EXAMPLE = os.path.join(PY_ROOT, "composeio", "render", "examples",
                       "dual_throttle.compose.yaml")

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
# A CHAINED BUS: `mcu.GPIO0 -> p1.wiper -> p2.wiper -> p3.wiper -> p4.wiper`
# plus a second host tap onto the same net.  That is exactly the TM1637 panel's
# shared-CLK topology (one net, chained pin-to-pin between modules, tapped by
# the host) reduced to catalog parts that exist here — and it reproduced BOTH
# halves of D1: three reversal spurs and one net drawn in five colors.
_CHAINED_BUS = {
    "board": "perfboard",
    "parts": [
        # rows 11 apart: the pot footprint is 260 px tall against a 35 px row
        # step, so a tighter stack overlaps the BODIES and no router can keep a
        # wire off them.  This spacing is physically buildable, which is what
        # makes the invariants below meaningful rather than vacuous.
        {"id": "mcu", "part": "esp32-c3-supermini", "at": [12, 1]},
        {"id": "p1", "part": "pot-10k", "at": [0, 10]},
        {"id": "p2", "part": "pot-10k", "at": [11, 10]},
        {"id": "p3", "part": "pot-10k", "at": [22, 10]},
        {"id": "p4", "part": "pot-10k", "at": [33, 10]},
    ],
    "wires": [
        {"from": "mcu.GPIO0", "to": "p1.wiper", "publishes": "bus/clk"},
        {"from": "p1.wiper", "to": "p2.wiper"},
        {"from": "p2.wiper", "to": "p3.wiper"},
        {"from": "p3.wiper", "to": "p4.wiper"},
        {"from": "mcu.3V3", "to": "p1.vcc"},
        {"from": "p1.vcc", "to": "p2.vcc"},
        {"from": "p2.vcc", "to": "p3.vcc"},
        {"from": "mcu.GND", "to": "p1.gnd"},
        {"from": "p1.gnd", "to": "p2.gnd"},
        {"from": "mcu.GPIO1", "to": "p2.wiper"},
    ],
}


def _dual_throttle(board: str = "perfboard") -> Hookup:
    comp = parse_compose_yaml(EXAMPLE)
    h = comp.hookup
    assert h is not None
    if h.board != board:
        h = Hookup(board=board, parts=h.parts, wires=h.wires)
    return h


def _chained_bus(board: str = "perfboard") -> Hookup:
    return parse_hookup(dict(_CHAINED_BUS, board=board))


def _all_fixtures():
    return [
        ("dual_throttle/perfboard", _dual_throttle("perfboard")),
        ("dual_throttle/breadboard", _dual_throttle("breadboard")),
        ("chained_bus/perfboard", _chained_bus("perfboard")),
        ("chained_bus/breadboard", _chained_bus("breadboard")),
    ]


# ---------------------------------------------------------------------------
# reading the emitted SVG back — conductors are MARKED, so a test can count
# them without confusing them with the (many) <path> elements of footprint art
# ---------------------------------------------------------------------------
_CONDUCTOR = re.compile(r'<path d="([^"]+)"[^>]*data-wire="([^"]+)"[^>]*/>')
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _conductors(svg: str):
    """[(wire_id, path_d)] for every DRAWN CONDUCTOR in the document."""
    return [(m.group(2), m.group(1)) for m in _CONDUCTOR.finditer(svg)]


def _d_endpoints(d: str):
    """First and last coordinate pair of a path `d` (arcs included)."""
    nums = [float(v) for v in _NUM.findall(d)]
    return (nums[0], nums[1]), (nums[-2], nums[-1])


# ===========================================================================
# D1 — the drawn topology equals the netlist
# ===========================================================================
def test_one_drawn_conductor_per_declared_wire():
    """The count of drawn conductor paths equals the count of declared wires.

    This is the assertion the TM1637 guide never had.  It refutes the
    double-draw hypothesis directly and would catch any future renderer that
    emits an extra stroke per endpoint, per net, or per chain link."""
    for name, h in _all_fixtures():
        svg = hookup_to_svg(h)
        drawn = _conductors(svg)
        assert len(drawn) == len(h.wires), (
            f"{name}: {len(drawn)} drawn conductors for {len(h.wires)} "
            f"declared wires")
        # and each one is the wire it claims to be, exactly once
        declared = [f"{w.frm} {w.to}" for w in h.wires]
        assert sorted(wid for wid, _ in drawn) == sorted(declared), name


def test_every_conductor_terminates_on_a_declared_pad():
    """Both ends of every drawn conductor land on a declared pin coordinate —
    a conductor may never end in open space."""
    for name, h in _all_fixtures():
        lay = layout_hookup(h)
        pads = {(round(x, 2), round(y, 2)) for x, y in lay.pins.values()}
        for wid, d in _conductors(hookup_to_svg(h)):
            a, b = _d_endpoints(d)
            assert (round(a[0], 2), round(a[1], 2)) in pads, (name, wid, a)
            assert (round(b[0], 2), round(b[1], 2)) in pads, (name, wid, b)


def test_no_conductor_doubles_back_on_itself():
    """DEFECT 1, the segment terminating in nothing.

    The router escaped a pin PAST the trunk lane it then turned onto, so the
    polyline reversed — `M 619.6 158.94 L 540.6 158.94 L 558.0 158.94 …` on the
    TM1637 panel.  The overshoot is stroked with a round cap and reads as a
    17-px wire stub attached to nothing: a connection point that does not
    exist.  Every interior vertex must be a real 90 degree corner."""
    for name, h in _all_fixtures():
        for seg in wire_segments(h):
            pts = seg.points
            for k in range(len(pts) - 2):
                a, b, c = pts[k], pts[k + 1], pts[k + 2]
                dot = ((b[0] - a[0]) * (c[0] - b[0])
                       + (b[1] - a[1]) * (c[1] - b[1]))
                assert dot > -1e-9, (
                    f"{name}: {seg.frm}->{seg.to} reverses at {b} "
                    f"({a} -> {b} -> {c}) — draws a stub ending in open space")


def test_one_electrical_net_is_drawn_in_one_color():
    """DEFECT 1, the 'two green CLK wires'.

    Color was assigned per SIGNAL-WIRE INDEX mod 5, so the TM1637 shared-CLK
    chain came out green / purple / orange — ONE net wearing three signal
    colors, while green was simultaneously reused on an unrelated net.  Color
    must be a property of the NET."""
    for name, h in _all_fixtures():
        nets = wire_nets(h)
        by_net = {}
        for seg, net in zip(wire_segments(h), nets):
            by_net.setdefault(net, set()).add(seg.color)
        for net, colors in by_net.items():
            assert len(colors) == 1, (
                f"{name}: net {net} drawn in {len(colors)} colors: {colors}")


def test_distinct_signal_nets_use_distinct_colors_until_the_palette_runs_out():
    """The converse guard: a color is shared between two nets ONLY because the
    palette is exhausted, never because the assignment cycled early."""
    for name, h in _all_fixtures():
        segs = wire_segments(h)
        nets = wire_nets(h)
        sig_nets, sig_colors = set(), set()
        for seg, net in zip(segs, nets):
            if seg.role == "signal":
                sig_nets.add(net)
                sig_colors.add(seg.color)
        assert len(sig_colors) == min(len(sig_nets), len(COLOR_SIGNAL)), (
            name, len(sig_nets), len(sig_colors))


def test_the_endpoint_exemption_can_never_hide_a_body_traversal():
    """Fallout from D1, found while fixing it.

    The router lets a wire's FIRST and LAST segment overlap its own part body —
    the pin-to-edge exit off a pad that sits on the art.  The exemption was
    unbounded, and the moment reversal collapse made a previously-rejected lane
    reachable, a lead was accepted that entered the ESP32-C3 from the far side
    and ran 144 px straight across it.  The exemption is now capped at the
    pin's distance to its nearest body edge."""
    from composeio.render.engine.hookup_layout import (
        _exit_budget, _seg_rect_overlap,
    )
    for name, h in _all_fixtures():
        lay = layout_hookup(h)
        for seg in wire_segments(h, lay):
            n = len(seg.points)
            own = (seg.frm.split(".", 1)[0], seg.to.split(".", 1)[0])
            for k, (p, q) in enumerate(zip(seg.points, seg.points[1:])):
                for pid, box in lay.parts.items():
                    ov = _seg_rect_overlap(p, q, box.body)
                    if ov <= 1e-6:
                        continue
                    budget = 0.0
                    if k == 0 and pid == own[0]:
                        budget = _exit_budget(box.body, seg.points[0])
                    elif k == n - 2 and pid == own[1]:
                        budget = _exit_budget(box.body, seg.points[-1])
                    assert ov <= budget + 1e-6, (
                        f"{name}: {seg.frm}->{seg.to} segment #{k} covers "
                        f"{ov:.1f}px of {pid}'s body (budget {budget:.1f})")


# ===========================================================================
# D2 — line hops
# ===========================================================================
def _crossings(segs):
    """Every proper crossing of two DIFFERENT conductors: (i, j, x, y) with i's
    HORIZONTAL run crossing j's VERTICAL run through both interiors."""
    out = []
    for i, si in enumerate(segs):
        for (ax, ay), (bx, by) in zip(si.points, si.points[1:]):
            if abs(ay - by) > 1e-6:
                continue
            lo, hi = min(ax, bx), max(ax, bx)
            for j, sj in enumerate(segs):
                if j == i:
                    continue
                for (cx, cy), (dx, dy) in zip(sj.points, sj.points[1:]):
                    if abs(cx - dx) > 1e-6:
                        continue
                    ylo, yhi = min(cy, dy), max(cy, dy)
                    if lo + 1e-6 < cx < hi - 1e-6 and ylo + 1e-6 < ay < yhi - 1e-6:
                        out.append((i, j, cx, ay))
    return out


def test_every_crossing_is_hopped_or_explicitly_suppressed():
    """Every crossing of two conductors is accounted for: it either carries a
    hop, or it appears in the suppression list with a stated reason.  Silence
    is not an option — an un-hopped crossing reads as a junction."""
    for name, h in _all_fixtures():
        lay = layout_hookup(h)
        segs = wire_segments(h, lay)
        hops, suppressed = plan_hops(lay, segs)
        accounted = {(hp.wire, round(hp.x, 2), round(hp.y, 2)) for hp in hops}
        accounted |= {(s.wire, round(s.x, 2), round(s.y, 2))
                      for s in suppressed}
        for (i, j, x, y) in _crossings(segs):
            assert (i, round(x, 2), round(y, 2)) in accounted, (
                f"{name}: crossing of {segs[i].frm}->{segs[i].to} with "
                f"{segs[j].frm}->{segs[j].to} at ({x}, {y}) is neither hopped "
                f"nor suppressed")
        for s in suppressed:
            assert s.reason in ("pad", "corner", "crowded"), (name, s.reason)


def test_the_hop_rule_is_horizontal_over_vertical():
    """The stable rule: the HORIZONTAL run always hops the VERTICAL one.  It
    depends on geometry alone — never on wire order, index, color or net — so
    re-rendering a committed diagram can never flip which side bridges."""
    for name, h in _all_fixtures():
        lay = layout_hookup(h)
        segs = wire_segments(h, lay)
        hops, _ = plan_hops(lay, segs)
        for hp in hops:
            a = segs[hp.wire].points[hp.piece]
            b = segs[hp.wire].points[hp.piece + 1]
            assert abs(a[1] - b[1]) < 1e-6 and abs(a[0] - b[0]) > 1e-6, (
                name, "a hop was placed on a non-horizontal run", a, b)


def test_hops_are_deterministic():
    """A committed artifact must re-render byte-identically."""
    for name, h in _all_fixtures():
        first = hookup_to_svg(h)
        for _ in range(3):
            assert hookup_to_svg(h) == first, name


def test_a_hop_never_lands_on_a_pad():
    """A bridge over a solder pad would obscure the one thing on the diagram
    that IS a junction.  Such crossings are suppressed, with the reason."""
    for name, h in _all_fixtures():
        lay = layout_hookup(h)
        segs = wire_segments(h, lay)
        hops, _ = plan_hops(lay, segs)
        for hp in hops:
            for (px, py) in lay.pins.values():
                d = max(abs(px - hp.x), abs(py - hp.y))
                assert d > PAD_R, (name, "hop on a pad", hp, (px, py))


def test_hop_radius_cannot_reach_a_neighbouring_conductor():
    """Hop radius against wire pitch: distinct nets are guaranteed >= PITCH
    apart (the lane invariant), so a bridge that rises HOP_R plus half a stroke
    must stay inside half that gap or it would touch the neighbour it is trying
    to distinguish itself from."""
    assert HOP_R + 5.2 / 2.0 < PITCH / 2.0
    assert HOP_R > 5.2 / 2.0     # ...and still visibly an arc, not a bump


def test_three_way_crowding_suppresses_rather_than_overlapping():
    """Two crossings closer along one run than a bridge is wide cannot both
    hop — the arcs would merge into an unreadable blob.  The planner emits the
    first and suppresses the second with reason 'crowded' (left-to-right greedy
    == deterministic), never two overlapping arcs."""
    for name, h in _all_fixtures():
        lay = layout_hookup(h)
        segs = wire_segments(h, lay)
        hops, _ = plan_hops(lay, segs)
        by_piece = {}
        for hp in hops:
            by_piece.setdefault((hp.wire, hp.piece), []).append(hp.x)
        for key, xs in by_piece.items():
            xs = sorted(xs)
            for a, b in zip(xs, xs[1:]):
                assert b - a >= 2 * HOP_R, (name, key, a, b)


def test_a_hop_is_never_confusable_with_a_junction():
    """Junctions are drawn as FILLED pads; a hop is an unfilled arc in the
    conductor's own stroke color.  The emitted markup keeps them disjoint:
    every arc command lives on a `fill="none"` conductor path."""
    for name, h in _all_fixtures():
        svg = hookup_to_svg(h)
        arcs = 0
        for wid, d in _conductors(svg):
            arcs += d.count(" A ")
        # the conductor paths are the ONLY place an arc may appear, and they
        # are unfilled strokes
        for m in re.finditer(r"<path [^>]*/>", svg):
            frag = m.group(0)
            if " A " in frag.split('d="')[1].split('"')[0]:
                assert 'fill="none"' in frag, (name, frag[:120])
        # the chained-bus fixture genuinely crosses, so this is not vacuous
        if name.startswith("chained_bus"):
            assert arcs > 0, f"{name}: expected at least one hop"


# ===========================================================================
# D3 — the renderer may not silently drop text
# ===========================================================================
def test_png_rasterization_carries_the_labels(tmp_path):
    """`hookup_svg_to_png` produced a valid, LABEL-FREE PNG and no error: skia's
    `SVGDOM` has no `Builder`, hence no font manager, hence no text.  The PNG is
    now produced through the engine's own SVG -> Canvas lowering (the same path
    the doc-viewer uses, with the engine's FreeType text), and the function
    PROVES the labels reached the raster before returning."""
    h = _dual_throttle("perfboard")
    out = str(tmp_path / "hookup.png")
    hookup_svg_to_png(h, out, scale=1.0)
    assert os.path.exists(out) and os.path.getsize(out) > 5000


def test_the_gate_fires_on_the_KNOWN_BROKEN_rasterizer(tmp_path):
    """NEGATIVE CONTROL 1 — the shipped fault injector.

    `svgdom_rasterize` is kept public precisely because its failure mode is
    real; handed to the proof, it must be REFUSED and must leave no file."""
    from composeio.maker.hookup_svg import (
        rasterize_with_label_proof, svgdom_rasterize,
    )
    h = _dual_throttle("perfboard")
    lay = layout_hookup(h)
    out = str(tmp_path / "mute.png")
    with pytest.raises(RuntimeError, match="text elements removed"):
        rasterize_with_label_proof(svgdom_rasterize, hookup_to_svg(h), out,
                                   width=lay.width, height=lay.height)
    assert not os.path.exists(out), "a mute raster must not be left on disk"


def test_png_rasterization_REFUSES_when_text_cannot_render(tmp_path,
                                                           monkeypatch):
    """NEGATIVE CONTROL 2 — the gate is renderer-agnostic, not skia-specific.

    Stub the Canvas text primitive to a no-op (exactly what skia's font-manager-
    less SVGDOM did) and the rasterizer must REFUSE, not return a pretty PNG."""
    from composeio.render import skia_backend

    monkeypatch.setattr(skia_backend.SkiaCanvas, "draw_text",
                        lambda self, *a, **k: None)
    h = _dual_throttle("perfboard")
    out = str(tmp_path / "mute.png")
    with pytest.raises(RuntimeError, match="text"):
        hookup_svg_to_png(h, out, scale=1.0)
