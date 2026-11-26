#!/usr/bin/env python3
"""Fleet HUD — a live ComposeUX dashboard over the running VeloCloud-540 soak.

Uses the in-process route the codebase scope recommended: build a ComposeIOBus, register
the namespaces from fleet-540-soak.compose.yaml, hand the parsed scene to the Skia render
engine (`composeio.render.engine`), and feed the widgets from a publish loop that pulls the
live soak telemetry (fleetnode) + the console observer's semantic events (benchnode). No MQTT
broker required.

Run modes:
  python3 fleet_hud.py --dry-run     # headless: poll once, print what would be published
  python3 fleet_hud.py               # GUI: live dashboard (needs a display)

PYTHONPATH must include composeio/python (the launcher below adds it relative to this file).
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # .../composeio
sys.path.insert(0, str(REPO / "python"))

CONFIG = HERE / "fleet-540-soak.compose.yaml"

# --- live data sources (the running soak) ------------------------------------
# fleetnode is behind the 540 NAT: reach it via the reverse tunnel (benchnode:22222).
#: SSH targets are CONFIGURATION: FLEET_BENCH_HOST (neutral default benchnode.local).
_BENCH_HOST = os.environ.get("FLEET_BENCH_HOST", "benchnode.local")
#: The tunnel login defaults to the INVOKING user (FLEET_SOAK_SSH_USER overrides it).
_SOAK_SSH_USER = os.environ.get("FLEET_SOAK_SSH_USER") or getpass.getuser()
#: Remote soak log names carry the node names: CONFIGURATION, supplied by the deploy overlay
#: (FLEET_SOAK_NODE_NAME, FLEET_BENCH_NAME). There is NO default: a guessed name tails a file that
#: does not exist and blanks the soak panel silently. Unset = the panel says "not configured".
_SOAK_NODE = os.environ.get("FLEET_SOAK_NODE_NAME", "").strip() or None
_BENCH_NAME = os.environ.get("FLEET_BENCH_NAME", "").strip() or None
_FLEETNODE = (f"ssh -o ProxyCommand='ssh -W %h:%p {_BENCH_HOST}' "
           "-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "
           f"-p 22222 {_SOAK_SSH_USER}@localhost")
_BENCHNODE = f"ssh -o ConnectTimeout=8 {_BENCH_HOST}"
#: The 540's REST status URL, as fleetnode reaches it: CONFIGURATION (FLEET_FW_STATUS_URL). No
#: default: an unset target is "not configured", never a probe of a placeholder that reports a
#: firewall that is up as "unreachable".
_FW_STATUS_URL = os.environ.get("FLEET_FW_STATUS_URL", "").strip() or None

#: The soak panel's configuration line (namespace ``soak_config``), and the probe's unset state.
SOAK_NOT_CONFIGURED = "soak logs not configured: set FLEET_SOAK_NODE_NAME and FLEET_BENCH_NAME"
FW_NOT_CONFIGURED = "540 status not configured: set FLEET_FW_STATUS_URL"


def soak_missing_names() -> list:
    """The soak-log env names that are unset (empty list = configured)."""
    return [n for n, v in (("FLEET_SOAK_NODE_NAME", _SOAK_NODE),
                           ("FLEET_BENCH_NAME", _BENCH_NAME)) if not v]


def _sh(cmd: str, timeout: int = 20) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def _last_json(blob: str) -> dict:
    for line in reversed(blob.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


def poll() -> dict:
    """Pull current values from the live soak. Returns namespace -> value."""
    missing = soak_missing_names()
    if missing:
        # The DUT log name carries no node name, so the DUT panel still reads; the node + iperf
        # logs are NOT guessed at. soak_config states why their gauges are empty.
        g = _sh(f"{_FLEETNODE} 'tail -1 ~/soak/logs/dut_540.jsonl 2>/dev/null; echo'")
        parts = [p for p in g.split("\n") if p.strip().startswith("{")]
        dut = _last_json(parts[0]) if parts else {}
        node, iperf = {}, {}
        soak_config = SOAK_NOT_CONFIGURED
    else:
        # one fleetnode round-trip for all three soak logs
        g = _sh(f"{_FLEETNODE} 'for f in ~/soak/logs/dut_540.jsonl ~/soak/logs/node_{_SOAK_NODE}.jsonl "
                f"~/soak/logs/iperf_{_BENCH_NAME}.jsonl; do tail -1 \"$f\" 2>/dev/null; echo; done'")
        parts = [p for p in g.split("\n") if p.strip().startswith("{")]
        dut = _last_json(parts[0]) if len(parts) > 0 else {}
        node = _last_json(parts[1]) if len(parts) > 1 else {}
        iperf = _last_json(parts[2]) if len(parts) > 2 else {}
        soak_config = f"soak logs: node {_SOAK_NODE}, bench {_BENCH_NAME}"
    ev_count = _sh(f"{_BENCHNODE} 'wc -l < ~/velo540_console_events.jsonl'").strip()

    ports_up = dut.get("ports_up", 0)
    ns = {
        "velo540_temp_c": dut.get("temp_max_c"),
        "velo540_pf": dut.get("pf_states"),
        "velo540_phase": "green" if ports_up and ports_up >= 2 else "red",
        "soak_tx_mbit": iperf.get("tx_mbit_s") or (node.get("net_rates", {})
                        .get("enp2s0", {}).get("tx_mbit_s")),
        "soak_retx": iperf.get("retransmits"),
        "soak_verdict": "yellow",   # pending until Monday collection + power-cycle merge
        os.environ.get("COMPOSEIO_VAR_SOAK_NODE_TEMP_NS", "fleetnode_temp_c"): node.get("temp_c"),
        "console_activity": int(ev_count) if ev_count.isdigit() else None,
        "soak_config": soak_config,
    }
    return {k: v for k, v in ns.items() if v is not None}


# --- mcp_action runner (v2 control layer, spec 13-mcp-action-binding) ---------
# The host side of a CommandWidget's mcp_action binding. Renderers stay host-agnostic;
# THIS is where "invoke a fleet tool" gets a concrete (allowlisted) implementation.
# Nothing imperative is reimplemented — these thin adapters call the same infra the
# fleet MCP servers wrap. Destructive tools are allowlisted but gated (never fire from
# the HUD demo); read tools run for real against whatever is currently reachable.

def _act_console_tail(args: dict) -> dict:
    """Read the 540's semantic console events off benchnode (up now, no fleetnode needed)."""
    n = int(args.get("n", 5))
    out = _sh(f"{_BENCHNODE} 'wc -l < ~/velo540_console_events.jsonl; "
              f"tail -{n} ~/velo540_console_events.jsonl'")
    lines = [l for l in out.splitlines() if l.strip()]
    if not lines:
        return {"ok": False, "summary": "no console events file (benchnode unreachable?)"}
    total, events = lines[0], lines[1:]
    last = events[-1] if events else "—"
    return {"ok": True, "summary": f"{total} events; last: {last[:90]}", "detail": out}


def _act_fw_status(args: dict) -> dict:
    """Live 540 REST status via fleetnode. Demonstrates external authority honestly:
    with fleetnode down + tunnel dead this returns a real failure, NOT an optimistic green.
    With no target configured it probes NOTHING and says so: neither green nor red."""
    if not _FW_STATUS_URL:
        return {"ok": False, "not_configured": True, "summary": FW_NOT_CONFIGURED,
                "detail": "no probe was run: FLEET_FW_STATUS_URL is unset"}
    if any(c in _FW_STATUS_URL for c in "'\"`$\\;&|<>() \t"):
        return {"ok": False, "summary": "FLEET_FW_STATUS_URL holds a quote, space or shell character; "
                                        "no probe was run",
                "detail": "the URL is passed inside a remote shell command and must be a plain URL"}
    out = _sh(f"{_FLEETNODE} 'curl -sk --connect-timeout 6 "
              f"{_FW_STATUS_URL} || echo __UNREACHABLE__'",
              timeout=25)
    ok = bool(out.strip()) and "__UNREACHABLE__" not in out and "closed" not in out.lower()
    return {"ok": ok,
            "summary": "540 REST reachable" if ok
                       else "540 unreachable (fleetnode down / tunnel dead) — external authority, no fake green",
            "detail": out or "(no output — fleetnode tunnel is dead)"}


def _act_gated(args: dict) -> dict:
    """Destructive tools: allowlist + confirm are proven at the widget; execution is
    deferred to the real MCP server (fleetpower / fleet540) post-soak. Never fires here."""
    return {"ok": False, "blocked": True,
            "summary": "gated — confirm+allowlist proven; execution deferred to the MCP server (post-soak)",
            "detail": "destructive action intentionally not fired from the HUD demo"}


# tool -> (adapter, destructive?)  — the design's per-surface allowlist (MCPA-5)
_ACTIONS = {
    "fleetsoak.console_tail":    (_act_console_tail, False),
    "fleet540.fw_status":        (_act_fw_status,    False),
    "fleet540.fw_config_backup": (_act_gated,        True),
    "fleetpower.power_cycle":    (_act_gated,        True),
}


def invoke_action(tool: str, args: dict, on_event) -> None:
    """RendererContext.invoke: run an allowlisted tool off the UI thread, stream events."""
    entry = _ACTIONS.get(tool)
    if entry is None:
        on_event({"kind": "result", "ok": False,
                  "summary": f"REFUSED: '{tool}' not in HUD allowlist"})
        return
    fn, _destructive = entry

    def _run():
        on_event({"kind": "progress", "pct": 15, "phase": "invoke"})
        try:
            res = fn(dict(args))
        except Exception as exc:  # never let a tool crash take down the UI
            res = {"ok": False, "summary": f"error: {exc}"}
        res["kind"] = "result"
        on_event(res)

    threading.Thread(target=_run, daemon=True).start()


def fire(tool: str, args: dict | None = None) -> int:
    """Headless: invoke one action end-to-end and print the streamed events."""
    events: list[dict] = []
    invoke_action(tool, args or {}, events.append)
    for _ in range(80):  # up to ~40s for the worker thread
        if any(e.get("kind") == "result" for e in events):
            break
        time.sleep(0.5)
    print(f"=== fire {tool} ===")
    for e in events:
        print("  ", e)
    result = next((e for e in events if e.get("kind") == "result"), None)
    return 0 if result and result.get("ok") else 1


# --- bus + namespaces (shared by dry-run and GUI) ----------------------------
def build_bus():
    from composeio.bus import ComposeIOBus
    from composeio.config import load_config
    from composeio.render.engine.components import expand_components
    # Expand `use:` component references into concrete widgets (spec 14) before
    # anything downstream — namespace registration + layout see only real widgets.
    cfg = expand_components(load_config(str(CONFIG)))
    bus = ComposeIOBus(bus_id="fleet-hud")
    bus.start()
    for nsdef in cfg.get("namespaces", []):
        bus.register_namespace(nsdef["name"], nsdef.get("state_engine", "continuous_0_255"),
                               label=nsdef.get("label"))
    return bus, cfg


def dry_run() -> int:
    bus, cfg = build_bus()
    vals = poll()
    print("=== poll() live values (would publish to the bus) ===")
    for k, v in vals.items():
        print(f"  {k:18} = {v}")
    for k, v in vals.items():
        bus.publish_state(k, v, source="hud")   # exercise the publish path
    print(f"\n[dry-run] published {len(vals)} namespaces to the in-process bus OK")
    return 0 if vals else 1


def gui(interval: float = 5.0) -> int:
    """Live dashboard on the Skia render engine.

    The engine owns the window, the widget tree and the frame loop; this app owns
    the bus (already started by build_bus) and the `invoke` runner. Telemetry is
    pushed from the SAME `feed()` daemon the ComposeUX shell uses as a driver hook,
    so the standalone and shell-hosted paths share one publish loop.
    """
    from composeio.render.engine.parser import parse_widget_tree
    from composeio.render.engine.runtime import RenderApp
    bus, cfg = build_bus()
    comp = parse_widget_tree(cfg)
    comp.scene_dir = str(HERE)      # bundled relative assets resolve off the scene
    # The CTk root auto-grew to its content and was user-resizable; the engine sizes
    # from design.width/height, so give the scene a roomy default when it declares none.
    if not cfg.get("design", {}).get("width"):
        comp.width, comp.height = 1180, 860
    comp.resizable = True
    app = RenderApp(comp, bus=bus, invoke=invoke_action)
    threading.Thread(target=feed, args=(app.bus, interval), daemon=True).start()
    app.run()
    return 0


def feed(bus, interval: float = 5.0) -> None:
    """ComposeUX-shell driver hook: poll the live soak and publish to the bus, forever.
    Lets the shell host this app's live data without app-specific wiring in the shell."""
    while True:
        try:
            for k, v in poll().items():
                bus.publish_state(k, v, source="hud")
        except Exception:
            pass
        time.sleep(interval)


def main() -> None:
    if "--dry-run" in sys.argv:
        sys.exit(dry_run())
    if "--fire" in sys.argv:
        i = sys.argv.index("--fire")
        tool = sys.argv[i + 1] if i + 1 < len(sys.argv) else "fleetsoak.console_tail"
        sys.exit(fire(tool))
    sys.exit(gui())


if __name__ == "__main__":
    main()
