"""Default-loaded ComposeServer plugins ([CXC-CS-DEFAULT-PLUGINS]).

These are the capabilities SHED out of the ComposeBus kernel micro-kernel
refactor ([DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23] extractions-determined).
Each is a ``ComposePlugin`` ([SPEC:CXC-COMPOSESERVER]), installed by default so
a bare ``ComposeIOBus()`` behaves exactly as before — but the CODE lives here,
out of ``bus.py``, and the kernel no longer transforms, publishes time/heartbeat
or owns discovery/log machinery.
"""
