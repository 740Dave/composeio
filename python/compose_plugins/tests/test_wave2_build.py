"""`compose-plugin build` and the drift check ([CXC-PC-DERIVE-ONCE],
`bus-types-attribute`)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from typing import ClassVar

import pytest
from pydantic import BaseModel

from composebus.plugin import ComposePlugin
from compose_plugins import derive
from compose_plugins.fixtures import package_dir


def test_shipped_fixture_manifests_are_exactly_what_build_generates(tmp_path):
    for v in ("v1", "v2"):
        dst = tmp_path / v
        shutil.copytree(package_dir(v), dst, ignore=shutil.ignore_patterns("__pycache__"))
        r = subprocess.run([sys.executable, "-m", "compose_plugins.derive", "build", str(dst)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        assert (dst / "manifest.json").read_bytes() == \
            (package_dir(v) / "manifest.json").read_bytes()


def _cls(**attrs):
    return type("P", (ComposePlugin,), attrs)


def test_bus_types_missing_is_a_loud_build_refusal():
    with pytest.raises(derive.BuildRefused) as ei:
        derive.derive_bus(_cls(publishes=["plugins/x/a"]))
    print(ei.value)
    assert "no bus_types" in str(ei.value)


@pytest.mark.parametrize("types,needle", [
    ({}, "no type for ['plugins/x/a']"),
    ({"plugins/x/a": "STATE", "plugins/x/b": "EVENT"}, "neither publishes nor subscribes"),
    ({"plugins/x/a": "HEARTBEAT"}, "not STATE or EVENT"),
])
def test_bus_types_missing_extra_or_bad_entry_is_refused(types, needle):
    with pytest.raises(derive.BuildRefused) as ei:
        derive.derive_bus(_cls(publishes=["plugins/x/a"], bus_types=types))
    print(ei.value)
    assert needle in str(ei.value)


def test_no_traffic_omits_bus_and_no_config_is_the_empty_object_schema():
    cls = _cls()
    assert derive.derive_bus(cls) is None
    assert derive.derive_config_schema(cls) == {"properties": {}, "title": "Config",
                                                "type": "object"}
    out = json.loads(derive.build_manifest_bytes({"domain": "x"}, cls))
    assert "bus" not in out and out["config_schema"]["properties"] == {}


def test_drift_compares_namespace_and_type_pairs():
    class C(ComposePlugin):
        class Config(BaseModel):
            a: int = 1
        publishes = ["plugins/x/a"]
        bus_types: ClassVar[dict] = {"plugins/x/a": "STATE"}
    good = json.loads(derive.build_manifest_bytes({"domain": "x"}, C))
    assert derive.drift(good, C) == []
    bad = json.loads(json.dumps(good))
    bad["bus"]["publishes"][0]["type"] = "EVENT"
    assert derive.drift(bad, C) == [("bus.publishes", [("plugins/x/a", "EVENT")],
                                     [("plugins/x/a", "STATE")])]
    bad2 = json.loads(json.dumps(good))
    del bad2["config_schema"]["properties"]["a"]
    assert derive.drift(bad2, C)[0][0] == "config_schema.properties.a"


def test_entry_that_is_not_a_composeplugin_is_refused(tmp_path, monkeypatch):
    (tmp_path / "notplug.py").write_text("class P:\n    pass\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(derive.BuildRefused):
        derive.resolve_entry("notplug:P")


def test_cli_build_failure_exits_1(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"run": {"entry": "noplug:P"}}))
    (tmp_path / "noplug.py").write_text(textwrap.dedent("""
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            publishes = ["plugins/x/a"]
    """))
    r = subprocess.run([sys.executable, "-m", "compose_plugins.derive", "build", str(tmp_path)],
                       capture_output=True, text=True)
    print(r.stderr)
    assert r.returncode == 1 and "no bus_types" in r.stderr


def test_config_validation_matches_spec_87_taxonomy():
    pytest.importorskip("composeio")
    from composeio.composeserver_loader import validate_config
    from compose_plugins.config import config_errors

    class Config(BaseModel):
        greeting: str = "hello"
        repeat: int = 1
    for payload in ({"greeting": 42}, {"repeat": "x", "greeting": None}, [], {}):
        assert config_errors(Config, payload) == validate_config(Config, payload)
