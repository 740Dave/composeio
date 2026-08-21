"""Render a derived :class:`Sheet` as a printable markdown beep-out sheet.

The reader is a human with a multimeter and two hands. So: checkbox rows, probe labels
that match the silkscreen WHERE ONE EXISTS (and say so where one does not), the expected
reading, and a blank to write the actual.

⛔ EVIDENCE SLOTS CARRY AN EXPLICIT ``EMPTY`` SENTINEL, never an absent key.
An absent key is indistinguishable from a key nobody thought to write, and that ambiguity
is the whole failure being closed. This reuses the shape ratified at
``[CXC-PERIPH-EVIDENCE-SLOTS]`` (*"meant to be copied"*) rather than inventing a parallel
convention: ``evidence-state: EMPTY`` is the queryable form and ``RECORDED`` is what a
bench session changes it to.
"""

from __future__ import annotations

from datetime import date

from .derive import Category, ProbeRow, RowState, Sheet

__all__ = ["render_markdown", "EMPTY"]

EMPTY = "⛔ EMPTY"

_BLANK = "____________"


def _hdr(sheet: Sheet) -> list[str]:
    b = sheet.board
    cats = sheet.by_category()
    states = sheet.by_state()
    L: list[str] = []
    L.append(f"# `{b.name}` — BEEP-OUT SHEET (bare board)")
    L.append("")
    L.append("> **Do this with a multimeter on a BARE, UNPOPULATED board — before any part is")
    L.append("> soldered, before any module is seated, and before power is ever applied.**")
    L.append(">")
    L.append("> Bare is the only time some of these faults are visible. Once parts are on, a")
    L.append("> **fab defect and a solder defect are indistinguishable**, and some of what is")
    L.append("> checked here (an open between two pads) reads *identically to a good board*")
    L.append("> from the MCU's side forever after.")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## Provenance — what this sheet is true of")
    L.append("")
    L.append("⛔ **Every probe row below was DERIVED FROM THE FABRICATED BOARD FILE, not from")
    L.append("`generate_pcb.py`.** A hand-typed pad pair is a *claim*; a netlist-derived one is")
    L.append("a *measurement*. That distinction is the entire value of this document.")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| derived-from | `{b.source_path}` |")
    L.append(f"| derived-from-sha256 | `{b.source_sha256}` |")
    L.append(f"| kicad-version | `{b.kicad_version}` (generator {b.generator}) |")
    L.append(f"| generated-by | `composeio.maker.beepout` |")
    L.append(f"| generated-on | {date.today().isoformat()} |")
    L.append(f"| nets / footprints / pads | {len(b.nets)} / {len(b.footprints)} / {len(b.pads)} |")
    L.append("")
    L.append("**Re-check that this sheet still describes the board in your hand:**")
    L.append("")
    L.append("```bash")
    L.append(f"shasum -a 256 {b.source_path}")
    L.append(f"# must print: {b.source_sha256}")
    L.append("```")
    L.append("")
    L.append("If it differs, the board was revised and **this sheet is stale** — regenerate it.")
    L.append("")
    L.append("### Row counts")
    L.append("")
    L.append("| category | rows |")
    L.append("|---|---|")
    for cat in Category:
        if cat.value in cats:
            L.append(f"| {cat.value} | {cats[cat.value]} |")
    L.append(f"| **total** | **{len(sheet.rows)}** |")
    L.append("")
    L.append("| derivation state | rows | meaning |")
    L.append("|---|---|---|")
    L.append(f"| `DERIVED` | {states.get('DERIVED', 0)} | follows from the netlist/geometry; "
             "mutate the source and the row changes |")
    L.append(f"| `UNVERIFIED` | {states.get('UNVERIFIED', 0)} | a REAL check the board file "
             "cannot settle — **emitted, never omitted** |")
    L.append(f"| `BLOCKED` | {states.get('BLOCKED', 0)} | cannot even be stated from this "
             "source; that itself is a finding |")
    L.append("")
    return L


def _ladder() -> list[str]:
    return [
        "## The ladder — why this sheet comes first",
        "",
        "> **Prove POWER AT THE PART, then the SUBSTRATE, then the PROTOCOL.**",
        "> — `[CXC-VISION-EPISTEMICS]` `rule-power-then-substrate-then-protocol`",
        "",
        "That rule was paid for when three rounds of protocol diagnostics ran against a",
        "transceiver that had no supply. Every round produced clean, confident, entirely",
        "uninformative output — the part could not have answered, and nothing in the",
        "diagnostics could say so.",
        "",
        "**A meter and a diagnostic firmware answer different questions and neither can",
        "answer the other's:**",
        "",
        "| | the meter (this sheet) | a diagnostic firmware |",
        "|---|---|---|",
        "| question | **what *is* this board?** | **what can the MCU *reach*?** |",
        "| power | **OFF** | ON |",
        "| finds | shorts, opens, bridges, swapped pins, wrong/reversed parts | marginal "
        "logic levels, intermittent writes, dead output drivers |",
        "| cannot see | anything intermittent or rate-dependent | **a short** (it would be "
        "destroyed by one) and **an open** (which reads identically to a good trace) |",
        "",
        "⛔ **Flashing firmware into a board with a rail-to-rail short is how you lose the",
        "MCU.** The isolation block below is first for that reason.",
        "",
        "---",
        "",
    ]


def _meter_table(sheet: Sheet) -> list[str]:
    L = [
        "## Meter functions used here",
        "",
        "Say the function out loud each time. The failure mode of a document like this is",
        "reading a resistance with the meter in DC volts and believing it.",
        "",
        "| function | usually marked | used for |",
        "|---|---|---|",
        "| **resistance, 2 MΩ** | Ω, 2M | rail-to-rail isolation. Read the **settled** value |",
        "| **continuity / beep** | ●))) | net continuity and adjacent-pad bridges |",
        "| **diode mode** | ▶⊦ | part orientation and transistor pin identification |",
        "",
    ]
    L.append("> ⛔ **THE TRAP THAT MAKES A GOOD BOARD LOOK SHORTED.** In continuity/beep mode")
    L.append("> an **uncharged 100 µF capacitor BEEPS** — the meter's test current charges it,")
    L.append("> the voltage starts near zero, and the beeper fires for a fraction of a second")
    L.append("> before going quiet. That is a *correct* reading of a *good* board.")
    L.append("> **For every rail-to-rail check, use resistance (2 MΩ), watch the number climb,")
    L.append("> and read the SETTLED value.** Above ~100 kΩ is fine, whatever it did in the")
    L.append("> first half second.")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _notes(sheet: Sheet) -> list[str]:
    if not sheet.notes:
        return []
    L = ["## Board-wide notes", ""]
    for n in sheet.notes:
        L.append(f"- {n}")
    L.append("")
    L.append("---")
    L.append("")
    return L


_SECTION_TITLE = {
    Category.RAIL_ISOLATION: (
        "§1 — RAIL ISOLATION",
        "**Power has never been applied. Nothing is seated.** Meter in **resistance, 2 MΩ**. "
        "⛔ **NOT continuity.** These are the checks whose failure destroys parts on "
        "power-up, so they are first.",
    ),
    Category.PART_ORIENTATION: (
        "§2 — PART / FOOTPRINT ASSUMPTIONS",
        "⛔ **`UNVERIFIED` BY CONSTRUCTION.** The footprint encodes an assumption about a "
        "part the board file cannot see. Check the LOOSE PART before soldering. These are "
        "emitted rather than omitted precisely because an absent check is "
        "indistinguishable from one nobody thought of.",
    ),
    Category.ADJACENT_PAD: (
        "§3 — ADJACENT-PAD ISOLATION",
        "Meter in **continuity**. Adjacency is derived from each footprint's OWN minimum pad "
        "spacing — not from a guess about which parts are fine-pitch. Every pair below "
        "carries **different nets** and must **not** beep.",
    ),
    Category.NET_CONTINUITY: (
        "§4 — NET CONTINUITY",
        "Meter in **continuity**. Each pair spans the **longest run** of its net, so the "
        "check crosses the whole net rather than a short hop. ⛔ This section is the only "
        "thing in the whole bring-up that can see an OPEN — from the MCU's side an open "
        "trace and a perfect trace are indistinguishable.",
    ),
    Category.NET_BLOCKED: (
        "§5 — NETS THAT CANNOT BE TESTED HERE",
        "⛔ **`BLOCKED` — listed, not dropped.** Each of these has fewer than two pads, so "
        "there is no second probe point. A net with one pad is either a legitimate "
        "single-ended signal or a net whose other end was never placed, and those look "
        "identical from the board file.",
    ),
}


def _rows_table(rows: list[ProbeRow], *, grouped: bool) -> list[str]:
    L: list[str] = []
    if grouped:
        by_group: dict[str, list[ProbeRow]] = {}
        for r in rows:
            by_group.setdefault(r.net_hint or "—", []).append(r)
        for group, grows in sorted(by_group.items()):
            L.append(f"#### `{group}`")
            L.append("")
            L += _plain_table(grows)
            L.append("")
        return L
    return _plain_table(rows)


def _plain_table(rows: list[ProbeRow]) -> list[str]:
    L = [
        "| ✓ | probe A | probe B | meter | EXPECT | ACTUAL |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        L.append(
            f"| ☐ | {r.from_label} | {r.to_label} | {r.meter.value} | **{r.expect}** | {_BLANK} |"
        )
    return L


def _section(sheet: Sheet, cat: Category) -> list[str]:
    rows = [r for r in sheet.ordered() if r.category == cat]
    if not rows:
        return []
    title, blurb = _SECTION_TITLE[cat]
    L = [f"## {title}", "", blurb, ""]

    grouped = cat in (Category.ADJACENT_PAD,)
    L += _rows_table(rows, grouped=grouped)
    L.append("")

    # Per-row provenance and failure guidance, kept OUT of the table so the table stays
    # printable and scannable while the reasoning remains available.
    L.append("<details><summary>Why each row exists, and what a failure means</summary>")
    L.append("")
    seen: set[str] = set()
    for r in rows:
        key = f"{r.from_label}||{r.to_label}"
        if key in seen:
            continue
        seen.add(key)
        L.append(f"- **{r.from_label} ↔ {r.to_label}** — {r.why}")
        if r.caution:
            L.append(f"  - {r.caution}")
        if r.on_fail:
            L.append(f"  - **ON FAIL:** {r.on_fail}")
    L.append("")
    L.append("</details>")
    L.append("")
    L.append("---")
    L.append("")
    return L


def _evidence(sheet: Sheet) -> list[str]:
    b = sheet.board
    L = [
        "## POST-INTEGRATION — evidence slots, ALL EMPTY",
        "",
        "⛔ **`hardware-status`: NO BOARD HAS BEEN BEEPED OUT AGAINST THIS SHEET.** Every slot",
        "below is `EMPTY` and says so. An unfilled slot carries the literal sentinel rather",
        "than an absent key, because an absent key is indistinguishable from a key nobody",
        "thought to write — and that ambiguity is the whole failure this closes. It makes",
        '*"has this ever touched a bench"* a **query**, not something a reader must already',
        "know. (`[DX-SPEC-CARRIES-ITS-EVIDENCE]`, shape from `[CXC-PERIPH-EVIDENCE-SLOTS]`.)",
        "",
        "**How to fill:** replace `evidence-state: EMPTY` with `RECORDED`, fill `observed`,",
        "`observed-on-date` and `observed-by`, and **leave the question and the expectation",
        "untouched** so the two can be compared later.",
        "",
    ]
    slots = [
        ("rail-isolation-pass",
         "Did every rail-to-rail pair in §1 settle above 100 kΩ?",
         "all pairs > 100 kΩ, read as SETTLED values on the 2 MΩ range"),
        ("net-continuity-pass",
         "Did every §4 pair beep?",
         "every net's longest-run pair beeps"),
        ("adjacent-pad-pass",
         "Did every §3 adjacent pair stay silent?",
         "no beep on any different-net neighbour"),
        ("part-orientation-confirmed",
         "Was every §2 loose part diode-checked BEFORE soldering?",
         "each part's measured pinout matches the footprint, or the deviation is recorded here"),
        ("board-serial",
         "WHICH physical board was this? (5 were fabbed; they are not interchangeable "
         "once one is reworked)",
         "a serial or a hand-written mark on the board itself"),
        ("blocked-nets-adjudicated",
         "For each §5 net, is the single pad INTENDED, or is the other end missing?",
         "a per-net ruling — intended / defect"),
    ]
    for name, question, expectation in slots:
        L.append(f"### `{name}`")
        L.append("")
        L.append(f"- question: {question}")
        L.append(f"- expectation: {expectation}")
        L.append(f"- evidence-state: EMPTY")
        L.append(f"- observed: {EMPTY}")
        L.append(f"- observed-on-date: {EMPTY}")
        L.append(f"- observed-by: {EMPTY}")
        L.append("")
    L.append("---")
    L.append("")
    return L


def _appendix(sheet: Sheet) -> list[str]:
    b = sheet.board
    pads_by_net = b.pads_by_net
    L = ["## Appendix — the net table, for probing anything not listed above", ""]
    L.append("| net | name | pads | appears at |")
    L.append("|---|---|---|---|")
    for num, name in sorted(b.nets.items()):
        if num == 0 or not name:
            continue
        pads = pads_by_net.get(num, [])
        where = ", ".join(sorted({p.label for p in pads}))
        if len(where) > 150:
            where = where[:147] + "…"
        rail = " ⚡" if num in sheet.rails else ""
        L.append(f"| {num} | `{name}`{rail} | {len(pads)} | {where or '—'} |")
    L.append("")
    return L


#: Machine-readable provenance, carried as an HTML comment so it is invisible in print and
#: exact for a validator. The human table above it is the SAME facts rendered for eyes; a
#: reader gets prose, the gate gets structure, and neither has to parse the other.
PROVENANCE_BEGIN = "<!-- BEGIN BEEPOUT-PROVENANCE (composeio.maker.beepout) -->"
PROVENANCE_END = "<!-- END BEEPOUT-PROVENANCE -->"


def _provenance_block(sheet: Sheet) -> list[str]:
    import json

    b = sheet.board
    payload = {
        "schema": "composeio.beepout/1",
        "board": b.name,
        "derived_from": str(b.source_path),
        "derived_from_sha256": b.source_sha256,
        "kicad_version": b.kicad_version,
        "rows_total": len(sheet.rows),
        "rows_by_category": sheet.by_category(),
        "rows_by_state": sheet.by_state(),
        "nets": len(b.nets),
        "footprints": len(b.footprints),
        "pads": len(b.pads),
    }
    return [PROVENANCE_BEGIN, json.dumps(payload, indent=2, sort_keys=True),
            PROVENANCE_END, ""]


def render_markdown(sheet: Sheet) -> str:
    L: list[str] = []
    L += _provenance_block(sheet)
    L += _hdr(sheet)
    L.append("---")
    L.append("")
    L += _ladder()
    L += _meter_table(sheet)
    L += _notes(sheet)
    for cat in (Category.RAIL_ISOLATION, Category.PART_ORIENTATION,
                Category.ADJACENT_PAD, Category.NET_CONTINUITY, Category.NET_BLOCKED):
        L += _section(sheet, cat)
    L += _evidence(sheet)
    L += _appendix(sheet)
    return "\n".join(L) + "\n"
