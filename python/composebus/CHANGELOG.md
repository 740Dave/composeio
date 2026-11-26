# composebus changelog

## 0.1.1

Fix lost messages under concurrent publish (unlocked `_emitted_fingerprints` trim); no API change.

When several threads called `publish_state` (or any `publish_*`) on one bus with a global or remote connector, the
self-echo memory was trimmed without a lock. Two threads could delete the same oldest entry (`KeyError`), or one could
iterate while another inserted (`RuntimeError`). The exception left `publish_*` after the local state update and
before the connector send, so that message never reached the transport. The memory is now guarded by a lock.

## 0.1.0

First release: the ComposeBus kernel as a standalone package.
