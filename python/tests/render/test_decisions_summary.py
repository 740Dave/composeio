"""
Tests for the compact Decisions-summary rail card
([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D4) — the P4 pattern: the compact rail
view + the full tab detail read the SAME ``self/decisions`` bus state.

Acceptance: given a ``self/decisions`` projection (the decisions_publisher
payload), the card derives OPEN/SUPERSEDED/TOTAL + the latest (anchor) call +
freshness; the Explore ▸ jump chip publishes the console tab-switch seam; a bus
update requests a scoped redraw only.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import DecisionsSummaryWidget
from composeio.render.widgets import console_theme as ct

from .conftest import surface_to_array

PAYLOAD = {
    "nodes": [
        {"id": "DEC-95-HANDOFF-LINE", "title": "The handoff line is the contract",
         "lifecycle-position": "active", "is_anchor": True,
         "session": "2026-07-22"},
        {"id": "DEC-90-COMPACT-PREP", "title": "Compact prep",
         "lifecycle-position": "superseded", "superseded": True,
         "session": "2026-07-20"},
        {"id": "DEC-CROSSING-IS-CHEAP", "title": "Crossing is cheap",
         "lifecycle-position": "active", "session": "2026-07-21"},
    ],
    "edges": [], "anchor": "DEC-95-HANDOFF-LINE", "corpus": "intelligence-console",
}


class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []
        self.redraws = 0

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def teardown_function(_fn):
    ct.set_theme("dark")


def _mounted():
    w = DecisionsSummaryWidget("ds")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = Rect(4, 4, 332, 150)
    return w, ctx


# --- payload -> derived state -----------------------------------------------

def test_payload_coercion_dict_and_json_string():
    for value in (PAYLOAD, json.dumps(PAYLOAD)):
        w = DecisionsSummaryWidget("ds")
        w.on_bus_state("self/decisions", value)
        assert w._counts() == (2, 1, 3)            # open, superseded, total
        assert w._latest()["id"] == "DEC-95-HANDOFF-LINE"   # the anchor
        assert w._freshness() == "2026-07-22"      # newest session


def test_latest_falls_back_to_first_node_without_anchor():
    payload = {"nodes": [{"id": "A", "title": "Alpha", "lifecycle": "active"}],
               "edges": []}
    w = DecisionsSummaryWidget("ds")
    w.on_bus_state("self/decisions", payload)
    assert w._latest()["id"] == "A"
    assert w._counts() == (1, 0, 1)


def test_garbage_payload_never_clobbers_state():
    w = DecisionsSummaryWidget("ds")
    w.on_bus_state("self/decisions", PAYLOAD)
    w.on_bus_state("self/decisions", "not-json")
    w.on_bus_state("self/decisions", 42)
    assert w._counts() == (2, 1, 3)                 # kept


def test_bus_update_requests_scoped_redraw_only():
    w, ctx = _mounted()
    w.on_bus_state("self/decisions", PAYLOAD)
    assert ctx.redraws == 1                          # request_redraw(), nothing else


# --- [CXC-R-DAMAGE-ONCHANGE] the redraw is conditional on a REAL change ------
# Regression guard for the console render-loop SPIN: the compact card is an
# always-visible rail projection; a redelivery of an UNCHANGED snapshot (the
# retained-value replay a hot-add re-bind fires, a family fan-out, or the
# decisions_publisher re-emitting the same graph on its interval) must damage
# nothing.  Pre-fix, on_bus_state() called request_redraw() unconditionally, so
# a redelivery stream turned the idempotent card into a continuous-damage source
# (loop never idles) — invisible to a single-frame capture, visible only as CPU.

def test_redelivered_unchanged_projection_damages_nothing():
    w, ctx = _mounted()
    w.on_bus_state("self/decisions", PAYLOAD)         # the one real change
    assert ctx.redraws == 1
    for _ in range(30):                               # 30 identical redeliveries
        w.on_bus_state("self/decisions", PAYLOAD)
    assert ctx.redraws == 1                           # ZERO further damage


def test_redelivered_as_json_string_also_damages_nothing():
    # the same snapshot arriving as a JSON string (the wire form) is still a
    # no-op — the change-gate compares DERIVED state, not the raw envelope.
    w, ctx = _mounted()
    w.on_bus_state("self/decisions", PAYLOAD)
    w.on_bus_state("self/decisions", json.dumps(PAYLOAD))
    assert ctx.redraws == 1


def test_a_real_change_still_redraws():
    # the gate must not over-suppress: an actual content change repaints.
    w, ctx = _mounted()
    w.on_bus_state("self/decisions", PAYLOAD)
    assert ctx.redraws == 1
    moved = json.loads(json.dumps(PAYLOAD))
    moved["nodes"][0]["title"] = "The handoff line is renegotiated"
    w.on_bus_state("self/decisions", moved)
    assert ctx.redraws == 2                           # real change -> one redraw
    changed_anchor = json.loads(json.dumps(moved))
    changed_anchor["anchor"] = "DEC-CROSSING-IS-CHEAP"
    w.on_bus_state("self/decisions", changed_anchor)
    assert ctx.redraws == 3


# --- render ------------------------------------------------------------------

def test_renders_non_blank_with_data():
    w, _ = _mounted()
    w.on_bus_state("self/decisions", PAYLOAD)
    c = SkiaCanvas(); c.create_surface(360, 180)
    w.draw(c, Rect(4, 4, 332, 150))
    assert surface_to_array(c)[..., :3].std() > 3.0


def test_empty_state_renders_awaiting():
    w, _ = _mounted()
    c = SkiaCanvas(); c.create_surface(360, 180)
    w.draw(c, Rect(4, 4, 332, 150))                  # no data yet — must not raise
    assert surface_to_array(c)[..., :3].std() > 1.0


# --- the jump chip (P4 tab seam) --------------------------------------------

def test_explore_chip_publishes_tab_switch():
    w, ctx = _mounted()
    w.on_bus_state("self/decisions", PAYLOAD)
    c = SkiaCanvas(); c.create_surface(360, 180)
    w.draw(c, Rect(4, 4, 332, 150))                  # records the chip rect
    cx = (w._chip_rect[0] + w._chip_rect[2] / 2.0)
    cy = (w._chip_rect[1] + w._chip_rect[3] / 2.0)
    w.on_tap(cx, cy)
    assert ("_app/console/tab", "decisions") in ctx.published


def test_jump_tab_is_configurable():
    w = DecisionsSummaryWidget("ds", jump_tab="architecture",
                               tab_namespace="_app/console/tab")
    ctx = _Ctx(); w.on_mount(ctx)
    w.arranged_rect = Rect(4, 4, 332, 150)
    c = SkiaCanvas(); c.create_surface(360, 180)
    w.draw(c, Rect(4, 4, 332, 150))
    w.on_tap(w._chip_rect[0] + w._chip_rect[2] / 2.0,
             w._chip_rect[1] + w._chip_rect[3] / 2.0)
    assert ("_app/console/tab", "architecture") in ctx.published
