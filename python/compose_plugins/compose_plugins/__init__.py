"""compose_plugins — the shared plug-in contract library ([SPEC:CXC-PLUGIN-CONTRACT],
contract 0.2: spec 95 r24 plus its v0.2 delta, [DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA]).

The manifest validator and JSON Schema (``manifest``), the no-execution manifest
and EULA reader and the manifest_sha256 rule (``package``), the version rule
(``version``), consent content, granted sets, widening and the consent-record
store (``consent``), the ``Host`` protocol (``host``), the lifecycle operations
and ``record_decline`` (``lifecycle``), config validation (``config``),
``compose-plugin build`` and the drift check (``derive``), the in-process and
subprocess runner (``runner``), the registry, config and install-event-log
stores (``store``), the plain web Plugin Manager (``web``), and the conformance
script (``conformance``) with its hello-world fixtures. See README.md.

Contract 0.2 adds these exports ([CXC-PC-V02-STORES-COMPAT] manager-signatures).
"""

__version__ = "0.2.0"

from .consent import eula_text_shown  # noqa: E402
from .errors import (  # noqa: E402
    EulaShaMismatch, InstallLogRefused, PromptNotPrepared, PromptStale,
)
from .store import InstallLog, InstallLogFinding  # noqa: E402

__all__ = ["EulaShaMismatch", "InstallLog", "InstallLogFinding", "InstallLogRefused",
           "PromptNotPrepared", "PromptStale", "__version__", "eula_text_shown"]
