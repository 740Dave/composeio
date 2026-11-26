"""DOM-V13 — the JS binding-expression evaluator conformance-matches the
Python golden reference ([CXC-DOM-REUSE-REIMPLEMENT-RUNTIME],
[DD-DOM-DSL-EVAL-REUSE]).

The SHARED corpus (render/engine/dsl_conformance/dsl_corpus.json) is the
one-measurement-path byte-contract already gating the Python reference and the
C++/CYD tier.  This test runs the SAME corpus through the JS evaluator
(render/dom/static/dsl.js) via a Node harness that matches numbers by exact
IEEE-754 bits, and — belt-and-suspenders — reruns the Python reference adapter
so a corpus edit can never make the two tiers silently disagree.

Skips (never fails) when Node is unavailable, so the pure-Python CI lane is
unaffected; the JS tier is proven wherever Node is present.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_PY = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))  # python/
_CORPUS = os.path.join(_REPO_PY, "composeio", "render", "engine",
                       "dsl_conformance", "dsl_corpus.json")
_DSL_JS = os.path.join(_REPO_PY, "composeio", "render", "dom", "static", "dsl.js")
_RUNNER = os.path.join(_HERE, "js", "run_dsl_conformance.js")

_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node not installed")


@_needs_node
def test_js_evaluator_passes_full_corpus():
    proc = subprocess.run(
        [_NODE, _RUNNER, _CORPUS, _DSL_JS],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    result = json.loads(proc.stdout)
    assert result["failures"] == [], (
        f"{len(result['failures'])} JS conformance failures "
        f"(first 10): {result['failures'][:10]}")
    assert result["passed"] == result["total"]
    # the corpus is non-trivial — guard against an empty/short corpus quietly
    # passing (the [DD-DOM-DSL-EVAL-REUSE] proof obligation must be real).
    assert result["total"] >= 200


def test_python_reference_still_passes_same_corpus():
    """The other side of the parity claim: the SAME corpus, the Python
    reference adapter — so a corpus edit that breaks one tier is caught here
    even without Node."""
    from composeio.render.engine.dsl_conformance import runner
    total, failures = runner.run_corpus(runner.reference_adapter, _CORPUS)
    assert failures == [], f"python reference regressed: {failures[:10]}"
    assert total >= 200
