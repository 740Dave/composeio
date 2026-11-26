# Runbook: bringing a new display board into ComposeUX

**Audience: a future intelligence with no context on this project, holding an unfamiliar board.**

Follow this end to end. It is written from the boards already in the tree — classic CYD, Qualia,
Hosyond ESP32-S3, Waveshare ESP32-C6 — and every rule below was paid for by a specific defect, cited
so you can judge whether it still applies rather than obeying it blindly.

The governing spec is `[CXC-BRINGUP]` in `corpora/platform-specs/31-composeux-board-bringup.lts`; the
board seam is `[CXC-ER-BOARDS]` in `29-composeux-embedded-reactive.lts`. Query them with
`lts_find` / `lts_node` — don't read whole corpora.

---

## The one architectural rule

**A new board is a new PROFILE, not a new rasterizer.**

Everything above the board seam — `main.cpp`, the canvas, the scene builder, every widget, the capture
server — is shared and stays untouched. Your job is to add a `BoardProfile` (`cyd/include/board_profile.h`)
and a build env. If you find yourself editing a widget or the `Canvas` contract, **stop** — you have
crossed the narrow waist, and the answer is almost always to extend the *backend* instead.

Precedents to read before writing anything, in this order:

| board | why you'd read it |
|---|---|
| `boards/classic_cyd.cpp` + `ili9341.cpp` | the reference SPI band-tier profile; the performance floor |
| `boards/hosyond_s3.cpp` | SPI on an S3, PSRAM full-frame tier, I²C capacitive touch |
| `boards/waveshare_c6_lcd147.cpp` | ST7789, RISC-V, non-240 panel width, no touch |
| `boards/esp32c3_st7796.cpp` | ST7796S, the widest panel (480) on the smallest RAM; band tier under a CAN transport |
| `boards/qualia_rgb666.cpp` | a genuinely different architecture (RGB dot-clock scan-out) |

Each profile **owns its display driver**. Do not refactor two panels into one parameterized driver
mid-bring-up: the boards you'd regress are usually not on the bench.

---

## Phase 0 — Before you touch the board

1. **Find the vendor kit.** Ask where it is. It is the authoritative source, and it is worth more than
   any datasheet: it contains the *pin map as wired*, the *init byte table as shipped*, and often a
   complete demo project.
2. **Prefer demo SOURCE over PDFs.** On both recent boards the ESP-IDF demo's headers gave the exact
   pins, SPI host, clock and init sequence in minutes. The PDFs took longer and said less.
   *Read from the kit before inventing anything — but never port a demo into the tree. It is reference,
   not architecture.*
3. **BACK UP THE FACTORY FLASH FIRST.** Before the first write.
   ⛔ **Every esptool command resets the board it connects to** — run the identity gate on the port
   first, every time. It reads the USB serial passively and refuses unless the host overlay sets
   `FLASH_DENY_SERIALS` and the port is not on it (`pio run -t upload` runs the same gate itself,
   through `python/scripts/pio_flash_guard.py`). Public code names no device identity.
   ```bash
   python3 python/composeio/flash_guard.py check --port <port> || exit 1   # [--expect-var <BOARD>_EXPECT_SERIAL]
   esptool.py --port <port> flash-id                       # get the size first
   esptool.py --port <port> read-flash 0 <size> factory.bin
   ```
   Verify it (`0xE9` at `0x0`, `0xAA50` at `0x8000`, `0xE9` at `0x10000`), record a SHA-256 alongside,
   and store it in `docs/board-bringup/factory-images/`. **The `.bin` is gitignored** — commit the
   `.sha256` and a README row.
   *Why: the Hosyond's BOOT button is not brought out, so firmware that breaks USB enumeration has no
   manual recovery. High baud can fail — 921600 broke that board; use the default.*

---

## Phase 1 — Identify the silicon, then decide the tier

```bash
esptool.py --port <port> flash-id     # chip revision, features, crystal, flash size, MAC
```

Two properties decide almost everything downstream:

**PSRAM?**
- *Yes* → the full-frame tier is available: set `CXC_BAND_HEIGHT` to the panel height so the scene is
  walked **once** per frame instead of once per band. Allocate the buffer in PSRAM
  (`canvas_esp32.cpp` prefers PSRAM above 32 KB).
- *No* → the band tier. Leave `kBandHeight` at 16.

**FPU?** Check the ISA. Xtensa cores have single-precision FP; **RISC-V RV32IMAC does not.**
- *No FPU* → the canvas is float-heavy and every float is soft-emulated. This does not forbid the board;
  it changes which *scene primitives* are affordable. Measured on the C6: dial gauges **892.9 ms/frame**
  vs `led_bar` rectangles **31.2 ms** — a 28× swing from scene authoring alone.
  *Do not "fix" this by de-floating the shared canvas. Choose primitives.*

Also record, because a silently-occupied pin is a bug waiting to happen: does the SD card share the
display's SPI bus? Is there a latching RGB LED? Where does the panel's reset come from?

---

## Phase 2 — Write the two files

**`cyd/include/<board>_panel.h`** — the panel constants block. *Every* board-specific number lives here
and only here: pins, SPI host and clock, geometry, MADCTL, inversion, backlight polarity and limits, the
init byte table (as a reviewable data table, transcribed from the vendor init, with any deviations
commented), and any GRAM offset. Retune this file, rebuild — never chase constants through the driver.

**`cyd/src/boards/<board>.cpp`** — the profile: display driver, touch driver or `nullptr`, geometry,
name. Follow the nearest precedent's shape.

---

## Phase 3 — Build wiring, and the trap that will get you

Add a branch to `cyd/src/CMakeLists.txt`, an env to `platformio.ini`, and an `sdkconfig.<board>.defaults`.

> ### ⚠ `platformio.ini` `build_flags` NEVER reach ESP-IDF component compiles
>
> For `framework = espidf` they are **silently dropped**. Verified against
> `.pio/build/<env>/compile_commands.json`: only `ESP_PLATFORM`, `IDF_VER` and `SOC_*` survive.
>
> This has caused two real defects. A board rendered in 16-line bands while its config claimed full-frame
> and every test passed; and `config_fetch.cpp`'s device-id prefix, gated on a `build_flag`, made a board
> announce itself as `cyd-…`, 404 its scene, and silently fall back to another board's dashboard.
>
> **Every compile-time switch must be `target_compile_definitions(${COMPONENT_LIB} PUBLIC …)` in the
> CMake branch.** Verify it landed:
> ```bash
> python3 -c "import json;d=json.load(open('.pio/build/<env>/compile_commands.json'));\
> print(sum('YOUR_DEFINE' in (e.get('command') or ' '.join(e.get('arguments',[]))) for e in d))"
> ```

Checklist for the branch:
- [ ] `CXC_DEVICE_PREFIX="<short>"` — else the board answers to `cyd-` and cannot own a scene config.
      Keep it short; `device_id()`'s buffer is `char[16]`.
- [ ] `CXC_BAND_HEIGHT=<height>` **only** if the board has PSRAM.
- [ ] For a **non-`esp32` target that drives glass**, place the branch **above** the
      `if(NOT IDF_TARGET STREQUAL "esp32")` early-return — that guard exists to keep Xtensa display
      sources out of DSL-only RISC-V builds.
- [ ] Partition table and flash size match the silicon.
- [ ] Native-USB boards: `CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y`.

---

## Phase 4 — Machine verification

Flash, then confirm on serial:

- [ ] board profile name and `Panel: WxH <shape>`
- [ ] `Device id: <prefix>-<mac3>` — **the prefix is yours, not `cyd-`**
- [ ] display driver ready line: geometry, MADCTL, inversion, SPI clock, backlight
- [ ] `Band-rendered surface: … band=N scanlines` — **N proves `CXC_BAND_HEIGHT` reached the compile.**
      A dropped define falls back to 16 and everything still "works"
- [ ] PSRAM boards: the buffer is in PSRAM, and the driver resolved its DMA route deliberately
- [ ] touch controller ACK + chip id, if fitted
- [ ] config fetched, scene built, bindings and subscriptions non-zero
- [ ] `GET /capture.bmp` returns a well-formed image

**Then stop and read the next section, because you are not done and the remaining signals will lie to you.**

---

## Phase 5 — The human tier, and why it is not a formality

`[CXC-BRINGUP]` demands two tiers and the second is not optional. The canonical failure: the classic CYD
rendered flawlessly — every SPI write `ESP_OK`, `capture.bmp` advancing — over **a panel frozen on frame 0**.

The Waveshare C6 bring-up produced **four** defects caught by eyes and **none** by instrumentation. In
every case a machine signal existed and *agreed with the wrong answer*:

| defect | what the machine said | why it couldn't know |
|---|---|---|
| RGB565 **byte swap** (ST7789 `RAMCTRL` ENDIAN bit) | `capture.bmp` rendered perfectly | the capture decodes the same buffer with the same endian assumption it was written with — the assumption was read back to itself |
| **Backlight at 100%** vs a vendor 50% limit | nothing to report | the constraint was prose in a vendor header already read; nothing computes it |
| **Neopixel burning** the factory demo's colour | "unclaimed, not initialized" | a WS2812 *latches*; not touching it is not the same as it being off |
| **SPI overclock** dropping whole transactions | transfer 3.7× faster, every timing improved | SPI writes are fire-and-forget; the panel's failure is invisible upstream |

**The rule: any property that is read back through the same assumption that wrote it cannot be
self-verified.** Colour order, endianness, GRAM offset, clock integrity, brightness, and anything latched
in external hardware are all in this class.

### The colour baseline — a named step, not an improvisation

**Every new panel gets one flash with `CXC_SPLASH_COLOR_BARS` on.** It adds five **labelled** bands of
pure RGB565 to the standard ComposePlatform splash — the logo and device id are untouched — holds them
for `CXC_SPLASH_COLOR_BARS_MS` (6 s), then continues automatically to the scene. No input, no second
flash, no waiting.

| band | RGB565 | 8-bit written |
|---|---|---|
| `RED` | `0xF800` | `FF 00 00` |
| `GREEN` | `0x07E0` | `00 FF 00` |
| `BLUE` | `0x001F` | `00 00 FF` |
| `WHITE` | `0xFFFF` | `FF FF FF` |
| `GREY` | `0x8410` | `80 80 80` |

The procedure:

1. Add `target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_SPLASH_COLOR_BARS)` to the board's
   CMake branch. **Not a `platformio.ini` build_flag** — see Phase 3.
2. Flash. Ask the owner about **specific colours**, never "does it look right".
3. Record the answer in the board's `.descriptor.yaml` under `verify.human`, including *that it was
   eyes and not a capture*.
4. Comment the definition out and rebuild. It is off in a shipping build.

**Why it is worth the pixels, and why the first real scene is not a substitute.** A widget conflates
layers: if the engine bars look wrong the cause could be MADCTL/BGR, RGB565 byte order, the scene's own
colour constants, or the renderer's mapping. A pure band carrying the *name of the colour it is supposed
to be* isolates one variable, so the owner's sentence is arithmetic rather than an impression:

| symptom | cause | fix |
|---|---|---|
| the band labelled **BLUE** renders **GREEN**, and **GREEN** renders **RED** | **BYTE** swap — `0x001F` → `0x1F00`, `0x07E0` → `0xE007` | the controller's RGB565 endian bit (ST7789 `RAMCTRL` 0xB0 param 2 bit 3) |
| the band labelled **BLUE** renders **RED** and **RED** renders **BLUE**, GREEN unchanged | **CHANNEL** swap | MADCTL bit 3 (BGR) |
| everything is a photo negative | inversion | `0x21` INVON vs `0x20` INVOFF |

Those two swaps are told apart by *which* colour goes wrong. That is what turned the C6's hunt into a
one-bit fix, and it is why the labelling — not the bands — is the part worth keeping.

*Worked results:* Waveshare C6, 2026-08-11 — byte swap found, `RAMCTRL` `0xE8` → `0xE0`.
ESP32-C3 + ST7796S, 2026-08-12 — "I see the Compose logo on the screen along with the color bars which
are all correct", so the vendor-default RAMCTRL and MADCTL `0x28` (BGR set) were both confirmed correct,
by eyes, on the first flash. **No machine signal could have produced either result.**

Rows to put in front of the owner:
0. The **labelled colour bands** above — the one row that must be asked before any other, because every
   row below it is only interpretable once colour order is settled.
1. Panel lit, and a known colour fills it. *(⚠ On a module whose backlight line has a pull-up — the
   LCDWIKI MSP3525 has a 10K into a BSS138 gate — the panel is lit at full brightness before any code
   runs, so "it is lit" proves the 5 V rail and nothing about the MCU.)*
2. The boot splash `build: <date> <time>` matches the firmware just flashed — *a stale panel shows a
   stale tag*.
3. Colours not inverted, red/blue not swapped, **and no byte-swap** — ask about specific colours, not
   "does it look right". "Blue shows as green and green as red" is arithmetically a **byte** swap; a BGR
   *channel* swap would show blue as **red**. That distinction turns a hunt into one bit.
4. Image not shifted sideways (any panel narrower than its controller's GRAM).
5. Touch lands where the finger is, corners included — if fitted.
6. Reactivity under live bus traffic *feels* right.

**Use the BSG engine-power bars as the standard first scene**
(`compose_projects/bsg/game_hardware/engine_thrust_panel/`, 240-wide and 172-wide variants). It spans the
full blue/green/amber/red palette, which is what made the byte-swap legible as a *colour-specific pattern*
rather than a vague "looks off"; it exercises three binding forms; live hardware publishes to it
continuously; and it is cheap to draw on FPU-less silicon. A grey dashboard hides these bugs.

---

## Phase 6 — Document, then commit

Write both, matching the existing schema exactly:
- `docs/board-bringup/<board>.descriptor.yaml` — machine-readable, the source of truth
- `docs/board-bringup/<board>.md` — the human recipe

**Record failed experiments, with their numbers, especially when the numbers looked good.** The C6's
40 MHz SPI attempt is kept precisely *because* its results table reads like a success — deleting it would
send the next reader straight back into it.

Commit on a branch **and push it** (`[DEC-PUSH-IS-FREE-EXCEPT-TO-A-PUBLIC-REPO]`, David 2026-08-17 — the public `740Dave/factlattice` is the one exception). Do not touch `.claude/`. Do not sweep in unrelated working-tree
changes — stage explicit paths.

> If a subagent is running in this repo, note that `git checkout -b` moves `HEAD` in the **shared** working
> tree. A commit you make meanwhile can land on *its* branch. Check `git branch --show-current` before
> committing, and verify afterwards with `git merge-base --is-ancestor`.

---

## When to involve `composeux-render-engineer`

This agent is for **render-systems depth**: rasterization, the band/framebuffer tiers, dirty-region
strategy, glyph/font atlases, pixel parity, transfer paths. It is a scarce, expensive dispatch. The
question is not "is this rendering?" but **"is the dominant cost inside the renderer, and can the result
be verified?"**

### Dispatch when *all* of these hold

1. **There is a felt symptom.** Someone can say "this is sluggish." A number you find unsatisfying is not
   a symptom.
2. **The dominant cost is inside the renderer.** Measure first. If transfer is 70% of a repaint, the
   renderer is not your problem — the wire is.
3. **It is verifiable.** The affected boards are on the bench, or the change is provably inert for boards
   that are not.
4. **The work is systems judgment, not a constant.** Restructuring dirty-region tracking, yes. Changing a
   clock or an offset, no.

*Worked example (dispatched, correctly):* the full-frame tier had coarsened dirty repaint to the whole
frame; the display was visibly sluggish under bus traffic; the fix required restructuring how the canvas
tracks and transfers dirty spans. It returned a 2× end-to-end improvement — **and found that half the
latency was outside the renderer entirely**, in a touch-poll loop that delayed bus draining by up to
100 ms. That second finding is the argument for using a specialist: it looked past the brief.

### Do NOT dispatch when

- **The fix is a board constant.** SPI clock, GRAM offset, MADCTL, inversion, backlight — these live in
  the panel header, take minutes, and need *eyes*, not an agent.
- **There is no symptom.** Speculative optimization buys shared-canvas regression risk against boards you
  cannot eyeball, for a benefit nobody asked for.
- **The dominant cost is elsewhere** — the wire, the bus, the network, the broker.
- **It cannot be verified.** The C6's X-range dirty windowing was measured as a real win and *declined*: a
  wrong X window is invisible to every signal except the glass.
- **The board is not on the bench.** Changing a profile blind is how a working board gets broken.

### Scope the brief as ONE batch unless a boundary is technically required

Before you split work across dispatches — or into staged slices inside one — apply a single
test: **must something be validated at this boundary before the next thing can safely
proceed?**

Slicing is a habit that makes a plan legible to a human, and it is not free: every boundary
is a seam to design, defend and re-verify across, and it leaves the implementing agent
holding several partial designs instead of one whole one.

A boundary is **real** when there is an unmade design decision (you would be guessing), a
test that cannot exist yet (absent hardware, peer, or upstream host), or a blast radius that
must be measured before a second change compounds it.

A boundary is **scaffolding** when it exists to be narratable, or when it separates a
refactor from a feature that is *inert until selected*. If new code cannot execute until a
flag or registry entry picks it, then **selection is what isolates it, not commit
sequencing**, and the same before/after check is available at the end of one batch.

Worked example (2026-08-12): a firmware bus-transport port was briefed as five slices. Only
one carried a genuine check — "MQTT behaviour unchanged" — and that check survived
resequencing into a single cycle, because MQTT stayed the selected transport and the UART
code was inert. Two items stayed out on real grounds: an unmade decision about what an
upstream host does, and a transport that could not be tested end-to-end because no such host
was wired.

When you scope something out, justify it as an unmade decision or an impossible test, and
say which. Never as sequencing taste.

### If you do dispatch, the brief must carry

- **Frozen wiring** — device IDs, IPs, ports, env names, broker and asset-store addresses.
- **Damage discipline** — which boards must not regress and which cannot be tested; do not cross the
  waist; commit and push to your own branch but never land on main; do not touch `.claude/`; do not disturb unrelated working-tree changes.
- **Data contracts** — the namespaces in play and how to see them live.
- **The known traps** — at minimum the dropped `build_flags`, or it will lose an afternoon.
- **An acceptance bar that demands measurement.** "Report before/after numbers with your method."
  *"Feels snappier" is not a result.*
- **The reporting split** — what is machine-proven versus what needs the owner's eyes, stated separately
  and never conflated.

### When it reports back, review — do not relay

Rebuild the affected envs yourself. Re-derive at least one headline measurement on the live board. Read
the diff for the equivalence claims about untestable boards. Check any edits it made to *your* docs
against actual evidence. A good agent will also have gone wider than its brief — that is usually good
instinct and always needs review.

---

## Appendix — the trap catalogue

Every one of these was a real defect in this tree.

| trap | signature | where it bit |
|---|---|---|
| PlatformIO `build_flags` dropped | config asserts one thing, binary does another, all tests pass | Hosyond band height; Qualia device prefix |
| ST7789 `RAMCTRL` ENDIAN bit | blue→green, green→red; capture looks perfect | Waveshare C6 |
| GRAM column offset | whole image slides sideways on a panel narrower than the controller | C6 (172 of 240, offset 34) |
| Panel reset tied to `CHIP_PU` | cold boot fine, `esp_restart()` leaves the panel in its old state | Hosyond — init must open with a SW reset |
| SD sharing the display SPI bus | SD teardown kills the display's DMA; `ESP_OK` forever after | classic CYD; latent on the C6 |
| PSRAM not tagged DMA-capable | DMA silently cannot read the framebuffer | Hosyond — resolve at runtime, bounce if needed |
| Overclocked SPI | *no tearing*, updates go missing; all timings improve | C6 at 40 MHz vs a ~15 MHz datasheet limit |
| Vendor brightness limits | none — it is prose | C6, 50% cap; enforce in `set_backlight()`, don't just document |
| Latching RGB LEDs | "unclaimed" ≠ off; holds the previous firmware's colour | C6 WS2812 |
| Manual build tags | rot silently and look authoritative | `TF13`, unchanged across ~10 flashes and 3 boards |
| Non-240 panel width | scenes overflow; the tier does not scale | C6 — author scenes to the board |
| Capture row buffer sized for today's widest panel | `/capture.bmp` 500s on a wider board; the guard is loud but the endpoint is dead | `capture_http.cpp` was `uint8_t row_buf[240*3]`; the 480-wide ST7796S board |
| Boot-logo decode held across config fetch + YAML parse | `yaml arena malloc failed` forever, on a board with "plenty" of heap | ESP32-C3 + ST7796S — 51,200 B of decoded logo vs a 44 KB parse arena; freeing it first took free heap 77,480 → 128,700 |
| Scene cache blob larger than the NVS partition | the loud error names the *cache*; the damage is that **every other NVS key** stops storing, welding shut the bus-settable transport/config control planes | ESP32-C3 + ST7796S — an 8,139 B scene in a 24 KB `nvs`; a 4-byte transport selection failed after a full erase |
| Growing `nvs` in place pushes `factory` | PlatformIO still writes the app at `0x10000`; bootloader reports `invalid magic byte` / `No bootable app partitions` and loops | same board — fixed by putting `nvs` *after* the app so the image offset never moves |
| A control command that does not fit the substrate | the documented escape hatch is refused at publish time, silently in prose | `"can listen=0"` is 13 B; a classic-CAN state string is **7 characters** |
