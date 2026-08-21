"""Enforcing regression test for DEFECT-AWAY-WATCH-DASHBOARD-ENTRY-NOT-0317D0 (A2).

The symptom: clicking the "Away 2026-07-26 (maker)" away-watch row does nothing —
its report `.md` never opens. Attempt A1 added the widget's TYPED routing (a doc
target publishes `_app/doc/open`) and that was correct, but the defect SURVIVED:
the console scene pinned `_app/doc/open` to `scope: local`, and per DP-CIO-5 scope
resolution a local namespace is BARRED from the global (MQTT) transport — so the
OUT-OF-PROCESS `doc_viewer_launcher` daemon (which subscribes at
`composeio/global/_app/doc/open`) never received the tap. The publish happened; the
message died at the local/global scope gate downstream of the widget.

Root cause: implementation-oversight — the in-console doc_browser overlay
(in-process subscriber ⇒ local was correct) was refactored to the out-of-process
launcher ([DEC-DOCVIEWER-WINDOW], 27150d9) WITHOUT flipping the now-stale scope.

Two guards, so the CLASS cannot recur:
  * test_out_of_process_launcher_intents_are_not_local — the scene-level guard that
    would have caught THIS defect: every `_app/*/open` intent whose consumer is an
    out-of-process launcher daemon must NOT be declared `scope: local`. Parametrized
    off the launchers' own `INTENT_NS` (the source of truth) so a FUTURE launcher
    intent that gets pinned local fails loudly too — [CXC-RETRO-AWAYWATCH-OPEN-REACHES-LAUNCHER].
  * test_local_scope_never_reaches_global_transport — the MECHANISM proof (the wire
    behavior the guard protects): a `scope: local` publish never reaches the global
    transport; a `scope: global` one does. This is WHY the pin was fatal.
"""
from __future__ import annotations

import importlib.util
import os
import sys

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.bus import ComposeIOBus            # noqa: E402
from composeio.namespace import Scope             # noqa: E402
from composeio.transport import Transport         # noqa: E402

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console", "console.compose.yaml")
TOOLS = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools")

# The out-of-process launcher daemons that consume an `_app/*/open` intent over the
# GLOBAL transport. Each MUST have its intent namespace reach global (declared
# `scope: global`, or left undeclared so DP-CIO-5 AUTO-resolves it global) — never
# pinned `scope: local`. We read INTENT_NS from each launcher module so this list is
# the launchers' OWN source of truth, not a hand-kept copy (cross-implementation).
_LAUNCHER_FILES = [
    "doc_viewer_launcher.py",
    "dsd_viewer_launcher.py",
    "decisions_viewer_launcher.py",
]


def _launcher_intent_ns(fname: str) -> str:
    path = os.path.join(TOOLS, fname)
    spec = importlib.util.spec_from_file_location(fname[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.INTENT_NS


def _scene_namespace_scopes() -> dict:
    with open(SCENE, "r", encoding="utf-8") as fh:
        scene = yaml.safe_load(fh)
    out = {}
    for nsdef in scene.get("namespaces", []) or []:
        if isinstance(nsdef, dict) and "name" in nsdef:
            out[nsdef["name"]] = nsdef.get("scope")   # None ⇒ undeclared ⇒ AUTO
    return out


LAUNCHER_INTENTS = [_launcher_intent_ns(f) for f in _LAUNCHER_FILES]


@pytest.mark.parametrize("intent_ns", LAUNCHER_INTENTS)
def test_out_of_process_launcher_intents_are_not_local(intent_ns):
    """An intent consumed by an out-of-process launcher must reach the global
    transport — declared `scope: global`, or undeclared (AUTO ⇒ global). It must
    NEVER be pinned `scope: local`, which a local publish can never leave.
    [CXC-RETRO-AWAYWATCH-OPEN-REACHES-LAUNCHER]"""
    scopes = _scene_namespace_scopes()
    declared = scopes.get(intent_ns)  # None if undeclared (AUTO-resolves global)
    assert declared != "local", (
        f"{intent_ns!r} is declared `scope: local` in console.compose.yaml but its "
        f"consumer is an out-of-process launcher subscribing on "
        f"composeio/global/{intent_ns}; a local intent is barred from the global "
        f"transport, so the click is a silent no-op. Declare it `scope: global`.")


def test_doc_open_declared_global_explicitly():
    """The specific regression: `_app/doc/open` (the away-watch report + card Open ▸
    seam) must be declared `scope: global` now that doc_viewer_launcher is
    out-of-process. FAILS at `scope: local` (the shipped-but-broken state)."""
    scopes = _scene_namespace_scopes()
    assert scopes.get("_app/doc/open") == "global"


class _FakeGlobal(Transport):
    """A stand-in global transport that records the namespaces routed to it."""

    def __init__(self):
        self.received = []

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, msg):
        self.received.append(msg.namespace)

    def subscribe(self, *a, **k):
        pass

    def unsubscribe(self, *a, **k):
        pass

    def subscribe_all(self, *a, **k):
        pass

    # A @property, matching the real contract. This double previously declared
    # `def is_connected(self)`, which is precisely the bug
    # `Transport.__init_subclass__` now refuses: every caller reads
    # `transport.is_connected` as an ATTRIBUTE, so a method hands them an
    # always-truthy bound object and a dead link reports CONNECTED. A double that
    # violates the contract is worse than no double — it lets the real bug pass.
    @property
    def is_connected(self):
        return True


@pytest.mark.parametrize("scope,reaches_global", [
    (Scope.LOCAL, False),
    (Scope.GLOBAL, True),
])
def test_local_scope_never_reaches_global_transport(scope, reaches_global):
    """The wire mechanism the scene guard protects (DP-CIO-5 / _resolve_scope):
    a `scope: local` publish is delivered ONLY in-process and never reaches the
    global transport an out-of-process launcher subscribes on; a `scope: global`
    publish does. This is precisely why pinning `_app/doc/open` local made the tap
    a dead click even though the widget published it correctly."""
    fg = _FakeGlobal()
    bus = ComposeIOBus(global_transport=fg)
    bus.ensure_namespace("_app/doc/open", engine_config="json", scope=scope)
    bus.publish_state(
        "_app/doc/open",
        "docs/watch_reports/away-watch-2026-07-26-report.md",
        source="away_watch_view")
    assert ("_app/doc/open" in fg.received) is reaches_global
