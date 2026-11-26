# R503 Sensor UX — captured design spec (2026-08-20; decisions ratified 2026-08-21)

Captured from the r503-sensor-ux design session, then AUDITED against David's own transcript words
(`DSD-r503-sensor-ux-audit.md`) and CLOSED by David's answers to the six-decision DSD
(`DSD-r503-sensor-ux-decisions.md`, 2026-08-21). The table below is now RATIFIED, with one axis
(pattern distinguishability) confirmed-by-eye but explicitly tunable after we live with it (D3).

## David's ratified decisions (2026-08-21 DSD)
- **D1 — error/fault = red.** Confirmed (was an architect assumption; red is the alert axis).
- **D2 — capturing = PURPLE, not blue.** David OVERRULED the blue lean: *"Capture should be purple
  themed."* This makes the emergent semantic **purple = the sensor is actively reading a finger**
  (the capture instant AND the whole enroll flow), **blue = idle / armed / processing**, **red =
  reject / fault**. ⚠ Purple now spans both capture and enroll — to eyeball on hardware that it reads
  right (folds into D3's "TBD based on interaction experiences"). ⚠ **SUPERSEDED 2026-08-20** — on
  seeing it, David reverted the MATCH capture to BLUE and reserved purple for the enroll flow only:
  **blue = match, purple = enroll, red = reject/fault** (see "The match arc is ALL BLUE" below).
- **D3 — patterns confirmed distinguishable by eye; no re-demo needed. ⛔ LATITUDE CLOSED 2026-08-22.**
  David originally left this tunable: *"I was able to see the difference between the patterns, no need
  to re-run… after we land the changes it's possible that we will need to make some changes. TBD based
  on interaction experiences."* That "may want to make some changes" latitude is now **RESOLVED/CLOSED**
  (David 2026-08-22): having lived with the built interaction — in particular the **colour-shift timing
  after finger-to-sensor**, the axis he was most concerned about — he confirms no changes are needed.
  The pattern cells AND the finger-to-sensor colour-shift timing are **LOCKED by lived observation**,
  not merely provisional; there is **no open tripwire**. (Per `PRINCIPLE-CONDITIONAL-LATITUDE-MUST-CARRY-A-STOP`:
  the conditional carried a stop, the condition was observed on glass, and this is the verdict on it —
  not a hedge left to rot inside the plan.)
- **D4 — start polled, defer WAKEUP.** Confirmed. No 6th wire now.
- **D5 — ratified:** downgrade "empirically verified" to what was actually shown (color verified;
  patterns demonstrated + eye-confirmed; `off`/`gradual-on`/`gradual-off` never shown).
- **D6 — ratified:** the architecture framing (state-on-bus vs. expression-in-leaf) is the architect's
  synthesis, kept and attributed as such.

## Primitive inventory
- **Colors that RENDER: red, blue, purple only.** The AuraLED is a two-channel Red+Blue ring
  (purple = R+B together); there is NO green channel, so green/cyan/yellow/white (indices
  0x04-0x07) are accepted by the command parser but come up dark. The datasheet's "7 colors"
  is FALSE for this part. **(Empirically verified — the sweep David eyeballed.)**
- **The LED is a parameterized SCENE generator, not direct per-LED control.** AuraLedConfig
  (0x35) takes {control-mode, speed, color, count}; the R503's own MCU runs the animation.
  6 named control modes: breathing / flashing / steady-on / off / gradual-on / gradual-off.
  "N flashes then stop" = flashing mode with count=N. ⚠ Only **breathing** and **flashing** were
  demonstrated on hardware (at two speeds each); `steady-on`, `off`, `gradual-on`, `gradual-off`
  are named from the command's parameter shape and were NOT shown (D5).
- Three usable axes: **color (3) × pattern × speed** — David confirmed the demonstrated patterns are
  visually distinguishable (D3).

## State machine (finger-triggered)
armed/awaiting → [finger touch] → capturing → processing → terminal result → back to armed.
- **Trigger: GenImg-poll (finger-aware, ~100 ms).** Ratified (D4). The WAKEUP-interrupt (true
  low-power sleep) is a later option that needs a 6th wire (WAKEUP → GPIO; 3V3T-on-3.3V keeps it
  safe) and is UX-identical, so it is deferred until idle power matters.
- Capturing is near-instant, so its LED reads as a single blip ("I felt you"), not a sustained state.

## Feedback language (color × pattern) — RATIFIED
| state | color | pattern |
|---|---|---|
| armed / awaiting | blue | steady on 100% |
| capturing | blue | fast strobe (a blip) |
| processing | blue | breathing |
| granted | blue | 3 flashes |
| denied | red | 3 flashes |
| unrecognized | red | 3 flashes |
| enroll · awaiting | purple | steady on |
| enroll · capturing | purple | fast strobe |
| enroll · processing | purple | breathing |
| enroll success | purple | 3 flashes |
| error / fault | red | fast strobe, 2 s |

- **denied and unrecognized are INTENTIONALLY identical on the ring (3 red flashes)** — one
  "rejected" signal; the distinction is carried by the bus auth_event, not the LED. (David explicit.)
- **granted = 3 blue flashes, enroll-success = 3 purple flashes** — "three flashes" is the universal
  success gesture, colored by which flow (match = blue, enroll = purple).
- **The match arc is ALL BLUE** (David 2026-08-20, REVISING D2 "capture is purple"): armed → blue blip
  (capture) → blue breathing (processing) → result. A blue blip (fast flash) is told apart from armed
  (steady) and processing (breathing) by PATTERN, not colour — which keeps PURPLE cleanly reserved for the
  enroll flow. The rule collapses to: **blue = match, purple = enroll, red = reject/fault**; the device's
  feedback-flow mode sets the base colour, and all three shared states (awaiting, capture, success) follow it.

## Architecture (architect's synthesis — D6)
The two design halves: (a) the semantic STATE model, bus-visible/portable; (b) this R503-specific
EXPRESSION on the AuraLED. The device owns the mapping locally (state on the bus, behavior in the leaf).

## Follow-on work
- Rebuild the driver's `command_feedback()` to THIS ratified table (it currently assigns unrenderable
  green/cyan/yellow/white). Sequenced AFTER the real-HAL-bus round-trip lands, to avoid churning the
  same firmware file twice.
- Author the `r503-sensor-ux` `.lts` spec in the r503 corpus, encoding this table + the state machine +
  the six ratified decisions as DEC nodes (`dsd-source:` → this DSD).
