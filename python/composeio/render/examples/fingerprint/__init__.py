"""R503 fingerprint-game player/character/RBAC binding UI — example package.

A FUNCTIONAL PLACEHOLDER binding surface for the ``fingerprint_service``
service (``services/fingerprint_service``): enter people, capture up to two
templates each, cast people to characters in a game module, define the
character RBAC, and START a game (which fires the roster push).

The visual design is a scaffold — David redoes the look in the Colonial (BSG)
UX.  Everything here is FUNCTION + REUSE:

* ``binder.py``  — in-process controller: owns a FingerprintService +
                   an in-process reference device, subscribes to the scene's
                   ``fp/cmd/*`` command namespaces, mutates the superset store,
                   and publishes formatted ``fp/view/*`` state.  It invents NO
                   transport — it rides the existing ComposeIOBus.
* the scene      — ``examples/fingerprint_casting.compose.yaml`` composes
                   EXISTING widgets (text_input, emit_button, label) + the one
                   new ``info_list`` panel, wired to those namespaces.
* ``drive.py``   — headless driver: stands the whole thing up, runs the
                   binding scenario over the bus, captures a proof PNG, and
                   shows the roster push firing.
"""
