#!/usr/bin/env python3
"""
Generate the ComposeUX Expression DSL conformance corpus
([SPEC:CXC-DSL] [CXC-DSL-CROSSPLAT]).

The corpus is the BYTE-CONTRACT every backend interpreter (Python
reference, C++/CYD Pass 2b, JS/browser tier) must reproduce exactly.
Expected values here are computed by INDEPENDENT plain-Python mirrors of
the pinned semantics — never by calling the interpreter under test —
so the corpus checks the reference rather than echoing it.

Output: composeio/render/engine/dsl_conformance/dsl_corpus.json
Format: documented in dsl_conformance/README.md (format v1).

Reproducible: re-running this script must produce the identical file.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir,
                   "composeio", "render", "engine", "dsl_conformance",
                   "dsl_corpus.json")

CASES = []


def _expected(value):
    """Encode an expected value: numbers carry authoritative IEEE-754
    bits (big-endian hex); bool/string carry the value."""
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, (int, float)):
        f = float(value)
        approx = f if math.isfinite(f) else ("inf" if f > 0 else "-inf")
        return {"type": "number",
                "hex": struct.pack(">d", f).hex(),
                "approx": approx}
    return {"type": "string", "value": str(value)}


def case(cid, group, expr, expected, *, ns=None, vars=None, dt=0.0, t=0.0):
    entry = {"id": cid, "group": group, "expr": expr,
             "expected": _expected(expected)}
    if ns:
        entry["ns"] = ns
    if vars:
        entry["vars"] = vars
    if dt:
        entry["dt"] = dt
    if t:
        entry["t"] = t
    CASES.append(entry)


def err(cid, group, expr, kind, *, ns=None, vars=None):
    entry = {"id": cid, "group": group, "expr": expr, "error": kind}
    if ns:
        entry["ns"] = ns
    if vars:
        entry["vars"] = vars
    CASES.append(entry)


# ---------------------------------------------------------------------------
# Independent semantic mirrors (plain Python, hand-written to the spec)
# ---------------------------------------------------------------------------

def m_fmod(a, b):                       # pinned: C fmod / JS %, guarded
    return 0.0 if b == 0.0 else math.fmod(a, b)


def m_div(a, b):                        # pinned: guarded division
    return 0.0 if b == 0.0 else a / b


def m_round(x):                         # pinned: half-to-even, IEEE zero sign
    r = float(round(x))
    return math.copysign(0.0, x) if r == 0.0 else r


def m_floor(x):
    r = float(math.floor(x))
    return math.copysign(0.0, x) if r == 0.0 else r


def m_ceil(x):
    r = float(math.ceil(x))
    return math.copysign(0.0, x) if r == 0.0 else r


def m_clamp(x, lo, hi):                 # pinned composition
    return min(max(x, lo), hi)


# ---------------------------------------------------------------------------
# 1. Atoms & literals
# ---------------------------------------------------------------------------

case("atom-001", "atoms", "42", 42.0)
case("atom-002", "atoms", "3.5", 3.5)
case("atom-003", "atoms", ".5", 0.5)
case("atom-004", "atoms", "2.", 2.0)
case("atom-005", "atoms", "1e3", 1000.0)
case("atom-006", "atoms", "2.5e-2", 0.025)
case("atom-007", "atoms", "true", True)
case("atom-008", "atoms", "false", False)
case("atom-009", "atoms", "'hello'", "hello")
case("atom-010", "atoms", "''", "")
case("atom-011", "atoms", "dt", 0.1, dt=0.1)
case("atom-012", "atoms", "t", 2.5, t=2.5)
case("atom-013", "atoms", "dt", 0.0)
case("atom-014", "atoms", "(7)", 7.0)
case("atom-015", "atoms", "1e400", float("1e400"))     # literal overflow: inf

# ---------------------------------------------------------------------------
# 2. Precedence & associativity ([CXC-DSL-C1]/[C2] — no reassociation)
# ---------------------------------------------------------------------------

case("prec-001", "precedence", "2 + 3 * 4", 2.0 + 3.0 * 4.0)
case("prec-002", "precedence", "(2 + 3) * 4", (2.0 + 3.0) * 4.0)
case("prec-003", "precedence", "10 - 3 - 2", (10.0 - 3.0) - 2.0)
case("prec-004", "precedence", "100 / 10 / 5", (100.0 / 10.0) / 5.0)
case("prec-005", "precedence", "2 * 3 % 4", m_fmod(2.0 * 3.0, 4.0))
case("prec-006", "precedence", "1 + 2 == 3", True)
case("prec-007", "precedence", "-2 * 3", (-2.0) * 3.0)
case("prec-008", "precedence", "-2 - 3", (-2.0) - 3.0)
case("prec-009", "precedence", "not 0 and 0", 0.0)      # (not 0) and 0
case("prec-010", "precedence", "1 or 0 and 0", 1.0)     # 1 or (0 and 0)
case("prec-011", "precedence", "1 ? 2 : 3 + 10", 2.0)
case("prec-012", "precedence", "0 ? 2 : 3 + 10", 13.0)
case("prec-013", "precedence", "0 ? 1 : 1 ? 2 : 3", 2.0)   # right-nest
case("prec-014", "precedence", "1 == 1 and 2 == 2", True)
# float non-reassociation: left-assoc bits are the contract
case("prec-015", "precedence", "0.1 + 0.2 + 0.3", (0.1 + 0.2) + 0.3)
case("prec-016", "precedence", "0.1 + (0.2 + 0.3)", 0.1 + (0.2 + 0.3))
case("prec-017", "precedence", "1e16 + 1 - 1e16", (1e16 + 1.0) - 1e16)
case("prec-018", "precedence", "1e16 + (1 - 1e16)", 1e16 + (1.0 - 1e16))
case("prec-019", "precedence", "not true", False)
case("prec-020", "precedence", "not not 5", True)

# ---------------------------------------------------------------------------
# 3. Short-circuit and / or (operand-returning) + lazy ternary ([CXC-DSL-C3])
# ---------------------------------------------------------------------------

case("lazy-001", "short_circuit", "false and 1/0", False)
case("lazy-002", "short_circuit", "0 and 1/0", 0.0)
case("lazy-003", "short_circuit", "true or 1/0", True)
case("lazy-004", "short_circuit", "1 or 1/0", 1.0)
case("lazy-005", "short_circuit", "1 ? 5 : 1/0", 5.0)
case("lazy-006", "short_circuit", "0 ? 1/0 : 7", 7.0)
case("lazy-007", "short_circuit", "0 or 5", 5.0)
case("lazy-008", "short_circuit", "3 and 7", 7.0)
case("lazy-009", "short_circuit", "'' or 'x'", "x")
case("lazy-010", "short_circuit", "'off' and 5", "off")
case("lazy-011", "short_circuit", "'on' and 5", 5.0)
case("lazy-012", "short_circuit", "false or ''", "")
case("lazy-013", "short_circuit", "0.0 and true", 0.0)

# ---------------------------------------------------------------------------
# 4. Guarded division / modulo ([CXC-DSL-C4]) — 0.0, never NaN/inf
# ---------------------------------------------------------------------------

case("div0-001", "guarded_div", "1 / 0", 0.0)
case("div0-002", "guarded_div", "-1 / 0", 0.0)          # POSITIVE zero
case("div0-003", "guarded_div", "0 / 0", 0.0)
case("div0-004", "guarded_div", "5 % 0", 0.0)
case("div0-005", "guarded_div", "1 / -0.0", 0.0)        # -0.0 divisor guarded
case("div0-006", "guarded_div", "10 / (2 - 2)", 0.0)
case("div0-007", "guarded_div", "1 / 0 + 5", 5.0)

# ---------------------------------------------------------------------------
# 5. Modulo semantics — pinned: TRUNCATED remainder (C fmod / JS %)
# ---------------------------------------------------------------------------

case("mod-001", "modulo", "-7 % 2", m_fmod(-7.0, 2.0))          # -1.0
case("mod-002", "modulo", "7 % -2", m_fmod(7.0, -2.0))          # 1.0
case("mod-003", "modulo", "7.5 % 2", m_fmod(7.5, 2.0))
case("mod-004", "modulo", "-7.5 % 2", m_fmod(-7.5, 2.0))
case("mod-005", "modulo", "5 % 3", m_fmod(5.0, 3.0))
case("mod-006", "modulo", "5.5 % 1.25", m_fmod(5.5, 1.25))

# ---------------------------------------------------------------------------
# 6. Float64 edge values ([CXC-DSL-C2] exact bits)
# ---------------------------------------------------------------------------

case("f64-001", "float64", "0.1 + 0.2", 0.1 + 0.2)
case("f64-002", "float64", "0.1 + 0.2 == 0.3", (0.1 + 0.2) == 0.3)  # False
case("f64-003", "float64", "1 / 3", 1.0 / 3.0)
case("f64-004", "float64", "1e308 * 10", float("inf"))     # IEEE overflow
case("f64-005", "float64", "-1e308 * 10", float("-inf"))
case("f64-006", "float64", "1e-320", 1e-320)               # subnormal
case("f64-007", "float64", "9007199254740993", 9007199254740993.0)  # 2^53+1
case("f64-008", "float64", "-0.0", -0.0)
case("f64-009", "float64", "0.0 - 0.0", 0.0 - 0.0)         # +0.0
case("f64-010", "float64", "-(0.0)", -0.0)
case("f64-011", "float64", "2 / 7 * 7", (2.0 / 7.0) * 7.0)
case("f64-012", "float64", "0.007 * 100", 0.007 * 100.0)

# ---------------------------------------------------------------------------
# 7. Comparisons + canon-typed equality ([CXC-DSL-C6])
# ---------------------------------------------------------------------------

case("cmp-001", "comparison", "1 < 2", True)
case("cmp-002", "comparison", "2 <= 2", True)
case("cmp-003", "comparison", "3 > 2", True)
case("cmp-004", "comparison", "2 >= 3", False)
case("cmp-005", "comparison", "1 == 1.0", True)
case("cmp-006", "comparison", "1 != 2", True)
case("cmp-007", "comparison", "-0.0 == 0.0", True)          # IEEE equality
case("eq-001", "canon_equality", "true == 'on'", True)
case("eq-002", "canon_equality", "1 == 'on'", True)
case("eq-003", "canon_equality", "'1' == true", True)
case("eq-004", "canon_equality", "2 == '2'", True)          # numeric route
case("eq-005", "canon_equality", "'off' == 0", True)
case("eq-006", "canon_equality", "false == 'no'", True)
case("eq-007", "canon_equality", "'crit' == 'CRIT'", True)  # canon lowers
case("eq-008", "canon_equality", "'a' != 'b'", True)
case("eq-009", "canon_equality", "'x' == 5", False)
case("eq-010", "canon_equality", "true == 'yes'", True)
case("eq-011", "canon_equality", "'2.0' == 2", True)        # numeric route
case("eq-012", "canon_equality", "'on' != 'off'", True)
case("eq-013", "canon_equality", "ns('d') == true", True, ns={"d": "on"})
case("eq-014", "canon_equality", "ns('d') == 1", True, ns={"d": True})

# ---------------------------------------------------------------------------
# 8. Truthiness / not
# ---------------------------------------------------------------------------

case("truth-001", "truthiness", "not 0", True)
case("truth-002", "truthiness", "not 0.5", False)
case("truth-003", "truthiness", "not 'off'", True)
case("truth-004", "truthiness", "not 'on'", False)
case("truth-005", "truthiness", "not ''", True)
case("truth-006", "truthiness", "not 'false'", True)        # canon 'off'
case("truth-007", "truthiness", "not 'anything'", False)
case("truth-008", "truthiness", "not -0.0", True)           # -0.0 is falsy

# ---------------------------------------------------------------------------
# 9. The closed function set at boundaries ([CXC-DSL-C5]/[C12])
# ---------------------------------------------------------------------------

case("fn-001", "functions", "min(2, 3)", 2.0)
case("fn-002", "functions", "min(3, 2)", 2.0)
case("fn-003", "functions", "max(2, 3)", 3.0)
case("fn-004", "functions", "min(-0.0, 0.0)", min(-0.0, 0.0))
case("fn-005", "functions", "abs(-3.5)", 3.5)
case("fn-006", "functions", "abs(-0.0)", abs(-0.0))          # +0.0
case("fn-007", "functions", "abs(2)", 2.0)
case("fn-008", "functions", "clamp(5, 0, 10)", m_clamp(5.0, 0.0, 10.0))
case("fn-009", "functions", "clamp(-5, 0, 10)", m_clamp(-5.0, 0.0, 10.0))
case("fn-010", "functions", "clamp(15, 0, 10)", m_clamp(15.0, 0.0, 10.0))
case("fn-011", "functions", "clamp(5, 10, 0)", m_clamp(5.0, 10.0, 0.0))
# ^ pinned: clamp == min(max(x, lo), hi), so lo > hi resolves to hi
case("fn-012", "functions", "clamp(0.5, 0, 1)", 0.5)
# round(): HALF-TO-EVEN (banker's / IEEE), the [CXC-DSL-C12] pin
case("rnd-001", "round_half_even", "round(0.5)", m_round(0.5))    # 0.0
case("rnd-002", "round_half_even", "round(1.5)", m_round(1.5))    # 2.0
case("rnd-003", "round_half_even", "round(2.5)", m_round(2.5))    # 2.0
case("rnd-004", "round_half_even", "round(3.5)", m_round(3.5))    # 4.0
case("rnd-005", "round_half_even", "round(-0.5)", m_round(-0.5))  # -0.0
case("rnd-006", "round_half_even", "round(-1.5)", m_round(-1.5))  # -2.0
case("rnd-007", "round_half_even", "round(-2.5)", m_round(-2.5))  # -2.0
case("rnd-008", "round_half_even", "round(2.675)", m_round(2.675))
# ^ 2.675 is stored as 2.67499999999999982... -> rounds DOWN to 3? No: to 2
#   or 3 per the double's true value; the mirror computes it — the point is
#   backends must round the DOUBLE, not the decimal literal.
case("rnd-009", "round_half_even", "round(0.49999999999999994)",
     m_round(0.49999999999999994))
case("rnd-010", "round_half_even", "round(2.4)", m_round(2.4))
case("rnd-011", "round_half_even", "round(-2.6)", m_round(-2.6))
case("flr-001", "floor_ceil", "floor(2.7)", m_floor(2.7))
case("flr-002", "floor_ceil", "floor(-0.3)", m_floor(-0.3))       # -1.0
case("flr-003", "floor_ceil", "floor(-2.7)", m_floor(-2.7))
case("flr-004", "floor_ceil", "floor(-0.0)", m_floor(-0.0))       # -0.0
case("flr-005", "floor_ceil", "floor(3.0)", m_floor(3.0))
case("ceil-001", "floor_ceil", "ceil(2.3)", m_ceil(2.3))
case("ceil-002", "floor_ceil", "ceil(-0.3)", m_ceil(-0.3))        # -0.0
case("ceil-003", "floor_ceil", "ceil(-2.3)", m_ceil(-2.3))        # -2.0
case("ceil-004", "floor_ceil", "ceil(0.0)", m_ceil(0.0))
case("fn-013", "functions", "min(max(0, -5), 10)", 0.0)     # nesting
case("fn-014", "functions", "round(clamp(2.5, 0, 10))", m_round(2.5))

# ---------------------------------------------------------------------------
# 10. ns() coercion + inline variables
# ---------------------------------------------------------------------------

case("ns-001", "ns_read", "ns('a')", 42.0, ns={"a": 42})
case("ns-002", "ns_read", "ns('a')", 42.0, ns={"a": "42"})   # numeric string
case("ns-003", "ns_read", "ns('a')", "on", ns={"a": True})   # bool -> canon
case("ns-004", "ns_read", "ns('a')", "off", ns={"a": False})
case("ns-005", "ns_read", "ns('missing')", 0.0)              # unset -> 0.0
case("ns-006", "ns_read", "ns('mode')", "CRIT", ns={"mode": "CRIT"})
case("ns-007", "ns_read", "ns('a') + ns('b')", 5.0, ns={"a": 2, "b": 3})
case("ns-008", "ns_read", "ns('a') * 2", 5.0, ns={"a": 2.5})
case("ns-009", "ns_read", "ns('missing') >= 40", False)
case("ns-010", "ns_read", "ns('d') ? 1.0 : 2.0", 1.0, ns={"d": "on"})
case("ns-011", "ns_read", "ns('d') ? 1.0 : 2.0", 2.0, ns={"d": "off"})
case("var-001", "variables", "$k * 4", 1.0, vars={"k": 0.25})
case("var-002", "variables", "$name", "pump", vars={"name": "pump"})
case("var-003", "variables", "$flag", "on", vars={"flag": True})
case("var-004", "variables", "$n + 1", 41.0, vars={"n": 40})
case("var-005", "variables", "ns('f') >= $lo", True,
     ns={"f": 50}, vars={"lo": 40})

# ---------------------------------------------------------------------------
# 11. Errors — the sandbox is a PARSE boundary ([CXC-DSL-C9])
# ---------------------------------------------------------------------------

err("sbx-001", "sandbox", "__import__('os')", "parse")
err("sbx-002", "sandbox", "x.__class__", "parse")
err("sbx-003", "sandbox", "a[0]", "parse")
err("sbx-004", "sandbox", "foo()", "parse")
err("sbx-005", "sandbox", "x", "parse")
err("sbx-006", "sandbox", "ns(dt)", "parse")            # non-literal ns name
err("sbx-007", "sandbox", "ns('a' + 'b')", "parse")
err("sbx-008", "sandbox", "1 < 2 < 3", "parse")         # cmp non-assoc
err("sbx-009", "sandbox", "min(1)", "parse")            # arity
err("sbx-010", "sandbox", "clamp(1, 2)", "parse")
err("sbx-011", "sandbox", "'unterminated", "parse")
err("sbx-012", "sandbox", "{{ x }}", "parse")
err("sbx-013", "sandbox", "x = 1", "parse")
err("sbx-014", "sandbox", "lambda: 1", "parse")
err("sbx-015", "sandbox", "sqrt(4)", "parse")           # no transcendentals
err("sbx-016", "sandbox", "pow(2, 3)", "parse")
err("sbx-017", "sandbox", "eval('1')", "parse")
err("sbx-018", "sandbox", "1 +", "parse")
err("sbx-019", "sandbox", "", "parse")
err("sbx-020", "sandbox", "import os", "parse")
err("evl-001", "eval_error", "'a' + 'b'", "eval")       # no concatenation
err("evl-002", "eval_error", "'a' < 'b'", "eval")       # ordering non-numeric
err("evl-003", "eval_error", "-'a'", "eval")
err("evl-004", "eval_error", "min('a', 1)", "eval")
err("evl-005", "eval_error", "$unknown", "eval")
err("evl-006", "eval_error", "ns('mode') * 2", "eval", ns={"mode": "warp"})

# ---------------------------------------------------------------------------
# 12. The triad over a tick sweep ([CXC-DSL-TRIAD] — the flagship)
#
# A plain-Python simulation of the re-authored jump-drive triad; each
# step contributes standalone cases (expr + ns snapshot -> expected).
# tank:      ns('fuel/remaining') - ns('pump/flow_rate') * dt
# pump:      min(ns('pump/damaged') ? 2.5 : 10.0, 0.25 * ns('fuel/remaining'))
# readiness: ns('fuel/remaining') >= 40 and ns('ship/charge') >= 60
# ---------------------------------------------------------------------------

TANK_EXPR = "ns('fuel/remaining') - ns('pump/flow_rate') * dt"
PUMP_EXPR = ("min(ns('pump/damaged') ? 2.5 : 10.0, "
             "0.25 * ns('fuel/remaining'))")
READY_EXPR = "ns('fuel/remaining') >= 40 and ns('ship/charge') >= 60"


def sim_pump(damaged, remaining):
    demand = 2.5 if damaged else 10.0
    return min(demand, 0.25 * remaining)


remaining = 100.0
flow = 0.0          # pump/flow_rate unset on the very first tick reads 0.0
seq = 0
# scripted events: (tick_index, damaged, charge)
script = {0: (False, 0.0), 3: (True, 0.0), 6: (False, 80.0),
          10: (False, 50.0)}
damaged, charge = False, 0.0
for k in range(14):
    if k in script:
        damaged, charge = script[k]
    dt = 0.1 if k > 0 else 0.0
    ns_snap = {"fuel/remaining": remaining, "pump/flow_rate": flow,
               "pump/damaged": ("on" if damaged else "off"),
               "ship/charge": charge}
    nxt = remaining - flow * dt
    case(f"triad-tank-{seq:03d}", "triad_sweep", TANK_EXPR, nxt,
         ns={"fuel/remaining": remaining, "pump/flow_rate": flow}, dt=dt,
         t=k * 0.1)
    new_flow = sim_pump(damaged, nxt)
    case(f"triad-pump-{seq:03d}", "triad_sweep", PUMP_EXPR, new_flow,
         ns={"pump/damaged": ("on" if damaged else "off"),
             "fuel/remaining": nxt})
    ready = nxt >= 40.0 and charge >= 60.0
    case(f"triad-ready-{seq:03d}", "triad_sweep", READY_EXPR, ready,
         ns={"fuel/remaining": nxt, "ship/charge": charge})
    remaining, flow = nxt, new_flow
    seq += 1

# threshold-exact edges of the readiness AND
case("triad-edge-001", "triad_sweep", READY_EXPR, True,
     ns={"fuel/remaining": 40.0, "ship/charge": 60.0})
case("triad-edge-002", "triad_sweep", READY_EXPR, False,
     ns={"fuel/remaining": 39.999999999999996, "ship/charge": 100.0})
case("triad-edge-003", "triad_sweep", READY_EXPR, False,
     ns={"fuel/remaining": 100.0})               # charge unset -> 0.0
case("triad-edge-004", "triad_sweep", PUMP_EXPR, 2.5,
     ns={"pump/damaged": True, "fuel/remaining": 100.0})  # bool publish
case("triad-edge-005", "triad_sweep", PUMP_EXPR, 0.0,
     ns={"pump/damaged": "off", "fuel/remaining": 0.0})   # dry tank
case("triad-edge-006", "triad_sweep", TANK_EXPR, 100.0,
     ns={"fuel/remaining": 100.0}, dt=0.1)       # flow unset -> no drain


# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Totality on non-finite inputs (Pass-2b falsification finding).
# A `1e400` literal overflows to +inf.  The reference formerly RAISED on
# round/floor/ceil/fmod of inf, breaking its value|error contract; it now
# returns IEEE results, matching the C++ (which was already total).  Only
# DETERMINISTIC (inf/finite) results are pinned here — NaN-producing cases
# (e.g. `1e400 % 2`) are deliberately NOT pinned: NaN bit-parity on the
# ESP32 soft-float is unverified ([CXC-DSL-SEMANTICS] change record).
# ---------------------------------------------------------------------------
case("total-001", "totality", "round(1e400)", math.inf)
case("total-002", "totality", "floor(1e400)", math.inf)
case("total-003", "totality", "ceil(-1e400)", -math.inf)
case("total-004", "totality", "abs(-1e400)", math.inf)
case("total-005", "totality", "max(1e400, 5)", math.inf)
case("total-006", "totality", "min(1e400, 5)", 5.0)
case("total-007", "totality", "2 % 1e400", 2.0)      # fmod(2, inf) = 2
case("total-008", "totality", "1e400 / 2", math.inf)


def main() -> None:
    corpus = {
        "format": "composeux-dsl-conformance-v1",
        "spec": "[SPEC:CXC-DSL] [CXC-DSL-CROSSPLAT]",
        "generator": "python/scripts/generate_dsl_corpus.py",
        "notes": "Byte-contract for every DSL backend. Numeric expected "
                 "values are authoritative as IEEE-754 big-endian hex "
                 "bits ('hex'); 'approx' is informational. See "
                 "dsl_conformance/README.md for the case schema and the "
                 "pinned semantics (round half-to-even, truncated %, "
                 "guarded /0, operand-returning and/or, canon equality).",
        "case_count": len(CASES),
        "cases": CASES,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(corpus, f, indent=1, sort_keys=False, ensure_ascii=True)
        f.write("\n")
    groups = {}
    for c in CASES:
        groups[c["group"]] = groups.get(c["group"], 0) + 1
    print(f"wrote {len(CASES)} cases -> {os.path.normpath(OUT)}")
    for g, n in sorted(groups.items()):
        print(f"  {g:18s} {n}")


if __name__ == "__main__":
    sys.exit(main())
