#!/usr/bin/env python3
"""Terminal chat client for the Intelligence Console channel.

A minimal, dependency-light messaging UX for talking to an Intelligence from a
terminal. It publishes each line you type to `compose_projects/intelligence/<id>/in` -- which the
composeio-channel MCP server delivers into the Intelligence's session as a
<channel> event -- and prints the Intelligence's replies from
`compose_projects/intelligence/<id>/out` (what its `reply` tool publishes).

STILL CURRENT, despite what its original note said. This was written as a
stopgap "until the browser console's live input (MQTT-over-WebSocket) is wired";
that browser console (`index.html` + `ws_bridge/`) was RETIRED and deleted
2026-08-08 -- the live console is the native SDL scene `console.compose.yaml`,
which has its own chat composer. So this is no longer a stopgap for anything: it
is the headless/second-terminal way in, useful when the console is down, when
you are on another machine, or when you want to drive the channel from a script.

    python3 compose_projects/intelligence/console/chat.py            # talk to "architect"
    INTELLIGENCE_ID=xo python3 .../chat.py           # talk to a different id
    python3 compose_projects/intelligence/console/chat.py --selftest  # offline wiring check

Run it in a SECOND terminal while the instance is resumed into the console via
compose_projects/intelligence/launcher/transport.sh.

CONVERTED TO THE COMPOSEBUS WAIST 2026-08-11 ([SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], the
§7b sweep beyond the console source roster). It no longer imports paho or builds a client. Two
kinds of address, and telling them apart is the whole point:

  * `compose_projects/intelligence/<id>/{in,out}` are FOREIGN CHANNELS — the composeio-channel MCP's own wire,
    a bare topic carrying a bare `{from,text}` payload. They go through
    `bus.subscribe_foreign` / `bus.publish_foreign`, which preserves the bytes exactly.
  * `thread/<id>/narration` is a ComposeBus NAMESPACE, published by `transcript_publisher`
    through the bus — so it goes through `bus.subscribe`.

THAT DISTINCTION FIXED A LIVE BUG. This client subscribed the narration topic RAW, i.e. the
literal topic `thread/<id>/narration`. `transcript_publisher` has never published there: it
publishes the NAMESPACE, which the transport frames as `composeio/global/thread/<id>/narration`.
So the "four voices" this file's own comments promise were only ever three — the narration voice
has been silently dead for as long as the publisher has been on the bus, with no error, because
subscribing a topic nobody publishes looks exactly like a quiet Intelligence. Naming the seam is
what surfaced it; a `git mv`-proof grep never would have.
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT / "python") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "python"))

from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

ID = os.environ.get("INTELLIGENCE_ID", "architect")
BROKER = os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883")


# `host_port` is GONE (2026-08-11). It was this file's private copy of the
# broker-URL parser; `source_bus.acquire_bus` resolves the URL now, through the
# ONE implementation in `bus_client.parse_mqtt_url`. A per-module copy of a
# parser is the fork this codebase keeps paying for ([DX-NARROW-WAIST]) — the
# selftest below exercises the shared one by reference instead.
host_port = source_bus.parse_mqtt_url


def parse_out(payload: bytes) -> tuple[str, str]:
    """(who, text) from an compose_projects/intelligence/<id>/out message (JSON {from,text} or raw)."""
    raw = payload.decode("utf-8", "replace")
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return str(d.get("from", ID)), str(d.get("text", raw))
    except (json.JSONDecodeError, ValueError):
        pass
    return ID, raw


def make_in(text: str) -> str:
    """The payload to publish to compose_projects/intelligence/<id>/in (channel maps text -> content)."""
    return json.dumps({"from": "david", "text": text, "ts": time.time()})


def main(*, bus=None) -> None:
    chan_in, chan_out = f"intelligence/{ID}/in", f"intelligence/{ID}/out"
    # [CXC-IH-5A] carried-identity: the narration feed is id-scoped
    # (thread/<id>/narration) so two intelligences on the shared broker never
    # merge — consistent with the console's bindings and the
    # transcript_publisher --namespace-by-id hard-switch.
    ns_narr = f"thread/{ID}/narration"     # a BUS NAMESPACE, not a raw topic

    def _show(label: str, text: str) -> None:
        sys.stdout.write(f"\r{label}> {text}\nyou> ")
        sys.stdout.flush()

    def on_foreign(channel: str, payload: bytes) -> None:
        who, text = parse_out(payload)
        if channel == chan_in and who == "david":
            return                          # don't echo the human's own messages back
        _show(who, text)                    # harness on /in, deliberate replies on /out

    def on_narration(_ns: str, value, _old) -> None:
        # A bus value arrives DECODED, so `parse_out` (which takes bytes) is
        # not the right reader here — the shapes genuinely differ, and pretending
        # otherwise is what produced the dead subscription this conversion found.
        who, text = ID, value
        if isinstance(value, dict):
            who = str(value.get("from", ID))
            text = value.get("text", value)
        _show(f"{who}·narrate", text)

    b = source_bus.acquire_bus(BROKER, label=f"console-chat-{ID}", bus=bus)
    source_bus.declare(b, ns_narr, scope=Scope.GLOBAL, source=f"console-chat-{ID}",
                       label="narration")
    source_bus.manifest(f"console-chat-{ID}")
    # Four voices: deliberate replies (.../out) and harness/system messages
    # (.../in, minus the human's own echoes) as FOREIGN channels; the
    # auto-mirrored narration as a BUS NAMESPACE. Human input is what you type.
    #
    # REPEAT DELIVERY: required on narration, so it is a PATTERN subscription —
    # `transcript_publisher` stamps `turn`/`ts` into each payload precisely so
    # two identical spoken lines differ, but a display client must not DEPEND on
    # the publisher's payload shape to see the second one.
    for _ch in (chan_out, chan_in):
        b.subscribe_foreign(_ch, on_foreign)
    b.subscribe_pattern(f"thread/{ID}/*",
                        lambda ns, v, o: on_narration(ns, v, o) if ns == ns_narr else None)
    print(f"[chat] connected to {BROKER}  ·  talking to '{ID}'")
    print(f"[chat] you -> {chan_in}   ·   {ID} -> {chan_out}   ·   narration -> {ns_narr}")
    print("[chat] type a message + Enter.  Ctrl-C to quit.\n")
    try:
        while True:
            sys.stdout.write("you> ")
            sys.stdout.flush()
            line = sys.stdin.readline()
            if not line:
                break
            msg = line.rstrip("\n")
            if msg and not b.publish_foreign(chan_in, make_in(msg), qos=1):
                sys.stdout.write("[chat] !! NOT SENT — this transport cannot carry "
                                 "the channel\n")
    except KeyboardInterrupt:
        print()
    finally:
        b.stop()


def selftest() -> None:
    ok = True
    cases = [
        ("mqtt://mqtt-broker.local:1883", ("mqtt-broker.local", 1883)),
        ("mqtt-broker.local", ("mqtt-broker.local", 1883)),
        ("mqtt://host:1884", ("host", 1884)),
    ]
    for url, exp in cases:
        got = host_port(url)
        ok &= got == exp
        print(f"  host_port({url!r}) -> {got}  {'OK' if got == exp else 'FAIL exp ' + str(exp)}")
    who, text = parse_out(b'{"from":"architect","text":"hello from the seam","ts":1.0}')
    ok &= (who, text) == ("architect", "hello from the seam")
    print(f"  parse_out(json) -> {(who, text)}  {'OK' if who == 'architect' else 'FAIL'}")
    who, text = parse_out(b"raw string reply")
    ok &= text == "raw string reply"
    print(f"  parse_out(raw)  -> {(who, text)}  {'OK' if text == 'raw string reply' else 'FAIL'}")
    d = json.loads(make_in("hi"))
    ok &= d["text"] == "hi" and d["from"] == "david"
    print(f"  make_in -> {d}  {'OK' if d['text'] == 'hi' else 'FAIL'}")
    # [CXC-IH-5A] the narration topic must be id-scoped (thread/<id>/narration), consistent with
    # the console bindings + the transcript_publisher --namespace-by-id hard-switch.
    narr = f"thread/{ID}/narration"
    ok &= narr.startswith("thread/") and narr.endswith("/narration") and f"/{ID}/" in narr
    print(f"  narration topic -> {narr!r}  {'OK' if f'/{ID}/' in narr else 'FAIL not id-scoped'}")
    print("SELFTEST:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    main()
