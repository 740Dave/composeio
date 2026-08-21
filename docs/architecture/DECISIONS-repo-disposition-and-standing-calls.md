<!-- dsd-status: answered 2026-08-14T17:42:23Z -->
# Decisions — repo disposition, content calls, and standing deferrals

> Consolidates every item currently parked on David from the 2026-08-13/14 session: git-expert's tree
> cleanup and push report, the console-worktree standup, the spec/01 gate, and long-standing entries from
> `docs/PLAN-fleet-baseline-and-bus-native.md` that were never ruled.
> Format per decision: **What this is** → **Why it matters** → **Options** → **My recommendation** →
> a fillable **Your call** slot. Answer in place, or reply on the channel. "Recommendations all accepted"
> is a valid answer.
>
> **Checked against the committed decision log first** — none of these are already settled.
>
> ⛔ **`runtime.py` is NOT in this document.** It was on the waiting-on-you list an hour ago; a hunk-level
> read resolved it to "purely stale, take main wholesale, no judgment required." It is recorded here only
> so its absence is deliberate rather than an omission.

## How to use this

These are **lower stakes than the wire-format DSD** — most are disposition and hygiene, none block the
build sequence. Recommendations are my honest lean; overrule freely. **⛔ marks the two that decay if
left** — one is losing recoverability, the other is accumulating cross-agent conflict. Everything else
keeps indefinitely.

Nothing here needs deep context to answer. If you want to spend three minutes and no more, answer D1
and D2 and reply "rest accepted."

---

## Theme A — Repo disposition

### D1 ⛔ · `perf/hosyond-dirty-rows` — what happens to its 8 commits?

**What this is.** The branch your live checkout still sits on. git-expert cherry-picked the 7 commits of
*new work* onto main and pushed; what remains on `perf` are 8 **pre-existing** commits of session-handover
bookkeeping.

**Why it matters.** It decays: `session_handover/` is now gitignored on main by your own ruling, so those
8 commits are largely mooted — but the branch is also the base your live checkout is pinned to, and every
day it drifts further behind (28 behind at cleanup time, more now). Left alone it becomes an
ever-more-expensive thing to reconcile for ever-less content.

| Option | Tradeoffs |
|---|---|
| **Delete the branch after confirming the 8 commits carry nothing but handover bookkeeping** | Removes a stale base and ends the drift. **Cost: one verification pass first — and the live checkout must move to a detached main or a worktree, which touches the 18 running processes** |
| Merge the 8 into main | Keeps everything. Cost: re-introduces `session_handover/` content you already ruled out of git, and the merge is 28-commits-behind messy |
| Leave it | Free today. The live checkout stays pinned to a base that only gets staler |

**My recommendation:** **verify then delete** — but *after* the running demos have moved to the deployment
checkout, not before. The branch isn't the problem; the pinning is. Deleting while 18 processes still root
there just moves the pin somewhere else.

**Your call:** Delete the branch after confirming the 8 commits carry nothing but handover bookkeeping

---

### D2 ⛔ · The two stashes — keep, or drop?

**What this is.** `stash@{0}` is the **pre-change golden render captures** for two PNGs currently modified
in the tree. `stash@{1}` is 37 files (+1,813 / −5,184) against `ux_composer/`, a tree **deleted 2026-08-08**.

**Why it matters.** Stashes are reachable until git garbage-collects them, so this quietly decays into
"unrecoverable" without anyone acting. **Both are already exported to patch files outside the repo**, so
dropping them is now reversible — that was the point of exporting.

| Option | Tradeoffs |
|---|---|
| **Keep `stash@{0}`, drop `stash@{1}`** | The golden captures are the before-image for a render change still in flight — exactly what you'd want if a visual regression turns up. `stash@{1}` targets a tree that no longer exists |
| Drop both | Cleanest. Cost: if a render regression appears, the before-image is a patch-file restore rather than a `git stash apply` |
| Keep both | Free, and `stash@{1}` stays confusing forever |

**My recommendation:** **keep `stash@{0}` until the render captures settle, drop `stash@{1}` now.**

**Your call:** Keep stash@{0}, drop stash@{1}

---

### D3 · Four worktrees are clean *and* fully landed — prune them?

**What this is.** Of 13 worktrees, four are clean with zero commits ahead of main: `fleet-console`,
`corpus/improvement-suggestions`, `cyd-spec-lts-conversion`, `fix/mcp-json-skip-and-sdl-dummy`.

**Why it matters.** Only clutter — but 13 worktrees is enough that the *useful* ones (the new
`composeio-main` deployment tree, `wt-primer`, `wt-quiescence`) are hard to pick out.

| Option | Tradeoffs |
|---|---|
| **Prune the four, after confirming no agent is sitting in them** | Reversible — the branches are landed, so a worktree can be recreated in seconds. **Cost: pulling a worktree out from under a running agent is disruptive**, hence the confirmation |
| Leave them | Free; the list stays noisy |

**My recommendation:** **prune, with a liveness check first.** `composeux-architect` is active and I don't
know which trees it holds.

**Your call:** Prune the four, after confirming no agent is sitting in them
**Comments:** No agent is sitting in the worktrees by definition - you and your subagents are the only claude instances running.

---

### D4 · git-expert committed `.claude/agents/composeio-hardware-architect.md` against its own contract

**What this is.** Its standing contract says `.claude/agents/*.md` is not its to stage. It committed this
one anyway — **alone, in its own commit** — and flagged it rather than hiding it.

**Why it matters.** The evidence was decisive: six siblings in that directory are tracked, this one was in
no commit anywhere, and it was moved into the repo *today specifically so it would be versioned*. Its
reasoning was that the guard exists to stop passengers riding along in unrelated commits, not to block
deliberate additions.

| Option | Tradeoffs |
|---|---|
| **Ratify the deviation and amend the contract to say "deliberate, alone, and flagged" is permitted** | Keeps the role def versioned and makes the rule match the judgment a good agent actually applied. Cost: slightly weakens a bright-line guard |
| Ratify this instance, leave the contract alone | The guard stays bright. Cost: the next agent faces the identical dilemma and may guess differently |
| Drop the commit | Restores the bright line. Cost: my role definition — including everything written into it tonight — is version-controlled nowhere again |

**My recommendation:** **ratify and amend.** A guard that a careful agent must violate to do the obviously
right thing is mis-specified, and it announced the violation rather than quietly committing it — which is
the behaviour you'd want to reward.

**Your call:** Ratify the deviation and amend the contract to say "deliberate, alone, and flagged" is permitted

---

## Theme B — Content calls (30 seconds each, but only you can make them)

### D5 · `60-board-hardware-inventory.lts.bak` — a section heading that exists nowhere else

**What this is.** The `.bak` contains `## 5. What Generalizes`, present in **neither main nor the live
file**. Everything else in it is a subset of the live version.

**Why it matters.** Either the section was renamed and the heading is noise, or content was dropped when
`60-` was last edited. The `.bak` is in one commit so it's recoverable either way — but nobody has looked.

| Option | Tradeoffs |
|---|---|
| **You glance at it and say "noise" or "restore that section"** | Definitive, ~30 seconds |
| I diff the surrounding content and infer | I can tell you *whether* prose went missing; I can't tell you whether you meant to remove it |
| Delete the `.bak` unread | Fast; loses the only copy of that heading |

**My recommendation:** **thirty seconds of your eyes.** This is the one item in the document where my
analysis genuinely cannot substitute for yours.

**Your call:** You glance at it and say "noise" or "restore that section"

---

### D6 · `68-bootstrap-primer-families.lts` is on main *and* on another agent's live branch

**What this is.** git-expert landed it; a live `spec/68-bootstrap-primer-families` branch also carries it.
**The blobs are byte-identical**, so there's no fork-divergence and no conflict — but that branch is now a
partial no-op against main.

**Why it matters.** Low stakes now, higher if that agent keeps building on a branch it believes is unlanded.

| Option | Tradeoffs |
|---|---|
| **Tell the owning agent it's landed and have them rebase or drop** | Prevents them building on a false premise. Cost: a message |
| Leave it | Byte-identical today, so nothing breaks — until they add a commit and diverge |

**My recommendation:** **tell them.** It costs one message and prevents the "my branch is unlanded"
assumption from compounding.

**Your call:** Tell the owning agent it's landed and have them rebase or drop
**Comments:** Threre's no other live agent right now, should be checked in/pushed.

---

## Theme C — Fleet and design

### D7 · A second console with its own data layer — is that wanted?

**What this is.** The deployment console at `~/worktrees/composeio-main` runs as a **pure viewer**: live
bus data in, but it does not host the session publishers. That is deliberate — the data layer takes
per-role singleton flocks that **supersede rather than refuse**, so a fully-live second console would
silently *take the data layer away* from your running console, which would not die but would quietly stop
being authoritative.

**Why it matters.** If you ever want two genuinely independent consoles, the seam exists
(`COMPOSEIO_LOCK_DIR`) but it needs a **distinct bus identity** — and the scene hardcodes
`personalization: architect` and `chat_id: architect-hosted` with no CLI override, so two consoles under
one id would both publish to `self/architect/*`.

| Option | Tradeoffs |
|---|---|
| **Leave the deployment console as a pure viewer** | Zero risk to the running console, and it does what you asked for — see the latest UI. Cost: it can't exercise the publisher path |
| Add a scene-level identity override so a second console can be fully live | Enables real side-by-side testing, which the fleet will want. Cost: a genuine identity design decision — what makes two consoles distinct peers rather than two mouths on one identity |

**My recommendation:** **leave it a viewer for now**, and treat the identity override as its own design
conversation when a second live console is actually needed. Naming identity casually is how you get two
peers publishing to one namespace.

**⚠ This one wants a discussion, not a slot** — the real question is what a console's *identity* is,
and a binary here would settle it by accident.

**Your call:** Neither
**Comments:** This is a high-priority workstream that we can pick up later today when your exiting to-do list is completed.

---

### D8 · `[DD-TIER-DEFAULT]` — does `digest` become the lts-engine default?

**What this is.** Long-standing and never ruled. Today `lts_traverse`/`lts_find` default to a token budget
that **truncates silently** — every short result carries `truncated: true` and looks like a coherent,
smaller answer. `tier="digest"` is complete by construction.

**Why it matters.** It bit this session directly: an architect worked from a five-row gloss table for an
hour rather than the actual vision nodes. Until the default changes, **the truncating path is the easy
one** and every caller must remember to check a flag.

| Option | Tradeoffs |
|---|---|
| **Make `digest` the default; require an explicit tier for full bodies** | The easy path becomes the safe one. Cost: every existing caller expecting bodies must ask for them — a real migration across agents and tooling |
| Keep the current default, rely on the `truncated` flag | No migration. Cost: the flag is exactly what a hurried caller skips, which is the measured failure |
| Raise the default budget | Cheap. Cost: moves the cliff without removing it |

**My recommendation:** **make `digest` the default.** Your own framing is the argument — *"it should be
enough to tell you if your query is returning data that you should read"* — and insufficiency only
protects you if it's what you get by default.

**Your call:** Make digest the default; require an explicit tier for full bodies

---

## Theme D — Standing deferrals: confirm they stay deferred

### D9 · The eight substrate ports and six door-4 sites

**What this is.** Two backlogs, each seeded with per-site `DISPOSITION:` lines by the agents that triaged
them. They were **deliberately not completed**.

**Why it matters.** A fresh instance reads a backlog with dispositions and "helpfully" finishes it. This
entry exists so the deferral is a recorded decision rather than an accident that erodes.

| Option | Tradeoffs |
|---|---|
| **Confirm they stay deferred; record it so no successor completes them** | Costs nothing and makes the deferral durable |
| Un-defer some | Fine if you want them — but name which, or the whole backlog reopens |

**My recommendation:** **confirm deferred.** They were triaged by agents that had the context; nothing
since has changed the reasoning.

**Your call:** Confirm they stay deferred; record it so no successor completes them

---

## Summary at a glance

| # | Decision | My recommendation |
|---|---|---|
| **D1** ⛔ | `perf/hosyond-dirty-rows` disposition | **Verify then delete — after the demos move to the deployment checkout** |
| **D2** ⛔ | The two stashes | **Keep `stash@{0}`, drop `stash@{1}`** |
| **D3** | Four landed worktrees | **Prune, after a liveness check** |
| **D4** | git-expert's `.claude/agents` commit | **Ratify and amend the contract** |
| **D5** | `60-…lts.bak`'s orphan heading | **Thirty seconds of your eyes — I can't substitute here** |
| **D6** | `68-…lts` on main and a live branch | **Tell the owning agent it's landed** |
| **D7** | Second console with its own data layer | **Leave it a viewer** *(wants a discussion)* |
| **D8** | `[DD-TIER-DEFAULT]` | **Make `digest` the default** |
| **D9** | Substrate ports / door-4 sites | **Confirm deferred** |

---

## Not asking — already resolved or already doing

- **`runtime.py` and the four other "stale" files** — resolved by analysis, not judgment. Main strictly
  supersedes on every hunk, including keeping `chat_id` as a fallback under the newer `instances:` list.
  Taking main wholesale; `quality.jsonl` unioned, never overwritten.
- **`SO_PRIORITY` + `prio` qdisc, ENOBUFS accounting, gitignoring `build_fingerprints.json`** — doing.
- **Verifying whether the MQTT path negotiates v5** — a fact-check, not a decision; it determines whether
  Message Expiry Interval is available to make retain safe.
- **Registering the retain defect** — a user-visible behaviour that survived weeks because it had a
  comfortable workaround is exactly what the registry exists for.
