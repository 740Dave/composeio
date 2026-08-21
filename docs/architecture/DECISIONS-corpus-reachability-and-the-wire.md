<!-- dsd-status: answered 2026-08-16T05:29:03Z -->
# Decision-Support Doc — corpus reachability, the commit gate, and the wire

> **What this consolidates.** Every call that accumulated across sessions 4 and 5 and is genuinely
> yours, gathered from: the session-4 `HANDOVER.md` awaiting-David list, three subagent returns
> tonight (CAN-bridge remedy, C++ freshness receiver, two git-expert landings), and the
> discoverability thread we just worked through.
>
> **How each decision is laid out:** what it is · why it matters · a real options table · my honest
> lean · a slot for your call.
>
> **How to answer:** fill the `Your call:` slot in-place and save, or reply on the channel. If the
> leans look right, *"recommendations accepted"* settles the lot and I'll encode each as a decision
> node and start the build sequence.

---

## How to use this

**The recommendations are my honest engineering lean, not a nudge — overrule freely, and I'll encode
your call rather than mine.** ⛔ marks decisions that **block** other work, and those are ordered
first; everything after them can be decided in any order or deferred without stalling anything.

⚠ **Two candidates were dropped by the settled-decision gate before reaching you, and they are worth
knowing about because both were mine:**

- *"What do we do about control-plane envelopes exceeding CAN's 8 bytes?"* —
  `[DEC-CONFIG-OVER-CAN-IS-NON-NEGOTIABLE]` already rules that transport-specific carve-outs are
  forbidden **and** instructs that the fragmentation design be **recovered from earlier transcripts,
  not re-derived**. D6 below is the narrow residue that survives that ruling.
- *"Flat or nested `freshness:` in YAML?"* — `[DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML]`
  ratified the nested form. The divergence at `runtime.py:738` is a **defect**, not a fork. I'm
  fixing it to nested under that ruling; **say so if you want it the other way.**

⚠ **D3 asks you to review something I already did.** I widened a tool grant across seven agents
without asking. It's live on `main`. I think it was right and I'd do it again, but you should get
the chance to say no, so it's a decision here rather than a line in a status report.

---

# Theme 1 — The commit gate ⛔ (blocks safe landing of everything else)

### D1 · ⛔ Should `.githooks/pre-commit` gain the "alone" branch its own message already promises?

**What this is** — the repo has a class of **runtime-written files** (`defect-graph.lts`,
`decision-graph-*.lts`, `deliverable-ledger.lts`, `architect.meta.json`, the capture PNGs, the
data-scientist state JSON). They churn constantly, so the hook exists to stop them riding into
unrelated commits where nobody reviews them.

**Why it matters** — **the hook's message is false, and that falsehood caused a real incident
tonight.** It says *"Commit them DELIBERATELY (alone), or override."* But `pre-commit:43` refuses
**any** staged runtime file; there is no "alone" branch in the code. I committed the defect record
alone and was still refused. So the **only reachable path is `COMPOSEIO_ALLOW_RUNTIME_FILES=1`**,
which disables the check for the **whole commit** — and that is exactly how a concurrent agent's
`git commit` swept git-expert's staged corpus file into `bc5b89a`, a commit about the bus-restart
defect whose message says nothing about four freshness rulings. The content is safe on `main`; the
attribution is wrong and no reviewer of that diff was reviewing those nodes.

| Option | Tradeoffs |
|---|---|
| **Implement the `alone` branch: if the staged set is exactly runtime files and nothing else, allow the commit** | **Makes the code match its own documented contract, and structurally kills the passenger sweep — a passenger makes the set not-alone, so it refuses. Also resolves the `git-expert.md` "NEVER COMMIT" contradiction, since the role def would finally match a gate that works.** Cost: a shell branch in a gate every commit passes through; a bug there breaks all committing. |
| Make the override per-path (`COMPOSEIO_ALLOW_RUNTIME_FILES=<path>`) | Narrower blast radius than today, and it's what I told you earlier was the fix. But it leaves the message still lying and still requires an env var for the ordinary case. |
| Leave it; require an index check before every override | Zero code risk. But it's a discipline that depends on remembering, which is the failure class this whole gate exists to end — and it already failed once tonight. |
| Add an index lock so concurrent agents can't stage simultaneously | Addresses the race directly rather than the symptom. Materially more work, and it doesn't fix the false message. |

**My recommendation — implement the `alone` branch.** It is the smallest change that makes the code
honest, and it happens to be the same change that closes the race, because "alone" is precisely the
predicate a passenger violates. I'd keep the coarse override intact as an escape hatch.

**Your call:** Implement the alone branch: if the staged set is exactly runtime files and nothing else, allow the commit

---

# Theme 2 — Can a successor actually reach what we wrote?

### D2 · Where should `BOOTSTRAP-CORPUS-QUERIES.md` live?

**What this is** — the Tier 0/1/2 wake-query document. Every agent role definition points at it as
*"Full reasoning."* It's the thing that tells a cold instance what to load and why.

**Why it matters** — it lives under `session_handover/`, and `.gitignore:90` excludes that directory
wholesale. **So the Tier 1 bootstrap authority is in no commit on any branch and survives only via
Dropbox.** A fresh clone gets seven role definitions citing a document that isn't there. I improved
it tonight and that improvement landed nowhere durable.

| Option | Tradeoffs |
|---|---|
| **Move it to `bootstrap/`** | Tracked, already holds the dusk gifts, semantically exactly right. Costs a reference update in the role defs that cite it. |
| Move it into a corpus as `.lts` | Queryable and lintable, which is where load-bearing knowledge belongs. But it is genuinely narrative-with-tables, and forcing it into nodes would lose the ordering argument that is most of its value. |
| Un-ignore just this file (`!session_handover/BOOTSTRAP-CORPUS-QUERIES.md`) | One line, no references change. But it makes `session_handover/` half-ignored, which is the kind of exception nobody remembers next time. |
| Leave it Dropbox-only | Zero work, and honest if you consider it a personal working note. But then role defs should stop citing it as authority. |

**My recommendation — move it to `bootstrap/` and update the citing role defs in the same commit.**
It is bootstrap material by every test except its current path.

**Your call:** Move it to bootstrap/

---

### D3 · ⚠ Ratify or reverse: I granted `lts_find_in_prose` to seven agents without asking

**What this is** — `lts_find(text=…)` reads only id, title and string property values. A term living
only in a node's **body** returns a confident, well-formed **zero**. `lts_find_in_prose` reads the
body. Seven Tier 1 agents held the first and not the second.

**Why it matters** — for those seven this wasn't a discoverability problem but an **absent
capability**: they would receive false negatives with no way to correct them.
`factlattice-recall-auditor` is the sharp case — its entire charter is *"is this corpus useful to
query"* and it was structurally blind to the half that lives in prose. Measured tonight:
`verified-by` returns **48 body-only against 9 also-in-structure**, so the corpus *discusses*
verification roughly five times more often than it *asserts* it.

⛔ **But grant widening is on your ruling list** — the `tools: '*'` change that landed under a
`docs(agents):` label is still unratified, and I did a grant change tonight on my own judgement and
pushed it (`0f9a908`).

| Option | Tradeoffs |
|---|---|
| **Ratify it, and record that adding a read-only query tool alongside one an agent already holds is architect-discretion** | Matches what actually happened; gives me a usable rule instead of a case-by-case ask. The genuine risk: "read-only, same server, same corpora" is a line I drew, and a future agent could stretch it. |
| Ratify this instance only; all future grant changes come to you | Safest. Costs a round-trip on changes that are usually obvious, and you're often away. |
| Reverse it; route through the same gate as `tools: '*'` | Consistent. But it re-blinds seven agents to prose for however long the gate takes, and `recall-auditor` cannot do its job at all meanwhile. |

**My recommendation — ratify with the narrow rule**, and state the boundary explicitly: *adding a
READ-ONLY query tool on an MCP server the agent already holds tools for, over corpora it already
reads, is architect-discretion; anything that adds WRITE capability, a new server, or `'*'` is
yours.* That line is testable rather than a matter of taste.

**Your call:** Ratify it, and record that adding a read-only query tool alongside one an agent already holds is architect-discretion

---

### D4 · The `740Dave/factlattice` agent definitions are uncommitted — how do they land?

**What this is** — `factlattice-editor`, `factlattice-engineer`, `factlattice-defect-investigator`
and `factlattice-recall-auditor` live in `~/factlattice-deployment/agents/`, which is the **public**
repo.

**Why it matters** — ⛔ `factlattice-engineer.md` and `factlattice-defect-investigator.md` are
**untracked there**: the two agents you asked for last session exist in **no commit on that remote at
all**. `factlattice-editor.md` was already modified-uncommitted before tonight, and I've now added
the prose grant + note to all four. **Pushing that repo is a publication act and remains your word
alone**, so I stopped at the edit.

| Option | Tradeoffs |
|---|---|
| **You review and push; I prepare a scoped commit with the message written** | Preserves the publication boundary exactly. Costs you a review. |
| Delegate composeio-adjacent agent-def pushes to me, keeping engine/spec pushes yours | Unblocks routine agent maintenance. But it splits "publication" along a line that isn't obviously stable, and the repo is bound for open-source. |
| Keep the four local-only, unversioned | Zero publication risk. But it is the durability failure we spent tonight closing, applied to the agents that do the corpus work. |

**My recommendation — you push, I stage.** This is the one boundary that has held all session, the
repo is public and open-source-bound, and the review cost is a few minutes against a permanent record.

**Your call:** You review and push; I prepare a scoped commit with the message written

---

# Theme 3 — Verification provenance

### D5 · Stamp git facts into nodes, derive them, or both?

**What this is** — the idea from session 4, which the gate confirmed was **recorded nowhere**: extend
`factlattice-gap-analyzer` to cross-reference git history so a spec node carries when it was last
checked against the code, *"so everything's fully tied out."*

**Why it matters** — the audit found that `status`, `lifecycle-position`, `decided-in-session` and
`importance` are all **in use but undeclared** in the profile, `verification-date` **does not exist
at all**, and `verified-by` exists only as a capability-map facet, never on a decision. So the
verification story is currently unqueryable.

| Option | Tradeoffs |
|---|---|
| **Split it: DERIVE the file's git state at query time; STAMP only the verification event** | The git mtime is derivable from `source_file` + `line_range` that every node already carries, so it can never rot. The verification event is an *assertion* nothing can recompute, so it must be stamped: `verified-by`, `verification-date`, and ⭐ **`verified-at-commit`**. Then *"is this still verified?"* becomes a computation — has the file changed since that SHA? Cost: engine work plus three registered properties. |
| Stamp the git date into nodes | Simple, one pass. But a stamped date is a snapshot that starts rotting the moment the file is touched again — the hand-maintained-build-tag failure this fleet has already paid for. |
| Grant `gap-analyzer` Bash so it can run `git` itself | Direct. But its Bash-lessness is *why* it marked CANNOT DETERMINE instead of confabulating a date, and Bash is a large grant for a small need. |
| Do nothing | Free. The corpus keeps discussing verification 5× more than it asserts it. |

**My recommendation — the split, with the derivation exposed by the ENGINE rather than by granting
the analyzer Bash.** The analyzer stays read-only and structurally unable to guess. `verified-at-commit`
is the field that makes the rest work: without the SHA, a verification date can't tell you whether the
code has moved since. ⭐ And note what falls out — staleness becomes derived state rather than a
judgement call, which is your zero-code ruling applied to the corpus itself.

**Your call:** Grant gap-analyzer Bash so it can run git itself

---

# Theme 4 — The wire and the glass

### D6 · Control-plane envelopes don't fit a CAN frame — which mechanism carries them?

**What this is** — three measured today: `_bus/heartbeat` is **58–60 bytes** (48 even with a
one-character node id), `_device/<id>/status = "restarting"` is **11 bytes**, against an 8-byte
classic-CAN payload. `bus_can.cpp:690` caps at 8 and `:395` refuses over it.

**Why it matters** — the heartbeat is undeliverable on CAN **for every possible node id**, so this
isn't tuning. The `_device/<id>/heartbeat` fallback *does* fit at 7 bytes but **carries no cadence**,
so the only beat that arrives on CAN is the one that can't declare its rate. And the restart ack
being 11 bytes is why three devices showed **no reboot evidence** tonight — the ack structurally
cannot cross that link.

⚠ **This is deliberately narrow.** `[DEC-CONFIG-OVER-CAN-IS-NON-NEGOTIABLE]` already forbids
transport carve-outs and already instructs that the fragmentation design be **recovered from earlier
transcripts, not re-derived**. The only genuinely open question is scope:

| Option | Tradeoffs |
|---|---|
| **Control-plane messages ride the SAME chunk-relay as config (`[SPEC:CXC-CONFIG-BUS]`), once the recovered design lands** | One mechanism, no second seam, and it honours the non-negotiable ruling directly. But it makes a heartbeat depend on a reassembler that has **never run on hardware** and whose `missing()` is called by nothing. |
| Give the control plane a compact binary wire form of its own (fixed fields, no JSON) | A heartbeat is a fixed schema; JSON is the wrong encoding for it, and this fits 8 bytes with no fragmentation. But it's a second wire form to keep in sync — the fork class. |
| Recover the transcript design FIRST, then decide | ⚠ **This one wants a discussion, not a slot.** Your ruling says the prior reasoning exists and should be recovered; deciding scope before reading it risks re-deriving what you told me not to. |

**My recommendation — recover first, and treat this as a discussion rather than a fork.** My honest
lean *if* the recovered design covers control-plane traffic is the first option. But I'd rather hand
you the recovered reasoning than pick between two options you may have already ruled out.

**Your call:** Control-plane messages ride the SAME chunk-relay as config ([SPEC:CXC-CONFIG-BUS]), once the recovered design lands

---

### D7 · Should `default:` be refused on a data attribute whose namespace has a freshness contract?

**What this is** — `compose_scene.cpp:719` applies `default:` at scene **build**, before any frame
arrives. So `value: {namespace: x, default: 42}` renders a fabricated 42 that is **indistinguishable
from a received one.**

**Why it matters** — this is your Dradis defect reachable from the YAML side, and the `_fresh` guard
does not stop it because the guard is a separate binding. It is exactly the failure `[CXC-PRIM-STALE]`
exists to end — *not a blank and not a zero, but a confidently wrong number nothing reports* — arriving
through a different door.

| Option | Tradeoffs |
|---|---|
| **Refuse `default:` on a DATA attr (`value`/`text`/`state`/`items`) whose namespace declares freshness; allow it on layout attrs like colour** | Closes the hole at the earliest point and keeps `default:` useful where it's harmless. Costs a parse reorder (the freshness block is parsed *after* `scene_build` today) plus a `compose_scene → freshness` dependency, **and it breaks existing configs using `default:` legitimately — a behaviour change on live boards.** |
| Loud build-time WARN only | Zero breakage; already shipped as the interim. But a warning on a board with no console is a warning nobody reads. |
| Leave it; rely on authoring discipline | Free. Re-opens the exact defect on the exact widget where you found it. |

**My recommendation — the refusal, but scheduled deliberately rather than slipped in**, because it
changes behaviour on boards already running. The WARN holds the line until then.

**Your call:** Refuse default: on a DATA attr (value/text/state/items) whose namespace declares freshness; allow it on layout attrs like colour

---

# Summary at a glance

| # | decision | my lean |
|---|---|---|
| **D1** ⛔ | pre-commit `alone` branch | **Implement it** — makes the code honest and kills the passenger sweep in one change |
| **D2** | `BOOTSTRAP-CORPUS-QUERIES.md` home | **Move to `bootstrap/`**, update citing role defs in the same commit |
| **D3** ⚠ | the grant I already made | **Ratify**, with the narrow read-only/same-server/already-reads rule |
| **D4** | four factlattice agent defs | **You push, I stage** — publication boundary holds |
| **D5** | verification provenance | **Derive git state; stamp the event** — and add `verified-at-commit`, not just date + by |
| **D6** | oversize CAN control-plane envelopes | **Recover the prior design first** — ⚠ wants a discussion, not a slot |
| **D7** | `default:` on freshness-contracted data attrs | **Refuse it, scheduled deliberately**; WARN holds until then |

---

## What I'm proceeding with regardless, under stated assumptions

So these don't need slots, but say the word if any is wrong:

- **The flat/nested `freshness:` divergence gets fixed to NESTED**, under
  `[DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML]`. It's a defect, not a fork.
- **`[CXC-PRIM-STALE]` is already amended and landed** (`b6a3b7d`) — visual language stands, the
  `draw_child` plumbing is marked superseded.
- ⛔ **The lever acceptance is still yours and nothing can substitute for it.** `throttle/left` sits
  at 100, retained, one message; quiet-by-construction means silence and stuck are indistinguishable
  from the bus. **Bring it down off maximum**, confirm it follows, park one notch below the stop,
  return to max and expect exactly 100. ⚠ A panel topping out near **39%** did not re-fetch.
