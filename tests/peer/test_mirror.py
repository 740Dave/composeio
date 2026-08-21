"""[BPR-A-MIRROR] / [BPR-MIRROR] / [SCEN-BPR-MIRROR-LAZY] — subscription-scoped
mirror + lazy fetch-on-resolve, with last-known-good and explicit stale markers.
"""

import time


def _wait(pred, timeout=3.0, interval=0.005):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(interval)
    return pred()


class CountingResolver:
    """Upstream body store that counts fetches (proves lazy + cached)."""

    def __init__(self, store):
        self.store = store
        self.count = 0

    def __call__(self, uri):
        self.count += 1
        return self.store.get(uri)


def test_mirror_is_scoped_lazy_and_marks_unresolved(segment_factory):
    resolver = CountingResolver({"doc://runbook/x": b"runbook-body"})
    seg = segment_factory(["jumpdrive/*"], resolver=resolver, auto_reconnect=False)
    seg.master.register_namespace("jumpdrive/x", "json")
    seg.master.register_namespace("unrelated/y", "json")
    assert seg.link.wait_ready(3.0)

    # --- subscription-scoped mirror: jumpdrive/* mirrored, unrelated/* not ---
    seg.master.publish_state("jumpdrive/x", "on", source="master")
    seg.master.publish_state("unrelated/y", "zzz", source="master")
    assert _wait(lambda: seg.link.mirror.get("jumpdrive/x") == "on")
    # The unrelated namespace never crossed the seam (scope, not full replica).
    assert seg.link.resolve_state("unrelated/y").status == "unresolved"
    # A subscribed-but-absent namespace also resolves unresolved, NOT empty.
    assert seg.link.resolve_state("jumpdrive/absent").status == "unresolved"

    # --- lazy fetch on resolve: first fetch hits upstream once, then cache ---
    r1 = seg.link.fetch("doc://runbook/x")
    assert r1.status == "fetched" and r1.value == b"runbook-body"
    assert resolver.count == 1
    r2 = seg.link.fetch("doc://runbook/x")
    assert r2.status == "cached" and r2.value == b"runbook-body"
    assert resolver.count == 1          # zero additional upstream fetches

    # --- link down: cached body is last-known-good; uncached -> stale ---
    seg.hub.sever_all()
    assert _wait(lambda: not seg.link.is_connected)
    r3 = seg.link.fetch("doc://runbook/x")
    assert r3.status == "cached" and r3.value == b"runbook-body"
    r4 = seg.link.fetch("doc://runbook/never-cached")
    assert r4.status == "stale"         # explicit marker, never a fabricated empty


def test_resolve_of_unsubscribed_namespace_is_unresolved_not_empty(segment_factory):
    seg = segment_factory(["jumpdrive/*"])
    assert seg.link.wait_ready(3.0)
    r = seg.link.resolve_state("shields/status")   # outside the subscription set
    assert r.status == "unresolved"
    assert r.value is None
    assert not r.ok
