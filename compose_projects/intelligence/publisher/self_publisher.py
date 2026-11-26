#!/usr/bin/env python3
"""self_publisher — poll the SelfSensor and publish `self/*` to the bus continuously.

The `self/*` sensor (`composeio.self_sensor`, [CXC-IC-SELF]) COMPUTES the readings but does not
open a live bus link — its own docstring notes "that wiring is the Console's job in a later slice."
This IS that slice: a standalone poll-loop daemon that publishes the sensor's `to_bus_lines()` set
so the console's context gauge, registration checklist, and ledger bind to the Intelligence's REAL
state — the numeric complement to the transcript publisher's event streams.

Source priority for context (Claude Code's `used_percentage` is delivered ONLY to the statusline's
stdin — never the PTY/JSONL, Problem 1):
  1. haven-hud's per-session context-cache snapshot (`_read_hud_cache`) — AUTHORITATIVE, zero-touch
     (hud already persists the native `used_percentage` to disk each tick; we read it). When present
     it is ALSO teed to `self-{id}.json` (`source:"statusline"`) so the launcher's HarnessMonitor
     threshold-alerts stop being blind — one read fixes both the gauge and the alerts.
  2. a wired `--emit-state` state file (`_read_state_level`, trusts only `source=="statusline"`).
  3. the JSONL estimate (over-reads; last resort).
Publishes `self/context_level` (0-1, canonical) AND `self/context_pct` — a rich dict
`{pct, tokens, max_tokens, source}` when authoritative (drives the gauge readout + SRC line), a
bare 0-100 float on the estimate fallback. Plus the rest of the sensor set.

Also publishes `self/rate_limit` for the SelfLedgerWidget's two rate-limit cells from a SECOND
zero-touch hud file — haven-hud's external-usage snapshot (`external-usage.json`, written atomically
+ throttled when the hud's `display.externalUsageWritePath` is set). Same "one core, N consumers"
pattern as the context read: we are a read-only consumer of a file the hud already persists, no hud
change. Absent/malformed/stale/no-reading -> we publish nothing for rate_limit (the ledger keeps its
seed), so this is correct BEFORE the hud writer is enabled and lights up automatically after.

Standalone + read-only on the transcript; no launcher change, no relaunch.

Run:   python3 self_publisher.py --jsonl <path> [--meta <manifest>] [--state <file>]
                                 [--id architect] [--mqtt mqtt://host:port] [--poll 15]
                                 [--external-usage <file>] [--external-usage-max-age 300]
       python3 self_publisher.py --selftest
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "self"                       # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])


# ── the SHARED context-source reader (de-fork; see _read_hud_cache) ──────────────────────────
def _ensure_composeio_on_path() -> None:
    py = os.path.join(_repo_root(), "python")
    if py not in sys.path:
        sys.path.insert(0, py)


def _shared_read_hud_cache(jsonl_path):
    """Delegate to the ONE shared hud-cache reader (`composeio.self_sensor.context_source`).
    Lazy import so this module keeps working standalone; None on any import/read failure."""
    _ensure_composeio_on_path()
    try:
        from composeio.self_sensor.context_source import read_hud_cache
    except Exception:
        return None
    return read_hud_cache(jsonl_path)


def _hud_cache_path(jsonl_path: str) -> str:
    """The haven-hud context-cache path, via the shared single-sourced scheme (never forked)."""
    _ensure_composeio_on_path()
    from composeio.self_sensor.context_source import hud_cache_path
    return hud_cache_path(jsonl_path)


def compute_lines(parsed: dict, state_level: float | None = None) -> dict:
    """The publishable `self/*` set from a SelfSensor.parse() dict. Drops private/`_*` and the
    bulky `/list`,`/files`,`/manifest` records; overrides `self/context_level` with the
    authoritative `state_level` (0-1) when supplied; adds `self/context_pct` (0-100) for the gauge."""
    level = float(parsed.get("self/context_level", 0.0) or 0.0)
    if state_level is not None:
        level = float(state_level)
    out = {k: v for k, v in parsed.items()
           if not k.startswith("_") and not k.endswith(("/list", "/files", "/manifest"))}
    out["self/context_level"] = round(level, 4)
    out["self/context_pct"] = round(level * 100.0, 1)

    # self/registered as the checklist payload ([CXC-ICV-REGISTERED]). The sensor emits a bare bool
    # + a `/manifest` list that the filter above DROPS — so the widget only ever saw `True` and fell
    # back to the scene seed. Rebuild the payload the RegisteredChecklistWidget wants
    # ({items:[{name,path,ok}]}) FROM the manifest, so the checklist shows the REAL required
    # artifacts. (Per-item read status isn't in the manifest yet, so `ok` falls back to the overall
    # registered bool — correct while all-read; a future sensor field can make it per-item.)
    manifest = parsed.get("self/registered/manifest")
    if isinstance(manifest, list) and manifest:
        reg = bool(parsed.get("self/registered", False))
        items = []
        for m in manifest:
            p = str(m.get("path", "")) if isinstance(m, dict) else str(m)
            d = os.path.dirname(p)
            # carry the per-artifact KIND (role def / bootstrap-read / dusk gift…)
            # so the widget caption is manifest-driven, not a hardcoded summary
            # ([CXC-IC-SELF]).
            items.append({"name": os.path.basename(p) or p,
                          "path": (d + "/") if d else "",
                          "kind": m.get("kind") if isinstance(m, dict) else None,
                          "role": m.get("role") if isinstance(m, dict) else None,
                          "ok": bool(m.get("ok", m.get("read", reg))) if isinstance(m, dict) else reg})
        out["self/registered"] = {"items": items, "ok": reg,
                                  "count": sum(1 for it in items if it["ok"]),
                                  "total": len(items)}
    return out


# [CXC-IH-5A] Intent/config channels are console→intelligence DIRECTIVES (a widget publishes them
# to actuate the intelligence), NOT per-instance READINGS. They MUST stay GLOBAL (bare) so a widget
# on any surface reaches the intelligence at a well-known topic; id-scoping them would break the
# actuation path. The rewrite helpers NEVER scope these even though they share a scoped family
# prefix — a guarantee at the helper level so a future caller that accidentally routes one through a
# publisher cannot clobber/mis-scope it. `self/deliverables` is the separate deliverable_publisher's
# projection (out of this slice) — kept bare here so scoping a sensor-set never touches it; it is a
# FLAGGED follow-on cross-instance vector (see [CXC-IH-5A] `migration`).
BARE_INTENT_CHANNELS = frozenset({
    "self/model/select",     # model/effort picker → intelligence (intent)
    "self/account/signin",   # Account card Sign in ▸ → intelligence (intent)
    "self/meta",             # meta_publisher projection / config surface (bare for now — flagged)
    "self/deliverables",     # deliverable_publisher projection (out of slice — flagged follow-on)
})


def scope_namespace(ns: str, intelligence_id: str, families: tuple[str, ...] = ("self",),
                    *, keep_bare: frozenset[str] = BARE_INTENT_CHANNELS) -> str:
    """[CXC-IH-5A] Carried-identity rewrite of ONE namespace: `<family>/<rest>` →
    `<family>/<id>/<rest>` for any `family` in `families` (`<id>` = the publisher's CARRIED
    INTELLIGENCE_ID). A bare `<family>` (no `/rest`) becomes `<family>/<id>`. A namespace in
    `keep_bare`, or under no listed family, passes through UNCHANGED.

    This is the keystone that fixes the singleton wart: `self/context_pct` is a SINGLETON — two
    intelligences publishing it clobber each other on the shared bus. Under `self/<id>/*` the
    architect's `self/architect/context_pct` and a spawned builder's `self/builder-1/context_pct`
    are DISTINCT topics that coexist without overwrite; a fleet view subscribes `self/+/context_pct`
    (or `thread/+/narration`) to see every live intelligence at once."""
    if ns in keep_bare:
        return ns
    for fam in families:
        if ns == fam or ns.startswith(fam + "/"):
            rest = ns[len(fam):]              # "" for a bare family, else "/context_pct"
            return f"{fam}/{intelligence_id}{rest}"
    return ns


def apply_id_namespace(lines: dict, intelligence_id: str, families: tuple[str, ...] = ("self",),
                       *, keep_bare: frozenset[str] = BARE_INTENT_CHANNELS) -> dict:
    """Batch form of `scope_namespace`: rewrite every key of `lines` under any of `families`,
    leaving `keep_bare` + non-family keys untouched. Returns a NEW dict (non-destructive).

    The ONE shared helper both publishers use: self_publisher scopes its sensor set over the
    `self` family; transcript_publisher scopes its feed set over `("self", "thread")`. Keeping the
    rewrite in one place means the two publishers can NEVER drift on the scheme.

    ADDITIVE contract: applied ONLY when `--namespace-by-id` is set. Default OFF preserves the
    legacy bare `self/*` / `thread/*` topics so a console that has NOT migrated is byte-for-byte
    unchanged (provisionio's console is untouched until it opts in). See [CXC-IH-5A]."""
    return {scope_namespace(ns, intelligence_id, families, keep_bare=keep_bare): val
            for ns, val in lines.items()}


def apply_self_id_namespace(lines: dict, intelligence_id: str) -> dict:
    """`self`-family specialization of `apply_id_namespace` (back-compat shim + the self_publisher
    call site). Rewrites `self/<reading>` → `self/<id>/<reading>`; keys not under `self/` (and the
    `keep_bare` intent channels) pass through untouched. See [CXC-IH-5A]."""
    return apply_id_namespace(lines, intelligence_id, families=("self",))


def _read_state_level(state_file: str | None) -> float | None:
    """The AUTHORITATIVE context_level (0-1) from a statusline-written state file, or None.

    Only a file whose `source == "statusline"` is trusted — that is Claude Code's real
    `used_percentage`. A file written from the JSONL fallback (`source == "jsonl"`) is NOT
    authoritative (it is the over/under-reading estimate) and is IGNORED so the daemon falls back
    to the live JSONL parse rather than freezing on a stale estimate. (The first glass test showed
    a stale `source:"jsonl"` file pinning the gauge at a wrong 20.3%.)"""
    if not state_file or not os.path.exists(state_file):
        return None
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if data.get("source") != "statusline":
        return None  # not the authoritative source — use the live JSONL estimate instead
    lvl = data.get("context_level", data.get("level"))
    if lvl is None:
        return None
    lvl = float(lvl)
    return lvl / 100.0 if lvl > 1.0 else lvl


# ── Authoritative context via haven-hud's context-cache (zero-touch option C) ────────────────
# Claude Code's REAL context figure (`context_window.used_percentage`) is delivered only to the
# STATUSLINE command's stdin — never to the PTY or the JSONL (Problem 1: perception lives where the
# stdin lands). haven-hud reads that native figure each ~300ms tick and PERSISTS it (<=3s throttle)
# to a per-session snapshot on disk. We become a zero-touch SECOND consumer of that snapshot.
#
# DE-FORK ([CXC-IC-MONITOR], 2026-07-26): the hud-cache reader + path scheme now live in ONE place
# — `composeio.self_sensor.context_source` — shared by REFERENCE with the harness monitor's reader
# (`compose_projects/intelligence/launcher/launcher.py` build_context_reader). They were forked (the console read the
# fresh hud-cache while the monitor read only the redraw-gated statusline state file), so the gauge
# and the 90/95 crossings perceived different levels. `_read_hud_cache` here is a thin adapter over
# the shared reader that adds the `max_tokens` alias this module's tee/payload code already uses.
def _read_hud_cache(jsonl_path: str | None) -> dict | None:
    """The AUTHORITATIVE reading from haven-hud's snapshot via the SHARED source reader, or None.
    Adapts the shared canonical dict (`window`) to this module's `max_tokens` key and preserves the
    legacy `source == "statusline"` label its tee/payload path keys on (the shared reader labels the
    CHANNEL "hud-cache"; the teed state file's own `source` field stays "statusline")."""
    r = _shared_read_hud_cache(jsonl_path)
    if r is None:
        return None
    return {"pct": r["pct"], "level": r["level"], "tokens": r["tokens"],
            "max_tokens": r.get("window", 0), "source": "statusline", "age_s": r.get("age_s")}


# ── self/rate_limit via haven-hud's external-usage snapshot (a SECOND zero-touch hud file) ────
# haven-hud persists a rate-limit snapshot (`external-usage.json`, atomic + throttled 30s) whenever
# its `display.externalUsageWritePath` is set. Shape (external-usage.ts `ExternalUsageWriteSnapshot`):
#   {"updated_at": <iso>,
#    "five_hour": {"used_percentage": <int 0-100|null>, "resets_at": <iso|null>},
#    "seven_day": {"used_percentage": <int 0-100|null>, "resets_at": <iso|null>}}
# We are a read-only consumer (no hud change) and map it to the SelfLedgerWidget's accepted shape
# (`self_ledger.py` `_rate_cells`): {"5h": {"pct": <n>, "resets": "HH:MM"}, "7d": {"pct": <n>}}.
# Defensive like `_read_hud_cache`: every field guarded, a bad/absent/stale read degrades to None
# (caller publishes nothing for rate_limit -> the ledger keeps its seed) rather than raising.
def _external_usage_path() -> str:
    """Default external-usage snapshot path (`~/.claude/composeio/external-usage.json`), overridable
    via $EXTERNAL_USAGE_FILE — the location David points the hud's externalUsageWritePath at."""
    env = os.environ.get("EXTERNAL_USAGE_FILE", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    return os.path.join(os.path.expanduser("~"), ".claude", "composeio", "external-usage.json")


def _parse_iso(value: object) -> datetime | None:
    """A hud ISO-8601 timestamp (`Date.toISOString()`, UTC 'Z') -> aware datetime, or None.
    Tolerates the trailing 'Z' on pre-3.11 `fromisoformat`; a naive value is assumed UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _format_reset_local(value: object) -> str | None:
    """A `resets_at` ISO timestamp -> local wall-clock `HH:MM` (24h) — the short form the ledger
    renders (`· resets 14:22`), matching the hud's own absolute-time display
    (`format-reset-time.ts` `toLocaleTimeString({hour:'2-digit',minute:'2-digit'})`). None when
    absent/unparseable, so a missing reset simply drops the suffix."""
    dt = _parse_iso(value)
    return dt.astimezone().strftime("%H:%M") if dt is not None else None


def _rate_window(win: object, *, with_resets: bool) -> dict | None:
    """One window ({used_percentage, resets_at}) -> the widget entry {pct?, resets?}, or None when
    it carries no usable reading. A null `used_percentage` drops `pct` (the widget renders '—' when
    absent — graceful, per its `_rate_cells`). `resets` is only mapped for the 5h window (the pre-vis
    shows a reset for 5h only; a bare HH:MM would be ambiguous for a multi-day 7d window)."""
    if not isinstance(win, dict):
        return None
    out: dict = {}
    up = win.get("used_percentage")
    if isinstance(up, (int, float)) and not isinstance(up, bool):
        out["pct"] = up  # already an int 0-100 from the hud; the widget floats it
    if with_resets:
        r = _format_reset_local(win.get("resets_at"))
        if r:
            out["resets"] = r
    return out or None


def _read_external_usage(path: str | None = None, max_age_s: float | None = None,
                         now: float | None = None) -> dict | None:
    """The `self/rate_limit` payload (widget shape) from the hud external-usage snapshot, or None
    when the file is absent / malformed / stale / carries no usable reading — in every such case the
    caller publishes nothing and the ledger keeps its seed. `max_age_s` (>0) drops a snapshot whose
    `updated_at` is older than that many seconds (rate-limit %s go misleading as their windows reset);
    <=0 or None disables the freshness gate. Never raises."""
    p = path or _external_usage_path()
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if max_age_s is not None and max_age_s > 0:
        ts = _parse_iso(data.get("updated_at"))
        if ts is None:
            return None  # no/invalid timestamp -> can't prove freshness -> treat as stale
        age = (now if now is not None else time.time()) - ts.timestamp()
        if age > max_age_s:
            return None
    rl: dict = {}
    fh_win = _rate_window(data.get("five_hour"), with_resets=True)
    sd_win = _rate_window(data.get("seven_day"), with_resets=False)
    if fh_win is not None:
        rl["5h"] = fh_win
    if sd_win is not None:
        rl["7d"] = sd_win
    return rl or None


def _write_state_file(state_file: str | None, ctx: dict) -> None:
    """Tee the authoritative reading into the `self-{id}.json` that the launcher's HarnessMonitor
    (`_read_state_file`) AND the self_publisher fallback (`_read_state_level`) already PREFER
    (`source=='statusline'`). This is the single write that fixes BOTH the frozen gauge and the
    blind threshold-alerts. Best-effort + atomic; a write failure never kills the daemon."""
    if not state_file:
        return
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        payload = {"context_level": ctx["level"], "context_pct": ctx["pct"],
                   "context_tokens": ctx["tokens"], "context_window": ctx["max_tokens"],
                   "source": "statusline", "ts": int(time.time() * 1000)}
        tmp = f"{state_file}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, state_file)  # atomic swap so a reader never sees a half-written file
    except OSError:
        pass


def _parse_mqtt_url(url: str) -> tuple[str, int]:
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


def run(jsonl_path: str, mqtt_url: str, meta: str | None, state_file: str | None,
        poll_s: float, external_usage_path: str | None = None,
        external_usage_max_age: float | None = 300.0,
        intelligence_id: str = "architect", namespace_by_id: bool = False,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 4 — publish-only, TRANSCRIPT-based.

    Publishes through `ComposeIOBus` ([CXC-SOB-THESIS]): this module names a
    NAMESPACE and a SCOPE, never a topic and never a client. It used to hand-roll
    the envelope over raw paho because `publish_state` auto-registered an unknown
    namespace with a BINARY engine that rejected a float like 0.203 — the bug the
    first live glass test surfaced. That was FIXED on 2026-08-08 (auto-create now
    defaults to `json`, [CXC-SOB-BYPASS-EXPIRED]), and this source declares its
    namespaces explicitly anyway, so the workaround has no remaining
    justification.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe. A steady context
    level republishes an identical value; in-process subscribers dedup it and the
    wire does not. That is correct for a GAUGE — it renders the value, not the
    arrival — and it is why the context gauge cannot "stick" from this change.
    """
    sys.path.insert(0, os.path.join(_repo_root(), "python"))
    from composeio.self_sensor.sensor import SelfSensor

    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): key on the OUTPUT namespace — bare for
    # the legacy singleton self/*, <role>-<id> when --namespace-by-id scopes a
    # spawned builder's self/<id>/*, so two publishers with distinct output never
    # contend. Any second instance yields without connecting.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    _key = f"self-publisher-{intelligence_id}" if namespace_by_id else "self-publisher"
    lock = acquire_singleton(_key)
    if lock is None:
        sys.stderr.write(f"[self_publisher] another {_key} holds the singleton; yielding\n")
        return 0

    b = source_bus.acquire_bus(mqtt_url, label=_key, bus=bus)
    _declared: set[str] = set()

    #: DYNAMIC NAMESPACE SET, declared LAZILY ([CXC-SOB-DECLARE]).
    #: This source does not know its namespace set at startup — it is whatever
    #: `SelfSensor.parse()` yields for this transcript, which grows as the
    #: session does. A hardcoded startup list would be a LIE that passes the
    #: SOB-C3 audit, and a manifest you cannot trust is worse than none. So each
    #: namespace is declared with an EXPLICIT scope immediately before its first
    #: publish, and `manifest(only_if_grown=True)` puts a line in the boot log
    #: the moment a new one appears. A quiet log means the set is stable.
    def _emit(ns: str, val) -> None:
        if ns not in _declared:
            source_bus.declare(b, ns, scope=Scope.GLOBAL, source=SRC)
            _declared.add(ns)
        b.publish_state(ns, val, source=SRC)

    ext_path = external_usage_path or _external_usage_path()
    hud_seen = _read_hud_cache(jsonl_path) is not None
    rl_seen = _read_external_usage(ext_path, external_usage_max_age) is not None
    # [CXC-IH-5A] carried-identity: legacy singleton `self/*` (default) or namespaced `self/<id>/*`.
    self_ns = intelligence_id if namespace_by_id else None
    ns_label = f"self/{intelligence_id}/* (carried id)" if self_ns else "self/* (legacy singleton)"
    sys.stderr.write(f"[self_publisher] {jsonl_path} -> {ns_label} every {poll_s}s "
                     f"(hud-cache="
                     f"{'yes (authoritative context)' if hud_seen else 'no -> jsonl estimate'}; "
                     f"rate_limit={'yes' if rl_seen else 'no -> ledger keeps seed'} <- {ext_path})\n")

    def _tick() -> None:
        try:
            d = SelfSensor.from_jsonl(jsonl_path, registration_manifest=meta).parse()
            # Authoritative context first (haven-hud snapshot); tee it to the state file so the
            # launcher's HarnessMonitor sees the real % too. Fall back to a wired --emit-state
            # file, then to the over-reading JSONL estimate.
            ctx = _read_hud_cache(jsonl_path)
            if ctx is not None:
                _write_state_file(state_file, ctx)
                level = ctx["level"]
            else:
                level = _read_state_level(state_file)
            lines = compute_lines(d, level)
            if ctx is not None:
                # Rich gauge payload ([CXC-ICV-GAUGE] dict form): pct + token breakdown +
                # a display source label matching the pre-vis ("src: stdin · used_percentage").
                lines["self/context_pct"] = {"pct": ctx["pct"], "tokens": ctx["tokens"],
                                             "max_tokens": ctx["max_tokens"],
                                             "source": "stdin · used_percentage"}
            # self/rate_limit from the hud external-usage snapshot (the ledger's rate-limit cells).
            # None (absent/malformed/stale/no-reading) -> omit the line -> the ledger keeps its seed.
            rl = _read_external_usage(ext_path, external_usage_max_age)
            if rl is not None:
                lines["self/rate_limit"] = rl
            # [CXC-IH-5A] carried-identity rewrite (opt-in). Applied AFTER every reading is built
            # so it catches the whole set (context_pct override + rate_limit included); a no-op in
            # legacy mode so the live architect's `self/*` topics are byte-for-byte unchanged.
            if self_ns:
                lines = apply_self_id_namespace(lines, self_ns)
            for ns, val in lines.items():
                _emit(ns, val)
            source_bus.manifest(SRC, only_if_grown=True)
        except Exception as exc:  # a bad read must never kill the daemon
            sys.stderr.write(f"[self_publisher] read error (continuing): {exc!r}\n")

    _tick()                            # publish NOW, then park — see slice 2
    source_bus.park(stop, poll_s=poll_s, on_tick=_tick)
    return 0


def _repo_root() -> str:
    # This file is compose_projects/intelligence/<sub>/<me>.py, so the repo root is FOUR
    # dirnames up. Three stops at compose_projects/ -- see the doubled-metrics.db defect.
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def _selftest() -> int:
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    # (a) compute_lines from a JSONL-estimate parse: context_pct derived, private keys dropped.
    parsed = {
        "self/context_level": 0.19, "self/registered": True, "self/subagents/count": 6,
        "self/compactions": 3, "self/registered/files": ["a", "b"], "self/subagents/list": [1],
        "_reads": ["x"], "_tools": {},
    }
    lines = compute_lines(parsed)
    check(lines["self/context_level"] == 0.19, f"(a) context_level wrong: {lines}")
    check(lines["self/context_pct"] == 19.0, f"(a) context_pct not derived: {lines}")
    check(lines["self/registered"] is True and lines["self/subagents/count"] == 6,
          f"(a) sensor fields dropped: {lines}")
    check("_reads" not in lines and "self/registered/files" not in lines
          and "self/subagents/list" not in lines,
          f"(a) private/list/files keys leaked into the publish set: {list(lines)}")

    # (a2) self/registered is rebuilt into the checklist payload FROM the manifest (the fix for the
    # checklist showing the scene seed): bare bool + dropped /manifest ->
    # {items:[{name,path,kind,role,ok}]}. The per-artifact KIND rides along so the widget caption is
    # manifest-driven, not a hardcoded "role def + N dusk gifts" ([CXC-IC-SELF]).
    reg_out = compute_lines({"self/registered": True, "self/registered/manifest": [
        {"path": ".claude/agents/composeux-architect.md", "kind": "role def", "role": "role_descriptor"},
        {"path": "bootstrap/dusk-gifts/README.md", "kind": "dusk gift", "role": "theory"}]})
    reg = reg_out.get("self/registered")
    check(isinstance(reg, dict) and reg.get("total") == 2 and reg.get("count") == 2,
          f"(a2) self/registered not rebuilt into a 2/2 checklist payload: {reg}")
    check(isinstance(reg, dict) and reg["items"][0] == {
        "name": "composeux-architect.md", "path": ".claude/agents/",
        "kind": "role def", "role": "role_descriptor", "ok": True},
          f"(a2) checklist item shape wrong: {reg}")
    check(reg["items"][1].get("kind") == "dusk gift",
          f"(a2) per-artifact kind not carried into the item payload: {reg['items'][1]}")

    # (b) an authoritative state_level OVERRIDES the JSONL estimate.
    lines2 = compute_lines(parsed, state_level=0.42)
    check(lines2["self/context_level"] == 0.42 and lines2["self/context_pct"] == 42.0,
          f"(b) authoritative state_level did not override the estimate: {lines2}")

    # (c) _read_state_level trusts ONLY an authoritative source=="statusline" file (fraction OR
    # percent); a jsonl-sourced or source-less file is IGNORED (None -> fall to live JSONL).
    import tempfile
    tmp = tempfile.mkdtemp()
    p1 = os.path.join(tmp, "frac.json")
    with open(p1, "w") as fh:
        json.dump({"context_level": 0.63, "source": "statusline"}, fh)
    check(_read_state_level(p1) == 0.63, "(c) authoritative fraction state-file not read")
    p2 = os.path.join(tmp, "pct.json")
    with open(p2, "w") as fh:
        json.dump({"context_level": 63.0, "source": "statusline"}, fh)
    check(_read_state_level(p2) == 0.63, "(c) authoritative percent not normalized to fraction")
    p3 = os.path.join(tmp, "jsonl.json")
    with open(p3, "w") as fh:
        json.dump({"context_level": 0.20, "source": "jsonl"}, fh)
    check(_read_state_level(p3) is None,
          "(c) a jsonl-sourced state file MUST be ignored (not authoritative)")
    check(_read_state_level(os.path.join(tmp, "nope.json")) is None,
          "(c) missing state-file should be None")

    # (d) the hud-cache reader + the state-file tee (option C). Write a hud-shaped snapshot at the
    # EXACT path our key scheme resolves to, then assert we read it authoritatively and tee it.
    jsonl = os.path.join(tmp, "session", "abc123.jsonl")
    os.makedirs(os.path.dirname(jsonl), exist_ok=True)
    open(jsonl, "w").close()
    os.environ["CLAUDE_CONFIG_DIR"] = tmp  # isolate the config dir to the temp tree
    hud_path = _hud_cache_path(jsonl)
    check(hud_path.startswith(os.path.join(tmp, "plugins", "haven-hud", "context-cache")),
          f"(d) hud cache path scheme wrong: {hud_path}")
    os.makedirs(os.path.dirname(hud_path), exist_ok=True)
    with open(hud_path, "w") as fh:
        json.dump({"used_percentage": 57, "context_window_size": 1_000_000,
                   "current_usage": {"input_tokens": 2, "output_tokens": 3,
                                     "cache_creation_input_tokens": 500,
                                     "cache_read_input_tokens": 569_495},
                   "saved_at": int(time.time() * 1000)}, fh)
    ctx = _read_hud_cache(jsonl)
    check(ctx is not None and ctx["pct"] == 57.0 and ctx["level"] == 0.57,
          f"(d) hud used_percentage not read authoritatively: {ctx}")
    check(ctx is not None and ctx["tokens"] == 570_000 and ctx["max_tokens"] == 1_000_000,
          f"(d) hud token breakdown/window wrong: {ctx}")
    check(ctx is not None and ctx["source"] == "statusline",
          f"(d) hud reading must carry source=='statusline': {ctx}")
    check(_read_hud_cache(os.path.join(tmp, "no", "such.jsonl")) is None,
          "(d) a missing hud cache must degrade to None (-> jsonl estimate)")
    # the tee makes the state file readable by BOTH existing consumers (source-guarded).
    st = os.path.join(tmp, "self-architect.json")
    _write_state_file(st, ctx)
    check(_read_state_level(st) == 0.57,
          "(d) teed state file must be trusted by _read_state_level (source=='statusline')")
    os.environ.pop("CLAUDE_CONFIG_DIR", None)

    # (e) self/rate_limit from the hud external-usage snapshot -> the EXACT SelfLedgerWidget shape,
    # plus every defensive degrade (absent / malformed / stale / all-null) -> None (ledger keeps seed).
    now = time.time()
    reset_iso = datetime.fromtimestamp(now + 3600, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    expect_hhmm = datetime.fromtimestamp(now + 3600).strftime("%H:%M")  # local wall-clock
    snap_full = os.path.join(tmp, "external-usage.json")
    with open(snap_full, "w") as fh:
        json.dump({"updated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                   "five_hour": {"used_percentage": 38, "resets_at": reset_iso},
                   "seven_day": {"used_percentage": 12, "resets_at": None}}, fh)
    rl = _read_external_usage(snap_full, max_age_s=300, now=now)
    check(rl == {"5h": {"pct": 38, "resets": expect_hhmm}, "7d": {"pct": 12}},
          f"(e) rate_limit not mapped to the exact widget shape: {rl}")
    check(_format_reset_local(reset_iso) == expect_hhmm,
          f"(e) resets_at not formatted to local HH:MM: {_format_reset_local(reset_iso)}")

    # (e2) null used_percentage -> pct omitted (widget renders '—'); 5h resets still passes through;
    # a window with no reading is dropped entirely.
    snap_null = os.path.join(tmp, "external-usage-null.json")
    with open(snap_null, "w") as fh:
        json.dump({"updated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                   "five_hour": {"used_percentage": None, "resets_at": reset_iso},
                   "seven_day": {"used_percentage": None, "resets_at": None}}, fh)
    rl_null = _read_external_usage(snap_null, max_age_s=300, now=now)
    check(rl_null == {"5h": {"resets": expect_hhmm}},
          f"(e2) null pct not handled gracefully (pct omitted, empty window dropped): {rl_null}")

    # (e3) missing file, non-dict/malformed JSON, and an all-null/no-reading snapshot -> None.
    check(_read_external_usage(os.path.join(tmp, "nope.json")) is None,
          "(e3) a missing external-usage file must degrade to None (ledger keeps seed)")
    bad = os.path.join(tmp, "bad.json")
    with open(bad, "w") as fh:
        fh.write("{ not json ]")
    check(_read_external_usage(bad) is None, "(e3) malformed JSON must degrade to None")
    empty = os.path.join(tmp, "empty.json")
    with open(empty, "w") as fh:
        json.dump({"updated_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
                   "five_hour": {"used_percentage": None, "resets_at": None},
                   "seven_day": {"used_percentage": None, "resets_at": None}}, fh)
    check(_read_external_usage(empty, max_age_s=300, now=now) is None,
          "(e3) a snapshot with no usable reading must publish nothing (None)")

    # (e4) staleness gate: a snapshot older than max_age_s -> None; disabling the gate reads it.
    stale = os.path.join(tmp, "stale.json")
    with open(stale, "w") as fh:
        json.dump({"updated_at": datetime.fromtimestamp(now - 4000, tz=timezone.utc).isoformat(),
                   "five_hour": {"used_percentage": 38, "resets_at": reset_iso},
                   "seven_day": {"used_percentage": 12, "resets_at": None}}, fh)
    check(_read_external_usage(stale, max_age_s=300, now=now) is None,
          "(e4) a stale snapshot (updated_at older than max_age_s) must degrade to None")
    check(_read_external_usage(stale, max_age_s=0, now=now) is not None,
          "(e4) max_age_s<=0 must disable the freshness gate")

    # (f) [CXC-IH-5A] carried-identity namespacing: `self/X` -> `self/<id>/X`, NO key overlap with
    # legacy `self/*` (the on-the-wire no-clobber proof at the envelope-key level), values preserved,
    # non-self keys untouched, and legacy output byte-for-byte unchanged when NOT namespacing.
    base = compute_lines({"self/context_level": 0.5, "self/registered": True,
                          "self/subagents/count": 4})
    ns = apply_self_id_namespace(base, "builder-1")
    check(all(k.startswith("self/builder-1/") for k in ns if k.startswith("self")),
          f"(f) not every self/* key namespaced under self/builder-1/: {list(ns)}")
    check(ns.get("self/builder-1/context_pct") == 50.0,
          f"(f) namespaced value not preserved: {ns.get('self/builder-1/context_pct')}")
    check(ns.get("self/builder-1/context_level") == 0.5,
          f"(f) namespaced context_level wrong: {ns.get('self/builder-1/context_level')}")
    # THE no-clobber proof: the namespaced key-set is DISJOINT from the legacy key-set. On the wire
    # these become distinct `composeio/global/self/builder-1/*` vs `composeio/global/self/*` topics,
    # so a builder never overwrites the architect's singleton.
    check(set(base).isdisjoint(set(ns)),
          f"(f) namespaced keys COLLIDE with legacy self/* (clobber!): "
          f"{set(base) & set(ns)}")
    check("self/context_pct" in base and "self/builder-1/context_pct" in ns,
          "(f) legacy and namespaced context_pct must coexist as distinct keys")
    # architect legacy path (namespace off) is unchanged — compute_lines emits bare self/*.
    check("self/context_pct" in base and not any(k.startswith("self/architect/") for k in base),
          "(f) legacy compute_lines must keep bare self/* (architect untouched)")

    # (f2) the GENERALIZED helper: scope_namespace/apply_id_namespace over ANY family, with the
    # intent-channel passthrough guarantee transcript_publisher relies on ([CXC-IH-5A] hard-switch).
    check(scope_namespace("self/context_pct", "architect") == "self/architect/context_pct",
          "(f2) self/ family scopes")
    check(scope_namespace("thread/narration", "architect", ("self", "thread"))
          == "thread/architect/narration",
          "(f2) thread/ family scopes under the generalized helper")
    check(scope_namespace("thread/diagram", "architect", ("self", "thread"))
          == "thread/architect/diagram",
          "(f2) thread/diagram scopes")
    check(scope_namespace("self/avatar/expression", "architect", ("self", "thread"))
          == "self/architect/avatar/expression",
          "(f2) a nested self/ reading scopes at the SECOND segment (id inserted after family)")
    # THE intent-channel passthrough: bare directive channels are NEVER scoped, even though they
    # sit under a scoped family — so a widget always reaches the intelligence at a stable topic.
    for intent in ("self/model/select", "self/account/signin", "self/meta", "self/deliverables"):
        check(scope_namespace(intent, "architect", ("self", "thread")) == intent,
              f"(f2) intent/config channel MUST pass through UNSCOPED: {intent}")
    # a namespace under NO listed family is untouched (compose_projects/intelligence/* stays as-is).
    check(scope_namespace("intelligence/architect/out", "architect", ("self", "thread"))
          == "intelligence/architect/out",
          "(f2) a non-family namespace passes through unchanged")
    # the batch form threads the same guarantees; a mixed dict scopes readings, keeps intents bare.
    mixed = apply_id_namespace(
        {"self/context_pct": 1, "thread/narration": 2, "self/model/select": 3,
         "intelligence/architect/out": 4}, "architect", ("self", "thread"))
    check(mixed == {"self/architect/context_pct": 1, "thread/architect/narration": 2,
                    "self/model/select": 3, "intelligence/architect/out": 4},
          f"(f2) apply_id_namespace mixed-family/intent rewrite wrong: {mixed}")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: self_publisher selftest (7/7 groups)\n")
    for line in (
        "  (a) compute_lines derives self/context_pct + drops private/list/files keys",
        "  (b) an authoritative statusline state_level overrides the JSONL estimate",
        "  (c) _read_state_level parses fraction OR percent, None when absent",
        "  (d) _read_hud_cache reads hud's authoritative snapshot + tees a source=='statusline' file",
        "  (e) _read_external_usage maps the hud snapshot to the widget's self/rate_limit shape "
        "(+ null/missing/malformed/stale -> None, ledger keeps seed)",
        "  (f) [CXC-IH-5A] self/X -> self/<id>/X namespacing; namespaced keys DISJOINT from legacy "
        "self/* (no-clobber); legacy path unchanged",
        "  (f2) generalized scope_namespace/apply_id_namespace over self/ AND thread/ families; "
        "intent channels (self/model/select ...) pass through UNSCOPED",
    ):
        sys.stdout.write(line + "\n")
    return 0


def main(argv: list[str], *, bus=None, stop=None) -> int:
    if "--selftest" in argv:
        return _selftest()

    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    jsonl = opt("--jsonl")
    if not jsonl:
        sys.stderr.write("usage: self_publisher.py --jsonl <path> [--meta <manifest>] "
                         "[--state <file>] [--id architect] [--mqtt mqtt://host:port] [--poll 15] "
                         "[--external-usage <file>] [--external-usage-max-age 300] "
                         "[--namespace-by-id]\n")
        return 2
    intelligence_id = opt("--id", os.environ.get("INTELLIGENCE_ID", "architect"))
    mqtt_url = opt("--mqtt", os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    meta = opt("--meta", os.environ.get("META_MANIFEST"))
    state = opt("--state", os.environ.get(
        "SELF_STATE_FILE",
        os.path.join(os.path.expanduser("~"), ".claude", "composeio", f"self-{intelligence_id}.json")))
    poll = float(opt("--poll", "15"))
    ext_usage = opt("--external-usage", os.environ.get("EXTERNAL_USAGE_FILE")) or _external_usage_path()
    try:
        ext_max_age = float(opt("--external-usage-max-age",
                                os.environ.get("EXTERNAL_USAGE_MAX_AGE", "300")))
    except ValueError:
        ext_max_age = 300.0
    # [CXC-IH-5A] carried-identity: default OFF (legacy `self/*`, so the live architect is
    # untouched); `--namespace-by-id` opts a publisher into `self/<id>/*`, id taken from --id /
    # $INTELLIGENCE_ID (CARRIED, never guessed). A spawned builder inherits INTELLIGENCE_ID from
    # the IntelligenceHost's child env, so it namespaces automatically with no extra flag needed.
    namespace_by_id = "--namespace-by-id" in argv
    return run(jsonl, mqtt_url, meta, state, poll, ext_usage, ext_max_age,
               intelligence_id=intelligence_id, namespace_by_id=namespace_by_id,
               bus=bus, stop=stop)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
