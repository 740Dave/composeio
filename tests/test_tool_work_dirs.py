"""Every release-gate tool removes the work folder it made, and the removal is guarded.

THE DEFECT. Five tools made a folder with tempfile.mkdtemp and never removed it:
  tools/ci_gitleaks.py       "ci-gitleaks-"       the gitleaks binary, a copy of the tree, a history clone
  tools/leakcheck.py         "leakcheck-"         a copy of the scanned tree (private-derived)
  tools/queue_leakcheck.py   "queue-leakcheck-"   the PR text and a worktree of the head
  tests/leakcheck/negative_control.py  "leakcheck-negctl-"  scratch repositories, a synthetic vocabulary
  tools/source_capture_diff.py  "capture-locks-"  a private lock folder per capture
Plumb measured 410 ci-gitleaks-* folders, 14.2 GiB, in TMPDIR on one Mac.

THE FIX. One helper, tools/workdir.py (modelled on FactLattice's PR #98): remove in a `finally` by the
exact path; refuse a symlink, a path that is not a direct child of the temp folder, or a wrong prefix,
and say why; COMPOSEIO_KEEP_WORK=1 keeps the folder and prints its path once.

⛔ THE REAL TMPDIR IS NEVER READ. The autouse fixture points TMPDIR, and `tempfile.tempdir` (which
caches it), at a folder under pytest's tmp_path before any tool is loaded, and every assertion lists
only that folder. Gate runs on this machine create these same prefixes in the real TMPDIR right now,
so a test that globbed the real one would be measuring someone else's run.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def tmpdir_iso(tmp_path, monkeypatch):
    d = tmp_path / "TMPDIR"
    d.mkdir()
    monkeypatch.setenv("TMPDIR", str(d))
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    for var in ("CI", "GITHUB_ACTIONS", "BASE_REF", "COMPOSEIO_KEEP_WORK"):
        monkeypatch.delenv(var, raising=False)
    assert tempfile.gettempdir() == str(d)
    return d


def left(d):
    return sorted(p.name for p in pathlib.Path(d).iterdir())


def git_repo(root, name="tree"):
    r = root / name
    r.mkdir()
    g = ["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run(["git", "init", "-q", "-b", "main", str(r)], check=True)
    (r / "a.txt").write_text("nothing secret\n")
    subprocess.run(g + ["add", "a.txt"], check=True)
    subprocess.run(g + ["commit", "-q", "--no-verify", "-m", "base"], check=True)
    base = subprocess.run(g + ["rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    (r / "b.txt").write_text("a clean line\n")
    subprocess.run(g + ["add", "b.txt"], check=True)
    subprocess.run(g + ["commit", "-q", "--no-verify", "-m", "second"], check=True)
    return r, base


def boom(*a, **k):
    raise RuntimeError("boom")


# --------------------------------------------------------------------------- #
# the helper: removal, the three refusals, KEEP_WORK
# --------------------------------------------------------------------------- #

@pytest.fixture
def wd():
    return _load(TOOLS / "workdir.py", "_workdir_under_test")


def test_helper_removes_on_return_and_on_an_exception(wd, tmpdir_iso):
    assert wd.run_in_work_dir("x-", "t", lambda w: os.path.isdir(w)) is True
    with pytest.raises(RuntimeError):
        wd.run_in_work_dir("x-", "t", boom)
    with pytest.raises(KeyboardInterrupt):
        wd.run_in_work_dir("x-", "t", lambda w: (_ for _ in ()).throw(KeyboardInterrupt()))
    assert left(tmpdir_iso) == []


def test_helper_removes_read_only_entries(wd, tmpdir_iso):
    def ro(w):
        sub = pathlib.Path(w) / "ro"
        sub.mkdir()
        (sub / "f").write_text("x")
        (sub / "f").chmod(0o444)
        sub.chmod(0o555)
    wd.run_in_work_dir("x-", "t", ro)
    assert left(tmpdir_iso) == []


def test_helper_refuses_a_symlink(wd, tmpdir_iso, tmp_path, capsys):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    link = tmpdir_iso / "x-link"
    link.symlink_to(victim, target_is_directory=True)
    assert wd.remove_work(link, "x-", "t") is False
    assert (victim / "keep").exists() and link.is_symlink()
    err = capsys.readouterr().err
    assert "REFUSED" in err and "symlink" in err


@pytest.mark.parametrize("spelling", [
    "{link}/",          # F1: lstat follows a trailing slash, so islink said False
    "{link}//",
    "{link}/./",
    "{tmp}//{name}",
    "{tmp}/./{name}/",
])
def test_helper_refuses_a_symlink_however_it_is_spelled(wd, tmpdir_iso, tmp_path, spelling):
    """Candidate review F1: `<tmp>/x-link/` passed every check and rmtree
    deleted the link's TARGET, a folder outside the temp folder."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    link = tmpdir_iso / "x-link"
    link.symlink_to(victim, target_is_directory=True)
    path = spelling.format(link=link, tmp=tmpdir_iso, name=link.name)
    assert wd.remove_work(path, "x-", "t") is False
    assert (victim / "keep").exists(), "the symlink's target was deleted"
    assert link.is_symlink()


@pytest.mark.parametrize("spelling", ["{d}/", "{d}//", "{tmp}/./{name}/", "{tmp}//{name}"])
def test_helper_removes_a_real_work_folder_however_it_is_spelled(wd, tmpdir_iso, spelling):
    """CONTROL for the test above: the same spellings of a REAL folder are
    removed, so the refusal there is about the link, not the spelling."""
    d = tmpdir_iso / "x-real"
    d.mkdir()
    (d / "f").write_text("x")
    assert wd.remove_work(spelling.format(d=d, tmp=tmpdir_iso, name=d.name), "x-", "t") is True
    assert not d.exists()


def test_helper_refuses_a_wrong_prefix_and_a_path_outside_the_temp_folder(wd, tmpdir_iso, tmp_path, capsys):
    wrong = tmpdir_iso / "other-x"
    wrong.mkdir()
    nested = tmpdir_iso / "x-a" / "x-sub"
    nested.mkdir(parents=True)
    outside = tmp_path / "x-outside"
    outside.mkdir()
    for bad in (wrong, nested, outside, tmpdir_iso):
        assert wd.remove_work(bad, "x-", "t") is False, bad
        assert bad.is_dir(), bad
    err = capsys.readouterr().err
    assert "does not start with 'x-'" in err and "not a direct child" in err


def test_keep_work_keeps_the_folder_and_prints_its_path_once(wd, tmpdir_iso, monkeypatch, capsys):
    monkeypatch.setenv("COMPOSEIO_KEEP_WORK", "1")
    seen = []
    wd.run_in_work_dir("x-", "t", seen.append)
    (kept,) = left(tmpdir_iso)
    assert str(tmpdir_iso / kept) == seen[0]
    lines = [ln for ln in capsys.readouterr().out.splitlines() if seen[0] in ln]
    assert len(lines) == 1 and "kept" in lines[0]


# --------------------------------------------------------------------------- #
# ci_gitleaks
# --------------------------------------------------------------------------- #

@pytest.fixture
def gl(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "gitleaks"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n[ \"$1\" = version ] && echo 0.0.0-fake\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("GITLEAKS_BIN", str(fake))
    return _load(TOOLS / "ci_gitleaks.py", "_ci_gitleaks_under_test")


def test_ci_gitleaks_leaves_nothing_after_a_run_a_refusal_and_an_exception(gl, tmp_path, tmpdir_iso, monkeypatch):
    tree, base = git_repo(tmp_path)
    assert gl.main(["--tree", str(tree), "--history-base", base]) == 0
    assert left(tmpdir_iso) == [], "a clean run"
    monkeypatch.setenv("CI", "1")                 # GITLEAKS_BIN under CI: refused, exit 2
    assert gl.main(["--tree", str(tree)]) == 2
    assert left(tmpdir_iso) == [], "an early could-not-run return"
    monkeypatch.delenv("CI")
    monkeypatch.setattr(gl, "stage_tree", boom)
    with pytest.raises(RuntimeError):
        gl.main(["--tree", str(tree)])
    assert left(tmpdir_iso) == [], "an exception"


def test_ci_gitleaks_keep_work(gl, tmp_path, tmpdir_iso, monkeypatch):
    """CONTROL (accepted as A2): KEEP_WORK keeps the folder. Main kept every
    folder, so this passes there by construction; it pins that the opt-out
    survives the fix."""
    tree, base = git_repo(tmp_path)
    monkeypatch.setenv("COMPOSEIO_KEEP_WORK", "1")
    assert gl.main(["--tree", str(tree)]) == 0
    assert [n for n in left(tmpdir_iso) if n.startswith("ci-gitleaks-")] != []


# --------------------------------------------------------------------------- #
# leakcheck
# --------------------------------------------------------------------------- #

@pytest.fixture
def lc():
    return _load(TOOLS / "leakcheck.py", "_leakcheck_under_test")


@pytest.fixture
def synthetic_private(tmp_path):
    nc = _load(REPO / "tests" / "leakcheck" / "negative_control.py", "_negctl_vocab")
    p = tmp_path / "private"
    p.mkdir()
    (p / "publish-vocabulary.tsv").write_text(nc.SYNTHETIC_VOCAB, encoding="utf-8")
    (p / "disclosure-denylist.tsv").write_text(nc.SYNTHETIC_DENY, encoding="utf-8")
    return p


def test_leakcheck_leaves_nothing_after_a_run_and_an_exception(lc, tmp_path, tmpdir_iso, synthetic_private, monkeypatch):
    tree, base = git_repo(tmp_path)
    args = ["diff", "--repo", str(tree), "--base", base, "--head", "HEAD", "--private-dir", str(synthetic_private)]
    assert lc.main(args) == 0
    assert left(tmpdir_iso) == [], "a clean run"
    monkeypatch.setattr(lc, "run_guard", boom)
    with pytest.raises(RuntimeError):
        lc.main(args)
    assert left(tmpdir_iso) == [], "an exception"


def test_leakcheck_keep_flag_keeps_the_scan_tree(lc, tmp_path, tmpdir_iso, synthetic_private):
    """CONTROL (accepted as A2): --keep keeps the scan tree. Main kept every
    folder, so this passes there by construction; it pins that --keep still
    works through the helper."""
    tree, base = git_repo(tmp_path)
    assert lc.main(["diff", "--repo", str(tree), "--base", base, "--private-dir", str(synthetic_private), "--keep"]) == 0
    kept = [n for n in left(tmpdir_iso) if n.startswith("leakcheck-")]
    assert len(kept) == 1 and (tmpdir_iso / kept[0] / "scan").is_dir()


# --------------------------------------------------------------------------- #
# queue_leakcheck: the real body up to its first refusal (a head that does not
# contain main), and an exception raised inside the body
# --------------------------------------------------------------------------- #

@pytest.fixture
def ql():
    return _load(TOOLS / "queue_leakcheck.py", "_queue_leakcheck_under_test")


def test_queue_leakcheck_leaves_nothing_after_a_refusal_and_an_exception(ql, tmp_path, tmpdir_iso, monkeypatch):
    pr = tmp_path / "pr.txt"
    pr.write_text("title\n\nbody\n")
    args = ["--local-probe", "--pr-text", str(pr), "--head", "0" * 40, "--private-dir", str(tmp_path / "none")]
    assert ql.main(args) == 1                    # REFUSED: the head does not contain main's tip
    assert left(tmpdir_iso) == [], "a refusal"
    monkeypatch.setattr(ql, "say", boom)
    with pytest.raises(RuntimeError):
        ql.main(args)
    assert left(tmpdir_iso) == [], "an exception"


# --------------------------------------------------------------------------- #
# negative_control
# --------------------------------------------------------------------------- #

@pytest.fixture
def nc():
    return _load(REPO / "tests" / "leakcheck" / "negative_control.py", "_negctl_under_test")


def test_negative_control_leaves_nothing_after_a_run_and_an_exception(nc, tmpdir_iso, monkeypatch):
    # The real synthetic run, without the gitleaks cases (they download the pinned binary). Its leakcheck
    # subprocesses inherit TMPDIR, so their folders are counted here too.
    assert nc.main(["--synthetic", "--no-gitleaks"]) == 0
    assert left(tmpdir_iso) == [], "a full run"
    monkeypatch.setattr(nc, "load_guard", boom)
    with pytest.raises(RuntimeError):
        nc.main(["--synthetic", "--no-gitleaks"])
    assert left(tmpdir_iso) == [], "an exception"


# --------------------------------------------------------------------------- #
# source_capture_diff
# --------------------------------------------------------------------------- #

def test_source_capture_diff_leaves_no_lock_folder(tmp_path, tmpdir_iso, monkeypatch):
    pytest.importorskip("paho.mqtt.client")
    src = tmp_path / "src"
    src.mkdir()
    (src / "w26_quiet_source.py").write_text("def main(argv, stop=None):\n    if stop is not None:\n        stop.wait(5)\n")
    monkeypatch.syspath_prepend(str(src))
    monkeypatch.syspath_prepend(str(TOOLS))
    scd = _load(TOOLS / "source_capture_diff.py", "_source_capture_diff_under_test")
    scd.capture("w26_quiet_source", [], 0.05)
    assert left(tmpdir_iso) == [], "a capture"
    with pytest.raises(ModuleNotFoundError):
        scd.capture("w26_no_such_source_module", [], 0.05)
    assert left(tmpdir_iso) == [], "a capture that raises"


# --------------------------------------------------------------------------- #
# SIGTERM (candidate review F2): a REAL child process is sent SIGTERM while its
# work folder is live. The default action ends a process without unwinding the
# stack, so before the fix the `finally` never ran and the folder was left.
# --------------------------------------------------------------------------- #
import signal                                                    # noqa: E402

CHILD = r'''
import importlib.util, signal, sys, time
spec = importlib.util.spec_from_file_location("wd", sys.argv[1])
wd = importlib.util.module_from_spec(spec); spec.loader.exec_module(wd)
if sys.argv[2] == "chain":
    def mine(signum, frame):
        open(sys.argv[3], "w").write("called")
    signal.signal(signal.SIGTERM, mine)
def body(work):
    print(work, flush=True)
    time.sleep(30)
wd.run_in_work_dir("x-", "t", body)
'''


def _term_child(tmp_path, tmpdir_iso, mode, marker=None):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    env = dict(os.environ, TMPDIR=str(tmpdir_iso))
    p = subprocess.Popen([sys.executable, "-I", str(script), str(TOOLS / "workdir.py"), mode,
                          str(marker or "")], stdout=subprocess.PIPE, text=True, env=env)
    try:
        work = p.stdout.readline().strip()
        assert work and os.path.isdir(work), f"child did not report a live work folder: {work!r}"
        p.send_signal(signal.SIGTERM)
        rc = p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()
    return work, rc


def test_a_sigterm_killed_run_removes_its_work_folder(tmp_path, tmpdir_iso):
    work, rc = _term_child(tmp_path, tmpdir_iso, "plain")
    assert not os.path.exists(work), "SIGTERM left the work folder behind"
    assert rc == 128 + signal.SIGTERM, rc
    assert left(tmpdir_iso) == []


def test_sigterm_chains_to_a_handler_already_set(tmp_path, tmpdir_iso):
    marker = tmp_path / "handler-ran"
    work, rc = _term_child(tmp_path, tmpdir_iso, "chain", marker)
    assert marker.read_text() == "called", "the caller's own SIGTERM handler was not called"
    assert not os.path.exists(work)
    assert left(tmpdir_iso) == []


def test_the_previous_sigterm_handler_is_restored(wd):
    before = signal.getsignal(signal.SIGTERM)
    during = []
    wd.run_in_work_dir("x-", "t", lambda w: during.append(signal.getsignal(signal.SIGTERM)))
    assert signal.getsignal(signal.SIGTERM) is before
    assert during[0] is not before, "no SIGTERM handler was installed while the folder was live"


def test_off_the_main_thread_nothing_is_installed(wd, tmpdir_iso):
    import threading
    out = {}

    def run():
        out["rc"] = wd.run_in_work_dir("x-", "t", lambda w: signal.getsignal(signal.SIGTERM))

    before = signal.getsignal(signal.SIGTERM)
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    assert out["rc"] is before
    assert left(tmpdir_iso) == []


# --------------------------------------------------------------------------- #
# Matching FactLattice PR #98 as merged (de896c0) and its two reviews
# --------------------------------------------------------------------------- #

def test_a_cleanup_error_never_replaces_the_result(wd, tmpdir_iso, monkeypatch, capsys):
    """Any Exception in cleanup: ONE line naming the folder and the exception
    class, and fn's own result is returned."""
    def broken_rmtree(*a, **k):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(wd.shutil, "rmtree", broken_rmtree)
    seen = []
    assert wd.run_in_work_dir("x-", "t", lambda w: seen.append(w) or 7) == 7
    lines = [ln for ln in capsys.readouterr().err.splitlines() if seen[0] in ln]
    assert len(lines) == 1 and "RuntimeError" in lines[0], lines


def test_a_guard_that_itself_fails_never_replaces_the_result(wd, tmpdir_iso, monkeypatch, capsys):
    def broken_guard(*a, **k):
        raise RuntimeError("guard exploded")
    monkeypatch.setattr(wd, "check_removable", broken_guard)
    assert wd.run_in_work_dir("x-", "t", lambda w: 3) == 3
    err = capsys.readouterr().err.splitlines()
    assert len(err) == 1 and "RuntimeError" in err[0], err


def test_keyboardinterrupt_in_cleanup_still_propagates(wd, tmpdir_iso, monkeypatch):
    def ki(*a, **k):
        raise KeyboardInterrupt()
    monkeypatch.setattr(wd.shutil, "rmtree", ki)
    with pytest.raises(KeyboardInterrupt):
        wd.run_in_work_dir("x-", "t", lambda w: 0)


def test_a_tools_exit_code_survives_a_cleanup_failure(gl, tmp_path, tmpdir_iso, monkeypatch, capsys):
    import shutil as _shutil
    tree, base = git_repo(tmp_path)
    def broken_rmtree(*a, **k):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(_shutil, "rmtree", broken_rmtree)
    assert gl.main(["--tree", str(tree)]) == 0, "a clean scan must still exit 0"
    err = [ln for ln in capsys.readouterr().err.splitlines() if "could not remove" in ln]
    assert len(err) == 1 and "PermissionError" in err[0]


def test_the_guard_is_an_explicit_raise(wd, tmpdir_iso, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    link = tmpdir_iso / "x-link"
    link.symlink_to(victim, target_is_directory=True)
    with pytest.raises(wd.WorkDirRefused):
        wd.check_removable(link, "x-")


def test_the_guard_still_refuses_under_python_O(tmp_path, tmpdir_iso):
    """`assert` is stripped by -O. In a real `python -O -I` child the symlinked
    leaf is still refused and its target survives."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    link = tmpdir_iso / "x-link"
    link.symlink_to(victim, target_is_directory=True)
    code = ("import importlib.util,sys\n"
            "s=importlib.util.spec_from_file_location('wd',sys.argv[1]);wd=importlib.util.module_from_spec(s);s.loader.exec_module(wd)\n"
            "assert not __debug__\n"
            "print(wd.remove_work(sys.argv[2],'x-','t'))\n"
            "try:\n wd.check_removable(sys.argv[2],'x-'); print('NOT-REFUSED')\n"
            "except wd.WorkDirRefused: print('REFUSED')\n")
    p = subprocess.run([sys.executable, "-O", "-I", "-c", code, str(TOOLS / "workdir.py"), str(link)],
                       capture_output=True, text=True, env=dict(os.environ, TMPDIR=str(tmpdir_iso)), timeout=30)
    assert p.returncode == 0, p.stderr
    assert p.stdout.split() == ["False", "REFUSED"], p.stdout
    assert (victim / "keep").exists()


def _ro_tree_with(wd, tmpdir_iso, entry_maker):
    work = pathlib.Path(tempfile.mkdtemp(prefix="x-"))
    ro = work / "ro"
    ro.mkdir()
    entry_maker(ro)
    ro.chmod(0o555)
    return work


def test_the_retry_never_chmods_a_symlinks_target(wd, tmpdir_iso, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    outside.chmod(0o444)
    work = _ro_tree_with(wd, tmpdir_iso, lambda ro: (ro / "link").symlink_to(outside))
    assert wd.remove_work(work, "x-", "t") is True
    assert stat.S_IMODE(outside.stat().st_mode) == 0o444, "the symlink's target was chmodded"


def test_the_retry_never_chmods_a_hard_linked_file(wd, tmpdir_iso, tmp_path):
    """DIAMOND delta P3-3: a file inside the folder can share an inode with one
    outside; only folders are ever chmodded, so its mode is untouched."""
    outside = tmpdir_iso / "outside-hard.txt"           # same filesystem as the work folder
    outside.write_text("x")
    outside.chmod(0o444)
    work = _ro_tree_with(wd, tmpdir_iso, lambda ro: os.link(outside, ro / "hard"))
    assert wd.remove_work(work, "x-", "t") is True
    assert stat.S_IMODE(outside.stat().st_mode) == 0o444
    outside.chmod(0o644)
    outside.unlink()


def test_the_retry_never_chmods_the_temp_root(wd, tmpdir_iso, capsys):
    """A temp root that is not writable: removal fails, ONE line, the result is
    kept, and the temp root keeps its mode (it used to be chmodded to 0700)."""
    seen = []

    def body(w):
        seen.append(w)
        tmpdir_iso.chmod(0o555)
        return 5
    try:
        assert wd.run_in_work_dir("x-", "t", body) == 5
        assert stat.S_IMODE(tmpdir_iso.stat().st_mode) == 0o555, "the temp root was chmodded"
    finally:
        tmpdir_iso.chmod(0o755)
    lines = [ln for ln in capsys.readouterr().err.splitlines() if seen[0] in ln]
    assert len(lines) == 1 and "PermissionError" in lines[0], lines


@pytest.fixture
def tmpdir_link(tmp_path, monkeypatch):
    """TMPDIR is a SYMLINK to a real folder: the default macOS shape (/var -> /private/var)."""
    real = tmp_path / "real-tmp"
    real.mkdir()
    link = tmp_path / "tmp-link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv("TMPDIR", str(link))
    monkeypatch.setattr(tempfile, "tempdir", str(link))
    return link


def test_a_symlinked_temp_folder_still_has_its_work_folder_removed(wd, tmpdir_link, tmp_path):
    seen = []
    wd.run_in_work_dir("x-", "t", seen.append)
    assert seen[0].startswith(str(tmpdir_link)), seen
    assert left(tmpdir_link) == []


def test_a_symlinked_temp_folder_with_a_real_tool(gl, tmpdir_link, tmp_path):
    tree, base = git_repo(tmp_path)
    assert gl.main(["--tree", str(tree), "--history-base", base]) == 0
    assert left(tmpdir_link) == []


def test_a_symlinked_leaf_is_still_refused_under_a_symlinked_temp_folder(wd, tmpdir_link, tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    leaf = tmpdir_link / "x-leaf"
    leaf.symlink_to(victim, target_is_directory=True)
    assert wd.remove_work(leaf, "x-", "t") is False
    assert wd.remove_work(str(leaf) + "/", "x-", "t") is False
    assert (victim / "keep").exists()


# --------------------------------------------------------------------------- #
# SIGTERM DURING THE REMOVAL (candidate review R2-N1). The handler used to be
# restored BEFORE remove_work, so a SIGTERM mid-delete killed the process with
# the folder partly deleted (QA: 40,000 files left at 40,000, 38,433, 34,922).
# A real child builds a large folder, announces when rmtree starts, and is sent
# SIGTERM at three points into the delete.
# --------------------------------------------------------------------------- #

BIG_CHILD = r'''
import importlib.util, os, sys, time
spec = importlib.util.spec_from_file_location("wd", sys.argv[1])
wd = importlib.util.module_from_spec(spec); spec.loader.exec_module(wd)
N = int(sys.argv[2])
real_rmtree = wd.shutil.rmtree
def announced_rmtree(path, *a, **k):
    print("RMTREE-START", path, flush=True)
    real_rmtree(path, *a, **k)
    print("RMTREE-END", flush=True)
wd.shutil.rmtree = announced_rmtree
def body(work):
    for d in range(N // 1000):
        sub = os.path.join(work, f"d{d}")
        os.mkdir(sub)
        for f in range(1000):
            with open(os.path.join(sub, f"f{f}"), "w") as fh:
                fh.write("x")
    print("BUILT", work, flush=True)
wd.run_in_work_dir("x-", "t", body)
print("RETURNED", flush=True)
'''

BIG_N = 40_000


@pytest.mark.parametrize("delay_s", [0.0, 0.05, 0.2])
def test_a_sigterm_during_the_removal_still_removes_the_whole_folder(tmp_path, tmpdir_iso, delay_s):
    import time
    script = tmp_path / "big_child.py"
    script.write_text(BIG_CHILD)
    env = dict(os.environ, TMPDIR=str(tmpdir_iso))
    p = subprocess.Popen([sys.executable, "-I", str(script), str(TOOLS / "workdir.py"), str(BIG_N)],
                         stdout=subprocess.PIPE, text=True, env=env)
    try:
        built = p.stdout.readline().split()
        assert built[:1] == ["BUILT"], built
        work = built[1]
        start = p.stdout.readline().split()
        assert start[:1] == ["RMTREE-START"], start
        time.sleep(delay_s)
        still_there = os.path.isdir(work)
        p.send_signal(signal.SIGTERM)
        rest = p.stdout.read()
        rc = p.wait(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert still_there, "the delete finished before the signal: grow BIG_N, the test measured nothing"
    assert not os.path.exists(work), "a SIGTERM during the delete left (part of) the folder"
    assert "RMTREE-END" in rest, "the delete was cut off"
    assert "RETURNED" not in rest, "the recorded SIGTERM was not re-delivered"
    assert rc == 128 + signal.SIGTERM, rc
    assert left(tmpdir_iso) == []


def test_a_sigterm_recorded_during_removal_is_chained_to_the_previous_handler(wd, tmpdir_iso, monkeypatch):
    """In-process: a SIGTERM that lands while the folder is being removed is
    held, the folder is removed, the previous handler is restored, and only
    then is the signal re-delivered, through the previous Python handler."""
    calls = []

    def mine(signum, frame):
        calls.append("prev")
    old = signal.signal(signal.SIGTERM, mine)
    try:
        real = wd.shutil.rmtree

        def rmtree_with_signal(path, *a, **k):
            os.kill(os.getpid(), signal.SIGTERM)       # lands during the removal
            calls.append("rmtree-continues")
            real(path, *a, **k)
            calls.append("rmtree-done")
        monkeypatch.setattr(wd.shutil, "rmtree", rmtree_with_signal)
        seen = []
        with pytest.raises(SystemExit) as ei:
            wd.run_in_work_dir("x-", "t", seen.append)
        assert ei.value.code == 128 + signal.SIGTERM
        assert calls == ["rmtree-continues", "rmtree-done", "prev"], calls
        assert not os.path.exists(seen[0])
        assert signal.getsignal(signal.SIGTERM) is mine, "the previous handler was not restored"
    finally:
        signal.signal(signal.SIGTERM, old)
