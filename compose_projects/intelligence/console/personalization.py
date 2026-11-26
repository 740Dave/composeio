#!/usr/bin/env python3
"""console personalization — the READ + APPLY side of the render-deep surfaces
([SPEC:CXC-CONSOLE-PROD] surfaces 1-3, [CXC-ICP-S1-RESIZE]/[CXC-ICP-S2-FONTSCALE]
/[CXC-ICP-S3-THEME]).

Foundation A (compose_projects/intelligence/publisher/config_persister.py :: console_settings)
is the durable, per-<id> personalization READ SEAM: window / font_scale / theme /
theme_custom.  THIS module is the render-engineer half — it reads that seam at
LAUNCH and APPLIES the settings to the running console (modulo restart,
[CXC-ICP-RESTART]).  It does NOT write anything; the Config-tab selectors that
WRITE these settings are a separate app-surface build.

Split of concern:
  * WINDOW    -> the scene's design.width/height default (the geometry seam then
                 layers any live-resize drag on top; [CXC-ICP-S1-RESIZE]).
  * FONT_SCALE -> comp.font_scale, applied to the canvas at start via the S2
                 set_font chokepoint, AND the console_theme font-scale token.
  * THEME     -> comp.theme (so widget theme_from_ctx adopts it) + set_theme.
  * THEME_CUSTOM -> console_theme.apply_custom_colors (bg/accent overlay), plus
                 the accent-colored chrome LITERAL (the send button) re-pointed
                 by id so David's send-button example follows the custom accent.

Kept PURE where possible (apply_personalization / apply_to_composition take
plain data + the parsed composition) so the whole read+apply is unit-testable
with no window and no bus.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

# Foundation A read seam.  config_persister lives in compose_projects/intelligence/publisher; add
# it to the path the same way console_supervisor does, then import the pure fn.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PUBLISHER = os.path.join(_HERE, os.pardir, "publisher")
if _PUBLISHER not in sys.path:
    sys.path.insert(0, _PUBLISHER)

from config_persister import console_settings  # noqa: E402

from composeio.render.widgets import console_theme as ct  # noqa: E402


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def read_settings(meta_path: str) -> Dict[str, Any]:
    """Foundation A read: the full personalization object for the console at
    *meta_path* (defaults-filled; a missing/corrupt file yields the coded
    defaults, never raises)."""
    meta: Any = {}
    try:
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        meta = {}
    return console_settings(meta if isinstance(meta, dict) else {})


def meta_path_for(intelligence_id: str,
                  repo_root: Optional[str] = None) -> str:
    """The per-<id> meta-object path Foundation A stores into.  A launcher may
    override via COMPOSEIO_CONSOLE_META (or the geometry-store env, which the
    launcher already points at the same file)."""
    env = (os.environ.get("COMPOSEIO_CONSOLE_META")
           or os.environ.get("COMPOSEIO_GEOMETRY_STORE"))
    if env:
        return env
    root = repo_root or os.environ.get("COMPOSEIO_REPO_ROOT") or os.getcwd()
    return os.path.join(root, "compose_projects", "intelligence", "meta",
                        f"{intelligence_id}.meta.json")


# ---------------------------------------------------------------------------
# Apply — theme engine (console_theme)
# ---------------------------------------------------------------------------

def apply_personalization(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Apply theme + custom colors + font-scale to the console_theme engine and
    return the RESOLVED values ({theme, font_scale, theme_custom, window}) — the
    single point that skins the process before the first frame.  Unknown theme
    falls back to 'dark' ([CXC-ICP-S3-THEME] registry fallback)."""
    theme = str(settings.get("theme", "dark") or "dark").lower()
    if theme not in ct.list_themes():
        theme = "dark"
    ct.set_theme(theme)
    custom = settings.get("theme_custom") or {}
    ct.apply_custom_colors(custom)
    font_scale = ct.set_font_scale(settings.get("font_scale", 1.0))
    # scroll_direction ([CXC-ICP-S6-SCROLLDIR]): resolve to the accepted set;
    # anything malformed falls back to "natural" (respect the OS delta).
    scroll_direction = str(settings.get("scroll_direction", "natural")
                           or "natural").strip().lower()
    if scroll_direction not in ("natural", "traditional"):
        scroll_direction = "natural"
    win = settings.get("window") or {}
    return {
        "theme": theme,
        "font_scale": font_scale,
        "theme_custom": ct.normalize_custom(custom),
        "scroll_direction": scroll_direction,
        "window": {"w": int(win.get("w", 1280)),
                   "h": int(win.get("h", 1740))},
    }


# ---------------------------------------------------------------------------
# Apply — the parsed composition (window / font_scale / theme / send button)
# ---------------------------------------------------------------------------

def _walk(widget: Any) -> List[Any]:
    out = [widget]
    for ch in getattr(widget, "children", []) or []:
        out.extend(_walk(ch))
    return out


def _find(root: Any, widget_id: str) -> Optional[Any]:
    for w in _walk(root):
        if getattr(w, "widget_id", None) == widget_id:
            return w
    return None


# accent-colored chrome LITERALS in the scene (not token-driven): re-pointed to
# the custom accent so the send button follows David's example.  (bg/accent
# token consumers — the account bars, cards, gauge — follow automatically.)
#
# [CXC-FC-UNIT] MATCHED BY SUFFIX, NOT BY EXACT ID.  This was the literal
# `("composer_send",)`, which is a per-console name for a per-INSTANCE widget: the
# console now instantiates one `intelligence_pane` per declared Intelligence, so the
# send buttons are `pane1_<id>_send`, `pane2_<id>_send`, ...  An exact-id match would
# have re-pointed NOTHING — silently, because "no widget matched" and "the widget
# matched and was already the right colour" look identical from here.  A suffix match
# reaches every pane's button and keeps working when a third is declared.
_ACCENT_CHROME_ID_SUFFIXES = ("composer_send", "_send")


def _is_accent_chrome(widget_id: str) -> bool:
    """True for a scene widget whose accent chrome follows a custom accent."""
    wid = str(widget_id or "")
    return any(wid == s or wid.endswith(s) for s in _ACCENT_CHROME_ID_SUFFIXES)


def apply_to_composition(comp: Any, settings: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the personalization to a PARSED composition before start:
    seed the default window size, font-scale, theme (so widget theme_from_ctx
    adopts it), and re-point the accent-colored chrome literals to a custom
    accent.  Returns the resolved values for logging/tests."""
    resolved = apply_personalization(settings)

    # WINDOW ([CXC-ICP-S1-RESIZE]): the launch DEFAULT size.  The geometry seam
    # (design.persist_geometry) still wins for a size the user dragged to —
    # restore-at-launch precedence, resolve_geometry(persisted-wins).
    win = resolved["window"]
    if win["w"] > 0 and win["h"] > 0:
        comp.width = win["w"]
        comp.height = win["h"]

    # FONT SCALE ([CXC-ICP-S2-FONTSCALE]): comp.font_scale -> canvas at start.
    comp.font_scale = resolved["font_scale"]

    # SCROLL DIRECTION ([CXC-ICP-S6-SCROLLDIR]): comp.scroll_direction, read by
    # the runtime's wheel-scroll dispatch.  "natural" (default) passes the
    # OS-provided delta through; "traditional" flips the sign.
    comp.scroll_direction = resolved["scroll_direction"]

    # THEME ([CXC-ICP-S3-THEME]): drive comp.theme so every widget's
    # theme_from_ctx(ctx) re-adopts the SAME table (else the scene's design.theme
    # would clobber set_theme on the first draw).  The custom overlay is retained
    # across those set_theme calls by the holder.
    comp.theme = resolved["theme"]

    # ACCENT-colored chrome LITERALS (the send button): re-point to the ACTIVE
    # accent so BOTH a theme swap (high-contrast -> amber send) AND a custom
    # accent reach the scene literals that a token swap alone cannot — David's
    # send-button example, coherent for every theme.  (Token consumers — the
    # account bars, gauge, cards — already follow CONSOLE.active.accent.)
    active_accent = ct.CONSOLE.active.accent
    rgba = (active_accent[0], active_accent[1], active_accent[2],
            active_accent[3] if len(active_accent) > 3 else 255)
    # EVERY pane's send button, not one ([CXC-FC-UNIT]).  A per-instance surface needs a
    # per-instance walk; re-pointing only the first pane would leave the second wearing
    # the previous theme's accent, which is a two-Intelligence console that looks like a
    # rendering bug.
    root = getattr(comp, "root", None)
    if root is not None and hasattr(root, "iter_widgets"):
        for w in root.iter_widgets():
            if _is_accent_chrome(getattr(w, "widget_id", "")) and hasattr(w, "background"):
                w.background = rgba

    # APP-LEVEL surfaces ([CXC-ICP-S4-WIDGETSEL] / [CXC-ICP-S5-DASHBOARD]): the
    # persisted tabs selection + dashboard placements are READ here at launch and
    # applied to the parsed tree (modulo restart).  Both are no-ops when the
    # scene declares neither surface, so the generic path calls them always.
    apply_collaboration_selection(comp, settings)
    apply_dashboard(comp, settings)
    return resolved


# ---------------------------------------------------------------------------
# Apply — app-level surfaces (S4 Collaboration selection, S5 Dashboard)
# ---------------------------------------------------------------------------

def _find_by_type(root: Any, widget_type: str) -> List[Any]:
    """Every widget in the tree whose ``widget_type`` matches."""
    return [w for w in _walk(root)
            if getattr(w, "widget_type", None) == widget_type]


def _find_by_id(root: Any, widget_id: str) -> Optional[Any]:
    for w in _walk(root):
        if getattr(w, "widget_id", None) == widget_id:
            return w
    return None


# The rail is the compact-card region catalog widgets mount INTO
# ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED]); its scene id + width.
DEFAULT_RAIL_CONTAINER_ID = "rail_col"
DEFAULT_RAIL_WIDTH = 332.0


def _collaboration_added(tabs: Any, category: str = "collaboration"
                         ) -> List[str]:
    """The EXPLICIT rail-add list — ``console.tabs.<category>.added`` — the
    types the user genuinely ADDED to the rail ([CXC-ICP-S4-DEFAULT-RAIL]).
    Distinct from the show/hide roster (``widgets``/``hidden``), which is the
    whole category shown-by-default and is CURATE-ONLY.  Tolerates the bare or
    ``{category:{...}}`` shapes; ``[]`` when absent/malformed (mount nothing)."""
    if not isinstance(tabs, dict):
        return []
    blk = tabs.get(category)
    if not isinstance(blk, dict):
        return []
    return [str(x) for x in blk.get("added", []) if isinstance(x, str)]


def apply_collaboration_selection(comp: Any, settings: Dict[str, Any], *,
                                  mount_container_id: str =
                                  DEFAULT_RAIL_CONTAINER_ID) -> int:
    """S4 READ side ([CXC-ICP-S4-WIDGETSEL] + [CXC-ICP-S4-DEFAULT-RAIL]): apply
    the persisted ``console.tabs.collaboration`` selection to the rail.

    Two DISTINCT seams, deliberately not conflated ([CXC-ICP-S4-DEFAULT-RAIL]):

      * ``widgets``/``hidden`` — the CURATE roster.  It hides + reorders the
        collaboration widgets ALREADY placed in the rail.  It is category-scoped
        (every registered ``collaboration`` type is a row, all shown by
        default), so "shown in the roster" is NOT "mount into the rail"; the
        reorder is SUBSET-scoped to the collaboration widgets among their own
        slots so a non-collaboration scene card keeps its absolute position and
        is never sunk.  The DEFAULT (all-shown roster, nothing hidden) is a
        no-op — the rail stays exactly the scene-declared cards.

      * ``added`` — the EXPLICIT rail-add list.  Only these types are
        INSTANTIATED (rail-fitting, not already placed, appended) — the config
        genuinely ADDs.  Empty/absent ``added`` mounts NOTHING.  The live half
        (hot-add on toggle) is ``hot_add_collaboration_widget`` below.

    A selection referencing a type no longer registered is SKIPPED, never a
    crash.  Returns the number of widgets whose visibility/order/membership
    changed (0 = default)."""
    root = getattr(comp, "root", None)
    if root is None:
        return 0
    tabs = settings.get("tabs") or {}
    from composeio.render.widgets.widget_select import (  # noqa: E402
        collaboration_selection)
    from composeio.render.engine.widget_registry import (  # noqa: E402
        canonical_key, get_descriptor)
    visible, hidden = collaboration_selection(tabs, "collaboration")
    added = _collaboration_added(tabs, "collaboration")
    if not visible and not hidden and not added:
        return 0

    def _canon(token: Any) -> Optional[str]:
        """Map any type token (a selection entry OR a mounted widget's
        ``widget_type``) to its registry descriptor TYPE, so the underscored
        constructor form (``self_ledger``) and the canonical registry key
        (``selfledger``) resolve to the SAME identity."""
        if token is None:
            return None
        d = get_descriptor(str(token))
        return d.type if d is not None else canonical_key(str(token))

    hide_set = {_canon(h) for h in hidden}
    order = {_canon(t): i for i, t in enumerate(visible)}

    def _ck(w: Any) -> Optional[str]:
        return _canon(getattr(w, "widget_type", None))
    changed = 0

    # [CXC-ICP-S4-DEFAULT-RAIL] MOUNT half (launch-time): instantiate ONLY the
    # EXPLICIT adds — never the shown roster.  Done FIRST so the curate/reorder
    # pass below positions the new widget among the collaboration slots.
    add_types = [t for t in added if _canon(t) not in hide_set]
    if add_types:
        container = _find_by_id(root, mount_container_id)
        if container is not None:
            changed += _mount_collaboration_widgets(
                comp, container, add_types, get_descriptor, _ck)

    # [CXC-ICP-S4-DEFAULT-RAIL] CURATE half: hide + SUBSET-scoped reorder of the
    # collaboration widgets ALREADY placed.  The reorder writes the sorted
    # collaboration widgets back into their OWN slot indices, so a
    # non-collaboration scene card (Intelligence/Account/Deliverables — not
    # collaboration-category, so not in `order`) keeps its absolute position and
    # is never displaced.  With the default all-shown roster this is a no-op.
    for parent in _walk(root):
        kids = getattr(parent, "children", None)
        if not isinstance(kids, list) or not kids:
            continue
        touched = [k for k in kids if _ck(k) in order or _ck(k) in hide_set]
        if not touched:
            continue
        for k in touched:
            want = _ck(k) not in hide_set
            if getattr(k, "visible", True) != want:
                k.visible = want
                changed += 1
        # reorder ONLY the collaboration widgets the selection references (the
        # `touched` set — visible-in-order + hidden), among THEIR OWN slots; a
        # hidden one sinks to the end of that subset.  Non-collaboration scene
        # cards keep their absolute positions and are never displaced.
        collab_slots = [i for i, k in enumerate(kids) if k in touched]
        if len(collab_slots) > 1:
            ordered = sorted((kids[i] for i in collab_slots),
                             key=lambda k: order.get(_ck(k), 10_000))
            if ordered != [kids[i] for i in collab_slots]:
                for slot, w in zip(collab_slots, ordered):
                    kids[slot] = w
                changed += 1
    return changed


def _mount_collaboration_widgets(comp: Any, container: Any,
                                 types: List[str], get_descriptor: Any,
                                 ck: Any) -> int:
    """Instantiate each rail-FITTING type in *types* into *container* via the
    registry (``resolve_class`` + ``required_namespaces`` — the
    ``apply_dashboard`` idiom), registering its subscriptions on ``comp`` for
    the launch-time bind ([CXC-ICP-LAUNCH-BIND], wired by ``start()``).  Appends
    (never displaces the scene baseline).  Skips a misfit / unregistered /
    already-present type.  Returns the number mounted."""
    kids = getattr(container, "children", None)
    if not isinstance(kids, list):
        return 0
    have = {ck(k) for k in kids}
    mounted = 0
    for typ in types:
        desc = get_descriptor(str(typ))
        if desc is None or desc.cls is None:
            continue
        if desc.type in have:
            continue
        # backstop the widget_select fit filter: never mount a rail-misfit.
        if not desc.fits_region("rail", DEFAULT_RAIL_WIDTH, None):
            continue
        wid = f"{getattr(container, 'widget_id', 'rail')}_add_{desc.type}"
        try:
            child = desc.cls(wid)
        except Exception:                        # noqa: BLE001
            continue
        child.interactive = getattr(child, "interactive", False)
        for ns in desc.required_namespaces:
            comp.subscriptions.append((ns, child))
        kids.append(child)
        have.add(desc.type)
        # keep an added child SHOWN under an active order/shown-set.
        if hasattr(container, "register_added_child"):
            container.register_added_child(child)
        mounted += 1
    return mounted


def hot_add_collaboration_widget(app: Any, wtype: str, *,
                                 container_id: str = DEFAULT_RAIL_CONTAINER_ID,
                                 rail_width: float = DEFAULT_RAIL_WIDTH
                                 ) -> Optional[Any]:
    """S4 hot-add (LIVE half, [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D3): mount a
    rail-FITTING catalog widget into the RUNNING rail via ``add_widget_live``
    (the W2 primitive), binding its subscriptions immediately so it lights up
    NOW, not on restart.  Returns the new widget, or ``None`` if the type is
    unregistered / a rail-misfit / the container isn't found / already present.

    Reuse-by-reference: ``RenderApp.add_widget_live`` owns the mount + delta
    re-bind; this only resolves the target container + the fit guard."""
    from composeio.render.engine.widget_registry import get_descriptor
    desc = get_descriptor(str(wtype))
    if desc is None or desc.cls is None:
        return None
    if not desc.fits_region("rail", rail_width, None):
        return None
    comp = getattr(app, "comp", None)
    root = getattr(comp, "root", None) if comp is not None else None
    if root is None:
        return None
    container = _find_by_id(root, container_id)
    if container is None:
        return None
    for k in getattr(container, "children", []) or []:
        if getattr(k, "widget_type", None) in (desc.type, wtype):
            return None                          # idempotent — already placed
    return app.add_widget_live(container, {"type": desc.type})


def apply_dashboard(comp: Any, settings: Dict[str, Any]) -> int:
    """S5 READ side ([CXC-ICP-S5-DASHBOARD]): overlay the persisted
    ``console.dashboard.placements`` onto every DashboardWidget in the tree and
    register the placed children's ``required_namespaces`` as engine
    subscriptions (the launch-time bind).  Returns the number of placements
    mounted.  A no-op when the scene declares no Dashboard."""
    root = getattr(comp, "root", None)
    if root is None:
        return 0
    dash = settings.get("dashboard") or {}
    placements = dash.get("placements") if isinstance(dash, dict) else None
    if not isinstance(placements, list):
        return 0
    mounted = 0
    for w in _find_by_type(root, "dashboard"):
        w.mount_placements(placements)
        for ns, child in getattr(w, "child_subs", []):
            comp.subscriptions.append((ns, child))
        mounted += len(getattr(w, "children", []))
    return mounted


def apply_to_app(app: Any) -> Optional[Dict[str, Any]]:
    """Read Foundation A for the app's declared personalization id
    (design.personalization) and apply it to the app's composition.  A no-op
    (returns None) for a scene that declares no personalization id — so the
    generic runtime can call this unconditionally."""
    comp = getattr(app, "comp", None)
    if comp is None:
        return None
    pid = str(getattr(comp, "personalization", "") or "").strip()
    if not pid:
        return None
    settings = read_settings(meta_path_for(pid))
    return apply_to_composition(comp, settings)


if __name__ == "__main__":
    # Diagnostic: print the resolved personalization for an id.
    _id = sys.argv[1] if len(sys.argv) > 1 else "architect"
    _s = read_settings(meta_path_for(_id))
    print(json.dumps({"read": _s, "resolved": apply_personalization(_s)},
                     indent=2))
