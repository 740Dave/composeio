---
domain: PLT.CUX
confidentiality: internal-only
artifact-readiness: system-design
created: 2026-07-20
audience: a Sonnet builder subagent
author: ComposeUX architect (guidelines for David's deliverable-sweep concept)
---
# Guidelines: the Deliverable Sweep + Ledger (spec in .lts, then implement)

> David's concept, architect-shaped into a build brief. Deliver in TWO passes: **(A) spec it in `.lts` first**
> (reviewable before code), **(B) implement the deterministic python app + a selftest.** The design north star
> is the perceive/act split: the app does what a script CAN compute; the reasoning layer (the Intelligence)
> does only what it can't. Build it schema-TOLERANT — the deliverable node schema is still converging (see
> "Schema in flux" below).

## The idea in one line

A deterministic python app sweeps one or more **project roots** for **non-code artifacts** (deliverables),
maintains a **deliverable-ledger `.lts`**, and reports the **delta** since the last run — NEW, MODIFIED, and
REMOVED artifacts — so the Intelligence can (at bootstrap) report changes to David and (at session-end) enrich
the new/changed entries with the semantic fields the app cannot produce.

## The perceive/act split (the load-bearing principle — do not blur it)

- **The app OWNS (mechanical, deterministic):** walking the roots per filters; per-artifact `path`,
  `project-root`, `format` (by extension), `created` (first-seen), `modified` (mtime/content-hash change),
  a content hash; the DIFF against the ledger (new/modified/removed); the run-history + counts.
- **The app NEVER produces (semantic — leave for the Intelligence):** `kind`, `topics`, `summary`,
  `target-audience`, `confidentiality`, `review-outcome`, `lifecycle-position`. For a NEW artifact the app
  writes the mechanical fields and stamps the semantic ones `NEEDS-ENRICHMENT` (a marker the Intelligence
  finds and fills). It must never GUESS them. This is Problem 1: the cheapest node fully owns the mechanical
  check; the reasoning layer does only the residue.

## Reuse — do NOT reinvent

Study and mirror these siblings (same repo, same idioms — the walk, the config, the jsonl/git reads, the
`--selftest` convention, the CLI `python -m composeio.<mod>` shape):
- `python/composeio/session_sweep/` — the loose-ends sweep. The deliverable-sweep is its **deliverable-focused
  sibling**; sit alongside it (`python/composeio/deliverable_sweep/`), reuse its config/walk patterns. Note the
  distinction in your spec: session_sweep = undone WORK; deliverable_sweep = non-code ARTIFACTS. Complementary.
- `python/composeio/compact_summary.py` — a deterministic extractor with `--selftest` + a clean CLI; the
  quality bar and confound-guard discipline to match (there, matching a STRING vs the authoritative field —
  here, don't misclassify a file by a loose rule).
- `intelligence/publisher/self_publisher.py` — the poll-loop daemon shape, if a watch/daemon mode is wanted
  (default is one-shot; a daemon mode is optional).

## Deliverable A — the `.lts` spec (author FIRST, get it reviewed)

Author a FactLattice `.lts` spec (a new spec in `ux_composer/specs/`, next free number; the architect will
confirm the id). Read `~/factlattice/EXEC-SUMMARY.md` then `SPEC.md` first; humans don't hand-edit `.lts`, you
compose them; record design choices with the `change`/`context_for_change` primitive (reasoning +
`invalidating_assumption`, not just the conclusion). The spec covers:

1. **The ledger `.lts` format ("one-stop-shopping", David's ask)** — a single file (propose
   `deliverables/deliverable-ledger.lts`, parallel to `decisions/`) holding:
   - a **config block** (frontmatter or a config node): `project-roots` (list); **positive filters** (include
     globs/dirs); **negative filters** (exclude globs); a **do-not-track** list (suffixes + specific
     files/dirs). Ship sensible defaults — exclude code (`*.py *.js *.ts *.cpp *.h *.mjs`), `node_modules/`,
     `.git/`, `__pycache__/`, `*.pyc`, build/generated dirs, secrets; include docs/reports/previs/mockups
     (`*.md *.html *.svg *.pdf` under docs/ and the like). The filters are the ONLY thing standing between a
     useful ledger and a ton of noise — make them clear and easy to tune.
   - the **`[DELIV-*]` nodes** — per `docs/factlattice/deliverable-node-kind-proposal.md` (the schema source of
     truth; the mechanical fields the app maintains, the semantic ones the Intelligence fills).
   - a **run-history** — a temporal event stream ([SPEC] event-stream layer): one entry per sweep run with a
     timestamp + `{new, modified, removed}` counts + the affected node ids (pointers). This is how a run's
     findings stay queryable after the fact.
   - a **findings summary** — the latest-run human-readable rollup.
2. **The sweep semantics** — the new/modified/removed rules; how `created`/`modified` are stamped; how a
   REMOVED artifact is handled (do NOT delete its node — it's a historical record; mark it
   `lifecycle-position: superseded` or a `missing: true` flag, and REPORT it; David explicitly wants removals
   surfaced).
3. **The two integration points** — bootstrap (harness runs the sweep, hands the Intelligence the delta incl.
   removals) and session-end (the sweep points the Intelligence at new/changed entries to enrich). Spec the
   report format the Intelligence consumes.

## Deliverable B — the implementation

`python/composeio/deliverable_sweep/` (package, `python -m composeio.deliverable_sweep`). Behavior:
- `sweep --ledger <path> [--root <dir> ...] [--write] [--json]`: walk the roots per filters; diff vs the
  ledger; emit a findings report (new/modified/removed, each with the node id / proposed id + path). `--write`
  updates the ledger (adds NEW nodes with mechanical fields + `NEEDS-ENRICHMENT` semantic markers; updates
  `modified` on changed; flags REMOVED); without `--write` it's a dry-run report (safe for bootstrap display).
- Deterministic: same tree + same ledger → same findings, byte-stable. No wall-clock in the diff logic; take
  timestamps from file mtimes / an injected "now", not scattered `time.time()` calls.
- Never writes a semantic field. Never deletes a node. Never touches a do-not-track path.
- `--selftest`: a synthetic temp tree — assert a NEW file is detected + gets mechanical fields +
  NEEDS-ENRICHMENT markers; a MODIFIED file bumps `modified`; a REMOVED file is flagged not deleted; a
  code file / do-not-track suffix is EXCLUDED; determinism (re-run = identical). Match `compact_summary.py`'s
  selftest rigor.
- A dry-run against THIS repo in your SUMMARY: show it correctly finds the real non-code artifacts (the
  ComposeUX primer/gifts, the console pre-vis `docs/intelligence-console/previs/*.html`, the reports under
  `docs/`, `decisions/*.lts`) and EXCLUDES the code — proving the filters are tuned right.

## Schema in flux (why schema-tolerant)

The deliverable node schema (`docs/factlattice/deliverable-node-kind-proposal.md`) is CONVERGING — it's headed
into the FactLattice base spec and David is still adding fields (target-audience, topics, confidentiality,
project-root, modified…). So: treat that doc as the schema source, keep the mechanical field set (path,
project-root, format, created, modified, hash) as your stable core, and pass through / don't hardcode-reject
unknown fields. If a field name there changes, the app should need a one-line map update, not a rewrite.

## Hygiene (every dispatch)

- Do NOT commit or push — the architect reviews + commits. Do NOT touch anything under `.claude/`.
- Do NOT edit `apps/minnow-bringup/minnow-bringup.compose.yaml` (unrelated uncommitted file).
- Secrets by reference only; the do-not-track defaults MUST exclude any secrets path.
- Your FINAL TEXT is the deliverable: SUMMARY with (a) the spec id + a changelog of files, (b) the selftest
  output, (c) the dry-run-against-this-repo result (what it found / excluded), (d) what it deliberately leaves
  to the Intelligence (the semantic fields), (e) anything unsure / needing the architect's or David's call.
