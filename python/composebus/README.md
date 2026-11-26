# composebus

The ComposeBus kernel: the bare, transport-agnostic ComposeIO bus
(`ComposeIOBus`, namespaces, state engines, the transport contract, the
in-process local transport, the plugin contract and the five default plugins a
bare bus installs). One runtime dependency: `pydantic>=2.0`.

```python
from composebus.bus import ComposeIOBus

bus = ComposeIOBus(bus_id="demo")
bus.start()
```

Normative spec: `[SPEC:CXC-COMPOSEBUS-KERNEL]` §7 (the composebus distribution).
Inside the ComposeIO monorepo every `composeio.<module>` for a moved module is
the identical module object `composebus.<module>`.

Tests: `pip install ./python/composebus[test]` then `pytest python/composebus/tests`.
