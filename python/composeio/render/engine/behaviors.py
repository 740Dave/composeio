"""
ComposeUX State-Driven Behaviors ([SPEC:CXC-BIND] [CXC-BIND-BEHAVIOR])

A widget's ``behaviors:`` list maps a declarative bus-state trigger to a
named, ENGINE-OWNED temporal behavior applied to a target attribute:

    behaviors:
      - when: { namespace: red_alert, is: on }
        do:   { behavior: blink, target: visible, period_ms: 2000 }
      - when: { namespace: scanning, is: on }
        do:   { behavior: rotate, target: rotation, deg_per_s: 90 }

Principle ([CXC-BIND-PRINCIPLE]): the bus carries semantic STATE
(``red_alert = on``, published once); the widget owns the temporal
PRESENTATION.  Frames are never streamed over the bus, and an
Intelligence drives a Red-Alert screen by flipping one bit.

The behavior library is finite and engine-owned:

    blink            toggle the target at period_ms (duty optional)
    pulse            ease the target between from/to at period_ms
    ramp/ramp_color  one-shot interpolate toward `to` over duration_ms
    rotate           advance the target continuously at deg_per_s
    animate/transition  one-shot from->to over duration_ms with easing

Lifecycle ([CXC-BIND-C5]): a behavior is active while its trigger holds;
on release the target reverts to its base value (literal or bound).
Composition: conflicts on one target resolve last-declared-wins —
instances evaluate in declaration order, so the last write lands.

Determinism ([CXC-BIND-C6]): every behavior value is a pure function of
elapsed time since activation.  ``tick(now)`` takes the clock as an
argument; activation defers its start timestamp to the first tick, so a
fixed-timestep caller (RenderApp.render_frame_at) reproduces frames
byte-identically across platforms.  Builds on the [CXC-R-ENGINE-ANIM]
easing/interpolation vocabulary (reused, not reinvented).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .animation import EASING, _ease_out, _interpolate
from .bindings import canon, color_tuple, strip_code_fields, truthy

logger = logging.getLogger("composeio.render.behaviors")

BEHAVIOR_NAMES = frozenset({
    "blink", "pulse", "ramp", "ramp_color", "rotate", "animate", "transition",
})

_ONE_SHOT = frozenset({"ramp", "ramp_color", "animate", "transition"})

# ---------------------------------------------------------------------------
# [CXC-BE-INTROSPECT] — declarative behavior-model tables.
# The Composer binding editor renders its trigger/behavior/param fields by
# READING these; nothing is hardcoded in the editor.  A new behavior added
# to _evaluate + a schema row here appears in the editor automatically.
# ---------------------------------------------------------------------------

# Trigger conditions the `when:` block accepts ([CXC-BIND-BEHAVIOR] +
# the is_exact: escape hatch — raw string compare, no canonicalization —
# + the [SPEC:CXC-DSL] expr condition: `when: {expr: "..."}` evaluates
# a sandboxed multi-namespace expression to a canon on/off).
TRIGGER_KINDS: tuple = ("is", "is_exact", "truthy", "in", "always", "expr")

# Each behavior's params with types/defaults — the implicit schema of
# _evaluate, surfaced as a table.  type: number | value | color | enum.
BEHAVIOR_PARAM_SCHEMA: Dict[str, Dict[str, dict]] = {
    "blink": {
        "period_ms": {"type": "number", "default": 1000},
        "duty":      {"type": "number", "default": 0.5},
        "on":        {"type": "value",  "default": None},
        "off":       {"type": "value",  "default": 0.0},
    },
    "pulse": {
        "period_ms": {"type": "number", "default": 1000},
        "from":      {"type": "value",  "default": None},
        "to":        {"type": "value",  "default": 1.0},
        "easing":    {"type": "enum",   "default": "linear",
                      "values": tuple(EASING.keys())},
    },
    "ramp": {
        "to":          {"type": "value",  "default": None, "required": True},
        "from":        {"type": "value",  "default": None},
        "duration_ms": {"type": "number", "default": 500},
        "easing":      {"type": "enum",   "default": "linear",
                        "values": tuple(EASING.keys())},
    },
    "ramp_color": {
        "to":          {"type": "color",  "default": None, "required": True},
        "from":        {"type": "color",  "default": None},
        "duration_ms": {"type": "number", "default": 500},
        "easing":      {"type": "enum",   "default": "linear",
                        "values": tuple(EASING.keys())},
    },
    "rotate": {
        "deg_per_s": {"type": "number", "default": 90.0},
    },
    "animate": {
        "from":        {"type": "value",  "default": None},
        "to":          {"type": "value",  "default": None, "required": True},
        "duration_ms": {"type": "number", "default": 500},
        "easing":      {"type": "enum",   "default": "linear",
                        "values": tuple(EASING.keys())},
    },
}
BEHAVIOR_PARAM_SCHEMA["transition"] = BEHAVIOR_PARAM_SCHEMA["animate"]

# The natural target attribute per behavior (editor pre-select hint).
BEHAVIOR_DEFAULT_TARGET: Dict[str, str] = {
    "blink": "visible", "pulse": "opacity", "ramp": "opacity",
    "ramp_color": "color", "rotate": "rotation",
    "animate": "opacity", "transition": "opacity",
}


# ---------------------------------------------------------------------------
# Declarative pieces
# ---------------------------------------------------------------------------

@dataclass
class Trigger:
    """Declarative state condition ([CXC-BIND-BEHAVIOR] `when`):
    is: <value> / truthy / in: [lo, hi] / always — or, since
    [SPEC:CXC-DSL], the sanctioned trigger-condition site
    `when: {expr: "..."}`: a sandboxed multi-namespace expression
    evaluated to a canon on/off (kind "expr"; the runtime subscribes
    every namespace in ``expr.namespaces`` and calls evaluate_expr)."""
    namespace: Optional[str] = None
    kind: str = "truthy"            # is | truthy | in | always | expr
    value: Any = None               # is-value, or [lo, hi] for `in`
    expr: Any = None                # compiled dsl.Expression (expr kind)

    def evaluate_expr(self, ns_reader: Any) -> bool:
        """Evaluate an expr trigger against the bus ([CXC-DSL-SITES]:
        'evaluates to a canon on/off').  dt/t are 0 at this site (a
        trigger is event-evaluated, not integrated).  An eval error
        reads as inactive (warned once by the expression)."""
        from .dsl import DslEvalError, EvalContext, dsl_truthy
        try:
            return dsl_truthy(self.expr.evaluate(EvalContext(ns=ns_reader)))
        except DslEvalError as e:
            if "evalerr" not in self.expr._warned:
                self.expr._warned.add("evalerr")
                logger.warning("[CXC-DSL] trigger expr error (inactive): %s",
                               e)
            return False

    def matches(self, raw: Any) -> bool:
        if self.kind == "always":
            return True
        if self.kind == "is":
            # canonical compare: YAML `is: on` (bool True), binary-engine
            # 1/0, and string "on" all agree ([CXC-BIND-BEHAVIOR] trigger)
            return canon(raw) == canon(self.value)
        if self.kind == "is_exact":
            # the escape hatch when canonical tokens could collide: raw
            # string compare, no canonicalization ([CXC-BE-EDGES])
            return str(raw) == str(self.value)
        if self.kind == "in":
            try:
                lo, hi = float(self.value[0]), float(self.value[1])
                return lo <= float(raw) <= hi
            except (TypeError, ValueError, IndexError):
                return False
        return truthy(raw)


def parse_trigger(when: Any, where: str) -> Optional[Trigger]:
    """Parse a `when:` block.  Returns None on malformed input (warned)."""
    if when in ("always", True):
        return Trigger(kind="always")
    if not isinstance(when, dict):
        logger.warning("behavior on %s: when: must be a mapping or "
                       "'always' — ignored", where)
        return None
    if when.get("always"):
        return Trigger(kind="always")
    # [CXC-DSL-SITES] trigger-condition site: when: {expr: "..."} — the
    # multi-namespace condition (closes harvested use-cases #3/#4/#5).
    # pop_expr_field lifts it BEFORE any code-gate could see it; a parse
    # failure refuses the trigger (=> the behavior entry is ignored).
    from .bindings import pop_expr_field
    expr_src = pop_expr_field(when, f"{where}.when")
    if expr_src is not None:
        from . import dsl
        compiled = dsl.try_parse(expr_src, f"{where}.when")
        if compiled is None:
            return None
        return Trigger(kind="expr", expr=compiled)
    ns = when.get("namespace")
    if not ns:
        logger.warning("behavior on %s: when: needs namespace: — ignored",
                       where)
        return None
    if "is_exact" in when:
        return Trigger(namespace=str(ns), kind="is_exact",
                       value=when["is_exact"])
    if "is" in when:
        return Trigger(namespace=str(ns), kind="is", value=when["is"])
    if "in" in when:
        return Trigger(namespace=str(ns), kind="in", value=when["in"])
    return Trigger(namespace=str(ns), kind="truthy")


@dataclass
class BehaviorDecl:
    """One parsed `behaviors:` entry, attached to a widget."""
    trigger: Trigger
    behavior: str                   # library name
    target: str                     # widget attribute
    params: Dict[str, Any] = field(default_factory=dict)
    widget: Any = None


def parse_behaviors(spec_list: Any, where: str) -> List[BehaviorDecl]:
    """Parse a widget's `behaviors:` list.  Malformed entries are ignored
    with a warning; code-shaped params were already stripped by the
    parser's [CXC-BIND-BOUNDARY] gate."""
    out: List[BehaviorDecl] = []
    if not isinstance(spec_list, list):
        logger.warning("behaviors: on %s must be a list — ignored", where)
        return out
    for i, entry in enumerate(spec_list):
        if not isinstance(entry, dict):
            continue
        # [CXC-BIND-BOUNDARY]: no inline code anywhere in a behavior.
        strip_code_fields(entry, f"{where}[{i}]")
        if isinstance(entry.get("do"), dict):
            strip_code_fields(entry["do"], f"{where}[{i}].do")
        trig = parse_trigger(entry.get("when", "always"), f"{where}[{i}]")
        do = entry.get("do")
        if trig is None or not isinstance(do, dict):
            logger.warning("behavior %s[%d]: needs when: + do: — ignored",
                           where, i)
            continue
        name = str(do.get("behavior", "")).lower()
        if name not in BEHAVIOR_NAMES:
            logger.warning("behavior %s[%d]: unknown behavior %r (library: "
                           "%s) — ignored", where, i, name,
                           ", ".join(sorted(BEHAVIOR_NAMES)))
            continue
        target = str(do.get("target", ""))
        if not target:
            logger.warning("behavior %s[%d]: needs target: — ignored",
                           where, i)
            continue
        params = {k: v for k, v in do.items()
                  if k not in ("behavior", "target")}
        out.append(BehaviorDecl(trigger=trig, behavior=name,
                                target=target, params=params))
    return out


# ---------------------------------------------------------------------------
# Runtime instances
# ---------------------------------------------------------------------------

_SKIP = object()


@dataclass
class BehaviorInstance:
    decl: BehaviorDecl
    active: bool = False
    start: Optional[float] = None   # clock time of activation (first tick)
    base: Any = None                # target's value at activation (revert-to)
    done: bool = False              # one-shot completed


def _param_color(params: dict, key: str, fallback: Any) -> Any:
    v = params.get(key)
    if v is None:
        return fallback
    c = color_tuple(v)
    return c if c is not None else fallback


class BehaviorEngine:
    """Owns every BehaviorInstance; ticked once per frame by the runtime.

    set_active() is called on the render thread (trigger transitions are
    marshalled through the runtime's schedule_update, like every bus
    callback).  tick(now) evaluates active behaviors in declaration order
    (last-declared-wins on a shared target) and writes target values.
    """

    def __init__(self) -> None:
        self.instances: List[BehaviorInstance] = []
        # [CXC-R-DAMAGE] widgets whose target this tick() actually changed,
        # for the engine to scope the redraw to those widgets' regions
        # rather than dirtying the whole surface every animation frame.
        self.last_touched: List[Any] = []

    def add(self, decl: BehaviorDecl) -> BehaviorInstance:
        inst = BehaviorInstance(decl=decl)
        self.instances.append(inst)
        return inst

    def remove(self, insts: List[BehaviorInstance]) -> None:
        """Drop instances (e.g. a preview rebuild discarding its widgets)."""
        gone = set(id(i) for i in insts)
        self.instances = [i for i in self.instances if id(i) not in gone]

    # -- lifecycle ([CXC-BIND-C5]) ------------------------------------------

    def set_active(self, inst: BehaviorInstance, active: bool) -> None:
        if active == inst.active:
            return                      # re-publish of same state: no restart
        if active:
            inst.base = getattr(inst.decl.widget, inst.decl.target, None)
            inst.start = None           # stamped on the next tick's clock
            inst.done = False
            inst.active = True
        else:
            inst.active = False
            inst.start = None
            if inst.base is not None or inst.decl.target == "visible":
                setattr(inst.decl.widget, inst.decl.target, inst.base)

    def notify_base_change(self, widget: Any, attr: str, value: Any) -> None:
        """An attribute binding wrote a new base while a behavior holds the
        target: revert-on-release must land on the NEW bound value."""
        for inst in self.instances:
            if inst.active and inst.decl.widget is widget \
                    and inst.decl.target == attr:
                inst.base = value

    # -- per-frame evaluation ------------------------------------------------

    def tick(self, now: float) -> bool:
        """Advance all active behaviors at clock *now*.  Returns True while
        any behavior still needs frames (continuous active, or a one-shot
        mid-flight) so the runtime keeps the composition dirty."""
        busy = False
        self.last_touched = []
        for inst in self.instances:
            if not inst.active:
                continue
            if inst.start is None:
                inst.start = now
            t = max(0.0, now - inst.start)
            value = self._evaluate(inst, t)
            if value is not _SKIP:
                w = inst.decl.widget
                if getattr(w, inst.decl.target, None) != value:
                    self.last_touched.append(w)
                setattr(w, inst.decl.target, value)
            name = inst.decl.behavior
            if name in _ONE_SHOT:
                busy = busy or not inst.done
            else:
                busy = True
        return busy

    @property
    def active(self) -> bool:
        return any(i.active for i in self.instances)

    # -- the behavior library (pure functions of elapsed time) ---------------

    def _evaluate(self, inst: BehaviorInstance, t: float) -> Any:
        p = inst.decl.params
        name = inst.decl.behavior
        base = inst.base
        easing = EASING.get(str(p.get("easing", "linear")), _ease_out)

        if name == "blink":
            period = float(p.get("period_ms", 1000)) / 1000.0
            duty = float(p.get("duty", 0.5))
            on_phase = (t % period) < period * duty if period > 0 else True
            if isinstance(base, bool) or inst.decl.target == "visible":
                return bool(base) and on_phase if base is not None \
                    else on_phase
            on_v = p.get("on", base if base is not None else 1.0)
            off_v = p.get("off", 0.0)
            return on_v if on_phase else off_v

        if name == "pulse":
            period = float(p.get("period_ms", 1000)) / 1000.0
            frm = p.get("from", base if base is not None else 0.0)
            to = p.get("to", 1.0)
            if inst.decl.target in ("color", "fill_color", "stroke_color"):
                frm = _param_color(p, "from", base)
                to = _param_color(p, "to", base)
            if period <= 0:
                return to
            phase = (t % period) / period          # 0..1
            u = phase * 2.0 if phase < 0.5 else 2.0 - phase * 2.0
            return _interpolate(frm, to, easing(u))

        if name == "rotate":
            dps = float(p.get("deg_per_s", 90.0))
            try:
                b = float(base) if base is not None else 0.0
            except (TypeError, ValueError):
                b = 0.0
            return (b + dps * t) % 360.0

        if name in _ONE_SHOT:                       # ramp / ramp_color /
            dur = float(p.get("duration_ms", 500)) / 1000.0  # animate/transition
            frm = p.get("from", base)
            to = p.get("to")
            if name == "ramp_color" or inst.decl.target in (
                    "color", "fill_color", "stroke_color"):
                frm = _param_color(p, "from", base)
                to = _param_color(p, "to", base)
            if to is None:
                inst.done = True
                return _SKIP
            if frm is None:
                frm = 0.0
            u = 1.0 if dur <= 0 else min(t / dur, 1.0)
            if u >= 1.0:
                inst.done = True
                return to
            return _interpolate(frm, to, easing(u))

        return _SKIP
