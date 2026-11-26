# PREP — title & key discipline for the FactLattice core spec + design-excellence

> HANDOFF PACKAGE for the composeio architect. HELD by David's instruction until it's back from its
> self-directed compact. Origin: David's 2026-08-18 critique of the R503 `70`-file model-test outputs —
> Fable's titles ROUTE ("The packet", "Behaviour while Busy is undefined") while Sonnet/Opus/original
> titles SATISFY ("Big-endian, address-gated; Length counts the checksum"), which spoils the body in the
> digest tier. Two rules fall out. Both go in the **FactLattice core spec** (so `factlattice-editor` inherits
> them on its next invocation via its hard gate) AND, duplicatively, in **design-excellence**.
>
> Deconfliction: both targets are composeio-side corpora. Per the branches-only + heads-up protocol, land on
> a branch with a heads-up to the composeux-architect (this session) before any main op.

---

## 1. FactLattice core spec — normative prose (place in the node/title/property discipline section)

### Title discipline — a title routes, it never satisfies

A node's title NAMES its subject; it does not STATE its content. Because the digest response tier returns the
title (and structural counts) but nothing from the body, a title that packs the body's fact —
*"Big-endian, address-gated; Length counts the checksum"* — hands a reader working in digest a false sense of
completeness: they take the title as the answer and never fetch the node, losing everything the body carries.
Write titles that ROUTE — *"The packet"*, *"Data-packet bursts"*, *"Behaviour while Busy is undefined"* —
carrying enough to decide whether to fetch and never enough to skip. This is the title-side of two rules already
in force: the identifier discipline (an id names a subject, never a verdict) and the response-tier contract (the
cheap tier routes, it never satisfies). **Tell:** a title a reader could cite without opening the node.

### Property-key discipline — a key names a field

A property key NAMES A FIELD: an orthogonal, reusable dimension of the subject — not a sentence, not a one-off.
Prefer several decomposing keys — `shape:`, `byte-order:`, `address-gating:` — over one prose `description:`
blob: each names a dimension a reader or a query can request independently, and a key set that recurs across the
corpus is what a domain profile can declare and validate. A sentence-shaped key
(*"why-the-estimate-was-not-wrong-when-it-was-made"*) is content wearing a field's clothes; its value belongs
under a reusable field and the sentence belongs in that value. **Tell:** a key used exactly once, or a key that
reads as a claim.

---

## 2. design-excellence — two DX nodes (terse DX style: one edge-decision rule + the locating question)

### [DX-TITLE-ROUTES-NEVER-SATISFIES] A node title routes to the body; it never satisfies in the digest

- rule: A title NAMES the subject, never STATES the content. The digest tier returns the title and nothing from the body, so a title that packs the fact hands a digest reader false completeness — they stop at the title and never pull the node. "The packet" / "Behaviour while Busy is undefined", never "Big-endian, address-gated; Length counts the checksum".
- locating-question: could a reader cite this title without opening the node? If yes, it satisfies — rewrite it to route.
- why: the title-side of [LTS-TIER] (the cheap answer routes, never satisfies) and of the identifier-names-a-subject-not-a-verdict rule; both make the short form self-sufficient so the body goes unread.

### [DX-KEY-NAMES-A-FIELD] A property key names a reusable field, never a sentence

- rule: A key NAMES an orthogonal, reusable dimension of the subject. Decompose into keys that recur (shape / byte-order / address-gating), not one prose blob and not a one-off sentence-key. A small reused key set is what a domain profile can declare.
- locating-question: is this key used exactly once, or could you read it as a claim? Either way it is content in a field's clothes — move the content to the value under a reusable field.
- why: keys are the queryable structure; a sentence-key is unqueryable and unprofiled — [STRUCTURE-SURVIVES-THE-BOUNDARY], prose leaks and structure survives.

---

## Worked example to attach (both homes): the R503 `70` busy node

The discipline has a live worked example from the model test — the busy-behaviour gap:
- ROUTES (Fable): `### [CXC-R503-BUSY] Behaviour while Busy is undefined` — you must pull it to learn the module has a Busy flag + Cancel and that three transaction layers hang off the undocumented answer.
- SATISFIES (Sonnet): `### [CXC-R503C-BUSY-BEHAVIOUR-UNDEFINED] What happens to a command sent while the module is Busy is undocumented` — the fact is in the title; a digest reader skips the body and misses the consequence.
Same subject, opposite discoverability. Attach as the canonical illustration.
