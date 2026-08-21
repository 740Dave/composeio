"""CAN-to-MQTT Bridge — bidirectional ComposeIO transport bridge.

Bridges CAN bus messages to MQTT and vice versa. Runs as a service hub
workload on mqtt-broker.local and on pi.example.local (Waveshare 2-CH CAN HAT).

Requires:
  - Python stdlib socket (SocketCAN AF_CAN/CAN_RAW) — NO python-can
    ([SPEC:CXC-CAN] / [CXC-CAN-SOCKETCAN] / [CXC-CAN-BRIDGE-MIGRATE])
  - paho-mqtt (MQTT client)
  - ComposeIO bus library (transport_uart for binary value encoding)

CAN → MQTT: Receives CAN frames, decodes namespace hash + type + value,
publishes to MQTT topic composeio/{scope}/{namespace}

MQTT → CAN: Subscribes MQTT, encodes to CAN frame format, transmits on CAN.

DP-CIO-6: Transport is an implementation detail. This bridge makes CAN and
MQTT participants indistinguishable from the bus perspective.


WHY THIS FILE WAS REBUILT (2026-08-12, the day after it first met real silicon)
==============================================================================

FILTERING USED TO HAPPEN AT SUBSCRIBE TIME, WHICH IS WHERE IT CANNOT BE SEEN.
    The old bridge narrowed `CAN_BRIDGE_ALLOWLIST` by subscribing only
    `composeio/+/<ns>` per allowed namespace. The handler then "re-checked" the
    allowlist — against traffic the broker had already excluded. So the
    `filtered` counter was structurally incapable of incrementing, and the
    shutdown line `allowlist dropped 0` was not a measurement: it was an
    arithmetic identity dressed as evidence. A counter that cannot increment is
    not an instrument.

    NOW: subscribe `composeio/#`, ALWAYS. Every filtering decision happens in
    this process, where it is counted, named, and published. The cost is that
    the bridge decodes traffic it will not forward; the benefit is that it can
    tell you what it did not forward and why. Silence is no longer the only
    possible report.

DENY, DON'T ALLOW.
    `CAN_BRIDGE_DENYLIST` replaces the allowlist. An allowlist makes the
    default "nothing crosses", which sounds safe and produces a bridge that
    quietly does nothing when a namespace is renamed. A deny list makes the
    default "it crosses, and it is measured", so the failure mode is loud
    traffic rather than silent absence — and the representability gate + the
    per-namespace drop census below are what keep loud from meaning unbounded.
    `CAN_BRIDGE_ALLOWLIST` is still HONORED where it is already deployed, with
    a deprecation warning naming its replacement; it is never silently ignored.

THE REGISTRY IS SELF-POPULATING AND BIDIRECTIONAL, AND IT IS NOT A PERMISSION
SYSTEM.
    The insight that removes almost all the configuration: the bridge never
    needs to REVERSE a hash, it needs to have SEEN THE NAME ONCE. Every
    namespace observed on MQTT registers name→hash AND hash→name, so a CAN node
    replying on a namespace the bridge has relayed is resolvable without anyone
    listing it anywhere. `CAN_BRIDGE_REGISTER` shrinks to what it should always
    have been: a fallback for namespaces that exist ONLY on the CAN side and
    therefore can never be learned from MQTT.

    Learning is deliberately kept SEPARATE from forwarding rights. If observing
    a namespace granted permission to put it on the segment, then anything that
    can publish to MQTT could put traffic on a shared 500 kbit bus — the
    registry would have become an authorization oracle by accident. So:
    `observe_ns` learns; `_forwarding_decision` governs; they share no state
    except the collision refusal, which is an integrity rule, not a policy.

DROP, NEVER TRUNCATE.
    `transport_can.py:279` DROPS a value that will not fit a classic frame and
    says so. This file used to TRUNCATE (`data = encoded[:8]`) and send the
    fragment with a warning. Two files in one repo disagreeing about the same
    wire is how a receiver learns to trust a prefix. The bridge was the wrong
    one: a fragment is not a smaller version of the value, it is a DIFFERENT
    value that decodes cleanly. Oversize is now a DROP, counted per namespace,
    warned once per namespace.

COLLISION REFUSES THE NEWCOMER.
    Two different namespaces can hash to the same 16 bits (CRC-16 over a 65536
    space). Refusing BOTH would break a namespace that is currently working;
    refusing the newcomer cannot break anything, because the bridge is the
    chokepoint — a namespace it has never forwarded is one no CAN node can have
    learned. So the incumbent keeps the hash, the newcomer is refused, counted
    and warned loudly, and the forwarding table published every 5 s carries
    name→hash for everything forwarded so any consumer can see the collision
    for itself rather than taking this process's word for it.

ENOBUFS IS NOT "A CAN WRITE ERROR".
    `txqueuelen` is 10 on the MCP2515 interfaces. A burst offered faster than
    the driver drains gets `OSError(ENOBUFS)` — measured on rpi5: a 2000-frame
    back-to-back burst is refused after roughly a dozen frames. The old code
    caught that as a generic `Exception` and logged "CAN write error", which
    reads like a dead interface. It is now detected by errno, counted
    separately, and published. It is NOT retried in a loop: a retry loop turns
    a measurable refusal into an invisible latency, and the first job is to
    know the number.

THE TRAP THIS BRIDGE SETS FOR ITSELF.
    It subscribes `composeio/#` AND publishes to MQTT. Its own status blobs
    therefore come straight back to `_on_mqtt_message`, and the echo guard does
    NOT catch them — that guard only knows `source == "can"`, and these are
    sourced from the bridge itself. Left alone the bridge would relay its own
    forwarding table onto the segment, every five seconds, forever. The
    exclusion is BY CONSTRUCTION (`SELF_NS_PREFIX`, computed from
    MQTT_CLIENT_ID at import) and not by a line in a config file, because a
    config file is a thing an operator can edit and this is a thing that must
    be impossible.

CONFIGURATION (all environment variables; the systemd unit ships an
EnvironmentFile, which is where a deployment states its deny list)
    CAN_INTERFACE          interface to bind (default can0). ONE channel.
    MQTT_BROKER/MQTT_PORT  broker (default localhost:1884)
    MQTT_CLIENT_ID         distinct per deployment — two clients sharing an id
                           on one broker evict each other in a reconnect loop.
                           It also names this bridge's own status namespaces.
    CAN_BRIDGE_DENYLIST    MQTT → CAN deny list, comma/space separated.
                           `fnmatch` patterns allowed (`dradis/*`). Default
                           EMPTY: everything representable crosses.
    CAN_BRIDGE_ALLOWLIST   DEPRECATED, still honored. When set, it is an
                           allowlist as before and a WARNING names
                           CAN_BRIDGE_DENYLIST.
    CAN_BRIDGE_REGISTER    namespaces that exist only on the CAN side, so they
                           can never be learned from MQTT observation.
    CAN_BRIDGE_STATUS_S    forwarding-table publication period (default 5.0).
    CAN_BRIDGE_NODE_ID     this bridge's ALLOCATED 4-bit node id for the
                           [BP-CAN-ID] `src` field, 1..15. Default 0 =
                           UNASSIGNED, which is the truth until something
                           allocates ids. Out of range is REFUSED, not masked.
    CAN_BRIDGE_REGISTRY_FILE  where to PERSIST the learned name table. Unset =
                           persistence OFF, said out loud at startup. There is
                           deliberately no default path.
    CAN_BRIDGE_ANNOUNCE_POLL  1 (default) to publish one `_device/broadcast/
                           announce` frame onto CAN at startup, so nodes
                           re-announce their names into a table this process
                           has just lost. `0` disables it.


THE LAST HOLE, CLOSED (2026-08-13): A CAN-ORIGINATED NAME CAN NOW BE LEARNED
===========================================================================
Everything above learns `name -> hash` from MQTT. That closes ONE direction
completely and leaves the other one wide open: **a namespace that has never
appeared on MQTT cannot be learned at all.** A board publishing
`_device/hosyond-d1afe8/heartbeat` puts 0xCC01 on the wire and nothing here
could turn 0xCC01 back into that string — a CRC is not reversible — so the
frame was correctly, loudly and permanently dropped. That is why device
self-announcement shipped with CAN scoped OUT and why `composeio fleet` states
in its own output that it cannot name a CAN device.

A PREFIX RULE (`_device/*`) IS NOT MERELY MISSING, IT IS IMPOSSIBLE. The wire
id is a CRC over the FULL name INCLUDING the device id, so the prefix cannot be
expanded into hashes without already knowing every device id — the thing being
discovered. Nine `CAN_BRIDGE_REGISTER` entries were hand-typed for one board;
the tenth board reports a number.

WHAT WORKS IS ONE HARDCODED ENTRY. `_device/announce` (0x31AE) is pre-registered
HERE AT IMPORT, before any configuration is read, in both directions. A node
sends its namespace NAMES over it, chunked with `config_chunk`; this bridge
reassembles, verifies the CRC-16, and feeds each name through the SAME
`observe_ns` the MQTT path uses. Learning is therefore identical whichever
substrate the name arrived on — including the collision refusal, which an
announced newcomer is subject to exactly like an observed one.

`composeio/ns_announce.py` holds the root, the payload format and the
reassembly; this file holds the routing, the census and the persistence.

PERSISTED LEARNING CAN PERSIST A MISTAKE, SO IT STAYS VISIBLE. The learned
table survives a restart via CAN_BRIDGE_REGISTRY_FILE, every entry is
re-verified against the hash function on load, every entry carries its
PROVENANCE (builtin / env / mqtt / can / persisted), and the whole table goes
out on the 5 s publication. A persisted table nobody can see is a configuration
file nobody wrote.
"""

from __future__ import annotations
import errno
import fnmatch
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("can_mqtt_bridge")

# Add composeio package to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))

from composeio.transport import (
    BusMessage, MessageType,  # noqa: F401  (MessageType used by callers/handlers)
    message_type_for_wire_code, wire_code_for_type,  # noqa: F401
)
from composeio.config import namespace_hash
# THE ID LAYOUT AND THE VALUE CODEC ARE IMPORTED, NEVER RESTATED. Both of this
# file's local copies became defects: its own `(ns << 13) | (type << 9) | src`
# was one half of [BP-CAN-ID-DIVERGENCE], and its own value decoder was one half
# of DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9.
from composeio.transport_can import (
    NODE_ID_MAX, NODE_ID_UNASSIGNED,
    _TYPE_SHIFT as CAN_TYPE_SHIFT,
    can_id_ns_hash, decode_arbitration_id, encode_arbitration_id,
)
from composeio.transport_uart import decode_value, encode_value
from composeio.peer.socketcan import open_can
# THE WELL-KNOWN ROOT AND THE ANNOUNCEMENT FORMAT ARE IMPORTED, NEVER RESTATED
# — the same rule that reconciled [BP-CAN-ID-DIVERGENCE] two imports up. The
# node and the bridge must agree on ONE constant; two copies of it is the whole
# failure mode the constant exists to remove.
from composeio.ns_announce import (
    NS_ANNOUNCE, NS_ANNOUNCE_HASH, NS_ANNOUNCE_POLL, WELL_KNOWN,
    AnnounceLearner, load_registry, registry_path_from_env, save_registry,
)


# --- Configuration ---

CAN_INTERFACE = os.environ.get("CAN_INTERFACE", "can0")
# Bitrate is an INTERFACE concern, set by can0.service / systemd
# (`ip link set can0 type can bitrate 500000`), NOT a userspace socket arg
# ([CXC-CAN-SOCKETCAN], [CXC-CAN-HW-MCP2515]). Kept only for the log line.
CAN_BITRATE = int(os.environ.get("CAN_BITRATE", "500000"))
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1884"))
MQTT_PREFIX = os.environ.get("MQTT_PREFIX", "composeio")
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "composeio-can-bridge")

#: Status publication period. PERIODIC, not on-change: a table that is only
#: published when it changes cannot be distinguished from a dead bridge, so the
#: MISSING publication is itself the liveness signal.
STATUS_PERIOD_S = float(os.environ.get("CAN_BRIDGE_STATUS_S", "5.0"))

#: This bridge's own namespaces. Computed from MQTT_CLIENT_ID at import so it
#: cannot be misconfigured — see the module docstring's trap section.
SELF_NS_PREFIX = f"_bridge/{MQTT_CLIENT_ID}/"
NS_FORWARDING = SELF_NS_PREFIX + "forwarding"
NS_DROPS = SELF_NS_PREFIX + "drops"
NS_DENYLIST = SELF_NS_PREFIX + "denylist"

#: A classic CAN 2.0B data field. The limit is the CONTROLLER, not a setting.
CAN_MAX_DATA = 8

#: Namespaces registered by every deployment, whatever its filtering — the BSG
#: game vocabulary this bridge was built for. Kept because a CAN-only talker in
#: that vocabulary must be nameable before the bridge has ever seen its name on
#: MQTT; everything else the registry now learns for itself.
BUILTIN_NAMESPACES = [
    "ftl_arm", "ftl_jump", "ftl_jump_led", "ftl_cap_charge",
    "ftl_coord_status", "ftl_coordinates", "sublight_engines",
    "alert_status", "shields", "warp_factor",
]

#: The allowlist value meaning "relay the whole MQTT bus onto CAN".
ALLOW_ALL = "*"

#: Messages decoded off CAN carry this source; never relay them back down.
#: This is the whole echo guard — see the module docstring.
CAN_SOURCE = "can"

#: The MQTT → CAN default, stated ONCE and out loud rather than as a `#` hidden
#: in a subscribe call. The bridge now ALWAYS subscribes `composeio/#` and
#: filters in-process, so this names the FORWARDING default, not the
#: subscription: with no deny list and no allowlist, everything representable
#: crosses — and every namespace that does not is counted and published.
DEFAULT_ALLOWLIST = ALLOW_ALL

# --- drop reasons: the vocabulary of the published census -------------------
DROP_DENIED = "denied"                # deny list / legacy allowlist
DROP_OVERSIZE = "oversize"            # will not fit a classic frame
DROP_UNRESOLVABLE = "unresolvable"    # CAN→MQTT: a hash with no name
DROP_COLLISION = "collision"          # a newcomer that hashes onto an incumbent
DROP_ENOBUFS = "enobufs"              # txqueuelen refused it
DROP_SELF = "self"                    # the bridge's own status blob, echoed
DROP_ENCODE = "encode_error"          # the value has no wire encoding
DROP_IO = "io_error"                  # any other write failure, with errno
DROP_REASONS = (DROP_DENIED, DROP_OVERSIZE, DROP_UNRESOLVABLE, DROP_COLLISION,
                DROP_ENOBUFS, DROP_SELF, DROP_ENCODE, DROP_IO)

# --- the namespace registry -------------------------------------------------
# Module-level and BIDIRECTIONAL, exactly as the service runs it.
_hash_to_name: dict[int, str] = {}
_name_to_hash: dict[str, int] = {}

#: Newcomers refused because their hash already belongs to a different
#: namespace: newcomer -> incumbent. A refusal is PERMANENT for the life of the
#: process, so a colliding namespace never intermittently gets through.
_collisions: dict[str, str] = {}

#: Unresolvable inbound hashes: hash -> frames seen. Reported loudly on first
#: sight and summarised at shutdown, never guessed at.
_unresolved_hashes: dict[int, int] = {}

#: Hashes that WERE unresolvable and are now named, and by what: hash ->
#: (name, frames dropped before it became nameable). THE INSTRUMENT REPORTING
#: ITS OWN REPAIR. Without it, an announcement that fixes a fleet-wide blind
#: spot is indistinguishable from a bridge that was never broken — and the
#: dropped-frame count that proves the guard was real would vanish the moment
#: the guard stopped firing.
_resolved_after_announce: dict[int, tuple[str, int]] = {}

#: name -> where the mapping came from. Not decoration: a persisted table can
#: persist a MISTAKE, so the audit publication has to be able to say "this
#: entry came out of a file written by a previous process" rather than implying
#: every row was observed this run.
PROV_WELLKNOWN = "wellknown"    # hardcoded in code, in every bridge and node
PROV_BUILTIN = "builtin"        # the BSG vocabulary below
PROV_ENV = "env"                # CAN_BRIDGE_REGISTER / legacy allowlist
PROV_MQTT = "mqtt"              # observed crossing the broker
PROV_CAN = "can"                # LEARNED FROM AN ANNOUNCEMENT — the new one
_provenance: dict[str, str] = {}

#: Provenance kinds worth writing to disk. `wellknown` and `builtin` are in the
#: source and `env` is in the unit file; re-deriving them every boot is free and
#: keeps the file to what was genuinely LEARNED — which is also exactly the set
#: that can be wrong.
PERSISTABLE = (PROV_MQTT, PROV_CAN)

#: The prefix `_load_persisted_registry` stamps on a restored row so the audit
#: publication can say "this came out of a file" rather than implying it was
#: observed this run.
PROV_PERSISTED_PREFIX = "persisted:"


def persistable_provenance(prov: str | None) -> str | None:
    """The provenance to WRITE for `prov`, or None if it must not be written.

    ⛔ MEASURED DECAY, 2026-08-15. `_load_persisted_registry` re-registers a
    restored row as `persisted:mqtt`, and the old test was `prov in
    PERSISTABLE` — which `persisted:mqtt` fails. So every shutdown wrote ONLY
    the names observed in the run that was ending, and silently discarded
    everything it had restored at its own startup:

        19:05 restart:  restored 71  ->  next shutdown wrote 71   (looked fine)
        19:16 restart:  restored 71  ->  next shutdown wrote 10

    The table decayed to whatever the last few minutes happened to see, and the
    startup line said `restored 71` both times — an instrument that could only
    report success. A CAN-only namespace would have gone back to being an
    irreversible hash after two ordinary restarts, which is the exact failure
    the persistence exists to prevent.

    Normalising here (rather than at load time) keeps the AUDIT honest: the
    published `registry` still shows `persisted:mqtt`, so a row restored from a
    file never masquerades as an observation made this run — while the FILE
    stores the underlying kind and cannot grow `persisted:persisted:mqtt`.
    """
    if not prov:
        return None
    base = prov[len(PROV_PERSISTED_PREFIX):] \
        if prov.startswith(PROV_PERSISTED_PREFIX) else prov
    return base if base in PERSISTABLE else None


def register_ns(name: str, via: str = PROV_ENV) -> None:
    """Register a namespace in BOTH directions, unconditionally.

    The blunt form, used for the well-known root, the built-ins and
    `CAN_BRIDGE_REGISTER` where the deployment has ASSERTED the mapping.
    `observe_ns` is the learning path and is the one that refuses collisions."""
    h = namespace_hash(name)
    _hash_to_name[h] = name
    _name_to_hash[name] = h
    _provenance.setdefault(name, via)


# --------------------------------------------------------------------------- #
# G1 — THE WELL-KNOWN ROOT, REGISTERED BEFORE ANYTHING ELSE HAPPENS            #
# --------------------------------------------------------------------------- #
# AT IMPORT, not in main(), and that placement is the point. This is the ONE
# entry that cannot be learned, cannot be configured and cannot be omitted: if
# it depended on main() running, then every code path that uses this module as a
# library (the tests, a future embedded runner) would silently lack the one
# namespace that makes discovery possible at all. It is also NOT in
# BUILTIN_NAMESPACES, because that list is a deployment's game vocabulary and is
# editable; this is protocol.
for _wk in WELL_KNOWN:
    register_ns(_wk, PROV_WELLKNOWN)


#: observe_ns outcomes
OBSERVE_NEW = "new"
OBSERVE_KNOWN = "known"
OBSERVE_COLLISION = "collision"


def observe_ns(name: str, via: str = PROV_MQTT) -> str:
    """Learn a namespace. Returns one of the OBSERVE_* outcomes.

    ONE learning path for BOTH substrates. A name observed on MQTT and a name
    announced over CAN differ only in the `via` tag: they get the same
    collision rule, the same permanence and the same table. That sameness is
    the design — a second, softer learning path for the substrate that cannot
    be cross-checked is how a bridge ends up trusting the wire more than the
    broker.

    THIS GRANTS NO FORWARDING RIGHT. It exists so that a CAN node publishing on
    a namespace can be NAMED, which is the only thing the hash direction is
    for. Forwarding is decided separately by
    `CANMQTTBridge._forwarding_decision`.

    Collision policy: the INCUMBENT keeps the hash and the NEWCOMER is refused.
    Refusing both would take down a namespace that currently works; refusing
    the newcomer cannot, because this bridge is the chokepoint and a namespace
    it has never forwarded is one no CAN node has ever learned."""
    if name in _collisions:
        return OBSERVE_COLLISION
    known = _name_to_hash.get(name)
    if known is not None:
        return OBSERVE_KNOWN
    h = namespace_hash(name)
    incumbent = _hash_to_name.get(h)
    if incumbent is not None and incumbent != name:
        _collisions[name] = incumbent
        logger.error(
            "NAMESPACE HASH COLLISION: %r (via %s) hashes to 0x%04X, which is "
            "already held by %r. REFUSING the newcomer %r — it will not be "
            "forwarded to %s and frames carrying 0x%04X keep resolving to %r. "
            "The incumbent is load-bearing (CAN nodes have already learned it); "
            "the newcomer is not (this bridge is the chokepoint and has never "
            "carried it). A 16-bit id is 2.3%% likely to do this at 56 "
            "namespaces and 39%% at 256 — announcement makes it VISIBLE, it "
            "does not make it rarer. Rename one of them.",
            name, via, h, incumbent, name, CAN_INTERFACE, h, incumbent)
        return OBSERVE_COLLISION
    _hash_to_name[h] = name
    _name_to_hash[name] = h
    _provenance[name] = via
    # THE REPAIR, RECORDED. If this hash has been arriving off CAN and getting
    # dropped, say so at the moment it stops — with the count of what was lost.
    dropped = _unresolved_hashes.pop(h, None)
    if dropped:
        _resolved_after_announce[h] = (name, dropped)
        logger.warning(
            "0x%04X is now RESOLVABLE as %r (learned via %s). %d frame(s) "
            "carrying it were DROPPED before this — that is what the gap cost, "
            "and it is published on %s so it is not just a log line nobody "
            "read.", h, name, via, dropped, NS_DROPS)
    return OBSERVE_NEW


# --------------------------------------------------------------------------- #
# G3 — LEARNING FROM THE CAN SIDE                                              #
# --------------------------------------------------------------------------- #
#: Module-level, exactly like the registry it feeds, because `message_from_can`
#: is module-level and the run loop's routing must not depend on which
#: CANMQTTBridge instance happens to exist.
def _learn_from_announcement(name: str) -> str:
    outcome = observe_ns(name, PROV_CAN)
    if outcome == OBSERVE_NEW:
        _persist_registry("learned %r from a CAN announcement" % name)
    return outcome


_announce_learner = AnnounceLearner(_learn_from_announcement, label="bridge")


# --------------------------------------------------------------------------- #
# G4 — PERSISTENCE                                                             #
# --------------------------------------------------------------------------- #
REGISTRY_FILE = registry_path_from_env()

#: Problems the loader refused to swallow. Published, not just logged: a
#: persisted entry that failed verification is a namespace that will NOT be
#: named this run, and the operator's mental model still says it will be.
_registry_problems: list[str] = []
_registry_loaded = 0
_registry_saves = 0
_registry_save_errors = 0


def _persist_registry(why: str = "") -> int:
    """Write the LEARNED half of the table. Returns entries written, or -1.

    Only `mqtt` and `can` provenance are written. Everything else is in the
    source or the unit file and is re-derived every boot — persisting it would
    make a code change look like it had taken effect while a stale file quietly
    overrode it."""
    global _registry_saves, _registry_save_errors
    if not REGISTRY_FILE:
        return -1
    # The NORMALISED kind goes to disk (see `persistable_provenance`): the file
    # records what a row IS, the publication records where this process got it.
    entries = {}
    for _n, _p in _provenance.items():
        _base = persistable_provenance(_p)
        if _base is not None:
            entries[_n] = _base
    try:
        n = save_registry(REGISTRY_FILE, entries,
                          meta={"client_id": MQTT_CLIENT_ID,
                                "interface": CAN_INTERFACE, "why": why})
    except Exception as e:
        _registry_save_errors += 1
        logger.error(
            "could not persist the learned namespace table to %s: %r. The "
            "table is still LIVE in this process; it will be LOST on restart "
            "and every CAN-only namespace will go back to being an "
            "irreversible hash until the next announcement.", REGISTRY_FILE, e)
        return -1
    _registry_saves += 1
    logger.debug("persisted %d learned namespace(s) to %s (%s)",
                 n, REGISTRY_FILE, why)
    return n


def _load_persisted_registry() -> int:
    """Restore the learned table. AT IMPORT — a restart is a re-import.

    Every entry is re-verified against `namespace_hash` before it is believed
    (see `ns_announce.load_registry`), and a refusal is kept in
    `_registry_problems` for the audit publication rather than dropped."""
    global _registry_loaded
    if not REGISTRY_FILE:
        logger.info(
            "CAN_BRIDGE_REGISTRY_FILE is not set — the learned namespace table "
            "is NOT persisted. Every CAN-only namespace becomes an "
            "irreversible hash again on restart, until each node re-announces. "
            "Set it in the unit's EnvironmentFile to keep the table across a "
            "restart; there is deliberately no default path, because a hidden "
            "state file changes the answer at exactly the moment an operator "
            "restarts a misbehaving bridge.")
        return 0
    res = load_registry(REGISTRY_FILE)
    _registry_problems.extend(res.problems)
    for problem in res.problems:
        logger.error("persisted registry: %s", problem)
    n = 0
    for name, prov in res.entries.items():
        # Through observe_ns, NOT register_ns: a persisted entry is subject to
        # the same collision rule as a live one. A file must not be able to
        # install a mapping that an observation would have been refused.
        if observe_ns(name, f"persisted:{prov}") == OBSERVE_NEW:
            n += 1
    _registry_loaded = n
    if n or res.problems:
        logger.info(
            "restored %d learned namespace(s) from %s (saved %s)%s — provenance "
            "travels with them and the whole table is published on %s every "
            "%.0f s, because a persisted table nobody can see is a "
            "configuration file nobody wrote.", n, REGISTRY_FILE,
            time.strftime("%Y-%m-%dT%H:%M:%S",
                          time.localtime(res.saved_at)) if res.saved_at else "?",
            f"; {len(res.problems)} entry(ies) REFUSED" if res.problems else "",
            NS_FORWARDING, STATUS_PERIOD_S)
    return n


_load_persisted_registry()


def parse_namespace_list(raw: str | None) -> list[str]:
    """Split a comma/whitespace separated namespace list from the environment.

    Returns `[]` for unset/empty. `ALLOW_ALL` survives as a single entry so the
    caller can distinguish "everything, deliberately" from "not configured"."""
    if not raw:
        return []
    out: list[str] = []
    for chunk in raw.replace(",", " ").split():
        if chunk and chunk not in out:
            out.append(chunk)
    return out


def load_denylist() -> list[str]:
    """The MQTT → CAN deny list. EMPTY by default — see the module docstring.

    Entries are `fnmatch` patterns, so `dradis/*` denies a whole family without
    an operator having to enumerate a game's vocabulary."""
    return parse_namespace_list(os.environ.get("CAN_BRIDGE_DENYLIST"))


def load_allowlist() -> list[str] | None:
    """DEPRECATED. The legacy MQTT → CAN allowlist, or None for "no allowlist".

    Still honored wherever it is already deployed — `deploy/can_mqtt_bridge/
    composeio-can-bridge.env` sets it on rpi5 today — because silently ignoring
    a filter that a deployment is relying on would put the whole production bus
    onto a shared 500 kbit segment on the strength of a rename. `main()` warns
    and names `CAN_BRIDGE_DENYLIST`.

    None is returned for both the unset default and an explicit `*`; the two
    are distinguished for LOGGING by :func:`allowlist_is_default`."""
    entries = parse_namespace_list(os.environ.get("CAN_BRIDGE_ALLOWLIST")) \
        or parse_namespace_list(DEFAULT_ALLOWLIST)
    if ALLOW_ALL in entries:
        return None
    return entries


def allowlist_is_default() -> bool:
    """True when no deployment stated an allowlist and the default applied."""
    return not parse_namespace_list(os.environ.get("CAN_BRIDGE_ALLOWLIST"))


def allowlist_is_set() -> bool:
    """True when a deployment states CAN_BRIDGE_ALLOWLIST at all, `*` included.
    That is the condition for the deprecation warning — a caller who chose the
    firehose through the old variable still needs to hear about the new one."""
    return bool(parse_namespace_list(os.environ.get("CAN_BRIDGE_ALLOWLIST")))


# --- CAN Frame Encoding ([BP-CAN-ID] v2, ratified 2026-08-13) ---
# 29-bit extended ID: prio(3) | type(4) | ns_id(16) | src(4) | scope(2)
# Data: up to 8 bytes of value encoding
#
# THE LAYOUT IS NOT DECLARED HERE EITHER. `composeio.transport_can` owns the
# shifts and the masks; this file imports its encoder and decoder. That is the
# reconciliation of [BP-CAN-ID-DIVERGENCE]: there used to be two layouts in this
# repo — `ns_hash(16)|type(4)|src(9)` here and in the firmware,
# `type(4)|scope(2)|ns(16)|seq(7)` in transport_can — that did not merely differ
# in bit positions but DISAGREED ABOUT WHAT ARBITRATION IS FOR, and could not
# share a physical wire. Restating the shifts here is how that happened; not
# restating them is how it stays fixed.
#
# WHAT v2 BUYS, in this file specifically:
#   * `prio` — arbitration priority is now a design decision instead of a CRC
#     artifact. This bridge used to put `shields` on the wire at the LOWEST
#     priority of anything it carried, because 0xCC50 happens to be a big number.
#   * `scope` — was structurally impossible to carry and was reconstructed as
#     "global" on every inbound frame. A relay that cannot see scope leaks all
#     local chatter upstream, so this is a prerequisite for the mothercard trunk,
#     not tidiness.
#   * `src` — an ALLOCATED node id (CAN_BRIDGE_NODE_ID), 1..15, 0 = unassigned.
#     It replaces `namespace_hash(msg.source) & 0x1FF`, a nine-bit smear that
#     nothing decoded.
#
# The type nibble table is NOT declared here. It is [BP-TYPE-CODES], reused by
# reference from composeio.transport. This file used to hold its own five-entry
# copy {state, event, heartbeat, discovery, time_sync} with a `.get(type, 0)`
# fallback, so config_push/config_request/binding/ack/log_entry crossed the
# bridge relabelled as STATE — the same defect that made a UART ACK decode as
# STATE, in a third place (2026-08-10).

#: This bridge's ALLOCATED node id for the `src` field. 0 = unassigned, which is
#: the honest default: nothing allocates ids yet ([PLAN F], workstream 0 will).
#: Out of range is REFUSED, never masked — 16 masked to 4 bits becomes node 0.
def _load_node_id() -> int:
    raw = os.environ.get("CAN_BRIDGE_NODE_ID", "")
    if not raw:
        return NODE_ID_UNASSIGNED
    try:
        node = int(raw, 0)
    except ValueError:
        logger.error("CAN_BRIDGE_NODE_ID=%r is not an integer — this bridge "
                     "stays UNASSIGNED (src=0).", raw)
        return NODE_ID_UNASSIGNED
    if not (NODE_ID_UNASSIGNED <= node <= NODE_ID_MAX):
        logger.error("CAN_BRIDGE_NODE_ID=%d is outside 0..%d ([BP-CAN-ID] src "
                     "is 4 bits) — this bridge stays UNASSIGNED (src=0). NOT "
                     "masked: 16 would become 0 and collide with unassigned.",
                     node, NODE_ID_MAX)
        return NODE_ID_UNASSIGNED
    return node


CAN_NODE_ID = _load_node_id()


def can_id_from_message(msg: BusMessage) -> int:
    """Encode a bus message into a 29-bit CAN extended ID ([BP-CAN-ID] v2).

    `scope` is taken from the message and defaults to `auto` — the bus's own
    word for "the sender did not say" — never to `global`, which is a claim
    about locality nobody made.

    The registry lookup that used to sit here (`_name_to_hash.get(ns, hash(ns))`)
    is gone: `register_ns`/`observe_ns` store exactly `namespace_hash(name)`, so
    the lookup could only ever return the value the fallback computed. It read
    as though the registry could override the hash. It could not.
    """
    return encode_arbitration_id(msg.namespace, msg.type,
                                 msg.scope or "auto", CAN_NODE_ID)


def message_from_can(can_id: int, data: bytes) -> BusMessage | None:
    """Decode a CAN frame into a BusMessage, or None if it cannot be named."""
    ns_h, msg_type_or_none, scope, src = decode_arbitration_id(can_id)
    type_nibble = (can_id >> CAN_TYPE_SHIFT) & 0x0F

    # THE WELL-KNOWN ROOT IS CONSUMED HERE, NOT RELAYED ([PLAN G3]).
    # `_device/announce` carries config_chunk frame VALUES, not a bus value:
    # relaying `[seq_hi][seq_lo][6 bytes]` onto MQTT as though it were a reading
    # would put binary fragments on the broker under a name that looks like a
    # device state. It is reassembled instead, and what CROSSES to MQTT is the
    # consequence — every `_device/<id>/*` frame that now has a name.
    #
    # Routed BEFORE the `_hash_to_name` lookup even though the root IS
    # registered, so the ordering states the intent rather than relying on the
    # registry entry to be absent.
    if ns_h == NS_ANNOUNCE_HASH:
        _announce_learner.on_frame(src, data)
        return None

    ns_name = _hash_to_name.get(ns_h)
    if ns_name is None:
        # A namespace hash is NOT reversible. This used to fabricate
        # `_unknown_0x1234` and publish under it, putting a namespace no author
        # ever wrote onto the real bus, indistinguishable from data. Same rule
        # as the type-code path below and as uart_mqtt_bridge: DROPPED, not
        # guessed. Register the namespace (`CAN_BRIDGE_REGISTER`) to carry it.
        seen = _unresolved_hashes.get(ns_h, 0)
        _unresolved_hashes[ns_h] = seen + 1
        if seen == 0:
            logger.warning(
                "CAN frame id %#x carries UNRESOLVABLE namespace hash 0x%04X "
                "(%d data byte(s)) — DROPPED, not guessed. No registered "
                "namespace hashes to 0x%04X; add it to CAN_BRIDGE_REGISTER if "
                "this link should carry it — or, better, have the node ANNOUNCE "
                "its names on %s (0x%04X), which needs no hand-typed entry and "
                "cannot go stale when a board is added. Further frames for this "
                "hash are counted, not logged.",
                can_id, ns_h, len(data), ns_h, NS_ANNOUNCE, NS_ANNOUNCE_HASH)
        return None

    msg_type = msg_type_or_none
    if msg_type is None:
        logger.warning(
            "CAN frame id %#x for %s carries unallocated message-type code %d "
            "(allocated 0..9, see [BP-TYPE-CODES]) — DROPPED, not guessed.",
            can_id, ns_name, type_nibble)
        return None

    # THE VALUE DECODER IS NOT WRITTEN HERE ANY MORE.
    # `composeio.transport_uart.decode_value` is the ONE implementation; this
    # file's inline copy is what made DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-
    # EACAE9 a two-headed defect. That copy guessed INT for a 4-byte value while
    # the canon guessed FLOAT, so one wire value had two readings: 42.0 arrived
    # here as 1109917696 and 1000 arrived there as 1.4e-42. Neither was a safe
    # convergence target — negative integers were wrong in BOTH — so the fix was
    # a type tag on the wire ([BP-VALUE-CODEC], the 0xFE/0xFF value tags) plus
    # deleting the second decoder. Two decoders cannot disagree if there is one.
    value = decode_value(data)

    # `scope` COMES OFF THE WIRE. It was hardcoded "global" here because the old
    # layout had no scope field at all, so a CAN node's `local` message arrived
    # on MQTT relabelled `global` — which is precisely the leak the mothercard
    # trunk cannot tolerate. `src` names the transmitting node when one has been
    # allocated; diagnostic only, and it never becomes the message's identity —
    # the echo guard depends on `source == CAN_SOURCE` matching exactly.
    if src != NODE_ID_UNASSIGNED:
        logger.debug("CAN frame id %#x for %s came from node %d (scope=%s)",
                     can_id, ns_name, src, scope)
    return BusMessage(
        namespace=ns_name,
        type=msg_type,
        value=value,
        source=CAN_SOURCE,
        scope=scope,
    )


class CANMQTTBridge:
    """Bidirectional CAN ↔ MQTT bridge."""

    def __init__(self, allowlist: list[str] | None = None,
                 denylist: list[str] | None = None):
        """`denylist` is the MQTT → CAN filter: namespaces (or `fnmatch`
        patterns) that must NOT reach the segment. Empty/None means nothing is
        denied.

        `allowlist` is the DEPRECATED inverse and is still honored when a
        deployment supplies one: when it is a list, only those namespaces
        cross. None means no allowlist. Both may be set; deny is evaluated
        first, because a deny list is a statement about harm and an allowlist
        is a statement about scope."""
        self._can_bus = None
        self._mqtt_client = None
        self._running = False
        self._allowlist: set[str] | None = set(allowlist) if allowlist else None
        self._denylist: list[str] = list(denylist or [])
        self.up = 0        # CAN  -> MQTT
        self.down = 0      # MQTT -> CAN
        self.filtered = 0  # MQTT messages dropped by deny list / legacy allowlist
        self.echoes = 0    # MQTT messages dropped by the source=="can" guard
        self.enobufs = 0   # CAN writes refused because txqueuelen was full
        self.observed = 0  # namespaces learned from MQTT observation
        #: namespace -> reason -> count. The REALITY half of the published
        #: table; the deny list is only the INTENT half.
        self.drops: dict[str, dict[str, int]] = {}
        #: namespace -> hash, for everything actually forwarded to CAN. Published
        #: so a collision is detectable by a consumer instead of only by this
        #: process, which is the one that would be wrong about it.
        self.forwarded: dict[str, int] = {}
        self._warned: set[tuple[str, str]] = set()
        self._status_thread: threading.Thread | None = None
        self._status_stop = threading.Event()
        self._started_at = time.time()
        self.status_publications = 0
        #: `_device/broadcast/announce` frames this bridge put on the segment.
        self.announce_polls = 0

    # -- counting ---------------------------------------------------------- #

    def count_drop(self, namespace: str, reason: str) -> int:
        """Record one dropped message, BY NAMESPACE and BY REASON.

        The whole point of the rebuild: `filtered` used to be a single integer
        that could not increment. A census can name the namespace that is being
        refused, which is the difference between "the bridge is quiet" and "the
        bridge is refusing `dradis/bodies` 41 times a second because it does not
        fit in eight bytes"."""
        per = self.drops.setdefault(namespace, {})
        per[reason] = per.get(reason, 0) + 1
        return per[reason]

    def _warn_once(self, namespace: str, reason: str, msg: str, *args) -> None:
        key = (namespace, reason)
        if key in self._warned:
            return
        self._warned.add(key)
        logger.warning(msg, *args)

    def drop_totals(self) -> dict[str, int]:
        totals = {r: 0 for r in DROP_REASONS}
        for per in self.drops.values():
            for reason, n in per.items():
                totals[reason] = totals.get(reason, 0) + n
        return {r: n for r, n in totals.items() if n}

    # -- what will cross, said before a single frame does ------------------ #

    def _describe_allowlist(self) -> None:
        """The LEGACY allowlist's description. Kept because a deployment that
        still sets `CAN_BRIDGE_ALLOWLIST` deserves the same warning it always
        got; `_describe_filtering` is the one that describes the new design."""
        if self._allowlist is not None:
            logger.info("MQTT->CAN allowlist (DEPRECATED): %d namespace(s): %s",
                        len(self._allowlist), ", ".join(sorted(self._allowlist)))
            return
        logger.warning(
            "MQTT->CAN allowlist is NOT SET — every namespace on '%s/#' is a "
            "forwarding candidate for %s. On a shared 500 kbit segment that "
            "contends with every other talker (including config transfers). "
            "Set CAN_BRIDGE_DENYLIST to bound it; oversize values are DROPPED "
            "and counted rather than truncated, and the per-namespace census "
            "is published on %s every %.0f s.",
            MQTT_PREFIX, CAN_INTERFACE, NS_DROPS, STATUS_PERIOD_S)

    def _describe_filtering(self) -> None:
        logger.info("MQTT->CAN deny list: %d entry(ies)%s%s",
                    len(self._denylist),
                    ": " if self._denylist else " (EMPTY — nothing is denied)",
                    ", ".join(self._denylist))
        logger.info("this bridge's OWN namespaces are excluded from %s by "
                    "construction: %s* (never configurable)",
                    CAN_INTERFACE, SELF_NS_PREFIX)
        self._describe_allowlist()

    # -- the forwarding decision ------------------------------------------- #

    def _is_denied(self, namespace: str) -> bool:
        return any(namespace == p or fnmatch.fnmatchcase(namespace, p)
                   for p in self._denylist)

    def _forwarding_decision(self, msg: BusMessage) -> str | None:
        """None to forward; otherwise the DROP_* reason.

        Deliberately separate from `observe_ns`. The registry learns; this
        governs. If learning granted the right to forward, then anything with
        MQTT publish access would have write access to a shared physical
        segment — which is a permission system nobody designed."""
        ns = msg.namespace
        # BY CONSTRUCTION, first, and before anything a config file can reach:
        # the bridge subscribes `#` and publishes its own status, so without
        # this it relays its own forwarding table onto CAN every 5 s. The echo
        # guard cannot catch it — source is this bridge, not "can".
        if ns.startswith(SELF_NS_PREFIX):
            return DROP_SELF
        if ns in _collisions:
            return DROP_COLLISION
        if self._is_denied(ns):
            return DROP_DENIED
        if self._allowlist is not None and ns not in self._allowlist:
            return DROP_DENIED
        return None

    # -- lifecycle ---------------------------------------------------------- #

    def start(self):
        """Connect to CAN bus and MQTT broker."""
        self._running = True
        self._describe_filtering()

        # CAN setup — stdlib SocketCAN, no python-can ([CXC-CAN-BRIDGE-MIGRATE]).
        # Bitrate is already set on the interface by can0.service.
        try:
            self._can_bus = open_can(CAN_INTERFACE)
            logger.info(f"CAN connected: {CAN_INTERFACE} @ {CAN_BITRATE} (interface bitrate)")
            self.poll_announcements()
        except Exception as e:
            logger.error(f"CAN setup failed: {e}")
            logger.info("Running in MQTT-only mode (CAN messages will not be bridged)")

        # MQTT setup
        try:
            import paho.mqtt.client as mqtt
            # MQTT 5, EXPLICITLY. paho's default is MQTTv311 and an omitted
            # `protocol=` is INVISIBLE at the call site — which is exactly how
            # this whole stack negotiated 3.1.1 for its entire life without
            # anyone noticing. At 3.1.1 there are no PUBLISH properties, so
            # `MessageExpiryInterval` is silently ignored and a retained value
            # carries NO AGE ([DEC-IMPLEMENT-MQTT-RETAIN-WITH-AN-AGE-CONTRACT]).
            # A BRIDGE IS THE WORST PLACE TO LOSE IT: everything CAN-side is
            # already un-ageable, so this hop is the first one that CAN date a
            # value.
            self._mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID,
                                            protocol=mqtt.MQTTv5)
            self._mqtt_client.on_connect = self._on_mqtt_connect
            self._mqtt_client.on_message = self._on_mqtt_message
            self._mqtt_client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)
            self._mqtt_client.loop_start()
            logger.info(f"MQTT connected: {MQTT_BROKER}:{MQTT_PORT}")
        except Exception as e:
            logger.error(f"MQTT setup failed: {e}")

        self._start_status_thread()

    def poll_announcements(self) -> bool:
        """Put ONE `_device/broadcast/announce` frame on the segment.

        This bridge has just lost (or just restored, possibly wrongly) its
        learned table, and the nodes are the only authority on their own names.
        Asking costs one frame; not asking costs a blind spot that lasts until
        something happens to reboot a board.

        The OUTBOUND direction was never the problem — a hash is computed from a
        name the sender already has — so this needs no registry entry and works
        on the very first frame after start.
        """
        if not self._can_bus:
            return False
        if os.environ.get("CAN_BRIDGE_ANNOUNCE_POLL", "1").strip() in ("0", "false", "no"):
            logger.info("CAN_BRIDGE_ANNOUNCE_POLL=0 — NOT polling %s at "
                        "startup. Names learned only from nodes that announce "
                        "on their own boot.", NS_ANNOUNCE_POLL)
            return False
        msg = BusMessage(namespace=NS_ANNOUNCE_POLL, type=MessageType.STATE,
                         value=1, source=MQTT_CLIENT_ID, scope="global")
        try:
            self._can_bus.send(can_id_from_message(msg), encode_value(1),
                               eff=True)
        except Exception as e:
            logger.warning(
                "could not poll %s at startup (%r) — nodes will still announce "
                "on their own boot, but this bridge's table stays as it is "
                "until they do.", NS_ANNOUNCE_POLL, e)
            return False
        self.announce_polls += 1
        logger.info(
            "polled %s (0x%04X) on %s — every node should re-announce its "
            "namespace NAMES on %s, which is how a CAN-originated name becomes "
            "resolvable at all.", NS_ANNOUNCE_POLL,
            namespace_hash(NS_ANNOUNCE_POLL), CAN_INTERFACE, NS_ANNOUNCE)
        return True

    def stop(self):
        self._running = False
        self._status_stop.set()
        if self._status_thread is not None:
            self._status_thread.join(timeout=2.0)
        if self._can_bus:
            self._can_bus.close()
        if self._mqtt_client:
            self._mqtt_client.loop_stop()
            self._mqtt_client.disconnect()
        logger.info("bridge down. relayed CAN->MQTT=%d MQTT->CAN=%d "
                    "(echo guard dropped %d, filter dropped %d, "
                    "learned %d namespace(s), status published %d time(s))",
                    self.up, self.down, self.echoes, self.filtered,
                    self.observed, self.status_publications)
        totals = self.drop_totals()
        if totals:
            logger.warning("MQTT->CAN drop census by reason: %s",
                           ", ".join(f"{r}={n}" for r, n in sorted(totals.items())))
            for ns, per in sorted(self.drops.items()):
                logger.warning("  %-28s %s", ns,
                               ", ".join(f"{r}={n}" for r, n in sorted(per.items())))
        if _collisions:
            logger.error("%d namespace(s) REFUSED for hash collision: %s",
                         len(_collisions),
                         ", ".join(f"{new!r}->0x{namespace_hash(new):04X} held by "
                                   f"{old!r}" for new, old in sorted(_collisions.items())))
        if _unresolved_hashes:
            logger.warning(
                "%d unresolvable namespace hash(es) seen on %s, %d frame(s) "
                "DROPPED: %s. A node that announces its names on %s needs no "
                "CAN_BRIDGE_REGISTER entry; one that cannot announce still "
                "does.", len(_unresolved_hashes), CAN_INTERFACE,
                sum(_unresolved_hashes.values()),
                ", ".join(f"0x{h:04X}x{n}"
                          for h, n in sorted(_unresolved_hashes.items())),
                NS_ANNOUNCE)
        # THE ANNOUNCEMENT CENSUS, ALWAYS — including when it is all zeros.
        # "0 announcements heard" on a segment that should have boards on it is
        # a REPORT; the absence of this line would be an assumption.
        cen = _announce_learner.census()
        logger.info(
            "announcement root %s (0x%04X): %d frame(s), %s, %d name(s) seen, "
            "%d refused, %d learned from CAN, %d poll(s) sent",
            NS_ANNOUNCE, NS_ANNOUNCE_HASH, cen["frames"],
            cen["by_outcome"] or "no outcomes", cen["names_seen"],
            cen["names_refused"],
            sum(1 for p in _provenance.values() if p == PROV_CAN),
            self.announce_polls)
        if _resolved_after_announce:
            logger.info(
                "%d hash(es) went from UNRESOLVABLE to named this run: %s",
                len(_resolved_after_announce),
                ", ".join(f"0x{h:04X}->{n} (after {c} dropped frame(s))"
                          for h, (n, c) in sorted(_resolved_after_announce.items())))
        _persist_registry("shutdown")

    def run(self):
        """Main loop: poll CAN bus, forward to MQTT."""
        while self._running:
            if self._can_bus:
                try:
                    frame = self._can_bus.read_frame(timeout=0.1)
                    if frame is not None:
                        arb, eff, data = frame
                        # ComposeBus-over-CAN rides 29-bit extended ids only.
                        if eff:
                            bus_msg = message_from_can(arb, data)
                            if bus_msg:
                                self._forward_to_mqtt(bus_msg)
                            elif can_id_ns_hash(arb) != NS_ANNOUNCE_HASH:
                                # An announcement frame is CONSUMED, not
                                # dropped: counting it as unresolvable would
                                # make the mechanism that removes the drops look
                                # like the biggest source of them.
                                self.count_drop(
                                    f"0x{can_id_ns_hash(arb):04X}",
                                    DROP_UNRESOLVABLE)
                except Exception as e:
                    logger.error(f"CAN read error: {e}")
                    time.sleep(0.5)
            else:
                time.sleep(0.1)

    # -- CAN -> MQTT --------------------------------------------------------- #

    def _forward_to_mqtt(self, msg: BusMessage):
        """Forward a CAN message to MQTT."""
        if not self._mqtt_client:
            return
        topic = f"{MQTT_PREFIX}/global/{msg.namespace}"
        payload = json.dumps(msg.to_json())
        # retain=False (paho default, stated here because it is a rule, not a
        # default we happen to like): a bridge relays live traffic; a retained
        # copy would resurrect a stale reading for every future subscriber.
        self._mqtt_client.publish(topic, payload, qos=1, retain=False)
        self.up += 1
        logger.info("CAN->MQTT  %-24s = %-12r (type=%s)",
                    msg.namespace, msg.value, msg.type.value)

    # -- MQTT -> CAN --------------------------------------------------------- #

    def _forward_to_can(self, msg: BusMessage):
        """Forward an MQTT message to CAN, or DROP it and say which and why."""
        if not self._can_bus:
            return
        ns = msg.namespace
        try:
            encoded = encode_value(msg.value)
        except Exception as e:
            self.count_drop(ns, DROP_ENCODE)
            self._warn_once(ns, DROP_ENCODE,
                            "%s value has no wire encoding (%r): %s — DROPPED. "
                            "Further occurrences are counted, not logged.",
                            ns, msg.value, e)
            return

        # REPRESENTABILITY GATE. `transport_can.py` drops an oversize value and
        # says so; this file used to truncate to 8 bytes and send the fragment,
        # so the same repo put two different values on two substrates for one
        # publish. A fragment is not a smaller value — it decodes cleanly as
        # something else, which is worse than nothing arriving.
        if len(encoded) > CAN_MAX_DATA:
            self.count_drop(ns, DROP_OVERSIZE)
            self._warn_once(
                ns, DROP_OVERSIZE,
                "%s value encodes to %d bytes; a classic CAN frame carries %d "
                "— DROPPED, not truncated (matching transport_can.py). "
                "Namespaces with values this large do not belong on this link; "
                "chunk them ([SPEC:CXC-CONFIG-BUS]) or deny them "
                "(CAN_BRIDGE_DENYLIST). Further occurrences are counted, not "
                "logged; the running total is published on %s.",
                ns, len(encoded), CAN_MAX_DATA, NS_DROPS)
            return

        arb_id = can_id_from_message(msg)
        try:
            self._can_bus.send(arb_id, encoded, eff=True)
        except OSError as e:
            if e.errno == errno.ENOBUFS:
                # NOT "CAN write error". `txqueuelen` is 10 on the MCP2515
                # interfaces; a burst offered faster than the driver drains is
                # refused here, and that is a CAPACITY fact about the segment,
                # not a fault. Deliberately NOT retried: a retry loop converts a
                # countable refusal into invisible latency, and this number is
                # the input to the pacing decision.
                self.enobufs += 1
                self.count_drop(ns, DROP_ENOBUFS)
                self._warn_once(
                    ns, DROP_ENOBUFS,
                    "%s: CAN write refused with ENOBUFS — the %s transmit queue "
                    "(txqueuelen, 10 by default on MCP2515) is full. The frame "
                    "is DROPPED, not queued and not retried. This is offered "
                    "load exceeding drain rate; pace the sender or raise "
                    "txqueuelen deliberately. Further occurrences are counted "
                    "per namespace and published on %s.", ns, CAN_INTERFACE,
                    NS_DROPS)
                return
            self.count_drop(ns, DROP_IO)
            self._warn_once(ns, DROP_IO,
                            "%s: CAN write failed, errno=%s (%s) — DROPPED. "
                            "Further occurrences are counted, not logged.",
                            ns, e.errno, e.strerror)
            return
        except Exception as e:
            self.count_drop(ns, DROP_IO)
            self._warn_once(ns, DROP_IO, "%s: CAN write error: %r — DROPPED.",
                            ns, e)
            return

        self.down += 1
        self.forwarded[ns] = can_id_ns_hash(arb_id)
        logger.info("MQTT->CAN  %-24s = %-12r id=0x%08X len=%d (src=%s)",
                    ns, msg.value, arb_id, len(encoded), msg.source)

    # -- MQTT callbacks ------------------------------------------------------ #

    def _on_mqtt_connect(self, client, userdata, flags, rc, properties=None):
        # `properties` DEFAULTS so BOTH callback arities work: MQTT 5 passes a
        # 5th argument and 3.1.1 does not. Without the default this raises a
        # TypeError inside paho's NETWORK THREAD, where it is swallowed — the
        # bridge would simply never mark itself connected. A dead bridge with no
        # error is the exact failure class this upgrade must not introduce.
        if rc != 0:
            logger.error("MQTT connect refused, rc=%s", rc)
            return
        # ALWAYS the whole bus. Narrowing HERE is what made `filtered`
        # structurally zero: a message the broker never delivered cannot be
        # counted as filtered, so the old bridge's own report was an identity,
        # not a measurement. Every decision now happens in-process where it is
        # named and published.
        client.subscribe(f"{MQTT_PREFIX}/#", qos=1)
        logger.info("MQTT subscribed to %s/# — filtering is IN-PROCESS so every "
                    "drop is counted by namespace and reason", MQTT_PREFIX)

    def _on_mqtt_message(self, client, userdata, mqtt_msg):
        payload = None      # bound BEFORE the try: the handler below reads it,
                            # and `json.loads` raising would leave it unbound.
        try:
            payload = json.loads(mqtt_msg.payload.decode())
            bus_msg = BusMessage.from_json(payload)
        except Exception as e:
            # Subscribing `composeio/#` means the bridge now sees EVERY payload
            # on the prefix, including ones that are not BusMessage-shaped at
            # all. That is a consequence of moving the filter in-process and it
            # is not an error condition — but at ERROR-per-message it would
            # bury the journal, and a log nobody can read is the same as no log.
            # Counted by TOPIC (there is no namespace to count it under),
            # warned once per topic.
            topic = getattr(mqtt_msg, "topic", "?")
            n = self.count_drop(f"<unparseable>{topic}", DROP_ENCODE)
            if n == 1:
                # ⛔ NAME THE PUBLISHER IF THE BYTES CARRY ONE.
                #
                # 2026-08-15: six restarts were refused here for carrying
                # `"type": "command"`, and this line named the TOPIC and the
                # enum error and nothing else. The publisher was never
                # identified from the log — it had to be reconstructed by hand
                # from six timestamps. Yet `src` was RIGHT THERE in the JSON:
                # the envelope failed on ONE field and the whole message,
                # including the only field that says who sent it, was
                # discarded with it.
                #
                # So the raw dict is re-read for `src` alone, defensively (the
                # payload may not even be a dict). A drop that can name its
                # author is a drop somebody can act on.
                who = ""
                try:
                    if isinstance(payload, dict) and payload.get("src"):
                        who = f" from src={payload['src']!r}"
                except Exception:               # pragma: no cover - defensive
                    who = ""
                logger.warning(
                    "MQTT topic %s carries a payload this bridge cannot read as "
                    "a BusMessage%s (%s) — DROPPED. This is normal on a shared "
                    "prefix; further occurrences on this topic are counted, not "
                    "logged, and appear in the census on %s.",
                    topic, who, e, NS_DROPS)
            return
        try:
            # 1. echo guard — anything decoded off CAN never goes back to CAN.
            if bus_msg.source == CAN_SOURCE:
                self.echoes += 1
                return

            # 2. LEARN. Unconditional, and before the forwarding decision, so
            #    that a namespace this bridge refuses to FORWARD is still
            #    NAMEABLE when a CAN node sends it up. Learning is not consent.
            outcome = observe_ns(bus_msg.namespace)
            if outcome == OBSERVE_NEW:
                self.observed += 1

            # 3. GOVERN, separately.
            reason = self._forwarding_decision(bus_msg)
            if reason is not None:
                self.count_drop(bus_msg.namespace, reason)
                if reason in (DROP_DENIED, DROP_COLLISION):
                    self.filtered += 1
                return

            self._forward_to_can(bus_msg)
        except Exception as e:
            logger.error(f"MQTT message error: {e}")

    # -- the forwarding table, published ------------------------------------ #

    def _start_status_thread(self):
        if STATUS_PERIOD_S <= 0:
            logger.warning("CAN_BRIDGE_STATUS_S=%.1f — forwarding table will "
                           "NOT be published. The bridge is now unobservable "
                           "from the bus.", STATUS_PERIOD_S)
            return
        self._status_thread = threading.Thread(
            target=self._status_loop, name="bridge-status", daemon=True)
        self._status_thread.start()

    def _status_loop(self):
        # PERIODIC, not on-change. A table published only when it changes is
        # indistinguishable from a dead bridge; here, a MISSING publication is
        # itself the signal.
        while not self._status_stop.wait(STATUS_PERIOD_S):
            try:
                self.publish_status()
            except Exception as e:
                logger.error("status publication failed: %r", e)

    def status_blobs(self) -> dict[str, dict]:
        """The three published documents. Split out so a test can read them
        without an MQTT broker."""
        return {
            NS_DENYLIST: {                       # INTENT
                "denylist": list(self._denylist),
                "allowlist_deprecated": sorted(self._allowlist)
                                        if self._allowlist is not None else None,
                "self_prefix_excluded_by_construction": SELF_NS_PREFIX,
                "interface": CAN_INTERFACE,
                "period_s": STATUS_PERIOD_S,
            },
            NS_DROPS: {                          # REALITY
                "by_reason": self.drop_totals(),
                "by_namespace": {ns: dict(per) for ns, per in self.drops.items()},
                "collisions": {new: {"held_by": old,
                                     "hash": f"0x{namespace_hash(new):04X}"}
                               for new, old in _collisions.items()},
                "unresolvable_hashes": {f"0x{h:04X}": n
                                        for h, n in _unresolved_hashes.items()},
                # THE GUARD'S OWN REPAIR RECORD. `unresolvable_hashes` shrinking
                # to nothing after an announcement would otherwise be
                # indistinguishable from a bridge that never had a gap, and the
                # frames it cost would be gone from the report entirely.
                "resolved_after_announce": {
                    f"0x{h:04X}": {"name": n, "frames_dropped_before": c}
                    for h, (n, c) in _resolved_after_announce.items()},
                "enobufs": self.enobufs,
            },
            NS_FORWARDING: {                     # what actually crossed
                "name_to_hash": {ns: f"0x{h:04X}"
                                 for ns, h in sorted(self.forwarded.items())},
                "registered": len(_name_to_hash),
                "learned_from_mqtt": self.observed,
                # THE WHOLE TABLE, WITH PROVENANCE — the audit channel [PLAN G4].
                # Not only what was forwarded: a name learned from a CAN
                # announcement may never be forwarded MQTT->CAN at all (the
                # traffic goes the other way), so a table keyed on "what
                # crossed" would be structurally unable to show the thing this
                # workstream added. Each entry says where its mapping came from,
                # so a row restored from a file cannot masquerade as an
                # observation made this run.
                "registry": {ns: {"hash": f"0x{h:04X}",
                                  "via": _provenance.get(ns, "?")}
                             for ns, h in sorted(_name_to_hash.items())},
                "learned_from_can": sum(1 for p in _provenance.values()
                                        if p == PROV_CAN),
                "announcements": _announce_learner.census(),
                "announce_root": {"ns": NS_ANNOUNCE,
                                  "hash": f"0x{NS_ANNOUNCE_HASH:04X}",
                                  "poll": NS_ANNOUNCE_POLL,
                                  "polls_sent": self.announce_polls},
                "persistence": {
                    "file": REGISTRY_FILE,          # None == OFF, said plainly
                    "restored": _registry_loaded,
                    "saves": _registry_saves,
                    "save_errors": _registry_save_errors,
                    "refused_on_load": list(_registry_problems),
                },
                "up_can_to_mqtt": self.up,
                "down_mqtt_to_can": self.down,
                "echoes_dropped": self.echoes,
                "uptime_s": round(time.time() - self._started_at, 1),
                "client_id": MQTT_CLIENT_ID,
            },
        }

    def publish_status(self) -> int:
        if not self._mqtt_client:
            return 0
        n = 0
        for ns, body in self.status_blobs().items():
            msg = BusMessage(namespace=ns, type=MessageType.STATE, value=body,
                             source=MQTT_CLIENT_ID, scope="global")
            self._mqtt_client.publish(f"{MQTT_PREFIX}/global/{ns}",
                                      json.dumps(msg.to_json()),
                                      qos=0, retain=False)
            n += 1
        self.status_publications += 1
        return n


def main():
    denylist = load_denylist()
    allowlist = load_allowlist()

    if allowlist_is_set():
        logger.warning(
            "CAN_BRIDGE_ALLOWLIST is DEPRECATED — use CAN_BRIDGE_DENYLIST. It "
            "is still HONORED here (silently ignoring a filter a deployment "
            "relies on would put the whole MQTT bus onto a 500 kbit segment), "
            "but the allowlist model hides its own failures: a renamed "
            "namespace simply stops crossing and nothing says so. A deny list "
            "plus the per-namespace drop census on %s reports both what was "
            "refused and why.", NS_DROPS)

    # Register the namespaces that can NEVER be learned from MQTT observation:
    # the CAN-only vocabulary. Everything the bridge sees on MQTT registers
    # itself (`observe_ns`), in BOTH directions, which is the whole reason
    # CAN_BRIDGE_REGISTER shrank from "the mechanism" to "the fallback".
    for ns in BUILTIN_NAMESPACES:
        register_ns(ns, PROV_BUILTIN)
    for ns in parse_namespace_list(os.environ.get("CAN_BRIDGE_REGISTER")):
        register_ns(ns, PROV_ENV)
    for ns in (allowlist or []):
        register_ns(ns, PROV_ENV)
    logger.info("pre-registered %d namespace hash(es) (well-known root + "
                "CAN-only vocabulary + CAN_BRIDGE_REGISTER); every namespace "
                "seen on MQTT registers itself, and every namespace ANNOUNCED "
                "on %s registers itself too: %s", len(_name_to_hash),
                NS_ANNOUNCE,
                ", ".join(f"{n}=0x{h:04X}[{_provenance.get(n, '?')}]"
                          for n, h in sorted(_name_to_hash.items())))
    # THE ONE ENTRY THAT IS NOT CONFIGURATION, said out loud at every startup.
    logger.info(
        "well-known announcement root: %s = 0x%04X, poll %s = 0x%04X. These "
        "are the ONLY hardcoded name->hash pairs in the discovery path; "
        "everything else is learned. `_device/<id>/*` needs NO hand-typed "
        "CAN_BRIDGE_REGISTER entry — a prefix rule cannot work here, because "
        "the hash is over the FULL name including the device id, which is the "
        "thing being discovered.", NS_ANNOUNCE, NS_ANNOUNCE_HASH,
        NS_ANNOUNCE_POLL, namespace_hash(NS_ANNOUNCE_POLL))

    bridge = CANMQTTBridge(allowlist=allowlist, denylist=denylist)
    bridge.start()

    def shutdown(sig, frame):
        logger.info("Shutting down")
        bridge.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    bridge.run()


if __name__ == "__main__":
    main()
