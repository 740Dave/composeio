# Competitive Analysis: SparkFun 7-Segment Serial Display vs ComposeIO Equivalent

## SparkFun Product
- **Product**: SparkFun 7-Segment Serial Display (White) — COM-11441
- **URL**: https://www.sparkfun.com/sparkfun-7-segment-serial-display-white.html
- **Price**: $15.50 retail
- **MCU**: ATMega328P with Arduino bootloader
- **Display**: 4-digit 7-segment LED
- **Interface**: Serial (UART), SPI, or I2C (selectable)
- **Configuration**: Hardcoded in Arduino sketch, reprogrammed via Arduino IDE
- **Ecosystem**: None — standalone peripheral with proprietary serial commands
- **Power**: 5V

## Proposed ComposeIO Equivalent

### BOM Estimate

| Component | Package | JLCPCB Part | Est. Unit Price | Notes |
|---|---|---|---|---|
| CH32V003J4M6 | SOP-8 | C5190065 | $0.10 | 48MHz RISC-V, 16KB flash, 2KB SRAM |
| TM1637 | SOP-20 | C8148 | $0.35 | LED driver with key scan, 2-wire interface |
| 4-digit 7-seg display | THT common cathode | varies | $0.40 | Standard 0.56" or 0.36" display |
| 100nF decoupling (×2) | 0805 | C49678 | $0.01 | Basic parts, no setup fee |
| 10K pullup | 0805 | C25804 | $0.01 | Basic part |
| PCB | 2-layer, ~20mm × 30mm | — | ~$0.30 | At qty 10 from JLCPCB |
| Assembly (SMD) | — | — | ~$0.50 | PCBA for CH32V003 + TM1637 + passives |
| JST-XH connector (I2C/power) | THT 4-pin | — | $0.10 | Hand-soldered, bus connection |

**Estimated total BOM: ~$1.77 per unit**
**JLCPCB PCBA (qty 10): ~$3-4 per unit** (including PCB, assembly, extended part fees amortized)

### Comparison

| Dimension | SparkFun ($15.50) | ComposeIO (~$3-4) |
|---|---|---|
| **MCU** | ATMega328P ($3.00) | CH32V003 ($0.10) |
| **Price** | $15.50 retail | ~$3-4 PCBA assembled |
| **Price at qty 10** | $155.00 | ~$35-40 |
| **Protocol** | Proprietary serial commands | ComposeIO bus (namespace pub/sub) |
| **Configuration** | Arduino IDE, recompile, reflash | YAML config, `composeio push` |
| **Self-configuring** | No | Yes — pulls config from bus storage on boot |
| **Replacement** | Reprogram with Arduino IDE | Plug in, self-configures from bus storage |
| **Bus participant** | No — point-to-point serial to one master | Yes — any namespace, any transport |
| **Display content** | Whatever the master sends via serial | Subscribes to any namespace |
| **Change what's displayed** | Edit Arduino sketch on master, recompile | Change one line in YAML config |
| **Multiple displays** | Each needs unique serial config | Each subscribes to its namespace, zero config on master |
| **Ecosystem** | Standalone peripheral | Full ComposeIO infrastructure (bus storage, document store, service hub) |

### Feature Advantages of ComposeIO Version

1. **Namespace binding**: Display subscribes to `ftl_coordinates` and shows jump coordinates. Change config to subscribe to `fleet_water_level` and it shows water percentage. No code change anywhere.

2. **Compound display**: Two ComposeIO 7-seg modules treated as one 8-digit display via config. The SparkFun board requires custom master code to coordinate two displays.

3. **Bus storage self-config**: Flash the role firmware once at the bench. Push YAML config via bus storage. Replace a dead module — new one self-configures. SparkFun requires re-uploading the Arduino sketch.

4. **Label propagation**: The display's human-readable label travels via discovery. An admin console shows "FTL Coordinates Display" not "device at 0x42."

5. **State engine integration**: Display can flash, blink, or animate based on state engine values. "Flashing zeroes" for empty coordinates, "solid digits" for entered values, "all flashing" for confirm mode — all from YAML config, not C++ animation code.

### Action Items

1. **Design the PCB**: Use the existing `generate_pcb.py` pattern. CH32V003 SOP-8 + TM1637 + 4-digit display + JST-XH connector. ~20mm × 30mm, 2-layer.

2. **Get JLCPCB quote**: Upload gerbers + BOM + pick-and-place for qty 5, 10, 25. Compare against SparkFun at same quantities.

3. **Write the role firmware**: `ch32v-7seg-display` — subscribes to configured namespace, drives TM1637, supports flash/blink/scroll animations from config.

4. **Document as ComposeIO reference design**: This is a simple, complete, useful ComposeIO module. Good for the open-source repo as an example build.

### Strategic Value

This comparison demonstrates ComposeIO's value proposition concretely:
- **4× cheaper** at unit level ($3-4 vs $15.50)
- **4× cheaper** at qty 10 ($35-40 vs $155)
- **Infinitely more flexible** — namespace binding vs proprietary serial
- **Zero-config replacement** — bus storage vs Arduino IDE
- **Ecosystem participant** — not an isolated peripheral

If ComposeIO can deliver a better product at 1/4 the price for a simple display module, the value proposition for more complex modules (button matrices, motor controllers, sensor hubs) is even more compelling.

### Notes

- SparkFun's $15.50 includes retail markup, packaging, documentation, support. A fair apples-to-apples comparison would be SparkFun's BOM cost (probably $4-5) vs ComposeIO's BOM ($1.77). Even at BOM level, ComposeIO is 2-3× cheaper due to the CH32V003 vs ATMega328P difference.
- The ATMega328P is massive overkill for driving a 7-segment display. SparkFun uses it because it's their standard platform with existing bootloader/tooling. ComposeIO uses the right-sized MCU for the job.
- This analysis could be repeated for every SparkFun/Adafruit "smart peripheral" board. The pattern holds: expensive general-purpose MCU + proprietary protocol → cheap purpose-sized MCU + ComposeIO bus.
