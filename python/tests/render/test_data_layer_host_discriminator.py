"""The in-process console data layer is hosted ONLY by the CONSOLE scene, never
by another `--transports` scene that happens to share the architect id.

[CXC-IC-SELF-IDENTITY] [DEC-INPROCESS-SOURCES] — regression guard for
DEFECT-LAUNCH-BAR-RIBBON-CO-HOSTS: the runtime gate that starts the session-owned
publishers (`self_publisher`, `transcript_publisher`, the monitors + launchers)
used to fire for ANY `--transports` launch as long as
`compose_projects/intelligence/console/data_sources.py` existed on disk — it never consulted the
SCENE.  So the Launch Bar ribbon (`launch_bar.compose.yaml`, a pure tile scene
with NO transcript-dependent sources in its own YAML), launched with
`--transports` under the same architect id, would ALSO bring up the console data
layer and could WIN the per-role singleton flocks
(`composeio-self-publisher-architect.lock`, ...) away from the real console —
darkening the console's telemetry on a ribbon-only cycle and contradicting the
/bounce-console contract ("the Launch Bar is a pure tile scene with NO
transcript-dependent in-process sources").

The fix is a DECLARATIVE, fail-safe opt-in: a scene hosts the data layer ONLY if
its `design.data_sources: true` (parsed to `comp.data_sources`).  The console
opts in; the ribbon does not; absent => off.  These tests assert the gate keys on
that declared SCENE intent, broker-free (no data layer is ever actually started).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from composeio.render.engine import runtime
from composeio.render.engine.parser import parse_compose_yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CONSOLE = _REPO_ROOT / "compose_projects" / "intelligence" / "console" / "console.compose.yaml"
_RIBBON = _REPO_ROOT / "apps/compose_platform_launch_bar" / "launch_bar.compose.yaml"


class _StubApp:
    """The runtime data-layer gate reads only ``app.comp`` — a real SDL window is
    never needed to exercise the discriminator."""

    def __init__(self, comp):
        self.comp = comp


# --- the SCENE decides, not the agent-id or the mere presence of --transports ---
@pytest.mark.parametrize(
    "scene, expect_host",
    [
        (_CONSOLE, True),    # the console DECLARES design.data_sources: true
        (_RIBBON, False),    # the ribbon is a pure tile scene — no opt-in
    ],
    ids=["console-hosts", "ribbon-does-not-host"],
)
def test_scene_hosts_data_layer_is_declarative(scene, expect_host):
    app = _StubApp(parse_compose_yaml(str(scene)))
    assert runtime._scene_hosts_data_layer(app) is expect_host


def test_ribbon_launch_never_starts_the_data_layer(monkeypatch):
    """`_maybe_start_inprocess_sources` on the ribbon scene returns None WITHOUT
    ever loading `data_sources.py` — proving a `--transports` ribbon can neither
    acquire the publisher flocks nor bring up the console's telemetry layer.  The
    guard fires BEFORE any importlib work, so the assert is broker- and
    filesystem-independent."""
    loaded = {"data_sources": False}
    import importlib.util as _ilu
    real = _ilu.spec_from_file_location

    def _spy(name, location, *a, **k):
        if str(location).endswith("data_sources.py"):
            loaded["data_sources"] = True
        return real(name, location, *a, **k)

    monkeypatch.setattr(_ilu, "spec_from_file_location", _spy)

    app = _StubApp(parse_compose_yaml(str(_RIBBON)))
    assert runtime._maybe_start_inprocess_sources(app) is None
    assert loaded["data_sources"] is False, \
        "ribbon must NOT even load data_sources.py — the data layer is console-only"


def test_console_scene_is_the_declared_host():
    """The console scene DOES declare the opt-in — the invariant cuts only the
    ribbon, it never weakens the console's own non-droppable data layer
    ([DEC-INPROCESS-SOURCES])."""
    app = _StubApp(parse_compose_yaml(str(_CONSOLE)))
    assert runtime._scene_hosts_data_layer(app) is True


def test_absent_flag_is_fail_safe():
    """A scene that declares no design block at all does NOT host the data layer —
    the opt-in defaults off, so a NEW `--transports` scene can never co-host by
    accident."""
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"app": {"id": "some-other-scene"},
                              "design": {"title": "x"},
                              "widgets": []})
    assert comp.data_sources is False
    assert runtime._scene_hosts_data_layer(_StubApp(comp)) is False
