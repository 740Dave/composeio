"""Proof that LAYER 3 of [SPEC:AUTH-PATH] actually FIRES — and only where it should.

BOTH LEGS, ALWAYS
-----------------
A gate that denies nothing and a gate that denies everything are equally useless, and
only the PAIR distinguishes them. So every behavioural claim here is made twice:

  * POSITIVE CONTROL — stage a defective `.lts`, run a REAL `git commit`, assert the
    commit is REJECTED and the offending LINE is named.
  * NEGATIVE CONTROL — stage content that legitimately holds colons, em dashes,
    pictographs, sha digests, URLs, bracketed refs and mixed-case identifiers IN THE
    VALUE, run a REAL `git commit`, assert it is ALLOWED. This is the load-bearing leg:
    a gate that fires on valid content gets disabled, after which it protects nothing.

The commit legs drive a throwaway repo whose `core.hooksPath` points at THIS repo's
`.githooks`, with `scripts/` + `python/` symlinked in — i.e. the real wrapper, the real
`pre-commit-tests.sh`, the real gate, a real `git commit`. Not a function call dressed
up as a hook: the thing being proven is that the HOOK PATH fires.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_GATE = _REPO / "scripts" / "lts_property_gate.py"
sys.path.insert(0, str(_REPO / "scripts"))

_ENGINE_ROOT = os.environ.get("FACTLATTICE_ENGINE_ROOT",
                              str(Path.home() / "factlattice-internal-dev"))
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)
pytest.importorskip("factlattice",
                    reason="factlattice parser not importable (set FACTLATTICE_ENGINE_ROOT)")

import lts_property_gate as gate  # noqa: E402

_FM = "---\ndomain: plt.cux\n---\n"

# The SIX shapes measured in the live corpus (26 instances in one 561-line document),
# plus the two classes `lts_selfcheck` records as detected NO. Every one must be caught.
# id -> (line, a substring the report must name)
DEFECTIVE = {
    "pictograph-prefix":   ("- ⚠ premise: a pictograph prefix", "⚠"),
    "comma-and-space":     ("- measured, 2026-08-12: a comma in the key region", "comma"),
    "space-in-key":        ("- worked example: a space in the key region", "whitespace"),
    "em-dash-qualifier":   ("- forbidden — X: an em-dash qualifier", "em dash"),
    "uppercase-emphasis":  ("- what-DOES-transfer: the inverse-correlation case", "uppercase"),
    "digit-initial":       ("- 2nd-attempt: a digit-initial key", "digit"),
    "en-dash-bullet":      ("– en-dash-bullet: an en dash where a hyphen was meant",
                            "en dash"),
    "no-space-after-colon": ("- missingspace:no space after the colon", "no space after"),
}

# Content that MUST be allowed. Every one of these puts the hostile character PAST the
# first colon, which is the only place the rule is allowed to stop looking.
LEGITIMATE = [
    "- kind: finding",
    "- colon-in-value: the ratio was 3:1, the broker is at mqtt-broker.local:1883",
    "- em-dash-in-value: the fix — which was NOT a rollback — landed on main",
    "- pictograph-in-value: ⚠ hazard, ⛔ forbidden, ✅ verified",
    "- sha-in-value: 50facfa, parent 8c0be60, defect 6E7171",
    "- mixed-case-in-value: ComposeBus, ComposeUX, MQTT, RPi, CYD, NSException",
    "- url-in-value: https://example.invalid/a:b?c=d and bus://assets/logo.png",
    "- ref-in-value: per [SPEC:AUTH-PATH] and [AUTH-PATH-LAYER-3-PRECOMMIT]",
    "- **bold-key**: the P2 bold form is a first-class property",
    "- key_with_underscores: fine",
    "- key-with-digits-2026: fine",
    "- see-also:",
    "  - [NEG-A]",
    "- mapping-key:",
    "  sub_one: a value",
    "  sub_two: another value",
    # Prose bullets that merely CONTAIN a colon. `- roughly 2:1` must not read as a key
    # `roughly 2` — a ratio is not a lost property.
    "- ratio 3:1 was measured on the bench",
    "- roughly 2:1 in favour, per the survey",
    # A sentence that happens to END in a colon is not a key either.
    "- MUST — the standalone WINDOW TITLE reads exactly \"Graph Viewer\", and this "
    "is NORMATIVE for the following reason:",
]


def _doc(body_lines) -> str:
    return (_FM + "# Fixture [SPEC:FIX]\n\n## 1. A node [FIX-A]\n\n"
            + "\n".join(body_lines) + "\n")


# --------------------------------------------------------------------------------------
# Tier 1 — the CHECKER, line by line. Fast, and it localises a failure to one shape.
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("shape", sorted(DEFECTIVE))
def test_defective_shape_is_caught_and_named(shape):
    line, expected = DEFECTIVE[shape]
    from factlattice.parser import parse_text
    text = _doc(["- kind: finding", line])
    findings = gate.check_text(text, "fixture.lts", parse_text)
    assert len(findings) == 1, (
        f"{shape}: expected exactly one finding, got {[f.line for f in findings]}")
    rendered = findings[0].render()
    assert expected in rendered, f"{shape}: report did not say why -> {rendered}"
    assert line.strip() in rendered, f"{shape}: report did not NAME the line -> {rendered}"


def _authored_keys(node) -> list:
    """The node's AUTHORED property keys: `properties` minus the keys the engine mints itself.
    The parser adds minted keys (today `normative_effective`) to every node; the engine names
    them through `minted_keys_in`, the same source as `minted_keys` on every reply. Only a
    key the ENGINE declares minted is removed, so an authored key can never hide here."""
    from factlattice.core_keys import minted_keys_in
    minted = set(minted_keys_in(node.properties))
    return sorted(k for k in node.properties if k not in minted)


def _assert_only_kind_survives(shape: str, line: str) -> None:
    from factlattice.parser import parse_text
    parsed = parse_text(_doc(["- kind: finding", line]), "fixture.lts")
    node = next(n for n in parsed.nodes if n.id == "FIX-A")
    assert _authored_keys(node) == ["kind"], (
        f"{shape}: expected only `kind` to survive, got {_authored_keys(node)} "
        f"(all properties: {sorted(node.properties)})")


def test_defective_shapes_really_do_lose_the_field():
    """The premise, proven rather than assumed: each defective line's key is ABSENT from
    the parsed AUTHORED properties. If the parser ever starts accepting one of these, this
    test fails and the gate's rule is the thing that is now wrong."""
    for shape, (line, _) in DEFECTIVE.items():
        _assert_only_kind_survives(shape, line)


@pytest.mark.parametrize("line", ["- well-formed-key: a value", "- second_key: 42"])
def test_the_loss_check_fails_on_a_well_formed_shape(line):
    """NEGATIVE CONTROL for the test above: on a NON-defective line the field lands, so the
    same check must FAIL. Without this, ignoring minted keys could make the check vacuous."""
    with pytest.raises(AssertionError):
        _assert_only_kind_survives("well-formed", line)


def test_legitimate_content_is_not_flagged():
    """THE NEGATIVE CONTROL. Values legitimately hold colons, em dashes, pictographs,
    sha digests and mixed case — the rule applies ONLY to the key region."""
    from factlattice.parser import parse_text
    findings = gate.check_text(_doc(LEGITIMATE), "fixture.lts", parse_text)
    assert findings == [], "gate fired on VALID content:\n" + "\n".join(
        f.render() for f in findings)


def test_legitimate_properties_actually_landed():
    """...and the negative control is not green merely because everything was SKIPPED."""
    from factlattice.parser import parse_text
    parsed = parse_text(_doc(LEGITIMATE), "fixture.lts")
    node = next(n for n in parsed.nodes if n.id == "FIX-A")
    for key in ("kind", "colon-in-value", "em-dash-in-value", "pictograph-in-value",
                "sha-in-value", "mixed-case-in-value", "url-in-value", "ref-in-value",
                "bold-key", "key_with_underscores", "key-with-digits-2026",
                "see-also", "mapping-key"):
        assert key in node.properties, (
            f"{key!r} did not land — the fixture is wrong, not the gate: "
            f"{sorted(node.properties)}")


def test_fenced_and_relationship_lines_are_exempt():
    """A property-shaped check applied inside a code fence or `## Relationships` fires on
    the corpus's own idiom, and a rule that warns on valid authoring gets switched off
    within a day."""
    from factlattice.parser import parse_text
    text = _doc([
        "- kind: finding",
        "",
        "```",
        "- ⚠ THIS IS FENCED: invisible to the gate",
        "- what-DOES-transfer: also fenced",
        "```",
        "",
        "## Relationships",
        "",
        "- [FIX-A] DEPENDS_ON [SPEC:FIX]",
    ])
    assert gate.check_text(text, "fixture.lts", parse_text) == []


def test_property_branch_roster_matches_the_engine():
    """The gate's notion of "this line became a property" is a NAMED roster of parser
    branches. If the engine grows a new property branch, this fails loudly here rather
    than the gate silently rejecting valid new syntax."""
    from factlattice import parser as fl
    engine = {v for k, v in vars(fl).items()
              if k.startswith("BRANCH_PROP") and isinstance(v, str)}
    assert engine == gate.PROPERTY_BRANCHES, (
        "the engine's property branches and the gate's roster disagree; "
        f"engine-only={sorted(engine - gate.PROPERTY_BRANCHES)} "
        f"gate-only={sorted(gate.PROPERTY_BRANCHES - engine)}")


# --------------------------------------------------------------------------------------
# Tier 2 — the HOOK. A real repo, the real wrapper, a real `git commit`.
# --------------------------------------------------------------------------------------
# ⛔ THIS SCRUB IS NOT OPTIONAL, AND IT IS NOT THEORETICAL.
# git EXPORTS GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE into every hook it runs, and those
# BEAT `git -C <dir>` — `-C` changes the working directory, it does NOT repoint repository
# discovery. THIS FILE RUNS INSIDE THAT HOOK (tests/ is a mapped suite), so an unscrubbed
# fixture would commit into the REAL repository. That is not a hypothetical: a previous test
# for this same pre-commit gate wrote five throwaway commits onto the real worktree's HEAD
# and is the probable cause of `core.bare = true` appearing in the real .git/config, which
# silently broke every working-tree operation for an hour.
# [INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT] instance 6.
_GIT_AMBIENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_PREFIX", "GIT_AUTHOR_DATE",
                "GIT_COMMITTER_DATE")


def _scrub(env=None) -> dict:
    e = dict(env if env is not None else os.environ)
    for k in _GIT_AMBIENT:
        e.pop(k, None)
    return e


def _git(repo: Path, *args: str, env=None):
    e = _scrub(env)
    # A second belt: refuse to discover a repository ABOVE the fixture, whatever the
    # ambient configuration says.
    e["GIT_CEILING_DIRECTORIES"] = str(repo.parent)
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, env=e)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    # Prove the fixture is its OWN repo before a single commit is made into it. If this
    # ever resolves to the real checkout, every commit below lands on the real HEAD.
    top = _git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    assert Path(top).resolve() == repo.resolve(), (
        f"the fixture repo resolved to {top!r}, NOT {repo}! The ambient GIT_* leak is "
        "back — STOP: unscrubbed, the commits below would land on the real repository.")
    _git(repo, "config", "user.email", "gate@test.invalid")
    _git(repo, "config", "user.name", "gate test")
    # The REAL hook wrapper, and the REAL scripts it execs. `core.hooksPath` is what this
    # project actually uses, and the wrapper derives its root from `git rev-parse
    # --show-toplevel` — so it will exec THIS repo's copies through the symlink below.
    _git(repo, "config", "core.hooksPath", str(_REPO / ".githooks"))
    (repo / "scripts").symlink_to(_REPO / "scripts")
    (repo / "python").symlink_to(_REPO / "python")
    (repo / "corpora").mkdir()
    (repo / "seed.txt").write_text("seed\n")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-qm", "seed")
    return repo


def _env():
    e = dict(os.environ)
    e["FACTLATTICE_ENGINE_ROOT"] = _ENGINE_ROOT
    # The throwaway repo has no python suite to run; the .lts arm runs REGARDLESS, which
    # is the point of it sitting above this flag.
    e["SKIP_PRECOMMIT_TESTS"] = "1"
    e.pop("SKIP_LTS_PROPERTY_GATE", None)
    return e


def _commit(repo: Path, rel: str, text: str, env=None):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    assert _git(repo, "add", rel).returncode == 0
    return _git(repo, "commit", "-m", f"add {rel}", env=env or _env())


def test_hook_REJECTS_a_defective_lts(tmp_path):
    """POSITIVE CONTROL, end to end: a real `git commit` of a defective `.lts` FAILS,
    the offending line is named, and NO commit is created."""
    repo = _make_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    body = ["- kind: finding"] + [line for line, _ in DEFECTIVE.values()]
    res = _commit(repo, "corpora/x.lts", _doc(body))
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"the commit was ALLOWED. output:\n{out}"
    assert gate.BLOCK_HEADER in out, f"blocked, but not by this gate:\n{out}"
    for shape, (line, why) in DEFECTIVE.items():
        assert line.strip() in out, f"{shape}: the hook did not NAME the line:\n{out}"
        assert why in out, f"{shape}: the hook did not give the reason:\n{out}"
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert after == before, "HEAD moved — the commit was created despite the rejection"


def test_hook_ALLOWS_legitimate_lts(tmp_path):
    """NEGATIVE CONTROL, end to end: the same hook, the same repo, valid content — the
    commit is CREATED. Without this leg, a gate that rejects everything would pass the
    test above."""
    repo = _make_repo(tmp_path)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    res = _commit(repo, "corpora/x.lts", _doc(LEGITIMATE))
    out = res.stdout + res.stderr
    assert res.returncode == 0, f"the hook REJECTED valid content:\n{out}"
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert after != before, f"HEAD did not move — no commit was created:\n{out}"


def test_hook_gates_the_INDEX_not_the_working_tree(tmp_path):
    """The bytes checked must be the bytes ABOUT TO BE COMMITTED. Stage a defective file,
    then repair the WORKING TREE without staging it: the commit must still be rejected.
    Gating the wrong bytes is the [DEFECT-...-6E7171] class, and it reads green."""
    repo = _make_repo(tmp_path)
    p = repo / "corpora" / "x.lts"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_doc(["- kind: finding", "- what-DOES-transfer: lost"]), encoding="utf-8")
    _git(repo, "add", "corpora/x.lts")
    p.write_text(_doc(LEGITIMATE), encoding="utf-8")  # working tree now clean, index is not
    res = _git(repo, "commit", "-m", "sneak", env=_env())
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"the gate read the WORKING TREE, not the index:\n{out}"
    assert "what-DOES-transfer" in out


def test_pre_existing_findings_do_not_block_an_unrelated_edit(tmp_path):
    """The proportionality rule. A file already carrying a losing line can still receive
    an unrelated, VALID edit — otherwise every touch of a legacy corpus file becomes an
    unrelated remediation, which is how a gate gets disabled. The pre-existing finding is
    still REPORTED, as a counted advisory."""
    repo = _make_repo(tmp_path)
    dirty = _doc(["- kind: finding", "- what-DOES-transfer: pre-existing loss"])
    p = repo / "corpora" / "x.lts"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dirty, encoding="utf-8")
    _git(repo, "add", "corpora/x.lts")
    # Land the debt with a deliberate, TRACEABLE bypass — exactly the affordance
    # [AUTH-PATH-NOT-A-CAGE] insists must remain.
    env = _env()
    env["SKIP_LTS_PROPERTY_GATE"] = "1"
    assert _git(repo, "commit", "-m", "legacy", env=env).returncode == 0
    # Now an unrelated valid edit to the same file.
    res = _commit(repo, "corpora/x.lts", dirty.replace("- kind: finding",
                                                       "- kind: finding\n- added-now: fine"))
    out = res.stdout + res.stderr
    assert res.returncode == 0, f"an unrelated valid edit was blocked by legacy debt:\n{out}"
    assert "PRE-EXISTING" in out, f"the pre-existing finding was not reported at all:\n{out}"


def test_a_NEW_defective_line_in_a_dirty_file_still_blocks(tmp_path):
    """...and proportionality must not become permissiveness: a NEW losing line in an
    ALREADY-dirty file is still rejected."""
    repo = _make_repo(tmp_path)
    dirty = _doc(["- kind: finding", "- what-DOES-transfer: pre-existing loss"])
    p = repo / "corpora" / "x.lts"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dirty, encoding="utf-8")
    _git(repo, "add", "corpora/x.lts")
    env = _env()
    env["SKIP_LTS_PROPERTY_GATE"] = "1"
    assert _git(repo, "commit", "-m", "legacy", env=env).returncode == 0
    res = _commit(repo, "corpora/x.lts",
                  dirty.replace("- kind: finding", "- kind: finding\n- 2nd-attempt: new loss"))
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"a NEW losing line was allowed:\n{out}"
    assert "2nd-attempt" in out


def test_missing_parser_REFUSES_rather_than_skipping(tmp_path):
    """A gate that silently no-ops when its dependency is missing reads green while
    protecting nothing. Point FACTLATTICE_ENGINE_ROOT at an empty dir: exit 2, and the
    commit does NOT happen."""
    repo = _make_repo(tmp_path)
    # `factlattice` is ALSO installed site-wide here, so an empty FACTLATTICE_ENGINE_ROOT
    # would not defeat the import and the test would prove nothing. Seat a POISONED engine
    # instead: the gate puts ENGINE_ROOT at sys.path[0], so this package is what it finds.
    broken = tmp_path / "no-engine"
    (broken / "factlattice").mkdir(parents=True)
    (broken / "factlattice" / "__init__.py").write_text(
        "raise ImportError('poisoned engine seat — the gate must REFUSE, not skip')\n")
    env = _env()
    env["FACTLATTICE_ENGINE_ROOT"] = str(broken)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    res = _commit(repo, "corpora/x.lts", _doc(LEGITIMATE), env=env)
    out = res.stdout + res.stderr
    assert res.returncode != 0, f"a broken parser seat was treated as a PASS:\n{out}"
    assert "NOT importable" in out, f"it blocked, but not for the stated reason:\n{out}"
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == before, \
        "HEAD moved — the commit happened despite an unusable parser"


def _gate(repo: Path, *args: str, env=None):
    e = _scrub(env or _env())
    e["GIT_CEILING_DIRECTORIES"] = str(repo.parent)
    return subprocess.run([sys.executable, str(_GATE), *args], cwd=str(repo),
                          capture_output=True, text=True, env=e)


def test_range_mode_gates_a_LANDING(tmp_path):
    """`git cherry-pick` does NOT fire the pre-commit hook, and every subagent branch in
    this project lands by cherry-pick. Without --range, every landing is ungated BY
    CONSTRUCTION — the hole `pre-commit-tests.sh` already had to grow a `--range` to close."""
    repo = _make_repo(tmp_path)
    env = _env()
    env["SKIP_LTS_PROPERTY_GATE"] = "1"
    p = repo / "corpora" / "x.lts"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_doc(["- kind: finding", "- what-DOES-transfer: lost"]), encoding="utf-8")
    _git(repo, "add", "corpora/x.lts")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "commit", "-m", "landing", env=env).returncode == 0
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    res = _gate(repo, "--range", f"{base}..{head}")
    assert res.returncode == 1, f"the landing was not gated:\n{res.stdout}{res.stderr}"
    assert "what-DOES-transfer" in res.stderr


@pytest.mark.parametrize("rng,expect", [
    ("HEAD", "needs a <base>..<head>"),
    ("nosuchrev..HEAD", "does not resolve"),
])
def test_undeterminable_range_REFUSES_rather_than_passing(tmp_path, rng, expect):
    """Exit 2, never 0. `git diff <rev>` with no range silently compares the WORKING TREE,
    so a bare revision would gate something else entirely and still look like it worked."""
    repo = _make_repo(tmp_path)
    res = _gate(repo, "--range", rng)
    assert res.returncode == 2, f"got {res.returncode}:\n{res.stdout}{res.stderr}"
    assert expect in res.stderr


def test_empty_range_REFUSES(tmp_path):
    """THE EMPTY-SET RULE. "the range touches no .lts" and "I could not determine the file
    set" must not be the same code path — that conflation IS the recurring defect."""
    repo = _make_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    res = _gate(repo, "--range", f"{head}..{head}")
    assert res.returncode == 2, f"got {res.returncode}:\n{res.stdout}{res.stderr}"
    assert "changed NO files" in res.stderr


def test_bypass_remains_possible_and_is_deliberate(tmp_path):
    """[AUTH-PATH-NOT-A-CAGE]. `--no-verify` and the named env var both still work. A
    gate nobody can pass gets patched out under deadline, and the patch is permanent."""
    repo = _make_repo(tmp_path)
    body = _doc(["- kind: finding", "- what-DOES-transfer: lost"])
    p = repo / "corpora" / "x.lts"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    _git(repo, "add", "corpora/x.lts")
    assert _git(repo, "commit", "-m", "wip", "--no-verify", env=_env()).returncode == 0

    p.write_text(body.replace("lost", "lost again"), encoding="utf-8")
    _git(repo, "add", "corpora/x.lts")
    env = _env()
    env["SKIP_LTS_PROPERTY_GATE"] = "1"
    assert _git(repo, "commit", "-m", "wip2", env=env).returncode == 0
