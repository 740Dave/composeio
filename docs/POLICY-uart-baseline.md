# POLICY — UART is baseline firmware, not a feature

**[POLICY-UART-BASELINE].** Standing rule, opened 2026-08-13. Workstream B of
`docs/PLAN-fleet-baseline-and-bus-native.md`.

> **David, 2026-08-12:** *"our baseline firmware now and forever more should have UART
> support baked in, unless we have to waive it for extremely slow/limited MCUs."*

## Why

A board with `bus_uart.cpp` compiled in can be recovered, reconfigured and re-pointed
over **three wires with no IP address and no working radio**. A board without it is only
as reachable as its radio.

That is not a convenience. It is the difference between a bricked board and a recoverable
one, and this project has already used exactly that path: `hosyond-080739` was configured
over UART with its WiFi deliberately off.

## The rule

An env that speaks the bus **at all** must be able to speak it **over wire**:

```
bus_can.cpp | bus_mqtt.cpp | bus_transport_select.cpp   ==>   bus_uart.cpp
```

An env that registers **none** of those is not a bus node and is exempt — **by shape, not
by name**. There is no allow-list of exempt envs to keep in step with `platformio.ini`,
because an allow-list is a thing you forget to update.

Enforced at configure time by `cxc_assert_uart_baseline()` at the top of
`cyd/src/CMakeLists.txt`. See "The guard", below.

## Audit — every env in `cyd/platformio.ini`, 2026-08-13

### ESP-IDF envs (in scope — these produce firmware)

| # | env | `platformio.ini` | CMake branch (`cyd/src/CMakeLists.txt`) | `bus_uart.cpp` | verdict |
|---|---|---|---|---|---|
| 1 | `cyd` | L29, no cache var, `esp32` | fallthrough register, L863 | **L945** | ✅ compliant |
| 2 | `cyd_dsl_test` | L59, `extends = env:cyd` | same fallthrough (target is still `esp32`) | **L945** | ✅ compliant |
| 3 | `esp32c3_dsl_test` | L71, no cache var, `esp32c3` | `NOT IDF_TARGET STREQUAL "esp32"` → DSL-only, L848–861 | — | ⚪ **exempt by shape** — registers `dsl.cpp` + `dsl_selftest.cpp` and **no transport at all**. Not a bus node. |
| 4 | `qualia_s3_dsl_test` | L96, no cache var, `esp32s3` | same DSL-only branch, L848–861 | — | ⚪ **exempt by shape** — same, on Xtensa LX7. |
| 5 | `qualia_display` | L126, `-DQUALIA_DISPLAY=1` | `if(QUALIA_DISPLAY)`, L411 | **L496** | ✅ compliant |
| 6 | `hosyond_s3` | L160, `-DHOSYOND_S3=1` | `if(HOSYOND_S3)`, L587 | **L634** | ✅ compliant |
| 7 | `waveshare_c6` | L194, `-DWAVESHARE_C6=1` | `if(WAVESHARE_C6)`, L739 | **L785** | ✅ compliant |
| 8 | `esp32_c3_st7796` | L256, `-DCXC_C3_ST7796=1` | `if(CXC_C3_ST7796)`, L185 | **L231** | ✅ compliant |
| 9 | `esp32_can_relay` | L315, `-DCXC_RELAY=1` | `if(CXC_RELAY)`, L12 | **L19** | ✅ compliant — and it is the node that holds *two* transports on purpose |
| 10 | `esp32_c3_can_bringup` | L362, `-DCXC_CAN_BRINGUP=1` | `if(CXC_CAN_BRINGUP)`, L86 | **L92** | ✅ compliant |
| 11 | `esp32_c3_pin_drive_test` | L416, `extends` #10 `+ -DCXC_PIN_DRIVE=1` | same `CXC_CAN_BRINGUP` branch, L86 | **L92** | ✅ compliant |

**9 compliant, 2 exempt, 0 violations.** The requirement was already met — by accident.
This document and the guard are what turn it into policy.

Machine-verified, not read off the file: with the guard installed, all 11 envs were
built on a scratch copy and the object files checked.

```
cyd                      build_exit=0  bus_uart.o=YES
cyd_dsl_test             build_exit=0  bus_uart.o=YES
esp32c3_dsl_test         build_exit=0  bus_uart.o=no     <- exempt, no transport
qualia_s3_dsl_test       build_exit=0  bus_uart.o=no     <- exempt, no transport
qualia_display           build_exit=0  bus_uart.o=YES
hosyond_s3               build_exit=0  bus_uart.o=YES
waveshare_c6             build_exit=0  bus_uart.o=YES
esp32_c3_st7796          build_exit=0  bus_uart.o=YES
esp32_can_relay          build_exit=0  bus_uart.o=YES
esp32_c3_can_bringup     build_exit=0  bus_uart.o=YES
esp32_c3_pin_drive_test  build_exit=0  bus_uart.o=YES
```

### `native_*` host-test envs (out of scope — and stated, not silently skipped)

`native_dsl`, `native_wrap`, `native_chat_wrap`, `native_test`, `native_scroll_test`,
`native_overlay_test`, `native_wire_codes`, `native_frame`, `native_config_chunk`,
`native_can_id`, `native_relay`, `native_announce`, `native_keyboard_test` — **13 envs.**

**Out of scope, for two reasons, and neither is "they're just tests":**

1. **They produce no firmware.** `platform = native` builds a host binary that runs on the
   Mac. There is no board to recover, so there is nothing for the recovery path to
   recover. The policy protects a *physical device's reachability*; these have no device.
2. **They never read `cyd/src/CMakeLists.txt`.** `platform = native` selects sources with
   `build_src_filter` under SCons and never enters CMake at all, so the guard is
   structurally incapable of seeing them. Adding them to the policy would mean writing a
   second, differently-implemented guard for a class of build that cannot fail the way
   the policy is about.

Note that several of them (`native_frame`, `native_can_id`, `native_relay`,
`native_config_chunk`) *do* test the wire codec that UART transport depends on. They
strengthen the UART path; they are not subject to the policy.

## The waiver criterion — a number, not a vibe

### What the UART transport actually costs

Measured, not estimated — from the real object files of the current build
(`riscv32-esp-elf-size` / `xtensa-esp-elf-size` on `.pio/build/*/src/bus_uart.cpp.o`):

| | ESP32-C3 (RISC-V) | ESP32-S3 (Xtensa LX7) |
|---|---|---|
| `.text` | 6,391 B | 6,096 B |
| `.data` | 25 B | 25 B |
| `.bss` | 3,149 B | 3,149 B |

Plus, at runtime, from `bus_uart.cpp`: `kRxRingBytes = 1024` (L176) and
`kRxTaskStack = 4096` (L177) → roughly **5.5 KB of heap** once
`uart_driver_install()` and the `bus_uart_rx` task are up.

The `.bss` is dominated by the subscription tables: `kMaxSubs = 16` (L156) and
`kMaxPersist = 8` (L174) entries of `NsEntry`, whose `char ns[96]` (L186) is the
expensive field — 24 × ~100 B ≈ 2.4 KB of the 3.1 KB.

**Full profile as built today: ~6.4 KB flash, ~8.7 KB RAM** (3.2 KB static + ~5.5 KB heap).

`bus_frame.cpp` (1,406 B text) is the shared codec and is *not* charged to UART — CAN
needs it too.

### Two profiles and one floor

**Tier A — full profile.** The current constants. **~6.4 KB flash, ~8.7 KB RAM.**
Required of any part with **≥ 32 KB RAM and ≥ 128 KB flash**. Every ESP32 part in the
fleet clears this by more than an order of magnitude (the C3 has 320 KB RAM / 4 MB flash;
the guard costs it 2.7% of RAM).

**Tier B — constrained profile.** `kMaxSubs = 4`, `kMaxPersist = 2`,
`kRxRingBytes = 256`, and a polled RX or a 1,536 B task stack. Static drops to roughly
800 B and heap to roughly 2 KB. **~6 KB flash, ~2.8 KB RAM.** Required of any part with
**≥ 8 KB RAM and ≥ 32 KB flash**. A Tier B node subscribes fewer namespaces; it does not
lose the recovery path, which is the entire point.

**WAIVER FLOOR — a part may be waived if and only if it has**

> ### **< 8 KB usable RAM, OR < 32 KB usable flash.**

**Rationale for 8 KB / 32 KB, specifically.** Tier B costs ~2.8 KB RAM. At an 8 KB part
that is 35% of RAM before the application has allocated anything — already aggressive, and
the last point at which a node can carry the transport *and* still be the reason the board
was chosen. Below 8 KB the transport crowds out the application and you are shipping a
board whose only function is to be reachable. The flash figure is set the same way: ~6 KB
is 19% of a 32 KB part, and the ratio degrades fast below that.

The floor is **OR**, not AND, because either resource alone is a hard stop.

### The two parts that force the question

| part | RAM | flash | vs. floor | verdict |
|---|---|---|---|---|
| **CH32V003** | ~2 KB | 16 KB | below **both** — Tier B's 2.8 KB static does not fit in 2 KB of total RAM | **WAIVABLE** |
| **CH32V203C8T6** | **20 KB** | **64 KB** marked (~128 KB per-unit extended) | **2.5× the RAM floor, 2× the flash floor** | **NOT WAIVABLE — Tier B required** |

The CH32V203 was expected to be the interesting boundary. **It is not a boundary at all —
it is comfortably above the floor**, and it must carry UART. The evidence is already in
`docs/board-bringup/ch32v203.descriptor.yaml`: the 82.8 KB `ch32v203_dsl_test` image
wrote and verified clean on the physical unit, so a ~6 KB transport is not close to a
flash problem, and 2.8 KB of 20 KB RAM leaves 17 KB for the application.

That matters beyond bookkeeping. The CH32V203 is the part the plan proposes as a
CAN-attached host with a UART leaf hanging off it
(`[SPEC:CXC-CONFIG-BUS]`, `[CXC-CONFIG-BUS-INTENT]`). **A waived CH32V203 would break that
topology outright** — it cannot proxy config to a leaf over a transport it does not have.

## What a waived board loses

A waiver is not free and must not be granted as a formality. A waived board gives up:

1. **Radio-independent recovery.** It cannot be re-pointed or reconfigured without its
   radio. If the radio config is what broke, the board is bricked short of a reflash.
2. **Config over the bus** — `[SPEC:CXC-CONFIG-BUS]` cannot reach it over wire.
3. **Remote restart** — `[CXC-ER-REMOTE-RESTART]`'s handler has no wire path.
4. **Self-announcement.** It is invisible to a fleet census unless its radio is up, so a
   *missing* announcement stops being a fault report and becomes ambiguous — the exact
   failure the announcement design exists to avoid.
5. **Peer status.** It becomes a **leaf**: reachable only through a host that does have
   UART.

## What a waived board must provide instead

All three, or the waiver is denied:

1. **A hardware ISP/bootloader recovery path** that reflashes it with no network, recorded
   in its descriptor's `flash:` block. For the CH32V003 that is WCH single-wire SWIO via
   `minichlink`; for the CH32V203 it would be BOOT0 + `wchisp` USB-ISP. This is the
   substitute for "recoverable over three wires" and it is the *only* substitute the
   waiver accepts.
2. **A named UART-bearing proxy host** that carries it onto the bus (the relay pattern,
   `env:esp32_can_relay`), named in the descriptor. A waived board with no proxy is not
   on the fleet.
3. **A `uart_waiver:` block** in `docs/board-bringup/<board>.descriptor.yaml` naming the
   part, its RAM and flash, the recovery path, and the proxy host.

And the build must carry both cache vars:

```ini
board_build.cmake_extra_args =
    -DCXC_UART_WAIVED=1
    -DCXC_UART_WAIVER_REASON="CH32V003: 2 KB RAM / 16 KB flash"
```

A waiver with no reason is a **fatal** configure error. That is deliberate: a reasonless
waiver is exactly the silent accident this policy exists to catch.

## The guard

`cxc_assert_uart_baseline()`, at the top of `cyd/src/CMakeLists.txt`. Three design notes
worth keeping, each of which cost a failed experiment on a scratch copy:

**It is deferred, not called per branch.** Every board branch ends in `return()`, so a
check written at the *bottom* of the file would audit only the fallthrough env.
`cmake_language(DEFER CALL ...)` runs at the end of the directory scope, which `return()`
does not skip — so a **new board branch added anywhere in the file is audited without its
author opting in**. Covering the branch nobody has written yet is the whole point.

**It skips script mode.** ESP-IDF re-reads a component's `CMakeLists.txt` under `cmake -P`
(via `component_get_requirements.cmake`) to expand requirements. Script mode has no
directory scope, and `DEFER` there is a hard error: *"cmake_language DEFER CALL may not be
scheduled in directory."* Without the `if(NOT CMAKE_SCRIPT_MODE_FILE)` wrapper, **every
ESP-IDF env in the tree fails to configure**. Found by building the scratch copy, not by
reading.

**A waiver cannot be made loud in the console, and the guard does not pretend otherwise.**
Tested in this order, all on the scratch copy:

- `message(WARNING)` — swallowed by PlatformIO.
- `message(STATUS)` — also swallowed. On a *successful* configure PlatformIO echoes
  nothing from CMake at all; the CMake output seen in a failing configure is only printed
  because the configure failed.
- `add_custom_command(TARGET ... POST_BUILD)` — never runs. For `framework = espidf`,
  PlatformIO uses CMake solely to **extract** the build configuration and then compiles
  and links with SCons, so no CMake target is ever built.

So the waiver mints `CXC_UART_WAIVED_BUILD=1` as a real compile definition — the one
channel this file already documents as surviving to the compiler — and the loudness lives
where it can be *enforced* rather than merely printed: the descriptor's `uart_waiver:`
block and the corpus policy node. Audit a build with:

```sh
grep -c CXC_UART_WAIVED_BUILD cyd/.pio/build/<env>/compile_commands.json
```

### Proven, not assumed

A guard that has never failed is not a guard. On a scratch copy of the build tree:

| # | scenario | expected | observed |
|---|---|---|---|
| 1 | guard installed, tree unmodified | all 11 ESP-IDF envs build | **all 11 SUCCESS** |
| 2 | `bus_uart.cpp` dropped from the `CXC_CAN_BRINGUP` branch | configure FATAL | **FATAL**, naming `env: esp32_c3_can_bringup`, `found: bus_can.cpp, bus_mqtt.cpp, bus_transport_select.cpp`, `missing: bus_uart.cpp`; `pio run` exit 1 |
| 3 | `bus_uart.cpp` dropped from the `HOSYOND_S3` branch | configure FATAL | **FATAL**, naming `env: hosyond_s3`, `IDF_TARGET: esp32s3` |
| 4 | `-DCXC_UART_WAIVED=1` with no reason | configure FATAL | **FATAL** — "CXC_UART_WAIVED is set with no CXC_UART_WAIVER_REASON" |
| 5 | `-DCXC_UART_WAIVED=1` + reason | configure proceeds, definition minted | **13 compile units** carry `CXC_UART_WAIVED_BUILD=1` in `compile_commands.json` |
| 6 | break reverted | env builds again | **SUCCESS** |

Scenario 5 also surfaced a real consequence worth recording: on an ESP-IDF board a waiver
is *not* just "drop the file". `bus_transport_select.cpp`'s static registry references
`bus_uart_transport()`, so a waived ESP-IDF build fails at **link** with
`undefined reference to 'bus_uart_transport()'`. A genuine waiver on this platform would
have to remove the registry entry too. That is a feature — on an ESP32 the linker is a
second, independent objection to waiving something no ESP32 has any business waiving.

## Applying the guard

The guard is delivered as a patch, not as an edit, because `cyd/src/CMakeLists.txt` was
being edited concurrently when this was written.

```sh
cd ${COMPOSEIO_HOME}
git apply --check docs/patches/uart-baseline-guard.patch   # verify first
git apply         docs/patches/uart-baseline-guard.patch
```

It is a **single hunk inserted at line 3**, above every board branch, so it does not
conflict with edits to any branch below. If `git apply` ever rejects it, the entire
content is the block between the file header and the `[SPEC:CXC-CONFIG-BUS]`
`CXC_RELAY` comment — paste it there.

Verify after applying:

```sh
cd cyd && pio run -e esp32_c3_can_bringup      # must SUCCEED
# then, to confirm the guard is live rather than merely present:
#   delete "bus_uart.cpp" from any board branch, rebuild, see it FATAL, restore.
```

## Corpus

`corpora/platform-specs/65-uart-baseline-policy.lts`, `[SPEC:CXC-UART-BASELINE]`. The
corpus node is the normative record; this document is the operational how-to.
