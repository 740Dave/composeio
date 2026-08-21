# Adafruit Qualia ESP32-S3 RGB666 — bring-up recipe

Board: Adafruit Qualia ESP32-S3 RGB666 (product 5800).
Panel: Adafruit product 5793 — 4.0" round RGB TTL TFT, 720x720, NV3052C
driver, **no touchscreen**. Confirmed as the attached panel by David,
2026-07-10 (`cyd/include/qualia_panel.h` header comment).

All facts below are sourced from the `composeio` repo — file paths and
commits are cited inline. This is the third architecture the DSL corpus
has been proven on (LX6 classic ESP32 → RISC-V C3 → LX7 S3), and the
first "framebuffer tier" board in the ComposeUX Reactive Embedded stack
(`[SPEC:CXC-EMBED-REACTIVE]` R4/R5).

## 1. Identity

- **MCU**: ESP32-S3 (Xtensa LX7), `board = esp32-s3-devkitc-1` in
  `cyd/platformio.ini`.
- **Display architecture**: genuinely different from the classic CYD.
  The NV3052C panel has **no framebuffer of its own** — the S3's
  LCD_CAM RGB peripheral continuously DMA-scans a 720x720 RGB565
  framebuffer that lives in **PSRAM**, over a 16-bit parallel RGB666
  bus. The panel controller itself is configured once at boot over a
  bit-banged 9-bit SPI link routed through the board's PCA9554 I2C IO
  expander (`cyd/src/boards/qualia_rgb666.cpp`) — every MCU data pin is
  committed to the parallel bus, so there are no spare pins for a real
  SPI/QSPI panel link.
- **Memory**: octal PSRAM, 8 MB, 80 MHz (`CONFIG_SPIRAM_MODE_OCT=y`,
  `CONFIG_SPIRAM_SPEED_80M=y`), CPU at 240 MHz
  (`CONFIG_ESP_DEFAULT_CPU_FREQ_MHZ_240=y`) — both load-bearing, not
  just perf tuning: the scan-out DMA and the CPU compositor both hit
  the same PSRAM bus (`cyd/sdkconfig.qualia.defaults`).
- **Flash**: 4 MB (`board_upload.flash_size = 4MB`,
  `CONFIG_ESPTOOLPY_FLASHSIZE=4MB`).
- **Device id**: `qualia-<mac3>` — `qualia-%02x%02x%02x` from the last
  3 bytes of the STA MAC (`cyd/src/config_fetch.cpp:32-38`), prefixed
  differently from the CYD's `cyd-<mac3>` specifically so a dispatcher
  can tell the two board families apart on the same bus/bus_storage.
- **Touch**: none. `QualiaProfile::touch()` returns `nullptr`
  (`cyd/src/boards/qualia_rgb666.cpp`) — product 5793 has no
  touchscreen.

## 2. Connect + flash

- Native USB — the S3 enumerates over its native USB-Serial-JTAG, not
  a USB-UART bridge. Port glob: `/dev/cu.usbmodem*` (repo pins it to
  `/dev/cu.usbmodem31101` in `cyd/platformio.ini`, both
  `upload_port` and `monitor_port`).
- `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y` makes that same usbmodem port
  the primary console (`cyd/sdkconfig.s3.defaults`), so flashing and
  serial monitoring share one port.
- Build + flash:
  ```
  cd cyd && pio run -e qualia_display -t upload
  ```
  Monitor: `pio device monitor -e qualia_display` (115200 baud).
- **Known transient flash failure**: a native-USB CDC re-enumeration
  can throw `OSError [Errno 6] Device not configured` mid-flash. This
  is called out directly in-repo:
  > "(native-USB CDC — a transient re-enumeration may need one upload
  > retry, same as R4.)" — `cyd/platformio.ini`, comment above
  > `[env:qualia_display]`.

  **Retry once** — the second `pio run -e qualia_display -t upload`
  attempt succeeds. Same behavior noted for the R4 DSL-corpus env
  (`qualia_s3_dsl_test`) on the same physical port.
- Build wiring: `board_build.cmake_extra_args` layers
  `sdkconfig.defaults` → `sdkconfig.s3.defaults` →
  `sdkconfig.qualia.defaults`, plus `-DQUALIA_DISPLAY=1` (CMake cache
  var that switches `src/CMakeLists.txt` onto the Qualia sources —
  `qualia_main.cpp`, `canvas_qualia`, `boards/qualia_rgb666.cpp` —
  instead of the CYD's banded-SPI path).

## 3. The RGB panel tune

This is the load-bearing part of this recipe. Every panel constant
lives in **one file**, `cyd/include/qualia_panel.h`, by design — "If
the physical panel needs tuning ... this file is the one place to
change." All values below are copied verbatim from there and from the
R5 tuning commit `f84f048` (2026-07-10, David, eyes on the glass).

### Horizontal flicker → pclk 16 MHz → 10 MHz

```
kQualiaPclkHz = 10000000   /* was 16 MHz */
```
Root cause: at 16 MHz the DMA didn't have enough PSRAM bandwidth
headroom per scanline; dropping the dot clock to 10 MHz (yielding
~15 Hz refresh) fixed horizontal flicker. Commit `f84f048`: "pclk
16->10 MHz: kills horizontal flicker (PSRAM bandwidth headroom/line)."

### Vertical drift / black-flash → 20-line bounce buffer

```
kQualiaBounceBufferPx = 720 * 20   /* two ~29 KB SRAM bounce buffers */
```
Two symptoms, one fix family:
- **Black flash** (bounce=0, DMA reads straight from PSRAM at line
  rate): a bandwidth spike underruns the FIFO, `RESTART_IN_VSYNC`
  blanks a frame to recover.
- **Few-pixel vertical jump**: the (non-IRAM) bounce-buffer refill ISR
  is briefly suspended whenever a critical section holds off
  interrupts — notably the 1 Hz `uxTaskGetSystemState` CPU-load
  sampler used for on-device telemetry. If the refill falls behind,
  the frame rides up until it re-locks.

  10 lines (~0.8 ms slack) was borderline against that stall; **20
  lines (~1.6 ms) rides it out**. `720*20` divides `720*720` evenly
  (=36 bounce-buffer-fuls per frame) — chosen deliberately, per the
  inline comment in `qualia_panel.h`.

This bounce buffer **requires** `CONFIG_LCD_RGB_ISR_IRAM_SAFE=n` (next
item) — the two are a matched pair, not independent knobs.

### `CONFIG_LCD_RGB_ISR_IRAM_SAFE=n`

`cyd/sdkconfig.qualia.defaults`:
```
CONFIG_LCD_RGB_ISR_IRAM_SAFE=n
CONFIG_GDMA_CTRL_FUNC_IN_IRAM=y
```
The bounce-buffer refill happens inside the RGB panel ISR and reads
from the PSRAM framebuffer. PSRAM is unreachable when cache is
disabled (e.g. during flash writes). With `ISR_IRAM_SAFE=y` that
refill faults during a flash op → **Guru Meditation (Cache error)
reboot loop** — documented in-repo as "the documented Cache-error
combo — hence =n." The tradeoff (ISR paused during flash ops) is
covered by `RESTART_IN_VSYNC` below and is invisible on a steady
display.

### `CONFIG_LCD_RGB_RESTART_IN_VSYNC=y`

Resyncs the RGB DMA at every VSYNC. Belt-and-suspenders on top of the
bounce buffer: it recovers cleanly from the rare refill stall during
an actual flash write, and (per commit `6137a49`) it's also what makes
the R5 zero-copy double-buffer swap presentable — the driver restarts
scan-out from the new `cur_fb_index` at vsync after a pointer swap.

### Supporting sdkconfig (why they're needed, not just what)

- `CONFIG_SPIRAM=y` / `_MODE_OCT=y` / `_SPEED_80M=y` — octal PSRAM at
  80 MHz for the ~1 MB framebuffers (panel scan-out fb + canvas back
  buffer at RGB565, 720×720×2 bytes each).
- `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL=16384` — keeps small
  allocations off PSRAM (latency-sensitive RTOS structures stay in
  internal SRAM).
- `CONFIG_FREERTOS_USE_TRACE_FACILITY=y` +
  `CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS=y` +
  `CONFIG_FREERTOS_RUN_TIME_STATS_USING_ESP_TIMER=y` — the display
  meters its **own** CPU load onto the demo `sys/cpu` gauge via
  `uxTaskGetSystemState`; this is the same 1 Hz sampler implicated in
  the vertical-jump root cause above (all in
  `cyd/sdkconfig.qualia.defaults`).

### num_fbs=2 zero-copy swap (animated tier, commit `6137a49`)

For the animated DRADIS radar scene, the board config moved from
`num_fbs=1` to `num_fbs=2` (`cyd/src/boards/qualia_rgb666.cpp`,
`cfg.num_fbs = 2`): the driver owns two PSRAM scan-out buffers, the
canvas composites into the back one, and "presenting" is a pointer
swap (`esp_lcd_panel_draw_bitmap` called with a pointer that **equals**
one of the driver's own framebuffers — esp_lcd's documented zero-copy
path) plus a one-vsync wait (`s_vsync_sem`) so the old front buffer is
guaranteed done scanning before it's reused as the next back buffer.
No PSRAM→PSRAM flush copy, no tearing.

**Honest ceiling** (commit `6137a49`, hardware-measured): 2.4 → 3.3 fps
for the full animated sweep scene, busiest-core CPU 99% → 85%, zero
tearing/vsync-timeout/panics. The tier is **PSRAM-bandwidth bound**:
scan-out DMA contends with CPU writes at ~11 MB/s effective; background
restore memcpy (~93 ms) + beam alpha-blend (~123 ms) dominate frame
time, so full-disc animation tops out **~10-12 fps regardless** of
further code tuning. Accepted as a proof-of-concept ceiling (David,
2026-07-10, "option C"); further gains would need regional restore + a
shorter trail, or leaning into a low-fps radar-afterglow aesthetic
rather than fighting the bandwidth wall. Non-animated/static scenes are
unaffected — they use the unchanged R3 composite+flush path.

## 4. Verify

On a clean boot, serial (115200 baud, same usbmodem port) should show,
in order (`cyd/src/qualia_main.cpp`, `cyd/src/boards/qualia_rgb666.cpp`):

```
ComposeIO generic display firmware (R5 framebuffer tier)
Board profile: qualia-s3-rgb666 (NV3052C 720x720 round, Adafruit 5793)
Panel: 720x720 round
Device id: qualia-<mac3>
...
RGB panel up: 720x720 @ <N> Hz pclk 10.0 MHz (fb in PSRAM)
...
Capture server: http://<ip>/capture.bmp
```

Plus **eyes on the round glass**: the boot sequence paints a color
wheel with the device id and connection status before any network
dependency, so panel geometry/colors/orientation are verifiable even
with zero network config (`qualia_main.cpp`: "PANEL BRING-UP frame:
the color wheel proves the panel before any network dependency").

**Caveat — do not rely on full framebuffer HTTP captures for
verification.** The capture server streams a 24-bit BGR BMP
(`cyd/src/capture_http_fb.cpp`) of a 720×720 frame — 720×720×3 ≈
1.5 MB. In practice this size **truncates over HTTP** (client sees
`IncompleteRead`) rather than completing cleanly. Treat the serial log
line (`RGB panel up: 720x720 ...`) plus a direct look at the panel as
the source of truth for hardware bring-up; don't gate verification on
a full `capture.bmp` pull succeeding.

## 5. Known issues / follow-ups

- **Capture-server robustness for the large framebuffer**: the
  `/capture.bmp` endpoint (`cyd/src/capture_http_fb.cpp`) was built for
  a ~1.5 MB BMP and reliably truncates over HTTP (`IncompleteRead`) —
  needs either chunked/streamed transfer that the client can actually
  consume to completion, a compressed/scaled-down capture format, or a
  documented max-size caveat surfaced to callers. Until fixed, treat
  captures as best-effort, not a bring-up gate.
- **Animated-scene fps ceiling (~10-12 fps)** is an accepted PSRAM-
  bandwidth limit for the current full-composite restore approach
  (commit `6137a49`), not a bug — flagged here so it isn't
  re-investigated as a regression. Real fix path: regional dirty-rect
  restore instead of full-background memcpy, or a shorter/lower-fps
  trail aesthetic.
- Panel tune values (pclk, porches, bounce buffer size) are the
  **only** place to retune for a different physical panel revision —
  `cyd/include/qualia_panel.h`. Do not duplicate/hand-edit panel
  constants elsewhere; the renderer, scene builder, and capture server
  are all parameter-driven off this one file.

## 6. Machine-readable descriptor

```yaml
board:
  id: qualia-s3-rgb666
  vendor: adafruit
  product_id: 5800          # Qualia ESP32-S3 RGB666
  panel_product_id: 5793    # NV3052C 720x720 round, no touch
  chip: esp32-s3
  arch: xtensa-lx7
  ram: octal_psram_8mb_80mhz
  flash: 4MB
  device_id_pattern: "qualia-%02x%02x%02x"   # last 3 STA MAC bytes

flash:
  tool: esptool               # via PlatformIO/esp-idf
  build_dir: cyd
  flash_cmd: "pio run -e qualia_display -t upload"
  monitor_cmd: "pio device monitor -e qualia_display"
  monitor_baud: 115200
  port_glob: "/dev/cu.usbmodem*"
  transport: native_usb_serial_jtag
  flash_retry:
    trigger_error: "OSError [Errno 6] Device not configured"
    cause: transient_native_usb_cdc_reenumeration
    action: retry_once
    known_second_attempt: succeeds

verify:
  serial_token: "RGB panel up: 720x720"
  boot_log_sequence:
    - "ComposeIO generic display firmware (R5 framebuffer tier)"
    - "Board profile: qualia-s3-rgb666 (NV3052C 720x720 round, Adafruit 5793)"
    - "Panel: 720x720 round"
    - "Device id: qualia-<mac3>"
    - "RGB panel up: 720x720 @ <N> Hz pclk 10.0 MHz (fb in PSRAM)"
  human_check: eyes_on_round_glass_for_geometry_color_orientation
  full_framebuffer_capture: unreliable   # see quirks.capture_truncation

panel_tune:
  source_file: cyd/include/qualia_panel.h
  pclk_hz: 10000000            # was 16000000; horizontal-flicker fix
  refresh_hz_approx: 15
  bounce_px: 14400              # 720px * 20 lines; two ~29KB SRAM bounce buffers
  bounce_buffer_rationale: "rides out ~1.6ms ISR stalls (1Hz CPU-load sampler); divides 720x720 evenly"
  sdkconfig_flags:
    - name: CONFIG_LCD_RGB_ISR_IRAM_SAFE
      value: "n"
      rationale: "PSRAM-fb bounce refill in ISR is incompatible with IRAM_SAFE=y (Cache-error reboot loop during flash ops)"
    - name: CONFIG_LCD_RGB_RESTART_IN_VSYNC
      value: "y"
      rationale: "resyncs RGB DMA at vsync; backstop for bounce refill stalls; required for zero-copy num_fbs=2 swap presentation"
    - name: CONFIG_SPIRAM_MODE_OCT
      value: "y"
      rationale: "octal PSRAM required for framebuffer DMA bandwidth"
  num_fbs: 2
  num_fbs_rationale: "zero-copy pointer-swap scan-out for animated scenes (commit 6137a49); 2.4->3.3fps, 99%->85% busiest-core CPU"
  fps_ceiling_animated: "10-12 fps, PSRAM-bandwidth bound (~11MB/s effective), accepted PoC limit"

quirks:
  - id: native_usb_flash_errno6
    description: "transient native-USB CDC re-enumeration during flash; retry upload once"
  - id: capture_truncation
    description: "720x720 24-bit BMP (~1.5MB) over /capture.bmp truncates (IncompleteRead) over HTTP; rely on serial + eyes, not full captures"
  - id: touch_not_present
    description: "Adafruit 5793 panel has no touchscreen; TouchDriver* is nullptr for this board profile"
  - id: panel_no_own_framebuffer
    description: "NV3052C is TTL/RGB only, no internal GRAM; LCD_CAM DMA continuously scans a PSRAM framebuffer, panel controller config-only over bit-banged SPI via PCA9554 expander"
```
