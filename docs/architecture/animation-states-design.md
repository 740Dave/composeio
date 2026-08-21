# ComposeUX Animation + States Substrate — Design

**Author:** composeux-render-engineer
**Date:** 2026-08-04
**Status:** Design / scoping. Opinionated. Grounds Slint-analysis Lift #1 into a buildable design.
**Extends:** `[CXC-RM-BEHAVIORS]` (declarative layer), `[CXC-RM-GESTURE]`, `[SPEC:CXC-BIND]` (`[CXC-BIND-BEHAVIOR]`), `[CXC-R-ENGINE-ANIM]`, `[SPEC:CXC-GESTURE]`.
**Reads on:** `[CXC-RM-M5]` (CYD perf floor — the CYD path *depends on* Lift #2).
**Audience:** David (architect) — greenlight decision.

> **Provenance / licensing (MIT clean-room).** ComposePlatform is to be published as **MIT open source**;
> Slint is GPL/commercial and is a **hard dealbreaker as a source**. This design is therefore **clean-room**:
> the model below is *derived from ComposeUX's own `engine/behaviors.py` + `engine/animation.py`* (which already
> do trigger→temporal-behavior, easing, and deterministic interpolation), from **first principles**, and from
> **general, non-proprietary UI-animation knowledge** (state machines; the CSS Animations/Transitions
> vocabulary — `duration`, `delay`, `iteration-count`, `cubic-bezier` — which is a W3C standard, not Slint's).
> Slint is cited **only as prior-art inspiration in prose**: it validated that a *declarative* states/transitions
> layer is the right shape. **No Slint code, code structure, type names, or keyword grammar is copied** — and
> our concrete syntax deliberately diverges from Slint's (§2.1: a from/to-matched rule list, not Slint's
> `in {} / out {}` blocks). The result carries no GPL provenance and is MIT-clean.

---

## 0. What this is, and the one sentence that governs it

ComposeUX has queued a tactical-UX animation substrate in `[CXC-RM-BEHAVIORS]`/`[CXC-RM-GESTURE]`: the ability to designate a main-gun target or sketch a flak barrier with animated, state-driven feedback. The competitive review of Slint (GPL) confirmed, as prior art, that a **declarative states + transitions + duration×easing** layer is the right general shape for this — and observed it should map cleanly onto our existing `engine/behaviors.py` + `engine/animation.py`. This doc turns that observation into a concrete, buildable extension **derived from our own substrate**, not transcribed from anyone: a declarative **states + transitions + animated-property** substrate.

**Governing sentence:** *`states:` is data parsed at runtime into a live object attached to the widget, mutated one bus command at a time, hot-swappable, byte-reproducible via `render_frame_at(t)`, and damage-disciplined to the animating sliver — the general declarative-animation idea, realized on ComposeUX's runtime-declarative, bus-native heart with no compile step.* Every design choice below defends that sentence.

---

## 1. Where this fits in the substrate we already have

We already own three of the four pieces. This is an **extension, not a greenfield**.

| Piece | Exists today | What it does | What's missing for states |
|---|---|---|---|
| **Property tween** | `engine/animation.py` `Animator` | interpolates one attr from→to over duration×easing, per-frame `tick(now)`, records `last_touched` for damage | (a) parameterized `cubic-bezier`; (b) `delay`/`iteration_count`; (c) **deterministic start-stamp** (see §2.3 — the current `Animator` stamps `time.monotonic()` at `animate()` call and is therefore *not* `render_frame_at`-reproducible) |
| **Trigger → temporal behavior** | `engine/behaviors.py` `BehaviorEngine` | maps a bus-state `when:` to an engine-owned *continuous/looping* behavior (blink/pulse/rotate/ramp), pure function of elapsed time, deferred start-stamp, `last_touched` damage, revert-on-release | behaviors are **one attr, one temporal function** — they cannot express a **named multi-attribute configuration** with **entry/exit choreography between arbitrary pairs** |
| **Clocked-widget + damage** | `runtime._tick_clocked` / `on_clock_tick` / `clock_damage_rect` / `request_redraw()` | ticks per-frame animators, damages only the touched widget's rect (the caret blinks its 3px sliver, not the field) | nothing — this is exactly the seam a state transition rides |
| **Gesture → bus** | `runtime._dispatch_pointer` + `_publish_interaction` | `on_pan_start/on_pan/on_pan_end`, `on_long_press`, `on_swipe`, `on_tap`, and every gesture is *also published to `INTERACTION_NS`* | a declarative way for a gesture to **drive a state**, on the same path an Intelligence would (control-is-a-peer) |

**The precise relationship between behaviors and states** (this is the design's spine):

- A **behavior** is *continuous temporal presentation of one attribute* while a trigger holds: blink `visible`, rotate `rotation`, pulse `opacity`. It has no notion of "where we are" — it's a function of `t`.
- A **state** is a *named, discrete, multi-attribute target configuration* (`locked = {opacity:1, scale:1.0, color:red}`), selected by bus state, with a **transition** (a tween choreography) describing *how to move between two states*.
- They **compose, they don't compete.** A state can still carry looping behaviors (the `locked` reticle also `pulse`s). Conflicts on a shared target already resolve **last-declared-wins** in `BehaviorEngine`; the `StateEngine` writes on the same pass and obeys the same rule.

States are the layer that behaviors reach for and can't express: `[CXC-RM-BEHAVIORS]` explicitly names "generalize today's `behaviors:` `when → do` into **named states with entry/exit choreography**." This is that generalization.

---

## 2. The declarative model (the `.compose.yaml` syntax)

### 2.1 Three declarative blocks on any widget

```yaml
- type: image
  id: reticle
  src: bus://assets/tactical/reticle.bmp32

  # (A) STATES — named, multi-attribute target configurations.
  #     Each state is a literal map of bindable-attr -> value.
  states:
    idle:      { opacity: 0.0, scale: 1.6, color: "#3FB0FF" }
    acquiring: { opacity: 1.0, scale: 1.15, color: "#FFCC00" }
    locked:    { opacity: 1.0, scale: 1.0,  color: "#FF3B30" }

  # (B) STATE SELECTOR — WHICH state is active. "State on the bus."
  #     Same three forms as an attr binding: bare namespace, {namespace, map},
  #     or {expr: "..."} (DSL). A literal map only — arithmetic is DSL territory.
  state:
    namespace: fire_control/lock
    map: { none: idle, acquiring: acquiring, tracking: locked }
    default: idle

  # (C) TRANSITIONS — HOW to move between states (the tween choreography).
  #     Each rule: which attrs animate, over how long, with which easing.
  #     Rules match by from/to; the first matching rule wins; an unmatched
  #     attr SNAPS (no tween). `*` = any state.
  transitions:
    - animate: [opacity, color]
      duration_ms: 220
      easing: ease_out
    - animate: [scale]
      to: locked                       # only when entering `locked`
      duration_ms: 140
      easing: cubic-bezier(0.2, 0.9, 0.1, 1.0)   # snappy overshoot-free settle
      delay_ms: 40
```

**Why a from/to-matched rule list (our shape, clean-room):** the natural derivation from *our* substrate is a rule list, not entry/exit blocks. Our existing `behaviors:` is already a flat list of `when → do` rules matched against bus state (`parse_behaviors`); `transitions:` is the direct analog — a flat list of `from/to → animate` rules matched against the state edge. One rule (`animate: [opacity, color]`) covers *all* transitions; a specialized rule (`to: locked`) overrides one edge; an unmatched attr snaps. This is declarative-data-shaped (no per-state restatement), trivially editable by Composer as peer bus commands (add a rule = one `set_property`-style mutation), and it falls straight out of our own binding model — no borrowed grammar. (Prior-art note: this is a different structure from Slint's per-state `in {}/out {}` blocks; we derived the rule-list form from our `behaviors:` list, and it also mirrors how CSS attaches `transition` to a selector rather than to a keyframe.)

### 2.2 The values a state carries are exactly the bindable attrs we already have

`states.<name>` values key on the **same `BINDABLE_ATTRS_BY_TYPE` / `UNIVERSAL_BINDABLE_ATTRS`** table (`engine/bindings.py`) — `opacity`, `rotation`, `scale`, `visible`, `color`, plus per-type attrs (`value`, `text`, …). This means:

- Composer's binding editor gets states **for free** via the existing `bindable_attrs_for(widget_type)` introspection (one new tab section, zero hardcoding — the same pattern `[CXC-BINDEDIT]` and `[CXC-FLAUTH]` already use).
- The `strip_code_fields` / `validate_map` gate (`[CXC-BIND-BOUNDARY]`) applies unchanged: a state map is *literal values only*; anything arithmetic is refused → DSL territory. No inline code, ever. RCE-safe on the fetch-from-bus fleet.

### 2.3 One honest correction to the existing `Animator` (a real determinism gap)

The current `Animator.animate()` stamps `start_time = time.monotonic()` **at call time** (`animation.py:99`). That is fine for the interactive live loop, but it is **not reproducible under `render_frame_at(t)`** — the determinism gate `[CXC-BIND-C6]` (SHA-256-identical frames macOS↔RPi5) that behaviors and Lottie both honor. `BehaviorEngine` already solved this correctly: it **defers the start-stamp to the first tick's logical clock** (`behaviors.py:343`, `if inst.start is None: inst.start = now`).

**Design requirement:** state transitions must run on the **behaviors-style deferred-start** discipline, not the `Animator`'s call-time stamp — so a transition is a pure function of `(now - start)` and samples byte-identically cross-platform. This is why §3 proposes a `StateEngine` that mirrors `BehaviorEngine` rather than routing through `Animator`. (Separately, we should fix `Animator` to defer its stamp too, but that's a small orthogonal cleanup, not this deliverable.)

---

## 3. Runtime mechanism

### 3.1 A `StateEngine`, sibling to `BehaviorEngine`, ticked in the same three places

New module `engine/states.py`. It mirrors `BehaviorEngine` deliberately — same lifecycle, same determinism discipline, same damage contract — so it slots into the runtime with near-zero new plumbing.

```
StateMachine (one per widget with a `states:` block)
  states:        Dict[str, Dict[str, Any]]     # parsed literal target configs
  selector:      AttrBinding-like              # namespace|map|expr -> state name
  transitions:   List[TransitionRule]          # from/to-matched animate rules
  current:       str                           # the settled state
  active:        List[_Tween]                  # in-flight per-attr tweens
```

**On selector change** (a bus publish on `fire_control/lock`, marshalled through `runtime.schedule_update` on the render thread exactly like every other bus callback):

1. Resolve new state name via the selector (map/expr → name; unknown → `default`).
2. For each attr that differs between `current` and the new state, find the first matching `TransitionRule` (`from`/`to`/`animate` match). If a rule covers the attr → enqueue a `_Tween(attr, from=current_live_value, to=target, dur, easing, delay, iterations)`. If no rule covers it → **snap** (`setattr` now).
3. `current = new`. Mark the widget's `_ctx.request_redraw(arranged_rect)` (or the tighter `paint_bounds`) so the first transition frame is scheduled.

**Per frame** — `StateEngine.tick(now)` runs right beside `self.behaviors.tick(t)` in all three runtime entry points (`render_frame_at`, the headless `tick`, and the live `run` loop):

```python
# runtime.render_frame_at(t) — additive, one line beside behaviors:
self.behaviors.tick(t)
self.states.tick(t)          # NEW — same logical clock, same determinism
```

`tick(now)` for each in-flight tween:
- `if tween.start is None: tween.start = now` (deferred start-stamp → reproducible).
- `u = clamp((now - start - delay)/dur, 0, 1)`; for `iteration_count > 1`, wrap `u` per iteration.
- `value = _interpolate(from, to, easing(u))` — **reuse `animation._interpolate` and the resolved easing** (R3: reuse by reference, never fork).
- `setattr(widget, attr, value)`; if the value changed, append widget to `self.last_touched`.
- when `u >= 1.0` on the final iteration, snap to `to` and drop the tween.

Returns `busy = bool(active)` so the runtime keeps the composition dirty *only while a transition is in flight* — the moment every tween settles, the widget stops requesting frames and costs zero. Exactly the caret/behavior idle model.

### 3.2 Damage discipline — animate the sliver, damage the sliver

This is non-negotiable and already solved; the `StateEngine` just plugs into it:

- `StateEngine.last_touched` (like `BehaviorEngine.last_touched` / `Animator.last_touched`) lists the widgets a tick actually wrote. The runtime already has `_damage_widgets(widgets)` which calls `mark_damage(w.arranged_rect)` per widget (`runtime.py:850`). One added call: `self._damage_widgets(self.states.last_touched)`.
- A widget that wants a **tighter** damage than its full rect overrides `clock_damage_rect()` / `paint_bounds` — the exact mechanism the text_input caret uses to damage only its 3px sliver (`text_input.py:349`). A reticle transition on `opacity`/`color` damages the reticle's bounds, **not the DRADIS scope**, and never the 1280×1300 scene.
- A transition that touches `scale`/`rotation` grows the painted bounds; the runtime already damages **old-rect ∪ new-rect** post-layout (`_apply_drained_damage`, the growth path `text_input` relies on), so a scaling reticle repaints its union region and nothing else.

No new damage machinery. The bus-reactive redraw path (`on_bus_state` → `request_redraw` → `present_partial`) is untouched — a state transition is just another producer of scoped damage.

### 3.3 The heart, preserved (checklist)

- **No compile step:** `states:`/`transitions:` parse at runtime into a `StateMachine` object (parser addition beside `parse_behaviors`). A scene hot-swaps by re-parsing; Composer edits a state via a peer bus command (`add_widget`/`set_property`-shaped). No codegen, no build artifact.
- **Bus-native reactive:** the selector is an ordinary namespace subscription; state lives on the bus, transition lives in the widget. An Intelligence drives a lock-on by publishing `fire_control/lock = tracking`.
- **Narrow-waist Canvas:** transitions write *attribute values*; the widget's existing `draw()` against the `Canvas` contract renders them. No new primitives — `opacity`/`scale`/`color` already lower to `set_opacity`/`scale`/fill paints. Define-once holds.
- **Deterministic:** deferred start-stamp + pure-`float` `_interpolate` → `render_frame_at(t)` samples byte-identically macOS↔RPi5 (the same guarantee behaviors/Lottie carry, *stronger* than Slint offers).
- **reduced_motion:** the runtime already carries `design.reduced_motion` (`runtime.py:186`); under it, transitions **snap** (apply `to` instantly, no tween) — the same REST-state courtesy the caret and spinner already honor.

---

## 4. Easing — `cubic-bezier` into the `EASING` table

Today `EASING` is a `name -> Callable[[float], float]` dict (`linear`/`ease_in`/`ease_out`/`ease_in_out`/`spring`). `cubic-bezier(a,b,c,d)` is *parameterized*, so it can't be a static dict entry. The clean addition is a **resolver**:

```python
def resolve_easing(spec: str) -> Callable[[float], float]:
    # 1. named entry (existing) — 'ease_out', 'spring', ...
    if spec in EASING:
        return EASING[spec]
    # 2. CSS names -> canonical cubic-beziers (aliases, so 'ease-in-out' works)
    #    ease=(.25,.1,.25,1) ease-in=(.42,0,1,1) ease-out=(0,0,.58,1) ...
    # 3. cubic-bezier(a,b,c,d) -> a solver: given x=t_norm, Newton-Raphson
    #    solve the bezier param s where Bx(s)=x, then return By(s).
    ...
```

**Reuse, don't fork (R3):** `render/lottie/` already ships a **fixed-iteration bezier easing solver** for keyframe interpolation. The `resolve_easing` cubic-bezier path should call that same solver (or a shared extraction of it), so desktop, Lottie, and states all evaluate cubic-bezier identically — and it inherits the "Python floats are bit-identical cross-platform" property the Lottie pass already proved. One solver, three consumers.

Also add to the transition/behavior param vocabulary:
- `delay_ms` — offset before the tween starts (the standard CSS `animation-delay` idea).
- `iteration_count` — repeat the tween N times (or `loop: true` ≡ ∞). Wrap `u` per iteration.

`cubic-bezier` and `delay`/`iteration_count` drop into **both** `states` transitions **and** the existing `BEHAVIOR_PARAM_SCHEMA` easing enums (they become "any resolvable easing string," and the editor offers a bezier field) — so the whole animation vocabulary gains them at once.

---

## 5. Gesture-driven transitions (`[CXC-RM-GESTURE]`)

### 5.1 The seam: a gesture *produces* a bus state; the state machine *consumes* it

The interaction engine already publishes every gesture to `INTERACTION_NS` (`_publish_interaction`, `runtime.py:1598`) and exposes `on_pan_start/on_pan/on_pan_end`, `on_long_press`, `on_swipe`, `on_tap`. The design principle — and it is the *control-is-a-peer* seam — is:

> A gesture never mutates a widget's state directly. It **publishes a semantic bus state**, and the state selector binds that state. So a finger drag and an Intelligence `publish()` reach the state machine through the **identical path**.

Concretely, a widget that owns a tactical gesture (the DRADIS scope) maps its raw gesture to a semantic namespace:

```yaml
- type: dradis_scope
  id: scope
  # declarative gesture -> semantic bus publish (the producer side)
  gestures:
    - on: long_press          # tap-and-hold to begin designation
      publish: { namespace: fire_control/lock, value: acquiring }
    - on: pan                 # drag steers the designation; release commits
      publish: { namespace: fire_control/cursor, value: "@pointer" }  # x,y
    - on: pan_end
      publish: { namespace: fire_control/lock, value: tracking }
```

The reticle (§2.1) binds `fire_control/lock` in its `state:` selector — it neither knows nor cares whether a finger or an Intelligence set it. **One state path, two producers.** This is the whole `[CXC-RM-GESTURE]` "an Intelligence can swipe identically to a finger" requirement, realized declaratively: the gesture's *output* is a bus state, not a private call.

`gestures:` is a small declarative producer block (parsed beside `behaviors:`) that maps an engine-recognized gesture (`tap`/`long_press`/`pan`/`pan_end`/`swipe`/`double_click` — the ones the runtime already synthesizes) to a `publish`. The `@pointer` token resolves to the event's surface coords at publish time (the one non-literal, engine-provided, safe substitution). No inline code — the boundary holds.

### 5.2 Optional (Phase C advanced): progress-driven "scrub" transitions

For a *continuous* gesture that should **scrub** a transition rather than trigger-then-play it (iOS-style interactive transitions — drag halfway and the reticle is halfway locked), a transition rule may bind its progress `t` to a namespace instead of the clock:

```yaml
  transitions:
    - animate: [scale, color]
      progress: { namespace: fire_control/lock_progress }   # 0..1, not time
      easing: ease_out
```

The gesture publishes `lock_progress` as it drags; the tween's `u` is *read from the bus* instead of `(now-start)/dur`. Same `_interpolate`, same easing, same damage — only the clock source differs. This is genuinely more capable than Slint (whose transitions are time-only) and it stays pure state-on-bus (an Intelligence can publish `lock_progress = 0.5` and freeze the reticle mid-lock). Scope it to Phase C as the advanced case; the trigger-then-play path (§5.1) is the 90%.

---

## 6. Cross-tier

| Concern | Desktop / RPi (Skia) | CYD / ESP32 |
|---|---|---|
| **Parse `states`/`transitions`/`gestures`** | full | **portable** — `yaml_min` reads the same blocks; `compose_scene` builds the state machine. The DSL/canon selector already ports (`cyd/src/dsl`, corpus bit-identical). |
| **State model + snap-to-state** | full | **yes, now** — on a selector change, `set` the target state's attr values and repaint once. Discrete state changes work on the CYD today. |
| **Tweened transition (opacity/color)** | full, deterministic | **blocked on Lift #2** — per-frame tween needs dirty-band + DMA-overlap rendering (the `[CXC-RM-M5]` floor: 56ms/17.9fps empty frame). Until then: **snap**. |
| **Tweened transition (scale/rotation)** | full (`scale`/`rotate` on the contract) | **blocked** — transforms are documented **no-ops** on `ESP32Canvas` (escalated); needs live per-band inverse-mapping or the Qualia PSRAM tier. Sanctioned CYD path for heavy motion stays **pre-baked sprite sequences** (`bake_lottie_sprite.py`). |
| **Gesture → bus publish** | full | **yes** — touch is already a CYD input; the `gestures:` publish rides the reactive tier (`[CXC-RM-EMBED-REACTIVE]` R3). |
| **Determinism gate** | SHA-256 frame identity macOS↔RPi5 via `render_frame_at` | pixel crosscheck via `cyd_config_crosscheck.py` on **settled** states (snap targets), not mid-tween. |

**Scope call:** **desktop-first, CYD-follows.** The full tweened experience ships on the Skia workhorse (macOS > RPi, the actual runtime distribution). The CYD gets the *state model* immediately (snap-to-state — correct, just not animated) and gains *tweened* transitions when Lift #2's dirty-band software renderer lands. This is honest about the tier boundary (R2: your green build is not the glass — and here, the CYD's frame floor is a measured fact, not a hope). Do **not** gold-plate the CYD path in this deliverable.

---

## 7. Worked tactical-UX example — target-designation lock-on

**Scenario:** operator drags on the DRADIS scope to designate a contact for the main gun. The reticle fades in over the cursor (`acquiring`), tracks the drag, and on release **locks** onto the nearest contact with a snappy scale-settle and a color flip to threat-red. An Intelligence can perform the identical designation by publishing `fire_control/lock`.

```yaml
# examples/target_designation.compose.yaml  (pixel_stack composite)
design: { theme: tactical, reduced_motion: false }

namespaces:
  - { namespace: fire_control/lock, engine: json, external: true }
  - { namespace: fire_control/cursor, engine: json, external: true }

layers:
  # --- the scope: recognizes the gesture, publishes semantic state ---
  - type: dradis_scope
    id: scope
    rect: [0, 0, 480, 480]
    subscribe: [contacts/*]              # live contacts drive the blips
    gestures:
      - on: long_press
        publish: { namespace: fire_control/lock, value: acquiring }
      - on: pan
        publish: { namespace: fire_control/cursor, value: "@pointer" }
      - on: pan_end
        publish: { namespace: fire_control/lock, value: tracking }
      - on: double_tap                   # double-tap clears designation
        publish: { namespace: fire_control/lock, value: none }

  # --- the reticle: pure state-on-bus consumer, tweened transitions ---
  - type: image
    id: reticle
    src: bus://assets/tactical/reticle.bmp32
    rect: [0, 0, 96, 96]
    # follows the designation cursor (ordinary attr binding on x,y)
    x: { namespace: fire_control/cursor, attr: x }
    y: { namespace: fire_control/cursor, attr: y }

    states:
      none:      { opacity: 0.0, scale: 1.8, color: "#3FB0FF" }
      acquiring: { opacity: 0.9, scale: 1.25, color: "#FFCC00" }
      tracking:  { opacity: 1.0, scale: 1.0,  color: "#FF3B30" }

    state:
      namespace: fire_control/lock
      map: { none: none, acquiring: acquiring, tracking: tracking }
      default: none

    transitions:
      # fade + color: gentle on every edge
      - animate: [opacity, color]
        duration_ms: 180
        easing: ease_out
      # scale: fast, decisive settle ONLY on the lock (into `tracking`)
      - animate: [scale]
        to: tracking
        duration_ms: 130
        easing: cubic-bezier(0.2, 0.9, 0.1, 1.0)
      # scale on the way IN (acquiring): softer
      - animate: [scale]
        to: acquiring
        duration_ms: 200
        easing: ease_in_out

    # a locked reticle also PULSES — states + behaviors compose
    behaviors:
      - when: { namespace: fire_control/lock, is: tracking }
        do:  { behavior: pulse, target: opacity, from: 0.75, to: 1.0,
               period_ms: 900, easing: ease_in_out }
```

**End-to-end trace:**

1. Operator **long-presses** the scope → `scope` publishes `fire_control/lock = acquiring`.
2. `reticle`'s selector maps `acquiring` → the state machine tweens `opacity 0→0.9` (180ms ease_out) and `scale 1.8→1.25` (200ms ease_in_out). Damage: the reticle's ~96px bounds, nothing else.
3. Operator **drags** → `scope` publishes `fire_control/cursor = @pointer`; the reticle's `x`/`y` bindings move it (ordinary bus reactive, scoped damage on the old∪new rect).
4. Operator **releases** → `scope` publishes `fire_control/lock = tracking`. The state machine tweens `color amber→red` (180ms) and `scale 1.25→1.0` with the **snappy cubic-bezier** (130ms) — the decisive "lock" feel. The `pulse` behavior activates on the same bit and breathes `opacity` while locked. `render_frame_at(t)` samples every frame byte-identically macOS↔RPi5.
5. **Intelligence parity:** an Intelligence publishing `fire_control/lock = tracking` produces the *identical* transition — it reached the state machine through the same namespace. Control is a peer.
6. **reduced_motion / CYD:** under `reduced_motion` (or on the CYD before Lift #2), steps 2/4 **snap** to the target state — correct end-states, no tween.

This is states + transition + gesture + easing, end-to-end, in the proposed syntax, defending every line of the governing sentence.

---

## 8. Phased build plan

Ordered so each phase is independently valuable and pixel/determinism-gated before the next builds on it.

### Phase A — Animated-property core + easing (extends `[CXC-R-ENGINE-ANIM]`)
- `resolve_easing(spec)` with `cubic-bezier(a,b,c,d)` + CSS-name aliases, **reusing the Lottie bezier solver** (R3). Add `delay_ms` / `iteration_count` to the tween/behavior vocabulary.
- `engine/states.py` `StateEngine` + `_Tween`: deferred-start-stamp determinism (mirrors `BehaviorEngine`), reuses `_interpolate`, populates `last_touched`. Ticked beside `self.behaviors.tick(t)` in `render_frame_at` / headless `tick` / live `run`.
- **Acceptance:** a programmatic `to`→`from` tween is SHA-256-identical macOS↔RPi5 at sampled `render_frame_at(t)` frames; damage confined to the widget's bounds (asserted via the damage log); `cubic-bezier` matches the Lottie solver bit-for-bit. Suite green both platforms.

### Phase B — Declarative `states:` / `state:` / `transitions:` (extends `[SPEC:CXC-BIND]` → new `[CXC-BIND-STATES]` + `[CXC-BIND-TRANSITION]` nodes)
- Parser: `parse_states` / `parse_transitions` beside `parse_behaviors`; `strip_code_fields` + `validate_map` on state maps (boundary holds). Selector reuses the `AttrBinding` namespace/map/expr forms.
- Composer binding editor: a **States** section rendered from `bindable_attrs_for(widget_type)` (zero hardcoding — same introspection pattern as `[CXC-BINDEDIT]`). reduced_motion → snap.
- **Acceptance:** a bus-state flip drives a widget through a transition; goldens pinned at **rest** and **mid-transition** (like `bind_red_alert_on`/`_off` and `bind_dradis_mid_sweep`); Intelligence-over-MQTT smoke drives the state; `red_alert.compose.yaml`-class demo `target_designation.compose.yaml` exports and runs standalone byte-identical both platforms.

### Phase C — Gesture-driven transitions (extends `[SPEC:CXC-GESTURE]` / `[CXC-RM-GESTURE]`)
- Declarative `gestures:` producer block (`on: <gesture> → publish: {namespace, value}`, `@pointer` substitution). Wired to the existing `on_pan/on_long_press/on_swipe/...` synthesis + `_publish_interaction`.
- Optional progress-driven (`progress: {namespace}`) scrub transitions.
- **Acceptance:** drag on the DRADIS scope locks the reticle (the §7 example, live); an Intelligence publishing the same namespace produces the **identical** graph/transition (the `[CXC-RM-GESTURE]` "swipe == finger" and `[CXC-FLOW]` "authored == Intelligence-authored" bar); interaction events observable on the bus.

### Phase D — CYD-follows (gated on Lift #2, `[CXC-RM-M5]`)
- Snap-to-state ships with Phase B on the CYD (portable, no tween). Tweened transitions land **after** the dirty-band + DMA-overlap software renderer (Lift #2) raises the frame floor; scale/rotation tweens additionally wait on live transforms (escalated) or ride pre-baked sprites.
- **Acceptance:** on the CYD, a settled state change pixel-crosschecks (`cyd_config_crosscheck.py`) against the headless engine render of the same config; tweened acceptance deferred with Lift #2. **No gold-plating.**

### Spec formalization
**Yes — route a `factlattice-editor` pass** after Phase A lands, to formalize this into `16-composeux-bindings-behaviors.lts` as new nodes `[CXC-BIND-STATES]` (the named-config + selector model) and `[CXC-BIND-TRANSITION]` (the from/to-matched tween rules + easing/delay/iteration vocabulary), with the gesture-producer piece cross-linked into `19-composeux-gestures.lts` (`[SPEC:CXC-GESTURE]`). States generalize behaviors and belong in the same spec home; keep `[CXC-BIND-BOUNDARY]` and `[CXC-BIND-C6]` (determinism) as governing invariants. Record the `Animator` deferred-start-stamp correction as a `change`/`context_for_change` on `[CXC-R-ENGINE-ANIM]` (invalidating assumption: "call-time stamp is fine" — false under `render_frame_at`).

---

## 9. Why this is the right shape (the pushback-proof summary)

- It **realizes** the Lift-#1 idea — declarative states, transitions, `cubic-bezier`, delay/iteration (general UI-animation concepts, clean-room, no GPL provenance) — with the **compile step removed**: runtime-parsed data, incrementally mutable over the bus, hot-swappable.
- It **reuses** every load-bearing piece we already proved: `_interpolate`/`EASING`, the deferred-start determinism, the `last_touched` → `_damage_widgets` scoped-redraw, the `bindable_attrs_for` introspection, the gesture→`INTERACTION_NS` publish, the Lottie bezier solver. New code is one small engine module + a parser addition + one easing resolver.
- It **preserves the heart**: no compile, bus-native, narrow-waist Canvas, damage-disciplined, deterministic, define-once (CYD gets the model; the tween follows Lift #2).
- It **fits decompose/compose**: a state is a named configuration of contract-lowered attributes; a transition is a pure-function tween; a gesture is a bus producer. Every part decomposes to the contract, nothing forks to a backend.

**Recommendation: greenlight Phases A→B→C desktop-first; sequence Phase D behind Lift #2. Route the spec formalization to `factlattice-editor` after Phase A.**
