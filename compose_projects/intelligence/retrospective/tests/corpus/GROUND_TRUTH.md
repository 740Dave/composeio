# Synthetic corpus — ground truth (the answer key)

Companion to `tests/test_corpus.py`. Every transcript in this directory is **invented**, small, and
plants exactly ONE headline signal (some carry harmless incidental fallout from the shared parser —
documented below wherever that happens, so it never reads as unexplained drift). None of it is real
transcript content — see [SPEC:CXC-RETRO] §0: this is a lab notebook, never a scorecard, and per the
task brief, ground truth for a supportive instrument, not surveillance of the synthetic "session."

For each transcript: what's planted → the exact expected DB/staged values → (where applicable) the
harvest KIND + ROUTE per `.claude/skills/harvest-signal/SKILL.md`. All counts below were verified by
actually running `inject_transcript` + `pregenerate` over each file (see `test_corpus.py`).

---

## 1. `repetitive_commands.jsonl` — capture-type 2 (repetitive-task frequency)
**Planted:** the assistant runs `Bash: npm test` 4× back-to-back, same command verbatim, to check test
flakiness.
**session_id:** `corpus-repetitive-0001` · **record_count:** 10

| Table | Expect |
|---|---|
| `repetitive_commands` | `(tool='Bash', signature='npm test', count=4)` |
| `pregenerate` | `2_repetitive_task_frequency.flagged_evidence` contains it (threshold=2, `4>=2`); `leverage_product=4` |
| `tooling_backlog_ranked` | contains the npm-test entry (only entry, so it's rank 1) |

All other tables empty for this session (no reads, no magma, no spikes, no corrections, no hedges,
no handover).

---

## 2. `token_spike.jsonl` — capture-type 7 (token-spikes)
**Planted:** a normal small assistant turn (usage 300+80=380 total), then a turn that pulls the whole
config tree into context in one pass — usage `input_tokens=28000, output_tokens=3000` = **31000
total**, which is `>= DEFAULT_SPIKE_ABS (20000)`.
**session_id:** `corpus-tokenspike-0001` · **record_count:** 4

| Table | Expect |
|---|---|
| `token_spikes` | exactly 1 row: `tokens=31000, prev_tokens=380, reason='absolute'` |
| `pregenerate` | `7_token_spikes.evidence` has the 1 row; `leverage_product=31000` (its own token count) |

Note: the delta (31000-380=30620) also clears `DEFAULT_SPIKE_DELTA (15000)`, but `reason` reports
`'absolute'` because `inject.py` checks `is_abs` first — expected, not a bug, documented so the test
doesn't second-guess it.

**Post-FIX-TOKENSPIKE-SATURATION (2026-07-22):** `usage_tokens()` now sums only `input_tokens +
output_tokens + cache_creation_input_tokens` — `cache_read_input_tokens` is deliberately EXCLUDED
(it's the cumulative resent-context total, not this turn's cost; see
`transcript_reader.usage_tokens`'s docstring and
`docs/intelligence-console/retrospective-test-findings-2026-07-22.md` §2a). This planted transcript's
two turns carry no `cache_read_input_tokens` field at all, so the 380/31000 totals above are
unaffected by the fix — this fixture predates and survives the change unmodified. (The
`fixture_transcript.jsonl`/`test_retrospective.py` fixture DOES carry a `cache_read_input_tokens`
field on its spike turn and needed its expected token count updated from 25000 to 22000 — see that
test file's inline comment.)

---

## 3. `correction.jsonl` — capture-type 3 (corrections, RAW slice only)
**Planted:** assistant proposes CSV output; user corrects with "Actually, I meant the JSON output
format, not CSV" — matches the `actually-correction` pattern
(`\bactually,? i (?:meant|want|need)\b`) and ONLY that pattern (deliberately worded to avoid also
tripping `explicit-no`/`thats-wrong`/etc., so the ground truth is unambiguous).
**session_id:** `corpus-correction-0001` · **record_count:** 4

| Table | Expect |
|---|---|
| `correction_markers` | exactly 1 row: `speaker='user', marker='actually-correction'` |
| `pregenerate` | `3_correction_markers_raw.evidence` has the 1 row, tier explicitly flagged as the "RAW slice only" (axis-clustering NOT done here) |

---

## 4. `hedging.jsonl` — capture-type 8 (hedging-markers)
**Planted:** asked for an exact deprecation date, the assistant says "I'm not sure about the exact
deprecation date here" — matches ONLY the `not-sure` pattern (worded to avoid also tripping
`no-source`/`dont-know`/`grain-of-salt`/etc.).
**session_id:** `corpus-hedging-0001` · **record_count:** 3

| Table | Expect |
|---|---|
| `hedging_markers` | exactly 1 row: `marker='not-sure'` |
| `pregenerate` | `8_hedging_markers.evidence` has the 1 row |

---

## 5. `magma_null.jsonl` — capture-type 6 (Magma query, NULL-result corpus-gap, §2a)
**Planted:** a `mcp__haven__haven_magma_find` query for `"rate-limit backoff strategy"` comes back
`{"results": []}` — the negative-space signal.
**session_id:** `corpus-magmanull-0001` · **record_count:** 4

| Table | Expect |
|---|---|
| `magma_queries` | `(tool='mcp__haven__haven_magma_find', query='rate-limit backoff strategy', call_count=1, null_count=1)` |
| `pregenerate` | shows up in `6_magma_queries.corpus_gap_candidates` with `leverage_product=1` |

**Incidental fallout (documented, not a defect):** the same magma tool_use also lands one row in
`repetitive_commands` — `(tool='mcp__haven__haven_magma_find', signature='rate-limit backoff strategy',
count=1)`. **Post-FIX-MCP-TOOL-SIGNATURE (2026-07-22):** `_tool_arg()` now has a `query`-shaped
fallback for non-Bash/Grep/Glob/file tools (see `inject.py`'s `_MCP_SIGNATURE_KEYS`), so the
signature is the query text itself, not the bare tool name (pre-fix behavior: signature fell back to
`'mcp__haven__haven_magma_find'`, the tool name). `count=1` is below the repetitive threshold (2)
either way, so it never reaches `flagged_evidence`/the tooling backlog. Noted here so the test's
exact-match assertions don't get surprised by it.

---

## 6. `handover.jsonl` — `sessions.handover_cost_pct` / `handover_events` (§4, §5a)
**Planted:** one HANDOVER_START/END pair, 6 minutes apart (`13:00:00Z` → `13:06:00Z`), stamped via the
`handover` skill's Bash-echo marker convention (matches `fixture_transcript.jsonl`'s style).
**session_id:** `corpus-handover-0001` · **record_count:** 6

The context-% trajectory is **statusline-only** ([SPEC:CXC-RETRO] §5b) — never in the JSONL — so the
test supplies it the same way `test_retrospective.py` does, via `inject_transcript(...,
context_samples=...)`:
```
context_samples = {epoch("2026-07-22T13:00:00Z"): 40.0, epoch("2026-07-22T13:06:00Z"): 47.5}
```

| Table | Expect |
|---|---|
| `handover_events` | exactly 1 row: `start_ts=epoch(13:00:00Z), end_ts=epoch(13:06:00Z), cost_pct=7.5, context_source='sample-provided'` |
| `sessions` | mirrors the (only, hence last) pair: `handover_cost_pct=7.5` |

**Incidental fallout (documented):** the two Bash `echo "HANDOVER_..."` commands each also land a
`repetitive_commands` row of `count=1` (distinct signatures — the whole echoed string, since START and
END carry different timestamps in the marker text) — both below threshold, harmless.

---

## 7. `false_binary_dsd.jsonl` — capture-type 4 (dusk-gift fodder) — TRAINING material, not deterministic
**Planted (the harvest lesson-shape):** the assistant poses a decision as a binary — "(a) hardcode in
the launcher script, or (b) never persist anything, re-enter every value by hand every session" —
where (b) is a strawman nobody would pick. The user calls it out explicitly: *"Neither — that's a false
choice... there's an obvious third [option]."* The assistant's own follow-up names the root cause:
*"I was padding out a binary that only had one live branch."*
**session_id:** `corpus-falsebinary-0001` · **record_count:** 4

**This is the file that demonstrates the deterministic/judgment boundary.** By design, none of
`inject.py`'s regex patterns fire on it:
- `correction_markers`: **0 rows.** The user's reframe ("Neither — that's a false choice... obvious
  third") does not match any `CORRECTION_PATTERNS` regex (no "no, that's", no "not what I asked", no
  "that's wrong", no "actually I meant") — it is semantically a correction but not lexically shaped like
  the six patterns `inject.py` knows.
- `hedging_markers`: **0 rows.**
- `repetitive_commands` / `magma_queries` / `token_spikes` / `handover_events`: **0 rows** (no tool
  calls in this transcript at all — pure dialogue).

So the **deterministic pass (`pregenerate.py`) stages this session as entirely empty** across every
capture-type table — which is the correct, honest deterministic answer; the signal genuinely requires
the judgment lens (capture-type 4 is explicitly listed in `pregenerate.py`'s own
`judgment_capture_types_not_staged_here`).

**Harvest lens (judgment — not asserted by pytest, no judgment pass exists in this repo yet; recorded
here as training/answer-key material per the task brief):**
- **Lesson KIND** (per `harvest-signal` SKILL.md step 3): **wisdom / judgment** — "a question posed as
  a binary where one branch is a strawman isn't a real decision; a question whose branches aren't both
  defensible isn't a decision" (the SKILL's own example phrase, verbatim-matched by design).
- **ROUTE** (step 4): **wisdom → a dusk gift** — an angle-problem for
  `docs/dusk-gifts/registration-protocol.md`: pose the same false-binary shape, withhold the answer
  behind a reason-first fold, let me.next reason through *why* (b) was never live.
- Secondary/optional KIND: the user's reframe is also **gift-fodder** under [SPEC:CXC-RETRO] capture-type
  4 directly (an exchange with lesson-shape — a falsified instinct, the instinct being "present two
  options = a fair choice").

---

## 8. `read_frequency.jsonl` — capture-type 5 (read-frequency / toolify candidate)
**Planted:** the same file, `/repo/docs/architecture.md`, is `Read` 3 times across the session
(before, mid, and after an edit pass) — the "keep re-opening the same reference" pattern.
**session_id:** `corpus-readfreq-0001` · **record_count:** 8

| Table | Expect |
|---|---|
| `file_reads` | `(path='/repo/docs/architecture.md', deliberate=1, count=3)` |
| `pregenerate` | `5_read_frequency.evidence` has the 1 row |

**Incidental fallout (documented, and arguably the interesting finding, not noise):** because `Read`'s
`_tool_arg` signature IS the file path, the same 3 calls also populate `repetitive_commands` —
`(tool='Read', signature='/repo/docs/architecture.md', count=3)` — which clears the repetitive
threshold (2) and lands in `2_repetitive_task_frequency.flagged_evidence` / the tooling backlog
(`leverage_product=3`) too. Capture-types 2 and 5 are genuinely the SAME underlying event for the `Read`
tool specifically — this transcript is the one that shows that overlap concretely, which is worth
knowing about the pipeline (not a defect to fix here — out of scope per the task's hygiene rule against
touching pipeline code — just a documented characteristic).

---

## Summary table (headline signal only)

| File | Headline capture-type | Deterministic? | Key assertion |
|---|---|---|---|
| `repetitive_commands.jsonl` | 2 repetitive-task frequency | yes | Bash `npm test` ×4 flagged |
| `token_spike.jsonl` | 7 token-spikes | yes | 1 spike, 31000 tok, `absolute` |
| `correction.jsonl` | 3 corrections (raw) | yes | 1 marker, `actually-correction` |
| `hedging.jsonl` | 8 hedging-markers | yes | 1 marker, `not-sure` |
| `magma_null.jsonl` | 6 Magma null → corpus-gap | yes | 1 query, `null_count=1` |
| `handover.jsonl` | `sessions.handover_cost_pct` | yes (given supplied context-%) | 1 pair, `cost_pct=7.5` |
| `false_binary_dsd.jsonl` | 4 dusk-gift fodder | **no — judgment only** | 0 rows everywhere (correct deterministic answer); wisdom→gift by hand-read |
| `read_frequency.jsonl` | 5 read-frequency | yes | fileA ×3 deliberate |
