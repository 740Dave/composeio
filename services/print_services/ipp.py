"""Minimal IPP-Everywhere client — stdlib only (RFC 8010/8011).

WHY HAND-ROLLED: pyipp is the clean choice, but it is NOT installed and pulling
it (plus aiohttp) onto buildhost for two operations is disproportionate. This
encodes exactly two operations directly against the binary IPP wire format:

  * Get-Printer-Attributes (0x000B) — the driverless-IPP SMOKE TEST. Proves the
    ipp://.../ipp/print path end to end WITHOUT printing a page, and returns
    printer-state / printer-state-reasons.
  * Print-Job (0x0002) — submit a document (text/plain or application/pdf).

Driverless == NO CUPS: we talk IPP straight to the printer's ipp/print
endpoint over HTTP. Falls back ipp:// (631) -> ipps:// (443) on transport error.
"""

from __future__ import annotations

import http.client
import logging
import ssl
import struct
from urllib.parse import urlparse

logger = logging.getLogger("print_services.ipp")

# --- IPP constants (RFC 8010) ------------------------------------------------
OP_PRINT_JOB = 0x0002
OP_GET_PRINTER_ATTRIBUTES = 0x000B

TAG_OPERATION_ATTRS = 0x01
TAG_JOB_ATTRS = 0x02
TAG_END_ATTRS = 0x03
TAG_PRINTER_ATTRS = 0x04

# value tags
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_OCTET_STRING = 0x30
TAG_DATETIME = 0x31
TAG_RESOLUTION = 0x32
TAG_RANGE_OF_INTEGER = 0x33
TAG_TEXT = 0x41
TAG_NAME = 0x42
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_URI_SCHEME = 0x46
TAG_CHARSET = 0x47
TAG_NATURAL_LANG = 0x48
TAG_MIME_MEDIA_TYPE = 0x49

PRINTER_STATE_NAMES = {3: "idle", 4: "processing", 5: "stopped"}


class IPPError(Exception):
    """IPP transport failed or the printer returned a non-successful status."""


def _enc_str(tag: int, name: str, value: str) -> bytes:
    nb = name.encode("utf-8")
    vb = value.encode("utf-8")
    return struct.pack(">B", tag) + struct.pack(">H", len(nb)) + nb + \
        struct.pack(">H", len(vb)) + vb


def _enc_addl_value(tag: int, value: str) -> bytes:
    """Additional value for a 1setOf attribute: zero-length name."""
    vb = value.encode("utf-8")
    return struct.pack(">B", tag) + struct.pack(">H", 0) + \
        struct.pack(">H", len(vb)) + vb


def _build_request(op: int, request_id: int, printer_uri: str, *,
                   requested_attributes: list[str] | None = None,
                   job_name: str | None = None,
                   document_format: str | None = None,
                   document: bytes | None = None) -> bytes:
    out = bytearray()
    out += struct.pack(">H", 0x0200)          # version 2.0
    out += struct.pack(">H", op)              # operation-id
    out += struct.pack(">I", request_id)      # request-id

    # operation-attributes-tag: charset + natural-language MUST come first.
    out += struct.pack(">B", TAG_OPERATION_ATTRS)
    out += _enc_str(TAG_CHARSET, "attributes-charset", "utf-8")
    out += _enc_str(TAG_NATURAL_LANG, "attributes-natural-language", "en")
    out += _enc_str(TAG_URI, "printer-uri", printer_uri)
    out += _enc_str(TAG_NAME, "requesting-user-name", "composeio")
    if job_name:
        out += _enc_str(TAG_NAME, "job-name", job_name)
    if document_format:
        out += _enc_str(TAG_MIME_MEDIA_TYPE, "document-format", document_format)
    if requested_attributes:
        first, *rest = requested_attributes
        out += _enc_str(TAG_KEYWORD, "requested-attributes", first)
        for a in rest:
            out += _enc_addl_value(TAG_KEYWORD, a)

    out += struct.pack(">B", TAG_END_ATTRS)
    if document is not None:
        out += document
    return bytes(out)


def _parse_response(body: bytes) -> dict:
    """Parse an IPP response into {status_code, request_id, attributes:{name:val}}.

    Only the value types we ask for are decoded richly (enum/integer/keyword/
    text/name/uri); everything else is captured as raw bytes so nothing is lost.
    Repeated attribute names collapse to a list.
    """
    if len(body) < 8:
        raise IPPError(f"IPP response too short ({len(body)} bytes)")
    _ver, status_code, request_id = struct.unpack(">HHI", body[:8])
    attrs: dict[str, object] = {}
    i = 8
    cur_name = None
    while i < len(body):
        tag = body[i]
        i += 1
        if tag in (TAG_OPERATION_ATTRS, TAG_JOB_ATTRS, TAG_PRINTER_ATTRS,
                   0x05):  # delimiter/unsupported-attrs groups
            cur_name = None
            continue
        if tag == TAG_END_ATTRS:
            break
        if i + 2 > len(body):
            break
        (name_len,) = struct.unpack(">H", body[i:i + 2])
        i += 2
        name = body[i:i + name_len].decode("utf-8", "replace")
        i += name_len
        (val_len,) = struct.unpack(">H", body[i:i + 2])
        i += 2
        raw = body[i:i + val_len]
        i += val_len
        name = name or cur_name  # zero-length name == additional value
        cur_name = name
        val = _decode_value(tag, raw)
        if name in attrs:
            if not isinstance(attrs[name], list):
                attrs[name] = [attrs[name]]
            attrs[name].append(val)
        else:
            attrs[name] = val
    return {"status_code": status_code, "request_id": request_id, "attributes": attrs}


def _decode_value(tag: int, raw: bytes):
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack(">i", raw)[0]
    if tag == TAG_BOOLEAN and len(raw) == 1:
        return bool(raw[0])
    if tag in (TAG_TEXT, TAG_NAME, TAG_KEYWORD, TAG_URI, TAG_URI_SCHEME,
               TAG_CHARSET, TAG_NATURAL_LANG, TAG_MIME_MEDIA_TYPE):
        return raw.decode("utf-8", "replace")
    return raw.hex()


def _post_ipp(url: str, payload: bytes, timeout: float = 20.0) -> bytes:
    u = urlparse(url)
    host = u.hostname
    if u.scheme == "ipps":
        port = u.port or 443
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # printers ship self-signed certs
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    else:
        port = u.port or 631
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("POST", u.path or "/ipp/print", body=payload,
                     headers={"Content-Type": "application/ipp",
                              "Content-Length": str(len(payload))})
        resp = conn.getresponse()
        data = resp.read()
        if resp.status != 200:
            raise IPPError(f"HTTP {resp.status} {resp.reason} from {url}")
        return data
    finally:
        conn.close()


class IPPClient:
    """Driverless IPP client for one printer, with ipp->ipps fallback."""

    def __init__(self, primary_uri: str, fallback_uri: str | None = None):
        self.primary_uri = primary_uri
        self.fallback_uri = fallback_uri
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _send(self, uri: str, payload: bytes) -> dict:
        return _parse_response(_post_ipp(uri, payload))

    def _send_with_fallback(self, payload: bytes) -> tuple[dict, str]:
        try:
            return self._send(self.primary_uri, payload), self.primary_uri
        except (OSError, IPPError) as exc:
            if not self.fallback_uri:
                raise
            logger.warning("IPP primary %s failed (%s); trying %s",
                           self.primary_uri, exc, self.fallback_uri)
            return self._send(self.fallback_uri, payload), self.fallback_uri

    def get_printer_attributes(self,
                               requested: list[str] | None = None) -> dict:
        """Get-Printer-Attributes. Returns a dict incl. printer_state_name."""
        requested = requested or [
            "printer-state", "printer-state-reasons", "printer-state-message",
            "printer-make-and-model", "printer-is-accepting-jobs",
            "printer-location", "printer-info", "queued-job-count",
        ]
        payload = _build_request(
            OP_GET_PRINTER_ATTRIBUTES, self._next_id(), self.primary_uri,
            requested_attributes=requested)
        parsed, used_uri = self._send_with_fallback(payload)
        attrs = parsed["attributes"]
        state = attrs.get("printer-state")
        parsed["printer_state"] = state
        parsed["printer_state_name"] = PRINTER_STATE_NAMES.get(state, f"unknown({state})")
        parsed["endpoint"] = used_uri
        return parsed

    def print_job(self, document: bytes, document_format: str,
                  job_name: str = "composeio") -> dict:
        """Print-Job with an inline document. Returns parsed job attributes
        (job-id, job-state). CALLER enforces the page cap before this."""
        payload = _build_request(
            OP_PRINT_JOB, self._next_id(), self.primary_uri,
            job_name=job_name, document_format=document_format,
            document=document)
        parsed, used_uri = self._send_with_fallback(payload)
        attrs = parsed["attributes"]
        parsed["job_id"] = attrs.get("job-id")
        parsed["job_state"] = attrs.get("job-state")
        parsed["endpoint"] = used_uri
        # IPP status-code < 0x0100 == successful class.
        if parsed["status_code"] >= 0x0100:
            raise IPPError(f"IPP Print-Job status 0x{parsed['status_code']:04x} "
                           f"({attrs.get('status-message', 'no message')})")
        return parsed
