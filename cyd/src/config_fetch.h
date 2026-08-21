/*
 * config_fetch.h — Config + asset fetch over HTTP ([CXC-R-BOOT] steps 5/7)
 *
 * The device is generic; its behavior is entirely determined by the
 * .compose.yaml it fetches from bus_storage at boot ([CXC-R-BOOT-FUNGIBLE]).
 *
 * Config source of truth (M5 as-built; see the [CXC-R-BOOT] change
 * record): bus_storage assets namespace —
 *
 *     bus://assets/displays/<device_id>.compose.yaml
 *       -> GET {BUS_STORAGE_URL}/api/v1/assets/displays/<device_id>.compose.yaml
 *     fallback when 404:
 *     bus://assets/displays/default.compose.yaml
 *
 * device_id = DISPLAY_DEVICE_ID from wifi_config.h if defined, else
 * "cyd-" + the last 3 MAC octets (stable per unit, printed at boot so
 * a config can be assigned without touching firmware).
 */

#ifndef CONFIG_FETCH_H
#define CONFIG_FETCH_H

#include <cstddef>

#ifdef __cplusplus
extern "C" {
#endif

/** Stable device identity (also announced in logs). */
const char* device_id(void);

/**
 * Fetch this device's .compose.yaml.  Tries the per-device asset, then
 * the default.  Returns a malloc'd NUL-terminated buffer (caller frees)
 * or nullptr.  out_len receives the payload length.
 */
char* config_fetch(size_t* out_len);

/**
 * The HTTP status code of the most recent http_fetch/config_fetch: >0 is the
 * server's response code, 0 means the connection never reached one (open/DNS
 * failure).  Lets a failed trial store plane name `store-http-<code>` vs
 * `store-unreachable` ([CXC-REPOINT-REVERT]).
 */
int config_fetch_last_status(void);

/**
 * Fetch an arbitrary HTTP URL (or bus://assets/... URI) into a malloc'd
 * buffer.  Used for image assets.  Returns nullptr on failure.
 * max_len caps the download (assets beyond it are refused).
 */
char* http_fetch(const char* uri, size_t* out_len, size_t max_len);

#ifdef __cplusplus
}
#endif

#endif /* CONFIG_FETCH_H */
