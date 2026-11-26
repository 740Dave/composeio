"""SelfSensor — an Intelligence self-instrumentation SENSOR peripheral.

In HAL terms ([SPEC:CXC-HAL-DECLARE] / [CXC-HALD-ROLE]) this is a SENSOR whose
namespaces are ``self/*``: the Intelligence instruments ITSELF onto the
ComposeBus exactly the way a bme280 publishes ``env/temperature``.  A sensor
publishes; the Intelligence Console (the subscriber) reads ``self/*`` to render
the self-context gauge, the registration green-light, the subagent ledger.

Two sources, in priority order:

  1. **stdin (PRIMARY)** — the Claude Code statusline JSON.  Since v2.1.6 this
     carries a NATIVE, authoritative ``context_window.used_percentage`` (the
     predecessor's 82.4% was a JSONL ESTIMATE — this is the real number and it
     WINS), plus ``context_window_size``, ``cost``, ``rate_limits`` (5h/7d) and
     ``effort.level``.  It also carries ``transcript_path``, so the rich
     JSONL-derived readings (subagents, registration, compactions) are still
     available alongside the authoritative context figure.

  2. **JSONL (FALLBACK)** — when no statusline is on stdin (or it lacks the
     fields), parse the session ``.jsonl`` directly.  This is the original
     :class:`SessionSensor` logic, preserved byte-for-byte below, so every
     namespace the prototype computed is still produced.

Extraction pattern learned from haven-hud (``src/transcript.ts`` /
``src/context-cache.ts`` / ``src/types.ts``): accumulate
``assistant.message.usage``, read the latest frame for current context, pull
``subagent_type`` from Task ``tool_use``, watch ``compact_boundary`` entries,
and — when present — prefer the statusline's native ``used_percentage``.

This slice COMPUTES + EXPOSES the publish set (``to_bus_lines``); it does not
open a live bus/MQTT link — that wiring is the Console's job in a later slice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

# files whose Read means "this instance re-registered on its dusk gifts / CRP".
# This is David's "so he never has to ASK" green light — Problem 6 wired into an
# instrument: self/registered flips true the moment the session reads one.
REGISTRATION_MARKERS = (
    "dusk-gifts", "registration-protocol", "compact-continuity-study",
    "game-registration", "cognitive_registration", "COGNITIVE_REGISTRATION",
    "soul.md", "SOUL.md",
)

# The default self-model context window (Claude Code 1M-token sessions).  The
# statusline's context_window_size overrides this when present.
DEFAULT_CONTEXT_WINDOW = 1_000_000


# ---------------------------------------------------------------------------
# The JSONL engine — the original prototype logic, preserved EXACTLY.
# ---------------------------------------------------------------------------

class SessionSensor:
    """Parse a Claude Code session ``.jsonl`` and compute the ``self/*`` readout.

    This is the FALLBACK engine (and the original prototype): it accumulates
    ``assistant.message.usage``, reads the latest frame for current context,
    counts Task/Read tool_use, attaches subagent token utilization, and watches
    ``compact_boundary`` entries.  Its context figure is an ESTIMATE (the last
    frame's input+cache tokens); the statusline overrides it when available.
    """

    def __init__(self, jsonl_path: str, context_window: int = DEFAULT_CONTEXT_WINDOW):
        self.path = Path(jsonl_path)
        self.window = context_window

    def parse(self) -> dict:
        cur_ctx = 0                     # latest frame's context size (tokens)
        out_total = 0                   # cumulative output tokens
        subagents: list[dict] = []      # Task calls + token utilization
        reads: list[str] = []           # every file this session Read
        compactions = 0
        tools: dict[str, int] = {}      # tool name -> count

        with self.path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                t = e.get("type")

                if t == "assistant":
                    msg = e.get("message") or {}
                    u = msg.get("usage") or {}
                    if u:
                        frame = (int(u.get("input_tokens", 0) or 0)
                                 + int(u.get("cache_read_input_tokens", 0) or 0)
                                 + int(u.get("cache_creation_input_tokens", 0) or 0))
                        if frame:
                            cur_ctx = frame            # latest wins = current context
                        out_total += int(u.get("output_tokens", 0) or 0)
                    for b in (msg.get("content") or []):
                        if isinstance(b, dict) and b.get("type") == "tool_use":
                            name = b.get("name", "?")
                            tools[name] = tools.get(name, 0) + 1
                            inp = b.get("input") or {}
                            if name in ("Task", "Agent"):
                                subagents.append({
                                    "type": inp.get("subagent_type", "agent"),
                                    "desc": inp.get("description", ""),
                                    "tokens": None,   # filled from the result below
                                })
                            elif name == "Read":
                                fp = inp.get("file_path", "")
                                if fp:
                                    reads.append(fp)

                # subagent token utilization arrives in the tool_result / notification
                if t in ("user", "tool_result", "system"):
                    txt = json.dumps(e)
                    if "subagent_tokens" in txt:
                        # attach to the most recent subagent still missing tokens
                        try:
                            tok = int(txt.split("subagent_tokens")[1]
                                      .lstrip('":> ').split("<")[0].split(",")[0].split("}")[0])
                            for s in reversed(subagents):
                                if s["tokens"] is None:
                                    s["tokens"] = tok
                                    break
                        except Exception:
                            pass

                if t == "system" and "compact" in json.dumps(e).lower():
                    if e.get("subtype") == "compact_boundary" or "compact_boundary" in json.dumps(e):
                        compactions += 1

        registered_files = sorted({fp for fp in reads
                                    if any(m in fp for m in REGISTRATION_MARKERS)})
        sub_tok = sum(s["tokens"] for s in subagents if s["tokens"])

        return {
            "self/context_level": round(cur_ctx / self.window, 3),
            "self/context_tokens": cur_ctx,
            "self/context_window": self.window,
            "self/output_tokens": out_total,
            "self/tool_calls": sum(tools.values()),
            "self/subagents/count": len(subagents),
            "self/subagents/tokens": sub_tok,
            "self/subagents/list": subagents,
            "self/registered": bool(registered_files),
            "self/registered/files": registered_files,
            "self/compactions": compactions,
            "self/reads_total": len(reads),
            "_tools": tools,
            "_reads": reads,
        }

    def to_bus_lines(self) -> list[tuple[str, object]]:
        """The (namespace, value) pairs this sensor would publish to the ComposeBus."""
        d = self.parse()
        return [(k, v) for k, v in d.items() if not k.startswith("_")
                and not k.endswith("/list") and not k.endswith("/files")]


# ---------------------------------------------------------------------------
# statusline (stdin) parsing helpers ([types.ts] StdinData shape).
# ---------------------------------------------------------------------------

def _effort_level(effort: Any) -> Optional[str]:
    """Normalize the ``effort`` field to a level string.

    Claude Code ≥2.1.115 sends ``{level: "max"}``; the original PR future-proofed
    a bare-string form.  Both collapse to the level string here."""
    if isinstance(effort, dict):
        lvl = effort.get("level")
        return str(lvl) if lvl is not None else None
    if isinstance(effort, str):
        return effort
    return None


def _rate_limit(rate_limits: Any) -> Optional[dict]:
    """Normalize ``rate_limits`` to ``{window: {used_percentage, resets_at}}``
    for the two subscriber windows (5h / 7d)."""
    if not isinstance(rate_limits, dict):
        return None
    out: dict[str, dict] = {}
    for key in ("five_hour", "seven_day"):
        w = rate_limits.get(key)
        if isinstance(w, dict):
            out[key] = {
                "used_percentage": w.get("used_percentage"),
                "resets_at": w.get("resets_at"),
            }
    return out or None


def _context_tokens(ctx_win: dict) -> Optional[int]:
    """Current context size (tokens) from ``current_usage`` — the same frame sum
    the JSONL engine computes (input + cache_read + cache_creation)."""
    cu = ctx_win.get("current_usage")
    if not isinstance(cu, dict):
        return None
    frame = (int(cu.get("input_tokens", 0) or 0)
             + int(cu.get("cache_read_input_tokens", 0) or 0)
             + int(cu.get("cache_creation_input_tokens", 0) or 0))
    return frame or None


# ---------------------------------------------------------------------------
# Manifest-driven registration ([CXC-IC-SELF]; the meta-object DEFINES what
# "registered" means, the sensor reports it per-artifact so the widget can show
# a checklist — role descriptor + each dusk gift, green/red).
# ---------------------------------------------------------------------------

def _norm_path(p: str) -> str:
    """Normalize a path for suffix comparison: forward slashes, no leading
    ``./`` or ``/`` — so an absolute Read path and a repo-relative manifest
    path compare cleanly."""
    p = str(p or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _read_matches(read_path: str, artifact_rel: str) -> bool:
    """True iff a session Read ``file_path`` refers to the manifest artifact.

    Matches by SUFFIX on the normalized relative path (so an absolute Read of
    ``/Users/you/composeio/bootstrap/dusk-gifts/README.md`` matches the repo-relative
    ``bootstrap/dusk-gifts/README.md`` but NOT ``docs/other/README.md``).  A bare
    filename artifact falls back to basename equality."""
    r = _norm_path(read_path)
    a = _norm_path(artifact_rel)
    if not r or not a:
        return False
    if r == a or r.endswith("/" + a):
        return True
    # bare-filename artifact (no directory): basename equality is all we have.
    if "/" not in a:
        return r.rsplit("/", 1)[-1] == a
    return False


# The human-facing KIND labels the manifest-driven caption groups by ([CXC-IC-SELF]).
# role-descriptor and dusk-gifts have fixed kinds; a first-class bootstrap doc's kind
# is derived from its OWN declared role, so a NEW bootstrap doc needs no code here.
_KIND_ROLE_DESCRIPTOR = "role def"
_KIND_DUSK_GIFT = "dusk gift"


def _bootstrap_kind(role: Optional[str]) -> str:
    """Friendly KIND label for a first-class bootstrap doc, from its declared
    ``role`` (``bootstrap_read`` -> ``bootstrap-read``, ``method`` -> ``method``)."""
    return str(role).replace("_", "-") if role else "bootstrap doc"


def load_registration_manifest(manifest_path: str) -> dict:
    """Parse a meta-object ``.json`` into the registration spec the sensor uses.

    Returns ``{"required": [rel, ...], "meta": {rel: {role, kind, why}}}`` where
    the per-artifact role/kind/why is pulled from EVERY first-class artifact the
    meta-object declares, so a manifest-driven caption can name each by its kind
    ([CXC-IC-SELF]):

      * ``role_descriptor`` -> role ``"role_descriptor"``, kind ``"role def"``;
      * each first-class BOOTSTRAP doc (any top-level object carrying ``path`` +
        ``standalone_ux`` — ``principles``, ``method_flow``, ``bootstrap_read``,
        ``toolbox``, and any FUTURE such doc, detected structurally so adding one
        needs NO code change) -> its declared ``role``, kind from that role;
      * each ``dusk_gifts[]`` entry -> its declared ``role``, kind ``"dusk gift"``.

    Unknown-but-required artifacts still appear in the checklist (role/kind
    ``None``) — the meta-object is the source of truth for what is enriched."""
    obj = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    reg = obj.get("registration") or {}
    required = [str(p) for p in (reg.get("required") or [])]
    meta: dict[str, dict] = {}
    rd = obj.get("role_descriptor") or {}
    if isinstance(rd, dict) and rd.get("path"):
        meta[_norm_path(rd["path"])] = {
            "role": "role_descriptor", "kind": _KIND_ROLE_DESCRIPTOR,
            "why": rd.get("why")}
    # first-class BOOTSTRAP docs — structural detection (path + standalone_ux),
    # so principles/method_flow/bootstrap_read/toolbox (and any future doc) are
    # each enriched with their own kind, no per-doc code.
    for key, val in obj.items():
        if key in ("role_descriptor", "dusk_gifts"):
            continue
        if isinstance(val, dict) and val.get("path") and val.get("standalone_ux"):
            meta[_norm_path(val["path"])] = {
                "role": val.get("role"),
                "kind": _bootstrap_kind(val.get("role")),
                "why": val.get("why")}
    for g in (obj.get("dusk_gifts") or []):
        if isinstance(g, dict) and g.get("path"):
            meta[_norm_path(g["path"])] = {
                "role": g.get("role"), "kind": _KIND_DUSK_GIFT,
                "why": g.get("why")}
    return {"required": required, "meta": meta}


# ---------------------------------------------------------------------------
# SelfSensor — the public sensor: stdin PRIMARY, JSONL FALLBACK.
# ---------------------------------------------------------------------------

class SelfSensor:
    """The ``self/*`` sensor peripheral: authoritative statusline over JSONL.

    Construct from stdin (``from_stdin``), from a session file (``from_jsonl``),
    or let ``auto`` pick — stdin if a statusline is piped in, else the JSONL
    fallback.  :meth:`parse` returns the merged ``self/*`` readings dict;
    :meth:`to_bus_lines` returns the (namespace, value) publish set.
    """

    def __init__(self,
                 stdin_data: Optional[dict] = None,
                 jsonl_path: Optional[str] = None,
                 context_window: int = DEFAULT_CONTEXT_WINDOW,
                 registration_manifest: Optional[str] = None):
        self.stdin = stdin_data or None
        self.jsonl_path = jsonl_path
        self.window = context_window
        # optional meta-object .json ([CXC-IC-META]): when set, self/registered
        # is manifest-driven (per-artifact) instead of the legacy marker match.
        self.registration_manifest = registration_manifest

    # -- constructors --------------------------------------------------------

    @classmethod
    def from_stdin(cls, raw: Any,
                   context_window: int = DEFAULT_CONTEXT_WINDOW,
                   registration_manifest: Optional[str] = None) -> "SelfSensor":
        """Build from a statusline payload — a JSON string or an already-parsed
        dict.  ``transcript_path`` (if present) still drives the JSONL layer."""
        data = raw
        if isinstance(raw, (str, bytes)):
            data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("statusline stdin must be a JSON object")
        # A statusline-supplied context_window_size overrides the default.
        ctx_win = data.get("context_window") or {}
        window = int(ctx_win.get("context_window_size") or context_window)
        return cls(stdin_data=data,
                   jsonl_path=data.get("transcript_path"),
                   context_window=window,
                   registration_manifest=registration_manifest)

    @classmethod
    def from_jsonl(cls, jsonl_path: str,
                   context_window: int = DEFAULT_CONTEXT_WINDOW,
                   registration_manifest: Optional[str] = None) -> "SelfSensor":
        """Build from a session ``.jsonl`` (the fallback engine, no stdin)."""
        return cls(jsonl_path=jsonl_path, context_window=context_window,
                   registration_manifest=registration_manifest)

    @classmethod
    def auto(cls, argv: Optional[list[str]] = None,
             context_window: int = DEFAULT_CONTEXT_WINDOW,
             registration_manifest: Optional[str] = None) -> "SelfSensor":
        """Auto-detect: a statusline piped on stdin wins; else fall back to a
        JSONL path (``argv[0]`` or the session default)."""
        raw = ""
        if not sys.stdin.isatty():
            try:
                raw = sys.stdin.read()
            except Exception:
                raw = ""
        raw = (raw or "").strip()
        if raw:
            try:
                return cls.from_stdin(raw, context_window=context_window,
                                      registration_manifest=registration_manifest)
            except Exception:
                pass  # not a statusline payload -> fall through to JSONL
        argv = argv if argv is not None else sys.argv[1:]
        path = argv[0] if argv else str(
            Path.home() / ".claude/projects/-Users-you/"
            "59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3.jsonl")
        return cls.from_jsonl(path, context_window=context_window,
                              registration_manifest=registration_manifest)

    # -- readings ------------------------------------------------------------

    def parse(self) -> dict:
        """Compute the merged ``self/*`` readings.

        Layer 1 (base): the JSONL engine, from an explicit path or the
        statusline's ``transcript_path`` — the full prototype namespace set.
        Layer 2 (overlay): the authoritative statusline figures — native
        ``used_percentage`` (WINS for self/context_level), context window/tokens,
        and the stdin-only ``self/cost`` / ``self/rate_limit`` / ``self/effort``.
        """
        d: dict[str, Any] = {}

        # --- Layer 1: JSONL (fallback engine, also the rich source under stdin).
        path = self.jsonl_path
        jsonl_ok = False
        if path and Path(path).exists():
            d = SessionSensor(path, self.window).parse()
            jsonl_ok = True
        if not jsonl_ok:
            # No transcript available: emit a stable, honest default schema so
            # subscribers always see the same self/* keys.
            d = {
                "self/context_level": 0.0,
                "self/context_tokens": 0,
                "self/context_window": self.window,
                "self/output_tokens": 0,
                "self/tool_calls": 0,
                "self/subagents/count": 0,
                "self/subagents/tokens": 0,
                "self/subagents/list": [],
                "self/registered": False,
                "self/registered/files": [],
                "self/compactions": 0,
                "self/reads_total": 0,
                "_tools": {},
                "_reads": [],
            }

        # --- Layer 2: statusline authoritative overlay.
        if self.stdin:
            ctx_win = self.stdin.get("context_window") or {}
            window = int(ctx_win.get("context_window_size") or self.window)
            d["self/context_window"] = window

            used_pct = ctx_win.get("used_percentage")
            tokens = _context_tokens(ctx_win)
            if used_pct is not None:
                # AUTHORITATIVE — the native percentage wins over any estimate.
                d["self/context_level"] = round(float(used_pct) / 100.0, 3)
                if tokens is None and window:
                    tokens = int(round(float(used_pct) / 100.0 * window))
            if tokens is not None:
                d["self/context_tokens"] = tokens
                if used_pct is None and window:
                    d["self/context_level"] = round(tokens / window, 3)

            # Cumulative output preferred from JSONL; fall back to the frame.
            cu = ctx_win.get("current_usage") or {}
            if not jsonl_ok and cu.get("output_tokens"):
                d["self/output_tokens"] = int(cu.get("output_tokens") or 0)

            # stdin-only namespaces (added on top of the prototype set).
            cost = self.stdin.get("cost") or {}
            if isinstance(cost, dict) and cost.get("total_cost_usd") is not None:
                d["self/cost"] = float(cost["total_cost_usd"])
            rl = _rate_limit(self.stdin.get("rate_limits"))
            if rl is not None:
                d["self/rate_limit"] = rl
            eff = _effort_level(self.stdin.get("effort"))
            if eff is not None:
                d["self/effort"] = eff

        # --- Layer 3: manifest-driven registration overlay ([CXC-IC-SELF]).
        # When a meta-object manifest is supplied, self/registered becomes
        # per-artifact: a checklist row per required artifact (role descriptor +
        # each dusk gift) with role/why and a read-this-session flag, and
        # self/registered flips true IFF every required artifact was read.  With
        # NO manifest the legacy REGISTRATION_MARKERS behavior above stands.
        if self.registration_manifest:
            reads = d.get("_reads") or []
            spec = load_registration_manifest(self.registration_manifest)
            checklist = []
            for rel in spec["required"]:
                info = spec["meta"].get(_norm_path(rel), {})
                read = any(_read_matches(r, rel) for r in reads)
                checklist.append({
                    "path": rel,
                    "role": info.get("role"),
                    "kind": info.get("kind"),
                    "why": info.get("why"),
                    "read": read,
                })
            d["self/registered/manifest"] = checklist
            d["self/registered"] = bool(checklist) and all(
                row["read"] for row in checklist)

        return d

    def to_bus_lines(self) -> list[tuple[str, object]]:
        """The (namespace, value) pairs this sensor would publish to the
        ComposeBus (mirrors the prototype: drop private ``_*`` keys and the
        bulky ``/list`` / ``/files`` detail records)."""
        d = self.parse()
        return [(k, v) for k, v in d.items() if not k.startswith("_")
                and not k.endswith("/list") and not k.endswith("/files")
                and not k.endswith("/manifest")]

    # -- HAL self-description ------------------------------------------------

    def describe(self) -> dict:
        """HAL-style self-description ([CXC-HALD-DRIVER] ``describe``): this is a
        SENSOR peripheral in the ``self/*`` namespace family."""
        return {
            "driver": "self",
            "category": "sensor",
            "tier": "vm",
            "namespaces": sorted(k for k, _ in self.to_bus_lines()),
            "summary": "Intelligence self-instrumentation (context/registration/"
                       "subagents/cost/rate-limit/effort) — statusline over JSONL",
        }
