#!/usr/bin/env python3
"""MinnowBoard Bring-up — a ComposeUX control surface over fleetconsole provisioning.

Form fields (hostname, a locally-hashed password, option checkboxes) publish to bringup/*
namespaces; the Provision button is an mcp_action that resolves those namespaces into
fleetconsole.provision_run params. The plaintext password is hashed by the secret field
(openssl $6$) and only its hash reaches this driver — the agent never holds the credential.

Run modes:
  python3 bringup.py --plan        # headless: assemble+validate the provision params (safe)
  python3 bringup.py               # GUI form (needs a display)

Live provisioning is GATED: the driver validates + assembles the plan and refuses to fire
the serial install unless FLEET_BRINGUP_LIVE=1 (and fleetnode is powered on). This keeps the
default path safe and headless-verifiable.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # .../composeio
sys.path.insert(0, str(REPO / "python"))

CONFIG = HERE / "minnow-bringup.compose.yaml"          # MAX surface (default)
CONFIG_TURBOT = HERE / "turbot-bringup.compose.yaml"    # Turbot surface (--turbot)
#: The bench host is CONFIGURATION: FLEET_BENCH_HOST (neutral default benchnode.local).
_BENCHNODE = "ssh -o ConnectTimeout=8 " + os.environ.get("FLEET_BENCH_HOST", "benchnode.local")


def _opts(params: dict) -> dict:
    """Collect opt_* params into a plain {name: on/off} map."""
    return {k[len("opt_"):]: v for k, v in params.items() if k.startswith("opt_")}


def _capture_spec(args: dict, params: dict) -> None:
    """Auto-capture the device rebuild spec to the bus on provision — so provisioning a board
    IS the act of recording how to rebuild it (incl. the $6$ hash just used). MERGES into any
    existing device doc (won't clobber curated fields). Best-effort: never fail a provision
    because the doc store is unreachable.
    """
    host = (params.get("hostname") or "").strip()
    if not host:
        return
    try:
        from composeio.resolve import resolve_doc, put_doc
        try:
            doc = resolve_doc(f"bus://fleet_devices/{host}.json")
            for k in ("_id", "_created_at", "_updated_at"):
                doc.pop(k, None)
        except Exception:
            doc = {"device": host, "role": "fleet MinnowBoard"}
        prov = doc.setdefault("provision", {})
        prov["recipe"] = args.get("recipe")
        prov["serial_device"] = args.get("device")
        prov["params"] = {k: v for k, v in params.items() if k == "hostname" or k.startswith("opt_")}
        if params.get("password_crypted"):
            prov["password_crypted"] = params["password_crypted"]
        doc.setdefault("surface", {"collection": "surfaces", "doc_id": "minnow-bringup"})
        doc["last_provisioned"] = "auto-captured by bring-up pane"
        put_doc("fleet_devices", host, doc)
    except Exception:
        pass


def _provision_run(args: dict, on_event) -> None:
    """The fleetconsole.provision_run adapter for this surface.

    Receives args whose ${ns} refs are already resolved by the CommandWidget — so
    `password_crypted` is a $6$ HASH, never plaintext. Validates, assembles the plan, and
    (unless FLEET_BRINGUP_LIVE=1) stops short of driving the serial install.
    """
    on_event({"kind": "progress", "pct": 20, "phase": "validate"})
    params = dict(args.get("params") or {})
    host = (params.get("hostname") or "").strip()
    pw = params.get("password_crypted") or ""

    if not host:
        on_event({"kind": "result", "ok": False, "summary": "hostname is required"})
        return
    # Safety: never accept a plaintext password. Only a $6$ crypt hash (or empty => recipe
    # placeholder) may pass. If the secret field didn't hash, refuse loudly.
    if pw and not pw.startswith("$6$"):
        on_event({"kind": "result", "ok": False,
                  "summary": "password is not a $6$ hash — refusing (secret field must hash locally)"})
        return

    opts = _opts(params)
    _capture_spec(args, params)   # provisioning records its own rebuild spec to the bus
    # Redacted representation — the hash length only, never the value, and never plaintext.
    pw_repr = f"$6$…({len(pw)} chars)" if pw else "(recipe placeholder)"
    plan = (f"recipe={args.get('recipe')} device={args.get('device')} "
            f"host={host} pw={pw_repr} opts={opts}")

    if os.environ.get("FLEET_BRINGUP_LIVE") != "1":
        on_event({"kind": "result", "ok": True, "blocked": True,
                  "summary": f"plan ready (gated): {plan}",
                  "detail": "set FLEET_BRINGUP_LIVE=1 with fleetnode powered on to drive the serial install"})
        return

    # LIVE: hand the assembled params to fleetconsole on benchnode (drives the serial install).
    on_event({"kind": "progress", "pct": 40, "phase": "provision"})
    try:
        # fleetconsole runs on benchnode; provision_run reads params as JSON on stdin.
        import json
        payload = json.dumps({"recipe": args.get("recipe"), "device": args.get("device"),
                              "params": params, "live": True})
        out = subprocess.run(
            f"{_BENCHNODE} 'cd ~/fleetconsole && PYTHONPATH=src "
            f".venv/bin/python -m fleetconsole.provision_cli'",
            input=payload, shell=True, capture_output=True, text=True, timeout=1800)
        ok = out.returncode == 0
        on_event({"kind": "result", "ok": ok,
                  "summary": (f"{host}: provision {'complete' if ok else 'failed'}"),
                  "detail": (out.stdout or "") + (out.stderr or "")})
    except Exception as exc:
        on_event({"kind": "result", "ok": False, "summary": f"provision error: {exc}"})


# ---- optional power control (reuses fleetpower's guarded HA primitives) -------
# Import fleetpower's module directly so the outlet ALLOWLIST stays single-sourced
# (fleetnode -> switch.switch5 lives only in fleetpower/config.yaml). No FastMCP is pulled;
# only the module-level HA helpers are used.
_FP = None


def _fleetpower():
    global _FP
    if _FP is None:
        import importlib.util
        fp = Path(os.environ.get("MAKER_HOME", str(Path.home() / "maker"))) / "fleetpower/mcp_server.py"
        spec = importlib.util.spec_from_file_location("fleetpower_srv", fp)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FP = (mod, mod.load_config())
    return _FP


def _plug_entity(mod, cfg: dict, target, *, guard: bool) -> str:
    """Resolve a target (alias | plug number | entity_id) to a switch entity_id.

    A bare integer N -> switch.switch{N}, permitted for MUTATION only if N is in fleetpower
    config `provisioning_plugs` (the declared fleet-DUT plug set) or already an allowlisted
    outlet. Read-only (guard=False) resolves any plug#. Non-numeric targets defer to
    fleetpower's strict allowlist guard.
    """
    if target in (None, ""):
        raise ValueError("no plug/target specified (enter the Kasa plug #)")
    reg = mod._registered(cfg)
    if target in reg:                                   # alias, e.g. fleetnode
        return reg[target]
    s = str(target).strip()
    if s.isdigit():
        n = int(s)
        entity = f"switch.switch{n}"
        if not guard:                                   # read-only: any plug#
            return entity
        allowed = set(cfg.get("provisioning_plugs") or [])
        if n in allowed or entity in reg.values():
            return entity
        raise ValueError(
            f"plug #{n} ({entity}) not provisionable — add {n} to fleetpower/config.yaml "
            f"provisioning_plugs {sorted(allowed)} (keeps non-fleet plugs safe)")
    return mod._resolve_guarded(cfg, target)            # strict for named entities


def _power(action: str):
    """Build a power adapter for on|off|status against a plug#/alias/entity."""
    def run(args: dict, on_event) -> None:
        target = args.get("target")
        on_event({"kind": "progress", "pct": 30, "phase": action})
        try:
            mod, cfg = _fleetpower()
            if action == "status":
                entity = _plug_entity(mod, cfg, target, guard=False)
                st = mod.ha_state(cfg, entity).get("state")
                on_event({"kind": "result", "ok": True, "state": st,
                          "summary": f"plug {target} ({entity}): {st}", "detail": f"{entity} = {st}"})
                return
            entity = _plug_entity(mod, cfg, target, guard=True)
            mod.ha_service(cfg, "switch", "turn_on" if action == "on" else "turn_off", entity)
            on_event({"kind": "result", "ok": True,
                      "summary": f"plug {target} -> power {action} ({entity})"})
        except Exception as exc:
            on_event({"kind": "result", "ok": False, "summary": f"power {action} failed: {exc}"})
    return run


# tool -> (adapter, destructive?) — the surface's allowlist (MCPA-5)
_ACTIONS = {
    "fleetconsole.provision_run": (_provision_run,   True),
    "fleetpower.power_on":        (_power("on"),     False),  # benign: just applies mains
    "fleetpower.power_off":       (_power("off"),    True),   # interrupts a running board
    "fleetpower.power_status":    (_power("status"), False),
}


def invoke_action(tool: str, args: dict, on_event) -> None:
    entry = _ACTIONS.get(tool)
    if entry is None:
        on_event({"kind": "result", "ok": False, "summary": f"REFUSED: '{tool}' not allowlisted"})
        return
    fn, _destructive = entry
    threading.Thread(target=lambda: fn(dict(args), on_event), daemon=True).start()


# When True, the pane fetches its surface FRESH from the bus (doc store) instead of the
# local file — the DP-CIO-23 "widget bindings live in bus storage" path. Set by --from-bus.
_FROM_BUS = False
_SURFACE_DOC = "bus://surfaces/minnow-bringup.json"


def _load_cfg():
    from composeio.config import load_config
    from composeio.render.engine.components import expand_components
    if _FROM_BUS:
        from composeio.resolve import resolve_doc
        cfg = resolve_doc(_SURFACE_DOC)               # fetch the surface from the bus
        for k in ("_id", "_created_at", "_updated_at"):
            cfg.pop(k, None)                          # strip doc-store envelope fields
        print(f"[surface] loaded fresh from the bus: {_SURFACE_DOC}", file=sys.stderr)
    else:
        cfg = load_config(str(CONFIG))
    return expand_components(cfg)


def build_bus():
    from composeio.bus import ComposeIOBus
    cfg = _load_cfg()
    bus = ComposeIOBus(bus_id="minnow-bringup")
    bus.start()
    for nsdef in cfg.get("namespaces", []):
        bus.register_namespace(nsdef["name"], nsdef.get("state_engine", "json"),
                               label=nsdef.get("label"))
    return bus, cfg


def gui() -> int:
    """The bring-up form on the Skia render engine.

    The engine owns the window/widget tree/frame loop; this driver owns the bus and
    the `invoke` runner (the mcp_action allowlist above). The secret field hashes
    locally inside the engine's TextInputWidget, exactly as the CTk field did — the
    plaintext still never reaches this module.
    """
    from composeio.render.engine.parser import parse_widget_tree
    from composeio.render.engine.runtime import RenderApp
    bus, cfg = build_bus()
    comp = parse_widget_tree(cfg)
    comp.scene_dir = str(HERE)
    # The CTk root auto-grew to its content and was user-resizable; the engine sizes
    # from design.width/height, so give the scene a roomy default when it declares none.
    if not cfg.get("design", {}).get("width"):
        comp.width, comp.height = 1080, 860
    comp.resizable = True
    RenderApp(comp, bus=bus, invoke=invoke_action).run()
    return 0


def _placeholders(cfg: dict) -> dict:
    """namespace -> a widget placeholder (the surface's convention default), so --plan can
    exercise ANY surface generically instead of hardcoding one form's field set."""
    out: dict = {}
    for m in cfg.get("modules", []):
        for w in m.get("widgets", []):
            ns = w.get("namespace")
            if ns and w.get("placeholder"):
                out[ns] = str(w["placeholder"])
    return out


def plan() -> int:
    """Headless: exercise the resolve+assemble+validate path without a display.

    Config-driven: publishes a sensible default for every declared namespace (binary -> off;
    text -> the widget's placeholder; a couple flipped 'on' to exercise the opt path), then
    resolves the CommandWidget args exactly as the Provision button would. Works for the MAX
    surface (default) and the Turbot surface (--turbot) without per-form special-casing."""
    # The SAME two primitives the live widgets use — the engine's CommandWidget
    # resolves ${ns} with resolve_ns_refs, and its TextInputWidget (secret: true)
    # hashes with hash_password. --plan exercises the production path, not a copy.
    from composeio.render.widgets.command import resolve_ns_refs
    from composeio.render.widgets.text_input import hash_password
    bus, cfg = build_bus()
    ph = _placeholders(cfg)
    # Simulate a filled-in form (as the input widgets would publish).
    for nsdef in cfg.get("namespaces", []):
        name, eng = nsdef["name"], nsdef.get("state_engine", "json")
        if eng == "binary":
            bus.publish_state(name, "off", source="test")
        elif name in ph:
            bus.publish_state(name, ph[name], source="test")
    # Required + interesting overrides: a real hostname, a real $6$ hash, and (Turbot) exercise
    # the kiosk profile path so the full param set is populated.
    bus.publish_state("bringup/hostname", ph.get("bringup/hostname", os.environ.get("FLEET_BRINGUP_DEFAULT_HOST", "fleetnode")), source="test")
    bus.publish_state("bringup/pw_hash", hash_password("correct horse battery staple"), source="test")
    if any(n["name"] == "bringup/session_profile" for n in cfg.get("namespaces", [])):
        bus.publish_state("bringup/session_profile", "kiosk", source="test")
    if any(n["name"] == "bringup/opt_avahi" for n in cfg.get("namespaces", [])):
        bus.publish_state("bringup/opt_avahi", "on", source="test")
        bus.publish_state("bringup/opt_sshkey", "on", source="test")
    # Pull the CommandWidget's args template out of the config and resolve like the widget does.
    cmd = next(w for m in cfg["modules"] for w in m.get("widgets", [])
               if w.get("type") == "CommandWidget")
    resolved = resolve_ns_refs(dict(cmd["binding"]["args"]), bus.get_state)
    events = []
    invoke_action(cmd["binding"]["tool"], resolved, events.append)
    import time
    for _ in range(20):
        if any(e.get("kind") == "result" for e in events):
            break
        time.sleep(0.2)
    print("=== resolved provision args (as the Provision button would invoke) ===")
    pw = resolved["params"]["password_crypted"]
    safe = {**resolved["params"], "password_crypted": f"$6$…({len(pw)} chars)"}
    print("  params:", safe)
    print("=== runner events ===")
    for e in events:
        print("  ", e)
    result = next((e for e in events if e.get("kind") == "result"), None)
    return 0 if result and result.get("ok") else 1


def power_cli(action: str, target: str = "5") -> int:
    """Headless: drive a plug (by #/alias) via the same adapter the pane uses."""
    events: list[dict] = []
    invoke_action(f"fleetpower.power_{action}", {"target": target}, events.append)
    import time
    for _ in range(30):
        if any(e.get("kind") == "result" for e in events):
            break
        time.sleep(0.3)
    for e in events:
        print("  ", e)
    result = next((e for e in events if e.get("kind") == "result"), None)
    return 0 if result and result.get("ok") else 1


def main() -> None:
    global _FROM_BUS, CONFIG
    if "--turbot" in sys.argv:
        CONFIG = CONFIG_TURBOT    # the Turbot surface (minnowboard-turbot-debian13 param model)
    if "--config" in sys.argv:
        i = sys.argv.index("--config")
        if i + 1 < len(sys.argv):
            CONFIG = Path(sys.argv[i + 1]).resolve()
    if "--from-bus" in sys.argv:
        _FROM_BUS = True          # fetch the surface fresh from the bus (doc store)
    if "--plan" in sys.argv:
        sys.exit(plan())
    if "--power" in sys.argv:
        i = sys.argv.index("--power")
        action = sys.argv[i + 1] if i + 1 < len(sys.argv) else "status"
        plug = sys.argv[i + 2] if i + 2 < len(sys.argv) and not sys.argv[i + 2].startswith("-") else "5"
        sys.exit(power_cli(action, plug))
    sys.exit(gui())


if __name__ == "__main__":
    main()
