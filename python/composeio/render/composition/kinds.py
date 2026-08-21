"""The registered reusable widget-KIND set ([CXC-COMP-WIDGET-KIND]).

A ``slot:<widget>@<binding>`` label names an INTERACTION/BEHAVIOR model, never
an appearance ([CXC-COMP-WIDGET-KIND]): a custom-shaped button is still ``button``
(its shape is SVG art read from the inner shape element, [CXC-COMP-TEXT-STYLE]).
This module is the ONE closed set the derivation resolves ``<widget>`` against; an
unregistered kind is preserve-and-flag (placeholder + warning, [CXC-COMP-SLOT]),
never a crash and never a guessed widget.

Keeping the set HERE (not scattered across the router/linter) is the single-source
discipline: the linter ([CXC-COMP-LINT]) and the slot router consult the same set,
so lint == the render contract by construction.
"""

from __future__ import annotations

from typing import FrozenSet

# The closed set of registered interaction kinds ([CXC-COMP-WIDGET-KIND]).  Each
# names an interaction/behavior, NOT a look.  Appearance is SVG art on the slot's
# inner elements ([CXC-COMP-TEXT-STYLE]); a per-appearance kind (``custom-button``,
# ``round-button``, ``red-gauge``) is FORBIDDEN and lint-rejected.
REGISTERED_WIDGET_KINDS: FrozenSet[str] = frozenset({
    "button",    # momentary action
    "radio",     # mutually-exclusive selection
    "tab_row",   # segmented navigation selection
    "text",      # a static / bound text readout
    "readout",   # a bound scalar readout
    "value",     # a bound scalar readout (alias of readout in intent)
    "gauge",     # a bound analog indicator
    "graph",     # a bound series
    "table",     # a bound tabular / list view
    "led_bar",   # a bound discrete-level bar
    "scene",     # a self-contained sub-scene drop-in (its OWN internal
                 # bindings, e.g. engine_thrust_panel subscribing throttle/
                 # left + throttle/right itself) -- ratified
                 # [DD-MENU-SLOT-SCENE-GRAMMAR] Arm A: `slot:scene@<mount-
                 # scope>` fits this SAME grammar unchanged, `<binding>` is
                 # an OPTIONAL mount-scope naming the registered scene
                 # rather than a single bound value ([CXC-MENU-SLOT-SIZING]
                 # kind (2)).
})

# A per-APPEARANCE naming smell — a kind that describes a look, not a behavior.
# These are never registered; the linter flags them explicitly so the diagnostic
# points the author at [CXC-COMP-WIDGET-KIND] rather than a bare "unknown kind".
_APPEARANCE_SMELLS = ("custom-", "round-", "red-", "green-", "big-", "small-")


def is_registered_kind(widget: str) -> bool:
    """True iff *widget* names a registered interaction kind."""
    return widget in REGISTERED_WIDGET_KINDS


def looks_like_appearance_kind(widget: str) -> bool:
    """True for a per-appearance kind smell (``custom-button``, ``round-button``)
    — an unregistered kind that describes a LOOK, so the linter can point the
    author at expressing appearance as SVG art ([CXC-COMP-WIDGET-KIND])."""
    w = widget.strip().lower()
    return any(w.startswith(p) for p in _APPEARANCE_SMELLS)
