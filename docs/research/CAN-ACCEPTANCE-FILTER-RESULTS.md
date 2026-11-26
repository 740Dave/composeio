# RESULTS — ESP32-C3 TWAI acceptance filter: data-byte matching for extended frames

> **STATUS: RUN AND COMPLETE.** E1-CONTROL and E1a–E1e were executed on real hardware on
> 2026-08-10 on Track A (one ESP32-C3, one jumper). Every experiment passed its validity
> gate (`state=RUNNING`, `bus_error=0`, `msgs_to_tx=0`, `tx_failed=0`).
>
> The assignment, wiring, protocol and decision rules are in
> `docs/research/CAN-ACCEPTANCE-FILTER-RESEARCH.md`.
>
> **The headline:** the ESP32-C3 TWAI acceptance filter **CANNOT** match on data bytes for
> extended (29-bit) frames — it compares the identifier only. It **CAN** match on data
> bytes 1–2 for standard (11-bit) frames. Both were measured, and E1c proves the E1b null
> is a real capability boundary rather than a misconfiguration.
>
> **No layout recommendation is made here. David decides.**
>
> **Read §10 before anything else if you are re-running this.** The bench was wired
> GPIO2↔GPIO10, not GPIO3↔GPIO10, and the first full run was a complete false null because
> of it.

---

## 0. Run metadata

| | |
|---|---|
| Date run | 2026-08-10 |
| Run by | Intelligence (Claude Opus 5, `composeio-expert` agent), unattended; David out of office |
| Track | **A** — single board, single jumper. Track B not run (only one board present) |
| Bitrate | 500 kbit/s |
| Firmware `stamp=` | `c8c58d8b` (all E1 results). Pre-existing build was `bada7e85` — see §9 "Firmware changes" |
| Firmware `built=` | `Aug_10_2026_16:23:08` |
| PlatformIO env | `c3_usbcdc` |
| Driven by | `tools/bench.py --loop /dev/cu.usbmodem31101 --pins 2,10 --exp all`, plus hand-driven confirmations and E4 rounds via a serial REPL |
| Raw transcript saved at | **inlined in full in §12 of this file** (the scratch directory is session-scoped and will be cleaned; the transcript is the evidence, so it lives here) |

## 0.1 Step 0 — the board flashed in error

Research doc §0.1 concerns an ESP32-C3 with MAC `00:00:5e:00:53:02`.

| | |
|---|---|
| Was David asked what he wanted done with it? | **Not by me.** He was out of office. The launching context stated he had already authorised free flashing of the single board now on the bench, and that board is **not** the §0.1 board |
| His decision | n/a for the §0.1 board |
| Restore performed? | **No — the §0.1 board is NOT PRESENT.** Only one board is on USB and its MAC is `00:00:5e:00:53:03`, which is a different physical board. There was nothing to restore |
| Were the backups still present? | **Not checked / not needed.** The board they belong to is not attached. If David wants that board restored, it must be plugged in first and its MAC re-confirmed against `SER=` before any write |

> **The MAC in research doc §0.1 and §2.1 is stale with respect to today's bench.** Do not
> use it to identify the board on `/dev/cu.usbmodem31101`.

## 0.2 Step 1 — the REAL hardware inventory

> This supersedes research doc §2.1, and also supersedes §0.1's board identification.
> David was not available, so the inventory below is what was **measured**, not what he
> said. Every row is a reading from the bench, and each is reproducible.

| Question | Answer (measured, 2026-08-10) |
|---|---|
| How many ESP32 boards, and which variants? | **Exactly one on USB: an ESP32-C3.** `esptool chip_id` → `Chip is ESP32-C3 (revision v0.4)`. No other `/dev/cu.usbmodem*` or `/dev/cu.usbserial*` device was enumerated |
| Are any committed to another project? | Stated by the launching context to be a dedicated test board, flashable freely. Nothing on it was backed up |
| How many SN65HVD230 breakouts? | **Zero in use.** None required — Track A needs none |
| Do those breakouts already carry a 120 Ω resistor? | n/a — no transceivers used |
| Was any CAN hardware already wired? | **One dupont wire only** — and it was **not** where the assignment said it was. See §1 and §10 |
| Jumper wires available? | One, fitted |

**At least one ESP32-C3 available?** **Yes.**

### Board actually used

| Role | Chip | Rev | MAC | Port | `stamp=` | Flash |
|---|---|---|---|---|---|---|
| receiver under test **and** transmitter (Track A loopback) | **ESP32-C3** | **v0.4** (`rev=4`) | **`00:00:5e:00:53:03`** | `/dev/cu.usbmodem31101` | `c8c58d8b` | 4 MB, mfr `0x46`, dev `0x4016` |

Full inventory readings, verbatim:

```
$ python -c "from serial.tools import list_ports; ..."
/dev/cu.usbmodem31101 | USB VID:PID=303A:1001 SER=00:00:5E:00:53:03 LOCATION=3-1.1

$ esptool --port /dev/cu.usbmodem31101 --no-stub chip_id
Detecting chip type... ESP32-C3
Chip is ESP32-C3 (revision v0.4)
Features: WiFi, BLE
Crystal is 40MHz
MAC: 00:00:5e:00:53:03
Warning: ESP32-C3 has no Chip ID. Reading MAC instead.

$ esptool --port /dev/cu.usbmodem31101 --no-stub flash_id
Manufacturer: 46
Device: 4016
Detected flash size: 4MB
```

```
ID fw=can_filter_bench ver=1.0.0 stamp=c8c58d8b built=Aug_10_2026_16:23:08
ID chip=esp32c3 rev=4 cores=1 mac=1cdbd4c28020 idf=4.4.6
ID tx_gpio=3 rx_gpio=10 trig_gpio=1 role=idle cdc=1
END ID
```

**Do the two `stamp=` values match?** n/a — single board.

### Why Track B was not run

Not needed, and not possible. E1 is fully answered on Track A with one board and one
jumper, exactly as research doc §3.1 argues. E2 was not requested and there is only one
ESP32-C3 attached, so a two-node bus could not have been built even if it had been. **No
Track B outcome is inferred anywhere in this document.**

---

## 1. Pre-flight

### Track A (single board + jumper)

| Check | Result |
|---|---|
| Jumper fitted between TWAI TX GPIO and TWAI RX GPIO | **NO — as configured.** A jumper is fitted, but between **GPIO2 and GPIO10**, while the firmware's `TWAI_TX_GPIO` is **3**. Confirmed bidirectionally with a DC continuity probe (§10). E1 was therefore run with TWAI TX retargeted to **GPIO2** at runtime (`PINS 2 10`) so the peripheral used the pads the wire is actually on |
| `ID` reports `chip=esp32c3` | **Yes** — `ID chip=esp32c3 rev=4 cores=1 mac=1cdbd4c28020 idf=4.4.6` |
| Effective pin map for every result below | `tx_gpio=2 rx_gpio=10`, echoed on every `OK INIT` and on the added `STAT pins` line |

> **Does running TWAI TX on GPIO2 instead of GPIO3 affect any result here?** **No.** The
> acceptance filter is entirely inside the TWAI controller's receive path; which pad the
> GPIO matrix routes TX/RX through is invisible to it. GPIO2 is a *strapping* pin, which
> matters only for boot mode, not for filtering — see §10 for the caveat and the fix.

### Track B (bus)

**Omitted entirely — Track B was not run.**

### Wire-check rehearsal (§5.7) — attempted, and it DOES NOT WORK on a jumpered Track A

| | Observed |
|---|---|
| Fault A, RX stuck dominant (`RXPULL down`) | **Did not reproduce.** With the pull applied *before* `INIT` (as `bench.py` does): `STAT hw state=RUNNING tec=136 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=1 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=2` — the documented signature is `msgs_to_tx=1` with **all error counters zero**; this is the opposite shape. With the pull applied *after* `INIT`, and the jumper working (`PINS 2 10`): the frame transmitted and was **received normally** — `STAT rx accepted=2`, `STAT hw ... tec=0 ... bus_error=0` |
| Fault B, RX recessive/floating (`RXPULL up`) | **Did not reproduce.** `STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0` and `STAT alert2 lines_suppressed=0` — total silence, no bus-error storm, no throttling |
| Restored (`DEINIT`, `RXPULL none`) before real measurement | **Yes** |

**Why it does not work, and why that matters** — see the W2 row of §9 "Anything in the assignment document you found to be WRONG". Short version: an
ESP32-C3 internal pull is ~45 kΩ; it cannot override a push-pull GPIO driver at the other
end of a jumper. §5.7's rehearsal is only meaningful with the jumper **removed**, which is
almost certainly the state it was captured in on 2026-08-10 (research doc §0.2 records
that no CAN hardware was wired that day).

### On-chip cross-check of this document's filter literals

Computed on the silicon under test, `stamp=c8c58d8b`:

```
OK CALC kind=ext_single  id=0x12345678 code=0x91a2b3c0 mask_exact=0x00000007 mask_top16=0x0000ffff
OK CALC kind=std_single  id=0x123 d0=0xa5 d1=0x5a code=0x2460a55a mask_data_only=0xffff0000 mask_id_and_data=0x001f0000 mask_id_only=0x001fffff
OK CALC kind=dual_ext    id1=0x02468000 id2=0x1fffffff f1_top16=0x1234 f2_top16=0xffff code=0x1234ffff mask_exact=0x00000000
```

**Do the on-chip values match the document?** **Yes — all three, exactly.** `0x91a2b3c0 /
0x00000007`, `0x2460a55a / 0xffff0000`, `0x1234ffff / 0x00000000` are byte-for-byte what
research doc §7 prints. The document's acceptance-filter arithmetic (including the
ACR0..ACR3 MSB-first byte-swap reasoning in §2.5) is **confirmed correct**.

---

## 2. E1-CONTROL — accept-all

**Predicted:** `accepted=6` across 4 distinct ids; `bus_error=0`; `msgs_to_tx=0`.

Commands: `FILTER 0x00000000 0xFFFFFFFF single` → `INIT 500 no-ack` → six `SEND ext`.

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0x00000000 mask=0xffffffff mode=single rx_queue_len=16 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=6 fail=0 queue_err=0
STAT rx accepted=6 tally_overflow=0
STAT rxid ext=1 id=0x12345678 n=1
STAT rxid ext=1 id=0x12345679 n=1
STAT rxid ext=1 id=0x024694a0 n=2
STAT rxid ext=1 id=0x02468b40 n=2
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=6 rx_data=6 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

| | |
|---|---|
| Verdict | **PASS** |
| Notes | 6/6 accepted, all four distinct ids present with the right multiplicities, every error counter zero. Self-reception through the acceptance filter works, so Track A is valid. The **first** attempt at this experiment produced `accepted=0 tx_failed=6 bus_error=7 tec=176` — see §10 |

---

## 3. E1a — POSITIVE CONTROL: extended, single filter, exact 29-bit id

Filter: `code=0x91A2B3C0 mask=0x00000007 single`.
**Predicted:** `accepted=1`, only `id=0x12345678`.

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0x91a2b3c0 mask=0x00000007 mode=single rx_queue_len=16 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=4 fail=0 queue_err=0
STAT rx accepted=1 tally_overflow=0
STAT rxid ext=1 id=0x12345678 n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=4 rx_data=1 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

| Frame sent | Predicted | Observed |
|---|---|---|
| `ext 0x12345678` | accepted | **accepted** (`rxid n=1`) |
| `ext 0x12345679` | rejected | **rejected** (no `rxid` line) |
| `ext 0x02345678` | rejected | **rejected** (no `rxid` line) |
| `ext 0x12345670` | rejected | **rejected** (no `rxid` line) |

| | |
|---|---|
| Verdict | **PASS** |
| Notes | The filter is live and discriminating at single-ID-bit resolution — it rejects neighbours differing in ID bit 0, ID bit 28, and ID bits 3..0. `tx ok=4` with `tx_failed=0` proves all four frames reached the wire, so the three rejections are filter decisions and not lost frames. **Self-reception does NOT bypass the acceptance filter** — the one failure mode that would have invalidated Track A (research doc §10 item 13) is ruled out |

---

## 4. E1b — THE QUESTION

Filter: `code=0x0000A500 mask=0xFFFF00FF single`. Only code bits 15..8 are compared.

| Hypothesis | A (`0x024694a0`) | B (`0x02468b40`) | total |
|---|---|---|---|
| **H0 — identifier only** (documented) | 2 / 2 | 0 / 2 | 2 |
| **H1 — data bytes ARE filtered for EFF** | 1 / 2 | 1 / 2 | 2 |

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0x0000a500 mask=0xffff00ff mode=single rx_queue_len=16 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=4 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x024694a0 n=2
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=4 rx_data=2 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

`EVT RX` lines from the independent hand-driven re-run (same filter, same four frames) —
**this is the load-bearing evidence, because it shows the `data=` field of each accepted
frame:**

```
< SEND ext 0x024694a0 a5
OK SEND ext=1 id=0x024694a0 dlc=1 queued=1 esp_err=0x0
EVT RX ext=1 rtr=0 id=0x024694a0 dlc=1 data=a5 t=247397
< SEND ext 0x024694a0 5a
OK SEND ext=1 id=0x024694a0 dlc=1 queued=1 esp_err=0x0
EVT RX ext=1 rtr=0 id=0x024694a0 dlc=1 data=5a t=248037      <-- data[0]=0x5A ACCEPTED
< SEND ext 0x02468b40 a5
OK SEND ext=1 id=0x02468b40 dlc=1 queued=1 esp_err=0x0
                                                             <-- data[0]=0xA5 REJECTED
< SEND ext 0x02468b40 5a
OK SEND ext=1 id=0x02468b40 dlc=1 queued=1 esp_err=0x0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x024694a0 n=2
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
```

| Frame | id | `ID[12:5]` | `data[0]` | H0 predicts | H1 predicts | Observed |
|---|---|---|---|---|---|---|
| A1 | `0x024694a0` | `0xA5` | `0xA5` | accepted | accepted | **accepted** |
| A2 | `0x024694a0` | `0xA5` | `0x5A` | accepted | rejected | **accepted** |
| B1 | `0x02468b40` | `0x5A` | `0xA5` | rejected | accepted | **rejected** |
| B2 | `0x02468b40` | `0x5A` | `0x5A` | rejected | rejected | **rejected** |

| | |
|---|---|
| Total accepted | **2** |
| **Which hypothesis does the distribution match?** | **H0 — identifier only.** A: **2/2**, B: **0/2** |
| Verdict | **PASS (H0)** |
| Re-run performed? | **Yes, twice more**, though the rule only required it for an H1 answer: (a) a hand-driven repeat capturing `EVT RX data=` (above), and (b) a **mirror control** with the complementary code (below). Both agree |
| Notes | The two frames that got through are the two that share `ID[12:5]==0xA5`, *irrespective of their data bytes* — A2 was accepted with `data[0]=0x5A`, which H1 forbids, and B1 was rejected with `data[0]=0xA5`, which H1 requires to be accepted. **H1 is falsified in both directions by the same run** |

### Mirror control (added — not in the protocol)

The protocol's E1b is one-sided: it shows that code bits 15..8 select on `ID[12:5]`. To
rule out any residual "code `0xA5` happens to be special" reading, the same four frames
were sent with the **complementary** code `0x00005A00`, mask unchanged.

```
STAT filter code=0x00005a00 mask=0xffff00ff mode=single rx_queue_len=16 rxpull=none
STAT tx ok=4 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x02468b40 n=2
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
EVT RX ext=1 rtr=0 id=0x02468b40 dlc=1 data=a5 t=290517
EVT RX ext=1 rtr=0 id=0x02468b40 dlc=1 data=5a t=291152
```

The accepted set flipped exactly — **B: 2/2, A: 0/2**, and again both data values got
through. Changing only the *expected identifier bits* changes only which *identifiers* are
accepted. Nothing about the data bytes ever influences an extended-frame filter decision.

**Answer to the question this whole bench exists for:**

> Can an ESP32-C3 TWAI acceptance filter match on DATA BYTES for EXTENDED (29-bit) frames?

**NO.** With `code=0x0000A500 mask=0xFFFF00FF`, frame A2 (`data[0]=0x5A`) was **accepted**
and frame B1 (`data[0]=0xA5`) was **rejected** — `STAT rxid ext=1 id=0x024694a0 n=2` with
no `0x02468b40` line, on a run with `bus_error=0`, `tx_failed=0`, `msgs_to_tx=0` and
`tx ok=4`. The comparison was on `ID[12:5]`, not on the data. Extended-frame acceptance
filtering is **identifier-only**, exactly as C3 TRM v1.4 §31.4.6.1 says.

---

## 5. E1c — DISAMBIGUATOR: standard frame, data bytes 1–2

Filter: `code=0x2460A55A mask=0xFFFF0000 single` — identifier and RTR fully don't-care.

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0x2460a55a mask=0xffff0000 mode=single rx_queue_len=16 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=5 fail=0 queue_err=0
STAT rx accepted=3 tally_overflow=0
STAT rxid ext=0 id=0x00000123 n=1
STAT rxid ext=0 id=0x00000456 n=1
STAT rxid ext=0 id=0x000007ff n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=5 rx_data=3 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

| Frame sent | Predicted | Observed |
|---|---|---|
| `std 0x123 a55a` | accepted | **accepted** |
| `std 0x123 a55b` | rejected (data byte 2 differs) | **rejected** — `0x123` tallied `n=1`, not `n=2` |
| `std 0x123 5a5a` | rejected (data byte 1 differs) | **rejected** — same evidence |
| `std 0x456 a55a` | **accepted (id masked out)** | **accepted** |
| `std 0x7ff a55a` | **accepted (id masked out)** | **accepted** |

| | |
|---|---|
| Verdict | **PASS** |
| **Is E1b interpretable given this?** | **YES.** This is the whole point of E1c and it came out clean. Data-byte filtering demonstrably *works* on this silicon, in this firmware, through this driver, with this mask polarity — three frames with three *different* identifiers (`0x123`, `0x456`, `0x7ff`, spanning the full 11-bit range) were accepted purely because their two data bytes matched, and two frames with the *matching* identifier `0x123` were rejected purely because a data byte differed. So the E1b null is **not** "we configured the data-byte comparison wrong" — it is a **real capability boundary between SFF and EFF frames** |
| Notes | `0x7ff` (all-ones identifier) being accepted against `code` identifier field `0x123` is the strongest single observation: the identifier comparison really is fully disabled by `mask=0xFFFF0000`, and the data comparison really is doing the work. The mask polarity of the legacy API (1 = don't care) is confirmed correct as documented in research doc §2.5 |

---

## 6. E1d — LAYOUT DISCRIMINATOR: dual filter, extended

### Part 1 — `code=0x1234FFFF mask=0x00000000 dual`

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0x1234ffff mask=0x00000000 mode=dual rx_queue_len=16 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=3 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x02468000 n=1
STAT rxid ext=1 id=0x02469fff n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=3 rx_data=2 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

| Frame sent | `ID[28:13]` | Predicted | Observed |
|---|---|---|---|
| `ext 0x02468000` | `0x1234` | accepted | **accepted** |
| `ext 0x02469fff` | `0x1234` | **accepted — differs only in ID[12:0]** | **accepted** |
| `ext 0x0246a000` | `0x1235` | rejected | **rejected** |

### Part 2 — banks swapped, `code=0xFFFF1234 mask=0x00000000 dual`

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0xffff1234 mask=0x00000000 mode=dual rx_queue_len=16 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=3 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x02468000 n=1
STAT rxid ext=1 id=0x02469fff n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=3 rx_data=2 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

| | |
|---|---|
| Part 1 == Part 2? | **Yes — byte-identical `rx`/`rxid`/`hw` blocks.** Both banks are live and independently effective; the esp-idf#11897 "bank 2 is dead" trap did not occur here |
| **Does dual-filter EFF compare only ID[28:13]?** | **Yes.** `0x02469fff` differs from the target `0x02468000` in **all thirteen** low identifier bits and was accepted anyway; `0x0246a000`, which differs only in `ID[13]` (i.e. inside the compared window), was rejected |
| Verdict | **PASS** |
| Notes | Confirms C3 TRM v1.4 §31.4.6.2. Note the corollary the protocol only implies: in dual mode `ID[12:0]` and RTR are **not comparable at all**, so a dual-mode subscription is *always* a 16-bit prefix match on the top of the identifier, whatever you put there |

### What this does and does not decide

Field position is **irrelevant in single filter mode** — verified directly in §8 below, not
merely derived: a two-bit field at `ID[24:23]` was matched exactly while the other 27
identifier bits varied from `0x00800000` to `0x1EFFFFFF`. Position matters **only** in dual
mode. So E1d does not choose a layout on its own; it prices a second accept pattern.

| | |
|---|---|
| If a leaf needs ONE accept pattern | **This result constrains nothing.** Single-filter mode compares all 29 bits with a per-bit mask, so "accept exactly namespace X", "accept exactly scope=global", and even "accept scope=global AND namespace X" are each expressible in one bank under **either** L1 or L2 — all three were measured on the bench (§8). The layout decision then rests entirely on E1b, i.e. on the fact that anything in the data bytes is unfilterable for EFF |
| If a leaf needs TWO accept patterns | **This result is decisive, and it favours L1.** Dual mode compares only `ID[28:13]`. Under **L1** `[ns_hash 16][type 4][src_hash 9]` the namespace hash occupies *exactly* that window, so two namespaces are two exact patterns with no false positives. Under **L2** `[type 4][scope 2][ns_hash 16][seq 7]` that window is `type(4) + scope(2) + ns_hash[15:6]`, so a bank can only express a **10-bit prefix** of a 16-bit hash — it will additionally admit the **64** other namespaces sharing that prefix, which must then be rejected in software. Measured cost, not asserted: E1d part 1 shows `0x02469fff` — an identifier the filter was never aimed at — arriving because its top 16 bits matched |

**Topology question handed back to David — NOT answered here:**

> **Do ComposeBus CAN leaf nodes need ONE hardware accept pattern, or TWO (or more)?**
>
> Concretely: does a typical CAN leaf subscribe to exactly one namespace, or does it need
> (say) `state` on namespace X *and* `heartbeat` from anywhere, or two unrelated
> namespaces, matched **in silicon**?
>
> - If **one**: dual mode is never needed, field position within the identifier is
>   irrelevant, and E1d contributes nothing to the layout choice.
> - If **two or more**: dual mode is needed, only `ID[28:13]` is comparable, and the
>   layouts diverge sharply — L1 gives exact namespace matching, L2 gives a 10-bit hash
>   prefix admitting 64 unrelated namespaces per bank.
>
> A third option exists and is worth naming because the bench can now price it: a leaf
> could use **one** exact single-mode filter and accept that its *second* subscription is
> filtered in software. Whether that is acceptable is a question about node CPU budget and
> expected bus load — also David's.
>
> **Evidence I happened to see that bears on it:** none. I did not read
> `transport_can.py`, `services/can_mqtt_bridge/`, or `spec/` — they are out of scope for
> this bench and hold the layouts under dispute. Nothing in this document is informed by
> them.

---

## 7. E1e — are rejected frames dropped in silicon?

### E1e-1 — accept-all positive control (`RXQ 4`, `DRAIN OFF`, `RX OFF`, 20 frames)

```
OK RXQ rx_queue_len=4 applies=next_INIT
OK DRAIN drain=0
OK RX rx_print=0
OK FILTER code=0x00000000 mask=0xffffffff mode=single reinit=1 counters=zeroed
OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x00000000 mask=0xffffffff filter=single rx_queue_len=4 counters=zeroed
OK BURST ext=1 id=0x12345678 dlc=1 count=20 queued=20 queue_err=0

STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=0 rx_print=0
STAT filter code=0x00000000 mask=0xffffffff mode=single rx_queue_len=4 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=20 fail=0 queue_err=0
STAT rx accepted=0 tally_overflow=0
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=4 tx_failed=0 rx_missed=16 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=20 rx_data=4 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=16 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=8
END STATS
```

### E1e-2 — rejecting filter (`code=0x91A2B3C0`, send `0x0fedcba8` ×20)

```
OK FILTER code=0x91a2b3c0 mask=0x00000007 mode=single reinit=1 counters=zeroed
OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x91a2b3c0 mask=0x00000007 filter=single rx_queue_len=4 counters=zeroed
OK BURST ext=1 id=0x0fedcba8 dlc=1 count=20 queued=20 queue_err=0

STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=0 rx_print=0
STAT filter code=0x91a2b3c0 mask=0x00000007 mode=single rx_queue_len=4 rxpull=none
STAT pins tx_gpio=2 rx_gpio=10
STAT tx ok=20 fail=0 queue_err=0
STAT rx accepted=0 tally_overflow=0
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=20 rx_data=0 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
STAT alert2 lines_suppressed=0
END STATS
```

| | E1e-1 predicted | E1e-1 observed | E1e-2 predicted | E1e-2 observed |
|---|---|---|---|---|
| `msgs_to_rx` | 4 | **4** | 0 | **0** |
| `rx_missed` | 16 | **16** | 0 | **0** |
| `BURST queued=` | 20 | **20** (`queue_err=0`) | 20 | **20** (`queue_err=0`) |

| | |
|---|---|
| **Are rejected frames dropped before the CPU is interrupted?** | **Yes.** |
| Verdict | **PASS** |
| Notes | The positive control is exact: 20 frames sent, queue capacity 4, `msgs_to_rx=4` + `rx_missed=16` = 20, and `rx_queue_full=16` corroborates from the alert side. The rejecting run then transmitted an *identical* 20-frame burst (`tx ok=20`, `tx_failed=0`, so the frames definitely hit the wire) and produced **`rx_missed=0` with `msgs_to_rx=0`** — the RX ISR never ran. Since `rx_missed_count` is incremented inside the RX interrupt handler and the legacy ESP-IDF TWAI driver does no software filtering, this is a clean measurement: **hardware acceptance filtering costs the CPU literally nothing per rejected frame.** `STAT rx accepted=0` in E1e-1 is expected and not a failure — `DRAIN OFF` means `twai_receive()` is never called, so the *software* tally cannot increment even though the *hardware* queued four frames. Read `STAT hw`, not `STAT rx`, for this experiment |

---

## 8. E2 / E3 / E4

| Experiment | Needs | Attempted? | Outcome |
|---|---|---|---|
| E2 — arbitration under contention | 2× C3 + full bus | **No** — only one ESP32-C3 is attached and no transceivers are wired. Not requested for this run | Not established. Nothing about arbitration is inferred anywhere in this document |
| E3 — cross-layout garbage decode | nothing (arithmetic) | **No — deliberately skipped** | The research doc §8 is right that it "is not an experiment and cannot fail". It is also the one item that would have required reading `spec/01-composeio-bus-protocol.smd` for the hash function, and `spec/` is out of scope for this task. Skipped and declared, per §8's own instruction |
| E4 — subscription expressiveness per layout | Track A is enough | **Yes, partially** — three of the four rows were **measured on hardware**, the fourth is derived from E1b/E1d | See table below |

### E4 table — **measured** vs **derived**

| Subscription | Under **L1** `[ns16][type4][src9]` | Under **L2** `[type4][scope2][ns16][seq7]` |
|---|---|---|
| `namespace == X`, **single** filter | **Exact, one bank — MEASURED.** `code=0x12340000 mask=0x0000FFFF single`. `0x02468000` and `0x02469fff` accepted (both `ns_hash=0x1234`), `0x0246a000` (`ns_hash=0x1235`) rejected. `accepted=2`, `bus_error=0` | **Exact, one bank — DERIVED** from the measured L2 rows below; the ns_hash field at `ID[22:7]` is maskable identically to any other identifier field, which the `ID[24:23]` row proves constructively |
| `namespace == X`, **dual** filter | **Exact — MEASURED (this is E1d).** `ID[28:13]` *is* the ns_hash under L1, and the bank compares exactly that window | **10-bit prefix only — DERIVED from E1d.** `ID[28:13]` under L2 is `type(4)+scope(2)+ns_hash[15:6]`; a bank cannot reach `ns_hash[5:0]`, so it admits **64** unrelated namespaces per pattern. Not separately measured, but it is a direct arithmetic consequence of the measured "only `ID[28:13]` is compared" |
| `scope == global` | **NOT filterable in hardware for EFF — MEASURED (this is E1b).** Under L1, `scope` lives in **data byte 0 bits 7..6** (spec `[BP-CAN-DATA]`), and E1b shows extended-frame filters cannot see data bytes at all. It would be filterable only for *standard* frames (E1c) | **Exact, one bank — MEASURED.** `code=0x04000000 mask=0xF3FFFFFF single` compares only `ID[24:23]`. Sent five frames: `0x00800000` and `0x1effffff` (both `ID[24:23]=01`) **accepted**; `0x1e7fffff` (`00`), `0x1f7fffff` (`10`), `0x1fffffff` (`11`) **rejected**. `accepted=2`, `tx ok=5`, `bus_error=0`. Note the accepted pair differ in **27 of the other bits** — this is the direct proof that field position is irrelevant in single-filter mode |
| `scope == global` AND `namespace == X` | **Impossible for EFF in one bank — MEASURED consequence of E1b.** It would require comparing an identifier field and a data byte simultaneously, and the extended-frame filter has no data-byte inputs | **Exact, one bank — MEASURED.** `code=0x0448D000 mask=0xF00003FF single` compares `ID[24:7]` (scope + ns_hash together). Sent four frames: `0x00891a00` (type 0, seq 0) and `0x1e891a7f` (type 0xF, seq 0x7F) **accepted**; `0x00091a00` (scope 00) and `0x00891a80` (ns_hash `0x1235`) **rejected**. `accepted=2`, `tx ok=4`, `bus_error=0` |

Raw counters for the three measured E4 rounds:

```
--- L1 namespace==X, SINGLE ---
OK FILTER code=0x12340000 mask=0x0000ffff mode=single
EVT RX ext=1 rtr=0 id=0x02468000 dlc=1 data=a5 t=310498
EVT RX ext=1 rtr=0 id=0x02469fff dlc=1 data=a5 t=311141
STAT tx ok=3 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x02468000 n=1
STAT rxid ext=1 id=0x02469fff n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0

--- L2 scope==global, SINGLE (field at ID[24:23]) ---
OK FILTER code=0x04000000 mask=0xf3ffffff mode=single
EVT RX ext=1 rtr=0 id=0x00800000 dlc=1 data=a5 t=317845
EVT RX ext=1 rtr=0 id=0x1effffff dlc=1 data=a5 t=318486
STAT tx ok=5 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x00800000 n=1
STAT rxid ext=1 id=0x1effffff n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0

--- L2 scope==global AND namespace==X, SINGLE, ONE bank (fields at ID[24:23] + ID[22:7]) ---
OK FILTER code=0x0448d000 mask=0xf00003ff mode=single
EVT RX ext=1 rtr=0 id=0x00891a00 dlc=1 data=a5 t=348414
EVT RX ext=1 rtr=0 id=0x1e891a7f dlc=1 data=a5 t=349059
STAT tx ok=4 fail=0 queue_err=0
STAT rx accepted=2 tally_overflow=0
STAT rxid ext=1 id=0x00891a00 n=1
STAT rxid ext=1 id=0x1e891a7f n=1
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
```

---

## 9. Summary of findings

**What the hardware does. No layout is recommended.**

| Question | Answer | Confidence | Evidence (section) |
|---|---|---|---|
| Can EFF single-filter match on data bytes? | **NO — identifier only** | **High.** Positive control passed (E1a), the discriminator passed (E1c), the distribution matched H0 exactly (A 2/2, B 0/2), a mirror control flipped it exactly, `EVT RX data=` confirms an accepted frame with the "wrong" data byte and a rejected frame with the "right" one, and every validity gate was clean | §4 |
| Can SFF single-filter match on data bytes 1–2? | **YES — and the identifier can be fully masked out while doing it** | **High.** Three different identifiers (`0x123`, `0x456`, `0x7ff`) accepted on data alone; two frames with the matching identifier rejected on data alone | §5 |
| Does dual-filter EFF compare only `ID[28:13]`? | **YES.** `ID[12:0]` and RTR are not compared at all | **High.** An identifier differing in all 13 low bits was accepted; both bank orderings gave byte-identical results, so bank 2 is genuinely live | §6 |
| Are filtered-out frames dropped before the CPU sees them? | **YES — zero ISR entries for 20 rejected frames** | **High.** Positive control landed exactly on `msgs_to_rx=4 / rx_missed=16`; the rejecting run produced `0 / 0` from an identical burst that provably reached the wire (`tx ok=20`, `tx_failed=0`) | §7 |
| Is field position within the identifier relevant in single-filter mode? | **NO** | **High.** Measured, not derived — a 2-bit field at `ID[24:23]` matched exactly across identifiers differing in 27 other bits | §8 |
| Did anything contradict the ESP32-C3 TRM? | **NO.** The silicon agreed with TRM v1.4 §31.4.6.1 and §31.4.6.2 on every point tested, and the document's derived hex literals were confirmed on-chip by `CALC` | **High** | §1, §4, §5, §6 |

> Recorded because it was worth checking and would have been the night's biggest result:
> the silicon did **not** disagree with the documentation anywhere. What *did* disagree
> with reality was the assignment's description of the **bench** (§10).

### Questions handed BACK to David (not answered here)

| Question | Why it is his |
|---|---|
| **Do ComposeBus CAN leaves need one accept pattern or two?** | Decides whether E1d's dual-mode finding constrains the layout at all. One pattern → single mode → position irrelevant → the decision rests wholly on E1b. Two → dual mode → only `ID[28:13]` is comparable → L1 matches exactly, L2 matches a 10-bit hash prefix admitting 64 unrelated namespaces (§6) |
| **Is software rejection of a second subscription acceptable?** | The bench can now price the middle option — one exact hardware pattern plus software filtering for the rest. Whether that fits the CPU budget of a leaf is a system question, not a silicon one (§6) |
| **Should the bench jumper be moved to GPIO3, or should `TWAI_TX_GPIO` become 2?** | I ran on GPIO2 because that is where the wire is. Moving the wire is the right fix — GPIO2 is a strapping pin. But it is his bench and his wire (§10, W1) |
| **Does the §0.1 board (`00:00:5e:00:53:02`) still need restoring?** | It is not attached, so I could not act. If it matters, it must be plugged in and MAC-verified first (§0.1) |

### Anything that surprised you

1. **The bench was wired to the wrong pin, and the failure looked nothing like a wiring
   fault.** This is the big one. See §10. It produced a *fully self-consistent* false null:
   frames queued fine, left the queue fine (`msgs_to_tx=0`), and simply never arrived. Had
   I trusted the totals, E1-CONTROL would have read as "0 of 6 accepted".
2. **The one counter that gave it away was `tec`.** A *single* single-shot frame drove
   `tec` to **136** — 17 error increments from one transmission. That is only possible if
   the transmitter's own error flag is also failing to read back, i.e. RX is stuck
   recessive. `tec` jumping straight past the error-passive threshold from one frame is a
   sharper "TX is not reaching RX" tell than anything in research doc §5.7, and it is not
   listed there. **Suggested addition to the doc's failure table: `tec ≥ 128 after a single
   frame ⇒ the RX path is not carrying the TX signal.**
3. **`RXPULL` cannot inject wiring faults once the jumper is fitted** (W2 below). An
   internal ~45 kΩ pull loses to a push-pull driver, every time.
4. **The `CALC` cross-check was completely clean.** All three literals matched the document
   bit for bit. The one thing in the assignment that was most likely to contain an
   arithmetic slip contained none.
5. **`STAT rx accepted=0` in E1e-1 is correct behaviour and reads like a failure.** With
   `DRAIN OFF` the software tally cannot increment. Anyone skimming E1e will see
   `accepted=0` on the *positive control* and think it broke.
6. **Both E1d bank orderings matched exactly**, so the well-known esp-idf#11897 dead-bank-2
   trap did not reproduce. Worth recording as a negative: on this part, with this driver,
   bank 2 is live.

### Anything in the assignment document you found to be WRONG

Five items. Two are consequential.

| # | Where | The claim | What is actually true |
|---|---|---|---|
| **W1** | Assignment brief / research doc §2.3, §3.1 | "A dupont wire is fitted GPIO3 → GPIO10." Stated as verified hardware state | **The wire is fitted GPIO2 ↔ GPIO10.** Verified by DC continuity in both directions: `PROBE 2 10 → wired=1` and `PROBE 10 2 → wired=1`; every other pad pair on the chip probed `wired=0`. GPIO3 is genuinely floating (`free_pullup_reads=1, free_pulldown_reads=0`). **This invalidated an entire E1 run.** The assignment doc's author has now misidentified the hardware twice — once the board (its own §0.1/§2.1), once the wire |
| **W2** | Research doc §5.7 and `tools/bench.py::wirecheck` | The `RXPULL` rehearsal "reproduces both 'the wire is wrong' shapes... in about thirty seconds" and is "worth doing **even on Track A**" | **It reproduces neither once the Track A jumper is fitted.** Measured: `RXPULL down` → frame transmits and is *received normally* (`accepted=2, tec=0, bus_error=0`); `RXPULL up` → total silence, `bus_error=0`, `lines_suppressed=0`, no storm. An internal pull cannot override the far end's push-pull driver. The signatures printed in §5.7 were necessarily captured with **no jumper fitted** — which matches §0.2's note that no CAN hardware was wired on 2026-08-10. **The rehearsal is only valid with the wire removed, and the doc should say so** |
| **W3** | `tools/bench.py::wirecheck` | Issues `DEINIT` → `RXPULL <mode>` → `INIT` | The ESP-IDF legacy TWAI driver reconfigures the RX pad when the driver installs, so a pull set **before** `INIT` may not survive it. Any future fault-injection must apply `RXPULL` **after** `INIT`. (Even applied after, it still loses to the jumper — W2.) Not fixed in this run; recorded |
| **W4** | `tools/bench.py::control` report string | `"accepted=6 across 5 distinct ids"` | **Four** distinct ids, not five: `0x12345678`, `0x12345679`, `0x024694a0`×2, `0x02468b40`×2. Research doc §7's own prediction table lists four. Cosmetic, but it is a predicted value printed next to an observed one, which is exactly where an off-by-one is most likely to be copied into a conclusion |
| **W5** | Research doc §0.1 / §2.1 | ESP32-C3 at `/dev/cu.usbmodem31101` has MAC `00:00:5e:00:53:02`; an ESP32-C6 sits at `/dev/cu.usbmodem31201` | Neither is present. `/dev/cu.usbmodem31101` is now a **different** ESP32-C3, MAC `00:00:5e:00:53:03`. No second board is attached. §2.1 is correctly labelled an anecdote — but §0.1 is *not* so labelled and reads as current fact, and a reader following §0.1 literally would flash a 4 MB backup onto the wrong physical board. **§0.1 needs the same "verify the MAC first" hazard framing that §4.2 has** |

**On W1, and how it should be caught next time.** The failure mode deserves promotion into
research doc §10's table, because it defeats the existing gates. The doc's §10 item 5
gates on `msgs_to_tx > 0`, and this fault produces `msgs_to_tx = 0` — frames leave the
queue, they simply die on the wire. The distinguishing signature is `tx_failed = <every
frame>` together with `tec ≥ 128` **and** `bus_error > 0`. `bench.py`'s validity gate does
already flag `tx_failed` and `bus_error`, and it correctly refused to let the bad run be
read as a result — the instrument behaved well. What was missing was any way to find out
*where the wire actually was* without reflashing once per guess. Hence the `PROBE` command
(§11).

### Firmware changes made during the run

Three, all inside `firmware/can_filter_bench/`, all additive, none altering the TWAI
configuration path used by any experiment.

| File | Change | Why |
|---|---|---|
| `src/main.cpp` | `TWAI_TX_GPIO` / `TWAI_RX_GPIO` promoted from compile-time-only to runtime variables `g_tx_gpio` / `g_rx_gpio`, still defaulting to the `-D` build flags. New `STAT pins tx_gpio=.. rx_gpio=..` line; `ID` and `OK INIT` now echo the live values | Without it, "the jumper is one hole out" is indistinguishable from "the controller is broken" except by reflashing per guess. Every result in this file records the pins it was taken with |
| `src/main.cpp` | New `PINS <tx> <rx>` command, applied at next `INIT` | Lets the bench point TWAI at the pads the wire is actually on. **This is how every E1 result here was obtained** (`PINS 2 10`) |
| `src/main.cpp` | New `PROBE <drv> <sense>` command — drives one pad high then low as push-pull, reads the other; then releases and re-reads `sense` with its own pull-up and pull-down to distinguish *floating* from *tied to a rail*. Requires `DEINIT`; refuses to drive GPIO18/19 (USB) | This is what found the misplaced wire. It also distinguishes "no wire" from "wire to the wrong pad" from "pad externally pulled", which no existing command could |
| `tools/bench.py` | New `--pins TX,RX` option; `Rig.configure_roles()` issues `DEINIT` + `PINS` when given | So the scripted protocol runs unmodified on the real wiring |

Notes on the changes: `PROBE` never runs while the TWAI driver is installed, and leaves
both pads as plain inputs. No experiment logic, filter arithmetic, timing config, counter
handling or decision rule was touched. The `CALC` implementation is untouched, so its
agreement with the document is still an independent check. Build stamp moved
`bada7e85` → `c8c58d8b`; **all reported E1 results are from `c8c58d8b`.**

### What was NOT established, and what it would take

| Not established | What it would take |
|---|---|
| **E2 — arbitration under contention** | A second ESP32-C3, two SN65HVD230 breakouts, a terminated bus (~60 Ω), a common ground, and the GPIO1↔GPIO1 trigger line. Genuinely impossible on one board. Not requested |
| **Track B confirmation of E1** | Same hardware as E2. Would guard against the (remote) possibility that self-reception and peer-reception take different paths through the acceptance filter. E1a rules out the *gross* version of that concern — self-reception plainly does pass through the filter, since it discriminated single ID bits — but "identical in every respect to peer reception" is not proven. Worth doing only if the bus gets built for E2 anyway |
| **E3 — cross-layout garbage decode** | Nothing but 5 minutes of Python and read access to `spec/01-composeio-bus-protocol.smd` §1.5.3. Skipped deliberately: it is arithmetic, it cannot fail, and it is the one item that would have pulled `spec/` into a task scoped to exclude it |
| **The L2 dual-mode 10-bit-prefix cost, measured rather than derived** | One more `FILTER` + `SEND` round: a dual-mode filter on an L2-shaped identifier plus a frame from a different namespace sharing the top 10 hash bits, to watch the false positive arrive. Straightforward on Track A. Not run because constructing a *realistic* colliding pair needs the ComposeBus namespace hash from `spec/`, which is out of scope. The arithmetic consequence of the measured "only `ID[28:13]` is compared" is not in doubt |
| **Whether GPIO3 itself works as TWAI TX on this board** | Move the jumper to GPIO3 and re-run with default pins. GPIO3 probed as a healthy floating pad, so there is no reason to expect otherwise — but it was never exercised as TWAI TX here |

---

## 10. The wiring fault, in full — read this before re-running

The first complete E1 run on this bench produced a **clean-looking total null**, and it
was wrong. Recorded in detail because it is reproducible and because it defeats one of the
gates the research document relies on.

### What was observed (run 1, default pins `tx=3 rx=10`)

```
E1-CONTROL, FILTER 0x00000000 0xFFFFFFFF single, six SEND ext:
STAT tx ok=6 fail=0 queue_err=0
STAT rx accepted=0 tally_overflow=0
STAT hw state=RUNNING tec=176 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=6 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=7

E1a, FILTER 0x91A2B3C0 0x00000007 single, four SEND ext:
STAT tx ok=4 fail=0 queue_err=0
STAT rx accepted=0 tally_overflow=0
STAT hw state=RUNNING tec=160 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=4 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=7
```

`msgs_to_tx=0` — so research doc §5.7's fault-A gate says the frames *did* leave the
queue. `state=RUNNING`. Every frame accepted by `twai_transmit()`. The only tells are
`tx_failed` and a `tec` that reaches error-passive off a single frame.

### The diagnosis

`tec=136` from **one** single-shot frame is 17 error increments. A transmitter that detects
a bit error sends a 6-bit dominant error flag; if the error flag *also* fails to read back,
it errors again, +8 each time, until error-passive. That only happens when the RX path is
**stuck recessive** — the transmitter's dominant bits are not coming back.

### Locating the wire

`PROBE <drv> <sense>` was added for this. Driving GPIO3 and sensing every other usable pad:

```
OK PROBE drv=3 sense=10 drv_hi_reads=1 drv_lo_reads=1 wired=0 free_pullup_reads=1 free_pulldown_reads=1
OK PROBE drv=3 sense=0  drv_hi_reads=0 drv_lo_reads=0 wired=0 free_pullup_reads=1 free_pulldown_reads=0
OK PROBE drv=3 sense=1  ... wired=0    (floating)
OK PROBE drv=3 sense=2  drv_hi_reads=1 drv_lo_reads=1 wired=0 free_pullup_reads=1 free_pulldown_reads=1
OK PROBE drv=3 sense=4,5,7,21 ... wired=0  (floating)
OK PROBE drv=3 sense=6,8,9,20 ... wired=0
OK PROBE drv=10 sense=3 drv_hi_reads=0 drv_lo_reads=0 wired=0 free_pullup_reads=1 free_pulldown_reads=0
```

**Nothing follows GPIO3.** GPIO10 reads `1` whether GPIO3 is driven high *or* low, and
still reads `1` against its own internal pull-down — so it is held high by something
external. Driving every other pad against GPIO10:

```
OK PROBE drv=2  sense=10 drv_hi_reads=1 drv_lo_reads=0 wired=1 free_pullup_reads=1 free_pulldown_reads=1
OK PROBE drv=10 sense=2  drv_hi_reads=1 drv_lo_reads=0 wired=1 free_pullup_reads=1 free_pulldown_reads=1
OK PROBE drv=0,1,4,5,6,7,8,9,20,21 sense=10 ... wired=0   (all)
```

**GPIO2 ↔ GPIO10, confirmed from both ends.** GPIO10 read high because GPIO2 is a strapping
pin carrying an external pull-up on this board, and the jumper propagated it.

The wire is one header position off: on a typical ESP32-C3 board GPIO2 sits immediately
beside GPIO3.

### The fix used, and its caveat

`PINS 2 10` — TWAI TX retargeted to GPIO2, the pad the wire is on. Immediately clean:

```
OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 ...
OK SEND ext=1 id=0x12345678 dlc=1 queued=1 esp_err=0x0
EVT RX ext=1 rtr=0 id=0x12345678 dlc=1 data=a5 t=103953
STAT tx ok=1 fail=0 queue_err=0
STAT rx accepted=1 tally_overflow=0
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
```

**Caveat, stated plainly.** GPIO2 is one of the C3's three strapping pins (Datasheet v2.4
§3, Table 3-1) and must read HIGH at reset for a normal boot. Running TWAI TX on it is
**not** a configuration to keep:

- It is safe *here* because TWAI TX idles recessive (high), the board carries an external
  pull-up on GPIO2, and the driver was uninstalled at the end (§13), leaving the pad an
  input.
- It would be unsafe as a standing configuration: a reset arriving while TX happens to be
  driving GPIO2 dominant (low) could put the chip into the wrong boot mode. Recoverable by
  a power cycle, but exactly the "the board sometimes doesn't start" fault that research
  doc §2.3 item 5 warns about.
- **The correct fix is to move the jumper one hole, from GPIO2 to GPIO3**, and re-run with
  default pins. `TWAI_TX_GPIO` should **not** be changed to 2 in `platformio.ini`.

**Does this affect any result in this document?** No. The acceptance filter lives inside
the TWAI controller's receive path; the GPIO matrix routing is invisible to it. Every
experiment's positive control passed on these pins, and E1c independently demonstrated
that data-byte filtering works through this exact signal path — so the path is not
selectively dropping anything.

---

## 11. Reproducing this run

```bash
cd firmware/can_filter_bench
pio run -e c3_usbcdc -t upload --upload-port /dev/cu.usbmodem31101

# 1. find out where the jumper actually is, BEFORE trusting any count
#    (DEINIT first; PROBE refuses to run with the driver installed)
#    -> look for wired=1
#
# 2. then run the protocol against those pads
python3 tools/bench.py --loop /dev/cu.usbmodem31101 --pins 2,10 --exp all | tee /tmp/e1.log
```

If the jumper has been moved to GPIO3 as recommended, drop `--pins` entirely.

`--pins` and `PROBE`/`PINS` are additions from this run; see §9 "Firmware changes".

---

## 12. Raw transcript — the full scripted run, verbatim

Everything below is the unedited output of

```
python3 firmware/can_filter_bench/tools/bench.py --loop /dev/cu.usbmodem31101 --pins 2,10 --exp all
```

on firmware `stamp=c8c58d8b`, board MAC `00:00:5e:00:53:03`, 2026-08-10. `L<` is a command
sent to the board; `L|` is a line the board emitted.
```

### PRE-FLIGHT — identity and build stamp

L< ID
L| # > ID
L| ID fw=can_filter_bench ver=1.0.0 stamp=c8c58d8b built=Aug_10_2026_16:23:08
L| ID chip=esp32c3 rev=4 cores=1 mac=1cdbd4c28020 idf=4.4.6
L| ID tx_gpio=2 rx_gpio=10 trig_gpio=1 role=loop cdc=1
L| END ID
L< DEINIT
L| # > DEINIT
L| OK DEINIT installed=0
L< PINS 2 10
L| # > PINS 2 10
L| OK PINS tx_gpio=2 rx_gpio=10 note=applied_at_next_INIT
L< ROLE loop
L| # > ROLE loop
L| OK ROLE role=loop rx_print=1 drain=1 self=1 note=run_INIT_<kbps>_no-ack

#   Cross-checking the document's filter literals against numbers
#   computed on the silicon under test:
L< CALC ext 0x12345678
L| # > CALC ext 0x12345678
L| OK CALC kind=ext_single id=0x12345678 code=0x91a2b3c0 mask_exact=0x00000007 mask_top16=0x0000ffff
L< CALC std 0x123 0xa5 0x5a
L| # > CALC std 0x123 0xa5 0x5a
L| OK CALC kind=std_single id=0x123 d0=0xa5 d1=0x5a code=0x2460a55a mask_data_only=0xffff0000 mask_id_and_data=0x001f0000 mask_id_only=0x001fffff
L< CALC dual 0x02468000 0x1fffffff
L| # > CALC dual 0x02468000 0x1fffffff
L| OK CALC kind=dual_ext id1=0x02468000 id2=0x1fffffff f1_top16=0x1234 f2_top16=0xffff code=0x1234ffff mask_exact=0x00000000

### E1-CONTROL — accept-all filter; all six frames must arrive

L< FILTER 0x00000000 0xFFFFFFFF single
L| # > FILTER 0x00000000 0xFFFFFFFF single
L| OK FILTER code=0x00000000 mask=0xffffffff mode=single reinit=0 counters=unchanged
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x00000000 mask=0xffffffff filter=single rx_queue_len=16 counters=zeroed
L< SEND ext 0x12345678 a5
L| # > SEND ext 0x12345678 a5
L| OK SEND ext=1 id=0x12345678 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x12345679 a5
L| # > SEND ext 0x12345679 a5
L| OK SEND ext=1 id=0x12345679 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x024694a0 a5
L| # > SEND ext 0x024694a0 a5
L| OK SEND ext=1 id=0x024694a0 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x024694a0 5a
L| # > SEND ext 0x024694a0 5a
L| OK SEND ext=1 id=0x024694a0 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02468b40 a5
L| # > SEND ext 0x02468b40 a5
L| OK SEND ext=1 id=0x02468b40 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02468b40 5a
L| # > SEND ext 0x02468b40 5a
L| OK SEND ext=1 id=0x02468b40 dlc=1 queued=1 esp_err=0x0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
L| STAT filter code=0x00000000 mask=0xffffffff mode=single rx_queue_len=16 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=6 fail=0 queue_err=0
L| STAT rx accepted=6 tally_overflow=0
L| STAT rxid ext=1 id=0x12345678 n=1
L| STAT rxid ext=1 id=0x12345679 n=1
L| STAT rxid ext=1 id=0x024694a0 n=2
L| STAT rxid ext=1 id=0x02468b40 n=2
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=6 rx_data=6 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1-CONTROL — OBSERVED ===
    predicted (documented behaviour): accepted=6 across 5 distinct ids, bus_error=0
    rx accepted total : 6
    rx id=0x02468b40 n=2
    rx id=0x024694a0 n=2
    rx id=0x12345678 n=1
    rx id=0x12345679 n=1
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)


### E1a — POSITIVE CONTROL: extended, single filter, exact 29-bit id

#   Without E1a passing, a null in E1b means nothing at all.
L< FILTER 0x91A2B3C0 0x00000007 single
L| # > FILTER 0x91A2B3C0 0x00000007 single
L| OK FILTER code=0x91a2b3c0 mask=0x00000007 mode=single reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x91a2b3c0 mask=0x00000007 filter=single rx_queue_len=16 counters=zeroed
L< SEND ext 0x12345678 a5
L| # > SEND ext 0x12345678 a5
L| OK SEND ext=1 id=0x12345678 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x12345679 a5
L| # > SEND ext 0x12345679 a5
L| OK SEND ext=1 id=0x12345679 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02345678 a5
L| # > SEND ext 0x02345678 a5
L| OK SEND ext=1 id=0x02345678 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x12345670 a5
L| # > SEND ext 0x12345670 a5
L| OK SEND ext=1 id=0x12345670 dlc=1 queued=1 esp_err=0x0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
L| STAT filter code=0x91a2b3c0 mask=0x00000007 mode=single rx_queue_len=16 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=4 fail=0 queue_err=0
L| STAT rx accepted=1 tally_overflow=0
L| STAT rxid ext=1 id=0x12345678 n=1
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=4 rx_data=1 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1a — OBSERVED ===
    predicted (documented behaviour): accepted=1, only id=0x12345678
    rx accepted total : 1
    rx id=0x12345678 n=1
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)


### E1b — THE QUESTION: extended, single filter, code/mask placed at a DATA-BYTE position

#   code=0x0000A500  mask=0xFFFF00FF
#   Under the ID-only interpretation, code bits 15..8 == ID[12:5].
#   Set A ids have ID[12:5]=0xA5; set B ids have ID[12:5]=0x5A.
#   Each set has one frame with data[0]=0xA5 and one with data[0]=0x5A.
#     ID-only       -> A: 2/2   B: 0/2    (total 2)
#     data-byte     -> A: 1/2   B: 1/2    (total 2)
#   SAME TOTAL, DIFFERENT DISTRIBUTION. Neither a stuck-accept nor a
#   stuck-reject nor a dead bus can produce either pattern, so the
#   result cannot be faked by a misconfiguration.

L< FILTER 0x0000A500 0xFFFF00FF single
L| # > FILTER 0x0000A500 0xFFFF00FF single
L| OK FILTER code=0x0000a500 mask=0xffff00ff mode=single reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x0000a500 mask=0xffff00ff filter=single rx_queue_len=16 counters=zeroed
L< SEND ext 0x024694a0 a5
L| # > SEND ext 0x024694a0 a5
L| OK SEND ext=1 id=0x024694a0 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x024694a0 5a
L| # > SEND ext 0x024694a0 5a
L| OK SEND ext=1 id=0x024694a0 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02468b40 a5
L| # > SEND ext 0x02468b40 a5
L| OK SEND ext=1 id=0x02468b40 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02468b40 5a
L| # > SEND ext 0x02468b40 5a
L| OK SEND ext=1 id=0x02468b40 dlc=1 queued=1 esp_err=0x0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
L| STAT filter code=0x0000a500 mask=0xffff00ff mode=single rx_queue_len=16 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=4 fail=0 queue_err=0
L| STAT rx accepted=2 tally_overflow=0
L| STAT rxid ext=1 id=0x024694a0 n=2
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=4 rx_data=2 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1b — OBSERVED ===
    predicted (documented behaviour): ID-only: id=0x024694a0 n=2 and id=0x02468b40 absent
    rx accepted total : 2
    rx id=0x024694a0 n=2
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)


### E1c — DISAMBIGUATOR: STANDARD frame, single filter, data bytes 1-2

#   If E1b is null AND E1c filters, that is a real capability boundary.
#   If E1c is ALSO null, we configured something wrong and E1b proves
#   nothing. id=0x456 and id=0x7ff passing is what proves the match is
#   on DATA and not on the identifier.

L< FILTER 0x2460A55A 0xFFFF0000 single
L| # > FILTER 0x2460A55A 0xFFFF0000 single
L| OK FILTER code=0x2460a55a mask=0xffff0000 mode=single reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x2460a55a mask=0xffff0000 filter=single rx_queue_len=16 counters=zeroed
L< SEND std 0x123 a55a
L| # > SEND std 0x123 a55a
L| OK SEND ext=0 id=0x00000123 dlc=2 queued=1 esp_err=0x0
L< SEND std 0x123 a55b
L| # > SEND std 0x123 a55b
L| OK SEND ext=0 id=0x00000123 dlc=2 queued=1 esp_err=0x0
L< SEND std 0x123 5a5a
L| # > SEND std 0x123 5a5a
L| OK SEND ext=0 id=0x00000123 dlc=2 queued=1 esp_err=0x0
L< SEND std 0x456 a55a
L| # > SEND std 0x456 a55a
L| OK SEND ext=0 id=0x00000456 dlc=2 queued=1 esp_err=0x0
L< SEND std 0x7ff a55a
L| # > SEND std 0x7ff a55a
L| OK SEND ext=0 id=0x000007ff dlc=2 queued=1 esp_err=0x0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
L| STAT filter code=0x2460a55a mask=0xffff0000 mode=single rx_queue_len=16 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=5 fail=0 queue_err=0
L| STAT rx accepted=3 tally_overflow=0
L| STAT rxid ext=0 id=0x00000123 n=1
L| STAT rxid ext=0 id=0x00000456 n=1
L| STAT rxid ext=0 id=0x000007ff n=1
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=5 rx_data=3 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1c — OBSERVED ===
    predicted (documented behaviour): accepted=3: 0x123/a55a, 0x456/a55a, 0x7ff/a55a; 0x123/a55b and 0x123/5a5a rejected
    rx accepted total : 3
    rx id=0x00000123 n=1
    rx id=0x00000456 n=1
    rx id=0x000007ff n=1
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)


### E1d — LAYOUT DISCRIMINATOR: dual filter mode, extended frames

#   filter1 requires ID[28:13]==0x1234; filter2 requires 0xFFFF (never sent).
#   0x02469fff differs from 0x02468000 ONLY in ID bits 12..0.
L< FILTER 0x1234FFFF 0x00000000 dual
L| # > FILTER 0x1234FFFF 0x00000000 dual
L| OK FILTER code=0x1234ffff mask=0x00000000 mode=dual reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x1234ffff mask=0x00000000 filter=dual rx_queue_len=16 counters=zeroed
L< SEND ext 0x02468000 a5
L| # > SEND ext 0x02468000 a5
L| OK SEND ext=1 id=0x02468000 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02469fff a5
L| # > SEND ext 0x02469fff a5
L| OK SEND ext=1 id=0x02469fff dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x0246a000 a5
L| # > SEND ext 0x0246a000 a5
L| OK SEND ext=1 id=0x0246a000 dlc=1 queued=1 esp_err=0x0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
L| STAT filter code=0x1234ffff mask=0x00000000 mode=dual rx_queue_len=16 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=3 fail=0 queue_err=0
L| STAT rx accepted=2 tally_overflow=0
L| STAT rxid ext=1 id=0x02468000 n=1
L| STAT rxid ext=1 id=0x02469fff n=1
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=3 rx_data=2 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1d-part1 — OBSERVED ===
    predicted (documented behaviour): accepted=2 (0x02468000, 0x02469fff); 0x0246a000 rejected
    rx accepted total : 2
    rx id=0x02468000 n=1
    rx id=0x02469fff n=1
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)


#   part 2 — identical ids, filter banks SWAPPED. If part 2 differs from
#   part 1, filter bank 2 is not live and part 1 was a one-bank result.
L< FILTER 0xFFFF1234 0x00000000 dual
L| # > FILTER 0xFFFF1234 0x00000000 dual
L| OK FILTER code=0xffff1234 mask=0x00000000 mode=dual reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0xffff1234 mask=0x00000000 filter=dual rx_queue_len=16 counters=zeroed
L< SEND ext 0x02468000 a5
L| # > SEND ext 0x02468000 a5
L| OK SEND ext=1 id=0x02468000 dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x02469fff a5
L| # > SEND ext 0x02469fff a5
L| OK SEND ext=1 id=0x02469fff dlc=1 queued=1 esp_err=0x0
L< SEND ext 0x0246a000 a5
L| # > SEND ext 0x0246a000 a5
L| OK SEND ext=1 id=0x0246a000 dlc=1 queued=1 esp_err=0x0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
L| STAT filter code=0xffff1234 mask=0x00000000 mode=dual rx_queue_len=16 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=3 fail=0 queue_err=0
L| STAT rx accepted=2 tally_overflow=0
L| STAT rxid ext=1 id=0x02468000 n=1
L| STAT rxid ext=1 id=0x02469fff n=1
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=3 rx_data=2 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1d-part2 — OBSERVED ===
    predicted (documented behaviour): identical to part 1: accepted=2
    rx accepted total : 2
    rx id=0x02468000 n=1
    rx id=0x02469fff n=1
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)


### E1e — does a REJECTED frame reach the CPU at all?

#   rx queue = 4, DRAIN OFF (nothing is ever dequeued).
#   rx_missed_count is incremented IN THE RX ISR when a frame arrives
#   and the queue is full. rx_missed>0 therefore proves the CPU was
#   interrupted. rx_missed==0 AND msgs_to_rx==0 proves the frame was
#   dropped in silicon before any interrupt was raised — which is the
#   only thing that makes hardware filtering worth having.

L< RXQ 4
L| # > RXQ 4
L| OK RXQ rx_queue_len=4 applies=next_INIT
L< DRAIN OFF
L| # > DRAIN OFF
L| OK DRAIN drain=0
L< RX OFF
L| # > RX OFF
L| OK RX rx_print=0
#   E1e-1 accept-all (positive control)
L< FILTER 0x00000000 0xFFFFFFFF single
L| # > FILTER 0x00000000 0xFFFFFFFF single
L| OK FILTER code=0x00000000 mask=0xffffffff mode=single reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x00000000 mask=0xffffffff filter=single rx_queue_len=4 counters=zeroed
L< BURST ext 0x12345678 a5 20 5
L| # > BURST ext 0x12345678 a5 20 5
L| EVT ALERT flags=0x00000802 t=178351
L| EVT ALERT flags=0x00000802 t=178356
L| EVT ALERT flags=0x00000802 t=178361
L| EVT ALERT flags=0x00000802 t=178366
L| EVT ALERT flags=0x00000802 t=178371
L| EVT ALERT flags=0x00000802 t=178376
L| EVT ALERT flags=0x00000802 t=178381
L| EVT ALERT flags=0x00000802 t=178386
L| EVT ALERT_THROTTLED note=further_alerts_this_second_suppressed_see_STATS
L| OK BURST ext=1 id=0x12345678 dlc=1 count=20 queued=20 queue_err=0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=0 rx_print=0
L| STAT filter code=0x00000000 mask=0xffffffff mode=single rx_queue_len=4 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=20 fail=0 queue_err=0
L| STAT rx accepted=0 tally_overflow=0
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=4 tx_failed=0 rx_missed=16 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=20 rx_data=4 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=16 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=8
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1e-1 accept-all — OBSERVED ===
    predicted (documented behaviour): msgs_to_rx=4 rx_missed=16
    rx accepted total : 0
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=16 msgs_to_rx=4 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)

#   E1e-2 rejecting filter (accepts only 0x12345678; we send 0x0fedcba8)
L< FILTER 0x91A2B3C0 0x00000007 single
L| # > FILTER 0x91A2B3C0 0x00000007 single
L| OK FILTER code=0x91a2b3c0 mask=0x00000007 mode=single reinit=1 counters=zeroed
L< INIT 500 no-ack
L| # > INIT 500 no-ack
L| OK INIT bitrate_kbps=500 mode=no-ack tx_gpio=2 rx_gpio=10 code=0x91a2b3c0 mask=0x00000007 filter=single rx_queue_len=4 counters=zeroed
L< BURST ext 0x0fedcba8 a5 20 5
L| # > BURST ext 0x0fedcba8 a5 20 5
L| OK BURST ext=1 id=0x0fedcba8 dlc=1 count=20 queued=20 queue_err=0
L< STATS
L| # > STATS
L| STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=0 rx_print=0
L| STAT filter code=0x91a2b3c0 mask=0x00000007 mode=single rx_queue_len=4 rxpull=none
L| STAT pins tx_gpio=2 rx_gpio=10
L| STAT tx ok=20 fail=0 queue_err=0
L| STAT rx accepted=0 tally_overflow=0
L| STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
L| STAT alert tx_success=20 rx_data=0 below_warn=0 err_active=0 recovery=0 recovered=0 arb_lost=0 above_warn=0 bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 rx_fifo_overrun=0 tx_retried=0 periph_reset=0
L| STAT alert2 lines_suppressed=0
L| STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
L| END STATS

=== E1e-2 rejecting — OBSERVED ===
    predicted (documented behaviour): msgs_to_rx=0 rx_missed=0
    rx accepted total : 0
    rx hw: state=RUNNING tec=0 rec=0 bus_error=0 rx_missed=0 msgs_to_rx=0 msgs_to_tx=0 arb_lost=0 tx_failed=0
    validity gate: OK (bus healthy, all frames left the TX queue)
    (verdict is YOURS — apply the decision rule in the research doc)

L< DRAIN ON
L| # > DRAIN ON
L| OK DRAIN drain=1
L< RX ON
L| # > RX ON
L| OK RX rx_print=1
L< RXQ 16
L| # > RXQ 16
L| OK RXQ rx_queue_len=16 applies=next_INIT

### DONE. The transcript above IS the evidence. Transcribe raw counters
### into docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md — not just verdicts.
```

---

## 13. Final bench state

The board was left idle and safe:

```
OK DEINIT installed=0
OK RXPULL rx_gpio=10 pull=none esp_err=0x0
OK PROBE drv=10 sense=2 drv_hi_reads=1 drv_lo_reads=0 wired=1 free_pullup_reads=1 free_pulldown_reads=1
OK PINS tx_gpio=3 rx_gpio=10 note=applied_at_next_INIT
STAT cfg role=loop installed=0 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT pins tx_gpio=3 rx_gpio=10
```

- **TWAI driver uninstalled** (`installed=0`).
- **RX pull restored to `none`.**
- **Pins restored to the build defaults** `tx=3 rx=10`, so the next `INIT` behaves exactly
  as an untouched board would (and will fail the same way until the jumper is moved — that
  is deliberate; a silent workaround would hide the real fault).
- **GPIO2 released to input**, so its external pull-up holds it high and the board boots
  normally. Verified by the final `PROBE`, which leaves both probed pads as plain inputs.
- Nothing was written to any other board. `/dev/cu.usbmodem31101` was the only port opened
  at any point in this session.
- No MQTT connection was made and nothing was published to any ComposeBus namespace.
