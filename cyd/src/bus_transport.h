/*
 * bus_transport.h — the ComposeBus TRANSPORT SEAM for the display tier.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER] [SPEC:CXC-BOARD-HW]
 * [CXC-BOARD-HW-UART-SLAVE]  DP-CIO-6.
 *
 * DP-CIO-6: transport is an IMPLEMENTATION DETAIL, not an architectural
 * decision.  An app publishes and subscribes namespaces; it must not know
 * whether they ride MQTT, UART, CAN, HTTP or in-process.
 *
 * The Python tier has had this waist for a long time
 * (python/composeio/transport.py `Transport`), and the firmware did NOT
 * adopt it: main.cpp and qualia_main.cpp called `bus_mqtt_init` /
 * `bus_mqtt_poll` / `bus_mqtt_publish_state` directly — a transport-NAMED
 * surface, so a WiFi->UART switch was a code change in the app shell.
 * This header is the adoption.  Above it, nothing names a substrate.
 *
 * TWO DELIBERATE DIFFERENCES FROM THE PYTHON ABC.  They are correct for a
 * leaf node and are not gaps to be closed later:
 *
 *   - Python DISPATCHES (`subscribe(ns, callback)`); the MCU POLLS
 *     (`poll(BusEvent*)`).  Arrivals land on a driver task (esp-mqtt's
 *     event task, the UART rx task) and are queued; the render task drains
 *     them.  That keeps ALL widget / reactive-core mutation on one task
 *     with no locks and no callback reentrancy — the property the R3
 *     design has depended on since bus_mqtt.cpp was written.
 *   - Python's `subscribe_all` (wildcard) has no use on a leaf: a display
 *     wants the namespaces its scene binds and nothing else.  Omitted.
 *
 * `bus_transport()` is the ONE transport for this boot, resolved exactly
 * like `board_profile()` resolves the ONE display driver — except that the
 * choice may also come from NVS at runtime (see bus_transport_select.cpp),
 * because on an embedded node the scene YAML ARRIVES OVER the transport
 * and therefore cannot be where the transport is chosen
 * ([CXC-BOARD-HW-UART-SLAVE] bootstrap-trap).
 */

#ifndef BUS_TRANSPORT_H
#define BUS_TRANSPORT_H

#include <cstddef>
#include <cstdint>

#include "bus_wire_codes.h"

/* One decoded arrival (mirrors R1's CoreValue kinds).  A JSON array/object
 * `val` (e.g. the dradis/contacts contact array) arrives as Kind::String
 * carrying the raw JSON text verbatim — the consuming widget parses it.
 *
 * str capacity is TIERED: the Qualia framebuffer tier renders the DRADIS
 * contacts layer and needs a contract-sized contact array (~16 contacts;
 * the 16-deep arrival queue then holds ~18 KB — fine beside 8 MB PSRAM +
 * S3 SRAM).  The PSRAM-less band tier keeps the original 96 (its widget
 * set consumes no structured states; oversized ones are warn-dropped). */
#ifdef QUALIA_DISPLAY
#define BUS_EVENT_STR_CAP 1024
#else
#define BUS_EVENT_STR_CAP 96
#endif

struct BusEvent {
    enum class Kind : uint8_t { Number, Bool, String };

    char ns[96];
    Kind kind;
    double num;
    bool b;
    char str[BUS_EVENT_STR_CAP];

    /* WHY type AND scope ARE HERE (2026-08-12).  This struct used to be
     * {ns, kind, value} only — which is MQTT-shaped, because the MQTT
     * transport recovers the type from a JSON field and the scope from a
     * topic segment and then throws both away.  A FRAMED substrate cannot:
     * on UART/CAN the 4-bit type code and the 2-bit scope are literally
     * bits in the header ([BP-UART-FRAME]), and a transport that cannot
     * carry them cannot round-trip an ACK or a HEARTBEAT — the exact
     * defect [BP-TYPE-CODES] exists to prevent.  Carrying them here is
     * what makes the struct substrate-independent rather than MQTT's
     * internal shape wearing a general name.
     *
     * Today every arrival that reaches the scene is a State (both
     * transports filter to state traffic before queueing, as they always
     * have); these fields record what the wire said rather than what the
     * consumer assumes. */
    BusMessageType type;
    char scope[12];         /* "global" / "local" / "remote" / "auto" */

    /* THE PUBLISHER SAID `unknown` — wire tag 0xFD, ZERO-length payload
     * ([CXC-FRESH-WIRE]).  "I am alive and I know that I do not know."
     *
     * ⛔ WHY IT IS A FLAG ON THE ENVELOPE AND *NOT* A FOURTH `Kind`.  On the
     * Python tier unknown is a member of the VALUE DOMAIN precisely so no
     * signature changes ([CXC-FRESH-UNKNOWN-TYPE]).  Here that argument
     * INVERTS.  `Kind` is switched on in the drain, in gauge_main's dispatch
     * and in main.cpp's log, and two of those switches have a `default:` that
     * coerces to a number — so a fourth Kind would arrive at a needle as the
     * value 0.0.  That is the EXACT lie this mechanism exists to stop, one
     * layer lower: Python measured 22 of 52 widgets swallowing unknown into a
     * retained value, and a C++ `default:` swallows it into a REAL ZERO, which
     * is worse because a zeroed gauge is indistinguishable from a true zero.
     *
     * So an unknown NEVER reaches a consumer as a value at all.  The drain's
     * control-plane sieve routes it to the freshness receiver, which
     * republishes `<ns>/_fresh = 0` — and every consumer reacts through the
     * declarative binding it already had.  `kind`/`num`/`str` are UNSET when
     * this is true; a reader that ignores the flag reads a zero, which is why
     * the sieve consumes the event before any such reader exists. */
    bool unknown;
};

/* The transport contract.  One implementation is SELECTED per boot. */
class BusTransport {
public:
    virtual ~BusTransport() = default;

    /* Human-readable selector name — "mqtt", "uart", "can".  Same string the
     * NVS key and (on the Python side) a scene's `transports: type:` uses. */
    virtual const char* name() const = 0;

    /* DOES THIS SUBSTRATE NEED AN IP STACK?  ([SPEC:CXC-CONFIG-BUS]
     * [CXC-CONFIG-BUS-INTENT])
     *
     * Pure virtual on purpose — no default.  This is the question that makes
     * `wifi_init()` conditional, and the whole point of the UART/CAN work is
     * that a slaved board stops taking a DHCP lease.  A default of `true`
     * would let a future transport quietly bring the radio up, which is
     * exactly the failure the seam was built to remove; a default of `false`
     * would silently strand MQTT.  So every transport must answer.
     *
     * It is NOT `connected()` and not a probe: it is a property of the
     * substrate, answerable before start(), because the app has to decide
     * whether to spend ten seconds and ~40 KB on a radio BEFORE it opens the
     * link. */
    virtual bool needs_network() const = 0;

    /* Bring the link up.  client_id must be unique on the far side; pass
     * device_id() (two MQTT peers sharing an id kick each other, rc=7 —
     * the BINDEDIT ops finding).  Returns false when the BACKEND is not
     * available; per transport_registry.py's second rule that is a LOUD
     * degrade, not a fatal error: a down broker is not a broken app. */
    virtual bool start(const char* client_id) = 0;

    virtual void stop() = 0;

    /* MUST STAY CHEAP — the render loop reads it per pass, and Python's
     * ABC carries the same warning for the same reason (a property that
     * performs I/O is a trap for every attribute read).  No probing. */
    virtual bool connected() const = 0;

    /* Add a PERSISTENT subscription that survives scene swaps — the
     * `_device/` restart control namespaces ([CXC-ER-RESTART-BUS]).
     * Copies the string. */
    virtual void subscribe(const char* ns) = 0;

    /* Replace the SCENE subscription set (scene swap).  The set is kept
     * and re-established on every reconnect.  Copies the inputs. */
    virtual void set_subscriptions(const char* const* ns, size_t count) = 0;

    /* Non-blocking drain of ONE queued arrival; false when empty. */
    virtual bool poll(BusEvent* out) = 0;

    /* How many arrivals are waiting.  The render loop uses this to cut a
     * touch-poll burst short: a queued arrival means the scene is NOT
     * idle, and every millisecond spent polling touch first is latency the
     * operator sees on the glass. */
    virtual size_t pending() const = 0;

    /* esp_timer stamp (us) of the arrival that woke an idle queue, 0 when
     * idle.  (paint_time - this) is the bus->glass latency. */
    virtual int64_t pending_since_us() const = 0;

    /* Publish a STRING state.  The display is a full bus peer. */
    virtual void publish_state(const char* ns, const char* value) = 0;

    /* Publish a NUMERIC state — a separate entry point because the two
     * encode differently on every substrate (unquoted JSON here, a raw
     * float32 there) and a string that happens to look numeric must not
     * silently become a number. */
    virtual void publish_number(const char* ns, double value) = 0;

    /* Publish a message of an ARBITRARY type carrying OPAQUE BYTES.
     * ([SPEC:CXC-CONFIG-BUS] — CONFIG_REQUEST, ACK, and the config chunks a
     * relay forwards.)
     *
     * publish_state/publish_number cannot express this: both hard-code
     * BusMessageType::State, and both put a VALUE ENCODING between the caller
     * and the wire.  A config chunk is a byte string with its own internal
     * framing ([seq(2)][content...]); a second length prefix, or a 4-byte
     * chunk being helpfully decoded as a float32, corrupts it.
     *
     * Returns FALSE when this substrate cannot carry the message — an
     * unframed transport has no type field at all, and an oversized value is a
     * refusal, never a truncation.  Every caller must treat false as "it did
     * not go", because a config transfer that silently loses a frame is
     * indistinguishable from a wire fault. */
    virtual bool publish_raw(const char* ns, BusMessageType type,
                             const uint8_t* value, size_t len) = 0;

    /* Largest opaque value publish_raw() will carry on this substrate.  The
     * config chunker asks BEFORE it chunks — that is how one chunk format
     * serves UART's 252 bytes and classic CAN's 8 without either number being
     * baked into the format ([CXC-CONFIG-BUS-BUDGET]). */
    virtual size_t max_raw_value() const = 0;

    /* THE ONE GENERIC HEALTH FACT — the substrate's own error state, summarised
     * to the five values in announce_pack.h (kAnnLink*).  Fed into
     * `_device/<id>/health` by announce.cpp, which therefore never names a
     * substrate ([SPEC:CXC-BOARD-HW] H2a, DP-CIO-6).
     *
     * NOT PURE VIRTUAL, and the default is UNKNOWN rather than OK.  That
     * asymmetry is the whole point.  `needs_network()` is pure virtual because
     * a wrong answer strands a board; here a substrate genuinely may have NO
     * error model (a UART port being open says nothing about the far end), and
     * "I have no error model" is a true and useful answer.  What is NOT
     * acceptable is a default of OK: that is precisely the defect H2 records —
     * `connected()` returning `s_started`, so a BUS-OFF ESP32 reported
     * CONNECTED forever.  Ask of any instrument "what would it show if the
     * thing were broken?"  An OK default shows OK.  UNKNOWN shows a dash, and
     * a dash is not a clean bill of health.
     *
     * MUST STAY CHEAP for the same reason connected() must: it is sampled once
     * per announcement, but nothing stops a future caller from sampling it per
     * render pass.  A register read is fine; a probe is not.
     *
     * The literal 0 is `kAnnLinkUnknown` (announce_pack.h).  It is spelled as a
     * literal so the transport SEAM does not take a dependency on the
     * ANNOUNCEMENT encoding — the layering runs one way.  announce.cpp
     * static_asserts that the two still agree. */
    virtual uint8_t link_health() const { return 0; }

    /* True iff this substrate's own error counters are non-zero — the
     * `kAnnFlagLinkErrors` bit.  Separate from link_health() because "errors
     * have occurred" and "the link is currently bad" are different facts: a
     * CAN node that recovered from a burst is Ok WITH history, and collapsing
     * the two would hide exactly the intermittent fault H1 is about. */
    virtual bool link_has_errors() const { return false; }

    /* True iff this substrate is RECEIVE-ONLY — the `kAnnFlagListenOnly` bit.
     * Today only TWAI listen-only mode, which is a WORKAROUND for a dead
     * transceiver and not a design; a board in it publishes nothing, so a
     * fleet report that did not say so would show a silent device and no
     * reason. */
    virtual bool link_listen_only() const { return false; }
};

/* ------------------------------------------------------------------ */
/* Selection ([CXC-BOARD-HW-UART-SLAVE] bootstrap-trap)                */
/* ------------------------------------------------------------------ */

/* The transport for this boot.  Resolved once, on first call:
 *   1. the NVS key `transport` in namespace `composeio` (settable over the
 *      CURRENT link, so a board is flipped to UART by a bus command and
 *      THEN rewired — no reflash, no circularity), else
 *   2. the compile-time default CXC_BUS_TRANSPORT_DEFAULT ("mqtt").
 *
 * Mirrors transport_registry.py's two failure rules:
 *   - an UNKNOWN name is FATAL and LOUD.  It never degrades to the default
 *     — a mistyped `mqqt` that quietly kept working over MQTT would be a
 *     device lying about its own configuration.  See bus_transport_select.
 *   - a KNOWN name whose backend is unreachable degrades LOUDLY (start()
 *     returns false, the app keeps running and keeps retrying).
 */
BusTransport& bus_transport();

/* The name that was actually selected, and where it came from ("nvs" or
 * "default").  For the boot banner — a device must be able to SAY which
 * substrate it is on. */
const char* bus_transport_selected_name();
const char* bus_transport_selection_source();

/* Persist a transport selection for the NEXT boot.  Returns false and
 * stores NOTHING for an unknown name — validating on WRITE is what keeps
 * the fatal-on-read path practically unreachable, and it means a bus
 * command carrying a typo is rejected at the moment it is issued instead
 * of bricking the device on its next boot. */
bool bus_transport_set_selection(const char* name);

/* Every transport this build can select, ' '-separated (for the error
 * message that a wrong name produces — mirrors registry.known_types()). */
const char* bus_transport_known_names();

/* ---- the control namespace that closes the bootstrap trap ---- */
/*
 * `_device/<id>/transport` — publish "uart" (or "mqtt") on the CURRENT
 * link and the device stores it in NVS for its NEXT boot.  That is the
 * whole answer to [CXC-BOARD-HW-UART-SLAVE]'s bootstrap-trap: the scene
 * YAML arrives OVER the transport, so the transport cannot be chosen in
 * the scene YAML.  Flip a board to UART over WiFi, then rewire and power
 * cycle — no reflash, no circularity, and the same namespace shape as
 * `_device/<id>/restart` ([CXC-ER-RESTART-BUS]).
 *
 * It does NOT reboot: rebooting before the operator has moved the wires
 * would strand the board.  An UNKNOWN name is REJECTED and NOT stored —
 * validating on write is what keeps the fatal-on-read path unreachable in
 * practice.
 */
void bus_transport_control_init(const char* device_id);

/* Fill out[] with this device's transport-control namespace(s) and return
 * the count.  The caller subscribes each as an EXACT subscription. */
size_t bus_transport_control_namespaces(const char** out, size_t cap);

/* Handle a decoded arrival; true iff it was a transport-control command
 * and was CONSUMED here (must not be routed to the scene).  kind mirrors
 * BusEvent::Kind (0=Number, 1=Bool, 2=String). */
bool bus_transport_on_bus_value(const char* ns, int kind, double num,
                                bool b, const char* str);

#endif /* BUS_TRANSPORT_H */
