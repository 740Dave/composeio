"""
ComposeUX Declarative Bindings & Variables ([SPEC:CXC-BIND])

Implements the config-side half of the reactive-widget layer:

  [CXC-BIND-ATTR]     bindable attributes — a widget attribute takes a
                      literal, a bare namespace reference, or a binding
                      object ({namespace, when, invert, map, default}).
                      Generalizes the M4 ``visible:`` binding to opacity,
                      rotation (deg), scale, color, and value attributes.
  [CXC-BIND-VARS]     inline ``variables:`` blocks — ``$name`` parse-time
                      substitution; a namespace-bound variable propagates
                      the binding (stays reactive).
  [CXC-BIND-BOUNDARY] NO arbitrary inline code — code-shaped fields are
                      stripped at parse time and never executed; value
                      maps are literal lookups only (arithmetic/templated
                      maps are refused — that is DSL territory, deferred).

The runtime side (subscription wiring + coercion on bus change) lives in
engine/runtime.py; temporal behaviors live in engine/behaviors.py.
"""

from __future__ import annotations

import copy
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("composeio.render.bindings")


# ---------------------------------------------------------------------------
# Color helpers (shared with the parser)
# ---------------------------------------------------------------------------

def color_tuple(v: Any, default: tuple | None = None) -> tuple | None:
    """Accept [r,g,b], [r,g,b,a], or '#RRGGBB'/'#RRGGBBAA' color specs."""
    if v is None:
        return default
    if isinstance(v, str) and v.startswith("#"):
        h = v.lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in range(0, len(h), 2))
    if isinstance(v, (list, tuple)):
        return tuple(v)
    return default


def rgba(v: Any, default: tuple | None = None) -> tuple | None:
    """color_tuple normalized to 4 components (alpha padded to 255)."""
    c = color_tuple(v, default)
    if c is None:
        return None
    return (*c, 255) if len(c) == 3 else tuple(c)


# ---------------------------------------------------------------------------
# [CXC-BIND-BOUNDARY] — the no-code gate
# ---------------------------------------------------------------------------

# Keys whose presence means "inline code": stripped, warned, never executed.
#
# [SPEC:CXC-DSL] Pass-2 note: `expr`/`expression` REMAIN in this refusal
# set.  The three sanctioned sites ([CXC-DSL-SITES]: output expr, binding
# value expr, trigger condition expr) each POP the field and route it
# through the sandboxed DSL parser (engine/dsl.py) BEFORE this gate runs;
# an `expr:` anywhere else is still stripped and never executed.
FORBIDDEN_CODE_KEYS = frozenset({
    "code", "script", "python", "eval", "exec", "expr", "expression",
    "template", "lambda", "js", "javascript", "shell", "command_line",
})


def pop_expr_field(spec: Any, where: str) -> Optional[str]:
    """Lift a sanctioned `expr:`/`expression:` source string out of a
    site dict ([CXC-DSL-SITES]) so the [CXC-BIND-BOUNDARY] gate never
    sees it.  Only the three sanctioned call sites use this."""
    if not isinstance(spec, dict):
        return None
    src = spec.pop("expr", None)
    alt = spec.pop("expression", None)
    if src is None:
        src = alt
    if src is None:
        return None
    if not isinstance(src, str):
        logger.warning("[CXC-DSL] expr: on %s must be a string — refused",
                       where)
        return None
    return src

# String content that smells like an expression/template rather than a
# literal value (HA-Jinja braces, ${...} shell/JS interpolation, leading =).
_EXPRESSION_RE = re.compile(r"\{\{|\{%|\$\{|^\s*=")


def strip_code_fields(spec: dict, where: str) -> None:
    """Remove any inline-code field from *spec* in place ([CXC-BIND-BOUNDARY]
    negative test: rejected/ignored, never executed)."""
    for key in list(spec.keys()):
        if not isinstance(key, str):
            # YAML 1.1 can yield non-string keys (a bare `on:` field name
            # loads as bool True); those are never code-field names.
            continue
        if key.lower() in FORBIDDEN_CODE_KEYS:
            spec.pop(key)
            logger.warning(
                "[CXC-BIND-BOUNDARY] inline code is not supported in "
                ".compose.yaml — field %r on %s ignored (never executed); "
                "compute in a bus workload instead", key, where)


def _looks_like_expression(v: Any) -> bool:
    return isinstance(v, str) and bool(_EXPRESSION_RE.search(v))


def looks_like_expression(v: Any) -> bool:
    """Public check: does *v* smell like an expression/template rather than
    a literal?  Editors use this to refuse arithmetic and capture a DSL
    use-case candidate instead ([CXC-BE-C8])."""
    # spaced infix operators ("value * 2") read as arithmetic; URI slashes
    # and hex colors do not.
    return _looks_like_expression(v) or (
        isinstance(v, str) and bool(re.search(r"\s[-+*/%^]\s", v)))


def validate_map(m: Any, where: str) -> Optional[dict]:
    """Validate a binding value map: literal lookups ONLY ([CXC-BIND-C8]).

    Returns a str-keyed dict, or None when the map is refused (non-dict,
    expression-valued, or nested-structure values other than color lists).
    """
    if not isinstance(m, dict):
        logger.warning("[CXC-BIND-BOUNDARY] map: must be a literal lookup "
                       "dict on %s — refused", where)
        return None
    out: Dict[str, Any] = {}
    for k, v in m.items():
        if _looks_like_expression(k) or _looks_like_expression(v):
            logger.warning(
                "[CXC-BIND-BOUNDARY] map: entry %r on %s looks like an "
                "expression/template — literal lookups only; the whole map "
                "is refused (expression DSL is deferred, use-case-gated)",
                k, where)
            return None
        if isinstance(v, dict):
            logger.warning("[CXC-BIND-BOUNDARY] map: value for %r on %s is "
                           "a mapping — literal lookups only; refused",
                           k, where)
            return None
        out[str(k)] = v
    return out


# ---------------------------------------------------------------------------
# [CXC-BIND-VARS] — inline variables, $name parse-time substitution
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _subst_value(v: Any, scope: Dict[str, Any]) -> Any:
    """Substitute $name in a single value.

    Exact-token form ("$name") replaces with the variable's value of ANY
    type — including a binding object, which keeps it reactive.  Embedded
    form ("every $n ms") stringifies scalar variables in place.  Unknown
    names are left untouched (a literal dollar string stays a literal).
    """
    if isinstance(v, str):
        m = _VAR_RE.fullmatch(v.strip())
        if m and m.group(1) in scope:
            return copy.deepcopy(scope[m.group(1)])

        def _embed(match: "re.Match[str]") -> str:
            name = match.group(1)
            val = scope.get(name)
            if val is None or isinstance(val, (dict, list)):
                return match.group(0)          # not substitutable inline
            return str(val)

        return _VAR_RE.sub(_embed, v)
    if isinstance(v, dict):
        return _subst_dict(v, scope)
    if isinstance(v, list):
        return [_subst_value(item, scope) for item in v]
    return v


def _subst_dict(d: dict, scope: Dict[str, Any]) -> dict:
    """Recurse into a mapping, honoring widget-scoped variables: blocks."""
    local = d.get("variables")
    if isinstance(local, dict):
        merged = dict(scope)
        for name, val in local.items():
            merged[name] = _subst_value(val, scope)   # vars may use outer vars
        scope = merged
    return {k: _subst_value(v, scope)
            for k, v in d.items() if k != "variables"}


def apply_variable_launch_overrides(
        doc_vars_raw: Dict[str, Any],
        overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """[DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE] Merge LAUNCH-source
    overrides on top of a scene's declared document ``variables:`` block,
    BEFORE $name chain resolution — so an override participates in chaining
    exactly like the declared default it replaces.

    Only a NAME THE SCENE ALREADY DECLARES is overridable: an override for
    an undeclared name is a no-op (never silently promoted to an ad hoc new
    global) but is logged, so a typo doesn't just vanish.  This keeps the
    scene's own ``variables:`` block the single source of what is
    overridable, and keeps every scene backward-compatible — no override
    given, and resolution is byte-identical to before this existed.
    """
    if not overrides:
        return doc_vars_raw
    merged = dict(doc_vars_raw)
    for name, value in overrides.items():
        if name not in merged:
            logger.warning(
                "[CXC-BIND-VARS] launch override for undeclared variable "
                "%r ignored — the scene must declare `variables: {%s: "
                "<default>}` before it can be overridden at launch",
                name, name)
            continue
        merged[name] = value
    return merged


def resolve_doc_variables(config: dict,
                           overrides: Optional[Dict[str, Any]] = None
                           ) -> Dict[str, Any]:
    """The document-level ``variables:`` block with $name chaining
    resolved — the same scope substitute_variables applies.  Exposed so
    the parser can RETAIN the resolved vars on the Composition for the
    [SPEC:CXC-HMON] internal-state publish ([CXC-HMON-STATE]): the
    substitution itself stays parse-time, this is observability only.

    `overrides` is the (optional) LAUNCH-source override map
    ([DEC-STATION-ID-IS-A-RUNTIME-BINDING-ATTRIBUTE]) applied on top of the
    scene's declared defaults before chaining — omit it (the default) and
    resolution is exactly the pre-existing behavior."""
    doc_vars_raw = config.get("variables")
    if not isinstance(doc_vars_raw, dict):
        doc_vars_raw = {}
    doc_vars_raw = apply_variable_launch_overrides(doc_vars_raw, overrides)
    doc_vars: Dict[str, Any] = {}
    for name, val in doc_vars_raw.items():
        doc_vars[name] = _subst_value(val, doc_vars)   # allow chaining
    return doc_vars


def substitute_variables(config: dict,
                          overrides: Optional[Dict[str, Any]] = None) -> dict:
    """Apply [CXC-BIND-VARS]: resolve every $name against the document-level
    ``variables:`` block plus any widget-scoped blocks, at parse time.
    Returns a new config dict; ``variables:`` blocks are consumed.

    `overrides`: see `resolve_doc_variables`.

    NOTE: `_subst_dict` re-derives the document scope from `d["variables"]`
    itself (that's how a WIDGET-scoped ``variables:`` block gets the same
    treatment as the document-level one — same function, same code path).
    At the top level that means the config's own raw `variables:` block gets
    read a second time here, downstream of `resolve_doc_variables` above —
    so an override must be folded into the RAW block *before* this call, or
    that second pass re-derives the un-overridden literal default and
    silently clobbers it (measured: a literal default has no `$` to
    re-substitute, so the override vanished with zero error).  Overriding
    the raw block leaves `_subst_dict`'s chaining behavior completely
    untouched — a launch override is indistinguishable, at this point, from
    the scene having declared a different default."""
    if overrides:
        raw = config.get("variables")
        if isinstance(raw, dict):
            config = {**config,
                     "variables": apply_variable_launch_overrides(raw, overrides)}
    return _subst_dict(config, resolve_doc_variables(config, overrides))


# ---------------------------------------------------------------------------
# [CXC-BIND-ATTR] — binding extraction from widget specs
# ---------------------------------------------------------------------------

# Attributes bindable via the object form {namespace: ...}.  Keys are the
# .compose.yaml keys; they match the widget attribute names.
# [SPEC:CXC-PRIM] [CXC-PRIM-DRAGVAL]/[CXC-PRIM-POPUP] — pixel-stack child
# geometry is bindable, so a composite's fill.width / thumb.x is an ordinary
# bound expression on the published value (the slider's visual is pure
# composite).  The .compose.yaml keys x/y/w/h map to the widget attributes
# the PixelStackContainer reads (stack_x/stack_y/stack_w/stack_h); a bound
# geometry attr is always resolved as ABSOLUTE PIXELS (never the 0..1
# fraction shorthand — see layout._resolve_coord_px), so a computed value
# under 1px is not silently reinterpreted as a fraction.
GEOMETRY_ATTR_MAP = {"x": "stack_x", "y": "stack_y",
                     "w": "stack_w", "h": "stack_h"}
STACK_GEOMETRY_ATTRS = frozenset(GEOMETRY_ATTR_MAP.values())

BINDABLE_ATTRS = frozenset({
    "visible", "opacity", "rotation", "scale",
    "color", "fill_color", "stroke_color",
    # [CXC-PRIM-SELECTED-STATE] a CONTAINER's fill.  Added 2026-08-12 with the
    # non-bindable-attr refusal below, and the two belong together: `background` was
    # absent, so `background: {expr: ...}` on a mode chip was DISCARDED IN SILENCE and
    # the chips rendered as identical bare text.  A selected-state affordance that
    # cannot be bound is a selected-state affordance that does not exist.
    "background",
    "value", "text", "label", "state", "src", "unit",
    # [SPEC:CXC-LAUNCHBAR] [DX-NARROW-WAIST] launch_tile bound status affordance:
    # a bound COLOUR that recolours the monochrome icon, and a bound BOOL that
    # blinks it.  General/optional — the thresholds live in the scene expr.
    "icon_tint", "blink",
    # [SPEC:CXC-LOTTIE] playback transport ([CXC-LOT-PLAYBACK])
    "speed", "autoplay", "loop", "go_to_and_stop", "play_segments",
    # [SPEC:CXC-PRIM] pixel-stack geometry (object/expr form only)
    "x", "y", "w", "h",
})

# The keys that MAKE a dict a binding site.  A dict carrying one of these on an attribute
# outside BINDABLE_ATTRS is an authoring error we must not swallow ([CXC-BIND-NO-SILENT-DROP]).
BINDING_SITE_KEYS = frozenset({"expr", "namespace"})

# ATTRIBUTES THAT CARRY A BINDING-SHAPED DICT AND ARE BOUND BY SOMEBODY ELSE.
#
# `extract_bindings` is not the only consumer of `{namespace: ...}` / `{expr: ...}`: several
# widgets and engine seams read the dict themselves, at their own site, for their own
# runtime.  Those are NOT silent drops — they are handled — so the refusal below must skip
# them.  Enumerated rather than pattern-matched, and each names its consumer, because the
# whole value of the refusal is that an attribute NOT on either list is loudly wrong.  A
# regex over "looks handled" would re-open the silence this closes.
#
# Derived by scanning every `.compose.yaml` in the tree, 2026-08-12 — not by guessing.
BOUND_ELSEWHERE_ATTRS: Dict[str, str] = {
    "drag_value":   "[CXC-PRIM-DRAGVAL] pixel-stack drag seam (parser: _pop_drag_value)",
    "when":         "a binding OBJECT's own condition key, and the trigger-condition site",
    "widget":       "a `components:` template body, not a widget attribute",
    "publish":      "target_designation's publish spec (widget-owned)",
    "alert":        "dradis alert spec (widget-owned)",
    "bodies":       "dradis contact list (widget-owned)",
    "own_heading":  "dradis own-ship heading (widget-owned)",
    "source":       "dradis source spec (widget-owned)",
    "scope_label":  "away-watch scope label (widget-owned)",
    "sessions":     "away-watch session list (widget-owned)",
}

# ---------------------------------------------------------------------------
# [CXC-BE-INTROSPECT] — declarative model-introspection tables.
# The Composer binding editor enumerates what is bindable by READING these;
# it hardcodes nothing.  Adding a widget type (or attr) here makes it appear
# in the editor with no editor code change.
# ---------------------------------------------------------------------------

# Base attributes every widget carries (Widget base class transforms).
UNIVERSAL_BINDABLE_ATTRS: tuple = ("visible", "opacity", "rotation", "scale")

# Per-widget-type VALUE attributes (beyond the universal transform set).
# Keys are parser type keys (build_leaf); values are widget attribute names.
BINDABLE_ATTRS_BY_TYPE: Dict[str, tuple] = {
    "gauge": ("value", "label", "unit"),
    "led": ("state", "label"),
    "label": ("text", "color"),
    "rect": ("fill_color", "stroke_color"),
    "rectangle": ("fill_color", "stroke_color"),
    "circle": ("fill_color", "stroke_color"),
    "image": ("src",),
    "button": ("label",),
    "callout": ("text", "color"),
    "checkbox": ("label",),
    "relay": ("label",),
    "text_input": ("label",),
    "coordinate": ("label",),
    "bus_monitor": ("label",),
    "dialog": (),
    # [SPEC:CXC-LOTTIE] — playback controls are just bindings
    # ([CXC-LOT-PLAYBACK]); this row surfaces them in the Composer
    # Bindings tab ([CXC-BE-INTROSPECT], zero editor hardcoding).
    "lottie": ("speed", "autoplay", "loop", "go_to_and_stop",
               "play_segments", "src"),
    # [SPEC:CXC-SVG] — a bound `src` swaps the vector graphic reactively
    # ([CXC-SVG-C3]); surfaced in the Composer Bindings tab.
    "svg": ("src", "fit"),
    "media": ("src", "speed", "autoplay", "loop", "fit"),
    # [SPEC:CXC-IC] [CXC-IC-CANVAS] — the mermaid SOURCE is the state on the
    # bus; a bound `source` (or the subscribed `thread/diagram` namespace)
    # re-renders the diagram with no model round-trip.
    "mermaid": ("source", "theme"),
    # [SPEC:CXC-FLAUTH] — the headless logic node ([CXC-FLAUTH-CREATE]).
    "logic": ("value", "label"),
    # [SPEC:CXC-LAUNCHBAR] — a launch-bar tile carries a general, optional
    # bound-status affordance ([DX-NARROW-WAIST]): recolour the icon
    # (icon_tint) and blink it (blink); surfaced in the Composer Bindings tab
    # with zero editor hardcoding ([CXC-BE-INTROSPECT]).
    "launch_tile": ("label", "icon_tint", "blink"),
}


def bindable_attrs_for(widget_type: str) -> tuple:
    """The full bindable-attribute list for a widget type: the universal
    transform set plus the type's value attributes ([CXC-BE-INTROSPECT])."""
    key = str(widget_type).lower().replace("widget", "")
    return UNIVERSAL_BINDABLE_ATTRS + BINDABLE_ATTRS_BY_TYPE.get(key, ())

# Attributes where a bare string is a namespace reference (a string literal
# is meaningless for these); for the rest, strings are literals and binding
# requires the object form.  NOTE: play_segments is NOT here — a bare
# string is a Lottie marker-name literal; bind it via the object form.
NAMESPACE_STRING_ATTRS = frozenset({"visible", "opacity", "rotation", "scale",
                                    "speed", "autoplay", "loop",
                                    "go_to_and_stop"})

COLOR_ATTRS = frozenset({"color", "fill_color", "stroke_color", "background",
                         # launch_tile icon recolour — coerces to an RGBA tuple
                         # even from a None default ([SPEC:CXC-LAUNCHBAR]).
                         "icon_tint"})


@dataclass
class AttrBinding:
    """One attribute bound to a namespace ([CXC-BIND-ATTR]) — or, since
    [SPEC:CXC-DSL], to a sandboxed expression (the binding-value site,
    [CXC-DSL-SITES]).

    Namespace form: the runtime subscribes the namespace and, on change,
    coerces the raw bus value to the attribute's type (via
    ``coerce_bound_value``) and sets it on the widget — within one frame
    ([CXC-BIND-C1]).

    Expression form (``expr`` is a compiled dsl.Expression, namespace is
    ""): the runtime subscribes every namespace the expression reads
    (``expr.namespaces``) and re-evaluates on any change, coercing the
    result via ``coerce_expr_value``."""
    namespace: str
    attr: str
    when: Any = None            # exact-match value (visible-style bindings)
    invert: bool = False
    map: Optional[dict] = None  # literal lookup ([CXC-BIND-C8])
    default: Any = None         # value until the namespace speaks
    widget: Any = None          # attached after widget construction
    expr: Any = None            # compiled dsl.Expression (value-expr site)


@dataclass
class OutputDecl:
    """One widget attribute published to a namespace ([SPEC:CXC-HEADLESS]
    [CXC-HW-RUNTIME] "publish changed output namespaces").

    The publish-side mirror of AttrBinding: after each headless tick the
    runtime publishes ``widget.<attr>`` to ``namespace`` when it changed.
    Declared on a widget spec as::

        outputs:
          - namespace: fuel/remaining
            attr: value          # default: value

    [SPEC:CXC-DSL] output-expr site ([CXC-DSL-SITES], [CXC-DSL-STATE]):
    an entry may instead carry a sandboxed expression evaluated each
    tick with the REAL dt/t — read-modify-write integration where the
    bus is the state::

        outputs:
          - namespace: fuel/remaining
            expr: "ns('fuel/remaining') - ns('pump/flow_rate') * dt"
    """
    namespace: str
    attr: str = "value"
    widget: Any = None          # attached after widget construction
    expr: Any = None            # compiled dsl.Expression (output-expr site)


def parse_outputs(spec_list: Any, where: str) -> List[OutputDecl]:
    """Parse a widget's ``outputs:`` list.  Malformed entries are ignored
    with a warning.  Entries are literal {namespace, attr} pairs, or —
    the [CXC-DSL-SITES] output-expr site — {namespace, expr} routed
    through the sandboxed DSL parser.  A refused expr refuses the whole
    entry; nothing else code-shaped survives ([CXC-BIND-BOUNDARY])."""
    from . import dsl              # sanctioned-site routing (lazy: no cycle)
    out: List[OutputDecl] = []
    if not isinstance(spec_list, list):
        logger.warning("outputs: on %s must be a list — ignored", where)
        return out
    for i, entry in enumerate(spec_list):
        if not isinstance(entry, dict) or not entry.get("namespace"):
            logger.warning("outputs %s[%d]: needs namespace: — ignored",
                           where, i)
            continue
        site = f"{where}.outputs[{i}]"
        expr_src = pop_expr_field(entry, site)
        strip_code_fields(entry, site)
        compiled = None
        if expr_src is not None:
            compiled = dsl.try_parse(expr_src, site)
            if compiled is None:
                continue           # refused expr => entry refused
            if "attr" in entry:
                logger.warning("outputs %s: attr: ignored — expr: supplies "
                               "the value", site)
        out.append(OutputDecl(namespace=str(entry["namespace"]),
                              attr=str(entry.get("attr", "value")),
                              expr=compiled))
    return out


def truthy(value: Any) -> bool:
    """The engine's shared truthiness rule (visible/truthy triggers)."""
    return str(value).lower() not in ("", "0", "off", "false", "none")


def canon(value: Any) -> str:
    """Canonical comparison token for declarative state matching.

    Binary-state vocabulary collapses to on/off so that YAML 1.1 booleans
    (`is: on` loads as True), the binary state engine (`1`/`0`), and
    string publishes (`"on"`) all agree — a trigger authored as `is: on`
    matches however the peer published the bit."""
    if isinstance(value, bool):
        return "on" if value else "off"
    s = str(value).strip().lower()
    if s in ("on", "true", "yes", "1"):
        return "on"
    if s in ("off", "false", "no", "0"):
        return "off"
    return s


def match_when(value: Any, when: Any, invert: bool) -> bool:
    """Visible-style condition: canonical match when ``when`` is given,
    binary truthiness otherwise; ``invert`` flips."""
    shown = canon(value) == canon(when) if when is not None \
        else truthy(value)
    return not shown if invert else shown


def extract_bindings(spec: dict, where: str) -> List[AttrBinding]:
    """Pull attribute bindings out of a widget spec IN PLACE.

    For every bindable key whose value is a namespace reference (bare
    string for NAMESPACE_STRING_ATTRS, or the object form), the key is
    replaced by its declared ``default`` (or removed, so the widget's
    constructor default holds) and an AttrBinding is returned.  Literals
    pass through untouched.  ``visible`` keeps its M4 semantics (handled
    by the caller for base-value defaults) but flows through the same
    generalized mechanism.
    """
    from . import dsl              # sanctioned-site routing (lazy: no cycle)
    out: List[AttrBinding] = []
    for key in list(spec.keys()):
        if key not in BINDABLE_ATTRS:
            # ⚠ A BINDING SITE ON A NON-BINDABLE ATTRIBUTE IS A REFUSAL, NOT A SKIP
            # ([CXC-BIND-NO-SILENT-DROP], 2026-08-12).
            #
            # This `continue` used to be unconditional, and that made the worst kind of
            # bug: `background: {expr: "..."}` on a mode chip fell through here, then
            # fell through the colour parse (a dict is not a colour) to `None`, and
            # NOTHING WARNED.  The chips rendered as identical bare text, the scene
            # parsed clean, every test passed, and the only way to find it was to look
            # at the glass and notice an affordance that could not be selected.
            #
            # A silently dropped binding is this platform's recurring failure. It is
            # worse than a crash because the surface still renders — it just renders a
            # state the system is not in. So: an attribute carrying `expr:`/`namespace:`
            # that this engine cannot bind RAISES, at parse time, naming the attribute
            # and what IS bindable.
            v = spec.get(key)
            if (isinstance(v, dict) and (set(v) & BINDING_SITE_KEYS)
                    and key not in BOUND_ELSEWHERE_ATTRS):
                raise ValueError(
                    f"[CXC-BIND-NO-SILENT-DROP] {where}: attribute {key!r} "
                    f"carries a binding ({sorted(set(v) & BINDING_SITE_KEYS)}) but is NOT "
                    f"bindable, so the binding would be DISCARDED IN SILENCE and the "
                    f"attribute would fall back to its literal/constructor default. "
                    f"Bindable attributes are: {sorted(BINDABLE_ATTRS)}. Either bind a "
                    f"bindable attribute, or add {key!r} to BINDABLE_ATTRS (and to "
                    f"COLOR_ATTRS if it is a colour) with a runtime that applies it.")
            continue
        v = spec[key]
        binding: Optional[AttrBinding] = None

        if isinstance(v, str) and key in NAMESPACE_STRING_ATTRS and v:
            binding = AttrBinding(namespace=v, attr=key)
        elif isinstance(v, dict):
            # [CXC-DSL-SITES] binding-value site: {expr: "..."} — an
            # attribute bound to a sandboxed expression instead of a raw
            # namespace.  The condition spelling {when: {expr: "..."}}
            # (visible-style trigger-condition) folds to the same
            # evaluator; the result is coerced at the site.
            expr_src = pop_expr_field(v, f"{where}.{key}")
            if expr_src is None and isinstance(v.get("when"), dict):
                expr_src = pop_expr_field(v["when"], f"{where}.{key}.when")
                if expr_src is not None:
                    v.pop("when")
            if expr_src is not None:
                compiled = dsl.try_parse(expr_src, f"{where}.{key}")
                if compiled is None or v.get("namespace") or \
                        v.get("map") is not None:
                    if compiled is not None:
                        logger.warning(
                            "[CXC-DSL] expr binding on %s.%s cannot combine "
                            "with namespace:/map: — refused", where, key)
                    spec.pop(key)
                    continue       # refused => attr keeps constructor default
                binding = AttrBinding(
                    namespace="", attr=GEOMETRY_ATTR_MAP.get(key, key),
                    expr=compiled,
                    invert=bool(v.get("invert", False)),
                    default=v.get("default", v.get("initial")))
                if binding.default is not None:
                    spec[key] = binding.default
                else:
                    spec.pop(key)
                out.append(binding)
                continue
            ns = v.get("namespace")
            if not ns:
                # a dict without namespace on a bindable attr is malformed
                # config — drop it rather than passing a dict to a widget
                # constructor expecting a scalar.
                logger.warning("binding object on %s.%s has no namespace: "
                               "— ignored", where, key)
                spec.pop(key)
                continue
            vmap = None
            if "map" in v:
                vmap = validate_map(v.get("map"), f"{where}.{key}")
                if vmap is None:
                    spec.pop(key)      # refused map => binding refused (C8)
                    continue
            binding = AttrBinding(
                namespace=str(ns), attr=GEOMETRY_ATTR_MAP.get(key, key),
                when=v.get("when"),
                invert=bool(v.get("invert", False)),
                map=vmap,
                default=v.get("default", v.get("initial")))

        if binding is None:
            continue                    # literal — leave for the constructor
        if binding.default is not None:
            spec[key] = binding.default
        else:
            spec.pop(key)
        out.append(binding)
    return out


# ---------------------------------------------------------------------------
# Runtime coercion — raw bus value -> typed attribute value
# ---------------------------------------------------------------------------

_UNCHANGED = object()


def coerce_bound_value(widget: Any, b: AttrBinding, raw: Any) -> Any:
    """Coerce *raw* (a bus value) for widget.<b.attr>.

    Order: map lookup first (literal, [CXC-BIND-C8]) then type-directed
    coercion keyed on the attribute's CURRENT value type — bool attrs get
    the visible-style match, color attrs parse color specs, numeric attrs
    float(), string attrs str().  Returns _UNCHANGED sentinel when the
    value cannot be applied (missing map key with no default, bad float).
    """
    if b.map is not None:
        if str(raw) in b.map:
            raw = b.map[str(raw)]
        elif b.default is not None:
            raw = b.default
        else:
            return _UNCHANGED

    # Widget-directed coercion: a widget may declare coerce_bound_attr
    # for attrs whose types the generic rules cannot infer (e.g. the
    # LottieWidget's play_segments, which is a list OR a marker-name
    # string).  Returning NotImplemented falls through to the defaults.
    hook = getattr(widget, "coerce_bound_attr", None)
    if hook is not None:
        coerced = hook(b.attr, raw)
        if coerced is not NotImplemented:
            return coerced

    cur = getattr(widget, b.attr, None)

    if isinstance(cur, bool) or b.attr == "visible":
        return match_when(raw, b.when, b.invert)
    if b.attr in COLOR_ATTRS or isinstance(cur, tuple):
        want_alpha = not (isinstance(cur, tuple) and len(cur) == 3)
        c = rgba(raw) if want_alpha else color_tuple(raw)
        return c if c is not None else _UNCHANGED
    if isinstance(cur, (int, float)):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return _UNCHANGED
    if cur is None:
        return raw
    return str(raw)


def apply_binding(widget: Any, b: AttrBinding, raw: Any) -> bool:
    """Set the coerced value on the widget.  Returns True when applied."""
    value = coerce_bound_value(widget, b, raw)
    if value is _UNCHANGED:
        return False
    setattr(widget, b.attr, value)
    return True


def coerce_expr_value(widget: Any, b: AttrBinding, value: Any) -> Any:
    """Coerce a DSL expression RESULT for widget.<b.attr>
    ([CXC-DSL-SEMANTICS] "result coercion at the site").

    Differs from coerce_bound_value in the bool path: an expression
    result uses DSL truthiness (0.0 is false), not the stringly
    visible-match rule (where str(0.0) would read truthy)."""
    from . import dsl

    hook = getattr(widget, "coerce_bound_attr", None)
    if hook is not None:
        coerced = hook(b.attr, value)
        if coerced is not NotImplemented:
            return coerced

    cur = getattr(widget, b.attr, None)

    if isinstance(cur, bool) or b.attr == "visible":
        shown = dsl.dsl_truthy(value)
        return (not shown) if b.invert else shown
    if b.attr in COLOR_ATTRS or isinstance(cur, tuple):
        want_alpha = not (isinstance(cur, tuple) and len(cur) == 3)
        c = rgba(value) if want_alpha else color_tuple(value)
        return c if c is not None else _UNCHANGED
    if isinstance(cur, (int, float)):
        n = dsl.try_number(value)
        return n if n is not None else _UNCHANGED
    if cur is None:
        return value
    return str(value)


def apply_expr_binding(widget: Any, b: AttrBinding, value: Any) -> bool:
    """Set a coerced expression result on the widget ([CXC-DSL-SITES]
    binding-value site).  Returns True when applied."""
    coerced = coerce_expr_value(widget, b, value)
    if coerced is _UNCHANGED:
        return False
    setattr(widget, b.attr, coerced)
    return True
