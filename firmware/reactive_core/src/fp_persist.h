/*
 * fp_persist.h — pure, dependency-free (de)serialization of DevicePersistence
 * for NVS ([CXC-FPID-DEVICE-BRIDGE]: the bridge must survive reboot).
 *
 * The R503's own flash keeps the templates + roster stamp across a reboot, so a
 * device reports fingerprint_ready — but the bridge (slot -> person/ordinal/
 * character/role) and the role_capability_table live in RAM (DevicePersistence)
 * and are LOST on reboot, so every match against a previously-enrolled slot
 * resolves UNRECOGNIZED (a crew member misread as an intruder while the device
 * claims healthy).  This serializes DevicePersistence to a compact blob the
 * firmware stores in ESP32 NVS and reloads at boot.
 *
 * PURE C++: no Arduino, no ArduinoJson — so it compiles in env:native_test and
 * a round-trip is host-tested.  The wire format uses control-char separators
 * (US 0x1f between fields, '\n' between records) that never appear in the
 * identifier strings this system carries.
 */
#ifndef FP_PERSIST_H
#define FP_PERSIST_H

#include <cstdlib>
#include <map>
#include <string>
#include <vector>

#include "fingerprint_device.h"   /* DevicePersistence, BridgeEntry, Slot */

namespace fp_persist {

inline std::string to_blob(const DevicePersistence& p) {
    const char FS = '\x1f';
    std::string out = "FPDEV1";
    out += FS;
    out += std::to_string(p.count_authentication_attempts);
    out += '\n';
    for (const auto& kv : p.role_capability_table) {
        out += 'R';
        out += FS;
        out += kv.first;
        for (const auto& cap : kv.second) { out += FS; out += cap; }
        out += '\n';
    }
    for (const auto& kv : p.bridge) {
        const BridgeEntry& e = kv.second;
        out += 'E';
        out += FS; out += std::to_string(kv.first);
        out += FS; out += e.person;
        out += FS; out += std::to_string(e.ordinal);
        out += FS; out += e.character;
        out += FS; out += e.role;
        out += '\n';
    }
    return out;
}

/* Returns true iff the blob parsed as a valid FPDEV1 record (so a garbage or
 * empty NVS value leaves `out` reset and reports failure rather than half-
 * loading). `out` is always reset first. */
inline bool from_blob(const std::string& s, DevicePersistence& out) {
    out = DevicePersistence();
    const char FS = '\x1f';
    bool header = false;
    size_t pos = 0;
    while (pos < s.size()) {
        size_t nl = s.find('\n', pos);
        if (nl == std::string::npos) nl = s.size();
        const std::string line = s.substr(pos, nl - pos);
        pos = nl + 1;
        if (line.empty()) continue;
        std::vector<std::string> f;
        size_t fp = 0;
        for (;;) {
            const size_t next = line.find(FS, fp);
            if (next == std::string::npos) { f.push_back(line.substr(fp)); break; }
            f.push_back(line.substr(fp, next - fp));
            fp = next + 1;
        }
        if (!header) {
            if (f.size() < 2 || f[0] != "FPDEV1") return false;
            out.count_authentication_attempts = std::atoi(f[1].c_str());
            header = true;
        } else if (f[0] == "R" && f.size() >= 2) {
            std::vector<std::string> caps(f.begin() + 2, f.end());
            out.role_capability_table[f[1]] = caps;
        } else if (f[0] == "E" && f.size() >= 6) {
            BridgeEntry e;
            const Slot slot = std::atoi(f[1].c_str());
            e.person = f[2];
            e.ordinal = std::atoi(f[3].c_str());
            e.character = f[4];
            e.role = f[5];
            out.bridge[slot] = e;
        }
    }
    return header;
}

}  /* namespace fp_persist */

#endif /* FP_PERSIST_H */
