<!-- dsd-status: answered 2026-08-10T21:43:57Z -->
# Decision — the CAN arbitration-id layout

> One decision, but a wire format with hardware consequences, so it gets the full treatment.
> Raised by `[DEC-CAN-ARBITRATION-ID-LAYOUT-DIVERGENCE]`; discovered 2026-08-10 while de-forking the
> message-type wire codes. David asked for pro/con on each option before choosing.
>
> **Authored 2026-08-10** by the ComposeUX Architect.

## The situation

Two ComposeBus CAN implementations pack the 29-bit extended identifier differently:

```
spec [BP-CAN-ID]  (and services/can_mqtt_bridge/bridge.py:66)
  bit 28..13        12..9      8..0
  [ ns_hash 16 ]  [ type 4 ] [ src_hash 9 ]

python/composeio/transport_can.py  (after today's message-type fix)
  bit 28..25   24..23     22..7          6..0
  [ type 4 ] [ scope 2 ] [ ns_hash 16 ] [ seq 7 ]
```

Two nodes using these on one wire exchange **garbage** — each decodes the other's namespace hash from
the wrong bit range and routes to a namespace that does not exist. Silently: there is no checksum on a
CAN id, and a wrong-but-valid hash is indistinguishable from a real one.

**Three facts that shape the choice, all verified today rather than assumed:**

1. **`can_mqtt_bridge` is a NATIVE ComposeBus peer, not a foreign-CAN decoder.** Its own docstring:
   *"This bridge makes CAN and MQTT participants indistinguishable from the bus perspective."* It
   implements the spec layout exactly. So the two implementations are **required** to interoperate —
   that is the bridge's entire purpose, and David ratified MQTT↔CAN bridging as legitimate *when both
   are running*.
2. **The spec already has homes for `scope` and `seq` — in the DATA payload.** `[BP-CAN-DATA]` byte 0
   bits 7..6 carry scope (0=local, 1=global, 2=remote, 3=auto); bytes 1–3 carry seq. `CANTransport`
   moved both into the ID *and* omits the data flags/seq bytes entirely. So this is not "two designs";
   it is one design plus one deviation that also dropped fields.
3. **Hardware acceptance filtering works on the ID, not the payload.** On SJA1000-derived controllers
   (ESP32 TWAI), extended-frame acceptance filtering covers the 29-bit id; data-byte filtering exists
   only for *standard* frames. **⚠ Confirm against the ESP32 TWAI TRM before this is load-bearing** —
   it is the crux of David's argument and I have not verified it on silicon.

---

### D1 · Which 29-bit arbitration-id layout does ComposeBus CAN use?

**Why it matters.** It is a wire format. Once hardware exists it is expensive to change, and today the two
implementations cannot share a segment. Four options; (c) I no longer consider defensible, (d) is new.

#### Option (a) — conform `CANTransport` to the spec

`[ns_hash 16][type 4][src_hash 9]`, with scope and seq moving to the data bytes where the spec puts them.

| Pro | Con |
|---|---|
| The spec is the declared contract, and the bridge already honours it — one implementation changes, not two plus a document | `CANTransport` and its tests are rewritten; today's `[4b type][2b scope]` rebudget is thrown away |
| Restores `src_hash`, which the transport dropped entirely. Source identity enables loop prevention on a bridged segment, multi-master diagnostics, and "who published this" without burning a data byte | **Scope leaves the id, so it cannot be hardware-filtered** — the thing David explicitly wants |
| `ns_hash` in the high bits makes "accept exactly this namespace" a single-mask filter (`0x1FFF_E000`), which is the filter a leaf device actually wants most often | Requires implementing `[BP-CAN-DATA]` byte 0/1–3 properly, which `CANTransport` skips today — real work, not a field move |
| Uses the whole design: 8 data bytes stop being value-only and start carrying flags/seq/ttl as specified | Namespace determines arbitration priority — and CRC-16 hashes are effectively random, so priority becomes arbitrary-but-stable |

#### Option (b) — amend the spec to the transport's layout

`[type 4][scope 2][ns_hash 16][seq 7]`.

| Pro | Con |
|---|---|
| **Scope is hardware-filterable.** A gateway can accept only cross-segment traffic, and a leaf can ignore local chatter, without waking the CPU — the `[CXC-PS-CAN]` filter-at-the-bus capability, which is currently spec'd-not-built | The bridge changes *and* the spec changes — two artifacts instead of one, and the spec is the thing other tiers read |
| Already implemented and tested in `CANTransport` today | **Loses `src_hash` entirely.** No source identity on the id at all; loop prevention and "who sent this" need a data byte, which partly cancels the saving |
| `seq` on the id means a receiver can detect gaps without parsing the payload | `seq` shrinks to 7 bits (128) versus the spec's 3 data bytes, and today's fix already narrowed it from 512 to buy type bits — it is being squeezed twice |
| Message type in the high bits gives type-based arbitration priority, which is at least *comprehensible* | Only 2 bits for scope, but the spec defines **four** scope values and `auto` should never reach the wire — so the encoding needs a rule the spec does not currently state |
| | Type-in-high-bits means *every* `STATE` outranks *every* `EVENT` regardless of namespace importance — a control system probably does not want that |

#### Option (c) — declare them separate profiles that must never share a segment

| Pro | Con |
|---|---|
| Zero work today | **Contradicts the bridge's stated purpose** — it exists to make CAN and MQTT participants indistinguishable on one bus. Fact 1 above kills this. |
| | Nothing enforces it. The next engineer wires them together and gets silent garbage. |
| | It is documenting the bug rather than fixing it |

**I do not consider (c) defensible** now that the bridge's role is established. Including it for completeness.

#### Option (d) — the one neither of us listed: fix arbitration priority while we're here

`[priority 3][type 4][scope 2][ns_hash 16][seq 4]` or similar.

**Why this exists as an option:** `[BP-CAN-DATA]` byte 0 bits 5..4 define a 2-bit `priority` field —
**in the data payload, where it cannot affect arbitration at all.** CAN decides bus arbitration by
comparing identifier bits, so a priority field outside the id is inert by construction. Both (a) and (b)
leave that broken; they just differ in *which* accidental field ends up governing priority.

| Pro | Con |
|---|---|
| Makes CAN do the one thing CAN is uniquely good at — deterministic priority arbitration under contention | The largest change: transport, bridge, spec, and firmware constants |
| Keeps scope hardware-filterable (David's requirement) *and* gives deliberate priority | `seq` squeezed hardest, or dropped from the id to the data |
| Fixes a spec defect rather than ratifying around it | Highest risk of getting a wire format wrong while no hardware exists to falsify it |

---

#### My recommendation

**(b), with (d) recorded as the intended successor** — and one condition.

David's acceptance-mask argument is the deciding one and it is not a preference: filtering is the
capability that lets a $0.10-class node participate on a shared bus without a CPU budget for traffic it
does not care about. That is the same "cheapest node fully owns its behavior" principle the whole
platform rests on, expressed in silicon. Scope in the payload cannot deliver it.

**The condition:** (b) must not ship while `src_hash` simply vanishes. Losing source identity is a real
regression — the bridge needs it for loop prevention the moment two segments are joined. So (b) means
*"transport layout wins, and `src` moves to `[BP-CAN-DATA]` byte 0/1 with the spec updated to say so,"*
not *"delete the field."*

**Why not (a)** despite the spec being on its side: the spec's layout was authored before the
filter-at-the-bus capability was articulated, and conforming to it would foreclose the thing we now know
we want. A contract is worth honouring until it is worth amending; this one is worth amending.

**Why (d) is not the recommendation today:** it is the technically correct answer and I would take it if
hardware existed. With no CAN hardware wired, we would be committing to a priority scheme we cannot
falsify, and priority mistakes surface only under bus contention — the exact condition a loopback cannot
produce. Better to take (b) now, and revisit priority when there is a bus to measure.

**Your call: _______**

---

## Whichever way this goes

- The `seq` narrowing from 512 → 128 that today's message-type fix introduced is **moot under (a) and (d)**
  and needs a bless only under (b).
- `CANTransport` must implement `[BP-CAN-DATA]` byte 0 regardless — it currently sends no flags at all,
  so `ttl`, `priority`, and (under (a)) `scope` have nowhere to live.
- **A cross-implementation test is the real fix.** The defect survived because `CANTransport`'s tests use
  an in-process loopback where both ends share one encoder — a closed world that cannot detect
  disagreement with a different implementation. The de-fork test landed today is the right pattern:
  parametrized over *(substrate × case)*, with the bridge registered as a substrate. Extend it to
  arbitration-id round-tripping so this class cannot recur silently.

---

## RESOLVED — 2026-08-13. **Option (d)**, and the recommendation was wrong.

**David's call: (d).** Ratified 2026-08-13 after an independent CAN specialist's teardown
(`docs/can-binding-teardown-brief.md`) and implemented the same day on `feat/wire-format-v2`.
The landed layout:

```
bit 28..26   25..22    21..6      5..2     1..0
[ prio 3 ] [ type 4 ] [ ns_id 16 ] [ src 4 ] [ scope 2 ]
```

**Where this record's reasoning held up.** Option (d) was correctly identified as *"the
technically correct answer"* and as the one nobody had listed. The observation that
`[BP-CAN-DATA]`'s priority field is **inert by construction** — a priority field outside the
arbitration id cannot affect arbitration — is the whole basis of the change. And the closing
note was right about the method: the defect survived because both ends of the loopback shared
one encoder, and the fix is cross-implementation testing. `env:native_can_id` now compares all
29 bits across two languages, and `python/tests/test_can_wire_format_v2.py` computes ids a
third way, from the layout, importing no shift.

**Where it was wrong, and this is the part worth keeping.** The recommendation deferred (d)
on the grounds that *"with no CAN hardware wired, we would be committing to a priority scheme
we cannot falsify, and priority mistakes surface only under bus contention."*

That reasoning inverts the actual risk. **There was already a priority scheme — an accidental
one.** Declining to design priority did not leave the question open; it left `ns_hash` in the
top 16 bits deciding it, so arbitration order was a CRC artifact of the namespace *name*.
Waiting for hardware to falsify a deliberate scheme meant shipping an arbitrary one in the
meantime, and every node that shipped raised the cost of changing it. The window for a free
wire-format change is *before* hardware, not after.

The correct form of the caution is narrower and survives: **the tier assignments are
falsifiable only under contention, and have not been measured.** The *layout* did not need a
bus to get right.

**What else the teardown overturned.** The brief's own §5b proposal — renumbering
`[BP-TYPE-CODES]` by urgency — was rejected: it breaks the UART `type_scope` byte for a
property point-to-point UART cannot use, welds message identity to urgency permanently, and
cannot express within-class priority at all. An explicit `prio` field makes it unnecessary.
Type codes 0–9 are unchanged.

**Disposition of the fields this record argued about:**

| Field | Outcome |
|---|---|
| `scope` | **In the id**, bits 1..0 — David's hardware-filterability requirement met. At the BOTTOM, not under `type`, so it cannot become a second undesigned arbitration axis (and `auto`, the default, would have sorted every unspecified message last) |
| `src` | **Kept and redefined**: 4 bits of *allocated* node id, 1..15, 0 = unassigned. The condition in this record — "(b) must not ship while `src_hash` simply vanishes" — is honoured. It is no longer a hash: on the bridge side that was CPython's salted `hash()`, which differed between two runs of the same process |
| `seq` | **Dropped from the id.** Its own docstring described it as "a rolling counter with no consumer that needs 512 values". Nothing read it |
| `[BP-CAN-DATA]` byte 0 | **Not implemented, and now marked NOT NORMATIVE in the spec.** `scope` is in the id; the value's type is carried by the [BP-VALUE-CODEC] tag. Retained as a design sketch |

**Still outstanding, and it is not code:** nothing has been flashed and the Pi bridge has not
been redeployed. The fleet interoperates only after one coordinated reflash — the value type
tag makes tagged and untagged peers deliberately non-interoperable.
