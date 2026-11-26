# Publishing to the public ComposeIO repository

Every push to `740Dave/composeio` passes all five release gates below before it is pushed. Each gate is judged by its
**exit code**, not by its printed summary: a summary can read green while the process exits non-zero. Record every
gate's exit code in the push's report. A gate that could not run (exit 2) is a failure, never a pass.

In the commands below, `BASE` is the branch the push lands on (normally `origin/main`), and `HEAD` is the commit being
pushed. `PRIVATE_DIR` is the directory that holds the private vocabulary (`publish-vocabulary.tsv`,
`disclosure-denylist.tsv`). That directory is never in this repository.

## The five gates

| # | gate | command | pass |
|---|---|---|---|
| 1 | candidate review | An independent review of `BASE..HEAD`, run on a **different model** than the one that wrote the change. It writes a report with a verdict. | The verdict is PASS (record it as exit 0). FAIL, or no report, is a failure (record it as exit 1). |
| 2 | cleanup | `python3 -I tools/cleanup_check.py --base BASE --head HEAD --scope <the globs the brief declares>` | exit 0 |
| 3 | private-term scan | `python3 -I tools/leakcheck.py diff --base BASE --head HEAD --pr-text <file holding the PR title, a blank line, and the body> --private-dir PRIVATE_DIR` | exit 0, with `RESULT: GREEN`. It scans every added line, every commit message in the range, and the PR title and body. |
| 4 | secret scan | `python3 -I tools/ci_gitleaks.py --tree .` | exit 0 (pinned gitleaks 8.30.1, sha256-checked, built-in rules) |
| 5 | history scan | `python3 -I tools/ci_gitleaks.py --tree . --history-base BASE --require-history` | exit 0. The run scans every commit in `BASE..HEAD` (with `-m`), so a secret added and then removed is still caught. |

Gates 3 to 5 also run in CI and in the merge queue (`.github/workflows/`). The merge queue runs main's copies of the
tools, never the PR's (`docs/leakcheck/RUNNER.md`). Running the gates by hand before the push does not replace that,
and CI does not replace running them by hand.

## Cleanup, for ComposeIO

A pushed change:

- adds or changes no path that `PUBLISH-EXCLUDE` names;
- adds no generated or scratch file:
  - anything under `.qa/`, `__pycache__/`, `.venv/` or any `private/` folder;
  - any file whose name contains `.bak`;
  - any re-rendered `python/tests/render/captures/*.png`;
- keeps its diffstat inside the scope its brief declares. Every changed path, deletions included, must match a
  `--scope` glob.

`tools/cleanup_check.py` checks all three and exits 1 on a violation. The negative-control cases `red-cleanup` and
`green-cleanup` in `tests/leakcheck/fixtures/cases.tsv` prove that it fires.

## Neutrality rules do not apply here

The disclosure guard can also load generated neutrality rules: agent ids and corpus ids, read from
`FACTLATTICE_NEUTRALITY_TERMS`. **They do not apply to ComposeIO.** This is HoP's selection, recorded as
`zd-dsd-answers DEC-DSD-1A21679CF61-D1`. Quoted verbatim, except for one company name, which the vocabulary blocks
and which is shown here as `[the ZB company]`:

> "Block secrets, private host names and IP addresses, absolute home, Dropbox and drive paths, [the ZB company] names,
> BSG game files and the vendor R503 PDF manuals; allow your name, your project names (BSG, carousel, R503, Haven,
> Gnomon) and agent role names such as Plumb and composeux-architect in text"

That company's corpus ids and product ids are caught by the vocabulary's `org-zb` and `org-zb-product` rules, so no
neutrality list is needed for them.

The scans therefore run **without** `--require-neutrality-terms`. Every guard run prints this line, and it is
expected:

    ⚠️  neutrality terms: NOT LOADED, no input named (set FACTLATTICE_NEUTRALITY_TERMS or pass --neutrality-terms); the neutrality-agent-id and neutrality-corpus-id rules were NOT checked.

It reports this decision. It is not a failure.
