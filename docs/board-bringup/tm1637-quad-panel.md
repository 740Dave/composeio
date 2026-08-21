# TM1637 Quad Panel — hookup guide

**Four TM1637 4-digit modules on one ESP32-C3 SuperMini. Shared CLK, individual DIO.**

- Diagram: `docs/board-bringup/tm1637-quad-panel.hookup.svg` (opens natively in the doc-viewer) and `.png`
- Netlist + bindings (single source for both): `python/composeio/render/examples/tm1637_quad_panel.compose.yaml`
- Part facts: `[SPEC:CXC-TM1637]` (`corpora/platform-specs/69-tm1637-peripheral.lts`), `docs/board-bringup/tm1637.descriptor.yaml`

> ⛔ **NOTHING HERE HAS BEEN OBSERVED ON HARDWARE.** No TM1637 has been connected to anything in
> this project as of 2026-08-15. Nine evidence slots stand EMPTY at `[CXC-TM1637-EV-INDEX]`.
> Every number below is either a datasheet citation or arithmetic; the section
> **"What is fact, what is arithmetic, what needs your eyes"** says which is which, per row.

<!-- BEGIN GENERATED: warnings-and-disclaimers (composeio.maker.hookup_guide) -->
## Warnings and Disclaimers

*Generated from this project's `hookup.must_not:` and `hookup.facts:` by `composeio.maker.hookup_guide` — do not edit by hand. It is derived from the netlist so it cannot drift from the wires it warns about.*

### ⛔ MUST NOT — machine-checked

The parser **refuses** a netlist that violates any of these, so a build that got this far cannot have broken one *on paper*. They are listed anyway, because the check cannot see your soldering iron.

- **`no-boot-straps`** — leave UNCONNECTED: mcu.GPIO2, mcu.GPIO8, mcu.GPIO9
  <br>*why:* ESP32-C3 STRAPPING PINS.  A peripheral holding one of these at reset changes the boot mode — the failure looks like a dead board, not like a wiring fault.  Also declared fleet-wide in the board descriptor's `pin_contract.reserved`; carried here too because THIS scene must refuse on its own, off-repo, where descriptors are not present.
- **`console-uart-stays-free`** — leave UNCONNECTED: mcu.GPIO20, mcu.GPIO21
  <br>*why:* GPIO20/21 are U0RXD/U0TXD.  The ROM bootloader banner on UART0 is the substrate proof that costs nothing and excludes wiring faults BEFORE any firmware runs.  Spending these two pins on displays buys one more module and forfeits the cheapest diagnostic on the board.
- **`b-header-dio-unconnected`** — leave UNCONNECTED: tm1.DIO-B, tm2.DIO-B, tm3.DIO-B, tm4.DIO-B
  <br>*why:* The `-B` pads are the SAME FOUR NETS as the A header, INCLUDING DIO. VCC/GND/CLK may legitimately be linked on either header; a DIO-B link commons DIO between modules and produces identical displays.  The whole asymmetry of this topology is that every DIO-B stays open.
- **`dio-never-common`** — never on one net: tm1.DIO, tm2.DIO, tm3.DIO, tm4.DIO
  <br>*why:* Individual DIO per module IS the topology ([CXC-TM1637-SHARED-CLK]). Two modules on one DIO net show IDENTICAL CONTENT — a well-formed, confident, wrong panel with no error anywhere.  Checked against the computed nets, so a link made on the pass-through pads is caught too.

### ⛔ MUST NOT — stated (no machine can check these)

Nothing in the toolchain can see a mechanical butt-joint, a habit, or a firmware behaviour. These are carried in the artifact and checked by **you**.

- **`do-not-butt-the-modules`** — Do not butt the modules together on the pass-through headers.  Use them PIN-BY-PIN for VCC/GND/CLK only.  ⛔ Nothing here can check this: a mechanical joint is not a wire, and this netlist looks identical either way.
  <br>*why:* The two 4-pin headers are wired IN PARALLEL along the board, so butting modules together commons all four nets including DIO.  They LOOK like a daisy-chain and are not one ([CXC-TM1637-NO-CHAIN]).
- **`no-added-pullups`** — Do not fit pull-up resistors on CLK or DIO.  A hookup diagram showing them is WRONG.  (Stated rather than checked: no resistor part exists in the catalog to forbid, so an `absent_parts:` clause here could never fire — and a constraint that cannot fire reads as protection.)
  <br>*why:* Each module already carries the datasheet reference network — a 10 kOhm pull-up and a 100 pF cap on EACH of CLK and DIO (V2.4 p.8).  A second pull-up in parallel lowers the effective resistance, raises low-side sink current and buys nothing; the BSS138 breakout adds its own 10 kOhm per side on top of that already.
- **`never-touch-an-idle-dio`** — A non-selected DIO must be PARKED HIGH and never driven ([CXC-TM1637-DIO-PARK-HIGH]).  Selection is "drive exactly one DIO, leave the other three released".  ⛔ This is a DRIVER obligation: it is firmware behaviour, invisible to any netlist check.
  <br>*why:* Pulling an idle module's DIO low while CLK is high IS a start condition (V2.4 p.3).  That module arms and latches the next eight bits meant for its neighbour, displaying its sibling's value — well-formed and wrong.

### ⇢ MEASURE BEFORE YOU BUILD

Every row below is an **assumption**, not a measurement. Put a meter on it before the iron is hot: the *if it differs* column is what the design does when the assumption is wrong.

| what | assumed value | where the number came from | ⛔ if it differs |
|---|---|---|---|
| onboard pull-up per line, per module (CLK and DIO) | **10 kOhm to 5 V** | TM1637 V2.4 p.8 shows R1/R2 = 10 kOhm in the reference application, and the common breakout follows it — but the RESISTORS ARE THE VENDOR'S CHOICE, not the chip's, and clones ship 4.7 kOhm silently. | ⛔ EVERY NUMBER IN §1 MOVES. At 4.7 kOhm the shared CLK sees 4 in parallel = 1.175 kOhm (not 2.5 kOhm) and the clamp-injected current roughly DOUBLES to ~1.9 mA; DIO settling — the binding constraint, at 1.2x — improves rather than degrades. ⚠ NOTE WHICH WAY: the decision does NOT invert, it gets STRONGER (a bigger back-drive current makes the direct connection worse, not better), so nobody should read a 4.7 kOhm reading as permission to delete the level shifters. Measure ONE module pad-to-pad with the power OFF; if it reads 4.7 kOhm, recompute §1 and re-derive the 50 kHz choice before building. |
| voltage a released 5 V line settles at on an ESP32-C3 pin | **~3.9 V (V_3V3 + one diode drop)** | ARITHMETIC over an ESD structure Espressif does not specify — 3.3 + 0.6. The abs-max V_IO on this part is 3.6 V, so this number is the whole reason the direct connection is refused. | Below ~3.68 V the direct connection fails V_IH outright at the USB +5 % corner; above ~4.0 V the abs-max violation and the sleep back-drive are both worse. Either way the BSS138 decision stands — this row exists so the number is never quoted as if it were measured. |
| which bit of a data byte lights which segment | **conventional a..g,dp = bit0..bit7 — UNVERIFIED** | [CXC-TM1637-SEGMENT-MAP-UNVERIFIED]. The V2.4 datasheet does not give the tube's segment-to-SEG wiring for THIS breakout; the map is the community convention. | ⛔ A WRONG MAP PRODUCES CONFIDENT, WELL-FORMED, INCORRECT GLYPHS that no machine signal in this project can distinguish from correct ones. Display 1 then 7 and look: '1' must light the two RIGHT-HAND verticals only. Run [CXC-TM1637-EV-01..09] before trusting a digit. |

### Evidence slots — what is fact, what is arithmetic, what is a guess

**6 of 7** evidence slots below are `EMPTY` — they have never touched a bench. An `EMPTY` slot is a record, not an omission: design intent and measured fact read identically in prose, and the difference is the whole verdict.

*These are the rows the ARTIFACT carries (`hookup.facts:`) and a machine can therefore query. The long-form classification of every claim in this document — including the ones that are not netlist facts — is the final section of this guide.*

| # | value | kind | evidence |
|---|---|---|---|
| `module-pullups` | onboard pull-up per line, per module (CLK and DIO) = **10 kOhm to 5 V** | ⚠ ASSUMED | `EMPTY` |
| `tube-colour` | LED colour of the tubes in hand, which fixes VDD = **white (Vf ~3.0-3.4 V, so VDD must be 5 V)** | MEASURED | WHITE — the physical modules in hand, David, 2026-08-15 |
| `vih-min` | TM1637 input HIGH threshold = **3.5 V (0.7 x VDD at VDD = 5.00 V)** | datasheet | `EMPTY` |
| `clamp-voltage` | voltage a released 5 V line settles at on an ESP32-C3 pin = **~3.9 V (V_3V3 + one diode drop)** | ⚠ ASSUMED | `EMPTY` |
| `panel-current` | total panel draw, four modules at full brightness = **~320 mA at 5 V** | computed | `EMPTY` |
| `dio-settling` | DIO rise time, the BINDING timing constraint (not CLK) = **4.15 us (5 tau at 5 kOhm x 165 pF) against a 10 us half-period** | computed | `EMPTY` |
| `segment-map` | which bit of a data byte lights which segment = **conventional a..g,dp = bit0..bit7 — UNVERIFIED** | ⚠ ASSUMED | `EMPTY` |
<!-- END GENERATED: warnings-and-disclaimers -->

---

## 1. `DD-TM1637-LEVEL` — SETTLED: **direct connection**, no level shifter

**Answer: tie the 3.3 V GPIOs straight to CLK and DIO, open-drain, 5 V to VCC. No level shifter.
Four 470 Ω resistors on CLK — for fault containment, not for levels. One supply.**

David challenged an earlier draft of this section that recommended a BSS138 shifter, on the grounds
that the near-universal field practice for this module is exactly the direct connection. **He is right,
and two of my three objections do not survive contact with this installation.** Here they are, re-run
honestly at the corner that actually applies: a rack panel, USB-powered, room temperature, four
modules, always on.

### Option (c) still dies — but by physics, not by preference

Your tubes are **white** (Vf ≈ 3.0–3.4 V). At VDD 3.3 V the segment drivers have no headroom above Vf
and the tube does not light *at all* — that is what V2.4 p.8 note 3 means. Red/green/yellow modules
(Vf ≈ 1.8–2.1 V) do run at 3.3 V, which is why "just run it at 3.3 V" is the first suggestion everyone
makes and why it is wrong here. **VDD = 5 V is fixed**, giving V_IH = 3.5 V and V_IL = 1.5 V (p.10).

### ✅ The pull-up value is no longer an assumption — David read `103` = **10 kΩ, confirmed**

> **Evidence class: MARKING-READ, not meter-measured.** Those are different confidence levels and the
> distinction is kept deliberately. A marking can be misread and a part can be mis-stuffed; a meter
> across an unpowered `CLK`↔`VCC` settles it in ten seconds. But this is enough to close the question
> for design purposes, and it answers the pre-integration slot.

Every number below was computed against 10 kΩ, so **nothing inverts** — and the confirmation is worth
more than it looks, because it is the *favourable* end. What was at risk:

| pull-up | CLK (4 ∥) | CLK clamp | DIO clamp ×4 | total | 470 Ω containment ceiling |
|---|---|---|---|---|---|
| **10 kΩ — CONFIRMED** | 2.5 kΩ | **440 µA** | **110 µA** each | **880 µA** | 1046 Ω — **470 Ω sits at 45 %** ✅ |
| 4.7 kΩ — now excluded | 1.175 kΩ | 936 µA | 234 µA each | 1.87 mA | 479 Ω — **470 Ω would be AT THE EDGE** ⛔ |

At 4.7 kΩ the clamp current would have **more than doubled** and the 470 Ω containment resistor would
have sat 2 % below its V_IL ceiling — unbuildable. The confirmed 10 kΩ is what makes both the direct
connection and the containment resistor comfortable rather than marginal.

⚠ **Still open: are the line CAPACITORS fitted?** David reported *resistors*. See §4b — it does not
change the level decision, and the clock rate chosen below is correct either way.

### Objection 1 — the abs-max / clamp-current argument. ⛔ DOES NOT SURVIVE

A released open-drain line does **not** reach 5 V; the C3's ESD clamp pins it at ≈ 3.9 V. The current
that flows is set by the module's own fitted 10 kΩ — and **the two lines are different networks**,
which I previously blurred into one figure:

| network | topology | pull-up seen | clamp current |
|---|---|---|---|
| **CLK** | ONE C3 pin → FOUR modules | 4 × 10 kΩ ∥ = **2.5 kΩ** | **440 µA** ← worst pin |
| **DIO** (×4) | ONE C3 pin → ONE module | **10 kΩ** | **110 µA** each |
| | | **total into the 3.3 V rail** | **880 µA** |

⛔ **And the honest part: Espressif publishes no input-clamp or injection-current rating for the
ESP32-C3.** There is no vendor number to hold 440 µA against. The ±5 mA/pin figure I quoted earlier is
the *industry convention* from ST / Microchip / Nordic — **not an Espressif spec**, and I should not
have presented it as the standard this was failing.

So the position is: 440 µA on the worst pin, self-limited by a resistor already on the module, at
exactly the operating point of the entire installed base of these modules. **That is not a reason to
add parts.** Objection withdrawn.

### Objection 2 — the 39 mV V_IH margin. ⛔ DOES NOT SURVIVE

| corner | V_line | V_IH req | margin |
|---|---|---|---|
| **25 °C, VBUS 5.00 V — the rack panel** | 3.900 V | 3.500 V | **+400 mV** |
| 50 °C, VBUS 5.10 V — a warm rack | 3.850 V | 3.570 V | **+280 mV** |
| 85 °C, VBUS 5.25 V, LDO −2 % — stacked | 3.714 V | 3.675 V | +39 mV |

**400 mV is comfortable, and it is the corner this panel lives at.** The 39 mV figure required 85 °C
*and* a high VBUS *and* a low LDO simultaneously — a corner a rack panel in a room will not reach. I
over-weighted it.

Two things make it better still, and both cut David's way: **3.5 V is a guaranteed-*max* threshold**,
not the switching point — a real CMOS input switches near VDD/2 ≈ 2.5 V — and V2.4 p.10 specifies
**0.35 V of input hysteresis (CLK/DIN are Schmitt inputs)**. The typical part has well over a volt of
real margin. Objection withdrawn.

### Objection 3 — it forecloses light/deep sleep. ✅ SURVIVES, but does not apply here

This one is real and it is a **hard capability loss**, not a reliability worry. 880 µA cannot be sunk
by an LDO. Against light sleep (~130 µA) and deep sleep (~5 µA) that current **back-drives the 3.3 V
rail**. So:

> **Direct connection means this board can never sleep while the panel is powered.**

**This panel never sleeps** — it is a mains/USB-powered rack display that is always on and always
driving tubes. So it costs nothing *here*. But it is yours to weigh, not mine: if this C3 ever needs
light sleep, the shifter is the fix and `bss138-4ch-levelshift` is kept in the catalog for exactly that.

⛔ **What objection 3 does buy is a wiring constraint, and it is not optional:**

> **Everything runs from ONE supply.** Power the panel from `mcu.5V` (USB VBUS — the 3.3 V LDO is not
> in that path). Both rails then rise and fall together and the back-drive case cannot occur.
> **A separate 5 V brick reintroduces it**, because the panel would be up whenever USB is out. That is
> the point at which a level shifter earns its place again — not before.

### The middle path — and it lands on the *opposite* line from the obvious guess

The natural hypothesis is "series resistor on DIO, CLK direct". **The arithmetic says the reverse.**

```
V_IL ceiling:  DIO (R_p = 10 kΩ)   ->  R_s ≤ 4260 Ω     (a huge budget)
               CLK (R_p = 2.5 kΩ)  ->  R_s ≤ 1046 Ω     (a tight one)
```

DIO has the *room* for a resistor and no *use* for one: its clamp current is already only 110 µA, and
because DIO is point-to-point **a dead module can only pull down its own line** — it is self-isolating.
2.2 kΩ there would buy 110 → 90 µA. Not worth a part.

**CLK is where a resistor earns its keep, and not for levels at all.** CLK is *shared*, so:

> ⛔ A module whose VCC connection goes intermittent is unpowered — but its CLK pin still has an ESD
> clamp to its own dead rail. The other three modules' pull-ups feed 5 V into a dead chip, which can
> hold the **shared** CLK low. **All four displays blank at once, and the ACK reports NAK on all four**,
> pointing you at the host instead of at one connector.

**470 Ω at each module's CLK pin** (star-wired — not one resistor at the host) turns that panel-wide
failure into a single-module failure. And it is free:

```
driven low : common node 47 mV, module CLK pin  270 mV   (V_IL limit 1500 mV)
released   : module CLK pin 3.949 V  ->  V_IH margin +449 mV  (better than direct's +400)
clamp      : 420 µA total             (vs 440 µA with no resistor)
```

**Both margins improve slightly.** ⛔ Do **not** substitute 1 kΩ — on the shared CLK at N=4 that lands
at 1.454 V against a 1.5 V limit, a 3 % budget on a ±5 % part. Usable band is 220–560 Ω. (At 470 Ω the
DC fan-out limit is N ≤ 8; you are at 4.)

### What the shifter would still have been right about

One thing survives and is worth keeping in the record, because it decided the *part choice* and it
remains true if a shifter is ever fitted: **DIO is bidirectional in every transaction, and not because
of the key-scan matrix.** The chip pulls DIO low itself to ACK on the 8th falling clock (p.3), and the
driver reads it (`nak-is-reported`). On a four-module panel that ACK is the **only per-module presence
signal**. So a push-pull direction-pin translator (74LVC1T45/2T45) would silently destroy it, and
TXS0102 is out on every line (TI wants ≥ 50 kΩ pull-ups; yours are 10 kΩ). **If you ever fit a shifter,
it must be a BSS138-style open-drain bidirectional cell.**

### The comparison, plainly

| | **direct connect** ✅ **recommended** | BSS138 shifter |
|---|---|---|
| parts | 4 × 470 Ω (a resistor kit) | 2 × 4-ch breakout + the resistors |
| extra joints | 8 | ~24 |
| build time | ~15 min | ~45 min |
| V_IH margin | **+449 mV** @ 25 °C (+280 mV warm) | +1.33 V worst-case, fully specified |
| clamp on GPIOs | 420 µA, no vendor limit published | **zero — clamp never conducts** |
| abs-max V_IO | exceeded (3.95 V on a 3.6 V pin) | respected |
| light/deep sleep | ⛔ **foreclosed** | available |
| separate 5 V supply | ⛔ not allowed | fine |
| ACK / presence detection | native | native (BSS138 only) |
| matches field practice | **yes** | no |

**Recommendation: build it direct.** The panel is always-on, room-temperature, single-supply, and four
modules deep — every condition that made the shifter look necessary is absent, and the two objections
that argued for it were a borrowed current limit and a corner nobody reaches. The one that survives
(sleep) is a capability this panel does not use, and it is stated so you can overrule me if that
changes.

**What it costs you to ignore the surviving objection:** if you later want this C3 to light-sleep with
the panel powered, you will be back here fitting two breakouts and re-cutting the harness. Nothing else
about the build changes.

## 2. Topology — shared CLK, individual DIO (and why, given the fallback is stronger)

**Implemented in the diagram: `[CXC-TM1637-SHARED-CLK]` — shared CLK, one DIO per module. 4 modules = 5 host pins.**

⛔ **This rests on a STRONG INFERENCE, not a datasheet guarantee.** V2.4 has no state diagram anywhere.
The argument: data input *starts* on a DIO high→low edge while CLK is high (p.3), so a module whose DIO
never moves is never armed. Two facts upgrade it from "probably fine" to "safe under a stated failure
model" — refresh runs off the internal RC oscillator (fosc ≈ 450 kHz, p.10) not off CLK, so there is no
clock-starvation state; and a STOP *"initializes serial communication"* (p.4), so damage from a phantom
bit is bounded to **one transaction** rather than being permanent.

The fallback `[CXC-TM1637-FALLBACK-TOPOLOGY]` — shared DIO, one CLK per module, non-selected CLK parked
LOW — is **mechanically stronger** (latching is defined on the CLK *rising edge*, so a CLK that never
rises cannot latch at all) and costs the **same 5 pins**. I did not choose it, and here is why:

1. **It commons the ACK line across all four modules.** The ACK becomes the OR of four parts, so
   per-module presence detection is destroyed and one wedged module holds DIO low for the whole panel.
   Since I just made the ACK the deciding fact in the level-shift choice, throwing it away here would be
   incoherent.
2. It needs four shifter channels sitting driven-LOW continuously rather than one released-HIGH.
3. The recommendation's risk is **cheaply falsifiable first** (§5), and because the pin count is
   identical, switching later is a rewire — not a redesign.

### ⛔ The driver rule that makes it true

> **A non-selected DIO must be parked HIGH and never touched.**

Pulling an idle module's DIO low while CLK is high **IS a start condition** (p.3). It arms that module,
which then latches the next eight bits meant for its neighbour — **a display showing its sibling's
value, well-formed and wrong**. Selection is *"drive exactly one DIO, leave the other three released"*.
The driver's open-drain seams make this structural: they express only "drive low" and "release", never
"drive high".

### ⛔ The pass-through headers are a trap, and you have already noticed them

Each module has **two 4-pin headers wired pin-for-pin in parallel** — all four nets, **including DIO**.
They look exactly like a chain connector. They are not one: the TM1637 has no serial-out pin and **no
slave address at all** (p.3, verbatim: *"there is no slave address"*).

**Butt-joining your four panelized modules on those headers commons DIO and gives you four IDENTICAL
displays.** In the diagram, CLK / VCC / GND are chained module-to-module and **every DIO runs
individually back to its own shifter channel** — and the entire `-B` header is left unconnected. That
asymmetry *is* the topology. Individual pads on the B header are fine for any net **except DIO**.

---

## 3. Pin selection

| signal | C3 pin | via |
|---|---|---|
| shared CLK | **GPIO6** | `ls1.LV1` → `ls1.HV1` |
| DIO 1 | **GPIO3** | `ls1.LV2` → `ls1.HV2` |
| DIO 2 | **GPIO4** | `ls1.LV3` → `ls1.HV3` |
| DIO 3 | **GPIO5** | `ls1.LV4` → `ls1.HV4` |
| DIO 4 | **GPIO7** | `ls2.LV1` → `ls2.HV1` |

These five are **exactly** the non-strap, non-console, non-ADC-reserved pins that this fleet has
actually *probed* on this board. `esp32c3-supermini-can.descriptor.yaml` records a scan of *"every
accessible pin (0,1,2,3,4,5,6,7,20,21)"* — that is measured evidence, not a pinout image.

**What was avoided, and why:**

- **GPIO2, GPIO8, GPIO9** — ESP32-C3 **strapping pins**. Excluded outright. (GPIO0/1/3 are *not* straps
  on a C3 — that was checked and refuted, so GPIO3 is used deliberately.)
- **GPIO20 / GPIO21** — the console UART (U0RXD/U0TXD). Kept free on purpose: the ROM bootloader prints
  on UART0 at 115200 regardless of the app's console config, so a reset gives you a banner that proves
  TX, ground and baud **with no firmware involved**. That is the cheapest substrate proof there is and
  it costs two pins you do not need.
- **GPIO0 / GPIO1** — ADC1 CH0/CH1, reserved so one C3 can also carry the dual-throttle pots.
- **GPIO18 / GPIO19** — not on the header (USB D−/D+ internally).
- ⚠ **GPIO10 deliberately NOT used**, although the catalog lists it on the header and it is not a strap:
  **it was not in that probed set**, so its accessibility on this board is unconfirmed. If a continuity
  check confirms it, GPIO10 is the free pin that buys GPIO6 back.

⚠ **Stated collision: this panel and the DY-SV5F audio module cannot share one C3 as things stand.**
`dual_throttle.compose.yaml` claims **GPIO5** (UART TX) *and* **GPIO6** (BUSY), and this panel now needs
both. Freeing GPIO10 is the way out. Recorded rather than silently absorbed.

⚠ **Continuity-check the five pins header-to-pad before you solder.** A prior bring-up on this exact
board lost a session to firmware listening on a pin that had no wire on it — the note reads *"GPIO4 is
connected to nothing"*, which is a fact about that harness, **not** about the pad (the same probe shows
GPIO4 responding correctly to the internal pull-up). Do not read it as a dead pin, and do not skip the
check either.

---

## 4. The 5 V rail

```
4 modules × ~80 mA at full brightness (step 7, 14/16 duty)  =  ~320 mA
ESP32-C3 with WiFi: ~100 mA average, ~350 mA peak on TX
                                            TOTAL  ~420 mA avg / ~670 mA peak
```

**The diagram powers the panel from an EXTERNAL 5 V feed (`psu`), not from the C3.** Two reasons:

1. **A 500 mA USB 2.0 port does not cover 670 mA of peak.** It would work at reduced brightness and fail
   intermittently at full — the worst possible failure shape.
2. It makes *"the C3's regulator is not in this path"* **true by construction** rather than by argument.

On the "is the regulator in the path" question specifically: on an ESP32-C3 SuperMini the `5V` pin is
**USB VBUS direct**, and the 3.3 V LDO only feeds the ESP32 core — so even if you *did* power the panel
from `mcu.5V`, the LDO would not be carrying panel current. **The USB port would be.** ⚠ Some SuperMini
clones fit a Schottky between VBUS and the 5V pin; at 320 mA that drops ~0.3 V (and a 500 mA-rated
SOD-323 is then marginal). **Meter this before relying on it** — see §5 step 2.

⛔ **The common ground is not optional.** `psu.GND` must tie to `mcu.GND`. Two supplies with no shared
return means CLK/DIO have no defined reference and the level-shifting is meaningless. It is one wire and
it is the one people leave out.

**Brightness is a STEP INDEX, never a duty cycle.** V2.4 p.5 gives eight non-linear steps —
1/16, 2/16, 4/16, 10/16, 11/16, 12/16, 13/16, 14/16. There is no 3/16 and no 5…9/16.

⛔ **But dimming is the wrong fix for a peak-current problem, and that was my first mistake here.**
Brightness is PWM duty, so it scales **average** current roughly linearly — the *instantaneous* segment
current is set by the driver and does not fall by one milliamp. The failing constraint above was a
**peak**. The correct mitigation is **bulk capacitance: 470–1000 µF on the 5 V rail at the panel**, plus
**10 µF + 100 nF at each module's VCC pin**. That costs about 50 ¢ and does what dimming cannot. Step 3
is an aesthetic default, not a power mitigation. Sizing: use a **1 A** supply.

⚠ Note the first daisy-chain link (`psu.5V → tm1.VCC`) carries the **whole panel current**, not one
module's share. Inside a 0.1″ header pin (rated > 1 A) and a typical 1 oz trace, but worth knowing
before anyone chains eight.

⚠ **Never connect the external 5 V to the SuperMini's `5V` pin.** If that pin is VBUS-direct with no
ORing diode, doing so while USB is plugged in **shorts a wall supply to your laptop's VBUS.** The
diagram shares **ground only** — that is deliberate.

---

## 4a. Three faults that bite later, and the cheap answers

**(a) A module with a bad VCC connection takes the *entire panel* down.** Half-seat a connector and that
TM1637 is unpowered — but its CLK/DIO pins still have ESD clamps to *its* dead VDD. The other three
modules' pull-ups now feed 5 V into a dead chip, which can hold the shared CLK low. **All four modules
fail, and the ACK reports NAK on all four — pointing you at the host rather than at one connector.**
Fix: **470 Ω in series at *each module's* CLK pin** (not one resistor at the host). With the shifter this
is free — the high side still reaches a full 5.0 V, and a driven-low module CLK reaches 255 mV against a
1500 mV limit. Now a dead module can only pull its own branch down.

**(b) Inductive ground bounce, not IR drop.** The DC answer is boring (~29 mV over 300 mm of 24 AWG —
and there is a pleasant counter-intuitive result: an equal VCC/GND droop actually *improves* high-side
margin by about +40 mV per 100 mV of drop, so stop worrying about it). The AC answer is not: 300 mm of
wire is ~400 nH, and the TM1637 commutates grids at a plausible 2.4 A/µs, giving
**V = L·di/dt ≈ 0.96 V of bounce** at the grid-scan rate, on every line referenced to that ground. That
is not small against a 1.5 V V_IL. Requirements: **LED power ground must not share a conductor with
signal ground return** (separate wires to the supply, joined only there), ground-signal-ground ordering
on the harness if you can, and the decoupling in §4.

**(c) Daisy-chained 0.1″ pin headers are a guaranteed year-two fault.** Four modules chained is three
connector pairs in series on CLK; Dupont contacts fret and oxidise and are the least reliable thing in
the build. The symptom — "module 4 shows garbage" — is indistinguishable from a firmware bug. Prefer a
**home-run star with crimped latching connectors** (JST-XH / Molex), or solder. The per-module ACK check
is the diagnostic that will actually catch this, which is one more reason not to give it up.

**(d) Rewrite all four modules unconditionally, once a second.** These are stateful displays with **no
readback**: a module corrupted by a glitch, a false START or a double-clock stays wrong *forever*. A
periodic rewrite makes the panel self-heal within a second and upgrades the ACK from a boot-time probe
into a **continuous presence monitor**. Cost: 5.6 ms/s — 0.6 % duty. This is arguably worth more than
the entire level-shifting decision above.

---

## 4b. The capacitors are still open — and a rise-time error I owe you

⛔ **First, a correction to an instruction I gave earlier.** I told you to read the cap markings —
*"`101` is 100 pF, `102` is 1 nF"*. **That is wrong for this board.** Those are through-hole disc-ceramic
markings. **SMD ceramic capacitors are unmarked** — plain tan or brown rectangles with nothing printed
on them. You cannot read their value off the part at all.

The right check is a **count**, not a reading: look at the passives clustered near the `CLK`/`DIO`
header pins. Two SMD resistors marked `103` and *nothing else adjacent to those two pins* means the
p.8 note-2 line caps are **omitted** — which plenty of clones do. Unmarked rectangles sitting next to
`VCC`/`GND` are decoupling and are a different question.

⛔ **Second, an arithmetic error I made, and it is the real cost of the direct connection.** I computed
rise time as "70 % of the rail, 1.2 τ". That is the figure for a line rising toward **5 V**. Direct-
connected, the clamp caps the asymptote at **3.9 V**, and V_IH is 3.5 V — so the line is chasing
**89.7 %** of where it is going, and the tail of an exponential is slow:

```
shifter (5.0 V asymptote): 3.5/5.0 = 70.0%  ->  1.20 τ
direct  (3.9 V asymptote): 3.5/3.9 = 89.7%  ->  2.28 τ      nearly DOUBLE
```

**The clamp does not just eat voltage margin — it eats timing margin too.** That is a real cost of
direct-connect that I had not costed, and it is why the clock rate matters here more than it would with
a shifter.

Both cases, with 470 Ω on CLK and the confirmed 10 kΩ pull-ups:

| | caps **FITTED** (100 pF/line) | caps **NOT fitted** (die only) |
|---|---|---|
| CLK: C, τ, rise + tWAIT | 540 pF, 1.35 µs, **4.07 µs** | 140 pF, 0.35 µs, **1.80 µs** |
| DIO: C, τ, rise ← *binding* | 145 pF, 1.45 µs, **3.30 µs** | 45 pF, 0.45 µs, **1.02 µs** |
| **100 kHz** (5 µs half-period) | **1.23× — MARGINAL** | 2.78× — OK |
| **50 kHz** (10 µs half-period) | **2.45× — OK** | 5.56× — OK |

> **This is why the config runs at 50 kHz: it is the setting that is correct under BOTH answers.**
> A full 4-module refresh is 5.6 ms, which at a 1 Hz repaint is 0.6 % duty. There is no throughput
> pressure anywhere in this design, so buying immunity to an unresolved question costs literally
> nothing. If you confirm the caps are absent and ever want 100 kHz, it is available — you will just
> never need it.

Note also that **DIO is the binding constraint, not CLK**, in both columns. The intuition that a shared
clock across four modules is the slow line is wrong: CLK gets four pull-ups in parallel (stiffer) while
each DIO has only one, so DIO settles more slowly despite carrying a quarter of the capacitance.

---

**One thing that is inherently safe, and worth knowing why:** START is *DIO falling while CLK is high*.
A CLK **rising** edge couples a **positive** spike onto a parked DIO (harmless); a CLK **falling** edge
couples negative, but CLK is then low, so no START can be formed. **The crosstalk polarity of
shared-CLK/individual-DIO is structurally benign for false-START.** That is a real property of this
topology, not luck.

---

## 5. Build order — and the one real checkpoint

The wiring is a single engineering batch **except for one genuine technical boundary**: `EV-08` is a
test that *cannot exist* until a two-module harness exists, and its result decides whether the
four-module layout is the right one at all. So:

### Phase A — two modules, then STOP

1. **Meter before power.** With the supply OFF and nothing connected:
   - Continuity `psu.GND` ↔ `mcu.GND` — **must beep.**
   - Resistance `psu.5V` ↔ `psu.GND` — must **not** be a short.
   - On each module: `VCC` ↔ `GND` — should read a few hundred Ω to kΩ, **never 0 Ω**.
   - `CLK` ↔ `VCC` and `DIO` ↔ `VCC` on **each of the four modules** — expect ≈ **10 kΩ**.
     ✅ Already confirmed by marking (`103`), so this is now a *cheap corroboration* rather than a
     gate — but do it on all four anyway, because a mis-stuffed board reads differently from a
     correctly-marked one. It is also the positive confirmation that the pull-ups are fitted and that
     **you must not add your own.**
   - **Count the passives beside `CLK`/`DIO`** — do not try to read a cap value. SMD ceramics are
     unmarked (see §4b). Two `103` resistors and nothing else adjacent means the line caps are omitted.
   - ⛔ **Confirm the header silkscreen order on the physical board.** The diagram draws
     CLK, DIO, VCC, GND; **several clones ship GND, VCC, DIO, CLK.** Swapping VCC and GND on a TM1637 is
     a destructive error, not a functional one.
2. **Meter the host's 5 V path.** Power the C3 by USB alone and measure `mcu.5V` to `mcu.GND`. ~5.0 V
   means VBUS is direct; ~4.7 V means there is a Schottky in the path. Record which.
3. **Power the rails with no signals connected.** Confirm 5 V at each module's VCC pin and 3.3 V at
   `ls1.LV` / `ls2.LV`. ⛔ *Power at the part, then the substrate, then the protocol* — three rounds of
   protocol diagnostics once ran in this fleet against a transceiver that had no supply.
4. Connect **two** modules (CLK shared, DIO individual) and run **EV-01 … EV-07** (§6).
5. Run **`EV-08`** — the load-bearing one — and then **`EV-09` immediately, on the unchanged harness**.

⛔ **`EV-09` is not optional.** It is the negative control: strap the two DIO lines together and confirm
both displays go *identical*. If they do not, the harness is not what you think it is (a broken DIO, an
unpowered module, a wrong pin) and **`EV-08` proved nothing whatever its result was**. An instrument
that can only report success is not an instrument. Rebuilding the harness between the two re-opens the
question they were run to close.

### ⛔ And the ACK itself needs a negative control — it has a false-PASS mode

The per-module ACK is now load-bearing in two places (it decided the shifter choice, and it is the
panel's only presence signal). So ask the fleet's standard question: **what would it read if the
mechanism were broken?**

- **False PASS, the dangerous direction:** if the driver fails to release DIO before the 9th clock —
  pin left push-pull, or set output-low — **the host reads its own driven low and calls it an ACK.**
  A module that is not there reports present. That is structurally the same defect as the drop counter
  in this repo that read zero because it *could not increment*, and the controller that reported
  "successfully initialized" with its interrupt on the wrong GPIO.
- **False NAK, the annoying direction:** if the driver drives DIO push-pull *high* during the ACK, 3.3 V
  through ~25 Ω fights the TM1637's pull-down and the line settles near 2.9 V — a good module reported
  missing.

**Mandatory, on hardware, both directions:** unplug module 3 → the driver must report **NAK on 3 and ACK
on 1, 2, 4**. Plug it back → ACK returns. Then scope DIO during the 9th clock and confirm the host pin
is genuinely high-Z (line at ~0.1 V, **not** ~2.9 V). **Until you have watched it fail correctly, the
ACK is not an instrument.**

### Measurements that settle what neither of us can currently know

In cost order. Items 1 and 2 gate §1's arithmetic; item 5 gates §4's whole USB argument.

| # | measure | settles |
|---|---|---|
| 1 | ~~Pull-up R~~ ✅ **ANSWERED** — SMD marked `103` = 10 kΩ (marking-read, not metered) | R_p confirmed at the favourable end. Nothing in §1 inverts. |
| 2 | **COUNT** the passives beside CLK/DIO (SMD ceramics are UNMARKED — do not try to read a value) | Whether the p.8 line caps are fitted. 50 kHz is correct either way; only 100 kHz depends on it. |
| 3 | SuperMini `5V` pin vs USB VBUS, **under load** | Whether there is a series Schottky (a lower VDD *lowers* V_IH and helps you — but do not assume it) |
| 4 | 3.3 V rail, DMM, loaded | Real LDO tolerance; replaces the ±2 % assumption in §1's corner table |
| 5 | One module's current at all 8 brightness steps showing `8888.` | Replaces the 20–80 mA estimate the entire 500 mA argument rests on |
| 6 | Are there segment resistors near the tube? | Resistive vs constant-current drive — decides whether mild thermal feedback exists |
| 7 | Hang 1 nF on CLK — does the panel corrupt? | Whether edge rate is load-bearing (→ the 74HCT variant in §1) |
| 8 | Stall 10 ms mid-byte — does the display still update? | Whether the TM1637 has any bus timeout. If none, bit-banging is preemption-safe and a whole class of WiFi-jitter risk evaporates. |
| 9 | Scope a **parked** DIO at the far module, all four showing `8888.`, **spring-tip ground, not a clip lead** | Whether inductive ground bounce (§4a b) threatens a false START. A 6″ clip lead will manufacture the exact ringing you are hunting. |

**A note on timing:** the config runs at **50 kHz, not the driver's 100 kHz default.** The naive check
("1.42 µs rise against a 5 µs half-period, 3.5×") is wrong twice — tWAIT is 1 µs of CLK high *above
V_IH*, making the CLK budget 2.42 µs → 2.07×; and the binding constraint is actually **DIO settling**
(5τ ≈ 4.15 µs against 5 µs → **1.2×**). A full 4-module refresh is 5.6 ms at 50 kHz, which at 1 Hz is
0.6 % duty. There is no throughput pressure here at all, so 2× on every timing number is free.

**If you can, put one module from a *different vendor lot* on the panel.** Four identical modules from
one lot prove the software and one silicon revision. A different-vendor module is the only configuration
that can *disagree* with you about pull-up values, cap values and threshold spread — which is precisely
the axis this whole analysis is exposed on.

**If `EV-08` comes back negative:** switch to `[CXC-TM1637-FALLBACK-TOPOLOGY]` — shared DIO, one CLK per
module, non-selected CLK parked LOW. Same 5 pins, same shifter channels, and it does not depend on the
falsified inference.

### Phase B — add modules 3 and 4

Only after `EV-08`/`EV-09` are green. Then re-run `EV-03` (digit order) and `EV-08` at N=4.

---

## 6. Wire order (from the diagram)

Work rails-first, then low side, then high side, then the panel chain.

1. **Grounds:** `psu.GND → mcu.GND`, `→ ls1.GND-HV`, `→ ls2.GND-HV`; `mcu.GND → ls1.GND-LV`, `→ ls2.GND-LV`.
2. **Rails:** `psu.5V → ls1.HV`, `→ ls2.HV`; `mcu.3V3 → ls1.LV`, `→ ls2.LV`.
   ⛔ `LV` is not just a reference — it is the **gate bias for all four FETs**. With LV unpowered every
   channel is off and nothing works at all.
3. **Host side:** `GPIO6→ls1.LV1` (CLK), `GPIO3→ls1.LV2`, `GPIO4→ls1.LV3`, `GPIO5→ls1.LV4`,
   `GPIO7→ls2.LV1`.
4. **Panel side:** `ls1.HV1→tm1.CLK`, `ls1.HV2→tm1.DIO`, `ls1.HV3→tm2.DIO`, `ls1.HV4→tm3.DIO`,
   `ls2.HV1→tm4.DIO`.
5. **Panel power chain:** `psu.5V→tm1.VCC→tm2.VCC→tm3.VCC→tm4.VCC`; same for GND.
6. **Shared CLK chain:** `tm1.CLK→tm2.CLK→tm3.CLK→tm4.CLK`.
7. **Leave every `-B` pad unconnected.** Especially `DIO-B`.

---

## 7. What is fact, what is arithmetic, what needs your eyes

> The rows that are also **machine-readable** live in the netlist's `hookup.facts:` block and are
> reproduced, with their `EMPTY` evidence slots, in the generated **Warnings and Disclaimers**
> section at the top of this guide. This table is the LONG FORM: it classifies every claim in the
> document, including the ones that are not properties of the netlist and so have nowhere to live
> in the artifact. When the two disagree, the artifact wins — it is the one a machine can check.

| claim | class |
|---|---|
| V_IH = 0.7×VDD, V_IL = 0.3×VDD, 10 kΩ + 100 pF per line, CI = 15 pF, tWAIT ≥ 1 µs, fosc ≈ 450 kHz | **DATASHEET FACT** — TM1637 V2.4, pp. 8/10 |
| No slave address; no serial-out pin; ACK on the 8th falling edge; STOP re-initializes | **DATASHEET FACT** — V2.4 p.3, p.4, p.1–2 pinout |
| Brightness steps 1,2,4,10,11,12,13,14 / 16 | **DATASHEET FACT** — V2.4 p.5 |
| Input hysteresis V_H typ 0.35 V — CLK/DIN are Schmitt inputs | **DATASHEET FACT** — V2.4 p.10 (this is what defuses the slow-edge worry in §1) |
| White/blue tubes will not light at VDD 3.3 V | **DATASHEET FACT** (p.8 note 3); the *Vf-headroom explanation* is my **inference** |
| Clamp pins a released line at ≈ 3.9 V; I_clamp ≈ 0.88 mA total | **MY ARITHMETIC**, with an assumed V_f = 0.6 V and R_dson = 25 Ω. Not measured. |
| Worst-case V_IH margin 39 mV (85 °C, LDO −2 %, VBUS +5 %) | **MY ARITHMETIC** over **assumed** tolerances. The ±2 % LDO figure is a guess → measurement #4. |
| Abs-max V_IO 3.6 V; light sleep ~130 µA, deep sleep ~5 µA | **VENDOR SPEC (ESP32-C3)**, quoted from recall — **verify against the Espressif datasheet before relying on the sleep numbers** |
| 1 kΩ series fails V_IL at 1.454 V; ceiling 1046 Ω; balance 549 Ω; max gain 0.33 V; fan-out N ≤ 8 | **MY ARITHMETIC** from the divider. Algebra independently checked. Not measured. |
| Clamp lowers the asymptote to 3.9 V, so rise-to-V_IH costs **2.28 τ**, not 1.20 τ | **MY ARITHMETIC** — and a correction to an earlier draft that used the 5 V-rail figure |
| 50 kHz has ≥ 2.45× margin whether or not the caps are fitted; **DIO is the binding line, not CLK** | **MY ARITHMETIC**, first-order RC; `C_wire` is **assumed**, not known |
| Ground bounce ≈ 0.96 V from 400 nH at 2.4 A/µs | **MY ARITHMETIC** over an **assumed** grid-commutation edge rate → measurement #9 |
| ~320 mA for four modules at full brightness | ⚠ **INFERENCE** from a 20–80 mA/module range that is itself an estimate → measurement #5. The USB budget argument rests on this one unmeasured number. |
| TXS needs ≥ 50 kΩ pull-ups; BSS138 C_oss ~12–14 pF | **VENDOR GUIDANCE**, from recall, **unverified** |
| TM1637 has no clock stretching and no bus timeout | ⚠ **UNVERIFIED INFERENCE** — the datasheet specifies only minimum timings → measurement #8 |
| Shared CLK with a static DIO is safe | ⛔ **STRONG INFERENCE, NOT A GUARANTEE.** Only `EV-08` can falsify it. |
| SuperMini `5V` is VBUS-direct, LDO not in path | **NEEDS A METER** — clone-dependent (§5 step 2) |
| Header pin order CLK/DIO/VCC/GND | ⛔ **NEEDS YOUR EYES** — varies by clone, and getting it wrong is destructive |
| **Segment map** (SEG1..8 = a,b,c,d,e,f,g,dp) | ⛔ **NEEDS YOUR EYES.** A module fact, decided by *this board's* traces. A wrong map gives **confident, well-formed, incorrect glyphs**; every write ACKs, every host test passes, and no machine signal anywhere distinguishes it. → `EV-01`, `EV-02` |
| **Digit order** (GRID1 = leftmost) | ⛔ **NEEDS YOUR EYES** — `4321` for `1234` is well-formed and wrong. → `EV-03` |
| **Colon vs. per-digit dp** (clock or non-clock variant) | ⛔ **NEEDS YOUR EYES** — `[CXC-TM1637-PRE-MODULE-VARIANT]`, still unanswered. → `EV-05` |

**When you look at the tubes, answer about specific characters, never "does it look right".**
`0` must light six outer bars with the centre bar **dark**; `1` must light the two **right-hand**
verticals only (if it lights two on the *left*, segments b/c are swapped with e/f — a one-line fix, but
only if the question named the bars). `42` on four digits must show **two blanks then 4 then 2** — not
`42  ` and not `0042`.
