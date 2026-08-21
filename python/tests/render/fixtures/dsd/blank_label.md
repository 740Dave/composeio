# Decisions — context-guard fixture · BLANK LABEL

> The EDGE-CASE negative control for [CXC-DSDV-CONTEXT-GUARD]: the label is STRUCTURALLY PRESENT but
> carries no prose. D1 is complete. D2's `What this is` is a bare label with nothing after it, and D3's
> is emphasis characters only. Both must count as BLANK — otherwise the guard measures the presence of
> a heading rather than the presence of context, which is the exact failure it exists to catch.

---

## Theme 1 — blankness

### D1 · A complete decision

**What this is** — the control row: this decision is fully formed and must NOT be named in the refusal.

**Why it matters** — a refusal that names a complete decision is a false positive, and one false
positive costs more trust than the guard earns.

| Option | Tradeoffs |
|---|---|
| **Leave it alone** | Correct |
| Flag it too | Wrong, and teaches the author to ignore the message |

**My recommendation** — **Leave it alone.**

**Your call: _______**

---

### D2 · A label with no prose after it

**What this is** —

**Why it matters** — this one is fine; only the gloss above is empty, so the refusal must name D2 for
`What this is` and NOT for `Why it matters`.

| Option | Tradeoffs |
|---|---|
| **Refuse** | A label with nothing after it is not context |
| Accept | Renders an empty line where the orientation should be |

**My recommendation** — **Refuse.**

**Your call: _______**

---

### D3 · Emphasis characters only

**What this is** — this gloss is present, so D3 must be named only for `Why it matters`.

**Why it matters** — ****

| Option | Tradeoffs |
|---|---|
| **Refuse** | Markdown that strips to nothing is nothing |
| Accept | The viewer shows an empty stakes line |

**My recommendation** — **Refuse.**

**Your call: _______**
