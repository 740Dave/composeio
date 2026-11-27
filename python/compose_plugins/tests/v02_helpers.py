"""Shared helpers for the contract 0.2 tests (spec 95 v0.2 delta,
[DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA]).

Every public name of the 0.2 library is looked up INSIDE a test (``cp()``),
never imported at module level, so that each test fails on its own against a
0.1 library (red) rather than the whole file failing at collection.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
import os
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from wave2_helpers import fixture_copy, make


def cp():
    """The ``compose_plugins`` package (its 0.2 exports are read from it)."""
    return importlib.import_module("compose_plugins")


_N = [0]

EULA_TEXT = ("Hello World licence agreement (test)\r\n"
             "1.\tYou may run this plug-in.\n"
             "2.\tIt comes with no warranty — none at all.\n")


def eula_package(tmp_path, *, text: "str | bytes | None" = EULA_TEXT, path="EULA.txt",
                 contract="0.2", name=None, version="v1", edit=None, license=None):
    """A hello-world copy (declaring hosts.testhost) whose manifest says
    ``contract`` and declares ``eula: path``; the file holds ``text`` (bytes
    are written as given; None writes no file)."""
    def _edit(d):
        d["contract"] = contract
        if path is not None:
            d["eula"] = path
        if license is not None:
            d["license"] = license
        if edit is not None:
            edit(d)
    _N[0] += 1
    dst = fixture_copy(tmp_path, version, edit=_edit, name=name or f"pkg-eula-{_N[0]}")
    if text is not None and isinstance(path, str):
        target = dst / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    return dst


def wheel_of(dir_pkg: Path, out: Path, *, symlinks: "dict[str, str] | None" = None) -> Path:
    """The directory package ``dir_pkg`` as a wheel: every file at
    ``hello_world/<relpath>`` (the manifest's directory is the import package).
    ``symlinks`` adds zip entries flagged as symlinks: {member-relpath: target}."""
    whl = out / f"{dir_pkg.name}-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        for f in sorted(dir_pkg.rglob("*")):
            if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts:
                rel = f.relative_to(dir_pkg).as_posix()
                if rel == "__init__.py":         # the fixtures' package marker
                    continue
                if rel.startswith("hello_world/"):
                    rel = rel[len("hello_world/"):]
                zf.writestr(f"hello_world/{rel}", f.read_bytes())
        for rel, target in (symlinks or {}).items():
            info = zipfile.ZipInfo(f"hello_world/{rel}")
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, target)
        zf.writestr("hello_world-1.0.0.dist-info/METADATA", "Name: hello-world\n")
    return whl


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def snapshot(root: Path, *, skip=()) -> dict:
    """{relpath: (size, sha256, mtime_ns)} for every file under ``root``, plus
    every directory as {relpath/: 'dir'}; ``skip`` names relpaths left out."""
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel in skip:
            continue
        if p.is_dir() and not p.is_symlink():
            out[rel + "/"] = "dir"
        else:
            st = os.lstat(p)
            out[rel] = (st.st_size, sha(p.read_bytes()), st.st_mtime_ns)
    return out


def log_lines(pm) -> "list[dict]":
    p = Path(pm.host.state_root) / "installs.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def consent_lines(pm) -> "list[dict]":
    p = Path(pm.host.state_root) / "consent.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]


def install_eula(pm, src, **kw):
    """prepare + install, passing the shown EULA digest when there is one."""
    p = pm.prepare_install(src)
    e = getattr(p.content, "eula", None)
    pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True,
               **({"consented_eula_sha256": e.sha256} if e is not None else {}), **kw)
    return p


def upgrade_eula(pm, src, *, accepted=True):
    p = pm.prepare_upgrade(src)
    e = getattr(p.content, "eula", None)
    kw = {"consented_eula_sha256": e.sha256} if e is not None else {}
    pm.upgrade(src, consented_sha256=p.manifest_sha256, operator="op", accepted=accepted, **kw)
    return p


def wsgi(app, prefix, path, method="GET", form=None, ident="op", pairs=None):
    """One same-origin request; POSTs carry the CSRF token. ``pairs`` sends a
    raw list of (key, value) pairs (for repeated keys)."""
    form = dict(form or {})
    items = list(pairs or [])
    if method == "POST" and ident and "csrf" not in form \
            and not any(k == "csrf" for k, _ in items):
        form["csrf"] = app.csrf_token(ident)
    body = urlencode(list(form.items()) + items).encode()
    environ = {"REQUEST_METHOD": method, "SCRIPT_NAME": prefix, "PATH_INFO": path,
               "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body),
               "REMOTE_ADDR": "127.0.0.1", "_ident": ident, "wsgi.url_scheme": "http",
               "HTTP_HOST": "127.0.0.1:8080"}
    out = {}

    def sr(status, headers):
        out["status"], out["headers"] = status, dict(headers)
    data = b"".join(app(environ, sr)).decode()
    return out["status"], out["headers"], data


def web_app(tmp_path, **kw):
    from compose_plugins.web import PluginManagerApp
    host, pm = make(tmp_path, auth=lambda env: env.get("_ident"), **kw)
    return host, pm, PluginManagerApp(pm)


GOLDEN = Path(__file__).resolve().parent / "data" / "v01_golden"
