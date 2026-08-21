# Decisions — context-guard fixture · COMPLETE

> The POSITIVE control for [CXC-DSDV-CONTEXT-GUARD] (David 2026-08-17). Every decision below carries
> BOTH a `What this is` and a `Why it matters` line, so `validate_dsd` must ACCEPT it and
> `send_dsd_for_human_review` must publish its intent.
>
> It deliberately exercises all three authored punctuation forms of the labels — `:**`, `.**`, and
> `** —` — because the parser previously recognised only the first and silently dropped the others.

---

## Theme 1 — the fixture's decisions

### D1 · ⛔ The em-dash label form

**What this is** — the house style writes the label bold and follows it with an em-dash rather than a
colon. This paragraph also soft-wraps, so the collector has to gather two source lines.

**Why it matters** — measured: the pre-fix grammar required a colon somewhere on the line, so this
form parsed as nothing at all and the viewer showed a decision with no context.

| Option | Tradeoffs |
|---|---|
| **Accept all three forms** | One alternation in the label grammar; no authoring change needed |
| Force authors onto the colon form | Cheaper regex, but every existing DSD becomes non-conforming |

**My recommendation** — **Accept all three forms.**

**Your call: _______**

---

### D2 · The colon label form

**What this is:** the original authored form, `**What this is:** text`, still the majority of the
retained corpus.

**Why it matters:** whatever the guard does, it must not refuse the documents that were correct all
along — a false refusal trains the author to route around the guard.

| Option | Tradeoffs |
|---|---|
| **Keep parsing it** | Backwards compatible with every filed DSD |
| Drop it | Silently unrenders half the retention directory |

**My recommendation** — **Keep parsing it.**

**Your call: _______**

---

### D3 · The full-stop label form

**What this is.** A third form seen in the wild: the label terminated with a full stop inside the
emphasis.

**Why it matters.** It is a real authored form in `docs/`, so leaving it out of the grammar would
make the guard refuse documents whose context is genuinely present.

| Option | Tradeoffs |
|---|---|
| **Accept it** | One more alternation branch |
| Refuse it | A correct document is refused for its punctuation |

**My recommendation** — **Accept it.**

**Your call: _______**

---

## Summary at a glance

- **D1** → Accept all three forms
- **D2** → Keep parsing it
- **D3** → Accept it
