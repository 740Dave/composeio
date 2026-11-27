#!/usr/bin/env python3
"""stack_member_probe: is a console-stack member UP? Asked of the member's OWN lock.

Usage:
    python3 -m composeio.stack_member_probe --scene <scene.compose.yaml>   # a surface
    python3 -m composeio.stack_member_probe --role <singleton role>         # a daemon

Exit status follows pgrep's contract, so the bringup's `surface_absent` keeps its shape:
    0  the member's lock is HELD by a live process (UP). stdout: the recorded holder pid, or `?`.
    1  ABSENT: the lock file is missing from a usable lock dir, or present with its flock free.
    2  the probe could not answer. The reason goes to stderr. This covers a lock dir that is
       missing or inaccessible, a lock file that exists but cannot be opened, and a scene that
       declares no host lock. See classify(): an unreadable lock must never read as absent.

WHY A LOCK AND NOT THE PROCESS TABLE (DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E, 2026-11-26).
The bringup used to infer membership from command-line TEXT (`pgrep -f <token>`). Text
cannot tell a member from a shell that merely MENTIONS one: a caller's `zsh -c "… pgrep -f
intelligence.tools.app_launcher"` counted as a live app_launcher, so it was never launched.
An anchored pattern fixed that but then missed real members started another way (an absolute
scene path, `python3 -u -m`, `./scene`, app_launcher run as a script, an interpreter path with a
space), and a duplicate was started. Every member already holds an authoritative host lock: the
surface flock ([CXC-IC-SURFACE-SINGLETON]) for a scene, and the role singleton for a daemon.
Asking "is that lock held?" is right however the member was started, and no amount of text can
fake it.

NO SECOND LOCK MECHANISM. The scene's key is `app_singleton.surface_singleton_key(app_id)`,
the app id comes from the real scene parser, "is this scene a singleton at all" is the
runtime's own gate (`_surface_singleton_required`), and the probe is
`singleton.probe_singleton_path`, which reuses the repo's single flock call site and never
disturbs a real holder. Lock paths honour $COMPOSEIO_LOCK_DIR exactly as the members do.
"""
from __future__ import annotations

import argparse
import os
import sys


class CannotProbe(Exception):
    """The question has no lock-shaped answer (yet). Maps to exit 2."""


def _singleton():
    from composeio.app_singleton import _singleton_module
    mod = _singleton_module()
    if mod is None:
        raise CannotProbe("the intelligence tree's singleton module is absent")
    return mod


def lock_path_for_role(role: str) -> str:
    role = str(role or "").strip()
    if not role:
        raise CannotProbe("empty role")
    return _singleton().singleton_lock_path(role)


def lock_path_for_scene(scene: str) -> str:
    from composeio.app_singleton import surface_singleton_key
    from composeio.render.engine.parser import parse_compose_yaml
    from composeio.render.engine.runtime import _surface_singleton_required
    if not os.path.isfile(scene):
        raise CannotProbe(f"scene not found: {scene}")
    comp = parse_compose_yaml(scene)

    class _App:                                   # the gate reads only `.comp`
        pass
    app = _App()
    app.comp = comp
    if not _surface_singleton_required(app):
        raise CannotProbe(
            f"scene {scene!r} (app id {comp.app_id!r}) declares no host lock "
            "(design.data_sources / design.surface_singleton), so its liveness "
            "cannot be read from one")
    return _singleton().singleton_lock_path(surface_singleton_key(comp.app_id))


def classify(path: str) -> None:
    """Raise CannotProbe unless the lock at *path* is READABLE as either "absent" or "held/free".

    WHY HERE AND NOT IN singleton.probe_singleton_path (candidate review R2-F2, 2026-11-26).
    That shared primitive maps ANY failure to open the lock file to "down", and daemon_watch
    relies on that, so it stays as it is. For the bringup, "down" means LAUNCH, and a lock it
    cannot read is not a lock a member can hold either: the member then degrades to running
    UNLOCKED (app_singleton returns /dev/null by design, so a console can always start). Every
    later bringup would read "absent" again and start another unlocked copy, and none of them
    would yield. So the only conditions reported as absent are:
      * the file is missing from a lock dir the member could create it in, or
      * the file is present and openable, and its flock is free (decided by the shared probe).
    Anything else (the dir is missing, inaccessible, or not writable for a missing file; or the
    file exists but cannot be opened) is "cannot probe" (exit 2), and the guard declines."""
    d = os.path.dirname(path) or "."
    if not os.path.isdir(d):
        raise CannotProbe(f"lock dir is missing or not a directory: {d}")
    if not os.access(d, os.X_OK):
        raise CannotProbe(f"lock dir is not searchable: {d}")
    if os.path.lexists(path):
        try:
            with open(path, "r+"):                # what the member needs: read+write, no create
                pass
        except OSError as exc:
            raise CannotProbe(f"lock file exists but cannot be opened ({exc.strerror}): {path}")
    elif not os.access(d, os.W_OK):
        raise CannotProbe(f"lock dir is not writable, so no member could create its lock: {d}")


def probe(path: str):
    """(held, recorded_pid) for the lock file at *path*, after classify() has vouched for it."""
    classify(path)
    return _singleton().probe_singleton_path(path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stack_member_probe", description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scene", help="a surface scene (.compose.yaml)")
    g.add_argument("--role", help="a daemon's singleton role")
    args = ap.parse_args(argv)
    try:
        path = lock_path_for_scene(args.scene) if args.scene else lock_path_for_role(args.role)
        held, pid = probe(path)
    except CannotProbe as exc:
        sys.stderr.write(f"[stack_member_probe] cannot probe: {exc}\n")
        return 2
    except Exception as exc:                      # noqa: BLE001 (an unanswerable probe, never "absent")
        sys.stderr.write(f"[stack_member_probe] cannot probe: {type(exc).__name__}: {exc}\n")
        return 2
    if held:
        print(pid if pid else "?")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
