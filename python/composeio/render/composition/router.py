"""The SLOT ROUTER — resolve a composition manifest's layers into an ordered
render PLAN of backdrops + reusable bus-bound widgets + placeholders
([CXC-COMP-SLOT], [CXC-COMP-WIDGET-KIND], [CXC-COMP-INTERLEAVE], [CXC-COMP-MISSING]).

This is the render-facing back-end of the derivation ([CXC-COMP-CREDO]: the SVG is
the front-end, this lowers it).  Given:

  * a :class:`CompositionManifest` (from :func:`composition.derive.derive`), and
  * a WIDGET REGISTRY mapping a registered kind ([CXC-COMP-WIDGET-KIND]) to a
    factory, and
  * the station+console SCOPE for binding-key qualification
    ([CXC-CIC-DUTY-STATION]),

it produces an ordered :class:`LayerPlan` — one :class:`PlanEntry` per derived
layer, in the SVG's derived z-order — that a compositor turns into ``pixel_stack``
layers.  The router does NOT draw; it resolves.  This keeps the ONE deterministic
order (the derivation) as the single source and never re-orders in code.

Resolution rules ([CXC-COMP-SLOT]):
  * a backdrop layer -> a ``backdrop`` entry (render the group as-is);
  * a ``slot:`` layer whose kind is REGISTERED -> a ``widget`` entry carrying the
    resolved kind, the SCOPE-qualified binding namespace, the slot bbox, and the
    derived styling; the SAME registered kind serves every screen (define-once);
  * a ``slot:`` layer whose kind is UNREGISTERED, or malformed grammar ->
    a ``placeholder`` entry (render the slot's static children) + a warning
    (preserve-and-flag, never crash, never guess a widget).

The router is layout-only over the manifest; it imports no widget classes, so it
stays an engine module free of a project-layer dependency.  A caller supplies the
factory that builds the concrete widget for a kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .derive import CompositionLayer, CompositionManifest, Diagnostic
from .kinds import is_registered_kind

# A factory builds a concrete widget for a resolved slot.  It receives the plan
# entry (kind, bbox, binding namespace, styling) and returns the caller's widget
# object (opaque to the router).  ``None`` -> the router falls back to placeholder.
WidgetFactory = Callable[["PlanEntry"], Any]


@dataclass
class PlanEntry:
    """One resolved render layer, in derived z-order."""
    z: int
    kind: str                              # 'backdrop' | 'widget' | 'placeholder'
    layer: CompositionLayer               # the derived layer it came from
    widget_kind: Optional[str] = None      # the registered interaction kind
    binding_namespace: Optional[str] = None  # scope-qualified bus namespace
    bbox: Optional[Any] = None             # slot rect (viewBox space)
    text_style: Optional[Any] = None       # derived styling ([CXC-COMP-TEXT-STYLE])
    shape_fill: Optional[str] = None
    shape_ry: Optional[float] = None
    reason: Optional[str] = None           # why placeholder (unregistered/malformed)


@dataclass
class LayerPlan:
    """The ordered render plan ([CXC-COMP-INTERLEAVE]) + any router diagnostics."""
    entries: List[PlanEntry] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)

    def widgets(self) -> List[PlanEntry]:
        return [e for e in self.entries if e.kind == "widget"]

    def placeholders(self) -> List[PlanEntry]:
        return [e for e in self.entries if e.kind == "placeholder"]


def qualify_binding(binding: str, *, station: str = "", console: str = "",
                    prefix: str = "colonial_console") -> str:
    """Scope-qualify a slot binding key ([CXC-CIC-DUTY-STATION]).

    ``slot:gauge@velocity`` on a HELM screen at station ``cic.1.helm`` binds
    ``colonial_console/cic.1.helm/helm/velocity``.  With no station/console the
    binding is returned console-relative (``helm/velocity`` -> just ``velocity``),
    so slots never cross-wire between stations."""
    parts = [p for p in (prefix, station, console, binding) if p]
    return "/".join(parts)


def route(manifest: CompositionManifest, *,
          factory: Optional[WidgetFactory] = None,
          station: str = "", console: str = "",
          binding_prefix: str = "colonial_console") -> LayerPlan:
    """Resolve *manifest* into an ordered :class:`LayerPlan` ([CXC-COMP-SLOT]).

    *factory* (optional) builds the concrete widget for a registered slot; when it
    returns ``None`` (or is absent) the entry is still a ``widget`` plan entry — a
    caller that only wants the PLAN (tests, the linter, an Intelligence inspecting
    a screen) needs no factory.  An unregistered/malformed slot becomes a
    ``placeholder`` entry with a reason (preserve-and-flag)."""
    plan = LayerPlan()
    for layer in manifest.layers:
        if layer.category == "backdrop":
            plan.entries.append(PlanEntry(z=layer.z, kind="backdrop", layer=layer))
            continue
        # a slot layer
        b = layer.slot
        if b is None or not b.well_formed:
            plan.entries.append(PlanEntry(
                z=layer.z, kind="placeholder", layer=layer,
                bbox=layer.bbox,
                reason=f"malformed slot grammar '{layer.label}'"))
            plan.diagnostics.append(Diagnostic(
                "warning", "malformed-slot",
                f"slot '{layer.label}' has malformed grammar; "
                f"placeholder rendered", layer.label))
            continue
        if not is_registered_kind(b.widget):
            plan.entries.append(PlanEntry(
                z=layer.z, kind="placeholder", layer=layer,
                widget_kind=b.widget, bbox=layer.bbox,
                text_style=layer.text_style, shape_fill=layer.shape_fill,
                shape_ry=layer.shape_ry,
                reason=f"unregistered widget kind '{b.widget}'"))
            plan.diagnostics.append(Diagnostic(
                "warning", "unregistered-kind",
                f"slot '{layer.label}' names unregistered widget kind "
                f"'{b.widget}' -> placeholder rendered", layer.label))
            continue
        # registered slot -> a widget entry (bound + placed + styled)
        entry = PlanEntry(
            z=layer.z, kind="widget", layer=layer, widget_kind=b.widget,
            binding_namespace=qualify_binding(
                b.binding, station=station, console=console,
                prefix=binding_prefix),
            bbox=layer.bbox, text_style=layer.text_style,
            shape_fill=layer.shape_fill, shape_ry=layer.shape_ry)
        plan.entries.append(entry)
    return plan


# ---------------------------------------------------------------------------
# missing-art placeholder ([CXC-COMP-MISSING])
# ---------------------------------------------------------------------------
def missing_screen_placeholder_label(console: str, subtab: str) -> str:
    """The clean labeled placeholder for an unbuilt (console, sub-tab)
    ([CXC-COMP-MISSING]) — NEVER a fall-through to another screen's content."""
    return f"{console.upper()} / {subtab.upper()} — screen not built"
