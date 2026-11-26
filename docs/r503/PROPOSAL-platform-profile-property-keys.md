# Proposal — add a `PROPKEY` registry to `platform-composeio-profile.lts`

**Drafted** 2026-08-17 by the composeux-architect working the R503 project, at David's request.
⛔ **This is a PROPOSAL, not a change.** The profile lives in composeio and I do not write outside
`${DATASHEETS_HOME}/R503`. **Route to the composeio owner.**

---

## Why this exists

Adding the `lts: "1.1"` format-version marker to a spec switches it from **legacy** to **strict** mode,
which promotes `undeclared_key` from `warning` to **`error`** (SPEC §1.3.3 line 282). Measured on the
R503 corpus: stamping all three files takes it from **30 errors to 1,923**, buying only the removal of
3 `legacy_document` warnings.

⛔ **None of those errors is fixable from inside the R503 project**, because SPEC §1.3.3 gives a strict
key exactly three homes and only one is reachable:

| tier | reachable from R503? |
|---|---|
| **core key** | ⛔ no — normatively closed under amendment A2 |
| **profile-declared** | ⛔ no — the profile is in composeio, **and it declares ZERO property keys today** |
| **`x-` private prefix** | ⚠ yes, and it is the wrong instrument — see below |

⭐ **The sharpest datum:** `invalidating_assumption` is flagged undeclared. That is the field
**SPEC §8.1 line 1182 specifies by name** and line 1186 calls *"the defense"* against the
debate-amplifier anti-pattern. **When strict mode errors on vocabulary the specification itself
mandates, the corpus is not the thing that is wrong.**

## Measured, not asserted

Via `lts_profile_audit(corpus="r503")` and the parser:

| measurement | value |
|---|---|
| distinct property keys used | **930** |
| declared by the profile | **0** |
| `used_but_unregistered` | **930** |
| total property instances | **2,387** |
| keys used ≥10× | **30 keys → 53.3% of instances** |
| keys used ≥3× | **71 keys → 61.8% of instances** |
| keys used exactly once | **~804** |

⚠ **The long tail is BY DESIGN, and this proposal does not try to close it.** SPEC's own §1.3.3 preamble
describes a private tier, and P5 says do not architect vocabulary upfront. 804 single-use keys is that
policy working, not a mess. **The registry should cover the recurring PATTERNS and leave the tail to
legacy mode or `x-`.**

⛔ **Why `x-` is the wrong instrument here:** it would mean renaming ~1,893 authored lines including
`evidence-class` (72 uses), `claim` (60), `discriminates` (50) and `observed-by` (50) — the exact fields
the evidence-slot pattern exists to make queryable. Renaming them to `x-` silences the report by making
the fields harder to find, which inverts the point of having them.

## ⛔ These are FLEET keys, not R503 keys — the argument for the PLATFORM profile

Verified by grep across `composeio/corpora/**/*.lts`, outside R503 entirely:

| key | files | uses |
|---|---|---|
| **`invalidating_assumption`** | **22** | **98** |
| `evidence-state` | 2 | 16 |
| `discriminates` | 2 | 14 |
| `grounds-claim` | 1 | 9 |
| `question-state` | 1 | 5 |
| `falsifier` | 2 | 2 |

And the evidence-slot pattern is **defined** in `platform-specs/69-tm1637-peripheral.lts`
(`[CXC-PERIPH-EVIDENCE-SLOTS]`) and already reused in `platform-specs/73-bus-storage.lts`. R503 is its
**third** instance. ⇒ This vocabulary was always platform-level; nobody ever declared it.

## ⛔ The refusal constraint — read this before adding anything

`_declared_property_keys_from_raw` (`factlattice/profile.py:546`) **REFUSES** a key that already mints
an edge: *"Declaring an edge-bearing key as a plain descriptive key would be a quiet contradiction — one
profile saying a key both does and does not mint."*

**The 22 minting keys, from `parser.REL_PROPERTY_EDGES` + `FLOW_PROPERTY_EDGES`:**
`branches, contrast-with, converges-to, depends-on, follows, if-false, if-true, implements, issuer,
next-step, part-of, related-to, relates-to, resolves, see-also, spawns, superseded-by, supersedes,
tested-by, used-by, verified-by, verify`

⚠ **Two of them are R503's most-used keys** — `part-of` (240 uses) and `see-also` (191). **They are
deliberately ABSENT from the proposal below.** They already mint and need no declaration. A registry
that included them would be refused wholesale, so this is the difference between a proposal that
applies and one that errors on load.

## The syntax — from the parser, not from memory

`profile.py:1112-1124` dispatches on a `PROPKEY` node prefix and accepts either form. The bulk form is
explicitly intended for this scale: *"a `- property-keys:` nested list is accepted too, because A2's
seed sets run to hundreds of keys per corpus and one node per key would be unreadable."*

---

## THE PROPOSED SECTION

Insert after `## Registered relationship verbs` and before `## Linter guidance`, matching the profile's
existing `## Registered <thing>` / `### [PREFIX-*]` shape.

```markdown
## Registered descriptive property keys

> SPEC §1.3.3 amendment A2's declared-key tier. Every key below is DESCRIPTIVE — it is stored and
> queryable and mints no edge. ⛔ A MINTING KEY IS NEVER LISTED HERE: `part-of`, `see-also`,
> `depends-on`, `supersedes` and the other 18 in `parser.REL_PROPERTY_EDGES` /
> `FLOW_PROPERTY_EDGES` are refused by `_declared_property_keys_from_raw`, deliberately, because a
> profile that said a key both does and does not mint would be a fork.
>
> ⚠ These are grouped BY PATTERN rather than alphabetically, because each group is a reusable
> document shape the fleet already has more than one instance of. The grouping is the argument for
> declaring them at PLATFORM level rather than per project.
>
> ⛔ This registry deliberately does NOT attempt full coverage. Measured on the R503 corpus:
> 930 distinct keys, ~804 used exactly once. That tail is SPEC's private tier working as designed
> and it stays undeclared.

### [PROPKEY-EVIDENCE-SLOT] The post-integration evidence-slot pattern

- defined-at: [CXC-PERIPH-EVIDENCE-SLOTS] in `platform-specs/69-tm1637-peripheral.lts`
- instances: TM1637 (spec 69), bus-storage (spec 73), R503 (specs 70/71/72) — three and counting
- why-declared: a slot's whole value is that it is QUERYABLE. An undeclared key is one strict-mode
  switch away from being an error on the pattern the platform recommends.
- property-keys:
  - evidence-class
  - evidence-state
  - question
  - expected
  - discriminates
  - observed
  - observed-on-date
  - observed-by
  - grounds-claim
  - falsifier
  - requires-control

### [PROPKEY-DECISION-RECORD] The in-spec decision record

- companion-to: [NODEFAMILY-DECISION], which already registers the node family but declares none of
  its property vocabulary
- ⛔ invalidating-assumption-is-spec-mandated: SPEC §8.1 line 1182 names `invalidating_assumption`
  and line 1186 calls it "the defense". It is used in 22 files and 98 times across this corpus and
  is currently UNDECLARED — a strict-mode document would report a hard error on it.
- property-keys:
  - decision
  - alternatives
  - rationale
  - invalidating_assumption
  - do-not-decide-here
  - who-decides
  - blocks
  - blocked-by
  - ruling
  - status-note

### [PROPKEY-PRE-QUESTION] The pre-integration question pattern

- what-it-is: a question that must be answered before hardware is touched or a build begins,
  carried in the spec rather than in a tracker so it travels with the design
- property-keys:
  - question-state
  - how-answered
  - answer

### [PROPKEY-INTERFACE-CONTRACT] Callable-interface contracts

- introduced-by: `72-fingerprint-hal-contract.lts`, the first spec in this fleet to carry typed
  signatures rather than prose obligations
- ⚠ newest group and the least proven — one instance, not three. Declare it if the shape is wanted
  fleet-wide; defer it without affecting the other three groups.
- property-keys:
  - signature
  - semantics
  - preconditions
  - postconditions
  - failures
  - acceptance-machine
  - acceptance-human

### [PROPKEY-SKELETON] The universal claim skeleton

- what-it-is: the keys nearly every node of every family carries
- property-keys:
  - claim
  - claim-detail
  - rule
  - the-rule
  - why
  - why-it-matters
  - the-tell
  - worked-example
  - consequence
  - scope-note
  - second-reason
  - owns
  - ordering
  - status
```

**44 keys, none of them a minting key.**

## What this buys, stated honestly

⚠ **It does NOT make the R503 corpus strict-clean.** These 44 keys are the recurring core; the ~880
remaining distinct keys stay undeclared and a stamped file would still error on them. **This proposal
is a precondition for stamping, not a completion of it.**

What it does buy: the vocabulary that is genuinely FLEET-LEVEL and pattern-bearing stops being
undeclared, `lts_profile_audit` starts reporting real drift instead of `930 of 930 unregistered`, and
the three-instance evidence-slot pattern becomes a declared platform convention rather than a habit.

⛔ **The honest sequencing:** declare these, then re-measure, then decide whether stamping is worth it.
**Do not stamp any file on the strength of this proposal alone.**

## Acceptance, if someone implements it

1. The profile still loads — ⛔ a refused key raises `ProfileCollisionError` and takes the whole profile
   down, so a single mistake here breaks every corpus that resolves it.
2. `lts_profile_audit(corpus="r503")` shows `declared: 44` and `used_but_unregistered` down from 930.
3. ⛔ **NEGATIVE CONTROL:** add `part-of` to the list temporarily and confirm the profile REFUSES it. A
   registry only ever observed accepting things has not been shown to reject anything.
4. Every other corpus that resolves this profile still lints unchanged — this is a shared file.

---

*Sources, all read rather than recalled: `factlattice-internal-dev/factlattice/profile.py` lines 272-289,
546-583, 1112-1124 · `factlattice/parser.py` `REL_PROPERTY_EDGES` / `FLOW_PROPERTY_EDGES` ·
`SPEC.md` §1.3.3, §1.5.1, §8.1 · `lts_profile_audit(corpus="r503")` ·
`platform-composeio-profile.lts` section structure.*
