# BSG World-Model Redesign Notes

**Status:** DESIGN REVIEW — not a code change. Captured for a future rebuild so the
world model has a good spec before anyone refactors. Read-only pass over `~/maker/bsg`
(`/Users/davidleeburnett/Dropbox/Maker/bsg`).

**Author's stance:** opinionated. The current `world/` is a well-factored *state
machine + physics substrate*, but its **data model is hotwired for a single-ship,
single-planet demo**. It will not carry the multi-ship / sub-ship-inventory / Raptor
future without a data-model rebuild. The good news: the *seams* (command seam, sensor
fog-of-war, publish contract, director-agnostic loop) are excellent and should survive
the rebuild almost untouched. The rebuild is an **entity-model** rebuild, not a
loop/seam rebuild.

**Key reconciliation up front:** `spec/11-dradis-physical-model.lts` already specifies a
much richer *physics* target (absolute positions + velocities, fuel/delta-v budgets, a
full ship roster, Raptor picket doctrine, civilian-fleet aggregate). That spec is the
**physics/units layer**. David's target perspective in this review is the
**entity/object-model layer** (a ship registry, hierarchical sub-ship inventories,
per-entity namespaces, own-ship as a first-class Ship, systems with N coordinate-bearing
planets). These are complementary, not competing: spec-11 says *what numbers a ship
has*; this redesign says *what a Ship IS as an object and where it lives on the bus*. The
code today implements **neither** — it implements the earlier hotwired demo. Where spec-11
already anticipates a target field (position, velocity, fuel), I note it as "spec ahead of
code" rather than a new idea.

---

## 1. Current-state map

For each concept: how it is represented **today**, and its nature (first-class entity /
`World` field / hotwired scalar / ephemeral vs persistent). All citations are
`file:line` in `~/maker/bsg`.

### 1.1 Own-ship (Galactica)

**NOT an entity. It is a scatter of `World` fields + a lone published scalar — the P1 god-object smell.**

- Galactica has **no object**. Own-ship exists only as: scope-center-by-convention
  ("range 0"), plus a handful of `World` fields.
- `World.own_heading` — `entities.py:213`, published as `game/own_heading`
  (`publish.py:49`). Seeded at the planet bearing (`sim.py:89-90`, `sim.py:303`),
  integrated each tick from the helm differential (`sim.py:372-374`).
- `World.thrust_left` / `World.thrust_right` — `entities.py:214-215`; standing stick
  STATE written by `_apply_thrust` (`sim.py:335-349`) from `throttle/left|right`. This is
  **the interim throttle-helm shortcut David flagged**: it drives `game/own_heading`, not
  a ship entity. The vendored kinematics live in `helm.py` (`heading_rate`, `helm.py:59`).
- `World.civilians` (int, `entities.py:184`), `World.vipers` (posture string,
  `entities.py:185`), `World.jump_state` (`entities.py:183`) — all own-ship/fleet concerns
  smeared across the god-object.
- The helm docstring itself concedes the target: "The throttle helm should drive
  `ship/galactica/thrust`" — today it drives `World.thrust_left/right` and
  `game/own_heading` (`helm.py` header + `entities.py:209-215`).

**Nature:** persistent-per-`World` fields; NOT an entity; heading is a hotwired scalar.
This is exactly the "complexity pooling in the World god-object" smell.

### 1.2 The CAP Vipers

**Flat, ephemeral, re-spawned-each-jump `ViperContact` list. No inventory, no persistence, no hangar.**

- `ViperContact` dataclass — `entities.py:158-174`: `id`, `label`, `bearing`, `range_ls`,
  `mode` (launch/orbit/intercept/return/landed), transit snapshots, `target_id`. It is a
  **motion record**, not a ship: no fuel, no weapons, no airframe type (Mk-II vs Mk-VI),
  no pilot.
- Held in `World.viper_contacts` (`entities.py:196`), **re-created wholesale every jump**
  by `_spawn_cap_vipers` (`sim.py:156-173`), which throws away the prior list
  (`w.viper_contacts = []`, `sim.py:161`). Count is a config scalar `viper_count = 2`
  (`entities.py:108`).
- Labels `VIPER NNN` are 3 random digits regenerated each spawn (`sim.py:166`) — **so a
  Viper has no stable identity across jumps**. There is no notion of "this airframe was
  lost / rearmed / refueled."
- All CAP behavior (orbit, redirect-on-ID smaller-first, recall) is engine-managed physics
  in `_advance_vipers` (`sim.py:208-242`), keyed off `mode`.

**Nature:** ephemeral; per-engagement; NOT persistent entities; no inventory. This is the
flat model David wants to generalize into a typed sub-ship hierarchy with inventory.

### 1.3 Cylon contacts

**World-private ground-truth `TrueContact` list; hardcoded 2-contact spawn; no inventory.**

- `TrueContact` dataclass — `entities.py:145-155`: `id`, `true_type`, `size_class`,
  `bearing`, `weave`, `range_ls`, engine-assigned `label`. Ground truth the engine holds;
  the sensor derives the sensed view.
- `World.cylons` list (`entities.py:195`), spawned by `_spawn_cylons` (`sim.py:144-153`) as
  a **hardcoded pair**: one `basestar`/large + one `raider_squadron`/small, both at
  `planet_bearing ± const`, range = `cylon_spawn_ls` (60 LS).
- Range is projected by a shared closing formula `_cylon_range` (`sim.py:418-421`) — a
  scalar interpolation from spawn range to hold range, identical for every contact.
- Per-engagement label counters (`baseship_count`, `squadron_count`, `heavy_count` —
  `entities.py:200-201`, reset each jump because `World` is rebuilt) drive names in
  `_assign_enemy_label` (`sim.py:264-276`).

**Nature:** ephemeral ground-truth records; hardcoded roster; no fuel/weapons/hierarchy
(a basestar does not "own" its raider squadrons as sub-ships — they are siblings in a flat
list). The Cylon side has the **same** flat-vs-hierarchical gap as the Viper side.

### 1.4 Star systems

**Frozen dataclass with baked-in single-star + single-planet scalars. This is the hotwiring David suspected.**

- `System` dataclass — `entities.py:40-50`, **`frozen=True`**: `name`, `star_name`,
  `star_type`, `star_bearing`, `planet_name`, `planet_type`, `planet_bearing`, `x_ly`,
  `y_ly`.
- A system has **exactly one star and exactly one planet, each as a bearing scalar**
  (`star_bearing`, `planet_bearing`) — not a body object, not a position, not a list.
- The galaxy is a hardcoded module-level list `GALAXY` of 8 systems (`entities.py:57-66`)
  with `SYSTEMS_BY_NAME` index (`entities.py:67`).
- `x_ly`/`y_ly` are the **only real coordinates in the model** — but they are
  *inter-system* galaxy positions used solely for jump distance (`distance_ly`,
  `entities.py:70-72`). They do NOT position bodies within a system.

**Nature:** persistent (module constant); frozen; **single-planet hotwired**; bodies are
bearing scalars, not addressable objects.

### 1.5 Planets & spatial coordinates (within a system)

**No spatial coordinates. Bodies are `(bearing, range)` polar scalars pinned to own-ship at center. Range is a synthesized closing animation, not a position.**

- The star/planet appear only as `dradis/bodies` frame entries built each tick in
  `_bodies_frame` (`sim.py:424-432`) from the `System` scalars.
- **Range is not a position** — it is an animation. `_planet_range` (`sim.py:408-411`) and
  `_star_range` (`sim.py:413-416`) interpolate from an arrival range to a hold range over
  `dwell_t` — i.e. the planet "sweeps inward" purely as a function of time-since-arrival,
  not because anything has a coordinate and a velocity.
- Own-ship sits at scope center (range 0) by convention; there is no `(x,y)` for own-ship,
  the star, the planet, or any contact **within a system**. Everything is polar-relative
  to an implicit origin.
- spec-11 §8 `[CXC-DPM-SUBLIGHT]` (`11-dradis-physical-model.lts:385-405`) already calls
  for absolute-unit positions + velocities per object — **the code has not caught up**.

**Nature:** hotwired polar scalars + a time-driven range animation. No `(x,y)`. This is
the single biggest divergence from the target "System with 1..n Planets each with real
spatial coordinates."

### 1.6 Jump / FTL

**Well-modeled as physics/economy, but the *act* of jumping mutates own-ship-as-World-fields, and "jump" means two different things in two layers.**

- Tactical jump: the FTL recharge-∝-distance engine. `GameConfig` FTL knobs
  (`entities.py:94-104`): `su_per_ly`, `max_su`, `red_line_ly`, `ftl_cooldown_k`,
  `rough_jump_max_p`, `cylon_cooldown_s = 0` (the asymmetry). Recharge state on `World`
  (`ftl_t`, `ftl_cooldown_s`, `last_jump_ly`, `last_jump_rough`, `jump_deferred` —
  `entities.py:203-208`). `ftl_ready()` (`entities.py:217-221`).
- The jump SEQUENCE `[CXC-GAME-JUMPSEQ]`: `_begin_jump_sequence` (`sim.py:248-262`) →
  civilians jump first, Vipers recalled, gated on all-Vipers-landed (`sim.py:399-405`),
  then `_arrive` (`sim.py:278-311`).
- **Smell:** `_arrive` **constructs a brand-new `World`** (`sim.py:297-304`) — this is how
  own-ship "state" is carried across a jump: by manually threading `own_heading`,
  `thrust_left`, `thrust_right` into the new god-object. If own-ship were an entity, it
  would simply *persist* across the jump; instead the jump has to know every own-ship field
  by name.
- Strategic jump: `StrategicState.strategic_jump` (`strategic.py:127-140`) — a totally
  separate, per-action bookkeeping jump (clears detection, records visited/known systems).
  Two "jump" verbs, two layers, no shared entity.

**Nature:** physics is persistent-per-`World`; the jump is a `World`-rebuild that must
hand-carry own-ship fields. Load-bearing smell for the entity rebuild.

### 1.7 Fuel

**Does not exist per-ship. Only a single fleet-wide `tylium` reserve at the strategic layer.**

- `StrategicState.tylium` (`strategic.py:52`), depleted per-action (`TYLIUM_IDLE_COST`,
  `strategic.py:26`; `advance_per_action`, `strategic.py:112`), settable by GM
  (`set_resource`, `strategic.py:152-169`).
- **No Viper fuel, no Raptor delta-v budget, no per-ship burn.** The Viper transit math is
  explicitly "ZERO fuel burn" ballistic (spec-11 §5, `11-...lts:259-262`), and the code
  has no fuel field on `ViperContact` at all.
- spec-11 §7/§8 wants delta-v/fuel budgets per ship (`11-...lts:389`, roster table
  `11-...lts:338-344`) — **spec ahead of code.**

**Nature:** fleet-wide scalar only; no per-entity inventory. Directly the "sub-ship with
its own inventory (fuel, weapons)" gap.

### 1.8 Weapons / combat

**Barely modeled. There is no combat resolution, no weapon inventory, no damage.**

- "Weapons" appears only as: (a) a contact *type* vocabulary in the contract
  (`contract.py:21-25`) — `raider`, `viper`, etc. are icon types, not armed entities; and
  (b) a **fire-escalation label** "weapons lockers" in the hangar-fire crisis
  (`crises.py:82-86`) — narrative, not ordnance.
- Viper "intercept" is pure kinematics (`_advance_vipers` intercept mode, `sim.py:228-237`)
  — a Viper flies to a contact's bearing/range and **tracks** it; nothing is shot, nothing
  dies, no ammo decrements. Threat "resolution" is a *sensor* event (range < 0.5 LS →
  `hostile`, `sensor.py:24-33`), not a combat event.
- spec-11 §6 `[CXC-DPM-WEAPONS]` + the roster's weapons column (`11-...lts:291-344`)
  specify flak domes, KEW cannon, missile salvos — **entirely spec-side; no code.**

**Nature:** essentially absent. Greenfield for the rebuild — which is *good*, because it
means the entity model can define weapon inventory without unwinding existing code.

### 1.9 Sensor / fog-of-war model

**Excellent. Single-authority, ground-truth-derives-sensed, published-vs-derived rule intact. KEEP.**

- `SensorModel` (`sensor.py:14-33`): the 0.5 LS resolve threshold + the
  `unknown → resolved`/`neutral → hostile` flip live here and **nowhere else**. `sense`
  returns the currently-sensed `type` + `allegiance`; `is_threat` is range < resolve_ls.
- The published-vs-derived invariant (`[CXC-ER-DRADIS-MOTION]`/`[CXC-ER-DRADIS-IDENT]`,
  spec `10-dradis-game.lts:42-61`, `10-...lts:134-137`): contact *heading* is derived at
  the display from screen-space motion (works because contacts are off-center); own-ship
  *heading* is **published** because own-ship at center has zero motion in its own frame
  and cannot be motion-derived. Both realized in code — heading published as
  `game/own_heading` (`sim.py:328-333`, `publish.py:49-51`), fog-of-war applied in
  `_contacts_frame` (`sim.py:434-460`) via `sensor.sense`.

**Nature:** the model's crown jewel. The redesign MUST preserve this invariant verbatim.

### 1.10 Command / director seam

**Excellent and directly reusable. KEEP.**

- The seam: `CommandSource` ABC (`directors/base.py:56-68`); the world polls it every tick
  and executes returned `Command`s (`sim.py:494-495`), never deciding inline.
- Three authority tiers over one `Command` type routed by namespace: `game/cmd/*` (fleet,
  player-permitted), `cylon/cmd/*` (enemy), `gm/cmd/*` (world-authoring, GM-only), with a
  fail-closed authority gate (`base.py:44-64`, `publish.py:114-128`). GM vocabulary is a
  closed-but-extensible verb set (`directors/gm.py:33-65`).
- Directors: `AutonomousPolicy` (autopilot, `directors/autopilot.py`), `BusCommandSource`
  (playable, `directors/bus.py`), scripted + priority composition
  (`directors/__init__.py:19-30`). Swapping the director is the only change ambient→playable
  needs.
- **This IS the ComposeUX "Intelligence drives identically to a human" credo** already
  realized: a mouse, an autopilot, and an MCP publish are peers on the same namespaces.
  The helm proves it — the throttle controller is "just another publisher, exactly like
  `game/cmd/*`" (`helm.py` header; `_apply_thrust` ingress, `sim.py:335-349`).

**Nature:** the best part of the codebase. Rides the rebuild unchanged.

### 1.11 Crises / events

**Excellent generic abstraction. KEEP. Orthogonal to the entity rebuild.**

- `Event` (`events.py:63-123`): a real-time consequence timer + ordered `EscalationStage`
  chain + optional TERMINAL branch + the `game/cmd/respond` that contains it. `EventManager`
  owns the active set and provides the deliberative↔real-time predicate
  (`has_active_timed_event`, `events.py:145-148`).
- Catalog (`crises.py`): hangar_fire / centurion_boarding / hull_breach share one shape;
  `dradis_engagement` (`crises.py:196-213`) is the largest kind, `drives_sim=True` — its
  "physics" IS the sim's 4 Hz tick.
- `UnifiedWorld` (`unified.py`) fuses STRATEGIC (`StrategicState`) + TACTICAL (the sim,
  stood up on demand) + EVENTS. The tactical sim is `None` while deliberative
  (`unified.py:39-40`, `start_engagement` `unified.py:47-60`).

**Nature:** generic + persistent; independent of how ships are modeled. One integration
touchpoint: `start_engagement` constructs the sim (`unified.py:47-60`) and will need to
seed it from the ship registry instead of `GameConfig` scalars.

### 1.12 Contract (the narrow waist)

**Good discipline, but its vocabulary encodes the flat model. Additive-widen, don't break.**

- `contract.py`: frozen `CONTACT_TYPES` / `BODY_TYPES` sets (`contract.py:21-30`),
  `contact()`/`body()` builders (`contract.py:39-61`), hot-path validators
  (`contract.py:64-90`). `battlestar` is own-ship-only, never a contact (`contract.py:20`)
  — encoding "own-ship is special / not a first-class contact."
- Bodies carry `(bearing, range_ls)` — **no `(x,y)`, no velocity** (`contract.py:51-61`).
  spec-11 §14 `[CXC-DPM-BUS]` (`11-...lts:605-621`) says widen `range`, optionally add
  `velocity`/`emcon`/`light_lag_s`, additive-only. **Spec ahead of code.**

**Nature:** persistent contract; single home for the display seam; the widen path is
already spec'd as non-breaking.

---

## 2. Gap analysis (ranked)

Ranked by how **load-bearing** each gap is for the multi-ship / sub-ship-inventory /
Raptor future. **L** = load-bearing (blocks the future), **S** = structural (needed for
clean entity model), **C** = cosmetic / follow-on.

### L1 — No first-class entity model at all (the root gap)

Everything else is a symptom. There is no `Entity` / `Ship` / `Body` base type. State
lives as (a) fields on the `World` god-object, (b) ad-hoc dataclasses that are really
*motion records* (`ViperContact`, `TrueContact`), and (c) frozen config scalars
(`System`). There is no registry, no per-entity identity that persists across a jump, no
inventory container. **Until an entity model exists, ships cannot own sub-ships and
sub-ships cannot own inventory.** This is the gap David named as "ships are
dynamically-loaded objects" — today they are not objects at all.

### L2 — Own-ship is `World` fields, not a Ship (the P1 god-object smell)

`own_heading` + `thrust_left/right` + `civilians` + `vipers` + `jump_state` are smeared on
`World` (`entities.py:182-215`). The jump has to hand-carry own-ship fields into a
freshly-constructed `World` (`sim.py:297-304`) — proof the state has no home of its own.
`game/own_heading` is the lone published own-ship channel; the target is
`ship/galactica/*`. **Load-bearing** because own-ship must become the exemplar Ship that
proves the hierarchy (Galactica owns the CAP + the hangar inventory).

### L3 — Flat `ViperContact` vs. the sub-ship hierarchy + inventory

CAP Vipers are ephemeral motion records re-spawned each jump with fresh random labels
(`sim.py:156-173`), no fuel, no weapons, no stable identity, no airframe type. The target:
Galactica owns an **iterated inventory** of N Mk-II Vipers, N Mk-VI Vipers, N Raptors,
**each its own object with its own inventory**. Today there is no container, no type
distinction (Mk-II vs Mk-VI vs Raptor — the contract even lists `viper`, `raptor`,
`viper_squadron` as flat icon types, `contract.py:22-23`), and no persistence. **The
single most load-bearing gap for the stated future.** Note the Cylon side (§1.3) has the
identical flat-vs-hierarchy gap: a basestar should own its raider squadrons.

### L4 — No spatial coordinates; System is single-planet-bearing hotwiring

`System` is frozen with one `planet_bearing` + one `star_bearing` (`entities.py:40-50`);
within-system position is polar `(bearing, range)` pinned to own-ship, and *range is a
time-driven closing animation* (`_planet_range`, `sim.py:408-411`), not a coordinate. The
target: a System with a star + 1..n Planets, **each with a real position**, so bodies are
addressable objects. **Load-bearing** for: multiple planets, a Raptor picket sphere at
real 1–5 LS positions (spec-11 §10), interceptor lanes, and any "fly to body X" order. The
only real coordinates today (`x_ly`/`y_ly`) are galaxy-level and used only for jump
distance (`entities.py:49, 70-72`).

### L5 — `World` is rebuilt on every jump (identity discontinuity)

`_arrive` does `self.world = World(...)` (`sim.py:297-304`). Any entity that must survive a
jump has to be manually threaded through the constructor. This actively **fights**
persistent entities: with a registry, ships persist and only their per-system tactical
placement resets. **Load-bearing** because sub-ship inventory (which Vipers are aboard,
which are damaged, fuel remaining) MUST survive a jump — it cannot if the world is
wholesale-recreated.

### S6 — Two disjoint "worlds": `World` (tactical) vs `StrategicState` (strategic)

`UnifiedWorld` fuses them by *composition* (`unified.py`), but they share no entity
vocabulary. `civilians` is an int on `World` (`entities.py:184`) AND the civilian fleet is
a strategic/aggregate concern (spec-11 §12). `tylium` is strategic-only (`strategic.py:52`)
yet fuel is a per-ship tactical concern. A single Ship/entity model should be the shared
vocabulary both layers project (strategic = coarse zoom, tactical = fine zoom), rather than
two parallel state bags. **Structural**, not blocking, but the rebuild should unify.

### S7 — Contract vocabulary encodes the flat model

`CONTACT_TYPES`/`BODY_TYPES` are flat icon-type sets (`contract.py:21-30`); `battlestar` is
"own-ship only, never a contact" (`contract.py:20`) — cementing own-ship's non-entity
special-case. Bodies carry no `(x,y)`/velocity. The widen is spec'd as additive
(`11-...lts:620-621`), so this is **structural**: extend the contract to carry per-entity id
+ optional position/velocity without breaking the display.

### S8 — Sub-ship *type* is a string, not a definition (no data-driven ship registry)

Ship/airframe types are bare strings scattered across code (`"basestar"`,
`"raider_squadron"`, `viper_count`) and contract sets. David wants **data-driven ship
definitions loaded into the world** (a registry/loader). Today there is no ship-definition
schema, no loader, no `doc://`-backed catalog — the roster is Python literals
(`_spawn_cylons` `sim.py:144-153`, `GALAXY` `entities.py:57-66`). **Structural**, and it is
the natural home for the spec-11 §7 roster numbers.

### S9 — Per-engagement counters/labels live on `World` and reset by rebuild

`baseship_count`/`squadron_count`/`heavy_count` (`entities.py:200-201`) and the CAP
random labels reset every jump because `World` is rebuilt. With persistent entities, a
ship's designation is a property of the *entity*, stable for its lifetime, not a counter on
the world. **Structural** cleanup that falls out of the entity model.

### C10 — Helm is a vendored shortcut bypassing the entity model

`helm.py` is explicitly vendored (`helm.py:1-15`) and drives `World.thrust_left/right` →
`game/own_heading`. Its own header names the target: drive `ship/galactica/thrust`.
**Cosmetic/interim** — it works, and it correctly proves control-is-a-peer; it just points
at the wrong (non-entity) target. Replace when Galactica becomes a Ship.

### C11 — `dradis_engagement` seeds the sim from `GameConfig`, not the registry

`start_engagement` (`unified.py:47-60`) builds the sim from `GameConfig` + `SensorModel`
with a hardcoded roster. Post-rebuild it should seed from the ship registry (which ships
are present, their inventory state carried from strategic). **Cosmetic/follow-on** — one
constructor.

### C12 — `game/threat`, `game/vipers`, `game/civilians` are aggregate scalars

These fleet-summary channels (`publish.py:42-43`) are fine as *derived* summaries, but
today they are the *only* representation (e.g. `vipers = "aboard|cap|engaging|recalled"` is
the whole CAP state, `entities.py:185`). Post-rebuild they become derived rollups over
per-entity namespaces. **Cosmetic** — keep them as convenience aggregates.

**Non-gaps (explicitly good — do not touch in the rebuild):** the sensor fog-of-war
authority (§1.9), the command/authority seam (§1.10), the Event/crisis abstraction
(§1.11), the published-vs-derived heading rule, the director-agnostic loop. These are the
reasons the rebuild is *tractable*.

---

## 3. Proposed target model

Three layers, cleanly separated: **entities** (what things ARE), **namespaces** (where they
live on the bus), **projections** (dradis/fog-of-war/command-seam, all unchanged in kind).

### 3.1 Entity model

A small type hierarchy replacing the `World` field-scatter + the motion-record dataclasses.

```
Entity                      # id (stable, lifetime), kind, display type, position, velocity
├── Ship(Entity)            # a hull: inventory, fuel/delta-v, weapons, ftl state, faction
│   ├── carrier hulls own an inventory of sub-ships
│   └── sub-ships are themselves Ship objects (own inventory)
├── Body(Entity)            # star / planet / asteroid: position, no inventory, no ftl
└── (aggregate) FleetGroup  # civilian box: centroid + box-width + count (spec-11 §12)
```

- **Ship** carries: `id`, `class_id` (→ registry definition), `faction`
  (`colonial|cylon|civilian`), `position` (in-system `(x,y)` LS or polar — pick one canonical,
  see Q1), `velocity`, `heading`, `ftl` (recharge state, per-ship character), `inventory`
  (fuel/tylium, weapons/ordnance, and — for carriers — a list of sub-ship Ships), and
  `status` (docked/aloft/damaged). Own-ship (Galactica) is **just a Ship** with
  `faction=colonial` and a rich sub-ship inventory. This dissolves L2.
- **Sub-ships** (Mk-II Viper, Mk-VI Viper, Raptor) are `Ship`s in a carrier's inventory,
  each with **its own** fuel + weapons + status. `ViperContact`'s motion fields
  (`mode`, `mode_t`, transit snapshots) become a *tactical placement* on the sub-ship, not
  the sub-ship's identity — so a Viper persists across jumps (fuel/damage carried), only its
  in-system placement resets. This dissolves L3 + L5 + S9.
- **Ship definitions are data**, loaded by a registry/loader (S8): a `ShipClass` catalog
  (Galactica, Viper Mk-II, Viper Mk-VI, Raptor, Basestar, Raider Squadron, Civilian) keyed
  by `class_id`, carrying the spec-11 §7 roster numbers (size, accel, delta-v/fuel,
  weapons, ftl character). Stored as `doc://ships/<class_id>` (document_store) or a
  versioned in-repo catalog; the loader instantiates Ships from classes. **This is the SDR
  move** — push ship specificity out of firmware (Python literals) into configuration.
- **System** becomes: `id`, galaxy position (`x_ly,y_ly` — kept, they already work), a
  `star` Body, and a **list of `planets: [Body]`** each with an in-system position. The
  frozen single-planet-bearing dataclass (`entities.py:40-50`) is replaced. Dissolves L4.
  The galaxy catalog becomes data too (alongside the ship registry).
- **World** stops being a god-object. It becomes a thin container: `current_system`, the
  set of present entities (own fleet + contacts + bodies, all `Entity`s), the engagement
  clocks that are genuinely global (detection, jump sequencing). Own-ship fields move to the
  Galactica Ship. The per-jump rebuild (L5) becomes: persistent entities stay; only their
  in-system placement + the transient contact set reset.

### 3.2 Per-entity namespace scheme

Define-once/render-many: the dradis renders ANY ship/body from its namespace. The scheme
generalizes today's `game/own_heading` + `dradis/contacts`.

- **Own fleet (colonial), per ship:**
  - `ship/galactica/heading`, `ship/galactica/thrust` (throttle helm lands HERE, retiring
    `game/own_heading` via alias — see migration), `ship/galactica/ftl_ready`,
    `ship/galactica/position`, `ship/galactica/status`.
  - `ship/galactica/inventory/vipers_mk2` (count/aloft/aboard), `.../vipers_mk6`,
    `.../raptors` — rollup counts; and per sub-ship where individually tracked:
    `ship/viper-017/position`, `ship/viper-017/fuel`, `ship/viper-017/mode`,
    `ship/viper-017/status`.
- **Bodies, per system/planet:** `system/<id>/star/position`,
  `system/<id>/planet/<pid>/position`, `system/<id>/planet/<pid>/type`. Multiple planets
  fall out for free.
- **Contacts (fog-of-war applied):** KEEP `dradis/contacts` + `dradis/bodies` as the
  *projected* display frame (the sensor-filtered rollup). The per-entity `ship/*` and
  `system/*` namespaces are **ground-truth / own-fleet** channels; the *sensed* view of
  hostiles still rides `dradis/contacts` through the SensorModel. This is the crucial split
  (see 3.4).
- **Aggregate rollups (derived, kept):** `game/threat`, `game/civilians`, `game/vipers`,
  `game/alert`, `game/jump`, `game/ftl_ready` remain as convenience summaries derived from
  the per-entity namespaces (C12).
- **State-engine discipline:** headings/positions/fuel are `continuous`/`json`; **free-form
  ids, class_ids, status strings, and selection targets MUST use the `json` engine, never
  `binary`** (the SIGABRT hazard — an empty/out-of-domain string into a strict engine aborts
  the process). Ship `status` (`aloft|aboard|damaged`) is an `enum` engine; `position` is
  `json` (a coordinate pair).

### 3.3 How the command seam rides it unchanged

The three-tier command seam (`game/cmd/*` / `cylon/cmd/*` / `gm/cmd/*`) is **untouched** in
kind. New verbs are additive within the existing closed-but-extensible pattern
(`directors/gm.py:57-65`): e.g. `game/cmd/launch {class: viper_mk2, n: 2}`,
`game/cmd/order {ship: viper-017, to: intercept, target: <id>}`,
`gm/cmd/spawn_entity {class: basestar, faction: cylon, at: <pos>}` (spawn_entity already
exists as a verb, `gm.py:37`; it just gains a real entity to instantiate). The director
ABC, authority tiers, and fail-closed gate are unchanged. The helm remains a peer publisher
— it just publishes `ship/galactica/thrust` instead of `throttle/*`→`game/own_heading`.

### 3.4 How fog-of-war rides it unchanged (invariant preserved)

**The published-vs-derived rule is the load-bearing invariant and it survives verbatim:**

- **Ground truth stays world-private.** Cylon Ship entities (position/inventory/faction)
  live in the world's entity set but are **never published on a `ship/cylon-*/*`
  ground-truth channel a display can read.** They reach a display ONLY through
  `SensorModel.sense` → `dradis/contacts`, exactly as today (`sim.py:434-460`,
  `sensor.py:24-33`). The 0.5 LS resolve threshold + the neutral→hostile flip stay the
  single authority in `SensorModel`.
- **Own-fleet is publishable** on `ship/galactica/*` and own sub-ships — because you *own*
  your fleet, publishing your own ship's truth is not a fog-of-war leak (this is already
  true of `game/own_heading`).
- **Heading rule intact:** contact heading is display-derived from motion
  (`[CXC-ER-DRADIS-MOTION]`) — still true for off-center contacts, now backed by real
  velocities. Own-ship heading is **published** (`[CXC-GAME-OWNSHIP]`) because own-ship at
  center is not motion-derivable — now published as `ship/galactica/heading` instead of
  `game/own_heading`. The *reason* the rule exists is unchanged; only the namespace name
  moves.
- **Define-once holds:** the dradis widget subscribes `dradis/contacts` + `dradis/bodies`
  and renders any blip from `{id, type, bearing, range, allegiance, label}`. It does **not**
  grow per ship type — a Raptor, a Mk-VI, a basestar are all just contacts with a `type`
  icon. Adding a ship class adds a registry row + (maybe) an icon type in the contract set;
  it adds **zero** display logic. This is the whole point.

### 3.5 What the physics layer (spec-11) hangs on this

spec-11's absolute positions/velocities/fuel/roster/picket become **properties of the
entity model** rather than a parallel rewrite: `Ship.position`/`.velocity`/`.fuel` are the
sublight physics (§8); `ShipClass` carries the roster numbers (§7); the Raptor picket is
N Raptor sub-ships placed at real 1–5 LS positions (§10); the civilian FleetGroup is the
aggregate (§12). The spec-11 migration ladder (§15, `11-...lts:625-649`) becomes *tractable*
once entities exist — several of its steps (roster, picket, fuel) are blocked today purely
because there is nowhere to hang the numbers.

---

## 4. Migration sketch (non-committal, staged — for WHEN we rebuild, not now)

Ordered so each step is shippable and keeps the display + seam working. The guiding rule:
**introduce entities behind the existing publish contract first, then move state into them,
then retire the god-object fields.** Nothing here should be done as a band-aid now.

1. **Introduce the entity types + registry, no behavior change.** Add `Entity`/`Ship`/`Body`
   + a `ShipClass` registry loaded from data. Do not wire them into the loop yet. Pure
   additive; the sim still runs on `World` fields. (Unblocks everything; risk-free.)
2. **Own-ship becomes a Ship, published behind an alias.** Construct a `galactica: Ship`;
   move `own_heading`/`thrust_left`/`thrust_right` onto it. Publish `ship/galactica/heading`
   AND keep `game/own_heading` as an **alias** (dual-publish) so no display breaks. The
   throttle helm retargets `ship/galactica/thrust`; `game/own_heading` deprecates over a
   release. Retires L2's smell first because it is the smallest and highest-value.
3. **Persist entities across the jump.** Change `_arrive` (`sim.py:297-304`) from
   `World(...)` wholesale-rebuild to: keep the entity set, reset only in-system placement +
   transient contacts. This is the L5 fix and it is a prerequisite for sub-ship inventory
   surviving a jump. Validate with a jump that carries a "damaged Viper" flag through.
4. **CAP Vipers become sub-ship inventory.** Galactica owns `[Ship(class=viper_mk2)]`; the
   `ViperContact` motion fields become a tactical placement on each sub-ship. Labels/identity
   become stable ship properties (S9 falls out). Fuel/weapons fields added (initially inert).
   `_spawn_cap_vipers` (`sim.py:156-173`) becomes "place the aloft Vipers," not "create new
   objects." Add Mk-VI + Raptor classes to the registry (still all render as `dradis/contacts`
   blips — zero display change).
5. **System gets 1..n coordinate-bearing planets.** Replace the frozen single-planet `System`
   (`entities.py:40-50`) with `star: Body` + `planets: [Body]`, each positioned. The
   `_bodies_frame` builder (`sim.py:424-432`) iterates planets instead of emitting one. The
   time-driven `_planet_range` animation (`sim.py:408-411`) is replaced by real
   position+velocity → range projection (aligns with spec-11 §8). Galaxy catalog becomes data.
6. **Cylon side becomes entities with hierarchy.** `TrueContact` ground truth becomes Cylon
   `Ship` entities (basestar owns raider squadrons). `SensorModel.sense` reads the entity
   instead of the dataclass — **fog-of-war authority unchanged**, just its input type. The
   hardcoded `_spawn_cylons` pair (`sim.py:144-153`) becomes a `gm/cmd/spawn_entity` from the
   registry.
7. **Unify strategic ↔ tactical vocabulary (S6).** `civilians` int + `tylium` reserve project
   onto the entity model (civilian FleetGroup; per-ship fuel derived from / feeding the
   strategic tylium reserve). `start_engagement` (`unified.py:47-60`) seeds the sim from the
   registry + carried inventory instead of `GameConfig` scalars (C11).
8. **Retire the god-object fields + the `game/own_heading` alias.** Once all consumers read
   `ship/*`/`system/*`, drop the deprecated aggregate-only fields. `game/threat` etc. remain
   as **derived** rollups (C12).
9. **Layer in spec-11 physics** (fuel burn, weapons resolution, Raptor picket, real
   velocities) on the now-existing entities — this is spec-11 §15's ladder, unblocked.

Compatibility notes: steps 1–3 are invisible to any display (contract unchanged). Step 2 is
the only one that touches a published namespace and it does so via a dual-publish alias.
The contract widen (position/velocity fields) is additive per spec-11 §14
(`11-...lts:620-621`) — old displays ignore new fields.

---

## 5. Open design questions for David (real forks only)

1. **In-system coordinate frame: cartesian `(x,y)` LS vs. polar `(bearing, range)` as the
   canonical store?** The display is polar (own-ship-centered scope). spec-11 §8 says "polar
   or cartesian." Cartesian generalizes cleanly to multi-body + velocity + "fly to X" and
   makes own-ship-not-at-center possible later; polar is a trivial projection off it. My
   recommendation: **store cartesian, project to polar at the contract boundary.** But this
   is a real fork with a code-shape cost — confirm.
   > **David (2026-07-26, on reading these notes):** the coordinate system will also need to
   > MAP TO THE FTL/jump SYSTEM depicted on screen — and that's a genuine gap in the show's
   > science. So defining the frame is partly a research task: survey third-party attempts to
   > reverse-engineer BSG's on-screen coordinate/jump system, then define OUR OWN reasonable
   > frame tuned for the precision-vs-entertainment level we want in the game (not physical
   > realism for its own sake). The cartesian recommendation stands as the STORE; the open work
   > is the SCALE/UNITS/JUMP-MAPPING of that frame — a design+research sub-task to do when we
   > build the coordinate system, not a blocker on the entity model. [FORK-1-EXTENDED]

2. **Sub-ship granularity: individual objects vs. squadrons-as-objects?** David said "N
   Mk-II vipers, N Mk-VI vipers, N raptors — EACH sub-ship its own object with its own
   inventory." spec-11 §12 models the *civilian* fleet as an aggregate box, not per-hull, and
   the current Cylon side uses `raider_squadron` (a squadron blip). Fork: are **Vipers
   individual** (17 objects) while **civilians + raider swarms are aggregates**? My
   recommendation: **yes — individual for the player's own launchable craft (Vipers/Raptors),
   aggregate FleetGroup for civilians and mass enemy swarms.** Confirm the split; it decides
   the inventory container shape.

3. **Ship-definition store: in-repo versioned catalog vs. `doc://ships/*` in document_store?**
   The SDR/config-over-code instinct says externalize to data. document_store gives you a
   query API + hot-editability (and a GM could author a Pegasus at runtime); an in-repo YAML
   catalog is simpler + reviewable in git. Fork on *who authors ships and when* — a GM at
   runtime (→ doc store) or only devs at build time (→ repo catalog). Recommendation: **repo
   catalog now, doc-store-backed later** when the GM needs runtime ship authoring.

4. **Does own-ship stay pinned at scope center, or does the entity model allow it to move?**
   spec-11 §8 pins Galactica as "effectively stationary" (sub-pixel drift). If own-ship is a
   real entity with a position, do we *use* that position (own-ship can drift off-center on a
   zoomed scope) or keep the center-pin convention? Keeping the pin preserves the
   published-heading invariant's *rationale* exactly; letting her move complicates it.
   Recommendation: **keep the center-pin as a display convention** even though the entity has
   a real position — the invariant survives and the physics still integrates. Confirm.

5. **Persistence scope of sub-ship inventory across a full campaign (not just a jump)?**
   Once Vipers persist across a jump (step 3), do they persist across a whole
   deliberative↔tactical cycle — i.e. is a Viper lost in engagement N gone in engagement
   N+1, fuel carried, rearmed only at a strategic action? This ties the tactical entity model
   to the strategic layer (S6) and is a genuine campaign-design fork, not just a code choice.

---

## Appendix: what to KEEP verbatim in the rebuild

- `SensorModel` and the 0.5 LS / neutral→hostile authority (`sensor.py`) — the fog-of-war
  single-home rule.
- The command/authority seam: `CommandSource` ABC, three tiers, fail-closed gate
  (`directors/base.py`, `directors/gm.py`, `publish.py:114-128`).
- The `Event`/`EventManager` crisis abstraction (`events.py`, `crises.py`) — orthogonal to
  entities.
- The published-vs-derived heading invariant (`[CXC-GAME-OWNSHIP]` /
  `[CXC-ER-DRADIS-MOTION]` / `[CXC-ER-DRADIS-IDENT]`).
- The `dradis/contacts` + `dradis/bodies` narrow-waist contract (widen additively, never
  break — `contract.py`, spec-11 §14).
- The director-agnostic loop (`sim.py:491-497`) — the world executes, never decides.

These are why the rebuild is an entity-model rebuild and not a from-scratch rewrite.
</content>
</invoke>
