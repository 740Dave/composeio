"""
The rendered hookup guide's "Warnings and Disclaimers" section — DERIVED.

David, 2026-08-15: *"Probably a good idea to add a 'Warnings and Disclaimers'
section to the rendered hookup guide with any salient items from the must_not
block and the 4.7k vs 10k pullups."*

⛔ IT IS GENERATED FROM THE NETLIST, NOT WRITTEN BESIDE IT.  A hand-written
warning section is a SECOND SOURCE OF TRUTH about the same wires, and the
entire reason `must_not:` and `facts:` exist is that there should be one.  A
warning that has drifted from the netlist is worse than no warning, because it
is read with the authority of the artifact it no longer describes.

Sources, both machine-readable:
  * ``hookup.must_not`` — the negative space, split into what the parser
    REFUSES a netlist for and what only a human can check.  The split is
    reproduced verbatim on the page: a statement is never dressed as a check.
  * ``hookup.facts`` — provenance ([DX-SPEC-CARRIES-ITS-EVIDENCE]).  Everything
    marked ``measure_before_build`` becomes a row in a table that names the
    ASSUMED value, where it came from, and WHAT BREAKS IF IT DIFFERS — the
    4.7 kOhm / 10 kOhm case, stated so the reader knows what to put a meter on
    before the iron is hot.
  * (advisory) the descriptor pin parity's WARNINGS, so a pad the fleet has
    never confirmed is visible on the page a builder is holding.

PLACEMENT.  Injected between markers immediately after the guide's front
matter and BEFORE the analysis — someone about to solder reads the top of a
document, not the end of it.

    python3 -m composeio.maker.hookup_guide <scene>.compose.yaml \\
        --inject docs/board-bringup/<guide>.md

Re-running is idempotent, and ``tests/maker/test_hookup_guide.py`` regenerates
the block and diffs it against the file — "derived so it cannot drift" is a
claim about a process until something compares the two.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable, List, Optional

import yaml

from ..render.engine.hookup import Hookup, MustNot, HookupFact, parse_hookup

BEGIN_MARK = "<!-- BEGIN GENERATED: warnings-and-disclaimers (composeio.maker.hookup_guide) -->"
END_MARK = "<!-- END GENERATED: warnings-and-disclaimers -->"

_PROVENANCE_LABEL = {
    "datasheet": "datasheet",
    "computed": "computed",
    "measured": "MEASURED",
    "assumed": "⚠ ASSUMED",
}


def _md_cell(text: str) -> str:
    """One table cell: no pipes, no newlines, nothing that breaks the row."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _must_not_block(entries: Iterable[MustNot], *, enforced: bool) -> List[str]:
    rows = [m for m in entries]
    if not rows:
        return []
    out: List[str] = []
    if enforced:
        out.append("### ⛔ MUST NOT — machine-checked")
        out.append("")
        out.append("The parser **refuses** a netlist that violates any of "
                   "these, so a build that got this far cannot have broken one "
                   "*on paper*. They are listed anyway, because the check "
                   "cannot see your soldering iron.")
    else:
        out.append("### ⛔ MUST NOT — stated (no machine can check these)")
        out.append("")
        out.append("Nothing in the toolchain can see a mechanical butt-joint, "
                   "a habit, or a firmware behaviour. These are carried in the "
                   "artifact and checked by **you**.")
    out.append("")
    for m in rows:
        out.append(f"- **`{m.id}`** — {m.statement}")
        out.append(f"  <br>*why:* {m.why}")
    out.append("")
    return out


def _measure_block(facts: Iterable[HookupFact]) -> List[str]:
    rows = list(facts)
    if not rows:
        return []
    out = [
        "### ⇢ MEASURE BEFORE YOU BUILD",
        "",
        "Every row below is an **assumption**, not a measurement. Put a meter "
        "on it before the iron is hot: the *if it differs* column is what the "
        "design does when the assumption is wrong.",
        "",
        "| what | assumed value | where the number came from | ⛔ if it differs |",
        "|---|---|---|---|",
    ]
    for f in rows:
        out.append("| {} | **{}** | {} | {} |".format(
            _md_cell(f.what), _md_cell(f.value),
            _md_cell(f.source or "—"), _md_cell(f.if_different or "—")))
    out.append("")
    return out


def _facts_block(facts: Iterable[HookupFact]) -> List[str]:
    rows = list(facts)
    if not rows:
        return []
    empty = [f for f in rows if f.is_empty]
    out = [
        "### Evidence slots — what is fact, what is arithmetic, what is a guess",
        "",
        f"**{len(empty)} of {len(rows)}** evidence slots below are `EMPTY` — "
        f"they have never touched a bench. An `EMPTY` slot is a record, not an "
        f"omission: design intent and measured fact read identically in prose, "
        f"and the difference is the whole verdict.",
        "",
        "*These are the rows the ARTIFACT carries (`hookup.facts:`) and a "
        "machine can therefore query. The long-form classification of every "
        "claim in this document — including the ones that are not netlist "
        "facts — is the final section of this guide.*",
        "",
        "| # | value | kind | evidence |",
        "|---|---|---|---|",
    ]
    for f in rows:
        out.append("| `{}` | {} = **{}** | {} | {} |".format(
            _md_cell(f.id), _md_cell(f.what), _md_cell(f.value),
            _PROVENANCE_LABEL.get(f.provenance, f.provenance),
            "`EMPTY`" if f.is_empty else _md_cell(f.measured)))
    out.append("")
    return out


def _parity_block(hookup: Hookup) -> List[str]:
    """Board-level pin cautions in play — advisory, never fatal to the page."""
    try:
        from ..render.engine.hookup_descriptor import check_pin_parity
        findings = [f for f in check_pin_parity(hookup)
                    if f.severity == "warning"]
    except Exception:            # descriptors are a repo artifact, not a dep
        return []
    if not findings:
        return []
    out = ["### ⚠ Board cautions this netlist touches", ""]
    for f in findings:
        out.append(f"- **{f.code}** — {f.message}")
    out.append("")
    return out


def warnings_section(hookup: Optional[Hookup]) -> str:
    """The whole section, or ``''`` when the hookup declares no negative space.

    An empty heading would read as "checked and clean", which is the one thing
    it must never say about a scene that simply never declared anything.
    """
    if hookup is None or (not hookup.must_not and not hookup.facts):
        return ""
    body: List[str] = [
        "## Warnings and Disclaimers",
        "",
        "*Generated from this project's `hookup.must_not:` and `hookup.facts:` "
        "by `composeio.maker.hookup_guide` — do not edit by hand. It is derived "
        "from the netlist so it cannot drift from the wires it warns about.*",
        "",
    ]
    body += _must_not_block(hookup.enforced_must_not, enforced=True)
    body += _must_not_block(hookup.stated_must_not, enforced=False)
    body += _measure_block(hookup.measure_before_build)
    body += _facts_block(hookup.facts)
    body += _parity_block(hookup)
    return "\n".join(body).rstrip() + "\n"


# ---------------------------------------------------------------------------
# injection
# ---------------------------------------------------------------------------

def inject_section(doc: str, section: str, *, anchor: str = "---") -> str:
    """Put *section* between the markers in *doc*, adding them if absent.

    A fresh document gets the block immediately BEFORE the first *anchor*
    line (the front-matter rule under a guide's title), because a warning at
    the bottom of a long document is a warning nobody reads.
    """
    block = f"{BEGIN_MARK}\n{section.rstrip()}\n{END_MARK}"
    if BEGIN_MARK in doc and END_MARK in doc:
        head = doc[:doc.index(BEGIN_MARK)]
        tail = doc[doc.index(END_MARK) + len(END_MARK):]
        return head + block + tail
    lines = doc.splitlines(keepends=True)
    at = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == anchor and i > 0:
            at = i
            break
    return "".join(lines[:at]) + block + "\n\n" + "".join(lines[at:])


def _load_hookup(scene: str) -> Optional[Hookup]:
    with open(scene) as fh:
        cfg = yaml.safe_load(fh)
    if not isinstance(cfg, dict) or not cfg.get("hookup"):
        return None
    return parse_hookup(cfg["hookup"])


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("scene", help="a maker .compose.yaml carrying `hookup:`")
    ap.add_argument("--inject", metavar="GUIDE.md",
                    help="write the section into this guide, between markers")
    args = ap.parse_args(argv)

    hookup = _load_hookup(args.scene)
    section = warnings_section(hookup)
    if not section:
        print(f"{args.scene}: no `must_not:` or `facts:` — nothing to warn about",
              file=sys.stderr)
        return 1
    if not args.inject:
        sys.stdout.write(section)
        return 0
    with open(args.inject) as fh:
        doc = fh.read()
    out = inject_section(doc, section)
    if out == doc:
        print(f"{args.inject}: already current")
        return 0
    with open(args.inject, "w") as fh:
        fh.write(out)
    print(f"{args.inject}: warnings section regenerated "
          f"({len(section.splitlines())} lines)")
    return 0


__all__ = ["BEGIN_MARK", "END_MARK", "warnings_section", "inject_section",
           "main"]


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
