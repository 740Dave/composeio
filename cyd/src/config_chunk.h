/*
 * config_chunk.h — the [SPEC:CXC-CONFIG-BUS] chunk format, firmware side.
 * [CXC-CONFIG-BUS-CHUNKS] [CXC-CONFIG-BUS-BUDGET] [CXC-CONFIG-BUS-ADDRESSING]
 * [CXC-CONFIG-BUS-TTL] [CXC-CONFIG-BUS-RELAY]
 *
 *     frame value := [seq_hi][seq_lo][content ...]
 *
 *     seq == 0  -> HEADER, content EXACTLY 6 bytes:
 *                    [0]    ttl            seed 8, decremented by each relay
 *                    [1]    chunk_payload  content bytes per DATA frame
 *                    [2..3] total_len      uint16 BIG-endian
 *                    [4..5] content_hash   CRC-16/CCITT-FALSE, BIG-endian
 *     seq >= 1  -> DATA; belongs at payload offset (seq-1)*chunk_payload
 *
 * SIZED FOR CLASSIC CAN, NOT UART.  Every CAN controller in the fleet is
 * classic CAN 2.0B — EIGHT data bytes, and the limit is the CONTROLLER, so
 * no purchase changes it ([CXC-CONFIG-BUS-BUDGET]).  Two bytes of sequence
 * plus a six-byte header is exactly one CAN frame with nothing to spare;
 * there is no destination field and no transfer id because THE NAMESPACE IS
 * THE ADDRESS ([CXC-CONFIG-BUS-ADDRESSING]) — `_device/<id>/config`, whose
 * 16-bit hash is already in every frame's header (the UART payload, the CAN
 * arbitration id).  A format sized against UART's 252 bytes would be quietly
 * unusable on the substrate that actually needs it.
 *
 * PORTED, NOT DERIVED.  The reference implementation is
 * python/composeio/config_chunk.py, and env:native_config_chunk compares this
 * code against frames baked FROM it (cyd/test/config_chunk_corpus_generated.h).
 * A C++ round trip would pass with the sequence bytes swapped; the parity test
 * is what makes "these are the bytes a live head puts on the wire" checkable.
 *
 * Pure C++ (no ESP-IDF, no Arduino) so it host-compiles, same idiom as
 * bus_frame.h / dsl.h / restart.h.  It allocates with malloc/free ONLY inside
 * ConfigReassembler, which is the one thing here that must hold state.
 *
 * WHY A RELAY NEVER TOUCHES ConfigReassembler ([CXC-CONFIG-BUS-RELAY]): a
 * CH32V203 has ~20 KB of RAM and cannot buffer a 6 KB payload to re-emit it.
 * Only the ENDPOINT reassembles.  A relay reads the TTL, decrements it, and
 * forwards — config_ttl_decrement() below is the whole of its config logic.
 */

#ifndef CONFIG_CHUNK_H
#define CONFIG_CHUNK_H

#include <cstddef>
#include <cstdint>

/* Per-frame overhead. TWO bytes — see the header comment. */
static constexpr size_t kConfigSeqBytes = 2;
/* The header frame's content length. SIX, so header+seq == one CAN frame. */
static constexpr size_t kConfigHeaderContentBytes = 6;
static constexpr uint16_t kConfigHeaderSeq = 0;
/* TTL seed. A PROTOCOL CONSTANT ([CXC-CONFIG-BUS-TTL]) — never a deployment
 * setting, never in a scene, never in a descriptor. */
static constexpr uint8_t kConfigTtlSeed = 8;
/* Content bytes per frame, per substrate. The CAN number is the one the
 * format is designed against; the UART number is what falls out. */
static constexpr size_t kConfigCanContentBytes = 8 - kConfigSeqBytes;
static constexpr size_t kConfigUartContentBytes = 252 - kConfigSeqBytes;
/* total_len is a uint16 in the header — a HARD protocol ceiling, enforced in
 * config_encode_header() rather than merely written down. */
static constexpr size_t kConfigMaxPayload = 0xFFFF;

/* CRC-16/CCITT-FALSE over arbitrary bytes.  The SAME algorithm as
 * bus_ns_hash() / composeio.config.namespace_hash, which only take a C string;
 * deliberately not a new hash, so one polynomial covers namespace hashing and
 * config content and there is one implementation to get wrong. */
uint16_t config_content_hash(const uint8_t* data, size_t len);

struct ConfigChunkHeader {
    uint8_t ttl;
    uint8_t chunk_payload;
    uint16_t total_len;
    uint16_t content_hash;
    /* DERIVED, never carried — see config_chunk.py's docstring for why the
     * six-byte header carries chunk_payload instead: chunk_count follows from
     * (total_len, chunk_payload) exactly, and the converse is false. */
    uint16_t chunk_count;
};

enum class ConfigFrameKind : uint8_t {
    Header,
    Data,
    Malformed,      /* shorter than a sequence, or a bad header length */
};

/* Classify a frame value.  For Header, *hdr is filled (chunk_count derived).
 * For Data, *seq / *content / *content_len are filled.  Never allocates. */
ConfigFrameKind config_frame_parse(const uint8_t* value, size_t len,
                                   ConfigChunkHeader* hdr, uint16_t* seq,
                                   const uint8_t** content,
                                   size_t* content_len);

/* Build the seq-0 header frame value.  0 when it will not fit or the payload
 * exceeds the uint16 ceiling — REFUSED, never truncated. */
size_t config_encode_header(uint16_t total_len, uint8_t chunk_payload,
                            uint16_t content_hash, uint8_t ttl,
                            uint8_t* out, size_t cap);

/* Build one DATA frame value: [seq(2)][content...].  seq is 1-based. */
size_t config_encode_data(uint16_t seq, const uint8_t* content, size_t n,
                          uint8_t* out, size_t cap);

/* chunk count for a (total, payload) pair — the field [CXC-CONFIG-BUS-CHUNKS]
 * names, computed rather than trusted. */
uint16_t config_chunk_count(uint32_t total_len, uint8_t chunk_payload);

/* ------------------------------------------------------------------ */
/* The ACK  [CXC-CONFIG-BUS-CHUNKS]                                    */
/* ------------------------------------------------------------------ */
/* BINARY, SEVEN BYTES, because the string form was PHYSICALLY UNSENDABLE on
 * the one substrate this protocol exists for.  `snprintf("ok %04x %u", ...)`
 * for the proven 5,856 B scene is "ok beef 5856" = 13 bytes; the mismatch form
 * "mismatch beef want cc50" is 24; and bus_can.cpp REFUSES anything over 8.
 * So a CAN config transfer could not be acknowledged at all: the leaf applied
 * the scene, cleared its retry timer and carried on, the head never learned,
 * and because there is no ACK timeout nothing detected the silence.
 *
 *     [0]     0xA0 | status      tag(4) | status(4)
 *     [1..2]  hash               uint16 BE — the TRANSFER's announced hash
 *     [3..4]  total_len          uint16 BE
 *     [5..6]  ackseq             uint16 BE — last received, or first missing
 *
 * The tag nibble costs nothing (status needs four bits) and buys two things:
 * the first byte is always >= 0xA0, so the value can never be mis-decoded as a
 * length-prefixed string by [BP-VALUE-CODEC]; and a receiver can positively
 * identify a config ACK rather than parsing whatever seven bytes arrived.
 *
 * `hash` ALWAYS names the transfer the head announced, never the corrupt value
 * a mismatch computed — so the head's check ("is this about the transfer I
 * sent?") is one comparison for every status.  Ported from
 * python/composeio/config_chunk.py; env:native_config_chunk compares the bytes.
 */
static constexpr size_t kConfigAckBytes = 7;
static constexpr uint8_t kConfigAckTag = 0xA0;
static constexpr uint8_t kConfigAckTagMask = 0xF0;
static constexpr uint8_t kConfigAckStatusMask = 0x0F;

enum class ConfigAckStatus : uint8_t {
    Ok = 0,             /* reassembled, hash verified                     */
    HashMismatch = 1,   /* every frame in, whole-payload hash wrong       */
    Incomplete = 2,     /* abandoned before it completed                  */
    NoMemory = 3,       /* could not be buffered — the heap, not the wire */
};

struct ConfigAck {
    uint8_t status;
    uint16_t hash;
    uint16_t total_len;
    uint16_t ackseq;
};

/* Build the 7-byte ACK value.  0 when it will not fit — never a short frame. */
size_t config_encode_ack(ConfigAckStatus status, uint16_t hash,
                         uint16_t total_len, uint16_t ackseq,
                         uint8_t* out, size_t cap);

/* Decode one.  false for anything that is not an ACK (wrong length, wrong tag)
 * — REFUSED rather than parsed, so seven bytes of something else can never be
 * reported as a successful transfer. */
bool config_decode_ack(const uint8_t* value, size_t len, ConfigAck* out);

const char* config_ack_status_name(uint8_t status);

/* RELAY HOP ([CXC-CONFIG-BUS-TTL]).  Decrements the TTL of a header frame IN
 * PLACE.  Returns false when the TTL has EXPIRED — and a caller that swallows
 * that is the silent depth limit this design was corrected to remove, so every
 * caller MUST announce.  A non-header frame is left untouched and returns
 * true: only the header carries the TTL, and a relay forwards data frames
 * verbatim without parsing them. */
bool config_ttl_decrement(uint8_t* value, size_t len);

/* ------------------------------------------------------------------ */
/* Endpoint reassembly                                                 */
/* ------------------------------------------------------------------ */

/* ONE transfer at a time: a header arriving mid-transfer ABANDONS the old one
 * ([CXC-CONFIG-BUS-CHUNKS] interleaving).  Cheaper than transfer ids and
 * sufficient, because a leaf has exactly one scene. */
class ConfigReassembler {
public:
    enum class Result : uint8_t {
        Ignored,        /* not a usable config frame                       */
        Started,        /* header accepted, buffer sized                   */
        Restarted,      /* header mid-transfer: the old one was abandoned  */
        Progress,       /* data chunk stored                               */
        Duplicate,      /* this sequence was already held                  */
        Stray,          /* data frame with no header, or seq out of range  */
        Complete,       /* every chunk in AND the content hash matches     */
        HashMismatch,   /* every chunk in and the hash does NOT match      */
        NoMemory,       /* could not size the buffer — loud, never silent  */
    };

    ~ConfigReassembler();

    Result on_frame(const uint8_t* value, size_t len);

    /* True once Complete: the payload is held and NUL-terminated. */
    bool complete() const { return complete_; }
    const char* payload() const { return (const char*)buf_; }
    size_t payload_len() const { return total_; }
    uint16_t expected_hash() const { return expect_hash_; }
    uint16_t actual_hash() const { return actual_hash_; }
    uint16_t chunk_count() const { return count_; }
    uint16_t received() const { return received_; }
    uint8_t chunk_payload() const { return chunk_payload_; }
    uint8_t ttl() const { return ttl_; }
    bool active() const { return count_ > 0 && !complete_; }

    /* The first sequence NOT received (1-based), or 0 when none is missing.
     * The field a go-back-N repair would resume from — nothing reads it yet,
     * and it is on the wire so that something CAN.  Python's counterpart is
     * Reassembler.first_missing(), which is the first caller its long-unused
     * missing() iterator has ever had. */
    uint16_t first_missing() const;

    /* The `ackseq` the CURRENT state should report: on a complete transfer the
     * LAST correctly received sequence (== chunk_count), otherwise the FIRST
     * MISSING one.  0 on a failure means nothing was missing — which is real
     * information: it separates CORRUPTION (every frame arrived, the bytes are
     * wrong) from LOSS, and a repair that cannot tell them apart retransmits
     * the wrong thing. */
    uint16_t ack_seq() const;

    /* Hand the payload to the caller, who then owns the malloc'd buffer.
     * The reassembler is reset — a scene is delivered ONCE. */
    char* take(size_t* out_len);

    void reset();

    static const char* result_name(Result r);

private:
    uint8_t* buf_ = nullptr;      /* total_+1 bytes, NUL-terminated */
    uint8_t* have_ = nullptr;     /* received bitmap, (count_+7)/8  */
    size_t total_ = 0;
    uint16_t count_ = 0;
    uint16_t received_ = 0;
    uint8_t chunk_payload_ = 0;
    uint8_t ttl_ = 0;
    uint16_t expect_hash_ = 0;
    uint16_t actual_hash_ = 0;
    bool complete_ = false;

    Result finish();
};

#endif /* CONFIG_CHUNK_H */
