/*
 * reactive_core.h — the board-agnostic reactive CORE, R1.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-CORE] [CXC-ER-C1]
 *
 * A C++ port of the Python reactive model (the SEMANTICS of
 * python/composeio/render/engine/bindings.py AttrBinding + the
 * runtime's publish-on-change output path, engine/runtime.py
 * _publish_outputs / tick):
 *
 *   namespace store          name -> current raw bus value
 *   bindings                 output namespace <- (bare namespace | expr:)
 *                            expr: compiles via the canonical on-chip DSL
 *                            (cyd/src/dsl, [CXC-DSL-SITES] value-expr site)
 *   on a value arrival       re-evaluate exactly the bindings that READ
 *                            the changed namespace; a changed result fires
 *                            the output sink (first evaluation always
 *                            fires, so downstream gets a defined initial
 *                            state — runtime._publish_outputs semantics)
 *
 * Pinned Python-parity semantics:
 *   - bare-namespace binding result = dsl_coerce_input(raw): bool ->
 *     "on"/"off", numeric (incl. numeric strings) -> float64, else the
 *     string — the same view an ns() read gets.
 *   - dt/t ([CXC-DSL-STATE]): t is caller-supplied seconds; dt is the gap
 *     to the previous EXTERNAL arrival on this core (0.0 on the first),
 *     exactly runtime.tick's dt.  Locally cascaded re-evaluations reuse
 *     the same-tick dt (Python: a later output sees an earlier one's
 *     same-tick publish).
 *   - an eval error WITHHOLDS the publish (warn path), never crashes.
 *   - change detection matches Python `!=`: number/bool compare
 *     numerically (true == 1.0), strings exactly; NaN != NaN so a NaN
 *     result always republishes.
 *   - a fired output is written back into the store and cascades to
 *     bindings that read it (Python: bus.publish_state notifies local
 *     subscribers).  Cascades are depth-capped ([kMaxCascade]) so a
 *     non-converging cycle degrades to a warning, not a hang.
 *
 * PURE C++: no Arduino, no ESP-IDF, no display, no board pins — the
 * board seam lives in board.h and is consumed by the app shell only
 * ([CXC-ER-C3] board-agnostic discipline).  Host-testable exactly like
 * the DSL (env:native_test).
 */

#ifndef REACTIVE_CORE_H
#define REACTIVE_CORE_H

#include <cstddef>
#include <memory>
#include <string>
#include <vector>

#include "dsl.h"    /* canonical: cyd/src/dsl.h via -I */

/* An OWNED raw bus value — the namespace store's cell.  Mirrors DslRaw
 * (which borrows its string) with owned storage. */
struct CoreValue {
    enum class Kind : uint8_t { Missing, Number, Bool, String };

    Kind kind = Kind::Missing;
    double num = 0.0;
    bool b = false;
    std::string str;

    static CoreValue missing() { return CoreValue(); }
    static CoreValue number(double v) {
        CoreValue c; c.kind = Kind::Number; c.num = v; return c;
    }
    static CoreValue boolean(bool v) {
        CoreValue c; c.kind = Kind::Bool; c.b = v; return c;
    }
    static CoreValue string(const char* s) {
        CoreValue c; c.kind = Kind::String; c.str = s ? s : ""; return c;
    }
    static CoreValue from_dsl(const DslValue& v);

    /* Borrowed DSL view — valid while *this* is alive and unmodified. */
    DslRaw raw() const;
};

class ReactiveCore {
public:
    /* Fired on a changed binding result (first evaluation always fires).
     * value is the DSL-typed result; route it to a publish and/or a
     * board output in the app shell. */
    using OutputSink = void (*)(void* user, const char* output_ns,
                                const DslValue& value);

    void set_sink(OutputSink sink, void* user) {
        _sink = sink;
        _sink_user = user;
    }

    /* Declare a binding: *source* is either
     *   "expr: <dsl source>"   the [CXC-DSL-SITES] value-expr site,
     *                          compiled by the canonical DSL, or
     *   "<namespace>"          a bare-namespace (identity) binding.
     * Returns false when the expr is refused (see error()) — the
     * binding is NOT added, matching the Python refused-expr path. */
    bool add_binding(const char* output_ns, const char* source);
    const char* error() const { return _error.c_str(); }

    /* Deduped input namespaces every binding reads, in first-appearance
     * order — the bus subscription list (expr.namespaces analog). */
    size_t subscription_count() const { return _subscriptions.size(); }
    const char* subscription_at(size_t i) const {
        return _subscriptions[i].c_str();
    }

    /* An EXTERNAL bus value arrived for *ns* at time *t* (seconds,
     * monotonic).  Stores it, then re-evaluates the bindings that read
     * *ns*; changed results fire the sink, write back to the store, and
     * cascade.  Returns the number of outputs fired (incl. cascades). */
    int on_bus_value(const char* ns, const CoreValue& value, double t);

    /* Store read (tests / serial dump).  nullptr when never seen. */
    const CoreValue* get(const char* ns) const;

    /* Observability for tests: total binding evaluations performed. */
    size_t eval_count() const { return _eval_count; }
    /* Last withheld-publish eval error ("" when none). */
    const char* last_eval_error() const { return _eval_error.c_str(); }

    size_t binding_count() const { return _bindings.size(); }

    /* A non-converging binding cycle stops here per external arrival. */
    static const int kMaxCascade = 8;

private:
    struct Binding {
        std::string output_ns;
        std::string bare_ns;         /* identity binding when non-empty */
        DslExpression expr;          /* compiled when bare_ns is empty  */
        std::vector<std::string> deps;
        bool has_last = false;
        DslValue last;
    };

    void _note_subscriptions(const Binding& b);
    void _store(const char* ns, const CoreValue& value);
    int _arrival(const char* ns, const CoreValue& value, int depth);
    bool _evaluate(Binding& b, DslValue& out);
    static bool _same(const DslValue& a, const DslValue& b);

    static DslRaw _read_ns_cb(void* user, const char* name);
    static DslRaw _read_var_cb(void* user, const char* name);

    std::vector<std::pair<std::string, CoreValue>> _values;
    std::vector<std::unique_ptr<Binding>> _bindings;
    std::vector<std::string> _subscriptions;
    OutputSink _sink = nullptr;
    void* _sink_user = nullptr;
    std::string _error;
    std::string _eval_error;
    size_t _eval_count = 0;
    double _cur_dt = 0.0;
    double _cur_t = 0.0;
    double _last_tick_t = 0.0;
    bool _ticked = false;
    bool _warned_cascade = false;
};

#endif /* REACTIVE_CORE_H */
