"""Contract 0.2, item 4: the two-step uninstall confirm in the library's web
manager ([DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA] section 5).

The one non-additive change of 0.2: ``POST .../uninstall`` no longer uninstalls.
"""
from __future__ import annotations

import html
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from v02_helpers import consent_lines, log_lines, snapshot, web_app, wsgi
from wave2_helpers import fixture_copy, install, wait_for

D = "hello_world"
GREETING = "plugins/hello_world/greeting"


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.urls, self.forms, self._form, self.items = [], [], None, []
        self._li = None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for k in ("href", "src", "action"):
            if k in a:
                self.urls.append(a[k])
        if tag == "form":
            self._form = {"action": a.get("action"), "hidden": {}, "buttons": []}
            self.forms.append(self._form)
        if tag == "input" and self._form is not None and a.get("type") == "hidden":
            self._form["hidden"][a["name"]] = a.get("value", "")
        if tag == "button" and self._form is not None:
            self._form["buttons"].append((a.get("name"), a.get("value")))
        if tag == "li":
            self._li = ""

    def handle_data(self, data):
        if self._li is not None:
            self._li += data

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        if tag == "li" and self._li is not None:
            self.items.append(self._li)
            self._li = None


def _frozen(host):
    ip = host.state_root / "installs.json"
    return ip.read_bytes(), snapshot(host.install_root)


def _setup(tmp_path, *, state_topic=True):
    host, pm, app = web_app(tmp_path)
    if state_topic:
        install(pm, fixture_copy(tmp_path, "v1"))
        pm.configure(D, {"greeting": "hi"})
        pm.enable(D)
        assert wait_for(lambda: host.bus().get_state(GREETING) is not None)
    else:
        def no_state(d):
            d["bus"]["publishes"] = [e for e in d["bus"]["publishes"] if e["type"] != "STATE"]
        install(pm, fixture_copy(tmp_path, "v1", edit=no_state, name="nostate"))
    return host, pm, app


@pytest.mark.parametrize("prefix", ["", "/a/plugins"])
@pytest.mark.parametrize("state_topic", [True, False], ids=["one-STATE-topic", "no-STATE"])
def test_CXC_PC_V02_T_UNINSTALL_CONFIRM_FLOW(tmp_path, prefix, state_topic):
    host, pm, app = _setup(tmp_path, state_topic=state_topic)
    seen = []
    # the detail page posts to .../uninstall/prepare, never to .../uninstall
    st, hd, detail = wsgi(app, prefix, f"/plugin/{D}")
    seen.append((hd, detail))
    actions = [f["action"] for f in Page(detail).forms]
    assert not any(a.endswith("/uninstall") for a in actions)
    assert sum(a.endswith("/uninstall/prepare") for a in actions) == 1
    frozen, n_events, n_consent = _frozen(host), len(log_lines(pm)), len(consent_lines(pm))
    screens = []
    for route in ("uninstall", "uninstall/prepare"):          # one click changes nothing
        st, hd, page = wsgi(app, prefix, f"/plugin/{D}/{route}", "POST")
        assert st == "200 OK", page
        seen.append((hd, page))
        screens.append(page)
        assert _frozen(host) == frozen and pm.state(D) in ("enabled", "installed")
    assert screens[0] == screens[1]
    screen = Page(screens[0])
    rec = pm.record(D)
    assert f"<title>Uninstall Hello World 1.0.0?</title>" in screens[0]
    topic = ([f"clears retained topic {GREETING} (tombstone)"] if state_topic
             else ["no retained topics to clear"])
    assert screen.items == [f"removes the installed code of {D}",
                            f"removes the stored config of {D}", *topic,
                            "keeps the consent records and the install history"]
    (form,) = [f for f in screen.forms if f["action"].endswith("/uninstall/decide")]
    assert form["hidden"]["sha256"] == rec["manifest_sha256"]
    assert form["hidden"]["version"] == rec["version"] and "csrf" in form["hidden"]
    assert form["buttons"] == [("decision", "cancel"), ("decision", "confirm")]
    assert screens[0].index("value='cancel'") < screens[0].index("value='confirm'")
    decide = form["action"][len(prefix):]
    hidden = form["hidden"]
    # cancel, a missing decision, an unknown one and a repeated one all cancel
    for pairs in ([("decision", "cancel")], [], [("decision", "yes")],
                  [("decision", "confirm"), ("decision", "confirm")]):
        st, hd, page = wsgi(app, prefix, decide, "POST", hidden, pairs=pairs)
        assert st == "303 See Other" and hd["Location"] == f"{prefix}/plugin/{D}", pairs
        seen.append((hd, page))
        assert _frozen(host) == frozen
    assert (len(log_lines(pm)), len(consent_lines(pm))) == (n_events, n_consent)
    # confirm uninstalls
    code = Path(rec["path"])
    st, hd, page = wsgi(app, prefix, decide, "POST", hidden, pairs=[("decision", "confirm")])
    seen.append((hd, page))
    assert st == "303 See Other" and hd["Location"] == f"{prefix}/"
    assert pm.state(D) == "uninstalled" and not code.exists()
    assert not pm.configs.exists(D)
    if state_topic:
        assert host.bus().get_state(GREETING) is None
    assert len(consent_lines(pm)) == n_consent
    assert len(log_lines(pm)) == n_events + 1 and log_lines(pm)[-1]["event"] == "uninstall"
    for path in ("/", "/consent"):
        seen.append(wsgi(app, prefix, path)[1:])
    # the URL-prefix crawl covers the new screen
    bad = []
    for hd, page in seen:
        urls = Page(page).urls + ([hd["Location"]] if "Location" in hd else [])
        for u in urls:
            if prefix and u.startswith("/") and not u.startswith(prefix + "/"):
                bad.append(u)
            if not prefix and u.startswith("//"):
                bad.append(u)
    assert bad == []


def test_CXC_PC_V02_T_UNINSTALL_CONFIRM_FLOW_csrf_and_auth(tmp_path, monkeypatch):
    host, pm, app = _setup(tmp_path)
    frozen = _frozen(host)
    ran = []
    monkeypatch.setattr(pm, "uninstall", lambda *a, **k: ran.append("uninstall"))
    real_screen = app._uninstall_confirm_screen
    monkeypatch.setattr(app, "_uninstall_confirm_screen",
                        lambda *a, **k: (ran.append("screen"), real_screen(*a, **k))[1])
    rec = pm.record(D)
    hidden = {"sha256": rec["manifest_sha256"], "version": rec["version"], "decision": "confirm"}
    for route in ("uninstall", "uninstall/prepare", "uninstall/decide"):
        st, _, _ = wsgi(app, "", f"/plugin/{D}/{route}", "POST", {**hidden, "csrf": "wrong"})
        assert st == "403 Forbidden"
        st, _, _ = wsgi(app, "", f"/plugin/{D}/{route}", "POST", hidden, ident=None)
        assert not st.startswith("2")
    assert ran == [] and _frozen(host) == frozen and pm.state(D) == "enabled"


def test_CXC_PC_V02_T_UNINSTALL_CONFIRM_FLOW_prepare_from_a_refused_state(tmp_path):
    host, pm, app = web_app(tmp_path)
    st, hd, page = wsgi(app, "", f"/plugin/{D}/uninstall/prepare", "POST")
    assert st == "409 Conflict" and "uninstall REFUSED" in html.unescape(page)
    assert "confirm uninstall" not in page


# =========================================================================== #
# [CXC-PC-V02-T-UNINSTALL-CONFIRM-STALE]
# =========================================================================== #
def _screen_form(app, prefix=""):
    st, hd, page = wsgi(app, prefix, f"/plugin/{D}/uninstall/prepare", "POST")
    assert st == "200 OK", page
    (form,) = [f for f in Page(page).forms if f["action"].endswith("/uninstall/decide")]
    return form["action"][len(prefix):], form["hidden"]


def _confirm(app, action, hidden, drop=()):
    form = {k: v for k, v in hidden.items() if k not in drop}
    st, hd, page = wsgi(app, "", action, "POST", form, pairs=[("decision", "confirm")])
    return st, html.unescape(page)


def test_CXC_PC_V02_T_UNINSTALL_CONFIRM_STALE(tmp_path):
    host, pm, app = web_app(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    sha1 = pm.record(D)["manifest_sha256"]
    # shown v1, upgraded to v2 by the API: refused, v2 stays
    action, hidden = _screen_form(app)
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    sha2 = pm.record(D)["manifest_sha256"]
    st, page = _confirm(app, action, hidden)
    assert st == "409 Conflict"
    assert (f"uninstall confirm is stale: {D} changed since the screen was shown "
            f"(shown 1.0.0 {sha1}, now 2.0.0 {sha2})") in page
    assert pm.state(D) == "installed" and pm.record(D)["version"] == "2.0.0"
    # a state change that still allows uninstall is not stale
    pm.enable(D)
    action, hidden = _screen_form(app)
    pm.disable(D)
    st, page = _confirm(app, action, hidden)
    assert st == "303 See Other" and pm.state(D) == "uninstalled"
    # shown, then uninstalled by the API: the transition refusal names the state
    install(pm, fixture_copy(tmp_path, "v1", name="again"))
    action, hidden = _screen_form(app)
    pm.uninstall(D)
    n = len(log_lines(pm))
    st, page = _confirm(app, action, hidden)
    assert st == "409 Conflict" and "'uninstalled'" in page and "uninstall REFUSED" in page
    assert len(log_lines(pm)) == n
    # a confirm without its hidden sha256 is refused as stale, naming it
    install(pm, fixture_copy(tmp_path, "v1", name="third"))
    action, hidden = _screen_form(app)
    st, page = _confirm(app, action, hidden, drop=("sha256",))
    assert st == "409 Conflict" and "uninstall confirm is stale" in page and "sha256" in page
    assert pm.state(D) == "installed"
