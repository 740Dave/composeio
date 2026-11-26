"""
ComposeUX Render Runtime (M0+M1)

The event loop that turns a .compose.yaml Composition into a live,
bus-bound, interactive surface:

    load config -> parse widget tree -> register namespaces on the
    ComposeIO bus -> mount widgets (ctx: bus, assets, animator) ->
    loop: pump events -> hit-test/dispatch -> animate -> layout (if
    dirty) -> draw -> present -> frame-pace

Bus semantics ([CXC-R-C4]): a namespace subscription marks the
composition dirty; the very next frame redraws — "widget updates within
one frame".  Every user interaction is ALSO published to
`_app/render/interaction` ([CXC-R-CONTROL]) so an Intelligence observes
taps exactly as a human sees them.

Headless mode renders the identical tree to the raster reference
backend for pixel verification and CI.

Headless WIDGETS ([SPEC:CXC-HEADLESS] [CXC-HW-RUNTIME]) are a further
cut: the same app with NO canvas at all (``headless=True``) — the
per-frame evaluation with the draw step removed.  ``tick(t)`` is the
``render_frame_at(t)`` analog minus the canvas: drain updates (apply
bindings) -> advance behaviors at logical time *t* -> publish changed
output namespaces (the widget's declared ``outputs:``).  One tick
primitive; the three clock sources (continuous / periodic /
bus-triggered) live in engine/headless.py.
"""

from __future__ import annotations

import json
import logging
import math
import os
import queue
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from composeio.bus import ComposeIOBus
from composeio.namespace import Scope

from ..canvas import Canvas
from ..widget import Constraints, Rect, Widget
from .animation import Animator
from .assets import AssetManager
from .behaviors import BehaviorEngine
from .states import StateEngine
from .damage import DamageRegion
from .bindings import (AttrBinding, apply_binding, apply_expr_binding,
                       match_when)
from .dsl import DslEvalError, EvalContext
from .focus import FocusManager
from .geometry import GeometryWriteThrottle as _GeometryThrottle
from .layout import Container, OrderedFlowContainer
from .parser import Composition, parse_compose_yaml, parse_widget_tree

logger = logging.getLogger("composeio.render.runtime")

INTERACTION_NS = "_app/render/interaction"

def _deliver_bus_state(widget: Any, namespace: str, value: Any) -> None:
    """Hand a bus value to a widget — THE seam where `UNKNOWN` is withheld
    ([CXC-PRIM-STALE]).

    ⛔ WHY WITHHOLDING IS THE RIGHT CALL, AND NOT A DODGE.  52 widgets
    implement on_bus_state; 22 of them wrap a coercion in an
    `except (TypeError, ValueError)`, and `UnknownValueError` SUBCLASSES
    TypeError — so handing them the unknown singleton produces the exact
    defect this change exists to fix: the handler catches the refusal, passes,
    and the widget keeps rendering the stale number with nothing reported.
    The remaining 30 are worse in a different direction: a handler doing
    `value.strip()` or `int(value)` OUTSIDE a guard raises inside a bus
    callback.

    Fixing 52 handlers one at a time is opt-in wearing a different hat: it is
    52 chances to forget, plus one for every widget written after today.
    Withholding at the single seam every subscription passes through makes the
    RETENTION DELIBERATE — the widget keeps its last value, which is what an
    operator wants ("it was 3, forty seconds ago") — and the engine's central
    treatment makes that retention unmissable.

    A widget that can render unknown NATIVELY opts in with
    ``wants_unknown = True`` and receives the singleton unchanged.
    """
    from composeio.freshness import is_unknown

    if is_unknown(value) and not getattr(widget, "wants_unknown", False):
        return
    widget.on_bus_state(namespace, value)


# [CXC-PRIM-STALE] how often the wall-clock loop asks the bus for a freshness
# verdict.  Four times a second: the badge's age is formatted to whole seconds
# so nothing finer is renderable, and a per-frame poll would compute 60 verdicts
# a second for a caption that can change once.
FRESHNESS_POLL_INTERVAL_S = 0.25

# ---------------------------------------------------------------------------
# Window launch placement + console bbox ([DEC-LAUNCH-PLACEMENT], David
# 2026-07-22).  A launched window (Away/Night Watch, doc-viewer) must open
# BESIDE the console and come to the FRONT, not centered-behind.
#
#   * The console PUBLISHES its own screen bbox on CONSOLE_BBOX_NS so any
#     launcher always knows the CURRENT console position (better than
#     stuffing coords into each click intent — one publisher, every
#     launcher).  Mirrors the _app/away_watch/open path: an undeclared,
#     no-local-subscriber namespace auto-resolves to GLOBAL scope and rides
#     MQTT, so the out-of-process launchers receive it.
#   * A launched window RECEIVES its origin + raise flag via ENV VARS the
#     launcher sets on the spawned process — the cleanest process-boundary
#     handoff (no scene rewrite, no CLI plumbing through the spawn string).
#     COMPOSEIO_WINDOW_X also doubles as the "I am a launched child, not the
#     console" sentinel that suppresses this window's own bbox publish.
# [CXC-FC-S4-BBOX] the topic is DERIVED PER APP (`_app/<app_id>/bbox`), never a constant.  It
# was `"_app/console/bbox"` here, which meant every scene the engine launched published its
# geometry under the name `console`: four apps clobbering one topic at 2 Hz, and the placement
# consumer reading whichever ticked last.  `CONSOLE_BBOX_NS` is KEPT as the console's own
# resolved topic so existing importers (the launch-placement tests) keep working, but nothing
# publishes to it by default any more — `RenderApp` resolves `app_bbox_ns(self.comp.app_id)`.
from composeio.app_topics import app_bbox_ns, console_bbox_ns  # noqa: E402

CONSOLE_BBOX_NS = console_bbox_ns()
_BBOX_HEARTBEAT_S = 2.0          # re-publish at least this often (late subscribers)


def _launch_window_opts() -> Tuple[Optional[Tuple[int, int]], bool]:
    """Read this process's launch placement from the environment
    ([DEC-LAUNCH-PLACEMENT]).  Returns ``(pos, raise_to_front)`` where *pos*
    is ``(x, y)`` screen origin or None (keep OS-centered).  A launcher sets
    COMPOSEIO_WINDOW_X / COMPOSEIO_WINDOW_Y (ints) and COMPOSEIO_WINDOW_RAISE
    (truthy) on the spawned runtime; a normally-launched console sets none."""
    xs = os.environ.get("COMPOSEIO_WINDOW_X")
    ys = os.environ.get("COMPOSEIO_WINDOW_Y")
    pos: Optional[Tuple[int, int]] = None
    if xs is not None and ys is not None:
        try:
            pos = (int(xs), int(ys))
        except ValueError:
            pos = None
    raise_to_front = os.environ.get(
        "COMPOSEIO_WINDOW_RAISE", "").strip().lower() in ("1", "true", "yes", "on")
    return pos, raise_to_front

# Back-compat alias (pre-[SPEC:CXC-BIND] name; logic now in bindings.py).
_visible_match = match_when


# Gesture thresholds per [CXC-R-ENGINE-TOUCH]
_TAP_MAX_MS = 300
_TAP_MAX_MOVE = 10.0
_LONG_PRESS_MS = 500
# [CXC-R-ENGINE-HIT] double-click: a second tap on the SAME widget within this
# window (and within _TAP_MAX_MOVE of the first) synthesizes on_double_click.
_DOUBLE_CLICK_MS = 400

# Single-touch swipe thresholds ([SPEC:CXC-GESTURE] [CXC-GEST-SWIPE]).
# A down->move->up is *additionally* classified as a directional swipe when
# the travel exceeds _SWIPE_MIN_DISTANCE within _SWIPE_MAX_MS, the flick is
# fast enough (_SWIPE_MIN_VELOCITY), and the path is dominantly axial
# (dominant-axis travel >= _SWIPE_AXIS_DOMINANCE x the cross-axis travel).
# Sub-threshold sequences stay tap/long-press/pan (M1) — swipe is purely
# additive and never suppresses those.  Defaults are tuned to fire on both a
# desktop mouse-drag flick and a resistive-touch (CYD XPT2046) flick.
_SWIPE_MIN_DISTANCE = 40.0      # logical px of total travel
_SWIPE_MAX_MS = 600.0          # a flick completes quickly; a slow drag is a pan
_SWIPE_MIN_VELOCITY = 0.12     # px per ms (~120 px/s)
_SWIPE_AXIS_DOMINANCE = 1.5    # dominant : cross-axis travel ratio
_SWIPE_DIRECTIONS = ("up", "down", "left", "right")


def _classify_swipe(st: Dict[str, Any], up_x: float, up_y: float,
                    elapsed_ms: float) -> Optional[str]:
    """Classify a finished single-touch drag as a 4-way swipe, or None
    ([CXC-GEST-SWIPE]).  Pure function of the down point (st["x"]/["y"]),
    the up point, and the elapsed time — no engine state, so it is trivially
    testable and identical on every backend that delivers pointer events.

    Returns one of _SWIPE_DIRECTIONS, or None when the sequence is not a
    swipe (too short, too slow, took too long, or not dominantly axial) —
    in which case it simply remains the pan it already was.
    """
    dx = up_x - st["x"]
    dy = up_y - st["y"]
    adx, ady = abs(dx), abs(dy)
    distance = math.hypot(dx, dy)
    if distance < _SWIPE_MIN_DISTANCE:
        return None
    # A slow, deliberate drag is a pan, not a flick.  elapsed_ms <= 0 only on
    # a degenerate/synthetic zero-duration sequence — treat that as an
    # infinitely fast flick (velocity check passes) rather than dividing by 0.
    if elapsed_ms > _SWIPE_MAX_MS:
        return None
    if elapsed_ms > 0 and (distance / elapsed_ms) < _SWIPE_MIN_VELOCITY:
        return None
    # Dominant axis + sign.  The dominant travel must beat the cross-axis
    # travel by _SWIPE_AXIS_DOMINANCE, else the path is too diagonal to name.
    if adx >= ady:
        if ady * _SWIPE_AXIS_DOMINANCE > adx:
            return None
        return "right" if dx > 0 else "left"
    if adx * _SWIPE_AXIS_DOMINANCE > ady:
        return None
    return "down" if dy > 0 else "up"


class EngineContext:
    """What a mounted widget can reach: bus, assets, animator, redraw,
    thread-safe update scheduling, and the optional host runner hooks."""

    def __init__(self, app: "RenderApp") -> None:
        self._app = app
        self.bus: ComposeIOBus = app.bus
        self.assets: AssetManager = app.assets
        self.animator: Animator = app.animator
        # Host BehaviorEngine: embedders (the Composer WW preview) may
        # register BehaviorInstances so previewed behaviors run on the
        # host animation loop ([CXC-BE-BINDING] live behavior preview).
        self.behaviors: BehaviorEngine = app.behaviors
        # Host StateEngine ([CXC-BIND-STATES]): the Composer WW preview
        # registers a widget's StateMachine so a previewed transition runs
        # live on the host loop, exactly as behaviors do.
        self.states: StateEngine = app.states
        # mcp_action runner: (tool, args, *, on_event) -> None.  Host-provided;
        # None => CommandWidgets render disarmed (mirrors RendererContext.invoke).
        self.invoke: Optional[Callable] = app.invoke
        # app-switch: (app_id) -> None (the ComposeUX launcher/shell primitive).
        self.launch: Optional[Callable] = app.launch
        # [SPEC:CXC-CONSOLE-VISUAL] scene design plumbing: the parsed
        # design.theme ([CXC-ICV-THEME]) and design.reduced_motion
        # ([CXC-ICV-MOTION]) travel to widgets on the mount context —
        # clocked widgets consult reduced_motion and render their REST
        # frame; theme-aware widgets adopt the token table at mount.
        self.theme: str = app.comp.theme
        self.reduced_motion: bool = bool(getattr(app.comp,
                                                 "reduced_motion", False))
        # [SPEC:CXC-TERM] · [CXC-TERM-WIDGET] headless safety.  TRUE when this app has
        # no live window — a headless runtime OR a capture/golden render.  A widget
        # whose mount would spawn a CHILD PROCESS (the terminal) must be able to tell:
        # forking a `claude` per reference image is how a capture suite becomes a fork
        # bomb.  Read-only and additive; every existing widget ignores it.
        self.headless: bool = bool(getattr(app, "headless", False)
                                   or not getattr(app, "windowed", False))

    def request_redraw(self, rect: Optional[Rect] = None) -> None:
        """Request a redraw ([CXC-R-DAMAGE]).  With a *rect* (a widget's
        paint bounds) the engine damages only that region; without one it
        falls back to a full-surface redraw (the conservative default for
        callers that cannot scope their change)."""
        if rect is None:
            self._app.mark_dirty()
        else:
            self._app.mark_damage(rect)

    def set_background(self, color: Any, grid: Optional[dict] = None,
                       wash: Optional[dict] = None) -> None:
        """Update the scene background pass at runtime ([CXC-ICV-CHROME] /
        [CXC-ICV-THEME]): ground color + optional grid/wash config.  The
        companion of the parse-time design.background* keys — the console
        theme toggle re-syncs the ground when the token table swaps."""
        comp = self._app.comp
        if color is not None:
            comp.background = tuple(color)
        comp.background_grid = grid
        comp.background_wash = wash
        self._app.mark_dirty()

    def register_clocked(self, widget: Any) -> None:
        """Register a widget for per-frame clock ticks ([SPEC:CXC-LOTTIE]
        [CXC-LOT-DETERMINISM]): its on_clock_tick(now) receives the SAME
        clock as behaviors — wall time in run(), the logical timestep in
        render_frame_at(t) — so time-based playback stays a pure function
        of the logical clock.  Unregister on unmount (stale-instance
        hygiene, the BehaviorEngine.remove convention)."""
        self._app.register_clocked(widget)

    def unregister_clocked(self, widget: Any) -> None:
        self._app.unregister_clocked(widget)

    def publish(self, namespace: str, value: Any, source: str = "widget") -> None:
        self._app.publish(namespace, value, source=source)

    def schedule_update(self, fn: Callable[[], None],
                        *, target: Optional[Any] = None) -> None:
        """Thread-safe handoff: run *fn* on the render thread before the
        next frame.  Bus/MQTT callbacks arrive on transport threads —
        stateful widgets (TextInput, BusMonitor) MUST mutate their state
        through this, never directly from the callback thread.  ``target``
        (the single widget this update affects, when known) scopes the
        resulting damage to that widget's card ([CXC-R-DAMAGE])."""
        self._app.schedule_update(fn, target=target)

    def request_stop(self) -> None:
        """Ask the host run-loop to close AFTER the current frame — the SAME
        clean shutdown a window-close ('X') triggers ([CXC-DSDV-SAVE] SAVE
        ANSWERS BUTTON).  Deferred by design: it only clears the run flag, so
        the widget event handler that called it returns safely and the loop's
        ``finally: self.stop()`` does the real teardown (unmount the tree —
        which runs the save-on-close fallback — and close the window).  A
        widget primitive (the 'Save Answers' button drives it); off a live
        run-loop (headless / a bounded render) it is a harmless flag flip."""
        self._app._running = False


class RenderApp:
    """Config-driven render engine application.

    Usage:
        app = RenderApp.from_file("dashboard.compose.yaml")
        app.run()                      # live window until closed

        app = RenderApp.from_file(path, windowed=False)
        app.render_headless("/tmp/ref.png")   # pixel-verification render

        app = RenderApp(comp, headless=True, bus=shared_bus)
        app.start(); app.tick(0.0)     # headless widget ([CXC-HW-RUNTIME])
    """

    def __init__(self, composition: Composition, *,
                 windowed: bool = True,
                 fullscreen: bool = False,
                 window_scale: float = 1.0,
                 fps: float = 60.0,
                 bus: Optional[ComposeIOBus] = None,
                 connect_transports: bool = False,
                 headless: bool = False,
                 invoke: Optional[Callable] = None,
                 launch: Optional[Callable] = None) -> None:
        self.comp = composition
        # [SPEC:CXC-HEADLESS]: headless = engine minus the backend.  No
        # canvas is ever created; layout/draw/present are skipped; the
        # tick(t) primitive replaces render_frame_at(t).
        self.headless = headless
        self.windowed = False if headless else windowed
        self.fullscreen = fullscreen
        self._window_scale = window_scale
        self.frame_interval = 1.0 / fps
        self.invoke = invoke
        self.launch = launch

        # [CXC-R-ENGINE-ASSETS] seed the scene's own directory so a scene's
        # bundled RELATIVE assets resolve independent of the launch cwd.
        self.assets = AssetManager(base_dir=getattr(composition, "scene_dir", ""))
        self.animator = Animator()
        self.behaviors = BehaviorEngine()
        # [CXC-BIND-STATES] declarative states + transitions, sibling to the
        # BehaviorEngine and ticked on the same clock.  reduced_motion snaps.
        self.states = StateEngine(
            reduced_motion=bool(getattr(self.comp, "reduced_motion", False)))
        self.focus = FocusManager()

        self.bus = bus or self._build_bus(connect_transports)
        self._owns_bus = bus is None
        # [DEC-LAUNCH-PLACEMENT] console bbox publish gate + state.  The
        # console (windowed, transports on, NOT a launched child, not
        # explicitly disabled) advertises its screen bbox so launchers place
        # new windows beside it.  A launched window carries COMPOSEIO_WINDOW_X
        # (set by its launcher) and so suppresses its own bbox publish; a
        # no-transports window has no global leg to reach the launchers anyway.
        self._is_launched_window = "COMPOSEIO_WINDOW_X" in os.environ
        # [CXC-FC-S4-BBOX] DERIVED from the running app's id, so two apps up at once publish to
        # two distinct topics instead of racing on one.  The explicit env override is unchanged
        # and still wins — the seam already existed; only the default was a constant.
        self._bbox_ns = os.environ.get("COMPOSEIO_CONSOLE_BBOX_NS",
                                       app_bbox_ns(self.comp.app_id))
        self._publish_bbox = (
            self.windowed
            and connect_transports
            and not self._is_launched_window
            and os.environ.get("COMPOSEIO_CONSOLE_BBOX", "").strip() != "0")
        self._last_bbox: Optional[Tuple[int, int, int, int]] = None
        self._last_bbox_pub_t: float = 0.0

        self.canvas: Optional[Canvas] = None
        self._dirty = True
        # [CXC-R-DAMAGE] the frame's dirty region.  Starts FULL (the first
        # frame paints everything); every subsequent frame accumulates either
        # scoped rects (a keystroke -> the composer field; the avatar breathe
        # -> the presence card) or a full mark for the conservative cases
        # (bus-state changes, resize, expose).  render_dirty() reads this to
        # raster + present ONLY the damaged region instead of the whole
        # 1280x1300 surface.
        self._damage = DamageRegion()
        self._damage.add_full()
        self._running = False
        self._frame_count = 0
        self._pointer_state: Dict[int, dict] = {}   # pointer_id -> press info
        self._pointer_capture: Optional[Widget] = None
        # [CXC-R-ENGINE-HOVER] hover-cursor affordance: the SEMANTIC cursor
        # currently applied to the window ("default"|"pointer"|...).  The
        # MOUSEMOTION path resolves the widget under the pointer via the SAME
        # hit_test_tree the click path uses and lowers its hover_cursor() to a
        # system cursor — but ONLY on a CHANGE (this is the debounce that keeps
        # continuous motion over one widget from thrashing SetCursor).  A pure
        # hover READ: it never marks the scene dirty ([CXC-R-DAMAGE] — a cursor
        # swap is not a repaint).
        self._current_cursor: str = "default"
        # [CXC-R-ENGINE-HIT] double-click synthesis: the last committed tap as
        # (widget, monotonic_t, x, y); a fast second tap on the same widget
        # fires on_double_click instead of a second on_tap.
        self._last_tap: Optional[tuple] = None
        self._interaction_log: List[dict] = []
        # Clock-ticked widgets ([SPEC:CXC-LOTTIE]): receive the same clock
        # as behaviors each frame (deterministic under render_frame_at).
        self._clocked: List[Any] = []
        # [CXC-PRIM-STALE] staleness poll: widget -> the namespaces feeding it.
        # Rebuilt lazily when the wiring watermark moves (hot-add / launch
        # mount), so a widget added at runtime is judged like a scene one.
        self._fresh_map: Optional[List[tuple]] = None
        self._fresh_map_stamp: tuple = ()
        self._last_fresh_poll: float = 0.0
        # Thread-safe handoff queue: bus/runner callbacks (any thread) ->
        # render thread.  Drained once per frame before layout/draw.
        self._update_queue: "queue.SimpleQueue[tuple]" = queue.SimpleQueue()
        # [CXC-R-DAMAGE] targets drained THIS frame — each (widget_or_None,
        # pre-run rect).  Resolved to damage POST-layout by
        # _apply_drained_damage(): a targeted bus update scopes to its widget,
        # an untargeted one keeps the conservative full redraw.
        self._drained_targets: List[tuple] = []
        # Headless outputs ([CXC-HW-RUNTIME] "publish changed output
        # namespaces"): last published value per OutputDecl, so a tick
        # only publishes namespaces whose attribute actually changed.
        self._last_outputs: Dict[int, Any] = {}
        # [SPEC:CXC-DSL] tick context: dt = t - previous tick's t (0.0 on
        # the first tick), fed to output expressions ([CXC-DSL-STATE]).
        self._last_tick_t: Optional[float] = None
        # [SPEC:CXC-HMON] Diagnosis instrumentation — OPT-IN ([CXC-HMON-
        # C6]): default False leaves tick() on the exact untraced path
        # (goldens/deterministic drives byte-identical).  When True, each
        # tick(t) leaves its record on last_tick_record; the HOST
        # (HeadlessRuntime) owns ring-buffering + throttled publishing.
        self.trace_enabled: bool = False
        self.last_tick_record: Optional[dict] = None
        self.last_error: Optional[str] = None    # last output eval error
        # [CXC-ICP-REBIND] the mount context, retained at start() so widgets
        # added at RUNTIME (Dashboard hot-add) mount + wire against the live
        # engine.  None until start().
        self._engine_ctx: Optional[EngineContext] = None

        # [SPEC:CXC-DVL] standalone-window geometry persistence.  A scene that
        # opts into design.persist_geometry gets a durable store + a
        # write-on-change throttle; every other scene keeps a Null store (no
        # restore, no writes) so nothing changes for the console/dashboards.
        self._geometry_store, self._geometry_key = self._build_geometry_store()
        self._geometry_throttle = _GeometryThrottle(0.5)
        # which precedence tier this launch resolved to (persisted | launcher |
        # screen-origin | default) — the post-creation frame-offset pass keys
        # off it so it can never nudge a RESTORED position.
        self._geometry_source: str = ""
        self._restored_geometry: Any = None
        self._policy_work_area: Optional[Tuple[int, int, int, int]] = None

        self._register_namespaces()
        self._wire_subscriptions()

    # -- construction -----------------------------------------------------

    def _build_geometry_store(self) -> Tuple[Any, str]:
        """Construct this window's geometry store + identity key
        ([CXC-DVL-GEOMETRY]).  Off (Null store) unless the scene opts into
        ``design.persist_geometry``.  The store LOCATION is the architect's
        per-instance ruling ([DD-DVL-GEOMETRY-STORE]): a launcher points
        ``COMPOSEIO_GEOMETRY_STORE`` at the instance's meta.<id>.meta.json (the
        config_persister meta-object seam); a direct launch falls back to a
        per-app file under ~/.composeio so the viewer still remembers its
        geometry standalone.  Never raises — a bad path yields a Null store."""
        from .geometry import (JsonFileGeometryStore, NullGeometryStore)
        key = (self.comp.window_key or self.comp.app_id or "window").strip()
        if not getattr(self.comp, "persist_geometry", False) or self.headless:
            return NullGeometryStore(), key
        try:
            path = os.environ.get("COMPOSEIO_GEOMETRY_STORE")
            if not path:
                path = os.path.join(os.path.expanduser("~"), ".composeio",
                                    "window-geometry.json")
            return JsonFileGeometryStore(path), key
        except Exception:                               # never block launch
            return NullGeometryStore(), key

    def _record_geometry(self, *, final: bool = False) -> None:
        """Read the live window bounds and submit them to the write throttle
        ([CXC-DVL-GEOMETRY] WRITE-ON-CHANGE).  ``final=True`` (close /
        resize-end) flushes any pending write past the throttle."""
        from .geometry import NullGeometryStore, WindowGeometry
        if isinstance(self._geometry_store, NullGeometryStore):
            return
        get_bounds = getattr(self.canvas, "get_bounds", None)
        if get_bounds is None:
            return
        bounds = get_bounds()
        if bounds is None:
            return
        x, y, w, h = bounds
        geom = WindowGeometry(int(x), int(y), int(w), int(h))
        if not geom.is_valid:
            return
        if final:
            self._geometry_throttle.submit(geom, time.monotonic())
            self._flush_geometry()
        else:
            due = self._geometry_throttle.submit(geom, time.monotonic())
            if due is not None:
                self._geometry_store.save(self._geometry_key, due)

    def _poll_geometry(self, now: float) -> None:
        """Flush a throttle-pending geometry once the interval elapses (called
        per frame, [CXC-DVL-GEOMETRY])."""
        due = self._geometry_throttle.poll(now)
        if due is not None:
            self._geometry_store.save(self._geometry_key, due)

    def _flush_geometry(self) -> None:
        due = self._geometry_throttle.flush()
        if due is not None:
            self._geometry_store.save(self._geometry_key, due)

    def _default_window_origin(self) -> Optional[Tuple[int, int]]:
        """The first-launch policy origin ([CXC-DVL-GEOMETRY]): the top-left
        of the primary display's VISIBLE WORK AREA + a fixed inset — below the
        macOS menu bar, never OS-centered (David 2026-08-05).

        The work area comes from the backend's duck-typed
        ``display_usable_bounds`` affordance (SDL_GetDisplayUsableBounds), the
        same shape as the off-screen clamp's ``get_display_bounds`` but MINUS
        the menu bar / Dock.  It is read BEFORE the window exists, so it is
        the module-level helper, not a canvas method.  Any failure -> None ->
        the OS default placement (a display-less box never crashes here)."""
        from .geometry import default_origin
        try:
            from ..window import display_usable_bounds
            areas = display_usable_bounds()
        except Exception:
            return None
        # stashed for the post-creation frame-offset probe (which needs the
        # work-area CORNER, not the already-inset origin).
        self._policy_work_area = areas[0] if areas else None
        return default_origin(areas, (self.comp.width, self.comp.height))

    def _restore_geometry(self, launcher_pos: Optional[Tuple[int, int]]
                          ) -> Optional[Tuple[int, int]]:
        """Apply restore-on-launch precedence ([CXC-DVL-GEOMETRY]): read any
        persisted geometry, resolve it against the launcher pos, the
        first-launch work-area origin and the scene default, set the client
        SIZE on the composition (so the first layout uses the restored size),
        and return the origin for window creation (None = OS-centered).  The
        resolved geometry is stashed for the post-creation off-screen clamp.

        SIZE is restored only for a RESIZABLE scene.  A fixed-size window (the
        launch-bar ribbon) has no user-authored size to remember — its size is
        a SCENE fact, so a stale persisted size must never pin it to yesterday's
        dimensions after the scene grows.  Position is always restored."""
        from .geometry import NullGeometryStore, resolve_geometry
        self._restored_geometry = None
        self._geometry_source = ""
        if isinstance(self._geometry_store, NullGeometryStore):
            return launcher_pos
        try:
            persisted = self._geometry_store.load(self._geometry_key)
        except Exception:
            persisted = None
        # only a real window needs (and can measure) the work-area origin.
        default_pos = self._default_window_origin() if self.windowed else None
        geom, source = resolve_geometry(
            persisted, launcher_pos,
            (self.comp.width, self.comp.height),
            default_pos=default_pos)
        if source == "persisted":
            if self.comp.resizable:
                self.comp.width = int(geom.w)
                self.comp.height = int(geom.h)
            elif (int(geom.w), int(geom.h)) != (int(self.comp.width),
                                                int(self.comp.height)):
                # stale size on a fixed-size scene: keep the POSITION, restate
                # the scene's own size (so the off-screen clamp below reasons
                # about the window that will actually be created).
                from .geometry import WindowGeometry
                geom = WindowGeometry(geom.x, geom.y,
                                      int(self.comp.width),
                                      int(self.comp.height))
        self._restored_geometry = geom
        self._geometry_source = source
        return (geom.x, geom.y) if geom.has_pos else None

    def _window_decoration_offset(self) -> Tuple[int, int]:
        """``(dx, dy)`` — how far the window's CONTENT origin sits inside its
        visible FRAME (left border, title bar).  ``(0, 0)`` for a borderless
        surface or when it cannot be determined.

        Two routes, in order:
          1. ``get_border_size`` (SDL_GetWindowBordersSize) where the backend
             implements it — X11 and Windows do.
          2. A PROBE, because SDL's Cocoa backend does NOT (it returns -1, so
             macOS — the console tier's own platform — always lands here):
             ask for the content origin AT the work-area corner.  A WM that
             refuses to put a title bar under its own chrome hands back a
             content origin pushed down/right by exactly the decoration
             thickness, which is the number we want.  A WM that doesn't clamp
             returns the request unchanged -> (0, 0), i.e. content == frame,
             which is the correct answer there."""
        get_bounds = getattr(self.canvas, "get_bounds", None)
        set_pos = getattr(self.canvas, "set_position", None)
        borders = getattr(self.canvas, "get_border_size", None)
        b = borders() if borders is not None else None
        if b:
            try:
                top, left = int(b[0]), int(b[1])
            except (TypeError, ValueError, IndexError):
                top = left = 0
            if top > 0 or left > 0:
                return left, top
        area = getattr(self, "_policy_work_area", None)
        if not area or set_pos is None or get_bounds is None:
            return 0, 0
        try:
            wx, wy = int(area[0]), int(area[1])
            set_pos(wx, wy)
            probed = get_bounds()
        except Exception:
            return 0, 0
        if not probed:
            return 0, 0
        return (max(0, int(probed[0]) - wx), max(0, int(probed[1]) - wy))

    def _apply_policy_origin_frame_offset(self) -> None:
        """Land the WINDOW FRAME (title bar included) on the first-launch
        policy origin ([CXC-DVL-GEOMETRY]).

        SDL both places and reports the CONTENT rect, but "20 px from the
        top-left of the work area" is about what the user SEES.  On macOS the
        content sits ~28 px below the frame top AND the WM refuses to tuck a
        title bar under the menu bar, so creating at the policy origin lands
        the frame FLUSH against the menu bar (measured: asked for y=45, got
        content y=53 == menu-bar bottom + title bar) instead of 20 px below
        it.  Once the window exists we can measure the decoration and re-place
        by exactly that much.

        ONLY runs on the ``screen-origin`` path.  A RESTORED position is
        already in SDL's own content coordinates (it was saved by
        ``get_bounds``), so offsetting it would drift the window one title bar
        further down on every relaunch — that round-trip must stay exact."""
        if getattr(self, "_geometry_source", "") != "screen-origin":
            return
        geom = getattr(self, "_restored_geometry", None)
        if geom is None or not geom.has_pos:
            return
        set_pos = getattr(self.canvas, "set_position", None)
        if set_pos is None:
            return
        dx, dy = self._window_decoration_offset()
        # unconditional: the probe may have parked the window at the work-area
        # corner, so the final placement is always re-asserted.
        set_pos(int(geom.x) + dx, int(geom.y) + dy)

    def _clamp_restored_geometry(self) -> None:
        """After window creation, clamp a restored off-screen position onto a
        visible display and re-place the window if needed ([CXC-DVL-GEOMETRY]
        FAIL-SAFE)."""
        geom = getattr(self, "_restored_geometry", None)
        if geom is None or not geom.has_pos:
            return
        get_displays = getattr(self.canvas, "get_display_bounds", None)
        set_pos = getattr(self.canvas, "set_position", None)
        if get_displays is None:
            return
        from .geometry import clamp_onscreen
        displays = get_displays()
        if not displays:
            return
        clamped = clamp_onscreen(geom, displays)
        if (clamped.x, clamped.y) != (geom.x, geom.y) and set_pos is not None:
            set_pos(clamped.x, clamped.y)

    @classmethod
    def from_file(cls, path: str,
                  variable_overrides: Optional[Dict[str, Any]] = None,
                  **kwargs: Any) -> "RenderApp":
        """`variable_overrides` is the LAUNCH-source override map for the
        scene's document-level ``variables:`` block
        ([DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE]) — e.g. a physical
        console's station id. It is consumed at PARSE time (never reaches the
        RenderApp constructor), and omitting it renders exactly as before."""
        return cls(parse_compose_yaml(path, variable_overrides=variable_overrides),
                    **kwargs)

    @classmethod
    def from_dict(cls, config: dict,
                  variable_overrides: Optional[Dict[str, Any]] = None,
                  **kwargs: Any) -> "RenderApp":
        """`variable_overrides`: see `from_file`
        ([DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE])."""
        return cls(parse_widget_tree(config, variable_overrides=variable_overrides),
                    **kwargs)

    def _build_bus(self, connect_transports: bool) -> ComposeIOBus:
        """Build the bus, resolving each declared `transports:` entry via the
        TRANSPORT REGISTRY.

        This used to hardcode `if t.get("type") == "mqtt"`, which meant a scene
        could declare `type: can` / `uart` / `synthetic` and have it SILENTLY
        dropped — `global_transport` stayed None and the app ran local-only with
        no error (measured 2026-08-08 by a substitution test). The Transport ABC,
        seven implementations, and [BPR-TRANSPORT-CONTRACT] were all in place;
        only this factory made them unreachable, defeating the whole point that
        an app "shouldn't have to care how ComposeBus is instantiated".

        Now: an UNKNOWN type RAISES (a mis-typed `mqqt` is a startup error, not a
        mystery about missing telemetry); a KNOWN type whose backend is
        unreachable degrades LOUDLY to local-only, because a down broker is not a
        broken app.
        """
        global_transport = None
        if connect_transports:
            from composeio.transport_registry import build as build_transport
            for t in self.comp.transports:
                built = build_transport(t, app_id=self.comp.app_id)
                if built is not None:
                    global_transport = built
                    logger.info("transport: %s -> %s",
                                t.get("type"), type(built).__name__)
                    break
                logger.warning(
                    "transport %r declared but UNAVAILABLE — this app will run "
                    "LOCAL-ONLY (no bus traffic leaves the process).",
                    t.get("type"))
        return ComposeIOBus(bus_id=self.comp.app_id,
                            global_transport=global_transport)

    def _register_namespaces(self) -> None:
        from composeio.bus import is_pattern
        self._ns_initials: List[tuple] = []
        for ns_def in self.comp.namespaces:
            if isinstance(ns_def, dict) and ns_def.get("name"):
                if is_pattern(str(ns_def["name"])):
                    # a declared PATTERN subscription ([CXC-FL-PATTERN]) is
                    # not a namespace — members register themselves when
                    # they speak; nothing to create here.
                    continue
                # [DP-CIO-5] an optional scene `scope:` (local|global|remote|
                # auto) pins the namespace's scope; without it the bus auto-
                # resolves.  A SEND-only outbound topic (the console composer)
                # declares `scope: global` so its own reflection subscription
                # can't downgrade the publish to local and strand it in-process.
                _scope_str = ns_def.get("scope")
                _scope = Scope(str(_scope_str)) if _scope_str else Scope.AUTO
                # [SPEC:CXC-FRESHNESS] the DECLARED freshness contract, carried
                # from the scene to the receiver ([CXC-PRIM-STALE]).  Without
                # this forward a scene could not declare a cadence at all, and
                # the presentation would be reachable only for namespaces
                # declared somewhere else — which is how a mechanism ends up
                # correct and unreachable.
                _fresh = {}
                if ns_def.get("refresh_interval") is not None:
                    _fresh["refresh_interval"] = float(
                        ns_def["refresh_interval"])
                if ns_def.get("timeout") is not None:
                    _fresh["timeout"] = float(ns_def["timeout"])
                if ns_def.get("quiet_by_construction"):
                    _fresh["quiet_by_construction"] = True
                if _fresh:
                    # register_namespace honours a RE-declaration of the
                    # freshness contract; ensure_namespace does not carry it.
                    self.bus.register_namespace(
                        ns_def["name"],
                        engine_config=ns_def.get("state_engine", "json"),
                        label=ns_def.get("label"), scope=_scope, **_fresh)
                self.bus.ensure_namespace(
                    ns_def["name"],
                    engine_config=ns_def.get("state_engine", "json"),
                    label=ns_def.get("label"),
                    scope=_scope)
                # [SPEC:CXC-DSL] initial: — declarative state seeding.
                # A read-modify-write output expression needs the bus to
                # HOLD the state it integrates ([CXC-DSL-STATE] "the bus
                # is the state"); `initial:` publishes the declared value
                # at start() iff the namespace has no current value.
                if "initial" in ns_def:
                    self._ns_initials.append((str(ns_def["name"]),
                                              ns_def["initial"]))
        self.bus.ensure_namespace(INTERACTION_NS, engine_config="json",
                                  label="Render interaction events")

    def _wire_subscriptions(self) -> None:
        for ns_name, widget in self.comp.subscriptions:
            self._wire_subscription(ns_name, widget)
        # Declarative attribute bindings ([SPEC:CXC-BIND] [CXC-BIND-ATTR]).
        for b in self.comp.attr_bindings:
            self._wire_attr_binding(b)
        # State-driven behaviors ([CXC-BIND-BEHAVIOR]).
        for decl in self.comp.behaviors:
            self._wire_behavior(decl)
        # Declarative state machines ([CXC-BIND-STATES]).
        for sm in self.comp.state_machines:
            self._wire_state_machine(sm)
        # [CXC-ICP-LAUNCH-BIND] watermark the wired tail: a pre-start mount (the
        # console personalization launch-mount, applied AFTER __init__ but BEFORE
        # start()) appends past these counts; start() wires that delta so the
        # mounted widget binds live data, not blank-until-restart.
        self._wired_subs = len(self.comp.subscriptions)
        self._wired_attr = len(self.comp.attr_bindings)
        self._wired_beh = len(self.comp.behaviors)
        self._wired_states = len(self.comp.state_machines)

    def _wire_pending(self) -> None:
        """Wire any subscription/binding/behavior appended AFTER the initial
        wiring watermark — the pre-start mount delta ([CXC-ICP-LAUNCH-BIND]).
        Mirrors ``add_widget_live``'s hot-add re-bind (wire only the appended
        tail) so a launch-mounted widget reaches the bus by the identical seam a
        scene widget does.  Idempotent: advances the watermark as it wires."""
        for ns_name, w in self.comp.subscriptions[self._wired_subs:]:
            self._wire_subscription(ns_name, w)
        for b in self.comp.attr_bindings[self._wired_attr:]:
            self._wire_attr_binding(b)
        for decl in self.comp.behaviors[self._wired_beh:]:
            self._wire_behavior(decl)
        for sm in self.comp.state_machines[self._wired_states:]:
            self._wire_state_machine(sm)
        self._wired_subs = len(self.comp.subscriptions)
        self._wired_attr = len(self.comp.attr_bindings)
        self._wired_beh = len(self.comp.behaviors)
        self._wired_states = len(self.comp.state_machines)

    def _wire_subscription(self, ns_name: str, widget: Widget) -> None:
        """Wire ONE (namespace, widget) subscription to the live bus — the
        body shared by initial wiring and runtime hot-add ([CXC-ICP-REBIND]).
        Bus callbacks arrive on the transport (MQTT) thread, so the update is
        marshalled onto the render thread ([CXC-R-ENGINE] M2)."""
        from composeio.bus import is_pattern

        def _cb(ns: str, new_val: Any, old_val: Any,
                _w: Widget = widget) -> None:
            self.schedule_update(
                lambda n=ns, v=new_val, w=_w: _deliver_bus_state(w, n, v),
                target=_w)

        if is_pattern(ns_name):
            # [CXC-FL-PATTERN]: a widget listening to a FAMILY — on_bus_state
            # receives the actual member namespace, and members appearing
            # later are included by construction.
            self.bus.subscribe_pattern(ns_name, _cb)
            return
        self.bus.ensure_namespace(ns_name, engine_config="json")
        self.bus.subscribe(ns_name, _cb)
        # Deliver the current retained value so a HOT-ADDED widget lights up
        # with live state immediately, not blank-until-next-publish
        # ([CXC-ICP-REBIND]).  bus.subscribe does NOT replay to a fresh
        # subscriber, so a widget added after start() would otherwise stay
        # blank until the producer next publishes.  Gated on ``_running`` so
        # the INITIAL wiring (called from __init__, before start()) is
        # byte-identical to before — a scene's widgets receive state on the
        # next publish exactly as they always have.
        if self._running:
            current = self.bus.get_state(ns_name)
            if current is not None:
                _deliver_bus_state(widget, ns_name, current)

    def _wire_attr_binding(self, b: "AttrBinding") -> None:
        """Wire ONE attribute binding ([SPEC:CXC-BIND] [CXC-BIND-ATTR]): the
        M4 visible: binding generalized to opacity/rotation/scale/color/value
        attrs.  A namespace change updates the bound attribute within one
        frame ([CXC-BIND-C1])."""
        from composeio.bus import is_pattern

        if b.expr is not None:
            # [CXC-DSL-SITES] binding-value site: subscribe every namespace
            # the expression reads; ANY change re-evaluates the whole
            # expression against the live bus.
            for ns_name in b.expr.namespaces:
                self.bus.ensure_namespace(ns_name, engine_config="json")

                def _ecb(ns: str, new_val: Any, old_val: Any,
                         _b: AttrBinding = b) -> None:
                    self.schedule_update(
                        lambda: self._apply_expr_attr_binding(_b),
                        target=_b.widget)

                self.bus.subscribe(ns_name, _ecb)
            # evaluate once at wire time (unset namespaces read 0.0, warned
            # once) so the attribute has a defined state
            self._apply_expr_attr_binding(b)
            return

        def _bcb(ns: str, new_val: Any, old_val: Any,
                 _b: AttrBinding = b) -> None:
            self.schedule_update(
                lambda: self._apply_attr_binding(_b, new_val),
                target=_b.widget)

        if is_pattern(b.namespace):
            # family binding: every matching publish drives the attr
            # (last-writer-wins across the family, documented).
            self.bus.subscribe_pattern(b.namespace, _bcb)
            return
        self.bus.ensure_namespace(b.namespace, engine_config="json")
        self.bus.subscribe(b.namespace, _bcb)
        # apply the namespace's current value at wire-time
        current = self.bus.get_state(b.namespace)
        if current is not None:
            self._apply_attr_binding(b, current)

    def _wire_behavior(self, decl: "BehaviorDecl") -> None:
        """Wire ONE state-driven behavior ([CXC-BIND-BEHAVIOR]): the trigger
        namespaces feed the BehaviorEngine; evaluation runs on the animation
        loop.  Shared by initial wiring and runtime hot-add."""
        from composeio.bus import is_pattern

        inst = self.behaviors.add(decl)
        trig = decl.trigger

        if trig.kind == "expr":
            # [CXC-DSL-SITES] trigger-condition site: the condition is a
            # multi-namespace expression; any referenced namespace publish
            # re-evaluates it to a canon on/off.
            for ns_name in trig.expr.namespaces:
                self.bus.ensure_namespace(ns_name, engine_config="json")

                def _xcb(ns: str, new_val: Any, old_val: Any,
                         _inst=inst) -> None:
                    self.schedule_update(
                        lambda: self.behaviors.set_active(
                            _inst, _inst.decl.trigger.evaluate_expr(
                                self.bus.get_state)))

                self.bus.subscribe(ns_name, _xcb)
            self.behaviors.set_active(
                inst, trig.evaluate_expr(self.bus.get_state))
            return

        if trig.kind == "always" or trig.namespace is None:
            # [CXC-ICV-MOTION] reduced-motion gate: an always-on behavior is
            # by definition ambient motion (the header live-dot beat, a
            # spinner); under design.reduced_motion it stays inactive so the
            # widget renders its REST frame.  State-triggered behaviors
            # (semantic transitions) still run.
            self.behaviors.set_active(
                inst, not getattr(self.comp, "reduced_motion", False))
            self._dirty = True
            return

        def _tcb(ns: str, new_val: Any, old_val: Any,
                 _inst=inst) -> None:
            self.schedule_update(
                lambda: self.behaviors.set_active(
                    _inst, _inst.decl.trigger.matches(new_val)))

        if is_pattern(trig.namespace):
            # any member of the family can arm/disarm the behavior
            self.bus.subscribe_pattern(trig.namespace, _tcb)
            return
        self.bus.ensure_namespace(trig.namespace, engine_config="json")
        self.bus.subscribe(trig.namespace, _tcb)
        current = self.bus.get_state(trig.namespace)
        if current is not None:
            self.behaviors.set_active(inst, trig.matches(current))

    def _wire_state_machine(self, sm: "StateMachine") -> None:
        """Wire ONE declarative state machine ([CXC-BIND-STATES]): the
        selector's namespace(s) drive state transitions on the animation
        loop, exactly as a trigger drives a behavior.  Shared by initial
        wiring and runtime hot-add.  The current bus value SNAPS the widget
        to its resting state at wire time (no tween on load)."""
        from composeio.bus import is_pattern

        self.states.add(sm)
        sel = sm.selector

        if sel.expr is not None:
            for ns_name in sel.namespaces:
                self.bus.ensure_namespace(ns_name, engine_config="json")

                def _scb(ns: str, new_val: Any, old_val: Any,
                         _sm=sm) -> None:
                    self.schedule_update(
                        lambda: self.states.set_state_expr(
                            _sm, self.bus.get_state),
                        target=_sm.widget)

                self.bus.subscribe(ns_name, _scb)
            # snap to the resting state now (unset namespaces read default)
            self.states.set_state_expr(sm, self.bus.get_state, animate=False)
            return

        if not sel.namespace:
            # no selector namespace: rest at the default state
            self.states.set_state(sm, None, animate=False)
            return

        def _tcb(ns: str, new_val: Any, old_val: Any, _sm=sm) -> None:
            self.schedule_update(
                lambda: self.states.set_state(_sm, new_val),
                target=_sm.widget)

        if is_pattern(sel.namespace):
            self.bus.subscribe_pattern(sel.namespace, _tcb)
            return
        self.bus.ensure_namespace(sel.namespace, engine_config="json")
        self.bus.subscribe(sel.namespace, _tcb)
        current = self.bus.get_state(sel.namespace)
        # snap to the resting state at wire time (current value or default)
        self.states.set_state(sm, current, animate=False)

    # -- runtime hot-add ([CXC-ICP-REBIND]) -----------------------------------

    def add_widget_live(self, parent: Container, spec: dict, *,
                        index: Optional[int] = None) -> Widget:
        """Add a widget from a ``.compose.yaml`` *spec* to a LIVE *parent*
        container and bind its bus wiring IMMEDIATELY ([CXC-ICP-REBIND]) — the
        Dashboard hot-add / live-subscribe path.

        A widget dropped onto a running surface must light up with live data
        NOW, not on next launch: the same ``build_node`` parse-time collection
        that seeds ``comp.subscriptions``/``attr_bindings``/``behaviors``/
        ``outputs``/``asset_uris`` runs here, then ONLY the newly-appended
        entries are wired to the live bus (the delta), the child is mounted
        against the retained engine context, and its arranged region is
        damaged for repaint.  Returns the new widget.

        Must be called after ``start()`` (needs the live bus + mount context);
        marshal from another thread via ``schedule_update`` if the drop event
        arrives off the render thread."""
        from .parser import build_node

        if self._engine_ctx is None:
            raise RuntimeError("add_widget_live requires start() first "
                               "(no live mount context)")
        comp = self.comp
        n_sub = len(comp.subscriptions)
        n_attr = len(comp.attr_bindings)
        n_beh = len(comp.behaviors)
        n_states = len(comp.state_machines)
        n_out = len(comp.outputs)
        n_asset = len(comp.asset_uris)

        in_stack = parent.widget_type in ("pixel_stack", "pixelstack")
        child = build_node(spec, comp, in_pixel_stack=in_stack)
        if in_stack:
            from .parser import _wire_stack_position
            _wire_stack_position(child, spec)
        elif parent.widget_type == "grid" and ("row" in spec or "column" in spec):
            child.grid_row = int(spec.get("row", 0))        # type: ignore
            child.grid_col = int(spec.get("column", 0))     # type: ignore

        if index is None:
            parent.add(child)
        else:
            parent.children.insert(index, child)
        # An ordered/visibility container keeps a hot-added child SHOWN under
        # an active order/allow-list rather than hidden by omission.
        if isinstance(parent, OrderedFlowContainer):
            parent.register_added_child(child)

        # mount against the live engine (bus/assets/animator/redraw)
        child.on_mount(self._engine_ctx)

        # wire ONLY the delta the parse just appended — the hot-add re-bind.
        for ns_name, w in comp.subscriptions[n_sub:]:
            self._wire_subscription(ns_name, w)
        for b in comp.attr_bindings[n_attr:]:
            self._wire_attr_binding(b)
        for decl in comp.behaviors[n_beh:]:
            self._wire_behavior(decl)
        for sm in comp.state_machines[n_states:]:
            self._wire_state_machine(sm)
        # newly declared outputs publish on the next tick (headless) — the
        # _last_outputs cache keys on id(out), so appended decls are fresh.
        _ = n_out
        # prefetch any assets the new subtree references
        new_assets = comp.asset_uris[n_asset:]
        if new_assets:
            self.assets.prefetch(new_assets)

        # focus index + damage: re-collect focusables and repaint the parent's
        # region (which contains the new child) — scoped, no full redraw.
        self.focus.collect(self.comp.root)
        parent.request_redraw()
        return child

    def _apply_attr_binding(self, b: AttrBinding, raw: Any) -> None:
        """Coerce + set a bound attribute; keep any behavior holding the
        same target revert-consistent ([CXC-BIND-C5]).

        ⛔ `UNKNOWN` IS WITHHELD HERE TOO ([CXC-PRIM-STALE]).  The binding
        coercion path is the declarative twin of on_bus_state and fails the
        same way: `_coerce` around a `float()` would either swallow the
        UnknownValueError and leave the old number, or raise inside a bus
        callback.  The bound attribute keeps its last value on purpose and the
        engine's central treatment marks the widget — the same answer at both
        seams, because two answers is how the two seams start to disagree.
        """
        from composeio.freshness import is_unknown

        if b.widget is None:
            return
        if is_unknown(raw) and not getattr(b.widget, "wants_unknown", False):
            return
        if apply_binding(b.widget, b, raw):
            self.behaviors.notify_base_change(
                b.widget, b.attr, getattr(b.widget, b.attr))

    def _apply_expr_attr_binding(self, b: AttrBinding) -> None:
        """Re-evaluate an expression binding ([CXC-DSL-SITES] binding-
        value site) against the live bus and set the coerced result.
        dt/t are 0 here — bindings are event-evaluated, not integrated
        ([CXC-DSL-STATE]); integration belongs to the output site."""
        if b.widget is None or b.expr is None:
            return
        try:
            value = b.expr.evaluate(EvalContext(ns=self.bus.get_state))
        except DslEvalError as e:
            if "evalerr" not in b.expr._warned:
                b.expr._warned.add("evalerr")
                logger.warning("[CXC-DSL] binding expr error on %s.%s "
                               "(value withheld): %s",
                               getattr(b.widget, "widget_id", "?"),
                               b.attr, e)
            return
        if apply_expr_binding(b.widget, b, value):
            self.behaviors.notify_base_change(
                b.widget, b.attr, getattr(b.widget, b.attr))

    # -- clock-ticked widgets ([SPEC:CXC-LOTTIE]) ------------------------------

    def register_clocked(self, widget: Any) -> None:
        """Idempotent registration for per-frame on_clock_tick(now) calls."""
        if all(widget is not w for w in self._clocked):
            self._clocked.append(widget)

    def unregister_clocked(self, widget: Any) -> None:
        self._clocked = [w for w in self._clocked if w is not widget]

    def _tick_clocked(self, now: float) -> bool:
        """Tick every clocked widget at clock *now*.  Returns True while
        any still needs frames (keeps the composition dirty).

        A widget that reports it is still animating damages only its OWN
        paint bounds ([CXC-R-DAMAGE]) — the avatar breathe redraws the
        presence card, not the 1280x1300 scene."""
        busy = False
        for w in list(self._clocked):
            try:
                if w.on_clock_tick(now):
                    busy = True
                    # A widget may scope its clock damage TIGHTER than its full
                    # rect ([CXC-R-DAMAGE]): a focused text_input blinks its
                    # caret and damages only that sliver, not the whole field.
                    # Default clock_damage_rect() is the arranged rect (the
                    # live-dot beat, Lottie playback repaint their whole rect).
                    self.mark_damage(w.clock_damage_rect())
            except Exception:
                logger.exception("clocked widget %s tick failed",
                                 getattr(w, "widget_id", w))
        return busy

    # -- staleness poll ([CXC-PRIM-STALE]) -----------------------------------

    def _freshness_map(self) -> List[tuple]:
        """[(widget, [namespace, ...])] — every widget with a bus input.

        BOTH seams count.  A `subscriptions` entry drives on_bus_state; an
        `attr_bindings` entry drives a bound attribute (a gauge whose `value:`
        is bound is exactly as capable of showing a dead number as one whose
        handler sets it).  Judging only the first would leave the declaratively
        bound half of the fleet silently untreated — the same defect, scoped to
        whichever seam the author happened to use.

        Pattern namespaces ('prefix/*') are excluded: a family has no single
        declared cadence, and inventing one would condemn a family whose
        members are individually healthy.
        """
        from composeio.bus import is_pattern

        stamp = (len(self.comp.subscriptions), len(self.comp.attr_bindings))
        if self._fresh_map is not None and stamp == self._fresh_map_stamp:
            return self._fresh_map
        by_widget: Dict[int, tuple] = {}
        def _add(w, ns):
            if w is None or not ns or is_pattern(ns):
                return
            entry = by_widget.setdefault(id(w), (w, []))
            if ns not in entry[1]:
                entry[1].append(ns)
        for ns, w in self.comp.subscriptions:
            _add(w, ns)
        for b in self.comp.attr_bindings:
            _add(getattr(b, "widget", None), getattr(b, "namespace", None))
        self._fresh_map = list(by_widget.values())
        self._fresh_map_stamp = stamp
        return self._fresh_map

    def poll_freshness(self) -> int:
        """Ask the bus for a verdict on every widget's inputs and record it
        ([CXC-PRIM-STALE]).  Returns the number of widgets whose treated
        appearance CHANGED (each is damaged).

        ⛔ A POLL IS REQUIRED AND IT IS NOT AN OVERSIGHT.  Expiry in
        [SPEC:CXC-FRESHNESS] is LAZY — computed at read, with no timer and no
        sweep — and the transition to stale therefore fires NO bus callback,
        because the defining event is that NOTHING WAS PUBLISHED.  A widget
        waiting to be told it went stale waits forever.  The render tier is the
        right place to ask, because a renderer is by definition a reader, and
        [CXC-FRESH-LAZY]'s reason for refusing a sweep ("a stalled receiver
        demotes the whole world and blames the world") is honoured: a stalled
        render loop stops asking, so it condemns nothing.

        Deterministic paths are unaffected by construction: a namespace with no
        declared cadence answers UNDECLARED, which carries no treatment, so
        every existing golden scene polls to exactly the state it started in
        and reports zero changes.
        """
        from composeio.freshness import Verdict
        from composeio.render.freshness_view import worst

        changed = 0
        for widget, namespaces in self._freshness_map():
            verdict: Optional[Verdict] = None
            age: Optional[float] = None
            culprit: Optional[str] = None
            for ns_name in namespaces:
                ns = self.bus.get_namespace(ns_name)
                if ns is None:
                    continue
                v = ns.verdict
                nxt = worst(verdict, v)
                if nxt is not verdict:
                    verdict, age, culprit = nxt, ns.age, ns_name
                elif verdict is None:
                    verdict, age, culprit = v, ns.age, ns_name
            if verdict is None:
                continue
            if widget.note_freshness(verdict, age, culprit):
                changed += 1
                self.mark_damage(getattr(widget, "arranged_rect", None))
        return changed

    def _poll_freshness_paced(self, now: float) -> None:
        """The wall-clock loop's poll, rate-limited to FRESHNESS_POLL_HZ.

        Per-frame would be wasteful (60 verdict computations a second buys
        nothing a human can see) and — more importantly — the age in the badge
        would tick a new caption every frame in the sub-second range if it were
        formatted any finer than whole seconds.  It is not; the caption changes
        at most once a second, so the poll only needs to keep up with that.
        """
        if now - self._last_fresh_poll < FRESHNESS_POLL_INTERVAL_S:
            return
        self._last_fresh_poll = now
        if self.poll_freshness():
            self._dirty = True

    def _damage_widgets(self, widgets: List[Any]) -> None:
        """Damage each widget's paint bounds ([CXC-R-DAMAGE]) — the engine's
        scoped-redraw hook for the per-frame animation tickers (behaviors,
        animator) that report which widgets they wrote."""
        for w in widgets:
            self.mark_damage(getattr(w, "arranged_rect", None))

    # -- thread-safe update handoff ------------------------------------------

    def schedule_update(self, fn: Callable[[], None],
                        *, target: Optional[Widget] = None) -> None:
        """Queue *fn* to run on the render thread before the next frame.
        Safe to call from any thread; marks the composition dirty.

        A cross-thread update (a bus/MQTT callback) usually targets ONE bound
        widget — the subscription/attr-binding wiring captures exactly which
        (``target``).  When known, the damage is scoped to that widget's card
        POST-layout ([CXC-R-DAMAGE], _apply_drained_damage), so routine
        telemetry (a per-second ``self/*`` publish -> its gauge/ledger) no
        longer re-rasters the whole fidelity-rich scene and stalls keystrokes.
        ``target=None`` (a caller that could touch anything — a behavior
        trigger, a widget-authored update) keeps the conservative full redraw.
        The actual damage is applied at drain time on the render thread; here
        we only enqueue and flag dirty (no cross-thread _damage mutation)."""
        self._update_queue.put((fn, target))
        self._dirty = True

    def drain_updates(self) -> int:
        """Run the cross-thread updates that were QUEUED AT FRAME START (render
        thread only), recording each update's damage TARGET for
        _apply_drained_damage() ([CXC-R-DAMAGE]).  Returns the number executed.
        The pre-run arranged_rect is captured before fn() so a widget that MOVES
        or GROWS on the update (the multi-line composer, the growing thread) can
        be damaged old-UNION-new.

        THE FRAME BOUNDARY IS LOAD-BEARING ([CXC-R-DAMAGE] frame budget; found
        2026-08-11).  This loop used to run until the queue was EMPTY, which made
        it re-entrant with its own output: a widget that consumes a bounded slice
        of work and re-schedules itself for the next frame — the Terminal Widget's
        `drain_once`, exactly as its docstring describes — had its follow-up picked
        up by this same loop, in this same frame.  Its per-tick cap then bounded
        nothing at all: a 256 KiB burst from the child ran the emulator to
        completion inside ONE frame (597 ms measured, window frozen, keyboard
        dead).  Snapshotting the depth at entry is what makes "carry the remainder
        to the next tick" true for EVERY widget, not just the one that noticed.

        The cost of the boundary is at most one frame (~17 ms) of latency for work
        a scheduled update itself schedules; the alternative is an unbounded frame."""
        n = 0
        targets: List[tuple] = []
        budget = self._update_queue.qsize()
        while budget > 0:
            budget -= 1
            try:
                fn, target = self._update_queue.get_nowait()
            except queue.Empty:
                break
            old = getattr(target, "arranged_rect", None) \
                if target is not None else None
            try:
                fn()
            except Exception:
                logger.exception("scheduled update failed")
            targets.append((target, old))
            n += 1
        self._drained_targets = targets
        return n

    def _apply_drained_damage(self) -> None:
        """Translate this frame's drained bus updates into damage, POST-layout
        so arranged_rect is current ([CXC-R-DAMAGE]).  A targeted update damages
        only its widget's card (pre- UNION post-layout rect, so a grown widget
        reveals cleanly); an untargeted update keeps the conservative full
        redraw.  This is the fan-in analog of the on-thread scoped fast paths —
        it stops routine ``self/*`` telemetry from re-rastering the whole scene."""
        for target, old in self._drained_targets:
            if target is None:
                self._damage.add_full()
            else:
                if old is not None:
                    self.mark_damage(old)
                self.mark_damage(getattr(target, "arranged_rect", None))
        self._drained_targets = []

    # -- bus ------------------------------------------------------------------

    def publish(self, namespace: str, value: Any, source: str = "render") -> None:
        self.bus.ensure_namespace(namespace, engine_config="json")
        # THE RENDER UI->BUS BOUNDARY ([CXC-IBUS-ENGINE-REJECT-UNIFORM], 0x70).
        # EVERY widget / gesture / interaction callback funnels through here
        # (EngineContext.publish delegates to this; _fire_gesture_publishes and
        # _publish_interaction both call it). A DECLARED strict engine handed an
        # out-of-domain value RAISES — e.g. '' (the CONV-3 no-value string, and
        # the default a value-less `gestures:` entry publishes) on a `binary`
        # namespace. Escaping a live callback, that raise reaches the native
        # (SDL/Tk) event dispatch and on macOS becomes an NSException that
        # SIGABRTs the whole surface. This is a value-ingest BOUNDARY exactly
        # like the external transport boundary (ComposeIOBus._dispatch_incoming),
        # so it MUST behave identically: log-and-drop, never abort. The internal
        # bus.publish_state API keeps RAISING for a programmatic caller — that
        # raise is catchable and is a real bug signal; only the uncatchable
        # boundary is guarded.
        try:
            self.bus.publish_state(namespace, value, source=source)
        except ValueError as exc:
            logger.warning(f"Value rejected on '{namespace}': {exc}")

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        """Create the surface, mount the tree, do the initial layout+draw.

        Headless ([SPEC:CXC-HEADLESS]): no surface, no layout, no draw —
        the backend import never even happens, so a headless runtime host
        (hub / container) needs no skia."""
        if not self.headless:
            if self.windowed:
                from ..window import SkiaWindow
                # [DEC-LAUNCH-PLACEMENT] a launcher-spawned window opens at an
                # explicit origin (beside the console) and raises to the front.
                pos, raise_to_front = _launch_window_opts()
                # [CXC-DVL-GEOMETRY] restore-on-launch precedence: a persisted
                # geometry wins for BOTH size and position (overriding the
                # beside-console launcher placement); with none, the scene
                # default size + the launcher pos apply.  Applied BEFORE the
                # first frame so the window opens exactly where/how big it was.
                pos = self._restore_geometry(pos)
                min_size = None
                if self.comp.min_width > 0 and self.comp.min_height > 0:
                    min_size = (self.comp.min_width, self.comp.min_height)
                self.canvas = SkiaWindow(title=self.comp.title,
                                         fullscreen=self.fullscreen,
                                         scale=self._window_scale,
                                         pos=pos,
                                         raise_to_front=raise_to_front,
                                         resizable=self.comp.resizable,
                                         min_size=min_size)
            else:
                from ..skia_backend import SkiaCanvas
                self.canvas = SkiaCanvas()
            self.canvas.create_surface(self.comp.width, self.comp.height)
            # [CXC-ICP-S2-FONTSCALE] apply the scene/personalization font-scale
            # BEFORE the first layout+draw so measure and draw are consistent
            # (a font_scale of 1.0 is a byte-identical no-op).
            fs = getattr(self.comp, "font_scale", 1.0)
            set_fs = getattr(self.canvas, "set_font_scale", None)
            if set_fs is not None and fs != 1.0:
                set_fs(fs)
            # [CXC-DVL-GEOMETRY] off-screen clamp: a position saved on a
            # since-disconnected monitor is pulled back onto a visible display
            # now that SDL can report the display bounds.
            self._clamp_restored_geometry()
            # [CXC-DVL-GEOMETRY] first-launch policy origin: re-place so the
            # window FRAME (not the content rect) lands on the work-area
            # inset.  No-op unless this launch used the policy origin.
            self._apply_policy_origin_frame_offset()

        if self._owns_bus:
            self.bus.start()

        # [SPEC:CXC-DSL] seed declared namespace initial: values (only
        # where the bus holds no state yet — a retained/live value wins).
        for ns_name, value in getattr(self, "_ns_initials", []):
            if self.bus.get_state(ns_name) is None:
                self.publish(ns_name, value, source="initial")

        # [SPEC:CXC-RETRO §5c] launch-initials: a launcher that spawns THIS
        # scene for one specific entity (the Away-Watch relaunch → the CLICKED
        # watch) parametrizes the window by handing it a per-launch
        # {namespace: value} map in COMPOSEIO_SCENE_INITIAL — race-free (it is
        # in the process env before first draw) and PER-WINDOW-ISOLATED (each
        # launched process owns its own env, so two windows for two entries
        # never clobber each other, which a shared-bus publish would). These win
        # over the scene's static widget defaults, so the window shows the
        # clicked entry, not the scene's baked-in default.  Same guard as the
        # declared initials: a live/retained value already on the bus wins.
        # Independent of the COMPOSEIO_WINDOW_* placement env
        # ([DEC-LAUNCH-PLACEMENT]) — this seeds bus state, that positions the
        # window; a launch may carry both.
        for ns_name, value in _launch_scene_initials().items():
            if self.bus.get_state(ns_name) is None:
                self.publish(ns_name, value, source="launch-initial")

        # Prefetch assets in parallel ([CXC-R-ENGINE-ASSETS])
        self.assets.prefetch(self.comp.asset_uris)

        ctx = EngineContext(self)
        self._engine_ctx = ctx        # retained for runtime hot-add ([CXC-ICP-REBIND])
        self.comp.root.on_mount(ctx)
        self.focus.collect(self.comp.root)
        if not self.headless:
            self._layout()
            self._draw()
            self.canvas.present()
        self._running = True

        # [CXC-ICP-LAUNCH-BIND] wire the pre-start mount delta LAST — after
        # _running is set — so `_wire_subscription`'s retained-value replay
        # fires ([CXC-ICP-REBIND]): the launch-mounted widget lights up with the
        # current bus state (seeded initials + any retained/live value)
        # synchronously, exactly as a hot-added widget does, never blank.  A
        # scene with no pre-start mount has an empty delta — a no-op.
        self._wire_pending()

    def stop(self) -> None:
        self._running = False
        # [CXC-DVL-GEOMETRY] final geometry write on close (past the throttle),
        # so the last size/pos is never lost to the debounce.
        try:
            self._record_geometry(final=True)
        except Exception:
            logger.exception("final geometry write failed")
        self.comp.root.on_unmount()
        if self._owns_bus:
            self.bus.stop()
        if self.windowed and self.canvas is not None:
            self.canvas.close()          # type: ignore[attr-defined]

    def mark_dirty(self) -> None:
        """Mark the WHOLE surface dirty ([CXC-R-DAMAGE] conservative path).
        Every pre-damage caller (pointer events, expose, resize, background
        changes) keeps this behavior and renders byte-identically to before."""
        self._dirty = True
        self._damage.add_full()

    def mark_damage(self, rect: Optional[Rect]) -> None:
        """Mark only *rect* dirty ([CXC-R-DAMAGE]).  A None rect (a widget
        with no arranged rect yet) escalates to a full redraw inside the
        DamageRegion.  This is the scoped fast path: input + animation."""
        self._dirty = True
        self._damage.add_rect(rect)

    # -- layout / draw --------------------------------------------------------------

    def _layout(self) -> None:
        root = self.comp.root
        root.measure(Constraints(self.comp.width, self.comp.height))
        root.arrange(Rect(0, 0, self.comp.width, self.comp.height))

    def _draw(self) -> None:
        c = self.canvas
        bg = self.comp.background
        c.set_fill_color(*bg[:3], bg[3] if len(bg) > 3 else 255)
        c.fill_rect(0, 0, self.comp.width, self.comp.height)
        self._draw_background_layers(c)
        self.comp.root.draw(c, self.comp.root.arranged_rect)
        self.focus.draw_focus_ring(c)
        self._frame_count += 1

    def _draw_background_layers(self, c: Canvas) -> None:
        """[CXC-ICV-CHROME] optional scene background pass: a faint grid
        (design.background_grid: {color, size}) and a soft radial wash
        (design.background_wash: {color, cx, cy, r} in surface fractions),
        drawn UNDER the widget tree.  Absent config -> zero-cost no-op."""
        W, H = self.comp.width, self.comp.height
        grid = getattr(self.comp, "background_grid", None)
        if isinstance(grid, dict):
            from .bindings import rgba as _rgba_
            col = _rgba_(grid.get("color"), (120, 150, 190, 9))
            size = float(grid.get("size", 40))
            if size >= 4:
                c.set_stroke_color(*col)
                c.set_stroke_width(1.0)
                x = size
                while x < W:
                    c.draw_line(x, 0, x, H)
                    x += size
                y = size
                while y < H:
                    c.draw_line(0, y, W, y)
                    y += size
        wash = getattr(self.comp, "background_wash", None)
        if isinstance(wash, dict):
            from ..canvas import GradientStop
            from .bindings import rgba as _rgba_
            col = _rgba_(wash.get("color"), (78, 168, 255, 33))
            cx = float(wash.get("cx", 0.84)) * W
            cy = float(wash.get("cy", -0.10)) * H
            r = float(wash.get("r", 0.9)) * max(W, H)
            c.set_radial_gradient(cx, cy, r, [
                GradientStop(0.0, *col),
                GradientStop(1.0, col[0], col[1], col[2], 0)])
            c.fill_rect(0, 0, W, H)
            c.set_fill_color(0, 0, 0, 0)   # clear the gradient override

    def render_frame(self) -> None:
        """One FULL layout+draw+present pass (public for tests/embedding).

        Always a whole-surface render — the deterministic path that goldens
        and embedders rely on.  The scoped dirty-region path lives in
        render_dirty() ([CXC-R-DAMAGE]); render_frame() clears the pending
        damage since it has just repainted everything."""
        self.drain_updates()
        self.poll_freshness()        # [CXC-PRIM-STALE]
        self._layout()
        self._draw()
        self.canvas.present()
        self._dirty = False
        self._damage = DamageRegion()

    def render_dirty(self) -> None:
        """Render + present ONLY the damaged region ([CXC-R-DAMAGE]) — the
        live-loop frame.  A full or empty damage region falls back to a
        whole-surface render_frame(); a scoped region clips the raster to
        the damage bounds, prunes the widget tree to the widgets whose paint
        bounds intersect it (via draw_child), and presents just that sub-rect.

        The composited pixels are identical to a full redraw ([CXC-R-DAMAGE-
        C1]); the win is that a keystroke rasters ~0.1 ms of composer field
        instead of ~52 ms of full scene."""
        self.drain_updates()             # records this frame's damage targets
        self._layout()
        self._apply_drained_damage()     # targets -> scoped damage, post-layout
        if self._damage.is_full or self._damage.is_empty:
            self._draw()
            self.canvas.present()
        else:
            regions = self._draw_partial(self._damage)
            if not regions:
                self.canvas.present()
            else:
                self.canvas.present_partial(
                    [(r.x, r.y, r.w, r.h) for r in regions])
        self._dirty = False
        self._damage = DamageRegion()

    def _draw_partial(self, damage: DamageRegion):
        """Raster ONLY the *damage* rects and return the presented Rects
        (empty when there was nothing to scope — the caller then presents the
        full surface).

        Each damage rect is rastered in ITS OWN clip ([CXC-R-DAMAGE-C1]) —
        never the bounding box of them all.  A live frame accumulates disjoint
        damage (a keystroke's composer field at the bottom, the header live-dot
        beat at the top, the gauge hotpulse in the rail); their bounding box
        spans nearly the whole scene, and clearing it would erase every
        un-damaged card lying between the rects (its paint bounds intersect no
        actual damage rect, so pruning skips its redraw) — a clear-then-restore
        FLASH.  Per-rect clipping touches ONLY the pixels that changed, so the
        cards between the rects keep their (still correct) pixels untouched.

        Correctness ([CXC-R-DAMAGE-C1]): within each rect the clip bounds the
        pixels touched but never changes what a primitive draws; the background
        pass + every widget whose paint bounds intersect THAT rect are redrawn
        in the SAME z-order as a full frame, so the rect composites to the same
        image."""
        c = self.canvas
        regions = damage.clamped_rects(self.comp.width, self.comp.height)
        if not regions:
            self._draw()
            return []
        bg = self.comp.background
        for region in regions:
            x, y, w, h = region.x, region.y, region.w, region.h
            c.save()
            c.clip_rect(x, y, w, h)
            c.set_fill_color(*bg[:3], bg[3] if len(bg) > 3 else 255)
            c.fill_rect(x, y, w, h)
            self._draw_background_layers(c)
            # Stamp ONLY this rect so draw_child prunes to the widgets that
            # actually touch it (the REAL draw path, no reimplementation) —
            # a widget bound for a different damage rect is never drawn here.
            c._damage = DamageRegion._of_rect(region)
            try:
                self.comp.root.draw(c, self.comp.root.arranged_rect)
            finally:
                c._damage = None
            # The focus ring is clipped to this rect like everything else; a
            # ring that does not fall in it simply retains its pixels.
            self.focus.draw_focus_ring(c)
            c.restore()
        self._frame_count += 1
        return regions

    def render_frame_at(self, t: float) -> None:
        """Deterministic fixed-timestep sampling ([CXC-BIND-C6]): drain
        pending updates, evaluate behaviors at LOGICAL time *t* (seconds),
        then layout+draw+present.  Behavior activation stamps its start on
        its first tick, so publishing a trigger and sampling at t=0, 0.6,
        ... reproduces frames byte-identically on every platform — this is
        the golden-pinning path for temporal behaviors.  Do not interleave
        with the wall-clock run() loop on the same app."""
        self.drain_updates()
        self.behaviors.tick(t)
        self.states.tick(t)         # [CXC-BIND-STATES] same logical clock
        self._tick_clocked(t)       # Lottie et al. sample the same clock
        # [CXC-PRIM-STALE] the verdict is read on THIS sampled frame, so a
        # scene with an injected namespace clock renders staleness
        # deterministically.  With no declared cadence every namespace answers
        # UNDECLARED, which carries no treatment — every pre-existing golden
        # is byte-identical.
        self.poll_freshness()
        self._layout()
        self._draw()
        self.canvas.present()
        self._dirty = False
        # A deterministic full frame clears any scoped damage the ticks
        # accumulated ([CXC-R-DAMAGE]); this path always paints everything.
        self._damage = DamageRegion()

    def tick(self, t: float) -> List[tuple]:
        """ONE headless tick at logical time *t* seconds ([CXC-HW-RUNTIME]
        [CXC-HW-C9]/[C10]) — the render_frame_at(t) analog minus the
        canvas:

            drain updates  (bus callbacks -> apply attr bindings /
                            behavior trigger transitions)
            advance behaviors at t   (pure functions of elapsed time)
            tick clocked widgets at t
            publish changed output namespaces  (declared ``outputs:``)

        Deterministic by construction: *t* is an argument and NOTHING in
        this path reads a wall clock, so ``tick(0.0), tick(0.1), ...``
        reproduces the exact published sequence — the headless analog of
        the byte-reproducible render_frame_at.  All three scheduling
        clocks (continuous / periodic / bus-triggered, engine/headless.py)
        drive THIS one method; there is no per-mode execution path.

        Returns the list of (namespace, value) pairs published this tick.
        """
        self.drain_updates()
        self.behaviors.tick(t)
        self.states.tick(t)         # [CXC-BIND-STATES] same logical clock
        self._tick_clocked(t)
        # [SPEC:CXC-DSL] tick context: dt is the gap to the previous
        # tick of THIS app (0.0 on the first tick — an integration
        # expression integrates nothing before time has passed).
        dt = 0.0 if self._last_tick_t is None else max(0.0,
                                                       t - self._last_tick_t)
        self._last_tick_t = t
        # [SPEC:CXC-HMON] [CXC-HMON-TRACE]: when tracing is enabled the
        # tick leaves a full record — t, inputs read (ns -> value), per
        # output the traced evaluation, and published-or-SUPPRESSED.
        # Observation never perturbs execution: the published values are
        # identical either way (evaluate_traced returns evaluate's value).
        record: Optional[dict] = None
        if self.trace_enabled:
            record = {"t": round(t, 6), "dt": round(dt, 6),
                      "inputs": {}, "outputs": []}
        published = self._publish_outputs(dt=dt, t=t, record=record)
        if record is not None:
            self._record_declared_inputs(record)
            self.last_tick_record = record
        self._dirty = False
        return published

    def _record_declared_inputs(self, record: dict) -> None:
        """Fill the tick record's inputs with everything this widget
        READS beyond its exprs' own ns() reads (already recorded by the
        traced walk): its subscribe namespaces, attr-binding namespaces,
        and behavior trigger namespaces — current bus values.  Mirrors
        introspect_ports' input semantics ([CXC-FLAUTH-INTROSPECT])."""
        from composeio.bus import is_pattern

        def _add(ns: Any) -> None:
            ns = str(ns or "")
            if ns and not is_pattern(ns) and ns not in record["inputs"]:
                record["inputs"][ns] = self.bus.get_state(ns)

        for ns_name, _w in self.comp.subscriptions:
            _add(ns_name)
        for b in self.comp.attr_bindings:
            if b.expr is not None:
                for n in b.expr.namespaces:
                    _add(n)
            elif b.namespace:
                _add(b.namespace)
        for decl in self.comp.behaviors:
            trig = decl.trigger
            if trig.kind == "expr" and trig.expr is not None:
                for n in trig.expr.namespaces:
                    _add(n)
            elif trig.namespace:
                _add(trig.namespace)

    def _publish_outputs(self, dt: float = 0.0, t: float = 0.0,
                         record: Optional[dict] = None) -> List[tuple]:
        """Publish every declared output whose value changed since the
        last publish (first evaluation always publishes, so downstream
        subscribers get a defined initial state).

        attr outputs read ``widget.<attr>``; expr outputs
        ([CXC-DSL-SITES]) evaluate their sandboxed expression against
        the live bus with the REAL dt/t — the read-modify-write
        integration primitive ([CXC-DSL-STATE]: the bus IS the state).
        Outputs evaluate in declaration order; a later output sees an
        earlier one's same-tick publish (deterministic).

        *record* ([SPEC:CXC-HMON], None on the default untraced path):
        when given, expr outputs run evaluate_traced (same value, plus
        the walk) and every output appends a per-output dict including
        the publish-on-change verdict — a suppression is represented,
        not silent ([CXC-HMON-C3])."""
        published: List[tuple] = []
        for out in self.comp.outputs:
            if out.widget is None:
                continue
            entry: Optional[dict] = None
            if record is not None:
                entry = {"namespace": out.namespace,
                         "attr": None if out.expr is not None else out.attr,
                         "expr": out.expr.source if out.expr is not None
                         else None}
                record["outputs"].append(entry)
            if out.expr is not None:
                try:
                    if entry is not None:
                        traced = out.expr.evaluate_traced(
                            EvalContext(ns=self.bus.get_state, dt=dt, t=t))
                        cur = traced.value
                        entry["trace"] = traced.steps
                        for n, v in traced.reads().items():
                            record["inputs"].setdefault(n, v)
                    else:
                        cur = out.expr.evaluate(
                            EvalContext(ns=self.bus.get_state, dt=dt, t=t))
                except DslEvalError as e:
                    if "evalerr" not in out.expr._warned:
                        out.expr._warned.add("evalerr")
                        logger.warning("[CXC-DSL] output expr error on %s "
                                       "(publish withheld): %s",
                                       out.namespace, e)
                    self.last_error = f"{out.namespace}: {e}"
                    if entry is not None:
                        entry["error"] = str(e)
                        entry["published"] = False
                        entry["suppressed"] = "error"
                    continue
            else:
                cur = getattr(out.widget, out.attr, None)
            key = id(out)
            changed = key not in self._last_outputs or \
                self._last_outputs[key] != cur
            if entry is not None:
                entry["value"] = cur
                entry["previous"] = self._last_outputs.get(key)
                entry["published"] = changed
                if not changed:
                    # publish-on-change: the SUPPRESSION is the diagnosis
                    # ("why did nothing change") — [CXC-HMON-C3]
                    entry["suppressed"] = "unchanged"
            if changed:
                self.publish(out.namespace, cur,
                             source=out.widget.widget_id)
                self._last_outputs[key] = cur
                published.append((out.namespace, cur))
        return published

    def render_headless(self, out_png: str) -> str:
        """Render the composition once via the raster reference and save.

        This is the pixel-verification path: the windowed present() blits
        the same raster surface, so window contents == this PNG.
        """
        if self.headless:
            raise RuntimeError(
                "render_headless() is the pixel-verification render; a "
                "headless=True app has no canvas at all — use tick(t)")
        if self.canvas is None:
            self.start()
        self.render_frame()
        self.canvas.save_png(out_png)   # type: ignore[attr-defined]
        return out_png

    # -- events ------------------------------------------------------------------

    def _dispatch_pointer(self, ev: Any) -> None:
        """Hit-test + gesture synthesis per [CXC-R-ENGINE-HIT]/[TOUCH]."""
        root: Container = self.comp.root

        # [CXC-R-ENGINE-HIT] RAW per-finger feed: deliver every PointerEvent —
        # with its pointer_id intact — to the widget the finger LANDED on
        # (tracked per pointer_id in _pointer_state), for widgets that opted in
        # (wants_raw_pointers).  Runs ALONGSIDE the gesture synthesis below,
        # which collapses fingers through the single _pointer_capture.  The
        # 'down' case is handled inline (target is resolved there); move/up
        # look the widget back up so a finger's stream follows the same widget
        # even as other fingers come and go.
        if ev.type in ("move", "up"):
            _st = self._pointer_state.get(ev.pointer_id)
            _rw = _st.get("widget") if _st is not None else None
            if _rw is not None and getattr(_rw, "wants_raw_pointers", False):
                _rw.on_raw_pointer(ev)
                self.mark_dirty()

        if ev.type == "down":
            target = root.hit_test_tree(ev.x, ev.y)
            self._pointer_state[ev.pointer_id] = {
                "widget": target, "x": ev.x, "y": ev.y,
                "t": time.monotonic(), "moved": False}
            self._pointer_capture = target
            if target is not None:
                if getattr(target, "wants_raw_pointers", False):
                    target.on_raw_pointer(ev)
                target.on_pointer_down(ev.x, ev.y)
                self.mark_dirty()
            # focus-on-tap ([CXC-R-ENGINE-FOCUS])
            if target is not None and getattr(target, "accepts_focus", False):
                self.focus.focus(target)
                self.mark_dirty()
            elif target is None:
                self.focus.focus(None)

        elif ev.type == "move":
            # [CXC-R-ENGINE-HOVER] update the OS cursor to reflect the widget
            # under the pointer (hand over clickable, arrow over empty space).
            # Runs on EVERY motion — hover is button-independent — but only
            # touches the backend on a change (debounced in _apply_hover_cursor).
            self._apply_hover_cursor(ev.x, ev.y)
            st = self._pointer_state.get(ev.pointer_id)
            if st is not None:
                if (abs(ev.x - st["x"]) > _TAP_MAX_MOVE or
                        abs(ev.y - st["y"]) > _TAP_MAX_MOVE):
                    if not st["moved"]:
                        st["moved"] = True
                        w = self._pointer_capture
                        if w is not None:
                            w.on_pan_start()
                            self._fire_gesture_publishes(
                                w, "pan_start", ev.x, ev.y)
                    w = self._pointer_capture
                    if w is not None:
                        w.on_pan(ev.x - st["x"], ev.y - st["y"])
                        # [CXC-RM-GESTURE] a drag publishes its steer stream
                        # (@pointer coords) so a bound reticle tracks it.
                        self._fire_gesture_publishes(w, "pan", ev.x, ev.y)

        elif ev.type == "up":
            st = self._pointer_state.pop(ev.pointer_id, None)
            w = self._pointer_capture
            self._pointer_capture = None
            if w is not None:
                w.on_pointer_up(ev.x, ev.y)
                self.mark_dirty()
            if st is None:
                return
            held_ms = (time.monotonic() - st["t"]) * 1000.0
            if st["moved"]:
                if w is not None:
                    w.on_pan_end()
                    self._fire_gesture_publishes(w, "pan_end", ev.x, ev.y)
                    # [CXC-GEST-SWIPE] additively classify the SAME finished
                    # drag as a directional swipe.  This is layered ON TOP of
                    # pan (on_pan_end already fired) — a qualifying flick is
                    # both a pan and a swipe; it never changes pan semantics.
                    direction = _classify_swipe(st, ev.x, ev.y, held_ms)
                    if direction is not None:
                        self._fire_swipe(w, direction, ev.x, ev.y)
            elif w is not None:
                if held_ms >= _LONG_PRESS_MS:
                    self._last_tap = None
                    w.on_long_press(ev.x, ev.y)
                    self._fire_gesture_publishes(w, "long_press", ev.x, ev.y)
                    self._publish_interaction("long_press", w, ev.x, ev.y)
                else:
                    self._maybe_double_tap(w, ev.x, ev.y)

        elif ev.type == "scroll":
            target = root.hit_test_tree(ev.x, ev.y)
            if target is not None:
                # [CXC-ICP-S6-SCROLLDIR] The window layer already normalized the
                # wheel delta to ONE OS-respecting orientation (honoring
                # SDL_MOUSEWHEEL_FLIPPED).  "natural" (default) passes it
                # straight through so scrolling matches the OS setting; the
                # per-instance "traditional" preference flips the sign for a
                # user who wants the opposite of their OS.
                dx, dy = ev.dx, ev.dy
                if getattr(self.comp, "scroll_direction", "natural") == \
                        "traditional":
                    dx, dy = -dx, -dy
                target.on_scroll(dx, dy)

    # -- hover cursor ([CXC-R-ENGINE-HOVER]) ----------------------------------

    def resolve_cursor(self, x: float, y: float) -> str:
        """The semantic hover cursor at surface point (x, y) — a PURE READ
        ([CXC-R-ENGINE-HOVER]).

        Reuses the SAME ``hit_test_tree`` the click/drop paths use (one hit
        model, never a fork): the topmost INTERACTIVE widget under the pointer,
        if any, decides its own ``hover_cursor()`` (default: a pointing hand);
        empty space -> "default" (the arrow).  This is the headless-observable
        seam the live cursor swap is verified against — it mutates nothing and
        requests no redraw."""
        root = getattr(self.comp, "root", None)
        if root is None:
            return "default"
        target = root.hit_test_tree(x, y)
        if target is None:
            return "default"
        fn = getattr(target, "hover_cursor", None)
        if fn is None:
            # any non-Widget hit target that is still interactive -> the hand
            return "pointer"
        try:
            name = fn()
        except Exception:
            return "default"
        return str(name) if name else "default"

    def _apply_hover_cursor(self, x: float, y: float) -> None:
        """Resolve the cursor under the pointer and set it on the backend,
        DEBOUNCED to changes only ([CXC-R-ENGINE-HOVER]).

        Called from the MOUSEMOTION path.  Continuous motion over one widget
        resolves to the same name and issues NO backend call; only crossing a
        clickable boundary flips the OS cursor.  Never marks the scene dirty (a
        cursor change is not a repaint, [CXC-R-DAMAGE]).  ``set_cursor`` is a
        duck-typed backend affordance (like get_bounds/set_position): a canvas
        without it — headless / the raster reference — is a safe no-op, which
        is exactly the per-backend lowering (browser -> CSS cursor; CYD ->
        no cursor)."""
        if not self.windowed or self.canvas is None:
            return
        want = self.resolve_cursor(x, y)
        if want == self._current_cursor:
            return
        self._current_cursor = want
        set_cursor = getattr(self.canvas, "set_cursor", None)
        if set_cursor is not None:
            try:
                set_cursor(want)
            except Exception:
                logger.exception("set_cursor(%s) failed", want)

    def _maybe_double_tap(self, w: Widget, x: float, y: float) -> None:
        """Fire on_double_click when this tap is a fast second tap on the SAME
        widget ([CXC-R-ENGINE-HIT] double-click), else a normal tap.  A double
        clears the record so a third tap starts a fresh pair (no triple-fire)."""
        now_t = time.monotonic()
        last = self._last_tap
        is_double = (
            last is not None and last[0] is w
            and (now_t - last[1]) * 1000.0 <= _DOUBLE_CLICK_MS
            and abs(x - last[2]) <= _TAP_MAX_MOVE
            and abs(y - last[3]) <= _TAP_MAX_MOVE)
        if is_double:
            self._last_tap = None
            w.on_double_click(x, y)
            self._fire_gesture_publishes(w, "double_click", x, y)
            self._publish_interaction("double_click", w, x, y)
            self.mark_dirty()
        else:
            self._last_tap = (w, now_t, x, y)
            self._fire_tap(w, x, y)

    def _fire_tap(self, w: Widget, x: float, y: float) -> None:
        w.on_tap(x, y)
        # declarative publish binding (parser: publish_namespace)
        pub_ns = getattr(w, "_publish_namespace", None)
        if pub_ns:
            value = getattr(w, "_publish_value", "toggle")
            if value == "toggle":
                current = self.bus.get_state(pub_ns)
                value = 0 if current else 1
            self.publish(pub_ns, value, source=w.widget_id)
        self._fire_gesture_publishes(w, "tap", x, y)
        self._publish_interaction("tap", w, x, y)
        self.mark_dirty()

    def _fire_swipe(self, w: Widget, direction: str,
                    x: float, y: float) -> None:
        """Dispatch a recognized directional swipe ([CXC-GEST-BUS]).  Mirrors
        _fire_tap's shape: the widget hook, an optional declarative publish
        binding (parser: swipe_namespace), and the bus interaction event so an
        Intelligence observes/injects a swipe identically to a finger
        ([CXC-R-CONTROL] / [CXC-GEST-C5])."""
        w.on_swipe(direction, x, y)
        # declarative publish binding (parser: swipe_namespace) — a widget
        # binds a swipe the same way it binds a tap's publish_namespace.
        sw_ns = getattr(w, "_swipe_namespace", None)
        if sw_ns:
            self.publish(sw_ns, direction, source=w.widget_id)
        self._fire_gesture_publishes(w, "swipe", x, y)
        self._publish_interaction("swipe", w, x, y, direction=direction)
        self.mark_dirty()

    def _maybe_publish_bbox(self, now: float) -> None:
        """Publish the console window's screen bbox on change or heartbeat
        ([DEC-LAUNCH-PLACEMENT]).  Only the console (gate set in __init__)
        publishes; the payload ``{x, y, w, h}`` lets any launcher place a new
        window top-aligned 10px right of the console.  Publish on the FIRST
        loop (last_bbox is None), whenever the window moves/resizes, and every
        _BBOX_HEARTBEAT_S so a launcher that started later still learns the
        position (state publishes are not broker-retained)."""
        if not self._publish_bbox:
            return
        get_bounds = getattr(self.canvas, "get_bounds", None)
        if get_bounds is None:
            return
        bounds = get_bounds()
        if bounds is None:
            return
        changed = bounds != self._last_bbox
        stale = (now - self._last_bbox_pub_t) >= _BBOX_HEARTBEAT_S
        if not (changed or stale):
            return
        x, y, w, h = bounds
        self._last_bbox = bounds
        self._last_bbox_pub_t = now
        try:
            self.publish(self._bbox_ns, {"x": x, "y": y, "w": w, "h": h},
                         source="console-bbox")
        except Exception:
            logger.exception("console bbox publish failed")

    def _fire_gesture_publishes(self, w: Widget, gesture: str,
                                x: float, y: float) -> None:
        """A declarative ``gestures:`` producer ([CXC-RM-GESTURE]): when the
        widget under this gesture declared publishes for it, publish the
        semantic bus state.  The reticle's state selector CONSUMES that
        namespace — so a finger drag and an Intelligence ``publish()`` reach
        the state machine through the identical path (control-is-a-peer).

        The one non-literal substitution is engine-provided pointer coords:
        ``@pointer`` -> {x, y}; ``@pointer_x`` / ``@pointer_y`` -> the scalar
        coordinate.  No inline code — the [CXC-BIND-BOUNDARY] holds."""
        pubs = getattr(w, "_gesture_publishes", None)
        if not pubs:
            return
        specs = pubs.get(gesture)
        # double_tap is an author alias for the engine's double_click
        if specs is None and gesture == "double_click":
            specs = pubs.get("double_tap")
        if not specs:
            return
        for gp in specs:
            value = gp.value
            if value == "@pointer":
                value = {"x": round(x, 1), "y": round(y, 1)}
            elif value == "@pointer_x":
                value = round(x, 1)
            elif value == "@pointer_y":
                value = round(y, 1)
            self.publish(gp.namespace, value, source=w.widget_id)

    def _publish_interaction(self, kind: str, w: Widget,
                             x: float, y: float, **extra: Any) -> None:
        event = {"type": kind, "widget_id": w.widget_id,
                 "x": round(x, 1), "y": round(y, 1)}
        event.update(extra)          # e.g. direction= for a swipe ([CXC-GEST-C3])
        self._interaction_log.append(event)
        try:
            self.publish(INTERACTION_NS, event, source="render-engine")
        except Exception:
            logger.exception("interaction publish failed")

    def _compose_send_namespace(self) -> Optional[str]:
        """The console composer's outbound send namespace, or None.

        A drop only sends when the scene actually declares an
        `compose_projects/intelligence/<id>/compose` namespace (the composer's SEND leg the
        channel_bridge unwraps).  Non-console scenes have none, so a stray
        drop publishes nothing.  Cached after first resolution."""
        cached = getattr(self, "_compose_ns_cache", False)
        if cached is not False:
            return cached
        found: Optional[str] = None
        for ns_def in self.comp.namespaces:
            name = ns_def.get("name") if isinstance(ns_def, dict) else None
            if (name and str(name).startswith("intelligence/")
                    and str(name).endswith("/compose")):
                found = str(name)
                break
        self._compose_ns_cache = found
        return found

    def _handle_drop(self, path: str, x: float = -1.0, y: float = -1.0) -> None:
        """A file was dropped on the window.

        Position-aware routing (5b): if the drop lands on a widget that ACCEPTS
        file drops (``wants_file_drop`` + ``on_file_drop`` — the message-prompt
        text_input), hand the path to THAT widget so it attaches/inserts it into
        the prompt, exactly like the thread already ingests a drop.  This reuses
        the ONE drop path — no second SDL pipeline — and only re-targets it.

        Otherwise (drop over the thread / anywhere else, or an unknown
        position) fall back to the historical whole-window behavior: publish
        the ABSOLUTE local path on the composer's send namespace as
        ``{"from": "david", "image": <path>}`` (ephemeral by design — nothing
        stored; the channel_bridge unwraps it onto ``compose_projects/intelligence/<id>/in`` and
        the co-located Intelligence Reads the path directly)."""
        import os
        abs_path = os.path.abspath(path)

        # 1) prompt-targeted drop: route to the widget under the pointer.
        target = self._drop_target(x, y)
        if target is not None:
            try:
                accepted = bool(target.on_file_drop(abs_path))
            except Exception:
                logger.exception("widget on_file_drop failed")
                accepted = False
            if accepted:
                # focus the field so its caret shows where the path landed and
                # an immediate Enter sends it (focus-on-drop, the tap idiom).
                if getattr(target, "accepts_focus", False):
                    self.focus.focus(target)
                self.mark_damage(getattr(target, "arranged_rect", None))
                return

        # 2) whole-window fallback: publish to the composer send namespace.
        ns = self._compose_send_namespace()
        if not ns:
            return  # non-console scene: no composer send leg to publish onto
        self.publish(ns, {"from": "david", "image": abs_path},
                     source="console-drop")

    def _drop_target(self, x: float, y: float) -> Optional[Widget]:
        """The widget under a drop at (x, y) that opts into file drops
        (``wants_file_drop``), or None when the position is unknown / no such
        widget is there.  Uses the SAME hit_test_tree the pointer path uses."""
        if x < 0 or y < 0 or not self.windowed:
            return None
        root = getattr(self.comp, "root", None)
        if root is None:
            return None
        w = root.hit_test_tree(x, y)
        if w is not None and getattr(w, "wants_file_drop", False):
            return w
        return None

    def _handle_key(self, ev: Any) -> None:
        if ev.type == "down":
            prev = self.focus.focused
            if self.focus.handle_key(ev.key, ev.modifiers):
                cur = self.focus.focused
                if cur is not prev:
                    # focus MOVED (Tab / blur): repaint both the old and new
                    # widgets so the focus ring clears + redraws ([CXC-R-DAMAGE]);
                    # a None side (blur to nothing) escalates to full via mark_damage.
                    self.mark_damage(getattr(prev, "arranged_rect", None))
                    self.mark_damage(getattr(cur, "arranged_rect", None))
                else:
                    # the focused widget handled the key (edit / nav / clipboard)
                    # — it changed ONLY itself, so scope damage to its card, like
                    # the text fast path below.  A PRINTABLE key's down event does
                    # nothing here (the insert rides the paired "text" event) yet
                    # handle_key still returns True; scoping keeps it OFF the full-
                    # scene path, so a keystroke no longer forces a full raster of
                    # the (fidelity-rich, expensive) scene ([CXC-R-DAMAGE]).  A key
                    # that grows the field (paste) is covered by the widget's own
                    # request_redraw + old∪new growth schedule.
                    self.mark_damage(cur.arranged_rect if cur is not None else None)
        elif ev.type == "text":
            # Platform text input -> focused widget ([CXC-R-C8]).  A keystroke
            # changes only the focused field, so damage only its region
            # ([CXC-R-DAMAGE]) — this is the typing-latency fast path: the
            # engine rasters the composer field, not the whole console.
            if self.focus.handle_text(ev.text):
                w = self.focus.focused
                self.mark_damage(w.arranged_rect if w is not None else None)

    def process_events(self) -> bool:
        """Pump + dispatch one batch of window events.
        Returns False when the window was closed."""
        if not self.windowed:
            return self._running
        for ev in self.canvas.pump_events():     # type: ignore[attr-defined]
            kind = type(ev).__name__
            if kind == "PointerEvent":
                self._dispatch_pointer(ev)
            elif kind == "KeyEvent":
                self._handle_key(ev)
            elif kind == "DropFileEvent":
                self._handle_drop(ev.path, getattr(ev, "x", -1.0),
                                  getattr(ev, "y", -1.0))
            elif kind == "WindowEvent":
                if ev.type == "close":
                    return False
                if ev.type == "expose":
                    self.mark_dirty()
                elif ev.type == "resize":
                    self._handle_resize(ev.width, ev.height)
                elif ev.type == "move":
                    # geometry only — no re-layout on a pure move.
                    self._record_geometry()
        return True

    def _handle_resize(self, width: int, height: int) -> None:
        """The standalone window was resized ([CXC-DVL-WINDOW]): rebuild the
        surface at the new client size, update the composition dimensions,
        force a full redraw so nothing is clipped to the old size, and record
        the new geometry (debounced, [CXC-DVL-GEOMETRY])."""
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            return
        if width == self.comp.width and height == self.comp.height:
            self._record_geometry()
            return
        resize = getattr(self.canvas, "resize_surface", None)
        if resize is not None:
            resize(width, height)
        self.comp.width = width
        self.comp.height = height
        self.mark_dirty()           # full redraw at the new size
        self._record_geometry()

    # -- main loop -------------------------------------------------------------------

    def run(self, max_frames: Optional[int] = None,
            on_frame: Optional[Callable[["RenderApp"], None]] = None) -> None:
        """Run the interactive loop until the window closes.

        max_frames / on_frame exist for smoke tests: bounded runs and
        per-frame hooks (e.g. injecting synthetic events).
        """
        if self.canvas is None:
            self.start()
        frames = 0
        try:
            while self._running:
                t0 = time.monotonic()
                if not self.process_events():
                    break
                # [DEC-LAUNCH-PLACEMENT] advertise the console's screen bbox so
                # launchers place new windows beside it (no-op off the console).
                self._maybe_publish_bbox(t0)
                # [CXC-DVL-GEOMETRY] flush a throttle-pending geometry write
                # (a drag's final size/pos lands ~500ms after the last event).
                self._poll_geometry(t0)
                # Each per-frame ticker damages ONLY the widgets it wrote
                # ([CXC-R-DAMAGE]): the ambient animations no longer dirty the
                # whole scene every loop — the live-dot beat redraws the dot,
                # the avatar breathe redraws the presence card, the gauge
                # hotpulse redraws the gauge.  A frame where nothing changed
                # adds no damage and skips the render entirely.
                if self.animator.tick():
                    self._damage_widgets(self.animator.last_touched)
                # [CXC-BIND-BEHAVIOR]: behaviors run on the animation loop
                # (wall clock here; render_frame_at drives a logical clock).
                if self.behaviors.tick(t0):
                    self._damage_widgets(self.behaviors.last_touched)
                # [CXC-BIND-STATES] state transitions tick on the same loop
                # and damage ONLY the animating widget's bounds.
                if self.states.tick(t0):
                    self._damage_widgets(self.states.last_touched)
                # clocked widgets (Lottie playback) share that clock; they
                # damage their own paint bounds inside _tick_clocked.
                self._tick_clocked(t0)
                # [CXC-PRIM-STALE] the transition to stale fires NO bus
                # callback — the defining event is that nothing was published —
                # so the render loop asks.  Damage is scoped to the widgets
                # whose treated appearance actually changed.
                self._poll_freshness_paced(t0)
                if on_frame is not None:
                    on_frame(self)
                if self._dirty:
                    self.render_dirty()
                frames += 1
                if max_frames is not None and frames >= max_frames:
                    break
                _pace(t0 + self.frame_interval)
        finally:
            self.stop()


def _pace(deadline: float) -> None:
    """Frame pacing: coarse sleep until ~2ms before the deadline, then spin.
    time.sleep alone overshoots 1-2ms on macOS/Linux, which drags a 60fps
    loop down to ~55fps."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if remaining > 0.002:
            time.sleep(remaining - 0.002)
        # spin the last <2ms


SCENE_INITIAL_ENV = "COMPOSEIO_SCENE_INITIAL"


def _launch_scene_initials() -> Dict[str, Any]:
    """Per-launch initial state a launcher hands the spawned window via the
    ``COMPOSEIO_SCENE_INITIAL`` env var: a JSON object mapping ``namespace ->
    value``, seeded at start() so the window renders for THAT launch's entity
    (the Away-Watch relaunch → the clicked watch; [SPEC:CXC-RETRO §5c]).

    Returns {} for an absent/blank/malformed var (a direct/legacy launch simply
    has none) — never raises, so a bad env can't stop a window from opening."""
    raw = os.environ.get(SCENE_INITIAL_ENV, "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(obj, dict):
        return {}
    return {str(k): v for k, v in obj.items()}


VARIABLE_OVERRIDE_ENV_PREFIX = "COMPOSEIO_VAR_"


def _variable_overrides_from_env(env: Optional[Dict[str, str]] = None
                                  ) -> Dict[str, str]:
    """[DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE] Document-variable
    overrides supplied via ``COMPOSEIO_VAR_<NAME>=<value>`` env vars — the
    per-physical-device launch source (a kiosk's systemd unit / launch script
    sets one env var per machine, no CLI plumbing required).  ``<NAME>`` is
    lowercased to match the scene's own (lowercase-by-convention) ``$name``
    variables; an absent prefix means no overrides, same as today."""
    env = os.environ if env is None else env
    out: Dict[str, str] = {}
    for key, value in env.items():
        if key.startswith(VARIABLE_OVERRIDE_ENV_PREFIX):
            name = key[len(VARIABLE_OVERRIDE_ENV_PREFIX):].lower()
            if name:
                out[name] = value
    return out


def _parse_var_flag(spec: str) -> tuple[str, str]:
    """Parse one ``--var name=value`` CLI flag."""
    if "=" not in spec:
        raise ValueError(f"--var must be name=value (got {spec!r})")
    name, _, value = spec.partition("=")
    name = name.strip()
    if not name:
        raise ValueError(f"--var must be name=value (got {spec!r})")
    return name, value


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(
        description="ComposeUX render engine — run a .compose.yaml live")
    ap.add_argument("config", help="path to .compose.yaml")
    ap.add_argument("--fullscreen", action="store_true",
                    help="borderless fullscreen (RPi kiosk mode)")
    ap.add_argument("--headless", metavar="OUT_PNG",
                    help="render one frame to PNG and exit")
    ap.add_argument("--transports", action="store_true",
                    help="connect the config's external transports (MQTT)")
    ap.add_argument("--scale", type=float, default=1.0, metavar="N",
                    help="window magnification: the design surface stays native "
                         "resolution, the window opens N× larger and the texture "
                         "is stretched to fill it (input maps back correctly)")
    ap.add_argument("--var", action="append", default=[], metavar="NAME=VALUE",
                    help="[DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE] override "
                         "a document `variables:` default at launch (repeatable); "
                         "e.g. --var station_1=cic.2.helm. Also settable via "
                         "COMPOSEIO_VAR_<NAME> env vars (this flag wins on a "
                         "name collision); an override for a name the scene "
                         "does not declare is ignored and logged.")
    args = ap.parse_args()

    variable_overrides = _variable_overrides_from_env()
    for spec in args.var:
        name, value = _parse_var_flag(spec)
        variable_overrides[name] = value

    if args.headless:
        app = RenderApp.from_file(args.config, windowed=False,
                                  variable_overrides=variable_overrides)
        app.start()
        print(app.render_headless(args.headless))
        app.stop()
        return

    app = RenderApp.from_file(args.config, fullscreen=args.fullscreen,
                              connect_transports=args.transports,
                              window_scale=args.scale,
                              variable_overrides=variable_overrides)
    # [SPEC:CXC-IC-SINGLETON] [CXC-IC-SURFACE-SINGLETON] — a SESSION-OWNING surface
    # is a host singleton, decided HERE (before any window exists) so the invariant
    # holds for EVERY launch path rather than only the one that remembers to check.
    # A second surface yields and exits 0; it must not open a window and must not
    # disturb the incumbent.  See DEFECT-TWO-CONSOLES-SPAWNED-THEN-KILLED.
    _surface_lock = _acquire_surface_singleton(app)
    if _surface_lock is False:
        return
    # [SPEC:CXC-CONSOLE-PROD] surfaces 1-3: apply the persisted personalization
    # (window/font/theme/custom) BEFORE run()/start() so the first frame is
    # already personalized.  Loaded by file path (the render engine keeps no
    # dependency on the intelligence tree); a no-op unless the scene declares
    # design.personalization.
    _maybe_apply_console_personalization(app)
    # [DEC-INPROCESS-SOURCES] When transports are on, ONE console launch also
    # brings up the session-owned data-source daemons IN-PROCESS (daemon threads
    # this process owns), pinned to THIS console's session — replacing the
    # fragile standalone start-console-daemons.sh path. Gated off with
    # COMPOSEIO_INPROCESS_SOURCES=0 (headless-daemon path). The render loop keeps
    # the main thread; sources are background threads. A missing intelligence
    # tree (render engine reused elsewhere) is a no-op, never a crash.
    sources = _maybe_start_inprocess_sources(app) if args.transports else None
    try:
        app.run()
    finally:
        if sources is not None:
            try:
                sources.stop()
            except Exception as exc:                      # never mask app exit
                logger.warning("in-process data sources stop failed: %s", exc)


def _surface_singleton_required(app: "RenderApp") -> bool:
    """Is this scene a SESSION-OWNING surface — one that may exist only once?

    Gated on the SAME declarative discriminator the in-process data layer uses
    (`design.data_sources: true`, `_scene_hosts_data_layer`), and for the same
    reason: a scene that hosts the per-`<id>` publishers OWNS this session's
    identity, and two of them fight over every role flock, every `self/*` topic and
    the transcript pin.  Never gate on the agent id (console and ribbon both run as
    `architect`) nor on `--transports` (the ribbon needs the bus too).

    CONTAINMENT, and it is the load-bearing half: a doc viewer, a hookup viewer, a
    beepout sheet and the Launch Bar ribbon declare NO data layer, so they are not
    constrained — several may run at once, which is their whole point.  Making a
    second viewer window impossible would be a worse regression than the duplicate
    console this closes.
    """
    return _scene_hosts_data_layer(app)


def _acquire_surface_singleton(app: "RenderApp"):
    """Hold the host singleton for this surface, or return False meaning YIELD.

    Returns the held lock object (keep it alive for the process lifetime), True when
    the scene is unconstrained, or False when a live surface already owns this app
    id — in which case `main()` returns WITHOUT opening a window.

    `COMPOSEIO_SURFACE_SUPERSEDE=1` makes this launch REAP the incumbent instead of
    yielding: the deliberate-bounce semantic ([CXC-IC-SINGLETON]'s SUPERSEDE variant,
    as the launcher already uses).  It is opt-in because the default must never kill
    the console the operator is working in.
    """
    if not _surface_singleton_required(app):
        return True
    app_id = getattr(getattr(app, "comp", None), "app_id", "") or ""
    try:
        from composeio.app_singleton import acquire_surface_singleton
    except Exception as exc:                      # never block a launch
        logger.warning("surface singleton unavailable (%s) — launching unguarded", exc)
        return True
    supersede = os.environ.get("COMPOSEIO_SURFACE_SUPERSEDE", "").strip() in ("1", "true", "yes")
    lock = acquire_surface_singleton(app_id, supersede=supersede)
    if lock is None:
        # stderr, not a log level a caller can silence: the operator asked for a
        # console and is getting none, and must be told exactly why.
        import sys as _sys
        _sys.stderr.write(
            f"[surface-singleton] a live surface already owns app id {app_id!r} — "
            f"yielding without opening a window ([CXC-IC-SURFACE-SINGLETON]). "
            f"To make THIS launch win a deliberate bounce, set "
            f"COMPOSEIO_SURFACE_SUPERSEDE=1.\n")
        return False
    return lock


def _maybe_apply_console_personalization(app: "RenderApp") -> None:
    """Apply persisted console personalization (surfaces 1-3) to *app* before
    it runs, by loading compose_projects/intelligence/console/personalization.py by FILE PATH
    (the render engine package keeps no dependency on the intelligence tree, the
    same discipline as _maybe_start_inprocess_sources).  A no-op for any scene
    that does not declare design.personalization, and never raises — a bad
    profile must not block a launch."""
    comp = getattr(app, "comp", None)
    if comp is None or not getattr(comp, "personalization", ""):
        return
    import importlib.util
    import sys as _sys
    root = os.environ.get("COMPOSEIO_REPO_ROOT", os.getcwd())
    path = os.path.join(root, "compose_projects", "intelligence", "console", "personalization.py")
    if not os.path.isfile(path):
        logger.info("console personalization: %s not present — skipping", path)
        return
    try:
        name = "composeio_console_personalization"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules[name] = mod
        spec.loader.exec_module(mod)          # type: ignore[union-attr]
        resolved = mod.apply_to_app(app)
        if resolved is not None:
            logger.info("console personalization applied: %s", resolved)
    except Exception as exc:                  # never block launch
        logger.warning("console personalization failed: %s", exc)


def _scene_hosts_data_layer(app: "RenderApp") -> bool:
    """[CXC-IC-SELF-IDENTITY] The in-process console data layer
    ([DEC-INPROCESS-SOURCES]) is hosted ONLY by a scene that DECLARATIVELY opts
    in via `design.data_sources: true` (parsed to `comp.data_sources`).  This is
    the discriminator that keeps a NON-console `--transports` scene sharing the
    same `<id>` — notably the Launch Bar ribbon, a pure tile scene — from
    co-hosting the data layer and winning the per-role singleton flocks
    (`composeio-self-publisher-<id>.lock`, ...) away from the real console.  Gate
    on the DECLARED intent of the SCENE, never on the agent-id (both scenes run
    as `architect`) or the mere presence of `--transports` (the ribbon needs the
    bus too).  Fail-safe: a scene that does not opt in NEVER hosts it."""
    comp = getattr(app, "comp", None)
    return bool(comp is not None and getattr(comp, "data_sources", False))


def _maybe_start_inprocess_sources(app: "RenderApp"):
    """Load compose_projects/intelligence/console/data_sources.py (by file path — the render
    engine package does not depend on the intelligence tree) and start the
    console data layer in-process. Returns the supervisor or None.

    [CXC-IC-SELF-IDENTITY] A no-op unless the SCENE declared `design.data_sources:
    true` (`_scene_hosts_data_layer`) — so the Launch Bar ribbon, run with
    `--transports` under the same architect id, does NOT bring up the console's
    session-owned publishers (it has no data_sources in its own YAML)."""
    import importlib.util
    import sys as _sys

    if not _scene_hosts_data_layer(app):
        logger.info("in-process data sources: scene %r does not declare "
                    "design.data_sources — NOT hosting the console data layer "
                    "(pure viewer/tile scene)",
                    getattr(getattr(app, "comp", None), "app_id", "?"))
        return None

    root = os.environ.get("COMPOSEIO_REPO_ROOT", os.getcwd())
    path = os.path.join(root, "compose_projects", "intelligence", "console", "data_sources.py")
    if not os.path.isfile(path):
        logger.info("in-process data sources: %s not present — skipping "
                    "(headless/reused render engine)", path)
        return None
    try:
        name = "composeio_console_data_sources"
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        # Register before exec so the module's @dataclass can resolve its own
        # module dict (dataclasses looks the class up via sys.modules).
        _sys.modules[name] = mod
        spec.loader.exec_module(mod)                       # type: ignore[union-attr]
        # HAND THE SOURCES THIS APP'S BUS ([SPEC:CXC-SOURCES-ON-BUS] SOB-C1).
        # Without it each source self-creates its own bus + transport, so the
        # console ends at TWO broker connections (the render bus + one shared
        # source bus) instead of ONE. Passing app.bus is the whole difference
        # between "the sources talk through the app's bus" and "the sources run
        # a second bus alongside it" — and a second bus is a second peer on the
        # same namespaces, which is the doubling class this arc exists to remove.
        #
        # HAND IT THE SCENE'S DECLARED INSTANCE LIST TOO ([CXC-FC-UNIT],
        # [CXC-FC-S1-BRIDGES]). The scene's `instances:` block is the SINGLE source
        # naming the Intelligences this console hosts; the widgets take their topics
        # from it, and the N channel bridges — the WRITE half of those same surfaces —
        # must take them from the SAME place or a pane's composer publishes to a topic
        # no bridge is unwrapping. We read it off the LIVE composition (authoritative:
        # it is the scene actually running), and pass each kwarg only when the loaded
        # module accepts it, so an older data_sources.py degrades to its own scene read
        # instead of dying in the broad except below (which would take the WHOLE data
        # layer down silently — the failure mode this file is full of scar tissue for).
        kwargs = {"bus": app.bus}
        import inspect as _inspect
        try:
            params = _inspect.signature(mod.start_console_data_layer).parameters
        except (TypeError, ValueError):
            params = {}

        instances = list(getattr(app.comp, "instances", []) or [])
        if instances:
            if "instances" in params:
                kwargs["instances"] = instances
            else:
                logger.warning("scene declares %d instance(s) but this "
                               "data_sources.start_console_data_layer takes no "
                               "`instances` — only the first will get a bridge",
                               len(instances))
        # The single-instance spelling, kept for an older data layer AND for the
        # derived `$chat_id` a scene may still declare directly.
        chat_id = ((getattr(app.comp, "variables", {}) or {}).get("chat_id")
                   or (instances[0]["id"] if instances else None))
        if chat_id and "chat_id" in params:
            kwargs["chat_id"] = str(chat_id)
        elif chat_id:
            logger.warning("scene names chat_id=%r but this "
                           "data_sources.start_console_data_layer takes no "
                           "chat_id — the channel bridge will resolve it "
                           "itself from the scene file", chat_id)
        return mod.start_console_data_layer(**kwargs)
    except Exception as exc:
        logger.warning("in-process data sources failed to start "
                       "(console runs as a viewer): %s", exc)
        return None


if __name__ == "__main__":
    main()
