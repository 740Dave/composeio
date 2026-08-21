"""
[CXC-R-ENGINE-HOVER] Hover-cursor affordance — compliance tests.

Across the whole suite (console, away-watch, doc/decision viewers) the OS
pointer should become the "pointing hand" whenever it crosses a CLICKABLE
item, and the arrow over empty space.  The engine already hit-tests for
clicks; this closes the gap by resolving the SAME hit target on hover and
lowering the widget's semantic ``hover_cursor()`` to a system cursor.

The seam is the existing ``interactive`` flag — every clickable widget
already sets it (parser: publish/swipe bindings; widgets: in __init__), so
they light up with ZERO per-widget edits.  Because the real OS cursor swap
is not headless-observable, these tests exercise the RESOLVER (a pure read)
and the debounce, which are:

  CXC-HOVER-C1  Widget.hover_cursor() defaults to "pointer" iff interactive
  CXC-HOVER-C2  a widget may OVERRIDE hover_cursor() (opt-out / text / etc.)
  CXC-HOVER-C3  resolve_cursor() -> "pointer" over an interactive widget,
                "default" over empty space — reusing hit_test_tree
  CXC-HOVER-C4  _apply_hover_cursor debounces: SetCursor only on a CHANGE
  CXC-HOVER-C5  hover resolution never marks the scene dirty (damage
                discipline — a cursor swap is not a repaint)

Run:  python3 -m pytest tests/render/test_hover_cursor.py -q
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.widget import Widget


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

class _FakePointer:
    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = float(x), float(y)
        self.pointer_id = 0
        self.dx = self.dy = 0.0


class _CursorCanvas:
    """Minimal fake backend: records set_cursor() calls (the duck-typed
    affordance the runtime looks up on the canvas)."""

    def __init__(self):
        self.calls = []

    def set_cursor(self, name):
        self.calls.append(name)


def _hover_app():
    """A 400x300 surface with an interactive rect (publish binding -> the
    clickable) at the left and a plain, non-interactive rect at the right,
    both absolute-positioned in a pixel_stack so hit-testing is exact."""
    config = {
        "design": {"width": 400, "height": 300},
        "namespaces": [{"name": "demo/tap", "state_engine": "json",
                        "initial": 0}],
        "widgets": [{
            "type": "pixel_stack", "id": "root_stack",
            "width": 400, "height": 300,
            "children": [
                {"type": "rect", "id": "clickable",
                 "x": 0, "y": 0, "w": 200, "h": 300,
                 "fill_color": "#334455",
                 "publish_namespace": "demo/tap"},   # -> interactive = True
                {"type": "rect", "id": "inert",
                 "x": 200, "y": 0, "w": 200, "h": 300,
                 "fill_color": "#222222"},            # not interactive
            ],
        }],
    }
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    app.render_frame()          # arrange so rects have arranged_rects
    return app


# ---------------------------------------------------------------------------
# CXC-HOVER-C1 / C2 — the widget seam (inferred from `interactive`)
# ---------------------------------------------------------------------------

def test_hover_cursor_defaults_from_interactive():
    """CXC-HOVER-C1: the base Widget shows the hand ONLY when interactive —
    the same flag hit_test_tree resolves a click target on, so existing
    clickable widgets need no edit."""
    w = Widget("w", "rect")
    assert w.hover_cursor() == "default"
    w.interactive = True
    assert w.hover_cursor() == "pointer"


def test_hover_cursor_is_overridable():
    """CXC-HOVER-C2: a widget that is interactive only to catch scroll/pan
    (a scrollable pane) can opt out of the hand without touching the engine."""
    class _ScrollPane(Widget):
        def hover_cursor(self):
            return "default"

    p = _ScrollPane("p", "scroll")
    p.interactive = True
    assert p.hover_cursor() == "default"     # override wins over the default


def test_real_interactive_widget_reports_pointer():
    """CXC-HOVER-C1: a real clickable widget (button) reports the hand with
    NO per-widget edit — it only ever set `interactive`."""
    from composeio.render.widgets.button import ButtonWidget
    b = ButtonWidget("b", label="Go")
    assert b.interactive is True
    assert b.hover_cursor() == "pointer"


# ---------------------------------------------------------------------------
# CXC-HOVER-C6 / C7 — cursor keyed on the KIND of interaction
#   text_selectable (edit/select text) -> "text" (I-beam)
#   interactive (tap-to-act)           -> "pointer" (hand)
#   static                             -> "default" (arrow)
# ---------------------------------------------------------------------------

def test_text_selectable_trait_wins_over_interactive():
    """CXC-HOVER-C6: the text-content trait resolves to the I-beam and WINS
    over `interactive` — a text field is both, and must read as an I-beam."""
    w = Widget("w", "rect")
    assert w.hover_cursor() == "default"          # static
    w.interactive = True
    assert w.hover_cursor() == "pointer"          # click-activation
    w.text_selectable = True
    assert w.hover_cursor() == "text"             # selectable text wins
    w.interactive = False
    assert w.hover_cursor() == "text"             # text even without click


def test_prompt_input_reports_text():
    """CXC-HOVER-C6: the prompt/message text input -> I-beam (it has a real
    cursor + drag-select via MultiLineEditMixin)."""
    from composeio.render.widgets.text_input import TextInputWidget
    ti = TextInputWidget("prompt")
    assert ti.text_selectable is True
    assert ti.hover_cursor() == "text"


def test_chat_message_thread_reports_text():
    """CXC-HOVER-C6: the chat/message thread (the console's messages to the
    human) is selectable text -> I-beam.  Real drag-select + Cmd+C today."""
    from composeio.render.widgets.chat_thread import ChatThreadWidget
    ct = ChatThreadWidget("chat")
    assert ct.text_selectable is True
    assert ct.hover_cursor() == "text"


def test_markdown_doc_body_reports_text():
    """CXC-HOVER-C6: the markdown/doc BODY viewer -> I-beam.  (Selection gesture
    is a documented follow-on; the cursor is the affordance promise.)"""
    from composeio.render.widgets.markdown_viewer import MarkdownViewerWidget
    mv = MarkdownViewerWidget("doc")
    assert mv.text_selectable is True
    assert mv.hover_cursor() == "text"


def test_artifacts_registry_row_reports_pointer_not_text():
    """CXC-HOVER-C7: an artifacts-registry file-name row is a CLICK target
    (tap opens the doc) -> the hand, NOT the I-beam.  It carries no
    text-selection (the file name is not drag-selectable content)."""
    from composeio.render.widgets.nightwatch_artifacts import (
        NightWatchArtifactsWidget)
    a = NightWatchArtifactsWidget("artifacts")
    assert a.interactive is True
    assert a.text_selectable is False        # click target, not selectable text
    assert a.hover_cursor() == "pointer"


def test_decision_row_reports_pointer():
    """CXC-HOVER-C7: a decision row/card (tap to explore) -> the hand."""
    from composeio.render.widgets.decisions_summary import (
        DecisionsSummaryWidget)
    d = DecisionsSummaryWidget("decisions")
    assert d.hover_cursor() == "pointer"


def test_away_watch_row_reports_pointer():
    """CXC-HOVER-C7: an away-watch roster row (tap to relaunch) -> the hand."""
    from composeio.render.widgets.away_watch_view import AwayWatchViewWidget
    aw = AwayWatchViewWidget("away")
    assert aw.hover_cursor() == "pointer"


def test_static_label_reports_default():
    """CXC-HOVER-C7: a static, non-interactive label -> the arrow."""
    from composeio.render.widgets.label import LabelWidget
    lbl = LabelWidget("title", text="ComposeUX")
    assert lbl.interactive is False
    assert lbl.text_selectable is False
    assert lbl.hover_cursor() == "default"


def test_scroll_and_panzoom_panes_report_default():
    """CXC-HOVER-C7: panes that are interactive ONLY to catch scroll/pan-zoom
    (no click target, no selectable text) opt out of the hand -> the arrow."""
    from composeio.render.widgets.svg import SvgWidget
    from composeio.render.widgets.nightwatch_checklist import (
        NightWatchChecklistWidget)
    svg = SvgWidget("diagram", pan_zoom=True)
    assert svg.interactive is True
    assert svg.hover_cursor() == "default"
    chk = NightWatchChecklistWidget("checklist")
    assert chk.interactive is True
    assert chk.hover_cursor() == "default"


# ---------------------------------------------------------------------------
# CXC-HOVER-C3 — the resolver reuses hit_test_tree
# ---------------------------------------------------------------------------

def test_resolve_cursor_pointer_over_clickable():
    """CXC-HOVER-C3: over the interactive rect -> the hand."""
    app = _hover_app()
    assert app.resolve_cursor(100, 150) == "pointer"


def test_resolve_cursor_default_over_inert_region():
    """CXC-HOVER-C3: over the non-interactive rect -> the arrow.  (The plain
    rect is not a hit target, so hit_test_tree returns None there.)"""
    app = _hover_app()
    assert app.resolve_cursor(300, 150) == "default"


def test_resolve_cursor_default_outside_surface():
    """CXC-HOVER-C3: off the surface entirely -> the arrow."""
    app = _hover_app()
    assert app.resolve_cursor(1000, 1000) == "default"


def test_resolve_cursor_matches_click_hit_target():
    """CXC-HOVER-C3: the hover resolver and the click path agree — same
    hit_test_tree, no forked hit model."""
    app = _hover_app()
    for (x, y) in [(50, 50), (100, 150), (300, 150), (999, 10)]:
        target = app.comp.root.hit_test_tree(x, y)
        expect = "default" if target is None else target.hover_cursor()
        assert app.resolve_cursor(x, y) == expect


# ---------------------------------------------------------------------------
# CXC-HOVER-C4 — the debounce (SetCursor only on change)
# ---------------------------------------------------------------------------

def test_apply_hover_cursor_debounces_to_changes():
    """CXC-HOVER-C4: continuous motion over one widget issues NO repeated
    backend calls; only crossing a clickable boundary flips the cursor."""
    app = _hover_app()
    fake = _CursorCanvas()
    app.canvas = fake
    app.windowed = True          # enable the backend leg for the test

    # start on empty space -> no change from the "default" initial, no call
    app._apply_hover_cursor(300, 150)
    assert fake.calls == []
    assert app._current_cursor == "default"

    # cross onto the clickable -> one hand call
    app._apply_hover_cursor(100, 150)
    assert fake.calls == ["pointer"]

    # keep moving WITHIN the clickable -> debounced, still just one call
    app._apply_hover_cursor(120, 160)
    app._apply_hover_cursor(150, 200)
    assert fake.calls == ["pointer"]

    # cross back to empty -> one arrow call
    app._apply_hover_cursor(300, 150)
    assert fake.calls == ["pointer", "default"]


def test_move_event_drives_cursor_through_dispatch():
    """CXC-HOVER-C4: the wiring is live — a MOUSEMOTION through the real
    dispatch path resolves + applies the cursor (crossing into the clickable
    yields exactly one hand call)."""
    app = _hover_app()
    fake = _CursorCanvas()
    app.canvas = fake
    app.windowed = True

    app._dispatch_pointer(_FakePointer("move", 300, 150))   # empty: no change
    app._dispatch_pointer(_FakePointer("move", 100, 150))   # clickable: hand
    assert fake.calls == ["pointer"]


# ---------------------------------------------------------------------------
# CXC-HOVER-C5 — hover is a pure read (damage discipline)
# ---------------------------------------------------------------------------

def test_hover_resolution_never_marks_dirty():
    """CXC-HOVER-C5: resolving/applying the hover cursor must NOT dirty the
    scene — a cursor swap is not a repaint ([CXC-R-DAMAGE])."""
    app = _hover_app()
    fake = _CursorCanvas()
    app.canvas = fake
    app.windowed = True
    app._dirty = False
    app._apply_hover_cursor(100, 150)     # crosses onto the clickable
    assert app.resolve_cursor(100, 150) == "pointer"
    assert app._dirty is False            # no redraw requested by hover
