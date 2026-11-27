"""One scratch work folder per run, removed on every exit. Shared by the release-gate tools.

    run_in_work_dir(prefix, tool, fn, keep=False) -> fn(work)'s return value

WHY. tools/ci_gitleaks.py, tools/leakcheck.py, tools/queue_leakcheck.py, tests/leakcheck/negative_control.py and
tools/source_capture_diff.py each made a folder with tempfile.mkdtemp and never removed it, on the theory that "the runner's
temp is discarded". On a developer Mac it is not (410 ci-gitleaks-* folders, 14.2 GiB, measured in TMPDIR). Several of them hold
a copy of the scanned tree, which is private-derived, so a leftover folder is a disclosure surface as well as wasted disk.

THE RULES (modelled on FactLattice's ci_gitleaks/ci_smoke fix, factlattice-internal-dev PR #98, with one shared copy here):
  - the folder is removed in a `finally`, by the exact path mkdtemp returned, so a clean run, a finding, an early return and
    an exception (KeyboardInterrupt included) all clean up;
  - before removing, `remove_work` checks that the path is NOT a symlink, that it is a DIRECT child of tempfile.gettempdir(), and
    that its name starts with the expected prefix. If any check fails it refuses, says why on stderr, and deletes nothing;
  - read-only entries (a git clone's objects can be) are made writable and the removal retried once; a removal that still fails
    is reported on stderr and never replaces the tool's own exit code;
  - COMPOSEIO_KEEP_WORK=1 (or the caller's own keep flag, such as leakcheck's --keep) keeps the folder and prints its path once.

Each tool loads this file by path from its own folder (`load_workdir()` below, copied as four lines), because the gates run
under `python -I`, which does not put the script's folder on sys.path.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

KEEP_ENV = "COMPOSEIO_KEEP_WORK"


def _say(tool, msg):
    print(f"[{tool}] {msg}", file=sys.stderr, flush=True)


def refusal(work, prefix):
    """-> None when `work` may be removed, else the reason it may not."""
    work = os.fspath(work)
    if os.path.islink(work):
        return "it is a symlink"
    parent, name = os.path.split(os.path.abspath(work))
    if os.path.realpath(parent) != os.path.realpath(tempfile.gettempdir()):
        return f"it is not a direct child of the temp folder {tempfile.gettempdir()!r}"
    if not name.startswith(prefix):
        return f"its name does not start with {prefix!r}"
    if not os.path.isdir(work):
        return "it is not a folder"
    return None


def remove_work(work, prefix, tool="workdir") -> bool:
    """Remove `work` if `refusal` allows it. -> True when removed. Never raises for a refusal or a failed removal."""
    why = refusal(work, prefix)
    if why is not None:
        _say(tool, f"REFUSED to remove the work folder {os.fspath(work)!r}: {why}; nothing was deleted")
        return False

    def writable(func, path, _exc):  # make the entry and its folder writable, then retry once
        try:
            os.chmod(os.path.dirname(path), 0o700)
            os.chmod(path, 0o700)
        except OSError:
            pass
        func(path)

    kw = {"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}
    try:
        shutil.rmtree(work, **kw)
    except OSError as e:
        _say(tool, f"WARNING: could not remove the work folder {os.fspath(work)!r}: {type(e).__name__}: {e}")
        return False
    return True


def run_in_work_dir(prefix, tool, fn, keep=False):
    """Make a work folder, call fn(work) and return its result; remove the folder on every exit unless asked to keep it."""
    work = tempfile.mkdtemp(prefix=prefix)
    try:
        return fn(work)
    finally:
        if keep or os.environ.get(KEEP_ENV) == "1":
            why = "--keep" if keep else f"{KEEP_ENV}=1"
            print(f"[{tool}] work folder kept ({why}): {work}", flush=True)
        else:
            remove_work(work, prefix, tool)
