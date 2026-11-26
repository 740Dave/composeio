#include "r503_link.h"

#include <string.h>

#include "driver/uart.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define R503_UART UART_NUM_1
#define RX_RING 1024
#define TX_RING 512

static int s_tx_gpio = -1;
static int s_rx_gpio = -1;
static bool s_installed = false;
static bool s_loopback = false;

static inline uint32_t now_ms(void) { return (uint32_t)(esp_timer_get_time() / 1000); }

bool r503_link_init(int tx_gpio, int rx_gpio) {
    if (s_installed) {
        uart_driver_delete(R503_UART);
        s_installed = false;
    }
    const uart_config_t cfg = {
        .baud_rate = R503_BAUD,  // ⛔ 57600, always. No baud-change path exists.
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    if (uart_driver_install(R503_UART, RX_RING, TX_RING, 0, NULL, 0) != ESP_OK) {
        return false;
    }
    if (uart_param_config(R503_UART, &cfg) != ESP_OK) {
        uart_driver_delete(R503_UART);
        return false;
    }
    if (uart_set_pin(R503_UART, tx_gpio, rx_gpio, UART_PIN_NO_CHANGE,
                     UART_PIN_NO_CHANGE) != ESP_OK) {
        uart_driver_delete(R503_UART);
        return false;
    }
    s_tx_gpio = tx_gpio;
    s_rx_gpio = rx_gpio;
    s_installed = true;
    s_loopback = false;
    return true;
}

int r503_link_tx_gpio(void) { return s_tx_gpio; }
int r503_link_rx_gpio(void) { return s_rx_gpio; }

bool r503_link_set_loopback(bool on) {
    if (!s_installed) return false;
    if (uart_set_loop_back(R503_UART, on) != ESP_OK) return false;
    s_loopback = on;
    return true;
}

bool r503_link_loopback_is_on(void) { return s_loopback; }

uint32_t r503_link_drain(uint32_t settle_ms, uint8_t *keep, uint8_t keep_cap,
                         uint8_t *kept_out) {
    uint32_t total = 0;
    uint8_t kept = 0;
    if (!s_installed) {
        if (kept_out) *kept_out = 0;
        return 0;
    }
    // BOUNDED: a wall-clock deadline AND a byte budget. Neither alone is enough —
    // a babbling line would satisfy a byte budget forever if the clock were not
    // also checked, and a slow trickle would satisfy the clock while overflowing.
    const uint32_t deadline = now_ms() + (settle_ms ? settle_ms : 1);
    const uint32_t byte_budget = 4096;
    uint8_t b;
    while (now_ms() < deadline && total < byte_budget) {
        int n = uart_read_bytes(R503_UART, &b, 1, pdMS_TO_TICKS(2));
        if (n <= 0) {
            // Nothing right now. Keep checking until the deadline, so a byte that
            // is still in flight is not missed by an over-eager exit.
            continue;
        }
        total++;
        if (keep && kept < keep_cap) {
            keep[kept++] = b;
        }
    }
    if (kept_out) *kept_out = kept;
    return total;
}

uint32_t r503_link_listen(uint32_t window_ms, uint8_t *buf, uint32_t cap,
                          uint32_t *first_byte_ms) {
    uint32_t total = 0;
    if (first_byte_ms) *first_byte_ms = 0;
    if (!s_installed) return 0;
    const uint32_t t0 = now_ms();
    const uint32_t deadline = t0 + (window_ms ? window_ms : 1);
    uint8_t b;
    while (now_ms() < deadline && total < cap) {
        int n = uart_read_bytes(R503_UART, &b, 1, pdMS_TO_TICKS(5));
        if (n <= 0) continue;
        if (total == 0 && first_byte_ms) *first_byte_ms = now_ms() - t0;
        buf[total++] = b;
    }
    return total;
}

// Drain, write `frame` verbatim, then read exactly one framed response.
// Shared by r503_link_txn (which encodes first) and r503_link_rawtx (which does not),
// so both go through IDENTICAL receive semantics — a test that exercised a different
// receive path from the real one would certify nothing.
static r503_txn_result_t tx_then_receive(const uint8_t *frame, uint16_t flen,
                                         uint32_t deadline_ms, uint32_t inter_byte_ms,
                                         r503_packet_t *out, r503_txn_stats_t *stats) {
    memset(stats, 0, sizeof(*stats));
    if (!s_installed) return R503_TXN_TX_FAIL;
    if (deadline_ms == 0) deadline_ms = 1;
    if (inter_byte_ms == 0) inter_byte_ms = 1;

    // ⛔ DRAIN IMMEDIATELY BEFORE THE TRANSMIT — the ORDER is the remedy.
    // [CXC-R503-UNSOLICITED-0X55]. Draining at init only would leave the
    // post-SoftRst 0x55, and any stale packet from an aborted transaction, sitting
    // in the ring to be mistaken for the head of THIS response.
    stats->pre_drain_count = r503_link_drain(3, stats->pre_drain,
                                             sizeof(stats->pre_drain),
                                             &stats->pre_drain_kept);

    const uint32_t t_tx = now_ms();
    int wrote = uart_write_bytes(R503_UART, frame, (size_t)flen);
    if (wrote != (int)flen) return R503_TXN_TX_FAIL;
    // Bounded flush: never wait forever for the FIFO to empty.
    (void)uart_wait_tx_done(R503_UART, pdMS_TO_TICKS(200));

    r503_parser_t parser;
    r503_parser_reset(&parser);

    const uint32_t hard_deadline = now_ms() + deadline_ms;
    uint32_t last_byte_at = 0;
    bool any_byte = false;
    uint8_t b;

    for (;;) {
        uint32_t t = now_ms();

        // HARD DEADLINE — the outer bound. Always checked first.
        if (t >= hard_deadline) {
            stats->elapsed_ms = t - t_tx;
            stats->skipped_count = parser.skipped_count;
            stats->skipped_kept = parser.skipped_kept;
            memcpy(stats->skipped, parser.skipped, R503_SKIP_LOG);
            return any_byte ? R503_TXN_TIMEOUT_PARTIAL : R503_TXN_TIMEOUT_NO_BYTES;
        }

        // INTER-BYTE TIMEOUT — the inner bound, armed only once a byte has arrived.
        // [CXC-R503-DATA-PACKET-BURSTS]: this is the only timeout form that survives
        // a variable-length burst, because the total length is not knowable up front.
        if (any_byte && (t - last_byte_at) >= inter_byte_ms) {
            stats->elapsed_ms = t - t_tx;
            stats->skipped_count = parser.skipped_count;
            stats->skipped_kept = parser.skipped_kept;
            memcpy(stats->skipped, parser.skipped, R503_SKIP_LOG);
            return R503_TXN_TIMEOUT_PARTIAL;
        }

        int n = uart_read_bytes(R503_UART, &b, 1, pdMS_TO_TICKS(2));
        if (n <= 0) continue;

        stats->rx_total++;
        if (!any_byte) {
            any_byte = true;
            stats->first_byte_ms = now_ms() - t_tx;
        }
        last_byte_at = now_ms();

        r503_parse_result_t r = r503_parse_byte(&parser, b, out);
        if (r == R503_PARSE_NEED_MORE) continue;

        stats->elapsed_ms = now_ms() - t_tx;
        stats->skipped_count = parser.skipped_count;
        stats->skipped_kept = parser.skipped_kept;
        memcpy(stats->skipped, parser.skipped, R503_SKIP_LOG);

        switch (r) {
            case R503_PARSE_OK:
                return R503_TXN_OK;
            case R503_PARSE_BAD_CHECKSUM:
                return R503_TXN_BAD_CHECKSUM;
            case R503_PARSE_LENGTH_ABSURD:
                return R503_TXN_LENGTH_ABSURD;
            default:
                return R503_TXN_TIMEOUT_PARTIAL;
        }
    }
}

r503_txn_result_t r503_link_txn(const uint8_t *content, uint16_t content_len,
                                bool checksum_fault, uint32_t deadline_ms,
                                uint32_t inter_byte_ms, r503_packet_t *out,
                                r503_txn_stats_t *stats) {
    uint8_t frame[R503_MAX_CONTENT + 11];
    int flen = r503_encode(frame, sizeof(frame), R503_DEFAULT_ADDRESS, R503_PID_COMMAND,
                           content, content_len, checksum_fault);
    if (flen < 0) {
        memset(stats, 0, sizeof(*stats));
        return R503_TXN_TX_FAIL;
    }
    return tx_then_receive(frame, (uint16_t)flen, deadline_ms, inter_byte_ms, out, stats);
}

r503_txn_result_t r503_link_rawtx(const uint8_t *bytes, uint16_t n,
                                  uint32_t deadline_ms, uint32_t inter_byte_ms,
                                  r503_packet_t *out, r503_txn_stats_t *stats) {
    // ⛔ Verbatim. No header, no length, no checksum is computed or appended.
    return tx_then_receive(bytes, n, deadline_ms, inter_byte_ms, out, stats);
}
