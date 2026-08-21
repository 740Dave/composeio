"""
SMD Rewriter — transforms SMD Markdown into hyperlinked, navigable MkDocs pages.

Transformations applied in on_page_markdown:
  SD-C1:  [BRACKETED-ID] in prose -> hyperlinks with relative URLs
  SD-C2:  Hover tooltips with title + first property
  SD-C3:  depends-on frontmatter -> navigation admonition
  SD-C4:  Relationship section entries -> hyperlinked
  SD-C5:  Causal graph sections -> Mermaid diagrams with clickable nodes
  SD-C9:  References in code blocks/fences NOT rewritten
  SD-C10: Unresolved references left as-is
  SD-C12: Source .smd files unchanged (we rewrite in-memory only)

Frontmatter handling:
  - scope -> page subtitle
  - boundary -> collapsed admonition
  - depends-on -> navigation links
  - Raw YAML NOT rendered
"""
from __future__ import annotations

import os
import re
from typing import Optional

from .indexer import SmdIndex, NodeEntry
from .parser import (
    RE_BRACKETED_REF,
    RE_CAUSAL_FENCED,
    RE_CAUSAL_PROSE,
    RE_FENCE_OPEN,
    RE_NODE_HEADER,
    RE_NODE_HEADER_TRAILING,
    RE_DOC_HEADER,
    RE_PROPERTY,
    RE_HEADING,
    parse_frontmatter,
    _fence_close_pattern,
)


def _relative_link(from_page: str, to_entry: NodeEntry) -> str:
    """Compute a relative URL from from_page to the target node entry."""
    # from_page: 'spec/00-composeio-design-principles/'
    # to_entry.page: 'spec/01-composeio-bus-protocol/'
    # We need a relative path from from_page to to_entry.page

    from_parts = from_page.strip("/").split("/")
    to_parts = to_entry.page.strip("/").split("/")

    # Find common prefix
    common = 0
    for a, b in zip(from_parts, to_parts):
        if a == b:
            common += 1
        else:
            break

    # Navigate up from from_page, then down to to_page
    up = len(from_parts) - common
    down = to_parts[common:]

    rel = "../" * up + "/".join(down)
    if not rel.endswith("/"):
        rel += "/"

    return rel


def _make_link(ref_id: str, entry: NodeEntry, from_page: str, is_self: bool = False) -> str:
    """Build a Markdown hyperlink for a resolved reference.

    SD-C1: link to target page/anchor
    SD-C2: tooltip with title + first property
    """
    tooltip_parts = [entry.title]
    if entry.first_property:
        tooltip_parts.append(entry.first_property)
    tooltip = " -- ".join(tooltip_parts).replace('"', "'")

    if is_self:
        # Self-reference: link to anchor only, don't navigate away
        url = f"#{entry.anchor}"
    else:
        rel = _relative_link(from_page, entry)
        url = f"{rel}#{entry.anchor}"

    return f'[{ref_id}]({url} "{tooltip}"){{.smd-ref}}'


def _make_rel_link(ref_id: str, entry: NodeEntry, from_page: str) -> str:
    """Build a hyperlink for a relationship section reference."""
    tooltip = entry.title.replace('"', "'")
    rel = _relative_link(from_page, entry)
    return f'[{ref_id}]({rel}#{entry.anchor} "{tooltip}"){{.smd-rel}}'


def rewrite_frontmatter_banner(frontmatter: dict, index: SmdIndex, from_page: str) -> str:
    """Generate the frontmatter banner (scope, boundary, depends-on) as Markdown.

    This replaces the raw YAML frontmatter in the rendered output.
    """
    parts: list[str] = []

    # Scope as subtitle
    scope = frontmatter.get("scope", "")
    if scope:
        parts.append(f'*{scope}*\n')

    # depends-on as navigation links (SD-C3)
    depends = frontmatter.get("depends-on", [])
    if depends and isinstance(depends, list) and any(depends):
        dep_links = []
        for dep in depends:
            if not isinstance(dep, str):
                continue
            # Extract bracketed IDs from the depends-on value
            ref_match = RE_BRACKETED_REF.search(dep)
            if ref_match:
                ref_id = ref_match.group(1)
                entry = index.resolve(ref_id)
                if entry:
                    dep_links.append(_make_rel_link(ref_id, entry, from_page))
                else:
                    dep_links.append(f"[{ref_id}]")
            else:
                # Try bare string as an ID (e.g. "SPEC:CIO-BUS-PROTOCOL" without brackets)
                dep_stripped = dep.strip()
                entry = index.resolve(dep_stripped)
                if entry:
                    dep_links.append(_make_rel_link(dep_stripped, entry, from_page))
                else:
                    dep_links.append(dep)

        if dep_links:
            dep_str = " | ".join(dep_links)
            parts.append(f'!!! abstract "Dependencies"\n    {dep_str}\n')

    # Boundary as collapsed admonition
    boundary = frontmatter.get("boundary", "")
    if boundary:
        parts.append(f'??? note "Boundary"\n    {boundary}\n')

    return "\n".join(parts)


def rewrite_causal_section_to_mermaid(
    lines: list[str],
    start: int,
    end: int,
    index: SmdIndex,
    from_page: str,
    prose: bool = False,
) -> list[str]:
    """Convert a causal graph block into a Mermaid diagram.

    SD-C5: Causal graph sections produce Mermaid diagrams with clickable nodes.

    When prose=False (default), start/end are the fence markers and the
    content is lines[start+1 : end].  When prose=True, start/end are the
    first and last prose causal lines (inclusive) — there are no fence
    markers to skip.
    """
    edges: list[tuple[str, str, str]] = []  # (source, edge_type, target)
    node_ids: set[str] = set()

    content_lines = lines[start : end + 1] if prose else lines[start + 1 : end]

    for line in content_lines:
        stripped = line.strip()

        # Try fenced format: EDGE_TYPE: [SOURCE] -> [TARGET]
        m = RE_CAUSAL_FENCED.match(stripped)
        if m:
            edge_type, source, target = m.group(1), m.group(2), m.group(3)
            edges.append((source, edge_type, target))
            node_ids.update([source, target])
            continue

        # Try prose format: [SOURCE] EDGE_TYPE [TARGET]
        m = RE_CAUSAL_PROSE.search(stripped)
        if m:
            source, edge_type, target = m.group(1), m.group(2), m.group(3)
            edges.append((source, edge_type, target))
            node_ids.update([source, target])

    if not edges:
        return lines[start : end + 1]  # Leave unchanged if no edges parsed

    # Build Mermaid block
    mermaid_lines = ["```mermaid", "graph LR"]

    for source, edge_type, target in edges:
        # Sanitize IDs for Mermaid (replace colons with underscores)
        src_mermaid = source.replace(":", "_")
        tgt_mermaid = target.replace(":", "_")
        mermaid_lines.append(
            f'    {src_mermaid}["{source}"] --> |{edge_type}| {tgt_mermaid}["{target}"]'
        )

    # Add click handlers for nodes with known definitions
    for nid in sorted(node_ids):
        entry = index.resolve(nid)
        if entry:
            rel = _relative_link(from_page, entry)
            url = f"{rel}#{entry.anchor}"
            mermaid_id = nid.replace(":", "_")
            mermaid_lines.append(f'    click {mermaid_id} "{url}"')

    mermaid_lines.append("```")
    return mermaid_lines


def rewrite_page_markdown(
    markdown: str,
    index: SmdIndex,
    from_page: str,
    page_defined_ids: set[str] | None = None,
    frontmatter: dict | None = None,
) -> str:
    """Rewrite a single page's Markdown with hyperlinks, tooltips, and navigation.

    This is the main entry point called from on_page_markdown.

    Args:
        markdown: The page markdown (frontmatter already stripped by MkDocs).
        index: Cross-document reference index.
        from_page: Page path for relative link computation.
        page_defined_ids: IDs defined on this page (for self-reference detection).
        frontmatter: YAML frontmatter dict (from page.meta, since MkDocs strips it).
    """
    # If frontmatter is provided externally (from page.meta), use it.
    # Otherwise try to parse from the markdown (for standalone usage).
    if frontmatter is None:
        frontmatter, body = parse_frontmatter(markdown)
    else:
        # MkDocs already stripped frontmatter; body IS the markdown
        _, body = parse_frontmatter(markdown)
        if body == markdown:
            # No frontmatter was in the text — use as-is
            body = markdown

    lines = body.split("\n")
    output_lines: list[str] = []

    # Generate frontmatter banner
    banner = rewrite_frontmatter_banner(frontmatter, index, from_page)
    if banner:
        output_lines.append(banner)

    # Collect causal graph section ranges (code fences AND bare prose blocks
    # inside ## Causal Graph sections).  We need a pre-pass to identify these.
    causal_ranges: list[tuple[int, int, str]] = []  # (start, end, kind)
    # kind = "fence" for fenced blocks, "prose" for bare causal lines
    in_causal_section = False
    in_fence = False
    fence_marker = ""
    fence_start = -1
    prose_causal_lines: list[int] = []  # accumulator for consecutive prose lines

    def _flush_prose(acc: list[int], out: list[tuple[int, int, str]]) -> None:
        if acc:
            out.append((acc[0], acc[-1], "prose"))
            acc.clear()

    for i, line in enumerate(lines):
        stripped = line.strip()

        if not in_fence:
            m = RE_FENCE_OPEN.match(stripped)
            if m:
                _flush_prose(prose_causal_lines, causal_ranges)
                in_fence = True
                fence_marker = m.group(1)
                fence_start = i
                continue

            m = RE_HEADING.match(stripped)
            if m and len(m.group(1)) == 2:
                _flush_prose(prose_causal_lines, causal_ranges)
                heading_text = m.group(2).strip().lower()
                in_causal_section = "causal graph" in heading_text or "causal" == heading_text
                continue

            # Detect bare prose causal declarations in Causal Graph sections
            if in_causal_section and RE_CAUSAL_PROSE.search(stripped):
                prose_causal_lines.append(i)
            else:
                _flush_prose(prose_causal_lines, causal_ranges)
        else:
            close_pat = _fence_close_pattern(fence_marker)
            if close_pat.match(stripped):
                if in_causal_section and fence_start >= 0:
                    causal_ranges.append((fence_start, i, "fence"))
                in_fence = False
                fence_marker = ""
                fence_start = -1

    _flush_prose(prose_causal_lines, causal_ranges)

    # Build a set of line numbers that are inside causal graph ranges
    causal_fence_lines: set[int] = set()
    for start, end, kind in causal_ranges:
        for ln in range(start, end + 1):
            causal_fence_lines.add(ln)

    # Now process lines
    in_fence = False
    fence_marker = ""
    skip_until = -1
    in_relationship_section = False

    for i, line in enumerate(lines):
        # Skip lines that were replaced by Mermaid
        if i < skip_until:
            continue

        stripped = line.strip()

        # Handle code fences
        if not in_fence:
            # Check if this line starts a causal range (fence or prose)
            if i in causal_fence_lines:
                for start, end, kind in causal_ranges:
                    if start == i:
                        mermaid = rewrite_causal_section_to_mermaid(
                            lines, start, end, index, from_page,
                            prose=(kind == "prose"),
                        )
                        output_lines.extend(mermaid)
                        skip_until = end + 1
                        break
                continue

            m = RE_FENCE_OPEN.match(stripped)
            if m:
                in_fence = True
                fence_marker = m.group(1)
                output_lines.append(line)
                continue
        else:
            close_pat = _fence_close_pattern(fence_marker)
            if close_pat.match(stripped):
                in_fence = False
                fence_marker = ""
            # SD-C9: Do NOT rewrite references inside code fences
            output_lines.append(line)
            continue

        # Track section headers
        m = RE_HEADING.match(stripped)
        if m and len(m.group(1)) == 2:
            heading_lower = m.group(2).strip().lower()
            in_relationship_section = "relationship" in heading_lower

        # Apply relationship formatting when in a Relationships section.
        # Relationship lines like "- depends-on: [FOO]" get bold keys and
        # the .smd-rel CSS class on their links.
        if in_relationship_section and stripped.startswith("- "):
            rewritten = _rewrite_relationship_line(line, index, from_page, page_defined_ids)
        else:
            rewritten = _rewrite_line_refs(line, index, from_page, page_defined_ids)
        output_lines.append(rewritten)

    return "\n".join(output_lines)


def _rewrite_line_refs(
    line: str,
    index: SmdIndex,
    from_page: str,
    page_defined_ids: set[str] | None = None,
) -> str:
    """Rewrite all [BRACKETED-ID] references in a single line.

    SD-C1: resolved refs become hyperlinks
    SD-C9: code spans (backtick-wrapped) are skipped
    SD-C10: unresolved refs left as-is
    """
    # Don't rewrite lines that are node header definitions
    stripped = line.strip()
    if (RE_NODE_HEADER.match(stripped)
            or RE_NODE_HEADER_TRAILING.match(stripped)
            or RE_DOC_HEADER.match(stripped)):
        return line

    # Split line into code spans and non-code segments
    # to avoid rewriting refs inside inline code
    segments = _split_code_spans(line)
    result_parts = []

    for is_code, segment in segments:
        if is_code:
            result_parts.append(segment)
        else:
            # Rewrite refs in non-code segments
            result_parts.append(_rewrite_segment_refs(
                segment, index, from_page, page_defined_ids
            ))

    return "".join(result_parts)


def _split_code_spans(text: str) -> list[tuple[bool, str]]:
    """Split text into (is_code, segment) pairs, handling backtick code spans."""
    result: list[tuple[bool, str]] = []
    i = 0
    while i < len(text):
        if text[i] == '`':
            # Find matching close backtick(s)
            tick_count = 0
            j = i
            while j < len(text) and text[j] == '`':
                tick_count += 1
                j += 1
            # Find closing backtick sequence
            close_pos = text.find('`' * tick_count, j)
            if close_pos >= 0:
                code_end = close_pos + tick_count
                result.append((True, text[i:code_end]))
                i = code_end
            else:
                result.append((False, text[i:]))
                break
        else:
            # Find next backtick or end of string
            next_tick = text.find('`', i)
            if next_tick >= 0:
                result.append((False, text[i:next_tick]))
                i = next_tick
            else:
                result.append((False, text[i:]))
                break
    return result


def _rewrite_segment_refs(
    segment: str,
    index: SmdIndex,
    from_page: str,
    page_defined_ids: set[str] | None = None,
) -> str:
    """Rewrite [BRACKETED-ID] references in a non-code segment of text."""

    def replace_ref(m: re.Match) -> str:
        ref_id = m.group(1)

        # Check if ref is already inside a Markdown link: [REF](url)
        # Look at what comes immediately after the ]
        end_pos = m.end()
        if end_pos < len(segment) and segment[end_pos] == '(':
            return m.group(0)  # Already a link, don't rewrite

        entry = index.resolve(ref_id)
        if entry is None:
            # SD-C10: unresolved, leave as-is
            return m.group(0)

        is_self = (page_defined_ids is not None and ref_id in page_defined_ids)
        return _make_link(ref_id, entry, from_page, is_self=is_self)

    return RE_BRACKETED_REF.sub(replace_ref, segment)


_RE_REL_KEY = re.compile(r"^(-\s+)([a-z][a-z0-9_\-]*)(\s*:\s+)")


def _rewrite_relationship_line(
    line: str,
    index: SmdIndex,
    from_page: str,
    page_defined_ids: set[str] | None = None,
) -> str:
    """Rewrite a relationship section line with bold keys and .smd-rel links.

    Input:  - depends-on: [FOO-BAR]
    Output: - **depends-on:** [FOO-BAR](../path/#anchor "title"){.smd-rel}
    """
    stripped = line.strip()

    # Bold the key portion (e.g., "depends-on" -> "**depends-on**")
    m = _RE_REL_KEY.match(stripped)
    if m:
        prefix = m.group(1)    # "- "
        key = m.group(2)       # "depends-on"
        sep = m.group(3)       # ": "
        rest = stripped[m.end():]

        # Rewrite refs in the value portion using relationship link style
        rewritten_rest = _rewrite_segment_refs_rel(rest, index, from_page, page_defined_ids)
        # Reconstruct with bold key
        indent = line[: len(line) - len(line.lstrip())]
        return f"{indent}{prefix}**{key}**{sep}{rewritten_rest}"

    # Fallback: just rewrite refs normally
    return _rewrite_line_refs(line, index, from_page, page_defined_ids)


def _rewrite_segment_refs_rel(
    segment: str,
    index: SmdIndex,
    from_page: str,
    page_defined_ids: set[str] | None = None,
) -> str:
    """Rewrite [BRACKETED-ID] refs using relationship-style links (.smd-rel class)."""

    def replace_ref(m: re.Match) -> str:
        ref_id = m.group(1)
        end_pos = m.end()
        if end_pos < len(segment) and segment[end_pos] == '(':
            return m.group(0)
        entry = index.resolve(ref_id)
        if entry is None:
            return m.group(0)
        return _make_rel_link(ref_id, entry, from_page)

    return RE_BRACKETED_REF.sub(replace_ref, segment)


def append_referenced_by(
    markdown: str,
    inbound_refs: dict[str, list],
    index: SmdIndex,
    from_page: str,
) -> str:
    """Append a 'Referenced By' section to the page markdown.

    SD-C6: Pages with inbound references get a reverse-reference table.
    """
    if not inbound_refs:
        return markdown

    lines = [
        "",
        "---",
        "",
        "## Referenced By",
        "",
        "| Node | Referenced From | Context |",
        "|------|----------------|---------|",
    ]

    for node_id, refs in sorted(inbound_refs.items()):
        entry = index.resolve(node_id)
        if entry:
            node_link = f"[{node_id}](#{entry.anchor})"
        else:
            node_link = node_id

        for ref in refs:
            # Build link to the referencing page
            ref_page_label = ref.page.strip("/").split("/")[-1]
            if ref.node_id:
                ref_entry = index.resolve(ref.node_id)
                if ref_entry:
                    ref_link = _make_rel_link(ref.node_id, ref_entry, from_page)
                else:
                    ref_link = ref.node_id
            else:
                ref_link = ref_page_label

            context = ref.context.replace("|", "\\|").replace("\n", " ")[:80]
            lines.append(f"| {node_link} | {ref_link} | {context} |")

    return markdown + "\n".join(lines)
