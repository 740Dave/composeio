"""Smoke + unit coverage for scripts/branch_hygiene.py.

WHY THIS FILE EXISTS (2026-09-02). branch_hygiene.py is hygiene-CRITICAL — it is the
landed-oracle the wake/dusk skills call as the ONLY enforcement surface (there is no
CI) — yet it landed with NO test-suite mapping and shipped uncovered (git-expert flag).
This closes that gap with a proportionate suite: it imports the module and exercises the
two pure-logic axes a `--merged` prune or a stale audit would get wrong —

  * the NEW #2 orphaned-origin-ref sweep (`orphan_origin_refs`): every LIVE origin head
    whose content is landed on origin/main is an ORPHAN the land dance left behind; one
    that is real unlanded work must be LEFT; one whose SHA is unfetched is UNKNOWN, never
    silently 'landed'. The classification, not the git plumbing, is where a false 'landed'
    would cause a wrong prune, so the git calls are stubbed and the DECISION is asserted.
  * the dirty-worktree churn filter (`_is_churn` / `_porcelain_files`): the runtime churn
    a prune-safety scan must NOT mistake for unique work.

No live network / live repo is required — the git surface is monkeypatched — so this runs
in the fast pre-commit gate deterministically.
"""
from __future__ import annotations

import os
import sys

import pytest

# The hygiene scripts are not a package; add scripts/ to the path and import directly.
_SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import branch_hygiene as bh  # noqa: E402


# --------------------------------------------------------------------------------------
# 0. Smoke — the module imports and the new surface exists (the coverage the tree lacked).
# --------------------------------------------------------------------------------------

def test_module_imports_and_exposes_the_orphan_sweep():
    for name in ("orphan_origin_refs", "orphan_origin_refs_cmd", "_ls_remote_heads",
                 "_cherry_all_landed", "_obj_present", "dirty_worktree_findings",
                 "_is_churn"):
        assert hasattr(bh, name), f"branch_hygiene lost {name!r}"


# --------------------------------------------------------------------------------------
# 1. _ls_remote_heads — parses `<sha>\trefs/heads/<name>`, drops non-head refs, and
#    STATES a failure (note != None) rather than reporting an empty 'no orphans'.
# --------------------------------------------------------------------------------------

class _Proc:
    def __init__(self, rc=0, stdout=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = ""


def test_ls_remote_heads_parses_and_filters(monkeypatch):
    payload = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\trefs/heads/main\n"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/heads/feat/x\n"
        "cccccccccccccccccccccccccccccccccccccccc\trefs/tags/v1\n"   # not a head
    )
    monkeypatch.setattr(bh, "git", lambda *a: _Proc(0, payload))
    heads, note = bh._ls_remote_heads("origin")
    assert note is None
    assert heads == {
        "main": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "feat/x": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }


def test_ls_remote_heads_failure_is_stated_not_empty(monkeypatch):
    monkeypatch.setattr(bh, "git", lambda *a: _Proc(128, ""))
    heads, note = bh._ls_remote_heads("origin")
    assert heads == {}
    assert note and "SKIPPED" in note  # a failure must be VISIBLE, never a silent clean


# --------------------------------------------------------------------------------------
# 2. _cherry_all_landed — the patch-id oracle. Empty / all-'-' => landed; any '+' => not;
#    a non-zero git => None (UNKNOWN, never a false 'landed').
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("rc,stdout,expected", [
    (0, "", True),                                  # no unique commits -> pure ancestor
    (0, "- deadbeef\n- cafef00d\n", True),          # all equivalents present
    (0, "- deadbeef\n+ 0badf00d\n", False),         # one commit not on base
    (0, "+ 0badf00d\n", False),
    (1, "", None),                                  # git cherry failed -> indeterminate
])
def test_cherry_all_landed(monkeypatch, rc, stdout, expected):
    monkeypatch.setattr(bh, "git", lambda *a: _Proc(rc, stdout))
    assert bh._cherry_all_landed("origin/main", "sometip") is expected


# --------------------------------------------------------------------------------------
# 3. orphan_origin_refs — the DECISION. Stub the git surface so the classification is what
#    is under test: ancestor -> orphan; patch-id present -> orphan; unique work -> unlanded;
#    unfetched SHA -> unknown; and base/main/HEAD are never themselves orphans.
# --------------------------------------------------------------------------------------

def test_orphan_origin_refs_classifies(monkeypatch):
    heads = {
        "main": "m" * 40,
        "landed-ancestor": "a" * 40,     # ancestor of base       -> orphan
        "landed-rebased": "r" * 40,      # patch-id present        -> orphan
        "real-work": "w" * 40,           # unique commits          -> unlanded (LEAVE)
        "not-fetched": "n" * 40,         # SHA absent locally      -> unknown
    }
    monkeypatch.setattr(bh, "_ls_remote_heads", lambda remote="origin": (heads, None))
    monkeypatch.setattr(bh, "out", lambda *a: ("b" * 40) if a[:1] == ("rev-parse",) else "")
    monkeypatch.setattr(bh, "_obj_present", lambda sha: not sha.startswith("n"))
    monkeypatch.setattr(bh, "is_ancestor", lambda sha, base: sha.startswith("a"))

    def fake_cherry(base, tip):
        if tip.startswith("r"):
            return True          # rebase-ff landed by patch-id
        if tip.startswith("w"):
            return False         # genuinely ahead
        return None
    monkeypatch.setattr(bh, "_cherry_all_landed", fake_cherry)

    orphans, unlanded, unknown, note = bh.orphan_origin_refs("origin/main")
    assert note is None
    assert {o["branch"] for o in orphans} == {"landed-ancestor", "landed-rebased"}
    assert {u["branch"] for u in unlanded} == {"real-work"}
    assert {u["branch"] for u in unknown} == {"not-fetched"}
    # main is never classified as an orphan of itself.
    assert "main" not in {o["branch"] for o in orphans + unlanded + unknown}


def test_orphan_sweep_cmd_reports_only_never_deletes(monkeypatch, capsys):
    # A found orphan must NOT make the standalone command non-zero — deletion is a human
    # action; the command exits non-zero only when the sweep itself could not run.
    monkeypatch.setattr(bh, "orphan_origin_refs",
                        lambda base="origin/main", remote="origin":
                        ([{"branch": "feat/x", "sha": "abc123", "why": "ancestor"}], [], [], None))
    rc = bh.orphan_origin_refs_cmd("origin/main")
    assert rc == 0
    out = capsys.readouterr().out
    assert "report-only" in out and "feat/x" in out


def test_orphan_sweep_cmd_degraded_is_nonzero(monkeypatch):
    monkeypatch.setattr(bh, "orphan_origin_refs",
                        lambda base="origin/main", remote="origin":
                        ([], [], [], "git ls-remote origin failed — orphan sweep SKIPPED"))
    assert bh.orphan_origin_refs_cmd("origin/main") == 2


# --------------------------------------------------------------------------------------
# 4. Dirty-worktree churn filter — the runtime noise a prune-safety scan must not mistake
#    for unique work, and the real WIP it must keep.
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("path,is_churn", [
    (".mcp.json", True),
    ("some/dir/metrics.db", True),
    ("python/tests/render/captures/doc_browser.png", True),
    ("a/captures/x.png", True),
    ("python/composeio/render/engine.py", False),
    ("scripts/branch_hygiene.py", False),
    ("notcaptures.png", False),          # a stray png NOT under captures/ is real work
])
def test_is_churn(path, is_churn):
    assert bh._is_churn(path) is is_churn


def test_porcelain_files_drops_churn_keeps_work(monkeypatch):
    status = (
        " M python/composeio/render/engine.py\n"
        " M .mcp.json\n"
        "?? metrics.db\n"
        "?? python/tests/render/captures/doc_browser.png\n"
        "?? real/new_file.py\n"
    )
    monkeypatch.setattr(bh, "git", lambda *a: _Proc(0, status))
    files, err = bh._porcelain_files("/some/wt")
    assert err is None
    assert set(files) == {"python/composeio/render/engine.py", "real/new_file.py"}


def test_porcelain_files_status_failure_is_reported(monkeypatch):
    monkeypatch.setattr(bh, "git", lambda *a: _Proc(1, ""))
    files, err = bh._porcelain_files("/broken/wt")
    assert files == []
    assert err and "git status failed" in err


# --------------------------------------------------------------------------------------
# 4. `wt list --format json` SHAPE — wt 0.80.0 moved to "schema 2": an envelope object
#    {schema, repo, collected, items} instead of a bare array, and renamed the fields
#    (main_state -> display.state, path -> worktree.path, working_tree ->
#    worktree.changes). Iterating the envelope as the array yielded its key strings and
#    the daily repo-health LaunchAgent died with `'str' object has no attribute 'get'`.
#    Both shapes must parse; any other shape must SKIP with a note, never raise.
#    The schema-2 fixture is REAL `wt 0.80.0 list --branches --format json` output (a
#    probe repo: one squash-landed branch, one ancestor, one unlanded, the main
#    worktree) with its path replaced by a made-up one.
# --------------------------------------------------------------------------------------

import json  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "wt080_list_branches_schema2.json")


def _schema2():
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


def _schema1():
    # The pre-0.80 bare-array shape, as the reporter has always read it.
    return [
        {"branch": "main", "path": "/srv/demo-repo", "main_state": "is_main",
         "working_tree": {"modified": True, "untracked": False}},
        {"branch": "squashed", "main_state": "integrated"},
        {"branch": "ancestor", "main_state": "empty"},
        {"branch": "unlanded", "main_state": "ahead"},
    ]


def _stub_wt(monkeypatch, payload):
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(bh.subprocess, "run", lambda *a, **k: _Proc(0, stdout))


def test_fixture_is_the_schema2_envelope():
    d = _schema2()
    assert isinstance(d, dict) and d.get("schema") == 2
    assert sorted(d) == ["collected", "items", "repo", "schema"]


def test_integrated_set_old_list_shape(monkeypatch):
    _stub_wt(monkeypatch, _schema1())
    integ, note = bh.wt_integrated_set()
    assert note is None
    assert integ == {"squashed"}


def test_integrated_set_new_envelope_shape(monkeypatch):
    _stub_wt(monkeypatch, _schema2())
    integ, note = bh.wt_integrated_set()
    assert note is None
    # positive control: the squash-landed branch; negative controls: the ancestor
    # ("empty", whose integration.reason is same_commit), the unlanded branch, main.
    assert integ == {"squashed"}


@pytest.mark.parametrize("payload", [
    {"schema": 3, "rows": []},          # object without items
    {"schema": 2, "items": {"a": 1}},   # items not a list
    42,                                 # JSON scalar (number)
    ["main", "squashed"],               # list of non-objects
])
def test_integrated_set_unknown_shape_is_noted_not_raised(monkeypatch, payload):
    _stub_wt(monkeypatch, payload)
    integ, note = bh.wt_integrated_set()
    assert integ == set()
    assert note and "SKIPPED" in note


def test_integrated_set_field_drift_is_noted_not_silently_empty(monkeypatch):
    # Rows present but no state field we know: an empty set would read as
    # "nothing integrated" — it must be a stated SKIP instead.
    _stub_wt(monkeypatch, {"schema": 2, "items": [{"branch": "x", "status": "?"}]})
    integ, note = bh.wt_integrated_set()
    assert integ == set()
    assert note and "SKIPPED" in note and "drift" in note


def test_worktree_rows_old_list_shape(monkeypatch):
    _stub_wt(monkeypatch, _schema1())
    rows, note = bh._worktree_paths_via_wt()
    assert note is None
    assert rows == [("/srv/demo-repo", "main", True)]


def test_worktree_rows_new_envelope_shape(monkeypatch):
    _stub_wt(monkeypatch, _schema2())
    rows, note = bh._worktree_paths_via_wt()
    assert note is None
    # only the worktree row has a path; branch-only rows are skipped; it is clean
    assert rows == [("/srv/demo-repo", "main", False)]


def test_worktree_rows_unknown_shape_falls_back_with_note(monkeypatch):
    _stub_wt(monkeypatch, {"schema": 9})
    rows, note = bh._worktree_paths_via_wt()
    assert rows == [] and note and "git worktree list" in note


# --------------------------------------------------------------------------------------
# 5. Field drift and EMPTY rows in the worktree enumeration (candidate review P2/P3).
#    Rows that all lack a path, or zero rows (a repo always has its main worktree), must
#    not read as "every worktree is clean": the scan must fall back to
#    `git worktree list` and STATE why.
# --------------------------------------------------------------------------------------

def test_worktree_rows_field_drift_is_noted():
    rows, note = bh.worktree_rows_from_wt_json(
        {"schema": 2, "items": [{"branch": "main", "location": {"dir": "/srv/demo-repo"}}]})
    assert rows == []
    assert note and "drift" in note and "git worktree list" in note


def test_worktree_rows_empty_items_is_noted():
    rows, note = bh.worktree_rows_from_wt_json({"schema": 2, "items": []})
    assert rows == []
    assert note and "no rows" in note and "git worktree list" in note


def test_integrated_set_empty_items_is_noted():
    integ, note = bh.integrated_from_wt_json({"schema": 2, "items": []})
    assert integ == set()
    assert note and "no rows" in note and "SKIPPED" in note


def test_dirty_scan_field_drift_falls_back_to_git_and_says_so(monkeypatch, capsys):
    _stub_wt(monkeypatch, {"schema": 2, "items": [{"branch": "main"}]})
    monkeypatch.setattr(bh, "_worktree_paths_via_git",
                        lambda: [("/srv/demo-repo", "main", None)])
    monkeypatch.setattr(bh, "_porcelain_files", lambda p: (["work.py"], None))
    findings, note = bh.dirty_worktree_findings()
    # the git fallback ran: the dirty worktree is FOUND, not hidden behind "clean"
    assert [f["path"] for f in findings] == ["/srv/demo-repo"]
    assert note and "drift" in note
    bh.show_dirty(findings, note)
    shown = capsys.readouterr().out
    assert "drift" in shown and "every worktree is clean" not in shown
