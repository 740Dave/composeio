# Build brief — profile-driven validation in `lts_lint`

**2026-08-11 · ComposeUX Architect · ratified by David: build this BEFORE parser conformance work**

---

## 1. The gap, in one sentence

**The profile data is already loaded and parsed; nothing consumes it.** `factlattice/profile.py`'s own
docstring says so:

> *"`raw` is the full parsed profile dict — richer consumers (**a linter**) read prefixes /
> relationship_verbs / enums / decision-node schema off it directly."*

That consumer was never written. `[PROFILE-CONTRACT]` admits the consequence plainly:

> *"profile-driven validation of prefixes, required properties, and enum values is specified here but
> **not yet enforced** by the shipped linter. A profile is currently documentation plus a namespace
> registration."*

## 2. Inventory — what is loaded vs. consumed

The only live machine-readable profile in the ecosystem is `${OTHER_PROJECT_HOME}/corpora/99-other-ds-profile.yaml`.
It declares **10 top-level keys. Two are consumed.**

| key | consumed today | by what |
|---|---|---|
| `domain.namespaces` | ✅ | `registered_ns` → parser |
| `relationship_verbs` | ✅ | `domain_verbs` → `_find_edge_type` |
| `prefixes` (7 entries) | ❌ inert | — |
| `document_tags` (3) | ❌ inert | — |
| `confidentiality` (3) | ❌ inert | — |
| `artifact_readiness` | ❌ inert | — |
| `decision_nodes` | ❌ inert | — |
| `decision_lifecycle` | ❌ inert | — |
| `mutation_nodes` | ❌ inert | — |
| `linter_guidance` | ❌ inert | — |

⚠ **`linter_guidance.notes` is a hand-authored warning about silent property drop** — one physical line
per property, the key regex, continuation lines discarded — written by the OtherProject Data Scientist *into a
field nothing reads*, in a system whose central defect is silent drop. It is the inventory's own
punchline and the clearest argument that inert profile hooks are not harmless.

⚠ **`decision_lifecycle` declares `pipeline: [proposed, committed, validated]` + `off_pipeline`.** This
is exactly the capability David asked for — *"show me decisions made but never implemented"* — declared
and never implemented. Wiring it is a deliverable, not a side effect.

## 3. ⛔ The architecture instruction that prevents rework

**Validation MUST read a typed `Profile` interface, never `profile.raw[...]` directly.**

David has ruled that profiles move from `99-{domain}-profile.yaml` to `{domain}-profile.lts`. Any
validator that reaches into the YAML dict shape (`raw["prefixes"][i]["prefix"]`) is coupled to a
serialization we have already decided to replace, and will need rewriting.

Add typed accessors to `Profile` and implement every validator against them:

```
Profile.prefixes            -> list[PrefixSpec]      (prefix, document, note)
Profile.document_tags       -> set[str]
Profile.enum_values(key)    -> set[str] | None       (confidentiality, artifact_readiness, …)
Profile.required_properties(section_type | prefix) -> set[str]
Profile.recommended_properties(...)                -> set[str]
Profile.lifecycle_stages    -> (pipeline: list[str], off_pipeline: list[str])
Profile.guidance_notes      -> list[str]
```

The loader then gains a second backend (`.lts` → the same accessors) with **no change to any
validator**. This is `[DX-NARROW-WAIST]` applied to the profile: one interface, two serializations.

## 4. Validators to build, each traced to its spec clause

| # | check | source | notes |
|---|---|---|---|
| V1 | node `type_prefix` is registered | §10.1 `prefixes`, `[PROFILE-CONTRACT]` | unregistered prefix |
| V2 | `document_tag` is core (§1.7) or profile-registered | §1.7 + `document_tags` | |
| V3 | required properties present per node family | `decision_nodes.required` | OtherProject: `rationale`, `lifecycle-position` |
| V4 | recommended properties present | `decision_nodes.recommended` | lower severity than V3 |
| V5 | enum-valued properties hold a declared value | `confidentiality`, `artifact_readiness` | |
| V6 | `lifecycle-position` ∈ pipeline ∪ off_pipeline | `decision_lifecycle` | **also yields the made-but-never-built query** |
| V7 | confidence block conforms | §7.1 | `value` float 0.0–1.0; `blast_radius` ∈ {low,medium,high} |
| V8 | `change` present ⇒ `context_for_change` carries `invalidating_assumption` | §8.1 | the load-bearing field |
| V9 | required frontmatter present | §1.5 | `scope`, `audience`, `boundary` — **`audience` ratified required by David** |
| V10 | density metric | §12.1 | ids+keys+refs per 1000 tokens; target 15–30; flag < 70% of max |

**V8 is currently unreachable** — `context_for_change` is authored as a YAML mapping per §8.1 and the
parser drops it (the dogfood failure). V8 must be built, but it cannot pass until the parser
conformance work lands. Build it; expect it dark; assert that in its fixture.

## 5. Severity policy — warnings first, and this is not negotiable

`[LAYER-LINT]`: *"Severity is linter policy, not parser judgement… an unreferenced node is routinely
legitimate, so making it fail a gate by default would train authors to disable the gate."*

Ten new validators against corpora that have never been checked will produce a large finding count on
day one. **Land every validator at `warning`. Measure. Triage. Promote to `error` per-validator, with a
recorded reason.** Landing at error fails every gate immediately and the gate gets switched off — which
is the failure `[LAYER-LINT]` explicitly warns about.

## 6. Acceptance — the two rules this project learned the hard way

- **Positive control per validator, asserting an EXACT count** (standing rule 3). A rule that reports
  zero looks exactly like a rule that works.
- **Agreement check** (standing rule 16). Each validator must describe what the ENGINE serves, not a
  re-derived model of it. Proof it matters: the collision detector fired 14× with a correct count and
  named the wrong file. Count-correctness and object-correctness are independent.
- **No second grammar.** Validators read the parsed graph and the `Profile` interface. They MUST NOT
  re-derive "would this have parsed?" — SPEC §2.5 names a duplicated grammar as a silent-drop
  mechanism, and `agents/factlattice-linter.md` is the 440-line proof (its property table lists ten
  edge-bearing keys; the parser has fifteen across two tables).

## 7. Test subject, and a dependency

**ZB (`${OTHER_PROJECT_HOME}/corpora`) is the only corpus with a loadable profile.** ComposeIO's corpora load
**none** — `lts_lint` returns `profile_domain: null, profile_path: null` — so these validators are
inert for ComposeIO until a profile exists.

That makes **authoring `platform-composeio-profile.lts` a prerequisite** for ComposeIO to benefit. The
13 existing `.lts` files under `domain-profiles/` are NOT profiles — 11 define zero nodes, and
`platform-composeio.lts` still describes a `.smd` corpus. They are prose conventions surveys and must
be re-authored, not migrated.

## 8. Order

1. `Profile` typed accessors (§3) — no behaviour change, pure interface
2. V1, V2, V5, V9 — the cheap structural ones
3. V3, V4, V6 — node-family requirements + **the decision-lifecycle query David asked for**
4. V7, V10
5. V8 — build it, expect it dark until parser conformance
6. Parity run against `agents/factlattice-linter.md`'s rule set, then **delete that agent** (§17.3
   table, BUILD-PROCESS, and the toolbox all reference it)

Then, and only then, the parser conformance work: P2 notations (YAML blocks, tables under a property
key), closing the §2.5 ignorable set, SL-16, SL-17.
