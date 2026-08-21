"""Tests for THE `.mcp.json` GATE itself (`python/tests/conftest.py`).

WHY THIS FILE EXISTS (2026-08-13). The gate converts ~20 FAILURES into ~20 SKIPS when the
gitignored `.mcp.json` is absent — which is the permanent condition of every `git worktree`.
That is the right conversion, and it is also, structurally, the exact move by which a suite goes
quietly hollow: "green" now costs nothing if the skip set can grow unwatched.

So the gate is held to three properties, and they are tested HERE rather than assumed:

  1. IT DECIDES FROM THE FILESYSTEM, through the SAME path function as the code under test —
     not from a hand-copied path that can drift.
  2. IT COUNTS, IN BOTH DIRECTIONS. Absent -> "N SKIPPED"; present -> "INERT, N RAN". A reader
     of the output never has to infer which condition the run was in.
  3. IT IS BOUNDED and NON-VACUOUS. There is a declared ceiling on the gated set, and there is
     at least one test actually wearing the mark (a gate nothing opts into is decoration that
     reads like protection).

⚠️ NOTHING HERE MAY CARRY THE MARK. These tests must run in BOTH conditions — a gate whose own
tests are gated by the gate proves nothing at all.
"""

from __future__ import annotations

import importlib.util
import os
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))       # <repo>/python/tests
PY_ROOT = os.path.dirname(TESTS_ROOT)                             # <repo>/python
REPO_ROOT = os.path.dirname(PY_ROOT)                              # <repo>
GATE_PATH = os.path.join(TESTS_ROOT, "conftest.py")


def _gate() -> types.ModuleType:
    """Load `python/tests/conftest.py` by PATH — no package-layout assumptions, and no
    dependence on how pytest happened to import it."""
    spec = importlib.util.spec_from_file_location("_mcp_config_gate_under_test", GATE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Recorder:
    """The minimum of a TerminalReporter the summary hook touches."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def write_line(self, line: str, **kw) -> None:
        self.lines.append(line)


def _fake_config(**state) -> types.SimpleNamespace:
    base = {"marked": 0, "skipped": 0, "present": True, "path": "/nowhere/.mcp.json"}
    base.update(state)
    return types.SimpleNamespace(_mcp_config_gate=base)


# ══ 1. the decision comes from the filesystem, via the code-under-test's own path ══

def test_the_gate_resolves_the_SAME_path_the_code_under_test_reads():
    """If the gate computed the path independently it could skip tests that would have run, or
    run tests that cannot — `COMPOSEIO_MCP_CONFIG` repoints both, or neither."""
    from composeio.ptycore import repo_mcp_config_path
    assert _gate().repo_mcp_config_path() == repo_mcp_config_path()


def test_presence_is_read_from_the_filesystem_not_remembered():
    gate = _gate()
    assert gate.repo_mcp_config_present() == os.path.isfile(gate.repo_mcp_config_path())


def test_the_skip_reason_NAMES_the_file_and_the_remedy():
    """A reader of `-rs` must learn WHICH file is absent and WHAT to do — "skipped" with no
    reason is the same silence in a friendlier font."""
    reason = _gate().skip_reason()
    assert ".mcp.json" in reason
    assert "gitignore" in reason.lower()
    assert "worktree" in reason.lower()
    assert "COMPOSEIO_MCP_CONFIG" in reason


# ══ 2. the LIVE gate's state agrees with the tree it is running in ══

def test_the_live_gate_state_matches_this_run(request):
    """Against the REAL config object of the run in progress — not a fixture, not a mock."""
    st = request.config._mcp_config_gate
    assert st["present"] == os.path.isfile(st["path"])
    if st["present"]:
        assert st["skipped"] == 0, "the file is present; nothing may be skipped for its absence"
    else:
        assert st["skipped"] == st["marked"], (
            "the file is absent, so EVERY gated test must be skipped — a partial skip means "
            "some gated test is still failing for the reason the gate exists to absorb")


def test_this_module_is_NOT_gated(request):
    """The gate's own tests must execute in both conditions."""
    for item in request.session.items:
        if item.nodeid.startswith("tests/render/test_mcp_config_gate.py") or \
                os.path.basename(str(item.fspath)) == "test_mcp_config_gate.py":
            assert item.get_closest_marker(_gate().MARKER) is None, (
                f"{item.nodeid} carries the gate's own marker — it would stop running in "
                f"exactly the condition it exists to check")


# ══ 3. it COUNTS, in BOTH directions ══

def test_the_summary_reports_a_COUNT_when_tests_were_skipped():
    gate = _gate()
    rec = _Recorder()
    gate.pytest_terminal_summary(rec, 0, _fake_config(marked=20, skipped=20, present=False,
                                                      path="/x/.mcp.json"))
    assert len(rec.lines) == 1
    line = rec.lines[0]
    assert "20" in line and "SKIP" in line.upper() and "/x/.mcp.json" in line


def test_the_summary_reports_a_COUNT_when_the_gate_was_INERT():
    """The other direction is the one that is usually forgotten. Without it you cannot tell
    "the gate was inert and 20 tests ran" from "the gate silently ate 20 tests"."""
    gate = _gate()
    rec = _Recorder()
    gate.pytest_terminal_summary(rec, 0, _fake_config(marked=20, skipped=0, present=True,
                                                      path="/x/.mcp.json"))
    assert len(rec.lines) == 1
    line = rec.lines[0]
    assert "20" in line and "INERT" in line.upper() and "RAN" in line.upper()


def test_the_summary_is_never_silent_even_with_nothing_gated():
    gate = _gate()
    for state in (_fake_config(present=True), _fake_config(present=False)):
        rec = _Recorder()
        gate.pytest_terminal_summary(rec, 0, state)
        assert len(rec.lines) == 1 and rec.lines[0].strip(), (
            "a run that says nothing about the gate is a run whose reader must guess")


# ══ 4. bounded, and non-vacuous ══

def test_the_gated_set_has_a_declared_CEILING_above_the_measured_size():
    gate = _gate()
    assert gate.GATED_BUDGET >= gate.MEASURED_GATED_ITEMS
    assert gate.GATED_BUDGET < 100, (
        "a ceiling loose enough to swallow a suite is not a ceiling")


def test_something_actually_WEARS_the_mark():
    """Non-vacuity. A gate with no subscribers passes every check above while protecting
    nothing — and would sail on unnoticed if the marks were lost in a refactor."""
    gate = _gate()
    needle = f"@pytest.mark.{gate.MARKER}"
    hits = 0
    for root, dirs, files in os.walk(TESTS_ROOT):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.startswith("test_") or not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                hits += fh.read().count(needle)
    assert hits >= 10, (
        f"only {hits} use(s) of {needle} found under {TESTS_ROOT}; the measured gated set was "
        f"{gate.MEASURED_GATED_ITEMS} items across three terminal test files. If the marks were "
        f"removed the tests are FAILING again in every worktree; if they were never added the "
        f"gate is decoration.")


def test_the_mark_is_REGISTERED_so_strict_markers_cannot_break_the_gate(request):
    """An unregistered mark warns today and is an ERROR under `--strict-markers`; either way a
    gate that pytest does not recognise is a gate that does not fire."""
    declared = "\n".join(request.config.getini("markers"))
    assert _gate().MARKER in declared
