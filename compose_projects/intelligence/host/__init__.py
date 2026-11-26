"""intelligence.host — the console-owned IntelligenceHost ([SPEC:CXC-INTELLIGENCE-HOST]).

A repeatable spawner that instantiates N parallel carried-identity peer intelligences over the
launcher's spawn machinery. See host.py for the contract ([CXC-IH-HOST]) and poc_driver.py for the
Option-A proof-of-concept ([CXC-IH-POC]).

Import style matches the rest of compose_projects/intelligence/* (script-style: the dir goes on sys.path and modules
are imported flat, e.g. `import host`). This shim just re-exports when imported as a package."""
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
try:
    # package import (e.g. pytest collecting `intelligence.host.*`): resolve the SUBMODULE, not the
    # partially-initialized package itself (`from host import ...` would resolve `host` -> this
    # __init__ and raise a spurious circular ImportError).
    from .host import IntelligenceHost, IntelligenceSpec, IntelligenceHandle  # noqa: F401
except ImportError:
    # script-style (the dir is on sys.path; `import host` loads host.py flat).
    from host import IntelligenceHost, IntelligenceSpec, IntelligenceHandle  # noqa: E402,F401
