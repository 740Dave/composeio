/*
 * reactive_core.cpp — [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-CORE], R1.
 * See reactive_core.h for the ported semantics.
 */

#include "reactive_core.h"

#include <cctype>
#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ */
/* CoreValue                                                            */
/* ------------------------------------------------------------------ */

CoreValue CoreValue::from_dsl(const DslValue& v) {
    switch (v.type) {
        case DslValue::Type::Number: return CoreValue::number(v.num);
        case DslValue::Type::Bool:   return CoreValue::boolean(v.b);
        default:                     return CoreValue::string(v.str.c_str());
    }
}

DslRaw CoreValue::raw() const {
    switch (kind) {
        case Kind::Number: return DslRaw::number(num);
        case Kind::Bool:   return DslRaw::boolean(b);
        case Kind::String: return DslRaw::string(str.c_str());
        default:           return DslRaw::missing();
    }
}

/* ------------------------------------------------------------------ */
/* Binding declaration                                                  */
/* ------------------------------------------------------------------ */

/* "expr:" prefix detection — tolerant of "expr:" / "expr: " spellings
 * (the .compose.yaml site key, flattened to a string for R1). */
static const char* expr_source(const char* s) {
    if (strncmp(s, "expr:", 5) != 0) return nullptr;
    const char* p = s + 5;
    while (*p == ' ' || *p == '\t') p++;
    return p;
}

bool ReactiveCore::add_binding(const char* output_ns, const char* source) {
    _error.clear();
    if (!output_ns || !*output_ns || !source) {
        _error = "binding needs an output namespace and a source";
        return false;
    }
    auto b = std::unique_ptr<Binding>(new Binding());
    b->output_ns = output_ns;

    if (const char* src = expr_source(source)) {
        if (!b->expr.parse(src)) {
            /* refused expr => binding refused ([CXC-BIND-BOUNDARY] /
             * dsl.try_parse semantics) */
            _error = b->expr.error();
            return false;
        }
        for (size_t i = 0; i < b->expr.namespace_count(); i++)
            b->deps.push_back(b->expr.namespace_at(i));
    } else {
        b->bare_ns = source;
        b->deps.push_back(source);
    }

    _note_subscriptions(*b);
    _bindings.push_back(std::move(b));
    return true;
}

void ReactiveCore::_note_subscriptions(const Binding& b) {
    for (const auto& dep : b.deps) {
        bool seen = false;
        for (const auto& s : _subscriptions)
            if (s == dep) { seen = true; break; }
        if (!seen) _subscriptions.push_back(dep);
    }
}

/* ------------------------------------------------------------------ */
/* Store                                                                */
/* ------------------------------------------------------------------ */

void ReactiveCore::_store(const char* ns, const CoreValue& value) {
    for (auto& kv : _values) {
        if (kv.first == ns) { kv.second = value; return; }
    }
    _values.emplace_back(ns, value);
}

const CoreValue* ReactiveCore::get(const char* ns) const {
    for (const auto& kv : _values)
        if (kv.first == ns) return &kv.second;
    return nullptr;
}

/* ------------------------------------------------------------------ */
/* Evaluation                                                           */
/* ------------------------------------------------------------------ */

DslRaw ReactiveCore::_read_ns_cb(void* user, const char* name) {
    const ReactiveCore* core = (const ReactiveCore*)user;
    const CoreValue* v = core->get(name);
    return v ? v->raw() : DslRaw::missing();
}

DslRaw ReactiveCore::_read_var_cb(void*, const char*) {
    /* R1 has no variables: block — an unknown $var is an eval error,
     * matching the reference. */
    return DslRaw::missing();
}

bool ReactiveCore::_evaluate(Binding& b, DslValue& out) {
    _eval_count++;
    if (!b.bare_ns.empty()) {
        const CoreValue* v = get(b.bare_ns.c_str());
        /* identity binding: the ns()-read view of the raw value */
        out = dsl_coerce_input(v ? v->raw() : DslRaw::missing());
        return true;
    }
    DslContext ctx;
    ctx.read_ns = _read_ns_cb;
    ctx.read_var = _read_var_cb;
    ctx.user = this;
    ctx.dt = _cur_dt;
    ctx.t = _cur_t;
    if (!b.expr.evaluate(ctx, out)) {
        /* publish withheld — runtime._publish_outputs DslEvalError path */
        _eval_error = b.expr.eval_error();
        return false;
    }
    return true;
}

/* Python `!=` change detection: numbers and bools compare numerically
 * (True == 1.0), strings exactly; cross string/number is a change.
 * NaN != NaN => a NaN result always counts as changed. */
bool ReactiveCore::_same(const DslValue& a, const DslValue& b) {
    const bool a_num = a.type != DslValue::Type::String;
    const bool b_num = b.type != DslValue::Type::String;
    if (a_num && b_num) {
        const double av = a.type == DslValue::Type::Bool ? (a.b ? 1.0 : 0.0)
                                                         : a.num;
        const double bv = b.type == DslValue::Type::Bool ? (b.b ? 1.0 : 0.0)
                                                         : b.num;
        return av == bv;                      /* NaN != NaN by IEEE */
    }
    if (a.type == DslValue::Type::String && b.type == DslValue::Type::String)
        return a.str == b.str;
    return false;
}

int ReactiveCore::_arrival(const char* ns, const CoreValue& value,
                           int depth) {
    if (depth >= kMaxCascade) {
        if (!_warned_cascade) {
            _warned_cascade = true;
            _eval_error = "binding cascade depth cap hit (cycle?)";
        }
        return 0;
    }
    _store(ns, value);

    int fired = 0;
    for (auto& bp : _bindings) {
        Binding& b = *bp;
        bool reads = false;
        for (const auto& dep : b.deps)
            if (dep == ns) { reads = true; break; }
        if (!reads) continue;

        DslValue result;
        if (!_evaluate(b, result)) continue;

        const bool changed = !b.has_last || !_same(b.last, result);
        if (!changed) continue;               /* publish-on-change */
        b.has_last = true;
        b.last = result;
        fired++;
        if (_sink) _sink(_sink_user, b.output_ns.c_str(), result);
        /* Local write-back + cascade: Python's bus.publish_state
         * notifies in-process subscribers of the derived namespace. */
        fired += _arrival(b.output_ns.c_str(), CoreValue::from_dsl(result),
                          depth + 1);
    }
    return fired;
}

int ReactiveCore::on_bus_value(const char* ns, const CoreValue& value,
                               double t) {
    if (!ns || !*ns) return 0;
    /* One external arrival == one tick: dt is the gap to the previous
     * tick (0.0 on the first), runtime.tick semantics. */
    _cur_dt = _ticked ? (t > _last_tick_t ? t - _last_tick_t : 0.0) : 0.0;
    _cur_t = t;
    _last_tick_t = t;
    _ticked = true;
    _eval_error.clear();
    return _arrival(ns, value, 0);
}
