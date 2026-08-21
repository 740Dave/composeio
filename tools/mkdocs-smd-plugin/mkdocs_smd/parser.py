"""
SMD Parser — regex extraction of nodes, properties, edges, and references.

Extracts five element types from SMD files:
- Document headers: `# Title [DOCTYPE:ID]`
- Node headers: `## [NODE-ID] Title` or `### [NODE-ID] Title`
- Properties: `- key: value`
- Bracketed references: `[SOME-ID]` or `[DOCTYPE:ID]`
- Causal declarations (two corpus formats):
    1. `[SOURCE] EDGE_TYPE [TARGET]` (prose in Relationships sections)
    2. `EDGE_TYPE: [SOURCE] -> [TARGET]` (code fences in Causal Graph sections)

Uses PyYAML for frontmatter extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

# Document header: # Title [DOCTYPE:ID]
RE_DOC_HEADER = re.compile(
    r"^#\s+(.+?)\s+\[([A-Z][A-Z0-9]+):([A-Z0-9_\-]+)\]\s*$"
)

# Node header format 1: ## [NODE-ID] Title  (h2-h4)
RE_NODE_HEADER = re.compile(
    r"^(#{2,4})\s+\[([A-Z][A-Z0-9_\-]+)\]\s+(.+)$"
)

# Node header format 2: ## Title [NODE-ID]  (h2-h4, trailing ID — used in ComposeIO corpus)
RE_NODE_HEADER_TRAILING = re.compile(
    r"^(#{2,4})\s+(.+?)\s+\[([A-Z][A-Z0-9_\-]+)\]\s*$"
)

# Property: - key: value
RE_PROPERTY = re.compile(
    r"^-\s+([a-z][a-z0-9_\-]*)\s*:\s+(.+)$"
)

# Bracketed reference: [SOME-ID] or [DOCTYPE:SOME-ID]
RE_BRACKETED_REF = re.compile(
    r"\[([A-Z][A-Z0-9_\-]+(?::[A-Z][A-Z0-9_\-]+)?)\]"
)

# Causal declaration format 1 (prose): [SOURCE] EDGE_TYPE [TARGET]
RE_CAUSAL_PROSE = re.compile(
    r"\[([A-Z][A-Z0-9_\-]+(?::[A-Z][A-Z0-9_\-]+)?)\]\s+"
    r"(CAUSES|ENABLES|PREVENTS|CASCADES_TO|PRECONDITION_FOR|DEPENDS_ON|"
    r"IMPLEMENTS|RELATED_TO|CONTRAST_WITH)\s+"
    r"\[([A-Z][A-Z0-9_\-]+(?::[A-Z][A-Z0-9_\-]+)?)\]"
)

# Causal declaration format 2 (code fence): EDGE_TYPE: [SOURCE] -> [TARGET]
RE_CAUSAL_FENCED = re.compile(
    r"^(CAUSES|ENABLES|PREVENTS|CASCADES_TO|PRECONDITION_FOR|DEPENDS_ON|"
    r"IMPLEMENTS|RELATED_TO|CONTRAST_WITH):\s+"
    r"\[([A-Z][A-Z0-9_\-]+(?::[A-Z][A-Z0-9_\-]+)?)\]\s+->\s+"
    r"\[([A-Z][A-Z0-9_\-]+(?::[A-Z][A-Z0-9_\-]+)?)\]"
)

# Section heading (any level)
RE_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")

# Code fence boundaries
RE_FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})")
RE_FENCE_CLOSE_CACHE: dict[str, re.Pattern] = {}


def _fence_close_pattern(marker: str) -> re.Pattern:
    if marker not in RE_FENCE_CLOSE_CACHE:
        RE_FENCE_CLOSE_CACHE[marker] = re.compile(
            r"^" + re.escape(marker[0]) + r"{" + str(len(marker)) + r",}\s*$"
        )
    return RE_FENCE_CLOSE_CACHE[marker]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class NodeInfo:
    """A parsed SMD node (document-level or section-level)."""
    node_id: str
    title: str
    level: int  # 1 for doc header, 2-4 for section nodes
    heading_text: str = ""  # full heading text as MkDocs sees it (for anchor generation)
    first_property: str = ""
    doc_type: str = ""  # DOCTYPE from [DOCTYPE:ID] if doc-level
    doc_id: str = ""  # ID from [DOCTYPE:ID] if doc-level
    children: list[str] = field(default_factory=list)
    parent_id: Optional[str] = None


@dataclass
class CausalEdge:
    """A parsed causal relationship declaration."""
    source: str
    edge_type: str
    target: str


@dataclass
class PropertyRelationship:
    """A relationship extracted from property lines (depends-on, see-also, etc.)."""
    rel_type: str
    source_node: str  # enclosing node
    target_ref: str  # bracketed ID referenced
    context: str  # parenthetical context if any


@dataclass
class ParsedDocument:
    """Complete parse result for a single SMD file."""
    filepath: str
    frontmatter: dict
    doc_header: Optional[NodeInfo] = None
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    causal_edges: list[CausalEdge] = field(default_factory=list)
    references: list[tuple[str, Optional[str], str]] = field(default_factory=list)
    # (ref_id, enclosing_node_id, surrounding_text_snippet)
    relationship_lines: list[tuple[str, str]] = field(default_factory=list)
    # (raw_line, enclosing_section — e.g. "Relationships", "Causal Graph")
    causal_graph_sections: list[tuple[int, int]] = field(default_factory=list)
    # (start_line, end_line) of code fences inside ## Causal Graph sections


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter and return (metadata_dict, remaining_text).

    Returns empty dict if no frontmatter found.
    """
    if not text.startswith("---"):
        return {}, text

    lines = text.split("\n")
    end_idx = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, text

    yaml_block = "\n".join(lines[1:end_idx])
    try:
        fm = yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError:
        fm = {}

    remaining = "\n".join(lines[end_idx + 1:])
    return fm, remaining


def parse_smd(filepath: str, text: str) -> ParsedDocument:
    """Parse an SMD file and extract all structural elements."""
    frontmatter, body = parse_frontmatter(text)
    doc = ParsedDocument(filepath=filepath, frontmatter=frontmatter)

    lines = body.split("\n")
    in_fence = False
    fence_marker = ""
    in_causal_graph_section = False
    causal_fence_start: Optional[int] = None
    current_section = ""  # name of the current h2 section
    current_node_id: Optional[str] = None
    current_node_first_prop_set = False

    # Stack for section hierarchy: [(level, node_id)]
    node_stack: list[tuple[int, str]] = []

    for line_num, line in enumerate(lines):
        stripped = line.strip()

        # Track code fences
        if not in_fence:
            m = RE_FENCE_OPEN.match(stripped)
            if m:
                in_fence = True
                fence_marker = m.group(1)
                if in_causal_graph_section:
                    causal_fence_start = line_num
                continue
        else:
            close_pat = _fence_close_pattern(fence_marker)
            if close_pat.match(stripped):
                if causal_fence_start is not None:
                    doc.causal_graph_sections.append((causal_fence_start, line_num))
                    causal_fence_start = None
                in_fence = False
                fence_marker = ""
                continue

            # Inside a code fence — extract causal declarations (fenced format)
            if in_causal_graph_section:
                m = RE_CAUSAL_FENCED.match(stripped)
                if m:
                    doc.causal_edges.append(CausalEdge(
                        source=m.group(2),
                        edge_type=m.group(1),
                        target=m.group(3),
                    ))

            # Do NOT extract refs from code fences (SD-C9)
            continue

        # --- Not inside a code fence ---

        # Document header
        m = RE_DOC_HEADER.match(stripped)
        if m:
            title, doc_type, doc_id = m.group(1), m.group(2), m.group(3)
            full_id = f"{doc_type}:{doc_id}"
            # heading_text = what MkDocs sees after the # marker
            heading_text = stripped.lstrip("#").strip()
            node = NodeInfo(
                node_id=full_id,
                title=title,
                level=1,
                heading_text=heading_text,
                doc_type=doc_type,
                doc_id=doc_id,
            )
            doc.doc_header = node
            doc.nodes[full_id] = node
            current_node_id = full_id
            current_node_first_prop_set = False
            node_stack = [(1, full_id)]
            continue

        # Node header (h2-h4 with bracketed ID)
        # Try format 1: ## [NODE-ID] Title
        m = RE_NODE_HEADER.match(stripped)
        if m:
            level = len(m.group(1))
            node_id = m.group(2)
            title = m.group(3)
        else:
            # Try format 2: ## Title [NODE-ID]  (trailing ID)
            m = RE_NODE_HEADER_TRAILING.match(stripped)
            if m:
                level = len(m.group(1))
                title = m.group(2)
                node_id = m.group(3)
            else:
                level = None
                node_id = None
                title = None

        if m and node_id:
            # heading_text = what MkDocs sees after the ## markers
            heading_text = stripped.lstrip("#").strip()
            node = NodeInfo(node_id=node_id, title=title, level=level, heading_text=heading_text)

            # Build hierarchy
            while node_stack and node_stack[-1][0] >= level:
                node_stack.pop()
            if node_stack:
                parent_id = node_stack[-1][1]
                node.parent_id = parent_id
                if parent_id in doc.nodes:
                    doc.nodes[parent_id].children.append(node_id)

            doc.nodes[node_id] = node
            node_stack.append((level, node_id))
            current_node_id = node_id
            current_node_first_prop_set = False
            continue

        # Track current h2 section name (for relationship section detection)
        m = RE_HEADING.match(stripped)
        if m:
            level = len(m.group(1))
            heading_text = m.group(2).strip()
            if level == 2:
                current_section = heading_text.lower()
                in_causal_graph_section = "causal graph" in current_section
                # Reset current node for non-node sections so properties
                # below (e.g. under ## Relationships) don't get attributed
                # to the last node before the heading.
                if not RE_NODE_HEADER.match(stripped) and not RE_NODE_HEADER_TRAILING.match(stripped):
                    current_node_id = None
                    current_node_first_prop_set = False
                    while node_stack and node_stack[-1][0] >= level:
                        node_stack.pop()
            else:
                # For non-h2 headings without a bracketed node ID, also
                # reset current_node_id to prevent property misattribution.
                if not RE_NODE_HEADER.match(stripped) and not RE_NODE_HEADER_TRAILING.match(stripped):
                    current_node_id = None
                    current_node_first_prop_set = False
            continue

        # Property extraction
        m = RE_PROPERTY.match(stripped)
        if m:
            key, value = m.group(1), m.group(2)
            if current_node_id and not current_node_first_prop_set:
                if current_node_id in doc.nodes:
                    doc.nodes[current_node_id].first_property = f"{key}: {value}"
                    current_node_first_prop_set = True

            # Extract relationship-type properties that reference other nodes
            if key in ("depends-on", "see-also", "amends", "derives-from",
                       "implements", "contrast-with", "related-to"):
                refs_in_val = RE_BRACKETED_REF.findall(value)
                for ref_id in refs_in_val:
                    doc.references.append((ref_id, current_node_id, stripped[:80]))
            continue

        # Causal declarations in prose (Relationships sections)
        m = RE_CAUSAL_PROSE.search(stripped)
        if m:
            doc.causal_edges.append(CausalEdge(
                source=m.group(1),
                edge_type=m.group(2),
                target=m.group(3),
            ))
            if "relationship" in current_section or "causal" in current_section:
                doc.relationship_lines.append((stripped, current_section))

        # Extract all bracketed references in non-fence text
        for ref_id in RE_BRACKETED_REF.findall(stripped):
            # Skip if this is a definition site (node header already captured)
            snippet = stripped[:80] if len(stripped) > 80 else stripped
            doc.references.append((ref_id, current_node_id, snippet))

        # Track relationship section lines
        if ("relationship" in current_section) and stripped.startswith("- "):
            doc.relationship_lines.append((stripped, current_section))

    return doc
