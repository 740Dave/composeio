"""ComposeBus namespaces this service originates.

ONE semantic fact leaves this service on its own name — ``buildhost/storage/free_bytes``
— the number of free bytes on the monitored filesystem. This is the FROZEN contract a
peer render build (the launch bar) depends on EXACTLY; the name is defined once here so
the service and every consuming leaf agree on the same spelling, the same discipline
``time_service/topics.py`` keeps.

storage_monitor is a DUMB SENSOR ([DX-STATE-ON-BUS]): it publishes the FACT and does
nothing else — no thresholds, no colors, no policy. A downstream consumer (the launch
bar) owns all reaction. No namespace here carries a threshold, a status, or a color.
"""

from __future__ import annotations
import os

#: The one fact storage_monitor originates: the number of FREE BYTES on the monitored
#: filesystem (``shutil.disk_usage(path).free``), a bare int on the wire. Published as a
#: retained STATE so a late-joining leaf reads the current free-space immediately instead
#: of waiting up to a (slow, 30 s) tick.
#: The host segment is CONFIGURATION (env COMPOSEIO_VAR_STORAGE_HOST, the same knob the launch-bar scene
#: reads as $storage_host). Neutral public default; a deployment sets its real host name.
STORAGE_HOST = os.environ.get("COMPOSEIO_VAR_STORAGE_HOST", "buildhost")
FREE_BYTES = f"{STORAGE_HOST}/storage/free_bytes"
