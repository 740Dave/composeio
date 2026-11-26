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

/* ======================================================================== *
 * [SPEC:CXC-DEVICE-BASELINE] cap 6d + capability gate — the ROLE axis.
 *
 * The SAME NVS/board_profile idiom as the substrate axis above, NEVER a second
 * bespoke mechanism ([CXC-DEVICE-BASELINE-TRANSPORT-SELECT-RUNTIME]
 * why-nothing-per-build): a board's ROLE (standalone / mothercard /
 * daughtercard) and its UART PINS are resolved at runtime from config/NVS with
 * a board_profile default, so "build standalone, later slave as a daughtercard"
 * is a config change, ZERO reflash either side.  Every function here is PURE +
 * host-tested; the NVS read/write lives behind #ifdef ESP_PLATFORM.
 * ======================================================================== */

/* The board's ROLE for a UART/mothercard topology.  standalone = rides the bus
 * directly (today's default); mothercard = holds an upstream transport AND a
 * downstream UART, relaying between them ([CXC-DEVICE-BASELINE-RELAY-MEMBRANE]);
 * daughtercard = slaved over UART to a mothercard, no direct bus. */
#ifndef CXC_TRANSPORT_MODE_DEFAULT
#define CXC_TRANSPORT_MODE_DEFAULT "standalone"
#endif

struct TransportMode {
    std::string name;      /* "standalone" / "mothercard" / "daughtercard"   */
    std::string source;    /* "nvs" or "default"                             */
    bool known = false;    /* is `name` a role this build knows?             */
    bool needs_capability = false;  /* mothercard needs the capability gate  */
};

/* All roles, ' '-separated. */
const char* transport_mode_known_names();
bool transport_mode_name_known(const char* name);

/* PURE resolution — unset NVS -> the compile-time default ("standalone"); a set
 * value is taken verbatim and validated.  An UNKNOWN role does NOT fall back to
 * a working one (known=false) so the caller can refuse loudly — identical
 * discipline to transport_select_resolve. */
TransportMode transport_mode_resolve(const char* nvs_value);

/* --- UART pins, resolved NVS-over-board_profile-default ------------------- */
struct UartPins {
    int tx;                /* resolved TX gpio (-1 == unresolved/none)        */
    int rx;                /* resolved RX gpio                                */
    std::string source;    /* "nvs" (either pin came from NVS) or "profile"   */
};

/* Resolve the UART pins: an NVS override (>= 0) wins per-pin over the
 * board_profile default; a negative NVS value means "unset, use the profile
 * default".  `source` is "nvs" iff EITHER pin was overridden. */
UartPins uart_pins_resolve(int nvs_tx, int nvs_rx,
                           int profile_tx, int profile_rx);

/* --- The capability gate ([CXC-DEVICE-BASELINE-CAPABILITY-THRESHOLD]) ------
 *
 * ⛔ NEVER a chip allowlist, NEVER "am I an ESP32" (david-ruling): a device
 * tests its OWN declared resources — "do I have the room to host the relay +
 * the full baseline?".  ESP32-class and RP2040 CLEAR it; the CH32V003 sub-engine
 * floor FAILS it (relayed daughtercard by construction).  Those are EXAMPLES,
 * not the definition — the definition is the resource comparison below. */

/* ⚠ PLACEHOLDER THRESHOLD — the spec deliberately does NOT state a numeric
 * cutover ([CXC-DEVICE-BASELINE-CAPABILITY-THRESHOLD] threshold-definition-
 * status: "left for the architect's audit or a follow-on decision, so this
 * baseline does not silently over-specify a boundary David did not state
 * numerically").  These are a DEFENSIBLE FIRST CUT chosen only so the predicate
 * MECHANISM is testable: comfortably above the CH32V003 floor (2 KB RAM /
 * 16 KB flash) and comfortably below ESP32 / RP2040 (>= 264 KB RAM, >= 2 MB
 * flash).  The architect OWNS the final number; it is one edit here. */
#ifndef CXC_BASELINE_MIN_RAM_KB
#define CXC_BASELINE_MIN_RAM_KB 64
#endif
#ifndef CXC_BASELINE_MIN_FLASH_KB
#define CXC_BASELINE_MIN_FLASH_KB 512
#endif

/* True iff a device with `ram_kb` RAM and `flash_kb` flash has the room to host
 * the relay + the full baseline (mothercard-capable).  Pure comparison against
 * the declared resources — the board_profile supplies the numbers, this never
 * looks at a chip id.  A non-positive resource (unknown/undeclared) FAILS
 * CLOSED: an undeclared board is treated as a daughtercard, never silently
 * promoted to mothercard. */
bool board_can_host_baseline(int ram_kb, int flash_kb);

/* --- Runtime accessors (ESP: NVS-backed; host: the pure default) ---------- */

/* The role resolved for THIS boot from NVS key `transport_mode` (namespace
 * `composeio`), else the default.  On host: the default ("standalone"). */
const TransportMode& bus_transport_mode();

/* Persist a role for the NEXT boot.  Refuses (stores nothing, returns false) an
 * unknown role — and refuses "mothercard" on a board that FAILS the capability
 * gate, so a device cannot be told to be something its resources cannot host.
 * `ram_kb`/`flash_kb` are the caller's board_profile resources (for the
 * capability check); pass 0/0 to skip it (host / non-mothercard writes). */
bool bus_transport_set_mode(const char* name, int ram_kb, int flash_kb);

/* The UART pins resolved for THIS boot: NVS keys `uart_tx`/`uart_rx` override
 * the board_profile defaults passed in.  On host: the profile defaults. */
UartPins bus_uart_pins(int profile_tx, int profile_rx);

#endif /* BUS_TRANSPORT_SELECT_H */
