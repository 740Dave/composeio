"""[SPEC:CXC-PRIM] [CXC-PRIM-STALE] — the STALENESS PRESENTATION, on pixels.

Closes [CXC-FRESH-NO-PRESENTATION]: the freshness receiver correctly decided a
value was no longer believable and the glass kept rendering the old number.

⛔ EVERY ASSERTION HERE THAT MATTERS IS ON RENDERED PIXELS.  A change whose
entire purpose is to make a failure VISIBLE cannot be certified by reading back
an attribute the same code just wrote — that is the same mental model checking
itself ([PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]).  The attribute
assertions in here exist only to name WHY a pixel assertion failed.

BOTH DIRECTIONS ARE CONTROLLED.  A treatment that never fires is a claim; a
treatment that fires on a healthy channel is the same defect inverted and marks
a stationary throttle dead.  So:

  positive  a declared-cadence namespace that stops    -> treated  (test_c1..)
  negative  a QUIET-BY-CONSTRUCTION namespace silent
            for 100x any plausible timeout             -> UNtreated (C7)
  negative  an UNDECLARED namespace silent forever     -> UNtreated (C8)
  negative  a live publisher restating a steady value  -> UNtreated (C9)

Captures land in tests/render/captures/staleness/ for David's eyes — this is a
visual-language change and it cannot be self-certified.
"""

from __future__ import annotations

import inspect
import os
import pkgutil
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.freshness import UNKNOWN, Verdict
from composeio.render import freshness_view as fv
from composeio.render.engine.layout import draw_child
from composeio.render.engine.runtime import RenderApp
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect, Widget

from .conftest import surface_to_array

CAPTURES = Path(HERE) / "captures" / "staleness"


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #

class _Clock:
    """A hand-driven monotonic clock.  A freshness test built on real sleeps
    cannot honestly assert the NEGATIVE control ([CXC-FRESH-MONOTONIC])."""

    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _widget(app: RenderApp, wid: str) -> Widget:
    return next(w for w in app.comp.root.iter_widgets() if w.widget_id == wid)


def _pin_clock(app: RenderApp, *namespaces: str) -> _Clock:
    clk = _Clock()
    for name in namespaces:
        ns = app.bus.get_namespace(name)
        assert ns is not None, f"namespace {name!r} was never registered"
        ns._clock = clk
    return clk


def _frame(app: RenderApp) -> np.ndarray:
    app.render_frame()
    return surface_to_array(app.canvas)


def _capture(app: RenderApp, name: str) -> None:
    CAPTURES.mkdir(parents=True, exist_ok=True)
    app.canvas.save_png(str(CAPTURES / f"{name}.png"))


def _count_color(img: np.ndarray, rgb, tol: int = 12) -> int:
    """Pixels within *tol* of an exact RGB.  The badge is drawn at full
    opacity so its fill survives as a near-exact colour; the hatch is at
    alpha 90 so it does NOT — which is why this counts the badge."""
    d = np.abs(img[:, :, :3].astype(np.int16) - np.array(rgb, dtype=np.int16))
    return int((d.max(axis=2) <= tol).sum())


def _diff_px(a: np.ndarray, b: np.ndarray) -> int:
    return int((np.abs(a.astype(np.int16) - b.astype(np.int16)).max(axis=2)
                > 2).sum())


#: One gauge, one namespace, one declared cadence.  Deliberately the EXACT
#: widget the defect was measured on ([CXC-FRESH-NO-PRESENTATION]).
DRADIS_SCENE = {
    "design": {"width": 260, "height": 140, "background": "#1a1a1a"},
    "namespaces": [
        {"name": "dradis/contacts", "state_engine": "json",
         "refresh_interval": 1.0},
    ],
    "widgets": [
        {"type": "gauge", "id": "g", "min": 0, "max": 10, "label": "CONTACTS",
         "x": 20, "y": 20, "width": 220, "height": 100,
         "value": {"namespace": "dradis/contacts", "default": 0}},
    ],
}


# --------------------------------------------------------------------------- #
# C1 — THE DEFECT ITSELF, on pixels
# --------------------------------------------------------------------------- #

def test_c1_the_dradis_gauge_stops_lying():
    """publisher alive -> 3.  publisher DEAD -> still 3, but UNMISSABLY marked.

    The measured defect: gauge.value stayed 3.0 and NOT ONE PIXEL CHANGED.
    """
    app = _app(DRADIS_SCENE)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        g = _widget(app, "g")

        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        _capture(app, "01_fresh_publisher_alive")
        assert g.value == 3.0
        assert g.freshness.verdict is Verdict.FRESH

        # The publisher dies.  Nothing is published — that IS the event.
        clk.advance(12.0)
        stale = _frame(app)
        _capture(app, "02_stale_publisher_dead")

        # 1. the pixels changed.  This is the whole point.
        assert _diff_px(fresh, stale) > 500, (
            "the glass is IDENTICAL after the publisher died — this is the "
            "original defect, unfixed")
        # 2. the amber STALE badge is on the glass.
        assert _count_color(stale, fv.TREATMENTS[Verdict.STALE].color) > 200
        assert _count_color(fresh, fv.TREATMENTS[Verdict.STALE].color) < 20
        # 3. and the LAST VALUE IS STILL THERE — we dim, we never blank.
        assert g.value == 3.0
    finally:
        app.stop()


def test_c1b_the_stale_frame_is_not_a_blank_frame():
    """⛔ THE TRAP: a blanked bar gauge reads as ZERO.

    Blanking is the intuitive fix and it is worse than the disease, because a
    viewer's default reading of an empty instrument is a MEASUREMENT.  So the
    stale frame must NOT resemble the never-received frame, and must NOT
    resemble a real zero.
    """
    app = _app(DRADIS_SCENE)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        g = _widget(app, "g")
        app.publish("dradis/contacts", 8)
        _frame(app)
        clk.advance(12.0)
        stale = _frame(app)

        # A REAL zero, fresh.
        clk.advance(0.0)
        app.publish("dradis/contacts", 0)
        zero = _frame(app)
        _capture(app, "03_control_real_zero_fresh")

        assert _diff_px(stale, zero) > 500, (
            "a stale 8 renders the same as a fresh 0 — the exact misreading "
            "blanking would have introduced")
        assert g.value == 0.0
    finally:
        app.stop()


# --------------------------------------------------------------------------- #
# C2 — three states, three appearances
# --------------------------------------------------------------------------- #

def _state_frame(app, clk, kind: str) -> np.ndarray:
    ns = app.bus.get_namespace("dradis/contacts")
    if kind == "stale":
        clk.advance(12.0)
    elif kind == "node_silent":
        # quiet-by-construction routes to the NODE, and only the node's death
        # can condemn it ([CXC-FRESH-QUIET]).
        ns.refresh_interval = None
        ns.quiet_by_construction = True
        ns.attach_node_liveness(lambda: False)
    elif kind == "wire_unknown":
        app.publish("dradis/contacts", UNKNOWN)
    elif kind == "never":
        ns._last_receipt = None
    return _frame(app)


def test_c2_stale_node_silent_and_wire_unknown_look_DIFFERENT():
    """David leans yes and so do I: the remedies differ, so the glass must.

    STALE      -> go look at that one publisher process.
    NODE DOWN  -> go look at the BOARD.  Cross-hatched as well as red, so the
                  distinction survives a colour-blind operator.
    NO DATA    -> nothing is broken; the publisher is alive and honestly does
                  not know.  Deliberately the gentlest treatment, because an
                  alarm here trains the operator to ignore alarms.
    """
    frames = {}
    for kind in ("stale", "node_silent", "wire_unknown", "never"):
        app = _app(DRADIS_SCENE)
        try:
            clk = _pin_clock(app, "dradis/contacts")
            app.publish("dradis/contacts", 3)
            _frame(app)
            frames[kind] = _state_frame(app, clk, kind)
            _capture(app, {"stale": "04_state_stale",
                           "node_silent": "05_state_node_silent",
                           "wire_unknown": "06_state_wire_unknown",
                           "never": "07_state_never_received"}[kind])
        finally:
            app.stop()

    kinds = list(frames)
    for i, a in enumerate(kinds):
        for b in kinds[i + 1:]:
            assert _diff_px(frames[a], frames[b]) > 100, (
                f"{a} and {b} render the same — the operator is told a "
                f"different remedy is needed and shown the same picture")

    assert _count_color(frames["stale"],
                        fv.TREATMENTS[Verdict.STALE].color) > 200
    assert _count_color(frames["node_silent"],
                        fv.TREATMENTS[Verdict.NODE_SILENT].color) > 200
    assert _count_color(frames["wire_unknown"],
                        fv.TREATMENTS[Verdict.WIRE_UNKNOWN].color) > 200


# --------------------------------------------------------------------------- #
# C3 — opt-OUT, not opt-in
# --------------------------------------------------------------------------- #

def test_c3_a_brand_new_widget_is_covered_with_zero_edits():
    """The whole shape of the fix: a widget written today, which knows nothing
    about freshness, is treated because the DEFAULT is treated."""

    class _NoviceWidget(Widget):
        """Exactly the widget an author writes without thinking about staleness
        — and exactly the swallowing handler that produced the defect."""

        def __init__(self, wid, **kw):
            super().__init__(wid, "novice", **kw)
            self.value = 0.0

        def measure(self, c):
            from composeio.render.canvas import Size
            return Size(100, 40)

        def draw(self, canvas, rect):
            canvas.set_fill_color(0x3B, 0x8E, 0xD0)
            canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        def on_bus_state(self, namespace, value):
            try:
                self.value = float(value)
            except (TypeError, ValueError):
                pass                        # <- THE 22-WIDGET DEFECT

    w = _NoviceWidget("n")
    assert w.staleness_treatment == "auto", (
        "the default must be treated; opt-in is what cost 22 widgets")
    w.arranged_rect = Rect(10, 10, 100, 40)

    before = _draw_one(w)
    w.note_freshness(Verdict.STALE, 12.0, "x/y")
    after = _draw_one(w)
    assert _diff_px(before, after) > 200


def test_c3b_opting_out_is_deliberate_and_works():
    class _Chrome(Widget):
        def __init__(self, wid):
            super().__init__(wid, "chrome")
            self.staleness_treatment = "none"

        def draw(self, canvas, rect):
            canvas.set_fill_color(0x3B, 0x8E, 0xD0)
            canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

    w = _Chrome("c")
    w.arranged_rect = Rect(10, 10, 100, 40)
    before = _draw_one(w)
    w.note_freshness(Verdict.STALE, 12.0, "x/y")
    assert _diff_px(before, _draw_one(w)) == 0


def test_c3c_staleness_none_is_declarable_in_compose_yaml():
    cfg = dict(DRADIS_SCENE)
    cfg = {**cfg, "widgets": [{**cfg["widgets"][0], "staleness": "none"}]}
    app = _app(cfg)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        clk.advance(12.0)
        after = _frame(app)
        # The VERDICT is still recorded (the widget knows), it is only the
        # DRAWING that is declined — an opt-out must not blind the widget.
        assert _widget(app, "g").freshness.verdict is Verdict.STALE
        assert _diff_px(fresh, after) == 0, (
            "an opted-out widget was treated anyway")
    finally:
        app.stop()


def _draw_one(w: Widget) -> np.ndarray:
    canvas = SkiaCanvas()
    canvas.create_surface(140, 70)
    canvas.set_fill_color(0x1A, 0x1A, 0x1A)
    canvas.fill_rect(0, 0, 140, 70)
    draw_child(canvas, w, w.arranged_rect)
    canvas.present()
    return surface_to_array(canvas)


# --------------------------------------------------------------------------- #
# C4 — the coverage claim, measured over the REAL widget population
# --------------------------------------------------------------------------- #

def _bus_widget_classes():
    """Every widget class in the render tree that implements on_bus_state —
    the exact population [CXC-FRESH-NO-PRESENTATION] measured at 52."""
    import composeio.render.widgets as pkg

    seen = {}
    for mod in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        try:
            m = __import__(mod.name, fromlist=["*"])
        except Exception:
            continue
        for _n, obj in vars(m).items():
            if (inspect.isclass(obj) and issubclass(obj, Widget)
                    and "on_bus_state" in obj.__dict__
                    and obj.__module__ == mod.name):
                seen[f"{obj.__module__}.{obj.__name__}"] = obj
    return seen


def test_c4_every_bus_bound_widget_class_defaults_to_TREATED():
    """⛔ A test that only covers GaugeWidget certifies one of fifty-two.

    This walks the ACTUAL class population and asserts the default, then
    reports exactly which classes could not be instantiated bare (they are
    still covered — `staleness_treatment` is set in Widget.__init__, which
    every one of them calls — but this test cannot prove it by construction,
    and saying so is the point).
    """
    classes = _bus_widget_classes()
    assert len(classes) >= 45, f"only found {len(classes)} bus-bound widgets"

    built, unbuildable, opted_out = [], [], []
    for name, cls in sorted(classes.items()):
        try:
            w = cls(f"w_{cls.__name__}")
        except Exception as exc:                # noqa: BLE001
            unbuildable.append((name, type(exc).__name__))
            continue
        built.append(name)
        if w.staleness_treatment != "auto":
            opted_out.append((name, w.staleness_treatment))

    # The ONE deliberate decliner, and it must be deliberate.
    assert [n for n, _ in opted_out] == [
        "composeio.render.widgets.link_health.LinkHealthWidget"], (
        f"unexpected opt-outs: {opted_out}")
    assert opted_out[0][1] == "self"

    print(f"\n[CXC-PRIM-STALE] coverage: {len(classes)} widget classes "
          f"implement on_bus_state; {len(built)} instantiate bare and were "
          f"asserted directly; {len(unbuildable)} need constructor args and "
          f"are covered via Widget.__init__ but NOT asserted here: "
          f"{unbuildable}")
    # >70% asserted by construction is the honest bar; the rest is named above.
    assert len(built) >= int(0.7 * len(classes))


def test_c4b_every_buildable_widget_class_actually_CHANGES_PIXELS():
    """The default being "auto" is an attribute.  This is the glass.

    Each buildable class is drawn fresh and then stale through the REAL
    draw_child path, and the frames must differ.  A class whose draw() paints
    nothing at all cannot be diffed and is reported, not silently passed.
    """
    classes = _bus_widget_classes()
    changed, blank, errored = [], [], []
    for name, cls in sorted(classes.items()):
        try:
            w = cls(f"w_{cls.__name__}")
        except Exception:                        # noqa: BLE001
            continue
        if w.staleness_treatment != "auto":
            continue
        w.arranged_rect = Rect(6, 6, 128, 58)
        try:
            before = _draw_one(w)
            w.note_freshness(Verdict.STALE, 12.0, "x/y")
            after = _draw_one(w)
        except Exception as exc:                 # noqa: BLE001
            errored.append((name, f"{type(exc).__name__}: {exc}"))
            continue
        (changed if _diff_px(before, after) > 100 else blank).append(name)

    print(f"\n[CXC-PRIM-STALE] pixel coverage: {len(changed)} classes visibly "
          f"treated; undiffable={blank}; draw-errors={errored}")
    assert not blank, f"treated but produced no pixel change: {blank}"
    assert len(changed) >= 30
    assert not errored, f"draw() raised under treatment: {errored}"


# --------------------------------------------------------------------------- #
# C5/C6 — the swallow seam
# --------------------------------------------------------------------------- #

def test_c5_unknown_is_withheld_from_a_swallowing_handler():
    """The mechanism, at the seam.  A wire-unknown arrives; the handler that
    would have swallowed it is never called; the value is retained ON PURPOSE
    and the widget is marked."""
    app = _app(DRADIS_SCENE)
    try:
        _pin_clock(app, "dradis/contacts")
        g = _widget(app, "g")
        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        assert g.value == 3.0

        app.publish("dradis/contacts", UNKNOWN)
        marked = _frame(app)
        assert g.value == 3.0, "retention is the DESIGN — 'it was 3' matters"
        assert g.freshness.verdict is Verdict.WIRE_UNKNOWN
        assert _diff_px(fresh, marked) > 200, (
            "the value was retained and NOTHING said so — the defect")
    finally:
        app.stop()


def test_c6_a_widget_can_opt_IN_to_receiving_unknown():
    seen = []

    class _Native(Widget):
        def __init__(self, wid):
            super().__init__(wid, "native")
            self.wants_unknown = True

        def on_bus_state(self, ns, value):
            seen.append(value)

    from composeio.render.engine.runtime import _deliver_bus_state
    w = _Native("n")
    _deliver_bus_state(w, "x/y", UNKNOWN)
    assert seen == [UNKNOWN]

    seen.clear()
    w.wants_unknown = False
    _deliver_bus_state(w, "x/y", UNKNOWN)
    assert seen == []
    _deliver_bus_state(w, "x/y", 5)
    assert seen == [5]


# --------------------------------------------------------------------------- #
# C7/C8/C9 — ⛔ THE NEGATIVE CONTROLS.  The inverse defect is just as bad.
# --------------------------------------------------------------------------- #

QUIET_SCENE = {
    "design": {"width": 260, "height": 140, "background": "#1a1a1a"},
    "namespaces": [
        {"name": "throttle/percent", "state_engine": "json",
         "quiet_by_construction": True},
    ],
    "widgets": [
        {"type": "gauge", "id": "g", "min": 0, "max": 100, "label": "THROTTLE",
         "x": 20, "y": 20, "width": 220, "height": 100,
         "value": {"namespace": "throttle/percent", "default": 0}},
    ],
}


def test_c7_a_QUIET_BY_DESIGN_namespace_is_never_marked_by_its_own_silence():
    """⛔ THE INVERSE DEFECT.  `controller_main.cpp` gates throttle emission on
    a deadband — "quiet-bus by construction" — so a stationary lever publishes
    NOTHING, indefinitely, while being perfectly healthy.  A treatment that
    fires here marks the fleet's most-watched namespace DEAD seconds after
    David stops touching the lever, and it passes every "did it go stale?" test
    a naive suite would write.
    """
    app = _app(QUIET_SCENE)
    try:
        clk = _pin_clock(app, "throttle/percent")
        app.publish("throttle/percent", 42)
        fresh = _frame(app)
        _capture(app, "08_control_quiet_by_design_fresh")

        clk.advance(100_000.0)              # a day and a bit of silence
        quiet = _frame(app)
        _capture(app, "09_control_quiet_by_design_after_silence")

        assert _widget(app, "g").freshness.verdict is Verdict.FRESH
        assert _diff_px(fresh, quiet) == 0, (
            "a healthy stationary throttle was marked dead — the inverse "
            "defect, and exactly as useless as marking nothing")
    finally:
        app.stop()


def test_c8_an_UNDECLARED_namespace_is_never_marked():
    """UNDECLARED means the question was never asked ([CXC-FRESH-VERDICT]).
    Treating it would hatch every widget in every existing scene."""
    cfg = {**DRADIS_SCENE, "namespaces": [
        {"name": "dradis/contacts", "state_engine": "json"}]}
    app = _app(cfg)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        clk.advance(100_000.0)
        assert _widget(app, "g").freshness.verdict is Verdict.UNDECLARED
        assert _diff_px(fresh, _frame(app)) == 0
    finally:
        app.stop()


def test_c9_a_publisher_restating_a_STEADY_value_is_never_marked():
    """[CXC-FRESH-RECEIPT] at the presentation layer: the age resets on
    RECEIPT, not on CHANGE, so the steadiest signals in the fleet — the ones
    an operator most expects to trust — must not be the ones that get hatched.
    """
    app = _app(DRADIS_SCENE)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        for _ in range(20):
            clk.advance(0.9)
            app.publish("dradis/contacts", 3)   # SAME value, no callback
            frame = _frame(app)
        assert _widget(app, "g").freshness.verdict is Verdict.FRESH
        assert _diff_px(fresh, frame) == 0
    finally:
        app.stop()


def test_c9b_still_fresh_at_2999ms_and_stale_at_3001ms():
    """The boundary, asserted honestly on an injected clock — the claim a
    wall-clock sleep cannot make ([CXC-FRESH-MONOTONIC])."""
    app = _app(DRADIS_SCENE)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        clk.advance(2.999)                  # timeout = 3 * 1.0
        assert _diff_px(fresh, _frame(app)) == 0
        clk.advance(0.002)
        assert _diff_px(fresh, _frame(app)) > 200
    finally:
        app.stop()


# --------------------------------------------------------------------------- #
# C10 — self-healing, and the small-widget degrade
# --------------------------------------------------------------------------- #

def test_c10_the_treatment_clears_when_the_publisher_RETURNS():
    """Nothing latches ([CXC-FRESH-LAZY]).  A treatment that needed a reset
    step is a treatment somebody forgets to reset."""
    app = _app(DRADIS_SCENE)
    try:
        clk = _pin_clock(app, "dradis/contacts")
        app.publish("dradis/contacts", 3)
        fresh = _frame(app)
        clk.advance(12.0)
        assert _diff_px(fresh, _frame(app)) > 200
        app.publish("dradis/contacts", 3)   # the publisher comes back
        assert _diff_px(fresh, _frame(app)) == 0
    finally:
        app.stop()


def test_c11_a_tiny_widget_still_says_it_is_dead():
    """A 10x10 live-dot cannot carry a 60-px pill, and "too small for the
    badge" must never become "silently untreated" — that is the whole defect
    class reappearing at small sizes."""
    class _Dot(Widget):
        def draw(self, canvas, rect):
            canvas.set_fill_color(0x2E, 0xCC, 0x71)
            canvas.fill_circle(rect.x + rect.w / 2, rect.y + rect.h / 2,
                               rect.w / 2)

    w = _Dot("d", "dot")
    w.arranged_rect = Rect(10, 10, 10, 10)
    before = _draw_one(w)
    w.note_freshness(Verdict.STALE, 12.0, "x/y")
    after = _draw_one(w)
    assert _diff_px(before, after) > 10
    assert _count_color(after, fv.TREATMENTS[Verdict.STALE].color) > 5


def test_c12_a_stale_container_does_not_double_hatch_its_children():
    """Two stacked textures say "two things died" when one did."""
    from composeio.render.engine.layout import FlexContainer

    class _Box(Widget):
        def measure(self, c):
            from composeio.render.canvas import Size
            return Size(40, 20)

        def draw(self, canvas, rect):
            canvas.set_fill_color(0x3B, 0x8E, 0xD0)
            canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

    parent = FlexContainer("p")
    child = _Box("c", "box")
    parent.add(child)
    parent.arranged_rect = Rect(6, 6, 128, 58)
    child.arranged_rect = Rect(10, 10, 40, 20)

    child.note_freshness(Verdict.STALE, 12.0, "x/y")
    child_only = _draw_one(parent)
    parent.note_freshness(Verdict.STALE, 12.0, "x/y")
    both = _draw_one(parent)
    parent_only_child_fresh = None
    child.note_freshness(Verdict.FRESH, 0.0, "x/y")
    parent_only_child_fresh = _draw_one(parent)

    assert _diff_px(both, parent_only_child_fresh) == 0, (
        "the child was hatched inside an already-hatched parent")
    assert _diff_px(child_only, both) > 100


# --------------------------------------------------------------------------- #
# C13 — the vocabulary itself
# --------------------------------------------------------------------------- #

def test_c13_fresh_and_undeclared_carry_no_treatment_by_construction():
    assert fv.treatment_for(Verdict.FRESH) is None
    assert fv.treatment_for(Verdict.UNDECLARED) is None
    assert fv.treatment_for(None) is None
    for v in (Verdict.STALE, Verdict.NODE_SILENT, Verdict.NEVER_RECEIVED,
              Verdict.WIRE_UNKNOWN):
        assert fv.treatment_for(v) is not None
        assert 0.0 < fv.TREATMENTS[v].dim < 1.0, "we dim; we never blank"


def test_c13b_the_worst_verdict_wins_across_a_widgets_inputs():
    """[CXC-FRESH-CLASS]'s max rule: the freshest input must not LAUNDER the
    staleness of the others."""
    assert fv.worst(Verdict.FRESH, Verdict.STALE) is Verdict.STALE
    assert fv.worst(Verdict.STALE, Verdict.NODE_SILENT) is Verdict.NODE_SILENT
    assert fv.worst(Verdict.WIRE_UNKNOWN,
                    Verdict.NEVER_RECEIVED) is Verdict.NEVER_RECEIVED
    assert fv.worst(None, Verdict.STALE) is Verdict.STALE


def test_c13c_a_verdict_only_reports_an_age_it_actually_HAS():
    """⛔ FOUND BY LOOKING AT THE CAPTURE, not by a test.

    NODE_SILENT rendered "NODE DOWN 0s" — the NAMESPACE's age, which is
    precisely the number [CXC-FRESH-QUIET] says proves nothing about a
    quiet-by-construction namespace.  The relevant age is the NODE's, and the
    widget does not hold it.  An irrelevant number beside a condemnation is
    the whole defect in miniature.
    """
    for v in (Verdict.NEVER_RECEIVED, Verdict.NODE_SILENT,
              Verdict.WIRE_UNKNOWN):
        t = fv.TREATMENTS[v]
        assert not t.show_age
        assert fv.badge_caption(t, 12.0) == t.label, (
            f"{v.name} printed an age it does not have")
    t = fv.TREATMENTS[Verdict.NEVER_RECEIVED]
    assert fv.badge_caption(t, None) == "NO SIGNAL"
    assert fv.badge_caption(fv.TREATMENTS[Verdict.STALE], 12.4) == "STALE 12s"
    assert fv.badge_caption(fv.TREATMENTS[Verdict.STALE], 130.0) == "STALE 2m"


# --------------------------------------------------------------------------- #
# C15 — the artifact for DAVID'S EYES: one dead card among live neighbours
# --------------------------------------------------------------------------- #

DASHBOARD = {
    "design": {"width": 660, "height": 200, "background": "#1a1a1a"},
    "namespaces": [
        {"name": "dradis/contacts", "state_engine": "json",
         "refresh_interval": 1.0},
        {"name": "engine/temp", "state_engine": "json",
         "refresh_interval": 1.0},
        {"name": "throttle/percent", "state_engine": "json",
         "quiet_by_construction": True},
    ],
    "widgets": [
        {"type": "pixel_stack", "id": "root", "width": 660, "height": 200,
         "children": [
             {"type": "gauge", "id": "a", "min": 0, "max": 10,
              "label": "CONTACTS", "x": 10, "y": 20,
              "width": 200, "height": 160,
              "value": {"namespace": "dradis/contacts", "default": 0}},
             {"type": "gauge", "id": "b", "min": 0, "max": 120,
              "label": "TEMP", "x": 228, "y": 20,
              "width": 200, "height": 160,
              "value": {"namespace": "engine/temp", "default": 0}},
             {"type": "gauge", "id": "c", "min": 0, "max": 100,
              "label": "THROTTLE", "x": 446, "y": 20,
              "width": 200, "height": 160,
              "value": {"namespace": "throttle/percent", "default": 0}},
         ]},
    ],
}


def test_c15_one_dead_card_among_live_neighbours():
    """⛔ THE ARTIFACT DAVID MUST LOOK AT.  Three cards: one publisher dies,
    one keeps publishing, one is quiet BY DESIGN.  The only correct picture
    marks exactly ONE of them, and no machine assertion can tell him whether
    the marked card reads as dead at a glance from across the room."""
    app = _app(DASHBOARD)
    try:
        clk = _pin_clock(app, "dradis/contacts", "engine/temp",
                         "throttle/percent")
        for ns, v in (("dradis/contacts", 3), ("engine/temp", 87),
                      ("throttle/percent", 42)):
            app.publish(ns, v)
        _frame(app)
        _capture(app, "10_dashboard_all_live")

        # Only the dradis publisher dies.  Temp keeps beating; throttle is
        # quiet by design and must stay untouched.
        for _ in range(13):
            clk.advance(1.0)
            app.publish("engine/temp", 87)
        _frame(app)
        _capture(app, "11_dashboard_one_publisher_dead")

        assert _widget(app, "a").freshness.verdict is Verdict.STALE
        assert _widget(app, "b").freshness.verdict is Verdict.FRESH
        assert _widget(app, "c").freshness.verdict is Verdict.FRESH
    finally:
        app.stop()


def test_c14_a_multi_input_card_names_the_culprit_namespace():
    app = _app({
        "design": {"width": 260, "height": 140, "background": "#1a1a1a"},
        "namespaces": [
            {"name": "nav/lat", "state_engine": "json", "refresh_interval": 1.0},
            {"name": "nav/lon", "state_engine": "json", "refresh_interval": 1.0},
        ],
        "widgets": [
            # ONE widget, TWO bus inputs — the shape [CXC-FRESH-CLASS] is about.
            {"type": "gauge", "id": "c", "min": 0, "max": 10,
             "x": 10, "y": 10, "width": 240, "height": 120,
             "value": {"namespace": "nav/lat", "default": 0},
             "opacity": "nav/lon"},
        ],
    })
    try:
        clk_lat = _Clock()
        clk_lon = _Clock()
        app.bus.get_namespace("nav/lat")._clock = clk_lat
        app.bus.get_namespace("nav/lon")._clock = clk_lon
        app.publish("nav/lat", 1.0)
        app.publish("nav/lon", 2.0)
        _frame(app)
        clk_lat.advance(12.0)               # ONE input dies
        _frame(app)
        c = _widget(app, "c")
        assert c.freshness.verdict is Verdict.STALE
        assert c.freshness.namespace == "nav/lat", (
            "the operator needs to know WHICH feed died")
    finally:
        app.stop()
