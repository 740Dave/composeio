"""[CXC-CBK-PKG-CONTENTS] — the package holds exactly the 13 named top-level
modules (+ ``__init__.py``) and the 6 ``plugins/`` files, and nothing else.

Lists the .py files of the package as imported (the INSTALLED copy in a consumer
venv). Run directly to print the raw listing; exit 0 iff the sets match.
"""
from __future__ import annotations

import pathlib
import sys

TOP_LEVEL = {
    "bus.py", "bus_clock.py", "bus_envelope.py", "freshness.py", "module.py",
    "namespace.py", "selector.py", "state_engine.py", "transport.py",
    "transport_contract.py", "transport_local.py", "plugin.py", "transforms.py",
}
PLUGINS = {
    "plugins/__init__.py", "plugins/state_manager.py", "plugins/discovery.py",
    "plugins/time.py", "plugins/liveness.py", "plugins/log.py",
}
EXPECTED = TOP_LEVEL | PLUGINS | {"__init__.py"}


def listing() -> tuple[pathlib.Path, set[str]]:
    import composebus
    pkg = pathlib.Path(composebus.__file__).resolve().parent
    return pkg, {p.relative_to(pkg).as_posix() for p in pkg.rglob("*.py")}


def test_package_contents_are_exactly_the_named_modules():
    assert len(TOP_LEVEL) == 13 and len(PLUGINS) == 6
    _, found = listing()
    assert found, "listed ZERO files — the instrument is blind"
    assert found == EXPECTED, {"missing": sorted(EXPECTED - found),
                               "unexpected": sorted(found - EXPECTED)}


def test_non_moving_plugins_are_not_importable_from_composebus():
    import importlib
    import pytest
    for name in ("bus_storage", "fingerprint_manager", "fingerprint_template_store"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"composebus.plugins.{name}")


def main() -> int:
    pkg, found = listing()
    print(f"package_dir: {pkg}")
    print(f"files_found: {len(found)}")
    for f in sorted(found):
        print(f"  {f}")
    print(f"missing: {sorted(EXPECTED - found)}")
    print(f"unexpected: {sorted(found - EXPECTED)}")
    ok = found == EXPECTED
    print("RESULT: " + ("MATCH" if ok else "MISMATCH"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
