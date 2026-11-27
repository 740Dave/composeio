# compose-plugins

The shared plug-in contract library for ComposeIO's ComposeServer and for
Gnomon, implementing the Shared Plug-in Contract v0.2 (ComposeIO spec 95,
`[SPEC:CXC-PLUGIN-CONTRACT]`, revision r24 plus its v0.2 delta). It is the layer
a plug-in is INSTALLED and CONSENTED to, plus the shared manager that drives it.

Contract 0.2 is an additive MINOR over 0.1: optional manifest fields `license`
(an SPDX expression, informational) and `eula` (a package-relative UTF-8 text
file, shown verbatim at consent, its sha256 stored in the consent record);
`PluginManager.record_decline(prompt, *, decided_by)`; the append-only install
event log `installs.jsonl` and `PluginManager.install_log_findings()`; and a
confirm step in front of uninstall in the web manager. The one non-additive
change: the web manager's `POST .../uninstall` no longer uninstalls, it shows
the confirm screen.

One runtime dependency: `composebus==0.1.2` (the runtime `ComposePlugin` and
its `MessageType` names). No ComposeIO distribution is required, so a host
that is not ComposeIO can use the library on its own.

## What is in the package

| module | what |
|---|---|
| `compose_plugins.manifest` | the v0.1 manifest validator (authoritative) and the shipped JSON Schema (`schema/manifest-0.1.schema.json`), generated from the validator's own patterns |
| `compose_plugins.package` | reading `manifest.json` from a directory or a wheel WITHOUT executing anything, and the `manifest_sha256` rule (exact bytes, no canonicalization) |
| `compose_plugins.version` | the contract version rule: a host accepts the same MAJOR and a MINOR at or below its own, and refuses anything newer, naming both versions |
| `compose_plugins.consent` | consent content rendered from the manifest alone, the granted set, the widening rule, and the append-only consent-record store |
| `compose_plugins.host` | the `Host` protocol each host implements: consent gate, install root, own host-section validator and renderer, auth hook, `extra_panels`, supported isolation levels |
| `compose_plugins.lifecycle` | `PluginManager`: install, configure, enable, disable, upgrade and uninstall, with the state machine of the contract |
| `compose_plugins.config` | config validated against `config_schema` before the plug-in is instantiated; invalid config parks loudly |
| `compose_plugins.derive` | `compose-plugin build` (writes `config_schema` and `bus` into `manifest.json` from the class) and the enable-time class/manifest drift check |
| `compose_plugins.runner` | running a plug-in in-process, or in a child process over an inherited `AF_UNIX` socketpair (never a network listener) |
| `compose_plugins.store` | the install records (the registry), stored config, and the append-only install event log `InstallLog` with its findings |
| `compose_plugins.web` | the plain Plugin Manager: server-rendered HTML, a mountable WSGI app that is correct under any URL prefix, with the host's auth hook and CSRF defence |
| `compose_plugins.conformance` | the conformance script (below) |
| `compose_plugins.fixtures` | the hello-world v1 and v2 conformance plug-ins, and `hello-world-eula` (contract 0.2, declares a EULA) |

```python
from compose_plugins.package import read_manifest
from compose_plugins.manifest import validate_manifest

mf = read_manifest("path/to/plugin_dir_or.whl")      # executes nothing
m = validate_manifest(mf.data, host="myhost",
                      section_validator=my_section_validator, package=mf.package)
```

## The conformance script

The contract is authoritative, and the conformance script is the arbiter
between hosts: a host that fails any step or negative control is
non-conformant. It runs the hello-world lifecycle in nine steps (install with
consent, configure, start, health, a publish/subscribe round trip, an upgrade
to v2 with re-consent and config migration, disable, re-enable, and uninstall
with the retained topic tombstoned and the consent record kept). It then runs
negative controls NC1 to NC8, each of which must fail loudly. This happens once
per host and per isolation level the host supports. At the subprocess level it
also checks that the child holds no network socket and that a child crash is
reported as `failed(<reason>)`.

    python -m compose_plugins.conformance --target <module>:<factory> --isolation inprocess|subprocess

The factory is called as `factory(isolation, workdir)` and returns a TARGET
with `.manager`, `.bus`, `.host`, `.isolation`, `.operator` and `.close()`,
plus the two members added in spec 95 r24:

- `foreign_host: str` (REQUIRED): the name of another host. NC4's paired
  positive control puts an unknown key inside `hosts.<foreign_host>` and
  expects this host to ignore it and install the plug-in. The key is never put
  in this host's own section, so the control runs unchanged on every host. The
  runner refuses to run before step 1 if `foreign_host` is absent
  ("foreign_host missing"), equals `target.host.name`, or breaks the host-name
  rule; the refusal names the value, the exit status is 2, and nothing is
  installed.
- `host_section(fixture: str) -> dict` (optional): this host's own valid
  section for a fixture id: `hello-world-v1`, `hello-world-v2`, or a
  negative-control id `NC1` to `NC8`. Before every install it hands to the
  host, the runner writes that value at `hosts.<target.host.name>`, so the
  consented `manifest_sha256` covers it. A target without the hook gets `{}`
  there. The shared fixtures never decide a host's section: each host owns its
  own.

The script prints the observed value for every step, and exits 0 only when
every step and control passes.

## Tests

From a checkout of the ComposeIO repository:

    pip install ./python/composebus ./python/compose_plugins -r python/compose_plugins/tests/requirements.txt
    pytest python/compose_plugins/tests
