# Attention Panel — Build Spec + Wiring Guide

Status: **settled design, ready to build.** Design session closed 2026-08-20. This document
transcribes the locked decisions and wiring facts as given; it does not redesign anything.
Open items are called out explicitly in the [Parked / Open](#parked--open) section at the
end — do not resolve them by guessing.

## 1. What it is

A **multi-room ComposeBus status panel**: N identical **ESP32-S3** bus-peers, each showing
which of David's 2–4 concurrent Claude instances needs his attention right now.

Each panel carries a pool of **slots**. A Claude instance registers itself to a slot
(announce-then-bind — see [§2](#2-bus-contract)). Per slot, the panel drives:

- a **WS2812 addressable RGB LED** — colour = state, the across-the-room triage signal, and
- an **SSD1306 I2C OLED** — the instance's name, a state icon, and a live `MM:SS` dwell-timer.

**2–3 identical panels** live in different rooms (e.g. office, kitchen/dinner, living room/TV)
so David sees status wherever he is. Every panel is an independent bus-peer subscribing the
*same* retained bus state and rendering it locally — **publish-once, realize-many**. There is
no coordination protocol between panels: each resolves the same slot assignment from the same
bus state, so they agree without talking to each other, and a panel powered on late just
catches up from retained messages.

## 2. Bus contract

All topics are plain MQTT on the existing ComposeBus broker. `<instance-id>` is a short stable
id per Claude instance (e.g. `cioa`, `ds` — see [Parked / Open](#parked--open) item 5 for the
full name list).

### 2.1 State (retained)

```
attention/<instance-id>/state
```

Retained, one message per instance. Payload is one of:

| Value | Meaning |
|---|---|
| `working` | actively working, does not need David |
| `free` | idle / caught up, available to take on something |
| `question` | has a question, wants an answer when convenient |
| `review` | wants a review / decision (e.g. a DSD) |
| `blocked` | hit something unexpected, cannot proceed without David — highest urgency |

**Colour map** (tunable, not fixed in silicon — firmware constants):

| State | Colour |
|---|---|
| `working` | off / dim |
| `free` | green |
| `question` | amber |
| `review` | cyan |
| `blocked` | red / strobe |

### 2.2 Registration (announce-then-bind)

```
attention/<instance-id>/register
```

Payload: `{"name": "CIoA"}` (short display name for the OLED) plus a periodic heartbeat on the
same topic (retained, refreshed). Slot binding is a pool, not a hardcoded per-instance map:

- On seeing a `register` for an instance not currently bound to a slot, a panel assigns the
  next free slot.
- On `unregister` (explicit) or heartbeat-timeout, the panel frees that instance's slot.
- Because every panel derives slot assignment **deterministically from the same retained bus
  state**, all panels converge on the same assignment independently — no cross-panel
  coordination message is needed.

### 2.3 Elapsed-time (state-on-bus, behaviour-in-leaf)

The dwell-timer is **not** streamed over the bus. Each panel counts `MM:SS` **locally**, one
tick loop, from the moment it observes a flag state (`question` / `review` / `blocked`) raised
for a slot. The OLED shows:

- the instance name, while `working` or `free`;
- `MM:SS` counting up, once the state is `question` / `review` / `blocked` — reset back to the
  name the moment state returns to `working` or `free`.

Panels may subscribe the fleet's retained `time/base` topic for a shared wall-clock reference
(useful for consistent multi-panel timestamps), but the timer itself is computed locally from
whenever this panel last observed the state transition — it is not replayed from the bus.

## 3. Wiring

**ESP32-S3 devkit (WROOM-1)**, powered over USB 5 V.

### 3.1 WS2812 (addressable RGB)

- DIN → **330 Ω series resistor** → a free S3 GPIO. **Recommended: GPIO4.**
  - Avoid the strapping pins **GPIO0 / GPIO3 / GPIO45 / GPIO46** and the USB-JTAG pins
    **GPIO19 / GPIO20**.
- **1000 µF capacitor across 5 V/GND** at the strip input, to absorb inrush.
- ⚠ **Logic-level caveat**: WS2812 DIN wants 5 V logic; the S3's GPIO is 3.3 V. This usually
  works over a short wire run, but if the strip is flaky (flicker, wrong colours, dead
  pixels), add a **74AHCT125** level shifter between GPIO4 and DIN, or sacrifice the first
  LED in the chain as a buffer (it absorbs the marginal signal and the rest of the strip reads
  clean 5 V logic from it). Note this as a known risk, not a redesign — try direct-drive
  first per the prove-substrate step ([§5](#5-prove-substrate-do-this-before-any-bus-code)).

### 3.2 I2C (SSD1306 OLEDs)

- S3 default I2C pins: **SDA = GPIO8, SCL = GPIO9**.
- **4.7 kΩ pull-ups** on SDA and SCL (many SSD1306/TCA9548A breakout modules already carry
  these onboard — check before adding a second set).
- **Address limit**: each SSD1306 is fixed at **0x3C** (0x3D is selectable via an on-board
  jumper on some modules) — so a bare I2C bus supports at most **2** OLEDs.
- **For more than 2 slots**, use a **TCA9548A I2C mux (address 0x70)**: the mux sits upstream
  on the S3's SDA/SCL, and each SSD1306 hangs off its own mux channel (SD0/SC0, SD1/SC1, …),
  each still answering at 0x3C — the mux, not the address jumper, is what disambiguates them.

### 3.3 Wiring table — 4-slot panel

Reference build: **1× WS2812 strip of 4 pixels + 1× TCA9548A + 4× SSD1306**, all on one
ESP32-S3 devkit.

| S3 pin | Net | Device pin |
|---|---|---|
| 5V (USB) | `+5V` | WS2812 strip V+ |
| GND | `GND` | WS2812 strip GND |
| GPIO4 | `WS2812_DIN` (via 330 Ω) | WS2812 strip DIN (pixel 0) |
| — | `+5V` / `GND` | 1000 µF cap, across strip V+/GND at the strip end |
| 3V3 | `+3V3` | TCA9548A VIN |
| GND | `GND` | TCA9548A GND |
| GPIO8 | `SDA` | TCA9548A SDA (upstream) |
| GPIO9 | `SCL` | TCA9548A SCL (upstream) |
| — | `+3V3`/`GND` | 4.7 kΩ pull-up SDA→3V3, 4.7 kΩ pull-up SCL→3V3 (if not already on-module) |
| TCA9548A SD0/SC0 | `SDA0`/`SCL0` | SSD1306 #1 (slot 0) SDA/SCL @ 0x3C |
| TCA9548A SD1/SC1 | `SDA1`/`SCL1` | SSD1306 #2 (slot 1) SDA/SCL @ 0x3C |
| TCA9548A SD2/SC2 | `SDA2`/`SCL2` | SSD1306 #3 (slot 2) SDA/SCL @ 0x3C |
| TCA9548A SD3/SC3 | `SDA3`/`SCL3` | SSD1306 #4 (slot 3) SDA/SCL @ 0x3C |
| 3V3 | `+3V3` | each SSD1306 VCC |
| GND | `GND` | each SSD1306 GND |

Each SSD1306 stays at its factory default address (0x3C) — the mux channel is the only thing
that distinguishes slot 0 from slot 1, 2, 3.

### 3.4 Power budget

- WS2812: up to ~60 mA/pixel × 4 pixels = ~240 mA worst case (full white, full brightness).
- SSD1306: ~20 mA × 4 = ~80 mA.
- Total worst case: ~320 mA, comfortably inside a USB 5 V supply (typical port: 500 mA–1 A+).
  No separate 5 V supply needed for a 4-slot panel.

## 4. BOM

| Qty | Part |
|---|---|
| 1 | ESP32-S3 devkit (WROOM-1) |
| 4 (or 1 strip of 4) | WS2812 addressable RGB LED |
| 4 | SSD1306 I2C OLED, 0.91" or 0.96" |
| 1 | TCA9548A I2C mux breakout (address 0x70) |
| 1 | 330 Ω resistor (WS2812 DIN series) |
| 2 | 4.7 kΩ resistor (I2C pull-ups, if not already on-module) |
| 1 | 1000 µF capacitor (WS2812 supply) |
| 1 | protoboard |
| 1 | USB cable (5 V power + programming) |
| optional | 74AHCT125 level shifter (only if WS2812 direct-drive proves flaky) |

## 5. Prove-substrate — do this before any bus code

Before writing a single line of MQTT/slot-manager code, verify the physical build works in
isolation:

1. **WS2812 colour-wipe** — cycle each of the 4 pixels through a few solid colours in
   sequence. Confirms DIN wiring, series resistor, and the cap are all correct.
2. **Single-OLED "HELLO"** — write literal text to one SSD1306 over I2C (through the mux,
   channel 0) and confirm it is legible on the glass.

Do not trust a green `ESP_OK` / a successful I2C write as proof the display lit — that only
proves the bus transaction completed, not that anything visible happened. Confirm both steps
**by eye** before layering on WiFi/MQTT/slot logic.

## 6. Firmware architecture (outline)

Recommended stack: **Arduino framework** on the S3 (matches the PubSubClient tier already used
in `firmware/reactive_core/`) + **u8g2** for the SSD1306s + **Adafruit_NeoPixel or FastLED**
for the WS2812 strip.

Modules:

- **WiFi + MQTT connect** — standard PubSubClient bring-up, matching the existing
  `firmware/reactive_core/` / `firmware/esp32_mqtt_client/` pattern.
- **Bus subscriptions** — `attention/+/state` and `attention/+/register` (wildcard on
  instance-id); optionally `time/base`.
- **Slot manager** (announce-then-bind) — on `register`, assign the next free slot to a
  not-yet-bound instance; on `unregister` or heartbeat-timeout, evict and free the slot.
  Deterministic purely from observed bus state, so every panel converges on the same
  assignment independently.
- **Per-slot render**:
  - WS2812: colour (+ blink/strobe for `blocked`) driven by the slot's current state — the
    leaf owns the animation, nothing streamed from the bus beyond the state value itself.
  - OLED: name ↔ `MM:SS` timer (per [§2.3](#23-elapsed-time-state-on-bus-behaviour-in-leaf))
    + state icon.
- **Tick loop** — drives the `MM:SS` counters and any blink/strobe timing.

**Identical firmware runs on every panel** — one build, define-once, flashed to N boards; the
only variance across panels is which room it physically sits in. No per-panel configuration
is required for the panels to agree with each other.

## 7. Instance-side publish helper (sketch)

How a Claude instance updates its own status — manual-first, not automated yet (see
[Parked / Open](#parked--open) item 3). Conceptually a tiny retained MQTT publish:

```
# pseudocode / CLI shape
mqtt_publish \
  --topic "attention/<instance-id>/state" \
  --payload "question" \
  --retain

mqtt_publish \
  --topic "attention/<instance-id>/register" \
  --payload '{"name": "CIoA"}' \
  --retain
```

This is a manual step (or a short helper script an instance invokes) today — not wired into
automatic session-lifecycle hooks. Auto-hooks (state flips on tool-call boundaries, session
start/end, etc.) are future work, not part of this build.

## 8. Parked / Open

These are inferred defaults, not settled — confirm with David before treating them as final:

1. **Slot count**: 4 vs 6 slots per panel. This doc's wiring table is for 4; a 6-slot build
   changes the WS2812 pixel count and adds 2 more TCA9548A mux channels + SSD1306s (mux has
   8 channels total, so 6 slots still fit on one TCA9548A).
2. **Slot assignment policy**: dynamic first-free (lean, simplest) vs stable per-identity
   (same instance always lands on the same slot across sessions).
3. **Instance-side publish mechanism**: manual helper (as sketched in §7) vs auto-hooks tied
   to session lifecycle events. Manual-first is the stated starting point.
4. **David's full instance → display-name list** (only `CIoA` = ComposeIO Architect and
   `DS` = Data Scientist are confirmed so far).
5. **Elapsed-time cadence** — how often the tick loop actually needs to redraw (e.g. once/sec
   is very likely fine; not yet measured against OLED refresh cost).

## Wiring facts I'm not fully certain of — flag for review

- The **MQTT broker host/port** for the `attention/*` topics isn't specified in the source
  design note. `firmware/reactive_core/` defaults to `mqtt-broker.local:1884`, and
  composeio-wide memory says the live broker is still `mqtt-broker.local` (.75) — I've
  assumed the panel firmware reuses that same broker, but nexus't hardcoded a value into
  this doc since the design note didn't state one explicitly.
- The **74AHCT125 level-shifter wiring** (which of its 4 buffers to use, OE tie-off) isn't
  detailed in the source note beyond "add one if flaky" — I've left it as a named contingency
  rather than inventing pinout specifics that weren't given.
