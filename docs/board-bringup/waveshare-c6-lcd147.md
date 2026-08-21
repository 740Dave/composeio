# Board bring-up: Waveshare ESP32-C6-LCD-1.47

**ST7789T 172×320 IPS over SPI, no touch, ESP32-C6 (RISC-V), 4 MB flash, no PSRAM.**

Third display architecture in the tree, and the first RISC-V board that actually drives glass — the
existing `esp32c3_dsl_test` env is a DSL-conformance build with no display. It is a **band-tier** board
like the classic CYD (no PSRAM), so it takes the 16-scanline path, not the Hosyond's full frame.

Everything above the board seam is the CYD's, unchanged.

Bring-up date: 2026-08-11. Vendor kit:
`~/Dropbox/Data Sheets/waveshare/esp32-c6-1.47inch-lcd/` (docs + `ESP32-C6-LCD-1.47-Demo.zip`, whose
ESP-IDF demo is the authoritative pin/init source).

---

## 1. Identity

| | |
|---|---|
| chip | ESP32-C6FH4 (QFN32) rev v0.1 — RV32IMAC, **single core** + LP core, 160 MHz |
| radios | Wi-Fi 6, BT 5 LE, 802.15.4 |
| flash | 4 MB embedded; `partitions.csv` (the CYD's 4 MB layout) applies unchanged |
| PSRAM | **none** |
| MAC / device id | base `AA:BB:CC:DD:EE:FF` → `wsc6-6f4ce4` |
| port | `/dev/cu.usbmodem11301` — native USB-Serial-JTAG, no bridge chip |
| panel | 1.47" IPS **172×320**, ST7789T, portrait, **no touch** |

### Pin map (from the vendor ESP-IDF demo)

| function | GPIO |
|---|---|
| LCD SCLK / MOSI | 7 / 6 |
| LCD CS / DC | 14 / 15 |
| LCD RESET | **21** — a real pin, active LOW |
| LCD backlight | 22 — active HIGH |
| SD CS / MISO | 4 / 5 — **MOSI+SCLK shared with the LCD** (see §4.3) |
| WS2812 RGB LED | 8 |

SPI clock is the vendor's 12 MHz. Resolution constants and the column offset come from
`main/LCD_Driver/ST7789.h`; the init byte table is transcribed from
`main/LCD_Driver/Vernon_ST7789T/Vernon_ST7789T.c`.

---

## 2. Connect + flash

```bash
cd cyd
pio run -e waveshare_c6 -t upload
pio device monitor -e waveshare_c6
```

Build wiring: `sdkconfig.defaults` → `sdkconfig.c6.defaults`, plus `-DWAVESHARE_C6=1`. The
`WAVESHARE_C6` branch in `src/CMakeLists.txt` **must sit above** the `if(NOT IDF_TARGET STREQUAL
"esp32")` early-return — that guard exists to keep the CYD's Xtensa-specific display sources out of
DSL-only RISC-V builds, and this is an `esp32c6` target that genuinely drives a panel.

Factory image dumped before the first flash (4 MB, ~1 min):

```
docs/board-bringup/factory-images/waveshare-c6-lcd147-factory-4mb.bin        # 4,194,304 bytes
docs/board-bringup/factory-images/waveshare-c6-lcd147-factory-4mb.bin.sha256 # a399266998…
```

---

## 3. THE performance fact: the C6 has no FPU

This is the thing to know about this board, and it is silicon, not a bug. The ESP32-C6 core is
**RV32IMAC — no floating-point unit**, so every float in the band canvas is software-emulated.

Measured on the live board, same binary, same canvas, only the *scene* changed:

| scene | primitives | draw / frame |
|---|---|---|
| stock `displays/default` (240-wide CYD dashboard) | two **dial** gauges — arcs, trig, per-pixel SDF | **892.9 ms** |
| narrow engine-thrust panel | `led_bar` (rectangles) + labels | **31.2 ms** |

**28×, decided entirely by which primitives the scene uses.** The practical rule for authors on this
board: prefer rect / label / `led_bar` geometry; avoid dial gauges, gradients, shadows and large arcs.
De-floating the canvas would be a large change for a board where choosing primitives is free.

After that change the bottleneck moves to the wire — see §4.4.

---

## 4. Gotchas / workarounds

### 4.1 The 34-column GRAM offset

The ST7789's GRAM is 240 columns wide; this panel shows **172** of them, starting at column **34**. Every
column address is biased by `kWsC6ColOffset` in `set_address_window()` and nowhere else.

`(240 − 172) / 2 == 34` — the glass is *centred* in GRAM, which is why the same offset is correct with or
without the MX mirror. Get it wrong and the entire image slides sideways. This is the single most likely
bring-up failure on an ST7789 of non-240 width.

### 4.2 172 is not 240 — scenes are authored to the board

First board in the tree whose `PanelGeometry.width` isn't 240. A scene written for the CYD or Hosyond
overflows; the tier does no scaling and says so:

```
design 240x320 != panel 172x320 — rendering 1:1 (no scaling on this tier)
```

Width is a **board** property. `engine_thrust_narrow.compose.yaml` is the worked example — a redesign,
not a rescale: bars narrowed 56→44 px, and the wiring caption and scale hint *deleted* rather than shrunk,
because at 172 px they could only be set small enough to be unreadable.

### 4.3 The SD card shares the display's SPI bus

Vendor `SD_SPI.h` reuses the LCD's MOSI/SCLK and adds MISO 5 / CS 4 — the same SPI2 bus. **This is the
hazard class that froze the classic CYD**: an SD teardown reset the DMA channel the display relied on, the
panel kept ACKing `ESP_OK`, and nothing updated again.

This profile never initializes an SD bus, so the display owns SPI2 outright and the bug is structurally
impossible. Init still drives SD CS HIGH so an inserted card cannot chatter on the shared lines. **If SD is
ever enabled here, design the DMA channel and bus arbitration first** — do not discover it.

### 4.4 SPI is transfer-bound at 11.43 MHz — and that is the ceiling

A 272-row dirty repaint is 97.5 ms, of which **67.9 ms is transfer**: 93,568 B at 11.43 MHz = 65.5 ms, so
the bus is essentially saturated. (The vendor's nominal "12 MHz" is really **80/7 = 11.43 MHz** — the C6's
SPI source is 80 MHz, so only exact dividers are reachable.)

**That is not headroom, it is the limit.** The ST7789 datasheet gives a serial write-cycle minimum of 66 ns
— about **15 MHz maximum write clock**. The vendor's choice sits just under it.

#### The 40 MHz experiment, and why it is recorded rather than deleted

Raising to 40 MHz (80/2) improved *every machine signal*:

| | 11.43 MHz | 40 MHz |
|---|---|---|
| transfer (272 rows) | 67.9 ms | 18.4 ms |
| dirty repaint | 97.5 ms | ~46 ms |
| bus→glass | ~103 ms | ~50 ms |

**And the display got worse** — David: *"it's updating very infrequently,"* with **no tearing**.

That symptom is the diagnosis. At 2.6x over the datasheet the panel does not latch reliably, so whole
transactions are **dropped** rather than half-written: nothing is torn because nothing partial arrives, and
the ESP32 ACKs regardless because SPI writes are fire-and-forget. Faster on the wire, less reactive on the
glass, every number green.

The bad reasoning that led there is worth naming: *"ST7789 parts commonly run 40-80 MHz."* True of some
modules with short traces and matched drive; **not a property of this part**, and the datasheet was the
authority the whole time. If revisited, 80/6 = 13.33 MHz is the only step still inside spec, and it buys
17% - not worth it.

### 4.5 A real reset pin, for once

Unlike the Hosyond (whose panel reset is tied to `CHIP_PU`), LCD reset is on GPIO21, so init does a genuine
hardware reset — correct from a warm `esp_restart()` as well as a cold boot.

### 4.6 The RAMCTRL endian bit — a glass-only bug

**The sharpest `verify.human` catch in this tree so far.** ST7789 `RAMCTRL` (`0xB0`) param 2 bit 3 is the
ENDIAN bit for 16-bit pixel data. The vendor ships `0xE8` — bit set, **little-endian** — because their LVGL
buffers are native little-endian `uint16`. This canvas emits RGB565 **big-endian** (`rgb565_be`, what the
ILI9341 boards want), so the panel byte-swapped every pixel. Fix: clear bit 3, `0xE8 → 0xE0`.

The symptom was diagnostic on its own:

| intended | RGB565 | byte-swapped | displayed |
|---|---|---|---|
| blue | `0x001F` | `0x1F00` | green |
| green | `0x07E0` | `0xE007` | red |

Blue→green and green→red is arithmetically a **byte** swap. A BGR *channel* swap would have shown blue as
**red**, and MADCTL would have been the suspect. The distinction turned a hunt into one bit.

**Why no machine signal caught it:** `capture.bmp` is decoded from the *same* band buffer with the *same*
big-endian assumption, so the capture rendered perfectly while the panel did not. The assumption was being
read back to itself. Only eyes can separate "correct" from "consistently wrong at both ends."

### 4.7 Backlight is capped at 50% — a hardware constraint

The Waveshare wiki: *"Keep the screen brightness at 50% or lower during use."* Sustained full brightness
shortens panel life. The vendor's own demo agrees — `LEDC_TEST_DUTY 4000` at 13-bit = **48.8%**.

`kWsC6BacklightDefault` is 124/255 (48.6%) and `kWsC6BacklightMax` = 127 is **enforced in
`set_backlight()`** with a warn-once log, not merely documented: a limit any future caller can exceed is
not a limit. Do **not** copy this to other profiles — the CYD's ~40% is a *looks* reason (TN washout); this
is a *lifetime* reason.

*(The first bring-up ran at 100% for about an hour. The evidence was in `ST7789.h`, already read, and simply
not divided out.)*

### 4.8 An unclaimed WS2812 is not an OFF WS2812

The RGB LED latches its last colour and holds it until given new data or power-cycled. A freshly reflashed
board still powered over USB therefore keeps the **factory demo's** LED burning — "this profile does not use
the RGB LED" does not mean dark. The profile now claims GPIO8 once at init, writes 24 zero bits over RMT
(bit-bang cannot hit the ~0.4/0.85 µs timing reliably), and deletes the channel to hand the pin back. On a
panel the vendor already wants run at ≤50% for heat and longevity, that matters beyond tidiness.

### 4.9 Vendor anomaly kept deliberately

The vendor's power-control-1 write (`0xD0`) declares two bytes `{0xA4, 0xA1}` but passes length **1**, so
their shipping firmware sends only `0xA4`. The datasheet wants two. We reproduce the **vendor's** behaviour,
because that is the configuration their board is known to run; if power/contrast artefacts appear, sending
both bytes is the first thing to try. Recorded in `waveshare_c6_panel.h`.

---

## 5. Verify

Machine-verified on the live board (2026-08-11):

```
main: Board profile: waveshare-c6-lcd147 (ESP32-C6, ST7789T 172x320, no touch)
main: Panel: 172x320 rect
main: Device id: wsc6-6f4ce4
ws_c6_lcd147: ST7789T ready — 172x320 portrait, MADCTL 0x48, inversion cmd 0x21, SPI 12000000 Hz, backlight ON
canvas_esp32: Band-rendered surface: 172x320, band=16 scanlines, buffer=5504 bytes
canvas_esp32: Dirty quantum: 16 rows/band, 20 bands, repaint mode: band walk
compose_scene: scene 'ENGINE THRUST': 9 widgets built, 6 reactive bindings (2 subscriptions)
main: [PERF] full frame: 320 rows, draw 31.2 ms + xfer 82.5 ms = 113.7 ms
main: [REACT] 1 arrival(s) -> repainted 17/20 bands = 272/320 rows in 97.5 ms; bus->glass 103.3 ms
```

`GET http://<ip>/capture.bmp` returns a correct 172×320 BMP of the engine-thrust panel with **no
horizontal shift** — the practical evidence that the 34-column offset is right.

**Use the BSG engine-power bars as the standard first scene for any board bring-up** (David, 2026-08-11),
in place of the stock dashboard. It is a better test article: it spans the full segment palette
(blue/green/amber/red), which is exactly what made the endian byte-swap legible as a *colour-specific
pattern* rather than a vague "looks off"; it exercises three binding forms; live hardware publishes to it
continuously; and it is cheap to draw, which matters on FPU-less silicon. A grey dashboard would have
hidden the bug this bring-up turned up.

**And that is still framebuffer, not glass.** A capture cannot distinguish "correct" from "consistently
offset in both the framebuffer and the readback"; only eyes can.

---

## 6. Known issues / follow-ups

| | status |
|---|---|
| **Eyes on the glass — ALL ROWS** | ✅ **confirmed 2026-08-11 (David)** |
| ↳ colours correct | after the RAMCTRL endian fix (§4.6) |
| ↳ brightness ~half, RGB LED dark | §4.7, §4.8 |
| ↳ no sideways shift | confirms the 34-column GRAM offset (§4.1) |
| ↳ reactive, "about as fast as the S3" | after reverting 40 MHz (§4.4) |
| ↳ splash build tag matches flashed firmware | the panel tracks the build, not a stale frame |
| SPI clock above 11.43 MHz | **tried, reverted** — 40 MHz drops transactions (§4.4); ~15 MHz is the datasheet ceiling |
| Dial gauges cost ~893 ms/frame here | silicon (no FPU); choose primitives instead (§3) |
| SD, RGB LED | unclaimed, out of scope, pins recorded |
| Touch | none fitted — `touch()` returns `nullptr` by design |

---

## 7. Machine-readable descriptor

`waveshare-c6-lcd147.descriptor.yaml` — same schema as the cyd / hosyond-s3 / qualia / esp32c3 /
ch32v203 siblings.
