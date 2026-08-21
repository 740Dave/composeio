"""
SMD Traceability — generate relationship chain matrices.

SD-TRACE: Auto-generate traceability matrix pages for principle -> constraint -> anti-pattern chains.
SD-C7: Matrix page exists in nav with all cells linked.
SD-C8: Empty cells render as dash, not hidden.

Supports extensible chain types (SD-TRACE-CUSTOM):
  - principles -> constraints -> anti-patterns
  - spec -> amendment chains
  - depends-on chains
  - causal propagation paths
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .indexer import SmdIndex, NodeEntry
from .parser import ParsedDocument, CausalEdge


# ---------------------------------------------------------------------------
# Chain type configuration
# ---------------------------------------------------------------------------

@dataclass
class ChainConfig:
    """Configuration for a traceability chain type."""
    name: str                    # e.g., "Design Principles"
    slug: str                    # e.g., "traceability-principles"
    source_prefixes: list[str]   # prefixes that identify source nodes (e.g., ["DP-CIO-", "DP-CXC-", "DP-NEWS-"])
    derived_prefixes: list[str]  # prefixes for derived nodes (constraints, etc.)
    guard_prefixes: list[str]    # prefixes for guard nodes (anti-patterns, etc.)
    edge_types: list[str]        # edge types that connect source -> derived
    columns: list[str] = field(default_factory=lambda: [
        "Source", "Derived", "Guards", "Specs Referencing"
    ])


# Default chain configurations for the ComposeIO corpus
DEFAULT_CHAINS: list[ChainConfig] = [
    ChainConfig(
        name="Design Principles",
        slug="traceability-principles",
        source_prefixes=["DP-CIO-", "DP-CXC-", "DP-NEWS-", "DP-NOTIFY-"],
        derived_prefixes=[
            "BP-", "CS-", "MR-", "CXC-", "APP-", "MCP-", "NR-",
            "HB-", "SCHED-", "NEWS-", "NOTIFY-", "DASH-", "REN-",
            "CR-", "SD-",
        ],
        guard_prefixes=["AP-", "INV-"],
        edge_types=["CAUSES", "ENABLES", "IMPLEMENTS", "DERIVES_FROM"],
        columns=["Principle", "Derived Constraints", "Anti-patterns", "Specs Referencing"],
    ),
    ChainConfig(
        name="Dependencies",
        slug="traceability-dependencies",
        source_prefixes=["SPEC:", "AMEND:", "SYSTEM:"],
        derived_prefixes=["SPEC:", "AMEND:", "SYSTEM:"],
        guard_prefixes=[],
        edge_types=["DEPENDS_ON", "depends-on"],
        columns=["Document", "Depends On", "", "Referenced By"],
    ),
]


def _node_matches_prefix(node_id: str, prefixes: list[str]) -> bool:
    """Check if a node ID matches any of the given prefixes."""
    return any(node_id.startswith(p) for p in prefixes)


def _make_cell_link(node_id: str, index: SmdIndex) -> str:
    """Build a Markdown link for a traceability cell.

    Links are relative from _generated/<slug>/ so need ../../ prefix
    to reach the site root.
    """
    entry = index.resolve(node_id)
    if entry:
        return f"[{node_id}](../../{entry.page}#{entry.anchor})"
    return f"[{node_id}]"


def generate_traceability_matrix(
    chain: ChainConfig,
    documents: list[ParsedDocument],
    index: SmdIndex,
) -> str:
    """Generate a traceability matrix page for a given chain configuration.

    Returns the Markdown content for the matrix page.
    """
    # Collect source nodes
    source_nodes: list[str] = []
    for doc in documents:
        for node_id in doc.nodes:
            if _node_matches_prefix(node_id, chain.source_prefixes):
                if node_id not in source_nodes:
                    source_nodes.append(node_id)

    # Build edge maps from causal edges and property references
    source_to_derived: dict[str, set[str]] = {}
    source_to_guards: dict[str, set[str]] = {}
    source_to_refs: dict[str, set[str]] = {}  # specs referencing this source

    for doc in documents:
        # Causal edges
        for edge in doc.causal_edges:
            if edge.edge_type in chain.edge_types or edge.edge_type.lower().replace("_", "-") in chain.edge_types:
                if _node_matches_prefix(edge.source, chain.source_prefixes):
                    if _node_matches_prefix(edge.target, chain.derived_prefixes):
                        source_to_derived.setdefault(edge.source, set()).add(edge.target)
                    if chain.guard_prefixes and _node_matches_prefix(edge.target, chain.guard_prefixes):
                        source_to_guards.setdefault(edge.source, set()).add(edge.target)

                # Reverse: if target is a source and source is derived
                if _node_matches_prefix(edge.target, chain.source_prefixes):
                    if _node_matches_prefix(edge.source, chain.derived_prefixes):
                        source_to_derived.setdefault(edge.target, set()).add(edge.source)

        # Property references (derives-from, etc.)
        for ref_id, enclosing_node, context in doc.references:
            if enclosing_node and ref_id in source_nodes:
                # This node references a source node
                if _node_matches_prefix(enclosing_node, chain.derived_prefixes):
                    source_to_derived.setdefault(ref_id, set()).add(enclosing_node)
                if chain.guard_prefixes and _node_matches_prefix(enclosing_node, chain.guard_prefixes):
                    source_to_guards.setdefault(ref_id, set()).add(enclosing_node)

        # Count spec references
        doc_id = doc.doc_header.node_id if doc.doc_header else ""
        for ref_id, _, _ in doc.references:
            if ref_id in source_nodes and doc_id:
                source_to_refs.setdefault(ref_id, set()).add(doc_id)

    # Build markdown
    lines: list[str] = []
    lines.append(f"# Traceability: {chain.name}")
    lines.append("")
    lines.append(f"*Auto-generated traceability matrix. Regenerated on every build.*")
    lines.append("")

    # Column headers
    cols = chain.columns
    header = "| " + " | ".join(c for c in cols if c) + " |"
    separator = "|" + "|".join("---" for c in cols if c) + "|"
    lines.append(header)
    lines.append(separator)

    # Sort source nodes for consistent output
    source_nodes.sort()

    for src in source_nodes:
        entry = index.resolve(src)
        if entry:
            src_cell = f"[{src}](../../{entry.page}#{entry.anchor})"
        else:
            src_cell = src

        derived = source_to_derived.get(src, set())
        derived_cell = ", ".join(_make_cell_link(d, index) for d in sorted(derived)) if derived else "\u2014"

        if chain.guard_prefixes:
            guards = source_to_guards.get(src, set())
            guards_cell = ", ".join(_make_cell_link(g, index) for g in sorted(guards)) if guards else "\u2014"
        else:
            guards_cell = ""

        refs = source_to_refs.get(src, set())
        # Don't count the source's own document
        if entry:
            own_doc_id = f"{entry.doc_type}:{entry.doc_id}" if entry.doc_type and entry.doc_id else ""
            refs.discard(own_doc_id)
        refs_cell = f"{len(refs)} specs" if refs else "\u2014"

        cells = [src_cell, derived_cell]
        if chain.guard_prefixes:
            cells.append(guards_cell)
        cells.append(refs_cell)

        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Generated by mkdocs-smd plugin*")

    return "\n".join(lines)


def generate_all_matrices(
    documents: list[ParsedDocument],
    index: SmdIndex,
    output_dir: str,
    chains: list[ChainConfig] | None = None,
) -> list[tuple[str, str]]:
    """Generate all traceability matrix pages.

    Returns a list of (relative_path, content) tuples for each generated page.
    """
    if chains is None:
        chains = DEFAULT_CHAINS

    os.makedirs(output_dir, exist_ok=True)

    results: list[tuple[str, str]] = []
    for chain in chains:
        content = generate_traceability_matrix(chain, documents, index)
        filename = f"{chain.slug}.md"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w") as f:
            f.write(content)

        results.append((f"_generated/{filename}", content))

    return results
