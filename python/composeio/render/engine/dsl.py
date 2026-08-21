"""
ComposeUX Expression DSL — the Python REFERENCE interpreter
([SPEC:CXC-DSL], Pass 2).

A single sandboxed, pure, bit-identical-across-backends expression:

    ns('fuel/remaining') - ns('pump/flow_rate') * dt
    ns('pump/damaged') ? 0.0 : min(ns('fuel/remaining'), 8.0)
    ns('fuel/remaining') >= 40 and ns('ship/charge') >= 60

Architecture: tokenizer -> recursive-descent (Pratt-precedence) parser
-> AST (plain tuples) -> tree-walking evaluator.  The interpreter builds
and walks its OWN AST; nothing here ever touches the Python execution
machinery — no dynamic evaluation of any kind ([CXC-DSL-SANDBOX], and
the [CXC-BIND-BOUNDARY] refusal detector must never fire on this file).

Grammar ([CXC-DSL-GRAMMAR], precedence high -> low):

    atoms       number (float64), true/false, 'single-quoted string',
                dt, t, $name (inline variable), ns('namespace'),
                whitelisted call, ( grouping )
    unary       -x, not x
    * / %       left-assoc
    + -         left-assoc
    < <= > >= == !=     non-assoc (one comparison per level)
    and         short-circuit
    or          short-circuit
    cond ? a : b        lazy ternary (lowest; right-assoc)

The CLOSED function set (v1) — anything else is a PARSE error
([CXC-DSL-C5]): min(a,b) max(a,b) abs(x) clamp(x,lo,hi) round(x)
floor(x) ceil(x).  ns('...') takes exactly one string LITERAL (names
must be static so the runtime can subscribe them).

Semantics ([CXC-DSL-SEMANTICS]) — every rule below is pinned by the
conformance corpus (dsl_conformance/dsl_corpus.json), which is the
byte-contract for the C++/CYD (Pass 2b) and JS backends:

  * float64 everywhere; left-assoc evaluation, NO reassociation.
  * guarded division: x/0 and x%0 -> 0.0 (one-time warning per
    expression), never NaN/inf ([CXC-DSL-C4]).
  * `%` is the TRUNCATED remainder (C fmod / JS `%` — sign of the
    dividend), NOT Python's floored `%`.  Pinned for cross-backend
    bit-identity; implemented via math.fmod.
  * round() is HALF-TO-EVEN (banker's / IEEE roundTiesToEven),
    corpus-pinned ([CXC-DSL-C12]).  round/floor/ceil preserve IEEE
    signed zero (ceil(-0.3) == -0.0).
  * `and`/`or` short-circuit and return the deciding OPERAND (Python/JS
    style): `a and b` == a when a is falsy else b.  Lazy ternary: the
    dead branch NEVER evaluates ([CXC-DSL-C3]).
  * truthiness: bool -> itself; number -> != 0.0; string -> canon(s)
    not in ('off', '') — shares the [CXC-BIND] canon vocabulary.
  * typed equality ([CXC-DSL-C6]): when BOTH sides coerce to numbers
    (bool -> 1/0, numeric strings parse), == compares as float64;
    otherwise both sides route through bindings.canon() so
    true == 'on' == '1' == 1.  Ordering comparators require numeric
    operands (author error otherwise).
  * ns() coercion: numeric bus values -> float64; bool -> 'on'/'off'
    canon token; other values -> str.  An UNSET namespace reads 0.0
    with a one-time warning (inspectable, never NaN — the guarded-
    division rationale).
  * dt/t are tick context ([CXC-DSL-STATE]): real at the output site
    (RenderApp.tick), 0.0 at the binding-value / trigger sites (those
    are event-evaluated, not integrated).

Sandbox ([CXC-DSL-C9]): expressions can name ONLY dt/t/$vars/ns(...) and
call ONLY the seven whitelisted functions.  There is no attribute
access, no indexing, no assignment, no iteration, no imports — those
are not merely refused, they are UNREPRESENTABLE (no such tokens or AST
nodes exist).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .bindings import canon

logger = logging.getLogger("composeio.render.dsl")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DslError(Exception):
    """Base class for every DSL failure."""


class DslParseError(DslError):
    """The source is not a sentence of the closed grammar."""


class DslEvalError(DslError):
    """A well-formed expression hit an author error at evaluation time
    (unknown $var, ordering comparison on a non-numeric, ...)."""


# ---------------------------------------------------------------------------
# The closed function table ([CXC-DSL-C5]) — name -> arity.
# Adding one is a SPEC change, not an author liberty.
# ---------------------------------------------------------------------------

FUNCTIONS: Dict[str, int] = {
    "min": 2, "max": 2, "abs": 1, "clamp": 3,
    "round": 1, "floor": 1, "ceil": 1,
}

_KEYWORDS = frozenset({"true", "false", "and", "or", "not", "dt", "t", "ns"})


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<number>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<string>'[^']*')
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<var>\$[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><=|>=|==|!=|[-+*/%<>?:(),])
""", re.VERBOSE)


def _tokenize(src: str) -> List[Tuple[str, Any, int, int]]:
    """(kind, value, start, end) tuples.  kind: num/str/name/var/op.
    end (exclusive source position) feeds the [SPEC:CXC-HMON] span
    table; the parser's error paths keep using start (index 2)."""
    tokens: List[Tuple[str, Any, int, int]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if m is None:
            raise DslParseError(
                f"unexpected character {src[pos]!r} at position {pos} "
                f"in expression {src!r}")
        if m.lastgroup == "ws":
            pass
        elif m.lastgroup == "number":
            tokens.append(("num", float(m.group()), pos, m.end()))
        elif m.lastgroup == "string":
            tokens.append(("str", m.group()[1:-1], pos, m.end()))
        elif m.lastgroup == "name":
            tokens.append(("name", m.group(), pos, m.end()))
        elif m.lastgroup == "var":
            tokens.append(("var", m.group()[1:], pos, m.end()))
        else:
            tokens.append(("op", m.group(), pos, m.end()))
        pos = m.end()
    return tokens


# ---------------------------------------------------------------------------
# Parser — recursive descent, spec precedence, AST = plain tuples:
#   ('num', f) ('str', s) ('bool', b) ('dt',) ('t',) ('var', name)
#   ('ns', name) ('call', fname, [args]) ('neg', x) ('not', x)
#   ('bin', op, a, b) ('cmp', op, a, b) ('and', a, b) ('or', a, b)
#   ('ternary', cond, a, b)
# ---------------------------------------------------------------------------

_CMP_OPS = frozenset({"<", "<=", ">", ">=", "==", "!="})


class _Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.tokens = _tokenize(src)
        self.i = 0
        # [SPEC:CXC-HMON] source spans: id(ast node) -> (start, end).
        # A side table, so the AST tuples (the corpus-pinned shape shared
        # with the C++/JS backends) stay untouched.  The Expression keeps
        # the AST alive, which keeps the ids stable.
        self.spans: Dict[int, Tuple[int, int]] = {}
        self._last_end = 0

    def _peek(self) -> Optional[Tuple[str, Any, int, int]]:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _next(self) -> Tuple[str, Any, int, int]:
        tok = self._peek()
        if tok is None:
            raise DslParseError(f"unexpected end of expression {self.src!r}")
        self.i += 1
        self._last_end = tok[3]
        return tok

    def _start(self) -> int:
        """Source position where the NEXT node will begin."""
        tok = self._peek()
        return tok[2] if tok is not None else self._last_end

    def _span(self, node: tuple, start: int) -> tuple:
        """Record *node*'s source span (first recording wins, so a
        parenthesized group keeps the inner expression's span)."""
        self.spans.setdefault(id(node), (start, self._last_end))
        return node

    def _expect_op(self, op: str) -> None:
        tok = self._next()
        if tok[0] != "op" or tok[1] != op:
            raise DslParseError(
                f"expected {op!r} at position {tok[2]} in {self.src!r}, "
                f"got {tok[1]!r}")

    def _at_op(self, *ops: str) -> bool:
        tok = self._peek()
        return tok is not None and tok[0] == "op" and tok[1] in ops

    def _at_name(self, *names: str) -> bool:
        tok = self._peek()
        return tok is not None and tok[0] == "name" and tok[1] in names

    # -- grammar, lowest first ------------------------------------------------

    def parse(self) -> tuple:
        node = self.expression()
        tok = self._peek()
        if tok is not None:
            raise DslParseError(
                f"unexpected {tok[1]!r} at position {tok[2]} in "
                f"{self.src!r} (comparisons are non-associative; "
                f"parenthesize compound conditions)")
        return node

    def expression(self) -> tuple:            # ternary — lowest
        start = self._start()
        cond = self.or_expr()
        if self._at_op("?"):
            self._next()
            a = self.expression()             # branches may nest ternaries
            self._expect_op(":")
            b = self.expression()
            return self._span(("ternary", cond, a, b), start)
        return cond

    def or_expr(self) -> tuple:
        start = self._start()
        node = self.and_expr()
        while self._at_name("or"):
            self._next()
            node = self._span(("or", node, self.and_expr()), start)
        return node

    def and_expr(self) -> tuple:
        start = self._start()
        node = self.cmp_expr()
        while self._at_name("and"):
            self._next()
            node = self._span(("and", node, self.cmp_expr()), start)
        return node

    def cmp_expr(self) -> tuple:              # non-assoc: at most ONE
        start = self._start()
        node = self.add_expr()
        if self._at_op(*_CMP_OPS):
            op = self._next()[1]
            node = self._span(("cmp", op, node, self.add_expr()), start)
        return node

    def add_expr(self) -> tuple:              # left-assoc, no reassociation
        start = self._start()
        node = self.mul_expr()
        while self._at_op("+", "-"):
            op = self._next()[1]
            node = self._span(("bin", op, node, self.mul_expr()), start)
        return node

    def mul_expr(self) -> tuple:
        start = self._start()
        node = self.unary()
        while self._at_op("*", "/", "%"):
            op = self._next()[1]
            node = self._span(("bin", op, node, self.unary()), start)
        return node

    def unary(self) -> tuple:
        start = self._start()
        if self._at_op("-"):
            self._next()
            return self._span(("neg", self.unary()), start)
        if self._at_name("not"):
            self._next()
            return self._span(("not", self.unary()), start)
        return self.primary()

    def primary(self) -> tuple:
        tok = self._next()
        kind, value, pos = tok[0], tok[1], tok[2]
        if kind == "num":
            return self._span(("num", value), pos)
        if kind == "str":
            return self._span(("str", value), pos)
        if kind == "var":
            return self._span(("var", value), pos)
        if kind == "op" and value == "(":
            node = self.expression()
            self._expect_op(")")
            return node
        if kind == "name":
            # NOTE: constant leaves are built via tuple([...]) so CPython
            # does not constant-fold them into ONE shared object — the
            # id-keyed span table needs a distinct tuple per occurrence.
            if value == "true":
                return self._span(tuple(["bool", True]), pos)
            if value == "false":
                return self._span(tuple(["bool", False]), pos)
            if value == "dt":
                return self._span(tuple(["dt"]), pos)
            if value == "t":
                return self._span(tuple(["t"]), pos)
            if value == "ns":
                return self._span(self._ns_call(pos), pos)
            if value in FUNCTIONS:
                return self._span(self._fn_call(value, pos), pos)
            # anything else is OUTSIDE the closed set — a parse error,
            # whether it looks like a call or a bare name ([CXC-DSL-C5]).
            raise DslParseError(
                f"unknown name {value!r} at position {pos} in "
                f"{self.src!r} — the DSL knows only dt, t, $vars, "
                f"ns('...'), true/false, and the functions "
                f"{'/'.join(sorted(FUNCTIONS))}")
        raise DslParseError(
            f"unexpected {value!r} at position {pos} in {self.src!r}")

    def _ns_call(self, pos: int) -> tuple:
        self._expect_op("(")
        tok = self._next()
        if tok[0] != "str":
            raise DslParseError(
                f"ns(...) takes exactly one string literal at position "
                f"{pos} in {self.src!r} — namespace names must be static "
                f"so the runtime can subscribe them")
        self._expect_op(")")
        return ("ns", tok[1])

    def _fn_call(self, name: str, pos: int) -> tuple:
        self._expect_op("(")
        args = [self.expression()]
        while self._at_op(","):
            self._next()
            args.append(self.expression())
        self._expect_op(")")
        arity = FUNCTIONS[name]
        if len(args) != arity:
            raise DslParseError(
                f"{name}() takes exactly {arity} argument(s), got "
                f"{len(args)} at position {pos} in {self.src!r}")
        return ("call", name, args)


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------

@dataclass
class EvalContext:
    """What an expression can see — and NOTHING else ([CXC-DSL-SANDBOX]):
    namespace values, inline variables, and the tick clock."""
    ns: Any = None                  # dict | Callable[[str], Any] | None
    vars: Dict[str, Any] = field(default_factory=dict)
    dt: float = 0.0
    t: float = 0.0

    def read_ns(self, name: str) -> Any:
        if callable(self.ns):
            return self.ns(name)
        if isinstance(self.ns, dict):
            return self.ns.get(name)
        return None


# ---------------------------------------------------------------------------
# Value model helpers — pinned by the corpus
# ---------------------------------------------------------------------------

_NUMERIC_STR_RE = re.compile(
    r"\s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\s*\Z")


def try_number(v: Any) -> Optional[float]:
    """The ONE numeric-coercion rule: float64 -> itself, bool -> 1/0,
    int -> float64, numeric-looking string -> parsed.  None on failure."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and _NUMERIC_STR_RE.match(v):
        return float(v)
    return None


def dsl_truthy(v: Any) -> bool:
    """DSL truthiness: bool -> itself; number -> != 0.0; string ->
    canon(s) not in ('off', '').  (NOT bindings.truthy, whose stringly
    rule calls the float 0.0 truthy.)"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v) != 0.0
    c = canon(v)
    return c not in ("off", "")


def _canon_token(v: Any) -> str:
    """canon() with float64 vocabulary: integral floats drop the '.0'
    so 1.0 -> '1' -> 'on' (typed equality, [CXC-DSL-C6])."""
    if isinstance(v, float) and not isinstance(v, bool):
        if math.isfinite(v) and v == int(v) and abs(v) < 1e16:
            return canon(str(int(v)))
        return canon(repr(v))
    return canon(v)


def values_equal(a: Any, b: Any) -> bool:
    """Typed equality ([CXC-DSL-SEMANTICS]): numeric when both sides
    coerce, else through the binding layer's canon()."""
    na, nb = try_number(a), try_number(b)
    if na is not None and nb is not None:
        return na == nb
    return _canon_token(a) == _canon_token(b)


def _signed_zero(r: float, x: float) -> float:
    """IEEE sign-of-zero preservation for round/floor/ceil."""
    if r == 0.0:
        return math.copysign(0.0, x)
    return r


# ---------------------------------------------------------------------------
# [SPEC:CXC-HMON] [CXC-HMON-DSL] — the trace record ("explain this
# evaluation").  JSON-native throughout so a trace publishes over the bus
# as-is and any peer (Monitor, raw Bus Monitor, an Intelligence) reads it.
# ---------------------------------------------------------------------------

@dataclass
class EvalTrace:
    """evaluate_traced()'s result: the SAME value evaluate() returns,
    plus the structured walk.  Step kinds:

        clock    {name: dt|t, value, span}
        var      {name, value, span}
        ns       {namespace, value, span, unset?}          (a read)
        op       {op, operands, result, span}              (bin/cmp/unary)
        logic    {op: and|or, operands, result, span,
                  short_circuit, dead_span?, dead_evaluated?}
        fn       {fn, operands, result, span}
        ternary  {cond, taken: then|else, result, span,
                  cond_span, taken_span, dead_span, dead_evaluated: False}

    Spans are [start, end) offsets into ``source`` so a viewer annotates
    the expression text directly ([CXC-HMON-C7]) without re-parsing."""
    value: Any
    source: str
    steps: List[dict] = field(default_factory=list)

    def reads(self) -> Dict[str, Any]:
        """namespace -> value actually READ during this evaluation
        (dead-branch namespaces are absent by construction)."""
        out: Dict[str, Any] = {}
        for s in self.steps:
            if s.get("kind") == "ns" and s["namespace"] not in out:
                out[s["namespace"]] = s["value"]
        return out

    def to_dict(self) -> dict:
        return {"source": self.source, "value": self.value,
                "steps": self.steps}


# ---------------------------------------------------------------------------
# The compiled expression
# ---------------------------------------------------------------------------

class Expression:
    """A parsed, immutable expression: pure function of
    (namespace values, $vars, dt, t) -> value ([CXC-DSL-INTENT])."""

    __slots__ = ("source", "ast", "namespaces", "spans", "_warned")

    def __init__(self, source: str, ast: tuple, namespaces: Tuple[str, ...],
                 spans: Optional[Dict[int, Tuple[int, int]]] = None):
        self.source = source
        self.ast = ast
        self.namespaces = namespaces    # static ns() reads, for subscription
        # [SPEC:CXC-HMON] id(node) -> (start, end) source spans; holding
        # the AST on self keeps the ids stable for the expression's life.
        self.spans = spans or {}
        self._warned: set = set()       # one-time warnings ([CXC-DSL-C4])

    def __repr__(self) -> str:
        return f"Expression({self.source!r})"

    # -- warnings (one-time per expression per condition) ------------------

    def _warn_once(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            logger.warning("[CXC-DSL] %s in expr %r", msg, self.source)

    # -- evaluation ----------------------------------------------------------

    def evaluate(self, ctx: EvalContext) -> Any:
        return self._eval(self.ast, ctx)

    def _num(self, node_val: Any, what: str) -> float:
        n = try_number(node_val)
        if n is None:
            raise DslEvalError(
                f"{what} needs a numeric value, got {node_val!r} "
                f"(expr {self.source!r})")
        return n

    def _eval(self, node: tuple, ctx: EvalContext) -> Any:
        tag = node[0]

        if tag == "num":
            return node[1]
        if tag == "str":
            return node[1]
        if tag == "bool":
            return node[1]
        if tag == "dt":
            return float(ctx.dt)
        if tag == "t":
            return float(ctx.t)

        if tag == "var":
            name = node[1]
            if name not in ctx.vars:
                raise DslEvalError(
                    f"unknown variable ${name} (expr {self.source!r}) — "
                    f"declare it in a variables: block")
            return _coerce_input(ctx.vars[name])

        if tag == "ns":
            raw = ctx.read_ns(node[1])
            if raw is None:
                self._warn_once(
                    f"ns:{node[1]}",
                    f"namespace {node[1]!r} has no value yet — reading 0.0")
                return 0.0
            return _coerce_input(raw)

        if tag == "neg":
            return -self._num(self._eval(node[1], ctx), "unary -")

        if tag == "not":
            return not dsl_truthy(self._eval(node[1], ctx))

        if tag == "bin":
            op = node[1]
            a = self._num(self._eval(node[2], ctx), f"operator {op!r}")
            b = self._num(self._eval(node[3], ctx), f"operator {op!r}")
            return self._bin_core(op, a, b)

        if tag == "cmp":
            op = node[1]
            a = self._eval(node[2], ctx)
            b = self._eval(node[3], ctx)
            return self._cmp_core(op, a, b)

        if tag == "and":                # short-circuit, operand-returning
            a = self._eval(node[1], ctx)
            if not dsl_truthy(a):
                return a
            return self._eval(node[2], ctx)

        if tag == "or":
            a = self._eval(node[1], ctx)
            if dsl_truthy(a):
                return a
            return self._eval(node[2], ctx)

        if tag == "ternary":            # lazy: the dead branch never runs
            if dsl_truthy(self._eval(node[1], ctx)):
                return self._eval(node[2], ctx)
            return self._eval(node[3], ctx)

        if tag == "call":
            name = node[1]
            args = [self._num(self._eval(a, ctx), f"{name}()")
                    for a in node[2]]
            return self._call_core(name, args)

        raise DslEvalError(f"unreachable AST node {tag!r}")   # pragma: no cover

    # -- op cores — ONE implementation shared by evaluate AND
    # evaluate_traced ([CXC-HMON-DSL]: the traced walk must be the same
    # value, so the arithmetic lives exactly once) ------------------------

    def _bin_core(self, op: str, a: float, b: float) -> float:
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            if b == 0.0:
                self._warn_once("div0", "division by zero guarded to 0.0")
                return 0.0
            return a / b
        # '%' — TRUNCATED remainder (C fmod / JS %), corpus-pinned.
        if b == 0.0:
            self._warn_once("mod0", "modulo by zero guarded to 0.0")
            return 0.0
        # Totality on non-finite inputs — match C++ std::fmod exactly
        # (fmod(inf|nan, y) = nan; fmod(x, inf) = x).  Found by the
        # Pass-2b falsification test: math.fmod raised on inf inputs,
        # breaking the value|error contract ([CXC-DSL-SEMANTICS]).
        if not (math.isfinite(a) and math.isfinite(b)):
            if math.isnan(a) or math.isnan(b) or math.isinf(a):
                return math.nan
            return a                        # a finite, b infinite
        return math.fmod(a, b)

    def _cmp_core(self, op: str, a: Any, b: Any) -> bool:
        if op == "==":
            return values_equal(a, b)
        if op == "!=":
            return not values_equal(a, b)
        na = self._num(a, f"comparison {op!r}")
        nb = self._num(b, f"comparison {op!r}")
        if op == "<":
            return na < nb
        if op == "<=":
            return na <= nb
        if op == ">":
            return na > nb
        return na >= nb

    def _call_core(self, name: str, args: List[float]) -> float:
        if name == "min":
            return min(args[0], args[1])
        if name == "max":
            return max(args[0], args[1])
        if name == "abs":
            return abs(args[0])
        if name == "clamp":
            # pinned composition: min(max(x, lo), hi) — lo > hi
            # resolves to hi (corpus-encoded)
            return min(max(args[0], args[1]), args[2])
        # round/floor/ceil: non-finite passes through (inf->inf,
        # nan->nan) to match C++ floor/ceil/round and stay total —
        # Python's round/math.floor/ceil RAISE on inf/nan (the Pass-2b
        # falsification finding, [CXC-DSL-SEMANTICS]).
        if name == "round":
            # HALF-TO-EVEN (banker's / IEEE), signed zero preserved
            # ([CXC-DSL-C12])
            x = args[0]
            if not math.isfinite(x):
                return x
            return _signed_zero(float(round(x)), x)
        if name == "floor":
            x = args[0]
            if not math.isfinite(x):
                return x
            return _signed_zero(float(math.floor(x)), x)
        # ceil
        x = args[0]
        if not math.isfinite(x):
            return x
        return _signed_zero(float(math.ceil(x)), x)

    # -- traced evaluation ([SPEC:CXC-HMON] [CXC-HMON-DSL]) -----------------

    def evaluate_traced(self, ctx: EvalContext) -> "EvalTrace":
        """Evaluate exactly like evaluate() — same walk order, same op
        cores, bit-identical value — while recording the walk as a list
        of JSON-native step dicts: every ns()/$var/dt/t read + value,
        every operator/function + operands + result, and for a ternary
        or short-circuit the branch taken WITH the dead branch's source
        span recorded as not-evaluated ([CXC-HMON-C2]).

        Steps are appended in evaluation (post-order) order.  Each step
        carries the node's source span [start, end) where known, so a
        viewer can annotate the source text without re-parsing.  Raises
        DslEvalError exactly as evaluate() does."""
        steps: List[dict] = []
        value = self._eval_traced(self.ast, ctx, steps)
        return EvalTrace(value=value, source=self.source, steps=steps)

    def _span_of(self, node: tuple) -> Optional[List[int]]:
        got = self.spans.get(id(node))
        return [got[0], got[1]] if got is not None else None

    def _eval_traced(self, node: tuple, ctx: EvalContext,
                     steps: List[dict]) -> Any:
        tag = node[0]

        if tag in ("num", "str", "bool"):
            return node[1]                  # literals: not trace-worthy

        if tag == "dt" or tag == "t":
            value = float(ctx.dt) if tag == "dt" else float(ctx.t)
            steps.append({"kind": "clock", "name": tag, "value": value,
                          "span": self._span_of(node)})
            return value

        if tag == "var":
            name = node[1]
            if name not in ctx.vars:
                raise DslEvalError(
                    f"unknown variable ${name} (expr {self.source!r}) — "
                    f"declare it in a variables: block")
            value = _coerce_input(ctx.vars[name])
            steps.append({"kind": "var", "name": name, "value": value,
                          "span": self._span_of(node)})
            return value

        if tag == "ns":
            raw = ctx.read_ns(node[1])
            if raw is None:
                self._warn_once(
                    f"ns:{node[1]}",
                    f"namespace {node[1]!r} has no value yet — reading 0.0")
                value: Any = 0.0
                unset = True
            else:
                value = _coerce_input(raw)
                unset = False
            step = {"kind": "ns", "namespace": node[1], "value": value,
                    "span": self._span_of(node)}
            if unset:
                step["unset"] = True
            steps.append(step)
            return value

        if tag == "neg":
            a = self._eval_traced(node[1], ctx, steps)
            result = -self._num(a, "unary -")
            steps.append({"kind": "op", "op": "neg", "operands": [a],
                          "result": result, "span": self._span_of(node)})
            return result

        if tag == "not":
            a = self._eval_traced(node[1], ctx, steps)
            result = not dsl_truthy(a)
            steps.append({"kind": "op", "op": "not", "operands": [a],
                          "result": result, "span": self._span_of(node)})
            return result

        if tag == "bin":
            op = node[1]
            # order preserved vs _eval: eval a -> num a -> eval b -> num b
            a = self._num(self._eval_traced(node[2], ctx, steps),
                          f"operator {op!r}")
            b = self._num(self._eval_traced(node[3], ctx, steps),
                          f"operator {op!r}")
            result = self._bin_core(op, a, b)
            steps.append({"kind": "op", "op": op, "operands": [a, b],
                          "result": result, "span": self._span_of(node)})
            return result

        if tag == "cmp":
            op = node[1]
            a = self._eval_traced(node[2], ctx, steps)
            b = self._eval_traced(node[3], ctx, steps)
            result = self._cmp_core(op, a, b)
            steps.append({"kind": "op", "op": op, "operands": [a, b],
                          "result": result, "span": self._span_of(node)})
            return result

        if tag == "and" or tag == "or":
            a = self._eval_traced(node[1], ctx, steps)
            took_right = dsl_truthy(a) if tag == "and" \
                else not dsl_truthy(a)
            step = {"kind": "logic", "op": tag, "operands": [a],
                    "span": self._span_of(node)}
            if took_right:
                result = self._eval_traced(node[2], ctx, steps)
                step["operands"] = [a, result]
                step["short_circuit"] = False
            else:
                result = a
                step["short_circuit"] = True
                # the dead operand was NOT evaluated ([CXC-HMON-C2])
                step["dead_span"] = self._span_of(node[2])
                step["dead_evaluated"] = False
            step["result"] = result
            steps.append(step)
            return result

        if tag == "ternary":            # lazy: the dead branch never runs
            cond = self._eval_traced(node[1], ctx, steps)
            taken_then = dsl_truthy(cond)
            branch = node[2] if taken_then else node[3]
            dead = node[3] if taken_then else node[2]
            result = self._eval_traced(branch, ctx, steps)
            steps.append({
                "kind": "ternary", "cond": cond,
                "taken": "then" if taken_then else "else",
                "result": result, "span": self._span_of(node),
                "cond_span": self._span_of(node[1]),
                "taken_span": self._span_of(branch),
                "dead_span": self._span_of(dead),
                "dead_evaluated": False,     # [CXC-HMON-C2]: never ran
            })
            return result

        if tag == "call":
            name = node[1]
            args = [self._num(self._eval_traced(a, ctx, steps), f"{name}()")
                    for a in node[2]]
            result = self._call_core(name, args)
            steps.append({"kind": "fn", "fn": name, "operands": list(args),
                          "result": result, "span": self._span_of(node)})
            return result

        raise DslEvalError(f"unreachable AST node {tag!r}")   # pragma: no cover


def _coerce_input(raw: Any) -> Any:
    """ns()/$var value coercion ([CXC-DSL-GRAMMAR] namespace read):
    numeric -> float64; bool -> canon 'on'/'off' token; else str."""
    if isinstance(raw, bool):
        return "on" if raw else "off"
    n = try_number(raw)
    if n is not None:
        return n
    if isinstance(raw, str):
        return raw
    return str(raw)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _collect_namespaces(node: tuple, out: List[str]) -> None:
    if node[0] == "ns":
        if node[1] not in out:
            out.append(node[1])
        return
    for part in node[1:]:
        if isinstance(part, tuple):
            _collect_namespaces(part, out)
        elif isinstance(part, list):
            for sub in part:
                _collect_namespaces(sub, out)


def parse(source: Any) -> Expression:
    """Parse *source* into an Expression.  Raises DslParseError on
    anything outside the closed grammar ([CXC-DSL-C1])."""
    if not isinstance(source, str) or not source.strip():
        raise DslParseError(f"expr: must be a non-empty string, "
                            f"got {source!r}")
    p = _Parser(source)
    ast = p.parse()
    ns_names: List[str] = []
    _collect_namespaces(ast, ns_names)
    return Expression(source, ast, tuple(ns_names), spans=p.spans)


def try_parse(source: Any, where: str) -> Optional[Expression]:
    """Config-site parse: on error, warn and return None so the site
    REFUSES the entry (never falls back to executing anything)."""
    try:
        return parse(source)
    except DslParseError as e:
        logger.warning("[CXC-DSL] expr refused on %s: %s", where, e)
        return None


def evaluate(source: str, *, ns: Any = None,
             vars: Optional[Dict[str, Any]] = None,
             dt: float = 0.0, t: float = 0.0) -> Any:
    """One-shot convenience: parse + evaluate."""
    return parse(source).evaluate(
        EvalContext(ns=ns, vars=vars or {}, dt=dt, t=t))
