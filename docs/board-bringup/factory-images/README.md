# Factory flash images

As-shipped vendor firmware, dumped **before** the first flash of a board, so a bring-up is always
reversible.

The `.bin` files are deliberately **not** tracked (multi-megabyte binaries, no git-lfs here) — see
`.gitignore`. The `.sha256` sidecars and this README are tracked, so a restored image can be proven
byte-identical to what was dumped. If a `.bin` is missing locally, it must be re-dumped from a board
still carrying its factory image, or sourced from the vendor kit.

## hosyond-s3

| | |
|---|---|
| file | `hosyond-s3-factory-16mb.bin` |
| size | 16,777,216 bytes (full 16 MB flash) |
| sha256 | `7678d207aeb19cdbfee65da582132059eb6f8f933b514e7508a2fc57e8b97dce` |
| dumped | 2026-08-11, before the first ComposeUX flash |
| contents | `arduino-lib-builder` `bb76cb1`, IDF v5.4.1, built Mar 28 2025 |
| verified | bootloader magic `0xE9` @ `0x0`, partition-table magic `0xAA50` @ `0x8000`, app magic `0xE9` @ `0x10000`; ~1.95 MB of content, remainder erased |

## waveshare-c6-lcd147

| | |
|---|---|
| file | `waveshare-c6-lcd147-factory-4mb.bin` |
| size | 4,194,304 bytes (full 4 MB flash) |
| sha256 | `a399266998f8028bfe642c4db9bd911e9162e0a8757346c21ecd885f5c18637d` |
| dumped | 2026-08-11, before the first ComposeUX flash |
| contents | the as-shipped Waveshare LVGL demo |
| verified | bootloader magic `0xE9` @ `0x0`, partition-table magic `0xAA50` @ `0x8000`, app magic `0xE9` @ `0x10000`; ~1.98 MB of content, remainder erased |

Dumps in about a minute at the default baud (contrast the Hosyond's 16 MB / ~25 min).

## esp32c3-supermini-047dd8

| | |
|---|---|
| file | `esp32c3-supermini-047dd8-factory-4mb.bin` |
| size | 4,194,304 bytes (full 4 MB flash) |
| sha256 | `13961a23051013f7143caacc868bae02cb2becdd8a5f81ac638878dbb4b56905` |
| dumped | 2026-08-12, before the first ComposeIO flash (`[env:esp32_c3_can_bringup]`) |
| board | ESP32-C3 Super Mini, chip rev v0.4, QFN32, embedded 4 MB XMC flash, 40 MHz crystal, MAC `00:00:5e:00:53:03` |
| contents | some prior ESP-IDF **v4.4.6** app (`3572900934`) — NOT a vendor demo. This board had already been used. |
| verified | bootloader magic `0xE9` @ `0x0`, partition-table magic `0xAA50` @ `0x8000`, app magic `0xE9` @ `0x10000` |

The owner confirmed nothing on this board mattered, and the dump was taken anyway — it costs ninety
seconds and it is the only thing standing between a bad flash and a board with no known-good state.
It also turned up a fact worth recording: the image is **not** an as-shipped vendor demo but an
IDF 4.4.6 app from earlier work, so "factory image" is a filename convention here, not a claim.

Unlike the Hosyond, this board's BOOT button **is** brought out, so bootloader entry is manual and
recovery does not depend on this file. Default baud; the dump takes about a minute.

## Dumping and restoring

Dump:

```bash
esptool.py --port /dev/cu.usbmodem31101 read-flash 0 0x1000000 hosyond-s3-factory-16mb.bin
```

Restore:

```bash
esptool.py --port /dev/cu.usbmodem31101 write-flash 0 hosyond-s3-factory-16mb.bin
```

Two notes specific to this board:

- `--baud 921600` **fails** ("chip stopped responding"); use the default baud. The dump takes ~25 minutes.
- The BOOT button is GPIO0 and is **not brought out**, so there is no manual bootloader entry. That is
  precisely why this backup exists: recovery from firmware that breaks native-USB enumeration is not
  straightforward.
