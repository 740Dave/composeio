#!/usr/bin/env python3
"""[CXC-IH-5A] Consistency guard for the console's id-scoping hard-switch.

The migration's invariant: on the ARCHITECT console every PER-INSTANCE feed (the self_publisher
readings + the transcript_publisher feeds) is bound under `self/architect/*` / `thread/architect/*`,
while the console→intelligence INTENT/CONFIG channels — and OTHER daemons' projections — stay BARE.
If someone later adds a widget bound to a bare `self/<reading>` or `thread/<reading>` (a fresh
cross-instance MERGE vector), this test FAILS and names it, so the regression is caught in CI rather
than as provisionio's narration bleeding into the architect console.

The check parses the REAL scene (not a fixture) and inspects every `self/*` / `thread/*` namespace
the console SUBSCRIBES. Each must be either id-scoped (`self/architect/*` / `thread/architect/*`) or
in the explicit BARE allowlist below (with the reason it is exempt).

Run:  python3 compose_projects/intelligence/console/test_id_scoping.py --selftest
      pytest compose_projects/intelligence/console/test_id_scoping.py
"""
from __future__ import annotations

import os
import sys

SCENE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "console.compose.yaml")
CONSOLE_ID = "architect"          # the HOST whose transcript this console tails

# Namespaces DELIBERATELY left bare (global). Each is NOT a per-instance reading of THIS console:
#  - intent/config channels: a widget must reach the intelligence at a stable, well-known topic;
#    id-scoping them would break the actuation path.
#  - other daemons' projections: published by a SEPARATE daemon out of this slice — flagged as
#    follow-on cross-instance vectors ([CXC-IH-5A] migration), not silently scoped here.
BARE_ALLOWLIST = {
    # intent / config (console -> intelligence)
    "self/model/select":   "intent: model/effort picker -> intelligence",
    "self/account/signin": "intent: Account Sign in -> intelligence",
    # ambiguous / other-daemon projections (FLAGGED follow-ons)
    "self/meta":         "meta_publisher projection + config surface (AMBIGUOUS — flagged)",
    "self/deliverables": "deliverable_publisher projection (out of slice — flagged follow-on)",
    "self/away_watches": "away_watch_publisher projection (separate daemon — out of slice)",
    "self/projects":     "project_validator projection (separate daemon — out of slice)",
}


def _declared_ids(scene_path: str = SCENE) -> list[str]:
    """Every id the scene DECLARES in `instances:` ([CXC-FC-UNIT])."""
    import yaml
    try:
        with open(scene_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    except OSError:
        return []
    return [str(e["id"]) for e in (cfg.get("instances") or [])
            if isinstance(e, dict) and e.get("id")]


def _scoped_prefixes(scene_path: str = SCENE) -> tuple[str, ...]:
    """The id-scoped prefixes this scene may bind.

    [CXC-FC-UNIT]: the console hosts N Intelligences now, so "id-scoped" means scoped to
    ANY DECLARED id, not to the host's alone.  `thread/architect-hosted/narration` is a
    pane's narration leg — properly scoped, just to a different declared instance — and a
    guard that only knew `architect` reported it as UNSCOPED, which is the opposite of the
    truth and would have pushed a builder to un-scope it.
    The invariant is unchanged: a `self/*` or `thread/*` topic must name SOMEBODY, so
    another intelligence's readings can never bleed into this console ([CXC-IH-5A]).
    """
    ids = [CONSOLE_ID] + [i for i in _declared_ids(scene_path) if i != CONSOLE_ID]
    return tuple(f"{fam}/{i}/" for i in ids for fam in ("self", "thread"))


def audit_scene(scene_path: str = SCENE) -> list[str]:
    """Return a list of VIOLATION strings (empty == consistent). A violation is a subscribed
    `self/*` or `thread/*` namespace that is neither id-scoped nor in the bare allowlist."""
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "python"))
    from composeio.render.engine.parser import parse_compose_yaml

    comp = parse_compose_yaml(scene_path)
    subscribed = sorted({ns for ns, _w in comp.subscriptions})
    scoped = _scoped_prefixes(scene_path)
    violations: list[str] = []
    for ns in subscribed:
        if not (ns.startswith("self/") or ns.startswith("thread/")):
            continue
        if ns.startswith(scoped):
            continue
        if ns in BARE_ALLOWLIST:
            continue
        violations.append(
            f"UNSCOPED per-instance binding {ns!r} — id-scope it to one of "
            f"{sorted(scoped)}, or add it to BARE_ALLOWLIST with a reason if it is "
            f"genuinely global.")
    return violations


def scoped_subscriptions(scene_path: str = SCENE) -> list[str]:
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "python"))
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(scene_path)
    return sorted({ns for ns, _w in comp.subscriptions if ns.startswith(_scoped_prefixes())})


def test_no_unscoped_per_instance_bindings():
    violations = audit_scene()
    assert not violations, "\n".join(violations)


def test_the_scoped_set_is_nonempty_and_id_carrying():
    scoped = scoped_subscriptions()
    # the hard-switch actually happened: the console binds its own identity's feeds.
    assert scoped, "expected id-scoped self/architect/* + thread/architect/* subscriptions"
    # EVERY scoped topic names SOME DECLARED id — not necessarily the host's.  With N
    # panes the console legitimately binds another declared instance's narration
    # ([CXC-FC-UNIT]); what must never happen is a `self/*` or `thread/*` topic naming
    # NOBODY, because that is the shared-broker merge [CXC-IH-5A] closed.
    known = {CONSOLE_ID, *_declared_ids()}
    assert all(any(f"/{i}/" in ns for i in known) for ns in scoped), scoped
    # spot-check the load-bearing ones are present and scoped.
    # thread/architect/diagram dropped with the retired Canvas tab
    # ([DEC-CANVAS-TAB-RETIRED], 2026-07-24) — its only consumer is gone.
    # `thread/architect/narration` is still here — it is the HOST pane's narration leg
    # now rather than a leg of a single bespoke thread, and it must not go missing in
    # either form.
    for want in ("self/architect/context_pct", "self/architect/compactions",
                 "self/architect/registered", "self/architect/rate_limit",
                 "self/architect/avatar/expression", "thread/architect/narration"):
        assert want in scoped, f"expected {want} in the id-scoped subscription set: {scoped}"


def _selftest() -> int:
    violations = audit_scene()
    scoped = scoped_subscriptions()
    print("[CXC-IH-5A] console id-scoping consistency check")
    print(f"  id-scoped per-instance subscriptions ({len(scoped)}):")
    for ns in scoped:
        print(f"    {ns}")
    print(f"  bare intent/other-daemon channels intentionally left global "
          f"({len(BARE_ALLOWLIST)}): {sorted(BARE_ALLOWLIST)}")
    if violations:
        print(f"\n{len(violations)} VIOLATION(S):")
        for v in violations:
            print(f"  FAIL {v}")
        return 1
    print("\nPASS: every per-instance self/thread binding is id-scoped; "
          "intent/config + other-daemon channels remain bare (allowlisted).")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv or len(sys.argv) == 1:
        raise SystemExit(_selftest())
