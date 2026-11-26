"""D1/D2 liveness — REAL-DISPATCH bringup suite (folds first-bringup's findings).

Re-derives, on the TRUE send -> transport -> dispatch path (two `ComposeIOBus`
peers sharing ONE `LocalTransport` object as their global connector), the two
liveness rulings ratified 2026-08-24 (DECISIONS-phase1-plan.md D1/D2;
[DEC-PHASE1-DSD-CALLS-SETTLED]). NOT a spy that patches an internal method: every
heartbeat below crosses a real connector and lands in a DIFFERENT bus's real
`_dispatch_incoming` / `_dispatch_freshness`, exactly as an MQTT beat would.

⛔ THE ISOLATION CAVEAT first-bringup surfaced. A bus's LITERAL self-echo — its
own beat handed back by the broadcast transport — is suppressed EARLIER, by the
pre-existing `_is_own_echo` fingerprint path ([CXC-PS-NO-SELF-ECHO]), and never
reaches `_dispatch_freshness` at all. So a literal self-echo does NOT exercise
D2's guard. The ONLY scenario that isolates D2 is a genuinely DISTINCT envelope
that happens to name our own `bus_id`: a SEPARATE bus/connection with the SAME
`bus_id` "twin". Its envelope carries its own seq/ts, so `_is_own_echo` (which
fingerprints the whole envelope) does not match it — it reaches the receiver,
and D2's `node != self.bus_id` filter is the only thing that stops it from
polluting the twin-under-test's roster and MASKING a dead same-id peer.

Every assertion carries a negative control; the D2-guard assertions were each
re-verified RED with the `node != self.bus_id` filter removed.
"""
from __future__ import annotations

from composeio.bus import ComposeIOBus
from composeio.plugins.liveness import LivenessPlugin
from composeio.transport_local import LocalTransport


def _roster(bus):
    """The twin-under-test's REAL peer-liveness roster (the heartbeat receiver),
    NOT `status()` — which has no 'nodes' key and would make every membership
    check vacuously true."""
    return bus._heartbeat_rx.nodes()


# =========================================================================== #
# D2 — a same-`bus_id` peer's beat, delivered over a REAL shared transport,
#      must NOT enter the twin-under-test's roster (else it masks a dead peer),
#      while a DISTINCT peer's beat MUST (proving the wire path is live).
# =========================================================================== #
def test_D2_same_bus_id_peer_beat_never_enters_the_twins_own_roster():
    shared_wire = LocalTransport()
    shared_wire._running = True

    twin_a = ComposeIOBus(bus_id="twin")     # the twin UNDER TEST (the listener)
    twin_b = ComposeIOBus(bus_id="twin")     # a DISTINCT connection, SAME id
    peer_c = ComposeIOBus(bus_id="peer-c")   # a distinct-id peer (positive ctl)
    for b in (twin_a, twin_b, peer_c):
        b.attach_connector(shared_wire, "global")

    # NEGATIVE CONTROL FIRST: before anyone beats, the twin's roster is empty —
    # a receiver that defaulted to "known/alive" would void this whole proof.
    assert "twin" not in _roster(twin_a)
    assert "peer-c" not in _roster(twin_a)

    # The LISTENER subscribes first (LocalTransport delivers only to currently
    # registered subscribers), then the two emitters fire their PROMPT beats.
    twin_a.start()
    peer_c.start()
    twin_b.start()
    try:
        # POSITIVE / TEETH: the DISTINCT peer's beat crossed the wire and was
        # counted — proving the real dispatch path is live, so the negative
        # result below is exclusion, not an empty wire.
        assert "peer-c" in _roster(twin_a), (
            "peer-c's beat never reached twin_a over the shared transport — the "
            "wire is dead and the D2-guard assertion has no teeth")
        assert twin_a.node_is_alive("peer-c") is True

        # ⛔ D2 GUARD: twin_b (a DISTINCT connection with our OWN bus_id) beat
        # across the SAME wire. Its envelope is not our echo, so `_is_own_echo`
        # let it through — and D2's `node != self.bus_id` filter is what keeps
        # "twin" OUT of our roster. Verified RED with that filter removed.
        assert "twin" not in _roster(twin_a), (
            "a same-bus_id peer's beat entered twin_a's OWN roster — a self/"
            "same-id beat now MASKS a dead same-bus_id peer, the harm D2 forbids")

        # twin_a's OWN id still resolves ALIVE — but via 'am I running' (it is
        # started), NOT from twin_b's beat. The roster assertion above is the
        # real D2 proof; this documents that self-liveness is answered elsewhere.
        assert twin_a.node_is_alive("twin") is True

        # ...and it STAYS out even after the distinct same-id peer stops.
        twin_b.stop()
        assert "twin" not in _roster(twin_a), (
            "'twin' appeared in twin_a's own roster after the same-id peer "
            "stopped — the guard must hold across the peer's whole lifecycle")
    finally:
        twin_a.stop()
        peer_c.stop()
        try:
            twin_b.stop()
        except Exception:
            pass


# =========================================================================== #
# D1 — the DEFAULT liveness plugin, brought up on a real bus, REMAINS THE
#      FALLBACK: it never claims the oracle seat, so the kernel's receiver
#      answers. A richer plugin can take the single seat later.
# =========================================================================== #
def test_D1_default_liveness_plugin_never_claims_the_oracle_seat_on_bringup():
    bus = ComposeIOBus(bus_id="d1-bringup")
    bus.start()
    try:
        # brought up, beating, and the seat is STILL empty
        assert bus._liveness_oracle is None, (
            "the default liveness plugin claimed the oracle seat on bringup — "
            "D1 says it must remain the FALLBACK")
        # therefore a peer's verdict comes from the fallback receiver
        assert bus.node_is_alive("stranger") is False
        bus.note_heartbeat("stranger", interval_s=5.0)
        assert bus.node_is_alive("stranger") is True
    finally:
        bus.stop()

    # the explicit standup path (LivenessPlugin.start) also leaves it empty
    bare = ComposeIOBus(bus_id="d1-standup-bringup")
    LivenessPlugin().start(bare)
    assert bare._liveness_oracle is None
