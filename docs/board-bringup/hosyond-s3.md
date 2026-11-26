# Board bring-up: Hosyond ESP32-S3 2.8" IPS (ES3C28P)

**ILI9341V 240×320 IPS over SPI + FT6336G capacitive touch, ESP32-S3, 16 MB flash, 8 MB octal PSRAM.**

The one-line summary of this board: **it is the classic CYD's panel on the Qualia's MCU.** The display
controller is the ILI9341 family the CYD already drives, so the driver shape is that driver's shape; the
MCU is the S3-with-octal-PSRAM the Qualia build already targets, so the build config is that config. The
genuinely new work was the **FT6336G capacitive touch driver** — the first I²C touch controller in the tree.

Everything *above* the board seam — `main.cpp`, the canvas, the scene builder, every widget, the capture
server — is the CYD's, **unchanged**. A new board is a new profile, not a new rasterizer.

Bring-up date: 2026-08-11. Vendor kit:
`${DATASHEETS_HOME}/hoysond/2.8inch_IPS_ESP32-S3_ILI9341V_ES3C28P_ES3N28P_V1.0/`

---

## 1. Identity

| | |
|---|---|
| chip | ESP32-S3 (QFN56) rev v0.2, dual core + LP, 240 MHz, Xtensa LX7 |
| flash | 16 MB quad, 3.3 V per eFuse, `dio` mode |
| PSRAM | 8 MB **octal** @ 80 MHz — AP vendor, generation 3, 64 Mbit die (read off the live chip) |
| MAC / device id | this board's STA MAC (read passively; not written in public code) → `hosyond-<last 3 MAC bytes>` |
| port | `/dev/cu.usbmodem31101` — native USB (VID `0x303a` / PID `0x1001`) |
| panel | 2.8" IPS 240×320, ILI9341V, portrait |
| touch | FT6336G capacitive, I²C `0x38`, chip id `0x64` |

The vendor kit ships the schematic, an **IO allocation table** (`5-…_Schematic/…IO….xlsx`), the ILI9341V
init sequence, every component datasheet, and complete Arduino / ESP-IDF-LVGL / MicroPython demo sources.
The IO table is the authoritative pin map and every pin in `cyd/include/hosyond_s3_panel.h` is a row in it.
Read from the kit before inventing anything — but the demos are **reference, not architecture**; nothing
was ported from them.

### Pin map (from the vendor IO allocation table)

| function | GPIO |
|---|---|
| LCD CS / MOSI / SCLK / MISO | 10 / 11 / 12 / 13 (the S3's **native IOMUX FSPI** pins) |
| LCD DC | 46 (HIGH = data, LOW = command) |
| LCD backlight | 45 — **active HIGH** |
| LCD reset | **none** — tied to `CHIP_PU` (see §4.1) |
| Touch SCL / SDA | 15 / 16 — **shared with the ES8311 audio codec** |
| Touch INT / RESET | 17 / 18 — both active LOW |
| Audio I²S MCLK/SCLK/DOUT/LRCK/DIN | 4 / 5 / 6 / 7 / 8 |
| Audio amp enable (FM8002E) | 1 — active LOW |
| Battery voltage ADC (TP4054) | 9 |
| WS2812B RGB LED | 42 |
| SD SDIO CLK/D0/CMD/D1/D2/D3 | 38 / 39 / 40 / 41 / 48 / 47 |
| UART0 TX / RX | 43 / 44 |
| USB D− / D+ | 19 / 20 |
| BOOT button | 0 — **not brought out** |
| free general-purpose IO | **2, 3, 14, 21** |
| flash + PSRAM (unusable) | 26–37 |

> **Correction to the bring-up briefing:** the briefing lists free GPIO as 2/3/21. The IO table also frees
> **GPIO14** (brought out, no external device). GPIO 15/16 and 43/44 are *conditionally* free — only while
> touch+audio, respectively UART0, are unused.

Audio in and out, an RGB LED, SD and battery charging are all present and **none are claimed by this
profile**. They are recorded in `hosyond_s3_panel.h` because `[SPEC:CXC-HAL-ACTUATE]` will want them, and
because a silently-occupied pin is a bug waiting to happen.

---

## 2. Connect + flash

The Type-C port is the S3's **own native USB** — there is no onboard USB-serial bridge, so flashing and the
console share one port and `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` is not a preference here, it is the only
option.

```bash
cd cyd
pio run -e hosyond_s3 -t upload
pio device monitor -e hosyond_s3
```

Build wiring: `sdkconfig.defaults` → `sdkconfig.s3.defaults` → `sdkconfig.hosyond.defaults`, plus
`-DHOSYOND_S3=1` (a CMake cache var that switches `src/CMakeLists.txt` onto `boards/hosyond_s3.cpp` +
`touch_ft6336.cpp`). Partition table: `partitions.16mb.csv` (4 MB app, rest SPIFFS).

**Back up the factory image before the first flash.** The BOOT button is GPIO0 and is *not brought out*,
so there is no manual bootloader entry — recovery from firmware that breaks native-USB enumeration is not
straightforward. The as-shipped vendor image (arduino-lib-builder `bb76cb1`, IDF v5.4.1, built Mar 28 2025)
is preserved at:

```
docs/board-bringup/factory-images/hosyond-s3-factory-16mb.bin        # 16,777,216 bytes
docs/board-bringup/factory-images/hosyond-s3-factory-16mb.bin.sha256 # 7678d207ae…
# restore (UNGUARDED: manual; check identity first; run flash_guard.py check --port first): esptool.py --port /dev/cu.usbmodem31101 write-flash 0 <file>
```

Dumping 16 MB takes ~25 minutes. `--baud 921600` fails on this board ("chip stopped responding"); use the
default baud.

---

## 3. The rendering-tier call: bands or full framebuffer

`[CXC-BRINGUP]` §5 asks for this decision to be made deliberately. **Full framebuffer — one full-height
band.**

The CYD streams 16-scanline bands because it has no room for a frame. This board has 8 MB of PSRAM and a
frame costs 153,600 bytes.

The seam did **not** need widening: `DisplayDriver::transfer_band` already accepts a band of any height,
and one full-height band *is* the frame. What widened is the **backend** — `kBandHeight` in
`canvas_esp32.h` became a build-time constant (`CXC_BAND_HEIGHT`, default 16, unchanged for the CYD). That
is the sanctioned direction across the narrow waist: extend the backend, never fork a widget.

The win is **not** the SPI transfer — that moves identical bytes either way. It is that `render_bands`
walks the entire widget scene **once** per frame instead of 20 times.

Costs, recorded honestly:
- the rasteriser now writes into PSRAM rather than internal SRAM;
- dirty-repaint granularity coarsened to 1 band = the whole frame. That was a real regression against
  the CYD's per-band repaint, it *did* make the reactive tier feel sluggish, and it is **fixed** — see
  §3.1.

Confirmed on the live board:
```
canvas_esp32: band buffer (153600 bytes) placed in PSRAM
canvas_esp32: Band-rendered surface: 240x320, band=320 scanlines, buffer=153600 bytes
canvas_esp32: Dirty quantum: 16 rows/band, 20 bands, repaint mode: in-place row span (persistent frame buffer)
hosyond_s3:   frame buffer at 0x3c130974 is DMA-reachable — transmitting in place
```

### 3.1 The dirty quantum is NOT the buffer height

One band meant one dirty region, so every bus arrival re-rasterised and re-transferred all 153,600
bytes — **47.9 ms per arrival, measured**, whatever moved. The fix does not give the single scene pass
back up; it separates two things that were only accidentally the same number:

| constant | value here | meaning |
|---|---|---|
| `kBandHeight` | 320 | how much buffer exists |
| `kDirtyBandHeight` | 16 (everywhere) | repaint granularity |

The buffer **is** the frame and it **persists** between renders, so a repaint needs no band at all: it
re-rasterises the dirty **row span** in place at its true offsets — still one scene pass, with every
write gated by the canvas's active row window — and transfers only those rows. The seam again needed no
widening: `transfer_band` already documents `band_buf` as starting at `y_start`, so `_band_buf +
y0 * _width` is the whole trick. This is the `QualiaCanvas` `_limit_y0/_limit_y1` idiom ported to the
band canvas; that tier had already solved dirty-on-a-framebuffer. On the CYD the quantum *is* the band,
so nothing changes there.

Half the sluggishness was not in the renderer at all: the loop polled touch 10 × 10 ms **before** it
looked at the bus queue, so an arrival waited up to 100 ms with the repaint not yet started. A queued
arrival now ends that burst (one 10 ms tick always elapses, so the loop can never spin).

Measured on the live board, one binary, same injected 10 Hz throttle sweep. Both prior behaviours are
restorable for A/B via the commented-out `CXC_FORCE_FULL_REPAINT` / `CXC_LEGACY_POLL_BURST` in
`src/CMakeLists.txt` (**not** `platformio.ini` — §4.2):

| | before | after |
|---|---|---|
| repaint | 47.9 ms mean | 35.8 mean / 33.7 median |
| rows painted | 320/320 | 224/320 |
| bus→glass | 124.9 ms mean, 127.8 median, 167 p90 | 57.1 mean, **45.0 median**, 90 p90 |
| sustained cycle | 150 ms (≈6.7 Hz) | 60 ms (**≈17 Hz**) |

Proof it did not smear: after a sweep of dirty-only repaints, `GET /capture.bmp` is **byte-identical**
to a full render of the same state (0 of 230,400 pixel bytes). That check only became meaningful
because capture on this tier now streams the framebuffer **as-is** instead of re-rendering it — a
re-rendering capture reports what the scene *would* look like and would paper over precisely the
stale-region bug a dirty repaint can introduce.

**What is left, and why it was not taken.** A repaint is now ~70% SPI transfer, and that transfer is
wire-bound: 107 µs/row, 4.4 MB/s, 88% of what a 40 MHz clock allows. It scales with bytes sent and
nothing else. Two levers remain — an **X-range dirty rect** (the gauges are 56 px of 240, so 24.4 ms
would become ~5.7 ms with one engine moving, ~16.3 ms with both) and **80 MHz SPI** (halves it
outright). Both were deliberately deferred, for the same reason: a wrong X address window or a marginal
clock is invisible to every machine signal on this board — the framebuffer capture stays correct and
the SPI writes still return `ESP_OK`. The row-range transfer that *did* ship is not new panel
behaviour (the CYD clocks 20 partial-row transfers per frame), only new arithmetic on a proven shape.
These two want a human in front of the panel.

---

## 4. Gotchas / workarounds

### 4.1 There is no LCD reset GPIO — the panel's reset is tied to `CHIP_PU`

The defining hazard of this board. Resetting the ESP32 resets the panel, and *nothing else can*. The
vendor's `LCD_RESET()` in `ILI9341V_Init.txt` **does not apply**, and `init()` must never pulse a reset pin.

The subtlety worth internalising: a **cold** boot arrives with the panel already hardware-reset (`CHIP_PU`
fell), but `esp_restart()` does **not** reset it — a digital reset leaves the panel in whatever state the
previous run left it. So the init table opens with a **software** reset (`0x01`), which is an SPI command,
not a pin. That is the same substitution the classic CYD makes (`CYD_TFT_RST == -1`).

### 4.2 PlatformIO `build_flags` never reach ESP-IDF component compiles

**This one silently produced a wrong build and no warning of any kind.**

For `framework = espidf`, PlatformIO's `build_flags` are dropped before the IDF component compile. The
first build of this board carried `-DCXC_BAND_HEIGHT=320` in `platformio.ini` and rendered in **16-line
bands** anyway. Verified against `.pio/build/hosyond_s3/compile_commands.json`, where the only surviving
defines are `ESP_PLATFORM`, `IDF_VER` and the `SOC_*` pair.

Fix: `target_compile_definitions(${COMPONENT_LIB} PUBLIC …)` inside the board's branch of
`src/CMakeLists.txt`. The serial line `band=320 scanlines` is the proof that it landed — a dropped define
falls back to 16 and *everything still works*, which is exactly what makes it dangerous.

**This is not confined to this board.** `src/config_fetch.cpp` gated its device-id prefix on
`#ifdef QUALIA_DISPLAY`, a define supplied only as a `build_flag` — so this board's first boot announced
itself as `cyd-080739` and 404'd fetching `displays/cyd-080739.compose.yaml`. The prefix is now
`CXC_DEVICE_PREFIX`, set as a CMake definition, with the old `QUALIA_DISPLAY` fallback preserved verbatim
so no existing board's identity shifts. **`env:qualia_display` is worth auditing separately** — it passes
`-DQUALIA_DISPLAY` and `-DREACTIVE_TRANSFORM_ATTRS` the same dropped way.

### 4.3 This panel's init table is *not* the CYD's

Same controller family, materially different panel. Transcribed verbatim from the vendor's
`ILI9341V_Init.txt` into a reviewable data table in `hosyond_s3_panel.h`:

| | this board | classic CYD |
|---|---|---|
| power control 1 / 2 (`0xC0`/`0xC1`) | `0x13` / `0x13` | `0x23` / `0x10` |
| VCOM (`0xC5`/`0xC7`) | `0x22 0x35` / `0xBD` | `0x3E 0x28` / `0x86` |
| inversion | **`0x21` ON** (IPS) | off |
| MADCTL | `0x08` — portrait, **BGR** | `0x20` — MV set, RGB |
| gamma tables | vendor IPS curves | different |
| interface control `0xF6` | `0x01 0x30` | not sent |

Do not copy one panel's MADCTL or gamma to the other. The `BGR` bit compensates for the panel's *physical*
subpixel order so that standard RGB565 pixel data shows correct colours — confirmed by capture (the DANGER
LED renders red and the ACK button blue; a swap would exchange them).

### 4.4 SPI at 40 MHz on native IOMUX pins

GPIO 10/11/12/13 are the S3's native IOMUX pins for FSPI (`CS0`/`D`/`CLK`/`Q`), so `SPI2_HOST` takes the
fast direct route rather than the GPIO matrix — which is why 40 MHz is comfortable. The vendor's own demo
runs the same clock (80 MHz ÷ 2). Drop `kHosyondSpiFreqHz` to 20 MHz if the glass shows init or first-frame
corruption — that was the CYD's documented failure at 27 MHz.

DMA is `SPI_DMA_CH_AUTO`: the S3 uses GDMA, where the driver allocates channels, so the ESP32-era "pin
channel 1" idiom in `ili9341.cpp` does not apply. **The CYD's shared-DMA freeze class is also structurally
absent** — this board's SD card is on its own SDIO pins, not the display bus, and no SD bus is ever
initialized. Revisit if SD is ever enabled.

### 4.5 PSRAM is not tagged DMA-capable even where GDMA can reach it

The heap allocator does not mark PSRAM `MALLOC_CAP_DMA`, so whether the full-frame buffer is directly
transmittable is a **runtime** question. `HosyondDisplay` resolves it once at first transfer
(`esp_ptr_dma_capable` / `esp_ptr_dma_ext_capable`), logs the answer, and stages through an internal-SRAM
bounce buffer if the answer is no.

This is deliberate defence, not belt-and-braces: handing the DMA engine a pointer it cannot read is
*precisely* how you get `ESP_OK` over a dead panel. On this board the answer is yes — it transmits in place.

### 4.6 Capacitive touch means no calibration — only orientation

The FT6336G is self-calibrating and reports **panel-native pixels**, so `[CYD-TOUCH-CAL]`'s four-corner
raw-ADC calibration simply does not apply. All that remains is how the glass is mounted:
`kHosyondTouchSwapXY` / `InvertX` / `InvertY`, all defaulting to `false` for portrait.

The vendor's LVGL demo sets `SWAPXY` + an invert — but it runs the panel **landscape**, so those do not
carry over. The driver logs raw controller coordinates next to mapped ones on every touch, so **one corner
press says which flag to flip**.

### 4.7 The I²C bus is shared with the audio codec

The FT6336G and the ES8311 share GPIO 15/16. Harmless today because nothing initializes audio and this
profile claims the bus exclusively. The moment audio is enabled, that bus needs arbitration. Not a bug — a
scheduled collision.

---

## 5. Verify

Machine-verified on the live board (2026-08-11):

```
main: Board profile: hosyond-s3 (ES3C28P, ILI9341V 240x320 IPS, FT6336G touch)
main: Panel: 240x320 rect
main: Device id: hosyond-080739
hosyond_s3: ILI9341V ready — 240x320 portrait, MADCTL 0x08, inversion cmd 0x21, SPI 40000000 Hz, backlight ON
canvas_esp32: band buffer (153600 bytes) placed in PSRAM
canvas_esp32: Band-rendered surface: 240x320, band=320 scanlines, buffer=153600 bytes
main: Firmware build: Aug 11 2026 21:47:47
hosyond_s3: frame buffer at 0x3c130974 is DMA-reachable — transmitting in place
config_fetch: GET …/displays/default.compose.yaml -> 2355 bytes
compose_scene: scene 'CYD Dashboard (config-driven)': 10 widgets built
main: [CXC-R-C9] boot -> first config-driven frame: 4743 ms
ft6336: FT6336 up on SDA=16 SCL=15 @400000 Hz — chip 0x64 (0x64 = FT6336G), firmware 0x02, panel 0x11
main: Touch ready — [CXC-R-C9] responds from frame 1
```

`GET http://<ip>/capture.bmp` returns a well-formed 240×320 BMP (~230 KB) — unlike the Qualia's 720×720,
this size does not truncate, so captures are reliable here.

**And a capture is still not acceptance.** The canonical failure in this project was the classic CYD
rendering flawlessly — every SPI write ACKing `ESP_OK`, `capture.bmp` showing the framebuffer advancing —
over a panel frozen on frame 0. Three green machine signals, one dead display. A capture proves the
*framebuffer*; only eyes prove the *glass*.

**Human tier satisfied 2026-08-11 (David): "The screen is live and the touch interactions work fine."**
Panel lit, scene displaying, colour order and inversion correct on the glass, and the capacitive touch
mapping (identity — no swap, no invert) correct for this portrait orientation. Bring-up **accepted**.

---

## 6. Known issues / follow-ups

| | status |
|---|---|
| **Eyes on the glass** — backlight, colour order, inversion, build tag | ✅ **confirmed 2026-08-11 (David)** — "the screen is live" |
| **Touch mapping** — a press lands where the finger is | ✅ **confirmed 2026-08-11 (David)** — "the touch interactions work fine"; identity mapping is correct |
| Per-corner touch accuracy at the extremes | not swept separately; raw coords are logged if it ever matters |
| Long-run / thermal stability | minutes of uptime, not hours |
| SPI clock ceiling above 40 MHz | not explored; 40 MHz is vendor-proven and stable |
| Dirty-repaint granularity was the whole frame | ✅ **fixed 2026-08-11** — dirty quantum decoupled from buffer height (§3.1); bus→glass 125 → 57 ms mean |
| Repaint is now ~70% SPI transfer (107 µs/row, wire-bound) | X-range dirty rect and 80 MHz SPI both deferred — **glass-only failure modes**, need eyes (§3.1) |
| Touch is sampled once per pass under continuous bus traffic | trade taken for latency; ~45 ms between samples, far shorter than a real tap — **not yet exercised on a touch-heavy reactive scene** |
| `env:qualia_display` may be silently losing its `build_flags` defines | **worth auditing** (§4.2) |
| `boards/hosyond_s3.cpp` owns its ILI9341V driver rather than sharing `ili9341.cpp` | deliberate; deferred consolidation until both boards can be regression-tested together |
| Audio / RGB LED / SD / battery ADC | unclaimed, out of scope, pins recorded |
| MQTT dropped once with a write timeout at RSSI −79 | weak signal, auto-reconnected; not board-specific |

---

## 6.5 Operating mode as of 2026-08-12: CONFIG OVER THE BUS, RADIO OFF

**This board is not on WiFi.** It is the fleet's first node running `[SPEC:CXC-CONFIG-BUS]`:
transport `uart`, config source `bus`, and `wifi_init()` never runs. There is **no DHCP lease,
no ping, and no `GET /capture.bmp`** — that is the intended state, not a fault.

Two consequences to internalise before touching it:

1. **Rendering can only be confirmed by eyes.** The capture endpoint is gone. Every machine
   signal available — hash-verified transfer, `ESP_OK`, scene built, frame painted — proves the
   *bytes* and the *framebuffer*, never the glass. `[CXC-BOARD-HW-SELF-CONFIRMING]` gets sharper
   here, not softer.
2. **It is still fully reachable — over the bus.** `restart`, `transport` and `config_source`
   all work, and the board publishes `_device/hosyond-080739/status`. What is lost is pixel
   evidence, not telemetry.

### Talking to it

The Mac-side head is the UART bridge, which must be running for the board to get a scene:

```
PYTHONPATH=python nohup python3 -m services.uart_mqtt_bridge.bridge \
  --port /dev/cu.usbserial-<device-serial> --broker 192.0.2.75 \
  --ns throttle/left --ns throttle/right \
  --ns _device/hosyond-080739/config_source \
  --ns _device/hosyond-080739/transport \
  --ns _device/hosyond-080739/restart --ns _device/restart \
  --config-device hosyond-080739 --bus-storage http://192.0.2.75:8420 &
```

With the bridge down the board announces `NO CONFIG: asked N time(s)...` on its splash and backs
off to a 30 s retry. It recovers by itself when the bridge returns — no power cycle.

### Putting it back on WiFi

Two commands over the bus, no reflash and no cable — this is the whole point of the bootstrap-trap
answer (`_device/<id>/config_source` is settable over the *current* link):

```
publish _device/hosyond-080739/config_source = "http"     # radio back up, HTTP config, capture.bmp
publish _device/hosyond-080739/restart       = 1
```

Set `= "bus"` to convert it back. `= "cached"` boots it from the NVS copy with **no network and
no bus request at all** (607 ms to first frame) and paints a magenta `CACHED +Nb` badge in the
top-right — deliberately impossible to mistake for scene content, because a stale panel and a
working panel are otherwise identical.

⚠ An **unknown** strategy name is REFUSED at write time and nothing is stored. That is the belt
to the braces: the fatal-on-read path exists but should be unreachable.

### The one non-obvious constant

`kMaxPersist` (persistent, scene-swap-surviving subscriptions) is **8**, not 4. At 4 it was exactly
the size of the control plane and one short the moment `_device/<id>/config` was added, so the
board requested its scene, the head served all 25 frames, and every one was dropped as an
unregistered namespace hash. One WARN line at boot was the only symptom. If you add a control
namespace, check this number.

---

## 7. Machine-readable descriptor

`hosyond-s3.descriptor.yaml` — same schema as the cyd / qualia / esp32c3 / ch32v203 siblings. The
descriptor is the source; this recipe is the human view.
