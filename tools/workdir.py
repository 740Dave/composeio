"""One scratch work folder per run, removed on every exit. Shared by the release-gate tools.

    run_in_work_dir(prefix, tool, fn, keep=False) -> fn(work)'s return value

WHY. tools/ci_gitleaks.py, tools/leakcheck.py, tools/queue_leakcheck.py, tests/leakcheck/negative_control.py and
tools/source_capture_diff.py each made a folder with tempfile.mkdtemp and never removed it, on the theory that "the runner's
temp is discarded". On a developer Mac it is not (410 ci-gitleaks-* folders, 14.2 GiB, measured in TMPDIR). Several of them hold
a copy of the scanned tree, which is private-derived, so a leftover folder is a disclosure surface as well as wasted disk.

THE RULES (modelled on FactLattice's ci_gitleaks/ci_smoke fix, factlattice-internal-dev PR #98, with one shared copy here):
  - the folder is removed in a `finally`, by the exact path mkdtemp returned, so a clean run, a finding, an early return and
    an exception (KeyboardInterrupt included) all clean up, and so does SIGTERM on the main thread: while the folder is
    live a handler is installed (chained to any Python handler already set). During fn it raises SystemExit so the
    `finally` runs; during the removal it only RECORDS the signal, which is re-delivered once the folder is gone and
    the previous handler is restored (`_SigtermGuard`);
  - the path is normalised ONCE (`normalise`: trailing separators stripped, made absolute, links not resolved) and that
    one string is used for every check and for the removal;
  - before removing, `remove_work` checks that the path is NOT a symlink, that it is a DIRECT child of tempfile.gettempdir(), and
    that its name starts with the expected prefix. If any check fails it refuses, says why on stderr, and deletes nothing;
  - the guard (`check_removable`) is explicit checks that RAISE, never `assert`, so `python -O` keeps it; the leaf is
    judged with islink on the normalised path, the parent by realpath (a symlinked TMPDIR, the macOS default, still matches);
  - read-only folders (a git clone's objects can be) are made writable and the removal retried once, but only REAL folders
    whose realpath is inside the work folder's realpath: never the temp root, a symlink, its target, or a file;
  - a removal that fails for any Exception is reported in ONE line (folder and exception class) and never replaces the
    tool's own exit code; KeyboardInterrupt and SystemExit still propagate;
  - COMPOSEIO_KEEP_WORK=1 (or the caller's own keep flag, such as leakcheck's --keep) keeps the folder and prints its path once.

Each tool loads this file by path from its own folder (`load_workdir()` below, copied as four lines), because the gates run
under `python -I`, which does not put the script's folder on sys.path.
"""
from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
import threading

KEEP_ENV = "COMPOSEIO_KEEP_WORK"


def _say(tool, msg):
    print(f"[{tool}] {msg}", file=sys.stderr, flush=True)


def normalise(work) -> str:
    """The ONE spelling of `work` that every check and the removal use.

    Trailing separators are stripped and the path is made absolute (which also
    collapses `//`, `./` and `..` lexically). Links are NOT resolved: the leaf
    must stay a link if it is one, so `islink` can see it. This matters because
    a link spelled `<tmp>/x-link/` passes `islink` (lstat follows a trailing
    slash) while `abspath` strips the slash for the name checks; checking one
    spelling and deleting another let rmtree walk into the link's target
    (candidate review F1, 2026-11-27).
    """
    s = os.fspath(work)
    stripped = s.rstrip(os.sep) or os.sep
    return os.path.abspath(stripped)


class WorkDirRefused(ValueError):
    """The guard refused to remove a path. An explicit raise, so `python -O` cannot strip it."""


def check_removable(work, prefix) -> str:
    """The guard. -> the normalised path when it may be removed; RAISES WorkDirRefused otherwise.

    Explicit checks that raise, never `assert` (which `python -O` removes). The LEAF is judged
    on the one normalised string with no trailing separator (`os.path.islink`, an lstat), so a
    symlinked leaf is refused. The PARENT is compared by realpath, so a TMPDIR that is itself a
    symlink to the real temp folder (the default macOS shape: /var -> /private/var) still matches.
    """
    work = normalise(work)
    if os.path.islink(work):
        raise WorkDirRefused("it is a symlink")
    parent, name = os.path.split(work)
    if os.path.realpath(parent) != os.path.realpath(tempfile.gettempdir()):
        raise WorkDirRefused(f"it is not a direct child of the temp folder {tempfile.gettempdir()!r}")
    if not name.startswith(prefix):
        raise WorkDirRefused(f"its name does not start with {prefix!r}")
    if not os.path.isdir(work):
        raise WorkDirRefused("it is not a folder")
    return work


def refusal(work, prefix):
    """-> None when `work` may be removed, else the reason `check_removable` refuses it."""
    try:
        check_removable(work, prefix)
    except WorkDirRefused as e:
        return str(e)
    return None


def remove_work(work, prefix, tool="workdir") -> bool:
    """Remove `work` if the guard allows it. -> True when removed.

    A refusal or a failed removal is logged as ONE line on stderr and returns False; it never
    raises (KeyboardInterrupt and SystemExit still propagate). The string checked and the string
    removed are the same one, `normalise(work)`.
    """
    given = os.fspath(work)
    try:
        work = check_removable(given, prefix)
    except WorkDirRefused as e:
        _say(tool, f"REFUSED to remove the work folder {given!r}: {e}; nothing was deleted")
        return False
    real = os.path.realpath(work)

    def inside(p):
        """A real FOLDER (not a symlink) at or below the work folder's real path."""
        if os.path.islink(p) or not os.path.isdir(p):
            return False
        rp = os.path.realpath(p)
        return rp == real or rp.startswith(real + os.sep)

    def writable(func, path, exc):
        """Read-only retry: make the failing entry's folder (and the entry, if it is a folder)
        writable, then retry once. Only real folders inside the work folder are ever chmodded:
        never the temp root, never a symlink or its target, and never a file (unlinking a file
        needs only its folder's permission, and a file may be a hard link to one outside)."""
        fixed = False
        for p in (os.path.dirname(path), path):
            if inside(p):
                try:
                    os.chmod(p, 0o700)
                    fixed = True
                except OSError:
                    pass
        if not fixed:
            raise exc if isinstance(exc, BaseException) else exc[1]
        func(path)

    kw = {"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}
    try:
        shutil.rmtree(work, **kw)
    except Exception as e:  # a failed cleanup is said, in one line, and never replaces the tool's result
        _say(tool, f"could not remove the work folder {work!r}: {type(e).__name__}: {e}")
        return False
    return True


class _SigtermGuard:
    """While a work folder is live, including while it is being REMOVED, a SIGTERM must
    not end the process with the folder (or half of it) left behind.

    SIGTERM's default action ends the process WITHOUT unwinding, so a CI cancel, a job
    timeout or a plain `kill` left the folder behind (candidate review F2). Two phases:
      - RUNNING (fn is working): a SIGTERM is DELIVERED at once, so it interrupts fn and
        the `finally` runs the removal;
      - REMOVING (`defer()` called): a SIGTERM is RECORDED, not raised, so the delete is
        never cut off half-way (candidate review R2-N1: a 40,000-file folder was left at
        40,000, 38,433 and 34,922 files). `release()` restores the previous handler, then
        re-delivers a recorded SIGTERM.
    Delivering means: call the previous Python handler if there is one (CHAIN), then, if it
    returned, raise SystemExit(128 + SIGTERM), the status the default action reports.
    Rules:
      - main thread only (Python delivers signals there, and signal.signal refuses
        elsewhere); off the main thread nothing is installed;
      - previous handler SIG_IGN: left alone; SIGTERM is ignored on purpose;
      - previous handler SIG_DFL, not set from Python, or a Python callable: ours is
        installed, chaining to the callable as above.
    Known limit: between fn returning and `defer()` there are a few bytecodes in which a
    SIGTERM still raises; and SIGKILL cannot be guarded at all.
    """

    def __init__(self):
        self.active = False
        self.deferring = False
        self.pending = None
        self.prev = None
        self.status = 128 + int(signal.SIGTERM)
        if threading.current_thread() is not threading.main_thread():
            return
        prev = signal.getsignal(signal.SIGTERM)
        if prev is signal.SIG_IGN:
            return
        self.prev = prev
        signal.signal(signal.SIGTERM, self._on_term)
        self.active = True

    def _deliver(self, signum, frame):
        if callable(self.prev):
            self.prev(signum, frame)
        raise SystemExit(self.status)

    def _on_term(self, signum, frame):
        if self.deferring:
            self.pending = (signum, frame)
            return
        self._deliver(signum, frame)

    def defer(self):
        """From now on, record a SIGTERM instead of raising it (the removal is running)."""
        self.deferring = True

    def release(self):
        """Put the previous handler back, then re-deliver a SIGTERM recorded meanwhile."""
        if not self.active:
            return
        self.active = False
        signal.signal(signal.SIGTERM, self.prev if self.prev is not None else signal.SIG_DFL)
        if self.pending is not None:
            signum, frame = self.pending
            self.pending = None
            self._deliver(signum, frame)


def run_in_work_dir(prefix, tool, fn, keep=False):
    """Make a work folder, call fn(work) and return its result; remove the folder on every exit unless asked to keep it.

    "Every exit" includes SIGTERM on the main thread, during fn AND during the removal:
    see `_SigtermGuard`. A SIGTERM that lands during the removal is held until the folder
    is gone, then delivered (the process still ends with status 143).
    """
    work = tempfile.mkdtemp(prefix=prefix)
    guard = _SigtermGuard()
    try:
        return fn(work)
    finally:
        guard.defer()
        try:
            if keep or os.environ.get(KEEP_ENV) == "1":
                why = "--keep" if keep else f"{KEEP_ENV}=1"
                print(f"[{tool}] work folder kept ({why}): {work}", flush=True)
            else:
                try:
                    remove_work(work, prefix, tool)
                except Exception as e:  # belt and braces: cleanup must never replace fn's result
                    _say(tool, f"could not remove the work folder {work!r}: {type(e).__name__}")
        finally:
            guard.release()
