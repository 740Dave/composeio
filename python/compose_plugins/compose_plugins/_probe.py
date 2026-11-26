"""One-shot probe, run in a FRESH CHILD by ``compose_plugins.probe_client``.

    python -m compose_plugins._probe --fd N
    stdin:  {"entry", "sys_path", "config", "nonce"}   (read BEFORE plug-in code)
    fd N:   ONE line {"nonce", "resolve_error", "config_schema", "bus", "bus_error",
                      "config_errors"}, then fd N is closed.

stdout and stderr belong to the plug-in and are never read as the verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fd", type=int, required=True)
    args = ap.parse_args()
    req = json.loads(sys.stdin.read())        # before ANY plug-in code runs
    fd, nonce = args.fd, req["nonce"]
    for p in reversed(req["sys_path"]):
        sys.path.insert(0, p)
    from .config import config_errors
    from .derive import BuildRefused, derive_bus, derive_config_schema, resolve_entry
    out: dict = {"nonce": nonce, "resolve_error": None, "config_schema": None, "bus": None,
                 "bus_error": None, "config_errors": []}
    try:
        cls = resolve_entry(req["entry"])
    except Exception as exc:
        out["resolve_error"] = f"{type(exc).__name__}: {exc}"
    else:
        out["config_schema"] = derive_config_schema(cls)
        try:
            out["bus"] = derive_bus(cls)
        except BuildRefused as exc:
            out["bus_error"] = str(exc)
        out["config_errors"] = config_errors(cls.Config, req["config"])
    data = (json.dumps(out) + "\n").encode("utf-8")
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        view = view[n:]
    os.close(fd)                              # nothing written later can reach the host
    return 0


if __name__ == "__main__":
    sys.exit(main())
