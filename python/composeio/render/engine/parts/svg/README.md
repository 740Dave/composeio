# Cached Fritzing part footprints (S3 hookup-SVG art)

Real component art for the hookup-SVG generator
(`composeio.maker.hookup_svg`), fetched from the open
[`fritzing/fritzing-parts`](https://github.com/fritzing/fritzing-parts) repo
(`develop` branch) and cached here so the generator has no network dependency.
Each catalog part (`../catalog/*.yaml`) names its asset via `fritzing:` and its
source via `fritzing_part` / `fritzing_note`.

| file | catalog part | source (fritzing-parts `svg/core/breadboard/`) |
|------|--------------|--------------------------------------------------|
| `potentiometer.svg` | `pot-10k` | `potentiometer_rotary_16mm_5_breadboard.svg` |
| `esp32-c3-supermini.svg` | `esp32-c3-supermini` | David-supplied community `esp32-c3-super-mini-tht.fzpz` breadboard SVG (2026-07-26); `esp32-c3-supermini.fzp.xml` is its .fzp (connector0..15 = "Pin 1".."Pin 16"). Pin→connector map is `fritzing_connectors:` in the catalog yaml (positional, silkscreen order — FLAGGED for board-confirm). |
| `esp32-s3-supermini.svg` | `esp32-s3-supermini` | David-supplied community `esp32-S3-supermini-tht.fzpz` breadboard SVG (2026-07-26); `esp32-s3-supermini.fzp.xml` is its .fzp (40 connectors). Pin→connector map is `fritzing_connectors:` in the catalog yaml — AUTHORITATIVE from the .fzp connector descriptions (real names, no board-confirm caveat). |
| `dy-sv5f.svg` | `dy-sv5f` | David-supplied community `sv5w.fzpz` (DY-SV5W, pin-compatible with DY-SV5F) breadboard SVG (2026-07-26); `dy-sv5f.fzp.xml` is its .fzp (14 connectors, real names). A PERIPHERAL (audio actuator), not an MCU. Pin→connector map is `fritzing_connectors:` — AUTHORITATIVE from the .fzp names. |

## Status / architect's glass

- **`esp32-c3` has NO footprint in the Fritzing core repo** (the core bin ships
  ESP8266 / WeMos-D1 modules, not an ESP32-C3-DevKitM). Sourcing one (a
  community `.fzpz` or an authored SVG) is a follow-up; until then the MCU
  renders with a **generated** footprint.
- The rendered board currently draws **generated** footprints (a labeled body +
  named pin stubs from the catalog pin map) as the load-bearing electrical
  geometry that wires lock to. These cached Fritzing SVGs are the assets a
  future pass composites over that geometry — which requires aligning each
  Fritzing connector terminal (`connectorNpin`) to the generated pin
  coordinate. That connector-terminal alignment is the deferred item.
