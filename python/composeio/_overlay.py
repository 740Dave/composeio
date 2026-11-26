"""The host overlay: live values for names the public tree carries only as neutral defaults.

[CXC-SCL-SCRUB-OVERLAY-PARITY]. Called from composeio/__init__.py BEFORE any submodule import,
because several submodules read the environment at their own import time. Standard library only.

File: $COMPOSEIO_OVERLAY_ENV, else ~/.config/composeio/live.env; the value `none` disables it; a
missing file is a no-op. Lines are KEY=VALUE, `#` comments, optional `export ` prefix, one pair of
matching surrounding quotes stripped, NO shell expansion. Only names absent from the environment
are set, so an explicit environment always wins. `applied` records the file and the NAMES set,
never values. A malformed line is skipped and recorded by line number; nothing here raises.
scripts/lib/overlay-env.sh applies the same rules for shell consumers.
"""
import os
import re
from collections import namedtuple

ENV_NAME = "COMPOSEIO_OVERLAY_ENV"
DEFAULT_PATH = "~/.config/composeio/live.env"
LIVE_HOST_MARKER = "COMPOSEIO_LIVE_HOST"
# The one list of names a live host must supply (env or overlay) for parity with the live tree.
OVERLAY_REQUIRED = ("COMPOSEBUS_MQTT", "COMPOSEIO_MQTT_BROKER", "COMPOSEIO_STORAGE_URL",
                    "COMPOSEIO_SECRETS_PATH", "COMPOSEIO_TEMPLATE_STORE_URL", "MQTT")

Parsed = namedtuple("Parsed", "values skipped")          # values: {name: value}; skipped: ((line, reason),)
Applied = namedtuple("Applied", "path names skipped")    # path read (or None), names SET, skipped lines
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

applied = Applied(None, (), ())


def default_path():
    return os.path.expanduser(DEFAULT_PATH)


def resolve_path(environ=None):
    env = os.environ if environ is None else environ
    path = env.get(ENV_NAME) or default_path()
    return None if path.strip().lower() == "none" else os.path.expanduser(path)


def read(path):
    """Parse `path`; never raises. Line 0 in `skipped` means the file itself could not be read."""
    values, skipped = {}, []
    if not path or path.strip().lower() == "none" or not os.path.exists(path):
        return Parsed(values, ())
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except Exception as exc:  # noqa: BLE001 — a broken overlay must never break `import composeio`
        return Parsed(values, ((0, f"unreadable: {type(exc).__name__}"),))
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not _KEY.match(key):
            skipped.append((n, "not KEY=VALUE"))
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return Parsed(values, tuple(skipped))


def apply(path=None, environ=None):
    """Fill unset names in `environ` (default os.environ) from the overlay; record what was done."""
    global applied
    env = os.environ if environ is None else environ
    path = resolve_path(env) if path is None else path
    parsed = read(path)
    names = tuple(k for k, v in parsed.values.items() if k not in env)
    for k in names:
        env[k] = parsed.values[k]
    used = path if path and path.strip().lower() != "none" and os.path.exists(path) else None
    applied = Applied(used, names, parsed.skipped)
    return applied


def missing_required(environ=None, path=None):
    """OVERLAY_REQUIRED names with no non-empty value in `environ` nor in the overlay at `path`."""
    env = os.environ if environ is None else environ
    file_values = read(resolve_path(env) if path is None else path).values
    return tuple(n for n in OVERLAY_REQUIRED if not (env.get(n) or file_values.get(n)))
