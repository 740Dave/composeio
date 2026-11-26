# ESP32-C3 bring-up: reactive-core devkit + two actuator boards

Three PlatformIO envs in `firmware/reactive_core/platformio.ini`, all
`board = esp32-c3-devkitm-1` (RISC-V, native USB), sharing the same app
shell (`main.cpp`) and board-agnostic core (`reactive_core.cpp`) —
only `boards/*.cpp` differs per target. Facts below are sourced from
that `platformio.ini`, `firmware/reactive_core/src/boards/*.cpp`, and
commits `e74e56a` (R1, reactive core) and `9d85c95` (R2, actuator HAL).

## 1. Identity

| Env | Board profile | Role |
|---|---|---|
| `esp32c3` | ESP32-C3-DevKitM-1 (bare devkit) | Headless reactive core — R1. `board_profile().id = "esp32c3-devkitm-1"` |
| `lego_dots` | ESP32-C3 SuperMini | Actuator — R2. `board_profile().id = "lego-dots-12ch"` |
| `rack_triple_gauge` | ESP32-C3 SuperMini | Actuator — R2. `board_profile().id = "rack-triple-gauge"` |

All three are RISC-V (single-core ESP32-C3), `framework = arduino`,
`platform = espressif32@6.5.0`, `-std=gnu++17`,
`-DARDUINO_USB_CDC_ON_BOOT -DARDUINO_USB_MODE` (native-USB CDC
console, not a UART bridge). Each env's `build_src_filter` excludes
`boards/` wholesale then re-includes only its own board `.cpp`
(`firmware/reactive_core/platformio.ini`).

The devkit's R1 demo output is `demo_gpio = GPIO3` (ADC pin, no
strapping/USB conflict) mirrored on the on-board WS2812 (`neopixel_gpio
= GPIO8`) — see `src/boards/esp32c3_devkitm.cpp`. Both SuperMini
actuator boards set `demo_gpio = -1` and `neopixel_gpio = -1` (no demo
pin wired, no addressable LED on the SuperMini).

## 2. Connect + flash

- Native USB. On macOS the port enumerates as `/dev/cu.usbmodem*` —
  the bare devkit is pinned in the ini to `/dev/cu.usbmodem11301`
  (`upload_port` / `monitor_port` in `[env:esp32c3]`).
- The two actuator envs (`lego_dots`, `rack_triple_gauge`) have **no
  upload_port pinned** — PlatformIO auto-detects the CDC port, because
  "the boards are plugged in ad hoc" (comment above `[env:lego_dots]`
  in `platformio.ini`).
- Flash:
  ```
  cd firmware/reactive_core
  pio run -e esp32c3 -t upload && pio device monitor -e esp32c3
  pio run -e lego_dots -t upload && pio device monitor -e lego_dots
  pio run -e rack_triple_gauge -t upload && pio device monitor -e rack_triple_gauge
  ```
- Same native-USB **`Errno 6` transient re-enumeration** caveat seen
  on other native-USB ESP boards in this repo (documented for the
  Qualia S3 sibling env in the same `platformio.ini`: "a transient
  re-enumeration may need one upload retry") — retry the upload once
  if it fails immediately after a reset/replug.
- `monitor_speed = 115200` on all three envs.

## 3. Secrets / WiFi

- `main.cpp` defaults `WIFI_SSID` / `WIFI_PASS` to `YOUR_WIFI_SSID` /
  `YOUR_WIFI_PASSWORD` placeholders (`firmware/reactive_core/src/main.cpp`).
- Real credentials go in a **gitignored** `src/wifi_config.h` (pattern
  `**/wifi_config.h` in the repo-root `.gitignore`, added in `e74e56a`)
  defining the two macros — **never commit credentials**. Alternative:
  inject at build time via
  `PLATFORMIO_BUILD_FLAGS='-DWIFI_SSID=\"ssid\" -DWIFI_PASS=\"pass\"' pio run -e esp32c3 -t upload`.
- MQTT broker: all three envs set `-DMQTT_PORT=1883` — the **shared
  ecosystem broker** (`192.0.2.75:1883`, i.e. `mqtt-broker.local`),
  the same broker the Composer/headless smokes use. This is a deliberate
  override: the ini's header comment documents the *foundation's*
  historical default as `mqtt-broker.local:1884`, but reactive-core
  devices ride 1883, not 1884 (`platformio.ini` header + inline comment
  above `-DMQTT_PORT=1883`; confirmed in `e74e56a` commit message:
  "devices ride :1883 (shared ecosystem broker, now the reactive_core
  default; not the 1884 foundation)"). `MQTT_BROKER` is overridable the
  same way as WiFi creds if it ever needs to move off the default.

## 4. The actuator boards

Both actuator boards are physically **STAGED FOR DAVID** per the R2
commit (`9d85c95`) — compiled/HAL-verified on the bare C3 over serial
self-report, but the actual PCBs were not connected at commit time.
Both need **12V** supplied and eyes-on-hardware to verify real motion/
light.

### `lego_dots` — 12x low-side light switches (Output Tier 1)

- Source: `firmware/reactive_core/src/boards/lego_dots.cpp`; PCB
  generator `${MAKER_HOME}/pcb/lego_dots/generate_pcb.py` v1.0.
- ESP32-C3 SuperMini. Each channel: GPIO -> 150R -> BC337 base,
  emitter to GND, collector = low-side switch for a **12/24V** light
  (JST-XH pin3 return, pin2 = +12V). GPIO HIGH = transistor ON = light
  ON (active-high).
- Pin map, `light/1`..`light/12` -> GPIO `0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
  10, 20` (in order, from the generator's CHANNELS table).
- **Strapping-pin caution**: GPIO2 (`light/3`), GPIO8 (`light/9`), and
  GPIO9 (`light/10`) are ESP32-C3 boot-strapping pins — fine as
  outputs once booted, but a live BC337 base on a strapping pin is a
  boot-mode risk if externally pulled during reset (flagged as a PCB
  concern in `9d85c95`: "lego_dots GPIO2/8/9 are C3 strapping pins
  driving BC337 bases (could clamp at reset, PCB concern)"). GPIO8
  also carries the SuperMini's on-board LED, which will visibly mirror
  `light/9`.
- All channels init LOW (lights off) in `board_outputs_init()`. No
  addressable RGB LED and no demo pin on this board.

### `rack_triple_gauge` — 3x stepper needle gauges + PWM backlights (Output Tier 2 + Tier 1)

- Source: `firmware/reactive_core/src/boards/rack_triple_gauge.cpp`;
  PCB generator `${MAKER_HOME}/pcb/rack_triple_gauge/generate_pcb.py`.
- ESP32-C3 SuperMini -> MCP23017 over I2C (`SDA = GPIO4`, `SCL =
  GPIO5`, 4.7k pullups to 3V3, 400kHz) -> 3x L293D -> 3x stepper motors
  (the dial needles), plus 3x 2N2222 low-side PWM -> LED backlights.
  All L293D EN/VS/VCC tied to **+5V** (always enabled); motors are the
  **12V** load.
- MCP23017 address `0x20` (A0..A2 = GND), RESET tied high. Motor 1 =
  GPA0..3 -> L293D#1 IN1..IN4; Motor 2 = GPA4..7 -> L293D#2; Motor 3 =
  GPB0..3 -> L293D#3. Backlights: `gauge/1/light` = GPIO0,
  `gauge/2/light` = GPIO1, `gauge/3/light` = GPIO3 (all PWM, 5kHz/8-bit
  LEDC).
- If the MCP23017 doesn't ACK at init, `board_outputs_init()` logs
  `"MCP23017 @0x20 not responding — steppers disabled (board
  attached?)"` over serial and steppers become inert (backlights still
  work, they're direct GPIO PWM).
- Actuator table: `gauge/1..3` are Stepper actuators, `in 0..100 ->
  out 0..600` full steps, `step_interval_ms = 3`, `home_on_boot =
  true` (homes against the mechanical stop, no position feedback).
  **`out_max = 600` is a placeholder** — the R2 commit explicitly
  flags it as needing tuning to the real dial sweep at bring-up
  ("rack_triple_gauge out_max=600 steps is a placeholder to tune to
  the dial", `9d85c95`). Adjust the `out_max` field in the
  `kActuators` table in `rack_triple_gauge.cpp` once the physical dial
  full-scale travel is measured.

## 5. Verify

- **DSL corpus self-test** (RISC-V determinism re-verify — proves the
  soft-double DSL is bit-exact on the C3, not just Xtensa): lives in
  `cyd/platformio.ini`, not `firmware/reactive_core/platformio.ini`.
  ```
  cd cyd
  pio run -e esp32c3_dsl_test -t upload && pio device monitor -e esp32c3_dsl_test
  ```
  Expect **234/234** conformance cases pass (hardware-verified result
  from `e74e56a`: "DSL corpus 234/234 on RISC-V soft-double (219
  us/case)"). This env is ESP-IDF (not Arduino), `board =
  esp32-c3-devkitm-1`, `upload_port = /dev/cu.usbmodem11301`, built
  with `-DDSL_CORPUS_TEST` against `dsl.cpp` + `dsl_selftest.cpp` only.
- **Host unit test** (pure core + actuator HAL, no hardware):
  ```
  cd firmware/reactive_core
  pio run -e native_test && .pio/build/native_test/program
  ```
  Must print `ALL PASS (N checks)` and exit 0
  (`src/host_test_main.cpp`). R1 baseline was 46/46; R2 added the
  actuator HAL cases for 94/94 total (`9d85c95`).
- **Live reactive core** (`esp32c3` env): publish to the bus (MQTT
  `192.0.2.75:1883`) and watch it land — either GPIO3 toggling on
  the devkit (mirrored on the WS2812) or the serial monitor's
  self-report. Hardware-verified pattern from `e74e56a`: publishing
  `demo/level` drove `ns('demo/level')>50` -> GPIO3 (`72->HIGH,
  30->LOW, 88->HIGH`).
- **Actuator boards** (`lego_dots`, `rack_triple_gauge`): HAL logic is
  serial-self-report verified per-command (e.g. `light/1=on->GPIO4
  HIGH`, `light/2=60->PWM 153/255`, `gauge/1=50->120
  steps->arrived`), but **physical verification requires human eyes**
  on the actual light/needle/backlight with 12V applied — this is not
  automatable from the DSL/host-test harness.

## 6. Machine-readable descriptor

```yaml
boards:
  - id: esp32c3-devkitm-1
    role: reactive_core_headless
    chip: esp32c3
    arch: riscv32
    framework: arduino
    platformio_env: esp32c3
    platformio_project_dir: firmware/reactive_core
    flash_tool: esptool
    flash_cmd: "cd firmware/reactive_core && pio run -e esp32c3 -t upload"
    monitor_cmd: "pio device monitor -e esp32c3"
    port_glob: /dev/cu.usbmodem*
    port_pinned: /dev/cu.usbmodem11301
    monitor_speed: 115200
    flash_retry:
      condition: native_usb_errno6_transient_reenum
      action: retry_upload_once
    secrets:
      wifi:
        pattern: src/wifi_config.h
        gitignore_rule: "**/wifi_config.h"
        placeholder_defines: [WIFI_SSID=YOUR_WIFI_SSID, WIFI_PASS=YOUR_WIFI_PASSWORD]
        alt_injection: "PLATFORMIO_BUILD_FLAGS='-DWIFI_SSID=... -DWIFI_PASS=...'"
      mqtt:
        broker_host: 192.0.2.75
        broker_alias: mqtt-broker.local
        port: 1883
        build_flag: -DMQTT_PORT=1883
        note: "ecosystem broker, NOT the 1884 foundation default"
    verify:
      - name: dsl_corpus_riscv
        env: esp32c3_dsl_test
        project_dir: cyd
        cmd: "cd cyd && pio run -e esp32c3_dsl_test -t upload && pio device monitor -e esp32c3_dsl_test"
        expect: "234/234"
      - name: host_unit_test
        env: native_test
        project_dir: firmware/reactive_core
        cmd: "cd firmware/reactive_core && pio run -e native_test && .pio/build/native_test/program"
        expect: "ALL PASS (94 checks)"
      - name: live_bus_to_gpio
        method: manual
        cmd: "publish MQTT topic -> observe GPIO3 / WS2812 mirror / serial monitor"
    quirks: []

  - id: lego-dots-12ch
    role: actuator
    chip: esp32c3
    board_variant: esp32-c3-supermini
    arch: riscv32
    framework: arduino
    platformio_env: lego_dots
    platformio_project_dir: firmware/reactive_core
    flash_tool: esptool
    flash_cmd: "cd firmware/reactive_core && pio run -e lego_dots -t upload"
    monitor_cmd: "pio device monitor -e lego_dots"
    port_glob: /dev/cu.usbmodem*
    port_pinned: null
    monitor_speed: 115200
    flash_retry:
      condition: native_usb_errno6_transient_reenum
      action: retry_upload_once
    secrets:
      wifi:
        pattern: src/wifi_config.h
        gitignore_rule: "**/wifi_config.h"
      mqtt:
        broker_host: 192.0.2.75
        port: 1883
        build_flag: -DMQTT_PORT=1883
    power: 12V_low_side_switched
    actuators:
      - {ns: "light/1..12", mode: binary, pins: [0,1,2,3,4,5,6,7,8,9,10,20], driver: BC337, active_high: true}
    verify:
      - name: hal_serial_selfreport
        method: serial
        example: "light/1=on -> GPIO4 HIGH"
      - name: physical_light
        method: manual
        note: "requires 12V supply + human eyes; no automated check"
    quirks:
      - strapping_pins_driving_transistor_bases: [GPIO2, GPIO8, GPIO9]
      - note: "GPIO8 also carries SuperMini on-board LED (mirrors light/9)"

  - id: rack-triple-gauge
    role: actuator
    chip: esp32c3
    board_variant: esp32-c3-supermini
    arch: riscv32
    framework: arduino
    platformio_env: rack_triple_gauge
    platformio_project_dir: firmware/reactive_core
    flash_tool: esptool
    flash_cmd: "cd firmware/reactive_core && pio run -e rack_triple_gauge -t upload"
    monitor_cmd: "pio device monitor -e rack_triple_gauge"
    port_glob: /dev/cu.usbmodem*
    port_pinned: null
    monitor_speed: 115200
    flash_retry:
      condition: native_usb_errno6_transient_reenum
      action: retry_upload_once
    secrets:
      wifi:
        pattern: src/wifi_config.h
        gitignore_rule: "**/wifi_config.h"
      mqtt:
        broker_host: 192.0.2.75
        port: 1883
        build_flag: -DMQTT_PORT=1883
    power: 12V_motors_5V_driver_logic
    i2c:
      bus: {sda_gpio: 4, scl_gpio: 5, clock_hz: 400000}
      devices: [{addr: "0x20", part: MCP23017}]
    actuators:
      - {ns: "gauge/1..3", mode: stepper, channel: [0,1,2], driver: "MCP23017->L293D", in_range: [0,100], out_max: 600, out_max_status: PLACEHOLDER_NEEDS_TUNING, step_interval_ms: 3, home_on_boot: true}
      - {ns: "gauge/1..3/light", mode: pwm, pins: [0,1,3], driver: 2N2222, out_max: 255}
    verify:
      - name: hal_serial_selfreport
        method: serial
        example: "gauge/1=50 -> 120 steps -> arrived"
      - name: mcp23017_presence_check
        method: serial
        failure_message: "MCP23017 @0x20 not responding — steppers disabled (board attached?)"
      - name: physical_needle_and_backlight
        method: manual
        note: "requires 12V supply + human eyes; tune out_max to real dial sweep"
    quirks:
      - out_max_placeholder: "600 full-steps is unverified; tune to actual dial travel at bring-up"
      - graceful_degradation: "steppers no-op if MCP23017 absent; backlights (direct GPIO PWM) still work"
```
