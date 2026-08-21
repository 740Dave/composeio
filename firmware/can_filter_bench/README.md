# can_filter_bench

Instrument for one question:

> **Can an ESP32-C3 TWAI acceptance filter match on DATA BYTES for EXTENDED (29-bit)
> frames, or only on the identifier?**

The answer decides which of two incompatible 29-bit CAN arbitration-id layouts ComposeBus
adopts (`spec/01-composeio-bus-protocol.smd` §6.3.4, `[BP-CAN-ID-DIVERGENCE]`).

**Read `docs/research/CAN-ACCEPTANCE-FILTER-RESEARCH.md` first**, and
`docs/research/START-HERE-can-research-instance.md` before that. They have the framing,
the verified hardware facts, the wiring, the pre-flight checks, the per-experiment protocol
and the decision rules. This directory is only the tool.

> **The whole bench is one ESP32-C3 and one jumper wire, GPIO3 → GPIO10.**
> No transceiver, no bus, no termination, no second board. Self-reception loops the frame
> back **through the acceptance filter**, which is the thing under test. A two-node bus
> adds no information about the filter — only ways to be wrong. Build one only for E2.
>
> **Never flash a board without asking David which board it is.** This has already gone
> wrong once on this assignment; see the research doc §0.1 and §9.

## Quick start

```bash
pio run -e c3_usbcdc                                        # build
# ---> confirm with David that this port is a board he wants flashed <---
pio run -e c3_usbcdc -t upload --upload-port /dev/cu.usbmodemXXXX
pio device monitor -p /dev/cu.usbmodemXXXX -b 115200        # then type HELP
```

The whole of E1 — the experiment that decides the question — in one command:

```bash
python3 tools/bench.py --loop /dev/cu.usbmodemXXXX --exp all | tee /tmp/e1.log
```

## Design notes

- **One binary, every role.** There is no transmitter build, receiver build or loopback
  build. Role is a runtime serial command (`ROLE tx|rx|loop|idle`), and a role is only a
  label plus defaults — `STATS` always reports the real flags, so a role can never make
  the rig behave in a way the transcript does not show.
- **Machine-parseable output.** Every line starts with `#`, `OK`, `ERR`, `EVT`, `STAT`,
  `ID` or `END`, and carries `key=value` tokens with no spaces inside values.
- **`CALC`** recomputes this document's acceptance-filter hex literals on the silicon
  under test, so the bench can cross-check the paperwork's arithmetic.
- **`RXPULL up|down|none`** forces the RX pin's internal pull, reproducing both "the wire
  is wrong" failure signatures with no external hardware — so you can learn to recognise a
  bad bus before you record a result from one.
- **Alert output is rate-limited** to 8 lines/second with a reported suppression count. A
  genuinely broken bus produces ~48,000 bus errors/second; unthrottled that saturates
  USB-CDC and makes a diagnosable fault look like a hang.
- **Single-shot TX is the default** (`SS ON`), so a bad bus increments `tx_failed` instead
  of retransmitting forever into bus-off.

## Environments

| env | Use when | macOS port |
|---|---|---|
| `c3_usbcdc` (default) | USB-C wired straight to the chip (GPIO18/19 native USB Serial/JTAG) | `/dev/cu.usbmodem*` |
| `c3_uart` | board has a USB-to-UART bridge chip (CP210x, CH340) | `/dev/cu.usbserial*`, `/dev/cu.wchusbserial*`, `/dev/cu.SLAB*` |

`upload_speed = 115200` and `upload_flags = --no-stub` are both load-bearing on the C3's
native USB-JTAG — see the research doc §4.2.

**ESP32-C6 is not supported by this project** (`espressif32@6.5.0` builds Arduino for the
C3 but only ESP-IDF for the C6). A C3 and a C6 enumerate identically as
`USB JTAG/serial debug unit`, VID `0x303A` PID `0x1001` — the port name does **not** tell
you which chip you have. Confirm with `esptool … flash_id` before flashing. Research doc
§2.1.
