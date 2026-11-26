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
- The `leakcheck` job runs the same script first, through `tools/queue_leakcheck.py`, and refuses to scan when it does not pass.

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
