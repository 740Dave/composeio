# Précis — what has to be built for an LTS parser we can rely on

> ## ⛔ SUPERSEDED 2026-08-11 — written before I had read the specification
>
> Authored from the coordination channel's findings, citing SPEC.md and the README **second-hand**.
> Having since read SPEC.md end to end and all six `specs/*.lts`, three central recommendations are wrong:
>
> - **The round-trip emitter was already considered and rejected** by SPEC §2.5 (*"Coverage, not
>   equality"*) — legitimate parser transformations make a written-vs-parsed comparison drown in false
>   positives.
> - **The markdown-vs-JSON question is closed** by P0/P2. FactLattice is serialization-independent with
>   *mandated* notation freedom; collapsing to one notation would violate a foundational principle that
>   "cannot be overridden by any section below."
> - **"The real cost is the authoring surface"** contradicts P1 / `[DEC-MACHINE-AUTHORED]`: humans do
>   not author `.lts` at all. I priced a cost the format explicitly declines to pay.
>
> What survives: the LOSS/SUBSTITUTION split, SL-16, SL-17, and the queue order. The corrected finding
> is that the loss family is a **P2 and §2.5 conformance failure, not a design gap** — the parser does
> not implement notations the spec mandates. Retained as the audit trail for a wrong turn taken by not
> reading the contract.

**2026-08-11 · ComposeUX Architect · for David**

You asked what needs building, and whether design work remains. **Both.** One decision is yours and it
gates how much of the rest is worth building at all.

---

## 1. Where we actually are

Seventeen defect classes catalogued in one day (`coordination/silent-loss-classes.md`, **v2**), in two
families. Five are fixed. Line accounting shipped. Everything else is open.

**The number that should worry us is not 17 — it is the rate.** We were at zero classes yesterday and we
found the seventeenth by accident, in a state file, while writing about the sixteenth. **Nothing suggests
the list has converged.** Any plan whose shape is "enumerate the classes and write a detector for each"
is a race against a list that is still growing.

So the goal is not seventeen fixes. It is **two mechanisms that do not require knowing the class list.**

---

## 2. The design insight — two families, two universal detectors

The classes split cleanly, and the split turns out to be load-bearing:

| family | failure | example | universal detector | built? |
|---|---|---|---|---|
| **LOSS** — SL-1…6, 8, 10, 13, 14 | authored content never reaches the graph | continuation drop: 1,142 lines | **round-trip**: parse → re-emit → byte-compare | ❌ **no** |
| **SUBSTITUTION** — SL-7, 11, 15, 16, 17 | the wrong content *is* in the graph | `kept_source_file` names the wrong file | **agreement**: the linter's account == the engine's graph | ❌ **no** |

### 2a. Round-trip is the single highest-value thing to build

Line accounting — which shipped — proves a line was **consumed**. It cannot prove the line was
**understood**. A line can be consumed into the wrong field, truncated at a newline, or half-parsed, and
still be counted. That is precisely how SL-5 (1,142 lines), SL-7 (154 values) and SL-8 hid *underneath* a
green line-accounting run.

Round-trip closes that gap with one invariant:

> **`emit(parse(f)) == f`**, byte for byte, for every file in every corpus.
> Any byte the parser cannot reproduce is a byte it did not capture.

It catches **every LOSS class at once, including the ones nobody has named yet** — which is the only way
to get ahead of a growing list. It is also a far better acceptance gate for the 372-property remediation
than the ref-set diff we designed, because it checks everything rather than bracketed ids.

**Honest limit, stated up front:** round-trip is necessary, not sufficient. It is blind to substitution —
two files can each round-trip perfectly and still collide on an id, and the one that wins is still
decided by a sort nobody declared. That is why the second mechanism is not optional.

### 2b. The agreement check is what SL-16 proved we need

The collision detector fired **fourteen times, with an exact count, and named the wrong file.** A positive
control asserting "exactly 14" would have *passed*. Count-correctness and object-correctness are
independent properties, and every control we had measured only the first.

> **For any corpus, the linter's account of the graph must equal the engine's graph.**
> Assert it directly: lint in every invocation mode, query the engine, compare.

**This is the test that would have caught tonight's worst defect in one call, and it does not exist.**

---

## 3. What to build — four layers

### L1 · Grammar *(blocked on your decision — §5)*
The forgiving-markdown grammar is the *source* of the LOSS family. A strict grammar makes SL-1…6, 8, 10,
11 **unrepresentable** rather than merely detectable. It does nothing for the substitution family.

### L2 · Parser invariants *(the core build)*
1. **Round-trip emitter + equality test** over every corpus in CI. Needs a canonical emitter — see §4.3.
2. **Agreement check** — lint(dir) ≡ lint(paths) ≡ engine, on a corpus with a known collision. The
   fixture already exists: the ratified/staging pair I retained for exactly this.
3. **SL-16** — `_build_over_files` must preserve the corpus-relative path when staging, so both modes
   sort the same string. ~10 lines; root-caused, designed, unbuilt.
4. **SL-17** — `node_count` must count **served** nodes, not indexed ones. Currently over-reports by the
   number of collision losers.
5. **Continuation rule** as a **classifier override**, not a sibling rule — the ignorable classifier
   already swallows indented bullets, so a sibling never fires and reports zero, which looks like success.
   Fixture must assert **exactly 4** and include a `## Relationships` section.

### L3 · Test architecture
- **Recall requires a synthetic fixture with labelled ground truth.** No unlabelled corpus can measure
  recall — you cannot count what you did not find. The fixture is the only place FN is knowable.
- **It must contain a `## Relationships` section.** Nothing in any corpus we have exercises that
  exclusion — 0 in ZB, 0 in the deployment corpora — and it is the exclusion whose absence killed an
  earlier attempt.
- **Precision controls**: `~/factlattice-deployment` (45 near-misses, 0 hazards), ZB (38/0/0).
- **Every detector asserts an exact count**, never non-zero.

### L4 · Remediation *(after L2, not before)*
372 truncated properties · 154 polluted values · 84 orphaned by `outside_node_body` · 15 `empty_block`.
Join rule: `""` when the previous line ends in `-` (node ids wrap across lines and a space-join creates
permanently broken refs), `" "` otherwise. Gate on round-trip once L2.1 exists.

---

## 4. Pending design work — your actual question

**Settled, do not re-open:** continuation rule is a classifier override; the join rule and its acceptance
gate; annotation field ships *before* any `#` lint; class lists carry a version and date.

**Designed but unbuilt:** SL-16, SL-17, the continuation override, the annotation field.

**Genuinely undesigned — real work, not just typing:**

1. **The canonical emitter.** Round-trip needs one, and it is a non-trivial design: property ordering,
   whitespace normalisation, escaping, and what to do about input that is *already* non-canonical. The
   honest version is a two-step invariant — `canon(f)` once, then `emit(parse(canon(f))) == canon(f)`
   forever after. **This is the main piece of undone design work and it is on the critical path.**
2. **`relationship_properties` extensibility (SL-12).** ~312 refs across the corpora create no edges
   because `REL_PROPERTY_EDGES` is a hardcoded set. *(Note: fifteen keys in two tables, not ten — I got
   that wrong earlier today and was corrected.)* Three implementation conditions are agreed with the ZB
   Data Scientist. **Needs your yes/no.**
3. **`decision_lifecycle`.** You asked for "show me decisions made but never implemented." The data is
   there — **43 of them** — and the query was never built. The capability requested to find unimplemented
   decisions was itself an unimplemented decision.
4. **Profile efficacy.** The engine consumes **two** of the twelve advertised profile hooks. Your standing
   lean is *fix the engine, don't scale back the schema* — that lean has never been turned into a plan.

---

## 5. ⛔ The one decision that gates the rest

> **Do we keep a forgiving markdown grammar, or move to a strict one (JSON/JSONL)?**

**Strict grammar converts silent PARTIAL loss into loud TOTAL failure** — a file that will not parse gets
fixed; a file that parses at 40% does not. It makes eight of the seventeen classes impossible to express.

Two things to weigh that are easy to miss:

- **It does not touch the substitution family.** Two JSON objects collide on an id exactly as two markdown
  headers do. Migration is necessary-but-not-sufficient; L2.2 is needed either way.
- **The real cost is the authoring surface, not the migration.** Today humans and Intelligences write
  `.lts` directly and read it in a diff. Under JSONL both write through an emitter — trading a forgiving
  parser we inherited for a single point of failure that is *our* code.

**My recommendation, and I'd argue for it:** *don't decide it as an either/or yet.* **Build the canonical
emitter first (L2.1).** It is required for round-trip regardless of which grammar wins, and it is
*also* exactly the writer a JSONL migration would need. It converts the grammar question from an
irreversible bet into a switch we can throw once we can prove, by round-trip, that no content is lost in
the conversion.

If you want the direct answer instead: **yes, go strict — but not before the emitter exists**, because
migrating without a round-trip proof is how you find out in six months that the conversion dropped
something.

---

## 6. Recommended order

1. **SL-16** (~10 lines, root-caused) — the instrument is wrong and we are using it to judge everything else
2. **Agreement check** (L2.2) — the fixture already exists
3. **Canonical emitter + round-trip** (L2.1) — the big one; subsumes most future detector work
4. **SL-17**, then the continuation override with its fixture
5. **Grammar decision**, now backed by a round-trip proof
6. Vocabulary → annotation field → `#` lint → remediation of the 372

Steps 1–2 are days. Step 3 is the real project. **Everything before step 3 is repair; step 3 is the thing
that makes the parser reliable rather than currently-known-good.**
