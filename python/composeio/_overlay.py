"""The host overlay: live values for names the public tree carries only as neutral defaults.

[CXC-SCL-SCRUB-OVERLAY-PARITY]. Called from composeio/__init__.py BEFORE any submodule import,
because several submodules read the environment at their own import time. Standard library only.

File: $COMPOSEIO_OVERLAY_ENV, else ~/.config/composeio/live.env; the value `none` disables it; a
missing file is a no-op. Lines are KEY=VALUE, `#` comments, optional `export ` prefix, one pair of
matching surrounding quotes stripped, NO shell expansion. Only names absent from the environment
are set, so an explicit environment always wins. `applied` records the file and the NAMES set,
never values. The FIRST occurrence of a name wins. A malformed line is skipped and recorded by
line number: no `=`, a bad key, a duplicate, a value holding NUL, a key or value over its cap,
text that is not UTF-8. A path that is not a regular file is never opened (a FIFO would block).
Nothing here raises: `import composeio` must survive any overlay content.
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
MAX_KEY = 256              # characters
MAX_VALUE = 64 * 1024      # characters; a larger value would break every child spawn (E2BIG)

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
    try:
        if not path or path.strip().lower() == "none" or not os.path.exists(path):
            return Parsed(values, ())
        if not os.path.isfile(path):
            return Parsed(values, ((0, "not a regular file"),))
        with open(path, "rb") as fh:
            data = fh.read()
    except Exception as exc:  # noqa: BLE001 — a broken overlay must never break `import composeio`
        return Parsed(values, ((0, f"unreadable: {type(exc).__name__}"),))
    for n, raw in enumerate(data.splitlines(), 1):
        if len(raw) > 4 * (MAX_KEY + MAX_VALUE) + 16:   # cannot pass the caps: do not even decode it
            skipped.append((n, "line over the caps"))
            continue
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            skipped.append((n, "not UTF-8"))
            continue
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not _KEY.match(key) or len(key) > MAX_KEY:
            skipped.append((n, "not KEY=VALUE"))
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if "\x00" in value:
            skipped.append((n, "NUL in value"))
        elif len(value) > MAX_VALUE:
            skipped.append((n, "value over MAX_VALUE"))
        elif key in values:
            skipped.append((n, "duplicate name (the first wins)"))
        else:
            values[key] = value
    return Parsed(values, tuple(skipped))


def apply(path=None, environ=None):
    """Fill unset names in `environ` (default os.environ) from the overlay; record what was done.
    Never raises: a write the environment refuses is a skipped line, and any other fault is
    recorded as line 0."""
    global applied
    env = os.environ if environ is None else environ
    try:
        path = resolve_path(env) if path is None else path
        parsed = read(path)
        names, skipped = [], list(parsed.skipped)
        for k, v in parsed.values.items():
            if k in env:
                continue
            try:
                env[k] = v
                names.append(k)
            except Exception as exc:  # noqa: BLE001 — e.g. ValueError from os.environ
                skipped.append((0, f"{k}: refused by the environment ({type(exc).__name__})"))
        used = path if path and path.strip().lower() != "none" and os.path.exists(path) else None
        applied = Applied(used, tuple(names), tuple(skipped))
    except Exception as exc:  # noqa: BLE001 — last resort: never break `import composeio`
        applied = Applied(None, (), ((0, f"loader fault: {type(exc).__name__}"),))
    return applied


def missing_required(environ=None, path=None):
    """OVERLAY_REQUIRED names with no non-empty value in `environ` nor in the overlay at `path`."""
    env = os.environ if environ is None else environ
    file_values = read(resolve_path(env) if path is None else path).values
    return tuple(n for n in OVERLAY_REQUIRED if not (env.get(n) or file_values.get(n)))


def live_host_status(environ=None, path=None):
    """(is_live, problems). Live when COMPOSEIO_LIVE_HOST=1 is in the environment or in the overlay.
    On a live host, a missing overlay file and every unset OVERLAY_REQUIRED name are problems
    (names only, never values)."""
    env = os.environ if environ is None else environ
    path = resolve_path(env) if path is None else path
    file_values = read(path).values
    live = env.get(LIVE_HOST_MARKER) == "1" or file_values.get(LIVE_HOST_MARKER) == "1"
    if not live:
        return False, ()
    problems = []
    if not (path and os.path.isfile(path)):
        problems.append(f"no overlay file at {path}")
    problems += [f"{n} unset" for n in missing_required(environ=env, path=path)]
    return True, tuple(problems)
