/*
 * bus_uart.cpp — ComposeBus over a framed UART link (ESP-IDF).
 * See bus_uart.h.  Frame codec: bus_frame.h ([BP-UART-FRAME]).
 *
 * Shape deliberately mirrors bus_mqtt.cpp: a driver task decodes arrivals
 * and pushes BusEvent onto a queue; the render task drains it.  ALL widget
 * and reactive-core mutation therefore stays on one task, exactly as it
 * does on MQTT, and the app shell cannot tell the two apart.
 */

#include "bus_uart.h"
#include "bus_frame.h"
#include "announce_pack.h"
#include "config_bus.h"
#include "restart.h"

#include <cstdio>
#include <cstring>

#include "driver/uart.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "nvs.h"

static const char* TAG = "bus_uart";

/* ------------------------------------------------------------------ */
/* Wiring — CONFIGURABLE, not baked                                    */
/* ------------------------------------------------------------------ */
/*
 * transport_registry.py's UART factory takes its wiring from the scene
 * spec — `_uart(spec)` reads `port` and `baudrate` — so a host is pointed
 * at a different adapter without touching code.  This transport must be
 * configurable the same way, for the same reason: a bring-up rig on the
 * bench should be re-pointed with a command, not a rebuild.
 *
 * The MCU analogue of a device path is three numbers (which UART
 * peripheral, and which two GPIOs the matrix routes it to), so the
 * settable set is port / tx / rx / baudrate, held in the SAME NVS
 * namespace as the transport selection itself:
 *
 *     nvs namespace "composeio":
 *       uart.port      i32   UART peripheral number (0,1,...)
 *       uart.tx        i32   TX GPIO
 *       uart.rx        i32   RX GPIO
 *       uart.baudrate  i32   bit rate
 *
 * settable over the CURRENT link with, e.g.
 *     _device/<id>/transport = "uart port=0 tx=43 rx=44 baudrate=115200"
 * (bus_transport_select.cpp parses it), and reported on every boot with
 * the SOURCE of each value, so a board can always say how it is wired.
 *
 * COMPILE-TIME DEFAULTS come from the hardware inventory
 * ([SPEC:CXC-BOARD-HW]) and are only defaults:
 *   hosyond  TXD0/RXD0 = GPIO43/44 on the dedicated UART JST, each through
 *            a 100R series resistor ([CXC-BOARD-HW-HOSYOND]).
 *   c6       the dedicated TXD/RXD pads = GPIO16/17 ([CXC-BOARD-HW-WSC6]).
 * The default PERIPHERAL is UART1, because UART0 belongs to the ROM
 * console on boards whose console is a USB-serial BRIDGE (the classic
 * CYD).  Both S3/C6 boards console over USB-Serial-JTAG, so their UART0
 * is genuinely free and `uart.port = 0` is a legitimate setting there.
 *
 * A board with NO default is not given invented pins: start() fails
 * loudly, which is the honest report for "this build cannot reach that
 * backend" (transport_registry.py rule 2).  Those pins remain per-BOARD
 * facts that ultimately belong in the BoardProfile; this slice was scoped
 * to leave board_profile.h alone.
 */
/* NO per-board #if HERE, and that is deliberate.  The board is selected by
 * a CMake CACHE VARIABLE (HOSYOND_S3 / WAVESHARE_C6 from
 * board_build.cmake_extra_args), which picks the branch in
 * src/CMakeLists.txt but does NOT become a -D on the component compile —
 * verified against .pio/build/<env>/compile_commands.json, where only
 * CXC_BAND_HEIGHT and CXC_DEVICE_PREFIX (real
 * target_compile_definitions) appear.  A `#if defined(HOSYOND_S3)` here
 * would therefore be DEAD CODE that silently took the -1 branch: the
 * board would report "no pin assignment" while the source looked like it
 * had one.  That is the same class as the two defects the CMakeLists
 * comments already record.  So the pins are target_compile_definitions in
 * each board's CMake branch, beside CXC_DEVICE_PREFIX, and this file only
 * supplies the "unconfigured" fallback. */
#ifndef CXC_UART_TX_GPIO
#define CXC_UART_TX_GPIO (-1)
#endif

#ifndef CXC_UART_RX_GPIO
#define CXC_UART_RX_GPIO (-1)
#endif

#ifndef CXC_UART_PORT
#define CXC_UART_PORT 1
#endif

#ifndef CXC_UART_BAUD
#define CXC_UART_BAUD 115200
#endif

#define CXC_NVS_NAMESPACE "composeio"

/* Resolved at start(); every field records where its value came from so
 * the boot line is a full account of the wiring. */
static int32_t s_port = CXC_UART_PORT;
static int32_t s_tx = CXC_UART_TX_GPIO;
static int32_t s_rx = CXC_UART_RX_GPIO;
static int32_t s_baud = CXC_UART_BAUD;
static char s_src[5] = "dddd";     /* d = compiled default, n = NVS */

static void load_config(void) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return;
    struct { const char* key; int32_t* dst; int idx; } fields[] = {
        { "uart.port",     &s_port, 0 },
        { "uart.tx",       &s_tx,   1 },
        { "uart.rx",       &s_rx,   2 },
        { "uart.baudrate", &s_baud, 3 },
    };
    for (auto& f : fields) {
        int32_t v;
        if (nvs_get_i32(h, f.key, &v) == ESP_OK) {
            *f.dst = v;
            s_src[f.idx] = 'n';
        }
    }
    nvs_close(h);
}

bool bus_uart_set_config(int32_t port, int32_t tx, int32_t rx,
                         int32_t baudrate) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed — UART wiring NOT stored");
        return false;
    }
    esp_err_t e = ESP_OK;
    /* A negative value means "leave this one alone" — so a command can set
     * just the baud rate without having to restate the pin map. */
    if (port >= 0) e |= nvs_set_i32(h, "uart.port", port);
    if (tx >= 0) e |= nvs_set_i32(h, "uart.tx", tx);
    if (rx >= 0) e |= nvs_set_i32(h, "uart.rx", rx);
    if (baudrate > 0) e |= nvs_set_i32(h, "uart.baudrate", baudrate);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs write failed — UART wiring NOT stored");
        return false;
    }
    ESP_LOGW(TAG, "UART wiring stored (port=%ld tx=%ld rx=%ld baud=%ld; "
             "negative = unchanged) — takes effect on the NEXT boot",
             (long)port, (long)tx, (long)rx, (long)baudrate);
    return true;
}

static constexpr int kMaxSubs = 16;
/* PERSISTENT (control-plane) subscriptions — the ones that survive a scene
 * swap.  EIGHT, not four.
 *
 * Four was exactly the number the control plane had on 2026-08-12 and one
 * fewer than it needed the moment config-over-bus landed:
 *   _device/<id>/restart, _device/restart, _device/<id>/transport,
 *   _device/<id>/config_source  -> full, and then
 *   _device/<id>/config         -> "persistent subscription set full — dropped"
 * The board therefore requested its scene, the head served 25 frames, and
 * every one of them was discarded as "unregistered ns hash" — a leaf that
 * asked for something it had made itself structurally unable to receive.
 * Caught on the bench in the first live boot; NO host test could have seen it,
 * because the cap only binds when a real board subscribes its real set.
 *
 * Sized with headroom rather than to fit: each entry is 98 bytes, the control
 * plane grows by one namespace per feature, and the failure mode of being one
 * short is a silent drop of the most important namespace on the device. */
static constexpr int kMaxPersist = 8;
static constexpr int kQueueDepth = 16;
static constexpr int kRxRingBytes = 1024;
static constexpr int kRxTaskStack = 4096;

/* Subscribed namespaces.  On a framed substrate the wire carries a 16-bit
 * ns HASH, not the name — so a device can only name what it already knows
 * ([BP-UART-FRAME]; transport_uart.py keeps the identical reverse map via
 * register_namespace_hash).  A frame for an unregistered hash is dropped
 * and logged: inventing a namespace string would put a value into the
 * reactive core under a name nothing bound. */
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
static TaskHandle_t s_rx_task = nullptr;

/* FRAME-LEVEL RECEIVE ERRORS — bad CRC, an unallocated type code, or a full
 * buffer with no frame in it.  Fed to BusTransport::link_has_errors() so
 * `_device/<id>/health` can say "this link is passing traffic it cannot
 * decode", which is a real UART fault (a baud mismatch, a floating rx pin, a
 * half-connected JST) that until now existed only as a log line nobody was
 * reading.
 *
 * IT CAN INCREMENT.  This repo has shipped a structurally-pinned counter
 * twice — a dropped-frame counter that could never move, and a bridge
 * `filtered` counter pinned at 0 because the filtering happened at the broker.
 * Each of the three sites below is on the ONLY path that reaches it, and the
 * negative control is trivial: pull the rx wire, or set a wrong baud, and it
 * climbs. */
static volatile uint32_t s_rx_frame_errors = 0;

/* VALUES DROPPED FOR HAVING NO BYTES — a separate counter, on purpose.
 *
 * `BusValueKind::None` used to `return;` with no log and no counter anywhere,
 * so a value THIS LEAF DISCARDED and a value the publisher NEVER SENT were
 * indistinguishable to every instrument the board has.  That is the same shape
 * as the defect this whole change is about, one layer down: an absence with no
 * evidence of itself.
 *
 * ⛔ NOT folded into s_rx_frame_errors, which feeds link_has_errors().  A
 * zero-length State is a LEGITIMATE thing for a publisher to send (Python's
 * decode_value returns None for it) and is not a link fault; counting it as
 * one would make a healthy link report errors and would devalue the counter
 * that detects a real baud mismatch. */
static volatile uint32_t s_rx_empty_values = 0;

static const char* ns_for_hash(uint16_t h) {
    for (int i = 0; i < s_sub_count; i++)
        if (s_subs[i].hash == h) return s_subs[i].ns;
    for (int i = 0; i < s_persist_count; i++)
        if (s_persist[i].hash == h) return s_persist[i].ns;
    return nullptr;
}

/* ------------------------------------------------------------------ */
/* rx: bytes -> frames -> BusEvent queue                               */
/* ------------------------------------------------------------------ */

/* PROMISCUOUS RAW SINK — for a RELAY, and for nothing else.
 *
 * A relay forwards frames for namespaces it has never heard of (it cannot know
 * every downstream device's `_device/<id>/config`), and the wire carries a
 * HASH, not a name, so relaying must happen BEFORE ns resolution and without
 * one.  That is why this hook sits above `ns_for_hash` rather than beside the
 * subscription machinery: a relay is a dumb repeater by design
 * ([CXC-CONFIG-BUS-RELAY]), and giving it a subscription set would make
 * topology a thing somebody configures — the exact leak this design refuses.
 *
 * It OBSERVES; it does not consume.  A node can be both a relay and a leaf. */
static BusRawFrameFn s_raw_sink = nullptr;
static void* s_raw_ud = nullptr;

void bus_uart_set_raw_sink(BusRawFrameFn fn, void* ud) {
    s_raw_sink = fn;
    s_raw_ud = ud;
    ESP_LOGW(TAG, "raw frame sink installed — this node now forwards frames "
             "it does not subscribe (relay mode)");
}

static void deliver(const BusFrame& f) {
    if (s_raw_sink)
        s_raw_sink(f.ns_hash, f.type, f.scope_bits, f.value, f.value_len,
                   s_raw_ud);

    const char* ns = ns_for_hash(f.ns_hash);
    if (!ns) {
        /* RATE-LIMITED, and that is not cosmetic.  Before config-over-bus the
         * only unregistered arrivals were strays; a config transfer is 25
         * back-to-back frames, so an unsubscribed one produced 25 ESP_LOGW
         * lines in ~60 ms.  Console writes at 115200 are SLOW and they happen
         * on this rx task, so the log storm delays the very draining that
         * empties the ring — a diagnostic that causes the failure it reports.
         * One line per second, with the count, says the same thing. */
        static int64_t last_us = 0;
        static uint32_t suppressed = 0;
        int64_t now = esp_timer_get_time();
        if (!last_us || now - last_us > 1000000LL) {
            ESP_LOGW(TAG, "frame for unregistered ns hash 0x%04X — dropped "
                     "(this device subscribes %d + %d namespaces; %lu further "
                     "such frames dropped in the last second)",
                     f.ns_hash, s_sub_count, s_persist_count,
                     (unsigned long)suppressed);
            last_us = now;
            suppressed = 0;
        } else {
            suppressed++;
        }
        return;
    }

    /* CONFIG CONTROL PLANE, consumed at RECEIVE time ([SPEC:CXC-CONFIG-BUS]).
     *
     * It must be here and not in the render loop: a 6 KB scene is ~25 frames,
     * the arrival queue is 16 deep, and the render loop paces its drain behind
     * a touch-poll burst.  Reassembling on the rx task keeps the transfer at
     * wire speed and off the queue entirely — the same reasoning that puts the
     * restart intercept here ([CXC-ER-RESTART-BUS]).
     *
     * ⛔ AND SINCE 0xFD BECAME A TYPE TAG, THIS ORDERING IS ALSO A CORRECTNESS
     * CONSTRAINT, NOT ONLY A LATENCY ONE.  A config chunk is OPAQUE BYTES with
     * its own internal framing, and a one-byte chunk whose content happens to
     * be 0xFD is indistinguishable from an encoded `unknown`.  It survives
     * TODAY only because this intercept runs BEFORE bus_value_decode() — the
     * chunk is consumed by content-agnostic code and never reaches the value
     * codec at all.
     *
     * Move this line below the decode and a 0xFD config byte silently becomes
     * an UNKNOWN and the transfer loses a chunk.  The reordering would look
     * free: nothing in the types, the signatures or the tests says otherwise,
     * and the failure would appear as an occasional config transfer that never
     * completes.  It is written down here because that is the only place a
     * refactorer will be standing when they consider it. */
    if (config_bus_on_frame(ns, f.type, f.value, f.value_len)) return;

    /* State traffic — PLUS heartbeats, exactly as the MQTT path filters.
     *
     * ⛔ THIS LINE USED TO READ `!= State` AND THAT MADE NODE_SILENT
     * STRUCTURALLY UNREACHABLE ([CXC-FRESH-HEARTBEAT-RX]).  Both framed
     * transports dropped every non-State message HERE, before queueing, so a
     * peer heartbeat could never reach BusTransport::poll() — the receiver
     * half was not merely unbuilt, it was unbuildable.  The Python tier had
     * the identical hole from the other end: "HEARTBEAT messages reach neither
     * the STATE nor the EVENT branch of the inbound dispatcher, so they fell
     * through entirely."
     *
     * ⚠ AND HEARTBEATS DO NOT FLOOD THE SCENE PATH, because they never reach
     * it: the drain's control-plane sieve consumes them one stage BEFORE
     * scene_on_bus_value(), the same way it already consumes transport,
     * announce and config_source commands.  The queue cost is one 5 s beat per
     * DECLARED node — and a node is only declared when a namespace names it,
     * so an undeclared peer's ns hash is not registered and its beat is
     * dropped at the line above this one anyway.
     *
     * Every OTHER non-state type still stops here, unchanged. */
    if (f.type != BusMessageType::State &&
        f.type != BusMessageType::Heartbeat) {
        ESP_LOGD(TAG, "%s: non-state type %u — ignored", ns,
                 (unsigned)f.type);
        return;
    }

    static BusEvent ev;             /* rx-task-only, like bus_mqtt's */
    memset(&ev, 0, sizeof(ev));
    ev.type = f.type;
    snprintf(ev.ns, sizeof(ev.ns), "%s", ns);
    snprintf(ev.scope, sizeof(ev.scope), "%s",
             bus_scope_name_for_bits(f.scope_bits));

    double num = 0;
    switch (bus_value_decode(f.value, f.value_len, &num, ev.str,
                             sizeof(ev.str))) {
        case BusValueKind::Number:
            ev.kind = BusEvent::Kind::Number;
            ev.num = num;
            break;
        case BusValueKind::String:
            ev.kind = BusEvent::Kind::String;
            break;
        case BusValueKind::Unknown:
            /* ⛔ AN UNKNOWN IS DELIVERED, NEVER DROPPED.
             *
             * ON THE WIRE IT IS EXACTLY ONE BYTE, 0xFD — `value_len == 1`
             * here, `DLC == 1` on CAN.  Say ONE BYTE and not "zero-length
             * payload": the payload AFTER the tag is zero-length, but the
             * VALUE FIELD is one byte, and reading it as DLC=0 sends you to
             * the `None` case below, which is a different and silently-dropped
             * path.  That one-word ambiguity is exactly how this decodes
             * correctly on the tier that wrote it and vanishes on the next.
             *
             * The `None` case returns early because an empty value means "no
             * state".  Folding unknown into it would SILENTLY DISCARD the one
             * message whose entire content is "do not believe me" — the
             * failure this whole change exists to prevent, reintroduced by an
             * early return.
             *
             * It rides as a FLAG, not as a Kind — see BusEvent::unknown. */
            ev.unknown = true;
            ev.kind = BusEvent::Kind::Number;   /* unread; the sieve eats it */
            ev.num = 0;
            break;
        case BusValueKind::None:
            /* Python decodes an empty value to None — no state to deliver.
             * COUNTED AND SAID OUT LOUD, because until this line a value the
             * leaf discarded and a value never sent looked identical from
             * every instrument on the board.  Rate-limited to one line per
             * 10 s with a running total: a publisher stuck emitting empties at
             * cadence would otherwise turn this diagnostic into the load that
             * causes the drops it reports. */
            s_rx_empty_values++;
            {
                static int64_t last_us = 0;
                int64_t now = esp_timer_get_time();
                if (!last_us || now - last_us >= 10 * 1000000LL) {
                    last_us = now;
                    ESP_LOGW(TAG, "%s: value with NO BYTES — dropped (%lu "
                             "total).  This is not an `unknown`: an unknown is "
                             "ONE byte, 0xFD.  (rate-limited 1 line / 10 s)",
                             ns, (unsigned long)s_rx_empty_values);
                }
            }
            return;
        case BusValueKind::Raw:
            ESP_LOGW(TAG, "%s: %u value bytes this codec will not guess "
                     "at — dropped", ns, (unsigned)f.value_len);
            return;
    }

    /* Same control-plane intercept as MQTT ([CXC-ER-RESTART-BUS]): a
     * restart command is consumed at RECEIVE time so the boot-loop guard
     * sees the true arrival time and a restart works even if the render
     * loop is wedged. */
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
    static uint8_t buf[kBusFrameMaxLen * 2];
    size_t have = 0;
    while (true) {
        int n = uart_read_bytes((uart_port_t)s_port, buf + have,
                                (uint32_t)(sizeof(buf) - have),
                                pdMS_TO_TICKS(50));
        if (n > 0) have += (size_t)n;

        /* Drain every complete frame in the buffer. */
        while (have > 0) {
            BusFrame f;
            size_t used = 0;
            BusFrameResult r = bus_frame_decode(buf, have, &f, &used);
            if (r == BusFrameResult::Incomplete) break;
            if (r == BusFrameResult::Ok) deliver(f);
            else if (r == BusFrameResult::BadCrc) {
                s_rx_frame_errors++;
                ESP_LOGW(TAG, "CRC mismatch — frame dropped, resyncing");
            } else if (r == BusFrameResult::UnallocatedType) {
                s_rx_frame_errors++;
                ESP_LOGW(TAG, "frame carries an UNALLOCATED message-type "
                         "code (allocated 0..9, see [BP-TYPE-CODES]) — "
                         "DROPPED, not guessed");
            }
            if (used == 0) break;
            memmove(buf, buf + used, have - used);
            have -= used;
        }
        if (have == sizeof(buf)) {
            s_rx_frame_errors++;
            ESP_LOGW(TAG, "rx buffer full with no frame — flushed");
            have = 0;
        }
    }
}

/* ------------------------------------------------------------------ */
/* tx                                                                  */
/* ------------------------------------------------------------------ */

/* Raw egress by HASH — a relay's write side.  It has no namespace NAME to
 * work with (the wire never carried one) and must not invent one. */
bool bus_uart_send_raw(uint16_t ns_hash, BusMessageType type,
                       uint8_t scope_bits, const uint8_t* value, size_t len) {
    if (!s_started) return false;
    uint8_t frame[kBusFrameMaxLen];
    size_t n = bus_frame_encode(ns_hash, type, scope_bits, value, len,
                                frame, sizeof(frame));
    if (!n) {
        ESP_LOGE(TAG, "raw frame for ns hash 0x%04X NOT encoded (unmapped "
                 "type or %u-byte value) — DROPPED, not substituted",
                 ns_hash, (unsigned)len);
        return false;
    }
    uart_write_bytes((uart_port_t)s_port, (const char*)frame, n);
    return true;
}

static bool send(const char* ns, BusMessageType type,
                 const uint8_t* value, size_t value_len) {
    if (!s_started || !ns) return false;
    uint8_t frame[kBusFrameMaxLen];
    size_t n = bus_frame_encode(bus_ns_hash(ns), type,
                                bus_scope_bits_for_name("global"),
                                value, value_len, frame, sizeof(frame));
    if (!n) {
        /* Only two ways to get here: an unmapped message type (which must
         * NEVER be silently re-labelled — [BP-TYPE-CODES]) or an oversized
         * value.  Both are drops, and both say so. */
        ESP_LOGE(TAG, "%s: frame NOT encoded (unmapped type or oversized "
                 "value, %u bytes) — DROPPED rather than sent as something "
                 "else", ns, (unsigned)value_len);
        return false;
    }
    uart_write_bytes((uart_port_t)s_port, (const char*)frame, n);
    return true;
}

/* ------------------------------------------------------------------ */
/* Lifecycle                                                           */
/* ------------------------------------------------------------------ */

static bool uart_start(const char* client_id) {
    if (s_started) return true;

    load_config();

    if (s_tx < 0 || s_rx < 0) {
        /* KNOWN transport, UNREACHABLE backend -> loud degrade, keep
         * running (transport_registry.py rule 2). */
        ESP_LOGE(TAG, "UART transport selected but there is NO pin "
                 "assignment for this board (tx=%ld rx=%ld).  Set them "
                 "without a reflash: publish _device/<id>/transport = "
                 "\"uart tx=<gpio> rx=<gpio>\", or set the NVS keys "
                 "uart.tx / uart.rx in namespace '%s'.  Pins come from the "
                 "board's entry in [SPEC:CXC-BOARD-HW].  The bus is DOWN.",
                 (long)s_tx, (long)s_rx, CXC_NVS_NAMESPACE);
        return false;
    }

    uart_config_t cfg = {};
    cfg.baud_rate = (int)s_baud;
    cfg.data_bits = UART_DATA_8_BITS;
    cfg.parity = UART_PARITY_DISABLE;
    cfg.stop_bits = UART_STOP_BITS_1;
    cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    cfg.source_clk = UART_SCLK_DEFAULT;

    esp_err_t e = uart_driver_install((uart_port_t)s_port, kRxRingBytes,
                                      kRxRingBytes, 0, nullptr, 0);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "uart_driver_install(port %ld) failed: %s — the bus "
                 "is DOWN", (long)s_port, esp_err_to_name(e));
        return false;
    }
    uart_param_config((uart_port_t)s_port, &cfg);
    uart_set_pin((uart_port_t)s_port, (int)s_tx, (int)s_rx,
                 UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);

    s_queue = xQueueCreate(kQueueDepth, sizeof(BusEvent));
    s_started = true;
    xTaskCreate(rx_task, "bus_uart_rx", kRxTaskStack, nullptr, 5,
                &s_rx_task);

    /* The wiring AND where each number came from — a board must be able
     * to say how it is wired without anyone reading its build. */
    ESP_LOGI(TAG, "started: UART%ld TX=%ld RX=%ld @ %ld 8N1 "
             "(sources port/tx/rx/baud = %c%c%c%c, d=compiled default, "
             "n=NVS; client id %s)",
             (long)s_port, (long)s_tx, (long)s_rx, (long)s_baud,
             s_src[0], s_src[1], s_src[2], s_src[3],
             client_id ? client_id : "?");
    ESP_LOGW(TAG, "NO UPSTREAM HOST is known to be attached — this link "
             "has been verified by host loopback of its codec only.");
    return true;
}

static void register_ns(NsEntry* slot, const char* ns) {
    snprintf(slot->ns, sizeof(slot->ns), "%s", ns);
    slot->hash = bus_ns_hash(ns);
}

/* ------------------------------------------------------------------ */

class UartTransport final : public BusTransport {
public:
    const char* name() const override { return "uart"; }

    /* NO IP STACK.  This is the whole point of the substrate: a slaved board
     * stops taking a DHCP lease, an MQTT client slot and an HTTP server just
     * to render a scene ([CXC-BOARD-HW-UART-SLAVE]).  Returning true here
     * would silently give the address back. */
    bool needs_network() const override { return false; }

    bool start(const char* client_id) override { return uart_start(client_id); }

    void stop() override {
        if (!s_started) return;
        if (s_rx_task) { vTaskDelete(s_rx_task); s_rx_task = nullptr; }
        uart_driver_delete((uart_port_t)s_port);
        s_started = false;
        ESP_LOGI(TAG, "stopped");
    }

    /* A point-to-point link has no session to lose: once the driver is
     * installed the wire is as connected as it will ever be.  There is no
     * cheap way to know whether anything is LISTENING, and inventing one
     * (a probe on every render pass) is exactly what the Python ABC warns
     * against — this must stay a field read. */
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
        uint8_t v[1 + kBusFrameMaxString];
        size_t n = bus_value_encode_str(value, v, sizeof(v));
        send(ns, BusMessageType::State, v, n);
        ESP_LOGI(TAG, "PUBLISH %s = %s", ns, value);
    }

    /* float32, always — transport_uart.encode_value picks its encoding
     * from the PYTHON TYPE (a bool is one byte, an int 0..255 is one byte,
     * a float is four), and a C++ `double` carries no such distinction.
     * Choosing float32 for every number is the encoding that never lies:
     * the alternative, inferring "integral, so send one byte", would make
     * 1.0 and true indistinguishable on the wire. */
    void publish_number(const char* ns, double value) override {
        uint8_t v[kBusValueMaxNumeric];
        size_t n = bus_value_encode_f32((float)value, v);
        send(ns, BusMessageType::State, v, n);
    }

    /* Opaque bytes, arbitrary type — the config control plane's write side.
     * `send()` already refuses an unmapped type and an oversized value rather
     * than substituting either, so this only has to propagate the verdict. */
    bool publish_raw(const char* ns, BusMessageType type,
                     const uint8_t* value, size_t len) override {
        return send(ns, type, value, len);
    }

    /* 252 on this substrate: [BP-UART-FRAME]'s `len` is one byte and covers
     * ns_hash(2) + type_scope(1) + value.  The config chunker asks rather than
     * assuming, which is what lets the SAME format run at 8 bytes on CAN. */
    size_t max_raw_value() const override { return kBusFrameMaxValue; }

    /* STOPPED or UNKNOWN — never Ok, and that is deliberate.
     *
     * A point-to-point UART has NO link state.  The driver being installed
     * says the PORT is open; it says nothing about whether a wire is attached,
     * whether the far end is powered, or whether anything is listening.
     * Reporting Ok here would be the H2 defect rebuilt from scratch: an
     * instrument that reads the same whether the thing works or not.
     *
     * So the honest answer is kAnnLinkUnknown, and the fleet report prints a
     * dash.  What IS observable is decode failure, and that goes out through
     * link_has_errors() below — traffic arriving that this end cannot parse is
     * real evidence of a real fault (baud mismatch, floating rx, a half-seated
     * JST) in a way that "the port is open" never is. */
    uint8_t link_health() const override {
        return s_started ? kAnnLinkUnknown : kAnnLinkStopped;
    }

    bool link_has_errors() const override { return s_rx_frame_errors != 0; }
};

BusTransport& bus_uart_transport() {
    static UartTransport t;
    return t;
}
