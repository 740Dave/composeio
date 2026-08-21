"""Tests for `composeio.decision_ops.graph_home` — the guard that makes it
STRUCTURALLY IMPOSSIBLE to file a decision or defect record into a linked
worktree's copy of a graph, and the receipt that makes every write self-proving.

WHY THIS TEST LIVES IN python/tests/render/ : that is the tree the pre-commit gate
maps (`python/*.py -> render suite`), so the gate actually runs these. A test the
gate never runs is exactly the vacuity the 6E7171 fix exists to prevent.

THE DEFECT (2026-08-08, measured TWICE in one session):

    _REPO_ROOT = str(Path(__file__).resolve().parents[3])   # defect_ops/mcp_server.py:64

derived the registry's home from the MODULE'S OWN FILE LOCATION. Inside a linked
worktree that is the worktree's COPY of the module, so `<repo>/defects/defect-graph.lts`
resolved to the WORKTREE's copy. Two defects filed by dispatched subagents —
DEFECT-UART-FRAME-MESSAGE-TYPE-DOES-D62D7F and
DEFECT-DOMAIN-RELATIONSHIP-VERBS-DISCARDED-SILENTLY-D542D5 — landed on a graph that
was live on NO branch. Both agents honestly reported "filed"; both had to be
excavated from unlanded commits and re-filed by hand. Main's graph was 747 lines,
one worktree's 653.

Same root cause as `.githooks/pre-commit` deriving REPO_ROOT from BASH_SOURCE
(061503b) and `scripts/pre-commit-tests.sh` reading only `--cached` (7cbfd10):
CONTEXT DERIVED FROM WHERE THE CODE LIVES, INSTEAD OF WHAT THE OPERATION IS ABOUT.

WHAT IS PINNED, in priority order:
  1. THE ACTUAL REGRESSION — a REAL `git worktree add` fixture; resolution started
     from a file inside the LINKED worktree must yield the MAIN checkout. Real git,
     no mocks: `--git-common-dir` is the whole mechanism, and a mock of it would
     pin the mock rather than git.
  2. AN EXPLICIT WORKTREE-INTERNAL PATH RAISES, naming the canonical path — driven
     END TO END through the real CLI, from inside the fixture worktree.
  3. THE RECEIPT CARRIES THE ABSOLUTE RESOLVED PATH, so "filed the defect" is a
     verifiable fact and not a claim.
  4. A NORMAL CHECKOUT IS UNCHANGED — writes land, receipt says canonical.
  5. THE ESCAPE HATCH WORKS, so a hermetic test can use a scratch graph WITHOUT
     weakening the guard. Every test here that needs a worktree-internal write uses
     the hatch; none of them relaxes the guard itself.

CRITICAL, LEARNED THE HARD WAY: git EXPORTS GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE
into hooks and subprocesses, and those BEAT `git -C <dir>` — `-C` changes the cwd, it
does not repoint discovery. A fixture built without scrubbing GIT_* operates on the
REAL repository; that exact mistake wrote five throwaway commits onto real HEAD on
2026-08-08. Every subprocess below runs with GIT_* stripped and
GIT_CEILING_DIRECTORIES pinned to the tmpdir, per
`python/tests/render/test_precommit_gate_range.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))   # <repo>/python

if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.decision_ops.graph_home import (  # noqa: E402
    ALLOW_WORKTREE_ENV,
    WorktreeGraphRefused,
    canonical_equivalent,
    canonical_graph_path,
    canonical_repo_root,
    containing_worktree_root,
    graph_receipt,
    guard_graph_write,
    is_in_linked_worktree,
    repo_context,
)

# git invoked with every ambient influence pinned off. The user's global
# core.hooksPath points at THIS repo's .githooks, so an unguarded commit in the
# fixture would re-enter the real gate.
_GIT_ISOLATION = [
    "-c", "user.name=worktree guard test",
    "-c", "user.email=guard@test.invalid",
    "-c", "commit.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "init.defaultBranch=main",
]

MIN_GRAPH = """\
---
scope: "Synthetic fixture defect corpus for the worktree-guard tests. Never the real graph."
domain: defect
confidentiality: internal-only
artifact-readiness: system-design
---
# Fixture Defect Corpus [SYSTEM:FIXTURE-DEFECTS]

> Synthetic fixture. Written only by tests, read by nobody.
"""


def _clean_env(extra=None):
    """Environment with EVERY GIT_* pointer stripped.

    NOT paranoia — earned. See the module docstring: an unscrubbed fixture writes
    to the REAL repository, because exported GIT_DIR beats `git -C`.
    GIT_CEILING_DIRECTORIES additionally stops upward discovery from escaping the
    tmpdir if a `-C` is ever dropped."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = tempfile.gettempdir()
    env.pop(ALLOW_WORKTREE_ENV, None)
    if extra:
        env.update(extra)
    return env


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", repo, *_GIT_ISOLATION, *args],
        capture_output=True, text=True, check=check, env=_clean_env(),
    )


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _make_repo(root):
    """A real git repo shaped like composeio: a graph at defects/defect-graph.lts
    and a stand-in module at the same depth defect_ops/mcp_server.py sits at."""
    os.makedirs(root, exist_ok=True)
    _git(root, "init", "-q")
    _write(os.path.join(root, "defects", "defect-graph.lts"), MIN_GRAPH)
    # Same depth as python/composeio/defect_ops/mcp_server.py — the file whose
    # location USED to decide where the registry lived.
    _write(os.path.join(root, "python", "composeio", "defect_ops", "mcp_server.py"),
           "# stand-in for the module whose __file__ used to decide the repo root\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


@pytest.fixture()
def wt_fixture(tmp_path):
    """A REAL main checkout + a REAL linked worktree (`git worktree add`).

    The whole mechanism under test is `git rev-parse --git-common-dir`; mocking it
    would pin the mock. So this drives actual git."""
    main = str(tmp_path / "main")
    wt = str(tmp_path / "wt-agent-fixture")
    _make_repo(main)
    _git(main, "worktree", "add", "-q", "-b", "fixture-branch", wt)
    return {"main": main, "wt": wt}


def _real(p):
    """macOS tmpdirs are symlinks (/var -> /private/var); git answers with the
    resolved path, so every comparison here must realpath both sides."""
    return os.path.realpath(str(p))


# --------------------------------------------------------------------------- #
# 1. THE ACTUAL REGRESSION — resolution from inside a linked worktree
# --------------------------------------------------------------------------- #
def test_canonical_root_from_inside_linked_worktree_is_the_main_checkout(wt_fixture):
    """`--git-common-dir` un-does the worktree from the inside. This is the single
    assertion that would have caught the two lost defect records."""
    wt_module = os.path.join(wt_fixture["wt"], "python", "composeio",
                             "defect_ops", "mcp_server.py")
    assert os.path.isfile(wt_module), "fixture worktree should carry its own copy"

    root = canonical_repo_root(wt_module)
    assert root is not None
    assert _real(root) == _real(wt_fixture["main"]), (
        "resolution started from the WORKTREE's copy of the module must yield the "
        "MAIN checkout — this is exactly the derivation that lost two defect records")

    # ...and the naive parents[3] form, which is what shipped, yields the WORKTREE.
    # Pinned so the regression is visible as a contrast, not just an assertion.
    from pathlib import Path
    naive = Path(wt_module).resolve().parents[3]
    assert _real(naive) == _real(wt_fixture["wt"])
    assert _real(naive) != _real(root)


def test_default_graph_path_from_inside_worktree_resolves_to_canonical_graph(wt_fixture):
    """The DEFAULT registry path, resolved from a module living in the worktree,
    is the MAIN checkout's graph."""
    wt_module = os.path.join(wt_fixture["wt"], "python", "composeio",
                             "defect_ops", "mcp_server.py")
    resolved = canonical_graph_path("defects/defect-graph.lts", start=wt_module)
    assert resolved is not None
    assert _real(resolved) == _real(os.path.join(wt_fixture["main"], "defects",
                                                 "defect-graph.lts"))


def test_worktree_detection_and_canonical_equivalent(wt_fixture):
    wt_graph = os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts")
    main_graph = os.path.join(wt_fixture["main"], "defects", "defect-graph.lts")

    assert is_in_linked_worktree(wt_graph) is True
    assert is_in_linked_worktree(main_graph) is False

    ctx = repo_context(wt_graph)
    assert ctx.in_repo and ctx.in_linked_worktree
    assert _real(ctx.worktree_root) == _real(wt_fixture["wt"])
    assert _real(ctx.canonical_root) == _real(wt_fixture["main"])

    assert _real(containing_worktree_root(wt_graph)) == _real(wt_fixture["wt"])
    assert _real(canonical_equivalent(wt_graph)) == _real(main_graph)


# --------------------------------------------------------------------------- #
# 2. AN EXPLICIT WORKTREE-INTERNAL PATH RAISES, naming the canonical path
# --------------------------------------------------------------------------- #
def test_guard_raises_on_worktree_internal_path_and_names_canonical(wt_fixture):
    wt_graph = os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts")
    main_graph = os.path.join(wt_fixture["main"], "defects", "defect-graph.lts")

    with pytest.raises(WorktreeGraphRefused) as exc:
        guard_graph_write(wt_graph)

    msg = str(exc.value)
    # The canonical path must be IN THE MESSAGE — a refusal that does not say where
    # to go instead just moves the dig, it does not end it.
    assert _real(main_graph) in msg or str(main_graph) in msg
    assert str(wt_graph) in msg or _real(wt_graph) in msg
    assert "LINKED GIT WORKTREE" in msg
    # ...and as DATA, so a caller can retry without re-parsing prose.
    assert _real(exc.value.canonical_path) == _real(main_graph)
    assert _real(exc.value.given_path) == _real(wt_graph)


def test_core_write_refuses_worktree_graph_before_touching_the_file(wt_fixture):
    """The guard sits on `_apply_with_guard`, the ONE write choke point BOTH
    packages share — so CLI, MCP dispatch and a direct core import are all covered
    by one gate. It fires BEFORE the read, so even a would-be no-op refuses."""
    from composeio.defect_ops.core import report

    wt_graph = os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts")
    before = open(wt_graph, "rb").read()

    with pytest.raises(WorktreeGraphRefused):
        report(wt_graph, symptom="a symptom that must never be written here")

    assert open(wt_graph, "rb").read() == before, "refused write must not touch bytes"


def test_decision_ops_core_write_is_guarded_too(wt_fixture):
    """decision_ops had the SAME class of defect (dsd_review/graph_projection root
    derivation) and its CLI calls the core DIRECTLY, bypassing the MCP layer — so a
    guard on the server alone would have left it wide open."""
    from composeio.decision_ops.core import add

    wt_graph = os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts")
    with pytest.raises(WorktreeGraphRefused):
        add(wt_graph, {"id": "DEC-MUST-NOT-LAND", "title": "must not land",
                       "lifecycle-position": "raised",
                       "decided-in-session": "2026-08-08",
                       "importance": "low", "rationale": "must not land"})


def test_cli_end_to_end_from_inside_worktree_refuses(wt_fixture):
    """END TO END through the real CLI, launched from INSIDE the fixture worktree —
    the exact shape of the invocation that lost the two records."""
    proc = subprocess.run(
        [sys.executable, "-m", "composeio.defect_ops", "report",
         os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts"),
         "--symptom", "a throwaway symptom that must never be written"],
        capture_output=True, text=True, cwd=wt_fixture["wt"],
        env=_clean_env({"PYTHONPATH": PY_ROOT}),
    )
    assert proc.returncode != 0, f"expected refusal, got:\n{proc.stdout}\n{proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["success"] is False
    assert payload["refused_worktree_graph"] is True
    assert _real(payload["canonical_path"]) == _real(
        os.path.join(wt_fixture["main"], "defects", "defect-graph.lts"))


# --------------------------------------------------------------------------- #
# 3. THE RECEIPT — the absolute resolved path travels with every write
# --------------------------------------------------------------------------- #
def test_receipt_carries_absolute_resolved_path_on_a_real_write(wt_fixture):
    """A write to the MAIN checkout's graph returns WHERE it landed, absolute and
    resolved — the fact that turns "filed the defect" from a claim into a receipt."""
    from composeio.defect_ops.core import report

    main_graph = os.path.join(wt_fixture["main"], "defects", "defect-graph.lts")
    res = report(main_graph, symptom="a fixture symptom for the receipt test")
    out = res.to_dict()

    assert out["changed"] is True
    assert "graph_path" in out and "graph_is_canonical" in out
    assert os.path.isabs(out["graph_path"])
    assert _real(out["graph_path"]) == _real(main_graph)
    assert out["graph_is_canonical"] is True
    # additive only — nothing that existed before moved or changed shape
    for key in ("ok", "defect_id", "changed", "message", "node_id",
                "lint_clean", "lint_summary"):
        assert key in out


def test_receipt_rides_the_mcp_dispatch_payload(wt_fixture):
    """The receipt must reach the MCP callers (report / attempt / resolve), because
    that is the surface a dispatched Intelligence actually reports from."""
    from composeio.defect_ops.mcp_server import DefectOpsMcp

    main_graph = os.path.join(wt_fixture["main"], "defects", "defect-graph.lts")
    mcp = DefectOpsMcp(default_graph=main_graph,
                       refresher=_NullRefresher(), record_investigation=lambda **k: 1)

    sym = "a fixture symptom driven through the dispatch seam"
    for tool, args in (("defect_report", {"symptom": sym}),
                       ("defect_attempt", {"symptom": sym, "outcome": "held"}),
                       ("defect_resolve", {"symptom": sym, "status": "verified"})):
        out = mcp.dispatch(tool, args)
        assert out["success"] is True, out
        assert _real(out["graph_path"]) == _real(main_graph), tool
        assert out["graph_is_canonical"] is True, tool

    q = mcp.dispatch("defect_query", {"symptom": sym})
    assert _real(q["graph_path"]) == _real(main_graph)


def test_decision_ops_receipt_carries_the_path(tmp_path):
    """decision_add / decision_update / decision_resolve gained the same receipt."""
    from composeio.decision_ops.core import add, resolve, update

    graph = tmp_path / "fixture-decisions.lts"
    graph.write_text(
        "---\ndomain: PLT.CUX\nconfidentiality: internal-only\n"
        "artifact-readiness: system-design\ncreated: 2026-08-08\n---\n"
        "# Fixture [SPEC:FIXTURE-WTGUARD]\n\n"
        "### [DEC-WTGUARD-ROOT] A root fixture decision\n"
        "- lifecycle-position: committed\n- decided-in-session: 2026-08-08\n"
        "- importance: low\n- rationale: exists so nothing here starts as a dangle.\n",
        encoding="utf-8")

    r = add(str(graph), {"id": "DEC-WTGUARD-NEW", "title": "a new fixture decision",
                         "lifecycle-position": "raised",
                         "decided-in-session": "2026-08-08",
                         "importance": "low", "rationale": "receipt test."})
    assert _real(r.to_dict()["graph_path"]) == _real(graph)
    u = update(str(graph), "DEC-WTGUARD-NEW", {"lifecycle-position": "decided"})
    assert _real(u.to_dict()["graph_path"]) == _real(graph)
    v = resolve(str(graph), "DEC-WTGUARD-NEW", status="resolved")
    assert _real(v.to_dict()["graph_path"]) == _real(graph)


def test_receipt_is_honest_about_a_non_repo_scratch_graph(tmp_path):
    """A tmpdir graph is in no repo at all. `graph_is_canonical` answers None —
    honest "not applicable", never a reassuring True."""
    scratch = tmp_path / "scratch.lts"
    scratch.write_text(MIN_GRAPH, encoding="utf-8")
    receipt = graph_receipt(str(scratch))
    assert _real(receipt["graph_path"]) == _real(scratch)
    assert receipt["graph_is_canonical"] is None


# --------------------------------------------------------------------------- #
# 4. A NORMAL CHECKOUT IS UNCHANGED
# --------------------------------------------------------------------------- #
def test_normal_checkout_behaviour_is_unchanged(tmp_path):
    """No worktree anywhere: the guard is transparent, the write lands, and the
    receipt reports canonical."""
    from composeio.defect_ops.core import report

    main = _make_repo(str(tmp_path / "plain"))
    graph = os.path.join(main, "defects", "defect-graph.lts")

    assert is_in_linked_worktree(graph) is False
    assert guard_graph_write(graph) == _real(graph)
    assert _real(canonical_repo_root(graph)) == _real(main)

    res = report(graph, symptom="a symptom filed in a normal checkout")
    assert res.changed is True
    assert res.graph_is_canonical is True
    assert "a symptom filed in a normal checkout" in open(graph, encoding="utf-8").read()


def test_cli_end_to_end_in_a_normal_checkout_writes_and_reports_the_path(tmp_path):
    main = _make_repo(str(tmp_path / "plain"))
    graph = os.path.join(main, "defects", "defect-graph.lts")
    proc = subprocess.run(
        [sys.executable, "-m", "composeio.defect_ops", "report", graph,
         "--symptom", "a normal-checkout CLI symptom"],
        capture_output=True, text=True, cwd=main,
        env=_clean_env({"PYTHONPATH": PY_ROOT}),
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert _real(payload["graph_path"]) == _real(graph)
    assert payload["graph_is_canonical"] is True


# --------------------------------------------------------------------------- #
# 5. THE ESCAPE HATCH — explicit, never a weakening of the guard
# --------------------------------------------------------------------------- #
def test_escape_hatch_allows_a_deliberate_worktree_scratch_graph(wt_fixture, monkeypatch):
    wt_graph = os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts")

    # kwarg form
    assert guard_graph_write(wt_graph, allow_worktree=True) == _real(wt_graph)

    # env form — what a hermetic test would set
    monkeypatch.setenv(ALLOW_WORKTREE_ENV, "1")
    assert guard_graph_write(wt_graph) == _real(wt_graph)

    from composeio.defect_ops.core import report
    res = report(wt_graph, symptom="a deliberate scratch symptom")
    assert res.changed is True
    # ...and the receipt tells the truth about it: NOT canonical.
    assert res.graph_is_canonical is False


def test_escape_hatch_is_off_by_default(wt_fixture, monkeypatch):
    monkeypatch.delenv(ALLOW_WORKTREE_ENV, raising=False)
    with pytest.raises(WorktreeGraphRefused):
        guard_graph_write(os.path.join(wt_fixture["wt"], "defects", "defect-graph.lts"))


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _NullRefresher:
    """Stand-in LtsRefresher: the engine refresh is orthogonal to path resolution,
    and building a real corpus partition per test would make these slow without
    testing anything more."""

    def refresh(self, graph, area=None):
        return _NullRefresh()

    def query(self, **kwargs):
        return {"success": True}


class _NullRefresh:
    def to_dict(self):
        return {"ok": True, "refreshed": False, "fresh": True}
