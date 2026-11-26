#!/usr/bin/env python3
"""fleet_roster — THE roster computation ([SPEC:CXC-FLEET] [CXC-FC-S3-ROSTER], FC-C10).

EXACTLY ONE subscriber to `compose_projects/intelligence/+/channel/alive` applies a liveness timeout and publishes
`fleet/roster` = `[{id, role, pid, instance, last_seen, hosted}]`.

WHY ONE, AND WHY THIS IS NOT A PER-PANE CONCERN.  If each pane did its own wildcard discovery
that would be fork-divergence BY CONSTRUCTION: N implementations of one liveness rule, guaranteed
to disagree under load, and the disagreement would show up as panes flickering in and out of
"alive" independently.  One computation, one topic, N readers ([CXC-FC-ANTI]).

THE DIVIDEND.  The heartbeat already carries `instance` — the session uuid, which is the key to
that agent's transcript (`compose_projects/intelligence/channel/channel.mjs:243-245` publishes
`{ts, id, pid, start, instance, delivery, unacked}`).  So a HOSTED CHILD and an INDEPENDENTLY
LAUNCHED PEER become THE SAME CODE PATH: the console does not need to have spawned something in
order to instrument it, which is what actually closes [INSIGHT-SELF-IS-NOT-YET-A-PEER].

⚠ MEASURED, NOT ASSUMED (2026-08-12, on the live broker at mqtt-broker.local:1883):

    intelligence/architect/channel/alive ->
      {"ts":1786574117334,"id":"architect","pid":82007,"start":1786507054623,
       "instance":"1cb3c08b-...","delivery":"ok","unacked":1}

REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): this source NEEDS repeats on the way IN and DEDUPS on the
way OUT, and the two halves are load-bearing in opposite directions.

  * INBOUND it uses `subscribe_foreign`, which delivers EVERY frame — no state engine, no
    `new != old` comparison.  That is required: a heartbeat's whole meaning is the PULSE, and an
    identical heartbeat arriving again is the signal, not a duplicate.  `bus.subscribe` would
    have delivered the first beat and then gone silent forever, and the roster would show every
    instance as alive-then-dead-then-never-again.
  * OUTBOUND it publishes `fleet/roster` only when the computed roster CHANGES (plus a periodic
    expiry tick, because silence has no event to fire on).  The roster is id-SORTED for exactly
    this reason: an unsorted list would appear to change on every beat and every reader would
    re-render for nothing.

`role` and `hosted` are NOT on the heartbeat.  This source therefore RESOLVES `role` through the
manifest chain the id already resolves through, and DERIVES `hosted` from the host's own declared
roster.  When either is unresolvable it publishes the literal string `"unknown"` rather than a
guess — a guessed role keys nothing (and would mis-key a memory store under [CXC-FC-PROJECT-DIR]),
and a guessed `hosted` flag mislabels an independent peer as somebody's child.

Run:  python3 compose_projects/intelligence/publisher/fleet_roster.py --mqtt mqtt://mqtt-broker.local:1883 \
          --hosted architect-hosted --hosted architect
      python3 compose_projects/intelligence/publisher/fleet_roster.py --selftest
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Callable, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
# _HERE is <repo>/compose_projects/intelligence/publisher — THREE up, not two
# (2026-08-17 reorg; a depth count carries no path text, so the stale two kept
#  resolving and named <repo>/compose_projects as the root).
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _d in (os.path.join(_REPO, "python"), _HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "fleet-roster"                     # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
ROSTER_NS = "fleet/roster"
# THE ONE wildcard. A second spelling of this filter anywhere is a second discovery path, which
# FC-C15 guards machine-checkably.
ALIVE_FILTER = "compose_projects/intelligence/+/channel/alive"
DEFAULT_LIVENESS_S = 45.0                # channel.mjs heartbeats well inside this
DEFAULT_PUBLISH_S = 5.0
UNKNOWN = "unknown"


# ── role resolution, through the chain the id ALREADY resolves through ───────────────────────
def resolve_role(intelligence_id: str) -> str:
    """The ROLE for `intelligence_id`, or `"unknown"`.

    Uses `ptycore.resolve_meta_manifest` — the SAME chain a spawn resolves
    ([CXC-TERM-MANIFEST-CHAIN]) — and then takes the BASENAME OF THE ROLE DESCRIPTOR, which is
    the ratified rule for what a role is NAMED ([CXC-FC-ROLE-MEMORY]).  `architect.meta.json`
    carries `"role": "ComposeUX / ComposeIO Architect"`, a human-readable TITLE; the store name is
    `composeux-architect`, from `role_descriptor.path`.  Reading the title would produce a role
    that keys nothing.

    Never guesses from the id by string surgery: `architect-hosted` does not mean "role architect"
    by convention ([CXC-FC-THREE-AXES] prohibition) — it means whatever its declaration says.
    """
    try:
        from composeio.ptycore import load_manifest, resolve_meta_manifest
    except Exception:
        return UNKNOWN
    try:
        path, _tried = resolve_meta_manifest(intelligence_id)
        if not path:
            return UNKNOWN
        manifest = load_manifest(path)
    except Exception:
        return UNKNOWN
    descriptor = (manifest.get("role_descriptor") or {})
    rel = str(descriptor.get("path") or "").strip()
    if rel:
        base = os.path.basename(rel)
        if base.endswith(".md"):
            return base[:-3]
    return UNKNOWN


class RosterComputation:
    """The pure, testable half: fold heartbeats in, read a roster out.

    Kept separate from the bus wiring so the liveness rule can be asserted without a broker —
    the rule is the thing that must not fork, and a rule only reachable through a live transport
    is a rule only reachable through a live transport.
    """

    def __init__(self, *, liveness_s: float = DEFAULT_LIVENESS_S,
                 hosted_ids: Optional[List[str]] = None,
                 declared_roles: Optional[Dict[str, str]] = None,
                 role_resolver: Optional[Callable[[str], str]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.liveness_s = float(liveness_s)
        # None (never declared) is NOT the same as an empty list: with no declared roster we
        # cannot know whether a peer is hosted, so `hosted` publishes as "unknown".
        self.hosted_ids = None if hosted_ids is None else set(hosted_ids)
        # The scene's DECLARED `(instance_id, role)` pairs ([CXC-FC-UNIT]).  These win over the
        # manifest chain, and that is not a shortcut: the pane DECLARES the pair, so for a
        # declared instance the role is known rather than inferred.  The chain remains the
        # answer for a peer nobody declared — measured 2026-08-12, `architect-hosted` has no
        # per-id manifest (chain leg 2 MISSES for every child that is not hand-curated, which is
        # the whole reason leg 3 exists), so without this it would publish `unknown` for an
        # instance whose role the scene is holding in its hand.
        self.declared_roles = dict(declared_roles or {})
        self._role_of = role_resolver or resolve_role
        self._clock = clock
        self._beats: Dict[str, dict] = {}
        self._roles: Dict[str, str] = {}      # memoised — the chain touches the filesystem
        self._lock = threading.Lock()

    @staticmethod
    def id_from_topic(topic: str) -> str:
        """`compose_projects/intelligence/<id>/channel/alive` -> `<id>` ("" when it is not that shape)."""
        parts = str(topic or "").split("/")
        if len(parts) == 4 and parts[0] == "intelligence" and parts[2:] == ["channel", "alive"]:
            return parts[1]
        return ""

    def ingest(self, topic: str, payload) -> bool:
        """Fold one heartbeat in. Returns True when the roster's CONTENT changed."""
        iid = self.id_from_topic(topic)
        if not iid:
            return False
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = json.loads(payload.decode())
            except (ValueError, UnicodeDecodeError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        # The heartbeat's own `id` is authoritative when present, but the TOPIC is what we key
        # on: a payload claiming another id must not be able to rename a topic's owner.
        beat = {"pid": payload.get("pid"), "instance": payload.get("instance"),
                "start": payload.get("start"), "delivery": payload.get("delivery"),
                "unacked": payload.get("unacked"), "last_seen": self._clock()}
        with self._lock:
            prev = self._beats.get(iid)
            self._beats[iid] = beat
            changed = prev is None or any(
                prev.get(k) != beat.get(k)
                for k in ("pid", "instance", "start", "delivery"))
        return changed

    def _role_for(self, iid: str) -> str:
        declared = self.declared_roles.get(iid)
        if declared:
            return declared
        with self._lock:
            cached = self._roles.get(iid)
        if cached is not None:
            return cached
        role = self._role_of(iid) or UNKNOWN
        with self._lock:
            self._roles[iid] = role
        return role

    def _hosted_for(self, iid: str):
        if self.hosted_ids is None:
            return UNKNOWN
        return iid in self.hosted_ids

    def roster(self) -> List[dict]:
        """`[{id, role, pid, instance, last_seen, hosted}]` for every LIVE instance, id-sorted.

        Sorted so two consecutive publishes of the same fleet are byte-identical — otherwise the
        roster would appear to change on every tick and every reader would re-render for nothing.
        """
        now = self._clock()
        with self._lock:
            live = {i: b for i, b in self._beats.items()
                    if now - float(b.get("last_seen") or 0) <= self.liveness_s}
        out = []
        for iid in sorted(live):
            b = live[iid]
            out.append({"id": iid,
                        "role": self._role_for(iid),
                        "pid": b.get("pid"),
                        "instance": b.get("instance"),
                        "last_seen": b.get("last_seen"),
                        "hosted": self._hosted_for(iid)})
        return out


def run(mqtt_url: str, *, hosted_ids: Optional[List[str]] = None,
        declared_roles: Optional[Dict[str, str]] = None,
        liveness_s: float = DEFAULT_LIVENESS_S,
        publish_s: float = DEFAULT_PUBLISH_S,
        bus=None, stop=None) -> int:
    computation = RosterComputation(liveness_s=liveness_s, hosted_ids=hosted_ids,
                                    declared_roles=declared_roles)
    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    source_bus.declare(b, ROSTER_NS, scope=Scope.GLOBAL, source=SRC,
                       label="Fleet roster (live Intelligences)")
    source_bus.manifest(SRC)

    last: List[dict] = []

    def _publish(force: bool = False) -> None:
        nonlocal last
        current = computation.roster()
        if force or current != last:
            last = current
            b.publish_state(ROSTER_NS, current, source=SRC)

    def _on_alive(topic: str, payload) -> None:
        # Republish on CONTENT change only; the periodic tick below covers expiry, which no
        # heartbeat can announce (a dead instance's silence is the signal).
        if computation.ingest(topic, payload):
            _publish()

    # THE ONE wildcard subscription ([CXC-FC-S3-ROSTER]). `subscribe_foreign` because the channel
    # topics are a PEER PROTOCOL, not ComposeBus namespaces — the same reason `channel_bridge`
    # uses it, reused by reference rather than re-derived.
    b.subscribe_foreign(ALIVE_FILTER, _on_alive)
    sys.stderr.write(f"[{SRC}] discovery {ALIVE_FILTER} -> {ROSTER_NS} "
                     f"(liveness {liveness_s}s, hosted={sorted(hosted_ids) if hosted_ids else hosted_ids})\n")
    _publish(force=True)

    # [CXC-SOB-RUN-BLOCKS]: `run` must BLOCK (a clean return makes the supervisor re-invoke
    # this source every 5 s) AND must join promptly on `stop` (a source that blocks forever and
    # cannot be stopped hangs console shutdown and leaves an orphan holding its flock across a
    # bounce).  `stop.wait(publish_s)` is both: it is the expiry tick AND the stop check, so the
    # loop never sleeps past a stop.
    while True:
        if stop is not None:
            if stop.wait(publish_s):
                break
        else:
            time.sleep(publish_s)
        _publish()          # expiry tick: SILENCE is what removes an entry, and silence has
                            # no event to fire on — only a clock can notice it
    return 0


def main(argv: list, *, bus=None, stop=None) -> int:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if "--selftest" in argv:
        return _selftest()
    mqtt_url = opt("--mqtt", os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    # `--instance <id>:<role>` is the DECLARED pair ([CXC-FC-UNIT]); repeatable, one per pane.
    # An id given without a role still counts as hosted — the console declared it — but its role
    # falls through to the manifest chain.
    hosted: List[str] = []
    declared: Dict[str, str] = {}
    for i, a in enumerate(argv):
        if a in ("--hosted", "--instance") and i + 1 < len(argv):
            iid, _, role = argv[i + 1].partition(":")
            if iid:
                hosted.append(iid)
                if role:
                    declared[iid] = role
    return run(mqtt_url, hosted_ids=hosted or None, declared_roles=declared or None,
               liveness_s=float(opt("--liveness", DEFAULT_LIVENESS_S)),
               bus=bus, stop=stop)


# ── selftest (no broker) ─────────────────────────────────────────────────────────────────────
def _selftest() -> int:
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    t = [1000.0]
    r = RosterComputation(liveness_s=30.0, hosted_ids=["architect-hosted"],
                          role_resolver=lambda i: "composeux-architect"
                          if i.startswith("architect") else UNKNOWN,
                          clock=lambda: t[0])

    beat = json.dumps({"ts": 1, "id": "architect", "pid": 82007, "start": 5,
                       "instance": "1cb3c08b", "delivery": "ok", "unacked": 1}).encode()
    check(r.ingest("intelligence/architect/channel/alive", beat) is True,
          "(a) first heartbeat must register as a change")
    check(r.ingest("intelligence/architect/channel/alive", beat) is False,
          "(a) an identical heartbeat must NOT churn the roster")
    check(r.ingest("intelligence/architect/out", beat) is False,
          "(a) a non-heartbeat topic must be ignored")

    r.ingest("intelligence/architect-hosted/channel/alive",
             json.dumps({"pid": 2, "instance": "beta"}).encode())
    roster = r.roster()
    check([e["id"] for e in roster] == ["architect", "architect-hosted"],
          f"(b) roster must be id-sorted: {roster}")
    check(roster[0]["hosted"] is False and roster[1]["hosted"] is True,
          f"(b) hosted must come from the DECLARED roster, not the id: {roster}")
    check(roster[0]["instance"] == "1cb3c08b",
          "(b) the session uuid must survive — it is the key to that agent's transcript")

    # liveness: silence expires an entry; nothing else does.
    t[0] += 31.0
    check(r.roster() == [], "(c) a silent instance must age out of the roster")

    # unresolvable role / unknown hosted are EXPLICIT, never guessed.
    r2 = RosterComputation(role_resolver=lambda i: UNKNOWN, clock=lambda: t[0])
    r2.ingest("intelligence/mystery/channel/alive", b"{}")
    e = r2.roster()[0]
    check(e["role"] == UNKNOWN, f"(d) an unresolvable role must publish 'unknown': {e}")
    check(e["hosted"] == UNKNOWN,
          f"(d) with NO declared roster, hosted must be 'unknown', not False: {e}")

    # an independently-launched PEER (never spawned by the console) is an ordinary entry.
    r3 = RosterComputation(hosted_ids=["architect-hosted"], role_resolver=lambda i: "r",
                           clock=lambda: t[0])
    r3.ingest("intelligence/somebody-elses-agent/channel/alive", b'{"pid": 9}')
    check(r3.roster()[0]["id"] == "somebody-elses-agent" and r3.roster()[0]["hosted"] is False,
          "(e) a peer the console never spawned must still appear in the roster")

    # a DECLARED role wins over the chain — measured: `architect-hosted` has no per-id manifest,
    # so the chain says "unknown" for an instance whose role the scene declared.
    r4 = RosterComputation(hosted_ids=["architect-hosted"],
                           declared_roles={"architect-hosted": "composeux-architect"},
                           role_resolver=lambda i: UNKNOWN, clock=lambda: t[0])
    r4.ingest("intelligence/architect-hosted/channel/alive", b'{"pid": 3}')
    check(r4.roster()[0]["role"] == "composeux-architect",
          f"(g) a DECLARED (id, role) pair must beat an unresolvable chain: {r4.roster()}")

    check(RosterComputation.id_from_topic("intelligence/x/channel/alive") == "x",
          "(f) topic parse")
    check(RosterComputation.id_from_topic("intelligence/x/out") == "", "(f) topic parse (neg)")

    if fails:
        sys.stderr.write(f"\n{len(fails)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: fleet_roster selftest\n"
                     "  (a) heartbeat fold + change detection (no churn on an identical beat)\n"
                     "  (b) roster shape: id-sorted, hosted from the DECLARED roster, uuid kept\n"
                     "  (c) liveness timeout ages a silent instance out\n"
                     "  (d) unresolvable role/hosted publish an EXPLICIT 'unknown'\n"
                     "  (e) an independently-launched peer is an ordinary entry\n"
                     "  (f) topic parsing\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
