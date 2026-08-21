# Handoff — board bring-up + the ComposeBus transport/config tier

**Written 2026-08-12 at end of session, for me.next, who remembers none of this.**
Branch `perf/hosyond-dirty-rows`, ~24 commits, **NOTHING PUSHED.**

Read this before touching hardware or the bus tier. It is ordered by what will bite you
soonest, not by what happened first.

---

## 1. Physical state of the bench, right now

| thing | state | note |
|---|---|---|
| **Hosyond ESP32-S3** `hosyond-d1afe8` | **UART transport, `config: bus`, RADIO DOWN** | No IP. `ping 192.0.2.36` fails **by design**. No `/capture.bmp` at all. |
| **Mac↔Hosyond UART** | FTDI `/dev/cu.usbserial-FTZ29P6A`, 115200 | To the Hosyond's UART JST: TXD0=GPIO43, RXD0=GPIO44, **common ground**. Crossed TX/RX. |
| **uart_mqtt_bridge** | running under `nohup`, `/tmp/uart_bridge.log` | **The Hosyond is deaf without it.** It dies with the terminal — bench-grade, not infrastructure. |
| **Waveshare C6** `wsc6-6f4ce4` | offline, USB power only | JST soldered to UART pads. **Owes a reflash + runtime re-verify.** |
| **C3 dual-throttle** `cio-ctrl-db1c` | live, publishing `throttle/left|right` | ⚠ **NEVER flash/reset/open this port.** It drives the panels. |
| **pi.example.local + Waveshare 2-CH CAN HAT** | can0+can1 UP @500k, ERROR-ACTIVE | Channels jumpered H↔H/L↔L, 120 Ω both. Loopback proven byte-exact. |
| **broker / bus_storage** | `mqtt-broker.local` :1883 / :8420 | **LIVE PRODUCTION BUS.** Console, throttle, intelligences. |

**To get the Hosyond back on WiFi** — two bus commands, no cable:
`_device/hosyond-d1afe8/config_source = "http"` then `_device/hosyond-d1afe8/restart = 1`.

---

## 2. The one thing to internalise

**Machine signals in this fleet routinely agree with the wrong answer.** Not "are absent" —
*agree*. Tonight produced at least six, each looking healthy:

- `capture.bmp` rendered perfectly while the panel byte-swapped every pixel — the capture
  decodes the same buffer with the same endian assumption that wrote it.
- Raising SPI 11.4→40 MHz improved **every** timing number while the display got worse
  (dropped whole transactions; no tearing, just fewer updates).
- `MCP2515 successfully initialized` with the interrupt line wired to the wrong GPIO — inits
  clean, transmits fine, never receives.
- `TF13` build tag unchanged across ~10 flashes and three boards, one of which did not exist
  when the string was written.
- A `kMaxPersist` of 4 made a leaf structurally unable to receive what it had just requested.
- "Unclaimed" peripherals: a WS2812 latches, so *not touching it* left the factory demo's LED
  burning.

**Rule that falls out:** any property read back through the same assumption that wrote it
cannot be self-verified. Colour order, endianness, GRAM offset, clock integrity, brightness,
latched external state. Those need eyes, always. `[CXC-BOARD-HW-SELF-CONFIRMING]`.

**And when asking David to check the glass, ask about SPECIFIC colours** — "blue shows green,
green shows red" is arithmetically a *byte* swap; a *channel* swap shows blue as red. That
distinction turned a hunt into a one-bit fix.

---

## 3. What was built (all on one branch, unpushed)

**Two board bring-ups, both accepted on the glass.** Hosyond ESP32-S3 (ILI9341V 240×320 +
FT6336G touch, PSRAM full-frame tier) and Waveshare ESP32-C6 (ST7789T **172×320**, band tier,
no FPU, no touch). Profiles, build envs, descriptors, recipes, factory-image backups.

**Dirty-repaint restored** on the full-frame tier — and half the latency turned out to be
*outside* the renderer, in a touch-poll loop that delayed bus draining by up to 100 ms.

**The ComposeBus transport seam** (`DP-CIO-6` reaches the firmware). `bus_transport.h`; MQTT
and UART and CAN behind it; NVS-selected per boot; the wire-code table generated from Python
with a parity test. `restart.cpp` was already doing dependency injection correctly and needed
**no changes** — that is the canary for a correctly-shaped seam.

**Config over the bus** — `[SPEC:CXC-CONFIG-BUS]`. A board with **no IP** requests its scene
over UART, reassembles 5,856 bytes in 25 frames, verifies a content hash, renders. Unsolicited
push works too.

**Docs that matter:** `docs/board-bringup/RUNBOOK.md` (generalized procedure + trap
catalogue), `corpora/platform-specs/60-board-hardware-inventory.lts` (per-board pin facts,
queryable), `61-config-over-bus.lts` (the protocol + as-built + measured + verified).

---

## 4. Traps that will cost you hours if you rediscover them

**`platformio.ini` `build_flags` NEVER reach ESP-IDF component compiles.** Silently dropped.
So do `HOSYOND_S3`/`WAVESHARE_C6` — those are CMake **cache vars**, which select source files
but never reach the compiler. Every compile-time switch is `target_compile_definitions`,
verified in `.pio/build/<env>/compile_commands.json`. This caused three separate defects.

**Port paths are not identities.** `/dev/cu.usbmodem*` renumbers when devices are unplugged.
Verify passively with `system_profiler SPUSBDataType` (native-USB ESP32s publish their MAC as
the USB serial). **Never `esptool chip-id` as an identity check** — it resets the board, which
is the harm. I reset the live throttle controller this way.

**Prove the substrate before the protocol.** ROM banner for UART; `cangen`/`candump` for CAN.
Never let a wiring fault and new firmware be suspects simultaneously.

**UART crosses TX/RX. CAN does not** (H↔H, L↔L). The habit from the previous link carries.

**`.lts` property keys must be single-token.** `- why this matters:` is prose; `-
why-this-matters:` is data. `lts_preview` against the target corpus before landing, and check
property counts — a section with one property is a section that did not land.

---

## 5. Open, and whose call it is

**Needs David's eyes (nothing else can settle it):** that the Hosyond's engine-thrust panel is
rendering correctly under `config: bus` — there is *no capture endpoint* with the radio down.
Ask about the L/R bars specifically: green→amber→red bottom-up. Also the magenta `CACHED +Nb`
staleness badge, log-verified only.

**Owed work:** C6 reflash + runtime re-verify (it is build-verified only, and the transport
work has never run on it). CAN has **never carried a live message** and the relay has **never
run on silicon** — both host-proven against the Python oracle only; an SN65HVD230 was being
soldered when the session ended.

**Unresolved by decision, not by effort:** CAN arbitration priority for config chunks —
`[BP-CAN-ID]`'s 29-bit layout has no spare bits and a live bridge depends on it.

**Latent, not urgent:** `env:qualia_display` builds correctly now but that board still runs an
older binary; it comes back correct on next reflash. The `can_mqtt_bridge` subscribes
`composeio/#` and would firehose the whole MQTT bus onto a 500 kbit CAN segment — an agent was
mid-fix at session end.

**Placement unratified:** `60-` and `61-` sit in `corpora/platform-specs/`; the bus_storage
singleton invariant is recorded in `60-` but belongs in a bus/platform spec.

---

## 6. How David works, learned the hard way

- **He corrects framing, and the corrections are load-bearing.** "Not *should* — there may NOT
  be more than one bus_storage instance." Prohibition vs convention. I twice made his
  invariants *stricter* than stated; over-tightening looks like caution and costs capability.
- **He rejects slicing that exists for legibility.** "It often isn't necessary and can be
  counter-intuitive to the implementing intelligence." The test: must something be validated
  at this boundary before the next thing can proceed? If not, one batch.
- **He will wire hardware if asked.** Ask. Do not scope around an absent part.
- **He gives context before answers when the context reframes the question** — if he says he
  is setting context, the design question you were about to ask may dissolve.
- **He wants opinions, not option menus.** Recommend, then give the trade.

---

## 7. If you do nothing else

1. Ask David about the glass (§5) — it is the only unblocked verification.
2. Do not push. Nothing has been pushed all session; that is deliberate.
3. Do not touch the C3 on the live throttle port.
4. The `uart_mqtt_bridge` under `nohup` is what keeps the Hosyond alive. If the Hosyond looks
   frozen, check that first — a slaved board is exactly as available as its upstream.
