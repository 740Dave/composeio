#!/usr/bin/env python3
"""LAYER 3 of [SPEC:AUTH-PATH] — the pre-commit `.lts` property reconciliation gate.

WHAT IT ENFORCES
----------------
For every staged `.lts`, every PROPERTY-SHAPED LINE THE AUTHOR WROTE must appear in
the parser's `properties`. A line that looks like `- key: value` but whose key region
violates `^[a-z][a-z0-9_-]*$` parses as PROSE: the field vanishes from the node, and
`lts_lint` reports the corpus CLEAN, because prose is not an error. `lost: 0` at 100%
coverage is likewise not a gate — a dropped key produced exactly that reading
([AUTH-PATH-RECURRENCE]). So this gate reconciles the TEXT against the PARSER'S OWN
LINE-CONSUMPTION RECORD (`LineCoverage.consumed`), which is the only signal that
distinguishes "became a property" from "was read as prose".

Measured recurrence that motivates it: EIGHT silent losses by one author in one day,
two of them AFTER a preventive tool was built and the rule written into that author's
own bootstrap, one inside a script-generated string where no character was typed by
hand, and one inside the enforcement proposal itself while counting the others. The
author caught zero of eight. A diagnostic depends on being read; this does not.

WHY REJECT AND NOT WARN
-----------------------
[AUTH-PATH-LAYER-3-PRECOMMIT] `suggested-strictness`. A warning at commit time is read
by the same author who wrote the losing line — which is the failure mode being fixed.
Exit 1 blocks the commit.

WHY IT IS CONTENT-BASED
-----------------------
It never asks which tool produced the file or who ran git. It reads the bytes about to
be committed and checks them against the real parser. That is what makes it hard to
defeat by accident — and it is the ONLY proposed layer whose firing does not depend on
Claude Code configuration or on `--dangerously-skip-permissions`.

WHY ADDED LINES, NOT THE WHOLE FILE  (the one deliberate refinement to §5)
-------------------------------------------------------------------------
§5 says "parse the file and assert that every property-shaped line the author wrote
appears in the parsed properties". This gate PARSES THE WHOLE FILE (it must — node
bodies, fences and section context only exist in the whole) but RENDERS ITS VERDICT
over the lines THIS COMMIT ADDS. Measured reason (2026-08-12, `--census corpora docs`):
417 pre-existing property-shaped lines in this repo do not land — 269 in `corpora/`,
148 in `docs/`, up to 46 in a single file. Whole-file strictness would reject a
one-word typo fix to `docs/intelligence-console/dsd-viewer-widget.lts` with 46
unrelated findings, and a gate that turns every edit into an unrelated 46-line
remediation is the "fires constantly -> gets disabled" failure
[AUTH-PATH-COVERAGE-MATRIX] `sequencing` names explicitly. Pre-existing findings in a
touched file are still REPORTED, as a counted advisory, so the debt stays visible
without being load-bearing. Whole-file strictness is available on demand:
`--all-lines`, and `--census` for the standing survey.

USAGE
  scripts/lts_property_gate.py                       # gate the STAGED set (the hook path)
  scripts/lts_property_gate.py --staged              # explicit form of the default
  scripts/lts_property_gate.py --range <base>..<head>  # gate a LANDING (cherry-pick/merge)
  scripts/lts_property_gate.py --files a.lts b.lts   # audit files on disk (whole file)
  scripts/lts_property_gate.py --census <dir>...     # whole-corpus survey, never blocks
  scripts/lts_property_gate.py --all-lines           # staged set, whole-file strictness

`--range` exists for the same reason pre-commit-tests.sh grew it: `git cherry-pick` does
NOT fire the pre-commit hook, and every subagent branch in this project lands by
cherry-pick. Without it, every landing would be ungated BY CONSTRUCTION.

EXIT CODES — three outcomes, never two (the same discipline as pre-commit-tests.sh):
  0 = reconciled (or legitimately nothing to reconcile)
  1 = a property-shaped line did NOT land  -> BLOCK
  2 = the gate could not DETERMINE the answer (parser missing, unreadable blob, git
      error) -> BLOCK, loudly. NEVER a pass. A gate that no-ops when its dependency is
      absent reads green while protecting nothing; this repo has already been bitten by
      exactly that ([DEFECT-THE-PRE-COMMIT-TEST-GATE-6E7171], green by construction for
      13 days).

BYPASS (deliberate, and it leaves a trace — [AUTH-PATH-NOT-A-CAGE])
  SKIP_LTS_PROPERTY_GATE=1 git commit ...   # this gate only
  git commit --no-verify                    # every hook
Not made impossible on purpose: "a gate nobody can pass gets patched out under
deadline, and the patch is permanent."
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

# --------------------------------------------------------------------------------------
# The parser seat. Import failure is a REFUSAL (exit 2), never a skip — see the exit-code
# note above. `factlattice` is an EXTERNAL, SHARED library seated via FACTLATTICE_ENGINE_ROOT
# exactly as tests/test_lts_authored_edges_survive_parse.py seats it.
# --------------------------------------------------------------------------------------
# ⚠ RENAMED 2026-08-16: `factlattice-deployment` -> `factlattice-internal-dev`, when the
# checkout was re-pointed from the PUBLIC 740Dave/factlattice to the PRIVATE
# 740Dave/factlattice-internal-dev. The old path no longer exists. Deliberately a single
# hardcoded default and NOT a try-new-then-fall-back-to-old chain: a fallback would let a
# future rename keep working silently, and this gate's whole value is that it REFUSES
# rather than reporting a pass it cannot justify — which is exactly what it did when the
# rename broke it. A loud refusal is the correct failure mode; keep it loud.
ENGINE_ROOT = os.environ.get("FACTLATTICE_ENGINE_ROOT",
                             str(Path.home() / "factlattice-internal-dev"))


def _refuse(msg: str) -> "NoReturn":  # noqa: F821
    print(f"lts-property-gate: !! {msg}", file=sys.stderr)
    print("lts-property-gate: !! REFUSING to report a pass it cannot justify (exit 2).",
          file=sys.stderr)
    sys.exit(2)


def _load_parser():
    if ENGINE_ROOT not in sys.path:
        sys.path.insert(0, ENGINE_ROOT)
    try:
        from factlattice.parser import parse_text  # noqa: WPS433
    except Exception as exc:  # pragma: no cover - exercised by the import-failure test
        _refuse(
            "the factlattice parser is NOT importable, so no `.lts` line can be "
            f"reconciled: {exc!r}\n"
            f"            tried FACTLATTICE_ENGINE_ROOT='{ENGINE_ROOT}'\n"
            "            Seat the engine (or set FACTLATTICE_ENGINE_ROOT) — do not "
            "assume the staged corpus is clean.")
    return parse_text


# --------------------------------------------------------------------------------------
# THE RULE. A key must match ^[a-z][a-z0-9_-]*$. Held here as ONE regex because a second
# copy of a key grammar drifts from the parser's (the parser makes the same remark about
# its own P5 branch reusing P1's key grammar).
# --------------------------------------------------------------------------------------
KEY_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# Every LineCoverage branch that means "this line became part of a property". Anything
# else — including the parser's own `ignorable` non-record — means the field is not in
# `properties`. Named exhaustively rather than by prefix so a NEW property branch in the
# engine fails the roster test in tests/test_lts_property_gate.py instead of silently
# widening what this gate accepts.
PROPERTY_BRANCHES: Set[str] = {
    "property_simple",
    "property_bold",
    "property_key_only",
    "property_nested_item",
    "property_mapping_entry",
    "property_mapping_continuation",
    "property_continuation",
    "property_item_continuation",
}

# Bullet markers. The first three are the parser's real set (`_CONT_BULLET_RE`); the last
# three are the MIS-BULLET class `lts_selfcheck` records as detected NO — an en-dash or a
# bullet glyph where a hyphen was meant. The parser never sees them as a list item at all.
REAL_BULLETS = "-*+"
MIS_BULLETS = "–—•"  # en dash, em dash, bullet
# ⛔ THE `[ \t]+` IS LOAD-BEARING, and it was measured, not assumed. With `[ \t]*` the
# markdown BOLD LABEL `**Version**: 0.3` (a real, deliberate shape in
# corpora/platform-specs/56-terminal-widget.lts, six of them in one block) matched with
# marker `*` and key region `*Version**` — a false positive with a confidently wrong
# explanation attached, which is worse than no finding at all. A list item has whitespace
# after its marker; that is the parser's own definition (`_CONT_BULLET_RE`).
# KNOWN GAP accepted in exchange: an UNBULLETED `**Bold**: value` at column 0 is not
# examined. It is a different authoring shape (a metadata label, not a list property) and
# `**Bold intro**: prose` is a legitimate idiom in these documents.
_BULLET_RE = re.compile(rf"^([ \t]*)([{REAL_BULLETS}{MIS_BULLETS}])[ \t]+(.*)$")

# --------------------------------------------------------------------------------------
# THE NEGATIVE-CONTROL BOUNDARY — and it is the load-bearing half of this file.
#
# The rule applies ONLY to the KEY REGION: the text before the FIRST colon. NOTHING past
# that colon is ever inspected. Values in this corpus legitimately hold colons, em dashes,
# pictographs, sha digests, URLs, bracketed ids and mixed-case identifiers, and a gate that
# fires on valid content gets disabled — after which it protects nothing.
#
# A colon alone does not make a line property-shaped, either: a bullet is only a CANDIDATE
# when its key region is plausibly a key someone MEANT to be a key. The bounds below are
# measured against the live corpora, not invented:
#   * <= 4 words and <= 40 chars — `- MUST — the standalone WINDOW TITLE reads exactly
#     "Graph Viewer". This is NORMATIVE:` is a sentence that happens to end in a colon, not
#     a lost field;
#   * none of `[]()"`/\<>|{}$#=` — those open a REFERENCE, a PATH, a URL or code, none of
#     which is a key;
#   * a colon NOT followed by whitespace only counts when the key region is otherwise
#     PERFECT (the `- key:value` missing-space class). Otherwise `- roughly 2:1 in favour`
#     would read as a key `roughly 2`.
# --------------------------------------------------------------------------------------
MAX_KEY_CHARS = 40
MAX_KEY_WORDS = 4
FORBIDDEN_IN_KEY = set('[]()"`/\\<>|{}$#=')
_URLISH_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


class Finding(NamedTuple):
    path: str
    line_no: int          # 1-based
    line: str
    key_region: str
    reasons: Tuple[str, ...]
    node_id: Optional[str]
    added: bool           # this commit wrote this line

    def render(self) -> str:
        why = ", ".join(self.reasons)
        where = f"{self.path}:{self.line_no}"
        node = f" (node {self.node_id})" if self.node_id else ""
        return (f"  {where}{node}\n"
                f"      {self.line.rstrip()}\n"
                f"      key region -> {self.key_region!r}\n"
                f"      why it did not land: {why}")


def _describe(key_region: str, marker: str, space_after_colon: bool) -> Tuple[str, ...]:
    """Name the reason in the author's terms. Never guesses a fix that requires authoring."""
    k = key_region.strip().strip("*").strip()
    reasons: List[str] = []
    if marker in MIS_BULLETS:
        name = {"–": "an en dash", "—": "an em dash", "•": "a bullet glyph"}[marker]
        reasons.append(f"the line is bulleted with {name} ({marker!r}), not '-' (U+002D) — "
                       "the parser never sees a list item at all")
    if not k:
        reasons.append("the key region is empty")
        return tuple(reasons)
    first = k[0]
    if not first.isascii() or not first.isalpha():
        if first.isdigit():
            reasons.append(f"the key starts with a digit ({first!r}); keys must start "
                           "[a-z] — correcting this needs an INVENTED prefix, which is "
                           "authoring, so fix it by hand")
        else:
            reasons.append(f"the key starts with {first!r}, which is not [a-z] "
                           "(pictograph/punctuation prefixes make the whole line prose)")
    if any(c.isupper() for c in k):
        # Order-preserving dedupe: quote the author's own capitals back at them in the
        # order they typed them. `sorted(set(...))` turned `what-DOES-transfer` into
        # "DEOS", which reads like a different mistake than the one that was made.
        seen: List[str] = []
        for c in k:
            if c.isupper() and c not in seen:
                seen.append(c)
        ups = "".join(seen)
        reasons.append(f"the key contains uppercase ({ups}) — emphasis in a key is the "
                       "single most common cause here, and it is INVERSELY correlated "
                       "with survival")
    if any(c in " \t" for c in k):
        reasons.append("the key region contains whitespace (a key is one token; "
                       "use '-' or '_' between words)")
    for ch, label in (("—", "an em dash"), ("–", "an en dash"),
                      (",", "a comma"), (".", "a full stop"), (";", "a semicolon"),
                      (":", "a colon")):
        if ch in k:
            reasons.append(f"the key region contains {label} ({ch!r})")
    leftovers = {c for c in k
                 if not (c.islower() and c.isascii()) and not c.isdigit()
                 and c not in "_- \t—–,.;:" and not c.isupper()
                 and c != first}  # the initial is already named above; do not say it twice
    if leftovers:
        reasons.append("the key region contains "
                       + ", ".join(repr(c) for c in sorted(leftovers)))
    if not reasons and not space_after_colon:
        reasons.append("there is no space after the colon — the parser requires "
                       "`- key: value`, and `- key:value` is prose")
    if not reasons:
        # The key region is a perfect key and the colon is spaced, yet the parser did not
        # make a property of it. Structural, not typographic: the line sits somewhere the
        # property grammar was never run (a duplicate key, an ambiguous mapping block the
        # parser refused to guess at, a stray indent). Say exactly that rather than invent
        # a typographic reason that is not there.
        reasons.append("the key is well-formed, yet the parser produced no property from "
                       "this line — structural, not typographic (duplicate key? a mixed "
                       "list/mapping block the parser refuses to guess at? check the "
                       "surrounding node body)")
    return tuple(reasons)


def _candidate(rest: str) -> Optional[Tuple[str, bool]]:
    """Is this bullet body plausibly a `key: value` the author MEANT as a property?

    Returns (key_region, space_after_colon) or None. Reads NOTHING past the first colon.
    """
    if ":" not in rest:
        return None
    if _URLISH_RE.match(rest.strip()):
        return None
    key_region, tail = rest.split(":", 1)
    space_after = (tail == "" or tail[0] in " \t")
    stripped = key_region.strip().strip("*").strip()
    if not space_after:
        # Missing-space class: only a PERFECT key qualifies, so `2:1` stays prose.
        return (key_region, False) if KEY_RE.match(stripped) else None
    if not key_region or len(key_region) > MAX_KEY_CHARS:
        return None
    if len(key_region.split()) > MAX_KEY_WORDS:
        return None
    if any(c in FORBIDDEN_IN_KEY for c in key_region):
        return None
    return key_region, True


def check_text(text: str, path: str, parse_text,
               added_lines: Optional[Set[int]] = None) -> List[Finding]:
    """Reconcile one `.lts` document. `added_lines` is a set of 1-based line numbers.

    The whole document is parsed (node bodies, fences and section context exist only in
    the whole); `added` marks which findings this commit is responsible for.
    """
    parsed = parse_text(text, path)
    cov = parsed.coverage
    if cov is None:  # pragma: no cover - the engine always builds one today
        _refuse(f"the parser returned no LineCoverage for {path} — the gate's only "
                "evidence of what became a property is absent; it cannot judge.")
    findings: List[Finding] = []
    for idx, line in enumerate(cov.lines):
        # Skip everything with its OWN grammar. A property-shaped check applied inside
        # `## Relationships` fires on every well-formed edge triple, and a rule that
        # warns on the corpus's own edge idiom gets switched off within a day.
        if idx in cov.fenced:
            continue
        node_id = cov.node_body.get(idx)
        if node_id is None:
            continue            # never offered to the property grammar (frontmatter, preamble)
        if cov.section.get(idx):
            continue            # ## Relationships / ## Causal
        if cov.consumed.get(idx) in PROPERTY_BRANCHES:
            continue            # it landed — nothing to report
        m = _BULLET_RE.match(line)
        if not m:
            continue
        indent, marker, rest = m.group(1), m.group(2), m.group(3)
        if indent:
            # Indented bullets are LIST ITEMS under a key-only property (P4) and mapping
            # entries (P5). Their near-misses are real but they are ambiguous with genuine
            # list content, and a gate that guesses there fires on valid corpora. KNOWN GAP,
            # stated rather than papered over.
            continue
        cand = _candidate(rest)
        if cand is None:
            continue
        key_region, space_after = cand
        findings.append(Finding(
            path=path, line_no=idx + 1, line=line, key_region=key_region,
            reasons=_describe(key_region, marker, space_after), node_id=node_id,
            added=(added_lines is None or (idx + 1) in added_lines)))
    return findings


# --------------------------------------------------------------------------------------
# git plumbing. Every invocation is checked; a non-zero git exit is exit 2, never a pass.
# --------------------------------------------------------------------------------------
# git EXPORTS GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE into every hook it runs, and those
# BEAT `git -C <dir>` — `-C` changes the working directory, it does NOT repoint repository
# discovery ([INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT] instance 6). Stripped in RANGE mode,
# where inheriting them would silently classify whatever repo invoked us. NOT stripped in
# `--cached`, where GIT_INDEX_FILE legitimately points at a temporary index during a partial
# or amended commit — and reading the wrong index there would gate the wrong bytes.
_GIT_AMBIENT = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                "GIT_OBJECT_DIRECTORY", "GIT_PREFIX")


def _clean_env() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GIT_AMBIENT}


def _git(args: Sequence[str], repo: str, *, scrub: bool = False) -> str:
    proc = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          env=_clean_env() if scrub else None)
    if proc.returncode != 0:
        _refuse(f"`git {' '.join(args)}` exited {proc.returncode}: "
                f"{proc.stderr.strip() or '<no stderr>'}")
    return proc.stdout


def _diff_args(rev_range: Optional[str]) -> List[str]:
    """ONE routing table, two input sources — the same discipline pre-commit-tests.sh's
    `--range` fix established. Forking the source selection is how a landing ends up
    gated by a different rule than a commit."""
    return ["diff", rev_range] if rev_range else ["diff", "--cached"]


def _changed_lts(repo: str, rev_range: Optional[str]) -> List[str]:
    out = _git([*_diff_args(rev_range), "--name-only", "--diff-filter=ACMR"], repo,
               scrub=bool(rev_range))
    return [p for p in out.splitlines() if p.endswith(".lts")]


def _blob(repo: str, path: str, rev_range: Optional[str]) -> str:
    """The bytes ABOUT TO BE COMMITTED — the INDEX blob, never the working tree.

    They differ constantly (`git add -p`, an edit after `git add`), and gating the
    working tree while committing the index is the same wrong-bytes class as
    [DEFECT-THE-PRE-COMMIT-TEST-GATE-6E7171]. In range mode the head revision's blob is
    the equivalent authority.
    """
    rev = f":{path}" if not rev_range else f"{rev_range.split('..')[-1] or 'HEAD'}:{path}"
    proc = subprocess.run(["git", "-C", repo, "show", rev], capture_output=True,
                          env=_clean_env() if rev_range else None)
    if proc.returncode != 0:
        _refuse(f"cannot read the blob for {path} (`git show {rev}` exited "
                f"{proc.returncode}): {proc.stderr.decode('utf-8', 'replace').strip()}")
    try:
        return proc.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        _refuse(f"the blob for {path} is not valid UTF-8 ({exc}) — a `.lts` that the "
                "parser cannot read is a refusal, not a pass.")


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _added_lines(repo: str, path: str, rev_range: Optional[str]) -> Set[int]:
    """1-based NEW-file line numbers this change adds/modifies, from a -U0 diff."""
    out = _git([*_diff_args(rev_range), "-U0", "--no-color", "--", path], repo,
               scrub=bool(rev_range))
    added: Set[int] = set()
    for line in out.splitlines():
        m = _HUNK_RE.match(line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2) or 1)
            added.update(range(start, start + count))
    return added


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------
BLOCK_HEADER = "lts-property-gate: BLOCKED — property-shaped line(s) that did NOT land:"


def _report(blocking: List[Finding], advisory: List[Finding], n_files: int,
            source: str) -> int:
    if not blocking:
        note = ""
        if advisory:
            note = (f" ({len(advisory)} PRE-EXISTING finding(s) in the touched file(s) — "
                    "not yours, not blocking; `--all-lines` to see them)")
        print(f"lts-property-gate: {n_files} .lts reconciled against the parser, "
              f"0 lost properties in {source}{note}")
        return 0
    print(BLOCK_HEADER, file=sys.stderr)
    for f in blocking:
        print(f.render(), file=sys.stderr)
    print("", file=sys.stderr)
    print("lts-property-gate: a key must match ^[a-z][a-z0-9_-]*$. Anything else makes the",
          file=sys.stderr)
    print("lts-property-gate: WHOLE LINE parse as prose: the field vanishes from `properties`,",
          file=sys.stderr)
    print("lts-property-gate: and lts_lint still reports the corpus CLEAN, because prose is",
          file=sys.stderr)
    print("lts-property-gate: not an error. Nothing past the first colon is inspected — the",
          file=sys.stderr)
    print("lts-property-gate: VALUE may hold any colon, dash, pictograph or mixed case.",
          file=sys.stderr)
    print("lts-property-gate: If a line above is genuinely PROSE, unbullet it or move it out",
          file=sys.stderr)
    print("lts-property-gate: of the node body; if it is a property, fix the key.",
          file=sys.stderr)
    print("lts-property-gate: Deliberate bypass (it leaves a trace):", file=sys.stderr)
    print("lts-property-gate:   SKIP_LTS_PROPERTY_GATE=1 git commit ...   (this gate only)",
          file=sys.stderr)
    print("lts-property-gate:   git commit --no-verify                    (every hook)",
          file=sys.stderr)
    return 1


def main(argv: List[str]) -> int:
    mode = "staged"
    all_lines = False
    rev_range: Optional[str] = None
    files: List[str] = []
    roots: List[str] = []
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--staged":
            mode = "staged"
        elif a == "--all-lines":
            all_lines = True
        elif a == "--range" or a.startswith("--range="):
            val = a[len("--range="):] if a.startswith("--range=") else (
                args.pop(0) if args else "")
            # Require the two-dot shape TEXTUALLY: `git diff <rev>` silently compares the
            # WORKING TREE, so `--range HEAD` would gate something else and still look
            # like it worked. Same guard, same reason, as pre-commit-tests.sh.
            if ".." not in val or val.startswith("--"):
                _refuse(f"--range needs a <base>..<head> argument, got {val!r}. A bare "
                        "revision makes git compare the WORKING TREE instead, silently "
                        "gating the wrong thing.")
            mode, rev_range = "staged", val
        elif a == "--files":
            mode = "files"
            files, args = args, []
        elif a == "--census":
            mode = "census"
            roots, args = args, []
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            _refuse(f"unknown argument: {a!r} (see --help)")

    if os.environ.get("SKIP_LTS_PROPERTY_GATE") == "1" and mode == "staged":
        print("lts-property-gate: SKIP_LTS_PROPERTY_GATE=1 -> skipping (deliberate bypass)")
        return 0

    parse_text = _load_parser()

    if mode == "census":
        if not roots:
            _refuse("--census needs at least one directory")
        total = 0
        for root in roots:
            for f in sorted(Path(root).rglob("*.lts")):
                fs = check_text(f.read_text(encoding="utf-8", errors="replace"),
                                str(f), parse_text)
                if fs:
                    print(f"--- {f} ({len(fs)})")
                    for x in fs:
                        print(x.render())
                    total += len(fs)
        print(f"lts-property-gate: census total {total}")
        return 0

    if mode == "files":
        if not files:
            _refuse("--files needs at least one path")
        blocking: List[Finding] = []
        for p in files:
            try:
                text = Path(p).read_text(encoding="utf-8")
            except Exception as exc:
                _refuse(f"cannot read {p}: {exc!r}")
            blocking.extend(check_text(text, p, parse_text))
        return _report(blocking, [], len(files), "the named file(s)")

    # --- staged / range (the hook path) --------------------------------------------
    repo = _git(["rev-parse", "--show-toplevel"], os.getcwd()).strip()
    if not repo:
        _refuse("cannot resolve the committing worktree's root")
    where = f"range {rev_range}" if rev_range else "the staged set"
    if rev_range:
        # THE EMPTY-SET RULE, borrowed verbatim in spirit from pre-commit-tests.sh. "the
        # range genuinely touches no .lts" and "I could not determine the file set" were
        # the SAME code path there, and that conflation WAS the defect. Endpoints first,
        # then: a range that changed nothing at all did not describe the landing.
        for rev in [p or "HEAD" for p in rev_range.split("..")[:2]]:
            probe = subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet",
                                    f"{rev}^{{commit}}"], capture_output=True,
                                   env=_clean_env())
            if probe.returncode != 0:
                _refuse(f"--range endpoint {rev!r} does not resolve to a commit in {repo}")
        if not _git([*_diff_args(rev_range), "--name-only", "--diff-filter=ACMR"],
                    repo, scrub=True).strip():
            _refuse(f"range {rev_range!r} changed NO files — an undetermined change set, "
                    "not a pass. Check the endpoints (reversed? already-merged base? "
                    "wrong branch?).")
    paths = _changed_lts(repo, rev_range)
    if not paths:
        print(f"lts-property-gate: no .lts in {where} -> pass (nothing to reconcile)")
        return 0
    blocking, advisory = [], []
    for p in paths:
        text = _blob(repo, p, rev_range)
        added = None if all_lines else _added_lines(repo, p, rev_range)
        for f in check_text(text, p, parse_text, added_lines=added):
            (blocking if f.added else advisory).append(f)
    scope = ("the whole file(s) in " + where) if all_lines else (
        f"the lines {where} adds")
    return _report(blocking, advisory, len(paths), scope)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
