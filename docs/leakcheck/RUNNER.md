# The self-hosted leak-check runner

The merge queue's `leakcheck` job runs on a self-hosted runner labelled
`[self-hosted, macOS, composeio-private-vocab]`. That runner's machine holds the private vocabulary of the disclosure
guard (`COMPOSEIO_LEAKCHECK_PRIVATE` in the runner's environment). Any workflow that reaches the runner can read
that directory, so who can start a workflow there decides who can read the vocabulary.

## Preconditions: checked by a script, not a promise

`tools/runner_preconditions.py` reads two repository settings through `gh api`:

1. **Interaction limit**: `collaborators_only`, with at least 7 days left before it expires. The script prints the
   expiry date, so its renewal (every 6 months) is visible. Outside accounts cannot open PRs at all while it holds.
2. **Approval for workflow runs from fork PRs**: `all_external_contributors`. No outside contributor's workflow runs
   until a maintainer approves it.

It exits 0 only when both hold. It exits 1 when one is unmet, and 2 when a setting cannot be read. Exit 2 is never a
pass. Reading these settings needs a token that can read the repository's administration settings.

**The rule:**

- The runner is registered **only after** `tools/runner_preconditions.py` exits 0.
- It is **deregistered** as soon as the script fails, for example when the interaction limit lapses.
- The `leakcheck` job does **not** read the settings itself (see below). It judges the latest recorded result first,
  through `tools/queue_leakcheck.py`, and refuses to scan when that record is missing, stale or not a pass.

## Where the check runs, and how the job sees it without a credential

The runner's account has no GitHub login, and the job's `GITHUB_TOKEN` cannot read either setting (both need admin
read). An admin token stored on the runner or in Actions secrets would be the very exposure these settings guard
against, so none is stored. The work is split:

1. **The live check runs on the maintainer's Mac**, under its own `gh` login, run by git-expert at least every
   12 hours and again just before labelling a PR `queue`:

       python3 -I tools/runner_preconditions.py --record --publish --repo 740Dave/composeio

   It reads both settings, then writes a dated JSON record (`checked_at`, `verdict` pass/fail/unreadable, the
   interaction-limit expiry, the fork-PR policy) to the repository's Actions variable `RUNNER_PRECONDITIONS`. A
   failing or unreadable check is recorded too, so a lapse replaces an older pass at once.
2. **The job reads that variable through the `vars` context**, which GitHub injects into the run with no token, and
   runs `runner_preconditions.py --verify-record`. It refuses (no scan) unless the record exists, parses, names this
   repository, says `pass`, is at most 26 hours old and not future-dated, and the interaction limit it saw still has
   at least 7 days left as of now.

**Residual risk, stated plainly.** (a) A setting changed after the last record is not seen for up to 26 hours: an
interaction limit removed by hand, or the fork-PR policy loosened, leaves a passing record in place until it goes
stale. Only a person can change either setting, and the record's age is printed on every run. (b) Anyone who can
write Actions variables (a collaborator, not an outside account and not a PR's `GITHUB_TOKEN`) could write a false
pass; collaborators are already trusted below. (c) If the Mac stops running the check, the queue goes red after 26
hours: fail-closed, not silent.

## Second layer: the job checks where it came from

The `leakcheck` job's first step refuses unless `github.workflow_ref` is the default branch's
`.github/workflows/merge-queue.yml`. This does not stop a hostile workflow: a workflow written to attack the runner
would simply leave the step out. It does make a misrouted run fail loudly instead of scanning.

The job runs no PR code. It checks out main and fetches the PR head as git objects without a token. Every check it
runs is main's copy: the guard, the diff front end, gitleaks and the negative control. A PR that edits those tools
does not decide its own result.

## Residual limit, stated plainly

A **collaborator** (anyone with write access) can still open a PR whose own workflow file names this runner's labels,
and that workflow runs with the PR's definition. Neither precondition stops a collaborator, and the
`workflow_ref` check does not either. This is the same known limit FactLattice accepted for its queue: collaborators
are trusted, and the protection is aimed at outside accounts. Removing it needs an ephemeral runner per job, or a
vocabulary that is never on a machine a PR's workflow can reach.
