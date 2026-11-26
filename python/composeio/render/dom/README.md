# ComposeUX HTML/DOM render backend ([SPEC:CXC-DOM])

The browser-side realization of the `Canvas`/`.compose.yaml` contract as **native
DOM elements** plus the interactive **control → publish / form** seam. v1 renders
the **form / control / layout** subset as native DOM; rich graphics (gauges,
data-viz) are the **deferred CanvasKit path** and are explicitly out of scope
([CXC-DOM-SCOPE-SPLIT]) — an unsupported graphics widget renders as a **named
placeholder**, never silent blank space, and **no CanvasKit/WASM is ever loaded**
for a control-only scene ([CXC-DOM-REALIZATION-SPLIT]).

## Layout

```
static/                         the browser RUNTIME (dependency-free — a page loads it directly)
  dsl.js          [SPEC:CXC-DSL] JS expression evaluator (conformance-matched)
  bindings.js     [CXC-BIND] AttrBinding parse + coercion (READ half)
  dom_canvas.js   [CXC-DOM-CANVAS-SUBCLASS] the Canvas ABC subclass -> DOM elements
  controls.js     [CXC-DOM-REALIZATION-SPLIT] control renderer registry (native elements)
  form.js         [CXC-DOM-FORM-PRIMITIVE] buffer -> submit -> server-validate -> atomic publish
  ws_client.js    [CXC-DOM-TRANSPORT] WS connector client (faithful BusMessage envelope)
  runtime.js      [CXC-DOM-REUSE-REIMPLEMENT-RUNTIME] scene parse -> DOM build -> bind -> patch -> publish
validation.py     [CXC-DOM-SERVER-VALIDATION] SERVER-side pydantic Config validation reuse
server.py         GLASS demo server (WS bus relay + validation) — NOT the production transport
demo/demo.html    the verify-human glass page (form + namespace tree + live control surface)
```

## The `[DD-DOM-DSL-EVAL-REUSE]` choice (recorded per the spec's open fork)

**Chosen: candidate (b) — an independent hand-written JS evaluator gated by the
shared conformance corpus**, *not* a build-time transpile of the grammar.

Rationale: it keeps the browser runtime **dependency-free and build-step-free** —
a page loads `dsl.js` directly, with no transpiler in the publish path — which is
exactly how the **CYD C++ tier** already relates to the Python reference (an
independent interpreter gated by the same `dsl_corpus.json`). The corpus is the
**one-measurement-path proof obligation** for *both* non-Python tiers uniformly;
a transpile would instead couple the browser to a build step that must re-run on
every grammar change. The drift risk candidate (b) carries is precisely what the
corpus catches — and it now catches it for JS on **every test run** (234/234,
matched by exact IEEE-754 bits).

## Machine acceptance (green here; run with `pytest python/tests/render/dom/`)

| Row | What | Where |
|---|---|---|
| DOM-V13 | JS evaluator conformance-matches the Python golden reference | `test_dsl_conformance_js.py` (234/234, bit-exact) |
| DOM-V5 | DOM Canvas subclass element map + save/restore/clip stack | `js/run_dom_tests.js` |
| DOM-V6 | controls publish the SAME payload shape as their Python originals | `js/run_dom_tests.js` |
| DOM-V7 | publish mode selected structurally by form membership | `js/run_dom_tests.js` |
| DOM-V8 | form buffers, validates, publishes atomically; per-field errors; nested-form refusal | `js/run_dom_tests.js` |
| DOM-V9 | validation is server-side, real pydantic, `config:<field>` taxonomy | `test_server_validation.py` |
| DOM-V10 | two-way binding: bus updates a live control, not a mid-edit form field | `js/run_dom_tests.js` |
| DOM-V1 (machine slice) | controls realize as native semantic elements + labels | `js/run_dom_tests.js` |
| DOM-V2/V4 | realization split; zero CanvasKit for a control-only scene | `js/run_dom_tests.js` |
| DOM-V12 | rides WS; no `EventSource`/SSE/HTTP-POST publish side-channel | `test_transport_hygiene.py` |

## ⛔ GLASS items — machine-green here is NOT glass-green ([CXC-VISION-EPISTEMICS])

The machine tests run against a **dependency-free DOM shim with no layout
engine**. The following are **NOT proven** by them and must be verified in a real
browser (the pair glass test + David):

1. **DOM-V1 accessibility** — the axe-core audit (zero name/role/value
   violations) and the **screen-reader pass** (VoiceOver/NVDA operates every
   control by keyboard, announcing label + value). The shim asserts the semantic
   element/label structure only.
2. **DOM-V5 visual parity** — a rendered fixture compared side-by-side against
   the Skia/SVG renders. `measure_text` in the shim returns a fallback estimate
   (flagged `approximate: true`); real font metrics need a browser.
3. **DOM-V6/V8/V9 end-to-end over a real WS** — a real click seen by a live
   subscriber; a real form fill → submit → server error inline; the two-tab
   two-way-binding scenario (DOM-V10 glass).

To run the glass demo: `python -m composeio.render.dom.server`, then open the
printed `http://localhost:8900/demo/demo.html`. The demo server is a **glass
harness, not the production transport** — production rides the WS connector at
[SPEC:CXC-WS].

## ⚠ Flags / couplings (parallel builds)

- **WS control-frame vocabulary.** The `BusMessage` **envelope** is frozen by
  [SPEC:CXC-WS] and used verbatim. The lifecycle/control `op` frames
  (`select_tenant` → `bound`, `subscribe`, `validate_config` → `config_result`)
  are this backend's provisional control channel, matched by the demo server.
  Aligning them to the landed [SPEC:CXC-WS] control frames is a one-adapter
  change; the bus-envelope leg needs none.
- **Server validation reuse.** `validation.py` realizes the loader's exact
  validation call sequence (`cls.Config(**payload)` before instantiation,
  `config:<field>: <msg>` taxonomy). Once [SPEC:CXC-CS-LOADER] lands, it can call
  the loader directly; the contract is identical by construction.

## Dependencies (kept minimal + flagged)

- Runtime (`static/*.js`): **none** — deliberately dependency-free.
- Machine tests: **Node** (`node`) to run the JS harnesses; the DOM tests use an
  in-repo shim (`tests/render/dom/js/dom_shim.js`), **no jsdom / npm install**.
  Python tests **skip** (never fail) where Node is absent.
- Demo server: `websockets` + `pydantic` (both already present in the repo env).
