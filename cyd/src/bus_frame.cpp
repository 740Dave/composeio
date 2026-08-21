/*
 * bus_frame.cpp — see bus_frame.h.  Ported from
 * python/composeio/transport_uart.py + firmware/ftl_jump_button/src/main.cpp.
 */

#include "bus_frame.h"

#include <cstring>

uint16_t bus_ns_hash(const char* ns) {
    uint16_t crc = 0xFFFF;
    if (!ns) return crc;
    for (const unsigned char* p = (const unsigned char*)ns; *p; p++) {
        crc ^= (uint16_t)(*p) << 8;
        for (int i = 0; i < 8; i++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                 : (uint16_t)(crc << 1);
    }
    return crc;
}

uint8_t bus_crc8(const uint8_t* data, size_t len) {
    uint8_t crc = 0;
    for (size_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (int j = 0; j < 8; j++)
            crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x31)
                               : (uint8_t)(crc << 1);
    }
    return crc;
}

/* ------------------------------------------------------------------ */
/* Values                                                              */
/* ------------------------------------------------------------------ */

size_t bus_value_encode_u8(uint8_t v, uint8_t* out) {
    /* ⛔ 253 IS CARVED OUT OF THE FAST PATH — [CXC-FRESH-WIRE], and it is the
     * IDENTICAL carve-out at transport_uart.encode_value:175.  A bare 0xFD
     * byte is the `unknown` tag, so emitting 253 as one byte makes the codec
     * NON-INJECTIVE: two distinct values, one encoding, and no decoder can
     * recover what the encoder threw away.
     *
     * Tagged int32 costs four extra bytes for this ONE value; every other
     * integer in 0..255 keeps its single byte.  ⚠ 254 and 255 are NOT carved
     * out — they never collided, because 0xFE/0xFF are discriminated by
     * LENGTH (read as tags only at five bytes) and 0xFD is not.  Widening the
     * carve-out to them would be cargo-culting.
     *
     * ⚠ THIS FUNCTION CAN NOW RETURN 5.  Every caller must size its buffer
     * kBusValueMaxNumeric, exactly as the i32/f32 encoders already require —
     * the same one-byte stack overwrite the tag landing already cost this
     * file once. */
    if (v == kBusValueTagUnknown) return bus_value_encode_i32((int32_t)v, out);
    out[0] = v;
    return 1;
}

size_t bus_value_encode_i32(int32_t v, uint8_t* out) {
    uint32_t u = (uint32_t)v;                 /* two's complement, "<i" */
    out[0] = kBusValueTagInt32;               /* [BP-VALUE-CODEC] type tag */
    out[1] = (uint8_t)(u & 0xFF);
    out[2] = (uint8_t)((u >> 8) & 0xFF);
    out[3] = (uint8_t)((u >> 16) & 0xFF);
    out[4] = (uint8_t)((u >> 24) & 0xFF);
    return 5;
}

size_t bus_value_encode_f32(float v, uint8_t* out) {
    uint32_t u;
    memcpy(&u, &v, 4);                        /* host is little-endian on
                                               * every target in this tree
                                               * (Xtensa LE, RV32 LE, x86,
                                               * arm64) — "<f" */
    out[0] = kBusValueTagFloat32;             /* [BP-VALUE-CODEC] type tag */
    out[1] = (uint8_t)(u & 0xFF);
    out[2] = (uint8_t)((u >> 8) & 0xFF);
    out[3] = (uint8_t)((u >> 16) & 0xFF);
    out[4] = (uint8_t)((u >> 24) & 0xFF);
    return 5;
}

size_t bus_value_encode_str(const char* s, uint8_t* out, size_t cap) {
    if (!s) return 0;
    size_t n = strlen(s);
    if (n > kBusFrameMaxString) n = kBusFrameMaxString;   /* Python truncates */
    if (n + 1 > cap) {
        if (cap == 0) return 0;
        n = cap - 1;
    }
    out[0] = (uint8_t)n;
    memcpy(out + 1, s, n);
    return n + 1;
}

/* UTF-8 structural validity — Python's decode_value falls through to the
 * float/raw branches on UnicodeDecodeError, so we must too.  Structure
 * only (lead byte + continuation count); it does not reject overlong
 * encodings or surrogates, which CPython would.  A frame carrying such a
 * sequence would decode as a string here and as a float/raw there — the
 * scene text on a display, not a control decision, and closing it costs
 * more code than the band tier has to spare.  Recorded rather than hidden. */
static bool utf8_valid(const uint8_t* p, size_t n) {
    size_t i = 0;
    while (i < n) {
        uint8_t c = p[i];
        size_t extra;
        if (c < 0x80) extra = 0;
        else if ((c & 0xE0) == 0xC0) extra = 1;
        else if ((c & 0xF0) == 0xE0) extra = 2;
        else if ((c & 0xF8) == 0xF0) extra = 3;
        else return false;
        if (i + extra >= n) return false;
        for (size_t k = 1; k <= extra; k++)
            if ((p[i + k] & 0xC0) != 0x80) return false;
        i += extra + 1;
    }
    return true;
}

size_t bus_value_encode_unknown(uint8_t* out) {
    out[0] = kBusValueTagUnknown;
    return 1;                                 /* ZERO-length payload */
}

BusValueKind bus_value_decode(const uint8_t* v, size_t n,
                              double* num, char* str, size_t str_cap) {
    if (n == 0) return BusValueKind::None;

    /* `unknown` BEFORE the single-byte branch, which would otherwise return
     * the integer 253.  Mirrors transport_uart.decode_value's ordering
     * exactly.  ⛔ It is the ENCODER's 253 carve-out that makes this
     * unambiguous rather than merely preferential — without it both readings
     * are equally correct and the information is already gone. */
    if (n == 1 && v[0] == kBusValueTagUnknown) return BusValueKind::Unknown;

    /* TAGGED WIDTHS FIRST, and they are EXACT — [BP-VALUE-CODEC].  This is
     * the branch that closes DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9:
     * before the tag existed, these five bytes were four bytes that this
     * decoder read as a float and the CAN bridge read as an int. */
    if (n == 5 && (v[0] == kBusValueTagInt32 || v[0] == kBusValueTagFloat32)) {
        uint32_t u = (uint32_t)v[1] | ((uint32_t)v[2] << 8) |
                     ((uint32_t)v[3] << 16) | ((uint32_t)v[4] << 24);
        if (v[0] == kBusValueTagInt32) {
            if (num) *num = (double)(int32_t)u;   /* SIGNED — -5 is -5, not
                                                   * 4294967291 and not nan */
        } else {
            float f;
            memcpy(&f, &u, 4);
            if (num) *num = (double)f;
        }
        return BusValueKind::Number;
    }

    if (n == 1) {
        if (num) *num = (double)v[0];
        return BusValueKind::Number;         /* bool/int/enum index alike */
    }

    /* Length-prefixed string, exactly spanning the rest (Python's
     * `str_len == len(data) - 1` branch). */
    if ((size_t)v[0] == n - 1 && utf8_valid(v + 1, n - 1)) {
        if (str && str_cap) {
            size_t m = n - 1;
            if (m > str_cap - 1) m = str_cap - 1;
            memcpy(str, v + 1, m);
            str[m] = '\0';
        }
        return BusValueKind::String;
    }

    /* NO untagged-4-byte-means-float32 branch any more.  That guess is what
     * turned 1000 into 1.4e-42 and -5 into nan; an untagged wide value is a
     * frame from a peer this fleet has not reflashed, and Raw makes it
     * visibly undecodable instead of invisibly wrong. */
    return BusValueKind::Raw;
}

/* ------------------------------------------------------------------ */
/* Frames                                                              */
/* ------------------------------------------------------------------ */

size_t bus_frame_encode(uint16_t ns_hash, BusMessageType type,
                        uint8_t scope_bits,
                        const uint8_t* value, size_t value_len,
                        uint8_t* out, size_t cap) {
    uint8_t code;
    if (!bus_wire_code_for_type(type, &code))
        return 0;      /* NO SUBSTITUTE — [BP-TYPE-CODES]; caller drops. */
    if (value_len > kBusFrameMaxValue) return 0;

    size_t payload_len = 3 + value_len;
    size_t total = 2 + payload_len + 1;
    if (total > cap) return 0;

    out[0] = kBusFrameSync;
    out[1] = (uint8_t)payload_len;
    out[2] = (uint8_t)((ns_hash >> 8) & 0xFF);
    out[3] = (uint8_t)(ns_hash & 0xFF);
    out[4] = (uint8_t)((code << 4) | ((scope_bits & kBusWireScopeMask) << 2));
    if (value_len) memcpy(out + 5, value, value_len);
    out[2 + payload_len] = bus_crc8(out + 1, 1 + payload_len);
    return total;
}

BusFrameResult bus_frame_decode(const uint8_t* buf, size_t len,
                                BusFrame* out, size_t* consumed) {
    if (consumed) *consumed = 0;

    /* Resync: skip anything before the sync byte. */
    size_t start = 0;
    while (start < len && buf[start] != kBusFrameSync) start++;
    if (start >= len) {
        if (consumed) *consumed = len;      /* nothing usable — discard */
        return BusFrameResult::NoSync;
    }
    if (start > 0) {
        if (consumed) *consumed = start;
        return BusFrameResult::NoSync;      /* caller drops the garbage,
                                             * then calls again */
    }

    if (len < 2) return BusFrameResult::Incomplete;
    size_t payload_len = buf[1];
    size_t frame_len = 2 + payload_len + 1;
    if (payload_len < 3) {
        /* Cannot even hold ns_hash + type_scope: a false sync byte. */
        if (consumed) *consumed = 1;
        return BusFrameResult::NoSync;
    }
    if (len < frame_len) return BusFrameResult::Incomplete;

    uint8_t crc = bus_crc8(buf + 1, 1 + payload_len);
    if (crc != buf[2 + payload_len]) {
        if (consumed) *consumed = 1;   /* resync PAST this sync byte only —
                                        * the length may itself be noise */
        return BusFrameResult::BadCrc;
    }

    uint8_t type_scope = buf[4];
    BusMessageType type;
    if (!bus_type_for_wire_code((uint8_t)((type_scope >> 4) & kBusWireTypeCodeMask),
                                &type)) {
        if (consumed) *consumed = frame_len;
        return BusFrameResult::UnallocatedType;   /* DROP, never guess */
    }

    if (out) {
        out->ns_hash = (uint16_t)(((uint16_t)buf[2] << 8) | buf[3]);
        out->type = type;
        out->scope_bits = (uint8_t)((type_scope >> 2) & kBusWireScopeMask);
        out->value = buf + 5;
        out->value_len = (uint8_t)(payload_len - 3);
    }
    if (consumed) *consumed = frame_len;
    return BusFrameResult::Ok;
}
