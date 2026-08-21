"""
ComposeUX Declarative States & Transitions ([SPEC:CXC-BIND]
[CXC-BIND-STATES] + [CXC-BIND-TRANSITION])

A widget may carry three declarative blocks:

    states:            named, multi-attribute target configurations
      idle:      { opacity: 0.0, scale: 1.6, fill_color: "#3FB0FF" }
      acquiring: { opacity: 1.0, scale: 1.15, fill_color: "#FFCC00" }
      locked:    { opacity: 1.0, scale: 1.0,  fill_color: "#FF3B30" }

    state:             WHICH state is active ("state on the bus") — the same
                       three forms as an attr binding: bare namespace,
                       {namespace, map, default}, or {expr: "..."}.
      { namespace: fire_control/lock,
        map: { none: idle, acquiring: acquiring, tracking: locked },
        default: idle }

    transitions:       HOW to move between states — a flat from/to-matched
                       rule list (our own shape, derived from behaviors: —
                       NOT Slint's in/out blocks).  The first matching rule
                       wins; an attr no rule covers SNAPS.
      - animate: [opacity, fill_color]
        duration_ms: 220
        easing: ease_out
      - animate: [scale]
        to: locked
        duration_ms: 140
        easing: cubic-bezier(0.2, 0.9, 0.1, 1.0)
        delay_ms: 40

Relationship to behaviors ([CXC-BIND-BEHAVIOR]): a behavior is the
CONTINUOUS temporal presentation of ONE attribute while a trigger holds
(blink/pulse/rotate); a state is a NAMED, discrete, multi-attribute
target selected by bus state, with a transition tween describing how to
move between two states.  They compose (a locked reticle also pulses);
conflicts on a shared target resolve last-declared-wins, and the
StateEngine writes on the same pass as the BehaviorEngine.

Determinism ([CXC-BIND-C6]): this StateEngine mirrors BehaviorEngine's
discipline — every tween value is a pure function of elapsed time since
activation, and the activation start-stamp is DEFERRED to the first
tick's logical clock (NOT stamped at set_state() call time).  So a
fixed-timestep caller (RenderApp.render_frame_at) reproduces frames
byte-identically across platforms.  This is why transitions run here and
NOT through engine/animation.Animator (whose animate() stamps
time.monotonic() at call time — reproducible only under a wall clock).

Boundary ([CXC-BIND-BOUNDARY]): a state config is LITERAL values only —
strip_code_fields + validate_map refuse any inline code/arithmetic; that
is DSL territory.  RCE-safe on the fetch-from-bus fleet.

Clean-room (MIT): derived from ComposeUX's own behaviors.py + animation.py
and the W3C CSS-animation vocabulary (duration/delay/iteration-count/
cubic-bezier).  The cubic-bezier timing function REUSES the Lottie bezier
solver by reference (render/lottie/model.bezier_ease) — one solver, three
consumers (desktop, Lottie, states), all bit-identical cross-platform.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..lottie.model import bezier_ease           # R3: reuse the solver, no fork
from .animation import EASING, _ease_out, _interpolate
from .bindings import (AttrBinding, _UNCHANGED, canon, coerce_bound_value,
                       pop_expr_field, strip_code_fields, validate_map)

logger = logging.getLogger("composeio.render.states")


# ---------------------------------------------------------------------------
# [CXC-R-ENGINE-ANIM] easing resolver — named entries, CSS aliases, and the
# parameterized cubic-bezier(a,b,c,d) timing function.
# ---------------------------------------------------------------------------

_CUBIC_RE = re.compile(r"^\s*cubic[-_]bezier\s*\(([^)]*)\)\s*$", re.IGNORECASE)

# W3C CSS timing-function keyword equivalents, as canonical cubic-beziers.
# These are the standard control points from the CSS Easing spec — a public
# W3C standard, not proprietary.  Our own underscore-named entries
# (ease_in/ease_out/ease_in_out in animation.EASING) are DISTINCT curves and
# win first; the hyphenated CSS names resolve here so `ease-in-out` works.
_CSS_ALIASES: Dict[str, tuple] = {
    "ease":        (0.25, 0.1, 0.25, 1.0),
    "ease-in":     (0.42, 0.0, 1.0, 1.0),
    "ease-out":    (0.0, 0.0, 0.58, 1.0),
    "ease-in-out": (0.42, 0.0, 0.58, 1.0),
}


def _bezier_fn(x1: float, y1: float, x2: float, y2: float
               ) -> Callable[[float], float]:
    """A timing function from four control values, evaluated by the shared
    Lottie cubic-bezier solver ([R3] reuse-by-reference — desktop, Lottie,
    and states all solve cubic-bezier identically, and inherit the
    'Python floats are bit-identical cross-platform' property)."""
    return lambda u: bezier_ease(u, x1, y1, x2, y2)


def resolve_easing(spec: Any) -> Callable[[float], float]:
    """Resolve an easing spec to a timing function ([CXC-BIND-TRANSITION]).

    Accepts: a callable (passed through); a named entry from the existing
    EASING table (linear/ease_in/ease_out/ease_in_out/spring); a CSS-name
    alias (ease / ease-in / ease-out / ease-in-out); or the parameterized
    `cubic-bezier(a, b, c, d)`.  Unknown -> ease_out (warned)."""
    if callable(spec):
        return spec
    name = str(spec if spec is not None else "").strip()
    if not name:
        return EASING["linear"]
    if name in EASING:                       # our own named curves win first
        return EASING[name]
    low = name.lower()
    if low in _CSS_ALIASES:
        return _bezier_fn(*_CSS_ALIASES[low])
    alt = low.replace("-", "_")              # tolerate ease-in for ease_in etc.
    if alt in EASING:
        return EASING[alt]
    m = _CUBIC_RE.match(low)
    if m:
        try:
            parts = [float(p.strip()) for p in m.group(1).split(",")
                     if p.strip() != ""]
        except ValueError:
            parts = []
        if len(parts) == 4:
            return _bezier_fn(*parts)
        logger.warning("[CXC-BIND-TRANSITION] cubic-bezier needs 4 numbers, "
                       "got %r — falling back to ease_out", name)
        return _ease_out
    logger.warning("[CXC-BIND-TRANSITION] unknown easing %r — ease_out", name)
    return _ease_out


# ---------------------------------------------------------------------------
# Declarative pieces (parsed from .compose.yaml)
# ---------------------------------------------------------------------------

@dataclass
class StateSelector:
    """WHICH state is active ([CXC-BIND-STATES] selector).  Mirrors the
    AttrBinding namespace/map/expr forms, but resolves to a STATE NAME
    (a string) rather than a typed attribute value."""
    namespace: str = ""
    map: Optional[dict] = None
    default: Optional[str] = None
    expr: Any = None                     # compiled dsl.Expression | None

    @property
    def namespaces(self) -> List[str]:
        if self.expr is not None:
            return list(self.expr.namespaces)
        return [self.namespace] if self.namespace else []

    def _name_for(self, raw: Any) -> Optional[str]:
        if raw is None:
            return self.default
        if self.map is not None:
            key = str(raw)
            if key in self.map:
                return str(self.map[key])
            ckey = canon(raw)                # on/off/True/1 agree, like triggers
            for k, v in self.map.items():
                if canon(k) == ckey:
                    return str(v)
            return self.default
        return str(raw)

    def resolve_raw(self, raw: Any) -> Optional[str]:
        """Resolve directly from a bus value (namespace form)."""
        return self._name_for(raw)

    def resolve(self, ns_reader: Callable[[str], Any]) -> Optional[str]:
        """Resolve against the live bus (expr form, or namespace lookup)."""
        if self.expr is not None:
            from . import dsl
            try:
                val = self.expr.evaluate(dsl.EvalContext(ns=ns_reader))
            except dsl.DslEvalError as e:
                if "evalerr" not in self.expr._warned:
                    self.expr._warned.add("evalerr")
                    logger.warning("[CXC-DSL] state selector expr error "
                                   "(default): %s", e)
                return self.default
            return self._name_for(val)
        return self._name_for(ns_reader(self.namespace) if self.namespace
                              else None)


@dataclass
class TransitionRule:
    """HOW to move between states ([CXC-BIND-TRANSITION]).  Matched by
    from/to state and the animated-attr set; the first matching rule in
    declaration order wins."""
    attrs: Optional[frozenset]           # None = any attr
    from_state: Optional[str]            # None = any
    to_state: Optional[str]              # None = any
    duration_ms: float
    easing: Callable[[float], float]
    delay_ms: float = 0.0
    iteration_count: float = 1.0         # >=1, or math.inf for loop
    # raw easing spec retained for Composer introspection (edit round-trip)
    easing_spec: str = "ease_out"

    def matches(self, attr: str, frm: Optional[str], to: Optional[str]) -> bool:
        if self.attrs is not None and attr not in self.attrs:
            return False
        if self.from_state is not None and self.from_state != frm:
            return False
        if self.to_state is not None and self.to_state != to:
            return False
        return True


@dataclass
class _Tween:
    """One in-flight per-attribute tween.  ``start`` is DEFERRED to the
    first tick (determinism, [CXC-BIND-C6])."""
    widget: Any
    attr: str
    from_val: Any
    to_val: Any
    duration: float                      # seconds (>=0)
    easing: Callable[[float], float]
    delay: float = 0.0                   # seconds
    iterations: float = 1.0
    start: Optional[float] = None
    done: bool = False


@dataclass
class StateMachine:
    """One per widget carrying a ``states:`` block."""
    states: Dict[str, Dict[str, Any]]
    selector: StateSelector
    transitions: List[TransitionRule]
    widget: Any = None
    current: Optional[str] = None
    active: List[_Tween] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _str_or_none(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def _iteration_count(rule: dict) -> float:
    if canon(rule.get("loop")) == "on":
        return math.inf
    try:
        n = float(rule.get("iteration_count", 1))
    except (TypeError, ValueError):
        n = 1.0
    return n if n == math.inf else max(1.0, n)


def _parse_states(raw: Any, where: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        logger.warning("[CXC-BIND-STATES] states: on %s must be a mapping "
                       "of name -> attr config — ignored", where)
        return out
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            logger.warning("[CXC-BIND-STATES] state %r on %s must be an attr "
                           "map — ignored", name, where)
            continue
        site = f"{where}.states.{name}"
        strip_code_fields(cfg, site)                 # [CXC-BIND-BOUNDARY]
        clean = validate_map(cfg, site)              # literal-only gate
        if clean is None:
            continue                                 # refused config -> skip
        out[str(name)] = clean
    return out


def _parse_selector(raw: Any, where: str) -> StateSelector:
    if isinstance(raw, str) and raw:
        return StateSelector(namespace=raw)
    if not isinstance(raw, dict):
        return StateSelector()                       # no selector: stays at default
    site = f"{where}.state"
    expr_src = pop_expr_field(raw, site)
    if expr_src is not None:
        from . import dsl
        compiled = dsl.try_parse(expr_src, site)
        if compiled is not None:
            return StateSelector(expr=compiled,
                                 default=_str_or_none(raw.get("default")))
    strip_code_fields(raw, site)
    vmap = None
    if "map" in raw:
        vmap = validate_map(raw.get("map"), f"{site}.map")
    return StateSelector(namespace=str(raw.get("namespace") or ""),
                         map=vmap, default=_str_or_none(raw.get("default")))


def _parse_transitions(raw: Any, where: str) -> List[TransitionRule]:
    out: List[TransitionRule] = []
    if raw is None:
        return out
    if not isinstance(raw, list):
        logger.warning("[CXC-BIND-TRANSITION] transitions: on %s must be a "
                       "list — ignored", where)
        return out
    for i, rule in enumerate(raw):
        if not isinstance(rule, dict):
            continue
        strip_code_fields(rule, f"{where}.transitions[{i}]")
        animate = rule.get("animate")
        attrs: Optional[frozenset] = None
        if isinstance(animate, (list, tuple)):
            attrs = frozenset(str(a) for a in animate)
        elif isinstance(animate, str) and animate:
            attrs = frozenset({animate})
        easing_spec = str(rule.get("easing", "ease_out"))
        try:
            dur = float(rule.get("duration_ms", 300))
        except (TypeError, ValueError):
            dur = 300.0
        try:
            delay = float(rule.get("delay_ms", 0))
        except (TypeError, ValueError):
            delay = 0.0
        out.append(TransitionRule(
            attrs=attrs,
            from_state=_str_or_none(rule.get("from")),
            to_state=_str_or_none(rule.get("to")),
            duration_ms=dur,
            easing=resolve_easing(easing_spec),
            delay_ms=delay,
            iteration_count=_iteration_count(rule),
            easing_spec=easing_spec))
    return out


def parse_state_machine(spec: dict, where: str) -> Optional[StateMachine]:
    """Parse a widget's states:/state:/transitions: blocks into a
    StateMachine ([CXC-BIND-STATES]).  Returns None when no usable states
    survive the boundary gate."""
    states = _parse_states(spec.get("states"), where)
    if not states:
        return None
    selector = _parse_selector(spec.get("state"), where)
    transitions = _parse_transitions(spec.get("transitions"), where)
    # An unresolved default falls back to the first declared state so the
    # widget always has a defined resting configuration.
    if selector.default is None or selector.default not in states:
        selector.default = next(iter(states))
    return StateMachine(states=states, selector=selector,
                        transitions=transitions)


# ---------------------------------------------------------------------------
# gestures: producer block ([CXC-RM-GESTURE]) — a gesture PUBLISHES a
# semantic bus state; the state selector CONSUMES it.  One state path, two
# producers (a finger and an Intelligence reach it identically).
# ---------------------------------------------------------------------------

# The engine-synthesized gestures a producer block may map (the ones the
# runtime already recognizes).  double_tap is an author-friendly alias for
# the engine's double_click.
GESTURE_NAMES = frozenset({
    "tap", "double_tap", "double_click", "long_press",
    "pan", "pan_start", "pan_end", "swipe",
})

# The one non-literal, engine-provided substitution: the pointer's surface
# coordinates at publish time.  @pointer -> {x, y}; @pointer_x / @pointer_y
# -> the scalar coordinate (so x/y can bind to separate namespaces).
POINTER_TOKENS = frozenset({"@pointer", "@pointer_x", "@pointer_y"})


@dataclass
class GesturePublish:
    namespace: str
    value: Any                           # literal, or an @pointer token


def parse_gestures(raw: Any, where: str) -> Dict[str, List[GesturePublish]]:
    """Parse a widget's ``gestures:`` list into gesture-name -> publishes
    ([CXC-RM-GESTURE]).  Each entry maps ``on: <gesture>`` to one or more
    ``publish: {namespace, value}`` (value may be an @pointer token)."""
    out: Dict[str, List[GesturePublish]] = {}
    if not isinstance(raw, list):
        logger.warning("[CXC-RM-GESTURE] gestures: on %s must be a list — "
                       "ignored", where)
        return out
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        # YAML 1.1 loads the bare field name `on:` as boolean True (the same
        # trap as `is: on`); recover it so `on: <gesture>` reads canonically
        # without forcing the author to quote the field name.
        entry = {("on" if k is True else "off" if k is False else k): v
                 for k, v in entry.items()}
        strip_code_fields(entry, f"{where}.gestures[{i}]")
        on = str(entry.get("on", "")).lower()
        if on not in GESTURE_NAMES:
            logger.warning("[CXC-RM-GESTURE] gesture %s[%d]: unknown gesture "
                           "%r (recognized: %s) — ignored", where, i, on,
                           ", ".join(sorted(GESTURE_NAMES)))
            continue
        pub = entry.get("publish")
        specs = pub if isinstance(pub, list) else [pub]
        for p in specs:
            if not isinstance(p, dict):
                continue
            strip_code_fields(p, f"{where}.gestures[{i}].publish")
            ns = p.get("namespace")
            if not ns:
                logger.warning("[CXC-RM-GESTURE] gesture %s[%d]: publish needs "
                               "namespace: — ignored", where, i)
                continue
            out.setdefault(on, []).append(
                GesturePublish(namespace=str(ns), value=p.get("value", "")))
    return out


# ---------------------------------------------------------------------------
# Runtime engine
# ---------------------------------------------------------------------------

def _coerce_state_value(widget: Any, attr: str, raw: Any) -> Any:
    """Coerce a literal state value for widget.<attr>, reusing the exact
    binding coercion ([CXC-BIND-ATTR]) so a state value and a bound value
    of the same attr type-agree.  Returns _UNCHANGED when inapplicable."""
    return coerce_bound_value(widget, AttrBinding(namespace="", attr=attr), raw)


class StateEngine:
    """Owns every StateMachine; ticked once per frame by the runtime, right
    beside BehaviorEngine.tick(t) ([CXC-BIND-STATES]).

    set_state() is called on the render thread (selector transitions are
    marshalled through the runtime's schedule_update, like every bus
    callback).  tick(now) advances in-flight tweens (deferred start-stamp),
    writes attribute values, and records ``last_touched`` so the runtime
    damages ONLY the animating widgets' bounds ([CXC-R-DAMAGE])."""

    def __init__(self, reduced_motion: bool = False) -> None:
        self.machines: List[StateMachine] = []
        # [CXC-R-DAMAGE] widgets whose attr a tick() actually changed.
        self.last_touched: List[Any] = []
        # [CXC-ICV-MOTION] under reduced motion transitions SNAP (apply the
        # target instantly, no tween) — the REST-state courtesy.
        self.reduced_motion = bool(reduced_motion)

    def add(self, sm: StateMachine) -> StateMachine:
        self.machines.append(sm)
        return sm

    def remove(self, machines: List[StateMachine]) -> None:
        """Drop machines (a preview rebuild discarding its widgets)."""
        gone = set(id(m) for m in machines)
        self.machines = [m for m in self.machines if id(m) not in gone]

    @property
    def active(self) -> bool:
        return any(m.active for m in self.machines)

    # -- selection --------------------------------------------------------

    def set_state(self, sm: StateMachine, raw: Any, *,
                  animate: bool = True) -> None:
        """Enter the state the selector maps *raw* (a bus value) to."""
        self._enter(sm, sm.selector.resolve_raw(raw), animate=animate)

    def set_state_expr(self, sm: StateMachine,
                       ns_reader: Callable[[str], Any], *,
                       animate: bool = True) -> None:
        """Enter the state an expr/namespace selector resolves against the
        live bus."""
        self._enter(sm, sm.selector.resolve(ns_reader), animate=animate)

    def _enter(self, sm: StateMachine, name: Optional[str], *,
               animate: bool) -> None:
        if name is None or name not in sm.states:
            name = sm.selector.default
        if name is None or name not in sm.states or name == sm.current:
            return
        frm = sm.current
        target = sm.states[name]
        snap_all = (not animate) or self.reduced_motion
        w = sm.widget
        for attr, raw_target in target.items():
            to_val = _coerce_state_value(w, attr, raw_target)
            if to_val is _UNCHANGED:
                continue
            # any in-flight tween on this attr is superseded by the new edge
            sm.active = [t for t in sm.active if t.attr != attr]
            if snap_all:
                setattr(w, attr, to_val)
                continue
            rule = self._match(sm, attr, frm, name)
            from_val = getattr(w, attr, None)
            if rule is None or from_val is None or from_val == to_val:
                setattr(w, attr, to_val)                # snap: unmatched/equal
                continue
            sm.active.append(_Tween(
                widget=w, attr=attr, from_val=from_val, to_val=to_val,
                duration=max(rule.duration_ms, 0.0) / 1000.0,
                easing=rule.easing,
                delay=max(rule.delay_ms, 0.0) / 1000.0,
                iterations=rule.iteration_count))
        sm.current = name
        # schedule the first transition frame (and, for a pure snap, the
        # settled repaint); the tick loop drives the rest.
        if getattr(w, "_ctx", None) is not None:
            w.request_redraw()

    def _match(self, sm: StateMachine, attr: str,
               frm: Optional[str], to: Optional[str]) -> Optional[TransitionRule]:
        for rule in sm.transitions:
            if rule.matches(attr, frm, to):
                return rule
        return None

    # -- per-frame --------------------------------------------------------

    def tick(self, now: float) -> bool:
        """Advance every in-flight tween at clock *now* (deferred start-stamp
        -> byte-reproducible under render_frame_at).  Returns True while any
        transition is still in flight, so the runtime keeps the composition
        dirty ONLY while animating; a settled machine costs zero."""
        self.last_touched = []
        busy = False
        for sm in self.machines:
            if not sm.active:
                continue
            still: List[_Tween] = []
            for tw in sm.active:
                if tw.start is None:
                    tw.start = now                       # deferred stamp
                elapsed = now - tw.start - tw.delay
                finished = False
                if elapsed < 0.0:
                    still.append(tw)                     # in the delay window
                    busy = True
                    continue
                if tw.duration <= 0.0:
                    value = tw.to_val
                    finished = True
                else:
                    local = elapsed / tw.duration
                    if local >= tw.iterations:
                        value = tw.to_val
                        finished = True
                    else:
                        u = local - math.floor(local)
                        value = _interpolate(tw.from_val, tw.to_val,
                                             tw.easing(u))
                w = tw.widget
                if getattr(w, tw.attr, None) != value:
                    if w not in self.last_touched:
                        self.last_touched.append(w)
                    setattr(w, tw.attr, value)
                if finished:
                    tw.done = True
                else:
                    still.append(tw)
                    busy = True
            sm.active = still
        return busy
