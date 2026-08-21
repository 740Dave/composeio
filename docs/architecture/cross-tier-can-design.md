# Cross-Tier CAN Architecture — Design

**Author:** composeio-expert
**Date:** 2026-08-04
**Status:** Design / scoping. Opinionated. Decision already made — this documents the *how*.
**Extends:** `[BP-MSG-CAN]` / `[BP-CAN-ID]` (bus-protocol CAN encoding, `spec/01-composeio-bus-protocol.smd`),
`[DD-BPR-MOCK-CAN]` (the mock-CAN substrate), the carousel Fiat tile
(`compose_projects/carousel/README.md`), `[CXC-EMBED-REACTIVE]` (the CYD reactive tier).
**Retires:** the `python-can` LGPL-3.0 caution in `docs/oss-readiness/mit-readiness-assessment.md` §2b / checklist P1-5.
**Audience:** David (architect) — ratify the contract + phasing.

> **The decision, restated.** Drop `python-can` (LGPL-3.0). Go **native per tier**: stdlib
> **SocketCAN** on Linux/RPi, ESP-IDF **TWAI** (Apache-2.0) on ESP32/CYD. There is **no shared
> CAN library** — there *can't* be, because neither `python-can` nor SocketCAN exists on the
> ESP32, whose CAN controller is on-chip silicon (TWAI) with its own driver. So **the tiers do
> not meet at a library — they meet at the bus.** The unifier is a ComposeBus namespace contract,
> and both readers are thin producers into it.

---

## 0. The one sentence that governs this

*A CAN frame is a transport detail; the load-bearing artifact is the **decoded signal on
ComposeBus**, and every tier's job is identical — pull raw frames off its native controller,
run a per-vehicle **decoder**, and publish retained signal state to a tier-agnostic namespace so
that no consumer can tell whether the bytes came off an RPi's MCP2515 or an ESP32's on-chip
TWAI.*

Everything below defends that sentence. The narrow waist is the namespace contract (§1); the
two readers (§2, §3) are interchangeable producers behind it; the license win (§4) falls out of
choosing native controllers over a wrapper; the plan (§5) is contract-first.

---

## 1. The bus contract — the load-bearing unifier

This is the whole design. Get this right and the two readers are mechanical.

### 1.0 First, disambiguate the two CANs (this bites if you skip it)

There are **two distinct CAN concerns** in this codebase and they share only the wire, not the
meaning:

| | **ComposeBus-over-CAN** (`[BP-MSG-CAN]`) | **Foreign-vehicle CAN ingestion** (this doc's focus) |
|---|---|---|
| What CAN is | a **transport for ComposeBus itself** | a **foreign data source** we read and translate |
| Who owns the arbitration IDs | ComposeIO — `id = namespace_hash(16)\|type(4)\|source(9)` | the vehicle OEM (FCA/Fiat) — opaque to us |
| The decode | reverse the namespace hash → a ComposeBus namespace | a per-vehicle **DBC-like map** frame_id → signals |
| Current code | `services/can_mqtt_bridge/bridge.py` (`message_from_can` / `can_id_from_message`) | **does not exist yet** — this is the Fiat gap |

`bridge.py` today is a **ComposeBus-over-CAN transport bridge** (concept 1): it assumes every
frame's arbitration ID encodes a namespace hash. The Fiat 500e (concept 2) emits IDs defined by
Fiat, meaning nothing to ComposeIO until a decoder gives them meaning. **Both ride the exact same
stdlib SocketCAN raw-frame reader** — the difference is purely which codec runs on the bytes. That
shared primitive is why one migration (§2) serves both. Keep the concepts separate in your head;
unify them at the reader.

### 1.1 Two namespace layers: raw frame, and decoded signal

Both readers publish the **same two-layer shape**, and consumers subscribe whichever layer they
need — tier-agnostic, by reference (the carousel Fiat tile already subscribes `fiat/soc` without
knowing a bridge exists).

**Layer 1 — raw frame (diagnostic / capture / bring-up).** One namespace per `(bus, frame_id)`:

```
can/<bus>/<frame_id_hex>        engine: json
# e.g.  can/can0/0x3d5   -> {"id": 981, "eff": false, "dlc": 8,
#                            "data": "0a1b00ff0022ee01", "t": 1723200000.123}
```

- `<bus>` is the logical bus name (`can0`, `fiat`, `hvac`) — lets one host carry several buses.
- `<frame_id_hex>` is the arbitration ID, lower-cased hex, `0x` prefixed. 11-bit and 29-bit IDs
  share the space; the **`eff` flag** (extended-frame format) disambiguates `0x123` (standard)
  from a 29-bit `0x00000123` — publish both `id` and `eff`, never infer width from magnitude.
- Value is **`json` engine, always.** A frame is a structured record (id + eff + dlc + up to 8
  data bytes as a hex string). This is non-negotiable — see the SIGABRT hazard in §1.4.
- This layer is **opt-in and firehose-y** (a vehicle bus is hundreds of frames/sec). Default it
  **off**; enable per-bus for a `candump`-style capture or decoder bring-up. It is *not* what a
  dashboard subscribes.

**Layer 2 — decoded signal (the product).** One namespace per physical quantity, vehicle-scoped:

```
fiat/soc        engine: continuous_0_255  (or json for a float %)  -> 74
fiat/speed      engine: json (float kph)                           -> 48.5
fiat/range      engine: json (int km)                              -> 180
fiat/gear       engine: enum [P,R,N,D]                             -> "D"
```

- These are the namespaces the carousel Fiat tile **already** subscribes
  (`compose_projects/carousel/README.md`, `[CXC-PRIM-CAROUSEL-BIND]`). This doc's decoder is what
  finally *publishes* them; until now they rest at 0.
- **Retained / current-value semantics** (the MQTT "retained message ≈ current state" instinct):
  a decoder publishes on-change, and a late-subscribing dashboard immediately sees the last known
  SOC. This is state, not event.
- Pick the engine per signal's real domain. A percentage that is genuinely 0–100 can be `json`
  (float) or a purpose engine; do **not** cram a float into `continuous_0_255` if it loses
  precision — `json` is the safe default for any continuous physical value.

### 1.2 The frame convention (the wire truth both readers must honor)

CAN 2.0B: a frame is `{ arbitration_id, eff_flag, dlc (0..8), data[0..8] }`. Both tiers already
agree on this because the physics is the same:

- **11-bit** standard ID (`eff=false`) or **29-bit** extended ID (`eff=true`). SocketCAN carries
  the EFF bit as `0x80000000` in the 32-bit `can_id` field; TWAI carries it as
  `twai_message_t.extd`. Both readers normalize to the `{id, eff}` pair before publishing.
- **DLC ≤ 8** classic data bytes. **No CAN-FD, no ISO-TP, no multi-frame reassembly** in scope —
  `bridge.py` never used them and the Fiat OBD-II PIDs are single-frame. (The mock-CAN substrate
  *does* model fragmentation for the critical-ack Slice-B path; that is a separate ComposeBus-over-CAN
  concern, not foreign ingestion. Do not conflate.)
- **RTR / error frames**: drop them at the reader. A decoder wants data frames only.

### 1.3 The decoder — a small declarative map, not a code plugin

This is the "what would SDR do?" call: **push the vehicle specificity out of the reader firmware
and into configuration.** The same SocketCAN reader and the same TWAI reader become a Fiat
decoder, a Tesla decoder, or an HVAC decoder purely by loading a different map. No per-vehicle
`if frame_id == 0x3d5` branches in the reader — ever.

The decoder is a **DBC-subset declarative map** (a `.yaml` or a `doc://decoders/<vehicle>` bus
document), keyed by frame id, each carrying one or more signal extractions:

```yaml
# decoders/fiat_500e.yaml   (the artifact OQ-1's candump populates — see §5)
bus: fiat
signals:
  - frame_id: 0x3d5          # <-- UNKNOWN until the candump; placeholder
    eff: false
    publish: fiat/soc
    start_bit: 8             # big-endian bit offset into the 8-byte payload
    length: 8                # bits
    scale: 0.4               # raw * scale + offset -> engineering value
    offset: 0.0
    engine: json
  - frame_id: 0x11a
    publish: fiat/gear
    start_bit: 0
    length: 4
    map: { 0: P, 1: R, 2: N, 3: D }   # enum decode instead of scale/offset
    engine: enum
```

- **A subset of DBC on purpose.** DBC (CANdb) is the industry standard for exactly this
  (frame → signal via start-bit/length/scale/offset/byte-order). We adopt its *semantics* but a
  YAML/bus-document surface — no `.dbc` parser dependency, editable by a human or an Intelligence
  as a peer bus artifact (config-over-code). If a real `.dbc` shows up later, a one-file importer
  lowers it into this map; the runtime never learns DBC.
- **The decoder is pure and tier-shared *in spec*, not in code.** The map is identical bytes on
  both tiers; the *evaluator* is reimplemented per tier (Python on Linux, C++ on ESP32) because
  the languages differ. Test the Python evaluator against the map; the C++ evaluator crosschecks
  against the same golden decode (the CYD `*_crosscheck.py` pattern). One contract, two
  evaluators — the same discipline the render engine uses.
- **Byte order matters and is a classic bug source.** Big-endian (Motorola) vs little-endian
  (Intel) bit numbering is per-signal in DBC; carry it explicitly (`byte_order: big|little`,
  default big for OBD-II). Guessing corrupts every multi-byte signal silently.

### 1.4 Why the raw layer is `json` and never `binary` (the SIGABRT trap)

A frame id, a hex data string, a gear selection — these are **free-form structured values**. Put
them through a strict engine (`binary`, `tri_level`) and the first out-of-domain value (a `""`
during init, an unmapped enum index) raises `ValueError` inside a callback → on macOS an
`NSException` → **`SIGABRT`, whole-process abort.** The rule from the hazards list applies
directly here: **raw frames and any string-y decoded signal use the `json` engine.** Only a
decoded signal with a *proven* strict domain (a real binary contactor state) may use a strict
engine, and only after the decoder guarantees the domain. When unsure, `json`.

### 1.5 Loop guard (don't re-ingest your own writes)

Every published message carries `source` (the reader's id). A bidirectional bridge (`bridge.py`
also does MQTT→CAN) **must not** transmit a frame it just received — `bridge.py` already guards
this (`if bus_msg.source != "can"`). The foreign-ingestion path is **read-only** (you do not
transmit onto a moving vehicle's bus), which sidesteps the loop entirely for the Fiat use case.
Keep foreign decoders strictly RX; reserve TX for the ComposeBus-over-CAN transport role.

---

## 2. The Linux / RPi reader — stdlib SocketCAN (retires `python-can`)

**SocketCAN is in the Linux kernel and the Python stdlib already speaks it.** `python-can`'s
`interface="socketcan"` backend is a thin convenience wrapper over exactly the socket calls
below. `bridge.py` uses *only* `can.Bus(interface="socketcan")` + `can.Message` for raw frames —
no notifiers, no CAN-FD, no ISO-TP — so the wrapper earns nothing but an LGPL obligation. Cut it.

### 2.1 The whole dependency, replaced by ~20 lines of stdlib

```python
import socket, struct

# SocketCAN classic frame: <can_id:u32><dlc:u8><pad×3><data:8s>  = 16 bytes
CAN_FRAME_FMT = "=IB3x8s"
CAN_EFF_FLAG  = 0x80000000
CAN_RTR_FLAG  = 0x40000000
CAN_ERR_FLAG  = 0x20000000
CAN_EFF_MASK  = 0x1FFFFFFF
CAN_SFF_MASK  = 0x000007FF

def open_can(channel: str = "can0") -> socket.socket:
    s = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
    s.bind((channel,))
    return s

def read_frame(s: socket.socket):
    frame = s.recv(16)
    can_id, dlc, data = struct.unpack(CAN_FRAME_FMT, frame)
    if can_id & (CAN_RTR_FLAG | CAN_ERR_FLAG):
        return None                       # drop RTR/error frames
    eff = bool(can_id & CAN_EFF_FLAG)
    arb = can_id & (CAN_EFF_MASK if eff else CAN_SFF_MASK)
    return arb, eff, data[:dlc]
```

- `socket.AF_CAN` / `SOCK_RAW` / `CAN_RAW` / `PF_CAN` are all in Python's stdlib `socket` on
  Linux. **Zero third-party imports.** MIT-clean.
- **Kernel-side acceptance filters** (optional, cheap): `s.setsockopt(socket.SOL_CAN_RAW,
  socket.CAN_RAW_FILTER, struct.pack("=II", frame_id, mask) * n)` — the kernel drops
  non-matching frames before they cross into userspace. On a busy vehicle bus this is the
  difference between decoding four PIDs and drowning in the firehose. `bridge.py` didn't filter;
  a Fiat decoder should filter to exactly the frame ids in its decoder map.
- **Blocking `recv` in a thread** (as `bridge.py` polls today) or a `select`/non-blocking loop —
  either works; keep `bridge.py`'s existing threaded poll shape to minimize the diff.
- **`send`** (for the ComposeBus-over-CAN TX path): `s.send(struct.pack(CAN_FRAME_FMT, arb |
  (CAN_EFF_FLAG if eff else 0), len(data), data.ljust(8, b"\x00")))`. Symmetric, still stdlib.

### 2.2 The non-Linux dev/test backend — `mockcan.py`, not `vcan`

`python/composeio/peer/mockcan.py` **already exists** as the deliberate, zero-dependency,
in-memory virtual CAN — chosen over Linux `vcan` (Linux+kernel-module, off the table on macOS
CI) and over `python-can`'s virtual bus (adds the dep we're removing). It is the correct dev/test
substrate for the SocketCAN reader: run the reader against `MockCanBus` on macOS, against real
`AF_CAN` on the Pi. The reader abstracts one seam — "give me the next `(arb, eff, data)`" — with a
SocketCAN implementation and a mockcan implementation behind it. `[DD-BPR-MOCK-CAN]` already
sanctions this split; we're extending its reach from the critical-ack harness to the foreign-frame
reader.

### 2.3 Migrating `services/can_mqtt_bridge/bridge.py`

Mechanical and low-risk. Today (`bridge.py:124-127, 182-188`):

```python
import can
self._can_bus = can.Bus(interface="socketcan", channel=CAN_INTERFACE, bitrate=CAN_BITRATE)
...
can_msg = self._can_bus.recv(timeout=0.1)      # -> can_msg.arbitration_id, .data, .is_extended_id
...
self._can_bus.send(can.Message(arbitration_id=arb_id, data=data, is_extended_id=True))
```

After — swap the `can.Bus` for the §2.1 socket, keep every line of the `message_from_can` /
`can_id_from_message` codec (that's ComposeBus-over-CAN logic, untouched):

- `can.Bus(...)` → `open_can(CAN_INTERFACE)`. **Note:** SocketCAN does **not** set bitrate from
  userspace — the bitrate is configured on the interface (`ip link set can0 type can bitrate
  500000`), which the carousel deploy already does via `can0.service` (systemd). So `CAN_BITRATE`
  becomes a comment / a systemd concern, not a socket arg. This matches the MCP2515 reality
  (`dtoverlay=mcp2515-can0`, oscillator 16 MHz, `can0.service`).
- `self._can_bus.recv(timeout=0.1)` → a `select([s], [], [], 0.1)` + `read_frame(s)`; map the
  returned `(arb, eff, data)` into the existing `message_from_can(arb, data)` (guard `eff` where
  the old code checked `can_msg.is_extended_id`).
- `.send(can.Message(...))` → the §2.1 `s.send(...)` pack.
- **Graceful degrade preserved.** `bridge.py` already runs "MQTT-only" if CAN setup fails; keep
  that — an `OSError` binding `AF_CAN` on a host without `can0` logs a warning and continues,
  exactly as the carousel Fiat backend degrades when `can0` is absent.

Delete `python-can` from `services/can_mqtt_bridge` requirements. That single deletion **clears
MIT-readiness checklist item P1-5** and the §2b LGPL caution.

---

## 3. The ESP32 / CYD reader — ESP-IDF TWAI (Apache-2.0)

The ESP32 has an **on-chip CAN controller**, historically "CAN" but renamed **TWAI** (Two-Wire
Automotive Interface) for trademark reasons. It is driven by ESP-IDF's `driver/twai.h`
(**Apache-2.0**). Neither `python-can` nor SocketCAN is relevant here — this is bare silicon +
C++, and it lands in the existing `cyd/` firmware alongside `bus_mqtt` / `reactive_core`.

### 3.1 What TWAI needs (and the one piece that isn't on-chip)

- **An external transceiver is mandatory.** The TWAI controller outputs *logic-level* TX/RX
  (3.3V); it does **not** drive the differential CAN_H / CAN_L. You need an **SN65HVD230**
  (3.3V, the right choice for the ESP32) or a TJA1050 (5V, needs level care) between the ESP32
  and the vehicle bus. Terminate the bus with **120Ω** across CAN_H/CAN_L at both physical ends;
  many SN65HVD230 breakouts ship a 120Ω with a solder-jumper — check before adding a second.
- **Exactly two GPIOs**: `TWAI_TX` (output-capable) and `TWAI_RX` (any GPIO, input-only is fine).
  Routed through the GPIO matrix, so the pin choice is free within what the board exposes.
- **Bitrate in firmware**, unlike SocketCAN: `TWAI_TIMING_CONFIG_500KBITS()` (the Fiat/OBD-II
  rate). Standard ESP-IDF timing macros cover 125k…1M.

### 3.2 The driver shape (drops beside `bus_mqtt` / `reactive_core`)

```cpp
#include "driver/twai.h"
// install once (RX-only for foreign ingestion; NO_ACK avoids perturbing the vehicle bus)
twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
    (gpio_num_t)TWAI_TX, (gpio_num_t)TWAI_RX, TWAI_MODE_LISTEN_ONLY);
twai_timing_config_t  t = TWAI_TIMING_CONFIG_500KBITS();
twai_filter_config_t  f = TWAI_FILTER_CONFIG_ACCEPT_ALL();   // or a hw acceptance mask
twai_driver_install(&g, &t, &f);
twai_start();
// in the reactive loop:
twai_message_t m;
if (twai_receive(&m, pdMS_TO_TICKS(0)) == ESP_OK) {          // non-blocking poll
    // m.identifier, m.extd (EFF), m.data_length_code, m.data[8]
    // -> run the §1.3 decoder map -> bus_mqtt_publish_number("fiat/soc", soc);
}
```

- **`TWAI_MODE_LISTEN_ONLY` is the right mode for reading a vehicle** — the controller receives
  and ACKs are *not* transmitted, so plugging into a live car's bus is passive and safe. (Full
  `TWAI_MODE_NORMAL` only if you ever transmit — the ComposeBus-over-CAN role, not foreign
  ingestion.)
- **Hardware acceptance filter** = the TWAI analog of SocketCAN's kernel filter: let silicon drop
  the firehose so the reactive loop only wakes for the decoder's frame ids.
- **Publishing is already built.** The decoded signal goes straight onto the existing bus surface:
  `bus_mqtt_publish_number("fiat/soc", 74.0)` (numeric → `BusEvent::Kind::Number`) or
  `bus_mqtt_publish_state(ns, str)` for enums (`bus_mqtt.h`). The frame arrives on esp-mqtt's task
  and is drained on the render/reactive task exactly like every other value — the threading
  discipline `bus_mqtt` documents. **No new publish path.** The TWAI reader is one more producer
  into `reactive_core`.
- **`no_std` note:** ESP-IDF is C/C++ on FreeRTOS, not Rust — there is no `no_std` question here.
  (If a Rust-on-ESP path is ever chosen, `esp-idf-hal`'s `twai` wraps this same peripheral; out of
  scope.)

### 3.3 The board decision — CYD pin budget vs a bare ESP32 / qualia-class S3

**Flag this before committing the CYD to a car.** The classic CYD (ESP32-2432S028R) exposes
**exactly three** GPIOs on its P3 connector (`cyd/include/cyd_pins.h`):

```
CYD_P3_IO1  21     (output-capable)
CYD_P3_IO2  22     (output-capable)
CYD_P3_IO3  35     (INPUT-ONLY)
```

TWAI needs TX (output) + RX (any). So the CYD *can* host CAN — e.g. `TWAI_TX=21`, `TWAI_RX=35`
— but that **consumes 2 of its 3 free pins**, leaving one, and GPIO35 being input-only forces the
RX assignment. Everything else (SPI display, XPT2046 touch, backlight, LDR, speaker, SD-CS) is
already committed. It's tight but viable for a fixed CAN dash.

The **ESP32-S3 qualia-class** board (`cyd/include/qualia_panel.h`, the PSRAM tier) has far more
free GPIO and the headroom the CYD lacks — and it's already the sanctioned tier for the heavier
DRADIS contact rendering. **Recommendation:** if the target is a permanent Fiat dash on a small
panel, the CYD's two-pin budget is acceptable and cheapest. If the CAN node is doing anything
beyond a couple of gauges, or you want pins free for a transceiver enable / bus-off LED / a second
bus, **put CAN on a bare ESP32 devkit or the qualia S3** and let it publish to the same `fiat/*`
namespaces the CYD dash subscribes — decoupling the *reader board* from the *display board* over
the bus (which is the whole point of the contract: they need not be the same device).

---

## 4. License — the whole reason we're doing this

| Tier | CAN mechanism | License | In-repo? |
|---|---|---|---|
| Linux / RPi | Python **stdlib `socket`** (`AF_CAN`/`CAN_RAW`) | **PSF (MIT-compatible)** | stdlib, nothing vendored |
| ESP32 / CYD | **ESP-IDF `driver/twai.h`** | **Apache-2.0** | fetched by PlatformIO, not vendored |
| dev/test | `mockcan.py` (in-house) | project (→ MIT) | first-party |

- **`python-can` (LGPL-3.0) is eliminated**, not merely isolated. The MIT-readiness assessment
  (§2b, §2e, checklist P1-5) currently lists it as "the one copyleft dep in the publishable tree"
  and recommends *keep-optional-noted* as a fallback. This design takes the **stronger** option
  the assessment names — "reimplement over SocketCAN directly" — so the copyleft caution simply
  **disappears** from the CAN path. Update `mit-readiness-assessment.md` §2b/§2e and strike P1-5
  when §2.3 lands.
- **ESP-IDF Apache-2.0** was already an accepted platform license (the CYD graphics stack is
  ESP-IDF; `mit-readiness-assessment.md` §2c). TWAI adds no new license surface.
- Net: the entire cross-tier CAN capability is **MIT-clean with zero copyleft**, and it removes an
  existing obligation rather than adding one. This is a pure license win on top of a dependency
  reduction.

---

## 5. Phased plan (contract-first)

Ordered so the contract is ratified before either reader is built, and each reader is
independently shippable.

### Phase 0 — Ratify the contract + spec home (this doc → a spec node)
- Land §1 as a spec node. **Spec home:** a new `spec/21-cross-tier-can.smd` (migrating to
  `.lts` with the corpus), or a `[BP-CAN-FOREIGN]` section under
  `spec/01-composeio-bus-protocol.smd` beside the existing `[BP-MSG-CAN]` — **recommend the
  latter's *namespace contract* lives with the bus protocol, and the reader/decoder mechanics get
  their own doc.** Cross-reference the hardware node **`[HW:mcp2515]`** (today only prose in
  `compose_projects/carousel/README.md`:72 — promote it to a real hardware spec node covering the
  SPI-CAN wiring + `can0.service`) and a sibling `[HW:sn65hvd230]` / `[HW:twai]` node for the
  ESP32 transceiver + pin budget (§3.3).
- Define the decoder map schema (§1.3) as the ratified artifact. Route the `.lts` authoring to
  **`factlattice-editor`**; record the python-can-drop decision with a `change` /
  `context_for_change` (invalidating assumption: *"a shared CAN library is worth an LGPL dep"* —
  false once the ESP32 tier proves no shared library is even possible).
- **Acceptance:** the two-layer namespace shape + frame convention + decoder schema reviewed and
  frozen. No code depends on an unratified contract.

### Phase 1 — SocketCAN reader + `bridge.py` migration (retires the LGPL dep)
- Build the §2.1 stdlib reader behind the `mockcan.py` seam (§2.2); unit-test the frame
  pack/unpack + EFF handling against `MockCanBus` on macOS CI.
- Migrate `services/can_mqtt_bridge/bridge.py` (§2.3), delete `python-can`, update
  `mit-readiness-assessment.md`, strike checklist P1-5.
- **Acceptance:** `bridge.py`'s ComposeBus-over-CAN round-trip is byte-identical to today on real
  `can0` (RPi) and green under `mockcan` (macOS CI); `pip`-installing the bridge pulls **no LGPL
  dependency**; the decoder evaluator decodes a synthetic frame → the right `fiat/*` value.

### Phase 2 — ESP32 TWAI reader (the second native producer)
- Add the §3.2 TWAI reader to `cyd/` firmware behind a board profile; wire its decoded output
  through the existing `bus_mqtt_publish_*`. Crosscheck the C++ decoder evaluator against the
  Python golden on the shared decoder map.
- Make the board call (§3.3): CYD two-pin budget vs a bare ESP32/S3 CAN node. **Recommend a
  standalone ESP32/S3 CAN reader** publishing `fiat/*`, so the display board (CYD or RPi) stays a
  pure subscriber — maximal use of the contract's decoupling.
- **Acceptance:** a bench SN65HVD230 + a CAN frame generator drives `fiat/*` onto the shared
  broker; the carousel Fiat tile's gauges move live off the ESP32-published state, indistinguishable
  from an RPi-published one (the tier-agnostic proof).

### The blocker: the Fiat 500e `candump` (OQ-1)
The decoder map (§1.3) is a **placeholder until we know the Fiat 500e's actual CAN IDs and signal
layouts.** This is the standing open question — `fiat/project_spec.smd` **OQ-1**: *"Which specific
CAN PIDs does the Fiat 500e expose on the OBD-II port? — Open, needs candump capture from live
vehicle."* Every SOC/speed/range/gear frame id in this doc is a stand-in.

- **The unblocking action is a `candump` from the car** (Layer-1 raw capture is *exactly* the
  tool for this — enable `can/fiat/*`, drive/toggle the car, correlate frame changes to
  dashboard changes, back out start-bit/scale/offset). The stdlib reader's raw layer **is** the
  reverse-engineering instrument; Phase 1 delivers the capture tool as a side effect.
- Until the candump exists: the readers, the bus contract, the tile, and a *synthetic* decoder all
  ship and are testable end-to-end (published synthetic `fiat/*` moves the gauges, already proven
  in the carousel tests). Only the **real Fiat map** waits on David + the car.

---

## 6. Why this is the right shape (pushback-proof summary)

- **The tiers meet at the bus, not a library** — because they physically cannot share a library
  (SocketCAN ≠ TWAI silicon). The namespace contract (§1) is the only possible unifier, and it's
  the *better* one anyway: reader board and display board decouple over the bus.
- **`python-can` earned nothing but an LGPL obligation** — `bridge.py` used only raw
  `socketcan` frames, which is ~20 lines of stdlib (§2.1). Dropping it is a dependency reduction
  *and* a license win (§4), taking the strongest option the MIT assessment itself named.
- **Config-over-code (SDR heuristic)** — vehicle specificity lives in a declarative decoder map
  (§1.3), not in reader firmware. The same reader is any vehicle via a different map; an
  Intelligence edits the map as a peer bus artifact.
- **Reuses what exists** — `mockcan.py` (the sanctioned non-Linux substrate), `bridge.py`'s codec,
  the CYD `bus_mqtt`/`reactive_core` publish path, the carousel `fiat/*` contract. New code is a
  stdlib socket wrapper + a TWAI reader + one decoder evaluator per tier.
- **Honest about the blocker** — the architecture is complete and testable today; only the real
  Fiat 500e decoder map waits on an OQ-1 candump, and Phase 1 ships the very tool that captures it.

**Recommendation: ratify the §1 contract (Phase 0) now; build Phase 1 (SocketCAN + bridge
migration, which clears the LGPL caution) next; sequence Phase 2 (TWAI) behind a board decision;
capture the Fiat candump opportunistically to populate the real decoder map.**

---

### Sources (ESP32 TWAI specifics)
- [TWAI — ESP-IDF Programming Guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/twai.html)
- [ESP32 CAN Bus with SN65HVD230 — Zbotic](https://zbotic.in/esp32-can-bus-industrial-communication-with-sn65hvd230/)
- [Mastering ESP32 CAN Bus — SaludPCB](https://saludpcb.com/esp32-can-bus-complete-guide-sending-receiving/)
- [can-multibackend-idf (TWAI + MCP2515 unified interface)](https://github.com/idf-can-bus/can-multibackend-idf)
</content>
</invoke>
