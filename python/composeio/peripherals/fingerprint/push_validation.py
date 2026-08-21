"""Service-side push-time validations — reference for roster_push.py.

These are the SERVICE's preconditions ([CXC-FPBUS-REQ-SET-SIZE-LIMIT],
[CXC-FPBUS-REQ-SET-UUID-PRESENT], [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD]), NOT
device logic: a set that fails any of them is never published, so no
template_set message ever reaches the bus. They are kept here, one pure
function, so the service scenarios can be exercised against the reference
without pulling in the whole fingerprint_storage service (which lands at
services/fingerprint_storage/roster_push.py).

The device receipt deliberately does NOT re-check these — a push that reaches a
device already passed them ([CXC-FPBUS-PUSH-SEQUENCE]) — EXCEPT the template-
size guard, which is deliberately duplicated at the device
([CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD]: a wrong-sized write may brick the sensor,
so it is a defense-in-depth safety guard, not an optimization).

``slot_count`` and ``template_size_bytes`` are NEVER hardcoded constants here —
the caller (roster_push.py) reads them SECOND-HAND from the target device's
announced capabilities ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]) and passes them in.
``None`` means "no announced value is known for the target device(s)", which
SKIPS that specific check rather than falling back to a constant — there is
nothing to check against yet, and this is not silent degradation of a KNOWN
value (see [CXC-FPBUS-MSG-DEVICE-ANNOUNCE] constraint-consumer: never default
a value that WAS announced but omitted a key).
"""

from __future__ import annotations

from typing import Any


def validate_publishable(
    push: dict[str, Any],
    *,
    slot_count: int | None = None,
    template_size_bytes: int | None = None,
) -> tuple[bool, str]:
    """Return (ok, reason). A push is publishable only if:

    1. it carries a UUID ([CXC-FPBUS-REQ-SET-UUID-PRESENT]);
    2. when ``slot_count`` is known, it holds no more than ``slot_count``
       templates ([CXC-FPBUS-REQ-SET-SIZE-LIMIT]) — the target device's own
       announced capacity, never a constant;
    3. when ``template_size_bytes`` is known, EVERY template's byte length
       equals it exactly ([CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD]) — a wrong size,
       too long or too short, may brick the sensor.

    ``reason`` is empty when ok.
    """
    uuid = push.get("template_set_uuid")
    if not uuid:  # None or "" — [CXC-FPBUS-REQ-SET-UUID-PRESENT]
        return False, "set has no UUID"
    entries = push.get("entries", [])
    if slot_count is not None and len(entries) > slot_count:
        # [CXC-FPBUS-REQ-SET-SIZE-LIMIT]
        return False, f"set has {len(entries)} templates (> {slot_count})"
    if template_size_bytes is not None:
        # [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD]
        for entry in entries:
            template = entry.get("template")
            if template is None:
                continue
            if len(template) != template_size_bytes:
                who = f"{entry.get('person')}#{entry.get('ordinal')}"
                return False, (
                    f"template for {who} is {len(template)} bytes "
                    f"(device requires {template_size_bytes})"
                )
    return True, ""
