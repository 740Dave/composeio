"""
Tests for the Configuration-tab settings surface ([SPEC:CXC-CONSOLE-VISUAL];
retention · quiescence-nudge cadence · the notification escalation filter · the
dangerous-mode LOCKED indicator).

Slice acceptance: real STEPPERS drive the deterministic settings (an edit
publishes the value bus-native); the escalation filter is a DEFAULT-DENY
CAN/firewall ruleset ([DEC-NOTIFICATION-ESCALATION-FILTER]) whose action pills
cycle escalate -> glass-only -> drop and republish the ruleset; dangerous-mode
is a LOCKED indicator with NO hit target (D10 — not user-flippable).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import ConfigSettingsWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.config_settings import (
    DEFAULT_RETENTION_NS, DEFAULT_HEARTBEAT_NS, DEFAULT_HEARTBEAT_CURRENT_NS,
    DEFAULT_RULES_NS, SEED_RULES, ACTIONS)

from .conftest import surface_to_array

RECT = Rect(0, 0, 1256, 654)


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


def _mounted():
    w = ConfigSettingsWidget("cfgset")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(1256, 654)
    w.draw(c, RECT)
    return c


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# seed + draw
# =============================================================================

def test_seeds_the_minimal_default_deny_ruleset():
    w, _ = _mounted()
    assert len(w._rules) == len(SEED_RULES) == 3
    patterns = [r["pattern"] for r in w._rules]
    assert "intelligence/architect/permission/request" in patterns
    assert "intelligence/architect/in" in patterns
    assert "intelligence/architect/lifecycle" in patterns
    # every seed rule escalates (the only three that pierce default-deny)
    assert all(r["action"] == "escalate" for r in w._rules)


def test_draw_paints_all_four_sections():
    w, _ = _mounted()
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 3.0
    # the stepper + action-pill hit zones are recorded
    assert w._steppers          # retention + 3 heartbeat = 4 steppers x 2 btns
    assert len(w._action_zones) == 3


def test_draws_expected_labels():
    w, _ = _mounted()
    c = SkiaCanvas(); c.create_surface(1256, 654)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    # card titles (uppercase tracked) + the locked reinforcement + default row
    for token in ("RETENTION", "QUIESCENCE-NUDGE CADENCE",
                  "NOTIFICATION ESCALATION FILTER", "PERMISSIONS"):
        assert token in joined
    assert "Dangerous-mode: bypassed · always on" in joined
    assert "everything else" in joined         # the default-deny row


def test_heartbeat_card_is_honestly_scoped_to_the_quiescence_nudge():
    """[CXC-IC-HEARTBEAT-TWO-SOURCES] IC-C13d — the recurrence guard.

    "Harness heartbeat" is TWO streams: (1) composeio's quiescence nudge, which
    this control DOES drive, and (2) the Claude-Code session-runtime heartbeat
    the human sees (~cadence_s=600), which this control CANNOT reach.  Every
    prior fix (2cc5076 -> e46927f -> 832e479/a307159 -> cb9c60a/2ff5b6f) treated
    the two as one and recurred.  The control must be HONESTLY SCOPED: its header
    names the nudge (not the bare, conflating "Heartbeat cadence") and its note
    EXPLICITLY disclaims the session-runtime heartbeat.  This FAILS against the
    old bare-labeled card (no disclaimer, header "HEARTBEAT CADENCE")."""
    w, _ = _mounted()
    c = SkiaCanvas(); c.create_surface(1256, 654)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    # the header names the quiescence nudge, NOT the bare conflating word.
    assert "QUIESCENCE-NUDGE CADENCE" in joined
    assert "HEARTBEAT CADENCE" not in joined
    # the note EXPLICITLY disclaims stream (2) — the session-runtime heartbeat.
    # tokens are single words, so they survive line-wrapping intact.
    assert "session-runtime" in joined         # names the stream it does NOT drive
    assert "NOT" in joined                      # the disclaimer verb
    assert "runtime" in joined                  # "…lives in the harness runtime…"


# =============================================================================
# SET-vs-CURRENT readback indicator ([CXC-IC-HEARTBEAT-TWO-SOURCES] readback
# half) — the harness publishes its EFFECTIVE cadence on heartbeat/current and
# the card shows a ✓ applied / ⚠ not-yet / ⚠ awaiting delta affordance.
# =============================================================================

def _heartbeat_strings(w):
    """Capture the strings the heartbeat card draws (pre-substitution, so the
    literal ✓ / ⚠ affordance is visible)."""
    c = SkiaCanvas(); c.create_surface(1256, 654)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    return "".join(drawn)


def test_readback_awaiting_when_current_never_arrives():
    """A fresh card (no readback yet) shows the ⚠ caution, not a false ✓."""
    w, _ = _mounted()
    assert w._heartbeat_current is None
    joined = _heartbeat_strings(w)
    assert "⚠" in joined                        # caution affordance present
    assert "awaiting harness readback" in joined
    assert "✓" not in joined                     # never a false all-clear


def test_readback_applied_shows_the_check_affordance():
    """When the harness readback MATCHES the SET triple, the card shows ✓
    applied with the base value as the headline."""
    w, _ = _mounted()
    # the seed SET cadence is {base:10, backoff_after:60, then:60}; the harness
    # reports the SAME -> applied.
    w.on_bus_state(DEFAULT_HEARTBEAT_CURRENT_NS,
                   {"base": 10, "backoff_after": 60, "then": 60})
    assert w._heartbeat_current == {"base": 10, "backoff_after": 60, "then": 60}
    joined = _heartbeat_strings(w)
    assert "✓" in joined                         # affirmative affordance present
    assert "applied" in joined
    assert "current 10 min" in joined            # base value headline


def test_readback_differs_shows_the_caution_affordance():
    """When the harness reports a DIFFERENT cadence than the steppers set, the
    card shows the ⚠ not-yet-applied caution (with the effective triple)."""
    w, _ = _mounted()
    # SET stays the seed {10,60,60}; the harness is still running {5,30,30}.
    w.on_bus_state(DEFAULT_HEARTBEAT_CURRENT_NS,
                   {"base": 5, "backoff_after": 30, "then": 30})
    joined = _heartbeat_strings(w)
    assert "⚠" in joined                         # caution affordance present
    assert "not yet applied" in joined
    assert "current 5/30/30" in joined           # the effective triple
    assert "✓ applied" not in joined


def test_readback_namespace_is_read_only():
    """Receiving the readback NEVER publishes — the widget subscribes
    heartbeat/current but must not write it (frozen contract)."""
    w, ctx = _mounted()
    w.on_bus_state(DEFAULT_HEARTBEAT_CURRENT_NS,
                   {"base": 5, "backoff_after": 30, "then": 30})
    assert ctx.published == []                   # read-only: no write-back
    # and a stepper edit still publishes ONLY the writable heartbeat namespace,
    # never the /current readback.
    _drawn(w)
    cx, cy = _stepper_for(w, "hb:base", +1)
    w.on_tap(cx, cy)
    published_ns = [ns for ns, _ in ctx.published]
    assert DEFAULT_HEARTBEAT_NS in published_ns
    assert DEFAULT_HEARTBEAT_CURRENT_NS not in published_ns


def test_readback_current_ns_follows_heartbeat_ns_override():
    """An overridden heartbeat_ns carries the /current readback with it, so the
    SET/CURRENT pair stays coherent."""
    w = ConfigSettingsWidget("cfg2", heartbeat_ns="fleet/unit7/cadence")
    assert w.heartbeat_current_ns == "fleet/unit7/cadence/current"


# =============================================================================
# retention + heartbeat steppers publish bus-native
# =============================================================================

def _stepper_for(w, key, delta):
    for (x0, y0, x1, y1, kind, k, d) in w._steppers:
        if k == key and d == delta:
            return (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return None


def test_retention_stepper_publishes_bus_native():
    w, ctx = _mounted()
    _drawn(w)
    cx, cy = _stepper_for(w, "retention", +1)
    w.on_tap(cx, cy)
    assert w._retention == 31
    assert ctx.published == [(DEFAULT_RETENTION_NS, {"cleanupPeriodDays": 31})]
    cx, cy = _stepper_for(w, "retention", -1)
    w.on_tap(cx, cy)
    assert w._retention == 30


def test_retention_floors_at_one():
    w, ctx = _mounted()
    w._retention = 1
    _drawn(w)
    cx, cy = _stepper_for(w, "retention", -1)
    w.on_tap(cx, cy)
    assert w._retention == 1                    # never below 1


def test_heartbeat_steppers_publish_the_full_triple():
    w, ctx = _mounted()
    _drawn(w)
    cx, cy = _stepper_for(w, "hb:base", +1)
    w.on_tap(cx, cy)
    assert w._heartbeat["base"] == 11
    ns, val = ctx.published[-1]
    assert ns == DEFAULT_HEARTBEAT_NS
    assert val == {"base": 11, "backoff_after": 60, "then": 60}


def test_bus_value_overrides_local_seed():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_RETENTION_NS, {"cleanupPeriodDays": 7})
    assert w._retention == 7
    w.on_bus_state(DEFAULT_HEARTBEAT_NS, {"base": 15, "backoff_after": 90})
    assert w._heartbeat["base"] == 15 and w._heartbeat["backoff_after"] == 90
    # garbage never clobbers
    w.on_bus_state(DEFAULT_RETENTION_NS, "not-a-number")
    assert w._retention == 7


# =============================================================================
# escalation filter — action pills cycle + republish the ruleset
# =============================================================================

def test_rule_action_cycles_and_republishes():
    w, ctx = _mounted()
    _drawn(w)
    x0, y0, x1, y1, idx = w._action_zones[0]
    assert w._rules[0]["action"] == "escalate"
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w._rules[0]["action"] == ACTIONS[1]      # glass-only
    ns, payload = ctx.published[-1]
    assert ns == DEFAULT_RULES_NS
    assert payload["rules"][0]["action"] == "glass-only"
    # cycle wraps escalate -> glass-only -> drop -> escalate
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w._rules[0]["action"] == "drop"
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w._rules[0]["action"] == "escalate"


def test_rules_bus_state_replaces_the_seed():
    w, _ = _mounted()
    new_rules = [{"pattern": "self/context_level", "condition": "if >80%",
                  "action": "escalate", "rate_limit": ""}]
    w.on_bus_state(DEFAULT_RULES_NS, {"rules": new_rules})
    assert w._rules == new_rules and w._rules_seen is True


# =============================================================================
# dangerous-mode is a LOCKED indicator — NOT a toggle (D10)
# =============================================================================

def test_dangerous_mode_has_no_hit_target():
    """The permissions card is a reinforcement statement, not a control: no
    stepper/action zone belongs to it, so no tap can flip dangerous-mode."""
    w, ctx = _mounted()
    _drawn(w)
    # the only interactive zones are the steppers + the 3 rule action pills —
    # NONE toggle permissions.  A tap in dead space publishes nothing.
    w.on_tap(5.0, 5.0)
    assert ctx.published == []


# =============================================================================
# parser wiring — four subscribed config namespaces (retention · heartbeat ·
# heartbeat/current READBACK · rules)
# =============================================================================

def test_parser_wires_four_subscriptions():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "config_settings",
                                           "id": "cfgset"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed.count(DEFAULT_RETENTION_NS) == 1
    assert subbed.count(DEFAULT_HEARTBEAT_NS) == 1
    assert subbed.count(DEFAULT_HEARTBEAT_CURRENT_NS) == 1   # the readback
    assert subbed.count(DEFAULT_RULES_NS) == 1
    w = comp.subscriptions[0][1]
    assert isinstance(w, ConfigSettingsWidget)
    assert w.interactive is True
    assert w.paint_overflow == ct.SHADOW_PAINT_OVERFLOW


# =============================================================================
# seeded full render (glass-checkable PNG)
# =============================================================================

def test_seeded_config_settings_render_png():
    w, _ = _mounted()
    # seed the harness readback matching the SET cadence so the glass artifact
    # shows the ✓-applied indicator (the retained steady-state a live console
    # normally shows), not only the pre-boot ⚠-awaiting state.
    w.on_bus_state(DEFAULT_HEARTBEAT_CURRENT_NS,
                   {"base": 10, "backoff_after": 60, "then": 60})
    c = SkiaCanvas(); c.create_surface(1256, 654)
    w.draw(c, RECT)
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    out = os.path.join(cap_dir, "config_settings.png")
    c.save_png(out)
    assert os.path.exists(out) and os.path.getsize(out) > 3000


# =============================================================================
# ADDITIVE `sections` filter ([CXC-IC-CONFIG-MASTERDETAIL]) — the master/detail
# right pane draws ONE section; UNSET preserves the legacy 2x2 grid
# =============================================================================

def test_sections_unset_is_the_legacy_grid():
    """A widget with no `sections` prop is byte-for-byte the original: all four
    cards, all hit zones."""
    w, _ = _mounted()
    assert w.sections is None
    _drawn(w)
    # retention + 3 heartbeat steppers = 4 x 2 buttons; 3 rule action zones
    assert len(w._steppers) == 8
    assert len(w._action_zones) == 3


def test_sections_retention_only_draws_one_card():
    w = ConfigSettingsWidget("cfgret", sections=["retention"])
    w.on_mount(_Ctx())
    w.arranged_rect = RECT
    c = SkiaCanvas(); c.create_surface(1256, 654)
    drawn = []
    real = c.draw_text
    c.draw_text = lambda s, *a, **k: (drawn.append(s), real(s, *a, **k))[1]
    w.draw(c, RECT)
    joined = "".join(drawn)
    assert "RETENTION" in joined
    # the other sections' titles are NOT drawn
    assert "QUIESCENCE-NUDGE CADENCE" not in joined
    assert "NOTIFICATION ESCALATION FILTER" not in joined
    assert "PERMISSIONS" not in joined
    # only the retention stepper's hit zones exist (no heartbeat/rules)
    assert len(w._steppers) == 2
    assert w._action_zones == []


def test_sections_escalation_only_has_the_rule_pills_not_steppers():
    w = ConfigSettingsWidget("cfgesc", sections=["escalation"])
    w.on_mount(_Ctx())
    w.arranged_rect = RECT
    _drawn(w)
    assert w._steppers == []                 # no retention/heartbeat steppers
    assert len(w._action_zones) == 3         # the three seed-rule action pills


def test_parser_passes_sections_through():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "config_settings",
                                           "id": "c", "sections": ["heartbeat"]}]})
    w = comp.subscriptions[0][1]
    assert w.sections == ["heartbeat"]
