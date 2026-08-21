"""
Conformance-corpus runner ([SPEC:CXC-DSL] [CXC-DSL-CROSSPLAT]).

The corpus (dsl_corpus.json) is the byte-contract for EVERY DSL backend:
the Python reference (this repo), the C++/CYD interpreter (Pass 2b) and
the JS/browser tier.  This runner is the reference harness — backends in
other languages re-implement this exact loop against the same JSON (the
file format is documented in README.md and contains nothing
Python-specific).

Adapter contract: a backend exposes ONE callable

    adapter(expr: str, ns: dict, vars: dict, dt: float, t: float)
        -> ("value", result)
         | ("parse_error", message)
         | ("eval_error", message)

Result matching:
    number  -> compared by IEEE-754 BITS against expected 'hex'
               (big-endian; the 'approx' field is informational only)
    bool    -> exact type + value (a 1.0 does NOT match true)
    string  -> exact value
"""

from __future__ import annotations

import json
import math
import os
import struct
from typing import Any, Callable, Dict, List, Tuple

CORPUS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "dsl_corpus.json")


def load_corpus(path: str = CORPUS_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        corpus = json.load(f)
    assert corpus.get("format") == "composeux-dsl-conformance-v1", \
        "unknown corpus format"
    return corpus


def _float_bits_hex(v: float) -> str:
    return struct.pack(">d", float(v)).hex()


def match_expected(expected: dict, value: Any) -> Tuple[bool, str]:
    """(ok, detail).  Numbers are matched by exact IEEE-754 bits."""
    etype = expected["type"]
    if etype == "bool":
        if not isinstance(value, bool):
            return False, f"expected bool, got {type(value).__name__} " \
                          f"{value!r}"
        ok = value is expected["value"] or value == expected["value"]
        return ok, f"expected {expected['value']}, got {value}"
    if etype == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, f"expected number, got {type(value).__name__} " \
                          f"{value!r}"
        got_hex = _float_bits_hex(value)
        return got_hex == expected["hex"], \
            f"expected bits {expected['hex']} ({expected.get('approx')}), " \
            f"got {got_hex} ({value!r})"
    if etype == "string":
        if not isinstance(value, str):
            return False, f"expected string, got {type(value).__name__} " \
                          f"{value!r}"
        return value == expected["value"], \
            f"expected {expected['value']!r}, got {value!r}"
    return False, f"unknown expected type {etype!r}"


def run_case(adapter: Callable, case: dict) -> Tuple[bool, str]:
    """Run one case through *adapter*.  Returns (passed, detail)."""
    kind, payload = adapter(case["expr"], case.get("ns", {}),
                            case.get("vars", {}),
                            float(case.get("dt", 0.0)),
                            float(case.get("t", 0.0)))
    if "error" in case:
        want = case["error"] + "_error"
        if kind == want:
            return True, ""
        return False, f"expected {want}, got {kind} {payload!r}"
    if kind != "value":
        return False, f"expected a value, got {kind}: {payload}"
    return match_expected(case["expected"], payload)


def run_corpus(adapter: Callable,
               path: str = CORPUS_PATH) -> Tuple[int, List[str]]:
    """Run the whole corpus.  Returns (case_count, failures)."""
    corpus = load_corpus(path)
    failures: List[str] = []
    for case in corpus["cases"]:
        try:
            ok, detail = run_case(adapter, case)
        except Exception as e:                      # a crash is a failure
            ok, detail = False, f"adapter raised {type(e).__name__}: {e}"
        if not ok:
            failures.append(f"{case['id']} [{case['group']}] "
                            f"{case['expr']!r}: {detail}")
    return len(corpus["cases"]), failures


# ---------------------------------------------------------------------------
# The Python REFERENCE adapter
# ---------------------------------------------------------------------------

def reference_adapter(expr: str, ns: Dict[str, Any], vars: Dict[str, Any],
                      dt: float, t: float) -> Tuple[str, Any]:
    from composeio.render.engine import dsl
    try:
        compiled = dsl.parse(expr)
    except dsl.DslParseError as e:
        return ("parse_error", str(e))
    try:
        value = compiled.evaluate(
            dsl.EvalContext(ns=ns, vars=vars, dt=dt, t=t))
    except dsl.DslEvalError as e:
        return ("eval_error", str(e))
    return ("value", value)


def main() -> int:
    total, failures = run_corpus(reference_adapter)
    for f in failures:
        print("FAIL", f)
    print(f"{total - len(failures)}/{total} conformance cases pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
