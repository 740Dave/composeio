"""
Compliance tests — the ComposePlatform Launch Bar core slice
([SPEC:CXC-LAUNCHBAR]).

Covers the ribbon + tile model ([CXC-LB-RIBBON]/[CXC-LB-TILE]/[CXC-LB-ROSTER]/
[CXC-LB-ACTIVE]) and the generalized launcher's intent wiring
([CXC-LB-INTENT-BINDING]/[CXC-LB-LAUNCHER]/[CXC-LB-INTENT-TABLE]) — the
machine leg of [CXC-LB-VERIFY] LB-C1/C2/C3/C4/C5/C6 for what this slice builds
(the two storage viewers, LB-C7/C8/C9, are a separate slice).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]          # .../python
_REPO_ROOT = _PKG_ROOT.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.widgets.launch_tile import LaunchTileWidget

SCENE = _REPO_ROOT / "apps/compose_platform_launch_bar" / "launch_bar.compose.yaml"

# The ratified roster ([CXC-LB-ROSTER]): (target, label, enabled).
# 7 active / 0 disabled (D7, David 2026-07-29): the two storage viewers landed
# ([CXC-DOCNAV-APP]/[CXC-BUSNAV-APP]) — every tile now launches a real app.
ROSTER = [
    ("designer", "Designer", True),
    ("harness", "AI Harness", True),
    ("composer", "Composer", True),
    ("graph", "Graph Viewer", True),
    ("docstore", "Document Storage", True),
    ("busstore", "Bus Storage", True),
    ("bus", "ComposeBus", True),
]


def _collect_tiles(widget, out):
    if isinstance(widget, LaunchTileWidget):
        out.append(widget)
    for ch in getattr(widget, "children", []) or []:
        _collect_tiles(ch, out)
    return out


@pytest.fixture(scope="module")
def tiles():
    comp = parse_compose_yaml(str(SCENE))
    return _collect_tiles(comp.root, [])


class _RecordCtx:
    """A minimal EngineContext stand-in that records publishes and never
    spawns — proves the tile PUBLISHES and does nothing else ([CXC-LB-TILE])."""

    def __init__(self):
        self.published = []
        self.assets = None

    def publish(self, namespace, value, source="widget"):
        self.published.append((namespace, value, source))

    def request_redraw(self, *_a, **_k):
        pass


# ---------------------------------------------------------------------------
# LB-C1 — ribbon renders the 7 tiles in roster order, labels match.
# ---------------------------------------------------------------------------
def test_scene_parses_and_has_seven_tiles(tiles):
    assert len(tiles) == 7, f"expected 7 launch tiles, got {len(tiles)}"


def test_roster_order_targets_labels(tiles):
    got = [(t.target, t.label) for t in tiles]
    want = [(target, label) for target, label, _ in ROSTER]
    assert got == want, f"roster order/labels drift: {got}"


def test_enabled_flags_match_slice(tiles):
    for t, (target, _label, enabled) in zip(tiles, ROSTER):
        assert t.enabled is enabled, f"{target} enabled={t.enabled}, want {enabled}"
        # only an enabled tile is a click-activation target ([CXC-LB-TILE]).
        assert t.interactive is enabled


def test_every_tile_icon_asset_exists(tiles):
    for t in tiles:
        assert t.icon, f"{t.target} has no icon src"
        p = _REPO_ROOT / t.icon
        assert p.is_file(), f"{t.target} icon missing on disk: {p}"


def test_intent_namespace_convention(tiles):
    # [CXC-LB-INTENT-TABLE]: every intent is `_app/<target>/open`.
    for t in tiles:
        assert t.intent_namespace == f"_app/{t.target}/open"


# ---------------------------------------------------------------------------
# LB-C1 (legibility reflow, David 2026-08-05) — the ribbon's absolute geometry
# is DERIVED from launch_tile's constants, and the scene still matches them.
#
# `pixel_stack` is absolute layout, so the scene carries literal offsets; this
# is the drift guard that makes "edit ICON_BOX / LABEL_FONT, re-materialize"
# a safe one-edit resize instead of a hunt through 7 hand-typed y values.
# ---------------------------------------------------------------------------
import yaml as _yaml                                            # noqa: E402
from composeio.render.widgets import launch_tile as LT          # noqa: E402


@pytest.fixture(scope="module")
def scene_raw():
    with open(SCENE, encoding="utf-8") as fh:
        return _yaml.safe_load(fh)


def test_icon_and_label_are_the_legible_sizes():
    """David 2026-08-05: 28px icons / 7.5pt labels were unreadable on the
    console's 4K glass.  These are the ratified targets."""
    assert LT.ICON_BOX == 64.0
    assert LT.LABEL_FONT == 12.0


def test_scene_ribbon_size_matches_constants(scene_raw):
    d = scene_raw["design"]
    assert (d["width"], d["height"]) == (LT.RIBBON_W, LT.RIBBON_H)
    stack = scene_raw["widgets"][0]
    assert stack["type"] == "pixel_stack"
    assert (stack["width"], stack["height"]) == (LT.RIBBON_W, LT.RIBBON_H)


def test_scene_logo_matches_constants(scene_raw):
    logo = scene_raw["widgets"][0]["children"][0]
    assert logo["id"] == "brand_logo"
    assert (logo["x"], logo["y"]) == (LT.LOGO_X, LT.LOGO_Y)
    assert (logo["width"], logo["height"]) == (LT.LOGO_BOX, LT.LOGO_BOX)


def test_scene_tile_offsets_match_constants(scene_raw):
    kids = [c for c in scene_raw["widgets"][0]["children"]
            if c["type"] == "launch_tile"]
    assert len(kids) == LT.TILE_COUNT
    for i, c in enumerate(kids):
        assert c["x"] == 0, c["id"]
        assert c["y"] == LT.tile_y(i), f"{c['id']} y drift"
        assert c["width"] == LT.DEFAULT_TILE_W, c["id"]
        assert c["height"] == LT.DEFAULT_TILE_H, c["id"]


def test_label_line_height_constant_is_the_measured_advance():
    """LABEL_LINE_H feeds DEFAULT_TILE_H, but draw() uses the MEASURED
    advance — they must agree or the reserved label band is wrong."""
    from composeio.render.skia_backend import SkiaCanvas
    c = SkiaCanvas()
    c.create_surface(64, 64)
    c.set_font("sans-serif", LT.LABEL_FONT, "bold")
    m = c.measure_text("Ag")
    assert m.ascent + m.descent + 1.0 == LT.LABEL_LINE_H


def test_every_label_fits_or_wraps_within_the_reserved_band(tiles):
    """No clipping and no overlap into the next tile: every label wraps to at
    most LABEL_LINES lines, each line fits the label column, and the last
    descent lands inside the tile ([CXC-LB-TILE])."""
    from composeio.render.skia_backend import SkiaCanvas
    c = SkiaCanvas()
    c.create_surface(int(LT.RIBBON_W), int(LT.DEFAULT_TILE_H))
    c.set_font("sans-serif", LT.LABEL_FONT, "bold")
    col_w = LT.DEFAULT_TILE_W - 2 * LT.LABEL_PAD
    m = c.measure_text("Ag")
    for t in tiles:
        lines = t._wrap_label(c, col_w)
        assert lines, t.label
        assert len(lines) <= LT.LABEL_LINES, f"{t.label!r} -> {lines}"
        for ln in lines:
            w = c.measure_text(ln).width
            assert w <= col_w, f"{t.label!r} line {ln!r} clips ({w} > {col_w})"
        # baseline of the last line + its descent must sit inside the tile
        line_h = m.ascent + m.descent + 1.0
        last = (LT.ICON_PAD_TOP + LT.ICON_BOX + LT.LABEL_GAP + m.ascent
                + (len(lines) - 1) * line_h)
        assert last + m.descent <= LT.DEFAULT_TILE_H, t.label


def test_pressed_chip_covers_the_icon_box():
    """The pressed inset chip must still wrap the (now 64px) icon."""
    assert LT.CHIP_H >= LT.ICON_BOX
    assert LT.CHIP_W_MAX >= LT.ICON_BOX
    # chip top above the icon top, chip bottom below the icon bottom
    chip_top = LT.ICON_PAD_TOP - LT.CHIP_INSET_TOP
    assert chip_top <= LT.ICON_PAD_TOP
    assert chip_top + LT.CHIP_H >= LT.ICON_PAD_TOP + LT.ICON_BOX
    # symmetric pad around the icon box (a backdrop, not a lopsided slab)
    assert (chip_top + LT.CHIP_H) - (LT.ICON_PAD_TOP + LT.ICON_BOX) == \
        LT.ICON_PAD_TOP - chip_top
    # ...and it stops short of the first label baseline (the label is drawn on
    # TOP of the chip, as in the pre-resize treatment, but never buried by it)
    first_baseline = LT.ICON_PAD_TOP + LT.ICON_BOX + LT.LABEL_GAP + 13.0
    assert chip_top + LT.CHIP_H < first_baseline


# ---------------------------------------------------------------------------
# Window placement ([CXC-DVL-GEOMETRY], David 2026-08-05): the ribbon opts into
# the SHARED geometry seam — last saved position wins, else work-area top-left.
# ---------------------------------------------------------------------------
def test_ribbon_scene_opts_into_geometry_persistence(scene_raw):
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(str(SCENE))
    assert comp.persist_geometry is True
    assert comp.window_key == "compose-launch-bar"
    # fixed-size ribbon: the scene's own size stays authoritative on restore
    assert comp.resizable is False


def test_ribbon_first_launch_opens_at_work_area_top_left_inset():
    """No saved record -> 20px in from the top-left of the WORK AREA (below the
    macOS menu bar), NOT OS-centered.  The work area is injected so the test is
    display-independent."""
    from composeio.render.engine.geometry import (
        DEFAULT_ORIGIN_INSET, default_origin, resolve_geometry)
    work = [(0, 25, 3840, 2050)]            # macOS: 25px menu bar at the top
    origin = default_origin(work, (LT.RIBBON_W, LT.RIBBON_H))
    assert origin == (0 + DEFAULT_ORIGIN_INSET, 25 + DEFAULT_ORIGIN_INSET)
    geom, source = resolve_geometry(None, None, (LT.RIBBON_W, LT.RIBBON_H),
                                    default_pos=origin)
    assert source == "screen-origin"
    assert (geom.x, geom.y) == (20, 45)
    assert geom.y > 25, "ribbon must not open under the macOS menu bar"


def test_ribbon_saved_position_wins_over_the_default_origin():
    from composeio.render.engine.geometry import (WindowGeometry,
                                                  resolve_geometry)
    saved = WindowGeometry(1200, 300, int(LT.RIBBON_W), int(LT.RIBBON_H))
    geom, source = resolve_geometry(saved, None, (LT.RIBBON_W, LT.RIBBON_H),
                                    default_pos=(20, 45))
    assert source == "persisted" and (geom.x, geom.y) == (1200, 300)


def test_ribbon_restore_keeps_scene_size_and_saved_position():
    """End-to-end on the runtime with an injected store: a stale saved SIZE
    never pins the fixed-size ribbon, but the saved POSITION is honored."""
    from composeio.render.engine.geometry import WindowGeometry
    from composeio.render.engine.parser import parse_compose_yaml
    from composeio.render.engine.runtime import RenderApp

    class MemStore:
        def __init__(self, g=None):
            self.g = g
        def load(self, key):
            return self.g
        def save(self, key, geom):
            self.g = geom
            return True

    comp = parse_compose_yaml(str(SCENE))
    app = RenderApp(comp, windowed=False)
    # a record saved back when the ribbon was 72x486
    app._geometry_store = MemStore(WindowGeometry(1200, 300, 72, 486))
    pos = app._restore_geometry(None)
    assert pos == (1200, 300)                      # saved position honored
    assert (comp.width, comp.height) == (LT.RIBBON_W, LT.RIBBON_H)
    app.stop()


# ---------------------------------------------------------------------------
# LB-C2 — a tile press publishes exactly its intent, nothing else.
# ---------------------------------------------------------------------------
def test_enabled_tile_press_publishes_its_intent():
    tile = LaunchTileWidget("t", target="graph", label="Graph Viewer",
                            icon="x.svg", enabled=True)
    ctx = _RecordCtx()
    tile._ctx = ctx
    tile.on_tap(1.0, 1.0)
    assert ctx.published == [("_app/graph/open", "open", "t")], ctx.published


def test_disabled_tile_press_publishes_nothing():
    # a not-built tile is inert — it can never publish into the void.
    tile = LaunchTileWidget("t", target="composer", label="Composer",
                            icon="x.svg", enabled=False)
    ctx = _RecordCtx()
    tile._ctx = ctx
    assert tile.interactive is False
    tile.on_tap(1.0, 1.0)
    assert ctx.published == []


# ---------------------------------------------------------------------------
# No persistent active-app highlight (David 2026-07-29) — the only chip is the
# click-and-hold press feedback.
# ---------------------------------------------------------------------------
def test_no_persistent_active_state():
    tile = LaunchTileWidget("t", target="composer", label="Composer",
                            icon="x.svg", enabled=True)
    # the widget carries no active-app machinery.
    assert not hasattr(tile, "_active")
    assert not hasattr(tile, "current_namespace")


def test_press_state_is_transient():
    tile = LaunchTileWidget("t", target="composer", label="Composer",
                            icon="x.svg", enabled=True)
    tile._ctx = _RecordCtx()
    assert tile._pressed is False
    tile.on_pointer_down(1.0, 1.0)
    assert tile._pressed is True             # dark chip only while held
    tile.on_pointer_up(1.0, 1.0)
    assert tile._pressed is False            # pops back on release


# ---------------------------------------------------------------------------
# The generalized launcher ([CXC-LB-LAUNCHER]/[CXC-LB-INTENT-TABLE]).
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def app_launcher():
    tools = _REPO_ROOT / "compose_projects" / "intelligence" / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import app_launcher as mod
    return mod


class _FakeProc:
    def __init__(self):
        self.pid = 4242
        self._alive = True

    def poll(self):
        return None if self._alive else 0


# LB-C5 — every roster intent has a registered consumer (register-guard).
def test_launcher_register_guard_covers_roster(app_launcher):
    assert set(app_launcher.registered_targets()) == {t for t, _, _ in ROSTER}


# --------------------------------------------------------------------------- #
# THE FORK-DIVERGENCE GUARD (DEFECT-FORK-DIVERGENCE-RECURRING-CLASS-A-62CF12).
# The Graph Viewer rename repointed decisions_viewer_launcher but MISSED
# app_launcher — the launcher the ribbon actually goes through — so the tile kept
# spawning the OLD scene and the fix "landed" while changing nothing on the glass.
# A green suite did not catch it because nothing tied the SCENE's tile targets to
# the LAUNCHER's table to the FILE on disk.  These do, for all seven tiles.
# --------------------------------------------------------------------------- #
def test_every_tile_target_routes_to_a_table_row_whose_scene_EXISTS(app_launcher):
    """Each ribbon tile's `target:` must resolve to a real TARGET_TABLE row, and
    a SCENE row's scene file must EXIST on disk.  This is the cross-implementation
    assertion the fork-divergence cure calls for: scene -> table -> filesystem."""
    doc = _yaml.safe_load(SCENE.read_text())
    targets = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "launch_tile" and node.get("target"):
                targets.append(str(node["target"]))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc.get("widgets"))
    assert len(targets) == len(ROSTER), f"expected {len(ROSTER)} tiles, got {targets}"
    for target in targets:
        row = app_launcher.TARGET_TABLE.get(target)
        assert row is not None, (
            f"tile target {target!r} has NO app_launcher.TARGET_TABLE row — the "
            f"ribbon press would be routed nowhere")
        if row.kind == app_launcher.SCENE:
            resolved = app_launcher.resolve_scene(row.scene)
            assert resolved is not None, (
                f"target {target!r} -> scene {row.scene!r} does NOT exist on "
                f"disk — the tile would spawn a missing scene")


def test_both_launchers_agree_on_the_graph_route(app_launcher):
    """[CXC-GV-ROUTE-TABLE] the dedicated launcher must not hold a SECOND copy of
    the graph route.  It derives its scene + intent from the shared table, so the
    two can never disagree about which scene `graph` opens — the exact divergence
    that shipped."""
    import decisions_viewer_launcher as dvl
    row = app_launcher.TARGET_TABLE[dvl.TARGET_KEY]
    # same scene file, resolved
    assert dvl.resolve_scene(str(dvl.DECISIONS_SCENE)) == \
        app_launcher.resolve_scene(row.scene)
    # same intent namespace, derived from the same target key
    assert dvl.INTENT_NS == f"_app/{dvl.TARGET_KEY}/open"
    # and the ribbon tile publishes exactly that namespace
    assert app_launcher.target_from_topic(
        f"composeio/global/{dvl.INTENT_NS}") == dvl.TARGET_KEY


def test_no_launcher_still_points_at_the_retired_decisions_scene(app_launcher):
    """The retired scene name must not survive in either routing table (a
    decisions-named target driving the general viewer is the misleading debt the
    `_app/graph/*` decision rejected)."""
    assert "decisions" not in app_launcher.TARGET_TABLE
    for row in app_launcher.TARGET_TABLE.values():
        assert "decisions_tab.compose.yaml" not in (row.scene or "")


def _launcher(app_launcher, spawned):
    return app_launcher.AppLauncher(
        spawn=lambda argv: (spawned.append(argv) or _FakeProc()))


# LB-C3/C6 — the scene target spawns via the shared seams; dedupe + reap.
def test_launcher_graph_spawns_once_then_dedupes(app_launcher):
    spawned = []
    lau = _launcher(app_launcher, spawned)
    scene_abs = app_launcher.resolve_scene(app_launcher.GRAPH_SCENE)
    r1 = lau.open("graph")
    assert r1.startswith("opened graph") and len(spawned) == 1
    assert spawned[0][-1] == scene_abs          # argv ends at the resolved scene
    assert "already open" in lau.open("graph") and len(spawned) == 1
    # a closed window respawns fresh (reap); dedupe key is the scene path
    lau._windows.live[scene_abs]._alive = False
    assert lau.open("graph").startswith("opened") and len(spawned) == 2


# 5-active split: composer + ComposeBus-monitor spawn `python -m <module>`.
def test_launcher_module_targets_spawn(app_launcher):
    spawned = []
    lau = _launcher(app_launcher, spawned)
    assert lau.open("composer").startswith("opened composer")
    assert spawned[-1][1:3] == ["-m", app_launcher.COMPOSER_MODULE]
    assert lau.open("bus").startswith("opened bus")
    assert spawned[-1][1:3] == ["-m", app_launcher.MONITOR_MODULE]


# AI Harness is a TERMINAL/PTY app (flagged): a real, registered spawnable
# target — NOT a not-built no-op.
def test_launcher_harness_focuses_never_spawns(app_launcher):
    # David 2026-07-29: the AI Harness tile FOCUSES the already-running harness
    # (HOST kind, like designer) rather than spawning a new PTY session.
    row = app_launcher.TARGET_TABLE["harness"]
    assert row.kind == app_launcher.HOST


def test_launcher_host_target_never_spawns(app_launcher):
    spawned = []
    lau = _launcher(app_launcher, spawned)
    assert "host focus" in lau.open("designer")
    assert spawned == []


# D7: the two storage viewers graduated from not_built to SCENE spawns, each
# with --transports (they publish `_app/doc/open` over the global bus).
@pytest.mark.parametrize("target,scene_const", [
    ("docstore", "DOCSTORE_SCENE"),
    ("busstore", "BUSSTORE_SCENE"),
])
def test_launcher_storage_viewers_spawn_scene_with_transports(
        app_launcher, target, scene_const):
    spawned = []
    lau = _launcher(app_launcher, spawned)
    assert lau.open(target).startswith(f"opened {target}")
    scene_abs = app_launcher.resolve_scene(getattr(app_launcher, scene_const))
    assert scene_abs is not None
    # argv ends at the resolved scene + --transports (global bus leg).
    assert spawned[-1][-2:] == [scene_abs, "--transports"]


def test_no_roster_target_is_not_built(app_launcher):
    # every roster target now resolves to a real spawnable/host consumer.
    for tgt, row in app_launcher.TARGET_TABLE.items():
        assert row.kind != app_launcher.NOT_BUILT, f"{tgt} still not_built"


def test_launcher_topic_target_extraction(app_launcher):
    assert app_launcher.target_from_topic(
        "composeio/global/_app/graph/open") == "graph"
    assert app_launcher.target_from_topic("_app/busstore/open") == "busstore"
    assert app_launcher.target_from_topic(
        "composeio/global/_app/console/bbox") is None


def test_launcher_intent_parse(app_launcher):
    assert app_launcher.parse_intent("open") is True
    assert app_launcher.parse_intent({"val": {"open": True}}) is True
    for falsy in ("", "off", "close", 0, None, "false"):
        assert app_launcher.parse_intent(falsy) is False


def test_launcher_path_guard_refuses_escape(app_launcher):
    assert app_launcher.resolve_scene("/etc/passwd") is None
    assert app_launcher.resolve_scene("../../../../etc/hosts") is None
    assert app_launcher.resolve_scene(app_launcher.GRAPH_SCENE) is not None


# ---------------------------------------------------------------------------
# LB-C12 — the ribbon SCENE is BUS-BOUND ([CXC-LB-RIBBON-BUS-BOUND]).
#
# REGRESSION GUARD for DEFECT-LAUNCH-BAR-BUTTONS-DON-T-* (David 2026-08-05): the
# ribbon shipped launched with `--transports` but WITHOUT a `transports:` block
# in its own scene, so RenderApp._build_bus connected NO global MQTT leg — every
# tile publish auto-resolved to global scope and was then DROPPED at
# _send_message (scope global, but bus._global is None). No intent reached the
# out-of-process app_launcher, so NO button launched. LB-C2 above only ever
# proved the publish against an INJECTED bus, so it could not catch this: the
# real scene's missing global leg is a SCENE-level fact, tested here.
# ---------------------------------------------------------------------------
def test_ribbon_scene_declares_mqtt_transport():
    """The ribbon scene MUST declare an `mqtt` transport under `app:` so
    `_build_bus` connects the global leg ([CXC-LB-RIBBON-BUS-BOUND]).  RED on
    the pre-fix scene (empty comp.transports)."""
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(str(SCENE))
    mqtt = [t for t in comp.transports
            if isinstance(t, dict) and t.get("type") == "mqtt"]
    assert mqtt, (
        "ribbon scene declares no mqtt transport -> RenderApp with --transports "
        "connects no global leg -> every tile publish is dropped, no button "
        "launches ([CXC-LB-RIBBON-BUS-BOUND])")
    assert mqtt[0].get("broker") and int(mqtt[0].get("port", 0)) > 0


def test_ribbon_transports_flag_connects_global_leg(monkeypatch):
    """With the scene bus-bound, a `--transports`-equivalent RenderApp gets a
    NON-None global transport ([CXC-LB-RIBBON-BUS-BOUND]).  This is the exact
    mechanism the defect broke: `connect_transports=True` is inert unless the
    scene declares the transport.  RED before the fix (bus._global is None).
    The MQTT client is STUBBED so the test needs no live broker."""
    import composeio.transport_mqtt as tmod
    from composeio.render.engine.parser import parse_compose_yaml
    from composeio.render.engine.runtime import RenderApp

    built = {}

    class _StubMQTT:
        def __init__(self, broker="localhost", port=1883, client_id=""):
            built["broker"], built["port"] = broker, port
        def start(self, *a, **k):
            pass
        def send(self, *a, **k):
            pass
        def subscribe(self, *a, **k):
            # the disk-space alert ([DX-STATE-ON-BUS]) SUBSCRIBES
            # buildhost/storage/free_bytes over the global leg, so the stub
            # must accept subscriptions — a no-op is enough for this test,
            # which only asserts the global transport connected.
            pass
        def stop(self, *a, **k):
            pass
    monkeypatch.setattr(tmod, "MQTTTransport", _StubMQTT)

    comp = parse_compose_yaml(str(SCENE))
    app = RenderApp(comp, headless=True, connect_transports=True)
    assert app.bus._global is not None, (
        "scene bus-bound but _build_bus connected no global transport")
    # built from the scene's own broker/port, not a default guess.
    assert built.get("broker") and int(built.get("port", 0)) > 0


# End-to-end: the on_message routing an intent publish would drive
# ([CXC-LB-CREDO] — an Intelligence publish is indistinguishable from a click).
def test_launcher_routes_published_intent_end_to_end(app_launcher):
    spawned = []
    lau = _launcher(app_launcher, spawned)
    topic = "composeio/global/_app/graph/open"
    target = app_launcher.target_from_topic(topic)
    assert app_launcher.parse_intent("open")
    status = lau.open(target)
    assert status.startswith("opened graph") and len(spawned) == 1
