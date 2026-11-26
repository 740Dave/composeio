"""Acceptance tests for away-watch IDLE TRACKING ([SPEC:CXC-IDLE-TRACKING]).

Proves, against a TEMP db (NEVER the real metrics.db): the idle round-trip
mark_build_complete -> mark_resumed -> derived idle_seconds; the mark_resumed
guard (unset-only AND build-complete-required); record_away_watch's two new
UPSERT-PRESERVING kwargs; and additive migration of a pre-existing table.

Run:  cd <repo root> && python3 -m pytest compose_projects/intelligence/retrospective/tests/test_idle_tracking.py -q
"""
from __future__ import annotations

import sqlite3

import pytest

from intelligence.retrospective import db as db_mod


@pytest.fixture()
def db_file(tmp_path):
    return str(tmp_path / "metrics.db")


def _conn(path):
    return db_mod.connect(path)


def _row(conn, label="W"):
    return next(r for r in db_mod.list_away_watches(conn) if r["label"] == label)


def test_idle_round_trip_derives_idle_seconds(db_file):
    """The headline path: stamp build-complete, then resume, and idle_seconds is
    DERIVED (resumed - build_complete), never stored."""
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="W", started_ts=100.0, ended_ts=1000.0)
    # before any stamp: idle is unknown
    assert _row(conn)["idle_seconds"] is None

    assert db_mod.mark_build_complete("W", 1000.0, conn) is True
    # only one end -> still None
    assert _row(conn)["build_complete_ts"] == 1000.0
    assert _row(conn)["idle_seconds"] is None

    assert db_mod.mark_resumed("W", 1600.0, conn) is True
    r = _row(conn)
    assert r["resumed_ts"] == 1600.0
    assert r["idle_seconds"] == 600.0            # 10 min idle, derived
    conn.close()


def test_mark_resumed_requires_build_complete(db_file):
    """A mid-build message (no build_complete_ts yet) must NOT count as a return."""
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="W", started_ts=100.0)
    assert db_mod.mark_resumed("W", 500.0, conn) is False   # guarded: no build-complete
    assert _row(conn)["resumed_ts"] is None
    conn.close()


def test_mark_resumed_is_idempotent(db_file):
    """Only the FIRST inbound after completion stamps; a second is a no-op."""
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="W")
    db_mod.mark_build_complete("W", 1000.0, conn)
    assert db_mod.mark_resumed("W", 1600.0, conn) is True
    assert db_mod.mark_resumed("W", 9999.0, conn) is False  # already set -> no move
    assert _row(conn)["resumed_ts"] == 1600.0               # first value stands
    conn.close()


def test_mark_build_complete_missing_label_is_noop(db_file):
    conn = _conn(db_file)
    assert db_mod.mark_build_complete("nope", 1.0, conn) is False
    conn.close()


def test_record_kwargs_roundtrip_and_derive(db_file):
    """The two new kwargs land and idle_seconds derives from them directly."""
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="W", started_ts=0.0, ended_ts=100.0,
                             build_complete_ts=100.0, resumed_ts=460.0)
    r = _row(conn)
    assert r["build_complete_ts"] == 100.0 and r["resumed_ts"] == 460.0
    assert r["idle_seconds"] == 360.0
    conn.close()


def test_upsert_preserves_stamped_boundaries(db_file):
    """A metrics re-record (kwargs default None) must NOT clobber a stamped
    boundary — the COALESCE upsert-preserving guard."""
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="W", started_ts=100.0)
    db_mod.mark_build_complete("W", 1000.0, conn)
    db_mod.mark_resumed("W", 1600.0, conn)
    # a later metrics refresh that says nothing about idle
    db_mod.record_away_watch(conn, label="W", started_ts=100.0, decisions=7)
    r = _row(conn)
    assert r["decisions"] == 7                    # the refresh landed
    assert r["build_complete_ts"] == 1000.0       # ...but idle boundaries preserved
    assert r["resumed_ts"] == 1600.0
    assert r["idle_seconds"] == 600.0
    conn.close()


def test_negative_idle_is_none(db_file):
    """A resumed_ts before build_complete_ts (should not happen) reads as None,
    never a negative idle — same defensive shape as `elapsed`."""
    conn = _conn(db_file)
    db_mod.record_away_watch(conn, label="W",
                             build_complete_ts=1000.0, resumed_ts=900.0)
    assert _row(conn)["idle_seconds"] is None
    conn.close()


def test_additive_migration_of_preexisting_table(db_file):
    """A DB created before the idle columns is migrated in connect() via ALTER —
    old data preserved, new columns land NULL, then usable."""
    c = sqlite3.connect(db_file)
    c.executescript(
        """CREATE TABLE away_watch (
             id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT,
             label TEXT UNIQUE, started_ts REAL, ended_ts REAL, commits INTEGER,
             subagents INTEGER, tests INTEGER, artifacts TEXT, scene_path TEXT,
             recorded_at REAL, decisions INTEGER, tokens INTEGER, tasks TEXT,
             git_state TEXT);""")
    c.execute("INSERT INTO away_watch (label, tests) VALUES (?,?)", ("old", 99))
    c.commit(); c.close()

    conn = _conn(db_file)                             # connect() migrates
    cols = {row[1] for row in conn.execute("PRAGMA table_info(away_watch)")}
    assert {"build_complete_ts", "resumed_ts"} <= cols
    r = _row(conn, "old")
    assert r["tests"] == 99                           # preserved
    assert r["build_complete_ts"] is None and r["idle_seconds"] is None
    # the migrated table now stamps idle
    db_mod.mark_build_complete("old", 10.0, conn)
    db_mod.mark_resumed("old", 70.0, conn)
    assert _row(conn, "old")["idle_seconds"] == 60.0
    conn.close()
