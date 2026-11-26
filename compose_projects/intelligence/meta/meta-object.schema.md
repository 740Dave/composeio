# Meta-object schema — `intelligence/meta/<name>.meta.json`

The **stable identity** an Intelligence bootstraps from and a successor re-registers against
([SPEC:CXC-INTELLIGENCE-CONSOLE] · node [CXC-IC-META]).

This file is the target of the `$schema` pointer in every meta-object. It was referenced but
absent until 2026-08-08; written from the live `architect.meta.json` plus its actual consumers.
Extended 2026-08-11 to describe the **ROLE** form as well as the instance form.

**All paths are repo-relative** — the launcher supplies the root — so the object is portable.

## Two kinds of meta-object — INSTANCE and ROLE

There are **two** manifest kinds, and they are two legs of one resolution chain
([SPEC:CXC-TERM] · node [CXC-TERM-MANIFEST-CHAIN]). Both use the schema below; they differ in
what `<name>` means and in which fields may appear.

| leg | file | `<name>` is | what it is |
| --- | --- | --- | --- |
| 1 | `$META_MANIFEST` | — | an explicit operator override; unchanged |
| 2 | `intelligence/meta/<instance_id>.meta.json` | the **instance id** | a manifest curated for THIS running instance. `architect.meta.json` is the live one. Runtime-written (Config tab, viewer launchers) |
| 3 | `intelligence/meta/<role>.meta.json` | the **role** | what ANY instance wearing this role orients from when no leg-2 file exists. `composeux-architect.meta.json` is the first one. Hand-authored; never runtime-written |
| 4 | — | — | exhausted → **REFUSE**. No fork, no degraded boot |

Leg 3 is what makes N instances of one role possible at all: give every hosted child an id
distinct from its host's ([CXC-TERM-CHILD-ID]) and leg 2 misses for every child nobody
hand-curated.

### The naming rule — a role's name is the BASENAME of its role descriptor

`.claude/agents/composeux-architect.md` → role `composeux-architect` →
`intelligence/meta/composeux-architect.meta.json`.

This makes leg 3 **mechanically derivable** from an artifact that has to exist anyway (the
descriptor the manifest already points at) instead of a convention someone maintains — and
conventions are what drifted into the defects of 2026-08-11. A new agent file mints its role
name and its manifest path with no registry to keep in sync.

Short hand-chosen role names are **rejected**. The previous bare `architect` is a fleet-wide
collision, not merely a vague label: a live `other-architect` session runs on this machine, and
`architect` names no particular descriptor. `intelligence/meta/architect.meta.json` remains the
live host's **instance** manifest (leg 2, `id` == `architect`); it is not renamed, not moved and
not superseded by the role file.

### The instance id WINS — the manifest supplies artifacts, not the name

`build_bootstrap_prompt(manifest, *, instance_id="")` names the peer

```python
ident = instance_id or manifest["id"] or "intelligence"
```

so **`manifest["id"]` is the FALLBACK only** — for a caller with no instance to name (a
hand-built manifest, a preview render). `build_bootstrap_for(intelligence_id, …)` threads its
instance id through; before 2026-08-11 it used that id only to resolve the manifest PATH, and
the prompt took its name from the manifest.

Why it matters the moment leg 3 works: a child running as `architect-hosted` and orienting from
`composeux-architect.meta.json` would have been told *"You are the Intelligence peer
composeux-architect"* while its channel topics, its `intelligence-launcher-<id>` lock, its
`self/<id>/*` telemetry and its refusal publish all say `architect-hosted` — it would post to a
channel under a name nobody told it it had.

> **Why this was invisible.** Leg 3 had never once been exercised with a real ROLE manifest.
> Only `architect.meta.json` existed — an instance manifest whose `id` happens to equal its role
> — so the two derivations of the child's name agreed **by accident**, and no test could tell
> them apart. Minting the first role manifest is what exposed it. *An agreement that holds by
> coincidence is not an agreement, and a leg never exercised is not a leg that works.* Any
> fixture for a two-derivation agreement must make the two inputs **differ**.

## Who reads and writes it

| actor | access | what for |
| --- | --- | --- |
| `launcher.py` → `load_heartbeat_cadence` | read, **every launch** | `lifecycle.heartbeat` (minutes → seconds) |
| `ptycore.build_bootstrap_prompt` | read, only when `BOOTSTRAP=1` | `role_descriptor` / `soul` / `dusk_gifts` paths, `channel`, `lifecycle`. Takes `instance_id=` and uses `manifest["id"]` **only as a fallback** |
| `ptycore.resolve_meta_manifest` | read (existence) | walks the chain above and returns `(path_or_None, tried)`; the caller REFUSES on `None` |
| `config_persister` (console source) | **write** | folds Config-tab edits into `lifecycle`. **Instance manifests only** — never a role file |
| `decisions_viewer_launcher` | **write** | `window_geometry.<window>` on move/resize. **Instance manifests only** |
| `meta_publisher` (console source) | read | projects the whole object (minus `$schema`) to `self/meta` |

> Two independent writers share this tracked JSON. A geometry write on every window move keeps the
> file permanently dirty in `git status`. If that becomes noisy, split geometry into an untracked
> sidecar (`<id>.geometry.json`) and leave the meta-object holding only durable identity + lifecycle.

> **Every writer above writes an INSTANCE manifest.** A role manifest is hand-authored and is
> never a write target: per-instance state written into a role file would be inherited by every
> instance of the role, which is the displacement defect [CXC-TERM-CHILD-ID] closed.

## Fields

### Required

| field | type | meaning |
| --- | --- | --- |
| `id` | string | **instance manifest:** the bus id — topics (`intelligence/<id>/…`, `self/<id>/*`) and the launcher's `INTELLIGENCE_ID`. **Role manifest:** the ROLE's name, which is NOT what a child is told it is — see *the instance id WINS* above |
| `role_descriptor` | artifact-ref | the operational role definition. **The only artifact the bootstrap prompt requires.** Its **basename is the role name**, and therefore the role manifest's filename |

### Identity

| field | type | meaning |
| --- | --- | --- |
| `role` | string | short human label (e.g. `"ComposeUX / ComposeIO Architect"`) |
| `description` | string | what this meta-object is and what it is for |
| `soul` | artifact-ref \| `null` | the stable identity across seams, distinct from the operational role. `null` is valid. |
| `soul_note` | string | why `soul` is null, when it is |

### Bootstrap artifacts

Each is an **artifact-ref** and each is optional; a missing one is simply skipped when the bootstrap
prompt is built (section numbering is dynamic).

| field | `role` value | meaning |
| --- | --- | --- |
| `principles` | `principles` | the shared collaboration + design principles |
| `method_flow` | `method` | how a project actually flows |
| `bootstrap_read` | `bootstrap_read` | the strategic wake read — what to query for current state |
| `toolbox` | `toolbox` | what you can reach for, and when |
| `dusk_gifts` | — | **array** of artifact-refs; the warm-calibration set, read in order |

#### artifact-ref

```jsonc
{
  "path": "bootstrap/toolbox.md",   // REQUIRED, repo-relative
  "why":  "…",                       // REQUIRED — why a successor should read it
  "role": "toolbox",                 // optional classifier
  "standalone_ux": true              // optional: surface as its own console doc
}
```

A `dusk_gifts` element additionally uses `role` as a free-form slot label (`keystone`, `theory`,
`relationship`, `supporting`).

> **Carry-depth-by-reference.** `build_bootstrap_prompt` emits these **as paths and never inlines
> their contents** — it opens no artifact file. The load-bearing state lives in the committed
> artifacts; only re-reading them rebuilds it. This is also why the function never touches a
> `.claude/` path: such paths are carried through as plain text for the running instance to read.

### `channel`

| field | type | meaning |
| --- | --- | --- |
| `server` | string | MCP server name — always `composeio-channel` |
| `mqtt` | string | broker URL |
| `topics` | object | the resolved topic map (informational). **Instance manifest:** the literal names. **Role manifest:** the SHAPE only — `intelligence/<instance_id>/in` — never a literal |
| `topics_note` | string | **role manifests:** why the map is a shape. Required whenever `topics` is not literal |

> **Topics are per-INSTANCE, not per-role.** A role manifest spelling the live
> `intelligence/architect/in` would hand every instance of the role the host's own topics. The
> live derivation is `channel.mjs`'s, off `INTELLIGENCE_ID` — the running instance's id.

### `lifecycle`

Where `config_persister` writes Config-tab edits. **In a role manifest these values are the
role's DEFAULTS**; per-instance overrides are written into a leg-2 instance manifest, never into
the role file.

| field | type | meaning |
| --- | --- | --- |
| `compact_threshold` | number (0–1) | the self-directed `/compact` trigger point |
| `heartbeat` | object | `{base, backoff_after, then}` — **MINUTES**. The launcher converts to seconds and re-reads them every check, so an edit takes effect with no restart. Env (`HEARTBEAT_*_S`, in seconds) overrides. Absent ⇒ coded defaults 10/60/60 min. |
| `retention_days` | number | console retention setting |
| `note`, `config_note` | string | free-form provenance |

> **Unit trap:** this file is in **minutes**; every env override is in **seconds**.

### Console / presentation

| field | type | meaning |
| --- | --- | --- |
| `registration` | object | `{definition, required}` — what counts as re-registered |
| `console` | object | `{tabs, dashboard, theme_custom, scroll_direction}`. **INSTANCE-ONLY** |
| `window_geometry` | object | `{<window-id>: {x, y, w, h}}`, written by the viewer launchers. **INSTANCE-ONLY** |

## Instance-only fields — what a ROLE manifest must NOT carry

A role manifest is derived from an instance manifest **key for key, with every `why` carried**,
and then the instance-specific parts are removed or neutralised. Anything below is per-instance
state; in a role file it would be silently inherited by every instance wearing the role.

| field | in a role manifest | why |
| --- | --- | --- |
| `window_geometry` | **DROP** | per-instance UI state the viewer launchers write; meaningless for a role |
| `console` | **DROP** | per-instance UI state the Config tab writes |
| `channel.topics` | **NEUTRALISE** to `intelligence/<instance_id>/…` + a `topics_note` | a literal map hands every instance the authoring host's own topics |
| `lifecycle.*` | **KEEP as role DEFAULTS** | overrides belong in a leg-2 instance manifest; `config_persister` must never write here |
| `id` | **KEEP**, naming the ROLE | it is a required key and it names the role legitimately — it is simply not what the child is called |

Everything else — `role`, `description`, `role_descriptor`, `soul`/`soul_note`, `principles`,
`method_flow`, `bootstrap_read`, `toolbox`, `dusk_gifts`, `channel.server`, `channel.mqtt`,
`registration` — is role-level by nature and carries across unchanged.

## Minimal valid object

An **instance** manifest (leg 2) — `intelligence/meta/architect.meta.json`:

```json
{
  "$schema": "./meta-object.schema.md",
  "id": "architect",
  "role_descriptor": { "path": ".claude/agents/composeux-architect.md", "why": "the operational role" }
}
```

A **role** manifest (leg 3) — `intelligence/meta/composeux-architect.meta.json`. The filename is
the basename of `role_descriptor.path`, and `id` repeats it:

```json
{
  "$schema": "./meta-object.schema.md",
  "id": "composeux-architect",
  "role_descriptor": { "path": ".claude/agents/composeux-architect.md", "why": "the operational role" }
}
```

Note what the second one does **not** have: no `window_geometry`, no `console`, and — once it
grows a `channel` block — no literal topic names.

See [`../launcher/architect.meta.sample.json`](../launcher/architect.meta.sample.json) for a fuller
template, `architect.meta.json` for the live instance object, and
`composeux-architect.meta.json` for the live role object.

## Change record

- **2026-08-11** — extended to describe BOTH manifest kinds. The ROLE manifest (chain leg 3) is
  recorded as a distinct artifact kind; the basename naming rule is stated; the instance-only
  fields are enumerated; and `build_bootstrap_prompt`'s **instance-id-wins** semantics are
  documented, with the mechanism that hid the defect (leg 3 had never been exercised with a real
  role manifest, so two derivations of one name agreed by coincidence). Spec side:
  [SPEC:CXC-TERM] · [CXC-TERM-MANIFEST-CHAIN], [CXC-TERM-BOOTSTRAP-DERIVED],
  [CXC-TERM-CHILD-ID], acceptance TERM-C19, change record
  [CXC-TERM-CHANGE-4-ROLE-NAME-AND-INSTANCE-ID].
- **2026-08-08** — written, from the live `architect.meta.json` plus its actual consumers.
