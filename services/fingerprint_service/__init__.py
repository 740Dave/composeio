"""fingerprint_service — the superset authority and enrollment orchestration.

The service that holds every enrolled fingerprint template keyed by LOGICAL
identity ``(person, ordinal)`` and NEVER by a physical slot; selects a device's
cast; originates the roster push (uuid + entry_count + role table + ordered
logical entries, NO hash); reconciles each device's held set by SUBSCRIPTION to
its ``fingerprint_ready`` heartbeat (never a query); orchestrates enrollment
(logical enroll command -> device capture -> readback -> store keyed by (person,
ordinal)); owns person + casting + RBAC CRUD; and enforces the TOTAL-DELETE
retention rule (six months of inactivity removes templates AND the person
record, no tombstone).

Specs: [SPEC:CXC-FPSVC] (C74), [SPEC:CXC-FPBUS] (C73), [SPEC:CXC-FPID] (C71).
It composes against the reference device layer in
``composeio.peripherals.fingerprint`` and reuses its ``push_validation``.
"""

from __future__ import annotations

from .cast import CastMember, select_cast
from .enroll import EnrollOrchestrator
from .roster_push import PushResult, RosterPublisher
from .service import FingerprintService
from .store import Casting, Person, SupersetStore, TemplateRecord

__all__ = [
    "FingerprintService",
    "SupersetStore",
    "Person",
    "Casting",
    "TemplateRecord",
    "RosterPublisher",
    "PushResult",
    "EnrollOrchestrator",
    "CastMember",
    "select_cast",
]
