# Fleet device registry — MAC-as-serial → device identity

**The per-project index from a physical device's stable identity to what it IS.** A native-USB
ESP32 publishes its **MAC as its USB serial number**, and that MAC is the only stable identity a
device has — `/dev/cu.*` port paths **churn** every time something is plugged/unplugged. This table
is the lookup the fleet lacked (identity was scattered across `platformio.ini` comments, a build
script, a deploy `.env`, and dated research docs); it is maintained here so "which board is this,
and may I touch it?" is a **lookup, not a re-derivation**.

> Created 2026-09-11 (composeio-hardware-architect), seeded from `docs/can-v2-fleet-sweep-2026-08-15.lts`,
> `firmware/reactive_core/platformio.ini`, `firmware/r503_probe/{platformio.ini,build.sh}`,
> `deploy/can_mqtt_bridge/composeio-can-bridge.env`, `docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md`,
> and a passive 2026-09-11 bench scan. Every row cites its source; unproven cells say so.

## ⛔ The hard rule this table exists to enforce
1. **Verify identity PASSIVELY before any destructive operation, every time.** Read the USB serial
   (`system_profiler SPUSBDataType`, or walk `ioreg` to the `IOCalloutDevice` leaf) — these read the
   descriptor and **reset nothing**. ⛔ **NEVER `esptool chip-id`** — it resets the board to connect,
   which is the exact harm the identity check exists to prevent.
2. **Disambiguate by FULL MAC, never a prefix and never a port.** Multiple boards share the
   `1C:DB:D4:xx` prefix; only the full serial separates the **ring-fenced** controller from a board
   you may work on. A cached port→board mapping once reset a ring-fenced controller — that incident is
   why this rule is absolute.
3. **A bridge (FTDI/CH340) exposes the ADAPTER's serial, not a board MAC.** You cannot identify what
   is on the far end passively, and opening an FTDI port asserts DTR/RTS and may **reset** whatever is
   attached. An unidentified bridge far-end is a **do-not-touch** until a human confirms it.

## The registry

> ⛔ **No real identity lives in this public file** (slice 7d). The identity column names the
> **host-overlay variable** that holds each value; the flash guard
> (`python/scripts/pio_flash_guard.py` → `python/composeio/flash_guard.py`) reads those variables and
> **refuses** when one is unset. A MAC quoted here would be a pseudonym that matches no real board,
> so a guard comparing against it would pass for the ring-fenced controller it exists to protect.

| Full MAC / serial | device_id | board | role / firmware | USB identity | on bench 2026-09-11? | status / ⛔ notes | source |
|---|---|---|---|---|---|---|---|
| on `FLASH_DENY_SERIALS` | `cio-ctrl-<last3>` | ESP32-C3 | throttle controller — reaches the bridge over **MQTT**, puts **no** frames on CAN | native-USB (MAC=serial) | **no** | ⛔ **RING-FENCED — NEVER TOUCH.** Shares the `1C:DB:D4` prefix with `c3lcd`; only the full MAC separates them. | CANV2-SWEEP `[CANV2-ROSTER]` |
| `C3LCD_EXPECT_SERIAL` | `c3lcd-<last3>` | ESP32-C3 SuperMini + ST7796 LCD + SN65HVD230 CAN | CAN/LCD node, **on CAN v2**, `listen-only`; fw `1b72183-dirty` (Aug 14 2026) | native-USB (MAC=serial) | **yes** | Transceiver **DRIVER is DEAD** (receiver OK) → invisible on the wire until the transceiver is replaced. `ns_announce` armed, blocked only by the dead driver. Role note: register list names `c3can-<last3>` but live fw announces `c3lcd-<last3>` — an UNMADE identity decision (`[CANV2-REGISTER-UNMADE]`). | CANV2-SWEEP; `composeio-can-bridge.env`; CAN-ACCEPTANCE-FILTER-RESULTS.md |
| `R503_EXPECT_SERIAL` | *(S3, unset)* | ESP32-S3 SuperMini | `reactive_core` build target / **R503-probe** dev board (`r503_probe/build.sh` and `env:fingerprint_s3` pin this as `R503_EXPECT_SERIAL`) | native-USB (MAC=serial) | **yes** | Role slightly ambiguous: `reactive_core` is the throttle-controller firmware tree and this S3 is its dev target + the R503 fingerprint probe. Confirm intended role before flashing. | `firmware/reactive_core/platformio.ini` (`env:fingerprint_s3`); `firmware/r503_probe/{platformio.ini,build.sh}` |
| `display-node.local` | `node-rpi5` | Raspberry Pi 5 | CAN bridge host — 2× MCP2515 (`spi0.0`/`spi0.1`) + `composeio-can-bridge.service`; `CAN_BRIDGE_NODE_ID=1` | host (mDNS name) | n/a (network) | Fleet's fixed infra CAN peer (`src=1`). `sjw 2` @ SP 0.875 on `can0`/`can1`. | CANV2-SWEEP `[CANV2-ROSTER]`,`[CANV2-BITTIMING]` |
| FTDI (adapter serial: host overlay; candidate for `FLASH_DENY_SERIALS`) | *(unknown)* | — | **UNIDENTIFIED far end** | FTDI USB↔serial bridge (adapter serial, not a MAC) | **yes** | ⛔ **NEEDS DAVID'S EYES — do not probe or flash.** Opening the port asserts DTR/RTS and may reset the far end. Flagged unknown since 2026-08-15; still unknown. | CANV2-SWEEP `[CANV2-UNPROVEN]`; bench scan 2026-09-11 |

## Conventions
- **`device_id = <board>-<last-3-MAC-hex>`** (e.g. MAC `…00:53:03` → `c3lcd-**005303**`). This is the
  fleet's existing naming rule (`cyd.descriptor.yaml`: `device_id = cyd-<last3 STA MAC hex>`), so the
  MAC is already the identity anchor — this table is the full-MAC → identity index over it.
- **Board type → bring-up detail** lives in the per-board `*.descriptor.yaml` in this directory; this
  registry indexes *physical units*, the descriptors specify *board types*.
- **Ports are not identity.** The `last-known port` cells are deliberately omitted here because they
  churn; resolve the port from the MAC at use time (`system_profiler` / `ioreg`), never the reverse.

## Maintenance — this is a LIVING table, populated as the fleet is touched
Update a row **every time a device is connected for a flash or bring-up**: read its MAC passively,
confirm or add its row (board, role, firmware SHA from the compiler-written stamp, status). The
device-baseline reflash campaign (`[SPEC:CXC-DEVICE-BASELINE]`, Phase C) is the natural moment to
fill this out fully — each board David reconnects → passive MAC read → a confirmed row here **before**
the flash. Keep it current the same way `[SPEC:CXC-BOARD-HW]` keeps pin maps current; a registry that
is confidently incomplete is worse than one that is honestly partial, so mark unknowns `EMPTY`/`unknown`
rather than guessing.

See also: `docs/board-bringup/RUNBOOK.md`, `[SPEC:CXC-BOARD-HW]`, the `*.descriptor.yaml` set,
`docs/can-v2-fleet-sweep-2026-08-15.lts`.
