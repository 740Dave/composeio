"""Printer backends — each is a bus peer under `print_services/<id>/`.

A backend owns: a descriptor (registry row), a submit handler, and a status
probe. The server wires each backend's namespaces onto the bus and drives the
heartbeat; a backend never touches MQTT directly.
"""

from __future__ import annotations

import logging
import time
import uuid

from . import content as content_mod
from .ipp import IPPClient, IPPError
from . import sdcp

logger = logging.getLogger("print_services.backends")


class Backend:
    id: str
    descriptor: dict

    def status(self) -> dict:
        """Return {state, ts, ...} — retained status. Never raises."""
        raise NotImplementedError

    def submit(self, job: dict) -> dict:
        """Handle a submit job dict; return a result dict (status/detail/...)."""
        raise NotImplementedError


class HpBackend(Backend):
    """HP Color LaserJet MFP M476dn — driverless IPP-Everywhere (NO CUPS)."""

    def __init__(self, ip: str = "192.0.2.77"):
        self.id = "hp"
        self.ip = ip
        self.primary_uri = f"ipp://{ip}:631/ipp/print"
        self.fallback_uri = f"ipps://{ip}:443/ipp/print"
        self.ipp = IPPClient(self.primary_uri, self.fallback_uri)
        self.descriptor = {
            "id": "hp",
            "kind": "laser",
            "width": "letter",
            "color": "color",
            "graphics": True,
            "dpi": 600,
            # Advertise only what actually works on THIS host: text+pdf always;
            # markdown/html/svg only when weasyprint is importable (else submit
            # returns a loud error). A wire descriptor must not promise a
            # content-type this deploy cannot render ([CXC-PRINT-DISCOVERY]).
            "content_types": (["text", "pdf"]
                              + (["markdown", "html", "svg"]
                                 if content_mod.HAS_WEASYPRINT else [])),
            "status_ns": "print_services/hp/status",
            "backend": "ipp-everywhere",
        }

    def get_printer_attributes(self) -> dict:
        return self.ipp.get_printer_attributes()

    def status(self) -> dict:
        try:
            attrs = self.ipp.get_printer_attributes()
            name = attrs.get("printer_state_name", "unknown")
            reasons = attrs["attributes"].get("printer-state-reasons")
            state = {"idle": "ready", "processing": "busy",
                     "stopped": "paper_out"}.get(name, "ready")
            # A stopped printer with a media reason is paper_out; otherwise offline-ish.
            if name == "stopped" and reasons and "media" not in str(reasons):
                state = "offline"
            return {"state": state, "ts": time.time(),
                    "printer_state": name, "reasons": reasons}
        except (IPPError, OSError) as exc:
            return {"state": "offline", "ts": time.time(), "detail": str(exc)}

    def submit(self, job: dict, page_cap=None) -> dict:
        job_id = job.get("job_id") or f"hp-{uuid.uuid4().hex[:12]}"
        content_type = job.get("content_type", "text")
        options = job.get("options") or {}
        copies = int(options.get("copies", 1) or 1)
        try:
            document, doc_format, pages = content_mod.render(
                content_type, job.get("content", ""))
        except content_mod.ContentError as exc:
            return {"job_id": job_id, "status": "error", "detail": str(exc)}

        total_pages = pages * max(copies, 1)
        if page_cap is not None:
            ok, err = page_cap.try_consume(total_pages)
            if not ok:
                return {"job_id": job_id, "status": "error", "detail": err,
                        "pages": total_pages}

        try:
            # copies via repeated Print-Job keeps the IPP path minimal and works
            # on printers that ignore the `copies` job attribute.
            last = None
            for _ in range(max(copies, 1)):
                last = self.ipp.print_job(document, doc_format, job_name=job_id)
            return {"job_id": job_id, "status": "done",
                    "pages": total_pages,
                    "detail": f"ipp job-id={last.get('job_id')} "
                              f"state={last.get('job_state')} via {last.get('endpoint')}"}
        except (IPPError, OSError) as exc:
            return {"job_id": job_id, "status": "error", "detail": str(exc),
                    "pages": total_pages}


class Elegoo3dBackend(Backend):
    """Elegoo Centauri Carbon — SDCP status+discovery only (submission is v2)."""

    def __init__(self, ip: str = "192.0.2.34"):
        self.id = "elegoo3d"
        self.ip = ip
        self.descriptor = {
            "id": "elegoo3d",
            "kind": "fdm",
            "width": None,
            "color": None,
            "graphics": False,
            "dpi": None,
            "content_types": [],
            "status_ns": "print_services/elegoo3d/status",
            "backend": "sdcp",
        }

    def status(self) -> dict:
        try:
            s = sdcp.get_status(self.ip, timeout=10.0)
            code = s.get("state")
            name = s.get("state_name", "unknown")
            state = {"idle": "ready", "printing": "busy",
                     "paused": "busy", "error": "offline"}.get(name, "ready")
            return {"state": state, "ts": time.time(),
                    "sdcp_state": name, "sdcp_code": code, "raw": s.get("raw")}
        except sdcp.SDCPError as exc:
            return {"state": "offline", "ts": time.time(), "detail": str(exc)}

    def submit(self, job: dict, page_cap=None) -> dict:
        job_id = job.get("job_id") or f"elegoo3d-{uuid.uuid4().hex[:12]}"
        return {"job_id": job_id, "status": "error",
                "detail": "3D print submission is v2 — needs slicing/upload; "
                          "v1 is status+discovery only"}
