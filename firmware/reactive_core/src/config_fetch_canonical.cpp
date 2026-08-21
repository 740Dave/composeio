/*
 * config_fetch_canonical.cpp — compile the CANONICAL boot config fetch by
 * reference.  [CXC-R-BOOT] steps 5/7 live in cyd/src/config_fetch.{h,cpp}
 * (device_id() + config_fetch() + http_fetch() over the bus_storage asset
 * store).  This TU pulls it in through -I${PROJECT_DIR}/../../cyd/src so the
 * headless controller tier fetches its .compose.yaml the SAME way the
 * display tier does — reuse-by-reference, never a fork.
 *
 * Guarded to the controller tier: config_fetch.cpp depends on ESP-IDF
 * (esp_http_client / esp_mac) and on BUS_STORAGE_URL from wifi_config.h /
 * build_flags, so it must NOT compile into the pure-demo/actuator envs (or
 * the host test).  Only [env:esp32c3] (the dual-throttle controller) defines
 * CONTROLLER_CONFIG_FETCH.  device_id() resolves DISPLAY_DEVICE_ID (set to
 * "dual-throttle" for this unit) or falls back to a MAC-derived id.
 */
#ifdef CONTROLLER_CONFIG_FETCH
#include "config_fetch.cpp"
#endif
