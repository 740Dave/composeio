"""
[FLOW:MAKER-PROJECT] S5 — the dual-throttle validation project, end to end.

Proves the whole maker flow falls out of the ONE artifact
(``render/examples/dual_throttle.compose.yaml``) AND that the payoff loop works:

  A. the flow's derived consumers (S2/S3/S4) all read the one YAML —
     hookup SVG (both substrates) + the MCU read/publish config;
  B. the composeio-side integration CONTRACT — differential engine thrust
     (throttle/left - throttle/right) -> the Galactica's heading rate;
  C. THE PROOF (headless) — publishing throttle/left / throttle/right MOVES the
     Galactica's heading in the reference world model (the executable statement of
     the world-app hook the Qualia dradis reflects via game/own_heading).

The world app + Qualia dradis live in a SEPARATE repo (~/maker/bsg), so the proof
drives ``ThrustHeadingModel`` — the reference model the documented world-app hook
(``WORLD_APP_HOOK``) mirrors line-for-line.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.hookup import Hookup
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.svg import SvgDocument
from composeio.maker import (
    hookup_to_svg, hookup_svg_to_file, emit_mcu_config,
    NS_THROTTLE_LEFT, NS_THROTTLE_RIGHT, NS_OWN_HEADING,
    differential_thrust, mean_thrust, heading_rate,
    ThrustHeadingModel, ThrottleThrustBridge, WORLD_APP_HOOK,
    DEFAULT_MAX_RATE_DPS, DEFAULT_DEADBAND, THRUST_FULL_SCALE,
)
from composeio.maker.projects import dual_throttle_build

SCENE = os.path.join(PY_ROOT, "composeio", "render", "examples",
                     "dual_throttle.compose.yaml")


@pytest.fixture(scope="module")
def comp():
    return parse_compose_yaml(SCENE)


# ===========================================================================
# A. the flow's derived consumers all read the ONE YAML
# ===========================================================================

def test_scene_is_the_headless_controller(comp):
    """2x pot-10k -> ESP32-C3, wipers publish the two thrust slots; the DEVICE is
    headless (no display part in the hookup)."""
    h = comp.hookup
    assert h is not None and h.board == "perfboard"
    parts = {p.id: p.part for p in h.parts}
    # S6 additive: the throttle cluster PLUS the DY-SV5F audio module + its
    # speaker terminus.  The DEVICE is still headless (no DISPLAY part); the
    # DY-SV5F is an OUTPUT actuator the C3 drives, not a screen.
    assert parts == {"mcu": "esp32-c3-supermini", "potL": "pot-10k",
                     "potR": "pot-10k", "dysv5f": "dy-sv5f", "spk": "speaker"}
    # the load-bearing seam: each wiper pin -> a thrust slot (S2 keys the map by
    # the fully-qualified MCU pin, `mcu.<pin>`)
    assert h.pin_slot_map() == {"mcu.GPIO0": "throttle/left",
                                "mcu.GPIO1": "throttle/right"}
    # the two slots are declared with the continuous thrust engine
    engines = {ns["name"]: ns["state_engine"] for ns in comp.namespaces}
    # The wire carries a PERCENTAGE since 2026-08-15 (David), not an 8-bit code.
    assert engines["throttle/left"] == "continuous_0_100"
    assert engines["throttle/right"] == "continuous_0_100"


def test_hookup_guide_svg_both_substrates(comp):
    """S3: one netlist -> a valid SVG on breadboard AND perfboard."""
    for board in ("breadboard", "perfboard"):
        h = comp.hookup
        if h.board != board:
            h = Hookup(board=board, parts=h.parts, wires=h.wires)
        svg = hookup_to_svg(h)
        doc = SvgDocument.from_bytes(svg.encode("utf-8"))
        assert doc.width > 0 and doc.height > 0
        assert "throttle/left" in svg and "throttle/right" in svg
        assert "mcu" in svg and "potL" in svg and "potR" in svg


def test_mcu_config_falls_out_of_the_yaml(comp):
    """S4: the pin->slot seam derives the ESP32 analog read/publish config."""
    cfg = emit_mcu_config(comp)
    assert cfg.board == "esp32-c3-supermini"
    by_slot = {s.publish: s for s in cfg.sensors}
    assert set(by_slot) == {"throttle/left", "throttle/right"}
    left = by_slot["throttle/left"]
    assert left.pin == "GPIO0" and left.mode == "analog"
    assert left.engine == "continuous_0_100" and left.sensor == "pot-10k"
    # The CALIBRATED 12-bit ADC span (the in_min/in_max SAFETY BANDS declared
    # on the wire) normalized onto the engine domain (0..100 %).  in_min is NOT
    # 0 and in_max is NOT 4095: the end bands are what make both rails
    # reachable regardless of pot end-resistance, and an emitter that reported
    # the full span here would be describing a device that does not exist.
    assert (left.normalize.in_min, left.normalize.in_max) == (96.0, 3999.0)
    assert (left.normalize.out_min, left.normalize.out_max) == (0.0, 100.0)
    # ...and the conditioning knobs the emitter used to DROP ENTIRELY.
    assert left.conditioning == {"deadband": 1}
    assert by_slot["throttle/right"].conditioning == {"invert": True, "deadband": 1}
    assert by_slot["throttle/right"].pin == "GPIO1"
    assert not cfg.warnings


def test_build_writes_committed_artifacts(tmp_path):
    """The build script writes the doc-viewer-openable SVG + the MCU config
    (YAML + JSON) to repo-relative paths; regenerating is idempotent."""
    res = dual_throttle_build.build(repo_root=str(tmp_path))
    svg_path = tmp_path / res["hookup_guide"]
    yaml_path = tmp_path / res["mcu_config_yaml"]
    json_path = tmp_path / res["mcu_config_json"]
    assert svg_path.exists() and svg_path.read_text().startswith("<svg")
    assert yaml_path.exists() and "throttle/left" in yaml_path.read_text()
    doc = json.loads(json_path.read_text())
    pins = {s["publish"]: s["pin"] for s in doc["sensors"]}
    assert pins == {"throttle/left": "GPIO0", "throttle/right": "GPIO1"}
    # doc-viewer seam: the guide path is repo-relative (publishable to _app/doc/open)
    assert res["hookup_guide"] == os.path.join(
        "python", "composeio", "maker", "projects", "dual_throttle",
        "artifacts", "hookup-guide.perfboard.svg")


# ===========================================================================
# B. the composeio-side integration CONTRACT — differential thrust -> heading
# ===========================================================================

def test_differential_and_mean_thrust():
    # balanced (both idle / both full) -> no steering, mean reflects drive
    assert differential_thrust(0, 0) == 0.0
    assert differential_thrust(100, 100) == 0.0
    assert mean_thrust(0, 0) == 0.0
    assert mean_thrust(100, 100) == pytest.approx(1.0)
    # hard-over left / right
    assert differential_thrust(100, 0) == pytest.approx(1.0)
    assert differential_thrust(0, 100) == pytest.approx(-1.0)
    # out-of-range / bad frames clamp, never raise
    assert differential_thrust(999, -5) == pytest.approx(1.0)
    assert differential_thrust("", None) == 0.0


def test_heading_rate_mapping():
    # balanced -> zero rate (drive straight), regardless of drive level
    assert heading_rate(0, 0) == 0.0
    assert heading_rate(100, 100) == 0.0
    assert heading_rate(50, 50) == 0.0
    # hard-over -> +/- full rate
    assert heading_rate(100, 0) == pytest.approx(DEFAULT_MAX_RATE_DPS)
    assert heading_rate(0, 100) == pytest.approx(-DEFAULT_MAX_RATE_DPS)
    # deadband suppresses pot jitter near centre
    jitter = DEFAULT_DEADBAND * 0.5 * 100.0
    assert heading_rate(50 + jitter, 50) == 0.0
    # sign flip knob reverses the turn
    assert heading_rate(100, 0, sign=-1.0) == pytest.approx(-DEFAULT_MAX_RATE_DPS)


def test_contract_namespaces_match_bsg():
    """The slots this controller publishes + the heading slot the world publishes
    are the exact bsg namespaces (drift guard for the cross-repo contract)."""
    assert NS_THROTTLE_LEFT == "throttle/left"
    assert NS_THROTTLE_RIGHT == "throttle/right"
    # bsg/world/publish.py NS_OWN_HEADING — the slot the Qualia dradis reflects
    assert NS_OWN_HEADING == "game/own_heading"
    # the documented world-app hook names the real seams
    assert "game/own_heading" in WORLD_APP_HOOK or "own_heading" in WORLD_APP_HOOK
    assert "throttle/left" in WORLD_APP_HOOK and "throttle/right" in WORLD_APP_HOOK


def test_vendored_helm_fork_agrees_with_upstream():
    """``compose_projects/bsg/world/helm.py`` is a SELF-DECLARED VENDORED COPY of
    this module, kept so the bsg repo takes no composeio dependency.

    ⛔ A VENDORED COPY IS A FORK, AND A FORK THAT MISSES A WIRE-FORMAT CHANGE
    DOES NOT FAIL.  When ``THRUST_FULL_SCALE`` moved 255 -> 100 here (the
    2026-08-15 percentage migration), a helm.py still dividing by 255 would
    have produced a ship that steers at 2.55x the intended rate — forever, with
    no exception on any tier, because both halves are internally consistent.
    That class of drift is invisible to every "do these two lists match?" check,
    since the disagreement is in a NUMBER rather than in a list of names.

    So this asserts BEHAVIOURAL equivalence over a sweep, not merely equal
    constants: a fork that re-derived the same physics differently is fine, and
    a fork that kept the old full scale is not.
    """
    import importlib.util

    helm_path = os.path.join(REPO_ROOT, "compose_projects", "bsg", "world",
                             "helm.py")
    assert os.path.isfile(helm_path), f"vendored helm fork missing: {helm_path}"
    spec = importlib.util.spec_from_file_location("_bsg_helm_fork", helm_path)
    helm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helm)

    # The one constant that couples the fork to the wire format.
    assert helm.THRUST_FULL_SCALE == THRUST_FULL_SCALE, (
        f"vendored helm.py full scale {helm.THRUST_FULL_SCALE} != upstream "
        f"{THRUST_FULL_SCALE} — the fork missed a wire-format change")
    assert helm.NS_THROTTLE_LEFT == NS_THROTTLE_LEFT
    assert helm.NS_THROTTLE_RIGHT == NS_THROTTLE_RIGHT

    # ...and the physics it drives, across the whole percentage domain.
    for left in range(0, 101, 5):
        for right in range(0, 101, 5):
            assert helm.heading_rate(left, right) == pytest.approx(
                heading_rate(left, right), abs=1e-9), (left, right)


# ===========================================================================
# C. THE PROOF — publishing throttle/* MOVES the Galactica's heading
# ===========================================================================

def _drive(model, left, right, *, seconds=1.0, hz=50.0):
    model.set_thrust(left=left, right=right)
    dt = 1.0 / hz
    for _ in range(int(seconds * hz)):
        model.advance(dt)
    return model.heading


def test_throttle_moves_heading():
    """Left engine harder than right -> heading swings one way; right harder ->
    the other way; balanced -> heading HOLDS.  This is the payoff: publishing the
    two thrust slots moves the Galactica's heading (which the dradis reflects)."""
    # hard-over LEFT for 1 s -> +45 deg (default max rate)
    m = ThrustHeadingModel(heading=90.0)
    assert _drive(m, 100, 0, seconds=1.0) == pytest.approx(135.0, abs=1.0)

    # hard-over RIGHT for 1 s -> -45 deg
    m = ThrustHeadingModel(heading=90.0)
    assert _drive(m, 0, 100, seconds=1.0) == pytest.approx(45.0, abs=1.0)

    # balanced thrust -> heading unchanged (no creep, even at full drive)
    m = ThrustHeadingModel(heading=90.0)
    assert _drive(m, 80, 80, seconds=2.0) == pytest.approx(90.0, abs=1e-6)

    # partial differential -> partial rate (proportional, monotone)
    slow = ThrustHeadingModel(heading=0.0)
    fast = ThrustHeadingModel(heading=0.0)
    _drive(slow, 63, 50, seconds=1.0)     # small differential
    _drive(fast, 100, 0, seconds=1.0)     # hard-over
    assert 0.0 < slow.heading < fast.heading


def test_thrust_moves_position_forward():
    """The mean-thrust drive half: balanced full thrust -> a straight line at the
    current heading (steer on the difference, drive on the mean)."""
    m = ThrustHeadingModel(heading=0.0, max_speed=1.0)   # heading 0 -> +x
    _drive(m, 100, 100, seconds=1.0)     # full drive, no differential
    x, y = m.position
    assert x == pytest.approx(1.0, abs=1e-6)     # 1 unit forward
    assert y == pytest.approx(0.0, abs=1e-6)     # dead straight (heading held)


def test_bus_ingress_shape_and_bridge():
    """The world subscribes the two slots via the bus-callback shape; the optional
    in-process bridge republishes game/own_heading on its clock."""
    published = []
    bridge = ThrottleThrustBridge(lambda ns, v: published.append((ns, v)),
                                  model=ThrustHeadingModel(heading=0.0))
    # a bus delivers throttle state messages -> the world ingests them
    bridge.on_message(NS_THROTTLE_LEFT, 100)
    bridge.on_message(NS_THROTTLE_RIGHT, 0)
    assert bridge.model.on_throttle("some/other/ns", 1) is False   # only the slots
    # tick for a second -> heading advanced AND republished on the heading slot
    for _ in range(50):
        bridge.tick(1.0 / 50.0)
    assert published, "the bridge must republish game/own_heading"
    assert all(ns == NS_OWN_HEADING for ns, _ in published)
    assert bridge.model.heading == pytest.approx(45.0, abs=1.0)
