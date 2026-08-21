"""
SMD Indexer — cross-document reference index and reverse reference index.

SD-INDEX: Maps every bracketed ID to (page, anchor, title, first_property, doc_type, doc_id).
SD-INDEX-REVERSE: Maps every node ID to the list of pages/nodes that reference it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .parser import ParsedDocument


@dataclass
class NodeEntry:
    """Index entry for a single node definition."""
    page: str           # relative MkDocs page path (e.g., "spec/00-composeio-design-principles/")
    anchor: str         # HTML anchor slug
    title: str          # node title text
    first_property: str # first property value for tooltip
    doc_type: str       # SPEC, AMEND, SYSTEM, etc.
    doc_id: str         # document-level ID


@dataclass
class ReferenceEntry:
    """A single inbound reference to a node."""
    page: str           # page containing the reference
    node_id: str | None # enclosing node on that page (if any)
    context: str        # brief surrounding text snippet


def _slugify(text: str) -> str:
    """Convert heading text to an HTML anchor slug.

    Matches MkDocs/Material slug generation: lowercase, non-alphanumeric
    characters replaced with hyphens, consecutive hyphens collapsed.
    The input should be the full heading text as MkDocs sees it
    (e.g., "[DP-CIO-1] Adding Hardware Is Configuration, Not Recoding").
    """
    slug = text.lower()
    # MkDocs uses python-markdown's toc extension which strips certain chars
    # and converts spaces/punctuation to hyphens
    slug = re.sub(r"[^\w\s\-]", "", slug)  # Remove non-word chars except spaces/hyphens
    slug = re.sub(r"[\s]+", "-", slug)  # Spaces to hyphens
    slug = re.sub(r"-+", "-", slug)  # Collapse multiple hyphens
    slug = slug.strip("-")
    return slug


def _page_path_from_filepath(filepath: str, docs_dir: str) -> str:
    """Compute the MkDocs page path from a source file path.

    Given filepath = '/path/to/docs/spec/00-foo.smd' and docs_dir = '/path/to/docs',
    returns 'spec/00-foo/' (MkDocs directory-style URLs).
    """
    import os
    rel = os.path.relpath(filepath, docs_dir)
    # Strip extension
    base, _ = os.path.splitext(rel)
    return base + "/"


class SmdIndex:
    """Cross-document reference index for the entire SMD corpus."""

    def __init__(self) -> None:
        self.index: dict[str, NodeEntry] = {}
        self.reverse_index: dict[str, list[ReferenceEntry]] = {}
        self._definition_ids: set[str] = set()  # IDs that have a definition (node header)

    def add_document(self, doc: ParsedDocument, docs_dir: str) -> None:
        """Index all nodes from a parsed document."""
        page = _page_path_from_filepath(doc.filepath, docs_dir)

        # Determine document-level type and id
        doc_type = doc.doc_header.doc_type if doc.doc_header else ""
        doc_id = doc.doc_header.doc_id if doc.doc_header else ""

        for node_id, node in doc.nodes.items():
            # Use full heading text for anchor generation (matches MkDocs' slug)
            anchor_source = node.heading_text if node.heading_text else node.node_id
            anchor = _slugify(anchor_source)
            entry = NodeEntry(
                page=page,
                anchor=anchor,
                title=node.title,
                first_property=node.first_property,
                doc_type=node.doc_type or doc_type,
                doc_id=node.doc_id or doc_id,
            )
            # Prefer definition sites over reference sites (SD-INDEX compliance)
            if node_id not in self.index or node_id not in self._definition_ids:
                self.index[node_id] = entry
                self._definition_ids.add(node_id)

    def add_references(self, doc: ParsedDocument, docs_dir: str) -> None:
        """Build the reverse index from a parsed document's references."""
        page = _page_path_from_filepath(doc.filepath, docs_dir)

        seen = set()  # deduplicate per (ref_id, page, node_id)
        for ref_id, enclosing_node, context in doc.references:
            key = (ref_id, page, enclosing_node)
            if key in seen:
                continue
            seen.add(key)

            if ref_id not in self.reverse_index:
                self.reverse_index[ref_id] = []
            self.reverse_index[ref_id].append(ReferenceEntry(
                page=page,
                node_id=enclosing_node,
                context=context[:100],
            ))

    def resolve(self, ref_id: str) -> NodeEntry | None:
        """Look up a reference ID in the index."""
        return self.index.get(ref_id)

    def get_references_to(self, node_id: str) -> list[ReferenceEntry]:
        """Get all pages/nodes that reference a given node ID."""
        return self.reverse_index.get(node_id, [])

    def get_page_inbound_references(self, page: str) -> dict[str, list[ReferenceEntry]]:
        """Get all inbound references to nodes defined on a given page.

        Returns a dict mapping node_id -> list of ReferenceEntry, filtered
        to only nodes that are defined on the given page.
        """
        result: dict[str, list[ReferenceEntry]] = {}
        for node_id, entry in self.index.items():
            if entry.page == page:
                refs = self.get_references_to(node_id)
                # Filter out self-references from the same page
                external_refs = [r for r in refs if r.page != page]
                if external_refs:
                    result[node_id] = external_refs
        return result
