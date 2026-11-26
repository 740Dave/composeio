# ComposeBus-over-CAN — design brief for adversarial teardown

**Status:** proposal, NOT landed. Written 2026-08-12 for review by a CAN
specialist before any wire-format change reaches silicon.

**Why now:** two boards exist, nothing is pushed, and both are reflashable
(one is on a cable on the bench). The window where a wire-format change is
cheap closes as soon as a third node ships. The system owner's words:
*"this is the time to get prioritization right across the board, even within
namespaces. If we don't do it now, we'll never do it, almost certainly, and
we'll have mysterious delays in how different devices react."*

---

## 1. What this bus is

ComposeBus is a pub/sub bus. Messages are `(namespace, value, type, scope,
source)`. Namespaces are strings — `throttle/left`, `shields`,
`_device/hosyond-080739/config`. The primary substrate is MQTT over IP; UART
and CAN are alternate substrates so that a microcontroller with **no IP
address** can participate. That is the point of the whole exercise: an MCU
slaved over CAN or UART is a first-class bus participant.

Namespaces are identified on binary substrates by a **16-bit CRC-16/CCITT-FALSE
hash of the name** (`namespace_hash`). A hash is not reversible, so every peer
needs a name→hash registry; unknown hashes are dropped, never guessed.

Message types (`[BP-TYPE-CODES]`, 4-bit wire code, one table shared by every
binary substrate, generated into the firmware header and parity-tested):

| type | code | notes |
|---|---|---|
| STATE | 0 | live values — throttle positions, alert status, sensor readings |
| EVENT | 1 | discrete occurrences |
| CONFIG_PUSH | 2 | **bulk** — chunked scene payloads, see §4 |
| CONFIG_REQUEST | 3 | small, control plane |
| HEARTBEAT | 4 | liveness |
| DISCOVERY | 5 | |
| TIME_SYNC | 6 | |
| BINDING | 7 | |
| ACK | 8 | terminates a chunked transfer |
| LOG_ENTRY | 9 | diagnostics |

Codes 10–15 reserved. **These codes were allocated in declaration order, not
urgency order.** No priority meaning was ever intended for them.

---

## 2. The physical and logical situation

- **Bitrate 500 kbit/s**, sample point 0.875, classic **CAN 2.0B only**
  (extended 29-bit ids, 8 data bytes). No CAN FD anywhere in the fleet.
- **Controllers, deliberately heterogeneous:** Raspberry Pi 5 with a Waveshare
  2-channel HAT (two **MCP2515** + TJA1050, SocketCAN) and an **ESP32-C3**
  using its on-die **TWAI** peripheral with an SN65HVD230 transceiver. Two
  independent controller families and two independent stacks.
- **Topology at review time:** Pi `can0` and `can1` are jumpered H↔H / L↔L —
  one electrical segment. 120 Ω at `can0`; `can1`'s terminator is being
  removed so it remains an **unterminated listening stub** used as an
  independent witness. The ESP32-C3's SN65HVD230 breakout has a **soldered**
  120 Ω (permanently end-of-bus). Target: 60 Ω across the segment.
- **`txqueuelen` is 10** on the Pi interfaces. A measured 2000-frame burst
  produced **828 ENOBUFS backoffs**. The bridge currently catches ENOBUFS as a
  generic exception logged as `"CAN write error"`.
- **`restart-ms 100`** on both Pi interfaces (automatic bus-off recovery).
- **Live traffic:** an ESP32-C3 controller publishes `throttle/left` and
  `throttle/right` at roughly 20 Hz during physical lever movement (measured:
  271 frames in 240 s across both, smooth analog ramps).
- **Namespace population:** 56 distinct namespaces observed on the production
  MQTT bus in a 30 s sample; ~237 messages / 25 s. **Zero hash collisions
  today.** Birthday bound on a 16-bit hash: 2.3% at 56, 7.3% at 100, 15.7% at
  150, 39.2% at 256.
- A bridge (`services/can_mqtt_bridge`) translates MQTT↔CAN and is **deployed
  and running** on the Pi. It is being changed concurrently to subscribe the
  whole MQTT bus (`#`) rather than an allowlist, which will raise the
  namespace count crossing to CAN from 2 to ~56.

---

## 3. THE PROBLEM: two divergent layouts that disagree about arbitration

Two implementations exist in the same codebase with different 29-bit layouts.
This is recorded as `[BP-CAN-ID-DIVERGENCE]` — they "cannot share a physical
wire until reconciled."

**`[BP-CAN-ID]`** — used by the deployed bridge and by the firmware
(`cyd/src/bus_can_id.h`):

```
ns_hash(16) | type(4) | source_hash(9)
```

**`transport_can.py`** — the Python `CANTransport`:

```
type(4) | scope(2) | ns_hash(16) | seq(7)
```

They do not merely differ in bit positions. **They disagree about what
arbitration priority is for.** `[BP-CAN-ID]` puts the namespace hash in the
most significant bits, so priority is a pseudo-random function of the
namespace *name*. `transport_can.py` puts type at the top, so priority is by
message class.

Evidence that the first is an accident rather than a design — actual hashes,
sorted, which is the actual current arbitration order:

```
0x03F6  throttle/left
0x0D9E  throttle/right
0x159F  _app/console/bbox
0x2A97  dradis/bodies
0x6B92  _device/relay-000000/config
0x8921  _device/wsc6-07f199/config
0x9FC4  alert_status
0xC6B6  _device/hosyond-080739/config
0xCC50  shields
```

- Throttle wins everything **by luck** — its two names happen to hash lowest.
  Renaming the signal would silently re-rank it.
- `_device/wsc6-07f199/config` (0x8921) outranks
  `_device/hosyond-080739/config` (0xC6B6). Two transfers of **identical role
  and urgency** get different bus priority because of a MAC suffix.
- `shields` (0xCC50) is the lowest-priority thing sampled — a combat-critical
  value loses arbitration to console bounding boxes.

**Field usage note:** `source_hash(9)` is not decoded by any receiver today,
but it proved diagnostically valuable — it is what made a live capture legible
by identifying the physical controller on the wire. `seq(7)` in
`transport_can.py` is documented in its own source as "a rolling counter with
no consumer that needs 512 values." `scope(2)` is real data that the
`[BP-CAN-ID]` path currently **loses silently** on every CAN round trip.

---

## 4. The bulk-transfer regime change (untested, and the thing most likely to bite)

Configuration payloads are chunked: one header frame plus N sequenced data
frames, then an ACK naming a content hash over the whole payload.

- `UART_CONTENT_BYTES = 250` → a proven 5,856-byte scene = **25 frames**.
  This has run successfully on hardware.
- `CAN_CONTENT_BYTES = 6` (8 minus a 2-byte sequence number) → the same scene
  = **977 frames**. **This has never run.**

**Recovery is whole-transfer.** A `Reassembler.missing()` gap iterator exists
but nothing uses it; a bad ACK means the receiver discarded everything and
re-requests the entire payload. There is no selective repair and no flow
control.

Naive arithmetic on transfer success, if per-frame loss is p:

| per-frame loss | UART (25 frames) | CAN (977 frames) |
|---|---|---|
| 0.1% | 97.5% succeed | 37.6% succeed |
| 1% | 78% succeed | ~0.005% succeed |

Every retry is another 977 frames, so a marginal bus plausibly exhibits
congestion collapse rather than graceful degradation — and `restart-ms 100`
sits inside that loop.

**Also note:** with type codes as allocated, `CONFIG_PUSH` (2) outranks `ACK`
(8). Under any layout that puts type in the high bits, a bulk transfer would
outrank the acknowledgements that terminate it.

---

## 5. The proposal under review

**5a. Converge on one layout, type above namespace:**

```
type(4) | scope(2) | ns_hash(16) | src(7)      = 29 bits
```

Rationale: ends the divergence; makes priority a design decision rather than a
CRC artifact; preserves `scope` (currently lost); retains a narrowed `src` for
diagnostic identifiability; drops `seq`, which its own author documents as
having no consumer.

**5b. Renumber the type codes by urgency:**

```
STATE 0 · EVENT 1 · TIME_SYNC 2 · ACK 3 · CONFIG_REQUEST 4 ·
HEARTBEAT 5 · DISCOVERY 6 · BINDING 7 · CONFIG_PUSH 8 · LOG_ENTRY 9
```

Load-bearing moves: `CONFIG_PUSH` 2→8 (bulk yields to everything functional)
and `ACK` 8→3 (must beat the bulk it acknowledges).

Cost: the type code also rides in the UART frame's `type_scope` byte, so this
breaks UART too and requires reflashing both boards. The system owner has
explicitly accepted that cost.

---

## 6. THE OPEN PROBLEM — within-class priority, and the bit budget

Proposal 5a fixes *cross-class* priority but leaves *within-class* priority as
a hash accident. `alert_status` (0x9FC4) still loses to `throttle/left`
(0x03F6) among STATE messages, because alerts ride as STATE — there is no
ALERT type. The system owner has explicitly rejected deferring this:

> *"I would argue that this is the time to get prioritization right across the
> board, even within namespaces. If we don't do it now, we'll never do it,
> almost certainly, and we'll have mysterious delays in how different devices
> react."*

**The bit budget does not obviously accommodate it.** 29 bits total, and the
candidate fields are: priority(?) + type(4) + scope(2) + ns_hash(16) +
src(?). Adding an explicit per-namespace priority field above `ns_hash`
requires taking bits from somewhere. Candidates, none free:

- Narrow `src` further (already 9→7 in the proposal). At 4–5 bits it is
  nearly useless even as a diagnostic.
- Move `scope` out of the id into the data-side flags byte
  (`[BP-CAN-DATA]` reportedly already contemplates this). Frees 2 bits.
- Narrow `ns_hash`. **Strongly suspect** — 12 bits at 56 namespaces gives a
  ~53% collision probability, and a collision is not a drop, it is a receiver
  confidently decoding the wrong namespace.
- Stop hashing names entirely and **allocate** short ids from the bridge as a
  registrar, distributing the mapping over the bus. This eliminates collisions
  by construction and frees many bits, at the cost of making the segment
  stateful — a node that misses the allocation broadcast cannot decode until
  the next one (a 5 s periodic publication is already planned for other
  reasons). This is the direction that most resembles a conventional
  communication matrix, but it is the largest change.

**Also unresolved:** the 16-bit hash has no headroom. At 256 namespaces the
collision probability is 39%, and the fleet is on a growth path. Any layout
that keeps name-hashing inherits that curve.

---

## 7. What the reviewer is asked to do

Perform a complete teardown. Specifically:

1. **Is the proposal in §5 right, wrong, or right-for-the-wrong-reasons?**
   Challenge the premise if the premise is the problem.
2. **Solve or explicitly bound §6** — within-class priority within 29 bits.
   Do the arithmetic. Say what you would cut and what it costs. If the answer
   is "stop hashing and allocate," say so and describe the migration.
3. **Assess §4** — is the 977-frame bulk transfer viable on a shared 500
   kbit/s segment at all? What flow control does it need? Compare against
   ISO-TP / CANopen SDO block transfer practice.
4. **State worst-case latency** for the most urgent message class under the
   proposed scheme with the measured traffic, or state exactly what
   measurement is missing to compute it.
5. **Physical layer review** of §2 — the unterminated `can1` listening stub,
   the mixed MCP2515 / ESP32-TWAI segment, `txqueuelen 10`, `restart-ms 100`.
6. **Name the prior art** wherever J1939, CANopen, ISO-TP, or NMEA 2000
   already answers one of these questions, and say whether our difference is
   justified.
7. Separate **PROVEN from ASSUMED**, and flag anything here that you think is
   asserted more confidently than the evidence supports.

Nothing in §5 or §6 has been implemented. This is the moment to be told it is
wrong.
