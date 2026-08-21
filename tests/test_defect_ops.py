"""pytest coverage for `composeio.defect_ops` (report/attempt/resolve/query) —
the Defect Registry graph CORE ([SPEC:CIO-DEFECT-REGISTRY]).

Every test operates on a `tmp_path` COPY of a synthetic fixture corpus — never the
real `defects/defect-graph.lts`. See `python/composeio/defect_ops/selftest.py`
(``python3 -m composeio.defect_ops --selftest``) for the equivalent no-pytest
end-to-end run through the MCP dispatch seam (incl. the SQLite cross-link).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

# importing defect_ops seats the lts-engine path (via decision_ops._enginepath)
from composeio.defect_ops import (
    DangleRefusedError,
    NodeNotFoundError,
    attempt,
    list_defects,
    query,
    report,
    resolve,
    symptom_slug,
)
from composeio.defect_ops.profile import default_profile_path

FIXTURE = """\
---
domain: defect
confidentiality: internal-only
artifact-readiness: system-design
---
# Fixture Defect Corpus [SYSTEM:DEFECT-CORPUS]

> Synthetic fixture for defect_ops pytest coverage. Seeded empty.

- status: fixture
"""

PROFILE = default_profile_path()
SY = "heartbeat get_layout returns a MERGED snapshot from two live GUIs"


@pytest.fixture()
def graph(tmp_path):
    p = tmp_path / "defect-graph.lts"
    p.write_text(FIXTURE, encoding="utf-8")
    return str(p)


# --- stable symptom identity -------------------------------------------------

def test_symptom_slug_is_stable_across_casing_and_spacing():
    a = symptom_slug(SY)
    b = symptom_slug("Heartbeat   get_layout returns a MERGED snapshot from two live GUIS!!")
    assert a == b
    assert a.startswith("DEFECT-")


# --- report ------------------------------------------------------------------

def test_report_creates_then_query_finds(graph):
    r = report(graph, symptom=SY, area="ux_composer/src/composer", profile=PROFILE)
    assert r.ok and r.changed and r.lint_clean is True
    q = query(graph, SY, profile=PROFILE)
    assert q["found"]
    assert q["defect"]["report_count"] == 1
    assert q["defect"]["lifecycle"] == "reported"
    assert q["defect"]["area"] == "ux_composer/src/composer"
    assert q["attempts"] == []


def test_repeat_report_dedups_and_bumps_count(graph):
    report(graph, symptom=SY, profile=PROFILE)
    report(graph, symptom=SY.upper() + "!!", profile=PROFILE)   # same identity
    q = query(graph, SY, profile=PROFILE)
    assert q["defect"]["report_count"] == 2
    # dedup -> exactly ONE defect node
    assert len([d for d in list_defects(graph, profile=PROFILE)]) == 1


def test_query_accepts_symptom_or_defect_id(graph):
    report(graph, symptom=SY, profile=PROFILE)
    did = symptom_slug(SY)
    assert query(graph, did, profile=PROFILE)["found"]
    assert query(graph, SY, profile=PROFILE)["defect"]["id"] == did


# --- attempt: accumulation + the recurrence chain ----------------------------

def test_attempts_accumulate_with_supersedes_chain(graph):
    report(graph, symptom=SY, profile=PROFILE)
    a1 = attempt(graph, symptom=SY, root_cause_hypothesis="race in dispatch",
                 what_changed="seq guard", outcome="recurred", branch="defect/hb-1",
                 why_it_didnt_hold="merge is upstream of dispatch", profile=PROFILE)
    a2 = attempt(graph, symptom=SY, root_cause_hypothesis="no instance lease",
                 what_changed="lease", outcome="held", branch="defect/hb-2",
                 profile=PROFILE)
    assert a1.node_id.endswith("-A1") and a2.node_id.endswith("-A2")
    q = query(graph, SY, profile=PROFILE)
    assert q["defect"]["attempt_count"] == 2
    atts = q["attempts"]
    assert [a["outcome"] for a in atts] == ["recurred", "held"]
    # the anti-re-dig payload: attempt #1's WHY-IT-DIDN'T-HOLD is queryable
    assert "upstream" in atts[0]["why_it_didnt_hold"]
    # the recurrence chain: A2 SUPERSEDES A1
    assert atts[0]["id"] in (atts[1]["supersedes"] or "")


def test_attempt_creates_defect_if_absent(graph):
    # first thing filed is an attempt -> the defect is created so the parent
    # (part-of) edge never dangles
    a = attempt(graph, symptom="brand new symptom never reported",
                root_cause_hypothesis="hunch", outcome="held", profile=PROFILE)
    assert a.changed and a.lint_clean is True
    q = query(graph, "brand new symptom never reported", profile=PROFILE)
    assert q["found"] and q["defect"]["attempt_count"] == 1


def test_recurred_attempt_reopens_defect(graph):
    report(graph, symptom=SY, profile=PROFILE)
    attempt(graph, symptom=SY, outcome="recurred", profile=PROFILE)
    assert query(graph, SY, profile=PROFILE)["defect"]["lifecycle"] == "recurred"


# --- resolve -----------------------------------------------------------------

def test_resolve_verified_then_idempotent(graph):
    report(graph, symptom=SY, profile=PROFILE)
    r = resolve(graph, symptom=SY, status="verified", note="held", profile=PROFILE)
    assert r.changed
    assert query(graph, SY, profile=PROFILE)["defect"]["lifecycle"] == "verified"
    r2 = resolve(graph, symptom=SY, status="verified", profile=PROFILE)
    assert r2.changed is False                     # idempotent


def test_report_after_verified_is_a_recurrence(graph):
    report(graph, symptom=SY, profile=PROFILE)
    resolve(graph, symptom=SY, status="verified", profile=PROFILE)
    report(graph, symptom=SY, note="came back", profile=PROFILE)
    assert query(graph, SY, profile=PROFILE)["defect"]["lifecycle"] == "recurred"


def test_resolve_missing_defect_raises(graph):
    with pytest.raises(NodeNotFoundError):
        resolve(graph, symptom="never filed", status="verified", profile=PROFILE)


# --- no-forward-dangle guard -------------------------------------------------

def test_kin_edge_to_missing_node_is_refused(graph):
    with pytest.raises(DangleRefusedError):
        report(graph, symptom="a defect with a bad kin link",
               related_to="DEFECT-DOES-NOT-EXIST-000000", profile=PROFILE)
    # the write rolled back -> the defect was NOT created
    assert query(graph, "a defect with a bad kin link", profile=PROFILE)["found"] is False


def test_kin_edge_to_existing_node_is_allowed(graph):
    report(graph, symptom="the kin defect", profile=PROFILE)
    kid = symptom_slug("the kin defect")
    report(graph, symptom="the sibling defect", related_to=kid, profile=PROFILE)
    q = query(graph, "the sibling defect", profile=PROFILE)
    assert kid in (q["defect"]["related_to"] or "")
    assert kid in q["kin"]


# --- list / fix-resistant ----------------------------------------------------

def test_list_defects_orders_fix_resistant_first(graph):
    report(graph, symptom="quiet defect", profile=PROFILE)
    report(graph, symptom=SY, profile=PROFILE)
    attempt(graph, symptom=SY, outcome="recurred", profile=PROFILE)
    attempt(graph, symptom=SY, outcome="held", profile=PROFILE)
    defects = list_defects(graph, profile=PROFILE)
    # the corpus header node is excluded; the 2-attempt defect leads
    assert defects[0]["id"] == symptom_slug(SY)
    assert defects[0]["attempt_count"] == 2
    assert all(d["id"] != "DEFECT-CORPUS" for d in defects)
