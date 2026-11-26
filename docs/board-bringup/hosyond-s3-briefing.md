# Bring-up briefing — Hosyond ESP32-S3 2.8" 240×320 IPS touchscreen (ES3C28P)

**For a fresh Claude instance with no prior context on this project.** Read this end to end before
touching the board. Everything below marked **measured** was read off the live device or extracted
from the vendor kit on 2026-08-11. There are no guessed pins in this document.

Vendor kit: `${DATASHEETS_HOME}/hoysond/2.8inch_IPS_ESP32-S3_ILI9341V_ES3C28P_ES3N28P_V1.0/`
It contains the schematic, the IO allocation table, every component datasheet, an ILI9341V init
sequence, and Arduino / ESP-IDF / MicroPython demos. **Read from it before inventing anything.**

---

## 1. The device — measured

```
port      /dev/cu.usbmodem31101      native USB (VID 0x303a / PID 0x1001), NO UART bridge chip
chip      ESP32-S3 (QFN56) rev v0.2 · Wi-Fi · BT 5 LE · dual core + LP core · 240 MHz
PSRAM     8 MB embedded (AP_3v3)      flash  16 MB quad, 3.3 V per eFuse
crystal   40 MHz                      MAC    00:00:5e:00:53:01
```

Currently flashed: a vendor factory image — `arduino-lib-builder`, version `bb76cb1`, IDF
`v5.4.1`, built Mar 28 2025. **Read the flash before erasing** if you want its driver hints.

Reproduce any of this — never trust a briefing over the device:
```bash
esptool.py --port /dev/cu.usbmodem31101 chip-id
esptool.py --port /dev/cu.usbmodem31101 flash-id
```

**The spec that decides the rendering tier: 8 MB PSRAM.** A 240×320 RGB565 frame is 153,600 bytes.
This board holds a full framebuffer, double-buffered, easily. The classic CYD could not — which is
*why* it renders in bands. See §5; decide deliberately.

---

## 2. Pinout — authoritative, from the vendor IO allocation table

Source: `5-…_Schematic/ESP32-S3…IO….xlsx`, cross-check against
`5-…_Schematic/2.8inch_ESP32-S3_Display_Schematic.pdf`.

### Display — ILI9341V over SPI
| signal | GPIO | note |
|---|---|---|
| CS | **10** | active LOW |
| MOSI (write) | **11** | |
| SCLK | **12** | |
| MISO (read) | **13** | |
| DC | **46** | HIGH = data, LOW = command |
| BACKLIGHT | **45** | **active HIGH** |
| RESET | — | **tied to `CHIP_PU`** — see the hazard below |

⚠ **There is no LCD reset GPIO.** The panel's reset shares the chip reset line: resetting the
ESP32 resets the panel, and nothing else can. Your `DisplayDriver::init()` **must not** try to
pulse a reset pin — the vendor's `LCD_RESET()` in `ILI9341V_Init.txt` does not apply to this
board. Init must be correct from a cold panel with no software reset available.

### Touch — FT6336G, capacitive, I²C
| signal | GPIO | note |
|---|---|---|
| SCL | **15** | **shared with the audio codec** |
| SDA | **16** | **shared with the audio codec** |
| INT | **17** | active LOW on touch event |
| RESET | **18** | active LOW |

Capacitive and self-calibrating — **unlike the CYD's resistive XPT2046, there is no calibration
step.** Registers: `4-…_DataSheet/FT6336G_Register.xlsx` and `D-FT6336G-DataSheet-V1.0.pdf`.

### Everything else on the board
| function | GPIO |
|---|---|
| Audio I²S: MCLK / SCLK / DOUT / LRCK / DIN | 4 / 5 / 6 / 7 / 8 |
| Audio amp enable (FM8002E) | 1 — active **LOW** |
| Audio codec ES8311 control | I²C on 15/16 (shared with touch) |
| Battery voltage ADC (TP4054 charger) | 9 |
| WS2812B RGB LED (single-wire) | 42 |
| SD card SDIO: CLK / D0 / CMD / D1 / D2 / D3 | 38 / 39 / 40 / 41 / 48 / 47 |
| UART0 TX / RX | 43 / 44 |
| USB D− / D+ | 19 / 20 |
| BOOT button | 0 (not brought out) |
| **Free general-purpose IO** | **2, 3, 21** |
| Flash + PSRAM (unusable) | 26–37 |

There is more on this board than a display: audio in and out, an RGB LED, SD, and battery
charging. **None of that is in scope for the first bring-up** — but record it in the descriptor,
because `[SPEC:CXC-HAL-ACTUATE]` will want it later.

---

## 3. What you are building into

ComposeUX renders the *same* declarative scene on desktop Skia, a browser, and an MCU, via a
**narrow waist**: every widget is defined once against a ~30-primitive `Canvas` contract plus a
`.compose.yaml`. Formats lower *to* the contract; backends are *extended to meet* it.

**A new board is a new PROFILE, not a new rasterizer.** If you find yourself editing a widget or
the canvas, stop — you have crossed the waist.

### The seam — read this first
`cyd/include/board_profile.h`
```
BoardProfile
  ├─ PanelGeometry   width / height + shape (rect | round)
  ├─ DisplayDriver   init / transfer_band / set_backlight
  └─ TouchDriver     init / read() -> calibrated TouchPoint (or null)
```
Exactly **one** profile is linked per build — compile-time selection. The generic `main.cpp`, the
canvas, the scene builder and the touch dispatcher never learn which board they are on.

### Your two precedents — read both
| file | board | take from it |
|---|---|---|
| `cyd/src/boards/classic_cyd.cpp` | ESP32 + **ILI9341** 240×320 rect + XPT2046 | **The display driver is nearly your driver** — same controller family, same geometry. Different pins, different SPI host, and its resistive touch does *not* apply. |
| `cyd/src/boards/qualia_rgb666.cpp` | **ESP32-S3** + RGB666 + PSRAM framebuffer | The S3 build config and PSRAM framebuffer handling. Its display architecture is unrelated to yours. |

**Your board is the CYD's panel on the Qualia's MCU.** Take the ILI9341 driver shape from one and
the S3 setup from the other. Existing S3 sdkconfigs: `cyd/sdkconfig.s3.defaults`,
`cyd/sdkconfig.qualia.defaults`.

The genuinely new work is the **FT6336G capacitive touch driver** — there is no I²C touch
precedent in the tree.

### Specs — normative
- `corpora/platform-specs/31-composeux-board-bringup.lts` — `[CXC-BRINGUP]`, the discipline and
  the two-tier verdict
- `corpora/platform-specs/29-composeux-embedded-reactive.lts` — `[CXC-ER-BOARDS]`, the board seam
- `corpora/platform-specs/27-composeux-peripheral-hal.lts` — the peripheral HAL, for the audio /
  LED / SD surfaces later

These are FactLattice `.lts`. **Query, don't read whole:** `lts_find(corpus="intelligence-console",
text="bringup")`, then `lts_node` for a body. If you author any `.lts`, you must run `lts_preview`
on it before writing it into a corpus directory — that is a ratified protocol, not a suggestion.

### Descriptors — copy the pattern
`docs/board-bringup/` holds a descriptor + narrative per board (`cyd`, `qualia`, `esp32c3`,
`ch32v203`). Yours: **`hosyond-s3.descriptor.yaml` + `hosyond-s3.md`**, same shape. The descriptor
is machine-readable and is simultaneously the documentation and the tool's input — define once,
never prose duplicating config.

---

## 4. Still unknown — the only two

1. **Which SKU is on the desk.** The kit covers `ES3C28P` and `ES3N28P`. The FT6336G pinout above
   is the capacitive-touch variant. Confirm the silkscreen, or confirm touch responds at all,
   before trusting the touch half.
2. **SPI host and clock ceiling.** Pins 10–13 map to the S3's FSPI signals; pick the host and a
   clock the panel tolerates. Start conservative (~20–40 MHz), raise it once the glass is stable.

---

## 5. A design call that is yours, made deliberately

**Bands or full framebuffer?** The CYD streams bands because it has no room for a frame. You have
8 MB PSRAM and need 150 KB.

`DisplayDriver` exposes `transfer_band`. Check whether the generic layer can be handed one
full-height band, or whether the seam needs widening. **If it needs widening, widen the BACKEND to
meet the contract — do not fork a board-specific widget.** Record the choice and the reason in the
descriptor.

---

## 6. The acceptance bar — two tiers, and the second is not optional

**`verify.machine[]`** — tests, serial `[PERF]`, SPI returning `ESP_OK`, a framebuffer that
advances. Necessary, **not sufficient.**

**`verify.human`** — eyes on the glass.

> The canonical failure, from this project: on the classic CYD the render loop was flawless, every
> SPI write ACKed `ESP_OK`, and `capture.bmp` showed the framebuffer advancing — **three green
> machine signals over a panel frozen on frame 0.** A downstream DMA-channel teardown had killed
> the display while the transport still ACKed. Only a human's eyes caught it.

**Minimum acceptance:**
1. Chip detected, 16 MB flash, firmware boots — your banner on serial
2. Backlight on (GPIO45 HIGH) and a known solid colour fills the panel
3. Test pattern with an **on-screen build tag** — David confirms on the glass
4. Touch: a reported coordinate matches where he pressed, corners included
5. One real `.compose.yaml` scene renders, cross-checked against the Skia reference

---

## 7. Hazards

- **`ESP_OK` is not "it worked."** The transport can ACK a dead panel.
- **No LCD reset line.** Init must work from cold with no software reset. Do not copy the vendor's
  `LCD_RESET()`.
- **I²C is shared** between the FT6336G and the ES8311 codec. Fine while audio is unused; the
  moment it is, that bus needs arbitration.
- **Backlight is active HIGH here.** Do not inherit a polarity from another profile.
- **Shared SPI DMA channels** caused a prior CYD freeze when SD and display shared a bus. This
  board has an SD slot on separate pins — if you enable it, check channel allocation.
- **Don't fork a widget to a backend.** Extend the backend instead.
- **Report what you could not verify.** Name which acceptance rows are machine-proven and which
  need David's eyes. Do not claim the latter.

---

## 8. Deliverables

1. `cyd/src/boards/hosyond_s3.cpp` + a panel-constants header (pins/timings/init bytes live in the
   header, as the precedents do)
2. A build config for this board, alongside the existing S3 ones
3. `docs/board-bringup/hosyond-s3.descriptor.yaml` + `hosyond-s3.md`
4. A bring-up report: what passed machine verification, what needs the glass, what you could not
   verify

Commit on a branch. **Do not push. Do not touch `.claude/`.**

---

## 9. Working notes

- `esptool.py` v5.1.0 is installed and on PATH.
- The S3 re-enumerates after reset, so `/dev/cu.usbmodem31101` can briefly vanish. Normal.
- The vendor README warns the Type-C port is **native USB only, no onboard USB-serial bridge** —
  which matches the scan. Flashing works over the native port.
- Vendor demos in `1-…_Demo/` (Arduino, ESP-IDF, MicroPython) are the fastest way to confirm a pin
  or an init byte. They are reference, **not** the architecture — do not port a demo into the tree.
