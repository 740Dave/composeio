# Decision-support — the two R503 spec calls that are genuinely yours

> Consolidates the open design decisions (`DD-*`) surfaced by the c-pass rewrite of the fingerprint specs
> (`docs/c-pass/C70–C73`). Most of the ~17 `DD-*` nodes the rewrite raised were **auto-decided** — the
> architecture or a prior ruling of yours makes the answer clear and a wrong call is cheaply fixable. **Two**
> defer a *silent* problem if I pick wrong, so they come to you. Answer by filling the `Your call:` slot in the
> viewer (or on the channel). Recommendations are my honest lean, not a nudge — overrule freely.

## How to use this

Both decisions **⛔ block a finalizable acceptance test or a safe default**, so neither can be quietly deferred.
Neither blocks the *build* from starting (the device-logic and firmware structure don't depend on either answer),
so you can take your time — but the specs can't be called final until both are ruled. D1 in particular **wants a
short discussion, not just a slot** — the right answer depends on physical facts about your BSG setup I don't have.

---

## D1 · What state does a fingerprint-gated latch fall to when power is lost?

- **What this is** — In the game, a fingerprint match can drive a *physical* latch or relay — a hatch, a console
  lock, a "permission to fire" arm. When the device loses power, that latch falls to whatever state its wiring
  defaults to (energized-to-open vs energized-to-hold). This decision declares that default in the spec so it's
  never left implicit in the wiring.
- **Why it matters** — It's a silent safety/feel failure and it can't be inferred from software. A prop that
  silently *releases* on a power blip (a hatch pops, an "armed" light fires) reads as a bug at best and unsafe at
  worst; a fire-egress that silently *locks* is the opposite hazard. The one thing that must not happen is an
  *undeclared* default, because then it's decided by accident at install time.

| Option | Tradeoffs |
|---|---|
| **A — fail-secure default (latch holds closed / inert on power loss) + per-installation override** *(rec)* | Safe default for a game prop: a power blip triggers no action. The override still lets a specific install (an egress) invert it — but the default is the safe one. |
| B — fail-open default (latch releases on power loss) | Correct for a fire egress, wrong and alarming for an arm/console prop. Wrong default for most game props. |
| C — per-installation only, no global default | Most flexible, but if an installer forgets, the default is implicit — the exact silent failure this decision exists to prevent. |

- **My recommendation** — **A.** Fail-secure default + per-installation override. ⚠ *This one wants a discussion,
  not just a slot:* tell me what the latch physically gates in your setup and I'll make sure the spec's default
  matches the prop, rather than picking a default in the abstract.
- **Your call:** C, and we need to discuss.

---

## D2 · When a device's cleanup-delete fails, does it report "push me again" or "I'm broken"?

- **What this is** — When a template push fails, the device empties itself; that cleanup delete can *itself* fail.
  The device must then tell the fleet one of exactly two things: `fingerprint_push_needed` ("I'm unconfigured,
  re-push me") or `fingerprint_inoperable` ("I'm broken, a push won't help"). This decision maps each of the
  HAL's six failure axes to one of those two messages.
- **Why it matters** — It's a silent-UX trap. Call a *recoverable* error `inoperable` and a healthy device sits
  reporting "dead" while no one re-pushes it. Call an *unrecoverable* error `push_needed` and a broken device
  forever begs for a push that can't fix it. The mock acceptance test for this path can't be finalized until the
  map is ruled.

The rewrite proposes: **recoverable → `push_needed`:** transport, capacity, protocol_desync, unknown_code · **unrecoverable → `inoperable`:** sensor_refusal, hardware-absent/disconnected. The only genuinely arguable axis is `unknown_code`:

| Option | Tradeoffs |
|---|---|
| **A — the split above, with `unknown_code` → `push_needed`** *(rec)* | Retry-biased. `inoperable` demands a human physically touch the device; an *unknown transient* shouldn't require that. A persistent unknown failure surfaces as repeated `push_needed` — visible, not silent. |
| B — the split above, but `unknown_code` → `inoperable` | Conservative: if we don't understand the error, call the device broken. Safer against acting-on-garbage, but strands a device on a one-off transient we simply hadn't catalogued. |
| C — let's walk the full six-axis map together | If any axis beyond `unknown_code` feels mis-binned to you. |

- **My recommendation** — **A.** Bias `unknown_code` toward retryable, because the failure mode of guessing wrong
  is *visible* (a device repeatedly asking for a push) rather than *silent* (a healthy device parked as dead).
- **Your call:** A

---

## Summary at a glance

- **D1 — latch power-loss default** → my rec: **A, fail-secure + per-install override** *(wants a 2-line discussion about what the latch gates).*
- **D2 — failed-delete → which message** → my rec: **A, agent's split with `unknown_code` → `push_needed` (retry-biased).**

*Auto-decided and not brought to you (say the word if you want any surfaced): capability-binding → announce;
enroll device-addressed → yes; announcement shape → the 6 capability fields; template-ref → opaque
service-private, no hash; verification → one tier (tier-2 removed); match-locality → on the part; failure
raised-or-returned → raised; threshold-in-series → additive, off by default (per your prior). Cross-corpus and
tracked, not fixable from R503: the `experiential` rename (specs 26/32), the namespace spelling, and the
`fingerprint/match {id, score}` score-on-wire contradiction in spec 32.*
