"""Headless driver + proof for the R503 binding UI.

Stands up ONE shared ComposeIOBus, the FingerprintCastingBinder (service +
reference device), and the render scene bound to it; drives the full binding
scenario over the bus (add people, capture templates, define RBAC, cast,
START GAME); captures a proof PNG; and asserts the roster push fired.

Also exercises the WIDGET path directly (text_input.commit -> emit_button.on_tap
-> binder -> view) so the proof is not just "the binder works" but "the widgets
are wired to the binder".

Run:
    PYTHONPATH=python:services python3 -m composeio.render.examples.fingerprint.drive
"""

from __future__ import annotations

import os
import sys

from composeio.bus import ComposeIOBus
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.engine.runtime import RenderApp

# self-registers `info_list`; also loaded via the scene's widget_modules:
from composeio.render.widgets import fingerprint_panel  # noqa: F401
from composeio.render.examples.fingerprint.binder import (
    FingerprintCastingBinder, DEFAULT_MODULE)

_SCENE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fingerprint_casting.compose.yaml")


def _find_widget(root, wid):
    if getattr(root, "widget_id", None) == wid:
        return root
    for ch in getattr(root, "children", []) or []:
        found = _find_widget(ch, wid)
        if found is not None:
            return found
    return None


def build(bus=None):
    """Wire bus + binder + app, return (app, binder, bus).  Shared bus so the
    binder's publishes reach the scene's subscribers."""
    bus = bus or ComposeIOBus()
    binder = FingerprintCastingBinder(bus)
    binder.wire()
    comp = parse_compose_yaml(_SCENE)
    app = RenderApp(comp, bus=bus, windowed=False)
    app.start()
    return app, binder, bus


def _cmd(bus, ns, payload):
    """Publish a command exactly as the scene's emit_button would."""
    bus.ensure_namespace(ns, engine_config="json")
    bus.publish_state(ns, payload, source="drive")


def run_scenario(bus):
    """The operator's session, driven as bus commands."""
    people = [("adama", "William Adama"), ("roslin", "Laura Roslin"),
              ("starbuck", "Kara Thrace")]
    for pid, name in people:
        _cmd(bus, "fp/cmd/add_person", {"person_id": pid, "display_name": name})
    # two templates each (the real enroll ceremony on the reference device);
    # explicit ordinals keep the two capture commands distinct on the bus.
    for pid, _ in people:
        _cmd(bus, "fp/cmd/capture_template", {"person": pid, "ordinal": 1})
        _cmd(bus, "fp/cmd/capture_template", {"person": pid, "ordinal": 2})
    # RBAC: two roles, then characters
    _cmd(bus, "fp/cmd/define_role",
         {"role": "command", "capabilities": "open_hatch, jump, launch"})
    _cmd(bus, "fp/cmd/define_role",
         {"role": "civilian", "capabilities": ""})
    _cmd(bus, "fp/cmd/define_character", {"character": "commander", "role": "command"})
    _cmd(bus, "fp/cmd/define_character", {"character": "president", "role": "civilian"})
    _cmd(bus, "fp/cmd/define_character", {"character": "viper_pilot", "role": "command"})
    # cast people to characters
    _cmd(bus, "fp/cmd/cast", {"person": "adama", "character": "commander"})
    _cmd(bus, "fp/cmd/cast", {"person": "roslin", "character": "president"})
    _cmd(bus, "fp/cmd/cast", {"person": "starbuck", "character": "viper_pilot"})


def prove_widget_path(app, binder, bus):
    """Prove the actual widgets drive the binder: type into the person fields,
    commit, tap Add Person, and confirm a new person landed in the store."""
    root = app.comp.root
    f_pid = _find_widget(root, "f_person_id")
    f_name = _find_widget(root, "f_display_name")
    b_add = _find_widget(root, "b_add_person")
    assert f_pid is not None and b_add is not None, "scene widgets missing"
    f_pid.text = "tigh"
    f_pid.commit()                     # publishes fp/in/person_id
    f_name.text = "Saul Tigh"
    f_name.commit()
    app.drain_updates()                # deliver the field echoes to the button
    before = "tigh" in binder.store.persons
    b_add.on_tap(0, 0)                 # emits fp/cmd/add_person from live values
    after = "tigh" in binder.store.persons
    return (not before) and after


def main(out_png="/tmp/fp_casting.png"):
    app, binder, bus = build()

    # 1) drive the whole binding scenario over the bus
    run_scenario(bus)

    # 2) prove the real widget -> binder path
    widget_ok = prove_widget_path(app, binder, bus)

    # 3) START GAME — fires the roster push
    _cmd(bus, "fp/cmd/start_game", {"module": DEFAULT_MODULE})

    # 4) capture the populated panel
    app.render_headless(out_png)

    # ---- assertions (machine-green) ----
    persons = sorted(binder.store.persons)
    push = binder.last_push_msg
    ok = True
    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")

    check("widget -> binder path added a person", widget_ok)
    check("4 people in the superset (3 scenario + 1 widget)", len(persons) == 4)
    check("each person has 2 templates keyed by (person, ordinal)",
          all(len(binder.store.ordinals_for(p)) == 2
              for p in ("adama", "roslin", "starbuck")))
    check("roster push FIRED on the bus", push is not None)
    check("push carries a template_set_uuid",
          bool(push and push.get("template_set_uuid")))
    check("push entry_count == 3 cast persons * 2 templates == 6",
          bool(push and push.get("entry_count") == 6))
    check("NO slot anywhere in the push",
          bool(push) and "slot" not in str(push).lower())
    check("device acked the push",
          binder.svc.publisher.ack(binder.device_id) == "loaded")
    check("device converged (held uuid == desired, learned by heartbeat)",
          binder.svc.publisher.is_converged(binder.device_id))
    check("capture PNG written", os.path.exists(out_png) and
          os.path.getsize(out_png) > 0)

    print(f"\nPNG: {out_png}")
    print(f"people: {persons}")
    if push:
        print(f"push: uuid={push['template_set_uuid'][:12]}… "
              f"entries={push['entry_count']} "
              f"roles={list((push.get('role_capability_table') or {}).keys())}")
    print("\n" + ("ALL GREEN" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/fp_casting.png"))
