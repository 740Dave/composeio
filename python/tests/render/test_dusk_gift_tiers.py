"""[SPEC:CXC-FLEET] FC-C17 — the withheld answer stays withheld, and every gift declares a tier.

FIRST HALF: DISCHARGED 2026-08-13.  The guard below is LIVE (no xfail).

THE HISTORY, KEPT BECAUSE IT IS THE INTERESTING PART.  [CXC-FC-GIFT-MECHANISM] recorded
`measured-2026-08-12` that the answers/ split had "ALREADY LANDED" in commit `4bca26e`, and on
that basis NARROWED the requirement from "do the split" to "add a regression guard".  Measured at
`main` (50facfa) the next day, `bootstrap/dusk-gifts/answers/` DID NOT EXIST and 21 live
`<details><summary>reason first</summary>` blocks remained: `4bca26e` was in the object database
but only on `perf/hosyond-dirty-rows`, a different effort's branch — and the one the worktree hook
branches from by default.  The spec-writer had measured the shared checkout's HEAD, not main.  So
[CXC-FC-GIFT-MECHANISM]'s stated `invalidating_assumption` ("that commit 4bca26e is the whole
fix") was never the condition that fired; the commit was not in the baseline at all.

Doing the split a second time would have duplicated ~21 files that already existed on another
branch, so this file instead shipped the guard XFAILED plus a self-retiring test whose whole job
was to fail the moment `4bca26e` reached main.

    $ git merge-base --is-ancestor 4bca26e 694a78a   ->  0   (ancestor)
    $ ls bootstrap/dusk-gifts/answers/ | wc -l       ->  19
    $ grep -r '<details><summary>' bootstrap/dusk-gifts/ | grep -v /answers  ->  (nothing)

`4bca26e` reached main; the self-retiring test fired exactly as designed and has been DELETED
per its own instruction, and the guard is un-xfailed and passing on the merged content.  A
mechanism that names the condition of its own retirement, and is then actually retired when the
condition occurs, is the shape worth copying.

FC-C17's SECOND half (every gift declares a tier, [CXC-FC-GIFT-TIERS]) is STILL DEFERRED: adding
frontmatter to seven files that another branch is concurrently rewriting is a conflict factory.
`gift_tier()` below is the resolver those declarations will be read by, so the mechanism is in
place and only the declarations are outstanding.
"""
from __future__ import annotations

import os
import re

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
GIFTS = os.path.join(_REPO, "bootstrap", "dusk-gifts")

# [CXC-FC-TIERS] the three bootstrap tiers. PLATFORM is inherited BY REFERENCE by every role —
# a copy of platform text into a role manifest is "a fork with a delay fuse".
TIERS = ("platform", "role", "instance")
_TIER_RE = re.compile(r"^tier\s*:\s*(\w+)\s*$", re.M)


def gift_tier(path: str) -> str:
    """The declared tier of one dusk gift, or "" when it declares none.

    THE RESOLVER, shipped ahead of the declarations it reads ([CXC-FC-GIFT-TIERS]).  A new role
    (`composeio-hardware-architect`) must inherit the PLATFORM gifts and NONE of the ComposeUX
    model problems — a hardware architect reasoning through ComposeUX widget-model problems is
    being oriented to somebody else's map.  That selection is only possible once each gift says
    which tier it belongs to.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            head = fh.read(2048)
    except OSError:
        return ""
    m = _TIER_RE.search(head)
    return m.group(1).lower() if m and m.group(1).lower() in TIERS else ""


def _gift_files():
    for base, dirs, files in os.walk(GIFTS):
        dirs[:] = [d for d in dirs if not d.startswith("answers")]
        for fn in sorted(files):
            if fn.endswith(".md"):
                yield os.path.join(base, fn)


def _withheld_blocks(path: str) -> int:
    """Count LIVE withheld answers — a `<details>` whose summary invites reasoning first."""
    try:
        src = open(path, "r", encoding="utf-8").read()
    except OSError:
        return 0
    return len(re.findall(r"<details><summary>\s*reason first\s*</summary>", src))


# NOTE: `test_the_answers_split_has_NOT_landed_on_main` USED TO LIVE HERE.  It was a passing
# test that pinned a fact the spec got wrong, and it carried its own retirement instruction:
# "when 4bca26e merges to main this test FAILS — and that failure is correct: it is the prompt
# to delete this test and un-xfail the guard below."  On 2026-08-13, at 694a78a, it failed.  It
# was deleted and the guard un-xfailed.  This comment is the receipt, so the next reader does
# not have to dig the deletion out of the log to learn the test ever existed.


def test_FC_C17_no_withheld_answer_outside_answers():
    """The regression guard [CXC-FC-GIFT-MECHANISM] asks for.

    WHY THE `<details>` MECHANISM FAILED AT ALL: it is a BROWSER affordance.  `Read` returns the
    whole file, answers included, so an agent cannot un-read them and "reason first" was an
    honour system the tool defeats.  It failed for two consecutive instances and was found
    independently by the architect AND by the hosted child — strong corroboration.  The fix is
    the FILESYSTEM: one file per answer, under `answers*/`, which an agent must choose to open.
    """
    offenders = {os.path.relpath(p, _REPO): n
                 for p in _gift_files() if (n := _withheld_blocks(p))}
    assert not offenders, (
        "a withheld answer outside answers*/ is withheld by an honour system the Read tool "
        f"defeats ([CXC-FC-GIFT-MECHANISM]): {offenders}")


@pytest.mark.xfail(strict=False, reason=(
    "[CXC-FC-GIFT-TIERS] DEFERRED: adding `tier:` frontmatter to seven gift files that "
    "`perf/hosyond-dirty-rows` is concurrently rewriting is a conflict factory. The resolver "
    "(`gift_tier`) ships so only the declarations are outstanding."))
def test_FC_C17_every_gift_declares_exactly_one_tier():
    """Every file under `bootstrap/dusk-gifts/` resolves to exactly one declared tier, so a new
    role inherits the platform gifts and NONE of the ComposeUX model problems."""
    undeclared = [os.path.relpath(p, _REPO) for p in _gift_files() if not gift_tier(p)]
    assert not undeclared, f"dusk gifts with no declared tier: {undeclared}"


def test_the_tier_resolver_reads_a_declaration_and_refuses_an_unknown_one(tmp_path):
    """The MECHANISM is testable now even though the declarations are not written yet."""
    good = tmp_path / "g.md"
    good.write_text("---\nname: g\ntier: platform\n---\nbody\n")
    assert gift_tier(str(good)) == "platform"
    bad = tmp_path / "b.md"
    bad.write_text("---\nname: b\ntier: whatever\n---\nbody\n")
    assert gift_tier(str(bad)) == "", "an unknown tier must resolve to none, not pass through"
    none = tmp_path / "n.md"
    none.write_text("# no frontmatter\n")
    assert gift_tier(str(none)) == ""
