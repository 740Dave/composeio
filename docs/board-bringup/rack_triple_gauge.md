# Board bring-up: `rack_triple_gauge`

**Three x27.168 stepper-needle gauges + three 12 V LED backlights, ESP32-C3 Super Mini (RISC-V),
direct WiFi/MQTT ComposeBus peer. David's first custom PCB.**

> ⛔ **THE BOARD DOES NOT EXIST YET.** It is designed and not fabbed. This document is a
> **build-ahead**: the firmware is written and builds, the homing is designed and host-proven, and
> the ladder below is the procedure the board will be checked against when it arrives. **No number
> in this document is a bench measurement.** Rows that need one are marked ⇢MEASURE.

> ⛔ **THIS IS THE FLEET'S FIRST NON-RASTER REALIZATION BACKEND.** It has no glass, no
> `PanelGeometry`, no `DisplayDriver`, no touch and **no `/capture.bmp`**. It does **not** implement
> `BoardProfile`; it implements `RealizationBackend` (`cyd/include/realization.h`), which is a
> **peer** of `BoardProfile` under `[CXC-VISION-SEMANTIC-REALIZATION]` — *"the widget is semantic,
> the backend is the realization."*

Hardware ground truth: `${MAKER_HOME}/pcb/rack_triple_gauge/generate_pcb.py` (there is no
`.kicad_sch`; the board is emitted programmatically) confirmed against the pad→net assignments in
the emitted `rack_triple_gauge.kicad_pcb`. Corpus characterisation: `[PCB-GAUGE3]` in
`${MAKER_HOME}/pcb/boards-overview.lts`, derived at `[SPEC:PCB-BOARDS-OVERVIEW]`
(`corpora/platform-specs/64-pcb-board-landscape.lts`).

---

## 0. The strapping verdict — read this before anything else

**The premise this bring-up started from was wrong, and finding that out is the most valuable
result here.**

The brief asserted that **GPIO0 is a strapping pin on the ESP32-C3**, that it is wired to a 2N2222
base through 150 Ω, and asked whether that could hold the part in the wrong boot mode — flagging it
as a probable pre-fab board defect.

**It cannot, because GPIO0 is not a strapping pin on this part.**

| pin | ESP32-C3 role | on this board | verdict |
|---|---|---|---|
| **GPIO0** | ordinary GPIO (ADC1_CH0, XTAL_32K_P) | `LED1_GPIO` → 150 Ω → 2N2222 base | **BOOT-SAFE** |
| **GPIO1** | ordinary GPIO (ADC1_CH1, XTAL_32K_N) | `LED2_GPIO` → 150 Ω → 2N2222 base | **BOOT-SAFE** |
| **GPIO3** | ordinary GPIO (ADC1_CH3) | `LED3_GPIO` → 150 Ω → 2N2222 base | **BOOT-SAFE** |
| **GPIO2** | **STRAP** — must be HIGH at reset | `J_ESP_L` pin 3 **and `J_EXP_ESP` pin 1** | latent trap, see below |
| **GPIO8** | **STRAP** — HIGH for download boot | `J_ESP_R` pin 5 (module LED) | fine |
| **GPIO9** | **STRAP** — LOW forces download boot | `J_ESP_R` pin 4 **and `J_EXP_ESP` pin 4** | latent trap, see below |

The ESP32-C3's boot straps are **GPIO2, GPIO8 and GPIO9, and only those three** (ESP32-C3 datasheet
§2.4). This is corroborated *inside this fleet, on this exact module*:
`[SPEC:CXC-BOARD-HW]` `strapping-and-uart-caveats` says in as many words *"GPIO2, GPIO8 and GPIO9
are boot-mode straps: nothing may hold them LOW at reset"*, and `pin-occupancy` on the same board
marks 2/8/9 STRAPPING while using 0/1/3 as ordinary display pins.

**Why the assumption was reasonable and still wrong:** GPIO0 *is* the boot strap on the classic
ESP32, and GPIO0/GPIO46 are on the ESP32-S3. It is the same shape of error as the fleet's
UART-crosses / CAN-does-not collision — a habit from the last part carried into the next one. The
prior "C3 + BC337 finding" this board was checked against was about GPIO2/8/9, not GPIO0.

**Second-order check, also clear.** At reset the C3's GPIOs are high-Z inputs, so no base current
flows through the 150 Ω, the transistors are off and the strips are dark until LEDC claims the
pins. Nothing on this board pulls any strap in either direction. **Do not respin for this.**

### What the check *did* find — `J_EXP_ESP` exposes two straps, unlabelled

`J_EXP_ESP` breaks out six GPIOs: **pin 1 = GPIO2**, pin 2 = GPIO6, pin 3 = GPIO7,
**pin 4 = GPIO9**, pin 5 = GPIO10, pin 6 = GPIO20. Two of the six are boot straps and the silkscreen
does not say so.

Anything a future user hangs on pin 1 or pin 4 that presents a low impedance to ground at reset —
an LED to GND, an NPN base, a switch — **prevents boot**. The probability is not low: this board's
own design language is *"GPIO → 150 Ω → NPN base → GND"* repeated three times, so a fourth channel
on `J_EXP_ESP` pin 1 is the obvious next move, and it bricks boot.

- **Fix A, pre-fab, free:** silkscreen pin 1 `GPIO2 *STRAP*` and pin 4 `GPIO9 *STRAP*`.
- **Fix B, respin:** 10 kΩ pull-ups to +3V3 on GPIO2 and GPIO9, so they default boot-safe and any
  expansion load must overcome a pull-up — turning a silent brick into a visible design decision.
- **Safe use pattern:** **I²C idles HIGH**, so a pulled-up second I²C bus on GPIO2/GPIO9 *satisfies*
  the strap constraint instead of fighting it (`[SPEC:CXC-BOARD-HW]` `touch-recovery-path`). Those
  two pins are fine for I²C and hostile to a low-side switch.

---

## 1. Identity and pin map

| | |
|---|---|
| chip | ESP32-C3 Super Mini, **socketed** on 2× 1×8 headers (replaceable — and it is the recovery path) |
| arch | RV32IMAC, single core, 160 MHz, **no FPU** (irrelevant here — no rasterizer) |
| flash | 4 MB; the shared `partitions.csv` applies unchanged |
| PSRAM | none, and nothing wants it |
| device id | `gauge3-XXXXXX` (last 3 MAC octets — unknown until a module is seated) |
| port | ⚠ **not pinned.** No board exists; a guessed port is a loaded gun |
| transport | **direct WiFi/MQTT** (ruled — `DP-CIO-6`: this part has a radio) |
| config | **HTTP from `bus_storage`** (ruled — the Qualia's path, not config-over-bus) |

### Pin map

Read from `generate_pcb.py`'s `NETS` / `J_ESP_*_NETS` / `U1_NETS` tables and **confirmed against the
pad→net assignments in the emitted `.kicad_pcb`.**

| function | GPIO | header pin |
|---|---|---|
| **I²C SDA** | **GPIO4** | `J_ESP_L` pin 5 |
| **I²C SCL** | **GPIO5** | `J_ESP_R` pin 8 |
| backlight 1 / 2 / 3 | **GPIO0 / GPIO1 / GPIO3** | `J_ESP_L` pins 1 / 2 / 4 |
| free, non-strapping | GPIO6, GPIO7, GPIO10 | `J_ESP_R` pins 7 / 6 / 3 |
| **UART0 TX / RX** | **GPIO21 / GPIO20** | `J_ESP_R` pins **1 / 2** |
| ⚠ straps | GPIO2 / GPIO8 / GPIO9 | `J_ESP_L` 3, `J_ESP_R` 5, `J_ESP_R` 4 |

> **SCL was the one pin the brief asked to be found from the netlist: it is GPIO5**, on
> `J_ESP_R` pin 8, with a 4.7 kΩ pull-up to +3V3 (`R_SCL`), reaching MCP23017 pin 12.

### MCP23017 @ 0x20 (A0=A1=A2=GND)

| expander pins | net | destination |
|---|---|---|
| GPA0–3 | `M1_IN1..4` | L293D #1 (U2) IN1/IN2/IN3/IN4 → gauge 1 |
| GPA4–7 | `M2_IN1..4` | L293D #2 (U3) → gauge 2 |
| GPB0–3 | `M3_IN1..4` | L293D #3 (U4) → gauge 3 |
| GPB4–7 | `GPB4..7` | **`J_EXP_MCP` — NOT OURS.** `IODIRB = 0xF0` keeps them inputs |

**One I²C transaction drives all twelve coil lines.** `IOCON = 0x00` leaves address auto-increment
on, so `[0x12][GPIOA][GPIOB]` is 4 bytes on the wire. At 400 kHz that is ~100 µs; at the 800
half-steps/s slew rate the bus sits at ~8 %. This is why all three needles home *concurrently* and
the boot cost is 5.4 s rather than 16.2 s.

### Power

12 V screw terminal → 1N5401 (3 A) → +12V → LM7805 (TO-220) → +5V → everything.

---

## 2. The homing design

> ⛔ **The x27.168 has no absolute position feedback. None, at any price.** No encoder, no index
> pulse, no limit switch. The only position reference in the entire part is the internal mechanical
> stop at each end of its 315° sweep. **At power-on the firmware does not know, and cannot ask,
> where the needle is.**

Without homing, a needle sitting at 40 is driven as though it were at 0. Every I²C write returns
`ESP_OK`. Every step is counted correctly. The transport reports healthy. `_device/<id>/health` is
green. **And the gauge reads 40 units high, forever.** There is no instrument on this hardware that
catches it — other than a human looking at the dial. This is the canonical
`[CXC-VISION-EPISTEMICS]` shape: *machine signals do not merely go absent, they agree with the
wrong answer.*

### Geometry

| | |
|---|---|
| sweep | 315° |
| full step | 1/3° → **945 full steps** of travel |
| drive | **half-step, 8 states** → 1/6° quantum, **1890 half-steps** of travel |
| coil table | `{0x1, 0x5, 0x4, 0x6, 0x2, 0xA, 0x8, 0x9}`, bit0=IN1 … bit3=IN4 |

Half-step rather than full-step because it halves the audible step noise (three gauges next to a
person), doubles resolution so a 0–255 throttle maps onto 7.4 quanta per unit with no visible
stair, and costs nothing — the same one transaction per step.

`IN1 = IN2` shorts the coil through the low-side transistors: no current, plus a mild dynamic brake
that helps the needle hold against vibration. That is why `0x00` is safe to write at any moment,
including the instant before `IODIR` is flipped to output.

### The step count: **2160 half-steps = 360.0° commanded into a 315° range**

```
worst-case start    1890   needle parked hard against the FAR stop
pointer press-fit   +180   the pointer is HAND-fitted to the shaft at assembly
unit sweep tol.      +90   15° on the 315° spec
----------------------
total               2160   = exactly ONE FULL REVOLUTION
```

Sized this way **deliberately against this fleet's first named failure shape** — *"a limit sized
exactly for today"* (`kMaxPersist` was 4 for a control plane of exactly 4, and the whole symptom was
one WARN line). At N+1 — a 270° gauge, a 20° pointer offset, a replacement motor from another lot —
2160 still covers it, and the fix is one constant in one header.

**The overtravel is not wasted motion, it is the guarantee.** Once the needle reaches the stop the
remaining commands *slip*, harmlessly, and that slip is precisely how an unknown start becomes a
known zero. This is the `SwitecX25 zero()` method and it is accepted practice for this motor family.

**After the sweep, back off 18 half-steps (3.0°) and call *that* zero.** Two independent reasons:
a coil energised against a hard stop is where this motor buzzes and heats; and the offset leaves the
needle somewhere to *go* below the printed zero, so an under-range value is **visible** as a needle
below the mark instead of being clipped into invisibility against the stop.

### Rates — ⇢MEASURE, both of them

| | value | why |
|---|---|---|
| homing | **400 half-steps/s** (66.7°/s), **flat, no ramp** | a step lost while homing corrupts zero for the whole session and nothing can detect it |
| slew | **800 half-steps/s** (133°/s → 2.4 s full-scale) | a step lost while slewing costs one quantum and is corrected at the next re-home |
| homing duration | 2160 / 400 = **5.4 s**, all three concurrently | |

Homing runs flat with **no acceleration ramp** so that during bring-up the ramp is never a second
live suspect.

**Why conservative:** the coils see ~3.2 V, not 5 V. The L293D is a BJT bridge dropping ~1.8 V total
out of the +5 V rail it is fed from (`U2/U3/U4` pin 8 `VS` → net 4 = **+5V, not +12V**), and across
a ~280 Ω coil that is ~11.4 mA against a ~20 mA rating — roughly **57 % of rated torque**, which
lowers the achievable step rate. Datasheet speed figures do not apply to this drive.

### The gate

`RealizationBackend::ready()` is on the **seam**, not in gauge code, so every future backend with a
calibration phase inherits it. It returns false until all three needles have homed, and
`gauge_main.cpp` **drops and counts** every bus value that arrives before then.

**Dropping is visible** (the needle stays at zero). **Honouring is invisible** (the needle reads
plausibly and is wrong by the homing offset). That asymmetry is the entire argument.
`X27168::set_target()` also **refuses rather than queues** a pre-homing target — a queued target
would be applied against a zero that does not exist yet.

---

## 3. The bring-up ladder

**Each rung stops on failure. Do not proceed to rung N+1 with rung N unexplained** — the rule exists
because on 2026-08-12 three rounds of protocol diagnostics ran against a transceiver that had no
supply.

---

### 3.0 THE FULL ORDERED SEQUENCE — read this before touching anything

> **Prove POWER AT THE PART, then the SUBSTRATE, then the PROTOCOL.**
> — `[CXC-VISION-EPISTEMICS]` `rule-power-then-substrate-then-protocol`

**There are TWO instruments and neither can substitute for the other.** A **multimeter** answers
*"what is this board?"* with the power **off**. **Firmware** answers *"what can the MCU reach?"*
with the power **on**. **A short is a multimeter question; a marginal logic level is a firmware
question, and this board has a known one** (§4.1). Flashing a diagnostic into a board with a
5 V/GND short is how an ESP32-C3 is lost; conversely, no meter can see a register write that
fails one time in ten thousand.

| # | stage | instrument | artifact |
|---|---|---|---|
| **1** | **PREFLIGHT meter pass** — shorts, continuity, orientation, rails | multimeter, **power off** for §A–§D | [`rack_triple_gauge-PREFLIGHT.md`](rack_triple_gauge-PREFLIGHT.md) §A–§C |
| **2** | **THE MODS** — decide *before* powering anything | judgement | PREFLIGHT §D (MOD A, MOD B, MOD C) |
| **3** | **rails under power, no ICs; then seat, layer by layer** | multimeter, **power on** | PREFLIGHT §E, §F |
| **4** | **ROM banner on GPIO21** — the substrate, proven by code we did not write | scope / USB-serial | PREFLIGHT §F4, rung 2 below |
| **5** | **DIAGNOSTIC FIRMWARE** — 7 rungs, error rates, fault injectors | `pio run -e rack_triple_gauge_selftest -t upload` | `cyd/src/gauge_diag.cpp` |
| **6** | **read the output; answer the needs-eyes rows** | eyes | the diagnostic's own verdict block |
| **7** | **REAL FIRMWARE** | `pio run -e rack_triple_gauge -t upload` | rungs 3–8 below |
| **8** | **HOMING** — 5.4 s, and it is a *mechanical* event | eyes + ears | rung 6 below |
| **9** | **SWEEP TEST**, then the bus rows | `-e rack_triple_gauge_sweep` | rungs 7–8 below |

#### What each stage proves, and what it explicitly does NOT

| stage | PROVES | DOES **NOT** prove |
|---|---|---|
| **1** meter, power off | no rail is shorted to any other or to ground; SDA/SCL reach U1 13/12 through 4.7 kΩ pull-ups **to +3V3**; all twelve MCP→L293D lines are continuous with no neighbour bridges; all twelve L293D outputs reach `J_MOT`; the three 150 Ω base paths; each harness has two isolated ~280 Ω coils **on the expected pins** | anything intermittent, anything rate-dependent, anything that fails one time in ten thousand. A DC level of 3.3 V reads 3.3 V whether or not it clears a 3.5 V threshold |
| **2** the mods | that the V<sub>IH</sub> deficit and the 2 W regulator are **decisions**, not accidents | nothing measurable — it is a fork, taken or declined |
| **3** rails under power | every rail at nominal under real load; **+3V3 reads 0 V with no module seated** (it is generated *on* the module — 3.3 V there is a short or a backfeed, and it is the failure that looks like a pass) | that any *signal* net works |
| **4** ROM banner | TX, ground and baud on UART0 **with no code of ours involved** — the free negative control | anything about our firmware, the expander, or the motors |
| **5** diagnostic firmware | an ACK at 0x20 with the address straps correct; a **measured error rate** over 10,000 register round trips at 400 kHz and 2,000 at 100 kHz; every expander pad drives both levels with the **GPIO** register (the pad) agreeing and no pad dragging a neighbour; all twelve coil lines assertable | that any pad's copper reaches its L293D input (**an open reads identically to a good trace from the MCU side** — that is stage 1's job and it is not repeated); that any H-bridge *output* reaches a connector; that a needle moved; that a backlight lit |
| **6** eyes on the diagnostic | **which** needle twitched and **which** strip lit | position, calibration, direction |
| **7** real firmware | the scene fetched, the channel set bound, the transport is up | nothing about needle truth — see rung 6 |
| **8** homing | that 2160 step commands were issued and 2160 I²C writes returned `ESP_OK` | ⛔ **that any needle reached its stop.** A needle whose coil is open, whose connector is on the wrong JST, or whose motor is not fitted **produces this message identically** |
| **9** sweep + bus | that the needles track full scale and return; gauge identity, direction, mid-scale, range parity | nothing that is not on the list — every one of these is a **human** row |

#### Two independent chains — a stop in one does not gate the other

```
CHAIN A   GPIO4/5 -> 4k7 pull-ups -> MCP23017 -> 12 lines -> 3x L293D -> J_MOT
CHAIN B   GPIO0/1/3 -> 150R -> 2N2222 -> J_LED -> +12V strip
```

They share the +5 V rail and **nothing else** — different pins, different parts, a different rail
on the load side. The ladder's stop rule exists so a later rung is never read against an unproven
earlier one; **chain B is not downstream of chain A**, so a dead expander must not suppress a
backlight test that would have passed. The diagnostic firmware implements this split explicitly.

#### ⚠ Two files whose names sound alike and whose jobs are disjoint

| | runs on | proves |
|---|---|---|
| `gauge_selftest.cpp` — `[env:native_gauge]` | the **host** | the homing **state machine** and the binding **arithmetic**. Would pass identically if the board were never fabbed — **which is exactly what it did** |
| `gauge_diag.cpp` — `[env:rack_triple_gauge_selftest]` | the **C3** | what the MCU can **reach**. Knows nothing about needles |

⛔ **The diagnostic drives no needle to a position and does not home.** Homing slams a needle
against a mechanical stop for 5.4 s; against wrong wiring that is a mechanical event, not a
diagnostic. It *does* energise single coil lines for ~150 ms each, which twitches a needle by a
step or two — that twitch is the measurement.

⚠ **Unplug `J_EXP_MCP` before running the diagnostic** — its rung 4 briefly drives GPB4–7, which
the real firmware deliberately leaves as inputs (`IODIRB = 0xF0`).

---

### Rung 1 — power rails, **with no MCU seated**

> ⛔ **THE FULL PROCEDURE IS [`rack_triple_gauge-PREFLIGHT.md`](rack_triple_gauge-PREFLIGHT.md)**
> — §A (shorts, power never applied), §B (continuity of every net that matters), §C (orientation
> and polarity before any IC goes in a socket), §E (rails under power with nothing seated), §F
> (seat one layer at a time). Every step there states its PASS reading, its FAIL reading, and
> what to do about a FAIL. What follows here is the summary; **do not run the board off the
> summary.**

⚠ **Measure, do not assume, and do this before any silicon is exposed to it.**

1. **Module OUT of its socket.** Apply regulated 12 V to `J_PWR`.
2. On `J_PWR_TAP` (pin 1 = +12V, 2 = +5V, 3 = +3V3, 4 = GND) expect **12.0 / 5.0 / 0.0 V**.
   **0 V on pin 3 is CORRECT here** — the only source of +3V3 is the seated module. **3.3 V at this
   stage means a short.**
3. Confirm the 1N5401 orientation by reversing the supply briefly: **nothing should power up.**
4. ⇢MEASURE the LM7805 tab temperature after 10 min. Design figures: **1.96 W typical, 3.93 W peak**
   ((12−5) × 280 mA / 561 mA). A bare TO-220 in free air is ~65 °C/W → a 127 °C rise → a junction at
   ~152 °C against a 150 °C shutdown: **it would thermal-cycle at idle.** The heatsink is mandatory
   and must be ≤25 °C/W. ⚠ Feed it **regulated 12 V** — a 13.8 V "12 V" supply raises this to
   2.46 W typical / 4.9 W peak.

**STOP if:** any rail is out by more than 5 %, or the regulator is too hot to hold.

### Rung 2 — the MCU enumerates, proven by something we did not write

1. Seat the module. Re-check `J_PWR_TAP` pin 3 = **3.3 V**.
2. **Before flashing anything**, put a scope or a USB-serial adapter on **GPIO21 = `J_ESP_R`
   pin 1 = U0TXD** at 115200 and reset the board. **The ESP32 ROM bootloader prints on UART0 TX
   at 115200 on every reset regardless of the app's console config** — so this banner proves TX,
   ground and baud with *no code of ours involved*. It is free, and it is the negative control the
   whole rest of the ladder rests on. (Rung 2 exists because on 2026-08-11 exactly this trick
   excluded the wiring on a new UART link before any transport was a suspect.)

   > ⛔ **CORRECTED 2026-08-15 — this step said GPIO20, and GPIO20 is the RECEIVE pin.**
   > On the ESP32-C3, **U0TXD = GPIO21 and U0RXD = GPIO20**; the pin map above had them
   > reversed too. Probing RX for a transmit banner yields **silence**, and this rung's stated
   > failure action is *"that is a power, ground or module-seating fault"* — so **a correct board
   > would have been condemned by its own bring-up document.** The repo's own
   > `esp32c3-supermini-st7796.md` had it right (*"GPIO21 is UART0 TX"*); this file drifted from
   > it. Recorded rather than silently patched, because a diagnostic that confidently returns the
   > wrong answer is exactly the class `[CXC-VISION-EPISTEMICS]` is about — here the instrument
   > was the *document*.
   >
   > ⚠ **GPIO21 is not broken out on `J_EXP_ESP`** (that header carries GPIO20/RX on pin 6, not
   > 21), so `J_ESP_R` pin 1's solder side is the only access. ⚠ **UART crosses; CAN does not** —
   > adapter RX to board TX.
3. ⚠ **Identity before flashing.** `ioreg -r -c IOUSBHostDevice -l -w0`, match the **full** USB
   serial. `esptool chip-id` is **not** an identity check — it resets the board, which is the harm
   being prevented. The **live dual-throttle controller** is on `$FLASH_DENY_SERIALS`
   and the c3lcd/c3can board is `$C3LCD_EXPECT_SERIAL` (host overlay). Never either; the flash guard
   refuses the first and fails closed when the list is unset.
4. ⚠ **There is no factory image and there never will be.** A custom board has no as-shipped
   firmware to back up, so the fleet's usual `esptool read-flash` net does not exist. **The recovery
   path is the socket**: pull the module and flash it out of circuit, or on a spare. Second net:
   GPIO9 (BOOT) is on `J_ESP_R` pin 4, so hardware download-boot entry survives a firmware that
   breaks native USB. Understand both **before** the first flash.

```bash
cd cyd
pio run -e rack_triple_gauge -t upload && pio device monitor -e rack_triple_gauge
```

**STOP if:** no ROM banner on GPIO20 — that is a power, ground or module-seating fault, and no
amount of firmware will fix it.

### Rung 3 — I²C scan for 0x20

> ⛔ **FLASH THE DIAGNOSTIC FIRST, NOT THE REAL FIRMWARE.**
> ```bash
> pio run -e rack_triple_gauge_selftest -t upload && \
>   pio device monitor -e rack_triple_gauge_selftest
> ```
> Rungs 3, 4 and 5 below are what the **real** firmware shows you *in passing*, as a side effect
> of trying to work. `[env:rack_triple_gauge_selftest]` tests them **on purpose**, with numbers:
>
> | its rung | what it adds over the real firmware |
> |---|---|
> | 2 | reports **every** responder on 0x03–0x77, not just 0x20; decodes a wrong MCP address into *which* strap is not grounded; treats **>8 responders as a SHORT**, because SDA held low ACKs everywhere and a scan reports that as a rich bus |
> | 3 | ⛔ **the rung for §4.1.** 10,000 register round trips at 400 kHz and 2,000 at 100 kHz, reporting an **error rate and its resolution floor** — *"0 errors means the rate is below 100 ppm; it does not mean zero"* — plus a **fault injector** proving the counter can move |
> | 4 | per-pin drive and read-back on all 16 pads, reading **GPIO (the pad)** and never **OLAT (the latch, which is a mirror)**, comparing the **full 16-bit port** so a solder bridge shows as a neighbour moving |
> | 5 | the twelve coil lines, one at a time, named |
> | 6 | the three backlights, **named individually** — *"the backlights work"* cannot distinguish a swapped pair |

Expect on the console:

```
MCP23017 @0x20 ready — IODIRA=00 IODIRB=F0 (GPB4-7 left as INPUTS: they are J_EXP_MCP)
```

**The IODIR readback is the load-bearing part**, and it is the one check on this board that does not
read a value back through the same assumption that wrote it: a NAK, a wrong address, a half-powered
part and a floating SDA all make the readback **disagree**, whereas *"`i2c_master_transmit` returned
`ESP_OK`"* is equally true of anything on the bus that happens to ACK.

Failure text to expect instead: `*** NO DEVICE AT 0x20 ***`. Check U1's orientation, its VDD rail,
and that A0/A1/A2 are grounded.

⚠ **Also at this rung: the marginal-logic defect.** See §4 — the MCP23017 is powered from **+5 V**
while the pull-ups go to **+3V3**, which is 200 mV below its guaranteed V<sub>IH</sub>. It will
probably work. That is what makes it dangerous. If rung 3 is *intermittent*, this is why.

### Rung 4 — one stepper, one coil at a time

Before any sequencing, prove the wiring statically. With the board powered and the firmware halted
(or with a bench script), drive **one** L293D input pair at a time and confirm continuity/voltage
across the corresponding `J_MOT` pins:

| pattern (M1 nibble) | expect |
|---|---|
| `0x1` (IN1 hi, IN2 lo) | `MOT1_A` high, `MOT1_B` low, ~11 mA through coil A |
| `0x2` | the reverse |
| `0x4` / `0x8` | the same for coil B on `MOT1_C` / `MOT1_D` |
| `0x0` | both coils shorted low, **no current** |

**This is the rung that catches a swapped JST-XH-4.** A gauge with its coil pair transposed
(A↔C) still steps, still homes, still reports perfect health — and runs **backwards**. Nothing
downstream can see it.

**STOP if:** any coil reads open (a broken harness) or shorted.

### Rung 5 — one motor sequences

Enable a single gauge and step it slowly by hand-triggered commands. Confirm it turns **smoothly and
in one direction**. A motor that buzzes without turning has a coil pair transposed *within* a coil
(A+/A− swapped); a motor that jitters back and forth has the two *coils* swapped.

### Rung 6 — homing

```
HOMING all three needles: 2160 half-steps (360 deg commanded into a 315 deg range)
  at 400 half-steps/s = 5400 ms
...
HOMED in ~5400 ms. Zero is now 18 half-steps off the mechanical stop.
```

⚠ **That second line proves nothing about the needles.** It reports that 2160 step commands were
issued and 2160 I²C writes returned `ESP_OK`. A needle whose coil is open, whose connector is on the
wrong JST, or whose motor is not fitted **produces this message identically.**

**The actual test is your eyes and your ears:**

- **Eyes:** do all three needles sweep counter-clockwise and come to rest **just below the printed
  zero**, and stay there?
- **Ears:** is there a soft ticking that **stops** when the needle reaches the stop and then
  continues as a quieter slip? **A loud rattle for the whole 5.4 s means the needle never reached
  the stop.**

### Rung 7 — the sweep test, the one instrument that can fail

```bash
pio run -e rack_triple_gauge_sweep -t upload
```

Its own env, not a flag, so there is never any doubt about which binary is on the board while
somebody is watching needles. It drives every needle to full scale, back to zero, then re-homes.

**Why it exists:** `ready()` is true, every I²C write returns `ESP_OK`, every step counts perfectly —
and all of that is equally true of a needle that never moved and of one losing steps. **An
instrument that can only report success is not an instrument.**

- Does every needle reach the printed **full-scale** mark and return **exactly** to zero?
- A needle that falls short is losing steps: **800 half-steps/s is a design value, not a
  measurement.** Lower `kG3SlewHalfStepsPerSec` in
  `cyd/include/rack_triple_gauge_panel.h` until it does not. ⇢MEASURE and record the number found.
- Publish 128 to a 0–255 namespace: does the needle sit at **mid-scale**? If mid-scale is visibly
  off-centre, this gauge face is not a 315° face and `kG3SweepDegrees` needs the real number.

### Rung 8 — the bus

1. Confirm the scene fetched. If it did not:
   `*** NO SCENE FETCHED. The needles will home and then sit at zero forever. ***` —
   **a homed gauge reading zero and a gauge with no binding look identical on the dial; this log
   line is the only difference.**
2. **Prove the gate.** Publish `throttle/left` **during** the 5.4 s homing window. The console must
   show `dropped N bus value(s) so far — backend NOT READY`. **If values arrive during homing and
   this line does not appear, the gate is broken and every subsequent reading is suspect.**
3. **Identity:** publish 255 to `throttle/left` **only**. Does the **leftmost** gauge move — the one
   you intend to be gauge 1? Physical assignment is *document order* of `type: gauge` widgets and
   nothing checks it (see §4).
4. **Direction:** publish 0 then 255. Does the needle move **clockwise** for increasing values? If it
   sweeps the wrong way, the gauge face is mounted mirrored relative to the homing direction, and the
   fix is the homing-direction constant, **not** the coil wiring.
5. **Range parity:** gauge 3 binds `self/context_pct` (0–100) while gauges 1 and 2 bind 0–255. At
   50 % and 128 respectively, all three needles must sit at the **same angle**. That is the
   *"one semantic value, many physical realizations"* claim tested as arithmetic on real glass —
   or rather, on real brass.
6. **Backlights:** all three lit at power-on, full brightness, and each one belonging to the gauge
   next to it.

---

## 4. Findings the board should be changed for, before it is fabbed

Ordered by value. All are pre-fab and none blocks the firmware.

### 4.1 ⚠ MCP23017 is on +5 V with 3.3 V pull-ups — **fix this before fabbing**

- **As designed:** U1 pin 9 (VDD) → net 4 (**+5V**); `R_SDA`/`R_SCL` (4.7 kΩ) pull to net 5
  (**+3V3**).
- **The numbers:** the MCP23017 datasheet specifies SDA and SCL as I²C-level Schmitt inputs with
  **V<sub>IH</sub> = 0.7 × VDD**. At VDD = 5.0 V that is **3.5 V**. The bus can only reach **3.3 V**.
  **The high level is 200 mV below the guaranteed input-high threshold.**
- **Why it is dangerous rather than fatal:** the real Schmitt threshold on a typical part is
  ~2.5–3 V, so it will very often *work* — at 25 °C, on this lot, on this bench. It is a design with
  no margin, and when it fails the failure is a **corrupted register write with every I²C
  transaction returning `ESP_OK`**: a needle driving the wrong coil pattern while the whole stack
  reports health.
- **The fix is free:** move the expander to the 3.3 V rail.
  ```python
  U1_NETS[9]  = 5   # VDD   +5V -> +3V3
  U1_NETS[18] = 5   # RESET +5V -> +3V3   <-- MUST move too
  R_RST_NETS  = {1: 5, 2: 5}
  ```
  ⚠ **RESET must move too.** Leaving pin 18 on +5 V while VDD is 3.3 V puts V<sub>pin</sub> above
  VDD+0.3 and forward-biases the ESD diode. That is what makes it a **two-net** change, and it is
  the part an eager fix would miss.
- **It costs nothing downstream:** the expander then drives the L293D inputs at 3.3 V, and the L293D
  datasheet gives **V<sub>IH</sub> = 2.3 V max for Vcc1 = 4.5–7 V** — 1.0 V of margin.
- ⛔ **Do not "fix" it the other way.** Moving the pull-ups to +5 V would put 5 V on the ESP32-C3's
  GPIO4/GPIO5, which are **not 5 V tolerant**. That fix is strictly worse than the defect.
- **Worth checking on `button_matrix_4x4` too** — it carries two MCP23017s and the same design hand
  wrote both. Not claimed here; checked, not assumed.

### 4.2 `J_EXP_ESP` exposes two boot straps unlabelled

See §0. Silkscreen is free; pull-ups are a respin.

### 4.3 LM7805 → a drop-in switching regulator

**BOM change, no PCB change.** A Recom `R-78E5.0-1.0`-class module has the same In/GND/Out order and
0.1″ pitch; dissipation falls from ~2 W to ~0.15 W and **the heatsink disappears entirely**. Verify
the body outline and pin pitch against the TO-220 footprint before ordering — the pinout matches;
the mechanical envelope is what to check.

### 4.4 `generate_pcb.py` carries dead routing that contradicts the netlist

**Low urgency, high nastiness — and it is a *source-of-truth* defect, not a board defect.**

The board as emitted is **correct**: it was rerouted with Freerouting (`.dsn`/`.ses` present, 377
segments, DRC reports **0 unconnected pads** and 51 violations all of which are
footprint-library/padstack/silk warnings). The pad→net assignments are what got routed, so this
document's pin map is trustworthy.

But the hand-routing block in `emit_traces()` was never updated when the header pinout was fixed:

| it routes | pad table says |
|---|---|
| SDA from `J_ESP_L` pin 7 | pin 7 is **GND** |
| SCL from `J_ESP_L` pin 8 | pin 8 is **+5V**; SCL is `J_ESP_R` pin 8 |
| +3V3 from `J_ESP_R` pin 2 | pin 2 is **GPIO20** |
| LED nets from left pins 3/4/6 | 1/2/4 |
| GPIO10 from `J_ESP_L` **pin 9** | **a 1×8 header has no pin 9** |

**The hazard:** regenerating the board from this source *without* re-running Freerouting produces a
PCB whose hand-routed traces short SDA to GND and SCL to +5 V. Nothing announces it; the generator
runs clean.

**Fix:** delete the dead block, or assert every `(header, pin) → net` against the `*_NETS` tables at
generation time. The second is better — a convention nothing checks will collide again.

### 4.5 ⚠ No rail is readable by the MCU, and the only free ADC pin is a boot strap

**Found 2026-08-15 while building `[env:rack_triple_gauge_selftest]`'s rung 7.** The diagnostic
wants a rail measurement and **there is none to take.**

* `J_PWR_TAP` (+12V / +5V / +3V3 / GND) is a **header for a meter**. It connects to **no GPIO**.
* On the ESP32-C3 the ADC-capable pins are **ADC1 CH0–4 = GPIO0…4** and **ADC2 CH0 = GPIO5**.
  This board uses **GPIO0/1/3** for backlights, **GPIO4** for SDA and **GPIO5** for SCL.
* ⛔ **The only free ADC pin on the whole part is GPIO2 — which is a boot strap**, must be at its
  correct level at reset, and is brought out **unlabelled** on `J_EXP_ESP` pin 1 (§4.2). A
  divider hung on it is not a free addition: it is the §4.2 hazard, realised by us.

**Consequence, and it is the honest one:** rung 7 of the diagnostic reports **NO MEASUREMENT**
rather than inventing one. **The one rail signal that does exist** is the C3's brownout detector
— `esp_reset_reason() == ESP_RST_BROWNOUT`, which the diagnostic reports at rung 1 and treats as
a hard FAIL. It sees the **3V3 rail at the module only**, and only when it sags far enough to
reset. ⚠ **A +5V rail sagging to 4.0 V is entirely invisible to it — and would put the
MCP23017's V<sub>IH</sub> at 2.8 V and make §4.1's defect PASS for the wrong reason.** Two faults
cancelling is not a pass.

**Fix for a respin:** a 12 V→ADC divider on a **non-strapping** pin. GPIO6, GPIO7 and GPIO10 are
free and non-strapping but **are not ADC-capable on the C3**, so this needs either a part with
more ADC pins or an external ADC on the existing I²C bus (an ADS1115 at 0x48 costs one part and
no pins, and would read all three rails). Not urgent; **recorded so the next reader does not
spend the evening looking for the divider.**

### 4.6 ⚠ This document sent you to the wrong UART pin — corrected 2026-08-15

§1's pin map and rung 2 both said **UART0 TX = GPIO20**. On the ESP32-C3, **U0TXD = GPIO21 and
U0RXD = GPIO20.** Probing GPIO20 for the ROM bootloader banner yields **silence**, and rung 2's
own stated failure action is *"that is a power, ground or module-seating fault"* — **so a correct
board would have been condemned by its bring-up document.**

The repo's own `esp32c3-supermini-st7796.md` has it right (*"GPIO21 is UART0 TX"*); this file
drifted from it. Both places are now corrected and the probe point is **`J_ESP_R` pin 1**.

**Kept as a finding rather than silently patched**, because it is `[CXC-VISION-EPISTEMICS]` with
the *document* as the instrument: rung 2 exists to be the one check that involves no code of
ours, and it was pointed at a pin that cannot answer. A bring-up step that returns a confident
wrong answer is worse than one that is missing.

### 4.7 Smaller items

| item | severity | fix |
|---|---|---|
| 150 Ω base resistors → 17.3 mA base drive, ~12× overdrive | improvement | **470 Ω** (5.5 mA → ~300 mA collector at forced β 55). 1 kΩ is too thin |
| no fuse on the 12 V input; 1N5401 (3 A) is the only series element | improvement | polyfuse in series with `J_PWR` |
| MCP23017 RESET hard-tied; `R_RST` has **both ends on +5V** | improvement | respin: route RESET to GPIO10 (free, non-strapping) |
| L293D EN12/EN34 hard-tied high — no hardware coil-float | not a defect | firmware de-energises with `IN1=IN2=0`; respin could use GPB4/5/6 |

---

## 5. The channel-set decision this board forced

`[CXC-VISION-SEMANTIC-REALIZATION]` carries a **deadline**:

> *"A widget maps to a SET OF NAMED CHANNELS, not a single value. … Settle the channel-set shape
> BEFORE the first non-raster backend ships. Retrofitting a second channel means touching every
> backend, and there is currently exactly one."*

**This board is that first backend**, and it is the pillar's own named forcing case: three needles
*and* three independently-switchable backlights, where per-gauge illumination is an actuator with
**no raster counterpart** (on a screen, brightness is just pixel colour).

Settled here, in full at the head of `cyd/include/realization.h` as `[DD-G3-CHANNELS]`:

1. **Channel names are semantic and widget-kind-scoped.** `illum`, not `backlight`. `value`, not
   `needle_position`. A channel name that encodes a realization has already leaked. Vocabulary for
   kind `gauge`: `value` (required), `illum` (optional).
2. **The domain belongs to the binding**, not the backend. The scene says `min:`/`max:`; the seam
   carries a **unit float in [0,1]**; the backend maps unit onto 1890 half-steps, or an 8-bit duty,
   or two 7-segment digits. **That mapping is the mechanism** behind "one value, many realizations" —
   it is why 0–255 throttle and 0–100 percent drive the same rack, and the host suite proves both
   land identically.
3. **`realizes()` is three-valued**: `Realized` / `NotApplicable` (a raster gauge has no `illum` —
   silent, it is a design) / `Unknown` (**loud, once, at bind time** — a scene/firmware mismatch).
   Two values would collapse *"I chose not to"* with *"I do not understand you"*, and only the second
   is a defect.
4. **`ready()` gates the backend and lives on the seam**, so every future backend with a calibration
   phase inherits the homing gate instead of reinventing it.

**Migration:** a bare `namespace:` remains sugar for `channels.value`, so **every existing scene keeps
working and no raster backend changes**. An explicit `channels.value` **wins** — a stated precedence,
because two sources for one fact without one is a defect nobody can attribute.

```yaml
- type: gauge
  id: left
  min_val: 0
  max_val: 255
  namespace: throttle/left          # legacy — still valid, == channels.value
  channels:                         # new — the channel set
    value: {namespace: throttle/left,  min: 0, max: 255}
    illum: {namespace: rack/backlight, min: 0, max: 255}
```

### What a later HAL declaration must be able to express

Not fixed here — `[PCB-GAUGE3]` stress **S4** (no `stepper` kind, no `motor/position`) and **S3**
(no compound peripheral) are HAL-vocabulary work, and fixing vocabulary while bringing up a board is
two changes whose blast radii would compound. But the firmware's requirement is now concrete rather
than guessed:

```yaml
kind: stepper
  driver:        l293d                              # an H-bridge PAIR per motor
  on:            i2c{addr: 0x20, driver: mcp23017}  # S2's expander as a PORT PROVIDER
  pins:          [in1, in2, in3, in4]               # EXPANDER pin indices
  sequence:      half
  travel_steps:  1890
  slew_rate_hz:  800
  home:
    method:      hard-stop        # there IS no other on an x27.168
    direction:   down
    steps:       2160
    rate_hz:     400
    rest_offset: 18
  namespace:     gauge/{n}/value  # the absent `motor/position`
```

…and that a `gauge` is **one** object with **two** channels, not two atomic entries sharing an index.

---

## 6. What is proven and what is not

### Machine-proven (2026-08-14, on a host — there is no board)

| | |
|---|---|
| `pio run -e rack_triple_gauge` | **SUCCESS**, clean build: **998 compile steps**, 21 app TUs, 257 in wifi/mbedtls/freertos/lwip. RAM 13.5 %, flash 25.8 % |
| `pio run -e native_gauge && .pio/build/native_gauge/program` | **RESULT: PASS**, exit 0, 8 groups |
| compile definitions | `CXC_RACK_TRIPLE_GAUGE_APP`, `CXC_DEVICE_PREFIX="gauge3"`, `CXC_BUS_TRANSPORT_DEFAULT="mqtt"` present on all four board TUs — read out of `compile_commands.json` (911 entries), because `platformio.ini` `build_flags` are silently dropped for `framework=espidf` |
| negative control | the suite was broken **five** ways and failed each time with its intended message; all five reverted, back to PASS |
| `pio run -e rack_triple_gauge_selftest` (2026-08-15) | **SUCCESS** from a deleted build dir: **979 compile steps**, 22.8 s, 892 TUs in `compile_commands.json`. RAM 3.1 %, flash 5.1 % — *one twentieth* of the real firmware, because the SRCS list is two files |
| its compile definitions | **`-DCXC_G3_DIAG_APP` present on `gauge_diag.cpp`**, read out of `compile_commands.json`; **`CXC_RACK_TRIPLE_GAUGE_APP` absent from all 892 entries**, which is what keeps `gauge_main.cpp`'s `app_main()` out of this link |
| regression | `rack_triple_gauge` and `native_gauge` both still **SUCCESS** after the `src/CMakeLists.txt` edit |

### Needs eyes — and on this board that list is not a formality

**There is no `/capture.bmp` and there never will be.** On every other board in this fleet a capture
is a second (if self-confirming) channel. Here there is **no machine channel of any kind that
observes the output.** Human acceptance is not the last resort on this board; it is the **only**
resort.

When the board exists, David must confirm, specifically — and **rows 1–2 and 3a–3b belong to the
[PREFLIGHT](rack_triple_gauge-PREFLIGHT.md) and the diagnostic firmware, not to the real
firmware**:

1. **Rails**, module out: **11.0–11.6 / 4.80–5.20 / 0.00 V** on `J_PWR_TAP`. ⛔ **0 V on +3V3 is
   the PASS**; +3V3 is generated *on the module*, so a reading near 3.3 V there is a short or a
   backfeed — **the failure that looks like a pass.** ⚠ The ~11.3 V on +12V is D1's forward
   drop and is **correct**; expecting 12.0 V here is a false alarm. (PREFLIGHT §E2–§E4.)
2. **LM7805 tab temperature** after 10 min idle — the design figure is 1.96 W and a bare TO-220
   would be at thermal shutdown.
   **2a. ROM banner on GPIO21** (`J_ESP_R` pin 1), **not GPIO20** — §4.6.
   **2b. `esp_reset_reason()` is not `BROWNOUT`** — diagnostic rung 1, the only rail instrument
   this board has.
3. **From `[env:rack_triple_gauge_selftest]`, before the real firmware:**
   **3a.** which needle twitched for each of the twelve named coil lines (rung 5) — a swapped
   `J_MOT` still homes, still reports health, and runs **backwards forever**;
   **3b.** which strip lit for each of the three **named** backlight channels (rung 6) — *"the
   backlights work"* cannot distinguish a swapped pair.
4. **Homing**: all three needles sweep counter-clockwise and rest **just below printed zero**.
5. **The sound of homing**: ticking that stops at the stop, not a 5.4 s rattle.
6. **Sweep test**: every needle reaches printed full scale and returns **exactly** to zero.
7. **Which needle**: publish `throttle/left` alone — does gauge **1** move?
8. **Direction**: increasing values move the needle **clockwise**.
9. **Mid-scale**: 128 of 255 sits at the middle of the printed dial (proves the 315° figure).
10. **Range parity**: 50 % on gauge 3 and 128 on gauge 1 sit at the **same angle**.
11. **Backlights**: three lit, full brightness, each belonging to its own gauge.
12. **The gate**: publishing during the homing window produces
    `dropped N bus value(s) so far — backend NOT READY`.

### Not built, and deliberately

- **HAL `stepper` kind / `motor/position` namespace** — vocabulary work, §5.
- **Backlight PWM** — v1 is ON/OFF held on. A **scoping** decision, not the contract: LEDC is
  configured, `illum` answers `Realized`, and enabling dimming is adding a namespace to the scene.
  A change of value, not of shape.
- **A `slot:` key on the gauge widget** — physical assignment is document order today. Write it
  before there is a second gauge board.
- **An acceleration ramp** — 800 half-steps/s flat until rung 7 measures the real pull-in rate. An
  unverifiable speed-up on a motor with no feedback is the wrong first move.

---

## 7. Files

| | |
|---|---|
| `cyd/include/rack_triple_gauge_panel.h` | **the one retune point** — every board constant |
| `cyd/include/realization.h` | the non-raster seam + `[DD-G3-CHANNELS]` |
| `cyd/src/x27168.{h,cpp}` | sequencer + homing state machine (pure C++) |
| `cyd/src/mcp23017.{h,cpp}` | expander driver + bus recovery (ESP-IDF) |
| `cyd/src/gauge_bind.{h,cpp}` | channel-set binder (pure C++) |
| `cyd/src/boards/rack_triple_gauge.cpp` | the realization backend |
| `cyd/src/gauge_main.cpp` | the app shell |
| `cyd/src/gauge_selftest.cpp` | the **host** suite — the state machine and the binding arithmetic |
| **`cyd/src/gauge_diag.cpp`** | **the ON-TARGET diagnostic** — 7 rungs, error rates, 3 fault injectors. Shares **no code** with `mcp23017.cpp`, deliberately |
| **`docs/board-bringup/rack_triple_gauge-PREFLIGHT.md`** | **the multimeter pass** — shorts, continuity, orientation, the two mods, rails. Power off for §A–§D |
| `cyd/platformio.ini` | `env:rack_triple_gauge`, `env:rack_triple_gauge_sweep`, **`env:rack_triple_gauge_selftest`**, `env:native_gauge` |
| `cyd/src/CMakeLists.txt` | the `CXC_RACK_TRIPLE_GAUGE` **and `CXC_G3_DIAG`** branches — **where every `-D` actually lives** |

**The two instruments, one line each:** the **PREFLIGHT** answers *"what is this board?"* with the
power **off**; **`gauge_diag.cpp`** answers *"what can the MCU reach?"* with the power **on**.
**A short is a multimeter question; a marginal logic level is a firmware question.** Neither
substitutes for the other, and §4.1 is the reason the second one exists.
