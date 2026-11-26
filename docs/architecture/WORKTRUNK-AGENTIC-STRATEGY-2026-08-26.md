# Worktrunk Agentic Git Strategy — composeio REFERENCE IMPLEMENTATION

**Status: CANONICAL composeio reference implementation. The freeze flag points here.**
**Brought current: 2026-09-10** (validated against `wt 0.74.0` on macOS).
Original clean-sheet design + cutover: 2026-08-26 (worktrunk-expert, dispatched). The
cutover it planned has since completed; this doc now states what is TRUE NOW, once — not
what changed between phases. The dated design log is preserved, condensed, in §8.

> ⚠ Filename kept deliberately — it is the referenced canonical path / freeze-flag target.
> The date in the name is the doc's birth date, not its currency; see the "Brought current"
> stamp above.

## What this doc is, and where the WHY lives

This is the composeio-**specific** HOW: the actual `.config/wt.toml`, the exact land
commands and flags, the git-expert dance as configured here, current repo state, and the
places composeio **bends** `wt 0.74.0`. **It is standalone-usable** — an operator landing a
branch on composeio can work entirely from this doc; you do not need the portable doc for
the config or commands.

The **universal model, the rationale, and the failure stories** live in the portable
methodology doc and are NOT duplicated here:
[`WORKTRUNK-GIT-HYGIENE-SUCCESS-MODEL.md`](./WORKTRUNK-GIT-HYGIENE-SUCCESS-MODEL.md) — the
seven invariants, the negative-control doctrine, and the seven failure stories (A–G). Read
that for WHY a step exists; read this for HOW composeio does it. Cross-referenced both ways.

## Changelog — what the 2026-09-10 "brought current" pass folded in

- **Collapsed the Phase A / Phase B design log into current-state statements.** The body no
  longer reads as phases a reader must reconcile; §8 keeps the dated provenance.
- **Origin-ref-leak fix (commit `7cdfac0`)** — teardown now deletes the branch's **origin**
  ref LAST, guarded on `origin/main == NEW_MAIN`; plus `branch_hygiene.py`'s **orphan-origin**
  sweep. Prevented a measured ~51-ref pileup. Was ABSENT from the prior version (postdated it).
- **The `.qa/verdict.json` UNTRACK policy (commit `1651603`)**, its silent regression
  (`7cdfac0` collateral), the foreign-verdict "green that lies" incident (2026-09-08), the
  re-untrack (`d8f72dc`), and the **systemic pre-commit guard** that closes it (commit
  `56214a0`, `pre-commit-tests.sh` **Arm 0.5**). ⛔ **This CORRECTS a now-false claim in the
  prior version** (see §7, correction C4).
- **The hygiene scripts as they now stand** (`branch_hygiene.py` six-axis oracle,
  `land-and-prune.sh`, `repo-health-audit.sh`) and the **wake/dusk repo-health hard gate**.
- **The six local-bend findings (F1–F6)**, F1 inert-config being the load-bearing one.
- **Corrected outright-false statements** carried in the old clean-sheet body: the
  backup-ref "recoverable" claim, the "land removes the worktree in the merge" claim, the
  "commit the verdict" claim, and `flock`. See §7.

---

# 1 · Current repo state (measured 2026-09-10)

| Metric | Value |
|---|---|
| `wt` version | **0.74.0** (`~/.cargo/bin/wt`) |
| Total worktrees | **1** (`git worktree list`) |
| Local branches | **1** (`main`) |
| Tracked `.lts` corpus files in this repo | **0** (corpus moved to `~/.claude/factlattice_corpora`, remote `dropbox` — a SEPARATE repo; do NOT point `wt` at it) |
| `.config/wt.toml` present + driving lands | **yes** |
| `core.hooksPath` | `.githooks` (shared by every worktree) |

⚠ The cutover the 2026-08-26 design planned (from 48 worktrees / 117 branches / ~200 refs)
has **completed**: the tree is clean, the lifecycle is live, the heuristic oracle is retired.
The old "DELIVERABLE 2 — migration plan" is therefore **history, not current guidance** — it
is condensed in §8. What follows is the operating manual for the steady state.

---

# 2 · The land command (composeio, exact — copy-paste)

Run **from the branch's own worktree** (so `wt merge`'s "current branch" is the branch you
are landing; target defaults to `main`). `LOCK=<primary-checkout>/.git/wt-land.lock`.

```
lockf "$LOCK" wt merge --no-commit --no-squash --no-remove -y
```

Then, in strict order (each is a separate step; NONE is done by the merge):

```
git push origin main                 # the ONLY publisher — wt ff'd LOCAL main only
NEW_MAIN=$(git rev-parse main)
test "$(git rev-parse origin/main)" = "$NEW_MAIN" || { echo "origin did NOT advance — STOP"; exit 1; }
git rev-list --reverse "$OLD_MAIN..$NEW_MAIN"   # provenance: the landed commits, by TRUNK SHA
cd "<primary-checkout>"                          # you cannot remove your own cwd
wt remove <branch> --foreground -y               # teardown — worktree + LOCAL branch, synchronous
# origin-ref delete — LAST, guarded on the content being durable on trunk:
test "$(git rev-parse origin/main)" = "$NEW_MAIN" || { echo "origin/main NOT at landed SHA — do NOT delete origin/<branch>"; exit 1; }
git ls-remote --exit-code --heads origin "<branch>" >/dev/null 2>&1 \
  && git push origin --delete "<branch>" \
  || echo "no origin/<branch> to delete (already gone)"
```

Why each flag/step, in one line each (full rationale → portable doc §1, §4):

- `--no-squash` — honor `[DEC-DISPATCHED-BUILDS-COMMIT-TO-BRANCH]` (per-defect commits + test
  provenance). The native ANCESTOR property comes from **rebase + ff**, not from squashing.
- `--no-commit` — land ONLY already-committed history; nothing in the working tree can be
  swept onto trunk. **Refuses loudly** on an untracked-non-ignored file (correct — that
  refusal is the negative control); **gitignored files are tolerated and NOT landed**.
- `--no-remove` — ⛔ LOAD-BEARING. The merge's default teardown is a *background* step that
  runs BEFORE `git push origin main` can, stranding the land (origin left behind local main).
  Teardown must be the explicit LAST step, after publication.
- `-y` — skip the project-hook approval prompt (gate commands pre-approved via
  `wt config approvals add`), so an unattended land does not stall.
- `lockf` (⛔ **NOT `flock`** — it does not exist on macOS) — serialize concurrent lands: the
  loser waits, re-rebases onto the now-advanced main, re-runs the pre-merge gate, then ff's.
- `git push origin main` — the ONLY publisher; a no-commit ff pushes nothing itself. Confirm
  origin advanced **by your own hand**.
- Provenance against `OLD_MAIN..NEW_MAIN` — rebase rewrote the branch SHAs; a node pointing
  at a branch-side SHA points at a commit on a deleted branch. Record TRUNK-side, synchronously.
- Origin-ref delete LAST, guarded on `origin/main == NEW_MAIN` — `wt remove` deletes only the
  LOCAL branch+worktree; the durability-floor auto-push left an `origin/<branch>` ref that
  nothing else removes (this is the `7cdfac0` fix; without it, one orphan ref leaks per land).

⛔ **Check `wt merge`'s OWN exit status**, never a pipe tail. Exit 0 = gate passed AND trunk
fast-forwarded, worktree preserved (`○ Worktree preserved (--no-remove)`). A pre-merge
failure prints `✗ pre-merge command failed: …` and leaves trunk UNCHANGED — a refusal; report
it, do not `--no-hooks` around it.

Capture `OLD_MAIN=$(git rev-parse main)` **before** the merge (after `git fetch origin`).

---

# 3 · The full land dance (git-expert, as configured here)

The canonical step-by-step lives in `.claude/agents/git-expert.md` ("The core dance"); this
is the operator summary. Nine steps:

1. **Confirm a committed, passing verdict is present on the branch's disk.** The QA agent
   must have written `.qa/verdict.json` (`{"pass": true, …}`) into the branch worktree. ⛔
   See §5 — this file is **gitignored + UNTRACKED on purpose**; the land-gate reads it from
   the worktree **on disk**, it is not tracked on trunk. If absent/failing, the work is not
   landable; hand back, do not land.
2. **Fetch + capture pre-land trunk SHA:** `git fetch origin`; `OLD_MAIN=$(git rev-parse main)`.
3. **Confirm the branch worktree is clean of untracked-non-ignored files** (`git status
   --porcelain`). A `??` line that is not gitignored will make `--no-commit` refuse
   (correctly). Decide: scratch (remove) or real work (commit on the branch first). Never
   `-f` past it.
4. **Land — serialized, no squash, no working-tree commit, NO teardown:** the `lockf … wt
   merge --no-commit --no-squash --no-remove -y` from §2. Check its OWN exit status.
5. **Push the trunk — the ONLY publisher:** `git push origin main`; confirm `origin/main`
   advanced to `NEW_MAIN` by your own hand. If push fails, STOP and do NOT tear down — the
   branch is your only retry context.
6. **Record provenance against the TRUNK SHAs, synchronously, worktree still alive:**
   `git rev-list --reverse "$OLD_MAIN..$NEW_MAIN"`. Prefer a durable address (file + symbol +
   `OLD_MAIN..NEW_MAIN` range) over a bare SHA. If `.lts` files were in the range, run the
   corpus/property gate over that range explicitly (a fast-forward fires no `pre-commit`).
7. **Teardown LAST — from OUTSIDE the worktree, AFTER push+provenance:**
   `cd <primary-checkout>; wt remove <branch> --foreground -y`. `--foreground` makes it
   synchronous (no background race). `[remove] delete-branch` (default true) deletes the
   local branch too.
8. **Origin-ref delete LAST, guarded** (the `7cdfac0` fix): confirm `origin/main == NEW_MAIN`,
   then `git push origin --delete <branch>`. Confirm no orphaned `origin/<branch>` survives.
9. **Report:** the landed range (`OLD_MAIN..NEW_MAIN`, trunk SHAs), the pre-merge gate
   verdict, origin confirmed advanced, provenance recorded, worktree+branch torn down
   (confirmed gone) INCLUDING the origin-ref delete, and **that the ordering held** (worktree
   alive through push+provenance, removed only after). If you refused, say exactly why.

⛔ **HARD RULE — never force-destroy a DIRTY worktree/branch.** Before ANY `wt remove
--force`, `git worktree remove --force`, or `git branch -D`, confirm `git status --porcelain`
in that worktree is empty (only known never-commit churn — `captures/*.png`, `metrics.db`,
`.mcp.json` — may be present). There is **NO recoverable trash** (see §6, F2): `--force` is an
unrecoverable hard delete. If a gate-dirtied worktree blocks the final `wt remove`, restore
the tracked files (`git -C <wt> checkout -- <paths>`) + drop untracked artifacts and retry —
publication already completed at steps 5–6, so a teardown hiccup can no longer strand the land.

⚠ **Operational prerequisite:** the rebased worktree must have `.mcp.json` seated (or `wt
step copy-ignored`), or ~20 tests fail `FileNotFoundError` and abort the land (the
worktree-is-not-the-environment trap). Seat it before landing.

---

# 4 · Configuration reference (the actual composeio files)

## 4.1 `.config/wt.toml`

⛔ **F1 — the `[merge]`/`[remove]`/`[commit]` block is INERT on `wt 0.74.0`.** `wt config
show` reports each key as *"belongs in user config (will be ignored)"*, and there is no user
config, so **the land behaves by wt's DEFAULTS**. The keys are kept set-to-intent purely as
future-proofing. **Enforcement lives ENTIRELY in the dance's CLI flags** (`--no-commit
--no-squash --no-remove`), the only honored lever — a flag cannot be silently ignored the way
these keys are. Do NOT drop the flags trusting the file. (Durable option, architect's call:
move the block into USER config under `[projects."github.com/740Dave/composeio-internal"]`,
where wt honors it.)

The **honored** parts of the file are the hooks: `post-commit` and the two `[[pre-merge]]`
entries.

```toml
post-commit = "git push -u origin HEAD"     # wt-path half of the durability floor (see caveat)

[merge]            # ⛔ INERT on 0.74.0 — kept as intent only:
squash = false
commit = false
rebase = true
ff     = true
remove = false     # the guarantee is the CLI --no-remove, not this key
verify = true

[remove]
delete-branch = true

[commit]
stage = "none"

[[pre-merge]]      # array-of-tables = SEQUENTIAL fail-fast pipeline (a single [pre-merge] runs CONCURRENTLY)
qa-verdict = "scripts/land-gate.sh"

[[pre-merge]]
corpus-gate = "SDL_VIDEODRIVER=dummy scripts/pre-commit-tests.sh --range main..HEAD"
```

Three details not to paraphrase away:

- **`[[pre-merge]]` (array-of-tables) is a sequential fail-fast pipeline**; the cheap verdict
  check short-circuits the expensive suite. `[pre-merge]` (single table) would run them
  concurrently — not what we want.
- **The corpus gate MUST carry `--range main..HEAD`.** `pre-merge` hooks run **after rebase,
  before fast-forward**, so the landed delta is already committed and the index is empty. A
  BARE invocation reads the empty index and prints *"nothing staged → pass"* having run
  **zero tests** — a silent green-by-construction lie. At pre-merge, local `main` is still
  pre-ff (the rebase base) and `HEAD` is the rebased tip, so `main..HEAD` is exactly the
  landing delta (scratch-proven 2026-08-26: bare → 0 tests; `--range main..HEAD` → classified
  the delta and ran the suite). ⛔ Do NOT revert to bare.
- **TOML ordering:** root-level scalar keys (`post-commit`) MUST precede the first table
  header, and `[[pre-merge]]` MUST come last, or a bare key after it binds to the last
  pre-merge table instead of the document root.

⚠ **Durability-floor scope:** `wt`'s `post-commit` fires ONLY on a **wt-driven** commit (`wt
step commit`, or `wt merge`'s internal commit step). A plain `git commit` — what
`isolation:worktree` agents run — fires no `wt` hook. So this line is only the wt-path half;
the git-native half is `.githooks/post-commit` (§4.2).

## 4.2 The two git hooks (`core.hooksPath = .githooks`)

**`.githooks/pre-commit`** — thin wrapper → `scripts/pre-commit-tests.sh`. Two properties:

- **Derives the repo root from `git rev-parse --show-toplevel`, NOT from `BASH_SOURCE`.**
  `core.hooksPath` is one absolute path shared by every worktree; deriving from the hook's
  location always resolved to the *main* checkout, so a commit inside a worktree was gated by
  main's tests — green by construction, never running the committed code
  (`[DEFECT-THE-PRE-COMMIT-TEST-GATE-6E7171]`). On failure to resolve, it **refuses loudly**.
- **The runtime-written-file guard + decay self-check.** Files live tooling rewrites are
  tracked-on-purpose; the guard allows them staged **ALONE**, refuses them mixed (override
  `COMPOSEIO_ALLOW_RUNTIME_FILES=1`), and each allowlist pattern must still match a tracked
  file or the guard refuses and names the dead pattern. Current `RUNTIME_PATTERNS`:
  `compose_projects/intelligence/meta/*.meta.json`, `python/tests/render/captures/*.png`,
  `docs/improvement_suggestions/coordination/state-data-scientist.json`.

**`.githooks/post-commit`** — the git-native durability floor (the half that fires on a plain
`git commit`). Invariants: **non-fatal, non-blocking** (backgrounded, stdio-detached,
hard-timeout-wrapped — the commit returns immediately, always); **logs both outcomes** to
`git-common-dir/wt-autopush.log`; **skips cleanly** on detached HEAD, no `origin`, or
mid-rebase/merge/cherry-pick (an intermediate commit must not be published as the tip).

## 4.3 The pre-commit test gate — `scripts/pre-commit-tests.sh`

One classifier, two input sources (`--cached` for the hook; `--range <base>..<head>` for a
landing). **Three exit codes, never two:** `0` green (or legitimately nothing to gate), `1` a
test failed, `2` the gate could not DETERMINE the change set (block, loudly — an empty file
list must never silently pass). Arms in order:

- **Arm 0 — the `.lts` property-reconciliation gate** (`lts_property_gate.py`, LAYER 3 of
  `[SPEC:AUTH-PATH]`): parses every staged/in-range `.lts` with the real parser, rejects if a
  property-shaped line did not become a property. Skipped only by `SKIP_LTS_PROPERTY_GATE=1`.
- **⛔ Arm 0.5 — `.qa/` MUST NOT be staged/tracked** (`[DEFECT-LAND-GATE-READS-A-FOREIGN-
  356CD7]`, commit `56214a0`). See §5. Content-based (`git diff … --diff-filter=ACMR -- .qa/`),
  runs **at staging** (the regression vector) and **BEFORE the test bypass** (the sweep rides
  a docs/hygiene commit where `SKIP_PRECOMMIT_TESTS` gets set). Refuses with the exact
  un-stage command; traced override `SKIP_QA_TRACKING_GUARD=1`.
- **Test suites** — routed by **tree** (a `python/` change runs the python suite; an
  `compose_projects/intelligence/` change runs that suite; touched-area selftests). Rooted at
  the tree, not a named subdir, because *a mapping can silently cover less than its name
  claims* (a per-dir arm shipped two red maker tests unseen for 13 days). Structural guard:
  `test_precommit_gate_reachability.py`. Suite bypass: `SKIP_PRECOMMIT_TESTS=1`; everything:
  `--no-verify`.

⛔ **Range mode strips ambient repo pointers** (`unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
…`): `git -C <dir>` changes cwd but does NOT beat those env vars, which git exports into every
hook — inherit them and range mode silently classifies whatever repo invoked the hook. `--cached`
deliberately does not strip them (a partial/amend commit legitimately uses a temp index).

## 4.4 The land gate — `scripts/land-gate.sh`

First `[[pre-merge]]` hook. Runs after rebase, before ff, in the rebased worktree (cwd =
worktree root). Reads `.qa/verdict.json` **from disk**, passes IFF `pass === true`; missing
file, unparseable JSON, or `pass != true` all **abort the land**. ⛔ It captures the JSON
reader's OWN exit status (a separate `python3` process), never a pipe tail.

## 4.5 The hygiene oracle — `scripts/branch_hygiene.py` (+ `branch-hygiene.sh`)

The single instrument all gates reuse (one oracle, no drift). A thin reporter over the
**NATIVE landed oracle**: landed IFF the tip is an ancestor of `origin/main` (rebase-ff) OR
`wt` reports it `⊂` integrated. The homegrown patch-id/file-diff heuristic is **retired**.
Reports six axes on a `== totals: … ==` line:

`stranded` (real unlanded work) · `landed-unpruned` (on trunk, prune me) · `wt-agent-cruft`
(landed dispatch branches) · `dirty-worktrees` (uncommitted UNIQUE work — the axis the branch
oracle is structurally blind to; HIGH, because a prune would destroy it) · `orphan-origin`
(live `origin/<branch>` whose content is already on `origin/main` — the durability-push
leftover; the `7cdfac0` sweep) · `stale`.

`MAX_UNLANDED = 10`: **exits non-zero** when >10 stranded survive OR any landed / `wt-agent-*`
branch survives OR a dirty worktree holds unique work. Steady-state target is **all axes at
0** (`origin-heads = 1`, just `main`).

## 4.6 Prune-on-land — `scripts/land-and-prune.sh`

Seats prune as the standard close of a landing (deletes a landed branch, local + origin ref,
in the same operation). ⛔ **REFUSES unless the branch is LANDED by the native oracle** —
cannot delete unlanded work. Uses `git branch -D` on purpose (an integrated-via-different-
history branch is not SHA-merged, so `-d` would wrongly refuse; the landed guard is the real
safety). Demonstrate the refusal on a known-unlanded branch before trusting the reap.

## 4.7 The wake/dusk repo-health hard gate + scheduled audit

- **`.claude/skills/wake-composeio/SKILL.md` §"Repo-health hard gate"** runs
  `scripts/branch-hygiene.sh` at session boundaries and is a HARD gate: a non-zero is a real
  finding the Intelligence must address (land the stranded worklist, prune the landed cruft
  via the re-verifying `land-and-prune.sh`, re-run).
- **`scripts/repo-health-audit.sh`** runs the same oracle **non-interactively on a schedule**
  (launchd/cron), writes a dated report + `latest.txt`, emits a bus alert on drift. Rationale:
  session gates fire only when an agent crosses a boundary; **drift accumulates on wall-clock
  time** — a weekend of parallel fanout with no dusk crossing is when the pile re-forms unseen.
  Canonical-checkout-only; broker host is config, not hardcoded.

---

# 5 · The `.qa/verdict.json` policy (this session, 2026-09-08) — ratified

This is the composeio instance of the portable doc's **invariant 7** ("per-branch build
inputs are never tracked on trunk") and **Failure Story F** ("green that lies"). Read the
portable doc for the general pattern; here is the composeio-specific ruling and its history.

**Policy (ratified, commit `1651603`):** `.qa/verdict.json` is a **per-branch build input,
not trunk product**. The land-gate reads it from the rebased worktree **on disk**, so it is
**gitignored (`.gitignore:112`) AND deliberately UNTRACKED**. It is NOT committed to trunk.

⛔ **This CORRECTS the prior version of this doc**, which said the QA agent must *commit* the
verdict "so it lands-or-not with the code." That mechanism is now **false and forbidden** —
committing/tracking it:
- **(a)** resurrects a modify/modify **rebase-conflict tax** on every concurrent land, and
- **(b)** lets a rebased worktree reset the file to a **FOREIGN branch's verdict**, so a land
  can pass a GREEN gate reading another branch's instrument (`[CXC-VISION-EPISTEMICS]`).

The *persistence* intent survives (the verdict must be a file, not evaporating chat text); the
*mechanism* flipped from "commit it" to "write it untracked on disk in the worktree, where the
pre-merge gate reads it in place."

**The incident (2026-09-08):** gitignore alone did NOT hold. A broad `git add` in an
unrelated hygiene commit (`7cdfac0` collateral) **force-staged the verdict back onto trunk**,
silently regressing `1651603`. That (a) brought back the rebase-conflict tax and (b) left
trunk tracking a foreign branch's verdict — a concurrent land could then read the wrong
instrument. Caught by the **concurrent multi-agent land acid test** (the model's own stress
test — a did-not-hold surfaced, not absorbed).

**The fix:** re-untrack (`d8f72dc`) + a **systemic pre-commit guard** (`56214a0`,
`pre-commit-tests.sh` **Arm 0.5**) that refuses any staged `.qa/` path — because **gitignore
alone cannot stop a force-`git add`**. The guard is content-based, at the staging vector, and
before the test bypass. Override (traced): `SKIP_QA_TRACKING_GUARD=1`.

⭐ **Generalize, do not treat as a composeio quirk:** any file a land gate reads from disk is a
per-branch build input and gets the same treatment — gitignored + untracked + a staging guard.

---

# 6 · Local bends — composeio findings against `wt 0.74.0` (report as findings, not features)

These are the places composeio's model bends the tool. Full write-ups + the universal framing
are in the portable doc §7; here is the composeio-local statement of each.

- **F1 — Project `[merge]`/`[remove]`/`[commit]` config is INERT.** `wt config show` confirms
  "belongs in user config." All non-default land behavior rides on **CLI flags** in the dance.
  The `.config/wt.toml` block is kept as intent-only future-proofing. **The largest bend: an
  operator reading only the config would be WRONG about what the land does.**
- **F2 — There is NO recoverable trash.** Verified 2026-09-02: `.git/wt/trash/` stays EMPTY
  after `wt remove` / `wt step prune`, **including `--force`**; zero `refs/wt-backup/*`. A
  removed worktree is deleted DIRECTLY. The backup ref only ever captures TRACKED changes
  swept into a *squash* — never untracked files, never at `wt remove`. The safety net is
  **REFUSAL of a dirty tree** + the git-expert HARD RULE (never `--force` a dirty tree) + the
  `dirty-worktrees` audit pass. ⛔ Do NOT assume `wt` has an undo.
- **F3 — The durability-floor auto-push is a `wt` hook only for wt-driven commits.** Plain
  `git commit` fires none, so composeio adds the git-native `.githooks/post-commit`.
- **F4 — The `[[pre-merge]]` suite gate needs `--range main..HEAD`** or it green-lights on
  zero tests (index empty post-rebase). composeio bends its index-reading runner to accept a
  range.
- **F5 — The teardown default (`remove=true`) is unsafe for a publish-after-merge dance** and
  must be overridden per-command with `--no-remove`, with teardown moved to an explicit last
  step. (composeio's shape is "local ff + separate push," so the merge is not the publish.)
- **F6 — Provenance must move WHERE it is recorded**, not just its format: post-merge, against
  the TRUNK range `OLD_MAIN..NEW_MAIN`, synchronously (not a background `post-merge` hook,
  which cannot be trusted to complete before the pipeline moves on).

---

# 7 · Corrections — what the prior version claimed that is now FALSE (not just stale)

Surfaced for confirmation; each is corrected in the body above.

- **C1 — "The land ALWAYS tears the worktree down and deletes the branch in the same
  command" / `remove=true` as the recommended default.** FALSE now. Teardown in-merge is a
  background step that strands publication; the land runs `--no-remove` and teardown is the
  explicit LAST step (§2–3, F5).
- **C2 — "wt's own backup ref … uncommitted work at land time is recoverable from that ref
  even after the branch is deleted."** FALSE. The backup ref captures only TRACKED changes
  swept into a *squash*; under `--no-commit` nothing is swept, and there is NO trash at all.
  **Uncommitted UNTRACKED work is NOT recoverable** (F2). The prior version presented this ref
  as a general durability net; it is not.
- **C3 — Land command `wt merge --no-squash` (only).** Now `wt merge --no-commit --no-squash
  --no-remove -y`. `--no-squash` alone leaves the merge auto-staging working-tree scratch onto
  trunk and tearing the worktree down before the push.
- **C4 — "The QA subagent must COMMIT its verdict to a file on the branch … a committed file
  is landed-or-not with the code."** FALSE now — ratified policy (`1651603`) is the file is
  gitignored + UNTRACKED and read from disk; committing it causes the foreign-verdict trap
  (§5). The persistence intent survives; the "commit it" mechanism does not.
- **C5 — "Wrap git-expert's land step in a `flock`."** FALSE on macOS — `flock` does not
  exist; use `lockf` (or an atomic `mkdir` lock).
- **C6 — Measured state "48 worktrees / 117 branches" and "a cut-over now is NOT practical."**
  STALE/superseded — the cutover completed; the tree is at 1 worktree / 1 branch and the
  lifecycle is live (§1). The old migration plan is history (§8), not current guidance.

---

# 8 · History / provenance (condensed) + what remains unverified

## 8.1 Design log (dated, condensed)

- **Phase A (2026-08-26)** — clean-sheet target design by the dispatched worktrunk-expert
  (read-only; no refs/worktrees/commits created). Established: land by rebase-ff (not
  cherry-pick), the native `⊂`/`--merged` oracle, the squash-ruling-is-orthogonal finding, the
  provenance-against-trunk-SHA requirement, and the QA-verdict pre-merge gate.
- **Phase B (2026-08-26)** — verified every load-bearing behavior against running `wt 0.74.0`
  in scratch clones (both arms). Corrected the land mechanism to `--no-commit --no-squash`,
  found the teardown-strands-publication flaw (`--no-remove` + teardown-last), found `flock`
  absent on macOS, and found F1 (project `[merge]` keys inert).
- **2026-09-02** — verified there is NO `.git/wt/trash/` retention and no `refs/wt-backup/*`
  after removes (F2); added the `dirty-worktrees` audit pass.
- **`7cdfac0`** — origin-ref-leak fix: teardown deletes the origin ref LAST, guarded on
  `origin/main == NEW_MAIN`; `branch_hygiene.py` orphan-origin sweep. Prevented a measured
  ~51-ref pileup (the same manual `git push origin --delete` workaround had been needed twice
  in one session).
- **2026-09-08** — the `.qa/verdict.json` regression + foreign-verdict "green that lies"
  incident and its fix (`1651603` → `d8f72dc` re-untrack → `56214a0` Arm 0.5 guard). §5.
- **2026-09-10** — this "brought current" pass: collapsed the phase log, folded in the above,
  corrected C1–C6, split the universal model out to the portable doc.

## 8.2 The completed cutover (was "DELIVERABLE 2 — migration plan")

The 2026-08-26 doc planned a multi-phase cutover from 48 worktrees / 117 branches / ~200 refs
(47 landed-unreaped and climbing) with `wt` not yet installed and no project config. That
cutover has **completed**: `wt 0.74.0` drives lands, `.config/wt.toml` is canonical, the
heuristic oracle is retired for the native one, and the tree is at 1/1. The step-by-step
migration path is retained in git history and is no longer operative guidance.

## 8.3 What remains unverified locally (re-run in scratch on any wt version change)

1. **Does `wt`'s internal commit honor `core.hooksPath` git hooks, or bypass them (libgit2 /
   `--no-verify`)?** Load-bearing for the corpus gate; mitigated by also wiring the suite into
   `[[pre-merge]]`, but confirm directly. (Under `--no-commit`/`--no-squash` the internal
   commit step is skipped, so today's land does not exercise it — this matters if the land
   model ever changes.)
2. **Behavior on `wt` versions newer than 0.74.0** — the `Unreleased` CHANGELOG is non-empty
   and upstream pushes frequently. Any of F1–F6 (especially F1) could change silently. Pin the
   version; re-read the live docs; re-run the both-arm scratch checks at any bump.
3. **Provenance mapping fidelity** when a branch had multiple commits and one is dropped in
   rebase — confirm `OLD_MAIN..NEW_MAIN` yields exactly the intended landed set.

---

**See also:** [`WORKTRUNK-GIT-HYGIENE-SUCCESS-MODEL.md`](./WORKTRUNK-GIT-HYGIENE-SUCCESS-MODEL.md)
— the portable, cross-repo methodology (universal invariants, rationale, failure stories) this
composeio implementation is the reference for.
