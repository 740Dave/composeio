# Kickoff — the CAN acceptance-filter research instance

> **For David.** Hand this file to a freshly instantiated Claude in its own terminal. It is the whole
> briefing: scope, boundaries, and the order of work. The detailed protocol lives in
> `docs/research/CAN-ACCEPTANCE-FILTER-RESEARCH.md`, which that instance reads as step 2.
>
> Written 2026-08-10 by the ComposeUX Architect. This instance has **no context from that session** and
> should not be given any — its independence is the point.

## How to start it

Open a terminal in `/Users/davidleeburnett/Dropbox/Maker/composeio` and say roughly:

> Read `docs/research/START-HERE-can-research-instance.md` and work through it with me. I'm at the bench.

That's it. Everything it needs is in this file and the one it points to.

## What this instance is for

**One question, answered on silicon:**

> Can an ESP32-C3 TWAI acceptance filter match on **data bytes** for **extended (29-bit)** frames, or
> only on the identifier?

ComposeBus has two incompatible 29-bit CAN identifier layouts in-tree and cannot choose between them
without this answer. The instance produces **evidence**; David decides the layout. It has **no authority
to change the arbitration-id layout, the transport, the bridge, or any spec** — and should refuse if asked
to, because the decision is deliberately held elsewhere.

## Step 0 — restore a board that was modified in error (do this first)

An earlier automated run interrogated whatever ESP32s happened to be on USB, assumed they were the bench
hardware, and **flashed one of them**. It backed up both boards' full 4 MB flash first, so this is fully
recoverable, but do it before anything else so the bench starts from a known state.

- Flashed: the **ESP32-C3**, MAC `AA:BB:CC:DD:EE:FF`, seen at `/dev/cu.usbmodem31101`
- Untouched (read only): the **ESP32-C6**, MAC `AA:BB:CC:DD:EE:FF`
- Backups (4.0 MB each):
  `/private/tmp/claude-501/-Users-davidleeburnett-Dropbox-Maker-composeio/e1a82ba7-0c99-4f14-a7c9-4e7c40b1d0fc/scratchpad/flash_backup/board_1cdbd4c091f4.bin`
  and `…/board_dc1ed56f4ce4_C6.bin`

```
esptool.py --chip esp32c3 --port <PORT> write_flash 0x0 <that backup>.bin
```

**Ask David whether he wants it restored before proceeding** — he may prefer to keep the bench firmware on
it if that board turns out to be a spare. Do not assume either way.

## Step 1 — establish the hardware inventory WITH David. Do not infer it.

**The assignment document contains hardware claims that are WRONG**, because they were derived by probing
whatever was plugged in rather than by asking. Treat every board-specific statement in it as unverified
until you and David confirm it together. Specifically:

- Do **not** assume two ESP32-C3s exist. One board on USB at the time was an **ESP32-C6**, which the
  pinned toolchain (`espressif32@6.5.0`) **cannot build Arduino firmware for at all** — its C6 defs are
  ESP-IDF only. If a C6 is what's available, say so plainly; do not silently substitute it.
- Do **not** assume any CAN hardware is wired. As of 2026-08-10 **none of it was set up** — the
  transceivers, termination, and breadboard were still in the box. Building the bench is work you do
  *with* David, not a precondition you assume.
- Confirm what he actually has in hand before planning anything: how many ESP32s and of which variant,
  how many SN65HVD230 breakouts, whether those breakouts already carry a 120 Ω termination resistor.

**Why you cannot infer this from the machine — the fact that caused the original mistake:** an ESP32-C3
and an ESP32-C6 **enumerate identically** on macOS. Both present as `USB JTAG/serial debug unit`, VID
`0x303A`, PID `0x1001`. The port name tells you nothing about which chip is behind it, and port numbers
renumber across replugs. Only an `esptool.py … flash_id` / `chip_id` round-trip identifies the silicon —
and that resets the board, so ask David before running it on something that may be doing a job.

Ask. Then write what he tells you into the results file as the real inventory.

## Step 2 — read the protocol, then note how little hardware E1 needs

`docs/research/CAN-ACCEPTANCE-FILTER-RESEARCH.md`. The load-bearing thing to notice:

> **E1 — the experiment that decides the question — needs ONE ESP32-C3 and ONE jumper wire.**
> GPIO3 → GPIO10. No transceiver, no bus, no termination, no second board.

The SJA1000 core (which the C3's TWAI controller derives from) supports *Self Reception Request*: a
self-transmitted frame is received back **through the acceptance filter**. So the filter is exercised with
the entire physical layer removed as a confounder. Espressif's own `twai_self_test` example sanctions
exactly this.

Precisely which experiments need what — **the two-node bus is needed for E2 and nothing else**:

| | Needs | Note |
|---|---|---|
| **E1** (a–e) | one C3 + one jumper | The experiment that decides the question. |
| **E2** | the two-node bus | The **only** thing requiring it — and the least confident design in the set: its "the two nodes never actually contended" failure is detectable only after the fact, via `arb_lost == 0` on both boards. |
| **E3** | **no hardware at all** | It is arithmetic, not measurement — encode with one layout, extract the fields with the other, and the outcome is determined before you start. A demonstration artefact; can be done in Python. |
| **E4** | one C3 (Track A) | Runs on the same single-board rig as E1. |

**So: run E1 first, on one board. Build the bus only if David wants E2 afterward.**

*(An earlier draft of this file said the bus was needed for "E2 and E3" in one place and "E2/E4" in
another. Both were wrong; the table above is correct.)*

## Step 3 — run E1 and report honestly

The firmware and a driver script are in `firmware/can_filter_bench/`. The protocol gives exact serial
commands, expected output, and a **PASS / FAIL / INCONCLUSIVE** decision rule per experiment.

**INCONCLUSIVE is a real, valid, expected outcome. Report it when it happens.** Do not force a binary to
have something to say. The rig already demonstrates the standard: its last act on the bench was to refuse
to report an unwired accept-all run as "0 frames accepted", flagging INCONCLUSIVE with `msgs_to_tx=6`
instead — because "the filter rejected everything" and "nothing was ever transmitted" look identical
unless you check.

Two failure signatures worth knowing before you see them:

- **RX stuck dominant → total silence with every error counter at zero.** Indistinguishable from a filter
  that rejected everything, unless you check `msgs_to_tx`.
- **`Rs` pin high on an SN65HVD230 is not "slow mode" — it is standby with the driver switched off.** The
  node receives perfectly and can never transmit.

## Step 4 — write the results file

`docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md`, from the template in the assignment.

**Include raw counters, not just conclusions.** Whoever reads this later needs to re-derive your verdict,
not take it. Record the real hardware inventory from step 1, every command run, every count returned, and
anything that surprised you — including anything in the assignment doc you found to be wrong.

## Boundaries

- **Modify nothing outside** `firmware/can_filter_bench/` and `docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md`.
- **Do not touch** `python/composeio/transport_can.py`, `services/can_mqtt_bridge/`, or any file under
  `spec/` or `corpora/` — those hold the layouts under dispute and changing them would be deciding the
  question you were asked to inform.
- **Do not connect to the ComposeBus MQTT broker** and do not publish to any namespace. This bench is
  isolated on purpose.
- **Do not flash any board without asking David first** — which board is which is exactly what went wrong
  once already.
- Commit your work; **do not push**.

## What "done" looks like

A results file that lets someone who was not at the bench decide the identifier layout — or that says
clearly and specifically why it cannot yet, and what would be needed. Both are successful outcomes. A
confident answer that the raw counters do not support is the only failure.
