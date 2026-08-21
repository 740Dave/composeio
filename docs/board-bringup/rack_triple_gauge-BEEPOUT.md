<!-- BEGIN BEEPOUT-PROVENANCE (composeio.maker.beepout) -->
{
  "board": "rack_triple_gauge",
  "derived_from": "/Users/davidleeburnett/Dropbox/Maker/pcb/rack_triple_gauge/rack_triple_gauge.kicad_pcb",
  "derived_from_sha256": "a62cd7c777233ba9975a1df7b8d3b1ee02ccdeb3e67256a69ce6e6fdb234a007",
  "footprints": 38,
  "kicad_version": "20241229",
  "nets": 53,
  "pads": 170,
  "rows_by_category": {
    "adjacent-pad-isolation": 106,
    "net-continuity": 50,
    "net-untestable": 2,
    "part-orientation": 7,
    "rail-isolation": 10
  },
  "rows_by_state": {
    "BLOCKED": 2,
    "DERIVED": 166,
    "UNVERIFIED": 7
  },
  "rows_total": 175,
  "schema": "composeio.beepout/1"
}
<!-- END BEEPOUT-PROVENANCE -->

# `rack_triple_gauge` — BEEP-OUT SHEET (bare board)

> **Do this with a multimeter on a BARE, UNPOPULATED board — before any part is
> soldered, before any module is seated, and before power is ever applied.**
>
> Bare is the only time some of these faults are visible. Once parts are on, a
> **fab defect and a solder defect are indistinguishable**, and some of what is
> checked here (an open between two pads) reads *identically to a good board*
> from the MCU's side forever after.

---

## Provenance — what this sheet is true of

⛔ **Every probe row below was DERIVED FROM THE FABRICATED BOARD FILE, not from
`generate_pcb.py`.** A hand-typed pad pair is a *claim*; a netlist-derived one is
a *measurement*. That distinction is the entire value of this document.

| | |
|---|---|
| derived-from | `/Users/davidleeburnett/Dropbox/Maker/pcb/rack_triple_gauge/rack_triple_gauge.kicad_pcb` |
| derived-from-sha256 | `a62cd7c777233ba9975a1df7b8d3b1ee02ccdeb3e67256a69ce6e6fdb234a007` |
| kicad-version | `20241229` (generator 9.0) |
| generated-by | `composeio.maker.beepout` |
| generated-on | 2026-08-15 |
| nets / footprints / pads | 53 / 38 / 170 |

**Re-check that this sheet still describes the board in your hand:**

```bash
shasum -a 256 /Users/davidleeburnett/Dropbox/Maker/pcb/rack_triple_gauge/rack_triple_gauge.kicad_pcb
# must print: a62cd7c777233ba9975a1df7b8d3b1ee02ccdeb3e67256a69ce6e6fdb234a007
```

If it differs, the board was revised and **this sheet is stale** — regenerate it.

### Row counts

| category | rows |
|---|---|
| rail-isolation | 10 |
| part-orientation | 7 |
| adjacent-pad-isolation | 106 |
| net-continuity | 50 |
| net-untestable | 2 |
| **total** | **175** |

| derivation state | rows | meaning |
|---|---|---|
| `DERIVED` | 166 | follows from the netlist/geometry; mutate the source and the row changes |
| `UNVERIFIED` | 7 | a REAL check the board file cannot settle — **emitted, never omitted** |
| `BLOCKED` | 2 | cannot even be stated from this source; that itself is a finding |

---

## The ladder — why this sheet comes first

> **Prove POWER AT THE PART, then the SUBSTRATE, then the PROTOCOL.**
> — `[CXC-VISION-EPISTEMICS]` `rule-power-then-substrate-then-protocol`

That rule was paid for when three rounds of protocol diagnostics ran against a
transceiver that had no supply. Every round produced clean, confident, entirely
uninformative output — the part could not have answered, and nothing in the
diagnostics could say so.

**A meter and a diagnostic firmware answer different questions and neither can
answer the other's:**

| | the meter (this sheet) | a diagnostic firmware |
|---|---|---|
| question | **what *is* this board?** | **what can the MCU *reach*?** |
| power | **OFF** | ON |
| finds | shorts, opens, bridges, swapped pins, wrong/reversed parts | marginal logic levels, intermittent writes, dead output drivers |
| cannot see | anything intermittent or rate-dependent | **a short** (it would be destroyed by one) and **an open** (which reads identically to a good trace) |

⛔ **Flashing firmware into a board with a rail-to-rail short is how you lose the
MCU.** The isolation block below is first for that reason.

---

## Meter functions used here

Say the function out loud each time. The failure mode of a document like this is
reading a resistance with the meter in DC volts and believing it.

| function | usually marked | used for |
|---|---|---|
| **resistance, 2 MΩ** | Ω, 2M | rail-to-rail isolation. Read the **settled** value |
| **continuity / beep** | ●))) | net continuity and adjacent-pad bridges |
| **diode mode** | ▶⊦ | part orientation and transistor pin identification |

> ⛔ **THE TRAP THAT MAKES A GOOD BOARD LOOK SHORTED.** In continuity/beep mode
> an **uncharged 100 µF capacitor BEEPS** — the meter's test current charges it,
> the voltage starts near zero, and the beeper fires for a fraction of a second
> before going quiet. That is a *correct* reading of a *good* board.
> **For every rail-to-rail check, use resistance (2 MΩ), watch the number climb,
> and read the SETTLED value.** Above ~100 kΩ is fine, whatever it did in the
> first half second.

---

## Board-wide notes

- ⛔ 28 footprint(s) carry a Reference marked `(hide yes)` — their designator is NOT printed on the silkscreen, so those probe points cannot be found by label. Locate them by position against the board render. Affected: 4.7kR, C1, C2, C3, C6, C7, D1, Gauge1, J_ESP_L, J_ESP_R, J_EXP_ESP, J_EXP_MCP, J_LED1, J_LED2, J_LED3, J_MOT1…
- This board's BOM includes bulk electrolytic(s) (`CP_` footprint). On a TRULY BARE board they are not fitted and the rails read hard-open; if this sheet is reused on a STUFFED board, an uncharged 100 µF beeps and the 2 MΩ settled reading is the only trustworthy one.
- Geometry self-check: 130/130 netted pads lie within 0.6 mm of a copper endpoint on the same net (100%). This is an independent check on this tool's own pad-position arithmetic — copper endpoints are written by a different code path than pad positions, so agreement is evidence that does not share the parser's assumption.

---

## §1 — RAIL ISOLATION

**Power has never been applied. Nothing is seated.** Meter in **resistance, 2 MΩ**. ⛔ **NOT continuity.** These are the checks whose failure destroys parts on power-up, so they are first.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | +12V @ J_PWR_TAP pin 1 [no silk label] | +3V3 @ J_PWR_TAP pin 3 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V @ J_PWR_TAP pin 1 [no silk label] | +5V @ J_PWR_TAP pin 2 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V_IN @ J_PWR pin 1 | +12V @ J_PWR_TAP pin 1 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V_IN @ J_PWR pin 1 | +3V3 @ J_PWR_TAP pin 3 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V_IN @ J_PWR pin 1 | +5V @ J_PWR_TAP pin 2 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +5V @ J_PWR_TAP pin 2 [no silk label] | +3V3 @ J_PWR_TAP pin 3 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ J_PWR pin 2 | +12V @ J_PWR_TAP pin 1 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ J_PWR pin 2 | +12V_IN @ J_PWR pin 1 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ J_PWR pin 2 | +3V3 @ J_PWR_TAP pin 3 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ J_PWR pin 2 | +5V @ J_PWR_TAP pin 2 [no silk label] | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **+12V @ J_PWR_TAP pin 1 [no silk label] ↔ +3V3 @ J_PWR_TAP pin 3 [no silk label]** — `+12V` (net 3) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V @ J_PWR_TAP pin 1 [no silk label] ↔ +5V @ J_PWR_TAP pin 2 [no silk label]** — `+12V` (net 3) and `+5V` (net 4) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `LM7805` (LM7805).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V and +5V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V_IN @ J_PWR pin 1 ↔ +12V @ J_PWR_TAP pin 1 [no silk label]** — `+12V_IN` (net 2) and `+12V` (net 3) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `D1` (1N5401).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V_IN and +12V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V_IN @ J_PWR pin 1 ↔ +3V3 @ J_PWR_TAP pin 3 [no silk label]** — `+12V_IN` (net 2) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V_IN and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V_IN @ J_PWR pin 1 ↔ +5V @ J_PWR_TAP pin 2 [no silk label]** — `+12V_IN` (net 2) and `+5V` (net 4) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V_IN and +5V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+5V @ J_PWR_TAP pin 2 [no silk label] ↔ +3V3 @ J_PWR_TAP pin 3 [no silk label]** — `+5V` (net 4) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +5V and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ J_PWR pin 2 ↔ +12V @ J_PWR_TAP pin 1 [no silk label]** — `GND` (net 1) and `+12V` (net 3) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `LM7805` (LM7805).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +12V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ J_PWR pin 2 ↔ +12V_IN @ J_PWR pin 1** — `GND` (net 1) and `+12V_IN` (net 2) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +12V_IN. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ J_PWR pin 2 ↔ +3V3 @ J_PWR_TAP pin 3 [no silk label]** — `GND` (net 1) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ J_PWR pin 2 ↔ +5V @ J_PWR_TAP pin 2 [no silk label]** — `GND` (net 1) and `+5V` (net 4) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `LM7805` (LM7805).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +5V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.

</details>

---

## §2 — PART / FOOTPRINT ASSUMPTIONS

⛔ **`UNVERIFIED` BY CONSTRUCTION.** The footprint encodes an assumption about a part the board file cannot see. Check the LOOSE PART before soldering. These are emitted rather than omitted precisely because an absent check is indistinguishable from one nobody thought of.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C1 pin 1 [no silk label] — C1 | loose part (100uF_25V) | resistance, 2 MΩ | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | C3 pin 1 [no silk label] — C3 | loose part (100uF_16V) | resistance, 2 MΩ | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | D1 pin 1 [no silk label] — D1 | loose part (1N5401) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | LM7805 pin 1 — LM7805 | loose part (LM7805) | continuity / beep | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q_LED1 pin 1 [no silk label] — Q_LED1 | loose part (3 x 2N2222 (12v PWM)) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q_LED2 pin 1 [no silk label] — Q_LED2 | loose part (2N2222) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q_LED3 pin 1 [no silk label] — Q_LED3 | loose part (2N2222) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **C1 pin 1 [no silk label] — C1 ↔ loose part (100uF_25V)** — `C1` uses `CP_Radial_D5.0mm_P2.5mm`, which ENCODES a electrolytic polarity assumption. The board file records which net each pad carries (pad 1->`+12V`, pad 2->`GND`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ POLARISED PART. Confirm the NEGATIVE STRIPE faces the pad on the ground/return net before soldering. A reversed electrolytic on a 12 V rail vents, and it is the most likely single cause of a rail-to-rail short in the block above.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **C3 pin 1 [no silk label] — C3 ↔ loose part (100uF_16V)** — `C3` uses `CP_Radial_D5.0mm_P2.5mm`, which ENCODES a electrolytic polarity assumption. The board file records which net each pad carries (pad 1->`+5V`, pad 2->`GND`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ POLARISED PART. Confirm the NEGATIVE STRIPE faces the pad on the ground/return net before soldering. A reversed electrolytic on a 12 V rail vents, and it is the most likely single cause of a rail-to-rail short in the block above.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **D1 pin 1 [no silk label] — D1 ↔ loose part (1N5401)** — `D1` uses `D_DO-41_SOD81_P10.16mm_Horizontal`, which ENCODES a diode orientation assumption. The board file records which net each pad carries (pad 1->`+12V_IN`, pad 2->`+12V`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ ORIENTATION MATTERS AND THE SILKSCREEN MAY BE DRAWN ROTATED. Read the CATHODE BAND on the part, not the outline. Forward should read 0.4–0.8 V one way and OL reversed. A reverse-protection diode fitted backwards means the board never powers up; fitted shorted means it never protects.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **LM7805 pin 1 — LM7805 ↔ loose part (LM7805)** — `LM7805` uses `TO-220-3_Vertical`, which ENCODES a regulator pinout assumption. The board file records which net each pad carries (pad 1->`+12V`, pad 2->`GND`, pad 3->`+5V`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ CONFIRM THE PART'S PINOUT AGAINST THE FOOTPRINT. A linear regulator fitted backwards puts its input voltage onto the output rail through the substrate diode, destroying everything downstream. Note the TAB is usually tied to one of the pins — check what it touches.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q_LED1 pin 1 [no silk label] — Q_LED1 ↔ loose part (3 x 2N2222 (12v PWM))** — `Q_LED1` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`LED1_BASE`, pad 3->`LED1_COL`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q_LED2 pin 1 [no silk label] — Q_LED2 ↔ loose part (2N2222)** — `Q_LED2` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`LED2_BASE`, pad 3->`LED2_COL`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q_LED3 pin 1 [no silk label] — Q_LED3 ↔ loose part (2N2222)** — `Q_LED3` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`LED3_BASE`, pad 3->`LED3_COL`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.

</details>

---

## §3 — ADJACENT-PAD ISOLATION

Meter in **continuity**. Adjacency is derived from each footprint's OWN minimum pad spacing — not from a guess about which parts are fine-pitch. Every pair below carries **different nets** and must **not** beep.

#### `2x100nF`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 2x100nF pin 1 (`+5V`) | 2x100nF pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `3x100nF`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 3x100nF pin 1 (`+5V`) | 3x100nF pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `C1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C1 pin 1 [no silk label] (`+12V`) | C1 pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `C2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C2 pin 1 [no silk label] (`+12V`) | C2 pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `C3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C3 pin 1 [no silk label] (`+5V`) | C3 pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `C6`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C6 pin 1 [no silk label] (`+5V`) | C6 pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `C7`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C7 pin 1 [no silk label] (`+5V`) | C7 pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Gauge1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Gauge1 pin 1 [no silk label] (`+5V`) | Gauge1 pin 2 [no silk label] (`M1_IN1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 10 [no silk label] (`M1_IN3`) | Gauge1 pin 11 [no silk label] (`MOT1_C`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 11 [no silk label] (`MOT1_C`) | Gauge1 pin 12 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 13 [no silk label] (`GND`) | Gauge1 pin 14 [no silk label] (`MOT1_D`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 14 [no silk label] (`MOT1_D`) | Gauge1 pin 15 [no silk label] (`M1_IN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 15 [no silk label] (`M1_IN4`) | Gauge1 pin 16 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 2 [no silk label] (`M1_IN1`) | Gauge1 pin 3 [no silk label] (`MOT1_A`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 3 [no silk label] (`MOT1_A`) | Gauge1 pin 4 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 5 [no silk label] (`GND`) | Gauge1 pin 6 [no silk label] (`MOT1_B`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 6 [no silk label] (`MOT1_B`) | Gauge1 pin 7 [no silk label] (`M1_IN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 7 [no silk label] (`M1_IN2`) | Gauge1 pin 8 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Gauge1 pin 9 [no silk label] (`+5V`) | Gauge1 pin 10 [no silk label] (`M1_IN3`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_ESP_L`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_ESP_L pin 1 [no silk label] (`LED1_GPIO`) | J_ESP_L pin 2 [no silk label] (`LED2_GPIO`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_L pin 2 [no silk label] (`LED2_GPIO`) | J_ESP_L pin 3 [no silk label] (`GPIO2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_L pin 3 [no silk label] (`GPIO2`) | J_ESP_L pin 4 [no silk label] (`LED3_GPIO`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_L pin 4 [no silk label] (`LED3_GPIO`) | J_ESP_L pin 5 [no silk label] (`SDA`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_L pin 5 [no silk label] (`SDA`) | J_ESP_L pin 6 [no silk label] (`+3V3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_L pin 6 [no silk label] (`+3V3`) | J_ESP_L pin 7 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_L pin 7 [no silk label] (`GND`) | J_ESP_L pin 8 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_ESP_R`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_ESP_R pin 1 [no silk label] (`GPIO21`) | J_ESP_R pin 2 [no silk label] (`GPIO20`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_R pin 2 [no silk label] (`GPIO20`) | J_ESP_R pin 3 [no silk label] (`GPIO10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_R pin 3 [no silk label] (`GPIO10`) | J_ESP_R pin 4 [no silk label] (`GPIO9`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_R pin 4 [no silk label] (`GPIO9`) | J_ESP_R pin 5 [no silk label] (`GPIO8`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_R pin 5 [no silk label] (`GPIO8`) | J_ESP_R pin 6 [no silk label] (`GPIO7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_R pin 6 [no silk label] (`GPIO7`) | J_ESP_R pin 7 [no silk label] (`GPIO6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_ESP_R pin 7 [no silk label] (`GPIO6`) | J_ESP_R pin 8 [no silk label] (`SCL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_EXP_ESP`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_EXP_ESP pin 1 [no silk label] (`GPIO2`) | J_EXP_ESP pin 2 [no silk label] (`GPIO6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_EXP_ESP pin 2 [no silk label] (`GPIO6`) | J_EXP_ESP pin 3 [no silk label] (`GPIO7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_EXP_ESP pin 3 [no silk label] (`GPIO7`) | J_EXP_ESP pin 4 [no silk label] (`GPIO9`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_EXP_ESP pin 4 [no silk label] (`GPIO9`) | J_EXP_ESP pin 5 [no silk label] (`GPIO10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_EXP_ESP pin 5 [no silk label] (`GPIO10`) | J_EXP_ESP pin 6 [no silk label] (`GPIO20`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_EXP_MCP`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_EXP_MCP pin 1 [no silk label] (`GPB4`) | J_EXP_MCP pin 2 [no silk label] (`GPB5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_EXP_MCP pin 2 [no silk label] (`GPB5`) | J_EXP_MCP pin 3 [no silk label] (`GPB6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_EXP_MCP pin 3 [no silk label] (`GPB6`) | J_EXP_MCP pin 4 [no silk label] (`GPB7`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_LED1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_LED1 pin 1 [no silk label] (`+12V`) | J_LED1 pin 2 [no silk label] (`LED1_COL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_LED2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_LED2 pin 1 [no silk label] (`+12V`) | J_LED2 pin 2 [no silk label] (`LED2_COL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_LED3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_LED3 pin 1 [no silk label] (`+12V`) | J_LED3 pin 2 [no silk label] (`LED3_COL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_MOT1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_MOT1 pin 1 [no silk label] (`MOT1_A`) | J_MOT1 pin 2 [no silk label] (`MOT1_B`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_MOT1 pin 2 [no silk label] (`MOT1_B`) | J_MOT1 pin 3 [no silk label] (`MOT1_C`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_MOT1 pin 3 [no silk label] (`MOT1_C`) | J_MOT1 pin 4 [no silk label] (`MOT1_D`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_MOT2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_MOT2 pin 1 [no silk label] (`MOT2_A`) | J_MOT2 pin 2 [no silk label] (`MOT2_B`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_MOT2 pin 2 [no silk label] (`MOT2_B`) | J_MOT2 pin 3 [no silk label] (`MOT2_C`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_MOT2 pin 3 [no silk label] (`MOT2_C`) | J_MOT2 pin 4 [no silk label] (`MOT2_D`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_MOT3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_MOT3 pin 1 [no silk label] (`MOT3_A`) | J_MOT3 pin 2 [no silk label] (`MOT3_B`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_MOT3 pin 2 [no silk label] (`MOT3_B`) | J_MOT3 pin 3 [no silk label] (`MOT3_C`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_MOT3 pin 3 [no silk label] (`MOT3_C`) | J_MOT3 pin 4 [no silk label] (`MOT3_D`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_PWR_TAP`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_PWR_TAP pin 1 [no silk label] (`+12V`) | J_PWR_TAP pin 2 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_PWR_TAP pin 2 [no silk label] (`+5V`) | J_PWR_TAP pin 3 [no silk label] (`+3V3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_PWR_TAP pin 3 [no silk label] (`+3V3`) | J_PWR_TAP pin 4 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `LM7805`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | LM7805 pin 1 (`+12V`) | LM7805 pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | LM7805 pin 2 (`GND`) | LM7805 pin 3 (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `MCP23017 Top`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | MCP23017 Top pin 1 (`M3_IN1`) | MCP23017 Top pin 2 (`M3_IN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 12 (`SCL`) | MCP23017 Top pin 13 (`SDA`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 17 (`GND`) | MCP23017 Top pin 18 (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 2 (`M3_IN2`) | MCP23017 Top pin 3 (`M3_IN3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 21 (`M1_IN1`) | MCP23017 Top pin 22 (`M1_IN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 22 (`M1_IN2`) | MCP23017 Top pin 23 (`M1_IN3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 23 (`M1_IN3`) | MCP23017 Top pin 24 (`M1_IN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 24 (`M1_IN4`) | MCP23017 Top pin 25 (`M2_IN1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 25 (`M2_IN1`) | MCP23017 Top pin 26 (`M2_IN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 26 (`M2_IN2`) | MCP23017 Top pin 27 (`M2_IN3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 27 (`M2_IN3`) | MCP23017 Top pin 28 (`M2_IN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 3 (`M3_IN3`) | MCP23017 Top pin 4 (`M3_IN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 4 (`M3_IN4`) | MCP23017 Top pin 5 (`GPB4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 5 (`GPB4`) | MCP23017 Top pin 6 (`GPB5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 6 (`GPB5`) | MCP23017 Top pin 7 (`GPB6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 7 (`GPB6`) | MCP23017 Top pin 8 (`GPB7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 8 (`GPB7`) | MCP23017 Top pin 9 (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top pin 9 (`+5V`) | MCP23017 Top pin 10 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q_LED1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q_LED1 pin 1 [no silk label] (`GND`) | Q_LED1 pin 2 [no silk label] (`LED1_BASE`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q_LED1 pin 2 [no silk label] (`LED1_BASE`) | Q_LED1 pin 3 [no silk label] (`LED1_COL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q_LED2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q_LED2 pin 1 [no silk label] (`GND`) | Q_LED2 pin 2 [no silk label] (`LED2_BASE`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q_LED2 pin 2 [no silk label] (`LED2_BASE`) | Q_LED2 pin 3 [no silk label] (`LED2_COL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q_LED3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q_LED3 pin 1 [no silk label] (`GND`) | Q_LED3 pin 2 [no silk label] (`LED3_BASE`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q_LED3 pin 2 [no silk label] (`LED3_BASE`) | Q_LED3 pin 3 [no silk label] (`LED3_COL`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `U3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | U3 pin 1 [no silk label] (`+5V`) | U3 pin 2 [no silk label] (`M2_IN1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 10 [no silk label] (`M2_IN3`) | U3 pin 11 [no silk label] (`MOT2_C`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 11 [no silk label] (`MOT2_C`) | U3 pin 12 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 13 [no silk label] (`GND`) | U3 pin 14 [no silk label] (`MOT2_D`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 14 [no silk label] (`MOT2_D`) | U3 pin 15 [no silk label] (`M2_IN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 15 [no silk label] (`M2_IN4`) | U3 pin 16 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 2 [no silk label] (`M2_IN1`) | U3 pin 3 [no silk label] (`MOT2_A`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 3 [no silk label] (`MOT2_A`) | U3 pin 4 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 5 [no silk label] (`GND`) | U3 pin 6 [no silk label] (`MOT2_B`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 6 [no silk label] (`MOT2_B`) | U3 pin 7 [no silk label] (`M2_IN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 7 [no silk label] (`M2_IN2`) | U3 pin 8 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U3 pin 9 [no silk label] (`+5V`) | U3 pin 10 [no silk label] (`M2_IN3`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `U4`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | U4 pin 1 [no silk label] (`+5V`) | U4 pin 2 [no silk label] (`M3_IN1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 10 [no silk label] (`M3_IN3`) | U4 pin 11 [no silk label] (`MOT3_C`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 11 [no silk label] (`MOT3_C`) | U4 pin 12 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 13 [no silk label] (`GND`) | U4 pin 14 [no silk label] (`MOT3_D`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 14 [no silk label] (`MOT3_D`) | U4 pin 15 [no silk label] (`M3_IN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 15 [no silk label] (`M3_IN4`) | U4 pin 16 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 2 [no silk label] (`M3_IN1`) | U4 pin 3 [no silk label] (`MOT3_A`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 3 [no silk label] (`MOT3_A`) | U4 pin 4 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 5 [no silk label] (`GND`) | U4 pin 6 [no silk label] (`MOT3_B`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 6 [no silk label] (`MOT3_B`) | U4 pin 7 [no silk label] (`M3_IN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 7 [no silk label] (`M3_IN2`) | U4 pin 8 [no silk label] (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | U4 pin 9 [no silk label] (`+5V`) | U4 pin 10 [no silk label] (`M3_IN3`) | continuity / beep | **NO BEEP (open)** | ____________ |


<details><summary>Why each row exists, and what a failure means</summary>

- **2x100nF pin 1 (`+5V`) ↔ 2x100nF pin 2 (`GND`)** — Neighbouring pads on `2x100nF` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **3x100nF pin 1 (`+5V`) ↔ 3x100nF pin 2 (`GND`)** — Neighbouring pads on `3x100nF` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **C1 pin 1 [no silk label] (`+12V`) ↔ C1 pin 2 [no silk label] (`GND`)** — Neighbouring pads on `C1` (CP_Radial_D5.0mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **C2 pin 1 [no silk label] (`+12V`) ↔ C2 pin 2 [no silk label] (`GND`)** — Neighbouring pads on `C2` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **C3 pin 1 [no silk label] (`+5V`) ↔ C3 pin 2 [no silk label] (`GND`)** — Neighbouring pads on `C3` (CP_Radial_D5.0mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **C6 pin 1 [no silk label] (`+5V`) ↔ C6 pin 2 [no silk label] (`GND`)** — Neighbouring pads on `C6` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **C7 pin 1 [no silk label] (`+5V`) ↔ C7 pin 2 [no silk label] (`GND`)** — Neighbouring pads on `C7` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 1 [no silk label] (`+5V`) ↔ Gauge1 pin 2 [no silk label] (`M1_IN1`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `M1_IN1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 10 [no silk label] (`M1_IN3`) ↔ Gauge1 pin 11 [no silk label] (`MOT1_C`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN3` vs `MOT1_C`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 11 [no silk label] (`MOT1_C`) ↔ Gauge1 pin 12 [no silk label] (`GND`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT1_C` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 13 [no silk label] (`GND`) ↔ Gauge1 pin 14 [no silk label] (`MOT1_D`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `MOT1_D`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 14 [no silk label] (`MOT1_D`) ↔ Gauge1 pin 15 [no silk label] (`M1_IN4`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT1_D` vs `M1_IN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 15 [no silk label] (`M1_IN4`) ↔ Gauge1 pin 16 [no silk label] (`+5V`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN4` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 2 [no silk label] (`M1_IN1`) ↔ Gauge1 pin 3 [no silk label] (`MOT1_A`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN1` vs `MOT1_A`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 3 [no silk label] (`MOT1_A`) ↔ Gauge1 pin 4 [no silk label] (`GND`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT1_A` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 5 [no silk label] (`GND`) ↔ Gauge1 pin 6 [no silk label] (`MOT1_B`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `MOT1_B`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 6 [no silk label] (`MOT1_B`) ↔ Gauge1 pin 7 [no silk label] (`M1_IN2`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT1_B` vs `M1_IN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 7 [no silk label] (`M1_IN2`) ↔ Gauge1 pin 8 [no silk label] (`+5V`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN2` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Gauge1 pin 9 [no silk label] (`+5V`) ↔ Gauge1 pin 10 [no silk label] (`M1_IN3`)** — Neighbouring pads on `Gauge1` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `M1_IN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 1 [no silk label] (`LED1_GPIO`) ↔ J_ESP_L pin 2 [no silk label] (`LED2_GPIO`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LED1_GPIO` vs `LED2_GPIO`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 2 [no silk label] (`LED2_GPIO`) ↔ J_ESP_L pin 3 [no silk label] (`GPIO2`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LED2_GPIO` vs `GPIO2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 3 [no silk label] (`GPIO2`) ↔ J_ESP_L pin 4 [no silk label] (`LED3_GPIO`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO2` vs `LED3_GPIO`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 4 [no silk label] (`LED3_GPIO`) ↔ J_ESP_L pin 5 [no silk label] (`SDA`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LED3_GPIO` vs `SDA`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 5 [no silk label] (`SDA`) ↔ J_ESP_L pin 6 [no silk label] (`+3V3`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`SDA` vs `+3V3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 6 [no silk label] (`+3V3`) ↔ J_ESP_L pin 7 [no silk label] (`GND`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+3V3` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_L pin 7 [no silk label] (`GND`) ↔ J_ESP_L pin 8 [no silk label] (`+5V`)** — Neighbouring pads on `J_ESP_L` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 1 [no silk label] (`GPIO21`) ↔ J_ESP_R pin 2 [no silk label] (`GPIO20`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO21` vs `GPIO20`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 2 [no silk label] (`GPIO20`) ↔ J_ESP_R pin 3 [no silk label] (`GPIO10`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO20` vs `GPIO10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 3 [no silk label] (`GPIO10`) ↔ J_ESP_R pin 4 [no silk label] (`GPIO9`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO10` vs `GPIO9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 4 [no silk label] (`GPIO9`) ↔ J_ESP_R pin 5 [no silk label] (`GPIO8`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO9` vs `GPIO8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 5 [no silk label] (`GPIO8`) ↔ J_ESP_R pin 6 [no silk label] (`GPIO7`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO8` vs `GPIO7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 6 [no silk label] (`GPIO7`) ↔ J_ESP_R pin 7 [no silk label] (`GPIO6`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO7` vs `GPIO6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_ESP_R pin 7 [no silk label] (`GPIO6`) ↔ J_ESP_R pin 8 [no silk label] (`SCL`)** — Neighbouring pads on `J_ESP_R` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO6` vs `SCL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_ESP pin 1 [no silk label] (`GPIO2`) ↔ J_EXP_ESP pin 2 [no silk label] (`GPIO6`)** — Neighbouring pads on `J_EXP_ESP` (PinHeader_1x06_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO2` vs `GPIO6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_ESP pin 2 [no silk label] (`GPIO6`) ↔ J_EXP_ESP pin 3 [no silk label] (`GPIO7`)** — Neighbouring pads on `J_EXP_ESP` (PinHeader_1x06_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO6` vs `GPIO7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_ESP pin 3 [no silk label] (`GPIO7`) ↔ J_EXP_ESP pin 4 [no silk label] (`GPIO9`)** — Neighbouring pads on `J_EXP_ESP` (PinHeader_1x06_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO7` vs `GPIO9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_ESP pin 4 [no silk label] (`GPIO9`) ↔ J_EXP_ESP pin 5 [no silk label] (`GPIO10`)** — Neighbouring pads on `J_EXP_ESP` (PinHeader_1x06_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO9` vs `GPIO10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_ESP pin 5 [no silk label] (`GPIO10`) ↔ J_EXP_ESP pin 6 [no silk label] (`GPIO20`)** — Neighbouring pads on `J_EXP_ESP` (PinHeader_1x06_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO10` vs `GPIO20`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_MCP pin 1 [no silk label] (`GPB4`) ↔ J_EXP_MCP pin 2 [no silk label] (`GPB5`)** — Neighbouring pads on `J_EXP_MCP` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB4` vs `GPB5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_MCP pin 2 [no silk label] (`GPB5`) ↔ J_EXP_MCP pin 3 [no silk label] (`GPB6`)** — Neighbouring pads on `J_EXP_MCP` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB5` vs `GPB6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_EXP_MCP pin 3 [no silk label] (`GPB6`) ↔ J_EXP_MCP pin 4 [no silk label] (`GPB7`)** — Neighbouring pads on `J_EXP_MCP` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB6` vs `GPB7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_LED1 pin 1 [no silk label] (`+12V`) ↔ J_LED1 pin 2 [no silk label] (`LED1_COL`)** — Neighbouring pads on `J_LED1` (JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `LED1_COL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_LED2 pin 1 [no silk label] (`+12V`) ↔ J_LED2 pin 2 [no silk label] (`LED2_COL`)** — Neighbouring pads on `J_LED2` (JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `LED2_COL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_LED3 pin 1 [no silk label] (`+12V`) ↔ J_LED3 pin 2 [no silk label] (`LED3_COL`)** — Neighbouring pads on `J_LED3` (JST_XH_B2B-XH-A_1x02_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `LED3_COL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT1 pin 1 [no silk label] (`MOT1_A`) ↔ J_MOT1 pin 2 [no silk label] (`MOT1_B`)** — Neighbouring pads on `J_MOT1` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT1_A` vs `MOT1_B`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT1 pin 2 [no silk label] (`MOT1_B`) ↔ J_MOT1 pin 3 [no silk label] (`MOT1_C`)** — Neighbouring pads on `J_MOT1` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT1_B` vs `MOT1_C`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT1 pin 3 [no silk label] (`MOT1_C`) ↔ J_MOT1 pin 4 [no silk label] (`MOT1_D`)** — Neighbouring pads on `J_MOT1` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT1_C` vs `MOT1_D`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT2 pin 1 [no silk label] (`MOT2_A`) ↔ J_MOT2 pin 2 [no silk label] (`MOT2_B`)** — Neighbouring pads on `J_MOT2` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT2_A` vs `MOT2_B`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT2 pin 2 [no silk label] (`MOT2_B`) ↔ J_MOT2 pin 3 [no silk label] (`MOT2_C`)** — Neighbouring pads on `J_MOT2` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT2_B` vs `MOT2_C`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT2 pin 3 [no silk label] (`MOT2_C`) ↔ J_MOT2 pin 4 [no silk label] (`MOT2_D`)** — Neighbouring pads on `J_MOT2` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT2_C` vs `MOT2_D`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT3 pin 1 [no silk label] (`MOT3_A`) ↔ J_MOT3 pin 2 [no silk label] (`MOT3_B`)** — Neighbouring pads on `J_MOT3` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT3_A` vs `MOT3_B`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT3 pin 2 [no silk label] (`MOT3_B`) ↔ J_MOT3 pin 3 [no silk label] (`MOT3_C`)** — Neighbouring pads on `J_MOT3` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT3_B` vs `MOT3_C`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_MOT3 pin 3 [no silk label] (`MOT3_C`) ↔ J_MOT3 pin 4 [no silk label] (`MOT3_D`)** — Neighbouring pads on `J_MOT3` (JST_XH_B4B-XH-A_1x04_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`MOT3_C` vs `MOT3_D`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_PWR_TAP pin 1 [no silk label] (`+12V`) ↔ J_PWR_TAP pin 2 [no silk label] (`+5V`)** — Neighbouring pads on `J_PWR_TAP` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+12V` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_PWR_TAP pin 2 [no silk label] (`+5V`) ↔ J_PWR_TAP pin 3 [no silk label] (`+3V3`)** — Neighbouring pads on `J_PWR_TAP` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `+3V3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_PWR_TAP pin 3 [no silk label] (`+3V3`) ↔ J_PWR_TAP pin 4 [no silk label] (`GND`)** — Neighbouring pads on `J_PWR_TAP` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+3V3` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **LM7805 pin 1 (`+12V`) ↔ LM7805 pin 2 (`GND`)** — Neighbouring pads on `LM7805` (TO-220-3_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+12V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **LM7805 pin 2 (`GND`) ↔ LM7805 pin 3 (`+5V`)** — Neighbouring pads on `LM7805` (TO-220-3_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 1 (`M3_IN1`) ↔ MCP23017 Top pin 2 (`M3_IN2`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN1` vs `M3_IN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 12 (`SCL`) ↔ MCP23017 Top pin 13 (`SDA`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`SCL` vs `SDA`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 17 (`GND`) ↔ MCP23017 Top pin 18 (`+5V`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 2 (`M3_IN2`) ↔ MCP23017 Top pin 3 (`M3_IN3`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN2` vs `M3_IN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 21 (`M1_IN1`) ↔ MCP23017 Top pin 22 (`M1_IN2`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN1` vs `M1_IN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 22 (`M1_IN2`) ↔ MCP23017 Top pin 23 (`M1_IN3`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN2` vs `M1_IN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 23 (`M1_IN3`) ↔ MCP23017 Top pin 24 (`M1_IN4`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN3` vs `M1_IN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 24 (`M1_IN4`) ↔ MCP23017 Top pin 25 (`M2_IN1`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M1_IN4` vs `M2_IN1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 25 (`M2_IN1`) ↔ MCP23017 Top pin 26 (`M2_IN2`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN1` vs `M2_IN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 26 (`M2_IN2`) ↔ MCP23017 Top pin 27 (`M2_IN3`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN2` vs `M2_IN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 27 (`M2_IN3`) ↔ MCP23017 Top pin 28 (`M2_IN4`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN3` vs `M2_IN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 3 (`M3_IN3`) ↔ MCP23017 Top pin 4 (`M3_IN4`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN3` vs `M3_IN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 4 (`M3_IN4`) ↔ MCP23017 Top pin 5 (`GPB4`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN4` vs `GPB4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 5 (`GPB4`) ↔ MCP23017 Top pin 6 (`GPB5`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB4` vs `GPB5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 6 (`GPB5`) ↔ MCP23017 Top pin 7 (`GPB6`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB5` vs `GPB6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 7 (`GPB6`) ↔ MCP23017 Top pin 8 (`GPB7`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB6` vs `GPB7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 8 (`GPB7`) ↔ MCP23017 Top pin 9 (`+5V`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPB7` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top pin 9 (`+5V`) ↔ MCP23017 Top pin 10 (`GND`)** — Neighbouring pads on `MCP23017 Top` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q_LED1 pin 1 [no silk label] (`GND`) ↔ Q_LED1 pin 2 [no silk label] (`LED1_BASE`)** — Neighbouring pads on `Q_LED1` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `LED1_BASE`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q_LED1 pin 2 [no silk label] (`LED1_BASE`) ↔ Q_LED1 pin 3 [no silk label] (`LED1_COL`)** — Neighbouring pads on `Q_LED1` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`LED1_BASE` vs `LED1_COL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q_LED2 pin 1 [no silk label] (`GND`) ↔ Q_LED2 pin 2 [no silk label] (`LED2_BASE`)** — Neighbouring pads on `Q_LED2` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `LED2_BASE`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q_LED2 pin 2 [no silk label] (`LED2_BASE`) ↔ Q_LED2 pin 3 [no silk label] (`LED2_COL`)** — Neighbouring pads on `Q_LED2` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`LED2_BASE` vs `LED2_COL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q_LED3 pin 1 [no silk label] (`GND`) ↔ Q_LED3 pin 2 [no silk label] (`LED3_BASE`)** — Neighbouring pads on `Q_LED3` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `LED3_BASE`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q_LED3 pin 2 [no silk label] (`LED3_BASE`) ↔ Q_LED3 pin 3 [no silk label] (`LED3_COL`)** — Neighbouring pads on `Q_LED3` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`LED3_BASE` vs `LED3_COL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 1 [no silk label] (`+5V`) ↔ U3 pin 2 [no silk label] (`M2_IN1`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `M2_IN1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 10 [no silk label] (`M2_IN3`) ↔ U3 pin 11 [no silk label] (`MOT2_C`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN3` vs `MOT2_C`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 11 [no silk label] (`MOT2_C`) ↔ U3 pin 12 [no silk label] (`GND`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT2_C` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 13 [no silk label] (`GND`) ↔ U3 pin 14 [no silk label] (`MOT2_D`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `MOT2_D`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 14 [no silk label] (`MOT2_D`) ↔ U3 pin 15 [no silk label] (`M2_IN4`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT2_D` vs `M2_IN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 15 [no silk label] (`M2_IN4`) ↔ U3 pin 16 [no silk label] (`+5V`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN4` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 2 [no silk label] (`M2_IN1`) ↔ U3 pin 3 [no silk label] (`MOT2_A`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN1` vs `MOT2_A`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 3 [no silk label] (`MOT2_A`) ↔ U3 pin 4 [no silk label] (`GND`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT2_A` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 5 [no silk label] (`GND`) ↔ U3 pin 6 [no silk label] (`MOT2_B`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `MOT2_B`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 6 [no silk label] (`MOT2_B`) ↔ U3 pin 7 [no silk label] (`M2_IN2`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT2_B` vs `M2_IN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 7 [no silk label] (`M2_IN2`) ↔ U3 pin 8 [no silk label] (`+5V`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M2_IN2` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U3 pin 9 [no silk label] (`+5V`) ↔ U3 pin 10 [no silk label] (`M2_IN3`)** — Neighbouring pads on `U3` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `M2_IN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 1 [no silk label] (`+5V`) ↔ U4 pin 2 [no silk label] (`M3_IN1`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `M3_IN1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 10 [no silk label] (`M3_IN3`) ↔ U4 pin 11 [no silk label] (`MOT3_C`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN3` vs `MOT3_C`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 11 [no silk label] (`MOT3_C`) ↔ U4 pin 12 [no silk label] (`GND`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT3_C` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 13 [no silk label] (`GND`) ↔ U4 pin 14 [no silk label] (`MOT3_D`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `MOT3_D`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 14 [no silk label] (`MOT3_D`) ↔ U4 pin 15 [no silk label] (`M3_IN4`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT3_D` vs `M3_IN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 15 [no silk label] (`M3_IN4`) ↔ U4 pin 16 [no silk label] (`+5V`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN4` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 2 [no silk label] (`M3_IN1`) ↔ U4 pin 3 [no silk label] (`MOT3_A`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN1` vs `MOT3_A`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 3 [no silk label] (`MOT3_A`) ↔ U4 pin 4 [no silk label] (`GND`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT3_A` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 5 [no silk label] (`GND`) ↔ U4 pin 6 [no silk label] (`MOT3_B`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `MOT3_B`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 6 [no silk label] (`MOT3_B`) ↔ U4 pin 7 [no silk label] (`M3_IN2`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`MOT3_B` vs `M3_IN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 7 [no silk label] (`M3_IN2`) ↔ U4 pin 8 [no silk label] (`+5V`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`M3_IN2` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **U4 pin 9 [no silk label] (`+5V`) ↔ U4 pin 10 [no silk label] (`M3_IN3`)** — Neighbouring pads on `U4` (DIP-16_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+5V` vs `M3_IN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.

</details>

---

## §4 — NET CONTINUITY

Meter in **continuity**. Each pair spans the **longest run** of its net, so the check crosses the whole net rather than a short hop. ⛔ This section is the only thing in the whole bring-up that can see an OPEN — from the MCU's side an open trace and a perfect trace are indistinguishable.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Gauge1 pin 10 [no silk label] | MCP23017 Top pin 23 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Gauge1 pin 15 [no silk label] | MCP23017 Top pin 24 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Gauge1 pin 2 [no silk label] | MCP23017 Top pin 21 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Gauge1 pin 7 [no silk label] | MCP23017 Top pin 22 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_ESP_L pin 1 [no silk label] | Strip Pins 3x150R pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_ESP_L pin 2 [no silk label] | R_LED2 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_ESP_L pin 3 [no silk label] | J_EXP_ESP pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_ESP_L pin 4 [no silk label] | R_LED3 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_ESP_L pin 5 [no silk label] | MCP23017 Top pin 13 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_EXP_ESP pin 2 [no silk label] | J_ESP_R pin 7 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_EXP_ESP pin 3 [no silk label] | J_ESP_R pin 6 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_EXP_ESP pin 4 [no silk label] | J_ESP_R pin 4 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_EXP_ESP pin 5 [no silk label] | J_ESP_R pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_EXP_ESP pin 6 [no silk label] | J_ESP_R pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_LED1 pin 2 [no silk label] | Q_LED1 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_LED3 pin 2 [no silk label] | Q_LED3 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT1 pin 1 [no silk label] | Gauge1 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT1 pin 2 [no silk label] | Gauge1 pin 6 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT1 pin 3 [no silk label] | Gauge1 pin 11 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT1 pin 4 [no silk label] | Gauge1 pin 14 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT3 pin 1 [no silk label] | U4 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT3 pin 2 [no silk label] | U4 pin 6 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT3 pin 3 [no silk label] | U4 pin 11 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_MOT3 pin 4 [no silk label] | U4 pin 14 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_PWR pin 1 | D1 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J_PWR_TAP pin 3 [no silk label] | R_SDA pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | LM7805 pin 1 | J_LED3 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | LM7805 pin 2 | Q_LED3 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | LM7805 pin 3 | U4 pin 9 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 1 | U4 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 12 | J_ESP_R pin 8 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 2 | U4 pin 7 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 3 | U4 pin 10 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 4 | U4 pin 15 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 5 | J_EXP_MCP pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 6 | J_EXP_MCP pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 7 | J_EXP_MCP pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top pin 8 | J_EXP_MCP pin 4 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q_LED2 pin 2 [no silk label] | R_LED2 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q_LED2 pin 3 [no silk label] | J_LED2 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q_LED3 pin 2 [no silk label] | R_LED3 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Strip Pins 3x150R pin 2 [no silk label] | Q_LED1 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 10 [no silk label] | MCP23017 Top pin 27 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 11 [no silk label] | J_MOT2 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 14 [no silk label] | J_MOT2 pin 4 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 15 [no silk label] | MCP23017 Top pin 28 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 2 [no silk label] | MCP23017 Top pin 25 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 3 [no silk label] | J_MOT2 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 6 [no silk label] | J_MOT2 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | U3 pin 7 [no silk label] | MCP23017 Top pin 26 | continuity / beep | **BEEP (short)** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **Gauge1 pin 10 [no silk label] ↔ MCP23017 Top pin 23** — `M1_IN3` (net 10) spans 2 pads; this pair is the FURTHEST APART (55.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Gauge1 pin 15 [no silk label] ↔ MCP23017 Top pin 24** — `M1_IN4` (net 11) spans 2 pads; this pair is the FURTHEST APART (48.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Gauge1 pin 2 [no silk label] ↔ MCP23017 Top pin 21** — `M1_IN1` (net 8) spans 2 pads; this pair is the FURTHEST APART (50.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Gauge1 pin 7 [no silk label] ↔ MCP23017 Top pin 22** — `M1_IN2` (net 9) spans 2 pads; this pair is the FURTHEST APART (59.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_ESP_L pin 1 [no silk label] ↔ Strip Pins 3x150R pin 1 [no silk label]** — `LED1_GPIO` (net 32) spans 2 pads; this pair is the FURTHEST APART (53.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_ESP_L pin 2 [no silk label] ↔ R_LED2 pin 1 [no silk label]** — `LED2_GPIO` (net 35) spans 2 pads; this pair is the FURTHEST APART (54.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_ESP_L pin 3 [no silk label] ↔ J_EXP_ESP pin 1 [no silk label]** — `GPIO2` (net 45) spans 2 pads; this pair is the FURTHEST APART (30.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_ESP_L pin 4 [no silk label] ↔ R_LED3 pin 1 [no silk label]** — `LED3_GPIO` (net 38) spans 2 pads; this pair is the FURTHEST APART (52.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_ESP_L pin 5 [no silk label] ↔ MCP23017 Top pin 13** — `SDA` (net 6) spans 3 pads; this pair is the FURTHEST APART (39.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_EXP_ESP pin 2 [no silk label] ↔ J_ESP_R pin 7 [no silk label]** — `GPIO6` (net 52) spans 2 pads; this pair is the FURTHEST APART (13.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_EXP_ESP pin 3 [no silk label] ↔ J_ESP_R pin 6 [no silk label]** — `GPIO7` (net 51) spans 2 pads; this pair is the FURTHEST APART (16.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_EXP_ESP pin 4 [no silk label] ↔ J_ESP_R pin 4 [no silk label]** — `GPIO9` (net 50) spans 2 pads; this pair is the FURTHEST APART (21.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_EXP_ESP pin 5 [no silk label] ↔ J_ESP_R pin 3 [no silk label]** — `GPIO10` (net 46) spans 2 pads; this pair is the FURTHEST APART (26.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_EXP_ESP pin 6 [no silk label] ↔ J_ESP_R pin 2 [no silk label]** — `GPIO20` (net 48) spans 2 pads; this pair is the FURTHEST APART (30.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_LED1 pin 2 [no silk label] ↔ Q_LED1 pin 3 [no silk label]** — `LED1_COL` (net 34) spans 2 pads; this pair is the FURTHEST APART (42.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_LED3 pin 2 [no silk label] ↔ Q_LED3 pin 3 [no silk label]** — `LED3_COL` (net 40) spans 2 pads; this pair is the FURTHEST APART (13.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT1 pin 1 [no silk label] ↔ Gauge1 pin 3 [no silk label]** — `MOT1_A` (net 20) spans 2 pads; this pair is the FURTHEST APART (17.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT1 pin 2 [no silk label] ↔ Gauge1 pin 6 [no silk label]** — `MOT1_B` (net 21) spans 2 pads; this pair is the FURTHEST APART (10.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT1 pin 3 [no silk label] ↔ Gauge1 pin 11 [no silk label]** — `MOT1_C` (net 22) spans 2 pads; this pair is the FURTHEST APART (10.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT1 pin 4 [no silk label] ↔ Gauge1 pin 14 [no silk label]** — `MOT1_D` (net 23) spans 2 pads; this pair is the FURTHEST APART (17.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT3 pin 1 [no silk label] ↔ U4 pin 3 [no silk label]** — `MOT3_A` (net 28) spans 2 pads; this pair is the FURTHEST APART (17.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT3 pin 2 [no silk label] ↔ U4 pin 6 [no silk label]** — `MOT3_B` (net 29) spans 2 pads; this pair is the FURTHEST APART (10.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT3 pin 3 [no silk label] ↔ U4 pin 11 [no silk label]** — `MOT3_C` (net 30) spans 2 pads; this pair is the FURTHEST APART (10.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_MOT3 pin 4 [no silk label] ↔ U4 pin 14 [no silk label]** — `MOT3_D` (net 31) spans 2 pads; this pair is the FURTHEST APART (17.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_PWR pin 1 ↔ D1 pin 1 [no silk label]** — `+12V_IN` (net 2) spans 2 pads; this pair is the FURTHEST APART (7.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J_PWR_TAP pin 3 [no silk label] ↔ R_SDA pin 2 [no silk label]** — `+3V3` (net 5) spans 4 pads; this pair is the FURTHEST APART (32.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **LM7805 pin 1 ↔ J_LED3 pin 1 [no silk label]** — `+12V` (net 3) spans 8 pads; this pair is the FURTHEST APART (81.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **LM7805 pin 2 ↔ Q_LED3 pin 1 [no silk label]** — `GND` (net 1) spans 30 pads; this pair is the FURTHEST APART (84.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **LM7805 pin 3 ↔ U4 pin 9 [no silk label]** — `+5V` (net 4) spans 24 pads; this pair is the FURTHEST APART (70.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 1 ↔ U4 pin 2 [no silk label]** — `M3_IN1` (net 16) spans 2 pads; this pair is the FURTHEST APART (42.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 12 ↔ J_ESP_R pin 8 [no silk label]** — `SCL` (net 7) spans 3 pads; this pair is the FURTHEST APART (21.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 2 ↔ U4 pin 7 [no silk label]** — `M3_IN2` (net 17) spans 2 pads; this pair is the FURTHEST APART (52.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 3 ↔ U4 pin 10 [no silk label]** — `M3_IN3` (net 18) spans 2 pads; this pair is the FURTHEST APART (48.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 4 ↔ U4 pin 15 [no silk label]** — `M3_IN4` (net 19) spans 2 pads; this pair is the FURTHEST APART (33.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 5 ↔ J_EXP_MCP pin 1 [no silk label]** — `GPB4` (net 41) spans 2 pads; this pair is the FURTHEST APART (28.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 6 ↔ J_EXP_MCP pin 2 [no silk label]** — `GPB5` (net 42) spans 2 pads; this pair is the FURTHEST APART (28.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 7 ↔ J_EXP_MCP pin 3 [no silk label]** — `GPB6` (net 43) spans 2 pads; this pair is the FURTHEST APART (28.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top pin 8 ↔ J_EXP_MCP pin 4 [no silk label]** — `GPB7` (net 44) spans 2 pads; this pair is the FURTHEST APART (28.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q_LED2 pin 2 [no silk label] ↔ R_LED2 pin 2 [no silk label]** — `LED2_BASE` (net 36) spans 2 pads; this pair is the FURTHEST APART (18.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q_LED2 pin 3 [no silk label] ↔ J_LED2 pin 2 [no silk label]** — `LED2_COL` (net 37) spans 2 pads; this pair is the FURTHEST APART (27.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q_LED3 pin 2 [no silk label] ↔ R_LED3 pin 2 [no silk label]** — `LED3_BASE` (net 39) spans 2 pads; this pair is the FURTHEST APART (20.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Strip Pins 3x150R pin 2 [no silk label] ↔ Q_LED1 pin 2 [no silk label]** — `LED1_BASE` (net 33) spans 2 pads; this pair is the FURTHEST APART (15.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 10 [no silk label] ↔ MCP23017 Top pin 27** — `M2_IN3` (net 14) spans 2 pads; this pair is the FURTHEST APART (56.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 11 [no silk label] ↔ J_MOT2 pin 3 [no silk label]** — `MOT2_C` (net 26) spans 2 pads; this pair is the FURTHEST APART (10.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 14 [no silk label] ↔ J_MOT2 pin 4 [no silk label]** — `MOT2_D` (net 27) spans 2 pads; this pair is the FURTHEST APART (17.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 15 [no silk label] ↔ MCP23017 Top pin 28** — `M2_IN4` (net 15) spans 2 pads; this pair is the FURTHEST APART (47.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 2 [no silk label] ↔ MCP23017 Top pin 25** — `M2_IN1` (net 12) spans 2 pads; this pair is the FURTHEST APART (45.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 3 [no silk label] ↔ J_MOT2 pin 1 [no silk label]** — `MOT2_A` (net 24) spans 2 pads; this pair is the FURTHEST APART (17.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 6 [no silk label] ↔ J_MOT2 pin 2 [no silk label]** — `MOT2_B` (net 25) spans 2 pads; this pair is the FURTHEST APART (10.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **U3 pin 7 [no silk label] ↔ MCP23017 Top pin 26** — `M2_IN2` (net 13) spans 2 pads; this pair is the FURTHEST APART (57.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.

</details>

---

## §5 — NETS THAT CANNOT BE TESTED HERE

⛔ **`BLOCKED` — listed, not dropped.** Each of these has fewer than two pads, so there is no second probe point. A net with one pad is either a legitimate single-ended signal or a net whose other end was never placed, and those look identical from the board file.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_ESP_R pin 1 [no silk label] | — no second pad — | continuity / beep | **NOT TESTABLE on a bare board** | ____________ |
| ☐ | J_ESP_R pin 5 [no silk label] | — no second pad — | continuity / beep | **NOT TESTABLE on a bare board** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **J_ESP_R pin 1 [no silk label] ↔ — no second pad —** — `GPIO21` (net 49) has 1 pad(s) in the board file. A continuity check needs two probe points; there is no second one.
  - ⛔ EMITTED, NOT OMITTED. A net with one pad is either a single-ended signal that is fine, or a net whose other end was never placed — and those look identical here. Confirm against the intended design which it is.
- **J_ESP_R pin 5 [no silk label] ↔ — no second pad —** — `GPIO8` (net 47) has 1 pad(s) in the board file. A continuity check needs two probe points; there is no second one.
  - ⛔ EMITTED, NOT OMITTED. A net with one pad is either a single-ended signal that is fine, or a net whose other end was never placed — and those look identical here. Confirm against the intended design which it is.

</details>

---

## POST-INTEGRATION — evidence slots, ALL EMPTY

⛔ **`hardware-status`: NO BOARD HAS BEEN BEEPED OUT AGAINST THIS SHEET.** Every slot
below is `EMPTY` and says so. An unfilled slot carries the literal sentinel rather
than an absent key, because an absent key is indistinguishable from a key nobody
thought to write — and that ambiguity is the whole failure this closes. It makes
*"has this ever touched a bench"* a **query**, not something a reader must already
know. (`[DX-SPEC-CARRIES-ITS-EVIDENCE]`, shape from `[CXC-PERIPH-EVIDENCE-SLOTS]`.)

**How to fill:** replace `evidence-state: EMPTY` with `RECORDED`, fill `observed`,
`observed-on-date` and `observed-by`, and **leave the question and the expectation
untouched** so the two can be compared later.

### `rail-isolation-pass`

- question: Did every rail-to-rail pair in §1 settle above 100 kΩ?
- expectation: all pairs > 100 kΩ, read as SETTLED values on the 2 MΩ range
- evidence-state: EMPTY
- observed: ⛔ EMPTY
- observed-on-date: ⛔ EMPTY
- observed-by: ⛔ EMPTY

### `net-continuity-pass`

- question: Did every §4 pair beep?
- expectation: every net's longest-run pair beeps
- evidence-state: EMPTY
- observed: ⛔ EMPTY
- observed-on-date: ⛔ EMPTY
- observed-by: ⛔ EMPTY

### `adjacent-pad-pass`

- question: Did every §3 adjacent pair stay silent?
- expectation: no beep on any different-net neighbour
- evidence-state: EMPTY
- observed: ⛔ EMPTY
- observed-on-date: ⛔ EMPTY
- observed-by: ⛔ EMPTY

### `part-orientation-confirmed`

- question: Was every §2 loose part diode-checked BEFORE soldering?
- expectation: each part's measured pinout matches the footprint, or the deviation is recorded here
- evidence-state: EMPTY
- observed: ⛔ EMPTY
- observed-on-date: ⛔ EMPTY
- observed-by: ⛔ EMPTY

### `board-serial`

- question: WHICH physical board was this? (5 were fabbed; they are not interchangeable once one is reworked)
- expectation: a serial or a hand-written mark on the board itself
- evidence-state: EMPTY
- observed: ⛔ EMPTY
- observed-on-date: ⛔ EMPTY
- observed-by: ⛔ EMPTY

### `blocked-nets-adjudicated`

- question: For each §5 net, is the single pad INTENDED, or is the other end missing?
- expectation: a per-net ruling — intended / defect
- evidence-state: EMPTY
- observed: ⛔ EMPTY
- observed-on-date: ⛔ EMPTY
- observed-by: ⛔ EMPTY

---

## Appendix — the net table, for probing anything not listed above

| net | name | pads | appears at |
|---|---|---|---|
| 1 | `GND` ⚡ | 30 | 2x100nF pin 2, 3x100nF pin 2, C1 pin 2, C2 pin 2, C3 pin 2, C6 pin 2, C7 pin 2, Gauge1 pin 12, Gauge1 pin 13, Gauge1 pin 4, Gauge1 pin 5, J_ESP_L p… |
| 2 | `+12V_IN` ⚡ | 2 | D1 pin 1, J_PWR pin 1 |
| 3 | `+12V` ⚡ | 8 | C1 pin 1, C2 pin 1, D1 pin 2, J_LED1 pin 1, J_LED2 pin 1, J_LED3 pin 1, J_PWR_TAP pin 1, LM7805 pin 1 |
| 4 | `+5V` ⚡ | 24 | 10kR pin 1, 10kR pin 2, 2x100nF pin 1, 3x100nF pin 1, C3 pin 1, C6 pin 1, C7 pin 1, Gauge1 pin 1, Gauge1 pin 16, Gauge1 pin 8, Gauge1 pin 9, J_ESP_… |
| 5 | `+3V3` ⚡ | 4 | 4.7kR pin 2, J_ESP_L pin 6, J_PWR_TAP pin 3, R_SDA pin 2 |
| 6 | `SDA` | 3 | J_ESP_L pin 5, MCP23017 Top pin 13, R_SDA pin 1 |
| 7 | `SCL` | 3 | 4.7kR pin 1, J_ESP_R pin 8, MCP23017 Top pin 12 |
| 8 | `M1_IN1` | 2 | Gauge1 pin 2, MCP23017 Top pin 21 |
| 9 | `M1_IN2` | 2 | Gauge1 pin 7, MCP23017 Top pin 22 |
| 10 | `M1_IN3` | 2 | Gauge1 pin 10, MCP23017 Top pin 23 |
| 11 | `M1_IN4` | 2 | Gauge1 pin 15, MCP23017 Top pin 24 |
| 12 | `M2_IN1` | 2 | MCP23017 Top pin 25, U3 pin 2 |
| 13 | `M2_IN2` | 2 | MCP23017 Top pin 26, U3 pin 7 |
| 14 | `M2_IN3` | 2 | MCP23017 Top pin 27, U3 pin 10 |
| 15 | `M2_IN4` | 2 | MCP23017 Top pin 28, U3 pin 15 |
| 16 | `M3_IN1` | 2 | MCP23017 Top pin 1, U4 pin 2 |
| 17 | `M3_IN2` | 2 | MCP23017 Top pin 2, U4 pin 7 |
| 18 | `M3_IN3` | 2 | MCP23017 Top pin 3, U4 pin 10 |
| 19 | `M3_IN4` | 2 | MCP23017 Top pin 4, U4 pin 15 |
| 20 | `MOT1_A` | 2 | Gauge1 pin 3, J_MOT1 pin 1 |
| 21 | `MOT1_B` | 2 | Gauge1 pin 6, J_MOT1 pin 2 |
| 22 | `MOT1_C` | 2 | Gauge1 pin 11, J_MOT1 pin 3 |
| 23 | `MOT1_D` | 2 | Gauge1 pin 14, J_MOT1 pin 4 |
| 24 | `MOT2_A` | 2 | J_MOT2 pin 1, U3 pin 3 |
| 25 | `MOT2_B` | 2 | J_MOT2 pin 2, U3 pin 6 |
| 26 | `MOT2_C` | 2 | J_MOT2 pin 3, U3 pin 11 |
| 27 | `MOT2_D` | 2 | J_MOT2 pin 4, U3 pin 14 |
| 28 | `MOT3_A` | 2 | J_MOT3 pin 1, U4 pin 3 |
| 29 | `MOT3_B` | 2 | J_MOT3 pin 2, U4 pin 6 |
| 30 | `MOT3_C` | 2 | J_MOT3 pin 3, U4 pin 11 |
| 31 | `MOT3_D` | 2 | J_MOT3 pin 4, U4 pin 14 |
| 32 | `LED1_GPIO` | 2 | J_ESP_L pin 1, Strip Pins 3x150R pin 1 |
| 33 | `LED1_BASE` | 2 | Q_LED1 pin 2, Strip Pins 3x150R pin 2 |
| 34 | `LED1_COL` | 2 | J_LED1 pin 2, Q_LED1 pin 3 |
| 35 | `LED2_GPIO` | 2 | J_ESP_L pin 2, R_LED2 pin 1 |
| 36 | `LED2_BASE` | 2 | Q_LED2 pin 2, R_LED2 pin 2 |
| 37 | `LED2_COL` | 2 | J_LED2 pin 2, Q_LED2 pin 3 |
| 38 | `LED3_GPIO` | 2 | J_ESP_L pin 4, R_LED3 pin 1 |
| 39 | `LED3_BASE` | 2 | Q_LED3 pin 2, R_LED3 pin 2 |
| 40 | `LED3_COL` | 2 | J_LED3 pin 2, Q_LED3 pin 3 |
| 41 | `GPB4` | 2 | J_EXP_MCP pin 1, MCP23017 Top pin 5 |
| 42 | `GPB5` | 2 | J_EXP_MCP pin 2, MCP23017 Top pin 6 |
| 43 | `GPB6` | 2 | J_EXP_MCP pin 3, MCP23017 Top pin 7 |
| 44 | `GPB7` | 2 | J_EXP_MCP pin 4, MCP23017 Top pin 8 |
| 45 | `GPIO2` | 2 | J_ESP_L pin 3, J_EXP_ESP pin 1 |
| 46 | `GPIO10` | 2 | J_ESP_R pin 3, J_EXP_ESP pin 5 |
| 47 | `GPIO8` | 1 | J_ESP_R pin 5 |
| 48 | `GPIO20` | 2 | J_ESP_R pin 2, J_EXP_ESP pin 6 |
| 49 | `GPIO21` | 1 | J_ESP_R pin 1 |
| 50 | `GPIO9` | 2 | J_ESP_R pin 4, J_EXP_ESP pin 4 |
| 51 | `GPIO7` | 2 | J_ESP_R pin 6, J_EXP_ESP pin 3 |
| 52 | `GPIO6` | 2 | J_ESP_R pin 7, J_EXP_ESP pin 2 |

