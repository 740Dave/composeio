"""[SPEC:CXC-TERM] TERM-C13..C18 — what the FIRST LIVE SPAWN returned.

Every criterion here maps one-to-one onto a finding of the first time this widget hosted a
real `claude` (2026-08-11).  That is why they are in their own file: every prior test in
`test_terminal_widget.py` used a `python -c` stand-in child, and a stand-in is exactly why
none of these were caught.  A stand-in cannot answer a startup dialog, cannot set `?2004h`,
cannot take a channel, and cannot displace anything — so five of the six defects were
structurally invisible to the suite that was green.

| row | criterion | provable HERE? |
|-----|-----------|----------------|
| C13 | the bootstrap arrives WITHOUT a keystroke | the ZERO-BYTES half and the argv/file shape: YES, against a real `pty.fork()`. Whether `claude` HONOURS a positional prompt when dialogs intervene: **glass only** — the mandatory, non-delegable half |
| C14 | one id, one instance | YES — the refusal is BEFORE the fork, so it is provable without a child |
| C15 | an unoriented instance is never indistinguishable | YES — chain, zero forks, the refusal publish and the plate |
| C16 | the hosted child has its OWN fuel gauge | the wiring, the thresholds, the FYI/GUIDANCE split and the child-transcript resolution: YES. That the gauge tracks a REAL child's real usage: **glass** |
| C17 | ⌘V through the ONE existing path | YES — byte identity against `on_text` |
| C18 | the census measures the configuration actually RUN | the argv FIDELITY: YES. The capture itself: **glass** (it spawns a bus-joining child) |
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.ptycore import (                                          # noqa: E402
    HOSTED_CHILD_THRESHOLDS, PASTE_END, PASTE_START, BootstrapRefused, build_monitor,
    resolve_child_jsonl,
)
from composeio.render.widget import Rect                                 # noqa: E402
from composeio.render.widgets import terminal as term                    # noqa: E402
from composeio.render.widgets.terminal import TerminalWidget             # noqa: E402

_KMOD_GUI = 0x0C00
_KMOD_CTRL = 0x00C0


# ── harness ────────────────────────────────────────────────────────────────
class _Bus:
    """The slice of the bus this widget uses: namespace publish + the FOREIGN-channel seam
    the harness messages ride (`compose_projects/intelligence/<id>/in` is a raw peer topic, not a composeio
    namespace)."""

    def __init__(self) -> None:
        self.foreign: list = []
        self.subs: list = []

    def subscribe(self, ns, cb):
        self.subs.append(ns)

    def publish_foreign(self, channel, payload):
        self.foreign.append((channel, payload))
        return True


class _Ctx:
    def __init__(self) -> None:
        self.published: list = []
        self.bus = _Bus()
        self.headless = False
        self.clocked: list = []

    def request_redraw(self, rect=None): pass
    def schedule_update(self, fn, target=None): fn()
    def publish(self, ns, value, source=None): self.published.append((ns, value))
    def register_clocked(self, w): self.clocked.append(w)
    def unregister_clocked(self, w): pass


def _widget(ctx=None, mount=True, **kw) -> tuple:
    ctx = ctx or _Ctx()
    kw.setdefault("role", "architect")
    kw.setdefault("instance_id", "architect-hosted")
    w = TerminalWidget("term", **kw)
    w.arranged_rect = Rect(0, 0, 800, 500)
    if mount:
        w.on_mount(ctx)
    w.set_cell_metrics(7.0, 10.0)
    return w, ctx


@pytest.fixture(autouse=True)
def _clean_id_registry():
    """The mounted-id registry is module state; a leaked claim would make an unrelated test
    refuse for the wrong reason."""
    yield
    term._MOUNTED_IDS.clear()


def _manifest(tmp_path, name="arch.meta.json", mid="architect") -> str:
    p = tmp_path / name
    p.write_text(json.dumps({
        "id": mid,
        "role_descriptor": {"path": "intelligence/roles/architect.md"},
        "dusk_gifts": [{"path": "bootstrap/dusk-gifts/registration-protocol.md"}],
    }))
    return str(p)


# ══ TERM-C14 — ONE id, ONE instance ════════════════════════════════════════
def test_a_child_spawned_with_the_HOSTS_id_is_REFUSED_BEFORE_THE_FORK(monkeypatch, tmp_path):
    """The root defect, measured on the first live spawn: the scene said
    `intelligence_id: architect` — the host's own id — and the child's `channel.mjs`
    displaced the host's, which exited and took David's Architect off the air.

    A warning would not have been enough and that is the whole point of the ruling: all
    three symptoms are invisible FROM INSIDE the child.  So the assertion is that `pty.fork`
    is called ZERO times, not that a message was logged."""
    monkeypatch.setenv("INTELLIGENCE_ID", "architect")
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    forks: list = []
    import pty as _pty
    monkeypatch.setattr(_pty, "fork", lambda: forks.append(1) or (_ for _ in ()).throw(
        AssertionError("pty.fork() was CALLED on a refused spawn")))

    w, _ctx = _widget(instance_id="architect", meta_manifest=_manifest(tmp_path))
    why = w.refusal()
    assert "ID COLLISION" in why and "architect" in why
    assert w.start_session() is False
    assert forks == []
    # the plate names it, so the human sees the cause rather than an empty terminal
    assert "ID COLLISION" in w._idle_plate()
    # and it is RE-LAUNCHABLE: fix the scene, and the same widget spawns
    assert w._child is None


def test_a_SIBLING_widget_cannot_take_an_id_another_one_already_holds(tmp_path, monkeypatch):
    """[DD-TERM-CHILD-ID-SCHEME] branch (a)'s known weakness, closed: the declared scheme's
    failure mode is a copy-pasted scene, and the refusal is WIDENED past the host's own id to
    cover siblings in the same process."""
    monkeypatch.delenv("INTELLIGENCE_ID", raising=False)
    ctx = _Ctx()
    first, _ = _widget(ctx, instance_id="architect-hosted")
    second = TerminalWidget("term2", role="architect", instance_id="architect-hosted")
    second.arranged_rect = Rect(0, 0, 800, 500)
    second.on_mount(ctx)
    second.set_cell_metrics(7.0, 10.0)
    assert first.refusal() == ""
    assert "ID COLLISION" in second.refusal()
    assert "term" in second.refusal()
    # unmounting the holder releases the id — a tab switch must not poison it forever
    first.on_unmount()
    assert second.refusal() == ""


def test_every_namespace_keys_on_the_CHILDS_id(tmp_path, monkeypatch):
    """[CXC-TERM-CHILD-ID]: "the child's namespaces follow the child's id ... One id, one
    instance, everywhere — a mixed keying is symptom 2 in a new costume"."""
    monkeypatch.delenv("INTELLIGENCE_ID", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    taken: list = []
    sys.path.insert(0, os.path.join(REPO, "compose_projects", "intelligence", "publisher"))
    import singleton as sg
    monkeypatch.setattr(sg, "acquire_singleton_superseding",
                        lambda name, *a, **k: taken.append(name) or None)

    w, ctx = _widget(role="architect", instance_id="architect-hosted",
                     claude_bin=sys.executable,
                     claude_args=["-c", "import time; time.sleep(0.4)"],
                     meta_manifest=_manifest(tmp_path))
    w.attach_emulator(80, 24)
    assert w.intelligence_id == "architect-hosted"
    assert w.role == "architect"
    assert ctx.bus.subs == ["intelligence/architect-hosted/out"]
    assert w.start_session() is True
    try:
        assert taken == ["intelligence-launcher-architect-hosted"]
        w._enter_degraded("test")
        ns, _payload = ctx.published[-1]
        assert ns == "intelligence/architect-hosted/terminal/degraded"
    finally:
        w.stop_session()


# ══ TERM-C15 — the manifest chain, and a refusal you cannot miss ══════════
def test_leg_2_MISSES_leg_3_HITS_and_the_child_is_oriented(tmp_path, monkeypatch):
    """The chain is REQUIRED BY the id fix, not a nicety beside it: give every hosted child a
    distinct id and `<id>.meta.json` misses for every child that is not hand-curated."""
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    meta = tmp_path / "compose_projects" / "intelligence" / "meta"
    meta.mkdir(parents=True)
    (meta / "architect.meta.json").write_text(json.dumps(
        {"id": "architect", "role_descriptor": {"path": "roles/architect.md"}}))

    w, _ctx = _widget(role="architect", instance_id="architect-hosted")
    assert not os.path.exists(meta / "architect-hosted.meta.json"), "leg 2 must MISS here"
    instruction = w._bootstrap_argv()
    body = open(instruction.split()[1], encoding="utf-8").read()
    assert body.splitlines()[0].endswith("architect.meta.json and complete the bootstrap "
                                         "it describes.")
    assert "roles/architect.md" in body
    assert w.refused is None


def test_an_exhausted_chain_refuses_with_ZERO_forks_and_a_bus_visible_payload(
        tmp_path, monkeypatch):
    """[CXC-TERM-MANIFEST-CHAIN]: "no fork, no degraded boot, no silent orientation gap".

    BOTH surfaces are asserted.  A refusal that reached only the log is the defect itself —
    `terminal.py::_log` was literally `pass`, so "degrades and logs" meant DISCARDS."""
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.delenv("INTELLIGENCE_ID", raising=False)
    (tmp_path / "compose_projects" / "intelligence" / "meta").mkdir(parents=True)
    forks: list = []
    import pty as _pty
    monkeypatch.setattr(_pty, "fork", lambda: forks.append(1) or (_ for _ in ()).throw(
        AssertionError("pty.fork() was CALLED on a refused spawn")))

    w, ctx = _widget(role="nobody", instance_id="nobody-7")
    w.attach_emulator(80, 24)
    assert w.start_session() is False
    assert forks == []

    # 1. THE BUS
    ns, payload = ctx.published[-1]
    assert ns == "intelligence/nobody-7/terminal/refusal"
    assert payload["refused"] is True and payload["reason"] == "no-manifest"
    assert payload["id"] == "nobody-7" and payload["role"] == "nobody"
    assert [os.path.basename(p) for p in payload["paths_tried"]] == [
        "nobody-7.meta.json", "nobody.meta.json"]
    # 2. THE GLASS — the plate names the id, the role and every path, IN ORDER
    plate = w._idle_plate()
    assert "nobody-7.meta.json" in plate and "nobody.meta.json" in plate
    assert plate.index("nobody-7.meta.json") < plate.index("nobody.meta.json")
    assert "REFUSED" in w.status_line()
    # 3. RE-LAUNCHABLE — a refusal costs one keypress to retry
    assert w._child is None and w.refused is not None


def test_the_widget_log_is_no_longer_a_DISCARDING_sink():
    """The mechanism behind "logged only" being worse than useless here: `_log` was
    `def _log(self, line): pass`, so the core's diagnosis was destroyed at the call site and
    an unoriented instance looked identical to an oriented one."""
    w, _ctx = _widget()
    w._log("[ptycore] something diagnostic")
    assert w.log_lines() == ["[ptycore] something diagnostic"]
    import inspect
    assert "pass" != inspect.getsource(TerminalWidget._log).strip().splitlines()[-1].strip()


# ══ TERM-C13 — the bootstrap arrives WITHOUT a keystroke ═══════════════════
def test_NO_BYTES_reach_the_master_fd_for_the_WHOLE_session(tmp_path, monkeypatch):
    """The unconditional zero-bytes assertion — no timer carve-out, because there is no
    timer.  A REAL `pty.fork()` and the REAL write path, watched from spawn until a
    synthetic keypress: the first byte on that fd must be the human's."""
    from composeio import ptycore
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.delenv("INTELLIGENCE_ID", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    writes: list = []
    real = ptycore.PtyChild._write
    monkeypatch.setattr(ptycore.PtyChild, "_write",
                        lambda self, data: (writes.append(data), real(self, data))[1])

    w, _ctx = _widget(claude_bin=sys.executable,
                      claude_args=["-c", "import time; time.sleep(1.2)"],
                      meta_manifest=_manifest(tmp_path))
    w.attach_emulator(80, 24)
    assert w.start_session() is True
    try:
        time.sleep(0.8)              # far past the 0.3-3.0s the retired timer used
        assert writes == [], f"the widget wrote {writes!r} to a starting child"
        w.on_key("A_key_that_produces_text_is_not_this", 0)
        assert writes == []
        w.on_text("x")               # the human's FIRST keypress
        assert writes == [b"x"]
    finally:
        w.stop_session()


def test_the_MIDSESSION_seam_still_actuates_after_the_deletion(tmp_path, monkeypatch):
    """[CXC-TERM-MIDSESSION-SEAM].  Both seams end in bytes on the same fd through the same
    lock, so a builder deleting "the injection path" can plausibly delete both.  What
    distinguishes them is the STATE they target: mid-session has a reader that is definitely
    ready, so nothing about it was unsound.  `/compact` self-direction is the entire reason
    the harness exists."""
    from composeio.ptycore import COMPACT_CLEAR, COMPACT_SUBMIT
    w, _ctx = _widget()

    class _Rec:
        def __init__(self): self.log = []
        def inject_line(self, line, kind="inject"):
            self.log.append((kind, line))
    rec = _Rec()
    w._child = rec

    w.on_out_message(json.dumps({"cmd": "compact", "direction": "carry the ledger"}))
    w.on_out_message({"cmd": "inject", "text": "hello"})
    assert rec.log == [("compact", "/compact carry the ledger"), ("inject", "hello")]
    # the three-phase mechanics themselves are the CORE's and are unchanged
    assert (COMPACT_CLEAR, COMPACT_SUBMIT) == ("\x1b\x1b", "\r")
    # and the subscription that carries them still exists, with ONE handler
    import inspect
    src = inspect.getsource(TerminalWidget.on_mount)
    assert src.count("/out") == 1


@pytest.mark.requires_repo_mcp_config
def test_the_argv_prompt_is_a_POINTER_and_the_file_names_the_manifest(tmp_path, monkeypatch):
    """The ratified form, both halves.  The argv element is short and names the prompt FILE;
    the FILE's first line names the MANIFEST.  This is the only shape with live evidence:
    David typed it by hand and the child read all nine artifacts and ran `lts_selfcheck`, a
    step that exists only in the CITED spec — which is how we know the pointer was FOLLOWED
    rather than paraphrased."""
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "prompts"))
    manifest = _manifest(tmp_path)
    w, _ctx = _widget(meta_manifest=manifest)
    instruction = w._bootstrap_argv()
    assert instruction.startswith("Read /") and len(instruction) < 400
    prompt_file = instruction.split()[1]
    assert os.path.isabs(prompt_file) and os.path.isfile(prompt_file)
    body = open(prompt_file, encoding="utf-8").read()
    assert body.splitlines()[0] == f"Read {manifest} and complete the bootstrap it describes."
    assert len(body) > len(instruction) * 5, "the body was inlined onto argv"
    assert w.child_argv(instruction)[-1] == instruction


# ══ TERM-C16 — the hosted child's OWN fuel gauge ═══════════════════════════
class _Publisher:
    def __init__(self): self.msgs = []
    def __call__(self, text="", kind="", extra=None):
        self.msgs.append({"text": text, "kind": kind, "extra": extra or {}})


def _monitor_at(pcts, publisher, thresholds=HOSTED_CHILD_THRESHOLDS):
    """The SHARED factory, with a scripted reader — the same class the launcher runs."""
    box = {"pct": pcts[0]}
    mon = build_monitor(intelligence_id="architect-hosted", publish_in=publisher,
                        jsonl_path="", thresholds=thresholds, enabled=True,
                        state_file="/nonexistent/state.json")
    mon.read_context = lambda: {"pct": box["pct"], "level": box["pct"] / 100.0,
                                "tokens": 0, "window": 0, "source": "test"}
    mon.hb_base_s = 0                      # heartbeat off: thresholds are what is under test
    return mon, box


def test_the_widget_constructs_the_LAUNCHERS_monitor_with_thresholds_90_95(tmp_path,
                                                                           monkeypatch):
    """[CXC-TERM-CHILD-TELEMETRY]: REUSE BY REFERENCE.  `HarnessMonitor` was instantiated
    ONLY by `launcher.py` and `terminal.py` referenced it NOWHERE, so a hosted child had no
    alerts, no heartbeat and no fuel gauge — while the prompt told it to watch
    `self/context_level`, a permanently silent namespace."""
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.delenv("INTELLIGENCE_ID", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    w, _ctx = _widget(claude_bin=sys.executable,
                      claude_args=["-c", "import time; time.sleep(0.5)"],
                      meta_manifest=_manifest(tmp_path))
    w.attach_emulator(80, 24)
    assert w.start_session() is True
    try:
        mon = w.monitor
        assert mon is not None, "the hosted child got NO monitor"
        assert type(mon).__name__ == "HarnessMonitor", type(mon)
        assert tuple(mon.thresholds) == (90, 95), mon.thresholds
        assert w._monitor_thread is not None and w._monitor_thread.is_alive()
    finally:
        w.stop_session()
    assert w.monitor is None, "the monitor outlived the child"


def test_90_is_an_FYI_and_95_is_the_hand_over_GUIDANCE():
    """The distinction `monitor.py` already draws, preserved EXACTLY: 90 is a heads-up, 95 is
    the actionable hand-over checklist.  Collapsing 95 into another FYI removes the only
    message that tells the instance what to DO."""
    sys.path.insert(0, os.path.join(REPO, "compose_projects", "intelligence", "launcher"))
    from monitor import KIND_FYI, KIND_GUIDANCE
    pub = _Publisher()
    mon, box = _monitor_at([50.0], pub)
    mon.tick()                                   # prime
    assert pub.msgs == []
    box["pct"] = 91.0
    mon.tick()
    assert [m["kind"] for m in pub.msgs] == [KIND_FYI]
    box["pct"] = 96.0
    mon.tick()
    assert [m["kind"] for m in pub.msgs] == [KIND_FYI, KIND_GUIDANCE]
    assert "prepare a SELF-DIRECTED /compact" in pub.msgs[-1]["text"]


def test_65_and_80_do_NOT_fire_for_a_hosted_child():
    """David, 2026-08-11: 65 and 80 were not useful.  The EXACT assertion, not "non-zero":
    a monitor that fired at 65 would be the CLI default leaking through the factory."""
    pub = _Publisher()
    mon, box = _monitor_at([10.0], pub)
    mon.tick()
    for pct in (66.0, 81.0, 89.9):
        box["pct"] = pct
        mon.tick()
    assert pub.msgs == [], [m["kind"] for m in pub.msgs]
    box["pct"] = 90.0
    mon.tick()
    assert len(pub.msgs) == 1


def test_the_monitor_reads_the_CHILDS_transcript_not_the_HOSTS(tmp_path, monkeypatch):
    """A monitor wired to the host's transcript would report the HOST's context to the CHILD
    — symptom 2 of [CXC-TERM-CHILD-ID] rebuilt on purpose."""
    proj = tmp_path / "projects"
    proj.mkdir()
    host = proj / "host.jsonl"
    host.write_text("{}\n")
    os.utime(host, (1000.0, 1000.0))            # written BEFORE our fork
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(proj))

    # nothing written after the fork yet -> "" (the honest answer, never a newest-wins guess)
    assert resolve_child_jsonl(str(tmp_path), since_ts=2000.0, exclude=(str(host),)) == ""

    child = proj / "child.jsonl"
    child.write_text("{}\n")
    os.utime(child, (3000.0, 3000.0))
    got = resolve_child_jsonl(str(tmp_path), since_ts=2000.0, exclude=(str(host),))
    assert got == str(child)
    # the host's pin is EXCLUDED even when it is the newest thing on disk
    os.utime(host, (9000.0, 9000.0))
    assert resolve_child_jsonl(str(tmp_path), since_ts=2000.0,
                               exclude=(str(host),)) == str(child)


def test_child_OUTPUT_marks_activity_so_the_heartbeat_measures_real_idle():
    """Parity with the launcher's sink, which is where this behaviour already lives: child
    output IS the Intelligence generating.  Without it a hosted child would be nudged while
    it works, and the two-tier cadence would measure the wrong thing entirely."""
    w, _ctx = _widget()

    class _Mon:
        def __init__(self): self.beats = 0
        def note_activity(self): self.beats += 1
        def stop(self): pass
    w._monitor = _Mon()
    w._on_child_bytes(b"the child is streaming a response")
    assert w._monitor.beats == 1


def test_a_context_query_is_ANSWERED_even_with_no_monitor():
    """[CXC-TERM-CHILD-TELEMETRY]: "it gets the explicit 'not enabled this session' answer,
    never silence".  An instruction to watch a signal that never arrives is worse than no
    instruction: the instance waits instead of acting."""
    w, ctx = _widget()
    w._monitor = None
    w.on_out_message(json.dumps({"cmd": "context"}))
    topic, payload = ctx.bus.foreign[-1]
    assert topic == "intelligence/architect-hosted/in"
    body = json.loads(payload)
    assert body["kind"] == "context-report"
    assert "not enabled this session" in body["text"]
    assert body["from"] == "harness"


def test_the_harness_message_rides_the_CHILDS_channel_topic():
    w, ctx = _widget()
    w._publish_to_child(text="hi", kind="context-fyi", extra={"level": 90})
    topic, payload = ctx.bus.foreign[-1]
    assert topic == "intelligence/architect-hosted/in"
    assert json.loads(payload)["level"] == 90


def test_the_generated_prompt_no_longer_names_the_LAUNCHER_as_the_actuator():
    """`ptycore` typed "The launcher will type it" into EVERY child — false for a
    widget-hosted one, where the widget owns the PTY.  One string, both hosts, so it names
    the actuator generically."""
    from composeio.ptycore import build_bootstrap_prompt
    prompt = build_bootstrap_prompt({
        "id": "x", "role_descriptor": {"path": "r.md"},
        "lifecycle": {"compact_threshold": 90}})
    assert "The launcher will type it" not in prompt
    assert "HOST that owns this session's PTY" in prompt


def test_only_ONE_HarnessMonitor_construction_site_outside_the_selftests():
    """[CXC-TERM-CHILD-TELEMETRY] verify-machine.  A second construction site is a second
    place the thresholds, the hysteresis and the echo grace can drift."""
    import subprocess
    out = subprocess.run(
        ["git", "grep", "-n", "HarnessMonitor("], cwd=REPO,
        capture_output=True, text=True).stdout.splitlines()
    def _is_code(line: str) -> bool:
        body = line.split(":", 2)[-1].strip()
        return not body.startswith("#")          # a COMMENT may name it; code may not

    sites = [ln for ln in out
             if not ln.startswith(("compose_projects/intelligence/launcher/monitor.py",     # its own selftest
                                   "compose_projects/intelligence/launcher/test_",          # a test
                                   "corpora/", "docs/",                    # the spec + prose
                                   "python/tests/"))
             and _is_code(ln) and "def " not in ln and "class " not in ln]
    assert len(sites) == 1, sites
    assert sites[0].startswith("python/composeio/ptycore.py"), sites


# ══ TERM-C17 — ⌘V through the ONE existing path ════════════════════════════
class _KeyChild:
    def __init__(self): self.writes = []
    def write_keys(self, data): self.writes.append(data)
    def inject_line(self, line, kind="inject"): pass


def test_CMD_V_delivers_bytes_IDENTICAL_to_the_same_text_through_on_text():
    """The proof that there is ONE paste path, not two: the ⌘V bytes are compared against
    `on_text`'s for the same payload, in both paste modes.  `terminal.py:821` swallowed every
    Cmd-chord before anything read the clipboard, so the clipboard never reached the wrapper
    [CXC-TERM-PASTE] exists to guarantee."""
    payload = "line one\nline two\nline three"

    w1, _ = _widget()
    w1._child = _KeyChild()
    w1.feed(b"\x1b[?2004h")                      # the child enables bracketed paste
    w1._clipboard_reader = lambda: payload
    w1.on_key("v", _KMOD_GUI)

    w2, _ = _widget()
    w2._child = _KeyChild()
    w2.feed(b"\x1b[?2004h")
    w2.on_text(payload)

    assert w1._child.writes == w2._child.writes
    assert w1._child.writes == [PASTE_START + payload.encode() + PASTE_END]

    # paste mode OFF -> verbatim, through the same one path
    w3, _ = _widget()
    w3._child = _KeyChild()
    w3._clipboard_reader = lambda: payload
    w3.on_key("V", _KMOD_GUI)
    assert w3._child.writes == [payload.encode()]


def test_an_EMPTY_or_unavailable_clipboard_writes_ZERO_bytes():
    """A bracketed-paste wrapper around nothing is a submit the human did not ask for."""
    for reader in (lambda: "", lambda: None, lambda: (_ for _ in ()).throw(OSError("no X"))):
        w, _ = _widget()
        w._child = _KeyChild()
        w.feed(b"\x1b[?2004h")
        w._clipboard_reader = reader
        w.on_key("v", _KMOD_GUI)
        assert w._child.writes == []


def test_every_OTHER_Cmd_chord_is_still_swallowed():
    """"Cmd-shortcuts belong to the app, not the child" — unchanged for everything but V."""
    w, _ = _widget()
    w._child = _KeyChild()
    w._clipboard_reader = lambda: "should not appear"
    for key in ("c", "a", "s", "w", "q", "z", "x"):
        w.on_key(key, _KMOD_GUI)
    # ⌘C is scoped OUT explicitly: copy-out needs a selection model neither the widget nor
    # the grid has, and ⌘C with nothing to copy is the selection slice with its visible half
    # removed.
    assert w._child.writes == []
    # Ctrl-⌘-V is not a paste either — a chord we do not own must not become one
    w.on_key("v", _KMOD_GUI | _KMOD_CTRL)
    assert w._child.writes == []


def test_the_clipboard_read_is_ONE_helper_and_the_paste_has_ONE_write_site():
    """Structural half of "do not fork a second paste path"."""
    import inspect
    src = inspect.getsource(TerminalWidget.on_key)
    assert "self.on_text(text)" in src, "⌘V must route through the EXISTING on_text"
    assert "PASTE_START" not in src, "on_key re-implemented the bracketed wrapper"
    assert src.count("clipboard_text()") == 1


# ══ TERM-C18 — the census measures the configuration actually RUN ══════════
def _census():
    sys.path.insert(0, os.path.join(REPO, "compose_projects", "intelligence", "tools"))
    sys.path.insert(0, os.path.join(REPO, "compose_projects", "intelligence", "launcher"))
    import terminal_census as tc
    return tc


@pytest.mark.requires_repo_mcp_config
def test_the_AS_DEPLOYED_profile_carries_the_real_config_and_the_dialogs():
    """[CXC-TERM-CENSUS-CONFIG-FIDELITY].  All five prior profiles ran
    `--strict-mcp-config --mcp-config '{"mcpServers": {}}'` with
    `skipDangerousModePermissionPrompt: true` and no dev-channels flag — a startup with no
    dialogs in it — and a STARTUP claim was then read off it (`?2004h` is already set), which
    the first live spawn refuted.  The as-deployed profile is what makes any startup claim
    admissible, so its argv is asserted EXACTLY.

    UPDATED with [CXC-IC-CHANNEL-INSTANCE]: fidelity is to WHAT THE WIDGET LAUNCHES, and the
    widget now launches `--strict-mcp-config --mcp-config <generated>`.  The old assertion
    ("no `--strict-mcp-config`") was a proxy for "the real server set is present"; it is
    replaced by asserting the SERVER SET ITSELF, which is the property that was ever meant.
    Strict + the derived config == the deployed servers and no others; strict + `{}` (the
    clean room) == no servers at all."""
    tc = _census()
    argv = tc.profile_argv("as-deployed", "sid-1")
    blob = " ".join(argv)
    assert '"mcpServers": {}' not in blob, "an EMPTY server set is the clean room, not this"
    assert "skipDangerousModePermissionPrompt" not in blob
    assert "--dangerously-skip-permissions" not in blob, "the prompts ARE the measurement"
    assert "--dangerously-load-development-channels" in argv
    # the config it names is the REAL one, server for server, with ONE key rebound
    assert "--strict-mcp-config" in argv
    cfg_path = argv[argv.index("--mcp-config") + 1]
    assert os.path.isfile(cfg_path), "the profile names a config that does not exist"
    generated = json.load(open(cfg_path, encoding="utf-8"))
    real = json.load(open(os.path.join(REPO, ".mcp.json"), encoding="utf-8"))
    assert set(generated["mcpServers"]) == set(real["mcpServers"])
    assert generated["mcpServers"]["composeio-channel"]["env"]["INTELLIGENCE_ID"] == \
        tc.PROFILES["as-deployed"]["env"]["INTELLIGENCE_ID"]
    # ...and the clean-room profiles are NOT retired: they remain the comparison that makes
    # the sequence sets discriminable.
    clean = " ".join(tc.profile_argv("ax", "sid-2"))
    assert "--strict-mcp-config" in clean and "skipDangerousModePermissionPrompt" in clean
    assert '"mcpServers": {}' in clean


@pytest.mark.requires_repo_mcp_config
def test_the_as_deployed_profile_measures_the_SAME_mcp_startup_the_widget_spawns():
    """The fidelity rule generalised ([CXC-TERM-CENSUS-CONFIG-FIDELITY] + the injection race
    it produced): the census may not measure a startup nobody runs.  The widget's MCP flags
    and the profile's MCP flags must be the same flags in the same order — differing only in
    the id the config is bound to, which is the whole point of the throwaway."""
    from composeio.ptycore import remove_child_mcp_config
    tc = _census()
    prof = tc.profile_argv("as-deployed", "sid-1")
    w, _ctx = _widget(mount=False)
    argv = w.child_argv()
    try:
        def mcp_shape(a):
            i = a.index("--mcp-config")
            return [a[i - 1], a[i], os.path.basename(a[i + 1]).split("-")[0]]
        assert mcp_shape(prof) == mcp_shape(argv) == ["--strict-mcp-config",
                                                      "--mcp-config", "mcp"]
    finally:
        w._release_mcp_config()
        remove_child_mcp_config(prof[prof.index("--mcp-config") + 1])


def test_the_as_deployed_profile_can_never_announce_itself_as_the_live_architect():
    """It is the ONE profile that joins the bus, and `channel.mjs` defaults to `architect`.
    An unpinned as-deployed child would have taken the Architect's channel — the exact defect
    [CXC-TERM-CHILD-ID] exists to prevent, reintroduced by a measurement tool."""
    tc = _census()
    assert tc.PROFILES["as-deployed"]["env"]["INTELLIGENCE_ID"].startswith("census")
    assert "as-deployed" not in tc.main.__doc__ if tc.main.__doc__ else True
    import argparse
    ap = argparse.ArgumentParser()
    # the default profile set must NOT include it: joining the bus is an attended act
    src = open(os.path.join(REPO, "compose_projects", "intelligence", "tools",
                            "terminal_census.py"), encoding="utf-8").read()
    default_line = [ln for ln in src.splitlines() if '"--profiles", default=' in ln][0]
    assert "as-deployed" not in default_line
    del ap


def test_no_code_comment_cites_a_STARTUP_census_figure_without_naming_its_profile():
    """[CXC-TERM-CENSUS-CONFIG-FIDELITY] verify-machine, the grep guard.  `ptycore.py:222`
    cited `bracketed_paste` "in the STARTUP segment of all five profiles" and concluded a
    child has `?2004h` on by the time anything is typed — true of the measured configuration
    and false of the deployed one."""
    watched = [
        os.path.join(REPO, "python", "composeio", "ptycore.py"),
        os.path.join(REPO, "python", "composeio", "render", "widgets", "terminal.py"),
        os.path.join(REPO, "python", "composeio", "term_sequences.py"),
    ]
    naming = ("clean-room", "as-deployed", "AS-DEPLOYED", "as deployed")
    for path in watched:
        lines = open(path, encoding="utf-8").read().splitlines()
        for i, line in enumerate(lines):
            low = line.lower()
            if "census" not in low or "startup" not in low:
                continue
            window = " ".join(lines[max(0, i - 8):i + 9])
            assert any(tag in window for tag in naming), (
                f"{path}:{i + 1} cites a STARTUP census figure without naming the profile "
                f"it came from: {line.strip()}")
