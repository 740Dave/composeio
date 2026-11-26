# Worktrunk Git-Hygiene Success Model — a portable methodology

Status: **LIVING PORTABLE REFERENCE** (not date-stamped; edit in place).
Audience: **other Intelligences on other repos** — specifically the another project
Intelligences — who want to adopt the same git-hygiene model composeio proved works.
Scope: this is a **transferable success model**, NOT a composeio migration plan and NOT a
fresh design. composeio's live configuration is cited throughout as the **reference
implementation**; the deep composeio-specific record is
[`WORKTRUNK-AGENTIC-STRATEGY-2026-08-26.md`](./WORKTRUNK-AGENTIC-STRATEGY-2026-08-26.md)
(the composeio-canonical doc — the freeze flag points there; this doc generalizes it and
folds in what post-dates it).

## Provenance of every `wt` claim in this doc

Validated against **`wt 0.74.0`** (`wt --version`, re-confirmed 2026-09-10 on macOS —
the same version the composeio-canonical doc scratch-proved against on 2026-08-26). `wt`
was released January 2026 and ships frequently; the `Unreleased` CHANGELOG section is
routinely non-empty. **Pin a version at adoption and re-read the live docs** — treat every
quote here as decaying.

- **Docs (read live 2026-09-10):** `https://worktrunk.dev`, `/merge/`, `/config/`.
  Behavioral claims marked *(scratch-proven)* were verified by the composeio-canonical
  engagement against the running binary, both arms (the op doing what it should AND
  refusing what it should refuse); those are quoted from that record and re-tie to
  `wt 0.74.0`.
- **Reference implementation (this repo, read at file:line):** `.config/wt.toml`,
  `.githooks/pre-commit`, `.githooks/post-commit`, `scripts/pre-commit-tests.sh`,
  `scripts/land-gate.sh`, `scripts/branch_hygiene.py` (+ `scripts/branch-hygiene.sh`),
  `scripts/land-and-prune.sh`, `scripts/repo-health-audit.sh`,
  `.claude/agents/git-expert.md`, `.claude/skills/wake-composeio/SKILL.md`.

⛔ Where a claim is sourced from the composeio record rather than a fresh run against your
own repo, treat it as a hypothesis to re-verify in a scratch clone with **your** pinned
`wt`. See **§6 What an adopter must verify locally**.

---

# 1 · THE SUCCESS MODEL — what "a clean tree" IS

The model is not "use `wt`." It is a small set of **invariants** about where work is allowed
to live, plus the mechanisms that make violating them a structurally-unreachable state
rather than a discipline anyone has to remember. An adopter who keeps the invariants and
swaps the mechanisms still has the model; an adopter who copies the config but drops an
invariant does not.

### The seven invariants

1. **Landed, not just pushed.** A branch that has been *pushed* is durable but not *done*;
   a branch that is *landed* has its content on trunk. **A branch is where completed work
   hides looking done.** The steady state is: no completed work parked in any branch.
   `[DEC-PUSH-IS-FREE]` is true for push and **false for land** — pushing costs nothing and
   is always safe; landing is a judgment and a gate. (Reference:
   `scripts/branch-hygiene.sh` header.)

2. **Verify land/abandon by content, NEVER by diff-stat.** A diff-stat (`N files, +X/-Y`)
   *lies on reorganized history*: a branch can show "16 commits absent from main" and be,
   by content, **empty** — and landing it would have **reverted a ratified 180° hardware
   panel rotation** (the `perf/hosyond-dirty-rows` case,
   `GIT-MODEL-git-expert-verdict-2026-08-17.md:141`). The sound instruments are
   **patch-id equivalence** (`git cherry`, `git patch-id`), **merge-tree**, and `wt`'s own
   `⊂` integrated oracle — never the numeric diff. This is a direct application of
   `[CXC-VISION-EPISTEMICS]`: git is one of the few instruments that is *sound in a stated
   direction*, but only when you ask it a content question, not a ref-position question.

3. **Landing makes git NATIVELY know.** Land by **rebase-onto-live-trunk + fast-forward**,
   so every landed commit is a literal **ancestor** of trunk and `git branch --merged` is
   authoritative. Do **not** land by `cherry-pick` — it mints a new SHA, the landed commit
   is never an ancestor, `--merged` cannot see it, and landed-but-unpruned branches pile up
   forever (Failure Story A). The ancestor property is what retires every homegrown
   patch-id/file-diff heuristic.

4. **Teardown is the LAST step, AFTER publication.** The worktree + branch are destroyed
   only *after* the content is published to the shared remote and provenance is recorded —
   never as part of the merge. Tearing down inside the merge destroys the execution context
   that would publish, and strands the land (Failure Story C).

5. **The origin ref is deleted last, guarded on trunk.** The durability-floor auto-push
   (invariant 6) creates an `origin/<branch>` ref that the local teardown does **not**
   remove. Delete it as the *final* teardown step, and **only after** confirming
   `origin/main` is at the landed SHA — so the branch's content is provably on trunk before
   its ref disappears. Skipping this leaks one origin ref per land (Failure Story D).

6. **The durability floor is automatic, never remembered.** Every commit auto-pushes the
   instant it exists (a `post-commit` hook), so there is no "committed-but-unpushed" window
   in which a worktree teardown can silently destroy real work (Failure Story E). The push
   is non-blocking and non-fatal — it can never fail or stall a commit.

7. **Per-branch build inputs are NEVER tracked on trunk.** A file that a *land gate reads
   from the worktree on disk* (a QA verdict, a per-branch config) is a **build input**, not
   trunk product. Tracking it on trunk (a) resurrects a modify/modify rebase-conflict tax on
   every concurrent land and (b) lets a rebased worktree reset the file to a **foreign
   branch's** value, so a land passes a green gate reading *another branch's instrument* —
   "green that lies" (Failure Story F). Such files must be **gitignored AND untracked**, and
   because gitignore alone cannot stop a force-`git add`, a **content-based pre-commit guard
   must refuse staging the path at all**.

### The lifecycle these invariants describe

```
DISPATCH   One isolated worktree + branch per unit of work (N agents = N disjoint trees).
           The isolation makes parallel WORK collision-free by construction: separate
           working dir, branch ref, and index. (Whoever creates the worktree — the agent
           harness or `wt` itself — the LAND path is what this model governs.)
WORK       Agent edits only inside its own worktree.
SNAPSHOT   Every commit auto-pushes (durability floor). A pre-commit gate fires per commit.
RETURN     Agent returns its branch name + a PERSISTED verdict file committed on the branch
           (never chat text — chat evaporates; a committed file lands-or-not with the code).
LAND       Serialized under a lock:
             rebase branch onto live trunk -> run pre-merge gate -> fast-forward trunk
           (no squash if per-commit history is required; see §Squash note). The merge does
           NOT remove the worktree and does NOT push.
PUBLISH    Push trunk to origin — the ONLY publisher (a no-commit ff pushes nothing itself).
           Confirm origin advanced by your own hand.
PROVENANCE Record where/when a capability landed against the TRUNK-side SHA range
           (OLD_MAIN..NEW_MAIN), synchronously, worktree still alive — because rebase
           rewrote the branch SHAs.
TEARDOWN   ONLY NOW: remove worktree + delete local branch (foreground), then delete the
           origin ref last, guarded on origin/main == landed SHA.
```

### A note on squash vs per-commit history (a decoupling worth stealing)

`wt merge` **squashes by default** (`https://worktrunk.dev/merge/`: *"Combines all commits
since target into one (like GitHub's 'Squash and merge')."*). composeio forbids squash
(`[DEC-DISPATCHED-BUILDS-COMMIT-TO-BRANCH]`, on per-defect-history + test-provenance
grounds). **The apparent conflict dissolves:** the native-landed (ancestor) property comes
from **rebase + fast-forward**, NOT from squashing. `wt merge --no-squash` preserves the
individual commits *and* still rebases+ff's, so each commit lands as an ancestor with a
stable trunk SHA. **An adopter chooses squash-vs-not independently of the hygiene model** —
it is a separate decision about whether you want "every trunk commit is one tested unit"
(squash) or "per-commit history on trunk" (`--no-squash`). Either honors invariant 3. ZB
should decide this on its own review culture; it is orthogonal.

---

# 2 · ALL CONFIGURATION DETAILS — the reference implementation, quotable

Everything in this section is composeio's live config. Treat it as the worked example;
§5 says what is universal vs what you re-point.

## 2.1 `.config/wt.toml` — the land config

⛔ **Load-bearing finding, verified `wt 0.74.0`:** the **entire `[merge]` / `[remove]` /
`[commit]` block in a PROJECT config is IGNORED.** `wt config show` reports each key as
*"belongs in user config (will be ignored)"*, and `https://worktrunk.dev/config/` confirms
*"Project-level `[merge]` configuration is not honored"* and *"project-level `[remove]` keys
are ignored; only user config applies."* So in composeio those keys are inert
future-proofing; **the land behaves by `wt`'s defaults, and every non-default intent is
enforced by the DANCE'S CLI FLAGS** (`--no-commit --no-squash --no-remove`), the only
honored lever. This is the single biggest place the tool's config surface does not do what
its shape implies — see Finding F1.

What the project config *can* do that IS honored: the **hooks** (`post-commit`,
`[[pre-merge]]`). Those are the working part of `.config/wt.toml`.

Reference `.config/wt.toml` (composeio, quoted essentials):

```toml
# Durability floor — wt-path half only (see the hook-scope caveat below):
post-commit = "git push -u origin HEAD"

# INERT on 0.74.0 (kept as intent/future-proofing; enforcement is the dance's CLI flags):
[merge]
squash = false   # honor per-commit-history ruling; ancestor property is from rebase+ff
commit = false   # never auto-commit working-tree changes onto trunk
rebase = true
ff     = true
remove = false   # teardown must NOT precede publication (enforced by --no-remove flag)
verify = true    # run the [[pre-merge]] gate; a failing hook ABORTS

[remove]
delete-branch = true

[commit]
stage = "none"

# The LAND GATE — array-of-tables = SEQUENTIAL pipeline, fail-fast:
[[pre-merge]]
qa-verdict = "scripts/land-gate.sh"

[[pre-merge]]
corpus-gate = "SDL_VIDEODRIVER=dummy scripts/pre-commit-tests.sh --range main..HEAD"
```

Three details an adopter must not paraphrase away:

- **`[[pre-merge]]` (array-of-tables) is a sequential fail-fast pipeline; `[pre-merge]`
  (single table) runs entries CONCURRENTLY.** composeio uses the array form so the cheap
  verdict check short-circuits the expensive suite. (`https://worktrunk.dev/config/`
  documents all three hook forms: scalar, `[hook]` table = concurrent, `[[hook]]`
  array-of-tables = sequential pipeline.)
- **The corpus gate MUST carry `--range main..HEAD`, never a bare invocation.** `pre-merge`
  hooks run **after rebase, before fast-forward** (`https://worktrunk.dev/merge/`:
  *"Hooks run after rebase, before merge. Failures abort."*), so the landed delta is already
  **committed** and the index is **empty**. A bare test runner reads the index, finds
  nothing, and prints "nothing staged -> pass" **having run zero tests** — green-by-
  construction. At that point local `main` is still pre-ff (the rebase base) and `HEAD` is
  the rebased tip, so `main..HEAD` is exactly the landing delta (scratch-proven 2026-08-26:
  bare -> 0 tests; `--range main..HEAD` -> classified the delta and ran the suite).
- **TOML ordering:** root-level scalar keys (`post-commit`) MUST precede the first table
  header, and the `[[pre-merge]]` array-of-tables MUST come last, or a bare key after it
  binds to the last pre-merge table instead of the document root.

⚠ **Hook-scope caveat (verified 0.74.0):** `wt`'s `post-commit` fires ONLY on a **wt-driven**
commit (`wt step commit`, or `wt merge`'s internal commit step). A plain `git commit` — what
an isolated agent runs — fires **no** `wt` hook. So the `wt.toml` `post-commit` is only the
wt-path half of the durability floor; the git-native half is the `.githooks/post-commit`
hook (§2.3).

**Approvals:** project hooks prompt for approval on first run
(`https://worktrunk.dev/config/`: stored in `~/.config/worktrunk/approvals.toml`). An
unattended land must either pre-approve (`wt config approvals add`) or pass `-y` on the
command, or it stalls on the prompt.

## 2.2 The land command (the only honored enforcement lever)

Because the config `[merge]` keys are inert, the correct land on `wt 0.74.0` is a **CLI
invocation**, serialized:

```
lockf "<primary>/.git/wt-land.lock" wt merge --no-commit --no-squash --no-remove -y
```

- `--no-squash` — keep per-commit history (drop if you want squash; §Squash note).
- `--no-commit` — *"Skip commit and squash"* (`https://worktrunk.dev/merge/`): the agent's
  already-committed history is rebased+ff'd and **nothing in the working tree can be swept
  onto trunk**. Scratch-proven both arms: default staging *auto-stages untracked scratch and
  rides it onto trunk*; `--no-commit` **refuses loudly** on an untracked-non-ignored file
  (`Cannot merge with --no-commit: <branch> has uncommitted changes … Commit or stash
  first`) — that refusal is the negative control working; **gitignored files are tolerated
  and are NOT landed**. (`--stage none` is the WRONG lever: it aborts `Nothing to commit` on
  a normal land because the agent already committed.)
- `--no-remove` — *"Keep worktree after merge"* (`https://worktrunk.dev/merge/`).
  LOAD-BEARING: default `remove=true` tears the worktree down as a **background** step of the
  merge, before the publish can run (Failure Story C). The `[merge] remove` config key
  cannot substitute — it is ignored on 0.74.0; only the flag is honored.
- `-y` — *"Skip approval prompts"* so an unattended land does not stall.
- ⛔ **`flock` does not exist on macOS.** Serialize with host-native `lockf` (or an atomic
  `mkdir` lock). Writing `flock` fails `command not found` and the merge never runs.
- ⛔ **Check `wt merge`'s OWN exit status**, never a pipe tail. Exit 0 = gate passed AND
  trunk fast-forwarded, worktree preserved (`○ Worktree preserved (--no-remove)`). A
  pre-merge failure prints `✗ pre-merge command failed: …` and leaves trunk unchanged — a
  refusal; report it, do not `--no-hooks` around it.

Then, in order: `git push origin main` (the **only** publisher — confirm origin advanced by
your own hand) -> record provenance against `OLD_MAIN..NEW_MAIN` -> `wt remove <branch>
--foreground -y` from the primary checkout -> delete origin ref last, guarded on
`origin/main == NEW_MAIN`. The full dance is the reference charter in
`.claude/agents/git-expert.md` ("The core dance"), which is the canonical step-by-step.

## 2.3 The two git hooks (wired by `core.hooksPath -> .githooks`)

**`.githooks/pre-commit`** — the commit gate, a thin wrapper delegating to
`scripts/pre-commit-tests.sh`. Two properties an adopter must preserve:

- **Derive the repo root from the COMMIT CONTEXT, not the hook's location.**
  `core.hooksPath` is ONE absolute path shared by every worktree; deriving from
  `BASH_SOURCE` always resolves to the *main* checkout, so a commit inside a worktree was
  gated by main's tests — **green by construction, never running the code being committed**
  (`[DEFECT-THE-PRE-COMMIT-TEST-GATE-6E7171]`). Use `git rev-parse --show-toplevel`. If it
  cannot resolve, **refuse loudly** — never silently fall back to an unverified tree.
- **The runtime-written-file guard + its self-check.** Files that live tooling rewrites at
  runtime are tracked-on-purpose but must not ride into unrelated commits as passengers. The
  guard allows them staged **ALONE**, refuses them mixed (override
  `COMPOSEIO_ALLOW_RUNTIME_FILES=1`), AND runs a **decay self-check**: each allowlist pattern
  must still match a tracked file, else the guard REFUSES and names the dead pattern — *"an
  instrument that can only report success is not an instrument."* (This is composeio-specific
  content; the *shape* — a guard that proves its own predicate is still live — is universal.)

**`.githooks/post-commit`** — the git-native durability floor, the half that fires on a plain
`git commit`. Invariants it must never violate:

- **Non-fatal, non-blocking.** git ignores a post-commit exit code, so it can never fail a
  commit — but a hook that *blocks* (a hung push on a dead network) stalls every commit. The
  push is **backgrounded, stdio-detached, and hard-timeout-wrapped**; the commit returns
  immediately, always.
- **Logs both outcomes** (`git-common-dir/wt-autopush.log`) with a timestamp — a push
  failure is logged and swallowed, never surfaced.
- **Skips cleanly** when a push would be wrong: detached HEAD, no `origin`, mid-rebase /
  mid-merge / mid-cherry-pick (an intermediate commit must not be published as the tip).

## 2.4 The pre-commit test gate — `scripts/pre-commit-tests.sh` and its arms

The gate has one classifier fed by **two input sources** (`--cached` for the git hook;
`--range <base>..<head>` for a landing), so a landing is gated by the *same* routing table as
a commit. Do not fork the routing. **Three exit codes, never two:** `0` green (or
legitimately nothing to gate), `1` a test failed, `2` the gate could not DETERMINE what to
test (block, loudly) — an empty file list must never silently mean pass.

Arms, in order:

- **Arm 0 — the `.lts` property-reconciliation gate.** Parses every staged/in-range `.lts`
  with the real parser; rejects if a property-shaped line did not become a property. Not
  skipped by the test-suite bypass. (composeio-specific to its corpus format.)
- **⛔ Arm 0.5 — `.qa/` MUST NOT be staged/tracked** (`[DEFECT-LAND-GATE-READS-A-FOREIGN-
  356CD7]`). This is the enforcement of invariant 7 and it is **general, not a composeio
  quirk**. `.qa/verdict.json` is a per-branch build input the land-gate reads from the
  worktree on disk; it is gitignored + untracked by ratified policy (commit `1651603`).
  gitignore alone did not hold — a broad `git add` force-staged it back in `7cdfac0`,
  silently regressing the policy. So the guard is **at staging** (the vector), **content-
  based** (`git diff --cached --name-only --diff-filter=ACMR -- .qa/`), and **before the
  test bypass** (the sweep rides a docs/hygiene commit, exactly where `SKIP_PRECOMMIT_TESTS`
  gets set). It refuses with the exact un-stage command and a traced override
  (`SKIP_QA_TRACKING_GUARD=1`). **Generalize this: any per-branch build input gets an
  identical staging guard.**
- **Test suites** — routed by tree (a python change runs the python suite; an intelligence
  change runs that suite; touched-area selftests). Rooted at the **tree**, not a named
  subdir, because *a mapping can silently cover less than its name claims* (a per-directory
  arm shipped two red maker tests unseen for 13 days). A structural test
  (`test_precommit_gate_reachability.py`) fails if any test directory is unreachable from the
  invocations.

Bypasses (each deliberate, each leaves a trace — `[AUTH-PATH-NOT-A-CAGE]`): `--no-verify`
(git skips the hook), `SKIP_PRECOMMIT_TESTS=1` (suites only), `SKIP_LTS_PROPERTY_GATE=1`
(arm 0 only), `SKIP_QA_TRACKING_GUARD=1` (arm 0.5 only).

⛔ **Range-mode strips ambient repo pointers.** `git -C <dir>` changes cwd but does NOT beat
`GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`, which git **exports into every hook**. Range
mode `unset`s them, or it silently classifies whatever repo invoked the hook instead of the
intended root (the wrong-tree class through a side door). `--cached` deliberately does not
strip them (a partial/amend commit legitimately uses a temp index).

## 2.5 The land gate — `scripts/land-gate.sh`

The first `[[pre-merge]]` hook. Runs after rebase, before ff, in the rebased worktree
(cwd = worktree root). Reads `.qa/verdict.json` **from disk**, passes IFF `pass === true`;
a missing file, unparseable JSON, or `pass != true` all **abort the land**. This makes "QA
verified" a *structural* precondition of "landed": the QA agent must **commit** its verdict
to the branch. ⛔ It captures the JSON reader's **own** exit status (a separate `python3`
process), never a pipe tail — *"a pipeline's exit status is its last command's, so
`check | tail; if $?` reports success when `check` failed."* (`[CXC-VISION-EPISTEMICS]
rule-negative-control`.)

## 2.6 The hygiene oracle — `scripts/branch_hygiene.py` (+ `branch-hygiene.sh`)

The single instrument all gates reuse (one oracle, no second implementation to drift). It is
a **thin reporter over the NATIVE landed oracle**: a branch is landed IFF its tip is an
ancestor of `origin/main` (rebase-ff) OR `wt` reports it `⊂` integrated (landed via
different history). The homegrown patch-id/file-diff heuristic is **retired**. It classifies
every ref and reports six axes on a `== totals: … ==` line:

| Axis | Meaning | Command |
|---|---|---|
| `stranded` | NOT landed — real unlanded work | `git branch --no-merged` + not `⊂` |
| `landed-unpruned` | on trunk, prune me | ancestor of base, survives |
| `wt-agent-cruft` | landed dispatch branches, prune me | landed + agent-prefixed |
| `dirty-worktrees` | worktrees holding uncommitted **UNIQUE** work | porcelain scan (churn-filtered) |
| `orphan-origin` | live `origin/<branch>` whose content is already on `origin/main` | `git ls-remote --heads` sweep |
| (`stale`) | landed-and-ahead legacy | classifier |

`MAX_UNLANDED = 10`: the audit **exits non-zero** when more than 10 stranded branches
survive OR any landed / `wt-agent-*` branch survives OR a dirty worktree holds unique work.
The **dirty-worktree pass is the axis the branch oracle is structurally blind to** — it
exists to surface uncommitted unique work *before a prune can destroy it* (a HIGH finding).
The **orphan-origin sweep** (added 2026-09-02) lists live remote heads and flags each whose
content is already on trunk (the durability-floor push left it behind); report-only, does not
flip the exit code (deletion is a human/git-expert action).

## 2.7 Prune-on-land — `scripts/land-and-prune.sh`

Seats prune as the **standard close of a landing** (not a later sweep) — deletes a landed
branch (local + its origin ref) in the same operation, which is what stops the pile from
re-forming. ⛔ **It REFUSES unless the branch is LANDED by the native oracle** (ancestor of
base OR `⊂`), so it can never delete unlanded work. It uses `git branch -D` on purpose: an
integrated-via-different-history branch is not SHA-merged, so `-d` would wrongly refuse — the
**landed guard is the real safety, not `-d`**. (This is the negative-control arm made
executable: demonstrate the refusal on a known-unlanded branch before trusting the reap.)

## 2.8 The standing safety-net — `scripts/repo-health-audit.sh`

Runs `branch_hygiene.py audit` **non-interactively on a schedule** (launchd/cron), writes a
dated report + `latest.txt`, and emits a bus alert on drift. Rationale: **wake/dusk gates
fire only when an agent is present to cross a session boundary; drift accumulates on
wall-clock time** — a weekend of parallel fanout with no dusk crossing is exactly when the
pile re-forms unseen. Same oracle as the gates. Canonical-checkout-only (derives root from
its own location so a mis-pointed agent cannot audit the wrong tree). Broker host is config,
not hardcoded.

## 2.9 The land-dance charter — `.claude/agents/git-expert.md`

The land dance in order, owned by a dedicated agent so it is never the architect's headache:
**(1)** confirm committed passing verdict on the branch; **(2)** fetch + capture
`OLD_MAIN`; **(3)** confirm worktree clean of untracked-non-ignored files; **(4)** land
under lock (`lockf … wt merge --no-commit --no-squash --no-remove -y`), check its OWN exit;
**(5)** `git push origin main` — the only publisher — confirm origin advanced by your own
hand; **(6)** record provenance against `OLD_MAIN..NEW_MAIN` (trunk SHAs), worktree still
alive; **(7)** teardown LAST — `wt remove <branch> --foreground -y` from the primary
checkout, then delete the origin ref guarded on `origin/main == NEW_MAIN`; **(8)** report:
the landed range, gate verdict, origin confirmed advanced, provenance recorded, teardown
confirmed gone including the origin-ref delete, and **that the ordering held**. A HARD RULE
in the same charter: **NEVER force-destroy a DIRTY worktree/branch** — `git status
--porcelain` must be empty first (see Failure Story E for why: `--force` is an unrecoverable
hard delete; there is no trash).

---

# 3 · VALIDATION CRITERIA — how you know the model holds

## 3.1 The steady-state target (the aspirational clean tree)

```
stranded = 0    landed-unpruned = 0    wt-agent-cruft = 0
dirty-worktrees = 0    origin-heads = 1 (just main)    orphan-origin = 0
```

The **wake/dusk hard gate** (`.claude/skills/wake-composeio/SKILL.md` §"Repo-health hard
gate") runs `scripts/branch-hygiene.sh` at session boundaries and **exits non-zero** — a
gate an Intelligence cannot cross without addressing — when more than 10 stranded survive OR
any landed / `wt-agent-*` branch survives OR a dirty worktree holds unique work. A non-zero
is a real finding: land the stranded worklist, prune the landed cruft (via the re-verifying
`land-and-prune.sh`), then re-run. (The `>10` threshold is a *tolerance for in-flight work*,
not the target; the target is 0.)

## 3.2 The three enforcement tiers

1. **Per-commit** — `.githooks/pre-commit` gate (green tests + guards) and
   `.githooks/post-commit` durability push.
2. **Per-land** — the `[[pre-merge]]` pipeline (verdict + suite over `main..HEAD`), the land
   lock, and the guarded teardown.
3. **Per-session + per-clock** — the wake/dusk hard gate and the scheduled
   `repo-health-audit.sh`. Session gates catch what an agent crosses; the scheduled run
   catches what accumulates while no one is looking. **You need both** — either alone has a
   blind spot (session boundary vs wall clock).

## 3.3 ⭐ THE ACID TEST — a concurrent multi-agent land

The stress test of whether the model holds is **two or more agents landing at once**. Under
concurrency: does the lock serialize (loser waits, re-rebases onto the now-advanced trunk,
re-runs the gate, then ff's)? Does each land publish before teardown? Does no land read a
foreign branch's build input?

⛔ **A DID-NOT-HOLD under this test is a real finding to SURFACE, not absorb.** This exact
test caught the `.qa/` regression on **2026-09-08**: the `7cdfac0` collateral force-add left
trunk tracking `.qa/verdict.json`, so a rebased concurrent land reset the file to a
**foreign branch's verdict** and could pass a green gate reading another branch's instrument
— *and* every concurrent land paid a modify/modify rebase-conflict tax. The fix was invariant
7 made structural (Arm 0.5). The acid test is the instrument that has the standing to falsify
the model; run it deliberately, and when it fails, the failure is the deliverable.

## 3.4 Negative-control doctrine (mandatory for every gate)

`[CXC-VISION-EPISTEMICS] rule-negative-control`: **an instrument that can only report
success is not an instrument.** Every gate and every demonstration must show **both arms** —
the op reaping/passing what it should AND refusing what it must not. Two concrete traps this
model has repeatedly hit:

- **The shell pipe-tail trap.** `if check | tail; then echo CLEAN; fi` reports CLEAN when
  `check` failed — `$?` is `tail`'s. Every gate here checks the operation's **own** status
  (or the resulting state directly), never a pipeline tail. Observed live: `python3 bh.py
  audit | tail` reported `$? == 0` while the audit had FAILED.
- **The environment-is-not-the-tree trap.** A green suite in a worktree that never seated
  `.mcp.json` presented **63 never-exercised tests as passes/absences rather than failures**.
  Before reporting "verified," ask what the check would have printed had it been broken; if
  the answer is "the same thing," nothing was verified. (Operational corollary for `wt`:
  seat `.mcp.json` / `wt step copy-ignored` in the rebased worktree before landing, or the
  pre-merge suite aborts on `FileNotFoundError`.)

## 3.5 Prove destructive ops in scratch, both arms, `--dry-run` first

Never validate a destructive `wt`/git operation against the primary checkout. Create a
scratch clone, demonstrate the op doing what it should AND refusing what it should refuse,
report the commands and their real output. Prefer `--dry-run` for the first run of anything
destructive (`wt step prune --dry-run` lists candidates; `branch_hygiene.py … --dry-run`
classifies without executing).

---

# 4 · THE FAILURE STORIES (the rationale — do not drop these)

A mechanism without its failure story gets dropped by an adopter as "ceremony." Each of the
following is a *measured* incident; each maps to exactly one mechanism above.

**A · The landed-but-unpruned pile (`cherry-pick` leaves non-ancestors forever).**
Landing by `cherry-pick` mints a new SHA, so the landed commit is never an ancestor of its
branch; `git branch --merged` cannot see it and the branch survives as invisible cruft.
Measured: **47 landed-unreaped** branches (up from 45 the day before, "trending up, zero
mechanisms deployed" — `CUTOVER-EVIDENCE-worktrunk-sweeps-2026-08-17.md:43`), inside a total
that reached the ~200-ref range. -> **Fixed by invariant 3** (rebase-ff makes landed commits
ancestors; the native oracle sees them; `land-and-prune` reaps them at land).

**B · The verdict-conflict tax (a per-branch build input tracked on trunk).**
When `.qa/verdict.json` was tracked, every concurrent land hit a modify/modify rebase
conflict on it — a tax paid on each land, for a file that is not trunk product. -> **Fixed by
invariant 7** (gitignore + untrack + Arm 0.5 staging guard).

**C · The teardown-strands-publication flaw (teardown before publish).**
With `remove=true`, `wt merge` tears the worktree down as a **background** step of the merge,
before the dance's `git push origin main` can run. Because a no-commit ff pushes nothing
itself, that push is the ONLY publisher — so teardown races and strands it: origin left
behind local main, the context that could push already gone (proven 2026-08-26 in scratch;
earlier lands survived by timing luck only — intermittent). -> **Fixed by invariant 4**
(`--no-remove`, teardown as explicit foreground LAST step).

**D · The origin-ref leak (51 refs).**
`wt remove` deletes the LOCAL branch + worktree only. The durability-floor auto-push created
an `origin/<branch>` ref that NOTHING in the teardown deleted, so every land left an orphan.
Measured: **51 accumulated** before a prune; the just-landed branch AND 6 cleanup refs had to
be `git push origin --delete`'d **by hand — the same manual workaround twice in one session**
(git-expert.md; repo-health-audit.sh). -> **Fixed by invariant 5** (delete the origin ref
last, guarded on `origin/main == landed SHA`) + the `orphan-origin` audit sweep.

**E · The committed-but-unpushed loss (and the no-trash reality).**
Precedent: `anthropics/claude-code#38287` — a harness's own worktree cleanup **silently
destroyed committed-but-unlanded commits**, and the obvious verification query could not see
the loss because `git branch --no-merged` cannot report on a branch that no longer exists.
Compounding it, `wt` has **NO recoverable trash**: verified 2026-09-02, `.git/wt/trash/`
stays EMPTY after `wt remove` / `wt step prune` **including `--force`**, and there are zero
`refs/wt-backup/*` (the backup ref only ever captures TRACKED changes swept into a *squash*,
never untracked files, never at `wt remove`). A removed worktree is deleted **directly**; the
only protection is that the non-forced path **REFUSES a dirty tree**, and `--force` is then an
unrecoverable hard delete. -> **Fixed by invariant 6** (auto-push every commit, so there is no
unpushed window) + the git-expert HARD RULE (never `--force` a dirty tree) + the
`dirty-worktrees` audit pass. ⛔ *An adopter must NOT assume `wt` has an undo.*

**F · The foreign-verdict trap ("green that lies").**
On 2026-09-08 the `7cdfac0` collateral force-add left trunk tracking `.qa/verdict.json`; a
rebased concurrent land reset the file on disk to **another branch's** verdict, so the land
could pass a green gate reading a foreign instrument. The content survived; the *meaning* did
not. -> **Fixed by invariant 7** (Arm 0.5 content-based staging guard — because gitignore
alone could not stop the force-add).

**G · Diff-stat lies on reorganized history.**
`perf/hosyond-dirty-rows` showed "16 commits absent from main"; an architect asserted
"pruning destroys real work." **False** — by content the branch was empty, and landing it
would have **reverted a ratified 180° panel rotation on live hardware**. Only `merge-tree`
and patch-id analysis could establish it
(`GIT-MODEL-git-expert-verdict-2026-08-17.md:141`). -> **Fixed by invariant 2** (verify
land/abandon by patch-id/content, never diff-stat) — and it is why the whole oracle is built
on ancestor + `⊂`, not numeric diffs.

---

# 5 · PORTABILITY / ADOPTION GUIDE

## 5.1 Universal vs composeio-specific

**Universal (adopt as-is; these are the model):**

- All **seven invariants** (§1). They are claims about *where work may live*, independent of
  language, CI, or agent harness.
- **Landing by rebase-ff, not cherry-pick** (invariant 3) and the native `--merged` + `⊂`
  oracle it enables (retiring all homegrown equivalence heuristics).
- **The land command shape** `wt merge --no-commit --no-squash --no-remove -y` under a lock,
  then publish -> provenance -> teardown-last -> origin-ref-last. (`--no-squash` is optional
  per §Squash note.)
- **The `[[pre-merge]]` fail-fast pipeline** with a **verdict gate** + a **suite gate carrying
  `--range <base>..<head>`** (never bare).
- **The durability floor** as a non-blocking, non-fatal, both-outcomes-logged git-native
  `post-commit` push.
- **The six-axis hygiene oracle** as one shared instrument, wired into a session hard gate AND
  a scheduled wall-clock audit.
- **The negative-control doctrine** — both arms, own exit status not a pipe tail, prove
  destructive ops in scratch first.
- **The "per-branch build input" pattern** (invariant 7 / Arm 0.5): any file a land gate reads
  from disk is gitignored + untracked + guarded at staging.

**composeio-specific (re-point or drop):**

- The **`.lts` corpus property gate** (Arm 0) and the corpus split
  (`~/.claude/factlattice_corpora`) — ZB has no `.lts`; replace Arm 0 with your own
  content-shape gate or drop it. The corpus-fork problem composeio faced (every worktree a
  fork of a single-source-of-truth graph) only applies if you have a graph corpus in-tree.
- The **runtime-written-file allowlist** (specific tracked paths) — re-derive the list for
  your repo, but keep the *shape*: allow-alone, refuse-mixed, decay self-check.
- **`SDL_VIDEODRIVER=dummy`** and the `.mcp.json`-seating prerequisite — composeio's render +
  MCP environment; substitute your suite's environment needs.
- The **bus alert** (MQTT broker) in the scheduled audit — swap for your notification channel,
  or drop.
- Branch/agent **naming** (`wt-agent-*`) — that prefix is the agent harness's, not `wt`'s;
  match your harness.
- **Push-everything-including-main** — composeio is a private single-developer repo where push
  is free. A team with a protected `main` and PRs must adapt: land into a branch you push and
  open a PR from, and let CI (not a local `[[pre-merge]]` hook) be the gate. See §5.3.

## 5.2 Minimum-viable adoption (the model with the least machinery)

If ZB wants the invariants without the full apparatus, adopt in this order — each step is
independently valuable and safe:

1. **Invariant 6 first — the durability floor.** A non-blocking git-native `post-commit`
   push. Immediately eliminates committed-but-unpushed loss; costs nothing; needs no `wt`.
2. **Invariant 3 — land by rebase-ff.** Switch the land path to `wt merge --no-commit
   --no-remove -y` (add `--no-squash` if you keep per-commit history). Now `git branch
   --merged` is your oracle.
3. **Invariants 4 + 5 — teardown last, origin-ref last, guarded.** Fixes the two strand/leak
   classes. This is a change to your land *procedure*, not new tooling.
4. **The hygiene oracle as a read-only audit.** Even before any gate, run a branch-hygiene
   report (ancestor + `⊂`) on a schedule so drift is *visible*. Report-only is a legitimate
   first deployment.
5. **Invariant 7 + Arm 0.5** the moment you introduce any per-branch build input (a QA
   verdict, a per-branch config the land reads from disk). Do not wait for the incident.
6. **The `[[pre-merge]]` gate + verdict persistence**, then the **session hard gate**, last —
   they are the highest-ceremony pieces and presuppose the rest.

Invariants 1 and 2 are *doctrine*, adopted the day you start (they cost nothing and prevent
the most expensive mistakes — landing/abandoning by the wrong instrument).

## 5.3 How a team (ZB) adapts the config to CI-or-no-CI

- **No CI, single developer, push-is-free (composeio's shape):** the local `[[pre-merge]]`
  hook IS the gate; land straight to trunk; push everything. Adopt as written.
- **CI + protected `main` + PR review (a likelier OtherProject shape):** keep invariants 1–7, but move
  the *gate* to CI. The local land becomes "rebase onto trunk, push the branch, open a PR";
  the verdict file and suite run in CI as required checks; merge is the forge's squash-or-
  rebase (choose per §Squash note); teardown-last and origin-ref-last still apply after the
  PR merges. The `[[pre-merge]]` hook can still run locally as a fast pre-flight, but CI is
  authoritative. `wt`'s value in this shape is worktree lifecycle + the `⊂` oracle for
  pruning merged branches, not the merge itself.
- Either way: **verify land/abandon by content (invariant 2)** and **never `--force` a dirty
  worktree (Failure Story E)** are non-negotiable regardless of CI.

## 5.4 Adoption ordering (safe sequence)

Ground truth first, deletions last. **(0)** Install + pin `wt`, read live docs, and in a
**scratch clone** confirm the §6 behaviors against your own repo. **(1)** Stand up the
read-only hygiene audit — measure your current pile before changing anything. **(2)** Deploy
the durability floor (invariant 6). **(3)** Switch the land path to rebase-ff with
teardown-last + origin-ref-last (invariants 3–5). **(4)** Add the per-branch-build-input
guard the moment you have one (invariant 7). **(5)** Add the `[[pre-merge]]` gate + verdict
persistence. **(6)** Wire the session hard gate + scheduled audit. **(7)** Only once the
native oracle is proven over ≥1 week of real lands, retire any homegrown equivalence
heuristics. Run the **acid test** (concurrent multi-agent land, §3.3) before declaring the
model held.

---

# 6 · What an adopter must verify LOCALLY (do not inherit these on faith)

The composeio record proved these against `wt 0.74.0` on composeio's repo. On a **different
version** or a **different repo shape**, re-verify each in a scratch clone, both arms:

1. **Does the project `[merge]`/`[remove]`/`[commit]` block get ignored?** On 0.74.0 it does
   (`wt config show` says "belongs in user config"). If your version honors it, you can move
   enforcement off the CLI flags into config — but confirm, do not assume. (Finding F1.)
2. **Does `wt merge --no-commit` refuse an untracked-non-ignored file and tolerate a
   gitignored one?** The whole "untracked scratch never lands" guarantee rests on this.
   Given `.git` in Dropbox and agents writing ignored artifacts, test explicitly.
3. **Does `wt`'s internal commit / merge honor `core.hooksPath` git hooks, or bypass them via
   libgit2 / `--no-verify`?** If it bypasses, your commit gate silently stops at land — which
   is why composeio also wires the suite into `[[pre-merge]]` (belt-and-suspenders). Confirm.
4. **Is there really no trash?** composeio verified `.git/wt/trash/` empty and zero
   `refs/wt-backup/*` after `wt remove`/`prune`/`--force` (2026-09-02). Re-confirm on your
   version before trusting any "undo."
5. **Does `wt step prune` / `wt remove` manage worktrees your AGENT HARNESS created (not
   `wt`)?** THE load-bearing adoption question. composeio confirmed `wt` treats Claude-Code
   `isolation:worktree` worktrees as ordinary registered worktrees (there is NO "foreign
   worktree" marker; `⚑` means duplicate-branch/path-mismatch, `⊟` means prunable). Verify on
   your harness's worktrees before trusting any prune.
6. **Does `--no-squash` + rebase yield the ancestor property** (so `git branch --merged`
   reports the landed branch), or force a merge commit? The oracle depends on it.

---

# 7 · Findings — where the composeio model BENDS the tool (report as findings, not features)

- **F1 — Project `[merge]`/`[remove]`/`[commit]` config is INERT on `wt 0.74.0`.** The config
  file's shape implies it configures the merge; it does not. All non-default land behavior is
  carried by **CLI flags** in the dance, which a flag cannot silently ignore the way the keys
  are. This is the largest bend: the reference `.config/wt.toml` keeps the block only as
  future-proofing, and an adopter reading only the config would be **wrong about what the
  land does**. Upstream *documents* this ("not honored" / "belongs in user config"), so it is
  a known limitation, not a surprise — but it means "the config is the source of truth" is
  false here; the dance is.

- **F2 — There is no recoverable trash; the safety net is REFUSAL, not undo.** The model
  leans on `wt remove`/`prune` **refusing a dirty tree**, plus a `dirty-worktrees` audit
  pass, plus never passing `--force`. This is composeio compensating for a missing tool
  affordance with process. An adopter who expects an undo will lose committed-but-unlanded
  work (Failure Story E / `claude-code#38287`).

- **F3 — The durability-floor auto-push is a `wt` hook only for wt-driven commits.** Plain
  `git commit` fires no `wt` hook, so composeio adds a git-native `post-commit`. The
  `wt.toml` `post-commit` alone would be half a floor. A bend the adopter must replicate if
  agents commit with plain git.

- **F4 — The `[[pre-merge]]` suite gate needs `--range main..HEAD` to test anything.** The
  natural (bare) wiring green-lights on zero tests because the index is empty post-rebase.
  This is a footgun in the interaction between `wt`'s hook timing and any index-reading test
  runner — composeio bends the runner to accept a range. Any adopter with an index-based
  pre-commit runner inherits this.

- **F5 — Teardown default (`remove=true`) is unsafe for a publish-after-merge dance.** The
  documented default tears down inside the merge; composeio must override it per-command
  (`--no-remove`) and move teardown to an explicit last step. For an adopter whose merge IS
  the publish (forge-side squash-merge), the default may be fine — the bend is specific to
  the "local ff + separate push" shape.

- **F6 — Provenance must move WHERE it is recorded, not just its format.** Because rebase
  rewrites branch SHAs, a decision-graph node "implemented at `<branch-sha>`" points at a
  commit on a deleted branch. composeio records provenance **after** the merge against the
  **trunk** range `OLD_MAIN..NEW_MAIN`, synchronously (not a background `post-merge` hook,
  which cannot be trusted to complete). Any adopter that records implementation provenance
  must make the same WHERE-in-the-pipeline change.

---

# 8 · What I could not determine

- **Behavior on `wt` versions newer than 0.74.0.** The `Unreleased` CHANGELOG is non-empty
  and upstream pushes frequently; any of the §6 behaviors (especially F1 — whether project
  `[merge]` keys become honored) could change silently. This doc is pinned to 0.74.0;
  re-read the live docs and re-run §6 at adoption. *Experiment that settles it:* `wt
  --version` + `wt config show` on the pinned binary in a scratch clone, plus the §6 both-arm
  tests.
- **Whether `wt`'s internal commit honors `core.hooksPath` on 0.74.0 was not re-run in THIS
  engagement** (it was flagged unresolved in the composeio-canonical doc's "what I could not
  determine" #1, mitigated by belt-and-suspenders `[[pre-merge]]` wiring). *Experiment:* in a
  scratch clone with a tracing `core.hooksPath` pre-commit, run `wt step commit` and a
  squash `wt merge`, observe whether the hook fires.
- **ZB's actual repo shape** (CI? protected `main`? PR review? languages? agent harness?) is
  unknown to me; §5.3 branches on it but I could not measure it. The adopter must supply it —
  it changes whether the local `[[pre-merge]]` hook or CI is the authoritative gate.
- **Whether ZB has any per-branch build input** (the invariant-7 trigger). If it does not
  today, Arm 0.5 is dormant-but-cheap insurance; if it introduces one later, the guard must
  precede it, not follow the incident.
