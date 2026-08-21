# `rack_triple_gauge` — PREFLIGHT: the multimeter pass

> **Do this with a multimeter, before an ESP32-C3 is ever inserted, before any IC goes into a
> socket, and — for §A and §B — before power is ever applied.**
>
> Companion documents: [`rack_triple_gauge.md`](rack_triple_gauge.md) (the full bring-up),
> [`rack_triple_gauge.descriptor.yaml`](rack_triple_gauge.descriptor.yaml) (the machine view).
> `[SPEC:CXC-BRINGUP]` `[SPEC:CXC-BOARD-HW]` `[PCB-GAUGE3]` `[CXC-VISION-EPISTEMICS]`

---

## 0. Why there is a meter pass AND a diagnostic firmware, and why neither replaces the other

David asked for a beep-out procedure and then proposed a diagnostic test flash instead. **The
answer is both**, and the reason is the fleet's own hard-won ladder:

> **Prove POWER AT THE PART, then the SUBSTRATE, then the PROTOCOL.**
> — `[CXC-VISION-EPISTEMICS]` `rule-power-then-substrate-then-protocol`

That rule was paid for on **2026-08-12, when three rounds of protocol diagnostics ran against a
CAN transceiver that had no supply.** Every round produced clean, confident, entirely
uninformative output. The transceiver could not have answered; nothing in the diagnostics could
say so.

The two instruments answer different questions and **neither can answer the other's:**

| | the meter | the diagnostic firmware |
|---|---|---|
| question | **"what *is* this board?"** | **"what can the MCU *reach*?"** |
| power | **OFF** (§A–§D) | **ON** |
| finds | shorts, opens, swapped pins, wrong parts, wrong orientation, missing rails, resistor values | marginal logic levels, intermittent register writes, dead expander pads, solder bridges *between* pads, unreachable coil lines |
| cannot see | anything intermittent, anything rate-dependent, anything that only fails one time in ten thousand | a short (it would be destroyed by one), an open trace (an open reads *identically* to a good one from the MCU side), an unpopulated pull-up |

**A short is a multimeter question. A marginal logic level is a firmware question — and this
board has a known one** (§D, MOD A: the MCP23017 sits on +5 V while its I²C pull-ups go to
+3V3, putting the bus 200 mV *under* the part's guaranteed V<sub>IH</sub>). No meter can see
that. It is why `[env:rack_triple_gauge_selftest]` rung 3 exists, and it is why that firmware is
not enough on its own: **flashing it into a board with a 5 V/GND short is how you lose an
ESP32-C3.**

**Order: §A → §B → §C → §D (decide) → §E → §F → then flash the diagnostic → then the real
firmware.**

---

## 1. What you need, and which meter functions

Say the function out loud each time; the failure mode of this whole document is reading a
resistance with the meter in DC volts and believing it.

| function | how it is usually marked | used for |
|---|---|---|
| **continuity / beep** | ●))) or a speaker icon | §B — "are these two points the same net?" Beeps below roughly 30–50 Ω |
| **resistance, 200 Ω** | Ω, 200 range | coil resistance, the 150 Ω base resistors |
| **resistance, 20 kΩ** | Ω, 20k range | the 4.7 kΩ I²C pull-ups, the 10 kΩ |
| **resistance, 2 MΩ** | Ω, 2M range | §A — rail-to-rail isolation. **Continuity/beep is the wrong function for §A**; see the warning below |
| **diode mode** | ▶⊦ | §C — D1 orientation, transistor pinout identification |
| **DC volts, 20 V** | V⎓ or DCV, 20 range | §E, §F — rails. Autoranging is fine |

Plus: a **regulated 12 V bench supply** with a current limit if you have one (set 500 mA), fine
probes or grabbers, and the board **with no ICs and no module seated.**

> ⛔ **THE TRAP THAT MAKES A GOOD BOARD LOOK SHORTED.** This board carries 2× 100 µF and 5×
> 100 nF across its rails. **In continuity/beep mode, an uncharged 100 µF capacitor beeps** —
> the meter's test current charges it, the voltage starts near zero, and the beeper fires for a
> fraction of a second before going quiet. That is a *correct* reading of a *good* board.
> **For every rail-to-rail check in §A, use resistance (2 MΩ), watch the number climb, and read
> the SETTLED value.** If it settles above ~100 kΩ it is fine, whatever it did in the first
> half second. Reverse the probes and repeat: the caps discharge through the meter, so a second
> reading in the same direction can be misleading.

---

## §A — SHORTS. Power has never been applied. Nothing is seated.

**Preconditions: no ESP32-C3 module in `J_ESP_L`/`J_ESP_R`. No MCP23017 in U1. No L293Ds in
U2/U3/U4. No transistors, or transistors fitted but nothing plugged into any JST. No supply
connected to `J_PWR`.**

All measurements at **`J_PWR_TAP`**, the 1×4 test header, which is on the board for exactly
this:

| `J_PWR_TAP` pin | net |
|---|---|
| 1 | **+12V** (post-diode) |
| 2 | **+5V** |
| 3 | **+3V3** |
| 4 | **GND** |

Meter in **resistance, 2 MΩ**. Read the **settled** value.

### A1 — +12V to GND

* Probes: `J_PWR_TAP` 1 ↔ 4.
* **PASS:** climbs and settles **> 100 kΩ** (typically OL / >2 MΩ with nothing seated).
* **FAIL:** settles **< 1 kΩ**, or beeps persistently in continuity mode.
* **ON FAIL:** a short on the +12V rail. Suspect, in order: **C1 (100 µF) fitted backwards** —
  an electrolytic in reverse is the single most common cause and it will read as a few tens of
  ohms; a solder bridge under the `J_PWR` screw terminal; a bridge at `J_LED1/2/3` pin 1, which
  is on +12V. **Do not apply power. Find it first.**

### A2 — +5V to GND

* Probes: `J_PWR_TAP` 2 ↔ 4.
* **PASS:** settles **> 100 kΩ**.
* **FAIL:** **< 1 kΩ**.
* **ON FAIL:** suspect **C3 (100 µF) backwards**, a bridge on the wide +5V pour, or a bridge at
  one of C5/C6/C7 (the L293D decouplers). ⚠ The +5V net is the largest net on this board — it
  reaches U1 pin 9, U1 pin 18, all three L293Ds' pins 1/8/9/16, `J_ESP_L` pin 8 and R_RST.
  **A +5V/GND short here would be applied directly to the ESP32-C3's 5V input pin.**

### A3 — +3V3 to GND

* Probes: `J_PWR_TAP` 3 ↔ 4.
* **PASS:** settles **> 100 kΩ**. With nothing seated, the only things on +3V3 are R_SDA and
  R_SCL (4.7 kΩ each, but to SDA/SCL, not to ground) — so expect a high reading.
* **FAIL:** **< 1 kΩ**.
* **ON FAIL:** ⛔ **this is a short on a rail the board does not even generate.** +3V3 comes
  *from the seated module*. A short here means the module's regulator would be shorted the
  instant it is plugged in. Suspect a bridge at `J_ESP_L` pin 6, or at R_SDA/R_SCL pin 2.

### A4 — rail to rail

Three measurements, all in **resistance, 2 MΩ**:

| probes | PASS | FAIL |
|---|---|---|
| `J_PWR_TAP` 1 ↔ 2  (**+12V ↔ +5V**) | settles **> 100 kΩ** | **< 1 kΩ** |
| `J_PWR_TAP` 1 ↔ 3  (**+12V ↔ +3V3**) | settles **> 100 kΩ** | **< 1 kΩ** |
| `J_PWR_TAP` 2 ↔ 3  (**+5V ↔ +3V3**) | settles **> 100 kΩ** | **< 1 kΩ** |

* **ON FAIL (+12V ↔ +5V):** the LM7805's input and output are bridged. With a real 12 V supply
  this puts **12 V onto the +5V rail** and destroys the MCP23017, all three L293Ds and the
  ESP32-C3's 5 V pin simultaneously. Check the TO-220 pads: pin 1 (IN) and pin 3 (OUT) are two
  pads apart and a solder blob spans them easily.
* **ON FAIL (anything ↔ +3V3):** ⛔ **the worst one, because it will look like a PASS later.**
  §E4 checks that +3V3 reads **0 V** with no module seated. A +5V↔+3V3 bridge makes it read
  ~5 V, and a reader expecting "a 3.3 V rail" sees a number near 3.3–5 and moves on. **The
  ESP32-C3's 3V3 pin is an output, not an input** — back-feeding it is how modules die quietly.

### A5 — the raw input, before the diode

* Probes: `J_PWR` screw terminal pin 1 (**+12V_IN**) ↔ pin 2 (**GND**).
* **PASS:** in resistance mode, **> 100 kΩ** in *one* probe orientation and possibly lower in
  the other (you are reading D1's reverse/forward behaviour, which is correct).
* **FAIL:** near 0 Ω in **both** orientations.
* **ON FAIL:** D1 is shorted (a dead 1N5401 fails short), or the terminal pads are bridged.

**⛔ STOP GATE. Do not proceed past §A with any unexplained reading. Every subsequent step
assumes the rails are isolated.**

Record:

```
A1 +12V–GND ______   A2 +5V–GND ______   A3 +3V3–GND ______
A4 12–5 ______  12–3V3 ______  5–3V3 ______   A5 ______
```

---

## §B — CONTINUITY. Every net that matters. Power still off.

Meter in **continuity / beep**. Here beep = good, and the capacitor trap does not apply (these
are signal nets).

> **What this section is really for.** From the MCU's side, **an open trace and a perfect trace
> are indistinguishable** — the expander pad drives fine either way, the I²C write returns
> `ESP_OK`, the diagnostic firmware's rung 4 reports PASS. **§B is the only thing in the whole
> bring-up that can see an open between U1 and a L293D input.** It is not optional and it is
> not covered later.

### B1 — I²C: ESP header pad → pull-up → MCP23017

Six checks. `J_ESP_L` and `J_ESP_R` are the female headers the module plugs into; probe the
solder side.

| # | from | to | expect |
|---|---|---|---|
| B1.1 | `J_ESP_L` **pin 5** (GPIO4/SDA) | **U1 pin 13** (SDA) | **beep** |
| B1.2 | `J_ESP_R` **pin 8** (GPIO5/SCL) | **U1 pin 12** (SCL) | **beep** |
| B1.3 | `J_ESP_L` **pin 5** | `R_SDA` **pin 1** | **beep** |
| B1.4 | `J_ESP_R` **pin 8** | `R_SCL` **pin 1** | **beep** |
| B1.5 | `R_SDA` **pin 2** | `J_PWR_TAP` **pin 3** (+3V3) | **beep** |
| B1.6 | `R_SCL` **pin 2** | `J_PWR_TAP` **pin 3** (+3V3) | **beep** |

* **ON FAIL (B1.1 / B1.2):** an open on SDA or SCL. This is the failure the diagnostic firmware
  explicitly cannot distinguish from a missing chip (`rung 2`, "4 IS THE ONE THIS FIRMWARE
  CANNOT SEE"). Reflow U1's pad and the header pad; check for a via that did not plate.
* **ON FAIL (B1.5 / B1.6):** the pull-up does not reach +3V3. Note that the firmware *can*
  indicate this (rung 2's pull-up presence test) but cannot tell 4.7 kΩ from 47 kΩ, and cannot
  tell +3V3 from +5V. **B1.5/B1.6 plus B2 are what settle it.**

### B2 — pull-up VALUE, and the rail they pull to

Meter in **resistance, 20 kΩ**.

| # | from | to | PASS | FAIL |
|---|---|---|---|---|
| B2.1 | `J_ESP_L` pin 5 (SDA) | `J_PWR_TAP` pin 3 (+3V3) | **4.5–4.9 kΩ** | anything else |
| B2.2 | `J_ESP_R` pin 8 (SCL) | `J_PWR_TAP` pin 3 (+3V3) | **4.5–4.9 kΩ** | anything else |
| B2.3 | `J_ESP_L` pin 5 (SDA) | `J_PWR_TAP` pin 2 (+5V) | **> 100 kΩ / OL** | any low reading |
| B2.4 | `J_ESP_R` pin 8 (SCL) | `J_PWR_TAP` pin 2 (+5V) | **> 100 kΩ / OL** | any low reading |

* **ON FAIL (B2.1/B2.2 reading ~10 kΩ):** R_SDA/R_SCL and R_RST have been swapped in assembly —
  10 kΩ is R_RST's value. At 10 kΩ the bus will be marginal at 400 kHz *in addition to* the
  V<sub>IH</sub> problem, and rung 3 of the diagnostic cannot separate the two causes.
* **ON FAIL (B2.3/B2.4 reading low):** ⛔ **the pull-ups are on +5 V. STOP.** That puts 5 V on
  GPIO4/GPIO5, **which are not 5 V tolerant on the ESP32-C3.** See §D MOD A — moving the
  pull-ups to 5 V is the *wrong* fix for the V<sub>IH</sub> problem and is strictly worse than
  the defect. Do not seat a module.

### B3 — the twelve coil lines: MCP23017 → L293D inputs

**This is the largest single block of checks and the one the firmware most needs backed up.**

| # | MCP23017 pin | net | L293D pin | expect |
|---|---|---|---|---|
| B3.1 | **U1 pin 21** (GPA0) | `M1_IN1` | **U2 pin 2** (IN1) | beep |
| B3.2 | **U1 pin 22** (GPA1) | `M1_IN2` | **U2 pin 7** (IN2) | beep |
| B3.3 | **U1 pin 23** (GPA2) | `M1_IN3` | **U2 pin 10** (IN3) | beep |
| B3.4 | **U1 pin 24** (GPA3) | `M1_IN4` | **U2 pin 15** (IN4) | beep |
| B3.5 | **U1 pin 25** (GPA4) | `M2_IN1` | **U3 pin 2** | beep |
| B3.6 | **U1 pin 26** (GPA5) | `M2_IN2` | **U3 pin 7** | beep |
| B3.7 | **U1 pin 27** (GPA6) | `M2_IN3` | **U3 pin 10** | beep |
| B3.8 | **U1 pin 28** (GPA7) | `M2_IN4` | **U3 pin 15** | beep |
| B3.9 | **U1 pin 1** (GPB0) | `M3_IN1` | **U4 pin 2** | beep |
| B3.10 | **U1 pin 2** (GPB1) | `M3_IN2` | **U4 pin 7** | beep |
| B3.11 | **U1 pin 3** (GPB2) | `M3_IN3` | **U4 pin 10** | beep |
| B3.12 | **U1 pin 4** (GPB3) | `M3_IN4` | **U4 pin 15** | beep |

* **ON FAIL (no beep):** an open. The diagnostic firmware will still report this pad as PASS —
  it drives the pad and reads the pad, and the pad is fine. Fix it here or it will present later
  as a motor that buzzes without turning, which is a *different* diagnosis you will chase.

**Then the bridge check, which is the other half:** with the meter still in continuity, probe
**each adjacent pair** of U1's GPA pins (21↔22, 22↔23, 23↔24, 25↔26, 26↔27, 27↔28) and GPB pins
(1↔2, 2↔3, 3↔4).

* **PASS:** **no beep** on every pair.
* **FAIL:** a beep.
* **ON FAIL:** a solder bridge. The diagnostic firmware *does* catch this (rung 4 compares the
  full 16-bit port, so a neighbour moving is visible), but catching it now costs nothing.

### B4 — the three backlight base paths

Meter in **resistance, 200 Ω** for the first column, **continuity** for the second.

| # | from | to | PASS | via |
|---|---|---|---|---|
| B4.1 | `J_ESP_L` **pin 1** (GPIO0) | `Q_LED1` **base** | **145–155 Ω** | R_LED1 |
| B4.2 | `J_ESP_L` **pin 2** (GPIO1) | `Q_LED2` **base** | **145–155 Ω** | R_LED2 |
| B4.3 | `J_ESP_L` **pin 4** (GPIO3) | `Q_LED3` **base** | **145–155 Ω** | R_LED3 |
| B4.4 | `Q_LED1` **emitter** | `J_PWR_TAP` pin 4 (GND) | beep | |
| B4.5 | `Q_LED2` **emitter** | GND | beep | |
| B4.6 | `Q_LED3` **emitter** | GND | beep | |

> ⚠ **`J_ESP_L` pin 1 is GPIO0, pin 2 is GPIO1, pin 4 is GPIO3.** `generate_pcb.py` carries a
> stale comment at line 949 claiming *"LED1: GPIO0 = left pin3"*, which contradicts
> `J_ESP_L_NETS`. The **netlist** is authoritative and the emitted `.kicad_pcb` agrees with it.
> This is finding §4.4 in `rack_triple_gauge.md`. **B4.1–B4.3 are the measurement that settles
> it on the physical board** — if pin 1 does not read 150 Ω to Q_LED1's base, find which pin
> does and write the answer on the board.

* **ON FAIL (open):** R_LED not soldered, or the transistor is in the wrong orientation so
  "base" is not where you think — do §C3 first, then come back.
* **ON FAIL (~0 Ω):** R_LED bridged. The GPIO would then drive the base directly with no current
  limit. **That destroys the GPIO, the transistor, or both.** Fix before power.

### B5 — every JST-XH connector to its driver

**Motor connectors**, meter in **continuity**:

| connector pin | net | L293D output pin |
|---|---|---|
| `J_MOT1` 1 / 2 / 3 / 4 | MOT1_A / B / C / D | **U2 pins 3 / 6 / 11 / 14** |
| `J_MOT2` 1 / 2 / 3 / 4 | MOT2_A / B / C / D | **U3 pins 3 / 6 / 11 / 14** |
| `J_MOT3` 1 / 2 / 3 / 4 | MOT3_A / B / C / D | **U4 pins 3 / 6 / 11 / 14** |

* **PASS:** twelve beeps, and **no beep between any two pins of the same connector**.
* **ON FAIL (beep between J_MOT pins):** two H-bridge outputs are bridged. When both drive, that
  is a direct output-to-output short through the L293D. **It will pass every firmware check** —
  the L293D has no fault reporting and nothing on this board reads its outputs. Find it here.

**Backlight connectors:**

| connector pin | net | goes to |
|---|---|---|
| `J_LED1` 1 | +12V | `J_PWR_TAP` pin 1 |
| `J_LED1` 2 | LED1_COL | `Q_LED1` **collector** |
| `J_LED2` 1 / 2 | +12V / LED2_COL | tap pin 1 / `Q_LED2` collector |
| `J_LED3` 1 / 2 | +12V / LED3_COL | tap pin 1 / `Q_LED3` collector |

* ⚠ **`J_LED*` pin 1 is +12 V and pin 2 is a transistor collector.** They are not
  interchangeable and the connector is not keyed against a reversed *crimp*. A reversed strip
  puts +12 V onto the collector through the strip — which is, coincidentally, *almost* the
  intended circuit and may even light dimly. **Confirm the crimp order on the harness, not just
  the connector.**

### B6 — the motor harnesses themselves, before they are plugged in

Meter in **resistance, 200 Ω**, probing the **gauge-end connector** of each x27.168 harness with
nothing connected to the board.

| probes | PASS | FAIL |
|---|---|---|
| pin 1 ↔ pin 2 (coil A) | **~250–300 Ω** | OL, or near 0 |
| pin 3 ↔ pin 4 (coil B) | **~250–300 Ω** | OL, or near 0 |
| pin 1 ↔ pin 3 | **OL** (the coils are isolated) | any low reading |
| pin 2 ↔ pin 4 | **OL** | any low reading |

* **ON FAIL (1↔3 reads ~280 Ω instead of 1↔2):** ⛔ **the harness is transposed.** This is the
  single most valuable check in §B, because a gauge with its coil pair swapped **still steps,
  still homes, still reports perfect health — and runs backwards forever.** Nothing downstream
  of here can see it: `[CXC-VISION-EPISTEMICS]`, every machine signal agreeing with the wrong
  answer. Re-pin the connector now.
* **ON FAIL (OL on a coil):** an open coil or a bad crimp. The motor will buzz and not turn.
* **ON FAIL (near 0 Ω):** a shorted coil. Do not connect it — the L293D will source into a dead
  short.

---

## §C — ORIENTATION AND POLARITY. Before any IC goes into a socket.

### C1 — D1, the 1N5401 reverse-protection diode

Meter in **diode mode**.

* Red probe on **D1 pin 1 (anode, the `+12V_IN` side)**, black on **pin 2 (cathode, the `+12V`
  side)**.
* **PASS:** **0.4–0.8 V**. Reverse the probes: **OL**.
* **FAIL:** OL both ways (open diode), or ~0 V both ways (shorted diode), or the forward
  direction is the *other* way round.
* **ON FAIL (backwards):** ⛔ **the board will not power up at all** and you will spend the
  evening on the regulator. **The cathode band goes toward `+12V` / the LM7805**, i.e. away from
  the screw terminal. D1 is placed at 180° in the generator, so *do not* infer the direction
  from the silkscreen outline alone — read the band.

### C2 — the LM7805

* **Pin 1 = IN (+12V), pin 2 = GND, pin 3 = OUT (+5V)**, tab tied to pin 2 (GND) on a standard
  TO-220 7805.
* Meter in **continuity**: pin 1 ↔ `J_PWR_TAP` pin 1 (**beep**), pin 2 ↔ GND (**beep**), pin 3 ↔
  `J_PWR_TAP` pin 2 (**beep**).
* **ON FAIL:** the part is reversed in its footprint. A 7805 fitted backwards puts the 12 V input
  onto the 5 V rail through the regulator's substrate diode. **Everything on the +5V rail dies.**
* ⚠ **The tab is at GND on a 7805.** If you fit a heatsink (§D MOD B) that touches anything else,
  it is a GND connection. That is usually fine and occasionally not — check clearance to the
  `J_PWR` terminal and to C1.

### C3 — the three 2N2222s. ⛔ **Verify the pinout; do not assume it.**

`generate_pcb.py` assigns **pad 1 = Emitter, pad 2 = Base, pad 3 = Collector (EBC)**. **Not
every TO-92 part sold as "2N2222" is EBC** — some are **ECB**, and the original metal-can
2N2222 (TO-18) is different again. Fitting an ECB part into an EBC footprint puts the GPIO's
150 Ω onto the *collector* and the 12 V strip onto the *base*.

Identify the actual part in hand, meter in **diode mode**, before soldering:

1. **Find the base.** Put the **red** probe on one leg and the black on each of the other two in
   turn. The base is the single leg that reads **0.5–0.8 V forward to *both*** others. (Red on
   base = forward for an NPN.)
2. **Distinguish emitter from collector.** With red still on the base: the leg with the
   **higher** forward reading (typically 50–100 mV higher) is the **emitter**; the lower is the
   **collector**.
3. **Confirm:** emitter ↔ collector reads **OL in both directions**.

* **PASS:** with the flat face toward you and legs down, the order is **E–B–C** left to right,
  matching the footprint.
* **FAIL:** any other order.
* **ON FAIL:** either source EBC parts, or fit these three **rotated/legs crossed** and record
  it in this document. Do not leave it undocumented — an undocumented deviation is the failure
  mode `[PCB-GAUGE3-GPIO5-CONFLICT]` names: *"undocumented is worse than either option."*

### C4 — the electrolytics, C1 and C3

* **C1 (100 µF, +12V rail) and C3 (100 µF, +5V rail).** Confirm the **negative stripe faces the
  GND pad**.
* **FAIL:** stripe on the rail pad.
* **ON FAIL:** a reversed electrolytic on 12 V vents, sometimes loudly. It is also the most
  likely cause of an A1/A2 short.

### C5 — the DIP sockets, before any IC goes in

* **U1 (MCP23017, DIP-28), U2/U3/U4 (L293D, DIP-16).** Confirm the **notch on the socket matches
  the silkscreen notch**, then confirm pin 1 by meter, not by eye:
  * **U1 pin 10 → GND**: continuity **beep**. (Pin 10 = VSS.)
  * **U1 pin 9 → +5V**: continuity **beep**. ⚠ **This is the pin MOD A moves. See §D.**
  * **U2/U3/U4 pins 4, 5, 12, 13 → GND**: **beep** on all four.
  * **U2/U3/U4 pins 1, 8, 9, 16 → +5V**: **beep** on all four. (EN12, VS, EN34, VCC — the
    enables are hard-tied high on this board, so **the bridges are always live** the moment the
    part is powered.)
* **ON FAIL:** the socket is rotated. An IC inserted into a rotated socket sees VDD on a GPIO
  and GND on VDD, and dies in under a second.

### C6 — U1 address straps

* Continuity: **U1 pins 15, 16, 17 (A0, A1, A2) → GND**. All three **beep**.
* **ON FAIL:** the expander will answer at an address other than **0x20**. The diagnostic
  firmware detects and decodes this (rung 2 prints which strap is not grounded), so this is a
  check you *can* defer — but it is two seconds here.

---

## §D — THE TWO KNOWN MODS. **Decide before powering anything.**

Both are known **before** the board is fabbed. If the board in your hand is not yet fabbed,
apply MOD A in `generate_pcb.py` and re-run it — the rework below is only for a board that is
already made.

### MOD A ⛔ — **MCP23017 VDD is +5 V while the I²C pull-ups go to +3V3**

**The numbers.**

* U1 pin 9 (VDD) → net 4 = **+5V**. `R_SDA` / `R_SCL` (4.7 kΩ) → net 5 = **+3V3**.
* The MCP23017 datasheet specifies SDA and SCL as I²C-level Schmitt inputs with
  **V<sub>IH</sub> = 0.7 × VDD**. At VDD = 5.0 V that is **3.5 V**.
* The bus can only ever reach **3.3 V**. **The high level is 200 mV below the guaranteed
  input-high threshold.**

**Why it is dangerous rather than fatal.** The *typical* Schmitt threshold on a real part is
~2.5–3 V, so it will very often work — at 25 °C, on this lot, on this bench, today. It is a
design with **no margin**, and when it does fail, it fails as a **corrupted register write with
every I²C transaction returning `ESP_OK`**: a needle driving the wrong coil pattern while the
entire stack reports health. That is why the diagnostic firmware's rung 3 measures an **error
rate over 10,000 round trips** rather than checking a box — a scan proves one ACK at 3.3 V and
says nothing about the ten-thousandth write on a warm afternoon.

**THE FIX — two nets, and the second one is the part an eager fix would miss.**

Pre-fab, in `generate_pcb.py`:

```python
U1_NETS[9]  = 5   # VDD    +5V -> +3V3
U1_NETS[18] = 5   # RESET  +5V -> +3V3   <-- MUST move too
R_RST_NETS  = {1: 5, 2: 5}
```

⛔ **RESET must move with VDD.** Leaving U1 pin 18 on +5 V while VDD is 3.3 V puts the pin at
**VDD + 1.7 V**, above the VDD+0.3 V absolute maximum, which **forward-biases the input's ESD
clamp diode**. Current then flows *into* the 3V3 rail through the protection structure — the
part may appear to work while being slowly damaged, and the 3V3 rail is pulled up by it. This is
a **two-net change**, always.

**Rework on an already-fabbed board** (this is why U1 should be socketed):

1. Fit a **DIP-28 socket** for U1 if one is not already there.
2. **Bend socket pins 9 and 18 outward, horizontally**, so they do not enter the PCB holes.
3. Solder a wire from **each** of those two bent legs to **+3V3** — `J_PWR_TAP` pin 3 is the
   convenient tap.
4. Leave `R_RST` alone. It is a 10 kΩ resistor with **both ends on net 4** (`R_RST_NETS = {1: 4,
   2: 4}`) — a decorative pull-up across a hard tie. Once pin 18 is lifted, R_RST is simply
   +5V-to-+5V and is inert. ⚠ **It also means the board offers no way to reset the expander;**
   a wedged MCP23017 can only be cleared by a power cycle.
5. **Verify the rework with the meter before powering:** U1 socket pin 9 → `J_PWR_TAP` pin 3
   (**beep**); U1 socket pin 9 → `J_PWR_TAP` pin 2 (**no beep**); same two for pin 18.

**What it costs downstream: nothing.** The expander then drives the L293D inputs at 3.3 V, and
the L293D datasheet gives **V<sub>IH</sub> = 2.3 V max for V<sub>CC1</sub> = 4.5–7 V** — about
**1.0 V of margin**, comfortably clear.

⛔ **DO NOT FIX IT THE OTHER WAY.** Moving `R_SDA`/`R_SCL` to +5 V would put 5 V on the
ESP32-C3's **GPIO4 and GPIO5, which are not 5 V tolerant.** That "fix" is strictly worse than
the defect: it trades an intermittent expander for a destroyed MCU. Check B2.3/B2.4 if there is
any doubt about which rail the pull-ups are on.

> **Also worth checking on `button_matrix_4x4`** — it carries two MCP23017s and the same design
> hand wrote both. Not claimed here; **checked, not assumed.**

### MOD B — the LM7805 dissipates 1.96 W typical / 3.93 W peak

**The arithmetic:**

| load | typical | peak |
|---|---|---|
| ESP32-C3 (WiFi STA) | ~120 mA | ~350 mA (TX) |
| 3× L293D quiescent | ~87 mA | ~138 mA |
| 6× coils energised | ~72 mA | ~72 mA |
| MCP23017 | ~1 mA | ~1 mA |
| **total** | **~280 mA** | **~561 mA** |

**P = (12 − 5) × I = 1.96 W typical, 3.93 W peak.**

* A **bare TO-220 in free air is ~65 °C/W**: 1.96 W × 65 = a **127 °C rise**, putting the
  junction at ~152 °C against a **150 °C thermal shutdown**. ⛔ **It would thermal-cycle at
  IDLE** — and a regulator cycling in and out of shutdown presents as a board that reboots
  randomly, which is a diagnosis you will chase into the firmware.
* **A heatsink of ≤25 °C/W (θ<sub>JA</sub> total) is MANDATORY.** 1.96 W × 25 = a **49 °C
  rise** → ~75 °C on a 25 °C bench. At the 3.93 W peak that is a 98 °C rise → ~123 °C, under
  shutdown but not comfortable; peaks are short (WiFi TX bursts) and thermal mass rides them.
* ⚠ **Feed it REGULATED 12 V.** A 13.8 V "12 V" supply (bench, automotive) raises this to
  **2.46 W typical / 4.94 W peak** and the margin disappears.

**The better fix — a BOM change with no PCB change.** A **Recom `R-78E5.0-1.0`**-class switching
module has the **same In/GND/Out pin order** and 0.1″ pitch as the TO-220 footprint.
Dissipation falls from ~2 W to **~0.15 W** and **the heatsink disappears entirely**. ⚠ Verify
the **body outline and pin pitch** against the TO-220 footprint before ordering — the *pinout*
matches; the *mechanical envelope* is what to check.

* **PASS (§E5):** with a heatsink, the tab is warm and holdable after 10 minutes.
* **FAIL:** too hot to hold, or the board resets periodically.
* **ON FAIL:** fit a larger heatsink or switch to the module. **Do not proceed to the real
  firmware with a regulator in thermal shutdown** — it will present as random reboots and the
  diagnostic firmware's rung 1 will report `ESP_RST_BROWNOUT`, which is the correct diagnosis
  arriving after you have already suspected the code.

### MOD C (do this now, it is free) — **mark `J_EXP_ESP` pins 1 and 4 by hand**

⛔ **`J_EXP_ESP` pin 1 = GPIO2 and pin 4 = GPIO9 are BOOT STRAPPING PINS, and the silkscreen
does not say so.**

* The ESP32-C3's boot straps are **GPIO2, GPIO8 and GPIO9 — those three only** (datasheet §2.4;
  corroborated by `[SPEC:CXC-BOARD-HW]` `strapping-and-uart-caveats`, owner-confirmed on this
  module during the 2026-08-12 bring-up).
* **GPIO0, GPIO1 and GPIO3 are NOT straps on the C3.** That was checked first and deliberately,
  because the brief that produced the board profile assumed they were. GPIO0 is a strap on the
  *classic* ESP32 and on the *S3* — carrying that across parts is the same class of
  habit-collision as assuming a CAN pair crosses the way a UART pair does. **The three 2N2222
  backlight nets are boot-safe.**
* **What the check did find is `J_EXP_ESP`.** Anything hung on pin 1 or pin 4 that presents a
  low impedance to ground at reset — **an LED to GND, an NPN base, i.e. exactly this board's own
  three-times-repeated design idiom** — prevents boot, silently and permanently.

**Do now, with a fine marker:** write **`GPIO2 ⚠STRAP`** next to `J_EXP_ESP` pin 1 and
**`GPIO9 ⚠STRAP`** next to pin 4. Silkscreen is free on the next revision; a marker is free
today. `J_EXP_MCP` is safe — its four pins are ordinary expander I/O.

**Full `J_EXP_ESP` pinout, since nothing on the board states it:**

| pin | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| GPIO | **2 ⚠STRAP** | 6 | 7 | **9 ⚠STRAP** | 10 | 20 (U0**RX**D) |

---

## §E — POWER APPLIED. **No ESP32-C3 module. No MCP23017. No L293Ds.**

Everything in §A–§D must be clean first. Sockets empty; only the passives, D1, the regulator and
the transistors are on the board.

Meter in **DC volts, 20 V**. Black probe stays on `J_PWR_TAP` pin 4 (GND) throughout.

### E1 — apply 12 V

Set the bench supply to **12.00 V**, current limit **500 mA** if available, and connect to
`J_PWR` (**pin 1 = +12V_IN, pin 2 = GND**).

* **PASS:** the supply's current draw is **< 20 mA** (the 7805's quiescent current plus leakage).
* **FAIL:** the supply hits its current limit, or draws > 100 mA.
* **ON FAIL:** **cut power immediately.** There is a short §A did not find — most likely one that
  only conducts above a diode drop. Re-check A1/A2/A4 and look for solder splash under the
  TO-220.

### E2 — `J_PWR_TAP` pin 1 (+12V)

* **PASS: 11.0–11.6 V.**
* ⚠ **11.3 V is CORRECT, not a fault.** The ~0.6 V shortfall is **D1's forward drop** — you are
  measuring *after* the reverse-protection diode. Expecting 12.0 V here and finding 11.3 V is a
  false alarm this step exists to prevent.
* **FAIL:** ~0 V, or the full 12.0 V.
* **ON FAIL (0 V):** D1 is open or backwards — go back to §C1.
* **ON FAIL (exactly 12.0 V):** D1 is **shorted**, so there is no reverse protection at all. The
  board works and one wrong connection later destroys everything on it. Replace D1.

### E3 — `J_PWR_TAP` pin 2 (+5V)

* **PASS: 4.80–5.20 V.**
* **FAIL:** outside that, especially **> 5.5 V** or **< 4.5 V**.
* **ON FAIL (≈ input voltage):** the regulator is bypassed or fitted backwards — §C2.
* **ON FAIL (0 V):** the regulator is dead, or in shutdown from a load §A missed.
* **ON FAIL (4.5–4.8 V):** ⚠ **note this and re-read after the ICs are in.** A sagging 5 V rail
  *lowers* the MCP23017's V<sub>IH</sub> (0.7 × VDD) and can make MOD A's defect **pass for the
  wrong reason** — a marginal design masked by a marginal rail. Two faults cancelling is not a
  pass; it is a board that fails when one of them is fixed.

### E4 ⛔ — `J_PWR_TAP` pin 3 (+3V3) **with no module seated**

* **PASS: 0.00 V.** Anything below ~0.1 V.
* **FAIL: any reading near 3.3 V, or near 5 V, or anything above ~0.2 V.**
* ⛔ **THIS IS THE FAILURE THAT LOOKS LIKE A PASS.** **+3V3 is generated ON the ESP32-C3
  module**, from the module's own LDO fed by its 5 V pin. **The board does not make this rail.**
  With no module seated there is no source, so the only correct reading is **zero**.
* A reader who expects "three rails, three readings" sees 3.3 V here and ticks the box. **What
  it actually means is a short or a backfeed from +5V into +3V3** — and the moment a module is
  seated, its 3V3 output is fighting the +5V rail through that path. Expect a dead module.
* **ON FAIL:** go back to **§A4** (`J_PWR_TAP` 2 ↔ 3) and find the bridge. Do not seat a module.

### E5 — the regulator's temperature

Leave it powered for **10 minutes** with nothing seated, then touch the LM7805 tab.

* **PASS:** cool. With no load it dissipates only its own quiescent current — a few tens of mW.
* **FAIL:** warm or hot **with nothing seated**.
* **ON FAIL:** the regulator is fighting a load that should not exist. Back to §A.
* ⚠ **The real thermal test is §F5, under load.** This one only establishes a baseline.

### E6 — reverse-polarity, which is what D1 is for

Power off. **Swap the two supply leads at `J_PWR`.** Power on briefly (a few seconds).

* **PASS:** `J_PWR_TAP` pin 1 reads **≈ 0 V**, pin 2 reads **≈ 0 V**, and supply current is
  **≈ 0 mA**. Nothing powers up.
* **FAIL:** any rail comes up, or the supply draws current.
* **ON FAIL:** D1 is shorted or backwards — §C1 and §E2. **Fix this before the board ever leaves
  the bench**; reverse protection that does not protect is worse than none, because it is
  relied upon.
* Restore correct polarity afterwards. ⚠ **Mark the screw terminal with polarity by hand** while
  you are here.

Record:

```
E1 draw ____ mA   E2 +12V ______ V   E3 +5V ______ V
E4 +3V3 ______ V  (MUST be 0.00)     E5 tab ______   E6 reverse ______
```

---

## §F — SEAT THE PARTS, ONE LAYER AT A TIME

**Power OFF for every insertion.** Never insert or remove an IC with the rails up.

### F1 — the MCP23017 and the three L293Ds

1. Power off, supply disconnected.
2. Insert **U1**, notch matching the socket notch. If MOD A rework is fitted, confirm socket pins
   9 and 18 are on their jumpers and **not** in the board holes.
3. Insert **U2, U3, U4**, notches matching.
4. Power on. **PASS:** supply draw **90–140 mA** (three L293D quiescent + expander).
   **FAIL:** > 250 mA, or a part warming.
5. **ON FAIL:** power off at once and feel each IC. **The warm one is the one that is in
   backwards.**
6. Re-read **E2/E3/E4**. **+3V3 must still read 0.00 V** — nothing seated so far generates it.
7. ⛔ **If MOD A is fitted, confirm U1 pin 9 reads 3.30 V, not 5.00 V**, with the meter on the
   socket leg. This is the one measurement that proves the rework took.

### F2 — identity, before the module is anywhere near the board

⛔ **A port path is not an identity.** `/dev/cu.usbmodem*` numbering has changed device **five
times** on this bench, and iterating over ports once reset a live controller that had been
explicitly ring-fenced.

* Read the module's USB serial **passively**, with no side effects:

  ```bash
  system_profiler SPUSBDataType | grep -A6 -i "esp\|jtag\|serial"
  # or
  ioreg -r -c IOUSBHostDevice -l -w0
  ```
  A native-USB ESP32 publishes its **MAC as the USB serial number**.
* ⛔ **`esptool chip-id` is NOT an identity check.** It resets the board to connect, and that
  reset is precisely the harm the identity check exists to prevent.
* ⚠ **`AA:BB:CC:DD:EE:FF` is the LIVE dual-throttle controller. `AA:BB:CC:DD:EE:FF` is the
  c3lcd/c3can board. Never either.**

### F3 — seat the module, and re-measure

1. Power off. Insert the ESP32-C3 Super Mini into `J_ESP_L` / `J_ESP_R`, **USB-C at the bottom,
   pin 1 at the GPIO0 end.** ⚠ Getting this 180° wrong puts +5 V onto GPIO0 and GND onto GPIO5.
   Confirm by meter before powering: **`J_ESP_L` pin 8 must be the module's 5V pin** and
   **`J_ESP_L` pin 7 must be its GND**.
2. Power on. **`J_PWR_TAP` pin 3 (+3V3) must NOW read 3.20–3.40 V.**
   * **FAIL (still 0 V):** the module's LDO is not running — check `J_ESP_L` pin 8 is actually
     carrying 5 V to the module.
   * **FAIL (> 3.5 V):** the +5V rail is bleeding into +3V3. Power off; back to §A4.
3. Supply draw should now be **~200–250 mA** (module idle, radio not yet up).

### F4 ⛔ — the ROM banner: prove the substrate with something you did not write

**Before flashing anything of ours.** The ESP32 ROM bootloader prints on **UART0 TX at 115200 on
every reset, regardless of the app's console configuration.** That banner proves TX, ground and
baud with **no code of ours involved** — the free negative control the whole rest of the ladder
rests on. On 2026-08-11 exactly this trick excluded the wiring on a new UART link before any
transport was a suspect.

> ⛔ **CORRECTION TO `rack_triple_gauge.md` — PROBE `GPIO21`, NOT `GPIO20`.**
> On the **ESP32-C3, U0TXD = GPIO21 and U0RXD = GPIO20.** `rack_triple_gauge.md` §1 and rung 2
> had these reversed and sent you to **GPIO20**, which is the *receive* pin. Probing RX for a
> transmit banner yields **silence**, and rung 2's stated failure action reads *"that is a power,
> ground or module-seating fault"* — so a correct board would have been condemned. The repo's own
> `esp32c3-supermini-st7796.md` has it right: *"GPIO21 is UART0 TX."* **The main document is
> corrected in the same commit as this one.**

* **Probe: `J_ESP_R` pin 1 = GPIO21 = U0TXD.** Ground to `J_PWR_TAP` pin 4. A USB-serial adapter
  at **115200 8N1**, or a scope.
  * ⚠ **GPIO21 is not on `J_EXP_ESP`.** That header breaks out GPIO20 (RX) on pin 6 but not
    GPIO21, so the header pad on `J_ESP_R` pin 1 is the only access. Probe the solder side.
  * ⚠ **UART crosses; CAN does not.** Adapter **RX** to board **TX (GPIO21)**. Two wires between
    two devices, opposite rules for the two protocols, and the habit from the last link carries
    into the next one.
* Press reset (or power-cycle).
* **PASS:** a burst of `ESP-ROM:esp32c3-...` / `boot:0x...` text at 115200.
* **FAIL:** nothing.
* **ON FAIL:** the module is not booting, or is held in the wrong boot mode. Check nothing is
  loading GPIO2, GPIO8 or GPIO9 (§D MOD C), and re-check the module's 5 V and GND.

### F5 — the regulator under real load

Leave the board powered with the module seated for **10 minutes**, then feel the LM7805 tab.

* **PASS:** warm, holdable. With a ≤25 °C/W heatsink, expect roughly **70–85 °C** — hot to touch
  but not instantly painful.
* **FAIL:** too hot to hold for a second, or the board resets periodically.
* **ON FAIL:** larger heatsink, or fit the switching module (§D MOD B). ⚠ **A regulator cycling
  in and out of thermal shutdown presents as random reboots** and will send you into the
  firmware. The diagnostic firmware's rung 1 reports `ESP_RST_BROWNOUT` when this happens — a
  correct diagnosis, arriving late.

### F6 — the recovery path, understood BEFORE the first flash

⛔ **A custom board has no factory image and never will.** On a vendor board you `esptool
read-flash` the as-shipped firmware before the first write. **There is nothing to back up here**,
so the safety net has to be *created*:

* **Primary: the socket.** The module is socketed on 2× 1×8 headers. Pull it and flash it out of
  circuit, or on a spare. That is the recovery path, and it is why the sockets matter.
* **Secondary: hardware download boot.** **GPIO9 (BOOT) is on `J_ESP_R` pin 4**, so
  download-mode entry survives a firmware that breaks native USB.
* **Understand both now.** After the first flash is not the time to discover neither exists.

Record:

```
F1 draw ____ mA   U1 pin9 ______ V (3.30 if MOD A)
F3 +3V3 ______ V   draw ____ mA
F4 ROM banner on GPIO21:  seen / NOT seen
F5 tab after 10 min: ______
```

---

## §G — HANDOFF: what the meter has proven, and what only firmware can

**⛔ Everything above is chain-independent of the diagnostic firmware, and the diagnostic
firmware cannot substitute for any of it.**

### The meter has PROVEN

* No rail is shorted to any other rail or to ground.
* SDA and SCL reach U1 pins 13 and 12, through 4.7 kΩ pull-ups that go to **+3V3** and not +5 V.
* All twelve MCP23017 → L293D input lines are continuous, with no bridges between neighbours.
* All twelve L293D outputs reach their `J_MOT` pins, with no output-to-output bridges.
* The three backlight paths are continuous through their 150 Ω resistors to correctly-identified
  transistor bases.
* Each motor harness has two isolated ~280 Ω coils **on the pins the driver expects**.
* Every rail is at its nominal voltage under real load, and **+3V3 read 0 V with no module
  seated.**
* Reverse-polarity protection actually protects.

### The meter CANNOT see, and the firmware is next

* **A marginal logic level.** MOD A's 200 mV V<sub>IH</sub> deficit is invisible to a meter: the
  DC level is 3.3 V and reads 3.3 V. It shows up as **one corrupted register write in ten
  thousand**, and downstream as a needle that occasionally jumps.
  → `[env:rack_triple_gauge_selftest]` **rung 3**, which reports an error rate over 10,000 round
  trips at 400 kHz and 2,000 at 100 kHz, with a fault injector proving the counter can move.
* **A dead expander output driver**, or a pad that will not pull to a level under load.
  → **rung 4**, per-pin, reading the **GPIO** register (the pad) and never **OLAT** (the latch,
  which would return what was written and be a mirror).
* **Whether the expander answers at 0x20 at all**, and what else is on the bus.
  → **rung 2**.

### Now, in this order

```bash
cd cyd
# 1. the diagnostic FIRST — it drives no needle to a position and does not home
pio run -e rack_triple_gauge_selftest -t upload && \
  pio device monitor -e rack_triple_gauge_selftest

# 2. only when rungs 1-7 are clean and the needs-eyes rows are answered
pio run -e rack_triple_gauge -t upload && \
  pio device monitor -e rack_triple_gauge
```

⚠ **Unplug `J_EXP_MCP` before running the diagnostic** — rung 4 briefly drives GPB4–7.

⛔ **Do not skip to the real firmware because the diagnostic "looked fine".** Rungs 5 and 6 are
**needs-eyes** rows: nothing in software can tell you *which* needle twitched or *which* strip
lit, and a swapped `J_MOT` harness homes perfectly and runs backwards forever.

---

## Appendix — the net table, for probing anything not listed above

Read from `~/Dropbox/Maker/pcb/rack_triple_gauge/generate_pcb.py` and confirmed against the
pad→net assignments in the emitted `rack_triple_gauge.kicad_pcb`.

| net | name | appears at |
|---|---|---|
| 1 | GND | `J_PWR` 2, `J_PWR_TAP` 4, `J_ESP_L` 7, U1 10, U2/3/4 pins 4/5/12/13, all Q emitters, all cap negatives |
| 2 | +12V_IN | `J_PWR` 1, D1 anode |
| 3 | +12V | D1 cathode, U_REG 1, C1/C2, `J_PWR_TAP` 1, `J_LED1/2/3` pin 1 |
| 4 | +5V | U_REG 3, C3/C4/C5/C6/C7, `J_PWR_TAP` 2, `J_ESP_L` 8, **U1 9 (VDD)** ⚠MOD A, **U1 18 (RESET)** ⚠MOD A, R_RST both ends, U2/3/4 pins 1/8/9/16 |
| 5 | +3V3 | `J_ESP_L` 6, `J_PWR_TAP` 3, R_SDA 2, R_SCL 2 |
| 6 | SDA | `J_ESP_L` 5 (GPIO4), R_SDA 1, **U1 13** |
| 7 | SCL | `J_ESP_R` 8 (GPIO5), R_SCL 1, **U1 12** |
| 10–13 | M1_IN1–4 | U1 21/22/23/24 → U2 2/7/10/15 |
| 14–17 | M2_IN1–4 | U1 25/26/27/28 → U3 2/7/10/15 |
| 18–21 | M3_IN1–4 | U1 1/2/3/4 → U4 2/7/10/15 |
| 30–33 | MOT1_A–D | U2 3/6/11/14 → `J_MOT1` 1/2/3/4 |
| 34–37 | MOT2_A–D | U3 3/6/11/14 → `J_MOT2` 1/2/3/4 |
| 38–41 | MOT3_A–D | U4 3/6/11/14 → `J_MOT3` 1/2/3/4 |
| 50/53/56 | LED1/2/3_GPIO | `J_ESP_L` 1 (GPIO0) / 2 (GPIO1) / 4 (GPIO3) → R_LED*1* |
| 51/54/57 | LED1/2/3_BASE | R_LED*2* → Q_LED base |
| 52/55/58 | LED1/2/3_COL | Q_LED collector → `J_LED*` pin 2 |
| 60–63 | GPB4–7 | U1 5/6/7/8 → `J_EXP_MCP` 1/2/3/4 |
| 70 | **GPIO2 ⚠STRAP** | `J_ESP_L` 3, `J_EXP_ESP` 1 |
| 71 | GPIO10 | `J_ESP_R` 3, `J_EXP_ESP` 5 |
| 72 | **GPIO8 ⚠STRAP** | `J_ESP_R` 5 |
| 73 | GPIO20 (**U0RXD**) | `J_ESP_R` 2, `J_EXP_ESP` 6 |
| 74 | GPIO21 (**U0TXD** — the ROM banner) | `J_ESP_R` 1 |
| 75 | **GPIO9 ⚠STRAP** (BOOT) | `J_ESP_R` 4, `J_EXP_ESP` 4 |
| 76 | GPIO7 | `J_ESP_R` 6, `J_EXP_ESP` 3 |
| 77 | GPIO6 | `J_ESP_R` 7, `J_EXP_ESP` 2 |

**Component values:** `R_SDA` = `R_SCL` = **4K7**; `R_RST` = **10K** (both ends on net 4);
`R_LED1/2/3` = **150R**; `C1`, `C3` = **100 µF**; `C2`, `C4`, `C5`, `C6`, `C7` = **100 nF**;
`D1` = **1N5401**; `U_REG` = **LM7805 TO-220**; `Q_LED1/2/3` = **2N2222 TO-92, footprint assumes
EBC** (§C3).
