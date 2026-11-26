# MIGRATION — Storage consolidation → Buildhost, and the device-baseline fleet cutover

**Project migration plan. Authored 2026-09-03 (composeio-hardware-architect, session 21), at the pre-`/compact` boundary.**
Durable, project-level. The session-successor continuity record is separate: `~/session_handover/composeio_hardware_architect/session_number_21/HANDOVER.md`.

---

## ⛔ SCOPE — this is the STORAGE/DATA migration, NOT the ComposeServer deployment
What is live on buildhost is the standalone **`bus_storage` service** (a bare-Python systemd daemon, `composeio-bus-storage.service`, `:8420`) carrying the merged/consolidated data — the *storage layer*, one service. **ComposeServer** (the microkernel host + per-tenant plugin framework + MCP surface + the other services as plugins) is **NOT deployed to buildhost** — that is task #7, `in_progress` (built+landed on main, never run on buildhost). `DEC-BUILDHOST-HOST-STORAGE-AS-PLUGINS-DAY-ONE` intended storage to run *as a ComposeServer plugin*; the cutover instead deployed it *standalone* (fastest path to land the merged data + the security fix). Converting the running standalone store into a ComposeServer-hosted plugin is part of #7, not this migration. Do not read "merged store live on buildhost" as "ComposeServer deployed."

## The end state we are migrating to

1. **One store, merged, on Buildhost.** `bus_storage` is the single store with a single `bus://` face; `document_store` is dead. The merged/consolidated maker data lives on **buildhost.local (.21)**; the maker ComposeServer does **not** run on mqtt-broker.local (.75). Haven (KB + LoRa) stays on .75. *(David ruling, `DEC-STORAGE-MERGED-DATA-ON-BUILDHOST-2026-09-02`.)*
2. **The fleet points at Buildhost, and `.75` is retired.**
3. **Config-over-bus going forward.** After one comprehensive flash pass, device updates are a YAML edit in bus_storage + a device auto-reload — **no USB-tethered reflash** (OTA intentionally deferred). *(`[SPEC:CXC-DEVICE-BASELINE]`, landed + ratified, corpora `589a7b8`.)*

## Where we are NOW (done + proven this session)

- **Storage consolidation: code `ad79123` + security `0f2e07d` on `origin/main`** (private `740Dave/composeio-internal`); spec `[SPEC:CXC-STORE]` + decision graph + provenance all landed/pushed.
- **Cutover to the store: DONE.** The merged store is **LIVE on `buildhost.local:8420` @ `0f2e07d`**, reconcile applied per David's rulings (qualia→.75, fleet_devices→buildhost, 4 Turbot images preserved, 46 docs identical), and the **blob-route deny-list is proven on the live face** (403 on `configs/` + `assets/fleet/private/`). Buildhost's stale pre-cutover data is archived + sha256-verified on two hosts.
- **`.75` retirement: HELD** — correctly. The device fleet is hardwired to `.75:8420` under heavy load; retiring it now would dark the fleet uncontrolled. Retirement is gated on the fleet repoint below.
- **`[SPEC:CXC-DEVICE-BASELINE]`: landed + ratified** (`589a7b8`) after an independent `end-design-session` audit (0 invented, 0 missing; 6 provenance corrections + 2 ambiguities, all resolved by David).

## The build — agentic phasing (scoped to agent capability, parallel where possible)

**Only one hard dependency gate exists** (scope-on-wire); everything else parallelizes.

### Phase A — pre-cutover engineering (DISPATCHED at this boundary; RETURNS in the successor's session)

Three parallel isolation:worktree agents, each returning a branch + `.qa/verdict.json` for the successor to **diamond (gap-analyzer → first-bringup → composeplatform-qa) → land via git-expert**:

1. **Scope-on-wire verify + corpus sync** *(factlattice-editor)* — confirm BP-CAN-ID v2 (`dd7e049`) reconciled ALL THREE wire sites (`bus_can.h` ✓, `transport_can.py` ✓, **`can_mqtt_bridge` — the unverified one**); true up the stale corpus (task #21) + resolve the spec precondition. ⛔ **If `can_mqtt_bridge` still speaks the old no-scope layout, the divergence is NOT closed and the mothercard membrane's prerequisite is UNMET — that is a HALT/real-gap, not a corpus lag.** This gates Phase-A-agent-3's relay/mothercard correctness.
2. **bus_storage server** *(composeio-expert)* — content-hash-on-GET (`ETag`/`X-Content-Hash`, so devices never compute sha256) + blob-route change-notify emit. No hardware.
3. **Firmware baseline** *(composeio-hardware-architect)* — the whole coherent firmware slice: `asset_reload` port to the display mains; device-side inbound-message buffering; UART/mothercard relay + scope-membrane + announce-bind L3 + transport-select runtime mode/pin config; the capability-gate predicate; and **rescue-floor→buildhost**. HOST-TESTED only — it returns a **hardware-bench worklist** for the empty evidence slots (relay-on-hardware, L3, bench-verify bus_binding), which is David's bench.

### Phase B — hardware bring-up (successor + David, at the bench)

Fill `[SPEC:CXC-DEVICE-BASELINE]`'s EMPTY evidence slots on real silicon: asset_reload self-select; the relay/mothercard membrane on two segments; announce-bind L3; transport-select role-switch with zero reflash; bus_binding repoint/confirm/**revert**; rescue-floor-is-buildhost. The Qualia (`qualia-004df8`, no OTA, not USB-tethered) needs planned physical access.

### Phase C — the human-in-the-loop cutover (David's design)

1. **Verify the fallback FIRST** — on a bench board, prove that a device set to buildhost **reverts to the original ComposeBus (`.75`) if buildhost's ComposeBus is bad** (the bus_binding trial/confirm/revert machinery), while `.75` is still the floor. This proves the safety mechanism.
2. **Bring `.75` down for the duration of the cutover.** All devices go dark — expected. Because every ESP32 is being flashed anyway (buildhost as the new compiled rescue-floor), and `.75` is gone, **no device can silently linger on `.75`; a mis-flashed board is visibly dark, not quietly wrong — 100% correct bringup by construction.**
3. **Flash the fleet, they boot fresh on buildhost**, the doc:// purge completes per-device, config-over-bus is live.
4. **Retire `.75`'s maker daemons** (Haven stays). Fill the storage spec's cutover evidence slots (`[CXC-STORE-EV-01..04]`, task #23).

## What lands AFTER `/compact`

- The **three Phase-A build agents** complete in the successor's session (task-notifications) → the successor **diamonds + lands** each. (Same pattern as this session's storage-consolidation build.)
- The **pre-`/compact` independent reviewer** (dispatched by `/prepare-for-compact`) may also resolve in the successor's session — read + remediate it; it read the raw transcript, it is not stale.

### ⛔ The parallel build is the ACID TEST of the git process (David, 2026-09-03)
Three isolation:worktree builds landing concurrently is precisely the multi-agent parallel-fanout that historically produced ~200 stray refs and day-long reconciliations (the session-20 arc). So the successor must **explicitly dispatch `git-expert` to assess repo health** before/around the three lands, and run `scripts/branch-hygiene.sh` **before and after** — expecting `stranded=0 / landed-unpruned=0 / origin-heads=1`. *"Your parallel build is the acid test to see if our git process changes and adoption of worktrunk solves the perennial problems that have plagued our work to date."* **If cruft accumulates or a land strands, the process did NOT hold under load and revising the procedures is REQUIRED** — surface it as a finding, do not absorb it.

## Open task ledger (see the live task list)
#19 storage cutover E/F (retire .75 — Phase C) · #21 scope-on-wire corpus sync (now Phase-A-1) · #22 land-gate.sh epistemics fix · #23 CXC-STORE cutover evidence slots · #24 fleet repoint bench (now Phase B/C) · #25 asset_reload port (now Phase-A-3) · #26 device-baseline spec (LANDED) · #17 USB device (HOLD, David).

## The invariants that must survive the crossing
- Push only to PRIVATE `740Dave/composeio-internal`; NEVER public `740Dave/factlattice`; corpora → `dropbox` remote only.
- Verify land/abandon by patch-id/content, NEVER diff-stat (the reorg makes diff-stat lie).
- `.75` STAYS UP until the fleet has demonstrably repointed (Phase C step 2 is the only sanctioned takedown, and only after the fallback is proven).
- machine-green ≠ glass-green: the firmware build is host-tested; the hardware slots are David's bench and stay EMPTY until real silicon fills them.
