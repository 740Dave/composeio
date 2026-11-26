"""Content-first print pipeline for the HP backend.

Content types -> a document + an IPP document-format:
  text      -> a minimal, zero-dependency PDF (Courier). IPP-Everywhere makes
               application/pdf mandatory and text/plain OPTIONAL, so rendering a
               trivial PDF is the robust driverless path — and it keeps the
               text path working with NO third-party dep, per the contract's
               "text+pdf must always work".
  pdf       -> passthrough (base64-decode).
  markdown  -> HTML -> PDF via weasyprint. If weasyprint is absent: DEGRADE
               LOUDLY (ContentError) — never crash the service.
  html/svg  -> PDF via weasyprint (same degrade rule).
"""

from __future__ import annotations

import base64
import html as html_mod
import logging
import re

logger = logging.getLogger("print_services.content")

try:
    import weasyprint  # noqa: F401
    HAS_WEASYPRINT = True
except Exception:  # noqa: BLE001 — import can fail on missing native libs too
    HAS_WEASYPRINT = False

PDF_MIME = "application/pdf"


class ContentError(Exception):
    """Content could not be rendered (e.g. an optional renderer is absent)."""


# --- minimal text -> PDF (zero deps) ----------------------------------------

_LINES_PER_PAGE = 58
_CHARS_PER_LINE = 95   # Courier 9pt across a Letter page with 0.5in margins


def _wrap(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\t", "    ").split("\n"):
        if raw == "":
            lines.append("")
            continue
        while len(raw) > _CHARS_PER_LINE:
            lines.append(raw[:_CHARS_PER_LINE])
            raw = raw[_CHARS_PER_LINE:]
        lines.append(raw)
    return lines


def _pdf_escape(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def text_to_pdf(text: str) -> tuple[bytes, int]:
    """Render plain text to a minimal multi-page PDF. Returns (bytes, page_count)."""
    lines = _wrap(text)
    pages = [lines[i:i + _LINES_PER_PAGE]
             for i in range(0, max(len(lines), 1), _LINES_PER_PAGE)] or [[""]]

    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)  # 1-based object number

    # Reserve: 1=Catalog, 2=Pages, then per page a Page + Contents; 1 shared font.
    font_num_placeholder = None
    page_obj_nums: list[int] = []
    content_streams: list[bytes] = []

    for page_lines in pages:
        parts = ["BT", "/F1 9 Tf", "12 TL", "36 756 Td"]
        for ln in page_lines:
            parts.append(f"({_pdf_escape(ln)}) Tj")
            parts.append("T*")
        parts.append("ET")
        content_streams.append("\n".join(parts).encode("latin-1", "replace"))

    # Build objects with real numbering.
    catalog_num = add(b"")   # 1
    pages_num = add(b"")     # 2
    font_num = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    for cs in content_streams:
        content_num = add(
            b"<< /Length " + str(len(cs)).encode() + b" >>\nstream\n" + cs + b"\nendstream")
        page_num = add(
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 " + str(font_num).encode() + b" 0 R >> >> "
            b"/Contents " + str(content_num).encode() + b" 0 R >>")
        page_obj_nums.append(page_num)

    kids = b" ".join(f"{n} 0 R".encode() for n in page_obj_nums)
    objects[catalog_num - 1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[pages_num - 1] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(len(page_obj_nums)).encode() + b" >>")

    # Serialise with xref.
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objects) + 1).encode()
            + b" /Root 1 0 R >>\nstartxref\n"
            + str(xref_pos).encode() + b"\n%%EOF\n")
    return bytes(out), len(page_obj_nums)


# --- markdown -> print HTML (reuses the Haven shape) ------------------------

def _inline(text: str) -> str:
    t = html_mod.escape(text)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"\*(.+?)\*", r"<em>\1</em>", t)
    return t


def markdown_to_html(md: str, title: str = "print") -> str:
    body: list[str] = []
    in_code = False
    code: list[str] = []
    for line in md.split("\n"):
        if line.startswith("```"):
            if in_code:
                body.append(f"<pre>{html_mod.escape(chr(10).join(code))}</pre>")
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        s = line.strip()
        if not s:
            body.append("<br>")
            continue
        m = re.match(r"^(#{1,6})\s+(.+)", s)
        if m:
            lvl = len(m.group(1))
            body.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
        elif s.startswith("- ") or s.startswith("* "):
            body.append(f"<li>{_inline(s[2:])}</li>")
        else:
            body.append(f"<p>{_inline(s)}</p>")
    return (f'<!DOCTYPE html><html><head><meta charset="utf-8">'
            f"<title>{html_mod.escape(title)}</title><style>"
            "body{font-family:Georgia,serif;font-size:12pt;max-width:7in;"
            "margin:0.5in auto;color:#222}code{font-family:Menlo,monospace;"
            "background:#f0f0f0;padding:1px 3px}pre{background:#f0f0f0;padding:8pt}"
            f"</style></head><body>{''.join(body)}</body></html>")


def _html_to_pdf(html: str) -> bytes:
    if not HAS_WEASYPRINT:
        raise ContentError("weasyprint not installed")
    try:
        return weasyprint.HTML(string=html).write_pdf()
    except Exception as exc:  # noqa: BLE001
        raise ContentError(f"weasyprint render failed: {exc}") from exc


def _count_pdf_pages(pdf: bytes) -> int:
    # Best-effort: count page objects. Good enough for the cap estimate.
    n = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
    return max(n, 1)


def render(content_type: str, content: str) -> tuple[bytes, str, int]:
    """Return (document_bytes, ipp_document_format, page_count).

    Raises ContentError for an unrenderable/absent-renderer case — the caller
    turns that into an `error` result, never a crash."""
    ct = (content_type or "").lower().strip()
    if ct == "text":
        pdf, pages = text_to_pdf(content)
        return pdf, PDF_MIME, pages
    if ct == "pdf":
        try:
            raw = base64.b64decode(content, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise ContentError(f"pdf content is not valid base64: {exc}") from exc
        if not raw.startswith(b"%PDF"):
            raise ContentError("decoded pdf does not start with %PDF")
        return raw, PDF_MIME, _count_pdf_pages(raw)
    if ct == "markdown":
        pdf = _html_to_pdf(markdown_to_html(content))
        return pdf, PDF_MIME, _count_pdf_pages(pdf)
    if ct == "html":
        pdf = _html_to_pdf(content)
        return pdf, PDF_MIME, _count_pdf_pages(pdf)
    if ct == "svg":
        wrapper = ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
                   '<body style="margin:0">' + content + "</body></html>")
        pdf = _html_to_pdf(wrapper)
        return pdf, PDF_MIME, _count_pdf_pages(pdf)
    raise ContentError(f"unsupported content_type: {content_type!r}")
