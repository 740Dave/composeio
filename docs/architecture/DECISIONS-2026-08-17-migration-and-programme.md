# Decisions — 2026-08-17 · the buildhost migration, the FactLattice programme, and the fleet

> Consolidates every open call from the 2026-08-16/17 session into one sitting. Sources:
> `MIGRATION-bus-master-to-buildhost.md` (draft 7, D-1…D-16), `PARSER-ARCHITECTURAL-REVIEW.md`,
> `PARSER-ENGINEERING-SPEC.md`, `PROCESS-smd-to-lts-porting.md`, and the Engine-Thrust / fleet thread.
>
> **Each decision carries my honest lean.** Overrule freely — where you did that last session
> (`re-derive-via`, the dark window, the relay carve-out) the result was better than my
> recommendation, twice measurably so.
>
> **How to answer:** fill the `Your call:` slot, or say *"recommendations accepted"* and I'll encode
> the lot. ⛔ marks a decision that BLOCKS work that is otherwise ready to run unattended.

---

## How to use this

Themes are ordered by what unblocks the most work. **Theme 1 is the whole reversible half of the
migration** — four answers there and P1 can run without you. Everything in Theme 2 is genuinely
harder and can wait. Themes 3–5 are independent of the migration and can be answered in any order.

⚠ Several decisions below were already answered *in substance* during the session and appear here
only because they were never written to the log. They are marked **[confirming]** — a nod is enough.

---

# Theme 1 — ⛔ Unblocking P1 (storage). The reversible half.

*Nothing in P1 stops a service, nothing is destructive, and rollback is under 60 seconds. These four
answers let it run unattended.*

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

### D3 · ⛔ The authority boundary — what is Haven's, what is maker's?

**Why it matters** — because mqtt-broker stays up, you will permanently run **two** storage
tiers. Measured: **no replication exists** between them (`grep` for `replicat|sync|mirror|peer_url|
federat` → zero matches), and `qualia-004df8.compose.yaml` is **already divergent**. Without a
declared owner per content class, they drift silently and "which one is right?" has no answer.

| Option | Tradeoffs |
|---|---|
| **Declare it before P1's reconciliation** | Reconciliation becomes mechanical instead of a judgment call per file |
| Decide it later | Every future divergence is re-litigated from scratch |
| Try to keep them in sync | No mechanism exists; you would be building one |

**My recommendation** — **declare it now**: mqtt-broker authoritative for Haven, buildhost for
maker/fleet, and nothing shared. ⚠ This one wants a short discussion if the split isn't that clean.

**Your call: _______**

---

### D4 · Which code lands on buildhost — and on mqtt-broker? **[confirming]**

**Why it matters** — ⛔ measured: buildhost's `bus_storage` has **zero** containment guards;
mqtt-broker has one partial. The traversal fix landed on main 2026-08-16 and is **deployed
nowhere**. Because mqtt-broker now stays up permanently, it keeps the hole permanently unless
you patch **both**.

**My recommendation** — `origin/main` (`b7c9616` or later) on **both** hosts. Migrating a vulnerable
service and calling it done would launder the defect.

**Your call: _______**

---

# Theme 2 — The broker half, and the fleet

*Genuinely harder. Needs a bench session and physical access.*

### D5 · ⛔ Rendezvous — R2+R3, or R1-revived?

**What this is** — how a device finds the bus. Measured: the broker URI, `BUS_STORAGE_URL` and
`DOCUMENT_STORE_URL` are **all three compile-time `#define`s** in one gitignored header, consumed
with **no override on any path**. mDNS was deliberately removed. **There is no OTA anywhere in the
firmware** — every reflash is physical.

| Option | Tradeoffs |
|---|---|
| **R2+R3: bake a name + NVS override; reflash 8 devices** | Physical USB, one bench session. Fixes the class permanently and survives the VLAN30/35 split |
| R1-revived: give buildhost `192.0.2.75` | **Zero reflashes** — measured viable (Haven uses names, 947 refs vs 2 IP literals). But it deletes the dark-window instrument and leaves the baked-address defect armed |

**My recommendation** — **R2+R3 as the plan, R1-revived as the ROLLBACK.** ⭐ The rollback's
existence is what makes attempting the reflash safe — if the campaign stalls, assigning the IP
restores every un-migrated device instantly. It is not a one-way door.

**Your call: _______**

---

### D6 · The retained store — copy or start clean?

**Why it matters** — it is literally one file: `/var/lib/mosquitto/mosquitto.db`, **207,813 bytes**
on mqtt-broker vs **47 bytes** on buildhost. ⛔ And there is a **retained, truthy restart command
for `wsc6-07f199` on the broker right now** — copying the store carries a live reboot trigger to the
new host, where a reconnecting device receives it outside the boot-window guard.

**My recommendation** — **start clean**, then manually re-publish the ~11 console preferences.

**Your call: _______**

---

### D7 · The rpi5 bundled touch **[confirming]**

**Why it matters** — rpi5 is a third participant nobody had counted: the CAN↔MQTT bridge **and** the
carousel kiosk, both pointing at mqtt-broker. Repoint is one env line each. Two ratified-but-
undeployed decisions already target that host, one of which says *"bundle with the next planned rpi5
touch"* — this is that touch.

**My recommendation** — **approve as one visit**, CAN first, ending in a **cold reboot as the
acceptance gate** (a restart verifies the running config; only a boot verifies the persisted one, and
both `can0.service` and the kiosk's `wayfire.ini` autostart are boot-path constructs).

**Your call: _______**

---

### D8 · Correct the stale SJW record

`[DEC-WIDEN-THE-PI-SJW]` records `sjw 1` as *"unrecoverable without re-measuring."* The host was
re-measured and shows **`sjw 2`**. Target `sjw 4` is unaffected.

**My recommendation** — amend the node. A wrong recorded value is worse than a missing one.

**Your call: _______**

---

### D9 · When does the dark window close?

**Why it matters** — you named yourself as the party who will push to reopen. ⛔ **Restoring
mqtt-broker does not degrade the oversight list, it ERASES it** — a dark device becomes
indistinguishable from a migrated one the instant the old host answers.

**My recommendation** — close on the **pre-declared checklist** (every censused participant seen in
buildhost's fetch log; powered-off devices reconciled, not assumed), never on impatience. If you close
early anyway, **name the unresolved device in writing first** — afterwards it is undetectable.

**Your call: _______**

---

# Theme 3 — The FactLattice programme

### D10 · ⛔ Proceed to Era 2 (migrate + stamp + flip strict)?

Era 0 (detection) and Era 1 (ratification) are both landed on the private trunk. **Era 2 is where
documents actually get stamped `lts: "1.1"` and strict is flipped** — until then every corpus stays
in legacy mode. It also carries your committed tail-cleanup (2,655 hapax lines → `x-`, mechanical,
zero data loss) and the `re-derive-via` 105-line target repair.

**My recommendation** — **yes, proceed**, one corpus at a time, `intelligence-console` first.

**Your call: _______**

---

### D11 · `_category` — the leading underscore is illegal

Era 1 flagged it: `_category` starts with `_`, which is **outside the key grammar A2 just closed** —
the identical objection that decided `x-` over `_` for private keys. My proposal walked into it.

| Option | Tradeoffs |
|---|---|
| **`x-category`** | Consistent with the private-key marker you already ratified |
| `category.lts` (no prefix) | Reads oddly as a folder-level file |
| Amend the grammar to allow leading `_` | Reopens a decision you just closed |

**My recommendation** — **`x-category`.**

**Your call: _______**

---

### D12 · Publish the FactLattice work to the public repo?

⛔ **This is the one authority I am NOT assuming**, despite your grant. Managing the git tree is an
SCM decision; pushing to `740Dave/factlattice` is a **publication** decision — irreversible, and the
audience is the world. Era 0, Era 1, the agent definitions and the two review documents are all on
the private trunk and ready.

**My recommendation** — **hold until Era 2 lands.** Publishing a half-ratified format spec invites
people to build on a version you are about to change.

**Your call: _______**

---

# Theme 4 — Fleet and assets

### D13 · ⛔ Publish COMMP **and** Tiny5 to `bus://assets/fonts/`

⚠ **You asked for this on 2026-08-16 and I never dispatched it.** Verified still true: bus_storage's
`fonts/` holds only IBM Plex TTFs. A deployed surface with no repo checkout gets the wrong face.

**My recommendation** — **dispatch now.** It is small and it closes a request I dropped.

**Your call: _______**

---

### D14 · The COMMP licence

COMMP ships with **no licence file**, unlike Plex and Tiny5. Matters before any OSS release.

**My recommendation** — resolve provenance before publishing anything that bundles it.

**Your call: _______**

---

### D15 · Engine Thrust: move the device readouts to `font_size: 20`?

On the device, 18 is a **1.125× nearest-neighbour upscale** of the COMMP 16 atlas — uneven stems on a
pixel font. `font_size: 20` hits the COMMP 20 atlas at exactly 1.0. You said it looks great, so this
is polish.

**My recommendation** — take it; it is free. Also: `wiring` and `scale_hint` are still bare — say the
word and they match too.

**Your call: _______**

---

# Theme 5 — Repo and infrastructure

### D16 · The 66 branches I just pushed — land or prune?

⛔ **107 commits existed on one disk when you woke up; all are now on `origin`.** But **77 local
branches** with 66 newly pushed is not a healthy trunk, and `[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST]`
says a branch is where work hides while looking done. The live checkout alone holds **14 unlanded
commits** including the console fix, the font fix and the P14 dusk gift.

**My recommendation** — dispatch `git-expert` to triage: land what is real, prune what is already
upstream by content, and report anything ambiguous rather than guessing. **This is the one I would
start today.**

**Your call: _______**

---

### D17 · Restart the Intelligence Console?

It has been down since ~19h uptime, killed by a pre-compact background task. Restarting now picks up
the repaired engine path — but also re-seats `decision_ops`/`defect_ops`, and `daemon_watch` reports
a **duplicate `decision_ops_mcp`** already.

**My recommendation** — reconnect the two MCP servers deliberately first (`/mcp reconnect
decision-ops`, then `defect-ops`), then start the console clean.

**Your call: _______**

---

### D18 · 40 worktrees, 29 under `/private/tmp`

A reboot clearing `/private/tmp` destroys 29 worktrees. Their **commits** are now safe on origin, but
uncommitted work in them is not.

**My recommendation** — have `git-expert` sweep for uncommitted content, then prune. ⚠ `wt-ctl` is
fixture-harvest material — do not delete it before the harvest is built.

**Your call: _______**

---

## Summary at a glance

| # | Decision | My lean |
|---|---|---|
| D1 | Instance identity in `/api/v1/status` | ⛔ **Yes, before P1** |
| D2 | Turbot images | **Relocate** |
| D3 | Authority boundary | **Declare before P1** |
| D4 | Which code on both hosts | **origin/main on BOTH** |
| D5 | Rendezvous | **R2+R3 plan, R1 rollback** |
| D6 | Retained store | **Start clean** |
| D7 | rpi5 bundled touch | **Approve, cold-boot gate** |
| D8 | Stale SJW record | **Amend** |
| D9 | Window close criterion | **Checklist, not impatience** |
| D10 | Era 2 | **Proceed, one corpus at a time** |
| D11 | `_category` spelling | **`x-category`** |
| D12 | Publish to public factlattice | **Hold until Era 2** |
| D13 | COMMP + Tiny5 to the bus | ⛔ **Dispatch now (I dropped it)** |
| D14 | COMMP licence | **Resolve before OSS** |
| D15 | Device font_size 20 | **Take it** |
| D16 | Branch triage | **Dispatch git-expert today** |
| D17 | Console restart | **Reconnect MCP first** |
| D18 | Worktree prune | **Sweep, then prune; keep `wt-ctl`** |
