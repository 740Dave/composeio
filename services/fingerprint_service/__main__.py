"""Deploy entrypoint — GLASS-PENDING.

⛔ This does NOT run here. Building and testing fingerprint_service is entirely
in-process (see ``tests/test_integration.py``). A real deploy — connecting the
service to the live ComposeBus broker — is Haven-Deployer's job and is gated on
VERIFYING THE LIVE BROKER HOST at deploy time (the cutover from
mqtt-broker.local to a dedicated Buildhost is pending). Running this without
that verification is exactly the step this service must not take autonomously.

When Haven-Deployer runs it, the broker URL comes from :func:`config.broker_url`
(env-overridable, defaulting to the current live master), a bridged
``ComposeIOBus`` is built against it, the service is attached, and the retained
device announces drive ``follow_device`` for the fleet.
"""

from __future__ import annotations

import sys

from .config import broker_url


def main() -> int:
    url = broker_url()
    sys.stderr.write(
        "fingerprint_service deploy is GLASS-PENDING.\n"
        f"  resolved broker URL: {url}\n"
        "  Haven-Deployer owns the deploy and must verify the live broker host\n"
        "  first (buildhost cutover pending). Refusing to connect autonomously.\n"
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
