<!-- dsd-status: answered 2026-08-18T22:43:07Z -->
# Decisions — the menu-indexed screen resolver spec (`[SPEC:CXC-MENU]`, draft 77)

> This consolidates the **five open forks** left in the menu-resolver spec draft
> (`corpora/platform-specs/77-composeux-menu-resolver.lts`, v0.3, unratified) after two independent QA
> passes and an architect read. Each carries: what the thing *is*, why it matters downstream, an options
> table with my honest lean **bolded**, my recommendation, and a `Your call:` slot. Answer by filling each
> slot (or the viewer's radio + comment), or bless the lot with **"recommendations accepted."** Nothing in
> the spec lands until these are ruled.

**How to use this.** Every recommendation is an honest engineering lean, not a fence — overrule any of them
freely; a one-word "no, do X" is enough, and I encode your call, not mine. Decisions marked **⛔** are
**blocking**: they shape the engine/slot contract or the file's placement, so real downstream work waits on
them. **D3** and **D4** are non-blocking (a deferral and a convention taste-call) and can be decided in any
order. Two of these (**D2**, **D1**) amend already-ratified decisions, so they need your direct read rather
than a rubber stamp — they're flagged inline.

---

## Theme 1 — Resolution behavior & placement (the structural, blocking calls)

### D1 · ⛔ Adopt the fallback walk, or keep the ratified "straight to placeholder"? — *(`[DD-MENU-WALK-AMENDMENT]`; ⚠ amends ratified `[CXC-COMP-MISSING]`)*

- **What this is.** Today, a screen with no art authored yet renders a bare "— screen not built" placeholder
  immediately — that's the ratified `[CXC-COMP-MISSING]` rule. The proposed *fallback walk* first tries a
  shared `default.svg` at each parent level (sub-tab → console-level default → mode-level default) and only
  shows the bare placeholder if none exists. You proposed the `default.svg` fallback yourself earlier in this
  session; this is the formal ratify of it.
- **Why it matters.** It changes a rule you already ratified, and it's the difference between a
  half-built console looking *coherent* (a shared console backdrop under the unbuilt tabs) versus showing bare
  placeholders while you author screens incrementally. It's deliberately built so that if no `default.svg` is
  ever authored, behavior is byte-identical to today.

| Option | Tradeoffs |
|---|---|
| **Adopt the walk** (sub-tab → ancestor defaults → placeholder) | Graceful mid-authoring degradation; only ever resolves to an *explicitly authored* ancestor `default.svg`, never another screen's art; no-op if no default is authored. Amends a ratified invariant. |
| Keep the ratified never-fall-through | Zero change to a ratified rule; but a half-built console shows bare placeholders instead of a shared backdrop. |

- **My recommendation.** **Adopt the walk.** You proposed it, it's genuinely useful, and its no-op-if-unused
  degradation makes it low-risk to a rule you already own.
**Your call:** Adopt the walk (sub-tab → ancestor defaults → placeholder)

### D5 · ⛔ New numbered doc, or fold the engine into `[SPEC:CXC-COMP]`? — *(`[DD-MENU-SPEC-HOME]`; placement)*

- **What this is.** The generic resolver is drafted as its own new numbered spec
  (`77-composeux-menu-resolver.lts`). The alternative is folding it into the existing `[SPEC:CXC-COMP]`
  (which owns BSG's SVG/slot composition). This also settles where the file physically lands — per the
  ratified corpora-out-of-repo rule, into the external corpus home, which then needs your `/mcp reconnect`.
- **Why it matters.** `[SPEC:CXC-COMP]` is BSG-specific SVG/slot policy. Folding a *domain-agnostic* engine
  into it would drag that SVG vocabulary along the moment a second ComposeIO app wants to reuse the engine —
  the exact seam the whole spec is built to keep clean. And nothing lands until placement is ruled.

| Option | Tradeoffs |
|---|---|
| **New doc `77-`, migrate §6's policy nodes out to `[CXC-COMP]`** | Keeps the generic engine portable and `[CXC-COMP]`'s node ids stable; leaves a clean home for a second app's future instance section. §6 (the SVG/slot extensions) moves to its true home. |
| Fold the whole thing into `[SPEC:CXC-COMP]` | One fewer document; but welds a reusable engine to BSG's SVG policy and re-opens `[CXC-COMP]`'s ids. |

- **My recommendation.** **New doc for the engine, and migrate §6 out to `[CXC-COMP]`** (those nodes already
  declare `part-of: [SPEC:CXC-COMP]`). Then `/mcp reconnect` seats it.
**Your call:** New doc 77-, migrate §6's policy nodes out to [CXC-COMP]

---

## Theme 2 — The slot contract

### D2 · ⛔ How does a self-contained scene fit the settled slot grammar? — *(`[DD-MENU-SLOT-SCENE-GRAMMAR]`; ⚠ amends-settled territory)*

- **What this is.** The slot label grammar is settled as `slot:<widget>@<binding>` — a *registered widget
  kind* followed by an `@`-delimited binding. A self-contained *drop-in scene* (like the engine-thrust panel,
  which subscribes `throttle/left` + `throttle/right` on its own) has no single external binding to hand in,
  so it doesn't obviously fit that grammar. (This is the correction to my earlier "just drop the `@binding`"
  suggestion — QA caught that that silently breaks the ratified grammar.)
- **Why it matters.** It decides whether dropping a whole reactive scene into a screen keeps the twice-ratified
  slot grammar intact, or forces a parser/grammar change. Getting it wrong quietly amends a settled convention.

| Option | Tradeoffs |
|---|---|
| **Arm A — register a scene as a widget kind** (`slot:scene@engine-thrust`) | Fits the settled grammar *unchanged*: `<widget>` = the registered `scene` kind, `<binding>` = an optional mount-scope. Registering a widget kind is already how the grammar extends. No amendment. |
| Arm B — amend the grammar to admit a bare `slot:<scene>` | Slightly terser labels; but a genuine change to a convention ratified twice, needing full amendment discipline, for a case Arm A already covers. |

- **My recommendation.** **Arm A.** It keeps the ratified grammar and reuses the existing widget-kind
  extension path; Arm B pays a grammar-change cost for nothing Arm A can't express.
**Your call:** Arm A — register a scene as a widget kind (slot:scene@engine-thrust)

### D3 · The stretch/reflow override mechanism — specify now, or defer? — *(`[DD-MENU-FIT-MODE]`, override half)*

- **What this is.** When a widget's native aspect ratio differs from its slot's, it *letterboxes* by default
  (uniform scale to fit, empty margins, never cropped/distorted) — you already decided that. This is *only*
  about the mechanism for *requesting* a non-default fit (stretch, or internal reflow) on a specific slot.
- **Why it matters.** Designing that override API now, with no widget that needs it, is a guess against an
  imagined case; but a genuine full-bleed background image would eventually want "fill exactly, no letterbox."

| Option | Tradeoffs |
|---|---|
| **Defer until a real widget needs a non-letterbox fit** | The letterbox default already covers every current screen; the override API gets designed against a concrete case instead of a guess. |
| Specify the override mechanism now | Completeness up front; risks an API shaped for a hypothetical that the first real case doesn't match. |

- **My recommendation.** **Defer.** Nothing needs a non-letterbox fit today; spec the override the moment a
  widget actually does.
**Your call:** Defer until a real widget needs a non-letterbox fit

---

## Theme 3 — Screen-authoring convention

### D4 · Content-band mandatory, or coexist with `ref:`? — *(`[DD-MENU-CONTENT-BAND]`)*

- **What this is.** A screen SVG's renderable region can be marked two ways: the existing **opt-out** `ref:`
  tags (tag each *context* layer to skip it), or the new **opt-in** `content-start`/`content-end` band
  (bracket the content *once*, everything outside is context). This asks whether the band becomes *mandatory*
  on every screen, or *coexists* with `ref:` so an author picks per screen.
- **Why it matters.** Making the band mandatory forces *every* screen — even a simple one-slot screen like
  helm/heading — to author the sentinel pair or fail the lint; coexistence lets simple screens stay simple and
  reserves the band for busy mockups where tagging every context layer is the real chore.

| Option | Tradeoffs |
|---|---|
| **Coexist — author picks per screen** | Simple screens use `ref:` (or nothing); mockup-heavy screens use the band. No forced ceremony on a one-slot screen. Two conventions to understand. |
| Band becomes the mandatory default | One convention to learn; but every screen must author the pair, and a simple screen fails the lint without it. |

- **My recommendation.** **Coexist** — but this is a genuine taste call about your own authoring workflow, so
  it's the one here I hold most loosely. If you'd rather have one convention, mandatory-band is defensible.
**Your call:** Coexist — author picks per screen
**Comments:** We will probably want to shift to an always-required model in the future, but now, for the transition period, co-exist please.

---

## Summary at a glance

Bless the whole set with **"recommendations accepted"**, or overrule any line.

- **D1 (⛔)** — Adopt the fallback walk (ancestor `default.svg` before the placeholder).
- **D5 (⛔)** — New doc `77-`, migrate §6's policy nodes out to `[SPEC:CXC-COMP]`.
- **D2 (⛔)** — Arm A: register a self-contained scene as a widget kind (grammar unchanged).
- **D3** — Defer the stretch/reflow override until a real widget needs it.
- **D4** — Content-band and `ref:` coexist; author picks per screen.
