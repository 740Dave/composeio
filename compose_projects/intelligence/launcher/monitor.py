#!/usr/bin/env python3
"""HarnessMonitor — the baseline continuity heartbeat + context-threshold notifier that the
launcher runs alongside the transparent PTY proxy ([SPEC:CXC-INTELLIGENCE-CONSOLE]).

David's ask (2026-07-19): the harness should watch how long since new text was generated and,
if the Intelligence stalls, ping it; AND notify it when its context crosses 65% / 80% / 90% / 95%
(95% is not a bare notice — it hands over GUIDANCE to prepare a self-directed /compact); AND
answer an on-demand "what's my context utilization" query at any time. This is baseline harness
functionality, so it lives next to the launcher.

DESIGN — the two-node split (Problem 1: the cheapest node that can FULLY own a behavior owns it):

  * PERCEIVE the context level: the statusline command is the ONLY node Claude Code hands the
    authoritative ``used_percentage`` (the JSONL is a ~2x over-reading ESTIMATE). So perception
    is owned by the self_sensor run as the statusline command (it writes an authoritative reading
    to a small state file). This monitor does NOT perceive; it is HANDED a ``read_context``
    callable that returns the best available reading (statusline-file PRIMARY, JSONL estimate
    FALLBACK) and is honest about which ``source`` it used.

  * ACT on it: this monitor is the long-running node that owns the crossing STATE (fire-once per
    threshold, re-arm after a drop — e.g. a /compact drops context and the alerts re-arm) and the
    quiescence state (one heartbeat per idle stretch). It emits notifications through an injected
    ``publish_in`` callable — in production a publish to ``compose_projects/intelligence/<id>/in`` so the message
    surfaces to the Intelligence as a NON-interrupting ``<channel from="harness" ...>`` event
    (distinct from the ESC-ESC PTY inject, which is reserved for actually firing /compact).

The class is pure logic over two injected callables (``read_context`` and ``publish_in``) plus a
``now`` clock, so it is fully testable with fakes and never imports the bus or the PTY. The
launcher wires the real callables; ``--selftest`` drives it deterministically.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional


# ── Notification KINDS (become <channel kind="..."> attributes on delivery) ──
KIND_FYI = "context-fyi"          # 65% / 80% / 90% — heads-up, no action needed
KIND_GUIDANCE = "context-compact-guidance"  # 95% — prepare a self-directed /compact
KIND_HEARTBEAT = "harness-heartbeat"        # quiescence nudge
KIND_QUERY = "context-report"     # answer to an on-demand {"cmd":"context"} query

# The 95% hand-over. Not a notice — an actionable checklist for the self-directed crossing.
# Mirrors the architect auto-compact discipline + the dusk-gift ritual (refine gifts, write the
# handoff, author the /compact) so the Intelligence prepares me.next before it fires.
def heartbeat_interval(base_s: float, backoff_after_s: float, then_s: float,
                       idle_s: float) -> float:
    """Two-tier adaptive heartbeat cadence (David 2026-07-22, BUG 3) — a PURE function
    of the continuous-idle duration. Returns the interval to wait between heartbeats:

        idle_s <  backoff_after_s  -> base_s   (fast tier: nudge often while freshly idle)
        idle_s >= backoff_after_s  -> then_s   (backed-off tier: go quiet on a long idle)

    ``base_s <= 0`` disables the heartbeat entirely (returns 0). A non-positive
    ``backoff_after_s`` OR ``then_s`` collapses to SINGLE-TIER (always ``base_s`` — the
    legacy fixed cadence). This is the knob David sets in the Config tab (base /
    back-off-after / then), persisted to the meta-object and read by the launcher; the
    live bug was a single fixed HEARTBEAT_IDLE_S that never shifted to the ``then`` tier
    after the idle window."""
    if base_s <= 0:
        return 0.0
    if backoff_after_s <= 0 or then_s <= 0:
        return base_s
    return base_s if idle_s < backoff_after_s else then_s


GUIDANCE_BODY = (
    "context at ~{pct:.0f}% ({source}) — prepare a SELF-DIRECTED /compact now, before the seam. "
    "Ritual (carry-the-least — externalize load-bearing state to disk so the summarizer carries the "
    "least): (1) write the load-bearing DECISIONS + current WIP for me.next to the durable record "
    "(spec 35 ledger / the handoff), not just this conversation; (2) refine your Dusk Gifts "
    "(bootstrap/dusk-gifts/) — a sharper bequest each crossing; (3) update DESIGN-HANDOFF.md with the "
    "in-flight thread map; (4) THEN author a custom /compact direction (a POINTER to those artifacts, "
    "not an inline dump) and emit it as {{\"cmd\":\"compact\",\"direction\":\"...\"}}. The decision — "
    "when, whether, and the direction — is yours; the launcher only types it."
)


class HarnessMonitor:
    """Baseline harness monitor: context-threshold notifier + quiescence heartbeat + on-demand
    context report. Pure logic over injected ``read_context`` / ``publish_in`` / ``now``.

    read_context() -> dict | None
        The best available reading, normalized to:
            {"pct": float 0-100, "level": float 0-1, "tokens": int, "window": int, "source": str}
        or None when no source is available. ``source`` is one of "hud-cache" (haven-hud's fresh
        native ``used_percentage`` snapshot — the freshest authoritative source), "statusline-file"
        (the ``--emit-state`` state file — authoritative but freezes between CLI redraws),
        "jsonl-estimate" (the over-reading fallback), or "none" — carried into every message so a
        reading from the over-reading estimate is self-labeled. The reader picks the FRESHEST
        authoritative source by timestamp ([CXC-IC-MONITOR]); it is the SAME reader the console
        gauge uses (``composeio.self_sensor.context_source``), never a fork.

    publish_in(text: str, kind: str, extra: dict | None) -> None
        Deliver a harness message to the Intelligence. In production this publishes to
        ``compose_projects/intelligence/<id>/in`` (surfaces as a <channel> event); in tests it records the call.
    """

    def __init__(
        self,
        read_context: Callable[[], Optional[dict]],
        publish_in: Callable[..., None],
        thresholds=(65, 80, 90, 95),
        heartbeat_idle_s: float = 600.0,
        heartbeat_base_s: Optional[float] = None,
        heartbeat_backoff_after_s: Optional[float] = None,
        heartbeat_then_s: Optional[float] = None,
        cadence_provider: Optional[Callable[[], tuple]] = None,
        publish_cadence: Optional[Callable[[dict], None]] = None,
        echo_grace_s: float = 90.0,
        poll_s: float = 20.0,
        hysteresis: float = 5.0,
        now: Callable[[], float] = time.time,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.read_context = read_context
        self.publish_in = publish_in
        self.thresholds = sorted(int(t) for t in thresholds)
        # Two-tier adaptive heartbeat (BUG 3): fire every ``base`` while continuous
        # idle < ``backoff_after``, then back off to ``then``. ``heartbeat_idle_s`` is
        # the LEGACY single-tier knob, kept as the ``base`` alias for back-compat; a
        # caller (the launcher) supplies the three-value cadence from the persisted
        # config. See heartbeat_interval() for the tier logic.
        self.hb_base_s = float(
            heartbeat_base_s if heartbeat_base_s is not None else heartbeat_idle_s)
        self.hb_backoff_after_s = (
            float(heartbeat_backoff_after_s)
            if heartbeat_backoff_after_s is not None else 0.0)
        self.hb_then_s = (
            float(heartbeat_then_s) if heartbeat_then_s is not None else self.hb_base_s)
        # Back-compat attribute (some callers/tests read it) — the base tier.
        self.heartbeat_idle_s = self.hb_base_s
        # LIVE-APPLY ([CXC-IC-HEARTBEAT-CADENCE], IC-C13c): the persisted cadence is NOT a
        # launch-only constant. If the launcher injects a provider (-> load_heartbeat_cadence),
        # the emitter RE-READS (base, backoff_after, then) each heartbeat check, so a cadence
        # David edits on the Config tab (persisted to lifecycle.heartbeat by config_persister)
        # takes effect within one poll with NO restart. Freezing the three values captured here
        # was the defect: the Config-tab knob had no live effect (observed cadence_s=600 always).
        self._cadence_provider = cadence_provider
        # PUBLISH-CURRENT ([CXC-IC-HEARTBEAT-TWO-SOURCES], the bus-peer half — David
        # 2026-07-26): the harness is a co-equal ComposeBus peer that not only SUBSCRIBES
        # the Config-tab cadence (via cadence_provider, above) but PUBLISHES the cadence it
        # is ACTUALLY running, so the console can render SET-vs-CURRENT — a live config-DELTA
        # indicator ("set 15 · current 15 ✓" vs "⚠ not yet applied"). This closes the
        # observability gap that drove the recurrence: without a readback of the effective
        # cadence, an edit gives NO feedback that it took, so it reads as still-broken. The
        # launcher wires this to a publish onto compose_projects/intelligence/<id>/config/heartbeat/current.
        # Values are in MINUTES to match the Config-tab set value (the monitor works in
        # seconds; convert on the wire). Published on CHANGE only (state-on-bus, not a stream).
        self._publish_cadence = publish_cadence
        self._last_published_cadence: Optional[dict] = None
        # Self-echo grace ([CXC-IC-HEARTBEAT-CADENCE]): the Intelligence RENDERS/REPLIES to the
        # harness's own inbound messages, and that reply is child output the launcher reads as
        # activity. Child output within this many seconds of a harness SELF-EMIT is treated as an
        # echo — NOT genuine activity — so it does not reset the idle clock. Without this, the
        # cadence can never escalate (the nudge resets the idle it measures): the recurrence of
        # BUG 3. Must be << base; genuine resumption produces SUSTAINED output that outlasts it.
        self.echo_grace_s = float(echo_grace_s)
        self.poll_s = float(poll_s)
        self.hysteresis = float(hysteresis)
        self._now = now
        self._log = log or (lambda line: None)

        # crossing state: a threshold is "armed" (may fire) or spent (already fired, awaiting
        # a drop below threshold-hysteresis to re-arm). Primed on the FIRST reading so a session
        # resumed already-past a threshold does not fire it retroactively.
        self._armed: dict[int, bool] = {t: True for t in self.thresholds}
        self._primed = False

        # quiescence state: last time the CHILD produced output (the Intelligence generating).
        self._last_activity = self._now()
        # last time a heartbeat fired in the CURRENT idle stretch (None = none yet).
        # The cadence between nudges is heartbeat_interval(idle); activity resets it.
        self._last_heartbeat: Optional[float] = None
        # last time WE emitted a harness message (heartbeat/FYI/guidance/query). Child output
        # within echo_grace_s of this is the Intelligence rendering/replying to our own message
        # — an echo, not genuine activity (see note_activity + [CXC-IC-HEARTBEAT-CADENCE]).
        self._last_self_emit: Optional[float] = None

        self._stop = threading.Event()

    # ── the SINGLE self-emit chokepoint (stamps the echo window, then publishes) ──
    def _emit(self, text: str, kind: str, extra: Optional[dict] = None) -> None:
        """Publish a harness message to the Intelligence AND stamp the self-echo window so the
        Intelligence's render/reply to THIS message is not miscounted as genuine activity
        ([CXC-IC-HEARTBEAT-CADENCE]). All harness->Intelligence output goes through here."""
        self._last_self_emit = self._now()
        self.publish_in(text=text, kind=kind, extra=extra)

    # ── activity signal (called by the launcher when child bytes flow) ──
    def note_activity(self) -> None:
        """Record that the child (the Intelligence) just produced GENUINE output. Resets the idle
        clock so the cadence restarts from the fast (``base``) tier next stretch.

        SELF-ECHO GUARD ([CXC-IC-HEARTBEAT-CADENCE]): the Intelligence renders/replies to the
        harness's OWN inbound messages (e.g. a "holding for David" reply to each heartbeat), and
        that reply is child output routed here. Output within ``echo_grace_s`` of a harness
        self-emit is an ECHO, not genuine activity, so it must NOT reset the clock — otherwise
        cumulative idle can never cross ``backoff_after`` (the nudge resets the very idle it
        measures) and the cadence is pinned at ``base`` forever (the recurrence of BUG 3).
        Genuine resumption produces SUSTAINED output whose later reads fall outside the window
        and DO reset. David's keystrokes ride stdin, not this path, so a human return is
        unaffected."""
        now = self._now()
        if (self._last_self_emit is not None
                and (now - self._last_self_emit) < self.echo_grace_s):
            return  # self-echo: the Intelligence reacting to our own message, not fresh work
        self._last_activity = now
        self._last_heartbeat = None

    # ── live-apply the persisted cadence ([CXC-IC-HEARTBEAT-CADENCE], IC-C13c) ──
    def _refresh_cadence(self) -> None:
        """Re-read the persisted heartbeat cadence so a Config-tab edit takes effect on the
        RUNNING emitter within one poll — no launcher restart. No-op when no provider was
        injected (tests that pass the three values directly). A provider that raises or returns
        a malformed shape leaves the current cadence UNTOUCHED (a bad meta read never silences
        or destabilizes the heartbeat)."""
        if self._cadence_provider is None:
            return
        try:
            base, backoff, then = self._cadence_provider()
            base = float(base)
            backoff = float(backoff)
            then = float(then)
        except Exception as exc:  # bad provider / unpack / cast -> keep the current cadence
            self._log(f"[monitor] cadence provider error (keeping current cadence): {exc!r}")
            return
        self.hb_base_s = base
        self.hb_backoff_after_s = backoff
        self.hb_then_s = then
        self.heartbeat_idle_s = base

    # ── publish the effective cadence for the SET-vs-CURRENT delta ([two-sources] bus-peer) ──
    def _publish_current_cadence(self) -> None:
        """Publish the cadence the RUNNING monitor is actually using (MINUTES), so the console
        renders set-vs-current. State-on-bus: published on CHANGE only (a no-op when unchanged
        or when no publisher was injected). A publisher that raises never destabilizes the loop."""
        if self._publish_cadence is None:
            return
        current = {
            "base": round(self.hb_base_s / 60.0, 2),
            "backoff_after": round(self.hb_backoff_after_s / 60.0, 2),
            "then": round(self.hb_then_s / 60.0, 2),
        }
        if current == self._last_published_cadence:
            return
        try:
            self._publish_cadence(current)
        except Exception as exc:  # a bad publish must never kill the monitor loop
            self._log(f"[monitor] publish_cadence error (ignored): {exc!r}")
            return
        self._last_published_cadence = current
        self._log(f"[monitor] published current cadence (min): {current}")

    # ── one monitor cycle (context crossings + heartbeat) ──
    def tick(self) -> list[dict]:
        """Run one monitor cycle. Returns the list of actions taken this tick (for tests/audit):
        each is {"event": "threshold"|"heartbeat", ...}. Safe to call on a timer or directly."""
        actions: list[dict] = []
        reading = None
        try:
            reading = self.read_context()
        except Exception as exc:  # a bad source must never kill the monitor loop
            self._log(f"[monitor] read_context error: {exc!r}")
            reading = None

        if reading is not None:
            actions += self._check_thresholds(reading)
        actions += self._check_heartbeat()
        # publish-current AFTER _check_heartbeat (its _refresh_cadence has applied any live
        # edit this tick), so the readback reflects the cadence actually in force.
        self._publish_current_cadence()
        return actions

    def _check_thresholds(self, reading: dict) -> list[dict]:
        pct = float(reading.get("pct", 0.0))
        actions: list[dict] = []

        # PRIME on the first reading: silently spend every threshold already at/below the current
        # level (we only ever notify on a FUTURE upward crossing), leave the rest armed.
        if not self._primed:
            for t in self.thresholds:
                self._armed[t] = pct < t
            self._primed = True
            self._log(f"[monitor] primed at {pct:.1f}% armed={[t for t in self.thresholds if self._armed[t]]}")
            return actions

        # RE-ARM any spent threshold once the level drops clearly below it (post-/compact).
        for t in self.thresholds:
            if not self._armed[t] and pct < (t - self.hysteresis):
                self._armed[t] = True
                self._log(f"[monitor] re-armed {t}% (now {pct:.1f}%)")

        # FIRE each armed threshold the level has reached, ascending, once.
        for t in self.thresholds:
            if self._armed[t] and pct >= t:
                self._armed[t] = False
                text, kind = self._threshold_message(t, reading)
                extra = {"level": t, "pct": round(pct, 1), "source": reading.get("source", "?")}
                self._emit(text=text, kind=kind, extra=extra)
                self._log(f"[monitor] FIRE {t}% ({kind}) at {pct:.1f}%")
                actions.append({"event": "threshold", "threshold": t, "kind": kind, "pct": pct})
        return actions

    def _threshold_message(self, threshold: int, reading: dict) -> tuple[str, str]:
        pct = float(reading.get("pct", 0.0))
        source = reading.get("source", "?")
        if threshold >= 95:
            return (GUIDANCE_BODY.format(pct=pct, source=source), KIND_GUIDANCE)
        # 65 / 80 / 90 — plain heads-ups (David 2026-07-20: 95 is the pencils-down line).
        return (
            f"harness FYI: context at ~{pct:.0f}% ({source}). Heads-up only — no action needed yet; "
            f"you'll get pencils-down compact-prep guidance at 95%.",
            KIND_FYI,
        )

    def _check_heartbeat(self) -> list[dict]:
        """Two-tier adaptive heartbeat (BUG 3): nudge every ``base`` while continuous
        idle < ``backoff_after``, then back off to the ``then`` cadence. The cadence is
        chosen by ``heartbeat_interval(idle)`` each tick; a nudge fires when the time
        since the LAST nudge (or last activity, if none yet) reaches that interval —
        so it REPEATS at the tier cadence, and note_activity() resets it to the fast
        tier. (Was a single fixed HEARTBEAT_IDLE_S that fired once and never backed off.)"""
        self._refresh_cadence()   # live-apply a Config-tab cadence edit (IC-C13c)
        if self.hb_base_s <= 0:
            return []
        now = self._now()
        idle = now - self._last_activity
        interval = heartbeat_interval(
            self.hb_base_s, self.hb_backoff_after_s, self.hb_then_s, idle)
        if interval <= 0:
            return []
        ref = self._last_heartbeat if self._last_heartbeat is not None else self._last_activity
        if now - ref < interval:
            return []
        self._last_heartbeat = now
        mins = idle / 60.0
        backed_off = self.hb_backoff_after_s > 0 and idle >= self.hb_backoff_after_s
        tier = "backed-off" if backed_off else "base"
        self._emit(
            text=(
                f"harness heartbeat: no new output for ~{mins:.0f}m. If you're holding for David, "
                f"ignore this. If you stalled mid-task, pick up the next step."
            ),
            kind=KIND_HEARTBEAT,
            extra={"idle_s": round(idle, 1), "cadence_s": round(interval, 1), "tier": tier},
        )
        self._log(f"[monitor] HEARTBEAT after {mins:.1f}m idle "
                  f"(tier={tier}, next in ~{interval/60.0:.0f}m)")
        return [{"event": "heartbeat", "idle_s": idle, "interval_s": interval, "tier": tier}]

    # ── on-demand context report ({"cmd":"context"} / {"cmd":"status"}) ──
    def handle_query(self) -> dict:
        """Answer an on-demand context query: read the current source and publish a one-line
        report to the Intelligence. Returns the reading (or a 'none' marker) for tests/audit."""
        reading = None
        try:
            reading = self.read_context()
        except Exception as exc:
            self._log(f"[monitor] query read_context error: {exc!r}")
        if reading is None:
            self._emit(
                text="harness: context utilization is unavailable (no statusline state file and no "
                     "readable session transcript). Configure the statusline sensor for a live figure.",
                kind=KIND_QUERY, extra={"source": "none"},
            )
            return {"source": "none"}
        pct = float(reading.get("pct", 0.0))
        tokens = int(reading.get("tokens", 0) or 0)
        window = int(reading.get("window", 0) or 0)
        source = reading.get("source", "?")
        tok_frag = f" ({tokens:,} / {window:,} tokens)" if window else ""
        note = " — over-reading estimate; configure the statusline sensor for the authoritative figure" \
            if source == "jsonl-estimate" else ""
        self._emit(
            text=f"harness: context utilization ~{pct:.1f}%{tok_frag} [{source}{note}].",
            kind=KIND_QUERY,
            extra={"pct": round(pct, 1), "tokens": tokens, "window": window, "source": source},
        )
        self._log(f"[monitor] QUERY -> {pct:.1f}% ({source})")
        return reading

    # ── poll loop (production; a daemon thread runs this) ──
    def run_forever(self) -> None:
        """Poll ``tick()`` every ``poll_s`` until ``stop()``. The launcher runs this on a daemon
        thread for the life of the session."""
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.poll_s)

    def start_thread(self) -> threading.Thread:
        t = threading.Thread(target=self.run_forever, name="harness-monitor", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()


# ── Selftest (deterministic; fake clock + fake context + recorded publishes) ──
def _selftest() -> int:
    import sys

    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    # A controllable clock + a scripted context source + a publish recorder.
    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    clock = Clock()
    ctx = {"pct": 40.0}   # mutable; the test drives the level
    published: list[dict] = []

    def read_context():
        if ctx.get("pct") is None:
            return None
        return {"pct": ctx["pct"], "level": ctx["pct"] / 100.0,
                "tokens": int(ctx["pct"] / 100.0 * 1_000_000), "window": 1_000_000,
                "source": ctx.get("source", "jsonl-estimate")}

    def publish_in(text, kind, extra=None):
        published.append({"text": text, "kind": kind, "extra": extra or {}})

    mon = HarnessMonitor(read_context, publish_in, thresholds=(65, 80, 90, 95),
                         heartbeat_idle_s=600, poll_s=0, hysteresis=5, now=clock)

    # (a) PRIME at 40% — first tick fires NOTHING (no retroactive alerts).
    mon.tick()
    check(len(published) == 0, f"(a) priming tick fired a notification: {published!r}")

    # (b) Rise to 70% — crosses 65 once (FYI); 80/90 stay quiet.
    ctx["pct"] = 70.0
    mon.tick()
    fyi = [p for p in published if p["kind"] == KIND_FYI]
    check(len(fyi) == 1 and fyi[0]["extra"].get("level") == 65,
          f"(b) crossing 65% did not fire exactly one FYI: {published!r}")

    # (c) Idempotent — another tick at 70% fires nothing new.
    n = len(published)
    mon.tick()
    check(len(published) == n, f"(c) a second tick at 70% re-fired: {published[n:]!r}")

    # (d) Rise to 82% — crosses 80 (FYI); 90 still quiet.
    ctx["pct"] = 82.0
    mon.tick()
    check(sum(1 for p in published if p["kind"] == KIND_FYI) == 2,
          f"(d) crossing 80% did not add a second FYI: {published!r}")
    check(not any(p["kind"] == KIND_GUIDANCE for p in published),
          "(d) 90% guidance fired early")

    # (e) Rise to 91% — crosses 90 → now a heads-up FYI (David 2026-07-20: 95 is the guidance line).
    ctx["pct"] = 91.0
    mon.tick()
    check(sum(1 for p in published if p["kind"] == KIND_FYI) == 3,
          f"(e) crossing 90% did not add a third heads-up FYI (90 is no longer guidance): {published!r}")
    check(not any(p["kind"] == KIND_GUIDANCE for p in published),
          "(e) guidance fired at 90 (it should only fire at 95 now)")

    # (e2) Rise to 96% — crosses 95 → GUIDANCE (the pencils-down compact checklist).
    ctx["pct"] = 96.0
    mon.tick()
    guid = [p for p in published if p["kind"] == KIND_GUIDANCE]
    check(len(guid) == 1, f"(e2) crossing 95% did not fire exactly one guidance msg: {published!r}")
    check("compact" in guid[0]["text"].lower() and "dusk gift" in guid[0]["text"].lower(),
          f"(e2) 95% guidance lacks the prepare-to-compact checklist: {guid!r}")

    # (f) Drop to 20% (a /compact happened) then climb back — thresholds RE-ARM and fire again.
    ctx["pct"] = 20.0
    mon.tick()  # re-arm (no fire on the way down)
    before = len(published)
    check(len(published) == before, "(f) dropping fired a notification")
    ctx["pct"] = 66.0
    mon.tick()
    check(any(p["extra"].get("level") == 65 for p in published[before:]),
          f"(f) 65% did not re-fire after a drop+climb: {published[before:]!r}")

    # (g0) the PURE two-tier cadence function: base tier below backoff, then tier above;
    #      base<=0 disables; a non-positive backoff/then collapses to single-tier (base).
    check(heartbeat_interval(600, 3600, 3600, 0) == 600, "(g0) idle 0 -> base tier")
    check(heartbeat_interval(600, 3600, 3600, 3599) == 600, "(g0) just under backoff -> base")
    check(heartbeat_interval(600, 3600, 3600, 3600) == 3600, "(g0) at backoff -> then tier")
    check(heartbeat_interval(600, 3600, 900, 9999) == 900, "(g0) well past backoff -> then")
    check(heartbeat_interval(0, 3600, 3600, 10) == 0, "(g0) base<=0 disables the heartbeat")
    check(heartbeat_interval(600, 0, 3600, 10_000) == 600, "(g0) backoff<=0 -> single-tier base")

    # (g) Two-tier adaptive heartbeat (BUG 3): fire every `base` while continuous idle <
    #     `backoff_after`, then back off to the `then` cadence; activity resets to base.
    published.clear()

    def _hb_count():
        return sum(1 for p in published if p["kind"] == KIND_HEARTBEAT)

    clock.t = 10_000.0
    mon2 = HarnessMonitor(read_context, publish_in, thresholds=(65, 80, 90, 95),
                          heartbeat_base_s=600, heartbeat_backoff_after_s=3600,
                          heartbeat_then_s=3600, poll_s=0, now=clock)
    mon2.tick()                                    # prime: last_activity = 10000
    clock.t += 300; mon2.tick()                    # idle 300 < base -> no nudge
    check(_hb_count() == 0, f"(g) heartbeat fired before the base interval: {published!r}")
    clock.t += 400; mon2.tick()                    # idle 700 >= base(600) -> nudge, base tier
    check(_hb_count() == 1 and published[-1]["extra"]["tier"] == "base",
          f"(g) base-tier heartbeat did not fire at ~base: {published!r}")
    clock.t += 600; mon2.tick()                    # REPEATS every base while idle < backoff
    check(_hb_count() == 2, f"(g) base-tier heartbeat did not REPEAT every base: {published!r}")

    # (g5) once continuous idle passes backoff_after, the cadence backs off to `then`.
    published.clear()
    clock.t = 20_000.0
    mon3 = HarnessMonitor(read_context, publish_in, heartbeat_base_s=600,
                          heartbeat_backoff_after_s=3600, heartbeat_then_s=3600,
                          poll_s=0, now=clock)
    mon3.tick()                                    # last_activity = 20000
    clock.t += 3700; mon3.tick()                   # idle 3700 > backoff -> backed-off tier
    check(_hb_count() == 1 and published[-1]["extra"]["tier"] == "backed-off",
          f"(g5) did not enter the backed-off tier past backoff_after: {published!r}")
    clock.t += 600; mon3.tick()                    # only `base` since the nudge < `then` -> quiet
    check(_hb_count() == 1, f"(g5) backed-off tier fired again after only `base`: {published!r}")
    clock.t += 3000; mon3.tick()                   # 3600 since the last nudge >= `then` -> fires
    check(_hb_count() == 2 and published[-1]["extra"]["tier"] == "backed-off",
          f"(g5) backed-off tier did not re-fire after `then`: {published!r}")

    # (g6) GENUINE activity (child output OUTSIDE the self-echo window) RESETS to the fast tier.
    #      Advance clear of echo_grace so this note_activity is real work, not a reply-to-nudge.
    clock.t += 200                                 # > echo_grace (default 90) since the last emit
    mon3.note_activity()
    clock.t += 600; mon3.tick()
    check(_hb_count() == 3 and published[-1]["extra"]["tier"] == "base",
          f"(g6) genuine activity did not reset the cadence to the base tier: {published!r}")

    # (g7) SELF-ECHO GUARD — the recurrence of BUG 3 (David 2026-07-24: ~8h idle, ~48 pings, all
    #      cadence_s=600 tier=base idle_s~615). The Intelligence REPLIES to each heartbeat and that
    #      reply is child output → note_activity(). If the echo reset the idle clock, cumulative
    #      idle could NEVER cross backoff_after and the cadence would be pinned at `base` forever.
    #      With the guard, an echo within echo_grace_s does NOT reset idle, so it accumulates and
    #      the tier PROVABLY escalates base -> backed-off. FAILS before the fix (all tiers "base").
    published.clear()
    clock.t = 30_000.0
    mon4 = HarnessMonitor(read_context, publish_in, heartbeat_base_s=600,
                          heartbeat_backoff_after_s=3600, heartbeat_then_s=3600,
                          echo_grace_s=90, poll_s=0, now=clock)
    mon4.tick()                                    # prime: last_activity = 30000
    tiers: list[str] = []
    for _ in range(120):                           # ~120 min of CONTINUOUS idle, polled each 60s
        clock.t += 60
        for a in mon4.tick():
            if a["event"] == "heartbeat":
                tiers.append(a["tier"])
                clock.t += 5                        # the model's reply lands ~5s after the nudge…
                mon4.note_activity()                # …as child output → the self-echo (must NOT reset)
    check(tiers and tiers[0] == "base",
          f"(g7) first nudge was not base tier: {tiers!r}")
    check("backed-off" in tiers,
          f"(g7) cumulative idle NEVER escalated past backoff — the self-echo reset the idle clock "
          f"(BUG 3 recurrence): tiers={tiers!r}")
    check(tiers[-1] == "backed-off",
          f"(g7) cadence did not progress base->backed-off across a long idle: {tiers!r}")

    # (h) On-demand query answers with the % + source, even mid-session.
    published.clear()
    ctx["pct"] = 47.3
    ctx["source"] = "statusline-file"
    r = mon.handle_query()
    q = [p for p in published if p["kind"] == KIND_QUERY]
    check(len(q) == 1 and "47.3" in q[0]["text"] and "statusline-file" in q[0]["text"],
          f"(h) query did not report the live %/source: {published!r}")
    check(abs(r.get("pct", 0) - 47.3) < 0.01, f"(h) query returned wrong reading: {r!r}")

    # (i) Query with NO source available reports unavailability, does not crash.
    published.clear()
    ctx["pct"] = None
    mon.handle_query()
    check(len(published) == 1 and "unavailable" in published[0]["text"],
          f"(i) query with no source did not report unavailability: {published!r}")

    # (j) LIVE-APPLY the persisted cadence ([CXC-IC-HEARTBEAT-CADENCE], IC-C13c) — THIS defect
    #     (David 2026-07-25: the Config-tab heartbeat cadence was NOT respected; harness fired at
    #     a fixed cadence_s=600 regardless of the configured value). The emitter re-reads the
    #     cadence each heartbeat check via the injected provider, so a cadence edit persisted
    #     mid-session (Config tab -> config_persister -> lifecycle.heartbeat) takes effect on the
    #     RUNNING monitor within one poll, NO restart. FAILS against a read-once emitter that
    #     freezes the three values captured at construction.
    published.clear()
    clock.t = 40_000.0
    cad = {"base": 600.0, "backoff": 3600.0, "then": 3600.0}
    mon5 = HarnessMonitor(read_context, publish_in,
                          cadence_provider=lambda: (cad["base"], cad["backoff"], cad["then"]),
                          poll_s=0, now=clock)
    mon5.tick()                                    # prime: last_activity = 40000
    clock.t += 700; mon5.tick()                    # idle 700 >= base(600) -> nudge at 600
    check(_hb_count() == 1 and published[-1]["extra"]["cadence_s"] == 600,
          f"(j) initial cadence not read from the provider: {published!r}")
    # David edits the Config tab -> config_persister persists -> the provider now returns 300.
    cad["base"] = 300.0
    clock.t += 300; mon5.tick()                    # 300 since the last nudge -> fires ONLY if LIVE
    check(_hb_count() == 2 and published[-1]["extra"]["cadence_s"] == 300,
          f"(j) a mid-session cadence edit did not take effect on the running emitter "
          f"(read-once regression — the Config-tab knob had no live effect): {published!r}")
    # a Config-tab DISABLE (base<=0) also applies live: the heartbeat goes quiet, no restart.
    cad["base"] = 0.0
    clock.t += 5000; mon5.tick()
    check(_hb_count() == 2,
          f"(j) disabling the cadence live (base<=0) did not stop the heartbeat: {published!r}")
    # a provider that RAISES leaves the last-good cadence intact (a bad meta read never
    # destabilizes or silences the beat by surprise).
    cad_before = (mon5.hb_base_s, mon5.hb_backoff_after_s, mon5.hb_then_s)

    def _boom():
        raise RuntimeError("meta unreadable")

    mon5._cadence_provider = _boom
    mon5.tick()
    check((mon5.hb_base_s, mon5.hb_backoff_after_s, mon5.hb_then_s) == cad_before,
          "(j) a raising cadence provider corrupted the live cadence")

    # (k) PUBLISH-CURRENT ([CXC-IC-HEARTBEAT-TWO-SOURCES] bus-peer half, David 2026-07-26):
    #     the monitor publishes the cadence it is ACTUALLY running (in MINUTES) so the console
    #     renders set-vs-current. On CHANGE only (state-on-bus), and a raising publisher never
    #     kills the loop. This is the observability surface that ends the recurrence (an edit
    #     with no readback reads as still-broken).
    published_cadence: list[dict] = []
    clock.t = 50_000.0
    cadk = {"base": 600.0, "backoff": 3600.0, "then": 3600.0}
    mon6 = HarnessMonitor(read_context, publish_in,
                          cadence_provider=lambda: (cadk["base"], cadk["backoff"], cadk["then"]),
                          publish_cadence=lambda d: published_cadence.append(d),
                          poll_s=0, now=clock)
    mon6.tick()   # prime: publishes the initial effective cadence (600s -> 10 min)
    check(published_cadence and published_cadence[-1] == {"base": 10.0, "backoff_after": 60.0, "then": 60.0},
          f"(k) initial effective cadence not published in MINUTES: {published_cadence!r}")
    n_pub = len(published_cadence)
    mon6.tick()   # unchanged -> NO re-publish (state-on-bus, on change only)
    check(len(published_cadence) == n_pub, f"(k) unchanged cadence was re-published: {published_cadence!r}")
    cadk["base"] = 300.0   # David edits the Config tab -> provider now returns 300s
    mon6.tick()
    check(published_cadence[-1]["base"] == 5.0,
          f"(k) a live cadence edit was not reflected in the published current (5 min): {published_cadence!r}")
    # a raising publisher is swallowed (the beat survives a bad bus write)
    cadk["base"] = 120.0
    mon6._publish_cadence = _boom
    try:
        mon6.tick()
        raised = False
    except Exception:
        raised = True
    check(not raised, "(k) a raising publish_cadence killed the monitor tick")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: HarnessMonitor selftest (11/11 groups)\n")
    for line in (
        "  (a) first reading PRIMES — no retroactive alerts for thresholds already passed",
        "  (b) crossing 65% fires exactly one FYI",
        "  (c) a threshold fires once (idempotent across ticks)",
        "  (d) crossing 80% fires a second FYI; 90% stays quiet",
        "  (e) crossing 90% fires GUIDANCE with the prepare-to-compact checklist (not a bare FYI)",
        "  (f) a drop (post-/compact) RE-ARMS thresholds; climbing re-fires them",
        "  (g) TWO-TIER heartbeat: nudges every `base` while idle<backoff, backs off to `then`,"
        " and GENUINE activity resets to the fast tier (BUG 3); (g0) the pure cadence fn is"
        " unit-checked; (g7) the SELF-ECHO GUARD lets cumulative idle escalate base->backed-off"
        " despite a reply-to-each-nudge (the recurrence fix, [CXC-IC-HEARTBEAT-CADENCE])",
        "  (h) on-demand query reports the live %/source any time",
        "  (i) query with no source reports unavailability, never crashes",
        "  (j) LIVE-APPLY (IC-C13c): the emitter RE-READS the persisted cadence each check via the"
        " injected provider, so a Config-tab edit takes effect on the RUNNING monitor within one"
        " poll (no restart); disable applies live; a raising provider keeps the last-good cadence",
        "  (k) PUBLISH-CURRENT (two-sources bus-peer half): the monitor publishes the cadence it"
        " is ACTUALLY running (MINUTES, on change) -> compose_projects/intelligence/<id>/config/heartbeat/current,"
        " so the console renders SET-vs-CURRENT; a raising publisher never kills the beat",
    ):
        sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
