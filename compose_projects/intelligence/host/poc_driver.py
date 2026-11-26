#!/usr/bin/env python3
"""poc_driver — the Option-A IntelligenceHost proof-of-concept ([CXC-IH-POC]).

Proves, headless (no human), the three PoC acceptance points:
  (a) `self/*` (the live architect's legacy singleton) and `self/builder-1/*` (a spawned builder's
      carried identity) coexist on the bus under DISTINCT topics — NO overwrite.
  (b) `host.spawn` starts a child carrying INTELLIGENCE_ID=builder-1.
  (c) `host.list` shows it and `host.stop` ends it.

Two ways to run it:

  --selftest      NO broker, NO real claude. Deterministic. Proves:
                    * the no-clobber MECHANISM at the envelope-key level (apply_self_id_namespace:
                      legacy `self/*` vs `self/builder-1/*` are DISJOINT key-sets → distinct topics);
                    * the FULL spawn/list/stop lifecycle with a carried-id stub child.
                  This is the mirror of away_watch_publisher's selftest: logic + lifecycle tested
                  without network. Composes host.py + self_publisher.py's own selftests too.

  --broker-proof  REAL broker. Spawns ONE headless builder (id=builder-1) that runs
                  `self_publisher --namespace-by-id` (the REAL 5a code path, id CARRIED from the env
                  the Host set — no --id passed), subscribes the `self/*` namespace family, and WITNESSES
                  `self/builder-1/*` appearing on the wire in parallel with the live architect's
                  `self/*`. Respects FROZEN: it never publishes legacy `self/*` itself (that would
                  fight the running console) — it only observes the architect and adds the builder.

The real-`claude` spawn path is CONSTRUCTED + unit-tested in host.py::build_command (the headless
`claude -p <task> --agent <t>` argv + carried env); it is NOT live-launched here (a real claude child
is too heavy/unsafe for an unattended proof). The wire proof uses self_publisher as the builder's
body so `self/builder-1/*` is genuinely produced by the real namespacing code.

Run:  python3 poc_driver.py --selftest
      python3 poc_driver.py --broker-proof [--mqtt mqtt://mqtt-broker.local:1883] [--duration 8]
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_INTEL_DIR = os.path.dirname(_THIS_DIR)
_PUBLISHER_DIR = os.path.join(_INTEL_DIR, "publisher")
for _d in (_THIS_DIR, _PUBLISHER_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

_PY_DIR = os.path.join(os.path.dirname(_INTEL_DIR), "python")
if _PY_DIR not in sys.path:
    sys.path.insert(0, _PY_DIR)

from host import IntelligenceHost, IntelligenceSpec  # noqa: E402
import self_publisher  # noqa: E402  (for apply_self_id_namespace + compute_lines in the selftest)
# [SPEC:CXC-SOURCES-ON-BUS] — the ONE bus seam.
from composeio import source_bus  # noqa: E402

_SELF_PUBLISHER = os.path.join(_PUBLISHER_DIR, "self_publisher.py")
_STUB = os.path.join(_THIS_DIR, "_stub_child.py")

# `_parse_mqtt_url` is GONE (2026-08-11): this file's private copy of the broker-URL
# parser. `source_bus.acquire_bus` resolves the URL through the ONE implementation
# ([DX-NARROW-WAIST]); a per-module copy is the fork that keeps costing.


# ── No-broker selftest ────────────────────────────────────────────────────────────────────────
def _selftest() -> int:
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    # (a) the no-clobber MECHANISM: legacy `self/*` and namespaced `self/builder-1/*` are DISJOINT
    #     key-sets. On the wire these are distinct composeio/global/self/... topics — no overwrite.
    parsed = {"self/context_level": 0.5, "self/registered": True, "self/subagents/count": 4}
    architect_lines = self_publisher.compute_lines(parsed)                       # legacy self/*
    builder_lines = self_publisher.apply_self_id_namespace(
        self_publisher.compute_lines(parsed), "builder-1")                       # self/builder-1/*
    check("self/context_pct" in architect_lines,
          f"(a) architect legacy self/context_pct missing: {list(architect_lines)}")
    check("self/builder-1/context_pct" in builder_lines,
          f"(a) builder self/builder-1/context_pct missing: {list(builder_lines)}")
    check(set(architect_lines).isdisjoint(set(builder_lines)),
          f"(a) CLOBBER: legacy and builder key-sets overlap: "
          f"{set(architect_lines) & set(builder_lines)}")
    # both carry a context_pct, but at distinct keys → distinct topics → coexist.
    check(architect_lines["self/context_pct"] == builder_lines["self/builder-1/context_pct"],
          "(a) same reading value expected at distinct keys")

    # (b)/(c) FULL lifecycle with a carried-id stub: spawn carries INTELLIGENCE_ID=builder-1, list
    #         shows it, stop ends it. (No broker: the stub writes its carried id to a file.)
    host = IntelligenceHost(claude_bin="claude")
    td = tempfile.mkdtemp()
    touch = os.path.join(td, "id.txt")
    spec = IntelligenceSpec(id="builder-1", mode="headless",
                            agent_type="composeio-expert", task="echo a status line")
    handle = host.spawn(spec, argv=[sys.executable, _STUB, "--touch", touch])
    check(handle.id == "builder-1" and handle.pid > 0, f"(b) bad handle: {handle!r}")

    carried = None
    for _ in range(150):
        if os.path.exists(touch):
            carried = open(touch).read().strip()
            if carried:
                break
        time.sleep(0.02)
    check(carried == "builder-1", f"(b) child did not carry INTELLIGENCE_ID=builder-1: {carried!r}")

    roster = host.list()
    check(len(roster) == 1 and roster[0].id == "builder-1" and roster[0].status() == "running",
          f"(c) list did not show a running builder-1: {[h.as_dict() for h in roster]}")

    check(host.stop("builder-1") is True, "(c) stop(builder-1) failed")
    check(host.get("builder-1").status().startswith("exited"),
          f"(c) builder-1 still running after stop: {host.get('builder-1').status()}")

    # (d) the REAL headless claude argv is constructed + carries the identity (constructed-not-launched).
    argv, env = host.build_command(spec)
    check(argv[:3] == ["claude", "-p", "echo a status line"]
          and argv[3:5] == ["--agent", "composeio-expert"],
          f"(d) real headless claude argv wrong: {argv!r}")
    check(env["INTELLIGENCE_ID"] == "builder-1",
          f"(d) real claude env missing carried id: {env.get('INTELLIGENCE_ID')!r}")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: poc_driver selftest (4/4 groups) — no broker, no real claude\n")
    for line in (
        "  (a) NO-CLOBBER: legacy self/* and self/builder-1/* are DISJOINT key-sets (distinct topics)",
        "  (b) host.spawn carries INTELLIGENCE_ID=builder-1 into the child (child wrote its id back)",
        "  (c) host.list shows the running builder; host.stop ends it (roster reflects the exit)",
        "  (d) the REAL `claude -p <task> --agent <t>` argv+env is constructed + carries the id "
        "(not live-launched)",
    ):
        sys.stdout.write(line + "\n")
    return 0


# ── Real-broker proof ──────────────────────────────────────────────────────────────────────────
def _broker_proof(mqtt_url: str, duration: float) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS] — the witness on the bus.

    WHY THIS ONE IS CONVERTIBLE AND `source_capture_diff.py` IS NOT. Both observe
    the wire, so the reflex is to call both "instruments" and exempt both. The
    distinction is WHAT IS UNDER TEST. `source_capture_diff` diffs the BUS's own
    output, so observing it through the bus would share the blind spot with the
    thing being measured — it must name the substrate. This driver's subject is a
    CHILD PROCESS (`self_publisher --namespace-by-id`) and the claim is that
    `self/*` and `self/builder-1/*` are DISTINCT NAMESPACES that do not clobber
    each other. A pattern subscription resolves that claim at the same fidelity —
    `_dispatch_incoming` hands over exactly the `ns`/`val` this code used to dig
    out of the envelope by hand — and the witness gains nothing from knowing the
    substrate. So it goes through the waist like everything else.

    REPEAT DELIVERY: REQUIRED, and structural here — `_notify_pattern_subs` fires
    on EVERY inbound publish, so a builder that re-publishes an unchanged
    `context_pct` still refreshes `seen[ns]`. An exact subscribe would have made
    the liveness timestamps lie.
    """
    seen: dict[str, float] = {}          # namespace -> when it was last observed
    values: dict[str, object] = {}

    def on_self(ns: str, value, _old) -> None:
        if not ns:
            return
        seen[ns] = time.time()
        values[ns] = value

    sys.stderr.write(f"[poc_driver] connecting witness to {mqtt_url} ...\n")
    witness = source_bus.acquire_bus(mqtt_url, label=f"poc-driver-witness-{os.getpid()}")
    witness.subscribe_pattern("self/*", on_self)

    # Snapshot the LIVE architect for a moment before we add the builder (respect FROZEN: we only
    # OBSERVE the architect; we never publish legacy self/* ourselves).
    time.sleep(2.0)
    architect_before = sorted(k for k in seen if k.startswith("self/")
                              and not k.split("/")[1:2] == [""]
                              and len(k.split("/")) == 2)  # self/<reading>, not self/<id>/<reading>

    # A tiny transcript for the builder's self_publisher body (empty → context_level 0.0; it still
    # publishes, which is all the wire proof needs).
    td = tempfile.mkdtemp()
    jsonl = os.path.join(td, "builder-1-session.jsonl")
    open(jsonl, "w").close()

    host = IntelligenceHost(claude_bin="claude", mqtt_url=mqtt_url)
    spec = IntelligenceSpec(id="builder-1", mode="headless",
                            agent_type="composeio-expert", task="publish self/builder-1/* and idle")
    # The builder's BODY = self_publisher --namespace-by-id. Crucially NO --id is passed: the id is
    # CARRIED via INTELLIGENCE_ID that host.spawn set → self_publisher derives builder-1 from the env
    # → publishes self/builder-1/*. This exercises the real 5a chain end-to-end on the wire.
    builder_argv = [sys.executable, _SELF_PUBLISHER,
                    "--jsonl", jsonl, "--mqtt", mqtt_url,
                    "--namespace-by-id", "--poll", "2"]
    handle = host.spawn(spec, argv=builder_argv)
    sys.stderr.write(f"[poc_driver] spawned builder-1 pid={handle.pid} status={handle.status()}\n")

    # (c) list shows it.
    roster = host.list()
    list_ok = any(h.id == "builder-1" and h.status() == "running" for h in roster)

    # Let the builder publish, then witness the wire.
    time.sleep(max(3.0, duration))

    builder_topics = sorted(k for k in seen if k.startswith("self/builder-1/"))
    architect_topics = sorted(k for k in seen if k.startswith("self/")
                              and len(k.split("/")) == 2)  # self/<reading>

    # (a) no-clobber: builder topics exist AND are disjoint from architect topics.
    builder_seen = len(builder_topics) > 0
    disjoint = set(builder_topics).isdisjoint(set(architect_topics))

    handle_stopped = host.stop("builder-1")
    stopped_ok = host.get("builder-1").status().startswith("exited")
    witness.stop()

    # ── Report ──
    print("=" * 78)
    print("IntelligenceHost PoC — REAL-BROKER wire proof  [CXC-IH-POC]")
    print(f"  broker: {mqtt_url}")
    print("-" * 78)
    print(f"(b) host.spawn started builder-1  : pid={handle.pid}  (INTELLIGENCE_ID carried via env)")
    print(f"(c) host.list showed it running   : {list_ok}   roster={[h.id for h in roster]}")
    print(f"(c) host.stop ended it            : stop_returned={handle_stopped}  "
          f"final_status={host.get('builder-1').status()}")
    print("-" * 78)
    print(f"    architect legacy self/* topics observed on the wire ({len(architect_topics)}):")
    for t in architect_topics:
        print(f"        {t:32s} = {values.get(t)!r}")
    if not architect_topics:
        print("        (none — the live architect's self_publisher was not observed this window;")
        print("         the builder proof below still stands. Ensure the console is publishing.)")
    print(f"    builder-1 carried-id self/builder-1/* topics on the wire ({len(builder_topics)}):")
    for t in builder_topics:
        print(f"        {t:32s} = {values.get(t)!r}")
    print("-" * 78)
    print(f"(a) NO-CLOBBER: builder self/builder-1/* seen : {builder_seen}")
    print(f"(a) NO-CLOBBER: builder ∩ architect topics    : {set(builder_topics) & set(architect_topics)}"
          f"  (disjoint={disjoint})")
    if "self/context_pct" in architect_topics and "self/builder-1/context_pct" in builder_topics:
        print("    -> self/context_pct (architect) and self/builder-1/context_pct (builder) COEXIST")
        print("       on the bus simultaneously, distinct topics, NO overwrite. ✓")
    print("=" * 78)

    passed = (list_ok and handle_stopped and stopped_ok and builder_seen and disjoint)
    if passed:
        print("PASS: builder-1 carried self/builder-1/* onto the live bus without clobbering "
              "the architect's self/*; spawn/list/stop lifecycle verified on the wire.")
        return 0
    print("PARTIAL/FAIL: see the flags above. (builder_seen requires a reachable broker; "
          "architect topics require the live console to be publishing.)")
    return 1


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return _selftest()
    if "--broker-proof" in argv:
        def opt(flag, default):
            return argv[argv.index(flag) + 1] if flag in argv else default
        mqtt_url = opt("--mqtt", os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
        duration = float(opt("--duration", "8"))
        return _broker_proof(mqtt_url, duration)
    sys.stderr.write(__doc__ or "")
    sys.stderr.write("\nusage: poc_driver.py --selftest | --broker-proof [--mqtt URL] [--duration N]\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
