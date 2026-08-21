/*
 * bus_mqtt.h — the MQTT implementation of the ComposeBus transport seam.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER]  DP-CIO-6.
 *
 * The ESP-IDF sibling of R1's Arduino PubSubClient shell
 * (firmware/reactive_core/src/main.cpp): same wire format — BusMessage
 * JSON over composeio/{scope}/{namespace}
 * (python/composeio/transport.py to_json / transport_mqtt.py) — same loop
 * guard (never re-ingest our own src), same state-only filter.  Devices
 * ride the shared ecosystem broker on :1883.
 *
 * Threading: esp-mqtt delivers events on its own task; arrivals are queued
 * and drained by the main loop (BusTransport::poll) so ALL widget / core
 * mutation stays on the render task.
 *
 * NOTHING ABOVE THE SEAM INCLUDES THIS HEADER.  main.cpp / qualia_main.cpp
 * hold a `BusTransport&` from bus_transport(); only the selector
 * (bus_transport_select.cpp) names MQTT.  That is the whole point of
 * DP-CIO-6: the app shell must not be able to tell which substrate it is
 * on.  BusEvent moved to bus_transport.h for the same reason — it was
 * declared here, in a transport-named header, while being the one struct
 * every transport must produce.
 */

#ifndef BUS_MQTT_H
#define BUS_MQTT_H

#include "bus_transport.h"

/* The MQTT transport singleton.  One per boot; constructing it does NOT
 * touch the network — start() does. */
BusTransport& bus_mqtt_transport();

#endif /* BUS_MQTT_H */
