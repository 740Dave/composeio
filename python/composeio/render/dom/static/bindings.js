// ComposeUX declarative bindings — JavaScript tier ([SPEC:CXC-BIND],
// [CXC-DOM-TWO-WAY-BINDING]).
//
// Re-realizes the READ half of the reactive-widget layer in the browser: the
// binding-DSL grammar ({namespace, when, invert, map, default, expr}) and the
// coercion contract (coerce_bound_value) that render/engine/bindings.py defines
// for every other backend.  Per [CXC-DOM-REUSE-REIMPLEMENT-RUNTIME] the GRAMMAR
// is reused by reference (this file parses the same shape) and the runtime is
// reimplemented; the expression form routes through the conformance-matched
// ComposeDSL evaluator (dsl.js), so a bound expression evaluates bit-identically
// to Python.
//
// This is deliberately the DOM-relevant subset: a control binds one displayed
// attribute (value/label/text/visible/color) to a namespace or expression.  It
// does NOT re-derive the full widget-spec extraction (geometry attrs, per-widget
// tables) — the DOM v1 scope is form/control/layout, not the whole widget zoo.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./dsl.js"));
  else root.ComposeBindings = factory(root.ComposeDSL);
})(typeof self !== "undefined" ? self : this, function (DSL) {
  "use strict";

  const UNCHANGED = Symbol("unchanged");

  // canon() and truthy() — shared [CXC-BIND] vocabulary (same rule as dsl.js's
  // canon; re-exported here so a consumer that only loads bindings has it).
  const canon = DSL.canon;

  function truthy(value) {
    // bindings.truthy (the visible/when rule): str(value).lower() not in the
    // falsy set.  NOTE this differs from DSL truthiness on numbers (here the
    // string "0" is falsy but the float 0.0 stringifies to "0.0" -> truthy),
    // matching render/engine/bindings.py:truthy exactly.
    const s = String(value).toLowerCase();
    return !(s === "" || s === "0" || s === "off" || s === "false" || s === "none");
  }

  function matchWhen(value, when, invert) {
    let shown;
    if (when !== null && when !== undefined) shown = canon(value) === canon(when);
    else shown = truthy(value);
    return invert ? !shown : shown;
  }

  // -- color helpers (mirror bindings.color_tuple/rgba) ---------------------
  function colorTuple(v) {
    if (v === null || v === undefined) return null;
    if (typeof v === "string" && v[0] === "#") {
      const h = v.replace(/^#+/, "");
      const out = [];
      for (let i = 0; i + 1 < h.length; i += 2) out.push(parseInt(h.slice(i, i + 2), 16));
      return out.length >= 3 ? out : null;
    }
    if (Array.isArray(v)) return v.slice();
    return null;
  }
  function rgba(v) {
    const c = colorTuple(v);
    if (c === null) return null;
    return c.length === 3 ? [c[0], c[1], c[2], 255] : c.slice(0, 4);
  }
  // A CSS color string for the DOM, from any accepted color spec.
  function cssColor(v) {
    const c = rgba(v);
    if (c === null) return null;
    return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + (c[3] / 255) + ")";
  }

  // Bare-string-is-a-namespace attrs (mirrors NAMESPACE_STRING_ATTRS, DOM
  // subset).  For the rest a bare string is a literal and binding needs the
  // object/expr form.
  const NAMESPACE_STRING_ATTRS = new Set(["visible", "opacity"]);

  // AttrBinding — the namespace|expr form ([CXC-BIND-ATTR]).
  class AttrBinding {
    constructor(o) {
      this.namespace = o.namespace || "";
      this.attr = o.attr;
      this.when = ("when" in o) ? o.when : null;
      this.invert = !!o.invert;
      this.map = o.map || null;               // literal lookup ([CXC-BIND-C8])
      this.default = ("default" in o) ? o.default : null;
      this.expr = o.expr || null;             // compiled ComposeDSL.Expression
    }
    // Every namespace this binding must subscribe.
    namespaces() {
      if (this.expr) return this.expr.namespaces.slice();
      return this.namespace ? [this.namespace] : [];
    }
  }

  // Parse a raw attribute value into an AttrBinding, or null when it is a
  // literal ([CXC-BIND-ATTR] object/expr/bare-namespace forms).  `where` is a
  // diagnostic label.
  function parseBinding(attr, raw, where) {
    if (typeof raw === "string" && NAMESPACE_STRING_ATTRS.has(attr) && raw) {
      return new AttrBinding({ namespace: raw, attr: attr });
    }
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      // expression site
      const exprSrc = (typeof raw.expr === "string") ? raw.expr
        : (typeof raw.expression === "string") ? raw.expression : null;
      if (exprSrc !== null) {
        const compiled = DSL.tryParse(exprSrc, (where || "") + "." + attr);
        if (compiled === null || raw.namespace || raw.map) return null; // refused
        return new AttrBinding({
          namespace: "", attr: attr, expr: compiled,
          invert: !!raw.invert,
          default: ("default" in raw) ? raw.default : raw.initial,
        });
      }
      if (!raw.namespace) return null;        // malformed binding object
      return new AttrBinding({
        namespace: String(raw.namespace), attr: attr,
        when: ("when" in raw) ? raw.when : null,
        invert: !!raw.invert,
        map: (raw.map && typeof raw.map === "object") ? raw.map : null,
        default: ("default" in raw) ? raw.default : raw.initial,
      });
    }
    return null;
  }

  // Coerce a raw bus value for a control attribute of a known kind, mirroring
  // coerce_bound_value's order: map lookup first, then type-directed coercion.
  // `kind`: 'bool' | 'number' | 'color' | 'string'.  Returns UNCHANGED when the
  // value cannot be applied.
  function coerceBoundValue(binding, raw, kind) {
    if (binding.map) {
      const key = String(raw);
      if (Object.prototype.hasOwnProperty.call(binding.map, key)) raw = binding.map[key];
      else if (binding.default !== null && binding.default !== undefined) raw = binding.default;
      else return UNCHANGED;
    }
    if (kind === "bool") return matchWhen(raw, binding.when, binding.invert);
    if (kind === "color") { const c = cssColor(raw); return c === null ? UNCHANGED : c; }
    if (kind === "number") { const n = DSL.tryNumber(raw); return n === null ? UNCHANGED : n; }
    return raw === null || raw === undefined ? UNCHANGED : String(raw);
  }

  // Coerce a DSL expression RESULT ([CXC-DSL-SEMANTICS] result coercion at the
  // site) — differs from coerceBoundValue only in the bool path (DSL truthiness:
  // 0.0 is false).
  function coerceExprValue(binding, value, kind) {
    if (kind === "bool") {
      const shown = DSL.dslTruthy(value);
      return binding.invert ? !shown : shown;
    }
    if (kind === "color") { const c = cssColor(value); return c === null ? UNCHANGED : c; }
    if (kind === "number") { const n = DSL.tryNumber(value); return n === null ? UNCHANGED : n; }
    return value === null || value === undefined ? UNCHANGED : String(value);
  }

  // Resolve a binding to a display value given a namespace-value store
  // (name -> value) and the tick context.  `kind` is the attribute type hint.
  // Returns UNCHANGED when nothing applicable is available.
  function resolveBinding(binding, store, kind, tick) {
    tick = tick || {};
    if (binding.expr) {
      let value;
      try {
        value = binding.expr.evaluate({
          ns: (name) => (Object.prototype.hasOwnProperty.call(store, name) ? store[name] : null),
          vars: tick.vars || {}, dt: tick.dt || 0.0, t: tick.t || 0.0,
        });
      } catch (e) {
        return UNCHANGED;                     // eval error withholds ([CXC-DSL])
      }
      return coerceExprValue(binding, value, kind);
    }
    let raw = Object.prototype.hasOwnProperty.call(store, binding.namespace)
      ? store[binding.namespace] : undefined;
    if (raw === undefined || raw === null) {
      if (binding.default !== null && binding.default !== undefined) raw = binding.default;
      else return UNCHANGED;
    }
    return coerceBoundValue(binding, raw, kind);
  }

  return {
    UNCHANGED, AttrBinding, parseBinding,
    resolveBinding, coerceBoundValue, coerceExprValue,
    canon, truthy, matchWhen, colorTuple, rgba, cssColor,
    NAMESPACE_STRING_ATTRS,
  };
});
