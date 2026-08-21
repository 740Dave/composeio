"""[SPEC:CXC-TERM] [CXC-IC-CHANNEL-INSTANCE] — the OTHER half of the identity defect.

[CXC-TERM-CHILD-ID] renamed every surface the WIDGET controls — the singleton lock, the bus
subscribe, the telemetry namespace, the refusal publish — and the ONE surface it does not
control is the one that caused the original outage.  Measured on this tree, 2026-08-11:

    .mcp.json  composeio-channel  env: {"INTELLIGENCE_ID": "architect", ...}
    child_argv():   no --mcp-config, no --strict-mcp-config
    widget.cwd:     None -> the host's cwd -> THE REPO
    HOST_IDENTITY_ENV: INTELLIGENCE_ID appears 0 times

So a widget-hosted child loaded the REPO's `.mcp.json`, and its `channel.mjs` bound
`intelligence/architect/in|out|permission/*` — THE HOST'S TOPICS — no matter what
`instance_id` the scene declared.  Symptom 1 of the first live spawn ("the child's channel
displaced the host's and the host lost its voice") was still live on the child's side: the
two halves of the fix disagreed with each other.

⚠️ THE ENV IS NOT THE LEVER.  An `env` block in `.mcp.json` OVERRIDES the inherited
environment for that server, so neither setting nor stripping `INTELLIGENCE_ID` in the
child's environment can move the binding.  The config file is the only lever.  The strip is
done anyway (`HOST_IDENTITY_ENV`) — two independent reasons the child cannot be the host.

Every test here is provable WITHOUT a real `claude`: the config is written before the fork,
so the identity it binds is a parent-side fact.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio import ptycore                                             # noqa: E402
from composeio.ptycore import (                                           # noqa: E402
    CHANNEL_FLAGS, CHANNEL_SERVER, HOST_IDENTITY_ENV, BootstrapRefused,
    build_child_mcp_config, build_claude_args, child_mcp_config_dir,
    remove_child_mcp_config, repo_mcp_config_path, sanitize_child_env,
    write_child_mcp_config,
)
from composeio.render.widget import Rect                                  # noqa: E402
from composeio.render.widgets.terminal import TerminalWidget              # noqa: E402

REAL_MCP = os.path.join(REPO, ".mcp.json")
HOSTED = "architect-hosted"


class _Ctx:
    def __init__(self) -> None:
        self.published: list = []
        self.headless = False
        self.bus = None

    def request_redraw(self, rect=None): pass
    def schedule_update(self, fn, target=None): fn()
    def publish(self, ns, value, source=None): self.published.append((ns, value))
    def register_clocked(self, w): pass
    def unregister_clocked(self, w): pass


def _widget(**kw) -> TerminalWidget:
    kw.setdefault("role", "architect")
    kw.setdefault("instance_id", HOSTED)
    w = TerminalWidget("term", **kw)
    w.arranged_rect = Rect(0, 0, 800, 500)
    return w


@pytest.fixture
def w():
    """A widget whose generated config is ALWAYS cleaned up, pass or fail — the test suite
    must not be the thing that leaves stale identities in /tmp."""
    widget = _widget()
    try:
        yield widget
    finally:
        widget._release_mcp_config()


def _real() -> dict:
    with open(REAL_MCP, encoding="utf-8") as fh:
        return json.load(fh)


# ══ 1. the config is DERIVED — one key changed, everything else carried ════
@pytest.mark.requires_repo_mcp_config
def test_the_generated_config_is_the_REAL_server_set_with_ONE_key_rebound():
    """The child NEEDS the other servers.  The first live spawn's wake ritual ran
    `lts_selfcheck`, `lts_find` x9 and `lts_aggregate`; a hand-written config carrying only
    the channel would break the bootstrap the child is spawned to perform.  So: assert the
    SERVER SET, not a count, and assert every non-channel server is carried VERBATIM."""
    cfg = build_child_mcp_config(HOSTED)
    real = _real()
    assert set(cfg["mcpServers"]) == set(real["mcpServers"]), (
        "a server was dropped or invented — the child's toolchain is not the host's")
    for name, server in real["mcpServers"].items():
        if name == CHANNEL_SERVER:
            continue
        assert cfg["mcpServers"][name] == server, f"{name} was not carried verbatim"
    # every top-level key other than mcpServers, too
    for key, value in real.items():
        if key != "mcpServers":
            assert cfg[key] == value


@pytest.mark.requires_repo_mcp_config
def test_ONLY_the_channel_identity_moves():
    """The channel server keeps its command, its args and its OTHER env (the broker URL) —
    a rewrite that lost `COMPOSEBUS_MQTT` would put the child on a different bus."""
    cfg = build_child_mcp_config(HOSTED)
    real_ch = _real()["mcpServers"][CHANNEL_SERVER]
    got_ch = cfg["mcpServers"][CHANNEL_SERVER]
    assert got_ch["command"] == real_ch["command"] and got_ch["args"] == real_ch["args"]
    assert got_ch["env"]["INTELLIGENCE_ID"] == HOSTED
    for key, value in real_ch["env"].items():
        if key != "INTELLIGENCE_ID":
            assert got_ch["env"][key] == value, f"channel env {key} was lost"
    # and the derivation MUTATES NOTHING SHARED: a second call sees the same source
    assert build_child_mcp_config("someone-else")["mcpServers"][CHANNEL_SERVER]["env"][
        "INTELLIGENCE_ID"] == "someone-else"


@pytest.mark.requires_repo_mcp_config
def test_a_spawn_under_architect_hosted_binds_architect_hosted_and_NEVER_architect(w):
    """The acceptance sentence, literally.  `channel.mjs` builds every topic as
    `compose_projects/intelligence/${INTELLIGENCE_ID}/...` (channel.mjs:46,61-76), so the id in this file IS
    the binding — and the host's id must not survive anywhere in the bytes."""
    path = w._ensure_mcp_config()
    blob = open(path, encoding="utf-8").read()
    assert json.loads(blob)["mcpServers"][CHANNEL_SERVER]["env"]["INTELLIGENCE_ID"] == HOSTED
    assert '"INTELLIGENCE_ID": "architect"' not in blob, (
        "the host's id survived into the child's config — this is the outage")
    assert f'"INTELLIGENCE_ID": "{HOSTED}"' in blob


# ══ 2. argv carries it, and STRICTLY ════════════════════════════════════════
@pytest.mark.requires_repo_mcp_config
def test_child_argv_carries_the_generated_config_and_strict(w):
    argv = w.child_argv()
    assert "--strict-mcp-config" in argv, (
        "without strict, `claude` ALSO loads the repo's .mcp.json and re-acquires the "
        "host's identity — the flag is half the fix, not decoration")
    i = argv.index("--mcp-config")
    path = argv[i + 1]
    assert path == w.mcp_config_path
    assert os.path.isfile(path), "argv names a config that does not exist AT SPAWN"
    assert json.load(open(path, encoding="utf-8"))["mcpServers"][CHANNEL_SERVER][
        "env"]["INTELLIGENCE_ID"] == HOSTED


@pytest.mark.requires_repo_mcp_config
def test_the_config_is_generated_ONCE_per_session(w):
    assert w.child_argv() and w.child_argv()   # twice
    first = w.mcp_config_path
    assert w.child_argv().count(first) == 1
    assert w.mcp_config_path == first, "a second argv build minted a second identity file"


@pytest.mark.requires_repo_mcp_config
def test_mcp_config_is_VARIADIC_so_it_can_never_swallow_the_bootstrap_positional(w):
    """`--mcp-config <configs...>` takes a SPACE-SEPARATED list on this build (`claude
    --help`), so it consumes every following bare word up to the next `--flag`.  The
    bootstrap prompt rides argv as a POSITIONAL ([CXC-TERM-BOOTSTRAP-ARGV]) — emit the config
    flag last and the 200-character wake instruction is parsed as a config path and the child
    dies at startup.  It is therefore emitted upstream of `CHANNEL_FLAGS`."""
    argv = w.child_argv("Read /tmp/x.md and complete the bootstrap it describes.")
    i = argv.index("--mcp-config")
    assert argv[i + 2].startswith("--"), "a bare word follows the config path"
    assert argv.index(CHANNEL_FLAGS[0]) > i
    assert argv[-1].startswith("Read "), "the positional prompt is still last"


def test_an_explicit_claude_args_tail_generates_NO_config():
    """"The caller owns the whole tail" is the launcher's existing contract and it covers the
    MCP flags: a caller spelling its own argv spells its own config, and we must not write a
    file behind its back (nor leave one to clean up)."""
    w = _widget(claude_args=["-c", "pass"])
    try:
        assert "--mcp-config" not in w.child_argv()
        assert w.mcp_config_path == ""
    finally:
        w._release_mcp_config()


def test_build_claude_args_without_a_config_is_BYTE_IDENTICAL_to_before():
    """The CLI launcher is the PRIMARY instance: the repo config's identity is already its
    own, so it passes no config and its argv must not have moved a byte."""
    assert build_claude_args() == list(CHANNEL_FLAGS)
    assert build_claude_args("sid", skip_permissions=True) == (
        ["--resume", "sid"] + list(CHANNEL_FLAGS) + ["--dangerously-skip-permissions"])


# ══ 3. the second, independent reason: the env ══════════════════════════════
def test_INTELLIGENCE_ID_is_stripped_from_the_child_env():
    """It cannot decide the channel binding (the config's `env` block overrides it), and it
    is stripped anyway: the console, the launcher and every `self/<id>/*` reader key on it,
    so a child inheriting the host's would report as the host on all of them."""
    assert "INTELLIGENCE_ID" in HOST_IDENTITY_ENV
    env = sanitize_child_env(base={"INTELLIGENCE_ID": "architect", "PATH": "/bin"})
    assert "INTELLIGENCE_ID" not in env
    assert env["PATH"] == "/bin"


def test_the_strip_reason_names_the_config_as_the_ACTUAL_lever():
    """A future reader who deletes the generated config because "we strip the env now" would
    reopen the defect exactly.  The reason string has to say so."""
    why = HOST_IDENTITY_ENV["INTELLIGENCE_ID"].lower()
    assert "mcp" in why and "override" in why


# ══ 4. where it lives, and what removes it ══════════════════════════════════
@pytest.mark.requires_repo_mcp_config
def test_the_generated_config_lives_OUTSIDE_the_repo_and_is_not_the_tracked_file(w):
    path = w._ensure_mcp_config()
    assert os.path.dirname(path) == os.path.abspath(child_mcp_config_dir())
    assert not path.startswith(REPO + os.sep), (
        "a generated config inside the tree can be mistaken for — or committed over — the "
        "tracked .mcp.json")
    assert os.path.basename(path) != ".mcp.json"


@pytest.mark.requires_repo_mcp_config
def test_stop_session_deletes_it(w, tmp_path, monkeypatch):
    monkeypatch.setenv("CHILD_MCP_CONFIG_DIR", str(tmp_path))
    path = w._ensure_mcp_config()
    assert os.path.isfile(path)
    w.stop_session()                       # no child: the teardown path still cleans
    assert not os.path.exists(path)
    assert w.mcp_config_path == ""


@pytest.mark.requires_repo_mcp_config
def test_a_stale_config_for_the_same_id_is_replaced_not_accumulated(tmp_path, monkeypatch):
    """The file names an identity; a stale one is a stale identity.  A host that was SIGKILLed
    cannot clean up after itself, so the next write for that id does it."""
    monkeypatch.setenv("CHILD_MCP_CONFIG_DIR", str(tmp_path))
    first = write_child_mcp_config(HOSTED)
    second = write_child_mcp_config(HOSTED)
    assert first != second and not os.path.exists(first) and os.path.isfile(second)
    assert len(os.listdir(tmp_path)) == 1
    # a DIFFERENT id is untouched — two hosted children coexist
    other = write_child_mcp_config("architect-review")
    assert os.path.isfile(second) and os.path.isfile(other)
    remove_child_mcp_config(second)
    remove_child_mcp_config(other)
    remove_child_mcp_config("")            # tolerant by design; teardown must not raise


# ══ 5. THE HOST IS UNTOUCHED ════════════════════════════════════════════════
@pytest.mark.requires_repo_mcp_config
def test_the_hosts_own_mcp_json_is_never_written(w, tmp_path, monkeypatch):
    """Regressing the live host to fix the child is the worst outcome available here."""
    before = open(REAL_MCP, "rb").read()
    stat_before = os.stat(REAL_MCP)
    monkeypatch.setenv("CHILD_MCP_CONFIG_DIR", str(tmp_path))
    w.child_argv("Read /tmp/x.md and complete the bootstrap it describes.")
    w.stop_session()
    assert open(REAL_MCP, "rb").read() == before
    assert os.stat(REAL_MCP).st_mtime == stat_before.st_mtime
    # ...and the host's channel identity is STILL `architect`
    assert json.loads(before)["mcpServers"][CHANNEL_SERVER]["env"][
        "INTELLIGENCE_ID"] == "architect"


def test_the_derivation_reads_the_repo_config_by_default():
    assert os.path.abspath(repo_mcp_config_path()) == os.path.abspath(REAL_MCP)


# ══ 6. it REFUSES rather than binding the host's topics ═════════════════════
def test_an_underivable_config_REFUSES_the_spawn_on_the_glass_AND_the_bus(monkeypatch):
    """A child spawned with a config we could not rebind is a child on the HOST's topics.
    That may not degrade and may not log-and-continue ([CXC-TERM-MANIFEST-CHAIN]'s rule,
    applied to the same class of failure): plate, publish, no fork, still re-launchable."""
    monkeypatch.setenv("COMPOSEIO_MCP_CONFIG", "/nonexistent/.mcp.json")
    with pytest.raises(BootstrapRefused) as exc:
        build_child_mcp_config(HOSTED)
    assert "mcp-config-unreadable" in exc.value.message()

    ctx = _Ctx()
    widget = _widget(bootstrap=False)
    widget.on_mount(ctx)
    widget.set_cell_metrics(7.0, 10.0)
    try:
        assert widget.start_session() is False
        assert widget.child is None, "it FORKED"
        assert "mcp-config-unreadable" in widget._plate
        topics = [ns for ns, _v in ctx.published]
        assert f"intelligence/{HOSTED}/terminal/refusal" in topics
        assert widget.mcp_config_path == ""
    finally:
        widget.on_unmount()


def test_a_config_with_no_channel_server_REFUSES(tmp_path, monkeypatch):
    """Off the bus SILENTLY is the failure class this whole area exists to end."""
    src = tmp_path / "no-channel.json"
    src.write_text(json.dumps({"mcpServers": {"lts-engine": {"command": "python3"}}}))
    monkeypatch.setenv("COMPOSEIO_MCP_CONFIG", str(src))
    with pytest.raises(BootstrapRefused) as exc:
        build_child_mcp_config(HOSTED)
    assert "no-channel-server" in exc.value.message()


# ══ 7. the whole spawn path, with a stand-in child ══════════════════════════
class _StubChild:
    """Everything `start_session` / `_read_loop` touch, and nothing else.  A stand-in is
    used HERE deliberately: what is under test is a PARENT-SIDE fact (the file that exists,
    and the argv that names it, before `pty.fork()`), and spawning a real `claude` to prove
    it would join the live bus."""

    seen: list = []

    def __init__(self, argv, term_size=None, env_extra=None, cwd=None, log=None):
        self.argv, self.env_extra, self.cwd = list(argv), dict(env_extra or {}), cwd
        self.pid = 4242
        self.exit_status = 0
        _StubChild.seen.append(self)

    def start(self): pass
    def pump(self, sink, timeout=0.05): return False
    def drain_final(self, sink): pass
    def child_exited(self): return True
    def exit_description(self): return "exited 0"
    def shutdown(self): pass


@pytest.mark.requires_repo_mcp_config
def test_the_spawn_hands_the_child_its_OWN_config_and_NOT_the_hosts_id(monkeypatch,
                                                                      tmp_path):
    monkeypatch.setenv("CHILD_MCP_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("INTELLIGENCE_ID", "architect")     # the host, as deployed
    monkeypatch.setattr(ptycore, "PtyChild", _StubChild)
    _StubChild.seen.clear()

    ctx = _Ctx()
    widget = _widget(bootstrap=False)
    widget.on_mount(ctx)
    widget.set_cell_metrics(7.0, 10.0)
    try:
        assert widget.start_session() is True
        child = _StubChild.seen[-1]
        cfg_path = child.argv[child.argv.index("--mcp-config") + 1]
        assert "--strict-mcp-config" in child.argv
        assert os.path.isfile(cfg_path), "the config did not exist AT the fork"
        blob = open(cfg_path, encoding="utf-8").read()
        assert json.loads(blob)["mcpServers"][CHANNEL_SERVER]["env"][
            "INTELLIGENCE_ID"] == HOSTED
        assert '"INTELLIGENCE_ID": "architect"' not in blob
        # ...and the env half, on the SAME spawn: the host's id is not in there either
        assert "INTELLIGENCE_ID" not in sanitize_child_env(extra=child.env_extra)
    finally:
        widget.on_unmount()
    assert not os.path.exists(cfg_path), "unmount left a stale identity on disk"
