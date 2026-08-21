# BSG First Build — Spec vs. Implementation Gap Analysis

> Systematic comparison of `00-bsg-game-design.smd` and `01-first-build.md` against
> `bsg/engine/`, `bsg/client/`, `bsg/resources/`, and `bsg/run_game.py`.
>
> Scope is bounded to what `01-first-build.md` declares IN scope. Out-of-scope items
> (voice, physical hardware, campaign persistence, combat) are noted only if the
> implementation accidentally handles them or materially mishandles their absence.

**Generated:** 2026-03-18
**Reviewer:** Spec-Implementation Gap Analyzer
**Build state:** First build, no prior gap report

---

## Executive Summary

The first build is in strong shape. The architectural skeleton matches the spec's intent:
three distinct Claude roles, asymmetric FTL, fog-of-war XO state, one mock-mode flag,
auto-discovering resource registry, and a playable CTk client. All ten success criteria
from `01-first-build.md` are at least partially addressed.

**Eight gaps identified**, none are blocking. Two are P1 (meaningful documentation or
gameplay accuracy issue), four are P2 (drift that should be corrected), two are P3
(informational). No P0 issues.

| Priority | Count | Blocking? |
|----------|-------|-----------|
| P0 | 0 | — |
| P1 | 2 | No |
| P2 | 4 | No |
| P3 | 2 | No |

---

## Focus Area Analysis

---

### 1. Information Boundaries

**Question:** Does the XO prompt contain anything it shouldn't? Does the Cylon God see XO private data?

**Finding: COMPLIANT — with one meaningful gap.**

`ClaudeXO.get_action()` receives an `XOState` object, which is produced by `GameStateManager.get_xo_state()`. That method applies fog-of-war correctly:

- Exact resource numbers are converted to qualitative estimates (`_resource_estimate()`)
- Cylon positions are withheld — DRADIS contacts only appear if the basestar is *in the same system*
- Morale is converted to impression string (`_morale_impression()`)
- Ship status is a summary, not raw system data
- The XO system prompt explicitly states: "You do NOT know Cylon positions or identities" and "You see resource ESTIMATES, not exact numbers"

`ClaudeCylonGod.get_guidance()` receives only the output of `GameStateManager.get_cylon_state()`, which contains: `basestar_location`, `basestar_tylium`, `search_pattern`, `galactica_last_known` (Galactica's *current* location), `colonial_alert_level`, and `fleet_visited`. It does not receive XO private journal entries or exact resource numbers.

**Gap identified:**

---

#### GAP-001 — Cylon God receives Galactica's exact current location every turn
**Category:** SPEC_IMPL_MISMATCH
**Priority:** P2
**Spec says (`00-bsg-game-design.smd`, `[BSG-AI-CYLON-GOD]`):**
> `bus_subscriptions: ["ftl_*", "dradis_*", "alert_*", "_cylon/*"]`
> Notably absent: individual player comms, engineering details

The Cylon God's authorized subscriptions are FTL state, DRADIS, alert status, and the private cylon channel. Galactica's *current position* is not a Cylon subscription — the Cylons are supposed to be *searching* for the fleet, not tracking it in real time.

**Implementation has:**
`get_cylon_state()` always returns `"galactica_last_known": self.state.current_location` — the actual current position, updated every turn. This undermines the hide-and-seek gameplay loop described in `01-first-build.md`.

**Recommendation:** Track `galactica_last_known` as a separate field that only updates when the basestar and Galactica are in the same system (i.e., a DRADIS contact occurred). Between contacts, the Cylon God should only know where the fleet *was*, not where it *is*.

---

#### GAP-002 — XO prompt does not include XO private journal history
**Category:** SPEC_NOT_IMPLEMENTED
**Priority:** P2
**Spec says (`01-first-build.md`):**
> XO Interface: `xo_state.private_journal` (last N entries) is passed to `build_xo_prompt()`

The spec's XO interface definition explicitly lists "XO private journal (last N entries)" as an input to the XO prompt.

**Implementation has:**
`ClaudeXO.get_action()` receives `xo_state: XOState` and `personality_smd: str`. The `XOState` model does not include the private journal — journal entries are stored on the `BaseXO` model but never included in the XO prompt payload. Each turn, the XO gets no memory of what it previously thought or wrote.

**Recommendation:** Pass the last N journal entries from `BaseXO.private_journal` into the XO prompt. This is what makes the XO a *persistent* character rather than a stateless responder. Without it, journal entries are written but never read — they're currently write-only.

---

### 2. Asymmetric FTL

**Question:** Is Colonial FTL limited (Red Line, 33min recharge, 1ly max) while Cylon is superior (15ly range, 7min recharge)?

**Finding: COMPLIANT — numbers match spec, mechanics partially match.**

`galactica/data.json`:
- `max_range: 1.0 ly` — matches spec (1 system per jump)
- `recharge_minutes: 33` — matches spec (30-45 min range)
- `accuracy: 0.95` — implements mis-jump risk
- `networked: false` — correct

`basestar/data.json`:
- `max_range: 15.0 ly` — matches spec (10-20x longer per jump)
- `recharge_minutes: 7` — matches spec (5-10 min range)
- `accuracy: 0.99` — near-perfect precision
- `networked: true` — correct

`navigation.py` implements mis-jump (accuracy roll), fuel cost, Red Line check, and per-ship recharge timing. The basestar jumps every turn in `_advance_basestar()` via a charge threshold check (`> 80%`), which with 7-minute recharge and 10-minute turns means the basestar effectively catches up every turn. This creates the correct pressure asymmetry.

**Gap identified:**

---

#### GAP-003 — Red Line is implemented as 1.5x max_range, spec implies max_range IS the Red Line
**Category:** SPEC_IMPL_MISMATCH
**Priority:** P3
**Spec says (`01-first-build.md`):**
> Jump distance: "Limited by Red Line safety threshold" — the Red Line is the maximum safe limit

**Implementation has (`navigation.py` line 42):**
```python
if dist > ship.ftl.max_range * 1.5:  # Red Line is 1.5x max safe range
    return False, f"Target is beyond the Red Line..."
```

The implementation treats `max_range` as the *safe* range, with jumps to 1.5x allowed but risky (they still succeed via accuracy check). This is actually more nuanced than the spec requires, and creates a "beyond the Red Line" dramatic moment. However, the spec says `max_range: 1.0` is already the Red Line threshold, not 1.5x it.

**Recommendation:** This is a reasonable creative interpretation, but clarify: is `max_range` the safe limit or the absolute limit? Currently a Galactica jump of 1.0-1.5 ly is *allowed* but not blocked, while the spec implies 1.0 ly is the hard wall. Consider making jumps beyond `max_range` require explicit player confirmation ("Beyond Red Line — confirm?") rather than silently allowing them.

---

### 3. Character Model

**Question:** Do Pydantic models match the spec's BaseShip, BasePlanet, BaseCharacter, BaseXO, BaseMission?

**Finding: STRONG MATCH — one notable divergence.**

All five required base models exist in `engine/models/base.py` with appropriate fields. Comparison:

| Spec Required | Implemented | Notes |
|---|---|---|
| `BaseShip` | `BaseShip` | Full match. FTLDrive as sub-model is correct. |
| `BasePlanet` | `BasePlanet` | Full match. |
| `BaseCharacter` | `BaseCharacter` | Full match. |
| `BaseXO(BaseCharacter)` | `BaseXO(BaseCharacter)` | Match. |
| `BaseMission` | `BaseMission` | Match on structure. |
| `ShipSystem` | `ShipSystem` | Match. |
| `SystemStatus` | `SystemStatus` | Match. |

**Gap identified:**

---

#### GAP-004 — Character trait model is divergent from spec
**Category:** SPEC_IMPL_MISMATCH
**Priority:** P2
**Spec says (`00-bsg-game-design.smd`, `[BSG-CHARACTER]`):**
```yaml
traits:
  authority_style: 0.7
  trust_disposition: 0.3
  sacrifice_threshold: 0.8
  cylon_stance: 0.2
  pragmatism: 0.6
  composure: 0.7
  risk_tolerance: 0.5
```
Named semantic dimensions with specific gameplay effects (trait evolution rules, extreme threshold triggers, difficulty calibration by GM).

**Implementation has (`engine/models/base.py`, `BaseXO`):**
```python
risk_tolerance: float = 0.5
loyalty: float = 0.8
competence: float = 0.7
traits: dict[str, Any]  # untyped bag
```

The implementation has three typed floats (risk_tolerance, loyalty, competence) that don't match the spec's named dimensions, plus a freeform `traits` dict used for narrative booleans in `thorne/data.json` (`"pragmatic": true, "ruthless_when_necessary": true`).

The spec's named trait dimensions (`authority_style`, `trust_disposition`, `sacrifice_threshold`, `cylon_stance`, `pragmatism`, `composure`) are not present as typed fields. This matters because:
1. The GM prompt mentions trait-based difficulty calibration
2. The XO mechanics use trust thresholds for advice style
3. The spec defines trait evolution (shifting per decision, decay toward center)

**Recommendation:** Either add the spec's trait dimensions as typed float fields on `BaseXO` (and `BaseCharacter` for the Commander), or document that the first build intentionally defers full trait modeling. The freeform `traits` dict is not enough for the GM to do trait-based calibration.

---

#### GAP-005 — Thorne's data.json uses wrong name/rank
**Category:** SPEC_IMPL_MISMATCH
**Priority:** P3
**Spec says (`00-bsg-game-design.smd`):**
> id: thorne, name: "Colonel Marcus Thorne", callsign: "Ironside", rank: Colonel

**Implementation has (`thorne/data.json`):**
```json
"name": "Commander Thorne", "rank": "Commander"
```

Thorne is ranked Colonel in the spec, not Commander. "Commander Thorne" also creates a title collision with the player character who is referred to as "the Commander." Minor but will cause confusion in NPC dialogue.

**Recommendation:** Correct to `"name": "Colonel Marcus Thorne"`, `"rank": "Colonel"`.

---

### 4. Global Mock Flag

**Question:** Is `BSG_MOCK_MODE` properly implemented — one flag, fail-loudly, realistic mock responses?

**Finding: FULLY COMPLIANT.**

`engine/config.py` is a textbook implementation of the spec's intent:
- Single environment variable: `BSG_MOCK_MODE`
- `MOCK_MODE = os.environ.get("BSG_MOCK_MODE", "false").lower() == "true"` — defaults off
- `validate_config()` calls `sys.exit(1)` if `MOCK_MODE=false` but no `ANTHROPIC_API_KEY` — hard failure, not a warning
- `AIRoleManager.__init__()` uses a single `if MOCK_MODE` branch to instantiate all three mock or all three live roles — no per-interface toggles

Mock responses are realistic and contextually grounded:
- `MockGamemaster` generates narrative that references actual game state (water level, location, turn number, alert level)
- `MockCylonGod` uses fleet visit history to generate strategic directives
- `MockXO` has a priority decision tree (basestar contact > water critical > water low > tylium > morale > normal)

The `run_game.py` launcher comments correctly show `BSG_MOCK_MODE=true python3 run_game.py`.

No gaps in this area.

---

### 5. Resource Registry

**Question:** Does it auto-discover from the resources/ directory?

**Finding: FULLY COMPLIANT.**

`ResourceRegistry` in `engine/registry.py` auto-discovers from `bsg/resources/` using directory tree traversal. All five resource types load correctly:

- Ships: `resources/ships/*/data.json` — Galactica and Basestar both loaded
- Planets: `resources/locations/planets/*/data.json` — all 10 planets
- NPCs: `resources/characters/npcs/*/data.json` — 7 NPCs (6 spec + Leoben)
- XO candidates: `resources/characters/xo_candidates/*/data.json` — Thorne
- Missions: `resources/missions/*/mission.yaml` — Water Crisis

Personality SMDs are auto-discovered alongside `data.json` and the path stored in `personality_path`. Portrait images are auto-scanned from a `portraits/` subdirectory. Errors are collected (not thrown) with descriptive messages.

The registry handles JSON-to-Pydantic conversion robustly: coordinates as dict or list, systems as dict or list, FTL as nested dict.

One minor observation: the `galactica/data.json` ships resources incorrectly — it has `"resources": {"tylium": 80, "water": 60, ...}` as a nested key, but `BaseShip` expects `tylium`, `water`, `food`, `ammo` as top-level fields. The registry loads them via `BaseShip(**data)` which would miss the nested resources key. This is a data file issue, not a registry issue.

---

#### GAP-006 — galactica/data.json resources are nested under "resources" key but BaseShip expects flat fields
**Category:** SPEC_IMPL_MISMATCH
**Priority:** P1
**Spec says:** `BaseShip` has `tylium: float`, `water: float`, `food: float`, `ammo: float` as top-level fields.

**Implementation has (`resources/ships/galactica/data.json`):**
```json
"resources": {
  "tylium": 80,
  "water": 60,
  "food": 70,
  "ammo": 90
}
```

**Implementation has (`engine/models/base.py`):**
```python
tylium: float = 100.0
water: float = 100.0
food: float = 100.0
ammo: float = 100.0
```

The `data.json` uses a nested `resources` sub-object, but `BaseShip` expects these as top-level fields. The registry does not handle this remapping (it handles FTL and systems nesting, but not resources). When Galactica loads, `tylium`, `water`, `food`, and `ammo` will all default to `100.0` instead of the intended `80`, `60`, `70`, `90`. The `GameStateManager` then initializes `total_water = galactica.water` — so the game starts with water at 100, never triggering the Water Crisis mission's opening urgency.

**Recommendation:** Either flatten the `data.json` resources to top-level fields, or add resource remapping in the registry's `_load_ships()` method, matching the pattern already used for FTL and systems.

---

### 6. Fog of War

**Question:** Does the XO get degraded/filtered state, not full game state?

**Finding: LARGELY COMPLIANT — spec's full vision not fully realized in first build, but first build requirements are met.**

`GameStateManager.get_xo_state()` builds a proper `XOState` that withholds:
- Exact resource numbers (converted to qualitative estimates)
- Cylon positions (withheld unless basestar is in-system)
- Full system data (summary string only)
- Basestar search pattern history

The `XOState` model's docstring explicitly says: "The XO does NOT see: cylon positions, full resource numbers (gets estimates), other players' actions, or the GM's internal state."

**What the spec describes but the first build defers (correctly, per `01-first-build.md`):**
- DRADIS degradation modes (ECM ghosts, sensor blind spots, nebula interference)
- Delayed damage reports (10-30s delay between damage and XO awareness)
- Unreliable NPC reports (panicked ensign reporting wrong deck number)
- Per-section hull status with fog-of-war

These are part of the full design but are out of scope for first build. The first build's simpler fog-of-war is appropriate.

**Gap identified (see GAP-001 above):** The `galactica_last_known` field in the Cylon state leaks current position, which is a related information boundary issue.

No additional fog-of-war gaps specific to the XO.

---

### 7. State Persistence

**Question:** Does game state save to document store?

**Finding: PARTIALLY COMPLIANT — save/load implemented but intentionally not wired.**

`GameStateManager` has complete save and load methods:
- `save_to_docstore()` serializes full `GameState` + event history to `bsg/games/{game_id}`
- `load_from_docstore()` deserializes and restores state

The docstore is accessed via a generic `self.docstore.put(key, json)` / `self.docstore.get(key)` interface, which is the right abstraction.

**Gap identified:**

---

#### GAP-007 — Docstore client is hardcoded to None; save is never called
**Category:** SPEC_NOT_IMPLEMENTED
**Priority:** P1
**Spec says (`01-first-build.md`, success criterion 10):**
> Game state saves to document store at mission end

**Implementation has (`engine/game.py` line 52):**
```python
self.state_mgr = GameStateManager(self.registry, docstore_client=None)
```

The docstore client is hardcoded to `None`. `save_to_docstore()` silently returns `False` when `self.docstore` is `None`. The method `complete()` in `MissionRunner` does not call `save_to_docstore()`. There is no call to save anywhere in the game loop.

The `config.py` has `DOCSTORE_HOST`, `DOCSTORE_PORT`, and `BUS_STORAGE_URL` configured, and the `validate_config()` function logs them — but no client is constructed and no save happens.

**Recommendation:** This is the one first-build success criterion that is entirely unimplemented. Wire up a minimal docstore client (even a simple HTTP POST to the configured endpoint) or, for the first build, implement a local JSON file fallback (`bsg/saves/{game_id}.json`) so state is at least written somewhere. Call `save_to_docstore()` from `MissionRunner.complete()` and optionally on each turn advance.

---

### 8. Mission State Machine

**Question:** Does Water Crisis have 5 phases with proper triggers?

**Finding: LARGELY COMPLIANT — phases match, trigger wiring has one bug.**

`resources/missions/water_crisis/mission.yaml` defines exactly 5 phases:
1. `briefing` — trigger: `water_below_20`
2. `active` — trigger: `location_has_water`
3. `crisis` — trigger: `basestar_arrival`
4. `resolution` — triggers: `extraction_complete`, `fleet_jumps_away`
5. `debriefing` — trigger: `resolution_complete`

All 5 trigger conditions are implemented in `MissionRunner._evaluate_trigger()`:
- `water_below_20` — checks `total_water < 20.0`
- `location_has_water` — checks `mission_objectives["water_source_found"]`
- `basestar_arrival` — checks basestar location == current location
- `extraction_complete` — checks `_extraction_progress >= 100.0`
- `fleet_jumps_away` — returns `False` (explicitly stub, noted in comment)
- `resolution_complete` — checks all primary objectives done

**Gap identified:**

---

#### GAP-008 — Phase timer elapsed_minutes is never updated in check_triggers()
**Category:** SPEC_IMPL_MISMATCH
**Priority:** P2
**Spec says:** Timer-based phases auto-advance when their `duration_minutes` expires.

**Implementation has (`engine/mission_runner.py` lines 111-117):**
```python
if current.duration_minutes > 0:
    self._phase_elapsed_minutes += 0  # caller should update this
    if self._phase_elapsed_minutes >= current.duration_minutes:
```

`check_triggers()` adds `0` to `_phase_elapsed_minutes` — a literal comment-admitted placeholder. The `update_elapsed()` method exists and is called correctly from `game.py`'s `process_turn()`. However, `check_triggers()` runs its timer check *before* `update_elapsed()` is called in the game loop (see `game.py` lines 161-167: `update_elapsed()` is called, then `check_triggers()`). Actually looking at the order: `update_elapsed(self._turn_minutes)` is called first, then `check_triggers()`. So the elapsed time IS correctly updated.

The real bug is the `+= 0` line inside `check_triggers()`. It's dead code that looks like a placeholder that was never removed. Since `update_elapsed()` correctly maintains `_phase_elapsed_minutes`, the timer-based advance will work — but only because `update_elapsed()` is called first externally. The `check_triggers()` method relies on external state management rather than self-contained logic, which is fragile.

**Additionally:** The `briefing` phase is where the mission *starts*, but its trigger (`water_below_20`) is a transition trigger *into* briefing, not out of it. The YAML uses triggers to describe when a phase becomes active, but the `MissionRunner` uses triggers to determine when to leave the current phase. The briefing phase has a trigger that says "water below 20 activates this phase" but the runner will evaluate it as "water below 20 exits this phase." In practice this means the briefing phase never exits on a trigger — it only advances via GM suggestion or manual call. This is probably intentional behavior for the first build but differs from the spec's trigger semantics.

**Recommendation:** Remove the `+= 0` placeholder line from `check_triggers()` and add a comment that phase elapsed time is managed by `update_elapsed()`. Clarify in a comment whether triggers are entrance conditions or exit conditions.

---

## What's NOT in the Implementation (First Build Deferred Items — Expected)

These are spec items explicitly out of scope for the first build. Listed for completeness, no gaps filed.

| Spec Feature | Status | First Build Scope |
|---|---|---|
| Voice TTS/STT | Not implemented | Deferred (v2/v3) |
| Physical hardware integration | Not implemented | Deferred |
| Campaign persistence across sessions | Not implemented | Single session first |
| Character trait evolution (decision history) | Not implemented | Deferred |
| XO location model (hangar/brig/engineering) | Not implemented | Deferred |
| Hull damage per-section display | Not implemented | Deferred |
| Multiple XO candidates (Vasquez, Kirin) | Not implemented | Thorne only, correct |
| Cylon player mechanics | Not implemented | Single player, correct |
| Speaker-aware wake word | Not implemented | Deferred |
| Combat system | Not implemented | Correct |
| XO private SMD (encrypted campaign doc) | Not implemented | Deferred |
| Deity mode evolution | Not implemented | Static mode, acceptable |
| D/C teams, viper squadrons as resources | Not implemented | Deferred |

---

## What's in the Implementation but Not in the Spec

---

#### EXTRA-001 — CTk action buttons use different action names than engine processes
**Category:** IMPLEMENTED_NOT_SPECIFIED
**Priority:** P3

The client has buttons: "Wait (10 min)", "Scan System", "Begin Extraction", "Launch Raptors". These dispatch action types `wait`, `scan`, `extract`, `scout`. The engine's `process_turn()` handles `jump`, `extract`, `scout`, `set_alert`, `address_crew`, `consult_staff`. The `wait` and `scan` actions fall through to the default case (`result["note"] = "Action acknowledged"`), meaning "Wait" and "Scan System" are button-visible but engine-invisible. Not a gap against spec (spec doesn't define button labels), but the `scan` action does nothing at the engine level.

**Recommendation:** Wire `scan` to `_execute_scout()` or remove the Scan System button.

---

#### EXTRA-002 — Jump is double-processed in client
**Category:** IMPLEMENTED_NOT_SPECIFIED
**Priority:** P2

In `client/app.py`, the `_on_jump()` handler does:
```python
result = await self.engine.execute_jump(destination)
turn = await self.engine.process_turn({"type": "jump_complete", "minutes": 5})
```

`execute_jump()` is called directly (bypassing `process_turn()`), then `process_turn()` is called with a synthetic `jump_complete` action. But `process_turn()` checks `if action_type == "jump"` and calls `self.execute_jump(target)` itself. Since `jump_complete` is not `jump`, the jump doesn't re-execute — but time advances twice (once in `execute_jump`'s event, once in `advance_turn(5)`), and GM/XO are called twice per jump. This means every jump costs 5 minutes of extra time advance and triggers two full AI role calls.

**Recommendation:** Remove the direct `execute_jump()` call from `_on_jump()` and route jump through `process_turn({"action": "jump", "target": destination})`.

---

## Summary

### By Category

| Category | Count | P0 | P1 | P2 | P3 |
|----------|-------|----|----|----|----|
| SPEC_NOT_IMPLEMENTED | 1 | 0 | 1 | 0 | 0 |
| SPEC_IMPL_MISMATCH | 6 | 0 | 1 | 4 | 1 |
| IMPLEMENTED_NOT_SPECIFIED | 2 | 0 | 0 | 1 | 1 |
| **Total** | **9** | **0** | **2** | **5** | **2** |

### Gate Assessment

- P0 issues: **0** — build is not blocked
- P1 issues: **2** — both addressable in a single session:
  - GAP-006: Galactica starts with wrong resource levels (water at 100, not 60); Water Crisis never triggers urgency
  - GAP-007: Game state is never saved to docstore; success criterion 10 not met

### Top Recommendations

1. **Fix GAP-006 first.** The nested `resources` key in `galactica/data.json` means the game starts with full water and the Water Crisis never naturally escalates. This defeats the core game loop. Either flatten the JSON or add a remapping step in `_load_ships()`.

2. **Fix GAP-007 second.** The docstore save is the one first-build success criterion that is fully unimplemented. A local JSON file fallback is acceptable for first build — five lines of code.

3. **Fix GAP-002 (XO journal not read back).** The XO writes journal entries every turn but never reads them. It has no memory of its previous reasoning. Passing the last 5 journal entries into the XO prompt is what makes the character feel persistent rather than stateless.

4. **Fix GAP-001 (Cylon God tracking exact Galactica position).** The `galactica_last_known` field updates every turn. The hide-and-seek tension requires the Cylons to *search* for the fleet, not track it. Track last *confirmed contact* instead.

5. **Fix EXTRA-002 (double-processing of jumps in client).** Jumps currently advance time twice and call AI roles twice. Low effort fix, meaningful performance/logic improvement.

---

## First Build Success Criteria Status

| Criterion | Status | Gap |
|---|---|---|
| 1. Engine starts, loads resources, validates with Pydantic | PASS | GAP-006 causes wrong resource values |
| 2. Galaxy map with 10 systems queryable | PASS | — |
| 3. FTL jump works (coordinate entry → jump → arrive) | PASS | — |
| 4. Gamemaster Claude receives full state, returns narrative + NPC dialogue | PASS | — |
| 5. Cylon God Claude receives filtered state, returns guidance | PARTIAL | GAP-001 leaks current position |
| 6. XO Claude receives situation display, returns action + dialogue + journal | PARTIAL | GAP-002 journal not fed back |
| 7. One complete Water Crisis mission playable start to finish | PARTIAL | GAP-006 breaks water urgency, GAP-008 timer issue |
| 8. XO face display shows correct expression | PASS | Portrait images not yet generated but architecture correct |
| 9. Planet artwork displays on arrival | NOT VERIFIED | `artwork_path` field present but no images generated yet |
| 10. Game state saves to document store at mission end | FAIL | GAP-007 |
