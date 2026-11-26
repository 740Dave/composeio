"""The host overlay seam ([CXC-SCL-SCRUB-OVERLAY-PARITY]).

The public tree carries only neutral defaults. A live host supplies its real values through ONE
file read at import (`composeio._overlay`), which fills only names the environment does not
already carry. These tests pin every rule of the loader, the transport broker fallback, the scene
files no longer naming a broker, the shell scripts' guarded overlay line, and the live-host check.

Every test here passes an explicit path or environ: the suite's conftest sets
COMPOSEIO_OVERLAY_ENV=none, so nothing here ever reads a developer's own overlay by accident.
"""

from __future__ import annotations

import os
import re
import pathlib
import subprocess
import sys

import pytest

from composeio import _overlay

REPO = pathlib.Path(__file__).resolve().parents[2]


def _write(tmp_path, text, name="live.env"):
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# --------------------------------------------------------------------------- loader rules

def test_fills_only_unset_names(tmp_path):
    env = {"ALREADY": "explicit"}
    path = _write(tmp_path, "ALREADY=from-file\nFRESH=from-file\n")
    _overlay.apply(path=path, environ=env)
    assert env == {"ALREADY": "explicit", "FRESH": "from-file"}


def test_an_explicit_empty_value_is_present_and_wins(tmp_path):
    env = {"EMPTY": ""}
    _overlay.apply(path=_write(tmp_path, "EMPTY=from-file\n"), environ=env)
    assert env["EMPTY"] == ""


def test_none_disables_the_overlay(tmp_path):
    path = _write(tmp_path, "FRESH=from-file\n")
    env = {"COMPOSEIO_OVERLAY_ENV": "none"}
    result = _overlay.apply(environ=env)
    assert result.path is None and result.names == ()
    assert "FRESH" not in env
    # and `none` as an explicit argument means the same
    env2 = {}
    _overlay.apply(path="none", environ=env2)
    assert env2 == {}
    assert os.path.exists(path)  # the file was there; it was not read


def test_env_names_the_file(tmp_path):
    path = _write(tmp_path, "FRESH=from-file\n", name="other.env")
    env = {"COMPOSEIO_OVERLAY_ENV": path}
    result = _overlay.apply(environ=env)
    assert env["FRESH"] == "from-file"
    assert result.path == path


def test_default_path_is_the_per_user_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".config" / "composeio"
    cfg.mkdir(parents=True)
    (cfg / "live.env").write_text("FRESH=from-default\n")
    env = {}
    _overlay.apply(environ=env)
    assert env == {"FRESH": "from-default"}


def test_missing_file_is_a_no_op(tmp_path):
    env = {}
    result = _overlay.apply(path=str(tmp_path / "absent.env"), environ=env)
    assert env == {} and result.path is None and result.names == ()


def test_comments_blank_lines_export_prefix_and_quotes(tmp_path):
    text = ("# a comment\n"
            "\n"
            "   # an indented comment\n"
            "export EXPORTED=one\n"
            "PLAIN=two=with=equals\n"
            "DQ=\"three\"\n"
            "SQ='four'\n"
            "  SPACED = five  \n")
    env = {}
    _overlay.apply(path=_write(tmp_path, text), environ=env)
    assert env == {"EXPORTED": "one", "PLAIN": "two=with=equals", "DQ": "three",
                   "SQ": "four", "SPACED": "five"}


def test_no_shell_expansion(tmp_path):
    env = {"HOME": "/home/placeholder"}
    _overlay.apply(path=_write(tmp_path, "P=$HOME/x\nQ=${HOME}\nR=`id`\nS=$(id)\n"),
                   environ=env)
    assert env["P"] == "$HOME/x" and env["Q"] == "${HOME}"
    assert env["R"] == "`id`" and env["S"] == "$(id)"


def test_malformed_lines_are_skipped_and_recorded_never_raised(tmp_path):
    text = ("GOOD=1\n"
            "no equals sign here\n"
            "=novalue\n"
            "9DIGIT=x\n"
            "BAD-KEY=x\n"
            "AFTER=2\n")
    env = {}
    result = _overlay.apply(path=_write(tmp_path, text), environ=env)
    assert env == {"GOOD": "1", "AFTER": "2"}
    assert [line for line, _ in result.skipped] == [2, 3, 4, 5]


def test_an_unreadable_file_never_raises(tmp_path):
    d = tmp_path / "a-directory.env"
    d.mkdir()
    env = {}
    result = _overlay.apply(path=str(d), environ=env)
    assert env == {} and result.names == ()
    assert result.skipped and result.skipped[0][0] == 0


def test_names_are_recorded_and_values_are_not(tmp_path):
    secret_shaped = "value-that-must-not-be-recorded"
    env = {"ALREADY": "x"}
    path = _write(tmp_path, f"ALREADY=y\nNEW_ONE={secret_shaped}\nNEW_TWO={secret_shaped}\n"
                            f"broken {secret_shaped}\n")
    result = _overlay.apply(path=path, environ=env)
    assert result.names == ("NEW_ONE", "NEW_TWO")   # set names only; ALREADY was not set
    assert result.path == path
    assert secret_shaped not in repr(result)
    assert secret_shaped not in repr(_overlay.applied)
    assert _overlay.applied is result


def test_import_applies_the_overlay_before_any_submodule(tmp_path):
    """A child process imports composeio with the overlay naming a file: the value is visible to
    a submodule that reads the environment AT ITS OWN IMPORT (fleet.DEFAULT_BROKER)."""
    path = _write(tmp_path, "COMPOSEIO_MQTT_BROKER=broker-from-overlay.invalid\n")
    env = {k: v for k, v in os.environ.items() if k != "COMPOSEIO_MQTT_BROKER"}
    env["COMPOSEIO_OVERLAY_ENV"] = path
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "python"), env.get("PYTHONPATH", "")])
    code = ("import composeio, composeio._overlay as o, composeio.fleet as f;"
            "print(f.DEFAULT_BROKER); print(','.join(o.applied.names))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["broker-from-overlay.invalid", "COMPOSEIO_MQTT_BROKER"]


def test_init_calls_the_loader_before_its_first_submodule_import():
    src = (REPO / "python" / "composeio" / "__init__.py").read_text().splitlines()
    first_sub = next(i for i, l in enumerate(src)
                     if l.startswith(("from composeio.", "import composeio.")))
    call = next(i for i, l in enumerate(src) if "_overlay" in l and not l.lstrip().startswith("#"))
    assert call < first_sub


# --------------------------------------------------------------------------- required names

def test_overlay_required_names_are_declared_once():
    assert _overlay.OVERLAY_REQUIRED == (
        "COMPOSEBUS_MQTT", "COMPOSEIO_MQTT_BROKER", "COMPOSEIO_STORAGE_URL",
        "COMPOSEIO_SECRETS_PATH", "COMPOSEIO_TEMPLATE_STORE_URL", "MQTT")


def test_missing_required_counts_environ_and_the_file(tmp_path):
    names = _overlay.OVERLAY_REQUIRED
    path = _write(tmp_path, "".join(f"{n}=x\n" for n in names[1:]))
    assert _overlay.missing_required(environ={}, path=path) == (names[0],)
    assert _overlay.missing_required(environ={names[0]: "x"}, path=path) == ()
    assert _overlay.missing_required(environ={}, path=str(tmp_path / "absent")) == names
    # an EMPTY value is not a live value
    assert names[0] in _overlay.missing_required(environ={names[0]: ""}, path=path)


def _live_host_marker_set():
    if os.environ.get(_overlay.LIVE_HOST_MARKER) == "1":
        return True
    return _overlay.read(_overlay.default_path()).values.get(_overlay.LIVE_HOST_MARKER) == "1"


@pytest.mark.skipif(not _live_host_marker_set(),
                    reason="not a live host (COMPOSEIO_LIVE_HOST unset in env and host overlay)")
def test_live_host_has_every_overlay_required_name():
    """[CXC-SCL-SCRUB-OVERLAY-PARITY]: on a live host, a required name that neither the
    environment nor the host overlay supplies means a live function is talking to a neutral
    public default. CI never sets the marker, so this skips there and fails loudly here."""
    missing = _overlay.missing_required(path=_overlay.default_path())
    assert missing == (), (
        f"live host is missing overlay-required names {missing}: add them to "
        f"{_overlay.default_path()} (names only are reported)")


# --------------------------------------------------------------------------- broker fallback

class _FakeMQTT:
    def __init__(self, broker, port, client_id, **kw):
        self.broker, self.port, self.client_id = broker, port, client_id


@pytest.fixture
def fake_mqtt(monkeypatch):
    import composeio.transport_mqtt as tm
    monkeypatch.setattr(tm, "MQTTTransport", _FakeMQTT)
    from composeio import transport_registry
    return transport_registry._mqtt


def test_scene_broker_wins_over_env(fake_mqtt, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_MQTT_BROKER", "env-broker.invalid")
    monkeypatch.setenv("COMPOSEIO_MQTT_PORT", "2883")
    t = fake_mqtt({"type": "mqtt", "broker": "scene-broker.invalid", "port": 1999}, "a")
    assert (t.broker, t.port) == ("scene-broker.invalid", 1999)


def test_absent_scene_broker_falls_back_to_env(fake_mqtt, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_MQTT_BROKER", "env-broker.invalid")
    monkeypatch.setenv("COMPOSEIO_MQTT_PORT", "2883")
    t = fake_mqtt({"type": "mqtt"}, "a")
    assert (t.broker, t.port) == ("env-broker.invalid", 2883)
    t = fake_mqtt({"type": "mqtt", "broker": "", "port": None}, "a")
    assert (t.broker, t.port) == ("env-broker.invalid", 2883)


def test_absent_scene_and_env_fall_back_to_the_neutral_default(fake_mqtt, monkeypatch):
    monkeypatch.delenv("COMPOSEIO_MQTT_BROKER", raising=False)
    monkeypatch.delenv("COMPOSEIO_MQTT_PORT", raising=False)
    t = fake_mqtt({"type": "mqtt"}, "a")
    assert (t.broker, t.port) == ("localhost", 1883)


# --------------------------------------------------------------------------- the scenes

# The eight scenes of P2b L1: each declared a scrubbed broker literal that no env could reach.
LIVE_SCENES = [REPO / p for p in (
    "compose_projects/intelligence/console/console.compose.yaml",
    "compose_projects/intelligence/console/dsdviewer.compose.yaml",
    "compose_projects/intelligence/console/nightwatch.compose.yaml",
    "apps/compose_platform_launch_bar/launch_bar.compose.yaml",
    "apps/compose_platform_launch_bar/busstore_viewer.compose.yaml",
    "apps/compose_platform_launch_bar/docstore_viewer.compose.yaml",
    "compose_projects/carousel/launcher.compose.yaml",
    "test_dashboard.compose.yaml")]


@pytest.mark.parametrize("scene", LIVE_SCENES, ids=lambda p: p.parent.name + "/" + p.name)
def test_live_console_scenes_name_no_broker(scene):
    import yaml
    doc = yaml.safe_load(scene.read_text())
    transports = (doc.get("app") or {}).get("transports") or []
    assert transports, "the scene must still declare its mqtt transport"
    for t in transports:
        assert t.get("type") == "mqtt"
        assert "broker" not in t, f"{scene.name}: broker literal bypasses the env seam"


# --------------------------------------------------------------------------- the shell scripts

SCRIPTS = [REPO / "compose_projects/intelligence/start-console-stack.sh",
           REPO / "compose_projects/intelligence/start-console-daemons.sh",
           REPO / "scripts/repo-health-audit.sh"]
HELPER = REPO / "scripts/lib/overlay-env.sh"


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_scripts_apply_the_overlay_before_their_defaults(script):
    lines = script.read_text().splitlines()
    hook = [i for i, l in enumerate(lines) if "overlay-env.sh" in l and not l.lstrip().startswith("#")]
    assert len(hook) == 1, f"{script.name}: expected one guarded overlay line, found {len(hook)}"
    assert lines[hook[0]].lstrip().startswith("[ -f "), "the line must be guarded"
    defaults = [i for i, l in enumerate(lines)
                if l.lstrip().startswith(("MQTT=\"${MQTT:-", "BROKER_HOST=\"${COMPOSEIO_MQTT_BROKER:-"))]
    assert defaults and hook[0] < min(defaults)


def _bash(snippet, env):
    return subprocess.run(["bash", "-c", snippet], env=env, capture_output=True, text=True,
                          timeout=30)


def test_shell_helper_follows_the_loader_rules(tmp_path):
    path = _write(tmp_path, "# c\nexport A=from-file\nB=$HOME/x\nC=\"q\"\nbad line\nEXPLICIT=file\n")
    env = {"PATH": os.environ["PATH"], "HOME": "/home/placeholder",
           "COMPOSEIO_OVERLAY_ENV": path, "EXPLICIT": "env"}
    snippet = (f'set -euo pipefail; . "{HELPER}"; '
               'printf "%s|%s|%s|%s\\n" "$A" "$B" "$C" "$EXPLICIT"; '
               'bash -c \'printf "%s\\n" "$A"\'')
    out = _bash(snippet, env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines() == ["from-file|$HOME/x|q|env", "from-file"]


def test_shell_helper_none_and_missing_are_no_ops(tmp_path):
    path = _write(tmp_path, "A=from-file\n")
    for value in ("none", str(tmp_path / "absent.env")):
        env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "COMPOSEIO_OVERLAY_ENV": value}
        out = _bash(f'set -euo pipefail; . "{HELPER}"; echo "rc=$? A=${{A:-unset}}"', env)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "rc=0 A=unset"
    assert os.path.exists(path)


# --------------------------------------------------------------------------- H1: store browser

ENV_STORE = "http://env-store.invalid:8420"


@pytest.fixture
def env_store_url(monkeypatch):
    import composeio.resolve as r
    monkeypatch.setattr(r, "BUS_STORAGE_URL", ENV_STORE)   # what COMPOSEIO_STORAGE_URL yields
    return r


def test_hostless_docstore_browser_keeps_the_process_storage_url(env_store_url):
    from composeio.render.widgets.store_browser import DocStoreAdapter, StoreBrowserWidget
    a = DocStoreAdapter()
    assert a.client.base_url == ENV_STORE
    assert env_store_url.BUS_STORAGE_URL == ENV_STORE
    StoreBrowserWidget("w", store="docstore")
    assert env_store_url.BUS_STORAGE_URL == ENV_STORE


def test_hostless_busstore_browser_uses_the_process_storage_url(env_store_url):
    from composeio.render.widgets.store_browser import BusStoreAdapter
    assert BusStoreAdapter().client.base_url == ENV_STORE


def test_no_store_browser_path_hard_codes_a_storage_host():
    """The scene parser and the widget defaults must leave the host UNSET when a scene names
    none, so the process's own storage URL (COMPOSEIO_STORAGE_URL) governs."""
    parser_src = (REPO / "python/composeio/render/engine/parser.py").read_text()
    widget_src = (REPO / "python/composeio/render/widgets/store_browser.py").read_text()
    assert 'spec.get("host", "mqtt-broker.local")' not in parser_src
    assert 'host: str = "mqtt-broker.local"' not in widget_src


def test_an_explicit_store_browser_host_is_still_honoured(env_store_url):
    from composeio.render.widgets.store_browser import BusStoreAdapter, DocStoreAdapter
    assert BusStoreAdapter(host="scene-store.invalid").client.base_url == "http://scene-store.invalid:8420"
    assert DocStoreAdapter(host="scene-store.invalid").client.base_url == "http://scene-store.invalid:8420"


# --------------------------------------------------------------------------- L5: memory card

CONSOLE = REPO / "compose_projects/intelligence/console/console.compose.yaml"


def _console():
    import yaml
    return yaml.safe_load(CONSOLE.read_text())


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_console_memory_card_carries_no_seed_snapshot():
    """The seed rows were a snapshot of one user's private memory index (P2b L5)."""
    ns = [n for n in _console().get("namespaces", [])
          if isinstance(n, dict) and n.get("name") == "self/architect/memories"]
    assert len(ns) == 1
    assert ns[0].get("initial") == []


def test_console_memory_card_declares_no_open_dir():
    cards = [n for n in _walk(_console()) if n.get("id") == "config_memory"]
    assert len(cards) == 1 and "open_dir" not in cards[0]
    assert "-Users-you" not in CONSOLE.read_text()


def test_memory_open_dir_is_worked_out_at_runtime(monkeypatch, tmp_path):
    from composeio.render.widgets import agents_skills as a
    home = str(tmp_path / "home" / "someone")
    monkeypatch.setenv("HOME", home)
    for k in ("COMPOSEIO_MEMORY_DIR", "MEMORY_MONITOR_DIR", "COMPOSEIO_CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(k, raising=False)
    slug = home.replace(os.sep, "-")
    want = os.path.join(home, ".claude", "projects", slug, "memory")
    assert a.AgentsSkillsWidget("m", mode="memory").open_dir == want
    monkeypatch.setenv("COMPOSEIO_CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    assert a.AgentsSkillsWidget("m", mode="memory").open_dir == str(tmp_path / "proj" / "memory")
    monkeypatch.setenv("COMPOSEIO_MEMORY_DIR", str(tmp_path / "mem"))
    assert a.AgentsSkillsWidget("m", mode="memory").open_dir == str(tmp_path / "mem")
    # an explicit scene open_dir still wins, and agents/skills keep their repo-relative dirs
    assert a.AgentsSkillsWidget("m", mode="memory", open_dir="/x").open_dir == "/x"
    assert a.AgentsSkillsWidget("s", mode="skills").open_dir == ".claude/skills"


def test_memory_open_dir_matches_the_publisher_rule(monkeypatch, tmp_path):
    """The (Open) reveal and the publisher must name the same directory."""
    import importlib.util
    home = str(tmp_path / "h")
    monkeypatch.setenv("HOME", home)
    for k in ("COMPOSEIO_MEMORY_DIR", "MEMORY_MONITOR_DIR", "COMPOSEIO_CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location(
        "_mp_for_test", REPO / "compose_projects/intelligence/publisher/memory_publisher.py")
    mp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mp)
    from composeio.render.widgets import agents_skills as a
    assert a.AgentsSkillsWidget("m", mode="memory").open_dir == mp.resolve_dir()


# --------------------------------------------------------------------------- every conftest

_GUARD = re.compile(r'environ\["COMPOSEIO_OVERLAY_ENV"\]\s*=\s*"none"')


def _all_conftests():
    skip = {"node_modules", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
    for p in sorted(REPO.rglob("conftest.py")):
        rel = p.relative_to(REPO)
        if not any(part in skip or part.startswith(".") for part in rel.parts[:-1]):
            yield p


def test_conftest_discovery_is_not_vacuous():
    found = {str(p.relative_to(REPO)) for p in _all_conftests()}
    assert {"python/tests/conftest.py", "tests/conftest.py", "compose_projects/conftest.py"} <= found
    assert len(found) >= 10


@pytest.mark.parametrize("conftest", list(_all_conftests()), ids=lambda p: str(p.relative_to(REPO)))
def test_every_conftest_turns_the_overlay_off_before_composeio(conftest):
    """No test anywhere may read a developer's live overlay: every conftest forces
    COMPOSEIO_OVERLAY_ENV=none, and does so before it imports composeio itself."""
    lines = conftest.read_text().splitlines()
    guard = [i for i, l in enumerate(lines) if _GUARD.search(l) and not l.lstrip().startswith("#")]
    assert guard, f"{conftest.relative_to(REPO)} lacks the overlay-off guard"
    # real import statements only (ast), anywhere in the file: prose in a docstring that
    # happens to start with "from composeio" is not an import
    import ast
    imports = [node.lineno - 1 for node in ast.walk(ast.parse("\n".join(lines)))
               if (isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "composeio")
               or (isinstance(node, ast.Import) and any(a.name.split(".")[0] == "composeio"
                                                        for a in node.names))]
    assert not imports or guard[0] < min(imports)
