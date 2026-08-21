# Board bring-up: classic CYD (ESP32-2432S024)

Sources: `cyd/platformio.ini`, `cyd/include/cyd_pins.h`, `cyd/src/ili9341.cpp`,
`cyd/src/main.cpp`, `cyd/src/boards/classic_cyd.cpp`, `cyd/src/config_fetch.cpp`,
`cyd/src/capture_http.cpp`, `cyd/src/canvas_esp32.h`, and commits `9a35baf`
("CYD fix: SD-bus DMA teardown was killing the display (sprite freeze)"),
`cfa313a` ("CYD: remove the SD card + push-capture dependency entirely"),
`6137a49` (R5 render-path sweep, referenced only for the shared `[PERF]`/render
lineage — not CYD-specific).

## 1. Identity

- **Chip**: ESP32 (classic, Xtensa LX6), no PSRAM (`cyd/platformio.ini`
  comment: "WiFi memory optimization — CYD has no PSRAM").
- **Board**: CYD — "Cheap Yellow Display", ESP32-2432S024
  (`cyd/src/boards/classic_cyd.cpp`, `cyd/platformio.ini` header comment).
  This is the reference/floor board profile for ComposeUX: "the performance
  FLOOR of the whole ComposeUX system — what renders acceptably here is
  universally deployable" (`classic_cyd.cpp`).
- **Display**: ILI9341, 240x320, rectangular, on the HSPI bus (SPI2_HOST).
  `ClassicCydProfile::geometry()` returns `{CYD_SCREEN_W=240, CYD_SCREEN_H=320,
  PanelShape::Rect}`.
- **Touch**: XPT2046 resistive touch, sharing the display's SPI2 bus as a
  second `spi_master` device (hardware CS, GPIO33) — not bit-banged (see
  §4.3).
- **Rendering**: band-rendered, not full-framebuffer. `canvas_esp32.h`:
  `kBandHeight = 16` — the 320-row scene is rendered in 20 bands of a
  7,680-byte band buffer (clear → draw widgets clipped to the band's Y range
  → DMA transfer), not a full 240x320 framebuffer in RAM (consistent with
  "no PSRAM").
- **Device id pattern** (`cyd/src/config_fetch.cpp::device_id()`): if
  `DISPLAY_DEVICE_ID` is defined at build time, that literal string is used;
  otherwise it's derived from the last 3 bytes of the STA MAC:
  `snprintf(s_id, "cyd-%02x%02x%02x", mac[3], mac[4], mac[5])` — e.g.
  `cyd-a1b2c3`. (The sibling Qualia/S3 tier uses the `qualia-` prefix from the
  same function, guarded by `#ifdef QUALIA_DISPLAY`, so a dispatcher can tell
  board families apart by id alone.)

## 2. Connect + flash

The CYD exposes a CH340 USB-serial adapter, enumerating as
`/dev/cu.usbserial-*` on macOS (the `[env:cyd]` block in `platformio.ini` pins
`upload_port = /dev/cu.usbserial-3120` / `monitor_port = /dev/cu.usbserial-3120`
as the last-known port — adjust to whatever `/dev/cu.usbserial-*` the board
enumerates as on your machine).

Framework is ESP-IDF (not Arduino) via PlatformIO:

```
cd cyd && pio run -e cyd -t upload
```

- Flashing goes through esptool (PlatformIO's `espressif32` platform); the
  CH340's auto-reset circuitry drives the ESP32's EN/GPIO0 for you, so
  esptool auto-enters the ROM bootloader — **no manual button dance**.
- Serial monitor / logging: **115200 baud** (`platformio.ini`:
  `monitor_speed = 115200`), same command family:
  `pio device monitor -e cyd` (or add `-t monitor` to the upload invocation).
- Flash geometry from `[env:cyd]`: `board_build.f_flash = 80000000L`,
  `flash_mode = qio`, `board_upload.flash_size = 4MB`,
  `board_build.partitions = partitions.csv`.
- SDK defaults are layered from `sdkconfig.defaults`
  (`board_build.cmake_extra_args = -DSDKCONFIG_DEFAULTS=.../sdkconfig.defaults`).

## 3. Config source (no SD)

The firmware is fungible — it ships with zero baked-in composition
(`cyd/src/main.cpp` header comment, `[CXC-R-BOOT-FUNGIBLE]`). At boot it:

1. Brings up WiFi (`wifi_init()`).
2. Starts the bus/MQTT client and the on-device capture HTTP server.
3. Fetches its scene config over HTTP from bus_storage:
   `bus://assets/displays/<device_id>.compose.yaml` is rewritten by
   `http_fetch()` (`config_fetch.cpp`) to
   `{BUS_STORAGE_URL}/api/v1/assets/displays/<device_id>.compose.yaml` and
   GET'd (`strncmp(uri, "bus://assets/", 13) == 0` → `snprintf(url, ...,
   "%s/api/v1/assets/%s", BUS_STORAGE_URL, uri + 13)`).
4. Retries every 5s until a scene is obtained (device is useless without one)
   — `main.cpp`: `"No config/scene — retrying in 5 s"`.
5. Re-polls the same URL every **60s** thereafter:
   `static constexpr int64_t kConfigPollSec = 60;` (`main.cpp`). Note the
   in-code comment: `[CXC-R-BOOT]` step 12 nominally specifies 5 min; 60s is
   used here to make config-swap turnaround practical while this tier is a
   PoC.
6. On a changed config, swaps the scene under the render lock; on unchanged
   content it's a no-op (`try_fetch_scene()` diff-checks the fetched bytes
   against the previous config).

There is **no SD card involved anywhere in this path** — config and assets
come over WiFi from bus_storage only (see §4.1 for why the SD card was
removed entirely).

## 4. Gotchas / workarounds

### 4.1 The SD-vs-display shared-SPI-DMA-channel hazard (fixed, then eliminated)

**Root cause** (commit `9a35baf`, hardware-confirmed by staged bisection):
the classic ESP32 exposes only **two SPI DMA channels** total, shared across
its SPI hosts. The CYD's display lives on SPI2/HSPI and its SD card lived on
SPI3/VSPI; both requested a DMA channel via `SPI_DMA_CH_AUTO`. The dev-only
`capture_and_push()` path ran `sd_init → ... → sd_deinit → spi_bus_free(SPI3)`
on every boot *and* on every 60s config re-poll. That `spi_bus_free()`
teardown reset DMA/peripheral state the display's SPI2 depended on. Symptom:
after the first capture, the panel kept **ACKing SPI writes (`ESP_OK`) but
displaying nothing new** — the visible scene appeared frozen on frame 0 while
the render loop and framebuffer kept advancing normally underneath. Pins were
never the issue (SD pins 18/19/23/CS5 are disjoint from HSPI 12/13/14/15).

The bisection that localized it: disabling touch still left it frozen (touch
exonerated); forcing fresh capture-driven panel flushes produced zero glass
movement (panel not accepting GRAM writes); disabling the SD/VSPI path made
the animation return (culprit confirmed).

**First fix** (`9a35baf`): explicit, non-overlapping DMA channels —
`ili9341.cpp::init_spi()` pins the display to `spi_bus_initialize(SPI2_HOST,
&bus_cfg, 1 /* DMA ch */)`; `sd_card.cpp` pinned the SD bus to channel 2.
`sd_card.cpp` was also changed to init the VSPI bus **once** (`s_bus_up`
guard) and never call `spi_bus_free(SPI3)` again — `sd_deinit()` only
unmounted the FAT vfs, leaving the bus up, which also killed the 60s-repoll
re-trigger of the bug.

**Final fix** (`cfa313a`, follows immediately): the push-capture feature
(render → BMP → HTTP PUT to document_store) was recognized as a **dev-time
image-similarity harness with no production purpose** and was the *sole*
user of the SD card / second SPI bus — so it was deleted outright
(`capture.cpp`, `sd_card.cpp`, and their headers removed; call sites in
`main.cpp` removed). This makes the whole shared-DMA-channel bug class
**structurally impossible**: the display now owns the only SPI bus on the
board. **The CYD needs no SD card** — a deliberate simplification for the
open-source release. `ili9341.cpp::init_gpio()` still drives `CYD_SD_CS`
(GPIO5) HIGH defensively at boot, in case a card is physically seated in the
slot, so it can't interfere with the (now sole) SPI bus — see the `[TF7]`
comment in `cyd_pins.h`.

### 4.2 Display SPI clock: 27 MHz → 20 MHz

`cyd_pins.h`: `CYD_SPI_FREQ_HZ` was `27000000` and is now `20000000`. 27 MHz
was above the reliable margin for CYD ILI9341 panels and caused intermittent
init/first-frame corruption — a flaky/blank boot splash. 20 MHz is the
current, hardware-verified value; do not raise it without re-verifying boot
splash reliability across multiple cold boots.

### 4.3 Touch (XPT2046) is a second SPI device on the display bus, not bit-banged

`touch_xpt2046.cpp` (rewritten as part of `9a35baf`): the XPT2046 is
registered as a **second `spi_master` device on `SPI2_HOST`** — the same
hardware bus the ILI9341 already owns — via `spi_bus_add_device(SPI2_HOST,
&dev_cfg, &_spi)` with `dev_cfg.spics_io_num = cs_pin` (hardware-managed CS,
GPIO33) and `dev_cfg.clock_speed_hz = CYD_SPI_TOUCH_FREQ_HZ` (2.5 MHz). The
ESP-IDF SPI driver configures the bus pins once and reconfigures per-device
clock/mode/CS atomically per transaction, so a touch read can never disturb
the display's pin routing.

This replaced an earlier bit-bang implementation that called
`gpio_reset_pin()` on the shared HSPI pins (MOSI 13 / MISO 12 / SCLK 14) for
every touch read and then tried to manually restore SPI peripheral routing
afterward — restoring 27 MHz display SPI this way proved impossible (GPIO
matrix ceiling ~26.6 MHz), so the display died on the first touch poll. The
shared-bus-peer architecture eliminates that teardown at the root, and as a
bonus GPIO12 (MISO) — an ESP32 strapping pin — is no longer reset on every
read.

## 5. Verify

- **Serial `[PERF]` line** (115200 baud, `main.cpp`): every 30 animated
  frames, logs:
  `[PERF] scene '%s' %dw: render avg %.1f ms (min %.1f / max %.1f) = %.2f fps
  raster (%.1f ms/band over 20 bands); loop fps %.2f; heap %lu`.
  The file header comment states a healthy full-frame render is ~56 ms.
- **On-device pull capture server** (`capture_http.cpp`, SD-free, added by
  `cfa313a`): `GET http://<ip>/capture.bmp` — triggers a live band re-render
  into a 24-bit BGR, top-down BMP streamed straight from the response (no SD,
  no buffering beyond one 720-byte row). `GET http://<ip>/` returns a
  one-line status (scene title, heap, pointer to `/capture.bmp`).
- **IMPORTANT**: on animated scenes, the exact failure this board once had
  (§4.1) was the framebuffer/render loop advancing normally while the glass
  stayed frozen — SPI writes kept ACKing `ESP_OK`. That means a healthy
  `[PERF]` log stream and even a moving `capture.bmp` are **not sufficient**
  proof the panel itself is updating. An eyes-on-glass confirmation is the
  final verification gate for this board, not a substitute check.

## 6. Known issues / follow-ups

- **Sprite-frame fetch flakiness**: per `9a35baf`'s commit message ("Minor
  follow-up (unrelated): a transient boot asset fetch can leave the dradis
  sprite at 6/8 frames — a bus_storage fetch-retry concern, not this fix."),
  a transient bus_storage sprite-frame fetch during boot can leave an
  animated sprite short of its full frame count (observed as N/8 frames
  loaded instead of 8/8). This is a fetch-retry gap in the sprite asset
  loader, not a display/DMA issue — not yet fixed as of the commits reviewed
  here.

## 7. Machine-readable descriptor

```yaml
board_id: classic-cyd
board_name: "CYD (ESP32-2432S024)"
chip: esp32
chip_arch: xtensa-lx6
psram: false
display:
  controller: ili9341
  width_px: 240
  height_px: 320
  shape: rect
  bus: spi2_host
  spi_clock_hz: 20000000
  render_strategy: band
  band_height_px: 16
  bands_per_frame: 20
touch:
  controller: xpt2046
  bus: spi2_host_shared_device
  cs_mode: hardware
  spi_clock_hz: 2500000
device_id:
  pattern: "cyd-{mac_bytes[3:6]_hex}"
  override_build_flag: DISPLAY_DEVICE_ID
connect:
  usb_serial_chip: ch340
  port_glob: "/dev/cu.usbserial-*"
  serial_baud: 115200
flash:
  flash_tool: esptool
  flash_tool_wrapper: platformio
  flash_cmd: "cd cyd && pio run -e cyd -t upload"
  monitor_cmd: "pio device monitor -e cyd"
  enter_bootloader: auto
  flash_size: 4MB
  flash_mode: qio
  flash_freq_hz: 80000000
config:
  source: bus_storage_http
  url_pattern: "{BUS_STORAGE_URL}/api/v1/assets/displays/{device_id}.compose.yaml"
  fetch_at_boot: true
  boot_retry_interval_s: 5
  repoll_interval_s: 60
  sd_card_required: false
verify:
  serial_token: "[PERF]"
  serial_baud: 115200
  capture_endpoint: "http://{ip}/capture.bmp"
  capture_format: bmp_24bit_bgr_topdown
  status_endpoint: "http://{ip}/"
  human_glass_confirm_required: true
  human_glass_confirm_reason: "framebuffer/render-loop can advance while panel is frozen (silent SPI ACK failure) — serial/capture alone are insufficient"
quirks:
  - id: shared-spi-dma-channel-hazard
    status: eliminated
    description: "Classic ESP32 has only 2 SPI DMA channels; SD-bus teardown (spi_bus_free) on SPI_DMA_CH_AUTO reset the display's DMA channel and froze the panel while it kept ACKing writes. Fixed with explicit non-overlapping DMA channels + SD-bus init-once/never-free, then made structurally impossible by removing the SD card entirely."
    fix_commits: ["9a35baf", "cfa313a"]
  - id: display-spi-clock-margin
    status: fixed
    description: "Display SPI clock lowered 27 MHz -> 20 MHz; 27 MHz was above the reliable margin and caused intermittent/blank boot splash."
    fix_commits: ["9a35baf"]
  - id: touch-shares-display-bus
    status: by_design
    description: "XPT2046 touch is a second spi_master device (hardware CS) on the display's SPI2 bus, not bit-banged. Replaces an earlier bit-bang path that tore down and tried to restore shared HSPI pin routing on every touch read, which could not reliably restore 27 MHz display SPI."
    fix_commits: ["9a35baf"]
  - id: sprite-frame-fetch-flakiness
    status: open
    description: "A transient boot-time bus_storage sprite-frame fetch can leave an animated sprite short of its full frame count (observed N/8 frames). Fetch-retry gap in the sprite asset loader."
    fix_commits: []
```
