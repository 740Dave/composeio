# pio_flash_guard.py — PlatformIO PRE: extra_script, loaded by EVERY platformio.ini in
# this repo that can write a device (cyd/, firmware/*/).  Two jobs, both fail-CLOSED:
#
#   1. FLASH GUARD.  When the command line asks for a target that writes a device
#      (upload, uploadfs, erase, program, `pio test`'s upload, ...), check the target
#      port's USB serial against the host overlay BEFORE anything is built or written:
#        FLASH_DENY_SERIALS            never-flash list; unset/empty = REFUSE
#        custom_flash_expect_env = V   (per env) $V must be set and must equal the
#                                      port's serial; unset = REFUSE
#        custom_flash_port_env = V     (per env) the port comes from $V (via
#                                      upload_port = ${sysenv.V}); unset = REFUSE
#      Auto-detect (no explicit port) and every protocol except esptool are refused
#      (an allowlist: only a write through the checked port is guardable).  The logic
#      lives in python/composeio/flash_guard.py, loaded BY PATH (no package import).
#
#   2. LIVE-BUILD ENV.  `custom_fw_required_env = A B` (per env) names host-overlay
#      variables the build bakes in via ${sysenv.A}.  PlatformIO substitutes an unset
#      sysenv as an EMPTY STRING, which would ship a board with a blank broker/storage
#      URL; so any unset name FAILS THE BUILD here, for every target.
#
# Public code carries no device identities.  Every identity is set in the host overlay.
#
# ⚠ `__file__` is not defined under SCons (see cyd/tools/gen_build_stamp.py), so the
#   repo root is found by walking up from $PROJECT_DIR.  The r503_probe mirror build
#   (firmware/r503_probe/build.sh) preserves the relative layout for exactly this reason.
import importlib.util
import os
import sys

_LIB_REL = os.path.join("python", "composeio", "flash_guard.py")


def _find_repo_root(project_dir):
    d = os.path.realpath(project_dir)
    while True:
        if os.path.isfile(os.path.join(d, _LIB_REL)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _load_lib(repo_root):
    spec = importlib.util.spec_from_file_location(
        "composeio_flash_guard", os.path.join(repo_root, _LIB_REL))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["composeio_flash_guard"] = mod  # dataclasses resolve via sys.modules
    spec.loader.exec_module(mod)
    return mod


def _opt(env, name):
    try:
        value = env.GetProjectOption(name, "")
    except Exception:  # option absent / older core
        value = ""
    if isinstance(value, (list, tuple)):
        value = " ".join(value)
    return (value or "").strip()


def run(env, targets, environ):
    """Return 0 to proceed, or a non-zero exit code after printing the refusal."""
    if env.IsCleanTarget():
        return 0
    pioenv = env.subst("$PIOENV")

    required = _opt(env, "custom_fw_required_env").split()
    missing = [n for n in required if not (environ.get(n) or "").strip()]
    if missing:
        sys.stderr.write(
            "flash_guard: env:%s bakes host-overlay values into the firmware, but these are "
            "unset or empty: %s. Set them in the host overlay; a build without them would "
            "ship a board that cannot reach its broker/config.\n" % (pioenv, " ".join(missing)))
        return 1

    writing = [t for t in targets if _is_write_target(t)]
    if not writing:
        return 0

    root = _find_repo_root(env.subst("$PROJECT_DIR"))
    if root is None:
        sys.stderr.write(
            "flash_guard: refusing %s for env:%s: cannot locate %s above %s, so the "
            "identity guard cannot run.\n" % (",".join(writing), pioenv, _LIB_REL,
                                             env.subst("$PROJECT_DIR")))
        return 2
    lib = _load_lib(root)
    try:
        serial = lib.check_flash(
            env.subst("$UPLOAD_PORT"), environ,
            expect_var=_opt(env, "custom_flash_expect_env") or None,
            port_var=_opt(env, "custom_flash_port_env") or None,
            protocol=_protocol(env))
    except lib.FlashRefused as exc:
        sys.stderr.write("flash_guard: env:%s: %s\n" % (pioenv, exc))
        return 2
    sys.stdout.write("flash_guard: env:%s may write %s (USB serial %s)\n"
                     % (pioenv, env.subst("$UPLOAD_PORT"), serial))
    return 0


def _protocol(env):
    """The upload protocol this env will use: the explicit option, else the board
    manifest's default (PlatformIO resolves the same way). An empty result refuses."""
    proto = (env.subst("$UPLOAD_PROTOCOL") or "").strip()
    if not proto:
        try:
            proto = (env.BoardConfig().get("upload.protocol", "") or "").strip()
        except Exception:
            proto = ""
    return proto


def _is_write_target(name):
    # Same set as flash_guard.is_flash_target; `__debug` is a build, not a write.
    import re
    return bool(re.search(r"(upload|erase|flash|program|bootloader|fuses)", name or "", re.I))


if "Import" in globals():  # under SCons; a test imports this file and calls run()
    Import("env")  # noqa: F821 — injected by SCons
    from SCons.Script import COMMAND_LINE_TARGETS  # noqa: E402
    _rc = run(env, list(COMMAND_LINE_TARGETS), os.environ)  # noqa: F821
    if _rc:
        env.Exit(_rc)  # noqa: F821
