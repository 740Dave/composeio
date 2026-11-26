// R503 link layer — owns the UART, the drain, the deadlines and the resync.
//
// ⛔ THIS LAYER CANNOT BE MOVED TO THE HOST. It is why the probe is two parts.
//   - inter-byte timeouts are the only form that survives a variable-length burst
//     ([CXC-R503-DATA-PACKET-BURSTS]: burst count is NOT derivable from the command,
//     it depends on a negotiated packet-size parameter)
//   - drain-RX-immediately-before-every-TX is the remedy for the unsolicited 0x55
//     ([CXC-R503-UNSOLICITED-0X55]) and the ORDERING is the whole remedy — it is
//     only meaningful with the UART in hand
//   - there is no end-of-stream marker; the ack-stream terminator is INFERRED
//     ([CXC-R503-ACK-STREAM-BREAKS-REQ-RESP] `stream-terminator-is-inferred`)
//
// ⛔ NO UNBOUNDED WAIT EXISTS IN THIS FILE. Every read carries a deadline AND a
//   byte budget. A shipped driver in this space hangs its host forever because its
//   timeout returns empty and the caller continues with no counter.
//
// ⛔ NO BAUD-CHANGE PATH EXISTS ANYWHERE IN THIS FIRMWARE.
//   [CXC-R503-BAUD-SELF-CONTRADICTS]: every revision contradicts itself on legal
//   rates, one shipped driver exposes seven forbidden values, and NO RECOVERY FROM A
//   BRICKED MODULE IS DOCUMENTED ANYWHERE. 57600 is the documented default and it is
//   the only rate this firmware will ever use. There is deliberately no SetSysPara
//   (0x0E) helper here; reaching it requires the operator to hand-assemble the bytes
//   through `raw`, which is a decision, not an accident.

#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "r503_codec.h"

#define R503_BAUD 57600  // ⛔ documented default. NEVER changed. See above.

typedef struct {
    // Bytes drained from RX immediately BEFORE the transmit. This is where the
    // unsolicited 0x55 shows up. Reported on every transaction, never swallowed.
    uint32_t pre_drain_count;
    uint8_t pre_drain[32];
    uint8_t pre_drain_kept;

    // Bytes discarded by the sliding header window while hunting for THIS response.
    uint32_t skipped_count;
    uint8_t skipped[R503_SKIP_LOG];
    uint8_t skipped_kept;

    uint32_t elapsed_ms;   // first byte of TX to terminal condition
    uint32_t first_byte_ms;  // TX complete to first RX byte; 0 if none arrived
    uint32_t rx_total;     // every byte read from the UART during this transaction
} r503_txn_stats_t;

typedef enum {
    R503_TXN_OK = 0,
    R503_TXN_TIMEOUT_NO_BYTES,   // deadline expired, ZERO bytes ever arrived
    R503_TXN_TIMEOUT_PARTIAL,    // bytes arrived then stopped — inter-byte timeout
    R503_TXN_BAD_CHECKSUM,       // a complete frame that did not verify
    R503_TXN_LENGTH_ABSURD,      // Length field outside any workable range
    R503_TXN_TX_FAIL,
} r503_txn_result_t;

// Configure/reconfigure the UART. Safe to call repeatedly (used by the `pins` verb,
// which exists because the SuperMini's silkscreen is not a fact this firmware holds).
bool r503_link_init(int tx_gpio, int rx_gpio);
int r503_link_tx_gpio(void);
int r503_link_rx_gpio(void);

// Internal UART loopback — the POSITIVE CONTROL for the negative control.
// A clean timeout with nothing attached only means something if the SAME code path
// demonstrably succeeds when bytes DO arrive. Loopback makes that demonstrable on
// the real silicon with no R503 and no wire.
// ⚠ While enabled the TX pin still drives the line: run it with nothing attached.
bool r503_link_set_loopback(bool on);
bool r503_link_loopback_is_on(void);

// Drain RX and report what was there. [CXC-R503-UNSOLICITED-0X55]
// Bounded: reads for at most `settle_ms` and at most sizeof(stats.pre_drain)*N bytes.
uint32_t r503_link_drain(uint32_t settle_ms, uint8_t *keep, uint8_t keep_cap,
                         uint8_t *kept_out);

// One transaction: drain, transmit, then read exactly one framed response.
//   deadline_ms   — hard ceiling from end-of-TX to a terminal result
//   inter_byte_ms — once ANY byte has arrived, the gap allowed before the frame is
//                   declared truncated
// Both are enforced. Neither may be zero.
r503_txn_result_t r503_link_txn(const uint8_t *content, uint16_t content_len,
                                bool checksum_fault, uint32_t deadline_ms,
                                uint32_t inter_byte_ms, r503_packet_t *out,
                                r503_txn_stats_t *stats);

// Transmit RAW bytes verbatim — NOT a framed packet — then run the ordinary
// receive path. This exists so a DELIBERATELY MALFORMED stream can be put on real
// UART hardware: a truncated frame, a bare 0x55 in front of a good ack, a false
// header. Without it the partial-frame timeout is untestable, and an untestable
// branch is one that gets reported as working forever.
// ⛔ It does NOT compute a checksum and does NOT add a header. What you give it is
//    exactly what goes on the wire.
r503_txn_result_t r503_link_rawtx(const uint8_t *bytes, uint16_t n,
                                  uint32_t deadline_ms, uint32_t inter_byte_ms,
                                  r503_packet_t *out, r503_txn_stats_t *stats);

// Listen for unsolicited traffic without transmitting anything at all.
// Fills `buf` with up to `cap` bytes over at most `window_ms`. Returns the count.
// This is how the power-on / post-SoftRst 0x55 gets captured as data.
uint32_t r503_link_listen(uint32_t window_ms, uint8_t *buf, uint32_t cap,
                          uint32_t *first_byte_ms);
