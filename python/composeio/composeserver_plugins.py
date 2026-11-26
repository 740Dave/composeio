"""The ComposeIO plug-in HOST: compose_plugins wired into ComposeServer
([SPEC:CXC-PLUGIN-CONTRACT] contract 0.1; [CXC-PC-COMPOSEIO-HOST-IDENTITY]).

This host-side change implements plug-in contract 0.1
([CXC-PC-STANDING-HOST-CITES-VERSION]).

* Name ``composeio``; its host section is ``{}`` (or empty) in v0.1.
* Isolation: chosen PER HOST INSTANCE (``minimum_isolation``), never below a
  plug-in's floor. Both ``inprocess`` and ``subprocess`` are supported.
* Plug-ins run on a ComposeServer tenant's bus; status goes to
  ``_server/plugins/<domain>/status`` beside the loader's own status surface.
* The consent GATE ([CXC-PC-CONSENT-GATES]): an operator click in the Plugin
  Manager, or a CLI install with explicit ``--consent --operator <name>``. The
  operator's identity becomes ``decided_by``. No implicit consent: a tenant
  manifest naming a domain enables only an ALREADY-INSTALLED plug-in.
* The AUTH hook (Q2, revised): HTTP BASIC, username = operator, password =
  token, LOOPBACK requests only. ``composeserver.plugin_manager.operators`` in
  the secrets file (read BY REFERENCE) is ``{<operator name>: <token>}``; the
  token is compared with ``hmac.compare_digest``. If the key is absent, EVERY
  request is denied. A denied browser gets 401 with ``WWW-Authenticate:
  Basic realm=...`` for its native prompt. A token is never logged or echoed.
"""
from __future__ import annotations
import os

import argparse
import base64
import hmac
import logging
import sys
from pathlib import Path
from typing import Any

from compose_plugins.consent import HostPermission
from compose_plugins.errors import PluginContractRefusal, Refusal
from compose_plugins.host import ConsentDecision, ConsentRequest, Host
from compose_plugins.lifecycle import PluginManager

logger = logging.getLogger("composeio.composeserver_plugins")

HOST_NAME = "composeio"
DEFAULT_SECRETS_PATH = Path(os.environ.get("COMPOSEIO_SECRETS_PATH", "~/.config/composeio/secrets.yaml"))
OPERATORS_KEY = ("composeserver", "plugin_manager", "operators")
LOOPBACK = frozenset({"127.0.0.1", "::1"})


BASIC_REALM = 'Basic realm="ComposeServer Plugin Manager", charset="UTF-8"'


class LoopbackTokenAuth:
    """environ -> operator name, or None (deny). HTTP Basic: username = the
    operator, password = that operator's token. Loopback only. The token is
    compared with ``hmac.compare_digest`` and is never logged or echoed."""

    #: the challenge a browser needs for its native credentials prompt
    challenge = BASIC_REALM

    def __init__(self, secrets_path: "str | Path" = DEFAULT_SECRETS_PATH):
        self.secrets_path = Path(secrets_path).expanduser()

    def _operators(self) -> "dict[str, str]":
        """``{operator name: token}`` from the secrets file, or ``{}``."""
        import yaml
        try:
            data = yaml.safe_load(self.secrets_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception as exc:  # unreadable store: deny, and say why without content
            logger.error("plugin manager auth: secrets file unreadable (%s); denying all",
                         type(exc).__name__)
            return {}
        node: Any = data
        for k in OPERATORS_KEY:
            if not isinstance(node, dict) or k not in node:
                return {}
            node = node[k]
        if not isinstance(node, dict):
            return {}
        return {str(n): str(t) for n, t in node.items() if n and t}

    @staticmethod
    def _basic(environ: dict) -> "tuple[str, str] | None":
        header = environ.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Basic "):
            return None
        try:
            raw = base64.b64decode(header[len("Basic "):].strip(), validate=True)
            user, sep, password = raw.decode("utf-8").partition(":")
        except (ValueError, UnicodeDecodeError):
            return None
        if not sep or not user or not password:
            return None
        return user, password

    def __call__(self, environ: dict) -> "str | None":
        if environ.get("REMOTE_ADDR") not in LOOPBACK:
            return None
        creds = self._basic(environ)
        if creds is None:
            return None
        user, password = creds
        token = self._operators().get(user)
        # compare even when the operator is unknown, so timing does not say which
        ok = hmac.compare_digest((token or "\0" * 32).encode(), password.encode())
        return user if (token is not None and ok) else None


class ComposeIOPluginHost(Host):
    def __init__(self, *, state_root: "str | Path", minimum_isolation: str = "inprocess",
                 server=None, tenant_id: "str | None" = None, auth_hook=None):
        super().__init__(HOST_NAME, state_root=state_root, minimum_isolation=minimum_isolation)
        self.server, self.tenant_id = server, tenant_id
        self.auth_hook = auth_hook if auth_hook is not None else LoopbackTokenAuth()
        #: WWW-Authenticate value the Plugin Manager sends on a denial
        self.auth_challenge = getattr(self.auth_hook, "challenge", None)
        self.manager = PluginManager(self)
        self._tick_attached = False
        self.attach_tick()

    def attach_tick(self) -> None:
        """r18 composeio-health-tick: drive the manager's status tick (which asks
        every plug-in ``health_reason()``) from ComposeServer's per-tenant
        announce tick. Needs the tenant to exist; ``bus()`` callers retry."""
        if self._tick_attached or self.server is None or self.tenant_id is None:
            return
        if self.tenant_id not in self.server.tenants():
            return
        self.server.add_tick_hook(self.tenant_id, self.manager.tick)
        self._tick_attached = True

    def consent_gate(self, request: ConsentRequest) -> ConsentDecision:
        op = (request.operator or "").strip()
        if request.accepted and op:
            return ConsentDecision("granted", op)
        return ConsentDecision("refused", op or "composeio:no-operator")

    def validate_section(self, section: Any) -> "list[Refusal]":
        if section in ({}, None):
            return []
        return [Refusal("", f"{section!r}: the composeio section holds nothing in "
                            "contract 0.1 (it must be {})")]

    def render_section(self, section: Any) -> "list[HostPermission]":
        return []

    def bus(self):
        if self.server is None or self.tenant_id is None:
            return None
        self.attach_tick()
        return self.server.tenant_bus(self.tenant_id)

    def publish_status(self, domain: str, status: str) -> None:
        bus = self.bus()
        if bus is not None:
            ns = f"_server/plugins/{domain}/status"
            bus.ensure_namespace(ns, "json")
            rec = self.manager.record(domain) or {}
            bus.publish_state(ns, {"plugin": domain, "status": status,
                                   "runs_as": rec.get("runs_as"),
                                   "host": HOST_NAME, "contract": self.contract},
                              source="_server")


# --------------------------------------------------------------------------- #
# The CLI install path ([CXC-PC-CONSENT-GATES]: --consent is explicit).
# --------------------------------------------------------------------------- #
def main(argv: "list[str] | None" = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m composeio.composeserver_plugins")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("install", help="install a plug-in package (directory or wheel)")
    i.add_argument("source")
    i.add_argument("--state-root", required=True)
    i.add_argument("--consent", action="store_true",
                   help="the operator consents to what is printed")
    i.add_argument("--operator", default=None, help="the consenting operator's name")
    args = ap.parse_args(argv)
    host = ComposeIOPluginHost(state_root=args.state_root)
    try:
        prompt = host.manager.prepare_install(args.source)
    except PluginContractRefusal as exc:
        print(f"install REFUSED: {exc}", file=sys.stderr)
        return 1
    print(prompt.content.text())
    print(f"manifest_sha256: {prompt.manifest_sha256}")
    if not args.consent or not (args.operator or "").strip():
        print("install REFUSED: consent needs --consent and --operator <name>; "
              "nothing was installed", file=sys.stderr)
        return 2
    try:
        host.manager.install(args.source, consented_sha256=prompt.manifest_sha256,
                             operator=args.operator, accepted=True)
    except PluginContractRefusal as exc:
        print(f"install REFUSED: {exc}", file=sys.stderr)
        return 1
    print(f"installed {prompt.manifest.domain} {prompt.manifest.version}")
    return 0


# --------------------------------------------------------------------------- #
# The conformance target ([CXC-PC-CONFORMANCE-SCRIPT]): one ComposeServer
# tenant, one host instance at the requested isolation level.
# r24: ``foreign_host`` is "gnomon" (NC4's positive control writes its unknown
# key in hosts.gnomon), and there is NO ``host_section`` hook, so the runner
# writes ComposeIO's own section as ``{}`` ([CXC-PC-HOST-SECTIONS] rule 6).
# --------------------------------------------------------------------------- #
class ConformanceTarget:
    foreign_host = "gnomon"

    def __init__(self, isolation: str, workdir: "str | Path"):
        from composeio.composeserver import ComposeServer
        self.server = ComposeServer()
        self.tenant_id = "conformance"
        self.server.add_tenant(self.tenant_id, plugins=[], start=True)
        self.host = ComposeIOPluginHost(state_root=workdir, minimum_isolation=isolation,
                                        server=self.server, tenant_id=self.tenant_id,
                                        auth_hook=lambda environ: None)
        self.manager = self.host.manager
        self.bus = self.host.bus()
        self.isolation = isolation
        self.operator = "conformance-operator"

    def close(self) -> None:
        self.manager.close()
        self.server.stop()


def conformance_target(isolation: str, workdir: "str | Path") -> ConformanceTarget:
    return ConformanceTarget(isolation, workdir)


if __name__ == "__main__":
    sys.exit(main())
