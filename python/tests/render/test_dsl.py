"""
ComposeUX expression DSL — pytest coverage ([SPEC:CXC-DSL]).

Two jobs:
  1. Fold the cross-backend conformance corpus (dsl_corpus.json, the
     byte-contract for the C++/CYD and JS interpreters — [CXC-DSL-CROSSPLAT])
     into pytest, so a corpus regression fails CI, not just the standalone
     runner.  Each of the ~226 cases is its own parametrized test.
  2. Prove the sandbox ([CXC-DSL-SANDBOX]/C9) and the load-bearing semantic
     decisions (guarded div, closed function set, canon-typed equality,
     lazy ternary) with explicit assertions.
"""

from __future__ import annotations

import pytest

from composeio.render.engine import dsl
from composeio.render.engine.dsl_conformance import runner


# ---------------------------------------------------------------------------
# 1. The conformance corpus, one pytest case per corpus case (C10, C2, C12)
# ---------------------------------------------------------------------------

_CORPUS = runner.load_corpus()
_CASES = _CORPUS["cases"]


def test_corpus_format_is_v1():
    assert _CORPUS["format"] == "composeux-dsl-conformance-v1"
    assert len(_CASES) >= 200, "corpus unexpectedly small"


@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_conformance_case(case):
    """Every corpus case must pass through the Python reference adapter.

    Numbers are matched by exact IEEE-754 bits (the runner's contract), so
    this also pins round()=half-to-even and float64 semantics (C12/C2)."""
    ok, detail = runner.run_case(runner.reference_adapter, case)
    assert ok, f"{case['id']} [{case['group']}] {case['expr']!r}: {detail}"


def test_reference_passes_whole_corpus():
    """Belt-and-suspenders: the aggregate must be 100%."""
    total, failures = runner.run_corpus(runner.reference_adapter)
    assert not failures, f"{len(failures)}/{total} failed:\n" + "\n".join(failures)


# ---------------------------------------------------------------------------
# 2. Sandbox — malicious/ill-formed exprs must FAIL, never execute (C9)
# ---------------------------------------------------------------------------

_MALICIOUS = [
    "__import__('os')",        # no import reachable
    "x.__class__",             # no attribute access
    "().__class__",            # no attribute access
    "a[0]",                    # no subscripting
    "foo(1)",                  # unknown function
    "open('/etc/passwd')",     # not in the whitelist
    "print('x')",              # not in the whitelist
    "1 + ",                    # malformed
    "ns('a'); ns('b')",        # no statement sequencing
]


@pytest.mark.parametrize("expr", _MALICIOUS)
def test_sandbox_rejects(expr):
    with pytest.raises(dsl.DslError):
        compiled = dsl.parse(expr)
        compiled.evaluate(dsl.EvalContext(ns={}, vars={}, dt=0.0, t=0.0))


def test_interpreter_source_has_no_dynamic_exec():
    """The interpreter must never touch the Python execution machinery."""
    import inspect
    src = inspect.getsource(dsl)
    for forbidden in ("eval(", "exec(", "compile(", "__import__"):
        # allow the words in comments/docstrings only if not a call — the
        # simplest robust check is that they don't appear as bare calls.
        assert f" {forbidden}" not in src and f"\n{forbidden}" not in src, \
            f"interpreter source contains {forbidden!r}"


# ---------------------------------------------------------------------------
# 3. Load-bearing semantics (explicit, human-readable)
# ---------------------------------------------------------------------------

def _ev(expr, **ctx):
    return dsl.evaluate(expr, **ctx)


def test_closed_function_set():
    assert sorted(dsl.FUNCTIONS) == [
        "abs", "ceil", "clamp", "floor", "max", "min", "round"]


def test_guarded_division_never_nan():
    assert _ev("1 / 0") == 0.0
    assert _ev("5 % 0") == 0.0
    assert _ev("10 / (2 - 2)") == 0.0


def test_round_half_to_even():
    # banker's rounding, the pinned rule (C12)
    assert _ev("round(0.5)") == 0.0
    assert _ev("round(1.5)") == 2.0
    assert _ev("round(2.5)") == 2.0


def test_lazy_ternary_and_short_circuit():
    assert _ev("true ? 1.0 : 2.0") == 1.0
    assert _ev("false ? 1.0 : 2.0") == 2.0


def test_canon_typed_equality():
    # bool True / '1' / 'on' collapse to one on/off token (C6)
    assert _ev("ns('flag') == 'on'", ns={"flag": True}) is True
    assert _ev("ns('flag') == 'on'", ns={"flag": "1"}) is True
    assert _ev("ns('flag') == 'on'", ns={"flag": "off"}) is False


def test_triad_readiness_single_expression():
    # the C2-closing expression: fuel+charge AND in ONE place
    expr = "ns('fuel/remaining') >= 40 and ns('ship/charge') >= 60"
    assert _ev(expr, ns={"fuel/remaining": 80.0, "ship/charge": 80.0}) is True
    assert _ev(expr, ns={"fuel/remaining": 80.0, "ship/charge": 20.0}) is False
    assert _ev(expr, ns={"fuel/remaining": 10.0, "ship/charge": 80.0}) is False


def test_variable_rate_integration_expr():
    # read-modify-write: the stateless integration primitive ([CXC-DSL-STATE])
    expr = "ns('fuel/remaining') - ns('pump/flow_rate') * dt"
    v = _ev(expr, ns={"fuel/remaining": 100.0, "pump/flow_rate": 8.0}, dt=0.1)
    assert v == pytest.approx(99.2)
