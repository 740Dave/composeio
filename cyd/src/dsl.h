/*
 * dsl.h — ComposeUX Expression DSL, C++/CYD interpreter (Pass 2b)
 *
 * Spec: [SPEC:CXC-DSL].  This is a byte-exact port of the Python
 * REFERENCE interpreter (python/composeio/render/engine/dsl.py):
 * tokenizer -> recursive-descent parser -> own-AST tree-walking
 * evaluator.  It must reproduce the shared conformance corpus
 * (render/engine/dsl_conformance/dsl_corpus.json, 226 cases)
 * bit-for-bit ([CXC-DSL-CROSSPLAT]) — see cyd/src/dsl_selftest.cpp.
 *
 * Pinned semantics ported 1:1 from the reference:
 *   - float64 (double) EVERYWHERE — the ESP32 soft-float double is
 *     IEEE-754; a float32 shortcut is FORBIDDEN ([CXC-DSL-SEMANTICS]).
 *   - left-assoc arithmetic, no reassociation; short-circuit and/or
 *     returning the deciding OPERAND; lazy ternary ([CXC-DSL-C3]).
 *   - guarded x/0 and x%0 -> +0.0, never NaN/inf ([CXC-DSL-C4]);
 *     '%' is the truncated remainder (C fmod), not Python's floored %.
 *   - round() is HALF-TO-EVEN (banker's) — NOT std::round
 *     ([CXC-DSL-C12]); round/floor/ceil preserve IEEE signed zero.
 *   - canon-typed equality: numeric when both sides coerce, else via
 *     the [CXC-BIND] canon() vocabulary (on/true/yes/1 -> "on", ...)
 *     ([CXC-DSL-C6]).
 *   - closed function set: min max abs clamp round floor ceil; any
 *     other name is a PARSE error ([CXC-DSL-C5]).
 *   - ns() takes one string LITERAL; an unset namespace reads 0.0.
 *
 * Sandbox ([CXC-DSL-SANDBOX]) is inherent — there is no dynamic
 * evaluation in C++ — but the parser also REJECTS everything outside
 * the closed grammar (unknown names, attribute/subscript syntax,
 * assignment) as a parse error, mirroring the DslParseError /
 * DslEvalError split the corpus encodes.
 *
 * Embedded discipline: hard bounds everywhere (source length, token
 * count, node pool, parser nesting, AST depth) — pathological input
 * returns a parse error instead of overflowing the ESP32 stack.  One
 * std::string source copy + a std::vector node pool per expression;
 * expressions are parsed once and evaluated many times with zero
 * further parse-side allocation.
 *
 * Pure C++ (no ESP-IDF includes) so it host-compiles for desk testing,
 * same idiom as yaml_min.h / cyd/tools/yaml_min_test.cpp.
 */

#ifndef DSL_H
#define DSL_H

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

/* -------------------------------------------------------------------
 * Values
 * ------------------------------------------------------------------- */

/* A DSL result: exactly one of float64 / bool / string — the three
 * result types the conformance contract distinguishes (a 1.0 does NOT
 * match true). */
struct DslValue {
    enum class Type : uint8_t { Number, Bool, String };

    Type type = Type::Number;
    double num = 0.0;
    bool b = false;
    std::string str;

    static DslValue number(double v);
    static DslValue boolean(bool v);
    static DslValue string(const char* s, size_t n);
    static DslValue string(const std::string& s);
};

/* A RAW bus/vars value BEFORE input coercion (the type a JSON/YAML/bus
 * publish carries).  ns()/$var reads go raw -> dsl_coerce_input(). */
struct DslRaw {
    enum class Kind : uint8_t { Missing, Number, Bool, String };

    Kind kind = Kind::Missing;
    double num = 0.0;
    bool b = false;
    const char* str = nullptr;      /* NUL-terminated, borrowed */

    static DslRaw missing();
    static DslRaw number(double v);
    static DslRaw boolean(bool v);
    static DslRaw string(const char* s);
};

/* Evaluation context ([CXC-DSL-STATE]): what an expression can see —
 * and NOTHING else.  read_ns/read_var return Missing when unset. */
struct DslContext {
    DslRaw (*read_ns)(void* user, const char* name) = nullptr;
    DslRaw (*read_var)(void* user, const char* name) = nullptr;
    void* user = nullptr;
    double dt = 0.0;
    double t = 0.0;
};

/* -------------------------------------------------------------------
 * Value-model helpers — pinned by the corpus (exported for reuse by
 * the [CXC-DSL-SITES] result-coercion code and for host tests).
 * ------------------------------------------------------------------- */

/* The ONE numeric-coercion rule: bool -> 1/0, number -> itself,
 * numeric-looking string -> parsed float64.  false when no coercion. */
bool dsl_try_number(const DslValue& v, double& out);

/* [CXC-BIND] canon(): trim + lowercase; on/true/yes/1 -> "on",
 * off/false/no/0 -> "off". */
std::string dsl_canon(const std::string& s);

/* DSL truthiness: bool -> itself; number -> != 0.0 (-0.0 falsy);
 * string -> canon(s) not in {"off", ""}. */
bool dsl_truthy(const DslValue& v);

/* Typed equality ([CXC-DSL-C6]): numeric when both sides coerce,
 * else both collapse to the canon token. */
bool dsl_values_equal(const DslValue& a, const DslValue& b);

/* ns()/$var input coercion: bool -> "on"/"off"; numeric (including
 * numeric strings) -> float64; anything else -> its string. */
DslValue dsl_coerce_input(const DslRaw& raw);

/* Optional warning sink for the one-time guarded-div / unset-ns
 * warnings ([CXC-DSL-C4]).  Default: silent. */
void dsl_set_log(void (*log)(const char* msg));

/* -------------------------------------------------------------------
 * Expression
 * ------------------------------------------------------------------- */

class DslExpression {
public:
    DslExpression() = default;
    DslExpression(const DslExpression&) = delete;
    DslExpression& operator=(const DslExpression&) = delete;

    /* Parse *src* against the closed grammar ([CXC-DSL-GRAMMAR]).
     * Returns false (see error()) on ANYTHING outside it — including
     * over-limit input (too long / too many nodes / too deep). */
    bool parse(const char* src);

    bool valid() const { return _valid; }
    const char* error() const { return _error.c_str(); }
    const char* source() const { return _source.c_str(); }

    /* Evaluate.  Returns false on an AUTHOR error at eval time
     * (unknown $var, ordering comparison on a non-numeric, ...) —
     * the DslEvalError split; see eval_error(). */
    bool evaluate(const DslContext& ctx, DslValue& out);
    const char* eval_error() const { return _eval_error.c_str(); }

    /* Static ns() reads, in first-appearance order — the subscription
     * list the runtime wires up ([CXC-DSL-GRAMMAR] namespace read). */
    size_t namespace_count() const { return _namespaces.size(); }
    const char* namespace_at(size_t i) const {
        return _namespaces[i].c_str();
    }

    /* Hard bounds (embedded discipline) — pathological input parse-
     * errors instead of overflowing the stack.  Worst-case parser
     * stack ~= kMaxNest * ~0.6 KB, worst-case evaluator stack ~=
     * kMaxAstDepth * ~0.25 KB; parse/evaluate on a task with >= 48 KB
     * of stack headroom (the corpus runner uses 48 KB; the production
     * scene task budget must cover this before bindings ship). */
    static const size_t kMaxSource = 2048;   /* bytes of expression   */
    static const size_t kMaxTokens = 512;    /* tokens                */
    static const int kMaxNodes = 512;        /* AST node pool         */
    static const int kMaxNest = 48;          /* parser nesting depth  */
    static const int kMaxAstDepth = 64;      /* evaluator recursion   */

private:
    friend struct DslParser;

    struct Node {
        enum class Kind : uint8_t {
            Num, Str, Bool, Dt, T, Var, Ns,
            Neg, Not, Bin, Cmp, And, Or, Ternary, Call
        };
        Kind kind = Kind::Num;
        uint8_t op = 0;       /* Bin/Cmp operator, or Call function id */
        uint8_t depth = 1;    /* AST height at this node               */
        uint8_t argc = 0;     /* Call arg count                        */
        double num = 0.0;     /* Num literal                          */
        bool bval = false;    /* Bool literal                         */
        int16_t str = -1;     /* index into _strs (Str/Var name)      */
        int16_t ns_idx = -1;  /* Ns: index into _namespaces           */
        int16_t a = -1, b = -1, c = -1;   /* children                 */
    };

    bool eval_node(int idx, const DslContext& ctx, DslValue& out);
    bool num_of(const DslValue& v, const char* what, double& out);
    void warn_once(bool& flag, const char* what);

    std::string _source;
    std::string _error;                 /* parse error   */
    std::string _eval_error;            /* last eval error */
    std::vector<Node> _nodes;
    std::vector<std::string> _strs;     /* string literals + var names */
    std::vector<std::string> _namespaces;
    std::vector<bool> _ns_warned;       /* one-time unset-ns warnings  */
    int _root = -1;
    bool _valid = false;
    bool _warned_div0 = false;
    bool _warned_mod0 = false;
};

#endif /* DSL_H */
