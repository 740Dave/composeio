"""The plain Plugin Manager: server-rendered HTML, no JavaScript, a mountable
WSGI app ([CXC-PC-WEB-UI], [CXC-PC-UI-URL-PREFIX], [CXC-PC-UI-HOST-HOOKS]).

* Every link, form action and redirect is built from ``SCRIPT_NAME``; no path
  starts with ``/`` outside the mount prefix.
* The library does NO authentication: every request goes through the host's
  ``auth_hook`` BEFORE any handler; a denial is 403 (or 401 with the host's
  ``auth_challenge`` as ``WWW-Authenticate``, when the host sets one) and no
  handler runs. A Host
  without an auth hook cannot construct the app.
* CSRF (r18 csrf-defence, both hosts): every form carries a per-session token
  (HMAC of the authenticated identity under a per-app random key). Every POST
  is refused BEFORE any handler, changing nothing and recording nothing, when
  the token is missing or wrong, when ``Origin`` is present and not the host's
  own origin, or when ``Sec-Fetch-Site`` is ``cross-site``.
* Consent is two steps: a POST to ``.../prepare`` renders the consent screen
  (with "runs as" and, on upgrade, every ADDED grant highlighted); a POST to
  ``.../decide`` with accept or refuse goes through the host's consent gate,
  with the authenticated identity as the operator.
* ``Host.extra_panels()`` are served at ``<prefix>/panel/<slug>``, behind the
  same auth.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import secrets
from typing import Any, Callable
from urllib.parse import parse_qs, quote

from .consent import ConsentContent
from .errors import PluginContractRefusal
from .host import Host
from .lifecycle import ConsentPrompt, PluginManager
from .version import HOST_CONTRACT

#: THE auto-upgrade switch ([CXC-PC-LIFECYCLE-UPGRADE]; pending DSD
#: dsd-1a215cd8523). Disabled, no behaviour: nothing auto-upgrades in v0.1.
#: To HIDE it, delete the single line in ``_plugin_row`` that appends it.
AUTO_UPGRADE_SWITCH = ('<label class="auto-upgrade"><input type="checkbox" '
                       'name="auto_upgrade" disabled> auto-upgrade arrives with '
                       'versioned sources (v0.2)</label>')

e = html.escape


class _Response(Exception):
    def __init__(self, status: str, body: str = "", headers: "list | None" = None):
        self.status, self.body, self.headers = status, body, headers or []


class PluginManagerApp:
    def __init__(self, manager: PluginManager):
        host = manager.host
        if not isinstance(host, Host) or not callable(getattr(host, "auth_hook", None)):
            raise PluginContractRefusal("Plugin Manager REFUSES TO START: the Host supplies "
                                        "no auth hook (the manager never defaults to open)")
        self.manager, self.host = manager, host
        #: per-app secret; a session's CSRF token is HMAC(key, identity)
        self._csrf_key = secrets.token_bytes(32)

    # ---- CSRF ([CXC-PC-UI-HOST-HOOKS] csrf-defence, r18) ------------------- #
    def csrf_token(self, identity: str) -> str:
        return hmac.new(self._csrf_key, identity.encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def own_origin(environ: dict) -> str:
        scheme = environ.get("wsgi.url_scheme", "http")
        host = environ.get("HTTP_HOST")
        if not host:
            host = environ.get("SERVER_NAME", "")
            port = environ.get("SERVER_PORT", "")
            if port and port not in ("80", "443"):
                host = f"{host}:{port}"
        return f"{scheme}://{host}"

    def _csrf_check(self, environ: dict, form: "dict[str, str]", identity: str) -> None:
        """Refuse a state-changing request BEFORE any handler: a cross-site
        Origin, Sec-Fetch-Site cross-site, or a missing/mismatched token."""
        origin = environ.get("HTTP_ORIGIN")
        if origin is not None and origin != self.own_origin(environ):
            raise _Response("403 Forbidden", self._page(
                environ.get("SCRIPT_NAME", "").rstrip("/"), "Refused",
                "<p>Refused: the request came from another origin.</p>"))
        if environ.get("HTTP_SEC_FETCH_SITE") == "cross-site":
            raise _Response("403 Forbidden", self._page(
                environ.get("SCRIPT_NAME", "").rstrip("/"), "Refused",
                "<p>Refused: a cross-site request.</p>"))
        if not hmac.compare_digest(form.get("csrf", ""), self.csrf_token(identity)):
            raise _Response("403 Forbidden", self._page(
                environ.get("SCRIPT_NAME", "").rstrip("/"), "Refused",
                "<p>Refused: the form's CSRF token is missing or does not match. "
                "Reload the page and try again.</p>"))

    # ---- WSGI ------------------------------------------------------------- #
    def __call__(self, environ: dict, start_response: Callable) -> "list[bytes]":
        prefix = environ.get("SCRIPT_NAME", "").rstrip("/")
        try:
            identity = self.host.auth_hook(environ)
            if not identity:
                challenge = getattr(self.host, "auth_challenge", None)
                if challenge:   # e.g. HTTP Basic: the browser shows its native prompt
                    raise _Response("401 Unauthorized", self._page(
                        prefix, "Unauthorized", "<p>Not authorized.</p>"),
                        [("WWW-Authenticate", challenge)])
                raise _Response("403 Forbidden", self._page(prefix, "Forbidden",
                                                            "<p>Not authorized.</p>"))
            body = self._route(environ, prefix, identity)
            status, headers = "200 OK", []
        except _Response as r:
            status, body, headers = r.status, r.body, r.headers
        except KeyError as exc:
            status, headers = "400 Bad Request", []
            body = self._page(prefix, "Bad request", f"<p>missing form field {e(str(exc))}</p>")
        except PluginContractRefusal as exc:
            status, headers = "409 Conflict", []
            body = self._page(prefix, "Refused", f"<pre class='refusal'>{e(str(exc))}</pre>"
                              f"<p><a href='{e(prefix)}/'>back</a></p>")
        data = body.encode("utf-8")
        start_response(status, [("Content-Type", "text/html; charset=utf-8"),
                                ("Content-Length", str(len(data)))] + headers)
        return [data]

    def _route(self, environ: dict, prefix: str, identity: str) -> str:
        method = environ.get("REQUEST_METHOD", "GET")
        parts = [p for p in environ.get("PATH_INFO", "/").split("/") if p]
        form = self._form(environ) if method == "POST" else {}
        if method == "POST":
            self._csrf_check(environ, form, identity)   # nothing below runs if refused
        elif method != "GET":
            raise _Response("405 Method Not Allowed", self._page(prefix, "Not allowed", ""))
        csrf = self.csrf_token(identity)
        if not parts and method == "GET":
            return self._index(prefix, csrf)
        if parts == ["consent"] and method == "GET":
            return self._consent_records(prefix)
        if len(parts) == 2 and parts[0] == "panel" and method == "GET":
            for panel in self.host.extra_panels():
                if panel.slug == parts[1]:
                    return self._page(prefix, panel.title, panel.render(environ, identity))
            raise _Response("404 Not Found", self._page(prefix, "Not found", ""))
        if parts == ["install", "prepare"] and method == "POST":
            return self._consent_screen(prefix, self.manager.prepare_install(form["source"]),
                                        "install/decide", csrf)
        if parts == ["install", "decide"] and method == "POST":
            self.manager.install(form["source"], consented_sha256=form["sha256"],
                                 operator=identity, accepted=form.get("decision") == "accept")
            self._redirect(prefix, "/")
        if len(parts) >= 3 and parts[0] == "plugin" and method == "POST":
            domain, op = parts[1], "/".join(parts[2:])
            m = self.manager
            if op == "enable":
                m.enable(domain)
            elif op == "disable":
                m.disable(domain)
            elif op == "uninstall":
                m.uninstall(domain)
            elif op == "configure":
                import json
                try:
                    cfg = json.loads(form.get("config", "{}"))
                except ValueError as exc:
                    raise PluginContractRefusal(f"config is not JSON: {exc}")
                m.configure(domain, cfg)
            elif op == "upgrade/prepare":
                return self._consent_screen(prefix, m.prepare_upgrade(form["source"]),
                                            f"plugin/{quote(domain)}/upgrade/decide", csrf)
            elif op == "upgrade/decide":
                m.upgrade(form["source"], consented_sha256=form["sha256"], operator=identity,
                          accepted=form.get("decision") == "accept")
            else:
                raise _Response("404 Not Found", self._page(prefix, "Not found", ""))
            self._redirect(prefix, f"/plugin/{quote(domain)}")
        if len(parts) == 2 and parts[0] == "plugin" and method == "GET":
            return self._detail(prefix, parts[1], csrf)
        raise _Response("404 Not Found", self._page(prefix, "Not found", ""))

    # ---- helpers ---------------------------------------------------------- #
    @staticmethod
    def _form(environ: dict) -> "dict[str, str]":
        try:
            n = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            n = 0
        raw = environ["wsgi.input"].read(n).decode("utf-8") if n else ""
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    @staticmethod
    def _redirect(prefix: str, path: str) -> None:
        raise _Response("303 See Other", "", [("Location", f"{prefix}{path}")])

    def _page(self, prefix: str, title: str, body: str) -> str:
        nav = (f"<nav><a href='{e(prefix)}/'>plug-ins</a> | "
               f"<a href='{e(prefix)}/consent'>consent records</a>")
        for panel in self.host.extra_panels():
            nav += f" | <a href='{e(prefix)}/panel/{e(quote(panel.slug))}'>{e(panel.title)}</a>"
        nav += "</nav>"
        return (f"<!doctype html><html><head><meta charset='utf-8'><title>{e(title)}</title>"
                f"</head><body><h1 style='white-space:pre-wrap'>{e(title)}</h1>{nav}{body}"
                f"<footer>host {e(self.host.name)}, contract {e(HOST_CONTRACT)}</footer>"
                f"</body></html>")

    def _plugin_row(self, prefix: str, domain: str) -> str:
        rec = self.manager.record(domain) or {}
        cells = [f"<a href='{e(prefix)}/plugin/{e(quote(domain))}'>{e(domain)}</a>",
                 e(str(rec.get("version", rec.get("last_version", "")))),
                 e(self.manager.state(domain)), e(self.manager.status(domain))]
        cells.append(AUTO_UPGRADE_SWITCH)   # dsd-1a215cd8523: delete this line to hide it
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    @staticmethod
    def _csrf_input(csrf: str) -> str:
        return f"<input type='hidden' name='csrf' value='{e(csrf)}'>"

    def _index(self, prefix: str, csrf: str) -> str:
        rows = "".join(self._plugin_row(prefix, d) for d in self.manager.installs.domains())
        body = (f"<table><tr><th>plug-in</th><th>version</th><th>state</th><th>status</th>"
                f"<th>upgrades</th></tr>{rows}</table>"
                f"<form method='post' action='{e(prefix)}/install/prepare'>"
                f"{self._csrf_input(csrf)}<label>package (directory or wheel) <input name='source'></label> "
                f"<button>review install</button></form>")
        return self._page(prefix, "Plugin Manager", body)

    def _detail(self, prefix: str, domain: str, csrf: str) -> str:
        m = self.manager
        rec = m.record(domain)
        if rec is None:
            raise _Response("404 Not Found", self._page(prefix, "Not found", ""))
        base = f"{e(prefix)}/plugin/{e(quote(domain))}"
        import json
        cfg = e(json.dumps(m.configs.get(domain)))

        def button(op: str, label: str) -> str:
            return (f"<form method='post' action='{base}/{op}'>{self._csrf_input(csrf)}"
                    f"<button>{label}</button></form>")
        body = (f"<p>state: {e(m.state(domain))}; status: {e(m.status(domain))}; "
                f"runs as: {e(str(rec.get('runs_as', '')))}</p>"
                + button("enable", "enable") + button("disable", "disable")
                + button("uninstall", "uninstall")
                + f"<form method='post' action='{base}/configure'>{self._csrf_input(csrf)}"
                  f"<textarea name='config'>"
                  f"{cfg}</textarea><button>save config</button></form>"
                + f"<form method='post' action='{base}/upgrade/prepare'>{self._csrf_input(csrf)}"
                  f"<label>new package "
                  f"<input name='source'></label><button>review upgrade</button></form>")
        return self._page(prefix, f"Plug-in {domain}", body)

    def _consent_screen(self, prefix: str, prompt: ConsentPrompt, action: str,
                        csrf: str) -> str:
        c: ConsentContent = prompt.content
        def item(ln) -> str:
            if ln.kind == "heading":        # the permissions heading, shown once
                return f"<li class='permissions-heading'><strong>{e(ln.value)}</strong></li>"
            body = e(ln.value) if ln.kind == "permission" else f"{e(ln.label)}: {e(ln.value)}"
            return (f"<li class='{'added' if ln.added else ''}'>"
                    + ("<strong>ADDED</strong> " if ln.added else "") + f"{body}</li>")
        items = "".join(item(ln) for ln in c.lines)
        hidden = (self._csrf_input(csrf)
                  + f"<input type='hidden' name='source' value='{e(prompt.source)}'>"
                  f"<input type='hidden' name='sha256' value='{e(prompt.manifest_sha256)}'>")
        decide = "".join(
            f"<form method='post' action='{e(prefix)}/{action}'>{hidden}"
            f"<input type='hidden' name='decision' value='{v}'><button>{label}</button></form>"
            for v, label in (("accept", "accept"), ("refuse", "refuse")))
        note = ("<p class='widens'>This upgrade WIDENS what the plug-in may do; the ADDED "
                "items need your consent.</p>" if c.widens else "")
        # [CXC-PC-CONSENT-CONTENT] dialog-title (r21): the shared renderer's title
        return self._page(prefix, c.title, f"<p>{e(prompt.kind)} {e(c.domain)} "
                          f"{e(c.version)}</p>{note}<ul>{items}</ul>"
                          f"<p>manifest_sha256 {e(prompt.manifest_sha256)}</p>{decide}")

    def _consent_records(self, prefix: str) -> str:
        rows = "".join(
            "<tr>" + "".join(f"<td>{e(str(v))}</td>" for v in (
                r.domain, r.version, r.decision, r.decided_by, r.decided_at, r.host,
                ", ".join(r.granted))) + "</tr>"
            for r in self.manager.consent.records())
        return self._page(prefix, "Consent records",
                          "<table><tr><th>plug-in</th><th>version</th><th>decision</th>"
                          "<th>decided by</th><th>decided at</th><th>host</th>"
                          f"<th>granted</th></tr>{rows}</table>")


def make_app(manager: PluginManager) -> PluginManagerApp:
    return PluginManagerApp(manager)
