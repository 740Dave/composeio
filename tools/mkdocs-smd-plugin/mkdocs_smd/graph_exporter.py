"""
SMD Graph Exporter — emit graph.json for d3.js visualization (phase 2 consumer).

SD-GRAPH-DATA: Emits a JSON file with all nodes and edges from the corpus.
This is a phase 1 scaffold — the d3.js interactive page is phase 2.

Node types are inferred from the ComposeIO prefix registry.
"""
from __future__ import annotations

import json
import os
from typing import Any

from .indexer import SmdIndex
from .parser import ParsedDocument


# Prefix -> semantic type mapping (from ComposeIO conventions)
PREFIX_TYPE_MAP: dict[str, str] = {
    "DP-CIO-": "principle",
    "DP-CXC-": "principle",
    "DP-NEWS-": "principle",
    "DP-NOTIFY-": "principle",
    "BP-": "protocol",
    "BP-EXT-": "protocol",
    "BP-ACK-": "protocol",
    "BP-MSG-": "protocol",
    "BP-SCOPE-": "protocol",
    "BP-TYPE-": "protocol",
    "CR-": "protocol",
    "CS-": "config",
    "CS-APP-": "config",
    "CS-LED-": "config",
    "CS-STATE-": "config",
    "MR-": "module",
    "HB-": "bridge",
    "SCHED-": "service",
    "NEWS-": "service",
    "NOTIFY-": "service",
    "APP-": "framework",
    "APP-ARCH-": "framework",
    "MCP-": "integration",
    "MCP-ARCH-": "integration",
    "NR-": "integration",
    "DASH-": "ui",
    "REN-": "ui",
    "CXC-": "composer",
    "SD-": "tooling",
    "AP-": "anti-pattern",
    "INV-": "invariant",
    "SPEC:": "document",
    "AMEND:": "amendment",
    "SYSTEM:": "system",
    "CONV-": "convention",
}


def _infer_node_type(node_id: str) -> str:
    """Infer a semantic type from the node ID prefix.

    Uses longest-prefix matching to handle overlapping prefixes
    (e.g., BP-MSG- vs BP-).
    """
    best_match = "unknown"
    best_len = 0
    for prefix, ntype in PREFIX_TYPE_MAP.items():
        if node_id.startswith(prefix) and len(prefix) > best_len:
            best_match = ntype
            best_len = len(prefix)
    return best_match


def export_graph_json(
    documents: list[ParsedDocument],
    index: SmdIndex,
    output_path: str,
) -> None:
    """Export the corpus graph as graph.json.

    SD-GRAPH-DATA compliance:
    - Nodes include id, title, doc, and type
    - Edges include source, target, and type
    - graph.json is emitted alongside the site build
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_nodes: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()

    for doc in documents:
        doc_name = os.path.splitext(os.path.basename(doc.filepath))[0]

        # Add nodes
        for node_id, node_info in doc.nodes.items():
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            nodes.append({
                "id": node_id,
                "title": node_info.title,
                "doc": doc_name,
                "type": _infer_node_type(node_id),
            })

        # Add edges from causal declarations
        for edge in doc.causal_edges:
            edge_key = (edge.source, edge.target, edge.edge_type)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append({
                "source": edge.source,
                "target": edge.target,
                "type": edge.edge_type,
            })

        # Add edges from depends-on frontmatter
        if doc.doc_header and doc.frontmatter.get("depends-on"):
            deps = doc.frontmatter["depends-on"]
            if isinstance(deps, list):
                from .parser import RE_BRACKETED_REF
                for dep in deps:
                    if isinstance(dep, str):
                        for ref_id in RE_BRACKETED_REF.findall(dep):
                            edge_key = (doc.doc_header.node_id, ref_id, "depends-on")
                            if edge_key not in seen_edges:
                                seen_edges.add(edge_key)
                                edges.append({
                                    "source": doc.doc_header.node_id,
                                    "target": ref_id,
                                    "type": "depends-on",
                                })

        # Add CONTAINS edges from section hierarchy
        for node_id, node_info in doc.nodes.items():
            if node_info.parent_id:
                edge_key = (node_info.parent_id, node_id, "CONTAINS")
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source": node_info.parent_id,
                        "target": node_id,
                        "type": "CONTAINS",
                    })

    graph = {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "generator": "mkdocs-smd",
            "version": "0.1.0",
            "node_count": len(nodes),
            "edge_count": len(edges),
        },
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(graph, f, indent=2)
