"""The plain WSGI Plugin Manager ([CXC-PC-WEB-UI], -UI-URL-PREFIX, -UI-HOST-HOOKS)."""
from __future__ import annotations

import io
import re
from html.parser import HTMLParser
from urllib.parse import urlencode

import pytest

from compose_plugins.errors import PluginContractRefusal
from compose_plugins.host import Panel
from compose_plugins.web import AUTO_UPGRADE_SWITCH, PluginManagerApp

from wave2_helpers import fixture_copy, make

PREFIXES = ["/a/plugins", "/gnomon/config/plugins", ""]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: "list[tuple[str, str]]" = []   # (attr, url)
        self.forms: "list[tuple[str, dict]]" = []
        self._form = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("href", "src", "action"):
            if k in a:
                self.urls.append((k, a[k]))
        if tag == "form":
            self._form = (a.get("action"), {})
            self.forms.append(self._form)
        if tag == "input" and self._form is not None and a.get("type") == "hidden":
            self._form[1][a["name"]] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None


def call(app, prefix, path, method="GET", form=None, ident="op", csrf=True, headers=None,
         raw_body=None):
    """A same-origin browser request. POSTs carry the session's CSRF token, as
    the rendered forms do, unless ``csrf=False``."""
    form = dict(form or {})
    if method == "POST" and csrf and "csrf" not in form and ident:
        form["csrf"] = app.csrf_token(ident)
    body = raw_body if raw_body is not None else urlencode(form).encode()
    environ = {"REQUEST_METHOD": method, "SCRIPT_NAME": prefix, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
               "REMOTE_ADDR": "127.0.0.1", "_ident": ident, "wsgi.url_scheme": "http",
               "HTTP_HOST": "127.0.0.1:8080", **(headers or {})}
    out = {}

    def sr(status, headers):
        out["status"], out["headers"] = status, dict(headers)
    data = b"".join(app(environ, sr)).decode()
    return out["status"], out["headers"], data


def app_for(tmp_path, auth=lambda env: env.get("_ident"), panels=()):
    host, pm = make(tmp_path, auth=auth, panels=panels)
    return host, pm, PluginManagerApp(pm)


def test_no_auth_hook_refuses_to_construct(tmp_path):
    host, pm = make(tmp_path, auth=None)
    with pytest.raises(PluginContractRefusal) as ei:
        PluginManagerApp(pm)
    assert "never defaults to open" in str(ei.value)


def _lifecycle_via_forms(app, prefix, src):
    """Install, configure, enable, disable, uninstall through the forms only.
    Returns every response seen."""
    seen = []
    st, h, page = call(app, prefix, "/install/prepare", "POST", {"source": str(src)})
    seen.append((st, h, page))
    p = Links()
    p.feed(page)
    accept = [f for f in p.forms if f[1].get("decision") == "accept"][0]
    st, h, page = call(app, prefix, accept[0][len(prefix):], "POST", accept[1])
    seen.append((st, h, page))
    for op, form in (("configure", {"config": '{"greeting": "web"}'}), ("enable", {}),
                     ("disable", {}), ("uninstall", {})):
        st, h, page = call(app, prefix, f"/plugin/hello_world/{op}", "POST", form)
        seen.append((st, h, page))
        assert st == "303 See Other", (op, st, page)
    for path in ("/", "/consent", "/plugin/hello_world"):
        seen.append(call(app, prefix, path))
    return seen


@pytest.mark.parametrize("prefix", PREFIXES)
def test_every_link_action_and_location_stays_under_the_prefix(tmp_path, prefix):
    host, pm, app = app_for(tmp_path, panels=[Panel("agents", "Agents",
                                                    lambda env, ident: "<p>agents</p>")])
    seen = _lifecycle_via_forms(app, prefix, fixture_copy(tmp_path, "v1"))
    seen.append(call(app, prefix, "/panel/agents"))
    bad = []
    for st, headers, page in seen:
        p = Links()
        p.feed(page)
        urls = [u for _, u in p.urls] + ([headers["Location"]] if "Location" in headers else [])
        for u in urls:
            if prefix and u.startswith("/") and not u.startswith(prefix + "/"):
                bad.append(u)       # an absolute path outside the active prefix
            if prefix == "" and u.startswith("//"):
                bad.append(u)
    print(f"prefix {prefix!r}: checked {sum(1 for _ in seen)} responses; bad {bad}")
    assert bad == []
    assert pm.state("hello_world") == "uninstalled"


def test_consent_screen_shows_runs_as_and_highlights_added(tmp_path):
    host, pm, app = app_for(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    st, h, page = call(app, "/m", "/install/prepare", "POST", {"source": str(src)})
    assert st == "200 OK" and "runs as: inprocess" in page and "manifest_sha256" in page
    p = Links()
    p.feed(page)
    accept = [f for f in p.forms if f[1].get("decision") == "accept"][0]
    call(app, "/m", accept[0][2:], "POST", accept[1])
    v2 = fixture_copy(tmp_path, "v2")
    st, h, page = call(app, "/m", "/plugin/hello_world/upgrade/prepare", "POST",
                       {"source": str(v2)})
    print(page)
    assert "<li class='added'><strong>ADDED</strong> subscribes: plugins/hello_world/name EVENT" in page
    assert "WIDENS" in page


def test_ui_consent_records_the_authenticated_operator(tmp_path):
    host, pm, app = app_for(tmp_path)
    _lifecycle_via_forms(app, "", fixture_copy(tmp_path, "v1"))
    assert [r.decided_by for r in pm.consent.records()] == ["op"]


def test_refuse_button_installs_nothing(tmp_path):
    host, pm, app = app_for(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    st, h, page = call(app, "", "/install/prepare", "POST", {"source": str(src)})
    p = Links()
    p.feed(page)
    refuse = [f for f in p.forms if f[1].get("decision") == "refuse"][0]
    st, h, page = call(app, "", refuse[0], "POST", refuse[1])
    assert st == "409 Conflict" and pm.record("hello_world") is None


ROUTES = [("GET", "/"), ("GET", "/consent"), ("GET", "/panel/agents"),
          ("GET", "/plugin/hello_world"), ("POST", "/install/prepare"),
          ("POST", "/install/decide"), ("POST", "/plugin/hello_world/enable"),
          ("POST", "/plugin/hello_world/disable"), ("POST", "/plugin/hello_world/uninstall"),
          ("POST", "/plugin/hello_world/configure"),
          ("POST", "/plugin/hello_world/upgrade/prepare"),
          ("POST", "/plugin/hello_world/upgrade/decide"), ("GET", "/nope")]


@pytest.mark.parametrize("method,path", ROUTES)
def test_denying_auth_hook_blocks_every_route_and_no_handler_runs(tmp_path, method, path):
    canary = []
    host, pm, app = app_for(tmp_path, auth=lambda env: None,
                            panels=[Panel("agents", "Agents",
                                          lambda env, ident: canary.append(1) or "x")])
    for name in ("prepare_install", "install", "enable", "disable", "uninstall",
                 "configure", "prepare_upgrade", "upgrade"):
        setattr(pm, name, lambda *a, _n=name, **k: canary.append(_n))
    st, h, page = call(app, "/p", path, method, {"source": "x", "sha256": "y"})
    assert st == "403 Forbidden" and canary == []


def test_extra_panel_is_served_under_the_prefix_and_behind_auth(tmp_path):
    panels = [Panel("agents", "Agents", lambda env, ident: f"<p>hello {ident}</p>")]
    host, pm, app = app_for(tmp_path, panels=panels)
    st, h, page = call(app, "/gnomon/config/plugins", "/")
    assert "href='/gnomon/config/plugins/panel/agents'" in page
    st, h, page = call(app, "/gnomon/config/plugins", "/panel/agents")
    assert st == "200 OK" and "<p>hello op</p>" in page
    st, h, page = call(app, "/gnomon/config/plugins", "/panel/agents", ident=None)
    assert st == "403 Forbidden"


def test_auto_upgrade_switch_is_one_disabled_element(tmp_path):
    host, pm, app = app_for(tmp_path)
    _lifecycle_via_forms(app, "", fixture_copy(tmp_path, "v1"))
    st, h, page = call(app, "", "/")
    assert page.count(AUTO_UPGRADE_SWITCH) == 1
    assert "disabled" in AUTO_UPGRADE_SWITCH
    assert "auto-upgrade arrives with versioned sources (v0.2)" in AUTO_UPGRADE_SWITCH
    import inspect
    from compose_plugins import web
    uses = [ln for ln in inspect.getsource(web).splitlines()
            if "AUTO_UPGRADE_SWITCH" in ln
            and not ln.lstrip().startswith(("AUTO_UPGRADE_SWITCH =", "#"))]
    assert len(uses) == 1, uses           # exactly one line to delete to hide it


def test_no_javascript_anywhere(tmp_path):
    host, pm, app = app_for(tmp_path)
    for st, h, page in _lifecycle_via_forms(app, "", fixture_copy(tmp_path, "v1")):
        assert "<script" not in page.lower() and "javascript:" not in page.lower()


# ---- CSRF ([CXC-PC-UI-HOST-HOOKS] csrf-defence, r18) ------------------------ #
def _installed_and_enabled(tmp_path):
    host, pm, app = app_for(tmp_path)
    _lifecycle_via_forms(app, "", fixture_copy(tmp_path, "v1", name="pkg-a"))
    src = fixture_copy(tmp_path, "v1", name="pkg-b")
    return host, pm, app, src


CSRF_CASES = {
    "cross-site-origin": dict(headers={"HTTP_ORIGIN": "https://evil.example"}),
    "null-origin": dict(headers={"HTTP_ORIGIN": "null"}),
    "sec-fetch-cross-site": dict(headers={"HTTP_SEC_FETCH_SITE": "cross-site"}),
    "no-token": dict(csrf=False),
    "wrong-token": dict(form_extra={"csrf": "0" * 64}),
    "other-operators-token": dict(form_extra={"csrf": "__other__"}),
    "text-plain-no-token": dict(csrf=False, headers={"CONTENT_TYPE": "text/plain"}),
}


@pytest.mark.parametrize("case", sorted(CSRF_CASES))
def test_a_forged_install_decide_changes_nothing_and_records_nothing(tmp_path, case):
    host, pm, app = app_for(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    from compose_plugins.package import read_manifest
    kw = dict(CSRF_CASES[case])
    form = {"source": str(src), "sha256": read_manifest(src).sha256, "decision": "accept"}
    extra = kw.pop("form_extra", {})
    if extra.get("csrf") == "__other__":
        extra = {"csrf": app.csrf_token("someone-else")}
    form.update(extra)
    st, h, page = call(app, "", "/install/decide", "POST", form, **kw)
    print(case, st)
    assert st == "403 Forbidden"
    assert pm.record("hello_world") is None and pm.consent.records() == []
    assert list(host.install_root.iterdir()) == [] and host.gate_calls == []


@pytest.mark.parametrize("op", ["enable", "configure", "uninstall"])
def test_forged_state_changes_on_an_installed_plugin_are_refused(tmp_path, op):
    host, pm, app = app_for(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    st, h, page = call(app, "", "/install/prepare", "POST", {"source": str(src)})
    p = Links()
    p.feed(page)
    accept = [f for f in p.forms if f[1].get("decision") == "accept"][0]
    call(app, "", accept[0], "POST", accept[1])
    before = (pm.state("hello_world"), pm.configs.get("hello_world"), len(pm.consent.records()))
    st, _, _ = call(app, "", f"/plugin/hello_world/{op}", "POST", {"config": '{"greeting": "x"}'},
                    headers={"HTTP_ORIGIN": "https://evil.example",
                             "HTTP_SEC_FETCH_SITE": "cross-site"}, csrf=False)
    assert st == "403 Forbidden"
    assert (pm.state("hello_world"), pm.configs.get("hello_world"),
            len(pm.consent.records())) == before


def test_same_origin_request_with_the_token_is_accepted(tmp_path):
    host, pm, app = app_for(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    st, h, page = call(app, "", "/install/prepare", "POST", {"source": str(src)},
                       headers={"HTTP_ORIGIN": "http://127.0.0.1:8080",
                                "HTTP_SEC_FETCH_SITE": "same-origin"})
    assert st == "200 OK" and "name='csrf'" in page


def test_every_rendered_form_carries_the_token(tmp_path):
    host, pm, app = app_for(tmp_path)
    seen = _lifecycle_via_forms(app, "", fixture_copy(tmp_path, "v1"))
    install_fixture = fixture_copy(tmp_path, "v1", name="again")
    seen.append(call(app, "", "/install/prepare", "POST", {"source": str(install_fixture)}))
    forms = 0
    for st, hd, page in seen:
        for m in re.finditer(r"<form [^>]*>(.*?)</form>", page, re.S):
            forms += 1
            assert f"name='csrf' value='{app.csrf_token('op')}'" in m.group(1)
    assert forms >= 6


def test_tokens_differ_per_operator_and_per_app(tmp_path):
    host, pm, app = app_for(tmp_path)
    other = PluginManagerApp(pm)
    assert app.csrf_token("a") != app.csrf_token("b")
    assert app.csrf_token("a") != other.csrf_token("a")


def test_r21_consent_screen_title_and_permission_lines(tmp_path):
    """[CXC-PC-CONSENT-CONTENT] dialog-title + permission-lines (r21) in the
    web UI: the title from the manifest (both spaces kept on screen), the
    heading once, each permission one line with its purpose, no per-line mark."""
    import html
    host, pm, app = app_for(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    st, h, page = call(app, "/m", "/install/prepare", "POST", {"source": str(src)})
    title = "Hello World 1.0.0  Manifest: Declared by its Developer"
    assert f"<h1 style='white-space:pre-wrap'>{html.escape(title, quote=True)}</h1>" in page
    assert "Declared by the plug-in; not checked" not in page
    perms = {"network": [{"direction": "outbound", "target": "printer.local:631",
                          "purpose": "print via IPP"}],
             "filesystem": [{"path": "/tmp/hello", "access": "read", "purpose": "read log"}]}
    src2 = fixture_copy(tmp_path, "v1", edit=lambda d: d.update(permissions=perms),
                        name="pkg-perms")
    st, h, page = call(app, "/m", "/install/prepare", "POST", {"source": str(src2)})
    print(page)
    assert page.count("Declared by the plug-in; not checked") == 1
    assert "<li class=''>outbound printer.local:631: print via IPP</li>" in page
    assert "<li class=''>read /tmp/hello: read log</li>" in page
