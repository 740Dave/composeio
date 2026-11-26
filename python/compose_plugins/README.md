# compose-plugins

The shared plug-in contract library of `[SPEC:CXC-PLUGIN-CONTRACT]` (contract
0.1): the layer a plug-in is INSTALLED and CONSENTED to, for ComposeIO's
ComposeServer and for Gnomon. One runtime dependency: `composebus==0.1.2`.

**0.1.0.dev0 is the FIRST WAVE and is incomplete on purpose.** Built:

| module | what |
|---|---|
| `compose_plugins.manifest` | the manifest validator (authoritative) and the shipped JSON Schema (`schema/manifest-0.1.schema.json`) |
| `compose_plugins.version` | the contract version rule |
| `compose_plugins.consent` | consent content from the manifest alone, granted strings, widening, the consent-record store |
| `compose_plugins.package` | `manifest_sha256` and reading `manifest.json` from a directory or a wheel without executing anything |
| `compose_plugins.conformance` | the conformance-script SKELETON: every step and negative control declared; host and runner steps are PENDING |

Not built yet: the subprocess runner, the web Plugin Manager UI, the `Host`
protocol's run/stop wiring, the install/upgrade/enable/disable/uninstall
operations, `compose-plugin build`, and the hello-world plug-in.

```python
from compose_plugins.package import read_manifest
from compose_plugins.manifest import validate_manifest

mf = read_manifest("path/to/plugin_dir_or.whl")      # executes nothing
m = validate_manifest(mf.data, host="composeio",
                      section_validator=my_section_validator, package=mf.package)
```

Tests: `pip install ./python/composebus ./python/compose_plugins -r python/compose_plugins/tests/requirements.txt`
then `pytest python/compose_plugins/tests`.
