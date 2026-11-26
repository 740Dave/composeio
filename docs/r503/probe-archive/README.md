# R503 bring-up probe

Instrumentation for taking the **first measurements ever made** of an R503 in this
project. Ruled at `[DD-FPID-NO-SERIAL-MOCK-BUILD-A-BRINGUP-PROBE]`: no serial-level
mock, a probe against the real part.

**Start here:** [`docs/bringup-wiring-2026-08-16.md`](../docs/bringup-wiring-2026-08-16.md)
— it carries the electrical rules that can destroy a part.

---

## What this is NOT

⛔ Not the HAL. Not `R503HAL`. Not a driver. Not `fingerprint_storage`. Not the
roster. `corpus/72-fingerprint-hal-contract.lts` is **UNRATIFIED and under David's
review**; nothing here implements it, imports its types, or pre-empts it. If the
probe's shape comes to resemble the HAL, that is convergence and not a claim.

---

## Two parts, and the split is not negotiable

```
   host (Mac)                    ESP32-S3 SuperMini              R503
  ┌──────────────┐   USB CDC   ┌────────────────────┐  UART   ┌────────┐
  │ r503probe.py │◄───────────►│ firmware/r503_probe│◄───────►│ module │
  │  exploration │  line proto │  THE WIRE LAYER    │ 57600   │        │
  │  session log │  (mine)     │  framing/checksum  │  8N1    │        │
  │  human Qs    │             │  drain/resync/     │         │        │
  └──────────────┘             │  every timeout     │         └────────┘
                               └────────────────────┘
```

**The wire layer MUST run on the S3**, not on the Mac with the S3 as a transparent
bridge. The R503's protocol is timing-coupled at the byte level and none of it
survives a USB hop:

- **inter-byte timeouts** — the only form that survives a variable-length data-packet
  burst, because the burst count is **not derivable from the command**: it depends on
  a negotiated packet-size parameter (`[CXC-R503-DATA-PACKET-BURSTS]`)
- **drain RX immediately before every transmit** — the *ordering* is the remedy for
  the unsolicited `0x55` (`[CXC-R503-UNSOLICITED-0X55]`), and it is only meaningful
  with the UART in hand
- **no end-of-stream marker** — the ack-stream terminator is *inferred*, so framing
  is stateful (`[CXC-R503-ACK-STREAM-BREAKS-REQ-RESP]`)

The link between host and S3 is a **line protocol of my own**, never raw R503 bytes.

---

## The host link protocol

```
host -> s3 :  "> <seq> <verb> [args...]\n"
s3 -> host :  "< <seq> <status> key=value ...\n"
s3 -> host :  "# <free text>\n"          diagnostics; the host IGNORES these
```

**Anything not starting with `<` is not a response.** A bootloader banner, a panic
dump or a stray byte is therefore harmless rather than a desync — the same lesson as
the sliding two-byte header window, one layer up. This is not theoretical: the ESP-IDF
second-stage bootloader prints six lines at every boot and the host discards all six.

### Verbs

| verb | does |
|---|---|
| `ping` | identity, compiler-written build stamp, **the GPIOs actually bound**, baud, loopback state |
| `pins <tx> <rx>` | retarget the UART **at runtime** — no reflash when the silkscreen disagrees |
| `drain [ms]` | drain RX and **report** what was there (this is where `0x55` shows up) |
| `listen <ms>` | capture unsolicited traffic **without transmitting** |
| `selftest` | 16 codec cases on the real silicon; needs no R503 |
| `fwver` | `GetFwVer 0x3A`, parsed |
| `prodinfo` | `ReadProdInfo 0x3C`, parsed, **bytes counted on the wire** |
| `led <ctrl> <speed> <colour> <times>` | `AuraLedConfig 0x35` |
| `cmd <hex> [deadline_ms] [interbyte_ms]` | **escape hatch** — arbitrary command content, framed properly |
| `badsum <hex> …` | same, transmitted with a **deliberately wrong checksum** |
| `rawtx <hex> …` | **verbatim bytes**, no header/length/checksum added — how a malformed stream gets onto real hardware |

---

## CLI

```bash
python3 probe/r503probe.py port      # resolve the S3's path from its USB SERIAL
python3 probe/r503probe.py selftest  # codec self-test on real silicon
python3 probe/r503probe.py negctl    # ⛔ negative-control battery, R503 UNPLUGGED
python3 probe/r503probe.py ping
python3 probe/r503probe.py listen 3000
python3 probe/r503probe.py --observer "David" ev25   # [CXC-R503-EV-25]
python3 probe/r503probe.py --observer "David" ev03   # [CXC-R503-EV-03] + [EV-15]
python3 probe/r503probe.py raw 3A --deadline-ms 500
python3 probe/r503probe.py rawtx EF01FFFFFFFF070003  # deliberately truncated frame
```

Requires `pyserial`. Nothing else.

---

## Rules this code enforces structurally, not by convention

⛔ **A device path is a LEASE, not an identity.** Every command resolves
`/dev/cu.*` from the USB **serial number** of the board named by `R503_EXPECT_SERIAL` **at use time**. The
path is never cached, never iterated over, and never written into a config.
`esptool chip-id` is **not** used for identification — it *resets the board*, which
is the exact harm an identity check is run to avoid. The USB descriptor read has no
side effects.

⛔ **No baud change exists anywhere.** No `SetSysPara 0x0E` helper in the firmware,
no subcommand in the CLI. 57600 is compiled in. Reaching a baud change requires
hand-assembling the bytes through `raw`, which is a decision rather than an accident.
`[CXC-R503-BAUD-SELF-CONTRADICTS]`: every revision contradicts itself on legal rates,
a shipped driver exposes seven forbidden values, and **no recovery from a bricked
module is documented anywhere.**

⛔ **No unbounded wait.** Every read carries a **deadline** *and* a **byte budget** —
the UART transaction, the drain, the listen window, the USB host link, and the CLI's
own read loop. A partial command line that stops arriving is discarded after 3 s.
The reported `elapsed_ms` is measured, and `FREERTOS_HZ` is raised to 1000 so that
number means what it says.

⛔ **An unknown confirmation code is DATA, never fatal.** `0x28` is used by the
vendor's own worked example and appears in **no revision's table**. Unknown codes are
reported as `conf_known=0 conf_name=UNDOCUMENTED` and the transaction still succeeds.
Transport success and command confirmation are **separate axes** and are reported
separately — a `conf` field is emitted **only** for an acknowledge packet (PID `0x07`),
because on a command packet that byte is the opcode, and naming it `conf` would be
asserting a confirmation code that does not exist.

⛔ **Nothing swallowed.** Every transaction reports `pre_drain` (bytes present before
the transmit) and `skipped` (bytes discarded by the sliding header window), with their
hex. A byte discarded silently is a measurement thrown away.

⛔ **A colour is never claimed.** A confirmation code of `0x00` means the command was
*accepted*, never that the intended colour *appeared*. `ev25` stops and asks a human
to **name** what they see — free text, blanks rejected — and **does not tell them what
was commanded**, because an observer told to expect green will see green. The mapping
is revealed only after all seven answers are recorded.

⛔ **A session that touched no hardware says so.** Every `ev25`/`ev03` log carries a
`provenance` record counting frames actually parsed from a module. Zero frames prints
a loud `NO MODULE EVER ANSWERED — DO NOT USE THIS SESSION TO FILL AN EVIDENCE SLOT`.

---

## Building the firmware

```bash
firmware/r503_probe/build.sh          # build
firmware/r503_probe/build.sh upload   # build + flash ONLY the board named by R503_EXPECT_SERIAL
```

⛔ **`build.sh` is not convenience.** This repo lives under `.../Data Sheets/R503`
and **`Data Sheets` contains a space**. PlatformIO refuses outright —
`Error: Detected a whitespace character in project paths` — because ESP-IDF's
CMake/ninja toolchain cannot build from such a path. The script mirrors the source to
`~/.cache/composeio/r503_probe_build` and builds there. **The mirror is one-way and
clobbering: edit in the repo, never in the mirror.**

⛔ **`build_flags` in `platformio.ini` do NOT reach ESP-IDF component compiles** and
are silently dropped. Compile-time switches live in `src/CMakeLists.txt` as
`target_compile_definitions(${COMPONENT_LIB} PUBLIC …)`. Verify against
`.pio/build/supermini_s3/compile_commands.json`.
