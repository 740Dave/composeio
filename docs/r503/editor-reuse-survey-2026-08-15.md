# ComposeUX reuse survey — what an identity editor can compose vs. must build

**Produced** 2026-08-15 by a `composeux-render-engineer` dispatch. **Read from source, nothing was run.**
Condensed from the full survey; the decision-relevant half.

⛔ **The one thing not glass-verified:** every §1 finding is a CODE READ. No text field with a caret was seen on
screen in that session. Per `[PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]` that is a different claim, and it is
the finding everything else rests on. One command settles it:
`python3 -m composeio.render.engine.runtime python/composeio/render/examples/components/keyboard.compose.yaml`
then type into the `readout` field.

---

## 1. Text input EXISTS, and is deeper than expected — the gate question, answered YES

`python/composeio/render/widgets/text_input.py`, 47 KB, registry-registered as `type: text_input`.

Caret with blink and damage-scoped redraw · selection with shift-arrow/Home/End extension · Cmd/Ctrl A-C-X-V
via SDL clipboard · click-to-place, drag-to-extend, double-click-word · soft wrap with grow-then-scroll ·
wheel scroll with caret-follow · secret mode masking to `$6$` crypt that never copies plaintext · file-drop ·
a status line under the field.

**`[PRINCIPLE-ONE-MEASUREMENT-PATH]` holds here in code, not just in spec** — `_prefix_widths` caches glyph
advances from the single `measure_text` path, and wrap, caret, hit-test and vertical nav all read that same
array.

### What it does NOT do, and it matters for an editor

1. **No hard newlines.** Enter COMMITS. Multi-line is soft wrap only — a "notes" field cannot hold paragraphs.
2. ⛔ **Escape CLEARS the field** (`:549-553`) — it does not revert or blur. A user hitting Esc to abandon an
   edit **destroys the content**. In a data-entry tool this is data loss on a reflex keystroke.
3. **No validation, no format mask, no required-field concept.** `_set_status` renders a string; nothing
   computes one.
4. No IME. No in-field undo. Paste collapses newlines to spaces.

---

## 2. ⛔ THE BLOCKER — no data-driven collections

Nothing in the declarative layer renders N rows from a runtime list.

- `repeat:` is a **static load-time unroll** — `_coerce_count` demands an integer at expand time. There is no
  `for each item in ns('people')`.
- `dialog.options` is a **compile-time literal**; `BINDABLE_ATTRS_BY_TYPE["dialog"] = ()`. **A dropdown cannot
  be populated from bus data** — which blocks "cast this person as \<character\>", where characters are
  user-created.
- `radio_button_group.labels` and `segmented_tab_row.labels` are static.
- The DSL has **seven numeric functions** (`min max abs clamp round floor ceil`) and **no string operations at
  all** — no length, concat, substring or regex.
- Every existing list/table widget is a **code widget with a welded row schema**.

**A person list, character list, role matrix, casting table, device registry and roster preview are ALL dynamic
collections.** This gap determines the shape of the build.

### Options

| | Approach | Verdict |
|---|---|---|
| **(a)** | ONE generic schema-parameterized `record_table` code widget — `namespace` (array), `columns:` spec, `select_namespace`, filter/search. Composed from `markdown_table.py`'s measure/draw substrate + `deliverable_history.py`'s sort/filter/scroll pattern, **both already proven**. | ⭐ **LEAN.** One widget serves every list in the editor, reusable beyond it, no engine change. |
| **(b)** | A declarative `repeat_over: <namespace>` engine capability. | Right EVENTUAL answer; purer; benefits the platform. But it is dynamic tree mutation + layout invalidation + focus re-collection + damage — a platform project, not an editor task. |
| **(c)** | Per-entity bespoke widgets. | ⛔ Fastest to first pixel and how every existing list got built. It is the fork discipline forbids, six times over. **Push back if proposed.** |

---

## 3. ⛔ TWO theme systems — and the editor lives on the wrong one

- **`console_theme.py`** (~600 lines) — role-named tokens, three tables, RGBA. Governs ~35 console surfaces.
- **`style.py`** (126 lines) — flat CTk dark-blue constants, RGB 3-tuples. Governs ~14 widgets — including
  **`button`, `checkbox`, `dialog`, `command`, `coordinate`, `doc_picker`, `tab`**.
- ~12 import both. ~14 read neither and use bare literals.

**`style.py` claims "edits here re-skin every backend at once." That is false as measured** — 14 widgets read
neither module, and `dialog.py` carries a hardcoded `_ACCENT` plus two inline stroke literals.

⛔ **The consequence for the planned Colonial Fleet restyle: it is a theme edit for console widgets and a
PER-WIDGET edit for input widgets — and an editor is mostly input widgets.** Exactly inverted from what is
wanted.

**Named blockers:** `dialog.py:41,201,211` · `doc_picker.py:88,119,131` (error red, inlined 3×) ·
`coordinate.py:30-35` · `radio_button_group.py` + `segmented_tab_row.py` (the CIC amber as constructor
defaults, violating `theme.py`'s own "a builder MUST NOT hardcode the amber constants elsewhere").

**Survey's lean:** make `style.py` a **projection of `console_theme`'s role tokens** and hoist `dialog.py`'s
three literals, BEFORE the editor's widgets pick a side. `style.py` is 126 lines and every constant is already
a named role. It converts the restyle from "audit 75 files" to "edit one token table."

---

## 4. What EXISTS and should be reused

| | |
|---|---|
| **75 widget modules, 120 registry types** | Not 18 — the render-engineer profile doc is five months stale. `widget_registry.py` is runtime-queryable. |
| **`DialogWidget`** | Real modal: scrim, centered panel, **modal hit-capture**, dismissible, bound open/close, dismissal republishes so bus state stays coherent. |
| **Buttons ×3** | `button` (state engine), `emit_button` (**fires a sequence; values may embed live `{namespace, default}` bindings resolved at publish time** — so "SAVE PERSON" is expressible today with zero new code), `command` (MCP action with real idle/invoking/running/ok/fail lifecycle). |
| **`doc_picker`** | Generic over a document_store query — `collection`, `where`, `title_field`, `load_map`. **For "list all People, click one to load it into the form" this is a near-exact fit today.** |
| **Intelligence Console** | **1,963 lines of declarative YAML defining a full desktop app** — tabs, rails, live bindings, resizable window, persisted geometry. Proof that a desktop-class app here is a `.compose.yaml`, not a Python program. |
| **Visual Composer `bindpanel.py`** | ⭐ **The single most liftable idea in the codebase**: the property editor renders its fields **from engine introspection tables**, zero editor-side hardcoding. Derive the property panel from a declared schema; never hand-write form code. |
| **Composer `core.py`** | Command-dispatcher owns the model; UI never writes it directly; undo is core-level inverse steps. Lift the discipline. |
| **`config_settings.py`** | **SET-vs-CURRENT readback** — the peer publishes what it is actually running and the card shows a ✓/⚠ delta. **Directly transferable to roster-push convergence**, and it is what `[CXC-R503FI-VERIFIER-REQUIREMENT]` asks for. |
| **`decisions_tab` → `graph_viewer`** | A shipped generalize-by-hooks example: subclass overrides eight named no-ops, base render stays byte-identical. |
| **Colonial Fleet** | `bsg/widgets/cic/theme.py` `resolve_theme()` — semantic token → palette, SVG-extracted overrides win. **`[DEC-COLONIAL-OS-SVG-IS-SOURCE-OF-TRUTH]`: z-order and slots are read FROM the SVG.** `RadioButtonGroupWidget`, `SegmentedTabRowWidget`, `SevenSegmentDisplay` were already promoted out of BSG into engine core. **The restyle path is Inkscape, not Python.** |

---

## 5. The remaining gaps, ranked

2. **Validation — absent, and not expressible.** Every field in this editor is a string NAME, and the DSL has no
   string operations. Lean: **uniqueness to the service** (only it can know), **required/format in the widget**
   via a small declared rule vocabulary (a round-trip to reject an empty name is bad UX).
3. **Confirm dialog — no primitive.** ~25 lines of YAML from `Dialog` + 2 `emit_button`s + a `visible:` binding.
   Delete needs it. Should be **one composite in a shared library**, not re-authored per site. Do not let
   "dialog exists" hide this.
4. **Focus covers 7 widget types only** — `text_input`, `coordinate`, `chat_thread`, `decisions_tab`,
   `dsd_form`, `markdown_viewer`, `terminal`. ⛔ **Buttons, checkboxes, radio groups and every composite are
   NOT focusable**, so Tab moves between text fields and nothing else. A data-entry tool that cannot
   tab-type-tab-type is unusable. `FocusManager` already does the rest — this is `accepts_focus = True` plus a
   Space/Enter handler per widget. **Small change, disproportionate payoff.**
5. **Bound dropdown** — add a bindable `options` attr to `DialogWidget`; small, contained, and it fixes the
   shipped `dropdown` composite's stated limitation for free.
6. **No generic scroll container.** Every scrolling surface re-implements clamp + thumb.
7. **No R503 driver exists.** `peripherals/` has only `nfc/` (PN532) — the right structural template, wrong
   sensor. Upstream of the enrollment UI; cannot be composed from widgets.
8. **Write path mostly present.** `DocumentStoreClient` has full CRUD. Missing: the service-side handler and the
   optimistic-vs-confirmed feedback loop — `CommandWidget`'s lifecycle states are the shipped pattern, and its
   own docstring warns against "an optimistic green."

---

## 6. ⛔ Ratify-before-dispatch

The survey correctly flags that `[SPEC:CXC-R503FI]` carries three decisions marked NOT MADE that **block
buildability**: `[DD-R503FI-BY-REFERENCE-MEANS-WHAT]`, `[DD-R503FI-FINGERPRINT-STORAGE-NEW-OR-EXTENSION]`,
`[DD-R503FI-IDENTITY-VERIFICATION-METHOD]`.

Per `[PRINCIPLE-RATIFY-BEFORE-DISPATCH]` these close **before** a build is dispatched, not during. The editor
spec can be authored now; the editor BUILD is gated on them, because the editor is a client of a service whose
shape those three decide.
