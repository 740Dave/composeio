# mkdocs-smd

MkDocs plugin for rendering SMD (Semantic Markdown) files as an internally hyperlinked documentation site.

## What it does

- **Cross-reference resolution**: Every `[BRACKETED-ID]` in prose becomes a clickable hyperlink to the node's definition
- **Hover tooltips**: Resolved references show title + first property on hover
- **Frontmatter navigation**: `depends-on` rendered as navigation links, `scope` as subtitle, `boundary` as collapsed admonition
- **Causal graph visualization**: `[A] CAUSES [B]` declarations become interactive Mermaid diagrams
- **Traceability matrices**: Auto-generated principle -> constraint -> anti-pattern chain tables
- **Referenced-by tables**: Reverse-reference index showing all inbound citations
- **Graph export**: `graph.json` emitted for d3.js visualization (phase 2)

## Installation

```bash
cd tools/mkdocs-smd-plugin
pip install -e .
```

This installs the plugin and all dependencies (MkDocs, Material theme, pymdown-extensions, PyYAML).

## Usage

From the ComposeIO root directory:

```bash
# Build the site
mkdocs build

# Serve locally with hot-reload
mkdocs serve
```

The site will be available at `http://localhost:8000`.

## Configuration

In `mkdocs.yml`:

```yaml
plugins:
  - search
  - mkdocs-smd:
      graph_json: true       # Emit graph.json (default: true)
      traceability: true     # Generate traceability matrices (default: true)
```

## Architecture

```
mkdocs_smd/
  __init__.py          # Plugin class with MkDocs hook registration
  parser.py            # Regex extraction of SMD elements
  indexer.py           # Cross-document reference index
  rewriter.py          # Markdown transformation engine
  traceability.py      # Relationship chain matrix generator
  graph_exporter.py    # graph.json emitter (phase 2 scaffold)
```

### Hook lifecycle

1. **`on_files`** (once at build start): Parse all `.smd` files, build cross-document index, generate traceability matrices, emit `graph.json`
2. **`on_page_markdown`** (per page): Rewrite Markdown with hyperlinks, tooltips, frontmatter banners, Mermaid diagrams
3. **`on_page_context`** (per page): Inject "Referenced By" tables from reverse index

## SMD Format

SMD (Semantic Markdown) is valid Markdown with semantic conventions:

- **Document headers**: `# Title [DOCTYPE:ID]`
- **Node headers**: `## [NODE-ID] Title`
- **Properties**: `- key: value`
- **References**: `[SOME-ID]` or `[DOCTYPE:ID]`
- **Causal declarations**: `[A] CAUSES [B]` or `CAUSES: [A] -> [B]`

See the [SMD v2 specification](${SMD_SPEC_HOME}/00-semantic-markdown-specification-v2.smd) for the full format reference.
