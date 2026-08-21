/*
 * bus_transport_select.h — which substrate does this boot ride? (reactive_core)
 * [SPEC:CXC-BOARD-HW] [CXC-BOARD-HW-UART-SLAVE] DP-CIO-6.
 *
 * The reactive_core port of cyd/src/bus_transport_select.cpp's SELECTION
 * pattern — the NVS key `transport` in namespace `composeio`, the two failure
 * rules (unknown = loud-not-silent; known-but-unreachable = a loud degrade),
 * and the "settable over the current link, takes effect next boot" idiom.
 *
 * ⛔ TWO DELIBERATE DIFFERENCES FROM THE CYD VERSION, both required by this
 * dispatch's mandate ("build the bus endpoint as a CONFIG value ... defaulting
 * to unset/offline ... do NOT hardcode a broker; do NOT attempt WiFi/MQTT"):
 *   1. THE DEFAULT IS "offline", not "mqtt".  An unset device carries no
 *      traffic on any substrate and brings up no radio — the live-bus join is
 *      a later, deliberate step needing creds + a confirmed-live broker.
 *   2. THE SELECTION LOGIC IS PURE + HOST-TESTABLE.  The cyd version links
 *      bus_can/bus_mqtt/bus_uart and the ESP NVS/log APIs directly; here the
 *      resolve/validate core is pure C++ (proven in env:native_test) and the
 *      NVS read/write lives behind #ifdef ESP_PLATFORM.  Nothing above this
 *      names a substrate.
 */
#ifndef BUS_TRANSPORT_SELECT_H
#define BUS_TRANSPORT_SELECT_H

#include <string>

/* The compile-time default.  "offline" everywhere today: no broker is baked,
 * and no network comes up until a later, deliberate selection. */
#ifndef CXC_BUS_TRANSPORT_DEFAULT
#define CXC_BUS_TRANSPORT_DEFAULT "offline"
#endif

struct TransportSelection {
    std::string name;     /* the resolved selector: "offline"/"mqtt"/"uart"/"can" */
    std::string source;   /* "nvs" or "default"                                   */
    bool known = false;   /* is `name` a transport this build knows?              */
    bool needs_network = false;   /* does the selected substrate need an IP stack? */
};

/* Every transport this build can select, ' '-separated (mirrors the cyd
 * registry + Python registry.known_types()).  "offline" is a member. */
const char* transport_known_names();

/* Is `name` a known transport name? */
bool transport_name_known(const char* name);

/* Does the named substrate need an IP stack?  "offline" and "uart"/"can" do
 * not; "mqtt" does.  Unknown -> false (a misconfigured board must NOT quietly
 * bring the radio up). */
bool transport_needs_network(const char* name);

/* PURE resolution — the host-testable core.  `nvs_value` is the raw NVS string
 * (nullptr or "" when the key is unset).  An unset key -> the compile-time
 * default ("offline"); a set key is taken verbatim and validated.  An UNKNOWN
 * name does NOT fall back to a working transport (that would be a device lying
 * about its own configuration); it resolves with known=false so the caller can
 * refuse loudly. */
TransportSelection transport_select_resolve(const char* nvs_value);

/* The transport selected for THIS boot, resolved once from NVS (ESP) or the
 * default.  On host this is the default ("offline"). */
const TransportSelection& bus_transport_selection();

/* Persist a selection for the NEXT boot.  Returns false and stores NOTHING for
 * an unknown name — validating on write keeps the fatal-on-read path
 * unreachable in practice, and rejects a bus command carrying a typo at the
 * moment it is issued.  On host this is a no-op returning validity only. */
bool bus_transport_set_selection(const char* name);

#endif /* BUS_TRANSPORT_SELECT_H */
