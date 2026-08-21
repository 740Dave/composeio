/*
 * restart.h — Remote config-reload (bus restart command)
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-REMOTE-RESTART] [CXC-ER-RESTART-BUS]
 *
 * The shared firmware app-shell handler that makes every ESP32-class
 * device honor a remote-restart command over the bus.  On a truthy
 * command to `_device/<id>/restart` (per-device) OR `_device/restart`
 * (broadcast) the device publishes a pre-restart ack and hard-restarts,
 * which re-runs the EXISTING boot-fetch path ([CXC-R-BOOT-SEQUENCE]:
 * WiFi -> GET bus://assets/displays/<id>.compose.yaml -> parse -> render)
 * and thus loads the config PUT to bus_storage after its last boot.  This
 * is CONFIG-reload, NOT firmware-OTA — the same firmware runs after boot.
 *
 * This module is PURE C++ (no ESP-IDF includes) so it host-compiles for
 * the native_test suite, same idiom as dsl.h / yaml_min.h.  The two
 * platform-specific actions — publishing the ack and performing the
 * hard reset — are injected as SEAMS (function pointers), so a host test
 * can assert "reboot invoked on a truthy command, NOT on a falsy one"
 * without actually resetting.  It lives in the shared app shell (the bus
 * client), NOT a board profile, so cyd / qualia_display / esp32c3 and
 * every future variant inherit remote-restart unmodified ([CXC-ER-C19]).
 */

#ifndef RESTART_H
#define RESTART_H

#include <cstddef>
#include <cstdint>

/* Seam: publish a state (ns, string value) on the bus — bound to the
 * bus client's publish in firmware, to a recorder in the host test. */
typedef void (*RestartPublishFn)(const char* ns, const char* value);

/* Seam: perform the hard reset — bound to esp_restart() (via a small
 * flush-then-reset wrapper) in firmware, to a recorder in the host test.
 * On real hardware this does NOT return. */
typedef void (*RestartRebootFn)(void);

/* Install the handler.  Computes the two control namespaces from
 * device_id (`_device/<id>/restart` + the broadcast `_device/restart`),
 * stores the seams, and arms the boot-loop guard: any restart command
 * whose receive time (ms since boot) is < boot_window_ms is IGNORED, so
 * a re-delivered / retained / doubled command cannot loop the device
 * ([CXC-ER-RESTART-BUS] boot-loop guard).  boot_window_ms ~= 3000
 * approximates "before the first successful config-fetch completes". */
void restart_init(const char* device_id,
                  RestartPublishFn publish,
                  RestartRebootFn reboot,
                  uint32_t boot_window_ms);

/* Clear all handler state (device id, seams, restarting latch).  For
 * host-test isolation between cases; a no-op cost in firmware. */
void restart_reset(void);

/* Fill out[] with the control namespaces this device subscribes (the
 * per-device restart then the broadcast restart) and return the count
 * (2 when initialized, else 0).  The caller registers each as an EXACT
 * bus subscription ([CXC-ER-RESTART-BUS]: two exact namespaces, not a
 * broad `_device`-wildcard glob). */
size_t restart_namespaces(const char** out, size_t cap);

/* True iff ns is one of this device's two restart control namespaces. */
bool restart_is_restart_ns(const char* ns);

/* Evaluate a decoded bus value for restart-truthiness using the
 * `dsl_truthy` canon ([CXC-ER-RESTART-BUS] payload contract):
 *   - a bare truthy scalar (true / 1 / "restart") -> true;
 *   - the canonical object {"restart": true} -> true (the "restart"
 *     field's truthiness; an object with no truthy restart field, incl.
 *     the empty {}, -> false);
 *   - a falsy / empty scalar (false / 0 / "" ) -> false.
 * kind mirrors BusEvent::Kind (0=Number, 1=Bool, 2=String). */
bool restart_payload_truthy(int kind, double num, bool b, const char* str);

/* Handle a decoded bus arrival at receive time (now_ms = ms since boot).
 * Returns true iff ns was a restart control namespace — i.e. the arrival
 * was a control-plane command CONSUMED here and must NOT be routed to the
 * scene.  When the payload is truthy AND now_ms is past the boot-guard
 * window AND we are not already restarting, this publishes the ack
 * (`_device/<id>/status` = "restarting") then invokes the reboot seam.
 * A falsy/empty payload, or a command inside the boot-guard window, is a
 * NO-OP (still returns true — it was a restart-namespace arrival). */
bool restart_on_bus_value(const char* ns, int kind, double num, bool b,
                          const char* str, uint32_t now_ms);

#endif /* RESTART_H */
