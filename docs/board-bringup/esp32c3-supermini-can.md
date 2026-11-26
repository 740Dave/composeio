# ESP32-C3 Super Mini — the fleet's first CAN node

**Status as of 2026-08-12: still BLOCKED, one fault down, one to go.** No frame from this node has
reached the segment. Two hardware faults were found; a third is outstanding — see
[What the bench needs to check](#what-the-bench-needs-to-check).

> **A claim in an earlier version of this document was withdrawn.** It said "MCU side fully proven"
> on the strength of the TWAI loopback self-test. That test later returned FAIL for the same binary
> on the same board, so its PASS was not repeatable and could not carry the weight put on it. See
> [The self-test is asymmetric evidence](#the-self-test-is-asymmetric-evidence).

The machine-readable source of truth is `esp32c3-supermini-can.descriptor.yaml`. This is the
human recipe and the reasoning.

---

## What this is

`cyd/[env:esp32_c3_can_bringup]` — a headless ComposeBus node on an ESP32-C3 Super Mini with an
SN65HVD230 transceiver. No glass, no scene, no config fetch, **no radio**. It does four things:

1. publishes an incrementing heartbeat on CAN every 1 s (MCU → Pi)
2. subscribes `throttle/left` and logs every arrival (Pi → MCU)
3. publishes `twai_get_status_info()` every 5 s — eight fields, one namespace each
4. says the same thing on the console

Item 3 is the reason the env exists. `cyd/src/bus_can.cpp` answers `BusTransport::connected()`
with `s_started`, never reads the controller's error state, never enables `TWAI_ALERT_BUS_OFF`
and never calls `twai_initiate_recovery()` — and ESP-IDF's TWAI driver does **not** recover on
its own. A bus-off ESP32 therefore reports CONNECTED forever. This env builds the missing
instrument. It deliberately does not change the policy: recovery semantics and a first light are
two changes whose blast radii would compound, and "retry forever, back off, or announce?" is a
genuinely unmade decision.

## Build

```bash
cd cyd
pio run -e esp32_c3_can_bringup -t upload
pio device monitor -e esp32_c3_can_bringup
```

Layers `sdkconfig.defaults` → `sdkconfig.c3.defaults` (shared with `env:esp32c3_dsl_test`; the
requirement is identical — native-USB console on a C3 — and a second near-identical defaults file
is exactly the kind of duplicate that drifts).

### The build trap, and how to prove you avoided it

`-DCXC_CAN_BRINGUP=1` is a CMake **cache variable**. It selects the branch in
`cyd/src/CMakeLists.txt` and **never reaches the compiler**. `platformio.ini` `build_flags` do not
reach an ESP-IDF component compile either. So every compile-time switch is a
`target_compile_definitions(${COMPONENT_LIB} PUBLIC …)` in the branch.

Verify before flashing, not after:

```bash
cd cyd && python3 -c "
import json; d=json.load(open('.pio/build/esp32_c3_can_bringup/compile_commands.json'))
c=lambda e: e.get('command') or ' '.join(e.get('arguments',[]))
for w in ['CXC_CAN_BRINGUP_APP','CXC_DEVICE_PREFIX','CXC_CAN_TX_GPIO','CXC_CAN_RX_GPIO','CXC_CAN_BITRATE']:
    print(w, sum(w in c(e) for e in d))"
```

2026-08-12: all five present in **14** translation units. And the negative control —
`-DCXC_CAN_BRINGUP` on its own — reached **0**. That zero is the trap made visible: a
`#if defined(CXC_CAN_BRINGUP)` in a source file would be dead code that looked correct.

## Identity before every flash

`/dev/cu.usbmodem11301` belonged to a different board an hour before this env was written, and
mid-session the *other* usbmodem port changed device entirely. **Both C3s on this bench share the
MAC prefix `1C:DB:D4`** — match the full serial.

```bash
ioreg -r -c IOUSBHostDevice -l -w0 | awk '/"USB Serial Number"/{sn=$NF} /IOCalloutDevice/{print sn, $NF}'
```

Target is `"00:00:5E:00:53:03" "/dev/cu.usbmodem11301"`. **`esptool chip-id` is not an identity
check** — it resets the board, which is the harm being prevented.

To reset without esptool (and so without a bootloader entry), pulse RTS on the open port.

## The namespaces, and the arbitration ids

Every namespace this node speaks is **CAN-only**: nothing on MQTT publishes `_device/c3can-047dd8/*`,
so the bridge's self-populating registry can never learn the names by observation. All nine must
be in `CAN_BRIDGE_REGISTER` (`deploy/can_mqtt_bridge/composeio-can-bridge.env`) or the bridge drops
them, correctly and loudly, as unresolvable hashes.

Ids **derived independently** with `composeio.config.namespace_hash` — not read back from the
firmware that writes them:

| namespace | type | ns hash | arbitration id |
|---|---|---|---|
| `_device/c3can-047dd8/heartbeat` | heartbeat (4) | `0xB000` | `1600090E` |
| `_device/c3can-047dd8/status` | state (0) | `0xB152` | `162A410E` |
| `_device/c3can-047dd8/twai/tx_error_counter` | state | `0x30B9` | `0617210E` |
| `_device/c3can-047dd8/twai/rx_error_counter` | state | `0xF002` | `1E00410E` |
| `_device/c3can-047dd8/twai/tx_failed_count` | state | `0xE1C1` | `1C38210E` |
| `_device/c3can-047dd8/twai/rx_missed_count` | state | `0x40EE` | `081DC10E` |
| `_device/c3can-047dd8/twai/rx_overrun_count` | state | `0x0792` | `00F2410E` |
| `_device/c3can-047dd8/twai/bus_error_count` | state | `0x6FC3` | `0DF8610E` |
| `_device/c3can-047dd8/twai/msgs_to_rx` | state | `0x5227` | `0A44E10E` |
| `throttle/left` *(inbound)* | state | `0x03F6` | `007EC000:1FFFFE00` |

Source field is `namespace_hash("c3can-047dd8") & 0x1FF` = `0x10E`.

The heartbeat rides as **`Heartbeat`**, not `State`, on purpose: it is what the message *is*, and
it puts a non-zero type nibble in the id, so a candump line proves the `[BP-TYPE-CODES]` table
crossed the wire instead of proving only that a zero is a zero.

Eight bytes cannot carry a health struct, so the eight `twai_get_status_info()` fields go up one
namespace each, and the state goes up as a short string. **Seven characters is the whole budget**
(`bus_value_encode_str` emits `[len][utf8…]` into an 8-byte field), so `ERROR-ACTIVE` does not fit
and is *refused* by `publish_state` rather than truncated: `ACTIVE` / `WARN` / `PASSIVE` /
`BUSOFF` / `RECOVER` / `STOPPED`. A truncated status string decodes cleanly as a **different**
status, which is the worst possible failure for a health namespace.

## The self-test is asymmetric evidence

The TWAI loopback self-test (`TWAI_MODE_NO_ACK` + self-reception, CTX and CRX on one unconnected
pad) once returned:

```
SELF-TEST loopback: sent id 0x1600090E, got 0x1600090E -> id MATCH, payload MATCH
SELF-TEST result: PASS | tx_err=0 rx_err=0 bus_err=0
```

and later, for the **same binary on the same board**, returned `tx_err=128` FAIL — on three
separate pads (GPIO10, GPIO7, GPIO3), each of which a drive-and-read-back test proved follows the
chip perfectly (`drove LOW -> read 0, drove HIGH -> read 1`).

Cause: with `tx_io == rx_io` the driver configures **one pad twice**, once as an output for CTX and
once as an input for CRX, and the surviving direction is not something the test controls. Adding
`gpio_set_direction(GPIO_MODE_INPUT_OUTPUT)` after install made the outcome *deterministic* but did
not make it pass.

**So the verdict is asymmetric, and the firmware now says so in the log itself:** a PASS is real
evidence (those matched bytes had to come from somewhere); a FAIL is the *absence* of evidence and
must never be read as "the MCU is broken". An earlier report leaned on the PASS as a headline
proof. That claim is withdrawn. **An unrepeatable test is not a measurement.**

## What IS proven, without the wire

**Without the C3 in the loop at all**, `cangen` (a tool this project did not write)
injected `162A410E` on `can1` and the bridge answered:

```
CAN->MQTT  _device/c3can-047dd8/status = 'ACTIVE'     (type=state)
```

That independently validates the id derivation, `CAN_BRIDGE_REGISTER`, and the CAN→MQTT path.

## What is NOT proven

Nothing this node has transmitted has reached the segment. `can0`'s RX counter did not move for
any C3 frame, and `candump can1` — the independent witness — recorded none.

## Two faults found so far

**Fault 1 — wrong RX pin (fixed in firmware).** The firmware listened on GPIO4; the transceiver's
`R`/`RXD` output is wired to **GPIO6**. GPIO4 is connected to nothing. `CXC_CAN_RX_GPIO` is now 6.
The owner's wiring was fine and was **not** changed.

**Fault 2 — no ground between the breadboard and the Pi HAT (fixed by the owner).** The C3 is
USB-powered from the Mac and the HAT from the Pi, so the pair had no shared common-mode reference.
The effect is visible in the instrument output before and after:

| | GPIO5 (CTX), floating-input probe | listen-only `bus_err` |
|---|---|---|
| before ground | `hi/lo = 351866/46145`, 6734 transitions, 125–138 ns pulses | 7832 at 500k |
| after ground | `hi/lo = 399720/0`, **0 transitions**, clean idle-high | **0** at every rate |

That is the ground fix, measured. The noise I had been characterising was common-mode garbage on an
unreferenced pair.

> **Neither fault could be diagnosed while the other was live.** An unconnected pin and a pin whose
> transceiver has no ground reference produce the *same* reading, so my GPIO scan — which did cover
> GPIO6 and correctly reported "no dominant pulse" — could not tell them apart. They were
> simultaneous, not sequential, so no ordering of software tests would have separated them. This is
> the limit of "never let two suspects be live at once": you cannot always choose.

## What the bench needs to check

**Outstanding: CANH/CANL between the C3's SN65HVD230 and the Pi HAT appear to be open.** Current
evidence, all post-ground-fix:

- **GPIO6 idles clean recessive HIGH and never goes dominant**, with zero transitions, while
  `cangen` puts 200 frames/s on the segment (confirmed: `can1` TX +6000 during the window). The
  transceiver is powered and driving its `R` output — it simply never sees the bus.
- **The C3 now transmits.** `heartbeat 1 sent`, then `tx_err` climbs 160 → **bus-off**. Per the TEC
  rule below that is *"transmitted and something came back wrong"*, not *"never started"* — a real
  advance on the original CRX=4 state, which pinned at `tx_err=0 / msgs_to_tx=17`.
- **`can0` RX did not move at all.** No C3 frame reached the Pi.

Both directions dead at the CANH/CANL layer while the transceiver's logic side is healthy. Please
check continuity **CANH ↔ CANH and CANL ↔ CANL** between the breakout and the HAT (CAN does not
cross; UART does), and that the breakout's 120 Ω is across the pair rather than to ground.

### Historical: the pre-ground-fix measurements

These are kept because they are what the instruments said while two faults were live, and because
the "no pin carries a signal" conclusion they supported was true but not sufficient.

**1. Pre-protocol pin probe** — plain GPIO input sampling, *before* `twai_driver_install()`, so
nothing here is the protocol's fault:

| pin | no pull | with pull-up | verdict |
|---|---|---|---|
| gpio4 | all LOW | all HIGH | high impedance — nothing driving it |
| gpio5 | idles HIGH, activity tracks traffic (1.2% low idle → 2.2% at 40 f/s → 11.6% dense) | all HIGH | driven, but see below |

**2. Bit time**, one uninterrupted 16384-sample capture with interrupts off:

```
BIT-TIME gpio5: 125 ns/sample, 2052 us window | shortest dominant run 1 samples = 125 ns
```

One bit at 500 kbit/s is **2000 ns**. gpio5's shortest dominant pulse is 125–138 ns — fifteen
times too narrow to be data. That is coupling or ringing, not a transceiver R output.

**3. Listen-only trials.** `TWAI_MODE_LISTEN_ONLY` never drives the bus, not even the ACK bit, so
it is safe on a live segment *and* it isolates RX from TX completely:

| rx pin | 1M | 800k | 500k | 250k | 125k | 100k |
|---|---|---|---|---|---|---|
| gpio5 frames | 0 | 0 | 0 | 0 | 0 | 0 |
| gpio5 bus_err | 16952 | 13112 | 7832 | 2945 | 10 | 0 |
| gpio4 frames | — | — | 0 | — | — | — |

`bus_error_count` scales with bit rate rather than dipping at any one, which **excludes** a
bit-rate mismatch instead of leaving it a suspicion. A scan of every accessible pin
(0,1,2,3,4,5,6,7,20,21) found none with a ~2000 ns dominant pulse.

The ground hypothesis this section originally led with was **correct** and has been fixed; the pin
hypothesis was **also** correct and has been fixed in firmware. What remains is the pair itself.

The segment itself is fine and was never disturbed: `can0` held at bus-errors 0 / error-warn 2 /
error-pass 2 / bus-off 0 (its pre-existing baseline) and `can1` at all zeros, through several C3
bus-off episodes.

> Note on what the earlier "substrate proven" result covered: 500/500 frames `can1`→`can0` proves
> the *Pi's two interfaces and the cable between them*. It never involved this board's
> transceiver, which was consequently never proven at all.

## Failed experiments, with their numbers

Kept because each one produced a confident, plausible, **wrong** answer.

| experiment | what it said | why it was wrong |
|---|---|---|
| Swapping the pin defaults to CTX=4 / CRX=5 | the pre-protocol probe showed gpio5 externally driven with traffic-correlated activity and gpio4 high-impedance — textbook R output and D input | gpio5's "traffic" was 125 ns common-mode noise on an ungrounded pair, not 2 µs bits. The real RX pin was **GPIO6** and neither candidate was right. Reverted, then corrected to CRX=6 once the owner confirmed the build. |
| Reading the six-rate listen-only sweep as excluding a bitrate mismatch | `bus_error_count` scaled linearly rather than dipping at any rate | The inference was sound but one hypothesis short: linear scaling is *equally* the signature of "there is no data here at all". A good instrument, over-read. |
| "MCU side fully proven" from the loopback self-test | id and payload matched byte for byte, `tx_err=0` | Not reproducible — the same binary later failed on three proven-good pads. Withdrawn. |
| `NO_ACK` self-test on the live pins | `tx_err=128`, "FAIL — the fault is on the MCU side" | `TWAI_MODE_NO_ACK` still monitors the **real** CRX pin for bit errors, so the test failed because of the very thing it existed to exclude, and accused the wrong component with total confidence. Fixed by putting CTX and CRX on one unconnected pad. |
| First bit-time instrument | "shortest dominant run = 1 sample ⇒ **~5812 kbit/s**" | It checked `esp_timer` every 100 reads, leaving a ~15 µs blind gap; a level change across a gap is indistinguishable from a one-sample pulse. The tell: the pulse count (23574 in 500 ms) matched the *gap* count almost exactly. Fixed with one uninterrupted capture, discarding runs that touch the buffer ends. |
| First pin probe | crashed the boot | A tight 400 ms sampling loop on this single-core part tripped the task watchdog — a diagnostic that caused the failure it was reporting. Now sampled in 10 ms slices with a yield. |

## `tx_error_counter == 0` is the diagnostic key

`twai_transmit failed: ESP_ERR_TIMEOUT`, under `bus_can.cpp`'s message *"no other node is present
to ACK"*, points at the far end. The far end was healthy the whole time. The discriminator is TEC:

- **TEC climbing to 128, then bus-off** — the node *is* transmitting, and something about what
  comes back is wrong.
- **TEC stuck at 0 with `msgs_to_tx` pinned at 17** (the 16-deep queue plus one in the controller)
  — the node has **never started**. Its CRX never sees the eleven recessive bits that mean
  bus-idle, so arbitration never begins and no error is ever earned.

This board showed both, from the two pin assignments. From inside the driver they are one log
line. Ask what the counter would read if the other story were true.

## Known defect this bring-up surfaced (not fixed here)

`services/can_mqtt_bridge/bridge.py::message_from_can` carries its **own** value decoder, missing
the `len(data) == 4 → struct.unpack("<f")` branch that `composeio.transport_uart.decode_value` —
the canon, which `cyd/src/bus_frame.h` mirrors — has. A float32 from CAN therefore arrives on MQTT
as `int.from_bytes(data, "little")`: heartbeat `42.0` would publish as `1109393408`. The same
module already *imports* `encode_value` from `transport_uart` for the other direction, so it is
asymmetric within one file.

This is the `[BP-TYPE-CODES]` defect shape recurring in the **value** codec — a decoder copied per
transport, drifting, with no test across the seam.

Its nastiest property: a healthy segment produces all-zero counters, and float32 `0.0` decodes to
`0` under **both** codecs. The bug is invisible exactly when everything looks fine, and only
appears once a counter goes non-zero. Strings and 1-byte ints are unaffected.

Not fixed here: the change is to delete the copy and call `decode_value`, it lands in a live
production service, and it deserves its own before/after.

## Re-running the acceptance once the wire is fixed

```bash
# terminal 1 — the independent witness
ssh display-node.local 'candump -td -x can1'

# terminal 2
cd cyd && pio device monitor -e esp32_c3_can_bringup
```

Expect `1600090E` at 1 Hz on `can1`, `_device/c3can-047dd8/heartbeat` on MQTT, and — as the
physical throttle lever moves — `RX  throttle/left = <value>` on the console. The subscription is
already registered and the bridge's MQTT→CAN allowlist already carries `throttle/left`, so no
configuration change is outstanding; only the wire is.
