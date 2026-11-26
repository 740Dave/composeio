# FactLattice Text→Graph — Architectural Review (rev 2)

- **Reviewer role**: parser-architect (`~/factlattice-deployment/agents/parser-architect.md`)
- **Date**: 2026-08-16 (rev 2, same day — see the corrections trail immediately below)
- **Subject**: the design problem of turning text into graphed edges, as implemented by `~/factlattice-deployment/factlattice/parser.py` + engine, governed by `~/factlattice-deployment/SPEC.md` (FactLattice 1.0 DRAFT, 74,716 bytes, RFC-2119 conformance language, twice audited), deployed under 13 corpora / 6,468 nodes.
- **Companion**: `PARSER-ENGINEERING-SPEC.md` (same directory) — the buildable contract, revised in lockstep.

---

## Corrections trail — where the framing led rev 1 astray, recorded because the pattern IS the finding

Rev 1 of this review was scoped by its briefing, and two of the briefing's constraints turned out to be the briefer's, not David's. A third briefing claim was retracted as false. Each is recorded here because the mechanism — *a confident narrative occupying the space where a primary source should have been read* — is the same mechanism this review found inside the parser, and the project will meet it again:

1. **"Never mandate a rewrite"** was in my role definition and the brief. David explicitly permits breaking extant documents (*"it's ok for \[made-up keys\] to be recognized as an ATTEMPT... and be REJECTED"*). Rev 1's delimiter rejection and additive-only roadmap were partly artifacts of that constraint. Re-argued in §5 and §7 below with migration on the table.
2. **Rev 1's central invariant could not answer the first question David asked** (*"are keys free form today?"*). Totality-over-declared-symbols is inexpressible when the symbol set is open: with a free key vocabulary there is no such thing as an undefined key, so a mis-typed `see-alos` and a deliberate one-off key are byte-indistinguishable. Rev 1 built the right floor and mistook it for a ceiling. The vocabulary question is now the center (§4).
3. **"The format has no specification and never did" — briefed to me as the root finding, and FALSE.** `SPEC.md` exists, is normative in form, cites "171 documents across 8 domains" as its empirical base, and has absorbed two audits (31 + 21 findings). The briefer had the pointer (the `lts-author` skill names SPEC.md as format authority) and built a narrative instead of reading it; I received the narrative mid-review. The correction sharpens rather than weakens the review: **the problem is not absence of design — it is that three normative artifacts exist and NOTHING BINDS THEM** (§0). An architect handing a reviewer a fabricated absence, caught only because David pushed back, is a live instance of this project's silent-loss problem operating at the human/agent layer — the corpus's disease is not only in the parser.

What survives from rev 1 unchanged: the code-level findings (every one re-verified), the measured populations, the totality invariant and detection phase (now the floor, not the ceiling), and the entry-point threading finding — which the SPEC reading *upgraded* from engine defect to conformance violation (§2-V5).

---

## 0. The root finding: three normative artifacts, no binding, silent divergence

The system has **three artifacts that each behave as an authority**:

| Artifact | Born | Claims authority by |
|---|---|---|
| **The corpus** | first `.lts` 2026-07-07 (`1d8b74e`); 113 files existed before any parser; lineage runs further back through SMD (~2026-03 in composeio, per David ~a year earlier in another project, with a research paper — identity not established here, see §8) | being the installed base; machine-authored under SPEC P1 |
| **SPEC.md** | FactLattice 1.0 DRAFT; RFC-2119; derived from analyzing 171 documents; twice audited | saying MUST/SHOULD/MAY (22/5/9 keyword instances, plus many un-keyworded normative statements — regexes, tables) |
| **The parser** | first commit 2026-07-23 (`9d75bcd`), 16 days after the corpus began | being what actually runs; its own docstring line 10 declares the dual allegiance: *"GRAMMAR MAPPING... as read from SPEC.md + the real staged corpus"* |

**And no mechanism binds any pair of them:**

- **No conformance suite is derived from SPEC.md.** The selfcheck (43 cases, PASS 43/43 live) is derived from remembered incidents and says so in its own `limit` field. Nothing maps SPEC clauses to tests; the two audits were manual. A parser change that violates a SPEC MUST fails nothing.
- **No document declares which format version it conforms to.** Verified: across 94 corpus files, zero version-ish frontmatter keys (the fields in live use: `domain, scope, boundary, not-this, depends-on, audience, confidentiality, artifact-readiness, created, ...`). `FACTLATTICE_PROFILE_VERSION = "factlattice-1.0-draft"` (constants.py) stamps the *index*, not the documents. **This is the single fact that decides whether a staged migration is possible — today it is not, because strictness cannot be scoped to documents that opted in.**
- **No precedence rule exists for disagreement.** When parser and SPEC diverge, the divergence is managed in *parser comments* — "DELIBERATELY NOT ADDED" (parser.py:190, 196), "documented scope choice" (E6, parser.py:87), the P5 naming collision (§2-V8) — i.e., **the parser's comment block is the de-facto errata of the spec**, unreviewed as such. When parser and corpus diverge, the corpus wins silently (that is what a descriptive fix is). When ruling and both diverge, results are split: David's 2026-08-15 property-key ruling landed in SPEC §1.3.1 as a formal ⛔AMENDED block *and* in the parser *and then died in the wiring* (V5); his 2026-08-11 `- [ID]:` ruling landed in **neither** SPEC nor code (V4).

David's phenomenon — *"every major improvement happened only because I insisted the problem was defined incorrectly"* — is what this structure produces. The team was extending whichever artifact was nearest (usually the parser, descriptively), because no artifact could pronounce another one wrong. **"One layer down" is what fixing looks like when authority is undefined.** The disease is not absence of design; it is unbound design.

---

## 1. THE MODEL AS IT ACTUALLY IS (condensed from rev 1; all findings re-verified)

A single-pass, line-oriented, regex-dispatch parser with section state and fixed branch precedence; no token layer; consumption recorded per line (`LineCoverage`) at branch commit points. Type is carried by **lexical case and position**: ids `^[A-Z][A-Z0-9_\-]*$` (parser.py:310), keys `^[a-z][a-z0-9_\-]*$` (:334), verbs UPPER_SNAKE (:396), namespaces lowercase-dotted. The bracket **scanner** (`_BRACKET_RE`, :309) accepts a strict superset of every validator — mixed case AND spaces — and every rejection path discards without a diagnostic (`_resolve_ref`→None :667; heading skip :696; edge-source `continue` :1790; causal skip :1828).

Read through the corpus-first chronology, the scanner/validator split acquires its correct explanation: **the scanner was written to read what is there; the validators encode what is wanted; they are two authorities sixteen days apart, and the gap between their charsets is exactly where the silence lives.** The parser comments carry ≥8 further decisions of the shape "the corpus authors it this way, so the grammar does" (unbulleted rel-triples :376; causal edges inside fences :1743; the WRAP furniture list :357; the `next` exclusion :217; the id-table under-claim; both "DELIBERATELY NOT ADDED" blocks; the P5 mapping form itself, added as "a CONFORMANCE fix to §2.2" that §2.2 still does not describe). Some are good judgment; none was reviewed as a *spec change*, which is what each one was.

**Undefined regions** (all verified live; populations re-measured this review):
- **U1 — the mixed-case bracket token**: `[UPPER-ID]` = ref (defined); `[lowercase words]` = prose (defined, pinned by selfcheck); `[Mixed-Case-P1]` / `[COMMIT-09b80e9]` / `[FOO BAR]` = **undefined**, silently prose. Population: 164 id-shaped mixed-case refs in property values across all 5 root sets; 102 of 105 `re-derive-via` lines; **0** in headings, **0** in Relationships sources — the class lives where authors write fastest.
- **U2 — entry-point divergence**: the three production parse paths thread three different subsets of the five config inputs; `property_edges` is dead on **all** of them (`build_index` lacks the parameter, index_builder.py:45-58; sole passer is parser.py:899 forwarding its own default); `domain_verbs` dead on the live engine path (incremental.py:124, config.py:161); `profile_layers` dead on the corpus.py path (corpus.py:212/273). Two disjoint profile systems (`Profile` vs `ProfileEdgeConfig`) project disjoint capability subsets. `binding.py:143` and `profile_audit.py:234` report *declared* keys as *minting* — the audit states the opposite of the truth. The live profile documents its own key's inertness in heroic detail (platform-composeio-profile.lts:602).
- **U4 — the half-implemented ruling**: `- [ID]: description` → `list_prose`, silent (tested); ruled FLAGGED 2026-08-11.
- **U5 — collision order**: first-definition-wins by sorted-file-order is an implementation accident with graph-visible consequences (SL-23 interaction).

---

## 2. THE CONFORMANCE CLASSIFICATION — every found divergence, against the normative document

Classes: **(a)** parser violates SPEC — conformance bug; **(b)** SPEC silent, parser decided — underspecification whose de-facto answer was never reviewed; **(c)** SPEC is the stale/self-inconsistent party; **(d)** a ratified ruling absent from BOTH spec and code — governance record that landed nowhere.

| # | Divergence | Class | Evidence and the deciding SPEC text |
|---|---|---|---|
| V1 | Mixed-case/ill-formed refs silently dropped in property values | **(b)** | SPEC §1.1 defines the well-formed id (`[A-Z][A-Z0-9_\-]+`); no clause says what a parser does with an ill-formed ref. §2.5's *principle* (silent loss must be reported) points the way but its letter covers lines, not values. The parser chose silence, unreviewed. |
| V2 | Mixed-case Relationships source silently skipped | **(b)** | Same. Live population 0; class open. |
| V3 | Ill-formed causal operand silently skipped | **(b)** | Same. |
| V4 | `- [ID]: description` neither minted (correct) nor flagged (ruled 2026-08-11) | **(d)** | The ruling is in neither SPEC.md nor code. Contrast the 2026-08-15 ruling, which reached SPEC §1.3.1 as a formal amendment — the governance pipe exists and fired once; it did not fire here. |
| V5 | `property_edges` unreachable from every production entry point; `domain_verbs`/`profile_layers` partially threaded | **(a)** | SPEC §1.3.1 (⛔AMENDED 2026-08-15): a profile MAY register edge-bearing keys; *"The edge type is declared ONCE and simply names the property that mints it."* Registered keys mint nothing anywhere. ⭐ SPEC §1.3.1 constraint 3's own rationale — a registration must not be *"inert while reading as functional"* — names precisely the failure the wiring reproduces one level above the constraint. This is the clearest MUST-grade violation found. |
| V6 | Audit surfaces report declared keys as in-effect | **(a)-adjacent** | Follows from V5: instruments implementing §1.3.1's visibility intent read the declaration, not the wiring. |
| V7 | Heading with only ill-formed brackets silently demotes to divider | **(b)** | §2.1 N1 covers the valid form only. Population 0; class open. |
| V8 | "P5" names two different rules — SPEC §2.1's `version-scope` row vs the parser's YAML-mapping form; §2.2 says "four formats" while the parser implements five + continuation folding + id-table promotion | **(c)** | SPEC is self-inconsistent (§2.2's closed list vs §8.1's mapping-shaped primitives vs P2's notation-freedom principle); the parser sided with §8.1+P2 and documented it as "a CONFORMANCE fix to §2.2" — a spec amendment executed in a code comment. |
| V9 | SPEC §2.1 rules N3, N4, E6-E9 are specified and unimplemented | **(c)/(a)** | Phase-1 scope choices recorded in the parser (E6 at parser.py:87), not in the spec. A conforming-parser claim is currently unfalsifiable either way because no clause is marked optional. |
| V10 | **Key vocabulary: 3,177 distinct keys used / 0 declared / 2,655 used exactly once** (all-roots measurement, predicate §4.1) | **NOT a violation — SPEC-SANCTIONED** | P3 explicitly grants a **Private tier**: *"bare names — local use, no registration needed, no collision guarantees."* P5: *"Don't architect vocabulary upfront. Observe what domains actually use... promotion."* The open key space is the spec working as designed. **Therefore David's new position — undeclared keys REJECTED — is a spec amendment to P3/P5, a change of the format's identity, and must be executed as one** (§4, §7). It cannot be shipped as a bug fix, and rev 1's framing of key losses purely as parser fragility was incomplete for this reason. |
| V11 | Edge verbs: 36 distinct bare verbs live in Relationships sections; core defines 33; 24 of the used set are non-core (USES 148, UPHOLDS 119, DOCUMENTS 87, REALIZES 85, GROUNDS 64, EXEMPLIFIES 60, + tail) | **mixed (a)+(b)** | The big six are profile-declared — and dead-threaded on the live path (V5 again). The tail (BUILDS_ON, CLOSES, MIRRORS, PORTS...) is unregistered: preserved+flagged per §2.4 when bracket-anchored, silently dropped when prose-targeted — the latter unspecified (b). |

**The highest-value row is V10**, because it reverses a premise every briefing carried: the hapax-key population is not indiscipline the parser failed to catch — it is licensed behavior under the ratified principles, produced by machine authors doing what P5 told them to do ("don't architect upfront"), minus the registry-sweep governance (P5's own promotion mechanism, and the `factlattice-registry` agent built for it) that was designed to close the loop and is not running as a gate.

---

## 3. THE DESIGN PROBLEM — what class of system this is, and the identity question

**Text→graph-edges systems live on a spectrum between two identities**, and the failure patterns of each are known from decades of precedent:

**Identity A — annotation vocabulary over prose** (Javadoc/Doxygen `@param`; reST field lists; Org keywords; BibTeX fields): the document is primary, structure is sprinkled in. The known failure: *vocabulary drift and silent ignoring*. BibTeX silently ignores unknown fields — a notorious, decades-complained-about wart. Sphinx is the instructive case: reST field lists are open, **and Sphinx was forced to maintain a registry of known fields anyway**, because consumers need semantics; an open vocabulary at scale grows a registry or grows noise. Javadoc survives because its tag set is closed and an unknown tag **warns** — intent is marked (`@`) and vocabulary is checked.

**Identity B — schema-bearing data format** (RDF/Turtle+OWL: predicates are IRIs that must resolve to published vocabularies; GraphQL SDL; protobuf; JSON Schema `additionalProperties:false`): the schema is primary, an undeclared field is an error, and adding a schema to schemaless data is a well-trodden migration. The known failure: authoring friction and rejected-but-meant content — mitigated everywhere by escape hatches (`x-` extension fields in OpenAPI/HTTP, `additionalProperties` opt-in).

**`.lts` is being used as both at once, and the failure patterns are exactly the superposition**: BibTeX-style silent ignoring (the 164 dropped refs, the unminted keys) *plus* open-vocabulary drift (3,177/2,655) *plus* the one thing neither identity suffers alone — structure-shaped prose and prose-shaped structure colliding in one syntax (the 456 key-region losses, the 96%-prose near-miss precision problem).

**The verdict this review owes: `.lts` is a schema-bearing data format wearing a document format's clothes, and should say so.** The deciding facts are already in SPEC.md: P1 makes the author a machine (the friction argument for openness — Identity A's main defense — does not apply, because the author is programmable); P0 says "design decisions are made at the graph level"; the graph is the product and the queries are the consumers. What the corpus needs from openness is *extensibility with declaration* (Identity B's registered tier), not *openness without declaration* (the Private tier as currently spelled for keys). §1.3.1 already moved the *relationship* keys to declared-with-refusals — the remaining move is the descriptive key space, and P5's own promotion machinery is the designed bridge.

---

## 4. THE VOCABULARY QUESTION — keys, verbs, ids: one principle, three regimes

**The principle that decides all three** (answering "can one rule cover them?"): *a symbol class is closed to the degree that consumers attach semantics to membership.* Ids are maximally semantic (resolution targets); verbs carry layer+direction+traversal semantics; keys split — a minority carry engine semantics (minting, `status`, `condition`/`actor`, lifecycle) and cross-node query semantics, while a long tail carries author-local narration. One rule does *not* cover all three; the same principle instantiates differently, and stating it this way lets the next person classify a case we did not see.

### 4.1 Keys — the hard case, decided
**Measured** (predicate: P1/P2/P4-head-shaped lines, fences excluded; all 8 live root sets, 113 files): 17,110 property-shaped lines; **3,177 distinct keys; 2,655 hapax**; 522 keys with freq≥2 cover 84.5% of uses; 738 of 2,691 header-form nodes (27%) carry ≥1 hapax key. (The briefing's 1,438/1,143 is the same phenomenon under a narrower root set/predicate — composeio `corpora/` alone reproduces as 1,695/1,362; the discrepancy is scope, not substance.) The tail includes 70-char sentence-keys (`how-many-artifacts-hold-a-stored-sha-stated-with-its-predicate-because`) — narration wearing key syntax, exactly what P3's private tier permits.

**Regime: DECLARED-OR-MARKED.** Amend P3 (for property keys specifically) and §1.3's tables:
1. **Core + profile-declared keys** are the schema: spelled exactly, checked by membership; an unmatched key-shaped line **near a declared key** gets an edit-distance rejection (`see-alos` → "did you mean see-also") — the closed vocabulary is what makes this diagnostic *precise* where today's shape-heuristics measured 96% false-positive.
2. **Private keys survive, but MARKED**: `x-` prefix (`- x-observed-tonight: ...`). One character declares intent ("deliberately local, no registration"), preserving P3's three-tier philosophy and the one-off annotation habit while making it *distinguishable from a typo* — which is the entire problem with today's bare private tier. This is the OpenAPI/HTTP `x-` precedent, and it is the delimiter idea (§5) landing at the only position where the evidence supports it.
3. **Unmarked + undeclared = ERROR** (in strict mode, §7): recognized as an attempt and rejected — David's words, operationalized.

**Cost, honestly**: mechanical migration can rewrite **all 2,655 hapax lines to `x-` form with zero data loss** (they remain parsed properties, now marked) — no author time, no breakage, no node loses content; 738 nodes touched, 113 files. Curation (promoting recurring tail keys into profiles, renaming near-misses onto declared keys) is opportunistic afterward, guided by P5 registry sweeps — **the designed mechanism, finally running with teeth**. What is lost if instead the loquacious keys were hard-rejected without the `x-` valve: 2,655 authored assertions and 27% of nodes damaged — which is why the valve is in the design. David authorized that breakage; the regime above shows it is not needed to get the rejection semantics he wants.

### 4.2 Verbs — closed with declared extension (already ruled; make it real, then ratchet)
The architecture already says declared-only (§1.3.1/§1.3.2 refusal constraints, core table, profile registration). Two gaps: the wiring (V5 — fix is mechanical) and the endgame for unregistered verbs: today preserve+flag (bracket-anchored) or silent drop (prose-target, unspecified). Regime: preserve+flag stays for legacy-mode documents; **strict mode upgrades unregistered verbs to errors**, and the prose-target silent path gets a diagnostic either way. Migration population: 24 non-core bare verbs; the six big ones (563 uses combined) are already profile-declared and start minting the moment V5 is fixed; the tail (≤20 uses each) is register-or-rename, an afternoon's curation per corpus.

### 4.3 Ids — closed by construction (works today; needs only totality)
An id is declared by its defining occurrence; references must resolve. This regime already functions — 0 heading losses, 0 edge-source losses. What it lacks is rev 1's floor, unchanged: loud rejection of ill-formed refs (V1-V3), normalization-with-warning (David's ruling), forward-dangle = error (already the dangle-lint default; ratchet per corpus).

---

## 5. THE DELIMITER QUESTION — re-argued without the no-migration constraint, and resolved

David raised `{SEE-ALSO}` vs `[SEE-ALSO]`. Rev 1 rejected it partly on migration cost — that reason is void. Re-derivation from the failure data:

There are two orthogonal ambiguities in this format. **Intent** (is this line data or prose?) and **vocabulary** (is this symbol known?). Case conventions currently carry both and can guarantee neither. A delimiter can mark either:
- **Delimiter as type marker (keys-vs-ids, the raised form)**: buys nothing measurable — keys and ids never collide in the data (lowercase-anchor vs uppercase-anchor, 0 confusions measured in headings/edges). Still the wrong answer, now for the right reason: it addresses an ambiguity that does not occur.
- **Delimiter as intent marker on every data line** (Javadoc's `@`): would make data/prose structurally disjoint — the strongest possible fix for the 456-line class — at the cost of rewriting all 17,110 property lines and every author habit. With migration permitted this is *affordable* (the rewrite is mechanical: the parser already knows which lines parse), and P1 means machines absorb the authoring cost. **But it is dominated**: once the key vocabulary is declared-or-marked (§4.1), membership does the disambiguation a sigil would do — a declared key parses, an `x-` key parses, an edit-distance near-miss rejects loudly, and residual prose-with-colon lines are errors an author repairs *once* at migration. The sigil would buy, on top of that, only the freedom to write unmarked prose bullets that look exactly like keys — a freedom worth less than the second migration it costs.
- **Delimiter as privacy marker on the tail only** (`x-`): adopted, §4.1. One character, one class, mechanical migration, preserves the annotation habit, kills the typo-vs-intent ambiguity.

**Resolution: no new bracket-class delimiter; `x-` on private keys is the delimiter this format actually needed.** Condition to reopen: if post-migration telemetry shows authors persistently tripping the undeclared-key error on genuine prose bullets (predicted rare — prose bullets opening `word:` are the near-miss survey's 4%), the full intent-sigil is the named fallback.

---

## 6. CONFORMANCE STRATEGY — now anchored to the normative document

Rev 1's grammar-derived strategy stands, with the anchor corrected: cases derive from **SPEC.md clauses first**, the EBNF second (the EBNF in the companion spec becomes SPEC §2's amended text — one grammar, not two).

1. **Clause inventory**: extract every normative statement (22 MUST / 5 SHOULD / 9 MAY keyword instances *plus* the un-keyworded normative regexes and tables — the §2.2 regexes, §1.1 id rules, §1.3 tables, §1.3.1/.2 refusal constraints). Each gets an id and ≥1 case. The V-table above seeds the first 11.
2. **Per-production rejection cases**: for every rejection path, a case whose *expected output includes the diagnostic* — silence becomes failable.
3. **Entry-point equivalence** as a conformance case (kills V5's class permanently, catches its recurrence structurally).
4. **Mutation adequacy**: deleting any production or diagnostic emission must fail ≥1 case.
5. **The selfcheck keeps its unique role** (behavior of the *running process*) and consumes the derived suite; the SL list remains a regression annex labeled by provenance.
6. **The spec-vs-parser delta becomes a maintained artifact** — the V-table, updated by the suite, replacing the current regime where the parser's comments are unreviewed errata.

Gap measured: today, 0 of the ~36+ keyword-normative clauses have a clause-labeled test; the 43 selfcheck cases cover incident classes only; the V5 violation of an explicit ⛔AMENDED spec block sat undetected by every instrument, including two spec audits, because nothing executable connects the two artifacts.

---

## 7. THE ROADMAP — descriptive→prescriptive transition, migration permitted, costed

The CommonMark lesson, applied honestly: the transition took a *decision procedure* (canonise vs bug), a *versioned spec with examples*, and *years of tolerated divergence* — not a flag day. The procedure here, stated so the next person can apply it to a case this review did not see:

> **Canonise** a corpus behavior iff it is load-bearing under queries (has downstream consumers) AND coherent with the abstract model (§1 of SPEC). **Declare it a bug** iff nothing forbade it and either its meaning is mechanically recoverable or its loss is accepted *in writing*. **Where corpus and spec disagree, the SPEC wins and the corpus migrates** — the explicit reversal of today's silent default. Every verdict is recorded in the spec with its rationale (the V-table is the register).

**Era 0 — Detection + record (now; no ratification, no breakage).** Rev 1's D-train stands: rejected-ref diagnostics (~164 predicted), unminted-reference-list lint (~186), truthful audit (declared vs threaded), the V4 flag, entry-point equivalence selfcheck (arrives red three ways, by design). Plus, new: the V-table lands in SPEC.md as an errata appendix — the divergences become *reviewed* for the first time.

**Era 1 — Ratify (the identity decision).** (1) **Format-version marker**: `lts: "1.1"` frontmatter field (spec §1.5 amendment). Absence = legacy mode (today's descriptive behavior + Era-0 warnings); presence = strict. *This marker is what makes everything after it stageable; it is the highest-leverage 20 lines in the roadmap.* (2) Vocabulary regimes ratified as spec amendments: keys declared-or-`x-`-marked with error on unmarked-undeclared (amends P3/P5 as applied to keys — §4.1); verbs strict-mode error on unregistered (§4.2); ids totality + normalization + forward-dangle error (§4.3). (3) Fix V5 (the wiring) and V8/V9 (the spec's own inconsistencies) — the spec must be clean before it can win arguments.

**Era 2 — Migrate (per corpus, mechanical-first).** Per corpus: run the mechanical migrator (hapax→`x-`: 2,655 lines, zero loss; A-tier key repairs: 210 lines via existing `lts_repair_property`; ref case-normalization where target resolves); burn down the author worklist (B-tier 246 lines; verb-tail registrations; `re-derive-via`'s 105-line FIXTURE decision — a corpus-design call this roadmap surfaces and does not make); stamp `lts: "1.1"`; flip strict. Corpus-by-corpus, no big bang; a corpus that never migrates keeps working in legacy mode with warnings — CommonMark's tolerated-divergence period, with a marker instead of a guess.

**What becomes structurally impossible (not merely detected) at end state**: a silent symbol — any key, verb, or ref that is neither declared, marked private, nor resolvable produces an error in strict mode; a typo that parses as intent (edit-distance rejection + closed membership); an entry-point-dependent graph (config as one constructed value + equivalence case); an unreviewed spec/parser divergence (the maintained V-table + clause-derived suite).

---

## 8. Uncertainties and stated gaps

- **The SMD lineage is asserted, not traced**: earliest reachable `.smd` in composeio is 2026-03-29; David places SMD's origin ~a year earlier in another project with a research paper behind it (`${OTHER_PROJECT_HOME}/research/` carries candidates). Which SMD guarantees failed to survive the SMD→LTS port — and whether deliberately — is unanswered here and is real due diligence for Era 1: **ask David or trace the the other project's data history; do not infer.**
- My all-roots key census (3,177/2,655) and the briefing's (1,438/1,143) differ by root-set and predicate; both are stated with predicates; neither is "the" number until the census predicate is ratified with the vocabulary amendment.
- SPEC.md was read in its normative-load-bearing parts (§0, §1, §2, plus targeted scans), not exhaustively line-by-line; §5-§17 may carry further V-table rows. The V-table is a seed, not a completion.
- Whether prose-bullets-that-look-like-keys are genuinely rare post-migration (§5's reopen condition) is predicted from the near-miss survey's 4% structure-shape rate, not measured directly.
- Population counts exclude fenced content and use the stated regex predicates; the true parser-eye-view counts belong to Era 0's instruments once landed.
