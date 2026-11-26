"""ComposeBus namespaces this service originates ([SPEC:CXC-TIME-SERVICE]).

ONE semantic fact leaves this service on its own name — ``time/base`` — and every
locale/planet realization rides its OWN ``time/<body>`` namespace
([CXC-TIME-BODY]). No namespace here carries a planet, a locale, an offset, or a
rotation: those are the time BODIES' concern, never time_service's
([CXC-TIME-SERVICE-INTENT] boundary).

The names are plain strings, defined once here so the service, its bodies, and any
consuming leaf agree on the same spelling — the same discipline
``fingerprint_service/topics.py`` keeps for its device-addressed names.
"""

from __future__ import annotations

#: The one fact time_service originates: RAW MONOTONIC time, epoch (int) on the
#: wire ([CXC-TIME-BASE], [DD-TIME-WIRE-FORMAT] ruled = epoch(int), the leaf
#: formats it). UTC epoch seconds when idle; game-elapsed-seconds-from-start in
#: game. Published as a retained STATE so a late joiner reads it immediately
#: ([DD-TIME-SCHEDULE-RETAINED] ruled = retain a last-value snapshot).
BASE = "time/base"


def body(name: str) -> str:
    """The namespace a time BODY publishes — ``time/<body>`` ([CXC-TIME-BODY]).

    ``name`` is a body slug (``ship``, ``caprica``, ...), never a planet object
    and never an offset. time_service knows nothing of what the body means; it
    only reserves the ``time/`` root that every realization hangs off.
    """
    return f"time/{name}"
