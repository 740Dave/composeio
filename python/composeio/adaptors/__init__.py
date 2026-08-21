"""ComposeIO output adaptors — bus-native drivers that subscribe semantic
namespaces and actuate external systems.

[CXC-HAL-ACTUATE]: the HAL's symmetric OUTPUT side. An adaptor is a Tier-2
output driver: it consumes SEMANTIC namespaces off ComposeBus and maps them to
some device's native protocol. Nothing here runs on the device — these are
pure client adaptors ("define once, actuate many").
"""
