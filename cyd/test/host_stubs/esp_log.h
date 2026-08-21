/*
 * esp_log.h — HOST STUB, test builds only.
 * [SPEC:CXC-CONFIG-BUS]
 *
 * config_relay.cpp is the one module whose LOGGING is part of its contract:
 * [CXC-CONFIG-BUS-TTL] requires that a relay which decrements TTL to zero
 * ANNOUNCES, because a silent discard reintroduces exactly the undiagnosable
 * depth limit the design was corrected to remove.  A host harness therefore
 * has to be able to compile and OBSERVE those calls.
 *
 * Only [env:native_relay] puts this directory on the include path; the ESP-IDF
 * builds see the real header.  Deliberately minimal — this exists so one
 * module can be tested off hardware, not to become a portability layer.
 */
#ifndef CXC_HOST_STUB_ESP_LOG_H
#define CXC_HOST_STUB_ESP_LOG_H

#include <cstdio>

/* The harness counts these so "did it say so?" is a checkable assertion. */
extern int g_host_log_error_count;
extern int g_host_log_warn_count;

#define ESP_LOGE(tag, fmt, ...) \
    do { g_host_log_error_count++; \
         printf("E (%s) " fmt "\n", tag, ##__VA_ARGS__); } while (0)
#define ESP_LOGW(tag, fmt, ...) \
    do { g_host_log_warn_count++; \
         printf("W (%s) " fmt "\n", tag, ##__VA_ARGS__); } while (0)
#define ESP_LOGI(tag, fmt, ...) do { (void)(tag); } while (0)
#define ESP_LOGD(tag, fmt, ...) do { (void)(tag); } while (0)

#endif /* CXC_HOST_STUB_ESP_LOG_H */
