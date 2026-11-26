<!-- BEGIN BEEPOUT-PROVENANCE (composeio.maker.beepout) -->
{
  "board": "button_matrix_4x4",
  "derived_from": "${MAKER_HOME}/pcb/button_matrix_4x4/button_matrix_4x4.kicad_pcb",
  "derived_from_sha256": "747a40e939d7fd19c0734359bf88851cbf8c02a4475fd26ebd0d4e76bda45e4d",
  "footprints": 50,
  "kicad_version": "20241229",
  "nets": 69,
  "pads": 200,
  "rows_by_category": {
    "adjacent-pad-isolation": 112,
    "net-continuity": 68,
    "rail-isolation": 3
  },
  "rows_by_state": {
    "DERIVED": 183
  },
  "rows_total": 183,
  "schema": "composeio.beepout/1"
}
<!-- END BEEPOUT-PROVENANCE -->

# `button_matrix_4x4` — BEEP-OUT SHEET (bare board)

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
| derived-from | `${MAKER_HOME}/pcb/button_matrix_4x4/button_matrix_4x4.kicad_pcb` |
| derived-from-sha256 | `747a40e939d7fd19c0734359bf88851cbf8c02a4475fd26ebd0d4e76bda45e4d` |
| kicad-version | `20241229` (generator 9.0) |
| generated-by | `composeio.maker.beepout` |
| generated-on | 2026-08-15 |
| nets / footprints / pads | 69 / 50 / 200 |

**Re-check that this sheet still describes the board in your hand:**

```bash
shasum -a 256 ${MAKER_HOME}/pcb/button_matrix_4x4/button_matrix_4x4.kicad_pcb
# must print: 747a40e939d7fd19c0734359bf88851cbf8c02a4475fd26ebd0d4e76bda45e4d
```

If it differs, the board was revised and **this sheet is stale** — regenerate it.

### Row counts

| category | rows |
|---|---|
| rail-isolation | 3 |
| adjacent-pad-isolation | 112 |
| net-continuity | 68 |
| **total** | **183** |

| derivation state | rows | meaning |
|---|---|---|
| `DERIVED` | 183 | follows from the netlist/geometry; mutate the source and the row changes |
| `UNVERIFIED` | 0 | a REAL check the board file cannot settle — **emitted, never omitted** |
| `BLOCKED` | 0 | cannot even be stated from this source; that itself is a finding |

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

- ⛔ 40 footprint(s) carry a Reference marked `(hide yes)` — their designator is NOT printed on the silkscreen, so those probe points cannot be found by label. Locate them by position against the board render. Affected: C2, Expansion (R), J1, J10, J11, J12, J13, J14, J15, J16, J2, J3, J4, J5, J6, J7…
- Geometry self-check: 156/156 netted pads lie within 0.6 mm of a copper endpoint on the same net (100%). This is an independent check on this tool's own pad-position arithmetic — copper endpoints are written by a different code path than pad positions, so agreement is evidence that does not share the parser's assumption.

---

## §1 — RAIL ISOLATION

**Power has never been applied. Nothing is seated.** Meter in **resistance, 2 MΩ**. ⛔ **NOT continuity.** These are the checks whose failure destroys parts on power-up, so they are first.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | GND @ Expansion (L) pin 2 | 3V3 @ Expansion (L) pin 3 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | GND @ Expansion (L) pin 2 | VCC @ Expansion (L) pin 1 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |
| ☐ | VCC @ Expansion (L) pin 1 | 3V3 @ Expansion (L) pin 3 | resistance, 2 MΩ | **settles > 100 kΩ (typically OL)** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **GND @ Expansion (L) pin 2 ↔ 3V3 @ Expansion (L) pin 3** — `GND` (net 1) and `3V3` (net 68) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. Continuity mode reports a threshold, not a value; a partial short reads as a pass. Read the SETTLED resistance.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and 3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **GND @ Expansion (L) pin 2 ↔ VCC @ Expansion (L) pin 1** — `GND` (net 1) and `VCC` (net 2) are DISTINCT nets in the board file, so no copper may join them. name matches a ground pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. Continuity mode reports a threshold, not a value; a partial short reads as a pass. Read the SETTLED resistance.
  - **ON FAIL:** < 1 kΩ is a SHORT between GND and VCC. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.
- **VCC @ Expansion (L) pin 1 ↔ 3V3 @ Expansion (L) pin 3** — `VCC` (net 2) and `3V3` (net 68) are DISTINCT nets in the board file, so no copper may join them. name matches a supply pattern; name matches a supply pattern.
  - ⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. Continuity mode reports a threshold, not a value; a partial short reads as a pass. Read the SETTLED resistance.
  - **ON FAIL:** < 1 kΩ is a SHORT between VCC and 3V3. ⛔ DO NOT APPLY POWER. Suspect a reversed electrolytic, a bridge under a regulator tab, or solder splash on the widest pour.

</details>

---

## §3 — ADJACENT-PAD ISOLATION

Meter in **continuity**. Adjacency is derived from each footprint's OWN minimum pad spacing — not from a guess about which parts are fine-pitch. Every pair below carries **different nets** and must **not** beep.

#### `2x100nf Caps`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 2x100nf Caps pin 1 (`VCC`) | 2x100nf Caps pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `C2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | C2 pin 1 [no silk label] (`VCC`) | C2 pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Expansion (L)`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Expansion (L) pin 1 (`VCC`) | Expansion (L) pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 2 (`GND`) | Expansion (L) pin 3 (`3V3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 3 (`3V3`) | Expansion (L) pin 4 (`CH_PC5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 4 (`CH_PC5`) | Expansion (L) pin 5 (`CH_PC6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 5 (`CH_PC6`) | Expansion (L) pin 6 (`CH_PC7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 6 (`CH_PC7`) | Expansion (L) pin 7 (`CH_PD2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 7 (`CH_PD2`) | Expansion (L) pin 8 (`CH_PD3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 8 (`CH_PD3`) | Expansion (L) pin 9 (`CH_PD4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (L) pin 9 (`CH_PD4`) | Expansion (L) pin 10 (`CH_PD5`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Expansion (R)`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Expansion (R) pin 1 [no silk label] (`VCC`) | Expansion (R) pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (R) pin 4 [no silk label] (`CH_PA1`) | Expansion (R) pin 5 [no silk label] (`CH_PA2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (R) pin 5 [no silk label] (`CH_PA2`) | Expansion (R) pin 6 [no silk label] (`CH_PC0`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (R) pin 6 [no silk label] (`CH_PC0`) | Expansion (R) pin 7 [no silk label] (`CH_PC3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Expansion (R) pin 7 [no silk label] (`CH_PC3`) | Expansion (R) pin 8 [no silk label] (`CH_PC4`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Host UART`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Host UART pin 1 (`VCC`) | Host UART pin 2 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Host UART pin 2 (`GND`) | Host UART pin 3 (`UART_TX`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Host UART pin 3 (`UART_TX`) | Host UART pin 4 (`UART_RX`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J1`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J1 pin 1 [no silk label] (`GND`) | J1 pin 2 [no silk label] (`LED1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J1 pin 2 [no silk label] (`LED1`) | J1 pin 3 [no silk label] (`BTN1`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J10`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J10 pin 1 [no silk label] (`GND`) | J10 pin 2 [no silk label] (`LED10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J10 pin 2 [no silk label] (`LED10`) | J10 pin 3 [no silk label] (`BTN10`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J11`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J11 pin 1 [no silk label] (`GND`) | J11 pin 2 [no silk label] (`LED11`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J11 pin 2 [no silk label] (`LED11`) | J11 pin 3 [no silk label] (`BTN11`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J12`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J12 pin 1 [no silk label] (`GND`) | J12 pin 2 [no silk label] (`LED12`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J12 pin 2 [no silk label] (`LED12`) | J12 pin 3 [no silk label] (`BTN12`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J13`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J13 pin 1 [no silk label] (`GND`) | J13 pin 2 [no silk label] (`LED13`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J13 pin 2 [no silk label] (`LED13`) | J13 pin 3 [no silk label] (`BTN13`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J14`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J14 pin 1 [no silk label] (`GND`) | J14 pin 2 [no silk label] (`LED14`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J14 pin 2 [no silk label] (`LED14`) | J14 pin 3 [no silk label] (`BTN14`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J15`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J15 pin 1 [no silk label] (`GND`) | J15 pin 2 [no silk label] (`LED15`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J15 pin 2 [no silk label] (`LED15`) | J15 pin 3 [no silk label] (`BTN15`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J16`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J16 pin 1 [no silk label] (`GND`) | J16 pin 2 [no silk label] (`LED16`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J16 pin 2 [no silk label] (`LED16`) | J16 pin 3 [no silk label] (`BTN16`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J2`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J2 pin 1 [no silk label] (`GND`) | J2 pin 2 [no silk label] (`LED2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J2 pin 2 [no silk label] (`LED2`) | J2 pin 3 [no silk label] (`BTN2`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J3`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J3 pin 1 [no silk label] (`GND`) | J3 pin 2 [no silk label] (`LED3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J3 pin 2 [no silk label] (`LED3`) | J3 pin 3 [no silk label] (`BTN3`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J4`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J4 pin 1 [no silk label] (`GND`) | J4 pin 2 [no silk label] (`LED4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J4 pin 2 [no silk label] (`LED4`) | J4 pin 3 [no silk label] (`BTN4`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J5`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J5 pin 1 [no silk label] (`GND`) | J5 pin 2 [no silk label] (`LED5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J5 pin 2 [no silk label] (`LED5`) | J5 pin 3 [no silk label] (`BTN5`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J6`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J6 pin 1 [no silk label] (`GND`) | J6 pin 2 [no silk label] (`LED6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J6 pin 2 [no silk label] (`LED6`) | J6 pin 3 [no silk label] (`BTN6`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J7`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J7 pin 1 [no silk label] (`GND`) | J7 pin 2 [no silk label] (`LED7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J7 pin 2 [no silk label] (`LED7`) | J7 pin 3 [no silk label] (`BTN7`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J8`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J8 pin 1 [no silk label] (`GND`) | J8 pin 2 [no silk label] (`LED8`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J8 pin 2 [no silk label] (`LED8`) | J8 pin 3 [no silk label] (`BTN8`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J9`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J9 pin 1 [no silk label] (`GND`) | J9 pin 2 [no silk label] (`LED9`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J9 pin 2 [no silk label] (`LED9`) | J9 pin 3 [no silk label] (`BTN9`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `J_GROVE`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | J_GROVE pin 1 [no silk label] (`VCC`) | J_GROVE pin 2 [no silk label] (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_GROVE pin 2 [no silk label] (`GND`) | J_GROVE pin 3 [no silk label] (`UART_TX`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | J_GROVE pin 3 [no silk label] (`UART_TX`) | J_GROVE pin 4 [no silk label] (`UART_RX`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Left`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Left pin 1 (`GND`) | Left pin 2 (`VCC`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 10 (`UART_RX`) | Left pin 11 (`UART_TX`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 2 (`VCC`) | Left pin 3 (`CH_PC5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 3 (`CH_PC5`) | Left pin 4 (`CH_PC6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 4 (`CH_PC6`) | Left pin 5 (`CH_PC7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 5 (`CH_PC7`) | Left pin 6 (`CH_PD2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 6 (`CH_PD2`) | Left pin 7 (`CH_PD3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 7 (`CH_PD3`) | Left pin 8 (`CH_PD4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 8 (`CH_PD4`) | Left pin 9 (`CH_PD5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Left pin 9 (`CH_PD5`) | Left pin 10 (`UART_RX`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `MCP23017 Top (L)`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | MCP23017 Top (L) pin 1 (`BTN9`) | MCP23017 Top (L) pin 2 (`BTN10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 12 (`SCL`) | MCP23017 Top (L) pin 13 (`SDA`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 17 (`GND`) | MCP23017 Top (L) pin 18 (`VCC`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 2 (`BTN10`) | MCP23017 Top (L) pin 3 (`BTN11`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 20 (`BTN_INT`) | MCP23017 Top (L) pin 21 (`BTN1`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 21 (`BTN1`) | MCP23017 Top (L) pin 22 (`BTN2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 22 (`BTN2`) | MCP23017 Top (L) pin 23 (`BTN3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 23 (`BTN3`) | MCP23017 Top (L) pin 24 (`BTN4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 24 (`BTN4`) | MCP23017 Top (L) pin 25 (`BTN5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 25 (`BTN5`) | MCP23017 Top (L) pin 26 (`BTN6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 26 (`BTN6`) | MCP23017 Top (L) pin 27 (`BTN7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 27 (`BTN7`) | MCP23017 Top (L) pin 28 (`BTN8`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 3 (`BTN11`) | MCP23017 Top (L) pin 4 (`BTN12`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 4 (`BTN12`) | MCP23017 Top (L) pin 5 (`BTN13`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 5 (`BTN13`) | MCP23017 Top (L) pin 6 (`BTN14`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 6 (`BTN14`) | MCP23017 Top (L) pin 7 (`BTN15`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 7 (`BTN15`) | MCP23017 Top (L) pin 8 (`BTN16`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 8 (`BTN16`) | MCP23017 Top (L) pin 9 (`VCC`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (L) pin 9 (`VCC`) | MCP23017 Top (L) pin 10 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `MCP23017 Top (R)`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | MCP23017 Top (R) pin 1 (`LDRV9`) | MCP23017 Top (R) pin 2 (`LDRV10`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 12 (`SCL`) | MCP23017 Top (R) pin 13 (`SDA`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 15 (`VCC`) | MCP23017 Top (R) pin 16 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 17 (`GND`) | MCP23017 Top (R) pin 18 (`VCC`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 2 (`LDRV10`) | MCP23017 Top (R) pin 3 (`LDRV11`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 21 (`LDRV1`) | MCP23017 Top (R) pin 22 (`LDRV2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 22 (`LDRV2`) | MCP23017 Top (R) pin 23 (`LDRV3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 23 (`LDRV3`) | MCP23017 Top (R) pin 24 (`LDRV4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 24 (`LDRV4`) | MCP23017 Top (R) pin 25 (`LDRV5`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 25 (`LDRV5`) | MCP23017 Top (R) pin 26 (`LDRV6`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 26 (`LDRV6`) | MCP23017 Top (R) pin 27 (`LDRV7`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 27 (`LDRV7`) | MCP23017 Top (R) pin 28 (`LDRV8`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 3 (`LDRV11`) | MCP23017 Top (R) pin 4 (`LDRV12`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 4 (`LDRV12`) | MCP23017 Top (R) pin 5 (`LDRV13`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 5 (`LDRV13`) | MCP23017 Top (R) pin 6 (`LDRV14`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 6 (`LDRV14`) | MCP23017 Top (R) pin 7 (`LDRV15`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 7 (`LDRV15`) | MCP23017 Top (R) pin 8 (`LDRV16`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 8 (`LDRV16`) | MCP23017 Top (R) pin 9 (`VCC`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | MCP23017 Top (R) pin 9 (`VCC`) | MCP23017 Top (R) pin 10 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |

#### `Right`

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | Right pin 1 (`GND`) | Right pin 2 (`CH_PC4`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 10 (`CH_PA1`) | Right pin 11 (`GND`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 2 (`CH_PC4`) | Right pin 3 (`CH_PC3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 3 (`CH_PC3`) | Right pin 4 (`SCL`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 4 (`SCL`) | Right pin 5 (`SDA`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 5 (`SDA`) | Right pin 6 (`CH_PC0`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 6 (`CH_PC0`) | Right pin 7 (`BTN_INT`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 7 (`BTN_INT`) | Right pin 8 (`3V3`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 8 (`3V3`) | Right pin 9 (`CH_PA2`) | continuity / beep | **NO BEEP (open)** | ____________ |
| ☐ | Right pin 9 (`CH_PA2`) | Right pin 10 (`CH_PA1`) | continuity / beep | **NO BEEP (open)** | ____________ |


<details><summary>Why each row exists, and what a failure means</summary>

- **2x100nf Caps pin 1 (`VCC`) ↔ 2x100nf Caps pin 2 (`GND`)** — Neighbouring pads on `2x100nf Caps` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **C2 pin 1 [no silk label] (`VCC`) ↔ C2 pin 2 [no silk label] (`GND`)** — Neighbouring pads on `C2` (C_Disc_D3.0mm_W1.6mm_P2.5mm), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 1 (`VCC`) ↔ Expansion (L) pin 2 (`GND`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 2 (`GND`) ↔ Expansion (L) pin 3 (`3V3`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `3V3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 3 (`3V3`) ↔ Expansion (L) pin 4 (`CH_PC5`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`3V3` vs `CH_PC5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 4 (`CH_PC5`) ↔ Expansion (L) pin 5 (`CH_PC6`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC5` vs `CH_PC6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 5 (`CH_PC6`) ↔ Expansion (L) pin 6 (`CH_PC7`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC6` vs `CH_PC7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 6 (`CH_PC7`) ↔ Expansion (L) pin 7 (`CH_PD2`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC7` vs `CH_PD2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 7 (`CH_PD2`) ↔ Expansion (L) pin 8 (`CH_PD3`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD2` vs `CH_PD3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 8 (`CH_PD3`) ↔ Expansion (L) pin 9 (`CH_PD4`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD3` vs `CH_PD4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (L) pin 9 (`CH_PD4`) ↔ Expansion (L) pin 10 (`CH_PD5`)** — Neighbouring pads on `Expansion (L)` (PinHeader_1x10_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD4` vs `CH_PD5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (R) pin 1 [no silk label] (`VCC`) ↔ Expansion (R) pin 2 [no silk label] (`GND`)** — Neighbouring pads on `Expansion (R)` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (R) pin 4 [no silk label] (`CH_PA1`) ↔ Expansion (R) pin 5 [no silk label] (`CH_PA2`)** — Neighbouring pads on `Expansion (R)` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PA1` vs `CH_PA2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (R) pin 5 [no silk label] (`CH_PA2`) ↔ Expansion (R) pin 6 [no silk label] (`CH_PC0`)** — Neighbouring pads on `Expansion (R)` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PA2` vs `CH_PC0`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (R) pin 6 [no silk label] (`CH_PC0`) ↔ Expansion (R) pin 7 [no silk label] (`CH_PC3`)** — Neighbouring pads on `Expansion (R)` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC0` vs `CH_PC3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Expansion (R) pin 7 [no silk label] (`CH_PC3`) ↔ Expansion (R) pin 8 [no silk label] (`CH_PC4`)** — Neighbouring pads on `Expansion (R)` (PinHeader_1x08_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC3` vs `CH_PC4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Host UART pin 1 (`VCC`) ↔ Host UART pin 2 (`GND`)** — Neighbouring pads on `Host UART` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Host UART pin 2 (`GND`) ↔ Host UART pin 3 (`UART_TX`)** — Neighbouring pads on `Host UART` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `UART_TX`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Host UART pin 3 (`UART_TX`) ↔ Host UART pin 4 (`UART_RX`)** — Neighbouring pads on `Host UART` (PinHeader_1x04_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`UART_TX` vs `UART_RX`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J1 pin 1 [no silk label] (`GND`) ↔ J1 pin 2 [no silk label] (`LED1`)** — Neighbouring pads on `J1` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J1 pin 2 [no silk label] (`LED1`) ↔ J1 pin 3 [no silk label] (`BTN1`)** — Neighbouring pads on `J1` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED1` vs `BTN1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J10 pin 1 [no silk label] (`GND`) ↔ J10 pin 2 [no silk label] (`LED10`)** — Neighbouring pads on `J10` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J10 pin 2 [no silk label] (`LED10`) ↔ J10 pin 3 [no silk label] (`BTN10`)** — Neighbouring pads on `J10` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED10` vs `BTN10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J11 pin 1 [no silk label] (`GND`) ↔ J11 pin 2 [no silk label] (`LED11`)** — Neighbouring pads on `J11` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J11 pin 2 [no silk label] (`LED11`) ↔ J11 pin 3 [no silk label] (`BTN11`)** — Neighbouring pads on `J11` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED11` vs `BTN11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J12 pin 1 [no silk label] (`GND`) ↔ J12 pin 2 [no silk label] (`LED12`)** — Neighbouring pads on `J12` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J12 pin 2 [no silk label] (`LED12`) ↔ J12 pin 3 [no silk label] (`BTN12`)** — Neighbouring pads on `J12` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED12` vs `BTN12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J13 pin 1 [no silk label] (`GND`) ↔ J13 pin 2 [no silk label] (`LED13`)** — Neighbouring pads on `J13` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED13`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J13 pin 2 [no silk label] (`LED13`) ↔ J13 pin 3 [no silk label] (`BTN13`)** — Neighbouring pads on `J13` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED13` vs `BTN13`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J14 pin 1 [no silk label] (`GND`) ↔ J14 pin 2 [no silk label] (`LED14`)** — Neighbouring pads on `J14` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED14`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J14 pin 2 [no silk label] (`LED14`) ↔ J14 pin 3 [no silk label] (`BTN14`)** — Neighbouring pads on `J14` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED14` vs `BTN14`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J15 pin 1 [no silk label] (`GND`) ↔ J15 pin 2 [no silk label] (`LED15`)** — Neighbouring pads on `J15` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED15`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J15 pin 2 [no silk label] (`LED15`) ↔ J15 pin 3 [no silk label] (`BTN15`)** — Neighbouring pads on `J15` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED15` vs `BTN15`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J16 pin 1 [no silk label] (`GND`) ↔ J16 pin 2 [no silk label] (`LED16`)** — Neighbouring pads on `J16` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED16`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J16 pin 2 [no silk label] (`LED16`) ↔ J16 pin 3 [no silk label] (`BTN16`)** — Neighbouring pads on `J16` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED16` vs `BTN16`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J2 pin 1 [no silk label] (`GND`) ↔ J2 pin 2 [no silk label] (`LED2`)** — Neighbouring pads on `J2` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J2 pin 2 [no silk label] (`LED2`) ↔ J2 pin 3 [no silk label] (`BTN2`)** — Neighbouring pads on `J2` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED2` vs `BTN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J3 pin 1 [no silk label] (`GND`) ↔ J3 pin 2 [no silk label] (`LED3`)** — Neighbouring pads on `J3` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J3 pin 2 [no silk label] (`LED3`) ↔ J3 pin 3 [no silk label] (`BTN3`)** — Neighbouring pads on `J3` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED3` vs `BTN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J4 pin 1 [no silk label] (`GND`) ↔ J4 pin 2 [no silk label] (`LED4`)** — Neighbouring pads on `J4` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J4 pin 2 [no silk label] (`LED4`) ↔ J4 pin 3 [no silk label] (`BTN4`)** — Neighbouring pads on `J4` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED4` vs `BTN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J5 pin 1 [no silk label] (`GND`) ↔ J5 pin 2 [no silk label] (`LED5`)** — Neighbouring pads on `J5` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J5 pin 2 [no silk label] (`LED5`) ↔ J5 pin 3 [no silk label] (`BTN5`)** — Neighbouring pads on `J5` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED5` vs `BTN5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J6 pin 1 [no silk label] (`GND`) ↔ J6 pin 2 [no silk label] (`LED6`)** — Neighbouring pads on `J6` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J6 pin 2 [no silk label] (`LED6`) ↔ J6 pin 3 [no silk label] (`BTN6`)** — Neighbouring pads on `J6` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED6` vs `BTN6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J7 pin 1 [no silk label] (`GND`) ↔ J7 pin 2 [no silk label] (`LED7`)** — Neighbouring pads on `J7` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J7 pin 2 [no silk label] (`LED7`) ↔ J7 pin 3 [no silk label] (`BTN7`)** — Neighbouring pads on `J7` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED7` vs `BTN7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J8 pin 1 [no silk label] (`GND`) ↔ J8 pin 2 [no silk label] (`LED8`)** — Neighbouring pads on `J8` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J8 pin 2 [no silk label] (`LED8`) ↔ J8 pin 3 [no silk label] (`BTN8`)** — Neighbouring pads on `J8` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED8` vs `BTN8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J9 pin 1 [no silk label] (`GND`) ↔ J9 pin 2 [no silk label] (`LED9`)** — Neighbouring pads on `J9` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`GND` vs `LED9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J9 pin 2 [no silk label] (`LED9`) ↔ J9 pin 3 [no silk label] (`BTN9`)** — Neighbouring pads on `J9` (JST_XH_B3B-XH-A_1x03_P2.50mm_Vertical), 2.50 mm apart — this footprint's own minimum pad spacing is 2.50 mm — carrying DIFFERENT nets (`LED9` vs `BTN9`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_GROVE pin 1 [no silk label] (`VCC`) ↔ J_GROVE pin 2 [no silk label] (`GND`)** — Neighbouring pads on `J_GROVE` (Conn_01x04_Pin_Header_2.00mm_Vertical), 2.00 mm apart — this footprint's own minimum pad spacing is 2.00 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_GROVE pin 2 [no silk label] (`GND`) ↔ J_GROVE pin 3 [no silk label] (`UART_TX`)** — Neighbouring pads on `J_GROVE` (Conn_01x04_Pin_Header_2.00mm_Vertical), 2.00 mm apart — this footprint's own minimum pad spacing is 2.00 mm — carrying DIFFERENT nets (`GND` vs `UART_TX`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **J_GROVE pin 3 [no silk label] (`UART_TX`) ↔ J_GROVE pin 4 [no silk label] (`UART_RX`)** — Neighbouring pads on `J_GROVE` (Conn_01x04_Pin_Header_2.00mm_Vertical), 2.00 mm apart — this footprint's own minimum pad spacing is 2.00 mm — carrying DIFFERENT nets (`UART_TX` vs `UART_RX`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 1 (`GND`) ↔ Left pin 2 (`VCC`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `VCC`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 10 (`UART_RX`) ↔ Left pin 11 (`UART_TX`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`UART_RX` vs `UART_TX`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 2 (`VCC`) ↔ Left pin 3 (`CH_PC5`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `CH_PC5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 3 (`CH_PC5`) ↔ Left pin 4 (`CH_PC6`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC5` vs `CH_PC6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 4 (`CH_PC6`) ↔ Left pin 5 (`CH_PC7`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC6` vs `CH_PC7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 5 (`CH_PC7`) ↔ Left pin 6 (`CH_PD2`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC7` vs `CH_PD2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 6 (`CH_PD2`) ↔ Left pin 7 (`CH_PD3`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD2` vs `CH_PD3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 7 (`CH_PD3`) ↔ Left pin 8 (`CH_PD4`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD3` vs `CH_PD4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 8 (`CH_PD4`) ↔ Left pin 9 (`CH_PD5`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD4` vs `CH_PD5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Left pin 9 (`CH_PD5`) ↔ Left pin 10 (`UART_RX`)** — Neighbouring pads on `Left` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PD5` vs `UART_RX`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 1 (`BTN9`) ↔ MCP23017 Top (L) pin 2 (`BTN10`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN9` vs `BTN10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 12 (`SCL`) ↔ MCP23017 Top (L) pin 13 (`SDA`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`SCL` vs `SDA`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 17 (`GND`) ↔ MCP23017 Top (L) pin 18 (`VCC`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `VCC`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 2 (`BTN10`) ↔ MCP23017 Top (L) pin 3 (`BTN11`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN10` vs `BTN11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 20 (`BTN_INT`) ↔ MCP23017 Top (L) pin 21 (`BTN1`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN_INT` vs `BTN1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 21 (`BTN1`) ↔ MCP23017 Top (L) pin 22 (`BTN2`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN1` vs `BTN2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 22 (`BTN2`) ↔ MCP23017 Top (L) pin 23 (`BTN3`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN2` vs `BTN3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 23 (`BTN3`) ↔ MCP23017 Top (L) pin 24 (`BTN4`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN3` vs `BTN4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 24 (`BTN4`) ↔ MCP23017 Top (L) pin 25 (`BTN5`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN4` vs `BTN5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 25 (`BTN5`) ↔ MCP23017 Top (L) pin 26 (`BTN6`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN5` vs `BTN6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 26 (`BTN6`) ↔ MCP23017 Top (L) pin 27 (`BTN7`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN6` vs `BTN7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 27 (`BTN7`) ↔ MCP23017 Top (L) pin 28 (`BTN8`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN7` vs `BTN8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 3 (`BTN11`) ↔ MCP23017 Top (L) pin 4 (`BTN12`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN11` vs `BTN12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 4 (`BTN12`) ↔ MCP23017 Top (L) pin 5 (`BTN13`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN12` vs `BTN13`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 5 (`BTN13`) ↔ MCP23017 Top (L) pin 6 (`BTN14`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN13` vs `BTN14`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 6 (`BTN14`) ↔ MCP23017 Top (L) pin 7 (`BTN15`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN14` vs `BTN15`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 7 (`BTN15`) ↔ MCP23017 Top (L) pin 8 (`BTN16`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN15` vs `BTN16`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 8 (`BTN16`) ↔ MCP23017 Top (L) pin 9 (`VCC`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN16` vs `VCC`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (L) pin 9 (`VCC`) ↔ MCP23017 Top (L) pin 10 (`GND`)** — Neighbouring pads on `MCP23017 Top (L)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 1 (`LDRV9`) ↔ MCP23017 Top (R) pin 2 (`LDRV10`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV9` vs `LDRV10`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 12 (`SCL`) ↔ MCP23017 Top (R) pin 13 (`SDA`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`SCL` vs `SDA`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 15 (`VCC`) ↔ MCP23017 Top (R) pin 16 (`GND`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 17 (`GND`) ↔ MCP23017 Top (R) pin 18 (`VCC`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `VCC`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 2 (`LDRV10`) ↔ MCP23017 Top (R) pin 3 (`LDRV11`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV10` vs `LDRV11`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 21 (`LDRV1`) ↔ MCP23017 Top (R) pin 22 (`LDRV2`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV1` vs `LDRV2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 22 (`LDRV2`) ↔ MCP23017 Top (R) pin 23 (`LDRV3`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV2` vs `LDRV3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 23 (`LDRV3`) ↔ MCP23017 Top (R) pin 24 (`LDRV4`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV3` vs `LDRV4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 24 (`LDRV4`) ↔ MCP23017 Top (R) pin 25 (`LDRV5`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV4` vs `LDRV5`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 25 (`LDRV5`) ↔ MCP23017 Top (R) pin 26 (`LDRV6`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV5` vs `LDRV6`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 26 (`LDRV6`) ↔ MCP23017 Top (R) pin 27 (`LDRV7`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV6` vs `LDRV7`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 27 (`LDRV7`) ↔ MCP23017 Top (R) pin 28 (`LDRV8`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV7` vs `LDRV8`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 3 (`LDRV11`) ↔ MCP23017 Top (R) pin 4 (`LDRV12`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV11` vs `LDRV12`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 4 (`LDRV12`) ↔ MCP23017 Top (R) pin 5 (`LDRV13`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV12` vs `LDRV13`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 5 (`LDRV13`) ↔ MCP23017 Top (R) pin 6 (`LDRV14`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV13` vs `LDRV14`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 6 (`LDRV14`) ↔ MCP23017 Top (R) pin 7 (`LDRV15`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV14` vs `LDRV15`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 7 (`LDRV15`) ↔ MCP23017 Top (R) pin 8 (`LDRV16`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV15` vs `LDRV16`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 8 (`LDRV16`) ↔ MCP23017 Top (R) pin 9 (`VCC`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`LDRV16` vs `VCC`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **MCP23017 Top (R) pin 9 (`VCC`) ↔ MCP23017 Top (R) pin 10 (`GND`)** — Neighbouring pads on `MCP23017 Top (R)` (DIP-28_W7.62mm), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`VCC` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 1 (`GND`) ↔ Right pin 2 (`CH_PC4`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`GND` vs `CH_PC4`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 10 (`CH_PA1`) ↔ Right pin 11 (`GND`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PA1` vs `GND`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 2 (`CH_PC4`) ↔ Right pin 3 (`CH_PC3`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC4` vs `CH_PC3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 3 (`CH_PC3`) ↔ Right pin 4 (`SCL`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC3` vs `SCL`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 4 (`SCL`) ↔ Right pin 5 (`SDA`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`SCL` vs `SDA`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 5 (`SDA`) ↔ Right pin 6 (`CH_PC0`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`SDA` vs `CH_PC0`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 6 (`CH_PC0`) ↔ Right pin 7 (`BTN_INT`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PC0` vs `BTN_INT`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 7 (`BTN_INT`) ↔ Right pin 8 (`3V3`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`BTN_INT` vs `3V3`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 8 (`3V3`) ↔ Right pin 9 (`CH_PA2`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`3V3` vs `CH_PA2`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.
- **Right pin 9 (`CH_PA2`) ↔ Right pin 10 (`CH_PA1`)** — Neighbouring pads on `Right` (PinHeader_1x11_P2.54mm_Vertical), 2.54 mm apart — this footprint's own minimum pad spacing is 2.54 mm — carrying DIFFERENT nets (`CH_PA2` vs `CH_PA1`).
  - **ON FAIL:** A beep is a SOLDER BRIDGE or an etch defect between two nets that must never meet.

</details>

---

## §4 — NET CONTINUITY

Meter in **continuity**. Each pair spans the **longest run** of its net, so the check crosses the whole net rather than a short hop. ⛔ This section is the only thing in the whole bring-up that can see an OPEN — from the MCU's side an open trace and a perfect trace are indistinguishable.

| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |
|---|---|---|---|---|---|
| ☐ | 16x100-150 ohm Resistors pin 1 | MCP23017 Top (R) pin 21 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 10 | Left pin 9 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 3 | Right pin 8 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 4 | Left pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 5 | Left pin 4 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 6 | Left pin 5 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 7 | Left pin 6 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 8 | Left pin 7 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (L) pin 9 | Left pin 8 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (R) pin 4 [no silk label] | Right pin 10 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (R) pin 5 [no silk label] | Right pin 9 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (R) pin 6 [no silk label] | Right pin 6 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (R) pin 7 [no silk label] | Right pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Expansion (R) pin 8 [no silk label] | Right pin 2 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Host UART pin 1 | MCP23017 Top (R) pin 15 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J1 pin 2 [no silk label] | 16x100-150 ohm Resistors pin 2 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J1 pin 3 [no silk label] | MCP23017 Top (L) pin 21 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J10 pin 2 [no silk label] | R10 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J10 pin 3 [no silk label] | MCP23017 Top (L) pin 2 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J11 pin 3 [no silk label] | MCP23017 Top (L) pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J12 pin 1 [no silk label] | Host UART pin 2 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J12 pin 3 [no silk label] | MCP23017 Top (L) pin 4 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J13 pin 3 [no silk label] | MCP23017 Top (L) pin 5 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J14 pin 3 [no silk label] | MCP23017 Top (L) pin 6 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J15 pin 2 [no silk label] | R15 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J15 pin 3 [no silk label] | MCP23017 Top (L) pin 7 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J16 pin 3 [no silk label] | MCP23017 Top (L) pin 8 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J2 pin 3 [no silk label] | MCP23017 Top (L) pin 22 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J3 pin 2 [no silk label] | R3 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J3 pin 3 [no silk label] | MCP23017 Top (L) pin 23 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J4 pin 2 [no silk label] | R4 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J4 pin 3 [no silk label] | MCP23017 Top (L) pin 24 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J5 pin 2 [no silk label] | R5 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J5 pin 3 [no silk label] | MCP23017 Top (L) pin 25 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J6 pin 3 [no silk label] | MCP23017 Top (L) pin 26 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J7 pin 3 [no silk label] | MCP23017 Top (L) pin 27 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J8 pin 2 [no silk label] | R8 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J8 pin 3 [no silk label] | MCP23017 Top (L) pin 28 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | J9 pin 3 [no silk label] | MCP23017 Top (L) pin 1 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Left pin 10 | Host UART pin 4 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Left pin 11 | Host UART pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top (R) pin 2 | R10 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top (R) pin 28 | R8 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | MCP23017 Top (R) pin 7 | R15 pin 1 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R11 pin 1 [no silk label] | MCP23017 Top (R) pin 3 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R11 pin 2 [no silk label] | J11 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R12 pin 1 [no silk label] | MCP23017 Top (R) pin 4 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R12 pin 2 [no silk label] | J12 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R13 pin 1 [no silk label] | MCP23017 Top (R) pin 5 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R13 pin 2 [no silk label] | J13 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R14 pin 1 [no silk label] | MCP23017 Top (R) pin 6 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R14 pin 2 [no silk label] | J14 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R16 pin 1 [no silk label] | MCP23017 Top (R) pin 8 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R16 pin 2 [no silk label] | J16 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R2 pin 1 [no silk label] | MCP23017 Top (R) pin 22 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R2 pin 2 [no silk label] | J2 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R3 pin 1 [no silk label] | MCP23017 Top (R) pin 23 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R4 pin 1 [no silk label] | MCP23017 Top (R) pin 24 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R5 pin 1 [no silk label] | MCP23017 Top (R) pin 25 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R6 pin 1 [no silk label] | MCP23017 Top (R) pin 26 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R6 pin 2 [no silk label] | J6 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R7 pin 1 [no silk label] | MCP23017 Top (R) pin 27 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R7 pin 2 [no silk label] | J7 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R9 pin 1 [no silk label] | MCP23017 Top (R) pin 1 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | R9 pin 2 [no silk label] | J9 pin 2 [no silk label] | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 4 | MCP23017 Top (L) pin 12 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 5 | MCP23017 Top (L) pin 13 | continuity / beep | **BEEP (short)** | ____________ |
| ☐ | Right pin 7 | MCP23017 Top (L) pin 20 | continuity / beep | **BEEP (short)** | ____________ |

<details><summary>Why each row exists, and what a failure means</summary>

- **16x100-150 ohm Resistors pin 1 ↔ MCP23017 Top (R) pin 21** — `LDRV1` (net 24) spans 2 pads; this pair is the FURTHEST APART (42.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 10 ↔ Left pin 9** — `CH_PD5` (net 67) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 3 ↔ Right pin 8** — `3V3` (net 68) spans 2 pads; this pair is the FURTHEST APART (25.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 4 ↔ Left pin 3** — `CH_PC5` (net 61) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 5 ↔ Left pin 4** — `CH_PC6` (net 62) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 6 ↔ Left pin 5** — `CH_PC7` (net 63) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 7 ↔ Left pin 6** — `CH_PD2` (net 64) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 8 ↔ Left pin 7** — `CH_PD3` (net 65) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (L) pin 9 ↔ Left pin 8** — `CH_PD4` (net 66) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (R) pin 4 [no silk label] ↔ Right pin 10** — `CH_PA1` (net 56) spans 2 pads; this pair is the FURTHEST APART (16.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (R) pin 5 [no silk label] ↔ Right pin 9** — `CH_PA2` (net 57) spans 2 pads; this pair is the FURTHEST APART (11.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (R) pin 6 [no silk label] ↔ Right pin 6** — `CH_PC0` (net 58) spans 2 pads; this pair is the FURTHEST APART (7.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (R) pin 7 [no silk label] ↔ Right pin 3** — `CH_PC3` (net 59) spans 2 pads; this pair is the FURTHEST APART (13.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Expansion (R) pin 8 [no silk label] ↔ Right pin 2** — `CH_PC4` (net 60) spans 2 pads; this pair is the FURTHEST APART (18.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Host UART pin 1 ↔ MCP23017 Top (R) pin 15** — `VCC` (net 2) spans 18 pads; this pair is the FURTHEST APART (82.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J1 pin 2 [no silk label] ↔ 16x100-150 ohm Resistors pin 2** — `LED1` (net 40) spans 2 pads; this pair is the FURTHEST APART (43.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J1 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 21** — `BTN1` (net 8) spans 2 pads; this pair is the FURTHEST APART (24.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J10 pin 2 [no silk label] ↔ R10 pin 2 [no silk label]** — `LED10` (net 49) spans 2 pads; this pair is the FURTHEST APART (40.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J10 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 2** — `BTN10` (net 17) spans 2 pads; this pair is the FURTHEST APART (60.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J11 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 3** — `BTN11` (net 18) spans 2 pads; this pair is the FURTHEST APART (66.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J12 pin 1 [no silk label] ↔ Host UART pin 2** — `GND` (net 1) spans 32 pads; this pair is the FURTHEST APART (90.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J12 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 4** — `BTN12` (net 19) spans 2 pads; this pair is the FURTHEST APART (74.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J13 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 5** — `BTN13` (net 20) spans 2 pads; this pair is the FURTHEST APART (48.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J14 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 6** — `BTN14` (net 21) spans 2 pads; this pair is the FURTHEST APART (51.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J15 pin 2 [no silk label] ↔ R15 pin 2 [no silk label]** — `LED15` (net 54) spans 2 pads; this pair is the FURTHEST APART (30.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J15 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 7** — `BTN15` (net 22) spans 2 pads; this pair is the FURTHEST APART (56.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J16 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 8** — `BTN16` (net 23) spans 2 pads; this pair is the FURTHEST APART (63.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J2 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 22** — `BTN2` (net 9) spans 2 pads; this pair is the FURTHEST APART (27.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J3 pin 2 [no silk label] ↔ R3 pin 2 [no silk label]** — `LED3` (net 42) spans 2 pads; this pair is the FURTHEST APART (29.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J3 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 23** — `BTN3` (net 10) spans 2 pads; this pair is the FURTHEST APART (34.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J4 pin 2 [no silk label] ↔ R4 pin 2 [no silk label]** — `LED4` (net 43) spans 2 pads; this pair is the FURTHEST APART (27.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J4 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 24** — `BTN4` (net 11) spans 2 pads; this pair is the FURTHEST APART (43.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J5 pin 2 [no silk label] ↔ R5 pin 2 [no silk label]** — `LED5` (net 44) spans 2 pads; this pair is the FURTHEST APART (31.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J5 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 25** — `BTN5` (net 12) spans 2 pads; this pair is the FURTHEST APART (54.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J6 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 26** — `BTN6` (net 13) spans 2 pads; this pair is the FURTHEST APART (64.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J7 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 27** — `BTN7` (net 14) spans 2 pads; this pair is the FURTHEST APART (46.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J8 pin 2 [no silk label] ↔ R8 pin 2 [no silk label]** — `LED8` (net 47) spans 2 pads; this pair is the FURTHEST APART (23.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J8 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 28** — `BTN8` (net 15) spans 2 pads; this pair is the FURTHEST APART (49.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **J9 pin 3 [no silk label] ↔ MCP23017 Top (L) pin 1** — `BTN9` (net 16) spans 2 pads; this pair is the FURTHEST APART (55.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Left pin 10 ↔ Host UART pin 4** — `UART_RX` (net 6) spans 3 pads; this pair is the FURTHEST APART (24.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Left pin 11 ↔ Host UART pin 3** — `UART_TX` (net 5) spans 3 pads; this pair is the FURTHEST APART (27.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top (R) pin 2 ↔ R10 pin 1 [no silk label]** — `LDRV10` (net 33) spans 2 pads; this pair is the FURTHEST APART (19.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top (R) pin 28 ↔ R8 pin 1 [no silk label]** — `LDRV8` (net 31) spans 2 pads; this pair is the FURTHEST APART (50.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **MCP23017 Top (R) pin 7 ↔ R15 pin 1 [no silk label]** — `LDRV15` (net 38) spans 2 pads; this pair is the FURTHEST APART (21.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R11 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 3** — `LDRV11` (net 34) spans 2 pads; this pair is the FURTHEST APART (19.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R11 pin 2 [no silk label] ↔ J11 pin 2 [no silk label]** — `LED11` (net 50) spans 2 pads; this pair is the FURTHEST APART (37.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R12 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 4** — `LDRV12` (net 35) spans 2 pads; this pair is the FURTHEST APART (19.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R12 pin 2 [no silk label] ↔ J12 pin 2 [no silk label]** — `LED12` (net 51) spans 2 pads; this pair is the FURTHEST APART (38.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R13 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 5** — `LDRV13` (net 36) spans 2 pads; this pair is the FURTHEST APART (20.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R13 pin 2 [no silk label] ↔ J13 pin 2 [no silk label]** — `LED13` (net 52) spans 2 pads; this pair is the FURTHEST APART (46.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R14 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 6** — `LDRV14` (net 37) spans 2 pads; this pair is the FURTHEST APART (20.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R14 pin 2 [no silk label] ↔ J14 pin 2 [no silk label]** — `LED14` (net 53) spans 2 pads; this pair is the FURTHEST APART (37.7 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R16 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 8** — `LDRV16` (net 39) spans 2 pads; this pair is the FURTHEST APART (21.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R16 pin 2 [no silk label] ↔ J16 pin 2 [no silk label]** — `LED16` (net 55) spans 2 pads; this pair is the FURTHEST APART (27.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R2 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 22** — `LDRV2` (net 25) spans 2 pads; this pair is the FURTHEST APART (41.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R2 pin 2 [no silk label] ↔ J2 pin 2 [no silk label]** — `LED2` (net 41) spans 2 pads; this pair is the FURTHEST APART (35.4 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R3 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 23** — `LDRV3` (net 26) spans 2 pads; this pair is the FURTHEST APART (40.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R4 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 24** — `LDRV4` (net 27) spans 2 pads; this pair is the FURTHEST APART (41.1 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R5 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 25** — `LDRV5` (net 28) spans 2 pads; this pair is the FURTHEST APART (42.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R6 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 26** — `LDRV6` (net 29) spans 2 pads; this pair is the FURTHEST APART (44.3 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R6 pin 2 [no silk label] ↔ J6 pin 2 [no silk label]** — `LED6` (net 45) spans 2 pads; this pair is the FURTHEST APART (38.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R7 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 27** — `LDRV7` (net 30) spans 2 pads; this pair is the FURTHEST APART (47.0 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R7 pin 2 [no silk label] ↔ J7 pin 2 [no silk label]** — `LED7` (net 46) spans 2 pads; this pair is the FURTHEST APART (33.5 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R9 pin 1 [no silk label] ↔ MCP23017 Top (R) pin 1** — `LDRV9` (net 32) spans 2 pads; this pair is the FURTHEST APART (18.8 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **R9 pin 2 [no silk label] ↔ J9 pin 2 [no silk label]** — `LED9` (net 48) spans 2 pads; this pair is the FURTHEST APART (46.9 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 4 ↔ MCP23017 Top (L) pin 12** — `SCL` (net 4) spans 4 pads; this pair is the FURTHEST APART (61.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 5 ↔ MCP23017 Top (L) pin 13** — `SDA` (net 3) spans 4 pads; this pair is the FURTHEST APART (61.2 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.
- **Right pin 7 ↔ MCP23017 Top (L) pin 20** — `BTN_INT` (net 7) spans 2 pads; this pair is the FURTHEST APART (44.6 mm), chosen so the check crosses the longest run of the net rather than a short hop.
  - **ON FAIL:** No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace from the MCU side — no firmware diagnostic can find this. It is found here or it is found as a mystery symptom later.

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
| 1 | `GND` ⚡ | 32 | 2x100nf Caps pin 2, C2 pin 2, Expansion (L) pin 2, Expansion (R) pin 2, Host UART pin 2, J1 pin 1, J10 pin 1, J11 pin 1, J12 pin 1, J13 pin 1, J14 … |
| 2 | `VCC` ⚡ | 18 | 2x100nf Caps pin 1, 2x10k Resistors pin 1, 2x10k Resistors pin 2, 2x4.7k Resistors pin 2, C2 pin 1, Expansion (L) pin 1, Expansion (R) pin 1, Host … |
| 3 | `SDA` | 4 | 2x4.7k Resistors pin 1, MCP23017 Top (L) pin 13, MCP23017 Top (R) pin 13, Right pin 5 |
| 4 | `SCL` | 4 | MCP23017 Top (L) pin 12, MCP23017 Top (R) pin 12, R_SCL pin 1, Right pin 4 |
| 5 | `UART_TX` | 3 | Host UART pin 3, J_GROVE pin 3, Left pin 11 |
| 6 | `UART_RX` | 3 | Host UART pin 4, J_GROVE pin 4, Left pin 10 |
| 7 | `BTN_INT` | 2 | MCP23017 Top (L) pin 20, Right pin 7 |
| 8 | `BTN1` | 2 | J1 pin 3, MCP23017 Top (L) pin 21 |
| 9 | `BTN2` | 2 | J2 pin 3, MCP23017 Top (L) pin 22 |
| 10 | `BTN3` | 2 | J3 pin 3, MCP23017 Top (L) pin 23 |
| 11 | `BTN4` | 2 | J4 pin 3, MCP23017 Top (L) pin 24 |
| 12 | `BTN5` | 2 | J5 pin 3, MCP23017 Top (L) pin 25 |
| 13 | `BTN6` | 2 | J6 pin 3, MCP23017 Top (L) pin 26 |
| 14 | `BTN7` | 2 | J7 pin 3, MCP23017 Top (L) pin 27 |
| 15 | `BTN8` | 2 | J8 pin 3, MCP23017 Top (L) pin 28 |
| 16 | `BTN9` | 2 | J9 pin 3, MCP23017 Top (L) pin 1 |
| 17 | `BTN10` | 2 | J10 pin 3, MCP23017 Top (L) pin 2 |
| 18 | `BTN11` | 2 | J11 pin 3, MCP23017 Top (L) pin 3 |
| 19 | `BTN12` | 2 | J12 pin 3, MCP23017 Top (L) pin 4 |
| 20 | `BTN13` | 2 | J13 pin 3, MCP23017 Top (L) pin 5 |
| 21 | `BTN14` | 2 | J14 pin 3, MCP23017 Top (L) pin 6 |
| 22 | `BTN15` | 2 | J15 pin 3, MCP23017 Top (L) pin 7 |
| 23 | `BTN16` | 2 | J16 pin 3, MCP23017 Top (L) pin 8 |
| 24 | `LDRV1` | 2 | 16x100-150 ohm Resistors pin 1, MCP23017 Top (R) pin 21 |
| 25 | `LDRV2` | 2 | MCP23017 Top (R) pin 22, R2 pin 1 |
| 26 | `LDRV3` | 2 | MCP23017 Top (R) pin 23, R3 pin 1 |
| 27 | `LDRV4` | 2 | MCP23017 Top (R) pin 24, R4 pin 1 |
| 28 | `LDRV5` | 2 | MCP23017 Top (R) pin 25, R5 pin 1 |
| 29 | `LDRV6` | 2 | MCP23017 Top (R) pin 26, R6 pin 1 |
| 30 | `LDRV7` | 2 | MCP23017 Top (R) pin 27, R7 pin 1 |
| 31 | `LDRV8` | 2 | MCP23017 Top (R) pin 28, R8 pin 1 |
| 32 | `LDRV9` | 2 | MCP23017 Top (R) pin 1, R9 pin 1 |
| 33 | `LDRV10` | 2 | MCP23017 Top (R) pin 2, R10 pin 1 |
| 34 | `LDRV11` | 2 | MCP23017 Top (R) pin 3, R11 pin 1 |
| 35 | `LDRV12` | 2 | MCP23017 Top (R) pin 4, R12 pin 1 |
| 36 | `LDRV13` | 2 | MCP23017 Top (R) pin 5, R13 pin 1 |
| 37 | `LDRV14` | 2 | MCP23017 Top (R) pin 6, R14 pin 1 |
| 38 | `LDRV15` | 2 | MCP23017 Top (R) pin 7, R15 pin 1 |
| 39 | `LDRV16` | 2 | MCP23017 Top (R) pin 8, R16 pin 1 |
| 40 | `LED1` | 2 | 16x100-150 ohm Resistors pin 2, J1 pin 2 |
| 41 | `LED2` | 2 | J2 pin 2, R2 pin 2 |
| 42 | `LED3` | 2 | J3 pin 2, R3 pin 2 |
| 43 | `LED4` | 2 | J4 pin 2, R4 pin 2 |
| 44 | `LED5` | 2 | J5 pin 2, R5 pin 2 |
| 45 | `LED6` | 2 | J6 pin 2, R6 pin 2 |
| 46 | `LED7` | 2 | J7 pin 2, R7 pin 2 |
| 47 | `LED8` | 2 | J8 pin 2, R8 pin 2 |
| 48 | `LED9` | 2 | J9 pin 2, R9 pin 2 |
| 49 | `LED10` | 2 | J10 pin 2, R10 pin 2 |
| 50 | `LED11` | 2 | J11 pin 2, R11 pin 2 |
| 51 | `LED12` | 2 | J12 pin 2, R12 pin 2 |
| 52 | `LED13` | 2 | J13 pin 2, R13 pin 2 |
| 53 | `LED14` | 2 | J14 pin 2, R14 pin 2 |
| 54 | `LED15` | 2 | J15 pin 2, R15 pin 2 |
| 55 | `LED16` | 2 | J16 pin 2, R16 pin 2 |
| 56 | `CH_PA1` | 2 | Expansion (R) pin 4, Right pin 10 |
| 57 | `CH_PA2` | 2 | Expansion (R) pin 5, Right pin 9 |
| 58 | `CH_PC0` | 2 | Expansion (R) pin 6, Right pin 6 |
| 59 | `CH_PC3` | 2 | Expansion (R) pin 7, Right pin 3 |
| 60 | `CH_PC4` | 2 | Expansion (R) pin 8, Right pin 2 |
| 61 | `CH_PC5` | 2 | Expansion (L) pin 4, Left pin 3 |
| 62 | `CH_PC6` | 2 | Expansion (L) pin 5, Left pin 4 |
| 63 | `CH_PC7` | 2 | Expansion (L) pin 6, Left pin 5 |
| 64 | `CH_PD2` | 2 | Expansion (L) pin 7, Left pin 6 |
| 65 | `CH_PD3` | 2 | Expansion (L) pin 8, Left pin 7 |
| 66 | `CH_PD4` | 2 | Expansion (L) pin 9, Left pin 8 |
| 67 | `CH_PD5` | 2 | Expansion (L) pin 10, Left pin 9 |
| 68 | `3V3` ⚡ | 2 | Expansion (L) pin 3, Right pin 8 |

