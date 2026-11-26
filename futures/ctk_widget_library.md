# Future: Full-Stack CTk Widget Library for Virtual Prop Prototyping

> A comprehensive library of CTk widgets that emulate every ComposeIO hardware module type, enabling complete game/experience development without any physical hardware. Props are designed and playtested virtually, then built physically in priority order.

## Concept

The current simulator has 4 widgets (button panel, relay, gauge, bus monitor) plus the coordinate entry widget. These were built for the FTL test. But the BSG game (and any ComposeIO game/experience) needs a much wider range of emulated hardware to support full virtual prototyping.

The widget library serves two purposes:
1. **Game development**: Design, wire, and playtest an entire game experience on a laptop before building any physical props
2. **Prop prioritization**: Once the game plays well virtually, prioritize physical prop builds by frequency of use, gameplay impact, and build difficulty — not by guessing what matters

## Widget Categories

### Input Widgets (emulate physical controls)
- **ButtonPanelWidget** — exists, extend with more LED behaviors
- **KeypadWidget** — exists (coordinate entry), generalize for any matrix keypad
- **ToggleSwitchWidget** — aerospace toggle with guard cover animation
- **RotaryEncoderWidget** — click-to-rotate, value display, push-button
- **JoystickWidget** — drag-to-move, X/Y continuous output, deadzone visualization
- **KeySwitchWidget** — guarded key switch, requires "insert key" click + turn
- **StompBoxWidget** — large momentary, guitar-pedal style
- **SlidePotWidget** — vertical/horizontal slider, continuous output
- **FingerprintWidget** — click-to-scan, accept/deny animation, player ID display

### Output Widgets (emulate physical displays/indicators)
- **GaugeWidget** — exists, extend with color threshold ranges
- **RelayWidget** — exists, extend with multi-channel grouping
- **SevenSegmentWidget** — TM1637 emulation, any digit count, colon support
- **LEDStripWidget** — NeoPixel-style addressable strip, color per pixel
- **StatusLampWidget** — single large indicator (red/yellow/green), 12V aesthetic
- **SpeakerWidget** — shows "now playing" + waveform animation, triggers actual audio playback
- **BuzzerWidget** — simple tone indicator

### Display Widgets (emulate screens)
- **DRADISWidget** — radar sweep, contact tracking, range rings, BSG-styled
- **LCDPanelWidget** — generic small LCD showing text/status from namespace
- **PortholeWidget** — round display showing location-dependent imagery from doc store
- **AlertPanelWidget** — full-screen color flash (Condition One red, etc.)

### Composite Widgets (emulate multi-component stations)
- **FTLStationWidget** — combines: arm toggle + cap gauge + coord keypad + jump button (already built as separate widgets, this composes them)
- **WeaponsConsoleWidget** — target selector + fire buttons + ammunition gauge
- **CommStationWidget** — channel selector + transmit button + signal strength
- **EngineeringWidget** — power distribution sliders + system status indicators

### Infrastructure Widgets (already built)
- **BusMonitorWidget** — exists, live message stream
- **AdminConsoleWidget** — exists as separate app, could embed

## Development Strategy

1. Build input + output widgets first — these are the atomic building blocks
2. Compose station widgets from atomic widgets — validates the composition model
3. Each widget is a standalone CTk class that takes a `ComposeIOBus` instance and a config dict
4. Widgets are bus participants — they publish/subscribe via namespaces, same as physical hardware
5. A "station builder" tool could let the designer drag widgets into a layout and wire them to namespaces visually

## Prop Prioritization Framework

Once a game is playable virtually, prioritize physical builds by:

| Factor | Weight | Rationale |
|---|---|---|
| Frequency of use in gameplay | High | The prop you touch every 30 seconds matters more than the one used once per game |
| Gameplay impact | High | Does this prop create a memorable moment? (FTL jump, cylon detection) |
| Tactile difference from virtual | Medium | Some props gain enormously from being physical (aerospace toggles, key switches). Others barely benefit (status displays) |
| Build difficulty | Factor | Easy builds with high impact should come first |
| Cost | Factor | Cheap + impactful beats expensive + marginal |

Example prioritization for BSG:
1. FTL panel (high frequency, high impact, moderate build) — physical aerospace switches + keypad
2. Cylon detector (moderate frequency, very high impact, moderate build) — handheld, dramatic moments
3. Alert status panel (moderate frequency, high impact, easy build) — just 12V lamps on a relay board
4. DRADIS display (always visible, moderate impact, easy build) — RPi + HDMI, mostly software
5. Weapons console (moderate frequency, moderate impact, complex build) — can stay virtual longer
6. Engineering panel (low frequency, low impact, complex build) — virtual until v2

## Relationship to iPads/Tablets

A web-based version of these widgets (HTML5 Canvas or similar) would enable tablet participation. Same namespaces, same bus (via MQTT/WebSocket), different rendering engine. The CTk widgets are the desktop/RPi version; the web widgets are the tablet version. Same game, same YAML, different display surface.

## Not V1

Build the widget library incrementally as game design demands specific widget types. Don't build all 25+ widgets speculatively. The FTL system already demonstrated the pattern — build the widgets you need for the current scenario, generalize after.
