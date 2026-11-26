# Decisions — 2026-08-17 · VERBATIM EXCERPT (regression fixture)

> ⛔ DO NOT EDIT THE DECISION BLOCKS BELOW. They are a BYTE-FOR-BYTE excerpt (D1 and D2) of
> `docs/architecture/DECISIONS-2026-08-17-migration-and-programme.md` — THE artefact that parsed,
> rendered and was DELIVERED to the human while being undecidable, which is why
> [CXC-DSDV-CONTEXT-GUARD] exists (David 2026-08-17).
>
> D1 is complete. **D2 — "The Turbot images — keep, relocate, or discard?" — has NO `What this is`
> line at all**, so the reader cannot tell whether "images" means files on buildhost, Docker images, or
> something ComposeIO-specific. (They are dated OS ISOs parked in buildhost's asset tree; the document
> never says so.) The guard MUST name D2. This fixture is checked in so that regression is pinned
> even when the original working document is not on the checkout.

---

# Theme 1 — ⛔ Unblocking P1 (storage). The reversible half.

### D1 · ⛔ Instance identity — add `hostname` + git SHA to `/api/v1/status`

**What this is** — `bus_storage` and `document_store` each expose a `/api/v1/status` endpoint. It is
the only machine-readable way to ask a running service *"who are you?"*

**Why it matters** — measured: both hosts return **identical** `service`, `version`, `data_dir`, and
`version: 0.1.0` is reported by **three builds with three different sha256 hashes**. A version field
that cannot distinguish three known-different builds is not an instrument. Every migration
verification is weaker without this, and one check (V14) cannot run at all.

| Option | Tradeoffs |
|---|---|
| **Add `hostname` + git SHA before P1** | ~5 lines, no protocol change. Makes every later check meaningful |
| Add it after the migration | The migration is exactly when you most need to tell the two apart |
| Don't bother | You cannot prove which instance answered you, ever |

**My recommendation** — **before P1.** It is the cheapest high-value item in the whole programme.

**Your call: _______**

---

### D2 · ⛔ The Turbot images — keep, relocate, or discard?

**Why it matters** — buildhost holds 4 files (~2.4 GB) that exist nowhere else. You ruled its *service
data* stale; the agent pushed back that **dated OS ISOs with `.sha256` siblings are immutable by
design, not stale** — and they are the only bytes on either host this repo cannot regenerate. This is
**the only place `--delete` appears in the plan.**

| Option | Tradeoffs |
|---|---|
| **Relocate to `/var/lib/turbot-images/`, then reconcile `assets/` cleanly** | Keeps them, removes them from what bus_storage serves, makes the asset tree a faithful mirror |
| Keep in place | `assets/` stays a mix of served content and parked artefacts; reconciliation stays fuzzy forever |
| Discard | Irreversible, and they are genuinely unregenerable |

**My recommendation** — **relocate.** Provisioning artefacts parked in the wrong bucket.

**Your call: _______**

---
