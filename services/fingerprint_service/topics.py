"""Device-addressed bus topics — every message names the device ([CXC-FPBUS-
ADDRESSING]).

The device-emitted names match the reference firmware twin exactly
(:mod:`composeio.peripherals.fingerprint.device`, which publishes
``f"{device_id}/{message}"``). The service-originated names (the push and the
enroll command) and the readback name (proposed [DD-FPBUS-ENROLL-READBACK-
SHAPE]) are defined here so the service and the device firmware bus client agree
on one string each.

No topic anywhere carries a physical slot ([CXC-FPBUS-WHAT-NEVER-CROSSES]).
"""

from __future__ import annotations

# --- service -> device -------------------------------------------------------
# The roster push. C73 names the push semantically ([CXC-FPBUS-PUSH-SEQUENCE]);
# the device receipt is FingerprintDevice.receive_push.
def template_set(device_id: str) -> str:
    return f"{device_id}/template_set"


# The enrollment command ([CXC-FPBUS-MSG-ENROLL] "<dev>/<cmd>/enroll").
def enroll_cmd(device_id: str) -> str:
    return f"{device_id}/cmd/enroll"


def enroll_cancel_cmd(device_id: str) -> str:
    return f"{device_id}/cmd/enroll/cancel"


# --- device -> service -------------------------------------------------------
def template_set_loaded(device_id: str) -> str:
    return f"{device_id}/template_set_loaded"


def template_set_failed(device_id: str) -> str:
    return f"{device_id}/template_set_failed"


def fingerprint_ready(device_id: str) -> str:
    return f"{device_id}/fingerprint_ready"


def fingerprint_push_needed(device_id: str) -> str:
    return f"{device_id}/fingerprint_push_needed"


def fingerprint_inoperable(device_id: str) -> str:
    return f"{device_id}/fingerprint_inoperable"


def device_announce(device_id: str) -> str:
    return f"{device_id}/announce"


# The just-enrolled template's return path (proposed [DD-FPBUS-ENROLL-READBACK-
# SHAPE]: "<dev>/<result>/enroll/complete {person, ordinal, template, request_id}").
def enroll_complete(device_id: str) -> str:
    return f"{device_id}/result/enroll/complete"


# The device's local + uplinked authentication event ([CXC-FPBUS-MSG-AUTH-EVENT]),
# the service's window onto biometric use for the retention clock.
def auth_event(device_id: str) -> str:
    return f"{device_id}/auth_event"
