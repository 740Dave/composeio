"""Live bus topics and device identifiers that name a deployment's machines are CONFIGURATION.

The public code carries NEUTRAL defaults; a deployment sets its own values through the env knobs below
(the scene side reads the same names as COMPOSEIO_VAR_<NAME> launch overrides of its `variables:` block).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "python"))
from composeio.render.engine.bindings import substitute_variables  # noqa: E402
from composeio.render.engine.runtime import _variable_overrides_from_env  # noqa: E402


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _scene(rel, env=None):
    cfg = yaml.safe_load((REPO / rel).read_text())
    return substitute_variables(cfg, _variable_overrides_from_env(env or {}))


def _strings(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)
    elif isinstance(node, str):
        yield node


# --- storage topic: publisher and launch-bar consumer share COMPOSEIO_VAR_STORAGE_HOST -----------------
def test_storage_topic_default_is_neutral(monkeypatch):
    monkeypatch.delenv("COMPOSEIO_VAR_STORAGE_HOST", raising=False)
    topics = _load("services/storage_monitor/topics.py", "_topics_default")
    assert topics.FREE_BYTES == "buildhost/storage/free_bytes"
    exprs = [s for s in _strings(_scene("apps/compose_platform_launch_bar/launch_bar.compose.yaml"))
             if "/storage/free_bytes" in s]
    assert exprs and all("ns('buildhost/storage/free_bytes')" in s for s in exprs)


def test_storage_topic_uses_the_configured_host(monkeypatch):
    monkeypatch.setenv("COMPOSEIO_VAR_STORAGE_HOST", "nas7")
    topics = _load("services/storage_monitor/topics.py", "_topics_configured")
    assert topics.FREE_BYTES == "nas7/storage/free_bytes"
    exprs = [s for s in _strings(_scene("apps/compose_platform_launch_bar/launch_bar.compose.yaml",
                                        {"COMPOSEIO_VAR_STORAGE_HOST": "nas7"}))
             if "/storage/free_bytes" in s]
    assert exprs and all("ns('nas7/storage/free_bytes')" in s for s in exprs)
    assert not any("buildhost" in s for s in exprs)


# --- soak node namespace, bench host and bring-up slot -------------------------------------------------
def test_soak_and_bringup_defaults_are_neutral(monkeypatch):
    for k in ("FLEET_BENCH_HOST", "COMPOSEIO_VAR_SOAK_NODE_TEMP_NS", "FLEET_SOAK_NODE_NAME", "FLEET_BENCH_NAME"):
        monkeypatch.delenv(k, raising=False)
    hud = _load("compose_projects/fleet-hud/fleet_hud.py", "_hud_default")
    assert "benchnode.local" in hud._BENCHNODE and "benchnode.local" in hud._FLEETNODE
    # The soak log names have NO default: unset is "not configured", never a guessed file name.
    assert (hud._SOAK_NODE, hud._BENCH_NAME) == (None, None)
    soak = _scene("compose_projects/fleet-hud/fleet-540-soak.compose.yaml")
    assert "fleetnode_temp_c" in [n["name"] for n in soak["namespaces"]]
    bring = _scene("compose_projects/minnow-bringup/minnow-bringup.compose.yaml")
    assert "fleetnode" in list(_strings(bring))


def test_soak_and_bringup_use_the_configured_values(monkeypatch):
    monkeypatch.setenv("FLEET_BENCH_HOST", "bench9.lan")
    monkeypatch.setenv("FLEET_SOAK_NODE_NAME", "n9")
    monkeypatch.setenv("FLEET_BENCH_NAME", "b9")
    hud = _load("compose_projects/fleet-hud/fleet_hud.py", "_hud_configured")
    assert hud._BENCHNODE.endswith("bench9.lan") and "-W %h:%p bench9.lan'" in hud._FLEETNODE
    assert (hud._SOAK_NODE, hud._BENCH_NAME) == ("n9", "b9")
    soak = _scene("compose_projects/fleet-hud/fleet-540-soak.compose.yaml",
                  {"COMPOSEIO_VAR_SOAK_NODE_TEMP_NS": "n9_temp_c"})
    assert "n9_temp_c" in [n["name"] for n in soak["namespaces"]]
    bring = _scene("compose_projects/minnow-bringup/minnow-bringup.compose.yaml",
                   {"COMPOSEIO_VAR_BRINGUP_DEVICE": "slot9"})
    default = list(_strings(_scene("compose_projects/minnow-bringup/minnow-bringup.compose.yaml")))
    configured = list(_strings(bring))
    assert "slot9" in configured and "slot9" not in default
    assert configured.count("fleetnode") == default.count("fleetnode") - 1   # the slot moved; the placeholder text did not
