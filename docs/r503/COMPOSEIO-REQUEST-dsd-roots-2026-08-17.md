# Requests to the ComposeIO owner — from the R503 project, 2026-08-17

**Raised:** 2026-08-17, by the composeux-architect working the R503 project.
**Owner:** whoever holds `composeio`. ⛔ **This is not mine to change** — I work only in
`${DATASHEETS_HOME}/R503` and do not write outside it.
**Requested by David**, who asked for this written up so he can hand it over.

⚠ **The filename says `dsd-roots` because ask A was the original subject.** It now carries **three**,
added 2026-08-17 after an independent review found that B and C had been promised in conversation and
never landed here — a one-ask document that everyone believed carried three. The name is kept so
existing citations still resolve.

| ask | subject | severity |
|---|---|---|
| **A** | `send_dsd_for_human_review` cannot reach a DSD outside the composeio checkout | convenience gap, confirmed by reading the source |
| **B** | **Platform spec 32 (`[SPEC:CXC-HAL-DECLARE]`) carries three claims the R503 work contradicts** | ⛔ **spec drift — one clause is now OVERRULED and still reads as normative** |
| **C** | The `decision-support-doc` skill's context gate passes a DSD that is still undecidable | process gap, with a worked failure |

⛔ **B is the one that bites if it is dropped.** A and C cost convenience; B leaves a *superseded rule
in force* in the fleet's own spec, where the next author will read it and obey it.

---

# ASK A — `send_dsd_for_human_review` cannot reach a DSD outside the composeio checkout

## The symptom

A DSD authored outside the composeio checkout cannot be delivered to the DSD viewer at all:

```
send_dsd_for_human_review("${DATASHEETS_HOME}/R503/docs/hal-contract/DECISIONS-hal-contract-close.md")
→ {"success": false, "status": "error", "message": "not a repo file: '...'"}
```

The R503 design project lives outside composeio **by David's standing ruling** (staged there until the work is
solid enough to check in) and, as of today, is **not under git at all**. So it has no working DSD delivery path.

⚠ **The workaround in use until today was worse than the problem:** DSDs were being COPIED into
`composeio/compose_projects/bsg/dsds/` purely to satisfy this check — writing into someone else's tree to route
around a guard. Both copies have now been deleted and the practice stopped.

## Where it comes from

**One function, and all three review surfaces inherit it.**

| file | line | what it does |
|---|---|---|
| `python/composeio/decision_ops/dsd_review.py` | **92–121** | `_allowed_dsd_roots()` — builds the allowed-root list |
| `python/composeio/decision_ops/dsd_review.py` | 122–145 | `resolve_dsd()` — returns `None` for anything not contained in those roots |
| `python/composeio/decision_ops/dsd_review.py` | 259–262 | `send_dsd_for_human_review()` — `None` → `"not a repo file"`, publishes nothing |
| `hookup_review.py` | 172, 194 | `from .dsd_review import … resolve_dsd as resolve_repo_file` |
| `beepout_review.py` | 55, 123 | `from .dsd_review import REPO_ROOT, resolve_dsd as _resolve_repo_file` |

⭐ **`resolve_dsd` IS the shared resolver.** The hookup and beep-out surfaces alias it rather than duplicating
it, so **one change fixes all three** — no triplicated edit, and no risk of the three drifting apart.

The roots it allows today are exactly three, and every one of them is composeio:

```python
roots = [REPO_ROOT.resolve()]           # the canonical checkout
_add(containing_worktree_root(Path.cwd()))   # the caller's worktree
_add(own_checkout_root())               # the checkout this code was loaded from
```

## Why I read this as a defect rather than a design choice

⛔ **The function's own docstring already argues the case.** It records that the root list was widened once
before, for exactly this class of reason:

> *"A DSD is a working document — reviewing the one in your own worktree is the normal case — so widening here
> costs nothing and keeps the containment guard intact. (Contrast the graph WRITE guard, which refuses a
> worktree path outright: a permanent RECORD has exactly one home.)"*

That distinction is the whole argument. A **permanent record** has one home, so the graph write-guard is right
to be strict. A **working document under review** does not — and the tool description calls the viewer *"a
general capability."* The current roots encode *"a DSD lives in composeio,"* which was true when written and is
no longer.

## The change requested

**Admit one or more additional DSD roots from configuration**, alongside the three already computed. An
environment variable in the shape of the existing `COMPOSEIO_ROOT` (line 55) would match local convention —
e.g. a path-separated `COMPOSEIO_DSD_ROOTS` folded in by the same `_add()` helper — but **the mechanism is
yours to choose**; a config key would serve equally.

Roughly:

```python
def _allowed_dsd_roots() -> List[Path]:
    roots = [REPO_ROOT.resolve()]
    def _add(p): ...                      # unchanged
    ...                                    # worktree + own-checkout, unchanged
    for extra in os.environ.get("COMPOSEIO_DSD_ROOTS", "").split(os.pathsep):
        if extra.strip():
            _add(Path(extra.strip()))
    return roots
```

⚠ **Note the path contains a space** — `${DATASHEETS_HOME}/R503`. Whatever separator
is chosen must survive that. (`os.pathsep` does; a space-separated list does not.) This has already bitten
once in this project: PlatformIO refuses that path outright, which is why the R503 firmware builds via a
mirrored directory.

## ⛔ What must NOT change

**The containment property is the point of that code and must survive.** `resolve_dsd` exists so an arbitrary
path cannot be published to the viewer — `/etc/passwd` is refused, and must stay refused. The request is to
make the root SET configurable, **not** to remove the containment check. A change that simply returns the
resolved path unconditionally would satisfy the symptom and destroy the guard.

Equally: the `[CXC-DSDV-CONTEXT-GUARD]` refusal (a DSD whose decisions lack `What this is` / `Why it matters`)
is downstream of this and must keep firing. This request touches **where a DSD may live**, nothing about
**whether it is fit to publish**.

## Acceptance — both arms, because one arm proves nothing

1. **POSITIVE** — with the extra root configured, this returns `status: "sent"`:
   `send_dsd_for_human_review("${DATASHEETS_HOME}/R503/docs/hal-contract/DECISIONS-hal-contract-close.md")`
   (a real, answered DSD — six `### D<n>` decisions, every one carrying both required context fields).
2. ⛔ **NEGATIVE** — with the same configuration active, `send_dsd_for_human_review("/etc/passwd")` still
   returns `"not a repo file"` and publishes nothing. **Without this arm the change is untested**: a guard that
   has only ever been observed admitting things has not been shown to refuse anything.
3. **UNCHANGED** — a DSD inside composeio still resolves exactly as it does today.
4. **SIBLINGS** — `send_hookup_for_human_review` and `send_beepout_for_human_review` inherit the widening
   through the shared import and are worth one smoke each, since they alias the same function.

## What happens if this isn't done

Nothing breaks; the R503 project simply delivers DSDs as plain files and names the path in conversation. That
costs the radio-button review interface — the thing the viewer exists to provide — and it costs it exactly
where the decision volume is highest right now. It is a convenience gap, not a blocker, and it should be
weighed as such against whatever else is in flight.

---

# ⛔ ASK B — platform spec 32 carries three fingerprint claims the R503 work contradicts

**Target:** `corpora/platform-specs/32-composeux-hal-declaration.lts` (`[SPEC:CXC-HAL-DECLARE]`).
⚠ **Resolve the path from a live receipt, not from this document** — the composeio corpora tree moved on
2026-08-17 and `lts_lint` now reports it at
`~/.claude/factlattice_corpora/composeio/corpora/platform-specs/`. Read `profile.path` off any lint.

⭐ **Why spec 32 in particular:** it is the spec that names the **R503 by name** as the worked example of
its `role: transactional` peripheral class (line 104). It was written before this fleet had a fingerprint
sensor in hand, so its fingerprint surface is a *sketch that has now been built against* — and three of its
clauses are no longer true. All three citations below were read out of the file, not recalled.

## B1 — ⛔ `templates NEVER cross the bus` is OVERRULED and still reads as normative

**Line 5**, in the spec's own `not-this:` frontmatter:

> *"raw framebuffers/images/waveforms/**templates** NEVER cross the bus"*

**David overruled this on 2026-08-17**, in those words: *"templates-never-cross-the-bus needs to be
revoked/overruled."* The R503 identity design **requires** templates on the bus — a roster is distributed
to devices by `fingerprint_storage`, and `write_template` / `read_template` are two of the HAL's eleven
operations. The R503 side records the overrule; **spec 32 does not**, and a reader of spec 32 alone gets
the revoked rule with no signal that it is revoked.

⛔ **This is the ask that must not be dropped.** The other two are inconsistencies a careful reader will
notice. This one is a **rule the next author will obey**, and obeying it makes the identity layer
unbuildable.

**Suggested minimum:** strike `templates` from that list and add a one-line supersession note naming the
2026-08-17 ruling. Framebuffers/images/waveforms are untouched — only `templates` is at issue.

## B2 — `fingerprint/match {id, score}` publishes a field that is now device-internal

**Line 104:**

> *"`fingerprint/enroll/status {step, prompt}` + `fingerprint/match {id, score}` out"*

**`score` is RULED device-internal and never on the wire** (R503 `[CXC-FPHAL-SCORE-DEVICE-INTERNAL]`,
answered on the HAL-closing DSD, D3). The match decision is made against a threshold **on the device**; a
score crossing the bus invites a consumer to re-threshold it, which is exactly the containment failure the
HAL exists to prevent. **Suggested:** `fingerprint/match {id}`.

## B3 — the namespace spelling differs, and nobody has ruled which wins

Spec 32 spells the progress namespace `fingerprint/enroll/status {step, prompt}` (lines 104, 111, 164).
The R503 identity spec carries `enroll_progress { ordinal, capture_n, capture_of }`. These are two
spellings of one thing and both are currently written down as if authoritative.

⚠ **This is a DECISION, not a defect — it is `[DD-R503-NAMESPACE-SPELLING]`, still OPEN**, and it is open
precisely because it cannot be settled from inside R503: the R503 side cannot edit spec 32, and picking
unilaterally would fork the fleet vocabulary. **Route it to David; the R503 side will conform to whatever
is ruled.**

## What is NOT in dispute — say this so the ask is not read as wider than it is

✅ Spec 32's `assurance: experiential` treatment of a consumer fingerprint reader (lines 5, 256) is
**correct and the R503 work adopts it** — spoofable, no liveness, no secure element, theatrical rather
than access control. ✅ The `role: transactional` classification is right. ✅ The authority-gating of
`fingerprint/cmd/*` (line 216) is right. **Only the three clauses above are contested.**

## Acceptance

1. Spec 32 no longer lists `templates` among what never crosses the bus, and carries the supersession note.
2. `score` is gone from the published `fingerprint/match` shape.
3. The namespace spelling is either ruled (one spelling, both specs agree) or explicitly recorded as open
   in **both** documents — ⛔ not silently left as two confident, different answers.

---

# ASK C — the `decision-support-doc` context gate can pass an UNDECIDABLE decision

**Target:** the `decision-support-doc` skill and `[CXC-DSDV-CONTEXT-GUARD]` /
`composeio.render.widgets.dsd_document.validate_dsd`.

## The gate today

`send_dsd_for_human_review` refuses to publish any DSD in which a decision leaves **`What this is`** or
**`Why it matters`** blank, where blank is *structural* — the label present and followed by non-empty
prose. That guard is good and it should stay. ⛔ **This ask does not weaken it.**

## The failure it does not catch

On 2026-08-17 a DSD shipped with both fields populated and one decision (**D1**) that was still
**undecidable** — and worse, whose `Why it matters` line was **false**. It read that the choice would
change *"every enrollment call site in three components."* Under a ruling recorded hours earlier
(`[DD-FPHAL-HOST-OR-VM-BINDING]`: the three components are ordinary bus clients and **never call a HAL
method at all**), there were no such call sites. The document parsed, rendered, and passed every check.

**David caught it in one question:** *"does the DSD you just opened on my glass reflect the architectural
discussion we were having? Or is it stale at this point?"*

⭐ **The mechanism:** the gate asks *"is this field filled in?"* and the harm is *"is the ADJACENT SETTLED
GROUND named?"* A question is undecidable not when it lacks context but when it lacks **the specific
rulings that already constrain it** — and a decision whose surrounding ground moved between drafting and
delivery reads perfectly while being about a world that no longer exists.

⚠ This is the same shape as the failure that produced the existing guard (a DSD whose D2 said *"the Turbot
images"* and never said what images were). That one was fixed by requiring a field. **This one cannot be,
because the field was there and wrong.**

## The change requested — the mechanism is yours; I have a lean and I flag its cost

**My lean: a third required field — `Already settled` — naming the rulings this decision is bounded by,
or explicitly `none`.** Requiring an explicit `none` is the point: it makes *"I checked and nothing
constrains this"* a written claim rather than an omission, and an author who cannot fill it has just
discovered they have not checked.

⚠ **Its honest cost, stated plainly:** a structural check on a third field is still only a
presence check, and a wrong `Already settled` would pass exactly as D1's wrong `Why it matters` did. **It
raises the floor; it does not close the hole.** A stronger form — the skill re-querying the decision log
per candidate at authoring time, which the skill's own text already anticipates
(`[FIX-DSD-IS-A-QUERY]`) — would actually close it, and costs more to build.

## Acceptance

1. A DSD omitting the new field is refused, naming the offending decision by heading — same shape as the
   existing refusal.
2. ⛔ **NEGATIVE ARM:** a DSD carrying the field is still published. A guard only ever observed refusing
   has not been shown to admit anything, and the inverse arm is the one that catches an over-tight regex.
3. ⚠ **The ~24 retained DSDs under `docs/` are answered, read-only posterity and must never be re-sent.**
   Most already fail today's guard. That is expected and is not a reason to weaken either check.

---

**Contact:** raise anything unclear with David; I can answer questions about the R503 side but will not edit
composeio.
