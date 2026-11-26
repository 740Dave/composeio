/*
 * wifi.h — WiFi station mode + mDNS for CYD
 *
 * Connects to the AP defined in wifi_config.h,
 * registers mDNS as "cyd.local".
 */

#ifndef WIFI_H
#define WIFI_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize WiFi in station mode and connect.
 * Blocks until connected or gives up after ~10 s.
 * Also starts mDNS so the device is reachable as cyd.local.
 */
void wifi_init(void);

/**
 * Returns true if WiFi station is connected and has an IP.
 */
bool wifi_is_connected(void);

/**
 * Returns the current IP address as a string (e.g. "198.51.100.18").
 * Returns "0.0.0.0" if not connected.
 */
const char* wifi_get_ip(void);

#ifdef __cplusplus
}
#endif

#endif /* WIFI_H */
