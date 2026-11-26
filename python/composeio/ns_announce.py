#!/usr/bin/env python3
"""ns_announce — THE WELL-KNOWN ROOT, so a CAN-originated name can be learned.

[SPEC:CXC-CAN] [CXC-CAN-BRIDGE-MIGRATE] [SPEC:CXC-CONFIG-BUS] — workstream G
(G1/G3/G4) of ``docs/PLAN-fleet-baseline-and-bus-native.md``.

THE HOLE THIS FILLS
===================
``services/can_mqtt_bridge`` learns ``name -> hash`` (and therefore
``hash -> name``) from every namespace it observes on MQTT.  That closes one
direction completely and leaves the other one wide open: **a namespace that has
never appeared on MQTT cannot be learned at all.**  A CAN node publishing
``_device/hosyond-080739/heartbeat`` puts 0xCC01 on the wire and nothing
anywhere can turn 0xCC01 back into that string, because a CRC is not
reversible.  The bridge drops the frame — correctly, loudly, and permanently.

That is why device self-announcement (workstream 0.2) shipped with CAN
explicitly scoped OUT, and why ``composeio fleet`` states in its own output
that it cannot name a CAN device.  Both are blocked on this file.

WHY A PREFIX RULE CANNOT WORK, AND WHY THAT IS THE WHOLE PROBLEM
================================================================
The obvious fix — "just register ``_device/*``" — is not merely unimplemented,
it is **impossible at this layer**.  The 16-bit wire id is a CRC over the FULL
namespace name *including the device id*, so ``_device/*`` cannot be expanded
into hashes without already knowing every device id — which is precisely the
thing being discovered.  It is circular.  Nine ``CAN_BRIDGE_REGISTER`` entries
were hand-typed during one C3 bring-up; a tenth board reports an irreversible
number and nothing else.

WHAT ACTUALLY WORKS: ONE HARDCODED ENTRY, EVERYTHING ELSE LEARNED
=================================================================
Exactly **one** namespace has a hash that every bridge and every node knows
without being told — ``_device/announce``, 0x31AE.  A node sends its namespace
**NAMES** over it, chunked with ``config_chunk`` (substrate-independent, sized
for classic CAN's 8 bytes, already host-proven).  The receiver learns
``name -> hash`` from those names exactly as it learns from MQTT traffic.

Same shape as a DNS root or DHCP's 255.255.255.255: one address is a constant so
that no other address has to be.  The bootstrap is one entry deep and it
terminates.

THIS ADDS NO BITS, AND SAYING SO IS PART OF THE DESIGN
======================================================
A 16-bit id over a birthday curve is 2.32% collision probability at the 56
namespaces measured live on the production broker, 7.28% at 100 and **39.27% at
256**.  A collision is not a drop — it is a receiver *confidently decoding the
wrong namespace*.  What announcement buys is that collisions become **visible
and refused** instead of silent and wrong; it buys no headroom whatsoever.  The
endpoint is to stop hashing and **allocate** ids from a registrar, which a
dynamic, published table finally makes possible.  Deliberately not built here —
and deliberately not foreclosed: nothing below assumes a name's id is a
function of the name, only that the sender and receiver agree on it.

WHAT IS *NOT* IN HERE
=====================
The transport.  These functions consume and produce frame VALUES and a list of
names; who carries them (a CAN frame, a UART frame, an in-process queue) is the
caller's business — the same separation ``config_chunk`` keeps, and the reason
one implementation serves both ``services/can_mqtt_bridge`` and
``composeio.transport_can``.

Also not in here: **permission**.  ``AnnounceLearner`` calls a ``learn``
callback and does exactly what that callback tells it.  The bridge's separation
of ``observe_ns`` (learns) from ``_forwarding_decision`` (governs) is load
bearing — if learning granted forwarding rights, anything that could announce
would have write access to a shared 500 kbit segment — and this module must not
quietly become the back door around it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from composeio.config import namespace_hash
from composeio.config_chunk import (
    CAN_CONTENT_BYTES, ConfigChunkError, Reassembler, chunk_config,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# G1 — THE WELL-KNOWN ROOT                                                     #
# --------------------------------------------------------------------------- #
#: The one namespace whose hash is a CONSTANT in every bridge and every node.
NS_ANNOUNCE = "_device/announce"

#: The one poll that makes a node re-send its names.  It already existed as the
#: device-announcement fleet poll (workstream 0.3) and is REUSED rather than
#: duplicated: "tell me what you are" and "tell me what you are called" are the
#: same question asked of the same device, and a second poll namespace would be
#: a second thing to keep subscribed on a 2 KB-class part.
NS_ANNOUNCE_POLL = "_device/broadcast/announce"

#: PINNED, not computed-and-trusted.  These literals are what make the root
#: "well-known": they are the values every deployed bridge and every flashed
#: node must already agree on, and a rename of NS_ANNOUNCE would otherwise
#: silently strand every node in the field on an id nothing listens to.  The
#: assertion below turns that into a build-time stop instead of a fleet-wide
#: silence.  The firmware pins the same two numbers in cyd/src/ns_announce.h and
#: [env:native_announce] compares them.
NS_ANNOUNCE_HASH = 0x31AE
NS_ANNOUNCE_POLL_HASH = 0xC77C

if namespace_hash(NS_ANNOUNCE) != NS_ANNOUNCE_HASH:      # pragma: no cover
    raise RuntimeError(
        f"the well-known announcement root {NS_ANNOUNCE!r} hashes to "
        f"0x{namespace_hash(NS_ANNOUNCE):04X}, not the PINNED "
        f"0x{NS_ANNOUNCE_HASH:04X}. Either the name or the hash function "
        f"changed. Every node already flashed transmits on the OLD id, so this "
        f"is a fleet-wide break, not a rename: nothing would resolve and "
        f"nothing would say why. Fix the name, or coordinate a reflash and "
        f"update BOTH this constant and cyd/src/ns_announce.h.")
if namespace_hash(NS_ANNOUNCE_POLL) != NS_ANNOUNCE_POLL_HASH:   # pragma: no cover
    raise RuntimeError(
        f"the well-known announcement poll {NS_ANNOUNCE_POLL!r} hashes to "
        f"0x{namespace_hash(NS_ANNOUNCE_POLL):04X}, not the PINNED "
        f"0x{NS_ANNOUNCE_POLL_HASH:04X} — see above.")

#: Registered by EVERY bridge and EVERY node, before any configuration is read.
WELL_KNOWN: Dict[str, int] = {
    NS_ANNOUNCE: NS_ANNOUNCE_HASH,
    NS_ANNOUNCE_POLL: NS_ANNOUNCE_POLL_HASH,
}


# --------------------------------------------------------------------------- #
# THE PAYLOAD — a list of NAMES, and nothing else                              #
# --------------------------------------------------------------------------- #
# Newline-separated ASCII.  No version byte, no count field, no per-name length
# prefix, because the transfer that carries it already states its own total
# length AND a CRC-16 of its whole content (config_chunk's header frame).  A
# second framing layer would be a second thing that can disagree with the first.
#
# The payload carries FULL names, never a device id plus suffixes to be
# recombined.  Recombination is the prefix rule wearing a disguise: the receiver
# would have to know the naming convention, and a node with one namespace off
# the pattern (`throttle/left`, say — no `_device/` at all) could not announce
# it.  Full names cost bytes and cost nothing else.

#: Separator.  A byte that cannot occur inside a legal namespace.
NAME_SEP = b"\n"

#: A single name's ceiling.  NOT arbitrary: `BusEvent::ns` in the firmware is
#: `char[96]`, so a name a receiver could not hold is one no node can usefully
#: announce.  Refused at BOTH ends rather than truncated — a truncated namespace
#: is a DIFFERENT namespace that resolves cleanly, the same failure class as the
#: truncated CAN value the bridge was rebuilt to stop doing.
MAX_NAME_BYTES = 95

#: How many names one announcement may carry.  A bound exists so a corrupt-but-
#: hash-valid payload cannot make a receiver allocate without limit; 64 is ~10x
#: the seven namespaces a baseline board actually has.
MAX_NAMES = 64

#: What a namespace may contain.  Deliberately strict: printable ASCII, no
#: whitespace, no control bytes.  A namespace is an ADDRESS on a shared segment,
#: and a receiver that accepts `"\x07throttle"` will publish it to MQTT where it
#: is indistinguishable from a real name nobody wrote.
_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./:@+-]*$")


class NsAnnounceError(ValueError):
    """A name or payload this format refuses. Always loud, never coerced."""


def validate_name(name: str) -> Optional[str]:
    """None if `name` is announceable, else the REASON it is not.

    Returns the reason rather than raising because the caller needs to keep
    going: one bad name in an announcement must not discard the good ones, and
    it must not disappear either — every refusal is counted and published.
    """
    if not name:
        return "empty"
    raw = name.encode("utf-8", errors="replace")
    if len(raw) > MAX_NAME_BYTES:
        return (f"{len(raw)} bytes, over the {MAX_NAME_BYTES}-byte ceiling "
                f"(BusEvent::ns is char[96]) — REFUSED, not truncated")
    try:
        name.encode("ascii")
    except UnicodeEncodeError:
        return "not ASCII"
    if not _NAME_RE.match(name):
        return "contains a character a namespace may not contain"
    return None


def encode_names(names: Iterable[str]) -> bytes:
    """The announcement payload for `names`. Refuses anything unannounceable.

    Refusal is at the SENDER too, not only at the receiver, because a node that
    cannot announce one of its namespaces has a namespace nothing will ever be
    able to name — and it is the only party that still knows what it meant.
    """
    out: List[str] = []
    for n in names:
        why = validate_name(n)
        if why is not None:
            raise NsAnnounceError(f"cannot announce namespace {n!r}: {why}")
        if n in out:
            continue                      # idempotent, not an error
        out.append(n)
    if len(out) > MAX_NAMES:
        raise NsAnnounceError(
            f"{len(out)} namespaces; one announcement carries at most "
            f"{MAX_NAMES}. Split it or reduce the device's namespace set — a "
            f"silently-truncated announcement is a device that is confidently, "
            f"partially nameable, which is worse than one that is honestly not.")
    return NAME_SEP.join(n.encode("ascii") for n in out)


def decode_names(payload: bytes) -> Tuple[List[str], List[Tuple[str, str]]]:
    """(accepted, refused) for an announcement payload.

    PER-NAME refusal, not whole-payload refusal, and the distinction is
    reasoned: `config_chunk` has already verified a CRC-16 over these exact
    bytes, so a malformed name here is a SENDER defect, not corruption.
    Discarding the whole announcement would punish the six good namespaces for
    the seventh; discarding just the bad one and saying so loudly names the
    actual bug. `refused` is returned, counted and published — never swallowed.
    """
    accepted: List[str] = []
    refused: List[Tuple[str, str]] = []
    if not payload:
        return accepted, [("", "the announcement payload was empty")]
    for raw in payload.split(NAME_SEP):
        try:
            name = raw.decode("ascii")
        except UnicodeDecodeError:
            refused.append((raw.decode("ascii", errors="replace"), "not ASCII"))
            continue
        why = validate_name(name)
        if why is not None:
            refused.append((name, why))
            continue
        if name in accepted:
            continue
        if len(accepted) >= MAX_NAMES:
            refused.append((name, f"over the {MAX_NAMES}-name ceiling"))
            continue
        accepted.append(name)
    return accepted, refused


def announcement_frames(names: Iterable[str],
                        chunk_payload: int = CAN_CONTENT_BYTES) -> List[bytes]:
    """The frame VALUES a node puts on `_device/announce`, in order.

    `chunk_payload` comes from the SUBSTRATE (`BusTransport::max_raw_value()`
    on the firmware, `CAN_CONTENT_BYTES` here), never from this format — that is
    what lets one announcement ride CAN's 6 usable bytes and UART's 250.
    """
    return chunk_config(encode_names(names), chunk_payload)


# --------------------------------------------------------------------------- #
# G3 — THE LEARNER                                                             #
# --------------------------------------------------------------------------- #

#: on_frame outcomes, the vocabulary the census is counted in.
ANN_PROGRESS = "progress"        # a chunk landed, transfer incomplete
ANN_STARTED = "started"          # header accepted
ANN_INTERLEAVED = "interleaved"  # a header arrived MID-transfer: two announcers
ANN_DUPLICATE = "duplicate"      # a sequence we already had
ANN_STRAY = "stray"              # a data frame with no header
ANN_MISMATCH = "mismatch"        # every frame in, whole-payload CRC wrong
ANN_COMPLETE = "complete"        # reassembled, verified, names handed to learn()
ANN_MALFORMED = "malformed"      # a frame this format cannot parse at all
ANN_OUTCOMES = (ANN_PROGRESS, ANN_STARTED, ANN_INTERLEAVED, ANN_DUPLICATE,
                ANN_STRAY, ANN_MISMATCH, ANN_COMPLETE, ANN_MALFORMED)


@dataclass
class AnnounceResult:
    """What one frame did. `outcome` is always one of ANN_*."""
    outcome: str
    src: int
    names: List[str] = field(default_factory=list)
    refused: List[Tuple[str, str]] = field(default_factory=list)
    #: name -> whatever the caller's `learn` returned. The learner does not
    #: interpret these; the bridge's vocabulary is new/known/collision.
    learned: Dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.outcome == ANN_COMPLETE


class AnnounceLearner:
    """Reassembles announcements off a substrate and hands the NAMES to `learn`.

    ONE REASSEMBLER PER SOURCE NODE, and that is the interesting decision.
    ``_device/announce`` is a BROADCAST root: every node on the segment
    transmits on the same 16-bit id, so two nodes announcing at once interleave.
    ``config_chunk.Reassembler`` abandons its transfer when a header arrives
    mid-transfer (it is built for a leaf with exactly one scene), which would
    make simultaneous announcements mutually destructive and — worse — could in
    principle interleave two payloads into one that still parsed.

    The [BP-CAN-ID] ``src`` field is exactly the demultiplexer for that: four
    bits, ALLOCATED, "which box put this on the wire".  Keying reassembly on it
    makes concurrent announcements from *allocated* nodes independent.

    ⚠ AND HERE IS THE HONEST LIMIT.  ``src == 0`` means UNASSIGNED, and nothing
    allocates node ids yet — so today every announcing node shares reassembler
    0.  Two unassigned nodes announcing simultaneously WILL collide.  They do
    not collide silently: the second header aborts the first
    (``ANN_INTERLEAVED``, counted and warned) and any surviving mixture fails
    the CRC-16 (``ANN_MISMATCH``, counted and warned).  The failure is loud, the
    announcement is retried on the next poll, and the fix is node-id allocation
    — not a bigger buffer here.
    """

    def __init__(self, learn: Callable[[str], str],
                 *, label: str = "can") -> None:
        self._learn = learn
        self._label = label
        self._re: Dict[int, Reassembler] = {}
        #: outcome -> count. The census. Every one of these can increment, and
        #: `test_ns_announce.py` drives each of them at least once — a counter
        #: nothing has ever been observed to move is a counter that may not be
        #: wired up at all (this repo has shipped four of those).
        self.counts: Dict[str, int] = {o: 0 for o in ANN_OUTCOMES}
        self.frames = 0
        self.names_seen = 0
        self.names_refused = 0
        #: name -> outcome from `learn`, most recent wins. The provenance the
        #: audit channel publishes.
        self.last_names: Dict[int, List[str]] = {}
        self._warned: set = set()

    # -- the census ------------------------------------------------------- #

    def _count(self, outcome: str) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1

    def _warn_once(self, key, msg: str, *args) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(msg, *args)

    # -- the frame path ---------------------------------------------------- #

    def on_frame(self, src: int, value: bytes) -> AnnounceResult:
        """Feed ONE frame value from the well-known root. Never raises."""
        self.frames += 1
        r = self._re.get(src)
        if r is None:
            r = self._re[src] = Reassembler()

        was_active = r.active
        try:
            state = r.on_frame(bytes(value))
        except ConfigChunkError as e:
            self._count(ANN_MALFORMED)
            self._warn_once(
                ("malformed", src),
                "%s: a frame on the well-known announcement root %s (src=%d) "
                "is not a config chunk (%s) — DROPPED. Something else is "
                "transmitting on id 0x%04X. Further occurrences from this "
                "source are counted, not logged.",
                self._label, NS_ANNOUNCE, src, e, NS_ANNOUNCE_HASH)
            return AnnounceResult(ANN_MALFORMED, src)

        if state == Reassembler.IGNORED:
            self._count(ANN_MALFORMED)
            self._warn_once(
                ("runt", src),
                "%s: a %d-byte value on %s (src=%d) is too short to be a "
                "config chunk — DROPPED.", self._label, len(value),
                NS_ANNOUNCE, src)
            return AnnounceResult(ANN_MALFORMED, src)

        if state in (Reassembler.STARTED, Reassembler.RESTARTED):
            # RESTARTED on a BROADCAST root is not "the sender changed its
            # mind", it is TWO ANNOUNCERS SHARING A SOURCE ID — which today
            # means two unassigned nodes. Named as `interleaved` because that is
            # the diagnosis, and warned, because the fix is node-id allocation
            # and nobody will do that on the strength of a silent retry.
            if state == Reassembler.RESTARTED or was_active:
                self._count(ANN_INTERLEAVED)
                self._warn_once(
                    ("interleaved", src),
                    "%s: a second announcement started on %s from src=%d while "
                    "one was in flight — the first is ABANDONED. src=%d means "
                    "%s, so two different boards are indistinguishable here. "
                    "The announcements will be retried on the next poll; the "
                    "real fix is allocating [BP-CAN-ID] node ids. Counted, not "
                    "re-logged.", self._label, NS_ANNOUNCE, src, src,
                    "UNASSIGNED" if src == 0 else "an allocated node")
                return AnnounceResult(ANN_INTERLEAVED, src)
            self._count(ANN_STARTED)
            return AnnounceResult(ANN_STARTED, src)

        if state == Reassembler.STRAY:
            self._count(ANN_STRAY)
            self._warn_once(
                ("stray", src),
                "%s: a data chunk arrived on %s from src=%d with no header — "
                "the header frame was lost, or this receiver started mid-"
                "announcement. DROPPED; the announcement is incomplete and is "
                "NOT partially learned.", self._label, NS_ANNOUNCE, src)
            return AnnounceResult(ANN_STRAY, src)

        if state == Reassembler.DUPLICATE:
            self._count(ANN_DUPLICATE)
            return AnnounceResult(ANN_DUPLICATE, src)

        if state == Reassembler.HASH_MISMATCH:
            self._count(ANN_MISMATCH)
            logger.error(
                "%s: an announcement from src=%d on %s reassembled to CRC "
                "0x%04X but announced 0x%04X — DISCARDED, no name learned. "
                "Every chunk arrived (ackseq=%d), so this is CORRUPTION or two "
                "interleaved announcers, not loss. Learning half a namespace "
                "table would make the fleet census confidently wrong.",
                self._label, src, NS_ANNOUNCE, r.actual_hash or 0,
                r.expect_hash, r.ack_seq())
            return AnnounceResult(ANN_MISMATCH, src)

        if state == Reassembler.PROGRESS:
            self._count(ANN_PROGRESS)
            return AnnounceResult(ANN_PROGRESS, src)

        if state != Reassembler.COMPLETE:                # pragma: no cover
            self._count(ANN_MALFORMED)
            return AnnounceResult(ANN_MALFORMED, src)

        # -- COMPLETE: verified bytes, now the names --------------------- #
        names, refused = decode_names(r.payload or b"")
        self._count(ANN_COMPLETE)
        self.names_seen += len(names)
        self.names_refused += len(refused)
        for bad, why in refused:
            logger.error(
                "%s: announcement from src=%d offered an unusable namespace "
                "%r (%s) — REFUSED. The rest of the announcement is still "
                "learned; this one name will never resolve, and the device "
                "that sent it is the only party that still knows what it "
                "meant.", self._label, src, bad, why)
        learned: Dict[str, str] = {}
        for n in names:
            learned[n] = self._learn(n)
        self.last_names[src] = list(names)
        logger.info(
            "%s: learned %d namespace name(s) from a CAN-side announcement "
            "(src=%d, %d B, CRC 0x%04X): %s", self._label, len(names), src,
            r.total_len, r.expect_hash,
            ", ".join(f"{n}={learned[n]}" for n in names) or "(none)")
        return AnnounceResult(ANN_COMPLETE, src, names=names, refused=refused,
                              learned=learned)

    # -- reporting ---------------------------------------------------------- #

    def census(self) -> dict:
        """The audit view. Every field is a number this object has moved."""
        return {
            "frames": self.frames,
            "by_outcome": {k: v for k, v in self.counts.items() if v},
            "names_seen": self.names_seen,
            "names_refused": self.names_refused,
            "sources": {str(s): list(n) for s, n in self.last_names.items()},
            "in_flight": {str(s): r.received for s, r in self._re.items()
                          if r.active},
        }


# --------------------------------------------------------------------------- #
# G4 — PERSISTENCE, AND WHY IT IS VERIFIED ON LOAD                             #
# --------------------------------------------------------------------------- #
# A learned table that dies with the process means a bridge restart un-names
# every CAN-only device until each one is polled again. Persisting it is
# obvious. What is NOT obvious, and is the whole reason this is not three lines
# of json.dump, is that PERSISTED LEARNING CAN PERSIST A MISTAKE:
#
#   * a name learned once from a corrupt-but-CRC-valid announcement outlives
#     the board that sent it,
#   * a collision decided in run N is silently re-decided the same way in run
#     N+1 even after somebody renamed the loser,
#   * and a hash written by an older build of the hash function would be
#     believed forever.
#
# So: the file stores name AND hash, the loader RECOMPUTES the hash and refuses
# any entry that disagrees, provenance travels with every entry, and the whole
# table keeps going out on the 5 s audit publication. A persisted table you
# cannot see is a configuration file nobody wrote.

#: Format version. Present so a future change can refuse an old file out loud
#: instead of misreading it — not so that old files can be silently upgraded.
REGISTRY_VERSION = 1


@dataclass
class RegistryLoad:
    """The result of reading a persisted table. `problems` is never hidden."""
    entries: Dict[str, str] = field(default_factory=dict)   # name -> provenance
    problems: List[str] = field(default_factory=list)
    path: Optional[str] = None
    saved_at: Optional[float] = None

    @property
    def ok(self) -> bool:
        return not self.problems


def save_registry(path, entries: Dict[str, str], *,
                  meta: Optional[dict] = None) -> int:
    """Write `name -> provenance` to `path`. Returns the number of entries.

    The hash is written too even though it is derivable, precisely so the
    loader can CHECK it: a file whose hashes no longer match the hash function
    is a file from a different protocol, and the loader must be able to say so
    rather than quietly recompute and carry on.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "version": REGISTRY_VERSION,
        "saved_at": time.time(),
        "hash": "crc16-ccitt-false",
        "well_known": {n: f"0x{h:04X}" for n, h in WELL_KNOWN.items()},
        "meta": dict(meta or {}),
        "entries": [
            {"name": n, "hash": f"0x{namespace_hash(n):04X}", "via": prov}
            for n, prov in sorted(entries.items())
        ],
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=1, sort_keys=False))
    tmp.replace(p)                       # atomic: never a half-written table
    return len(body["entries"])


def load_registry(path) -> RegistryLoad:
    """Read a persisted table, REFUSING every entry that does not verify.

    Refusal is per-entry and every refusal lands in `problems`, which the caller
    publishes. A loader that silently dropped a bad line would make a persisted
    mistake indistinguishable from a persisted absence.
    """
    out = RegistryLoad(path=str(path))
    p = Path(path)
    if not p.exists():
        return out                        # not an error: first run
    try:
        body = json.loads(p.read_text())
    except Exception as e:
        out.problems.append(
            f"{p}: unreadable ({e}) — starting with an EMPTY learned table. "
            f"Every CAN-only namespace must be re-announced before it can be "
            f"named again.")
        return out
    ver = body.get("version")
    if ver != REGISTRY_VERSION:
        out.problems.append(
            f"{p}: version {ver!r}, this build speaks {REGISTRY_VERSION} — the "
            f"file is REFUSED WHOLE rather than half-read. Delete it and let "
            f"the table re-learn.")
        return out
    out.saved_at = body.get("saved_at")
    seen_hashes: Dict[int, str] = {}
    for row in body.get("entries", []):
        name = row.get("name")
        if not isinstance(name, str):
            out.problems.append(f"{p}: an entry with no name: {row!r}")
            continue
        why = validate_name(name)
        if why is not None:
            out.problems.append(
                f"{p}: refusing persisted namespace {name!r} ({why})")
            continue
        want = namespace_hash(name)
        raw = row.get("hash")
        try:
            stored = int(str(raw), 16) if isinstance(raw, str) else int(raw)
        except Exception:
            out.problems.append(
                f"{p}: refusing {name!r} — unreadable hash {raw!r}")
            continue
        if stored != want:
            out.problems.append(
                f"{p}: refusing {name!r} — the file says 0x{stored:04X}, this "
                f"build computes 0x{want:04X}. The hash function or the name "
                f"changed under a saved table; believing the file would name "
                f"CAN frames WRONG, which is worse than not naming them.")
            continue
        if want in seen_hashes and seen_hashes[want] != name:
            out.problems.append(
                f"{p}: refusing {name!r} — 0x{want:04X} is already held by "
                f"{seen_hashes[want]!r} in the same file. A persisted "
                f"collision is still a collision; the first entry keeps it.")
            continue
        seen_hashes[want] = name
        out.entries[name] = str(row.get("via") or "persisted")
    return out


def registry_path_from_env(var: str = "CAN_BRIDGE_REGISTRY_FILE") -> Optional[str]:
    """The persistence path, or None when a deployment has not chosen one.

    NO DEFAULT PATH. A default would put a state file somewhere an operator did
    not ask for and did not know to look at — and the first thing they would do
    with a misbehaving bridge is restart it, which is exactly when a hidden
    persisted table changes the answer. Unset means OFF, and the bridge SAYS it
    is off at startup.
    """
    v = os.environ.get(var, "").strip()
    return v or None


# --------------------------------------------------------------------------- #
def _selftest() -> int:                                   # pragma: no cover
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    check(namespace_hash(NS_ANNOUNCE) == 0x31AE, "(1) well-known hash moved")

    names = ["_device/hosyond-080739/heartbeat", "_device/hosyond-080739/build",
             "_device/hosyond-080739/health"]
    frames = announcement_frames(names)
    check(all(len(f) <= 8 for f in frames), "(2) a frame exceeded 8 bytes")

    got: Dict[str, str] = {}
    lr = AnnounceLearner(lambda n: got.setdefault(n, "new"))
    res = [lr.on_frame(3, f) for f in frames]
    check(res[-1].complete, f"(3) last frame -> {res[-1].outcome}")
    check(sorted(got) == sorted(names), f"(3b) learned {sorted(got)}")

    # a corrupted chunk must NOT learn anything
    bad = list(frames)
    c = bytearray(bad[2]); c[3] ^= 0xFF; bad[2] = bytes(c)
    got2: Dict[str, str] = {}
    lr2 = AnnounceLearner(lambda n: got2.setdefault(n, "new"))
    out = [lr2.on_frame(3, f) for f in bad]
    check(out[-1].outcome == ANN_MISMATCH, f"(4) corrupt -> {out[-1].outcome}")
    check(got2 == {}, "(4b) a corrupt announcement taught something")

    if fails:
        for m in fails:
            print(f"FAIL {m}")
        return 1
    print(f"PASS: ns_announce selftest ({NS_ANNOUNCE} = "
          f"0x{NS_ANNOUNCE_HASH:04X}, {len(frames)} frames for {len(names)} "
          f"names on classic CAN)")
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(_selftest())
