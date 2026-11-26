"""Acceptance tests for the RUN-GRAINED session slug ([CXC-GV-SCOPE], David
2026-08-06) — D3 (the watch start script opens the observation window) and D4
(decisions written during the run carry a run-grained token), which are ONE
mechanism: an identity established at start.

The load-bearing claims proved here:

  * the slug is RUN-grained, not day-grained: two runs on ONE night get two
    distinct slugs, so the corpus can tell them apart (the case the bare-date
    convention cannot express);
  * `decision_ops` is OPT-IN BY PRESENCE: with no ambient token its behaviour is
    byte-identical to before, including the same error on a missing field;
  * a NON-WATCH decision write is completely unaffected — the token lives in the
    process environment, so a concurrent unrelated writer never sees it;
  * `decision_ops` never learns about away-watches (no import, no coupling);
  * the slug is a DECLARATION, never a scope: it is not written into `sessions`
    at start, and a run whose decisions did not carry it is reported as such;
  * end-to-end: start a run -> write decisions with the ambient token -> close ->
    the run's scope is EXACTLY that run's decisions, excluding a same-night run.

Run:  cd <repo root> && python3 -m pytest \
        compose_projects/intelligence/retrospective/tests/test_run_slug.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from composeio.decision_ops import core as dcore
from intelligence.retrospective import db as db_mod
from intelligence.retrospective import watch_sessions as ws

# .../compose_projects/intelligence/retrospective/tests/<this file> — FIVE levels to the
# repo root, not four. 2026-08-17: intelligence/ moved under compose_projects/, and a depth
# count carries no path text, so the stale four kept resolving and named
# <repo>/compose_projects as the root; every join below then read
# <repo>/compose_projects/compose_projects/intelligence/... and raised FileNotFoundError.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

_HEAD = ("---\ndomain: plt.cux\nconfidentiality: internal-only\n"
         "artifact-readiness: system-design\ncreated: 2026-08-06\n---\n"
         "# Run-slug test corpus [SPEC:TST-RUNSLUG]\n\n> throwaway.\n\n---\n\n")


@pytest.fixture()
def graph(tmp_path):
    """A real `.lts` graph file that `decision_ops.add()` writes into."""
    d = tmp_path / "corpus"
    d.mkdir()
    f = d / "graph.lts"
    f.write_text(_HEAD)
    return f


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_RETRO_DB", str(tmp_path / "metrics.db"))
    return str(tmp_path / "metrics.db")


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch):
    """Every test starts with NO ambient token — the default world."""
    monkeypatch.delenv(dcore.SESSION_ENV, raising=False)


def _add(graph, node_id, **fields):
    node = {"id": node_id, "title": f"node {node_id}",
            "lifecycle-position": "committed", "importance": "medium",
            "rationale": "a test node."}
    node.update(fields)
    return dcore.add(str(graph), node)


# --------------------------------------------------------------------------- #
# D4 — the ambient session token seam
# --------------------------------------------------------------------------- #

def test_no_ambient_token_means_exactly_the_old_behaviour(graph):
    """OPT-IN BY PRESENCE. Unset -> an omitted decided-in-session still raises the
    SAME error it always did. Nothing is defaulted, nothing is invented."""
    assert dcore.ambient_session() is None
    with pytest.raises(ValueError) as exc:
        _add(graph, "DEC-NO-TOKEN")
    assert "decided-in-session" in str(exc.value)
    # ...and an explicit value works exactly as before
    _add(graph, "DEC-EXPLICIT", **{"decided-in-session": "2026-07-22"})
    assert "decided-in-session: 2026-07-22" in graph.read_text()


def test_ambient_token_fills_an_omitted_field(graph, monkeypatch):
    monkeypatch.setenv(dcore.SESSION_ENV, "2026-08-06-away-watch-scope")
    assert dcore.ambient_session() == "2026-08-06-away-watch-scope"
    _add(graph, "DEC-AMBIENT")
    assert "decided-in-session: 2026-08-06-away-watch-scope" in graph.read_text()


def test_an_explicit_value_always_wins_over_the_ambient_token(graph, monkeypatch):
    """The caller's word is never overridden — an ambient token only FILLS a gap."""
    monkeypatch.setenv(dcore.SESSION_ENV, "2026-08-06-away-watch-scope")
    _add(graph, "DEC-EXPLICIT-WINS", **{"decided-in-session": "2026-07-20"})
    assert "decided-in-session: 2026-07-20" in graph.read_text()
    assert "2026-08-06-away-watch-scope" not in graph.read_text()


def test_blank_ambient_token_is_no_token(monkeypatch):
    """A blank/whitespace variable is UNSET, not an empty token — an empty
    `decided-in-session` would be a corrupt node, never a scope."""
    monkeypatch.setenv(dcore.SESSION_ENV, "   ")
    assert dcore.ambient_session() is None


def test_ambient_token_is_verbatim(monkeypatch):
    """Tokens are matched character-for-character downstream, so the seam must not
    reshape them — mixed shapes (uuid / date / slug) all pass through untouched."""
    for tok in ("2026-08-06-away-watch-scope", "2026-07-22",
                "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3", "Mixed-CASE-Slug"):
        monkeypatch.setenv(dcore.SESSION_ENV, f"  {tok}  ")
        assert dcore.ambient_session() == tok


def test_decision_ops_never_learns_about_away_watch():
    """The coupling runs ONE way: an orchestrator sets an ambient string, the
    generic decision tool reads it. decision_ops must not import the retrospective
    /watch subsystem, or the 'generic tool' claim is false."""
    import ast
    src = open(os.path.join(REPO_ROOT,
                            "python/composeio/decision_ops/core.py")).read()
    imported = set()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Import):
            imported.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            imported.add(n.module or "")
    # a STRUCTURAL check (prose may legitimately explain the relationship; an
    # import would BE the coupling)
    assert not any("retrospective" in m or "watch" in m or "intelligence" in m
                   for m in imported), imported
    # nor via the module registry at runtime
    assert not hasattr(dcore, "db") and not hasattr(dcore, "watch_sessions")


def test_a_concurrent_non_watch_writer_is_unaffected(graph):
    """HONEST UNDER CONCURRENCY, proved with a real second PROCESS: a watch exports
    the token in ITS process, and a separate process writing a decision at the same
    moment does not see it (env is inherited, never shared)."""
    os.environ[dcore.SESSION_ENV] = "2026-08-06-the-watch"      # "this" process
    try:
        env = dict(os.environ)
        env.pop(dcore.SESSION_ENV)                              # the other instance
        env["PYTHONPATH"] = os.path.join(REPO_ROOT, "python") + os.pathsep + REPO_ROOT
        out = subprocess.run(
            [sys.executable, "-m", "composeio.decision_ops", "--json",
             "session-token"],
            capture_output=True, text=True, env=env, cwd=REPO_ROOT)
        assert '"session_token": null' in out.stdout
        assert out.returncode == 1                              # gateable
        # and THIS process still has it — the two are independent
        assert dcore.ambient_session() == "2026-08-06-the-watch"
    finally:
        os.environ.pop(dcore.SESSION_ENV, None)


def test_session_token_cli_reports_a_set_token():
    env = dict(os.environ)
    env[dcore.SESSION_ENV] = "2026-08-06-away-watch-scope"
    env["PYTHONPATH"] = os.path.join(REPO_ROOT, "python") + os.pathsep + REPO_ROOT
    out = subprocess.run(
        [sys.executable, "-m", "composeio.decision_ops", "session-token"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert out.returncode == 0
    assert "2026-08-06-away-watch-scope" in out.stdout


def test_mcp_exposes_the_propagation_check(monkeypatch):
    """The token is inherited at server START, so only the server can answer
    whether it arrived. The read-only tool that reports it must exist, be
    dispatchable, and never require a graph (it must answer even when the corpus
    config is faulted)."""
    from composeio.decision_ops import mcp_server as m
    assert "decision_session_token" in {t["name"] for t in m.TOOLS}
    assert "decision_session_token" not in m._GRAPH_TOOLS
    ops = m.DecisionOpsMcp.__new__(m.DecisionOpsMcp)
    monkeypatch.setenv(dcore.SESSION_ENV, "2026-08-06-away-watch-scope")
    res = ops._session_token({})
    assert res["success"] and res["ambient"] is True
    assert res["session_token"] == "2026-08-06-away-watch-scope"
    monkeypatch.delenv(dcore.SESSION_ENV)
    res = ops._session_token({})
    assert res["success"] and res["ambient"] is False
    assert res["session_token"] is None


# --------------------------------------------------------------------------- #
# the slug itself
# --------------------------------------------------------------------------- #

def test_slug_is_run_grained_not_day_grained():
    """THE POINT of D4: two runs on ONE night must be distinguishable. The bare
    date convention cannot express this; the slug can."""
    import time
    night = time.mktime((2026, 8, 6, 21, 0, 0, 0, 0, -1))
    a = ws.run_slug_for("Away 2026-08-06 PM", night)
    b = ws.run_slug_for("Overnight 2026-08-06", night)
    assert a != b
    assert a.startswith("2026-08-06-") and b.startswith("2026-08-06-")


def test_slug_shape_matches_the_adopted_convention():
    import time
    day = time.mktime((2026, 8, 6, 9, 0, 0, 0, 0, -1))
    assert ws.run_slug_for("Away Watch Scope", day) == "2026-08-06-away-watch-scope"
    # a date already inside the label moves to the FRONT, never duplicates
    assert ws.run_slug_for("Overnight 2026-07-22", day) == "2026-07-22-overnight"
    # the shape is a token the corpus already uses (lowercase, hyphenated)
    assert ws.run_slug_for("Away 2026-07-26 (maker)", day) == "2026-07-26-away-maker"


def test_slug_needs_a_real_label():
    with pytest.raises(ValueError):
        ws.run_slug_for("   ")


# --------------------------------------------------------------------------- #
# D3 x D4 composed
# --------------------------------------------------------------------------- #

def test_open_establishes_the_identity_but_never_claims_a_scope(db_file, graph):
    """The slug is a DECLARATION. At start no decision carries it, so writing it
    into `sessions` would be a prediction — the scope must only ever name tokens
    OBSERVED on real nodes."""
    db_mod.record_away_watch(label="Away Watch Scope")
    rep = ws.open_watch("Away Watch Scope", path=str(graph.parent))
    assert rep["status"] == "opened"
    assert rep["run_slug"].endswith("-away-watch-scope")
    row = next(w for w in db_mod.list_away_watches()
               if w["label"] == "Away Watch Scope")
    assert row["run_slug"] == rep["run_slug"]
    assert row["sessions"] == []                    # NOT pre-claimed


def test_end_to_end_run_grained_scope(db_file, graph, monkeypatch):
    """THE COMPOSED MECHANISM: start the run (window + identity), write decisions
    with the ambient token, close. The run's scope is exactly ITS decisions — and
    a SECOND run the SAME night is cleanly excluded, which is the isolation the
    day-grained token could not give."""
    # a same-night run that already wrote a decision the old way
    _add(graph, "DEC-OTHER-RUN", **{"decided-in-session": "2026-08-06"})

    db_mod.record_away_watch(label="Away Watch Scope")
    rep = ws.open_watch("Away Watch Scope", path=str(graph.parent))
    slug = rep["run_slug"]

    # the run's decisions inherit the ambient token (what the start script exports)
    monkeypatch.setenv(dcore.SESSION_ENV, slug)
    _add(graph, "DEC-RUN-ONE")
    _add(graph, "DEC-RUN-TWO")
    # ...while the OTHER instance keeps writing date-stamped decisions
    _add(graph, "DEC-OTHER-LATER", **{"decided-in-session": "2026-08-06"})

    rep = ws.close_watch("Away Watch Scope", path=str(graph.parent))
    assert rep["status"] == "recorded"
    assert slug in rep["sessions"]
    assert rep.get("slug_missing") is not True
    # the run-grained token is what a viewer would filter on; the same-night
    # date token is also present because that node genuinely appeared in the
    # window — observed, not guessed, and reported as such.
    assert set(rep["sessions"]) == {slug, "2026-08-06"}
    assert ws.show_watch("Away Watch Scope")["run_grained"] is True


def test_close_warns_when_the_slug_never_reached_the_writer(db_file, graph):
    """The self-diagnosis: if decisions came out date-stamped, the ambient token
    did not propagate. The scope stays honest (still purely observed) but the
    operator is told the run-grained isolation did not happen."""
    db_mod.record_away_watch(label="Away Watch Scope")
    ws.open_watch("Away Watch Scope", path=str(graph.parent))
    _add(graph, "DEC-DATED", **{"decided-in-session": "2026-08-06"})  # no token
    rep = ws.close_watch("Away Watch Scope", path=str(graph.parent))
    assert rep["status"] == "recorded"
    assert rep["slug_missing"] is True
    assert "did not reach the decision writer" in rep["message"]
    assert rep["sessions"] == ["2026-08-06"]        # honest: what was observed
    assert ws.show_watch("Away Watch Scope")["run_grained"] is False


def test_start_script_opens_the_window_and_prints_the_slug(tmp_path, graph):
    """D3 end-to-end through the REAL script: it creates the row, opens the
    observation window, and derives the run slug — no checklist step involved."""
    env = dict(os.environ)
    env["COMPOSEIO_RETRO_DB"] = str(tmp_path / "start.db")
    env["COMPOSEIO_DECISION_CORPUS_DIR"] = str(graph.parent)
    env.pop(dcore.SESSION_ENV, None)
    out = subprocess.run(
        [os.path.join(REPO_ROOT, "compose_projects/intelligence/away-watch-start.sh"),
         "Away Watch Scope", "--print-env"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert out.returncode == 0, out.stderr
    assert "export COMPOSEIO_DECISION_SESSION=" in out.stdout
    assert "away-watch-scope" in out.stdout

    env2 = dict(env)
    from intelligence.retrospective import db as _d
    conn = _d.connect(env["COMPOSEIO_RETRO_DB"])
    row = next(w for w in _d.list_away_watches(conn)
               if w["label"] == "Away Watch Scope")
    assert row["run_slug"] and row["run_slug"].endswith("-away-watch-scope")
    assert row["sessions"] == []                       # declared, not claimed
    assert _d.decision_baseline("Away Watch Scope", conn=conn) is not None
    conn.close()
    assert env2                                        # (env copy kept for clarity)


def test_start_script_refuses_when_the_window_cannot_be_opened(tmp_path):
    """A run that cannot be observed must not silently start: the script exits
    non-zero rather than leaving the operator with a permanently unscoped run."""
    env = dict(os.environ)
    env["COMPOSEIO_RETRO_DB"] = str(tmp_path / "start.db")
    env["COMPOSEIO_DECISION_CORPUS_DIR"] = str(tmp_path / "no-such-corpus")
    out = subprocess.run(
        [os.path.join(REPO_ROOT, "compose_projects/intelligence/away-watch-start.sh"),
         "Doomed Watch"],
        capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    assert out.returncode == 1
    assert "permanently UNSCOPED" in out.stderr


def test_transport_forwards_the_token_only_when_set():
    """The bounce route: transport.sh forwards the token (never sets one) and
    CLEARS a stale one from the MCP server config, so a finished watch's slug
    cannot leak into the next session's decision writes."""
    src = open(os.path.join(REPO_ROOT, "compose_projects/intelligence/launcher/transport.sh")).read()
    assert 'COMPOSEIO_DECISION_SESSION="${COMPOSEIO_DECISION_SESSION:-}"' in src
    assert 'env.pop("COMPOSEIO_DECISION_SESSION", None)' in src
