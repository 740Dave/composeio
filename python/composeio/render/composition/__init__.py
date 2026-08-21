"""ComposeUX CIC composition model ([SPEC:CXC-COMP]).

The SVG is the source of truth for screen COMPOSITION — the render's LAYER
Z-ORDER, its LIVE-SLOT positions, and its TEXT STYLING.  This package is the
DERIVATION ([CXC-COMP-LAYER-ORDER]) the render lowers onto its ``pixel_stack``:

  * :func:`derive` — introspect an SVG, three-category sort ([CXC-COMP-REF]),
    read the render z-order from the SVG's own group order, parse
    ``slot:<widget>@<binding>`` ([CXC-COMP-SLOT]), read styling from inner
    elements ([CXC-COMP-TEXT-STYLE]); emit a :class:`CompositionManifest`
    ([CXC-COMP-MANIFEST]);
  * :mod:`lint` — the SAME derivation run in ``--check`` mode ([CXC-COMP-LINT]):
    lint == the render contract, by construction;
  * :mod:`kinds` — the registered widget-KIND set ([CXC-COMP-WIDGET-KIND]);
  * :mod:`router` — resolve a manifest's slot layers to reusable bus-bound
    widgets ([CXC-COMP-SLOT], [CXC-COMP-MISSING]).
"""

from __future__ import annotations

from .derive import (Box, CompositionLayer, CompositionManifest, Diagnostic,
                     SlotBinding, TextStyle, classify, derive, manifest_path_for,
                     parse_slot)
from .kinds import (REGISTERED_WIDGET_KINDS, is_registered_kind,
                    looks_like_appearance_kind)
from .lint import LintReport, lint
from .router import (LayerPlan, PlanEntry, missing_screen_placeholder_label,
                     qualify_binding, route)

__all__ = [
    "derive", "classify", "parse_slot", "manifest_path_for",
    "CompositionManifest", "CompositionLayer", "SlotBinding", "TextStyle",
    "Diagnostic", "Box",
    "REGISTERED_WIDGET_KINDS", "is_registered_kind", "looks_like_appearance_kind",
    "lint", "LintReport",
    "route", "LayerPlan", "PlanEntry", "qualify_binding",
    "missing_screen_placeholder_label",
]
