#!/usr/bin/env python3
"""[CXC-FC-HOMES-REKEY] (was [CXC-IH-HOMES]) Enforcing test for the isolation invariant, RE-KEYED
2026-08-12 from INSTANCE to ROLE: ONE ROLE, ONE HOME — no two distinct ROLES may share a project
key.

THE ROOT (not the band-aid). [CXC-IC-SELF-IDENTITY] (shipped 2026-07-24) stops the *symptom* at
read time: a console pins its own transcript by a PASSED id and refuses to `ls -t`-guess. But the
ROOT is that multiple intelligences launched from a shared cwd all write their transcript + memory
into ONE project dir — on 2026-08-12 that dir was THE REPO's, because the Terminal Widget's spawn
path defaulted `cwd=None`. The harness keys a session's project dir (transcript `.jsonl` + memory
store + statusline + agent/skill scope) off the ABSOLUTE cwd ([INSIGHT-CWD-IS-THE-SCOPE-LEVER]).

WHY THE RE-KEY, AND WHY NOT A BYPASS. The guard originally read "one INSTANCE, one home", and its
error message told the operator to use `~/compose-instances/<id>/`. David ruled memory PER ROLE
([DD-FC-MEMORY-GRAIN]) and the project dir keyed by role ([DD-FC-PROJECT-DIR-LAYOUT]), so
`architect` and `architect-hosted` — two INSTANCES of `composeux-architect` — now share one home
DELIBERATELY. The shipped guard would have refused the ratified configuration. It was re-keyed
rather than disabled ([CXC-FC-HOMES-REKEY] prohibition): a disabled guard is indistinguishable from
an absent one, and two DISTINCT roles sharing a key is still a real defect.

This test enforces the re-keyed invariant at the two load-bearing layers:

  1. `project_dir_key(cwd)` faithfully MODELS the harness sanitization — anchored against REAL
     on-disk project dirs so a drift in the harness rule is caught, not assumed.
  2. `IntelligenceHost(enforce_isolated_homes=True)` (fleet mode) REFUSES a managed instance with
     NO home and two DISTINCT ROLES on one project key, and ALLOWS the same role's host and hosted
     child to share one home — BOTH directions, because a test asserting only distinctness would
     encode per-INSTANCE memory and contradict the ratified decision ([CXC-FC-ACCEPT-MEMORY]
     FC-C6 warning).

Run:  python3 compose_projects/intelligence/host/test_instance_keyed_homes.py --selftest
      pytest compose_projects/intelligence/host/test_instance_keyed_homes.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))            # compose_projects/intelligence/host
_INTEL = os.path.dirname(_HERE)                               # intelligence
_LAUNCHER = os.path.join(_INTEL, "launcher")
for _d in (_HERE, _LAUNCHER):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _load(name, path):
    """Load a module by FILE PATH under a synthetic name — sidesteps the `compose_projects/intelligence/host/`
    package `__init__` (which self-imports `host`, colliding with pytest's package import).
    Registered in sys.modules BEFORE exec so the loaded dataclasses' `__module__` resolves."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_host = _load("_ikh_host", os.path.join(_HERE, "host.py"))
_launcher = _load("_ikh_launcher", os.path.join(_LAUNCHER, "launcher.py"))
IntelligenceHost = _host.IntelligenceHost
IntelligenceSpec = _host.IntelligenceSpec
project_dir_key = _launcher.project_dir_key


# ── 1. project_dir_key models the harness — anchored on GROUND TRUTH ──────────────────────────
# These are REAL directories the live harness created under ~/.claude/projects/. If Claude Code
# ever changes its cwd->key sanitization, this anchor fails and the whole invariant is re-checked
# rather than silently drifting ([INSIGHT-TRUST-IS-A-CONSTRUCTED-PROPERTY]).
_GROUND_TRUTH = {
    "/Users/you": "-Users-you",
    "/Users/you/composeio": "-Users-you-composeio",
    # Haven's per-session homes (the fragmentation shape) — a timestamp in the path yields a NEW
    # key every session; instance-keyed homes deliberately DROP the timestamp for a stable key.
    "/Users/you/haven/sessions/haven-architect-20260703-171646":
        "-Users-you-haven-sessions-haven-architect-20260703-171646",
}


@pytest.mark.parametrize("cwd,expected", list(_GROUND_TRUTH.items()))
def test_project_dir_key_matches_real_harness_dirs(cwd, expected):
    assert project_dir_key(cwd) == expected


def test_project_dir_key_is_home_expanding_and_absolute():
    # ~ expands, relative resolves to absolute — so the key is stable regardless of how the home
    # is expressed. `~/compose-instances/architect` and the absolute form yield the SAME key.
    tilde = project_dir_key("~/compose-instances/architect")
    absolute = project_dir_key(os.path.expanduser("~/compose-instances/architect"))
    assert tilde == absolute
    assert tilde.startswith("-")          # leading slash sanitized to a leading dash
    assert "compose-instances-architect" in tilde


def test_distinct_homes_never_co_locate():
    # The core invariant, stated in the terms that matter: distinct instance homes -> distinct
    # project keys -> transcripts + memory land in DIFFERENT ~/.claude/projects/ dirs.
    architect = project_dir_key("~/compose-instances/architect")
    widget = project_dir_key("~/compose-instances/widget-builder")
    shared_home = project_dir_key("~")
    assert architect != widget
    assert architect != shared_home and widget != shared_home
    # ...and the SHARED-HOME failure mode: two instances booted from ~ collapse to ONE key (the
    # co-location the invariant forbids). This is the bug, expressed as a key collision.
    assert project_dir_key("~") == project_dir_key(os.path.expanduser("~"))


# ── 2. IntelligenceHost enforces the invariant in fleet mode ──────────────────────────────────
def _fleet_host():
    return IntelligenceHost(enforce_isolated_homes=True, log=lambda _l: None)


def test_fleet_refuses_a_managed_instance_with_no_home():
    # A managed instance with no ROLE and no home would inherit the Host's cwd and co-locate in
    # the shared pile — which on 2026-08-12 was the repo's project key.
    host = _fleet_host()
    with pytest.raises(ValueError, match=r"no role and no instance_home"):
        host.spawn(IntelligenceSpec(id="architect", mode="headless", task="t"),
                   argv=[sys.executable, "-c", "pass"])


def test_the_no_home_message_points_at_the_ROLE_keyed_layout():
    # [CXC-FC-HOMES-REKEY] requires the guidance text to move too: a message still naming
    # `~/compose-instances/<id>/` would send an operator straight into the retired layout.
    host = _fleet_host()
    with pytest.raises(ValueError) as exc:
        host.spawn(IntelligenceSpec(id="architect", mode="headless", task="t"),
                   argv=[sys.executable, "-c", "pass"])
    assert "~/compose-instances/<role>/" in str(exc.value)
    assert "<id>" not in str(exc.value)


def test_fleet_refuses_two_distinct_ROLES_sharing_a_home():
    # The co-location the guard exists to catch, restated on the ROLE axis: two different agent
    # definitions must not land in one project key, or a hardware architect's memory joins the
    # ComposeUX architect's pile.
    host = _fleet_host()
    home = tempfile.mkdtemp()
    host.spawn(IntelligenceSpec(id="architect", mode="headless", task="t",
                                role="composeux-architect", instance_home=home),
               argv=[sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match=r"co-location"):
        host.spawn(IntelligenceSpec(id="hw-1", mode="headless", task="t",
                                    role="composeio-hardware-architect", instance_home=home),
                   argv=[sys.executable, "-c", "pass"])
    host.stop_all()


def test_fleet_ALLOWS_one_roles_host_and_hosted_child_to_share_a_home():
    # THE DIRECTION THE RE-KEY EXISTS FOR ([DD-FC-MEMORY-GRAIN]). `architect` and
    # `architect-hosted` are two INSTANCES of one role; they share a store BY DESIGN, and a guard
    # that refused this would refuse the ratified configuration.
    host = _fleet_host()
    home = tempfile.mkdtemp()
    for iid in ("architect", "architect-hosted"):
        host.spawn(IntelligenceSpec(id=iid, mode="headless", task="t",
                                    role="composeux-architect", instance_home=home),
                   argv=[sys.executable, "-c", "pass"])
    host.stop_all()


def test_fleet_still_refuses_two_distinct_ids_sharing_a_home_when_NEITHER_declares_a_role():
    # Back-compat leg: with no role to key on, the guard falls back to the instance id, so the
    # pre-re-key behaviour is preserved for callers that never adopted `role`.
    host = _fleet_host()
    home = tempfile.mkdtemp()
    host.spawn(IntelligenceSpec(id="architect", mode="headless", task="t", instance_home=home),
               argv=[sys.executable, "-c", "pass"])
    with pytest.raises(ValueError, match=r"co-location"):
        host.spawn(IntelligenceSpec(id="widget-builder", mode="headless", task="t",
                                    instance_home=home),
                   argv=[sys.executable, "-c", "pass"])
    host.stop_all()


def test_fleet_allows_distinct_homes_and_same_id_respawn():
    host = _fleet_host()
    ha, hw = tempfile.mkdtemp(), tempfile.mkdtemp()
    host.spawn(IntelligenceSpec(id="architect", instance_home=ha, task="t"),
               argv=[sys.executable, "-c", "pass"])
    host.spawn(IntelligenceSpec(id="widget-builder", instance_home=hw, task="t"),
               argv=[sys.executable, "-c", "pass"])
    host.stop_all()
    # same id back into ITS OWN home is NOT co-location (it IS that instance) — must be allowed.
    host.spawn(IntelligenceSpec(id="architect", instance_home=ha, task="t"),
               argv=[sys.executable, "-c", "pass"])
    host.stop_all()


def test_spawn_runs_the_child_in_its_instance_home(tmp_path):
    # Integration proof that the home is not just checked but HONORED: the child's real cwd is the
    # instance home, so the harness would key its transcript+memory there.
    home = tmp_path / "compose-instances" / "architect"
    home.mkdir(parents=True)
    out = tmp_path / "cwd.txt"
    host = _fleet_host()
    host.spawn(
        IntelligenceSpec(id="architect", instance_home=str(home), task="t"),
        argv=[sys.executable, "-c",
              "import os; open(os.environ['CWDOUT'],'w').write(os.getcwd())"],
        env_extra={"CWDOUT": str(out)},
    )
    # child writes then exits on its own; poll for the file
    import time
    for _ in range(100):
        if out.exists() and out.read_text():
            break
        time.sleep(0.02)
    host.stop_all()
    assert os.path.realpath(out.read_text()) == os.path.realpath(str(home))


def test_non_fleet_host_stays_backward_compatible():
    # Default Host (enforce_isolated_homes=False) must NOT enforce — the homeless stub-child PoC
    # path and existing selftests depend on this.
    host = IntelligenceHost(log=lambda _l: None)
    host.spawn(IntelligenceSpec(id="poc", mode="headless", task="t"),
               argv=[sys.executable, "-c", "pass"])   # no home, no raise
    host.stop_all()


# ── --selftest entrypoint (no pytest required) ────────────────────────────────────────────────
def _selftest() -> int:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = []
    for fn in fns:
        try:
            params = getattr(fn, "pytestmark", None)
            if fn is test_project_dir_key_matches_real_harness_dirs:
                for cwd, expected in _GROUND_TRUTH.items():
                    fn(cwd, expected)
            elif "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as td:
                    import pathlib
                    fn(pathlib.Path(td))
            else:
                fn()
            sys.stdout.write(f"  PASS {fn.__name__}\n")
        except Exception as e:  # noqa: BLE001
            failures.append(f"{fn.__name__}: {e}")
            sys.stderr.write(f"  FAIL {fn.__name__}: {e}\n")
    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: [CXC-IH-HOMES] instance-keyed-homes enforcing test\n")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.stderr.write("run with --selftest, or: pytest compose_projects/intelligence/host/test_instance_keyed_homes.py\n")
    sys.exit(2)
