<!-- BEGIN BEEPOUT-PROVENANCE (composeio.maker.beepout) -->
{
  "board": "lego_dots",
  "derived_from": "/Users/davidleeburnett/Dropbox/Maker/pcb/lego_dots/lego_dots.kicad_pcb",
  "derived_from_sha256": "cae1425f8f94d9afe7381974f01274f8a2e5cc8e0b0bfd5010a9cdf74283a35e",
  "footprints": 47,
  "kicad_version": "20241229",
  "nets": 42,
  "pads": 127,
  "rows_by_category": {
    "adjacent-pad-isolation": 65,
    "net-continuity": 40,
    "net-untestable": 1,
    "part-orientation": 15,
    "rail-isolation": 10
  },
  "rows_by_state": {
    "BLOCKED": 1,
    "DERIVED": 115,
    "UNVERIFIED": 15
  },
  "rows_total": 131,
  "schema": "composeio.beepout/1"
}
<!-- END BEEPOUT-PROVENANCE -->

# `lego_dots` — BEEP-OUT SHEET (bare board)

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
| derived-from | `/Users/davidleeburnett/Dropbox/Maker/pcb/lego_dots/lego_dots.kicad_pcb` |
| derived-from-sha256 | `cae1425f8f94d9afe7381974f01274f8a2e5cc8e0b0bfd5010a9cdf74283a35e` |
| kicad-version | `20241229` (generator 9.0) |
| generated-by | `composeio.maker.beepout` |
| generated-on | 2026-08-15 |
| nets / footprints / pads | 42 / 47 / 127 |

**Re-check that this sheet still describes the board in your hand:**

```bash
shasum -a 256 /Users/davidleeburnett/Dropbox/Maker/pcb/lego_dots/lego_dots.kicad_pcb
# must print: cae1425f8f94d9afe7381974f01274f8a2e5cc8e0b0bfd5010a9cdf74283a35e
```

If it differs, the board was revised and **this sheet is stale** — regenerate it.

### Row counts

| category | rows |
|---|---|
| rail-isolation | 10 |
| part-orientation | 15 |
| adjacent-pad-isolation | 65 |
| net-continuity | 40 |
| net-untestable | 1 |
| **total** | **131** |

| derivation state | rows | meaning |
|---|---|---|
| `DERIVED` | 115 | follows from the netlist/geometry; mutate the source and the row changes |
| `UNVERIFIED` | 15 | a REAL check the board file cannot settle — **emitted, never omitted** |
| `BLOCKED` | 1 | cannot even be stated from this source; that itself is a finding |

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

- ⛔ 26 footprint(s) carry a Reference marked `(hide yes)` — their designator is NOT printed on the silkscreen, so those probe points cannot be found by label. Locate them by position against the board render. Affected: J1, J2, J3, J4, Q1, Q10, Q11, Q12, Q2, Q3, Q4, Q5, Q6, Q7, Q8, Q9…
- This board's BOM includes bulk electrolytic(s) (`CP_` footprint). On a TRULY BARE board they are not fitted and the rails read hard-open; if this sheet is reused on a STUFFED board, an uncharged 100 µF beeps and the 2 MΩ settled reading is the only trustworthy one.
- Geometry self-check: 92/92 netted pads lie within 0.6 mm of a copper endpoint on the same net (100%). This is an independent check on this tool's own pad-position arithmetic — copper endpoints are written by a different code path than pad positions, so agreement is evidence that does not share the parser's assumption.

---

## §1 — RAIL ISOLATION

**Power has never been applied. Nothing is seated.** Meter in **resistance, 2 MΩ**. ⛔ **NOT continuity.** These are the checks whose failure destroys parts on power-up, so they are first.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | +12V @ J11 pin 2 | +3V3 @ Left pin 6 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V @ J11 pin 2 | +5V @ Left pin 8 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V_IN @ VIN pin 1 | +12V @ J11 pin 2 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V_IN @ VIN pin 1 | +3V3 @ Left pin 6 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +12V_IN @ VIN pin 1 | +5V @ Left pin 8 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | +5V @ Left pin 8 | +3V3 @ Left pin 6 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ VIN pin 2 | +12V @ J11 pin 2 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ VIN pin 2 | +12V_IN @ VIN pin 1 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ VIN pin 2 | +3V3 @ Left pin 6 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ VIN pin 2 | +5V @ Left pin 8 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **+12V @ J11 pin 2 ↔ +3V3 @ Left pin 6** — `+12V` (net 3) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V @ J11 pin 2 ↔ +5V @ Left pin 8** — `+12V` (net 3) and `+5V` (net 4) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `LM7805` (LM7805).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V and +5V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V_IN @ VIN pin 1 ↔ +12V @ J11 pin 2** — `+12V_IN` (net 2) and `+12V` (net 3) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `IN5401` (1N5401).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V_IN and +12V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V_IN @ VIN pin 1 ↔ +3V3 @ Left pin 6** — `+12V_IN` (net 2) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V_IN and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+12V_IN @ VIN pin 1 ↔ +5V @ Left pin 8** — `+12V_IN` (net 2) and `+5V` (net 4) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +12V_IN and +5V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **+5V @ Left pin 8 ↔ +3V3 @ Left pin 6** — `+5V` (net 4) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between +5V and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ VIN pin 2 ↔ +12V @ J11 pin 2** — `GND` (net 1) and `+12V` (net 3) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `LM7805` (LM7805).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +12V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ VIN pin 2 ↔ +12V_IN @ VIN pin 1** — `GND` (net 1) and `+12V_IN` (net 2) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +12V_IN. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ VIN pin 2 ↔ +3V3 @ Left pin 6** — `GND` (net 1) and `+3V3` (net 5) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ VIN pin 2 ↔ +5V @ Left pin 8** — `GND` (net 1) and `+5V` (net 4) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern. ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH `LM7805` (LM7805).** On a BARE board that part is not fitted, so OPEN is still the correct reading and a short here is still a defect. ⛔ But once the part IS fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL reversed in diode mode; a linear regulator some hundreds of kΩ. Do not re-run this row on a stuffed board and read the result as a short.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the meter's test current charges it. That is a CORRECT reading of a GOOD board. Watch the number climb and read the SETTLED value.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and +5V. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.

</details>

---

## §2 — PART / FOOTPRINT ASSUMPTIONS

⛔ **`UNVERIFIED` BY CONSTRUCTION.** The footprint encodes an assumption about a part the board file cannot see. Check the LOOSE PART before soldering. These are emitted rather than omitted precisely because an absent check is indistinguishable from one nobody thought of.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 100uF pin 1 — 100uF | loose part (100uF) | resistance, 2 MΩ | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | IN5401 pin 1 — IN5401 | loose part (1N5401) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | LM7805 pin 1 — LM7805 | loose part (LM7805) | continuity / beep | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q1 pin 1 [no silk label] — Q1 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q10 pin 1 [no silk label] — Q10 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q11 pin 1 [no silk label] — Q11 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q12 pin 1 [no silk label] — Q12 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q2 pin 1 [no silk label] — Q2 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q3 pin 1 [no silk label] — Q3 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q4 pin 1 [no silk label] — Q4 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q5 pin 1 [no silk label] — Q5 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q6 pin 1 [no silk label] — Q6 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q7 pin 1 [no silk label] — Q7 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q8 pin 1 [no silk label] — Q8 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |
| ☐ | Q9 pin 1 [no silk label] — Q9 | loose part (BC337) | diode mode | **part pinout MATCHES the footprint's assumption** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **100uF pin 1 — 100uF ↔ loose part (100uF)** — `100uF` uses `CP_Radial_D5.0mm_P2.5mm`, which ENCODES a electrolytic polarity assumption. The board file records which net each pad carries (pad 1->`+12V`, pad 2->`GND`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ POLARISED PART. Confirm the NEGATIVE STRIPE faces the pad on the ground/return net before soldering. A reversed electrolytic on a 12 V rail vents, and it is the most likely single cause of a rail-to-rail short in the block above.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **IN5401 pin 1 — IN5401 ↔ loose part (1N5401)** — `IN5401` uses `D_DO-41_SOD81_P10.16mm_Horizontal`, which ENCODES a diode orientation assumption. The board file records which net each pad carries (pad 1->`+12V_IN`, pad 2->`+12V`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ ORIENTATION MATTERS AND THE SILKSCREEN MAY BE DRAWN ROTATED. Read the CATHODE BAND on the part, not the outline. Forward should read 0.4–0.8 V one way and OL reversed. A reverse-protection diode fitted backwards means the board never powers up; fitted shorted means it never protects.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **LM7805 pin 1 — LM7805 ↔ loose part (LM7805)** — `LM7805` uses `TO-220-3_Vertical`, which ENCODES a regulator pinout assumption. The board file records which net each pad carries (pad 1->`+12V`, pad 2->`GND`, pad 3->`+5V`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ CONFIRM THE PART'S PINOUT AGAINST THE FOOTPRINT. A linear regulator fitted backwards puts its input voltage onto the output rail through the substrate diode, destroying everything downstream. Note the TAB is usually tied to one of the pins — check what it touches.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q1 pin 1 [no silk label] — Q1 ↔ loose part (BC337)** — `Q1` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE1`, pad 3->`COL1`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q10 pin 1 [no silk label] — Q10 ↔ loose part (BC337)** — `Q10` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE10`, pad 3->`COL10`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q11 pin 1 [no silk label] — Q11 ↔ loose part (BC337)** — `Q11` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE11`, pad 3->`COL11`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q12 pin 1 [no silk label] — Q12 ↔ loose part (BC337)** — `Q12` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE12`, pad 3->`COL12`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q2 pin 1 [no silk label] — Q2 ↔ loose part (BC337)** — `Q2` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE2`, pad 3->`COL2`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q3 pin 1 [no silk label] — Q3 ↔ loose part (BC337)** — `Q3` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE3`, pad 3->`COL3`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q4 pin 1 [no silk label] — Q4 ↔ loose part (BC337)** — `Q4` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE4`, pad 3->`COL4`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q5 pin 1 [no silk label] — Q5 ↔ loose part (BC337)** — `Q5` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE5`, pad 3->`COL5`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q6 pin 1 [no silk label] — Q6 ↔ loose part (BC337)** — `Q6` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE6`, pad 3->`COL6`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q7 pin 1 [no silk label] — Q7 ↔ loose part (BC337)** — `Q7` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE7`, pad 3->`COL7`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q8 pin 1 [no silk label] — Q8 ↔ loose part (BC337)** — `Q8` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE8`, pad 3->`COL8`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.
- **Q9 pin 1 [no silk label] — Q9 ↔ loose part (BC337)** — `Q9` uses `TO-92_Inline`, which ENCODES a transistor pin order assumption. The board file records which net each pad carries (pad 1->`GND`, pad 2->`BASE9`, pad 3->`COL9`) but CANNOT know which physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is emitted, not omitted, because a silently absent check is indistinguishable from one nobody thought of.
  - ⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. Not every TO-92 sold under a given part number has the same pinout: a 2N2222/PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.
  - **ON FAIL:** Re-orient the part, or source one matching the footprint. ⛔ Record the deviation on the board and in this sheet — an UNDOCUMENTED deviation is worse than either choice.

</details>

---

## §3 — ADJACENT-PAD ISOLATION

Meter in **continuity**. Adjacency is derived from each footprint's OWN minimum pad spacing — not from a guess about which parts are fine-pitch. Every pair below carries **different nets** and must **not** beep.

#### `100nF`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 100nF pin 1 (`+5V`) | 100nF pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `100uF`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 100uF pin 1 (`+12V`) | 100uF pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J1 pin 1 [no silk label] (`GND`) | J1 pin 2 [no silk label] (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J1 pin 2 [no silk label] (`+12V`) | J1 pin 3 [no silk label] (`COL1`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J10`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J10 pin 1 (`GND`) | J10 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J10 pin 2 (`+12V`) | J10 pin 3 (`COL10`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J11`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J11 pin 1 (`GND`) | J11 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J11 pin 2 (`+12V`) | J11 pin 3 (`COL11`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J12`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J12 pin 1 (`GND`) | J12 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J12 pin 2 (`+12V`) | J12 pin 3 (`COL12`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J2 pin 1 [no silk label] (`GND`) | J2 pin 2 [no silk label] (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J2 pin 2 [no silk label] (`+12V`) | J2 pin 3 [no silk label] (`COL2`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J3 pin 1 [no silk label] (`GND`) | J3 pin 2 [no silk label] (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J3 pin 2 [no silk label] (`+12V`) | J3 pin 3 [no silk label] (`COL3`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J4`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J4 pin 1 [no silk label] (`GND`) | J4 pin 2 [no silk label] (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J4 pin 2 [no silk label] (`+12V`) | J4 pin 3 [no silk label] (`COL4`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J5`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J5 pin 1 (`GND`) | J5 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J5 pin 2 (`+12V`) | J5 pin 3 (`COL5`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J6`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J6 pin 1 (`GND`) | J6 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J6 pin 2 (`+12V`) | J6 pin 3 (`COL6`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J7`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J7 pin 1 (`GND`) | J7 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J7 pin 2 (`+12V`) | J7 pin 3 (`COL7`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J8`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J8 pin 1 (`GND`) | J8 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J8 pin 2 (`+12V`) | J8 pin 3 (`COL8`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J9`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J9 pin 1 (`GND`) | J9 pin 2 (`+12V`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J9 pin 2 (`+12V`) | J9 pin 3 (`COL9`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `LM7805`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | LM7805 pin 1 (`+12V`) | LM7805 pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | LM7805 pin 2 (`GND`) | LM7805 pin 3 (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Left`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Left pin 1 (`GPIO0`) | Left pin 2 (`GPIO1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 2 (`GPIO1`) | Left pin 3 (`GPIO2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 3 (`GPIO2`) | Left pin 4 (`GPIO3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 4 (`GPIO3`) | Left pin 5 (`GPIO4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 5 (`GPIO4`) | Left pin 6 (`+3V3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 6 (`+3V3`) | Left pin 7 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 7 (`GND`) | Left pin 8 (`+5V`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q1 pin 1 [no silk label] (`GND`) | Q1 pin 2 [no silk label] (`BASE1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q1 pin 2 [no silk label] (`BASE1`) | Q1 pin 3 [no silk label] (`COL1`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q10`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q10 pin 1 [no silk label] (`GND`) | Q10 pin 2 [no silk label] (`BASE10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q10 pin 2 [no silk label] (`BASE10`) | Q10 pin 3 [no silk label] (`COL10`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q11`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q11 pin 1 [no silk label] (`GND`) | Q11 pin 2 [no silk label] (`BASE11`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q11 pin 2 [no silk label] (`BASE11`) | Q11 pin 3 [no silk label] (`COL11`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q12`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q12 pin 1 [no silk label] (`GND`) | Q12 pin 2 [no silk label] (`BASE12`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q12 pin 2 [no silk label] (`BASE12`) | Q12 pin 3 [no silk label] (`COL12`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q2 pin 1 [no silk label] (`GND`) | Q2 pin 2 [no silk label] (`BASE2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q2 pin 2 [no silk label] (`BASE2`) | Q2 pin 3 [no silk label] (`COL2`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q3 pin 1 [no silk label] (`GND`) | Q3 pin 2 [no silk label] (`BASE3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q3 pin 2 [no silk label] (`BASE3`) | Q3 pin 3 [no silk label] (`COL3`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q4`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q4 pin 1 [no silk label] (`GND`) | Q4 pin 2 [no silk label] (`BASE4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q4 pin 2 [no silk label] (`BASE4`) | Q4 pin 3 [no silk label] (`COL4`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q5`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q5 pin 1 [no silk label] (`GND`) | Q5 pin 2 [no silk label] (`BASE5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q5 pin 2 [no silk label] (`BASE5`) | Q5 pin 3 [no silk label] (`COL5`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q6`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q6 pin 1 [no silk label] (`GND`) | Q6 pin 2 [no silk label] (`BASE6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q6 pin 2 [no silk label] (`BASE6`) | Q6 pin 3 [no silk label] (`COL6`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q7`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q7 pin 1 [no silk label] (`GND`) | Q7 pin 2 [no silk label] (`BASE7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q7 pin 2 [no silk label] (`BASE7`) | Q7 pin 3 [no silk label] (`COL7`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q8`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q8 pin 1 [no silk label] (`GND`) | Q8 pin 2 [no silk label] (`BASE8`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q8 pin 2 [no silk label] (`BASE8`) | Q8 pin 3 [no silk label] (`COL8`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Q9`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Q9 pin 1 [no silk label] (`GND`) | Q9 pin 2 [no silk label] (`BASE9`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Q9 pin 2 [no silk label] (`BASE9`) | Q9 pin 3 [no silk label] (`COL9`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Right`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Right pin 2 (`GPIO20`) | Right pin 3 (`GPIO10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 3 (`GPIO10`) | Right pin 4 (`GPIO9`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 4 (`GPIO9`) | Right pin 5 (`GPIO8`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 5 (`GPIO8`) | Right pin 6 (`GPIO7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 6 (`GPIO7`) | Right pin 7 (`GPIO6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 7 (`GPIO6`) | Right pin 8 (`GPIO5`) | continuity / beep | **NO BEEP (open)** | ____________ |


<details><summary>Why each row exists, and what a failure means</summary>

- **100nF pin 1 (`+5V`) ↔ 100nF pin 2 (`GND`)** — Neighbouring pads on `100nF` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+5V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **100uF pin 1 (`+12V`) ↔ 100uF pin 2 (`GND`)** — Neighbouring pads on `100uF` (CP_Radial_D5.0mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J1 pin 1 [no silk label] (`GND`) ↔ J1 pin 2 [no silk label] (`+12V`)** — Neighbouring pads on `J1` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J1 pin 2 [no silk label] (`+12V`) ↔ J1 pin 3 [no silk label] (`COL1`)** — Neighbouring pads on `J1` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J10 pin 1 (`GND`) ↔ J10 pin 2 (`+12V`)** — Neighbouring pads on `J10` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J10 pin 2 (`+12V`) ↔ J10 pin 3 (`COL10`)** — Neighbouring pads on `J10` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J11 pin 1 (`GND`) ↔ J11 pin 2 (`+12V`)** — Neighbouring pads on `J11` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J11 pin 2 (`+12V`) ↔ J11 pin 3 (`COL11`)** — Neighbouring pads on `J11` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J12 pin 1 (`GND`) ↔ J12 pin 2 (`+12V`)** — Neighbouring pads on `J12` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J12 pin 2 (`+12V`) ↔ J12 pin 3 (`COL12`)** — Neighbouring pads on `J12` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J2 pin 1 [no silk label] (`GND`) ↔ J2 pin 2 [no silk label] (`+12V`)** — Neighbouring pads on `J2` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J2 pin 2 [no silk label] (`+12V`) ↔ J2 pin 3 [no silk label] (`COL2`)** — Neighbouring pads on `J2` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J3 pin 1 [no silk label] (`GND`) ↔ J3 pin 2 [no silk label] (`+12V`)** — Neighbouring pads on `J3` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J3 pin 2 [no silk label] (`+12V`) ↔ J3 pin 3 [no silk label] (`COL3`)** — Neighbouring pads on `J3` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J4 pin 1 [no silk label] (`GND`) ↔ J4 pin 2 [no silk label] (`+12V`)** — Neighbouring pads on `J4` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J4 pin 2 [no silk label] (`+12V`) ↔ J4 pin 3 [no silk label] (`COL4`)** — Neighbouring pads on `J4` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J5 pin 1 (`GND`) ↔ J5 pin 2 (`+12V`)** — Neighbouring pads on `J5` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J5 pin 2 (`+12V`) ↔ J5 pin 3 (`COL5`)** — Neighbouring pads on `J5` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J6 pin 1 (`GND`) ↔ J6 pin 2 (`+12V`)** — Neighbouring pads on `J6` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J6 pin 2 (`+12V`) ↔ J6 pin 3 (`COL6`)** — Neighbouring pads on `J6` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J7 pin 1 (`GND`) ↔ J7 pin 2 (`+12V`)** — Neighbouring pads on `J7` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J7 pin 2 (`+12V`) ↔ J7 pin 3 (`COL7`)** — Neighbouring pads on `J7` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J8 pin 1 (`GND`) ↔ J8 pin 2 (`+12V`)** — Neighbouring pads on `J8` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J8 pin 2 (`+12V`) ↔ J8 pin 3 (`COL8`)** — Neighbouring pads on `J8` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J9 pin 1 (`GND`) ↔ J9 pin 2 (`+12V`)** — Neighbouring pads on `J9` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `+12V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J9 pin 2 (`+12V`) ↔ J9 pin 3 (`COL9`)** — Neighbouring pads on `J9` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`+12V` vs `COL9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **LM7805 pin 1 (`+12V`) ↔ LM7805 pin 2 (`GND`)** — Neighbouring pads on `LM7805` (TO-220-3_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+12V` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **LM7805 pin 2 (`GND`) ↔ LM7805 pin 3 (`+5V`)** — Neighbouring pads on `LM7805` (TO-220-3_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 1 (`GPIO0`) ↔ Left pin 2 (`GPIO1`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO0` vs `GPIO1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 2 (`GPIO1`) ↔ Left pin 3 (`GPIO2`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO1` vs `GPIO2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 3 (`GPIO2`) ↔ Left pin 4 (`GPIO3`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO2` vs `GPIO3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 4 (`GPIO3`) ↔ Left pin 5 (`GPIO4`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO3` vs `GPIO4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 5 (`GPIO4`) ↔ Left pin 6 (`+3V3`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO4` vs `+3V3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 6 (`+3V3`) ↔ Left pin 7 (`GND`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`+3V3` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 7 (`GND`) ↔ Left pin 8 (`+5V`)** — Neighbouring pads on `Left` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `+5V`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q1 pin 1 [no silk label] (`GND`) ↔ Q1 pin 2 [no silk label] (`BASE1`)** — Neighbouring pads on `Q1` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q1 pin 2 [no silk label] (`BASE1`) ↔ Q1 pin 3 [no silk label] (`COL1`)** — Neighbouring pads on `Q1` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE1` vs `COL1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q10 pin 1 [no silk label] (`GND`) ↔ Q10 pin 2 [no silk label] (`BASE10`)** — Neighbouring pads on `Q10` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q10 pin 2 [no silk label] (`BASE10`) ↔ Q10 pin 3 [no silk label] (`COL10`)** — Neighbouring pads on `Q10` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE10` vs `COL10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q11 pin 1 [no silk label] (`GND`) ↔ Q11 pin 2 [no silk label] (`BASE11`)** — Neighbouring pads on `Q11` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q11 pin 2 [no silk label] (`BASE11`) ↔ Q11 pin 3 [no silk label] (`COL11`)** — Neighbouring pads on `Q11` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE11` vs `COL11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q12 pin 1 [no silk label] (`GND`) ↔ Q12 pin 2 [no silk label] (`BASE12`)** — Neighbouring pads on `Q12` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q12 pin 2 [no silk label] (`BASE12`) ↔ Q12 pin 3 [no silk label] (`COL12`)** — Neighbouring pads on `Q12` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE12` vs `COL12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q2 pin 1 [no silk label] (`GND`) ↔ Q2 pin 2 [no silk label] (`BASE2`)** — Neighbouring pads on `Q2` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q2 pin 2 [no silk label] (`BASE2`) ↔ Q2 pin 3 [no silk label] (`COL2`)** — Neighbouring pads on `Q2` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE2` vs `COL2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q3 pin 1 [no silk label] (`GND`) ↔ Q3 pin 2 [no silk label] (`BASE3`)** — Neighbouring pads on `Q3` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q3 pin 2 [no silk label] (`BASE3`) ↔ Q3 pin 3 [no silk label] (`COL3`)** — Neighbouring pads on `Q3` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE3` vs `COL3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q4 pin 1 [no silk label] (`GND`) ↔ Q4 pin 2 [no silk label] (`BASE4`)** — Neighbouring pads on `Q4` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q4 pin 2 [no silk label] (`BASE4`) ↔ Q4 pin 3 [no silk label] (`COL4`)** — Neighbouring pads on `Q4` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE4` vs `COL4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q5 pin 1 [no silk label] (`GND`) ↔ Q5 pin 2 [no silk label] (`BASE5`)** — Neighbouring pads on `Q5` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q5 pin 2 [no silk label] (`BASE5`) ↔ Q5 pin 3 [no silk label] (`COL5`)** — Neighbouring pads on `Q5` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE5` vs `COL5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q6 pin 1 [no silk label] (`GND`) ↔ Q6 pin 2 [no silk label] (`BASE6`)** — Neighbouring pads on `Q6` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q6 pin 2 [no silk label] (`BASE6`) ↔ Q6 pin 3 [no silk label] (`COL6`)** — Neighbouring pads on `Q6` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE6` vs `COL6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q7 pin 1 [no silk label] (`GND`) ↔ Q7 pin 2 [no silk label] (`BASE7`)** — Neighbouring pads on `Q7` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q7 pin 2 [no silk label] (`BASE7`) ↔ Q7 pin 3 [no silk label] (`COL7`)** — Neighbouring pads on `Q7` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE7` vs `COL7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q8 pin 1 [no silk label] (`GND`) ↔ Q8 pin 2 [no silk label] (`BASE8`)** — Neighbouring pads on `Q8` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q8 pin 2 [no silk label] (`BASE8`) ↔ Q8 pin 3 [no silk label] (`COL8`)** — Neighbouring pads on `Q8` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE8` vs `COL8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q9 pin 1 [no silk label] (`GND`) ↔ Q9 pin 2 [no silk label] (`BASE9`)** — Neighbouring pads on `Q9` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`GND` vs `BASE9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Q9 pin 2 [no silk label] (`BASE9`) ↔ Q9 pin 3 [no silk label] (`COL9`)** — Neighbouring pads on `Q9` (TO-92_Inline), 1.27 mm apart — this footprint's own minimum pad spacing is 1.27 mm — carrying DIFFERENT nets (`BASE9` vs `COL9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 2 (`GPIO20`) ↔ Right pin 3 (`GPIO10`)** — Neighbouring pads on `Right` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO20` vs `GPIO10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 3 (`GPIO10`) ↔ Right pin 4 (`GPIO9`)** — Neighbouring pads on `Right` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO10` vs `GPIO9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 4 (`GPIO9`) ↔ Right pin 5 (`GPIO8`)** — Neighbouring pads on `Right` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO9` vs `GPIO8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 5 (`GPIO8`) ↔ Right pin 6 (`GPIO7`)** — Neighbouring pads on `Right` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO8` vs `GPIO7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 6 (`GPIO7`) ↔ Right pin 7 (`GPIO6`)** — Neighbouring pads on `Right` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO7` vs `GPIO6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 7 (`GPIO6`) ↔ Right pin 8 (`GPIO5`)** — Neighbouring pads on `Right` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GPIO6` vs `GPIO5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.

</details>

---

## §4 — NET CONTINUITY

Meter in **continuity**. Each pair spans the **longest run** of its net, so the check crosses the whole net rather than a short hop. ⛔ This section is the only thing in the whole bring-up that can see an OPEN — from the MCU's side an open trace and a perfect trace are indistinguishable.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | IN5401 pin 1 | VIN pin 1 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | IN5401 pin 2 | J12 pin 2 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J1 pin 3 [no silk label] | Q1 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J11 pin 3 | Q11 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J5 pin 3 | Q5 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J7 pin 3 | Q7 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J8 pin 3 | Q8 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J9 pin 3 | Q9 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | LM7805 pin 3 | Left pin 8 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Left pin 1 | R1 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Left pin 2 | R2 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Left pin 3 | R3 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Left pin 4 | R4 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q10 pin 2 [no silk label] | R10 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q10 pin 3 [no silk label] | J10 pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q11 pin 2 [no silk label] | R11 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q12 pin 1 [no silk label] | VIN pin 2 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q12 pin 2 [no silk label] | R12 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q12 pin 3 [no silk label] | J12 pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q2 pin 2 [no silk label] | R2 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q2 pin 3 [no silk label] | J2 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q3 pin 2 [no silk label] | R3 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q3 pin 3 [no silk label] | J3 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q4 pin 2 [no silk label] | R4 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q4 pin 3 [no silk label] | J4 pin 3 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q6 pin 2 [no silk label] | R6 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q6 pin 3 [no silk label] | J6 pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Q7 pin 2 [no silk label] | R7 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R1 pin 2 [no silk label] | Q1 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R5 pin 1 | Left pin 5 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R5 pin 2 | Q5 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R8 pin 2 | Q8 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R9 pin 2 [no silk label] | Q9 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 2 | R12 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 3 | R11 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 4 | R10 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 5 | R9 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 6 | R8 pin 1 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 7 | R7 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 8 | R6 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **IN5401 pin 1 ↔ VIN pin 1** — `+12V_IN` (net 2) spans 2 pads; this pair is the FURTHEST APART (10.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **IN5401 pin 2 ↔ J12 pin 2** — `+12V` (net 3) spans 15 pads; this pair is the FURTHEST APART (128.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J1 pin 3 [no silk label] ↔ Q1 pin 3 [no silk label]** — `COL1` (net 30) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J11 pin 3 ↔ Q11 pin 3 [no silk label]** — `COL11` (net 40) spans 2 pads; this pair is the FURTHEST APART (12.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J5 pin 3 ↔ Q5 pin 3 [no silk label]** — `COL5` (net 34) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J7 pin 3 ↔ Q7 pin 3 [no silk label]** — `COL7` (net 36) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J8 pin 3 ↔ Q8 pin 3 [no silk label]** — `COL8` (net 37) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J9 pin 3 ↔ Q9 pin 3 [no silk label]** — `COL9` (net 38) spans 2 pads; this pair is the FURTHEST APART (12.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **LM7805 pin 3 ↔ Left pin 8** — `+5V` (net 4) spans 3 pads; this pair is the FURTHEST APART (32.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Left pin 1 ↔ R1 pin 1 [no silk label]** — `GPIO0` (net 6) spans 2 pads; this pair is the FURTHEST APART (22.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Left pin 2 ↔ R2 pin 1 [no silk label]** — `GPIO1` (net 7) spans 2 pads; this pair is the FURTHEST APART (28.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Left pin 3 ↔ R3 pin 1 [no silk label]** — `GPIO2` (net 8) spans 2 pads; this pair is the FURTHEST APART (33.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Left pin 4 ↔ R4 pin 1 [no silk label]** — `GPIO3` (net 9) spans 2 pads; this pair is the FURTHEST APART (39.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q10 pin 2 [no silk label] ↔ R10 pin 2 [no silk label]** — `BASE10` (net 27) spans 2 pads; this pair is the FURTHEST APART (6.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q10 pin 3 [no silk label] ↔ J10 pin 3** — `COL10` (net 39) spans 2 pads; this pair is the FURTHEST APART (12.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q11 pin 2 [no silk label] ↔ R11 pin 2 [no silk label]** — `BASE11` (net 28) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q12 pin 1 [no silk label] ↔ VIN pin 2** — `GND` (net 1) spans 29 pads; this pair is the FURTHEST APART (138.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q12 pin 2 [no silk label] ↔ R12 pin 2 [no silk label]** — `BASE12` (net 29) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q12 pin 3 [no silk label] ↔ J12 pin 3** — `COL12` (net 41) spans 2 pads; this pair is the FURTHEST APART (12.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q2 pin 2 [no silk label] ↔ R2 pin 2 [no silk label]** — `BASE2` (net 19) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q2 pin 3 [no silk label] ↔ J2 pin 3 [no silk label]** — `COL2` (net 31) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q3 pin 2 [no silk label] ↔ R3 pin 2 [no silk label]** — `BASE3` (net 20) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q3 pin 3 [no silk label] ↔ J3 pin 3 [no silk label]** — `COL3` (net 32) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q4 pin 2 [no silk label] ↔ R4 pin 2 [no silk label]** — `BASE4` (net 21) spans 2 pads; this pair is the FURTHEST APART (5.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q4 pin 3 [no silk label] ↔ J4 pin 3 [no silk label]** — `COL4` (net 33) spans 2 pads; this pair is the FURTHEST APART (12.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q6 pin 2 [no silk label] ↔ R6 pin 2 [no silk label]** — `BASE6` (net 23) spans 2 pads; this pair is the FURTHEST APART (6.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q6 pin 3 [no silk label] ↔ J6 pin 3** — `COL6` (net 35) spans 2 pads; this pair is the FURTHEST APART (12.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Q7 pin 2 [no silk label] ↔ R7 pin 2 [no silk label]** — `BASE7` (net 24) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R1 pin 2 [no silk label] ↔ Q1 pin 2 [no silk label]** — `BASE1` (net 18) spans 2 pads; this pair is the FURTHEST APART (6.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R5 pin 1 ↔ Left pin 5** — `GPIO4` (net 10) spans 2 pads; this pair is the FURTHEST APART (44.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R5 pin 2 ↔ Q5 pin 2 [no silk label]** — `BASE5` (net 22) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R8 pin 2 ↔ Q8 pin 2 [no silk label]** — `BASE8` (net 25) spans 2 pads; this pair is the FURTHEST APART (5.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R9 pin 2 [no silk label] ↔ Q9 pin 2 [no silk label]** — `BASE9` (net 26) spans 2 pads; this pair is the FURTHEST APART (6.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 2 ↔ R12 pin 1 [no silk label]** — `GPIO20` (net 17) spans 2 pads; this pair is the FURTHEST APART (111.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 3 ↔ R11 pin 1 [no silk label]** — `GPIO10` (net 16) spans 2 pads; this pair is the FURTHEST APART (101.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 4 ↔ R10 pin 1 [no silk label]** — `GPIO9` (net 15) spans 2 pads; this pair is the FURTHEST APART (90.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 5 ↔ R9 pin 1 [no silk label]** — `GPIO8` (net 14) spans 2 pads; this pair is the FURTHEST APART (79.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 6 ↔ R8 pin 1** — `GPIO7` (net 13) spans 2 pads; this pair is the FURTHEST APART (69.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 7 ↔ R7 pin 1 [no silk label]** — `GPIO6` (net 12) spans 2 pads; this pair is the FURTHEST APART (59.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 8 ↔ R6 pin 1 [no silk label]** — `GPIO5` (net 11) spans 2 pads; this pair is the FURTHEST APART (48.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.

</details>

---

## §5 — NETS THAT CANNOT BE TESTED HERE

⛔ **`BLOCKED` — listed, not dropped.** Each of these has fewer than two pads, so there is no second probe point. A net with one pad is either a legitimate single-ended signal or a net whose other end was never placed, and those look identical from the board file.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Left pin 6 | — no second pad — | continuity / beep | **NOT TESTABLE on a bare board** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **Left pin 6 ↔ — no second pad —** — `+3V3` (net 5) has 1 pad(s) in the board file. A continuity check needs two probe points; there is no second one.
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
| 1 | `GND` ⚡ | 29 | 100nF pin 2, 100uF pin 2, J1 pin 1, J10 pin 1, J11 pin 1, J12 pin 1, J2 pin 1, J3 pin 1, J4 pin 1, J5 pin 1, J6 pin 1, J7 pin 1, J8 pin 1, J9 pin 1… |
| 2 | `+12V_IN` ⚡ | 2 | IN5401 pin 1, VIN pin 1 |
| 3 | `+12V` ⚡ | 15 | 100uF pin 1, IN5401 pin 2, J1 pin 2, J10 pin 2, J11 pin 2, J12 pin 2, J2 pin 2, J3 pin 2, J4 pin 2, J5 pin 2, J6 pin 2, J7 pin 2, J8 pin 2, J9 pin … |
| 4 | `+5V` ⚡ | 3 | 100nF pin 1, LM7805 pin 3, Left pin 8 |
| 5 | `+3V3` ⚡ | 1 | Left pin 6 |
| 6 | `GPIO0` | 2 | Left pin 1, R1 pin 1 |
| 7 | `GPIO1` | 2 | Left pin 2, R2 pin 1 |
| 8 | `GPIO2` | 2 | Left pin 3, R3 pin 1 |
| 9 | `GPIO3` | 2 | Left pin 4, R4 pin 1 |
| 10 | `GPIO4` | 2 | Left pin 5, R5 pin 1 |
| 11 | `GPIO5` | 2 | R6 pin 1, Right pin 8 |
| 12 | `GPIO6` | 2 | R7 pin 1, Right pin 7 |
| 13 | `GPIO7` | 2 | R8 pin 1, Right pin 6 |
| 14 | `GPIO8` | 2 | R9 pin 1, Right pin 5 |
| 15 | `GPIO9` | 2 | R10 pin 1, Right pin 4 |
| 16 | `GPIO10` | 2 | R11 pin 1, Right pin 3 |
| 17 | `GPIO20` | 2 | R12 pin 1, Right pin 2 |
| 18 | `BASE1` | 2 | Q1 pin 2, R1 pin 2 |
| 19 | `BASE2` | 2 | Q2 pin 2, R2 pin 2 |
| 20 | `BASE3` | 2 | Q3 pin 2, R3 pin 2 |
| 21 | `BASE4` | 2 | Q4 pin 2, R4 pin 2 |
| 22 | `BASE5` | 2 | Q5 pin 2, R5 pin 2 |
| 23 | `BASE6` | 2 | Q6 pin 2, R6 pin 2 |
| 24 | `BASE7` | 2 | Q7 pin 2, R7 pin 2 |
| 25 | `BASE8` | 2 | Q8 pin 2, R8 pin 2 |
| 26 | `BASE9` | 2 | Q9 pin 2, R9 pin 2 |
| 27 | `BASE10` | 2 | Q10 pin 2, R10 pin 2 |
| 28 | `BASE11` | 2 | Q11 pin 2, R11 pin 2 |
| 29 | `BASE12` | 2 | Q12 pin 2, R12 pin 2 |
| 30 | `COL1` | 2 | J1 pin 3, Q1 pin 3 |
| 31 | `COL2` | 2 | J2 pin 3, Q2 pin 3 |
| 32 | `COL3` | 2 | J3 pin 3, Q3 pin 3 |
| 33 | `COL4` | 2 | J4 pin 3, Q4 pin 3 |
| 34 | `COL5` | 2 | J5 pin 3, Q5 pin 3 |
| 35 | `COL6` | 2 | J6 pin 3, Q6 pin 3 |
| 36 | `COL7` | 2 | J7 pin 3, Q7 pin 3 |
| 37 | `COL8` | 2 | J8 pin 3, Q8 pin 3 |
| 38 | `COL9` | 2 | J9 pin 3, Q9 pin 3 |
| 39 | `COL10` | 2 | J10 pin 3, Q10 pin 3 |
| 40 | `COL11` | 2 | J11 pin 3, Q11 pin 3 |
| 41 | `COL12` | 2 | J12 pin 3, Q12 pin 3 |

