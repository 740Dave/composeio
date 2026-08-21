# Away-Watch Progress Report — overnight 2026-07-25

> David handed me an away watch to push the build wave to completion and write up the progress. This is it.
> **TL;DR:** 8 builds dispatched, **all 8 landed clean on local main tonight**; the **bus-peer primitive is
> complete**; the **console productization epic is COMPLETE** (foundations + render-deep + app surfaces all
> landed); the **DSD-viewer, decision-viewer, #57, and projects tab all landed**; **4 roster agents installed**.
> Nothing pushed (your call). Every landing was verified green and cherry-picked with the base-ancestry discipline.

## Landed on main tonight (8 commits on top of 588b04a — LOCAL, not pushed)

| Commit | What | Tests | Needs your glass |
|---|---|---|---|
| `be90415` | #57 `decisions_publisher` enrichment `[CXC-DPE]` | 42 +1skip | — (data) |
| `9cd08bd` | Bus-peer **slice A** — segment relay `[CIO-BUS-PEER]` | 15 peer / 1624 bus | isolation posture (netns + fw1/Tailscale matrix) |
| `2b099d8` | Decision-viewer standalone + launcher rail `[CXC-DVL]` | 54 | resize feel, geometry persist, recenter, rail status |
| `d2784f8` | Bus-peer **slice B** — MQTT↔CANbus bridge | 35 peer | physical CAN actuate-with-ack (hardware) |
| `28d9a0e` | Console foundations A+B `[CXC-CONSOLE-PROD]` | 195 | — (foundation) |
| `96a632f` | DSD-viewer interactive form `[CXC-DSDV]` | 225 | the form: pre-selected recs / override / comments / save-writes-back |
| `cee6ba0` | Console render-deep S1–S3 (resize / font S·M·L / 3 themes + dark custom colors) | 1845 render | resize feel, 3 themes, font steps, dark bg/accent colors |

## The deliverables

**Bus-peer primitive — COMPLETE (both slices).** The committed platform build you asked for
(`[DEC-BUS-PEER-RELAY-BUILD]`). Slice A = pull-initiated multiplexed **segment relay**; Slice B = the
**MQTT↔CANbus bridge** (on a pure-Python mock CAN interface). The load-bearing correctness bar is **proven on
both edges**: a `pri:critical` message resolves as a genuine end-to-end ack OR an explicit `_bus/fault` — the
peer is a transparent forwarder, never synthesizes an `ok`, **silent-clear count = 0** (including the CAN case
where a bare hardware-ack degrades to fault rather than fabricating `ok`). Decoupled from the SMD→LTS migration
by forward-authoring the spec in LTS. **Awaiting your ratify:** `[DD-BPR-MOCK-CAN]` (chose the pure-Python
virtual CAN), `[DD-BPR-RELAY-WRAP]` (`coordination_relay` left intact — replace vs wrap), `[DD-BPR-AUTH]` (token
handshake). Hardware glass gate: a physical CAN device actuating-with-ack.

**Console productization epic — COMPLETE (all three slices landed).** Foundations (per-instance personalization
model on the `config_persister`/`meta.<id>` seam + an explicit 51-descriptor widget registry with the pluggable
build-hook) + render-deep (resize/reflow, font-scale **S/M/L**, three themes **Dark/Light/High-contrast** +
**your dark-theme bg/accent color customization**) + app surfaces (S4 widget-selection, S5 Dashboard tab, S6
pluggable, and the **Config selector UIs** — theme/font/color pickers) are ALL LANDED (`f7f8c69`; 1908 render
tests green). All your DSD calls implemented (D1 per-instance, D2 themes+colors, D3 S/M/L, D4
`center_focus_strong`). **Honest open items (limitations, not bugs):** S4 reorder/hide has limited reach until
the console's frozen `pixel_stack` regions become relative containers (a follow-on tied to S1 reflow); Dashboard
live-add binds data subscriptions only on next launch (matches `[CXC-ICP-RESTART]`); the dark color pickers are
curated swatch palettes, not a continuous wheel (no Canvas color-wheel primitive). *Note:* the render-deep agent
hit the account session limit at 3am mid-commit; I salvaged its complete work (1845 render tests green) and landed it.

**DSD-viewer — LANDED.** The interactive form you wanted: radios **pre-selected to my recommendation**,
override, per-decision comment fields, write-back to the `Your call:` slots, save-guarded so merely rendering a
DSD never rewrites it. `send_dsd_for_human_review` MCP endpoint. The next DSD reaches you as a **form**, not
text. Deferred: `[DD-DSDV-MCP-HOME]`, `[DD-DSDV-MULTISELECT-MARK]`, `[DD-DSDV-DECISION-NODE-WRITE]` (markdown-only for now).

**Decision-viewer standalone + rail — LANDED.** Own resizable window + per-instance persisted geometry,
`center_focus_strong` recenter, the console rail (lts-engine status + fresh/stale + metrics + Open button).

**#57 — LANDED.** Rich node props + five edge kinds feeding the live Decisions viewer.

**Projects tab — FIXED + populated** (16 projects incl. the BSG game; the empty tab was a `[PROJECT-x]`
hyphen-vs-`[PROJECT:x]`-colon schema mismatch → 0 parsed). **Memory viewer — you approved it on the glass.**

**Roster — 4 agents installed** (factlattice-editor hybrid, git-expert, composeplatform-qa, composeux-architect
co-author reframe), each with an `[AGENT-*]` registration block. **Finding:** newly-*installed* agent files are
NOT dispatchable until a session reload (only *edits* to existing agents take effect same-session) — so
git-expert + composeplatform-qa serve me.next; I ran tonight's landings myself with git-expert's base-ancestry
discipline (caught a stale base on every worktree branch).

## Decisions recorded (nodes 114–119, engine fresh throughout)
OSS-publication · console-productize · decision-viewer-standalone-launcher · hybrid-knowledge-A/B-method
(deferred) · bus-peer-slice-sequence · console-UX-calls-settled (your DSD answers).

## To see it on the glass — a bounce
The running console (pid 51459, launched 23:12) predates tonight's landings. To see the new work:
`pkill -f "console.compose.yaml"` then relaunch with `--transports` (the `console-up` skill). The Projects tab
already populates live (60s poll).

## Git state (your call)
Local `main` is **8 commits ahead of origin — nothing pushed**. A large pile of **session WIP sits uncommitted**
(the decision-graph writes, agent installs, the DSD-skill fix, the projects fix, the specs, the DSD file, this
report) — preserved on disk, uncommitted (your call to commit/dispose). One stash: stale pre-render-deep
goldens (superseded, droppable). All build worktrees pruned.

## For me.next
- **Console epic is DONE** — all 8 builds landed. Follow-on from app-surfaces: deep-reflow the console's frozen
  `pixel_stack` collaboration region so S4 reorder/hide reaches every widget (tied to S1); and a live
  subscription-registration path if the Dashboard should hot-add widgets (currently modulo-restart).
- **Bounce the console** and verify the glass gates in the table above.
- Remaining roster: the **builder tier** def (needs David's scope input), the shared **BUILD-PROCESS.md** home.
- **Agents-in-graph Phase 2:** wire a roster corpus so the `[AGENT-*]` blocks are live-queryable (David's
  corpus-home call).
- The flagged `[DD-*]` decisions above await David's ratify.
- Reconcile the old tasks (#39/#42/#43/#46/#49/#53) — some may be superseded by tonight's work.
