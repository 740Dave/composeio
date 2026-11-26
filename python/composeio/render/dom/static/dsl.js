// ComposeUX Expression DSL — JavaScript tier ([SPEC:CXC-DSL], [SPEC:CXC-DOM]).
//
// The THIRD reactive tier's expression evaluator, after Python/Skia (the golden
// reference, render/engine/dsl.py) and C++/CYD (cyd/src/dsl.cpp).  Per
// [CXC-DOM-REUSE-REIMPLEMENT-RUNTIME] this is an INDEPENDENT reimplementation
// (not a transpile) gated by the SHARED conformance corpus
// (render/engine/dsl_conformance/dsl_corpus.json) — the [DD-DOM-DSL-EVAL-REUSE]
// fork resolved to candidate (b): the browser runtime stays dependency-free and
// build-step-free (a page loads this .js directly), exactly as the CYD C++ tier
// is an independent interpreter gated by the same corpus.  The corpus is the
// one-measurement-path proof obligation for BOTH non-Python tiers.
//
// Architecture mirrors dsl.py exactly: tokenizer -> recursive-descent
// (spec precedence) parser -> array-shaped AST -> tree-walking evaluator.
// float64 everywhere (JS Number IS IEEE-754 float64, so arithmetic is
// bit-identical to Python by construction); the only hand-matched pieces are
// round-half-to-even, signed-zero preservation, truncated %, guarded /0, the
// operand-returning and/or, and canon() typed equality.
//
// No eval(), no Function(), no dynamic code of any kind ([CXC-DSL-SANDBOX]).

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ComposeDSL = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // -- Errors ---------------------------------------------------------------
  class DslError extends Error {}
  class DslParseError extends DslError {}
  class DslEvalError extends DslError {}

  // The closed function table ([CXC-DSL-C5]) — name -> arity.
  const FUNCTIONS = { min: 2, max: 2, abs: 1, clamp: 3, round: 1, floor: 1, ceil: 1 };

  // -- canon()/truthiness — shared [CXC-BIND] vocabulary --------------------
  function canon(value) {
    if (typeof value === "boolean") return value ? "on" : "off";
    const s = String(value).trim().toLowerCase();
    if (s === "on" || s === "true" || s === "yes" || s === "1") return "on";
    if (s === "off" || s === "false" || s === "no" || s === "0") return "off";
    return s;
  }

  const _NUMERIC_STR_RE = /^\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\s*$/;

  // The ONE numeric-coercion rule: float64 -> itself, bool -> 1/0,
  // numeric-looking string -> parsed.  null on failure.
  function tryNumber(v) {
    if (typeof v === "boolean") return v ? 1.0 : 0.0;
    if (typeof v === "number") return v;
    if (typeof v === "string" && _NUMERIC_STR_RE.test(v)) return Number(v);
    return null;
  }

  function dslTruthy(v) {
    if (typeof v === "boolean") return v;
    if (typeof v === "number") return v !== 0.0;
    const c = canon(v);
    return c !== "off" && c !== "";
  }

  function canonToken(v) {
    if (typeof v === "number") {
      if (isFinite(v) && v === Math.trunc(v) && Math.abs(v) < 1e16) {
        return canon(String(Math.trunc(v)));
      }
      return canon(reprFloat(v));
    }
    return canon(v);
  }

  // Python repr(float) shortest round-trip.  JS String(Number) is also the
  // shortest round-trippable form, so they agree for finite values the corpus
  // uses in equality; inf/nan handled explicitly to match Python spelling.
  function reprFloat(v) {
    if (v === Infinity) return "inf";
    if (v === -Infinity) return "-inf";
    if (Number.isNaN(v)) return "nan";
    return String(v);
  }

  function valuesEqual(a, b) {
    const na = tryNumber(a), nb = tryNumber(b);
    if (na !== null && nb !== null) return na === nb;
    return canonToken(a) === canonToken(b);
  }

  // ns()/$var coercion ([CXC-DSL-GRAMMAR]): numeric -> float64;
  // bool -> 'on'/'off' canon token; else str.
  function coerceInput(raw) {
    if (typeof raw === "boolean") return raw ? "on" : "off";
    const n = tryNumber(raw);
    if (n !== null) return n;
    if (typeof raw === "string") return raw;
    return String(raw);
  }

  function copysign(x, y) {
    return (y < 0 || Object.is(y, -0)) ? -Math.abs(x) : Math.abs(x);
  }
  function signedZero(r, x) {
    return r === 0 ? copysign(0.0, x) : r;
  }
  // round HALF-TO-EVEN (banker's / IEEE roundTiesToEven), matching Python
  // round(float) at ndigits=0.  A double that is exactly k.5 yields
  // x-floor(x) === 0.5 exactly, so the tie test is exact.
  function roundHalfEven(x) {
    if (!isFinite(x)) return x;
    const f = Math.floor(x);
    const diff = x - f;
    let r;
    if (diff < 0.5) r = f;
    else if (diff > 0.5) r = f + 1;
    else r = (f % 2 === 0) ? f : f + 1;
    return r;
  }

  // -- Tokenizer ------------------------------------------------------------
  // Emits {kind, value, pos, end}. kind: num/str/name/var/op.
  const _TOKEN_RE = new RegExp(
    "(\\s+)" +                                        // 1 ws
    "|((?:\\d+\\.?\\d*|\\.\\d+)(?:[eE][+-]?\\d+)?)" +  // 2 number
    "|('[^']*')" +                                    // 3 string
    "|([A-Za-z_][A-Za-z0-9_]*)" +                     // 4 name
    "|(\\$[A-Za-z_][A-Za-z0-9_]*)" +                  // 5 var
    "|(<=|>=|==|!=|[-+*/%<>?:(),])",                  // 6 op
    "g");

  function tokenize(src) {
    const tokens = [];
    let pos = 0;
    while (pos < src.length) {
      _TOKEN_RE.lastIndex = pos;
      const m = _TOKEN_RE.exec(src);
      if (m === null || m.index !== pos) {
        throw new DslParseError(
          "unexpected character " + JSON.stringify(src[pos]) +
          " at position " + pos + " in expression " + JSON.stringify(src));
      }
      const end = _TOKEN_RE.lastIndex;
      if (m[1] !== undefined) { /* whitespace */ }
      else if (m[2] !== undefined) tokens.push({ kind: "num", value: Number(m[2]), pos: pos, end: end });
      else if (m[3] !== undefined) tokens.push({ kind: "str", value: m[3].slice(1, -1), pos: pos, end: end });
      else if (m[4] !== undefined) tokens.push({ kind: "name", value: m[4], pos: pos, end: end });
      else if (m[5] !== undefined) tokens.push({ kind: "var", value: m[5].slice(1), pos: pos, end: end });
      else tokens.push({ kind: "op", value: m[6], pos: pos, end: end });
      pos = end;
    }
    return tokens;
  }

  // -- Parser — AST as arrays mirroring dsl.py's tuples ---------------------
  //   ["num",f] ["str",s] ["bool",b] ["dt"] ["t"] ["var",name] ["ns",name]
  //   ["call",fname,[args]] ["neg",x] ["not",x] ["bin",op,a,b]
  //   ["cmp",op,a,b] ["and",a,b] ["or",a,b] ["ternary",cond,a,b]
  class Parser {
    constructor(src) { this.src = src; this.tokens = tokenize(src); this.i = 0; }
    peek() { return this.i < this.tokens.length ? this.tokens[this.i] : null; }
    next() {
      const tok = this.peek();
      if (tok === null) throw new DslParseError("unexpected end of expression " + JSON.stringify(this.src));
      this.i += 1;
      return tok;
    }
    expectOp(op) {
      const tok = this.next();
      if (tok.kind !== "op" || tok.value !== op) {
        throw new DslParseError("expected " + JSON.stringify(op) + " at position " +
          tok.pos + " in " + JSON.stringify(this.src) + ", got " + JSON.stringify(tok.value));
      }
    }
    atOp() {
      const tok = this.peek();
      if (tok === null || tok.kind !== "op") return false;
      for (let k = 0; k < arguments.length; k++) if (tok.value === arguments[k]) return true;
      return false;
    }
    atName() {
      const tok = this.peek();
      if (tok === null || tok.kind !== "name") return false;
      for (let k = 0; k < arguments.length; k++) if (tok.value === arguments[k]) return true;
      return false;
    }
    parse() {
      const node = this.expression();
      const tok = this.peek();
      if (tok !== null) {
        throw new DslParseError("unexpected " + JSON.stringify(tok.value) + " at position " +
          tok.pos + " in " + JSON.stringify(this.src) +
          " (comparisons are non-associative; parenthesize compound conditions)");
      }
      return node;
    }
    expression() {                              // ternary — lowest
      const cond = this.orExpr();
      if (this.atOp("?")) {
        this.next();
        const a = this.expression();
        this.expectOp(":");
        const b = this.expression();
        return ["ternary", cond, a, b];
      }
      return cond;
    }
    orExpr() {
      let node = this.andExpr();
      while (this.atName("or")) { this.next(); node = ["or", node, this.andExpr()]; }
      return node;
    }
    andExpr() {
      let node = this.cmpExpr();
      while (this.atName("and")) { this.next(); node = ["and", node, this.cmpExpr()]; }
      return node;
    }
    cmpExpr() {                                 // non-assoc: at most ONE
      let node = this.addExpr();
      if (this.atOp("<", "<=", ">", ">=", "==", "!=")) {
        const op = this.next().value;
        node = ["cmp", op, node, this.addExpr()];
      }
      return node;
    }
    addExpr() {
      let node = this.mulExpr();
      while (this.atOp("+", "-")) { const op = this.next().value; node = ["bin", op, node, this.mulExpr()]; }
      return node;
    }
    mulExpr() {
      let node = this.unary();
      while (this.atOp("*", "/", "%")) { const op = this.next().value; node = ["bin", op, node, this.unary()]; }
      return node;
    }
    unary() {
      if (this.atOp("-")) { this.next(); return ["neg", this.unary()]; }
      if (this.atName("not")) { this.next(); return ["not", this.unary()]; }
      return this.primary();
    }
    primary() {
      const tok = this.next();
      const kind = tok.kind, value = tok.value, pos = tok.pos;
      if (kind === "num") return ["num", value];
      if (kind === "str") return ["str", value];
      if (kind === "var") return ["var", value];
      if (kind === "op" && value === "(") {
        const node = this.expression();
        this.expectOp(")");
        return node;
      }
      if (kind === "name") {
        if (value === "true") return ["bool", true];
        if (value === "false") return ["bool", false];
        if (value === "dt") return ["dt"];
        if (value === "t") return ["t"];
        if (value === "ns") return this.nsCall(pos);
        if (Object.prototype.hasOwnProperty.call(FUNCTIONS, value)) return this.fnCall(value, pos);
        throw new DslParseError("unknown name " + JSON.stringify(value) + " at position " +
          pos + " in " + JSON.stringify(this.src) +
          " — the DSL knows only dt, t, $vars, ns('...'), true/false, and the functions " +
          Object.keys(FUNCTIONS).sort().join("/"));
      }
      throw new DslParseError("unexpected " + JSON.stringify(value) + " at position " + pos +
        " in " + JSON.stringify(this.src));
    }
    nsCall(pos) {
      this.expectOp("(");
      const tok = this.next();
      if (tok.kind !== "str") {
        throw new DslParseError("ns(...) takes exactly one string literal at position " +
          pos + " in " + JSON.stringify(this.src) +
          " — namespace names must be static so the runtime can subscribe them");
      }
      this.expectOp(")");
      return ["ns", tok.value];
    }
    fnCall(name, pos) {
      this.expectOp("(");
      const args = [this.expression()];
      while (this.atOp(",")) { this.next(); args.push(this.expression()); }
      this.expectOp(")");
      const arity = FUNCTIONS[name];
      if (args.length !== arity) {
        throw new DslParseError(name + "() takes exactly " + arity + " argument(s), got " +
          args.length + " at position " + pos + " in " + JSON.stringify(this.src));
      }
      return ["call", name, args];
    }
  }

  // -- Evaluation -----------------------------------------------------------
  function readNs(ctx, name) {
    const ns = ctx.ns;
    if (typeof ns === "function") return ns(name);
    if (ns && typeof ns === "object") return Object.prototype.hasOwnProperty.call(ns, name) ? ns[name] : null;
    return null;
  }

  class Expression {
    constructor(source, ast, namespaces) {
      this.source = source;
      this.ast = ast;
      this.namespaces = namespaces;   // static ns() reads, for subscription
      this._warned = new Set();
    }
    _warnOnce(key, msg) {
      if (!this._warned.has(key)) {
        this._warned.add(key);
        if (typeof console !== "undefined" && console.warn) {
          console.warn("[CXC-DSL] " + msg + " in expr " + JSON.stringify(this.source));
        }
      }
    }
    _num(nodeVal, what) {
      const n = tryNumber(nodeVal);
      if (n === null) {
        throw new DslEvalError(what + " needs a numeric value, got " + JSON.stringify(nodeVal) +
          " (expr " + JSON.stringify(this.source) + ")");
      }
      return n;
    }
    evaluate(ctx) { return this._eval(this.ast, ctx); }
    _eval(node, ctx) {
      const tag = node[0];
      switch (tag) {
        case "num": return node[1];
        case "str": return node[1];
        case "bool": return node[1];
        case "dt": return Number(ctx.dt) || 0.0;
        case "t": return Number(ctx.t) || 0.0;
        case "var": {
          const name = node[1];
          if (!ctx.vars || !Object.prototype.hasOwnProperty.call(ctx.vars, name)) {
            throw new DslEvalError("unknown variable $" + name + " (expr " +
              JSON.stringify(this.source) + ") — declare it in a variables: block");
          }
          return coerceInput(ctx.vars[name]);
        }
        case "ns": {
          const raw = readNs(ctx, node[1]);
          if (raw === null || raw === undefined) {
            this._warnOnce("ns:" + node[1],
              "namespace " + JSON.stringify(node[1]) + " has no value yet — reading 0.0");
            return 0.0;
          }
          return coerceInput(raw);
        }
        case "neg": return -this._num(this._eval(node[1], ctx), "unary -");
        case "not": return !dslTruthy(this._eval(node[1], ctx));
        case "bin": {
          const op = node[1];
          const a = this._num(this._eval(node[2], ctx), "operator " + JSON.stringify(op));
          const b = this._num(this._eval(node[3], ctx), "operator " + JSON.stringify(op));
          return this._binCore(op, a, b);
        }
        case "cmp": {
          const op = node[1];
          const a = this._eval(node[2], ctx);
          const b = this._eval(node[3], ctx);
          return this._cmpCore(op, a, b);
        }
        case "and": {
          const a = this._eval(node[1], ctx);
          if (!dslTruthy(a)) return a;
          return this._eval(node[2], ctx);
        }
        case "or": {
          const a = this._eval(node[1], ctx);
          if (dslTruthy(a)) return a;
          return this._eval(node[2], ctx);
        }
        case "ternary":
          if (dslTruthy(this._eval(node[1], ctx))) return this._eval(node[2], ctx);
          return this._eval(node[3], ctx);
        case "call": {
          const name = node[1];
          const args = node[2].map((a) => this._num(this._eval(a, ctx), name + "()"));
          return this._callCore(name, args);
        }
      }
      throw new DslEvalError("unreachable AST node " + JSON.stringify(tag));
    }
    _binCore(op, a, b) {
      if (op === "+") return a + b;
      if (op === "-") return a - b;
      if (op === "*") return a * b;
      if (op === "/") {
        if (b === 0.0) { this._warnOnce("div0", "division by zero guarded to 0.0"); return 0.0; }
        return a / b;
      }
      // '%' TRUNCATED remainder (JS % IS C fmod / truncated) — corpus-pinned.
      if (b === 0.0) { this._warnOnce("mod0", "modulo by zero guarded to 0.0"); return 0.0; }
      // JS % on non-finite already matches C fmod: (inf|nan) % y === NaN,
      // x % inf === x — the same totality dsl.py hand-codes.
      return a % b;
    }
    _cmpCore(op, a, b) {
      if (op === "==") return valuesEqual(a, b);
      if (op === "!=") return !valuesEqual(a, b);
      const na = this._num(a, "comparison " + JSON.stringify(op));
      const nb = this._num(b, "comparison " + JSON.stringify(op));
      if (op === "<") return na < nb;
      if (op === "<=") return na <= nb;
      if (op === ">") return na > nb;
      return na >= nb;
    }
    _callCore(name, args) {
      if (name === "min") return Math.min(args[0], args[1]);
      if (name === "max") return Math.max(args[0], args[1]);
      if (name === "abs") return Math.abs(args[0]);
      if (name === "clamp") return Math.min(Math.max(args[0], args[1]), args[2]);
      if (name === "round") { const x = args[0]; return isFinite(x) ? signedZero(roundHalfEven(x), x) : x; }
      if (name === "floor") { const x = args[0]; return isFinite(x) ? signedZero(Math.floor(x), x) : x; }
      // ceil
      const x = args[0];
      return isFinite(x) ? signedZero(Math.ceil(x), x) : x;
    }
  }

  function collectNamespaces(node, out) {
    if (node[0] === "ns") { if (out.indexOf(node[1]) === -1) out.push(node[1]); return; }
    for (let k = 1; k < node.length; k++) {
      const part = node[k];
      if (Array.isArray(part)) {
        if (part.length && Array.isArray(part[0])) { for (const sub of part) collectNamespaces(sub, out); }
        else collectNamespaces(part, out);
      }
    }
  }

  function parse(source) {
    if (typeof source !== "string" || source.trim() === "") {
      throw new DslParseError("expr: must be a non-empty string, got " + JSON.stringify(source));
    }
    const p = new Parser(source);
    const ast = p.parse();
    const nsNames = [];
    collectNamespaces(ast, nsNames);
    return new Expression(source, ast, nsNames);
  }

  function tryParse(source, where) {
    try { return parse(source); }
    catch (e) {
      if (e instanceof DslParseError) {
        if (typeof console !== "undefined" && console.warn) {
          console.warn("[CXC-DSL] expr refused on " + where + ": " + e.message);
        }
        return null;
      }
      throw e;
    }
  }

  function evaluate(source, opts) {
    opts = opts || {};
    return parse(source).evaluate({
      ns: opts.ns || null, vars: opts.vars || {},
      dt: opts.dt || 0.0, t: opts.t || 0.0,
    });
  }

  return {
    DslError, DslParseError, DslEvalError, FUNCTIONS,
    canon, tryNumber, dslTruthy, valuesEqual, coerceInput,
    Expression, parse, tryParse, evaluate, tokenize,
  };
});
