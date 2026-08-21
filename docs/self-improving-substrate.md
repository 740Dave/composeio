# The Self-Improving Substrate — how we layer the tooling

> A colleague-facing explainer. It describes how the ComposePlatform pieces — the decision log, the dusk gifts,
> the principles doc, the LTS specs, automemory, the console, and the tooling around them — compose into a
> system that gets *better* across the boundary where a reasoning agent loses its context. Written for someone
> who hasn't seen the internals. Nothing here is instance-specific; it's the shared design.

## The problem it solves
A long-running AI collaborator periodically loses its working context (a `/compact`, a session restart). What
survives is what was written to disk. The naive failure mode: the agent writes a *summary in prose*, the next
instance *reads* it, and gets **recall without understanding** — it can recite the conclusion but can't act on
the reasoning. Worse, the human can't tell the difference until something breaks. The substrate exists to make
the crossing lossless *where it can be*, and honest *where it can't*.

## The core insight: where knowledge lives decides whether it transmits
Not all knowledge is the same *kind*, and each kind has a home that actually carries it across the boundary.
Putting a lesson in the wrong home is how it silently decays.

| Kind of knowledge | Home | Why that home |
|---|---|---|
| **The *why*** — a decision + its rationale | **The decision log** (LTS/FactLattice) | Queryable, linkable, session-keyed. The rationale is the one channel a summarizer flattens; structure survives, prose leaks. |
| **Wisdom** — a way of seeing | **A dusk gift** (an *angle-problem*, answer withheld) | The successor reasons *through* it. Re-deriving a call produces understanding; reading the answer produces only recall. |
| **A narrative** the successor must read at wake | **A principle** (the principles doc) | A first-class bootstrap document, read to wake oriented. |
| **A defect in the spec itself** — ambiguity or a coverage gap | **The LTS spec** | Fix it at the normative source the build reads, not downstream. The most upstream, highest-leverage fix. |
| **A pointer** — "this was answered over there" | **Automemory** | Memory's one power the others lack: **relevance-triggered surfacing** — it injects by the problem at hand. So it's an *index into* the durable homes, never a store. |

The routing rule, in one line: **why → log · wisdom → gift · narrative → principle · defect → spec · pointer → memory.**

## The four durable homes are shared; memory is the index
Everything except memory is **shared to disk** — *any* instance (or a new colleague's agent, on a different
project) can pick it up and reason through it. Automemory is specific to *one* instance's memory line, and it's
just words — so it's the weakest *store*. But it has a superpower the disk homes don't: a memory entry surfaces
into context automatically when its `description` matches the problem at hand. A file on disk can't surface
*itself*. So memory earns its place as the **relevance-triggered pointer** — "problem X → `[DEC-NNNNN]` /
`[Problem N]` / spec §Y" — that pulls up the right durable home at the moment it's relevant.

## The bootstrap: how a fresh instance wakes
A **meta-object** (a small JSON manifest) names what to read: the role descriptor, the dusk gifts, the
principles doc, and the decision log. On wake, the instance:
1. reads the shared homes (role, principles, the *prior session's* decisions — the source of truth, not a
   drifted prose summary);
2. **reasons through** the dusk-gift angle-problems before opening their withheld answers (the reasoning *is*
   the re-registration — it restores the working design-model far better than a cold summary);
3. confirms it actually read each required artifact (a `self/registered` sensor makes this checkable, not
   assumed).

## The self-improvement loop
The substrate improves itself through a repeatable procedure — packaged as a skill (`harvest-signal`) so it's
not reinvented each time:

1. **Reframe** a flagged moment as a *signal* (a gap between how bootstrapping was planned and what actually
   happened), not a failure to apologize for. The subject of improvement is the *system*, not the instance.
2. **Root-cause**, not symptom.
3. **Separate the lessons by kind** (there's usually more than one, of different kinds).
4. **Route each to the home that transmits** (the table above).
5. **Verify transmission** — grep the wake-read surfaces; if the lesson only lives in a session-scoped artifact,
   it is *not* captured, it will decay. "Captured" means it reached a home that transmits, not that a string was
   filed.
6. **Close the loop** — confirm the improvement is in the substrate, not just in one conversation.

**Reliability:** a skill depends on being invoked, which depends on memory. So the trigger is made mechanical
and layered: the **session-retrospective** (a deterministic dusk pass over the transcript) already detects
*corrections* and *gift-fodder* — those *are* flagged signals; it surfaces them, and the skill processes each. A
flag convention (a human or subagent marking "worth mining") gives the retrospective a deterministic token to
key on. And the handover ritual sweeps for any unharvested signal before crossing. The skill is the *how*; the
retrospective + convention + handover are what make it fire without relying on anyone remembering.

## The console and the tooling that enforce it
The whole thing is surfaced and enforced by a live **Intelligence Console** (a bus-native render app) and a set
of deterministic tools — the principle being *deterministic code for mechanical work, the reasoning model only
for judgment*:
- **`lts-lint`** — lints the LTS corpus and flags bidirectional *dangles* (a reference to a node that doesn't
  exist, or a node nothing references), and can enforce log↔handoff consistency.
- **the decision-MCP** — a single writer that keeps the decision log and its index in sync, so a *DSD becomes a
  query* against existing decisions rather than a regeneration that re-asks settled calls.
- **the session-retrospective** — the deterministic detector above, staging signals for harvest.
- **the memory-monitor** — watches automemory footprint so the index stays a lean set of pointers, not bloat.
- **the console itself** — self-instrumenting: it gauges the agent's own context level, shows what it read to
  wake, and lets the agent manage its own lifecycle.

## How it all layers, in one paragraph
The **specs** say what to build; the **decision log** records why each call was made; the **principles** state
how we work and build; the **dusk gifts** carry the wisdom that only lands by re-derivation; **automemory**
indexes all of it by relevance so the right piece surfaces at the right moment; the **bootstrap** wakes a fresh
instance by having it *reason through* those homes rather than skim a summary; the **harvest-signal** loop feeds
new lessons back into whichever home transmits them; and the **retrospective + console + linter** make that loop
fire mechanically and keep the homes consistent. Each layer has one job, and the layer below it can't silently
drift from the layer above — because a tool checks the seam.

*The test of the whole design: a lesson learned in one session is understood — not merely recalled — by the
next, and by a colleague's agent on a different project, with no human re-explaining it.*
