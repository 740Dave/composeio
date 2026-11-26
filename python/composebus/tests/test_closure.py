"""[CXC-CBK-PKG-CLOSURE-RULE] — nothing in composebus imports outside composebus,
the standard library and pydantic, at ANY depth (lazy imports count).

AST walk over every .py file of the package as found on the import path (the
installed copy in a consumer venv, the source tree in the monorepo). Collects every
Import / ImportFrom node anywhere in the tree — module body, functions, methods,
class bodies, TYPE_CHECKING blocks — and classifies its root module name.

Run directly (``python test_closure.py``) to print the raw measurement; exit 0
iff the violation set is empty.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ALLOWED_ROOTS = frozenset(sys.stdlib_module_names) | {"composebus", "pydantic"}


def _package_dir() -> pathlib.Path:
    import composebus
    return pathlib.Path(composebus.__file__).resolve().parent


def scan(pkg_dir: pathlib.Path | None = None):
    pkg_dir = pkg_dir or _package_dir()
    files = sorted(pkg_dir.rglob("*.py"))
    imports = []
    violations = []
    for path in files:
        rel = path.relative_to(pkg_dir.parent).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        top_level = {id(n) for n in tree.body}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
                level = 0
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                level = node.level
            else:
                continue
            where = "top" if id(node) in top_level else "nested"
            for name in names:
                if level:
                    root, ok = "composebus(relative)", True
                else:
                    root = name.split(".")[0]
                    ok = root in ALLOWED_ROOTS
                rec = (rel, node.lineno, where, ("." * level) + name, root, ok)
                imports.append(rec)
                if not ok:
                    violations.append(rec)
    return files, imports, violations


def test_closure_has_no_violations():
    files, imports, violations = scan()
    assert files, "scanned ZERO files — the instrument is blind, not the package clean"
    assert imports, "found ZERO imports — the instrument is blind"
    assert violations == [], violations


def main() -> int:
    pkg_dir = _package_dir()
    files, imports, violations = scan(pkg_dir)
    print(f"package_dir: {pkg_dir}")
    print(f"files_scanned: {len(files)}")
    print(f"import_statements_classified: {len(imports)}")
    print(f"nested_imports: {sum(1 for r in imports if r[2] == 'nested')}")
    roots = sorted({r[4] for r in imports})
    print(f"distinct_roots: {roots}")
    for r in imports:
        if r[2] == "nested":
            print(f"  nested: {r[0]}:{r[1]} {r[3]} -> {'ok' if r[5] else 'VIOLATION'}")
    print(f"violations: {len(violations)}")
    for r in violations:
        print(f"  VIOLATION {r[0]}:{r[1]} ({r[2]}) imports {r[3]}")
    return 1 if violations or not files else 0


if __name__ == "__main__":
    sys.exit(main())
