/*
 * fp_bus_messages.h — pure builders + router for the device's bus surface
 * (announce, enroll status/complete) and the inbound trailing-tail router.
 *
 * WHY PURE + HEADER-ONLY: the device firmware (fingerprint_bringup_main.cpp) is
 * NOT compiled in env:native_test, so its ArduinoJson publishes cannot be host-
 * tested.  These builders produce the exact wire JSON with only <string>, so the
 * SAME bytes the firmware puts on the wire are asserted on the host against the
 * service contract (services/fingerprint_storage/{topics,service,enroll}.py).
 *
 * The envelope mirrors MqttFpBusSink's exactly ({ns,type,src,ts,scope,seq,evt,
 * data}) so fingerprint_storage decodes these with no special case.
 *
 * ⛔ ADDRESSING ([CXC-FPBUS-ADDRESSING]): the device_id carries a slash
 * ("dev/s3-fp1"), so the inbound router NEVER splits on the first slash — it
 * strips the known "<prefix>/global/<device_id>/" and matches the remaining
 * TRAILING-TAIL vocabulary, exactly as service.py:_split_device_namespace does.
 */
#ifndef FP_BUS_MESSAGES_H
#define FP_BUS_MESSAGES_H

#include <string>

namespace fp_bus {

/* ---- inbound routing (service -> device) ------------------------------- */
enum class Inbound { UNKNOWN, TEMPLATE_SET, ENROLL, ENROLL_CANCEL };

/* Match against "<prefix>/global/<device_id>/<tail>" by stripping the scoped
 * prefix whole (device_id may contain '/'), then exact-matching the tail. */
inline Inbound route_inbound(const std::string& topic, const std::string& prefix,
                             const std::string& device_id) {
    const std::string scoped = prefix + "/global/" + device_id + "/";
    if (topic.rfind(scoped, 0) != 0) return Inbound::UNKNOWN;
    const std::string tail = topic.substr(scoped.size());
    if (tail == "template_set")      return Inbound::TEMPLATE_SET;
    if (tail == "cmd/enroll/cancel") return Inbound::ENROLL_CANCEL;
    if (tail == "cmd/enroll")        return Inbound::ENROLL;
    return Inbound::UNKNOWN;
}

/* ---- outbound retain policy (device -> service) ------------------------ */
enum class Outbound { ANNOUNCE, ENROLL_STATUS, ENROLL_COMPLETE };

/* Retained: the announce (discovery, [CXC-FPBUS-MSG-DEVICE-ANNOUNCE]) and the
 * enroll status (last-value-wins progress).  NOT the enroll-complete result
 * (an event, consumed once). */
inline bool retained(Outbound kind) {
    return kind == Outbound::ANNOUNCE || kind == Outbound::ENROLL_STATUS;
}

/* ---- JSON helpers (minimal, dependency-free) --------------------------- */
inline std::string json_escape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 2);
    for (char c : s) {
        switch (c) {
            case '"':  o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n";  break;
            case '\r': o += "\\r";  break;
            case '\t': o += "\\t";  break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    static const char* hex = "0123456789abcdef";
                    o += "\\u00";
                    o += hex[(c >> 4) & 0xF];
                    o += hex[c & 0xF];
                } else {
                    o += c;
                }
        }
    }
    return o;
}

inline std::string jstr(const std::string& s) { return "\"" + json_escape(s) + "\""; }

/* The shared envelope.  ts/seq are passed in so the builders stay pure and the
 * host tests are deterministic; the firmware passes millis()/1000.0 and a seq. */
inline std::string envelope(const std::string& ns, const std::string& evt,
                            const std::string& src, double ts, unsigned long seq,
                            const std::string& data_json) {
    std::string o = "{";
    o += "\"ns\":" + jstr(ns) + ",";
    o += "\"type\":\"event\",";
    o += "\"src\":" + jstr(src) + ",";
    o += "\"ts\":" + std::to_string(ts) + ",";
    o += "\"scope\":\"global\",";
    o += "\"seq\":" + std::to_string(seq) + ",";
    o += "\"evt\":" + jstr(evt) + ",";
    o += "\"data\":" + data_json;
    o += "}";
    return o;
}

/* ---- the three device->service messages -------------------------------- */

/* announce ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]).  The three capability fields sit
 * inside a nested "capabilities" object — service.py reads data["capabilities"]
 * and roster_push.py raises if slot_count / template_size_bytes is missing. */
inline std::string announce(const std::string& device_id,
                            const std::string& gated_capability,
                            int slot_count, int template_size_bytes,
                            bool supports_readback,
                            const std::string& feedback_profile,
                            double ts, unsigned long seq) {
    std::string data = "{";
    data += "\"device_id\":" + jstr(device_id) + ",";
    data += "\"gated_capability\":" + jstr(gated_capability) + ",";
    data += "\"capabilities\":{";
    data += "\"slot_count\":" + std::to_string(slot_count) + ",";
    data += "\"template_size_bytes\":" + std::to_string(template_size_bytes) + ",";
    data += "\"supports_readback\":" + std::string(supports_readback ? "true" : "false") + ",";
    data += "\"feedback_profile\":" + jstr(feedback_profile);
    data += "}}";
    return envelope(device_id + "/announce", "announce", device_id, ts, seq, data);
}

/* result/enroll/complete ([DD-FPBUS-ENROLL-READBACK-SHAPE]).  ⛔ template_ref (a
 * fingerprint:// reference), NEVER raw bytes on the bus
 * ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]); enroll.py resolves the
 * ref back to bytes. */
inline std::string enroll_complete(const std::string& device_id,
                                   const std::string& person, int ordinal,
                                   const std::string& template_ref,
                                   const std::string& request_id,
                                   double ts, unsigned long seq) {
    std::string data = "{";
    data += "\"person\":" + jstr(person) + ",";
    data += "\"ordinal\":" + std::to_string(ordinal) + ",";
    data += "\"template_ref\":" + jstr(template_ref) + ",";
    data += "\"request_id\":" + jstr(request_id);
    data += "}";
    return envelope(device_id + "/result/enroll/complete", "enroll_complete",
                    device_id, ts, seq, data);
}

/* status/enroll — retained, last-value-wins progress. capture_of ==
 * captures_per_template. */
inline std::string enroll_status(const std::string& device_id,
                                 const std::string& phase, int ordinal,
                                 int capture_n, int capture_of,
                                 double ts, unsigned long seq) {
    std::string data = "{";
    data += "\"phase\":" + jstr(phase) + ",";
    data += "\"ordinal\":" + std::to_string(ordinal) + ",";
    data += "\"capture_n\":" + std::to_string(capture_n) + ",";
    data += "\"capture_of\":" + std::to_string(capture_of);
    data += "}";
    return envelope(device_id + "/status/enroll", "enroll_status", device_id,
                    ts, seq, data);
}

}  /* namespace fp_bus */

#endif /* FP_BUS_MESSAGES_H */
