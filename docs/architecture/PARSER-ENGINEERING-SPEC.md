# FactLattice Text→Graph — Engineering Spec (rev 2: Conformance, Vocabulary, Migration)

- **Status**: PROPOSED — parser-architect, 2026-08-16 rev 2. Rev 1 was scoped to additive-only changes under a constraint David has lifted; this revision adds the vocabulary regimes, the format-version marker, and the migration machinery, and re-anchors everything to `~/factlattice-deployment/SPEC.md` as the normative document. Companion: `PARSER-ARCHITECTURAL-REVIEW.md` (the argument; carries the corrections trail and the conformance classification).
- **Target repo**: `740Dave/factlattice` (PUBLIC — push is David's word alone). Corpus changes land in their owning repos.
- **Constraints inherited** (not re-decidable here): undefined symbol = ERROR never silence (2026-08-16); mixed-case normalized with WARNING (2026-08-16); `- [ID]: description` FLAGGED never minted (2026-08-11); profiles may register edge keys (2026-08-15, = SPEC §1.3.1); **breaking extant documents is PERMITTED** (2026-08-16) — cost stated, never hidden.
- **Sequencing constraint**: Era 0 (C1, C2, C6, C7, C8 below) is independently shippable, changes no parse result, and is gated on no ratification.

---

## 1. Scope and non-goals

**In scope**: SPEC.md amendments A1-A5 (§2); parser/engine changes C1-C13 (§3); the clause-derived conformance suite (§4); migration tooling and per-corpus procedure (§5); predicted numbers (§6); compatibility/breakage contract (§7); evidence slots (§8-9).

**Non-goals**: no new bracket-class delimiter (review §5 — the `x-` private-key marker is the only new surface syntax; reopen condition named there); no E6 prose-mention edges; no SMD-lineage archaeology (review §8 — a stated gap, assigned to a human question, not to this build).

---

## 2. SPEC.md AMENDMENTS (the normative changes; everything in §3 implements one of these)

Each amendment follows the precedent of §1.3.1's ⛔AMENDED block: dated, attributed, rationale inline, old text superseded not deleted.

### A1 — Format-version marker (amends §1.5 Frontmatter)
New OPTIONAL frontmatter field `lts: "<major>.<minor>"`. Semantics: **absence = legacy mode** — the document is parsed under today's descriptive behavior plus Era-0 diagnostics (warnings); **presence with `lts: "1.1"` or later = strict mode** — the A2-A4 regimes apply with error severity. A parser MUST NOT refuse a legacy document; it MUST warn `legacy_document` once per file at lint. Verified precondition: **no version-ish frontmatter key exists in any of 94 live corpus files** — the field is uncontended. This is the mechanism that makes every other amendment stageable per-document and per-corpus; it ships first.

### A2 — Property-key vocabulary: DECLARED-OR-MARKED (amends P3-as-applied-to-keys, P5, §1.3)
1. A property key in a strict-mode document MUST be one of: (i) a **core key** (§1.3's descriptive/classification/structural tables — the tables become normative-closed rather than illustrative); (ii) a **profile-declared key** (extending §1.3.1's mechanism to non-minting keys: `property_keys: [...]` in the profile, same refusal constraints); (iii) an **explicitly-private key**: prefix `x-` (grammar: `"x-" , KEY`). Private keys parse as ordinary properties, mint nothing, and are exempt from declaration — P3's private tier, now marked instead of ambient.
2. An unmarked, undeclared key-shaped line in a strict document is an **ERROR** (`undeclared_key`), carrying edit-distance suggestions against the declared set. In legacy documents: warning.
3. P5's promotion pipeline becomes the governed path from `x-`/declared-domain keys into shared vocabulary (registry sweeps; the `factlattice-registry` agent) — P5's text gains the sentence that promotion is *from the marked tier*, closing the loop the principle always implied.
- **Identity note for the amendment's rationale**: this is the review §3 verdict landing — `.lts` is a schema-bearing data format; P1 (machine authors) is why the friction objection to closure does not apply.

### A3 — Edge-verb regime (amends §2.4): unregistered verbs remain preserve+flag in legacy documents; in strict documents they are **errors**. The prose-target + bare-unregistered-verb path (today: silent, unspecified) gets a diagnostic in BOTH modes (`unrecognized_verb_line`, warning legacy / error strict).

### A4 — Reference totality (amends §1.1/§2.1, implements the 2026-08-16 rulings)
At ref-expecting positions (closed list: minting-key values; Relationships operands; Causal operands; heading id-candidates), a bracket token is REF | MALFORMED-REF | PROSE-BRACKET per the partition below, and a MALFORMED-REF MUST NOT bind silently or vanish silently: it is normalized-with-warning where case-folding alone suffices, else rejected-with-diagnostic. Forward reference to an id defined nowhere: error (ratchet per corpus, C9).

```ebnf
ID    = UPPER , { UPPER | DIGIT | "_" | "-" } ;
KEY   = LOWER , { LOWER | DIGIT | "_" | "-" } ;
PRIVKEY = "x-" , KEY ;
VERB  = UPPER , { UPPER | DIGIT | "_" } ;
NSVERB = NSSEG , { "." , NSSEG } , "." , VERB ;    NSSEG = LOWER , { LOWER | DIGIT | "_" } ;
bracket = "[" , scanchar , { scanchar | ":" | " " } , "]" ;
scanchar = UPPER | LOWER | DIGIT | "_" | "." | "-" ;
ref   = [ domain , ":" ] , [ doctype , ":" ] , ID ;
(* Partition on the LAST colon-segment, trimmed:
   REF            segment matches ID
   MALFORMED-REF  matches ALPHA,{ALPHA|DIGIT|"_"|"-"}, is not ID, and has "-"/"_" or UPPER-initial
   PROSE-BRACKET  everything else the scanner accepts                                            *)
```

Line grammar (P1-P5, WRAP, id-tables, nodedef, rel/causal lines) is ratified **as the parser implements it today** — that is the canonise half of the CommonMark procedure: these branches are load-bearing and model-coherent, so the corpus behavior becomes the spec text, replacing §2.1/§2.2's drifted table (fixing review V8/V9: the P5 name collision is resolved by renaming the spec's old P5 row to **P6-version-metadata**, and N3/N4/E6-E9 are explicitly marked *reserved, unimplemented in conformance level 1* so a conformance claim is falsifiable).

### A5 — The divergence register (new appendix): the review's V-table lands in SPEC.md and is thereafter maintained by the conformance suite (§4). The parser's "DELIBERATELY NOT ADDED" comment blocks get corresponding register rows; a divergence managed only in a code comment is, from this amendment on, itself a lint finding on the engine repo.

---

## 3. CHANGE SPECS

C1-C9 are rev 1's, unchanged in substance, restated by one line each with their new anchors; C10-C13 are new. Every change: invariant, diagnostics, red→green acceptance, what it does NOT fix.

- **C1 (Era 0)** Rejected-ref accounting: `extract_refs_accounted()` + `ParseFinding(kind="malformed_ref")` at ref-expecting positions (implements A4's detection half). Red test: `- relates-to: [Mixed-case-x]` yields one finding, zero edges — fails today. Not fixed: binding (C5), non-minting keys (C2). Anchor: SPEC §1.1 + review V1-V3 (class b).
- **C2 (Era 0)** `unminted_reference_list` lint (`factlattice_engine/lint/unminted.py`), predicate owned by `binding.py` (single copy: `is_pure_reference_list`). Warns with `nearest_minting_alternatives`. Predicted ≈186. Anchor: review S4.
- **C3 (Era 1)** `ParserConfig` frozen dataclass — one constructed value for `registered_ns / profile_layers / domain_verbs / property_edges`; `ParserConfig.from_profile(profile, edge_config)` is the ONE seam where the two profile systems merge; loose kwargs deprecated one release (public repo: semver-major). Invariant: parse output is a function of `(bytes, config)`, never the entry point. Anchor: review V5/U2.
- **C4 (Era 1, may land as interim loose-param patch before C3)** Thread all four config inputs on all paths: add the missing `property_edges` parameter to `index_builder.build_index` (:45-58), thread through `incremental.py:124`, `config.py:161/204`, `corpus.py:212/273/377`. Acceptance: `documents:` mints via the live engine path (+28-29 DOCUMENTS edges predicted); this is the fix for the **class-(a) conformance violation of SPEC §1.3.1** and deletes the profile's line-602 inert-key annotation with ceremony. Not fixed: unregistered keys (A2 territory).
- **C5 (Era 1)** Case-normalization with warning at ref-expecting positions (`FL-W102 ref_case_normalized`); folding only, never guessing; unresolvable-after-fold = W101 stands; newly-bound refs that dangle surface via the forward-dangle error — both 2026-08-16 rulings composed. Anchor: A4.
- **C6 (Era 0)** Entry-point equivalence selfcheck case — parses a control (document, profile) pair through all four production paths, asserts graph identity. **Ships red-and-known three ways** (the `detection_status` mechanism exists for exactly this); flips to must-pass at C4. This case alone would have caught V5 in all three of its forms.
- **C7 (Era 0)** Truthful audit: `binding.minting_keys_for` + `profile_audit._property_edges_section` split `{declared, threaded, in_effect}`; a declared-not-threaded key is `FL-W106` at **error** severity (an engine defect, not an authoring one). Red: today's audit lists `documents` under `minting_keys_in_effect`.
- **C8 (Era 0)** Implement the 2026-08-11 ruling: `- [ID]: description` → `KIND_NEAR_MISS_ID_COLON` (coverage.py, section-aware, warning). Red test verified failing today (classifies `list_prose`). Also: the ruling text itself enters SPEC via A5 (it is currently in neither artifact — review class d).
- **C9 (Era 2)** Forward-dangle severity ratchet per corpus (dangle-lint default already error; index-level count flips per corpus config after its Era-2 baseline).
- **C10 (Era 1) — Version marker + mode switch.** Parser: read `lts` from frontmatter (F1 already parses the field; new: `ParsedFile.format_version`, mode derivation, `legacy_document` lint finding). All strict-mode severities key off this. Invariant: *no behavior of a legacy document changes except added warnings.* Acceptance: same file parsed with and without `lts: "1.1"` yields identical graphs, differing only in finding severities. Implements A1.
- **C11 (Era 1) — Key vocabulary enforcement.** Profile loader: `property_keys[]` declaration (same refusal constraints as §1.3.1); parser/lint: in strict docs, unmarked undeclared key → `undeclared_key` ERROR with edit-distance suggestions (threshold: distance ≤2 to a declared key suggests; else "declare, mark x-, or rewrite"); `x-` keys parse as properties, exempt. Core key tables (§1.3) shipped as data (`factlattice/core_keys.py`) imported by parser, linter, and writer — one definition (the `REQUIRED_FRONTMATTER` precedent in constants.py, generalized). Red: a strict-mode doc with `- unregistered-thing: v` errors; the same doc legacy-mode warns; `- x-unregistered-thing: v` is clean in both. Implements A2.
- **C12 (Era 2) — The mechanical migrator** (`python -m factlattice.migrate <corpus-root>`): (i) hapax/undeclared keys → `x-` prefix (2,655 lines predicted, zero data loss — value and node untouched); (ii) A-tier key-region repairs via the existing `lts_repair_property` engine (210 predicted); (iii) ref case-folds where the folded target resolves (subset of 164); (iv) emits the author worklist (B-tier 246; verb tail; `re-derive-via` 105 pending its ratified disposition) and the corpus's readiness report for the `lts: "1.1"` stamp. Invariant: the migrator is **idempotent** and its every edit is one of the three enumerated mechanical classes — anything requiring a choice lands on the worklist, never in the diff (the substitution doctrine applied to migration itself). Acceptance: run on a fixture corpus; re-run produces an empty diff; graph diff shows only: same nodes, same non-minting properties (renamed keys `k`→`x-k`), plus edges gained from (iii).
- **C13 (Era 1) — Verb regime enforcement** (implements A3): strict-mode unregistered verb → error; the prose-target bare-verb silent path (parser.py `_find_edge_type` returning None on unregistered bare verb with no bracket ahead) gains `unrecognized_verb_line` in both modes. Red: a strict doc with `[A] BUILDS_ON prose target` errors; today it vanishes.

---

## 4. CONFORMANCE SUITE (clause-derived; supersedes rev 1's §4 anchor, keeps its cases)

1. **Clause inventory**: every normative statement in SPEC.md (22 MUST / 5 SHOULD / 9 MAY keyword instances + un-keyworded normative regexes/tables) gets a clause id; each clause ≥1 case; the review's V-table rows V1-V11 seed the first eleven clause-case groups. Rev 1's 28 grammar cases (G1-G28) carry over with clause ids attached; strict/legacy mode variants double the vocabulary cases (same input, two expected severities).
2. **New case groups for rev 2**: A1 (version-marker semantics: absence/presence/unknown-version), A2 (declared / x-marked / undeclared / edit-distance-suggestion / core-table closure), A3 (verb strict/legacy / prose-target diagnostic), C12 (migrator idempotence + mechanical-classes-only).
3. **Adequacy by mutation**: deleting any production, diagnostic emission, or severity rule fails ≥1 case; run in CI against the engine.
4. **The selfcheck** keeps the running-process role, consumes the derived control corpus; SL-1..32 retained as a provenance-labeled regression annex.
5. **Measured gap this closes**: 0 of the keyword-normative clauses currently have a clause-labeled test; V5 violated an explicit ⛔AMENDED block undetected through two audits because no executable artifact connects SPEC to code.

---

## 5. MIGRATION PROCEDURE (per corpus; CommonMark-style tolerated divergence, with a marker instead of a guess)

Per corpus, in order: (1) Era-0 instruments land → baseline counts recorded; (2) run C12 migrator → mechanical diff reviewed+committed (predicted per-corpus sizes derivable from §6; worst file `decision-graph-composeux-architect.lts` carries 45 of the 456 key-region losses); (3) burn down worklist (author time; B-tier + verb tail + per-corpus decisions); (4) stamp `lts: "1.1"` in frontmatter (one mechanical pass); (5) flip strict severities + C9 ratchet in corpus config. A corpus that never migrates stays fully functional in legacy mode with warnings, indefinitely — divergence tolerated, never silent. **Decision debt surfaced, not made, by this spec**: the `re-derive-via`/FIXTURE disposition (105 lines, targets resolve to no corpus — David's call); the declared-key seed set per profile (start from the 522 freq≥2 keys as candidates, curated per corpus, promotion via P5 sweeps).

## 6. PREDICTED FIRST-RUN NUMBERS (predicates in the review; all measured 2026-08-16; "N is the correct pre-fix reading")

| Instrument / change | Predicted | Basis |
|---|---|---|
| C1 malformed_ref | ≈65 at strict positions (of 164 total id-shaped-mixed refs; split predicate review §1.4-U1) | per-key table |
| C2 unminted_reference_list | ≈186 (grounds-claim 54, re-derive-via 45, documents 28, upholds 14, uses 13, tail 32) | strict pure-list predicate; shifts to W106 for any key a profile actually declares |
| C7 declared_not_threaded | ≥1 (`documents`); check zb profile (§8 Q1) | profile line 602 |
| C4 edge delta | +28-29 DOCUMENTS; verb-flag deltas measurable by `stats` diff | profile annotation |
| C8 id-colon findings | NOT COMPUTED — measure in PR, slot below | — |
| C11 undeclared_key (legacy warnings, whole corpus, pre-migration) | ≈2,655 hapax + (undeclared recurring tail: 3,177−522−core−declared) | key census: 17,110 lines, 3,177 distinct, 522 cover 84.5% |
| C12 mechanical rewrites | 2,655 x-prefix lines / 113 files / 738 of 2,691 nodes touched; 210 A-tier repairs; ref-folds ≤164 | census + property_loss (A 210 / B 246, recall 0.25) |
| C13 unrecognized_verb_line | small; 24 non-core bare verbs, six biggest (563 uses) are declared-and-dead → become C4 edges, not errors | verb census |

## 7. COMPATIBILITY AND BREAKAGE CONTRACT

- **Era 0**: findings only; every graph byte-identical; nothing breaks.
- **Era 1**: legacy documents (no `lts` field — today: all 94+) change behavior only by C4/C5 (+edges, each with a warning receipt) — the two changes David's rulings directly command. Strict mode exists but no document is in it yet.
- **Era 2**: breakage is **opt-in per corpus via the version stamp**, after its mechanical migration, with its worklist burned down. Post-stamp, previously-tolerated inputs (undeclared keys, unregistered verbs, malformed refs, forward dangles) are errors — this IS the authorized breakage, scoped and dated, cost pre-stated in §6. What is never broken: legacy-mode parsing of unstamped documents; any node's content under the mechanical migrator (rename-to-`x-` preserves every value).
- **API**: loose parse kwargs → one-release deprecation → removal (semver-major on the public repo).

## 8. PRE-INTEGRATION QUESTIONS (answer before building)

1. Does the other-data-science profile declare `grounds-claim` (or others)? decides the C2/C7 split. **ANSWER: EMPTY**
2. `re-derive-via` disposition: minting + FIXTURE registry, ratified citation form, or re-point? (105 lines.) **ANSWER: EMPTY**
3. Declared-key seed sets: start each profile from its corpus's freq≥2 keys, or curate from zero? **ANSWER: EMPTY**
4. `x-` vs `_`-prefix spelling for the private marker (both satisfy A2; `x-` has the OpenAPI precedent and stays within KEY grammar). **ANSWER: EMPTY**
5. SMD-lineage due diligence (review §8): which SMD guarantees must Era 1 preserve? Ask David / trace the other project's data; do not infer. **ANSWER: EMPTY**
6. Version-marker spelling `lts: "1.1"` vs `factlattice: "1.1"` (field name is uncontended either way). **ANSWER: EMPTY**

## 9. POST-INTEGRATION EVIDENCE (fill on landing; an empty slot is a finding)

- Era-0 first-run counts vs §6, per corpus: **EMPTY**
- C6 arrival: the three failure signatures, verbatim: **EMPTY**
- C4: per-corpus edge/flag `stats` diff: **EMPTY**
- C12 per-corpus: mechanical diff size, worklist size, days-to-stamp: **EMPTY**
- Mutation-adequacy run over the clause suite: **EMPTY**
- Post-migration telemetry on `undeclared_key` false positives on genuine prose (the §5-review reopen criterion for the intent sigil): **EMPTY**
