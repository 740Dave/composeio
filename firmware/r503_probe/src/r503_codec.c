#include "r503_codec.h"

#include <string.h>

uint16_t r503_checksum(uint8_t pid, uint16_t length_field, const uint8_t *content,
                       uint16_t content_len) {
    // [CXC-R503-CHECKSUM-VERIFIED]: PID + BOTH length bytes + every content byte,
    // mod 2^16. ⛔ Header and Address are NOT included.
    uint32_t sum = pid;
    sum += (length_field >> 8) & 0xFFu;
    sum += length_field & 0xFFu;
    for (uint16_t i = 0; i < content_len; i++) {
        sum += content[i];
    }
    return (uint16_t)(sum & 0xFFFFu);
}

int r503_encode(uint8_t *out, size_t out_cap, uint32_t address, uint8_t pid,
                const uint8_t *content, uint16_t content_len, bool checksum_fault) {
    if (content_len > R503_MAX_CONTENT) {
        return -1;
    }
    size_t need = (size_t)content_len + 11u;  // 2 hdr + 4 addr + 1 pid + 2 len + 2 sum
    if (out_cap < need) {
        return -1;
    }

    // ⛔ Length counts content PLUS the 2 checksum bytes. [CXC-R503-FRAMING]
    uint16_t length_field = (uint16_t)(content_len + 2u);

    size_t i = 0;
    out[i++] = R503_HEADER_HI;
    out[i++] = R503_HEADER_LO;
    out[i++] = (uint8_t)((address >> 24) & 0xFFu);  // big-endian [CXC-R503-FRAMING]
    out[i++] = (uint8_t)((address >> 16) & 0xFFu);
    out[i++] = (uint8_t)((address >> 8) & 0xFFu);
    out[i++] = (uint8_t)(address & 0xFFu);
    out[i++] = pid;
    out[i++] = (uint8_t)((length_field >> 8) & 0xFFu);
    out[i++] = (uint8_t)(length_field & 0xFFu);
    for (uint16_t k = 0; k < content_len; k++) {
        out[i++] = content[k];
    }
    uint16_t sum = r503_checksum(pid, length_field, content, content_len);
    if (checksum_fault) {
        sum = (uint16_t)(sum + 1u);  // deliberate corruption — the negative control
    }
    out[i++] = (uint8_t)((sum >> 8) & 0xFFu);
    out[i++] = (uint8_t)(sum & 0xFFu);
    return (int)i;
}

enum {
    ST_HUNT = 0,  // sliding two-byte window, looking for EF 01
    ST_ADDR,
    ST_PID,
    ST_LEN,
    ST_BODY,
};

void r503_parser_reset(r503_parser_t *p) {
    memset(p, 0, sizeof(*p));
    p->state = ST_HUNT;
}

static void note_skipped(r503_parser_t *p, uint8_t b) {
    p->skipped_count++;
    if (p->skipped_kept < R503_SKIP_LOG) {
        p->skipped[p->skipped_kept++] = b;
    }
}

r503_parse_result_t r503_parse_byte(r503_parser_t *p, uint8_t b, r503_packet_t *out) {
    switch (p->state) {
        case ST_HUNT:
            // ⛔ SLIDING TWO-BYTE HEADER WINDOW. [CXC-R503-UNSOLICITED-0X55]
            // The window slides one byte at a time, so a stray 0x55 (or a whole
            // stale packet from an aborted transaction) costs synchronisation for
            // exactly as many bytes as it occupies, and no more. Every byte that
            // falls out of the window is COUNTED and the first R503_SKIP_LOG are
            // KEPT, so the 0x55 becomes a measurement rather than a swallowed byte.
            if (p->win_primed) {
                if (p->win_lo == R503_HEADER_HI && b == R503_HEADER_LO) {
                    p->state = ST_ADDR;
                    p->field_pos = 0;
                    return R503_PARSE_NEED_MORE;
                }
                note_skipped(p, p->win_lo);
            }
            p->win_hi = p->win_lo;
            p->win_lo = b;
            p->win_primed = true;
            return R503_PARSE_NEED_MORE;

        case ST_ADDR:
            p->addr_buf[p->field_pos++] = b;
            if (p->field_pos == 4) {
                p->address = ((uint32_t)p->addr_buf[0] << 24) |
                             ((uint32_t)p->addr_buf[1] << 16) |
                             ((uint32_t)p->addr_buf[2] << 8) | (uint32_t)p->addr_buf[3];
                p->state = ST_PID;
            }
            return R503_PARSE_NEED_MORE;

        case ST_PID:
            p->pid = b;
            p->state = ST_LEN;
            p->field_pos = 0;
            return R503_PARSE_NEED_MORE;

        case ST_LEN:
            if (p->field_pos == 0) {
                p->len_hi = b;
                p->field_pos = 1;
                return R503_PARSE_NEED_MORE;
            }
            p->length_field = (uint16_t)(((uint16_t)p->len_hi << 8) | b);
            // Length must cover at least a 1-byte content plus the 2 checksum bytes.
            // Anything smaller, or larger than the buffer, is a DESYNC TELL — report
            // it rather than trusting it, and resume hunting from scratch.
            if (p->length_field < 3u || p->length_field > (R503_MAX_CONTENT + 2)) {
                if (out) {
                    memset(out, 0, sizeof(*out));
                    out->address = p->address;
                    out->pid = p->pid;
                    out->length_field = p->length_field;
                }
                uint32_t carried = p->skipped_count;
                uint8_t carried_kept = p->skipped_kept;
                uint8_t carried_log[R503_SKIP_LOG];
                memcpy(carried_log, p->skipped, R503_SKIP_LOG);
                r503_parser_reset(p);
                p->skipped_count = carried;
                p->skipped_kept = carried_kept;
                memcpy(p->skipped, carried_log, R503_SKIP_LOG);
                return R503_PARSE_LENGTH_ABSURD;
            }
            p->body_expect = p->length_field;  // content + 2 checksum bytes
            p->body_pos = 0;
            p->state = ST_BODY;
            return R503_PARSE_NEED_MORE;

        case ST_BODY:
        default:
            p->body[p->body_pos++] = b;
            if (p->body_pos < p->body_expect) {
                return R503_PARSE_NEED_MORE;
            }
            {
                uint16_t content_len = (uint16_t)(p->body_expect - 2u);
                uint16_t rx = (uint16_t)(((uint16_t)p->body[content_len] << 8) |
                                         p->body[content_len + 1]);
                uint16_t calc = r503_checksum(p->pid, p->length_field, p->body, content_len);

                if (out) {
                    memset(out, 0, sizeof(*out));
                    out->address = p->address;
                    out->pid = p->pid;
                    out->length_field = p->length_field;
                    out->content_len = content_len;
                    memcpy(out->content, p->body, content_len);
                    out->checksum_rx = rx;
                    out->checksum_calc = calc;
                }

                // Carry the skip log out of the reset — it belongs to the transaction,
                // not to the parser's internal state, and it is the only record that
                // an unsolicited byte ever arrived.
                uint32_t carried = p->skipped_count;
                uint8_t carried_kept = p->skipped_kept;
                uint8_t carried_log[R503_SKIP_LOG];
                memcpy(carried_log, p->skipped, R503_SKIP_LOG);
                r503_parser_reset(p);
                p->skipped_count = carried;
                p->skipped_kept = carried_kept;
                memcpy(p->skipped, carried_log, R503_SKIP_LOG);

                return (rx == calc) ? R503_PARSE_OK : R503_PARSE_BAD_CHECKSUM;
            }
    }
}
