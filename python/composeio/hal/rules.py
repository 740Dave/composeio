"""Leaf reaction rules ([CXC-HALD-RULES]) — the EXISTING DSL, scoped to the
internal bus.

The spec is emphatic: reuse the existing headless-logic/expression layer, do
NOT write a HAL rule engine ([CXC-HALD-RULES] change record).  So each rule's
``when:`` is compiled with ``composeio.render.engine.dsl`` (the same sandboxed
interpreter the render tier uses), and the reads are subscribed on the MCU's
INTERNAL bus.  When any read-namespace changes, the condition is re-evaluated
against live bus state; on a truthy result, each ``set:`` target is published.

``set:`` values:
  * a plain scalar (``relay/state: on``) is published as-is, with YAML's
    ``on``/``off`` booleans canonicalized to the ``"on"``/``"off"`` tokens the
    bus vocabulary expects.
  * a string may embed ``{namespace|prec}`` fields (``"HOT {env/temperature|0}C"``)
    — a thin interpolation over bus state, NOT a second expression language.

This runs with the external bus ABSENT (autonomy at the leaf, verification
C8): the rule engine only ever touches the internal bus.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from composeio.render.engine import dsl

from .model import Rule

logger = logging.getLogger("composeio.hal.rules")

# {namespace|precision} — precision optional.  Namespace may contain '/'.
_FIELD_RE = re.compile(r"\{([^}|]+)(?:\|(\d+))?\}")


def _canon_scalar(value: Any) -> Any:
    """YAML parses ``on``/``off`` as booleans; the bus vocabulary wants the
    canonical tokens.  Leave everything else untouched."""
    if isinstance(value, bool):
        return "on" if value else "off"
    return value


def _interpolate(template: str, bus: Any) -> str:
    """Substitute ``{namespace|prec}`` fields from live bus state."""
    def _sub(m: re.Match) -> str:
        ns, prec = m.group(1), m.group(2)
        val = bus.get_state(ns)
        if prec is not None:
            try:
                return f"{float(val):.{int(prec)}f}"
            except (TypeError, ValueError):
                return str(val)
        return str(val)
    return _FIELD_RE.sub(_sub, template)


class LeafRuleEngine:
    """Runs declarative ``rules:`` on the internal bus ([CXC-HALD-RULES])."""

    def __init__(self, bus: Any):
        self.bus = bus
        self._compiled: list[tuple[dsl.Expression, dict[str, Any]]] = []

    def add(self, rule: Rule) -> None:
        """Compile a rule's condition with the existing DSL and subscribe its
        read-namespaces on the internal bus."""
        expr = dsl.parse(rule.when)                     # existing interpreter
        self._compiled.append((expr, dict(rule.sets)))
        # Ensure targets exist (permissive engine) so a set never drops.
        for target in rule.sets:
            self.bus.ensure_namespace(target, "json")
        # Subscribe every namespace the condition reads; a change re-fires it.
        for ns in expr.namespaces:
            self.bus.ensure_namespace(ns, "json")
            self.bus.subscribe(ns, self._make_cb(expr, rule.sets))

    def _make_cb(self, expr: dsl.Expression, sets: dict[str, Any]):
        def _cb(namespace: str, new_value: Any, old_value: Any = None) -> None:
            self._evaluate(expr, sets)
        return _cb

    def _read_ns(self, name: str) -> Any:
        return self.bus.get_state(name)

    def _evaluate(self, expr: dsl.Expression, sets: dict[str, Any]) -> None:
        result = expr.evaluate(dsl.EvalContext(ns=self._read_ns))
        if not dsl.dsl_truthy(result):
            return
        for target, value in sets.items():
            out = value
            if isinstance(value, str) and "{" in value:
                out = _interpolate(value, self.bus)
            else:
                out = _canon_scalar(value)
            self.bus.publish_state(target, out, source="hal-rule")
