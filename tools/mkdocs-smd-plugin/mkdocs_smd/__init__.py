"""
mkdocs-smd — MkDocs plugin for rendering SMD (Semantic Markdown) files.

Registers three MkDocs hooks:
  on_files:          Parse all .smd files, build cross-document index,
                     generate traceability matrices.
                     Also converts .smd File entries into documentation pages
                     so MkDocs renders them as HTML (not static media).
  on_page_markdown:  Rewrite [BRACKETED-IDs] to hyperlinks, transform
                     frontmatter, causal graphs, relationship sections,
                     and append "Referenced By" reverse-reference tables.
  on_post_build:     Emit graph.json (after site_dir is finalized).

All state is stored on the plugin instance (SD-ARCH-PLUGIN compliance).
"""
from __future__ import annotations

import logging
import os
import posixpath
from pathlib import PurePath

from mkdocs.config import config_options
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.plugins import BasePlugin
from mkdocs.structure.files import Files, File
from mkdocs.structure.pages import Page

from .parser import parse_smd, ParsedDocument
from .indexer import SmdIndex
from .rewriter import rewrite_page_markdown, append_referenced_by
from .traceability import generate_all_matrices, DEFAULT_CHAINS
from .graph_exporter import export_graph_json

log = logging.getLogger("mkdocs.plugins.mkdocs-smd")


class SmdFile(File):
    """A File subclass that tells MkDocs to treat .smd files as documentation pages.

    MkDocs only renders files as HTML if `is_documentation_page()` returns True,
    which by default only matches .md/.markdown extensions. This subclass overrides
    that check and also sets the correct dest_uri (mapping .smd -> .html).
    """

    def is_documentation_page(self) -> bool:
        return True

    @property
    def name(self) -> str:
        """File name without extension, used by MkDocs for dest_uri computation."""
        return PurePath(self.src_uri).stem

    @staticmethod
    def from_file(f: File) -> SmdFile:
        """Convert a regular File to an SmdFile with proper dest_uri."""
        # Compute the dest_uri: replace .smd with directory-style HTML
        src_uri = f.src_path.replace(os.sep, "/")
        parent = posixpath.dirname(src_uri)
        stem = PurePath(src_uri).stem

        if f.use_directory_urls and stem != "index":
            dest_uri = posixpath.join(parent, stem, "index.html")
        else:
            dest_uri = posixpath.join(parent, stem + ".html")

        smd_f = SmdFile(
            f.src_path,
            f.src_dir,
            f.dest_dir,
            f.use_directory_urls,
            dest_uri=dest_uri,
        )
        return smd_f


class SmdPlugin(BasePlugin):
    """MkDocs plugin that adds semantic navigation to SMD files."""

    config_scheme = (
        ("graph_json", config_options.Type(bool, default=True)),
        ("traceability", config_options.Type(bool, default=True)),
    )

    def __init__(self) -> None:
        super().__init__()
        self.index: SmdIndex = SmdIndex()
        self.documents: list[ParsedDocument] = []
        self._page_defined_ids: dict[str, set[str]] = {}  # page_path -> set of defined IDs
        self._docs_dir: str = ""
        self._site_dir: str = ""

    # ------------------------------------------------------------------
    # on_files: scan, parse, index, generate traceability, emit graph
    # ------------------------------------------------------------------

    def on_files(self, files: Files, *, config: MkDocsConfig) -> Files | None:
        self._docs_dir = config["docs_dir"]
        self._site_dir = config["site_dir"]
        self.index = SmdIndex()
        self.documents = []
        self._page_defined_ids = {}

        log.info("mkdocs-smd: scanning .smd files...")

        # ── .smd-only firewall ──────────────────────────────────────
        # Only three kinds of files pass through:
        #   1. .smd files  (converted to SmdFile for HTML rendering)
        #   2. _generated/*.md  (traceability matrices the plugin creates)
        #   3. Non-documentation assets (CSS, images, JS, etc.)
        # All other .md/.markdown files are dropped — no exclude_docs needed.
        new_files = Files([])
        smd_files: list[SmdFile] = []
        dropped = 0

        for f in files:
            if f.src_path.endswith(".smd"):
                smd_f = SmdFile.from_file(f)
                new_files.append(smd_f)
                smd_files.append(smd_f)
            elif f.src_path.endswith((".md", ".markdown")):
                # Allow plugin-generated traceability pages through
                if f.src_path.startswith("_generated/"):
                    new_files.append(f)
                else:
                    dropped += 1
            else:
                # Static assets (CSS, images, JS, etc.) pass through unchanged
                new_files.append(f)

        if dropped:
            log.info(f"mkdocs-smd: dropped {dropped} .md files (smd-only firewall)")

        log.info(f"mkdocs-smd: found {len(smd_files)} .smd files")

        # Parse all documents
        for f in smd_files:
            abs_path = os.path.join(self._docs_dir, f.src_path)
            try:
                with open(abs_path, "r", encoding="utf-8") as fh:
                    text = fh.read()
                doc = parse_smd(abs_path, text)
                self.documents.append(doc)
            except Exception as e:
                log.warning(f"mkdocs-smd: failed to parse {f.src_path}: {e}")

        # Build index (two passes: definitions first, then references)
        for doc in self.documents:
            self.index.add_document(doc, self._docs_dir)

        for doc in self.documents:
            self.index.add_references(doc, self._docs_dir)

        # Cache page-defined IDs for self-reference detection
        for node_id, entry in self.index.index.items():
            page = entry.page
            if page not in self._page_defined_ids:
                self._page_defined_ids[page] = set()
            self._page_defined_ids[page].add(node_id)

        log.info(f"mkdocs-smd: indexed {len(self.index.index)} nodes, "
                 f"{sum(len(v) for v in self.index.reverse_index.values())} references")

        # Generate traceability matrices
        if self.config.get("traceability", True):
            generated_dir = os.path.join(self._docs_dir, "_generated")
            results = generate_all_matrices(
                self.documents, self.index, generated_dir
            )
            log.info(f"mkdocs-smd: generated {len(results)} traceability matrices")

            # Add generated files to the file list
            for rel_path, _ in results:
                gen_file = File(
                    rel_path,
                    self._docs_dir,
                    self._site_dir,
                    use_directory_urls=config.get("use_directory_urls", True),
                )
                new_files.append(gen_file)

        return new_files

    # ------------------------------------------------------------------
    # on_post_build: emit graph.json (after site_dir is finalized)
    # ------------------------------------------------------------------

    def on_post_build(self, *, config: MkDocsConfig) -> None:
        """Emit graph.json after the build completes.

        Previously this ran in on_files, but mkdocs build --clean deletes
        site_dir after on_files, wiping out graph.json. Moving it here
        ensures the file survives.
        """
        if self.config.get("graph_json", True):
            graph_path = os.path.join(self._site_dir, "graph.json")
            os.makedirs(self._site_dir, exist_ok=True)
            export_graph_json(self.documents, self.index, graph_path)
            log.info(f"mkdocs-smd: emitted graph.json ({graph_path})")

    # ------------------------------------------------------------------
    # on_page_markdown: rewrite Markdown with hyperlinks, tooltips, etc.
    #                   and append Referenced By tables (SD-C6)
    # ------------------------------------------------------------------

    def on_page_markdown(
        self, markdown: str, *, page: Page, config: MkDocsConfig, files: Files
    ) -> str:
        if not page.file.src_path.endswith(".smd"):
            return markdown

        # Compute from_page for relative link resolution
        src = page.file.src_path
        base, _ = os.path.splitext(src)
        from_page = base + "/"

        page_ids = self._page_defined_ids.get(from_page, set())

        # MkDocs strips YAML frontmatter and stores it in page.meta before
        # calling on_page_markdown. Pass page.meta so the rewriter can
        # generate frontmatter banners (scope, boundary, depends-on).
        frontmatter = getattr(page, "meta", {}) or {}

        rewritten = rewrite_page_markdown(
            markdown, self.index, from_page, page_ids, frontmatter=frontmatter
        )

        # Append Referenced By section as Markdown (SD-C6).
        # This runs through the Markdown pipeline so it gets search-indexed
        # and themed properly, unlike the old raw HTML approach.
        inbound = self.index.get_page_inbound_references(from_page)
        if inbound:
            rewritten = append_referenced_by(
                rewritten, inbound, self.index, from_page
            )

        return rewritten
