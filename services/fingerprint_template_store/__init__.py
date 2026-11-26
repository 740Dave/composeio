"""fingerprint_template_store — the DEDICATED biometric template blob store.

Holds raw fingerprint template bytes, keyed by an OPAQUE service-private id
([DD-FPID-TEMPLATE-REF-IS-SERVICE-PRIVATE] — a uuid minted by
``fingerprint_service.RosterPublisher``, never a path built from
person/ordinal/module). A `fingerprint://<opaque-id>` reference resolves here
via :mod:`composeio.resolve`, never through ``bus_storage``.

⛔ THE SEPARATION IS THE WHOLE POINT. This is a DIFFERENT service — different
module, different port (:8422, never bus_storage's :8420), different data
directory (:data:`DEFAULT_DATA_DIR`, never bus_storage's `assets/`) — per
David's ruling that biometric templates must be stored separately from
bus_storage. It is deliberately NOT a re-skinned bus_storage instance: a
template row here has no path hierarchy, no content-type guessing, no asset
listing — it is a flat opaque-id -> bytes map, and that narrower shape is
itself part of keeping this store legibly biometric-only.

[SPEC:CXC-FPSVC], the closing half of
[DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]: the roster push used to
carry raw template `bytes` inline, which a JSON bus transport cannot serialise
(DP-CIO-18). The fix carries a `fingerprint://` REFERENCE on the wire instead;
this module is the store that reference resolves against.
"""

from __future__ import annotations
