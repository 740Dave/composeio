#!/usr/bin/env python3
"""config_persister — the DURABLE write-back for the Configuration tab's global
settings ([SPEC:CXC-INTELLIGENCE-CONSOLE]; the missing subscriber for D13's
"the widget publishes, something applies + persists").

THE BUG THIS FIXES (David 2026-07-22): the Configuration tab's ConfigSettingsWidget
publishes an edit BUS-NATIVE — retention -> ``compose_projects/intelligence/<id>/config/retention``
({"cleanupPeriodDays": N}) and the heartbeat cadence ->
``compose_projects/intelligence/<id>/config/heartbeat`` ({"base","backoff_after","then"}) — and then
STOPS at the publish (by design: "state on the bus, the launcher applies").  But
NOTHING subscribed those namespaces to persist them, and the render bus does NOT
publish MQTT-retained, so a config edit lived only in RAM on the widget and the
broker's live stream: a console/session restart LOST it.  This daemon is that
missing subscriber.

TWO DIRECTIONS (a projector + a write-back, mirroring meta_publisher's projector
role but closing the loop):

  * PERSIST (bus -> disk): subscribe the config namespaces; on an edit, read-modify-
    WRITE the meta-object (``compose_projects/intelligence/meta/<id>.meta.json``) ``lifecycle`` block
    atomically (temp file + os.replace — same discipline as
    bootstrap_manifest._write_meta_threshold), preserving every other key.  The
    meta-object is the ONE durable record the launcher already reads, so the
    heartbeat cadence (BUG 3) and retention land where deterministic software
    consumes them.  Canonical storage (minutes, matching the UI):
        lifecycle.retention_days : int
        lifecycle.heartbeat      : {"base": int, "backoff_after": int, "then": int}

  * RE-SEED (disk -> bus): because MQTT state here is NOT retained, a freshly
    launched widget would show the coded defaults until an edit arrives.  So on a
    poll loop (and at startup) this daemon RE-PUBLISHES the persisted values to the
    same config namespaces, so a late-joining ConfigSettingsWidget shows David's
    saved cadence within one poll.  Re-seeds carry ``src=config_persister`` and the
    daemon IGNORES its own re-seed (the request/announcement loop-guard) so it never
    re-writes the file off its own publish — see ECHO below.

Pure logic (``persist_config`` / ``seed_values`` / ``_write_meta``) is separated from
the transport/loop plumbing so the round-trip is fully testable with no broker.

[SPEC:CXC-SOURCES-ON-BUS] slice 5b — the FIRST source that both PUBLISHES and
SUBSCRIBES THE SAME NAMESPACE FAMILY.  Two things that were free under raw paho
have to be re-earned here:

  ECHO ([CXC-SOB-WIRE-DELTAS] item 4).  The old loop-guard was ``src ==
  config_persister``: paho handed the handler the whole envelope, so the daemon
  could see its own name on its own re-seed and ignore it.  The bus callback
  contract is ``(namespace, new_value, old_value)`` — THERE IS NO ``src``.  So the
  guard is re-expressed as an ECHO MAP: ``ConfigPersister`` records the value it
  last published per namespace and ignores a callback carrying exactly that value.
  This is load-bearing, not tidiness: ``seed_values`` is DEFAULTS-FILLED, so a
  meta-object with no ``console`` block re-seeds ``theme="dark"`` etc., and an
  unguarded self-callback would PERSIST those defaults into the durable file —
  materializing a ``console`` block that ``_persist_console`` explicitly promises
  never to create.  The one accepted consequence: if a human publishes a value
  byte-identical to the one we just re-seeded, we treat it as our echo.  The
  effective state is the same either way; only the defaults-materialization
  differs, and NOT doing it is the documented behaviour.

  REPEAT DELIVERY — NOT needed, so the subscription is EXACT, not a pattern
  ([CXC-SOB-SUBSCRIBE]).  Config is STATE, not an
  idempotent intent: "set retention to 45" twice in a row is genuinely a no-op, so
  ``Namespace.publish_state``'s ``new != old`` dedup is CORRECT here (unlike the
  viewer launchers, where the second tap on the same row must re-open the window
  and only ``subscribe_pattern`` delivers it).  Exact subscription also absorbs the
  MQTT round-trip echo for free — the value coming back off the broker equals the
  one we just set, so local subscribers are not notified at all.  The console
  personalization family was an MQTT single-level wildcard
  (``.../config/console/+``); it converts to one exact subscription per
  ``_CONSOLE_FIELDS`` member, which is EXACTLY equivalent because
  ``_persist_console`` already rejects any field outside that tuple.

Run:   python3 config_persister.py --meta compose_projects/intelligence/meta/architect.meta.json \
                                   --mqtt mqtt://mqtt-broker.local:1883
       python3 config_persister.py --once      # print the seed payloads, no bus
       python3 config_persister.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Tuple

# [SPEC:CXC-SOURCES-ON-BUS] slice 5b — the ONE bus seam. This module used to
# import `composeio.bus_envelope.publish_state` and hand-frame onto a paho client;
# it now states a NAMESPACE and a SCOPE and lets `transport_registry` choose the
# substrate ([CXC-SOB-THESIS]).
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                                   # noqa: E402
from composeio.namespace import Scope                              # noqa: E402

_DEFAULT_META = "compose_projects/intelligence/meta/architect.meta.json"
SELF_SRC = "config_persister"      # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
# The bus label = the paho client-id this daemon used, so broker-side / log-side
# identification is unchanged ([CXC-SOB-HANDLE] default).
LABEL = "config-persister"
# A sentinel distinct from every publishable value, so "we have never published
# this namespace" is not confusable with "we published None".
_NEVER = object()

# Coded defaults — mirror config_settings.py's RETENTION_DEFAULT / HEARTBEAT_DEFAULT
# (minutes). Used when the meta-object carries no persisted value yet.
DEFAULT_RETENTION_DAYS = 30
DEFAULT_HEARTBEAT = {"base": 10, "backoff_after": 60, "then": 60}

# ---------------------------------------------------------------------------
# Console personalization model ([SPEC:CXC-CONSOLE-PROD] [CXC-ICP-FOUND-A]).
#
# ONE durable, bus-published personalization state object per Intelligence
# <id>, folded into a `console` block SIBLING to `lifecycle` in the same
# meta-object, written by the SAME atomic read-modify-write this daemon
# already uses for the config settings — reuse-by-reference, not a new store
# ([CXC-ICP-REUSE]).  Window geometry was landed first on this seam by the
# decision-viewer (render/engine/geometry.py::JsonFileGeometryStore, section
# `window_geometry`); Foundation A GENERALIZES that same meta-object seam to
# the full personalization set.  The five fields, their bus namespaces
# (`compose_projects/intelligence/<id>/config/console/<field>`) and their coded DEFAULTS (a
# fork with an empty `console` block gets these, [CXC-ICP-FOUND-A]):
#     window     {"w": int, "h": int}          -> {"w": 1280, "h": 1740}
#     font_scale float                          -> 1.0
#     theme      str                            -> "dark"
#     tabs       {<tab>: {widgets:[], hidden:[]}} -> {}   (every widget shown)
#     dashboard  {"placements": [ ... ]}        -> {"placements": []}
# Every value is a JSON scalar / list of scalars — no nested objects beyond
# the shapes named above.  Storage key = `meta.console`; if
# [DD-ICP-PERSON-SCOPE] later ratifies a global store this key's LOCATION
# changes but its SHAPE (and these namespaces) do not.
#     theme_custom {"bg":[r,g,b], "accent":[r,g,b]} -> {}   (no override)
# theme_custom ([CXC-ICP-S3-THEME], David's D2 addition): the DARK theme's
# user-changeable BACKGROUND + HIGHLIGHT/ACCENT (his examples: the send-button
# color, the "Account" bar-graph color).  Stored as a small color-override map;
# the render-side applier (console_theme.apply_custom_colors) overlays it on the
# active token table.  Empty {} = no override (the theme's own tokens stand).
# scroll_direction ([CXC-ICP-S6-SCROLLDIR]): the per-instance scroll-wheel
# direction preference, "natural" | "traditional".  Default "natural" = respect
# the OS-provided wheel delta (the window layer already honors the platform
# natural-scroll setting via SDL_MOUSEWHEEL_FLIPPED), so scrolling matches the
# user's OS out of the box; "traditional" flips the sign for a user whose
# preference differs from their OS.
DEFAULT_CONSOLE = {
    "window": {"w": 1280, "h": 1740},
    "font_scale": 1.0,
    "theme": "dark",
    "theme_custom": {},
    "scroll_direction": "natural",
    "tabs": {},
    "dashboard": {"placements": []},
}
_CONSOLE_FIELDS = ("window", "font_scale", "theme", "theme_custom",
                   "scroll_direction", "tabs", "dashboard")
# The color keys theme_custom accepts; anything else is dropped on coerce so a
# fork can't smuggle an arbitrary object into the durable block.
_THEME_CUSTOM_KEYS = ("bg", "accent")
# The scroll_direction values accepted; anything else is dropped on coerce.
_SCROLL_DIRECTIONS = ("natural", "traditional")


def _retention_ns(intelligence_id: str) -> str:
    return f"intelligence/{intelligence_id}/config/retention"


def _heartbeat_ns(intelligence_id: str) -> str:
    return f"intelligence/{intelligence_id}/config/heartbeat"


def _console_prefix(intelligence_id: str) -> str:
    return f"intelligence/{intelligence_id}/config/console/"


def _console_ns(intelligence_id: str, field: str) -> str:
    return _console_prefix(intelligence_id) + field


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


# --------------------------------------------------------------------------- #
# Pure logic: console personalization ([CXC-ICP-FOUND-A])
# --------------------------------------------------------------------------- #
def _unwrap(val: Any) -> Any:
    """A config surface MAY publish a bare scalar OR a ``{"value": x}`` wrapper
    (the retention idiom).  Unwrap the single-key wrapper; leave everything
    else — including a real field object like ``window`` — untouched."""
    if isinstance(val, dict) and set(val.keys()) == {"value"}:
        return val["value"]
    return val


def _coerce_console_field(field: str, val: Any, current: Any) -> Optional[Any]:
    """Validate + normalize ONE console field to its canonical JSON shape, or
    return ``None`` for an out-of-domain value (the caller then no-ops, never
    writes garbage — the config_persister ``changed=False`` discipline)."""
    if field == "window":
        src = val if isinstance(val, dict) else None
        if src is None:
            return None
        base = current if isinstance(current, dict) else {}
        w = _as_int(src.get("w", base.get("w")))
        h = _as_int(src.get("h", base.get("h")))
        if w is None or h is None or w < 1 or h < 1:
            return None
        return {"w": w, "h": h}
    if field == "font_scale":
        v = _unwrap(val)
        if isinstance(v, bool):
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None
    if field == "theme":
        v = _unwrap(val)
        return v.strip() if isinstance(v, str) and v.strip() else None
    if field == "scroll_direction":
        v = _unwrap(val)
        if isinstance(v, str) and v.strip().lower() in _SCROLL_DIRECTIONS:
            return v.strip().lower()
        return None
    if field == "theme_custom":
        v = _unwrap(val)
        if not isinstance(v, dict):
            return None
        out: Dict[str, Any] = {}
        for key in _THEME_CUSTOM_KEYS:
            col = v.get(key)
            if isinstance(col, (list, tuple)) and 3 <= len(col) <= 4:
                try:
                    ch = [int(c) for c in col]
                except (TypeError, ValueError):
                    continue
                if all(0 <= c <= 255 for c in ch):
                    out[key] = ch
        # an empty map is a valid override state ("clear my customizations");
        # allow it so a reset persists rather than no-opping.
        return out
    if field == "tabs":
        if not isinstance(val, dict):
            return None
        try:
            return json.loads(json.dumps(val))     # scalar/list-only deep copy
        except (TypeError, ValueError):
            return None
    if field == "dashboard":
        if not isinstance(val, dict):
            return None
        try:
            d = json.loads(json.dumps(val))
        except (TypeError, ValueError):
            return None
        if not isinstance(d.get("placements"), list):
            d["placements"] = []
        return d
    return None


def _persist_console(meta: Dict[str, Any], field: str,
                     val: Any) -> Tuple[Dict[str, Any], bool]:
    """Fold a ``console/<field>`` edit into ``meta.console``, preserving every
    sibling ``console`` key (and every ``lifecycle`` key untouched).  No-ops
    (unchanged value / unknown field / invalid value) return changed=False and
    NEVER create a spurious ``console`` block."""
    if field not in _CONSOLE_FIELDS:
        return meta, False
    cons = meta.get("console") if isinstance(meta.get("console"), dict) else {}
    new = _coerce_console_field(field, val, cons.get(field))
    if new is None or cons.get(field) == new:
        return meta, False
    cons = dict(cons)
    cons[field] = new
    meta["console"] = cons
    return meta, True


def console_settings(meta: Dict[str, Any]) -> Dict[str, Any]:
    """The LAUNCH-path read seam ([CXC-ICP-FOUND-A]): the full personalization
    object with every field present, persisted values overlaid on the coded
    DEFAULTS.  A meta-object with NO ``console`` block returns the stated
    defaults — never raises, mirroring ``seed_values``' empty-meta fallback."""
    cons = meta.get("console") if isinstance(meta.get("console"), dict) else {}
    out: Dict[str, Any] = {
        "window": dict(DEFAULT_CONSOLE["window"]),
        "font_scale": DEFAULT_CONSOLE["font_scale"],
        "theme": DEFAULT_CONSOLE["theme"],
        "theme_custom": {},
        "scroll_direction": DEFAULT_CONSOLE["scroll_direction"],
        "tabs": {},
        "dashboard": {"placements": []},
    }
    for field in _CONSOLE_FIELDS:
        norm = _coerce_console_field(field, cons.get(field), None) \
            if cons.get(field) is not None else None
        if norm is not None:
            out[field] = norm
    return out


# --------------------------------------------------------------------------- #
# Pure logic: apply a config edit into the meta-object lifecycle
# --------------------------------------------------------------------------- #
def persist_config(meta: Dict[str, Any], ns: str, val: Any,
                   intelligence_id: str = "architect") -> Tuple[Dict[str, Any], bool]:
    """Fold a config-namespace edit into ``meta.lifecycle`` (retention/heartbeat)
    or ``meta.console`` (the [CXC-ICP-FOUND-A] personalization block).  Returns
    (meta, changed); ``changed`` is False for an unparsable value or a no-op
    write (so the caller skips a redundant disk write).  Mutates + returns the
    same dict (read-modify-write)."""
    cpref = _console_prefix(intelligence_id)
    if ns.startswith(cpref):
        return _persist_console(meta, ns[len(cpref):], val)

    lc = meta.get("lifecycle")
    if not isinstance(lc, dict):
        lc = {}
        meta["lifecycle"] = lc

    if ns == _retention_ns(intelligence_id):
        payload = val
        if isinstance(payload, dict):
            payload = payload.get("cleanupPeriodDays", payload.get("value"))
        days = _as_int(payload)
        if days is None or days < 1:
            return meta, False
        if lc.get("retention_days") == days:
            return meta, False
        lc["retention_days"] = days
        return meta, True

    if ns == _heartbeat_ns(intelligence_id):
        if not isinstance(val, dict):
            return meta, False
        cur = lc.get("heartbeat")
        cur = dict(cur) if isinstance(cur, dict) else {}
        merged = dict(cur)
        for k in ("base", "backoff_after", "then"):
            if k in val:
                iv = _as_int(val[k])
                if iv is not None and iv >= 1:
                    merged[k] = iv
        if merged == cur:
            return meta, False
        lc["heartbeat"] = merged
        return meta, True

    return meta, False


def seed_values(meta: Dict[str, Any]) -> Dict[str, Any]:
    """The reverse map: the config-namespace payloads to RE-PUBLISH so a late-joining
    widget shows the persisted values.  Falls back to the coded defaults for any key
    the meta-object has not persisted yet.  Keyed by the *ns suffix* ("retention" /
    "heartbeat") so the caller can build the full namespace for its id."""
    lc = meta.get("lifecycle") if isinstance(meta.get("lifecycle"), dict) else {}
    days = _as_int(lc.get("retention_days"), DEFAULT_RETENTION_DAYS)
    hb = lc.get("heartbeat") if isinstance(lc.get("heartbeat"), dict) else {}
    heartbeat = dict(DEFAULT_HEARTBEAT)
    for k in ("base", "backoff_after", "then"):
        iv = _as_int(hb.get(k))
        if iv is not None and iv >= 1:
            heartbeat[k] = iv
    return {
        "retention": {"cleanupPeriodDays": days},
        "heartbeat": heartbeat,
        # [CXC-ICP-FOUND-A] the console personalization block, defaults-filled,
        # keyed per-field so ``reseed`` re-publishes each on its own namespace.
        "console": console_settings(meta),
    }


def _write_meta(path: str, meta: Dict[str, Any]) -> bool:
    """Atomically write the meta-object (temp file + os.replace) so a crash can never
    leave a truncated file.  Mirrors bootstrap_manifest._write_meta_threshold."""
    try:
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".meta.", suffix=".json", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(meta, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except (OSError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# The persister (pure logic over an injected publish callable — testable)
# --------------------------------------------------------------------------- #
class ConfigPersister:
    """Bus <-> meta-object config bridge.  ``publish(ns, val)`` re-seeds a value to the
    bus; ``on_config(ns, val, src)`` folds a bus edit into the meta-object file."""

    def __init__(self, meta_path: str, publish: Callable[[str, Any], None],
                 intelligence_id: str = "architect",
                 log: Optional[Callable[[str], None]] = None):
        self.meta_path = meta_path
        self.publish = publish
        self.id = intelligence_id
        self._log = log or (lambda line: None)
        # ns -> the value WE last published on it ([CXC-SOB-SUBSCRIBE] echo
        # guard; see the module docstring for why this replaces the `src` check).
        self._echo: Dict[str, Any] = {}

    def _read_meta(self) -> Optional[Dict[str, Any]]:
        try:
            with open(self.meta_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError) as exc:
            self._log(f"[config_persister] meta read error: {exc!r}")
            return None

    def _publish_seed(self, ns: str, val: Any) -> None:
        """Publish a re-seed AND remember it, so the self-callback it provokes is
        recognisable as our own echo ([CXC-SOB-SUBSCRIBE])."""
        self._echo[ns] = val
        self.publish(ns, val)

    def is_own_echo(self, ns: str, val: Any) -> bool:
        """True iff *val* is exactly what we last re-seeded on *ns*.

        The bus hands a subscriber ``(namespace, new, old)`` and NO ``src``, so
        the old ``src == config_persister`` loop-guard cannot be expressed. This
        is its replacement, and it is what keeps a defaults-filled re-seed from
        being written back into the meta-object as if a human had chosen it.
        """
        prev = self._echo.get(ns, _NEVER)
        return prev is not _NEVER and prev == val

    def on_config(self, ns: str, val: Any, src: str = "") -> bool:
        """A config edit arrived on the bus -> persist it.  Ignores our OWN re-seed
        (the loop-guard: the echo map, plus the legacy ``src`` check that the
        standalone/selftest paths still exercise) and no-op writes."""
        if src == SELF_SRC or self.is_own_echo(ns, val):
            return False
        meta = self._read_meta()
        if meta is None:
            return False
        meta, changed = persist_config(meta, ns, val, self.id)
        if changed:
            if _write_meta(self.meta_path, meta):
                self._log(f"[config_persister] PERSIST {ns} -> {self.meta_path}")
                return True
            self._log(f"[config_persister] !! write FAILED for {ns}")
        return False

    def reseed(self) -> None:
        """Re-publish the persisted config so a late-joining widget shows saved values
        (MQTT state is not retained)."""
        meta = self._read_meta()
        if meta is None:
            return
        payloads = seed_values(meta)
        self._publish_seed(_retention_ns(self.id), payloads["retention"])
        self._publish_seed(_heartbeat_ns(self.id), payloads["heartbeat"])
        # [CXC-ICP-FOUND-A] re-seed each console field on its own namespace so a
        # late-joining config surface / the launch read shows saved values.
        console = payloads["console"]
        for field in _CONSOLE_FIELDS:
            self._publish_seed(_console_ns(self.id, field), console[field])


# --------------------------------------------------------------------------- #
# The bus leg ([SPEC:CXC-SOURCES-ON-BUS] slice 5b)
# --------------------------------------------------------------------------- #
def config_namespaces(intelligence_id: str) -> List[str]:
    """Every namespace this daemon publishes AND subscribes, in declaration order.

    ONE list, used for the declarations, the subscriptions and the SOB-C3 startup
    manifest — so "what we listen to" and "what we re-seed" cannot drift apart.
    The console personalization family is enumerated from ``_CONSOLE_FIELDS``
    rather than wildcarded, which is equivalent because ``_persist_console``
    already refuses any field outside that tuple.
    """
    return ([_retention_ns(intelligence_id), _heartbeat_ns(intelligence_id)]
            + [_console_ns(intelligence_id, f) for f in _CONSOLE_FIELDS])


def run(meta_path: str, mqtt_url: str, intelligence_id: str, poll_s: float,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 5b — a PUBLISH+SUBSCRIBE source.

    The two hazards this shape adds over slice 5a, and how each is answered, are
    written up in the module docstring (ECHO, EXACT SUBSCRIBE).  The third —
    ``run()`` MUST NOT RETURN ([CXC-SOB-RUN-BLOCKS]) — is answered by
    ``source_bus.park``: the old ``while True: reseed(); sleep(poll)`` was doing
    double duty as the re-seed cadence AND as the thing that kept the source
    thread alive, and dropping it for a bare return would have put the daemon in
    ``DataSourceSupervisor``'s reclaim retry loop with no error anywhere.
    ``park(stop, poll_s=poll_s, on_tick=reseed)`` preserves the cadence exactly
    (seed now, then every ``poll_s``) and joins on the supervisor's stop Event.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global; a second instance yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("config-persister")
    if lock is None:
        sys.stderr.write("[config_persister] another config-persister holds the singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    namespaces = config_namespaces(intelligence_id)
    # EXPLICIT scope, never Scope.AUTO ([CXC-SOB-DECLARE]). `compose_projects/intelligence/<id>/*`
    # is EXTERNAL by construction — the edits ORIGINATE outside this process (the
    # channel server, the launcher harness, David) — so it is `global` in Stage A
    # AND in Stage B ([CXC-SOB-INVENTORY] family rules), never census-gated.
    for ns in namespaces:
        source_bus.declare(b, ns, scope=Scope.GLOBAL, source=SELF_SRC,
                           label="intelligence config")
    source_bus.manifest(SELF_SRC)

    persister = ConfigPersister(
        meta_path, lambda ns, val: b.publish_state(ns, val, source=SELF_SRC),
        intelligence_id, log=lambda s: sys.stderr.write(s + "\n"))

    def _on_config(ns: str, value, _old) -> None:
        # The bus callback contract ([CXC-SOB-SUBSCRIBE]): (namespace, new, old),
        # NOT paho's (client, userdata, msg), and `value` arrives DECODED — the
        # old handler's `json.loads(msg.payload)` would raise on every message.
        persister.on_config(ns, value)

    for ns in namespaces:
        b.subscribe(ns, _on_config)
    sys.stderr.write(f"[config_persister] persisting {intelligence_id} config -> "
                     f"{meta_path}; listening {len(namespaces)} namespace(s)\n")

    persister.reseed()                 # seed NOW, then park — the slice-2 shape
    source_bus.park(stop, poll_s=poll_s, on_tick=persister.reseed)
    return 0


# --------------------------------------------------------------------------- #
# Selftest — no broker, full round-trip on a real temp meta-object file
# --------------------------------------------------------------------------- #
def _selftest() -> int:
    failures = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    RET = _retention_ns("architect")
    HB = _heartbeat_ns("architect")

    # (a) persist_config folds a retention edit into lifecycle.retention_days.
    meta = {"id": "architect", "lifecycle": {"compact_threshold": 0.93,
                                             "note": "keep me"}}
    meta, ch = persist_config(meta, RET, {"cleanupPeriodDays": 45})
    check(ch and meta["lifecycle"]["retention_days"] == 45,
          f"(a) retention not persisted: {meta!r}")
    check(meta["lifecycle"]["compact_threshold"] == 0.93
          and meta["lifecycle"]["note"] == "keep me",
          "(a) sibling lifecycle keys were clobbered")

    # (b) an identical retention edit is a NO-OP (changed=False → no redundant write).
    _, ch2 = persist_config(meta, RET, {"cleanupPeriodDays": 45})
    check(ch2 is False, "(b) idempotent retention write not detected as no-op")

    # (c) heartbeat cadence merges the triple (minutes).
    meta, ch3 = persist_config(meta, HB, {"base": 15, "backoff_after": 90, "then": 120})
    check(ch3 and meta["lifecycle"]["heartbeat"] ==
          {"base": 15, "backoff_after": 90, "then": 120},
          f"(c) heartbeat not persisted: {meta!r}")
    # a partial heartbeat edit merges, preserving the untouched fields.
    meta, ch4 = persist_config(meta, HB, {"base": 10})
    check(ch4 and meta["lifecycle"]["heartbeat"] ==
          {"base": 10, "backoff_after": 90, "then": 120},
          f"(c) partial heartbeat edit did not merge: {meta!r}")

    # (d) seed_values round-trips the persisted values back to the bus payloads.
    seeds = seed_values(meta)
    check(seeds["retention"] == {"cleanupPeriodDays": 45},
          f"(d) retention seed wrong: {seeds!r}")
    check(seeds["heartbeat"] == {"base": 10, "backoff_after": 90, "then": 120},
          f"(d) heartbeat seed wrong: {seeds!r}")

    # (e) an empty meta-object seeds the CODED defaults (no crash).
    seeds0 = seed_values({})
    check(seeds0["retention"] == {"cleanupPeriodDays": DEFAULT_RETENTION_DAYS}
          and seeds0["heartbeat"] == DEFAULT_HEARTBEAT,
          f"(e) empty-meta defaults wrong: {seeds0!r}")

    # (f) FULL restart round-trip on a real file, through ConfigPersister:
    #     edit on the bus -> file -> (simulated restart) -> re-seed shows David's value.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "architect.meta.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"id": "architect", "role": "keep",
                       "lifecycle": {"compact_threshold": 0.93}}, fh)
        published = []
        p = ConfigPersister(path, lambda ns, val: published.append((ns, val)))
        # David edits the cadence in the UI -> the widget publishes -> we persist.
        wrote = p.on_config(HB, {"base": 20, "backoff_after": 45, "then": 90}, src="widget")
        check(wrote, "(f) on_config did not persist the cadence edit")
        # our OWN re-seed must NOT trigger a write (loop-guard).
        check(p.on_config(HB, {"base": 20}, src=SELF_SRC) is False,
              "(f) persister re-wrote off its own re-seed (loop-guard broken)")
        # the file on disk carries it, and other keys survived.
        with open(path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        check(on_disk["lifecycle"]["heartbeat"] ==
              {"base": 20, "backoff_after": 45, "then": 90},
              f"(f) cadence not durable on disk: {on_disk!r}")
        check(on_disk["role"] == "keep"
              and on_disk["lifecycle"]["compact_threshold"] == 0.93,
              "(f) restart round-trip clobbered sibling keys")
        # SIMULATE A RESTART: a fresh persister re-seeds from disk -> the bus payload
        # carries David's saved cadence (the widget will show it, not the default).
        published.clear()
        ConfigPersister(path, lambda ns, val: published.append((ns, val))).reseed()
        pub = dict(published)
        check(pub.get(HB) == {"base": 20, "backoff_after": 45, "then": 90},
              f"(f) restart re-seed did not carry the saved cadence: {published!r}")
        check(pub.get(RET) == {"cleanupPeriodDays": DEFAULT_RETENTION_DAYS},
              f"(f) restart re-seed retention wrong: {published!r}")

    # (g) [CXC-ICP-FOUND-A] the console personalization block: each console/*
    #     edit folds into meta.console preserving lifecycle + sibling console
    #     keys; identical edits no-op; seed_values round-trips; empty meta ->
    #     stated defaults.
    CID = "architect"
    FONT = _console_ns(CID, "font_scale")
    WIN = _console_ns(CID, "window")
    THEME = _console_ns(CID, "theme")
    TABS = _console_ns(CID, "tabs")
    DASH = _console_ns(CID, "dashboard")

    cmeta: Dict[str, Any] = {"id": CID, "lifecycle": {"retention_days": 30}}
    cmeta, cg1 = persist_config(cmeta, FONT, 1.25, CID)
    check(cg1 and cmeta["console"]["font_scale"] == 1.25,
          f"(g) font_scale not folded into meta.console: {cmeta!r}")
    check(cmeta["lifecycle"]["retention_days"] == 30,
          "(g) folding console clobbered lifecycle")
    # an identical font edit is a NO-OP.
    _, cg1b = persist_config(cmeta, FONT, 1.25, CID)
    check(cg1b is False, "(g) identical font_scale edit not a no-op")
    # a sibling console field (theme) folds WITHOUT clobbering font_scale.
    cmeta, cg2 = persist_config(cmeta, THEME, "high-contrast", CID)
    check(cg2 and cmeta["console"]["theme"] == "high-contrast"
          and cmeta["console"]["font_scale"] == 1.25,
          f"(g) theme edit clobbered a sibling console key: {cmeta!r}")
    # window merges w/h; tabs + dashboard land as objects.
    cmeta, _ = persist_config(cmeta, WIN, {"w": 1024, "h": 768}, CID)
    cmeta, _ = persist_config(
        cmeta, TABS, {"collaboration": {"widgets": ["a", "b"], "hidden": ["c"]}}, CID)
    cmeta, _ = persist_config(
        cmeta, DASH, {"placements": [{"widget": "gauge", "row": 0, "col": 0,
                                      "w": 2, "h": 1}]}, CID)
    check(cmeta["console"]["window"] == {"w": 1024, "h": 768},
          f"(g) window not persisted: {cmeta!r}")
    check(cmeta["console"]["tabs"]["collaboration"]["widgets"] == ["a", "b"],
          f"(g) tabs not persisted: {cmeta!r}")
    check(cmeta["console"]["dashboard"]["placements"][0]["widget"] == "gauge",
          f"(g) dashboard placements not persisted: {cmeta!r}")
    # theme_custom ([CXC-ICP-S3-THEME]): folds as a color-override map, drops a
    # malformed/unknown key, preserves siblings.
    TCUST = _console_ns(CID, "theme_custom")
    cmeta, cg3 = persist_config(
        cmeta, TCUST,
        {"bg": [10, 12, 20], "accent": [255, 120, 0], "junk": "x",
         "bad": [999, 0]}, CID)
    check(cg3 and cmeta["console"]["theme_custom"] == {
        "bg": [10, 12, 20], "accent": [255, 120, 0]},
          f"(g) theme_custom not coerced/folded: {cmeta!r}")
    check(cmeta["console"]["font_scale"] == 1.25,
          "(g) theme_custom edit clobbered a sibling console key")
    # an out-of-domain value (empty theme, negative font) is a no-op, never a write.
    _, cbad1 = persist_config(cmeta, THEME, "", CID)
    _, cbad2 = persist_config(cmeta, FONT, -1, CID)
    check(cbad1 is False and cbad2 is False,
          "(g) invalid console value was not rejected as a no-op")
    # seed_values round-trips the persisted console block back to bus payloads.
    cseeds = seed_values(cmeta)["console"]
    check(cseeds["font_scale"] == 1.25 and cseeds["theme"] == "high-contrast"
          and cseeds["window"] == {"w": 1024, "h": 768}
          and cseeds["theme_custom"] == {"bg": [10, 12, 20],
                                         "accent": [255, 120, 0]},
          f"(g) console seed round-trip wrong: {cseeds!r}")
    # an empty meta-object seeds the STATED console defaults (no crash, no write).
    cdef = seed_values({})["console"]
    check(cdef == DEFAULT_CONSOLE, f"(g) empty-meta console defaults wrong: {cdef!r}")
    check(console_settings({}) == DEFAULT_CONSOLE,
          "(g) console_settings empty-meta defaults wrong")

    # (h) [SPEC:CXC-SOURCES-ON-BUS] slice 5b — the ECHO GUARD, which replaces the
    #     `src == config_persister` loop-guard the bus callback contract removed
    #     ((namespace, new, old): there is no `src`). THE CASE THAT MATTERS is the
    #     DEFAULTS-FILLED re-seed: a meta-object with no `console` block re-seeds
    #     theme="dark" etc., and an unguarded self-callback would write those
    #     defaults into the durable file — materializing a `console` block
    #     `_persist_console` promises never to create.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "architect.meta.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"id": "architect"}, fh)      # NO lifecycle, NO console
        seeded: List[Any] = []
        p = ConfigPersister(path, lambda ns, val: seeded.append((ns, val)))
        p.reseed()
        check(len(seeded) == 2 + len(_CONSOLE_FIELDS),
              f"(h) re-seed did not cover every namespace: {len(seeded)}")
        # feed EVERY re-seeded value straight back in, as the bus does
        for ns, val in seeded:
            check(p.on_config(ns, val) is False,
                  f"(h) the daemon persisted its OWN re-seed on {ns}")
        with open(path, encoding="utf-8") as fh:
            after = json.load(fh)
        check(after == {"id": "architect"},
              f"(h) a re-seed echo MUTATED the meta-object: {after!r}")
        # ...and a REAL edit (a value we did not publish) still lands.
        check(p.on_config(_console_ns("architect", "theme"), "high-contrast") is True,
              "(h) the echo guard swallowed a genuine edit")
        with open(path, encoding="utf-8") as fh:
            check(json.load(fh)["console"]["theme"] == "high-contrast",
                  "(h) the genuine edit did not reach disk")

    # (i) the namespace list is ONE list — declarations, subscriptions and the
    #     SOB-C3 manifest all read it, so "what we listen to" and "what we
    #     re-seed" cannot drift apart.
    nss = config_namespaces("architect")
    check(nss[0] == RET and nss[1] == HB and len(nss) == 2 + len(_CONSOLE_FIELDS),
          f"(i) config_namespaces wrong: {nss!r}")
    check(all(n.startswith("intelligence/architect/config/") for n in nss),
          f"(i) a namespace escaped the compose_projects/intelligence/<id>/config/ family: {nss!r}")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    print("PASS: config_persister selftest (9/9 groups)")
    for line in (
        "  (a) a retention edit folds into lifecycle.retention_days; siblings preserved",
        "  (b) an identical edit is a no-op (no redundant disk write)",
        "  (c) the heartbeat cadence merges the triple; partial edits preserve fields",
        "  (d) seed_values round-trips the persisted values back to bus payloads",
        "  (e) an empty meta-object seeds the coded defaults (never crashes)",
        "  (f) FULL restart round-trip on a real file: UI edit -> disk -> re-seed shows it",
        "  (g) the console personalization block folds/no-ops/round-trips; empty -> defaults",
        "  (h) the ECHO GUARD: a defaults-filled re-seed fed back in NEVER writes",
        "  (i) one namespace list drives declare + subscribe + the SOB-C3 manifest",
    ):
        print(line)
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Persist the Configuration tab's global settings to the meta-object.")
    ap.add_argument("--meta", default=_DEFAULT_META)
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--id", default=os.environ.get("INTELLIGENCE_ID", "architect"))
    ap.add_argument("--poll", type=float, default=15.0)
    ap.add_argument("--once", action="store_true",
                    help="print the seed payloads read from the meta-object and exit")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.once:
        try:
            with open(a.meta, encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            meta = {}
        print(json.dumps(seed_values(meta), indent=2))
        return 0
    return run(a.meta, a.mqtt, a.id, a.poll, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
