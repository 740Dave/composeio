# Independent design-input audit — `cic-mode-nav`

**Spec under audit:** `78-composeux-cic-mode-nav.lts` (`[SPEC:CXC-MODE-NAV]`, DRAFT v0.1, `[PLACEMENT UNRATIFIED]`)
**Ground truth:** David's 5 turns in the `cic-mode-nav` bracket (transcript indices 17026, 17049, 17059, 17060, 17071).
**Auditor was NOT in the design conversation.** Read-only; nothing landed.

---

## Headline

The extractor is FAITHFUL — I grepped the raw transcript independently and all three substantive David turns match the JSON byte-for-byte, nothing dropped or altered. The completeness claim ("all 5 statements accounted for") holds at the level of *nothing silently dropped* — every statement is either carried or explicitly parked in `[CXC-MODE-NAV-DEFERRED]`.

The problem is the OTHER direction, exactly as warned. **The spec's spine — a MODE ROW (STD/DRADIS/EXT/CHRONO) folded into the `cic_menu` tree as a new top level — is the ARCHITECT's proposal, not David's.** David was explicitly asked "do you want that consolidation now or later?" and **never answered it.** He answered a *different* question (duplicate-the-SVG) and then talked about rotaries, keypads and stations. The spec built its entire tree on the unanswered half.

**Findings: 4 QUESTIONS (AMBIGUOUS) · 4 CORRECTIONS (1 ALTERED, 3 INVENTED) · 0 MISSING.**

---

## QUESTIONS — silently-resolved ambiguities (only David can settle these)

### Q1 — The mode-fold "now or later" was ASKED and never ANSWERED  ⛔ highest value

In the opening turn the architect posed two questions. Question 2 was:

> "The **mode row itself** (STD/DRADIS/EXT/CHRONO) — is *that* also a `select` nav living in this same menu... it means the mode row stops being a special case in `composite.py` and becomes just the top `select` level — worth knowing if you want that consolidation **now or later**." (architect, opening turn)

David's reply (index 17049) addressed the *other* question — **David said** (line 17049): *"It's a happy accident that we don't need different screens for this particulaur use case. If it's more consistent and collapses certain problems to treat these as three different screens, it's easy to duplicate the SVG and I'd have no issue with that."* — and then went to rotaries, the keypad and soft-button state. **He never spoke to folding the mode row into the tree.** The architect's next turn unilaterally declared "Take the simplification... everything is uniformly a `screen`" and proceeded as if consolidation-NOW was chosen.

Everything load-bearing in the spec rests on this: `[CXC-MODE-NAV-TREE]` ("the tree gains a mode level at the top"), `[CXC-MODE-NAV-CIC-INSTANCE]` (the 4-mode top level, the `root` moving up one level), and the entire premise that `[CXC-MENU-CIC-INSTANCE]`'s ratified STD-only scope should be superseded. `[CXC-MODE-NAV-TREE]` is honestly marked `status: proposed — awaiting the architect's ratify`, but nowhere does the spec record that *David was asked now-vs-later and did not choose* — it reads as a settled design direction awaiting rubber-stamp, not as an open now/later fork.

**Do you want the mode row folded into `cic_menu` as the top tree level NOW (as the spec assumes), or is that a LATER consolidation and this slice should ship as CHRONO's own sub-nav + the selection primitive only?**

Your call: YES

---

### Q2 — "soft buttons ... states that they maintain" — read away as bus-derived

**David said** (line 17049): *"even if we use manual controls in some places, we may want to have the UX 'soft buttons' have different states that they **maintain**."*

`[CXC-MODE-NAV-SELECTION]` (`on-soft-buttons-with-state`) resolves this to: the states are "the DERIVED rendering of the published selection value ... not a second, independently-mutable copy of that state living in the widget," reasoning through `[DX-STATE-ON-BUS]`. The spec then explicitly declines to open it as a fork ("it is not itself re-opened as a fork because [CXC-CIC-TABROW] already implements it this way"). That is a defensible reading — but it *is* a silent pick. The word "maintain," plus David's separate remark that we "may well in the future need to have a different control that actually functions as a set of buttons," can equally read as: a *future* control that holds genuinely local/maintained state, distinct from the pure bus-derived selector.

**When you said soft buttons with "states that they maintain," did you mean (a) purely bus-derived display state (spec's reading — nothing new), or (b) a genuinely stateful control worth flagging as future work?**

Your call: I meant a purely bus-derived display state.  The pcb in ~/maker/pcb/button_matrix_4x4 is a good physical example of what we want to be able to do with virtual buttons.  The Galactica software object (compose-bus resident) will maintain state about all of its subsystems.  The state of these subsystems will be published.  Different button groups will react in different ways to different semantic messages.  When the FTL engine is ready, a big red button labled "JUMP READY" may start to blink on screen and in real life (different subscribers).

---

### Q3 — CHRONO's three options: you confirmed "three screens," the architect named them

**David said** (line 17049): *"we don't need different screens for this particulaur use case... treat these as three different screens, it's easy to duplicate the SVG."* You confirmed there are **three** screens sharing art. The specific labels — `ship-time` / `jump-countdown` / `timer` — are the **architect's** (opening turn: "Chrono's three (`ship-time` / `jump-countdown` / `timer`)"). Of the three, you named only the **timer** yourself (*"the timer screen," "set the countown timer to, say 33 minutes"*, line 17049). `[CXC-MODE-NAV-CIC-CHRONO-INSTANCE]` hard-codes all three labels and their slugs.

**Are `ship-time` / `jump-countdown` / `timer` the correct three CHRONO leaves and slugs?**

Your call: perfect.  The on-screen labels should not have hyphens should be all caps and be slightly more brief, e.g. "SHIP TIME" / "JUMP COUNT" / "TIMER". file names should have hyphens, of course.

---

### Q4 — Namespace shape (already a spec fork; confirming it is yours to decide)

The spec correctly does NOT silently pick here — `[DD-MODE-NAV-NAMESPACE-CONVENTION]` surfaces that your illustrative rotary message **David said** (line 17049): *"the rotary switch will emit a message like \"station_n::colonial_ux::tab_1_selected\""* uses `::` delimiters and a `colonial_ux` root, diverging from the ratified `colonial_console/<station>/<level>` (`/`-delimited) convention. This is well-handled — flagging it only so it doesn't get lost: it is the highest-churn open fork and genuinely needs your word (was `station_n::colonial_ux::tab_1_selected` a literal syntax preference, or just illustrative?).

Your call: Sorry, for whatever reason, '/' delimiters look like file paths to me, whereas '::' visually reads like namespace pathing.  But it doesn't matter and I don't see the point in changing this everywhere or supporting dual methods.  stick with '/'

---

## CORRECTIONS — MISSING / ALTERED / INVENTED

### C1 — INVENTED (structural, load-bearing): DRADIS & EXT as `annotation.path` override leaves

`[CXC-MODE-NAV-CIC-INSTANCE]` asserts DRADIS and EXT are override leaves carrying `annotation:{path: ".../dradis_mode.svg"}` / `ext_mode.svg`, and `MODE-NAV-C7` makes that a machine-tested acceptance gate. **David never mentioned DRADIS or EXT anywhere in the five turns** — the names and their treatment come from the architect's opening turn and from prior specs (`[CXC-CIC-RADIO-MODE]`), not from this design session. It is legitimately grounded in *prior* ratified art, but it is NOT session-derived and inherits Q1's unresolved premise (that the mode row is folded in at all). If Q1 resolves to "later," this node has no basis yet.

Ratify (DRADIS/EXT override-leaf treatment is prior-spec-derived, contingent on Q1)? I didn't mention DRADIS or EXT because they have been built and are running as worked examples on display-node.local (as you could see in the screen shot"

### C2 — ALTERED: your "we'll need a DIFFERENT control" repurposed as evidence for SAMENESS

**David said** (line 17049): *"the way we are using radio_button_group is functionally duplicative of a tab group, and we may well in the future need to have a different control that actually functions as a set of buttons or interactive elements responding to semantic messages."* `[CXC-MODE-NAV-SELECTOR-SKIN]` quotes this and uses it to justify collapsing `segmented_tab_row`/`radio_button_group` into ONE selector (correct for the first clause). But the second clause — your forward-looking need for a *genuinely different* control — is not carried anywhere as a live future need; it is only cited as support for the collapse. The `action`/`toggle` archetypes in `[CXC-MODE-NAV-DEFERRED]` plausibly cover it, but the link is never made, so your "different control" reads as settled-into-selector rather than parked.

Ratify (add an explicit note linking your "future different control" to the deferred taxonomy)? 

### C3 — INVENTED (low, but asserted as MUST): "drop the baked-in per-option buttons" I was vague, because I have not worked through enough of Colonial UX to have a full inventory of my UX element needs.  We should not guess at what we may need in the future.

`[CXC-MODE-NAV-CIC-CHRONO-INSTANCE]` (`dropped-elements`) mandates that any `slot:button@ship-time` / `@jump-countdown` / `@timer` elements baked into today's CHRONO art "MUST be dropped from the screen SVGs going forward." **David said nothing about existing baked-in buttons or removing them.** This is an architect inference asserted as a requirement (and `MODE-NAV-C8`'s glass gate tests for it). Reasonable, but it is spec-invented, not session-sourced.

Ratify (the drop-baked-in-buttons requirement is an architect inference, not from David)? It was an architect inference, and I have removed them.

### C4 — INVENTED (appropriately hedged): the `colonial-clock` widget/slot

`[CXC-MODE-NAV-CIC-CHRONO-INSTANCE]` names `slot:colonial-clock@<binding>`. **David never referenced a clock widget, a slot, or `colonial-clock`** — it originates in the architect's opening turn ("the single `slot:colonial-clock`"). This is the best-handled of the inventions: `[DD-MODE-NAV-CLOCK-WIDGET-REGISTRATION]` explicitly flags it `⛔ NEEDS-RENDER-ENGINEER-CONFIRM` and calls it a placeholder token. Noting it only so the provenance is on record: the clock widget is architect-introduced, not from the design session.

Ratify (acknowledge `colonial-clock` is architect/render-engineer territory, not a David statement)? I did suggest that we rename the 7 segment widget to 'colonial_clock' earlier this session and it was built -it's running on my glass right now.

---

## Statement-by-statement completeness check

| # | Index | Substance | Disposition |
|---|-------|-----------|-------------|
| trigger | 17026 | "let's do a design session" | n/a (session control) |
| A | 17049 | duplicate-SVG OK; radio≈tab; future different control; rotary→tab row; `station_n::colonial_ux::tab_1_selected`; keypad/33-min; soft-buttons maintain state | CARRIED (CHRONO shared-art, selector-skin, selection primitive, namespace fork, on-soft-buttons) + keypad/value DEFERRED. Ambiguities Q1/Q2/Q3 + alteration C2 live here. |
| B | 17059 | "input MAY be decoupled"; hardware frequently the controller; props (momentary/toggle/illuminated/thermal/thrust); semantic ship-model (damage/fuel/ammo/jump); "yes... there will be stations" | CARRIED (`input MAY`, control-is-peer, station-scope) + ship-model & prop taxonomy DEFERRED. |
| C(img) | 17060 | build-photo sources only | not modeled (correctly — they are images) |
| D | 17071 | station = physical console, loosely coupled to role; players run between panels; fingerprint sensor idea; "Good to close" | CARRIED (station-scope) + fingerprint DEFERRED. |

**No statement is silently dropped.** The completeness claim is accurate as stated.

---

## Scope — what I examined and what I did NOT

**Examined:** all 16 nodes of the spec (frontmatter + `[SPEC:CXC-MODE-NAV]` root + the 11 `CXC-MODE-NAV-*` nodes + all 4 `DD-MODE-NAV-*` decisions), the acceptance table (`MODE-NAV-C1..C8`), and the Relationships section. Read all 5 David turns from the extractor JSON **and independently grepped the raw transcript** (`sed`/`grep` on the `.jsonl`) to confirm verbatim text at indices 17049/17059/17071 — they match byte-for-byte. Also read the **architect's own bracket turns** (the DESIGN-SESSION-BEGIN framing + the three intervening replies + the END marker) to distinguish David-sourced claims from architect-sourced ones — this is where Q1 and C1–C4 came from.

**Did NOT do:** I did not open or verify the *content* of the referenced ratified specs (`[CXC-MENU-*]`, `[CXC-CIC-*]`, `[DX-*]`, `[CXC-COMP-*]`) — I took their existence and gist as the spec presents them, so a finding like "the spec misreads what `[CXC-MENU-CIC-INSTANCE]` actually says" is OUT of this audit's scope. I did not inspect on-disk SVG assets (e.g. whether `chrono_mode.svg`/`crono_mode.svg` truly both exist, or whether STD's 35 paths are byte-identical) — those are build-time checks the acceptance gates encode, not design-input questions. I did not run `lts_lint`/`lts_preview` (structural, not an intent-fidelity concern). Findings are limited to spec-vs-David-intent fidelity; a clean bill on the 11 other nodes means only "no INTENT divergence found," not "verified correct."
