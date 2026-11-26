#!/usr/bin/env python3
"""channel_bridge — republish the raw intelligence channel topics onto the render engine's bus.

The Intelligence channel (channel.mjs + chat.py) publishes on RAW MQTT topics
`compose_projects/intelligence/<id>/{in,out}` (David's messages, the Intelligence's replies, harness messages).
The ComposeUX render engine, however, subscribes on the ComposeBus scheme
`composeio/{scope}/{namespace}` with a BusMessage payload envelope. So the console's chat_thread
widget — bound to `compose_projects/intelligence/<id>/out` and `compose_projects/intelligence/<id>/in` — never sees them, and only
the daemon-published `thread/narration` voice appears. (The first live glass test caught this: my
narration streamed into the console, but David's messages + harness did not.)

This bridge subscribes the raw channel topics and re-publishes each onto
`composeio/global/<same-namespace>` wrapped in the BusMessage.to_json envelope, so the console's
`composeio/+/<ns>` subscription receives them and the chat_thread renders david + harness + the
architect's deliberate replies alongside the narration — the full four voices.

Read-only on the channel (it only re-publishes to a DIFFERENT topic); no loop (it never publishes
to a topic it subscribes to). Standalone; no relaunch.

CONVERTED TO THE COMPOSEBUS WAIST 2026-08-11 ([SPEC:CXC-SOURCES-ON-BUS] slice 7,
[CXC-SOB-CHANNEL-LAST]). It no longer imports paho, builds an `mqtt.Client`, composes a
`composeio/...` topic, or runs `loop_forever`. What it does instead, on each of its two legs:

  DISPLAY (raw channel -> the bus). `bus.subscribe_foreign("intelligence/<id>/in|out|
  channel/alive", cb)` — the channel topics are a PEER PROTOCOL, not ComposeBus namespaces, so
  they arrive through the waist's foreign-channel seam with their payload UNDECODED (this file
  has always had to survive a non-JSON payload, and a bus that pre-parsed would destroy that
  distinction). Each is then re-published with `bus.publish_state(ns, val, source="channel-bridge")`,
  which produces the SAME envelope this file used to hand-build — `_echo_envelope` is retained as
  the pinned reference for that claim, and a selftest asserts key-for-key parity against the
  canonical `BusMessage.to_json()`.

  SEND (the composer -> raw `/in`). The compose namespace is an ordinary bus namespace, so it is a
  plain `bus.subscribe`. The OUTBOUND hop is foreign again — `compose_projects/intelligence/<id>/in` carries a BARE
  `{from,text}` payload with no envelope, which `publish_state` cannot express — so it goes through
  `bus.publish_foreign`, byte-identical to before.

WHY THAT MATTERS BEYOND TIDINESS: on a CAN-only or UART-only deployment this bridge used to be
DARK with no error at all — the exact failure [CXC-SOB-THESIS] exists to remove, on the one
channel whose failure mode is silence. Through the waist, a transport that cannot carry a foreign
channel says so, loudly, at the call.

REPEAT DELIVERY: REQUIRED ON THE SEND LEG, AND IT IS A REAL HAZARD THIS CONVERSION HAD TO CLOSE
([CXC-SOB-SUBSCRIBE]). `TextInputWidget.commit` publishes the BARE COMMITTED STRING to
`compose_projects/intelligence/<id>/compose` — no timestamp, no nonce (`text_input.py:735-765`, verified, not
assumed). `Namespace.publish_state` notifies EXACT subscribers only when `new != old`. So a
`bus.subscribe(compose_ns, ...)` would have delivered the first "ok" David types and SILENTLY
SWALLOWED every identical one after it: the message never reaches the Intelligence, nothing
errors, and the thread shows no bubble. That is [CXC-SOB-CHANNEL-LAST]'s stated failure mode —
silence — manufactured by the conversion itself. The send leg therefore uses
`subscribe_pattern("intelligence/<id>/*")`, whose subscribers `_notify_pattern_subs` fires
UNCONDITIONALLY on every publish, local and inbound alike; the handler filters to `compose_ns`
so the relay never re-injects its own republished `/in` echo.

The DISPLAY leg needs no such care, and the reason is worth stating because it is NOT "foreign
frames are special": (a) inbound, `subscribe_foreign` delivers EVERY frame — no state engine, no
`new != old` gate — so the relay sees repeats exactly as it did under paho; (b) outbound,
`bus.publish_state` sends to the transport UNCONDITIONALLY (only its LOCAL subscriber notification
is change-gated), so the console's copy on the wire is byte-for-byte what it was. The console then
applies the same `new != old` absorption to an identical repeated bubble that it always has, on
both the old path and the new — this conversion did not move that behaviour in either direction.

Run: python3 channel_bridge.py [--id architect] [--mqtt mqtt://host:port]
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# [SPEC:CXC-SOURCES-ON-BUS] slice 7 — the ONE bus seam.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402


# ── SINGLETON guarantee ([SPEC:CXC-IC-SINGLETON], née IC-C15) ─────────────────
# The defect: the relay has TWO launch paths (in-process on a console-owned
# supervisor thread, [DEC-INPROCESS-SOURCES]; and the start-console-daemons.sh
# headless fallback) and its MQTT client-id was PID-suffixed, so two instances
# COEXISTED on the broker instead of colliding. Each unwrapped the composer's
# /compose (David's message reached the Intelligence TWICE) AND echoed every raw
# topic (the thread rendered every message TWICE — same text, same ts). A "clean
# singletons" process sweep misses the in-process relay because it is a THREAD in
# the render runtime, not a channel_bridge.py process.
#
# The fix makes singleton-ness STRUCTURAL: an exclusive host-level lock per <id>.
# That flock now lives in ONE shared helper (``singleton.py``), reused BY
# REFERENCE by every console daemon — never re-hand-rolled per daemon (a copy in
# ~15 files is the fork-divergence [DEC-SUBAGENT-CHIP-FORK-DIVERGENCE] warns
# against). ``run()`` keys it ``channel-bridge-<id>`` so the lock file is
# ``composeio-channel-bridge-<id>.lock`` — byte-for-byte the IC-C15 filename —
# and also uses a stable, non-PID client-id (`channel-bridge-<id>`) as a
# broker-level backstop.
try:
    from singleton import (acquire_singleton, acquire_singleton_reclaiming,
                           singleton_lock_path)
except ImportError:                            # standalone from another cwd
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from singleton import (acquire_singleton, acquire_singleton_reclaiming,
                           singleton_lock_path)

# Back-compat aliases: the pre-de-fork private names, kept so existing importers
# (e.g. test_channel_bridge.py) resolve unchanged. Same behavior, one implementation.
_acquire_singleton = acquire_singleton
_singleton_lock_path = singleton_lock_path


def _build_raw_in(val) -> dict | None:
    """Map an unwrapped compose payload to the raw ``compose_projects/intelligence/<id>/in`` payload dict,
    or None to ignore (an empty commit with no image).

    Pure + side-effect-free so it is unit-testable without the daemon. A typed message is a
    string (or a dict carrying text/content/body/message) -> ``{"from":"david","text":<text>}``,
    BYTE-IDENTICAL to the original text-only bridge. A DROPPED image arrives as a dict carrying
    ``image`` (an absolute local path, ephemeral — never stored); the path is forwarded so the
    co-located Intelligence can Read it: ``{"from":"david","text":<text or "">,"image":<path>}``.
    """
    image = None
    if isinstance(val, dict):
        text = val.get("text") or val.get("content") or val.get("message") or ""
        img = val.get("image")
        image = str(img) if img else None
    else:
        text = val
    text = str(text).strip()
    if not text and not image:
        return None
    out = {"from": "david", "text": text}
    if image:
        out["image"] = image
    return out


def _raw_topics(intelligence_id: str) -> list[str]:
    """The raw channel topics this bridge relays onto the composeio scheme.

    The first two (``/in``, ``/out``) are the PROVEN display path — David's
    messages, the Intelligence's replies + harness (BYTE-IDENTICAL to before).
    The third (``/channel/alive``) is the liveness HEARTBEAT
    ([SPEC:CXC-CONSOLE-VISUAL] link health): channel.mjs beats it every ~5s and
    the console's live-dot subscribes the relayed copy to tell "channel down"
    from "bus down".  All three flow through the SAME generic echo path below,
    so adding the heartbeat changed the relay SET, never the in/out handling.
    """
    return [
        f"intelligence/{intelligence_id}/in",
        f"intelligence/{intelligence_id}/out",
        f"intelligence/{intelligence_id}/channel/alive",
    ]


def _echo_envelope(ns: str, val, seq: int, ts: float) -> dict:
    """The BusMessage envelope a raw topic is re-published inside — a pure
    function so the relay is unit-testable without a broker.  BYTE-IDENTICAL
    to the original inline literal (same keys, same order): json.dumps of this
    for a ``/in`` or ``/out`` topic matches the pre-refactor bytes exactly.

    NO LONGER ON THE RUNTIME PATH after the waist conversion — `publish_state`
    frames the envelope now, through the canonical `BusMessage.to_json()`. It
    is deliberately KEPT as the pinned REFERENCE for [CXC-SOB-CONTRACT-FROZEN]:
    the selftest asserts the bus's envelope matches this dict key-for-key, so
    "the wire did not move" is an executable claim rather than a memory of
    what the old literal said. Delete it and that proof goes with it.
    """
    return {"ns": ns, "type": "state", "src": "channel-bridge",
            "ts": ts, "scope": "global", "seq": seq, "val": val}


def _decode_foreign(payload: bytes):
    """A raw channel payload -> JSON value, else the replace-decoded string.

    The relay has always had to survive a non-JSON payload on the channel; that
    is why the waist hands foreign frames over UNDECODED. Extracted from the
    old inline try/except so both legs use one implementation.
    """
    try:
        return json.loads(payload.decode())
    except (ValueError, UnicodeDecodeError):
        return payload.decode(errors="replace")


def run(intelligence_id: str, mqtt_url: str, reclaim_poll_s: float = 5.0,
        should_stop=None, *, bus=None, stop=None) -> int:
    # SINGLETON ([SPEC:CXC-IC-SINGLETON], née IC-C15): become THE relay for this
    # id, or YIELD. A second instance (in-process thread OR standalone) must not
    # re-inject David's message + double every thread bubble.
    # Key `channel-bridge-<id>` -> lock file composeio-channel-bridge-<id>.lock
    # (byte-for-byte the IC-C15 filename).
    #
    # RECLAIM-ON-RELEASE (IC-C27, [SPEC:CXC-IC-SINGLETON] publisher bounce-safety):
    # a one-shot yield-and-return went DARK FOREVER when this fresh console's relay
    # raced the DYING old console's relay across a /bounce-console — it lost the
    # boot race, returned, and never re-attempted, so when the predecessor died and
    # the flock FREED nothing reclaimed the (now-unowned) relay (the bounce-handoff
    # gap). So instead of returning we WAIT and RECLAIM: while another relay lives
    # we yield WITHOUT relaying (anti-doubling intact); the moment it releases (the
    # flock frees on its death) we acquire and relay. This is the yield-if-held
    # counterpart to the launcher's SUPERSEDE — WAIT for a healthy incumbent, never
    # kill it. Reuses the ONE acquire_singleton (no second flock call site).
    yielded = [False]
    def _log_yield():
        yielded[0] = True
        sys.stderr.write(f"[channel_bridge] another relay owns compose_projects/intelligence/"
                         f"{intelligence_id}; yielding — will RECLAIM when it "
                         f"releases (exactly-once relay)\n")
    lock = acquire_singleton_reclaiming(
        f"channel-bridge-{intelligence_id}", poll_s=reclaim_poll_s,
        should_stop=should_stop if should_stop is not None
        else (stop.is_set if stop is not None else None), on_yield=_log_yield)
    if lock is None:
        return 0                                   # stop signalled before reclaim
    if yielded[0]:
        sys.stderr.write(f"[channel_bridge] reclaimed compose_projects/intelligence/"
                         f"{intelligence_id} relay (predecessor released)\n")

    raw_channels = _raw_topics(intelligence_id)
    # ── SEND path (the console composer -> me) ───────────────────────────────────────────────
    # The console's TextInputWidget commits the typed message to a DEDICATED namespace
    # `compose_projects/intelligence/<id>/compose` (NOT `/in`). We unwrap it and re-publish to the RAW
    # `compose_projects/intelligence/<id>/in` the channel (channel.mjs) subscribes — so it reaches my context as a
    # `<channel from="david">` event. A DEDICATED send namespace (not `/in`) means NO feedback loop
    # with the display path below (which republishes raw `/in` -> the bus `/in` NAMESPACE):
    # compose in, in never flows back to compose. The message also echoes to the thread via that
    # display path, so David sees what he sent.
    compose_ns = f"intelligence/{intelligence_id}/compose"
    raw_in_channel = f"intelligence/{intelligence_id}/in"

    b = source_bus.acquire_bus(mqtt_url, label=f"channel-bridge-{intelligence_id}",
                               bus=bus)
    # DECLARE every namespace this relay publishes or subscribes, with an
    # EXPLICIT scope ([CXC-SOB-DECLARE]). `compose_ns` matters most: it is
    # SELF-SUBSCRIBED territory — the console's composer publishes it and this
    # relay consumes it in the same process once it shares the console's bus.
    # Left at AUTO, `_resolve_scope` would see a local subscriber and resolve
    # LOCAL, and David's message would die in-process LOOKING SENT. That is
    # [DEC-COMPOSER-SEND-GLOBAL-SCOPE] verbatim, on the human's channel.
    for _ns in list(raw_channels) + [compose_ns]:
        source_bus.declare(b, _ns, scope=Scope.GLOBAL,
                           source=f"channel-bridge-{intelligence_id}",
                           label="intelligence channel")
    source_bus.manifest(f"channel-bridge-{intelligence_id}")

    def _echo_raw_to_bus(channel: str, payload: bytes) -> None:
        # `channel` is e.g. "intelligence/architect/in" — used VERBATIM as the
        # bus namespace, which is what keeps the relayed topic byte-identical.
        # `source=` is the frozen `src` field the chat/thread widgets key voices
        # off ([CXC-SOB-WIRE-DELTAS]); it must stay "channel-bridge".
        b.publish_state(channel, _decode_foreign(payload), source="channel-bridge")

    def _compose_to_raw_in(ns: str, value, _old) -> None:
        # The pattern covers this relay's OWN republished `/in`, `/out` and
        # `/channel/alive` namespaces as well — filter to the compose leg, or
        # the relay would re-inject its own echo of David's message.
        if ns != compose_ns:
            return
        # The compose value arrives DECODED and already unwrapped by the bus —
        # no `env.get("val")` dance, because there is no envelope at this layer
        # any more. `_build_raw_in` is unchanged, so the outbound bytes are too.
        out = _build_raw_in(value)
        if out is None:
            return  # ignore an empty commit (focus-out with no text, no image)
        ok = b.publish_foreign(raw_in_channel, json.dumps(out), qos=1)
        image = out.get("image")
        sys.stderr.write(f"[channel_bridge] compose -> {intelligence_id}/in: {out['text'][:80]!r}"
                         + (f" image={image!r}" if image else "")
                         + ("" if ok else "  !! REFUSED by transport — the human's "
                                          "message did NOT reach the Intelligence")
                         + "\n")

    for _ch in raw_channels:
        b.subscribe_foreign(_ch, _echo_raw_to_bus)
    # PATTERN, NOT AN EXACT SUBSCRIBE, AND THIS ONE IS LOAD-BEARING — see the
    # REPEAT DELIVERY paragraph in this module's docstring. `bus.subscribe`
    # here would have silently dropped every repeated message David sends.
    b.subscribe_pattern(f"intelligence/{intelligence_id}/*", _compose_to_raw_in)
    sys.stderr.write(f"[channel_bridge] display {raw_channels} -> bus namespaces; "
                     f"send {compose_ns} -> {raw_in_channel}\n")
    # [CXC-SOB-RUN-BLOCKS]: there is no `loop_forever` to hold this thread any
    # more, and a clean return would make the supervisor re-invoke this relay
    # every 5 s — duplicating the human's channel, which is the doubling defect
    # [SPEC:CXC-IC-SINGLETON] closed. Park.
    source_bus.park(stop)
    return 0


def main(argv: list[str], *, bus=None, stop=None) -> int:
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    intelligence_id = opt("--id", os.environ.get("INTELLIGENCE_ID", "architect"))
    mqtt_url = opt("--mqtt", os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    return run(intelligence_id, mqtt_url, bus=bus, stop=stop)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
