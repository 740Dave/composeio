# Cutover evidence — four independent sweeps, 2026-08-17

> ⛔ **THIS IS THE EVIDENCE BASE FOR THE WORKTRUNK CUTOVER CHANGELIST.** Four read-only sweeps were
> dispatched in parallel against separate surfaces. Their findings existed only in the architect's
> context; they are persisted here **before** the `.lts` is authored, so the changelist is checkable
> against primary findings rather than against a summary. That ordering is deliberate — the
> preceding failure in this project was `git-expert`'s analysis living only in a transcript, which is
> why none of its seven mechanisms were ever executed.
>
> **Frame given to every sweep** (first principles, not a keyword list): does the item **DESCRIBE**
> the process · **ENFORCE** it · **RECORD a ruling** on it · **TEACH** it · **MEASURE** it · or
> **DEPEND ON** its artifacts (SHAs, branch names, worktree paths)?
>
> ⚠ Each sweep's stated limits are preserved. Where a sweep declined to claim a hit, that is
> recorded too — a surface with nothing stale is a finding.

## The to-be state every sweep judged against

- **worktrunk (`wt`) owns branch + worktree lifecycle.** `wt merge` lands AND destroys the branch in
  one operation, so creation and destruction become symmetric.
- **Landing is deterministic** — gated on `[[pre-merge]]` hooks (test suite + a persisted QA verdict
  file), not on human approval.
- **`wt merge` squashes by default** — one commit per feature on trunk.
- **Worktrees are addressed by PATH, never branch name** (upstream #3769: `wt remove <branch>` on a
  detached worktree deletes the branch, leaves the worktree, and **exits 0 reporting success**).
- **`.lts` corpora move to `~/.claude/factlattice_corpora/`** — outside every worktree.
- **Commit SHAs stop being minted as provenance** (existing ones are not invalidated).
- **Subagent findings must be persisted to a file** to count.
- **Push on first commit** to private repos; public repos need David's word
  (`[DEC-PUSH-IS-FREE-EXCEPT-TO-A-PUBLIC-REPO]`, 2026-08-17).

---

# MEASUREMENTS — counted, not asserted

| measurement | value | source |
|---|---|---|
| worktrees registered | **41** | corpora sweep, `git worktree list` |
| worktrees carrying a `corpora/` fork | **41 of 41**, 63–90 `.lts` each | corpora sweep |
| worktrees under `/private/tmp` (ephemeral) | **29** | architect, direct |
| worktrees under `~/.cache/claude-worktrees` | **9** ⛔ the enforcement sweep's "zero ever cleaned" is **FALSE** — see the falsification section: 35 hook fires, ~26 cleaned | architect, tested |
| local branches | **77** (one count reported 160 refs incl. remotes) | architect / corpora sweep |
| `N_landed_unreaped` | **47** (was 45 on 08-16 — trending up, zero mechanisms deployed) | architect, recomputed |
| `- branch:` provenance in `defect-graph.lts` | **43 properties, 39 distinct, 33 ALREADY GONE (85%)** | corpora sweep |
| `[COMMIT-*]` SHA references | **28 refs, 26 distinct, 26/26 still on `origin/main`** | corpora sweep |
| `re-derive-via` pointers | **44, of which 44 dangle** — zero `FIXTURE-*` nodes defined anywhere | corpora sweep |
| `.git` size, inside Dropbox | **1.3 GiB**, not Dropbox-ignored | architect |
| trash safety net available? | **yes** — `.git`, `~/.cache`, `/private/tmp` all on `/dev/disk3s5` | architect |

⭐ **The 85% branch rot is the strongest single finding.** `branch:` as defect provenance is already
mostly dead under the *current* model. Worktrunk does not create that problem — it completes it,
deterministically, from 85% to 100%. That is proof `branch:` was never provenance; it is a
success-only instrument, exactly as `commit:` is.

---

# SWEEP 1 — AGENT DEFINITIONS AND SKILLS

*69 agent defs in `~/.claude/agents/`, 8 in-repo (8 are symlinks — counted once), 9 skills,
`~/.claude/commands/`.*

## Tier 1 — would cause an agent to do the WRONG thing

| file | line | quoted | cat | verdict | should say |
|---|---|---|---|---|---|
| `.claude/agents/git-expert.md` | 4–12 frontmatter | "Core capability: land a dispatched isolation:worktree branch onto live main SAFELY — assert base-ancestry, then cherry-pick the branch's OWN commit onto live main … prune the worktree, report." | 1,2 | **CONTRADICTS** | This is the routing text the orchestrator matches on. git-expert becomes **auditor/adjudicator**; landing is `wt merge`. |
| `git-expert.md` | 116–124 | "`git checkout main` … `git cherry-pick -x <commit>`" | 2 | **CONTRADICTS** | Delete the recipe. |
| `git-expert.md` | 127–131 | "`git worktree remove <path>` … `git branch -d <branch>`" | 2 | **CONTRADICTS** | `wt merge` destroys the branch; separate prune is a double-delete. Address by **PATH** (#3769). |
| `git-expert.md` | 106–115 | base-ancestry assert → "Do NOT merge. Land by cherry-pick" | 2,5 | **STALE** | Keep at most as an *audit* check, never the landing instruction. |
| `git-expert.md` | 137–149 | the ref-only fast-forward WIP-preservation section | 1,2,4 | **STALE** | Exists only because landing touched the primary checkout. `wt merge` doesn't. |
| `.claude/skills/dispatch-subagent-build/SKILL.md` | 63–67 | "COMMIT … then STOP; do NOT push and do NOT merge to main (the architect / git-expert disposes by cherry-picking)" | 1,2 | **CONTRADICTS** | Commit **and push**. |
| same | 68–72 | "**Shell-less agents** … land it yourself by applying the edit to main directly" | 1,2 | **CONTRADICTS** | Writes into trunk outside the merge gate. Must land through a branch + `wt merge`. |
| same | 74–75 | "your FINAL TEXT is the deliverable" | 1 | **CONTRADICTS** | The exact loss mode being closed. Require findings PERSISTED to a file on the branch. |
| `.claude/agents/defect-investigator.md` | 119–120 | "`git checkout -b defect/<slug>` off the current branch" | 1,2 | **CONTRADICTS** | `checkout -b` in a shared tree moves HEAD for every co-resident agent. |
| same | 130–134, 155 | "RETURN FOR REVIEW — do NOT push or merge" / "**Branch, never push.**" | 1,3 | **CONTRADICTS** | Push on first commit; the gate is `[[pre-merge]]` + verdict file. |
| same | 8 frontmatter | "committed but NOT pushed — then returns the bounded diff for the architect to concur + push" | 1 | **CONTRADICTS** | Routing text advertising the loss mode as the deliverable shape. |
| `.claude/agents/composeio-hardware-architect.md` | 616–618 | "Commit on a branch, stage explicit paths, **do not push**." | 1,2 | **CONTRADICTS** | Inverts. Shared-HEAD hazard is fixed by `wt`-owned worktrees, not by a pre-commit check. |
| same | 287 | "hygiene (no commit, no push, no `.claude/`)" | 1 | **CONTRADICTS** | Backwards for dispatched builds. |
| same | 242 | dispatch table: "land a branch, cherry-pick … → `git-expert`" | 1 | **CONTRADICTS** | Read at wake. Add a `worktrunk-expert` row. |
| `.claude/agents/composeux-architect.md` | 214 | "→ git-expert — the base-ancestry + cherry-pick branch-landing dance" | 1,3 | **CONTRADICTS** | Pipeline terminal step. |
| same | 275–278 | "a PUSH to origin stays an explicit-go … push on his word" | 1,3 | **CONTRADICTS — stale TODAY** | Already contradicted by `[DEC-PUSH-IS-DELEGATED-TO-THE-SUPERVISING-ARCHITECT]` (2026-08-15) and now by `[DEC-PUSH-IS-FREE-EXCEPT-TO-A-PUBLIC-REPO]`. |
| same | 280–281 | "With a test suite + a not-yet-pushed branch, do the whole change in one pass" | 1 | **NEEDS-REWORD** | Safety now rests on branch isolation + the merge gate. |
| `.claude/agents/composeplatform-qa.md` | 146–149, 152–182 | "Emit the verdict — Deliver a structured report … the headline the architect acts on" | 1,6 | **CONTRADICTS** | ⭐ **The single highest-leverage edit in the entire sweep.** QA already holds `Write` (line 11). Its verdict must be a **file committed on the branch** so `[[pre-merge]]` can read it. Nothing anywhere currently instructs this. |
| same | 234–235 | "pipeline-position: … before architect-accept and git-expert landing" / "next-step: [AGENT-GIT-EXPERT]" | 3 | **STALE** | Machine-readable roster edge asserting the old terminal role. |
| `git-expert.md` | 382–384 | "role: branch-landing (base-ancestry + cherry-pick)…" / "pipeline-position: terminal" | 3 | **CONTRADICTS** | Same. |

## Tier 2 — wrong artifacts, paths, measurements

| file | line | issue | verdict |
|---|---|---|---|
| `git-expert.md` | 78–87 | depends on `~/.cache/claude-worktrees/hook-fires.log` as the creation record | **STALE** if `wt` owns creation — the discriminator silently reports on nothing |
| `git-expert.md` | 93–100 | documents the `WorktreeCreate` hook as the creator | **NEEDS-REWORD** — today `wt` and the hook are two creators of one resource |
| `git-expert.md` | 292–296 | "SEAT `.mcp.json` … it is gitignored, so a worktree can never contain it, and **20 tests** then fail" | **NEEDS-REWORD** — same gitignored-in-a-worktree class as the corpus move |
| `git-expert.md` | 310–312 | "A cherry-pick fires no `pre-commit` hook, so the corpus/property gate does not run on a landing. Run it explicitly" | **STALE** — belongs in `[[pre-merge]]`, run automatically |
| `git-expert.md` | 286–288 | never-commit list naming 4 in-tree `.lts` paths | **STALE** — those files leave the tree; dead text that reads authoritative |
| `git-expert.md` | 163–166 | audit item 1 uses `git branch --no-merged main` | ⛔ **CONTRADICTS** — under squash, a landed branch **never** shows as merged; reports every landed branch forever. And if `wt merge` deletes it, the query cannot see the loss at all (the #38287 shape) |
| `git-expert.md` | 173–176 | audit item 3, orphaned-worktree / stale-base flags | **NEEDS-REWORD** → `wt list` / `wt step prune` |
| `git-expert.md` | 251, 130 | "Delete merged/landed branches with `-d`" | **CONTRADICTS** — `-d` refuses a squash-landed branch |
| `git-expert.md` | 316 | "Do not prune any branch, worktree, or stash unless explicitly told" | **NEEDS-REWORD** — reconcile with `wt step prune` |
| `.claude/skills/add-corpus/SKILL.md` | 22–23, 27–30, 45 | `mkdir -p corpora/<name>`, `git mv … corpora/`, registration path `…/composeio/corpora/<name>` | ⛔ **CONTRADICTS** — this is the skill an agent invokes to add a corpus; it will **re-create the fork problem** |
| `add-corpus/SKILL.md` | 51–52 | cites "the repo's no-push rule" | **NEEDS-REWORD** |
| `defect-investigator.md` | 137–143 | `defect_ops attempt --branch "<your branch>"` | **STALE** — records a branch `wt merge` destroys |
| `defect-investigator.md` | 146–148 | "the architect … runs `resolve --status verified` after landing" | **NEEDS-REWORD** |
| `composeio-hardware-architect.md` | 304–306 | `[PLACEMENT UNRATIFIED]` "in both the commit subject and the spec body" | **NEEDS-REWORD** — branch commit subjects are squashed away |
| `composeio-hardware-architect.md` | 310–315 | "`pre-commit` routes `corpora/*.lts` into the test suite" | **NEEDS-REWORD** — goes inert on the move (see Sweep 4) |
| `composeux-architect.md` | 185 | points at `docs/agent-roster-drafts/BUILD-PROCESS.md` | **pointer to a doc that must also be swept** |
| `composeux-architect.md` | 228–229, 349 | cites `[INSIGHT-REF-ONLY-FF-STRANDS-THE-CHECKOUT]`; "committed vs uncommitted" | **STALE / NEEDS-REWORD** — the axis becomes landed vs unlanded |

## Tier 3 — other-project surfaces, and the carve-outs

| file | line | note |
|---|---|---|
| `~/.claude/agents/other-builder.md` | 182–184 | "Land via `--no-ff`" — **opposite** of `wt merge`'s squash default. Other project, but the second sentence ("a cherry-picked branch … reports 'not merged' forever") generalizes to squash and is worth harvesting |
| `~/.claude/agents/other-architect.md` | 278–280 | "worktree isolation binds the SESSION's repo … a brief that reaches a DIFFERENT repo by ABSOLUTE PATH is UNISOLATED and the isolation flag REPORTS SUCCESS ANYWAY" | **STILL-TRUE**, already reasons in paths — promote into composeio |
| `~/.claude/agents/factlattice-engineer.md` | 164–166 | "VERIFY you are not inside a linked worktree when writing a decision or defect record" | **PARTLY RESOLVED by the corpus move** — correct today, obsolete after. Do not delete before the move |
| `factlattice-gap-analyzer.md` | 51,55,65–68,80,172,201,252,384,418,428 | an entire finding taxonomy keyed on branch-name + commit-SHA (`IMPL_UNLANDED`, "must name the commit it was verified against") | **NEEDS-REWORD** — collides head-on with SHA retirement |
| ⛔ `factlattice-engineer.md:24`, `factlattice-defect-investigator.md:39,123` | | "commit on a branch, **never push**" — **STILL-TRUE and MUST NOT BE SWEPT.** These target the **public** `740Dave/factlattice`. A blanket rewrite would publish on David's behalf |
| `decision-archivist.md` 36,50 · `factlattice-decision-archivist.md` 79,120 | | node templates instructing "record commit SHAs" as provenance | **NEEDS-REWORD** (other project) |

## Clean — stated explicitly

`skills/handover`, `skills/transcript-query`, `skills/create-wiring-diagram`, `skills/create-beepout-sheet`,
`skills/bounce-console` (2 corpus-path refs only), `skills/harvest-signal`, `skills/decision-support-doc`,
`agents/composeio-expert.md`, `agents/composeux-render-engineer.md`, `~/.claude/commands/`.
`smd-*` / `factlattice-*` "branch" hits are **flow-step `branches:` schema keys — false positives.**

## Structural observations

1. **`git-expert.md` needs a rewrite, not edits.** Wake sequence, core dance, WIP section, audit
   items 1 and 3, hygiene block, changelog and roster block are all organized around *"I am the
   lander."* Patching leaves a document whose spine contradicts its patches.
   ⛔ **It is symlinked into `~/.claude/agents/`, so its stale instructions are live in EVERY session
   on this machine.**
2. **Two contradictions exist pre-worktrunk** — the push rules. Fix regardless.
3. **The QA-verdict-as-file change has no home yet** and is the change that makes `[[pre-merge]]`
   gating work at all.

---

# SWEEP 2 — TEACHING SURFACES

*33 dusk-gift files, 3 session_handover guides, RUNBOOK, BUILD-PROCESS, both CLAUDE.md.*

⭐ **Headline: the dusk gifts are far cleaner than expected — exactly ONE of 21 answers has a stale
LESSON.** The damage is concentrated in `BUILD-PROCESS.md` and `BOOTSTRAP-CORPUS-QUERIES.md`.
There is **no** `composeio/CLAUDE.md`; the two that exist are clean.

## Tier 1 — process docs that ENFORCE the old model

| file | line | issue | verdict |
|---|---|---|---|
| `docs/agent-roster-drafts/BUILD-PROCESS.md` | **136–163** | entire §5 "Branch-landing discipline — the worktree base trap": base-ancestry assert, cherry-pick, prune, ref-only-FF | **STALE — delete the section.** The one surviving fact is *why* the trap existed; that is now the tool's problem |
| same | 62–68 | "Commit to your OWN branch as the final act; **never push or merge**" | **CONTRADICTS** |
| same | 169–170 | "Commit + push at milestones … but push is the architect's/human's go" | **CONTRADICTS**, and self-contradicts line 62 |
| same | 33 | "Hand the validated branch to the **git-expert** for the … cherry-pick landing dance" | **STALE** |
| same | 69–70 | "a worktree pins its base to session-start, which changes how its branch must be landed" | **STALE** — replace with the path-addressing rule |
| same | 78 | receipt exemplars include "the SHA it landed" | **NEEDS-REWORD** — keep "the absolute path it wrote" |
| same | 85–89 | "A dispatched agent's MCP write goes to the CALLER'S tree … treat 'filed' as UNVERIFIED" | **NEEDS-REWORD — half is FIXED by the corpus move, half is not.** The `.lts` half dissolves; the general shape survives for every other writer. **Do not delete wholesale** |
| `docs/board-bringup/RUNBOOK.md` | 243–248 | "Commit on a branch. **Do not push.**" + shared-HEAD `git checkout -b` warning | **CONTRADICTS** |
| same | 320 | "do not cross the waist; **do not push**" | **CONTRADICTS** — remove only that clause |

## Tier 2 — the wake-time bootstrap doc

| file | line | issue | verdict |
|---|---|---|---|
| `session_handover/BOOTSTRAP-CORPUS-QUERIES.md` | 122–131 | "git-expert was PROMOTED to Tier 1 … **It makes the one boundary decision that determines whether any other decision survives** — what reaches `main`" | **NEEDS-REWORD — the ruling survives, the mechanism does not.** "It is its call" becomes false; the hooks make the call |
| same | 218 | roster row citing `[INSIGHT-REF-ONLY-FF-STRANDS-THE-CHECKOUT]` | **STALE** — drop that cite; `[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST]` survives |
| same | 27, 167 | `corpora/intelligence-console/…lts` paths | **STALE after the move** |
| same | 111–112 | "a worktree-prune loop reporting 'file absent' for every branch including one that existed" | ⚠ **STILL-TRUE — do not touch.** The lesson is the negative-control rule; the prune loop is incidental |

## Tier 3 — the compact guides

| file | line | issue | verdict |
|---|---|---|---|
| `GUIDE-preparing-for-compact.md` | 52–57 | "**Work that exists in no commit** … *what is on disk that no branch contains?*" | **NEEDS-REWORD on both halves** — `no branch contains` → **is not PUSHED**; and under squash every branch commit is discarded at merge, so the question becomes "did the content survive the squash" |
| same | 65 | "'Each branch merges cleanly' is not 'the sequence merges cleanly'" | **NEEDS-REWORD** — true epistemically; the git instance evaporates |
| same | 142 | "If something must be true … a branch exists, a rescue landed — dispatch an agent to verify it now" | **NEEDS-REWORD** + ⭐ **add the new rule here: the dispatched verifier must PERSIST its findings to a file.** This guide is its natural home and currently does not state it |
| same | 30 | the four-artifact handover structure | ⭐ **STILL-TRUE — and the one surface already compliant.** Cite as the exemplar for "findings must be persisted" |
| `ADDENDUM-harvest-fixtures-before-compact.md` | 110–111 | "work that exists in no commit" | **NEEDS-REWORD** → not pushed. The warning gets *stronger* |
| same | 98–108 | "⛔ COPY THE ARTIFACT NOW … Do not point at it; the pointer will rot" | **STILL-TRUE and reinforced** — same principle, independently derived |

## Tier 4 — the dusk gifts

| file | line | issue | verdict |
|---|---|---|---|
| `registration-protocol.md` | 220, 222 | P6d's worked example: "hand-cherry-picking the first landings until David said *'you have a git-expert agent'*" | **STALE — the only gift whose LESSON dies.** See below |
| `answers/P6d.md` | 8 | the actionable fix: "**route every landing to git-expert**" | **STALE — actively installs a removed habit.** → "route every landing through `wt merge`". The other two triggers survive verbatim |
| `fixtures/ps-composeio-full.txt` | 37 (embedded prompt) | "composeio is TRUNK-BASED … verify the SHA on origin/main" · "Work on a branch." · "git-expert for landings" | ⛔ **STALE BUT DO NOT EDIT THE FIXTURE.** `registration-protocol.md:325` states *"Nothing in it is synthetic and nothing has been elided"* — editing destroys the artifact's evidentiary value, which is the gift. **Annotate in `P14.md`.** ⚠ Sharp irony: P14 §3 explicitly drives the reader onto this exact line |
| `registration-protocol.md` | 45, 64, 308, 210 | `corpora/platform-specs/*.lts` etc. — P1's and P12's byte pointers | **STALE after the move** |
| `registration-protocol.md` | 78, 144 | `bsg/spec/10-dradis-game.lts` | ⛔ **ALREADY BROKEN TODAY** — the file is at `compose_projects/bsg/spec/`. Pre-existing rot in a gift whose one law is "go read the bytes" |
| `README.md` | 96 · `relational-registration.md` 35 | "committed" / "Verify and commit at green checkpoints" | **NEEDS-REWORD** — add "and push". One word each |
| `registration-protocol.md` | 92, 157 · `render-engineer-registration.md` 31 | commits `51ce2d4`, `9a35baf`, `cfa313a`, `5793299`, `2303db4` | ⚠ **STILL-TRUE — all verified to resolve.** History is not rewritten by cutover. The rule is *don't mint new ones* |
| `answers/P14.md` | 108, 136, 224 | `git rev-parse main`, "HEAD was on a defect branch" | ⚠ **STILL-TRUE — explicitly not a hit.** The lesson is subset-mistaken-for-set; git is one row of five |
| `answers/P6c.md` | 8 | "read the git history" as falsifier | **STILL-TRUE** — `wt` changes lifecycle, not history-reading |
| `RUNBOOK.md` | 53–56, 112–185 | flash-image SHA-256; CMake `if()` branches; "landed" = reached `compile_commands.json` | ⚠ **NOT HITS.** A naive grep flags all of them |
| `answers-render-engineer/R2.md` | 14 | "the flash even landed" | ⚠ **NOT A HIT** — landed = flashed |

**Clean, stated plainly:** both CLAUDE.md files · `a-note-across-the-seam.md` · **18 of 21 `answers/`
(P1, P1b, P2, P2b, P3, P4, P4b, P5, P5b, P6, P6b, P7, P8, P9, P10, P11, P12, P13)** · `R1`, `R3` ·
`compact-continuity-study.md` and `compact-summary-received-2026-07-18.md` (dated historical records
— rewriting a record is worse than leaving it dated).

### The one gift whose lesson dies — P6d, and the reasoning

The **abstract** lesson is intact and excellent: *reading guidance at wake installs orientation, not a
reflex; a reflex needs a trigger at the decision point.* What breaks is the **worked example that
makes it teachable** — and by the gift's own argument the example *is* the mechanism. Its entire
concrete payload is "you hand-cherry-picked a landing instead of routing it to git-expert." After
cutover there is no cherry-pick and no landing to delegate, and `answers/P6d.md:8` hands the reader
`route every landing to git-expert` as a habit to install. **That is a gift installing a working
habit we are removing** — the only one.

⭐ **The recursion worth recording:** every stale teaching pointer terminates at `git-expert.md`,
whose description block *is* the cherry-pick dance. **Fixing the teaching surfaces without rewriting
that role def will not remove the habit — it is reinstalled at every wake.** That is P6d's own
lesson pointed at this cleanup.

---

# SWEEP 3 — CORPORA (90 `.lts` files)

⚠ **Sweep limits, stated:** 9 of 13 corpora were STALE in the index (`built_at: null` on all 13), so
every finding is from **disk grep + file read**, cross-checked with `lts_find_in_prose`.
⛔ **`defects/defect-graph.lts` is in NO corpus root** — all 43 defect-branch findings are disk-only
and invisible to `lts_find` / `lts_node` / `lts_traverse`.
⛔ **"Subagent findings persisted to a file" has ZERO representation in any corpus** — that leg of the
cutover has nothing to supersede and nothing protecting it.

## Tier 1 — ACTIVELY-HARMFUL

### 1. `[LEC-CORPUS-SEAT]` — `corpora/platform-specs/44-lts-engine-consolidation.lts:136-142` · ENFORCE
> "an in-process lts-engine consumer MUST obtain its corpus set from … the DEDUPED UNION of (a)
> whatever the deployment config serves … and **(b) the REPO-LOCAL corpora derived from the repo root
> alone**. A bare `config.build_store()` is FORBIDDEN … **Corpus resolution is thus a function of the
> REPO**." · invariant (2): "non-empty given only a valid repo root, with NO `FACTLATTICE_*` set"

⛔ **The highest-damage node in the sweep, because it is MACHINE-ENFORCED** —
`python/composeio/decision_ops/_corpusseat.py` + `tests/test_lts_engine_rail_probe.py::test_in_process_consumers_resolve_corpora_without_deployment_env`,
run with `FACTLATTICE_*` **deleted from the environment**. After the move either the test goes red
(loud, fine) or **the repo-local half returns empty, the config half carries everything, and the test
passes while the invariant it encodes is false** — the node's own recorded failure, reproduced by the
fix for it.

**Amend in the SAME change as the move.** Second source becomes a declared, tracked **corpus-home**
path; invariant (2) restated as *non-empty given only a valid corpus home*.
**Falsifier:** after the move, run that test **and separately assert `len(store.corpus_ids()) >= 13`.**
Green with a count below 13 means the test certifies nothing.

### 2. `[PCB-CORPUS-PLACEMENT]` — `corpora/platform-specs/64-pcb-board-landscape.lts:50-61` · RECORD
> `not-registered-machine-local:` — "the corpus registry is `~/factlattice-deployment/private/corpus-config.json`
> — **GITIGNORED and machine-local**. A registration there is **invisible in any diff, unreviewable by
> the architect, absent on any other machine and absent at open-source release** … **A machine-local
> fix leaves those citations dangling everywhere except one laptop.**"

⛔ **The sharpest recorded argument AGAINST the cutover, and it is a ratified reason, not a
preference.** Its four costs are properties of the *mechanism*, not of `pcb/`. **This does not block
the move — it names what the move must buy back.** If the answer is "we accept the loss," that must
be recorded **in this node**, or the next architect finds a ratified objection and no rebuttal.

### 3. `[DEC-DISPATCHED-BUILDS-COMMIT-TO-BRANCH]` — `decision-graph-composeux-architect.lts:1426-1435`
> "lands via git-expert's base-ancestry cherry-pick … **never by hand-landing** … and **never by
> squash (loses per-defect history + enforcing-test provenance for bisect)**. Push the whole coherent
> set once green (**David's explicit go**)."

⛔ Three collisions in one node. ⭐ **And its premise was already abandoned:** the project's own
ratified landed-ness instrument (`merge-tree --write-tree`) is described as *"insensitive to
provenance, because tree identity is insensitive to provenance."* **SUPERSEDE, do not amend around.**

### 4. `[PRINCIPLE-WORKTREE-ISOLATION]` — `corpora/intelligence-console/principles.lts:269-279`
> "Every repo-mutating dispatch runs in an isolated worktree, and **`git-expert` lands it — assert
> base-ancestry, then cherry-pick the branch's own commit onto live main**"

⛔ **Widest reach of anything here** — `prefix="PRINCIPLE"` is enumerated in full at every wake
(30 PRINCIPLE / 14 STRATEGY / 16 INSIGHT on disk). **Amendment is small:** `principle:` becomes
*"…and `wt merge` lands it."* ⭐ **Everything else on the node is STILL-TRUE and must be preserved** —
`boundary:`, `cost:` (six dimensions), `operating-rule:` (*"never trust a signal produced INSIDE a
worktree ABOUT that worktree"*), `generalization:` (*"every isolation mechanism isolates exactly one
axis"*). `wt` changes who owns lifecycle; it does not make a worktree an isolated environment.

### 5. `[CXC-BOOT-CP-60-DISPATCHED-WRITES-LAND-IN-THEIR-OWN-TREE]` — `68-bootstrap-primer-families.lts:290-296`
(+ `[CXC-BOOT-CORE-70-WORKTREE-PRODUCED-SIGNALS]:106`, `[CXC-PRIMER-CHARTER-DOMAIN]` at `67-…:263`)
> "A decision or defect write issued from inside a worktree **resolves the graph RELATIVE TO THE
> CALLER** … and honestly reports 'filed' about a record no one will ever find."

⛔ **Highest TEACHING blast radius — this is bootstrap primer content injected into cold agents.**
After the move the claim is **false**. A cold agent carrying it will distrust, re-verify, or re-file a
write that is now correct. **Must be edited before the first post-cutover cold spawn.**

### 6. `[FIX-LTS-PHASE2-SILENT-STALE-WINDOW]` — `decision-graph-…:1102` · MEASURE
> `re-derive-via: git worktree add /tmp/chk phase-2-freshness; …`

⛔ **Already rotted, verified: branch `phase-2-freshness` is GONE** from local and origin. The **only**
`re-derive-via` in 90 files that is a git command rather than a `[FIXTURE-*]` pointer, and it is
already unexecutable. **Re-derive from a path and a condition, never a branch.**

## Tier 2 — NEEDS-AMENDMENT

### 7. `[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST]` — `strategies.lts:135-147`
**Split it, do not supersede.** The **verdict half survives**: *"PUSHED IS NOT LANDED"*, *"the fact is
a SHA on origin/main, re-fetched"*, and the `generalisation:` (*"Every landed-ness instrument here is
ONE-SIDED. State which direction yours is sound in"*). The **instrument half dies** — all three named
instruments take a **live branch ref**, and `wt merge` destroys the branch.
⭐ The replacement is a **one-call merge receipt**, which directly closes a gap
`[PRINCIPLE-SELF-PROVING-RECEIPT]` already records: *"git-expert's landings report SHAs the architect
could verify but usually does not."* **The cutover closes a principle-level gap; say so.**

### 8. `[DEC-GAP-ANALYZER-GETS-BASH-AND-RUNS-GIT-ITSELF]` — `decision-graph-…:2226-2237`
⛔ **David personally overruled the architect two days ago**, on a rationale whose two load-bearing
premises are (a) *reading another branch* and (b) *stamping `verified-at-commit` so verification is
re-checkable* — *"without the SHA a verification date cannot tell a reader whether the code has moved
since."* **Both evaporate at cutover.** An open residual on a ruling David took personally; must be
answered, not inherited. **Honest replacement: the TRUNK SHA at verification time** — still a SHA, but
permanently reachable, which is strictly better than the branch-side SHA the ruling assumed.

### 9. `[DEC-LANDING-GATE-MUST-BE-BASELINE-RELATIVE]` — `decision-graph-…:1802-1808` · `proposed`
> "**any pre-existing failure blocks every landing, including the landings that would fix it.** THE
> PRESSURE THAT CREATES IS TO REACH FOR `--no-verify` … MAKE THE GATE BASELINE-RELATIVE: fail only on
> tests that PASS at `<base>` and FAIL at HEAD. **It is NOT built.**" · "a **flaky** test inside a gate
> is strictly worse than a red one … measured 5-fail/1-pass over 6 isolated runs"

⭐ **The single most under-used node for the cutover** — the closest existing thing to a specification
of `[[pre-merge]]`, raised 2026-08-11, still `proposed`. **Ratify it INTO the hook spec.** Under
deterministic landing the escape hatch is not a human choosing `--no-verify`; it is the hook being
disabled wholesale. Pair with `[DEFECT-THE-PRE-COMMIT-TEST-GATE-6E7171]` (`defect-graph.lts:581-589`),
which measured the complementary failure: *"green-by-construction … it never executes the committed code."*

### 10–18 (condensed)
- `[INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT]` `insights_and_warts.lts:129-141` — the corpus move
  **kills instance 5 outright**. Its own `meta-lesson:` says a wart recording its remedy as OPEN
  *"must be updated when the remedy lands."* `generalization:` STILL-TRUE.
- `[INSIGHT-REF-ONLY-FF-STRANDS-THE-CHECKOUT]` `:121-127` — **already partly stale**; keep verbatim
  *"a passing test suite proves the code works, NOT that it was built on the right parent."*
- `[DEC-CHAINED-WORKTREE-DISPATCH-REOPENS-STALE-BASE]` `:1819-1827` — ⭐ **cleanest close in the
  sweep**; its own `invalidating_assumption` is exactly what the cutover falsifies.
- `[DEC-BEEPOUT-IS-ITS-OWN-ENDPOINT-NOT-A-HOOKUP-MODE]` `:2172` — ⛔ **the only node whose lifecycle
  VALUE is a branch name** (`hw/beep-out-sheets`, live). Land or gate it **before** cutover.
- `[DEC-DEPLOYMENT-CHECKOUT-NOT-THE-DEV-TREE]` `:2065-2075` — ⛔ the deployment checkout at
  `~/worktrees/composeio-main` holds ~11–18 long-lived processes. **The ruling must state it is EXEMPT
  from `wt` lifecycle.** Amend `[DEC-COMPOSEIO-IS-TRUNK-BASED]`'s `do-not-prune-rule` count (23 → 41).
- **The 2026-08-06 hook cluster** (`:1559`, `:1584`, `:1594`, `:1603`) — **SUPERSEDED as a set.** The
  hook is USER scope for ALL repos and is **currently broken**
  (`[DEFECT-WORKTREECREATE-HOOK-BRANCHES-FROM-THE-726559]`: *"every hook-created worktree is STALE AT
  CREATION"*). **The cutover fixes this defect by deletion — record that.**
- `[CXC-BUILD-*]` `07-composer-agent-build-plan.lts` — a 9-step ordered batch merge, opposite of
  squash-as-you-go. ⛔ **These six are `body-only`: `lts_find(text="worktree")` returns a confident
  ZERO. If the audit is re-run with `lts_find` alone, the entire file is invisible.**
- `[INSIGHT-WORKTREES-POLLUTE-SKILL-DISCOVERY]` `:97-103` — ⭐ **the ratified node that ARGUES FOR the
  move.** Same failure one namespace over: `.claude/skills/` tracked, replicated by worktrees,
  **108 `SKILL.md` on disk, 18× collision, discovery failing SELECTIVELY.** Substitute `corpora/` and
  41 for 18. **Cite it as precedent; it is the strongest support the move has.**
- `[CXC-FC-STORE-ACCESS]` / `[CXC-FC-ACCEPT-MEMORY]` FC-C19 `61-fleet-console.lts:201-210,439-454` —
  ⭐ **the design pattern to COPY rather than re-derive.** *"exactly ONE store per role … outside every
  worktree and therefore prune-proof … reached by TWO ACCESS PATHS."* **FC-C19 is directly reusable as
  the corpora-move acceptance criterion.**

## The contradiction map — pairs conflicting TODAY

| # | pair | conflict | resolution the cutover must state |
|---|---|---|---|
| **A** | `[PRINCIPLE-WORKTREE-ISOLATION]` ↔ `[DEC-COMPOSEIO-IS-TRUNK-BASED…]` | one mints a branch per dispatch; the other calls branches liabilities. **Neither owns destruction.** Measured: 160 refs, 41 worktrees | `wt` owning creation *and* destruction. The two nodes must **cross-reference each other**, which they do not today |
| **B** | `[DEC-DISPATCHED-BUILDS-COMMIT-TO-BRANCH]` ↔ `[DEC-COMPOSEIO-IS-TRUNK-BASED]` / `[STRATEGY-LAND-IT]` | the squash ban preserves per-commit provenance; the adopted instrument is *"insensitive to provenance."* **The project abandoned the ban's premise 18 days ago and never noticed** | Supersede the ban |
| **C** | `[LEC-CORPUS-SEAT]` + `[PCB-CORPUS-PLACEMENT]` ↔ `[INSIGHT-WORKTREES-POLLUTE-SKILL-DISCOVERY]` + `[CXC-FC-STORE-ACCESS]` | **four ratified nodes, split two-two.** Two: corpus resolution must be a function of the REPO. Two: a replicated tree must live OUTSIDE it. **Both right about different costs; no node reconciles them** | ⭐ Adopt the **FC two-access-path shape** — one out-of-tree home + a **tracked, reviewable registration**. `[PCB-CORPUS-PLACEMENT]` called its two options *"mutually exclusive, which is what forced the choice"*; removing the forcing is the cleanest supersession |
| **D** | `[DEC-PUSH-IS-DELEGATED-TO-THE-SUPERVISING-ARCHITECT]` ↔ `[DEC-DISPATCHED-BUILDS-COMMIT-TO-BRANCH]` | "push small and often" vs "push once green on David's explicit go" | Both now superseded by `[DEC-PUSH-IS-FREE-EXCEPT-TO-A-PUBLIC-REPO]`. Make the supersession **explicit on the older nodes** |
| **E** | `[STRATEGY-LAND-IT]` ↔ `wt merge` | the instrument takes a **branch ref**; `wt merge` destroys the branch at the moment it would be used | Replace with the merge receipt; keep the `generalisation:` |

## Provenance that rots

- **`[COMMIT-*]`: 28 refs, 26 distinct, 26/26 currently ON `origin/main`.** ⚠ **Do NOT strip these** —
  the rot is forward-looking. `[COMMIT-*]` is **defined as a node nowhere**, so all 28 already mint no edge.
- ⛔ **Two SHAs in prose are NOT on main:** `1c7b4d2` (`70-hookup-review.lts:129` — a **pinned negative
  control**, *"recorded verbatim as routed on `1c7b4d2`"*) and `d022482` (`73-bus-storage.lts:64`).
  **Both die on squash. `1c7b4d2` is the serious one — it is a control whose stated purpose is that a
  later fix could silently disarm it. Pin it by CONTENT HASH before cutover.**
- ⛔ **`- branch:` in `defect-graph.lts`: 43 properties, 39 distinct, 33 GONE (85%).** Survivors: 6.
  Two already degenerate: `- branch: main`, and `- branch: HELD-no-branch:/private/tmp/…patch`
  (`:879`) — an author already hit this wall and wrote a tmp path into the field.
  **Amendment: replace with `fixed-at:` carrying a trunk SHA plus the test that pins the fix** — which
  the corpus already prefers elsewhere (`[DEFECT-FLOAT32-…-5AA80B-A1]:1082` cites a test, not a branch).
- Other branch provenance: `[DEC-GRAPH-VIEWER-TRANSITION-SEQUENCING]:1573` (branch **GONE**),
  `[DEC-CTK-PORT-EXPOSED-ENGINE-DIALECT-GAPS]:1657`, `[CXC-RM-PATH]` roadmap `:347`,
  `[DEFECT-SUBAGENT-CHIP-PRUNE]` `43-…:87`.
  ⚠ `spec/bus-protocol-corrections-2026-08-14` is **THE ONE STANDING EXCEPTION** held by a ratified
  gate — **`wt merge` must be able to honour a named gate, or the tool breaks that ruling.**

## Dangling pointers

⛔ **All 44 `re-derive-via` are ALREADY dangling — pre-cutover.** A grep for a defined `FIXTURE-*`
node across every `.lts` and `.md` returns **ZERO**. `lts_lint` independently reports
`unminted.unminted_reference_list: 32` in `intelligence-console`, 26 of them `re-derive-via`, because
it is **not a minting key**. **The entire re-derivation mechanism is decorative** — invisible to
`lts_find`, invisible to `lts_traverse`, unresolvable by hand.

✅ **`corpora/deliverables/deliverable-ledger.lts` is cutover-tolerant** — 197 `path:` entries, **zero**
pointing into `corpora/` or `defects/`, keyed on repo-relative path + sha256, roots supplied by config.

---

# SWEEP 4 — ENFORCEMENT SURFACES

## ⛔ GATES THAT GO SILENTLY INERT — ranked

**1. `corpora/*.lts` → `pytest tests` (`scripts/pre-commit-tests.sh:295-297`).** The answer to the
highest-value question: **yes, fully inert, silently.**
```
while IFS= read -r f; do        # f from `git diff --cached --name-only`
  case "$f" in
    corpora/*.lts) run_toplevel=1 ;;
```
After the move no `.lts` can appear in a diff at all. `run_toplevel` stays `0`; line 338 prints
*"no python changes → pass (skipping tests)."* **529 tests stop being selected**, including
`test_lts_authored_edges_survive_parse.py` (the authored-triple census, `[CONV-RELVERB-NO-SILENT-DROP]`),
`test_decision_ops_corpus_path_guard.py`, `test_decision_ops_corpus_builds_nonempty.py`,
`test_decision_ops_boots_under_bad_corpus.py`, `test_lts_property_gate.py`, `test_lts_engine_rail_probe.py`.
⭐ **The comment at 287–294 says this arm exists precisely because an `.lts`-only landing once routed
to NOTHING and printed a green pass. The cutover restores that exact defect.**

**2. `test_no_silent_drop_in_repo_corpora` (`tests/test_lts_authored_edges_survive_parse.py:176-186`).**
`root = _REPO / "corpora/platform-specs"` → `if not root.is_dir(): pytest.skip(...)`.
**Double inertness: unreachable AND self-skipping.** Its docstring records it once caught
**79 of 299 triples dropped (26%)**. Turn the missing-dir skip into a **fail**.

**3. `~/.claude/hooks/dispatch-gate.sh` (lines 24, 35).** Compares `git rev-parse --show-toplevel`
against a hardcoded `$COMPOSEIO`. **Already inert in every worktree today**; under `wt`, inert 100% of
the time — so the Fable-tiering / role-profile gate never fires, and a dispatch with no tier check is
indistinguishable from a gated one. ⚠ Its own comment at line 34 claims it handles this; **the code
does not.** Compare `--git-common-dir` instead.

**4. `RUNTIME_WRITTEN` (`.githooks/pre-commit:64`).** 3 of 7 protected paths evaporate; regex still
compiles, no warning that the protect list shrank.

**5. ⛔ `core.hooksPath` is `.git/config`-local and UNVERSIONED.** `git config --show-origin` →
`file:.git/config`. `.git/hooks/` holds only samples. **A fresh clone silently has ZERO gates.**
Everything above is downstream of one key nothing in the repo sets.

**6. Gitignored content destroyed by `wt merge`.** `.mcp.json` (`.gitignore:64`) and
`session_handover/` (`.gitignore:90`, 8 entries incl. `BOOTSTRAP-CORPUS-QUERIES.md`). Invisible to
`git status --porcelain`, unstaged by `--stage=all`, **excluded from `refs/wt-backup/`**.
`python/tests/conftest.py:1-37` documents the consequence: *"fresh worktree, no .mcp.json: 20 failed,
3367 passed"* → converted to SKIPs. Under `wt` **every** worktree is fresh, so those 20 skip always.

## The full table

| file | line | now | after | verdict | change |
|---|---|---|---|---|---|
| `scripts/lts_property_gate.py` | 375–378 | `_changed_lts()` is **extension-keyed**, not `corpora/`-keyed | prints `no .lts in the staged set -> pass` | **INERT — but LOUDLY** (prints its own vacuity) | run as a corpus-write hook or in `[[pre-merge]]` over `--files $(find ~/.claude/factlattice_corpora -name '*.lts')` |
| `.githooks/pre-commit` | 13 | `REPO_ROOT="$(git rev-parse --show-toplevel)"` (the 6E7171 fix) | correct under `wt` | **STILL-WORKS, fragile** | see inert #5 |
| `python/composeio/bootstrap_paths.py` | 53–90 | `CORPORA_DIR = REPO_ROOT / "corpora"` + 5 more; **18 modules import this** | all point at nothing | **BREAKS (loud)** | ⭐ **the designed one-line seam.** ⚠ `rel_dir()`/`rel()` call `.relative_to(REPO_ROOT)` and will **raise ValueError** for an out-of-repo corpus |
| `decision_ops/_corpusseat.py` | 77–82 | `cdir = base + "/corpora"` | zero repo-local corpora; falls back to the gitignored overlay this module exists to stop depending on | **BREAKS the fallback** | restate premise as "function of the corpus root" |
| `decision_ops/graph_projection.py` | 394–427 | same partition list | same | **BREAKS** | same seam |
| `~/factlattice-internal-dev/private/corpus-config.json` | all roots | absolute `…/composeio/corpora/…` | 8+ corpora unresolvable | **BREAKS (loud)** | rewrite; outside the repo so it does not move with a worktree — good |
| `.mcp.json` (repo + `~`) | `DECISION_OPS_GRAPH` | absolute path to the decision graph | every `decision_add` fails | **BREAKS (loud)** | repoint; ⚠ **`.mcp.json` is gitignored** |
| `intelligence/launcher/transport.sh` | 28 | `REPO` derived from the script's own location — **the 6E7171 class** | run from a `wt` worktree copy it resolves to that worktree, then writes `.mcp.json` pointing INTO it → **decision writes land on a graph live nowhere, destroyed by `wt merge`** | **CONFLICTS** | derive from `git rev-parse --git-common-dir`'s parent (the pattern `graph_home.py` already implements) |
| same | 72 | `DECISION_GRAPH="${REPO}/corpora/…"` | gone | **BREAKS** | repoint |
| `~/.claude/hooks/create-worktree.sh` | 69, 79–102 | fetches origin, resolves base, `git worktree add -f -b "wt-${name}-${stamp}" ~/.cache/…` | duplicates what `wt` owns; different path AND branch conventions; `wt-*` prefix collides conceptually | **CONFLICTS** | retire, or shell out to `wt` so there is one lifecycle owner |
| `~/.claude/settings.json` | `hooks.WorktreeCreate` | registered; **no `WorktreeRemove`** | **7 worktrees under `~/.cache/`, zero ever cleaned, zero prunable** | **CONFLICTS** | register a remove hook, or delete create and let `wt` own it |
| `~/.claude/scripts/session_end.sh` | 50–51 | `PROJECT_ROOT="$(pwd)"`; reads `.claude/session_canary.json` | per-worktree canary; ⛔ **the canary is untracked and NOT gitignored → `wt merge --stage=all` sweeps it into the squash** | **BREAKS + CONFLICTS** | gitignore it, or key off `--git-common-dir` |
| same | 68–147 | counts engineering-log lines vs `lines_at_start` | meaningless across a `wt merge` | **MEASURES nothing** | anchor to the canonical root |
| `python/tests/render/test_precommit_gate_range.py` | 47 | `REAL_LANDING_RANGE = "32dba14..23c82d4"` hard-coded | line 361 **skips** rather than fails when absent | **degrades to a silent skip** | flag |
| `python/tests/render/test_precommit_gate_reachability.py` | 44–60 | deliberately a filesystem walk, **no git** | ⛔ **FAILING NOW** because `wt-ctl/` is a linked worktree **inside** the repo. Under `wt`, any in-repo worktree re-breaks it | **BREAKS** | prune worktree markers, or **forbid worktrees inside the repo** |
| `tools/smd_to_lts.py` | 22–24, 78–95 | archive = `<dir>-archived-smd`, never deletes | path-agnostic, but the archive now lands **outside git**, losing the versioned backstop the never-delete ruling relied on | **STILL-WORKS, weakened** | archive to an in-repo `archive/` |
| `~/.claude/scripts/pre-push-flow-check` | — | exists, executable | **not referenced anywhere** | **already INERT** | wire it or delete it |

## Q4 — is `origin/main` red? **Less than claimed, and mostly self-inflicted**

| environment | result |
|---|---|
| `pytest tests` (top-level, repo root) | **529 passed, 30 skipped — GREEN** |
| `pytest tests` (python tree, live working tree) | **7 failed**, 3879 passed |
| of those 7 | `test_precommit_gate_range` **all 20 PASS** here · **2** `test_sources_run_blocks` params fail (`took 2.01s` vs a 2.0 s budget) · **5 are caused by `wt-ctl/`** — confirmed, 49 `wt-ctl` mentions in the failure output |
| architect's fresh `origin/main` worktree | 4 failed — 3 timing + 1 gate-range (fresh worktree has **no `.mcp.json`**) |
| **inferred pristine** | **~2**, the join-budget race |

⭐ **`wt-ctl/` also shows as `??` — under `wt merge --stage=all` it would be swept at `git add -A`.**

**Known-good-baseline mechanism: NONE.** No golden list, no `--baseline`, no allowed-failures file.

**Bypasses, all reachable:** `--no-verify` (total) · `SKIP_PRECOMMIT_TESTS=1` · `SKIP_LTS_PROPERTY_GATE=1`
(staged mode only) · `COMPOSEIO_ALLOW_RUNTIME_FILES=1` · ⛔ **git plumbing — not hypothetical:
`defects/defect-graph.lts:945` records TEN commits landed in one night this way, `ade7009` staging four
RUNTIME_WRITTEN files the guard would have refused; recorded conclusion "NOT FIXED"** · and **not setting
`core.hooksPath`**, the null bypass.

⭐ **Under `[[pre-merge]]`, `--no-verify` and the null bypass stop being reachable at the landing
boundary, and plumbing stops mattering because the gate moves off the commit onto the merge. That is a
genuine strengthening — provided the suite is green at cutover.**

## Could not determine
- Whether `WorktreeCreate` registration actually disables Claude Code's exit cleanup (#37611
  unverified) — **but 7 uncleaned worktrees is consistent with it.**
- Whether `wt`'s `[[pre-merge]]` inherits `GIT_DIR`/`GIT_INDEX_FILE` the way git hooks do.
  `pre-commit-tests.sh:190-193` and `graph_home.py::_clean_env()` exist because hooks do. **Test before
  relying on the scrubbing.**
- Whether `origin/main` is red on a truly pristine checkout (sweep was read-only; evidence suggests
  only the 2 timing params).

---

# ⛔ TEST RESULT — THE `WorktreeCreate` HYPOTHESIS IS FALSIFIED

**Tested 2026-08-17 at David's instruction.** The hypothesis, taken from
`anthropics/claude-code#37611` via the web research: *registering a `WorktreeCreate` hook silently
disables Claude Code's exit-cleanup flow, and that is why 41 worktrees accumulated.*

**It is false here, and the real cause is different.**

## The measurements

| measurement | value |
|---|---|
| `WorktreeCreate` hook fires, ever (`hook-fires.log`) | **35** |
| hook-created worktrees still present | **9** |
| ⭐ **therefore cleaned up** | **~26 of 35 (74%)** |
| worktrees registered in total | **41** |
| of those, under `/private/tmp` scratchpad | **29 (71%)** |
| ⛔ mentions of `scratchpad` in 35 hook fires | **ZERO** |
| the hook IS in the path for `isolation: worktree` | **CONFIRMED** — the most recent fire carries `"name":"agent-a0f7455d07c3fb9df"`, this session's factlattice-editor dispatch |

## Why the 9 survive — the documented reason, confirmed per worktree

Claude Code's docs state the sweep *"skips a worktree that still holds work: changed or untracked
files, or unpushed commits."* Measured on every surviving hook-created worktree:

| worktree | dirty files | commits not on `origin/main` | branch |
|---|---|---|---|
| `composeio-agent-aece84d25116d1934` | 2 | 1 | `fix/gate-python-tests-reachability` |
| `composeio-agent-a42f272cb5f989539` | 2 | 4 | `wt-agent-a42f272cb5f989539-…` |
| `composeio-agent-affbe65d94ca48364` | 2 | **0** | `fleet-console` |
| `composeio-agent-a0065131a2aca4d31` | 0 | 1 | `auth-path-layer3-precommit` |
| `composeio-agent-ae58ead6119922dbd` | 3 | 2 | `wt-agent-ae58ead6119922dbd-…` |
| `composeio-agent-ac1fcccb16cbd67ce` | 0 | 1 | `wt-agent-ac1fcccb16cbd67ce-…` |

**Every one holds something. The mechanism is behaving exactly as documented.** Nothing is disabled.

## ⭐ The corrected diagnosis — two causes, neither of them the hook

**Cause 1 — 71% of the sprawl never entered the managed path.** 29 of 41 worktrees were hand-rolled
by agents with raw `git worktree add` into session scratchpad directories under `/private/tmp`,
which are ephemeral by design and invisible to the hook log and to every cleanup mechanism. Their
branches are the only durable record that they existed. This independently confirms `git-expert`'s
earlier finding (*"28 of 29 `/private/tmp` worktrees bypassed the sanctioned mechanism"*), reached
by a different instrument.

**Cause 2 — the managed remainder survives on a NAIVE "holds work" predicate.** `affbe65…`
(`fleet-console`) has **zero** commits absent from `origin/main` and survives on **two dirty files
alone**, which are almost certainly the generated-noise class (`captures/*.png`, rebuilt by the
pre-commit hook *by construction*). Others carry 1–4 "unlanded" commits that may well be
content-landed under a different SHA — the 47-branch figure says most are.

## What this changes

⛔ **The research recommendation — *"Do not write a `WorktreeCreate` hook until #37611 is resolved;
it disables the cleanup you currently get for free"* — must NOT be followed here.** Acting on it
would have fixed **0%** of the sprawl and removed the one mechanism that places worktrees somewhere
managed at all. It is a correct-sounding remedy for a cause that is not operative on this machine.

⭐ **worktrunk remains the right adoption, for different reasons than were first stated:**
- **`wt`'s M2-equivalent — one sanctioned worktree path, enforced — addresses cause 1**, which is
  71% of the problem. Nothing else on the table does.
- **`wt`'s six-tier content-integrated ladder addresses cause 2**, because it can see through
  "has unlanded commits" to "content is already on trunk under a different SHA" — precisely the
  distinction the naive predicate misses.
- The generated-noise question remains open and is now load-bearing: a dirty-file check that counts
  regenerated PNGs as "work" will keep worktrees alive forever. `wt merge`'s `--stage=all` default
  makes this worse, not better — hence `[commit] stage = "tracked"`.

## ⚠ Bounds of this test

- It measures **this machine, this hook, this period**. It does not refute `#37611` in general.
- `settings.json` mtime is **2026-08-14**, the hook script **2026-08-15**; several survivors predate
  both. The 74% cleanup figure therefore spans a period partly before the current hook, and the
  decisive evidence is not the ratio but the **per-worktree holds-work check above**, which explains
  all 9 survivors without needing the hypothesis.
- No live no-change dispatch was run as a positive control. The holds-work table makes one
  unnecessary — but a control would be the stronger instrument, and its absence is stated rather
  than glossed.
