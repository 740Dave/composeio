"""gen_build_stamp.py — emit the build FINGERPRINT and the fingerprint->string map.

[SPEC:CXC-BOARD-HW] — workstream 0.4 of docs/PLAN-fleet-baseline-and-bus-native.md.

WHAT PROBLEM THIS SOLVES
------------------------
A classic CAN frame is EIGHT data bytes, so "hosyond_s3 Aug 13 2026 21:41:02"
cannot go on the wire.  So the wire carries a 32-bit CRC of it and the TOOLING
resolves the string — the same compact-on-the-wire / registry-elsewhere trade
the namespace hash already makes.  This script is the registry's writer.

It runs as a PlatformIO ``pre:`` extra_script (see cyd/platformio.ini) on EVERY
``pio run``, for every env, and produces two things:

  cyd/src/gen/build_stamp.h     the two #defines the firmware compiles in
  cyd/build_fingerprints.json   the append-only fingerprint -> stamp map

WHY IT REGENERATES EVERY TIME, EVEN WHEN NOTHING CHANGED
--------------------------------------------------------
The question the reflash campaign asks is "is the binary on this board the one
I just flashed?"  A stamp keyed on source content would answer "did the source
change", which is a DIFFERENT question and answers "no" in exactly the case
that matters: reflashing a board that was already running an older build of the
same source.  Keying on the BUILD EVENT makes "flash, poll, assert the
fingerprint changed" a total function.

The cost is one recompile of build_stamp.cpp (40 lines) plus a relink, per
build.  That is why the include is confined to its own TU — see announce.h.

WHY NOT ``__DATE__``/``__TIME__`` IN A NORMAL SOURCE FILE
---------------------------------------------------------
Because an incremental build does not recompile an unchanged TU, so the macro
keeps the timestamp of the last time that FILE changed.  A hand-maintained tag
read "TF13" through ~10 flashes across three boards on 2026-08-11 — including a
board that did not exist when the string was written.  A stale __DATE__ is the
same defect with the compiler holding the pen.

THE MAP IS APPEND-ONLY, AND A COLLISION IS A HARD ERROR
--------------------------------------------------------
Old entries are never rewritten: a device in the field may still be running a
build from weeks ago, and a fleet report that cannot resolve its fingerprint is
back to "unknown binary".  If two DIFFERENT stamps ever produce the same
CRC-32, that is a genuine 1-in-4-billion collision and this script REFUSES to
write rather than silently making one build resolve to the other's string.

CAN ALSO BE RUN BY HAND (e.g. to resolve a fingerprint seen on the bus):
    python3 cyd/tools/gen_build_stamp.py --env hosyond_s3 --print-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zlib
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths.  NEVER from the bare cwd: a script that silently writes its map into
# the wrong tree is worse than one that fails.
#
# ⚠ `__file__` IS NOT DEFINED HERE UNDER PLATFORMIO.  SCons loads an
# extra_script with `exec(compile(...))` and does not set `__file__`, so the
# obvious `Path(__file__).parent` raises NameError and fails the build before
# anything compiles (observed 2026-08-13, first run).  Three sources, in
# descending order of authority:
#
#   1. an explicit override — set from SCons's `$PROJECT_DIR`, which is
#      authoritative even under `pio run -d <dir>`;
#   2. `__file__`, for a human running this by hand from anywhere;
#   3. the cwd, which PlatformIO does set to the project dir — last resort,
#      and only reached if both of the above are unavailable.
# ---------------------------------------------------------------------------
_CYD_DIR_OVERRIDE: "Path | None" = None


def cyd_dir() -> Path:
    if _CYD_DIR_OVERRIDE is not None:
        return _CYD_DIR_OVERRIDE
    try:
        return Path(__file__).resolve().parent.parent
    except NameError:
        return Path.cwd().resolve()


def header_path() -> Path:
    return cyd_dir() / "src" / "gen" / "build_stamp.h"


def map_path() -> Path:
    return cyd_dir() / "build_fingerprints.json"


def format_stamp(env: str, when: time.struct_time | None = None) -> str:
    """Return "<env> <date> <time>" with date/time in ``__DATE__``/``__TIME__`` shape.

    The C macros are ``"Aug 13 2026"`` (day SPACE-padded to width 2, so the 1st
    of a month is ``"Aug  1 2026"`` with TWO spaces) and ``"21:41:02"``.  The
    shape is matched exactly so a fingerprint computed here is identical to one
    a future in-firmware CRC of the macros would produce — which keeps the door
    open to cross-checking the two without a format negotiation.
    """
    t = when or time.localtime()
    date = "%s %2d %04d" % (time.strftime("%b", t), t.tm_mday, t.tm_year)
    clock = time.strftime("%H:%M:%S", t)
    return f"{env} {date} {clock}"


def fingerprint(stamp: str) -> int:
    """CRC-32 of the stamp, as an unsigned 32-bit int.

    zlib.crc32 is the same polynomial/convention the Python tooling uses to
    resolve it, and the firmware never computes it at all — the value is baked
    in at build time.  That asymmetry is deliberate: there is no second
    implementation to drift.
    """
    return zlib.crc32(stamp.encode("utf-8")) & 0xFFFFFFFF


def load_map(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        # LOUD, and do not clobber.  A corrupt map is recoverable by hand; a
        # map this script silently replaced with a fresh one is not, and every
        # device in the field running an older build becomes unidentifiable.
        raise SystemExit(
            f"gen_build_stamp: {path} is unreadable ({e}).  Refusing to "
            f"overwrite it — fix or move it by hand.  Nothing was written."
        )
    return data if isinstance(data, dict) else {}


def record(path: Path, key: str, entry: dict) -> None:
    m = load_map(path)
    prior = m.get(key)
    if prior is not None:
        if prior.get("stamp") != entry["stamp"]:
            raise SystemExit(
                f"gen_build_stamp: CRC-32 COLLISION on {key}\n"
                f"  already recorded: {prior.get('stamp')!r}\n"
                f"  this build:       {entry['stamp']!r}\n"
                f"Two different builds would resolve to one string, so a fleet "
                f"report would name the wrong firmware.  Nothing was written.  "
                f"Rebuild (the stamp includes the clock, so a second attempt "
                f"will not collide)."
            )
        return  # identical — nothing to do
    m[key] = entry
    path.write_text(json.dumps(m, indent=2, sort_keys=True) + "\n")


HEADER_TEMPLATE = """\
/* GENERATED — DO NOT EDIT.  cyd/tools/gen_build_stamp.py rewrites this on
 * every `pio run`.  It is gitignored on purpose: it is a build ARTEFACT whose
 * whole value is being newer than the last build.
 *
 * Included by EXACTLY ONE translation unit (src/build_stamp.cpp) so that the
 * per-build recompile it forces stays cheap.  See announce.h.
 *
 *   env    {env}
 *   stamp  {stamp}
 *   crc32  0x{fp:08x}
 */
#ifndef CXC_GEN_BUILD_STAMP_H
#define CXC_GEN_BUILD_STAMP_H

#define CXC_BUILD_ENV "{env}"
#define CXC_BUILD_STAMP "{stamp}"
#define CXC_BUILD_FINGERPRINT 0x{fp:08x}u

#endif /* CXC_GEN_BUILD_STAMP_H */
"""


def generate(env: str, *, write: bool = True) -> tuple[str, int]:
    stamp = format_stamp(env)
    fp = fingerprint(stamp)
    if write:
        header = header_path()
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(HEADER_TEMPLATE.format(env=env, stamp=stamp, fp=fp))
        record(
            map_path(),
            "%08x" % fp,
            {
                "env": env,
                "stamp": stamp,
                "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime()),
            },
        )
    return stamp, fp


# ---------------------------------------------------------------------------
# PlatformIO entry point.  `Import` only exists inside a SCons extra_script;
# outside one this whole block is skipped and __main__ below runs instead.
# ---------------------------------------------------------------------------
try:
    Import("env")  # noqa: F821  (injected by SCons)
except NameError:  # not running under PlatformIO — see __main__ below
    pass
else:  # pragma: no cover - only exercised inside a pio build
    _CYD_DIR_OVERRIDE = Path(env.subst("$PROJECT_DIR")).resolve()  # noqa: F821
    _pioenv = env.subst("$PIOENV")  # noqa: F821
    _stamp, _fp = generate(_pioenv)
    print(f"gen_build_stamp: {_pioenv} -> {_stamp!r} = 0x{_fp:08x}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=os.environ.get("PIOENV", "manual"),
                    help="PlatformIO env name to stamp")
    ap.add_argument("--print-only", action="store_true",
                    help="compute and print; write nothing")
    a = ap.parse_args(argv)
    stamp, fp = generate(a.env, write=not a.print_only)
    print(f"{fp:08x}  {stamp}")
    if not a.print_only:
        print(f"  header -> {header_path()}")
        print(f"  map    -> {map_path()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
