# R503 fingerprint peripheral — design / audit / datasheet archive

This tree is the **design, audit, and datasheet archive** for the R503 optical
fingerprint peripheral. It is the preserved deliberation trail from the R503 design
effort (originally its own git repo, since retired). It is documentation and archived
reference material — **not** build code.

## Where the LIVE code lives (this is archive, that is the product)
- **Firmware:** `firmware/reactive_core` (the shipping firmware)
- **Service:** `services/fingerprint_storage`
- **Python peripheral:** `python/composeio/peripherals/fingerprint`
- **Spec corpus:** the `r503` FactLattice (`.lts`) corpus in the corpora home
  (`~/.claude/factlattice_corpora/composeio/`) — that is the source of truth for the
  spec; the `.lts` files under this archive are *candidate/deliberation* passes, not
  the registered corpus.

## What is here
- `DESIGN-r503-sensor-ux.md`, `DSD-r503-sensor-ux-audit.md`,
  `DSD-r503-sensor-ux-decisions.md` — the sensor-UX design + its audit + decisions.
- `SOURCES.md` — provenance ledger: URL + SHA-256 for every vendor datasheet.
- `datasheets/` — the 5 vendor PDF user-manuals (V1.1 … V1.4.1) plus
  `datasheets/extracted-text/` (pdftotext-layout dumps). The 18 MB official vendor
  ZIP was intentionally NOT archived — it is re-fetchable per `SOURCES.md`.
- Deliberation / audit trail: `DSD-spec-audit-*`, `R503-characterization-*`,
  `REVIEW-implementability-*`, `fleet-identity-design-*`, `BUILD-PLAN-*`,
  `BRIEF-c-pass-*`, `open-decisions/`, `hal-contract/`, `handover-*/`,
  `c-pass-{fable,opus5,sonnet5}/` (parallel candidate corpus passes by different
  models, kept for comparison), `impl-test/` (cross-implementation wire excerpts).
- `probe-archive/` — the host-side R503 probe tool (`r503probe.py`, `README.md`) and
  its negative-control `sessions/` logs.
- `history/README.md` — pointer to the retired repo's git-history bundle (parked
  durably outside this repo; see that file).

The ESP-IDF **probe** firmware (a bring-up spike, not the product) is archived at
`firmware/r503_probe/`.
