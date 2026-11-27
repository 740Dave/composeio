"""Host-managed JSON stores: install records (the REGISTRY, [CXC-PC-DISCOVERY])
and stored config, each one JSON file written atomically (temp + rename); and
(contract 0.2) the append-only install event log ``installs.jsonl``
([CXC-PC-V02-INSTALL-LOG-FILE]) with its findings
([CXC-PC-V02-INSTALL-LOG-FINDINGS-METHOD])."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import InstallLogRefused, safe_class_name


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


# =========================================================================== #
# The install event log (contract 0.2).
# =========================================================================== #
#: [CXC-PC-V02-INSTALL-LOG-EVENT] keys: every event has EXACTLY these
EVENT_KEYS = ("seq", "at", "host", "event", "domain", "from_state", "to_state", "version",
              "manifest_sha256", "consent", "consent_basis")
#: ... and an ``abort`` event also exactly these two
ABORT_KEYS = ("aborts_seq", "reason")
EVENTS = ("install", "upgrade", "enable", "disable", "uninstall", "abort")
CONSENT_BASES = ("recorded", "carried", "none")
LIFECYCLE_STATES = ("available", "installed", "enabled", "disabled", "uninstalled")
#: [CXC-PC-V02-INSTALL-LOG-MALFORMED-LINES] the reason codes of a malformed line
MALFORMED_CODES = ("not-json", "not-an-object", "bad-keys", "bad-value",
                   "seq-not-increasing", "torn-last-line")
_AT_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _event_problem(obj: Any) -> "str | None":
    """The reason code for an event object that is NOT a valid event (apart
    from seq ordering), or None."""
    if not isinstance(obj, dict):
        return "not-an-object"
    is_abort = obj.get("event") == "abort"
    want = set(EVENT_KEYS) | (set(ABORT_KEYS) if is_abort else set())
    if set(obj) != want:
        return "bad-keys"
    ok = (_is_int(obj["seq"]) and obj["seq"] >= 1
          and isinstance(obj["at"], str) and _AT_RE.fullmatch(obj["at"]) is not None
          and isinstance(obj["host"], str) and _NAME_RE.fullmatch(obj["host"]) is not None
          and obj["event"] in EVENTS
          and isinstance(obj["domain"], str) and _NAME_RE.fullmatch(obj["domain"]) is not None
          and obj["from_state"] in LIFECYCLE_STATES and obj["to_state"] in LIFECYCLE_STATES
          and isinstance(obj["version"], str) and obj["version"] != ""
          and isinstance(obj["manifest_sha256"], str)
          and _SHA_RE.fullmatch(obj["manifest_sha256"]) is not None
          and obj["consent_basis"] in CONSENT_BASES
          and (obj["consent"] is None) == (obj["consent_basis"] == "none"))
    if ok and obj["consent"] is not None:
        from .consent import ConsentRecord
        try:
            ConsentRecord.from_json(obj["consent"])
        except Exception:            # noqa: BLE001 - any refusal is a bad value
            ok = False
    if ok and is_abort:
        ok = (_is_int(obj["aborts_seq"]) and obj["aborts_seq"] >= 1
              and isinstance(obj["reason"], str) and len(obj["reason"]) <= 200
              and _CLASS_RE.fullmatch(obj["reason"]) is not None)
    return None if ok else "bad-value"


def _event_line(event: dict) -> bytes:
    """One line: sorted keys, ASCII escaping (so no value can forge a line)."""
    return (json.dumps(event, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def _scan_bytes(raw: bytes) -> "tuple[list[dict], list[tuple[int, str]], list[tuple[int, int]]]":
    """(valid events, problems as (line, code), seq gaps as (line, seq))."""
    pieces = raw.split(b"\n")
    ends_with_lf = raw.endswith(b"\n")
    if ends_with_lf or raw == b"":
        pieces = pieces[:-1]
    events: "list[dict]" = []
    problems: "list[tuple[int, str]]" = []
    gaps: "list[tuple[int, int]]" = []
    last = 0
    for n, piece in enumerate(pieces, start=1):
        torn = n == len(pieces) and not ends_with_lf
        try:
            obj = json.loads(piece.decode("utf-8"))
            code = _event_problem(obj)
        except (UnicodeDecodeError, ValueError, RecursionError):
            code = "not-json"
        if code is None and obj["seq"] <= last:
            code = "seq-not-increasing"
        if code is not None:
            problems.append((n, "torn-last-line" if torn else code))
            continue
        if obj["seq"] > last + 1:
            gaps.append((n, obj["seq"]))
        last = obj["seq"]
        events.append(obj)
    return events, problems, gaps


class InstallLog:
    """The append-only event log beside ``installs.json``
    ([CXC-PC-V02-INSTALL-LOG-FILE]). Four public methods; there is NO method
    that rewrites, truncates or deletes. Reading is tolerant and never refuses:
    a malformed line is skipped for every purpose and reported by :meth:`scan`.
    :class:`InstallLogRefused` is raised only when a WRITE fails."""

    def __init__(self, path: "str | os.PathLike[str]"):
        self.path = Path(path)

    def append(self, event: dict) -> int:
        """Assign ``seq`` (the highest valid seq + 1, or 1), write the event as
        one line under an exclusive lock, ``fsync``, and return the seq. A file
        whose last byte is not a line feed (a torn last line) first gets one, so
        the new event starts on its own line."""
        import fcntl
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        except Exception as exc:
            raise InstallLogRefused(self.path, safe_class_name(exc)) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                with open(self.path, "rb") as fh:
                    raw = fh.read()
                events, _, _ = _scan_bytes(raw)
                seq = (events[-1]["seq"] if events else 0) + 1
                body = dict(event)
                body["seq"] = seq
                if _event_problem(body) is not None:
                    raise ValueError("not a valid install event")
                data = (b"\n" if raw and not raw.endswith(b"\n") else b"") + _event_line(body)
                os.write(fd, data)
                os.fsync(fd)
                return seq
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        except InstallLogRefused:
            raise
        except Exception as exc:
            raise InstallLogRefused(self.path, safe_class_name(exc)) from exc
        finally:
            os.close(fd)

    def _raw(self) -> bytes:
        if not self.path.exists():
            return b""
        return self.path.read_bytes()

    def events(self, *, domain: "str | None" = None) -> "list[dict]":
        """The VALID events, in file order; optionally of one domain."""
        evs = _scan_bytes(self._raw())[0]
        return [e for e in evs if domain is None or e["domain"] == domain]

    def last_seq(self) -> int:
        """The highest valid seq, or 0."""
        evs = _scan_bytes(self._raw())[0]
        return evs[-1]["seq"] if evs else 0

    def scan(self) -> "tuple[list[dict], list[tuple[int, str]]]":
        """(the valid events, the problems as (line number, reason code))."""
        evs, problems, _ = _scan_bytes(self._raw())
        return evs, problems


@dataclass(frozen=True)
class InstallLogFinding:
    """One install-log finding ([CXC-PC-V02-INSTALL-LOG-FINDINGS-METHOD]):
    ``kind`` is unreflected-event, snapshot-ahead, malformed-line, seq-gap or
    unreadable. ``message`` never quotes a line or an exception's text."""

    kind: str
    domain: "str | None"
    seq: "int | None"
    line: "int | None"
    code: "str | None"
    message: str


def install_log_findings(log_path: Path, installs_path: Path) -> "tuple[InstallLogFinding, ...]":
    """Compare the valid events of the log with ``installs.json``; read both
    afresh; write nothing; never raise ([CXC-PC-V02-INSTALL-LOG-DIVERGENCE],
    -MALFORMED-LINES, -FINDINGS-METHOD)."""
    out: "list[InstallLogFinding]" = []
    raw: "bytes | None"
    try:
        raw = log_path.read_bytes() if log_path.exists() or log_path.is_symlink() else b""
    except Exception as exc:                   # noqa: BLE001 - never raises
        out.append(InstallLogFinding("unreadable", None, None, None, None,
                                     f"installs.jsonl cannot be read ({safe_class_name(exc)})"))
        raw = None                             # and installs.json is still read
    try:
        records = json.loads(installs_path.read_text(encoding="utf-8")) \
            if installs_path.exists() else {}
        if not isinstance(records, dict):
            raise ValueError("installs.json is not an object")
    except Exception as exc:                   # noqa: BLE001 - never raises
        out.append(InstallLogFinding("unreadable", None, None, None, None,
                                     f"installs.json cannot be read ({safe_class_name(exc)})"))
        records = None
    if raw is None:      # no log to compare: only the unreadable finding(s)
        return tuple(out)
    events, problems, gaps = _scan_bytes(raw)
    for n, code in problems:
        out.append(InstallLogFinding("malformed-line", None, None, n, code,
                                     f"installs.jsonl line {n} is malformed ({code}) "
                                     "and is skipped"))
    for n, seq in gaps:
        out.append(InstallLogFinding("seq-gap", None, seq, n, None,
                                     f"installs.jsonl seq jumps to {seq} at line {n}"))
    if records is not None:
        aborted = {e["aborts_seq"] for e in events if e["event"] == "abort"}
        last_by_domain: "dict[str, dict]" = {}
        for e in events:
            if e["event"] != "abort" and e["seq"] not in aborted:
                last_by_domain[e["domain"]] = e
        for domain, e in last_by_domain.items():
            rec = records.get(domain)
            at = _record_seq(rec)
            if not isinstance(rec, dict) or at < e["seq"]:
                out.append(InstallLogFinding(
                    "unreflected-event", domain, e["seq"], None, None,
                    f"installs.jsonl event {e['seq']} ({e['event']} of {domain} "
                    f"{e['version']}) is not reflected in installs.json "
                    f"(record is at event {at})"))
        last = events[-1]["seq"] if events else 0
        for domain, rec in records.items():
            at = _record_seq(rec)
            if at > last:
                out.append(InstallLogFinding(
                    "snapshot-ahead", domain, None, None, None,
                    f"installs.json record for {domain} is at event {at}, beyond the "
                    f"last event {last} of installs.jsonl"))
    out.sort(key=lambda f: (f.kind, f.domain or "", f.line or 0, f.seq or 0))
    return tuple(out)


def _record_seq(rec: Any) -> int:
    """A record's ``event_seq``; a record written by 0.1 has none (read as 0)."""
    v = rec.get("event_seq", 0) if isinstance(rec, dict) else 0
    return v if _is_int(v) else 0
