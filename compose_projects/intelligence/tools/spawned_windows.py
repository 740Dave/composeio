#!/usr/bin/env python3
"""spawned_windows — the ONE window-lifecycle tracker every launcher daemon
reuses, so a relaunch never LEAKS a process ([SPEC:CXC-RETRO §5c
CXC-RETRO-AWAYWATCH-SINGLETON], [DX-NARROW-WAIST] reuse-by-reference).

The defect class this closes (David 2026-07-23 overnight): the Away/Night Watch
launch leg spawned a fresh window process on every roster click and tracked
NOTHING, so 8 windows leaked overnight — and since [cdf1217] gave that window an
outbound MQTT transport, every leaked window is a live bus participant, i.e. the
duplicates-coexist-silently / message-doubling hazard. The SIBLING launcher
(doc_viewer_launcher) already had lifecycle tracking (a `path -> proc` map + a
reap + same-path dedupe); the away-watch launcher was FORKED without it. This
module is that lifecycle logic extracted ONCE so BOTH launchers hold it by
reference — a fix to one can never again miss the other.

TWO collision policies, one tracker:

  * ``SKIP`` — a launch whose key is already live YIELDS (return the incumbent,
    spawn nothing). The multi-window doc viewer keys on the doc PATH: N distinct
    docs => N windows, but the SAME doc twice does not pile up (SDL2 gives no
    cross-process focus, so skip-while-alive is the honest behavior).

  * ``SUPERSEDE`` — a launch whose key is already live REPLACES the incumbent:
    spawn the replacement FIRST, then reap the predecessor (never leave zero
    windows if the spawn is refused). The single-instance Night Watch window
    keys on a CONSTANT role: every roster click resolves to ONE window that
    re-renders the clicked entry, and the prior window is reaped — at most one
    nightwatch process is ever live. (Plain flock-YIELD singleton semantics are
    WRONG here: a yielding new process would leave the OLD entry showing and
    make new clicks inert — the opposite of "the relaunched window shows the
    CLICKED entry", [CXC-RETRO-AWAYWATCH-RELAUNCH].)

Supersede is safe for the Night Watch window specifically because it is a
READ-ONLY projection of the retrospective SQLite store — it owns no unsaved
state, so reaping the predecessor loses nothing. A launcher whose windows CAN
hold unsaved state must not use SUPERSEDE.

A "process handle" is anything with ``.poll()`` (alive iff it returns None);
SUPERSEDE also uses ``.terminate()`` / ``.wait()`` / ``.kill()`` when present.
This keeps the tracker testable with a fake proc and unentangled from paho/skia.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

SKIP = "skip"
SUPERSEDE = "supersede"


def _alive(proc: Any) -> bool:
    """True iff *proc* is a live process handle (``poll()`` returns None). A
    None handle, or one without ``poll()``, reads as not-alive (mirrors the
    reap semantics the two launchers already used)."""
    if proc is None:
        return False
    poll = getattr(proc, "poll", None)
    if not callable(poll):
        return False
    return poll() is None


def _terminate(proc: Any) -> None:
    """Best-effort reap of a superseded window: SIGTERM (``terminate``), a short
    bounded wait so the common fast-exit case leaves no zombie, escalating to
    ``kill`` if it lingers. Never raises — a launch handler must not die because
    the predecessor was already gone."""
    term = getattr(proc, "terminate", None)
    if not callable(term):
        return
    try:
        term()
    except (OSError, ProcessLookupError):
        return
    wait = getattr(proc, "wait", None)
    if not callable(wait):
        return
    try:
        wait(timeout=3)
    except Exception:
        kill = getattr(proc, "kill", None)
        if callable(kill):
            try:
                kill()
            except (OSError, ProcessLookupError):
                pass
        try:
            wait(timeout=1)
        except Exception:
            pass


class SpawnedWindows:
    """Tracks the windows a launcher has spawned, keyed so a relaunch cannot
    LEAK a process. Construct with a policy (``SKIP`` | ``SUPERSEDE``) and an
    optional key function (defaults to identity — the caller passes the key).

    ``register(ident, spawn)`` is the whole surface: it reaps dead handles, then
    applies the policy against any live incumbent for ``key(ident)`` and calls
    ``spawn()`` (a no-arg thunk returning a process handle, or None) at most as
    the policy dictates. Returns ``(proc, action)`` with action in
    ``{"spawned", "skipped", "superseded", "refused"}``."""

    SKIP = SKIP
    SUPERSEDE = SUPERSEDE

    def __init__(self, policy: str = SUPERSEDE,
                 key: Optional[Callable[[Any], Any]] = None) -> None:
        if policy not in (SKIP, SUPERSEDE):
            raise ValueError(f"unknown policy: {policy!r}")
        self._policy = policy
        self._key = key or (lambda x: x)
        # key -> live process handle. Public-read (`live`) so a launcher/test can
        # observe the roster; mutate only through register()/reap().
        self.live: Dict[Any, Any] = {}

    def reap(self) -> None:
        """Drop handles whose process has exited, so a closed-then-reopened key
        spawns fresh (and superseded predecessors clear their slot)."""
        for k in [k for k, p in self.live.items() if not _alive(p)]:
            self.live.pop(k, None)

    def is_open(self, ident: Any) -> bool:
        """True iff a live window is already tracked for ``key(ident)`` (after a
        reap) — the SKIP-policy pre-check a caller may want before doing spawn
        preparation work."""
        self.reap()
        return _alive(self.live.get(self._key(ident)))

    def register(self, ident: Any,
                 spawn: Callable[[], Any]) -> Tuple[Any, str]:
        """Apply the collision policy for ``key(ident)`` and spawn accordingly.

        SKIP: a live incumbent is returned as ``(incumbent, "skipped")`` and
        ``spawn`` is NOT called. SUPERSEDE: ``spawn`` is called FIRST; only if it
        yields a real handle is the predecessor reaped and replaced
        (``"superseded"``) — a refused/failed spawn (None) leaves the incumbent
        untouched (``"refused"``), so a relaunch can never end with zero windows.
        With no live incumbent, ``spawn`` runs and the result is tracked
        (``"spawned"`` / ``"refused"``)."""
        self.reap()
        k = self._key(ident)
        incumbent = self.live.get(k)
        if _alive(incumbent):
            if self._policy == SKIP:
                return incumbent, "skipped"
            proc = spawn()
            if proc is None:
                return None, "refused"        # keep the incumbent — never 0 windows
            _terminate(incumbent)
            self.live[k] = proc
            return proc, "superseded"
        proc = spawn()
        if proc is None:
            self.live.pop(k, None)
            return None, "refused"
        self.live[k] = proc
        return proc, "spawned"


# ---------------------------------------------------------------------------
# selftest — deterministic, no subprocess/network. Parametrized over BOTH
# policies so any future launcher that drops the invariant fails loudly here.
# ---------------------------------------------------------------------------
def _selftest() -> int:
    fails = []
    total = [0]

    def check(cond, msg):
        total[0] += 1
        if not cond:
            fails.append(msg)

    class FakeProc:
        _n = 0

        def __init__(self):
            FakeProc._n += 1
            self.pid = 1000 + FakeProc._n
            self._alive = True
            self.terminated = False
            self.killed = False

        def poll(self):
            return None if self._alive else 0

        def terminate(self):
            self.terminated = True
            self._alive = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True
            self._alive = False

    # ---- helpers ----
    def spawner(sink):
        def _s():
            p = FakeProc()
            sink.append(p)
            return p
        return _s

    # ---- SKIP policy (the doc-viewer multi-window case) ----
    skip = SpawnedWindows(SKIP)
    sink = []
    p1, a1 = skip.register("docs/a.md", spawner(sink))
    check(a1 == "spawned" and len(sink) == 1, "(1) SKIP first key spawns")
    p2, a2 = skip.register("docs/a.md", spawner(sink))
    check(a2 == "skipped" and p2 is p1 and len(sink) == 1,
          "(2) SKIP same-key yields the incumbent, no 2nd spawn")
    p3, a3 = skip.register("docs/b.md", spawner(sink))
    check(a3 == "spawned" and len(sink) == 2 and len(skip.live) == 2,
          "(3) SKIP distinct key spawns a second window")
    p1._alive = False                          # incumbent dies
    p4, a4 = skip.register("docs/a.md", spawner(sink))
    check(a4 == "spawned" and len(sink) == 3 and p4 is not p1,
          "(4) SKIP reopen-after-close spawns fresh (dead reaped)")

    # ---- SUPERSEDE policy (the Night Watch single-window case) ----
    role = "nightwatch-window"
    sup = SpawnedWindows(SUPERSEDE)
    sink2 = []
    q1, b1 = sup.register(role, spawner(sink2))
    check(b1 == "spawned" and len(sink2) == 1, "(5) SUPERSEDE first launch spawns")
    q2, b2 = sup.register(role, spawner(sink2))
    check(b2 == "superseded" and len(sink2) == 2, "(6) SUPERSEDE 2nd launch replaces")
    check(q1.terminated is True and q1._alive is False,
          "(7) SUPERSEDE reaps the PREDECESSOR (the leaked-window fix)")
    check(q2._alive is True, "(8) SUPERSEDE replacement is alive")
    live = [p for p in sink2 if p.poll() is None]
    check(len(live) == 1 and live[0] is q2 and len(sup.live) == 1,
          "(9) SUPERSEDE: exactly ONE window alive after two launches (no leak)")
    # eight rapid relaunches never accumulate (the exact overnight symptom)
    for _ in range(8):
        sup.register(role, spawner(sink2))
    live = [p for p in sink2 if p.poll() is None]
    check(len(live) == 1 and len(sup.live) == 1,
          "(10) SUPERSEDE: eight relaunches -> still exactly ONE alive")

    # ---- refused spawn never orphans / never leaves zero windows ----
    sup2 = SpawnedWindows(SUPERSEDE)
    r1, c1 = sup2.register(role, lambda: None)
    check(c1 == "refused" and len(sup2.live) == 0, "(11) refused first spawn tracks nothing")
    sink3 = []
    sup2.register(role, spawner(sink3))
    r2, c2 = sup2.register(role, lambda: None)      # incumbent live, spawn refuses
    check(c2 == "refused" and sink3[0]._alive is True and len(sup2.live) == 1,
          "(12) refused supersede KEEPS the incumbent (never zero windows)")

    # ---- key function is honored ----
    keyed = SpawnedWindows(SKIP, key=lambda d: d["role"])
    s4 = []
    keyed.register({"role": "x", "n": 1}, spawner(s4))
    _, ak = keyed.register({"role": "x", "n": 2}, spawner(s4))
    check(ak == "skipped" and len(s4) == 1, "(13) key() collapses distinct idents onto one slot")

    # ---- bad policy rejected ----
    try:
        SpawnedWindows("nonsense")
        check(False, "(14) bad policy rejected")
    except ValueError:
        check(True, "(14) bad policy rejected")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: spawned_windows selftest ({total[0] - len(fails)}/{total[0]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
