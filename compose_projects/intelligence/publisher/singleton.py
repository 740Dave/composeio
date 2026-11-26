#!/usr/bin/env python3
"""singleton — the ONE host-level flock singleton every long-running console daemon uses.

[SPEC:CXC-IC-SINGLETON] (generalized from [SPEC:CXC-IC-CHANNEL] IC-C15).

The defect class this closes: a daemon's MQTT client-id was PID-suffixed
(`<role>-<os.getpid()>`), so two instances of the SAME daemon got DIFFERENT
client-ids and COEXISTED on the broker instead of colliding — each
publishing/relaying, DOUBLING the output (David's message reaching the
Intelligence twice; every thread bubble rendered twice). channel_bridge was
hardened with a per-`<id>` flock; this module is THAT implementation, extracted
so every daemon reuses it BY REFERENCE.

Why one shared module and not a copy per daemon: a copy in ~15 files is the exact
fork-divergence [DEC-SUBAGENT-CHIP-FORK-DIVERGENCE] warns against — a fix that
must be re-applied 15x is a fix that will drift. So this holds the ONLY host-lock
call site in the repo (grep-provable, asserted by
``compose_projects/intelligence/publisher/test_singleton_hardening.py``).

``acquire_singleton(name)`` is a NON-BLOCKING exclusive host lock keyed on the
daemon's ROLE. Whoever holds it is THE instance; any second instance — from ANY
launch path (a standalone process, an in-process console daemon-thread, or the
headless fallback) — gets ``None`` and must yield. flock is per
open-file-description, so a second acquire fails even from another THREAD of the
same process — which is exactly the in-process console case a broker rule and a
`pkill` sweep both miss.
"""
from __future__ import annotations

import os
import tempfile


def singleton_lock_path(name: str, lock_dir: str | None = None) -> str:
    """The host lock file for the daemon role ``name``. Honors
    ``$COMPOSEIO_LOCK_DIR`` (test / multi-tenant override), else the system
    tempdir. Filename: ``composeio-<name>.lock``."""
    d = lock_dir or os.environ.get("COMPOSEIO_LOCK_DIR") or tempfile.gettempdir()
    return os.path.join(d, f"composeio-{name}.lock")


def _flock_exclusive_nb(fh) -> bool:
    """THE one host-lock primitive: try a NON-BLOCKING exclusive ``flock`` on ``fh``.
    Returns ``True`` if we acquired it, ``False`` if another live holder has it.

    This is the SINGLE ``fcntl.flock`` CALL SITE in the whole repo (IC-C17,
    grep-proven by ``test_exactly_one_flock_call_site`` — which is why the prose here
    avoids the literal open-paren token it counts): ACQUIRE, RECLAIM, PROBE and
    SUPERSEDE all funnel through here so the lock semantics never fork. There is NO
    matching flock UNLOCK call anywhere — RELEASE is done by CLOSING the fh (the
    kernel frees the flock on close / process death), which is why a second
    flock-for-unlock call would break the single-call-site invariant. Callers on a
    non-POSIX platform (no ``fcntl``) must not reach here."""
    import fcntl
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False                           # a live holder has it


def _recorded_pid(path: str) -> int | None:
    """The pid a lock file records (its holder wrote it), or None if unreadable."""
    try:
        raw = open(path).read().strip()
    except OSError:
        return None
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _pid_alive(pid: int | None) -> bool:
    """Best-effort 'is this pid a live process?' — the non-POSIX probe fallback."""
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                            # exists, just not ours to signal
    except OSError:
        return False
    return True


def acquire_singleton(name: str, lock_dir: str | None = None):
    """Try to become THE instance for role ``name``. Returns the held lock file
    object on success (KEEP A REFERENCE for the process/thread lifetime — the
    lock releases when the fh is closed or the process dies) or ``None`` if
    another instance already owns it (the caller must then yield).

    Uses a non-blocking exclusive ``flock`` (the shared ``_flock_exclusive_nb``
    primitive — the ONE call site); on a platform without ``fcntl`` (non-POSIX) it
    degrades to always-acquire so the daemon still runs (the broker-level stable
    client-id then remains the sole guard). Writes the pid into the lock file for
    operator visibility."""
    try:
        import fcntl  # noqa: F401 — presence check for the non-POSIX degrade
    except ImportError:                       # non-POSIX: no host lock available
        return open(os.devnull, "w")
    path = singleton_lock_path(name, lock_dir)
    # "a+" (create, NEVER truncate on open): a FAILED acquire must leave the
    # holder's pid intact in the file so a superseding caller can read + reap it.
    # A "w" open truncated it on every contended attempt — the pid vanished and
    # SUPERSEDE had nobody to signal.
    fh = open(path, "a+")
    if not _flock_exclusive_nb(fh):
        fh.close()
        return None                            # another instance holds it — yield
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
    except OSError:
        pass
    return fh


def acquire_singleton_reclaiming(name: str, lock_dir: str | None = None,
                                 poll_s: float = 5.0, should_stop=None,
                                 on_yield=None):
    """RECLAIM-ON-RELEASE variant ([SPEC:CXC-IC-SINGLETON] publisher bounce-safety,
    IC-C27 2026-08-05, after DEFECT-CHANNEL-OFFLINE-CONSOLE-CHANNEL-BRIDGE): for a
    console PUBLISHER (yield-if-held) that must SURVIVE A BOUNCE.

    The plain ``acquire_singleton`` is a ONE-SHOT: a daemon that loses the boot
    race (the fresh console's relay racing the DYING old console's relay across a
    /bounce-console) yields and NEVER re-attempts — so when the predecessor finally
    dies and the flock FREES, the successor has already given up and the feed stays
    DARK though nothing owns the lock. This closes that gap WITHOUT reaping a live
    incumbent (that is SUPERSEDE, wrong for a healthy publisher you must not kill):
    it WAITS — re-attempting the SAME non-blocking ``acquire_singleton`` every
    ``poll_s`` — and returns the held fh the MOMENT the incumbent releases (flock
    releases on the holder's death, kernel-guaranteed; no LWT/retained message).
    While the incumbent lives it never relays (anti-doubling, IC-C15/IC-C17 intact).

    Reuses the ONE ``acquire_singleton`` (hence the single shared lock call site) —
    this is a retry loop, not a second lock implementation. ``on_yield()`` (optional)
    fires ONCE, the first time it must wait (so the log says "yielding" once, not
    every poll). ``should_stop()`` (optional) breaks the wait and returns ``None``
    (a stop signal / test hook); absent, it waits until it can reclaim. Returns the
    held lock fh (KEEP A REFERENCE — see ``acquire_singleton``)."""
    import time
    fh = acquire_singleton(name, lock_dir)
    if fh is not None:
        return fh                                  # won it outright — no wait
    if on_yield is not None:
        on_yield()                                 # log the yield ONCE
    while should_stop is None or not should_stop():
        time.sleep(poll_s)
        fh = acquire_singleton(name, lock_dir)
        if fh is not None:
            return fh                              # incumbent released — RECLAIMED
    return None                                    # stop signalled before reclaim


def acquire_singleton_superseding(name: str, lock_dir: str | None = None,
                                  grace_s: float = 5.0, poll_s: float = 0.1):
    """SUPERSEDE variant ([SPEC:CXC-IC-SINGLETON] launcher case, D5): for a
    single-instance daemon whose NEWEST launch must WIN — the launcher / PTY
    proxy, where a resume/restart INTENDS the fresh instance to re-host and a
    plain yield-if-held (``acquire_singleton``) would make the new launcher exit
    and SILENTLY fail the restart.

    Try to acquire; if another instance holds the lock, read its pid, SIGTERM it,
    and poll until it releases (up to ``grace_s``); escalate to SIGKILL if it will
    not exit. Returns the held lock fh once acquired, or ``None`` if it could not
    be (holder pid unreadable, or not ours to signal).

    Use ONLY where superseding an incumbent is the intended semantic. For a
    console PUBLISHER the incumbent should keep serving — use ``acquire_singleton``
    (yield-if-held) there, NOT this. flock releases on process death, so a crashed
    predecessor never blocks; this handles the LIVE-overlap case a bare flock
    can't (the new launcher started before the old finished tearing down)."""
    fh = acquire_singleton(name, lock_dir)
    if fh is not None:
        return fh                                  # no predecessor — clean acquire
    import time
    import signal
    path = singleton_lock_path(name, lock_dir)
    try:
        holder_pid = int((open(path).read().strip() or "0"))
    except (OSError, ValueError):
        holder_pid = 0
    if holder_pid <= 0 or holder_pid == os.getpid():
        return acquire_singleton(name, lock_dir)   # no distinct holder to reap
    for sig, window in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 1.0)):
        try:
            os.kill(holder_pid, sig)
        except ProcessLookupError:
            pass                                   # already gone — fall to re-acquire
        except PermissionError:
            return None                            # not ours to reap
        deadline = time.time() + window
        while time.time() < deadline:
            fh = acquire_singleton(name, lock_dir)
            if fh is not None:
                return fh
            time.sleep(poll_s)
    return acquire_singleton(name, lock_dir)       # final try after SIGKILL


def probe_singleton_path(path: str) -> tuple[bool | None, int | None]:
    """NON-DESTRUCTIVE liveness probe of a singleton lock FILE — the shared
    primitive the daemon-liveness watcher (`daemon_watch.probe_flock`) reuses so it
    no longer hand-rolls its own ``fcntl.flock`` (restoring the single-call-site
    invariant, IC-C17). Returns ``(held_by_a_live_process, recorded_pid)``.

    Reuses ``_flock_exclusive_nb`` (the ONE flock call site): a TRY-acquire that
    SUCCEEDS proves nobody was holding it (DOWN) — we release AT ONCE by closing the
    fh (no ``LOCK_UN`` call, so no second flock site); a try-acquire that FAILS means
    a live holder has it (UP). NEVER disturbs a real holder — we only ever hold it in
    the instant it was free. The file's absence is DOWN. On a non-POSIX platform (no
    ``fcntl``) it degrades to a pid-alive check on the recorded pid, exactly as the
    old hand-rolled probe did."""
    pid = _recorded_pid(path)
    try:
        import fcntl  # noqa: F401 — presence check for the non-POSIX degrade
    except ImportError:
        return (_pid_alive(pid) if pid else False), pid
    try:
        fh = open(path, "r+")                       # r+ : never create, never truncate
    except OSError:
        return False, None                          # no lock file -> down
    try:
        if _flock_exclusive_nb(fh):
            return False, pid                        # WE took it -> nobody held it -> down
        return True, pid                             # a live holder has it -> up
    finally:
        fh.close()                                   # release (if held) / just close
