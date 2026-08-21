"""Unit tests for the PowerToggleWidget semantic trigger
([power_toggle bus-event -> HA toggle]).

Verifies the trigger fires the toggle tool exactly once on a matching
``trigger_namespace`` message, respects ``trigger_value``, ignores a
non-matching message, de-dupes a retained redelivery — and that the
default (no-trigger) widget is behavior-unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.render.widgets.power_toggle import PowerToggleWidget


class _FakeCtx:
    """Minimal EngineContext stand-in: records invoke() calls and runs
    schedule_update synchronously."""

    def __init__(self):
        self.invocations = []          # (tool, args)

    def invoke(self, tool, args, *, on_event):
        self.invocations.append((tool, dict(args)))
        on_event({"kind": "result", "ok": True, "summary": f"{tool} ok"})

    def schedule_update(self, fn, *, target=None):
        fn()

    def request_redraw(self, rect=None):
        pass


def _mounted(**kw):
    w = PowerToggleWidget("pt", **kw)
    w.on_mount(_FakeCtx())
    return w


# ---------------------------------------------------------------- trigger fires
def test_matching_trigger_fires_toggle_once():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 trigger_value="contact",
                 ha_entity="switch.red_alert_lights")
    w.on_bus_state("bsg/dradis/contact", "contact")
    assert len(w._ctx.invocations) == 1
    tool, args = w._ctx.invocations[0]
    assert tool == "homeassistant.toggle"
    assert args == {"entity": "switch.red_alert_lights"}


def test_non_matching_trigger_does_not_fire():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 trigger_value="contact",
                 ha_entity="switch.red_alert_lights")
    w.on_bus_state("bsg/dradis/contact", "clear")
    assert w._ctx.invocations == []


def test_truthy_trigger_without_trigger_value():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 ha_entity="switch.red_alert_lights")
    w.on_bus_state("bsg/dradis/contact", "off")     # falsy -> no fire
    assert w._ctx.invocations == []
    w.on_bus_state("bsg/dradis/contact", "1")       # truthy -> fire
    assert len(w._ctx.invocations) == 1


def test_retained_redelivery_deduped():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 trigger_value="contact",
                 ha_entity="switch.red_alert_lights")
    w.on_bus_state("bsg/dradis/contact", "contact")   # fires
    w.on_bus_state("bsg/dradis/contact", "contact")   # same value -> deduped
    assert len(w._ctx.invocations) == 1


def test_rising_edge_refires_after_clear():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 trigger_value="contact",
                 ha_entity="switch.red_alert_lights")
    w.on_bus_state("bsg/dradis/contact", "contact")   # fire 1
    w.on_bus_state("bsg/dradis/contact", "clear")     # non-match, resets edge
    w.on_bus_state("bsg/dradis/contact", "contact")   # fire 2
    assert len(w._ctx.invocations) == 2


def test_trigger_without_ha_entity_is_noop():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 trigger_value="contact")             # no ha_entity
    w.on_bus_state("bsg/dradis/contact", "contact")
    assert w._ctx.invocations == []


def test_custom_toggle_tool_and_int_trigger_value():
    w = _mounted(trigger_namespace="alert/level",
                 trigger_value=1,
                 ha_entity="light.red",
                 toggle_tool="homeassistant.turn_on")
    w.on_bus_state("alert/level", 1)
    assert w._ctx.invocations[0][0] == "homeassistant.turn_on"


# ---------------------------------------------------------------- unchanged path
def test_default_widget_ignores_unrelated_namespace():
    w = _mounted()                                    # classic defaults
    w.on_bus_state("bsg/dradis/contact", "contact")   # no trigger configured
    assert w._ctx.invocations == []
    assert w._plug is None


def test_default_plug_path_still_reads_status():
    w = _mounted()                                    # plug_namespace default
    w.on_bus_state("bringup/plug", "5")
    # plug arrival triggers a status read via status_tool (fleetpower default)
    assert w._plug == "5"
    assert w._ctx.invocations[0][0] == "fleetpower.power_status"
    assert w._ctx.invocations[0][1] == {"target": "5"}


def test_trigger_message_never_treated_as_plug():
    w = _mounted(trigger_namespace="bsg/dradis/contact",
                 trigger_value="contact",
                 ha_entity="switch.red_alert_lights",
                 plug_namespace="bringup/plug")
    w.on_bus_state("bsg/dradis/contact", "contact")
    assert w._plug is None                            # trigger != plug
