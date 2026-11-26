# PROCESS — SMD → LTS Porting

**Status:** runbook, authored 2026-08-16. Dispatchable as-is. Nothing in this document has been executed.
**Audience:** the subagent executing one port, and the dispatcher who hands it a filename and nothing else.
**Scope:** the METHOD for converting one `.smd` spec into a corpus-resident, query-answering `.lts`.
It is iterative by design: one file per dispatch, four steps per file, a gate between each.

> **The purpose test, against which every step is judged.** David, 2026-08-16:
> *"the biggest reason for moving from SMD to LTS was to make a corpus semantically queryable so that a
> desired SUBSET could be surfaced instead of simply reading the whole subset of a given project's
> specifications into context at session-start."*
> A port that produces a well-formed `.lts` nobody can usefully query has failed, and every existing
> instrument will call it clean. That is the failure this process is built around.

---

## 0. What this supersedes, keeps, and contradicts

`docs/composeplatform-spec-migration-PLAN.md` (233 lines, on origin/main) is the prior art. It is a
**placement plan**, not a porting process. Measured: the string `factlattice-editor` appears **0 times**
in it; the string `gap-analyz` appears **0 times** (its §5 "GAP INVENTORY" is a *code-ahead-of-spec
census*, a different artifact from the gap-analysis GATE); the word `edge` appears **0 times**.

**KEPT, and this document depends on it:**
- The decided taxonomy tree (§ "The decided taxonomy") — it is the input to Step 4 (LAND).
- The §2 mapping table, `spec/*.smd` (22) → target group. That table IS the per-file placement input.
- The two genuine placement FORKS (Haven corpus; `09-composeio-intelligence-mcp-server`). They remain
  **unresolved** and are listed here as blocking preconditions for the files they cover.
- §5's gap inventory as *seed evidence* handed to Step 2 — a place gap-analysis should look first.

**SUPERSEDED:**
- §7 "Next phase" — *"port the 22 SMD"*. A bare port is now prohibited. Steps 2 and 3 are mandatory
  gates between conversion and authority.

**CONTRADICTED, with the measurement:**
- The plan inherits `tools/smd_to_lts.py`'s premise: *"Content/notation is NOT rewritten (that is a
  separate enrichment pass)."* Measured below (§3.1), that premise yields **zero inter-file edges and
  zero semantic edges**, and puts an interface spec's actual contract into the parser's `ignorable`
  bucket with **0 lint errors**. The "separate enrichment pass" is not optional and not separable —
  it is Step 3, and without it the port is net-negative because it *launders* the document into
  citable authority it has not earned.
- §2 row `99-composeio-conventions` — *"port as historical/merge"*. That file is the **target of a
  symlink** from `corpora/platform-specs/99-composeio-conventions.smd`. Porting or archiving it breaks
  a live platform-specs file. See Preconditions P4.

---

## 1. Preconditions — verify ALL before the first dispatch

Each is a **blocker**. Each must be verified by an executed command whose output is pasted into the
report, never by reading a file and inferring.

### P1 — `factlattice-gap-analyzer`'s Bash grant must be LIVE, not merely written

`~/.claude/agents/factlattice-gap-analyzer.md` line 4 reads `tools: Read, Grep, Glob, Write, Bash`.
**That is the file, not the grant.** Agent tool grants load with the registry at session start; a grant
added mid-session is inert for the remainder of that session. The agent's own def (line 51) makes Bash
load-bearing: it must identify the tree it read, because a prior run *"reported a landed security fix as
completely absent from the repo and returned a P0 laundering verdict"* from a checkout on an unrelated
branch.

**Verification, and it must come from INSIDE the agent:** the first line of the gap-analyzer's report
must be the pasted stdout of

```
git -C ${COMPOSEIO_HOME} rev-parse HEAD && git -C ... status --short | head
```

If the agent cannot run it, it must say `⛔ BASH GRANT NOT LIVE` and **stop**. An agent that reports
"Bash available" without a pasted command output has read its own frontmatter, which is precisely the
testimony-instead-of-evidence failure. **This currently blocks all 22 ComposeIO conversions.**

### P2 — The landing corpus root must exist and be registered

`spec/` is a root of **no corpus**. Measured: `lts_find`'s `corpora_available` lists 13 corpora and
`spec/` is under none of them, so its 22 `.smd` are invisible to every `lts_*` tool. A `.lts` staged
there **cannot be previewed against a corpus, cannot be linted as part of one, and cannot be found**.
(The same is true of `${SMD_SPEC_HOME}/othercorpus-test/` — `othercorpus-test` is not in `corpora_available`
either, which is why the worked example in §6 is measured in `isolated` mode.)

Before dispatch, the executor must name the target corpus id and prove it resolves:
`lts_lint(source=<target corpus dir>)` must return a `profile` block with `available: true`.
`available: false` means V1–V6 were **inert** and a clean verdict checked nothing.

### P3 — Placement must be ruled, or the file is out of scope for this pass

Two forks from the prior plan are open. Files under them **do not get dispatched**:
- FORK 1 (Haven): `04-haven-bridge`, `05-haven-scheduler`, `06-news-service`, `07-notification-bridge`,
  `12-haven-terminus-composeapp` — app family vs services vs not-in-corpus.
- FORK 2: `09-composeio-intelligence-mcp-server` — `layers/composeio` vs `families/services`.

Everything landed while placement is unratified carries `[PLACEMENT UNRATIFIED]` in its status per
`[CXC-VISION-PLACEMENT]`, and placement is recorded as a **property**, not as a filename-only fact, so
LTS 1.1's directory organization can re-home it without re-deriving the decision.

### P4 — The symlink trap

`corpora/platform-specs/99-composeio-conventions.smd` is a symlink to `spec/99-composeio-conventions.smd`.
Any plan phrased as "then archive `spec/`" breaks a platform-specs file. That file is **last** in the
sequence, and its port must land the real file in platform-specs and delete the symlink in the same
change — not port-then-archive.

### P5 — Instrument availability must be checked, not assumed

Step 3 calls `lts_aggregate(op=…)` and `lts_traverse(tier="digest")`. **Neither is in the grant of the
agent that authored this document**, and `lts_aggregate` appears in no agent def inspected here
(`factlattice-recall-auditor` grants `lts_traverse` but not `lts_aggregate`). The executor must
confirm both resolve before relying on §5's band metrics, and where `lts_aggregate` is unavailable,
must compute the same numbers deterministically with Bash over the parsed graph and say so. Do not
substitute an estimate for a measurement.

---

## 2. The four steps at a glance

| # | step | agent | gate it must clear to proceed |
|---|---|---|---|
| 1 | **CONVERT** `.smd` → `.lts` | `factlattice-editor` | `lts_preview` shows the document's semantic content CONSUMED, not `ignorable`; `declared_vs_minted` has 0 not-minted |
| 2 | **GAP-ANALYZE** — the authority gate | `factlattice-gap-analyzer` (⚠ NOT `spec-gap-analyzer`, which is scoped to another project) | every `SPEC_NOT_IMPLEMENTED` / `IMPLEMENTED_NOT_SPECIFIED` finding is resolved or explicitly deferred with a reason |
| 3 | **EDGE ASSESSMENT** | `factlattice-editor` authors; `factlattice-recall-auditor` reviews | the query set (§5) is inside the band, measured, both directions |
| 4 | **LAND** | dispatcher / `git-expert` | the landed nodes are returnable by `lts_find` against the registered corpus |

**Between 1 and 2 there is a status sentinel.** `factlattice-gap-analyzer` **refuses `.smd`** by charter
— it reads `.lts` only. So the file must EXIST as `.lts` before it can be gap-analyzed, while
`[DEC-MIGRATE-SPEC-01-GATED-ON-GAP-ANALYSIS]` says it must not be AUTHORITATIVE until gap-analysis
passes. The live precedent resolves this: `corpora/platform-specs/73-bus-storage.lts` carries

```
lifecycle-position: converted — gap-analysis PENDING, not yet reconciled to implementation
```

**Every Step-1 output carries that exact sentinel and no other status.** It is removed only by Step 2.

---

## 3. STEP 1 — CONVERT

**Dispatch:** `factlattice-editor`, one file.
**Inputs:** the `.smd` path; the target corpus dir + its domain profile; the placement row from the
prior plan's §2; the doctype the document should carry.

### 3.1 ⛔ `tools/smd_to_lts.py` is the wrong starting point. Use it for ONE thing.

The tool (74 lines) does three things: rename `.smd`→`.lts`, inject `domain:` into frontmatter, rewrite
sibling `.smd` refs to `.lts`. It explicitly does not touch content. **Two problems, one fatal.**

**Fatal — it destroys the source.** With `--apply` it calls `os.remove(f)` on every `.smd`. There is no
staging, no review surface, no rollback other than git. **RULING: never run it with `--apply`.** Run it
in dry-run to obtain the ref-rewrite inventory, and have `factlattice-editor` author the `.lts` as a
NEW file alongside the surviving `.smd`. The `.smd` is deleted only after Step 4 lands.

**Measured — its output is a graph nobody can query.** `lts_preview` on the unmodified
`${SMD_SPEC_HOME}/othercorpus-test/40-zt2-mcp-other-component.smd` (which already carries `domain:`, so the raw
file *is* the tool's output for this case):

```
BECAME: 5 nodes, 4 edges — ALL FOUR ARE STRUCTURAL `CONTAINS`. Zero semantic edges. Zero inter-file edges.
LINE ACCOUNTING: 128 lines = 23 consumed + 100 ignorable + 5 UNACCOUNTED (96.1% coverage)
ERRORS: 0.    DECLARED vs MINTED: 5 declared, 5 minted, 0 not-minted.
```

The document is an **MCP interface spec**. Its four tool tables — the entire contract, every tool name
and description — are inside those **100 ignorable lines**. So is the `## Information Scope` block.
Lint would report this file CLEAN. The graph answers "what tool categories exist" and cannot answer
"what tools exist."

Same measurement on `02-zt2-charter.smd`: **18 nodes, 17 edges, all 17 `CONTAINS`, 98.0% coverage,
0 errors** — a flat star with one root and seventeen leaves. And the charter's goals table becomes a
single `id_table` property blob on the root, so `GOAL-001`, `GOAL-002`, `GOAL-003` and `PH-1`…`PH-7`
**are not nodes**. The answer to "what is the purpose of this software" is not addressable.

Three defects the port carries forward that the executor must fix in Step 1, all measured on the charter:
- Five `[DP-*]` nodes minted with **empty titles** (`### [DP-NAMING]` with no title text). An identifier
  with no title carries a verdict into every citation and states nothing. Give each a title that names
  its conclusion, not its subject.
- The goals/phases tables must be promoted to nodes (see 3.2).
- Frontmatter schema drift *within one corpus*: the charter carries
  `scope/audience/boundary/not-this/parent/depends-on/confidentiality/product-type`; the MCP doc carries
  `title/id/domain/tier`. Normalize to the target corpus's profile.

### 3.2 Promotion rules — what must become a node

| SMD shape | naive port yields | required |
|---|---|---|
| `### [ID] Title` + `- key: value` bullets | node + properties ✅ | keep |
| `### [ID]` with no title | node with `title: ""` ❌ | author a verdict-bearing title |
| an ID **table** (`\| GOAL-001 \| … \|`) | one `id_table` property blob on the parent ❌ | one node per row, with the row's columns as properties |
| a non-ID table (the MCP tool tables) | `ignorable` ❌ | promote to nodes if any row is a thing another node would cite; otherwise fold into a property with an explicit note that it was folded |
| an un-tagged `### Subhead` with bullets under it | **all bullets lost** (`near_miss_property`) ❌ | tag the subhead with an `[ID]`, or re-parent the bullets |

The last row is the parser rule the previewer states verbatim: *"node bodies end at the NEXT heading of
ANY level, so an un-tagged `### Contract` subhead orphans every property under it."* This is what
produced all 5 losses on the MCP file and all 4 on the charter.

### 3.3 Key hygiene, before preview

Normalize the property-key region (bullet marker → hyphen; add the space after `:`; relocate a leading
pictograph into the value; lowercase inside the key). **Refuse, blocking, any key beginning with a
digit** — SMD tier-numbered keys like `- 01-bus-protocol:` are exactly this shape, and a refused line
left in the document lands in `ignorable` and reports 100% coverage on a document missing a field.
Report every normalization: original, corrected, rule.

### 3.4 Acceptance bar for Step 1

- `lts_preview` run with `corpus=<target>` and `replaces=` if applicable. `declared_vs_minted.not_minted` = **0**.
- **`lost.total` = 0**, or every entry named with a stated reason it is acceptable.
- The executor states, in prose, **what is in the `ignorable` bucket and why that is correct**. A large
  ignorable count is legal (prose, blank lines, rules) — an *unexamined* one is the failure.
- `lts_lint` returns 0 errors AND its `profile.available` is `true`.
- File carries `lifecycle-position: converted — gap-analysis PENDING, not yet reconciled to implementation`.
- **`.smd` source still exists.**

### 3.5 SILENT SUCCESS at Step 1, and the check that catches it

**The failure:** lint returns 0 errors, preview returns 0 errors and 96% coverage, and the document's
entire substance is in `ignorable`. This is not hypothetical — it is the measured result above.

**The catch:** `line_accounting.consumed` versus the document's semantic line count. Require the
executor to report `consumed / (lines - blank - prose)`, and to name every table and every list that
landed in `ignorable`. A file whose interface contract is not in `consumed` fails Step 1 regardless of
what lint says.

**Report back:** node count, edge count, edge count **by inference type** (structural vs property),
line accounting, the ignorable-bucket disposition, every normalization applied, every promotion made.

---

## 4. STEP 2 — GAP-ANALYZE (the authority gate)

**Dispatch:** `factlattice-gap-analyzer`. ⚠ **NOT `spec-gap-analyzer`** — that is the `.smd` sibling,
scoped to another project, a different agent for a different corpus.

**Ratified by David 2026-08-14, `[DEC-MIGRATE-SPEC-01-GATED-ON-GAP-ANALYSIS]`.** Quoted, because the
sharp half is the reason the gate exists at all:

> *"DAVID'S GATE, which the architect did not propose: migrate it ONLY IF IT PASSES the … gap-analyzer
> (static analysis of spec against implementation) first. The reasoning is the sharper half of the
> decision — **migration CONFERS AUTHORITY**. A `.lts` node is queryable, citable and treated as current
> by every downstream reader and agent bootstrap, so migrating a STALE spec does not merely carry the
> staleness forward, it **LAUNDERS** it: prose nobody trusted becomes a node everyone cites. This is the
> same failure shape as an identifier that carries a verdict its node never earned."*

The gate was scoped to `spec/01`. **David has since generalized it to every file.** A
converted-but-un-analyzed file is not fit to become authoritative; `corpora/platform-specs/73-bus-storage.lts`
sitting at `converted — gap-analysis PENDING` is the live precedent for what that looks like on disk.

**Inputs:** the Step-1 `.lts`; the implementation paths (seed from the prior plan's §5 where the file
appears there); the prior run's delta file if one exists.
**Do NOT hand it Step 1's conclusions.** Its own charter: *"Never inherit the author's conclusions;
re-derive them."* Step 1's author reconciling its own output is a green light with no independent light.

### Acceptance bar

- The report's first line is the pasted `git rev-parse HEAD` + `git status --short` of the tree it read,
  and that SHA **equals** the landing tree's SHA.
- Every `SPEC_NOT_IMPLEMENTED` is either fixed in the `.lts` or recorded with `status: planned` /
  `status: abandoned` and a reason.
- Every `IMPLEMENTED_NOT_SPECIFIED` is either specified or recorded as deliberate omission. This is the
  detector for code that outran the spec, which is the whole reason for the gate.
- Only then is `lifecycle-position` advanced off the PENDING sentinel.

### SILENT SUCCESS at Step 2, and the checks

**Failure A — wrong tree.** The analyzer reads a worktree or a stale branch and every fact in its report
is true of the tree it read and false of the project. Precedent is in its own def: a landed security fix
reported as *"completely absent from the repo"* with a P0 laundering verdict. **Catch:** the SHA
assertion above. It is not optional and it must be pasted, not asserted.

**Failure B — nothing to be wrong about.** A Step-1 port that minted 5 nodes for a 128-line interface
document has almost no surface for a gap to appear on, so the analyzer honestly returns few or zero
gaps. **Catch:** report gaps *alongside* node count and the spec's `consumed` line count. **A 0-gap
verdict on a file with fewer than ~10 nodes is not a pass — it is evidence Step 1 under-populated the
graph.** Send it back to Step 1.

---

## 5. STEP 3 — EDGE ASSESSMENT ⭐

This is the step nobody had, and the reason this document exists. David's framing, followed closely:

> *"After the port to LTS, the actual graph edges minted have to be generated and assessed for how well
> they actually allow traversal over the converted content. SMD is a subset of LTS, and therefore a pure
> port may not generate the edges that we'd want to have return from an lts-engine query. This is
> particularly true because of the hierarchical nature of SMD files that are not necessarily respected
> or even needed in LTS files."*

**The failure mode, stated plainly:** a per-file port preserves INTRA-file structure and mints NO
INTER-file edges. Measured, both ZT2 files: **21 of 21 edges are `CONTAINS`, 0 cross reference anything
outside their own file.** The graph then has the shape of the old file boundaries — islands — and
traversal returns nothing useful across them.

### 5.1 The method is QUERY-FIRST, not structure-first

Do not start from "what edges could I add." Start from **what would I ask this corpus**, then check
whether the ported graph answers it. The edges you author are the ones that close the gaps. Load all
the original specs of the project (not just the file under port) before answering — the questions are a
property of the *system*, not of the file.

### 5.2 THE QUERY SET — the acceptance test for a port

David's three seeds, plus the additions that earned their place. Each row is falsifiable in **both**
directions, which is the whole point.

| # | question | entry point | answered by | fails LOW if | fails HIGH if |
|---|---|---|---|---|---|
| Q1 | What is the **purpose** of this software? | corpus root / charter | 1 node stating intent | returns the doc root, or nothing | returns every goal, phase and principle |
| Q2 | What major **problems** does it solve? | `prefix=CH`/`PROB` or `resolves` reverse-edges | the challenge/problem set | problems are a property blob, not nodes | every design note is a "problem" |
| Q3 | What **decisions** were made most recently, and why? | `lts_find(status=…, since=…)` | dated decision nodes | decisions carry no date property | every node is a decision |
| Q4 | What is BUILT vs planned vs abandoned? | `status` lifecycle | 3 disjoint sets | `status` absent → all one bucket | statuses are free text, not an enum |
| Q5 | What is the **interface/contract** of component X? | the component node | the endpoint/tool nodes | contract is in `ignorable` (measured: the MCP tables) | every table row is a top-level node |
| Q6 | What **depends on** X — the blast radius of changing it? | reverse `depends-on` | the dependent set | 0 reverse edges (measured: naive port) | X is a hub with 40+ dependents |
| Q7 | What was **tried and REJECTED**, and why? (anti-re-derivation) | `status: abandoned` / `contrast-with` | the rejected-alternatives set | the corpus records only what was chosen | — |
| Q8 | What **evidence** exists that it works? | `tested-by` / `verified-by` | test + evidence nodes | evidence lives only in prose (⚠ `lts_find(text=)` cannot see prose — use `lts_find_in_prose`) | — |
| Q9 | Where is the **authoritative** definition of term T? | `lts_find(text=T)` | exactly ONE node | T is defined in 4 files with no `supersedes` | — |
| Q10 | Where has **code outrun the spec**? | Step 2's report, as nodes | the gap set | gaps live only in a markdown report | — |
| Q11 | How do I **operate** it? | ops/runbook nodes | the runbook | operations are prose under an un-tagged subhead | — |
| Q12 | What are its **known limits**? | `boundary` / constraint nodes | the constraint set | limits are only in frontmatter `boundary:` (not a node, not an edge) | — |

The executor **runs all twelve** and pastes the actual result counts. Not a checklist — a measurement.

### 5.3 THE BAND — measurable, so an executor knows when to stop

David: *"post-port edge assessment isn't a checkbox, it's tuning to a band."* The band is six numbers.
Calibration for node size is the live `intelligence-console` corpus (1,525 nodes): p50 = 246 tokens,
p90 = 800, p99 = 1,679.

| metric | how to measure | IN BAND | under-linked | over-linked |
|---|---|---|---|---|
| **B1 answer rate** | Q1–Q12, non-empty and on-topic | 12 / 12 | any question returns 0 or returns only islands | — |
| **B2 answer volume** | nodes returned per top-level question, `tier="digest"` | **1–10 nodes, ≤ 6,000 tokens** | 1 node that is the document ROOT (see silent success) | > 10 nodes / > 6k tokens — the caller is back to reading the whole document, which is the thing the migration was for |
| **B3 hops to answer** | `lts_traverse(tier="digest")` from the realistic entry point | **≤ 2 hops** | > 3 hops, or unreachable | 1 hop returning > 10 |
| **B4 orphans** | `lts_aggregate(op="orphans")` | **0** non-root nodes with degree 0 | — | — |
| **B5 harvest yield** | authored cross-file edges vs the routing-line inventory counted in §5.4 **before** the port | **≥ 1 : 1** (every routing line becomes exactly one edge) | fewer edges than routing lines = intent was discarded | materially more, with no source — invented edges |
| **B6 degree ceiling** | `lts_aggregate(op="top_by_degree")` | **no node > 25 edges** | — | a hub node that returns half the corpus on any traversal |

**B5 is the self-proving one.** The inventory is countable from the `.smd` *before* any porting happens,
so the target number is known in advance and the executor cannot grade its own work. Count it first.

**B6 has a design consequence, and it is `[PRINCIPLE-CHEAPEST-NODE-OWNS-BEHAVIOR]` applied to edges:**
attach a harvested edge to the **most specific node that owns the topic**, never to the document root.
Hanging all harvested edges off the root is the easy implementation and it manufactures exactly the hub
B6 forbids — and it also destroys B2, because the only answer the root can give is "read everything."

### 5.4 ⭐⭐ HARVEST THE NEGATIVE SPACE — the highest-leverage sub-step

SMD documents carry hand-authored, **topic-labelled cross-document routing already written in prose**.
A naive port reads what a document ASSERTS and discards all of it — yet it is exactly the traversal
metadata that is missing, because it declares what a document ISN'T and where to go instead.

**There are THREE harvest surfaces, and which one dominates is CORPUS-SPECIFIC. Measure before you
harvest; do not assume.**

| surface | OtherCorpus corpus (183 files) | ComposeIO `spec/` (22 files) |
|---|---|---|
| **A.** `## Information Scope` arrow lines | 86 files carry the heading; **60 carry arrow lines → 210 lines** | **0 files. Zero.** |
| **B.** frontmatter refs (`boundary:`, `depends-on:`, `not-this:`, `parent:`, `amends:`) | 83 files, **388 refs** | **21 of 22 files, 81 refs** |
| **C.** trailing "Related Documents" bullet lists | present; **lost as `near_miss_property`** (measured: charter L189/193/194, MCP L127/128) | present |

⛔ **This is the correction the brief's framing needs.** The `## Information Scope` convention is a
**OtherCorpus convention, not a ComposeIO one**. For the actual Era 3 target — the 22 files in
`spec/` — surface A yields **nothing**, and the entire harvest is surface B. The ComposeIO breakdown:

```
boundary:    41 refs   ← topic-labelled prose, semicolon-delimited. e.g. 01-composeio-bus-protocol.smd:
                         "Protocol only; config schema in 02-...smd; module role contracts in 03-...smd;
                          design rationale in 00-...smd"   → three edges, three topics, one line
depends-on:  30 refs   ← already a list. Mechanical. Highest confidence, lowest effort.
amends:       3 refs   ← A TYPED RELATION, not a topic. 4 files carry it.
scope:        3 refs
not-this:     1 ref
```

**Consistency — measured, not assumed.** David: *"prior intelligences actually made effort to link
across documents, but that's not a guarantee that it was done consistently, nor that the edges created
will answer obvious questions."* Both halves are confirmed:

- **Coverage is partial.** OtherCorpus: 60/183 files (33%) carry arrow routing. ZT2 specifically:
  **9 of 16** spec files carry an Information Scope block; 7 do not.
- **19.5% of routing lines are unresolvable.** 41 of the 210 arrow lines do not name a `.smd` file at
  all — e.g. `Implementation details → See 40-* implementation docs`, `Operational runbooks → See
  resolved_by sections in relevant docs`. These are **not harvestable**; they must be resolved by hand
  or recorded as `EMPTY` with a reason. Do not let a glob become an edge.
- **The convention is inverted exactly where it matters most.** In ZT2 the routing graph is:
  `10-zt2-architecture` has out-degree **9** (it routes to everything) and in-degree 5. The
  **charter — the answer to Q1 and Q2 — has out-degree 0** in surface A, because it carries no
  Information Scope block at all. Its routing is hidden in frontmatter `boundary:`/`not-this:` prose
  instead. A harvester that reads only surface A concludes the charter is a sink.
  **Six ZT2 files are total orphans, in-degree 0 AND out-degree 0:** `12-zct-comparison-api`,
  `35-zt3-tooling-backlog`, `36-zt2-zt3-parity-register`, `37-zt3-status-telemetry`,
  `99-zt3-conventions`, `_category`. After a naive port those are six unreachable islands.

**Harvest procedure:**
1. **Count first** (this is B5's target, and it must be recorded before any edit).
2. Parse surface A, B and C into `(source file, topic label, target file)` triples.
3. **Resolve each target to a NODE, not a file.** The routing line's target is a filename; the edge's
   target must be the most specific node in that file that owns the topic. This is also **how the topic
   label is preserved without inventing a verb**: the label was the routing key used to *pick* the node,
   and the chosen node's title carries it. Nothing is lost.
4. Author each as a relationship-bearing property or in an explicit `## Relationships` / `### Semantic`
   section, with a profile-declared verb. **Bare/inline edges do not parse.**
5. Anything unresolvable → an `EMPTY` sentinel with the reason, per `[DX-SPEC-CARRIES-ITS-EVIDENCE]`.
   An absent key is indistinguishable from one nobody thought to write.

### 5.5 The emergent vocabulary — what earns a typed verb, and what does not

The labels recur, and that recurrence is real: `architecture overview` ×5, `operations framework` ×4,
`component internals` ×6, `component implementation` ×6, `protocol flows` ×6, `storage schema` ×2,
`domain model` ×2. SPEC P5's frequency-driven taxonomy evolution says to look here.

**But measure the tail before registering anything: 210 routing lines carry 164 DISTINCT labels. Only
22 labels recur at all (≥2), and the mode is 6.** 142 labels are hapax. That is a long tail, not a
vocabulary.

**Recommendation — register ONE new verb, not five:**
- The recurring labels name a **topic/destination**, not a **relation**. `Architecture overview →
  10-…smd` and `Storage schema → 20-…smd` express the *same* relation: *this document defers this
  topic to that document*. Five labels, one verb. Registering five verbs would encode the destination
  in the verb, which is a category error and would make the profile grow without bound as the corpus does.
- **Use core `see-also` for surface A** (it is already core, semantic layer), with the topic preserved
  by target-node selection per 5.4 step 3. Register a profile verb **only** if a corpus proves it needs
  to distinguish *defers-to* from *see-also* — and prove it with a query that the distinction answers.
- **`depends-on` is already core.** Surface B's 30 ComposeIO refs and 73 corpus-wide `depends-on:`
  frontmatter keys convert mechanically. Do this first; it is the cheapest real edge in the corpus.
- **`amends` EARNS registration.** It is a genuine relation, distinct from core `supersedes` (partial
  modification vs replacement), and ComposeIO has four files that are literally amendments
  (`01-amendment-bus-extensions`, `-capture-replay`, `-critical-ack`, `02-amendment-app-config`). Add
  `amends` to the ComposeIO domain profile. **This is the one profile change this process recommends.**
- **`parent:`** (57 OtherCorpus files) maps to core `part-of`. Mechanical.

### 5.6 Should `factlattice-recall-auditor` be a formal Step-3 reviewer? YES.

Its charter is literally the question this step asks: *"is this corpus USEFUL TO QUERY, not merely
correct."* It is read-only, it holds `lts_traverse`, and it checks **identifier and title SHAPE** — a
pointer names its subject, an argument names its verdict — which is precisely the defect Step 1 measured
(five `[DP-*]` nodes with empty titles). Critically, it is an **independent reader**: Step 3's author
grading its own edge set is II.6's blind spot, and B1–B6 are exactly the kind of metric an author can
satisfy by construction.

**Ruling: `factlattice-editor` authors the edges; `factlattice-recall-auditor` runs the query set and
reports the band.** The author does not report B1–B6 about its own work.

### 5.7 SILENT SUCCESS at Step 3 — three of them, and they are the worst in the process

**S1 — every query returns the document ROOT, and the root contains everything.** Q1 through Q12 all
"pass": each returns a node, non-empty, on-topic. But the node is `[ZT2]` with 17 `CONTAINS` children,
and the only way to use the answer is to read the whole document — the exact thing the migration exists
to eliminate. **Catch:** B2 counts a **root-only answer as a FAILURE, not a pass.** Every question's
answer must be a non-root node. Report the answering node id for all twelve.

**S2 — the harvested edges were authored as prose and minted nothing.** A `## Relationships` section
written without brackets, or with a verb the profile does not declare, produces **0 edges** with 0 lint
errors. This is SPEC §2.5's named silent-drop mechanism: *"precisely how a domain profile's registered
relationship verbs came to be honoured by a reporting tool and ignored by the parser."* **Catch:**
`lts_preview`'s `declared_vs_minted` and the edge-count delta must **equal** the harvest inventory
from 5.4 step 1. Not "roughly match" — equal, with the discrepancy named.

**S3 — the band was computed on a scope that invents or hides its findings.** An isolated preview
reports no dangles by construction; a narrow scope inflates them (measured: 114 at subset scope vs 56 at
full). **Catch:** every Step-3 measurement runs with `corpus=` at full corpus scope, and the report
states the scope it used.

**Report back:** the harvest inventory (counted before), the edges authored (by verb, by surface),
B1–B6 with actual numbers, the answering node id for each of Q1–Q12, and every routing line that could
not be resolved with its reason.

---

## 6. WORKED EXAMPLE — the ZT2 charter ↔ MCP pair, end to end

One subject, two documents. This is David's example and it survives contact with measurement.

**Step 1 measured (both files, `lts_preview`, isolated, raw source):**

| | `02-zt2-charter.smd` | `40-zt2-mcp-other-component.smd` |
|---|---|---|
| lines | 205 | 128 |
| nodes minted | 18 | 5 |
| edges minted | 17 | 4 |
| **of which semantic** | **0** | **0** |
| **of which inter-file** | **0** | **0** |
| consumed / ignorable / unaccounted | 86 / 115 / 4 | 23 / 100 / 5 |
| coverage | 98.0% | 96.1% |
| errors | **0** | **0** |
| declared vs minted | 18 / 18 | 5 / 5 |

Both files are lint-clean, preview-clean, and mutually invisible.

**Step 3 — the routing evidence, and the gap it does not cover.**
The MCP file's Information Scope block routes to exactly two files:
```
- Operations framework → 30-zt2-operations.smd
- Architecture overview → 10-zt2-architecture.smd
```
**Neither is the charter.** The charter's own routing lives in frontmatter prose — `boundary: "…does not
specify architecture (see 10-zt2-architecture.smd), data models (see 20-…), or design decisions (see
15-…)"` — and it too **never names the MCP document**. So the charter↔MCP edge exists in **no** harvest
surface. It is a judgment edge, and this is precisely the class no converter and no harvester will
produce. David's read is correct.

**The edge I would author, in full:**

```
source:    [ZT2-MCP]                       (in 40-zt2-mcp-other-component.lts)
verb:      implements                      (core, semantic layer — no profile registration needed)
target:    [GOAL-001]                      (in 02-zt2-charter.lts)
```

**Direction — from the implementing document TO the charter, and this is not arbitrary.** The charter is
stable and heavily cited; the MCP document changes. An edge authored on the *changing* side means the
charter needs no edit when a second, third or fourth implementation appears — and it keeps the charter
from accreting outbound edges until it becomes the B6 hub that returns half the corpus on any traversal.
**Every implementation points at the goal; the goal points at none of them.**

**Why `implements`, and why `GOAL-001` specifically.** `GOAL-001`'s criteria read *"Delegable to another
engineer; usable by junior data scientist."* The MCP document's own rationale bullets — which the naive
port **lost** as `near_miss_property` at L78/L80 — read *"Debugging: Claude inspects intermediate state
when issues arise"* and *"Maintainability: Future engineers benefit from Claude assistance with full
system access."* The MCP surface exists to make the system delegable. That is `GOAL-001`, stated in the
MCP document's own words, in lines the port threw away.

**A second edge, from the same reading:**
```
source: [ZT2-MCP]  verb: resolves  target: [CH-007]   ("Usability" — problems: "CLI complexity, …")
```

⛔ **AND HERE IS THE SHARPEST THING IN THIS DOCUMENT.** After a naive port, **`GOAL-001` IS NOT A NODE.**
It is one row inside the `id_table` property blob hanging off the `[ZT2]` root. The edge cannot be
authored, because its target does not exist. `CH-007` *is* a node (it was a `### [CH-007]` heading), so
that edge is authorable immediately.

**Therefore Step 3 has a hard dependency on Step 1's table-promotion rule (§3.2), and the dependency is
only discoverable by trying to author the edge.** This is the argument for running the query set on a
real pair before dispatching the other 21 files: Q1 ("what is the purpose of this software") is
unanswerable in the ported charter not because an edge is missing but because the *node* is missing, and
no amount of edge authoring fixes it. **Step 3 is allowed to send a file back to Step 1, and it must.**

**What the pair looks like in band, after repair:** `GOAL-001..003` and `PH-1..7` promoted to nodes
(+10); the MCP tool tables promoted (+15 tool nodes, measured, under the four category nodes); two authored
semantic edges charter↔MCP; the two harvested `see-also` edges from the MCP's Information Scope; the
charter's three harvested `boundary:` edges. Q1 then returns `[GOAL-001]` — one node, ~120 tokens,
0 hops from the corpus entry point. That is B2 satisfied, and it is the entire point of the migration.

---

## 7. STEP 4 — LAND

**Inputs:** the Step-3 `.lts`; the placement row; a confirmed corpus root (P2); confirmed placement (P3).

- Land on `main` in the registered corpus root. Not `spec/` — nothing lands in a root of no corpus.
- Remove the `converted — gap-analysis PENDING` sentinel only if Step 2 cleared. Otherwise it stays and
  the file is explicitly not authoritative.
- Delete the `.smd` **in the same change**, not in a later sweep. For `99-composeio-conventions`, delete
  the symlink and land the real file in platform-specs in one change (P4).
- **The porting agent does not commit.** Output comes back unstaged; the dispatcher reads the diff.
- Emit the SPEC §17.4 quality event to `{corpus}/events/quality.jsonl`.

### SILENT SUCCESS at Step 4, and the check

**The failure:** the file lands somewhere that is a root of no corpus, so every `lts_*` tool reports it
clean **by not seeing it**. This is not hypothetical — it is the current state of all 22 files in
`spec/` and of `othercorpus-test/`.

**The catch — a positive-control query, run AFTER landing:**
```
lts_find(corpus=<target>, prefix=<a prefix the new file introduces>)
```
must return the new nodes. **A zero here means the file is invisible, not that it is clean.** Paste the
count. Follow with `lts_lint(source=<corpus root>, dangle=true)` and confirm `profile.available: true`
and that the dangling count did not rise.

---

## 8. ROLLBACK

The process is designed so rollback is cheap at every step, and the design decision that buys this is
in §3.1: **the `.smd` survives until Step 4.**

| step | rollback |
|---|---|
| 1 | delete the untracked `.lts`. The `.smd` is untouched. |
| 2 | revert the `lifecycle-position` line. No content changed. |
| 3 | `git checkout -- <path>` — the edge authoring is unstaged by construction. |
| 4 | revert the single landing commit; restore the `.smd`. Because the `.smd` deletion is in the *same* commit, one revert restores both sides. |

⛔ **The one irreversible action in reach is `tools/smd_to_lts.py --apply`**, which `os.remove`s every
source file in the directory with no staging. **It is prohibited by this process.** Dry-run only.

---

## 9. Forward-compatibility with LTS 1.1

Per `[DEC-LTS-1-1-SCOPE-AMENDED-BY-DAVID]` (2026-08-16), 1.1 adds per-corpus **directory organization**,
`_category` subsection frontmatter, and per-corpus **prefix numbering**. Note the correction David
accepted in that node: Era 0 detects and records with no ratification; **Era 1 ratifies; Era 2 is where
documents are stamped and strict is flipped, per corpus.** Until Era 2 every corpus stays in legacy
mode. Three interactions with this process:

1. **Do not copy SMD's tier numbers into node IDs or prefixes.** Each corpus defines its own numbering,
   and **the prefix never becomes a node**. SMD's `00-`/`01-`/`20-` tiers are the *old* corpus's
   organization, and baking them into identifiers makes them permanent. They belong in the filename
   and in a property, nowhere else.
2. **`_category.smd` is not a content document.** ZT2 has one; it is corpus organization and maps to
   1.1's `_category` frontmatter, not to a ported spec. Do not port it, do not count it in the census,
   and do not let it show up as an orphan in B4.
3. **Record placement as a property, not as a filename-only fact.** When 1.1's directory organization
   lands, files will move. A placement recorded only by where the file sits must be re-derived; a
   placement recorded as a property survives the move. This also carries the `[PLACEMENT UNRATIFIED]`
   tag forward where P3's forks are still open.

---

## 10. Recommended promotion to the graph (RECOMMENDATION ONLY — nothing authored)

Per `[PRINCIPLE-STRUCTURE-SURVIVES-THE-BOUNDARY]`: **route the RULE to a surface wake enumerates,
the RATIONALE to the graph.** This markdown is a runbook — an executor reads it start to finish once,
per dispatch. That is the wrong home for anything another node needs to *cite*.

**Promote to the graph (5 nodes):**

| proposed node | kind | why it must be structure, not prose |
|---|---|---|
| `[STRATEGY-PORT-IS-GATED-ON-GAP-ANALYSIS]` | STRATEGY | Generalizes `[DEC-MIGRATE-SPEC-01-GATED-ON-GAP-ANALYSIS]` from spec/01 to every file. It is a **ruling** and will be cited by every port; a ruling that lives only in a runbook is the loss class that put four of David's own decisions in no commit. |
| `[STRATEGY-EDGE-ASSESSMENT-IS-A-BAND]` | STRATEGY | The B1–B6 band and the "falsifiable in both directions" framing. Other corpora will want to cite the band without inheriting the SMD runbook. |
| `[SPEC:FL-ROUTING-HARVEST]` | SPEC | The three harvest surfaces, the target-resolves-to-a-node rule, and the ruling that a topic label picks a target rather than minting a verb. This is a **reusable parser/authoring contract**, not a migration step — it applies to any prose corpus entering LTS. |
| `[DEC-AMENDS-EARNS-A-VERB]` | DEC | The one profile change (§5.5). A profile verb registration must be a decision node with its rationale and its counter-case (why the other 163 labels did **not** earn one), or the next author re-derives the tail measurement. |
| `[INSIGHT-A-CLEAN-PORT-CAN-BE-AN-EMPTY-GRAPH]` | INSIGHT | The §3.1 measurement — 0 errors, 96.1% coverage, and the entire interface contract in `ignorable`. This is a **transferable scar**, not a process step, and it belongs where a future instance meets it by relevance. |

**Stays here, in markdown:**
- Everything procedural: dispatch order, per-step inputs, report-back shapes, the preconditions, the
  rollback table. An executor reads this linearly; a graph node would fragment a sequence into
  fragments nobody reassembles.
- The worked example. It is narrative and its value is in being read whole.
- The band's *specific numbers* (≤10 nodes, ≤6k tokens, ≤2 hops) — these are tuning parameters that
  will change per corpus. The **rule** ("there is a band, and it is falsifiable both ways") goes to the
  graph; the current dial settings stay here.

**Not authored. Recommendation only, per the brief.**
