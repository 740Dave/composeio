"""Run the dedicated template store: ``python -m fingerprint_template_store``.

Standalone process, standalone port (:8422 by default, ``FINGERPRINT_TEMPLATE_
STORE_PORT`` overridable), standalone data directory (``FINGERPRINT_TEMPLATE_
STORE_DATA_DIR``, default ``/var/lib/composeio/fingerprint_templates``) —
never bus_storage's. A dev/test instance is fine to run locally; deploying it
to any shared host is Haven-Deployer's call, same as ``bus_storage`` and
``fingerprint_service``.
"""

from __future__ import annotations

from .server import main

if __name__ == "__main__":
    raise SystemExit(main())
