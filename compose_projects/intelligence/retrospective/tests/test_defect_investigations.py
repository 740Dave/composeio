"""Acceptance tests for the DEFECT-INVESTIGATION store ([DEC-DEFECT-TRACKING-STORE],
[SPEC:CXC-DEFECT-STORE]).

Proves, against a TEMP db (NEVER the real metrics.db): a defect record round-trips
the full agent deliverable; root_cause_class normalizes to the taxonomy; bool flags
store as 0/1; and `defects_by_root_cause()` is the count-desc histogram that IS the
process-improvement signal (with an optional `since` window).

Run:  cd <repo root> && python3 -m pytest compose_projects/intelligence/retrospective/tests/test_defect_investigations.py -q
"""
from __future__ import annotations

import pytest

from intelligence.retrospective import db as db_mod


@pytest.fixture()
def db_file(tmp_path):
    return str(tmp_path / "metrics.db")


def _conn(path):
    return db_mod.connect(path)


def test_record_round_trip(db_file):
    """One investigation records every deliverable field and reads back verbatim
    (bools as 0/1, class normalized)."""
    conn = _conn(db_file)
    rid = db_mod.record_defect_investigation(
        conn,
        symptom="done-chips don't disappear",
        area="intelligence/console/intelligence_card.py",
        root_cause_class="fork-divergence",
        recurrence=True,
        prior_fix_ref="20856df",
        under_spec=True,
        fork=True,
        spec_added="[CXC-ICV-SUBAGENTS]",
        test_added=True,
        resolution="de-fork prune to shared subagent-chip model",
        branch="defect/subagent-chip-prune",
        ts=1000.0)
    assert rid > 0
    r = db_mod.list_defect_investigations(conn)[0]
    assert r["symptom"] == "done-chips don't disappear"
    assert r["area"] == "intelligence/console/intelligence_card.py"
    assert r["root_cause_class"] == "fork-divergence"
    assert (r["recurrence"], r["under_spec"], r["fork"], r["test_added"]) == (1, 1, 1, 1)
    assert r["prior_fix_ref"] == "20856df"
    assert r["spec_added"] == "[CXC-ICV-SUBAGENTS]"
    assert r["branch"] == "defect/subagent-chip-prune"
    conn.close()


def test_root_cause_normalization(db_file):
    """The agent's `implementation-oversight` collapses to `oversight`; an unknown
    class -> `other` (conservative, never dropped)."""
    conn = _conn(db_file)
    db_mod.record_defect_investigation(conn, symptom="a",
                                       root_cause_class="implementation-oversight")
    db_mod.record_defect_investigation(conn, symptom="b",
                                       root_cause_class="gremlins")
    db_mod.record_defect_investigation(conn, symptom="c",
                                       root_cause_class="UNDER-SPEC")
    classes = {r["root_cause_class"] for r in db_mod.list_defect_investigations(conn)}
    assert classes == {"oversight", "other", "under-spec"}
    conn.close()


def test_defaults_and_bool_coercion(db_file):
    """Only `symptom` is required; flags default False -> 0; class defaults other."""
    conn = _conn(db_file)
    db_mod.record_defect_investigation(conn, symptom="minimal")
    r = db_mod.list_defect_investigations(conn)[0]
    assert r["root_cause_class"] == "other"
    assert (r["recurrence"], r["under_spec"], r["fork"], r["test_added"]) == (0, 0, 0, 0)
    assert r["area"] == "" and r["branch"] == "" and r["spec_added"] == ""
    assert isinstance(r["ts"], float)             # auto-stamped
    conn.close()


def test_by_root_cause_rollup_is_count_desc_histogram(db_file):
    """The rollup IS the process signal: {class: count} ordered by count desc."""
    conn = _conn(db_file)
    for _ in range(3):
        db_mod.record_defect_investigation(conn, symptom="x", root_cause_class="under-spec")
    for _ in range(2):
        db_mod.record_defect_investigation(conn, symptom="y", root_cause_class="fork-divergence")
    db_mod.record_defect_investigation(conn, symptom="z", root_cause_class="spec-drift")

    hist = db_mod.defects_by_root_cause(conn=conn)
    assert hist == {"under-spec": 3, "fork-divergence": 2, "spec-drift": 1}
    # ordering is count-desc (a histogram readout, tallest bar first)
    assert list(hist.keys()) == ["under-spec", "fork-divergence", "spec-drift"]
    conn.close()


def test_by_root_cause_since_window(db_file):
    """`since` filters the histogram to the recent window (ts >= since)."""
    conn = _conn(db_file)
    db_mod.record_defect_investigation(conn, symptom="old", root_cause_class="other", ts=100.0)
    db_mod.record_defect_investigation(conn, symptom="new", root_cause_class="under-spec", ts=5000.0)
    assert db_mod.defects_by_root_cause(since=1000.0, conn=conn) == {"under-spec": 1}
    assert db_mod.defects_by_root_cause(conn=conn) == {"under-spec": 1, "other": 1}
    conn.close()


def test_empty_store_rollup_is_empty(db_file):
    conn = _conn(db_file)
    assert db_mod.defects_by_root_cause(conn=conn) == {}
    conn.close()


def test_records_are_appended_not_upserted(db_file):
    """Each investigation is a distinct event (like token_spikes) — two records of
    the same symptom are two rows, not an upsert."""
    conn = _conn(db_file)
    db_mod.record_defect_investigation(conn, symptom="same", root_cause_class="other")
    db_mod.record_defect_investigation(conn, symptom="same", root_cause_class="other")
    assert len(db_mod.list_defect_investigations(conn)) == 2
    conn.close()


# --- the defect_id graph<->SQLite cross-link ([SPEC:CIO-DEFECT-REGISTRY]) -----

def test_defect_id_cross_link_round_trips(db_file):
    """The additive defect_id column carries the graph DEFECT node id so an
    aggregate row cross-links to its recurrence-graph node; defaults to ''."""
    conn = _conn(db_file)
    db_mod.record_defect_investigation(
        conn, symptom="hb merge", root_cause_class="under-spec",
        defect_id="DEFECT-HEARTBEAT-GET-LAYOUT-7EE05D")
    db_mod.record_defect_investigation(conn, symptom="no link")
    rows = {r["symptom"]: r for r in db_mod.list_defect_investigations(conn)}
    assert rows["hb merge"]["defect_id"] == "DEFECT-HEARTBEAT-GET-LAYOUT-7EE05D"
    assert rows["no link"]["defect_id"] == ""          # default, never NULL-crashes
    conn.close()


def test_defect_id_migration_is_additive(tmp_path):
    """A defect_investigations table created BEFORE the defect_id column existed
    is brought up additively by connect() (ALTER ADD COLUMN), preserving old rows."""
    import sqlite3
    p = str(tmp_path / "old.db")
    old = sqlite3.connect(p)
    old.executescript(
        """CREATE TABLE defect_investigations (
             id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, symptom TEXT, area TEXT,
             root_cause_class TEXT, recurrence INTEGER, prior_fix_ref TEXT,
             under_spec INTEGER, fork INTEGER, spec_added TEXT, test_added INTEGER,
             resolution TEXT, branch TEXT);""")
    old.execute("INSERT INTO defect_investigations (symptom) VALUES ('legacy')")
    old.commit(); old.close()
    conn = db_mod.connect(p)                            # triggers the migration
    cols = {row[1] for row in conn.execute("PRAGMA table_info(defect_investigations)")}
    assert "defect_id" in cols
    assert len(db_mod.list_defect_investigations(conn)) == 1   # legacy row survived
    conn.close()
