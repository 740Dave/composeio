"""THE `.mcp.json` GATE — an ABSENT file must present as a SKIP, never as a FAILURE.

WHY THIS FILE EXISTS (2026-08-13).  `.mcp.json` is GITIGNORED (`.gitignore:64`), so a fresh
`git worktree` CANNOT contain it — not "usually does not", cannot.  Roughly twenty tests derive
a child MCP identity from that file (`composeio.ptycore.build_child_mcp_config`), and in a
worktree they all raised `FileNotFoundError` / `BootstrapRefused`.  Measured on 798560a:

    fresh worktree, no .mcp.json :  20 failed, 3367 passed
    the same tree, .mcp.json seated:  0 failed, 3387 passed

TWO CONSEQUENCES, AND THE SECOND IS WORSE.  `scripts/pre-commit-tests.sh` runs this suite, so an
agent committing from a worktree got a BLOCKED COMMIT — which is why worktree work kept reaching
for `--no-verify`.  And because the missing prerequisite presented as FAILURES rather than as
ABSENCES, every reader reasonably concluded "pre-existing red on HEAD" and moved on.  Twenty
tests were therefore not merely red in every worktree landing this project has ever done: they
were never actually EXERCISED there, and nothing said so.

A SKIP SAYS "NOT EXERCISED HERE".  A FAILURE SAYS "BROKEN".  Only one of those was ever true, and
the gate below makes the output say the true one.

⛔ A SKIP NOBODY COUNTS IS THE SAME DISEASE AS A FAILURE NOBODY READS.  So this gate is loud in
BOTH directions and bounded in size:

  * `pytest_terminal_summary` ALWAYS prints one line — "N skipped, .mcp.json ABSENT" when the
    file is missing, and "gate INERT — N gated tests RAN" when it is present.  You never have to
    infer from a silence which condition you were in.
  * `GATED_BUDGET` caps the gated set.  Growing it past the cap is a hard `UsageError`, not a
    quiet enlargement — "20 skipped" cannot drift into "200 skipped" without a human raising the
    number in a diff and saying why.

HOW A TEST OPTS IN:

    @pytest.mark.requires_repo_mcp_config
    def test_something_that_derives_a_child_identity(): ...

Mark ONLY tests that genuinely read the file.  A mark on a test that would pass without it
converts real coverage into a skip — the failure class this gate exists to end, wearing the
gate's own uniform.
"""

from __future__ import annotations

import os
import sys

import pytest

# ⛔ NO HOST OVERLAY IN TESTS ([CXC-SCL-SCRUB-OVERLAY-PARITY]). `import composeio` fills unset names
# from a developer's live overlay (composeio/_overlay.py); tests assert the PUBLIC neutral defaults,
# so the overlay is forced off here, before anything imports composeio, and for every child process
# (they inherit os.environ). Forced, not setdefault: an outer COMPOSEIO_OVERLAY_ENV naming a real
# file would otherwise leak live values into the suite. test_overlay.py passes explicit paths.
os.environ["COMPOSEIO_OVERLAY_ENV"] = "none"

# ⛔ THE GLASS POLICY — headless by DEFAULT, glass by ANNOUNCED OPT-IN.
# The render suite is where scenes actually rasterize, so this is the seam that
# matters most: without it, a bare `pytest python/tests` opened real windows on
# the developer's desktop while `scripts/pre-commit-tests.sh` stayed headless.
# Shared with tests/conftest.py by REFERENCE — see tests/_glass_policy.py for the
# measured incident, and note that glass tests are NOT suppressed, they are
# announced ([PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN] — human eyes are
# load-bearing, so the answer is consent, not silence).
#
# ⛔ LOADED BY EXPLICIT PATH, NEVER VIA sys.path. The first version of this did
# `sys.path.insert(0, <repo>/tests)` to make the import work — which put the ROOT
# `tests/` directory at the FRONT of sys.path for the WHOLE python suite. Both
# `tests/` and `python/tests/` exist, so every module name present in both began
# resolving to the root copy, and import resolution became ORDER-DEPENDENT.
# Measured: it turned `test_sources_run_blocks.py[away_watch_publisher]` red in a
# full run while passing 3/3 in isolation, and a clean control at origin/main ran
# 3800 green. A test-support import must not mutate the import path of the code
# under test — the shared module is loaded by file path so nothing is shadowed.
def _load_glass_policy():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "tests" / "_glass_policy.py"
    spec = importlib.util.spec_from_file_location("_composeio_glass_policy", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_glass = _load_glass_policy()
_glass_register = _glass.register
_glass_gate = _glass.announce_and_gate
_glass_enabled = _glass.glass_enabled

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY_ROOT = os.path.dirname(_HERE)                    # <repo>/python
_REPO_ROOT = os.path.dirname(_PY_ROOT)               # <repo>
if _PY_ROOT not in sys.path:
    sys.path.insert(0, _PY_ROOT)


# ⛔ MONOREPO RESOLUTION OF THE SIBLING DISTRIBUTIONS — for this process AND its children.
# python/ holds two distribution DIRECTORIES beside composeio: python/composebus/ and
# python/compose_plugins/, each wrapping its real package one level down. With python/ on
# sys.path (every `cd python && pytest tests`, including scripts/pre-commit-tests.sh), the bare
# outer directory satisfies `import compose_plugins` as an EMPTY namespace package, so collection
# fails with "No module named 'compose_plugins.conformance'". composeio._seat_composebus repairs
# composebus in-process only, and only once composeio is imported — but test_composeserver_plugins
# imports compose_plugins FIRST, and the plug-in runner and probe spawn
# `python -m compose_plugins._child|_probe` children that never import composeio at all.
# Same rule as _seat_composebus: an INSTALLED distribution always wins; otherwise the sibling
# root is APPENDED to sys.path (a regular package beats a namespace portion at any position,
# and appending never shadows a name python/ already provides) and to PYTHONPATH, which every
# subprocess child inherits. Removal condition: both distributions installed in the dev env.
def _seat_sibling_distributions() -> None:
    import importlib
    import importlib.util

    for dist in ("composebus", "compose_plugins"):
        loaded = sys.modules.get(dist)
        if loaded is not None and getattr(loaded, "__file__", None):
            continue
        try:
            spec = importlib.util.find_spec(dist)
        except (ImportError, ValueError):
            spec = None
        if spec is not None and spec.origin is not None:
            continue
        root = os.path.join(_PY_ROOT, dist)
        if root not in sys.path:
            sys.path.append(root)
        parts = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
        if root not in parts:
            os.environ["PYTHONPATH"] = os.pathsep.join(parts + [root])
        sys.modules.pop(dist, None)
    importlib.invalidate_caches()


_seat_sibling_distributions()

#: The one marker name.  Named once, here; the test files spell it, this file enforces it.
MARKER = "requires_repo_mcp_config"

#: The measured size of the gated set on 798560a (11 + 3 + 6 items across three files).
MEASURED_GATED_ITEMS = 20

#: The CEILING.  Deliberately close to the measurement: headroom for a handful of honest new
#: cases, and a wall well below "a whole suite quietly stopped running".  Raising it is a
#: decision that appears in a diff — which is the entire point.
GATED_BUDGET = 32


def repo_mcp_config_path() -> str:
    """The path the CODE UNDER TEST will read.

    Resolved through `ptycore.repo_mcp_config_path()` so the gate and the thing it gates cannot
    disagree — including about `COMPOSEIO_MCP_CONFIG`, which repoints both at once.  The fallback
    exists only so a conftest can never be the reason a suite fails to start.
    """
    try:
        from composeio.ptycore import repo_mcp_config_path as _path
        return _path()
    except Exception:                                            # pragma: no cover - defensive
        return os.environ.get("COMPOSEIO_MCP_CONFIG",
                              os.path.join(_REPO_ROOT, ".mcp.json"))


def repo_mcp_config_present() -> bool:
    """Is the file actually there, right now, in THIS tree?"""
    return os.path.isfile(repo_mcp_config_path())


def skip_reason() -> str:
    """The reason string a reader of `-rs` output sees.  It names the FILE, the fact that the
    file is gitignored, and the remedy — so nobody has to guess what "skipped" meant."""
    return (
        f"{MARKER}: {repo_mcp_config_path()} is ABSENT. It is gitignored "
        f"(.gitignore:64), so a fresh git worktree never has it; these tests derive a child "
        f"MCP identity FROM that file and cannot run without it. Seat a copy at the repo root "
        f"(or point COMPOSEIO_MCP_CONFIG at one) to exercise them. NOT a failure — not exercised."
    )


def _state(config) -> dict:
    st = getattr(config, "_mcp_config_gate", None)
    if st is None:
        st = {"marked": 0, "skipped": 0,
              "present": repo_mcp_config_present(),
              "path": repo_mcp_config_path()}
        config._mcp_config_gate = st
    return st


def pytest_configure(config) -> None:
    config.addinivalue_line(
        "markers",
        f"{MARKER}: needs the repo's GITIGNORED .mcp.json. SKIPPED (never failed) when the "
        f"file is absent, which is the normal state of a fresh git worktree. See "
        f"python/tests/conftest.py.",
    )
    _state(config)
    _glass_register(config)


def pytest_report_header(config) -> str:
    st = _state(config)
    where = "present" if st["present"] else "ABSENT -> gated tests will SKIP"
    driver = os.environ.get("SDL_VIDEODRIVER", "<unset>")
    return (f"mcp-config gate: {st['path']} {where}\n"
            f"glass policy: SDL_VIDEODRIVER={driver} "
            f"({'GLASS ENABLED — windows may open' if _glass_enabled() else 'headless'})")


def pytest_collection_modifyitems(config, items) -> None:
    _glass_gate(config, items)
    st = _state(config)
    marked = [it for it in items if it.get_closest_marker(MARKER) is not None]
    st["marked"] = len(marked)

    if len(marked) > GATED_BUDGET:
        raise pytest.UsageError(
            f"{len(marked)} tests now carry @pytest.mark.{MARKER}, over the declared "
            f"GATED_BUDGET of {GATED_BUDGET} (measured baseline: {MEASURED_GATED_ITEMS}).\n"
            f"This ceiling exists so a legible '20 skipped' cannot become an illegible '200 "
            f"skipped'. If the growth is genuine, raise GATED_BUDGET in python/tests/conftest.py "
            f"and say why in the commit. If it is not, you have marked tests that do not "
            f"actually read {st['path']} — and turned real coverage into a silence."
        )

    if st["present"]:
        return
    skip = pytest.mark.skip(reason=skip_reason())
    for item in marked:
        item.add_marker(skip)
    st["skipped"] = len(marked)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """ALWAYS emit one line, in BOTH directions.

    The absent case has to be counted or it is a hole; the present case has to be counted or you
    cannot tell "the gate was inert and the tests ran" from "the gate silently ate them".
    """
    st = _state(config)
    write = terminalreporter.write_line
    if st["skipped"]:
        write(f"mcp-config gate: {st['skipped']} test(s) SKIPPED — {st['path']} is ABSENT "
              f"(gitignored; a fresh worktree never has it). Not failures: NOT EXERCISED.")
    elif not st["present"]:
        write(f"mcp-config gate: armed but 0 gated tests were collected "
              f"({st['path']} is ABSENT).")
    else:
        write(f"mcp-config gate: INERT — {st['path']} is present, so all {st['marked']} "
              f"gated test(s) RAN.")
