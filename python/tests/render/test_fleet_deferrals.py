"""[SPEC:CXC-FLEET] — the DEFERRED criteria, made machine-visible.

An invisible deferral is how a criterion quietly stops existing.  FC-C9 and FC-C16 each had
real work outstanding and NO test at all — not even a failing one — so nothing in the suite
marked them pending and a reader of a green run would have concluded they were met.  This
file gives each one the shape `test_dusk_gift_tiers.py` uses for FC-C17, because that
handling was the one the reviewer called correct:

  * a PASSING test that pins the MEASURED truth — what is actually built today, so the
    deferral is a fact in the suite rather than a claim in a report; and
  * an `xfail(strict=False)` guard carrying the EVIDENCED reason, which flips to XPASS the
    moment the remaining work lands.

The pairing matters.  An xfail alone says "not done" without saying how far; a passing test
alone says "this much works" without saying what is owed.  Together they are a contract a
future builder can pick up without re-deriving the state of the world.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _d in (os.path.join(_REPO, "python"),
           os.path.join(_REPO, "compose_projects", "intelligence", "console"),
           os.path.join(_REPO, "compose_projects", "intelligence", "publisher")):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _build(**kw):
    import data_sources                                            # type: ignore
    base = dict(session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
                mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-08-12")
    base.update(kw)
    return data_sources.build_console_sources(**base)


# ═════ FC-C9 — telemetry per instance, and a transcript pin that RE-resolves ════
#
# BUILT: the per-instance half.  Each declared instance with a known transcript gets its
# own `self_publisher` / `transcript_publisher` on disjoint `self/<id>/*` namespaces.
# OUTSTANDING: the RE-RESOLVABLE pin and the visible stale marker.

def test_FC_C9_the_per_instance_half_IS_built():
    """The measured truth: telemetry is per-instance today."""
    sources = _build(instances=[{"id": "hardware", "role": "composeio-hardware-architect"}],
                     session_jsonl_by_id={"hardware": "/tmp/hw.jsonl"})
    names = [s.name for s in sources]
    assert "self_publisher:hardware" in names
    assert "transcript_publisher:hardware" in names
    # ...and the host's own set is untouched and still on the host's transcript.
    assert "self_publisher" in names


def test_FC_C9_the_transcript_map_is_populated_by_the_PRODUCTION_path(monkeypatch,
                                                                     tmp_path):
    """⚠ THE TEST ABOVE PASSES `session_jsonl_by_id` EXPLICITLY, AND FOR A WHILE THAT WAS
    THE ONLY WAY IT WAS EVER POPULATED.

    No live caller supplied it: the render runtime passed only bus/instances/chat_id, the
    headless script passed none, and both defaults were None. So `self_publisher:<id>`
    could NEVER start for a non-host instance in production, while the test above
    certified the capability as working. A capability reachable only from a test is not a
    capability — it is a claim, and a test that hand-feeds the missing input is what makes
    the claim look verified.

    This drives the REAL entry point and asserts the map is built there.
    """
    import data_sources                                            # type: ignore

    role = "composeio-hardware-architect"
    proj = tmp_path / ".claude" / "projects"
    monkeypatch.setenv("COMPOSE_INSTANCES_ROOT", str(tmp_path / "compose-instances"))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", str(tmp_path), 1)
                        if p.startswith("~") else p)

    from composeio.instance_home import project_dir_for, project_dir_key
    key = project_dir_key(project_dir_for(role))
    (proj / key).mkdir(parents=True)
    jsonl = proj / key / "sess-abc.jsonl"
    jsonl.write_text("{}\n")

    found = data_sources.resolve_instance_jsonl("hardware", role)
    assert found == str(jsonl), (
        "the production resolver did not find the instance's transcript in its ROLE's "
        "own project dir")


def test_FC_C9_the_resolver_is_scoped_by_ROLE_not_a_global_newest_wins(monkeypatch,
                                                                      tmp_path):
    """The reason this resolution is allowed to exist at all.

    An `ls -t` newest-wins guess across the SHARED project pile is the cross-instance
    transcript leak [CXC-IC-SELF-IDENTITY] closed. That hazard is a property of the PILE,
    and [CXC-FC-PROJECT-DIR] removed the pile — so picking the newest inside ONE ROLE's
    dir is a choice among that role's own sessions, not among strangers'.
    """
    import data_sources                                            # type: ignore
    monkeypatch.setenv("COMPOSE_INSTANCES_ROOT", str(tmp_path / "compose-instances"))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", str(tmp_path), 1)
                        if p.startswith("~") else p)
    # a role with no project dir resolves to NOTHING rather than to somebody else's
    assert data_sources.resolve_instance_jsonl("hardware", "a-role-with-no-sessions") is None
    # and a roleless instance never resolves at all
    assert data_sources.resolve_instance_jsonl("hardware", "") is None


def test_FC_C9_an_unknown_transcript_yields_NO_set_rather_than_a_wrong_one():
    """The honest degradation that makes the deferral safe to ship.

    A gauge attached to the WRONG Intelligence is worse than a missing one, because the
    surface then reports a state the system is not in — which is the whole failure class
    [SPEC:CXC-FLEET] exists to close.  So an instance whose transcript is not known gets
    empty gauges, not somebody else's numbers.
    """
    sources = _build(instances=[{"id": "hardware", "role": "r"}])
    assert "self_publisher:hardware" not in [s.name for s in sources]


@pytest.mark.xfail(strict=False, reason=(
    "[CXC-FC-S2-TELEMETRY] second half DEFERRED: the transcript pin is still resolved "
    "ONCE at console boot (`data_sources.resolve_session_jsonl`, called from "
    "`start_console_data_layer`). A re-hosted instance leaves a long-running console "
    "pinned to a DEAD transcript — observed: a console up 3 days pinned to a transcript "
    "dead for 2, rendering perfectly with every gauge frozen. Re-resolution needs a "
    "runtime re-pin loop AND a visible stale marker; the per-instance half this build "
    "delivered does not imply it."))
def test_FC_C9_the_transcript_pin_is_RE_resolvable_at_runtime():
    """When an instance is re-hosted, the console re-pins to the NEW transcript.

    ⚠ AND THE STALE INTERVAL MUST BE VISIBLE ([CXC-FC-S2-TELEMETRY]).  Re-resolution
    fixes the data; the marker is what stops the same class recurring unnoticed when
    re-resolution ITSELF fails.  A frozen gauge that renders is worse than a missing one.
    """
    import data_sources                                            # type: ignore
    assert hasattr(data_sources, "repin_session_jsonl"), \
        "no runtime re-pin seam exists yet"


@pytest.mark.xfail(strict=False, reason=(
    "[CXC-FC-S2-TELEMETRY] the STALE MARKER is deferred with the re-pin loop: there is "
    "no `self/<id>/transcript_stale` (or equivalent) namespace for a surface to bind, so "
    "a frozen gauge is currently indistinguishable from a quiet one ON THE GLASS."))
def test_FC_C9_a_stale_pin_is_visible_on_the_glass():
    scene = os.path.join(_REPO, "compose_projects", "intelligence", "console", "console.compose.yaml")
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(scene)
    declared = {n.get("name") for n in comp.namespaces}
    assert any(n and "stale" in n for n in declared), \
        "no stale-transcript signal is declared, so staleness cannot reach the glass"


# ═════ FC-C16 — nothing regresses ═══════════════════════════════════════════════
#
# "Every existing capture is byte-identical where no pane was added" cannot be asserted
# as stated for THIS build, because a pane WAS added to the console — that is the build.
# What can be asserted, and is asserted here, is the honest form: the capture set is
# stable RUN TO RUN, so a future change's diff means something.

# ⚠ TWO DIFFERENT CAUSES, AND MY FIRST LABEL CONFLATED THEM.  I recorded both of these
# as "nondeterministic".  That is true of ONE of them and FALSE of the other, and the
# mislabel is load-bearing: filed under "flaky", a REAL `doc_browser` regression would be
# absorbed by the wrong label and never investigated.  Re-measured 2026-08-12.
#
# GENUINELY NONDETERMINISTIC — differs run to run in the SAME checkout:
#   chat_thread_table.png   two consecutive full-suite runs, no edits between them:
#                           313d6997... then 4b8e45fb... Run in ISOLATION it is stable,
#                           so the variance comes from suite-wide state, not the widget.
_NONDETERMINISTIC_CAPTURES = {
    "chat_thread_table.png",
}

# DETERMINISTIC, but PER-CHECKOUT — identical across 3 runs here (521c70e9... every
# time), and different from the committed hash only because the surface RENDERS AN
# ABSOLUTE PATH: `test_doc_browser.py:64` constructs `DocBrowserWidget("db",
# doc_root=REPO_ROOT)`, so a worktree draws its own checkout path into the image.
# Outside that text bbox, base and build are pixel-identical. It is not flaky and it is
# not a regression; it is a capture that encodes where it was rendered.
_PER_CHECKOUT_CAPTURES = {
    "doc_browser.png",
}

_EXPECTED_CAPTURE_DRIFT = _NONDETERMINISTIC_CAPTURES | _PER_CHECKOUT_CAPTURES


def test_FC_C16_the_expected_capture_drift_set_has_not_grown():
    """The measured truth: exactly two captures drift, for two DIFFERENT reasons.

    This is the assertion that makes the capture suite meaningful again.  With an
    unbounded drift set, "the captures changed" carries no information; with a pinned
    set of two — each with its cause recorded — a third appearing is a real signal.
    """
    captures = os.path.join(_HERE, "captures")
    out = subprocess.run(["git", "status", "--porcelain", captures],
                         cwd=_REPO, capture_output=True, text=True)
    dirty = {os.path.basename(line.split()[-1])
             for line in out.stdout.splitlines() if line.strip()}
    unexpected = dirty - _EXPECTED_CAPTURE_DRIFT
    assert not unexpected, (
        f"capture(s) changed that are not in the expected-drift set: "
        f"{sorted(unexpected)}. Either a real render change landed (explain it), or the "
        f"nondeterminism spread (fix it), or a capture gained a path dependence (fix "
        f"THAT) — do NOT simply add it to a set. And add it to the RIGHT set: filing a "
        f"deterministic capture under 'flaky' is how a real regression gets absorbed.")


def test_FC_C16_the_per_checkout_captures_encode_an_absolute_path():
    """The DIAGNOSIS, asserted rather than asserted-about.

    If `doc_browser` ever stops rendering an absolute path it becomes an ordinary
    deterministic capture and belongs out of this set — and this test is what notices.
    """
    src = open(os.path.join(_HERE, "test_doc_browser.py"), encoding="utf-8").read()
    assert "doc_root=REPO_ROOT" in src, (
        "doc_browser.png no longer renders an absolute checkout path — it is now an "
        "ordinary capture; remove it from _PER_CHECKOUT_CAPTURES.")


def test_FC_C16_every_capture_for_an_UNTOUCHED_scene_is_byte_identical():
    """FC-C16 as CORRECTED (ratified 2026-08-12).

    The criterion said "every existing capture is byte-identical", which is unsatisfiable
    for a build whose point is that the console gained panes — so my first response was an
    xfail against a wrong criterion. The architect rewrote it: byte-identical for every
    scene this build did NOT touch; the console's own capture is regenerated only under a
    glass review. That IS satisfiable, and this asserts it.

    Everything outside the expected-drift set is an untouched scene, so the assertion is
    the same walk with the honest scope.
    """
    captures = os.path.join(_HERE, "captures")
    out = subprocess.run(["git", "status", "--porcelain", captures],
                         cwd=_REPO, capture_output=True, text=True)
    dirty = {os.path.basename(line.split()[-1])
             for line in out.stdout.splitlines() if line.strip()}
    assert not (dirty - _EXPECTED_CAPTURE_DRIFT), (
        f"an UNTOUCHED scene's capture changed: "
        f"{sorted(dirty - _EXPECTED_CAPTURE_DRIFT)}")


@pytest.mark.xfail(strict=False, reason=(
    "[CXC-FC-ACCEPT-GUARDS] FC-C16's GLASS leg is outstanding by construction: the "
    "console's capture must be regenerated and REVIEWED BY DAVID before it is a golden "
    "again. Regenerating it here would be blessing a golden rather than verifying one — "
    "'a regenerated golden nobody looked at is a golden that certifies whatever it "
    "happens to contain' (the corrected criterion's own words). There is no console "
    "reference capture in the tree today, so nothing is silently stale."))
def test_FC_C16_the_console_reference_capture_is_current():
    ref = os.path.join(_HERE, "reference_images", "console.png")
    assert os.path.isfile(ref), "no console reference capture exists to compare against"


def test_FC_C16_the_rest_of_the_suite_did_not_regress():
    """The half of FC-C16 that IS met, stated so it is not lost in the deferral.

    The collected count is a floor rather than an equality: this build ADDED tests, and an
    equality here would have to be edited by every future author, which is how a
    no-unexplained-drop guard becomes a number nobody trusts.
    """
    out = subprocess.run([sys.executable, "-m", "pytest", "python/tests",
                          "-q", "-p", "no:randomly", "--collect-only"],
                         cwd=_REPO, capture_output=True, text=True)
    tail = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
    collected = int(tail.split()[0]) if tail and tail.split()[0].isdigit() else 0
    assert collected >= 3160, f"collected count dropped to {collected}: {tail}"
