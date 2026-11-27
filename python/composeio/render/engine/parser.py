"""
ComposeUX .compose.yaml -> Widget Tree Parser (M1)

Spec: [CXC-R-ENGINE] "Config parser (.compose.yaml -> widget tree)".

Two schema dialects are accepted:

1. NESTED (the render-engine spec dialect, [CXC-R-WIDGET-BUS] /
   [CXC-R-WIDGET-CONTAINER]): a top-level `widgets:` list with container
   types `stack` (flex), `grid`, and `pixel_stack`, nesting via
   `children:`.  This is the dialect the pixel-stack composite is
   authored in.

2. PRODUCTION (the existing ComposeUX dashboard dialect used by
   apps/*.compose.yaml and ux_composer): `modules:` placed on an outer
   grid via grid_row/grid_column, each module holding widgets on an
   inner (row, column) grid.  As of the M2 parity pass the full
   production widget set renders for real (Button, Relay, Command,
   TextInput, Checkbox, PowerToggle, DocPicker, BusMonitor, Tab,
   Coordinate, Image, Callout + the Phase-1 primitives), and spec-14
   `components:`/`use:` stamping is expanded at parse time.
   PlaceholderWidget remains only for genuinely unknown types.

Widget bindings:
  namespace:          subscribe — widget.on_bus_state fires on change
  publish_namespace:  tap publishes publish_value (or toggles binary
                      when publish_value == "toggle")

[SPEC:CXC-BIND] declarative layer (M-BIND pass):
  variables:          document/widget-scoped $name blocks, parse-time
                      substitution ([CXC-BIND-VARS])
  attribute bindings: visible/opacity/rotation/scale/color/value attrs
                      take literal | namespace | {namespace, when,
                      invert, map, default} ([CXC-BIND-ATTR])
  behaviors:          trigger -> engine-owned temporal behavior
                      ([CXC-BIND-BEHAVIOR], engine/behaviors.py)
  no inline code:     code-shaped fields are stripped, never executed
                      ([CXC-BIND-BOUNDARY])
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

from ..widget import Widget
from ..widgets import (ActivityWidget, AvatarWidget, BusMonitorWidget,
                       ButtonWidget, EmitButtonWidget, CalloutWidget,
                       ChatThreadWidget, SubagentLedgerWidget,
                       CheckboxWidget, CircleWidget, CommandWidget,
                       BootstrapManifestWidget, AgentsSkillsWidget,
                       ConfigSettingsWidget, DocBrowserWidget,
                       DeliverableCardWidget,
                       LinkHealthWidget,
                       CoordinateWidget, DialogWidget, DocPickerWidget,
                       StoreBrowserWidget,
                       IntelligenceCardWidget, AccountCardWidget,
                       ModelPickerWidget,
                       CarouselWidget,
                       GaugeWidget, HookupWidget, ImageWidget, LabelWidget,
                       LEDWidget,
                       LogicWidget, LottieWidget, MarkdownViewerWidget,
                       MediaWidget,
                       MermaidWidget, PlaceholderWidget,
                       PowerToggleWidget, RectangleWidget,
                       RegisteredChecklistWidget, NightWatchChecklistWidget,
                       NightWatchArtifactsWidget, DecisionsGraphWidget,
                       DecisionsTabWidget, DecisionsSummaryWidget,
                       GraphViewerWidget,
                       DefectsSummaryWidget,
                       LtsEngineRailWidget,
                       RadioButtonGroupWidget, RelayWidget,
                       SegmentedTabRowWidget, SevenSegmentDisplay,
                       SelfLedgerWidget, SvgWidget, LaunchTileWidget,
                       TabWidget,
                       TextInputWidget, ThemeToggleWidget, TouchVizWidget,
                       # [CXC-ICP-FOUND-B] additional classes referenced by the
                       # widget registry (previously imported lazily per-branch;
                       # all live in the widgets package __init__, no cycle).
                       ConsoleTabWidget, MasterDetailWidget,
                       DeliverableAlertWidget, DeliverableHistoryWidget,
                       ProjectsViewWidget, AwayWatchViewWidget,
                       DsdFormWidget,
                       # [SPEC:CXC-CONSOLE-PROD] Config selectors + S4/S5
                       ConsolePrefsWidget, WidgetSelectWidget, DashboardWidget)
from .behaviors import BehaviorDecl, parse_behaviors
from .states import (StateMachine, parse_gestures, parse_state_machine)
from .bindings import (AttrBinding, OutputDecl, STACK_GEOMETRY_ATTRS,
                       resolve_doc_variables,
                       extract_bindings, parse_outputs, strip_code_fields,
                       substitute_variables)
from .component_imports import resolve_component_imports
from .components import expand_components
from .instances import seed_config_variables
from .drag_value import attach_drag_value
from .hookup import Hookup, HookupError, parse_hookup
from .hookup_seam import seam_errors
from .relay_actuator import RelayActuator, parse_relay_actuator
from .layout import (Container, FlexContainer, GridContainer,
                     OrderedFlowContainer, PixelStackContainer)
from .widget_registry import (WidgetDescriptor, canonical_key, get_descriptor,
                              register)

_BG = (0x1E, 0x1E, 0x2E)
_PANEL_BG = (0x28, 0x28, 0x3C)
_PANEL_BORDER = (0x3A, 0x3A, 0x50)
_TEAL = (0x26, 0xA6, 0x9A)


@dataclass
class Composition:
    """Parse result: the widget tree plus everything the runtime wires up."""
    root: Container
    title: str = "ComposeUX"
    width: int = 960
    height: int = 600
    theme: str = "dark"
    # [CXC-ICP-S2-FONTSCALE] scene-seedable font-scale multiplier (design.
    # font_scale, default 1.0); the runtime applies it to the canvas at start
    # (canvas.set_font_scale) so it threads through every text draw+measure.
    font_scale: float = 1.0
    # [CXC-ICP-S6-SCROLLDIR] scene/personalization scroll-direction preference
    # (design.scroll_direction, "natural"|"traditional", default "natural").
    # The runtime consults it in the wheel-scroll dispatch: "natural" passes the
    # OS-provided (direction-normalized) wheel delta straight through so
    # scrolling matches the user's OS setting out of the box; "traditional"
    # applies a sign flip on top for a user whose preference differs from their
    # OS.  Seeded from Foundation A's console.scroll_direction at launch.
    scroll_direction: str = "natural"
    # [SPEC:CXC-CONSOLE-VISUAL] [CXC-ICV-MOTION]: scene-level reduced-motion
    # flag (design.reduced_motion, default False) — plumbed onto the
    # EngineContext; clocked widgets consult it and render their REST frame.
    reduced_motion: bool = False
    # [CXC-DVL-WINDOW]/[CXC-DVL-GEOMETRY] standalone-window UX (design block):
    # a RESIZABLE window with a size floor whose geometry PERSISTS across
    # relaunch.  All default off/unset so every existing scene (the fixed-size
    # console, dashboards) is byte-identical — only a scene that opts in
    # (the Decisions viewer) becomes resizable/persistent.
    resizable: bool = False
    min_width: int = 0
    min_height: int = 0
    persist_geometry: bool = False
    window_key: str = ""
    # [SPEC:CXC-CONSOLE-PROD] the personalization profile id (design.
    # personalization = the Intelligence <id>).  When set, the live runtime
    # reads Foundation A (meta.<id>.console) and applies window/font/theme at
    # launch via the console personalization glue (loaded by file path, like
    # data_sources — the render engine keeps no dependency on the intelligence
    # tree).  Empty -> no personalization (every non-console scene unchanged).
    personalization: str = ""
    # [CXC-IC-SELF-IDENTITY] the DECLARATIVE opt-in that makes a scene the HOST
    # of the in-process console data layer ([DEC-INPROCESS-SOURCES]).  When a
    # scene sets `design.data_sources: true`, a `--transports` launch also brings
    # up the session-owned publishers in-process (self_publisher, transcript_
    # publisher, the monitors + launchers).  Default False and FAIL-SAFE: a scene
    # that does NOT opt in — notably the Launch Bar ribbon, a pure tile scene —
    # NEVER co-hosts the data layer even when launched with `--transports` under
    # the same `<id>`, so it can never win the per-role singleton flocks and
    # become the authoritative host of the console's telemetry publishers.  Only
    # the console scene opts in.
    data_sources: bool = False
    # [CXC-IC-SURFACE-SINGLETON] the DECLARATIVE opt-in that makes a scene a host
    # singleton WITHOUT hosting the data layer: one window per `app: id:` per host,
    # held as the surface flock.  The Launch Bar ribbon sets it.  It is a console-stack
    # member, and the stack's idempotency guard asks the member's LOCK, never the
    # process table (DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E).  Default False:
    # viewers stay unconstrained, so several may run at once.
    surface_singleton: bool = False
    background: tuple = _BG
    # [CXC-ICV-CHROME] scene background pass (all optional):
    #   design.background: color         -> overrides `background`
    #   design.background_grid: {color, size}  -> faint cockpit grid
    #   design.background_wash: {color, cx, cy, r} (fractions of surface)
    #                                    -> soft radial wash
    background_grid: Optional[dict] = None
    background_wash: Optional[dict] = None
    namespaces: List[dict] = field(default_factory=list)     # config namespace defs
    subscriptions: List[Tuple[str, Widget]] = field(default_factory=list)
    # Declarative visible: bindings ([CXC-RM-M4] engine-addition #2):
    # (namespace, widget, when_value_or_None, invert).  Kept for design-time
    # introspection (Composer chrome); the runtime wires attr_bindings.
    visible_bindings: List[Tuple[str, Widget, Any, bool]] = field(
        default_factory=list)
    # [SPEC:CXC-BIND] generalized attribute bindings ([CXC-BIND-ATTR]) —
    # visible/opacity/rotation/scale/color/value attrs bound to namespaces.
    attr_bindings: List[AttrBinding] = field(default_factory=list)
    # [CXC-BIND-BEHAVIOR] parsed behaviors: entries, widget-attached.
    behaviors: List[BehaviorDecl] = field(default_factory=list)
    # [CXC-BIND-STATES] parsed state machines (states:/state:/transitions:),
    # widget-attached; the runtime wires the selector + ticks the StateEngine.
    state_machines: List[StateMachine] = field(default_factory=list)
    # [SPEC:CXC-HEADLESS] declared outputs (attr -> namespace publishes,
    # evaluated by RenderApp.tick) + the document's headless schedule
    # block ({schedule: {mode, interval_s, trigger}}, [CXC-HW-SCHEDULE]).
    outputs: List[OutputDecl] = field(default_factory=list)
    headless: Optional[dict] = None
    # [SPEC:CXC-HMON] resolved document-level variables: block, retained
    # for the internal-state publish ([CXC-HMON-STATE] "$var values").
    # Substitution stays parse-time ([CXC-BIND-VARS]); observability only.
    variables: Dict[str, Any] = field(default_factory=dict)
    transports: List[dict] = field(default_factory=list)
    asset_uris: List[str] = field(default_factory=list)
    app_id: str = "composeux-render"
    # [CXC-FC-UNIT] the DECLARED `(instance_id, role)` pairs this scene hosts — the SINGLE
    # source the data layer reads to start N channel bridges and N telemetry publisher sets
    # ([CXC-FC-S1-BRIDGES], [CXC-FC-S2-TELEMETRY]).  Empty for every scene that hosts no
    # Intelligence, which is every scene but the console today.
    instances: List[Dict[str, Any]] = field(default_factory=list)
    # [FLOW:MAKER-PROJECT] S2: the parsed + validated `hookup:` block (the
    # PHYSICAL half of a maker `.compose.yaml` — board, placed parts, netlist
    # wires + the pin->slot seam).  ``None`` for every non-maker scene, which
    # therefore parses byte-for-byte as before.  S3 reads ``comp.hookup``
    # whole; S4 reads ``comp.hookup.pin_slot_map()``.
    hookup: Optional["Hookup"] = None
    # [FLOW:MAKER-PROJECT] S6: the OUTPUT/actuator half of a maker scene — the
    # DY-SV5F audio cover's declarative config (subscribes + cue->track map + the
    # UART pin driving the module).  Purely additive: ``None`` for every scene
    # without an ``audio_cover:`` block, so all existing scenes parse byte-for-
    # byte as before.  S4 (mcu_config) reads this to emit the actuator block.
    audio_cover: Optional[dict] = None
    # [CXC-HAL-ACTUATE] the OUTPUT/actuator half of a headless RELAY device — the
    # break-before-make relay-bank actuator (subscribes one enum ns + a value->pin
    # state map + dead-time).  The direct dual of ``audio_cover:``.  Purely
    # additive: ``None`` for every scene without a ``relay_actuator:`` block.  The
    # on-device dual is RelayActuatorSpec in reactive_core/controller_config.cpp.
    relay_actuator: Optional["RelayActuator"] = None
    # [CXC-R-ENGINE-ASSETS] the directory of the .compose.yaml this Composition
    # was parsed FROM (set by parse_compose_yaml).  The AssetManager resolves a
    # RELATIVE ``file://``/bare-path asset URI against this dir FIRST, so a
    # scene's bundled art loads regardless of the launch cwd (a demo run from
    # the repo root resolves ``file://assets/x.svg`` scene-relative, then falls
    # back to the cwd).  Empty ("") for an in-memory parse (from_dict) — the
    # legacy cwd-relative behavior, so nothing changes when it is unset.
    scene_dir: str = ""


# ---------------------------------------------------------------------------
# Leaf widget factory
# ---------------------------------------------------------------------------

_uid_counter = [0]


def _uid(prefix: str) -> str:
    _uid_counter[0] += 1
    return f"{prefix}_{_uid_counter[0]}"


# Color spec parsing lives in engine/bindings.py (shared with binding
# coercion + behavior params); the local names are kept as aliases.
from .bindings import color_tuple as _color_tuple  # noqa: E402
from .bindings import rgba as _rgba  # noqa: E402


# [SPEC:CXC-LOTTIE]: lottie-web style camelCase transport names normalize
# to the widget's snake_case bindable attributes BEFORE binding extraction.
_LOTTIE_ALIASES = {
    "setSpeed": "speed", "set_speed": "speed",
    "goToAndStop": "go_to_and_stop", "gotoandstop": "go_to_and_stop",
    "playSegments": "play_segments", "segments": "play_segments",
}


def build_leaf(spec: dict, comp: Composition) -> Widget:
    """Instantiate a leaf widget from a config dict (either dialect).

    [CXC-ICP-FOUND-B]: the widget type is resolved through ``WIDGET_REGISTRY``
    (the single, enumerable type->class map).  When a descriptor carries a
    ``build`` hook (the S6 pluggable seam) it CONSTRUCTS through it; the shipped
    widgets keep their audited bespoke branch below (``build=None``).  An
    unknown type still degrades to ``PlaceholderWidget`` (shipped behavior)."""
    wtype = str(spec.get("type", "unknown"))
    wid = spec.get("id") or _uid(wtype.lower())
    key = canonical_key(wtype)

    if key == "lottie":
        for alias, canonical in _LOTTIE_ALIASES.items():
            if alias in spec and canonical not in spec:
                spec[canonical] = spec.pop(alias)

    # [SPEC:CXC-BIND]: no inline code ever reaches a constructor, and
    # namespace-bound attributes are lifted out BEFORE construction so
    # constructors only ever see literals ([CXC-BIND-ATTR]).
    strip_code_fields(spec, wid)
    # [CXC-BIND-STATES]/[CXC-RM-GESTURE]: lift states/state/transitions +
    # gestures BEFORE binding extraction (so state: is a selector, not a
    # value binding) and BEFORE construction (so they never hit a kwarg).
    state_spec, gestures = _pop_state_gesture(spec)
    pending_bindings = extract_bindings(spec, wid)

    # [CXC-ICP-FOUND-B] pluggable-construction hook: a registered descriptor
    # with a ``build`` callable is constructed THROUGH the registry (a NEW
    # widget = its module + one descriptor, no edit here).  The shipped types
    # fall through to their bespoke branch (their descriptor's ``build`` is
    # None), which stays the audited source of truth for their wiring.
    _desc = get_descriptor(key)
    if _desc is not None and _desc.build is not None:
        w = _desc.build(spec, wid, comp)
        _wire_common(w, spec, comp, pending_bindings,
                     state_spec=state_spec, gestures=gestures)
        return w

    if key == "gauge":
        w: Widget = GaugeWidget(
            wid,
            min_val=float(spec.get("min_val", spec.get("min", 0))),
            max_val=float(spec.get("max_val", spec.get("max", 100))),
            value=float(spec.get("value", 0)),
            unit=str(spec.get("unit", "")),
            label=str(spec.get("label", "")),
            warning_threshold=spec.get("warning_threshold"),
            danger_threshold=spec.get("danger_threshold"),
            # [CXC-ICV-GAUGE] style MODE on the one GaugeWidget (architect
            # ratification: generalize, don't fork): "semicircle" selects
            # the console context gauge; "bar" the linear fill/fader
            # ([CXC-PRIM-CATALOG]); absent/"dial" keeps the 270° dial.
            style=str(spec.get("style", "dial")),
            # bar-mode fill axis/origin: "vertical" (default) bottom->top,
            # "horizontal" left->right.  Inert for dial/semicircle.
            orientation=str(spec.get("orientation", "vertical")),
            mid_threshold=spec.get("mid_threshold"),
            ns_tag=str(spec.get("ns_tag", "")),
            # led_bar-mode ([CXC-PRIM-CATALOG]): LM3914 segmented level meter.
            # Inert for dial/semicircle/bar.
            segments=int(spec.get("segments", 10)),
            segment_colors=spec.get("segment_colors"),
            reverse=bool(spec.get("reverse", False)),
            # [CXC-PRIM-LEDBAR] led_bar geometry (David 2026-07-29): element
            # shape, the rectangular long-axis, the bezel padding + inter-LED
            # gap (were hardcoded), and the short-axis floor.  Defaults keep the
            # render byte-identical.  Inert for dial/semicircle/bar.
            segment_shape=str(spec.get("segment_shape", "rectangular")),
            segment_long_axis=str(spec.get("segment_long_axis", "across")),
            segment_gap=float(spec.get("segment_gap", 4.0)),
            padding=float(spec.get("padding", 8.0)),
            min_segment_size=float(spec.get("min_segment_size", 0.0)))
    elif key == "led":
        w = LEDWidget(wid, state=str(spec.get("state", "off")),
                      color=_color_tuple(spec.get("color")),
                      label=str(spec.get("label", "")))
    elif key == "relay":
        w = RelayWidget(wid, label=str(spec.get("label", "")),
                        state_engine=spec.get("state_engine", "binary"))
    elif key == "button":
        w = ButtonWidget(wid, label=str(spec.get("label", "Button")),
                         state_engine=spec.get("state_engine", "binary"),
                         led_behavior=str(spec.get("led_behavior",
                                                   "follow_state")),
                         namespace=str(spec.get("namespace", "")))
    elif key == "emit_button":
        w = EmitButtonWidget(wid, label=str(spec.get("label", "Emit")),
                             emits=spec.get("emits"),
                             chrome=str(spec.get("chrome", "")))
        # An emit VALUE may embed declarative bindings ({namespace, default});
        # subscribe each so the payload interpolates the LIVE value at publish
        # time ([SPEC:CXC-GRAPH-VIEWER] [CXC-GV-SCOPE] — how "View Decisions"
        # carries THIS watch run's session ids).  Control-only: the button
        # publishes, it never renders these.
        for _vns in getattr(w, "value_namespaces", []):
            comp.subscriptions.append((_vns, w))
    elif key == "command":
        w = CommandWidget(wid, label=str(spec.get("label", "")),
                          binding=spec.get("binding"))
    elif key in ("textinput", "text_input"):
        w = TextInputWidget(wid, label=str(spec.get("label", "")),
                            placeholder=str(spec.get("placeholder", "")),
                            secret=bool(spec.get("secret", False)),
                            namespace=str(spec.get("namespace", "")),
                            # console composer mode (all opt-in; defaults
                            # leave every existing scene untouched)
                            chrome=str(spec.get("chrome", "")),
                            clear_on_commit=bool(
                                spec.get("clear_on_commit", False)),
                            commit_namespace=str(
                                spec.get("commit_namespace", "")),
                            # console chrome: visible soft-wrap line cap
                            # (grow min_lines..max_lines, then scroll)
                            max_lines=int(spec.get("max_lines", 3)),
                            # resting/minimum visible rows (default 1 = grow
                            # from one line; the console composer sets 3)
                            min_lines=int(spec.get("min_lines", 1)))
        if w.commit_namespace:
            # commit TRIGGER topic: any publish commits like Enter — the
            # SEND button's publish_namespace tap binding lands here
            # (cross-widget send via the bus, no engine plumbing).
            comp.subscriptions.append((w.commit_namespace, w))
        if w.clear_on_commit and w.bound_namespace:
            # SEND-ONLY console composer ([CXC-ICV-CHROME]): the entry clears
            # on send and never restores from the bus, so it must NOT
            # subscribe its own outbound `namespace:` for reflection.  That
            # self-subscription is what (a) forced the echo-suppression hack
            # and (b) gave the outbound topic a LOCAL subscriber, which made
            # _resolve_scope downgrade the publish to local and strand the
            # message in-process — it never reached the global MQTT leg the
            # channel_bridge reads.  Drop it so the topic auto-resolves global
            # (belt-and-suspenders with a scene `scope: global` declaration).
            spec.pop("namespace", None)
    elif key == "checkbox":
        w = CheckboxWidget(wid, label=str(spec.get("label", "")),
                           on_value=str(spec.get("on_value", "on")),
                           off_value=str(spec.get("off_value", "off")),
                           namespace=str(spec.get("namespace", "")))
    elif key in ("powertoggle", "power_toggle"):
        w = PowerToggleWidget(
            wid, label=str(spec.get("label", "Power")),
            plug_namespace=str(spec.get("plug_namespace", "bringup/plug")),
            on_tool=str(spec.get("on_tool", "fleetpower.power_on")),
            off_tool=str(spec.get("off_tool", "fleetpower.power_off")),
            status_tool=str(spec.get("status_tool", "fleetpower.power_status")),
            confirm_off=bool(spec.get("confirm_off", True)),
            trigger_namespace=str(spec.get("trigger_namespace", "")),
            trigger_value=spec.get("trigger_value"),
            ha_entity=str(spec.get("ha_entity", "")),
            toggle_tool=str(spec.get("toggle_tool", "homeassistant.toggle")))
        comp.subscriptions.append((w.plug_namespace, w))
        if w.trigger_namespace:               # semantic trigger subscription
            comp.subscriptions.append((w.trigger_namespace, w))
    elif key in ("docpicker", "doc_picker"):
        w = DocPickerWidget(
            wid, label=str(spec.get("label", "Restore from saved")),
            collection=str(spec.get("collection", "fleet_devices")),
            where=spec.get("where"),
            title_field=str(spec.get("title_field", "device")),
            subtitle_field=str(spec.get("subtitle_field", "role")),
            load_map=spec.get("load_map"),
            select_action=str(spec.get("select_action", "load")))
    elif key in ("storebrowser", "store_browser"):
        # [CXC-DOCNAV-APP] / [CXC-BUSNAV-APP] the storage-viewer navigation
        # SHELL: list a store's tree/entries and, on selecting a leaf, hand the
        # entry to the EXISTING doc-viewer by publishing open_namespace
        # (`_app/doc/open`).  It adds no renderer; it PUBLISHES the open intent
        # (no bus subscription of its own).
        from ..widgets.store_browser import (
            StoreBrowserWidget, DEFAULT_OPEN_NAMESPACE as _SB_OPEN)
        roots = spec.get("roots")
        w = StoreBrowserWidget(
            wid, store=str(spec.get("store", "docstore")),
            open_namespace=str(spec.get("open_namespace") or _SB_OPEN),
            title=str(spec.get("title", "")),
            host=(str(spec["host"]) if spec.get("host") else None),  # None: the process storage URL (H1)
            port=int(spec.get("port", 0) or 0),
            roots=[str(r) for r in roots] if isinstance(roots, list) else None,
            default_path=str(spec.get("default_path", "")))
    elif key in ("busmonitor", "bus_monitor"):
        w = BusMonitorWidget(
            wid, label=str(spec.get("label", "Bus Monitor")),
            max_lines=int(spec.get("max_lines", 150)),
            show_heartbeats=bool(spec.get("show_heartbeats", False)))
    elif key in ("chatthread", "chat_thread"):
        # [SPEC:CXC-IC] four-voice conversation surface.  Subscribes a LIST
        # of namespaces; voice is derived from (topic, from-field), so each
        # subscribed topic must fire on_bus_state — append them here (the
        # PowerToggle/Coordinate multi-subscription idiom).
        from ..widgets.chat_thread import DEFAULT_NAMESPACES
        ns_list = spec.get("namespaces") or list(DEFAULT_NAMESPACES)
        ns_list = [str(n) for n in ns_list]
        w = ChatThreadWidget(
            wid, label=str(spec.get("label", "Intelligence Console")),
            namespaces=ns_list,
            max_messages=int(spec.get("max_messages",
                                      spec.get("max_lines", 200))),
            font_size=float(spec.get("font_size", 12.0)),
            accent=_color_tuple(spec.get("accent"), (0x4E, 0xA8, 0xFF)),
            dim=_color_tuple(spec.get("dim"), (0x6D, 0x7C, 0x92)),
            harness=_color_tuple(spec.get("harness"), (0xF5, 0xA6, 0x23)),
            neutral=_color_tuple(spec.get("neutral"), (0xE0, 0xE4, 0xEC)))
        for _ns in ns_list:
            comp.subscriptions.append((_ns, w))
    elif key == "activity":
        # [SPEC:CXC-IC] "what's it doing now" panel.  Subscribes ONE namespace
        # (self/activity); rows keyed by id, start->done updates in place.
        from ..widgets.activity import DEFAULT_NAMESPACE as _ACT_NS
        ns = str(spec.get("namespace") or _ACT_NS)
        w = ActivityWidget(
            wid, label=str(spec.get("label", "Activity")),
            namespace=ns,
            max_lines=int(spec.get("max_lines", 6)),
            font_size=float(spec.get("font_size", 12.0)),
            accent=_color_tuple(spec.get("accent"), (0x4E, 0xA8, 0xFF)),
            dim=_color_tuple(spec.get("dim"), (0x6D, 0x7C, 0x92)),
            ok=_color_tuple(spec.get("ok", spec.get("done")), (0x2F, 0xD0, 0x7B)),
            background=_color_tuple(spec.get("background"), (0x0D, 0x12, 0x1B)),
            border=_color_tuple(spec.get("border"), (0x1B, 0x24, 0x32)),
            corner_radius=float(spec.get("corner_radius", 6.0)))
        comp.subscriptions.append((ns, w))
    elif key in ("subagentledger", "subagent_ledger"):
        # [SPEC:CXC-IC] the animating dispatch ledger.  Subscribes ONE
        # namespace (self/subagents); rows keyed by id, dispatched->done
        # updates the SAME row in place.
        from ..widgets.subagent_ledger import DEFAULT_NAMESPACE as _SUB_NS
        ns = str(spec.get("namespace") or _SUB_NS)
        w = SubagentLedgerWidget(
            wid, label=str(spec.get("label", "Subagents")),
            namespace=ns,
            max_rows=int(spec.get("max_rows", spec.get("max_lines", 8))),
            font_size=float(spec.get("font_size", 12.0)),
            accent=_color_tuple(spec.get("accent"), (0x4E, 0xA8, 0xFF)),
            dim=_color_tuple(spec.get("dim"), (0x6D, 0x7C, 0x92)),
            ok=_color_tuple(spec.get("ok", spec.get("done")), (0x2F, 0xD0, 0x7B)),
            background=_color_tuple(spec.get("background"), (0x0D, 0x12, 0x1B)),
            border=_color_tuple(spec.get("border"), (0x1B, 0x24, 0x32)),
            corner_radius=float(spec.get("corner_radius", 6.0)))
        comp.subscriptions.append((ns, w))
    elif key in ("registeredchecklist", "registered_checklist"):
        # [CXC-ICV-REGISTERED] the self/registered checklist.  ONE namespace;
        # payload is the manifest-driven per-artifact report ([CXC-ICD-D8]).
        from ..widgets.registered_checklist import DEFAULT_NAMESPACE as _REG_NS
        ns = str(spec.get("namespace") or _REG_NS)
        w = RegisteredChecklistWidget(
            wid, label=str(spec.get("label", "self / registered")),
            namespace=ns,
            font_size=float(spec.get("font_size", 10.0)))
        comp.subscriptions.append((ns, w))
    elif key in ("nightwatchchecklist", "nightwatch_checklist",
                 "task_checklist", "taskchecklist"):
        # the Away-Watch session task checklist (Night Watch window): a
        # data-driven done/partial/deferred list.  ONE subscribed namespace
        # (default fun/nightwatch/tasks); a static `tasks:` list in the spec is
        # the render default (like a label's text default), so the window draws
        # standalone and a publisher can override on the bus.
        from ..widgets.nightwatch_checklist import DEFAULT_NAMESPACE as _NWC_NS
        ns = str(spec.get("namespace") or _NWC_NS)
        w = NightWatchChecklistWidget(
            wid, namespace=ns, tasks=spec.get("tasks"),
            font_size=float(spec.get("font_size", 12.5)))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("nightwatchartifacts", "nightwatch_artifacts"):
        # the Night Watch ARTIFACTS tab: a data-driven list of the watch's
        # artifacts:[{title,path}].  ONE subscribed namespace (default
        # fun/nightwatch/artifacts); a static `artifacts:` list is the render
        # default (like the checklist's tasks:).  Tapping a row publishes the
        # doc-open intent (_app/doc/open = the path) the rest of the app uses.
        from ..widgets.nightwatch_artifacts import (
            DEFAULT_NAMESPACE as _NWA_NS,
            DEFAULT_OPEN_NAMESPACE as _NWA_OPEN)
        ns = str(spec.get("namespace") or _NWA_NS)
        w = NightWatchArtifactsWidget(
            wid, namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _NWA_OPEN),
            artifacts=spec.get("artifacts"))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("decisionsgraph", "decisions_graph"):
        # [SPEC:CXC-DECISIONS-TAB] the Night Watch DECISIONS tab: a first-cut
        # query-neighborhood viz.  ONE subscribed namespace (default
        # self/decisions — the decisions_publisher MAGMA projection); a static
        # `nodes:`/`edges:` projection is the render default so the window draws
        # a real neighborhood standalone.  Tapping a node publishes the
        # re-anchor intent (_app/decisions/anchor).
        from ..widgets.decisions_graph import (
            DEFAULT_NAMESPACE as _DG_NS,
            DEFAULT_ANCHOR_NAMESPACE as _DG_ANCHOR)
        ns = str(spec.get("namespace") or _DG_NS)
        w = DecisionsGraphWidget(
            wid, namespace=ns,
            anchor_namespace=str(spec.get("anchor_namespace") or _DG_ANCHOR),
            nodes=spec.get("nodes"), edges=spec.get("edges"),
            anchor=str(spec.get("anchor", "")),
            corpus=str(spec.get("corpus", "")))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("decisionstab", "decisions_tab"):
        # [SPEC:CXC-DECISIONS-TAB] the Intelligence Console DECISIONS tab: the
        # full query-driven graph explorer (query pane + graph pane + curation
        # drawer).  ONE subscribed namespace (default self/decisions — the
        # decisions_publisher projection); a static `nodes:`/`edges:` projection
        # is the render default so the tab draws a real neighborhood standalone.
        from ..widgets.decisions_tab import (
            DEFAULT_NAMESPACE as _DT_NS,
            DEFAULT_ANCHOR_NAMESPACE as _DT_ANCHOR)
        ns = str(spec.get("namespace") or _DT_NS)
        w = DecisionsTabWidget(
            wid, namespace=ns,
            anchor_namespace=str(spec.get("anchor_namespace") or _DT_ANCHOR),
            selected_namespace=str(spec.get("selected_namespace")
                                   or "_app/decisions/selected"),
            curate_namespace=str(spec.get("curate_namespace")
                                 or "_app/decisions/curate"),
            open_namespace=str(spec.get("open_namespace") or "_app/doc/open"),
            nodes=spec.get("nodes"), edges=spec.get("edges"),
            anchor=str(spec.get("anchor", "")),
            corpus=str(spec.get("corpus", "")),
            layout_mode=str(spec.get("layout_mode", "dag")))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("graphviewer", "graph_viewer"):
        # [SPEC:CXC-GRAPH-VIEWER] the corpus-agnostic Graph Viewer: the Decisions
        # tab GENERALIZED — decisions are one corpus among many.  DATA is a DIRECT
        # in-process lts-engine QUERY ([CXC-GV-DATA-PATH]); there is NO
        # `self/decisions` data subscription (retired, [CXC-GV-EMITTER-RETIRED]).
        # Only the lightweight CONTROL intents stay on the bus ([CXC-GV-INTENTS]):
        # re-anchor (`_app/graph/anchor`), refresh (`_app/graph/refresh`), and the
        # OPEN intent (`_app/graph/open`) — which may carry a run SCOPE
        # ([CXC-GV-SCOPE]).  All scalars; never graph data.
        from ..widgets.decisions_tab import (
            GV_ANCHOR_NAMESPACE as _GV_ANCHOR,
            GV_REFRESH_NAMESPACE as _GV_REFRESH,
            GV_OPEN_NAMESPACE as _GV_OPEN,
            GV_DEFAULT_CORPUS as _GV_DEFAULT)
        anchor_ns = str(spec.get("anchor_namespace") or _GV_ANCHOR)
        refresh_ns = str(spec.get("refresh_namespace") or _GV_REFRESH)
        open_scope_ns = str(spec.get("open_scope_namespace") or _GV_OPEN)
        w = GraphViewerWidget(
            wid,
            corpus=str(spec.get("corpus", "")),
            default_corpus=str(spec.get("default_corpus") or _GV_DEFAULT),
            corpora=spec.get("corpora"),
            style_profiles_path=str(spec.get("style_profiles") or ""),
            anchor_namespace=anchor_ns,
            refresh_namespace=refresh_ns,
            open_scope_namespace=open_scope_ns,
            direct_query=bool(spec.get("direct_query", True)),
            selected_namespace=str(spec.get("selected_namespace")
                                   or "_app/graph/selected"),
            curate_namespace=str(spec.get("curate_namespace")
                                 or "_app/graph/curate"),
            open_namespace=str(spec.get("open_namespace") or "_app/doc/open"),
            nodes=spec.get("nodes"), edges=spec.get("edges"),
            layout_mode=str(spec.get("layout_mode", "dag")))
        # CONTROL-only subscriptions ([CXC-GV-INTENTS]) — NOT a data feed.
        comp.subscriptions.append((anchor_ns, w))
        comp.subscriptions.append((refresh_ns, w))
        comp.subscriptions.append((open_scope_ns, w))
        spec.pop("namespace", None)
    elif key in ("decisionssummary", "decisions_summary"):
        # [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D4 the COMPACT decisions rail
        # card (P4: compact view on the rail, full detail on the tab, SAME bus
        # state).  ONE subscribed namespace (default self/decisions — the
        # decisions_publisher projection); the Explore ▸ chip publishes
        # tab_namespace = jump_tab (the ConsoleTab switch seam).
        from ..widgets.decisions_summary import (
            DEFAULT_NAMESPACE as _DS_NS,
            DEFAULT_TAB_NAMESPACE as _DS_TAB,
            DEFAULT_JUMP_TAB as _DS_JUMP)
        ns = str(spec.get("namespace") or _DS_NS)
        w = DecisionsSummaryWidget(
            wid, namespace=ns,
            label=str(spec.get("label", "Decisions")),
            tab_namespace=str(spec.get("tab_namespace") or _DS_TAB),
            jump_tab=str(spec.get("jump_tab") or _DS_JUMP),
            chip_label=str(spec.get("chip_label", "Explore")),
            header_dot=bool(spec.get("header_dot", True)))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("defectssummary", "defects_summary"):
        # [SPEC:CIO-DEFECT-REGISTRY] the Dashboard Defects card — the Defect
        # Registry roll-up at a glance (total · root-cause gauge · recurrence rate
        # · fix-resistant leaders).  ONE subscribed namespace (default
        # self/architect/defects — the defect_metrics_publisher projection).
        from ..widgets.defects_summary import DEFAULT_NAMESPACE as _DF_NS
        ns = str(spec.get("namespace") or _DF_NS)
        w = DefectsSummaryWidget(
            wid, namespace=ns,
            label=str(spec.get("label", "Defects")),
            header_dot=bool(spec.get("header_dot", True)))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("ltsenginerail", "lts_engine_rail"):
        # [SPEC:CXC-DVL] the console launcher rail: lts-engine status/freshness/
        # metrics + an Open button that publishes the _app/decisions/open intent
        # the in-process decisions launcher consumes.  ONE subscribed namespace
        # (self/lts_engine/status, the lts_engine_status source's projection).
        from ..widgets.lts_engine_rail import (
            DEFAULT_NAMESPACE as _LTS_NS,
            DEFAULT_OPEN_NAMESPACE as _LTS_OPEN)
        ns = str(spec.get("namespace") or _LTS_NS)
        w = LtsEngineRailWidget(
            wid, namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _LTS_OPEN),
            label=str(spec.get("label", "LTS Engine")))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("selfledger", "self_ledger"):
        # [CXC-ICV-LEDGER] the self/* telemetry grid.  Subscribes a LIST of
        # ledger namespaces (the chat_thread namespaces: idiom).
        from ..widgets.self_ledger import DEFAULT_NAMESPACES as _LEDGER_NS
        ns_list = [str(n) for n in
                   (spec.get("namespaces") or list(_LEDGER_NS))]
        w = SelfLedgerWidget(
            wid, label=str(spec.get("label", "self / ledger")),
            ns_tag=str(spec.get("ns_tag", "self/*")),
            namespaces=ns_list,
            font_size=float(spec.get("font_size", 10.0)))
        for _ns in ns_list:
            comp.subscriptions.append((_ns, w))
    elif key in ("deliverablecard", "deliverable_card"):
        # [DEC-DELIVERABLE-HISTORY-WIDGET] the LOCKED compact Deliverables
        # rail card.  ONE subscribed namespace (self/deliverables — the
        # deliverable_publisher projection); row taps publish
        # open_namespace, the Artifacts ▸ chip publishes tab_namespace.
        from ..widgets.deliverable_card import (
            DEFAULT_NAMESPACE as _DLV_NS,
            DEFAULT_OPEN_NAMESPACE as _DLV_OPEN,
            DEFAULT_TAB_NAMESPACE as _DLV_TAB)
        ns = str(spec.get("namespace") or _DLV_NS)
        w = DeliverableCardWidget(
            wid, label=str(spec.get("label", "Deliverables")),
            namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _DLV_OPEN),
            tab_namespace=str(spec.get("tab_namespace") or _DLV_TAB),
            chip_label=str(spec.get("chip_label", "Artifacts")),
            header_dot=bool(spec.get("header_dot", True)),
            max_rows=int(spec.get("max_rows", 5)))
        comp.subscriptions.append((ns, w))
        # `namespace:` here is the SUBSCRIBED topic (appended above);
        # clear it so _wire_common doesn't double-subscribe.
        spec.pop("namespace", None)
    elif key in ("deliverablealert", "deliverable_alert"):
        # [DEC-DELIVERABLE-HISTORY-WIDGET] the deliverable/new live-alert
        # banner: subscribes the monitor's TWO alert namespaces; Open
        # publishes open_namespace (the opener daemon actuates).
        from ..widgets.deliverable_alert import (
            DeliverableAlertWidget,
            DEFAULT_NAMESPACES as _ALERT_NS,
            DEFAULT_OPEN_NAMESPACE as _ALERT_OPEN)
        ns_list = [str(n) for n in
                   (spec.get("namespaces") or list(_ALERT_NS))]
        w = DeliverableAlertWidget(
            wid, namespaces=ns_list,
            open_namespace=str(spec.get("open_namespace") or _ALERT_OPEN),
            ttl_s=float(spec.get("ttl_s", 25.0)))
        for _ns in ns_list:
            comp.subscriptions.append((_ns, w))
    elif key in ("deliverablehistory", "deliverable_history"):
        # [DEC-DELIVERABLE-HISTORY-WIDGET] the full Artifacts filter/sort
        # view over the self/deliverables projection's `all` list.  ONE
        # subscribed namespace; row Open ▸ publishes open_namespace.
        from ..widgets.deliverable_history import (
            DeliverableHistoryWidget,
            DEFAULT_NAMESPACE as _DH_NS,
            DEFAULT_OPEN_NAMESPACE as _DH_OPEN)
        ns = str(spec.get("namespace") or _DH_NS)
        w = DeliverableHistoryWidget(
            wid, namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _DH_OPEN))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("bootstrapmanifest", "bootstrap_manifest"):
        # [SPEC:CXC-CONSOLE-VISUAL] the Configuration tab's bootstrap-manifest
        # viewer — the meta-object made visible (extends the registered_checklist
        # family).  Subscribes TWO namespaces: self/meta (the meta_publisher
        # projection) AND the bus-native compact-threshold config namespace
        # (authoritative-if-newer, D13).  A wake-step/gift tap publishes
        # open_namespace (the ONE Open seam, D11); a threshold edit publishes
        # config_namespace (state on the bus, the launcher applies it).
        from ..widgets.bootstrap_manifest import (
            DEFAULT_NAMESPACE as _META_NS,
            DEFAULT_OPEN_NAMESPACE as _META_OPEN,
            DEFAULT_CONFIG_NAMESPACE as _META_CFG)
        ns = str(spec.get("namespace") or _META_NS)
        cfg_ns = str(spec.get("config_namespace") or _META_CFG)
        # ADDITIVE `sections` filter ([CXC-IC-CONFIG-MASTERDETAIL]): a subset of
        # {wake, identity, channel, lifecycle, dusk_gifts} draws only those cards
        # single-column (the master/detail right pane); unset = the legacy layout.
        _bm_sections = spec.get("sections")
        w = BootstrapManifestWidget(
            wid, namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _META_OPEN),
            config_namespace=cfg_ns,
            # DIRECT-WRITE threshold ([DEC-HANDOVER-MEASURED], supersedes D13):
            # a threshold edit read-modify-writes this meta-object file.
            meta_path=spec.get("meta_path"),
            sections=list(_bm_sections) if _bm_sections else None)
        comp.subscriptions.append((ns, w))
        comp.subscriptions.append((cfg_ns, w))
        spec.pop("namespace", None)     # both subscribed above
    elif key in ("configsettings", "config_settings"):
        # [SPEC:CXC-CONSOLE-VISUAL] the Configuration tab's settings surface:
        # retention (stepper) · heartbeat cadence (steppers) · the escalation
        # filter ([DEC-NOTIFICATION-ESCALATION-FILTER], bus-native STATE) ·
        # the dangerous-mode LOCKED indicator (D10).  Subscribes FOUR config
        # namespaces (retention · heartbeat · heartbeat/current READBACK ·
        # rules); edits publish the writable three (state on the bus, the
        # launcher / channel-bridge applies).  heartbeat/current is READ-ONLY.
        from ..widgets.config_settings import (
            DEFAULT_RETENTION_NS as _RET_NS,
            DEFAULT_HEARTBEAT_NS as _HB_NS,
            DEFAULT_RULES_NS as _RULES_NS)
        ret_ns = str(spec.get("retention_ns") or _RET_NS)
        hb_ns = str(spec.get("heartbeat_ns") or _HB_NS)
        rules_ns = str(spec.get("rules_ns") or _RULES_NS)
        # ADDITIVE `sections` filter ([CXC-IC-CONFIG-MASTERDETAIL]): a subset of
        # {retention, heartbeat, escalation, permissions} draws only those
        # cards single-column (the master/detail right pane); unset = the
        # legacy 2x2 grid.
        sections = spec.get("sections")
        # the harness's effective-cadence readback ([CXC-IC-HEARTBEAT-TWO-SOURCES]
        # readback half): READ-ONLY, subscribed so the heartbeat card can show
        # SET-vs-CURRENT.  Follows heartbeat_ns unless explicitly overridden.
        hb_cur_ns = spec.get("heartbeat_current_ns")
        w = ConfigSettingsWidget(
            wid, retention_ns=ret_ns, heartbeat_ns=hb_ns,
            heartbeat_current_ns=str(hb_cur_ns) if hb_cur_ns else None,
            rules_ns=rules_ns,
            sections=list(sections) if sections else None)
        comp.subscriptions.append((ret_ns, w))
        comp.subscriptions.append((hb_ns, w))
        comp.subscriptions.append((w.heartbeat_current_ns, w))
        comp.subscriptions.append((rules_ns, w))
    elif key in ("consoleprefs", "console_prefs"):
        # [SPEC:CXC-CONSOLE-PROD] the Appearance selector (theme · font-scale ·
        # dark custom colors) — the WRITE side of the render-deep personalization.
        # Publishes Foundation A's console/* namespaces; config_persister persists
        # them and the NEXT launch applies (modulo restart).  Subscribes the same
        # three so a re-seed / late join shows the saved look.
        from ..widgets.console_prefs import (
            DEFAULT_THEME_NS as _CP_THEME, DEFAULT_FONT_NS as _CP_FONT,
            DEFAULT_CUSTOM_NS as _CP_CUSTOM, DEFAULT_SCROLL_NS as _CP_SCROLL)
        theme_ns = str(spec.get("theme_ns") or _CP_THEME)
        font_ns = str(spec.get("font_ns") or _CP_FONT)
        custom_ns = str(spec.get("custom_ns") or _CP_CUSTOM)
        scroll_ns = str(spec.get("scroll_ns") or _CP_SCROLL)
        _cp_sections = spec.get("sections")
        w = ConsolePrefsWidget(
            wid, theme_ns=theme_ns, font_ns=font_ns, custom_ns=custom_ns,
            scroll_ns=scroll_ns,
            sections=list(_cp_sections) if _cp_sections else None)
        comp.subscriptions.append((theme_ns, w))
        comp.subscriptions.append((font_ns, w))
        comp.subscriptions.append((custom_ns, w))
        comp.subscriptions.append((scroll_ns, w))
    elif key in ("collab_select", "collabselect"):
        # [CXC-ICP-S4-WIDGETSEL] the Collaboration widget-selection editor —
        # NB the type token avoids the substring "widget": canonical_key strips
        # "widget", so a "widget_select" token would mangle to "_select".
        # DRIVEN BY Foundation B (list_widgets(category)); a toggle/arrange
        # publishes Foundation A's console/tabs.  Subscribes it to reflect the
        # persisted selection (the Collaboration tab READS it at launch).
        from ..widgets.widget_select import DEFAULT_TABS_NS as _WS_TABS
        tabs_ns = str(spec.get("tabs_ns") or _WS_TABS)
        # [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D2: fit-aware against a target
        # region (default the 332px rail).  A catalog widget whose min_size /
        # region_affinity doesn't fit is shown DISABLED with a "→ add on <home>"
        # redirect — no more silent no-op on toggle.
        _rw = spec.get("region_width", spec.get("region_w"))
        _rh = spec.get("region_height", spec.get("region_h"))
        w = WidgetSelectWidget(
            wid, tabs_ns=tabs_ns,
            category=str(spec.get("category", "collaboration")),
            region=str(spec.get("region", "rail")),
            region_w=float(_rw) if _rw is not None else 332.0,
            region_h=float(_rh) if _rh is not None else None,
            title=spec.get("title"))
        comp.subscriptions.append((tabs_ns, w))
    elif key == "dashboard":
        # [CXC-ICP-S5-DASHBOARD] the Dashboard placement grid — a palette from
        # list_widgets(category="dashboard-eligible") (Foundation B) + children
        # built THROUGH the registry from persisted placements.  Placing/removing
        # publishes Foundation A's console/dashboard.  The placed children's
        # required_namespaces are wired at parse time (child_subs -> engine
        # subscriptions), the launch-time READ side ([CXC-ICP-RESTART]).
        w = _build_dashboard(spec, wid, comp)
        comp.subscriptions.append((w.dashboard_ns, w))
        for _ns, _child in w.child_subs:
            comp.subscriptions.append((_ns, _child))
    elif key in ("docbrowser", "doc_browser"):
        # [DEC-INAPP-MARKDOWN-VIEWER] the in-app doc-browser overlay: LEFT =
        # doc list (built from self/meta), RIGHT = the read-only markdown
        # viewer.  Subscribes its OPEN seam (a doc path opens it; the
        # bootstrap-manifest steps publish here now) + the meta namespace for
        # the list.  Draws nothing while idle (taps fall through).
        from ..widgets.doc_browser import (
            DEFAULT_OPEN_NAMESPACE as _DOC_OPEN,
            DEFAULT_DOCS_NAMESPACE as _DOC_META)
        open_ns = str(spec.get("open_namespace") or _DOC_OPEN)
        docs_ns = str(spec.get("docs_namespace") or _DOC_META)
        w = DocBrowserWidget(wid, open_namespace=open_ns, docs_namespace=docs_ns,
                             doc_root=str(spec.get("doc_root", "")))
        comp.subscriptions.append((open_ns, w))
        comp.subscriptions.append((docs_ns, w))
    elif key in ("projectsview", "projects_view"):
        # [DEC-PROJECT-STRUCTURE] the Projects tab surface: the roster GROUPED
        # BY FAMILY with the 3-state git indicator + the missing-decision /
        # structure-gap flags, and the select->swap structure detail.  ONE
        # subscribed namespace (self/projects, the project_validator
        # projection); the detail's Open ▸ publishes open_namespace (the
        # in-app doc-browser overlay opens the decision-LTS read-only).
        from ..widgets.projects_view import (
            ProjectsViewWidget,
            DEFAULT_NAMESPACE as _PV_NS,
            DEFAULT_OPEN_NAMESPACE as _PV_OPEN)
        ns = str(spec.get("namespace") or _PV_NS)
        w = ProjectsViewWidget(
            wid, namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _PV_OPEN))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("awaywatchview", "away_watch_view"):
        # the Away-Watch history browser (Collaboration tab): a scrollable
        # roster of prior overnight build-watches whose select swaps a detail
        # card.  ONE subscribed namespace (self/away_watches, the
        # away_watch_publisher projection).  Open watch ▸ publishes
        # launch_namespace (a launcher relaunches the Night Watch window);
        # each artifact link publishes open_namespace (the in-app doc-browser
        # overlay opens it read-only — same seam as Config/Projects).
        from ..widgets.away_watch_view import (
            AwayWatchViewWidget,
            DEFAULT_NAMESPACE as _AW_NS,
            DEFAULT_OPEN_NAMESPACE as _AW_OPEN,
            DEFAULT_LAUNCH_NAMESPACE as _AW_LAUNCH)
        ns = str(spec.get("namespace") or _AW_NS)
        w = AwayWatchViewWidget(
            wid, namespace=ns,
            open_namespace=str(spec.get("open_namespace") or _AW_OPEN),
            launch_namespace=str(spec.get("launch_namespace") or _AW_LAUNCH))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("consoletabs", "console_tabs"):
        # [DEC-DELIVERABLE-HISTORY-WIDGET] console-level
        # [Collaboration | Artifacts] tabs — TabWidget routing + the
        # previs bar chrome; tab STATE rides tab_namespace and the badge
        # rides the self/deliverables stats.
        w = _build_tab(spec, wid, comp, cls_name="console")
        comp.subscriptions.append((w.tab_namespace, w))
        comp.subscriptions.append((w.badge_namespace, w))
    elif key in ("masterdetail", "master_detail"):
        # [CXC-IC-CONFIG-MASTERDETAIL] the two-panel master/detail container:
        # LEFT nav-list + RIGHT detail pane, on TabWidget routing (reuse by
        # subclass, only the chrome differs). Selection rides nav_namespace
        # (default _app/config/section) — a nav tap publishes it, an
        # Intelligence drives the identical seam.
        w = _build_master_detail(spec, wid, comp)
        comp.subscriptions.append((w.nav_namespace, w))
    elif key in ("agentsskills", "agents_skills"):
        # [CXC-IC-CONFIG-LOADED] the Agents / Skills NESTED viewer. DATA ns
        # (self/<id>/agents | self/<id>/skills) — payload {name, description,
        # path, loaded}; SELECTION ns (_app/config/agent | _app/config/skill).
        # The detail is a read-only markdown_viewer ([CXC-MDV]) that LOADS the
        # item's .md body from `path`. The (Open) button publishes the source dir
        # on the reveal seam (_app/fs/open) — the widget only PUBLISHES it.
        # `mode: memory` ([DEC-LOCAL-MEMORY-CONFIG-SECTION]) is the THIRD mode on
        # this SAME widget — data ns self/<id>/memories, selection _app/config/memory.
        from ..widgets.agents_skills import (
            DEFAULT_AGENTS_NS as _AG_NS, DEFAULT_SKILLS_NS as _SK_NS,
            DEFAULT_MEMORY_NS as _MEM_NS,
            DEFAULT_AGENT_SELECT as _AG_SEL, DEFAULT_SKILL_SELECT as _SK_SEL,
            DEFAULT_MEMORY_SELECT as _MEM_SEL,
            DEFAULT_OPEN_NS as _OPEN_NS, DEFAULT_AGENT_DIR as _AG_DIR,
            DEFAULT_SKILL_DIR as _SK_DIR, DEFAULT_MEMORY_DIR as _MEM_DIR)
        mode = str(spec.get("mode", "agents")).lower()
        is_skill = mode.startswith("skill")
        is_mem = mode.startswith("mem")
        default_ns = _MEM_NS if is_mem else _SK_NS if is_skill else _AG_NS
        ns = str(spec.get("namespace") or default_ns)
        sel_ns = str(spec.get("select_namespace")
                     or (_MEM_SEL if is_mem else _SK_SEL if is_skill
                         else _AG_SEL))
        w = AgentsSkillsWidget(
            wid, mode=mode, namespace=ns, select_namespace=sel_ns,
            open_namespace=str(spec.get("open_namespace") or _OPEN_NS),
            open_dir=str(spec.get("open_dir")
                         or (_MEM_DIR if is_mem else _SK_DIR if is_skill
                             else _AG_DIR)),
            title=spec.get("title"),
            nav_width=float(spec.get("nav_width", 300.0)))
        comp.subscriptions.append((ns, w))          # DATA
        comp.subscriptions.append((sel_ns, w))      # SELECTION seam
        spec.pop("namespace", None)     # subscribed above
    elif key in ("intelligencecard", "intelligence_card"):
        # [DEC-INTELLIGENCE-WIDGET-CONSOLIDATION] the consolidated Intelligence
        # rail card: gauge hero + Model/Registered/Usage rows + subagent chips.
        # Subscribes FIVE self/* namespaces (all pre-declared, all already live);
        # the Change chip publishes model_pick_namespace.  (self/activity dropped
        # 2026-07-21 with the activity strip — David.)
        # [CXC-IH-5A] carried-identity: this card subscribes SIX self/* readings that are NOT
        # individual scene params. `self_prefix` lets the SCENE point the whole set at an id-scoped
        # family (e.g. `self/architect`) so a single-instance console binds ONLY its own identity —
        # WITHOUT baking an id into the shared engine. Default "self" preserves the legacy bare
        # topics (a non-migrated console is untouched). The widget dispatches by namespace TAIL, so
        # `self/<id>/compactions` routes to the compactions branch exactly as `self/compactions` did.
        self_prefix = str(spec.get("self_prefix", "self")).rstrip("/") or "self"
        w = IntelligenceCardWidget(
            wid, label=str(spec.get("label", "ComposeIO-Architect")),
            model_name=str(spec.get("model_name", "Opus 4.8")),
            context_namespace=str(spec.get("context_namespace",
                                           f"{self_prefix}/context_pct")),
            model_pick_namespace=str(spec.get("model_pick_namespace",
                                              "_app/model_picker")))
        for _ns in (w.context_namespace, f"{self_prefix}/compactions",
                    f"{self_prefix}/effort", f"{self_prefix}/registered",
                    f"{self_prefix}/output_tokens", f"{self_prefix}/subagents",
                    f"{self_prefix}/memory"):   # [DEC-MEMORY-BLOAT-MONITOR] automemory gauge
            comp.subscriptions.append((_ns, w))
    elif key in ("accountcard", "account_card"):
        # [DEC-INTELLIGENCE-WIDGET-CONSOLIDATION] the consolidated Account rail
        # card: the windowed rate-limit bars + Sign in ▸.  ONE subscribed
        # namespace (self/rate_limit); Sign in publishes signin_namespace.
        ns = str(spec.get("namespace") or "self/rate_limit")
        w = AccountCardWidget(
            wid, account_id=str(spec.get("account_id", "david740burnett")),
            namespace=ns,
            signin_namespace=str(spec.get("signin_namespace")
                                 or "self/account/signin"))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key in ("modelpicker", "model_picker"):
        # [DEC-INTELLIGENCE-WIDGET-CONSOLIDATION] the model/effort picker modal
        # (DialogWidget by reference).  Subscribes its OPEN namespace (Change
        # chip publishes "on"); Apply publishes self/model/select and STOPS.
        ns = str(spec.get("namespace") or "_app/model_picker")
        w = ModelPickerWidget(
            wid, namespace=ns,
            select_namespace=str(spec.get("select_namespace")
                                 or "self/model/select"),
            model_cmd=str(spec.get("model_cmd", "opus")),
            effort=str(spec.get("effort", "high")))
        comp.subscriptions.append((ns, w))
        spec.pop("namespace", None)     # already subscribed above
    elif key == "avatar":
        # [CXC-ICV-AVATAR] the self-animated presence card.  ONE namespace
        # (the bus-side PriorityCommandSource arbitration is upstream,
        # [CXC-ICD-D20]); the widget renders whatever enum value arrives.
        from ..widgets.avatar import DEFAULT_NAMESPACE as _AV_NS
        ns = str(spec.get("namespace") or _AV_NS)
        w = AvatarWidget(
            wid, name=str(spec.get("name", "architect")),
            namespace=ns,
            expression=str(spec.get("expression", "grounded")),
            compact=bool(spec.get("compact", False)),
            state_prefix=str(spec.get("state_prefix", "self/registered")),
            state_ok=str(spec.get("state_ok", "✓ grounded")),
            state_suffix=str(spec.get("state_suffix", "")))
        comp.subscriptions.append((ns, w))
    elif key in ("themetoggle", "theme_toggle"):
        # [CXC-ICV-THEME] the console header theme button: tap re-points
        # the console_theme active table + re-syncs the background pass.
        w = ThemeToggleWidget(wid, label=str(spec.get("label", "theme")))
    elif key in ("coordinate", "coordinateentry", "coordinate_entry"):
        w = CoordinateWidget(
            wid, label=str(spec.get("label", "Coordinates")),
            max_digits=int(spec.get("max_digits", 8)),
            namespace=str(spec.get("namespace", "")),
            namespace_coordinates=str(spec.get("namespace_coordinates", "")),
            namespace_status=str(spec.get("namespace_status", "")),
            namespace_arm=str(spec.get("namespace_arm", "")))
        if w.ns_arm:
            comp.subscriptions.append((w.ns_arm, w))
        if w.ns_coordinates and w.ns_coordinates != spec.get("namespace"):
            comp.subscriptions.append((w.ns_coordinates, w))
    elif key == "callout":
        w = CalloutWidget(wid, text=str(spec.get("text", spec.get("label", ""))),
                          target=spec.get("target", (0.5, 0.5)),
                          offset=spec.get("offset", (-70, -46)),
                          color=_color_tuple(spec.get("color")),
                          font_size=float(spec.get("font_size", 12)))
    elif key in ("tab", "tabcontainer", "tab_container"):
        w = _build_tab(spec, wid, comp)
    elif key == "label":
        w = LabelWidget(wid, text=str(spec.get("text", spec.get("label", ""))),
                        font_size=float(spec.get("font_size", 16)),
                        font_weight=str(spec.get("font_weight", "regular")),
                        font_family=str(spec.get("font_family",
                                                 "sans-serif")),
                        color=_color_tuple(spec.get("color"),
                                           (0xE0, 0xE0, 0xE0)),
                        align=str(spec.get("align", "left")))
    elif key in ("rectangle", "rect"):
        w = RectangleWidget(wid,
                            width=float(spec.get("width", 100)),
                            height=float(spec.get("height", 60)),
                            fill_color=_rgba(spec.get("fill_color"),
                                             (*_TEAL, 255)),
                            stroke_color=_rgba(spec.get("stroke_color")),
                            stroke_width=float(spec.get("stroke_width", 1.0)),
                            corner_radius=float(spec.get("corner_radius", 0)))
    elif key == "circle":
        w = CircleWidget(wid, radius=float(spec.get("radius", 30)),
                         fill_color=_rgba(spec.get("fill_color"),
                                          (*_TEAL, 255)),
                         stroke_color=_rgba(spec.get("stroke_color")),
                         stroke_width=float(spec.get("stroke_width", 1.0)))
    elif key in ("markdownviewer", "markdown_viewer"):
        # [CXC-MDV] the READ-ONLY, scrolling markdown surface as a first-class
        # scene widget — the substrate of the standalone doc-viewer window
        # ([DEC-DOCVIEWER-WINDOW]).  A ``path`` (repo-relative or absolute) is
        # read 'r' once; ``content`` is a literal string.  For the doc-viewer
        # window the launcher bakes the specific doc into ``path`` via the
        # scene's ``variables:`` block ($doc_path), so each window renders its
        # own doc independently — no shared namespace.  No subscriptions: a
        # static read surface (scroll is the only interaction).
        w = MarkdownViewerWidget(
            wid,
            content=spec.get("content"),
            path=spec.get("path"),
            label=str(spec.get("label", "")),
            font_size=float(spec.get("font_size", 14)),
            pad=float(spec.get("pad", 20)))
    elif key in ("dsdform", "dsd_form"):
        # [SPEC:CXC-DSDV] the interactive DSD form — one control-group per
        # `### D<n>` decision (radios pre-selected to the recommendation +
        # a per-decision comment field), writing chosen options + comments
        # back into the `Your call:` slots on save-close.  The launcher bakes
        # the specific DSD into `path` via the scene's `variables:` block
        # ($dsd_path).  Real INPUT controls (extends the input plane), so no
        # subscriptions — its answer state IS the source of write-back.
        w = DsdFormWidget(
            wid,
            content=spec.get("content"),
            path=spec.get("path"),
            correlation_id=str(spec.get("correlation_id", "")))
    elif key in ("touchviz", "touch_viz"):
        # [CXC-R-ENGINE-HIT] full-surface multitouch visualizer — one halo +
        # core block per active finger, tracked by pointer_id via the raw
        # per-finger feed (wants_raw_pointers).
        w = TouchVizWidget(
            wid,
            core_size=float(spec.get("core_size", 64)),
            halo_radius=float(spec.get("halo_radius", 150)),
            background=_rgba(spec.get("background"), (0x08, 0x0A, 0x10, 255)))
    elif key in ("linkhealth", "link_health"):
        # [SPEC:CXC-CONSOLE-VISUAL] connection-health dot: subscribes the
        # channel heartbeat (default namespace) and publishes its verdict on
        # state_namespace; the header label + SEND button bind to that.  Force
        # the subscribed namespace into the spec so _wire_common subscribes it.
        spec["namespace"] = spec.get("namespace") or \
            "intelligence/architect/channel/alive"
        w = LinkHealthWidget(
            wid, radius=float(spec.get("radius", 4)),
            namespace=str(spec["namespace"]),
            state_namespace=str(spec.get("state_namespace",
                                         "console/link/state")),
            fresh_threshold_s=float(spec.get("fresh_threshold_s", 12.0)))
        _fc = _rgba(spec.get("fill_color"))
        if _fc is not None:
            w.fill_color = _fc
    elif key == "image":
        w = ImageWidget(wid, src=str(spec.get("src", "")),
                        width=float(spec.get("width", 160)),
                        height=float(spec.get("height", 120)))
        if spec.get("src"):
            comp.asset_uris.append(str(spec["src"]))
    elif key == "lottie":
        # [SPEC:CXC-LOTTIE] — vector animation, bus-bindable transport
        w = LottieWidget(
            wid, src=str(spec.get("src", "")),
            width=float(spec.get("width", 200)),
            height=float(spec.get("height", 200)),
            fit=str(spec.get("fit", "contain")),
            autoplay=spec.get("autoplay", True),
            loop=spec.get("loop", True),
            speed=spec.get("speed", 1.0),
            go_to_and_stop=spec.get("go_to_and_stop"),
            play_segments=spec.get("play_segments"),
            trigger=spec.get("trigger"))
        if spec.get("src"):
            comp.asset_uris.append(str(spec["src"]))
    elif key == "svg":
        # [SPEC:CXC-SVG] — vector graphic decomposed to Canvas primitives
        w = SvgWidget(
            wid, src=str(spec.get("src", "")),
            width=float(spec.get("width", 200)),
            height=float(spec.get("height", 200)),
            fit=str(spec.get("fit", "contain")),
            pan_zoom=spec.get("pan_zoom", False))
        if spec.get("src"):
            comp.asset_uris.append(str(spec["src"]))
    elif key in ("launch_tile", "launchtile"):
        # [SPEC:CXC-LAUNCHBAR] [CXC-LB-TILE] one ribbon tile: icon + label +
        # one launch intent.  A press publishes `_app/<target>/open` and nothing
        # else; no persistent active-app highlight is drawn (David 2026-07-29).
        _icon = str(spec.get("icon", ""))
        w = LaunchTileWidget(
            wid,
            label=str(spec.get("label", "")),
            target=str(spec.get("target", "")),
            icon=_icon,
            intent_namespace=spec.get("intent_namespace"),
            enabled=spec.get("enabled", True),
            width=float(spec.get("width", 72)),
            height=float(spec.get("height", 56)))
        if _icon:
            comp.asset_uris.append(_icon)
        else:
            logger.warning("launch_tile %s: no icon (target=%s) -> label "
                           "fallback swatch", wid, spec.get("target"))
    elif key == "hookup":
        # [FLOW:MAKER-PROJECT] S3 native pass — the in-app hookup wiring
        # guide.  `src:` is the maker `.compose.yaml`; the widget renders its
        # `hookup:` block from the SHARED layout intermediate
        # (engine/hookup_layout.py) with real Fritzing footprint art
        # composited through the RecordingCanvas lowered-cache.
        w = HookupWidget(
            wid, src=str(spec.get("src", "")),
            width=float(spec.get("width", 1200)),
            height=float(spec.get("height", 900)),
            max_scale=float(spec.get("max_scale", 2.0)))
    elif key == "media":
        # [SPEC:CXC-RM-MEDIA] — frame-source media surface (video file / webcam /
        # network stream): externally-decoded frames composited via draw_image,
        # bus-bindable transport + the state-on-bus trigger model. No frames on the
        # bus. (PyAV is a lazy optional dep; absent -> placeholder chrome.)
        w = MediaWidget(
            wid, src=str(spec.get("src", "")),
            width=float(spec.get("width", 320)),
            height=float(spec.get("height", 240)),
            fit=str(spec.get("fit", "contain")),
            autoplay=spec.get("autoplay", True),
            loop=spec.get("loop", True),
            speed=spec.get("speed", 1.0),
            trigger=spec.get("trigger"),
            trigger_map=spec.get("trigger_map"),
            hwaccel=str(spec.get("hwaccel", "none")))
        # only a bundled/file source is a prefetchable asset; live schemes
        # (camera://, rtsp://, mjpeg://, http://) are opened by the decoder, not fetched.
        _msrc = str(spec.get("src", ""))
        if _msrc and (_msrc.startswith("file://") or "://" not in _msrc):
            comp.asset_uris.append(_msrc)
    elif key == "mermaid":
        # [SPEC:CXC-IC] [CXC-IC-CANVAS] — the composable canvas at its purest:
        # an Intelligence emits mermaid TEXT on `thread/diagram` and this
        # widget renders it (web tier: vendored mermaid.js; Canvas tier: source
        # panel).  The state on the bus is the diagram SOURCE, not an SVG URI.
        # Default the subscribed namespace so the widget is bus-live even when
        # the spec omits `namespace:` — _wire_common then subscribes it.
        spec["namespace"] = spec.get("namespace") or "thread/diagram"
        w = MermaidWidget(
            wid, source=str(spec.get("source", "")),
            namespace=str(spec["namespace"]),
            theme=str(spec.get("theme", "dark")),
            width=float(spec.get("width", 440)),
            height=float(spec.get("height", 320)),
            # [CXC-ICV-CANVAS] option B: chrome="console" wears the
            # pre-vis canvaspanel chrome on the Canvas tier.
            chrome=str(spec.get("chrome", "")))
    elif key == "logic":
        # [SPEC:CXC-FLAUTH] headless logic node — computes on the bus
        # (bindings/behaviors/outputs via the shared machinery), no render.
        w = LogicWidget(wid,
                        value=float(spec.get("value", 0.0))
                        if isinstance(spec.get("value"), (int, float))
                        and not isinstance(spec.get("value"), bool) else 0.0,
                        label=str(spec.get("label", "")))
    else:
        w = PlaceholderWidget(wid, original_type=wtype,
                              label=str(spec.get("label", "")),
                              width=float(spec.get("width", 140)),
                              height=float(spec.get("height", 80)))

    _wire_common(w, spec, comp, pending_bindings,
                 state_spec=state_spec, gestures=gestures)
    return w


def _build_tab(spec: dict, wid: str, comp: Composition,
               cls_name: str = "tab") -> "TabWidget":
    """TabWidget from either dialect: `tabs:` ([CXC-R-WIDGET-CONTAINER])
    or `children:` tab defs (the production TabRenderer shape).

    cls_name="console" builds the console-level ConsoleTabWidget on the
    SAME routing/page machinery (reuse by reference — only the bar chrome
    + the two bus seams differ); console pages carry the previs 12px
    below-bar gap instead of the CTk page padding."""
    tab_defs = spec.get("tabs") or spec.get("children") or []
    labels = [str(t.get("label", t.get("tab_label", f"Tab {i}")))
              for i, t in enumerate(tab_defs)]
    if cls_name == "console":
        from ..widgets.console_tabs import ConsoleTabWidget
        tab = ConsoleTabWidget(
            wid, labels=labels,
            initial_tab=int(spec.get("initial_tab", 0)),
            tab_namespace=str(spec.get("tab_namespace")
                              or "_app/console/tab"),
            badge_namespace=str(spec.get("badge_namespace")
                                or "self/deliverables"),
            routing=str(spec.get("routing", "all")))
        page_gap, page_pad = 10, (12, 0, 0, 0)
    else:
        tab = TabWidget(wid, labels=labels,
                        initial_tab=int(spec.get("initial_tab", 0)),
                        routing=str(spec.get("routing", "all")))
        page_gap, page_pad = 6, (8, 8, 8, 8)
    for t in tab_defs:
        page = FlexContainer(_uid("tabpage"), direction="column",
                             align="stretch")
        page.gap = page_gap
        page.padding = page_pad
        child_specs = t.get("children", [t] if t.get("type") else [])
        for ch_spec in child_specs:
            page.add(build_node(ch_spec, comp))
        tab.add(page)
    for i, ch in enumerate(tab.children):
        ch.visible = (i == tab.active_index)
    return tab


def _build_master_detail(spec: dict, wid: str, comp: Composition):
    """MasterDetailWidget ([CXC-IC-CONFIG-MASTERDETAIL]) — the same `sections:`/
    `children:` tab-def dialect as _build_tab, but arranged as a LEFT nav-list +
    RIGHT detail pane. Reuse by subclass of TabWidget; the ONE new seam is
    nav_namespace (the selection-STATE topic)."""
    from ..widgets.master_detail import MasterDetailWidget, DEFAULT_NAV_NAMESPACE
    sec_defs = spec.get("sections") or spec.get("children") or []
    labels = [str(s.get("label", s.get("tab_label", f"Section {i}")))
              for i, s in enumerate(sec_defs)]
    md = MasterDetailWidget(
        wid, labels=labels,
        initial_tab=int(spec.get("initial_tab", 0)),
        nav_namespace=str(spec.get("nav_namespace") or DEFAULT_NAV_NAMESPACE),
        nav_width=float(spec.get("nav_width", 264.0)),
        routing=str(spec.get("routing", "all")))
    for s in sec_defs:
        page = FlexContainer(_uid("mdpage"), direction="column",
                             align="stretch")
        page.gap = 12
        page.padding = (0, 0, 0, 0)
        child_specs = s.get("children", [s] if s.get("type") else [])
        for ch_spec in child_specs:
            page.add(build_node(ch_spec, comp))
        md.add(page)
    for i, ch in enumerate(md.children):
        ch.visible = (i == md.active_index)
    return md


def _build_dashboard(spec: dict, wid: str, comp: Composition):
    """DashboardWidget ([CXC-ICP-S5-DASHBOARD]) — the placement grid.  Placements
    come from the scene's ``placements:`` (a fixture/default); the launch path
    (console/personalization.py) overlays the persisted
    ``meta.console.dashboard.placements`` and re-wires child subscriptions.  The
    children are built THROUGH the registry inside the widget, and its
    ``child_subs`` (each placed widget's required_namespaces) are returned for the
    caller to register as engine subscriptions."""
    from ..widgets.dashboard import DashboardWidget as _DW, DEFAULT_DASHBOARD_NS
    placements = spec.get("placements") or []
    return _DW(
        wid,
        dashboard_ns=str(spec.get("dashboard_ns") or DEFAULT_DASHBOARD_NS),
        palette_category=str(spec.get("palette_category")
                             or "dashboard-eligible"),
        placements=list(placements) if placements else None)


def _pop_state_gesture(spec: dict) -> Tuple[Optional[dict], Any]:
    """Lift the declarative state-machine + gesture blocks out of *spec*
    BEFORE extract_bindings runs ([CXC-BIND-STATES] / [CXC-RM-GESTURE]).

    ``state:`` is ALSO a bindable attribute (the LED value); it belongs to
    the state machine ONLY when a ``states:`` block is present — so a widget
    with states uses state:/transitions: as its selector+choreography, while
    a lone state: on an LED stays an ordinary binding.  Returns
    (state_spec | None, gestures | None); consumed keys are popped so they
    never reach extract_bindings or a widget constructor."""
    state_spec: Optional[dict] = None
    if "states" in spec:
        state_spec = {
            "states": spec.pop("states"),
            "state": spec.pop("state", None),
            "transitions": spec.pop("transitions", None),
        }
    gestures = spec.pop("gestures", None)
    return state_spec, gestures


def _wire_common(w: Widget, spec: dict, comp: Composition,
                 bindings: Optional[List[AttrBinding]] = None,
                 *, state_spec: Optional[dict] = None,
                 gestures: Any = None) -> None:
    """namespace subscribe + publish-on-tap + attribute bindings/behaviors
    ([SPEC:CXC-BIND]) + states/transitions + gesture producers + layout
    hints."""
    ns = spec.get("namespace")
    if ns:
        comp.subscriptions.append((str(ns), w))

    pub_ns = spec.get("publish_namespace")
    if pub_ns:
        w.interactive = True
        w._publish_namespace = str(pub_ns)                  # type: ignore
        w._publish_value = spec.get("publish_value", "toggle")  # type: ignore
    elif spec.get("interactive"):
        w.interactive = True

    # Swipe binding ([SPEC:CXC-GESTURE] [CXC-GEST-BUS]): a recognized
    # directional swipe on this widget publishes the direction
    # ("up"/"down"/"left"/"right") to swipe_namespace — the finger-and-
    # Intelligence-identical hook a widget binds a swipe with, parallel to
    # publish_namespace for taps.  Additive; independent of tap binding.
    swipe_ns = spec.get("swipe_namespace")
    if swipe_ns:
        w.interactive = True
        w._swipe_namespace = str(swipe_ns)                  # type: ignore

    # Literal render attributes ([CXC-BIND-ATTR] literal form).  Bound
    # forms were lifted into *bindings* by extract_bindings — what is
    # left in the spec is a literal (or a binding's declared default).
    vis = spec.get("visible")
    if isinstance(vis, bool):
        w.visible = vis
    for attr in ("opacity", "rotation", "scale"):
        if isinstance(spec.get(attr), (int, float)):
            setattr(w, attr, float(spec[attr]))

    # Reusable per-widget focus-ring opt-out ([CXC-R-ENGINE-FOCUS]): any
    # widget can suppress the engine focus ring with `focus_ring: false`.
    # Applied AFTER construction, so an explicit scene key wins over a
    # widget's chrome default (e.g. the console composer's built-in opt-out).
    fr = spec.get("focus_ring")
    if isinstance(fr, bool):
        w.focus_ring = fr

    # Reusable per-widget staleness OPT-OUT ([CXC-PRIM-STALE]): `staleness:
    # none | self | auto`.  The default is "auto" — the engine marks a widget
    # whose bus inputs stopped arriving — so a scene author DECLINES the
    # treatment explicitly and never has to remember to request it.  An
    # unrecognized value is ignored rather than defaulted-off: a typo must not
    # silently disable the thing that reports a lying display.
    st = spec.get("staleness")
    if isinstance(st, str) and st.strip().lower() in ("auto", "self", "none"):
        w.staleness_treatment = st.strip().lower()

    # Generalized attribute bindings ([CXC-RM-M4] visible: generalized to
    # opacity/rotation/scale/color/value attrs, [CXC-BIND-ATTR]).
    px_geom: set = set()
    for b in bindings or []:
        b.widget = w
        if b.attr == "visible":
            # M4 semantics preserved: hidden until the namespace speaks,
            # unless the binding declared a default/initial.
            if not isinstance(vis, bool) and b.default is None:
                w.visible = False
            comp.visible_bindings.append((b.namespace, w, b.when, b.invert))
        if b.attr in STACK_GEOMETRY_ATTRS:
            # [CXC-PRIM-DRAGVAL] bound pixel-stack geometry is absolute px.
            px_geom.add(b.attr)
        comp.attr_bindings.append(b)
    if px_geom:
        w._stack_px_attrs = px_geom                          # type: ignore

    # [CXC-PRIM-DRAGVAL] drag-to-value input seam: any widget (typically the
    # slider's pixel_stack track surface) becomes a draggable value source.
    if isinstance(spec.get("drag_value"), dict):
        attach_drag_value(w, spec.get("drag_value"))

    # State-driven behaviors ([CXC-BIND-BEHAVIOR]).
    if "behaviors" in spec:
        for decl in parse_behaviors(spec.get("behaviors"), w.widget_id):
            decl.widget = w
            comp.behaviors.append(decl)

    # Declarative states + transitions ([CXC-BIND-STATES]).  The blocks were
    # lifted before extract_bindings (so state: is the selector, not a value
    # binding) — parse + attach here.
    if state_spec is not None:
        sm = parse_state_machine(state_spec, w.widget_id)
        if sm is not None:
            sm.widget = w
            comp.state_machines.append(sm)

    # Gesture producers ([CXC-RM-GESTURE]): a recognized gesture on this
    # widget publishes semantic bus state the state machine consumes.
    if gestures is not None:
        g = parse_gestures(gestures, w.widget_id)
        if g:
            w.interactive = True
            existing = getattr(w, "_gesture_publishes", None)
            if existing:
                for name, pubs in g.items():
                    existing.setdefault(name, []).extend(pubs)
            else:
                w._gesture_publishes = g              # type: ignore[attr-defined]

    # Declared outputs ([SPEC:CXC-HEADLESS]): attr -> namespace publishes.
    if "outputs" in spec:
        for out in parse_outputs(spec.get("outputs"), w.widget_id):
            out.widget = w
            comp.outputs.append(out)

    # explicit sizing overrides for flex/grid layout
    if "width" in spec:
        w.layout.width = spec["width"]
    if "height" in spec:
        w.layout.height = spec["height"]
    if "grow" in spec:
        w.layout.grow = float(spec["grow"])


def _wire_stack_position(w: Widget, spec: dict) -> None:
    """Pixel-stack child placement: x/y/w/h in px, fraction, or '%'."""
    w.stack_x = spec.get("x", 0)          # type: ignore
    w.stack_y = spec.get("y", 0)          # type: ignore
    if "w" in spec or "width" in spec:
        w.stack_w = spec.get("w", spec.get("width"))   # type: ignore
    if "h" in spec or "height" in spec:
        w.stack_h = spec.get("h", spec.get("height"))  # type: ignore


# ---------------------------------------------------------------------------
# Nested dialect
# ---------------------------------------------------------------------------

_CONTAINER_TYPES = {"stack", "flex", "grid", "pixel_stack", "pixelstack",
                    "ordered_stack", "orderedstack", "flow_stack",
                    "dialog"}


# Flex direction accepts the human-facing orientation vocabulary as an
# additive alias, so every primitive (segment_bar, slider, …) can declare
# `orient: horizontal|vertical` (David's terms) — the flex-native
# row/column continue to work unchanged. [SPEC:CXC-PRIM]
_DIRECTION_ALIASES = {"horizontal": "row", "vertical": "column"}


def _norm_direction(value) -> str:
    d = str(value).lower()
    return _DIRECTION_ALIASES.get(d, d)


# [CXC-ICP-S6-SCROLLDIR] the two accepted scroll-direction preferences; anything
# malformed/absent resolves to "natural" (respect the OS delta) — never raises.
SCROLL_DIRECTIONS = ("natural", "traditional")


def _norm_scroll_direction(value) -> str:
    if value is None:
        return "natural"
    v = str(value).strip().lower()
    return v if v in SCROLL_DIRECTIONS else "natural"


def build_node(spec: dict, comp: Composition,
               in_pixel_stack: bool = False) -> Widget:
    wtype = str(spec.get("type", "unknown")).lower()

    if wtype in _CONTAINER_TYPES:
        wid = spec.get("id") or _uid(wtype)
        strip_code_fields(spec, wid)                 # [CXC-BIND-BOUNDARY]
        state_spec, gestures = _pop_state_gesture(spec)   # before extraction
        pending_bindings = extract_bindings(spec, wid)
        if wtype in ("stack", "flex"):
            node: Container = FlexContainer(
                wid,
                direction=_norm_direction(spec.get("direction", "row")),
                align=str(spec.get("align", "start")),
                justify=str(spec.get("justify", "start")))
        elif wtype in ("ordered_stack", "orderedstack", "flow_stack"):
            # [SPEC:CXC-ORDER] a flex flow whose child order + shown-set are
            # bound to a bus state namespace (reorder/hide/show + reflow).
            # `state_namespace` (alias `order_namespace`) carries the order
            # list or the {order, hidden, visible} dict; `visibility_namespace`
            # is an optional separate shown-set source.  Byte-identical to a
            # `stack` until a state drives it — which is why `direction`
            # defaults to `row` HERE TOO, matching the `stack` branch above:
            # converting a `stack` that relied on the implicit default must not
            # silently flip the layout axis ([CXC-ORD-SUPERSET]).  Keep these
            # two defaults in lockstep.
            node = OrderedFlowContainer(
                wid,
                direction=_norm_direction(spec.get("direction", "row")),
                align=str(spec.get("align", "start")),
                justify=str(spec.get("justify", "start")),
                state_namespace=str(spec.get("state_namespace")
                                    or spec.get("order_namespace") or ""),
                visibility_namespace=str(spec.get("visibility_namespace") or ""))
            if node.state_namespace:
                comp.subscriptions.append((node.state_namespace, node))
            if node.visibility_namespace:
                comp.subscriptions.append((node.visibility_namespace, node))
        elif wtype == "grid":
            node = GridContainer(
                wid, stretch=bool(spec.get("stretch", True)),
                row_weights=spec.get("row_weights"),
                col_weights=spec.get("column_weights",
                                     spec.get("col_weights")))
        elif wtype == "dialog":
            node = DialogWidget(
                wid,
                title=str(spec.get("title", "")),
                panel_width=float(spec.get("panel_width", 340)),
                panel_height=float(spec.get("panel_height", 0)),
                dismissible=bool(spec.get("dismissible", True)),
                namespace=str(spec.get("namespace", "")),
                options=spec.get("options"),
                select_namespace=str(spec.get("select_namespace",
                                              spec.get("selected_ns", ""))),
                max_visible=int(spec.get("max_visible", 6)),
                anchor=spec.get("anchor"))
            # list-select mode ([CXC-PRIM-POPUP]): follow the selected-value
            # namespace too, so the check mark reflects the live selection.
            if node.select_namespace:                 # type: ignore[attr-defined]
                comp.subscriptions.append((node.select_namespace, node))
            if in_pixel_stack:
                # z-top full-surface overlay by default
                spec.setdefault("x", 0)
                spec.setdefault("y", 0)
                spec.setdefault("w", "100%")
                spec.setdefault("h", "100%")
        else:
            node = PixelStackContainer(
                wid,
                canvas_width=spec.get("width", 320),
                canvas_height=spec.get("height", 240))
        node.gap = float(spec.get("gap", 0))
        pad = spec.get("padding", 0)
        if isinstance(pad, (int, float)):
            node.padding = (pad, pad, pad, pad)
        elif isinstance(pad, (list, tuple)) and len(pad) == 4:
            node.padding = tuple(float(p) for p in pad)
        bg = _color_tuple(spec.get("background"))
        if bg:
            node.background = bg
        if spec.get("border"):
            node.border_color = _color_tuple(spec.get("border"), _PANEL_BORDER)
        node.corner_radius = float(spec.get("corner_radius", 0))
        if "clip" in spec:
            node.clip_children = bool(spec["clip"])

        _wire_common(node, spec, comp, pending_bindings,
                     state_spec=state_spec, gestures=gestures)
        child_in_stack = wtype in ("pixel_stack", "pixelstack")
        for ch_spec in spec.get("children", []) or []:
            child = build_node(ch_spec, comp, in_pixel_stack=child_in_stack)
            if child_in_stack:
                _wire_stack_position(child, ch_spec)
            elif "row" in ch_spec or "column" in ch_spec:
                child.grid_row = int(ch_spec.get("row", 0))      # type: ignore
                child.grid_col = int(ch_spec.get("column", 0))   # type: ignore
            node.add(child)
        if in_pixel_stack:
            pass  # position wired by the parent loop
        return node

    leaf = build_leaf(spec, comp)
    return leaf


# ---------------------------------------------------------------------------
# Production dialect (modules:)
# ---------------------------------------------------------------------------

def build_modules(config: dict, comp: Composition) -> Container:
    outer = GridContainer("modules_grid", stretch=True)
    outer.gap = 12
    outer.padding = (12, 12, 12, 12)

    for mod in config.get("modules", []) or []:
        panel = FlexContainer(f"module_{mod.get('name', _uid('mod'))}",
                              direction="column", align="stretch")
        panel.background = (*_PANEL_BG, 230)
        panel.border_color = _PANEL_BORDER
        panel.corner_radius = 10
        panel.padding = (10, 12, 12, 12)
        panel.gap = 6
        panel.grid_row = int(mod.get("grid_row", 0))       # type: ignore
        panel.grid_col = int(mod.get("grid_column", 0))    # type: ignore

        title = LabelWidget(_uid("mtitle"),
                            text=str(mod.get("label", mod.get("name", ""))),
                            font_size=13, font_weight="medium",
                            color=(0xAA, 0xAA, 0xBB))
        panel.add(title)

        # M4 grid weights: a module may request CTk-style weighted spreading
        # of its widget rows/columns (row_weights/column_weights: "equal",
        # dict, or list).  When weighted, the grid also grows to fill the
        # module panel (otherwise it top-packs at its measured size).
        rw = mod.get("row_weights")
        cw = mod.get("column_weights", mod.get("col_weights"))
        inner = GridContainer(_uid("mgrid"), stretch=True,
                              row_weights=rw, col_weights=cw)
        inner.gap = 10
        if rw is not None or cw is not None:
            inner.layout.grow = 1.0
        for wspec in mod.get("widgets", []) or []:
            if "use" in wspec:
                # normally rewritten by expand_components() before we get
                # here; a leftover means the config had no components: map
                # for this name — render an honest placeholder.
                w: Widget = PlaceholderWidget(
                    _uid("use"), original_type=f"use:{wspec['use']}",
                    label=str(wspec.get("label", "")))
            else:
                w = build_leaf(wspec, comp)
            w.grid_row = int(wspec.get("row", 0))          # type: ignore
            w.grid_col = int(wspec.get("column", 0))       # type: ignore
            inner.add(w)
        panel.add(inner)
        outer.add(panel)
    return outer


# ---------------------------------------------------------------------------
# [CXC-PROJECT-WIDGETS] Project-local widgets
# ---------------------------------------------------------------------------
#
# A project OWNS its widgets: rather than every widget registering through the
# engine core, a scene names the project's widget module(s) with a
# ``widget_modules:`` key and the parser imports them BEFORE it resolves any
# ``type:`` token.  The imported module SELF-REGISTERS its widget types by
# calling ``register(WidgetDescriptor(...))`` at import time (the same registry +
# ``build`` seam the engine core uses) — so a project widget is discovered,
# built, and personalization-listed exactly like an engine widget, with zero
# engine edits.  Import IS the contract; the engine never reaches into a project.
#
#   design/scene-level key (either dialect):
#     widget_modules:
#       - compose_projects.bsg.widgets      # dotted module on the repo root
#
# The engine core keeps registering exactly as before (``_REGISTRY_ROWS``); this
# only ADDS project descriptors on top.  A missing/broken module is logged, never
# fatal — its ``type:`` tokens still degrade to PlaceholderWidget (shipped
# behavior).

def _ensure_project_import_root() -> None:
    """Make ``compose_projects.*`` importable regardless of how the engine was
    launched (headless test with ``PYTHONPATH=python`` does NOT put the repo root
    on the path).  The repo root is the directory that CONTAINS
    ``compose_projects/``; derive it from the stable ``composeio`` package
    location (``<repo>/python/composeio/``), honoring ``COMPOSEIO_REPO_ROOT`` and
    the cwd as fallbacks, and add it to ``sys.path`` once."""
    import sys
    import composeio
    candidates = []
    env = os.environ.get("COMPOSEIO_REPO_ROOT")
    if env:
        candidates.append(env)
    # <repo>/python/composeio/__init__.py -> up two dirs from the package == <repo>
    pkg = os.path.dirname(os.path.abspath(composeio.__file__))   # …/python/composeio
    candidates.append(os.path.dirname(os.path.dirname(pkg)))     # …           <repo>
    candidates.append(os.getcwd())
    for root in candidates:
        if root and os.path.isdir(os.path.join(root, "compose_projects")):
            if root not in sys.path:
                sys.path.insert(0, root)
            return


def _load_widget_modules(config: dict) -> None:
    """[CXC-PROJECT-WIDGETS] Import each scene-declared project widget module so
    it self-registers its widget types into ``WIDGET_REGISTRY`` BEFORE any
    ``type:`` is resolved.  Accepts a list (or a bare string) of dotted module
    names under ``widget_modules:``.  A failed import is logged, not fatal."""
    mods = config.get("widget_modules") or []
    if isinstance(mods, str):
        mods = [mods]
    if not mods:
        return
    import importlib
    _ensure_project_import_root()
    for name in mods:
        try:
            importlib.import_module(str(name))
            logger.info("[CXC-PROJECT-WIDGETS] loaded widget module %r", name)
        except Exception as exc:      # noqa: BLE001 — a bad plugin must not kill the parse
            logger.warning("[CXC-PROJECT-WIDGETS] widget_modules: could not "
                           "import %r (%s); its widget types will fall back to "
                           "PlaceholderWidget", name, exc)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def parse_widget_tree(config: dict, base_dir: Optional[str] = None,
                       variable_overrides: Optional[Dict[str, Any]] = None
                       ) -> Composition:
    """Parse an already-loaded .compose.yaml dict into a Composition.

    `base_dir` is the directory the config was LOADED FROM, and it exists for
    `components_from:` ([CXC-FC-COMPOSITE]): a cross-file component import resolves
    relative to the IMPORTING SCENE, never to the launch cwd, so a scene means the
    same thing however it is launched.  `parse_compose_yaml` supplies it.  None is
    fine for every scene that imports nothing; a scene that DOES declare
    `components_from:` with no base_dir raises rather than guessing an anchor.

    `variable_overrides` is the (optional) LAUNCH-source override map for the
    document-level ``variables:`` block ([DEC-STATION-ID-IS-A-RUNTIME-BINDING-
    ATTRIBUTE]) — e.g. a physical console's station id.  Omit it (the default)
    and every scene resolves exactly as before this existed.
    """
    # [CXC-PROJECT-WIDGETS]: import any project-local widget modules FIRST so
    # their `type:` tokens are registered before component stamping / tree build.
    _load_widget_modules(config)
    # [CXC-FC-COMPOSITE] cross-file component import, resolved in the SAME load-time
    # pass as (and immediately before) stamping — so an imported component is
    # indistinguishable from a locally-declared one by the time the expander runs, and
    # the two-phase `{{param}}`-at-load / `${ns}`-at-runtime split (COMP-3) is
    # completely unchanged.
    config = resolve_component_imports(config, base_dir)
    # [CXC-FC-UNIT] the DECLARED INSTANCE LIST, resolved BEFORE component stamping so a
    # `for_each_instance:` entry becomes N ordinary `{use: ...}` sites that the expander
    # then stamps exactly as if a human had typed them.  ORDER IS LOAD-BEARING: run this
    # after `expand_components` and the generated `use:` sites arrive too late to be
    # expanded, and every pane renders a PlaceholderWidget.  The instance list is the
    # SINGLE source for the panes AND their namespaces ([CXC-FC-VAR-SCOPE]).
    instances = seed_config_variables(config)
    # spec-14 component stamping: rewrite {use: ...} into concrete widgets
    # BEFORE any widget construction (COMP-4).
    config = expand_components(config)
    # [CXC-BIND-VARS]: $name parse-time substitution (document-scoped
    # variables: block + widget-scoped blocks; component bodies may use
    # document variables since stamping already ran).
    doc_vars = resolve_doc_variables(config, variable_overrides)  # retained ([CXC-HMON-STATE])
    config = substitute_variables(config, variable_overrides)
    design = config.get("design", {}) or {}
    app = config.get("app", {}) or {}

    comp = Composition(
        root=None,  # type: ignore  (set below)
        title=str(design.get("title", "ComposeUX")),
        width=int(design.get("width", 960)),
        height=int(design.get("height", 600)),
        theme=str(design.get("theme", "dark")),
        font_scale=float(design.get("font_scale", 1.0) or 1.0),
        scroll_direction=_norm_scroll_direction(design.get("scroll_direction")),
        reduced_motion=bool(design.get("reduced_motion", False)),
        # [CXC-DVL-WINDOW]/[CXC-DVL-GEOMETRY] standalone-window UX.
        resizable=bool(design.get("resizable", False)),
        min_width=int(design.get("min_width", 0)),
        min_height=int(design.get("min_height", 0)),
        persist_geometry=bool(design.get("persist_geometry", False)),
        window_key=str(design.get("window_key", "")),
        personalization=str(design.get("personalization", "")),
        data_sources=bool(design.get("data_sources", False)),
        surface_singleton=bool(design.get("surface_singleton", False)),
        namespaces=list(config.get("namespaces", []) or []),
        transports=list(app.get("transports", []) or []),
        variables=doc_vars,
        app_id=str(app.get("id", "composeux-render")),
        instances=instances)

    # [CXC-ICV-CHROME] scene background pass config (all optional).
    if design.get("background") is not None:
        bg = _rgba(design.get("background"))
        if bg:
            comp.background = bg
    if isinstance(design.get("background_grid"), dict):
        comp.background_grid = design["background_grid"]
    if isinstance(design.get("background_wash"), dict):
        comp.background_wash = design["background_wash"]

    # [SPEC:CXC-HEADLESS]: the document-level headless block (schedule
    # travels INSIDE the widget config, never the roster — [CXC-HW-DEPLOY]).
    hless = config.get("headless")
    if isinstance(hless, dict):
        comp.headless = hless

    # [FLOW:MAKER-PROJECT] S2: the physical `hookup:` block (§3).  Purely
    # additive — absent -> comp.hookup stays None and the scene is unchanged;
    # present -> parsed + strictly validated against the parts catalog (a bad
    # part or pin is a hard error, so the netlist S3/S4 read is sound).
    if config.get("hookup") is not None:
        comp.hookup = parse_hookup(config.get("hookup"))
        # ...and the two halves of THIS artifact must agree.  A device block
        # that names a pin nothing wires, or binds a pin to a namespace the
        # wire does not carry, is drift that surfaces as a WRONG JOINT on a
        # bench.  Refused here, at load, on the error-severity findings only
        # (a namespace another app publishes is a warning, not a fault).
        _seam = seam_errors(config, comp.hookup)
        if _seam:
            raise HookupError(
                "the two halves of this .compose.yaml disagree\n  - "
                + "\n  - ".join(str(f) for f in _seam))

    # [FLOW:MAKER-PROJECT] S6: the `audio_cover:` block (the OUTPUT/actuator half).
    # Additive — absent -> comp.audio_cover stays None and the scene is unchanged.
    # Kept as the raw dict (a config descriptor the S4 emitter + the C3 firmware
    # read); the render engine itself never consumes it.
    if isinstance(config.get("audio_cover"), dict):
        comp.audio_cover = config.get("audio_cover")

    # [CXC-HAL-ACTUATE] the `relay_actuator:` block (the break-before-make
    # relay-bank OUTPUT actuator).  Additive — absent -> comp.relay_actuator
    # stays None and the scene is unchanged.  Validated (sensor-descriptor
    # style) into a RelayActuator; a malformed block raises RelayActuatorError.
    if config.get("relay_actuator") is not None:
        comp.relay_actuator = parse_relay_actuator(config.get("relay_actuator"))

    if "widgets" in config:            # nested dialect
        root = FlexContainer("root", direction="column", align="stretch")
        # Root chrome defaults to a 12px gutter; a fullscreen kiosk scene
        # zeroes it via `design: { padding: 0, gap: 0 }` (padding accepts a
        # uniform number or a [t,r,b,l] list) so a single fullscreen leaf
        # bleeds edge-to-edge with no inset that would clip edge touches.
        # [CXC-R-SCENE-GEOMETRY-POLICY] a scene that declares `fixed-size:` is
        # asserting its coordinates are ABSOLUTE against a named physical
        # glass, so the root gutter defaults to ZERO for it -- which is what
        # the firmware has always done unconditionally
        # (cyd/src/compose_scene.cpp: `build_children(s, widgets, canvas,
        # 0.0f, 0.0f)`, and it never reads `padding`/`gap` at all).
        #
        # THE DEFECT THIS CLOSES: the desktop inset every scene by 12px and
        # the device inset none, so the SAME scene drew at two different
        # origins with two different usable widths and nothing compared them.
        # Measured on engine_thrust.compose.yaml (240x320): every element
        # landed at +12,+12, the usable width fell 240 -> 216, and
        # `flank_banner` (declared y=292 h=22) arranged to 304..326 -- 6px
        # past the bottom of a 320px glass. With the gutter zeroed, every
        # element lands EXACTLY where the scene declares it.
        #
        # An explicit `padding:`/`gap:` still wins, so this only supplies a
        # default -- and only for scenes that opted into fixed-size.
        _fixed = design.get("fixed-size", design.get("fixed_size"))
        _gutter_default = 0 if _fixed else 12
        _pad = design.get("padding", _gutter_default)
        if isinstance(_pad, (list, tuple)) and len(_pad) == 4:
            root.padding = tuple(float(v) for v in _pad)
        else:
            root.padding = (float(_pad),) * 4
        root.gap = float(design.get("gap", _gutter_default))
        for spec in config.get("widgets", []) or []:
            root.add(build_node(spec, comp))
    elif "modules" in config:          # production dialect
        root = build_modules(config, comp)
    else:
        root = FlexContainer("root")
    comp.root = root
    return comp


def parse_compose_yaml(path: str,
                        variable_overrides: Optional[Dict[str, Any]] = None
                        ) -> Composition:
    """Load a .compose.yaml file and parse it into a Composition.

    `variable_overrides`: see `parse_widget_tree`
    ([DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE])."""
    with open(path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"not a .compose.yaml mapping: {path}")
    # The scene's own directory is the anchor for `components_from:` — passed in rather
    # than derived later, because the import must resolve BEFORE the tree is built.
    comp = parse_widget_tree(config, base_dir=os.path.dirname(os.path.abspath(path)),
                              variable_overrides=variable_overrides)
    # [CXC-R-ENGINE-ASSETS] remember the scene's own directory so bundled
    # RELATIVE assets resolve independent of the launch cwd.
    comp.scene_dir = os.path.dirname(os.path.abspath(path))
    return comp


# ===========================================================================
# Widget registry population ([SPEC:CXC-CONSOLE-PROD] [CXC-ICP-FOUND-B])
# ===========================================================================
#
# The SINGLE definition site of the ``type:``->widget-class map: every scene
# ``type:`` reachable through ``build_leaf`` above, made explicit + descriptor-
# bearing so the console personalization surfaces (S4 selector, S5 Dashboard
# palette, S6) can LIST and SELECT widgets without a hardcoded list.  Adding a
# scene type to ``build_leaf`` above WITHOUT a row here is a registry gap the
# ``test_widget_registry`` coverage assertion catches.  ``build`` stays None:
# the shipped types keep their audited bespoke branch; a NEW pluggable widget
# supplies a ``build`` hook in its own descriptor (the S6 seam) — the ONLY way
# to add a widget without editing ``build_leaf``.
#
# Fields per row: (primary_type, cls, display_name, required_namespaces,
#                  category, extra_category_tags, aliases).
_REGISTRY_ROWS = [
    # ── Collaboration-tab widgets ──────────────────────────────────────────
    ("chatthread", ChatThreadWidget, "Message Thread", [],
     "collaboration", (), ("chat_thread",)),
    ("activity", ActivityWidget, "Activity", ["self/activity"],
     "collaboration", (), ()),
    ("subagentledger", SubagentLedgerWidget, "Subagent Ledger",
     ["self/subagents"], "collaboration", (), ("subagent_ledger",)),
    ("selfledger", SelfLedgerWidget, "Self Ledger", [],
     "collaboration", (), ("self_ledger",)),
    ("registeredchecklist", RegisteredChecklistWidget, "Registered Checklist",
     ["self/registered"], "collaboration", (), ("registered_checklist",)),
    ("nightwatchchecklist", NightWatchChecklistWidget, "Night Watch Checklist",
     ["fun/nightwatch/tasks"], "collaboration", (),
     ("nightwatch_checklist", "task_checklist", "taskchecklist")),
    ("nightwatchartifacts", NightWatchArtifactsWidget, "Night Watch Artifacts",
     ["fun/nightwatch/artifacts"], "collaboration", (),
     ("nightwatch_artifacts",)),
    ("decisionsgraph", DecisionsGraphWidget, "Decisions Graph",
     ["self/decisions"], "collaboration", (), ("decisions_graph",)),
    ("decisionstab", DecisionsTabWidget, "Decisions Explorer",
     ["self/decisions"], "collaboration", ("dashboard-eligible",),
     ("decisions_tab",)),
    # [SPEC:CXC-GRAPH-VIEWER] the corpus-agnostic Graph Viewer — DATA is a direct
    # lts-engine query (no bus data feed); the CONTROL intents `_app/graph/*` stay.
    ("graphviewer", GraphViewerWidget, "Graph Viewer",
     ["_app/graph/anchor", "_app/graph/refresh"], "collaboration",
     ("dashboard-eligible",), ("graph_viewer",)),
    # [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D4: the compact rail-fitting decisions
    # card (region_affinity {rail}; min_size in _PLACEMENT below).  The FIRST
    # consumer of the fit-aware model — after this build the human adds it to the
    # rail via the collaboration config toggle.
    ("decisionssummary", DecisionsSummaryWidget, "Decisions Summary",
     ["self/decisions"], "collaboration", (), ("decisions_summary",)),
    # [SPEC:CIO-DEFECT-REGISTRY] the Dashboard Defects card — the process-
    # improvement roll-up (dashboard-first; region_affinity {dashboard} in
    # _PLACEMENT steers it onto the composable Dashboard grid).
    ("defectssummary", DefectsSummaryWidget, "Defects Summary",
     ["self/architect/defects"], "dashboard-eligible", (), ("defects_summary",)),
    ("mermaid", MermaidWidget, "Mermaid Diagram", ["thread/diagram"],
     "collaboration", ("dashboard-eligible",), ()),
    ("deliverablealert", DeliverableAlertWidget, "Deliverable Alert", [],
     "collaboration", (), ("deliverable_alert",)),
    ("deliverablehistory", DeliverableHistoryWidget, "Deliverables History",
     ["self/deliverables"], "collaboration", (), ("deliverable_history",)),
    ("projectsview", ProjectsViewWidget, "Projects", ["self/projects"],
     "collaboration", (), ("projects_view",)),
    ("awaywatchview", AwayWatchViewWidget, "Away Watch History",
     ["self/away_watches"], "collaboration", (), ("away_watch_view",)),
    # ── Rail cards ─────────────────────────────────────────────────────────
    ("intelligencecard", IntelligenceCardWidget, "Intelligence Card",
     ["self/context_pct"], "rail", ("dashboard-eligible",),
     ("intelligence_card",)),
    ("accountcard", AccountCardWidget, "Account Card", ["self/rate_limit"],
     "rail", ("dashboard-eligible",), ("account_card",)),
    ("deliverablecard", DeliverableCardWidget, "Deliverables Card",
     ["self/deliverables"], "rail", ("dashboard-eligible",),
     ("deliverable_card",)),
    ("ltsenginerail", LtsEngineRailWidget, "LTS Engine Rail",
     ["self/lts_engine/status"], "rail", (), ("lts_engine_rail",)),
    ("avatar", AvatarWidget, "Avatar", [], "rail", ("dashboard-eligible",), ()),
    ("linkhealth", LinkHealthWidget, "Link Health",
     ["intelligence/architect/channel/alive"], "rail", (), ("link_health",)),
    ("consoletabs", ConsoleTabWidget, "Console Tabs", [], "rail", (),
     ("console_tabs",)),
    # ── Configuration-tab surfaces ─────────────────────────────────────────
    ("configsettings", ConfigSettingsWidget, "Configuration Settings", [],
     "config", (), ("config_settings",)),
    ("bootstrapmanifest", BootstrapManifestWidget, "Bootstrap Manifest",
     ["self/meta"], "config", (), ("bootstrap_manifest",)),
    ("agentsskills", AgentsSkillsWidget, "Agents / Skills", [], "config", (),
     ("agents_skills",)),
    ("masterdetail", MasterDetailWidget, "Master / Detail", [], "config", (),
     ("master_detail",)),
    # [SPEC:CXC-CONSOLE-PROD] the Config selectors + S4/S5 app surfaces.
    ("consoleprefs", ConsolePrefsWidget, "Appearance", [], "config", (),
     ("console_prefs",)),
    ("collab_select", WidgetSelectWidget, "Collaboration Widgets", [], "config",
     (), ("collabselect",)),
    ("dashboard", DashboardWidget, "Dashboard", [], "config", (), ()),
    ("docbrowser", DocBrowserWidget, "Document Browser", [], "config", (),
     ("doc_browser",)),
    ("docpicker", DocPickerWidget, "Document Picker", [], "config", (),
     ("doc_picker",)),
    ("storebrowser", StoreBrowserWidget, "Storage Browser", [], "config", (),
     ("store_browser",)),
    ("modelpicker", ModelPickerWidget, "Model Picker", [], "config", (),
     ("model_picker",)),
    ("themetoggle", ThemeToggleWidget, "Theme Toggle", [], "config", (),
     ("theme_toggle",)),
    # ── Dashboard-eligible controls / readouts ─────────────────────────────
    ("gauge", GaugeWidget, "Gauge", [], "dashboard-eligible", (), ()),
    ("relay", RelayWidget, "Relay", [], "dashboard-eligible", (), ()),
    ("button", ButtonWidget, "Button", [], "dashboard-eligible", (), ()),
    ("emit_button", EmitButtonWidget, "Emit Button", [], "dashboard-eligible",
     (), ("emitbutton",)),
    ("command", CommandWidget, "Command Button", [], "dashboard-eligible",
     (), ()),
    ("checkbox", CheckboxWidget, "Checkbox", [], "dashboard-eligible", (), ()),
    ("textinput", TextInputWidget, "Text Input", [], "dashboard-eligible", (),
     ("text_input",)),
    ("powertoggle", PowerToggleWidget, "Power Toggle", ["bringup/plug"],
     "dashboard-eligible", (), ("power_toggle",)),
    ("busmonitor", BusMonitorWidget, "Bus Monitor", [], "dashboard-eligible",
     (), ("bus_monitor",)),
    ("coordinate", CoordinateWidget, "Coordinate Entry", [],
     "dashboard-eligible", (), ("coordinateentry", "coordinate_entry")),
    ("markdownviewer", MarkdownViewerWidget, "Markdown Viewer", [],
     "dashboard-eligible", (), ("markdown_viewer",)),
    ("media", MediaWidget, "Media", [], "dashboard-eligible", (), ()),
    # ── Layout / graphic primitives ────────────────────────────────────────
    ("label", LabelWidget, "Label", [], "primitive", (), ()),
    ("rectangle", RectangleWidget, "Rectangle", [], "primitive", (), ("rect",)),
    ("circle", CircleWidget, "Circle", [], "primitive", (), ()),
    ("led", LEDWidget, "LED", [], "primitive", (), ()),
    ("callout", CalloutWidget, "Callout", [], "primitive", (), ()),
    ("image", ImageWidget, "Image", [], "primitive", ("dashboard-eligible",),
     ()),
    ("lottie", LottieWidget, "Lottie Animation", [], "primitive",
     ("dashboard-eligible",), ()),
    ("svg", SvgWidget, "SVG", [], "primitive", ("dashboard-eligible",), ()),
    ("launch_tile", LaunchTileWidget, "Launch Tile", [], "primitive", (),
     ("launchtile",)),
    ("hookup", HookupWidget, "Hookup Guide", [], "primitive",
     ("dashboard-eligible",), ()),
    ("touchviz", TouchVizWidget, "Touch Visualizer", [], "primitive", (),
     ("touch_viz",)),
    ("tab", TabWidget, "Tab Container", [], "primitive", (),
     ("tabcontainer", "tab_container")),
    ("logic", LogicWidget, "Logic Node", [], "primitive", (), ()),
    # Generic selector widgets promoted out of the BSG project to the publishable
    # engine core.  Canonical tokens are project-neutral; the former BSG tokens
    # (``cic_tab_row``/``cictabrow``, ``colonial_radio_buttons``) stay as ALIASES
    # so existing scenes render unchanged.  Both carry a ``build`` hook (attached
    # post-loop) that wires the index-namespace subscription.
    ("segmented_tab_row", SegmentedTabRowWidget, "Segmented Tab Row", [],
     "primitive", (), ("cic_tab_row", "cictabrow")),
    ("radio_button_group", RadioButtonGroupWidget, "Radio Button Group", [],
     "dashboard-eligible", (), ("colonial_radio_buttons",)),
    # [CXC-SEVEN-SEG] a generic seven-segment digital readout; its ``build``
    # hook (attached post-loop) wires the color/value namespace subscriptions
    # and registers the SVG art as prefetchable assets.
    ("seven_segment", SevenSegmentDisplay, "Seven-Segment Display", [],
     "dashboard-eligible", (), ("sevensegment", "seven_seg", "seg7")),
    # [CXC-PRIM-CAROUSEL] a live-tile-hosting cover-flow; its ``build`` hook is
    # attached post-loop (below) — the row keeps it the single authoring site.
    ("carousel", CarouselWidget, "Carousel", [], "primitive",
     ("dashboard-eligible",), ()),
    # [SPEC:CXC-CIC] the CIC console shell + its generalized tab row are NOT
    # engine-core widgets — they are PROJECT-LOCAL to BSG ([CXC-PROJECT-WIDGETS])
    # and self-register from ``compose_projects/bsg/widgets/`` when a scene names
    # that module in ``widget_modules:``.  Deliberately absent from this table.
]

# [DEC-WIDGET-PLACEMENT-CALLS-SETTLED] D1 placement overlay: (min_size,
# region_affinity) keyed by canonical type.  min_size is the LOAD-BEARING
# generic fit test (a 332px rail rejects anything wider); region_affinity is
# declared ONLY where curation matters — the area-hungry collaboration widgets
# that *could* squeeze into some region but belong on the hero/tab/dashboard.
# A type ABSENT here keeps ((0, 0), None) — "fits anywhere", the shipped default
# for a widget that never declared a placement floor.  Widths are chosen against
# the 332px rail: a rail-fitting card is <= 332 wide, an area-hungry one is not.
_PLACEMENT = {
    # ── Collaboration catalog — area-hungry (steered off the 332px rail) ──
    "chatthread":        ((520, 360), frozenset({"hero", "tab"})),
    "decisionsgraph":    ((480, 340), frozenset({"hero", "dashboard", "tab"})),
    "decisionstab":      ((560, 400), frozenset({"tab", "dashboard"})),
    "mermaid":           ((420, 300), frozenset({"hero", "dashboard", "tab"})),
    "deliverablehistory": ((520, 360), frozenset({"tab", "dashboard"})),
    "projectsview":      ((520, 360), frozenset({"tab", "dashboard"})),
    # ── Collaboration catalog — rail-fitting compact cards (no curation) ──
    "activity":          ((260, 90), None),
    "subagentledger":    ((300, 150), None),
    "selfledger":        ((300, 150), None),
    "registeredchecklist": ((300, 140), None),
    "nightwatchchecklist": ((300, 150), None),
    "nightwatchartifacts": ((300, 150), None),
    "deliverablealert":  ((300, 60), None),
    "awaywatchview":     ((300, 240), None),
    "decisionssummary":  ((300, 150), frozenset({"rail"})),
    # [SPEC:CIO-DEFECT-REGISTRY] dashboard-first: affine to the composable grid,
    # sized for it (steered off the 332px rail by both min-width and affinity).
    "defectssummary":    ((360, 220), frozenset({"dashboard"})),
    # ── Rail cards ──
    "intelligencecard":  ((300, 200), None),
    "accountcard":       ((300, 150), None),
    "deliverablecard":   ((300, 200), None),
    "ltsenginerail":     ((300, 120), None),
    "avatar":            ((120, 120), None),
    "linkhealth":        ((260, 88), None),
    "consoletabs":       ((300, 44), None),
    # ── Dashboard-eligible controls / readouts ──
    "gauge":             ((200, 160), None),
    "relay":             ((160, 80), None),
    "button":            ((120, 44), None),
    "emit_button":       ((160, 44), None),
    "command":           ((140, 44), None),
    "checkbox":          ((140, 40), None),
    "textinput":         ((200, 60), None),
    "powertoggle":       ((160, 80), None),
    "busmonitor":        ((320, 200), None),
    "coordinate":        ((240, 120), None),
    "markdownviewer":    ((300, 200), None),
    "media":             ((240, 180), None),
    "storebrowser":      ((360, 320), None),
    # [CXC-PRIM-CAROUSEL] a cover-flow wants width + a hero/tab/dashboard home.
    "carousel":          ((520, 320), frozenset({"hero", "tab", "dashboard"})),
    "sevensegment":      ((120, 80), None),
}

for _row in _REGISTRY_ROWS:
    _rtype, _rcls, _rname, _rns, _rcat, _rtags, _raliases = _row
    _mn, _aff = _PLACEMENT.get(canonical_key(_rtype), ((0, 0), None))
    register(WidgetDescriptor(
        type=_rtype, cls=_rcls, display_name=_rname,
        required_namespaces=list(_rns), category=_rcat,
        categories=_rtags, aliases=_raliases,
        min_size=_mn, region_affinity=_aff))


# ---------------------------------------------------------------------------
# Carousel ([CXC-PRIM-CAROUSEL]) — a live-tile-HOSTING widget, so it registers
# a ``build`` hook (the [CXC-ICP-FOUND-B] pluggable-construction seam) rather
# than a bespoke ``build_leaf`` branch: its tiles come from a ``tiles:`` list,
# not ``children:``, and a live-widget tile is built THROUGH ``build_node`` so
# its own bus subscriptions/bindings are collected by reference (never forked).
# ---------------------------------------------------------------------------

def _build_carousel(spec: dict, wid: str, comp: Composition) -> Widget:
    """Construct a CarouselWidget from a ``tiles:`` list ([CXC-PRIM-CAROUSEL-
    TILES]).  Each tile is a static ``{image:…}`` or a LIVE ``{widget:…}`` child
    built via ``build_node`` (so the live child's subscriptions/bindings land in
    ``comp`` — reuse-by-reference).  The ``goto_ns`` command channel is wired as
    a subscription ([CXC-PRIM-CAROUSEL-BIND])."""
    w = CarouselWidget(
        wid,
        spacing=float(spec.get("spacing", 425)),
        tile_width=float(spec.get("tile_width", 500)),
        tile_height=float(spec.get("tile_height", 500)),
        center_scale=float(spec.get("center_scale", 1.0)),
        side_scale=float(spec.get("side_scale", 0.5)),
        snap_threshold=float(spec.get("snap_threshold", 0.15)),
        flick_velocity=float(spec.get("flick_velocity", 400)),
        snap_duration_ms=float(spec.get("snap_duration_ms", 300)),
        snap_easing=str(spec.get("snap_easing", "ease_out_cubic")),
        elastic_factor=float(spec.get("elastic_factor", 0.30)),
        tap_max_drift_px=float(spec.get("tap_max_drift_px", 15)),
        tap_max_duration_ms=float(spec.get("tap_max_duration_ms", 300)),
        wrap=bool(spec.get("wrap", False)),
        initial_index=int(spec.get("initial_index", 0)),
        show_dots=bool(spec.get("show_dots", True)),
        dot_active_color=spec.get("dot_active_color"),
        dot_inactive_color=spec.get("dot_inactive_color"),
        index_ns=str(spec.get("index_ns", "carousel/index")),
        goto_ns=str(spec.get("goto_ns", "carousel/goto")),
        activate_ns=str(spec.get("activate_ns", "carousel/activate")),
        position_ns=str(spec.get("position_ns", "carousel/position")))

    for tile in spec.get("tiles", []) or []:
        if not isinstance(tile, dict):
            continue
        target = str(tile.get("target", "") or "")
        label = str(tile.get("label", "") or "")
        if isinstance(tile.get("widget"), dict):
            # LIVE-WIDGET tile — build THROUGH build_node so its subscriptions
            # and bindings are collected into comp (reuse-by-reference).  A
            # construction failure falls back to a labeled swatch, never a
            # blank tile ([CXC-PRIM-CAROUSEL-TILES] preserve-and-flag).
            try:
                child: Widget = build_node(dict(tile["widget"]), comp)
            except Exception:
                logger.warning("[CXC-PRIM-CAROUSEL] live tile %r failed to "
                               "build; showing label swatch", label or target)
                child = ImageWidget(_uid("carousel_tile"), src="", alt=label)
        else:
            src = str(tile.get("image", "") or "")
            if src:
                comp.asset_uris.append(src)
            child = ImageWidget(_uid("carousel_tile"), src=src, fit="contain",
                                alt=label)
        w.add_tile(child, target=target, label=label)

    if w.goto_ns:
        comp.subscriptions.append((w.goto_ns, w))
    return w


# ---------------------------------------------------------------------------
# [SPEC:CXC-TERM] The terminal widget — a NEW widget is "its module + ONE
# descriptor with a ``build`` hook, no ``build_leaf`` edit" ([CXC-ICP-FOUND-B]
# S6).  Adding a build_leaf branch for it would be a review reject.
#
# The placement fields are LOAD-BEARING here ([CXC-TERM-WIDGET]): min_size
# (560, 380) is 40 columns of 12px Plex Mono plus padding, and the console's
# left rail is 332px — so the descriptor's own fit test steers the terminal off
# the rail with NO surface-specific special case.  region_affinity is
# explicitly hero/tab/dashboard and explicitly NOT rail.
# ---------------------------------------------------------------------------

def _build_terminal(spec: dict, wid: str, comp: Composition) -> Widget:
    """Construct a TerminalWidget from a scene node.  ``autostart`` defaults
    FALSE: a widget that spawned a child merely by appearing in a scene would
    spawn one on every headless capture ([CXC-TERM-WIDGET]).

    ``bootstrap`` is deliberately ABSENT-means-None, not absent-means-False: None
    defers to the environment, whose default is ON ([CXC-ICL-COLD]).  A scene that
    wants a blank instance must say ``bootstrap: false`` out loud.

    IDENTITY ([CXC-TERM-CHILD-ID], [DD-TERM-CHILD-ID-SCHEME] branch (a), ratified): the
    scene carries ``role`` PLUS a distinct ``instance_id``.  There is NO ``intelligence_id``
    scalar and no default id — a scene that omits ``instance_id`` gets a widget that REFUSES
    to spawn and says so on its plate, because the old default (``"architect"``) is exactly
    what handed a hosted child its host's identity.  ``intelligence_id`` is still ACCEPTED as
    an explicit ``instance_id`` for a scene that predates the split; it is not a default, and
    if it names the host's own id the spawn is refused before ``pty.fork()``."""
    from ..widgets.terminal import DEFAULT_TARGET_MODE, TerminalWidget
    boot = spec.get("bootstrap")
    role = str(spec.get("role", "") or "").strip()
    instance = str(spec.get("instance_id", "") or spec.get("intelligence_id", "") or "").strip()
    w = TerminalWidget(
        wid,
        role=role or instance,
        instance_id=instance,
        claude_bin=spec.get("claude_bin"),
        target_mode=str(spec.get("target_mode", DEFAULT_TARGET_MODE)),
        resume_session=str(spec.get("resume_session", "") or ""),
        autostart=bool(spec.get("autostart", False)),
        cwd=spec.get("cwd"),
        bootstrap=None if boot is None else bool(boot),
        meta_manifest=spec.get("meta_manifest"),
        scrollback_lines=int(spec.get("scrollback_lines", 2000)),
        font_px=float(spec.get("font_px", 12.0)),
    )
    # The /compact relay rides the SAME subscription seam every other widget
    # uses, so the actuation is a bus fact, not a private socket.
    comp.subscriptions.append((f"intelligence/{w.intelligence_id}/out", w))
    return w


from ..widgets.terminal import TerminalWidget as _TerminalWidget   # noqa: E402

register(WidgetDescriptor(
    type="terminal", cls=_TerminalWidget, display_name="Terminal Session",
    required_namespaces=[], category="collaboration",
    categories=("collaboration",), aliases=("terminal_session", "pty"),
    build=_build_terminal, min_size=(560, 380),
    region_affinity=frozenset({"hero", "tab", "dashboard"})))


# Attach the pluggable build hook to the carousel descriptor authored in
# _REGISTRY_ROWS (keeps the single authoring surface — one row per descriptor —
# while carrying the [CXC-ICP-FOUND-B] ``build`` seam for tile hosting).
_carousel_desc = get_descriptor("carousel")
if _carousel_desc is not None:
    _carousel_desc.build = _build_carousel


# ---------------------------------------------------------------------------
# Generic selector widgets (promoted engine-core): a standalone
# ``segmented_tab_row`` / ``radio_button_group`` subscribes its index namespace
# so a rotary/Intelligence publish moves the indicator — the same publish/
# subscribe seam a hosting shell wires.  Attached as ``build`` hooks (the
# [CXC-ICP-FOUND-B] pluggable-construction seam) since neither has a bespoke
# ``build_leaf`` branch.  The BSG tokens resolve here via the descriptor aliases.
# ---------------------------------------------------------------------------

def _build_segmented_tab_row(spec: dict, wid: str, comp: Composition) -> Widget:
    """A standalone segmented tab row — equal-width tabs with one of the two
    indicator styles, subscribing its index namespace so a peer publish moves
    the indicator (control-is-a-peer).  A hosting shell that needs injected SVG
    indicator artwork constructs :class:`SegmentedTabRowWidget` directly with
    prepared ``art_*`` nodes; this config path is the programmatic-indicator
    form."""
    base = _color_tuple(spec.get("base_color")) or (0x76, 0x24, 0x00, 0xFF)
    hi = _color_tuple(spec.get("highlight_color")) or (0xCD, 0x67, 0x1D, 0xFF)
    fill = _color_tuple(spec.get("fill_color"))
    w = SegmentedTabRowWidget(
        wid,
        labels=spec.get("labels") or [],
        selected_indicator=str(spec.get("selected_indicator",
                                        "filled-background")),
        fill_color=fill,                       # type: ignore[arg-type]
        fill_opacity=spec.get("fill_opacity"),
        index_namespace=str(spec.get("index_namespace", "")),
        base_color=base, highlight_color=hi,
        font_size=float(spec.get("font_size", 18.0)))
    if w.index_namespace:
        comp.subscriptions.append((w.index_namespace, w))
    return w


def _build_radio_button_group(spec: dict, wid: str, comp: Composition) -> Widget:
    """A standalone radio-button group — configurable-N stadium selectors with
    one highlighted selection, subscribing its index namespace so a peer publish
    moves the selection (control-is-a-peer)."""
    base = _color_tuple(spec.get("base_color")) or (0x76, 0x24, 0x00, 0xFF)
    hi = _color_tuple(spec.get("highlight_color")) or (0xCD, 0x67, 0x1D, 0xFF)
    kw: Dict[str, Any] = dict(
        labels=spec.get("labels") or [],
        selected_index=int(spec.get("selected_index", 0)),
        orientation=str(spec.get("orientation", "horizontal")),
        base_color=base, highlight_color=hi,
        index_namespace=str(spec.get("index_namespace", "")))
    for opt in ("padding", "button_width", "button_height", "font_size"):
        if spec.get(opt) is not None:
            kw[opt] = float(spec[opt])
    tc = _color_tuple(spec.get("text_color"))
    if tc is not None:
        kw["text_color"] = tc
    uf = _color_tuple(spec.get("unselected_fill"))
    if uf is not None:
        kw["unselected_fill"] = uf
    w = RadioButtonGroupWidget(wid, **kw)
    if w.index_namespace:
        comp.subscriptions.append((w.index_namespace, w))
    return w


def _build_seven_segment(spec: dict, wid: str, comp: Composition) -> Widget:
    """A generic seven-segment display ([CXC-SEVEN-SEG]).  Loads the segment
    (and optional colon) SVG art, subscribes the color/value namespaces so a
    peer/Intelligence publish re-colors or re-values the readout, and drives a
    clock off the clocked-widget substrate when ``clock: true``."""
    w = SevenSegmentDisplay(
        wid,
        value=str(spec.get("value", "")),
        src=str(spec.get("src", "")),
        colon_src=str(spec.get("colon_src", "")),
        lit_color=spec.get("lit_color"),
        dim_color=spec.get("dim_color"),
        color_namespace=str(spec.get("color_namespace", "")),
        value_namespace=str(spec.get("value_namespace", "")),
        digits=int(spec.get("digits", 0)),
        spacing=float(spec.get("spacing", 0.18)),
        clock=spec.get("clock", False),
        clock_format=str(spec.get("clock_format", "%H:%M:%S")),
        width=float(spec.get("width", 0.0)),
        height=float(spec.get("height", 120.0)))
    if spec.get("src"):
        comp.asset_uris.append(str(spec["src"]))
    if spec.get("colon_src"):
        comp.asset_uris.append(str(spec["colon_src"]))
    if w.color_namespace:
        comp.subscriptions.append((w.color_namespace, w))
    if w.value_namespace:
        comp.subscriptions.append((w.value_namespace, w))
    return w


_seg_desc = get_descriptor("segmented_tab_row")
if _seg_desc is not None:
    _seg_desc.build = _build_segmented_tab_row
_radio_desc = get_descriptor("radio_button_group")
if _radio_desc is not None:
    _radio_desc.build = _build_radio_button_group
_sevenseg_desc = get_descriptor("seven_segment")
if _sevenseg_desc is not None:
    _sevenseg_desc.build = _build_seven_segment
