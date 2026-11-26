"""compose_plugins — the shared plug-in contract library ([SPEC:CXC-PLUGIN-CONTRACT]).

FIRST WAVE ONLY (0.1.0.dev0). Built: the manifest JSON Schema and validator
(``compose_plugins.manifest``), the version rule (``compose_plugins.version``),
consent content, granted sets, widening and the consent-record store
(``compose_plugins.consent``), the manifest_sha256 rule and the no-execution
manifest reader for a directory or a wheel (``compose_plugins.package``), and a
conformance-script SKELETON (``compose_plugins.conformance``) whose host and
runner steps raise ``NotImplementedError("pending")``.

NOT BUILT YET (they wait on the owner's pending answers): the subprocess
runner, the web Plugin Manager UI, the ``Host`` protocol's run/stop wiring, the
install/upgrade/enable/disable/uninstall operations, the ``compose-plugin
build`` command, and the hello-world plug-in. Nothing in this package installs
or runs a plug-in.
"""

__version__ = "0.1.0.dev0"
