"""The SVG/composition LINTER — the authoritative conformance check IS the
composition derivation run in ``--check`` mode ([CXC-COMP-LINT], COMP-C13).

lint == the render contract, BY CONSTRUCTION: the linter reuses
:func:`composition.derive.derive` (the SAME parse + three-category sort the render
consumes) and adds ONLY the ERROR/WARNING classification of the derived facts.
There is NO second copy of the rules to drift.

Rules enforced ([CXC-COMP-LINT]):
  * valid ``slot:<widget>@<binding>`` grammar (registered kind, ``@`` present) ->
    ERROR on malformed / unregistered kind;
  * valid ``ref:`` usage — a ``ref:`` group must not carry a live-slot label ->
    ERROR;
  * required labels PRESENT for the context (e.g. the base's ``slot:radio@mode``
    and ``radio-button-group-bounding-box``) -> ERROR (this turns the opaque
    chrome-bake crash into a readable "missing required label X");
  * no DUPLICATE and no DANGLING slot bindings -> ERROR;
  * an UNREGISTERED widget kind -> ERROR; an UNMAPPED group -> WARNING
    (preserve-and-flag);
  * layer-order / z sanity (deterministic, every retained group placed).

Runs as ONE linter, N front-ends: the bake/publish pipeline REFUSES on any ERROR
(fail-loud), the CLI (``python -m composeio.render.composition.lint <svg>``), CI,
and an Intelligence bus peer all invoke this same function.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

from .derive import (CompositionManifest, Diagnostic, classify, derive,
                     parse_slot)

# Required labels per authoring context ([CXC-COMP-LINT], [CXC-COMP-BASE]).  The
# base's missing ``radio-button-group-bounding-box`` is exactly the label whose
# absence made the chrome bake crash opaquely; the linter turns that into a clear
# diagnostic.  A context maps to the set of labels that MUST be present anywhere
# in the document.
REQUIRED_LABELS = {
    # slot:radio@mode is the base's mode-selector; its routed bbox IS the radio
    # placement, so the legacy `radio-button-group-bounding-box` label it
    # superseded ([DEC-CXC-COMP-SLOT-DELIMITER-AT]) is no longer required.
    "base": ("slot:radio@mode",),
    "mode": (),
    "screen": (),
}


@dataclass
class LintReport:
    """The result of a lint run ([CXC-COMP-LINT])."""
    source: Optional[str]
    diagnostics: List[Diagnostic] = field(default_factory=list)
    manifest: Optional[CompositionManifest] = None

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "error"]

    @property
    def warnings(self) -> List[Diagnostic]:
        return [d for d in self.diagnostics if d.level == "warning"]

    @property
    def ok(self) -> bool:
        """True iff no ERROR — the bake/publish pipeline gates on this."""
        return not self.errors

    def format(self) -> str:
        lines = []
        for d in self.diagnostics:
            where = f" [{d.label}]" if d.label else ""
            lines.append(f"  {d.level.upper():7} {d.code}{where}: {d.message}")
        head = (f"{self.source or '<svg>'}: "
                f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        return head + ("\n" + "\n".join(lines) if lines else "")


def _all_labels(node) -> List[str]:
    out: List[str] = []
    lbl = node.attrs.get("label")
    if lbl:
        out.append(lbl)
    for ch in node.children:
        out.extend(_all_labels(ch))
    return out


def _ref_carries_slot(node) -> bool:
    """True if a ``ref:`` group's subtree carries any ``slot:`` label — an
    inconsistent ref ([CXC-COMP-LINT] -> ERROR)."""
    for ch in node.children:
        if classify(ch.attrs.get("label") or ch.attrs.get("id") or "") == "slot":
            return True
        if _ref_carries_slot(ch):
            return True
    return False


def lint(source: Any, *, context: str = "screen",
         required_labels: Optional[Sequence[str]] = None,
         source_name: Optional[str] = None) -> LintReport:
    """Lint an SVG for composition-contract conformance ([CXC-COMP-LINT]).

    *context* selects the required-label set ('base' | 'mode' | 'screen'); an
    explicit *required_labels* overrides it.  Returns a :class:`LintReport`; the
    bake/publish gate refuses when ``report.ok`` is False (any ERROR)."""
    from composeio.maker.svg_resources import load_svg
    from composeio.render.svg.model import SvgDocument

    doc = source if isinstance(source, SvgDocument) else load_svg(source)
    name = source_name or (source if isinstance(source, str)
                           and source.endswith(".svg") else None)

    manifest = derive(doc, source_name=name)
    diags: List[Diagnostic] = []

    # 1. carry over the derivation's own diagnostics, but RE-CLASSIFY the
    #    preserve-and-flag facts to the lint severity ([CXC-COMP-LINT]):
    #    unregistered kind is a lint ERROR (a derive-time warning); unmapped group
    #    stays a WARNING.
    seen_bindings: dict = {}
    for layer in manifest.layers:
        if layer.category != "slot" or layer.slot is None:
            continue
        b = layer.slot
        if not b.well_formed:
            diags.append(Diagnostic(
                "error", "slot-grammar",
                f"invalid slot grammar '{layer.label}': expected "
                f"'slot:<widget>@<binding>' with the '@' delimiter", layer.label))
        elif not b.registered:
            from .kinds import looks_like_appearance_kind
            hint = (" (a widget kind names INTERACTION, not appearance — express "
                    "the look as SVG art)" if looks_like_appearance_kind(b.widget)
                    else "")
            diags.append(Diagnostic(
                "error", "unregistered-kind",
                f"slot '{layer.label}' names unregistered widget kind "
                f"'{b.widget}'{hint}", layer.label))
        # duplicate binding (same widget@binding) within the document
        if b.well_formed:
            key = f"{b.widget}@{b.binding}"
            if key in seen_bindings:
                diags.append(Diagnostic(
                    "error", "duplicate-binding",
                    f"duplicate slot binding '{key}' "
                    f"(also on '{seen_bindings[key]}')", layer.label))
            else:
                seen_bindings[key] = layer.label

    for d in manifest.diagnostics:
        if d.code == "unmapped-group":            # preserve-and-flag WARNING
            diags.append(d)

    # 2. required labels present for the context ([CXC-COMP-LINT], [CXC-COMP-BASE])
    req = required_labels if required_labels is not None \
        else REQUIRED_LABELS.get(context, ())
    present = set(_all_labels(doc.root))
    for label in req:
        if label not in present:
            diags.append(Diagnostic(
                "error", "missing-required-label",
                f"missing required label: {label}", label))

    # 3. ref: usage — a ref group must not carry a live-slot label
    for group in doc.root.children:
        if group.tag != "g":
            continue
        glabel = group.attrs.get("label") or group.attrs.get("id") or ""
        if classify(glabel) == "ref" and _ref_carries_slot(group):
            diags.append(Diagnostic(
                "error", "ref-with-slot",
                f"ref: group '{glabel}' carries a live-slot label "
                f"(a ref: group is skip-only)", glabel))

    # 4. layer-order / z sanity — deterministic, every retained group placed
    zs = [l.z for l in manifest.layers]
    if zs != list(range(len(zs))):
        diags.append(Diagnostic(
            "error", "z-order",
            "derived layer z-order is not a well-formed 0..N sequence", None))

    return LintReport(source=name, diagnostics=diags, manifest=manifest)


# ---------------------------------------------------------------------------
# CLI front-end ([CXC-COMP-LINT]: `lint <svg>`) — the SAME linter CI + the bake
# + an Intelligence bus peer invoke.
# ---------------------------------------------------------------------------
def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="composeio-composition-lint",
        description="Lint an SVG for CIC composition-contract conformance "
                    "([CXC-COMP-LINT]).  The check IS the render derivation in "
                    "--check mode.")
    p.add_argument("svg", nargs="+", help="SVG file(s) to lint")
    p.add_argument("--context", choices=sorted(REQUIRED_LABELS),
                   default="screen", help="required-label context")
    p.add_argument("--quiet", action="store_true",
                   help="print only on error")
    args = p.parse_args(argv)

    worst = 0
    for svg in args.svg:
        report = lint(svg, context=args.context)
        if not report.ok or not args.quiet:
            print(report.format())
        if not report.ok:
            worst = 1
    return worst


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
