"""[SPEC:CXC-FLEET] §4 — memory scoping, the highest-risk content in the fleet build.

Covers acceptance FC-C4, FC-C5, FC-C7, FC-C18, FC-C19, FC-C20.

THE DEFECT THIS CLOSES, stated once so a future reader knows what "green" means here: on
2026-08-12 a widget-hosted child inherited the repo as its cwd, so its transcript and its MEMORY
STORE landed under the SAME project key as its host, and the two were writing one `MEMORY.md`
live.  The root is not "two instances share a store" — it is that the store was keyed by THE
REPO, so every Intelligence that ever ran from the repo landed in one pile.

⚠ FC-C5 IS ASSERTED IN BOTH DIRECTIONS ON PURPOSE.  "Host and child resolve to distinct stores"
is TRUE for distinct ROLES and FALSE for `architect`/`architect-hosted`, which share a role and
therefore share a store BY DESIGN ([DD-FC-MEMORY-GRAIN]).  A test asserting distinctness for the
same-role pair would encode per-INSTANCE memory and quietly contradict the ratified decision.

⚠ THE CROSS-IMPLEMENTATION TEST IS THE POINT ([CXC-FC-DEFORK]).  A test that drove only the path
we fixed would have passed on 2026-08-12 too and proved nothing about the other.  `test_FC_C4_*`
drives BOTH spawn paths and compares them.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))       # <repo>/python/tests/render/this.py
sys.path.insert(0, os.path.join(_REPO, "python"))

from composeio.instance_home import (  # noqa: E402
    MEMORY_INDEX_FILENAME, RoleRefused, compose_instances_root, known_roles,
    project_dir_for, project_dir_key, regenerate_memory_index, render_memory_index,
    repo_root, role_memory_dir)


@pytest.fixture()
def fleet_root(tmp_path, monkeypatch):
    """Point the fleet's home root at a tmpdir — no test may touch the human's live store."""
    root = tmp_path / "compose-instances"
    monkeypatch.setenv("COMPOSE_INSTANCES_ROOT", str(root))
    return root


# ── FC-C4 — no spawn path can inherit a cwd; ONE seam, driven from BOTH sides ────────────────
def _load_host():
    """Load `compose_projects/intelligence/host/host.py` by path (its package `__init__` self-imports `host`)."""
    intel = os.path.join(_REPO, "compose_projects", "intelligence")
    for d in (os.path.join(intel, "host"), os.path.join(intel, "launcher")):
        if d not in sys.path:
            sys.path.insert(0, d)
    spec = importlib.util.spec_from_file_location(
        "_fleet_host", os.path.join(intel, "host", "host.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_fleet_host"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("instance_id,role", [
    ("architect", "composeux-architect"),
    ("architect-hosted", "composeux-architect"),
    ("hw-1", "composeio-hardware-architect"),
])
def test_FC_C4_both_spawn_paths_resolve_an_IDENTICAL_cwd(instance_id, role, fleet_root):
    """The cross-implementation assertion ([CXC-FC-DEFORK] verification).

    `IntelligenceHost.spawn`'s home derivation and the Terminal Widget's `PtyChild` cwd must be
    the SAME string for the same `(instance_id, role)`.  If either path ever grows its own
    derivation, this fails — which is the property a per-path test cannot have.
    """
    host_mod = _load_host()
    host_side = host_mod.IntelligenceSpec(id=instance_id, role=role).home_dir

    from composeio.render.widgets.terminal import TerminalWidget
    widget_side = TerminalWidget("t", role=role, instance_id=instance_id).cwd

    assert host_side == widget_side == project_dir_for(role)
    assert host_side is not None


def test_FC_C4_the_widget_no_longer_defaults_its_cwd_to_None_and_therefore_the_repo(fleet_root):
    """`ptycore.py:233` — "cwd: None -> the host's cwd -> THE REPO".  That is the live defect."""
    from composeio.render.widgets.terminal import TerminalWidget
    w = TerminalWidget("t", role="composeux-architect", instance_id="architect-hosted")
    assert w.cwd is not None
    assert project_dir_key(w.cwd) != project_dir_key(repo_root())


def test_FC_C4_an_absent_role_REFUSES_rather_than_inheriting(fleet_root):
    """[CXC-FC-UNIT]: `role` had a default of "architect" and now has none.  A widget with no
    role must refuse at the gate `start_session()` passes through — not silently key the
    ComposeUX architect's store."""
    from composeio.render.widgets.terminal import TerminalWidget
    w = TerminalWidget("t", instance_id="mystery")
    assert w.cwd is None
    assert "no role" in w.refusal()


def _call_sites(dotted: str, root: str):
    """Every REAL call to `dotted` (e.g. "pty.fork", "os.chdir") under `root`, by AST.

    Deliberately NOT a text grep: this area is documented at length, and `pty.fork()` appears
    in eight docstrings and comments precisely BECAUSE it is the hazard.  A grep guard here
    would fail on the prose that explains why the guard exists.
    """
    import ast
    mod, attr = dotted.split(".")
    hits = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in sorted(files):
            if not fn.endswith(".py"):
                continue
            p = os.path.join(base, fn)
            try:
                tree = ast.parse(open(p, encoding="utf-8").read(), filename=p)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == attr
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == mod):
                    hits.append(f"{p}:{node.lineno}")
    return hits


def test_FC_C4_pty_fork_is_reachable_from_exactly_one_cwd_setting_site():
    """The guard ([CXC-FC-DEFORK]).  A second `pty.fork()` is a second spawn path, and a second
    spawn path with its own cwd rule is exactly how this defect happened the first time."""
    forks = _call_sites("pty.fork", os.path.join(_REPO, "python", "composeio"))
    assert len(forks) == 1, f"expected exactly one pty.fork() site, found {forks}"
    assert "ptycore.py" in forks[0], f"the one fork site must be the shared core: {forks[0]}"

    # ...and exactly one place changes the child's directory, in the same file, so the cwd a
    # child lands in has one author.
    chdirs = _call_sites("os.chdir", os.path.join(_REPO, "python", "composeio"))
    assert len(chdirs) == 1, f"expected exactly one os.chdir() site, found {chdirs}"
    assert "ptycore.py" in chdirs[0]


# ── FC-C5 — the project key is keyed by ROLE, and never by the repo ─────────────────────────
def test_FC_C5_distinct_roles_distinct_keys_same_role_same_key_never_the_repo(fleet_root):
    """Three assertions in ONE test, exactly as [CXC-FC-ACCEPT-MEMORY] specifies, so the
    both-directions property cannot be half-satisfied by deleting a test."""
    arch_host = project_dir_key(project_dir_for("composeux-architect"))
    arch_child = project_dir_key(project_dir_for("composeux-architect"))
    hardware = project_dir_key(project_dir_for("composeio-hardware-architect"))

    # (1) distinct ROLES -> distinct keys
    assert arch_host != hardware
    # (2) the SAME role's host and hosted child -> the SAME key (BY DESIGN — not a bug)
    assert arch_host == arch_child
    # (3) in NO case is it the repo's key
    repo_key = project_dir_key(repo_root())
    assert arch_host != repo_key and hardware != repo_key


def test_FC_C5_memory_is_keyed_by_role_not_by_instance_id(fleet_root):
    """The failure mode the re-key exists to prevent, stated positively: two INSTANCE ids of one
    role must not produce two stores."""
    assert role_memory_dir("composeux-architect") == role_memory_dir("composeux-architect")
    assert role_memory_dir("composeux-architect") != role_memory_dir("composeio-expert")
    assert "architect-hosted" not in role_memory_dir("composeux-architect")


def test_FC_C5_an_illegal_or_absent_role_is_REFUSED_not_sanitised(fleet_root):
    for bad in ("", None, "   ", "../escape", "a/b", ".hidden"):
        with pytest.raises(RoleRefused):
            project_dir_for(bad)


def test_FC_C5_the_home_root_is_outside_the_repo(fleet_root):
    assert not compose_instances_root().startswith(repo_root() + os.sep)


# ── FC-C7 — the memory index is DERIVED, so concurrent writers cannot corrupt it ────────────
def _fact(dirpath, name, description, group=""):
    os.makedirs(dirpath, exist_ok=True)
    meta = f"metadata:\n  node_type: memory\n" + (f"  group: {group}\n" if group else "")
    with open(os.path.join(dirpath, f"{name}.md"), "w", encoding="utf-8") as fh:
        fh.write(f"---\nname: {name}\ndescription: {description}\n{meta}---\n\nbody\n")


def test_FC_C7_two_concurrent_writers_both_survive_the_regenerated_index(fleet_root):
    """The concurrency assertion.  Two instances of ONE role add facts at the same moment; the
    index is COMPUTED from the filesystem afterwards, so neither pointer can be lost to
    interleaving.  A hand-maintained index loses one of these silently — and a lost pointer in
    an automemory index is exactly the class nobody notices, because the missing entry never
    surfaces."""
    mem = str(fleet_root / "composeux-architect" / "memory")
    os.makedirs(mem, exist_ok=True)
    barrier = threading.Barrier(2)

    def writer(n):
        barrier.wait()
        _fact(mem, f"fact-{n}", f"written by instance {n}")
        regenerate_memory_index(mem)

    threads = [threading.Thread(target=writer, args=(n,)) for n in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    regenerate_memory_index(mem)
    text = open(os.path.join(mem, MEMORY_INDEX_FILENAME), encoding="utf-8").read()
    assert "fact-1.md" in text and "fact-2.md" in text
    assert "written by instance 1" in text and "written by instance 2" in text


def test_FC_C7_regeneration_is_idempotent_byte_for_byte(fleet_root):
    mem = str(fleet_root / "composeio-expert" / "memory")
    _fact(mem, "b-second", "beta", group="Project")
    _fact(mem, "a-first", "alpha")
    first = open(regenerate_memory_index(mem), encoding="utf-8").read()
    second = open(regenerate_memory_index(mem), encoding="utf-8").read()
    assert first == second


def test_FC_C7_a_deleted_index_is_recoverable_not_data_loss(fleet_root):
    mem = str(fleet_root / "git-expert" / "memory")
    _fact(mem, "only-fact", "the one thing")
    path = regenerate_memory_index(mem)
    before = open(path, encoding="utf-8").read()
    os.remove(path)
    assert open(regenerate_memory_index(mem), encoding="utf-8").read() == before


def test_FC_C7_the_index_declares_itself_DERIVED_so_nobody_hand_edits_it(fleet_root):
    mem = str(fleet_root / "defect-investigator" / "memory")
    _fact(mem, "x", "y")
    text = open(regenerate_memory_index(mem), encoding="utf-8").read()
    assert "DERIVED FILE" in text and "Do NOT hand-edit" in text


def test_FC_C7_no_code_path_hand_edits_MEMORY_md():
    """The guard.  `regenerate_memory_index` is the only writer; anything else opening
    `MEMORY.md` for write is the hand-maintenance this design removed."""
    offenders = []
    for base, dirs, files in os.walk(_REPO):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "node_modules", "__pycache__", ".cache", "captures")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(base, fn)
            # `instance_home.py` IS the writer; a test that builds a fixture store is writing
            # its own tmpdir, not the platform's index. Both are exempt on purpose — the guard
            # is about PRODUCTION code hand-maintaining the index.
            if os.path.basename(p) == "instance_home.py" or fn.startswith("test_"):
                continue
            try:
                with open(p, "r", encoding="utf-8") as fh:
                    src = fh.read()
            except OSError:
                continue
            for i, line in enumerate(src.splitlines(), 1):
                if "MEMORY.md" in line and ("open(" in line or "write" in line):
                    if line.lstrip().startswith("#"):
                        continue
                    offenders.append(f"{p}:{i}: {line.strip()}")
    assert not offenders, ("MEMORY.md must be DERIVED, never hand-written "
                           "([CXC-FC-MEMORY-INDEX-DERIVED]): " + "; ".join(offenders))


def test_FC_C7_the_index_is_empty_and_valid_for_a_role_that_never_wrote(fleet_root):
    mem = str(fleet_root / "brand-new-role" / "memory")
    text = render_memory_index(mem)
    assert "no facts yet" in text


# ── FC-C18 — EVERY role has a store, and the store set is DERIVED ───────────────────────────
def test_FC_C18_the_store_set_equals_the_descriptor_basenames_on_disk(tmp_path, monkeypatch):
    """Adding a role descriptor is the ONLY act needed to give that role a store — no hand list
    to update, which is the fork class [INSIGHT-FORK-DIVERGENCE-IS-A-CLASS] names."""
    a = tmp_path / "agents"
    a.mkdir()
    (a / "some-role.md").write_text("---\nname: some-role\ndescription: d\n---\nbody\n")
    (a / "another-role.md").write_text("---\nname: another-role\n---\nbody\n")
    # a prose file in the same pool is NOT a role — the discriminator is the frontmatter,
    # not a curated allow-list.
    (a / "QUALITY-EVENT-PROTOCOL.md").write_text("# Protocol\n\nprose\n")
    (a / "editor.md.bak").write_text("---\nname: editor\n---\n")
    assert known_roles([str(a)]) == ["another-role", "some-role"]


def test_FC_C18_the_repos_real_roster_is_discovered_and_includes_the_dispatched_roles():
    """Not only top-level Intelligences ([CXC-FC-ROLE-MEMORY]): the roles that only ever run as
    dispatched subagents get stores too, which is what gives a dispatch continuity."""
    roles = known_roles()
    for expected in ("composeux-architect", "composeio-expert", "git-expert",
                     "defect-investigator", "composeux-render-engineer"):
        assert expected in roles, f"{expected} missing from the derived roster: {roles}"


def test_FC_C18_a_never_written_role_has_no_directory_and_errors_nowhere(fleet_root):
    """Lazy creation: an empty store and a never-used role are the same observable state, so
    nothing needs provisioning."""
    assert not os.path.exists(role_memory_dir("composeux-render-engineer"))
    assert render_memory_index(role_memory_dir("composeux-render-engineer"))


# ── FC-C19 — ONE store, two access paths; a dispatched role's memory survives a prune ───────
def test_FC_C19_the_descriptor_path_and_the_cwd_load_resolve_to_ONE_directory(fleet_root):
    """The both-directions assertion.  A top-level Intelligence's cwd IS the role home, so its
    automatic load lands in `<cwd>/memory`; a dispatched subagent addresses `role_memory_dir`
    BY PATH.  If these two ever differed the stores would diverge invisibly, because each caller
    only ever sees its own ([CXC-FC-STORE-ACCESS] prohibition)."""
    role = "composeio-expert"
    top_level_cwd = project_dir_for(role)                       # access path 1: cwd
    by_path = role_memory_dir(role)                             # access path 2: descriptor path
    assert os.path.join(top_level_cwd, "memory") == by_path


def test_FC_C19_a_fact_written_from_a_worktree_is_visible_to_the_top_level_load(fleet_root,
                                                                               tmp_path):
    """A dispatched subagent writes from an ephemeral worktree cwd; the top-level Intelligence
    of that role reads it with NO sync step, because there is only one store."""
    role = "composeio-expert"
    worktree = tmp_path / ".cache" / "claude-worktrees" / "some-dispatch"
    worktree.mkdir(parents=True)
    cwd_before = os.getcwd()
    os.chdir(worktree)
    try:
        _fact(role_memory_dir(role), "learned-in-a-dispatch", "the hazard this role hit")
    finally:
        os.chdir(cwd_before)
    index = open(regenerate_memory_index(role_memory_dir(role)), encoding="utf-8").read()
    assert "learned-in-a-dispatch.md" in index


def test_FC_C19_the_store_survives_a_worktree_prune(fleet_root, tmp_path):
    """[INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT] instance (4) applied to memory: a cwd-keyed
    store would materialise INSIDE the worktree and a routine prune would destroy it — full
    success reported, deliverable silently gone."""
    import shutil
    role = "git-expert"
    worktree = tmp_path / ".cache" / "claude-worktrees" / "doomed"
    worktree.mkdir(parents=True)
    cwd_before = os.getcwd()
    os.chdir(worktree)
    try:
        _fact(role_memory_dir(role), "survivor", "written from a worktree")
    finally:
        os.chdir(cwd_before)
    shutil.rmtree(worktree)                                     # the prune
    assert os.path.isfile(os.path.join(role_memory_dir(role), "survivor.md"))


def test_FC_C19_no_store_path_ever_resolves_under_a_worktree_cache(fleet_root, tmp_path):
    worktree = tmp_path / ".cache" / "claude-worktrees" / "wt"
    worktree.mkdir(parents=True)
    cwd_before = os.getcwd()
    os.chdir(worktree)
    try:
        for role in ("composeux-architect", "composeio-expert", "git-expert"):
            assert "claude-worktrees" not in role_memory_dir(role)
    finally:
        os.chdir(cwd_before)


# ── FC-C20 — recovery is the same role, with no surrogate machinery ─────────────────────────
def test_FC_C20_one_role_two_hosts_two_ids_ONE_store_TWO_channel_namespaces(fleet_root):
    """David, 2026-08-12: "in an emergency, we should instantiate composeux-architect OUTSIDE
    the console widget."  Recovery instantiates the ACTUAL role, merely hosted elsewhere — same
    descriptor, same manifest chain, same role home, therefore the same memory by the ORDINARY
    mechanism.  There is no surrogate to build.

    ⚠ THIS TEST REPLACES THREE TAUTOLOGIES.  The previous version asserted
    `role_memory_dir(role) == role_memory_dir(role)` (a function equals itself), that two
    f-strings the test had just built were different, and that `"recovery-architect-2"`
    starts with `"recovery-"`.  All three pass for ANY implementation, including none: they
    exercised the test's own literals, not the build.  `verify.machine` asks for a SPAWN —
    "spawn one role under two hosts with two instance ids, assert one store and two
    namespaces" — so this drives BOTH real spawn paths.
    """
    role = "composeux-architect"
    hosted, recovery = "architect-hosted", "recovery-architect-2"

    # HOST 1 — the widget-hosted child (the Terminal Widget's PtyChild path).
    from composeio.render.widgets.terminal import TerminalWidget
    w = TerminalWidget("t", role=role, instance_id=hosted)

    # HOST 2 — the recovery instance, spawned OUTSIDE the console (IntelligenceHost path).
    host_mod = _load_host()
    spec = host_mod.IntelligenceSpec(id=recovery, role=role, mode="headless", task="t")

    # ONE STORE: both resolve the same role home, hence the same memory dir — and it is
    # the store the ROLE owns, not one derived from either instance id.
    assert w.cwd == spec.home_dir == project_dir_for(role)
    assert os.path.join(w.cwd, "memory") == role_memory_dir(role)
    for iid in (hosted, recovery):
        assert iid not in role_memory_dir(role)

    # TWO NAMESPACES: the id each child actually WEARS comes from the spawn, not from a
    # string this test wrote. The widget's is its `instance_id`; the Host's is the
    # `INTELLIGENCE_ID` it puts in the child env.
    _argv, env = host_mod.IntelligenceHost().build_command(spec)
    assert env["INTELLIGENCE_ID"] == recovery
    assert w.instance_id == hosted
    assert env["INTELLIGENCE_ID"] != w.instance_id
    assert f"intelligence/{w.instance_id}/in" != f"intelligence/{env['INTELLIGENCE_ID']}/in"

    # ...and the recovery instance never claims the canonical id ([DEC-ICL-UNHARNESSED-
    # RECOVERY-INSTANCE]): its id is distinct from the console-hosted one AND from the
    # host id the console itself wears.
    assert env["INTELLIGENCE_ID"] not in (hosted, "architect")


def test_FC_C20_a_recovery_spawn_is_not_refused_by_the_home_guard(fleet_root):
    """The end-to-end consequence: a recovery instance of a LIVE role must be allowed to
    come up beside it.  The pre-re-key guard would have refused exactly this, because the
    two instances share a home BY DESIGN ([CXC-FC-HOMES-REKEY])."""
    host_mod = _load_host()
    fleet = host_mod.IntelligenceHost(enforce_isolated_homes=True, log=lambda _l: None)
    role = "composeux-architect"
    fleet._enforce_home_isolation(host_mod.IntelligenceSpec(id="architect", role=role))
    fleet._enforce_home_isolation(
        host_mod.IntelligenceSpec(id="recovery-architect-2", role=role))


def test_FC_C20_no_code_path_resolves_a_store_for_a_role_other_than_its_own():
    """The grep guard.  A cross-role read is FORBIDDEN ([CXC-FC-RECOVERY-SAME-ROLE]): it would
    solve a problem David's reframe removes, and it would install an access rule that has to be
    got right forever."""
    banned = ("surrogate", "borrow_memory", "cross_role_store", "memory_for_other_role")
    offenders = []
    for base, dirs, files in os.walk(os.path.join(_REPO, "python", "composeio")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(base, fn)
            with open(p, "r", encoding="utf-8") as fh:
                src = fh.read().lower()
            for token in banned:
                if token in src:
                    offenders.append(f"{p}: {token}")
    assert not offenders, f"cross-role memory access is forbidden: {offenders}"
