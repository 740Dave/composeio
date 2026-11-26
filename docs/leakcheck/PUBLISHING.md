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

Where the gates run besides by hand:

- **Hosted CI** (`ci.yml`) runs gates 4 and 5 (gitleaks over the tree and the PR's commits). It also runs the
  negative control with a **synthetic** vocabulary of made-up terms. That proves the mechanism fires, but CI never
  runs gate 3 with the real vocabulary.
- **The merge queue** (`merge-queue.yml`) runs gate 3 with the **real** vocabulary, plus main's copy of gitleaks and
  the real-vocabulary negative control. These run only on the self-hosted runner that holds the vocabulary, and
  always from main's copies of the tools, never the PR's (`docs/leakcheck/RUNNER.md`).

Neither replaces running all five gates by hand before the push.

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

The disclosure guard can also load generic neutrality rules: agent ids and corpus ids, read from
`FACTLATTICE_NEUTRALITY_TERMS`. **They are not used for ComposeIO**, because HoP's selection allows agent role names
in text. The selection is recorded as decision `DEC-DSD-1A21679CF61-D1`. Quoted verbatim, except for one
company name, which the vocabulary blocks and which is shown here as `[the ZB company]`:

> "Block secrets, private host names and IP addresses, absolute home, Dropbox and drive paths, [the ZB company] names,
> BSG game files and the vendor R503 PDF manuals; allow your name, your project names (BSG, carousel, R503, Haven,
> Gnomon) and agent role names such as Plumb and composeux-architect in text"

The rest of what a neutrality list would cover is blocked by the private vocabulary's `org-zb` rules:

- that company's name;
- its corpus ids, by one prefix rule over the registered id prefixes;
- its product and component names.

These rules are built from named sources, not from a sweep: the corpus registry that the engine loads, and that
company's portfolio overview. The vocabulary file's header names both. A term whose owner or origin is unclear is
left out until it is decided, never added by guess.

The scans therefore run **without** `--require-neutrality-terms`. Every guard run prints this line, and it is
expected:

    ⚠️  neutrality terms: NOT LOADED, no input named (set FACTLATTICE_NEUTRALITY_TERMS or pass --neutrality-terms); the neutrality-agent-id and neutrality-corpus-id rules were NOT checked.

It reports this decision. It is not a failure.
