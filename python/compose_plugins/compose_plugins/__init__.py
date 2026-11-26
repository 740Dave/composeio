"""compose_plugins — the shared plug-in contract library ([SPEC:CXC-PLUGIN-CONTRACT],
contract 0.1, spec 95 revision r24).

The manifest validator and JSON Schema (``manifest``), the no-execution manifest
reader and the manifest_sha256 rule (``package``), the version rule
(``version``), consent content, granted sets, widening and the consent-record
store (``consent``), the ``Host`` protocol (``host``), the lifecycle operations
(``lifecycle``), config validation (``config``), ``compose-plugin build`` and
the drift check (``derive``), the in-process and subprocess runner
(``runner``), the registry and config stores (``store``), the plain web Plugin
Manager (``web``), and the conformance script (``conformance``) with its
hello-world fixtures. See README.md.
"""

__version__ = "0.1.0.dev0"
