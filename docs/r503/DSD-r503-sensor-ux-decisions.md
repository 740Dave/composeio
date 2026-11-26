<!-- dsd-status: answered 2026-08-21T01:29:02Z -->
# DSD — R503 Sensor-UX: closing the six audit findings

> This turns the independent audit of the r503-sensor-ux design spec into a fast decision session.
> The audit (`DSD-r503-sensor-ux-audit.md`, retained as posterity) compared the LOCKED feedback-table
> spec against your own words in the design transcript and found **0 MISSING, 0 ALTERED, 2 INVENTED,
> 4 AMBIGUOUS** — six places where the spec presents an assistant inference or an unanswered question
> as settled fact. Each finding is a decision below, carrying my honest lean pre-selected.
> Answer by picking the radio (or editing the `Your call:` slot), or just say "recommendations
> accepted." Sources cited per decision by transcript line.

## How to use this

The recommendations are my real engineering lean, not a nudge — overrule any of them freely and I
encode your call, not mine. **D1, D2, and D3 ⛔ block** authoring the `.lts` spec and rebuilding the
driver's `command_feedback()` table, so they matter most; D4–D6 can be decided in any order. D3 is
the one that isn't really an A/B — it wants a **short hardware re-check**, not a guess (flagged inline).

---

## Theme A — Colors the spec inferred but you never said (the silent-resolution pattern)

### D1 · Is error/fault actually red?

- **What this is:** The locked table row `error / fault → red, fast-strobe 2s`. You confirmed the
  *pattern* — **David said** (line 9620): *"Agreed that error/fault is fast strobe for 2 seconds."* —
  but never said the color. I proposed red, flagged it "(?)", and your next turn answered a
  *different* question (denied/unrecognized) and closed the session, so the "(?)" silently hardened
  into a locked fact.
- **Why it matters:** Red is the alert axis on every other row; fault is exactly where a wrong signal
  costs the most, so this cell should be ratified rather than assumed.

| Option | Tradeoffs |
|---|---|
| **Red (fast-strobe 2s)** | Consistent with the alert axis; the obvious read. Only "cost" is that it was never in your words. |
| Purple, or a distinct fault color | Distinguishes fault from denied — but you reserved purple for enroll, so this muddies that axis. |
| Leave color unspecified in v1 | Honest, but ships a table with a hole right at the reliability-critical row. |

**My recommendation:** Red — it's the consistent, low-risk read.
**Your call:** Red (fast-strobe 2s)

### D2 · Is capturing's color actually blue?

- **What this is:** The locked table row `capturing → blue, fast-strobe blip`. **David said** (line
  9620): *"Let's reserve blue for armed/awaiting… capturing takes almost no time, so there we need a
  fast strobe. processing can be breathing (blue)."* You gave capturing's *pattern* (fast strobe) but
  named blue only for armed and processing — capturing's color is my inference from context.
- **Why it matters:** Small alone, but it's the same silent-resolution pattern as D1. If the rule is
  "unless stated, inherit the surrounding rows' color," that *rule* should be ratified once, not
  re-guessed per row.

| Option | Tradeoffs |
|---|---|
| **Blue** | Bookended by two blue states in your own sentence; capturing is part of the blue active-auth flow. |
| A distinct capturing color | Signals the capture instant more loudly — but adds a color to a 3-color budget for a sub-second state. |

**My recommendation:** Blue — capturing belongs to the blue active-auth arc.
**Your call:** A distinct capturing color
**Comments:** Capture should be purple themed.

---

## Theme B — The load-bearing empirical claim (⛔ gates the whole pattern axis)

### D3 · Were the ring's patterns actually confirmed distinguishable? ⛔

⚠ **This one wants a hardware re-check, not a slot.** It's the claim the entire locked table stands on,
and the honest status is "not yet verified" — so the real answer is a two-minute demo on the glowing
S3, not a yes/no from memory.

- **What this is:** The spec states "three usable axes: color (3) × pattern (6 modes) × speed" as an
  established foundation. On hardware I ran a blue pattern demo and asked you four specific questions
  (line 9618): fast-flash vs fast-strobe visibly different? breathing vs slow-breathing a clear tempo
  difference? double-flash reads as two clean pulses? speed direction as assumed? **David said** (line
  9620): *"Definitely saw the blue patterns and thanks for clarifying the 3 axes."* — a general ack
  that patterns *exist* as an axis, not an answer to any of the four distinguishability questions.
- **Why it matters:** If two "distinct" patterns actually look the same on the ring, several table
  rows collapse into ambiguous signals and the table needs rework. The audit also found only **2**
  underlying modes were ever shown (fast-flash/strobe and breathing/slow-breathing are the same two
  modes at different speeds); `off`, `gradual-on`, `gradual-off` were named from the command's
  parameter shape, never demonstrated.

| Option | Tradeoffs |
|---|---|
| **Re-run the pattern demo on hardware, you eyeball the 4 questions, then lock** | Costs ~2 min next time you're at the sensor; gives the table a real foundation. My lean. |
| Lock the pattern cells as-is now | Fast, but bakes an unverified claim into the spec that the whole table depends on. |
| Simplify to only the demonstrated patterns | Safe, but may throw away usable distinctions before we've checked them. |

**My recommendation:** Re-verify on hardware before locking; until then the pattern cells stay
PROVISIONAL (the spec doc already carries this correction).
**Your call:** Neither
**Comments:** I was able to see the difference between the patterns, no need to re-run, but after we land the changes it's possible that we will need to make some changes. TBD based on interaction experiences.

---

## Theme C — The trigger model

### D4 · Poll-based trigger vs. the interrupt model you assumed?

- **What this is:** You opened the state machine assuming an interrupt — **David said** (line 9620):
  *"When a finger is placed on the sensor, the R503 fires an interrupt, and transitions to
  'capturing', correct?"* My investigation found WAKEUP is **not currently wired** (the ratified
  hookup carries only UART + power), so what actually runs today is `GenImg`-polling. The spec labels
  "start polled, add WAKEUP later" honestly as *my* lean — but your stated interrupt model was never
  explicitly corrected-and-reconfirmed by you before you closed the session.
- **Why it matters:** It decides whether armed/awaiting is a true low-power sleep state or an
  actively-polling one — a real hardware/behavior difference — and it gates whether the 6th wire
  (WAKEUP → GPIO) is near-term work or deferred.

| Option | Tradeoffs |
|---|---|
| **Start polled, defer WAKEUP** | Works today with the wiring you already soldered; UX-identical to the user. Add the interrupt later if idle power matters. My lean. |
| Wire WAKEUP now (6th wire) | True interrupt + lowest idle power, matches your original model — but needs another solder joint and a GPIO, and buys nothing the poll doesn't for the demo. |

**My recommendation:** Start polled, defer WAKEUP — identical experience now, one less wire.
**Your call:** Start polled, defer WAKEUP

---

## Theme D — Accuracy ratifies (correcting overclaims the spec made in its own voice)

### D5 · Ratify: downgrade "empirically verified" to what was actually shown

- **What this is:** The spec's primitive-inventory header says the 6 control modes are "empirically
  verified on the connected module." Only **color** was verified that way (a real sweep + your
  eyewitness). Of the 6 modes, only 2 were ever demonstrated; `off`, `gradual-on`, `gradual-off` were
  described from the command's parameter shape and never shown.
- **Why it matters:** A reader will assume everything under that header was hardware-checked the way
  color was. The correction just tells the truth: color verified, three modes unshown.

| Option | Tradeoffs |
|---|---|
| **Ratify the correction** | Spec states exactly what was verified vs. described. No downside. |
| Keep "empirically verified" | Leaves false confidence that unshown modes were checked. |

**My recommendation:** Ratify.
**Your call:** Ratify the correction

### D6 · Ratify: mark the architecture framing as my synthesis, not your words

- **What this is:** Two spec statements — the "two design halves (semantic state on the bus vs.
  R503-specific expression in the leaf)" framing, and the "`command_feedback()` MUST be rebuilt"
  directive — are my architectural synthesis. They're reasonable and not misattributed to you (no
  `**David said**` tag), but per the audit's hunt-for-inventions instruction they're flagged as
  architect conclusions never put to you for a yes/no.
- **Why it matters:** Low severity — but keeping the spec honest about which claims are yours vs. mine
  is the whole point of the audit discipline.

| Option | Tradeoffs |
|---|---|
| **Ratify (keep, attributed to me)** | Framing stays; provenance is honest. My lean. |
| Strike the framing | Removes correct, useful architecture for no real gain. |

**My recommendation:** Ratify — keep it, attributed to the architect.
**Your call:** Ratify (keep, attributed to me)

---

## Summary at a glance

- **D1** error/fault color → **Red**
- **D2** capturing color → **Blue**
- **D3** ⛔ pattern distinguishability → **Re-verify on hardware, then lock** (not a slot — a 2-min demo)
- **D4** trigger model → **Start polled, defer WAKEUP**
- **D5** "empirically verified" overclaim → **Ratify the correction**
- **D6** architecture framing → **Ratify (keep, attributed to architect)**

If these leans look right, "recommendations accepted" (with D3 understood as "re-verify on hardware")
closes the lot, and I author the `.lts` spec + rebuild `command_feedback()` to the confirmed table.
