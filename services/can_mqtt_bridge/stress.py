#!/usr/bin/env python3
"""stress.py — a CAN segment stress harness that MEASURES rather than asserts.

RUN IT ON THE PI. It needs two SocketCAN interfaces on one physical segment
(rpi5 + Waveshare 2-CH CAN HAT: can0 and can1, jumpered CAN_H<->CAN_H /
CAN_L<->CAN_L, 120R at both ends).

WHY A SEPARATE WITNESS INTERFACE IS THE WHOLE POINT
    A transmitter that reports "I sent 977 frames" has measured its own
    intent. `can1` is an INDEPENDENT CONTROLLER on the same wire: it counts
    what actually arrived. Every number below is `witness observed` unless it
    is explicitly labelled `offered`. A claim verified only by the component
    under test is not verified.

    Where possible the harness ALSO writes a `candump` log so a third tool,
    which shares no code with this one, can contradict it.

WHAT IT ANSWERS
    loss        per-frame delivery rate tx->rx across pacing values, with gaps
                identified BY SEQUENCE (a total that matches can still hide a
                drop plus a duplicate).
    enobufs     the offered-rate at which `txqueuelen 10` starts refusing, and
                what the refusal looks like (errno, not a generic Exception).
    config      the REAL [SPEC:CXC-CONFIG-BUS] transfer: `chunk_config` at
                `CAN_CONTENT_BYTES`, reassembled by the REAL `Reassembler`,
                accepted only on a CRC-16 content-hash match. Repeated, so the
                answer is a success RATE and not one lucky sample.
    repair      the same transfer with a NAK-with-gaps selective-repair round,
                so whole-transfer restart and selective repair are measured on
                the same wire in the same conditions.
    contention  a config transfer while a second node transmits throttle-shaped
                traffic, so the arbitration outcome is OBSERVED, not derived
                from the id layout.
    busoff      fault injection: transmit into a downed peer, watch the error
                counters climb, and prove `restart-ms 100` recovers AND that a
                half transfer never becomes a completed payload.

FRAMING
    Every frame this harness emits is a real ComposeBus-over-CAN frame in the
    `services/can_mqtt_bridge` layout ([BP-CAN-ID]):

        29-bit id := ns_hash(16) << 13 | type_nibble(4) << 9 | src_hash(9)
        data      := the config frame VALUE, [seq_hi][seq_lo][content(<=6)]

    so what is measured is the format that would actually run, not a synthetic
    payload that happens to be 8 bytes long.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "python"))

from composeio.config import namespace_hash
from composeio.config_chunk import (
    CAN_CONTENT_BYTES, UART_CONTENT_BYTES, HEADER_SEQ, Reassembler,
    chunk_config, content_hash, chunk_count, parse_frame,
)
from composeio.peer.socketcan import (
    CAN_FRAME_SIZE, SocketCanReader, make_can_id, pack_can_frame, parse_can_frame,
)
from composeio.transport import MessageType, wire_code_for_type

# --- the namespaces this harness uses -------------------------------------- #
STRESS_NS = "_device/canstress/config"
STRESS_SRC = "canstress"
THROTTLE_NS = "throttle/left"

ENOBUFS = errno.ENOBUFS  # 105 on Linux


#: The two candidate 29-bit arbitration layouts. Both are computed HERE, in the
#: harness, so the proposed ordering can be MEASURED on real silicon before it
#: is landed in the wire format — the harness emits frames, it does not define
#: the protocol, and `services/can_mqtt_bridge/bridge.py::can_id_from_message`
#: remains the only authority on what the bridge actually sends.
#:
#:   current    ns_hash(16) | type(4) | src(9)
#:              Priority is a pseudo-random function of the namespace NAME:
#:              whichever string happens to CRC low wins the bus. throttle/left
#:              (0x03F6) and throttle/right (0x0D9E) outrank everything by luck.
#:   type-first type(4) | ns_hash(16) | src(9)
#:              Priority is the MESSAGE TYPE, with the namespace breaking ties.
LAYOUTS = ("current", "type-first")


def arb_for(ns: str, mtype: MessageType, src: str = STRESS_SRC,
            layout: str = "current") -> int:
    """A 29-bit [BP-CAN-ID] arbitration id in one of the candidate layouts.

    The `current` branch is the SAME expression the bridge uses, deliberately
    duplicated rather than imported so a bridge-side change shows up here as a
    disagreement instead of being silently inherited."""
    nsh = namespace_hash(ns) & 0xFFFF
    typ = wire_code_for_type(mtype) & 0xF
    src_h = namespace_hash(src) & 0x1FF
    if layout == "current":
        return (nsh << 13) | (typ << 9) | src_h
    if layout == "type-first":
        return (typ << 25) | (nsh << 9) | src_h
    raise ValueError(f"unknown layout {layout!r}; known: {LAYOUTS}")


class Tx:
    """A transmit socket that reports ENOBUFS AS ENOBUFS.

    `SocketCanReader.send` and `bridge.py::_forward_to_can` both let an
    `OSError(ENOBUFS)` fall into a generic handler, where a full `txqueuelen 10`
    queue is indistinguishable from a dead interface. Here it is a counter."""

    def __init__(self, iface: str):
        self.iface = iface
        self._sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self._sock.bind((iface,))
        self.sent = 0
        self.enobufs = 0
        self.other_err: dict[int, int] = {}
        self.backoff_s = 0.0

    def send(self, arb: int, data: bytes, *, retry: bool = True,
             backoff_s: float = 0.0005, max_tries: int = 2000) -> bool:
        """True when the kernel ACCEPTED the frame.

        `retry=False` is the honest measurement of the offered rate: a refusal
        is a refusal. `retry=True` is what a transfer that must complete has to
        do, and it CHARGES ITSELF the wall time it spent waiting so the cost is
        visible instead of hiding in the throughput number."""
        frame = pack_can_frame(make_can_id(arb, eff=True), data)
        for _ in range(max_tries):
            try:
                self._sock.send(frame)
                self.sent += 1
                return True
            except OSError as e:
                if e.errno == ENOBUFS:
                    self.enobufs += 1
                    if not retry:
                        return False
                    t0 = time.monotonic()
                    time.sleep(backoff_s)
                    self.backoff_s += time.monotonic() - t0
                    continue
                self.other_err[e.errno] = self.other_err.get(e.errno, 0) + 1
                return False
        return False

    def close(self):
        try:
            self._sock.close()
        except Exception:
            pass


class Witness:
    """A receive thread on the OTHER interface. Records (t, arb, data)."""

    def __init__(self, iface: str, on_frame=None):
        self.iface = iface
        self.reader = SocketCanReader(iface)
        self.frames: list[tuple[float, int, bytes]] = []
        self._on_frame = on_frame
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name=f"witness:{iface}",
                                   daemon=True)

    def start(self):
        self._t.start()
        time.sleep(0.15)          # let the thread reach recv before tx begins
        return self

    def _loop(self):
        while not self._stop.is_set():
            try:
                f = self.reader.read_frame(timeout=0.2)
            except Exception:
                continue
            if f is None:
                continue
            arb, eff, data = f
            now = time.monotonic()
            self.frames.append((now, arb, data))
            if self._on_frame is not None:
                self._on_frame(now, arb, data)

    def stop(self, drain_s: float = 0.5):
        time.sleep(drain_s)       # frames in flight are not losses
        self._stop.set()
        self._t.join(timeout=2.0)
        self.reader.close()
        return self


# --- link counters: the kernel's own opinion, which is not ours ------------- #

def link_stats(iface: str) -> dict:
    out = subprocess.run(["ip", "-s", "-d", "link", "show", iface],
                         capture_output=True, text=True).stdout
    st = {}
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        if "re-started bus-errors" in ln and i + 1 < len(lines):
            v = lines[i + 1].split()
            for k, val in zip(["restarts", "bus_errors", "arbit_lost",
                               "error_warn", "error_pass", "bus_off"], v):
                st[k] = int(val)
        if ln.strip().startswith("RX:") and i + 1 < len(lines):
            v = lines[i + 1].split()
            st["rx_packets"], st["rx_errors"], st["rx_dropped"] = \
                int(v[1]), int(v[2]), int(v[3])
        if ln.strip().startswith("TX:") and i + 1 < len(lines):
            v = lines[i + 1].split()
            st["tx_packets"], st["tx_errors"], st["tx_dropped"] = \
                int(v[1]), int(v[2]), int(v[3])
    st["state"] = "BUS-OFF" if "BUS-OFF" in out else (
        "ERROR-PASSIVE" if "ERROR-PASSIVE" in out else
        "ERROR-WARNING" if "ERROR-WARNING" in out else "ERROR-ACTIVE")
    return st


def delta(a: dict, b: dict) -> dict:
    return {k: b.get(k, 0) - v for k, v in a.items() if isinstance(v, int)}


# --- 1. per-frame loss ------------------------------------------------------ #

#: The config frame format spends TWO bytes on the sequence, so a run longer
#: than this cannot be accounted for by sequence at all. Learned the hard way:
#: a 200,000-frame run reported 67% loss and 134,464 "duplicates" — which was
#: the harness's own uint16 wrapping three times, not the wire. The kernel's
#: link counters said 200000 TX / 200000 RX / 0 errors and the kernel was
#: right. A long run is now SPLIT into batches instead of silently aliasing.
MAX_SEQ = 0xFFFF


def cmd_loss(args) -> dict:
    """Offer N sequenced frames; count what an independent controller saw.

    Runs longer than `MAX_SEQ` are split into batches, because the sequence is
    the only thing that can tell a DROP from a REORDER, and an aliased sequence
    invents both."""
    if args.n > MAX_SEQ:
        batches, agg = [], None
        remaining = args.n
        while remaining > 0:
            sub = argparse.Namespace(**vars(args))
            sub.n = min(MAX_SEQ, remaining)
            remaining -= sub.n
            batches.append(_loss_once(sub))
        agg = {"test": "loss", "tx": args.tx, "rx": args.rx,
               "pace_ms": args.pace_ms, "batched": len(batches),
               "batch_size": MAX_SEQ,
               "note": "split into batches: the frame sequence is uint16, so a "
                       "single longer run would alias and report the aliasing "
                       "as loss"}
        for k in ("offered", "kernel_accepted", "witness_unique",
                  "witness_duplicates", "lost", "enobufs"):
            agg[k] = sum(b[k] for b in batches)
        agg["elapsed_s"] = round(sum(b["elapsed_s"] for b in batches), 3)
        agg["enobufs_backoff_s"] = round(sum(b["enobufs_backoff_s"]
                                             for b in batches), 3)
        agg["loss_rate_vs_accepted"] = (agg["lost"] / agg["kernel_accepted"]
                                        if agg["kernel_accepted"] else None)
        agg["offered_fps"] = round(agg["offered"] / agg["elapsed_s"], 1)
        agg["kernel_tx_packets"] = sum(b["link_delta_tx"].get("tx_packets", 0)
                                       for b in batches)
        agg["kernel_rx_packets"] = sum(b["link_delta_rx"].get("rx_packets", 0)
                                       for b in batches)
        agg["kernel_rx_errors"] = sum(b["link_delta_rx"].get("rx_errors", 0)
                                      for b in batches)
        agg["kernel_bus_errors"] = sum(b["link_delta_tx"].get("bus_errors", 0)
                                       for b in batches)
        # THE CROSS-CHECK. Two instruments that share no code: this harness's
        # per-sequence bookkeeping, and the kernel's own packet counters. If
        # they disagree, one of them is broken and neither number may be quoted.
        agg["kernel_agrees_with_sequence_accounting"] = (
            agg["kernel_tx_packets"] == agg["kernel_rx_packets"] ==
            agg["kernel_accepted"] and agg["lost"] == 0) or (
            agg["kernel_tx_packets"] - agg["kernel_rx_packets"] == agg["lost"])
        agg["batches"] = batches
        return agg
    return _loss_once(args)


def _loss_once(args) -> dict:
    arb = arb_for(STRESS_NS, MessageType.CONFIG_PUSH, layout=args.layout)
    seen: dict[int, int] = {}

    def on_frame(t, a, d):
        if a == arb and len(d) >= 2:
            s = (d[0] << 8) | d[1]
            seen[s] = seen.get(s, 0) + 1

    pre_tx, pre_rx = link_stats(args.tx), link_stats(args.rx)
    w = Witness(args.rx, on_frame).start()
    tx = Tx(args.tx)
    pace = args.pace_ms / 1000.0
    t0 = time.monotonic()
    offered = 0
    for s in range(1, args.n + 1):
        data = bytes([(s >> 8) & 0xFF, s & 0xFF]) + bytes([(s + i) & 0xFF
                                                           for i in range(6)])
        offered += 1
        tx.send(arb, data, retry=args.retry)
        if pace:
            time.sleep(pace)
    dt = time.monotonic() - t0
    w.stop()
    tx.close()
    post_tx, post_rx = link_stats(args.tx), link_stats(args.rx)

    accepted = tx.sent
    got = len(seen)
    dupes = sum(v - 1 for v in seen.values() if v > 1)
    missing = [s for s in range(1, args.n + 1) if s not in seen]
    return {
        "test": "loss", "tx": args.tx, "rx": args.rx,
        "pace_ms": args.pace_ms, "retry_on_enobufs": args.retry,
        "offered": offered, "kernel_accepted": accepted,
        "witness_unique": got, "witness_duplicates": dupes,
        "lost": len(missing),
        "loss_rate_vs_accepted": (len(missing) / accepted) if accepted else None,
        "first_20_missing_seqs": missing[:20],
        "enobufs": tx.enobufs, "enobufs_backoff_s": round(tx.backoff_s, 4),
        "other_errno": tx.other_err,
        "elapsed_s": round(dt, 4),
        "offered_fps": round(offered / dt, 1) if dt else None,
        "accepted_fps": round(accepted / dt, 1) if dt else None,
        "link_delta_tx": delta(pre_tx, post_tx),
        "link_delta_rx": delta(pre_rx, post_rx),
    }


# --- 2. the ENOBUFS curve --------------------------------------------------- #

def cmd_enobufs(args) -> dict:
    """At what OFFERED rate does `txqueuelen 10` start refusing frames?

    Refusals are NOT retried here — the question is where the queue gives out,
    and a retry loop answers a different question while looking like this one."""
    arb = arb_for(STRESS_NS, MessageType.CONFIG_PUSH, layout=args.layout)
    rows = []
    for pace_ms in args.paces:
        seen: set[int] = set()

        def on_frame(t, a, d, _s=seen):
            if a == arb and len(d) >= 2:
                _s.add((d[0] << 8) | d[1])

        w = Witness(args.rx, on_frame).start()
        tx = Tx(args.tx)
        pace = pace_ms / 1000.0
        t0 = time.monotonic()
        for s in range(1, args.n + 1):
            data = bytes([(s >> 8) & 0xFF, s & 0xFF, 0, 0, 0, 0, 0, 0])
            tx.send(arb, data, retry=False)
            if pace:
                time.sleep(pace)
        dt = time.monotonic() - t0
        w.stop()
        rows.append({
            "pace_ms": pace_ms,
            "offered": args.n,
            "offered_fps": round(args.n / dt, 1),
            "accepted": tx.sent,
            "accepted_fps": round(tx.sent / dt, 1),
            "refused_enobufs": tx.enobufs,
            "refusal_pct": round(100.0 * tx.enobufs / args.n, 2),
            "witness_unique": len(seen),
            "delivered_of_accepted": len(seen) - tx.sent,
            "elapsed_s": round(dt, 4),
        })
        tx.close()
        time.sleep(0.3)
    return {"test": "enobufs", "tx": args.tx, "rx": args.rx, "rows": rows,
            "txqueuelen": _qlen(args.tx)}


def _int_after(tokens: list[str], key: str) -> int | None:
    """The integer following `key`, tolerating the trailing `\\` that
    `ip -o link show` appends to the line. Written after that backslash turned
    an otherwise-complete ENOBUFS run into a ValueError at the last line."""
    for i, tok in enumerate(tokens):
        if tok == key and i + 1 < len(tokens):
            digits = "".join(c for c in tokens[i + 1] if c.isdigit())
            return int(digits) if digits else None
    return None


def _qlen(iface: str) -> int | None:
    out = subprocess.run(["ip", "-o", "link", "show", iface],
                         capture_output=True, text=True).stdout.split()
    return _int_after(out, "qlen")


# --- 3. the real 977-frame config transfer ---------------------------------- #

def make_payload(n: int) -> bytes:
    """A deterministic, non-repeating payload — repeating bytes would let a
    misplaced chunk still hash correctly."""
    return bytes((i * 167 + (i >> 8) * 29 + 7) & 0xFF for i in range(n))


@dataclass
class TransferResult:
    ok: bool
    outcome: str
    frames_offered: int
    frames_accepted: int
    frames_seen: int
    missing: list = field(default_factory=list)
    enobufs: int = 0
    elapsed_s: float = 0.0
    repair_rounds: int = 0
    repair_frames: int = 0
    induced_drops: int = 0


def _run_transfer(tx: Tx, rx_iface: str, payload: bytes, pace_ms: float,
                  *, repair: bool = False, max_rounds: int = 3,
                  retry_enobufs: bool = True, drop_every: int = 0,
                  layout: str = "current") -> TransferResult:
    """One REAL config transfer, reassembled by the REAL Reassembler.

    `repair=False` is today's protocol: all-or-nothing, a shortfall means the
    leaf discards and the whole payload is re-sent. `repair=True` adds the
    NAK-with-gaps round `Reassembler.missing()` already makes possible."""
    arb = arb_for(STRESS_NS, MessageType.CONFIG_PUSH, layout=layout)
    frames = chunk_config(payload, CAN_CONTENT_BYTES)
    asm = Reassembler()
    state = {"result": None}
    lock = threading.Lock()

    def on_frame(t, a, d):
        if a != arb:
            return
        with lock:
            r = asm.on_frame(d)
            if r in (Reassembler.COMPLETE, Reassembler.HASH_MISMATCH):
                state["result"] = r

    w = Witness(rx_iface, on_frame).start()
    pace = pace_ms / 1000.0
    t0 = time.monotonic()
    offered = accepted = induced = 0
    for i, f in enumerate(frames):
        # INDUCED LOSS, first pass only. Without it, "repair rounds = 0" is
        # indistinguishable from "the repair code never runs", which is exactly
        # the class of confirming-signal this session keeps catching. Repair
        # rounds are never dropped, or the loop could not converge and the
        # instrument would be measuring itself.
        if drop_every and i > 0 and i % drop_every == 0:
            induced += 1
            offered += 1
            continue
        offered += 1
        if tx.send(arb, f, retry=retry_enobufs):
            accepted += 1
        if pace:
            time.sleep(pace)

    rounds = repair_frames = 0
    if repair:
        for _ in range(max_rounds):
            time.sleep(0.25)                      # let the tail land
            with lock:
                if state["result"] == Reassembler.COMPLETE:
                    break
                gaps = list(asm.missing())
            if not gaps:
                break
            rounds += 1
            # NAK-with-gaps: resend ONLY the sequences the far side lacks.
            # `frames[0]` is the header (seq 0); data seq S is frames[S].
            for s in gaps:
                repair_frames += 1
                offered += 1
                if tx.send(arb, frames[s], retry=retry_enobufs):
                    accepted += 1
                if pace:
                    time.sleep(pace)
    dt = time.monotonic() - t0
    w.stop()

    with lock:
        res = state["result"]
        missing = list(asm.missing())
    ok = (res == Reassembler.COMPLETE and asm.payload == payload)
    outcome = res or ("incomplete" if missing else "no-frames")
    if res == Reassembler.COMPLETE and asm.payload != payload:
        outcome = "HASH-COLLISION-OR-BUG"       # hash matched, bytes did not
    return TransferResult(ok=ok, outcome=outcome, frames_offered=offered,
                          frames_accepted=accepted, frames_seen=asm.received,
                          missing=missing[:20], enobufs=tx.enobufs,
                          elapsed_s=round(dt, 4), repair_rounds=rounds,
                          repair_frames=repair_frames, induced_drops=induced)


def cmd_config(args) -> dict:
    payload = make_payload(args.bytes)
    frames_total = chunk_count(len(payload), CAN_CONTENT_BYTES) + 1
    pre_tx, pre_rx = link_stats(args.tx), link_stats(args.rx)
    runs = []
    for i in range(args.runs):
        tx = Tx(args.tx)
        r = _run_transfer(tx, args.rx, payload, args.pace_ms,
                          repair=args.repair, retry_enobufs=args.retry,
                          drop_every=args.drop_every, layout=args.layout)
        tx.close()
        runs.append(r.__dict__)
        time.sleep(0.2)
    post_tx, post_rx = link_stats(args.tx), link_stats(args.rx)
    ok = sum(1 for r in runs if r["ok"])
    return {
        "test": "config-repair" if args.repair else "config",
        "payload_bytes": len(payload),
        "content_hash": f"0x{content_hash(payload):04X}",
        "chunk_payload": CAN_CONTENT_BYTES,
        "frames_per_transfer": frames_total,
        "uart_frames_for_comparison": chunk_count(len(payload),
                                                  UART_CONTENT_BYTES) + 1,
        "pace_ms": args.pace_ms, "runs": args.runs,
        "induced_drop_every": args.drop_every,
        "induced_drops_total": sum(r["induced_drops"] for r in runs),
        "succeeded": ok, "failed": args.runs - ok,
        "success_rate": round(ok / args.runs, 4) if args.runs else None,
        "median_elapsed_s": sorted(r["elapsed_s"] for r in runs)[args.runs // 2]
                            if runs else None,
        "total_frames_offered": sum(r["frames_offered"] for r in runs),
        "total_frames_reassembled": sum(r["frames_seen"] for r in runs),
        "total_enobufs": sum(r["enobufs"] for r in runs),
        "repair_rounds_total": sum(r["repair_rounds"] for r in runs),
        "repair_frames_total": sum(r["repair_frames"] for r in runs),
        "link_delta_tx": delta(pre_tx, post_tx),
        "link_delta_rx": delta(pre_rx, post_rx),
        "per_run": runs,
    }


# --- 4. arbitration under contention ---------------------------------------- #

def cmd_contention(args) -> dict:
    """A config transfer on one node WHILE another node transmits throttle.

    Both interfaces are real controllers on one segment, so this is genuine
    CAN arbitration and not a software scheduling artefact. The harness reports
    WHAT IT OBSERVED; it does not change the id layout — [BP-CAN-ID] priority
    is the user's call."""
    cfg_arb = arb_for(STRESS_NS, MessageType.CONFIG_PUSH, layout=args.layout)
    thr_arb = arb_for(THROTTLE_NS, MessageType.STATE, src="cio-ctrl-db1c",
                      layout=args.layout)
    payload = make_payload(args.bytes)
    frames = chunk_config(payload, CAN_CONTENT_BYTES)

    stop = threading.Event()
    thr_tx = Tx(args.rx)              # throttle rides the OTHER interface
    thr_sent = {"n": 0}

    def throttle_loop():
        v = 0
        while not stop.is_set():
            v = (v + 1) & 0xFF
            thr_tx.send(thr_arb, bytes([v]), retry=True)
            thr_sent["n"] += 1
            time.sleep(args.throttle_ms / 1000.0)

    # Witness both streams on the interface the throttle is NOT sent from.
    order: list[tuple[float, int]] = []
    asm = Reassembler()
    lock = threading.Lock()

    def on_frame(t, a, d):
        with lock:
            order.append((t, a))
            if a == cfg_arb:
                asm.on_frame(d)

    w = Witness(args.rx, on_frame).start()
    th = threading.Thread(target=throttle_loop, daemon=True)
    th.start()
    time.sleep(0.5)

    cfg_tx = Tx(args.tx)
    t0 = time.monotonic()
    for f in frames:
        cfg_tx.send(cfg_arb, f, retry=True)
        if args.pace_ms:
            time.sleep(args.pace_ms / 1000.0)
    dt = time.monotonic() - t0
    stop.set()
    th.join(timeout=2)
    w.stop()
    cfg_tx.close()
    thr_tx.close()

    with lock:
        obs = list(order)
    cfg_seen = sum(1 for _, a in obs if a == cfg_arb)
    # Longest run of consecutive config frames with no throttle between: if the
    # config transfer STARVED the throttle, this is the whole transfer.
    longest_cfg_run = cur = 0
    gaps_between_throttle = []
    since = 0
    for _, a in obs:
        if a == cfg_arb:
            cur += 1
            since += 1
            longest_cfg_run = max(longest_cfg_run, cur)
        else:
            cur = 0
            gaps_between_throttle.append(since)
            since = 0
    return {
        "test": "contention",
        "config_arb": f"0x{cfg_arb:08X}", "throttle_arb": f"0x{thr_arb:08X}",
        "lower_id_wins": "config" if cfg_arb < thr_arb else "throttle",
        "config_iface": args.tx, "throttle_iface": args.rx,
        "throttle_period_ms": args.throttle_ms,
        "config_frames_offered": len(frames),
        "config_frames_reassembled": asm.received,
        "config_outcome": "complete" if asm.payload == payload else "incomplete",
        "config_elapsed_s": round(dt, 4),
        "throttle_offered": thr_sent["n"],
        "throttle_frames_witnessed": sum(1 for _, a in obs if a == thr_arb),
        "config_frames_witnessed": cfg_seen,
        "longest_config_run_between_throttles": longest_cfg_run,
        "median_config_frames_between_throttles":
            sorted(gaps_between_throttle)[len(gaps_between_throttle) // 2]
            if gaps_between_throttle else None,
        "config_enobufs": cfg_tx.enobufs, "throttle_enobufs": thr_tx.enobufs,
        "note": "witness sees the throttle interface's own TX as local echo; "
                "ordering is TX-completion order, which is wire order for a "
                "single controller but is NOT a cross-controller wire arbiter. "
                "candump on both interfaces is the cross-check.",
    }


# --- 4b. does a bulk CONFIG_PUSH starve the ACK that terminates it? --------- #

def _busload(iface: str, seconds: float, out: list) -> None:
    """`canbusload` — a third tool that shares no code with this harness.

    Bus UTILIZATION is the hinge of the whole priority question: arbitration
    only decides anything when the bus is contended, and a bus at 50% has idle
    slots in which a losing node simply transmits next. Asking the transmitter
    how busy the bus is would be asking the thing under test."""
    # canbusload runs until interrupted and prints one line per second per
    # interface: "<iface> <frames> <bits_total> <bits_payload> <pct>%".
    # `-e` = exact stuffed-bit accounting, so the percentage is the real wire
    # occupancy and not a payload-only underestimate.
    try:
        r = subprocess.run(["timeout", "-s", "INT", str(seconds),
                            "canbusload", f"{iface}@500000", "-e", "-b"],
                           capture_output=True, text=True, timeout=seconds + 10)
        lines = [l.strip() for l in (r.stdout or "").splitlines()
                 if iface in l and "%" in l]
        best = None
        for l in lines:
            try:
                pct = int(l.split("%")[0].split()[-1])
            except Exception:
                continue
            if best is None or pct > best[0]:
                best = (pct, l)
        out.append({"peak_pct": best[0] if best else None,
                    "peak_line": best[1] if best else None,
                    "samples": len(lines)})
    except Exception as e:
        out.append({"error": repr(e)})


def cmd_ackstarve(args) -> dict:
    """THE congestion-collapse question, measured instead of reasoned about.

    A 977-frame CONFIG_PUSH transfer is terminated by ONE ACK from the leaf. If
    the bulk transfer outranks that ACK, the transfer suppresses its own
    completion signal and the server re-pushes — a self-worsening loop. Under
    BOTH candidate layouts CONFIG_PUSH(2) outranks ACK(8): `current` decides it
    per-namespace (same ns_hash, so the type nibble breaks the tie) and
    `type-first` decides it bus-wide. So this is not a new inversion, it is an
    existing one about to be generalized — which makes measuring it now the
    only way to know whether generalizing it costs anything.

    Method: run the transfer flat out from `--tx` while the far node emits
    ACK-shaped frames at a fixed period from `--rx`. Compare ACK delivery and
    latency against a CONTROL run with no transfer at all. Both runs are
    identical except for the bulk traffic, so the difference IS the starvation.
    """
    payload = make_payload(args.bytes)
    frames = chunk_config(payload, CAN_CONTENT_BYTES)
    results = {}
    for layout in args.layouts:
        cfg_arb = arb_for(STRESS_NS, MessageType.CONFIG_PUSH, layout=layout)
        ack_arb = arb_for(STRESS_NS, MessageType.ACK, src="leaf", layout=layout)
        row = {
            "config_arb": f"0x{cfg_arb:08X}",
            "ack_arb": f"0x{ack_arb:08X}",
            "arbitration_winner": "CONFIG_PUSH" if cfg_arb < ack_arb else "ACK",
        }
        for phase in ("control", "under_load"):
            sent_at: dict[int, float] = {}
            got_at: dict[int, float] = {}
            stop = threading.Event()

            def ack_loop():
                n = 0
                while not stop.is_set():
                    n += 1
                    sent_at[n] = time.monotonic()
                    # An ACK is `ack_ok(hash, len, ackseq)` — SEVEN BINARY
                    # BYTES ([CXC-CONFIG-BUS-CHUNKS]), so it is one frame and
                    # cannot itself be chunk-starved. It was an ASCII string
                    # when this harness was written, which is why the payload
                    # below is 7 bytes of stand-in rather than a real ack: the
                    # string form was 13 bytes and could not have been sent at
                    # all, so any latency this measured was of a message the
                    # firmware could never emit.
                    ack_tx.send(ack_arb, bytes([(n >> 8) & 0xFF, n & 0xFF]) +
                                b"ackok", retry=True)
                    time.sleep(args.ack_ms / 1000.0)

            def on_frame(t, a, d):
                if a == ack_arb and len(d) >= 2:
                    got_at.setdefault((d[0] << 8) | d[1], t)

            w = Witness(args.tx, on_frame).start()
            ack_tx = Tx(args.rx)
            th = threading.Thread(target=ack_loop, daemon=True)
            th.start()
            time.sleep(0.3)

            cfg = None
            busload: list = []
            t0 = time.monotonic()
            if phase == "under_load":
                bl = threading.Thread(target=_busload,
                                      args=(args.rx, 2, busload), daemon=True)
                bl.start()
                cfg_tx = Tx(args.tx)
                acc = 0
                for f in frames:
                    if cfg_tx.send(cfg_arb, f, retry=True):
                        acc += 1
                cfg = {"frames_offered": len(frames), "frames_accepted": acc,
                       "enobufs": cfg_tx.enobufs,
                       "elapsed_s": round(time.monotonic() - t0, 4)}
                cfg_tx.close()
                bl.join(timeout=8)
            else:
                time.sleep(args.control_s)
            stop.set()
            th.join(timeout=2)
            w.stop()
            ack_tx.close()

            lat = sorted(round((got_at[n] - sent_at[n]) * 1000.0, 3)
                         for n in got_at if n in sent_at)
            arrivals = sorted(got_at.values())
            gaps = [round((b - a) * 1000.0, 2)
                    for a, b in zip(arrivals, arrivals[1:])]
            row[phase] = {
                "acks_offered": len(sent_at),
                "acks_delivered": len(got_at),
                "acks_lost": len(sent_at) - len(got_at),
                "latency_ms": {
                    "min": lat[0] if lat else None,
                    "median": lat[len(lat) // 2] if lat else None,
                    "p95": lat[int(len(lat) * 0.95)] if len(lat) > 20 else None,
                    "max": lat[-1] if lat else None,
                },
                "max_inter_arrival_ms": max(gaps) if gaps else None,
                "nominal_period_ms": args.ack_ms,
                "config": cfg,
                "canbusload": busload[0] if busload else None,
            }
        u, c = row["under_load"], row["control"]
        row["verdict"] = {
            "ack_loss_under_load": u["acks_lost"],
            "ack_latency_max_ms_control": c["latency_ms"]["max"],
            "ack_latency_max_ms_under_load": u["latency_ms"]["max"],
            "worst_ack_delay_vs_period":
                round(u["max_inter_arrival_ms"] / args.ack_ms, 2)
                if u["max_inter_arrival_ms"] else None,
            "starved": bool(u["acks_lost"]) or bool(
                u["latency_ms"]["max"] and c["latency_ms"]["max"] and
                u["latency_ms"]["max"] > 4 * max(c["latency_ms"]["max"], 1.0)),
        }
        results[layout] = row
        time.sleep(0.5)
    return {"test": "ackstarve", "tx": args.tx, "rx": args.rx,
            "config_frames": len(frames), "layouts": results}


# --- 5. fault injection ----------------------------------------------------- #

def cmd_busoff(args) -> dict:
    """Transmit into a segment with no other node. A lone transmitter gets no
    ACK, so the TEC climbs and the controller leaves ERROR-ACTIVE. Then bring
    the peer back and prove `restart-ms` recovers WITHOUT a manual `ip link`."""
    arb = arb_for(STRESS_NS, MessageType.CONFIG_PUSH, layout=args.layout)
    payload = make_payload(args.bytes)
    frames = chunk_config(payload, CAN_CONTENT_BYTES)
    steps = []

    def snap(label):
        s = {"step": label, "tx": link_stats(args.tx), "rx": link_stats(args.rx)}
        steps.append(s)
        return s

    snap("before")
    # -- half a transfer, then the peer goes away -------------------------- #
    asm = Reassembler()
    w = Witness(args.rx, lambda t, a, d: asm.on_frame(d) if a == arb else None).start()
    tx = Tx(args.tx)
    half = len(frames) // 2
    for f in frames[:half]:
        tx.send(arb, f, retry=True)
    w.stop()
    half_state = {
        "frames_sent": half,
        "reassembler_received": asm.received,
        "reassembler_payload_is_none": asm.payload is None,
        "missing_count": len(list(asm.missing())),
        "outcome": "INCOMPLETE (correct)" if asm.payload is None
                   else "COMPLETED FROM HALF A TRANSFER (DEFECT)",
    }
    snap("after-half-transfer")

    # -- now down the peer and keep transmitting --------------------------- #
    subprocess.run(["sudo", "ip", "link", "set", args.rx, "down"], check=True)
    time.sleep(0.3)
    snap("peer-down")
    tx2 = Tx(args.tx)
    t0 = time.monotonic()
    blind = 0
    for i in range(args.blind_frames):
        if tx2.send(arb, bytes([0, 1, 2, 3, 4, 5, 6, 7]), retry=False):
            blind += 1
        time.sleep(0.002)
    # A burst is refused by ENOBUFS after ~a dozen frames, which is not enough
    # failed transmissions to drive TEC past 255. Keep the queue FED instead.
    if args.blind_seconds:
        deadline = time.monotonic() + args.blind_seconds
        while time.monotonic() < deadline:
            if tx2.send(arb, bytes([0, 1, 2, 3, 4, 5, 6, 7]), retry=True,
                        backoff_s=0.001, max_tries=50):
                blind += 1
            if link_stats(args.tx)["state"] == "BUS-OFF":
                steps.append({"step": "BUS-OFF reached",
                              "tx": link_stats(args.tx),
                              "rx": link_stats(args.rx)})
                break
    blind_dt = time.monotonic() - t0
    time.sleep(1.0)
    s_down = snap("after-blind-tx")

    # -- bring the peer back; restart-ms must do the rest ------------------- #
    subprocess.run(["sudo", "ip", "link", "set", args.rx, "up"], check=True)
    time.sleep(1.5)
    s_up = snap("peer-restored")

    # -- and the link must carry a whole transfer again --------------------- #
    asm2 = Reassembler()
    w2 = Witness(args.rx, lambda t, a, d: asm2.on_frame(d) if a == arb else None).start()
    tx3 = Tx(args.tx)
    for f in frames:
        tx3.send(arb, f, retry=True)
    w2.stop()
    tx.close(); tx2.close(); tx3.close()
    snap("after-recovery-transfer")

    return {
        "test": "busoff",
        "half_transfer": half_state,
        "blind_tx_frames_accepted": blind,
        "blind_tx_offered": args.blind_frames,
        "blind_tx_enobufs": tx2.enobufs,
        "blind_tx_other_errno": tx2.other_err,
        "blind_elapsed_s": round(blind_dt, 3),
        "blind_seconds_requested": args.blind_seconds,
        "bus_off_reached": any(s["tx"]["state"] == "BUS-OFF" for s in steps)
                           or any(s["tx"].get("bus_off", 0) for s in steps),
        "state_while_peer_down": s_down["tx"]["state"],
        "state_after_peer_restored": s_up["tx"]["state"],
        "restart_ms": _restart_ms(args.tx),
        "recovery_transfer_complete": asm2.payload == payload,
        "recovery_transfer_frames": asm2.received,
        "counters": steps,
    }


def _restart_ms(iface: str) -> int | None:
    out = subprocess.run(["ip", "-d", "link", "show", iface],
                         capture_output=True, text=True).stdout.split()
    return _int_after(out, "restart-ms")


# --- main -------------------------------------------------------------------- #

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tx", default="can0")
    p.add_argument("--rx", default="can1", help="the INDEPENDENT WITNESS")
    p.add_argument("--json", action="store_true", help="machine-readable only")
    p.add_argument("--layout", choices=LAYOUTS, default="current",
                   help="arbitration id layout to EMIT. `current` is what the "
                        "bridge sends today; `type-first` is the proposal, "
                        "measurable here before it is landed anywhere.")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("loss"); s.set_defaults(fn=cmd_loss)
    s.add_argument("--n", type=int, default=2000)
    s.add_argument("--pace-ms", type=float, default=1.0)
    s.add_argument("--retry", action="store_true",
                   help="retry on ENOBUFS (measures WIRE loss, not queue loss)")

    s = sub.add_parser("enobufs"); s.set_defaults(fn=cmd_enobufs)
    s.add_argument("--n", type=int, default=1000)
    s.add_argument("--paces", type=float, nargs="+",
                   default=[5, 2, 1, 0.75, 0.5, 0.4, 0.3, 0.2, 0.1, 0])

    s = sub.add_parser("config"); s.set_defaults(fn=cmd_config)
    s.add_argument("--bytes", type=int, default=5856)
    s.add_argument("--runs", type=int, default=20)
    s.add_argument("--pace-ms", type=float, default=0.0)
    s.add_argument("--retry", action="store_true", default=True)
    s.add_argument("--no-retry", dest="retry", action="store_false")
    s.add_argument("--repair", action="store_true",
                   help="add the NAK-with-gaps selective repair round")
    s.add_argument("--drop-every", type=int, default=0,
                   help="INDUCED loss: skip every Nth frame on the first pass. "
                        "Proves the repair path actually runs, so a report of "
                        "'0 repair rounds' means 'none were needed' and not "
                        "'the code is dead'.")

    s = sub.add_parser("contention"); s.set_defaults(fn=cmd_contention)
    s.add_argument("--bytes", type=int, default=5856)
    s.add_argument("--pace-ms", type=float, default=0.0)
    s.add_argument("--throttle-ms", type=float, default=20.0)

    s = sub.add_parser("ackstarve"); s.set_defaults(fn=cmd_ackstarve)
    s.add_argument("--bytes", type=int, default=5856)
    s.add_argument("--ack-ms", type=float, default=20.0)
    s.add_argument("--control-s", type=float, default=3.0)
    s.add_argument("--layouts", nargs="+", choices=LAYOUTS, default=list(LAYOUTS))

    s = sub.add_parser("busoff"); s.set_defaults(fn=cmd_busoff)
    s.add_argument("--bytes", type=int, default=5856)
    s.add_argument("--blind-frames", type=int, default=300)
    s.add_argument("--blind-seconds", type=float, default=0.0,
                   help="keep the transmit queue FED for this long with the "
                        "peer down. Bus-off needs TEC>255, which needs "
                        "sustained failed transmissions, not a burst that "
                        "ENOBUFS refuses after a dozen frames.")

    args = p.parse_args(argv)
    if os.name != "posix" or not Path("/sys/class/net").exists():
        print("This harness needs Linux SocketCAN. Run it on the Pi.",
              file=sys.stderr)
        return 2
    res = args.fn(args)
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
