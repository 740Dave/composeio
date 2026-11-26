"""state_manager — the reactive BINDINGS ENGINE, SHED from the kernel.

This is the code that used to live in ``bus.py:1016-1222`` (``add_binding`` /
``add_compound_binding`` / ``add_event_binding`` and their evaluators). It moved
here VERBATIM ([DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23]): the kernel was DOING
TRANSFORMS — deriving a NEW fact (the target value) from a source change —
which violates [DX-CONNECTOR-FORWARDS-NEVER-TRANSFORMS] / [CXC-CBK-CONNECTOR-
NEVER-TRANSFORMS]. A connector forwards by scope and encodes; deriving a new
fact is a state_manager PEER's job, never the kernel's.

BEHAVIOR IS PRESERVED. The engine binds to the kernel through the sanctioned
POST-PUBLISH HOOK ([bus].register_post_publish_hook), fired at the exact site the
baked-in engine ran, so the ordering the kernel gave its own bindings is the
ordering the plugin sees. A bare ``ComposeIOBus()`` installs a default engine so
``bus.add_binding(...)`` keeps working unchanged; ``StateManagerPlugin`` is the
same engine wearing the plugin contract for the standup/registry path.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from composebus.plugin import ComposePlugin

logger = logging.getLogger("composeio.plugins.state_manager")


# --------------------------------------------------------------------------- #
# helpers — moved verbatim from bus.py's module tail
# --------------------------------------------------------------------------- #
def _parse_condition_namespace(expr: str) -> "str | None":
    """Extract namespace name from a condition expression like 'ftl_arm == "on"'."""
    parts = expr.strip().split(None, 1)
    return parts[0] if parts else None


def _compare(actual: Any, op: str, expected: str) -> bool:
    """Compare actual value against expected using operator.

    Supports: ==, !=, <, >, <=, >=. For numeric comparisons, attempts float
    conversion."""
    if op == "==":
        return str(actual) == expected
    if op == "!=":
        return str(actual) != expected
    try:
        a = float(actual)
        e = float(expected)
    except (TypeError, ValueError):
        return False
    if op == "<":
        return a < e
    if op == ">":
        return a > e
    if op == "<=":
        return a <= e
    if op == ">=":
        return a >= e
    return False


class BindingEngine:
    """The reactive binding evaluator — a state_manager PEER, not the kernel.

    Owns the bindings list and derives target values on source change. Bound to
    a bus through the post-publish hook + event subscriptions; the kernel never
    calls into it by name.
    """

    def __init__(self, bus):
        self._bus = bus
        self._bindings: list[dict] = []

    @property
    def bindings(self) -> list[dict]:
        return self._bindings

    def install(self) -> None:
        """Wire onto the bus: observe every state publish at the kernel's own
        binding-evaluation site."""
        self._bus.register_post_publish_hook(self.on_source_change)

    def uninstall(self) -> None:
        self._bus.unregister_post_publish_hook(self.on_source_change)

    # --- authoring API (the former bus.add_* methods) -------------------- #
    def add_binding(self, source_ns: str, target_ns: str,
                    transform_config: "list[dict] | None" = None) -> None:
        """Add a simple sensor-to-actuator binding.

        When source_ns changes, the new value is transformed and published to
        target_ns. No intermediary server needed."""
        from composebus.transforms import TransformChain
        chain = TransformChain.from_config(transform_config) if transform_config else None
        self._bindings.append({
            "type": "simple",
            "source": source_ns,
            "target": target_ns,
            "transform": chain,
        })
        logger.debug(f"Binding: {source_ns} → {target_ns}")

    def add_compound_binding(self, config: dict) -> None:
        """Add a compound namespace binding — multi-source conditions.

        Evaluated whenever ANY referenced input namespace changes; first
        matching condition wins (top-to-bottom priority)."""
        output_ns = config["output_namespace"]
        requires = config.get("requires")
        default = config.get("default", "off")
        conditions = config.get("conditions", [])

        input_namespaces = set()
        if requires:
            ns_name = _parse_condition_namespace(requires)
            if ns_name:
                input_namespaces.add(ns_name)
        for cond in conditions:
            for ns_name in cond.get("when", {}).keys():
                input_namespaces.add(ns_name)

        binding = {
            "type": "compound",
            "output": output_ns,
            "requires": requires,
            "default": default,
            "conditions": conditions,
            "input_namespaces": input_namespaces,
        }
        self._bindings.append(binding)
        logger.debug(f"Compound binding: {input_namespaces} → {output_ns}")

    def add_event_binding(self, config: dict) -> None:
        """Add an event-triggered binding with side effects."""
        trigger = config["trigger"]
        parts = trigger.split(".")
        trigger_ns = parts[0]

        binding = {
            "type": "event_trigger",
            "trigger_namespace": trigger_ns,
            "trigger_condition": trigger,
            "output": config.get("output_namespace"),
            "sequence": config.get("sequence", []),
            "side_effects": config.get("side_effects", []),
        }
        self._bindings.append(binding)

        self._bus.subscribe_events(
            trigger_ns,
            lambda ns, evt, data: self._handle_event_binding(binding, ns, evt, data))
        logger.debug(
            f"Event binding: {trigger_ns} event → {config.get('output_namespace')}")

    # --- evaluation (the former bus._evaluate_* / _eval_* / _handle_*) --- #
    def on_source_change(self, source_ns: str, value: Any) -> None:
        """Evaluate all bindings triggered by a source namespace change.

        This is the post-publish hook. It replaces the kernel's former inline
        ``_evaluate_bindings`` call, fired at the identical site."""
        for binding in self._bindings:
            if binding["type"] == "simple":
                if binding["source"] == source_ns:
                    result = value
                    if binding["transform"]:
                        result = binding["transform"].apply(value)
                    if result is not None:
                        self._bus.publish_state(binding["target"], result,
                                                source=f"binding:{source_ns}")
            elif binding["type"] == "compound":
                if source_ns in binding["input_namespaces"]:
                    self._evaluate_compound_binding(binding)

    def _evaluate_compound_binding(self, binding: dict) -> None:
        output_ns = binding["output"]

        if binding["requires"]:
            if not self._eval_condition_expr(binding["requires"]):
                current = self._bus.get_state(output_ns)
                if current != binding["default"]:
                    self._bus.publish_state(output_ns, binding["default"],
                                            source="compound_binding")
                return

        for cond in binding["conditions"]:
            when = cond.get("when", {})
            if self._eval_when_conditions(when):
                current = self._bus.get_state(output_ns)
                target_val = cond["value"]
                if current != target_val:
                    self._bus.publish_state(output_ns, target_val,
                                            source="compound_binding")
                return

        current = self._bus.get_state(output_ns)
        if current != binding["default"]:
            self._bus.publish_state(output_ns, binding["default"],
                                    source="compound_binding")

    def _eval_condition_expr(self, expr: str) -> bool:
        parts = expr.strip().split(None, 2)
        if len(parts) != 3:
            return False
        ns_name, op, expected = parts
        expected = expected.strip('"').strip("'")
        actual = self._bus.get_state(ns_name)
        if actual is None:
            return False
        return _compare(actual, op, expected)

    def _eval_when_conditions(self, when: dict) -> bool:
        for ns_name, expr in when.items():
            actual = self._bus.get_state(ns_name)
            parts = expr.strip().split(None, 1)
            if len(parts) != 2:
                return False
            op, expected = parts
            expected = expected.strip('"').strip("'")
            if actual is None:
                if op == "!=":
                    return True
                return False
            if not _compare(actual, op, expected):
                return False
        return True

    def _handle_event_binding(self, binding: dict, ns_name: str,
                              event_type: str, data: dict) -> None:
        trigger = binding["trigger_condition"]
        if ".event" in trigger:
            parts = trigger.split(".event")
            expected_ns = parts[0].strip()
            rest = parts[1].strip()
            if rest.startswith("=="):
                expected_evt = rest[2:].strip().strip('"').strip("'")
                if ns_name != expected_ns or event_type != expected_evt:
                    return
            else:
                return
        else:
            return

        for effect in binding.get("side_effects", []):
            if "set" in effect:
                target = effect["set"]
                if "→" in target:
                    t_ns, t_val = target.split("→", 1)
                    self._bus.publish_state(t_ns.strip(), t_val.strip().strip('"'),
                                            source="event_binding")
                elif "value" in effect:
                    self._bus.publish_state(target, effect["value"],
                                            source="event_binding")

        for step in binding.get("sequence", []):
            if "set" in step:
                if binding["output"]:
                    self._bus.publish_state(binding["output"], step["set"],
                                            source="event_binding")


class StateManagerPlugin(ComposePlugin):
    """The bindings engine wearing the plugin contract ([CXC-CS-PLUGIN-CONTRACT]).

    A default-loaded plugin. Its published/subscribed surface is DYNAMIC — it
    grows as bindings are added — so it is the sole authority over it and would
    re-announce on change ([CXC-CS-DYNAMIC-SURFACE]).
    """

    class Config(ComposePlugin.Config):
        pass

    name = "state_manager"
    version = "1.0.0"
    default_loaded = True
    publishes: list[str] = []   # dynamic; see current_publishes
    subscribes: list[str] = []  # dynamic; see current_subscribes

    def __init__(self, config=None):
        super().__init__(config)
        self._engine: "BindingEngine | None" = None

    @property
    def engine(self) -> "BindingEngine | None":
        return self._engine

    def start(self, bus) -> None:
        self._bus = bus
        self._engine = install_default(bus)

    def stop(self) -> None:
        if self._engine is not None:
            self._engine.uninstall()
        self._engine = None
        super().stop()

    def current_publishes(self) -> list[str]:
        if self._engine is None:
            return []
        out = []
        for b in self._engine.bindings:
            t = b.get("target") or b.get("output")
            if t:
                out.append(t)
        return out

    def current_subscribes(self) -> list[str]:
        if self._engine is None:
            return []
        subs: set[str] = set()
        for b in self._engine.bindings:
            if b["type"] == "simple":
                subs.add(b["source"])
            elif b["type"] == "compound":
                subs.update(b["input_namespaces"])
            elif b["type"] == "event_trigger":
                subs.add(b["trigger_namespace"])
        return sorted(subs)


def install_default(bus) -> BindingEngine:
    """Create + wire the default binding engine on a bus, idempotently.

    Called by ``ComposeIOBus.__init__`` (so a bare bus keeps its reactive
    bindings) and by ``StateManagerPlugin.start`` (the standup path). Returns the
    bus's single engine, creating it on first call."""
    engine = getattr(bus, "_binding_engine", None)
    if engine is None:
        engine = BindingEngine(bus)
        bus._binding_engine = engine
        engine.install()
    return engine
