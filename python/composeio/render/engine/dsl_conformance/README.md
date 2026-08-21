# ComposeUX Expression DSL — Conformance Corpus

`dsl_corpus.json` is the **byte-contract** of [SPEC:CXC-DSL]
([CXC-DSL-CROSSPLAT]): every backend interpreter — the Python reference
(`render/engine/dsl.py`), the **C++/CYD interpreter (Pass 2b)** and the
**JS/browser interpreter (Web tier)** — ships only when it reproduces
every case **bit-for-bit**. The file is pure JSON with nothing
language-specific; a backend needs a JSON reader, an IEEE-754 double,
and this document.

Regenerate with `python/scripts/generate_dsl_corpus.py` (expected values
are computed by independent plain-Python mirrors of the pinned
semantics, never by the interpreter under test).

## File format (`composeux-dsl-conformance-v1`)

```json
{
  "format": "composeux-dsl-conformance-v1",
  "case_count": N,
  "cases": [ <case>, ... ]
}
```

A **case** is one standalone evaluation:

| field      | type   | meaning                                             |
|------------|--------|-----------------------------------------------------|
| `id`       | string | unique case id                                      |
| `group`    | string | coverage category                                   |
| `expr`     | string | the DSL source to evaluate                          |
| `ns`       | object | namespace name → raw bus value (optional, may omit) |
| `vars`     | object | `$name` → value (optional)                          |
| `dt`, `t`  | number | tick context (optional, default 0.0)                |
| `expected` | object | the required result (see below), **or**             |
| `error`    | string | `"parse"` or `"eval"` — the expr MUST be refused    |

`expected` is one of:

```json
{"type": "number", "hex": "<16 hex digits>", "approx": 14.0}
{"type": "bool",   "value": true}
{"type": "string", "value": "x"}
```

For numbers, **`hex` is authoritative**: the big-endian IEEE-754
binary64 bits of the required result (`approx` is human-readable only;
`"inf"`/`"-inf"` where JSON has no literal). A backend passes a numeric
case iff its result's bits equal `hex` exactly — including the sign of
zero (`8000000000000000` is −0.0, not +0.0).

For `error` cases the backend must REFUSE the expression (parse error
for grammar/sandbox violations, eval error for author-error evaluation)
— returning any value is a conformance failure.

## Pinned semantics the corpus enforces

* **float64 everywhere**; left-associative evaluation, **no
  reassociation** (`0.1 + 0.2 + 0.3` is `(0.1+0.2)+0.3`'s bits).
* **Guarded `/0` and `%0` → `+0.0`** (never NaN/inf; `-1/0` is +0.0).
* **`%` is the truncated remainder** (C `fmod` / JS `%`, sign of the
  dividend) — *not* Python's floored `%`. Python backends use
  `math.fmod`.
* **`round()` is half-to-even** (banker's / IEEE roundTiesToEven)
  ([CXC-DSL-C12]). C++ must not use `std::round` (half-away-from-zero);
  JS must not use `Math.round` (half-up). `round`/`floor`/`ceil`
  preserve the IEEE sign of zero (`ceil(-0.3)` → −0.0).
* **`clamp(x, lo, hi)` ≡ `min(max(x, lo), hi)`** (lo > hi resolves to hi).
* **`and`/`or` short-circuit and return the deciding operand**
  (`0 or 5` → 5.0, `'' or 'x'` → 'x'); the **ternary is lazy** (the dead
  branch never evaluates — a `/0` there must not fire, and must not warn).
* **Truthiness**: bool → itself; number → `!= 0.0` (−0.0 falsy);
  string → canon(s) not in {'off', ''} (so 'off'/'false'/'no'/'0' are
  falsy, any other non-empty string truthy).
* **Typed equality**: if BOTH sides coerce to numbers (bool → 1/0,
  numeric-looking strings parse), compare as float64; otherwise both
  sides collapse to the [CXC-BIND] canon token (case-insensitive,
  trimmed; on/true/yes/1 → `on`, off/false/no/0 → `off`; integral
  floats drop the `.0` first, so `1 == 'on'` is true). Ordering
  comparators (`< <= > >=`) require numeric operands — eval error
  otherwise.
* **`ns()` coercion**: numeric bus values (including numeric strings)
  → float64; bool → `'on'`/`'off'`; anything else → its string. An
  **unset namespace reads 0.0** (with a one-time warning in the
  engine).
* **Sandbox is a parse boundary**: only `dt`, `t`, `$name`,
  `ns('literal')`, `true`/`false`, and
  `min max abs clamp round floor ceil` exist. Attribute access,
  indexing, assignment, any other name — parse errors, exercised by the
  `sandbox` group.

## Running against a backend

Python reference: `python3 -m composeio.render.engine.dsl_conformance.runner`
(or the pytest wrapper in `tests/render/test_dsl.py`). Other backends
re-implement `runner.py`'s loop: feed each case to the interpreter,
match numbers by bits, expect refusals for `error` cases. 100% is the
gate — there is no tolerance parameter anywhere in this contract.
