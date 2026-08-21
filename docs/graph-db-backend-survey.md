---
title: "Graph-DB Backend Survey for MAGMA"
scope: "[DEC-MAGMA-AGGREGATION-AND-SCALING] part 4 — a comparative analysis of open-source graph databases that could back MAGMA IF the in-memory FactLattice engine hits its ceiling. Weighted against MAGMA's actual requirements; concludes with a ranked recommendation and a switch trigger tied to the perf-test ceiling."
audience: "The architect deciding whether/when to move MAGMA off its in-memory engine."
status: "Research — no migration proposed yet. Decision-support for a ceiling we have not hit."
written: "2026-07-23"
relates-to:
  - "docs/magma-primer.md (what MAGMA is + the §7 scaling honesty and §8 aggregation gap)"
  - "docs/magma-extraction-plan.md (the current engine's layer map + the transport-agnostic tool(corpus,args)->dict contract)"
  - "740Dave/factlattice (the parser + engine core: store.py / router.py / tools.py / ContentHashFreshness)"
---

# Graph-DB Backend Survey for MAGMA

## 0. TL;DR

**Do not switch yet.** MAGMA's in-memory FactLattice engine is right-sized for a curated
knowledge corpus (hundreds to low-thousands of nodes per partition) and its zero-dependency,
git-is-truth, instant-rebuild design is a *feature*, not debt. A backend DB is only worth its
weight once the parallel perf test shows a real ceiling (§8).

**If/when we do switch, the ranked answer is:**

1. **LadybugDB** — the maintained lineage of Kùzu. Embedded property graph, MIT, Cypher
   typed-edge traversal, columnar and disk-backed (breaks the RAM ceiling, not just raises it),
   fast bulk `COPY` load (fits our rebuildable-index model exactly), Python-native, one DB
   directory per corpus = partitioning for free. The single caveat is governance youth (see §2).
2. **DuckDB + DuckPGQ** — embedded, MIT, Python-native, and *uniquely* it also closes the
   aggregation gap the primer flags in §8 (count/sum/group-by) in the **same** engine as the
   graph traversal. The catch: DuckPGQ is still an experimental research extension.

A **cheapest-possible bridge** if the ceiling turns out mild — stdlib `sqlite3` with recursive
CTEs — is covered in §3 and §7; it keeps the pure-stdlib ethos and defers the "real graph DB"
question further.

---

## 1. What we are actually weighting against

From `magma-primer.md` and `magma-extraction-plan.md`, MAGMA is **not** a general graph-DB
workload. It is a very specific shape, and most of the graph-DB market is built for a *different*
shape (huge, mutating, transactional, server-hosted graphs). Our requirements, in priority order:

| # | Requirement | Why it filters hard |
|---|---|---|
| R1 | **Embedded / in-process** | MAGMA is a local stdio MCP service, stdlib-heavy. A server DB (daemon, port, auth, lifecycle) is an operational tax with no payoff for one operator + one local client. This eliminates most of the "famous" names. |
| R2 | **Typed-edge BFS traversal** | The core op: walk `DEPENDS_ON` / `SUPERSEDES` / `CONTAINS` / `REALIZES` / `RELATES_TO`, N hops, return a subgraph. The engine must do *relationship-typed* variable-length traversal well, not just KV or full-scan. |
| R3 | **Content-hash freshness / rebuildable index** | Truth stays in git-tracked `.lts`. The DB is a **throwaway, rebuildable index**. This inverts the usual DB value prop: we want **fast bulk rebuild + fast load**, and we do NOT care about durable transactional writes, WAL, crash recovery, or ACID isolation. A DB that is slow to bulk-load or that treats its file as the source of truth is a *worse* fit than a fast-rebuild one. |
| R4 | **Multi-corpus partitioning** | One graph_index per project/corpus, domain-filtered, rebuilt independently. Maps cleanly to "one DB file/dir per corpus." Any embedded DB gets this for free; server DBs force a shared-instance multi-tenancy model we don't want. |
| R5 | **Python-native / clean bindings** | The whole stack is Python; the MCP surface is Python. C-extension wheels on PyPI are fine; a JVM or a separate-language runtime is friction. |
| R6 | **Open-source, permissive** | Whole stack is going OSS (see memory: composeio-opensource-plan). Copyleft (AGPL/GPL/SSPL) or "open-core with a commercial server" is a poison pill for a dependency we ship. MIT/Apache/BSD strongly preferred. |

**Two consequences of R3 that keep tripping up naive comparisons:**
- We are indexing, not persisting. "Transactional write throughput," "concurrent writers,"
  "MVCC," "durability" — the headline features of most graph DBs — are **worth ~zero to us**.
- Because the index is rebuildable in seconds today, the bar a backend must clear is not
  "correctness under concurrent mutation" but "did it make traverse-at-scale fast *and* keep
  rebuild cheap." A backend that wins traversal but makes rebuild a 60s ETL job is a bad trade.

**The migration-cost anchor (applies to every candidate below).** Per the extraction plan, the
engine's real contract is four pure functions — `find` / `node` / `traverse` / `capabilities`
— shaped `tool(corpus, args) -> dict`, over a `MagmaStore` (a `corpus_id -> Partition` registry).
The `.lts` **parser stays** (FactLattice, already extracted, byte-identical to Nexus's). The
**content-hash freshness check stays** (it is storage-independent: hash the source files, compare).
So a backend swap touches exactly one layer: replace the in-RAM adjacency structure + the
hand-written BFS in `tools.py`/`store.py` with "load parsed nodes/edges into backend X, express
traverse as X's query." The MCP tool surface, the parser, and the freshness invariant are
untouched. **Migration cost per candidate below = how cleanly its ingest + query model maps onto
"bulk-load parsed nodes/edges, then typed variable-length BFS," measured against that ~500–800 LOC
store+traverse layer.**

---

## 2. Embedded property graphs (the natural fit) — Kùzu / LadybugDB

**This is the category built for exactly our shape:** an in-process property-graph engine, the
way SQLite/DuckDB are in-process relational engines. If we ever need a backend, it comes from here.

### Kùzu — the reference design, now archived
Kùzu was the standout: embedded, MIT, C++ core with first-class Python wheels on PyPI, Cypher,
columnar disk-backed storage, vectorized execution, built specifically to be *fast at multi-hop
joins/traversals* (its whole research pitch was worst-case-optimal join + factorization for graph
workloads). It maps onto our requirements almost perfectly on paper — R1 ✓, R2 ✓✓ (Cypher
variable-length + typed rels), R3 ✓ (fast `COPY FROM` bulk load from CSV/Parquet/Arrow — ideal for
"rebuild the index from parsed `.lts`"), R4 ✓ (one DB directory per corpus), R5 ✓ (`pip install
kuzu`), R6 ✓ (MIT).

**The catch — this is load-bearing for the recommendation:** in **October 2025 the `kuzudb/kuzu`
repo was archived** after Apple acqui-hired Kùzu Inc. The MIT code is frozen and still works, but
upstream maintenance stopped. A frozen MIT snapshot is *usable* (we could vendor it and it would
keep running), but recommending a dead upstream as a new strategic dependency is unwise. The
ecosystem forked immediately.

### LadybugDB — the maintained successor (the actual recommendation)
Community-driven fork of Kùzu launched Sept/Oct 2025 (Arun Sharma, ex-Facebook/Google), MIT,
carries the Kùzu core forward and is *actively* developing beyond it: multi-label nodes, and a
"graph lakehouse" direction that interoperates with DuckDB storage and reads/writes
**Arrow/Parquet** and object stores. It raised a $40M Series A (mid-2026), which cuts both ways —
resourced and unlikely to vanish, but now VC-backed, so watch for an eventual open-core split
(the MIT core is the thing to depend on; treat any future "cloud/enterprise" tier as out of scope).

- **Fit:** identical to Kùzu's on our six requirements (it *is* the Kùzu core), plus the
  Arrow/Parquet ingest path makes the rebuild story even cleaner — parse `.lts` → Arrow table →
  bulk load.
- **Embedded vs server:** embedded. ✓
- **License:** MIT. ✓
- **Python story:** Python bindings inherited from Kùzu; `pip`-installable. ✓
- **Migration cost from FactLattice:** **Moderate, and the lowest-friction of the "real graph DB"
  options.** You define a node table per node-kind (or one polymorphic node table with a `kind`
  column) and one rel table per edge type (`DEPENDS_ON`, `SUPERSEDES`, …). Ingest = bulk-load the
  parser's node/edge lists. `traverse` becomes a Cypher variable-length pattern
  (`MATCH (a {id:$start})-[:DEPENDS_ON*1..$hops]->(b) RETURN …`) — a near-literal translation of the
  current hand-written BFS, and *shorter* than the code it replaces. `find` becomes a `WHERE` over
  node properties; `node` a one-hop neighborhood match; `capabilities` a grouped edge-type query.
  The four `tool(corpus,args)->dict` signatures and the MCP surface are unchanged; only the guts of
  the store+traverse layer change. Freshness check unchanged.
- **Risk to weigh:** governance youth. Kùzu's death is the cautionary tale — depending on a
  single-vendor embedded DB is exactly what bit here. Mitigant: it's MIT and vendorable, so a
  second fork death is survivable (freeze on a known-good commit), and the `graph_index` is
  rebuildable so we are never data-locked. Also note the field is crowded (Ladybug, plus other
  forks like "bighorn" and Vela Partners' fork) — Ladybug is currently the most active and
  best-resourced, but re-verify liveness at decision time rather than trusting this doc's snapshot.

**Verdict:** if we switch, LadybugDB is #1. It is the only option that satisfies all six
requirements with a query model (Cypher typed variable-length traversal) that is a *direct*
translation of MAGMA's core op, and a disk-backed columnar store that breaks the RAM ceiling
rather than merely postponing it.

---

## 3. Embedded relational engines doing graph work (the pragmatic dark horses)

### DuckDB + DuckPGQ — one engine for BOTH traversal and the aggregation gap
DuckDB is the embedded analytical database (MIT, Python-native `pip install duckdb`, columnar,
absurdly fast bulk ingest, reads Parquet/Arrow/CSV natively — a perfect rebuild-from-source story).
On its own it is relational, but the **DuckPGQ** community extension adds SQL/PGQ (the SQL:2023
property-graph standard): define a property graph over base tables, then do
`MATCH`/variable-length paths, reachability, shortest path.

- **Fit:** R1 ✓ (embedded), R2 ✓ (SQL/PGQ path patterns; typed edges as labeled rel tables),
  R3 ✓✓ (DuckDB's ingest is best-in-class; rebuild-from-`.lts` is trivial), R4 ✓ (one `.duckdb`
  file per corpus, or schemas within one file), R5 ✓, R6 ✓ (MIT).
- **The unique win:** the primer's §8 explicitly calls out that MAGMA has **no aggregation**
  (count/sum/group-by) and that those meta-questions currently get punted to a *separate* SQLite
  store. DuckDB answers **both** the graph traversal (via DuckPGQ) *and* the count/rollup questions
  (native SQL) in **one embedded engine**. That collapses the "MAGMA for traversal + SQLite for
  counting" two-store split the primer describes into a single dependency. For
  `[DEC-MAGMA-AGGREGATION-AND-SCALING]` specifically — a decision that is *about* aggregation AND
  scaling — this is the option that addresses both halves at once.
- **Embedded vs server:** embedded. ✓
- **Python story:** excellent — DuckDB's Python API is one of the best in the category. ✓
- **Migration cost:** **Moderate.** Ingest is trivial (nodes/edges → DuckDB tables). `find`/`node`
  and all aggregation are plain SQL. `traverse`/`capabilities` need DuckPGQ path patterns. The
  friction is DuckPGQ's maturity, not DuckDB's.
- **The catch:** DuckPGQ is a **community extension and still a CWI research project** — explicitly
  "work in progress," API can move, some path features still landing. Property graphs became
  persistent + cross-connection-synced around DuckDB v1.1.3, so it's real, but this is a bet on a
  moving target, not a settled one. If DuckPGQ's traversal proves too immature at our scale, the
  fallback within the same engine is recursive-CTE traversal in plain SQL (works today, verbose).

**Verdict:** #2, and arguably #1 *if the scaling decision and the aggregation decision are being
made together* — which, given the ticket name, they are. Re-rank DuckDB above Ladybug the moment
"we also want native count/group-by over the corpus" becomes a firm requirement rather than a
nice-to-have.

### SQLite (stdlib `sqlite3`) + recursive CTEs — the minimum-migration bridge
Not a graph DB, but the honest cheapest option and the one most faithful to MAGMA's current ethos.
Nodes table + edges table, both indexed; typed BFS via `WITH RECURSIVE` over the edges table
filtered by edge type. One `.sqlite` file per corpus = partitioning. `sqlite3` is in the **Python
stdlib** — zero new dependencies, which preserves the "stdlib-heavy, no third-party deps" property
the extraction plan prizes (the trimmed engine imports only `dataclasses/typing/json/os/hashlib`;
adding `sqlite3` keeps that stdlib-only).

- **Fit:** R1 ✓ (embedded, and stdlib), R2 ~ (recursive CTE traversal works but is verbose,
  hand-written per edge-type, and not as fast as a purpose-built graph engine on deep/wide walks),
  R3 ✓ (fast bulk `INSERT`/`executemany`; on-disk index survives across runs so you can even skip
  rebuild when the hash matches), R4 ✓, R5 ✓✓ (stdlib), R6 ✓ (public domain).
- **Migration cost:** **Smallest of all.** No new dependency, no new deployment story. You move the
  adjacency structure from Python dicts to a SQLite file and rewrite BFS as recursive CTEs. This is
  the "we hit a mild ceiling — memory, not traversal speed — and just want the index on disk" answer.
- **Where it fails:** it does not *raise the traversal ceiling* much — recursive CTEs over a large
  edge table are not the worst-case-optimal joins Kùzu/Ladybug give you. It solves "index too big
  for RAM" better than it solves "traversal too slow." Also loses Cypher ergonomics; every traversal
  variant is bespoke SQL.
- **Note on "sqlite-graph"-style extensions** (loadable graph/Cypher extensions for SQLite, various
  hobby projects): these exist but are immature, sparsely maintained, and often require compiling a
  loadable extension — that gives up the stdlib-only advantage that is the *entire reason* to pick
  SQLite here. If you want SQLite, use plain recursive CTEs; if you want real graph traversal, use
  Ladybug/DuckDB. The extensions are the worst of both.

**Verdict:** the right answer *only if the ceiling we hit is memory footprint, not traversal
latency,* and only if keeping the dependency footprint at zero outweighs traversal ergonomics.
Otherwise it's a way-station, not a destination.

---

## 4. Embedded, but a different query paradigm — CozoDB / mnestic

CozoDB: embedded (like SQLite), Rust core with Python bindings, MPL-2.0, a
relational-graph-vector engine queried in **Datalog**. It genuinely fits R1/R4/R5/R6 and its
Datalog is expressive for recursive graph queries (transitive closure, fixed-point traversal are
natural in Datalog). It even bundled vector search early ("the hippocampus for AI").

- **The problems for us:**
  - **Maintenance:** upstream `cozodb/cozo` went quiet after ~Dec 2024; the actively-maintained
    path is now a fork (`mnestic`). Same "single-maintainer embedded DB went dormant, community
    forked" story as Kùzu, but *earlier* and with a *smaller* community — higher abandonment risk,
    less momentum than Ladybug.
  - **Query-model mismatch / migration cost:** Datalog is a *different mental model* from MAGMA's
    imperative typed-BFS. It's powerful, but the translation from our hand-written BFS to Datalog
    rules is less mechanical than the Cypher/SQL-PGQ translations, and Datalog is a skill the team
    (and future OSS contributors) is less likely to have. Higher cognitive migration cost for no
    capability the property-graph options don't also give us.
- **Verdict:** technically credible, strategically worse than Ladybug/DuckDB on every axis that
  matters (maintenance momentum, query-model familiarity, migration mechanics). Not recommended
  unless its vector-search integration became a hard requirement and we wanted graph+vector in one
  embedded box — and even then, Ladybug now has vector/FTS built in too. Pass.

---

## 5. Server graph DBs (fail R1 — documented for completeness)

The task asked to note server options. All of these are strong *databases* and **wrong for MAGMA**
because they violate R1 (embedded) and force an operational model — a daemon, a port, auth, a
lifecycle to babysit — that buys us nothing for one operator + one local stdio client, and actively
hurts the "ships as a self-contained OSS Python package" goal.

| DB | Embedded? | License | Python | Why it's wrong for MAGMA (beyond "it's a server") |
|---|---|---|---|---|
| **Neo4j** (Community) | No (JVM server). "Embedded" mode exists but is **Java-only, in-JVM** — not reachable from a Python process without a server or JPype hackery. | **GPLv3** (Community); Enterprise is commercial. | Bolt driver (client/server). | GPL + JVM + server. Triple disqualifier. Cypher is lovely, but Ladybug gives us Cypher *embedded and MIT*. |
| **Memgraph** | No (in-memory C++ **server**). | **BSL** (source-available, not OSI-open) → converts to Apache-2 after a delay. | Bolt client. | Server + BSL. Fast, but source-available ≠ the permissive OSS R6 wants, and it's a server. |
| **FalkorDB** (the maintained RedisGraph successor — RedisGraph itself was EOL'd by Redis in 2023) | No (Redis-module **server**). | **SSPL** (Redis-module lineage). | Redis client. | Server + SSPL (the least-shippable license of all for an OSS project). RedisGraph is dead; don't build on the successor's server+SSPL model. |
| **TerminusDB / TerminusCMS** | Effectively server (Rust/Prolog store, accessed over HTTP; "embedded" isn't its real posture). | Apache-2 (core) but the product is server-shaped. | HTTP client. | Document-graph + revision-control model is interesting but it's a server and a heavy conceptual mismatch (it wants to *be* the versioned source of truth — but our source of truth is git-tracked `.lts`, so its headline feature is redundant). |
| **Apache AGE** | No (PostgreSQL **extension** → needs a Postgres server). | Apache-2. | psycopg client. | Requires standing up Postgres. Same "server tax, zero payoff" verdict. |
| **ArcadeDB** | Optionally embedded, but **JVM**. | Apache-2. | HTTP/remote (embedded needs JVM). | Permissive and multi-model, but JVM embedding from Python is not a clean story; positioned as a Kùzu *migration target* but as a server/JVM engine, not a Python-in-process one. |

**Blanket verdict for §5:** none of these should back MAGMA. If MAGMA ever outgrew the
"embedded, one operator" model — e.g. it became a shared multi-consumer service on the bus with
many concurrent writers and a genuine multi-tenant security need — that would reopen the question
(and Neo4j/Memgraph would be real candidates *then*). But that is the *Nexus* problem the extraction
plan deliberately walked away from; composeio's MAGMA-lite is single-operator by design. Server DBs
solve problems we intentionally don't have.

---

## 6. The incumbent baseline — in-memory FactLattice + NetworkX-class options

For completeness, the "stay in-memory but formalize it" options:

- **Current engine (pure-Python dict adjacency + hand-written BFS).** Zero deps, git-is-truth,
  instant rebuild, freshness-checked. The baseline every candidate must *beat by enough to justify a
  dependency*. At current scale (hundreds of nodes, ~740 in the largest corpus) it is effectively
  instant and nothing beats it on simplicity. Its only weakness is the untested large-scale curve
  (primer §7).
- **NetworkX.** Would replace ~300 LOC of hand-rolled graph code with a mature library (BFS, typed
  edges via edge attributes, huge algorithm catalog, BSD, pure-Python, trivial Python story). But it
  is **still in-memory** — it raises code quality, not the ceiling. It also has meaningful per-node
  Python-object memory overhead, so it can hit the RAM wall *sooner* than the current lean dicts. Its
  "on-disk" options (pickle/GraphML/GEXF serialization, or `nx` + a KV store) are just
  cache-serialization, not on-disk traversal — no lazy loading, no bigger-than-RAM graphs. **If we
  want a nicer in-memory engine, NetworkX is a fine refactor; it is not a scaling answer.** Do not
  reach for it to solve a ceiling problem — it solves a code-tidiness problem.
- **igraph / graph-tool.** C-backed, faster and more memory-efficient than NetworkX for large graphs,
  still in-memory. igraph (GPLv2) and graph-tool (LGPL/GPL, painful to install) both bring copyleft
  and install friction. Marginal in-memory speedup, wrong license posture — skip.

**Verdict:** the in-memory family (including NetworkX) is where we *are*, not where we *go* when we
hit the ceiling. The whole premise of this survey — an on-disk, bigger-than-RAM-capable backend — is
what these can't provide.

---

## 7. Scorecard

Weighted against R1–R6 (✓✓ = excellent, ✓ = good, ~ = partial/caveated, ✗ = fails). Sorted by fit.

| Candidate | R1 Embedded | R2 Typed BFS | R3 Rebuild/load | R4 Partition | R5 Python | R6 License | Migration cost | Overall |
|---|---|---|---|---|---|---|---|---|
| **LadybugDB** (Kùzu fork) | ✓✓ | ✓✓ (Cypher var-len) | ✓✓ (COPY/Arrow) | ✓✓ (dir/corpus) | ✓ | ✓✓ MIT | Moderate | **#1** |
| **DuckDB + DuckPGQ** | ✓✓ | ~→✓ (SQL/PGQ, WIP) | ✓✓ (best-in-class ingest) | ✓✓ | ✓✓ | ✓✓ MIT | Moderate | **#2** (#1 if aggregation is in-scope) |
| **SQLite + recursive CTE** | ✓✓ (stdlib) | ~ (verbose, not WCO) | ✓ | ✓✓ | ✓✓ stdlib | ✓✓ PD | **Smallest** | Bridge / mild-ceiling only |
| Kùzu (archived) | ✓✓ | ✓✓ | ✓✓ | ✓✓ | ✓ | ✓✓ MIT | Moderate | Frozen — use Ladybug instead |
| CozoDB / mnestic | ✓✓ | ✓ (Datalog) | ✓ | ✓✓ | ✓ | ✓ MPL | Higher (Datalog) | Credible, strategically worse |
| NetworkX | ✓✓ (in-RAM) | ✓ | ~ (cache only) | ✓ | ✓✓ | ✓✓ BSD | Small | Tidier in-mem, **not** a ceiling fix |
| Neo4j Community | ✗ (server/JVM) | ✓✓ | ~ | ~ | ~ (client) | ✗ GPL | High | Wrong shape |
| Memgraph | ✗ (server) | ✓✓ | ~ | ~ | ~ | ✗ BSL | High | Wrong shape |
| FalkorDB | ✗ (server) | ✓ | ~ | ~ | ~ | ✗ SSPL | High | Wrong shape |
| TerminusDB | ✗ (server-ish) | ✓ | ~ | ~ | ~ | ✓ Apache | High | Redundant with git-truth |
| Apache AGE | ✗ (Postgres) | ✓ | ~ | ~ | ~ | ✓ Apache | High | Server tax |

---

## 8. When we'd actually switch — the trigger, tied to the perf test

**Default: don't.** The in-memory engine's value is that it is boring, dependency-free, and
correct. Every candidate above adds a third-party dependency, a build/wheel surface, and a query
translation layer, in exchange for headroom we are not yet using. Adopting a graph DB *before* the
ceiling is real is premature optimization that trades away MAGMA's best property (simplicity) for a
problem we don't have.

**The switch trigger is a measurement, not a hunch — and a parallel agent is taking that
measurement.** Frame the perf test's output against these thresholds. Cross any one of them on a
corpus we cannot bound smaller (i.e. the partition model in primer §7 has run out of room), and it's
time:

1. **Write-path refresh latency becomes user-visible.** `decision-ops` rebuilds a corpus partition
   synchronously on write (primer §6, "refresh on write"). If that rebuild crosses roughly **1–2 s**
   on the largest realistic corpus, the interactive write loop degrades. This is the *first* wall we'd
   hit, because it's on the hot path, and it's the one to watch most closely in the perf test.
2. **Cold build / load time for a query session** climbs past a few seconds — the on-demand
   `find`/`traverse` stops feeling instant.
3. **Per-partition memory footprint** becomes a problem when several corpora are resident at once
   (the whole queried corpus is in RAM, no lazy loading — primer §7). Watch resident-set growth as
   corpus count and size scale together.
4. **A single corpus that genuinely cannot be partitioned smaller** crosses into the
   **~tens-of-thousands-of-nodes** range where full-rebuild + in-RAM BFS stop being "instant." The
   partition model defers this; an unbounded single corpus is the real risk the primer names.

**Match the fix to which wall you hit — don't over-buy:**
- Wall **1 or 2 alone** (rebuild/build latency), corpus still RAM-sized → first reach for
  **incremental indexing** (don't rebuild a whole partition per edit) *inside the existing engine*.
  That may retire the problem with **no backend at all** and should be tried before any DB. This is
  the cheapest fix and the primer explicitly names it as option one.
- Wall **3 alone** (memory, not traversal speed) → **SQLite + recursive CTEs** (§3): get the index
  off the heap and onto disk with zero new dependencies, preserving the stdlib-only ethos.
- Wall **1/2/4 together, or traversal latency itself** (deep/wide walks getting slow, corpus
  genuinely large) → this is the real "we've outgrown in-memory" signal. Adopt **LadybugDB**: it is
  the only option that raises *both* the memory ceiling (disk-backed columnar) *and* the traversal
  ceiling (purpose-built graph joins), with a Cypher query model that translates our BFS almost
  line-for-line.
- **If the scaling decision lands together with the §8 aggregation decision** (and given the ticket
  is `[DEC-MAGMA-AGGREGATION-AND-SCALING]`, it likely does) → adopt **DuckDB + DuckPGQ** instead, and
  get graph traversal *and* native count/sum/group-by in one embedded engine, retiring the separate
  SQLite-for-rollups store the primer describes. Accept DuckPGQ's research-stage maturity as the cost
  of that consolidation, with recursive-CTE-in-DuckDB as the in-engine fallback if PGQ isn't ready.

**Whatever we pick, the migration is bounded and reversible.** The `.lts` files stay the source of
truth, the FactLattice parser stays, the content-hash freshness invariant stays, and the four MCP
tools keep their `tool(corpus,args)->dict` signatures. Only the store+traverse layer changes
(~500–800 LOC), and because the index is a rebuildable projection of git-tracked text, a backend that
disappoints can be ripped out and replaced without data loss or a migration script. That reversibility
is *why* it's safe to wait for the measurement instead of betting early — and it's the same
`[MAGMA-DERIVED-PROJECTION]` principle that makes a stale index a cheap rebuild rather than a
correctness emergency.

---

## 9. One-line recommendation

Stay in-memory until the perf test trips a threshold in §8; when it does, adopt **LadybugDB** (the
maintained Kùzu lineage) as the graph backend — unless the aggregation decision is being made in the
same breath, in which case adopt **DuckDB + DuckPGQ** to solve scaling and the §8 aggregation gap in
one embedded, MIT-licensed engine. Reach for incremental-indexing-in-place or stdlib SQLite first if
the wall is merely rebuild-latency or memory rather than traversal speed. Never a server DB — that
solves a multi-tenant problem composeio deliberately doesn't have.

---

### Sources (current as of 2026-07)
- Kùzu archival (Apple acqui-hire, Oct 2025) and the fork landscape: [The Data Quarry — From Kuzu to Ladybug](https://thedataquarry.com/blog/from-kuzu-to-ladybug/); [gdotv — Kuzu's Legacy](https://gdotv.com/blog/kuzu-legacy-embedded-graph-database-landscape/); [dbdb.io — Kuzu](https://dbdb.io/db/kuzu)
- LadybugDB: [dbdb.io — LadybugDB](https://dbdb.io/db/ladybugdb); [Ladybug blog](https://blog.ladybugdb.com/)
- Kùzu package/license: [kuzu on PyPI](https://pypi.org/project/kuzu/); [kuzudb/kuzu (archived)](https://github.com/kuzudb/kuzu)
- DuckDB graph queries + DuckPGQ: [DuckDB — Graph Queries guide](https://duckdb.org/docs/current/guides/sql_features/graph_queries); [duckpgq community extension](https://duckdb.org/community_extensions/extensions/duckpgq); [DuckPGQ project](https://duckpgq.org/)
- CozoDB status + mnestic fork: [cozodb/cozo](https://github.com/cozodb/cozo); [dbdb.io — CozoDB](https://dbdb.io/db/cozodb); [mnestic](https://www.mnesticdb.com/)
