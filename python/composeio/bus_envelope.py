#!/usr/bin/env python3
"""bus_envelope — the ONE place a ComposeIO peer frames a STATE wire envelope ([DX-NARROW-WAIST], reuse-by-reference-never-fork).

WHY THIS EXISTS (2026-08-08, Phase 2a step 1 of the bus-connection collapse):
**16** files under `compose_projects/intelligence/` each hand-rolled the same
`{ns, type, src, ts, scope, seq, val}` dict and published it with raw paho. That
is 16 copies of one contract, and it had ALREADY DRIFTED: 14 emitted `seq`,
while `lts_engine_status` and `config_persister` did not. An envelope change
would have had to land 16 times, and a fix to one copy leaving the others stale
is the single most common defect class in this repo
([DEC-FORK-DIVERGENCE-DOMINANT-CLASS-PROPOSE-SEAM-LINT]).

THE CANONICAL SHAPE WAS NEVER MISSING. `composeio.transport.BusMessage.to_json()`
emits exactly those fields in exactly that order. The hand-framing bypassed it.

WHY IT WAS BYPASSED, and why that reason does not apply here: per
[DEC-BUS-ENVELOPE-RAW-PAHO] (2026-07-19), `ComposeIOBus.publish_state` auto-
registers an unknown namespace with a BINARY state engine that then rejects the
real payload (a float, a dict). True, and STILL true today. But that is the
**bus API**; `BusMessage.to_json()` is *pure serialization* and never touches a
state engine. The workaround was broader than the problem it solved, and this
module is the narrow version: reuse the canonical envelope, keep raw paho for
transport (which is what [DEC-BUS-ENVELOPE-RAW-PAHO] actually needed).

WIRE COMPATIBILITY: byte-identical for the 14 sites that already emitted `seq`.
The two that did not now gain `"seq": 0` — which the decoder already defaults to
(`BusMessage.from_json` does `d.get("seq", 0)`), so no consumer can tell the
difference except that the wire is now consistent.

`compose_projects/intelligence/launcher/launcher.py` is DELIBERATELY EXEMPT: it hand-frames to keep
a paho-only, composeio-import-free startup footprint. That is a real constraint,
not an oversight — do not "fix" it. Its `publish_state` carries a pointer here.

NOT A NEW MODEL. If the envelope ever grows runtime validation, the right move is
to convert `BusMessage` ITSELF (a pydantic model in `composeio.transport`), not to
add a second envelope type here — two models for one envelope is the same fork at
a higher altitude. See the Phase 2a strategy doc, step 1 option B.
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Optional

from composeio.transport import (BusMessage, MessageType,
                                 UnknownMessageTypeName, message_type_for_name)

DEFAULT_SCOPE = "global"

__all__ = ["DEFAULT_SCOPE", "envelope", "publish", "state_envelope",
           "state_topic", "publish_state", "UnknownMessageTypeName"]


def envelope(ns: str, val: Any, src: str, *,
             mtype: "str | MessageType" = MessageType.STATE,
             seq: int = 0,
             scope: str = DEFAULT_SCOPE,
             ts: Optional[float] = None) -> dict:
    """The wire envelope for ANY allocated message type — THE PUBLISHER GATE.

    ⛔ THE GUARD IS THE REASON THIS FUNCTION EXISTS.  On 2026-08-15 six devices
    were sent a restart carrying ``"type": "command"``, hand-framed straight
    into `mosquitto_pub` because no publisher existed to frame it correctly.
    `command` is not one of the ten values [BP-TYPE-CODES] allocates, so every
    receiver refused it — the CAN bridge counted 9 `encode_error` drops, and
    `cyd/src/bus_mqtt.cpp` rejected it identically on the firmware tier.  The
    restart was a NO-OP on every device on every substrate and NOTHING TOLD THE
    PUBLISHER.

    `message_type_for_name` refuses HERE, before a byte is written, and its
    refusal explains that `command` is real ComposeBus PROSE (state vs command)
    that was never allocated a wire code — because a reader who is only told
    "invalid MessageType" reaches for the one fix that must not be made.
    """
    return BusMessage(
        namespace=ns,
        type=message_type_for_name(mtype),
        value=val,
        source=src,
        timestamp=time.time() if ts is None else ts,
        scope=scope,
        sequence=seq,
    ).to_json()


def publish(client, ns: str, val: Any, src: str, *,
            mtype: "str | MessageType" = MessageType.STATE,
            seq: int = 0,
            scope: str = DEFAULT_SCOPE,
            qos: int = 1,
            retain: bool = False,
            ts: Optional[float] = None) -> None:
    """Frame + publish one message of ANY allocated type on `client`.

    REFUSAL WRITES NOTHING: the envelope is built (and therefore validated)
    before `client.publish` is reached, so a rejected type cannot leave a
    half-published wire behind the exception that claims it avoided one.
    """
    payload = json.dumps(envelope(ns, val, src, mtype=mtype, seq=seq,
                                  scope=scope, ts=ts))
    client.publish(state_topic(ns, scope), payload, qos=qos, retain=retain)


def state_envelope(ns: str, val: Any, src: str, *,
                   seq: int = 0,
                   scope: str = DEFAULT_SCOPE,
                   ts: Optional[float] = None) -> dict:
    """The ComposeBus STATE wire envelope for `ns` -> `val`, as a JSON-ready dict.

    Delegates to the canonical `BusMessage.to_json()` so this module holds NO copy
    of the field set or their order — it only supplies the STATE-message defaults
    the intelligence data sources share.

    `ts` defaults to now; pass it explicitly only to make a test deterministic.

    Now a thin STATE-typed call of `envelope()` above — one framing path, so the
    publisher gate cannot be bypassed by picking the older helper.
    """
    return envelope(ns, val, src, mtype=MessageType.STATE, seq=seq,
                    scope=scope, ts=ts)


def state_topic(ns: str, scope: str = DEFAULT_SCOPE) -> str:
    """`composeio/{scope}/{ns}` — the topic the render engine subscribes."""
    return f"composeio/{scope}/{ns}"


def publish_state(client, ns: str, val: Any, src: str, *,
                  seq: int = 0,
                  scope: str = DEFAULT_SCOPE,
                  qos: int = 1,
                  retain: bool = False,
                  ts: Optional[float] = None) -> None:
    """Frame + publish one STATE message on `client` (a connected paho client).

    Kept deliberately thin: callers keep owning their own client, their own
    reconnect policy, and their own qos choice — this de-forks the ENVELOPE, not
    the transport. Collapsing the 20 per-source clients into one is a SEPARATE
    step (Phase 2a step 2) and must not be smuggled in here.
    """
    client.publish(state_topic(ns, scope),
                   json.dumps(state_envelope(ns, val, src, seq=seq, scope=scope, ts=ts)),
                   qos=qos, retain=retain)


def _selftest() -> int:
    """Pin the wire shape. These assertions ARE the contract the 15 converted
    sites depend on; if the canonical envelope ever changes shape, this fails
    here rather than silently on the glass."""
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    env = state_envelope("self/x", {"a": 1}, "self", seq=7, ts=1.5)
    # (1) exact field set AND order — order matters because the pre-de-fork sites
    #     emitted these keys in this sequence, and json.dumps preserves it.
    check(list(env.keys()) == ["ns", "type", "src", "ts", "scope", "seq", "val"],
          f"(1) wire key order changed: {list(env.keys())}")
    check(env == {"ns": "self/x", "type": "state", "src": "self", "ts": 1.5,
                  "scope": "global", "seq": 7, "val": {"a": 1}},
          f"(2) envelope content wrong: {env}")
    # (3) byte-identical to the literal the converted sites used to hand-roll.
    legacy = {"ns": "self/x", "type": "state", "src": "self", "ts": 1.5,
              "scope": "global", "seq": 7, "val": {"a": 1}}
    check(json.dumps(env) == json.dumps(legacy),
          "(3) serialized bytes differ from the pre-de-fork literal")
    # (4) the seq-less sites gain seq=0, which from_json already defaults to.
    e0 = state_envelope("self/y", 1, "cfg", ts=2.0)
    check(e0["seq"] == 0, f"(4) default seq should be 0: {e0}")
    check(BusMessage.from_json(e0).sequence == 0, "(4b) decoder round-trips seq=0")
    # (5) topic mapping.
    check(state_topic("self/z") == "composeio/global/self/z",
          f"(5) topic wrong: {state_topic('self/z')}")
    check(state_topic("a/b", "local") == "composeio/local/a/b", "(5b) scope honored")
    # (6) round-trip through the canonical decoder preserves the payload.
    back = BusMessage.from_json(env)
    check(back.namespace == "self/x" and back.value == {"a": 1}
          and back.source == "self" and back.scope == "global",
          f"(6) round-trip lost data: {back}")
    # (7) publish_state hands the right topic + qos to the client.
    sent = []

    class _C:
        def publish(self, topic, payload, qos=0, retain=False):
            sent.append((topic, payload, qos, retain))

    publish_state(_C(), "self/w", 5, "src", qos=1, ts=3.0)
    check(sent and sent[0][0] == "composeio/global/self/w", f"(7) topic: {sent}")
    check(sent and json.loads(sent[0][1])["val"] == 5, f"(7b) payload: {sent}")
    check(sent and sent[0][2] == 1, f"(7c) qos not forwarded: {sent}")

    if fails:
        for m in fails:
            sys.stderr.write(f"FAIL {m}\n")
        return 1
    print("PASS: bus_envelope selftest (7/7 checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
