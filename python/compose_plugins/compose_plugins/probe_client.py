"""Running the one-shot probe child and reading its verdict SAFELY.

The probe (``compose_plugins._probe``) imports a plug-in class in a FRESH child
process and reports its derived declaration and config-validation result. It is
used at enable (subprocess level, so the host never imports plug-in code) and
by ``compose-plugin build`` (so a build never reuses a module a previous build
loaded in the same process).

The verdict travels on a DEDICATED pipe fd passed with ``pass_fds``, never on
stdout or stderr, which plug-in code writes freely (an ``atexit`` print can no
longer forge it). The host sends a random nonce on stdin; the probe reads it
BEFORE importing anything from the plug-in and echoes it in the verdict. The
host accepts EXACTLY ONE JSON object carrying the nonce, and refuses anything
else (no verdict, extra writes, a wrong nonce).

Residual risk, stated plainly: plug-in code runs inside the probe, so a plug-in
written to defeat the probe can still find the fd and nonce in its own process
and forge a verdict. The drift check is an integrity check on an honestly built
package, not a sandbox; the runtime boundary (declared publishes enforced by the
host on every frame) does not depend on it.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys

from .errors import PluginContractRefusal


class ProbeRefused(PluginContractRefusal):
    """The probe did not return exactly one valid verdict."""


def run_probe(entry: str, sys_path: "list[str]", config, *, what: str,
              timeout: float = 60.0) -> dict:
    nonce = secrets.token_hex(16)
    r_fd, w_fd = os.pipe()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "compose_plugins._probe", "--fd", str(w_fd)],
            pass_fds=(w_fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, close_fds=True)
    except BaseException:
        os.close(r_fd)
        os.close(w_fd)
        raise
    os.close(w_fd)        # the child holds the only write end now
    try:
        _out, err = proc.communicate(json.dumps({"entry": entry, "sys_path": sys_path,
                                                 "config": config, "nonce": nonce}),
                                     timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        os.close(r_fd)
        raise ProbeRefused(f"{what} REFUSED: the probe did not finish within {timeout}s")
    with os.fdopen(r_fd, "rb") as fh:
        raw = fh.read(1 << 20)
    if proc.returncode != 0:
        raise ProbeRefused(f"{what} REFUSED: probe exit {proc.returncode}: "
                           f"{(err or '').strip()[-300:]}")
    lines = [ln for ln in raw.split(b"\n") if ln.strip()]
    if len(lines) != 1:
        raise ProbeRefused(f"{what} REFUSED: the probe's verdict channel held "
                           f"{len(lines)} objects, not exactly one")
    try:
        verdict = json.loads(lines[0])
    except ValueError as exc:
        raise ProbeRefused(f"{what} REFUSED: the probe's verdict is not JSON: {exc}")
    if not isinstance(verdict, dict) or verdict.get("nonce") != nonce:
        raise ProbeRefused(f"{what} REFUSED: the probe's verdict does not carry this "
                           "run's nonce")
    return verdict
