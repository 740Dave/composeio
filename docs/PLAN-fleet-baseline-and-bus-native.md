# Plan — clean fleet baseline, bus-native devices, config-over-bus proven

**Opened 2026-08-12.** Working list for David + the architect. Living document; agents
should read it before starting anything in scope.

Status key: **TODO** · **BLOCKED** (waiting on something named) · **DECIDE** (David's
call) · **DONE**

---

## Sequencing

**The fleet is flashed ONCE per device.** Everything that changes the wire or the
baseline lands before the reflash; everything that needs a fleet already speaking the
final wire format comes after.

```
  [F] wire format ─┐
  [B] UART policy ─┼──> [C] ONE fleet reflash ──> [D] config-over-bus test ──> [E] bus-native throttle
  [A] paho audit ──┘
        ▲
  [0] fleet inventory — gates C, because you cannot reflash what you cannot enumerate
```

---

## 0 · Fleet inventory — WHO IS ACTUALLY OUT THERE

**This gates C and it is not yet possible.** The scope is *every ComposeIO board built to
date*, not the subset that happened to be on the bench during one session. Two of them —
the fan-relay ESP32 and the Qualia/DRADIS board — have not been touched in 24 hours and
were missing from the first draft of this plan.

### What the repo knows

**Board profiles / flashable targets** (`docs/board-bringup/*.descriptor.yaml`,
`cyd/platformio.ini`): `cyd`, `qualia_display`, `hosyond_s3`, `waveshare_c6`,
`esp32_c3_st7796`, `esp32_can_relay`, `esp32_c3_can_bringup`, plus descriptors for
`esp32c3` and **`ch32v203`** — the last on a completely different toolchain (wchisp, no
ESP-IDF).

**Device definitions** (`compose_projects/**/device.yaml`):

| device | declared id | MAC | seen on bus (25 s) | notes |
|---|---|---|---|---|
| `office_fan` | `office_fan` | **TBD** | yes — 1 msg | ESP32 + fan relays, on the desk, untouched |
| `office_hepa` | `office_hepa` | **TBD** | no | idle or offline — unknown which |
| `dual_throttle` | `dual-throttle` | **TBD** | via `throttle/*` | ⚠ runtime id is `cio-ctrl-db1c`, **not** `dual-throttle` |
| `dradis_console` | `qualia-004df8` | `00:00:5e:00:53:04` | yes — 50 msgs | Qualia/DRADIS, untouched |
| `engine_thrust_panel` | `hosyond-080739` | `00:00:5e:00:53:01` | yes, over UART | live on the bench |

Plus, from tonight and not in any `device.yaml`: `wsc6-07f199` (Waveshare C6, offline,
**owes a reflash**) and `c3can-047dd8` (C3 Super Mini + ST7796S).

### The two problems

**P1 — the repo cannot identify half its own fleet.** Three of five records carry
`mac: "TBD"`, and `dual_throttle`'s declared id does not match the device actually
publishing. A reflash campaign driven off these records would target the wrong hardware or
skip it.

**P2 — the bus cannot enumerate anything.** A 25 s census of `composeio/#` found **zero
`_device/*` namespaces**. Nothing announces itself. There is no way to ask the bus what
exists, what firmware it runs, or whether it is alive — and `DISCOVERY` is *allocated* as
type code 5 in `[BP-TYPE-CODES]` and appears to be unused. The mechanism was designed and
never wired.

### Proposed design — device announcement

**David, 2026-08-12:** *"you should be able to poll the network, see what devices are on
it and get basic build info from them as well."*

**Announcement is primary; query is secondary.** Periodic announcement gives liveness for
free — a *missing* publication is itself the fault report, which query cannot provide
(silence from a query is ambiguous between "absent" and "did not hear"). This is the same
argument that made the bridge's 5 s table publication periodic rather than on-change.

**The wire carries fingerprints; the tooling resolves them.** This is the constraint that
shapes everything: a classic CAN frame is **8 data bytes**, so a human-readable build
string cannot go on the wire. Same principle already used for namespaces — compact on the
wire, registry elsewhere. It also means **one encoding on every substrate**, with no
transport-specific variant, which is the leak `DP-CIO-6` exists to prevent.

Each field is its own namespace and fits a single CAN frame:

| namespace | type | payload |
|---|---|---|
| `_device/<id>/heartbeat` | u32 | uptime seconds. **Presence is the liveness signal** |
| `_device/<id>/build` | u32 | CRC-32 of `env-name + __DATE__ + __TIME__` |
| `_device/<id>/transport` | u8 | active substrate + whether selected from compiled default or NVS |
| `_device/<id>/scene` | u16 | content hash of the running scene, 0 = none |
| `_device/<id>/health` | u32 | packed: free heap KB, reset reason, flags |

**Polling:** a single `_device/broadcast/announce = 1` makes every device republish its
full set immediately. One message, everyone answers — works identically on MQTT, UART and
CAN, and is exactly the "poll the network" ask.

**Cadence:** heartbeat every 5 s; the rest every 60 s and on change. For ten devices that
is trivial on MQTT and ~0.7 frames/s on a 500 kbit CAN segment.

**Why a build *fingerprint* rather than a string:** it fits in 4 bytes on every substrate,
it is exact (no truncation), and it makes the reflash campaign machine-checkable — flash,
poll, assert the fingerprint changed. The build system emits a fingerprint→string map so
tooling can print `Aug 12 2026 21:41:02` without the wire ever carrying it.

**Open:** whether to also use MQTT's Last Will and Testament for immediate death
detection. It is genuinely useful but MQTT-only, so it must be an *optimisation* over the
missing-heartbeat rule, never the mechanism — otherwise UART and CAN nodes get worse
liveness semantics than MQTT ones for no designed reason.

| # | task | status |
|---|---|---|
| 0.1 | Verify `DISCOVERY` (type code 5) is genuinely unused; if something publishes it, find out what and whether this design should use that type rather than STATE | **DONE — and the premise was wrong**, see below |
| 0.2 | **Baseline firmware announces itself** per the table above — the task that makes C verifiable | **DONE (host-verified; not yet on silicon)** |
| 0.3 | `_device/broadcast/announce` handler — poll-triggered republish | **DONE** |
| 0.4 | Build system emits the fingerprint→build-string map; tooling resolves it | **DONE** |
| 0.5 | `composeio fleet` command: poll, wait, print every device with build, transport, scene, uptime, health | **DONE** |
| 0.6 | Fill every `mac: "TBD"`, and reconcile `dual-throttle` vs `cio-ctrl-db1c` | TODO |
| 0.7 | Single source of truth for the fleet — replace five `device.yaml` files + eight descriptors + tribal memory with something queryable | TODO |
| 0.8 | Register the announcement namespaces in `CAN_BRIDGE_REGISTER` | **CLOSED, and NOT the way it was written** — see below and workstream G |

### 0.1 answered — `DISCOVERY` is NOT unused, and this design should not use it

Type code 5 is live: `ComposeApp._publish_discovery()` (`python/composeio/app.py`) and
`Bus.register_module()` (`python/composeio/bus.py`) both publish `MessageType.DISCOVERY`
on **`_bus/discovery`**, carrying a `data` dict of a module's exported namespaces.
`composer/discovery.py` consumes it. It is a **module-namespace-export EVENT**, not a
device-presence value.

So the announcement fields are **STATE**, not DISCOVERY, and that is the right call for
a reason beyond "the code was already taken": these five namespaces are *current values a
late subscriber must be able to read*, not events. What genuinely IS the DISCOVERY shape
is workstream **G2** — a node announcing its namespace NAMES. 0.2 and G3 remain one piece
of work, exactly as this plan said; they just sit on different message types.

### 0.8 — a prefix rule is not merely missing, it is impossible here

The 16-bit wire id is a hash of the **full** namespace name including the device id, so
`_device/*` cannot be expanded without already knowing every device id — the thing being
discovered. Hand-typed entries for one known board were **deliberately not added**: a
census that is confidently incomplete is worse than one that is honestly empty (the same
class of error as the `filtered` counter that could not increment). The reason is recorded
in `deploy/can_mqtt_bridge/composeio-can-bridge.env` and `composeio fleet` states the CAN
gap in its own output. **G1–G3 is what closes it.**

**Closed 2026-08-13 by G1–G4 — and the task as written stays refused.** No announcement
namespace was added to `CAN_BRIDGE_REGISTER`, and none ever should be: those entries are
per-device-id and would make the census confidently incomplete. What landed instead is one
hardcoded pair (`_device/announce` = 0x31AE) plus learning, so `_device/<id>/*` needs
**zero** hand-typed entries on any board that announces. A task can be right about the goal
and wrong about the mechanism; this one was.

### What landed for 0.2–0.5 (2026-08-13)

- `cyd/src/announce_pack.h` — encoding + field table, pure C++, no substrate named.
- `cyd/src/announce.{h,cpp}` — shared app-shell handler: cadence, poll, sampling. Wired
  into `main.cpp` (cyd / hosyond_s3 / waveshare_c6 / esp32_c3_st7796) and `qualia_main.cpp`.
- `BusTransport::link_health() / link_has_errors() / link_listen_only()` — the seam that
  lets `_device/<id>/health` carry the generic half of H2a's TWAI instrument with **no
  per-transport code in the announcer**. Default is `unknown`, never `ok`.
- `cyd/tools/gen_build_stamp.py` + `cyd/build_fingerprints.json` — per-build fingerprint,
  regenerated on **every** `pio run`, confined to one 40-line TU so it cannot go stale.
- `composeio fleet` (`python/composeio/fleet.py`), `python/composeio/device_announce.py`.
- `pio run -e native_announce` — 247-check firmware/Python parity over a packing corpus.
- `python/tests/render/smoke_device_announce.py` — positive AND negative control with an
  independent raw-socket subscriber.

**Two things a reader must not over-read.** (1) The 32-bit words ride as 8 hex digits
because the framed wire has no int32/float32 discriminator today — so they are 9 bytes and
**do not fit a classic CAN frame**; the CAN transport refuses them loudly. Flip
`announce.cpp::publish_u32` to a real integer when **F3** lands. (2) Nothing here has run
on silicon yet: no board on the bench could be flashed without disturbing live hardware.

**0.2 is the one that makes C tractable.** Once every board announces its build stamp, a
reflash campaign is checkable: flash, then read the bus and confirm the stamp changed.
Without it, verifying a fleet-wide flash means physically visiting every device — and a
stale build tag already survived ~10 flashes across three boards on 2026-08-11.

**Also unknown and worth an explicit sweep:** whether any board exists that is in *neither*
the descriptors nor the `device.yaml` set. The fan-relay board was missing from this plan's
first draft precisely because it was in neither the session's working memory nor a query.

---

## A · Devices talking MQTT directly instead of over ComposeBus

**Goal:** nothing above the transport layer speaks paho. A device on the bus is a bus
participant, not an MQTT client that happens to be nearby.

There are already guard tests — `python/tests/render/test_no_raw_paho_outside_transport.py`
and `test_sources_have_no_paho.py` — so the interesting question is not *whether* things
bypass the bus but **why these escaped an existing guard.**

| # | task | status |
|---|---|---|
| A1 | Read both guard tests; establish exactly what paths they cover and what they exempt | TODO |
| A2 | Triage the three candidates: `python/composeio/adaptors/wled/senders.py`, `compose_projects/bsg/world/publish.py`, `compose_projects/bsg/game_hardware/gm_mcp/publisher.py` | TODO |
| A3 | Confirm the legitimate exceptions and write down *why* they are legitimate | TODO |
| A4 | Port each real violation onto `BusTransport` | TODO |
| A5 | Widen the guard so the escape route is closed, not just the escapees | TODO |

**Legitimate exceptions, stated so nobody "fixes" them:** `transport_mqtt.py` IS the
transport. `services/can_mqtt_bridge` and `services/uart_mqtt_bridge` are **bridges** —
they translate between two live substrates and must hold both. That is a different thing
from a Transport, which lets ONE app speak ONE substrate. `node_modules/` is not ours.

**Not yet known:** whether `bus_client.py` is transport-layer or an app-level bypass.
A2 answers it.

---

## B · UART baked into the baseline firmware, forever

**Goal (David, 2026-08-12):** *"our baseline firmware now and forever more should have
UART support baked in, unless we have to waive it for extremely slow/limited MCUs."*

**Mostly already true.** `bus_uart.cpp` compiles alongside `bus_mqtt.cpp` and
`bus_can.cpp` in every board branch of `cyd/src/CMakeLists.txt`. This is therefore about
making an accident into a **policy**, and proving no env quietly omits it.

**WORKSTREAM B IS DONE — 2026-08-13**, branch `feat/uart-baseline-policy`, unpushed.
Full audit and rationale in `docs/POLICY-uart-baseline.md`; corpus node
`[SPEC:CXC-UART-BASELINE]` in `corpora/platform-specs/65-uart-baseline-policy.lts`.

**Waiver floor: `< 8 KB RAM OR < 32 KB flash`**, measured from the real object file
(6,391 B text + 3,149 B bss, plus a 1 KB RX ring and 4 KB task stack ≈ 6.4 KB flash /
8.7 KB RAM). **Only the CH32V003 (2 KB / 16 KB) qualifies.**

⚠ **The CH32V203 is NOT waivable — it is 2.5× above the floor.** This plan previously
implied it was the boundary case; that was wrong, and it matters: workstream J proposes the
CH32V203 as a CAN host with a UART leaf, so **waiving UART there would break the mothercard
topology outright.**

| # | task | status |
|---|---|---|
| B1 | Audit every env — **24 audited, ZERO violations.** 9 bus-bearing envs carry UART; 2 exempt; 13 `native_*` out of scope and *stated* rather than skipped. Verified by building 11 envs on a scratch copy and testing for `bus_uart.cpp.o`, not by reading | **DONE** |
| B2 | Build-time assertion — delivered as `docs/patches/uart-baseline-guard.patch` (`git apply --check` clean), **not applied** because two agents owned the target files. **Proven firing on two branches.** A `cmake_language(DEFER)` call, so it covers the board branch nobody has written yet with no allow-list | **DONE — patch awaits apply** |
| B3 | Waiver criterion — see above | **DONE** |
| B4 | Corpus record — 59 keys authored / 59 minted / 0 lost | **DONE** |
| B5 | **Apply the patch** once `platformio.ini` / `CMakeLists.txt` are uncontended | TODO |

**Known limitation, reported not papered over:** a waiver **cannot be made loud in the
console.** `message(WARNING)`/`(STATUS)` are swallowed on a successful configure, and
`POST_BUILD` never runs because PlatformIO uses CMake only to extract config and then builds
with SCons. The waiver instead mints a greppable `CXC_UART_WAIVED_BUILD=1` definition.

**And a waiver is not merely "drop the file":** `bus_transport_select.cpp`'s static registry
references `bus_uart_transport()`, so an ESP-IDF board without it **fails at link.** The
linker is a second, independent objection.

**Why it matters:** a board with UART compiled in can be recovered, reconfigured and
re-pointed over three wires with no IP address. A board without it is only as reachable
as its radio.

---

## C · Clean baseline burned onto every device

**Goal:** every device in the fleet running the same, current, verified baseline.

**Do not start until 0, A, B and F have landed.** Scope is *every board built to date* —
see workstream 0 for why the list below cannot yet be trusted to be complete.

| device | id | transport today | state |
|---|---|---|---|
| Hosyond ESP32-S3 | `hosyond-080739` | UART, radio down | live on the bench; config-over-bus proven cold-boot |
| Waveshare C6 | `wsc6-07f199` | offline | **owes a reflash + runtime re-verify** — build-verified only |
| C3 Super Mini + ST7796S | `c3can-047dd8` | CAN, listen-only | listen-only is a WORKAROUND for a dead transceiver, not a design |
| C3 dual-throttle | `cio-ctrl-db1c` | USB serial → Mac app | see E; ⚠ live infrastructure |
| **Qualia / DRADIS** | `qualia-004df8` | on the bus | **untouched in 24 h**; builds correctly now but still runs an older binary |
| **ESP32 fan relays** | `office_fan` | on the bus | **untouched**; MAC unrecorded; publishes ~1 msg/25 s |
| **ESP32 HEPA** | `office_hepa` | unknown | **not seen on the bus** — idle or offline, unknown which |
| CYD | — | — | build-verified only, not on the bench |
| CH32V203 | — | — | different toolchain (wchisp); descriptor exists, status unknown |

⚠ **This table is not yet known to be complete.** It is assembled from five `device.yaml`
files, eight descriptors and one session's memory. Workstream 0 exists to replace it with
something queryable.

| # | task | status |
|---|---|---|
| C1 | Back up current firmware for every device before flashing | TODO |
| C2 | Reflash each; **read the boot banner back** and confirm the build stamp changed | TODO |
| C3 | Re-verify each on the glass where it has one — not from a capture, see below | TODO |
| C4 | Clear the C3's stale NVS `transport=can` left from an injection test | TODO |
| C5 | Establish whether `office_hepa` is idle, offline or gone | TODO |
| C6 | Decide whether the CH32V203 is in scope — different toolchain, and the UART waiver in B may apply to it | DECIDE |

**A stale build tag survived ~10 flashes across three boards on 2026-08-11.** The boot
banner carries `__DATE__ __TIME__`; read it back every time.

---

## D · Config-over-bus for a UART leaf behind a CAN-connected host

**Goal (David):** *"we never sorted out the issues with a UART-slaved device to a host
with ComposeBus/CAN requesting and getting a fresh YAML. Or we might have built that but
I'm not sure we were able to test that. Clarity either way is important."*

### The clarity, stated plainly

**BUILT. NEVER RUN ON HARDWARE. And it cannot work today** — two defects block it, both
found 2026-08-12.

**Proven:** config-over-bus over **UART**, end to end, twice. A leaf with no IP address
requests its scene, reassembles 5,856 bytes in 25 frames, verifies a content hash, ACKs,
and renders — including from a genuine cold boot on independent power
(`17:06:16 CONFIG_REQUEST → 5856 B, hash 6f62, 62 ms → ACK ok`). What made that work is
that the **head had an IP** and dereferenced `bus_storage` on the leaf's behalf.

**Built but never executed on silicon:** the relay — `relay_main.cpp`, `config_relay.cpp`,
the TTL ladder, `[CXC-CONFIG-BUS-RELAY]`. CAN upstream, UART downstream, forwarding chunk
by chunk and reassembling nothing. Host-tested against the Python oracle only.

**Two blockers:**

| id | defect | consequence |
|---|---|---|
| **F1** | `config_bus.cpp:159` builds the ACK as `"ok %04x %u"` → `"ok beef 5856"` = **13 bytes** on the wire. `bus_can.cpp:263` refuses anything over 8. | A CAN config transfer **cannot be acknowledged**. `acks_ok`/`acks_bad` are structurally incapable of incrementing. The leaf applies the scene and the head never learns. There is no ACK timeout, so nothing detects the silence. **FIXED 2026-08-13 (D1)** |
| **F2** | `BusMessage.to_json()` puts raw `bytes` in `val`; `json.dumps` raises `TypeError`. | `CONFIG_PUSH` cannot traverse the MQTT↔CAN bridge in **either direction**. **FIXED 2026-08-13 (D4)** — it now raises `UnserialisableValue`, a named `TypeError` that names the namespace, the field and the remedy. Bulk is not *made* to traverse MQTT: `ConfigServer.for_transport` binds to a framed transport and refuses a JSON one. |

Related, same area: `ConfigServer.on_message` only tested `text.startswith("ok")` and
**never compared the returned hash against the one it sent** — so even on UART, "ok" plus
the wrong hash was accepted as success. **FIXED 2026-08-13 (D2).**

| # | task | status |
|---|---|---|
| D1 | Make the ACK **binary**, not a string: `[tag\|status][hash u16][len u16][ackseq u16]` = 7 bytes, fits with a byte spare and is machine-comparable | **DONE (host-proven; not on silicon)** |
| D2 | Compare the ACK's hash against the hash sent; stop accepting `"ok"` on faith | **DONE** |
| D3 | Bind `ConfigServer` to a `CANTransport` **directly** — it already takes a `send` callable and a `chunk_payload`. Bulk must not route through MQTT; JSON and opaque chunk bytes are incompatible by design, not by bug | **DONE** — `ConfigServer.for_transport`; an MQTT transport is REFUSED with the reason |
| D4 | Fix `to_json()` to fail loudly rather than at a random call site | **DONE** |
| D5 | **Test on hardware**: CAN host → relay → UART leaf → rendered scene | BLOCKED — needs a working CAN transceiver (see E2) and a relay node |
| D6 | **Corpus is now BEHIND the code**: `corpora/platform-specs/61-config-over-bus.lts` line 124 still specifies the ACK as `ok <hash4> <bytes>` / `mismatch <got4> want <want4>`. The corpus is owned by another agent in this tree and was deliberately not edited | TODO |

### What landed for D1–D4 (2026-08-13), `feat/config-ack-binary`

**The ACK is 7 bytes:** `[0xA0|status][hash u16 BE][total_len u16 BE][ackseq u16 BE]`.
Statuses 0 ok / 1 hash-mismatch / 2 incomplete / 3 no-memory. The **tag nibble** is the one
deviation from the proposed layout and it costs nothing (status needs 4 bits): a bare
status byte of 6 in a 7-byte value is read by `transport_uart.decode_value` as a
length-prefixed **string**, so the head would have gone back to comparing text. Pinned at
`0xA0` the first byte is always above any length this codec emits, and a receiver can
*positively identify* a config ACK instead of parsing whatever seven bytes arrived.

**`hash` always names the TRANSFER the head announced**, never the corrupt value a mismatch
computed — so the head's check is one comparison for every status. The leaf's computed hash
stays in the leaf's log; spending a wire field on a number the head cannot act on is how
8 bytes becomes 9.

**`ackseq` is defined once and ported verbatim**: on success the last correctly received
sequence, otherwise the **first missing** one, with **0 meaning nothing was missing** — which
on a mismatch is real information, because it separates corruption from loss. It gave
`Reassembler.missing()` its first caller since the format was written.

**Three failures the head could never previously hear are now reported:** a hash mismatch,
an **abandoned** transfer (a header arriving mid-transfer now ACKs the transfer it just
killed, so the "unexplained second `CONFIG_REQUEST`" has a diagnosis), and an
**out-of-heap** leaf (which used to ACK nothing at all). The leaf also counts ACKs the
transport **refused** — a listen-only node dropped every ACK it built and said nothing.

**Proven, host only:** `pio run -e native_config_chunk` 613 checks (was 568) compares the
firmware ACK codec byte-for-byte against the Python oracle over 10 encode vectors + 6
must-reject vectors; `env:native_relay` 43 checks now carry a **real** binary ACK and assert
it crosses byte-for-byte; `python/tests/test_config_ack.py` 43 tests drive the ACK through
the **real** `CANTransport` frame path — including a test that the retired 13-byte string
ACK is *dropped* by that same path. All 24 platformio envs build; every native selftest and
`tests/peer` (107) still pass. **Nothing has run on silicon** — see D5.

**Scale warning for D5.** `CAN_CONTENT_BYTES` is 6, so the 5,856-byte scene that took
**25 frames over UART takes 977 over CAN.** Recovery is whole-transfer — a
`Reassembler.missing()` gap iterator exists and **has no caller** — so one lost frame
costs all 977. Measured: unpaced without ENOBUFS retry, **0/20 transfers complete**, every
one dying at frame ~12 against a 10-deep TX queue. Paced at 1 ms, 20/20. **Pacing is not
optional, and it is not flow control** — see the CANopen SDO block-transfer note in the
teardown brief.

---

## E · Bus-native throttle controller

**Goal (David):** *"a fresh build of the throttle controller that is not slaved to the
throttle app on this mac — it should reside on the ComposeBus directly, and the throttle
app on this device should be modded to respond off the bus."*

**Why it matters, in his words:** *"It's very powerful to have multiple screens side by
side all reacting to the throttle levers."* Today every consumer depends on one Python
process on one Mac holding one USB serial device. That is the single point of failure
this whole architecture exists to remove.

Confirmed 2026-08-12: `composeio.render.engine.runtime … calib_throttle.compose.yaml
--transports`, running since 28 Jul, owns the serial link and republishes to MQTT. Unplug
the controller from the Mac and the levers stop existing as far as the bus is concerned.

| # | task | status |
|---|---|---|
| E1 | Choose the controller's transport — WiFi/MQTT is simplest; CAN is the better story but needs a transceiver | DECIDE |
| E2 | Fit and verify a working CAN transceiver (two dead modules so far) | TODO |
| E3 | Build + flash a bus-native firmware for `cio-ctrl-db1c` | BLOCKED on E1, C |
| E4 | Modify the Mac's `calib_throttle` app to **consume** from the bus rather than own the device | TODO |
| E5 | Prove it: unplug the controller from the Mac; levers still drive every screen | TODO |
| E6 | Multi-screen demo: Hosyond + C3/ST7796S + C6 all tracking the same levers | TODO |

⚠ `cio-ctrl-db1c` is **live infrastructure** right now. It is the data source for every
test on the bench. Do not reflash it until there is a tested replacement path.

---

## G · Bidirectional registry sync — no silent failures at the bridge

**David's framing:** *"bi-directional sync between CAN and MQTT/UART so that there's no
silent failures."*

**The insight that makes it tractable:** the bridge never needs to *reverse* a hash — it
needs to have **seen the name once, from anywhere**. Observing a name on MQTT populates
`name→hash` for the outbound path and `hash→name` for the inbound path in the same act.

### Already shipped (2026-08-12, deployed on rpi5)

- Self-populating registry from observed MQTT traffic — 50 namespaces learned with zero configuration
- **Registry ≠ permission**, enforced in code: `observe_ns` learns, `_forwarding_decision` governs, sharing no state. Without this separation anything able to publish to MQTT could put traffic on a shared 500 kbit segment — the registry would have become an authorization oracle by accident
- Deny list with `fnmatch` patterns (`dradis/*`), replacing the allowlist
- `#` subscribe so every filtering decision happens **in-process where it can be counted**, rather than at the broker where the old `filtered` counter was structurally pinned at 0
- Collision refusal — incumbent keeps the hash, newcomer refused, because the bridge is the chokepoint
- Drop, never truncate
- Per-namespace drop census + name→hash table published every 5 s

### The gap: CAN-originated names

A namespace that has **never appeared on MQTT** cannot be learned by observation. Today
that means hand-typed `CAN_BRIDGE_REGISTER` entries — nine of them were typed by hand
during the C3 bring-up — and a missing one reports only an irreversible hash. **That is
precisely the silent failure this workstream exists to remove**, and it goes stale the
moment a device is added.

**A prefix rule (`_device/*`) is not sufficient**, because the hash is over the *full*
name including the device id. `_device/*` cannot be expanded without already knowing every
device id — which is the thing we are trying to discover.

### Proposed: name announcement, with one well-known root

A node announces its **namespace names** over whatever substrate it has, chunked via
`config_chunk` (substrate-independent, sized for CAN's 8 bytes, already built and
host-proven). The bridge learns `name→hash` from that, populating both directions —
genuinely bidirectional, no configuration.

**Bootstrap:** the announcement itself needs a namespace the bridge already knows, so
exactly **one well-known namespace with a fixed hash** is pre-registered in every bridge
and every node — `_device/announce`. Everything else is discovered. Same shape as a DNS
root or a DHCP broadcast address: one hardcoded entry, and the rest is learned.

**This is the same mechanism as workstream 0.** A device announcing itself announces its
*names* first, then publishes *values* against them. 0.2 and G3 are one piece of work.

| # | task | status |
|---|---|---|
| G1 | Allocate the well-known `_device/announce` namespace + fixed hash; pre-register in every bridge and node | **DONE (host-proven)** |
| G2 | Firmware: emit a chunked name announcement on boot and on `_device/broadcast/announce` | **DONE (host-proven, NOT flashed)** |
| G3 | Bridge: learn `name→hash` from CAN-side announcements, exactly as it learns from MQTT | **DONE (host-proven)** |
| G4 | Persist the learned table across bridge restart, and keep the 5 s publication as the audit channel — persisted learning can persist a mistake, so it must stay visible | **DONE (host-proven)** |
| G5 | Retire the hand-typed `CAN_BRIDGE_REGISTER` list once announcement works; keep it as a fallback for nodes that cannot announce | **BLOCKED ON A REFLASH** — see below |

### What landed for G1–G4 (2026-08-13), `feat/wellknown-announce-root`

- **`python/composeio/ns_announce.py`** — the root (`_device/announce` = **0x31AE**,
  poll `_device/broadcast/announce` = **0xC77C**, both PINNED and verified against
  `namespace_hash` at import), the payload format (newline-separated full ASCII names),
  `AnnounceLearner` (per-`src` reassembly over `config_chunk`), and the persisted-table
  reader/writer.
- **`cyd/src/ns_announce.{h,cpp}`** — the firmware half. The wire-visible math is
  header-only and pure C++ so it host-parity-tests; `.cpp` holds only pacing and
  publishing, and names no substrate (it asks `BusTransport::max_raw_value()` for the
  chunk size). Wired into the shared app shell via `announce.cpp`, so **every** board
  inherits it — added to all five `CMakeLists.txt` source blocks beside `announce.cpp`.
- **`services/can_mqtt_bridge/bridge.py`** — the root registered **at import**, not in
  `main()` and not by any variable; announcement frames routed to the learner and
  **consumed, never relayed**; names fed through the *same* `observe_ns` the MQTT path
  uses, so collision refusal and permanence are identical whichever substrate a name
  arrived on. Provenance (`wellknown`/`builtin`/`env`/`mqtt`/`can`/`persisted:*`) on
  every row, the whole table published on `_bridge/<id>/forwarding`, and
  `resolved_after_announce` records what the gap cost in dropped frames.
- **`python/composeio/transport_can.py`** — the same learning on the fleet tool's own
  path, so `composeio fleet --transport type=can` can name a device with no bridge and
  no broker anywhere.
- **`tests/peer/test_can_announce_root.py`** — 29 tests. Includes the **negative
  control** (the same frame, unresolvable and loudly counted, *before* the
  announcement), a mutation-checked restart test, and one assertion per census counter
  because this repo has shipped four counters structurally incapable of incrementing.
- **`pio run -e native_announce`** — grew from 247 to **495 checks**: the pinned hash is
  compared against *three* independent sources (the C++ literal, `bus_ns_hash()`, and
  the Python literal), and every announcement frame is compared **byte for byte** to the
  Python oracle's. That last check caught a real divergence the first time it ran —
  Python collapsed duplicate names and the C++ builder repeated them, which would have
  produced a valid-looking announcement with a CRC the sender never sees.

**What is NOT proven: anything on silicon.** No board has been flashed. The chain that
*is* proven is: firmware frame bytes == Python frame bytes == the bytes the real bridge
learns from. What that cannot reach is a transceiver, bit timing, arbitration, or a TWAI
controller refusing a frame under load.

**G5 is a reflash task, not an edit.** The nine hand-typed `c3can-047dd8` entries stay in
`composeio-can-bridge.env` until that board is flashed with `ns_announce` — removing them
first would un-name a node that works today to prove a point about a mechanism it does not
have. Delete them in the same change that flashes it.

**The honest limit is unchanged and is now also enforced.** Announcement does not add
bits. What it adds is that a collision — an announced name landing on an incumbent's hash
— is **refused and published** rather than silently decoded as the wrong namespace. A
second limit is new and specific: `_device/announce` is a **broadcast** root, so two nodes
announcing at once interleave, and with `src` unallocated (0 for everyone) the receiver
cannot demultiplex them. It detects and refuses (`interleaved`, `mismatch`, both counted
and warned) rather than assembling a plausible table, the firmware jitters its boot
announcement by a device-derived 0–2000 ms, and the real fix is **node-id allocation** —
which the test asserts works, so the recommendation is measured rather than assumed.

**Honest limit, unchanged:** none of this adds bits. The 16-bit hash is still 16 bits —
2.3% collision probability at the 56 namespaces on the bus today, 7.3% at 100, 39% at 256.
Adaptivity converts collisions from *silent and wrong* into *visible and refused*, which is
the difference between a corrupted control surface and an alarm. It does not buy headroom.

**The endpoint, when the table is dynamic and published:** stop hashing and **allocate**.
Wire ids need only be unique among namespaces crossing *this segment*, so a registrar can
assign them and distribute the mapping through the same announcement channel — collisions
impossible by construction rather than merely detectable. The trade is a stateful segment
(a node that misses the broadcast cannot decode until the next one, bounded by the 5 s
interval). Not now; recorded because every layout decision in F should avoid foreclosing it.

---

## F · Wire format — one coordinated breaking change

**Already authorised by David.** Three independent things ride on this single reflash,
and the expensive thing is not the change, it is a *second* change later.

1. **Explicit priority field.** Today `ns_hash` occupies the top 16 bits, so arbitration
   priority is a pseudo-random function of the namespace *name*. `throttle/left` (0x03F6)
   wins everything **by luck**; `_device/wsc6-07f199/config` (0x8921) outranks
   `_device/hosyond-080739/config` (0xC6B6) because of a MAC suffix; `shields` (0xCC50) is
   the lowest-priority thing on the bus.
2. **`scope` preservation** — silently lost on every CAN round trip today. The ratified
   relay semantics *require* scope; a relay that cannot see it cannot implement them.
3. **Value type tag** — `DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9`. int32 and float32
   both encode to 4 bytes with **no discriminator**, so every decoder guesses. The canon
   guesses float (`1000` → `1.4e-42`, `-5` → `nan`); the bridge guesses int (`42.0` →
   `1109917696`). Negative integers are wrong in **both**. Costs **one bit**; the UART
   `type_scope` byte has **two spare**.

Recommended layout, from the CAN specialist's teardown — sums to 29:

```
prio(3) | type(4) | ns_id(16) | src(4) | scope(2)
```

**Dropped from the earlier proposal:** renumbering the type codes by urgency. It breaks
UART for a property UART cannot use (point-to-point has no arbitration), welds identity to
urgency permanently, and **cannot solve within-class priority by construction**. The
priority field makes it unnecessary.

| # | task | status |
|---|---|---|
| F1 | Priority tiers — **RATIFIED 2026-08-13**, named by urgency class. See Open Decisions | **DECIDED** |
| F2 | Decide on 7-byte chunk framing (CANopen-style) | DECIDE — untouched by F3; chunk content is opaque bytes and carries no value tag |
| F3 | Implement across `bus_can_id.h`, `transport_can.py`, the bridge's `can_id_from_message`, the corpus generator and parity test | **LANDED 2026-08-13**, `feat/wire-format-v2`, **NOT FLASHED, NOT DEPLOYED** |
| F4 | Reconcile `[BP-CAN-ID-DIVERGENCE]` — two layouts in one repo that disagree about what arbitration is *for* | **CLOSED by F3** — one layout, pinned by `test_the_bridge_and_the_transport_emit_the_same_id` and by a textual guard against the v1 shifts |

**F3 as built.** `prio(3)|type(4)|ns_id(16)|src(4)|scope(2)`, exactly as ratified.
Priority is a **rule with a default** (`composeio.transport.priority_for`): control-plane
types take their tier from `MESSAGE_TYPE_PRIORITY`, application data from an ordered
prefix/exact rule list, and anything unlisted gets tier 5 (live telemetry) — below every
control-plane tier, so an unclassified namespace can never starve an ACK. `dradis/*` is
deliberately absent from the rules: it lands on the default *by rule*, and a rule that
restates its own default is a list that goes stale. `register_namespace_priority()` is the
seam workstream 0 announces into. The firmware mirrors the same tables and the same
matcher (`bus_priority_for`), parity-tested both as tables (`env:native_wire_codes`) and as
110 evaluated answers (`env:native_can_id`).

**One deviation from the plan text, stated out loud.** The value type tag costs **one byte
on wide numerics**, not one bit: `[0xFE][int32]` / `[0xFF][float32]`. The plan costed it at
one bit in the UART `type_scope` byte, which works for UART and **cannot** work for CAN —
the ratified id sums to exactly 29 with nothing spare, and a CAN data-side flags byte would
cost a byte anyway *and* shrink `CAN_CONTENT_BYTES` from 6 to 5. Two mechanisms on two
substrates is the fork this codebase has paid for twice. One in-band mechanism also makes
`decode_value(encode_value(x)) == x` a property of the codec **alone**, which is what the
ten acceptance tests actually assert. `type_scope` bits 1..0 stay spare. Small ints (all
live throttle traffic) and strings are byte-identical to before.

**FLAG DAY.** An untagged 4-byte value is now `Raw` / raw bytes, not a guessed float32 —
so a peer that has not been reflashed is *visibly* undecodable rather than invisibly wrong.
Every node moves in one coordinated reflash. **Nothing has been flashed or redeployed.**

---

## H · Observability and diagnostics — from the CAN specialist teardown

An independent CAN specialist reviewed the binding on 2026-08-12
(`docs/can-binding-teardown-brief.md`). The layout findings are folded into F. These are
the rest, and every one of them is a fault that is currently **invisible** rather than
absent.

### H1 · `restart-ms 100` is silent recovery in a codebase that forbids silent recovery

This repo refuses to truncate a value, guess a namespace, or downgrade a message type —
all on the principle that silent degradation is worse than loud failure. `restart-ms 100`
does the forbidden thing one layer down: on a **persistent** fault (bitrate mismatch,
dominant-stuck driver, a terminator that fails at temperature) it produces a 10 Hz
bus-off/restart cycle that presents as **intermittent flakiness rather than a fault**.

The reviewer's assessment: *"the single best generator of 'mysterious delays in how
different devices react' in the whole system."*

| # | task | status |
|---|---|---|
| H1a | Set `restart-ms 0` on at least the witness interface, preferably everywhere, with application-level recovery that announces | TODO |
| H1b | If auto-restart is kept, publish `bus_off` and `restarts` from `ip -details -statistics` continuously — otherwise recovery is invisible by construction | TODO |

### H2 · A bus-off ESP32 reports `connected` forever

`cyd/src/bus_can.cpp` never calls `twai_get_status_info()`, never enables
`TWAI_ALERT_BUS_OFF`, never calls `twai_initiate_recovery()`. **ESP-IDF's TWAI driver does
not auto-recover** — on TEC ≥ 256 it enters BUS_OFF and stays there. Meanwhile
`connected()` returns `s_started`.

Ask the question the discipline demands: *what would this instrument show if the thing
were broken?* The same. **It is not an instrument.**

Worse, the diagnostic actively misdirects. In BUS_OFF, `twai_transmit` fails immediately
and `send_id` logs *"this most often means no other node is present to ACK, or the
transceiver is not powered / not terminated"* — sending the operator to the wiring for a
node in a protocol state, holding an error counter that would have told them the truth.
**Observed live on 2026-08-12**, and only visible because the bring-up agent built a
separate instrument.

| # | task | status |
|---|---|---|
| H2a | Publish TWAI `state`, TEC, REC, `tx_failed_count`, `rx_missed_count`, `bus_error_count` on `_device/<id>/health` — folds into workstream 0 | TODO |
| H2b | Make `connected()` reflect the actual peripheral state, not `s_started` | TODO |
| H2c | Implement bus-off recovery, **announcing** — never silently | TODO |
| H2d | Fix the misdirecting log line: distinguish "never started" (`TEC=0`, `msgs_to_tx` pinned) from "transmitted and something came back wrong" (`TEC` climbing) | TODO |

### H3 · Bit timing is asserted, never measured

"Sample point 0.875" is one number attached to **two controllers with different clock
trees**. ESP32-C3 TWAI is likely ~80%; the MCP2515 targets 87.5% but with an 8 MHz crystal
gets only 8 TQ/bit, so **SJW ≤ 1 TQ — almost no resynchronisation margin**. A 4–7%
sample-point difference between two nodes is survivable but it is not nothing, and it is
exactly the configuration that produces *"works against node A, sporadic stuff errors
against node B."*

**MEASURED 2026-08-13 — the Pi half is done, and SJW is worse than predicted.**

Both `can0` and `can1` are identical: `clock 8000000`, `brp 1`, `tq 125`, `prop-seg 6`,
`phase-seg1 7`, `phase-seg2 2`, `sjw 1`. That is **16 TQ/bit** (1 sync + 6 + 7 + 2) × 125 ns
= 2 µs = 500 kbit/s ✓, sample point (1+6+7)/16 = **0.875**, exactly as asserted.

**But SJW = 1 of 16 TQ = 6.25% of a bit, and `mcp251x` allows `sjw 1..4` — so it sits at
the MINIMUM.** The teardown feared "SJW ≤ 1 TQ ≈ 12.5%" on an assumed 8 TQ/bit; the real
figure is **half that margin**. Both Pi channels being identical is why Pi↔Pi ran 500/500
clean — **the exposure is Pi↔ESP32**, which is exactly the link about to carry real traffic.

Cheap mitigation to consider once H3b lands: raising `sjw` toward 4 costs nothing but a
`ip link set` argument in `can0.service`, and buys 4× the resynchronisation window.

| # | task | status |
|---|---|---|
| H3a | Pi bit timing recorded — see above | **DONE 2026-08-13** |
| H3b | Read the actual `TWAI_TIMING_CONFIG_500KBITS` values from the IDF that builds the firmware; compare sample points and SJW | TODO |
| H3c | Identify the HAT's MCP2515 crystal (8 vs 12 MHz) — it changes both figures materially | TODO |

### H4 · `can1` is not a passive witness

Unless explicitly in `listen-only`, `can1` **hardware-ACKs every frame it receives**. It is
a full bus participant. Every result tonight that depended on ACK behaviour was measuring
a **three-node bus**, and no report stated the mode.

| # | task | status |
|---|---|---|
| H4a | Every harness result must state `can1`'s mode explicitly | TODO |
| H4b | Decide per-test whether the witness should ACK. Note the trap: in listen-only on a two-node bus, the transmitter goes error-passive and every result changes | DECIDE |

### H5 · Do not adopt the ENOBUFS retry loop

Measurement showed ENOBUFS-retry makes the 977-frame transfer succeed. **The bridge
refuses to retry, in writing, for a good reason:** at 98.8% refusal the loop spins ~85
times per delivered frame, burning a core and converting a *measured drop census* into
unbounded, unmeasured latency for **every other namespace that process forwards**.

Someone will resolve that contradiction the lazy way. The correct answers are **pacing**
(receiver-declared, per ISO-TP's `STmin`) or a **queueing discipline** — never retry.

Related: **no arbitration layout can fix head-of-line blocking inside one node's queue.**
Raising `txqueuelen` to cure ENOBUFS converts loss into latency at a terrible rate — 3.2 ms
at 10, 320 ms at 1000. `txqueuelen 10` is not a misconfiguration; it is accidentally
correct, and it is why ENOBUFS is *visible*. If class separation is needed, get it from
separate sockets with `SO_PRIORITY` under a `prio` qdisc.

| # | task | status |
|---|---|---|
| H5a | Leave `txqueuelen` at 10; record why, so nobody "fixes" it | TODO |
| H5b | Receiver-declared pacing for bulk transfer (ISO-TP `STmin` shape), not a sender constant | TODO |

### Corrections to the record

- The brief's "12 bits at 56 namespaces gives ~53%" was the **11-bit** figure. 12-bit is **31.5%**. Conclusion unchanged (still disqualifying), number wrong.
- The stress harness's `type-first` layout **had no `scope` field**, so it was not measuring proposal 5a. Contention results stand; the label did not.
- `bus_can_id.h`'s host-parity claim covers **20 of 29 bits**. The corpus deliberately treats the source field as "this side's own choice," so the two implementations are *known* to disagree there and the test is built not to notice. Documented and defensible — but "host-parity-tested" should say which bits.
- Peak bus utilisation of **27% was the 1 ms-paced run**, not a saturated transmitter. A saturated MCP2515 sits at **~52%**, and at **≤262 kbit/s it saturates the segment on its own**.

---

## I · New device classes — and the architectural question they force

Five pieces of hardware to fold into ComposePlatform. Two are inputs, **three are outputs
with no pixels**, and that last fact is the one that matters.

| device | class | notes |
|---|---|---|
| **R503 fingerprint sensor** | input — identity events | UART packet protocol |
| **Adafruit 4×4 matrix keypad** | input — *stateful* entry | jump coordinates: a multi-key sequence, not a value |
| **TM1637 4-digit LED** | output — **7-segment**, ×1 and ×4 | 4 digits × 7 segments. No pixels |
| **`rack_triple_gauge`** (custom PCB) | output — **needle position** | ESP32-C3 Super Mini + MCP23017 (I²C) → 3× L293D → 3 stepper motors, JST-XH 4-pin |
| **`lego_dots`** (custom PCB) | output — **12 LED channels** | ESP32-C3 + 12-channel BC337 driver |

Both PCBs are **script-generated** (`generate_pcb.py` + gerbers, **no `.kicad_sch`**), so
pin assignments must be read from the Python generator, not a schematic. Both are
ESP32-C3 — the board already brought up twice on 2026-08-12, so the profile, build env and
`sdkconfig.c3.defaults` all exist.

### The question: ComposeUX assumes a pixel Canvas. These have none.

A TM1637 has four digits and seven segments each. A stepper gauge has a needle angle. A
BC337 channel has a brightness. **None of them can consume drawing primitives.**

**But ComposeIO's binding model fits all of them perfectly** — namespace → value → device
property is exactly what they need. So the gap is not in the bus, it is in where ComposeUX
lets a backend attach.

**Proposed framing: the extension point for non-raster devices is ABOVE the Canvas, at the
widget-value level — not a new Canvas backend.** A `SegmentBackend` cannot implement
`draw_rect`; asking it to is the wrong shape. But a scene with three gauge widgets whose
*values* map to three stepper positions preserves the entire `.compose.yaml` authoring
model and needs no rasterizer at all.

That keeps one authoring language across the fleet — the same scene grammar drives a 480×320
panel, three needles, or twelve LEDs — and it is consistent with ComposeUX's stated goal of a
renderer-agnostic engine. It just means the *renderer* boundary is not always the Canvas.

**The forcing function is the multi-surface node.** Four TM1637s on one device each showing
different bus data; three independent needles; twelve independent channels. **The scene model
currently assumes one surface per device.** Whether that becomes "one virtual scene, N physical
outputs" or "N independent bindings, no scene" is the decision — and 4× TM1637 is the cheapest
place to find out, because it is the smallest device that genuinely has four of something.

### I1 is settled — confirmed by David, 2026-08-13

*"rack_triple_gauge exists to take a semantic message, such as the % of engine thrust, and
to move an analog gauge to the corresponding value."*

That is the **widget-value binding**, not a Canvas. And the vocabulary already has the
concept: `GaugeWidget` supports a **`dial`** style alongside `led_bar`. **A stepper needle
IS a dial gauge** — same widget, same semantic value, same `.compose.yaml`. The only thing
missing is a backend that maps the widget's value to a motor position instead of pixels.

**The abstraction is not missing. Only the backend is.**

**But a widget needs more than ONE output channel.** `rack_triple_gauge` has three 12 V
PWM lines lighting the three gauges — **per-gauge illumination, not per-board.** On the
ST7796S, backlight is a *device* property (`set_backlight()`, one GPIO, unrelated to any
widget). Here it is a physical actuator belonging to a specific widget, and the raster
equivalent has no counterpart — on a screen, a widget's brightness is just its pixel colour.

So the backend contract should map **a widget to a set of NAMED CHANNELS**, not to a single
value. A dial gauge exposes `value` *and* `illumination`; the stepper backend maps one to
motor position and the other to a PWM duty cycle. **Decide this before the first backend
ships** — retrofitting a second channel later means touching every one of them.

**Pin headroom comes from the I²C expander.** Three steppers wired direct would need 12
GPIOs; through the MCP23017 it is 2. That is the opposite of the Super Mini + ST7796S,
where the panel consumed every free pin. Rough budget on a C3 Super Mini: 13 exposed, less
2 for I²C, 3 for backlight PWM, 2 for the console UART, 2 more if CAN is fitted — roughly
four left, with the strapping caveats on GPIO2/8/9. Enough for an R503 (UART, 2 pins) or a
TM1637 (CLK/DIO, 2 pins). **Worth recording as a fleet pattern: an I²C expander buys pin
headroom on pin-starved MCUs.**

**The demo this unlocks:** one `throttle/left` namespace driving an LED bar on the ST7796S,
a physical needle on the rack gauge, and a 7-segment numeral on a TM1637 — **simultaneously,
with no device aware of any other.** One semantic value, three physical modalities. That is
the platform thesis in hardware, and all three are ESP32-C3.

| # | task | status |
|---|---|---|
| I1 | Non-raster extension point = **widget-value backend** | **DECIDED** |
| I2 | Decide how a multi-surface node is expressed (4× TM1637 is the cheapest forcing case) | DECIDE |
| I3 | TM1637 driver + bus binding, single unit | TODO |
| I4 | TM1637 ×4 in series, each bound to a different namespace | BLOCKED on I2 |
| I5 | `rack_triple_gauge` bring-up: MCP23017 → 3× L293D → steppers. Backend detail now that I1 is settled, but still needs zeroing, travel limits and step rate | TODO |
| I5b | **Three 12 V transistor/GPIO-switched PWM lines** for the LED strips that illuminate the gauges — per-gauge, not per-board | TODO |
| I5c | `rack_triple_gauge` as a **composite node**: gauges + illumination + an input device on the spare GPIOs (R503 and/or TM1637) | TODO |
| I6 | `lego_dots` bring-up: 12 BC337 channels, PWM or on/off — decide which | TODO |
| I7 | Extract pin assignments for both PCBs from `generate_pcb.py`; author board profiles + descriptors | TODO |
| I8 | 4×4 keypad: driver, plus a model for **stateful entry** — a jump-coordinate sequence is a state machine, not a namespace value. Decide whether that state lives on the device or in a widget | TODO |
| I9 | R503 fingerprint: UART packet driver, publishes match events | TODO |

### Two things to settle before building

**Stateful input has no model yet.** Every input on the bus so far publishes a *value* — a
throttle position, a button state. A keypad entering `1-2-3-ENTER` is a **session**: partial
state, a commit, and a cancel. That is a genuinely new shape and I2/I8 should not paper over
it with per-key namespaces.

**Biometric identity on an unauthenticated bus.** The R503 publishes "fingerprint matched."
Anything that can publish to the bus can publish that message. For a game console that is
fine and worth saying out loud; the moment it gates anything real it is not. Decide the
intended trust level **before** it is wired to anything, not after.

**Steppers need a position model.** Unlike every output in the fleet, a stepper has no
absolute feedback — it needs zeroing at power-on, travel limits, and a step rate. Nothing in
the current widget vocabulary expresses "sweep to zero, then track." That is real design, not
a driver.

---

## J · DESIGN PATTERN — Software Defined Hardware daughtercards

**David's framing, 2026-08-13:** *"a base daughtercard that speaks i2c or UART to a parent
node that sits on the ComposeBus, not unlike `pcb/button_matrix_4x4`, but with a better
processor... we're basically taking a page from Software Defined Radio innovations and
creating Software Defined Hardware on the daughtercard."*

**The pattern already exists in the fleet.** `pcb/button_matrix_4x4`:

```
Host ESP32 --UART--> CH32V003 --I2C--> MCP23017 U1 (buttons)
                               |--I2C--> MCP23017 U2 (LEDs)
```

The analogy is apt. SDR replaced fixed-function RF chains with *(minimal analog frontend +
general processor + software)*. This replaces bespoke peripheral wiring with *(minimal
driver frontend + general MCU + software)*.

### Why this is the same seam we already committed to

`DP-CIO-6` says **transport is an implementation detail** — an app binds namespaces and
does not know whether it is on MQTT, UART or CAN. This says **peripheral is an
implementation detail** — a parent binds channels and does not know whether they are
steppers, 7-segments or LED strips. Same move, one layer down.

**The economic argument:** today every new peripheral means new firmware *on the parent*,
so the parent's baseline grows with the peripheral catalogue and every bring-up risks the
node that is already working. With a daughtercard the parent speaks ONE protocol forever.
**Peripheral drivers move off the critical path.**

### What makes it "defined" rather than merely "wired"

**A daughtercard that needs bespoke parent firmware has bought nothing.** The card must
**announce its own capabilities** — channel names, directions, types, ranges — and the
parent binds to what it finds.

**That is the same announce-then-bind pattern as workstreams 0 and G, at a third level:**

| level | who announces | to whom | mechanism |
|---|---|---|---|
| device → bus | a node | the fleet | `_device/<id>/*` (workstream 0) |
| namespace → bridge | a node | the CAN↔MQTT bridge | `_device/announce`, chunked (workstream G) |
| **daughtercard → parent** | **a card** | **its host node** | **capability descriptor over I²C/UART** |

One pattern, three levels. Whatever we build for 0 and G should be reusable here — and if
it isn't, that is a signal the design is too specific.

### What it buys, argued from tonight's evidence rather than theory

**1. It protects the parent's real-time budget.** We spent tonight taking bus→glass from
460 ms to 65 ms, then immediately found a periodic `config_fetch` stalling the loop.
Stepper stepping, matrix scanning and WS2812 bit-banging are exactly that shape: periodic,
timing-critical work that injects jitter into a render loop. **Off-board, the parent's loop
stays deterministic.**

**2. It solves pin starvation properly.** The MCP23017 on `rack_triple_gauge` turns 12 GPIOs
into 2. A *programmable* card generalises that — the expander stops being dumb.

**3. It removes three of the four fault classes we hit tonight.** All four failures were
physical: a missing ground between breadboard and HAT; CRX on the wrong GPIO; unbonded
castellated joints that read continuous on a meter; a dead transceiver. **A PCB with a
connector eliminates the first three by construction.** Hours went into diagnosing faults
that a board would have made impossible.

### When to suggest a PCB — trigger criteria

Recording these so the suggestion is made on evidence rather than taste:

- **fewer than 2 spare GPIOs**, or a peripheral that would consume the last ones
- **timing-critical bit-banging** that would compete with a render or bus-drain loop
- **the same wiring repeated on ≥ 2 boards** — that is a product, not a one-off
- **multiple voltage domains** (12 V PWM, 5 V transceivers, 3.3 V logic) — level shifting on a breadboard is where the silent faults live
- **a rig that must survive being moved**, or that has already cost a session to a physical fault

### Link protocol — and it recapitulates ComposeBus one level down

| | I²C | UART |
|---|---|---|
| pins for N cards | **2 total** | 2 **per card** |
| addressing | built in | none — needs framing |
| event notification | master-polled → **needs an INT line** | native |

**Recommend I²C plus a shared interrupt line.** Multi-drop for several cards on two pins,
with INT for input events — precisely the pattern the FT6336 touch controller already uses
on the Hosyond. Note the daughtercard link is *itself* a bus with the same problems the
ComposeBus solved: addressing, discovery, event notification. Do not re-invent them
differently at this level.

### Processor choice

| candidate | for | against |
|---|---|---|
| CH32V003 (today) | cheap, proven on `button_matrix_4x4` | **~2 KB RAM** — too small for self-description plus several peripherals |
| **RP2040** | **PIO is literally programmable I/O state machines** — WS2812, stepper stepping and matrix scanning become PIO programs. The hardware embodiment of the SDH thesis | external flash, larger BOM |
| CH32V203 | already has a fleet descriptor; cheap | no PIO equivalent; hand-rolled timing |
| ESP32-C3 | same toolchain as the parents, one build system | overkill, and it wants to be a bus node itself |

**Lean RP2040 if the card is meant to absorb arbitrary timing-critical peripherals** —
which is the whole thesis. PIO is the difference between a card that supports the
peripherals we anticipated and one that supports the peripherals we haven't met.

| # | task | status |
|---|---|---|
| J1 | Choose the daughtercard processor | DECIDE |
| J2 | Choose the link protocol (recommend I²C + shared INT) | DECIDE |
| J3 | Define the capability descriptor — reuse the 0/G announcement design, do not fork it | TODO |
| J4 | Base daughtercard schematic + PCB | BLOCKED on J1, J2 |
| J5 | Retrofit `button_matrix_4x4` onto the new card, as the reference implementation | TODO |
| J6 | Record the trigger criteria in the corpus so future intelligences suggest a PCB on evidence | TODO |
| J7 | ~~Inventory `pcb/`~~ — **done**, see below | DONE |

### What `pcb/boards-overview.lts` already establishes (David, 2026-07-18)

Read it before doing any of J. It characterises **four** real boards against
`[SPEC:CXC-HAL-DECLARE]` (spec 32) and its nine interface kinds, from KiCad extraction +
generator source + engineering logs. It is ahead of this plan on several points.

**`can_controller` already exists, and it is the internal-bus building block:**
CH32V003F4P6 + MCP2515 (SPI) + SN65HVD230 + switchable 120 Ω, `J_CAN` = JST-XH 4-pin
carrying **CANH / CANL / GND / +12 V** — **power and data on one cable**, which is exactly
what a mothercard→daughtercard trunk wants.

**Stress S1 already recommends CAN-as-internal-transport**, in July, unprompted:

> *"CAN here is a TRANSPORT / bus-of-buses... it carries semantic messages between peers, it
> does not publish a physical quantity... model CAN as a **transport option on the
> internal↔ComposeBus bridge**, not (or as well as) a peripheral KIND."*

**None of the four is a dumb daughtercard** — all have an MCU able to run ComposeIO
firmware (CH32V003 ×2, ESP32-C3 ×2). The SDH topology is already the fleet's actual shape;
J is about making it *deliberate and self-describing* rather than per-board.

**The five HAL stresses are the real design backlog**, and two of them match what this plan
derived independently tonight — which is corroboration, not duplication:

| | stress | status here |
|---|---|---|
| **S1** | CAN is a transport/membrane, not a kind. *"The single biggest break."* | = David's internal-bus proposal. See scope note below |
| **S2** | MCP23017 is a **pin multiplier**, not a sensor — on **3 of 4 boards**. *"Half the fleet's I/O is unexpressible."* | the other coverage break |
| **S3** | **Compound peripherals** — `gauge = {needle + backlight}` is ONE object, declares as scattered entries | **= this plan's "named channels per widget"** (I1) |
| **S4** | **Stepper/position actuator has no kind** — `motor/{speed,direction}` exists, `motor/position` does not | **= this plan's stepper model gap** (I5) |
| **S5** | no array sugar; inter-MCU UART is a transport, not a peripheral | cosmetic + a miniature of S1 |

### CAN as the INTERNAL bus — and `scope` is the mechanism

**David, 2026-08-13:** *"there's no reason why CAN can't be used for a mothercard to talk to
a series of daughtercards — not for the entire composebus, but scoped to the internal bus
managed by the ComposeBus resident mothercard."*

**The scoping mechanism already exists and is already ratified.** `scope` (local / global /
remote / auto) is a field in every message, and the 2026-08-10 decision holds that **a child
device is inside the host's locality**, and that a relay must respect scope on the publish
path or it leaks local chatter upstream. **An internal CAN segment IS a locality.**
`scope: local` stays on the internal trunk; `scope: global` crosses to the ComposeBus.

**This promotes workstream F from cleanup to prerequisite.** `scope` is *silently lost on
every CAN round trip today* — so an internal CAN segment cannot currently distinguish local
chatter from bus traffic, and a mothercard would leak everything upstream. **F's scope
preservation is load-bearing for the mothercard architecture**, not a tidiness fix.

**And the relay is already a mothercard.** `relay_main.cpp` is a node holding two transports
and forwarding between them — CAN upstream, UART downstream. A mothercard with an internal
CAN trunk is that same node with CAN on both sides. The code shape exists; what is missing is
scope enforcement and the daughtercard's self-description.

**Why CAN beats I²C for the trunk** (revising the I²C recommendation above for the
*cabled* case): I²C is an on-board protocol — no noise immunity, no error detection, no
arbitration priority, and it is notoriously unreliable over cable. CAN is differential, has
CRC and automatic retransmission, arbitrates by priority so a keypad event can outrank a
display refresh, and tolerates hot-plug. **Use I²C for on-card expanders (as all four boards
already do) and CAN for the card-to-card trunk.** That also means one wire format at both
levels — the mothercard speaks ComposeBus-over-CAN upstream and ComposeBus-over-CAN
downstream, on separate segments.

### Concrete conflict to resolve before revising `rack_triple_gauge`

`rack_triple_gauge` as built uses **GPIO4 = SDA, GPIO5 = SCL** to the MCP23017, and
**GPIO0 / 1 / 3** for the three backlight PWMs. The fleet's CAN convention is **GPIO5 = CTX,
GPIO6 = CRX**. **GPIO5 collides.** Either the board revision moves I²C, or that node's CAN
pins differ from the fleet default — and if they differ, it must be in the descriptor and in
NVS (`can.tx` / `can.rx` already exist for exactly this).

| # | task | status |
|---|---|---|
| J8 | Fold `pcb/boards-overview.lts` into the main corpus, or cross-reference it — it is currently the only place the fleet's PCB landscape is written down | TODO |
| J9 | Resolve S1 in spec 32: CAN as a **transport on the bridge**, with `scope` as the local/global boundary | BLOCKED on F |
| J10 | Resolve S2: a **pin-expander / "port provider" driver role** — breaks real coverage on 3 of 4 boards | TODO |
| J11 | Resolve the `rack_triple_gauge` GPIO5 conflict before any board revision | DECIDE |

---

## Open decisions — David's

**F1 · Priority tiers — RATIFIED by David, 2026-08-13.** 3 bits, 8 tiers.

**Tiers are named by URGENCY CLASS, not by example.** David's correction: an earlier draft
listed `throttle/*` alongside `ACK` and `HEARTBEAT`, mixing **namespaces** with
`[BP-TYPE-CODES]` **types**, which made the table a list rather than a rule. *"Would it be
better to have it be a string that indicates time-sensitive semantic messages, with
'throttle' as a namespace within tier 1?"* — yes. **A tier named by example goes stale the
moment someone adds a namespace; a tier named by its class tells you where the new thing
goes.** Same disease as `CAN_BRIDGE_REGISTER`, three `mac: "TBD"` records, and an allowlist
that could not count its own drops.

| tier | class | assigned by | examples |
|---|---|---|---|
| **0** | **alarm** — a human reacts; late is *wrong* | namespace | `alert_status`, `shields` |
| **1** | **live control** — closed-loop, lag is visible | namespace | `throttle/*` |
| **2** | **transaction control** — terminates something, must not be starved by what it terminates | type | `ACK`, `CONFIG_REQUEST`, flow control |
| **3** | **time sync** — small, and its value IS its timeliness | type | `TIME_SYNC` |
| **4** | **liveness & coordination** | type | `HEARTBEAT`, `DISCOVERY`, `BINDING` |
| **5** | **live telemetry** — late is stale, not wrong | namespace | `dradis/*` |
| **6** | **display state** | namespace | `_app/console/bbox` |
| **7** | **bulk** — nothing functional may live here | type | `CONFIG_PUSH`, `LOG_ENTRY` |

**The assignment rule:** control-plane messages take their tier from their **type**;
application data (nearly all of which is `STATE`) takes it from its **namespace**. A new
namespace is placed by asking *which class is this*, never *what is it like*.

**Why this composes with workstream 0:** because the tier is a property of a semantic
class, a device announcing a namespace can declare its tier — so the assignment travels
with the thing rather than living in a table someone has to maintain.

**Keep tier 0 nearly empty.** If everything is urgent, nothing is.

**Test for any future placement:** *if this message were 30 ms late, would a player notice —
and notice it as a bug, or as the game?*

**F2 · 7-byte chunk framing.** CANopen puts a 7-bit sequence + last-segment flag in one
byte, leaving **7 content bytes instead of 6**: 977 frames → **838** (−14%), wire
efficiency 36.6% → 42.7%, and go-back-N repair fits in a single ack frame. Cost: re-port
`config_chunk.cpp` and regenerate the parity corpus. Recommended, since the reflash is
happening anyway.

**E1 · Throttle controller transport.** WiFi/MQTT is simplest and works today. CAN is the
better architectural story and the one your IP-exhaustion concern points at — but it needs
a transceiver that works.

---

## Known defects, recorded and deliberately unfixed

| defect | why not fixed |
|---|---|
| ~~`DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9`~~ | **FIXED 2026-08-13 in F.** In-band `[0xFE]`/`[0xFF]` value tags + the bridge's second decoder deleted. All 20 cases in `python/tests/test_value_codec_type_ambiguity.py` pass; the 10 strict-xfails were deliberately un-marked. **Not yet on any board.** |
| ~~`can_mqtt_bridge.message_from_can` missing the float32 branch~~ | **FIXED** by deletion — it imports `decode_value` now. Two decoders cannot disagree if there is one. |
| `bus_can.cpp` never calls `twai_initiate_recovery()`; `connected()` returns `s_started` | **A bus-off ESP32 reports CONNECTED forever.** Instrument built on the C3; recovery not implemented — see H2 |
| `restart-ms 100` masks persistent faults as intermittent flakiness | See H1 — a policy decision, not a bug, but it contradicts the codebase's own stated ethic |
| Bit timing asserted as 0.875 across two differently-clocked controllers | See H3 — free to measure, never measured |
| SN65HVD230 driver dead in **two** CJMCU modules | Input proven clean at chip pin 1; three instruments agree on zero output. Replacement parts being soldered |
| `yaml_min` sizes its arena from raw file length including comments | Shared code; 3 of 4 affected boards not on the bench |
| `config_cache_store` will write a blob larger than its partition | Same |
| ENOBUFS caught as a generic exception, logged "CAN write error" | `txqueuelen` is 10; raising it converts loss into head-of-line latency (320 ms at 1000). Measure before changing |
| Deployed bridge still honours the deprecated `CAN_BRIDGE_ALLOWLIST` | Retire when F lands |
| `deploy/can_mqtt_bridge/` + `CAN_BRIDGE_REGISTER` edits live on rpi5 but uncommitted | Ownership was never claimed by any agent |

---

## Standing verification rules

**A machine signal that agrees with you is not proof.** 2026-08-11/12 produced at least
eight: a `capture.bmp` that rendered perfectly over a byte-swapped panel because it decoded
its own assumption; an SPI overclock where every timing number improved while the display
got worse; `MCP2515 successfully initialized` with the interrupt on the wrong GPIO; a
dropped-frame counter structurally pinned at zero; a listen-only sweep that read identically
for "wrong bitrate" and "receive pin not connected."

- **Colour order, endianness, GRAM offset, brightness and latched external state need
  eyes.** Ask about SPECIFIC colours — "blue shows green, green shows red" is a *byte*
  swap; a *channel* swap shows blue as red.
- **Prove the substrate before the protocol**, and prove **power at the part** before
  either. On 2026-08-12 three rounds of protocol diagnostics ran against a transceiver
  that had no supply.
- **Port paths are not identities.** They changed five times in one session. Match the
  full USB serial; never `esptool chip-id` — it resets the board.
- **One node talking to itself proves the software and the adapter, nothing about
  interoperation.**
