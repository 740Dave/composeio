"""
Widget registry — the EXPLICIT, enumerable ``type:``->widget-class map
([SPEC:CXC-CONSOLE-PROD] [CXC-ICP-FOUND-B]).

Before this, the scene ``type:`` token was resolved by an implicit if/elif
chain inside ``parser.py :: build_leaf`` (``key = type.lower().replace(
"widget","")``): the type->class association was real but SCATTERED and not
discoverable at runtime.  Foundation B makes that map a single, enumerable
data structure of DESCRIPTORS, so the console personalization surfaces can
LIST and SELECT widgets without hardcoding a widget list:

  * ``WIDGET_REGISTRY``   — the ONE definition site: canonical key -> descriptor.
  * ``WidgetDescriptor``  — {type, cls, display_name, required_namespaces,
                            category (+ categories tags), aliases, build}.
  * ``list_widgets(cat)`` — the discovery API the S4 Collaboration selector,
                            the S5 Dashboard palette, and S6 read (never a
                            literal widget list).
  * ``resolve_class`` / ``get_descriptor`` — single-lookup class resolution.
  * ``lint_registry``     — a registered type with a missing class / empty
                            display_name is a lint error, not a silent drop
                            ([CXC-ICP-FOUND-B]).

Construction stays reuse-by-reference: ``build_leaf`` consults this registry
as the class authority and, when a descriptor carries a ``build`` callable,
constructs THROUGH it — the S6 pluggable seam (a NEW widget = its module +
one descriptor with a ``build`` hook, no ``build_leaf`` edit).  The shipped
widgets keep their audited bespoke construction branch (``build=None``); the
registry remains authoritative for the map + all discovery metadata.  An
UNKNOWN ``type:`` still degrades to ``PlaceholderWidget`` (shipped behavior).

The registry is populated at import time by ``parser.py`` (which owns the
widget-class imports); ``_ensure_loaded`` lazily triggers that import so a
caller that imports THIS module first still sees a populated registry.  It is
built at import/parse time, so a newly registered widget appears on the NEXT
launch (modulo restart, [CXC-ICP-RESTART]).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple

# The four canonical category tags the surfaces filter on ([CXC-ICP-FOUND-B]):
#   collaboration      — a Collaboration-tab widget (S4 lists these)
#   rail               — a left-rail card
#   config             — a Configuration-tab surface
#   dashboard-eligible — placeable on the S5 Dashboard grid (S5 lists these)
#   primitive          — a layout/graphic primitive (label, rect, image, …)
CATEGORIES = ("collaboration", "rail", "config", "dashboard-eligible",
              "primitive")

# The placement REGIONS a fit-aware surface can host a widget in
# ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1).  A ``region_affinity`` set (when
# declared) names the regions a widget BELONGS to; absent = "wherever its
# ``min_size`` fits".  ``rail`` = the 332px compact-card column; ``hero`` = the
# big central read region; ``dashboard`` = the S5 composable grid; ``tab`` = a
# whole console tab.
REGIONS = ("rail", "hero", "dashboard", "tab")


@dataclass
class WidgetDescriptor:
    """One widget type's registry entry.

    ``category`` is the PRIMARY tag ([CXC-ICP-FOUND-B] descriptor shape);
    ``categories`` carries every tag a widget answers to (a gauge is both a
    ``rail`` card and ``dashboard-eligible``), so ``list_widgets(category=…)``
    can match a secondary tag without forcing one bucket per widget.  ``build``
    is the OPTIONAL pluggable-construction hook (``(spec, wid, comp) -> Widget``);
    ``None`` means the shipped ``build_leaf`` branch constructs it.

    Placement ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1):

    * ``min_size`` ``(w, h)`` — the LOAD-BEARING generic fit test.  A surface
      that knows its own dimensions answers "does this widget fit?" by
      dimensions alone; it survives a new region or a resized rail.  ``(0, 0)``
      (the default) means "fits anywhere" — the honest shipped behavior for a
      widget that never declared a floor.
    * ``region_affinity`` — the OPTIONAL curation set over ``REGIONS`` (David's
      D1 refinement: min_size is load-bearing, affinity is optional).  When
      declared it names the regions a widget belongs to, so a widget that
      *technically* fits the rail but is dashboard-first (a DAG, a diagram) is
      still steered off the rail.  ``None`` (absent) = "wherever ``min_size``
      allows", the generic path."""
    type: str
    cls: Any
    display_name: str
    required_namespaces: List[str] = field(default_factory=list)
    category: str = "collaboration"
    categories: tuple = ()
    aliases: tuple = ()
    build: Optional[Callable[..., Any]] = None
    min_size: Tuple[int, int] = (0, 0)
    region_affinity: Optional[FrozenSet[str]] = None

    def __post_init__(self) -> None:
        tags = tuple(self.categories) if self.categories else ()
        if self.category not in tags:
            tags = (self.category, *tags)
        self.categories = tags
        self.min_size = (int(self.min_size[0]), int(self.min_size[1]))
        if self.region_affinity is not None:
            self.region_affinity = frozenset(str(r) for r in
                                             self.region_affinity)

    # -- placement fit tests ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1) ---------

    def fits(self, area_w: Optional[float] = None,
             area_h: Optional[float] = None) -> bool:
        """Pure DIMENSION fit: does this widget's ``min_size`` fit an area?  A
        ``None`` bound is unconstrained (a rail column is width-bounded but
        scrolls, so its height is ``None``)."""
        w, h = self.min_size
        if area_w is not None and w > area_w:
            return False
        if area_h is not None and h > area_h:
            return False
        return True

    def fits_region(self, region: str, area_w: Optional[float] = None,
                    area_h: Optional[float] = None) -> bool:
        """Curated + dimensional fit for a named *region*: a declared
        ``region_affinity`` that EXCLUDES *region* is a miss (steer it
        elsewhere), then the generic ``min_size`` dimension test applies.  An
        absent affinity defers entirely to ``min_size`` (the generic path)."""
        if self.region_affinity is not None and region not in \
                self.region_affinity:
            return False
        return self.fits(area_w, area_h)

    def redirect_region(self, region: str) -> str:
        """Where a widget that does NOT fit *region* should go instead.  The
        Dashboard is the ratified home for area-hungry composable widgets AND
        the only USER-composable large region ([DEC-WIDGET-PLACEMENT-CALLS-
        SETTLED]), so it is the redirect target whenever the widget is affine
        to it (or declares no affinity); a widget explicitly affine to a
        NON-dashboard, non-*region* home names that home instead."""
        if self.region_affinity and "dashboard" not in self.region_affinity:
            for r in ("hero", "tab"):
                if r != region and r in self.region_affinity:
                    return r
        return "dashboard"


# The ONE definition site of the type->class map ([CXC-ICP-FOUND-B] verify (e)).
WIDGET_REGISTRY: Dict[str, WidgetDescriptor] = {}


def canonical_key(wtype: str) -> str:
    """The scene ``type:`` token reduced to its canonical registry key — the
    exact reduction ``build_leaf`` used (``lower().replace("widget","")``),
    centralized so there is one spelling of the rule."""
    return str(wtype).lower().replace("widget", "")


def register(desc: WidgetDescriptor) -> WidgetDescriptor:
    """Register a descriptor under its canonical type key + every alias.  The
    same descriptor object is shared by all its keys (so ``list_widgets``
    de-dups by identity)."""
    for token in (desc.type, *desc.aliases):
        WIDGET_REGISTRY[canonical_key(token)] = desc
    return desc


def _ensure_loaded() -> None:
    """Import ``parser`` on first access so the registry is populated even when
    a caller imports THIS module before the parser.  No cycle: ``parser``
    finishes importing ``widget_registry`` (fully initialized above) before it
    calls ``register`` at its module bottom."""
    if not WIDGET_REGISTRY:
        from . import parser  # noqa: F401


def get_descriptor(wtype: str) -> Optional[WidgetDescriptor]:
    """The descriptor for a scene ``type:`` token, or ``None`` if unknown."""
    _ensure_loaded()
    return WIDGET_REGISTRY.get(canonical_key(wtype))


def resolve_class(wtype: str) -> Optional[Any]:
    """Single-lookup class resolution: the widget class for a ``type:`` token,
    or ``None`` (the caller falls back to ``PlaceholderWidget``)."""
    desc = get_descriptor(wtype)
    return desc.cls if desc is not None else None


def list_widgets(category: Optional[str] = None) -> List[WidgetDescriptor]:
    """The discovery API ([CXC-ICP-FOUND-B]): every registered descriptor
    (de-duplicated across aliases), optionally filtered to those carrying
    ``category`` among their tags.  Sorted (category, display_name) for a
    stable selector order."""
    _ensure_loaded()
    unique: Dict[int, WidgetDescriptor] = {}
    for desc in WIDGET_REGISTRY.values():
        unique[id(desc)] = desc
    descs = list(unique.values())
    if category is not None:
        descs = [d for d in descs if category in d.categories]
    descs.sort(key=lambda d: (d.category, d.display_name.lower()))
    return descs


def lint_registry() -> List[str]:
    """Registry integrity: every descriptor must carry a class and a non-empty
    ``display_name``.  Returns a list of human-readable errors (empty == clean)
    — a registered type with a missing class is a lint error, not a silent
    drop ([CXC-ICP-FOUND-B])."""
    _ensure_loaded()
    errors: List[str] = []
    seen: Dict[int, WidgetDescriptor] = {}
    for key, desc in WIDGET_REGISTRY.items():
        seen[id(desc)] = desc
        if desc.cls is None:
            errors.append(f"registry key {key!r}: descriptor has no class")
        if not (desc.display_name and str(desc.display_name).strip()):
            errors.append(f"registry key {key!r}: empty display_name")
        if desc.category not in CATEGORIES:
            errors.append(
                f"registry key {key!r}: unknown category {desc.category!r}")
    return errors
