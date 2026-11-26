/*
 * bus_mailbox.h — a per-namespace LATEST-VALUE COALESCING MAILBOX for the
 * display tier's inbound bus path.  [SPEC:CXC-EMBED-REACTIVE] DP-CIO-6.
 *
 * WHY THIS EXISTS (measured 2026-08-19).  bus_mqtt.cpp and bus_uart.cpp both
 * buffered inbound subscribed messages in a 16-deep FreeRTOS FIFO.  Only
 * `State` and `Heartbeat` messages ever reach that buffer (both transports
 * `return` before enqueue for every other type), so its contents are 100%
 * LAST-VALUE-WINS state — and yet a FIFO drops the 17th arrival in a frame
 * SILENTLY.  The sharp case is the RETAINED-MESSAGE FLOOD on (re)connect: a
 * device subscribed to > 16 namespaces receives them all in a burst before
 * the render loop's first drain, and everything past 16 is lost with only an
 * ESP_LOGW nobody reads in the field.
 *
 * THE STRUCTURE.  Because every buffered message is state, the right shape is
 * a fixed set of per-namespace slots, OVERWRITE-ON-ARRIVAL:
 *
 *   - post(ev): if a pending slot already holds this ns, OVERWRITE it (a
 *     newer throttle value replacing an older un-drained one is exactly the
 *     self-healing the render tier already relies on); else take a free slot;
 *     else — and only else — the structure is genuinely full: count a drop
 *     and return false.
 *   - poll(out): hand back one pending slot's value and free it.
 *
 * Occupancy is therefore bounded by the number of DISTINCT namespaces with a
 * pending update between two drains, which cannot exceed the device's
 * subscription width.  Size CAP to that width plus control-plane/heartbeat
 * headroom and the width-overflow that killed the FIFO cannot happen; the
 * `dropped()` path becomes a should-never-fire alarm rather than routine loss.
 *
 * ⛔ THIS CLASS IS PURE LOGIC AND HOLDS NO LOCK.  On target the FreeRTOS queue
 * WAS the synchronisation between the writer (esp-mqtt / uart rx task) and the
 * reader (render loop).  A map has none, so the TRANSPORT wraps post()/poll()
 * in a short critical section (a FreeRTOS mutex).  Keeping the lock OUT of
 * this header is deliberate: it is what lets the coalescing logic be exercised
 * directly on the host (bus_mailbox_selftest.cpp wraps it in a std::mutex and
 * hammers it from two threads to prove the same discipline).  The critical
 * section must stay TINY — a post()/poll() is a bounded scan of CAP slots plus
 * one BusEvent copy, and NOTHING (no parse, no publish, no log) runs inside it.
 *
 * Pure C++ (no ESP-IDF) so it host-compiles, same idiom as bus_wire_codes.h.
 */

#ifndef BUS_MAILBOX_H
#define BUS_MAILBOX_H

#include <cstddef>
#include <cstdint>
#include <cstring>

#include "bus_transport.h"   /* BusEvent */

template <int CAP>
class BusMailbox {
public:
    /* Store an arrival, coalescing by namespace.  Returns true if it was
     * stored (either into this ns's existing pending slot or a fresh one);
     * false ONLY when every slot is held by a DIFFERENT pending namespace —
     * a genuine structure-full drop, which is counted. */
    bool post(const BusEvent& ev) {
        /* 1. Coalesce: a pending slot already holds this namespace.  The
         *    newer value wins — dropping the stale intermediate is correct
         *    for a state bus. */
        for (int i = 0; i < CAP; i++) {
            if (pending_[i] && strcmp(slot_[i].ns, ev.ns) == 0) {
                slot_[i] = ev;
                return true;
            }
        }
        /* 2. New namespace: take the first free slot. */
        for (int i = 0; i < CAP; i++) {
            if (!pending_[i]) {
                slot_[i] = ev;
                pending_[i] = true;
                pending_count_++;
                return true;
            }
        }
        /* 3. Full of OTHER pending namespaces — a real overflow. */
        dropped_++;
        return false;
    }

    /* Drain one pending slot into *out and free it; false when none pending.
     * Latest-value-wins means order among distinct namespaces is immaterial,
     * so a simple forward scan is sufficient (the render loop drains the whole
     * mailbox each frame). */
    bool poll(BusEvent* out) {
        for (int i = 0; i < CAP; i++) {
            if (pending_[i]) {
                *out = slot_[i];
                pending_[i] = false;
                pending_count_--;
                return true;
            }
        }
        return false;
    }

    /* Slots awaiting a drain.  Cheap (a maintained counter, no scan) because
     * the transport reads it per pass to drive the latency stamp. */
    size_t pending() const { return (size_t)pending_count_; }

    /* Cumulative genuine (structure-full) drops since boot.  The transport
     * publishes this, rate-limited, on `_device/<id>/bus_dropped`. */
    uint32_t dropped() const { return dropped_; }

    static constexpr int capacity() { return CAP; }

private:
    BusEvent slot_[CAP] = {};
    bool pending_[CAP] = {};
    int pending_count_ = 0;
    uint32_t dropped_ = 0;
};

#endif /* BUS_MAILBOX_H */
