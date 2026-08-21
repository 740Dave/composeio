# composeio.decision_extractor

A **DETERMINISTIC** (no LLM, no network) tool that reads a Claude Code session
`.jsonl` and emits a **DRAFT** LTS decision-provenance document — spec-35 shaped
(`corpora/platform-specs/35-intelligence-console-decisions.lts`) — pre-populated from
the decisions it finds in the transcript. So at end-of-session the architect
**refines a draft** instead of authoring the ledger from scratch.

Sibling to `composeio.session_sweep` (it imports that package's single-pass
`_Transcript` JSONL parser rather than duplicating it). Where the sweep answers
*what got left on the floor*, the extractor answers *what got **decided***.

## Why (the design law)

`bootstrap/dusk-gifts/README.md` — **"Externalize structure; make the summarizer
carry the LEAST."** Rationale is the one channel a `/compact` summarizer cannot
carry (it flattens a resolved fork to a bare conclusion and drops the argument a
successor needs to defend or revise it). LTS is purpose-built to hold it. This
tool mechanizes the *first pass*: SURFACE every decision with its evidence and
leave a literal `TODO` wherever the transcript does not supply a field. It
**never invents rationale** — where the bytes are silent, it writes `TODO`.

## Three sources

### 1. Explicit inline markers (a shared convention)

A convention both David and the Intelligence can type inline, in any assistant
**or** genuine-user turn. Case-insensitive; the trigger line may follow a `- ` /
`* ` bullet or `**` markdown emphasis.

**Primary (multi-line) form** — a trigger line, then any of the enrichment
tails on immediately-following lines (each OPTIONAL, any order):

```
load-bearing decision: <the decision / what was decided>
chose: <the chosen option>            (may be "chose: X over Y")
over: <the rejected alternative>
because: <the rationale>
flips-if: <the invalidating assumption>
tag: <DERIVABLE | CONTESTED | PREFERENCE>
status: <free text, e.g. open / resolved / ratified>
```

Tail aliases: `flips-if` also accepts `flips if`, `invalidated if`,
`would flip if`. An unmatched line ends the block.

**Compact single-line form** — everything on one line; the tag rides in parens:

```
load-bearing decision(CONTESTED): <title> — chose X over Y because Z; flips if W
```

The tail is split on `chose` / `over` / `because` / `flips if`; the tag is read
from the parentheses.

**Defaults & TODOs:** default `tag` = `UNTRIAGED`, default `status` = `draft`.
The marker's `tag` **is** the spec-35 `recoverability` field. Every field not
supplied (chosen / alternatives / rationale / invalidating_assumption) renders
as a literal `TODO` so the architect sees exactly what to fill.

### 2. Channel-reply markers (same grammar, over the bus)

An Intelligence resumed into the **Intelligence Console** speaks over ComposeBus
via the channel's `reply` tool — canonically `mcp__composeio-channel__reply` —
so any `load-bearing decision:` marker it plants lives inside that **tool call's
`input.text`**, not in a plain assistant/user turn. The text scan (source 1)
never sees it. This source closes that gap: it scans each channel-reply's
`input.text` with the **exact same marker parser** (both the multi-line and
compact forms — the grammar is not forked), and tags each resulting node
`channel-marker` (an HTML comment `<!-- source: channel-marker (turn N) -->`) so
a reader knows the decision came over the bus.

**Detection pattern.** A tool_use is treated as a channel reply when its `name`
ends with `__reply` **and** contains `channel` (case-insensitive) — general
enough that a differently-named channel still matches, precise enough not to
catch unrelated `*_reply` tools. Each reply's text is scanned independently so a
trailing marker's tail block cannot bleed across replies.

### 3. AskUserQuestion forks (automatic — no marker)

Every `AskUserQuestion` tool_use is a fork the human resolved, so it is captured
**automatically** (no marker needed). Each *question* in the call becomes one
node:

| spec-35 field       | source                                                    |
|---------------------|-----------------------------------------------------------|
| `decision`          | the question text (the fork)                              |
| `alternatives`      | the option labels                                        |
| `chosen`            | the recorded answer (parsed from the tool_result)        |
| `rationale`         | best-effort seed from a `(Recommended)` option and/or the assistant's pre-ask recommendation text, then `TODO: confirm rationale` |
| `invalidating_assumption` | `TODO`                                             |
| `recoverability`    | `UNTRIAGED` (TODO — the architect triages)               |
| `status`            | `draft`                                                   |

The answer parser anchors on the **known question texts** (from the tool_use
input), so an answer body containing commas or quotes does not break it; it also
handles the `"<label>" selected preview:<…>` single-select shape by keeping just
the chosen label.

## Output

A draft `.lts` string: YAML frontmatter (`status: DRAFT — auto-extracted <date>,
architect to triage`; `artifact-readiness: design-review`), a blockquote marking
it a machine draft to be refined, one `### [CXC-ICD-Dn]` node per decision
(numbered in transcript order, each source-tagged with an HTML comment —
`marker` vs `channel-marker` vs `askuserquestion`), a minimal Relationships stub,
and a Status
footer. The draft spec id is `[SPEC:CXC-IC-DECISIONS-DRAFT]` — distinct from the
real ledger's `[SPEC:CXC-IC-DECISIONS]` so the two never collide before triage.

## Usage

```bash
# End-of-session full pass (PRIMARY path) — print the draft:
python3 -m composeio.decision_extractor path/to/session.jsonl

# Write it to a file for the architect to refine:
python3 -m composeio.decision_extractor path/to/session.jsonl --out draft.lts

# Live: watch the file, re-emit the draft as decisions accrue (for a future
# console "decisions" panel). Poll-based; the full pass above is the main path.
python3 -m composeio.decision_extractor path/to/session.jsonl --tail

# Deterministic self-test (no network):
python3 -m composeio.decision_extractor --selftest
```

With no path argument the CLI defaults to the architect's session `.jsonl`.

## The grammar is a convention

The marker grammar above is a **convention** — the shared protocol both David
and the Intelligence use to plant load-bearing decisions inline as they happen,
so the end-of-session pass can harvest them. The architect will register it in
`spec/99-composeio-conventions.smd`.
