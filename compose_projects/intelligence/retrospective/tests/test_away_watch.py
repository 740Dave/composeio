"""Acceptance tests for the Away-Watch history persistence (David 2026-07-22:
"maintain a list of Away Watch entries in the sqlite db").

Proves, against a TEMP db (NEVER a real one): (a) record + list round-trips the
full row incl. the artifacts JSON list; (b) re-recording the same label UPSERTS
(idempotent seeding, no dupes); (c) list is most-recent-first; (d) the
2026-07-22 seed carries the real overnight tally + the two named artifacts.

Run:  cd <repo root> && python3 -m pytest compose_projects/intelligence/retrospective/tests/test_away_watch.py -q
"""
from __future__ import annotations

import pytest

from intelligence.retrospective import db as db_mod


@pytest.fixture()
def db_file(tmp_path):
    return str(tmp_path / "metrics.db")


def _conn(path):
    return db_mod.connect(path)


def test_record_and_list_round_trip(db_file):
    conn = _conn(db_file)
    rid = db_mod.record_away_watch(
        conn, label="Overnight A", session_id="sess-1",
        started_ts=100.0, ended_ts=200.0, commits=52, subagents=14, tests=1334,
        artifacts=[{"title": "Build log", "path": "docs/build.md"},
                   {"title": "Gap analysis", "path": "docs/gap.md"}],
        scene_path="compose_projects/intelligence/console/nightwatch.compose.yaml")
    assert rid > 0
    rows = db_mod.list_away_watches(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["label"] == "Overnight A"
    assert r["session_id"] == "sess-1"
    assert (r["commits"], r["subagents"], r["tests"]) == (52, 14, 1334)
    assert r["scene_path"] == "compose_projects/intelligence/console/nightwatch.compose.yaml"
    # artifacts came back as a parsed [{title, path}] list
    assert r["artifacts"] == [{"title": "Build log", "path": "docs/build.md"},
                              {"title": "Gap analysis", "path": "docs/gap.md"}]
    conn.close()


def test_relabel_upsert_is_idempotent(db_file):
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="Overnight A", tests=10)
    db_mod.record_away_watch(conn, label="Overnight A", tests=1334)  # same label
    rows = db_mod.list_away_watches(conn)
    assert len(rows) == 1                       # upserted, not duped
    assert rows[0]["tests"] == 1334             # latest values win
    conn.close()


def test_list_is_most_recent_first(db_file):
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="older", started_ts=100.0)
    db_mod.record_away_watch(conn, label="newer", started_ts=500.0)
    db_mod.record_away_watch(conn, label="middle", started_ts=300.0)
    labels = [r["label"] for r in db_mod.list_away_watches(conn)]
    assert labels == ["newer", "middle", "older"]
    conn.close()


def test_artifacts_normalized_and_junk_dropped(db_file):
    conn = _conn(db_file)
    db_mod.record_away_watch(
        conn, label="norm",
        artifacts=[{"path": "docs/x.md"},          # title defaulted from path
                   {"title": "no path"},           # dropped (no path)
                   "garbage"])                      # dropped (not a dict)
    r = db_mod.list_away_watches(conn)[0]
    assert r["artifacts"] == [{"title": "x.md", "path": "docs/x.md"}]
    conn.close()


def test_seed_default_matches_the_overnight_run(db_file):
    conn = _conn(db_file)
    db_mod.seed_default_away_watch(conn)
    db_mod.seed_default_away_watch(conn)            # idempotent
    rows = db_mod.list_away_watches(conn)
    assert len(rows) == 1
    r = rows[0]
    assert r["label"] == "Overnight 2026-07-22"
    assert (r["commits"], r["subagents"], r["tests"]) == (52, 14, 1334)
    titles = [a["title"] for a in r["artifacts"]]
    assert titles == ["Build log", "DRADIS gap analysis"]
    assert r["scene_path"] == "compose_projects/intelligence/console/nightwatch.compose.yaml"
    conn.close()


# =============================================================================
# the additive fields (David 2026-07-22): decisions / tokens / tasks / git_state
# + the DERIVED elapsed
# =============================================================================

def test_seed_carries_new_window_fields(db_file):
    conn = _conn(db_file)
    db_mod.seed_default_away_watch(conn)
    r = db_mod.list_away_watches(conn)[0]
    assert r["decisions"] == 12
    assert r["tokens"] == 23_700_000
    assert r["elapsed"] == 28320.0                  # 7h 52m, derived from start/end
    # the task checklist round-trips as [{label, status}] with all three states
    statuses = {tk["status"] for tk in r["tasks"]}
    assert statuses == {"done", "partial", "deferred"}
    assert len(r["tasks"]) == 8
    # git_state is a live string (this repo is a git tree -> non-empty)
    assert isinstance(r["git_state"], str)


def test_record_and_roundtrip_new_fields(db_file):
    conn = _conn(db_file)
    db_mod.record_away_watch(
        conn, label="W", started_ts=100.0, ended_ts=460.0,
        decisions=5, tokens=1_500_000,
        tasks=[{"label": "a", "status": "done"},
               {"label": "b", "status": "nonsense"},   # -> deferred
               {"label": "", "status": "done"},         # -> dropped (no label)
               "bare"],                                 # -> deferred
        git_state="clean")
    r = db_mod.list_away_watches(conn)[0]
    assert r["decisions"] == 5 and r["tokens"] == 1_500_000
    assert r["git_state"] == "clean"
    assert r["elapsed"] == 360.0                    # 6m, derived
    assert r["tasks"] == [{"label": "a", "status": "done"},
                          {"label": "b", "status": "deferred"},
                          {"label": "bare", "status": "deferred"}]
    conn.close()


def test_elapsed_none_when_end_missing(db_file):
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="open", started_ts=100.0)  # no end
    assert db_mod.list_away_watches(conn)[0]["elapsed"] is None
    conn.close()


def test_additive_migration_of_a_preexisting_table(db_file):
    """A DB created before the new columns is migrated in connect() via ALTER —
    old data preserved, new columns land as NULL, then usable."""
    import sqlite3
    c = sqlite3.connect(db_file)
    c.executescript(
        """CREATE TABLE away_watch (
             id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
             label TEXT UNIQUE, started_ts REAL, ended_ts REAL, commits INTEGER,
             subagents INTEGER, tests INTEGER, artifacts TEXT, scene_path TEXT,
             recorded_at REAL);""")
    c.execute("INSERT INTO away_watch (label, tests) VALUES (?,?)", ("old", 99))
    c.commit(); c.close()

    conn = _conn(db_file)                            # connect() migrates
    cols = {row[1] for row in conn.execute("PRAGMA table_info(away_watch)")}
    assert {"decisions", "tokens", "tasks", "git_state"} <= cols
    rows = db_mod.list_away_watches(conn)
    assert rows[0]["label"] == "old" and rows[0]["tests"] == 99   # preserved
    assert rows[0]["tasks"] == [] and rows[0]["decisions"] is None
    # the migrated table now records the new fields
    db_mod.record_away_watch(conn, label="old", decisions=3,
                             tasks=[{"label": "x", "status": "done"}])
    r = db_mod.list_away_watches(conn)[0]
    assert r["decisions"] == 3 and r["tasks"] == [{"label": "x", "status": "done"}]
    conn.close()


def test_git_state_summary_never_raises(monkeypatch):
    """Graceful on a non-repo path -> '' (missing capability reads blank).

    HERMETIC AGAINST GIT'S AMBIENT ENV. `git -C <dir> status` is STEERED by an
    inherited `GIT_DIR` / `GIT_INDEX_FILE` / `GIT_WORK_TREE`, and git EXPORTS
    those to every hook it runs. So this test passed from a shell and FAILED
    inside the repo's own pre-commit gate, reporting the real repo's dirty
    count for an empty temp dir. Clearing the env is what makes "is this a
    repo?" the actual question being asked.

    (The same ambient steering means `git_state_summary()` itself reports the
    WRONG repo when called from a hook. That is a real, separate defect in
    `db.py`; noted here rather than fixed, because it is outside this change.)
    """
    import tempfile
    for var in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE",
                "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        monkeypatch.delenv(var, raising=False)
    with tempfile.TemporaryDirectory() as d:
        assert db_mod.git_state_summary(d) == ""
