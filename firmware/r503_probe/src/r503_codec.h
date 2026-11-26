// R503 packet codec — the layer where a byte-level error is SILENT.
//
// Every rule here cites the spec node that pays for it. Do not "simplify" one
// without reading its citation first.
//
//   [CXC-R503-FRAMING]           Header 0xEF01, Address 4B, PID 1B, Length 2B,
//                                Content, Checksum 2B — BIG-ENDIAN throughout.
//   [CXC-R503-FRAMING]           ⛔ Length counts content PLUS the 2 checksum bytes.
//                                A parser reading Length as the content length
//                                overruns by exactly two bytes on EVERY packet.
//   [CXC-R503-CHECKSUM-VERIFIED] Checksum = PID + both Length bytes + every content
//                                byte, mod 2^16. ⛔ Header and Address are NOT
//                                included. Recomputed against all 21 worked examples
//                                in the manual set; all 21 recompute exactly. So a
//                                checksum failure here means OUR bug, not theirs.
//   [CXC-R503-UNSOLICITED-0X55]  A bare unframed 0x55 arrives at power-on AND after
//                                every SoftRst. The remedy is BOTH drain-before-TX
//                                (owned by r503_link) and a sliding two-byte header
//                                window (owned here). Either alone is partial.

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define R503_HEADER_HI 0xEFu
#define R503_HEADER_LO 0x01u
#define R503_DEFAULT_ADDRESS 0xFFFFFFFFu

// PIDs. [CXC-R503-FRAMING]
#define R503_PID_COMMAND 0x01u
#define R503_PID_DATA 0x02u
#define R503_PID_ACK 0x07u
#define R503_PID_END_OF_DATA 0x08u

// ⛔ [CXC-R503-LENGTH-256-UNWORKED] — the manual's "Max length is 256 bytes" is
// self-contradictory with the 256-byte data-packet-size parameter, and the 256 case
// is NEVER worked anywhere in any revision. This buffer is sized to accept a frame
// larger than any WORKED case so that an over-long frame is REPORTED as a finding
// rather than silently truncated into a plausible-looking short one.
#define R503_MAX_CONTENT 300

typedef struct {
    uint32_t address;
    uint8_t pid;
    uint16_t length_field;   // AS RECEIVED, not as recomputed. This is the raw
                             // wire value; [CXC-R503-EV-15] turns on it.
    uint16_t content_len;    // length_field - 2, i.e. the real content byte count.
    uint8_t content[R503_MAX_CONTENT];
    uint16_t checksum_rx;    // as received
    uint16_t checksum_calc;  // as recomputed
} r503_packet_t;

// Encode a command packet. Returns the total byte count written, or -1 if the
// content would not fit. `out` must hold at least content_len + 11 bytes.
//
// `checksum_fault` deliberately corrupts the transmitted checksum by +1. It exists
// so the NEGATIVE CONTROL is a first-class affordance and not a code edit: an
// instrument that has no way to be wrong cannot be shown to work.
int r503_encode(uint8_t *out, size_t out_cap, uint32_t address, uint8_t pid,
                const uint8_t *content, uint16_t content_len, bool checksum_fault);

// Checksum over PID + both length bytes + content. [CXC-R503-CHECKSUM-VERIFIED]
uint16_t r503_checksum(uint8_t pid, uint16_t length_field, const uint8_t *content,
                       uint16_t content_len);

typedef enum {
    R503_PARSE_OK = 0,
    R503_PARSE_NEED_MORE,        // not enough bytes yet — keep feeding
    R503_PARSE_BAD_CHECKSUM,     // framed correctly, checksum did not verify
    R503_PARSE_LENGTH_ABSURD,    // Length < 3 or > buffer — a desync tell
} r503_parse_result_t;

// Incremental parser with a SLIDING TWO-BYTE HEADER WINDOW.
// [CXC-R503-UNSOLICITED-0X55] — a fixed-offset reader is PERMANENTLY phase-shifted
// by the unsolicited 0x55. This one resynchronises on 0xEF 0x01 anywhere in the
// stream, and it COUNTS AND KEEPS the bytes it skipped, because a byte that is
// discarded silently is a measurement thrown away.
#define R503_SKIP_LOG 32

typedef struct {
    uint8_t state;
    uint8_t win_hi, win_lo;      // sliding two-byte header window
    bool win_primed;
    uint16_t field_pos;
    uint16_t body_pos;
    uint16_t body_expect;        // == length_field, i.e. content + 2 checksum bytes
    uint8_t body[R503_MAX_CONTENT + 2];
    uint32_t address;
    uint8_t pid;
    uint16_t length_field;
    uint8_t len_hi;
    uint8_t addr_buf[4];
    uint32_t skipped_count;      // bytes discarded looking for a header, this frame
    uint8_t skipped[R503_SKIP_LOG];
    uint8_t skipped_kept;
} r503_parser_t;

void r503_parser_reset(r503_parser_t *p);

// Feed exactly one byte. Returns R503_PARSE_NEED_MORE until a frame completes.
// On any terminal result, `out` is filled in as far as it is meaningful — a
// BAD_CHECKSUM packet still yields its PID, length and content, because a corrupt
// frame is evidence and throwing it away destroys the evidence.
r503_parse_result_t r503_parse_byte(r503_parser_t *p, uint8_t b, r503_packet_t *out);
