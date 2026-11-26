"""composeio.decision_ops.hookup_review — ``send_hookup_for_human_review``, the
delivery path for the artifact with the HIGHEST cost of being misread.

WHY THIS EXISTS.  A decision-support doc that is misread produces a wrong
ANSWER — caught in the next exchange.  A wiring diagram that is misread produces
a wrong JOINT.  It is executed with a soldering iron, on boards that may already
be panelized, and the failure surfaces hours later as hardware behaving
inexplicably.  Until now the DSD (low cost of misreading) had a purpose-built
interactive path and the hookup guide (highest cost of misreading) had a generic
file send.  That inversion is the whole reason for this endpoint.

SIBLING, NOT A PARALLEL UNIVERSE.  This is ``dsd_review.send_dsd_for_human_review``'s
shape, deliberately: validate that the path is a real, parseable artifact under
the repo, publish an ``_app/hookup/open`` intent through the ComposeBus waist that
an in-process launcher (``compose_projects/intelligence/tools/hookup_viewer_launcher.py``) turns
into a viewer window, and return a correlatable status.  The repo-scope guard is
literally ``dsd_review.resolve_dsd`` — reuse by reference, not a second copy of a
containment check ([DX-NARROW-WAIST]).  Validation-only failure publishes
NOTHING; that is the DSD endpoint's stated contract and it matters more here.

FIVE RULINGS THIS FILE MAKES, each with its reason:

  1. IT TAKES THE SCENE AND RENDERS; IT DOES NOT ACCEPT A PICTURE.
     ``hookup_path`` is the ``.compose.yaml`` carrying the ``hookup:`` block, and
     the SVG/PNG are produced HERE, now, from that netlist.  A rendered image
     cannot prove which netlist it came from: a stale PNG is byte-for-byte
     indistinguishable from a fresh one, and the failure it produces is a wrong
     joint discovered hours later.  Accepting an image would reintroduce exactly
     the class of defect this endpoint is a gate against.  It costs the caller
     nothing — the scene is the single source for BOTH halves (netlist + bus
     bindings) and the intent has to carry it anyway.  Rendering here also buys
     the strict netlist validation for free: ``parse_hookup`` refuses a wire to a
     pin that does not exist, so a diagram of an unsound netlist is never sent.
     A ``.png``/``.svg`` argument is REFUSED with a message naming what to pass.

  2. AN UNLABELLED DIAGRAM IS REFUSED, NOT FLAGGED.
     ``hookup_svg.svgdom_rasterize`` (the core of the shipped
     ``hookup_svg_to_png``) silently drops EVERY text label — skia-python's
     ``SVGDOM`` has no ``Builder``, so there is no font manager — and returns a
     VALID PNG with no error.  A diagram with no labels is not a diagram: every
     wire endpoint's identity lives in the text.  REFUSE, because the delivery
     mode that already failed IS "a picture with a warning attached to it": the
     human looks at the picture, and a status blob nobody reads is not a gate.
     There is no legitimate case for sending an unlabelled wiring diagram.

     THE DETECTOR IS RENDERER-AGNOSTIC BY CONSTRUCTION: rasterize the SVG, then
     rasterize the SAME SVG with every ``<text>`` element stripped, THROUGH THE
     SAME RASTERIZER, and compare.  Identical rasters mean the labels
     contributed zero pixels.  No OCR, no font knowledge, no golden image, and —
     because both sides go through whichever rasterizer produced the delivered
     image — no false verdict from antialiasing differences between two
     different renderers.

     ⛔ THAT DETECTOR IS NOT IMPLEMENTED HERE.  It lives ONCE, in
     ``composeio.maker.hookup_svg.label_ink_report``, and :func:`label_survival`
     below is a thin adapter onto it.  It was briefly implemented twice — here
     and there, hours apart on 2026-08-15, by two agents that could not see each
     other — which is the THIRD instance of the fork ``[BP-TYPE-CODES]`` already
     collapsed once for the message-type table and once for the value codec.
     The renderer's gate (``rasterize_with_label_proof``) and this endpoint's
     gate are two SEMANTICS over one MEASUREMENT: the renderer refuses to write
     a mute PNG, this endpoint refuses to publish one.

  3. ⛔ A SKIPPED CROSSING IS A HARD FAILURE (David, 2026-08-15).
     Where two conductors cross, the horizontal one is drawn with a small
     bridge so the crossing cannot be read as a junction.  ``plan_hops``
     returns the crossings it could NOT bridge as a SECOND value, and its own
     docstring calls silence about them "the same class of lie as a wire that
     isn't there" — yet the only caller bound that value to ``_skipped`` and
     dropped it on the floor.  Measured on
     ``render/examples/tm1637_quad_panel.compose.yaml``: 57 hops planned, **7
     skipped** (6 ``crowded``, 1 ``corner``, five of them piled at one
     congested column at x=360).  Seven perfboard intersections that read as
     solder joints, shipped invisibly.

     REFUSE, publish NOTHING, and NAME EVERY ONE.  The ruling is verbatim:
     "let's start with skipped crossing being a hard failure, and if needed we
     can relax."  A refusal must be actionable — "7 crossings skipped" without
     the coordinates is a dead end, so :func:`crossing_refusal` names both
     conductors, the coordinate and the reason for each, plus the remedy per
     reason.  The gate runs BEFORE rasterization, because it is a property of
     the drawing, and because NO raster check can substitute for it: a crossing
     the planner skipped has no arc to lose.

     ⛔ THE BRIDGES MUST ALSO REACH THE PIXELS, which is a different failure
     with a different instrument.  The label gate exists because a rasterizer
     can silently drop every ``<text>``; nothing stood beside it for the ARCS,
     so an arc-stripped render sailed through the gate that refuses a
     text-stripped one.  :func:`hop_survival` closes that asymmetry, riding the
     SAME differential as the label gate (``_ink_vs_reference``, written once)
     with ``strip_hops`` in place of ``strip_text``.

  4. ⛔ PRESENCE IS NOT READABILITY (David, 2026-08-16).
     Ruling 2's gate proves the labels made PIXELS.  It structurally cannot
     prove they can be READ, and the difference shipped: the R503 + ESP32-S3
     guide passed every gate above — 67 crossings bridged and 0 skipped; 178
     labels, 59153 ink pixels, ``ok: true``; 67 bridges, 19103 ink pixels,
     ``ok: true`` — and an additional "0 body overlaps".  David opened it: *"the
     fingerprint sensor placement is so close to the pins that the wiring is
     unreadable."*  178 of 178 labels present, and colliding.

     THREE FINDINGS, and the third is the one that matters:
       * the body-overlap check answered "no overlap" TRUTHFULLY about two
         parts at EXACTLY ZERO clearance — an interior-intersection test asked
         a clearance question.  The clearance rule already existed
         (``hookup_layout._NESTLE_MARGIN``) and was simply never applied to
         ``at:``-anchored parts; it is now ``PART_CLEARANCE``;
       * ``label_survival`` is a differential against a text-STRIPPED render,
         so it answers "did any text reach the pixels".  178 labels stamped on
         one spot answer it YES.  The instrument was not wrong; it was asked
         the wrong question and its answer was read as the other one;
       * NOTHING measured collisions.  :func:`legibility_survival` does, over
         ``maker.hookup_svg.legibility_report``, pre-raster and for the same
         reason as ruling 3: two labels on one point still make ink.

     ⚠ AND IT STILL CANNOT CLOSE THE QUESTION.  Zero collisions means no two
     things are printed on each other.  Density, contrast and colour live
     outside it — ``label_wire`` is reported as a NOTE precisely because a
     hard bar there would be unreachable and would get relaxed until it meant
     nothing.  A PASS here is a precondition for asking David, never a
     substitute: ``[PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]``.

  4b. ⛔ A CONDUCTOR UNDER A PART BODY (David, 2026-08-16) — ruling 4's gate
     went green and the SECOND delivery came back too: 117 crossings all
     bridged, 0 skipped, 0 collisions, ``ok: true``.  *"I COULD use this
     diagram, but it's really not quite right — see how the FP component and
     its wires overlay the gnd for tm2 and tm4."*  Measured: two ground trunks
     and both 5 V runs rode ONE lane at y=858.0 for the FULL 134.2 px width of
     the R503, 4.4 px above its six pads.

     ⛔ THIS IS THE UN-HOPPED CROSSING, ONE LAYER UP, AND IT IS HARD.
     ``plan_hops`` bridges wire x wire so a crossing READS as a crossing.  A
     part body cannot be hopped and cannot be seen through, so the reader loses
     the conductor for the width of the part — and "where does this ground go"
     is the question a wiring guide exists to answer.  ``body_wire`` is
     therefore a REFUSAL and is kept strictly out of the ``label_wire`` note:
     the soft bucket is soft because a hard bar there is unreachable, and this
     bar is reachable.  The router was taught the same rectangle
     (``hookup_layout._assign_routes`` routes against ``_part_extent``, not
     ``body``) so the gate can be SATISFIED and not merely obeyed — a gate that
     can only refuse gets relaxed.

  5. NEGATIVE SPACE RIDES THE INTENT.
     A netlist can only express connections that EXIST.  The load-bearing
     knowledge in a hookup guide is frequently about connections that must NOT
     exist ("do not butt the modules together, it commons DIO"; "leave the -B
     header unconnected"; "do not add pull-ups, the module has them"; "never use
     GPIO2/8/9").  **A diagram cannot draw an absence.**  So the intent carries a
     ``warnings: [{text, kind, about, source}]`` list the viewer shows BESIDE the
     picture.  Two sources today: the caller's ``warnings=`` argument, and a
     tolerantly-read ``hookup: do_not:`` block in the scene (PROPOSED schema —
     see :func:`read_negative_space`; deliberately NOT added to the strict parser
     here).

Everything is injectable at the edges (``publish``, ``rasterize``) so
validate+render+gate+publish is testable with no broker and with a deliberately
broken renderer.
"""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Reuse by reference: the repo-containment guard and the canonical repo root are
# the DSD endpoint's, not a second copy that can drift from it.
from .dsd_review import (REPO_ROOT, own_checkout_root,
                         resolve_dsd as resolve_repo_file)

INTENT_NS = "_app/hookup/open"
DEFAULT_MQTT = os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883")
SRC = "hookup-review"

# Rendered artifacts are DERIVED and per-send; they live in a temp dir keyed by
# the scene path, never in the repo.  An MCP call must not commit files.
ARTIFACT_DIR = Path(tempfile.gettempdir()) / "composeio-hookup"

# 2.0 = double resolution, matching the maker projects' committed doc images.
DEFAULT_SCALE = 2.0

_IMAGE_SUFFIXES = (".png", ".svg", ".jpg", ".jpeg", ".pdf", ".webp")


# ===========================================================================
# scene -> hookup
# ===========================================================================
def resolve_scene(path: str) -> Optional[str]:
    """Resolve *path* under an allowed repo root (the DSD guard, reused).
    Returns the absolute path iff it is a real file inside the repo, else None."""
    return resolve_repo_file(path)


def load_hookup(abspath: str):
    """Parse the scene and return ``(hookup, error)``.

    The netlist validation is the S2 parser's, unchanged and STRICT: an unknown
    part, a duplicate id, or a wire endpoint naming a pin that does not exist on
    that part is an ERROR here rather than a silent pass in the picture.  Imports
    are LAZY so this module stays importable (and the MCP server stays bootable)
    without the render engine."""
    try:
        from composeio.render.engine.parser import parse_compose_yaml
    except Exception as exc:                                   # noqa: BLE001
        return None, f"render engine unavailable: {type(exc).__name__}: {exc}"
    try:
        comp = parse_compose_yaml(abspath)
    except Exception as exc:                                   # noqa: BLE001
        return None, f"scene does not parse: {type(exc).__name__}: {exc}"
    hookup = getattr(comp, "hookup", None)
    if hookup is None:
        return None, ("scene carries no `hookup:` block — nothing to draw. The "
                      "physical half (board/parts/wires) belongs in the SAME "
                      "`.compose.yaml` as the bindings; do not fork a separate "
                      "wiring file.")
    return hookup, ""


# The module-level indirection is deliberate: tests monkeypatch these to simulate
# an upstream renderer regression without touching the maker package.
def render_guide(hookup):
    """``(svg_text, CrossingReport)`` — **THE render call this endpoint makes**,
    and the seam a test injects a broken renderer at.

    ⛔ THERE IS NO ``hookup_to_svg`` INDIRECTION HERE ANY MORE, deliberately.
    ``maker.hookup_svg.hookup_to_svg`` returns the picture and DROPS the
    crossing verdict — which is exactly the discard this endpoint now exists to
    catch (``plan_hops`` returns ``(hops, skipped)``; the sole caller bound the
    second value to ``_skipped``, and seven un-bridged crossings shipped on the
    TM1637 panel as intersections a reader takes for solder joints).  A delivery
    path must not be able to make that mistake by reaching for the shorter
    function name, and the name was REMOVED rather than kept-and-documented so
    that a stale ``monkeypatch.setattr(hr, "hookup_to_svg", ...)`` raises
    AttributeError instead of silently patching something nothing calls.  A
    fault injector that no longer injects is the loudest possible false green."""
    from composeio.maker.hookup_svg import render_hookup as _f
    return _f(hookup)


def layout_of(hookup):
    from composeio.maker.hookup_svg import layout_hookup as _f
    return _f(hookup)


def canvas_rasterize(svg: str, out_png: str, *, width: float, height: float,
                     scale: float = DEFAULT_SCALE) -> str:
    """THE DEFAULT rasterizer — lowers the SVG through this repo's own Canvas
    contract, which has a font stack, so the labels survive.  Thin re-export so
    the endpoint's default is nameable and injectable."""
    from composeio.maker.hookup_svg import canvas_rasterize as _f
    return _f(svg, out_png, width=width, height=height, scale=scale)


# ===========================================================================
# ⛔ THE LABEL GATE — one implementation, borrowed; two semantics, ours
#
# The MEASUREMENT lives in `composeio.maker.hookup_svg` beside the rasterizers
# it measures, because the renderer needs it too (`rasterize_with_label_proof`
# refuses to WRITE a mute PNG; this endpoint refuses to PUBLISH one).  The three
# names below are lazy re-exports, matching this module's existing idiom — the
# maker package pulls in the render engine at import time, and this module must
# stay importable (the MCP server bootable) without it.
# ===========================================================================
def strip_text(svg: str) -> str:
    """*svg* with every ``<text>`` element removed.  ``hookup_svg.strip_text``."""
    from composeio.maker.hookup_svg import strip_text as _f
    return _f(svg)


def count_text_elements(svg: str) -> int:
    """How many NON-EMPTY ``<text>`` elements the SVG source carries.  A hookup
    SVG with zero is unlabelled AT THE SOURCE (an upstream regression), and is
    refused before anything is rasterized.  ``hookup_svg.count_text_elements``."""
    from composeio.maker.hookup_svg import count_text_elements as _f
    return _f(svg)


def label_survival(svg: str, width: float, height: float, *,
                   rasterize: Callable[..., str],
                   workdir: str,
                   scale: float = DEFAULT_SCALE,
                   out_png: Optional[str] = None) -> Dict[str, Any]:
    """DID THE LABELS SURVIVE RASTERIZATION?  **The endpoint's adapter onto
    :func:`composeio.maker.hookup_svg.label_ink_report`** — the one
    implementation of the same-rasterizer differential.  It is deliberately not
    a second copy of that comparison: the fork this replaced is the third
    instance of a defect class ``[BP-TYPE-CODES]`` has already collapsed twice.

    All this adds is the endpoint's file convention (*workdir* supplies the
    default output path when the caller does not name one, so validate → render
    → gate → publish rasterizes exactly once).

    Returns the report verbatim: ``{in_svg, ink_px, ratio, ok, sparse, png,
    removed, rasterizer, reason}``.  ``ok`` False means the labels made ZERO
    pixels and the mute PNG has been DELETED (its path is under ``removed``);
    ``sparse`` True means present-but-thin, which is a NOTE and never a
    refusal."""
    from composeio.maker.hookup_svg import label_ink_report
    os.makedirs(workdir, exist_ok=True)
    return label_ink_report(
        rasterize, svg, out_png or os.path.join(workdir, "labelcheck.full.png"),
        width=width, height=height, scale=scale)


def hop_survival(svg: str, width: float, height: float, *,
                 rasterize: Callable[..., str],
                 workdir: str,
                 scale: float = DEFAULT_SCALE,
                 out_png: Optional[str] = None,
                 drawn_png: str = "") -> Dict[str, Any]:
    """DID THE LINE HOPS SURVIVE RASTERIZATION?  :func:`label_survival`'s
    sibling, adapting the SAME differential
    (``composeio.maker.hookup_svg.hop_ink_report``) onto this endpoint's file
    convention.  Not a second copy of anything: the measurement is
    ``_ink_vs_reference``, written once, and only the removed feature differs.

    WHY IT IS HERE AT ALL.  The label gate was built because a rasterizer can
    silently drop every ``<text>`` and still return a valid PNG.  Nothing stood
    beside it for the BRIDGES — so a label-stripped render was refused while an
    arc-stripped one, in which every crossing has become a plain intersection,
    sailed through the identical gate.  Same loss, same soldering iron.

    *drawn_png* is the raster the LABEL gate just wrote (same rasterizer, same
    scale), re-used so validate -> render -> gate -> publish still rasterizes
    the full document exactly once.

    ⚠ ``ok`` is True when the SVG declares NO arcs — a diagram whose conductors
    never cross has no bridges to lose.  Whether the planner SKIPPED a crossing
    is a different question with a different instrument (``CrossingReport.ok``,
    gated before anything is rasterized) — this one cannot see a bridge that was
    never drawn, and must not be mistaken for the gate that can."""
    from composeio.maker.hookup_svg import hop_ink_report
    os.makedirs(workdir, exist_ok=True)
    return hop_ink_report(
        rasterize, svg, out_png or os.path.join(workdir, "hopcheck.full.png"),
        width=width, height=height, scale=scale, drawn_png=drawn_png)


def legibility_survival(svg: str, *, layout, hookup) -> Dict[str, Any]:
    """CAN THE DRAWING BE READ?  The endpoint's adapter onto
    :func:`composeio.maker.hookup_svg.legibility_report` — the one
    implementation, matching this module's existing lazy-import idiom, adding
    only the wire segments (so the note-level ``label_wire`` count is populated
    for the intent) and nothing else.

    ⛔ NOT A SECOND COPY OF ANY COMPARISON.  The label gate was forked once
    already by two agents who could not see each other; the naming is the
    prevention."""
    from composeio.maker.hookup_svg import legibility_report
    from composeio.render.engine.hookup_layout import wire_segments
    return legibility_report(svg, layout,
                             segs=wire_segments(hookup, layout))


def legibility_refusal(report: Dict[str, Any], relpath: str) -> str:
    """The refusal text for an unreadable diagram — a WORK ORDER, not a verdict.

    Same shape as :func:`crossing_refusal` and for the same reason: "the
    drawing is illegible" is a dead end, while "`fp` and `mcu` have 0.0 px of
    clearance and need 22.0" is a one-line scene edit.

    ⛔ ``body_wire`` IS LISTED FIRST because it is the class that survived two
    machine-green deliveries: a conductor drawn beneath an opaque part body.
    David, 2026-08-16: *"see how the FP component and its wires overlay the gnd
    for tm2 and tm4."*  It is NOT the ``label_wire`` note one line further down
    and must never be folded into it — a label grazing a wire is dense, a
    conductor vanishing under a part is unfollowable, and it cannot be bridged
    the way a crossing can."""
    lines = [f"{relpath}: REFUSED — the rendered guide is not readable."]
    for r in report.get("body_wire", [])[:10]:
        lines.append(
            f"  · conductor '{r['wire']}' ({r['role']}) is drawn ACROSS part "
            f"'{r['part']}' — {r['run']} px of it, from {r['at']}, over a part "
            f"{r['part_width']} px wide. A crossing can be BRIDGED; a part body "
            f"cannot, so the reader loses this conductor for the width of the "
            f"part. Move the part out of the trunk lane, or open a channel the "
            f"trunk can use.")
    for r in report["body_body"]:
        lines.append(
            f"  · parts '{r['a']}' and '{r['b']}' have {r['clearance']} px of "
            f"clearance and need {r['required']}. A grid COLUMN is not a part "
            f"width — move one part's `at:` until the extents clear.")
    for r in report["label_body"][:10]:
        lines.append(
            f"  · label '{r['label']}' (of '{r['owner']}') prints across part "
            f"'{r['part']}' at {r['at']}, overlapping by {r['overlap']} px.")
    for r in report["label_label"][:10]:
        lines.append(
            f"  · labels '{r['a']}' ({r['a_owner']}) and '{r['b']}' "
            f"({r['b_owner']}) overprint at {r['at']} (gap {r['gap']} px) — "
            f"two names on one spot read as a third word.")
    extra = (len(report["label_body"]) + len(report["label_label"])
             - min(10, len(report["label_body"]))
             - min(10, len(report["label_label"])))
    if extra > 0:
        lines.append(f"  · ... and {extra} more label collision(s).")
    hidden = len(report.get("body_wire", [])) - min(
        10, len(report.get("body_wire", [])))
    if hidden > 0:
        lines.append(f"  · ... and {hidden} more conductor(s) under a part.")
    lines.append("Nothing was published. A diagram is read as fact, and an "
                 "overprinted label reads as a different word — the same class "
                 "of lie as an un-bridged crossing.")
    return "\n".join(lines)


# ===========================================================================
# ⛔ THE CROSSING GATE — a skipped crossing is a HARD FAILURE (David 2026-08-15)
#
# `plan_hops` bridges a crossing so it cannot be read as a junction, and returns
# the crossings it COULD NOT bridge as a second value whose own docstring says
# silence "would let an un-hopped crossing pass unnoticed ... the same class of
# lie as a wire that isn't there".  The single caller discarded that value.
# Measured on `render/examples/tm1637_quad_panel.compose.yaml`: 57 hops planned,
# 7 skipped (6 `crowded`, 1 `corner`), five piled at one congested column
# (x=360) — seven perfboard intersections that read as solder joints.
#
# RULING: refuse.  "Let's start with skipped crossing being a hard failure, and
# if needed we can relax."  Not a warning on the intent: the delivery mode that
# has already failed IS "a picture with a warning attached to it".
#
# THE REFUSAL MUST BE A WORK ORDER.  "7 crossings skipped" is a dead end — the
# author has to re-route a specific column.  Every crossing is named by both
# conductors, its coordinate and its reason, and each reason carries its remedy
# (`hookup_layout.skip_remedy`), so the message says what to DO.
# ===========================================================================
def crossing_refusal(report, relpath: str) -> str:
    """The refusal text for a diagram with un-bridged crossings.  One wording,
    built from the report's own rows, so nothing here can drift from what the
    planner actually decided."""
    from composeio.render.engine.hookup_layout import skip_remedy
    rows = report.skipped_rows()
    reasons = sorted({str(r["reason"]) for r in rows})
    return (
        f"{relpath}: REFUSED — {len(rows)} conductor crossing(s) could NOT be "
        f"bridged, out of {len(report.hops) + len(rows)} crossings in this "
        f"drawing. An un-bridged crossing renders as a plain intersection, and "
        f"on a wiring guide a plain intersection READS AS A JUNCTION — a solder "
        f"joint that must not exist. This diagram is not deliverable until the "
        f"layout is re-routed.\n"
        + report.skipped_text()
        + "\n\nHOW TO FIX:\n"
        + "\n".join(f"  [{r}] {skip_remedy(r)}" for r in reasons))


# ===========================================================================
# NEGATIVE SPACE — the connections that must NOT exist
# ===========================================================================
# PROPOSED SCHEMA ADDITION (NOT built into the strict parser here, per the
# standing instruction to propose rather than build):
#
#     hookup:
#       board: perfboard
#       parts: [...]
#       wires: [...]
#       do_not:                                  # <- the negative space
#         - text: Do not butt the modules together — the pass-through header
#                 commons DIO.
#           kind: no-common                      # no-common | leave-unconnected
#           about: [disp0, disp1]                # | no-pullup | reserved-pin
#         - text: Never use GPIO2/8/9 — ESP32-C3 boot straps.
#           kind: reserved-pin
#           about: [mcu.GPIO2, mcu.GPIO8, mcu.GPIO9]
#         - Do not add pull-ups; the module has them.        # bare-string form
#
# WHY IN THE SCENE: the same single-source argument the netlist already won. A
# warning that lives in the guide markdown drifts from the drawing the moment
# either is edited; one that lives beside `wires:` cannot.
#
# WHY `about:` IS THE PAYOFF: it names REAL part/pin ids, which makes the
# negative space CHECKABLE rather than decorative. A `kind: reserved-pin` entry
# naming `mcu.GPIO2` can be cross-checked against the netlist and become a HARD
# ERROR if a wire actually lands there; `no-common` can be checked against the
# routed topology. And the viewer can HIGHLIGHT the referenced pins in the
# drawing — which is how an absence finally gets a visual anchor without drawing
# a wire that does not exist.
#
# Read tolerantly below (raw YAML, never a validation error) so a scene can carry
# it TODAY and the strict schema can land later without a migration.
_WARNING_KINDS = ("no-common", "leave-unconnected", "no-pullup", "reserved-pin",
                  "no-power-order", "note")


def _warning(text: str, *, source: str, kind: str = "note",
             about: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"text": str(text).strip(), "kind": kind if kind in _WARNING_KINDS
            else "note", "about": [str(a) for a in (about or [])],
            "source": source}


def read_negative_space(abspath: str) -> List[Dict[str, Any]]:
    """The scene's ``hookup: do_not:`` entries, read TOLERANTLY from raw YAML.

    Never raises and never fails a send: this key is a PROPOSAL, so a scene that
    lacks it (all of them, today) is normal and a scene that malforms it loses
    only the malformed entry."""
    try:
        import yaml
        with open(abspath, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        raw = ((doc.get("hookup") or {}).get("do_not")) or []
    except Exception:                                          # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(_warning(item, source="scene"))
        elif isinstance(item, dict):
            text = item.get("text") or item.get("warning") or ""
            if not str(text).strip():
                continue
            about = item.get("about") or item.get("pins") or []
            if isinstance(about, str):
                about = [about]
            out.append(_warning(text, source="scene",
                                kind=str(item.get("kind") or "note"),
                                about=list(about)))
    return out


# ===========================================================================
# publish
# ===========================================================================
def _publish_intent_bus(mqtt_url: str, payload: dict) -> None:
    """One-shot publish of the open intent THROUGH THE COMPOSEBUS WAIST — the
    same framing seam ``dsd_review._publish_intent_mqtt`` uses, for the same
    reason: a hand-framed envelope is a dialect no canonical decoder can read.

    ONE-SHOT, SO IT FLUSHES: this runs inside an MCP server process with no bus
    and no event loop, and may exit immediately after; ``bus.stop()`` runs the
    transport's ``loop_stop`` + ``disconnect``, which flushes paho's outbound
    queue.  Without it a QoS-1 intent vanishes silently on a fast exit."""
    from composeio import source_bus
    from composeio.namespace import Scope
    bus = source_bus.acquire_bus(mqtt_url, label=f"{SRC}-{os.getpid()}")
    source_bus.declare(bus, INTENT_NS, scope=Scope.GLOBAL, source=SRC,
                       label="hookup open intent")
    bus.publish_state(INTENT_NS, payload, source=SRC)
    bus.stop()
    source_bus.reset_for_tests()   # drop the memo: this process keeps no bus


def _artifact_stem(abspath: str) -> Path:
    h = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:12]
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(abspath))
    stem = stem[:-len(".compose.yaml")] if stem.endswith(".compose.yaml") else stem
    return ARTIFACT_DIR / f"{stem[:48]}.{h}"


def _relpath(abspath: str) -> str:
    try:
        return os.path.relpath(abspath, REPO_ROOT)
    except ValueError:
        return abspath


def _err(path: str, message: str, **extra) -> Dict[str, Any]:
    return {"status": "error", "path": path, "correlation_id": "",
            "message": message, **extra}


# ===========================================================================
# THE ENDPOINT
# ===========================================================================
def send_hookup_for_human_review(
        hookup_path: str, *,
        mqtt_url: str = DEFAULT_MQTT,
        correlation_id: Optional[str] = None,
        warnings: Optional[List[str]] = None,
        scale: float = DEFAULT_SCALE,
        publish: Optional[Callable[[dict], None]] = None,
        rasterize: Optional[Callable[..., str]] = None) -> Dict[str, Any]:
    """The MCP tool.  Validate *hookup_path* (a repo-scoped ``.compose.yaml``
    with a SOUND ``hookup:`` netlist), RENDER the guide, GATE on every crossing
    being bridged, on the labels surviving and on the bridges surviving, publish
    an ``_app/hookup/open`` intent, and return a correlatable status.

    Returns::

        {"status": "sent"|"error", "path": <scene abspath>,
         "correlation_id": <id>, "message": <human text>,
         "svg": <path>, "png": <path>, "labels": {...}, "hops": {...},
         "crossings": {"hopped": n, "skipped": 0, "reasons": {}, "rows": []},
         "negative_space": {"count": n, "sources": [...], "note": ...}}

    NOTHING IS PUBLISHED on any validation failure — not an unresolvable path,
    not a pre-rendered image, not an unsound netlist, not an unlabelled render
    (``refused_unlabelled: True``), and ⛔ NOT a drawing with an un-bridged
    crossing (``refused_unhopped_crossing: True``, with ``crossings.rows``
    naming every one by conductor, coordinate and reason).

    ⚠ THE CROSSING GATE MAKES REAL SCENES UNDELIVERABLE UNTIL THEY ARE
    RE-ROUTED.  ``render/examples/tm1637_quad_panel.compose.yaml`` skips 7
    crossings today and is refused by this endpoint.  That is the ruling
    working, not a regression: those seven intersections read as solder joints
    on a perfboard guide."""
    raster = rasterize or canvas_rasterize

    # -- 1. repo-scoped, real file -----------------------------------------
    abspath = resolve_scene(hookup_path)
    if abspath is None:
        return _err(hookup_path, f"not a repo file: {hookup_path!r}")

    # -- 2. it must be the SCENE, not a picture of one ----------------------
    if abspath.lower().endswith(_IMAGE_SUFFIXES):
        return _err(abspath,
                    "this endpoint takes the SOURCE `.compose.yaml`, not an "
                    f"already-rendered image ({os.path.basename(abspath)}). A "
                    "rendered image cannot prove which netlist it came from — a "
                    "stale one is indistinguishable from a fresh one, and what "
                    "it produces is a wrong JOINT, hours later. Pass the scene "
                    "carrying the `hookup:` block; the diagram is rendered here, "
                    "from that netlist, now.")

    # -- 3. the netlist must be SOUND (strict S2 validation) ----------------
    hookup, why = load_hookup(abspath)
    if hookup is None:
        return _err(abspath, f"{_relpath(abspath)}: {why}")

    # -- 4. render, then ⛔ GATE ON THE LABELS -------------------------------
    stem = _artifact_stem(abspath)
    workdir = str(stem.parent)
    svg_path = str(stem) + ".svg"
    png_path = str(stem) + ".png"
    try:
        svg, crossings = render_guide(hookup)
        lay = layout_of(hookup)
        os.makedirs(workdir, exist_ok=True)
        with open(svg_path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    except Exception as exc:                                   # noqa: BLE001
        return _err(abspath, f"render failed: {type(exc).__name__}: {exc}")

    if count_text_elements(svg) == 0:
        return _err(abspath,
                    f"{_relpath(abspath)}: the rendered guide carries NO text "
                    "labels at all — a diagram with no labels is not a diagram, "
                    "every wire endpoint's identity lives in the text. Refusing "
                    "to send. (Unlabelled at the SOURCE: the SVG exporter itself "
                    "emitted no <text>.)",
                    refused_unlabelled=True, svg=svg_path)

    # -- 4b. ⛔ GATE ON THE CROSSINGS ---------------------------------------
    # Before anything is rasterized, because this is a property of the DRAWING,
    # not of the pixels — and because no raster check can ever see it: a
    # crossing the planner skipped has no arc to lose, so the hop-survival gate
    # below is blind to it BY CONSTRUCTION. Two failures, two instruments.
    if not crossings.ok:
        return _err(abspath, crossing_refusal(crossings, _relpath(abspath)),
                    refused_unhopped_crossing=True, svg=svg_path,
                    crossings=crossings.as_dict())

    # -- 4b2. ⛔ GATE ON LEGIBILITY -----------------------------------------
    # PRESENCE IS NOT READABILITY, and this endpoint had no instrument for the
    # difference.  The R503 guide shipped through every gate above — 67
    # crossings bridged, 178 labels with 59153 ink pixels — with one part
    # printed across another and both edge columns carrying doubled pin names.
    # Also pre-raster, and for the same reason as the crossing gate: two labels
    # stamped on the same point still make ink, so no differential can see it.
    try:
        legibility = legibility_survival(svg, layout=lay, hookup=hookup)
    except Exception as exc:                                   # noqa: BLE001
        return _err(abspath, f"legibility check failed: "
                             f"{type(exc).__name__}: {exc}", svg=svg_path)

    if not legibility["ok"]:
        return _err(abspath, legibility_refusal(legibility, _relpath(abspath)),
                    refused_illegible=True, svg=svg_path,
                    legibility=legibility)

    try:
        labels = label_survival(svg, float(lay.width), float(lay.height),
                                rasterize=raster, workdir=workdir,
                                scale=scale, out_png=png_path)
    except Exception as exc:                                   # noqa: BLE001
        return _err(abspath, f"rasterization failed: {type(exc).__name__}: {exc}")

    if not labels["ok"]:
        # No `png=` in the refusal: the primitive DELETED the mute raster, and
        # handing back a path to it would be the "picture with a warning
        # attached" this endpoint exists to refuse — in the one form where the
        # picture is also a lie about what is on disk.
        return _err(abspath,
                    f"{_relpath(abspath)}: REFUSED — the diagram's "
                    f"{labels['in_svg']} text labels did not survive "
                    f"rasterization. {labels['reason']} The mute PNG was "
                    f"deleted, not delivered.",
                    refused_unlabelled=True, svg=svg_path, labels=labels)

    # -- 4c. ⛔ GATE ON THE BRIDGES REACHING THE PIXELS ----------------------
    # The label gate's sibling. It re-uses the raster the label gate just wrote
    # (same rasterizer, same scale), so this costs one probe, not one document.
    try:
        hops = hop_survival(svg, float(lay.width), float(lay.height),
                            rasterize=raster, workdir=workdir, scale=scale,
                            out_png=png_path, drawn_png=labels["png"])
    except Exception as exc:                                   # noqa: BLE001
        return _err(abspath, f"rasterization failed: {type(exc).__name__}: {exc}")

    if not hops["ok"]:
        # Same rule as the mute raster: the primitive DELETED it, and no `png=`
        # rides the refusal.
        return _err(abspath,
                    f"{_relpath(abspath)}: REFUSED — the diagram's "
                    f"{hops['in_svg']} line-hop bridges did not survive "
                    f"rasterization. {hops['reason']} The bridge-free PNG was "
                    f"deleted, not delivered.",
                    refused_unhopped_crossing=True, svg=svg_path, hops=hops,
                    labels=labels)

    # -- 5. negative space: what must NOT be connected ----------------------
    warn: List[Dict[str, Any]] = [_warning(w, source="caller")
                                  for w in (warnings or []) if str(w).strip()]
    warn.extend(read_negative_space(abspath))
    sources = sorted({w["source"] for w in warn})
    ns_note = ("this diagram carries NO negative-space warnings — almost every "
               "hookup guide has at least one connection that must NOT exist, "
               "and a drawing cannot draw an absence. Pass `warnings=[...]` or "
               "add a `hookup: do_not:` block to the scene."
               if not warn else
               f"{len(warn)} warning(s) ride the intent; the viewer shows them "
               "beside the drawing.")

    # -- 6. publish + a correlatable status ---------------------------------
    corr = correlation_id or uuid.uuid4().hex[:12]
    intent = {
        # `path` is the doc/dsd-family alias so a generic consumer still works;
        # `scene` is the name that says what it IS.
        "path": abspath,
        "scene": abspath,
        "svg": svg_path,
        "png": png_path,
        "correlation_id": corr,
        "board": hookup.board,
        "parts": len(hookup.parts),
        "wires": len(hookup.wires),
        "warnings": warn,
        "labels": {k: labels[k] for k in ("in_svg", "ink_px", "ratio", "sparse")},
        # Both crossing facts ride the intent: how many bridges the planner drew
        # (and that ZERO were skipped — the gate above proved it), and that
        # those bridges made pixels in the delivered raster.
        "crossings": crossings.as_dict(),
        "hops": {k: hops[k] for k in ("in_svg", "ink_px", "ratio", "sparse")},
        # ⚠ LEGIBILITY IS A PRECONDITION, NEVER A VERDICT.  Zero collisions
        # means no two things are printed on each other; it does NOT mean the
        # drawing can be read. `label_wire` (a NOTE) says how many labels share
        # space with a conductor, so a human can see the density the gate
        # cannot judge — `[PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]`.
        # ⛔ `body_wire: 0` is a FACT ON THE INTENT, not an absence: the class
        # that survived two machine-green deliveries (a conductor drawn beneath
        # an opaque part body) was proved to be empty, and an absent key is
        # indistinguishable from one nobody thought to write.
        "legibility": {"labels": legibility["labels"],
                       "collisions": 0,
                       "body_wire": len(legibility["body_wire"]),
                       "crowded": len(legibility["crowded"]),
                       "label_wire": len(legibility["label_wire"])},
    }
    sink = publish or (lambda p: _publish_intent_bus(mqtt_url, p))
    try:
        sink(intent)
    except Exception as exc:                                   # noqa: BLE001
        return {"status": "error", "path": abspath, "correlation_id": corr,
                "message": f"publish failed: {exc}"}

    msg = (f"sent for review: {_relpath(abspath)} — {hookup.board}, "
           f"{len(hookup.parts)} parts, {len(hookup.wires)} wires, "
           f"{labels['in_svg']} labels VERIFIED present in the raster, "
           f"{crossings.as_dict()['hopped']} crossings bridged and 0 skipped "
           f"(correlation {corr})")
    if labels["sparse"]:
        msg += (" ⚠ label ink is unusually sparse for this drawing size — LOOK "
                "at the image before soldering from it")
    if hops["sparse"]:
        msg += (" ⚠ bridge ink is thinner than one pixel per declared bridge — "
                "individual crossings may be vanishing; LOOK at the image")
    msg += (f", 0 label collisions and 0 conductors under a part body ⚠ but "
            f"LEGIBILITY IS NOT MACHINE-CHECKABLE: "
            f"{len(legibility['label_wire'])} label(s) share space with a "
            f"conductor and contrast/density need EYES")
    return {"status": "sent", "path": abspath, "correlation_id": corr,
            "message": msg, "svg": svg_path, "png": png_path, "labels": labels,
            "crossings": crossings.as_dict(), "hops": hops,
            "legibility": legibility,
            "negative_space": {"count": len(warn), "sources": sources,
                               "note": ns_note}}


# ===========================================================================
# selftest — logic only, no broker, injected publish + rasterizer
# ===========================================================================
def _selftest() -> int:
    """⛔ THE FIXTURES COME FROM THIS MODULE'S OWN CHECKOUT — see
    [CXC-HKR-SELFTEST-ROOT].

    Every fixture below is made ABSOLUTE against :func:`own_checkout_root`
    before it is passed in. It used to pass repo-RELATIVE paths, which the
    containment guard resolves against the CANONICAL checkout first — so a copy
    of this module imported from a linked worktree rendered the CANONICAL tree's
    netlist while executing the worktree's code. On 2026-08-15 that reported
    checks (9), (10) and (16) RED on a checkout where all seventeen were green,
    and (16) reads as "the label gate did not fire" — the loudest possible false
    alarm about the one gate standing between a mute diagram and a soldering
    iron. The gate was never involved; the scene never loaded.

    Which is the second ruling here: a fixture that will not LOAD is a HARNESS
    failure, reported once, by name. It is never allowed to fall through and be
    counted as a failure of the behaviour it was meant to exercise. An
    instrument that cannot tell "the gate did not fire" from "the gate never
    ran" is not an instrument."""
    fails: List[str] = []
    root = own_checkout_root()

    def fixture(rel: str) -> str:
        """A fixture path, absolute, in the tree this code is made of."""
        return str(root / rel)

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    published: List[dict] = []

    def fake_publish(intent):
        published.append(intent)

    # -- pure helpers (no renderer needed) ---------------------------------
    svg = ('<svg viewBox="0 0 10 10"><rect width="10" height="10"/>'
           '<text x="1" y="2">GPIO0</text><text x="3" y="4"></text>'
           '<text x="5" y="6"/></svg>')
    check("<text" not in strip_text(svg), "(1) strip_text removes every <text>")
    check("<rect" in strip_text(svg), "(2) strip_text keeps the geometry")
    check(count_text_elements(svg) == 1,
          "(3) empty/self-closing <text> are not labels")
    check(count_text_elements("<svg/>") == 0, "(4) no text -> 0")

    # -- guards that need no render ----------------------------------------
    r = send_hookup_for_human_review("/etc/passwd", publish=fake_publish)
    check(r["status"] == "error" and not published,
          "(5) outside-repo -> error, no publish")
    r = send_hookup_for_human_review(fixture("python/nope.compose.yaml"),
                                     publish=fake_publish)
    check(r["status"] == "error" and not published,
          "(6) missing -> error, no publish")

    png = fixture("python/composeio/maker/projects/dual_throttle/artifacts/"
                  "hookup-guide.perfboard.png")
    if resolve_scene(png) is not None:
        r = send_hookup_for_human_review(png, publish=fake_publish)
        check(r["status"] == "error" and not published
              and ".compose.yaml" in r["message"],
              "(7) pre-rendered image REFUSED, names what to pass instead")

    no_hookup = fixture("compose_projects/intelligence/console/dsdviewer.compose.yaml")
    if resolve_scene(no_hookup) is not None:
        r = send_hookup_for_human_review(no_hookup, publish=fake_publish)
        check(r["status"] == "error" and not published and "hookup:" in r["message"],
              "(8) scene with no hookup: block -> error, no publish")

    # -- the full path, including ⛔ the label gate both ways ----------------
    scene = fixture("python/composeio/render/examples/dual_throttle.compose.yaml")
    if resolve_scene(scene) is None:
        print("SKIP: fixture scene missing:", scene)
    elif load_hookup(resolve_scene(scene))[1]:
        # HARNESS, not behaviour. Report once, by name, and do NOT run the gate
        # checks — a scene that never parsed cannot tell you anything about a
        # gate that never saw it.
        print(f"HARNESS: fixture did not load, so the gate was never exercised "
              f"— checkout root {root}, scene {scene}: "
              f"{load_hookup(resolve_scene(scene))[1]}")
        return 1
    else:
        try:
            from composeio.maker.hookup_svg import svgdom_rasterize
        except Exception as exc:                               # noqa: BLE001
            print("SKIP: renderer unavailable:", exc)
        else:
            published.clear()
            r = send_hookup_for_human_review(scene, correlation_id="st-1",
                                             warnings=["do not common DIO"],
                                             publish=fake_publish)
            check(r["status"] == "sent", "(9) valid scene -> sent")
            check(len(published) == 1, "(10) exactly one intent published")
            if published:
                i = published[0]
                check(i["scene"] == resolve_scene(scene),
                      "(11) intent carries the SOURCE .compose.yaml")
                check(os.path.isfile(i["svg"]) and os.path.isfile(i["png"]),
                      "(12) both rendered halves exist on disk")
                check(i["correlation_id"] == "st-1" == r["correlation_id"],
                      "(13) correlation id returned + on the intent")
                check(i["labels"]["ink_px"] > 0,
                      "(14) labels VERIFIED present in the delivered raster")
                check(i["warnings"] and i["warnings"][0]["source"] == "caller",
                      "(15) negative-space warnings ride the intent")

            published.clear()
            r = send_hookup_for_human_review(scene, publish=fake_publish,
                                             rasterize=svgdom_rasterize)
            check(r["status"] == "error" and r.get("refused_unlabelled") is True,
                  "(16) ⛔ the REAL label-dropping rasterizer is REFUSED")
            check(not published, "(17) an unlabelled diagram publishes NOTHING")

            # -- ⛔ THE CROSSING GATE, BOTH DIRECTIONS ----------------------
            # NEGATIVE DIRECTION FIRST: this same scene skips ZERO crossings
            # (measured: 29 hops, 0 skipped) and check (9) above proved it still
            # SENDS. A gate that refuses everything is not a gate.
            published.clear()
            r = send_hookup_for_human_review(scene, publish=fake_publish)
            check(r["status"] == "sent"
                  and r["crossings"]["skipped"] == 0
                  and r["crossings"]["hopped"] > 0
                  and r["hops"]["ink_px"] > 0,
                  "(18) a zero-skip netlist STILL DELIVERS, bridges verified "
                  "present in the raster")

            # POSITIVE DIRECTION: a scene with REAL skipped crossings. Not a
            # synthetic fixture — the guide David has open on glass today.
            buildhost = fixture("python/composeio/render/examples/"
                              "tm1637_quad_panel.compose.yaml")
            if resolve_scene(buildhost) is None or load_hookup(
                    resolve_scene(buildhost))[1]:
                # HARNESS, not behaviour — and a MISSING POSITIVE CONTROL is a
                # failure, never a silent skip. An instrument proven only on the
                # passing case is not an instrument.
                print("HARNESS: the positive control for the crossing gate did "
                      f"not load: {buildhost}. The gate was NOT exercised "
                      "against a real failure.")
                return 1
            published.clear()
            r = send_hookup_for_human_review(buildhost, publish=fake_publish)
            check(r["status"] == "error"
                  and r.get("refused_unhopped_crossing") is True,
                  "(19) ⛔ a diagram with un-bridged crossings is REFUSED")
            check(not published,
                  "(20) an un-bridged crossing publishes NOTHING")
            rows = (r.get("crossings") or {}).get("rows") or []
            check(len(rows) == 7 and all(
                      {"wire_name", "crosses_name", "x", "y", "reason"} <= set(k)
                      for k in [set(row) for row in rows]),
                  "(21) every skipped crossing is NAMED with coordinates + reason")
            check(all(str(row["x"]) in r["message"] for row in rows)
                  and "HOW TO FIX" in r["message"],
                  "(22) the refusal is a WORK ORDER, not a count")

            # -- ⛔ THE HOP-SURVIVAL GATE ------------------------------------
            from composeio.maker.hookup_svg import hopless_rasterize
            published.clear()
            r = send_hookup_for_human_review(scene, publish=fake_publish,
                                             rasterize=hopless_rasterize)
            check(r["status"] == "error"
                  and r.get("refused_unhopped_crossing") is True
                  and "line-hop" in r["message"],
                  "(23) ⛔ a rasterizer that drops the ARCS is REFUSED — the "
                  "loss the label gate cannot see")
            check(r.get("labels", {}).get("ok") is True,
                  "(24) ...and the LABEL gate passed it, which is exactly why "
                  "the hop gate had to exist")
            check(not published, "(25) a bridge-free raster publishes NOTHING")
            check(not os.path.exists(r.get("png", "") or "/nonexistent"),
                  "(26) the bridge-free PNG is DELETED, not left on disk")

            # -- ⛔ A CONDUCTOR UNDER A PART BODY (David 2026-08-16) ---------
            # NEGATIVE DIRECTION: the green path must carry `body_wire: 0` as a
            # PROVED fact.  An absent key is indistinguishable from one nobody
            # thought to write, and this is the class that survived two
            # machine-green deliveries.
            published.clear()
            r = send_hookup_for_human_review(scene, publish=fake_publish)
            check(r["status"] == "sent"
                  and published
                  and published[0]["legibility"]["body_wire"] == 0
                  and r["legibility"]["body_wire"] == [],
                  "(27) the intent carries body_wire as a PROVED zero")

            # POSITIVE DIRECTION: the refusal must be a WORK ORDER.  Fed the
            # real measurement from the drawing David rejected — the ground
            # trunk that ran the full 134.2 px width of `fp` at y=858.0.
            hurt = {"labels": 67, "label_label": [], "label_body": [],
                    "body_body": [], "crowded": [], "label_wire": [],
                    "body_wire": [{"part": "fp",
                                   "wire": "mcu.GND -> tm2.GND",
                                   "role": "ground", "at": [457.5, 858.0],
                                   "run": 139.4, "part_width": 134.2}],
                    "ok": False}
            text = legibility_refusal(hurt, "r503_tm1637_panel.compose.yaml")
            check("REFUSED" in text
                  and "mcu.GND -> tm2.GND" in text and "'fp'" in text
                  and "139.4" in text and "457.5" in text
                  and "cannot" in text,
                  "(28) ⛔ a conductor drawn ACROSS a part is refused BY NAME, "
                  "with the run length and the coordinate")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: hookup_review selftest (28/28)")
    return 0


__all__ = [
    "send_hookup_for_human_review", "resolve_scene", "load_hookup",
    "label_survival", "strip_text", "count_text_elements", "canvas_rasterize",
    "read_negative_space", "INTENT_NS", "DEFAULT_MQTT", "ARTIFACT_DIR",
    # ⛔ the crossing gates: the planner's verdict, and the bridges' pixels
    "render_guide", "crossing_refusal", "hop_survival",
]


if __name__ == "__main__":
    raise SystemExit(_selftest())
