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

SIGNALS. SIGTERM (systemd stop) and SIGINT (Ctrl-C) both call ``srv.stop()``
once and unblock — a clean teardown that shuts every plugin's HTTP face and
detaches every connector, so no port is left bound.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
from pathlib import Path

from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import LoaderResult, load_tenant_manifest

logger = logging.getLogger("composeio.composeserver_host")

DEFAULT_TENANT = "default"


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
    parser.add_argument("manifest", help="path to the JSON tenant manifest")
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
    stop_event.wait()
    srv.stop()
    logger.info("ComposeServer host stopped cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
