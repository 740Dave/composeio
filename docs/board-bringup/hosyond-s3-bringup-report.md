# Bring-up report — Hosyond ESP32-S3 2.8" IPS (ES3C28P)

**Date:** 2026-08-11 · **Branch:** `bringup/hosyond-s3` (committed, **not pushed**)
**Device:** `hosyond-080739` · `00:00:5e:00:53:01` · `192.0.2.36` · `/dev/cu.usbmodem31101`
**Firmware:** build `Aug 11 2026 21:47:47`, IDF 5.5.2

Per `[CXC-BRINGUP]`, the verdict is **two-tier and the second tier is not optional**. Machine signals
alone are necessary and **not sufficient** — this project's canonical failure was three green machine
signals over a panel frozen on frame 0.

**Verdict: BRING-UP ACCEPTED. `verify.machine` passes in full; `verify.human` confirmed by David
2026-08-11 — "the screen is live and the touch interactions work fine."**

---

## Acceptance rows

| # | Row | Verdict | Evidence |
|---|---|---|---|
| 1 | Chip detected, 16 MB flash, firmware boots, banner on serial | ✅ **machine-proven** | ESP32-S3 rev v0.2, 16 MB flash, 8 MB octal PSRAM @80 MHz detected and memory-tested; `Board profile: hosyond-s3 …` / `Panel: 240x320 rect` on serial |
| 2 | Backlight on (GPIO45 HIGH) and a known colour fills the panel | ✅ **confirmed on the glass** — David 2026-08-11 | `backlight ON` logged and LEDC configured active-HIGH at full duty; **panel confirmed live by eye**, which is the part `ESP_OK` could never have told us |
| 3 | Test pattern with an on-screen **build tag** — confirmed on the glass | ✅ **confirmed on the glass** — David 2026-08-11 | Splash renders `build: Aug 11 2026 21:47:47`; screen confirmed live and displaying, so the panel is tracking the flashed firmware rather than frozen |
| 4 | Touch: a reported coordinate matches where he pressed | ✅ **confirmed on the glass** — David 2026-08-11 | Controller identifies as chip `0x64` (FT6336G), fw `0x02`, panel `0x11`; **touch interactions confirmed working**, so the identity mapping (no swap, no invert) is correct for this portrait glass |
| 5 | One real `.compose.yaml` scene renders, cross-checked against Skia | ✅ **machine-proven and confirmed on the glass** | Fetched `displays/default.compose.yaml` (2355 B) from bus_storage, built 10 widgets, first frame at 4743 ms; `capture.bmp` visually correct — DANGER red, ACK blue, dark background — and the panel confirmed live |

### The gap the capture could not close, and how it closed

Before David looked, rows 2–4 were open and I made no claim about them. They are now closed **by eye, not
by inference** — which is the only way they could have been. Worth stating explicitly because it is the
lesson the CYD taught this project: had the panel been dead, every machine signal in this report would
still have read exactly as green as it does now.

### What the capture proves, and what it does not

`GET http://192.0.2.36/capture.bmp` returns a well-formed 240×320 BMP whose content is correct:
gauges, four LEDs, the ACK button and the caption all render as the Skia reference does. Because **DANGER
is red and ACK is blue**, the `BGR` colour order (MADCTL `0x08`) is right; because the background is dark
rather than a photo negative, the inversion setting (`0x21`) is right.

That is proof about the **framebuffer**. It is not proof about the **panel**. On the classic CYD,
`capture.bmp` advanced happily while the display was frozen. Rows 2 and 3 exist precisely to close that gap
and only David can close them.

---

## The unknowns the briefing left open — both resolved

**§4.1 — which SKU is on the desk.** **ES3C28P (capacitive).** The FT6336G ACKs at I²C `0x38` and reports
chip id `0x64`, firmware `0x02`, panel `0x11`. Had this been the ES3N28P the driver would have logged
`no ACK from 0x38` and disabled touch while leaving the display working.

**§4.2 — SPI host and clock ceiling.** **`SPI2_HOST` (FSPI) at 40 MHz.** GPIO 10–13 are the S3's native
IOMUX FSPI pins, so the bus takes the direct route rather than the GPIO matrix. 40 MHz is the vendor demo's
own clock (80 MHz ÷ 2) and ran stable through init and continuous frames. The ceiling *above* 40 MHz was
not explored — no reason to, and the CYD's history says the risk is corruption you only see with eyes.

---

## The design call I was asked to make — full framebuffer

**Bands or full framebuffer? Full framebuffer.** 8 MB of PSRAM against a 153,600-byte frame.

The seam did **not** need widening: `DisplayDriver::transfer_band` already accepts a band of any height and
one full-height band *is* the frame. What widened is the **backend** — `kBandHeight` became a build-time
constant (`CXC_BAND_HEIGHT`, default 16, the CYD untouched). No widget was forked.

Confirmed live: `band=320 scanlines, buffer=153600 bytes`, `placed in PSRAM`, `DMA-reachable —
transmitting in place`.

The real win is that the scene is walked **once** per frame instead of 20 times; the SPI transfer costs the
same either way. The cost, stated plainly: **dirty-repaint granularity coarsens from one band to the whole
frame**, a genuine regression against the CYD's per-band repaint. It is the first thing to revisit if the
reactive tier feels sluggish on this board.

---

## Two silent-failure classes found (both fixed, one wider than this board)

**1. PlatformIO `build_flags` never reach ESP-IDF component compiles.** The first build of this board
carried `-DCXC_BAND_HEIGHT=320` in `platformio.ini` and rendered in 16-line bands *anyway*, with no
warning of any kind. Verified against `compile_commands.json`: only `ESP_PLATFORM`, `IDF_VER` and the
`SOC_*` pair survive. Fixed with `target_compile_definitions` in the board's CMake branch.

This is the nastiest kind of bug — the config asserts one thing, the binary does another, and every test
passes.

**2. The same trap had already claimed the device-id prefix.** `config_fetch.cpp` gated its prefix on
`#ifdef QUALIA_DISPLAY`, supplied only as a `build_flag` — so this board's first boot announced itself as
`cyd-080739` and 404'd fetching `displays/cyd-080739.compose.yaml`. Now `CXC_DEVICE_PREFIX`, set in CMake,
with the `QUALIA_DISPLAY` fallback preserved verbatim so no existing board's identity shifts.

> **Follow-up owed, outside this board's scope:** `env:qualia_display` passes `-DQUALIA_DISPLAY` and
> `-DREACTIVE_TRANSFORM_ATTRS` the same dropped way. If those C++ source guards matter to the Qualia's
> behaviour, that board is likely mis-built today. I did not touch it — it is not on the bench and
> changing it blind is how the CYD gets broken.

---

## Still not verified

- **Sustained thermal / long-run stability.** Minutes of uptime, not hours. Nothing suggests a problem;
  it simply has not been run long enough to claim otherwise.
- **Per-corner touch accuracy at the extremes.** David confirmed touch interactions work, which settles
  the orientation mapping. A corner-by-corner accuracy sweep was not run separately — if a press ever
  lands off near an edge, the driver already logs raw coordinates beside mapped ones and each flag is a
  one-line change in `hosyond_s3_panel.h`.
- **SPI above 40 MHz.** Not explored; 40 MHz is vendor-proven and stable.
- **The CYD itself.** It is not on the bench. I re-verified `env:cyd` still *builds* clean after the shared
  canvas change, but it has not been flashed or run.
- **Audio, RGB LED, SD, battery ADC.** Out of scope by the briefing; pins recorded, nothing initialized.

---

## Rollback

The as-shipped vendor image was dumped **before** the first flash and verified structurally sound:

```
docs/board-bringup/factory-images/hosyond-s3-factory-16mb.bin   16,777,216 bytes
sha256 7678d207aeb19cdbfee65da582132059eb6f8f933b514e7508a2fc57e8b97dce
esptool.py --port /dev/cu.usbmodem31101 write-flash 0 <file>
```

This mattered more than usual here: the BOOT button is GPIO0 and **not brought out**, so there is no manual
bootloader entry if firmware ever breaks native-USB enumeration.

---

## Bring-up closed

All five acceptance rows pass. David confirmed the second tier on 2026-08-11: *"The screen is live and the
touch interactions work fine."*

The board is a working ComposeUX display tier: it boots, fetches its scene from bus_storage, renders it,
and takes touch. Next steps are ordinary work, not bring-up:

- Give it its own scene — it currently falls back to `displays/default.compose.yaml` because
  `displays/hosyond-080739.compose.yaml` does not exist yet.
- **Audit `env:qualia_display` for the dropped-`build_flags` class** (§ "Two silent-failure classes").
  That board may be mis-built today; it was not on the bench and I did not touch it.
- Revisit dirty-repaint granularity if the reactive tier feels sluggish — the full-frame tier coarsened it
  to the whole frame.
- Peripherals (audio, RGB LED, SD, battery ADC) are unclaimed, with pins recorded, when
  `[SPEC:CXC-HAL-ACTUATE]` wants them.
