# Decision-graph per-project split — FIRST-PASS PROPOSAL (not executed)

**Status:** PROPOSAL for David's approval. The monolithic snapshot
`platform/decision-graph-composeux-architect.lts` (byte-identical to the live
`decisions/decision-graph-composeux-architect.lts`) remains the **single canonical
source**. Nothing has been split. This note is the routing judgment to review.

## Why the split was NOT auto-executed (the blocking gap)

The MIGRATION-PLAN §"decisions (per-project split)" says route each node "to its
project's `decisions/` by domain." Two facts make that non-mechanical:

1. **The nodes carry no `domain:` field.** Of the 82 `DEC-*` nodes, **0** have a
   `domain:` and only **2** have a `spec:` back-reference. There is no machine-routable
   signal — routing is per-node *semantic* judgment.
2. **Plan node-count is off.** Plan says "972 lines, ~130 nodes." Actual: **1049 lines,
   82 `### [DEC-*]` nodes.** (The graph advanced since the plan was written — the copy
   was also stale by one node + 5 lifecycle updates; re-synced this watch.)

Additionally, a safe FactLattice split must carry **each node's edges** (relationship
lines + inline refs) with the node into its new file, and rewrite intra-graph edges that
become cross-corpus into the routing layer. Hand-extracting 82 blocks risks silently
dropping edges — high blast radius for an "if time permits" pass. So: **snapshot kept,
proposal delivered.**

## Heuristic first-pass routing (keyword classifier — REVIEW, do not trust blindly)

| corpus | count | confidence |
|---|---|---|
| app.console / intelligence-console | 44 | LOW — greedy bucket, over-captures (see below) |
| platform (UNROUTED) | 21 | needs David |
| app.composer / visual-composer | 7 | medium |
| svc / services | 4 | medium |
| io / composeio | 4 | medium |
| ux / composeux | 2 | medium |

**The 44-node console bucket is not trustworthy** — the classifier greedily pulls
anything mentioning COMPACT/CONTEXT/DELIVERABLE/SUBAGENT into console. Several clearly
belong elsewhere on a read: `DEC-DELIVERABLE-SWEEP` / `-LEDGER` / `-LIVE-MONITOR` →
**svc** (deliverable daemons), `DEC-COMPACT-SUMMARY-EXTRACTOR` → **svc**,
`DEC-INTELLIGENCE-HOST-SPAWNER` / `DEC-CONSOLE-AS-TEAM-HOST` / `DEC-INSIGHT-SELF-IS-NOT-
YET-A-PEER` → arguably **io** (Intelligence-as-peer is the ComposeIO origin).

**21 genuinely unrouted** (platform-cross-cutting or process): `DEC-COMPOSEPLATFORM-
REFRAME`, `DEC-SPEC-LTS-CONVERSION`, `DEC-PROJECT-STRUCTURE`, `DEC-CROSSING-IS-CHEAP`,
`DEC-MEMORY-IS-A-POINTER`, `DEC-CARRY-THE-LEAST-POINTER`, `DEC-HANDOVER-*`,
`DEC-MAGMA-AGGREGATION-AND-SCALING`, `DEC-DECISION-MCP-MAGMA-REFRESH`,
`DEC-DEFECT-INVESTIGATOR-AGENT`, etc. Many of these ARE legitimately platform-tier
(the reframe, the conversion, memory/handover discipline) and should stay in
`platform/decisions/`, not be forced into a project.

## Recommended path (for David)

1. Treat `platform/decisions/` as the home for the ~21 cross-cutting/process decisions.
2. Do the project split as a **supervised, node-at-a-time** pass (the console + svc
   buckets need eyes), not a keyword sweep — or add a `domain:` field to each DEC node
   at authoring time going forward so future splits ARE mechanical (recommended:
   amend the decision-archivist to stamp `domain:` on every new DEC node).
3. Keep the whole-graph snapshot as a cross-corpus reference view even after the split
   (MAGMA reads it as the `platform` corpus; project edges into it are routing).

_Generated first-pass classifier: preserved in the build log; deterministic keyword
rules over node IDs. Full per-node listing available on request._
