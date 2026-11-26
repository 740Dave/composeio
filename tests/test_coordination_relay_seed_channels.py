"""The relay's seed channel list is CONFIGURATION, not code.

The public default is neutral; a deployment supplies its own list through
RELAY_SEED_CHANNELS (comma-separated), the same env seam the relay already reads for
RELAY_HOST / RELAY_PORT. The served UI must carry exactly the configured list.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib

_SERVER = pathlib.Path(__file__).resolve().parents[1] / "services" / "coordination_relay" / "server.py"


def _load():
    spec = importlib.util.spec_from_file_location("_coordination_relay_server", _SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_default_seed_channels_are_neutral(monkeypatch):
    monkeypatch.delenv("RELAY_SEED_CHANNELS", raising=False)
    mod = _load()
    assert mod.seed_channels() == list(mod.DEFAULT_SEED_CHANNELS)
    assert mod.DEFAULT_SEED_CHANNELS == ("haven-reactivation", "general")
    html = mod.render_ui()
    assert "let channels = new Set(" + json.dumps(["haven-reactivation", "general"]) + ");" in html


def test_configured_seed_channels_are_used(monkeypatch):
    monkeypatch.setenv("RELAY_SEED_CHANNELS", " alpha-team , beta ,, ")
    mod = _load()
    assert mod.seed_channels() == ["alpha-team", "beta"]
    html = mod.render_ui()
    assert "let channels = new Set(" + json.dumps(["alpha-team", "beta"]) + ");" in html
    assert "general" not in html.split("let channels")[1].split(";")[0]


def test_a_channel_name_cannot_close_the_script(monkeypatch):
    monkeypatch.setenv("RELAY_SEED_CHANNELS", "x</script><b>")
    html = _load().render_ui()
    assert "x</script><b>" not in html
