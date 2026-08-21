# Instance Isolation: a model for transcript & memory co-location

> A general architecture model for any system that runs **multiple long-lived AI agent instances
> on one machine** and finds their transcripts and memory bleeding into each other. It names the
> problem, explains *why* co-location happens, states the isolation principle, and sketches the
> migration. It is deliberately **implementation-agnostic** — the failure is structural, so the
> fix is structural, and both generalize across harnesses.

## 1. The problem, precisely

Run several agent instances on one host — a builder, an architect, a reviewer, a data-scientist —
and two failures show up, often mistaken for bugs in the agents themselves:

- **Cross-talk.** Instance A's console/UI shows instance B's messages; A's "context remaining"
  meter reads B's number. A critical instrument is *plausibly wrong* rather than obviously broken,
  which is the worst failure shape — every decision built on it is quietly suspect.
- **Amnesia / drift.** An instance's own memory doesn't accumulate the way you expect: notes it
  wrote "yesterday" aren't there today, or the same fact exists in three slightly different places.

Both trace to **one root**: the instances' durable per-session state — the **transcript** (the
conversation log) and the **memory store** (persisted notes) — is being written to a location that
is *not uniquely determined by the instance's identity*. Either two instances land in the **same**
location (cross-talk), or one instance lands in a **new** location every run (amnesia).

## 2. Why co-location happens: the scope key

Almost every agent harness derives "where does this session's state live?" from some **scope key** —
most commonly **the working directory (cwd)** the process was started in. The harness sanitizes the
cwd into a folder name and puts the transcript and memory there:

```
cwd  →  <sanitized path>  →  ~/<harness-state>/projects/<sanitized path>/{transcript.jsonl, memory/}
```

This one key silently governs **four** things at once: the transcript location, the memory store,
the status/telemetry surface, and (often) agent/skill discovery. That coupling is the leverage — and
the hazard. **Whatever you choose as the scope key is your isolation boundary, for all four, whether
you meant it to be or not.**

The trap: the *convenient* default cwd is a **shared, coarse** location — the user's home
directory, or a common project root you `cd` into. Every instance launched from there resolves to
**the same key**, so they co-locate. Nothing warns you; the folder just accumulates N instances'
worth of interleaved state. The "which transcript is mine?" question exists **only because** more
than one instance shares a key. Remove the sharing and the question is moot.

### The scope key must be keyed *right*, not just isolated

There are **two** ways to get the key wrong, and they fail in opposite directions:

| Failure | Key is… | Symptom | Example shape |
|---|---|---|---|
| **Co-location** | too **coarse** (shared) | cross-talk: N instances in one store | launch every instance from `~` or one repo root |
| **Fragmentation** | too **fine / unstable** | amnesia: one instance scattered across N stores | put a **timestamp or session-id in the path**, so every launch is a new store |

Co-location and fragmentation are the **same disease** — a mis-keyed scope — presenting as fever vs
chills. Fragmentation is the sneakier one: it *looks* like good isolation (each session is separate!)
while quietly destroying continuity, because an instance's memory should persist **across** its
sessions and instead splinters by time. A team that "fixed" co-location by making the key *more*
unique can land straight in fragmentation.

The right key is **neither** the shared default **nor** the session. It is the **instance's stable
identity**.

## 3. The isolation principle

> **One instance, one home. Transcripts and memory never co-locate — and never fragment.**
> Boot each instance from a working directory keyed by its **stable role/identity**, so the harness
> scopes all per-session state to that instance: unique across peers (no co-location) and stable
> across its own sessions and across every project it touches (no fragmentation).

Three properties the identity key must have, in tension, all required:

1. **Unique per instance** — distinct instances get distinct keys ⇒ no cross-talk. *(defeats co-location)*
2. **Stable across sessions** — the same instance gets the **same** key every launch ⇒ memory
   accumulates. *(defeats fragmentation — this is the property a timestamped path lacks)*
3. **Stable across projects/workspaces** — a single instance often works across many repos; if the
   key is the *project*, it re-fragments the moment the instance switches projects (the same
   shared-cwd bug in new clothing). So the key is the **instance**, not its current project. Project
   file access is granted **separately** (mount the repos as *additional* working directories), which
   is an access concern, not an identity concern.

Concretely: give each instance a home like `~/agent-homes/<role>/` (`.../architect/`,
`.../widget-builder/`). The harness keys transcript + memory + telemetry + tooling to `<role>`,
stable forever; the projects the instance edits are mounted as extra directories.

**Isolation is a property of the launch, not a property of the reader.** You do not make it safe by
teaching every consumer to filter for "its own" records after the fact — that is a band-aid that
launders the co-location and re-breaks the instant a new launch path forgets to filter. You make it
safe by **construction**: distinct homes ⇒ distinct stores ⇒ there is nothing to filter.

## 4. Defense in depth (keep the band-aid, demote it)

A read-time disambiguation guard is still worth keeping as a **second** layer:

- **Pass identity, never guess it.** When state *must* be selected (e.g. a UI attaching to a
  transcript), the identity is **passed in** at launch, never inferred by a heuristic like
  "newest-modified file wins" — newest-wins picks a concurrent sibling exactly when two instances
  coexist, which is precisely when it matters.
- **Fail loud, never plausibly wrong.** If the pin is missing and the target is **ambiguous**, the
  consumer must **refuse and announce** ("cannot identify my own transcript") rather than silently
  render a sibling's. An instrument earns trust by making silent-wrong impossible.

But keep the ordering honest: **structural isolation is the root fix; the guard is defense in
depth.** If you only ship the guard, the co-location still happens and you are forever one forgotten
pin away from the leak.

## 5. The migration shape

You rarely get to cut over atomically, because of one hard constraint most harnesses share:

> **A resumed session re-pins to the directory it was originally created in.** Moving the *launcher*
> doesn't move an *existing* session's home.

So the migration is staged, not flipped:

1. **New instances are born isolated.** Any instance *cold-started* from its identity home is
   correct from birth. Adopt the pattern for all new instances first — zero risk to running ones.
2. **The mechanism is opt-in and reduces to the identity.** The isolated-home launch path must be a
   **no-op** for a legacy instance (default = prior behavior) so nothing in flight changes until you
   choose. When the home *is* the old shared location, every derived path must compute to the **same
   value** it does today — a strict superset, byte-identical in the legacy case.
3. **Enforce isolation by construction, in fleet mode.** The spawner/launcher for managed instances
   should **refuse** to place an instance with **no** home (it would inherit the shared pile) and
   **refuse** two distinct instances resolving to the **same** home. Same instance re-entering its
   *own* home is fine — that is continuity, not co-location.
4. **Anchor the key derivation to ground truth.** Whatever function models "cwd → state folder"
   should be **tested against real on-disk folders the harness actually created**, so a change in the
   harness's sanitization rule fails a test instead of silently drifting.
5. **Legacy instances cut over deliberately.** An existing shared-home instance stays put until it is
   **re-created** cold from its new home, and its accumulated memory is **migrated** into the new
   store as part of that crossing. Because a cold start carries no live context, that crossing is a
   human-chosen moment, not an automatic one.

## 6. Checklist

- [ ] Identify your harness's **scope key** (usually cwd) and everything it governs (transcript,
      memory, telemetry, tooling).
- [ ] Confirm your failure mode: **co-location** (shared key) or **fragmentation** (session/timestamp
      in the key) — or both, on different axes.
- [ ] Define a **stable per-instance identity** and a home path keyed by it — not the project, not the
      session.
- [ ] Grant project/workspace access as **additional** mounts, decoupled from identity.
- [ ] Make the isolated-home launch **opt-in and byte-identical** in the legacy case.
- [ ] **Enforce** by construction in fleet mode: no homeless managed instance; no two instances
      sharing a home.
- [ ] Keep a **pass-not-guess + fail-loud** read-time guard as defense in depth.
- [ ] **Anchor** the key-derivation model to real harness output in a test.
- [ ] Stage the migration; cut legacy instances over deliberately, migrating their memory.

---

*This model was extracted from a concrete fix ("instance-keyed homes") in the ComposeIO/ComposeUX
platform, generalized for reuse. The platform-specific realization (the spawner contract, the
launcher change, the enforcing test) lives in that project's specs under `[CXC-IH-HOMES]`; none of it
is needed to apply the pattern here — the model above is self-contained.*
