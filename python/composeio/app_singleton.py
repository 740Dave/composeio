#!/usr/bin/env python3
"""Per-APP surface singleton — exactly one session-owning window per `app: id:`
([SPEC:CXC-IC-SINGLETON] [CXC-IC-SURFACE-SINGLETON]).

THE DEFECT THIS CLOSES (DEFECT-TWO-CONSOLES-SPAWNED-THEN-KILLED, David 2026-08-15:
"some process is, on some random basis, firing up two copies of the IC and then
killing them shortly thereafter").

[CXC-IC-SINGLETON] made every console DAEMON a host-flock singleton and argued, in
its own reasoning, that "singleton-ness must be a PROPERTY of the daemon, not a
matter of remembering to run one copy."  It then enumerated the publishers, monitors
and relays — and stopped at the SURFACE.  The console window itself, the process
that hosts all of those daemons as in-process threads, had no singleton at all.

What stood in its place was [CXC-LB-AUTOSTART]'s "pgrep guard" in
`compose_projects/intelligence/start-console-stack.sh`.  A pgrep guard fails in TWO ways that a lock
cannot, and both were measured:

  * It is ADVISORY.  It binds the ONE launch path that runs it.  A hand
    `runtime --transports`, `console_supervisor.ConsoleSupervisor.start()` (whose
    `is_running()` inspects only its own child), and the bounce runbook's relaunch
    after a kill that did not take, all spawn a second surface beside a live one.
  * It answers a question it cannot answer.  `if ! pgrep …` reads EVERY nonzero exit
    as "nothing is running", including the exit that means "I could not tell".
    David's 2026-08-15 preflight `pgrep -fc …` (`-c` is a Linux flag macOS pgrep
    rejects) reported nothing while two surfaces were live, and a third was launched
    on top of them.

WHY THIS FILE LOOKS LIKE `app_topics.py`.  It is the same move, one seam over.  That
module removed a hardcoded `_app/console/bbox` by DERIVING the topic from the scene's
declared `app: id:`; this derives the singleton KEY from the same field.  Console,
ribbon, doc viewer and hookup viewer therefore key differently and can never contend,
and no new mechanism is introduced.

NO SECOND FLOCK.  The lock itself is `compose_projects/intelligence/publisher/singleton.py ::
acquire_singleton` — the ONE flock call site in the repo, asserted by
`compose_projects/intelligence/publisher/test_singleton_hardening.test_exactly_one_flock_call_site`.
(That test greps for the literal call token, so this prose deliberately does not
spell it — the same avoidance `singleton.py`'s own docstring practises.)
This module holds NO lock primitive; it resolves the key and delegates.  It loads
that module BY FILE PATH because the render engine keeps no import dependency on the
intelligence tree — the same discipline as `runtime._maybe_start_inprocess_sources`
and `runtime._maybe_apply_console_personalization`.

YIELD IS THE DEFAULT; SUPERSEDE IS EXPLICIT.  An accidental second launch must leave
the running console ALONE — reaping the incumbent by default would turn every stray
bringup into a kill of the console David is working in, which is strictly worse than
the defect.  A DELIBERATE bounce passes `supersede=True` and wins by reaping the
lock file's recorded holder pid — an owner-scoped reap, unlike the runbook's
`pgrep -f console.compose.yaml | kill -9`, which cannot tell one operator's console
from another's and is the "…and killing them shortly thereafter" half of the report.
"""
from __future__ import annotations

import os
import sys

__all__ = ["surface_singleton_key", "acquire_surface_singleton",
           "SURFACE_KEY_PREFIX"]

# Namespaced so a surface key can never collide with a DAEMON role key
# ([CXC-IC-SINGLETON] keying: bare role, or `<role>-<id>`).  `composeio-surface-
# intelligence-console.lock` sits beside `composeio-self-publisher.lock` and means
# something different by construction.
SURFACE_KEY_PREFIX = "surface"


def surface_singleton_key(app_id) -> str:
    """The singleton role key for the surface running the scene whose `app: id:` is
    *app_id*.

    An empty/blank app id falls back to `"app"` rather than to `"console"` — the
    same fail-safe as `app_topics.app_bbox_ns`: a nameless scene must not be able to
    claim the console's key and lock the real console out of its own window.
    """
    aid = str(app_id or "").strip() or "app"
    return f"{SURFACE_KEY_PREFIX}-{aid}"


def _singleton_module():
    """`compose_projects/intelligence/publisher/singleton.py`, loaded by FILE PATH.

    The render engine package does not import the intelligence tree (see
    `runtime._maybe_start_inprocess_sources`), and this must not become a second
    copy of the flock.  Returns None when the intelligence tree is absent — the
    render engine reused elsewhere still runs, it simply has no host lock, exactly
    as `acquire_singleton` itself degrades on a platform without `fcntl`.
    """
    import importlib.util
    root = os.environ.get("COMPOSEIO_REPO_ROOT") or os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    path = os.path.join(root, "compose_projects", "intelligence", "publisher", "singleton.py")
    if not os.path.isfile(path):
        return None
    name = "composeio_publisher_singleton"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def acquire_surface_singleton(app_id, *, supersede: bool = False,
                              lock_dir: str | None = None):
    """Become THE surface for *app_id*, or return None to say another one is live.

    Returns the held lock file object on success — **KEEP A REFERENCE for the
    process lifetime**; the lock releases when the object is closed or the process
    dies (kernel-guaranteed, so a crashed surface never blocks its successor).

    `supersede=False` (default) YIELDS to a live incumbent: the caller must exit
    WITHOUT opening a window.  `supersede=True` reaps the recorded holder and takes
    over — for a deliberate bounce only, per the module docstring.

    Never raises.  A missing intelligence tree, or a platform without `fcntl`,
    degrades to "acquired" so the surface still launches: this node removes a
    duplicate window, it must never become a new reason a console cannot start.
    """
    mod = _singleton_module()
    if mod is None:
        return open(os.devnull, "w")            # no host lock available — proceed
    key = surface_singleton_key(app_id)
    acquire = (mod.acquire_singleton_superseding if supersede
               else mod.acquire_singleton)
    try:
        return acquire(key, lock_dir)
    except Exception:                            # a lock fault must not block a launch
        return open(os.devnull, "w")
