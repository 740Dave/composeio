# Integrating with ComposeIO & ComposeBus — which model, and when

You've built (or want to build) something that should talk to the rest of your ComposeIO
world — a sensor, a display, a storage service, a game, a whole simulation. Where should it
*live*? ComposeIO supports several integration models on purpose, and picking the right one
is mostly about **what kind of thing you're building** and **how you'll keep working on it**.
This page walks you through the choice.

> ⭐ **Canonical source.** This page is the friendly, human-readable view. The authoritative,
> queryable rubric lives in the specs as **`[SPEC:CXC-INTEGRATION-MODELS]`** — fetch it with
> lts-engine (`lts_node(corpus="intelligence-console", node_id="CXC-INTEGRATION-MODELS")`, then
> its child nodes). **If this page and the spec ever disagree, the spec wins** — tell us so we
> can fix the page.

---

## The one thing that's always true

Whatever model you pick, **you're a peer on the bus.** You publish and subscribe *namespaces*;
you never care whether they ride MQTT, CAN, UART, or in-process. The **contract** — the
namespaces and their shapes — is the fixed seam. *How* your thing is hosted is a delivery
detail that does **not** leak to the things consuming your state. So this choice is reversible:
you can change hosting later without touching anyone who reads you.

---

## Step 0 — the big fork: a *capability* or an *application*?

Ask this first, because the rest of the decision depends on it:

- **A platform capability / service** — something *other* parts of the system rely on: storage,
  a time signal, a fingerprint-template store, a printer gateway. → Use the **service rubric** below.
- **An end-user application / project** — a game, a home-automation setup, a domain simulation,
  your own creative thing. → Its home sits on a **use-case × complexity spectrum** (model it
  declaratively in the state-manager ↔ a standalone app on the bus ↔ a plugin). You pick along it
  from *your own* skill, your use of agentic AI, and how complex the project is — there are no hard
  thresholds. See "If you're building an application."

The difference matters because an application under active creative iteration wants **freedom and
a tight edit-run loop**, while a platform capability wants **managed lifecycle and reuse**. Those
pull toward different models.

---

## The models, in plain terms

| Model | What it is | Typical fit |
|---|---|---|
| **ComposeServer plugin** | Hosted inside a ComposeServer, per-tenant; you write one typed declaration and the platform derives its announce/registry, manages its lifecycle, and parks it loudly if a dependency is missing | The **default for a platform capability** that a host should run and others rely on |
| **Standalone process** | Your own process (a Python app/daemon) that connects to the bus as a peer | An **application you're iterating on**, or a capability that must span *multiple* servers / run on a *different* host / outlive the server |
| **In-process source** | A thread *inside* one app, not its own process | A data source only *that one app* uses and that lives and dies with it |
| **Bus-native peer / device** | A leaf on real silicon (an ESP32, a sensor, a display) riding the bus over any transport | Anything physical, or a widget that must run on constrained hardware |
| **Bridge** | Translates between two live transports (e.g. CAN↔MQTT) | Joining two transport worlds — *complementary* to the others, not an alternative |
| **Kernel built-in** | Part of the ComposeBus micro-kernel itself | Almost nothing — only an irreducible primitive. If you're unsure, it's **not** this |

---

## If you're building a capability/service — the rubric

Go down this list; take the **first** match:

1. **Runs on constrained silicon / is a physical device or cross-tier leaf?** → **bus-native peer.**
   (It can't be a Python plugin.)
2. **Purely translates between two live transports?** → **bridge.**
3. **A genuinely irreducible kernel primitive?** → **kernel built-in.** Default *no* — almost
   nothing qualifies; capabilities are plugins, not kernel.
4. **The only consumer is one app, tied to that app's session, and it needn't outlive it?** →
   **in-process source** of that app.
5. **Otherwise → ComposeServer plugin.** This is the default, and it supersedes the old "standalone
   bare-Python daemon" shape for anything a host should run.
   - Use a **standalone process instead only if it must**: span *multiple* tenants/servers (a plugin
     is always per-tenant, so cross-tenant work literally can't be one), run on a *different* host
     than any ComposeServer, or *outlive* the server's lifecycle.
   - When it is a plugin, set its **isolation floor** by fault-domain (run it in a subprocess if a
     crash or disk-fault must not take the server down), and declare what it **requires** so it
     parks loudly instead of degrading silently.

---

## If you're building an application/project

Your application's home sits on a **use-case × complexity spectrum**, and you choose along it from
*your own* skill, your use of agentic AI, and how complex the project actually is. There are
deliberately **no hard thresholds** — these are axes to weigh, not a cutoff to obey:

- **Model it declaratively in the state-manager** (no process, no code). A *first-class* path, not a
  fallback — modeling a house in the state-manager was one of the founding use-cases for
  state-manager itself. Reach for it when your domain fits a declarative model: **home automation (a
  Home-Assistant replacement), a simple game, a dashboard.** You express entities, state, and rules
  as config/bindings, and the platform runs them.
- **A standalone app on the bus** — when the logic *exceeds* what's natural to express
  declaratively: a complex structural simulation, real algorithmic physics, an evolving rule-engine.
  You get the full language, a debugger, and the tightest edit-run loop (a dry-run mode that needs no
  broker). A declarative config stretched this far quietly becomes a programming language — and a
  worse one.
- **A plugin** — when a finished application is ready to be *hosted as a managed platform tenant*
  (done iterating), not while it's under active creative development.

**How your skill + an AI shift the choice.** Agentic help moves the frontier *both* ways: an AI can
maintain a larger, more complex *declarative* model than a human would tolerate (so more stays
viable as state-manager config), **and** it can write and iterate a *standalone app* fluently (so the
code path costs you less). With agentic help your own coding skill matters less, and the decision
turns more on **which shape fits your domain** than on what you can personally hand-write. Either
way, an app's *algorithmic core* (if it has one) is code; its *authorable rules* (scenarios,
tunables) can sit on the declarative side.

**Two ends of the same spectrum — both applications:**
- **A house / Home-Assistant replacement, or a simple game** → *declarative state-manager config.*
  The domain fits a model; no process needed. (state-manager's founding use-case.)
- **The BSG world** (`compose_projects/bsg/world`) → *a standalone app.* It owns a real simulation
  (ship, fleet, physics), publishes semantic state (`dradis/*`, `game/*`) for stations and props,
  and is driven by a swappable *director*. Its crisis rules are structured Python today with a
  planned expression-DSL swap — the live code↔config boundary this guidance describes.

There's no bright line; your use-case, complexity, skill, and AI-assistance place you on the spectrum.

---

## Worked examples at a glance

| Thing | Class | Model | Why |
|---|---|---|---|
| House model / Home-Assistant replacement, simple game | application | **state-manager config** | domain fits a declarative model; no process needed — state-manager's founding use-case |
| BSG world simulation | application | **standalone app** | complex logic beyond declarative expression; needs full-language + a tight loop |
| Fingerprint template store / enrollment | capability | **plugin** | server-side, tenant-scoped, host-co-located; no cross-tenant need |
| `time` signal, storage monitor | capability | **plugin** | shared fleet facts a host should publish |
| `coordination_relay` | capability | **standalone process** | spans tenants/servers — *can't* be a per-tenant plugin |
| A DRADIS display, an ESP32 gauge | device | **bus-native peer** | physical silicon; rides the bus over its transport |
| CAN↔MQTT bridge | — | **bridge** | joins two transport worlds |

---

## See also
- Canonical rubric (queryable): `[SPEC:CXC-INTEGRATION-MODELS]` via lts-engine.
- The plugin contract: `[SPEC:CXC-CS-PLUGIN-CONTRACT]` and the ComposeServer micro-kernel spec.
- The placement principles this rubric extends: `[DX-SESSION-OWNED-VS-DISTRIBUTED]`,
  `[DX-LEAF-OWNS-BEHAVIOR]`, `[DX-COLLAPSE-TRANSPORT]`, `[DX-DERIVE-ONCE-REALIZE-PER-LEAF]`.

---

*This is the first document in `user_documentation/`; the directory will grow to cover the rest of
the platform's user-facing surface. Canonical, precise, queryable content belongs in the specs
(lts-engine); these pages are the readable doorway to it.*
