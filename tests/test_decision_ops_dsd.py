"""W3 acceptance coverage — the DSD folded into the decision-ops surface.

  D5  `send_dsd_for_human_review` is a tool on the decision-ops MCP surface, still
      validating + publishing `_app/dsd/open` (a validation failure publishes
      nothing).
  D6  a DSD save-close mints one `[DEC-*]` node per ANSWERED decision through the
      guarded decision-ops write path, each carrying `dsd-source` + a read-only
      lock, with a deviation from the recommendation recorded AS THE HUMAN WORDED
      IT — tested against a TEMP/FIXTURE graph, NEVER the live one.
  (c) the decision-ops forward-dangle refusal is intact under the new write path.

Every graph write here targets a `tmp_path` fixture graph; a byte-hash tripwire
(as in `test_decision_ops.py`) is unnecessary because this suite never names the
real graph — but we assert the fixture is the only file touched.
"""
from __future__ import annotations

import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

# importing the package first seats the factlattice_engine path (its __init__
# calls ensure_engine_on_path) before we import the linter.
import composeio.decision_ops as decision_ops  # noqa: E402
from composeio.decision_ops import (  # noqa: E402
    DSD_LOCK_MARKER,
    DangleRefusedError,
    add,
    mint_decision_nodes,
    minted_node_id,
    query,
)
from composeio.decision_ops import dsd_review as dr  # noqa: E402
from composeio.decision_ops.mcp_server import DecisionOpsMcp, Server  # noqa: E402
from composeio.render.widgets import DsdFormWidget  # noqa: E402
from composeio.render.widgets.dsd_document import (  # noqa: E402
    DecisionAnswer, parse_dsd)
from factlattice_engine.lint import lint  # noqa: E402


FIXTURE_GRAPH = """\
---
domain: PLT.CUX
confidentiality: internal-only
artifact-readiness: system-design
created: 2026-07-01
---
# Fixture Decision Graph [SPEC:FIXTURE-DSD-DECISIONS]

> Synthetic fixture for the W3 DSD-fold tests. Never the real graph.

### [DEC-DSDFIX-ROOT] A root fixture decision so nothing here starts as a dangle
- lifecycle-position: committed
- decided-in-session: 2026-07-01
- importance: medium
- rationale: exists so add() targets have something to point at.
"""

# A DSD with a reco (D1: "Per-app file"), a bold lean row (D2: "Light"), a
# no-recommendation decision (D3), and a read-only no-slot decision (D4).
FIXTURE_DSD = """# DSD — W3 fixture

> preamble.

### D1 · Which store?
**Why it matters:** downstream windows.

| Option | Tradeoffs |
|---|---|
| Per-app file | isolated per window |
| Shared store | scales to the other windows |

**My recommendation:** Per-app file

**Your call:** _______

### D2 · Which cadence?
| Option | Tradeoffs |
|---|---|
| Heavy | slow but thorough |
| **Light** | fast, retune later |

**My recommendation:** the lighter cadence — less ceremony.

**Your call:** _______

### D3 · No lean here?
| Option | Tradeoffs |
|---|---|
| Alpha | option a |
| Beta | option b |

**Your call:** _______
"""


@pytest.fixture
def graph(tmp_path) -> str:
    p = tmp_path / "fixture-dsd-decisions.lts"
    p.write_text(FIXTURE_GRAPH, encoding="utf-8")
    return str(p)


# ===========================================================================
# D5 — send_dsd_for_human_review on the decision-ops MCP surface
# ===========================================================================
def test_d5_tools_list_advertises_send_dsd():
    srv = Server(DecisionOpsMcp(default_graph=None, profile=None))
    tl = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in tl["result"]["tools"]}
    assert "send_dsd_for_human_review" in names
    # the four originals are still there — this is a fold-in, not a replacement.
    assert {"decision_add", "decision_update", "decision_query",
            "decision_resolve"} <= names


def test_d5_dispatch_validates_and_publishes(monkeypatch):
    # A CONTEXT-COMPLETE DSD.  The retained historical DSD that used to stand
    # here is correctly REFUSED by [CXC-DSDV-CONTEXT-GUARD] (it predates the
    # 2026-08-17 ruling), so it can no longer stand for "a valid send".
    real_dsd = (dr.REPO_ROOT / "python" / "tests" / "render" / "fixtures"
                / "dsd" / "complete.md")
    if not real_dsd.is_file():
        pytest.skip("fixture DSD not present on this checkout")

    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))

    ops = DecisionOpsMcp(default_graph=None, profile=None)
    res = ops.dispatch("send_dsd_for_human_review",
                       {"dsd_path": str(real_dsd), "correlation_id": "c1"})
    assert res["success"] is True and res["status"] == "sent"
    assert res["correlation_id"] == "c1"
    assert len(published) == 1
    assert published[0]["path"] == dr.resolve_dsd(str(real_dsd))
    assert published[0]["correlation_id"] == "c1"


def test_d5_dispatch_invalid_path_publishes_nothing(monkeypatch):
    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    ops = DecisionOpsMcp(default_graph=None, profile=None)
    res = ops.dispatch("send_dsd_for_human_review", {"dsd_path": "/etc/passwd"})
    assert res["success"] is False and res["status"] == "error"
    assert not published


# ===========================================================================
# [CXC-DSDV-CONTEXT-GUARD] (David 2026-08-17) — a DSD any decision of which
# lacks `What this is` / `Why it matters` is REFUSED BEFORE ANY INTENT IS
# PUBLISHED, and the refusal NAMES the offending decisions.
#
# Fixture 1 is THE REAL ARTEFACT that reached the human's glass undecidable:
# `docs/architecture/DECISIONS-2026-08-17-migration-and-programme.md`, whose D2
# ("The Turbot images — keep, relocate, or discard?") carried no gloss, so the
# reader could not tell whether "images" meant files on buildhost, Docker images,
# or something ComposeIO-specific. It parsed and rendered perfectly. If the guard
# does not reject THAT file, it does not work.
#
# Every refusal test below is paired with its NEGATIVE CONTROL
# ([CXC-VISION-EPISTEMICS] rule-negative-control): an instrument that can only
# report failure is no better than one that can only report success.
# ===========================================================================
_FIXTURE_DIR = (Path(__file__).parent.parent / "python" / "tests" / "render"
                / "fixtures" / "dsd")
_THE_REAL_FAILURE = (Path(__file__).parent.parent / "docs" / "architecture"
                     / "DECISIONS-2026-08-17-migration-and-programme.md")


def test_context_guard_refuses_the_real_artefact_that_got_through(monkeypatch):
    if not _THE_REAL_FAILURE.is_file():
        pytest.skip("the 2026-08-17 artefact is not on this checkout")
    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    res = dr.send_dsd_for_human_review(str(_THE_REAL_FAILURE))
    assert res["status"] == "error"
    assert not published, "a defective DSD must not reach the bus at all"
    # the refusal is USEFUL: it names the decision and the field.
    assert ("D2", "what_this_is") in {(m["decision_id"], m["field"])
                                      for m in res["missing"]}
    assert "Turbot" in res["message"] and "What this is" in res["message"]


def test_context_guard_refuses_the_pinned_excerpt_of_that_artefact(monkeypatch):
    """The same regression, PINNED: a byte-for-byte excerpt of D1+D2 of the
    2026-08-17 artefact, checked in so this never degrades to a skip when the
    working document is absent.  D1 is complete and MUST NOT be named; D2 has no
    `What this is` line at all (a distinct sub-case from a present-but-empty
    label) and MUST be."""
    excerpt = _FIXTURE_DIR / "real_2026_08_17_excerpt.md"
    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    res = dr.send_dsd_for_human_review(str(excerpt))
    assert res["status"] == "error" and not published
    assert [(m["decision_id"], m["field"]) for m in res["missing"]] == [
        ("D2", "what_this_is")]
    assert "Turbot" in res["message"]


def test_context_guard_accepts_a_complete_dsd(monkeypatch):
    """NEGATIVE CONTROL for the test above."""
    good = _FIXTURE_DIR / "complete.md"
    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    res = dr.send_dsd_for_human_review(str(good))
    assert res["status"] == "sent", res["message"]
    assert len(published) == 1


def test_context_guard_treats_a_present_but_empty_label_as_blank(monkeypatch):
    """The bound is STRUCTURAL PRESENCE + NON-EMPTY CONTENT. A label with no
    prose after it, and one whose prose strips to nothing, are both BLANK; the
    complete decision in the same file is NOT named."""
    blank = _FIXTURE_DIR / "blank_label.md"
    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    res = dr.send_dsd_for_human_review(str(blank))
    assert res["status"] == "error" and not published
    assert {(m["decision_id"], m["field"]) for m in res["missing"]} == {
        ("D2", "what_this_is"), ("D3", "why_it_matters")}


# ===========================================================================
# [CXC-DSDV-MCP]/[CXC-DSDV-REGISTER] — the PRIMARY DSD-delivery path is trusted
# BY CONSTRUCTION: exposed on the tool surface AND spec-backed, no silent
# fallback. Regression guard for the silent-fallback-over-a-broken-primary
# defect (the documented `open`-the-markdown "fallback" masked a dead endpoint).
# ===========================================================================
DSDV_SPEC = _SPECS / "47-dsd-viewer.lts"


def test_dsdv_endpoint_present_on_both_registration_surfaces():
    """`send_dsd_for_human_review` must be on BOTH the wire `tools/list` AND the
    `dispatch` handler map — dropping it from EITHER is the silent-primary-path
    regression this guards. (A stale RUNTIME client is out of CI's reach; this
    catches the CODE dropping it, on either registration surface.)"""
    srv = Server(DecisionOpsMcp(default_graph=None, profile=None))
    tl = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in tl["result"]["tools"]}
    assert "send_dsd_for_human_review" in names, "dropped from tools/list"
    # dispatchable: an unknown tool returns the 'unknown ... tool' sentinel; the
    # real tool returns a validation result instead of that sentinel.
    ops = DecisionOpsMcp(default_graph=None, profile=None)
    res = ops.dispatch("send_dsd_for_human_review", {"dsd_path": ""})
    assert "unknown" not in (res.get("error") or "").lower(), \
        "send_dsd_for_human_review dropped from the dispatch handler map"


def test_dsdv_primary_path_is_spec_backed_no_silent_fallback():
    """The endpoint's exposure + no-silent-fallback invariant must live in the
    SPEC, not only in code — the under-spec that let a dead primary path hide
    behind the documented fallback. FAILS if the governing spec node is absent
    (which is exactly the state that let this defect ship undetected)."""
    assert DSDV_SPEC.is_file(), (
        f"missing governing spec {DSDV_SPEC} — the DSD endpoint's exposure/"
        "no-silent-fallback invariant is code-only (under-spec)")
    text = DSDV_SPEC.read_text(encoding="utf-8")
    assert "[SPEC:CXC-DSDV]" in text
    assert "[CXC-DSDV-MCP]" in text and "[CXC-DSDV-REGISTER]" in text
    assert "send_dsd_for_human_review" in text
    # the load-bearing invariant: primary path, no SILENT FALLBACK ([SBR-TRUST]).
    low = text.lower()
    assert "silent" in low and "fallback" in low
    assert "[SBR-TRUST]" in text


# ===========================================================================
# D6 — mint [DEC-*] nodes from DSD answers, through the guarded write path
# ===========================================================================
def test_d6_mints_one_node_per_answered_decision(graph, tmp_path):
    dsd_path = str(tmp_path / "DECISIONS-w3-fixture.md")
    doc = parse_dsd(FIXTURE_DSD, path=dsd_path)
    # D1 DEVIATES (picks the non-recommended option) with a verbatim override;
    # D2 accepts the recommendation; D3 is left unanswered.
    override = "Overriding the rec — I need cross-window sharing right now."
    answers = {
        "D1": DecisionAnswer(labels=["Shared store"], comment=override),
        "D2": DecisionAnswer(labels=["Light"], comment=""),
        # D3 present-but-empty, exactly as the widget's answers() emits it.
        "D3": DecisionAnswer(labels=[], comment=""),
    }

    results = mint_decision_nodes(graph, dsd_path, doc.decisions, answers,
                                  today="2026-07-25")

    # exactly one minted node per ANSWERED decision (D1, D2); D3 skipped.
    minted = [r for r in results if r["status"] == "minted"]
    assert {r["decision_id"] for r in minted} == {"D1", "D2"}
    d3 = [r for r in results if r["decision_id"] == "D3"]
    assert d3 and d3[0]["status"] == "skipped"

    text = Path(graph).read_text(encoding="utf-8")
    d1_id = minted_node_id(dsd_path, "D1")
    d2_id = minted_node_id(dsd_path, "D2")
    assert f"### [{d1_id}]" in text and f"### [{d2_id}]" in text
    # exactly two DEC-DSD- nodes minted (one per answered decision).
    assert text.count("### [DEC-DSD-") == 2

    # the D1 node: dsd-source pointer + read-only lock + as-worded deviation.
    q = query(graph, f"id:{d1_id}")
    assert len(q) == 1
    node_block = _node_block(text, d1_id)
    assert f"- dsd-source: {Path(dsd_path).name}" not in node_block  # rel, not name-only
    assert "- dsd-source:" in node_block and "DECISIONS-w3-fixture.md#D1" in node_block
    assert f"- read-only-lock: {DSD_LOCK_MARKER}" in node_block
    assert "- deviates-from-recommendation: yes" in node_block
    # the deviation is recorded AS THE HUMAN WORDED IT (verbatim), both as its
    # own bullet and in the rationale.
    assert override in node_block
    assert q[0]["rationale"] and override in q[0]["rationale"]

    # the D2 node accepted the reco: no deviation, chosen recorded.
    d2_block = _node_block(text, d2_id)
    assert "- deviates-from-recommendation: no" in d2_block
    assert "- answer: Light" in d2_block

    # the mint introduced NO new forward dangle (the nodes carry no edges).
    assert not lint(graph, dangle=True).has_errors


def test_d6_mint_is_idempotent_on_rerun(graph, tmp_path):
    dsd_path = str(tmp_path / "DECISIONS-idem.md")
    doc = parse_dsd(FIXTURE_DSD, path=dsd_path)
    answers = {"D2": DecisionAnswer(labels=["Light"], comment="")}
    r1 = mint_decision_nodes(graph, dsd_path, doc.decisions, answers, today="2026-07-25")
    before = Path(graph).read_text(encoding="utf-8")
    r2 = mint_decision_nodes(graph, dsd_path, doc.decisions, answers, today="2026-07-25")
    assert r1[0]["status"] == "minted" if r1 else True
    assert any(r["status"] == "idempotent" for r in r2)
    assert Path(graph).read_text(encoding="utf-8") == before  # byte-identical re-run


def test_d6_disabled_when_no_graph_configured(tmp_path):
    dsd_path = str(tmp_path / "DECISIONS-nograph.md")
    doc = parse_dsd(FIXTURE_DSD, path=dsd_path)
    answers = {"D2": DecisionAnswer(labels=["Light"], comment="")}
    # a falsy graph path is the safe default: nothing minted, nothing raised.
    assert mint_decision_nodes("", dsd_path, doc.decisions, answers) == []


# ===========================================================================
# D6 end-to-end — the widget save-close drives the mint against a fixture graph
# ===========================================================================
def test_d6_widget_save_close_mints_against_fixture_graph(graph, tmp_path,
                                                          monkeypatch):
    dsd_path = tmp_path / "DECISIONS-e2e.md"
    dsd_path.write_text(FIXTURE_DSD, encoding="utf-8")
    monkeypatch.setenv("COMPOSEIO_DSD_DECISION_GRAPH", graph)

    w = DsdFormWidget("w-e2e", path=str(dsd_path))
    # DEVIATE on D1 (pick the non-recommended option) + write a verbatim comment.
    d1 = w.doc.decisions[0]
    non_reco = [o.option_id for o in d1.options if o.option_id != d1.preselect_id][0]
    w._selected["D1"] = {non_reco}
    override = "override at the console — shared store wins today."
    w._comment_fields["D1"].text = override
    # D3 has no recommendation -> answer it so the completeness gate
    # ([CXC-DSDV-COMPLETE]) permits the save-close.
    w._selected["D3"] = {w.doc.decisions[2].options[0].option_id}

    payload = w.save()
    assert payload is not None                        # save-close ran

    # existing behavior intact: the DSD slot was written back.
    dsd_after = dsd_path.read_text(encoding="utf-8")
    assert "**Your call:** Shared store" in dsd_after

    # D6: the graph now carries one [DEC-*] node per answered decision.
    text = Path(graph).read_text(encoding="utf-8")
    d1_id = minted_node_id(str(dsd_path), "D1")
    d2_id = minted_node_id(str(dsd_path), "D2")
    assert f"### [{d1_id}]" in text and f"### [{d2_id}]" in text
    d1_block = _node_block(text, d1_id)
    assert f"- read-only-lock: {DSD_LOCK_MARKER}" in d1_block
    assert "DECISIONS-e2e.md#D1" in d1_block
    assert "- deviates-from-recommendation: yes" in d1_block
    assert override in d1_block                       # as-worded
    assert not lint(graph, dangle=True).has_errors


def test_d6_widget_save_close_no_graph_leaves_graph_untouched(tmp_path,
                                                              monkeypatch):
    """With no graph configured, save-close writes back the DSD but mints
    NOTHING — the pre-existing behavior is preserved exactly."""
    monkeypatch.delenv("COMPOSEIO_DSD_DECISION_GRAPH", raising=False)
    monkeypatch.delenv("DECISION_OPS_GRAPH", raising=False)
    dsd_path = tmp_path / "DECISIONS-nowrite.md"
    dsd_path.write_text(FIXTURE_DSD, encoding="utf-8")
    w = DsdFormWidget("w-nw", path=str(dsd_path))
    # answer D3 (no recommendation) so the completeness gate permits the save.
    w._selected["D3"] = {w.doc.decisions[2].options[0].option_id}
    payload = w.save()
    assert payload is not None
    assert "**Your call:** Per-app file" in dsd_path.read_text(encoding="utf-8")


# ===========================================================================
# (c) — the forward-dangle refusal is NOT broken by the new write path
# ===========================================================================
def test_c_guard_intact_after_new_write_path(graph, tmp_path):
    # first, the new write path lands real nodes on the graph.
    dsd_path = str(tmp_path / "DECISIONS-guard.md")
    doc = parse_dsd(FIXTURE_DSD, path=dsd_path)
    mint_decision_nodes(graph, dsd_path,
                        doc.decisions,
                        {"D2": DecisionAnswer(labels=["Light"], comment="")},
                        today="2026-07-25")
    before = Path(graph).read_text(encoding="utf-8")

    # the SAME guarded add() the mint routes through still REFUSES a new forward
    # dangle and rolls back byte-for-byte.
    with pytest.raises(DangleRefusedError):
        add(graph, {"id": "DEC-DSDFIX-BAD", "title": "points nowhere",
                    "lifecycle-position": "raised", "decided-in-session": "2026-07-25",
                    "importance": "low", "rationale": "deliberately dangling.",
                    "depends-on": "[DEC-DOES-NOT-EXIST-ANYWHERE]"})
    assert Path(graph).read_text(encoding="utf-8") == before
    assert query(graph, "id:DEC-DSDFIX-BAD") == []
    assert not lint(graph, dangle=True).has_errors


def test_c_mint_is_best_effort_when_add_refuses(graph, tmp_path):
    """A refusal in the write path is recorded, not raised — the DSD save-close
    must never be broken by a graph-write refusal."""
    dsd_path = str(tmp_path / "DECISIONS-refuse.md")
    doc = parse_dsd(FIXTURE_DSD, path=dsd_path)

    def refusing_add(*_a, **_k):
        raise DangleRefusedError("simulated refusal", ["1 new forward dangle"])

    before = Path(graph).read_text(encoding="utf-8")
    results = mint_decision_nodes(
        graph, dsd_path, doc.decisions,
        {"D1": DecisionAnswer(labels=["Shared store"], comment="x")},
        add_fn=refusing_add)
    assert any(r["status"] == "refused" for r in results)   # recorded
    assert Path(graph).read_text(encoding="utf-8") == before  # nothing written


def _node_block(text: str, node_id: str) -> str:
    """The lines of one `### [id] …` node block (heading through the line before
    the next heading), for precise per-node assertions."""
    lines = text.split("\n")
    start = None
    for i, ln in enumerate(lines):
        if ln.startswith(f"### [{node_id}]"):
            start = i
            break
    assert start is not None, f"node {node_id} not found"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("#"):
            end = j
            break
    return "\n".join(lines[start:end])


# ===========================================================================
# [CXC-DSDV-EXTRA-ROOTS] (requested 2026-08-17 by the R503 composeux-architect)
# — WHERE a DSD may live is CONFIGURATION; whether it is FIT TO PUBLISH is not.
#
# `_allowed_dsd_roots()` computed three roots and every one of them was
# composeio, so a design project staged outside the checkout (David's standing
# ruling; not under git at all) had no DSD-delivery path — and the workaround in
# use was COPYING the DSD into composeio purely to satisfy the check. Routing
# around a guard by moving the artefact is the symptom of a root list that is
# too narrow, not of a guard that is too strict.
#
# ⛔ EVERY WIDENING TEST HERE IS PAIRED WITH ITS REFUSAL, RUN UNDER THE SAME
# CONFIGURATION ([CXC-VISION-EPISTEMICS] rule-negative-control). A root list
# observed only admitting things has not been shown to refuse anything — and the
# failure mode this change had to avoid (returning the resolved path
# unconditionally) passes every positive arm and no negative one.
# ===========================================================================
_OUTSIDE_DSD = """# DSD — staged outside the checkout

> A DSD that lives where the project lives, not where the tool lives.

### D1 · Can a DSD live outside composeio?
**What this is:** the containment-root question, posed as a document.
**Why it matters:** the alternative was copying it into someone else's tree.

| Option | Tradeoffs |
|---|---|
| Configure a root | the guard stays on |
| Copy the file in | routes around the guard |

**My recommendation:** Configure a root

**Your call:** _______
"""


@pytest.fixture
def outside_dsd(tmp_path):
    """A real, parseable, context-complete DSD under a directory that is not any
    repo root — and whose path carries a SPACE.

    THE SPACE IS THE POINT, not decoration: the motivating path is
    `${DATASHEETS_HOME}/R503`, and a whitespace-separated root list
    would split it into two roots that do not exist. `os.pathsep` survives it.
    This project has been bitten by that exact path already (PlatformIO refuses
    it outright, which is why the R503 firmware builds via a mirror)."""
    root = tmp_path / "Data Sheets" / "R503"
    root.mkdir(parents=True)
    p = root / "DECISIONS-outside.md"
    p.write_text(_OUTSIDE_DSD, encoding="utf-8")
    return root, p


def _configure(monkeypatch, *roots):
    monkeypatch.setenv(dr.DSD_ROOTS_ENV,
                       os.pathsep.join(str(r) for r in roots))


# ---- 1. POSITIVE ----------------------------------------------------------
def test_extra_root_admits_a_dsd_outside_the_checkout(monkeypatch, outside_dsd):
    root, dsd = outside_dsd
    assert dr.resolve_dsd(str(dsd)) is None, (
        "precondition: without configuration this is refused — otherwise the "
        "assertion below proves nothing")

    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    _configure(monkeypatch, root)
    res = dr.send_dsd_for_human_review(str(dsd))
    assert res["status"] == "sent", res["message"]
    assert published == [{"path": str(dsd.resolve()),
                          "correlation_id": res["correlation_id"]}]


def test_the_widening_is_scoped_to_the_configuration(monkeypatch, outside_dsd):
    """It admits THAT root, not "outside" in general — the neighbouring file one
    level up from the configured root is still refused."""
    root, dsd = outside_dsd
    sibling = root.parent / "NEIGHBOUR.md"
    sibling.write_text(_OUTSIDE_DSD, encoding="utf-8")
    _configure(monkeypatch, root)
    assert dr.resolve_dsd(str(dsd)) == str(dsd.resolve())
    assert dr.resolve_dsd(str(sibling)) is None


# ---- 2. ⛔ NEGATIVE — the arm that matters ---------------------------------
def test_etc_passwd_is_still_refused_with_an_extra_root_configured(
        monkeypatch, outside_dsd):
    """⛔ THE ARM THAT MATTERS. The SAME configuration as the positive test
    above, live in the same process: containment is WIDENED, never REMOVED."""
    root, dsd = outside_dsd
    published = []
    monkeypatch.setattr(dr, "_publish_intent_mqtt",
                        lambda url, payload: published.append(payload))
    _configure(monkeypatch, root)
    # the configuration really is active — else this refusal proves nothing.
    assert dr.resolve_dsd(str(dsd)) is not None

    assert dr.resolve_dsd("/etc/passwd") is None
    res = dr.send_dsd_for_human_review("/etc/passwd")
    assert res["status"] == "error"
    assert "not a repo file" in res["message"]
    assert not published, "an out-of-root path must not reach the bus at all"


def test_a_relative_or_filesystem_root_entry_is_refused_not_honoured(
        monkeypatch, outside_dsd):
    """The two entry shapes that would switch the guard OFF while still looking
    like configuration: `/` contains every path on the machine, and a relative
    entry silently means "under whatever cwd this process happens to have"."""
    root, _dsd = outside_dsd
    accepted, rejected = dr.configured_dsd_roots(
        os.pathsep.join(["docs", "/", "", str(root)]))
    assert accepted == [root.resolve()]
    assert {e for e, _why in rejected} == {"docs", "/"}

    # end to end: `/` alone admits NOTHING — and the refusal SAYS SO, so a typo
    # is not indistinguishable from having configured nothing at all.
    monkeypatch.setenv(dr.DSD_ROOTS_ENV, "/")
    assert dr.resolve_dsd("/etc/passwd") is None
    res = dr.send_dsd_for_human_review("/etc/passwd")
    assert res["status"] == "error"
    assert "REFUSED" in res["message"] and dr.DSD_ROOTS_ENV in res["message"]


def test_an_empty_entry_does_not_admit_the_current_directory(monkeypatch,
                                                             tmp_path):
    """`Path("").resolve()` is cwd — the widest accidental root there is, and
    the one a trailing `os.pathsep` produces by accident."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sneak.md").write_text(_OUTSIDE_DSD, encoding="utf-8")
    monkeypatch.setenv(dr.DSD_ROOTS_ENV, os.pathsep + os.pathsep + "  ")
    assert dr.configured_dsd_roots()[0] == []
    assert dr.resolve_dsd(str(tmp_path / "sneak.md")) is None


# ---- 3. UNCHANGED ---------------------------------------------------------
def test_an_in_repo_dsd_resolves_identically_with_and_without_config(
        monkeypatch, outside_dsd):
    """ARM 3. The same answer before and after — including the repo-RELATIVE
    form, which must still bind to the checkout and not to the configured root
    (repo roots are tried first)."""
    root, _dsd = outside_dsd
    in_repo = (dr.REPO_ROOT / "python" / "tests" / "render" / "fixtures"
               / "dsd" / "complete.md")
    if not in_repo.is_file():
        pytest.skip("fixture DSD not present on this checkout")
    rel = "python/tests/render/fixtures/dsd/complete.md"

    before_abs, before_rel = dr.resolve_dsd(str(in_repo)), dr.resolve_dsd(rel)
    assert before_abs is not None and before_rel == before_abs

    _configure(monkeypatch, root)
    assert dr.resolve_dsd(str(in_repo)) == before_abs
    assert dr.resolve_dsd(rel) == before_rel


def test_the_computed_roots_are_still_all_there(monkeypatch, outside_dsd):
    """The configured roots are ADDED, never substituted, and they go LAST so a
    repo root always wins the relative-path candidate race."""
    root, _dsd = outside_dsd
    before = dr._allowed_dsd_roots()
    _configure(monkeypatch, root)
    after = dr._allowed_dsd_roots()
    assert after[:len(before)] == before
    assert after[-1] == root.resolve()


def test_a_missing_path_is_still_refused_under_every_root(monkeypatch,
                                                          outside_dsd):
    """NEGATIVE CONTROL for arm 3: containment is not the only condition — a
    path inside a root that is not a FILE is still None, on both sides."""
    root, _dsd = outside_dsd
    _configure(monkeypatch, root)
    assert dr.resolve_dsd(str(dr.REPO_ROOT / "docs" / "nope-not-here.md")) is None
    assert dr.resolve_dsd(str(root / "also-absent.md")) is None
    assert dr.resolve_dsd(str(root)) is None, "a DIRECTORY is not a DSD"
    assert dr.resolve_dsd("") is None


# ---- 4. SIBLINGS ----------------------------------------------------------
def test_the_three_review_surfaces_share_one_resolver():
    """Not a behavioural coincidence — the SAME function object. This is what
    makes one change reach all three, and it fails loudly if anyone forks it."""
    from composeio.decision_ops import beepout_review as br
    from composeio.decision_ops import hookup_review as hr
    assert hr.resolve_repo_file is dr.resolve_dsd
    assert br._resolve_repo_file is dr.resolve_dsd


def test_siblings_inherit_the_widening_and_the_refusal(monkeypatch,
                                                       outside_dsd):
    """One smoke each, BOTH arms. `resolve_scene` / `resolve_sheet` are the
    public delegates the hookup and beep-out surfaces expose."""
    from composeio.decision_ops import beepout_review as br
    from composeio.decision_ops import hookup_review as hr
    root, _dsd = outside_dsd
    scene = root / "hookup.yaml"
    sheet = root / "beepout.md"
    scene.write_text("nodes: []\n", encoding="utf-8")
    sheet.write_text("# sheet\n", encoding="utf-8")

    assert hr.resolve_scene(str(scene)) is None          # before
    assert br.resolve_sheet(str(sheet)) is None

    _configure(monkeypatch, root)
    assert hr.resolve_scene(str(scene)) == str(scene.resolve())
    assert br.resolve_sheet(str(sheet)) == str(sheet.resolve())
    # ...and the refusal came along with it.
    assert hr.resolve_scene("/etc/passwd") is None
    assert br.resolve_sheet("/etc/passwd") is None


# ---- the status message names the file usefully ---------------------------
def test_an_outside_dsd_is_named_absolutely_not_as_a_dotdot_path(
        monkeypatch, outside_dsd):
    """`os.path.relpath(abspath, REPO_ROOT)` answered `../../Data Sheets/...`
    once a configured root became reachable — a string that LOOKS repo-relative,
    resolves from nowhere in particular, and names the wrong project. In-repo
    messages are unchanged (see the arm-3 test)."""
    root, dsd = outside_dsd
    monkeypatch.setattr(dr, "_publish_intent_mqtt", lambda url, payload: None)
    _configure(monkeypatch, root)
    res = dr.send_dsd_for_human_review(str(dsd))
    assert res["status"] == "sent", res["message"]
    assert str(dsd.resolve()) in res["message"]
    assert ".." not in res["message"]
