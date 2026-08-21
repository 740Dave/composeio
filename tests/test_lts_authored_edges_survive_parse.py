"""Enforcing test for DEFECT-DOMAIN-RELATIONSHIP-VERBS-DISCARDED-SILENTLY.

THE INVARIANT UNDER TEST
------------------------
Every authored relationship triple in a `## Relationships` section of a `.lts`
document MUST become an edge in the parsed graph, or MUST raise a diagnostic.
It may NEVER be silently discarded.

That invariant is a FactLattice Prime-Directive obligation (preserve-and-flag,
`[LTS-PARSER]` / SPEC §2.4: "unregistered-namespace and dangling-target edges are
PRESERVED AND FLAGGED, never dropped") and a ComposeIO convention obligation
(`[PLT-CONV-RELVERB]`, `corpora/platform-specs/99-composeio-profile.yaml`
`relationship_verbs.domain`). Today it holds for neither of the two shapes the
ComposeIO corpora actually author, and the drop is SILENT — `lts_lint` reports
0 errors on a graph missing a quarter of its authored semantics.

TIERS
-----
1. HERMETIC (`test_*_hermetic`) — synthetic 6-line corpus + synthetic profile.
   These are the minimal reproducers; they pin the CONTRACT, not the parser's
   internals, so they stay valid whichever way the parser grows the hook.
2. CORPUS (`test_no_silent_drop_in_repo_corpora`) — the real census over the
   in-repo `.lts` corpora. This is the one that would have caught the defect in
   the wild: it fails the moment ANY authored triple stops producing an edge.

The parser is an EXTERNAL, SHARED library (`~/factlattice-internal-dev`, seated via
`FACTLATTICE_ENGINE_ROOT`), so the fix for these failures lands outside this repo
and is dispatched by the architect. The test lives HERE because the corpus and
the authoring convention live here — this repo is the party that gets hurt.
"""
from __future__ import annotations

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "python"))
_ENGINE_ROOT = os.environ.get("FACTLATTICE_ENGINE_ROOT",
                              str(Path.home() / "factlattice-internal-dev"))
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

pytest.importorskip("factlattice",
                    reason="factlattice parser not importable (set FACTLATTICE_ENGINE_ROOT)")

from factlattice.corpus import parse  # noqa: E402
from factlattice.parser import CORE_EDGE_LAYER  # noqa: E402

# A profile shaped exactly like `corpora/platform-specs/99-composeio-profile.yaml`:
# domain verbs registered BARE under `relationship_verbs.domain`.
_PROFILE = {
    "domain": {"id": "TEST", "namespaces": ["t.dom"]},
    "relationship_verbs": {
        "core": ["DEPENDS_ON", "RELATED_TO"],
        "domain": ["USES", "DOCUMENTS", "UPHOLDS", "REALIZES", "EXEMPLIFIES"],
    },
}

_BULLETED = """\
# Test Doc [SPEC:T-DOC]

## [T-A] Node A

- definition: a node

## [T-B] Node B

- definition: another node

## Relationships

- [T-A] DEPENDS_ON [T-B]
- [T-A] USES [T-B]
- [T-A] DOCUMENTS [T-B]
"""

_UNBULLETED = _BULLETED.replace("\n- [T-A]", "\n[T-A]")


def _parse_doc(tmp_path: Path, text: str):
    (tmp_path / "10-test.lts").write_text(text, encoding="utf-8")
    return parse(str(tmp_path), profile=_PROFILE)


def _types(parsed) -> Counter:
    return Counter(e.type for e in parsed.edges if e.inference == "authored")


def test_core_verb_bulleted_triple_produces_an_edge_hermetic(tmp_path):
    """Control. If THIS fails the harness is wrong, not the parser."""
    got = _types(_parse_doc(tmp_path, _BULLETED))
    assert got["DEPENDS_ON"] == 1, f"authored edges: {dict(got)}"


def test_domain_verb_bulleted_triple_produces_an_edge_hermetic(tmp_path):
    """`- [A] USES [B]` with USES registered in `relationship_verbs.domain`.

    FAILS TODAY. `parser._find_edge_type` matches a BARE verb only when it is in
    `CORE_EDGE_LAYER`; `profile._registered_ns_from_raw` never reads
    `relationship_verbs` at all, so a bare domain verb has no path into the
    parser. The line is dropped with a bare `continue` and no diagnostic.
    """
    parsed = _parse_doc(tmp_path, _BULLETED)
    got = _types(parsed)
    assert got["USES"] == 1 and got["DOCUMENTS"] == 1, (
        f"profile-registered domain verbs produced no edges; authored={dict(got)}. "
        f"diagnostics={[d.__dict__ for d in parsed.diagnostics]}")


def test_unbulleted_triple_produces_an_edge_hermetic(tmp_path):
    """`[A] VERB [B]` with NO leading `- `.

    FAILS TODAY. `parser._REL_SRC_RE` is anchored `^-[ \\t]+\\[`, so an unbulleted
    triple never even reaches the verb matcher. 800 triples repo-wide are authored
    this way, 466 of them with verbs the parser fully recognises.
    """
    parsed = _parse_doc(tmp_path, _UNBULLETED)
    got = _types(parsed)
    assert got["DEPENDS_ON"] == 1, (
        f"unbulleted relationship triples produced no edges; authored={dict(got)}")


def test_unrecognised_verb_is_flagged_not_silently_dropped_hermetic(tmp_path):
    """Preserve-and-flag: a verb that is NEITHER core NOR profile-registered must
    still raise a diagnostic. Today it is dropped in total silence — which is why
    a quarter of the corpus went missing without a single lint error.
    """
    text = _BULLETED + "- [T-A] TOTALLY_UNREGISTERED_VERB [T-B]\n"
    parsed = _parse_doc(tmp_path, text)
    emitted = any(e.type == "TOTALLY_UNREGISTERED_VERB" for e in parsed.edges)
    flagged = any("TOTALLY_UNREGISTERED_VERB" in str(d.__dict__)
                  for d in parsed.diagnostics)
    assert emitted or flagged, (
        "an unregistered relationship verb was neither preserved as an edge nor "
        "flagged as a diagnostic — it was silently discarded")


# --- Tier 2: the real corpora -----------------------------------------------

_HEAD_RE = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*$")
_TRIPLE_RE = re.compile(
    r"^(?:-[ \t]+)?\[([A-Za-z0-9_.:\- ]+?)\][ \t]+"
    r"([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)?)[ \t]+\[")

_CORPORA = [str(_SPECS)]


def _authored_triples(path: Path) -> Counter:
    """Count `[A] VERB [B]` lines inside a `## Relationships` section, bulleted or not."""
    out: Counter = Counter()
    section = False
    level = 0
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        h = _HEAD_RE.match(line)
        if h:
            lvl, title = len(h.group(1)), h.group(2).lower()
            if section and lvl <= level:
                section = False
            if "relationships" in title:
                section, level = True, lvl
            continue
        if not section:
            continue
        m = _TRIPLE_RE.match(line)
        if m:
            out[m.group(2)] += 1
    return out


@pytest.mark.parametrize("corpus", _CORPORA)
def test_no_silent_drop_in_repo_corpora(corpus):
    """Census: authored triples in the TEXT vs authored edges in the GRAPH.

    FAILS TODAY (platform-specs: 79 of 299 bulleted triples dropped, 26%; plus the
    unbulleted form). Any future verb or authoring shape that stops producing an
    edge re-fails this test loudly instead of quietly shrinking the graph.
    """
    root = Path(corpus)
    # ⛔ A MISSING CORPUS IS A FAILURE, NOT A SKIP. This census exists because 79 of
    #    299 bulleted triples were being silently dropped (26%); a skip on a missing
    #    directory means the guard reports GREEN precisely when it has stopped
    #    looking. The corpora moved out of the repo on 2026-08-17 and this test
    #    would have gone quietly inert -- measured, live, during that move.
    assert root.is_dir(), (
        f"corpus home missing: {root} — this census cannot run, and a SKIP here "
        f"would be an instrument that only reports success")

    text_verbs: Counter = Counter()
    for f in sorted(root.rglob("*.lts")):
        text_verbs.update(_authored_triples(f))

    parsed = parse(str(root))
    graph_verbs = Counter(e.type for e in parsed.edges if e.inference == "authored")

    missing = {v: (c, graph_verbs.get(v, 0))
               for v, c in text_verbs.items() if graph_verbs.get(v, 0) < c}
    dropped = sum(want - got for want, got in missing.values())
    assert not missing, (
        f"{dropped} of {sum(text_verbs.values())} authored relationship triples in "
        f"{corpus} produced no edge. verb -> (authored_in_text, in_graph): {missing}. "
        f"Core verbs among them: "
        f"{sorted(v for v in missing if v in CORE_EDGE_LAYER)}. "
        f"lint diagnostics raised: {len(parsed.diagnostics)}")
