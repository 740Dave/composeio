"""DOM-V5/V6/V7/V8/V10 + the machine slice of DOM-V1 + DOM-V2/V4 — the DOM
backend's browser-runtime machine tests, driven through a dependency-free DOM
shim by a Node harness (js/run_dom_tests.js).

⛔ These are the MACHINE tier only.  Per [CXC-VISION-EPISTEMICS] and the task's
verify-human GLASS gate, machine-green here is NOT glass-green: the shim has no
layout engine, so real text metrics, the axe-core accessibility audit, and the
screen-reader pass are GLASS items verified in a real browser (see the DOM
backend README's "Glass items" and the pair glass test).  This harness proves
the element-map, publish-contract, form-buffer, and binding-precedence LOGIC —
not the rendered, interactive experience.

Skips (never fails) when Node is unavailable.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_HARNESS = os.path.join(_HERE, "js", "run_dom_tests.js")
_NODE = shutil.which("node")


@pytest.mark.skipif(_NODE is None, reason="node not installed")
def test_dom_backend_machine_suite():
    proc = subprocess.run([_NODE, _HARNESS], capture_output=True, text=True, timeout=120)
    # the harness prints its JSON summary last; stderr may carry console.warn.
    assert proc.stdout, f"no harness output; stderr: {proc.stderr}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["failures"] == [], (
        f"{len(result['failures'])} DOM machine failures: {result['failures']}")
    assert result["passed"] == result["total"]
    assert result["total"] >= 15   # guard against a silently truncated harness
