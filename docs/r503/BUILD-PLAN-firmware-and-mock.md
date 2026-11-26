# Build plan — the fingerprint device-logic, HAL mock, and R503 firmware

**Author:** composeux-architect · **Date:** 2026-08-18 · **Status:** draft, for David's review

> The goal David set as the pencils-down test: *is each spec good enough to build the ESP32 firmware + a
> HAL mock, and use them to test the whole non-hardware bringup?* This plan is the route to that milestone.
> It assumes the c-pass C-docs (C70–C73) come back tight; they are the plan's **inputs**, and each phase
> names the one it pins to. The S3 target enters mid-stack (Phase 2) running a *mock* sensor; the real R503
> is the only variable the last phase introduces.

---

## 1. The architecture — one device-logic, two realizations, and the seam between them

The load-bearing move is **P10, collapse the transport not the seam**: the device's *behavior* is written
once against two abstractions — a **FingerprintHAL** (the sensor operations) and the **bus** (publish /
subscribe) — and knows nothing about which side of the seam it runs on.

```
                        ┌─────────────────────────────────────────────┐
                        │  DEVICE-LOGIC  (specified ONCE by C73)        │
   fingerprint_storage  │  uuid-compare → delete-all → load-all →       │
   ── desired set ─────▶│  stamp-last; 4-state heartbeat machine;       │
   (ONE semantic msg)   │  count_authentication_attempts; failure obl. │
                        └───────────────┬───────────────┬──────────────┘
                          calls          │               │  publishes
                        ┌────────────────▼──┐         ┌──▼──────────────┐
                        │   FingerprintHAL   │         │      bus        │
                        │   (interface, C72) │         │  (interface)    │
                        └───┬────────────┬───┘         └──┬───────────┬──┘
              host / test │              │ silicon        │ in-proc   │ real
                    ┌─────▼─────┐  ┌─────▼──────┐   ┌─────▼────┐ ┌────▼─────┐
                    │ MockFP-HAL│  │  R503HAL   │   │ host bus │ │ esp bus  │
                    │ (reference│  │ (UART/C++) │   │  (test)  │ │  client  │
                    │  + faults)│  └────────────┘   └──────────┘ └──────────┘
                    └───────────┘
```

The device-logic is **realized twice** — Python (for host testing the whole non-hardware system) and C++
(for silicon). That is a fork risk (`[INSIGHT-FORK-DIVERGENCE-IS-A-CLASS]`), and §5 is how the codebase's
own idiom closes it. C73 is the single source of truth both realizations cite.

## 2. Extend, don't invent — what already exists

| Need | Existing pattern to clone | New home |
|---|---|---|
| Transactional-peripheral package (framing, driver, poller, transport, selftest) | `python/composeio/peripherals/nfc/` (`frames.py`, `pn532.py`, `poller.py`, `transport.py`, `selftest.py`) | `python/composeio/peripherals/fingerprint/` |
| A bus service holding state + answering the bus | `services/bus_storage/`, `services/document_store/` | `services/fingerprint_storage/` (`roster_push.py` is named in the EXAMPLE) |
| Pure-C++, host-testable HAL interface, spec-anchored | `firmware/reactive_core/src/actuator_hal.h` | `firmware/reactive_core/src/fingerprint_hal.h` |
| Host unit test of firmware logic against a mock, no silicon | `firmware/reactive_core/src/host_test_main.cpp` (`pio run -e native_test`, mock sink, `CHECK()`) | extend the same harness |
| Cross-language parity (Python canon ↔ C++ canon, host-tested) | the `*_canonical.cpp` family (`dsl_canonical`, `config_fetch_canonical`, …) | `fingerprint_device_canonical.cpp` |
| Device→bus announce / health / heartbeat plumbing | `python/composeio/device_announce.py`, `device_control.py` | reuse for the heartbeat machine |

Nothing fingerprint-related exists yet except the catalog part
(`render/engine/parts/catalog/r503-fingerprint.yaml`) — so this is greenfield code laid into established grooves.

## 3. The phases

Reordered per David's bring-up sequence (2026-08-18): the strongest move is to **isolate the sensor as the
LAST variable.** Stand the S3 firmware up with a *mock* sensor on the real bus first — proving firmware,
transport, announce, and the wire contract while the sensor is still fake — then wire the real R503 so that,
at bring-up, the sensor is the only thing that changed. This is `[CXC-VISION-EPISTEMICS]`
`rule-power-then-substrate-then-protocol` applied to the whole stack. There is an ESP32-S3 already connected
to the machine as the intended target.

### Phase 0 — Pin the seam *(pins to C70–C73; no code)*
Freeze the three contracts the rest builds against: the **FingerprintHAL operation set + closed failure set**
(C72), the **bus message contract** (C73 = the EXAMPLE — five semantic messages, payloads, cadences, receipt
algorithm, failure obligations), and the **identity / cast model** (C71 — casting `(module, person, character)`,
enroll-everywhere, roles/capabilities). The R503 realization facts (C70) feed Phase 4 only. **Done when:** the
C-docs are ratified and the HAL signature list + payload fields are transcribed from them (not invented).

### Phase 1 — The Python reference layer *(pins to C72 + C73; agent: composeio-expert)*
`python/composeio/peripherals/fingerprint/`: the `FingerprintHAL` interface, the `MockFingerprintHAL`
reference, and the device-logic reference (`device.py`). **Two properties make the mock the reference, not a
stub:** (a) **fault injection** — a write-fail on slot N, a delete-fail of error-class X (the negative control;
`rule-negative-control`: a mock that can only succeed proves nothing); (b) **call-order recording** — so a test
asserts *delete-before-write* and *stamp-strictly-last* rather than trust "it worked." The device-logic is the
full C73 behavior (uuid-compare branches; the four-state heartbeat machine — `fingerprint_ready` 60 s /
`push_needed` 15 s / `inoperable` 15 s / **silence = offline**; `count_authentication_attempts` zeroed only on a
genuine uuid change; the failure obligations). This is the **fast inner loop** (pytest, many devices in-process,
no flash) AND the **executable reference** the C++ realization is parity-tested against (§5). **Done when:** every
`CXC-FPBUS-SCEN-*` from the EXAMPLE passes in-process.

### Phase 2 — ⭐ The S3 firmware with a MOCK sensor, on the real bus *(pins to C72 + C73; agent: composeio-hardware-architect)*
The de-risking milestone. `firmware/reactive_core/src/`: `fingerprint_hal.h` (interface, mirroring
`actuator_hal.h`), `mock_r503_hal.cpp` (in-memory slots + fault injection — the C++ twin of Phase 1's mock),
the C++ device-logic (`fingerprint_device_canonical.cpp`, the C73 behavior), and the bus client. **Host-test it
green first** (`pio run -e native_test`, the existing harness) — then flash **the board named by `R503_EXPECT_SERIAL`** (host overlay). The S3 is now
a **real bus citizen speaking the full contract with a faked sensor**: it boots, joins the real transport,
announces, emits the heartbeats, accepts a push, runs the receipt algorithm against the mock HAL, and answers
with the semantic messages. **This proves firmware + transport + contract with the sensor still out of the
picture.** **Done when:** the S3 completes a push→ack→reconcile round over the real bus, faults injected in the
mock HAL produce the right failure messages, and the heartbeat cadences are observed on the wire.

### Phase 3 — `fingerprint_storage` + the binding widgets, against the S3 *(pins to C71 + C73; agents: composeio-expert + composeux-render-engineer)*
`services/fingerprint_storage/` — the **superset** store; the **cast/subset** selection from C71's identity
model (casting, enroll-everywhere, roles → the RBAC that rides to the device); `roster_push.py` (push-time
validations ≤ 20 / uuid-present; the **one** semantic desired-set, **no hash**); reconciliation-by-subscription
(the service learns each device's held uuid from `fingerprint_ready`, never by querying). **And the human ↔
character ↔ RBAC binding widgets** (in scope per David): bind a human to up to two templates, a character, and
the character's RBAC — the cast that rides to the device. Iterated against the Python devices for speed,
**proven against the real S3-mock-device on the bus.** **Done when:** an operator can, on the glass, bind a cast
and "start a game," the service pushes it, the S3 confirms `template_set_loaded`, and the fleet view reconciles
from the heartbeats — a full vertical slice minus the physical sensor. **This is the yardstick.**

### Phase 4 — The real R503, the only new variable *(pins to C70; agent: composeio-hardware-architect)*
David wires the R503 to the S3 "for real." Then `r503_hal.cpp` (UART, ESPHome `fingerprint_grow` model,
event-driven, no busy-wait; C70's hardware truths — **WAKEUP tracks 3.3VT, tie 3.3VT → 3V3**; AuraLed's
**seven** colours; the stray `0x55` after power-on; AutoEnroll/AutoIdentify **ack-streams** that desync a
one-response-per-command assumption) **replaces the mock HAL under the unchanged device-logic.** Because
everything else was proven in Phases 2–3, the sensor is the sole new variable. **Done when:** a real finger
enrolls and matches on the board named by `R503_EXPECT_SERIAL`, with eyes on the AuraLed — machine-green is not glass-green.

## 4. Critical path & what can start now

`C72 + C73 → Phase 1 ∥ Phase 2 → Phase 3 → Phase 4`. Phases 1 and 2 are **parallel** — the Python reference and
the S3 firmware both build from the HAL interface + the contract, which the EXAMPLE already fully specifies on
the device side. Phase 3 pins to C71's identity model. **C70's R503-specific facts are needed only at Phase 4**,
so the entire stack stands up and is demonstrated on the glass before a single sensor-specific line is written.
The scaffolding — the `peripherals/fingerprint/` skeleton, the `fingerprint_hal.h` / `mock_r503_hal.cpp` pair,
the service skeleton, and the acceptance suite transcribed from the EXAMPLE's `SCEN` nodes — is **build-ready
now** against the existing templates.

## 5. The anti-fork mechanism — extend the parity idiom, don't invent one

The device-logic lives in Python (Phase 2) and C++ (Phase 5). The codebase already handles exactly this with
the **`*_canonical.cpp` + `host_test_main.cpp` parity idiom**: a canonical C++ implementation mirrors the
Python one, and host-tests assert identical behavior against shared fixtures. Apply it verbatim:

- Each `CXC-FPBUS-SCEN-*` node in C73 (given/when/then) becomes a **shared fixture**.
- The Python suite (pytest, against `MockFingerprintHAL`) and the C++ suite (`native_test`, against a C++ mock
  HAL) assert the **same** scenarios. One spec, two realizations, **parity-tested** — so a divergence is a red
  test, not a field surprise. This is `[STRATEGY-...]` "a forked invariant living only in code" made impossible
  by construction.

## 6. Verification discipline (carried from `[CXC-VISION-EPISTEMICS]`)

- **Negative control on every failure path** — the mock's fault injector is it; a failure test with no injector
  proves nothing.
- **Order proven by the call-log**, not by outcome — delete-before-write and stamp-strictly-last are the two
  that a "green" run silently violates.
- **Machine-green ≠ glass-green** — Phase 5 needs eyes on the AuraLed and a real match, not just `ESP_OK`/`UART ack`.
- **One measurement path** — the mock HAL is the single reference both realizations are measured against.

## 7. Standing constraints (non-negotiable, baked into every phase)

- **No destructive / brick-risk testing, and no disproportionate rigor.** The R503 is a toy game sensor
  (`assurance: experiential`); rigor appropriate to a car or aircraft is a category error here. Every failure
  test is **mock-injected**; no overvoltage, no corrupted-template-to-real-silicon, no fault injection on the part.
- **No hashing anywhere** — identity is `template_set_uuid`, completeness is `entry_count`.
- **Silicon safety (Phase 5):** flash **only** the board named by `R503_EXPECT_SERIAL`; resolve its port via `ioreg` /
  `system_profiler` every time (paths are a lease); **never `esptool chip-id`** (it resets the board); do not
  touch the two C3s (`1C:DB:D4:…`) or the FTDI. Tie **3.3VT → 3V3** before power. Two rails on this board
  (R503 3.3 V, TM1637 white tubes 5 V) — re-derive back-drive under two rails, don't inherit the C3 single-rail argument.

## 8. Agent assignment summary

| Phase | Agent | Why |
|---|---|---|
| 1 (Python reference: mock HAL + device-logic) | **composeio-expert** | bus-native state engine, the executable reference |
| 2 (S3 firmware + mock sensor on the bus) | **composeio-hardware-architect** | ESP32 firmware, real transport, host-test harness |
| 3 — `fingerprint_storage` service | **composeio-expert** | superset store, cast selection, reconciliation |
| 3 — human/character/RBAC binding widgets | **composeux-render-engineer** | widget composition + namespace binding on the glass |
| 4 (real R503 HAL, silicon) | **composeio-hardware-architect** | UART bring-up, board seam, the hardware truths |

Each dispatch runs through `dispatch-subagent-build` (frozen wiring, data contract, acceptance bar, worktree
isolation for the composeio repo, commit-to-branch-then-land via git-expert). The R503 corpus/docs stay
outside git by ruling; the composeio-side code (Python + firmware) lands on `main` the normal way.
