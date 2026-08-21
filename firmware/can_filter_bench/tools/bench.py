#!/usr/bin/env python3
"""bench.py — scripted driver for the can_filter_bench rig.

This is an ACCELERATOR, not an oracle. It sends exactly the same serial commands
the research document specifies by hand, prints EVERY line the board(s) emit
verbatim, and reports OBSERVED counts next to the document's PREDICTED counts.

It deliberately does NOT print a verdict. PASS / FAIL / INCONCLUSIVE is a
judgement the operator makes using the decision rules in
docs/research/CAN-ACCEPTANCE-FILTER-RESEARCH.md, against the raw transcript.
A script that decides for you is a script that can quietly confirm a prior.

TWO TRACKS
----------
Track A (recommended, and sufficient for the whole of E1):
    ONE board, ONE jumper wire from the TWAI TX GPIO to the TWAI RX GPIO, no
    transceiver, no bus, no termination. Self-reception request loops the frame
    back through the acceptance filter.

        python3 bench.py --loop /dev/cu.usbmodemXXXX --exp all

Track B (two boards on a real terminated differential bus):
        python3 bench.py --a /dev/cu.usbmodemXXXX --b /dev/cu.usbmodemYYYY --exp all
    Board A transmits, board B is the receiver under filter test.

Requires pyserial. If it is not on your python:
    ~/.platformio/penv/bin/python firmware/can_filter_bench/tools/bench.py ...
"""

from __future__ import annotations

import argparse
import glob
import sys
import time

try:
    import serial  # type: ignore
except ImportError:  # pragma: no cover
    sys.exit("pyserial not found. Try: python3 -m pip install pyserial")


BAUD = 115200


class Board:
    def __init__(self, port: str, label: str):
        self.port = port
        self.label = label
        self.ser = serial.Serial(port, BAUD, timeout=0.05)
        time.sleep(0.4)
        self.ser.reset_input_buffer()

    def _log(self, line: str) -> None:
        print(f"{self.label}| {line}", flush=True)

    def drain(self, settle: float = 0.4) -> list[str]:
        """Read whatever is pending. Always echoes it — nothing is hidden."""
        out: list[str] = []
        buf = b""
        deadline = time.time() + settle
        while time.time() < deadline and len(buf) < 200_000:
            chunk = self.ser.read(8192)
            if chunk:
                buf += chunk
        for raw in buf.split(b"\n"):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                out.append(line)
                self._log(line)
        return out

    def cmd(self, line: str, until: str | None = None, timeout: float = 3.0) -> list[str]:
        print(f"{self.label}< {line}", flush=True)
        self.ser.write((line + "\n").encode())
        self.ser.flush()
        out: list[str] = []
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(8192)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                out.append(text)
                self._log(text)
                done = text.startswith(until) if until else (
                    text.startswith("OK ") or text.startswith("ERR ")
                )
                if done:
                    return out
        out.append(f"<TIMEOUT after {timeout}s>")
        self._log(out[-1])
        return out

    def stats(self) -> dict:
        lines = self.cmd("STATS", until="END STATS", timeout=6.0)
        s: dict = {"rxid": {}, "raw": lines}
        for line in lines:
            if not line.startswith("STAT "):
                continue
            parts = line.split()
            group = parts[1]
            kv = {}
            for tok in parts[2:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    kv[k] = v
            if group == "rxid":
                s["rxid"][kv.get("id")] = int(kv.get("n", 0))
            else:
                s[group] = kv
        return s


class Rig:
    """One board in loopback, or two boards on a bus. Same experiment code."""

    def __init__(self, rx: Board, tx: Board, loop: bool, bitrate: int = 500,
                 pins: tuple[int, int] | None = None):
        self.rx = rx
        self.tx = tx
        self.loop = loop
        self.bitrate = bitrate
        self.pins = pins
        self.twai_mode = "no-ack" if loop else "normal"

    def configure_roles(self) -> None:
        if self.pins:
            # ADDED 2026-08-10. The Track A jumper on this bench turned out to be
            # in the wrong header hole; --pins lets you point TWAI at the pads the
            # wire is ACTUALLY on (verified with the firmware's PROBE command)
            # instead of reflashing once per guess. Applied at the next INIT.
            for b in {id(self.rx): self.rx, id(self.tx): self.tx}.values():
                b.cmd("DEINIT")
                b.cmd(f"PINS {self.pins[0]} {self.pins[1]}")
        if self.loop:
            self.rx.cmd("ROLE loop")
        else:
            self.tx.cmd("ROLE tx")
            self.rx.cmd("ROLE rx")

    def apply_filter(self, code: str, mask: str, mode: str) -> None:
        """Set the filter on the receiver, then (re)init BOTH ends.

        Re-init zeroes every counter on both boards, so a STATS report always
        describes exactly one filter configuration.
        """
        self.rx.cmd(f"FILTER {code} {mask} {mode}")
        self.rx.cmd(f"INIT {self.bitrate} {self.twai_mode}")
        if not self.loop:
            self.tx.cmd(f"INIT {self.bitrate} normal")

    def send(self, kind: str, ident: str, data: str) -> None:
        self.tx.cmd(f"SEND {kind} {ident} {data}")
        time.sleep(0.06)

    def settle(self) -> None:
        time.sleep(0.4)
        self.rx.drain()
        if not self.loop:
            self.tx.drain(settle=0.2)


def gate(st: dict, tx_st: dict | None) -> list[str]:
    """The checks that must hold before ANY count is interpretable."""
    problems = []
    hw = st.get("hw", {})
    src = tx_st or st
    src_hw = src.get("hw", {})
    if hw.get("state") not in ("RUNNING",):
        problems.append(f"receiver TWAI state={hw.get('state')} (want RUNNING)")
    if int(hw.get("bus_error", 0) or 0) != 0:
        problems.append(f"receiver bus_error={hw.get('bus_error')} (want 0)")
    if int(src_hw.get("msgs_to_tx", 0) or 0) != 0:
        problems.append(
            f"transmitter msgs_to_tx={src_hw.get('msgs_to_tx')} — frames are STUCK "
            "IN THE TX QUEUE and never reached the wire; every RX count below is "
            "meaningless")
    if int(src_hw.get("tx_failed", 0) or 0) != 0:
        problems.append(f"transmitter tx_failed={src_hw.get('tx_failed')} (want 0)")
    return problems


def report(name: str, predicted: str, st: dict, tx_st: dict | None = None) -> None:
    print()
    print(f"=== {name} — OBSERVED ===")
    print(f"    predicted (documented behaviour): {predicted}")
    print(f"    rx accepted total : {st.get('rx', {}).get('accepted')}")
    for ident, n in sorted(st["rxid"].items()):
        print(f"    rx id={ident} n={n}")
    hw = st.get("hw", {})
    print(f"    rx hw: state={hw.get('state')} tec={hw.get('tec')} rec={hw.get('rec')} "
          f"bus_error={hw.get('bus_error')} rx_missed={hw.get('rx_missed')} "
          f"msgs_to_rx={hw.get('msgs_to_rx')} msgs_to_tx={hw.get('msgs_to_tx')} "
          f"arb_lost={hw.get('arb_lost')} tx_failed={hw.get('tx_failed')}")
    problems = gate(st, tx_st)
    if problems:
        print("    !! VALIDITY GATE FAILED — this run is INCONCLUSIVE, not a result:")
        for p in problems:
            print(f"       - {p}")
    else:
        print("    validity gate: OK (bus healthy, all frames left the TX queue)")
    print("    (verdict is YOURS — apply the decision rule in the research doc)")
    print()


# --------------------------------------------------------------------------- #
# experiments                                                                  #
# --------------------------------------------------------------------------- #

def preflight(rig: Rig) -> None:
    print("\n### PRE-FLIGHT — identity and build stamp\n")
    rig.rx.cmd("ID", until="END ID")
    if not rig.loop:
        rig.tx.cmd("ID", until="END ID")
        print("#   The `stamp=` field MUST be identical on both boards. If it is not,")
        print("#   they are running different builds and nothing below is comparable.")
    rig.configure_roles()
    print("\n#   Cross-checking the document's filter literals against numbers")
    print("#   computed on the silicon under test:")
    rig.rx.cmd("CALC ext 0x12345678")
    rig.rx.cmd("CALC std 0x123 0xa5 0x5a")
    rig.rx.cmd("CALC dual 0x02468000 0x1fffffff")


def control(rig: Rig) -> None:
    """Accept-all. Every frame the later experiments use must be receivable."""
    print("\n### E1-CONTROL — accept-all filter; all six frames must arrive\n")
    rig.apply_filter("0x00000000", "0xFFFFFFFF", "single")
    for ident, data in (("0x12345678", "a5"), ("0x12345679", "a5"),
                        ("0x024694a0", "a5"), ("0x024694a0", "5a"),
                        ("0x02468b40", "a5"), ("0x02468b40", "5a")):
        rig.send("ext", ident, data)
    rig.settle()
    report("E1-CONTROL", "accepted=6 across 5 distinct ids, bus_error=0",
           rig.rx.stats(), None if rig.loop else rig.tx.stats())


def e1a(rig: Rig) -> None:
    print("\n### E1a — POSITIVE CONTROL: extended, single filter, exact 29-bit id\n")
    print("#   Without E1a passing, a null in E1b means nothing at all.")
    rig.apply_filter("0x91A2B3C0", "0x00000007", "single")
    for ident in ("0x12345678", "0x12345679", "0x02345678", "0x12345670"):
        rig.send("ext", ident, "a5")
    rig.settle()
    report("E1a", "accepted=1, only id=0x12345678", rig.rx.stats(),
           None if rig.loop else rig.tx.stats())


def e1b(rig: Rig) -> None:
    print("\n### E1b — THE QUESTION: extended, single filter, code/mask placed at "
          "a DATA-BYTE position\n")
    print("#   code=0x0000A500  mask=0xFFFF00FF")
    print("#   Under the ID-only interpretation, code bits 15..8 == ID[12:5].")
    print("#   Set A ids have ID[12:5]=0xA5; set B ids have ID[12:5]=0x5A.")
    print("#   Each set has one frame with data[0]=0xA5 and one with data[0]=0x5A.")
    print("#     ID-only       -> A: 2/2   B: 0/2    (total 2)")
    print("#     data-byte     -> A: 1/2   B: 1/2    (total 2)")
    print("#   SAME TOTAL, DIFFERENT DISTRIBUTION. Neither a stuck-accept nor a")
    print("#   stuck-reject nor a dead bus can produce either pattern, so the")
    print("#   result cannot be faked by a misconfiguration.\n")
    rig.apply_filter("0x0000A500", "0xFFFF00FF", "single")
    for ident, data in (("0x024694a0", "a5"), ("0x024694a0", "5a"),
                        ("0x02468b40", "a5"), ("0x02468b40", "5a")):
        rig.send("ext", ident, data)
    rig.settle()
    report("E1b", "ID-only: id=0x024694a0 n=2 and id=0x02468b40 absent",
           rig.rx.stats(), None if rig.loop else rig.tx.stats())


def e1c(rig: Rig) -> None:
    print("\n### E1c — DISAMBIGUATOR: STANDARD frame, single filter, data bytes 1-2\n")
    print("#   If E1b is null AND E1c filters, that is a real capability boundary.")
    print("#   If E1c is ALSO null, we configured something wrong and E1b proves")
    print("#   nothing. id=0x456 and id=0x7ff passing is what proves the match is")
    print("#   on DATA and not on the identifier.\n")
    rig.apply_filter("0x2460A55A", "0xFFFF0000", "single")
    for ident, data in (("0x123", "a55a"), ("0x123", "a55b"),
                        ("0x123", "5a5a"), ("0x456", "a55a"), ("0x7ff", "a55a")):
        rig.send("std", ident, data)
    rig.settle()
    report("E1c", "accepted=3: 0x123/a55a, 0x456/a55a, 0x7ff/a55a; "
                  "0x123/a55b and 0x123/5a5a rejected",
           rig.rx.stats(), None if rig.loop else rig.tx.stats())


def e1d(rig: Rig) -> None:
    print("\n### E1d — LAYOUT DISCRIMINATOR: dual filter mode, extended frames\n")
    print("#   filter1 requires ID[28:13]==0x1234; filter2 requires 0xFFFF (never sent).")
    print("#   0x02469fff differs from 0x02468000 ONLY in ID bits 12..0.")
    rig.apply_filter("0x1234FFFF", "0x00000000", "dual")
    for ident in ("0x02468000", "0x02469fff", "0x0246a000"):
        rig.send("ext", ident, "a5")
    rig.settle()
    report("E1d-part1", "accepted=2 (0x02468000, 0x02469fff); 0x0246a000 rejected",
           rig.rx.stats(), None if rig.loop else rig.tx.stats())

    print("\n#   part 2 — identical ids, filter banks SWAPPED. If part 2 differs from")
    print("#   part 1, filter bank 2 is not live and part 1 was a one-bank result.")
    rig.apply_filter("0xFFFF1234", "0x00000000", "dual")
    for ident in ("0x02468000", "0x02469fff", "0x0246a000"):
        rig.send("ext", ident, "a5")
    rig.settle()
    report("E1d-part2", "identical to part 1: accepted=2", rig.rx.stats(),
           None if rig.loop else rig.tx.stats())


def e1e(rig: Rig) -> None:
    print("\n### E1e — does a REJECTED frame reach the CPU at all?\n")
    print("#   rx queue = 4, DRAIN OFF (nothing is ever dequeued).")
    print("#   rx_missed_count is incremented IN THE RX ISR when a frame arrives")
    print("#   and the queue is full. rx_missed>0 therefore proves the CPU was")
    print("#   interrupted. rx_missed==0 AND msgs_to_rx==0 proves the frame was")
    print("#   dropped in silicon before any interrupt was raised — which is the")
    print("#   only thing that makes hardware filtering worth having.\n")
    rig.rx.cmd("RXQ 4")
    rig.rx.cmd("DRAIN OFF")
    rig.rx.cmd("RX OFF")

    print("#   E1e-1 accept-all (positive control)")
    rig.apply_filter("0x00000000", "0xFFFFFFFF", "single")
    rig.tx.cmd("BURST ext 0x12345678 a5 20 5", until="OK BURST", timeout=20.0)
    rig.settle()
    report("E1e-1 accept-all", "msgs_to_rx=4 rx_missed=16", rig.rx.stats(),
           None if rig.loop else rig.tx.stats())

    print("#   E1e-2 rejecting filter (accepts only 0x12345678; we send 0x0fedcba8)")
    rig.apply_filter("0x91A2B3C0", "0x00000007", "single")
    rig.tx.cmd("BURST ext 0x0fedcba8 a5 20 5", until="OK BURST", timeout=20.0)
    rig.settle()
    report("E1e-2 rejecting", "msgs_to_rx=0 rx_missed=0", rig.rx.stats(),
           None if rig.loop else rig.tx.stats())

    rig.rx.cmd("DRAIN ON")
    rig.rx.cmd("RX ON")
    rig.rx.cmd("RXQ 16")


def wirecheck(rig: Rig) -> None:
    """Reproduce both 'the wire is wrong' signatures on purpose, first.

    You should be able to recognise a bad bus BEFORE you record a result from
    one. Both of these are produced with no external hardware at all.
    """
    print("\n### WIRE-CHECK — deliberately break it, twice, and learn the shapes\n")
    print("#   Fault A: RX stuck DOMINANT. Expect TOTAL SILENCE — msgs_to_tx sticks")
    print("#   above zero and every error counter stays at 0. This is the dangerous")
    print("#   one: it is indistinguishable from 'the filter rejected everything'")
    print("#   unless you look at msgs_to_tx.")
    rig.rx.cmd("DEINIT")
    rig.rx.cmd("RXPULL down")
    rig.rx.cmd(f"INIT {rig.bitrate} {rig.twai_mode}")
    rig.rx.cmd("SEND ext 0x12345678 a55a")
    time.sleep(0.4)
    rig.rx.drain()
    report("WIRE-CHECK fault A (RX dominant)",
           "tx ok=1 BUT msgs_to_tx=1 and bus_error=0 — nothing reached the wire",
           rig.rx.stats())

    print("#   Fault B: RX recessive / floating. Expect a BUS_ERROR storm, rec")
    print("#   climbing, tec staying 0, msgs_to_tx stuck, and the alert throttle")
    print("#   engaging (EVT ALERT_THROTTLED + STAT alert2 lines_suppressed>0).")
    rig.rx.cmd("DEINIT")
    rig.rx.cmd("RXPULL up")
    rig.rx.cmd(f"INIT {rig.bitrate} {rig.twai_mode}")
    time.sleep(1.2)
    rig.rx.drain()
    report("WIRE-CHECK fault B (RX recessive/floating)",
           "bus_error large, rec>0, tec=0, alert2 lines_suppressed>0",
           rig.rx.stats())

    rig.rx.cmd("DEINIT")
    rig.rx.cmd("RXPULL none")
    print("#   Restored: RXPULL none, driver uninstalled. Now fit the real wiring.")


EXPERIMENTS = {
    "wirecheck": wirecheck,
    "control": control,
    "e1a": e1a,
    "e1b": e1b,
    "e1c": e1c,
    "e1d": e1d,
    "e1e": e1e,
}
DEFAULT_ORDER = ["control", "e1a", "e1b", "e1c", "e1d", "e1e"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="list candidate serial ports")
    ap.add_argument("--loop", help="Track A: single board port (TX GPIO jumpered to RX GPIO)")
    ap.add_argument("--a", help="Track B: transmitter port")
    ap.add_argument("--b", help="Track B: receiver-under-test port")
    ap.add_argument("--bitrate", type=int, default=500,
                    help="kbit/s: 25|50|100|125|250|500|800|1000. Drop to 250 if a "
                         "breadboard bus shows bus_error>0. Irrelevant to the "
                         "filter question — only to signal integrity.")
    ap.add_argument("--exp", default="all",
                    help="all | wirecheck | control | e1a | e1b | e1c | e1d | e1e")
    ap.add_argument("--pins", default=None, metavar="TX,RX",
                    help="override the TWAI pins at runtime, e.g. --pins 2,10. "
                         "Use the firmware's PROBE <drv> <sense> command first to "
                         "find where the jumper ACTUALLY is; a wire one hole out "
                         "presents as tx_failed + tec=136 from a single frame, "
                         "not as an obvious wiring fault.")
    args = ap.parse_args()

    pins = None
    if args.pins:
        try:
            a_, b_ = (int(x, 0) for x in args.pins.split(","))
        except ValueError:
            ap.error("--pins wants TX,RX e.g. --pins 2,10")
        pins = (a_, b_)

    if args.list:
        for pat in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*",
                    "/dev/cu.wchusbserial*", "/dev/cu.SLAB*"):
            for p in sorted(glob.glob(pat)):
                print(p)
        print("\n# /dev/cu.usbmodem*  = native USB Serial/JTAG (build env c3_usbcdc)")
        print("# anything else      = USB-to-UART bridge chip (build env c3_uart)")
        print("# To map a port to a specific chip WITHOUT flashing it:")
        print("#   python3 -c \"from serial.tools import list_ports;"
              "[print(p.device,p.hwid) for p in list_ports.comports()]\"")
        return 0

    if args.loop:
        b = Board(args.loop, "L")
        rig = Rig(rx=b, tx=b, loop=True, bitrate=args.bitrate, pins=pins)
    elif args.a and args.b:
        if args.a == args.b:
            ap.error("--a and --b must be different ports")
        rig = Rig(rx=Board(args.b, "B"), tx=Board(args.a, "A"), loop=False,
                  bitrate=args.bitrate, pins=pins)
    else:
        ap.error("give either --loop PORT, or both --a PORT --b PORT (or --list)")

    preflight(rig)
    names = DEFAULT_ORDER if args.exp == "all" else [args.exp]
    for name in names:
        fn = EXPERIMENTS.get(name)
        if fn is None:
            print(f"unknown experiment: {name}", file=sys.stderr)
            return 2
        fn(rig)

    print("\n### DONE. The transcript above IS the evidence. Transcribe raw counters")
    print("### into docs/research/CAN-ACCEPTANCE-FILTER-RESULTS.md — not just verdicts.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
