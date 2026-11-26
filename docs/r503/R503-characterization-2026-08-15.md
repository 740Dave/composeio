# R503 / R503-M22 — device characterization

**Produced** 2026-08-15 by a `composeio-hardware-architect` dispatch, from documents only. **No hardware was
touched.** Every hardware-observable claim here is UNVERIFIED; §7 is the honest ledger of that.

**Primary source, provenance verified first-hand:**
`R503_R503-M22_user_manual_V1.4.1_2023-09.pdf`, SHA-256
`fa5d4105f6754ddf0ebcbdb5875a78106a4f147534c3c3bbc0385bc5477dfc22`. The member
`R503 R503-M22 fingerprint module user manual-V1.4.1.pdf` was re-extracted from the manufacturer bundle ZIP and
hashed: **byte-identical**. Extracted text hashes `46de4651…`, matching `SOURCES.md`.

**Citation convention:** `p.N` = printed folio of V1.4.1 (PDF sheet = folio + 5). `L###` = line in the
extracted text. Cross-revision cites are `L###` into that revision's extracted-text file.

**Epistemic convention (per `[DX-SPEC-CARRIES-ITS-EVIDENCE]`):** every claim is labelled DATASHEET FACT,
INFERENCE, or DERIVED FACT. An inference is never promoted to a fact.

---

## 1. Electrical envelope

### 1.1 Pinout — six pins (DATASHEET FACT, V1.4.1 p.3, L240–256)

| Pin | Name | Manual's words | Rail |
|---|---|---|---|
| 1 | Power Supply | `DC3.3V` | VCC |
| 2 | GND | `Signal ground. Connected to power ground.` | — |
| 3 | TXD | `Data output. TTL logical level` | tracks VCC (INFERENCE) |
| 4 | RXD | `Data input. TTL logical level` | tracks VCC (INFERENCE) |
| 5 | WAKEUP | `Finger Detection Signal. Standby-high level, have finger-output low level.` | **tracks 3.3VT (INFERENCE)** |
| 6 | 3.3VT | `Touch induction power supply, DC3—5V` | separate rail |

- Pin 1 / pin 6 ends are identified **only by a photograph** on p.3. No keying drawing, no mechanical outline,
  no polarity mark in text.
- ⛔ **TRAP, stated by the manual (p.3, L253–256):** `The line order has nothing to do with color.`
  **Never infer pinout from wire colour.**
- The connector is on a **captive pigtail**, not the module body — so the connector designation describes the
  *free end of the cable*; a PCB needs the mating half.

### 1.2 Rails

| Rail | Value | Source |
|---|---|---|
| VCC (pin 1) | `DC 3.3V` — **a point value; no tolerance band appears anywhere** | p.1 L178; p.3; p.4 L300 |
| 3.3VT (pin 6) | `DC3—5V` (also `3V~5V`, p.3 L272–273) | p.3 L255, L272 |
| Ripple | `recommended that the ripple noise not exceed 50mV (peak-to-peak)` — **a recommendation, not a limit** | p.4 L314 |

**3.3VT is genuinely independent of VCC.** The intended low-power mode (§6.5, p.43, L2151–2153) is explicitly
*VCC off, 3.3VT on*: `the host can cut off the main power supply of the module (can not cut off the
touch-sensitive power supply). Once the module detects a finger, it outputs a signal in the IRQ signal.`

### 1.3 ⛔ The WAKEUP level trap — and its correct epistemic status

Manual's exact words (p.3, L278–279):
> `the touch signal output is CMOS output, and the output voltage is roughly the same as the input voltage.`

- **STATED:** CMOS drive; output ≈ "the input voltage".
- **NOT STATED:** *which* input voltage. The manual never writes "the same as 3.3VT".
- **INFERENCE (strong, two independent grounds):** (a) the sentence sits inside a paragraph entirely about the
  touch supply, three lines after `@vt =3.3V`; (b) §6.5 requires WAKEUP to work *while VCC is off*, which is
  only possible if WAKEUP is powered from 3.3VT.
- **CONSEQUENCE:** feeding 3.3VT at 5 V — which the manual permits — very likely puts a ~5 V CMOS high on
  WAKEUP. On a 3.3 V ESP32 GPIO that is an overdrive. **Tie 3.3VT to 3.3 V unless measured otherwise.**

⚠ `SOURCES.md` §6 and the project launcher both assert 3.3VT-tracking as a flat **fact**. It is an
**inference**. Downgraded here deliberately: the conclusion is probably right, the epistemic status was wrong.

### 1.4 Current

| Condition | Figure | Source |
|---|---|---|
| Working (acquisition) | **20 mA** | p.1 L180 |
| Touch standby (VCC off, 3.3VT on) | **~2 µA average** @ 3.3 V | p.1 L183–185; p.3 L273 |
| Idle but powered | **ABSENT** — no third figure exists | — |
| Peak / inrush / surge | **ABSENT** | — |
| Ring-LED contribution | **ABSENT — no LED current figure of any kind** | — |

⚠ **Whether the 20 mA includes the ring LED is NOT DETERMINABLE from the document.** There are exactly two
current numbers and neither is qualified by LED state — and AutoEnroll drives the ring for its entire
multi-second sequence. Bench question.

### 1.5 Logic levels — a total absence

Interface is `UART(3.3V TTL logical level)` (p.1 L178–179). **No VIH, VIL, VOH, VOL, drive strength, leakage,
or 5 V-tolerance figure appears anywhere in any revision** (verified by grep — zero numeric hits). There is no
stated pull-up requirement for WAKEUP either.

### 1.6 Other electrical rules (p.4)

- `After the power is turned off, the power must be turned on at least two seconds later.` (L306–307) — a hard
  floor on wake latency if VCC is power-gated per §6.5.
- `Electrical components … may be damaged if you insert or remove the cable (with the electric plug) when the
  cable is live.` (L302–304) — **no hot-plug.**
- `the power input … is directly supplied to the image sensor and decoding chip` (L311–312) — the rail is
  **unregulated internally**, which is why the ripple recommendation exists.

### 1.7 ⛔ The reference circuit is for a DIFFERENT, 5 V PART

§VII "Reference Circuit" (p.44) is the only schematic in the document, and its header symbol is labelled
**`R307`** with pin 1 = **5V**. The manual says so plainly (L2187–2189):
`Please refer to the circuit form of R307 (R307 is 5V power supply).`

**Building it as drawn puts 5 V on an R503's pin 1.**

Readable from the raster: `D1 1N5819` series-input diode, `Q1 MD301` P-channel high-side switch with
`R1 100K` gate pull-up, two `HT7333` LDOs, `Q4 8050D` NPN driven from an MCU GPIO to latch power on, and a
`Q3 8050D`/`Q2 8550D` pair between the module's TOUCH pin and the latch.

**Note what the vendor does NOT do: it does not wire TOUCH straight to a GPIO** — it buffers it into the
power-latch path.

⚠ Net topology beyond designators and header labels **could not be reliably traced at this raster
resolution** and is not claimed.

---

## 2. The wire protocol

### 2.1 Framing (DATASHEET FACT, p.8, L445–484)

```
[Header 2B][Address 4B][PID 1B][Length 2B][Content …][Checksum 2B]
 0xEF01      0xFFFFFFFF                                arithmetic sum
```

- **Byte order: big-endian throughout.** `For multi-bytes, the high byte precedes the low byte (for example, a
  2 bytes 00 06 indicates 0006, not 0600).` (L447–448). ⚠ **Absent from V1.1** — added in V1.2.1.
- **Address:** default `0xFFFFFFFF`; `at wrong adder value, module will reject to transfer.`
- **PID:** `01H` command · `02H` data (`shall not appear alone … must follow command packet or acknowledge
  packet`) · `07H` acknowledge · `08H` end-of-data.
- **Length = content + 2** (`the length of package content … plus the length of Checksum( 2 bytes)`).
- **Checksum:** `The arithmetic sum of package identifier, package length and all package contents. Overflowing
  bits are omitted.` — i.e. **PID + both length bytes + every content byte, mod 2^16. Header and address are
  NOT included.**

**VERIFIED, not merely read:** the checksum rule was recomputed against **all 21 worked examples** in the
document (18 fixed-command checksums + the three V1.4.1 hex traces). **All 21 recompute exactly.** e.g.
AuraLedConfig `EF 01 FF FF FF FF 01 00 07 35 01 50 06 00 | 00 94` → `01+00+07+35+01+50+06+00 = 0x94` ✓.
This is a falsifiable, passed test.

⚠ **Length-semantics contradiction.** `Max length is 256 bytes` (L476) conflicts with the data-packet-size
parameter allowing 256-byte payloads (L384), which would require Length = 258. §6.1.2 (L1816–1819) works only
the 128-byte case (`each packet length is 139 bytes` = 2+4+1+2+128+2). **The 256 case is never worked. Do not
assume 256-byte packets are legal.**

### 2.2 Confirmation codes — 35 entries (V1.4.1 pp.9–10, L534–571)

`00` complete · `01` packet receive error · `02` no finger · `03` enroll failed · `06` image over-disorderly ·
`07` too few feature points / image too small · `08` finger doesn't match · `09` no matching finger found ·
`0A` combine failed · `0B` PageID beyond library · `0C` read/invalid template · `0D` upload template error ·
`0E` can't receive following packets · `0F` upload image error · `10` delete failed · `11` clear library failed ·
`13` **wrong password** · `15` no valid primary image · `18` flash write error · `19` "No definition error" ·
`1A` invalid register number · `1B` bad register config · `1C` wrong notepad page · `1D` comms-port operation
failed · `1F` library full · `20` address incorrect · `21` **password must be verified** · `22` template empty ·
`24` library empty · `26` **timeout** · `27` fingerprint already exists · `29` sensor hardware error ·
`FC` unsupported command · `FD` hardware error · `FE` execution failure · `others: system reserved`.

⚠ **`0x28` is USED but is in NO revision's table.** §6.2.1 (p.38, L1952, L1962): `returns 28H, indicating that
there is a correlation between the current fingerprint feature and the previous feature` and `Whether it
returns 28H or 08H…`. The same paragraphs invoke a `registration logic` setting of 1 or 2 that **is defined
nowhere in any revision** — it is not AutoEnroll's Config1–4.

### 2.3 Command set — 35 instructions (V1.4.1 p.9, L490–521; each cross-checked against its body)

| Class | Opcodes |
|---|---|
| (a) capture / enrollment | `0x01` GetImg · `0x28` GetImageEx · `0x02` GenChar · `0x05` RegModel · `0x31` AutoEnroll |
| (b) matching / search | `0x03` Match · `0x04` Search · `0x32` AutoIdentify |
| (c) template store/load/delete/transfer | `0x06` Store · `0x07` LoadChar · `0x08` UpChar · `0x09` DownChar · `0x0C` DeletChar · `0x0D` Empty · `0x0A` UpImage · `0x0B` DownImage · `0x1D` TempleteNum · `0x1F` ReadIndexTable |
| (d) device config / system | `0x0E` SetSysPara · `0x0F` ReadSysPara · `0x12` SetPwd · `0x13` VfyPwd · `0x14` GetRandomCode · `0x15` SetAdder · `0x16` ReadInfPage · `0x18` WriteNotepad · `0x19` ReadNotepad · `0x30` Cancel · `0x36` CheckSensor · `0x39` GetAlgVer · `0x3A` GetFwVer · `0x3C` ReadProdInfo · `0x3D` SoftRst · `0x40` HandShake |
| (e) ring LED | `0x35` AuraLedConfig |

**⛔ Opcode corrections — widely-deployed drivers get these wrong:**
- **HandShake is `0x40`** (p.26 L1311). Not 0x53, not 0x20, not 0x3D.
- **SoftRst is `0x3D`** (p.27 L1361).
- **GetAlgVer `0x39`, GetFwVer `0x3A`** (pp.15–16), in that order.
- **`0x1B` HiSpeedSearch does NOT exist on the R503** (legacy ZFM/R30x). Nor do `0x33`, `0x50`–`0x53`.
- **`0x17` PortControl existed in V1.1 ONLY** and was removed — see §5.3.

### 2.4 Ring LED — `0x35` AuraLedConfig (p.27, L1379–1423)

Payload `[0x35][Ctrl][Speed][ColorIndex][Times]`, Length `0x0007`.

- **Ctrl:** `0x01` breathing · `0x02` flashing · `0x03` always on · `0x04` always off · `0x05` gradually on ·
  `0x06` gradually off
- **Speed:** `0x00-0xff, 256 gears, Minimum 5s cycle.` Effective only for breathing/flashing/gradual.
- **ColorIndex — SEVEN:** `0x01` Red · `0x02` Blue · `0x03` Purple · `0x04` Green · `0x05` Yellow ·
  `0x06` Cyan · `0x07` White
- **Times:** `0` = infinite, `1-255`. Effective only for breathing and flashing.

### 2.5 Internal inconsistencies in the protocol chapter

- ⚠ **`ReadProdInfo 0x3C` (p.16, L831–853) contradicts itself by exactly 4 bytes.** Ack Length is `0x0031` = 49,
  and Length = content + 2, so content = 47 = 1 confirmation byte + **46** data bytes. But the column header
  says **50 bytes** and the field rows sum to 16+4+8+2+8+2+2+2+2+4 = **50**. The rows *excluding* the final
  `Other / 4 / System Reserved` sum to **46** — exactly what the Length field implies.
  **Cross-revision resolution:** V1.2.1 said `46 byte` and was self-consistent; **V1.4 changed the column to 50
  and added the 4-byte reserved row without touching the Length field.** A driver must be told which to expect.
- `ReadSysPara` basic-parameter table (p.13, L700–707) keeps column headers `Offset (word)` / `Size (word)`
  while offsets are in words (0,1,2,3,4,6,7) and sizes are in bytes (2,2,2,2,4,2,2) — **two units in one table.**
- `GetAlgVer` and `GetFwVer` ack tables (L792–795, L813–816) both label their 32-byte field `Random number` in
  the column header — copy-paste from GetRandomCode.
- **`ModelBuffer`** is referenced by `Match` (L1013) and `UpChar` (L1192) and **defined nowhere**; the Buffer
  section defines only `ImageBuffer` and `CharBuffer[1:6]`.

---

## 3. Timing

**There is no per-command timing table in V1.4.1.** No latency figure exists for GenImg, GenChar, Match,
Search, RegModel, Store, LoadChar, UpChar or DownChar. Everything the document gives:

| Quantity | Figure | Source |
|---|---|---|
| Power-on to ready | `about 50ms for initialization. During this period, the module can't accept commands` | p.4 L293–294 |
| "Starting time" | `≤50ms` | p.1 L192 |
| Image acquisition | `Image acquiring time  <0.2s` | p.1 L188 |
| Feature generation | `Generate feature point time  < 500ms` | p.1 L192 |
| 1:N search | `Matching Time  1:N<10ms/Fingerprint` | p.1 L181 — **AMBIGUOUS, see below** |
| 1:1 match | **ABSENT** — `1:1 and 1:N` declared (L180), only the 1:N figure given | — |
| Template read / write | **ABSENT** — nothing for Store, LoadChar, UpChar, DownChar, or flash write | — |
| Touch-detect response | `The maximum response time of the touch function is about 120mS @vt =3.3V.` | p.3 L277 |
| Touch recalibration | `the recalibration period is about 4.0sec` | p.3 L278 |
| Power-cycle dwell | `the power must be turned on at least two seconds later` | p.4 L307 |
| Ring-LED animation | `Minimum 5s cycle` | p.27 L1401 |
| AutoEnroll/AutoIdentify no-finger exit | **10 s or 15 s — CONTRADICTORY, see §4.2** | — |

### 3.1 ⛔ The `1:N<10ms/Fingerprint` ambiguity — quoted, NOT resolved

Written exactly as `1:N<10ms/Fingerprint` (p.1 L181) and **never restated or qualified**. Read as "a 1:N sweep
completes in <10 ms" it is one thing; read literally as "<10 ms **per fingerprint**" it is up to
200 × 10 ms = **2 s** at full library. **Nothing in V1.4.1 disambiguates this, and the figure is absent from
V1.1 entirely.** Do not encode either reading.

**This single ambiguity is load-bearing for the fleet architecture** — it decides whether sensor-side matching
is instantaneous or a two-second wait. See §7 item 13.

### 3.2 One-shot verify budget — INFERENCE, clearly marked

**INFERENCE:** summing published worst cases, GetImg (<0.2 s) + GenChar (<0.5 s) + Search (<10 ms optimistic)
≈ **≤0.71 s, dominated by imaging, with the database search a rounding error.** This contradicts the folk
belief that the *match* is the slow part — **capture is the cost.**

⚠ This is arithmetic on three separately-specified worst cases; the manual never performs it, and worst cases
do not necessarily co-occur. **Order-of-magnitude expectation, not a spec.**

### 3.3 Ring-LED dwells inside AutoEnroll — INFERENCE

The §6.3 flowchart (p.41) mandates `Yellow light turn on 500ms`, `Green light flash 200ms`, `Red light turn on
500ms`, `Green light turn on 500ms`, repeated `6 times`. **INFERENCE:** if those dwells block, they impose a
floor of ~6 × 700 ms ≈ **4.2 s** on AutoEnroll before any finger-presentation time. **The manual never says
whether the LED dwells block the state machine.** Bench question.

---

## 4. Protocol traps

### 4.1 ⛔ The stray `0x55` — CONFIRMED, and it fires after SOFT-RESET too

Three places in V1.4.1 say it (verified by grep — exactly three occurrences):

1. **p.4, L293–296:** `After completing the initialization, the module will immediately send a byte (0x55) to
   the upper computer, indicating that the module can work normally…`
2. **p.26, under HandShake 0x40, L1326–1328:** `after the module is powered on, 0x55 will be automatically sent
   as a handshake sign.`
3. **p.27, under SoftRst 0x3D, L1374–1375:** `After module reset, 0x55 will be automatically sent as a
   handshake sign.`

**This is the ONLY unsolicited emission in the protocol.** The general rule (p.9, L526–527) is
`Commands shall only be sent from upper computer to the Module, and the Module acknowledges the commands.`

It is a **bare byte, not a packet** — no header, no address, no checksum. A parser requiring `EF 01` sees one
byte of garbage; one reading fixed-length is **permanently phase-shifted.**

⛔ **Design consequence beyond what was previously known: it fires after `0x3D SoftRst` as well as at power-on,
so a driver that handles it only at boot will desync the first time it soft-resets.**

### 4.2 ⛔ AutoEnroll / AutoIdentify intermediate-ack STREAM — CONFIRMED, with two corrections

**Correction 1 — the config index DIFFERS between the two commands.**
- **AutoEnroll `0x31` (p.28, L1443):** `Config3: Whether the module return the status in the critical step`
- **AutoIdentify `0x32` (p.31, L1591):** `Config1 Whether the module returns to the status in key steps`

**Correction 2 — the timeout is contradictory, and `SOURCES.md` §5 states it wrongly.** `SOURCES.md` says
">10s (AutoEnroll) or >15s (AutoIdentify)". That is not what the document says. **Both** commands' prose says
10 s and **both** commands' flowcharts say 15 s:

| | prose | flowchart |
|---|---|---|
| AutoEnroll 0x31 | p.28 L1437: `more than 10 seconds … automatically exits` | §6.3 p.41 L2063–2066: `more than 15 seconds, error code 26` |
| AutoIdentify 0x32 | p.31 L1584, p.32 L1623: `more than 10 seconds … exit` | §6.4 p.42 L2119–2124: `more than 15 seconds … return error code 26` |

**The split is prose-vs-flowchart, not command-vs-command. Unresolved in V1.2.1, V1.4 and V1.4.1 alike.**

**AutoEnroll stream shape (p.29–30, L1489–1557):** with Config3=1 the module emits **15 acknowledge packets**,
each Length `0x0005` = `[conf][step][fingerID]` + checksum, step running `0x01`→`0x0F`. Steps `0x01`/`0x02`
collect+generate 1st … `0x0B`/`0x0C` 6th, then `0x0D` repeat-check, `0x0E` merge, `0x0F` **storage template**
(the only packet where fingerID is meaningful — `modelID`).

Worked example, every checksum verified:
```
Send:   EF 01 FF FF FF FF 01 00 08 31 C8 00 01 01 01 01 05
Return: … 07 00 05 00 01 00 00 0D   (×15, steps 01…0F)
```
Decodes as ModelID `0xC8` (auto-assign; `0xC8-0xFF is automatic filling`), Config1=0, Config2=1, Config3=1,
Config4=1. ⚠ **ModelID is 1 byte here** (table L1451–1461 declares 1 byte; Length `0x0008` = 1 cmd + 5 params +
2 checksum confirms arithmetically). **Other Grow parts with >255 capacity use 2 bytes — do not carry that
assumption in.**

`If the status of return key step is set to 0 during registration, only returned the the last acknowledge
packet.` (L1560–1561)

**AutoIdentify stream shape (p.32, L1631–1645):** with Config1=1, **3 packets**, each Length `0x0008` =
`[conf][step][position 2B][score 2B]`, steps 1 collect / 2 generate / 3 search. Verified:
```
Send:   EF 01 FF FF FF FF 01 00 08 32 03 00 C8 01 01 01 08
Return: … 07 00 08 00 03 00 00 00 3D 00 4F   (ModelID 0x0000, score 0x003D)
```

### 4.3 ⛔ AutoIdentify's stream can be UNBOUNDED — INFERENCE, and it sizes your buffers

Config2 is `Number of fingerprint search error` (p.31–32, L1614–1621): `0: the operation of image collection
and feature search is carried out all the time … (0 means the cycle continues until the matching ID is found or
the power is cut off). 1-0xFF: … repeat the previous operations for 1-0xff times.`

**INFERENCE:** with Config1=1 *and* Config2≠1, the module must re-emit step 1/2/3 packets on each retry, so a
single `0x32` yields 3 × retries acknowledge packets — and at **Config2=0, with a finger continuously present
but never matching, an unbounded stream.** The manual shows only the 3-packet success case and never
illustrates a retry. The 10 s no-finger exit bounds the *finger-absent* case but **not** the
*finger-present-never-matches* case.

**This is a fixed-size-ring-buffer hazard and it is falsifiable on a bench in five minutes.**

**Stream terminator — INFERENCE from the documented tables:** there is no explicit end-of-stream marker. The
terminating condition appears to be `confirmation != 0x00` **OR** `step == 0x0F` (AutoEnroll) / `step == 0x03`
(AutoIdentify). Documented stream-ending error codes: AutoEnroll `01 07 0a 0b 1f 22 26 27`; AutoIdentify
`01 09 0b 22 24 26`.

### 4.4 Two further one-command-one-response breakers

- **Data-packet bursts.** `UpImage 0x0A`, `DownImage 0x0B`, `UpChar 0x08`, `DownChar 0x09`, `ReadInfPage 0x16`
  all return an ack **then N data packets** (PID `0x02`, final `0x08`): `the module sends the acknowledge packet
  first, and then sends several data packet.` **Packet size is a negotiated system parameter (32/64/128/256;
  `The value is 128 Bytes before delivery`), so the packet COUNT depends on device config, not on the command.**
- **`Cancel 0x30`** (p.25) exists precisely because operations run long, and the **Busy flag** (system status
  register bit 0, p.6 L394: `1: system is executing commands; 0: system is free`) is readable via
  `ReadSysPara 0x0F`. Together these are the manual's poll-don't-block affordances.
  ⚠ **What happens if you send a command while Busy is NOWHERE addressed in any revision.** Bench question.

---

## 5. Cross-revision contradictions

All four R503 revisions checked. **No winner picked — both sides reported.**

### 5.1 ⛔ Connector part number

| Revision | Exterior interface | Serial-comm section |
|---|---|---|
| V1.1 (2019.06) | `MX1.0--6P Thread:M25` (L82) | `MX1.0--6P` (L90) |
| V1.2.1 (2022.03) | `MX1.0--6P Thread:M25` (L112) | `MX1.0--6P` (L121) |
| V1.4 (2023.07) | `MX1.0--6P Thread:M25` (L216), `MX1.0--6P Thread:M22` (L224) | `MX1.0--6P` (L233) |
| **V1.4.1 (2023.09)** | **`SH1.0--6P Thread:M25`** (L219), **`SH1.0--6P Thread:M22`** (L229) | **`SH1.0--6P`** (L239) |

**The change is consistent across all three occurrences in V1.4.1 and is NOT in the changelog** — V1.4.1's own
row lists only `1. Updated Security level,Checksum` and `2. Added example instruction for AuraLedConfig,
AutoEnroll, AutoIdentify`. **A physically consequential figure changed silently.**

⛔ **The pitch question cannot be settled from paper, and the two available sources disagree on what "MX1.0"
even denotes.** `SOURCES.md` §4 reads it as *JST-SH 1.0 mm vs Molex PicoBlade 1.25 mm* — a real pitch change.
The cross-revision sweep read it as *both 1.0 mm, different families*. **There is no standard Molex part called
"MX1.0"; Molex PicoBlade is 1.25 mm.** Both readings are defensible from the string alone.
**This is a calipers question, not a document question.**

**Cross-part corroboration (context, NOT resolution)**, from the same manufacturer bundle: **R503S V1.2.1
(2023.09) → `SH1.0--6P`**; **R503Pro V1.1.1 (2026.05) → `SH1.0--6P`**; but **R503-5V V1.3.1 (2023.09) →
`MX1.25--6P`**, and that part's pin 1 is `DC 5V`. Three connector designations across parts all sold as
"R503-something".

### 5.2 Every other disagreement found

| Figure | V1.1 | V1.2.1 | V1.4 | V1.4.1 |
|---|---|---|---|---|
| 3.3VT range | **`DC3—6V`** | `DC3—5V` | `DC3—5V` | `DC3—5V` |
| Default security level | `5` | `5` | `5` | **`3`** |
| Characteristic value size | **384 B** | 512 B | 512 | 512 |
| Template size | **768 B** | 1536 B | 1536 | 1536 |
| Starting time | **`<30ms`** | `≤50ms` | `≤50ms` | `≤50ms` |
| Init narrative | **`about 200ms`** | `about 50ms` | 50 ms | 50 ms |
| Matching-time row | **ABSENT** | `1:N<10ms/Fingerprint` | same | same |
| Touch 120 ms / recal 4.0 s | **ABSENT** | present | present | present |
| WAKEUP polarity | **ABSENT** — no polarity, no drive type | stated + CMOS para | same | same |
| **Ring-LED colours** | **3** (Red/Blue/Purple) | **7** | 7 | 7 |
| Inner diameter | **23.5 mm** | 25 mm | 25 mm | 25 mm |
| `System identifier code` | **`0x0009`** | `0x0000` | `0x0000` | `0x0000` |
| USB support | `both UART and USB`; `For PC, USB … strongly recommended` | `for both UART` (vestigial "both") | same | same |
| Ripple 50 mVpp | **ABSENT** | present | present | present |
| Power-cycle ≥2 s | **ABSENT** | present | present | present |
| Big-endian note | **ABSENT** | present | present | present |
| ReadProdInfo data field | n/a | `46 byte` (**self-consistent**) | `50` (**inconsistent by 4**) | `50` (**inconsistent by 4**) |
| §6.2.3 BufferID default | n/a | `1` | **`2`** | `2` |
| AutoIdentify End position | n/a | `0-199` | **`1-200`** | `1-200` |
| **Capacity** | 200 | 200 | 200 | 200 — **no disagreement** |
| **FAR / FRR** | `<0.001%` / `<1%` | same | same | same — **no disagreement, and NO measurement conditions in any revision** |

### 5.3 Commands added and removed

- **`0x17` PortControl exists ONLY in V1.1** (full section, V1.1 L413–439). Removed from V1.2.1's body while
  **still listed in its instruction table** (V1.2.1 L423) — a dangling row. Gone entirely from V1.4/V1.4.1.
- **`0x31` AutoEnroll and `0x32` AutoIdentify added in V1.2** — **absent from V1.1.**
- **V1.4 and V1.4.1 have identical instruction sets** (35 opcodes each, verified by extracting every
  `Instruction code:` line from both). The V1.4→V1.4.1 delta is **documentation only.**
- Confirmation codes: V1.1 has 24; **V1.2.1 adds 11** (`20 21 22 24 26 27 29 1F FC FD FE`), removes none;
  V1.4/V1.4.1 byte-identical to V1.2.1.
- **V1.1 claims `R30X series provide 23 instructions`** while its own tables list 35 — wrong count *and* wrong
  product family. Deleted in V1.2.1.

### 5.4 ⛔ The changelog is materially incomplete

V1.4.1's "Revised Version" table (p.II, L41–79) does **not** record: the connector change, the 3.3VT 6 V→5 V
change, the 384/768→512/1536 sizes, the 200 ms→50 ms boot time, the removal of `0x17`, the 23.5→25 mm inner
diameter, the AutoEnroll step-code renumbering, or the `System identifier code` change.

**Do not use the changelog to decide whether a revision matters to you.**

Misleading in the other direction: `V1.4.1 … Updated Security level, Checksum`. The checksum *definition* was
diffed across all four revisions — **textually identical in every one** (only `contens`→`contents` fixed in
V1.4). What changed in V1.4.1 is *notation*: length fields rewritten `03H`→`0003H`, and computed checksums
substituted for the placeholder `sum` in ~12 tables. **The checksum algorithm has not changed since 2019.**

### 5.5 Self-contradictions within single revisions

- ⛔ **Baud rate — all four contradict themselves, identically, never fixed.** (i) intro table
  `(9600*N)bps, N=1～6 (default N=6）` → implies max 57600; (ii) §Baud rate control `N= [1/2/4/6/12]` →
  9600/19200/38400/57600/115200, with **N=3 and N=5 illegal**; (iii) §Serial communication protocol
  `default baud rate is 57600bps. User may set the baud rate in 9600～115200bps`.
  Two of three support N∈{1,2,4,6,12}; **the intro table is the outlier.**
- **10 s vs 15 s** (§4.2) — in V1.2.1, V1.4 and V1.4.1.
- **`0x28`** used in §6.2.1, in no revision's table; **`registration logic`** referenced, defined nowhere.
- **`R300-A`** named in the GenChar note of V1.2.1/V1.4/V1.4.1 — wrong product, in an R503 manual.
- **`R307`** is the only reference circuit — a 5 V part (§1.7).
- ⛔ **V1.2.1 AutoEnroll step codes are numerically incoherent** (`…0x09`, then `0x10, 0x11, 0x12`, then
  `0x0D, 0x0E, 0x0F` — later steps with lower codes). **Fixed in V1.4** to `0x0A/0x0B/0x0C`.
  **A driver written against V1.2.1's step table will mis-sequence on current firmware.**
- **`ModelBuffer`** referenced by Match and UpChar, defined nowhere.
- **ReadSysPara table mixes word and byte units** under headers that both say "word".

---

## 6. Driver survey

Denominator: **35** documented V1.4.1 instructions. Legacy opcodes a driver defines (0x1B, 0x33, 0x50–0x53) are
counted **out-of-spec**, not coverage.

| Driver | Coverage /35 | I/O model | Read timeout | `0x55` safe | `0x31`/`0x32` | LED colours | LED modes | Licence |
|---|---|---|---|---|---|---|---|---|
| **Adafruit C++** | 15 (43%) | hard busy-wait | 1000 ms, **cumulative not per-byte** | yes (resyncs on byte 0) | **no** | **3 of 7** | 6 | BSD-3 |
| **Adafruit CircuitPython** | 20 (57%) | blocking fixed-length reads, no framing | **none in driver** | **no — desyncs hard** | **no** | **0 constants** | 0 | MIT |
| **R503Lib** (mpagnoulle) | **26 (74%)** — highest | hot spin, **no `delay()`/`yield()`** | 3000 / default / 4000 ms | yes | **no** | **7 of 7** | 6 | Apache-2.0 |
| **FPM** (brianrho) | 22 (63%) | explicit FSM inside a blocking loop | 2000 ms **inter-byte** | **yes — sliding window, best of six** | **no** | **0 constants** | 0 | MIT (header only) |
| **pyfingerprint** | 20 (57%) | blocking; **`__readPacket` can hang forever** | driver `continue`s past expiry | **no** | **no** | **0 — no LED support** | 0 | **D-FSL** |
| **esphome `fingerprint_grow`** | 12 (34%) — lowest | polled 500 ms FSM + busy-wait transport | **1000 loop iterations, not ms** | **yes — drains RX before every command** | **no** | **7 of 7** | 6 | **not determinable from vendored tree** |

### 6.1 The ring-LED claim — VERIFIED, and worse than "one driver defines three"

`drivers/Adafruit-Fingerprint-Sensor-Library/Adafruit_Fingerprint.h:87-89` defines **exactly 3 of 7** —
RED/BLUE/PURPLE, missing GREEN `0x04`, YELLOW `0x05`, CYAN `0x06`, WHITE `0x07`. (Line 89 carries a real
shipped typo: `//!< Purple LEDpassword`.) All 6 control modes *are* present (`:81-86`).

Across all six: **two define all seven** (R503Lib `R503Lib.h:82-91`; esphome `fingerprint_grow.h:85-93`, which
additionally re-exports them as validated YAML enums so a bad colour is a **compile-time error**); **one defines
three** (Adafruit C++); **three define zero named colours** — CircuitPython (raw ints; 1/2/3 in a docstring at
`adafruit_fingerprint.py:332`), FPM (raw bytes), pyfingerprint (**no LED support at all**).

So **four of six leave a reader unable to discover the colours; one actively misleads.**
⚠ **Nuance:** Adafruit's 3 colours are *correct for V1.1*, which documents only 3. It is a **stale-revision**
artifact, not a plain bug.

### 6.2 Capability gaps — called out specifically

1. ⛔ **Not one of the six implements AutoEnroll `0x31` or AutoIdentify `0x32`.** Verified: grepping
   `0x31|0x32|autoenroll|autoidentify` across every `.h/.cpp/.py` in `drivers/` returns **zero** hits.
   **The intermediate-ack-stream behaviour is untested by every reference implementation available.**
2. **`GetImageEx 0x28` appears in none of them** — so every driver silently accepts poor-quality images
   (GetImg returns `0x00` on a bad image; GetImageEx returns `0x07`).
3. **Two drivers use the WRONG HandShake opcode.** FPM `fpm.h:49` `0x53`; CircuitPython
   `adafruit_fingerprint.py:70` `_GETECHO = const(0x53)`. **V1.4.1 specifies `0x40`.** Only R503Lib is right
   (`R503Lib.cpp:214`).
4. ⛔ **Adafruit C++ exposes SEVEN ILLEGAL baud settings.** `Adafruit_Fingerprint.h:96-106` defines
   `FINGERPRINT_BAUDRATE_*` for **N = 1…12 inclusive**, including `28800 0x3`, `48000 0x5`, `67200 0x7`,
   `76800 0x8`, `86400 0x9`, `96000 0xA`, `105600 0xB`. V1.4.1 permits only **N ∈ {1,2,4,6,12}**.
   **Setting one of the other seven and power-cycling could leave the module at an unreachable baud, with no
   documented recovery.**
5. **No driver handles data packets *and* is safe.** Adafruit C++ has **none at all** and a hard 45-byte receive
   ceiling (`data[64]`, bail at idx≥55) — **templates cannot be read**. esphome likewise none. R503Lib does, but
   by *scanning a fixed 1792-byte buffer for `EF 01` boundaries* — a heuristic that **mis-splits on payload
   bytes that happen to be `EF 01`**.
6. **CircuitPython never verifies checksums**, and concedes it (`adafruit_fingerprint.py:364-366`:
   *"we should check the checksum / but i don't know how"*).
7. ⛔ **R503Lib has a remote stack-smash.** `R503Lib.cpp:691-692` takes `packet.length` **straight off the wire**
   and `readBytes(packet.payload, packet.length)` into the caller's fixed stack array — often `uint8_t data[1]`
   from the `GET_PACKET` macro. **No bounds check anywhere in `R503Packet.cpp`.**
8. ⛔ **pyfingerprint's `__readPacket` (lines 310-319) has no escape.** pySerial's 2 s timeout returns `b''` and
   the driver `continue`s — no counter, no deadline. **A silent module hangs the host forever.** Licence is
   **D-FSL** — copyleft, non-OSI-approved, governed by German law. Flag before any embedding.
9. **esphome's licence is not determinable from the vendored files** — no LICENSE, no headers, not a git
   checkout. *INFERENCE only:* upstream ESPHome is GPLv3 for Python codegen / MIT for `components/**` C++.
   **Confirm upstream before shipping.**
10. **esphome's `WAIT_FOR_WAKE_UP_MS = 200`** comments *"The datasheet says a max wake up time of of 200ms"* —
    **no such figure exists in R503 V1.4.1**, which says ≤50 ms starting time and ~120 ms touch response.
    **The constant cites a datasheet it does not match.**

### 6.3 Best structural model: **FPM**, with esphome's transport discipline grafted on

**Why FPM** (`drivers/FPM/src/fpm.cpp`):

- **The only one of the six whose parser is already a finite state machine with an explicit state enum** —
  `enum class FPMState { READ_HEADER, READ_METADATA, READ_PAYLOAD, READ_CHECKSUM }` (`fpm.cpp:13-18`). The
  others are `switch(idx)` over a byte counter. Critically, **every state already handles "not enough bytes
  yet" by `break`ing without consuming** (`:640-641, :662-663, :714, :752-753`) — the non-blocking contract
  holds *per state*; only the driving `while` at `:634` is wrong. Hoisting the six locals at `:622-630` into
  members and returning `{PENDING, DONE, TIMEOUT}` is a **mechanical refactor. No other driver is one refactor
  away.**
- **Inter-byte timeout, not total** (`lastRead = millis()` refreshed at `:645, :665, :716, :755`) — the correct
  semantics for a transactional driver, and the only correct implementation here.
- **Self-resynchronising by construction.** A sliding two-byte header window (`:647-649`) absorbs any prefix
  garbage **including the `0x55`** without special-casing it; wrong-address (`:673`), bad-length (`:694`) and
  bad-checksum (`:762`) all reset to `READ_HEADER` rather than aborting. **The only reader of the six that
  cannot be permanently desynced by malformed input.**
- **Length validated against both the protocol max and the caller's buffer before any payload read**
  (`:690-692`) — the exact check R503Lib lacks.
- **Transport failure and sensor refusal are numerically disjoint** (`fpm.h:88-99`, `LIB_OK = 0xFF00`), so a
  transaction layer can retry a timeout while surfacing a `NOFINGER`. No other driver does this.
- **Constant-RAM chunked I/O with buffer-*or*-`Stream` sinks** (`:713-734, :562-597`).

**Its weaknesses, named:** **not** non-blocking today (`readPacket` blocks up to 2 s, state is local — do not
adopt as-is); wrong HandShake opcode; zero LED constants; **does not drain RX before transmitting**, so a stale
packet from an aborted transaction is consumed as the current answer; 13 in-spec commands missing (0x0B, 0x28,
0x30, 0x36, 0x3D, 0x40 among them); a single shared 47-byte `buffer` so only one transaction in flight; and a
real bug at `:270` — `memset(info, 0, sizeof(info))` **zeroes the pointer, not the struct.**

**The graft.** Take esphome's `fingerprint_grow.cpp:365-366` — `while (this->available()) this->read();`, a
full RX drain immediately before every transmit. **That is the cleanest structural answer to the `0x55` trap:
it discards the power-on byte, the post-SoftRst byte, *and* any stale packet.** Drain-then-sliding-window is
belt and braces, **and no single driver currently has both.** Also lift esphome's `GrowAuraLEDColor` /
`GrowAuraLEDState` verbatim (`fingerprint_grow.h:76-93` — complete and correct), and mine R503Lib for the 13
missing opcodes.

**Runner-up for a different layer.** esphome's `update()` (`fingerprint_grow.cpp:12-57`) is the best model for
the layer *above* the packet reader: multi-step enrollment as a tick-driven state machine over member state
(`enrollment_image_`, `waiting_removal_`) rather than a blocking loop — **exactly how you substitute for the
AutoEnroll `0x31` that none of the six give you.** It is also the only driver implementing the manual's §6.5
low-power pattern, with optional touch-sense and VCC power-control pins (`fingerprint_grow.h:181-182`,
`.cpp:478-534`), and it handles a field-earned detail no manual mentions: **a spurious `0x00` caused by the
RX-pin rising edge at power-up** (`.cpp:493-498`).

---

## 7. What only a bench can answer

Falsifiable, in rough priority order. **These become the spec's POST-INTEGRATION evidence slots.**

**Mechanical / connector**
1. **Measure the connector pitch with calipers** — 1.00 mm or 1.25 mm. Then measure housing width and pin
   spacing to identify the family (JST SH vs PicoBlade vs clone). Four revisions and two sibling parts disagree
   on the designation; the two document sources disagree on what "MX1.0" denotes.
2. **Identify pin 1 physically.** The only pin-1 reference is a photograph. With the latch/ramp facing a known
   direction, meter continuity from each contact to a known net (GND is the anchor) and confirm
   `1 VCC, 2 GND, 3 TXD, 4 RXD, 5 WAKEUP, 6 3.3VT`. ⛔ **Do not trust wire colour — the manual disclaims it.**
3. **Confirm which part is actually in hand** — R503 (3.3 V), R503-5V (5 V, MX1.25), R503S, or R503Pro.
   Marketplace listings conflate them. `ReadProdInfo 0x3C` returns `PARAM_FPM_MODEL` as 16 ASCII bytes;
   **read it before applying any of §1.**

**Electrical**
4. **Scope WAKEUP's high level with 3.3VT at 3.3 V, then at 5.0 V, VCC in both states.** Settles the §1.3
   inference and says directly whether it overdrives a 3.3 V GPIO.
5. **Measure WAKEUP's source/sink current and rise time into a real GPIO load.** "CMOS output" is the only
   characterization given; there is no VOH/VOL/IOL figure in any revision.
6. **Measure inrush on VCC at power-on.** No revision publishes any peak figure, and §6.5 hard-switches VCC
   repeatedly — the switch FET and bulk cap must be sized from a measurement.
7. **Measure current in three states the manual conflates:** VCC on but idle; VCC on during acquisition; VCC on
   with the ring LED at full white, always-on. **Whether the LED is inside the 20 mA is undetermined, and the
   LED is on for the whole of AutoEnroll.**
8. **Check RXD 5 V tolerance** before driving it from anything but a 3.3 V pin. No revision makes any tolerance
   statement.
9. **Verify the ≥2 s power-off dwell empirically** — §6.5 duty-cycles VCC and 2 s is a hard floor on wake
   latency if taken literally.

**Protocol**
10. **Capture the power-on `0x55` on a logic analyzer** and time it from VCC rising. Confirms ~50 ms, confirms
    it is a bare unframed byte, and confirms whether anything precedes it (esphome's code implies a spurious
    `0x00` from the RX-pin edge — look for it).
11. **Confirm `0x55` is re-emitted after `SoftRst 0x3D`.** This is the case that breaks drivers handling it only
    at boot.
12. **Settle 10 s vs 15 s.** Send `AutoEnroll 0x31` Config3=1, present no finger, time to the `0x26` timeout.
13. ⛔ **Settle `1:N<10ms/Fingerprint`.** Enrol 1 template, time a `Search 0x04`; enrol 200, time again.
    ~10 ms ⇒ whole-sweep figure; ~2 s ⇒ per-template. **This decides the fleet matching architecture.**
14. **Test the unbounded-stream hypothesis.** `AutoIdentify 0x32` with Config1=1, Config2=0, present a finger
    that will never match, count acknowledge packets. **Directly sizes the RX ring buffer.**
15. **Resolve the `ReadProdInfo 0x3C` length** — 49 total (46 ProdInfo) or 53 (50 ProdInfo).
16. **Determine the shipped default security level** via `ReadSysPara 0x0F`, offset 3. V1.4.1 says 3, earlier
    revisions say 5, firmware may match either.
17. ⛔ **Determine the shipped baud rate, and probe which N values are accepted — on a SACRIFICIAL module.**
    The intro table implies N=3/N=5 are legal, the parameter section says they are not, and Adafruit's header
    exposes seven such values. **Establish the recovery path before risking a module you care about.**
18. **Confirm the AutoEnroll `ModelID` field width** is 1 byte, as V1.4.1's table and worked example imply.
19. **Determine what happens when a command arrives while Busy** — queued, rejected, or dropped? The manual is
    silent, and it decides whether the transaction layer needs a lock.
20. **Verify whether `0x28` is a real confirmation code** on shipped firmware, and whether the undocumented
    `registration logic` setting it depends on is reachable at all.
21. **Check `0x17` PortControl** — removed from docs after V1.1 but possibly still in firmware. `0xFC` means
    genuinely gone.

**Explicitly not settled, and not guessed at:** the exact net topology of the §VII reference circuit (low
raster resolution; designators and header labels only, and drawn for the 5 V R307 regardless); the true licence
of the vendored esphome component; and whether any of the six drivers' `0x31`/`0x32` behaviour works, since
none implement it.
