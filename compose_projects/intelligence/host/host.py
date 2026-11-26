#!/usr/bin/env python3
"""IntelligenceHost — a console-owned, repeatable spawner over the launcher machinery
([SPEC:CXC-INTELLIGENCE-HOST] · node [CXC-IH-HOST]).

WHY (David 2026-07-22, away watch): "the first step of consolidating the harness and the console is
the ability for the console to spawn the composeIO-builder agent so they persist in PARALLEL to me —
copy my harness into the console and use it to run the build agent — in an ENCAPSULATED/REPEATABLE
fashion so MANY intelligences can be instantiated." This COMPLETES the platform's own decomposition:
every other participant is a co-equal carried-identity peer; the observing Intelligence was the last
singleton. The Host gives the console the machinery to instantiate N of them.

WHAT it generalizes: `compose_projects/intelligence/launcher/launcher.py` is a one-shot script (spawn ONE claude under
a PTY, proxy it, relay /compact). The Host lifts the reusable part — `build_claude_args` + the
`INTELLIGENCE_ID` env + the spawn model — into a parameterized API. It REUSES launcher.build_claude_args
BY REFERENCE (imported, not forked) so the child argv tail (dev-channel flag, --resume, skip-perms)
stays single-sourced.

THE CONTRACT ([CXC-IH-HOST]):
    IntelligenceSpec { id, mode: "interactive"|"headless", agent_type, task, resume_session, model }
    IntelligenceHost.spawn(spec) -> IntelligenceHandle { id, pid, mode, started_ts, status() }
    IntelligenceHost.list() -> [handle...]     # the live roster
    IntelligenceHost.stop(id) -> bool          # graceful terminate

ID-CARRYING (where 5a and the Host meet): spawn ALWAYS sets `INTELLIGENCE_ID=<spec.id>` in the child
env (and `COMPOSEIO_SESSION_JSONL` when known). A spawned intelligence's in-process sources
(self_publisher --namespace-by-id) then publish `self/<id>/*` automatically — carried, never guessed.
This holds even when the caller overrides the child argv (the stub-child proof path): the identity is
set by the Host, independent of what binary runs.

MODE is the TRANSPORT detail, not a new seam ([DX-COLLAPSE-TRANSPORT]):
  * headless   -> `claude -p "<task>" --agent <agent_type>` + the launcher's channel/perms tail; an
                  autonomous builder that runs a scoped slice and exits, publishing progress while it
                  lives. THIS is the PoC's "persist in parallel to you" builder. Spawned via
                  subprocess (no PTY needed — a -p run is non-interactive).
  * interactive-> the launcher's `pty.fork` model (a human can attach). NOT built in the PoC; the
                  Host constructs the argv and DEFERS the live PTY spawn to launcher.Launcher.

ENCAPSULATED/REPEATABLE: `spawn(spec)` is called N times with N ids; nothing here is singleton. Each
handle is tracked by id; list()/stop() operate on the live roster.

Run:   python3 host.py --selftest   # no broker, no real claude: argv/env construction + lifecycle
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

# ── Reuse the launcher's argv builder BY REFERENCE (do NOT fork its logic) ────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))            # compose_projects/intelligence/host
_INTEL_DIR = os.path.dirname(_THIS_DIR)                           # intelligence
_LAUNCHER_DIR = os.path.join(_INTEL_DIR, "launcher")
_PUBLISHER_DIR = os.path.join(_INTEL_DIR, "publisher")
if _LAUNCHER_DIR not in sys.path:
    sys.path.insert(0, _LAUNCHER_DIR)
# build_claude_args (argv tail) + project_dir_key/project_dir_for (THE cwd seam, [CXC-FC-DEFORK]).
# `launcher` re-exports the core's names, so this Host and the Terminal Widget resolve a child's
# cwd through the SAME function — the whole point of the de-fork.
from launcher import (build_claude_args, project_dir_for,  # noqa: E402
                      project_dir_key)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── The spec (what to instantiate) ────────────────────────────────────────────────────────────
@dataclass
class IntelligenceSpec:
    """A request to instantiate one carried-identity peer intelligence.

    id            the bus id — becomes INTELLIGENCE_ID in the child env, hence `self/<id>/*`.
    mode          "headless" (autonomous `claude -p` builder) or "interactive" (PTY, deferred).
    agent_type    the Claude Code subagent to run in headless mode (`--agent <agent_type>`), e.g.
                  "composeio-expert". None -> no --agent flag.
    task          the scoped slice for a headless builder (`claude -p "<task>"`). None -> "".
    resume_session a Claude Code session-id to re-host (RESUME/MOVE); None -> cold start.
    model         an optional model override (`--model <model>`); None -> the default.
    role          the AGENT DEFINITION this instance instantiates (`composeux-architect`) — axis 1
                  of [CXC-FC-THREE-AXES]. It keys memory, the bootstrap tier and agent-def
                  registration, and it is what `home_dir` DERIVES the cwd from. `id` is axis 2 and
                  keys topics/telemetry/locks/voice; the two are INDEPENDENT and neither may be
                  derived from the other by naming convention ([CXC-FC-THREE-AXES] prohibition).
    instance_home an EXPLICIT working-directory override. Normally left None: the home is DERIVED
                  as `project_dir_for(role)` = `~/compose-instances/<role>/`, so `architect` and
                  `architect-hosted` — two instances of ONE role — deliberately share one project
                  key and therefore one memory store ([DD-FC-MEMORY-GRAIN], [CXC-FC-PROJECT-DIR]).
                  Set it only when a caller must place a child somewhere else (a test, a probe).
    additional_dirs the Compose* project roots to mount as extra working dirs (`--add-dir <path>`),
                  so ONE role-keyed instance edits every project. None -> none added.
    project_dir   DEPRECATED alias for instance_home (the earlier project-keyed field). If only
                  project_dir is set it is used as the home; prefer `role`.
    """
    id: str
    mode: str = "headless"
    agent_type: Optional[str] = None
    task: Optional[str] = None
    resume_session: Optional[str] = None
    model: Optional[str] = None
    role: Optional[str] = None
    instance_home: Optional[str] = None
    additional_dirs: Optional[list] = None
    project_dir: Optional[str] = None          # deprecated alias for instance_home

    @property
    def home_dir(self) -> Optional[str]:
        """The resolved working dir, most-explicit first: `instance_home`, then the deprecated
        `project_dir` alias, then the DERIVED `project_dir_for(role)`.

        The derived leg is the one that matters: it routes this spawn path through the SAME
        function the Terminal Widget's `PtyChild` spawn uses ([CXC-FC-DEFORK]), so the same
        `(id, role)` resolves to the same cwd from either path. None (no role, no override) means
        "inherit the Host's cwd", which `_enforce_home_isolation` REFUSES for a managed fleet
        instance — inheriting is how a child landed in the repo's project key on 2026-08-12.
        """
        if self.instance_home:
            return self.instance_home
        if self.project_dir:
            return self.project_dir
        if self.role:
            return project_dir_for(self.role)
        return None


# ── The handle (a live intelligence in the roster) ───────────────────────────────────────────
@dataclass
class IntelligenceHandle:
    """A spawned intelligence's roster entry. `status()` reflects the child's live state."""
    id: str
    pid: int
    mode: str
    started_ts: str
    _proc: Optional[subprocess.Popen] = field(default=None, repr=False)

    def status(self) -> str:
        """"running" while the child lives, else "exited:<code>" (negative = killed by signal)."""
        if self._proc is None:
            return "unknown"
        rc = self._proc.poll()
        return "running" if rc is None else f"exited:{rc}"

    def as_dict(self) -> dict:
        return {"id": self.id, "pid": self.pid, "mode": self.mode,
                "started_ts": self.started_ts, "status": self.status()}


# ── The Host ─────────────────────────────────────────────────────────────────────────────────
class IntelligenceHost:
    """Console-owned, repeatable spawner. Instantiate once; call spawn(spec) N times.

    claude_bin        the binary a real spawn execs (default "claude").
    mqtt_url          the bus the child publishes to (threaded into the child env as COMPOSEBUS_MQTT).
    skip_permissions  add `--dangerously-skip-permissions` to the child argv (default True, matching
                      the launcher's own default).
    session_jsonl_for optional resolver `id -> jsonl path`; when it returns a path the child gets
                      COMPOSEIO_SESSION_JSONL set (so its self_publisher pins the right transcript).
    """

    def __init__(self, *, claude_bin: str = "claude",
                 mqtt_url: str = "mqtt://mqtt-broker.local:1883",
                 skip_permissions: bool = True,
                 session_jsonl_for: Optional[Callable[[str], Optional[str]]] = None,
                 enforce_isolated_homes: bool = False,
                 log: Optional[Callable[[str], None]] = None):
        self.claude_bin = claude_bin
        self.mqtt_url = mqtt_url
        self.skip_permissions = skip_permissions
        self.session_jsonl_for = session_jsonl_for
        # [CXC-IH-HOMES] fleet enforcement of the isolation invariant ("one instance, one home;
        # transcripts + memory never co-locate"). Default OFF for back-compat with the homeless
        # stub-child selftest path; the CONSOLE/fleet spawner constructs the Host with this ON so a
        # managed instance can never inherit the shared home pile or share a home with a sibling.
        self.enforce_isolated_homes = enforce_isolated_homes
        self._log = log or (lambda line: sys.stderr.write(line + "\n"))
        self._handles: dict[str, IntelligenceHandle] = {}
        self._home_by_key: dict[str, str] = {}   # project_dir_key(home) -> the id that owns it

    # ── Pure argv+env construction (testable without spawning) ──────────────────────────────
    def build_command(self, spec: IntelligenceSpec) -> tuple[list[str], dict]:
        """Build the (argv, env) for `spec` — the real-claude spawn path. Pure + deterministic so
        it can be unit-tested without launching anything.

        headless:    [claude, "-p", task, ("--agent", agent_type)?, *build_claude_args, ("--model",m)?]
        interactive: [claude, *build_claude_args, ("--model", m)?]   (spawn is deferred to launcher)

        env: a COPY of os.environ + INTELLIGENCE_ID=<id> (the carried identity → self/<id>/*),
             COMPOSEBUS_MQTT=<mqtt_url>, and COMPOSEIO_SESSION_JSONL when a transcript is known.
        The channel/perms/--resume tail comes from launcher.build_claude_args — single-sourced."""
        tail = build_claude_args(spec.resume_session or "", skip_permissions=self.skip_permissions)
        if spec.mode == "headless":
            argv = [self.claude_bin, "-p", spec.task or ""]
            if spec.agent_type:
                argv += ["--agent", spec.agent_type]
            argv += tail
        elif spec.mode == "interactive":
            argv = [self.claude_bin] + tail
        else:
            raise ValueError(f"unknown mode {spec.mode!r} (want 'headless' or 'interactive')")
        if spec.model:
            argv += ["--model", spec.model]
        # [DEC-INSTANCE-KEYED-HOMES] mount each Compose* project as an extra working dir so ONE
        # identity-keyed instance (cwd = its instance_home) can still edit every project.
        for d in (spec.additional_dirs or []):
            argv += ["--add-dir", d]
        return argv, self._build_env(spec)

    def _build_env(self, spec: IntelligenceSpec) -> dict:
        env = dict(os.environ)
        env["INTELLIGENCE_ID"] = spec.id           # 5a: the carried identity → self/<id>/*
        env["COMPOSEBUS_MQTT"] = self.mqtt_url
        jsonl = self.session_jsonl_for(spec.id) if self.session_jsonl_for else None
        if jsonl:
            env["COMPOSEIO_SESSION_JSONL"] = jsonl
        return env

    # ── The isolation invariant, RE-KEYED to ROLE ([CXC-IH-HOMES] -> [CXC-FC-HOMES-REKEY]) ───
    def _enforce_home_isolation(self, spec: IntelligenceSpec) -> None:
        """Enforce "one ROLE, one home; no two distinct ROLES may share a project key."

        RE-KEYED FROM INSTANCE TO ROLE, 2026-08-12 ([CXC-FC-HOMES-REKEY]). The original invariant
        was "one INSTANCE, one home", and it would have REFUSED the ratified fleet layout: under
        [CXC-FC-PROJECT-DIR] `architect` and `architect-hosted` are two instances of ONE role and
        DELIBERATELY share `~/compose-instances/composeux-architect/`, because per-role memory is
        what David chose ([DD-FC-MEMORY-GRAIN]) and cwd keys memory, transcripts and agent-def
        registration TOGETHER.

        It was re-keyed rather than DISABLED on purpose: a disabled guard is indistinguishable from
        an absent one, and the co-location it was written to catch — two DISTINCT roles landing in
        one project key, so a hardware architect's memory lands in the ComposeUX architect's pile —
        is still a real defect. What changed is which axis of [CXC-FC-THREE-AXES] the ownership map
        is keyed on, not whether co-location is caught.

        `_home_by_key` therefore maps project key -> ROLE. Re-spawning the same role (any number of
        instances of it) into its home is fine — that IS the ratified sharing. No-op unless
        `enforce_isolated_homes` (fleet mode)."""
        if not self.enforce_isolated_homes:
            return
        home = spec.home_dir
        if not home:
            raise ValueError(
                f"[CXC-FC-HOMES-REKEY] instance {spec.id!r} has no role and no instance_home — a "
                f"managed instance would inherit the Host's cwd and co-locate its "
                f"transcript+memory in the shared home pile (on 2026-08-12 that pile was THE "
                f"REPO). Set spec.role so the home derives as ~/compose-instances/<role>/.")
        key = project_dir_key(home)
        # The role is the OWNER. A spec with an explicit instance_home and no role owns its key
        # under its id — an unnamed role cannot be reasoned about, and refusing to record any
        # owner at all would let the guard pass by having nothing to compare against.
        claimant = str(spec.role or "").strip() or f"<id:{spec.id}>"
        owner = self._home_by_key.get(key)
        if owner is not None and owner != claimant:
            raise ValueError(
                f"[CXC-FC-HOMES-REKEY] home co-location: role {claimant!r} (instance {spec.id!r}) "
                f"resolves to project key {key!r}, already owned by role {owner!r}. Two distinct "
                f"ROLES may not share a home — their transcripts + memory would co-locate. Give "
                f"{claimant!r} its own home (~/compose-instances/{claimant}/). Two INSTANCES of "
                f"one role sharing a home is correct and is not what this guard catches.")
        self._home_by_key[key] = claimant

    # ── Spawn (the repeatable instantiation) ────────────────────────────────────────────────
    def spawn(self, spec: IntelligenceSpec, *,
              argv: Optional[list[str]] = None,
              env_extra: Optional[dict] = None) -> IntelligenceHandle:
        """Instantiate the intelligence described by `spec` and track it in the roster.

        By default the child argv comes from build_command(spec) (the real-claude path). A caller
        MAY override `argv` to run a stand-in child (the PoC's stub, or a self_publisher builder) —
        but the CARRIED IDENTITY is set by the Host regardless: INTELLIGENCE_ID=<spec.id> is always
        in the child env. This is what lets the PoC prove the id-carry + lifecycle on the wire
        without launching a heavyweight real `claude`.

        Raises if `spec.id` is already live (ids are unique in the roster). interactive mode has no
        live spawn in the PoC — its argv is constructed and it raises NotImplementedError on spawn,
        pointing at launcher.Launcher for the PTY path."""
        existing = self._handles.get(spec.id)
        if existing is not None and existing.status() == "running":
            raise ValueError(f"intelligence {spec.id!r} is already live (pid={existing.pid})")

        if argv is None:
            child_argv, env = self.build_command(spec)
            if spec.mode == "interactive":
                # The Host constructs interactive argv but does not own the PTY spawn — that is
                # launcher.Launcher's job ([DX-COLLAPSE-TRANSPORT]). Not needed for the PoC.
                raise NotImplementedError(
                    "interactive spawn is deferred to launcher.Launcher (PTY); the Host built the "
                    f"argv {child_argv!r} — pass it to a Launcher, or use mode='headless'.")
        else:
            child_argv = list(argv)
            env = self._build_env(spec)  # identity is carried even for an overridden argv
        if env_extra:
            env.update(env_extra)

        self._enforce_home_isolation(spec)

        # [DEC-INSTANCE-KEYED-HOMES] run the child from its INSTANCE HOME (keyed by role/identity,
        # not project) so the harness keys its memory store + statusline + agents/skills to the
        # INSTANCE — stable across every Compose* project it edits (mounted via --add-dir). None ->
        # inherit the Host's cwd. Supersedes the project-keyed [DEC-MEMORY-SCOPING-BY-CWD] bake-in.
        proc = subprocess.Popen(child_argv, env=env, cwd=spec.home_dir)
        handle = IntelligenceHandle(id=spec.id, pid=proc.pid, mode=spec.mode,
                                    started_ts=_iso_now(), _proc=proc)
        self._handles[spec.id] = handle
        self._log(f"[host] {handle.started_ts} SPAWN id={spec.id} mode={spec.mode} "
                  f"pid={proc.pid} INTELLIGENCE_ID={env['INTELLIGENCE_ID']} argv={child_argv!r}")
        return handle

    # ── The roster ──────────────────────────────────────────────────────────────────────────
    def list(self) -> list[IntelligenceHandle]:
        """The live roster — every tracked handle (each carries its own status())."""
        return list(self._handles.values())

    def get(self, id: str) -> Optional[IntelligenceHandle]:
        return self._handles.get(id)

    # ── Graceful stop ───────────────────────────────────────────────────────────────────────
    def stop(self, id: str, *, timeout: float = 5.0) -> bool:
        """Gracefully terminate the intelligence `id` (SIGTERM, then SIGKILL after `timeout`).
        Returns True if a tracked child was signaled, False if `id` is unknown. Idempotent — a
        stop on an already-exited child returns True (it is not running; the roster reflects that)."""
        handle = self._handles.get(id)
        if handle is None or handle._proc is None:
            self._log(f"[host] {_iso_now()} STOP id={id} — not tracked")
            return False
        proc = handle._proc
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._log(f"[host] {_iso_now()} STOP id={id} — SIGTERM ignored, SIGKILL")
                proc.kill()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    pass
        self._log(f"[host] {_iso_now()} STOP id={id} pid={handle.pid} status={handle.status()}")
        return True

    def stop_all(self, *, timeout: float = 5.0) -> None:
        for id in list(self._handles):
            self.stop(id, timeout=timeout)


# ── Selftest (no broker, no real claude) ─────────────────────────────────────────────────────
def _selftest() -> int:
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    stub = os.path.join(_THIS_DIR, "_stub_child.py")

    # ── (a) build_command: the REAL headless claude argv+env (constructed + unit-tested, not
    #        live-launched). Proves the -p/--agent/task shape, the reused launcher tail, and the
    #        carried INTELLIGENCE_ID env. ─────────────────────────────────────────────────────
    host = IntelligenceHost(claude_bin="claude", mqtt_url="mqtt://mqtt-broker.local:1883")
    spec = IntelligenceSpec(id="builder-1", mode="headless",
                            agent_type="composeio-expert", task="echo a status line and exit")
    argv, env = host.build_command(spec)
    check(argv[0] == "claude", f"(a) claude_bin not first: {argv!r}")
    check(argv[1] == "-p" and argv[2] == "echo a status line and exit",
          f"(a) headless -p <task> wrong: {argv!r}")
    check(argv[3:5] == ["--agent", "composeio-expert"], f"(a) --agent tail wrong: {argv!r}")
    check("--dangerously-load-development-channels" in argv,
          f"(a) reused launcher dev-channel flag missing: {argv!r}")
    check("--dangerously-skip-permissions" in argv,
          f"(a) skip-permissions default not applied: {argv!r}")
    check("--resume" not in argv, f"(a) cold headless must not --resume: {argv!r}")
    check(env["INTELLIGENCE_ID"] == "builder-1",
          f"(a) child env missing carried INTELLIGENCE_ID=builder-1: {env.get('INTELLIGENCE_ID')!r}")
    check(env["COMPOSEBUS_MQTT"] == "mqtt://mqtt-broker.local:1883",
          f"(a) child env missing COMPOSEBUS_MQTT: {env.get('COMPOSEBUS_MQTT')!r}")

    # (a2) a resume/model spec threads --resume + --model + COMPOSEIO_SESSION_JSONL.
    host2 = IntelligenceHost(session_jsonl_for=lambda i: f"/tmp/{i}.jsonl")
    argv2, env2 = host2.build_command(
        IntelligenceSpec(id="mover-9", mode="headless", task="t",
                         resume_session="sess-abc", model="claude-opus"))
    check(argv2[argv2.index("--resume") + 1] == "sess-abc", f"(a2) --resume not threaded: {argv2!r}")
    check(argv2[-2:] == ["--model", "claude-opus"], f"(a2) --model tail wrong: {argv2!r}")
    check(env2.get("COMPOSEIO_SESSION_JSONL") == "/tmp/mover-9.jsonl",
          f"(a2) session jsonl not carried: {env2.get('COMPOSEIO_SESSION_JSONL')!r}")

    # (a2b) [CXC-FC-PROJECT-DIR] the home is DERIVED from the ROLE; instance_home is an explicit
    # override; project_dir remains the back-compat alias; additional_dirs each become a --add-dir
    # so one role-keyed instance edits every project.
    s_home = IntelligenceSpec(id="widget-builder", mode="headless", task="w",
                              instance_home="/h/compose-instances/widget-builder",
                              additional_dirs=["/r/composeio", "/r/ComposePlatform"])
    check(s_home.home_dir == "/h/compose-instances/widget-builder",
          f"(a2b) instance_home must win as home_dir: {s_home.home_dir!r}")
    check(IntelligenceSpec(id="lg", project_dir="/old").home_dir == "/old",
          "(a2b) project_dir must remain the back-compat home_dir")
    check(IntelligenceSpec(id="architect-hosted", role="composeux-architect").home_dir
          == project_dir_for("composeux-architect"),
          "(a2b) a role-only spec must DERIVE its home from project_dir_for(role) "
          "([CXC-FC-DEFORK] — the same function the Terminal Widget's spawn calls)")
    check(IntelligenceSpec(id="architect").home_dir is None,
          "(a2b) no role and no override must yield NO home (the fleet guard refuses it) "
          "rather than a guessed one")
    argv_h, _ = host.build_command(s_home)
    adds = [argv_h[i + 1] for i, x in enumerate(argv_h) if x == "--add-dir"]
    check(adds == ["/r/composeio", "/r/ComposePlatform"],
          f"(a2b) additional_dirs must become --add-dir flags: {argv_h!r}")

    # (a2c) [CXC-FC-HOMES-REKEY] the isolation guard is RE-KEYED to ROLE, not disabled.
    #   * two INSTANCES of one role sharing a home is the RATIFIED layout -> must NOT raise;
    #   * two distinct ROLES on one project key is still a defect     -> must still raise;
    #   * a managed instance with no role and no home                 -> must still raise.
    fleet = IntelligenceHost(enforce_isolated_homes=True)
    fleet._enforce_home_isolation(IntelligenceSpec(id="architect", role="composeux-architect"))
    try:
        fleet._enforce_home_isolation(
            IntelligenceSpec(id="architect-hosted", role="composeux-architect"))
    except ValueError as exc:
        check(False, f"(a2c) SAME role, two instances must share a home freely: {exc}")
    try:
        fleet._enforce_home_isolation(
            IntelligenceSpec(id="hw-1", role="composeio-hardware-architect",
                             instance_home=project_dir_for("composeux-architect")))
        check(False, "(a2c) two DISTINCT roles on one project key must still raise")
    except ValueError as exc:
        check("[CXC-FC-HOMES-REKEY]" in str(exc) and "compose-instances" in str(exc),
              f"(a2c) the co-location message must name the node and the role-keyed "
              f"layout: {exc}")
    try:
        fleet._enforce_home_isolation(IntelligenceSpec(id="homeless"))
        check(False, "(a2c) a managed instance with no role and no home must raise")
    except ValueError as exc:
        check("~/compose-instances/<role>/" in str(exc),
              f"(a2c) the no-home message must point at the ROLE-keyed layout, not the "
              f"retired per-instance one: {exc}")

    # (a3) interactive build_command builds argv but spawn defers to the launcher (NotImplemented).
    iargv, _ = host.build_command(IntelligenceSpec(id="arch", mode="interactive"))
    check(iargv[0] == "claude" and "--resume" not in iargv,
          f"(a3) interactive argv wrong: {iargv!r}")
    try:
        host.spawn(IntelligenceSpec(id="arch-x", mode="interactive"))
        check(False, "(a3) interactive spawn should raise NotImplementedError")
    except NotImplementedError:
        pass

    # ── (b) spawn carries INTELLIGENCE_ID into a real child (env-carry proof), (c) list shows it,
    #        stop ends it. Uses the STUB child (writes its INTELLIGENCE_ID to a file then idles) —
    #        the real-claude path above is constructed+tested; the live spawn here is the trivial
    #        stub so the lifecycle is proven with no broker and no heavyweight `claude`. ─────────
    import tempfile
    td = tempfile.mkdtemp()
    touch = os.path.join(td, "carried_id.txt")
    handle = host.spawn(spec, argv=[sys.executable, stub, "--touch", touch])
    check(handle.id == "builder-1" and handle.pid > 0, f"(b) bad handle: {handle!r}")

    # the child writes its carried INTELLIGENCE_ID to `touch` — poll for it (env-carry proof).
    carried = None
    for _ in range(100):
        if os.path.exists(touch):
            with open(touch) as fh:
                carried = fh.read().strip()
            if carried:
                break
        time.sleep(0.02)
    check(carried == "builder-1",
          f"(b) child did not carry INTELLIGENCE_ID=builder-1 (got {carried!r})")

    # (c) list shows the live roster; the handle reports running.
    roster = host.list()
    check(len(roster) == 1 and roster[0].id == "builder-1",
          f"(c) list did not show the spawned builder: {[h.as_dict() for h in roster]}")
    check(host.get("builder-1").status() == "running",
          f"(c) spawned builder not running: {host.get('builder-1').status()}")

    # (c2) spawning the SAME id while live is rejected (ids are unique).
    try:
        host.spawn(spec, argv=[sys.executable, stub])
        check(False, "(c2) duplicate live id should raise")
    except ValueError:
        pass

    # (d) stop ends it gracefully; the roster reflects the exit.
    ok = host.stop("builder-1")
    check(ok is True, "(d) stop(builder-1) did not report success")
    check(host.get("builder-1").status().startswith("exited"),
          f"(d) child still running after stop: {host.get('builder-1').status()}")
    check(host.stop("no-such-id") is False, "(d) stop of unknown id must be False")

    # (e) REPEATABLE / ENCAPSULATED: spawn N ids, each carries its own identity, list shows all N,
    #     stop_all ends them. Nothing singleton.
    host3 = IntelligenceHost()
    ids = ["builder-a", "builder-b", "builder-c"]
    files = {}
    for i in ids:
        f = os.path.join(td, f"id-{i}.txt")
        files[i] = f
        host3.spawn(IntelligenceSpec(id=i, mode="headless", task="noop"),
                    argv=[sys.executable, stub, "--touch", f])
    # each child carried ITS OWN id
    carried_ids = set()
    for _ in range(150):
        carried_ids = {open(f).read().strip() for f in files.values() if os.path.exists(f)
                       and open(f).read().strip()}
        if carried_ids == set(ids):
            break
        time.sleep(0.02)
    check(carried_ids == set(ids),
          f"(e) N children did not each carry their own id: {carried_ids} vs {set(ids)}")
    check(len(host3.list()) == 3, f"(e) roster should hold 3: {[h.id for h in host3.list()]}")
    host3.stop_all()
    check(all(h.status().startswith("exited") for h in host3.list()),
          f"(e) stop_all left a child running: {[h.as_dict() for h in host3.list()]}")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: IntelligenceHost selftest (5/5 groups)\n")
    for line in (
        "  (a) build_command builds the REAL headless `claude -p <task> --agent <t>` argv + reused "
        "launcher tail + carried INTELLIGENCE_ID env (constructed + unit-tested, not live-launched)",
        "  (a2) resume/model/session-jsonl are threaded into argv+env",
        "  (a3) interactive build_command builds argv; spawn defers the PTY to launcher.Launcher",
        "  (b) spawn carries INTELLIGENCE_ID into the child (child wrote its own id back)",
        "  (c) list shows the live roster; duplicate live id rejected",
        "  (d) stop gracefully ends the child; roster reflects the exit; unknown-id stop is False",
        "  (e) REPEATABLE: N ids spawned, each carries its own identity, stop_all ends all N",
    ):
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.stderr.write("IntelligenceHost is a library; run --selftest, or drive it via "
                     "compose_projects/intelligence/host/poc_driver.py\n")
    sys.exit(2)
