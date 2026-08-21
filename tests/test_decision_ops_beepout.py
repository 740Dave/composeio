"""The beep-out surface, pinned.

Seven classes, mirroring the hookup suite's shape because this is its sibling:

* **B1** the endpoint exists on BOTH registration surfaces (the built-and-never-registered
  class).
* **B2** a HAND-AUTHORED sheet is refused — the whole point of the instrument.
* **B3** a path outside the repo publishes nothing.
* **B4** a STALE sheet (netlist has changed) is refused. This is the enforcement of a
  sync-contract that was previously only a convention.
* **B5** a HAND-EDITED sheet (rows no longer match a fresh derivation) is refused.
* **B6** a sheet with ZERO rows is refused — a document that cannot fail is not an
  instrument.
* **B7** the derivation is NOT re-implemented in the review module (the anti-fork test).

Plus **NC1–NC4**, the negative controls. These are the tests that decide whether the
generator is an instrument or a claim: each mutates a REAL netlist so it describes a board
with a known defect, and asserts the derived output CHANGES in the way that would catch it.
A generator whose first run produced a plausible sheet and was never seen to fail is a
claim; ``[CXC-VISION-EPISTEMICS]`` ``rule-negative-control``.
"""

from __future__ import annotations

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import json
import re
from pathlib import Path

import pytest

from composeio.decision_ops import beepout_review as br
from composeio.decision_ops import mcp_server
from composeio.maker.beepout import derive_sheet, parse_board
from composeio.maker.beepout.derive import Category, RowState
from composeio.maker.beepout.mutate import (break_net_pad, displace_pad, materialise,
                                            short_two_nets)
from composeio.maker.beepout.render import render_markdown

#: ⛔ THE TREE THIS TEST LIVES IN, not `beepout_review.REPO_ROOT`.
#: REPO_ROOT deliberately resolves to the CANONICAL checkout so decision-graph writes
#: cannot land on a branch. A test that used it would read and WRITE inside the live
#: checkout while claiming to exercise this worktree — plausible-looking wrong output, and
#: the exact INTERPRETER/FILES divergence [INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT] names.
#: Measured here 2026-08-15: pytest imports `composeio` from the worktree while REPO_ROOT
#: points at the canonical tree, so the two disagree inside one process.
TREE = Path(__file__).resolve().parents[1]

PCB = Path("/Users/davidleeburnett/Dropbox/Maker/pcb")
GAUGE = PCB / "rack_triple_gauge" / "rack_triple_gauge.kicad_pcb"
BUTTON = PCB / "button_matrix_4x4" / "button_matrix_4x4.kicad_pcb"
LEGO = PCB / "lego_dots" / "lego_dots.kicad_pcb"

pytestmark = pytest.mark.skipif(
    not GAUGE.exists(),
    reason="the pcb source tree is not present on this machine",
)


@pytest.fixture
def sink():
    out: list = []
    return out, out.append


@pytest.fixture(scope="module")
def gauge_board():
    return parse_board(GAUGE)


@pytest.fixture(scope="module")
def gauge_sheet(gauge_board):
    return derive_sheet(gauge_board)


def _write_sheet(tmp_repo_file, sheet) -> str:
    tmp_repo_file.write_text(render_markdown(sheet), encoding="utf-8")
    return str(tmp_repo_file)


@pytest.fixture
def repo_md(tmp_path):
    """A scratch .md INSIDE the repo, because containment is part of the contract."""
    d = TREE / "python" / "tests" / "_beepout_tmp"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "scratch-BEEPOUT.md"
    yield p
    if p.exists():
        p.unlink()
    try:
        d.rmdir()
    except OSError:
        pass


# ------------------------------------------------------------------ B1


def test_b1_endpoint_present_on_both_registration_surfaces():
    names = [t["name"] for t in mcp_server.TOOLS]
    assert "send_beepout_for_human_review" in names, "absent from tools/list"

    srv = mcp_server.DecisionOpsMcp()
    res = srv.dispatch("send_beepout_for_human_review", {"sheet_path": "/etc/hosts"})
    assert "unknown" not in json.dumps(res).lower(), (
        "declared in TOOLS but not wired into dispatch — the built-and-never-registered "
        "class"
    )


def test_b1_endpoint_is_graph_independent():
    assert "send_beepout_for_human_review" not in mcp_server._GRAPH_TOOLS, (
        "a beep-out sheet has nothing to do with the decision graph; membership would "
        "route it through the default-corpus config-fault short-circuit"
    )


# ------------------------------------------------------------------ B2, B3, B6


def test_b2_hand_authored_sheet_is_refused(repo_md, sink):
    published, publish = sink
    repo_md.write_text(
        "# rack_triple_gauge — BEEP-OUT SHEET\n\n"
        "| ✓ | probe A | probe B | expect |\n|---|---|---|---|\n"
        "| ☐ | J_PWR_TAP pin 2 | J_PWR_TAP pin 4 | OPEN |\n",
        encoding="utf-8",
    )
    res = br.send_beepout_for_human_review(str(repo_md), publish=publish)
    assert res["status"] == "error"
    assert res.get("refused_not_derived") is True
    assert not published, "a refusal must publish nothing"
    assert "hand-authored" in res["message"].lower()


def test_b3_outside_repo_publishes_nothing(sink):
    published, publish = sink
    res = br.send_beepout_for_human_review("/etc/hosts", publish=publish)
    assert res["status"] == "error"
    assert not published


def test_b6_zero_rows_is_refused(repo_md, sink):
    published, publish = sink
    body = repo_md
    prov = {"schema": "composeio.beepout/1", "board": "empty",
            "derived_from": str(GAUGE), "derived_from_sha256": "0" * 64,
            "rows_total": 0, "rows_by_category": {}}
    body.write_text(
        "<!-- BEGIN BEEPOUT-PROVENANCE (composeio.maker.beepout) -->\n"
        + json.dumps(prov)
        + "\n<!-- END BEEPOUT-PROVENANCE -->\n# empty\n",
        encoding="utf-8",
    )
    res = br.send_beepout_for_human_review(str(body), publish=publish)
    assert res["status"] == "error"
    assert res.get("refused_no_rows") is True
    assert not published


# ------------------------------------------------------------------ B4, B5


def test_b4_stale_sheet_is_refused(repo_md, gauge_sheet, sink):
    """The sync-contract, ENFORCED.

    [PCB-CORPUS-PLACEMENT] records the drift check as a CONVENTION and states its residual
    risk plainly: *"nothing runs shasum on a schedule today. An edit to the source will not
    announce itself."* Here it does — a sheet whose netlist has moved is refused.
    """
    published, publish = sink
    path = _write_sheet(repo_md, gauge_sheet)
    text = repo_md.read_text()
    tampered = re.sub(r'"derived_from_sha256": "[0-9a-f]{64}"',
                      '"derived_from_sha256": "%s"' % ("d" * 64), text)
    assert tampered != text
    repo_md.write_text(tampered, encoding="utf-8")

    res = br.send_beepout_for_human_review(path, publish=publish)
    assert res["status"] == "error"
    assert res.get("refused_stale") is True
    assert not published
    assert res["actual_sha256"] != res["recorded_sha256"]


def test_b5_hand_edited_rows_are_refused(repo_md, gauge_sheet, sink):
    published, publish = sink
    path = _write_sheet(repo_md, gauge_sheet)
    text = repo_md.read_text()
    # Claim one extra rail-isolation row than the derivation actually produced.
    tampered = re.sub(r'"rail-isolation": (\d+)',
                      lambda m: f'"rail-isolation": {int(m.group(1)) + 1}', text)
    assert tampered != text
    repo_md.write_text(tampered, encoding="utf-8")

    res = br.send_beepout_for_human_review(path, publish=publish)
    assert res["status"] == "error"
    assert res.get("refused_not_derived") is True
    assert not published
    assert res["recorded_rows_by_category"] != res["derived_rows_by_category"]


def test_b4_control_an_untampered_sheet_IS_accepted(repo_md, gauge_sheet):
    """The positive control: without the tamper, the same sheet passes.

    Without this, B4/B5 could both pass because the endpoint refuses EVERYTHING.
    """
    path = _write_sheet(repo_md, gauge_sheet)
    # ⛔ `publish=` IS INJECTED DELIBERATELY, AND IT IS NOT COSMETIC. Since the intent
    # leg went live ([CXC-BPO-LAUNCHER]) the default sink is a REAL ComposeBus publish
    # to the fleet broker. Without this injection the test silently acquires a network
    # dependency: it passes when a broker happens to be up and fails on a laptop that
    # is off the fleet LAN, and neither outcome is about the behaviour under test.
    published: list = []
    res = br.send_beepout_for_human_review(path, publish=published.append)
    assert res["status"] == "sent", res.get("message")
    assert res["rows_total"] == len(gauge_sheet.rows)
    assert res["netlist_sha256"] == gauge_sheet.board.source_sha256
    assert len(published) == 1, "an accepted sheet publishes EXACTLY ONE open intent"
    assert res["intent_published"] is True


# ------------------------------------------------------------------ B7 (anti-fork)


def test_b7_the_derivation_is_NOT_implemented_in_the_review_module(
        repo_md, gauge_sheet, monkeypatch):
    """The gate must CALL the engine, never re-implement it.

    The hookup surface pins the identical property for its label detector, after that
    detector was independently written twice by two agents who could not see each other
    and had to be collapsed. A gate that re-derives with its own code is checking the sheet
    against a second opinion that shares nothing with the generator — so the two can drift,
    and the gate then reports on a board neither of them describes.
    """
    path = _write_sheet(repo_md, gauge_sheet)
    sentinel = {"sentinel-category": 1}

    class _FakeSheet:
        rows = [object()]

        def by_category(self):
            return sentinel

        def by_state(self):
            return {"DERIVED": 1}

    import composeio.maker.beepout as engine

    monkeypatch.setattr(engine, "derive_sheet", lambda board: _FakeSheet())
    res = br.send_beepout_for_human_review(path)

    # The endpoint must have used the patched engine — proving it calls, not re-implements.
    assert res["status"] == "error"
    assert res["derived_rows_by_category"] == sentinel, (
        "the review module did not go through composeio.maker.beepout.derive_sheet — "
        "it has forked the derivation"
    )


# ------------------------------------------------------------------ negative controls


def _rail_pairs(sheet):
    return {r.net_hint for r in sheet.rows if r.category == Category.RAIL_ISOLATION}


def _continuity_pair(sheet, net_name):
    for r in sheet.rows:
        if r.category == Category.NET_CONTINUITY and r.net_hint == net_name:
            return (r.from_label, r.to_label)
    return None


def _blocked(sheet):
    return {r.net_hint for r in sheet.rows if r.state == RowState.BLOCKED}


def test_nc1_shorting_5v_to_gnd_removes_the_isolation_row(tmp_path, gauge_sheet):
    """⛔ THE HEADLINE NEGATIVE CONTROL.

    Merge ``+5V`` into ``GND`` so the file itself claims they are one net. Every isolation
    row involving ``+5V`` must vanish, because "expect OPEN" between two halves of the same
    net would be a lie. If the row survives, it was being printed from a template and the
    rail-isolation block proves nothing.
    """
    before = _rail_pairs(gauge_sheet)
    assert "GND <-> +5V" in before

    mutant = short_two_nets(GAUGE.read_text(), 4, 1, "GND")
    after = _rail_pairs(derive_sheet(parse_board(materialise(mutant, "nc1", tmp_path))))

    assert "GND <-> +5V" not in after
    assert before != after
    assert not any("+5V" in p for p in after), (
        "every pair naming +5V must be gone once +5V is no longer a distinct net"
    )


def test_nc2_breaking_a_two_pad_net_moves_it_to_the_BLOCKED_state(tmp_path, gauge_sheet):
    """The THIRD state earns its existence here.

    A two-state contract would have to either drop this net silently or call it clean.
    Both are wrong, and the descriptor-parity checker learned it the hard way — an unprobed
    pin produced SILENCE, neither clean nor reserved.
    """
    assert "MOT1_A" not in _blocked(gauge_sheet)
    mutant = break_net_pad(GAUGE.read_text(), 20, "MOT1_A", occurrence=0)
    after = derive_sheet(parse_board(materialise(mutant, "nc2", tmp_path)))
    assert "MOT1_A" in _blocked(after)


def test_nc3_removing_an_extreme_pad_moves_the_longest_run_pair(tmp_path, gauge_sheet):
    """Proves the continuity pair is chosen from GEOMETRY, not from file order.

    A short hop proves nothing about the far end, so the pair must span the net's longest
    run. Remove a pad at one extreme and the chosen pair must move.
    """
    before = _continuity_pair(gauge_sheet, "+5V")
    assert before is not None

    text = GAUGE.read_text()
    moved = False
    for i in range(24):
        try:
            mutant = break_net_pad(text, 4, "+5V", occurrence=i)
        except ValueError:
            break
        after = _continuity_pair(
            derive_sheet(parse_board(materialise(mutant, f"nc3_{i}", tmp_path))), "+5V")
        if after != before:
            moved = True
            break
    assert moved, "the longest-run pair never moved — it is not being chosen from geometry"


def test_nc4_displacing_a_pad_drops_its_adjacency_rows(tmp_path, gauge_sheet):
    """Proves adjacency is read from footprint geometry, not from a list of dense parts."""
    def touching(sheet):
        return [r for r in sheet.rows
                if r.category == Category.ADJACENT_PAD
                and "MCP23017 Top pin 13" in (r.from_label + r.to_label)]

    assert touching(gauge_sheet), "expected the SDA/SCL neighbour pair on the DIP-28"

    mutant = displace_pad(GAUGE.read_text(), "MCP23017 Top", "13", 0.0, 25.0)
    after = derive_sheet(parse_board(materialise(mutant, "nc4", tmp_path)))
    assert not touching(after)


# ------------------------------------------------------------------ engine invariants


@pytest.mark.parametrize("path", [GAUGE, BUTTON, LEGO])
def test_every_board_derives_a_nonempty_sheet(path):
    if not path.exists():
        pytest.skip(f"{path} absent")
    sheet = derive_sheet(parse_board(path))
    assert sheet.rows
    assert sheet.by_category()


@pytest.mark.parametrize("path", [GAUGE, BUTTON, LEGO])
def test_geometry_agrees_with_copper_written_by_a_different_code_path(path):
    """An independent check on our own pad-position arithmetic.

    The rotation convention and the relative/absolute composition are the two places the
    parser could be silently wrong, and both would produce plausible coordinates rather
    than an error — the capture.bmp shape. Copper endpoints come from a different writer,
    so agreement is evidence that does not share the parser's assumption.
    """
    if not path.exists():
        pytest.skip(f"{path} absent")
    board = parse_board(path)
    matched, total = board.geometry_coincidence()
    assert total > 0
    assert matched / total > 0.9, f"only {matched}/{total} pads sit on their own copper"


def test_rail_classification_catches_all_three_spellings():
    """`+3V3`, `3V3` and `VCC` are the same kind of thing spelled three ways in one fleet.

    A hardcoded rail list silently misses one — and a missed rail is a missed short, which
    is the single failure that destroys parts on power-up.
    """
    from composeio.maker.beepout.derive import classify_rails

    for path, expected in ((GAUGE, "+3V3"), (BUTTON, "3V3"), (LEGO, "+5V")):
        if not path.exists():
            continue
        board = parse_board(path)
        rails, _ = classify_rails(board)
        names = {board.net_name(n) for n in rails}
        assert expected in names, f"{expected} not classified as a rail on {path.name}"
        assert "GND" in names


def test_selftest_is_green():
    assert br._selftest() == 0


def test_the_gate_is_spec_backed_not_code_only():
    """A gate that lives only in code is under-spec'd, and under-spec is how a dead
    primary path hid behind its documented fallback for weeks on the sibling surface."""
    spec = _SPECS / "71-beepout-review.lts"
    assert spec.exists(), f"{spec} is missing — the gate is code-only"
    text = spec.read_text(encoding="utf-8")
    for node in ("[SPEC:CXC-BPO]", "[CXC-BPO-MCP]", "[CXC-BPO-DERIVE-NOT-AUTHOR]",
                 "[CXC-BPO-PROVENANCE-GATE]", "[CXC-BPO-STALE-GATE]",
                 "[CXC-BPO-THREE-STATES]", "[CXC-BPO-NEGATIVE-CONTROL]",
                 "[CXC-BPO-SEAM]"):
        assert node in text, f"{node} not spec'd"
    assert "refus" in text
    assert "publishes nothing" in text


# ------------------------------------------------------- the intent leg + its consumer


def test_the_publish_leg_is_live_and_its_consumer_is_registered():
    """⛔ THE RULE THIS SURFACE SHIPPED MUTE TO HONOUR: the intent leg lands WITH its
    launcher or not at all.

    This test exists so the two halves can never drift apart again. If someone flips
    `INTENT_PUBLISHED` on without a consumer, or deletes the consumer while the publish
    stays on, publishing resumes into a namespace nobody reads — reporting success while
    doing nothing, which is precisely what `_app/dsd/notice` did.
    """
    assert br.INTENT_PUBLISHED is True
    assert br.INTENT_NS == "_app/beepout/open"

    launcher = TREE / "compose_projects" / "intelligence" / "tools" / "beepout_viewer_launcher.py"
    assert launcher.exists(), (
        "the publish leg is ON but its consumer module is GONE — this is the "
        "into-the-void defect [CXC-BPO-MCP] kept the namespace mute to prevent"
    )
    ltext = launcher.read_text(encoding="utf-8")
    assert br.INTENT_NS in ltext, "the launcher does not subscribe the published namespace"
    # REPEAT DELIVERY: an exact subscribe would silently swallow a re-sent sheet.
    assert "subscribe_pattern" in ltext

    reg = (TREE / "compose_projects" / "intelligence" / "console" / "data_sources.py").read_text(encoding="utf-8")
    assert "beepout_viewer_launcher" in reg, (
        "the consumer exists but is NOT registered in the in-process data layer — "
        "built-tested-never-registered is a named class here"
    )


def test_the_launcher_reuses_the_doc_viewer_and_does_not_fork_one():
    """David 2026-08-15: "the viewer can be the composeio doc viewer."

    Pins the anti-fork property the way B7 pins it for the deriver: the launcher must
    CALL `doc_viewer_launcher`, never carry its own window/scene/spawn machinery.
    """
    ltext = (TREE / "compose_projects" / "intelligence" / "tools"
             / "beepout_viewer_launcher.py").read_text(encoding="utf-8")
    assert "import doc_viewer_launcher" in ltext
    assert "dvl.DocViewerLauncher()" in ltext, (
        "the launcher must INSTANTIATE the doc viewer's launcher, not reimplement it"
    )
    # the things it must NOT have grown its own copy of
    assert "def build_open_scene" not in ltext, "forked the scene builder"
    assert "SpawnedWindows(" not in ltext, "forked the window-lifecycle tracker"
    assert "subprocess.Popen" not in ltext, "forked the spawn"


def test_every_refusal_still_publishes_nothing(repo_md, gauge_sheet, monkeypatch):
    """Turning the publish ON must not have moved it above any gate.

    The publish sits AFTER every refusal, so a sheet that fails any predicate reaches a
    human's bench through no path at all. A publish that crept above a gate would put an
    unfalsifiable document in front of a meter.
    """
    published: list = []

    # outside the repo
    assert br.send_beepout_for_human_review(
        "/etc/hosts", publish=published.append)["status"] == "error"
    # no provenance block. Written to the FIXTURE-MANAGED path, not a sibling name:
    # the fixture cleans up exactly `repo_md` and then rmdir's, so a second filename
    # would survive the run as untracked residue inside the repo.
    repo_md.write_text("# hand written\n", encoding="utf-8")
    assert br.send_beepout_for_human_review(
        str(repo_md), publish=published.append)["status"] == "error"
    # hand-edited rows (the engine disagrees with the recorded counts)
    path = _write_sheet(repo_md, gauge_sheet)

    class _Fake:
        rows = [object()]

        def by_category(self):
            return {"tampered": 1}

        def by_state(self):
            return {"DERIVED": 1}

    import composeio.maker.beepout as engine
    monkeypatch.setattr(engine, "derive_sheet", lambda board: _Fake())
    assert br.send_beepout_for_human_review(
        path, publish=published.append)["status"] == "error"

    assert published == [], (
        "a REFUSED sheet published an open intent — the publish has moved above a gate"
    )


def test_the_skill_points_at_the_one_derivation():
    """The skill must route callers to the engine, not invite a second derivation."""
    skill = TREE / ".claude" / "skills" / "create-beepout-sheet" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    assert "composeio.maker.beepout" in text
    assert "DO NOT WRITE ANOTHER ONE" in text
    assert "generate_pcb.py" in text, "the skill must name the trap it exists to prevent"
