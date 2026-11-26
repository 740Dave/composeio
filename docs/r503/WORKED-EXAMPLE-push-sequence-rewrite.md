# Worked example — `[CXC-FPBUS-PUSH-SEQUENCE]`, narrative → structure

This is the transformation the rewrite is asking for, done once, on a real node from
`corpus/73-fingerprint-bus-contract.lts`. It is illustrative, not normative: **verify every edge verb and
property key against the normative FactLattice spec and the domain profile before copying the shape.**

The point of this example is the third section — **where the narrative goes.** Deleting prose is easy;
the hard part is that most of it carries real content and needs a structured home.

---

## BEFORE — one node, 13 properties, 3,393 bytes

`lts_flow` on it renders **one box and zero arrows** (measured). The sequence is invisible to the engine.

```
### [CXC-FPBUS-PUSH-SEQUENCE] ⛔ THE ORDER IS EMPTY, THEN WRITE, THEN STAMP — and the stamp is LAST or
                              the device testifies to a roster it does not hold

- claim: ⛔ a push is THREE ordered phases: publish the desired set; empty then write every template;
  write the roster stamp LAST, after the final template write has returned success.
- the-stamp-is-written-after-the-last-write-and-never-inside-the-loop: ⛔ the stamp is the device's
  TESTIMONY about which roster it believes it holds ([CXC-FPID-ROSTER-STAMP]). A stamp advanced
  mid-push means a device interrupted at template 12 of 20 testifies to a roster it half-holds — ⛔ and
  [CXC-FPHAL-CONTAINMENT-INVARIANTS] inv-6 forbids exactly that … TELL — a stamp write anywhere inside
  a per-template loop …
- empty-comes-first-because-the-set-is-desired-state: ⛔ delete all, then add the declared set — David's
  own framing at [CXC-FPID-ROSTER-SYNC-MODE] `david-named-two-modes-and-one-was-demoted` …
- the-window-this-opens-and-its-honest-cost: ⚠ between `empty` and the last write the device holds NO
  ONE … ⭐ THAT IS WHY [CXC-FPID-FULL-RESYNC-COST-DERIVED] `the-operational-rule` says BETWEEN ROUNDS,
  NEVER MID-SCENE, and this node is where that rule becomes a sequencing constraint rather than advice.
- empty-must-clear-the-stamp-in-the-same-operation: …
- a-failure-at-any-phase-aborts-the-push-for-that-device-only: …
- the-push-is-idempotent-and-re-running-it-is-the-normal-rhythm: …
- acceptance-machine: … / acceptance-human: … / evidence-class: … / artifact: … / part-of: … / see-also: …
```

**Four defects, all visible above.** A two-actor sequence as a semicolon list; **no property says which
component performs which phase**; six keys that are sentences rather than field names; and connective glue
(*"THAT IS WHY … and this node is where that rule becomes …"*) restating an edge the graph already has.

---

## AFTER — a container plus one node per step

Ownership becomes structural. `lts_flow` walks it. Each step binds to code.

```
### [CXC-FPBUS-PUSH-SEQUENCE] Roster push — the ordered sequence

- definition: A roster push moves the desired template set to one device in four ordered steps. Two
  actors alternate: the service drives, the device executes.
- constraint: The roster stamp is written last, after the final template write returns success.
- constraint: A failure at any step aborts the push for that device only. The service continues with
  the rest of the fleet.
- constraint: Run between rounds, never mid-scene. From step 2 until step 4 the device holds no
  templates and authenticates nobody, for roughly the resync duration.
- rationale: The stamp is the device's testimony about which roster it holds. A stamp advanced mid-push
  means a device interrupted at template 12 of 20 testifies to a roster it half-holds.
- idempotent: Yes. A full resync at every game start is the normal rhythm, not an edge case. A device
  that missed one converges at the next.
- acceptance-machine: Cut power at template 12 of 20; the stamp is absent or at its previous value and
  never at the new roster id. A clean push ends with the stamp at the new roster id and occupancy equal
  to the declared slot set.
- acceptance-human: An operator who interrupts a push and inspects the fleet view sees that device
  reported as NOT CONVERGED, in words, without reading a log.
- artifact: services/fingerprint_storage/roster_push.py
- part-of: [SPEC:CXC-FPBUS]
- see-also: [CXC-FPID-ROSTER-STAMP], [CXC-FPID-ROSTER-SYNC-MODE], [CXC-FPID-PUSH-TRIGGER],
  [CXC-FPHAL-CONTAINMENT-INVARIANTS], [CXC-FPID-FULL-RESYNC-COST-DERIVED]

#### [CXC-FPBUS-PUSH-STEP-DECLARE] Step 1 — publish the desired set

- actor: fingerprint_storage
- action: Publish the desired set for this device: roster_id, entry_count, roster_sha256.
- on-failure: Abort for this device. The stamp is unchanged.
- part-of: [CXC-FPBUS-PUSH-SEQUENCE]

#### [CXC-FPBUS-PUSH-STEP-EMPTY] Step 2 — empty the device

- actor: device
- action: Delete every template and clear the roster stamp, in one operation.
- rationale: The sync is desired-state. A push that only adds leaves a departed player resident, which
  is the silent drift the model exists to remove.
- constraint: The stamp must clear in the same operation as the delete.
- rationale-for-constraint: A device emptied and then interrupted otherwise testifies to the old roster
  while holding nothing — confidently wrong rather than merely stale, which is the worse state.
- tell: An empty handler with no stamp write.
- on-failure: Abort for this device. The stamp is absent.
- part-of: [CXC-FPBUS-PUSH-SEQUENCE]

#### [CXC-FPBUS-PUSH-STEP-WRITE] Step 3 — write each template

- actor: device
- action: Write each template in the declared set to its declared slot, one per message.
- on-failure: Abort for this device. The stamp remains absent, so the device reports NOT CONVERGED.
- part-of: [CXC-FPBUS-PUSH-SEQUENCE]

#### [CXC-FPBUS-PUSH-STEP-STAMP] Step 4 — write the roster stamp

- actor: device
- action: Write the roster stamp, after the final template write has returned success.
- constraint: Never inside the per-template loop. This must be the last statement of the push.
- tell: A stamp write anywhere inside a per-template loop, or a stamp write that is not the last
  statement of the push.
- enforces: [CXC-FPHAL-CONTAINMENT-INVARIANTS] inv-6 — a partial operation never advances a roster stamp.
- part-of: [CXC-FPBUS-PUSH-SEQUENCE]
```

Plus the sequence edges in the document's `## Relationships` section so `lts_flow` can walk them.
⚠ **Confirm the correct verb from the normative spec** — `lts_flow` walks BEFORE / SEQUENCE / CAUSES /
PRECONDITION_FOR / ENABLES / CASCADES_TO. Do not assume; check, then verify by running `lts_flow` and
confirming it renders four boxes in order rather than one.

---

## WHERE THE NARRATIVE WENT — this is the part worth studying

⛔ **Almost nothing was deleted for being narrative. It was RELOCATED into a field that names what it is.**

| before — a key that was a sentence | after — a named field, and where |
|---|---|
| `the-stamp-is-written-after-the-last-write-and-never-inside-the-loop` | `constraint` on the container + `constraint` and `tell` on step 4 |
| `empty-comes-first-because-the-set-is-desired-state` | `rationale` on step 2 |
| `empty-must-clear-the-stamp-in-the-same-operation` | `constraint` + `rationale-for-constraint` + `tell` on step 2 |
| `a-failure-at-any-phase-aborts-the-push-for-that-device-only` | `on-failure` on **every** step + one `constraint` on the container |
| `the-window-this-opens-and-its-honest-cost` | `constraint` on the container — the between-rounds rule, stated as a rule |
| `the-push-is-idempotent-and-re-running-it-is-the-normal-rhythm` | `idempotent` on the container |
| the citation of inv-6 buried mid-paragraph | `enforces` on step 4 — an edge, so the graph sees it |

**The three things that were genuinely deleted:**

1. **The connective glue.** *"THAT IS WHY [X] `the-operational-rule` says BETWEEN ROUNDS, NEVER MID-SCENE,
   and this node is where that rule becomes a sequencing constraint rather than advice."* The rule survives
   as a `constraint`. The sentence explaining that a rule is now a constraint does not: the graph carries
   the relationship as an edge and the reader does not need to be told what kind of statement they are reading.
2. **Restated citations.** Where the body re-explained what a cited node says, the citation stands alone.
3. **Every glyph.** Where one carried meaning it became a word or a field — `constraint`, `tell`, `on-failure`.

**The two rules that fall out of this, and generalise past this node:**

- **A key NAMES A FIELD; the value carries the content.** If the key is a sentence, its content belongs in
  a value under a field name that already exists elsewhere in the document.
- **If a sentence explains a relationship between two nodes, it is probably an EDGE.** `enforces`,
  `part-of`, `see-also` say it once, structurally, where the graph can see it.

---

## The measurement

| | before | after (this node) |
|---|---|---|
| nodes | 1 | 5 |
| properties | 13 | 32 |
| bytes | 3,393 | ~2,900 |
| distinct keys | 13, of which 6 are sentences | 12, all reused field names |
| `lts_flow` | 1 box, 0 edges | 5 boxes, 4 ordered edges |
| ownership | inferred by the reader | `actor` on every step |

⭐ **More nodes and more properties, fewer bytes, and a document a builder can execute rather than parse.**
Note that property COUNT went up while narrative went down — the count is not the problem, the sentence-shaped
keys were.
