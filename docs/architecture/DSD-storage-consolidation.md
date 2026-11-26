<!-- dsd-status: answered 2026-09-03T03:50:21Z -->
# DSD — Storage Consolidation Spec: Audit Resolutions

> Consolidates the independent design-audit of `[SPEC:CXC-STORE]` (the bus_storage + document_store
> collapse into one store, one `bus://` face) into your decision surface. A context-free auditor
> diffed the spec against your verbatim words (transcript L30807 / L30835 / L30858 / L30878) in four
> directions — MISSING · ALTERED · INVENTED · AMBIGUOUS — and found **zero inventions**: five
> corrections (mine to apply on your ratify) and one genuine fork. Fill a slot to change a call, or
> reply "recommendations accepted" to take all six.

## How to use this
The recommendations are my honest lean, not a nudge — overrule any freely. **D1 is the only genuine
design fork**; D2–D6 are corrections I apply to the spec once you ratify. None blocks the others, and
none of this touches code (the spec build waits on your ratify + the now-lifted freeze).

## Audit resolutions

### D1 · Does "Proceed" ratify dropping the query engine, or keep it an open decision?
- **What this is:** `document_store` has a server-side query face (`query_docs(where=…, fields=…)`) that `bus://` (path-addressed bytes) lacks. The consolidation retires it; its one caller — the `DocPicker` widget, over a 46-doc store — would filter client-side instead, with the platform's existing `[CXC-BUS-QUERY]` bus-native responder available if server-side query is ever needed at scale.
- **Why it matters:** dropping the engine removes a second SQLite DB and a whole code path, but it is a real capability change — and your "Proceed" answered a *compound* question (author the spec + run the audit), so it's ambiguous whether it also ratified the query-drop. The spec honestly left `[DD-STORE-QUERY-DISPOSITION]` open rather than assume.

| Option | Tradeoffs |
|---|---|
| **A · Ratify the query-drop now** | simplest end-state; client-side filter is trivial at 46 docs and more decentralization-aligned; `[CXC-BUS-QUERY]` is the escape hatch if scale ever demands it |
| B · Keep `[DD-STORE-QUERY-DISPOSITION]` open | defers to an explicit later decision; the spec still builds without settling it |

- **My recommendation:** **A** — drop the engine. One widget over 46 docs; server-side query buys nothing at this scale, and the bus-query mechanism covers a future need without re-introducing a second storage interface.
**Your call:** A · Ratify the query-drop now

### D2 · YAML repoint (audit C1 — MISSING)
- **What this is:** you named three actions — move the data, **repoint any YAML**, kill doc_storage — but the spec's repoint surface covered only Python seams. A YAML census found real consumers: the `docstore_viewer` app, the launch-bar "Document Storage" tile, `doc://surfaces/launcher`, the minnow/turbot restore scenes, and the service's own `docker-compose.yml`.
- **Why it matters:** without these in the spec, a third of your instruction is silently dropped and those consumers break at cutover.

| Option | Tradeoffs |
|---|---|
| **A · Add the 5 YAML consumers to the spec's repoint surface (§3/§6)** | closes the gap; the cutover is complete |
| B · Handle YAML outside the spec | leaves it undocumented and easy to miss |

- **My recommendation:** **A** — I'll fold them in.
**Your call:** A · Add the 5 YAML consumers to the spec's repoint surface (§3/§6)

### D3 · Record the HTTP-face discrepancy (audit C2 — MISSING)
- **What this is:** you asked me to verify a document_store HTTP-face discrepancy (L30762). I did — the already-built plugin silently dropped the `:8421` HTTP face the design said it would keep. The spec never records the episode.
- **Why it matters:** it's plausibly moot now that document_store is killed outright, but the spec should *say* that rather than silently omit a verification you explicitly requested.

| Option | Tradeoffs |
|---|---|
| **A · Add one line: discrepancy noted, consolidation supersedes it** | honest trail |
| B · Omit as fully moot | loses the record of your requested verification |

- **My recommendation:** **A**.
**Your call:** B · Omit as fully moot
**Comments:** This is mooted because we're deprecating document_store - if we are not deprecating document_store, then we need to discuss.

### D4 · Fix the decision's provenance (audit C3 — ALTERED)
- **What this is:** the spec labeled the consolidation decision "architect-ruling," but it is substantially *yours* ("kill doc_storage altogether" L30807; "we'd be keeping bus://" L30858).
- **Why it matters:** provenance is the audit trail — mislabeling your ruling as an architect inference inverts who actually decided.

| Option | Tradeoffs |
|---|---|
| **A · Relabel `david-ruling` and cite L30807 / L30858** | correct attribution |
| B · Leave as architect-ruling | inaccurate on the record |

- **My recommendation:** **A**.
**Your call:** A · Relabel david-ruling and cite L30807 / L30858

### D5 · Separate your reason from the architect's framing (audit C4 — ALTERED, minor)
- **What this is:** the spec dressed your plain "not dual-interface" preference (L30858) in narrow-waist / N×M-fan-out framing you never used.
- **Why it matters:** low stakes — same conclusion — but the citation trail should keep *your* stated reason distinct from *my* added technical justification.

| Option | Tradeoffs |
|---|---|
| **A · Split "your reason" from "architect rationale" in the node** | honest citation |
| B · Leave fused | reads as though you invoked the framing |

- **My recommendation:** **A**.
**Your call:** A · Split "your reason" from "architect rationale" in the node

### D6 · Soften "the same primitive" (audit C5 — ALTERED, minor)
- **What this is:** your "far more similar than initially expected" (L30807) hardened into the spec's flat "the same primitive."
- **Why it matters:** minor, but it's the document's opening premise; the investigation does support "same primitive" — that's just *my* finding, not your claim.

| Option | Tradeoffs |
|---|---|
| **A · Attribute "same primitive" as the architect's finding from the evidence** | keeps the strong claim, correctly sourced |
| B · Soften to your comparative "far more similar than expected" | matches your exact words |

- **My recommendation:** **A** (attribute) — but **B** is entirely fine if you'd rather the premise read in your own framing.
**Your call:** A · Attribute "same primitive" as the architect's finding from the evidence

## Summary at a glance
- **D1** (the fork) — drop the query engine → **A** *(my lean)*
- **D2** — add the YAML consumers → **A**
- **D3** — record the HTTP-face discrepancy → **A**
- **D4** — relabel provenance `david-ruling` → **A**
- **D5** — split your reason from the architect framing → **A**
- **D6** — attribute "same primitive" as the architect's finding → **A**

Reply **"recommendations accepted"** to take all six, or fill any slot to change it. On your answers, D1 becomes a decision node (`dsd-source:` this doc) and D2–D6 get applied to `92-storage-consolidation.lts`.
