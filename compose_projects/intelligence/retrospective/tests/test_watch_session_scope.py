"""Acceptance tests for the away-watch run->session SCOPE ([CXC-GV-SCOPE]).

Proves, against a TEMP db (NEVER the real metrics.db) and a TEMP `.lts` corpus
(NEVER the real decision graph):

  * the OBSERVATION round-trip: open the window -> decisions appear -> close ->
    the run carries exactly the tokens those decisions actually declared;
  * the HONESTY GATE: with no baseline, NOTHING is recorded (a run with no
    observation window stays UNSCOPED rather than claiming the corpus);
  * tokens are stored VERBATIM and MIXED-SHAPE (uuid + date + date-slug), never
    normalized — the viewer matches character-for-character;
  * nothing is ever derived from the run's date, label, or session_id;
  * BACKWARD COMPATIBILITY: a pre-scope DB migrates additively, old rows project
    `sessions == []`, and a metrics re-record never clobbers observed tokens.

Run:  cd <repo root> && python3 -m pytest \
        compose_projects/intelligence/retrospective/tests/test_watch_session_scope.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from intelligence.retrospective import db as db_mod
from intelligence.retrospective import watch_sessions as ws


# --------------------------------------------------------------------------- #
# fixtures — a temp DB and a temp, REAL `.lts` corpus we can grow node by node
# --------------------------------------------------------------------------- #

_CORPUS_HEAD = """---
domain: plt.cux
confidentiality: internal-only
artifact-readiness: system-design
created: 2026-08-06
---
# Test decision corpus [SPEC:TST-DECISIONS]

> A throwaway corpus for the scope tests.

---

"""


def _node(node_id: str, token: str) -> str:
    return (f"### [{node_id}] test node {node_id}\n"
            f"- lifecycle-position: committed\n"
            f"- decided-in-session: {token}\n"
            f"- importance: medium\n"
            f"- rationale: a test node.\n\n")


@pytest.fixture()
def corpus(tmp_path):
    """A live `.lts` corpus dir with an `append(id, token)` helper."""
    d = tmp_path / "corpus"
    d.mkdir()
    f = d / "graph.lts"
    f.write_text(_CORPUS_HEAD)

    class _Corpus:
        path = str(d)

        def append(self, node_id, token):
            f.write_text(f.read_text() + _node(node_id, token))

    return _Corpus()


@pytest.fixture()
def db_file(tmp_path, monkeypatch):
    p = str(tmp_path / "metrics.db")
    monkeypatch.setenv("COMPOSEIO_RETRO_DB", p)      # watch_sessions opens its own
    return p


def _row(label="W"):
    return next(r for r in db_mod.list_away_watches() if r["label"] == label)


# --------------------------------------------------------------------------- #
# the headline path
# --------------------------------------------------------------------------- #

def test_observation_round_trip_records_only_what_appeared(db_file, corpus):
    """[CXC-GV-SCOPE] the whole point: a run names the session token(s) carried by
    the decisions that appeared INSIDE its window — and nothing else."""
    # decisions that already existed BEFORE the watch (a prior night's work)
    corpus.append("DEC-BEFORE-ONE", "2026-07-20")
    corpus.append("DEC-BEFORE-TWO", "2026-07-20")

    db_mod.record_away_watch(label="W", started_ts=100.0)
    rep = ws.open_watch("W", path=corpus.path)
    # 2 decision nodes + the corpus's own document node
    assert rep["status"] == "opened" and rep["baseline"] == 3

    # ...the watch runs, and decisions are made in it
    corpus.append("DEC-DURING-ONE", "2026-08-06")
    corpus.append("DEC-DURING-TWO", "2026-08-06")

    rep = ws.close_watch("W", path=corpus.path)
    assert rep["status"] == "recorded"
    assert rep["new_nodes"] == ["DEC-DURING-ONE", "DEC-DURING-TWO"]
    # ONLY the in-window token — the pre-existing 2026-07-20 is NOT claimed
    assert rep["sessions"] == ["2026-08-06"]
    assert _row()["sessions"] == ["2026-08-06"]


def test_consumer_contract_a_populated_list_is_the_scope(db_file, corpus):
    """The row shape `away_watch_publisher.watch_sessions()` consumes: a non-empty
    `sessions` list on the projected row WINS over the unreliable `session_id`.
    (The consumer's preference rule, restated here as the write side's contract —
    the consumer itself lives in the not-yet-landed viewer branch.)"""
    corpus.append("DEC-IN", "2026-08-06")
    db_mod.record_away_watch(label="W", session_id="59f1b46e-shared-by-4-runs")
    ws.open_watch("W", path=corpus.path)
    corpus.append("DEC-NEW", "2026-08-06-away-watch-scope")
    ws.close_watch("W", path=corpus.path)

    row = _row()
    # exactly the preference `watch_sessions()` implements:
    assert isinstance(row["sessions"], list) and row["sessions"]
    assert row["sessions"] == ["2026-08-06-away-watch-scope"]
    assert row["session_id"] == "59f1b46e-shared-by-4-runs"   # still there, unused


# --------------------------------------------------------------------------- #
# the honesty gate — the tests that must never be weakened
# --------------------------------------------------------------------------- #

def test_no_baseline_records_nothing(db_file, corpus):
    """THE GATE: a run with no observation window CANNOT know which decisions are
    its own. It records NOTHING and stays UNSCOPED — it must never diff against
    the empty set and claim the entire corpus."""
    corpus.append("DEC-A", "2026-07-20")
    corpus.append("DEC-B", "2026-07-22")
    db_mod.record_away_watch(label="W", started_ts=100.0)     # no open_watch()

    rep = ws.close_watch("W", path=corpus.path)
    assert rep["status"] == "no-baseline"
    assert rep["sessions"] == [] and rep["tokens"] == []
    assert _row()["sessions"] == []                            # nothing invented
    assert "UNSCOPED" in rep["message"]


def test_baseline_absent_is_not_baseline_empty(db_file, corpus):
    """`decision_baseline()` returns None for "never opened" and a set for
    "opened over an empty corpus" — collapsing the two is what would let an
    un-baselined run claim everything."""
    db_mod.record_away_watch(label="W")
    assert db_mod.decision_baseline("W") is None               # never opened
    db_mod.snapshot_decision_baseline("W", [])                 # opened over empty
    assert db_mod.decision_baseline("W") == set()              # a REAL empty set
    # ...and an empty baseline is usable: everything found later is genuinely new
    corpus.append("DEC-NEW", "2026-08-06")
    assert ws.close_watch("W", path=corpus.path)["sessions"] == ["2026-08-06"]


def test_nothing_is_derived_from_date_label_or_session_id(db_file, corpus):
    """A run whose window saw nothing gets NO scope — not a guess from its label
    ("Overnight 2026-07-22"), its started_ts, or its session_id, even though
    decisions dated that night are sitting right there in the corpus."""
    corpus.append("DEC-THAT-NIGHT", "2026-07-22")
    db_mod.record_away_watch(label="Overnight 2026-07-22",
                             session_id="59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3",
                             started_ts=1784703000.0)
    ws.open_watch("Overnight 2026-07-22", path=corpus.path)
    rep = ws.close_watch("Overnight 2026-07-22", path=corpus.path)
    assert rep["status"] == "no-new-decisions"
    assert _row("Overnight 2026-07-22")["sessions"] == []


def test_unreadable_corpus_is_not_an_empty_corpus(db_file, corpus):
    """A corpus that cannot be READ records nothing (and stamps no baseline) —
    an unreadable corpus must never masquerade as an empty one."""
    db_mod.record_away_watch(label="W")
    rep = ws.open_watch("W", path=str(corpus.path) + "-does-not-exist")
    assert rep["status"] == "corpus-unavailable"
    assert db_mod.decision_baseline("W") is None       # NO baseline stamped
    # ...and the later close therefore refuses too, rather than over-claiming
    assert ws.close_watch("W", path=corpus.path)["status"] == "no-baseline"


def test_new_nodes_without_a_token_record_nothing(db_file, corpus):
    """New nodes that carry no `decided-in-session` yield no scope — the run does
    not substitute its own id for a token the node never declared."""
    db_mod.record_away_watch(label="W", session_id="sid-1")
    ws.open_watch("W", path=corpus.path)
    (list((__import__("pathlib").Path(corpus.path)).glob("*.lts"))[0]
     ).write_text(_CORPUS_HEAD +
                  "### [DEC-NOTOKEN] no session bullet\n"
                  "- lifecycle-position: raised\n"
                  "- importance: low\n"
                  "- rationale: a node with no session token.\n\n")
    rep = ws.close_watch("W", path=corpus.path)
    assert rep["status"] == "untokened-only"
    assert _row()["sessions"] == []


def test_missing_row_is_a_loud_noop(db_file, corpus):
    assert ws.open_watch("nope", path=corpus.path)["status"] == "no-row"
    assert ws.close_watch("nope", path=corpus.path)["status"] == "no-row"
    assert db_mod.observe_watch_sessions("nope", ["x"]) is None


# --------------------------------------------------------------------------- #
# verbatim + mixed-shape tokens
# --------------------------------------------------------------------------- #

def test_tokens_are_verbatim_and_may_be_mixed_shape(db_file, corpus):
    """`decided-in-session` is heterogeneous in the real corpus (uuid, date,
    date-slug). A run that spans several records them ALL, unmodified and in
    first-seen order — normalizing any of them would break the exact match the
    viewer does."""
    db_mod.record_away_watch(label="W")
    ws.open_watch("W", path=corpus.path)
    corpus.append("DEC-U", "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3")
    corpus.append("DEC-D", "2026-08-06")
    corpus.append("DEC-S", "2026-08-05-cic-composite-esp32-fan-continuation")
    rep = ws.close_watch("W", path=corpus.path)
    assert rep["sessions"] == ["2026-08-06",
                               "2026-08-05-cic-composite-esp32-fan-continuation",
                               "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3"]


def test_normalize_sessions_only_strips_dedups_and_drops_empties():
    n = db_mod._normalize_sessions
    assert n([" 2026-07-22 ", "2026-07-22", "", None, "UUID-A"]) == \
        ["2026-07-22", "UUID-A"]
    assert n(None) == [] and n([]) == []
    assert n("solo-token") == ["solo-token"]        # a bare token, not chars
    # NO canonicalization: case, slug tails and date shapes survive untouched
    assert n(["2026-08-05-cic-composite", "59F1B46E-AbCd"]) == \
        ["2026-08-05-cic-composite", "59F1B46E-AbCd"]


def test_observation_is_append_only_and_idempotent(db_file, corpus):
    """A long watch can sync mid-run: repeated closes accumulate without duping
    or reordering, so the scope stays current."""
    db_mod.record_away_watch(label="W")
    ws.open_watch("W", path=corpus.path)
    corpus.append("DEC-1", "2026-08-06")
    assert ws.close_watch("W", path=corpus.path)["sessions"] == ["2026-08-06"]
    assert ws.close_watch("W", path=corpus.path)["status"] == "recorded"
    corpus.append("DEC-2", "2026-08-07")
    assert ws.close_watch("W", path=corpus.path)["sessions"] == \
        ["2026-08-06", "2026-08-07"]
    assert _row()["sessions"] == ["2026-08-06", "2026-08-07"]


# --------------------------------------------------------------------------- #
# backward compatibility / retro-fit
# --------------------------------------------------------------------------- #

def test_additive_migration_of_a_prescope_table(tmp_path):
    """A DB created before the scope columns migrates in connect() via ALTER: old
    data preserved, new columns land NULL, old rows project `sessions == []`."""
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript(
        """CREATE TABLE away_watch (
             id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
             label TEXT UNIQUE, started_ts REAL, ended_ts REAL, commits INTEGER,
             subagents INTEGER, tests INTEGER, artifacts TEXT, scene_path TEXT,
             recorded_at REAL, decisions INTEGER, tokens INTEGER, tasks TEXT,
             git_state TEXT, build_complete_ts REAL, resumed_ts REAL);""")
    c.execute("INSERT INTO away_watch (label, tests, session_id) VALUES (?,?,?)",
              ("old", 99, "overnight-2026-07-22"))
    c.commit(); c.close()

    conn = db_mod.connect(p)                       # connect() migrates
    cols = {row[1] for row in conn.execute("PRAGMA table_info(away_watch)")}
    assert {"sessions", "decision_baseline_ts"} <= cols
    r = next(x for x in db_mod.list_away_watches(conn) if x["label"] == "old")
    assert r["tests"] == 99                        # preserved
    assert r["sessions"] == []                     # honest empty, not a crash
    assert r["session_id"] == "overnight-2026-07-22"
    assert db_mod.decision_baseline("old", conn=conn) is None   # unscopeable
    conn.close()


def test_a_metrics_rerecord_never_clobbers_observed_tokens(db_file, corpus):
    """POST-WATCH order-independence: the checklist's final `record_away_watch`
    (ended_ts + final counts) must not wipe the tokens already observed."""
    db_mod.record_away_watch(label="W", started_ts=100.0)
    ws.open_watch("W", path=corpus.path)
    corpus.append("DEC-X", "2026-08-06")
    ws.close_watch("W", path=corpus.path)

    db_mod.record_away_watch(label="W", started_ts=100.0, ended_ts=900.0,
                             decisions=7)          # says nothing about sessions
    r = _row()
    assert r["decisions"] == 7 and r["ended_ts"] == 900.0
    assert r["sessions"] == ["2026-08-06"]         # preserved


def test_explicit_sessions_kwarg_replaces_and_none_preserves(db_file):
    """`record_away_watch(sessions=[...])` is an explicit caller assertion and
    REPLACES; omitting it preserves. (Provided for a caller that observed its
    tokens by some other honest means — it is still never a guess.)"""
    db_mod.record_away_watch(label="W", sessions=["tok-a", "tok-a", " tok-b "])
    assert _row()["sessions"] == ["tok-a", "tok-b"]
    db_mod.record_away_watch(label="W", decisions=3)
    assert _row()["sessions"] == ["tok-a", "tok-b"]
    db_mod.record_away_watch(label="W", sessions=["tok-c"])
    assert _row()["sessions"] == ["tok-c"]


def test_reopening_a_window_replaces_the_baseline(db_file, corpus):
    """Re-opening is legitimate (e.g. the preflight row predated a reachable
    corpus) and REPLACES the baseline; already-observed tokens are untouched."""
    db_mod.record_away_watch(label="W")
    ws.open_watch("W", path=corpus.path)
    corpus.append("DEC-1", "2026-08-06")
    ws.close_watch("W", path=corpus.path)
    assert ws.open_watch("W", path=corpus.path)["baseline"] == 2   # doc + DEC-1
    corpus.append("DEC-2", "2026-08-07")
    assert ws.close_watch("W", path=corpus.path)["new_nodes"] == ["DEC-2"]
    assert _row()["sessions"] == ["2026-08-06", "2026-08-07"]   # append-only
