# Research assignment — ESP32-C3 TWAI acceptance filter: can it match on DATA BYTES for EXTENDED frames?

**Status:** NOT YET RUN. This document is the **protocol**; the framing and order of work
are in `docs/research/START-HERE-can-research-instance.md`, which you should have read
first. Results go in `docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md`.
**Audience:** an engineer (human or Intelligence) who has never seen this repository.
**Written:** 2026-08-10. **The instrument is bench-validated** — the firmware was built,
flashed and driven over serial on 2026-08-10, and three real defects were found and fixed
that way. **The experiments have not been run.**
**You have NO authority to change any arbitration-id layout.** You produce evidence.
David decides.

> ## The one thing to take from this document
>
> **E1 — the experiment that decides the question — needs ONE ESP32-C3 and ONE jumper
> wire, from GPIO3 to GPIO10.**
>
> No transceiver. No differential pair. No termination resistors. No breadboard. No second
> board. That is not a workaround for missing hardware; it is the *better* experiment,
> because it removes the entire physical layer as a possible explanation for what you
> measure (§3.1).
>
> **Do not build a two-node CAN bus before running E1.** The bus is needed only for E2
> (§8), which is the least confident design in the set. If you are about to spend an
> evening breadboarding, stop and read §3 first.

---

## 0. Read this first: the shape of the job, and three standing rules

You are going to answer one narrow hardware question with a falsifiable experiment,
and write down what you saw — including if what you saw was "inconclusive".

The question has a documented answer (§1.4). **Your job is not to confirm it.** Every
experiment below is built so that the documented answer and its negation produce
*different, specific, non-trivial* observations, and so that a wiring fault or a
misconfiguration produces a *third* observation distinguishable from both. If you find
yourself about to write "as expected" without a number next to it, stop.

### Standing rule 1 — NEVER flash a board without asking David which board it is

This is not boilerplate. **It has already gone wrong once on this assignment.** The
session that produced this document probed whatever ESP32s happened to be attached to
David's Mac, assumed they were the bench hardware, and flashed one of them. They were not
the bench hardware; they were connected for unrelated reasons. Full flash backups had been
taken first, so it is recoverable — see §0.1 — but the mistake was reaching for hardware
without asking.

`ls /dev/cu.*` tells you what is plugged in. It does **not** tell you what that board is
for, whose it is, or what is on it. Ask. Every time.

### Standing rule 2 — the inventory comes from David, not from probing

§0.2 is a conversation, not a command to run. **This document does not know what hardware
David has** and must not be read as if it does. §2.1 records what one machine looked like
on one afternoon; that is an anecdote, not an equipment list.

### Standing rule 3 — INCONCLUSIVE is a real result

Every experiment in §7 has a PASS / FAIL / **INCONCLUSIVE** rule, and the third one is
there because it will be needed. Report it when it happens. A confident answer the raw
counters do not support is the only way to fail this assignment.

---

## 0.1 STEP 0 — restore the board that was flashed in error

**Do this before anything else, and ask David first.**

An earlier automated run flashed `can_filter_bench` onto an ESP32-C3 that was attached to
David's Mac on 2026-08-10, having wrongly assumed it was bench hardware. It backed up both
attached boards' full 4 MB flash before doing so, so the original firmware is recoverable.

| | |
|---|---|
| Flashed (needs restoring) | **ESP32-C3**, MAC `00:00:5e:00:53:02`, seen at `/dev/cu.usbmodem31101` |
| Read only, never written | **ESP32-C6**, MAC `00:00:5e:00:53:05`, seen at `/dev/cu.usbmodem31201` |

Backups, 4.0 MB each, in the session scratch directory:

```
/private/tmp/claude-501/-Users-you-composeio/\
e1a82ba7-0c99-4f14-a7c9-4e7c40b1d0fc/scratchpad/flash_backup/board_00005e005302.bin
…/scratchpad/flash_backup/board_00005e005305_C6.bin
```

To restore (confirm the port maps to that MAC first — the `SER=` field in
`list_ports`, §4.2):

```bash
esptool.py --chip esp32c3 --port /dev/cu.usbmodemXXXX --no-stub \
  write_flash 0x0 .../flash_backup/board_00005e005302.bin
```

**ASK DAVID whether he wants it restored.** He may prefer to keep the bench firmware on it
if that board turns out to be a spare — in which case it is also the board you can run E1
on. Do not assume either way, and do not restore silently.

If the scratch directory has been cleaned and the backups are gone, say so plainly and ask
David what was on that board. Do not quietly move on.

---

## 0.2 STEP 1 — establish the hardware inventory WITH David

**Before planning anything, ask.** Write his answers into the results file (§0 of the
template) as the real inventory. Until then, treat every board-specific claim in this
document as unverified.

| Ask | Why it changes the plan |
|---|---|
| **How many ESP32 boards, and which variant?** (C3, C6, S3, classic ESP32…) | Only the **C3** is the chip under decision. **An ESP32-C6 cannot be used at all with this firmware** — see §2.1. If he has one C3, run Track A and you are done with the decision |
| **Are any of them already committed to something else?** | Standing rule 1. Flashing a board in use is what went wrong before |
| **How many SN65HVD230 breakouts?** | Zero is fine — **Track A needs none.** Two are needed only for E2 |
| **Do those breakouts already carry a 120 Ω resistor?** | If yes, adding more gives ~30 Ω and a bus that fails *intermittently* rather than cleanly (§5.1). This is the single most common way to build a subtly broken CAN bench |
| **Is anything wired up yet?** | As of 2026-08-10 **none of the CAN hardware was set up** — transceivers, termination and breadboard were still in the box. **Building the bench is work you do WITH David**, not a precondition you assume |
| **Does he have jumper wires?** | E1 needs exactly one |

Then tell him the thing he most needs to hear before he starts unpacking components:

> **E1 needs one C3 and one jumper wire. The transceivers can stay in the box unless you
> want E2.**

---

## 1. What we are trying to learn, and why anyone cares

### 1.1 The system, in one paragraph

This repository implements **ComposeBus**, a typed publish/subscribe message bus. A
message is `(namespace, type, value, scope, source)`. `namespace` is a string like
`office_lights`; `scope` is one of `local`, `global`, `remote`, `auto`; `type` is one of
ten message kinds (`state`, `event`, `heartbeat`, ...). The bus runs over several
transports — MQTT, UART, and **CAN**. Nothing about the bus semantics is CAN-specific;
CAN is one substrate among several.

### 1.2 Why CAN, and why the identifier layout matters so much

A classic CAN frame is an **11-bit or 29-bit arbitration identifier** plus **up to 8 data
bytes**. The identifier is not an address. It is a broadcast label that every node sees,
and — crucially — that every node's controller can **filter on in hardware, before the
CPU is ever interrupted**. That property is the entire reason CAN suits a pub/sub bus:
if a namespace hash lives *in the identifier*, then "subscribe to namespace X" becomes a
silicon-level acceptance filter, and uninterested nodes burn zero CPU.

So: **whatever you put in the identifier is hardware-filterable. Whatever you put in the
data bytes may not be.** That is the whole decision.

### 1.3 The conflict

Two shipped implementations in this tree pack the 29-bit identifier **differently** and
therefore cannot share a physical wire. This is recorded in
`spec/01-composeio-bus-protocol.smd` §6.3.4 as `[BP-CAN-ID-DIVERGENCE]`, "OPEN, recorded
2026-08-10, not blessed":

| # | Implementation | 29-bit layout | Where `scope` lives |
|---|---|---|---|
| **L1** "spec layout" | `services/can_mqtt_bridge/bridge.py`, matches spec `[BP-CAN-ID]` | `[ ns_hash 16 ][ type 4 ][ src_hash 9 ]`<br>bits 28..13 / 12..9 / 8..0 | **data byte 0, bits 7..6** (spec `[BP-CAN-DATA]`) |
| **L2** "transport layout" | `python/composeio/transport_can.py` (`CANTransport`) | `[ type 4 ][ scope 2 ][ ns_hash 16 ][ seq 7 ]`<br>bits 28..25 / 24..23 / 22..7 / 6..0 | **identifier bits 24..23** |

`CANTransport`'s own module docstring says why it diverged: *"It carries scope in the
identifier because it has nowhere else to put it."*

### 1.4 The question this bench settles

> **Can an ESP32-C3 TWAI acceptance filter match on DATA BYTES for EXTENDED (29-bit)
> frames, or only on the identifier?**

- **If it CAN**, then `scope` can live in data byte 0 (where the spec already puts it)
  and still be hardware-filterable, and there is no reason to spend identifier bits on
  it. **L1 (the spec layout) wins**, and `transport_can.py` is the thing that changes.
- **If it CANNOT**, then anything you need silicon to filter on must be in the
  identifier, and `CANTransport`'s stated reason for diverging is a real engineering
  constraint rather than an oversight. That materially strengthens **L2**.

**The documented answer** (ESP32-C3 Technical Reference Manual v1.4, §31.4.6.1, page 808)
is that single-filter mode can filter:

```
  SFF (11-bit):  the entire 11-bit ID, the RTR bit, data byte 1 and data byte 2
  EFF (29-bit):  the entire 29-bit ID, the RTR bit          <-- and nothing else
```

i.e. **no data bytes for extended frames**. Two independent corroborations: this is
SJA1000-derived silicon and the SJA1000 behaves the same way; and the ESP-IDF programming
guide says single filter mode allows "the first two data bytes of a standard frame to be
filtered, **or** the entirety of an extended frame's 29-bit ID".

**Do not treat that as the answer.** Documentation and silicon disagree often enough that
this bench exists. §7 is designed so a disagreement would be visible, not silently
absorbed.

### 1.5 The second question, which may matter more

§31.4.6.2 of the same TRM says that in **dual filter mode**, for extended frames, each of
the two filters can compare **only the first 16 bits of the 29-bit ID — that is,
ID[28:13]**. (Corroborated by an ESP-IDF maintainer in
[espressif/esp-idf#11897](https://github.com/espressif/esp-idf/issues/11897), who computes
a working dual-filter code as `id >> 13` placed in the top 16 bits.)

**First, the thing that is easy to get backwards, and that reframes the whole decision:**

> **Field position within the identifier does NOT matter in single filter mode.**
> Single-filter extended mode compares **all 29 bits**, and the mask is per-bit. So *any*
> field — namespace, type, scope, sequence — is exactly maskable **wherever it sits**,
> under **either** candidate layout. "Put it near the top so it's filterable" is not a
> real consideration. A single filter can express "accept exactly namespace X" or "accept
> exactly scope=global" identically well under L1 and under L2, provided the field is in
> the identifier at all.

**Position becomes decisive only in DUAL filter mode**, because dual mode buys you a
second, independent accept pattern at the price of comparing only ID[28:13]. So the real
question E1d answers is not "where should fields go" but:

> **Does a leaf node need ONE accept pattern, or TWO?**

- **One pattern** → use single filter mode → position is irrelevant → E1d does not
  constrain the layout choice at all, and the decision rests entirely on E1b.
- **Two or more patterns** (e.g. a node that subscribes two unrelated namespaces, or
  wants `state` on namespace X *and* `heartbeat` from anywhere) → dual mode → **only
  ID[28:13] is comparable**, and then position is everything:
  - Under **L1** `[ns_hash 16][type 4][src_hash 9]`, the namespace hash occupies
    **exactly** bits 28..13. It is perfectly aligned with the dual-filter window: "accept
    exactly these two namespaces" is expressible with no wasted bits and no false
    positives.
  - Under **L2** `[type 4][scope 2][ns_hash 16][seq 7]`, bits 28..13 are `type(4) +
    scope(2) + the top 10 bits of ns_hash`. A bank can only express "this type, this
    scope, and this 10-bit *prefix* of the namespace hash" — a prefix match on a hash,
    which admits **64 unrelated namespaces** alongside the one you wanted. Those extra
    frames reach the CPU and must be rejected in software, which is the cost hardware
    filtering exists to avoid.

So E1d does not settle the layout on its own. It settles **what it would cost L2 to
support a two-subscription leaf** — and that is a question about ComposeBus's expected
node topology, which is David's to answer, not the bench's. Record the hardware behaviour;
flag the topology question. **E1d is still likely the most valuable result of the set, but
for a narrower reason than "field position decides what is filterable".**

### 1.6 Things you do NOT need to know or decide

- You do not need to understand ComposeBus state engines, namespaces, or MQTT.
- You do not need to read the Python. If you want context, the two files are
  `python/composeio/transport_can.py` (see its module docstring, lines 23–60) and
  `services/can_mqtt_bridge/bridge.py` (`can_id_from_message` / `message_from_can`).
- **Do not modify either of them, or any spec file.** See §9.
- A `docs/architecture/DECISIONS-can-arbitration-id.md` was referenced when this
  assignment was commissioned. **It does not exist in the tree.** The authoritative
  framing is §1.3 above plus `spec/01-composeio-bus-protocol.smd` §1.5.1
  (`[BP-CAN-ID]`), §1.5.2 (`[BP-CAN-DATA]`) and §6.3.4 (`[BP-CAN-ID-DIVERGENCE]`).

---

## 2. Hardware facts

**Two different kinds of claim live in this section. Do not confuse them.**

| | |
|---|---|
| **§2.2 – §2.6 are VERIFIED FACTS** | checked against the primary datasheet, reference manual or vendor source, cited inline. Trust them; correct them if the silicon disagrees |
| **§2.1 is an OBSERVATION FROM ONE MACHINE** | what was attached to David's Mac on one afternoon. **It is not an inventory and must not be used as one.** §0.2 is how you get the inventory |

Two assumptions the commissioning brief carried turned out to be wrong; both are flagged
**CORRECTION** below.

### 2.1 An observation, NOT your inventory

> **This section does not tell you what hardware David has.** It records what happened
> when an earlier session probed whatever was on USB. Those boards were connected for
> unrelated reasons and **were not the bench hardware**. Get the real inventory from
> David (§0.2).

On 2026-08-10, two ESP32 boards were attached to David's Mac and were interrogated with
`esptool`. This is what they turned out to be:

| Port (as enumerated then) | Chip | Revision | MAC |
|---|---|---|---|
| `/dev/cu.usbmodem31101` | **ESP32-C3** | v0.4 | `00:00:5e:00:53:02` |
| `/dev/cu.usbmodem31201` | **ESP32-C6** | v0.1 | `00:00:5e:00:53:05` |

The observation is kept because **one part of it is a durable, reusable finding** rather
than an accident of that afternoon:

- **CORRECTION / durable finding — an ESP32-C6 cannot be used with this firmware.**
  `platform = espressif32@6.5.0` (Arduino core 2.0.14 / ESP-IDF 4.4.6) **cannot build
  Arduino firmware for the ESP32-C6 at all** — the C6 board definitions in that platform
  declare `"frameworks": ["espidf"]` only. Targeting a C6 would need the community
  `pioarduino` platform fork (Arduino 3.x / IDF 5.x): a different toolchain, out of scope
  here. **If David's actual inventory includes a C6, say so plainly and do not silently
  substitute it.** This is likely to recur — C3 and C6 boards look nearly identical and
  both enumerate as `USB JTAG/serial debug unit`, VID `0x303A` PID `0x1001`.
- The chip whose behaviour the decision depends on is the **C3**, because that is the MCU
  class ComposeIO targets for CAN nodes. A result from any other part does not settle the
  question.

The rest of that afternoon's inference — "therefore David has one C3 and one C6, therefore
Track B is blocked" — **was wrong**, and is retracted. Track A is the primary path on its
own merits (§3.1), not because a second board was missing.

Whatever David tells you in §0.2, verify the chip identity before flashing anything:

```bash
python3 -c "from serial.tools import list_ports; [print(p.device, p.hwid) for p in list_ports.comports()]"
~/.platformio/penv/bin/python -m esptool --chip esp32c3 --port /dev/cu.usbmodemXXXX flash_id
```

The `hwid` string carries the MAC in its `SER=` field, so you can map a port to a specific
physical board **without flashing anything**. Use that, then confirm with David that the
board on that port is the one he means you to use (standing rule 1).

**Before flashing any board whose contents might matter, back it up:**

```bash
esptool.py --chip esp32c3 --port PORT read_flash 0 0x400000 backup.bin
```

### 2.2 ESP32-C3 GPIO restrictions (ESP32-C3 Series Datasheet v2.4, §2.3.3–2.3.4, §3)

The commissioning brief's claim that "strapping pins are GPIO2, GPIO8, GPIO9" and
"GPIO18/19 are native USB D-/D+" is **CONFIRMED** (Datasheet §3, Table 3-1; §2.3.4). But
it is incomplete. The datasheet classifies every GPIO by priority:

| GPIOs | Datasheet priority | Why restricted |
|---|---|---|
| **0, 1, 3, 10** | **Priority 2 — "can be freely used without restrictions"** | — (0 and 1 double as `XTAL_32K_P/N` if a 32.768 kHz crystal is fitted; most C3 devkits do not fit one) |
| 2, 8, 9 | Priority 3 | **Strapping pins.** GPIO2 & GPIO8 float at reset, GPIO9 has a weak pull-up. GPIO2/8/9 select chip boot mode; GPIO8 also controls ROM message printing |
| 4, 5, 6, 7 | Priority 3 | JTAG interface (MTMS/MTDI/MTCK/MTDO) |
| 11 | Priority 3 | `VDD_SPI` — flash power supply pin by default |
| 12–17 | **Priority 4 — do not use** | SPI0/1 to the in-package flash |
| 18, 19 | Priority 3 | **USB Serial/JTAG D-/D+.** This is the USB-C port |
| 20, 21 | Priority 3 | UART0 RXD/TXD |

**There are exactly four unrestricted GPIOs on an ESP32-C3: 0, 1, 3, 10.**

### 2.3 Pin choice, and why

```
  TWAI TX  = GPIO3        TWAI RX = GPIO10        E2 trigger = GPIO1
```

Justification:

1. **All three are Priority 2** — the only unrestricted pins on the part. Nothing else on
   the C3 can be used without accepting a documented caveat.
2. **GPIO3 and GPIO10 avoid the XTAL_32K alternate function** that GPIO0/GPIO1 carry, so
   they work even on a board that fitted a 32 kHz crystal. GPIO1 is left for the trigger
   line, which is only used by E2 and is not timing-critical against a crystal.
3. **TWAI is routed through the GPIO matrix**, so the peripheral imposes no constraint of
   its own — TX must be output-capable (GPIO3 is), RX may be any input.
4. GPIO4–7 would also work electrically, but they are the JTAG pins; using them means an
   ESP-Prog debug session and the CAN bus fight over the same pads. Avoided on purpose.
5. **GPIO2/8/9 are excluded categorically.** A transceiver's `R` output driving a
   strapping pin during power-up can change the chip's boot mode — a fault that presents
   as "the board sometimes doesn't start", which is the worst kind on a bench.

The pins are `-D` build flags in `platformio.ini`, so changing them is a one-line edit if
your board breaks one of them out awkwardly.

### 2.4 SN65HVD230 transceiver (TI SLOS346O, Rev. April 2018)

Pin functions, verbatim from the datasheet's Pin Functions table:

| Pin | Name | Type | Description |
|---|---|---|---|
| 1 | **D** | I | CAN transmit data input (LOW = dominant, HIGH = recessive). Also called TXD |
| 2 | GND | — | Ground |
| 3 | VCC | — | **Transceiver 3.3 V supply voltage** |
| 4 | **R** | O | CAN receive data output (LOW = dominant, HIGH = recessive). Also called RXD |
| 5 | Vref | O | VCC / 2 reference output |
| 6 | CANL | I/O | Low-level CAN bus line |
| 7 | CANH | I/O | High-level CAN bus line |
| 8 | **RS** | I | Mode select: **strong pull down to GND = high speed mode**; strong pull up to VCC = low power mode; 10 kΩ to 100 kΩ pull down to GND = slope control mode |

**The brief's claim about Rs is CONFIRMED:** Rs must be at GND for high speed. Two
things the brief did not say, and that will bite you:

- **Rs pulled HIGH is not merely "slow" — it is STANDBY, and in standby the driver is
  switched off.** The datasheet: *"The SN65HVD230 enters a low current standby mode
  (listen only) during which the driver is switched off and the receiver remains
  active."* A floating Rs that drifts above 0.75·VCC gives you a node that **receives
  perfectly and can never transmit**. If one node mysteriously hears everything and sends
  nothing, check Rs before anything else.
- **Slope control is a continuum, and 100 kΩ is too slow for 500 kbit.** With Rs at 0 V
  the propagation delay is 35–120 ns; through 100 kΩ it is 500–1200 ns. A 500 kbit bit is
  2000 ns. Aim for Rs at 0 Ω. If your breakout fits a resistor, ≤10 kΩ is fine.
- **Bus termination**, from the datasheet's Application Information: 120 Ω characteristic
  impedance, *"Resistors equal to the characteristic impedance of the line should be used
  to terminate both ends of the cable"*. Two ends, two resistors, ~60 Ω measured across
  the pair.

**The D input's idle state.** The datasheet specifies both `IIH` (at VI = 2 V) and `IIL`
(at VI = 0.8 V) as up to **−30 µA** — current flowing *out of* the pin at both logic
levels, which is the signature of a weak internal pull-up to VCC (a floating D would then
be recessive, which is the safe direction). **TI does not state this in words**, and
breakout boards vary. Treat it as unverified: see §5.6 for the 10 kΩ insurance resistor
and the symptom if you skip it.

### 2.5 TWAI controller facts

- **The ESP32-C3 has exactly ONE TWAI controller** (C3 TRM v1.4, ch.31; ESP-IDF
  programming guide: *"The ESP32-C3 includes 1 TWAI controllers"*). **CONFIRMED.**
  (The ESP32-C6 has two — another reason not to silently substitute it.)
- **Acceptance mask semantics are inverted from intuition.** In the legacy
  `twai_filter_config_t` API this firmware uses, a mask bit of **1 means DON'T CARE**.
  `TWAI_FILTER_CONFIG_ACCEPT_ALL()` is `code = 0x00000000, mask = 0xFFFFFFFF`. A mask of
  `0x00000000` means *every* filtered bit must match exactly.
  **Beware:** the *new* `twai_mask_filter_config_t` API in ESP-IDF v5.2+ uses the
  OPPOSITE convention (1 = must match). Any StackOverflow answer or forum post you find
  may be about the other API. This firmware and this document are entirely on the legacy
  API.
- **ESP-IDF writes `acceptance_code` MSB-first into ACR0..ACR3.** `twai_ll_set_acc_filter`
  byte-swaps the 32-bit value before writing, so **bit 31 of `acceptance_code` is ACR0
  bit 7**. Every hex literal in §7 is derived from that, and the firmware's `CALC` command
  recomputes them on the silicon so you can cross-check this document's arithmetic.
- **A node that filters a message out still ACKNOWLEDGES it.** Acceptance filtering is a
  reception concern, not a bus-protocol concern. This is why a receiver must be in
  `normal` mode, not `listen-only` — see §5.5.
- **Silicon errata TWAI-3.13.5** (Espressif chip errata): if a bit or stuff error occurs
  in the data or CRC field of a received frame, *the next received data frame — including
  frames filtered out by the acceptance filter — should be considered invalid*. This is
  precisely why **any run with `bus_error > 0` is INCONCLUSIVE**, not "mostly fine".

### 2.6 The full 32-bit acceptance-filter bit map

Derived from the C3 TRM §31.4.6 plus the SJA1000 ACR0..ACR3 ordering. This is the table
every literal in §7 comes from. `acceptance_code` bit 31 is the MSB.

**Single filter mode, EXTENDED (EFF) frame:**
```
 bit  31                                     3   2    1 0
     [--------- ID.28 .. ID.0  (29 bits) ---------][RTR][ - ]
     code = id << 3            exact match mask = 0x00000007
```
**No data bytes appear anywhere in this layout.** That is the claim under test.

**Single filter mode, STANDARD (SFF) frame:**
```
 bit  31        21  20   19  16  15         8  7          0
     [ ID.10..ID.0 ][RTR][ unused ][ data byte 1 ][ data byte 2 ]
     code = (id << 21) | (rtr << 20) | (d0 << 8) | d1
     mask, data bytes only          = 0xFFFF0000
     mask, id + both data bytes     = 0x001F0000
     mask, id only                  = 0x001FFFFF
```

**Dual filter mode, EXTENDED (EFF) frame:**
```
 bit  31            16  15             0
     [ filter1: ID.28..ID.13 ][ filter2: ID.28..ID.13 ]
     A frame is accepted if EITHER filter matches.
     ID bits 12..0 and RTR are NOT COMPARED AT ALL.
```

**Dual filter mode, STANDARD (SFF) frame:**
```
 bit  31        21  20   19       16  15        5  4    3        0
     [f1 ID.10..0][RTR][f1 d1 hi nib][f2 ID.10..0][RTR][f1 d1 lo nib]
```
Note the trap that bit the reporter of esp-idf#11897: in dual mode a *standard* frame is
matched against the *standard* layout of **both** banks, so a second bank left at
`code = 0x0000` means "accept standard ID 0", not "accept anything".

---

## 3. Track A is the experiment. Track B is optional.

### 3.1 Track A — ONE ESP32-C3, ONE jumper wire. **This decides the question.**

**Bill of materials, in full:**

| Qty | Item |
|---|---|
| 1 | ESP32-C3 board |
| 1 | USB-C cable |
| 1 | jumper wire |

That is the entire bench.

```
        ESP32-C3
      ┌───────────┐
      │           │
      │  GPIO3 ●──┼───────┐   ONE JUMPER WIRE
      │  (TWAI TX)│       │
      │           │       │
      │  GPIO10 ●─┼───────┘
      │  (TWAI RX)│
      │           │
      │   USB-C ●─┼──────────► Mac
      └───────────┘
```

```bash
python3 firmware/can_filter_bench/tools/bench.py --loop /dev/cu.usbmodemXXXX --exp all
```

**Why this is not a shortcut — why it is the better experiment.** The TWAI controller
supports a **Self Reception Request**: a frame is transmitted and simultaneously received
by the same node. The SJA1000 family documentation is explicit about the part that makes
this decisive here:

> *"Upon self reception request a message is transmitted and simultaneously received
> **if the acceptance filter is set to the corresponding identifier**."*

The self-received frame travels **through the acceptance filter** — the exact mechanism
under test, on the exact silicon under test, on the real receive path. And Espressif's own
`twai_self_test` example sanctions the bare jumper: *"If you don't have an external
transceiver, this example can still be run by simply connecting the TX GPIO and RX GPIO
with a jumper."*

The acceptance filter is a property of the **receive path inside the controller**. It
cannot tell, and does not care, whether the bits arriving on its RX pin came from its own
transmitter through 3 cm of jumper wire or from a peer through a terminated differential
pair. **A two-node bus adds no information about the filter — it only adds ways to be
wrong.**

And it adds a lot of them. Every item in §5 — termination arithmetic, the Rs standby trap,
CANH/CANL continuity, common ground, the D-pin float, bitrate mismatch, listen-only
non-ACK, bus-off cascades — is a mechanism by which a two-node bus can silently produce
`accepted=0`, which is **indistinguishable from a working filter that rejected everything**
(§10 item 4). Track A deletes all of them by construction. If E1a–E1e give clean answers
here, the physical layer cannot be blamed for them, because there isn't one.

Mode: `INIT <bitrate> no-ack` (self-test mode — no ACK from another node is required) plus
`SELF ON`. `ROLE loop` sets both.

**What Track A cannot do.** It cannot run **E2** (arbitration under contention genuinely
requires two independent transmitters). It does not exercise a differential physical
layer. Neither of those bears on the question in §1.4. There is exactly one failure mode
that would invalidate Track A itself — self-reception bypassing the filter, contradicting
the quoted SJA1000 documentation — and **E1a is the gate that would catch it** (§7).

### 3.2 Track B — TWO boards, transceivers, a terminated bus. **Optional.**

**Do not build this before running E1.** Build it only if, after E1, David wants E2.

| Experiment | Needs Track B? |
|---|---|
| **E1a–E1e — the decision** | **No.** Track A is sufficient and preferable |
| E2 — arbitration under contention | **Yes.** Two independent transmitters, unavoidable |
| E3 — cross-layout garbage decode | **No hardware at all.** It is arithmetic (§8) |
| E4 — subscription expressiveness | **No.** It is `FILTER` + `SEND` rounds; runs fine on Track A. More convincing on a real bus if one already exists |

**Reconciliation with the kickoff file.** `START-HERE-can-research-instance.md` says in one
place that the bus is needed "only for E2 and E3" and in another "only build the bus if
David wants E2/E4". Neither grouping is quite right, and the difference does not matter
much because **the conclusion is the same in all three versions: run E1 first, on one
board.** The precise position, which this document is responsible for:

- **E2 genuinely requires two boards and the bus.** No way around it.
- **E3 requires no hardware whatsoever.** It is arithmetic (§8).
- **E4 runs on Track A.** It is `FILTER` + `SEND` rounds in loopback.

If you have no second C3, run E3 and E4 anyway and say so in the results.

Track B's other use is **confirmation**: repeating E1 on real wire guards against the
remote possibility that self-reception and peer-reception take different paths through the
filter. That is worth doing if the hardware is already built. It is not worth building the
hardware for.

**Requires two ESP32-C3 boards.** Not a C3 and a C6 — see §2.1. Confirm what David
actually has (§0.2) before planning this.

BOM:

| Qty | Item | Notes |
|---|---|---|
| 2 | ESP32-C3 dev board | must be C3, not C6 (§2.1) |
| 2 | SN65HVD230 breakout ("VP230" blue board is typical) | 3.3 V part. Do **not** substitute a 5 V TJA1050 without level shifting |
| 0–2 | 120 Ω resistor | **check first — most breakouts already fit one.** §5.1 |
| 1 | 10 kΩ resistor (optional but recommended ×2) | D-pin pull-up insurance, §5.6 |
| 1 | solderless breadboard + jumpers | |
| 2 | USB-C cable | both boards to the same Mac (this also commons their grounds) |

Wiring — **every** connection:

| From | To | Purpose |
|---|---|---|
| Board A GPIO3 | Transceiver A pin 1 (D / CTX / TXD) | TWAI TX |
| Board A GPIO10 | Transceiver A pin 4 (R / CRX / RXD) | TWAI RX |
| Board A 3V3 | Transceiver A pin 3 (VCC) | **3.3 V only** |
| Board A GND | Transceiver A pin 2 (GND) | |
| Transceiver A pin 8 (RS) | GND | high-speed mode — check §5.2 first |
| Board B GPIO3 | Transceiver B pin 1 (D) | |
| Board B GPIO10 | Transceiver B pin 4 (R) | |
| Board B 3V3 | Transceiver B pin 3 (VCC) | |
| Board B GND | Transceiver B pin 2 (GND) | |
| Transceiver B pin 8 (RS) | GND | |
| Transceiver A pin 7 (CANH) | Transceiver B pin 7 (CANH) | the bus |
| Transceiver A pin 6 (CANL) | Transceiver B pin 6 (CANL) | the bus |
| 120 Ω across CANH–CANL at end A | | **only if not already fitted** — §5.1 |
| 120 Ω across CANH–CANL at end B | | **only if not already fitted** — §5.1 |
| Board A GND rail | Board B GND rail | **explicit common ground.** Do not rely on the Mac |
| Board A GPIO1 | Board B GPIO1 | E2 trigger line only. Omit for E1 |
| 10 kΩ from Board A GPIO3 to Board A 3V3 | | optional, §5.6 |
| 10 kΩ from Board B GPIO3 to Board B 3V3 | | optional, §5.6 |

```
   ESP32-C3 "A"                                          ESP32-C3 "B"
  ┌────────────┐                                        ┌────────────┐
  │ GPIO3  ●───┼──D(1)┐                          ┌─D(1)─┼───●  GPIO3 │
  │ GPIO10 ●───┼──R(4)┤   SN65HVD230             │ R(4)─┼───● GPIO10 │
  │ 3V3    ●───┼─VCC(3)  transceiver A     transceiver B VCC(3)──● 3V3│
  │ GND    ●───┼─GND(2)│                          │GND(2)┼───●  GND   │
  │ GPIO1  ●───┼───────┼──────── trigger ─────────┼──────┼───●  GPIO1 │
  └────────────┘   RS(8)│                     RS(8)│     └────────────┘
        │               │                          │            │
        │            CANH(7)══════════════════════CANH(7)       │
        │            CANL(6)══════════════════════CANL(6)       │
        │               │                          │            │
        │            [120Ω]                     [120Ω]          │
        │          (only if not                (only if not     │
        │           already on                  already on      │
        │           the board)                  the board)      │
        │               │                          │            │
        └───────────────┴──── COMMON GROUND ───────┴────────────┘
             RS(8) of each transceiver tied to GND  (high-speed mode)
```

---

## 4. Flashing

### 4.1 Build

```bash
cd ${COMPOSEIO_HOME}/firmware/can_filter_bench
pio run -e c3_usbcdc
```

`c3_usbcdc` is the default env and is correct for a board whose USB-C goes straight to the
chip (GPIO18/19 — the built-in USB Serial/JTAG). Use `-e c3_uart` only if your board has a
separate USB-to-UART bridge chip. **You can tell which you have before flashing:**

| macOS device name | Meaning | Env |
|---|---|---|
| `/dev/cu.usbmodem*` | native USB Serial/JTAG (VID `0x303A`, PID `0x1001`) | `c3_usbcdc` |
| `/dev/cu.usbserial-*`, `/dev/cu.SLAB_USBtoUART` | CP210x bridge | `c3_uart` |
| `/dev/cu.wchusbserial*` | CH340 bridge | `c3_uart` |

The two ESP32s observed on 2026-08-10 (§2.1) both enumerated as `/dev/cu.usbmodem*`,
`USB JTAG/serial debug unit`, VID `0x303a` PID `0x1001` — i.e. `c3_usbcdc`. **Check
whatever board you are actually given rather than assuming that carries over.** Note that
a C3 and a C6 enumerate identically, so this string does **not** identify the chip; use
`flash_id` for that (§2.1).

### 4.2 Identify the ports, then upload

```bash
python3 -c "from serial.tools import list_ports; [print(p.device, p.hwid) for p in list_ports.comports()]"
```

The `SER=` field is the board's MAC. Write down which MAC is on which port; that is how
you tell the boards apart for the rest of the session, and it is how you check you are
about to write to the board you think you are.

> **STOP. Before the next command:** confirm with David that the board on this port is
> one he wants flashed, and back it up if its contents might matter (§9). This is the
> step that went wrong last time.

Then, **one board at a time**:

```bash
pio run -e c3_usbcdc -t upload --upload-port /dev/cu.usbmodemXXXX
pio run -e c3_usbcdc -t upload --upload-port /dev/cu.usbmodemYYYY   # Track B only
```

`platformio.ini` pins `upload_speed = 115200` and `upload_flags = --no-stub`. **Both are
load-bearing and were added because the default failed on this exact bench.** Without
them you get, intermittently:

```
A fatal error occurred: No serial data received.
A fatal error occurred: Unable to verify flash chip connection (No serial data received.).
```

The C3's USB Serial/JTAG has no real UART baud rate, so esptool's mid-session
renegotiation to 460800 can fail, and its RAM stub loader can drop the link right after
`Stub running...`. If an upload still fails: unplug/replug, or hold the BOOT button
(GPIO9) while pressing RESET to force ROM download mode, then upload.

### 4.3 Confirm the build stamp matches (Track B)

Open a serial monitor per board and type `ID`:

```bash
pio device monitor -p /dev/cu.usbmodemXXXX -b 115200
```

```
ID fw=can_filter_bench ver=1.0.0 stamp=a0669cb3 built=Aug_10_2026_15:15:24
ID chip=esp32c3 rev=4 cores=1 mac=00005e005302 idf=4.4.6
ID tx_gpio=3 rx_gpio=10 trig_gpio=1 role=idle cdc=1
END ID
```

- **`stamp=` MUST be byte-identical on both boards.** It is a CRC-32 of the firmware
  name, version and compile timestamp. If the two differ, the boards are running
  different builds and no cross-board result is comparable. Rebuild once, upload twice,
  do not touch the source in between.
- `chip=esp32c3` — if either board reports something else, stop and read §2.1.
- **On native USB-CDC the boot banner is normally missed**, because the device boots
  before the host opens the port. That is not a fault; type `ID`.

---

## 5. Pre-flight checks — TRACK B ONLY. Do these BEFORE powering anything.

> **If you are running Track A, skip this entire section. Go to §6.**
> Track A needs none of it, and that is precisely its point: everything below is a way a
> two-node bus can be subtly wrong, and Track A has no two-node bus.

**Building this bench is work you do WITH David, at the bench, together** — not a
precondition you assume is already satisfied. As of 2026-08-10 none of the CAN hardware
had been wired: the transceivers, resistors and breadboard were still in the box. Confirm
what he has (§0.2) before assuming any of the components below exist.

The `§5.7` rehearsal at the end is worth doing **even on Track A** — it teaches you both
failure signatures with no hardware, and one of them (RX stuck dominant) is exactly what
you will see on Track A if the jumper is missing or in the wrong holes.

### 5.1 The 120 Ω check — the one most likely to bite

**Many SN65HVD230 breakouts ship with a 120 Ω resistor already fitted between CANH and
CANL.** Two such boards on a 2-node bus already give the correct ~60 Ω. Adding two more
external resistors gives ~30 Ω, and a 30 Ω bus does not fail cleanly — it fails
*intermittently*, which is the worst possible failure for an experiment about counting
frames.

**With everything unpowered and disconnected**, measure resistance across CANH–CANL:

| Measurement | Configuration | Meaning | Action |
|---|---|---|---|
| ~120 Ω | one breakout, alone | resistor is fitted | add **no** external resistors |
| open / MΩ | one breakout, alone | no resistor fitted | you must add 120 Ω at each end |
| ~60 Ω | both breakouts wired together | **correct** | proceed |
| ~40 Ω | both wired | three 120 Ω in parallel | remove one |
| ~30 Ω | both wired | four in parallel | remove two |
| ~120 Ω | both wired | only one termination present | add one at the other end |
| 0 Ω | anything | **CANH is shorted to CANL** | find it before powering up |

Some breakouts put the 120 Ω behind a solder jumper or a removable jumper block — look at
the board as well as measuring it.

### 5.2 The Rs pin check

Unpowered, measure resistance from transceiver pin 8 (RS) to GND:

| Reading | Meaning | Action |
|---|---|---|
| ~0 Ω | hard-wired to GND — **high-speed mode** | correct, do nothing |
| 10 kΩ – 100 kΩ | slope control | works; ≤10 kΩ is comfortable at 500 kbit, 100 kΩ is marginal (§2.4) |
| open / MΩ | RS is floating | **wire it to GND.** A floating RS can drift into standby, where the driver is off and the node can receive but never transmit |

Many breakouts already tie RS to GND or through a resistor. Confirm; do not assume.

### 5.3 Continuity

Unpowered:
- CANH on transceiver A ↔ CANH on transceiver B: **continuous**.
- CANL A ↔ CANL B: **continuous**.
- CANH ↔ CANL: **NOT continuous** apart from the termination resistance measured in §5.1.
- GND rail A ↔ GND rail B: **continuous**. Both boards being on the same Mac commons the
  grounds through the USB shields, but that path is long and shared with data; run an
  explicit wire.
- Each board's 3V3 ↔ its transceiver VCC: continuous, and **not** shorted to GND.

### 5.4 Powered, bus idle

Power both boards, do **not** `INIT` anything yet. With a multimeter referenced to GND:

| Measurement | Healthy | Unhealthy means |
|---|---|---|
| CANH to GND | ~2.3–2.6 V | 0 V: transceiver unpowered, or bus held dominant |
| CANL to GND | ~2.3–2.6 V | as above |
| CANH − CANL | **< 0.5 V** (recessive idle) | ~2 V steady: **someone is driving the bus dominant** — usually a board in reset whose TX pin floats low into its transceiver's D input (§5.6) |
| Transceiver Vref (pin 5) to GND | ~VCC/2 ≈ 1.65 V | transceiver not powered or damaged |

### 5.5 Mode choice — and the listen-only trap

Both boards must run `INIT <bitrate> normal`.

A receiver in **`listen-only` does not transmit acknowledgements**. On a 2-node bus that
means the transmitter never gets an ACK, so with retries enabled it retransmits forever
and marches to error-passive and then bus-off — and you will read that as "the filter
rejected everything" when in fact nothing was ever successfully sent.

A receiver in `normal` **acknowledges frames it filters out**, which is exactly what you
want: acceptance filtering is a reception concern, not a bus concern.

The firmware defaults to **single-shot transmission** (`SS ON`): a frame that fails is not
retried, so a broken bus increments `tx_failed` instead of jamming. Leave it on. `SS OFF`
exists for E2.

### 5.6 The D-pin float, and the 10 kΩ insurance

Before the TWAI driver installs (i.e. during boot, and any time a board is held in
reset), GPIO3 is an input with no pull. If the transceiver's D input is also unpulled, it
floats. **D low = dominant = the entire bus is jammed** for as long as that board is in
reset.

The SN65HVD230 datasheet's input-current numbers are consistent with a weak internal
pull-up on D (§2.4), which would make a floating D recessive and harmless — but TI does
not say so in words. A **10 kΩ resistor from GPIO3 to 3V3 on each board** removes the
question for the price of one component.

**Symptom if you skip it and the assumption is wrong:** resetting or reflashing one board
makes the *other* board report a flood of bus errors and go error-passive/bus-off. §5.4's
"CANH − CANL ≈ 2 V steady" check catches it.

### 5.7 Rehearse both failure signatures — with no hardware at all

Do this once before you trust any real number. The firmware can force its own RX pin's
internal pull, which reproduces both "the wire is wrong" shapes on the bench in about
thirty seconds:

```
python3 firmware/can_filter_bench/tools/bench.py --loop /dev/cu.usbmodemXXXX --exp wirecheck
```

Both signatures were captured on this bench on 2026-08-10 and look like this:

**Fault A — RX stuck DOMINANT** (`RXPULL down`; in the real world: no jumper fitted,
transceiver unpowered, CANH shorted to CANL, TX wired but RX not):

```
STAT tx ok=1 fail=0 queue_err=0
STAT rx accepted=0 tally_overflow=0
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=1 msgs_to_rx=0 tx_failed=0
     rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
```

**Total silence. Every error counter is zero.** The controller never sees bus-idle, so it
never starts transmitting and the frame sits in the TX queue. **This is the dangerous
one** — it is indistinguishable from "the filter rejected everything" *unless you look at
`msgs_to_tx`*. `msgs_to_tx > 0` after everything has settled means **nothing reached the
wire and no RX count on this run means anything.**

**Fault B — RX recessive / floating** (`RXPULL up`; in the real world: broken RX path,
transceiver in standby with Rs high, wrong bitrate against a live bus):

```
STAT hw state=RUNNING tec=0 rec=130 msgs_to_tx=1 msgs_to_rx=0
     bus_error=193151   <-- ~48,000 per second
STAT alert ... above_warn=1 bus_error=192311 ...
STAT alert2 lines_suppressed=192279
EVT ALERT flags=0x00000200 t=9687          (0x200 = TWAI_ALERT_BUS_ERROR)
EVT ALERT_THROTTLED note=further_alerts_this_second_suppressed_see_STATS
```

**A bus-error storm, `rec` climbing while `tec` stays 0, and the alert throttle engaging.**
The firmware rate-limits alert lines to 8/second precisely so this fault does not saturate
the USB-CDC link and make the REPL look hung; the suppressed count is reported so nothing
is hidden.

Run `DEINIT`, then `RXPULL none`, before doing anything real.

---

## 6. The serial protocol

115200 baud, line-oriented, LF or CRLF terminated, commands case-insensitive. Every
output line begins with a stable prefix and carries `key=value` tokens; **no value ever
contains a space**, so a naive whitespace split is a valid parser.

| Prefix | Meaning |
|---|---|
| `#` | human commentary / echo / help. Ignore when parsing |
| `OK <CMD> ...` | command accepted; tokens describe what was applied |
| `ERR <CMD> reason=<token>` | command rejected; **nothing changed** |
| `EVT <KIND> ...` | asynchronous event: `RX`, `ALERT`, `ALERT_THROTTLED`, `TRIGGERED` |
| `STAT <GROUP> ...` | one line of a `STATS` report |
| `ID ...` | identity / build stamp |
| `END <CMD>` | end of a multi-line response |

### Commands

| Command | Effect |
|---|---|
| `HELP` | the command list, as `#` lines |
| `ID` | chip, revision, MAC, firmware build stamp, pin assignment |
| `ROLE tx\|rx\|loop\|idle` | label **plus defaults**. `loop` = Track A (`SELF ON`, drain on, print on). A role never hides anything: `STATS` always reports the real flags |
| `INIT <kbps> <normal\|listen-only\|no-ack>` | install + start TWAI. `kbps` ∈ 25, 50, 100, 125, 250, 500, 800, 1000. **Zeroes every counter, hardware and software** |
| `DEINIT` | stop + uninstall |
| `FILTER <code_hex> <mask_hex> <single\|dual>` | set the acceptance filter and re-init if running. **Mask bit 1 = DON'T CARE.** Zeroes counters |
| `CALC ext <id>` / `CALC std <id> <d0> <d1>` / `CALC dual <id1> <id2>` | print the code/mask this document claims, computed **on the chip**. Use it to cross-check §7's literals |
| `SEND ext\|std <id_hex> <data_hex\|->` | transmit one frame. `-` = zero-length |
| `BURST ext\|std <id_hex> <data_hex\|-> <count> <interval_ms>` | transmit `count` frames |
| `RX ON\|OFF` | print `EVT RX` lines. **Counting always happens regardless** |
| `DRAIN ON\|OFF` | whether `twai_receive()` is called at all — E1e needs `OFF` |
| `RXQ <n>` | rx queue length, applied at next `INIT` — E1e needs 4 |
| `SS ON\|OFF` | single-shot TX. Default **ON** |
| `SELF ON\|OFF` | self-reception request. Track A needs `ON` + `no-ack` + the jumper |
| `RXPULL up\|down\|none` | force the RX pin's internal pull — reproduces both wiring faults (§5.7) |
| `STATS` | full counter report, ends with `END STATS` |
| `CLEAR` | zero **software** counters only. Hardware counters need a re-`INIT` |
| `RECOVER` | `twai_initiate_recovery()` after bus-off |
| `TRIG master\|slave\|off`, `ARM ...`, `FIRE` | E2 contention primitives (§8) |
| `ECHO ON\|OFF` | echo command lines back as `# >` comments. Default ON |

### The `STATS` report

```
STAT cfg role=loop installed=1 bitrate_kbps=500 mode=no-ack single_shot=1 self=1 drain=1 rx_print=1
STAT filter code=0x00000000 mask=0xffffffff mode=single rx_queue_len=16 rxpull=none
STAT tx ok=6 fail=0 queue_err=0
STAT rx accepted=6 tally_overflow=0
STAT rxid ext=1 id=0x12345678 n=1                      <-- one line per distinct id seen
STAT hw state=RUNNING tec=0 rec=0 msgs_to_tx=0 msgs_to_rx=0 tx_failed=0 rx_missed=0 rx_overrun=0 arb_lost=0 bus_error=0
STAT alert tx_success=6 rx_data=6 ... bus_error=0 tx_failed=0 rx_queue_full=0 err_pass=0 bus_off=0 ...
STAT alert2 lines_suppressed=0
STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts
END STATS
```

**Read that last note carefully.** `twai_read_alerts()` latches and clears, so N frames
arriving between two polls set `RX_DATA` **once**. The `STAT alert` numbers are "how many
polls saw this flag" — a *lower bound* on events, useful for shape, useless for counting.
**For exact numbers use `STAT hw`**: `rx_missed`, `bus_error`, `arb_lost`, `tx_failed`,
`msgs_to_rx`, `msgs_to_tx` are hardware/driver counters and are exact.

### The three fields to check before believing any result

1. `STAT hw state=RUNNING` — not `BUS_OFF`, not `STOPPED`.
2. `STAT hw ... bus_error=0` — anything else invalidates the run (errata TWAI-3.13.5, §2.5).
3. `STAT hw ... msgs_to_tx=0` on the **transmitting** board — otherwise frames never left
   the queue and every RX number is meaningless (§5.7 fault A).

---

## 7. E1 — the deliverable. Full protocol.

Run them in the order given: the controls come first for a reason.

You may drive this by hand in a serial monitor, or run the scripted driver, which sends
**exactly these commands** and prints every line verbatim:

```bash
# Track A
python3 firmware/can_filter_bench/tools/bench.py --loop /dev/cu.usbmodemXXXX --exp all | tee /tmp/e1.log
# Track B
python3 firmware/can_filter_bench/tools/bench.py --a /dev/cu.usbmodemXXXX --b /dev/cu.usbmodemYYYY --exp all | tee /tmp/e1.log
```

The driver prints observed vs predicted and flags the validity gate, but **it deliberately
does not print a verdict**. That call is yours, from the raw transcript, using the
decision rules below.

**Conventions below.** "TX" is the transmitting board; on Track A that is the same board.
Track A: `ROLE loop` then `INIT 500 no-ack`. Track B: `ROLE tx` / `ROLE rx` then
`INIT 500 normal` on both. Every experiment starts by applying a filter, which re-inits
and zeroes all counters.

### E1-CONTROL — every frame we will use must be receivable

**Purpose:** if a frame cannot get through an accept-all filter, its absence in a later
experiment says nothing about filtering.

```
RX:  FILTER 0x00000000 0xFFFFFFFF single
RX:  INIT 500 <mode>
TX:  SEND ext 0x12345678 a5
TX:  SEND ext 0x12345679 a5
TX:  SEND ext 0x024694a0 a5
TX:  SEND ext 0x024694a0 5a
TX:  SEND ext 0x02468b40 a5
TX:  SEND ext 0x02468b40 5a
RX:  STATS
```

| | |
|---|---|
| **Predicted** | `rx accepted=6`; `rxid` shows `0x12345678`×1, `0x12345679`×1, `0x024694a0`×2, `0x02468b40`×2 |
| **PASS** | accepted = 6, `bus_error=0`, `msgs_to_tx=0` |
| **FAIL** | accepted < 6 with `bus_error=0` and `msgs_to_tx=0` — frames are being lost for a reason we do not understand. **Stop. Do not run E1a–E1e.** |
| **INCONCLUSIVE** | `bus_error>0` or `msgs_to_tx>0` — fix the wiring (§5) and repeat |

### E1a — POSITIVE CONTROL: extended, single filter, exact 29-bit identifier

**Purpose:** prove the filter is configured, live, and discriminating at all. **Without
E1a passing, a null result in E1b is uninterpretable.**

Filter: accept exactly id `0x12345678`.
`code = 0x12345678 << 3 = 0x91A2B3C0`, `mask = 0x00000007` (low 3 bits are RTR + unused).
Cross-check with `CALC ext 0x12345678`.

```
RX:  FILTER 0x91A2B3C0 0x00000007 single
RX:  INIT 500 <mode>
TX:  SEND ext 0x12345678 a5      <- target
TX:  SEND ext 0x12345679 a5      <- differs in ID bit 0
TX:  SEND ext 0x02345678 a5      <- differs in ID bit 28
TX:  SEND ext 0x12345670 a5      <- differs in ID bits 3..0
RX:  STATS
```

| | |
|---|---|
| **Predicted** | `accepted=1`, `rxid` shows only `id=0x12345678 n=1` |
| **PASS** | exactly that |
| **FAIL** | `accepted=4` → the filter is not being applied at all (check `STAT filter` echoes the code/mask you set, and that `INIT` ran *after* `FILTER`). `accepted=0` → the filter is rejecting everything, or nothing was transmitted (check `msgs_to_tx`) |
| **INCONCLUSIVE** | E1-CONTROL did not pass; or `bus_error>0`; or `msgs_to_tx>0` |
| **If E1a fails on Track A specifically** | this is the one failure mode that would invalidate Track A itself: it would mean self-reception **bypasses** the acceptance filter, contradicting the SJA1000 documentation quoted in §3.1. Record it, and fall back to Track B. Do not proceed with Track A. |

### E1b — THE QUESTION: extended, single filter, code/mask at a DATA-BYTE position

**Purpose:** the actual decision.

`code = 0x0000A500`, `mask = 0xFFFF00FF`. Only code bits 15..8 are compared; everything
else is don't-care.

- **Under a hypothetical data-byte interpretation** (the SFF layout, §2.6): bits 15..8 are
  "data byte 1", so this filter would mean *"accept any frame whose `data[0] == 0xA5`"*.
- **Under the documented ID-only interpretation** (the EFF layout, §2.6): bits 15..8 are
  ID bits 12..5, so it means *"accept any frame whose `ID[12:5] == 0xA5`"*.

Two id sets, chosen so the two hypotheses split the frames differently:

| Frame | id | `ID[12:5]` | `data[0]` |
|---|---|---|---|
| A1 | `0x024694a0` | `0xA5` | `0xA5` |
| A2 | `0x024694a0` | `0xA5` | `0x5A` |
| B1 | `0x02468b40` | `0x5A` | `0xA5` |
| B2 | `0x02468b40` | `0x5A` | `0x5A` |

```
RX:  FILTER 0x0000A500 0xFFFF00FF single
RX:  INIT 500 <mode>
TX:  SEND ext 0x024694a0 a5
TX:  SEND ext 0x024694a0 5a
TX:  SEND ext 0x02468b40 a5
TX:  SEND ext 0x02468b40 5a
RX:  STATS
```

| Hypothesis | A accepted | B accepted | total |
|---|---|---|---|
| **H0 — identifier only** (documented) | **2 / 2** | **0 / 2** | 2 |
| **H1 — data bytes are filtered for EFF** | **1 / 2** (A1 only) | **1 / 2** (B1 only) | 2 |

**Both hypotheses predict a total of 2.** They differ only in *which* frames arrive. That
is the point: a stuck-accept, a stuck-reject, a dead bus and a mis-set mask all produce
totals of 4, 0, 0 and 0/4 respectively — **none of them can imitate either hypothesis**.
This experiment cannot be passed by accident.

| | |
|---|---|
| **PASS (H0 — the id is the only filterable field)** | `rxid` shows `id=0x024694a0 n=2` and **no** `0x02468b40` line. `accepted=2` |
| **PASS (H1 — data bytes ARE filterable for extended frames)** | `rxid` shows `id=0x024694a0 n=1` **and** `id=0x02468b40 n=1`. `accepted=2`. **This would overturn the documentation — verify by re-running and by inspecting the `EVT RX` `data=` fields to confirm the accepted frames are the `a5` ones** |
| **FAIL / INCONCLUSIVE** | any total other than 2; or E1a did not pass; or `bus_error>0`; or `msgs_to_tx>0`. **Report inconclusive. Do not round it to whichever hypothesis is closer.** |

### E1c — DISAMBIGUATOR: STANDARD frame, single filter, data bytes 1–2

**Purpose:** E1b's null result is only meaningful if data-byte filtering works *at all*.
If it does for standard frames and not for extended ones, that is a real capability
boundary. If it fails for both, we configured something wrong and **E1b proves nothing**.

`code = (0x123 << 21) | (0xA5 << 8) | 0x5A = 0x2460A55A`, `mask = 0xFFFF0000` — identifier
and RTR fully don't-care, **both data bytes compared**. Cross-check with
`CALC std 0x123 0xa5 0x5a`.

```
RX:  FILTER 0x2460A55A 0xFFFF0000 single
RX:  INIT 500 <mode>
TX:  SEND std 0x123 a55a       <- matches both data bytes
TX:  SEND std 0x123 a55b       <- data byte 2 differs
TX:  SEND std 0x123 5a5a       <- data byte 1 differs
TX:  SEND std 0x456 a55a       <- DIFFERENT ID, matching data
TX:  SEND std 0x7ff a55a       <- DIFFERENT ID, matching data
RX:  STATS
```

| | |
|---|---|
| **Predicted** | `accepted=3` — `0x123`, `0x456` and `0x7ff`, all carrying `a55a` |
| **PASS** | exactly that. **`0x456` and `0x7ff` being accepted is the load-bearing observation**: the identifier is entirely masked out, so the only thing that let them through is the data-byte comparison |
| **FAIL** | `accepted=5` → data bytes are not being compared (mask wrong?). `accepted=1` (only `0x123`) → the identifier is still being compared despite the mask, meaning our understanding of the SFF bit layout is wrong and **E1b's mask placement is suspect too** |
| **INCONCLUSIVE** | `accepted=0`, or `bus_error>0`, or `msgs_to_tx>0`. **An E1c null makes E1b unusable — say so explicitly in the results** |

### E1d — dual filter mode, extended: what does a SECOND accept pattern cost?

**Purpose:** determine whether dual-filter extended mode really compares only ID[28:13].

**Read this before interpreting the result — it is easy to get backwards.**

**Field position within the identifier is irrelevant in SINGLE filter mode.** Single-filter
extended compares all 29 bits with a per-bit mask, so any field is exactly maskable
*wherever it sits*, under **either** candidate layout. A single filter expresses "accept
exactly namespace X" or "accept exactly scope=global" equally well under L1 and L2. If
every ComposeBus leaf needs only **one** accept pattern, **E1d constrains the layout choice
not at all**, and the decision rests entirely on E1b.

Position becomes decisive only in **dual** mode, which buys a second independent accept
pattern at the price of comparing only the top 16 identifier bits. So the question this
experiment actually answers is:

> **Does a leaf node need ONE accept pattern, or TWO?**

If two — a node subscribing two unrelated namespaces, or wanting `state` on namespace X
*and* `heartbeat` from anywhere — then under **L1** the namespace hash sits exactly at
ID[28:13] and both patterns are exact; under **L2** a bank can only match a **10-bit prefix**
of the namespace hash, admitting **64 unrelated namespaces** that must then be rejected in
software. See §1.5 for the full derivation.

**Record the hardware behaviour. Flag the topology question — "do our leaves need one
pattern or two?" is David's to answer, not the bench's.**

Part 1 — `code = 0x1234FFFF`, `mask = 0x00000000`, **dual**. Filter 1 requires
`ID[28:13] == 0x1234`; filter 2 requires `0xFFFF`, which we never send.
Cross-check with `CALC dual 0x02468000 0x1fffffff`.

```
RX:  FILTER 0x1234FFFF 0x00000000 dual
RX:  INIT 500 <mode>
TX:  SEND ext 0x02468000 a5     <- ID[28:13]=0x1234, low bits 0x0000
TX:  SEND ext 0x02469fff a5     <- ID[28:13]=0x1234, low bits 0x1FFF  (differs ONLY in ID[12:0])
TX:  SEND ext 0x0246a000 a5     <- ID[28:13]=0x1235
RX:  STATS
```

| | |
|---|---|
| **Predicted** | `accepted=2` — `0x02468000` and `0x02469fff`. `0x0246a000` rejected |
| **PASS (only ID[28:13] compared)** | exactly that. **`0x02469fff` being accepted is the whole result** — it differs from the target in all 13 low ID bits and gets through anyway |
| **FAIL (more than 16 bits compared)** | `accepted=1` (only `0x02468000`) → dual-filter extended compares more than ID[28:13]. This contradicts the TRM and **would remove E1d's argument for L1** |
| **INCONCLUSIVE** | `accepted=3` (nothing filtered) or `accepted=0`; or `bus_error>0`; or `msgs_to_tx>0` |

Part 2 — the same three frames with the banks swapped: `code = 0xFFFF1234`, `mask = 0`.

| | |
|---|---|
| **Predicted** | identical to part 1: `accepted=2` |
| **Why it is here** | if part 2 differs from part 1, **filter bank 2 is not live** and part 1 was a single-bank result wearing a dual-bank costume. Exactly this trap is reported in [esp-idf#11897](https://github.com/espressif/esp-idf/issues/11897) |
| **INCONCLUSIVE** | part 1 ≠ part 2 → report both, conclude nothing about dual-mode capacity |

### E1e — are rejected frames dropped in silicon, or just dropped later?

**Purpose:** filtering that still wakes the CPU buys nothing. This is the point of the
whole feature.

Method: set the RX queue to 4 and **never drain it**. `rx_missed_count` is incremented
**in the RX interrupt handler** when a frame arrives and the queue is full. So
`rx_missed > 0` proves the CPU was interrupted; `rx_missed == 0` together with
`msgs_to_rx == 0` proves the frame was discarded in hardware before any interrupt existed.
(There is no software filtering anywhere in the ESP-IDF legacy TWAI driver, so this is a
clean measurement.)

```
RX:  RXQ 4
RX:  DRAIN OFF
RX:  RX OFF

# E1e-1  positive control: accept everything
RX:  FILTER 0x00000000 0xFFFFFFFF single
RX:  INIT 500 <mode>
TX:  BURST ext 0x12345678 a5 20 5
RX:  STATS

# E1e-2  reject everything we send
RX:  FILTER 0x91A2B3C0 0x00000007 single     (accepts only 0x12345678)
RX:  INIT 500 <mode>
TX:  BURST ext 0x0fedcba8 a5 20 5            (we send a different id)
RX:  STATS

RX:  DRAIN ON
RX:  RX ON
RX:  RXQ 16
```

| | |
|---|---|
| **Predicted E1e-1** | `msgs_to_rx=4` (queue full) and `rx_missed=16` (20 sent − 4 queued) |
| **Predicted E1e-2** | `msgs_to_rx=0` **and** `rx_missed=0` |
| **PASS (hardware rejection — filtering is real)** | both predictions hold |
| **FAIL (rejection is not free)** | E1e-2 shows `rx_missed > 0` → rejected frames *are* reaching the ISR, and hardware filtering saves you nothing but queue space |
| **INCONCLUSIVE** | E1e-1 does not show `msgs_to_rx=4, rx_missed=16` — the positive control failed, so E1e-2's zeros mean nothing. Also inconclusive on any `bus_error>0` or `tx_failed>0` (on Track B check the burst actually completed: `OK BURST ... queued=20`) |

---

## 8. E2, E3, E4 — designed here, implement only if E1 goes smoothly

**None of these is needed to answer §1.4.** Do not let them delay or complicate E1. What
each actually costs:

| | Hardware needed | Nature | Confidence in the design |
|---|---|---|---|
| **E2** | **two ESP32-C3 boards + the full Track B bus** | measurement | **lowest in the set** — see the timing caveat below |
| **E3** | **none at all** | arithmetic, not measurement | n/a — the outcome is determined before you start |
| **E4** | none beyond Track A | mostly derivation, verified by a few `FILTER`+`SEND` rounds | good |

So: **E2 is the only reason to build a bus.** Discuss that with David explicitly before he
unpacks anything — it is the difference between an evening of breadboarding and none.

The firmware already carries the primitives for E2. E3 and E4 need no new firmware.

### E2 — arbitration under contention

**Question:** when two nodes transmit simultaneously with identifiers differing only in
the field under test, which wins, and does the loser recover cleanly? CAN arbitration is
bitwise from ID bit 28 downward, dominant (0) beats recessive (1) — so **the layout
determines which ComposeBus concept gets bus priority.** Under L1 the *namespace hash*
occupies the top bits, so priority is effectively random per namespace. Under L2 the
*message type* occupies bits 28..25, so `state` (code 0) would always beat `log_entry`
(code 9). That is a real, arguably desirable, difference worth measuring.

**Track B only** — it needs two independent transmitters.

**Wiring:** board A GPIO1 ↔ board B GPIO1 (already in the §3.2 table). No resistor needed;
the slave configures an internal pull-down.

**Why a plain trigger is not enough, and what the firmware does instead.** If node A simply
calls `twai_transmit()` and node B does the same a few microseconds later, B's controller
sees the bus already busy and *defers* — you measure scheduling latency, not arbitration.
Genuine arbitration requires both nodes to have a frame **pending while the bus is busy**,
so they both start at the same next SOF. `FIRE` therefore:

1. queues a **starter frame** (id `0x1FFFFF00`, DLC 8 — about 130 µs of bus time at
   500 kbit),
2. raises the trigger line, which the slave polls in its main loop and answers by queueing
   **its** contender,
3. queues A's contender.

Both contenders are pending during the starter frame and arbitrate at the following bus
idle. The 130 µs starter absorbs the slave's polling jitter.

```
B:  TRIG slave
B:  SS OFF                      # the loser must be allowed to retransmit
B:  ARM ext <idB> aa
A:  TRIG master
A:  SS OFF
A:  ARM ext <idA> bb
A:  FIRE
A:  STATS      B:  STATS
```

**Expected:** the numerically lower identifier wins. The winner shows `arb_lost=0`; the
loser shows `arb_lost=1` and — with `SS OFF` — retransmits successfully immediately after,
so **both** frames appear on the bus, winner first. Read the order from the `EVT RX`
timestamps on the receiving side.

**Suggested id pairs**, using the two candidate layouts with the same namespace hash and
differing only in the contested field:
- L1 contest on `type`: same `ns_hash`, `type` 0 vs 9 → `(ns<<13)|(0<<9)|src` vs
  `(ns<<13)|(9<<9)|src`.
- L2 contest on `scope`: same `type` and `ns_hash`, `scope` `local` (0) vs `global` (1).

**INCONCLUSIVE if:** `arb_lost` is 0 on both boards (they never actually contended — the
starter frame did not overlap, so raise the starter DLC or lower the bitrate), or either
board reports `bus_error > 0`.

### E3 — cross-layout decode. **Arithmetic, not measurement. No hardware.**

**Question:** show that a node encoding with L2 and a node decoding with L1 produce a
*garbage namespace*, rather than merely asserting it.

**This is not an experiment and cannot fail.** Field extraction is deterministic: the
answer is fully determined by the two layouts before you touch any hardware, and putting
the frame on a wire adds exactly nothing to the demonstration. It is listed here as a
*demonstration artefact* — something to paste into a decision record — not as evidence.
**Do it in Python in five minutes, or skip it and say you skipped it.** Do not build a bus
for it.

Pick a namespace, compute its CRC-16/CCITT hash with the function in
`spec/01-composeio-bus-protocol.smd` §1.5.3 (`[BP-CAN-HASH]`), build the identifier both
ways, and decode the L2-encoded identifier with L1's field extraction (`ns = id >> 13`,
`type = (id >> 9) & 0xF`, `src = id & 0x1FF`). Record the namespace hash L1 recovers and
confirm it maps to nothing. If you have a board wired for something else anyway, you may
`SEND ext` the id and decode what is captured — but that is decoration, not evidence.

### E4 — expressiveness: which subscriptions fit in one filter bank?

**Question:** for each candidate layout, can "accept only `scope == global`" and "accept
only `namespace == X`" be expressed in a single acceptance filter?

This is the analysis that turns E1b and E1d into a recommendation. **It runs on Track A —
it is only `FILTER` + `SEND` rounds and needs no bus** (§3.2). It is mostly derivation,
verified by a handful of those rounds.

Note before filling the table: rows 1 and 3 are **single-filter** rows, where field
position is irrelevant (§1.5) — both layouts should come out equally capable, and a
difference there means one of the two is not putting the field in the identifier at all.
The **dual-mode row is where the layouts actually diverge.**

| Subscription | Under **L1** `[ns16][type4][src9]` | Under **L2** `[type4][scope2][ns16][seq7]` |
|---|---|---|
| `namespace == X` | single filter, `code = (ns<<13)<<3`, `mask = 0x1FFF<<3 \| 7` — **exact, one bank** | single filter, `code = ns<<7<<3`, `mask` covers type+scope+seq — **exact, one bank** |
| `namespace == X` in **dual** mode | `ID[28:13]` **is** `ns_hash` — **exact** | `ID[28:13]` is type+scope+ns[15:6] — a **10-bit prefix** of the hash: accepts 64 unrelated namespaces |
| `scope == global` | scope is in **data byte 0**; filterable **only if E1b returns H1** | `code = 0x1<<23<<3`, mask elsewhere — **exact, one bank** |
| `scope == global` AND `namespace == X` | needs id + data byte together — impossible for EFF under H0 | **exact, one bank** |

Fill the right-hand columns from your measured E1b/E1d results, then verify two or three
rows on the bench with real `FILTER` + `SEND` rounds. **Record which rows you verified and
which you only derived.**

---

## 9. Safety and scope

### The hardware boundary — the one that has already been crossed once

> **Do not flash, erase or reconfigure ANY board without asking David which board it is
> and getting an answer.**

Not "any board that looks important" — **any board**. `ls /dev/cu.*` tells you what is
attached; it does not tell you whose it is, what is on it, or whether it is the bench
hardware. On 2026-08-10 an automated run probed two attached ESP32s, assumed they were the
bench, and flashed one. They were connected for unrelated reasons. Backups had been taken
first (§0.1), so it was recoverable — but the failure was reaching for hardware without
asking, and no amount of care afterwards substitutes for the question beforehand.

Corollaries:
- **Back up before writing.** `esptool.py --chip <chip> --port PORT read_flash 0 0x400000
  backup.bin`, and tell David where you put it.
- **Confirm the port maps to the board you mean** via the MAC in `list_ports`' `SER=`
  field (§4.2) before every write. Ports renumber.
- **Do not infer the inventory from what is plugged in.** §0.2.
- Reading (`flash_id`, `read_flash`, opening a serial port) is safe and needs no
  permission. **Writing does.**

### The bench is isolated. It touches nothing else.

- **Do not connect to any vehicle, appliance, or existing CAN network.** This is a
  standalone 1-node (or 2-node) bench bus.
- **Do not connect to the ComposeBus MQTT broker** (`mqtt-broker.local:1883`).
  Nothing in this assignment needs a network. The firmware has no WiFi code.
- **Do not publish to any ComposeBus namespace**, over any transport.
- **Do not run the Composer GUI or any ComposeIO app.** They are unrelated to this task.

**You may create or modify exactly these paths:**
- `firmware/can_filter_bench/**` — if you need a firmware change to complete an
  experiment. Record any change you make in the results file, including *why*.
- `docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md` — your output.

**You must NOT modify:**
- `python/composeio/transport_can.py`, `services/can_mqtt_bridge/**`, or **any** file
  under `spec/` or `corpora/`. **You have no authority to change the arbitration-id
  layout.** You produce evidence; David decides.
- `.claude/**`, any agent definition, or any repository configuration.

**Physical safety:** 3.3 V logic, tens of milliamps. The only real hazards are wiring 5 V
to a 3.3 V transceiver (don't), shorting 3V3 to GND (§5.3 catches it), and destroying
firmware you meant to keep (§2.1 — back up first).

**Report honestly.** If you could not run Track B because there is no second C3, say that
plainly rather than inferring the result. If a run was inconclusive, record it as
inconclusive with its raw counters. A recorded "we don't know" is worth more than a
confident wrong answer, because the next person can see exactly where it broke.

**And record what this document got wrong.** It was written by an instance that made a
hardware assumption and then built a whole section on it. If you find another — a claim
here that does not survive contact with David's actual bench — put it in the results file
under "anything in the assignment doc you found to be wrong". That is a deliverable, not a
complaint.

---

## 10. What would make each result WRONG

Read this before writing conclusions. Every item here has produced a plausible-looking
wrong answer on someone's bench.

| # | Failure | How it fakes a result | How you detect it |
|---|---|---|---|
| 1 | **Unterminated or wrongly terminated bus** (§5.1) | reflections cause sporadic frame loss → looks like a filter that *mostly* works, i.e. exactly the "partial filtering" signature that would be the most exciting possible finding | ~60 Ω across CANH/CANL; `bus_error=0`; E1-CONTROL delivering **all six** frames |
| 2 | **Bitrate mismatch between nodes** | receiver decodes nothing → looks like "the filter rejected everything" | both boards' `OK INIT` echo the same `bitrate_kbps`; receiver `bus_error` storms rather than sitting silent |
| 3 | **Receiver in `listen-only`** (§5.5) | never ACKs → transmitter retries to bus-off → zero frames received, read as perfect filtering | `STAT cfg mode=` on the receiver; transmitter `tx_failed>0` or `state=BUS_OFF` |
| 4 | **A filter that rejects everything looks like a filter that works** | `accepted=0` is consistent with "correct rejection", "mask wrong", "nothing transmitted", and "bus dead" | **this is why every experiment has a positive control.** E1a must pass before E1b counts; E1e-1 before E1e-2; E1-CONTROL before anything. And E1b is designed so the right answer is a *distribution*, never a zero |
| 5 | **Frames never left the TX queue** (§5.7 fault A) | total silence with **all error counters at zero** — the most convincing-looking null you will ever see | `STAT hw msgs_to_tx` must be 0 on the transmitter after settling. The scripted driver gates on this |
| 6 | **Silicon errata TWAI-3.13.5** (§2.5) | after a bit/stuff error, the *next* frame's data is invalid — including filtered-out frames — so a noisy bus silently corrupts counts | any `bus_error > 0` ⇒ **INCONCLUSIVE**, full stop |
| 7 | **Mask polarity inverted** | `mask` 1 = don't care in the legacy API, 1 = must-match in the v5.2+ API. Copying a modern snippet gives you an accept-all or reject-all filter that *looks* configured | `STAT filter` echoes what you set; `CALC` recomputes literals on-chip; E1a discriminates |
| 8 | **`FILTER` set but never applied** | the filter is only pushed to hardware at driver install | `FILTER` re-inits automatically when running and reports `reinit=1`; if it reports `reinit=0`, run `INIT` |
| 9 | **Counters not zeroed between experiments** | E1b's tallies contaminated by E1a's frames | `INIT`/`FILTER`-reinit zero everything and say `counters=zeroed`. Never interpret a `STATS` you did not preceded by a fresh filter application |
| 10 | **Two different builds on the two boards** | subtly different pin maps or behaviour | `stamp=` must be identical on both (§4.3) |
| 11 | **`ID[12:5]` coincidence in E1b** | if you change the ids, you can accidentally pick ones where H0 and H1 predict the same distribution and the experiment stops discriminating | use the ids as given. If you must change them, recompute `ID[12:5]` for both sets and confirm the two predicted distributions still differ |
| 12 | **Alert counts mistaken for frame counts** | `STAT alert rx_data=3` after 20 frames looks like massive loss | it is a poll count, not a frame count (§6). Use `STAT hw` and `STAT rxid` |
| 13 | **Self-reception bypassing the filter (Track A only)** | every frame accepted regardless of filter → looks like "filtering does not work" | E1a is the gate. If E1a fails on Track A but the config is right, suspect this and move to Track B |
| 14 | **Transceiver in standby, Rs floating high** (§2.4) | node receives fine, transmits nothing — looks like the *other* node's filter rejecting | §5.2 Rs check; transmitter `msgs_to_tx>0` with `bus_error=0` |
| 15 | **One board resetting mid-run jams the bus** (§5.6) | intermittent frame loss during flashing/monitor reconnects | do not reflash or reset one board while measuring on the other; §5.4 CANH−CANL check |

---

## 11. Deliverable

Fill in `docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md`. It already contains the
template. Requirements:

1. **The real hardware inventory**, as David gave it to you in §0.2 — boards and variants,
   transceivers, whether the breakouts carry their own 120 Ω. This replaces §2.1, which is
   an anecdote.
2. **Whether the §0.1 board was restored**, and what David decided.
3. **Raw counters, not just conclusions.** Paste the actual `STAT` lines. If someone
   disagrees with your reading, they must be able to re-derive it from what you recorded.
4. **Which track you ran**, and if Track B was skipped, why — "we didn't need it, E1 ran on
   one board and a jumper" is a perfectly good answer and the expected one.
5. **PASS / FAIL / INCONCLUSIVE per experiment**, using the decision rules in §7. An
   inconclusive result is a legitimate deliverable. Do not force a binary.
6. **Anything that surprised you**, including things that worked but not the way this
   document says they would, and **anything in this document you found to be wrong**.
7. **Any firmware change you made**, and why.
8. **No recommendation about the arbitration-id layout.** State what the hardware does.
   That is the whole job. For E1d specifically, that includes flagging the *topology*
   question it exposes ("do our leaves need one accept pattern or two?") rather than
   answering it.

---

## Sources

All claims in §2 were checked against these. Where this document disagrees with a forum
post, prefer this document; where it disagrees with these, prefer these — and record the
disagreement.

- **ESP32-C3 Technical Reference Manual v1.4**, ch.31 "Two-wire Automotive Interface
  (TWAI)", §31.4.6 Acceptance Filter, §31.4.6.1 Single Filter Mode (p.808), §31.4.6.2 Dual
  Filter Mode (p.808–809).
  <https://www.espressif.com/sites/default/files/documentation/esp32-c3_technical_reference_manual_en.pdf>
- **ESP32-C3 Series Datasheet v2.4**, §2.3.3 Restrictions for GPIOs, §2.3.4 Peripheral Pin
  Assignment, §3 Boot Configurations / Table 3-1 Default Configuration of Strapping Pins,
  Table 2-1 Pin Overview. <https://documentation.espressif.com/esp32-c3_datasheet_en.pdf>
- **ESP-IDF Programming Guide — TWAI (legacy driver)**, Acceptance Filter, Driver Modes,
  Alerts. <https://docs.espressif.com/projects/esp-idf/en/v5.1.2/esp32c3/api-reference/peripherals/twai.html>
- **ESP-IDF source**, `components/hal/esp32c3/include/hal/twai_ll.h` —
  `twai_ll_set_acc_filter` (the `HAL_SWAP32` that fixes ACR byte order).
- **ESP-IDF example** `examples/peripherals/twai/twai_self_test/README.md` — the TX-to-RX
  jumper loopback.
- **TI SN65HVD230/231/232 datasheet**, SLOS346O (rev. April 2018) — Pin Functions table,
  §"RS pin ... three different modes of operation", §11.2.1.1 CAN Termination.
  <https://www.ti.com/lit/ds/symlink/sn65hvd230.pdf>
- **NXP SJA1000 / AN97076** — PeliCAN acceptance filter, and self-reception request:
  *"upon self reception request a message is transmitted and simultaneously received if
  the acceptance filter is set to the corresponding identifier."*
- **Espressif chip errata** — TWAI-3.13.5, "Receiving an Erroneous Data Frame Can Cause the
  Data Bytes of the Next Received Data Frame to Be Invalid".
  <https://docs.espressif.com/projects/esp-chip-errata/en/latest/esp32/03-errata-description/esp32/twai-invalid-received-data-frame.html>
- **espressif/esp-idf#11897** — dual-filter extended mode computed as `id >> 13` in the top
  16 bits; and the filter-bank-2 trap. <https://github.com/espressif/esp-idf/issues/11897>
- **In-tree**: `spec/01-composeio-bus-protocol.smd` §1.5.1 `[BP-CAN-ID]`, §1.5.2
  `[BP-CAN-DATA]`, §1.5.3 `[BP-CAN-HASH]`, §6.3.4 `[BP-CAN-ID-DIVERGENCE]`;
  `python/composeio/transport_can.py`; `services/can_mqtt_bridge/bridge.py`.
