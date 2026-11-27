"""Contract 0.2, item 1: the version gate, `license`, `eula`, the EULA on the
consent screen, the host obligation, the EULA digest in the consent record and
re-consent on a changed EULA ([DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA] section 2).

Each test is named after the delta's test node id.
"""
from __future__ import annotations

import builtins
import html
import importlib
import json
import os
import re
import socket
from pathlib import Path

import pytest

from compose_plugins.errors import ManifestRefused, PluginContractRefusal
from compose_plugins.lifecycle import ConsentDenied
from compose_plugins.manifest import validate_manifest
from compose_plugins.package import open_package

from v02_helpers import (
    EULA_TEXT, GOLDEN, consent_lines, cp, eula_package, install_eula, sha, upgrade_eula,
    web_app, wheel_of, wsgi,
)
from wave2_helpers import fixture_copy, install, make


def _validate(src):
    data = json.loads((Path(src) / "manifest.json").read_text())
    return validate_manifest(data, host="testhost", section_validator=lambda s: [],
                             package=open_package(src))


def _outcome(pm, src):
    """("installed", None) or ("refused", ManifestRefused)."""
    try:
        install_eula(pm, src)
    except ManifestRefused as exc:
        return "refused", exc
    return "installed", None


# =========================================================================== #
# [CXC-PC-V02-T-VERSION-GATES-THE-NEW-KEYS]
# =========================================================================== #
VERSION_ROWS = [
    # id, contract, add-license, add-eula, expected, named
    ("0.1-plain", "0.1", False, False, "installed", None),
    ("0.1-license", "0.1", True, False, "refused", "license"),
    ("0.1-eula", "0.1", False, True, "refused", "eula"),
    ("0.2-plain", "0.2", False, False, "installed", None),
    ("0.2-license", "0.2", True, False, "installed", None),
    ("0.2-eula", "0.2", False, True, "installed", None),
    ("0.2-both", "0.2", True, True, "installed", None),
    ("0.3-plain", "0.3", False, False, "refused", "contract"),
]


@pytest.mark.parametrize("rid,contract,lic,eula,expected,named", VERSION_ROWS,
                         ids=[r[0] for r in VERSION_ROWS])
def test_CXC_PC_V02_T_VERSION_GATES_THE_NEW_KEYS(tmp_path, rid, contract, lic, eula,
                                                 expected, named):
    src = eula_package(tmp_path, contract=contract, path="EULA.txt" if eula else None,
                       text=EULA_TEXT if eula else None, license="MIT" if lic else None)
    # through the validator
    try:
        _validate(src)
        v_out = "accepted"
    except ManifestRefused as exc:
        v_out, v_exc = "refused", exc
    # through install
    host, pm = make(tmp_path / "h")
    out, exc = _outcome(pm, src)
    print(rid, v_out, out, exc)
    assert out == expected and (v_out == "accepted") == (expected == "installed")
    if expected == "refused":
        assert exc.fields == [named] and v_exc.fields == [named]
        if named in ("license", "eula"):
            assert "defined from contract 0.2" in str(exc)
        else:
            assert "0.3" in str(exc) and cp().version.HOST_CONTRACT in str(exc)
    else:
        assert pm.state("hello_world") == "installed"


def test_CXC_PC_V02_T_VERSION_GATES_THE_NEW_KEYS_constant_and_schema():
    from compose_plugins.manifest import schema
    from compose_plugins.version import HOST_CONTRACT
    assert HOST_CONTRACT == "0.2"
    s = schema()
    assert s["x-compose-plugins-contract"] == HOST_CONTRACT
    assert "license" in s["properties"] and "eula" in s["properties"]
    assert "license" not in s["required"] and "eula" not in s["required"]
    assert "defined from contract 0.2" in s["description"]
    assert cp().__version__ == "0.2.0"


# =========================================================================== #
# [CXC-PC-V02-T-LICENSE-TABLE]
# =========================================================================== #
LICENSE_REFUSED = [123, "", "M" * 257, "MITé", "mit and apache-2.0", "MIT Apache-2.0",
                   "(MIT OR Apache-2.0", "MIT OR", "AND", "NONE", "NOASSERTION",
                   "GPL-2.0-only WITH", "MIT  OR Apache-2.0"]
LICENSE_ACCEPTED = ["MIT", "Apache-2.0", "Apache-2.0+", "(MIT OR Apache-2.0)",
                    "GPL-3.0-or-later WITH Classpath-exception-2.0", "LicenseRef-Proprietary",
                    "DocumentRef-spdx-tool-1.2:LicenseRef-MyLicense",
                    "MIT AND (Apache-2.0 OR BSD-3-Clause)"]


@pytest.mark.parametrize("value", LICENSE_REFUSED, ids=[repr(v)[:24] for v in LICENSE_REFUSED])
def test_CXC_PC_V02_T_LICENSE_TABLE_boundary_refused(tmp_path, value):
    src = eula_package(tmp_path, path=None, text=None, edit=lambda d: d.update(license=value))
    host, pm = make(tmp_path / "h")
    out, exc = _outcome(pm, src)
    print(repr(value), exc)
    assert out == "refused" and exc.fields == ["license"]
    assert pm.record("hello_world") is None


@pytest.mark.parametrize("value", LICENSE_ACCEPTED)
def test_CXC_PC_V02_T_LICENSE_TABLE_accepted(tmp_path, value):
    src = eula_package(tmp_path, path=None, text=None, license=value)
    host, pm = make(tmp_path / "h")
    assert _outcome(pm, src) == ("installed", None)


def test_CXC_PC_V02_T_LICENSE_TABLE_no_consent_effect(tmp_path):
    with_l = eula_package(tmp_path, path=None, text=None, license="MIT")
    without = eula_package(tmp_path, path=None, text=None)
    host, pm = make(tmp_path / "h")
    p_with, p_without = pm.prepare_install(with_l), pm.prepare_install(without)
    assert p_with.content.granted == p_without.content.granted
    lines = [ln.text() for ln in p_with.content.lines]
    lic = [t for t in lines if t.startswith("license")]
    assert lic == ["license: MIT (declared by the plug-in; not verified)"]
    labels = [ln.label for ln in p_with.content.lines]
    assert labels.index("documentation") + 1 == labels.index("license") \
        and labels.index("license") + 1 == labels.index("run form")
    assert not any(t.startswith("license") for t in
                   (ln.text() for ln in p_without.content.lines))
    # a licence-only upgrade asks no one
    install_eula(pm, with_l)
    v2 = eula_package(tmp_path, path=None, text=None, license="Apache-2.0")
    calls = len(host.gate_calls)
    upgrade_eula(pm, v2)
    assert len(host.gate_calls) == calls
    assert pm.record("hello_world")["manifest"]["license"] == "Apache-2.0"


# =========================================================================== #
# [CXC-PC-V02-T-EULA-FIELD-TABLE]
# =========================================================================== #
def _bad_eula_rows():
    big = b"a" * 262145
    return [
        # id, eula-path, file bytes (None: none written), reason needle, dir/wheel
        ("not-a-string", 7, None, "must be a string", "both"),
        ("absolute", "/etc/x", None, "absolute", "both"),
        ("dotdot", "legal/../EULA.txt", EULA_TEXT, "'..'", "both"),
        ("backslash", "legal\\EULA.txt", EULA_TEXT, "backslash", "both"),
        ("empty-segment", "legal//EULA.txt", EULA_TEXT, "empty segment", "both"),
        ("trailing-slash", "legal/", None, "trailing", "both"),
        ("missing", "NOPE.txt", None, "does not exist", "both"),
        ("directory", "legal", None, "directory", "both"),
        ("symlink", "LINK.txt", None, "symlink", "both"),
        ("through-symlink", "linkdir/EULA.txt", None, "symlink", "both"),
        ("empty", "EULA.txt", b"", "is empty", "both"),
        ("whitespace-only", "EULA.txt", b" \n\t\r\n", "whitespace", "both"),
        ("too-large", "EULA.txt", big, "262144", "both"),
        ("invalid-utf8", "EULA.txt", b"ok\xff\xfe", "UTF-8", "both"),
        ("bom", "EULA.txt", "﻿hello".encode(), "byte-order mark", "both"),
        ("nul", "EULA.txt", b"a\x00b", "NUL", "both"),
        ("esc", "EULA.txt", b"a\x1b[31mred", "control character", "both"),
        ("bidi", "EULA.txt", "a‮b".encode(), "bidirectional", "both"),
        ("contract-0.1", "EULA.txt", EULA_TEXT, "defined from contract 0.2", "both"),
    ]


def _make_row_package(tmp_path, rid, path, data):
    """The row's package: a valid legal/EULA.txt always exists, and EULA.txt
    holds the row's bytes (or a valid text when the row names another path)."""
    contract = "0.1" if rid == "contract-0.1" else "0.2"
    src = eula_package(tmp_path, path=path, contract=contract, text=None)
    (src / "legal").mkdir()
    (src / "legal" / "EULA.txt").write_text(EULA_TEXT)
    (src / "EULA.txt").write_bytes(data if isinstance(data, bytes) else EULA_TEXT.encode())
    symlinks = {}
    if rid == "symlink":
        os.symlink("legal/EULA.txt", src / "LINK.txt")
        symlinks["LINK.txt"] = "legal/EULA.txt"
    if rid == "through-symlink":
        os.symlink("legal", src / "linkdir")
        symlinks["linkdir"] = "legal"
    return src, symlinks


@pytest.mark.parametrize("kind", ["directory", "wheel"])
@pytest.mark.parametrize("row", _bad_eula_rows(), ids=[r[0] for r in _bad_eula_rows()])
def test_CXC_PC_V02_T_EULA_FIELD_TABLE_boundary_refused(tmp_path, row, kind, monkeypatch):
    rid, path, data, needle, _ = row
    src, symlinks = _make_row_package(tmp_path, rid, path, data)
    if kind == "wheel":
        if rid == "through-symlink":
            # a symlink entry named linkdir, and a member under it
            import zipfile
            whl = wheel_of(src, tmp_path, symlinks={"linkdir": "legal"})
            with zipfile.ZipFile(whl, "a") as zf:
                zf.writestr("hello_world/linkdir/EULA.txt", EULA_TEXT)
            src = whl
        else:
            src = wheel_of(src, tmp_path, symlinks=symlinks)
    host, pm = make(tmp_path / "h")
    imports, conns = _spies(monkeypatch)
    out, exc = _outcome(pm, src)
    print(rid, kind, exc)
    assert out == "refused" and exc.fields == ["eula"], (rid, kind, exc)
    assert needle in str(exc), (rid, kind, str(exc))
    assert imports == [] and conns == []
    assert pm.record("hello_world") is None and list(host.install_root.iterdir()) == []


def _spies(monkeypatch):
    imports, conns = [], []
    real_import = builtins.__import__
    real_im = importlib.import_module

    def spy_import(name, *a, **kw):
        if name.split(".")[0] == "hello_world":
            imports.append(name)
        return real_import(name, *a, **kw)

    def spy_im(name, *a, **kw):
        if name.split(".")[0] == "hello_world":
            imports.append(name)
        return real_im(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", spy_import)
    monkeypatch.setattr(importlib, "import_module", spy_im)
    real_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect",
                        lambda self, *a: (conns.append(a), real_connect(self, *a))[1])
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: conns.append(a))
    return imports, conns


EULA_ACCEPTED = [
    ("EULA.txt", "EULA.txt", EULA_TEXT.encode()),
    ("legal/EULA.txt", "legal/EULA.txt", EULA_TEXT.encode()),
    ("crlf", "EULA.txt", b"line one\r\nline two\r\n"),
    ("u2028", "EULA.txt", "one two".encode()),
    ("max-size", "EULA.txt", b"a" * 262144),
]


@pytest.mark.parametrize("kind", ["directory", "wheel"])
@pytest.mark.parametrize("rid,path,data", EULA_ACCEPTED, ids=[r[0] for r in EULA_ACCEPTED])
def test_CXC_PC_V02_T_EULA_FIELD_TABLE_accepted(tmp_path, rid, path, data, kind, monkeypatch):
    src = eula_package(tmp_path, path=path, text=data)
    if kind == "wheel":
        src = wheel_of(src, tmp_path)
    host, pm = make(tmp_path / "h")
    imports, conns = _spies(monkeypatch)
    p = pm.prepare_install(src)
    assert imports == [] and conns == []
    assert p.content.eula.text == data.decode("utf-8")
    assert p.content.eula.sha256 == sha(data) and p.content.eula.path == path
    install_eula(pm, src)
    assert pm.state("hello_world") == "installed"


# =========================================================================== #
# [CXC-PC-V02-T-EULA-RENDERED-VERBATIM]
# =========================================================================== #
VERBATIM = "<b>&amp;</b>\tTAB \"q\" 'q' <script>alert(1)</script>\r\nCRLF line after\n"
EULA_HEADING = "License agreement (EULA) declared by the plug-in"


def _eula_block(page: str) -> "tuple[int, str]":
    m = re.search(r"<pre>(.*?)</pre>", page, re.S)
    assert m is not None, "no <pre> block"
    return m.start(), m.group(1)


def test_CXC_PC_V02_T_EULA_RENDERED_VERBATIM(tmp_path):
    host, pm, app = web_app(tmp_path)
    perms = {"network": [{"direction": "outbound", "target": "printer.example:631",
                          "purpose": "print via IPP"}]}
    src = eula_package(tmp_path, text=VERBATIM, edit=lambda d: d.update(permissions=perms))
    st, h, page = wsgi(app, "/m", "/install/prepare", "POST", {"source": str(src)})
    assert st == "200 OK", page
    at, pre = _eula_block(page)
    assert html.unescape(pre) == VERBATIM
    assert "<script>" not in page            # shown as characters, never executed
    heading = page.index(EULA_HEADING)
    last_perm = page.rindex("outbound printer.example:631: print via IPP")
    first_accept = page.index("value='accept'")
    assert last_perm < heading < at < first_accept
    assert f"EULA sha256: {sha(VERBATIM.encode())}" in page
    assert page.index(f"EULA sha256: {sha(VERBATIM.encode())}") < first_accept
    assert f"name='eula_sha256' value='{sha(VERBATIM.encode())}'" in page
    # 200 KiB appears in full
    big = ("x" * 99 + "\n") * 2048
    src2 = eula_package(tmp_path, text=big)
    st, h, page = wsgi(app, "/m", "/install/prepare", "POST", {"source": str(src2)})
    assert html.unescape(_eula_block(page)[1]) == big and len(big) == 204800


def test_CXC_PC_V02_T_EULA_RENDERED_VERBATIM_absent_is_0_1(tmp_path):
    """No `eula`: the consent content of hello-world v1 is byte-identical to the
    0.1 library's (tests/data/v01_golden, captured from the 0.1.0 tree), and so
    is the consent screen apart from the footer's contract number."""
    host, pm, app = web_app(tmp_path)
    src = fixture_copy(tmp_path, "v1")
    p = pm.prepare_install(src)
    assert p.content.eula is None and p.content.eula_changed is False
    assert p.content.text() + "\n" == (GOLDEN / "hello_world_v1_consent_text.txt").read_text()
    st, h, page = wsgi(app, "", "/install/prepare", "POST", {"source": str(src)})
    norm = page.replace(str(src), "{SOURCE}").replace(app.csrf_token("op"), "{CSRF}")
    golden = (GOLDEN / "hello_world_v1_consent_screen.html").read_text()
    assert "<pre>" not in page and EULA_HEADING not in page and "eula_sha256" not in page
    # the ONE difference: the footer names the contract this library implements
    assert norm == golden.replace(", contract 0.1</footer>", ", contract 0.2</footer>")


# =========================================================================== #
# [CXC-PC-V02-T-EULA-HOST-OBLIGATION]
# =========================================================================== #
def test_CXC_PC_V02_T_EULA_HOST_OBLIGATION(tmp_path):
    from compose_plugins.fixtures import package_dir
    fx = package_dir("hello-world-eula")
    data = json.loads((fx / "manifest.json").read_text())
    assert data["contract"] == "0.2" and isinstance(data["eula"], str)
    text = (fx / data["eula"]).read_text(encoding="utf-8")
    assert text.count("\n") >= 3 and "\t" in text and not text.isascii() and len(text) > 1000
    shown = cp().eula_text_shown
    host, pm, app = web_app(tmp_path)
    src = fixture_copy(tmp_path, "eula")
    p = pm.prepare_install(src)
    st, h, page = wsgi(app, "", "/install/prepare", "POST", {"source": str(src)})
    assert st == "200 OK"
    assert shown(page, p) is True
    esc = html.escape(text)
    assert esc in page
    truncated = page.replace(esc, html.escape(text[:1000]) + "<a href='more'>more</a>")
    flattened = page.replace(esc, html.escape(text.replace("\n", " ")))
    other = page.replace(esc, html.escape("z" * len(text)))
    for degraded in (truncated, flattened, other):
        assert degraded != page and shown(degraded, p) is False
    # plain text (a host whose screen is not HTML)
    assert shown(p.content.text(), p) is True
    assert shown(p.content.text().replace("\t", "    "), p) is False


# =========================================================================== #
# [CXC-PC-V02-T-EULA-HASH-BINDING]
# =========================================================================== #
def test_CXC_PC_V02_T_EULA_HASH_BINDING(tmp_path):
    EulaShaMismatch = cp().EulaShaMismatch
    a, b = "Text A: you agree.\n", "Text B: you obey!!\n"
    assert len(a) == len(b)
    # (a) the file changes after the screen: refused naming both digests
    host, pm = make(tmp_path / "a")
    src = eula_package(tmp_path, text=a)
    p = pm.prepare_install(src)
    (src / "EULA.txt").write_text(b)
    with pytest.raises(EulaShaMismatch) as ei:
        pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True,
                   consented_eula_sha256=sha(a.encode()))
    print(ei.value)
    assert sha(a.encode()) in str(ei.value) and sha(b.encode()) in str(ei.value)
    assert list(host.install_root.iterdir()) == [] and consent_lines(pm) == []
    assert host.gate_calls == [] and pm.record("hello_world") is None
    # (b) unchanged: installs; record and installs.json carry the file's digest
    (src / "EULA.txt").write_text(a)
    pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True,
               consented_eula_sha256=sha(a.encode()))
    digest = sha((src / "EULA.txt").read_bytes())
    assert consent_lines(pm)[-1]["eula_sha256"] == digest
    assert json.loads((host.state_root / "installs.json").read_text())["hello_world"][
        "eula_sha256"] == digest
    # (c) a refusing gate records the key too
    host2, pm2 = make(tmp_path / "c")
    p2 = pm2.prepare_install(src)
    with pytest.raises(ConsentDenied):
        pm2.install(src, consented_sha256=p2.manifest_sha256, operator="op", accepted=False,
                    consented_eula_sha256=p2.content.eula.sha256)
    (rec,) = consent_lines(pm2)
    assert rec["decision"] == "refused" and rec["eula_sha256"] == digest
    # (d) no eula: exactly the eight 0.1 keys
    host3, pm3 = make(tmp_path / "d")
    install(pm3, fixture_copy(tmp_path, "v1"))
    assert set(consent_lines(pm3)[0]) == {"domain", "version", "manifest_sha256", "granted",
                                          "decided_by", "decided_at", "host", "decision"}
    # (e) the reader refuses a non-hex digest and any other extra key, naming it
    from compose_plugins.consent import ConsentRecord, ConsentStore
    from compose_plugins.errors import ConsentRefused
    good = consent_lines(pm)[-1]
    for bad, key in (({**good, "eula_sha256": "XYZ"}, "eula_sha256"),
                     ({**good, "eula_sha256": None}, "eula_sha256"),
                     ({**good, "kind": "install"}, "kind")):
        with pytest.raises(ConsentRefused) as ei:
            ConsentRecord.from_json(bad)
        assert key in str(ei.value)
        store = tmp_path / f"store-{key}.jsonl"
        store.write_text(json.dumps(bad) + "\n")
        with pytest.raises(ConsentRefused) as ei:
            ConsentStore(store).records()
        assert key in str(ei.value)
    assert ConsentRecord.from_json(good).eula_sha256 == digest
    # (f) an upgrade to a package without eula removes the key from the record
    plain = eula_package(tmp_path, path=None, text=None)
    upgrade_eula(pm, plain)
    assert "eula_sha256" not in pm.record("hello_world")
    assert "eula_sha256" not in json.loads(
        (host.state_root / "installs.json").read_text())["hello_world"]


def test_CXC_PC_V02_T_EULA_HASH_BINDING_argument_rules(tmp_path):
    EulaShaMismatch = cp().EulaShaMismatch
    host, pm = make(tmp_path / "h")
    src = eula_package(tmp_path)
    p = pm.prepare_install(src)
    for arg in (None, "abc", "A" * 64):
        with pytest.raises(EulaShaMismatch) as ei:
            pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True,
                       **({} if arg is None else {"consented_eula_sha256": arg}))
        assert "consented_eula_sha256" in str(ei.value)
    plain = fixture_copy(tmp_path, "v1")
    pp = pm.prepare_install(plain)
    with pytest.raises(PluginContractRefusal) as ei:
        pm.install(plain, consented_sha256=pp.manifest_sha256, operator="op", accepted=True,
                   consented_eula_sha256=sha(b"x"))
    assert "consented_eula_sha256" in str(ei.value)
    assert host.gate_calls == [] and consent_lines(pm) == [] and pm.record("hello_world") is None


# =========================================================================== #
# [CXC-PC-V02-T-EULA-RECONSENT-TABLE]
# =========================================================================== #
A_TEXT, B_TEXT = "EULA A\n", "EULA B\n"
RECONSENT_ROWS = [("none-none", None, None, False), ("none-A", None, A_TEXT, True),
                  ("A-A", A_TEXT, A_TEXT, False), ("A-B", A_TEXT, B_TEXT, True),
                  ("A-none", A_TEXT, None, False)]


def _side(tmp_path, text, version_label):
    return eula_package(tmp_path, path="EULA.txt" if text else None, text=text,
                        edit=lambda d: d.update(version=version_label))


@pytest.mark.parametrize("rid,i_text,n_text,gate", RECONSENT_ROWS,
                         ids=[r[0] for r in RECONSENT_ROWS])
def test_CXC_PC_V02_T_EULA_RECONSENT_TABLE(tmp_path, rid, i_text, n_text, gate):
    host, pm = make(tmp_path / "h")
    install_eula(pm, _side(tmp_path, i_text, "1.0.0"))
    before_gate, before_records = len(host.gate_calls), len(consent_lines(pm))
    new = _side(tmp_path, n_text, "1.0.1")
    p = pm.prepare_upgrade(new)
    assert p.content.eula_changed is gate
    upgrade_eula(pm, new)
    assert (len(host.gate_calls) - before_gate) == (1 if gate else 0)
    assert (len(consent_lines(pm)) - before_records) == (1 if gate else 0)
    assert pm.record("hello_world")["version"] == "1.0.1"


def test_CXC_PC_V02_T_EULA_RECONSENT_TABLE_widening_and_eula_one_gate_call(tmp_path):
    host, pm = make(tmp_path / "h")
    install_eula(pm, _side(tmp_path, A_TEXT, "1.0.0"))
    n = len(host.gate_calls)
    v2 = eula_package(tmp_path, text=B_TEXT, version="v2")
    upgrade_eula(pm, v2)
    assert len(host.gate_calls) - n == 1 and len(consent_lines(pm)) == 2


def test_CXC_PC_V02_T_EULA_RECONSENT_TABLE_license_only_and_0_1_record(tmp_path):
    host, pm = make(tmp_path / "h")
    install_eula(pm, eula_package(tmp_path, text=A_TEXT, license="MIT"))
    n = len(host.gate_calls)
    upgrade_eula(pm, eula_package(tmp_path, text=A_TEXT, license="Apache-2.0",
                                  edit=lambda d: d.update(version="1.0.1")))
    assert len(host.gate_calls) == n
    # an install record written by a 0.1 library (no eula_sha256, no event_seq)
    host2, pm2 = make(tmp_path / "h2")
    install(pm2, fixture_copy(tmp_path, "v1"))
    ip = host2.state_root / "installs.json"
    data = json.loads(ip.read_text())
    for k in ("eula_sha256", "event_seq"):
        data["hello_world"].pop(k, None)
    ip.write_text(json.dumps(data))
    m = len(host2.gate_calls)
    upgrade_eula(pm2, _side(tmp_path, A_TEXT, "1.0.1"))
    assert len(host2.gate_calls) - m == 1


def test_CXC_PC_V02_T_EULA_RECONSENT_TABLE_refused_changes_nothing(tmp_path):
    from wave2_helpers import wait_for
    host, pm, app = web_app(tmp_path)
    install_eula(pm, _side(tmp_path, A_TEXT, "1.0.0"))
    pm.enable("hello_world")
    assert wait_for(lambda: (pm.tick(), pm.status("hello_world"))[1] == "running")
    running = pm.running("hello_world")
    before = (host.state_root / "installs.json").read_bytes()
    new = _side(tmp_path, B_TEXT, "1.0.1")
    st, h, page = wsgi(app, "", "/plugin/hello_world/upgrade/prepare", "POST",
                       {"source": str(new)})
    assert "EULA changed" in page and "<strong>ADDED</strong>" in page
    assert html.unescape(_eula_block(page)[1]) == B_TEXT
    with pytest.raises(ConsentDenied):
        upgrade_eula(pm, new, accepted=False)
    assert (host.state_root / "installs.json").read_bytes() == before
    assert pm.running("hello_world") is running and pm.status("hello_world") == "running"
    last = consent_lines(pm)[-1]
    assert last["decision"] == "refused" and last["eula_sha256"] == sha(B_TEXT.encode())


# =========================================================================== #
# Candidate review c48ab58, F2: [CXC-PC-V02-EULA-HASH-RECORD] verify-human,
# "the consent-records panel shows the EULA digest beside a record made with a
# EULA, and nothing for a record made without".
# =========================================================================== #
def test_CXC_PC_V02_T_EULA_HASH_BINDING_consent_records_panel(tmp_path):
    from html.parser import HTMLParser
    host, pm, app = web_app(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))                 # a record without a EULA
    host2, pm2, app2 = web_app(tmp_path / "e")
    install_eula(pm2, eula_package(tmp_path))
    digest = consent_lines(pm2)[-1]["eula_sha256"]

    class Rows(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows, self._row, self._cell = [], None, None

        def handle_starttag(self, tag, attrs):
            if tag == "tr":
                self._row = []
            if tag in ("td", "th"):
                self._cell = ""

        def handle_data(self, data):
            if self._cell is not None:
                self._cell += data

        def handle_endtag(self, tag):
            if tag in ("td", "th") and self._row is not None:
                self._row.append(self._cell)
                self._cell = None
            if tag == "tr":
                self.rows.append(self._row)
                self._row = None

    def table(a):
        st, h, page = wsgi(a, "", "/consent")
        assert st == "200 OK"
        r = Rows()
        r.feed(page)
        return r.rows
    with_rows, without_rows = table(app2), table(app)
    head = with_rows[0]
    col = head.index("EULA sha256")
    assert without_rows[0] == head
    assert [row[col] for row in with_rows[1:]] == [digest]
    assert [row[col] for row in without_rows[1:]] == [""]


# =========================================================================== #
# [CXC-PC-V02-T-SCHEMA-FILES], by its own procedure: hash the two shipped
# files and the schema inside the published 0.1.0 wheel; check the fields and
# the version per file; validate the eight manifests of the version table and
# the 0.1 and 0.2 manifests with `eula` against the schema their contract
# selects.
# =========================================================================== #
SCHEMA_ROWS = [  # (id, contract, license, eula, passes its selected schema)
    ("0.1-plain", "0.1", False, False, True), ("0.1-license", "0.1", True, False, False),
    ("0.1-eula", "0.1", False, True, False), ("0.2-plain", "0.2", False, False, True),
    ("0.2-license", "0.2", True, False, True), ("0.2-eula", "0.2", False, True, True),
    ("0.2-both", "0.2", True, True, True), ("0.3-plain", "0.3", False, False, False),
]


def test_CXC_PC_V02_T_SCHEMA_FILES():
    import hashlib
    import zipfile
    from importlib import resources
    jsonschema = pytest.importorskip("jsonschema")
    import compose_plugins.manifest as M
    from conftest import valid_manifest
    files = {c: resources.files("compose_plugins.schema").joinpath(n).read_bytes()
             for c, n in M.SCHEMA_RESOURCES.items()}
    for contract, raw in files.items():
        s = json.loads(raw)
        assert s["x-compose-plugins-contract"] == contract
        has = (b'"license"' in raw, b'"eula"' in raw)
        assert has == ((False, False) if contract == "0.1" else (True, True)), contract
    for rid, contract, lic, eula, ok in SCHEMA_ROWS:
        m = valid_manifest()
        m["contract"] = contract
        if lic:
            m["license"] = "MIT"
        if eula:
            m["eula"] = "EULA.txt"
        s = M.schema_for(m)
        errs = list(jsonschema.Draft202012Validator(s).iter_errors(m))
        assert (errs == []) is ok, (rid, s["x-compose-plugins-contract"],
                                    [e.message for e in errs])
    whl = os.environ.get("COMPOSE_PLUGINS_V010_WHEEL")
    if not whl:
        pytest.skip("COMPOSE_PLUGINS_V010_WHEEL is not set: the digest check against the "
                    "published 0.1.0 wheel's schema was NOT exercised (the rest ran)")
    with zipfile.ZipFile(whl) as zf:
        published = zf.read("compose_plugins/schema/manifest-0.1.schema.json")
    print("0.1 schema sha256", hashlib.sha256(published).hexdigest())
    assert files["0.1"] == published
