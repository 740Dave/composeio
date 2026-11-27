"""Conformance fixtures: hello-world v1 and v2 ([CXC-PC-HELLO-V1], [CXC-PC-HELLO-V2]),
and (contract 0.2) hello-world-eula ([CXC-PC-V02-EULA-HOST-OBLIGATION]): a
hello-world v1 manifest that states contract "0.2" and declares ``eula``.

Each ``hello_world_vN`` directory is a complete plug-in PACKAGE (a v0.1 package
source: a local directory) with ``manifest.json`` at its root, built by
``compose-plugin build``. Hosts install them like any third-party plug-in; the
library never imports them as ``compose_plugins.fixtures.*`` at run time.
"""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent


#: fixture name -> package directory name
_DIRS = {"v1": "hello_world_v1", "v2": "hello_world_v2", "eula": "hello_world_eula",
         "hello-world-eula": "hello_world_eula"}


def package_dir(version: str) -> Path:
    """'v1', 'v2' or 'hello-world-eula' (also 'eula') -> the package directory."""
    if version not in _DIRS:
        raise ValueError(f"unknown hello-world fixture {version!r}")
    return FIXTURES / _DIRS[version]
