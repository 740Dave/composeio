#!/usr/bin/env python3
"""config_server — the HEAD side of [SPEC:CXC-CONFIG-BUS].

A display board slaved over UART (or hanging off a relay on CAN) has no IP and
cannot GET its scene from bus_storage. This is the peer that dereferences on its
behalf and streams the CONTENT over ComposeBus itself.

    leaf  --CONFIG_REQUEST-->  _device/<id>/config
    head  --CONFIG_PUSH---->   header frame, then N sequenced data frames
    leaf  --ACK----------->    7 binary bytes: status, hash, length, ackseq

THE ACK IS CHECKED, NOT TRUSTED (2026-08-13). It used to be a formatted string
and the check was `text.startswith("ok")` — so "ok" carrying the WRONG hash was
counted as a success, on UART, where the string fit. On CAN it fit nowhere:
`"ok beef 5856"` is 13 bytes against an 8-byte frame, so `acks_ok`/`acks_bad`
were structurally incapable of incrementing over CAN at all. Both halves of that
are fixed here: the ACK is binary ([CXC-CONFIG-BUS-CHUNKS]) and `_on_ack`
compares its hash AND its length against what this server actually served.

AND SILENCE IS AN OUTCOME (2026-08-13, second pass). Making the ACK sendable and
checkable left the other half of the instrument missing: the ACK could arrive,
and NOTHING noticed when it did not. A head that can hear success but not
silence cannot tell a completed transfer from a leaf that died mid-reassembly —
and on CAN, where the same 5,856 B scene is 977 frames rather than UART's 25,
silence is the LIKELY failure. `check_timeouts()` is that half: every served
transfer is PENDING until something resolves it, and an unresolved one past its
deadline is counted, logged and (optionally) reported through `on_timeout`.

A TIMEOUT IS A DIAGNOSIS, NOT A RETRY TRIGGER. Nothing here re-pushes. The
reason is the same one `_on_ack` already gives for a failed ACK: an immediate
re-push from both ends is how you get two interleaved transfers, which is the
one thing the content hash exists to catch. The leaf re-requests on its own with
backoff ([CXC-CONFIG-BUS-OPEN]); the head's job is to SAY the transfer went
unanswered and let a human or a policy decide.

DP-CIO-18 EXCEPTION, STATED ON PURPOSE. That principle says keep messages under
256 bytes and reference large payloads BY URL. A URL presumes a peer that can
dereference it; a slaved board cannot. So this artifact class gets an explicit
exception rather than a quiet workaround ([CXC-CONFIG-BUS-INTENT]).

SUBSTRATE-AGNOSTIC BY CONSTRUCTION. This class takes a `send(BusMessage)`
callable and a `chunk_payload` size and knows nothing else about the wire. That
is not decoration: `chunk_payload` is 250 on UART and 6 on classic CAN, and the
whole chunk format exists to make those the SAME code path
([CXC-CONFIG-BUS-BUDGET]). Wiring it to a CAN transport is a constructor
argument, not a port.

FETCH-OR-HOLD IS A DEPLOYMENT DETAIL AND THE PROTOCOL DOES NOT ENCODE IT. The
scene arrives through an injected `fetch(device_id) -> bytes | None`. Whether
that reads bus_storage over HTTP, a local file, or an in-memory cache is the
head node's business; nothing on the wire changes.

LIVE PUSH IS THE MECHANISM, BOOT-TIME PULL IS THE DEGENERATE CASE
([CXC-CONFIG-BUS-PUSH]). `poll_once()` re-reads each known device's scene and
pushes an UNSOLICITED transfer when the content hash changed. That is the
capability the WiFi-bootstrap strategy can never offer — a board that killed its
radio can only be told something over the bus.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from composeio.config_chunk import (
    ACK_BYTES, ACK_STATUS_NAMES, CAN_CONTENT_BYTES, UART_CONTENT_BYTES,
    TTL_SEED, ConfigAckError, ConfigChunkError, chunk_config, chunk_count,
    content_hash, decode_ack,
)
from composeio.transport import BusMessage, MessageType, Transport

log = logging.getLogger("uart_mqtt_bridge.config")

#: `_device/<id>/config` — THE NAMESPACE IS THE ADDRESS
#: ([CXC-CONFIG-BUS-ADDRESSING]). There is no destination field anywhere in the
#: chunk format because this string already is one.
def config_namespace(device_id: str) -> str:
    return f"_device/{device_id}/config"


def status_namespace(device_id: str) -> str:
    return f"_device/{device_id}/status"


class BusStorageScenes:
    """The default fetcher: bus_storage's assets namespace over HTTP.

    Mirrors the firmware's own HTTP path (cyd/src/config_fetch.cpp) exactly —
    per-device asset first, then the tier default — so `config: bus` and
    `config: http` deliver byte-identical scenes and a board cannot render
    differently depending on how its config reached it.
    """

    def __init__(self, base_url: str = "http://mqtt-broker.local:8420",
                 timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> Optional[bytes]:
        url = f"{self.base_url}/api/v1/assets/{path}"
        try:
            with urlopen(url, timeout=self.timeout) as r:
                if r.status != 200:
                    log.warning("GET %s -> HTTP %s", url, r.status)
                    return None
                return r.read()
        except HTTPError as e:
            if e.code != 404:
                log.warning("GET %s -> HTTP %s", url, e.code)
            return None
        except (URLError, OSError) as e:
            # A down store is an ENVIRONMENT condition, not a broken server:
            # say so and keep running (transport_registry.py's second rule).
            log.error("bus_storage UNREACHABLE at %s (%s) — this device's "
                      "config cannot be served until it is back", url, e)
            return None

    def __call__(self, device_id: str) -> Optional[bytes]:
        body = self._get(f"displays/{device_id}.compose.yaml")
        if body is not None:
            return body
        log.warning("no per-device scene for %s; falling back to "
                    "displays/default.compose.yaml", device_id)
        return self._get("displays/default.compose.yaml")


class BulkTransportRefused(RuntimeError):
    """This transport cannot carry a config transfer, and will not be asked to.

    Raised at WIRING time, not at the first frame. A config transfer is opaque
    bytes with its own internal framing; a JSON-envelope transport cannot carry
    it at all (`BusMessage.to_json` now says so by name), and a transport whose
    frame size this module does not know cannot have its `chunk_payload`
    guessed — a wrong guess produces chunks the substrate silently drops.
    """


#: transport class -> content bytes per DATA frame. Two entries, because there
#: are exactly two FRAMED substrates in the fleet, and the numbers are the ones
#: the chunk format is defined against ([CXC-CONFIG-BUS-BUDGET]).
def chunk_payload_for_transport(transport: Transport) -> int:
    """The chunk size THIS substrate can carry, or a loud refusal.

    BULK MUST NOT ROUTE THROUGH MQTT, and that is a design statement rather
    than a missing feature: `transport_mqtt` serialises every message with
    `json.dumps(message.to_json())`, and a config chunk's value is raw bytes
    with its own `[seq(2)][content]` framing. JSON has no byte type. Base64ing
    it here would make the head and the leaf disagree about what a frame is,
    which is a fork of the wire format for one substrate's convenience — the
    exact shape [BP-TYPE-CODES] and [BP-CAN-ID-DIVERGENCE] have already cost
    this repo twice. A leaf that needs a scene is reachable over UART or CAN,
    or it is reachable by URL and does not need this protocol at all.
    """
    try:
        from composeio.transport_can import CANTransport
        if isinstance(transport, CANTransport):
            return CAN_CONTENT_BYTES
    except ImportError:                                   # pragma: no cover
        pass
    try:
        from composeio.transport_uart import UARTTransport
        if isinstance(transport, UARTTransport):
            return UART_CONTENT_BYTES
    except ImportError:                                   # pragma: no cover
        pass
    name = type(transport).__name__
    if "MQTT" in name.upper():
        raise BulkTransportRefused(
            f"{name} serialises every message as JSON, and a config chunk is "
            f"OPAQUE BYTES with its own framing — the two are incompatible by "
            f"design, not by bug. Bind the ConfigServer to the FRAMED "
            f"transport (CANTransport / UARTTransport) that actually reaches "
            f"the leaf; the bridge translates the leaf's REQUEST, not the "
            f"6 KB of payload.")
    raise BulkTransportRefused(
        f"{name} has no known frame size, so `chunk_payload` cannot be derived "
        f"from it. Pass `chunk_payload=` explicitly if you know what this "
        f"substrate carries — a guessed chunk size produces frames the wire "
        f"drops without telling anyone.")


# --------------------------------------------------------------------------- #
# The ACK deadline                                                             #
# --------------------------------------------------------------------------- #
# THE TIMEOUT SCALES WITH THE TRANSFER, AND IT HAS TO. The same 5,856 B scene is
# 25 frames on UART and 977 on CAN; at the default 2 ms pacing that is 50 ms of
# wire time against 1.95 SECONDS. A fixed one-second deadline — the obvious
# choice, and the wrong one — would fire on every healthy CAN transfer, and an
# alarm that cries on the happy path is worse than no alarm at all: it teaches
# the operator to ignore the one time it was right.
#
#     timeout = ACK_TIMEOUT_BASE_S + ACK_TIMEOUT_WIRE_MARGIN * wire_s
#     wire_s  = max(frames * pace_s, measured_send_seconds)
#
# WHY BOTH TERMS OF THAT `max`. The measured send duration is the truth on a
# BLOCKING transport (UARTTransport writes and returns), and it captures wire
# time this module has no business modelling — bitrate, arbitration, a relay
# hop. But a transport that QUEUES and returns immediately makes the measurement
# a lie: it would report ~0 s for a transfer that has not left yet. `frames *
# pace_s` is the floor that survives that case, because the pacing sleeps are
# real time this process actually spent. Neither alone is safe; the larger of
# the two is.
#
# THE MARGIN IS x2 ON WIRE TIME, and it buys exactly one thing: the leaf may
# take as long again to DRAIN what the head took to send. A leaf's rx ring is
# 1 KB, its rx task decodes and copies every frame, and it is competing with an
# LVGL render task whose full-screen flush is ~65 ms. Doubling says "the slowest
# healthy leaf runs at half the head's send rate"; a leaf slower than that is
# dropping frames and has a different problem.
#
# THE BASE IS 2 s, and it covers everything that does NOT scale with frame
# count: task scheduling latency on the leaf, the whole-payload CRC-16 (~47k
# iterations for a 5,856 B scene — under 2 ms at 240 MHz, genuinely noise), the
# ACK's own trip back, and up to a couple of relay hops. Two seconds is >25x the
# worst plausible leaf-side turnaround, and the cost of being generous is only
# that a REAL silence is reported two seconds later than it could have been.
# Nothing retries off this deadline, so late costs nothing and early costs the
# credibility of the instrument.
#
# WORKED NUMBERS, the two that matter:
#     CAN  977 frames @ 2 ms -> 2.0 + 2.0 * 1.954 = 5.91 s
#     UART  25 frames @ 2 ms -> 2.0 + 2.0 * 0.050 = 2.10 s
# and the protocol ceiling (65,535 B over CAN, 10,923 frames) lands at 45.7 s,
# which is deliberately NOT capped: a capped deadline would be a fixed deadline
# again for exactly the largest transfers, where silence is most likely.

#: Seconds every deadline gets regardless of size. See above.
ACK_TIMEOUT_BASE_S = 2.0

#: Multiplier applied to the transfer's own wire time. See above.
ACK_TIMEOUT_WIRE_MARGIN = 2.0

#: How often `run_watch` looks for expired deadlines. Independent of `poll_sec`
#: (15 s) on purpose: polling for CHANGED scenes and noticing an unanswered
#: transfer are different jobs on different timescales, and hanging the second
#: off the first would put a 15 s granularity on a 2 s deadline.
ACK_CHECK_SEC = 1.0


def ack_timeout_seconds(frames: int, pace_s: float, sent_s: float = 0.0,
                        base_s: float = ACK_TIMEOUT_BASE_S,
                        margin: float = ACK_TIMEOUT_WIRE_MARGIN) -> float:
    """The ACK deadline for a transfer of `frames` frames. Pure; testable."""
    wire_s = max(max(0, int(frames)) * max(0.0, pace_s), max(0.0, sent_s))
    return max(0.0, base_s) + max(0.0, margin) * wire_s


@dataclass
class PendingTransfer:
    """A transfer that has been SENT and not yet answered.

    It exists so that "no ACK" is a state this server can be IN, rather than
    the absence of one — which is what made the silence undetectable.
    """
    device_id: str
    hash: int
    total_len: int
    frames: int
    reason: str
    sent_at: float
    deadline: float

    def waited(self, now: float) -> float:
        return now - self.sent_at

    def __str__(self) -> str:
        return (f"hash={self.hash:04x} len={self.total_len} "
                f"frames={self.frames} reason={self.reason}")


@dataclass
class DeviceCensus:
    """Per-device outcomes, split so that WRONG and SILENT stay different.

    Two groups, and the split is the point:

    TRANSFER VERDICTS — every transfer this server sends reaches exactly one of
    these, or is still `pending`. They sum to `transfers`.
      confirmed   the leaf ACKed THIS transfer's hash and length, status OK
      refused     the leaf ACKed THIS transfer with a failure status, kept
                  BROKEN OUT BY STATUS: a hash mismatch is corruption, an
                  `incomplete` is an abandoned reassembly, `no-memory` is the
                  heap — three causes, three repairs, so never one bucket
      timeout     the deadline passed with nothing naming it. SILENCE: the leaf
                  or the link died, or the ACK could not leave the leaf
      superseded  a newer transfer to the same device replaced it before it was
                  answered. NOT a timeout — see `serve_payload`

    ACK MESSAGE COUNTERS — about values that arrived, not about transfers, and
    they deliberately do NOT resolve a pending transfer:
      unreadable  a value on the config namespace that is not a decodable ACK
                  (a corrupt frame, or an un-reflashed leaf's retired text ACK)
      unmatched   a decodable ACK naming a transfer this head never served
                  (a second head on the segment, or a restarted process)
      stale       an ACK naming a transfer whose verdict is already in — a
                  duplicate, or one that arrived AFTER its deadline. Counted
                  rather than promoted to `confirmed`, because an instrument
                  that reports both success and silence for one transfer is
                  lying about one of them.
    """
    confirmed: int = 0
    timeout: int = 0
    superseded: int = 0
    refused: Dict[int, int] = field(default_factory=dict)
    unreadable: int = 0
    unmatched: int = 0
    stale: int = 0

    @property
    def refused_total(self) -> int:
        return sum(self.refused.values())

    @property
    def bad(self) -> int:
        """The legacy `acks_bad` bucket: the leaf reported a failure, or said
        something this head could not read. Both are 'not confirmed'."""
        return self.refused_total + self.unreadable

    @property
    def resolved(self) -> int:
        return (self.confirmed + self.refused_total + self.timeout
                + self.superseded)

    def refused_summary(self) -> str:
        if not self.refused:
            return "none"
        return ", ".join(
            f"{ACK_STATUS_NAMES.get(s, f'unallocated({s})')}={n}"
            for s, n in sorted(self.refused.items()))

    def __str__(self) -> str:
        return (f"confirmed={self.confirmed} timeout={self.timeout} "
                f"superseded={self.superseded} "
                f"refused={self.refused_total} ({self.refused_summary()}) "
                f"unreadable={self.unreadable} unmatched={self.unmatched} "
                f"stale={self.stale}")


class ConfigServer:
    """Serve `_device/<id>/config` transfers over any ComposeBus substrate."""

    def __init__(self,
                 send: Callable[[BusMessage], None],
                 fetch: Callable[[str], Optional[bytes]],
                 devices: List[str],
                 chunk_payload: int = UART_CONTENT_BYTES,
                 pace_ms: float = 2.0,
                 ttl: int = TTL_SEED,
                 poll_sec: float = 15.0,
                 ack_timeout_base_s: float = ACK_TIMEOUT_BASE_S,
                 ack_timeout_margin: float = ACK_TIMEOUT_WIRE_MARGIN,
                 ack_check_sec: float = ACK_CHECK_SEC,
                 on_timeout: Optional[Callable[["PendingTransfer"], None]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._send = send
        self._fetch = fetch
        self.devices = list(devices)
        self.chunk_payload = int(chunk_payload)
        # INTER-FRAME PACING is a WIRE property, not politeness. A 6 KB scene
        # is ~25 back-to-back 255-byte UART frames; the leaf's rx ring is
        # 1 KB and its rx task also decodes and copies each frame. A few ms
        # between frames costs ~50 ms on a transfer that happens once and
        # removes a whole class of "it worked on the bench" overrun.
        self.pace_s = max(0.0, float(pace_ms) / 1000.0)
        self.ttl = int(ttl)
        self.poll_sec = float(poll_sec)
        self.ack_timeout_base_s = float(ack_timeout_base_s)
        self.ack_timeout_margin = float(ack_timeout_margin)
        self.ack_check_sec = max(0.05, float(ack_check_sec))
        #: Called (outside the lock) with the `PendingTransfer` that went
        #: unanswered. A SEAM, not a policy: this module refuses to invent a
        #: namespace to complain on, and equally refuses to make the operator
        #: grep a log for the one line that matters.
        self._on_timeout = on_timeout
        #: Injected so a test can drive a 977-frame deadline without sleeping
        #: six seconds — which is the difference between a regression test that
        #: runs and one that gets deleted for being slow. `sleep` goes with it:
        #: the pacing sleep is 1.95 s of REAL time for a CAN-sized transfer, and
        #: the alternative to stubbing it is a test that runs with `pace_ms=0`
        #: and therefore never exercises the term of the deadline that the
        #: pacing actually drives.
        self._clock = clock
        self._sleep = sleep

        self._last_hash: Dict[str, int] = {}
        # WHAT IS IN FLIGHT, so the ACK can be CHECKED against it and so its
        # ABSENCE is a state rather than a nothing. Until the binary ACK there
        # was nothing to check: `on_message` tested `text.startswith("ok")` and
        # never compared the returned hash to the one it served, so "ok" plus
        # the WRONG hash counted as a success — on UART, today, where the ACK
        # actually fit. And until `check_timeouts` there was nothing to notice:
        # a transfer that was never answered left no trace at all.
        self._pending: Dict[str, PendingTransfer] = {}
        #: The last few (hash, len) pairs this server has already RESOLVED per
        #: device, so a duplicate or post-deadline ACK is reported as `stale`
        #: rather than as `unmatched`. Four deep because the realistic depth is
        #: one (a supersede) and an unbounded set is a leak with a nicer name.
        self._resolved: Dict[str, Deque[Tuple[int, int]]] = {}
        self._census: Dict[str, DeviceCensus] = {}
        # REENTRANT, because `serve_payload` resolves expired deadlines while
        # holding it and `check_timeouts` takes it too. A plain Lock deadlocks
        # on that path.
        self._lock = threading.RLock()
        self.transfers = 0
        self.frames = 0
        self.requests = 0
        #: Set by `for_transport`, so a device that turns up unannounced can
        #: have its namespace registered for RECEIVE as well as served. On CAN
        #: that is not decoration: the wire carries a 16-bit hash and nothing
        #: else, so an unsubscribed namespace is unresolvable on arrival.
        self._transport: Optional[Transport] = None

    # -- the census ------------------------------------------------------
    #
    # COUNTED BY DEVICE, and summed for the fleet-wide view. Per-device is the
    # granularity that answers the operator's actual question: "is the CAN
    # segment sick, or is that ONE board dead?" A single global `acks_timeout`
    # cannot tell those apart, and they have nothing in common.

    def census(self, device_id: str) -> DeviceCensus:
        """This device's outcomes. Creates the row; a device with a clean
        record must still HAVE a record, or a zero is indistinguishable from
        an absence."""
        with self._lock:
            row = self._census.get(device_id)
            if row is None:
                row = self._census[device_id] = DeviceCensus()
            return row

    def census_rows(self) -> List[Tuple[str, DeviceCensus]]:
        with self._lock:
            return sorted(self._census.items())

    def _total(self, attr: str) -> int:
        with self._lock:
            return sum(getattr(row, attr) for row in self._census.values())

    #: The fleet sums. Properties now rather than plain ints — the numbers live
    #: in the per-device census and these are the view, so the two can never
    #: drift apart the way two hand-incremented counters do.
    @property
    def acks_ok(self) -> int:
        return self._total("confirmed")

    @property
    def acks_bad(self) -> int:
        return self._total("bad")

    @property
    def acks_unmatched(self) -> int:
        return self._total("unmatched")

    @property
    def acks_unreadable(self) -> int:
        return self._total("unreadable")

    @property
    def acks_refused(self) -> int:
        return self._total("refused_total")

    @property
    def acks_stale(self) -> int:
        return self._total("stale")

    @property
    def acks_timeout(self) -> int:
        """Transfers that went UNANSWERED. The counter this class was missing:
        `acks_ok` and `acks_bad` between them can only report a leaf that
        SPOKE."""
        return self._total("timeout")

    @property
    def acks_superseded(self) -> int:
        return self._total("superseded")

    @property
    def pending_transfers(self) -> int:
        with self._lock:
            return len(self._pending)

    def pending(self, device_id: str) -> Optional[PendingTransfer]:
        with self._lock:
            return self._pending.get(device_id)

    def counters(self) -> Dict[str, int]:
        """One dict for a status line or a `_device/<id>/status` publish."""
        return {
            "requests": self.requests,
            "transfers": self.transfers,
            "frames": self.frames,
            "acks_ok": self.acks_ok,
            "acks_refused": self.acks_refused,
            "acks_timeout": self.acks_timeout,
            "acks_superseded": self.acks_superseded,
            "acks_unreadable": self.acks_unreadable,
            "acks_unmatched": self.acks_unmatched,
            "acks_stale": self.acks_stale,
            "pending": self.pending_transfers,
        }

    # -- wiring ----------------------------------------------------------

    @classmethod
    def for_transport(cls, transport: Transport,
                      fetch: Callable[[str], Optional[bytes]],
                      devices: List[str],
                      *, chunk_payload: Optional[int] = None,
                      subscribe: bool = True,
                      **kwargs) -> "ConfigServer":
        """Bind a ConfigServer DIRECTLY to one framed transport — [D3].

        The seam always existed (`send` is a callable and `chunk_payload` is an
        argument); what did not exist was a caller that used it, so every real
        deployment ran the config service inside a bridge and the "bulk over
        MQTT" question was never forced. This is the one-line answer:

            srv = ConfigServer.for_transport(CANTransport("can0"),
                                             BusStorageScenes(), ["leaf-1"])

        No MQTT client is constructed, imported or reachable from this path.
        """
        cp = (chunk_payload_for_transport(transport)
              if chunk_payload is None else int(chunk_payload))
        srv = cls(send=transport.send, fetch=fetch, devices=devices,
                  chunk_payload=cp, **kwargs)
        srv._transport = transport
        if subscribe:
            for device_id in list(srv.devices):
                srv._subscribe_device(device_id)
        log.info("ConfigServer bound to %s: %d B/chunk, %d device(s), no "
                 "MQTT in the path", type(transport).__name__, cp,
                 len(srv.devices))
        return srv

    def _subscribe_device(self, device_id: str) -> None:
        if self._transport is None:
            return
        self._transport.subscribe(config_namespace(device_id), self.on_message)

    # -- serving ---------------------------------------------------------

    def serve(self, device_id: str, *, reason: str = "request") -> bool:
        """Chunk this device's scene and push it. Returns False when there is
        nothing to serve — LOUDLY, because a leaf that asked and got silence
        has no way to tell 'no scene' from 'no wire'."""
        payload = self._fetch(device_id)
        if payload is None:
            log.error("NO SCENE for %s — the leaf's CONFIG_REQUEST cannot be "
                      "answered. It will keep re-requesting with backoff and "
                      "will ANNOUNCE; that is the intended behaviour, but the "
                      "asset is missing from bus_storage.", device_id)
            return False
        return self.serve_payload(device_id, payload, reason=reason)

    def serve_payload(self, device_id: str, payload: bytes, *,
                      reason: str = "request") -> bool:
        ns = config_namespace(device_id)
        chash = content_hash(payload)
        try:
            frames = chunk_config(payload, self.chunk_payload, self.ttl)
        except ConfigChunkError as e:
            log.error("REFUSING to serve %s: %s", device_id, e)
            return False

        # DEADLINES FIRST, and the order is the whole superseded question.
        # A pending transfer whose deadline has ALREADY passed is a TIMEOUT —
        # it was silent for its entire window, and this new push had nothing to
        # do with that. Only a still-live pending transfer is SUPERSEDED.
        self.check_timeouts()

        t0 = self._clock()
        with self._lock:
            prev = self._pending.pop(device_id, None)
            if prev is not None:
                # SUPERSEDED IS NOT A TIMEOUT, and conflating them would poison
                # the silence counter with the head's own behaviour. When a
                # second header reaches the leaf mid-transfer its reassembler
                # ABANDONS the first (`Reassembler.RESTARTED`, and the firmware
                # then ACKs the old hash as `incomplete`), so the first transfer
                # can no longer be confirmed BY CONSTRUCTION. Charging that to
                # `timeout` would make a poll-driven scene change look like a
                # dead link, and an alarm that fires on normal operation is the
                # failure mode this deadline exists to avoid.
                self._resolve(prev, "superseded")
                self.census(device_id).superseded += 1
                log.warning("SUPERSEDING an unanswered transfer to %s (%s, "
                            "%.1f s in, deadline was %.1f s) with hash %04x — "
                            "the leaf will abandon the first one and ACK the "
                            "second. NOT counted as a timeout: this head, not "
                            "the wire, is what made the first ACK impossible.",
                            device_id, prev, prev.waited(t0),
                            prev.deadline - prev.sent_at, chash)

            # IN FLIGHT FROM THE FIRST FRAME, NOT THE LAST. Registering the
            # transfer after the send loop looks natural and is wrong: on a
            # synchronous substrate (a loopback, an in-process relay, a leaf
            # faster than the pacing) the ACK arrives INSIDE `self._send` for
            # the final frame, and a transfer that is not yet pending gets its
            # own ACK counted as `unmatched` — the head declaring a second head
            # on the segment because it had not finished writing itself a note.
            # Found by `test_the_same_can_loopback_stays_quiet_when_the_leaf_
            # DOES_ack`, which is why that test sends its ACK from the rx
            # callback rather than after `serve()` returns.
            self.transfers += 1
            self._last_hash[device_id] = chash
            timeout_s = ack_timeout_seconds(
                len(frames), self.pace_s, base_s=self.ack_timeout_base_s,
                margin=self.ack_timeout_margin)
            pending = PendingTransfer(
                device_id=device_id, hash=chash, total_len=len(payload),
                frames=len(frames), reason=reason, sent_at=t0,
                deadline=t0 + timeout_s)
            self._pending[device_id] = pending
            self.census(device_id)          # a served device always has a row

            for i, value in enumerate(frames):
                self._send(BusMessage(namespace=ns,
                                      type=MessageType.CONFIG_PUSH,
                                      value=value, scope="global",
                                      source="config-server"))
                self.frames += 1
                if self.pace_s and i + 1 < len(frames):
                    self._sleep(self.pace_s)

            t_end = self._clock()
            dt = t_end - t0
            if self._pending.get(device_id) is pending:
                # RE-ARMED FROM THE LAST FRAME. The deadline budgets the LEAF's
                # turnaround, so it starts when the last frame is out — and only
                # now is the MEASURED send time known, which is the term that
                # keeps a transport slower than its pacing from being declared
                # silent. Skipped when a fast leaf has already answered: that
                # transfer's verdict is in and re-arming would resurrect it.
                timeout_s = ack_timeout_seconds(
                    len(frames), self.pace_s, sent_s=dt,
                    base_s=self.ack_timeout_base_s,
                    margin=self.ack_timeout_margin)
                pending.sent_at = t_end
                pending.deadline = t_end + timeout_s

        log.info("CONFIG_PUSH %s (%s): %d B, hash %04x, %d data frames of "
                 "%d B + 1 header, %d wire bytes, %.0f ms; ACK due within "
                 "%.2f s",
                 ns, reason, len(payload), chash,
                 chunk_count(len(payload), self.chunk_payload),
                 self.chunk_payload,
                 sum(len(f) for f in frames), dt * 1000.0, timeout_s)
        return True

    # -- deadlines -------------------------------------------------------

    def _resolve(self, pending: PendingTransfer, verdict: str) -> None:
        """Record that this transfer's verdict is IN. Caller holds the lock."""
        seen = self._resolved.setdefault(pending.device_id, deque(maxlen=4))
        seen.append((pending.hash, pending.total_len))
        log.debug("transfer %s for %s resolved: %s", pending,
                  pending.device_id, verdict)

    def _was_resolved(self, device_id: str, chash: int, total_len: int) -> bool:
        return (chash, total_len) in self._resolved.get(device_id, ())

    def check_timeouts(self, now: Optional[float] = None) -> int:
        """Report every transfer whose ACK deadline has passed. Returns how
        many fired.

        THE OTHER HALF OF THE INSTRUMENT. `_on_ack` can only ever report a leaf
        that SPOKE; this is the only path by which a leaf that went quiet — the
        likely failure on a 977-frame CAN transfer — becomes a number and a log
        line instead of nothing at all.

        IT DOES NOT RE-PUSH, and that is deliberate. `_on_ack` already states
        the reason for the failure case and it is the same here: an immediate
        re-push from both ends is how you get two interleaved transfers, which
        is the one thing the content hash exists to catch. The leaf re-requests
        on its own with exponential backoff ([CXC-CONFIG-BUS-OPEN]); a head that
        also retried would race it. A timeout is a DIAGNOSIS.

        Safe to call from anywhere and at any rate: it is O(devices in flight)
        and takes no wall-clock time. `run_watch` calls it every
        `ack_check_sec`; `serve_payload` and `_on_ack` call it opportunistically
        so that even a deployment with no watch thread still notices.
        """
        now = self._clock() if now is None else now
        fired: List[PendingTransfer] = []
        with self._lock:
            for device_id, p in list(self._pending.items()):
                if now >= p.deadline:
                    del self._pending[device_id]
                    self._resolve(p, "timeout")
                    self.census(device_id).timeout += 1
                    fired.append(p)

        for p in fired:
            row = self.census(p.device_id)
            log.error("NO ACK from %s after %.2f s: transfer %s went "
                      "UNANSWERED. The leaf never confirmed it, never rejected "
                      "it, and said nothing at all — which on this substrate "
                      "(%d frames) most likely means the leaf or the link "
                      "died mid-reassembly, or its ACK could not leave. NOT "
                      "re-pushing: the leaf re-requests with backoff and two "
                      "transfers in flight is the fault the content hash "
                      "exists to catch. This device: %s",
                      p.device_id, p.waited(now), p, p.frames, row)
            if self._on_timeout is not None:
                try:
                    self._on_timeout(p)
                except Exception:               # noqa: BLE001
                    # A broken reporter must not take the server down, and must
                    # not silently swallow the very event it was given.
                    log.exception("on_timeout handler raised for %s (%s); the "
                                  "timeout above still stands", p.device_id, p)
        return len(fired)

    # -- the leaf's side of the conversation -----------------------------

    def on_message(self, msg: BusMessage) -> bool:
        """Handle a CONFIG_REQUEST or ACK arriving from a leaf.

        Returns True when the message was CONSUMED here."""
        ns = str(msg.namespace)
        if not (ns.startswith("_device/") and ns.endswith("/config")):
            return False
        device_id = ns[len("_device/"):-len("/config")]

        if msg.type == MessageType.CONFIG_REQUEST:
            self.requests += 1
            log.info("CONFIG_REQUEST from %s", device_id)
            if device_id not in self.devices:
                # Serve it anyway — the request IS the address, and refusing a
                # device merely because it was not on a command line would make
                # the head the thing that has to be configured.
                log.warning("%s was not pre-registered; serving it and adding "
                            "it to the watch set", device_id)
                self.devices.append(device_id)
                self._subscribe_device(device_id)
            self.serve(device_id, reason="request")
            return True

        if msg.type == MessageType.ACK:
            self._on_ack(device_id, msg)
            return True

        return False

    def _on_ack(self, device_id: str, msg: BusMessage) -> None:
        """Check a leaf's ACK against WHAT WAS ACTUALLY SENT.

        THREE independent things must hold before this counts as a success, and
        before 2026-08-13 none of them was checked — the whole test was
        `text.startswith("ok")`:

          1. the value DECODES as a config ACK ([CXC-CONFIG-BUS-CHUNKS]);
          2. its status is OK;
          3. its hash AND length are the ones this server served.

        (3) is the one that matters most, because it is the only check that can
        catch a leaf acknowledging a DIFFERENT transfer — a stale ACK from a
        push that crossed a retry, or a second head on the segment. "ok" plus
        the wrong hash used to be indistinguishable from success.

        AN ACK RESOLVES A TRANSFER ONLY IF IT NAMES IT. An ACK that arrives on
        the right namespace but names a different hash does NOT clear the
        pending transfer, and that matters concretely: when a push supersedes an
        earlier one, the leaf ACKs the OLD hash as `incomplete`, and if arrival
        alone were enough that message would silently absolve the NEW transfer
        of ever being confirmed.
        """
        # A deadline that passed BEFORE this value arrived has still passed —
        # check first, so a post-deadline ACK is reported as late rather than
        # racing the watch thread for the verdict.
        self.check_timeouts()
        row = self.census(device_id)

        try:
            ack = decode_ack(msg.value)
        except ConfigAckError as exc:
            # NOT resolved: an unreadable value cannot be ATTRIBUTED to a
            # transfer, so the pending one stays pending and will still time
            # out. Two counters move for one event and both are true — the leaf
            # said something unreadable, AND the transfer it was probably about
            # was never confirmed.
            row.unreadable += 1
            log.error("UNREADABLE ACK from %s: %s — this transfer is NOT "
                      "confirmed, and its deadline still stands.",
                      device_id, exc)
            return

        with self._lock:
            pending = self._pending.get(device_id)
            mine = (pending is not None and ack.hash == pending.hash
                    and ack.total_len == pending.total_len)
            if mine:
                del self._pending[device_id]
                self._resolve(pending, "confirmed" if ack.ok else "refused")
                if ack.ok:
                    row.confirmed += 1
                else:
                    row.refused[ack.status] = row.refused.get(ack.status, 0) + 1
            already = self._was_resolved(device_id, ack.hash, ack.total_len)

        if mine and ack.ok:
            log.info("ACK from %s: %s — hash and length match what was served "
                     "(%.2f s after the last frame)", device_id, ack,
                     pending.waited(self._clock()))
            return

        if mine:
            # The leaf DISCARDED and will re-request. Do not pre-empt it: an
            # immediate re-push from both ends is how you get two interleaved
            # transfers, which is the one thing the content hash exists to
            # catch. `ackseq` names where a repair would resume — nothing reads
            # it yet, and it is on the wire so that something CAN.
            log.error("ACK from %s reports a FAILED transfer: %s — the leaf "
                      "discarded it and will re-request. ackseq %d is %s.",
                      device_id, ack, ack.ackseq,
                      "the first MISSING chunk" if ack.ackseq
                      else "0: nothing was missing, so this is CORRUPTION, "
                           "not loss")
            return

        if already:
            # A duplicate, or one that arrived after its deadline. NOT promoted
            # to a success: this head has already reported a verdict on that
            # transfer, and an instrument that says both "confirmed" and
            # "silent" about one transfer is lying about one of them.
            row.stale += 1
            log.warning("STALE ACK from %s: %s names a transfer whose verdict "
                        "is already recorded (a duplicate, or one that arrived "
                        "after its deadline). Counted as stale, NOT as a "
                        "success. This device: %s", device_id, ack, row)
            return

        row.unmatched += 1
        served = "nothing" if pending is None else str(pending)
        log.error("ACK from %s names hash %04x / %d B, which this server has "
                  "never served that device (in flight: %s). NOT counted as a "
                  "success whatever its status: something else is answering on "
                  "%s, or this process restarted mid-transfer.",
                  device_id, ack.hash, ack.total_len, served,
                  config_namespace(device_id))

    # -- live push ([CXC-CONFIG-BUS-PUSH]) -------------------------------

    def poll_once(self) -> int:
        """Re-read every watched device's scene; push the ones that changed."""
        pushed = 0
        for device_id in list(self.devices):
            payload = self._fetch(device_id)
            if payload is None:
                continue
            chash = content_hash(payload)
            if self._last_hash.get(device_id) == chash:
                continue
            if device_id in self._last_hash:
                log.info("scene for %s CHANGED (%04x -> %04x) — unsolicited "
                         "push", device_id, self._last_hash[device_id], chash)
                if self.serve_payload(device_id, payload, reason="changed"):
                    pushed += 1
            else:
                # First sighting: record the hash but do NOT push. A leaf that
                # is already running asked for nothing; boot-time pull is the
                # degenerate case of push, not a reason to interrupt.
                self._last_hash[device_id] = chash
        return pushed

    def run_watch(self, stop: threading.Event) -> None:
        """The one loop, running TWO cadences.

        Polling for a changed scene is a 15 s job; noticing that a transfer went
        unanswered is a ~2 s one. Hanging the second off the first would put a
        15 s granularity on a 2 s deadline and make the timeout report arrive
        long after the operator had already gone looking.
        """
        next_poll = 0.0
        while not stop.is_set():
            now = self._clock()
            if now >= next_poll:
                next_poll = now + self.poll_sec
                try:
                    self.poll_once()
                except Exception:                   # noqa: BLE001
                    log.exception("config watch poll failed")
            try:
                self.check_timeouts()
            except Exception:                       # noqa: BLE001
                log.exception("config ACK deadline check failed")
            stop.wait(self.ack_check_sec)


def _selftest() -> int:
    """Hardware-free: an in-memory 'wire' from server straight to the Python
    reassembler, over BOTH substrate chunk sizes."""
    from composeio.config_chunk import Reassembler

    logging.basicConfig(level=logging.WARNING)
    fails = []
    payload = bytes((i * 31 + 5) & 0xFF for i in range(6012))

    for cp in (UART_CONTENT_BYTES, CAN_CONTENT_BYTES):
        r = Reassembler()
        results = []

        def send(msg: BusMessage, _r=r, _res=results):
            assert msg.type == MessageType.CONFIG_PUSH
            _res.append(_r.on_frame(msg.value))

        srv = ConfigServer(send, lambda d: payload, ["hosyond-080739"],
                           chunk_payload=cp, pace_ms=0)
        srv.on_message(BusMessage(namespace="_device/hosyond-080739/config",
                                  type=MessageType.CONFIG_REQUEST, value=None))
        if results[-1] != Reassembler.COMPLETE:
            fails.append(f"cp={cp}: transfer ended {results[-1]}")
        if r.payload != payload:
            fails.append(f"cp={cp}: reassembled payload differs")
        if srv.transfers != 1:
            fails.append(f"cp={cp}: {srv.transfers} transfers, expected 1")

    # An unknown device still gets served — the request IS the address.
    seen = []
    srv = ConfigServer(lambda m: seen.append(m), lambda d: b"x", [], pace_ms=0)
    srv.on_message(BusMessage(namespace="_device/newcomer/config",
                              type=MessageType.CONFIG_REQUEST, value=None))
    if not seen:
        fails.append("an unregistered device's request was not served")

    # A message on some other namespace is NOT consumed.
    if srv.on_message(BusMessage(namespace="throttle/left",
                                 type=MessageType.STATE, value=1)):
        fails.append("a state message was consumed by the config server")

    # THE ACK IS CHECKED. A right-hash ACK counts; a wrong-hash ACK does NOT,
    # which is the whole of [D2] in four lines.
    from composeio.config_chunk import ack_ok as _ack_ok

    scene = b"title: engine thrust\n"
    srv = ConfigServer(lambda m: None, lambda d: scene, ["leaf"], pace_ms=0)
    srv.serve("leaf")
    ns = config_namespace("leaf")
    good = content_hash(scene)
    srv.on_message(BusMessage(namespace=ns, type=MessageType.ACK,
                              value=_ack_ok(good, len(scene), 1)))
    if (srv.acks_ok, srv.acks_bad, srv.acks_unmatched) != (1, 0, 0):
        fails.append(f"a correct ACK counted {srv.acks_ok}/{srv.acks_bad}/"
                     f"{srv.acks_unmatched}, expected 1/0/0")
    srv.on_message(BusMessage(namespace=ns, type=MessageType.ACK,
                              value=_ack_ok(good ^ 0xFFFF, len(scene), 1)))
    if srv.acks_ok != 1 or srv.acks_unmatched != 1:
        fails.append("an ACK naming the WRONG hash was accepted as success")
    srv.on_message(BusMessage(namespace=ns, type=MessageType.ACK,
                              value="ok beef 5856"))
    if srv.acks_ok != 1 or srv.acks_bad != 1:
        fails.append("the retired TEXT ack was not counted as a bad ack")

    # SILENCE IS AN OUTCOME. A transfer nobody answers must become a number.
    clk = [1000.0]
    srv = ConfigServer(lambda m: None, lambda d: scene, ["leaf"], pace_ms=0,
                       clock=lambda: clk[0])
    srv.serve("leaf")
    if srv.check_timeouts() != 0 or srv.acks_timeout != 0:
        fails.append("the ACK deadline fired before it was due")
    clk[0] += ACK_TIMEOUT_BASE_S + 0.01
    if srv.check_timeouts() != 1 or srv.acks_timeout != 1:
        fails.append("an UNANSWERED transfer did not time out")
    if srv.pending_transfers:
        fails.append("a timed-out transfer is still pending")
    if srv.check_timeouts() != 0 or srv.acks_timeout != 1:
        fails.append("the same timeout fired twice")

    # ...and a HEALTHY 977-frame CAN transfer must NOT trip it. The real
    # pace_ms=2 with a stubbed sleep, so the deadline is the shipping one.
    clk = [0.0]
    big = bytes(5856)
    srv = ConfigServer(lambda m: None, lambda d: big, ["leaf"],
                       chunk_payload=CAN_CONTENT_BYTES, pace_ms=2.0,
                       clock=lambda: clk[0], sleep=lambda s: None)
    srv.serve("leaf")
    if srv.pending("leaf").frames != 977:
        fails.append(f"CAN transfer was {srv.pending('leaf').frames} frames, "
                     f"expected 977")
    clk[0] += ack_timeout_seconds(977, 0.002) - 0.01
    srv.check_timeouts()
    srv.on_message(BusMessage(namespace=ns, type=MessageType.ACK,
                              value=_ack_ok(content_hash(big), len(big), 977)))
    if (srv.acks_ok, srv.acks_timeout) != (1, 0):
        fails.append(f"a healthy CAN-sized transfer reported "
                     f"{srv.acks_ok} ok / {srv.acks_timeout} timeout")

    if fails:
        for m in fails:
            print(f"FAIL {m}")
        return 1
    print("PASS: config_server selftest (UART and CAN chunk sizes, "
          "server -> reassembler)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
