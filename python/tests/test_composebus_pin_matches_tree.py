"""composeio's declared composebus pin must equal the in-tree python/composebus version.

WHY (2026-11-26). python/pyproject.toml pinned `composebus==0.1.0` while the in-tree kernel
had moved to 0.1.2. 0.1.0 loses messages under concurrent publish (fixed in 0.1.1) and carries
device ids in a source comment (scrubbed in 0.1.2). Nothing failed: the test suite imports the
in-tree kernel from source, so it exercised 0.1.2 while `pip install composeio` from an index
would have resolved 0.1.0. The pin and the tree are two copies of one fact; this test makes
them one.

Both sides are read from the pyproject files, never from an installed distribution, so the
answer does not depend on what happens to be installed in the running interpreter.

compose_plugins pins composebus too; it is checked by the same rule.

The reader is given a negative control (a mismatching and a missing pin must be detected), so
a reader that silently finds nothing cannot pass the main check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

PYTHON_DIR = Path(__file__).resolve().parents[1]
COMPOSEBUS_PYPROJECT = PYTHON_DIR / "composebus" / "pyproject.toml"

# Every in-tree distribution that depends on composebus, by its pyproject path.
DEPENDENTS = {
    "composeio": PYTHON_DIR / "pyproject.toml",
    "compose-plugins": PYTHON_DIR / "compose_plugins" / "pyproject.toml",
}

_REQ = re.compile(r"^\s*composebus\s*(?P<op>==|>=|<=|~=|!=|>|<|===)?\s*(?P<ver>[^\s;,\]]*)",
                  re.IGNORECASE)


def tree_version(text: str) -> str:
    return tomllib.loads(text)["project"]["version"]


def composebus_pins(text: str) -> list[tuple[str, str]]:
    """(operator, version) for every composebus entry in [project].dependencies."""
    deps = tomllib.loads(text)["project"].get("dependencies", [])
    out = []
    for dep in deps:
        m = _REQ.match(dep)
        if m and re.match(r"^\s*composebus\b(?![-_.])", dep, re.IGNORECASE):
            out.append((m.group("op") or "", m.group("ver")))
    return out


def pin_problem(dependent_text: str, kernel_version: str) -> str | None:
    """None when the dependent pins exactly composebus==<kernel_version>, else the reason."""
    pins = composebus_pins(dependent_text)
    if len(pins) != 1:
        return f"expected exactly one composebus dependency, found {len(pins)}: {pins}"
    op, ver = pins[0]
    if (op, ver) != ("==", kernel_version):
        return f"declares composebus{op}{ver}, in-tree python/composebus is {kernel_version}"
    return None


@pytest.mark.parametrize("name", sorted(DEPENDENTS))
def test_composebus_pin_matches_in_tree_version(name):
    kernel = tree_version(COMPOSEBUS_PYPROJECT.read_text(encoding="utf-8"))
    problem = pin_problem(DEPENDENTS[name].read_text(encoding="utf-8"), kernel)
    assert problem is None, f"{name} ({DEPENDENTS[name].relative_to(PYTHON_DIR)}): {problem}"


def _project(deps: list[str]) -> str:
    body = ", ".join(f'"{d}"' for d in deps)
    return f'[project]\nname = "x"\nversion = "1"\ndependencies = [{body}]\n'


@pytest.mark.parametrize("deps, expect_ok", [
    (["pyyaml>=6.0", "composebus==0.1.2"], True),
    (["composebus==0.1.0"], False),            # the defect this file was written for
    (["composebus>=0.1.2"], False),            # a floor is not a pin
    (["composebus"], False),                   # unpinned
    (["pyyaml>=6.0"], False),                  # missing entirely
    (["composebus-extra==0.1.2"], False),      # a different distribution, not a pin
    (["composebus==0.1.2", "composebus==0.1.2"], False),
])
def test_negative_control_reader_detects_drift(deps, expect_ok):
    assert (pin_problem(_project(deps), "0.1.2") is None) is expect_ok
