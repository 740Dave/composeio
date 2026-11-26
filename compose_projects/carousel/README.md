# Carousel Launcher — ComposePlatform Maker launcher

The bus-native replacement for the CTk Maker launcher
(`${MAKER_HOME}/launcher`) that runs **fullscreen on display-node.local**. A
touch-driven cover-flow carousel of app tiles built on the LANDED carousel
widget (`python/composeio/render/widgets/carousel.py`,
`engine/swipe_carousel.py`) — no new widget code; every tile composes shipped
primitives (`carousel` + `pixel_stack` + `gauge`/`label`/`circle`/`rect`).

- Scene: [`launcher.compose.yaml`](launcher.compose.yaml)
- Host router / supervisor: [`kiosk.py`](kiosk.py) (the autostart entry that
  replaces the CTk `app.py` — see "Kiosk host router" below)
- Test:  `python/tests/render/test_launcher_scene.py` +
  `python/tests/render/test_carousel_kiosk.py` (in the standard
  `pytest tests/render/` gate)

## Run

```bash
cd python
# headless render proof
PYTHONPATH=. python3 -m composeio.render.run_compose \
    ../compose_projects/carousel/launcher.compose.yaml --headless /tmp/out.png
# live, fullscreen, MQTT-connected (the Pi kiosk invocation)
COMPOSEUX_MQTT_BROKER=mqtt-broker.local PYTHONPATH=. \
  python3 -m composeio.render.run_compose \
    ../compose_projects/carousel/launcher.compose.yaml --fullscreen --transports
```

## Bus surface ([CXC-PRIM-CAROUSEL-BIND])

| Direction | Namespace | Meaning |
|---|---|---|
| publish (on settle) | `launcher/index` | centered tile index |
| publish (on settle) | `launcher/index/target` | centered tile target string |
| consume | `launcher/goto` | snap to an index (control-is-a-peer) |
| publish (center-tap) | `_app/<target>/open` = `"open"` | launch intent for the centered tile |
| subscribe (fiat tile) | `fiat/speed`, `fiat/soc`, `fiat/range`, `fiat/gear` | live CANbus state, by reference |

The launch intents the **host** must bind (the carousel only *publishes* them;
it never spawns — widget-owns-intent / launcher-owns-runtime):
`_app/fiat/open`, `_app/bsg/open`, `_app/power/open`.

## Tiles: built vs deferred

| Tile | Order | Status | Notes |
|---|---|---|---|
| **Fiat** | 0 | LIVE (minimal) | Native ComposePlatform EV dash: a `fiat/speed` SPEED dial + a `fiat/soc` SOC LED-bar, subscribing the bus by reference. **Deferred:** `range`/`gear` readouts, a full skeuomorphic dash, and — the load-bearing gap — the **CAN→bus bridge** (nothing publishes `fiat/*` yet; see below). |
| **BSG** | 1 | LAUNCH COVER → 2-up app | A labeled tile (`BSG` ring + "Battlestar Galactica" + "tap to launch") that fires `_app/bsg/open`. The app it launches is the settled **2-up** ([CXC-CIC-TWO-UP]): TWO stacked Colonial Consoles, 20px vertical padding, portrait 1080×1920 — built as its own scene [`bsg_app.compose.yaml`](bsg_app.compose.yaml) (two distinct duty stations `cic.1.helm` / `cic.2.helm`; independent selection/content/status). NOT inlined as a tile body — the carousel owns all pointer input and a 4:3 console can't fit/stay interactive in a 520px tile — so it is **launched** via the `_app/bsg/open` seam. Proof PNGs in [`proofs/`](proofs/) (`bsg_2up.png`, `bsg_2up_independent.png`). **The 2-up scene is OPTIONAL overlay content**, shipped by the private bsg overlay at `compose_projects/carousel/bsg_app.compose.yaml` (relocate it with `COMPOSEIO_BSG_SCENE`). Without it the tile reads "BSG overlay not installed", `kiosk.py` answers the tap with that message and stays on the carousel, and `start-colonial-ux.sh` / `deploy_rpi5.sh` say the same instead of failing. |
| **Power** | 2 | LIVE (action) | System shutdown affordance (red power glyph). Center-tap fires `_app/power/open`; the host binds that to the poweroff path. HA tile **DROPPED** per the DSD. |

## Fiat data namespaces — what was found

Investigated `${MAKER_HOME}/fiat`. **There are no existing ComposeBus
namespaces to reuse.** The CTk `FiatApp` (`fiat/app.py`) is a splash-screen
stub; the hardware layer (`fiat/hardware/rpi.py`) reads raw CAN frames via
`python-can` / SocketCAN `can0` and dispatches them to callbacks **by
arbitration ID** — it never touches the bus. Critically,
`fiat/project_spec.smd` **OQ-1** records the decode itself as open: *"Which
specific CAN PIDs does the Fiat 500e expose on the OBD-II port? — Open, needs
candump capture from live vehicle."*

So "reuse the data by reference" resolves to: **this scene defines the `fiat/*`
bus contract** (`fiat/speed`, `fiat/soc`, `fiat/range`, `fiat/gear`) and the
tile subscribes it live. The missing half is a **CAN→bus bridge** that decodes
the MCP2515 frames and publishes retained state to those namespaces — a
distinct follow-up, blocked on OQ-1 (the actual Fiat 500e PIDs). Until that
bridge exists the gauges rest at 0; a published value moves them live (proven
in the test + proof PNGs).

CAN hardware (from `fiat/canbus hookup raspi.txt`): SparkFun/MCP2515 on SPI0
(GPIO 8/9/10/11 + INT GPIO25), `dtoverlay=mcp2515-can0`, oscillator 16 MHz,
SocketCAN `can0`, `can0.service` (systemd). Graceful-degrade: if `can0` is
absent the backend logs a warning and continues.

## rpi5 deploy mechanism — BUILT ([`deploy_rpi5.sh`](deploy_rpi5.sh))

display-node.local runs the carousel from a **file-copy** deploy at `~/maker/composeio`
(NOT a git checkout). The deploy is now a committed, auditable script driven by
a **manifest** — closing the class of bug where a package-only copy shipped the
python code but not the `compose_projects/` authoring tree, blanking the CIC nav
(defect 2026-07-31).

```bash
# from the repo root (…/composeio):
compose_projects/carousel/deploy_rpi5.sh --dry-run   # review the change set
compose_projects/carousel/deploy_rpi5.sh --grim      # backup→sync→verify→restart→grim
compose_projects/carousel/deploy_rpi5.sh --restart-only   # safe scene-swap only
```

- **What ships is DECLARED** in [`deploy.manifest`](deploy.manifest): the
  verified runtime closure of the running scenes — `python/` (render engine +
  the bundled CIC OFFLINE-fallback chrome/EXT/menu), `compose_projects/carousel/`
  (both scenes + icon PNGs + `kiosk.py`), and `fonts/`. The manifest also lists
  what is deliberately OMITTED (game sfx/content/code the kiosk never loads, and
  now the CIC art authoring sources) so the omission is a decision.
- **CIC art is served from the BUS STORE, not deployed.** The colonial_console
  chrome / EXT view / two-level nav are bus assets (`bus://assets/cic/chrome.svg`,
  `…/ext.svg`, `…/menu.md`, `…/icons/cic/planet.jpg`) the render engine resolves
  by reference at draw time (the DRADIS-console model). Publish/refresh them after
  David edits his authoring SVGs with — from the repo root —
  `cd python && PYTHONPATH=. python3 -m scripts.publish_cic_assets` (bakes chrome/
  EXT, then PUTs all four refs; `--dry-run` to preview). The manifest
  [`cic_assets.yaml`](../bsg/resources/screen_base_layout/cic_assets.yaml) records
  the refs. The prior file-copy of `cic_menu_structure.md` was RETIRED — the store
  is the source of truth; the package's bundled copies cover a store-unreachable
  kiosk (the rows are never blank).
- **Transport:** per-entry `rsync -aR` (path structure preserved) to
  `<user>@display-node.local:~/maker/composeio/` (`RPI_HOST`; the login defaults to the invoking
  user, `${USER}`). **Add/update by default** — no
  `--delete`, because rpi5's `python/` has legitimately diverged (pip
  editable-install `composeio.egg-info/`, a `python/services` dir). `--mirror`
  opts into `--delete` after a `--dry-run` review.
- **Reversible:** a timestamped `~/maker/backups/composeio-<ts>.tar.gz` of the
  hand-edited trees is taken before any overwrite (last 5 kept); stale `*.bak-*`
  from prior ad-hoc copies are cleaned.
- **Verified:** sha256 of the load-bearing files (menu, scenes, kiosk.py,
  menu.py) must match mac↔rpi5 or the deploy aborts; `--grim` pulls a screen
  readback into `proofs/`.
- **CLEAN restart — no cascade kill.** The script does NOT signal any process.
  It publishes scene-swap **intents** (`_app/launcher/open` then `_app/bsg/open`)
  via [`scene_swap.py`](scene_swap.py) on the shared broker; the running
  `kiosk.py` supervisor consumes them and kills+respawns only its render CHILD (a
  fresh process that re-reads the synced scenes + package + assets) while the
  supervisor stays up. This is the fix for the failure mode where a direct
  SIGTERM to the render child cascaded to `kiosk.py` and took the kiosk down.
  `scene_swap.py` publishes a unique nonce value each time because `_app/*/open`
  is retained state and the bus suppresses same-value notifications
  (`namespace.py` change-detection).

**Deps on the Pi** (provisioned separately, not by this script): the render
engine's Skia stack + the `.venv` at `~/maker/composeio/.venv`.

**Kiosk facts** (memory): display-node.local boots NVMe, wayfire `[autostart]` launches
`kiosk.py --broker mqtt-broker.local`, HDMI-A-1 rotated 270 — hence the
portrait **1080×1920** design here. `grim` needs `XDG_RUNTIME_DIR=/run/user/1000
WAYLAND_DISPLAY=wayland-1` from an ssh session (the script supplies this).

### History: the CTk launcher deploy (superseded)

The prior CTk launcher deployed via `${MAKER_HOME}/launcher/deploy.sh`:
`rsync -avz --delete` of `shared`+`launcher`+`fiat`+`bsg`+`ha` to
`~/maker/<component>/`, then `DISPLAY=:0 python3 launcher/app.py`. Deps were
CustomTkinter/evdev/pywebview. The Power tile reproduces its `sudo poweroff`
carousel affordance.

## Kiosk host router (`kiosk.py`) — BUILT

The **host router / supervisor** is the piece that turns a launch *intent* into
a running app — the launcher-owns-runtime half of the widget-owns-intent /
launcher-owns-runtime split. It is [`kiosk.py`](kiosk.py), the autostart entry
that **replaces the CTk `app.py`**. It:

- boots showing the **carousel launcher fullscreen**;
- **subscribes** the app-open intents on the shared broker —
  `_app/launcher/open`, `_app/bsg/open`, `_app/fiat/open`, `_app/power/open`;
- on an intent, **swaps the visible scene** or runs the system action:

  | Intent | Action |
  |---|---|
  | `_app/launcher/open` | back to the carousel launcher (the **back** affordance) |
  | `_app/bsg/open` | show `bsg_app.compose.yaml` (the 2-up) fullscreen |
  | `_app/fiat/open` | **deferred** — no standalone fullscreen scene yet (the LIVE fiat tile rides the carousel); the intent is observed + logged, the surface stays put |
  | `_app/power/open` | `sudo poweroff` — **guarded** (`poweroff_allowed()`: Linux + a `poweroff` binary; **never fires on the dev mac / Darwin**, and `COMPOSEIO_KIOSK_NO_POWEROFF=1` forces it off on the Pi too) |

**Scene-swap mechanism — subprocess-per-scene.** The render runtime binds ONE
`Composition` for the whole life of `RenderApp.run()` (window + widget tree +
namespaces are all wired to that one composition; there is no clean in-process
"load a different scene" seam). So a swap = **terminate the current
`run_compose` child + spawn the target as a fresh fullscreen process** — the
same process ownership as `compose_projects/intelligence/console/console_supervisor.py`. It is
trivially robust: a crashed scene is just a dead child, and the supervisor's
watchdog relaunches the current scene on unexpected child exit (kiosk
self-heal). Each child is spawned as:

```
<venv-python> -m composeio.render.run_compose <scene> --fullscreen --transports
```

**Back affordance.** Publishing `_app/launcher/open` returns to the carousel —
the same bus intent a BACK-button widget, an MCP publish, or an Intelligence
would send (the credo: an Intelligence drives identically to a human). A
physical hotkey is a deferred bonus (the render child owns the window/keyboard).

### `transports:` wiring (why `--transports` now works)

Both scenes carry a `transports:` block under `app:` so the `--transports` flag
connects them to the broker (previously the launcher scene had none, so live
data + intents were dead):

```yaml
app:
  id: maker-launcher
  transports:
    - type: mqtt
      broker: mqtt-broker.local   # == 192.0.2.75, the bus master
      port: 1883
```

The launcher child MUST run with `--transports` or its center-tap
`_app/<target>/open` publishes stay local and never reach the router. The bsg
scene's block puts the two consoles' station namespaces
(`colonial_console/cic.1.helm/*`, `colonial_console/cic.2.helm/*`) live on the
same broker.

### Autostart command line (for the deployer — do NOT deploy from here)

The deployer (provisionio-deployer) puts this in `wayfire.ini` `[autostart]`,
replacing the CTk `python3 launcher/app.py` line. The Wayland/XDG session env
comes from wayfire; the venv + `PYTHONPATH` + `COMPOSEIO_FONT_DIR` are supplied
here (the supervisor also fills `PYTHONPATH`/`COMPOSEIO_FONT_DIR` for its
children from `--repo-root` if unset):

```ini
[autostart]
carousel = sh -c 'cd $HOME/maker/composeio && \
  PYTHONPATH=$HOME/maker/composeio/python \
  COMPOSEIO_FONT_DIR=$HOME/maker/composeio/fonts \
  $HOME/maker/composeio/.venv/bin/python3 \
  compose_projects/carousel/kiosk.py --broker mqtt-broker.local'
```

`kiosk.py --selftest` is a no-broker, no-window sanity check (12 asserts) for
provisioning. `--windowed` spawns scenes windowed instead of fullscreen (dev).

The carousel only *publishes* `_app/bsg/open`; it never spawns. The 2-up is
byte-for-byte the shipped `colonial_console` widget composed twice — no widget
code was added or forked.

**Still deferred:** a full **Fiat app scene** (`_app/fiat/open` is a no-op until
one exists — the live fiat tile rides the carousel today) and the **CAN→bus
bridge** that publishes `fiat/*` (blocked on OQ-1, the Fiat 500e PIDs — see
"Fiat data namespaces" above).
