"""Host-managed JSON stores: install records (the REGISTRY, [CXC-PC-DISCOVERY])
and stored config. Each is one JSON file written atomically (temp + rename)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _atomic_write(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class InstallStore:
    """``{domain: record}``. A record survives uninstall as ``state:
    uninstalled`` (the code and config are gone; the consent records are kept
    in their own store)."""

    def __init__(self, path: "str | Path"):
        self.path = Path(path)

    def _all(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, domain: str) -> "dict | None":
        return self._all().get(domain)

    def put(self, domain: str, record: dict) -> None:
        data = self._all()
        data[domain] = record
        _atomic_write(self.path, data)

    def domains(self) -> "list[str]":
        return sorted(self._all())


class ConfigStore:
    """Stored config per domain, kept exactly as given ([CXC-PC-LIFECYCLE-CONFIG]:
    it is validated before instantiation, never on write)."""

    def __init__(self, root: "str | Path"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, domain: str) -> Path:
        return self.root / f"{domain}.json"

    def get(self, domain: str) -> dict:
        p = self._path(domain)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def put(self, domain: str, config: Any) -> None:
        _atomic_write(self._path(domain), config)

    def remove(self, domain: str) -> None:
        p = self._path(domain)
        if p.exists():
            p.unlink()

    def exists(self, domain: str) -> bool:
        return self._path(domain).exists()
