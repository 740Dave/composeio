/*
 * bus_can.cpp — ComposeBus over the ESP32 TWAI (classic CAN 2.0B) peripheral.
 * See bus_can.h for the wire layout and the three degradations it declares.
 *
 * Shape deliberately mirrors bus_uart.cpp, which mirrors bus_mqtt.cpp: a
 * driver task decodes arrivals and pushes BusEvent onto a queue; the render
 * task drains it.  ALL widget and reactive-core mutation therefore stays on
 * one task, and the app shell cannot tell the three substrates apart.
 */

#include "bus_can.h"
#include "bus_frame.h"
#include "announce_pack.h"
#include "config_bus.h"
#include "restart.h"

#include <cstdio>
#include <cstring>

#include "driver/twai.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "nvs.h"

static const char* TAG = "bus_can";

/* The [BP-CAN-ID] arbitration-id layout lives in bus_can_id.h so it can be
 * host-parity-tested against services/can_mqtt_bridge (env:native_can_id) —
 * the id is pure arithmetic, and it is exactly the part a board with no CAN
 * hardware attached can get wrong invisibly. */
#include "bus_can_id.h"

/* ------------------------------------------------------------------ */
/* Wiring — CONFIGURABLE, not baked (same contract as bus_uart.cpp)    */
/* ------------------------------------------------------------------ */
/*
 * NO per-board #if HERE, and that is deliberate — see the identical note in
 * bus_uart.cpp.  HOSYOND_S3 / WAVESHARE_C6 are CMake CACHE VARIABLES that pick
 * a branch in src/CMakeLists.txt and NEVER reach the compiler, so a
 * `#if defined(HOSYOND_S3)` here would be dead code that silently took the -1
 * branch while looking correct.  Board pins are target_compile_definitions in
 * each board's CMake branch; this file supplies only the "unconfigured"
 * fallback, which fails LOUDLY rather than inventing pins.
 */
#ifndef CXC_CAN_TX_GPIO
#define CXC_CAN_TX_GPIO (-1)
#endif
#ifndef CXC_CAN_RX_GPIO
#define CXC_CAN_RX_GPIO (-1)
#endif
#ifndef CXC_CAN_BITRATE
#define CXC_CAN_BITRATE 500000
#endif

/* ⚠ LISTEN-ONLY IS A WORKAROUND FOR A BROKEN PART, NOT A DESIGN.
 *
 * TWAI_MODE_LISTEN_ONLY never drives the bus — not even the ACK bit — so it
 * never transmits, never accumulates a transmit error and can never reach
 * bus-off.  That is exactly what is needed on the 2026-08-12 bench, where the
 * SN65HVD230's DRIVER is dead while its RECEIVER works: three independent
 * instruments agree (the Pi's `bus-errors` counter unmoved across ten 1.5 s
 * dominant attempts, a differential of -0.6 mV across CANH/CANL with the
 * driver input held low, and identical behaviour from two separate modules),
 * and RECEIVE is proven at 1,136 frames / 500 kbit/s with bus_err=0, verified
 * twice an hour apart.
 *
 * In NORMAL mode on that hardware the node tries to ACK, fails, accumulates
 * TEC and goes BUS-OFF within about five seconds — after which ESP-IDF does
 * NOT auto-recover (see the bus-off-is-invisible-to-the-transport-seam quirk:
 * this file never enables TWAI_ALERT_BUS_OFF and never calls
 * twai_initiate_recovery()), so it stops RECEIVING too.  Listen-only listens
 * indefinitely.
 *
 * DEFAULT 0 EVERYWHERE.  It is opt-in per board via
 * target_compile_definitions, overridable at runtime by NVS `can.listen`, and
 * every boot announces it in plain words at ERROR level.  It must never
 * become a silent default anywhere: a node that cannot transmit and does not
 * SAY so is a node whose publishes vanish with no error at all. */
#ifndef CXC_CAN_LISTEN_ONLY
#define CXC_CAN_LISTEN_ONLY 0
#endif

#define CXC_NVS_NAMESPACE "composeio"

/* This node's ALLOCATED id for the [BP-CAN-ID] `src` field, 1..15.
 *
 * DEFAULT 0 = UNASSIGNED, and that is the truth rather than a fallback: nothing
 * allocates node ids yet (workstream 0), and two unassigned nodes should be
 * visibly indistinguishable in a capture rather than accidentally distinct.
 * v1 derived this field from a hash of the device id, which made every board
 * look allocated and none of them actually were. */
#ifndef CXC_CAN_NODE_ID
#define CXC_CAN_NODE_ID 0
#endif

static int32_t s_tx = CXC_CAN_TX_GPIO;
static int32_t s_rx = CXC_CAN_RX_GPIO;
static int32_t s_bitrate = CXC_CAN_BITRATE;
static int32_t s_listen_only = CXC_CAN_LISTEN_ONLY;
static int32_t s_node_id = CXC_CAN_NODE_ID;
static char s_src_of[6] = "ddddd";     /* d = compiled default, n = NVS */

static void load_config(void) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return;
    struct { const char* key; int32_t* dst; int idx; } fields[] = {
        { "can.tx",      &s_tx,      0 },
        { "can.rx",      &s_rx,      1 },
        { "can.bitrate", &s_bitrate, 2 },
        /* Settable in BOTH directions over the current link, so a replacement
         * transceiver restores normal mode without a reflash — and so nobody
         * has to remember that a bench workaround was ever compiled in. */
        { "can.listen",  &s_listen_only, 3 },
        /* The allocated node id.  NVS so a board can be given one without a
         * reflash, which is the whole point of allocating rather than hashing. */
        { "can.node",    &s_node_id, 4 },
    };
    for (auto& f : fields) {
        int32_t v;
        if (nvs_get_i32(h, f.key, &v) == ESP_OK) {
            *f.dst = v;
            s_src_of[f.idx] = 'n';
        }
    }
    nvs_close(h);

    /* REFUSED, NOT MASKED.  `& 0xF` would turn node 16 into node 0 and node 17
     * into node 1 — two boards claiming one id, diagnosable only from a
     * capture.  An out-of-range value leaves this node UNASSIGNED and says so. */
    if (s_node_id < 0 || s_node_id > (int32_t)kBusCanNodeMax) {
        ESP_LOGE(TAG, "can.node = %ld is outside 0..%u ([BP-CAN-ID] src is 4 "
                 "bits) — this node stays UNASSIGNED (src=0). It is NOT masked: "
                 "16 would silently become 0.",
                 (long)s_node_id, (unsigned)kBusCanNodeMax);
        s_node_id = kBusCanNodeUnassigned;
        s_src_of[4] = 'x';
    }
}

bool bus_can_set_config(int32_t tx, int32_t rx, int32_t bitrate) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed — CAN wiring NOT stored");
        return false;
    }
    esp_err_t e = ESP_OK;
    if (tx >= 0) e |= nvs_set_i32(h, "can.tx", tx);
    if (rx >= 0) e |= nvs_set_i32(h, "can.rx", rx);
    if (bitrate > 0) e |= nvs_set_i32(h, "can.bitrate", bitrate);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs write failed — CAN wiring NOT stored");
        return false;
    }
    ESP_LOGW(TAG, "CAN wiring stored (tx=%ld rx=%ld bitrate=%ld; negative = "
             "unchanged) — takes effect on the NEXT boot",
             (long)tx, (long)rx, (long)bitrate);
    return true;
}

bool bus_can_set_listen_only(bool listen_only) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed — CAN mode NOT stored");
        return false;
    }
    esp_err_t e = nvs_set_i32(h, "can.listen", listen_only ? 1 : 0);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs write failed — CAN mode NOT stored");
        return false;
    }
    ESP_LOGW(TAG, "CAN mode stored: %s — takes effect on the NEXT boot",
             listen_only ? "LISTEN-ONLY (receive only, never ACKs)"
                         : "NORMAL (transmits and ACKs)");
    return true;
}

bool bus_can_is_listen_only(void) { return s_listen_only != 0; }

/* ENFORCED IN CODE, NOT IN A COMMENT.  TWAI's timing tables only exist for a
 * fixed set of bit rates; an unsupported one must be refused at start() rather
 * than silently becoming something else on the wire, because a bus running at
 * the wrong rate looks exactly like a bus with no peers. */
static bool timing_for(int32_t bitrate, twai_timing_config_t* out) {
    switch (bitrate) {
        case 1000000: { twai_timing_config_t t = TWAI_TIMING_CONFIG_1MBITS();  *out = t; return true; }
        case 800000:  { twai_timing_config_t t = TWAI_TIMING_CONFIG_800KBITS(); *out = t; return true; }
        case 500000:  { twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS(); *out = t; return true; }
        case 250000:  { twai_timing_config_t t = TWAI_TIMING_CONFIG_250KBITS(); *out = t; return true; }
        case 125000:  { twai_timing_config_t t = TWAI_TIMING_CONFIG_125KBITS(); *out = t; return true; }
        case 100000:  { twai_timing_config_t t = TWAI_TIMING_CONFIG_100KBITS(); *out = t; return true; }
        case 50000:   { twai_timing_config_t t = TWAI_TIMING_CONFIG_50KBITS();  *out = t; return true; }
        case 25000:   { twai_timing_config_t t = TWAI_TIMING_CONFIG_25KBITS();  *out = t; return true; }
        default: return false;
    }
}

static constexpr int kMaxSubs = 16;
/* EIGHT — see the note on the same constant in bus_uart.cpp.  Four was one
 * short of the control plane's real size and silently dropped
 * `_device/<id>/config`, the one namespace a leaf cannot boot without. */
static constexpr int kMaxPersist = 8;
static constexpr int kQueueDepth = 16;
static constexpr int kRxTaskStack = 4096;

struct NsEntry {
    char ns[96];
    uint16_t hash;
};
static NsEntry s_subs[kMaxSubs];
static int s_sub_count = 0;
static NsEntry s_persist[kMaxPersist];
static int s_persist_count = 0;

static QueueHandle_t s_queue = nullptr;
static volatile int64_t s_pending_since_us = 0;
static volatile bool s_started = false;

/* VALUES DROPPED FOR HAVING NO BYTES (DLC 0) — its own counter, on purpose.
 * The `BusValueKind::None` path used to `return;` with no log and no counter,
 * so a value THIS LEAF DISCARDED and a value the publisher NEVER SENT were
 * indistinguishable to every instrument the board has.  ⛔ Deliberately NOT
 * merged into the link-health/error path: a zero-length State is a legitimate
 * message, not a bus fault, and counting it as one would make a healthy
 * segment report errors. */
static volatile uint32_t s_rx_empty_values = 0;
static TaskHandle_t s_rx_task = nullptr;

static BusRawFrameFn s_raw_sink = nullptr;
static void* s_raw_ud = nullptr;

void bus_can_set_raw_sink(BusRawFrameFn fn, void* ud) {
    s_raw_sink = fn;
    s_raw_ud = ud;
    ESP_LOGW(TAG, "raw frame sink installed — this node now forwards frames "
             "it does not subscribe (relay mode)");
}

static const char* ns_for_hash(uint16_t h) {
    for (int i = 0; i < s_sub_count; i++)
        if (s_subs[i].hash == h) return s_subs[i].ns;
    for (int i = 0; i < s_persist_count; i++)
        if (s_persist[i].hash == h) return s_persist[i].ns;
    return nullptr;
}

/* ------------------------------------------------------------------ */
/* rx                                                                  */
/* ------------------------------------------------------------------ */

static void deliver(const twai_message_t& m) {
    uint16_t ns_hash = bus_can_id_ns_hash(m.identifier);
    uint8_t type_code = bus_can_id_type_code(m.identifier);

    BusMessageType type;
    if (!bus_type_for_wire_code(type_code, &type)) {
        /* NEVER guessed as State — [BP-TYPE-CODES] is one table precisely
         * because a `.get(type, 0)` here manufactured state changes that never
         * happened, on this substrate, for eight of ten types. */
        ESP_LOGW(TAG, "id 0x%08lX carries an UNALLOCATED message-type code %u "
                 "(allocated 0..9) — DROPPED, not guessed",
                 (unsigned long)m.identifier, (unsigned)type_code);
        return;
    }

    /* The frame's OWN scope, off the wire. It used to be reconstructed as
     * "global" here because v1 had no scope field, so a relay forwarded every
     * local message on as a global one — the leak [BP-CAN-ID] v2 exists to
     * close. */
    uint8_t scope_bits = bus_can_id_scope_bits(m.identifier);

    if (s_raw_sink)
        s_raw_sink(ns_hash, type, scope_bits,
                   m.data, m.data_length_code, s_raw_ud);

    const char* ns = ns_for_hash(ns_hash);
    if (!ns) {
        ESP_LOGD(TAG, "frame for unregistered ns hash 0x%04X — dropped "
                 "(this device subscribes %d + %d namespaces)",
                 ns_hash, s_sub_count, s_persist_count);
        return;
    }

    /* Config control plane, consumed at RECEIVE time — see the same intercept
     * in bus_uart.cpp for why reassembly belongs on the rx task.
     *
     * ⛔ THE ORDERING IS ALSO A CORRECTNESS CONSTRAINT NOW.  A config chunk is
     * OPAQUE BYTES, and a DLC=1 chunk whose single byte is 0xFD is
     * byte-identical to an encoded `unknown`.  It is safe TODAY only because
     * this line runs BEFORE bus_value_decode(); move it after and that chunk
     * silently becomes an UNKNOWN and the transfer loses a frame.  The
     * reordering would look free — nothing in the types or the tests says
     * otherwise — so the constraint is recorded where a refactorer stands. */
    if (config_bus_on_frame(ns, type, m.data, m.data_length_code)) return;

    /* State traffic PLUS heartbeats — see the long note at the same line in
     * bus_uart.cpp.  Dropping non-State here is what made NODE_SILENT
     * structurally unreachable ([CXC-FRESH-HEARTBEAT-RX]); the drain's
     * control-plane sieve consumes the beat before the scene ever sees it. */
    if (type != BusMessageType::State &&
        type != BusMessageType::Heartbeat) {
        ESP_LOGD(TAG, "%s: non-state type %u — ignored", ns, (unsigned)type);
        return;
    }

    static BusEvent ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type;
    snprintf(ev.ns, sizeof(ev.ns), "%s", ns);
    /* Scope CROSSES this substrate now ([BP-CAN-ID] v2, bits 1..0).  Until
     * 2026-08-13 this line hardcoded "global" because the layout had no scope
     * field, so every CAN round trip silently relabelled a local message. */
    snprintf(ev.scope, sizeof(ev.scope), "%s",
             bus_scope_name_for_bits(scope_bits));

    double num = 0;
    switch (bus_value_decode(m.data, m.data_length_code, &num, ev.str,
                             sizeof(ev.str))) {
        case BusValueKind::Number:
            ev.kind = BusEvent::Kind::Number;
            ev.num = num;
            break;
        case BusValueKind::String:
            ev.kind = BusEvent::Kind::String;
            break;
        case BusValueKind::Unknown:
            /* ⛔ DLC=1 CARRYING ONLY 0xFD SURVIVES THIS PATH.  An `unknown` on
             * this wire is EXACTLY ONE BYTE — DLC == 1, not DLC == 0.  The
             * payload after the tag is zero-length; the DATA FIELD is not, and
             * conflating the two sends the frame to the `None` case below,
             * which drops it.  Verified: a DLC=1 frame reaches
             * bus_value_decode with n == 1, which is the unknown branch.
             *
             * Without this case the one message whose entire content is "do
             * not believe me" would be discarded in silence.  See
             * BusEvent::unknown for why it is a flag and not a fourth Kind. */
            ev.unknown = true;
            ev.kind = BusEvent::Kind::Number;
            ev.num = 0;
            break;
        case BusValueKind::None:
            /* DLC == 0: no value bytes at all.  COUNTED and said out loud —
             * until this line, a value the leaf discarded and a value never
             * sent were indistinguishable from every instrument on the board.
             * Deliberately NOT folded into the link-error counter: an empty
             * State is legitimate and is not a bus fault. */
            s_rx_empty_values++;
            {
                static int64_t last_us = 0;
                int64_t now = esp_timer_get_time();
                if (!last_us || now - last_us >= 10 * 1000000LL) {
                    last_us = now;
                    ESP_LOGW(TAG, "%s: value with NO BYTES (DLC 0) — dropped "
                             "(%lu total).  This is not an `unknown`: an "
                             "unknown is DLC 1 carrying 0xFD.  (rate-limited "
                             "1 line / 10 s)",
                             ns, (unsigned long)s_rx_empty_values);
                }
            }
            return;
        case BusValueKind::Raw:
            ESP_LOGW(TAG, "%s: %u value bytes this codec will not guess at — "
                     "dropped", ns, (unsigned)m.data_length_code);
            return;
    }

    if (restart_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str,
                             (uint32_t)(esp_timer_get_time() / 1000)))
        return;

    if (s_queue) {
        if (uxQueueMessagesWaiting(s_queue) == 0)
            s_pending_since_us = esp_timer_get_time();
        if (xQueueSend(s_queue, &ev, 0) != pdTRUE)
            ESP_LOGW(TAG, "bus queue full — %s dropped", ev.ns);
    }
}

static void rx_task(void*) {
    while (true) {
        twai_message_t m;
        if (twai_receive(&m, pdMS_TO_TICKS(100)) != ESP_OK) continue;
        if (m.rtr) continue;                 /* remote frames carry no value */
        if (!m.extd) {
            /* [BP-CAN-ID] is a 29-bit layout; an 11-bit id cannot hold a
             * namespace hash, so a standard frame is somebody else's traffic
             * on a shared segment.  Dropped quietly by design — a shared bus
             * is normal and this must not be a log storm. */
            continue;
        }
        deliver(m);
    }
}

/* ------------------------------------------------------------------ */
/* tx                                                                  */
/* ------------------------------------------------------------------ */

static bool send_id(uint32_t id, const uint8_t* value, size_t len) {
    if (!s_started) return false;
    if (s_listen_only) {
        /* REFUSED HERE, not left to the controller.  In listen-only the TWAI
         * peripheral returns ESP_ERR_INVALID_STATE (or silently discards,
         * depending on IDF version), and a caller reading only the boolean
         * cannot tell "the bus rejected this" from "the bus is not there".
         * Say which.
         *
         * RATE-LIMITED to once per 10 s: publish_* is on a path that can fire
         * per frame (a button, a restart ack, a status heartbeat), and a
         * per-arrival ESP_LOGE at 115200 baud delays the very draining it
         * would be reporting — the diagnostic-causes-the-failure shape this
         * tree has already paid for once. */
        static int64_t last_us = 0;
        int64_t now = esp_timer_get_time();
        if (!last_us || now - last_us >= 10 * 1000000LL) {
            last_us = now;
            ESP_LOGE(TAG, "TX REFUSED — this node is in LISTEN-ONLY mode and "
                     "physically cannot transmit (id 0x%08lX, %u bytes, "
                     "dropped).  This is the broken-transceiver workaround, "
                     "not a bus fault.  Clear NVS can.listen to restore "
                     "normal mode; over the bus it takes TWO hops, because "
                     "\"can listen=0\" does not fit 8 bytes (see the boot "
                     "banner).  (rate-limited to 1 line / 10 s)",
                     (unsigned long)id, (unsigned)len);
        }
        return false;
    }
    if (len > 8) {
        /* REFUSED, never truncated.  Classic CAN is eight bytes and the limit
         * is the controller; a silently short value is a wrong reading on a
         * gauge, which is worse than a missing one. */
        ESP_LOGE(TAG, "value is %u bytes; classic CAN carries 8 — DROPPED "
                 "rather than truncated", (unsigned)len);
        return false;
    }
    twai_message_t m = {};
    m.identifier = id;
    m.extd = 1;
    m.data_length_code = (uint8_t)len;
    if (len && value) memcpy(m.data, value, len);
    esp_err_t e = twai_transmit(&m, pdMS_TO_TICKS(50));
    if (e != ESP_OK) {
        /* A TX timeout on CAN usually means NO OTHER NODE IS ACKING — a
         * single-node bus never wins arbitration acknowledgement.  Say that,
         * because "transmit failed" sends people to the code. */
        ESP_LOGW(TAG, "twai_transmit failed: %s (on CAN this most often means "
                 "no other node is present to ACK, or the transceiver is not "
                 "powered / not terminated)", esp_err_to_name(e));
        return false;
    }
    return true;
}

/* The RAW send path takes a hash, not a name — but [BP-WIRE-PRIORITY] places
 * application data BY NAMESPACE, and a hash is not reversible.  So the tier is
 * computed from the TYPE alone here and the name-aware `send()` below passes
 * the name.  A raw relay forwarding somebody else's STATE frame therefore gets
 * the DEFAULT tier, which is exactly right: it does not know what the traffic
 * is, and inventing a tier for traffic you cannot name is the kind of guess
 * this file refuses everywhere else. */
bool bus_can_send_raw(uint16_t ns_hash, BusMessageType type,
                      uint8_t scope_bits, const uint8_t* value, size_t len) {
    uint8_t code;
    if (!bus_wire_code_for_type(type, &code)) {
        ESP_LOGE(TAG, "type has no allocated wire code — frame DROPPED, "
                 "never substituted");
        return false;
    }
    uint8_t prio = bus_priority_for(nullptr, type);
    return send_id(bus_can_id_encode(prio, ns_hash, code,
                                     (uint8_t)s_node_id, scope_bits),
                   value, len);
}

/* Named send: the tier can be computed properly, and scope actually crosses. */
static bool send_scoped(const char* ns, BusMessageType type,
                        const char* scope, const uint8_t* value, size_t len) {
    if (!ns) return false;
    uint8_t code;
    if (!bus_wire_code_for_type(type, &code)) {
        ESP_LOGE(TAG, "type has no allocated wire code — frame DROPPED, "
                 "never substituted");
        return false;
    }
    return send_id(bus_can_id_encode(bus_priority_for(ns, type),
                                     bus_ns_hash(ns), code,
                                     (uint8_t)s_node_id,
                                     bus_scope_bits_for_name(scope)),
                   value, len);
}

static bool send(const char* ns, BusMessageType type, const uint8_t* value,
                 size_t len) {
    /* "global" is this transport's own publish default and is now a real field
     * on the wire rather than a comment about a field that did not exist. */
    return send_scoped(ns, type, "global", value, len);
}

/* ------------------------------------------------------------------ */
/* Lifecycle                                                           */
/* ------------------------------------------------------------------ */

static bool can_start(const char* client_id) {
    if (s_started) return true;
    load_config();

    if (s_tx < 0 || s_rx < 0) {
        /* KNOWN transport, UNREACHABLE backend -> loud degrade, keep running
         * (transport_registry.py rule 2). */
        ESP_LOGE(TAG, "CAN transport selected but there is NO pin assignment "
                 "for this board (tx=%ld rx=%ld).  Set them without a "
                 "reflash: publish _device/<id>/transport = \"can tx=<gpio> "
                 "rx=<gpio> bitrate=500000\", or set NVS can.tx / can.rx in "
                 "namespace '%s'.  Remember TWAI needs a TRANSCEIVER "
                 "(SN65HVD230 or similar) — the pins are logic-level.  The "
                 "bus is DOWN.", (long)s_tx, (long)s_rx, CXC_NVS_NAMESPACE);
        return false;
    }

    twai_timing_config_t t_cfg;
    if (!timing_for(s_bitrate, &t_cfg)) {
        ESP_LOGE(TAG, "bitrate %ld is not one TWAI has a timing table for "
                 "(25k 50k 100k 125k 250k 500k 800k 1M).  REFUSED rather than "
                 "run at a rate nobody asked for — the bus is DOWN.",
                 (long)s_bitrate);
        return false;
    }

    twai_general_config_t g_cfg =
        TWAI_GENERAL_CONFIG_DEFAULT((gpio_num_t)s_tx, (gpio_num_t)s_rx,
                                    s_listen_only ? TWAI_MODE_LISTEN_ONLY
                                                  : TWAI_MODE_NORMAL);
    g_cfg.rx_queue_len = 32;
    g_cfg.tx_queue_len = 16;
    /* ACCEPT EVERYTHING at the controller, and filter in software.
     *
     * This gives up CAN's best property — hardware subscription filtering, on
     * the controller, with zero CPU on uninterested nodes.  It is the right
     * trade HERE and the reason is [BP-CAN-ID]'s layout: the namespace hash
     * occupies the id's HIGH bits, so an acceptance mask can select a
     * CONTIGUOUS RANGE of hashes, and the handful of unrelated namespaces one
     * device subscribes are scattered across the whole 16-bit space.  A single
     * mask that admits all of them admits nearly everything anyway.  Making
     * this pay needs hash ALLOCATION to be range-aware, which is a bus-wide
     * decision, not a board one. */
    twai_filter_config_t f_cfg = TWAI_FILTER_CONFIG_ACCEPT_ALL();

    esp_err_t e = twai_driver_install(&g_cfg, &t_cfg, &f_cfg);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "twai_driver_install failed: %s — the bus is DOWN",
                 esp_err_to_name(e));
        return false;
    }
    e = twai_start();
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "twai_start failed: %s — the bus is DOWN",
                 esp_err_to_name(e));
        twai_driver_uninstall();
        return false;
    }

    s_queue = xQueueCreate(kQueueDepth, sizeof(BusEvent));
    s_started = true;
    xTaskCreate(rx_task, "bus_can_rx", kRxTaskStack, nullptr, 5, &s_rx_task);

    ESP_LOGI(TAG, "started: TWAI TX=%ld RX=%ld @ %ld bit/s, mode %s, extended "
             "ids, [BP-CAN-ID] v2 prio(3)|type(4)|ns(16)|src(4)|scope(2), "
             "node id %ld%s (sources tx/rx/bitrate/listen/node = %c%c%c%c%c, "
             "d=compiled default, n=NVS, x=refused; client id %s)",
             (long)s_tx, (long)s_rx, (long)s_bitrate,
             s_listen_only ? "LISTEN-ONLY" : "NORMAL", (long)s_node_id,
             s_node_id == kBusCanNodeUnassigned ? " (UNASSIGNED — set NVS "
                 "can.node to name this box in a capture)" : "",
             s_src_of[0], s_src_of[1], s_src_of[2], s_src_of[3], s_src_of[4],
             client_id ? client_id : "?");
    ESP_LOGW(TAG, "classic CAN 2.0B: EIGHT data bytes per frame.  A state "
             "value longer than 8 bytes is REFUSED, not truncated; config "
             "travels chunked ([SPEC:CXC-CONFIG-BUS]).");

    if (s_listen_only) {
        /* SIX LINES AT ERROR LEVEL, ON EVERY BOOT, IN PLAIN WORDS.  A node
         * that cannot transmit and does not SAY SO is a node whose publishes
         * vanish with no error at all — the [CXC-BOARD-HW-SELF-CONFIRMING]
         * shape, one layer down.  The cost of this banner is six lines; the
         * cost of it being absent is somebody debugging a bus for an hour. */
        ESP_LOGE(TAG, "================ LISTEN-ONLY MODE ================");
        ESP_LOGE(TAG, "THIS NODE WILL NEVER TRANSMIT AND NEVER ACK.  It can "
                 "RECEIVE and nothing else.  Every publish_* call on this "
                 "transport is REFUSED, not queued.");
        ESP_LOGE(TAG, "THIS IS A WORKAROUND FOR A BROKEN TRANSCEIVER, NOT A "
                 "DESIGN.  Bench 2026-08-12: the SN65HVD230's DRIVER is dead "
                 "(Pi bus-errors unmoved across ten 1.5 s dominant attempts; "
                 "-0.6 mV across CANH/CANL with the driver input held low; "
                 "two separate modules identical).  Its RECEIVER is fine — "
                 "1,136 frames at 500 kbit/s, bus_err=0, verified twice.");
        ESP_LOGE(TAG, "In NORMAL mode on that hardware this node reaches "
                 "BUS-OFF in ~5 s and then stops RECEIVING too, because "
                 "ESP-IDF does not auto-recover and this driver never calls "
                 "twai_initiate_recovery().");
        /* ⚠ THE OBVIOUS RECOVERY COMMAND DOES NOT FIT ON THIS SUBSTRATE, and
         * saying so here is the whole point of the line.  A classic-CAN state
         * string is [len][utf8…] in EIGHT bytes — SEVEN characters — so
         * `"can listen=0"` is 13 bytes and publish_state REFUSES it (rightly:
         * a truncated string decodes cleanly as a DIFFERENT command).  A
         * CAN-only board therefore cannot be handed any wiring key at all;
         * only the bare transport NAMES fit.  "mqtt" is 5 bytes, and that is
         * the way out. */
        ESP_LOGE(TAG, "TO RESTORE NORMAL MODE once the transceiver is "
                 "replaced: EITHER reflash without CXC_CAN_LISTEN_ONLY, OR "
                 "take the two-hop route, because \"can listen=0\" is 13 bytes "
                 "and a classic-CAN state string is SEVEN characters: publish "
                 "_device/<id>/transport = \"mqtt\" (5 bytes, fits), reboot "
                 "onto MQTT, then publish _device/<id>/transport = "
                 "\"can listen=0\" over MQTT, reboot.  Direct NVS also works: "
                 "can.listen = 0 in namespace '%s'.", CXC_NVS_NAMESPACE);
        ESP_LOGE(TAG, "==================================================");
    }
    return true;
}

static void register_ns(NsEntry* slot, const char* ns) {
    snprintf(slot->ns, sizeof(slot->ns), "%s", ns);
    slot->hash = bus_ns_hash(ns);
}

class CanTransport final : public BusTransport {
public:
    const char* name() const override { return "can"; }

    /* No IP stack — the same reason UART has none, and the reason a CH32V203
     * on a CAN segment is a legitimate deployment at all. */
    bool needs_network() const override { return false; }

    bool start(const char* client_id) override { return can_start(client_id); }

    void stop() override {
        if (!s_started) return;
        if (s_rx_task) { vTaskDelete(s_rx_task); s_rx_task = nullptr; }
        twai_stop();
        twai_driver_uninstall();
        s_started = false;
        ESP_LOGI(TAG, "stopped");
    }

    /* A MULTI-DROP bus has no session either.  There is no cheap way to know
     * whether anything is LISTENING, and probing on every render pass is what
     * the Python ABC warns against — this must stay a field read.  (TWAI's
     * error state is a real signal, but reading it per pass is a bus-off
     * detector, not a connectivity property, and belongs in diagnostics.) */
    bool connected() const override { return s_started; }

    void subscribe(const char* ns) override {
        if (!ns || !ns[0]) return;
        uint16_t h = bus_ns_hash(ns);
        for (int i = 0; i < s_persist_count; i++)
            if (s_persist[i].hash == h) return;
        if (s_persist_count >= kMaxPersist) {
            ESP_LOGW(TAG, "persistent subscription set full — %s dropped", ns);
            return;
        }
        register_ns(&s_persist[s_persist_count++], ns);
        ESP_LOGI(TAG, "subscribed %s (hash 0x%04X, persistent)", ns, h);
    }

    void set_subscriptions(const char* const* ns, size_t count) override {
        s_sub_count = 0;
        for (size_t i = 0; i < count && s_sub_count < kMaxSubs; i++) {
            if (!ns[i] || !ns[i][0]) continue;
            register_ns(&s_subs[s_sub_count], ns[i]);
            ESP_LOGI(TAG, "subscribed %s (hash 0x%04X)", s_subs[s_sub_count].ns,
                     s_subs[s_sub_count].hash);
            s_sub_count++;
        }
        if ((int)count > kMaxSubs)
            ESP_LOGW(TAG, "subscription set truncated to %d (asked %u)",
                     kMaxSubs, (unsigned)count);
    }

    bool poll(BusEvent* out) override {
        if (!s_queue || !out) return false;
        bool got = xQueueReceive(s_queue, out, 0) == pdTRUE;
        if (got && uxQueueMessagesWaiting(s_queue) == 0)
            s_pending_since_us = 0;
        return got;
    }

    size_t pending() const override {
        return s_queue ? (size_t)uxQueueMessagesWaiting(s_queue) : 0;
    }
    int64_t pending_since_us() const override { return s_pending_since_us; }

    void publish_state(const char* ns, const char* value) override {
        if (!value) return;
        uint8_t v[8];
        size_t n = bus_value_encode_str(value, v, sizeof(v));
        if (!n || n > 8) {
            ESP_LOGE(TAG, "%s: a %u-byte string does not fit a classic-CAN "
                     "frame (7 chars + length).  DROPPED rather than "
                     "truncated — a half string is a wrong value, not a late "
                     "one.", ns, (unsigned)strlen(value));
            return;
        }
        send(ns, BusMessageType::State, v, n);
    }

    /* float32 always, for the identical reason bus_uart gives: a C++ double
     * carries no Python type distinction, and inferring "integral, so one
     * byte" would make 1.0 and true indistinguishable on the wire. */
    void publish_number(const char* ns, double value) override {
        uint8_t v[kBusValueMaxNumeric];
        size_t n = bus_value_encode_f32((float)value, v);
        send(ns, BusMessageType::State, v, n);
    }

    bool publish_raw(const char* ns, BusMessageType type,
                     const uint8_t* value, size_t len) override {
        return send(ns, type, value, len);
    }

    /* EIGHT.  The number [CXC-CONFIG-BUS-BUDGET] sizes the whole config chunk
     * format against, reported through the seam so the chunker never has to
     * name a substrate. */
    size_t max_raw_value() const override { return 8; }

    /* ---------------------------------------------------------------- */
    /* THE GENERIC HALF OF THE TWAI INSTRUMENT ([SPEC:CXC-BOARD-HW] H2a) */
    /* ---------------------------------------------------------------- */
    /*
     * can_bringup_main.cpp publishes six TWAI-SPECIFIC counters (TEC, REC,
     * tx_failed, rx_missed, rx_overrun, bus_error) on `_device/<id>/twai/*`.
     * Those stay exactly where they are — a UART has no TEC and folding them
     * into a shared health word would make the word substrate-shaped.  What
     * is GENERIC is the five-state summary, and that is what goes into
     * `_device/<id>/health` on ANY board that happens to be running CAN.
     *
     * ⚠ THIS IS AN INSTRUMENT, NOT A FIX.  `connected()` above still returns
     * `s_started`, so a BUS-OFF node still reports CONNECTED forever, and this
     * driver still never calls twai_initiate_recovery().  Both are recorded,
     * deliberately-unfixed defects (H2b / H2c) whose fixes change RUNTIME
     * BEHAVIOUR on boards that are not on a bench.  What changes here is only
     * that the true state becomes VISIBLE: a fleet report showing
     * `connected=1  link=down` is the H2 defect finally saying its own name
     * out loud, rather than a green light over a deaf and mute board.
     *
     * READ-ONLY and CHEAP.  twai_get_status_info() is a register read; it
     * starts nothing, recovers nothing and transmits nothing, so calling it
     * from the announcement path cannot perturb a live segment.
     *
     * The error-active / warning / passive thresholds are the CAN 2.0B ones
     * (>=96 warning, >=128 passive) applied to the two counters the CONTROLLER
     * maintains — so this is reading hardware, not reading our own assumption
     * back, which is the one property that makes it worth publishing at all.
     */
    uint8_t link_health() const override {
        if (!s_started) return kAnnLinkStopped;
        twai_status_info_t st = {};
        if (twai_get_status_info(&st) != ESP_OK) return kAnnLinkUnknown;
        switch (st.state) {
            case TWAI_STATE_STOPPED:    return kAnnLinkStopped;
            case TWAI_STATE_BUS_OFF:    return kAnnLinkDown;
            case TWAI_STATE_RECOVERING: return kAnnLinkRecovering;
            case TWAI_STATE_RUNNING: {
                uint32_t worst = st.tx_error_counter > st.rx_error_counter
                                     ? st.tx_error_counter
                                     : st.rx_error_counter;
                if (worst >= 128) return kAnnLinkDegraded;   /* error-passive */
                if (worst >= 96)  return kAnnLinkWarning;
                return kAnnLinkOk;
            }
            default: return kAnnLinkUnknown;
        }
    }

    /* HISTORY, not current state — deliberately a different question from
     * link_health().  A node that took an error burst and recovered reads Ok
     * here and `errors` there, and that pairing is the only thing on the bus
     * that can distinguish a healthy segment from one that is bus-off/
     * restarting at 10 Hz (the H1 `restart-ms 100` failure, which presents as
     * intermittent flakiness rather than as a fault). */
    bool link_has_errors() const override {
        if (!s_started) return false;
        twai_status_info_t st = {};
        if (twai_get_status_info(&st) != ESP_OK) return false;
        return st.tx_error_counter || st.rx_error_counter ||
               st.tx_failed_count || st.rx_missed_count ||
               st.rx_overrun_count || st.bus_error_count;
    }

    bool link_listen_only() const override { return bus_can_is_listen_only(); }
};

BusTransport& bus_can_transport(void) {
    static CanTransport t;
    return t;
}
