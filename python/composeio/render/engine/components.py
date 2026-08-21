"""
Component expansion — the load-time transform for ComposeUX components
(spec 14-composer-components) plus the `repeat` generator
([SPEC:CXC-PRIM] [CXC-PRIM-REPEAT]).

Engine port of ux_composer/src/dashboard/components.py (the CTk-world
original): a config may declare reusable widget shapes under a top-level
`components:` map and reference them from a module's widget list via
`{use: <name>, <params...>, <layout...>}`.  `expand_components(config)`
rewrites every `use:` entry into a concrete widget by deep-copying the
component's `widget` template, substituting `{{param}}` tokens with
supplied param values (type-preserving), and overlaying layout keys.
Runs ONCE at parse time, so the widget factory never learns what a
component is (COMP-4).

Two-phase resolution (COMP-3):
    {{param}}  -> resolved HERE, at load time, by this expander.
    ${ns}      -> left untouched; resolved at RUNTIME (CommandWidget).

The `repeat` generator ([CXC-PRIM-REPEAT])
-------------------------------------------
A child entry in ANY widget list (`widgets:` / `children:`) of the form::

    - repeat: <count>        # int, numeric string, or a {{param}}/{{i}}
      as: i                  # index token name (optional, default "i")
      child: { <widget template, may reference {{i}} and {{count}}> }

is unrolled AT LOAD into ``<count>`` concrete children, substituting the
INTEGER index token (``{{i}}``) = 0..count-1 and ``{{count}}`` = count as
typed literals.  There is deliberately NO load-time arithmetic engine: the
generator only stamps the integer index; ALL per-index math (e.g. a
segment_bar's lit/dim + colour-zone logic) lives in the runtime DSL
([SPEC:CXC-DSL]) as one bound expression.  This is a direct dividend of
building the DSL before the widgets.

Composition (runs in the SAME load-time pass as `{{param}}`/`use:`):
  * ``repeat`` inside a ``use:`` component's template works — component
    params resolve FIRST (``instantiate`` leaves the un-owned ``{{i}}`` /
    ``{{count}}`` tokens intact), then the repeat pass stamps the index.
  * ``{{param}}`` inside a repeated child resolves (the component pass
    already ran before the child is cloned).
  * Nested ``repeat`` composes with correct lexical scoping: when an outer
    repeat substitutes its template, the reserved ``count`` token and any
    inner repeat's ``as`` index are SHADOWED inside the inner ``child:`` so
    the inner pass owns them.
  * Layout keys ([_LAYOUT_KEYS]) on the repeated child apply per-copy — a
    row/column container lays out the N generated children; a grid child
    can carry ``row: {{i}}`` / ``column: {{i}}`` to place each copy.

A count that resolves to <= 0 produces zero children (a sane empty list).
"""

from __future__ import annotations

import copy
import logging
import re

logger = logging.getLogger("composeio.render.components")

# Keys that describe placement, not component parameters.
_LAYOUT_KEYS = {"row", "column", "colspan", "rowspan", "padding",
                "weight_x", "weight_y", "x", "y", "w", "h"}

_TOKEN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _substitute(node, params, known=None):
    """Substitute ``{{token}}`` occurrences in *node* from *params*.

    Exact-token strings ("{{p}}") are replaced type-preservingly; embedded
    tokens ("temp {{p}}") stringify.

    *known* scopes what this pass OWNS.  A token whose name is in *params*
    resolves.  A token that is owned (in *known*) but unfilled prunes (dict
    key dropped) / blanks (embedded) — the spec-14 "prune unfilled optional
    params" rule.  A token that is NOT owned (name absent from *known*) is
    left INTACT for a later pass — this is what lets ``instantiate`` carry
    a component template's ``{{i}}`` / ``{{count}}`` through untouched so
    the repeat pass can stamp them.  ``known=None`` keeps the legacy
    behaviour (any unfilled token prunes/blanks).
    """
    if isinstance(node, str):
        m = _TOKEN.fullmatch(node.strip())
        if m:  # exact token -> typed passthrough
            name = m.group(1)
            if name in params:
                return params[name]
            if known is not None and name not in known:
                return node                    # un-owned: leave intact
            return None                        # owned but unfilled: prune

        def _embed(mm):
            name = mm.group(1)
            if name in params:
                return str(params[name])
            if known is not None and name not in known:
                return mm.group(0)             # un-owned: leave intact
            return ""                          # owned but unfilled: blank

        return _TOKEN.sub(_embed, node)
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            sv = _substitute(v, params, known)
            if sv is not None:  # prune unfilled optionals
                out[k] = sv
        return out
    if isinstance(node, list):
        return [_substitute(v, params, known) for v in node]
    return node


def instantiate(component: dict, params: dict) -> dict:
    declared = component.get("params", [])
    defaults = component.get("defaults", {})
    missing = [p for p in declared if p not in params and p not in defaults]
    if missing:
        raise ValueError(f"component missing required params: {missing}")
    resolved = {**defaults, **params}
    known = set(declared) | set(defaults) | set(resolved)
    template = component.get("widget", {})
    return _substitute(copy.deepcopy(template), resolved, known)


# ---------------------------------------------------------------------------
# [CXC-PRIM-REPEAT] — the repeat generator
# ---------------------------------------------------------------------------

def _subst_repeat(node, params):
    """Substitute the repeat index tokens (``{{i}}`` / ``{{count}}``) into a
    cloned child template.

    Un-owned tokens are ALWAYS left intact (component params already
    resolved; anything else belongs to a deeper pass).  Nested-repeat
    lexical scoping: descending into an inner ``repeat`` block's ``child:``
    drops the reserved ``count`` token and the inner block's ``as`` index
    from the active params so the INNER pass — not this one — owns them.
    """
    if isinstance(node, str):
        m = _TOKEN.fullmatch(node.strip())
        if m:
            name = m.group(1)
            return params[name] if name in params else node  # else intact
        return _TOKEN.sub(
            lambda mm: str(params[mm.group(1)]) if mm.group(1) in params
            else mm.group(0), node)
    if isinstance(node, dict):
        if "repeat" in node and "child" in node:
            inner_as = str(node.get("as", "i"))
            shadowed = {"count", inner_as}
            inner_params = {k: v for k, v in params.items()
                            if k not in shadowed}
            out = {}
            for k, v in node.items():
                out[k] = _subst_repeat(v, inner_params if k == "child"
                                       else params)
            return out
        return {k: _subst_repeat(v, params) for k, v in node.items()}
    if isinstance(node, list):
        return [_subst_repeat(v, params) for v in node]
    return node


def _coerce_count(raw) -> int:
    """Resolve a repeat count to a non-negative int (guarding bool, which
    is an int subclass)."""
    if isinstance(raw, bool):
        raise ValueError(f"repeat count must be an int, got bool {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        if raw.is_integer():
            return int(raw)
        raise ValueError(f"repeat count {raw!r} is not integral")
    if isinstance(raw, str):
        s = raw.strip()
        if re.fullmatch(r"-?\d+", s):
            return int(s)
    raise ValueError(
        f"repeat count {raw!r} did not resolve to an int (a {{{{param}}}} "
        "count must be filled by a component param or an outer index)")


def _expand_repeat(entry: dict, components: dict) -> list:
    """Unroll one ``{repeat, as, child}`` entry into concrete widgets."""
    if "child" not in entry:
        raise ValueError("repeat: entry requires a 'child:' template")
    count = _coerce_count(entry.get("repeat"))
    index_name = str(entry.get("as", "i"))
    template = entry["child"]
    out = []
    for i in range(max(0, count)):
        params = {index_name: i, "count": count}
        child = _subst_repeat(copy.deepcopy(template), params)
        # A stamped child may itself be a use:/repeat, or carry children
        # with a nested repeat -> feed it back through the pass.
        out.extend(_expand_list([child], components))
    return out


def _expand_use(usage: dict, components: dict) -> dict:
    name = usage["use"]
    component = components.get(name)
    if component is None:
        raise ValueError(f"unknown component '{name}'; known: {sorted(components)}")
    params = {k: v for k, v in usage.items()
              if k != "use" and k not in _LAYOUT_KEYS}
    declared = set(component.get("params", [])) | set(component.get("defaults", {}))
    for k in params:
        if k not in declared:
            logger.warning("component '%s': call-site key '%s' is not a "
                           "declared param (typo?)", name, k)
    widget = instantiate(component, params)
    for k in _LAYOUT_KEYS:
        if k in usage:
            widget[k] = usage[k]
    return widget


# Keys whose value is a NESTED WIDGET LIST, and which the stamping pass must therefore
# descend into.  `children:` was the only one until 2026-08-12, and the omission was
# invisible until a composite had to be used inside a TAB: the console's whole widget
# tree hangs off `tabs:`, so `{use: intelligence_pane}` sat there unexpanded and the
# scene rendered a PlaceholderWidget — with no error, because an unexpanded `use:` is
# only diagnosed at `build_leaf`, three layers away ([CXC-FC-COMPOSITE]).
#
# `tabs:` is the container dialect `parser.py:1252` already reads
# (`spec.get("tabs") or spec.get("children")`), so descending into it here makes the
# expander agree with the builder rather than teaching it something new.
_CHILD_LIST_KEYS = ("children", "tabs")


def _expand_list(widgets: list, components: dict) -> list:
    out = []
    for w in widgets or []:
        if isinstance(w, dict) and "repeat" in w:
            out.extend(_expand_repeat(w, components))
            continue
        if isinstance(w, dict) and "use" in w:
            w = _expand_use(w, components)
        if isinstance(w, dict):
            for key in _CHILD_LIST_KEYS:
                if isinstance(w.get(key), list):
                    w = dict(w)
                    w[key] = _expand_list(w[key], components)
        out.append(w)
    return out


def _has_repeat(node) -> bool:
    """Cheap recursive probe: does the config contain any repeat entry?
    Keeps the legacy fast path (no components, no repeats -> no-op)."""
    if isinstance(node, dict):
        if "repeat" in node and "child" in node:
            return True
        return any(_has_repeat(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_repeat(v) for v in node)
    return False


def expand_components(config: dict) -> dict:
    """Rewrite every `{use: ...}` widget into a concrete widget (COMP-4) and
    unroll every `repeat` child ([CXC-PRIM-REPEAT]) — one load-time pass.

    Handles BOTH config dialects: the production `modules:` lists and the
    nested `widgets:`/`children:` tree.  Mutates and returns *config*.
    """
    components = config.get("components") or {}
    # Legacy fast path: nothing to stamp and no repeat to unroll.
    if not components and not _has_repeat(config):
        return config
    for module in config.get("modules", []) or []:
        if isinstance(module, dict):
            module["widgets"] = _expand_list(module.get("widgets", []), components)
    if isinstance(config.get("widgets"), list):
        config["widgets"] = _expand_list(config["widgets"], components)
    return config
