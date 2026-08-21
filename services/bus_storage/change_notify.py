"""Asset-change notification PRODUCER — [SPEC:CXC-ASSET-CHANGE-NOTIFY] slice P-a.

Ratified contract built here (and ONLY this):
  [CXC-ACN-SHAPE]      — on any asset content change, broadcast a compact
                         `{old_content_hash -> new_content_hash}` record, bus-wide,
                         carrying NO asset name/id and NO device targeting. Consumers
                         self-select by the hash they currently hold.
  [CXC-ACN-OLDNEW]     — carry BOTH hashes: it lets change-advertisement be fully
                         stateless (nothing tracks which device holds which file) and
                         is the addressing for a future delta transfer.
  [CXC-ACN-RETENTION]  — publish RETAINED on MQTT so a late subscriber still gets the
                         latest state without polling (the missed-event cover). Retain
                         is inherited automatically: a STATE message is retained by the
                         platform's RETAIN_BY_TYPE table.
  [CXC-ACN-PRECEDENT]  — mirrors, not reinvents, `services/uart_mqtt_bridge/
                         config_server.py`, which already content-hashes a transfer and
                         logs `... CHANGED (<oldhash> -> <newhash>) — unsolicited push`.

WHY THIS RIDES THE PLATFORM WAIST, NOT A PRIVATE PAHO CLIENT.
  DP-CIO-6: transport is an implementation detail. This module names NO substrate — it
  hands an `mqtt://host:port` URL to `composeio.source_bus.acquire_bus`, which builds
  the transport through `transport_registry` (an unknown type RAISES; a known type whose
  broker is merely unreachable degrades LOUDLY to local-only). Publishing goes through
  `ComposeIOBus.publish_state`, so the `composeio/{scope}/{namespace}` topic mapping and
  the retain semantics are the platform's, reused by reference.

WIRE FORCING ([CXC-ACN-SHAPE.wire-forcing]). The event is FINGERPRINTS ONLY. The full
  asset never goes on the wire — the ESP32 bus payload cap is 512 B (2048 B on Qualia)
  and a full scene YAML (~9.6 KB) would be dropped. The `{old,new}` envelope is well
  under 512 B.

BEST-EFFORT, NEVER BLOCKING. A failure to publish (broker down, bus unavailable) is
  logged LOUDLY and swallowed — it MUST NOT break or block an asset PUT. That is the
  `[transport_registry]` rule applied here: a known-but-unreachable backend returns/logs,
  it never silently no-ops AND never propagates a fault into the storage write path.

⛔ OUT OF SCOPE (unratified / separate builds), left as marked seams below:
  * [CXC-ACN-RECOVERY] CAN / missed-event device-pull + content-lineage index —
    architect inference, open-for-ratify. No device in the flip set needs it.
  * [CXC-ACN-DELTA]    delta/patch transfer — future; `{old,new}` is the addressing
    precondition only, which THIS event already carries.
  * the ESP32 coalescing mailbox — separate dispatch.
  * the device/consumer (subscribe-and-swap) side — that is P-b firmware.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("bus_storage.change_notify")

# The bus namespace the change event is published on. Under the platform's
# `composeio/{scope}/{namespace}` mapping with global scope this becomes MQTT topic
# `composeio/global/bus_storage/asset/changed`. Env-overridable so a test can use an
# ephemeral namespace without polluting the production topic's retained value.
DEFAULT_NAMESPACE = "bus_storage/asset/changed"

# Ratified wire budget ([CXC-ACN-SHAPE.wire-forcing]). The smaller of the two device
# caps; the event must fit the ESP32-C3.
PAYLOAD_CAP_BYTES = 512


def content_hash(data: bytes) -> str:
    """Content fingerprint of an asset's bytes — sha256, lowercase hex.

    DEVIATION FROM THE PRECEDENT, FLAGGED FOR THE ARCHITECT. The cited precedent
    (`config_server.py`) uses a 16-bit CRC (`config_chunk.content_hash`) because its
    fingerprint has to fit a CAN wire field. This event rides MQTT with a 512 B budget,
    and [CXC-ACN-SHAPE.collision-safety] rests the ENTIRE addressing on "two assets never
    collide because their content hashes differ" — a 16-bit CRC has 65 536 buckets and
    collides across a real asset set, which would break self-selection. sha256 gives
    genuine content-addressing, fits the budget (two hex digests ≈ 128 B), and is cheap
    on the ESP32's hardware SHA. The fingerprint APPROACH (hash the bytes, pair
    old->new) is the precedent's; the algorithm is upgraded for collision safety.
    """
    return hashlib.sha256(data).hexdigest()


def build_event(old_hash: Optional[str], new_hash: str) -> dict:
    """The compact, identity-free change record. `old` is null on a create."""
    return {"old": old_hash, "new": new_hash}


class AssetChangeNotifier:
    """Publishes the retained `{old,new}` change event when an asset's bytes change.

    Construct directly with an injected `bus` (the in-process / test path), or via
    `from_env()` (the standalone server path, which self-creates a bus from
    COMPOSEUX_MQTT_BROKER). A `bus` of None means "no producer configured" — every
    `notify` then logs LOUDLY and drops the event, never raising.
    """

    def __init__(
        self,
        bus: Any,
        namespace: str = DEFAULT_NAMESPACE,
        *,
        source: str = "bus_storage",
        log: logging.Logger = logger,
    ):
        self._bus = bus
        self._namespace = namespace
        self._source = source
        self._log = log
        # Rate-limit the per-write "no bus configured" ERROR (mirrors
        # `_large_payload_warned` in transport_mqtt.py). Without a bus EVERY asset
        # PUT would log at ERROR forever; an operator intentionally running
        # broker-less already gets the once-at-startup INERT warning in `from_env`
        # and does not need a second ERROR per write on top of it.
        self._no_bus_warned = False

    # -- construction from the server's environment --------------------------------
    @classmethod
    def from_env(cls, *, connect_wait_s: float = 3.0) -> "AssetChangeNotifier":
        """Build the producer from the process environment.

        COMPOSEUX_MQTT_BROKER (mirrors the fleet) selects the broker host;
        COMPOSEUX_MQTT_PORT defaults to 1883; BUS_STORAGE_CHANGE_NAMESPACE overrides the
        namespace. With no broker set the producer is INERT (bus=None) and says so — the
        storage service still serves, it simply publishes no change events.
        """
        namespace = os.environ.get("BUS_STORAGE_CHANGE_NAMESPACE", DEFAULT_NAMESPACE)
        broker = os.environ.get("COMPOSEUX_MQTT_BROKER", "").strip()
        if not broker:
            logger.warning(
                "[bus_storage] COMPOSEUX_MQTT_BROKER unset — asset-change producer is "
                "INERT (no bus). Set it to broadcast {old->new} change events."
            )
            return cls(bus=None, namespace=namespace)

        port = int(os.environ.get("COMPOSEUX_MQTT_PORT", "1883"))
        mqtt_url = f"mqtt://{broker}:{port}"
        try:
            # Imported here, not at module top: with no broker the server needs no
            # `composeio` on the path at all (keeps the stdlib-only default intact).
            from composeio.source_bus import acquire_bus
            from composeio.namespace import Scope

            bus = acquire_bus(mqtt_url, label="bus_storage")
            # Explicit GLOBAL scope + json engine. GLOBAL so the event leaves the process
            # onto MQTT; json so the free-form hash dict is accepted (a strict engine
            # would reject it — the engine-mismatch SIGABRT hazard). Explicit rather than
            # AUTO to avoid the local-if-self-subscribed heuristic
            # ([DEC-COMPOSER-SEND-GLOBAL-SCOPE]).
            bus.register_namespace(namespace, engine_config="json", scope=Scope.GLOBAL)
        except Exception as exc:  # noqa: BLE001 — environment, never fatal to storage
            logger.error(
                "[bus_storage] could not acquire the bus (%s) — asset-change producer "
                "DISABLED, storage service continues: %s",
                mqtt_url,
                exc,
            )
            return cls(bus=None, namespace=namespace)

        inst = cls(bus=bus, namespace=namespace)
        inst._await_connection(connect_wait_s, mqtt_url)
        return inst

    def _await_connection(self, timeout_s: float, mqtt_url: str) -> None:
        """Give the async MQTT connect a bounded moment, and report the outcome LOUDLY.

        Reporting once at startup avoids a false 'unreachable' per-publish during the
        normal sub-second connect window, while still making a genuinely down broker
        loud immediately ([transport_registry] loud-degrade rule)."""
        transport = self._bus.global_transport if self._bus is not None else None
        if transport is None:
            self._log.error(
                "[bus_storage] bus for %s is LOCAL-ONLY (no external transport) — "
                "asset-change events will be dropped. Asset writes are unaffected.",
                mqtt_url,
            )
            return
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if getattr(transport, "is_connected", False):
                self._log.info(
                    "[bus_storage] asset-change producer LIVE on %s (namespace=%s, "
                    "retained {old->new} events).",
                    mqtt_url,
                    self._namespace,
                )
                return
            time.sleep(0.05)
        self._log.error(
            "[bus_storage] asset-change producer could NOT connect to %s within %.1fs — "
            "events will be best-effort and may be dropped. Asset writes are unaffected.",
            mqtt_url,
            timeout_s,
        )

    # -- connectivity introspection ------------------------------------------------
    def _connected(self) -> bool:
        transport = self._bus.global_transport if self._bus is not None else None
        return bool(transport is not None and getattr(transport, "is_connected", False))

    # -- the write-path hook -------------------------------------------------------
    def notify(self, old_bytes: Optional[bytes], new_bytes: bytes) -> Optional[dict]:
        """Compute old/new fingerprints; if the content changed, publish the event.

        Returns the published event dict, or None when the content was unchanged (same
        bytes -> no event). NEVER raises: a publish failure is logged and swallowed so an
        asset PUT is never broken or blocked by the bus.

          * create (no prior bytes)      -> `old` is null            [CXC-ACN-SHAPE / A3]
          * changed bytes                -> one `{old,new}` event     [A1]
          * identical bytes re-PUT        -> None, no event           [A2]
        """
        new_hash = content_hash(new_bytes)
        old_hash = content_hash(old_bytes) if old_bytes is not None else None
        if old_hash == new_hash:
            return None  # content unchanged — nothing to advertise
        event = build_event(old_hash, new_hash)
        self._publish(event)
        return event

    def _publish(self, event: dict) -> None:
        if self._bus is None:
            if not self._no_bus_warned:
                self._no_bus_warned = True
                self._log.error(
                    "[bus_storage] asset changed (%s -> %s) but NO bus is configured — "
                    "event NOT published (set COMPOSEUX_MQTT_BROKER). Asset write "
                    "unaffected. Reported ONCE per process; further no-bus asset "
                    "changes are silent (see the once-at-startup INERT warning above).",
                    event["old"],
                    event["new"],
                )
            return
        try:
            self._bus.publish_state(self._namespace, event, source=self._source)
        except Exception as exc:  # noqa: BLE001 — best-effort, never break the PUT
            self._log.error(
                "[bus_storage] FAILED to publish asset-change event (%s -> %s): %s. "
                "Asset write unaffected.",
                event["old"],
                event["new"],
                exc,
            )
            return
        # LOUD degrade: the value was accepted locally but the wire is down, so it did
        # not actually reach any subscriber. This is the [transport_registry] rule — a
        # known-but-unreachable backend must not silently no-op.
        if not self._connected():
            self._log.error(
                "[bus_storage] asset-change event (%s -> %s) published to a DISCONNECTED "
                "bus (broker unreachable) — it may not have reached the wire. Asset write "
                "unaffected.",
                event["old"],
                event["new"],
            )

    # ----------------------------------------------------------------------------- #
    # ⛔ SEAM — [CXC-ACN-RECOVERY] (unratified, open-for-ratify). Do NOT build here.
    #   A CAN device cannot rely on MQTT retain ([CXC-ACN-RETENTION.can-gap]); the
    #   ratified-but-inference recovery path is a membrane-relay-held manifest + a
    #   low-rate liveness heartbeat, plus a content-lineage index for device pull. When
    #   that ratifies, the recovery producer hangs off THIS notifier (it already holds
    #   old->new); nothing in the flip set needs it today, so it is a seam, not code.
    # ⛔ SEAM — [CXC-ACN-DELTA] (future). `{old,new}` is the addressing the delta path
    #   needs; the patch computation/transfer is a separate build.
    # ----------------------------------------------------------------------------- #
