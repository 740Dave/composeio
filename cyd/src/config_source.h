/*
 * config_source.h — WHERE does this board's scene come from?
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-STRATEGY] DP-CIO-6.
 *
 * Three strategies, selected exactly the way the TRANSPORT is selected
 * (bus_transport_select.cpp), because they are the same shape of decision and
 * one fewer thing to learn:
 *
 *   bus     request it over the SELECTED transport ([CXC-CONFIG-BUS-CHUNKS]).
 *           Works everywhere, MANDATORY for a chain with no wireless hop, and
 *           the ONLY strategy that can receive a LIVE update — a board that
 *           killed its radio can only be told something over the bus.
 *   http    WiFi up, GET from bus_storage.  Boot-only by construction; that is
 *           a real capability difference, not an implementation gap.
 *   cached  flash-provisioned, no fetch.  Resilient and DANGEROUS: a months-old
 *           scene renders perfectly healthily, so cache is paired with a
 *           VISIBLE staleness indication ([CXC-CONFIG-BUS-LEAF] staleness).
 *
 * THE SAME TWO FAILURE RULES AS transport_registry.py, for the same reason:
 *   1. An UNKNOWN strategy is FATAL and LOUD.  A mistyped `htpp` that quietly
 *      kept fetching over HTTP would be a device lying about its own
 *      configuration.  "Fatal" on an MCU is NOT abort() — see the .cpp; it is
 *      a refusal that fetches nothing and says why, forever, and is still
 *      recoverable over the bus.
 *   2. A KNOWN strategy whose backend is unreachable degrades LOUDLY and the
 *      app keeps running.  A down store is not a broken app.
 *
 * THE BOOTSTRAP TRAP ([CXC-CONFIG-BUS-STRATEGY]).  The strategy cannot live in
 * the scene YAML, because that YAML ARRIVES BY the strategy.  NVS is the local
 * source of truth, settable over the CURRENT link via
 * `_device/<id>/config_source` — the same precedent as
 * `_device/<id>/transport`.  Flip a board to `bus` over WiFi, then rewire and
 * power cycle: no reflash, no circularity.
 */

#ifndef CONFIG_SOURCE_H
#define CONFIG_SOURCE_H

#include <cstddef>
#include <cstdint>

enum class ConfigSource : uint8_t {
    Bus,
    Http,
    Cached,
    Unknown,       /* selected name is not one this build knows — REFUSE */
};

/* The strategy for this boot.  Resolved once, on first call:
 *   1. NVS key `config` in namespace `composeio`, else
 *   2. the compile-time default CXC_CONFIG_SOURCE_DEFAULT ("http").
 * The default stays `http` deliberately: adopting this seam must not, by
 * itself, change what any board already does. */
ConfigSource config_source(void);

const char* config_source_selected_name(void);
const char* config_source_selection_source(void);   /* "nvs" / "default" */
const char* config_source_known_names(void);        /* "bus http cached" */

/* Persist a selection for the NEXT boot.  Returns false and stores NOTHING for
 * an unknown name — validating on WRITE is what keeps the refusal path
 * practically unreachable, and it means a bus command carrying a typo is
 * rejected when it is issued rather than on the next boot. */
bool config_source_set_selection(const char* name);

/* DOES THIS STRATEGY NEED AN IP STACK?  The other half of the answer to
 * "should wifi_init() run" — the transport is the first half
 * (BusTransport::needs_network).  Both are asked, and the radio comes up only
 * if SOMETHING needs it.
 *
 * `http` -> true.  `bus` / `cached` -> false.  `Unknown` -> TRUE, deliberately:
 * a board that cannot fetch anything is a board someone has to reach in order
 * to fix, and keeping the radio up is the recovery path.  It is loud about it. */
bool config_source_needs_network(void);

/* ---- the control namespace that closes the bootstrap trap ---- */
/* `publish` is INJECTED, the restart.cpp shape: this module must not name a
 * transport, and a plain function pointer is what lets the app shell hand it
 * `bus_transport().publish_state` without this file including the seam. */
typedef void (*ConfigStatusPublishFn)(const char* ns, const char* value);
void config_source_control_init(const char* device_id,
                                ConfigStatusPublishFn publish);
size_t config_source_control_namespaces(const char** out, size_t cap);
/* True iff the arrival was a config-source command and was CONSUMED here.
 * kind mirrors BusEvent::Kind (0=Number, 1=Bool, 2=String). */
bool config_source_on_bus_value(const char* ns, int kind, double num, bool b,
                                const char* str);

/* ------------------------------------------------------------------ */
/* The cache ([CXC-CONFIG-BUS-LEAF] staleness)                         */
/* ------------------------------------------------------------------ */
/*
 * Stored in NVS (namespace "composeio"): `config.text` blob, `config.hash`
 * (the CRC-16 content hash) and `config.boots` (how many boots have started
 * since it was written — the only monotonic clock a board with no NTP and no
 * radio actually has).
 *
 * WRITTEN BY EVERY SUCCESSFUL FETCH, whatever the strategy, so `cached` is a
 * fallback that exists rather than one that has to be provisioned by hand.
 * Refused above kConfigCacheMax rather than truncated: half a scene stored is
 * a scene that will one day be loaded.
 */
static constexpr size_t kConfigCacheMax = 12 * 1024;

bool config_cache_store(const char* text, size_t len);
/* malloc'd, NUL-terminated, caller frees; nullptr when there is no cache. */
char* config_cache_load(size_t* out_len);
/* Boots elapsed since the cache was written — 0 means "written this boot".
 * Displayed, never trusted as a duration. */
uint32_t config_cache_age_boots(void);
/* Bumped once per boot by config_source(); exposed for the staleness badge. */
uint32_t config_boot_count(void);

/* True when the scene now on the glass came out of the cache and therefore
 * MUST carry a visible staleness indication.  A stale panel and a working
 * panel are otherwise identical ([CXC-BOARD-HW-SELF-CONFIRMING]). */
bool config_is_stale(void);
void config_set_stale(bool stale);

#endif /* CONFIG_SOURCE_H */
