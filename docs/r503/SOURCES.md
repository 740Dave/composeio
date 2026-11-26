# R503 — Sources, Provenance and Verification

Hangzhou Grow Technology Co., Ltd. **R503** capacitive fingerprint module.

Collected **2026-08-15**. Every file below has its exact origin URL, its SHA-256 as it
sits on disk, and the version/revision string printed *inside* the document (not inferred
from the filename).

> **Why this file exists.** The TM1637 work left an open integrity gap —
> `[CXC-TM1637-PRE-DATASHEET-HASH]` cites a datasheet identified only as "V2.4" with
> `datasheet_sha256: NOT YET RECORDED`, so nobody can prove which document the pin claims
> were read from. This file is the deliberate non-repetition of that.

---

## 1. Verification method (what was actually checked)

For every PDF in this directory:

| Check | How | Result |
|---|---|---|
| Is it really a PDF? | first 8 bytes = `25 50 44 46 2d 31 2e 37` (`%PDF-1.7`) | **all 5 PASS** |
| Does it open / parse? | `pdfinfo` + full `pdftotext -layout` extraction | **all 5 PASS** |
| Is it the right part? | read page 1 cover title | **all 5 PASS** (see table) |
| Is the pin count right? | read the "Serial Communication" pin table end-to-end | **all 5 PASS — exactly 6 rows, 1..6, no phantom row** |
| Version string | read from the cover page + the "Revised Version" table | recorded per-file |

The ZIP was verified with `unzip -t` — **no errors detected**, 20 members.

**The strongest provenance result here:** the R503 V1.4.1 PDF obtained from the
manufacturer's own download bundle and the copy vendored in the `mpagnoulle` GitHub
repository are **byte-for-byte identical** (`fa5d4105…`). The third-party mirror is
therefore proven to be the manufacturer's own file, not a re-render, not a scan, not an
edit. This is the check the TM16xx phantom-pin-row incident was missing.

**What was assumed, not verified:**
- That `download.mikroe.com`'s copy of the V1.1 (2019) manual is unaltered. There is no
  manufacturer-hosted V1.1 to diff against (Grow ships only current revisions), so this
  one rests on the host's reputation alone. Its content is *internally* consistent and its
  differences from V1.2.1 all match entries in the official "Revised Version" changelog,
  which is corroborating but not proof.
- That the robu.in mirror of V1.4 is unaltered — same situation, no manufacturer copy of
  V1.4 (only V1.4.1) to diff against.
- Nothing here was checked against physical hardware. No device was touched.

---

## 2. Documents

### 2.1 PRIMARY — R503/R503-M22 User Manual **V1.4.1** (2023.09)

- **File:** `R503_R503-M22_user_manual_V1.4.1_2023-09.pdf`
- **SHA-256:** `fa5d4105f6754ddf0ebcbdb5875a78106a4f147534c3c3bbc0385bc5477dfc22`
- **Size:** 1,908,857 bytes · 49 pages · PDF 1.7 · Creator `WPS 文字` · PDF CreationDate 2023-09-08
- **Version string printed inside:** cover reads
  `R503/R503-M22 Fingerprint Module User Manual — Hangzhou Grow Technology Co., Ltd. — 2023.09 Ver: 1.4.1`
- **Fetched:** 2026-08-15
- **Origin (manufacturer, authoritative):**
  `https://en.hzgrow.com/Download.html` → item "Grow Tech-Fingerprint module user manual"
  → `https://omo-oss-file110.thefastfile.com/portal-saas/pg2024112915541234745/cms/file/grow%20tech-fingerprint%20module%20user%20manual(2).zip`
  → member `Grow Tech-Fingerprint module user manual/R503 R503-M22 fingerprint module user manual-V1.4.1.pdf` (ZIP CRC32 `658e90e4`)
- **Corroborating origin (byte-identical):**
  `https://github.com/mpagnoulle/R503-Fingerprint-Sensor-Library` at commit
  `e0a657f9d57b9a09144c96c87eb9e828545afe8e`, path
  `docs/R503 R503-M22 Fingerprint User Manual V1.4.1.pdf`

**This is the document that matters.** For the R503 there is no separate "datasheet" vs
"protocol manual" — Grow ships one document that is both. It contains the electrical
specs, the pinout, the packet format, the full instruction table, every confirmation
code, and the operation flowcharts.

Note the fetch required an `en.hzgrow.com` **Referer** header; without one the CDN
returns HTTP 403 `Forbidden Access` (hotlink protection). The direct URL also 302s to a
short-lived signed CloudFront URL, so the link above must be re-followed, not cached.

### 2.2 R503 User Manual **V1.2.1** (2022.03)

- **File:** `R503_user_manual_V1.2.1_2022-03.pdf`
- **SHA-256:** `d454673a9bae05974aa1048222c984f90308c3cb8b0f2cde9e6032caf3fbf44a`
- **Size:** 2,119,412 bytes · 48 pages · PDF 1.7 · PDF CreationDate 2022-05-06
- **Version string printed inside:** `R503 Fingerprint Module User Manual — 2022.03 Ver: 1.2.1`
- **Fetched:** 2026-08-15
- **Origin:** `https://github.com/mpagnoulle/R503-Fingerprint-Sensor-Library` @ `e0a657f9…`,
  path `docs/R503 Fingerprint Module User Manual V1.2.1.pdf`
- Kept because several electrical/spec figures **changed** between this and V1.4.1 (§4).

### 2.3 R503/R503-M22 User Manual **V1.4** (2023.07)

- **File:** `R503_R503-M22_user_manual_V1.4_2023-07.pdf`
- **SHA-256:** `4536aec8b13d000a1bdfff4e3c6482386823bb40e2efb913df9f16c7499eb1b3`
- **Size:** 1,903,449 bytes · 49 pages · PDF 1.7 · PDF CreationDate 2023-07-24
- **Version string printed inside:** `R503/R503-M22 Fingerprint Module User Manual — 2023.07 Ver: 1.4`
- **Fetched:** 2026-08-15
- **Origin:** `https://robu-prod-media.s3.ap-south-1.amazonaws.com/uploads/2024/08/R503-fingerprint-module-user-manual.pdf`
  (mirror hosted by robu.in — third-party distributor, **not** manufacturer-verified)

### 2.4 R503 User Manual **V1.1** (2019.6) — earliest revision found

- **File:** `R503_user_manual_V1.1_2019-06.pdf`
- **SHA-256:** `95c30d1db8aaab8a40a0610451c6eb2f050186d188f74bb322439af3e4a16fdb`
- **Size:** 569,164 bytes · 31 pages · PDF 1.7
- **Version string printed inside:** `R503 Fingerprint Module User Manual — 2019.6 Ver: 1.1`
- **Fetched:** 2026-08-15
- **Origin:** `https://download.mikroe.com/documents/datasheets/R503_datasheet.pdf`
  (MikroElektronika, third-party — **not** manufacturer-verified)
- **⚠ DO NOT DESIGN FROM THIS FILE.** It is retained only as a historical baseline. It
  carries figures that Grow has since revised (§4), and many boards sold today ship
  firmware matching V1.2+. It predates AuraLedConfig's 7-colour table, AutoEnroll (0x31)
  and AutoIdentify (0x32) entirely. Note that this file is *distributed under the
  filename `R503_datasheet.pdf`* — the filename is not the version.

### 2.5 R503-5V User Manual **V1.3.1** (2023.09) — different part, kept for disambiguation

- **File:** `R503-5V_user_manual_V1.3.1_2023-09.pdf`
- **SHA-256:** `88d38940e8a3f6826f459fa69736efc2743f6242b6bc7fa1f290dad82dc21b02`
- **Size:** 1,852,712 bytes · 49 pages
- **Version string printed inside:** `R503-5V Fingerprint Module User Manual — 2023.09 Ver: 1.3.1`
- **Origin:** same manufacturer ZIP as §2.1, member `R503-5V fingerprint module user manual-V1.3.1.pdf`
- **This is a DIFFERENT PART.** Grow also sells an `R503-5V`. Modules are frequently
  listed on marketplaces as plain "R503" regardless. If a board arrives and 3.3V behaves
  oddly, check which one you have before trusting §2.1.

### 2.6 Manufacturer bundle (provenance anchor)

- **File:** `grow-tech-fingerprint-module-user-manual_official-bundle_2025-11-26.zip`
- **SHA-256:** `077860a275498585aa5451faa2e67552bd46577d6413f3b0fc2c841c72c97555`
- **Size:** 19,142,740 bytes · 20 members · `unzip -t` clean
- **Fetched:** 2026-08-15 (archive's own newest member dated 2026-05-26; directory entry 2025-11-26)
- **Origin:** `https://en.hzgrow.com/Download.html` → "Grow Tech-Fingerprint module user manual" (18.3MB ZIP)
- Retained unmodified so §2.1's chain of custody can be re-verified later without
  re-fetching from a CDN that may rotate its files. Also contains R301T, R302, R303,
  R303S, R304A, R306, R306S, R307S, R311, R502-A/AW/F, R502-F Pro, R503Pro, R503S,
  R558, R558-S manuals.

### 2.7 Extracted text (working copies, not sources)

`extracted-text/` holds `pdftotext -layout` output for the four R503 manuals, so figures
can be grepped and diffed without re-opening PDFs. These are **derived**, not sources.

| SHA-256 | file |
|---|---|
| `46de4651175d143f1261d9b9315a9e5a5e276c139e88906b52ea24580564dfbd` | `R503_V1.4.1_pdftotext-layout.txt` |
| `222469201a36bce59496170947c57e95dbfa0e14b2b160fbddea239b973c0b9f` | `R503_V1.4_pdftotext-layout.txt` |
| `de0e3e51d10ae6612745e0c9079e94a48b142cae8be0253a7e4b9523a4570480` | `R503_V1.2.1_pdftotext-layout.txt` |
| `fbe27f785b98ba474e7d174640d8159ffe404f15e583679be6044695e5571570` | `R503_V1.1_pdftotext-layout.txt` |

---

## 3. Failed / rejected fetches (recorded, not substituted)

| URL | Date | Outcome |
|---|---|---|
| `https://uelectronics.com/wp-content/uploads/2022/06/R503-fingerprint-module-user-manual-V1.2.1.pdf` | 2026-08-15 | **HTTP 403, `Content-Type: text/html`, 5,903 bytes.** Served an HTML block page under a `.pdf` URL — the exact failure mode seen with the LCSC "datasheet" link during the TM1637 work. **Discarded, not saved.** V1.2.1 was obtained from a different source (§2.2) instead. |
| `…/grow%20tech-fingerprint%20module%20user%20manual(2).zip` without a Referer header | 2026-08-15 | HTTP 403, 16-byte body `Forbidden Access`. Retried successfully with `Referer: https://en.hzgrow.com/`. |
| `https://en.hzgrow.com/product/132.html` (product page) | 2026-08-15 | Reachable but carries **no** datasheet link and **no** spec table. Not a source. |

**No manufacturer-published "V1.2.2" PDF was located.** V1.2.2 (2022.10) exists as a
changelog entry inside V1.4.1's revision table (its sole change: "Added Buffer
contents"), and a V1.2.2 file is referenced by ManualsLib and by an existing
Dropbox-placeholder filename elsewhere in `Data Sheets/`, but Grow's own download bundle
ships only the current V1.4.1. V1.2.2 was **not substituted with a lookalike**; V1.4.1
supersedes it.

Also noted: the pre-existing `Data Sheets/Fingerprint/R503_datasheet.pdf` and
`Data Sheets/R503.fingerprint.module.user.manual-V1.2.2.pdf` are **0-byte Dropbox
online-only placeholders** (xattr `com.dropbox.placeholder`) — they were not materialized
and could not be hashed or compared. They are unrelated to this directory's contents.

---

## 4. ⚠ FIGURES WHERE THE SOURCES DISAGREE

These are revision-to-revision changes within Grow's own documentation. **Both values are
recorded with their sources**; neither is picked.

| Figure | V1.1 (2019.6) | V1.2.1 (2022.03) | V1.4 (2023.07) | V1.4.1 (2023.09) |
|---|---|---|---|---|
| **Connector type** | `MX1.0--6P` | `MX1.0--6P` | `MX1.0--6P` | **`SH1.0--6P`** |
| **Touch supply (3.3VT) range** | **DC 3–6V** | DC 3–5V | DC 3–5V | DC 3–5V |
| **Security level (default)** | 5 | 5 | 5 | **3** |
| **Characteristic value size** | **384 bytes** | 512 bytes | 512 bytes | 512 bytes |
| **Template size** | **768 bytes** | 1536 bytes | 1536 bytes | 1536 bytes |
| **Starting time** | **<30ms** | ≤50ms | ≤50ms | ≤50ms |
| **Matching time row** | *absent* | 1:N <10ms/Fingerprint | same | same |
| WAKEUP description | "Finger Detection Signal" only — **no polarity given** | "Standby-high level, have finger-output low level" | same | same |

Notes on the disagreements:

- **Connector.** `MX1.0` vs `SH1.0` is a real, checkable difference (JST-SH 1.0mm vs
  Molex PicoBlade 1.25mm — and "MX1.0" is itself ambiguous naming). V1.4.1 is the only
  revision saying SH1.0. **Measure the connector on the physical part before ordering a
  mating cable or footprint.** Do not resolve this from paper.
- **Security level.** V1.4.1's own changelog states `V1.4.1 … 1. Updated Security level,
  Checksum`, so the 5→3 change is deliberate and documented, not a typo. All revisions
  agree the *scale* is 1–5 with 5 = strictest; they disagree on the shipped default.
- **Touch supply.** The 3–6V figure appears only in V1.1. Treat 3–5V as the design range.
- **Template/characteristic sizes.** The V1.1 values are for an older algorithm library.
  A driver sized for 768-byte templates will truncate on current firmware.

Additionally, **V1.4.1 contradicts itself on baud range** (this is *within* one document,
not between documents):

- Intro spec table: `(9600*N)bps, N=1~6 (default N=6)` → implies max 57600.
- §"Baud rate control (Parameter Number: 4)": `N = [1/2/4/6/12]` → 9600, 19200, 38400,
  57600, **115200**; N=3 and N=5 are **not** legal values despite "N=1~6".
- §"Serial communication protocol": "default baud rate is 57600bps. User may set the baud
  rate in 9600~115200bps."

The two-of-three reading is **N ∈ {1,2,4,6,12}, default 6 (57600 8N1), max 115200**. The
intro table's `N=1~6` is the outlier and is wrong in every revision including V1.1.

---

## 5. Answers to the two questions asked — as the documents actually state them

### Q1: "The R503 blocks for hundreds of ms during a match." — **PARTLY REFUTED as stated.**

The manual gives **no per-command execution timings** for `GenImg` (0x01), `GenChar`
(0x02), `Search` (0x04), `RegModel` (0x05) or `Store` (0x06). There is no timing table
and no per-instruction latency figure anywhere in V1.4.1. Only these aggregate figures
exist (V1.4.1 intro table, p.1):

| Figure | Value |
|---|---|
| Matching time | **1:N < 10ms / Fingerprint** |
| Generate feature point time | **< 500ms** |
| Image acquiring time | **< 0.2s** |
| Starting time | ≤ 50ms |
| Power-on init before commands accepted | ~50ms, then module emits a `0x55` byte |
| Touch-detect max response time | ~120ms @ Vt = 3.3V |
| Touch recalibration period when untouched | ~4.0s |

So the *match* is the **cheap** step (<10ms), not the expensive one. The hundreds of
milliseconds are in **feature generation (<500ms)** and **image acquisition (<0.2s)** —
i.e. `GenImg` + `GenChar`, not `Search`. A one-shot verify is therefore roughly
**≤700ms dominated by imaging**, with the actual database search a rounding error. If the
HAL's `role: transactional` timeout budget was set from a "matching is slow" premise, it
was set on the wrong step — though the resulting budget happens to be the right
magnitude.

⚠ **The "1:N<10ms/Fingerprint" wording is genuinely ambiguous and the manual never
resolves it.** Read one way it is 10ms for a whole 1:N sweep; read literally
("/Fingerprint") it is 10ms *per stored template*, which at the 200-template capacity
would be up to 2s. Nothing in the document disambiguates. **Do not encode either reading
as fact — measure it on the bench.**

On the second half — **"the host need not block"** — the manual **does not address host
implementation at all**; it is silent, so it neither confirms nor refutes. What it *does*
provide, which is what actually makes non-blocking hosting sound:

- **A `Busy` flag.** System status register bit 0: `1: system is executing commands;
  0: system is free`, readable via `ReadSysPara` (0x0F). Explicit poll-don't-block affordance.
- **`Cancel` (0x30)** — a documented instruction to abort a long-running operation.
- **Strictly request/response framing.** "Commands shall only be sent from upper computer
  to the Module, and the Module acknowledges the commands." The module never speaks
  unsolicited — *with one exception*: the `0x55` byte emitted once after power-on init.
  **A ring-buffer consumer must tolerate that stray byte before its first packet header.**
- **Self-delimiting packets**, so a ring buffer can be drained incrementally: fixed
  header `0xEF01`, 4-byte address, 1-byte PID, 2-byte length (big-endian, and `length`
  counts payload **+ 2 checksum bytes**), payload, 2-byte arithmetic-sum checksum. Max
  package length 256 bytes. **All multi-byte fields are big-endian.**
- **Multi-response commands.** `AutoEnroll` (0x31) and `AutoIdentify` (0x32) with
  `Config3 = 1` return **a stream of intermediate acknowledge packets** reporting each
  critical step (0x01 "collect image 1st time", 0x02 "generate feature 1st time", … 0x0C).
  A host that assumes exactly one response per command **will desynchronize on these.**
  These also self-terminate: >10s (AutoEnroll) or >15s (AutoIdentify) with no finger and
  the module exits with confirmation code `0x26` (timeout).

**Verdict:** non-blocking hosting is well supported by the protocol, but the manual makes
no claim about it either way. The `0x55` power-on byte and the 0x31/0x32 intermediate
packets are the two concrete facts that a ring-buffer design must account for.

### Q2: "The ring LED is a UART command, not a separate pin." — **CONFIRMED.**

There is no LED pin. The connector has exactly 6 pins (verified against the pin table in
all revisions) and none of them is an LED control: `1 VCC(3.3V) · 2 GND · 3 TXD · 4 RXD ·
5 WAKEUP · 6 3.3VT`. The ring is driven **only** by instruction `0x35 AuraLedConfig`
(V1.4.1 p.27):

```
Header  Addr      PID   Length  Cmd   Ctrl  Speed  ColorIdx  Times  Checksum
0xEF01  xxxxxxxx  0x01  0x0007  0x35  Ctrl  Speed  ColorIdx  Count  sum
```

| Param | Values |
|---|---|
| **Ctrl** | `0x01` breathing · `0x02` flashing · `0x03` always on · `0x04` always off · `0x05` gradually on · `0x06` gradually off |
| **Speed** | `0x00`–`0xFF`, 256 steps, **minimum 5s cycle**. Effective only for breathing, flashing, gradually-on, gradually-off. |
| **ColorIndex** | `0x01` Red · `0x02` Blue · `0x03` Purple · `0x04` Green · `0x05` Yellow · `0x06` Cyan · `0x07` White |
| **Times** | number of cycles; `0` = infinite, `1`–`255`. Effective only for breathing and flashing. |

Return: standard ack, `0x00` success / `0x01` receive error.

**Worked example from the manual (cyan breathing light) — verbatim, p.27:**
```
Send:   EF 01 FF FF FF FF 01 00 07 35 01 50 06 00 00 94
Return: EF 01 FF FF FF FF 07 00 03 00 00 0A
```
This example was added in V1.4.1 and is **absent from V1.2.1 and earlier** — another
reason to work from V1.4.1.

⚠ **Driver caveat found while collecting:** the Adafruit Arduino library
(`Adafruit_Fingerprint.h`, v2.1.4) defines only **three** colour constants —
`FINGERPRINT_LED_RED 0x01`, `FINGERPRINT_LED_BLUE 0x02`, `FINGERPRINT_LED_PURPLE 0x03`.
Green/Yellow/Cyan/White (0x04–0x07) are **missing** from the header. Those colours were
added by Grow in V1.2 (2021.10, "LED colors increased from three to seven, and the
instruction formats were downward compatible"). The values still work — pass them as
raw `uint8_t` to `LEDcontrol(control, speed, coloridx, count)` — but a driver written by
reading Adafruit's constants alone would conclude the R503 has three colours. **It has
seven.**

---

## 6. Other captured figures (all from V1.4.1 unless noted)

**Supply and current**
- Supply: **DC 3.3V** (pin 1). Interface: **UART, 3.3V TTL logic level.**
- Working current (fingerprint acquisition): **20 mA**
- Touch-standby: typical touch standby voltage **3.3V**, **average current ~2 µA**
- ⚠ **No idle/active-but-not-acquiring current figure is published.** Only "working
  current 20mA" and "touch standby ~2µA" exist. There is no third number. Do not invent one.
- **Ripple noise: recommended not to exceed 50 mVpp.** The input feeds the image sensor
  and decoding chip directly.
- **After power-off, wait ≥2 seconds before powering on again.** Short power cycles are
  called out as a cause of malfunction.
- Do not hot-plug the connector — insertion/removal while live may damage the module.

**Touch-induction pin (6 / 3.3VT) electrical behaviour** — this is the one the panel design cares about
- Separate supply rail from VCC: **DC 3–5V** (V1.1 said 3–6V, see §4), avg **~2 µA**.
- **WAKEUP (pin 5) output: standby = HIGH, finger present = LOW** (active-low).
- "The touch signal output is **CMOS output**, and the **output voltage is roughly the
  same as the input voltage**" — i.e. WAKEUP's logic-high tracks whatever is on **3.3VT**,
  not VCC. ⚠ **If 3.3VT is fed 5V, WAKEUP swings to ~5V and will over-drive an ESP32-S3
  GPIO.** Tie 3.3VT to 3.3V on this panel.
- Max touch response time **~120 ms @ Vt = 3.3V**; recalibration period when untouched **~4.0 s**.
- Intended low-power pattern (§6.5): host cuts **VCC** but keeps **3.3VT** powered; module
  asserts IRQ/WAKEUP on finger; host then powers VCC and runs the transaction.

**Serial**
- Semi-duplex asynchronous. **Default 57600 bps.** Frame **10-bit: 1 start, 8 data LSB-first,
  1 stop, no parity** (8N1).
- Baud parameter N ∈ {1,2,4,6,12} → 9600/19200/38400/57600/115200 (see §4 for the contradiction).
- Data package length parameter: 0/1/2/3 → 32/64/128/256 bytes.
- Default module address `0xFFFFFFFF`; module rejects packets with a wrong address.

**Capacity and storage**
- **Fingerprint template capacity: 200** (IDs 0–0xC7). Consistent across *all four* revisions.
- Template size 1536 bytes; characteristic value 512 bytes (V1.1 differs, §4).
- 6 feature buffers `CharBuffer[1:6]` + one `ImageBuffer`.
- Notepad: **512 bytes = 16 pages × 32 bytes**, power-off protected. Writing a page
  overwrites all 32 bytes of it.
- Sensing array 192×192 px, 508 dpi, detection area Ø15 mm.

**FAR / FRR — ⚠ NO MEASUREMENT CONDITIONS ARE STATED**
- `FAR < 0.001%`, `FRR < 1%`.
- The document gives **no** dataset, sample size, population, test protocol, environmental
  condition, or which security level these were measured at. The only related statement
  is qualitative: "At level 1, FAR is the highest and FRR is the lowest; however at
  level 5, FAR is the lowest and FRR is the highest." Since V1.4.1 changed the stated
  default security level from 5 to 3 (§4), **it is not even determinable which operating
  point the quoted FAR/FRR belongs to.** Treat both numbers as marketing figures.

**Environment**
- Operating: −20 °C to +60 °C, RH 10–85%. Storage: −40 °C to +75 °C, RH < 85%.

**Mechanical**
- R503: Ø28 mm outer / 25 mm inner, height 19 mm (15 mm and 32 mm also available), M25
  thread, zinc alloy. R503-M22: Ø25/22 mm, height 15 mm, M22 thread.

**Confirmation codes** (full list at V1.4.1 pp.9–10). The ones a driver must handle:
`00` OK · `01` packet receive error · `02` no finger · `03` enroll failed · `06` image too
disorderly · `07` too few feature points / image too small · `08` no match · `09` not found
in library · `0A` merge failed · `0B` PageID out of range · `0C` bad/invalid template ·
`13` **wrong password** · `15` no valid primary image · `18` flash write error · `20`
address incorrect · `21` **password must be verified first** · `22` template empty · `24`
library empty · `26` **timeout** · `27` fingerprint already exists · `29` sensor hardware
error · `1F` library full · `FC` unsupported command · `FD` hardware error · `FE`
execution failure.

---

## 7. Reference drivers (`drivers/`)

All fetched 2026-08-15. Git repos are full clones at the recorded commit — run
`git -C <dir> rev-parse HEAD` to re-verify.

| Directory | Origin | Commit / version | Notes |
|---|---|---|---|
| `Adafruit-Fingerprint-Sensor-Library/` | `https://github.com/adafruit/Adafruit-Fingerprint-Sensor-Library` | `b5de3ff6b430922d702db76e0c9ce29200c06d34` · tag **2.1.4** · 2026-02-26 | The canonical Arduino/C++ reference. `LEDcontrol(ctrl,speed,coloridx,count)` implements 0x35. ⚠ colour constants incomplete (§5 Q2). Vendors ZFM-20 and generic protocol PDFs in `documentation/`. |
| `Adafruit_CircuitPython_Fingerprint/` | `https://github.com/adafruit/Adafruit_CircuitPython_Fingerprint` | `b2d9a4842b8fca7fa51fde03aed8699d4718350b` · tag **2.2.25** · 2026-04-23 | CircuitPython/MicroPython-compatible port. Clean single-file `adafruit_fingerprint.py`. |
| `esphome-fingerprint_grow/` | `https://github.com/esphome/esphome` → `esphome/components/fingerprint_grow/` | component dir last touched `9f5ed6fdfd3d4fc68c4444c94bac71522e8c4c19` · 2026-06-22 | **The best-structured async/non-blocking implementation of the lot** — event-driven, no busy-wait, and the closest match to a `role: transactional` HAL. Files fetched individually from `raw.githubusercontent.com/esphome/esphome/dev/…` (not a clone): `__init__.py` `e3929e3b…`, `binary_sensor.py` `ac3f8327…`, `fingerprint_grow.cpp` `dd097b11…`, `fingerprint_grow.h` `309f05c8…`, `sensor.py` `2196e949…`. ⚠ fetched from the moving `dev` branch — the commit SHA above is the component's last-modified commit, not the ref fetched. |
| `R503-Fingerprint-Sensor-Library/` | `https://github.com/mpagnoulle/R503-Fingerprint-Sensor-Library` | `e0a657f9d57b9a09144c96c87eb9e828545afe8e` · 2023-11-04 | **R503-specific**, ESP32-targeted, Arduino SDK. Also the source of the byte-identical V1.4.1 manual (§2.1) and of V1.2.1 (§2.2). |
| `FPM/` | `https://github.com/brianrho/FPM` | `9b4cb891cf3474415969976077204d534737426c` · 2023-12-31 | Well-regarded alternative Arduino library; better image up/download and packet handling than Adafruit's. Broad R30x/R50x family support. |
| `pyfingerprint/` | `https://github.com/bastianraschke/pyfingerprint` | `83101c6bad4b5a71ddbc4666a7526f904297b0b1` · 2020-07-28 | The standard Python/Linux implementation. Unmaintained since 2020 — predates AuraLedConfig's 7 colours, AutoEnroll and AutoIdentify. Useful as a protocol reference, not as a current driver. |

**No native ESP-IDF component for the R503 was found.** Searched; the ESP32-targeted
options are all Arduino-SDK (`mpagnoulle`, `FPM`, Adafruit) or ESPHome's component. If a
pure ESP-IDF driver is wanted it will have to be written — ESPHome's `fingerprint_grow`
is the best structural model to copy, and the protocol is small enough that the V1.4.1
instruction table is a sufficient spec.

---

## 8. Bottom line for the panel design

- **Design from `R503_R503-M22_user_manual_V1.4.1_2023-09.pdf`, SHA-256 `fa5d4105f6754ddf0ebcbdb5875a78106a4f147534c3c3bbc0385bc5477dfc22`.**
  That hash is the citation. It is manufacturer-sourced and independently corroborated
  byte-for-byte.
- 3 pins is correct — but note the module wants **4** connections plus grounds if the
  low-power pattern is used: VCC (switchable), GND, TXD, RXD, WAKEUP, **and 3.3VT kept
  always-on**. On a 6-pin connector that is all six. Tie 3.3VT to 3.3V, never 5V.
- Budget the transaction at **≤700 ms**, dominated by imaging and feature generation,
  **not** by matching — and verify the 1:N sweep time on the bench because the manual's
  wording does not settle it.
- The ring LED costs no pins. `0x35`, four bytes of parameters, seven colours.
- Do not quote the FAR/FRR figures as characterized performance. They have no stated
  conditions and no determinable security level.
