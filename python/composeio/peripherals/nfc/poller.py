"""NFC poller + semantic publish binding.

[SPEC:CXC-NFC] [CXC-NFC-EVENT] / [CXC-NFC-SCOPE]: an NFC reader is an INPUT
peripheral whose signal is a tag. Reading it publishes a semantic namespace —
presence, uid, and optionally an NDEF payload — via an ordinary bus
``publish_state``. "Internal to a widget" vs "onto the shared bus" is just the
namespace's scope, one mechanism; this poller is scope-agnostic and simply
publishes to the configured prefix.

Namespaces published (generic; NO application/game specifics — a value-map
[CXC-BIND-C8] turns a raw uid into meaning downstream, per the spec):

    <prefix>/present : bool   — a tag is on the reader
    <prefix>/uid     : str    — lowercase hex UID, e.g. "04abcdef"
    <prefix>/ndef    : str    — decoded NDEF text (only if enabled + present)

Debounce: a held tag publishes uid ONCE (on arrival); presence is only
republished on transitions. This is the anti-bounce the spec flags as a
per-backend tuning knob ([CXC-NFC-OPEN]).
"""

from __future__ import annotations

import logging
import time
from typing import Protocol

from .pn532 import PN532, PN532Error

logger = logging.getLogger("composeio.peripherals.nfc.poller")


class PublishSink(Protocol):
    """The bus publish seam.

    Matches ``ComposeIOBus.publish_state`` exactly, so a live bus is a drop-in
    sink and a test recorder is trivial. The poller never imports the bus — it
    depends only on this method, keeping the driver platform-pure.
    """

    def publish_state(self, namespace: str, value: object, source: str = "") -> None:
        ...


def uid_to_hex(uid: bytes) -> str:
    """Canonical UID rendering: lowercase, no separators — ``04abcdef``."""
    return uid.hex()


class NfcPoller:
    """Polls a PN532 for tags and publishes semantic namespaces on change.

    :param reader: a :class:`PN532` (or anything with
        ``read_passive_target_uid`` / ``read_ndef_text``).
    :param sink: a :class:`PublishSink` (e.g. a live ``ComposeIOBus``).
    :param prefix: namespace prefix; defaults to the generic ``"nfc"``.
    :param source: bus ``source`` id for published messages.
    :param read_ndef: also read + publish an NDEF text record when present.
    """

    def __init__(
        self,
        reader: PN532,
        sink: PublishSink,
        prefix: str = "nfc",
        source: str = "nfc-reader",
        read_ndef: bool = False,
    ):
        self.reader = reader
        self.sink = sink
        self.prefix = prefix.rstrip("/")
        self.source = source
        self.read_ndef = read_ndef
        self._present = False
        self._last_uid: str | None = None

    @property
    def ns_present(self) -> str:
        return f"{self.prefix}/present"

    @property
    def ns_uid(self) -> str:
        return f"{self.prefix}/uid"

    @property
    def ns_ndef(self) -> str:
        return f"{self.prefix}/ndef"

    def ensure_namespaces(self) -> None:
        """Register this reader's namespaces with the json engine, if the sink
        supports registration.

        A tag event carries a bool (present), a hex string (uid), and text
        (ndef) — an unknown/mixed value shape. The platform's ``binary`` engine
        speaks 'on'/'off' tokens (not a Python bool) and would reject a hex
        string, so these are registered with the ``json`` engine — exactly how
        the bus itself auto-registers unknown-shape external namespaces
        ([CXC-FL-DISCOVERY]). Duck-typed: a bare :class:`PublishSink` with no
        ``register_namespace`` (e.g. a test recorder) is skipped harmlessly.
        """
        register = getattr(self.sink, "register_namespace", None)
        if register is None:
            return
        register(self.ns_present, engine_config="json")
        register(self.ns_uid, engine_config="json")
        if self.read_ndef:
            register(self.ns_ndef, engine_config="json")

    def begin(self) -> None:
        """Register namespaces and bring up the reader (wakeup + SAMConfig)."""
        self.ensure_namespaces()
        self.reader.begin()

    def poll_once(self) -> str | None:
        """Poll a single time; publish on transition. Returns current uid hex.

        Debounced:
          - arrival (no tag -> tag):     publish present=True, uid=<hex> (+ndef)
          - held (same uid):             no republish
          - swap (uid A -> uid B):       publish new uid (+ndef); present stays True
          - departure (tag -> no tag):   publish present=False
        """
        try:
            uid = self.reader.read_passive_target_uid()
        except PN532Error as exc:
            # A flaky read is not a departure — log and hold last state.
            logger.warning("NFC read error (holding state): %s", exc)
            return self._last_uid

        if uid is None:
            if self._present:
                self._present = False
                self._last_uid = None
                self.sink.publish_state(self.ns_present, False, source=self.source)
                logger.debug("tag departed")
            return None

        uid_hex = uid_to_hex(uid)
        if uid_hex == self._last_uid:
            return uid_hex  # held tag — debounced, no republish

        # Arrival or swap.
        self._last_uid = uid_hex
        if not self._present:
            self._present = True
            self.sink.publish_state(self.ns_present, True, source=self.source)
        self.sink.publish_state(self.ns_uid, uid_hex, source=self.source)
        logger.debug("tag present uid=%s", uid_hex)

        if self.read_ndef:
            try:
                text = self.reader.read_ndef_text()
            except PN532Error as exc:
                logger.warning("NDEF read failed: %s", exc)
                text = None
            if text is not None:
                self.sink.publish_state(self.ns_ndef, text, source=self.source)
        return uid_hex

    def run(self, interval: float = 0.2, iterations: int | None = None) -> None:
        """Poll forever (or *iterations* times) at *interval* seconds."""
        count = 0
        while iterations is None or count < iterations:
            self.poll_once()
            count += 1
            if iterations is not None and count >= iterations:
                break
            time.sleep(interval)
