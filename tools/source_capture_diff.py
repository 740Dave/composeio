#!/usr/bin/env python3
"""source_capture_diff — the SOB-C2 capture-diff for one console data source.

[SPEC:CXC-SOURCES-ON-BUS] acceptance criterion SOB-C2: "WHEN each namespace's
traffic is captured off the broker for 120 s before and after a slice THEN the
normalized payload sets are IDENTICAL."

WHY THIS AND NOT `mosquitto_sub`. The spec's mechanism is a broker-side capture,
which has two problems in practice on this machine and in CI:

  1. `mosquitto_sub` is not installed here, and more importantly
  2. the LIVE console is publishing the SAME namespaces with the SAME `src` at
     the same moment. A broker capture cannot tell the source under test from the
     production one, so "before" and "after" would both be contaminated by an
     unrelated third publisher and the diff would be meaningless.

So the capture is taken at the PAHO BOUNDARY instead: `paho.mqtt.client.Client`
is replaced with a recorder, and everything the source hands it — topic, payload
bytes, qos, retain — is captured. That boundary IS the wire; nothing downstream
of it can change a byte. It also catches BOTH publish paths in one instrument,
because the pre-conversion source and the post-conversion `MQTTTransport` both
bottom out in the same `client.publish(...)` call. And it is hermetic: no broker,
no network, deterministic, and reproducible by anyone.

WHAT IS NORMALIZED, AND WHY THAT LIST IS EXACTLY FOUR ITEMS
-----------------------------------------------------------
Per [CXC-SOB-WIRE-DELTAS], routing through the bus legitimately changes four
things and NOTHING ELSE. Anything else the diff surfaces is a DEFECT.

  `seq` — DROPPED. Each source keeps its own per-source counter today; the bus
          assigns one process-global monotonic counter across all namespaces.
          Leaving `seq` in the diff fails 100% of the time and tells you nothing.
  `ts`  — DROPPED. Wall clock; differs run to run even without a change.
  `qos` — pulled OUT of the payload diff and reported SEPARATELY, with a
          non-decreasing assertion. `_QOS_MAP[STATE] = 1`, so a source publishing
          at qos 0 today moves 0 -> 1. qos is a delivery guarantee, not payload;
          an upgrade cannot lose a message, a downgrade could.
  self-echo — not visible at this boundary (it is an inbound concern), noted for
          completeness.

Values that legitimately CHANGE between runs (a timestamp inside a payload, a
token count, a file mtime) would otherwise drown the diff, so `--volatile`
blanks named JSON paths inside `val` before comparing. Use it sparingly and say
which keys you blanked — a blanked key is a key you are NOT checking.

USAGE
    # before the conversion
    python3 tools/source_capture_diff.py capture --source lts_engine_status \\
        --argv "--mqtt mqtt://x:1883 --interval 1" --seconds 4 --out before.json
    # ...convert the source, then
    python3 tools/source_capture_diff.py capture --source lts_engine_status \\
        --argv "--mqtt mqtt://x:1883 --interval 1" --seconds 4 --out after.json
    python3 tools/source_capture_diff.py diff before.json after.json
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in (os.path.join(_REPO_ROOT, "python"),
           os.path.join(_REPO_ROOT, "compose_projects", "intelligence", "publisher"),
           os.path.join(_REPO_ROOT, "compose_projects", "intelligence", "tools")):
    if _d not in sys.path:
        sys.path.insert(0, _d)


# --------------------------------------------------------------------------- #
# The recorder that stands in for paho
# --------------------------------------------------------------------------- #
class RecordingPahoClient:
    """Everything a ComposeIO publisher asks of a paho client, recorded.

    `on_connect` is fired SYNCHRONOUSLY from `connect()` so `MQTTTransport` marks
    itself connected and publishes immediately instead of parking messages in its
    pre-connect queue — otherwise the AFTER capture would be empty and would look
    like a dark source.
    """

    _ALL: List["RecordingPahoClient"] = []
    _LOCK = threading.Lock()

    def __init__(self, client_id: str = "", **_kw):
        self.client_id = client_id
        self.published: List[dict] = []
        self.subscriptions: List[tuple] = []
        self.on_connect = None
        self.on_message = None
        self.on_disconnect = None
        self._looping = False
        with RecordingPahoClient._LOCK:
            RecordingPahoClient._ALL.append(self)

    # -- connection lifecycle (all no-ops that record) --------------------- #
    def username_pw_set(self, u, p=None): pass

    def connect(self, host, port=1883, keepalive=60):
        self.broker = (host, port)
        if self.on_connect is not None:
            self.on_connect(self, None, {}, 0)

    def connect_async(self, host, port=1883, keepalive=60):
        self.connect(host, port, keepalive)

    def reconnect(self): pass
    def loop_start(self): self._looping = True
    def loop_stop(self): self._looping = False
    def disconnect(self): self._looping = False

    def loop_forever(self):
        raise RuntimeError(
            "loop_forever() reached the capture harness. This source is not "
            "convertible without [CXC-SOB-RUN-BLOCKS] parking, and capturing it "
            "here would hang the harness rather than fail it.")

    # -- the wire ---------------------------------------------------------- #
    def publish(self, topic, payload=None, qos=0, retain=False):
        raw = payload.decode() if isinstance(payload, (bytes, bytearray)) else payload
        with RecordingPahoClient._LOCK:
            self.published.append({"topic": topic, "payload": raw,
                                   "qos": int(qos), "retain": bool(retain)})
        return _PublishResult()

    def subscribe(self, topic, qos=0):
        self.subscriptions.append((topic, int(qos)))
        return (0, 1)

    def unsubscribe(self, topic): pass

    @classmethod
    def reset(cls):
        with cls._LOCK:
            cls._ALL.clear()

    @classmethod
    def everything(cls) -> List[dict]:
        with cls._LOCK:
            out = []
            for c in cls._ALL:
                out.extend(c.published)
            return out

    @classmethod
    def all_subscriptions(cls) -> List[tuple]:
        with cls._LOCK:
            return [s for c in cls._ALL for s in c.subscriptions]

    @classmethod
    def client_ids(cls) -> List[str]:
        with cls._LOCK:
            return [c.client_id for c in cls._ALL]


class _PublishResult:
    rc = 0
    mid = 1

    def wait_for_publish(self, timeout=None): return True
    def is_published(self): return True


def _install_fake_paho():
    """Replace `paho.mqtt.client.Client` process-wide.

    Patching the CLASS on the real module (rather than injecting a factory)
    is what makes the harness work identically for a source that builds its own
    client, one that goes through `bus_client`, and one that goes through
    `MQTTTransport` — three different call paths, one instrument.
    """
    import paho.mqtt.client as mqtt
    original = mqtt.Client
    mqtt.Client = RecordingPahoClient                       # type: ignore[assignment]
    return mqtt, original


# --------------------------------------------------------------------------- #
# capture
# --------------------------------------------------------------------------- #
def capture(source: str, argv: List[str], seconds: float,
            lock_dir: Optional[str] = None) -> dict:
    """Run `<source>.main(argv)` on a daemon thread for `seconds`, recording the
    wire. Returns the capture document."""
    import importlib

    RecordingPahoClient.reset()
    mqtt_mod, original = _install_fake_paho()

    tmp_lock = None
    if lock_dir is None:
        tmp_lock = tempfile.mkdtemp(prefix="capture-locks-")
        lock_dir = tmp_lock
    # A PRIVATE lock dir so the source ACQUIRES its singleton instead of yielding
    # to the live console — otherwise `run()` returns immediately and the capture
    # is empty for a reason that has nothing to do with the conversion.
    prev_lock = os.environ.get("COMPOSEIO_LOCK_DIR")
    os.environ["COMPOSEIO_LOCK_DIR"] = lock_dir

    error: List[BaseException] = []
    stop = threading.Event()

    try:
        mod = importlib.import_module(source)
        # Reset the shared-connection singletons so a second capture in the same
        # process does not silently reuse the first one's client.
        try:
            from composeio import bus_client
            bus_client._reset_for_tests()
        except Exception:                                  # noqa: BLE001
            pass
        try:
            from composeio import source_bus
            source_bus.reset_for_tests()
        except Exception:                                  # noqa: BLE001
            pass

        kwargs: Dict[str, Any] = {}
        import inspect
        params = inspect.signature(mod.main).parameters
        if "stop" in params:
            kwargs["stop"] = stop

        returned_early = threading.Event()

        def _run():
            try:
                mod.main(list(argv), **kwargs)
            except BaseException as exc:                   # noqa: BLE001
                error.append(exc)
            finally:
                # Returning AFTER we asked it to stop is correct; returning
                # BEFORE is the [CXC-SOB-RUN-BLOCKS] defect. Distinguish them,
                # or the harness cries wolf on every converted source.
                if not stop.is_set():
                    returned_early.set()

        t = threading.Thread(target=_run, daemon=True, name=f"capture:{source}")
        started = time.time()
        t.start()
        time.sleep(seconds)
        stop.set()
        t.join(timeout=5.0)
        elapsed = time.time() - started

        doc = {
            "source": source,
            "argv": list(argv),
            "seconds": seconds,
            "elapsed": round(elapsed, 3),
            "thread_still_alive": t.is_alive(),
            "returned_before_stop": returned_early.is_set(),
            "stopped_cleanly": bool(kwargs) and not t.is_alive()
                               and not returned_early.is_set(),
            "error": repr(error[0]) if error else None,
            "client_ids": RecordingPahoClient.client_ids(),
            "subscriptions": [list(s) for s in RecordingPahoClient.all_subscriptions()],
            "messages": RecordingPahoClient.everything(),
        }
        return doc
    finally:
        mqtt_mod.Client = original                         # type: ignore[assignment]
        if prev_lock is None:
            os.environ.pop("COMPOSEIO_LOCK_DIR", None)
        else:
            os.environ["COMPOSEIO_LOCK_DIR"] = prev_lock


# --------------------------------------------------------------------------- #
# normalize + diff
# --------------------------------------------------------------------------- #
_DROP = ("ts", "seq")


def _blank_volatile(val: Any, paths: List[str]) -> Any:
    """Blank named fields inside a payload value so a legitimately changing one
    (a wall-clock timestamp, a token count, an mtime, this process's own pid)
    does not drown the diff.

    Two forms, and the difference matters:
      * a DOTTED path `a.b.c` blanks exactly that location — precise, and what
        you want when only one instance of a common key name is volatile;
      * a BARE key `ts` blanks that key ANYWHERE in the tree, at any depth. The
        console's payloads nest per-item records (`daemons.<name>.last_seen`,
        `corpora[].last_refresh`), and enumerating every occurrence by path is
        both unreadable and one new entry away from a false dirty.

    A blanked key is a key you are NOT checking. Say which ones you blanked.
    """
    if not paths:
        return val
    import copy
    out = copy.deepcopy(val)

    bare = {p for p in paths if "." not in p}
    dotted = [p for p in paths if "." in p]

    def _scrub(node):
        if isinstance(node, dict):
            for k in list(node):
                if k in bare:
                    node[k] = "<volatile>"
                else:
                    _scrub(node[k])
        elif isinstance(node, list):
            for item in node:
                _scrub(item)

    if bare:
        _scrub(out)

    for path in dotted:
        cur = out
        parts = path.split(".")
        for p in parts[:-1]:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                cur = None
                break
        if isinstance(cur, dict) and parts[-1] in cur:
            cur[parts[-1]] = "<volatile>"
        elif isinstance(cur, list):
            for item in cur:
                if isinstance(item, dict) and parts[-1] in item:
                    item[parts[-1]] = "<volatile>"
    return out


def normalize(doc: dict, volatile: Optional[List[str]] = None) -> dict:
    """The comparable form: the SET of (topic, src, ns, type, scope, val)
    tuples, plus a separate per-topic max qos."""
    volatile = volatile or []
    payloads = set()
    qos_by_topic: Dict[str, int] = {}
    topics: Dict[str, int] = {}
    undecodable = []
    for m in doc.get("messages", []):
        topic = m["topic"]
        topics[topic] = topics.get(topic, 0) + 1
        qos_by_topic[topic] = max(qos_by_topic.get(topic, 0), int(m.get("qos", 0)))
        try:
            env = json.loads(m["payload"])
        except (TypeError, ValueError):
            undecodable.append(topic)
            continue
        for k in _DROP:
            env.pop(k, None)
        if "val" in env:
            env["val"] = _blank_volatile(env["val"], volatile)
        payloads.add(json.dumps({"topic": topic, **env}, sort_keys=True))
    return {
        "topics": topics,
        "qos_by_topic": qos_by_topic,
        "payloads": payloads,
        "undecodable": undecodable,
        "subscriptions": sorted(tuple(s) for s in doc.get("subscriptions", [])),
    }


def diff(before: dict, after: dict, volatile: Optional[List[str]] = None) -> int:
    a = normalize(before, volatile)
    b = normalize(after, volatile)
    problems: List[str] = []

    only_before = sorted(set(a["topics"]) - set(b["topics"]))
    only_after = sorted(set(b["topics"]) - set(a["topics"]))
    if only_before:
        problems.append(
            f"BLOCKER — topic(s) present BEFORE and absent AFTER (a dark "
            f"namespace): {only_before}")
    if only_after:
        problems.append(f"NEW topic(s) AFTER: {only_after}")

    lost = a["payloads"] - b["payloads"]
    gained = b["payloads"] - a["payloads"]
    if lost:
        problems.append(f"{len(lost)} normalized payload(s) present BEFORE, "
                        f"absent AFTER:\n    " +
                        "\n    ".join(sorted(lost)[:5]))
    if gained:
        problems.append(f"{len(gained)} normalized payload(s) present AFTER, "
                        f"absent BEFORE:\n    " +
                        "\n    ".join(sorted(gained)[:5]))

    for topic, qb in sorted(a["qos_by_topic"].items()):
        qa = b["qos_by_topic"].get(topic)
        if qa is None:
            continue
        if qa < qb:
            problems.append(f"BLOCKER — qos DECREASED on {topic}: {qb} -> {qa}")
        elif qa != qb:
            print(f"  qos (recorded, not gated) {topic}: {qb} -> {qa}")

    if a["undecodable"] or b["undecodable"]:
        problems.append(f"undecodable payloads: before={a['undecodable']} "
                        f"after={b['undecodable']}")

    print(f"BEFORE: {sum(a['topics'].values())} message(s) over "
          f"{len(a['topics'])} topic(s); {len(a['payloads'])} distinct "
          f"normalized payload(s)")
    print(f"AFTER : {sum(b['topics'].values())} message(s) over "
          f"{len(b['topics'])} topic(s); {len(b['payloads'])} distinct "
          f"normalized payload(s)")
    if a["subscriptions"] != b["subscriptions"]:
        print(f"  subscriptions BEFORE: {a['subscriptions']}")
        print(f"  subscriptions AFTER : {b['subscriptions']}")

    if problems:
        print("\nCAPTURE-DIFF DIRTY:")
        for p in problems:
            print(f"  * {p}")
        return 1
    print("\nCAPTURE-DIFF CLEAN (ts + seq normalized out; qos recorded "
          "separately and non-decreasing)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture")
    c.add_argument("--source", required=True)
    c.add_argument("--argv", default="", help="the source's argv, as one string")
    c.add_argument("--seconds", type=float, default=4.0)
    c.add_argument("--out", required=True)

    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after")
    d.add_argument("--volatile", action="append", default=[],
                   help="dotted path inside `val` to blank before comparing "
                        "(repeatable). SAY which ones you blanked.")

    a = ap.parse_args(argv)
    if a.cmd == "capture":
        doc = capture(a.source, shlex.split(a.argv), a.seconds)
        with open(a.out, "w") as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        n = len(doc["messages"])
        print(f"captured {n} message(s) from {a.source} in {doc['elapsed']}s "
              f"-> {a.out}")
        if doc["error"]:
            print(f"  !! the source RAISED: {doc['error']}")
        if doc["returned_before_stop"] and not doc["error"]:
            print("  !! run() RETURNED ON ITS OWN — under DataSourceSupervisor "
                  "that is read as 'yielded my singleton' and starts a reclaim "
                  "retry loop ([CXC-SOB-RUN-BLOCKS])")
        elif doc["stopped_cleanly"]:
            print("  run() blocked, then joined on `stop` ([CXC-SOB-RUN-BLOCKS] ok)")
        topics: Dict[str, int] = {}
        for m in doc["messages"]:
            topics[m["topic"]] = topics.get(m["topic"], 0) + 1
        for t in sorted(topics):
            print(f"    {t}  x{topics[t]}")
        return 0

    with open(a.before) as f:
        before = json.load(f)
    with open(a.after) as f:
        after = json.load(f)
    return diff(before, after, a.volatile)


if __name__ == "__main__":
    raise SystemExit(main())
