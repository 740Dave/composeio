"""Scroll-direction fix + config ([CXC-ICP-S6-SCROLLDIR]).

Two concerns, verified end to end:

1. ROOT-CAUSE FIX — the wheel delta must be delivered in ONE canonical,
   OS-respecting orientation.  On macOS the OS bakes its "natural scrolling"
   preference into the wheel delta and flags it with SDL_MOUSEWHEEL_FLIPPED;
   the code used to ignore that flag and route the raw delta, so the
   scrollable widgets (authored against the NORMAL orientation) ran backwards.
   window.normalize_wheel_delta negates ONLY on FLIPPED — conditional on the
   OS-provided flag, never a hardcoded inversion — so NORMAL is untouched.

2. CONFIG — a per-instance ``scroll_direction`` preference (natural |
   traditional).  Default "natural" passes the OS delta straight through;
   "traditional" flips the sign in the runtime's scroll dispatch.  Persisted
   in Foundation A (console.scroll_direction) and written by the Config →
   Appearance ``console_prefs`` toggle.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
for _p in (os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher"),
           os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sdl2

from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.parser import (
    parse_widget_tree, _norm_scroll_direction, SCROLL_DIRECTIONS)
from composeio.render.window import normalize_wheel_delta
from composeio.render.widgets import console_prefs as cp


# ---------------------------------------------------------------------------
# 1. window.normalize_wheel_delta — honor SDL_MOUSEWHEEL_FLIPPED
# ---------------------------------------------------------------------------

def test_normal_delta_passes_through_unchanged():
    # RPi / mouse wheel: direction is NORMAL, the widget convention holds.
    assert normalize_wheel_delta(0.0, 1.0, sdl2.SDL_MOUSEWHEEL_NORMAL) == (0.0, 1.0)
    assert normalize_wheel_delta(2.0, -3.0, sdl2.SDL_MOUSEWHEEL_NORMAL) == (2.0, -3.0)


def test_flipped_delta_is_negated():
    # macOS natural scrolling: the OS pre-inverts + flags FLIPPED; we normalize
    # back to the canonical orientation the widgets expect.
    assert normalize_wheel_delta(0.0, 1.0, sdl2.SDL_MOUSEWHEEL_FLIPPED) == (0.0, -1.0)
    assert normalize_wheel_delta(2.0, -3.0, sdl2.SDL_MOUSEWHEEL_FLIPPED) == (-2.0, 3.0)


def test_normalization_is_not_a_hardcoded_flip():
    # The SAME physical delta yields OPPOSITE results purely from the OS flag —
    # so toggling the macOS natural-scroll checkbox (which flips the flag)
    # flips the behavior, i.e. it tracks the OS rather than hardcoding a sign.
    up_normal = normalize_wheel_delta(0.0, 1.0, sdl2.SDL_MOUSEWHEEL_NORMAL)
    up_flipped = normalize_wheel_delta(0.0, 1.0, sdl2.SDL_MOUSEWHEEL_FLIPPED)
    assert up_normal[1] == -up_flipped[1]


# ---------------------------------------------------------------------------
# 2a. parser — scene-level design.scroll_direction
# ---------------------------------------------------------------------------

def test_norm_scroll_direction_defaults_and_clamps():
    assert _norm_scroll_direction(None) == "natural"
    assert _norm_scroll_direction("natural") == "natural"
    assert _norm_scroll_direction("Traditional") == "traditional"
    assert _norm_scroll_direction("nonsense") == "natural"
    assert set(SCROLL_DIRECTIONS) == {"natural", "traditional"}


def _scroll_app(scroll_direction=None):
    """A full-surface interactive rect (a genuine hit target) in a pixel_stack;
    scroll dispatch routes to whatever hit_test_tree returns."""
    design = {"width": 400, "height": 400}
    if scroll_direction is not None:
        design["scroll_direction"] = scroll_direction
    config = {
        "design": design,
        "namespaces": [{"name": "demo/swipe", "state_engine": "json"}],
        "widgets": [{
            "type": "pixel_stack", "id": "root_stack",
            "width": 400, "height": 400,
            "children": [{
                "type": "rect", "id": "surface",
                "x": 0, "y": 0, "w": 400, "h": 400,
                "fill_color": "#222233",
                "swipe_namespace": "demo/swipe",   # -> interactive / hittable
            }],
        }],
    }
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    app.render_frame()          # arrange so the rect gets an arranged_rect
    return app


class _ScrollPointer:
    def __init__(self, x, y, dx, dy):
        self.type = "scroll"
        self.x, self.y = float(x), float(y)
        self.dx, self.dy = float(dx), float(dy)
        self.pointer_id = 0


def _record_scroll(app):
    surface = next(w for w in app.comp.root.iter_widgets()
                   if w.widget_id == "surface")
    got = []
    surface.on_scroll = lambda dx, dy: got.append((dx, dy))
    return got


def test_default_scroll_direction_is_natural():
    app = _scroll_app()
    assert app.comp.scroll_direction == "natural"


def test_natural_passes_the_delta_through():
    app = _scroll_app("natural")
    got = _record_scroll(app)
    app._dispatch_pointer(_ScrollPointer(200, 200, 0.0, 1.0))
    assert got == [(0.0, 1.0)]          # unchanged — matches the OS delta


def test_traditional_flips_the_effective_sign():
    app = _scroll_app("traditional")
    got = _record_scroll(app)
    app._dispatch_pointer(_ScrollPointer(200, 200, 0.0, 1.0))
    assert got == [(-0.0, -1.0)]        # inverted relative to natural


def test_traditional_is_the_exact_negation_of_natural():
    nat, trad = _scroll_app("natural"), _scroll_app("traditional")
    gn, gt = _record_scroll(nat), _record_scroll(trad)
    nat._dispatch_pointer(_ScrollPointer(200, 200, 3.0, -5.0))
    trad._dispatch_pointer(_ScrollPointer(200, 200, 3.0, -5.0))
    (ndx, ndy), (tdx, tdy) = gn[0], gt[0]
    assert (tdx, tdy) == (-ndx, -ndy)


# ---------------------------------------------------------------------------
# 2b. Foundation A (config_persister) — persist / read / default
# ---------------------------------------------------------------------------

def test_foundation_a_default_is_natural():
    import config_persister as cpst
    assert cpst.console_settings({})["scroll_direction"] == "natural"
    assert cpst.DEFAULT_CONSOLE["scroll_direction"] == "natural"
    assert "scroll_direction" in cpst._CONSOLE_FIELDS


def test_foundation_a_persists_a_valid_value_and_rejects_garbage():
    import config_persister as cpst
    ns = "intelligence/architect/config/console/scroll_direction"
    meta, changed = cpst.persist_config({}, ns, "traditional", "architect")
    assert changed is True
    assert meta["console"]["scroll_direction"] == "traditional"
    assert cpst.console_settings(meta)["scroll_direction"] == "traditional"
    # an out-of-domain value is a no-op (never writes garbage)
    meta2, changed2 = cpst.persist_config(meta, ns, "sideways", "architect")
    assert changed2 is False
    assert meta2["console"]["scroll_direction"] == "traditional"
    # an identical write is a no-op
    _, changed3 = cpst.persist_config(meta, ns, "traditional", "architect")
    assert changed3 is False


def test_foundation_a_scroll_edit_does_not_clobber_siblings():
    import config_persister as cpst
    pref = "intelligence/architect/config/console/"
    meta, _ = cpst.persist_config({}, pref + "font_scale", 1.25, "architect")
    meta, _ = cpst.persist_config(meta, pref + "scroll_direction",
                                  "traditional", "architect")
    assert meta["console"]["font_scale"] == 1.25
    assert meta["console"]["scroll_direction"] == "traditional"


# ---------------------------------------------------------------------------
# 2c. personalization applier — Foundation A -> comp.scroll_direction
# ---------------------------------------------------------------------------

def test_applier_resolves_and_seeds_comp():
    import personalization as person
    resolved = person.apply_personalization({"scroll_direction": "traditional"})
    assert resolved["scroll_direction"] == "traditional"
    # a malformed value resolves to natural (never raises)
    assert person.apply_personalization(
        {"scroll_direction": "bogus"})["scroll_direction"] == "natural"
    assert person.apply_personalization({})["scroll_direction"] == "natural"


def test_applier_writes_scroll_direction_onto_composition():
    import personalization as person
    cfg = {
        "design": {"width": 200, "height": 200},
        "namespaces": [],
        "widgets": [{"type": "label", "text": "hi"}],
    }
    comp = parse_widget_tree(cfg)
    assert comp.scroll_direction == "natural"
    person.apply_to_composition(comp, {"scroll_direction": "traditional"})
    assert comp.scroll_direction == "traditional"


# ---------------------------------------------------------------------------
# 2d. console_prefs — the Config → Appearance toggle WRITE side
# ---------------------------------------------------------------------------

def test_console_prefs_publishes_scroll_edit():
    w = cp.ConsolePrefsWidget("prefs")
    published = []
    w.publish = lambda ns, val: published.append((ns, val))
    w._activate("scroll", "traditional")
    assert published == [(cp.DEFAULT_SCROLL_NS, "traditional")]
    assert w._scroll_direction == "traditional"


def test_console_prefs_reflects_a_persisted_value():
    w = cp.ConsolePrefsWidget("prefs")
    w.on_bus_state(cp.DEFAULT_SCROLL_NS, "traditional")
    assert w._scroll_direction == "traditional"
    # garbage on the bus is ignored (keeps the last good value)
    w.on_bus_state(cp.DEFAULT_SCROLL_NS, "sideways")
    assert w._scroll_direction == "traditional"


def test_console_prefs_scroll_section_is_selectable():
    # the section filter accepts the new "scroll" key (drawn standalone).
    w = cp.ConsolePrefsWidget("prefs", sections=["scroll"])
    assert w.sections == ["scroll"]
    assert "scroll" in w._section_drawers()


# ---------------------------------------------------------------------------
# 3. WIRING REGRESSION — the seam that makes the live toggle actually work.
#
# The per-field pieces above all pass in ISOLATION; the live defect (David,
# 2026-07-25) was that ``console/scroll_direction`` was never DECLARED as a
# global/exported namespace in the console scene — its siblings theme /
# font_scale / theme_custom are (console.compose.yaml).  An undeclared
# namespace is not registered on the bus (runtime._register_namespaces), so the
# console_prefs publish was never bridged to MQTT where config_persister (a
# separate bus peer) folds edits into meta.console.  Consequence, BOTH symptoms:
#   A) launch always read the "natural" default -> flipping never changed live
#      scrolling; B) the edit never persisted / re-seeded -> the control reverted.
# ---------------------------------------------------------------------------

import yaml  # noqa: E402

_CONSOLE_SCENE = os.path.join(
    REPO_ROOT, "compose_projects", "intelligence", "console", "console.compose.yaml")

# every Foundation A APPEARANCE write seam the console_prefs widget publishes;
# each MUST be a declared global/exported json namespace or config_persister
# never receives the edit.  scroll_direction was the dropped member — this tuple
# is the cross-implementation guard so a FUTURE dropped namespace also fails.
_APPEARANCE_WRITE_NAMESPACES = (
    "intelligence/architect/config/console/theme",
    "intelligence/architect/config/console/font_scale",
    "intelligence/architect/config/console/theme_custom",
    "intelligence/architect/config/console/scroll_direction",
)


def _scene_namespaces():
    with open(_CONSOLE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return {n["name"]: n for n in (cfg.get("namespaces") or [])
            if isinstance(n, dict) and n.get("name")}


def test_appearance_write_namespaces_are_declared_global_and_exported():
    """WIRING GUARD ([CXC-ICP-S6-SCROLLDIR]): EVERY appearance write seam the
    console_prefs toggle publishes must be a declared global/exported json
    namespace, else its publish never reaches config_persister (the defect).
    Parametrized over the whole family so any future dropped namespace fails
    loudly — not just scroll_direction."""
    declared = _scene_namespaces()
    for ns in _APPEARANCE_WRITE_NAMESPACES:
        assert ns in declared, f"{ns} is not declared in the console scene"
        d = declared[ns]
        assert d.get("scope") == "global", f"{ns} must be scope: global"
        assert d.get("export") is True, f"{ns} must be export: true"
        assert d.get("state_engine") == "json", f"{ns} must be a json engine"


def test_console_prefs_scroll_ns_matches_a_declared_namespace():
    """The widget's scroll_ns default (its publish target) must be a declared
    scene namespace — the exact write<->declare linkage the defect severed."""
    assert cp.DEFAULT_SCROLL_NS in _scene_namespaces()


def test_scroll_edit_round_trips_through_persister_without_reverting():
    """Symptom B guard: a published "traditional" edit, folded by config_persister
    and read back through the launch seam then re-seeded to the widget, STAYS
    "traditional" — it does NOT snap back to the "natural" default."""
    import config_persister as cpst
    ns = cp.DEFAULT_SCROLL_NS
    # 1) the toggle publishes "traditional"; config_persister folds it into meta
    meta, changed = cpst.persist_config({}, ns, "traditional", "architect")
    assert changed is True
    # 2) the persister RE-SEED re-broadcasts the persisted value (not the default)
    reseed = cpst.console_settings(meta)["scroll_direction"]
    assert reseed == "traditional"
    # 3) the widget: tap Traditional, then the re-seed arrives -> must NOT revert
    w = cp.ConsolePrefsWidget("prefs")
    w.publish = lambda n, v: None
    w._activate("scroll", "traditional")
    assert w._scroll_direction == "traditional"
    w.on_bus_state(ns, reseed)
    assert w._scroll_direction == "traditional"      # no revert


def test_persisted_traditional_flips_runtime_scroll_end_to_end():
    """Symptom A guard, end to end: a PERSISTED "traditional" -> the launch
    applier seeds comp.scroll_direction -> the runtime's scroll dispatch flips
    the delta sign.  (The earlier flip test seeds comp via design; this proves
    the persistence READ path actually reaches the runtime.)"""
    import personalization as person
    app = _scroll_app("natural")            # scene default is natural
    person.apply_to_composition(app.comp, {"scroll_direction": "traditional"})
    assert app.comp.scroll_direction == "traditional"
    got = _record_scroll(app)
    app._dispatch_pointer(_ScrollPointer(200, 200, 0.0, 1.0))
    assert got == [(-0.0, -1.0)]            # flipped by the persisted preference


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
    print(f"\n{passed}/{len(fns)} passed")
    sys.exit(0 if passed == len(fns) else 1)
