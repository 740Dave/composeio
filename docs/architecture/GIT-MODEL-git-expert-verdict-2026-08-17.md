# git-expert — independent verdict on the git model (2026-08-17)

> ⛔ **RECOVERED FROM THE TRANSCRIPT ON 2026-08-18 AND LANDED.** This report was returned by
> `git-expert` at 18:49 on 2026-08-17, relayed to David in conversation, and **written to no file**.
> Zero of its seven mechanisms were executed, and the reason is visible in that sentence: there was
> nothing on disk to ratify, schedule, or point an agent at. Recovered verbatim from
> `89b2c3c0-a75d-4634-9808-a18904da4eb4.jsonl` line 11798.
>
> ⚠ **THE NUMBERS ARE AS-MEASURED 2026-08-17 AND DECAY.** `N_landed_unreaped = 45`, 77 local
> branches, 40 worktrees, `.git` = 1.28 GiB. The report's own falsifier requires re-measurement
> against a date; treat every figure here as needing a fresh pass before action.
>
> ⛔ **ONE CONSTRAINT POST-DATES THIS REPORT.** §4's "fast-forward local `main` to `origin/main`"
> and the checkout-restore item must now also PRESERVE `3a8ac91` (the DSD context guard, David's
> ruling), which lives on `defect/font-family-fork-commp` and **not** on main. The live checkout is
> that branch, so the guard is running today; a naive restore to `main` deletes a guard David ruled
> for. The landing must carry it.
>
> ⚠ **AND ONE FIGURE IN §1 IS NOW KNOWN WRONG IN THE OTHER DIRECTION.** The report repeats the
> architect's claim that zero commits exist only on this disk. The independent reviewer measured
> `5df105a` — the tip of local `main` — on **no remote**. That matters here because M5 and §4's
> "fast-forward local `main`" would destroy it. Confirm before executing either.
>
> **Provenance:** returned by the `git-expert` subagent, dispatched with David's high-level
> commentary and deliberately WITHOUT the architect's own proposal, so the opinion would be clean.


I changed nothing in the repo. One disclosure: `git merge-tree --write-tree` (the ruled verdict instrument) writes unreferenced tree objects into the object store — inert and gc-able, but it is a write, and I would rather name it than have you find it.

---

# Should this project keep using git?

**Recommendation: KEEP GIT. Do not drop it.** But the current model is not "git discipline that keeps slipping" — it is **two ratified rulings in direct structural contradiction**, and no amount of discipline resolves a contradiction. Fix the contradiction and the recurrence stops.

I'll show the evidence first, because the recommendation depends entirely on one measurement that overturns the framing.

---

## 1. Diagnosis — with evidence

### David's hunch is FALSIFIED. Consolidation is not late; it is fast.

I reconstructed landing latency by patch-id: for every branch commit that has an equivalent on `origin/main`, the time from the branch commit to its upstream twin. n=85 observations.

| statistic | value |
|---|---|
| median | **79 minutes** |
| p25 | 18 min |
| p75 | 15.0 h |
| p90 | 17.0 h |
| under 1h | 36 / 85 (42%) |
| under 6h | 60 / 85 (71%) |
| under 24h | 81 / 85 (95%) |
| over 3 days | 2 / 85 |

**MEASURED.** Work reaches `main` at a median of 79 minutes. The hunch — "branches created appropriately but consolidation isn't happening until well after the fact" — is wrong. Consolidation happens promptly and it happens nearly always.

### What actually never happens is DELETION.

| | count |
|---|---|
| local branches | 77 |
| **provably content-landed, still existing** | **45 (59%)** |
| holding genuinely unique content | 31 |

45 branches have already landed and were simply never deleted. That is the entire visible mess. It is not a landing-latency problem; it is a **destruction** problem.

Deletion *does* happen — 38 branch names appear in the reflogs and no longer exist as refs. But it happens in **campaigns**, when a human notices. Creation is automatic and continuous; destruction is manual and episodic. That asymmetry is the generator, and it regenerates the mess in days because creation never pauses.

### The root cause is a contradiction between two ratified rulings.

- `[PRINCIPLE-WORKTREE-ISOLATION]` — *"Every repo-mutating dispatch runs in an isolated worktree, **ALWAYS**."* Every worktree mints a branch.
- `[DEC-COMPOSEIO-IS-TRUNK-BASED-EVERYTHING-LANDS-ON-MAIN]` — *"a branch is a **liability** here... a place where work is INVISIBLE WHILE LOOKING LANDED."*

One policy mints a branch per dispatch. The other declares branches liabilities. **Neither owns the destruction.** Both are correct in isolation; together they are a leak with no drain. This is why it recurs within days regardless of how carefully anyone follows either ruling — following both *is* the leak.

### The second cost driver: the classification instruments are one-sided, so every cleanup is a per-branch adjudication.

Running the ruled verdict instrument (`git merge-tree --write-tree`, per `[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST]`) against `origin/main` on all 76 branches, cross-tabbed with `git cherry`:

| merge-tree verdict | cherry says 0 unique | cherry says >0 unique |
|---|---|---|
| LANDED (tree ≡ main) | **23** | 1 |
| CONFLICT | **22** | 22 |
| UNLANDED (clean, differs) | 0 | 8 |

**44 of 76 branches return CONFLICT. Exactly half of them (22) are provably content-landed** — `git cherry` marks every one of their commits `-` (already upstream), which the strategy node establishes as sound in the landed direction.

This is the tax, and it is the thing that costs days: a cleanup pass must hand-adjudicate 44 branches to discover that 22 need no adjudication at all. And the answer decays — the strategy node already recorded a prior report that was *"TRUE WHEN MADE and false when read."*

Your briefing's own figures show the same trap: **"24 fully on origin/main, 52 still hold unique bytes."** The 24 is the merge-tree LANDED count. The true content-landed count is **45**. The briefing undercounts safe deletes by 21 because CONFLICT was read as "holds work." That is `[CXC-VISION-EPISTEMICS]` firing on the cleanup process itself — an instrument that fails toward the expensive answer.

### Is the unlanded work valuable? Mostly NO — measured, not assumed.

For each of the 31 branches with unique content, I measured how many of its changed files `origin/main` has *also* changed since the fork point:

- **feat/device-announcement**: 120 files, 115 also changed on main
- **perf/hosyond-dirty-rows**: 85 files, 85 also changed
- **feat/esp32c3-can-node**: 32 files, 32 also changed
- **feature/singleton-hardening** (oldest, 25 days): 18 files, 18 also changed

I spot-checked the oldest. Its single unique commit is `defer(singleton): hold launcher.py hardening for architect review` — and its distinctive tokens (`_acquire_singleton`, `_singleton_lock_path`) are present on `origin/main` today. **The work landed; the branch holds a deferral note.**

Netting it out: **~6 branches hold genuinely distinct, non-superseded content, and every one was created in the last 3 days** — `feat/r503-s3-tm1637-panel`, `spec/73-bus-storage`, `fix/hookup-legibility-gate`, `defect/font-family-fork-commp`, `docs/session-2-record`, `defect/bus-storage-containment`. Two more (`defect/bus-storage-path-containment` and `rescue/console-surface-singleton-2026-08-15`) are **byte-identical duplicates of each other** — same tree OID. So is `defect/console-surface-singleton` / `fix/hookup-crossing-gate`.

**The "52 branches hold unlanded unique bytes" figure does not mean 52 units of value at risk. It means roughly 6, all fresh.**

### The "do not prune, worktrees hold uncommitted work" rule is true but misleading.

17 of 40 worktrees are dirty. I read the actual files. Every one is generated noise: `python/tests/render/captures/*.png` (which the pre-commit hook re-dirties **by construction**), `cyd/build_fingerprints.json`, `.cache/`, `compile_commands.json`, `sdkconfig.*`. **Zero source work is at risk in any worktree.** "Dirty worktree" is another instrument that can essentially only report one answer, and it has been inflating the perceived cost of cleanup.

### Two things nobody is currently watching

**The checkout is parked off main right now.** `HEAD` → `defect/font-family-fork-commp` @ `816f326`, not `main`. That is `[INSIGHT-REF-ONLY-FF-STRANDS-THE-CHECKOUT]` live at this moment.

**`main` vs `origin/main` is not actually diverged.** It reads 2-ahead/20-behind, but `git cherry` marks both "ahead" commits `-`, and their patch-ids match `origin/main` commits exactly (`3584efad7d36`, `32f370ef3233`). Local `main` holds **zero unique content**. The divergence is pure ref-position noise from landing by cherry-pick without moving the local ref.

### 28 of 29 `/private/tmp` worktrees bypassed the sanctioned mechanism.

`~/.claude/hooks/create-worktree.sh` fired **33 times** total and creates into `~/.cache/claude-worktrees` (6 registered there). But 28 worktrees sit in **session scratchpad directories** (`/private/tmp/claude-501/.../scratchpad/wt-*`) — hand-rolled `git worktree add -b` by agents, into storage that is ephemeral by design. They are invisible to the hook log, and their branches are the only durable record of them.

### Dropbox risk: real, but not yet realized — and it is not your backup story anymore.

- `.git` is **1.28 GiB** and **is being synced** (`com.dropbox.attrs` present; no `com.dropbox.ignored`).
- `git fsck --connectivity-only`: **clean, exit 0**.
- Dropbox "conflicted copy" files in repo or `.git`: **0**.

No damage to date. But 1.28 GiB of packfiles syncing while up to 40 worktrees write concurrently is a live corruption vector, and the near-miss precedent exists in your own corpus (`core.bare = true` appearing spontaneously, `[INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT]` instance 6).

---

## 2. The recommendation, unhedged

**Keep git.** I want to be clear this is not the reflexive answer — the briefing correctly warned that an analysis which only ever concludes "keep the tool" is not an analysis. Here is why dropping it is the wrong call, and I would say the opposite if the numbers supported it.

**The measured problem is not git.** It is 45 dead refs that nothing deletes. That is a scheduled job, not an SCM migration.

Four reasons dropping git makes this project measurably worse:

1. **Git is this project's primary falsifier for code claims, and the corpus proves it.** The `perf/hosyond-dirty-rows` case is the one to weigh: an architect asserted "16 commits absent from main, pruning destroys real work." It was **false** — by content the branch was empty, and landing it would have **reverted a ratified 180° panel rotation on live hardware**. Only `merge-tree` and patch-id analysis could establish that. This entire report is a git-history investigation that overturned the framing it was handed. `[CXC-VISION-EPISTEMICS]` is the project's central doctrine; git is one of the few instruments you own that is *sound in a stated direction*. Dropbox version history gives you file revisions. It cannot answer "is this content already present under a different SHA," which is the question that keeps mattering.

2. **The pre-commit gate is a git hook, and it caught a broken tree today.** Drop git, drop the only mechanism that stops agents committing broken code.

3. **Dropbox is strictly worse for *this* workload.** With N agents writing concurrently, Dropbox has no atomic commit, no conflict detection, no provenance — it produces "conflicted copy" files. That is silent-plausible-wrong, the exact failure class this project spends its life fighting. You would be trading a loud, auditable mess for a quiet, unauditable one.

4. **`[DEC-COMPOSEPLATFORM-MIT-OSS]`.** Publication requires git, and history cannot be reconstructed after the fact.

**Counter-argument I take seriously and reject:** "the coordination cost is days." Yes — but the measured cost is the *adjudication* of 44 CONFLICT verdicts, 22 of which needed none, plus manual campaign-mode cleanup. Both are automatable with proofs the project has already ratified. You have been paying a manual price for a mechanical problem.

---

## 3. The model — mechanisms, not resolutions

The design constraint: **anything requiring an agent to remember something has already failed here.** Every mechanism below is a hook or a scheduled job.

**M1 — The branch is owned by the worktree; removing the worktree deletes the branch.**
You already have a registered `SessionEnd` hook (`~/.claude/scripts/session_end.sh`). Give `create-worktree.sh` its counterpart: at session end, for each worktree under `~/.cache/claude-worktrees` — if the branch is content-landed (tree-test ≡ `origin/main` **OR** all commits `-` under `git cherry`) and the tree has no untracked/modified files *outside the known-generated set* — remove the worktree and delete the branch with `-d`.
→ *Structurally impossible: a landed branch outliving its session.*

**M2 — One sanctioned worktree path, enforced.**
Hand-rolled `git worktree add` into session scratchpads is banned; the hook is the only path. The dusk audit asserts every registered worktree lives under `~/.cache/claude-worktrees` and flags any that does not.
→ *Structurally impossible: a worktree whose only record lives in ephemeral storage.*

**M3 — Reap on a timer, not on notice.**
A daily (or SessionStart) reaper applying the two landed-direction-sound tests. This is not a judgement call — the tree test is a *proof*, already ratified as the verdict instrument. Today it reaps **45 branches**, including the 22 hiding behind a CONFLICT verdict.
→ *Structurally impossible: cleanup as a campaign that requires David.*

**M4 — The reaper carries a negative control. This is non-negotiable.**
Per `[CXC-VISION-EPISTEMICS]` `rule-negative-control`: a reaper reporting "0 to reap" is indistinguishable from a reaper that is broken. Every run must log `examined=N reaped=R refused=F` and **name the refused branches**. If `refused == 0` on a run where unlanded branches exist, the reaper is faulty and must say so. Without this you have built another drop counter pinned at zero.

**M5 — `main` is a mirror, never a place.**
Dusk audit asserts `main == origin/main` and `HEAD == main`; loud failure otherwise. Landings restore the checkout. Catches today's stranded-checkout state automatically.
→ *Structurally impossible: the next worktree branching from a stale local `main`.*

**M6 — A branch budget, surfaced at wake.**
Hard cap of 10 live branches. Reaper enforces; if it cannot reach budget, that is a wake-visible finding for the architect. Drift becomes something you learn at dawn, not discover at dusk.

**M7 — Exclude `.git` from Dropbox sync.** `xattr -w com.dropbox.ignored 1 .git`. `origin` on GitHub is now your offsite copy, which is the better backup anyway. Stops 1.28 GiB of continuous sync churn against a live object store. **This changes your backup posture and is your call, not mine** — flagging it as a decision, not doing it.

**Adoption cost:** M1/M3/M4 are one script plus one hook registration. M2/M5/M6 are three assertions added to the dusk audit I already run. The ongoing cost is near zero; the ongoing *saving* is the entire adjudication tax.

---

## 4. One-time cost to reach a healthy state

**Does NOT need David — roughly 2–3 hours of agent time:**
- Write the reaper + negative control; wire the SessionEnd counterpart (~90 min)
- Add M2/M5/M6 assertions to the dusk audit (~30 min)
- Execute one reaping pass: delete 45 provably-landed branches, remove their worktrees, prune the 28 scratchpad worktrees after confirming their dirt is generated-only (already confirmed) (~30 min)
- Fast-forward local `main` to `origin/main` (zero content at risk — proven above)

**Needs David — I estimate 30–45 minutes, three items:**

1. **Adjudicate ~6 branches** (one line each: keep or drop). Only these hold genuinely distinct content: `feat/r503-s3-tm1637-panel`, `spec/73-bus-storage`, `fix/hookup-legibility-gate`, `defect/font-family-fork-commp`, `docs/session-2-record`, `defect/bus-storage-containment`. Not 52. Six.
2. **Grant the reaper standing authority** to delete branches on the tree-test proof without per-branch approval. Without this the reaper is just another campaign that needs you.
3. **Decide M7** (`.git` out of Dropbox sync; backup posture moves to `origin`).

**The one genuinely expensive item, and it is yours to schedule:** the live checkout is parked on `defect/font-family-fork-commp`, and `[DEC-COMPOSEIO-IS-TRUNK-BASED...]` records ~11 long-running processes rooted in that tree (oldest PID 71731 since 28 July, driving the dual-throttle controller) reading scene YAML **at runtime**. Restoring `HEAD` to `main` rewrites files under live hardware. That is a coordinated cutover you time, not a checkout I run.

---

## 5. The falsifier

**Primary metric — `N_landed_unreaped`:** count of local branches that are provably content-landed (tree-test ≡ `origin/main` **OR** all commits `-` under `git cherry`) and still exist. Computed in one pass; I will report it at every wake.

| | value |
|---|---|
| Today | **45** |
| Target, steady state | **0** (tolerance ≤ 3, for branches landed within the hour) |
| **FAILURE THRESHOLD** | **> 10 at any wake, 7 days after adoption** |

**Secondary — total local branch count.** Today 77. Must be **< 15** within 7 days and stay there. **> 25 at day 7 = failed.**

**Health check on the instrument itself:** the reaper's `refused` list must be non-empty whenever unlanded branches exist. A week of `refused=0` is grounds to distrust the number, not to celebrate it.

**If either threshold is breached at day 7, the model failed and I would support your cron proposal** — with the caveat that dropping git also drops the pre-commit gate and the OSS path, so the honest fallback is not "Dropbox + cron" but "Dropbox + cron + rebuild those two mechanisms elsewhere," and that cost should be priced before the switch rather than after.

---

## 6. What I could not determine

- **MEASURED** file-overlap and sampled 4 branches semantically; I did **not** semantically adjudicate all 31. My "~6 genuinely stranded" figure is **INFERRED** from overlap plus 4 spot-checks. It should be confirmed before deletion — though every one of the 45 I propose reaping is proven by two independent landed-direction-sound tests, so that set is safe regardless.
- **The actual hours spent on git wrangling.** I have no timing instrument, only your testimony. I can measure the *mechanism* that generates the work, not the wall clock it consumed.
- **Whether the reaper's clean-tree predicate is safe in general.** I verified today's 40 worktrees hold only generated dirt. I cannot prove a future worktree won't hold real uncommitted work — hence M4's negative control and the `refused` list, which exist precisely to make that failure loud rather than silent.
- **Whether Dropbox has ever silently corrupted an object.** `fsck` is clean *now*. A repaired-by-luck past corruption would leave no trace I can read.

**Relevant paths:** `~/.claude/hooks/create-worktree.sh` · `~/.claude/settings.json` · `~/.claude/scripts/session_end.sh` · `${COMPOSEIO_HOME}/.githooks/pre-commit` · `${COMPOSEIO_HOME}/corpora/intelligence-console/strategies.lts` (`[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST]`, line 135) · `${COMPOSEIO_HOME}/corpora/intelligence-console/decision-graph-composeux-architect.lts` (line 2158) · `${COMPOSEIO_HOME}/corpora/intelligence-console/principles.lts` (line 269)