/*
 * controller_config.cpp — lower a .compose.yaml into the device model.
 * PURE C++ (uses cyd/src/yaml_min by reference); see controller_config.h.
 */

#include "controller_config.h"

#include "yaml_min.h"

#include <cstdlib>
#include <cstring>

int controller_parse_gpio(const char* token) {
    if (!token) return -1;
    const char* g = strstr(token, "GPIO");
    if (g) {
        g += 4;
        if (*g >= '0' && *g <= '9') return atoi(g);
        return -1;
    }
    /* bare token: accept only when it is entirely digits ("5"), so a rail
     * like "3V3" is rejected rather than misread as pin 3. */
    if (!*token) return -1;
    for (const char* q = token; *q; q++)
        if (*q < '0' || *q > '9') return -1;
    return atoi(token);
}

/* state_engine name -> the engine's numeric full-scale output. */
static int out_max_for_engine(const char* engine) {
    if (engine && strcmp(engine, "continuous_0_100") == 0) return 100;
    /* continuous_0_255 and the unknown/default case: 0..255 */
    return 255;
}

static const char* engine_for_ns(const YamlNode* root, const char* ns) {
    if (!root || !ns) return nullptr;
    const YamlNode* nss = root->get("namespaces");
    if (!nss) return nullptr;
    for (const YamlNode* it = nss->first_child; it; it = it->next_sibling) {
        const char* name = it->get_str("name", nullptr);
        if (name && strcmp(name, ns) == 0)
            return it->get_str("state_engine", nullptr);
    }
    return nullptr;
}

ControllerConfig controller_config_from_yaml(const YamlNode* root) {
    ControllerConfig cfg;
    if (!root) {
        cfg.error = "empty config (no root node)";
        return cfg;
    }

    /* --- PHYSICAL half: hookup wires with a `publishes:` slot -> sensors -- */
    const YamlNode* hookup = root->get("hookup");
    if (hookup) {
        const YamlNode* wires = hookup->get("wires");
        if (wires) {
            for (const YamlNode* w = wires->first_child; w;
                 w = w->next_sibling) {
                const char* pub = w->get_str("publishes", nullptr);
                if (!pub || !*pub) continue;         /* not a slot wire   */
                const int pin = controller_parse_gpio(w->get_str("to", nullptr));
                if (pin < 0) continue;               /* not an MCU GPIO   */
                SensorSpec s;
                s.ns = pub;
                s.pin = pin;
                s.adc_max = 4095;                    /* 12-bit ADC        */
                const char* eng = engine_for_ns(root, pub);
                s.state_engine = eng ? eng : "";
                s.out_max = out_max_for_engine(eng);
                s.sample_hz = 20;
                /* leaf-owned conditioning, declared on the wire (config, not
                 * firmware): invert any pot, deadband (default denoiser) and
                 * optional EMA smoothing. */
                s.invert    = w->get_bool("invert", false);
                s.deadband  = w->get_int("deadband", 2);
                s.ema_alpha = w->get_float("ema", 0.0f);
                /* Output-scale level quantization: snap to multiples of `step`
                 * and publish only on a level change.  Default 0 = disabled,
                 * so a wire that omits it is byte-identical. [CXC-HAL-CONDITION-STEP] */
                s.step      = w->get_int("step", 0);
                /* Calibration / safety bands, in RAW ADC CODES — the INPUT
                 * half of the same in_min..in_max -> 0..out_max map the
                 * OUTPUT half runs.  Absent = 0/0 = no band = today's
                 * behaviour exactly.  [CXC-HAL-CONDITION-BANDS] */
                s.in_min    = w->get_int("in_min", 0);
                s.in_max    = w->get_int("in_max", 0);
                cfg.sensors.push_back(s);
            }
        }
    }

    /* --- S6 AUDIO half: the DY-SV5F cover block -> AudioCoverConfig ------- */
    const YamlNode* ac = root->get("audio_cover");
    if (ac) {
        cfg.audio.present = true;
        const YamlNode* uart = ac->get("uart");
        if (uart) {
            cfg.audio.tx_pin =
                controller_parse_gpio(uart->get_str("tx_pin", nullptr));
            cfg.audio.busy_pin =
                controller_parse_gpio(uart->get_str("busy_pin", nullptr));
            cfg.audio.baud = uart->get_int("baud", 9600);
        }
        const YamlNode* subs = ac->get("subscribes");
        if (subs) {
            for (const YamlNode* s = subs->first_child; s; s = s->next_sibling)
                if (s->scalar && *s->scalar)
                    cfg.audio.subscribes.push_back(s->scalar);
        }
        const YamlNode* cues = ac->get("cues");
        if (cues) {
            for (const YamlNode* c = cues->first_child; c; c = c->next_sibling) {
                const char* cue = c->get_str("cue", nullptr);
                const char* source = c->get_str("source", nullptr);
                const int track = c->get_int("track", 0);
                if (!cue || !source) continue;
                if (strcmp(cue, "condition_one") == 0) {
                    cfg.audio.contacts_ns = source;
                    cfg.audio.condition_one_track = track;
                } else if (strcmp(cue, "jump") == 0) {
                    cfg.audio.jump_ns = source;
                    cfg.audio.jump_track = track;
                }
            }
        }
        /* Belt-and-suspenders: if the explicit subscribe list was absent,
         * derive it from the cue sources so the shell still subscribes. */
        if (cfg.audio.subscribes.empty()) {
            if (!cfg.audio.contacts_ns.empty())
                cfg.audio.subscribes.push_back(cfg.audio.contacts_ns);
            if (!cfg.audio.jump_ns.empty())
                cfg.audio.subscribes.push_back(cfg.audio.jump_ns);
        }
    }

    /* --- OUTPUT half: the relay-bank actuator -> RelayActuatorSpec ------- */
    /* Top-level `relay_actuator:` block (the direct dual of audio_cover:) —
     * subscribe an enum ns, drive a set of relay pins via a value->pin map,
     * break-before-make with a dead-time.  See relay_actuator.{h,cpp}. */
    const YamlNode* ra = root->get("relay_actuator");
    if (ra) {
        RelayActuatorSpec& r = cfg.relay;
        const char* rns = ra->get_str("subscribes", nullptr);
        if (!rns || !*rns) {
            cfg.error = "relay_actuator: missing `subscribes:` namespace";
            return cfg;
        }
        r.ns = rns;
        r.active_high  = ra->get_bool("active_high", true);
        r.dead_time_ms = ra->get_int("dead_time_ms", 80);
        const char* dv = ra->get_str("default", nullptr);
        r.default_value = (dv && *dv) ? dv : "off";

        /* ALL owned relay pins (break de-energizes every one). */
        const YamlNode* pins = ra->get("pins");
        if (pins) {
            for (const YamlNode* p = pins->first_child; p; p = p->next_sibling)
                if (p->scalar) {
                    int gp = controller_parse_gpio(p->scalar);
                    if (gp >= 0) r.pins.push_back(gp);
                }
        }

        /* value -> energized pin map ("none"/absent pin -> -1 = all open). */
        const YamlNode* states = ra->get("states");
        if (states) {
            for (const YamlNode* s = states->first_child; s;
                 s = s->next_sibling) {
                const char* val = s->get_str("value", nullptr);
                if (!val || !*val) continue;
                RelayState st;
                st.value = val;
                st.pin = controller_parse_gpio(s->get_str("pin", nullptr));
                r.states.push_back(st);
            }
        }

        if (r.states.empty()) {
            cfg.error = "relay_actuator: no `states:` value->pin rows";
            return cfg;
        }
        /* Every state pin must be one of the owned pins (or -1); a state that
         * energizes an unlisted pin is a config error, not a silent pass —
         * break-before-make must own every pin it can close. */
        for (const RelayState& st : r.states) {
            if (st.pin < 0) continue;
            bool owned = false;
            for (int p : r.pins) if (p == st.pin) { owned = true; break; }
            if (!owned) {
                cfg.error = "relay_actuator: state '" + st.value +
                            "' energizes a pin not in `pins:`";
                return cfg;
            }
        }
        r.present = true;
    }

    if (cfg.sensors.empty() && !cfg.audio.present && !cfg.relay.present)
        cfg.error = "config has no hookup sensors, relay_actuator, or "
                    "audio_cover";
    return cfg;
}

ControllerConfig controller_config_parse(const char* text, size_t len) {
    YamlDoc doc;
    const YamlNode* root = doc.parse(text, len);
    if (!root) {
        ControllerConfig cfg;
        const char* e = doc.error();
        cfg.error = (e && *e) ? e : "yaml parse failed";
        return cfg;
    }
    /* from_yaml COPIES every string into std::string, so the config is safe
     * after this transient YamlDoc arena is destroyed on return. */
    return controller_config_from_yaml(root);
}
