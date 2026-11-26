"""Conformance fixtures: hello-world v1 and v2 ([CXC-PC-HELLO-V1], [CXC-PC-HELLO-V2]).

Each ``hello_world_vN`` directory is a complete plug-in PACKAGE (a v0.1 package
source: a local directory) with ``manifest.json`` at its root, built by
``compose-plugin build``. Hosts install them like any third-party plug-in; the
library never imports them as ``compose_plugins.fixtures.*`` at run time.
"""
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent


def package_dir(version: str) -> Path:
    """'v1' or 'v2' -> the package directory."""
    if version not in ("v1", "v2"):
        raise ValueError(f"unknown hello-world fixture {version!r}")
    return FIXTURES / f"hello_world_{version}"
