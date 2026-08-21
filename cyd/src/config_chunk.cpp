/*
 * config_chunk.cpp — see config_chunk.h.
 * Port of python/composeio/config_chunk.py; env:native_config_chunk proves the
 * two agree byte-for-byte against frames baked from the Python side.
 */

#include "config_chunk.h"

#include <cstdlib>
#include <cstring>

uint16_t config_content_hash(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        /* The cast matters: `data[i] << 8` on a signed char promotes with sign
         * extension and every byte >= 0x80 hashes wrong.  The namespace hash
         * never sees one (ASCII namespaces); config CONTENT is arbitrary
         * bytes, so the corpus carries 0x80+ vectors on purpose. */
        crc = (uint16_t)(crc ^ ((uint16_t)data[i] << 8));
        for (int b = 0; b < 8; b++)
            crc = (uint16_t)((crc & 0x8000) ? ((crc << 1) ^ 0x1021)
                                            : (crc << 1));
    }
    return crc;
}

uint16_t config_chunk_count(uint32_t total_len, uint8_t chunk_payload) {
    if (chunk_payload == 0 || total_len == 0) return 0;
    return (uint16_t)((total_len + chunk_payload - 1) / chunk_payload);
}

ConfigFrameKind config_frame_parse(const uint8_t* value, size_t len,
                                   ConfigChunkHeader* hdr, uint16_t* seq,
                                   const uint8_t** content,
                                   size_t* content_len) {
    if (!value || len < kConfigSeqBytes) return ConfigFrameKind::Malformed;
    uint16_t s = (uint16_t)((value[0] << 8) | value[1]);
    const uint8_t* c = value + kConfigSeqBytes;
    size_t n = len - kConfigSeqBytes;

    if (s == kConfigHeaderSeq) {
        if (n != kConfigHeaderContentBytes) return ConfigFrameKind::Malformed;
        if (hdr) {
            hdr->ttl = c[0];
            hdr->chunk_payload = c[1];
            hdr->total_len = (uint16_t)((c[2] << 8) | c[3]);
            hdr->content_hash = (uint16_t)((c[4] << 8) | c[5]);
            hdr->chunk_count = config_chunk_count(hdr->total_len,
                                                  hdr->chunk_payload);
        }
        if (seq) *seq = s;
        if (content) *content = c;
        if (content_len) *content_len = n;
        return ConfigFrameKind::Header;
    }

    if (seq) *seq = s;
    if (content) *content = c;
    if (content_len) *content_len = n;
    return ConfigFrameKind::Data;
}

size_t config_encode_header(uint16_t total_len, uint8_t chunk_payload,
                            uint16_t content_hash, uint8_t ttl,
                            uint8_t* out, size_t cap) {
    if (!out || cap < kConfigSeqBytes + kConfigHeaderContentBytes) return 0;
    if (chunk_payload == 0 || chunk_payload > 250) return 0;
    out[0] = 0;
    out[1] = 0;
    out[2] = ttl;
    out[3] = chunk_payload;
    out[4] = (uint8_t)(total_len >> 8);
    out[5] = (uint8_t)(total_len & 0xFF);
    out[6] = (uint8_t)(content_hash >> 8);
    out[7] = (uint8_t)(content_hash & 0xFF);
    return kConfigSeqBytes + kConfigHeaderContentBytes;
}

size_t config_encode_data(uint16_t seq, const uint8_t* content, size_t n,
                          uint8_t* out, size_t cap) {
    if (!out || seq == 0 || cap < kConfigSeqBytes + n) return 0;
    out[0] = (uint8_t)(seq >> 8);
    out[1] = (uint8_t)(seq & 0xFF);
    if (n && content) memcpy(out + kConfigSeqBytes, content, n);
    return kConfigSeqBytes + n;
}

size_t config_encode_ack(ConfigAckStatus status, uint16_t hash,
                         uint16_t total_len, uint16_t ackseq,
                         uint8_t* out, size_t cap) {
    if (!out || cap < kConfigAckBytes) return 0;
    out[0] = (uint8_t)(kConfigAckTag |
                       ((uint8_t)status & kConfigAckStatusMask));
    out[1] = (uint8_t)(hash >> 8);
    out[2] = (uint8_t)(hash & 0xFF);
    out[3] = (uint8_t)(total_len >> 8);
    out[4] = (uint8_t)(total_len & 0xFF);
    out[5] = (uint8_t)(ackseq >> 8);
    out[6] = (uint8_t)(ackseq & 0xFF);
    return kConfigAckBytes;
}

bool config_decode_ack(const uint8_t* value, size_t len, ConfigAck* out) {
    if (!value || len != kConfigAckBytes) return false;
    if ((value[0] & kConfigAckTagMask) != kConfigAckTag) return false;
    if (out) {
        out->status = (uint8_t)(value[0] & kConfigAckStatusMask);
        out->hash = (uint16_t)((value[1] << 8) | value[2]);
        out->total_len = (uint16_t)((value[3] << 8) | value[4]);
        out->ackseq = (uint16_t)((value[5] << 8) | value[6]);
    }
    return true;
}

const char* config_ack_status_name(uint8_t status) {
    switch ((ConfigAckStatus)status) {
        case ConfigAckStatus::Ok:           return "ok";
        case ConfigAckStatus::HashMismatch: return "hash-mismatch";
        case ConfigAckStatus::Incomplete:   return "incomplete";
        case ConfigAckStatus::NoMemory:     return "no-memory";
    }
    /* NOT "unknown-and-therefore-fine": an unallocated status is reported as
     * unallocated and is never `ok`. */
    return "unallocated";
}

bool config_ttl_decrement(uint8_t* value, size_t len) {
    if (!value || len < kConfigSeqBytes) return true;   /* not ours to judge */
    if (!(value[0] == 0 && value[1] == 0)) return true; /* data: verbatim    */
    if (len - kConfigSeqBytes != kConfigHeaderContentBytes) return true;
    uint8_t& ttl = value[kConfigSeqBytes];
    if (ttl <= 1) return false;                         /* EXPIRED — announce */
    ttl = (uint8_t)(ttl - 1);
    return true;
}

/* ------------------------------------------------------------------ */

ConfigReassembler::~ConfigReassembler() { reset(); }

void ConfigReassembler::reset() {
    free(buf_);
    free(have_);
    buf_ = nullptr;
    have_ = nullptr;
    total_ = 0;
    count_ = 0;
    received_ = 0;
    chunk_payload_ = 0;
    ttl_ = 0;
    expect_hash_ = 0;
    actual_hash_ = 0;
    complete_ = false;
}

char* ConfigReassembler::take(size_t* out_len) {
    if (!complete_ || !buf_) return nullptr;
    char* p = (char*)buf_;
    if (out_len) *out_len = total_;
    buf_ = nullptr;          /* ownership moves; reset() must not free it */
    reset();
    return p;
}

ConfigReassembler::Result ConfigReassembler::finish() {
    actual_hash_ = config_content_hash(buf_, total_);
    if (actual_hash_ != expect_hash_) {
        /* DISCARD.  A leaf that reassembles to the wrong hash must never
         * build a scene from a plausible-looking ruin
         * ([CXC-CONFIG-BUS-CHUNKS] content-hash-rationale).
         *
         * The two hashes SURVIVE the reset on purpose: the caller's next act
         * is to report them and ACK with them, and a diagnostic that reads
         * "mismatch 0000 want 0000" would make a real failure unattributable. */
        uint16_t got = actual_hash_, want = expect_hash_;
        reset();
        actual_hash_ = got;
        expect_hash_ = want;
        return Result::HashMismatch;
    }
    buf_[total_] = 0;        /* the scene builder wants a C string */
    complete_ = true;
    return Result::Complete;
}

ConfigReassembler::Result ConfigReassembler::on_frame(const uint8_t* value,
                                                      size_t len) {
    ConfigChunkHeader hdr{};
    uint16_t seq = 0;
    const uint8_t* content = nullptr;
    size_t content_len = 0;
    ConfigFrameKind kind = config_frame_parse(value, len, &hdr, &seq,
                                              &content, &content_len);
    if (kind == ConfigFrameKind::Malformed) return Result::Ignored;

    if (kind == ConfigFrameKind::Header) {
        bool was_active = active();
        reset();
        ttl_ = hdr.ttl;
        chunk_payload_ = hdr.chunk_payload;
        total_ = hdr.total_len;
        expect_hash_ = hdr.content_hash;
        count_ = hdr.chunk_count;

        buf_ = (uint8_t*)calloc(total_ + 1, 1);
        size_t bitmap = (size_t)((count_ + 7) / 8);
        have_ = bitmap ? (uint8_t*)calloc(bitmap, 1) : nullptr;
        if (!buf_ || (bitmap && !have_)) {
            /* LOUD, never a partial transfer that looks alive. */
            reset();
            return Result::NoMemory;
        }
        if (count_ == 0) return finish();       /* zero-length config */
        return was_active ? Result::Restarted : Result::Started;
    }

    if (count_ == 0 || seq > count_) return Result::Stray;
    uint16_t idx = (uint16_t)(seq - 1);
    if (have_[idx >> 3] & (uint8_t)(1u << (idx & 7))) return Result::Duplicate;

    size_t off = (size_t)idx * (size_t)chunk_payload_;
    if (off > total_) return Result::Stray;
    size_t n = content_len;
    if (off + n > total_) n = total_ - off;      /* the ragged last chunk */
    if (n) memcpy(buf_ + off, content, n);
    have_[idx >> 3] = (uint8_t)(have_[idx >> 3] | (1u << (idx & 7)));
    received_++;

    if (received_ < count_) return Result::Progress;
    return finish();
}

uint16_t ConfigReassembler::first_missing() const {
    if (!have_ || count_ == 0) return 0;
    for (uint16_t i = 0; i < count_; i++)
        if (!(have_[i >> 3] & (uint8_t)(1u << (i & 7))))
            return (uint16_t)(i + 1);
    return 0;
}

uint16_t ConfigReassembler::ack_seq() const {
    if (complete_) return count_;
    return first_missing();
}

const char* ConfigReassembler::result_name(Result r) {
    switch (r) {
        case Result::Ignored:      return "ignored";
        case Result::Started:      return "started";
        case Result::Restarted:    return "restarted";
        case Result::Progress:     return "progress";
        case Result::Duplicate:    return "duplicate";
        case Result::Stray:        return "stray";
        case Result::Complete:     return "complete";
        case Result::HashMismatch: return "mismatch";
        case Result::NoMemory:     return "no-memory";
    }
    return "?";
}
