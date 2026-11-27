"""composeserver_host — the runnable ComposeServer PROCESS entrypoint.

    python -m composeio.composeserver_host <manifest.json>

This is the thin glue that turns the landed libraries — ``ComposeServer``
([SPEC:CXC-COMPOSESERVER]) and ``load_tenant_manifest`` ([SPEC:CXC-CS-LOADER],
`87-composeserver-plugin-loader.lts`) — into a supervised, long-lived process a
systemd unit can own. It is a pure CONSUMER: it reads a JSON manifest FILE (the
one thing the loader deliberately left OUT — the on-disk manifest FILE format
was DEFERRED as [DD-CSL-MANIFEST-FORMAT]; the loader takes an already-parsed
dict), hands the parsed dict + its ``connectors`` to ``load_tenant_manifest``,
and blocks until a signal.

LOUD, NEVER SILENT ([CXC-CS-REQUIRES-FLOOR], [CXC-VISION-EPISTEMICS]). The two
park classes are treated DIFFERENTLY because they mean different things:

  * A LOADER park (``resolve:`` a bad plugin ref, ``config:`` an invalid config)
    is a STATIC manifest-authoring error. It will NEVER self-heal on its own, so
    in the default (strict) mode it is a HARD, non-zero-exit FAILURE — the
    operator sees a failed unit start, not a tenant silently missing its store.
    This is the "refused loudly, not parked-and-green" contract.

  * A REQUIRES-FLOOR park (an unmet announced capability) is DYNAMIC — the floor
    may be met later and ComposeServer rebinds with no restart. It is logged
    LOUDLY (naming the specific unmet item) but the process keeps serving the
    healthy plugins, exactly as the platform intends.

Both are always logged AND surfaced on the ``_server/*`` announce spine by the
landed loader; this module adds the process-level exit policy on top.

HEALTH ([CXC-VISION-EPISTEMICS]: a host that answers HTTP with a dead bus is
not healthy). With ``--health-file PATH`` (or ``$COMPOSESERVER_HEALTH_FILE``) the
running host rewrites PATH every ``HEALTH_WRITE_INTERVAL_S`` with
``ComposeServer.connector_health()``. A container HEALTHCHECK then runs

    python -m composeio.composeserver_host --healthcheck

which exits 0 only if the file is fresh (at most HEALTH_MAX_AGE_S old and at
most HEALTH_FUTURE_SKEW_S in the future), every connector the manifest
DECLARES is attached (`expected_connectors`; a manifest declaring none is
healthy), and every attached connector, declared or extra, is connected. It
exits 1 otherwise (down, declared connector missing, stale or future-dated,
missing, unreadable, malformed) and 2 if no health file is configured. A transport reconnects by itself; the check is what makes the
outage VISIBLE while it lasts.

SIGNALS. SIGTERM (systemd stop) and SIGINT (Ctrl-C) both call ``srv.stop()``
once and unblock — a clean teardown that shuts every plugin's HTTP face and
detaches every connector, so no port is left bound.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import sys
import threading
import time
from pathlib import Path

from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import LoaderResult, load_tenant_manifest

logger = logging.getLogger("composeio.composeserver_host")

DEFAULT_TENANT = "default"

#: environment variable naming the health file; the same value serves the
#: writer (the running host) and the reader (``--healthcheck``).
HEALTH_FILE_ENV = "COMPOSESERVER_HEALTH_FILE"
#: how often the running host rewrites the health file.
HEALTH_WRITE_INTERVAL_S = 5.0
#: a health file older than this means the host is hung or gone.
HEALTH_MAX_AGE_S = 30.0


def write_health_file(srv: ComposeServer, path: "str | Path") -> dict:
    """Write ``srv.connector_health()`` plus a timestamp, atomically."""
    path = Path(path)
    data = dict(srv.connector_health())
    data["written_at"] = time.time()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, path)
    return data


#: a written_at further than this in the FUTURE is stale too: the wall clock
#: stepped back, so the file's age says nothing.
HEALTH_FUTURE_SKEW_S = 5.0


def _health_shape_error(data) -> "str | None":
    """Why `data` is not a health file this host wrote, or None."""
    if not isinstance(data, dict):
        return f"top level is {type(data).__name__}, not an object"
    w = data.get("written_at")
    if (isinstance(w, bool) or not isinstance(w, (int, float))
            or not math.isfinite(w)):
        # NaN would make every age comparison False and read as FRESH
        return f"written_at is {w!r}, not a finite number"
    e = data.get("expected_connectors")
    if isinstance(e, bool) or not isinstance(e, int) or e < 0:
        return f"expected_connectors is {e!r}, not a count"
    for key, default in (("connectors", None), ("extra_connectors", [])):
        # `extra_connectors` absent means none (a file that predates it)
        conns = data.get(key, default)
        if not isinstance(conns, list) or not all(isinstance(c, dict)
                                                  for c in conns):
            return f"{key} is not a list of objects"
    if not isinstance(data.get("healthy"), bool):
        return "healthy is not a boolean"
    return None


def _cannot_read(exc: BaseException) -> "tuple[int, str]":
    # The class name ONLY: the file's content (or a fragment of it echoed in
    # an exception message) never reaches the HEALTHCHECK output.
    return 1, f"unhealthy: cannot read health file ({type(exc).__name__})"


def check_health_file(path: "str | Path",
                      max_age_s: float = HEALTH_MAX_AGE_S) -> "tuple[int, str]":
    """(exit code, reason) for a HEALTHCHECK. 0 healthy, 1 not. Never raises
    for a bad file: every way a file can be wrong is an explicit exit 1.

    The final `except Exception` is the backstop for inputs the specific
    checks do not anticipate, measured: a 401-digit integer `written_at`
    (OverflowError converting it to float) and 100,000 nested `[`
    (RecursionError inside json.loads).
    """
    try:
        return _check_health_file(path, max_age_s)
    except Exception as exc:  # noqa: BLE001 — a health check must answer, not crash
        return _cannot_read(exc)


def _check_health_file(path: "str | Path", max_age_s: float) -> "tuple[int, str]":
    path = Path(path)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return 1, f"unhealthy: no health file at {path} (host not serving)"
    except (OSError, ValueError) as exc:
        return 1, f"unhealthy: health file unreadable: {exc}"
    shape = _health_shape_error(data)
    if shape:
        return 1, f"unhealthy: malformed health file: {shape}"
    age = time.time() - float(data["written_at"])
    if age < -HEALTH_FUTURE_SKEW_S:
        return 1, (f"unhealthy: health file timestamp is {-age:.0f}s in the "
                   f"future (clock stepped back?); treating it as stale")
    if age > max_age_s:
        return 1, f"unhealthy: health file is {age:.0f}s old (> {max_age_s:.0f}s)"
    declared = data["connectors"]
    extras = data.get("extra_connectors", [])
    expected = data["expected_connectors"]
    down = [(c, "") for c in declared if c.get("connected") is not True]
    down += [(c, "extra ") for c in extras if c.get("connected") is not True]
    if down:
        names = ", ".join(
            f"{c.get('tenant')}/{c.get('scope')} {tag}connector down"
            f"{' (' + str(c['broker']) + ')' if c.get('broker') else ''}"
            for c, tag in down)
        return 1, f"unhealthy: {names}"
    if len(declared) < expected:
        return 1, (f"unhealthy: {len(declared)} declared connector(s) "
                   f"attached, expected {expected} (declared by the manifest)")
    if not data["healthy"]:
        return 1, "unhealthy: healthy=false"
    more = f" + {len(extras)} extra" if extras else ""
    return 0, (f"healthy: {len(declared)} of {expected} expected connector(s)"
               f"{more} up")


class ManifestError(Exception):
    """The manifest file could not be read or is structurally invalid — raised
    BEFORE any tenant is stood up, so a malformed file fails fast and loud."""


def load_manifest_file(path: "str | Path") -> dict:
    """Read + json-parse a manifest FILE into the dict the loader consumes.

    Every failure is a named ``ManifestError`` — never a bare
    ``FileNotFoundError``/``JSONDecodeError`` escaping to a stack trace with no
    context about which file the operator pointed us at.
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"cannot read manifest file {str(p)!r}: {exc}") from exc
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest {str(p)!r} is not valid JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ManifestError(
            f"manifest {str(p)!r} must be a JSON object, got {type(manifest).__name__}")
    if not isinstance(manifest.get("plugins", []), list):
        raise ManifestError(
            f"manifest {str(p)!r} `plugins` must be a list when present")
    if not isinstance(manifest.get("connectors", []), list):
        raise ManifestError(
            f"manifest {str(p)!r} `connectors` must be a list when present")
    return manifest


def _report(tenant_id: str, result: LoaderResult) -> None:
    """Log the load outcome LOUDLY — loaded, requires-floor parks, loader parks."""
    loaded = [p.name for p in result.standup.loaded]
    logger.info("tenant %r: %d plugin(s) LOADED: %s",
                tenant_id, len(loaded), ", ".join(loaded) or "(none)")
    for name in result.standup.subprocess_pending:
        logger.info("tenant %r: plugin %r SUBPROCESS-PENDING (isolation floor; "
                    "spawning is Phase 1)", tenant_id, name)
    for name, reasons in result.standup.parked.items():
        logger.warning("tenant %r: plugin %r PARKED (requires-floor, may rebind) "
                       "— unmet: %s", tenant_id, name, "; ".join(reasons))
    for key, reasons in result.config_parked.items():
        logger.error("tenant %r: manifest entry %r PARKED (static authoring "
                     "error) — %s", tenant_id, key, "; ".join(reasons))


def run(manifest_path: "str | Path", *, strict: bool = True
        ) -> "tuple[ComposeServer, LoaderResult]":
    """Load one manifest FILE and stand up its tenant (started).

    Returns ``(server, result)``. In ``strict`` mode a LOADER park (resolve:/
    config:) means the returned server is already stopped and the caller must
    treat it as a failed start; the requires-floor parks never fail the start.
    """
    manifest = load_manifest_file(manifest_path)
    tenant_id = str(manifest.get("tenant", DEFAULT_TENANT))
    connectors = manifest.get("connectors", []) or []

    srv = ComposeServer()
    result = load_tenant_manifest(
        srv, tenant_id, manifest, connectors=connectors, start=True)
    _report(tenant_id, result)

    if strict and result.config_parked:
        logger.error(
            "tenant %r: %d static manifest error(s) — refusing to run degraded "
            "(strict). Fix the manifest and restart.",
            tenant_id, len(result.config_parked))
        srv.stop()
    return srv, result


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        prog="composeio.composeserver_host",
        description="Run a ComposeServer tenant from a JSON manifest file.")
    parser.add_argument("manifest", nargs="?",
                        help="path to the JSON tenant manifest")
    parser.add_argument(
        "--health-file", default=os.environ.get(HEALTH_FILE_ENV),
        help="write (when serving) or read (with --healthcheck) connector "
             f"health at this path (default: ${HEALTH_FILE_ENV}).")
    parser.add_argument(
        "--healthcheck", action="store_true",
        help="read the health file and EXIT: 0 iff it is fresh (no older than "
             "--health-max-age, and at most "
             f"{HEALTH_FUTURE_SKEW_S:.0f} s in the future, a tolerance for "
             "clock skew), every connector the manifest declares is attached, "
             "and every attached connector (declared or extra) is connected; "
             "1 otherwise. For a container HEALTHCHECK.")
    parser.add_argument(
        "--health-max-age", type=float, default=HEALTH_MAX_AGE_S,
        help="with --healthcheck: seconds after which the file counts as stale.")
    parser.add_argument(
        "--check", action="store_true",
        help="load, report, and EXIT without blocking (validation / CI). "
             "Exit 0 iff every plugin loaded with no static manifest error.")
    parser.add_argument(
        "--no-strict", dest="strict", action="store_false",
        help="do NOT fail-fast on a static manifest park; keep serving the "
             "healthy plugins (default is strict fail-fast).")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    if args.healthcheck:
        if not args.health_file:
            print(f"unhealthy: no health file configured (--health-file or "
                  f"${HEALTH_FILE_ENV})")
            return 2
        try:
            code, reason = check_health_file(args.health_file,
                                             args.health_max_age)
        except Exception as exc:  # noqa: BLE001 — never a traceback here
            code, reason = _cannot_read(exc)
        print(reason)
        return code
    if not args.manifest:
        parser.error("the manifest argument is required")

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Install signal handlers BEFORE run() starts the plugins' HTTP faces. A
    # `docker stop` (or a systemd stop) that arrives mid-startup must never fall
    # through to the DEFAULT SIGTERM disposition (which terminates the process
    # with exit 143 and leaves ports bound). The handler ONLY sets an event — the
    # actual srv.stop() runs on the main thread after run() returns and the wait
    # unblocks, so a signal during startup is remembered, not lost.
    stop_event = threading.Event()

    def _handle(signum, _frame):
        logger.info("signal %s received — stopping ComposeServer",
                    signal.Signals(signum).name)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    try:
        srv, result = run(args.manifest, strict=args.strict)
    except ManifestError as exc:
        logger.error("%s", exc)
        return 2

    failed = args.strict and bool(result.config_parked)

    if args.check:
        srv.stop()
        clean = result.is_clean()
        logger.info("check: %s", "CLEAN" if clean else "NOT clean")
        return 0 if clean else 3

    if failed:
        return 3  # strict start refused; systemd/Docker restart policy will retry

    # A signal that arrived DURING run() already set stop_event; wait() then
    # returns at once. Either way the main thread does the single srv.stop().
    logger.info("ComposeServer host READY — blocking (SIGTERM/SIGINT to stop).")
    if args.health_file:
        logger.info("writing connector health to %s every %.0fs",
                    args.health_file, HEALTH_WRITE_INTERVAL_S)
        while True:
            try:
                write_health_file(srv, args.health_file)
            except Exception as exc:  # noqa: BLE001 — a write error must not kill serving
                logger.error("health file write failed: %s", exc)
            if stop_event.wait(HEALTH_WRITE_INTERVAL_S):
                break
        try:
            os.remove(args.health_file)   # a stopped host is not healthy
        except OSError:
            pass
    else:
        stop_event.wait()
    srv.stop()
    logger.info("ComposeServer host stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
