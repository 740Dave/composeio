/*
 * bus_uri.h — PURE, header-only split of a ComposeBus endpoint URI into a
 * host + port for a host+port API (PubSubClient::setServer, which does NOT
 * take a URI).  [SPEC:CXC-REPOINT]
 *
 * The Arduino bus_binding glue (bus_binding.cpp) stores the active MQTT
 * broker as a `mqtt://host:port` URI — the SAME record shape the ESP-IDF
 * cyd/ glue persists — but PubSubClient wants host + port separately.  This
 * is the one scrap of pure logic the Arduino glue adds over the host-tested
 * bus_binding_core, so it lives here where a native positive-control test can
 * reach it (bus_uri_selftest.cpp), not inlined in the shell where nothing
 * off-hardware could exercise it ([CXC-VISION-EPISTEMICS]).
 *
 * Accepts `scheme://host:port`, `host:port`, or a bare `host` (port then
 * falls to default_port).  A path after the host is ignored.  Allocation-free.
 */
#ifndef BUS_URI_H
#define BUS_URI_H

#include <cstddef>
#include <cstdint>
#include <cstdlib>

/* Split `uri` into host[cap] + *port.  *port is default_port unless an
 * explicit `:port` follows the host.  host is always NUL-terminated. */
static inline void bus_uri_host_port(const char* uri, char* host, size_t cap,
                                     uint16_t* port, uint16_t default_port) {
    if (port) *port = default_port;
    if (cap) host[0] = '\0';
    if (!uri || !cap) return;

    /* skip an optional scheme:// prefix */
    const char* p = uri;
    for (const char* s = uri; *s; s++) {
        if (s[0] == ':' && s[1] == '/' && s[2] == '/') { p = s + 3; break; }
    }

    /* host runs to the FIRST ':' (port), '/' (path), or end */
    size_t i = 0;
    while (*p && *p != ':' && *p != '/' && i + 1 < cap) host[i++] = *p++;
    host[i] = '\0';

    /* a ':' here is the port separator (the host loop stopped at the first
     * ':' or '/', so this cannot be a colon buried in a path) */
    if (*p == ':') {
        long v = strtol(p + 1, nullptr, 10);
        if (v > 0 && v < 65536 && port) *port = (uint16_t)v;
    }
}

#endif /* BUS_URI_H */
