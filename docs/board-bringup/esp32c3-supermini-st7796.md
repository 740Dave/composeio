# ESP32-C3 Super Mini + 3.5" ST7796S — the widest panel on the smallest RAM

**Status 2026-08-12: UP AND ACCEPTED.** The full chain is proven on hardware end to end —

```
physical throttle lever -> C3 dual-throttle controller -> Mac app -> MQTT
  -> composeio-can-bridge (rpi5) -> CAN -> this ESP32-C3 in LISTEN-ONLY -> ST7796S 480x320
```

— with the owner moving the levers and the bars tracking: *"yes, and it's really snappy too."*
**The first MCU in this fleet to do real work on the ComposeBus CAN segment** rather than only
instrument one.

That verdict is worth a number next to it: 307,200 B per frame — **2.8× the C6's framebuffer** — on
single-core RISC-V with no PSRAM and no FPU, responsive under live ~10 Hz throttle traffic. Dirty-band
repaint is the whole reason that is possible; a full frame is 322 ms.

The machine-readable source of truth is `esp32c3-supermini-st7796.descriptor.yaml`. This is the human
recipe and the reasoning.

> **This is the SAME PHYSICAL BOARD AND THE SAME MAC as `esp32c3-supermini-can.md`**, in a different
> role. That one is a headless CAN first-light node with no glass; this one drives a 480x320 panel and
> takes throttle values *off* the segment. Role, not part, is what makes a board entry — the pin map,
> the render tier, the scene and the failure modes share nothing. Only one of the two firmwares can be
> on the board at a time.

---

## What this is

`cyd/[env:esp32_c3_st7796]` — the standard ComposeIO display firmware with exactly one substitution at
the board seam:

```
boards/classic_cyd.cpp + ili9341.cpp  ->  boards/esp32c3_st7796.cpp
touch_xpt2046.cpp                     ->  (none; no pins left)
```

`main.cpp`, the canvas, the scene builder, every widget and the capture server are untouched. The
board-specific numbers all live in `cyd/include/esp32c3_st7796_panel.h`.

It renders the BSG **ENGINE THRUST** panel from throttle values that arrive over CAN, relayed onto the
segment by `composeio-can-bridge` on rpi5 from the physical dual-throttle controller.

## Build and flash

```bash
cd cyd
pio run -e esp32_c3_st7796 -t upload
pio device monitor -e esp32_c3_st7796
```

### Identity before every flash — not optional

`/dev/cu.usbmodem11301` changed device **four times** in one evening on this bench, and iterating over
ports once reset a live controller that had been explicitly ring-fenced. Both C3s here share the MAC
prefix `1C:DB:D4`.

```bash
ioreg -r -c IOUSBHostDevice -l -w0 | awk '/"USB Serial Number"/{sn=$NF} /IOCalloutDevice/{print sn, $NF}'
```

| MAC | port | rule |
|---|---|---|
| `00:00:5E:00:53:03` | `/dev/cu.usbmodem11301` | **this board** |
| `00:00:5E:00:53:02` | `/dev/cu.usbmodem31101` | **LIVE dual-throttle controller — never touch** |

`esptool chip-id` is **not** an identity check: it resets the board, which is the harm being prevented.

### The build trap, and how to prove you avoided it

`-DCXC_C3_ST7796=1` is a CMake **cache variable**. It selects the branch in `cyd/src/CMakeLists.txt` and
**never reaches the compiler**; `platformio.ini` `build_flags` do not reach an ESP-IDF component compile
either. Every compile-time switch is a `target_compile_definitions(${COMPONENT_LIB} PUBLIC …)`.

Verify **before** flashing, with a negative control:

```bash
cd cyd && python3 -c "
import json; d=json.load(open('.pio/build/esp32_c3_st7796/compile_commands.json'))
c=lambda e: e.get('command') or ' '.join(e.get('arguments',[]))
for w in ['CXC_DEVICE_PREFIX','CXC_BUS_TRANSPORT_DEFAULT','CXC_CAN_LISTEN_ONLY=1',
          'CXC_CAN_TX_GPIO=5','CXC_CAN_RX_GPIO=6','CXC_CAN_BITRATE=500000']:
    print(w, sum(w in c(e) for e in d))
for w in ['CXC_C3_ST7796','CXC_BAND_HEIGHT','CXC_SPLASH_COLOR_BARS']:
    print('NEGATIVE', w, sum(w in c(e) for e in d))"
```

2026-08-12: all six positives present in **47** entries; all three negatives **0**.

> ⚠ **Grep for the define NAME, not name-plus-value.** String definitions appear in
> `compile_commands.json` as `-DCXC_DEVICE_PREFIX=\"c3lcd\"` with escaped quotes, so a search for
> `CXC_DEVICE_PREFIX="c3lcd"` returns 0 and the check lies in the direction of *looks broken*. That is
> the benign direction, but a check you learn to ignore is not a check.

`CXC_BAND_HEIGHT` reading 0 is the important one: this board must **not** get the full-frame tier.

## The hardware

### Panel — LCDWIKI MSP3525 / MSP3526, ST7796S

**Record both geometries or the next person re-derives which is which.**

| | value | where it comes from |
|---|---|---|
| **native** | **320 x 480 portrait** | `LCD_W` / `LCD_H` in the vendor `ST7796_Init.txt`; the controller's GRAM is exactly this |
| **shipped** | **480 x 320 landscape** | rotated by MADCTL's MV bit (`0x28`) |

Because GRAM and glass are the same size there is **no column or row offset in either
orientation** — unlike the C6's 172-of-240. `kC3LcdColOffset` / `kC3LcdRowOffset` are declared as `0`
rather than omitted, so the next reader can see the question was asked.

Landscape is deliberate and stays; the owner reviewed it — *"That's not a problem, per se."*


Wiring as built, owner-confirmed against the module's J2 silkscreen:

| C3 GPIO | J2 | label |
|---|---|---|
| GPIO4 | 7 | `SCK` |
| GPIO7 | 6 | `SDI(MOSI)` |
| GPIO10 | 3 | `LCD_CS` |
| GPIO3 | 5 | `LCD_RS` — this is **DC** (high=data, low=command) |
| GPIO1 | 4 | `LCD_RST`, active low, a **real** reset pin |
| GPIO0 | 8 | `LED` (backlight, active HIGH) |
| **5V** | 1 | `VCC` |
| GND | 2 | `GND` |
| — | 9 | `SDO(MISO)` unconnected — write-only panel |
| — | 10-13 | `CTP_*` unconnected — **touch disabled** |
| — | 14 | `SD_CS` unconnected, module's own 10K pull-up (R8) |

**VCC is 5V deliberately**: the module carries an XC6206 LDO and the manual states that feeding it 3.3V
loses the regulator's dropout and dims the backlight. Logic is 3.3V through a 74LVC245PW. SPI mode 0
(CPOL=0, CPHA=0), MSB first.

### Every pin is spoken for

```
GPIO0   LCD backlight      GPIO7   LCD MOSI
GPIO1   LCD reset          GPIO8   STRAPPING
GPIO2   STRAPPING          GPIO9   STRAPPING
GPIO3   LCD DC/RS          GPIO10  LCD CS
GPIO4   LCD SCLK           GPIO18/19  native USB (console + flashing)
GPIO5   CAN CTX            GPIO20/21  UART0
GPIO6   CAN CRX
```

Consequences worth stating rather than discovering:

- **Touch is off because there are no pins, not because the module lacks it.** An FT6336U is fitted.
- **This board cannot be UART-slaved while it drives glass and CAN.** GPIO20/21 are free only if the
  console is surrendered.
- ⚠ **GPIO2, GPIO8 and GPIO9 are boot-mode straps: nothing may hold them LOW at reset.** Beware anything
  driving a strap through an LED or a transistor (the fleet's C3 + BC337 finding). GPIO8 *also* drives
  the onboard LED and GPIO9 is the BOOT button, so both already carry a load.
- **GPIO21 is UART0 TX and the ESP32 ROM bootloader prints on it at 115200 at every reset**, regardless
  of the app's console config. That is a free instrument, not a nuisance: it proves TX, ground and baud
  on a new link with no firmware of yours involved.

### If touch is ever wanted — the boot-safe wiring

It works **with** the strapping pins rather than against them:

| C3 GPIO | module J2 | note |
|---|---|---|
| GPIO2 | `CTP_SDA` | strap — pull-up to 3.3V |
| GPIO8 | `CTP_SCL` | strap, also the onboard LED — pull-up to 3.3V |
| GPIO9 | `CTP_INT` | strap, also the BOOT button — pull-up to 3.3V |
| — | `CTP_RST` | tie to 3.3V, or omit |

**I2C idles HIGH, which is exactly what the strapping requirement is** — nothing may hold 2/8/9 low at
reset. A pulled-up idle-high bus *satisfies* the constraint instead of fighting it, which is why this is
the recovery path rather than "find three more pins".

**The driver already exists**: `cyd/src/touch_ft6336.cpp`, written for the Hosyond's FT6336G — same
family, I2C `0x38`, self-calibrating (it reports panel-native pixels, so there is no calibration step).
The remaining cost is that these three pins are only free if something else gives them up.

### There is no SD bus at all

`SD_CS` is unconnected and pulled up by the module. The classic-CYD hazard class — an SD teardown
resetting the DMA channel the display relies on, after which every SPI write still returns `ESP_OK` —
is *structurally* impossible here: there is no SD bus and no pin left to give one.

## Tier: band, and it is not a preference

`480 x 320 x 2 = 307,200 B`. The C3 has **321,296 B of DRAM total**, of which the linked image already
takes 150,904 B before any heap. A full framebuffer is not tight, it is impossible.

So there is deliberately **no `CXC_BAND_HEIGHT`** in this board's CMake branch and the 16-scanline
default stands: `480 * 16 * 2 = 15,360 B` of internal DMA-capable SRAM, 20 bands per frame, inside the
32-bit dirty mask. The boot log proves it:

```
Band-rendered surface: 480x320, band=16 scanlines, buffer=15360 bytes
Dirty quantum: 16 rows/band, 20 bands, repaint mode: band walk
```

This is the widest panel in the fleet on the smallest RAM budget — the interesting combination, because
width costs band buffer linearly while the tier decision is about the whole frame.

## No FPU — the scene, not the canvas, is the lever

RV32IMAC has no FPU and the band canvas is float-heavy. Measured on the C6 (same ISA class, same binary,
**only the scene changed**): dial gauges **892.9 ms/frame** vs `led_bar` rectangles **31.2 ms**. A 28×
swing from scene authoring alone.

`engine_thrust_480x320.compose.yaml` is therefore `led_bar` / `rect` / `label` only, and its label sizes
are native COMMP atlas sizes (12/14/16/20/24) so glyphs also skip the per-glyph scale multiply.

**Do not respond to this by de-floating the shared canvas.** Choose primitives.

## SPI clock — measured against the datasheet, and cross-checked

`ST7796S-Sitronix.pdf` serial AC characteristics: **TSCYCW 66 ns minimum → 15.15 MHz maximum write
clock** (TSCYCR is 150 ns, irrelevant — MISO is not wired).

The C3's GPSPI source is 80 MHz, so only exact dividers are reachable:

| divider | clock | verdict |
|---|---|---|
| 80/8 | **10.00 MHz** | shipped |
| 80/6 | 13.33 MHz | the fastest step still **inside** the datasheet |
| 80/5 | 16.00 MHz | already over spec |

10 MHz for first light because the panel sits on a breadboard behind a level shifter on dupont wire,
none of which is a controlled-impedance path.

**The clock is a measurement, not a claim.** `spi_device_get_actual_freq` reports the *achieved* rate
(requested and achieved are different numbers), and it is cross-checked against an independent quantity:
307,200 B at 10 MHz predicts 245.8 ms of transfer; the measured `[PERF]` xfer is **248.4 ms**, within
1.1%.

```
[PERF] full frame: 320 rows, draw 74.0 ms + xfer 248.4 ms = 322.3 ms
```

> **Transfer is 77% of a full repaint, so this board is wire-bound, not renderer-bound.** Per the
> RUNBOOK's dispatch test, `composeux-render-engineer` is the wrong tool here; the only lever is the SPI
> clock, which is a board constant. And the felt number is not this one — a throttle move repaints 1-2
> bands (~12 ms of SPI), not a frame.
>
> If 13.33 MHz is ever tried, change **one thing at a time** and remember the C6: at 40 MHz every machine
> signal improved while the glass got *worse*, updating infrequently with **no tearing**, because whole
> transactions were dropped rather than half-written and SPI writes are fire-and-forget.

## Colour — the part no instrument can check

The vendor init sequence (`ST7796_Init.txt`) is transcribed verbatim as a reviewable data table. Three
things about it matter:

- **MADCTL `0x28`** — the vendor's `LCD_direction()` case 1, `MV | BGR`. All four of their rotations set
  bit 3. We write the final value once in the table's `0x36` slot instead of writing `0x48` and
  overwriting it after DISPON; MADCTL is a plain register and the end state is identical.
- **`0x21` INVON**, deliberately, because it is IPS.
- **`0xF0` unlock/lock.** `C3`/`96` unlocks the extended command set, `3C`/`69` locks it. Every extended
  register the vendor writes sits between them. **Drop either half and the writes in between silently
  become no-ops** — a panel that initialises "successfully" with default gamma and power.
- **The endian register is never written**, so the reset default applies, and it matches the canvas's
  `rgb565_be`.

That last one **cannot be verified from a capture**, because `/capture.bmp` decodes the same band buffer
with the same big-endian assumption that wrote it. So the firmware carries a bring-up instrument:

### `CXC_SPLASH_COLOR_BARS` — default OFF, on for exactly one flash

Five labelled bands of pure RGB565 added to the **standard** ComposePlatform splash (logo and device id
untouched), held six seconds, then the boot continues automatically. Procedure and diagnostic arithmetic
are in `RUNBOOK.md`.

**Result for this panel, 2026-08-12, eyes on the glass:** David — *"I see the Compose logo on the screen
along with the color bars which are all correct."*

That one sentence settles both properties no instrument on this device can reach: RGB565 **byte** order
and colour **channel** order. Inversion is confirmed by the same look. The flag is now commented out in
the CMake branch, with the result recorded next to it.

> ⚠ `/capture.bmp` was returning a well-formed 460,854-byte image the whole time and would have looked
> equally perfect over a byte-swapped panel. The C6 lost an evening to exactly that.

### The backlight is not a signal

The module's LED line has a 10K pull-up into a BSS138 gate. The schematic says so in as many words:
*"LED unconnected OR driven HIGH, backlight always on" / "LED driven LOW, backlight off"*. So the panel
is lit at full brightness from the instant it has 5V, **before any code runs** — "the screen is lit"
proves the rail and nothing about the MCU.

The profile claims GPIO0 (LEDC, active HIGH) so brightness is *owned* rather than defaulted, and there
is **no clamp**. The C6 clamps to 50% because Waveshare publish a panel-lifetime directive for that
glass; this vendor's documented normal operating condition is the LED pin unconnected, i.e. 100%.
Copying the C6's limit would be cargo cult; inventing one here would be a number no evidence supports.

## CAN: listen-only, and it is a workaround

```
E bus_can: ================ LISTEN-ONLY MODE ================
E bus_can: THIS NODE WILL NEVER TRANSMIT AND NEVER ACK. …
E bus_can: THIS IS A WORKAROUND FOR A BROKEN TRANSCEIVER, NOT A DESIGN. …
```

**RECEIVE is proven on hardware.** 1,136 frames at 500 kbit/s, `bus_err=0`, correct arbitration id,
verified twice an hour apart.

**TRANSMIT is blocked by a dead transceiver driver** — the receiver is fine. Three independent
instruments agree:

1. the Pi's `bus-errors` counter **unmoved** across ten 1.5-second dominant attempts;
2. a differential of **−0.6 mV** across CANH/CANL while the driver input was held low;
3. **identical behaviour from two separate modules.**

And the drive signal was measured **clean at the chip's own leg** — a 3.25 V ↔ 0 square wave on pin 1 —
so the fault is *inside the part*, not upstream of it. The owner is soldering replacement SN65HVD230Ds
and will retest.

In **normal** mode this node tries to ACK, fails, accumulates TEC and reaches BUS-OFF in about five
seconds — after which ESP-IDF does not auto-recover (`bus_can.cpp` never enables `TWAI_ALERT_BUS_OFF`
and never calls `twai_initiate_recovery()`), so it stops *receiving* too. Listen-only never ACKs, never
accumulates, and listens indefinitely.

Discipline around it:

- `CXC_CAN_LISTEN_ONLY` defaults to **0** everywhere and is set in exactly **one** CMake branch.
- Every `publish_*` is **refused** with an ERROR, rate-limited to one line per 10 s (a per-frame publish
  path flooding the console at 115200 would delay the very draining it reports).
- It is NVS-overridable as `can.listen`.
- **Delete the definition once the replacement transceivers are soldered.**

### ⚠ The obvious escape hatch does not fit on this substrate

`_device/<id>/transport = "can listen=0"` is **13 bytes**. A classic-CAN state value is `[len][utf8…]`
in **eight** bytes — seven characters — so `publish_state` refuses it, correctly: a truncated string
decodes cleanly as a *different* command. A CAN-only board cannot be handed any wiring key at all; only
bare transport names fit (`"can"` 4 B, `"mqtt"` 5 B).

The first version of the boot banner promised the one-hop form anyway. **The real route out is two
hops:**

```
publish _device/c3lcd-047dd8/transport = "mqtt"          # 5 bytes, fits on CAN
reboot                                                    # now on MQTT
publish _device/c3lcd-047dd8/transport = "can listen=0"   # no 8-byte limit on MQTT
reboot
```

Or reflash without the define, or write NVS `can.listen=0` directly.

**Generalizes:** a control plane's vocabulary must be checked against the *narrowest* substrate it can
arrive on, not the widest.

## Where the scene comes from, and why it is HTTP

`config: http` — the scene is PUT to bus_storage and fetched over WiFi:

```bash
curl -X PUT --data-binary @engine_thrust_480x320.compose.yaml \
  http://192.0.2.75:8420/api/v1/assets/displays/c3lcd-047dd8.compose.yaml
```

Two alternatives were considered and rejected:

- **Config over CAN** is *blocked* by two defects found 2026-08-12: the terminating ACK is 13 bytes and
  classic CAN refuses anything over 8, and `BusMessage.to_json()` cannot serialise a config chunk's
  `bytes`. Not fixed here.
- **Compiling the scene in** would make this the first binary in the tree carrying composition, which is
  precisely the property `[CXC-R-BOOT-FUNGIBLE]` exists to protect, and it would cost a reflash per
  layout tweak while somebody is standing at the bench.

The honest cost: **this board is not yet a radio-free slaved node.** Only its *data* rides CAN. Keeping
the radio up is also what keeps `GET /capture.bmp` alive, and the boot log states the reason explicitly
rather than leaving it to be inferred:

```
WiFi REQUIRED — transport 'can' does NOT need an IP stack, config source 'http' needs one
```

## Bus vocabulary: zero configuration, and that is the result

This board needed **no bridge configuration at all**. It subscribes `throttle/left` (`0x03F6`) and
`throttle/right` (`0x0D9E`), which `CAN_BRIDGE_ALLOWLIST` already relays MQTT→CAN.

Contrast the headless CAN node on the *same silicon*: every one of its nine `_device/c3can-*` namespaces
is CAN-only and therefore unlearnable by the bridge's MQTT-observing registry — nine hand-typed
`CAN_BRIDGE_REGISTER` entries whose only error report is a 16-bit hash nobody can reverse.

**A new device on established namespaces is a new PEER, not a new vocabulary.** Search the fleet's
existing namespaces before inventing one.

## Proving CAN receive without disturbing live state

The acceptance test is the owner moving his levers. To prove the receive path *before* that — and
without publishing to a namespace the BSG world app helms on — a frame was injected on `can1` (the
independent witness channel) with `cansend`, a tool this project did not write:

```bash
# id computed in Python from [BP-CAN-ID]: ns_hash(16) | type(4) | src(9)
# _device/c3lcd-047dd8/config_source = 0x443A, type state = 0, src = 0x0A4
ssh display-node.local 'cansend can1 088740A4#0468747470'      # value = [len=4]"http"
```

The C3 answered:

```
I main: BUS _device/c3lcd-047dd8/config_source = 'http'
E bus_can: TX REFUSED — this node is in LISTEN-ONLY mode …
```

That proves the whole path in one shot — transceiver → GPIO6 → TWAI listen-only → arbitration-id decode
→ ns-hash resolution → value codec → `BusEvent` → the app shell's drain — *and* the listen-only refusal
firing on the ack it could not send.

A `_device/*` namespace was chosen over `throttle/*` **deliberately**: the bridge's registry has learned
the throttle names from MQTT, so a `throttle/left` injection would have been republished to MQTT and
would have moved the owner's live game state.

## Three heap and flash defects this bring-up paid for

### 1. The boot logo outlived its usefulness

An 8 KB scene failed to parse on a board with 128 KB free, and the reason was **ordering**.

The decoded boot logo is `W*H*2` = **51,200 B**, and `main.cpp` freed it *after* the scene was built — so
it was resident across the two largest transient allocations in the whole boot: the config fetch and the
`yaml_min` parse arena. Result: `config parse failed: yaml arena malloc failed`, on every retry,
forever, with the config fetching successfully each time.

Moving the free to just before the fetch loop:

```
Free heap before config fetch: 76,888 bytes (logo decode buffer still held)
boot logo decode buffer released — free heap now 128,108 bytes
```

and the same scene parsed first try. Both numbers are logged on every boot so the trade can be
re-checked rather than trusted. The cost is that a *retry* splash falls back to the ComposeUX wordmark;
the logo pixels already on the glass are unaffected, because freeing a decode buffer does not erase a
panel.

### 2. `yaml_min` sizes its arena from the raw length, comments included

```
arena = len*2 + 8192 + (len/8) * sizeof(YamlNode)      # YamlNode is 24 B on RV32
```

That is ~3.4 bytes of scarce **contiguous** heap per byte of prose, and comments are blanked to spaces
*after* the arena is allocated. A 9,803 B scene — a normal amount of commenting for this project — asks
for 57 KB; the same scene at 7,331 B asks for 45 KB and parses.

So scenes for no-PSRAM boards must be sparse on comments and the reasoning must live in the `.md`. That
inverts this project's usual habit, which is why the scene file says so in its own header.

Not fixed: computing the node budget from the *post-strip* content is a small change to a parser four
boards share, and three of them are not on the bench.

### 3. A full NVS partition starves everything, and the error names the wrong thing

`config_cache_store()` writes the **whole scene** into NVS on every successful fetch — boot and every
60 s re-poll. This board's scene is 8,139 B; the shared `partitions.csv` gives `nvs` 0x6000 = **24 KB**
(six pages) which also holds WiFi calibration. It filled and never recovered.

The message was benign-sounding and accurate:

```
W config_src: config cache write failed (ESP_ERR_NVS_NOT_ENOUGH_SPACE)
             — the board still runs, but `config: cached` has nothing fresh to load
```

**The damage was elsewhere.** A full NVS starves every *other* key, so `_device/<id>/transport` and
`_device/<id>/config_source` — the control planes whose entire purpose is re-pointing a board without a
reflash — silently stopped storing. Measured after a *complete* `esptool erase-region 0x9000 0x6000`, a
four-byte selection still failed:

```
E bus_select: nvs write failed (ESP_ERR_NVS_NOT_ENOUGH_SPACE) — selection NOT stored
```

Fixed for this board with a per-env `partitions_c3_st7796.csv` giving `nvs` 64 KB. Afterwards the cache
stored first try (`config cached: 8139 bytes, hash 0x0ECA`) and a transport selection injected over CAN
survived a reboot (`Bus transport: can (from nvs)`).

**Not fixed in shared code**, and the reason is a rule rather than a preference: bounding what
`config_cache_store` will write lands in a module four boards use, and three of them cannot be verified
today. Widening the shared `partitions.csv` is worse — it would move the NVS of every board in the fleet
and lose their calibration and selections on the next flash.

### 3b. …and the obvious partition fix boot-looped the board

Enlarging `nvs` in place at `0x9000` pushed `factory` from `0x10000` to `0x20000` — but PlatformIO still
wrote the app image at `0x10000`:

```
E esp_image: image at 0x20000 has invalid magic byte (nothing flashed here?)
E boot: Factory app partition is not bootable
E boot: No bootable app partitions in the partition table
```

Recovered by a normal reflash: the ROM bootloader is untouched and the board keeps enumerating, so this
is a **stall, not a brick**. The fix is the layout, not the tool — `nvs` now sits **after** the app at
`0x210000`, taking its extra 40 KB from the unused SPIFFS region, so `factory` stays at `0x10000` and
the image offset every tool assumes stays true. NVS has no required address; only the partition table is
authoritative.

## Failed experiments, with their numbers

Kept because each produced a confident, plausible, **wrong** picture.

| experiment | what it said | why it was wrong |
|---|---|---|
| First scene authored at the project's normal comment density (9,803 B) | the YAML was fine; `config_fetch` reported `-> 9803 bytes` every time and the parse failed after | the arena is sized from RAW length including comments, so the prose *was* the fault. The fetch success was real and irrelevant. |
| Trimming the scene to 7,331 B and calling it fixed | it parsed | it parsed by ~3 KB of luck. Adding 800 B of comments broke it again. The real constraint was the 51 KB logo buffer, and trimming had only moved the cliff. |
| `esptool erase-region 0x9000 0x6000` to fix the NVS errors | "Flash memory region erased successfully" | a fresh 24 KB partition still cannot hold an 8 KB blob plus WiFi data plus churn. The next boot re-filled it and a 4-byte write failed again. Erasing a too-small partition buys one write. |
| Growing `nvs` in place to 64 KB | the partition table was obviously correct and the build succeeded | it moved `factory` to `0x20000` while the flasher still wrote `0x10000`. Boot loop. A partition table is only correct *together with* the offset the tool writes at. |
| Reading `CXC_DEVICE_PREFIX="c3lcd"` out of `compile_commands.json` | **0 TUs** — apparently a dropped define, the exact trap being checked for | the quotes are escaped in the JSON (`-DCXC_DEVICE_PREFIX=\"c3lcd\"`). The define was present in 47 entries. A verification step that reports the failure it exists to detect, for the wrong reason, is worse than none. |
| `capture.bmp` as evidence of colour correctness | a well-formed 460,854-byte image, correct layout, correct-looking palette | it decodes the same buffer with the same byte-order assumption that wrote it. It agreed with the right answer here, and would have agreed just as confidently with a byte swap. |

## Re-running the acceptance

```bash
# terminal 1
cd cyd && pio device monitor -e esp32_c3_st7796

# terminal 2 — the independent witness (read-only, never the bridge's channel)
ssh display-node.local 'candump -td -x can1'
```

Then **move the physical throttle levers**. Expect `BUS throttle/left = <value>` on the console, a
`[REACT] 1 arrival(s) -> repainted N/20 bands` line, segments lighting bottom-up green → amber → red,
the numeric readouts leaving `---` for a number, and the **AHEAD FLANK** banner appearing once either
engine passes 230.
