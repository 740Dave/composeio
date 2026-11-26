# R503 bring-up wiring — six wires to an ESP32-S3 SuperMini

**Date:** 2026-08-16
**Host:** ESP32-S3 SuperMini, USB serial number = `$R503_EXPECT_SERIAL` (host overlay)
**Firmware:** `firmware/r503_probe` — UART1 @ **57600 8N1**, never changed
**Purpose:** the first measurements ever taken of this part in this project —
`[CXC-R503-EV-25]`, `[CXC-R503-EV-03]` / `[CXC-R503-EV-15]`, and `[CXC-R503-EV-02]`.

> Everything below marked ⛔ is a rule that can destroy a part, a module or a
> measurement. Everything marked ⚠ is a thing nobody has established yet.

---

## 0. ⛔ Before you touch a wire — three rules, in order

### ⛔ Rule 1 — TIE 3.3VT (pin 6) TO **3.3 V**. NEVER 5 V.

The manual permits 3.3VT anywhere in `DC3—5V`. Do not take it up on that offer.

`[CXC-R503-WAKEUP-TRACKS-3V3T-INFERRED]`: WAKEUP's HIGH level is a **strong
inference** — not a datasheet fact — to track the **3.3VT** rail rather than VCC.
The manual says only *"the touch signal output is CMOS output, and the output
voltage is roughly the same as the input voltage"*, in a paragraph entirely about
the touch supply, and §6.5 requires WAKEUP to work **while VCC is off**, which is
only possible if WAKEUP is powered from 3.3VT.

So feeding 3.3VT at 5 V very likely puts a **~5 V CMOS high on WAKEUP**.
**The ESP32-S3's IO is 3.3 V and is NOT 5 V tolerant.** That is an overdrive into
the MCU. There is no VIH, VIL, VOH, VOL, drive-strength or 5 V-tolerance figure
anywhere in any revision (`[CXC-R503-LOGIC-LEVELS-ABSENT]`, established by grep:
zero numeric hits), so there is nothing to fall back on.

**3.3VT to 3.3 V. Both rails at 3.3 V. No exceptions until `[CXC-R503-EV-04]` is
measured with a scope.**

### ⛔ Rule 2 — NEVER identify a pin by its wire colour.

The manual states it outright: **"The line order has nothing to do with color."**
Pin 1 is identified in the document by a *photograph* — the weakest evidence form
in the whole spec, and the reason `[CXC-R503-EV-02]` exists.

**Meter it. See §2 below. Do it before anything is powered.**

### ⛔ Rule 3 — no hot-plug, and a two-second dwell.

- `[CXC-R503-RAILS]`: *"Electrical components … may be damaged if you insert or
  remove the cable (with the electric plug) when the cable is live."*
  **Make every connection with the S3 unplugged from USB.**
- *"After the power is turned off, the power must be turned on at least two seconds
  later."* If you power-cycle the module, **wait two full seconds.**

---

## 1. The connections — six wires, and TXD/RXD **cross**

| R503 pin | function | goes to | note |
|---|---|---|---|
| **1** | **VCC** | 3.3 V — see §4 before choosing the source | ⛔ 3.3 V. A point value; no tolerance band exists in any revision. |
| **2** | **GND** | S3 `GND` | common ground, always. |
| **3** | **TXD** (module out) | S3 **GPIO 5** (probe RX) | ⛔ **CROSSES.** |
| **4** | **RXD** (module in) | S3 **GPIO 4** (probe TX) | ⛔ **CROSSES.** |
| **5** | **WAKEUP** | ⛔ **LEAVE UNCONNECTED** | see §3 — this removes the overdrive hazard entirely. |
| **6** | **3.3VT** | **3.3 V** — the *same* 3.3 V as pin 1 | ⛔ **NEVER 5 V.** Rule 1. |

### ⛔ UART CROSSES. CAN DOES NOT. The habit from your last link is wrong here.

`TX → RX` and `RX → TX`. Module TXD talks, S3 listens; S3 talks, module RXD
listens. If you wired a CAN bus recently, that one is `H↔H, L↔L` straight through —
**two wires between two devices, opposite rules.** This is a documented way to lose
an afternoon on this fleet.

**The symptom of getting it wrong is not an error.** It is `timeout_no_bytes`
forever, which looks exactly like a dead module.

### ⚠ The GPIO numbers are MY choice, and you must confirm them.

I chose **GPIO 4 (TX)** and **GPIO 5 (RX)** to avoid every hazard class on an S3:
not strapping pins (0, 3, 45, 46), not the USB D±/native-USB pins (19, 20), not the
SPI flash/PSRAM pins (26–32), not the UART0 pads (43, 44). They are broken out on
essentially every S3 mini board.

⚠ **I have NO verified knowledge of this specific SuperMini's silkscreen and will
not assert one from memory.** Read the board.

**If GPIO 4/5 are not brought out, you do NOT need a reflash.** Retarget at runtime:

```
python3 probe/r503probe.py raw --help          # (any command opens the link)
```
…or directly, with any two pins you can actually reach:
```
# in a serial terminal on the S3, or add a `pins` call before your commands:
> 1 pins 4 5
```
The probe reports the pins it is *actually* bound to in every `ping` response, so
the instrument tells you what it is connected to rather than you remembering.

---

## 2. ⛔ EV-02 FIRST — identify pin 1 with your meter, not my code

**`[CXC-R503-EV-02]` is your multimeter, not software.** It cannot be done from the
host and it must be done before power.

**Procedure** — with the module **unpowered and disconnected**:

1. Orient the connector so its latch/ramp faces a known direction. Write down which.
2. GND is the anchor: meter continuity from each contact to the module's ground —
   the metal shell/frame is usually the accessible ground reference.
3. Confirm the order counting from the photographed pin-1 end is:
   **`1 VCC, 2 GND, 3 TXD, 4 RXD, 5 WAKEUP, 6 3.3VT`**

**What a wrong answer looks like:** if the order comes out reversed, the photograph
was read from the wrong face and **every subsequent connection is mirrored** — VCC
would land on 3.3VT and TXD on WAKEUP. This is the single cheapest test in the whole
bring-up and it gates all the others.

**Record it.** The slot wants: what you did, what you expected, what you observed,
the date, and your name.

---

## 3. WAKEUP (pin 5) — leave it unconnected, and the hazard disappears

`[CXC-R503-PRE-WAKEUP-USE]` is **OPEN** and this bring-up does not need it answered.

The probe **polls**. It never sleeps and never power-gates. So:

- **Leave pin 5 unconnected.** ⛔ This removes the 5 V overdrive risk *entirely*,
  not partially — an unconnected pin cannot overdrive anything.
- What it costs: the ~2 µA standby mode and the manual's §6.5 low-power pattern.
  Neither is needed for these three measurements.
- ⚠ Note what the **vendor** does: its own reference circuit does **not** wire TOUCH
  to a GPIO. It buffers TOUCH into a power-latch path
  (`[CXC-R503-REFERENCE-CIRCUIT]`). The people who made the part reached the same
  conclusion independently. **If WAKEUP is ever used, buffer it — do not wire it
  straight to a GPIO.**

⛔ **Do not build the manual's §VII reference circuit as drawn.** It is for the
**5 V R307**, its header symbol is labelled `R307` with pin 1 = `5V`, and building
it puts 5 V on an R503's 3.3 V pin 1. The manual says so in one sentence under the
drawing.

---

## 4. ⚠ THE POWER QUESTION — and it is DAVID'S CALL, not mine

### What is unknown

⚠ **What the SuperMini's 3V3 regulator can actually source is UNKNOWN.**
The board is roughly 22 × 18 mm and its LDO is sized for the module, not for a
peripheral. I have no verified knowledge of its part number and will not assert one.

⚠ And the load is **not determinable from the datasheet**
(`[CXC-R503-CURRENT-UNDERSPECIFIED]`):

| figure | value | status |
|---|---|---|
| working current (acquisition) | 20 mA | stated |
| standby (VCC off, 3.3VT on) | ~2 µA | stated |
| **powered but idle** | — | ⛔ **does not exist in any revision** |
| **peak / inrush / surge** | — | ⛔ **does not exist in any revision** |
| **ring LED current** | — | ⛔ **does not exist in any revision** |

⛔ **Whether the 20 mA includes the ring LED is not determinable from the
document** — and EV-25 drives the ring **always-on, seven times in a row**. A rail
sized to 20 mA may be sized to the wrong number for exactly the operation this
bring-up runs longest.

### Option A — ⭐ ARCHITECT'S LEAN: give the R503 its own 3.3 V regulator

Feed a small 3.3 V LDO from the SuperMini's **5 V / VBUS** pin, and common **only
ground**. Three reasons, each already documented:

1. `[CXC-R503-RAILS]` — the module's power input is **unregulated** and *"directly
   supplied to the image sensor and decoding chip"*. Board-side filtering is the
   host's job, and a shared rail puts the sensor's noise on the MCU's supply.
2. `[CXC-R503-CURRENT-UNDERSPECIFIED]` — a shared LDO is being sized against
   **three numbers that do not exist**.
3. The manual's §6.5 low-power pattern **hard-switches VCC** repeatedly. Switching a
   rail the MCU also runs on is a brown-out generator.

**What it costs:** one more part and one more thing to wire, on a bench whose whole
appeal was six wires.

### Option B — share the SuperMini's 3V3 pin

Simplest. Six wires and nothing else. Acceptable **for these three measurements
only**, because they are short, and because a brown-out here costs a re-run rather
than a part.

⚠ If you take Option B and see the S3 reset, re-enumerate, or the probe report
`timeout_no_bytes` **only while the ring is lit**, that is the rail sagging — not a
protocol fault. Move to Option A before drawing any conclusion about the module.

### ⛔ What is NOT at stake

Nothing above the seam changes either way. This is a **power topology** choice, not
a contract choice, and `[CXC-R503-EV-06]` / `[CXC-R503-EV-07]` measure the same
quantities under both. **It can be decided late and cheaply — but it must be decided
by you, not defaulted into.**

---

## 5. ⛔ What this bring-up will NOT do — and why

| not done | why |
|---|---|
| **`[CXC-R503-EV-17]` — the baud probe** | ⛔ **SACRIFICIAL MODULE ONLY.** It is the only slot that can permanently **brick** a part, and **no recovery from a bricked module is documented in any revision**. David has **not yet marked a sacrificial unit**, and the three modules carry **no serial and no part number**, so they are **mutually indistinguishable by inspection**. |
| **`[CXC-R503-EV-08]` — RXD 5 V overdrive** | ⛔ Deliberately overdrives an input. Same sacrificial requirement, same unmet precondition. |
| **any baud change at all** | ⛔ `[CXC-R503-BAUD-SELF-CONTRADICTS]`: every revision contradicts itself on legal rates and one shipped driver exposes **seven forbidden values**. There is **no `SetSysPara` helper anywhere in this firmware or CLI**. 57600 is compiled in and never touched. |

⛔ **Before EV-17 or EV-08 is ever run: mark one module physically.** Marking is a
human step; nothing in software can do it and nothing in software can verify it was
done. An unmarked brick-test victim gets rotated back into the working set and then
fails mysteriously.

---

## 6. The sequence — exactly what to run

**With the S3 unplugged from USB:**

1. Meter the connector — **§2, `[CXC-R503-EV-02]`**. Record it.
2. Make the six connections per §1. **WAKEUP unconnected.** **3.3VT to 3.3 V.**
3. Double-check TXD/RXD **cross**.

**Then plug the S3 in and, from the repo root:**

```bash
# 0. Prove the instrument before trusting it. No R503 involvement at all.
python3 probe/r503probe.py selftest
python3 probe/r503probe.py negctl        # ⛔ run this with the R503 UNPLUGGED

# 1. Sanity: identity, build stamp, and the pins it is ACTUALLY bound to.
python3 probe/r503probe.py ping

# 2. Is anything on the line? A single 0x55 here is the documented power-on byte.
python3 probe/r503probe.py listen 3000

# 3. ⛔ EV-25 — firmware version, then the ring colour census.
#    This one STOPS AND ASKS YOU to name each colour. Have eyes on the ring.
python3 probe/r503probe.py --observer "David" ev25

# 4. EV-03 + EV-15 — the model string, and the byte count settled on the wire.
python3 probe/r503probe.py --observer "David" ev03
```

Session logs land in `probe/sessions/` in the evidence-slot shape: question,
expected, observed, observer, timestamp.

⛔ **Each session log carries a `provenance` record stating whether ANY frame was
ever parsed from hardware.** If it says `NO MODULE EVER ANSWERED`, the answers in
that log were given against a silent line and **must not fill an evidence slot** —
however plausible they read.

---

## 7. If nothing answers

Work down this list. **Do not change two things at once.**

1. `python3 probe/r503probe.py ping` — does the probe itself answer? If not, the
   problem is the S3 or the USB cable, not the module.
2. `python3 probe/r503probe.py selftest` — 16/16? If yes, framing and checksums are
   not the problem.
3. **Swap TXD and RXD.** This is the single most likely fault and it is free.
4. `python3 probe/r503probe.py listen 5000`, then power-cycle the module (**two
   second dwell**). A `55` appearing here proves TX-from-module, ground and baud in
   one shot, **with no firmware of mine involved** — the module's own handshake byte.
   ⭐ **This is the substrate test. Run it before suspecting any code.**
5. Confirm the GPIOs against the silkscreen; retarget with `pins` if wrong.
6. Meter VCC and 3.3VT **at the module connector**, not at the S3 pin header.

⛔ **Never let a wiring fault and new firmware be suspects at the same time.**
Step 4 excludes the wiring using a byte the module sends unbidden. Do it first.
