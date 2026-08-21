/*
 * dsl.cpp — ComposeUX Expression DSL interpreter ([SPEC:CXC-DSL],
 * Pass 2b).  Byte-exact port of render/engine/dsl.py — see dsl.h for
 * the pinned-semantics summary.  Every deliberate choice below cites
 * the reference behavior it reproduces.
 */

#include "dsl.h"

#include <cctype>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Logging (one-time warnings; conformance-irrelevant, parity-kept)    */
/* ------------------------------------------------------------------ */

static void (*g_dsl_log)(const char*) = nullptr;

void dsl_set_log(void (*log)(const char* msg)) { g_dsl_log = log; }

static void dsl_log(const char* fmt, ...) {
    if (!g_dsl_log) return;
    char buf[192];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    g_dsl_log(buf);
}

/* ------------------------------------------------------------------ */
/* DslValue / DslRaw constructors                                      */
/* ------------------------------------------------------------------ */

DslValue DslValue::number(double v) {
    DslValue r;
    r.type = Type::Number;
    r.num = v;
    return r;
}

DslValue DslValue::boolean(bool v) {
    DslValue r;
    r.type = Type::Bool;
    r.b = v;
    return r;
}

DslValue DslValue::string(const char* s, size_t n) {
    DslValue r;
    r.type = Type::String;
    r.str.assign(s, n);
    return r;
}

DslValue DslValue::string(const std::string& s) {
    DslValue r;
    r.type = Type::String;
    r.str = s;
    return r;
}

DslRaw DslRaw::missing() { return DslRaw(); }

DslRaw DslRaw::number(double v) {
    DslRaw r;
    r.kind = Kind::Number;
    r.num = v;
    return r;
}

DslRaw DslRaw::boolean(bool v) {
    DslRaw r;
    r.kind = Kind::Bool;
    r.b = v;
    return r;
}

DslRaw DslRaw::string(const char* s) {
    DslRaw r;
    r.kind = Kind::String;
    r.str = s;
    return r;
}

/* ------------------------------------------------------------------ */
/* Value-model helpers — each mirrors a dsl.py function by name        */
/* ------------------------------------------------------------------ */

/* dsl.py _NUMERIC_STR_RE:
 *   \s*[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?\s*\Z
 * Validate exactly that shape, then convert with strtod (correctly
 * rounded on macOS libc AND ESP-IDF newlib — both gdtoa-derived, the
 * same nearest-double result CPython's float() produces). */
static bool parse_numeric_string(const char* s, size_t n, double& out) {
    size_t i = 0, j = n;
    while (i < j && isspace((unsigned char)s[i])) i++;
    while (j > i && isspace((unsigned char)s[j - 1])) j--;
    if (i >= j) return false;
    size_t p = i;
    if (s[p] == '+' || s[p] == '-') p++;
    if (p < j && s[p] == '.') {                       /* .digits */
        p++;
        if (p >= j || !isdigit((unsigned char)s[p])) return false;
        while (p < j && isdigit((unsigned char)s[p])) p++;
    } else {                                          /* digits[.digits*] */
        if (p >= j || !isdigit((unsigned char)s[p])) return false;
        while (p < j && isdigit((unsigned char)s[p])) p++;
        if (p < j && s[p] == '.') {
            p++;
            while (p < j && isdigit((unsigned char)s[p])) p++;
        }
    }
    if (p < j && (s[p] == 'e' || s[p] == 'E')) {      /* exponent */
        p++;
        if (p < j && (s[p] == '+' || s[p] == '-')) p++;
        if (p >= j || !isdigit((unsigned char)s[p])) return false;
        while (p < j && isdigit((unsigned char)s[p])) p++;
    }
    if (p != j) return false;
    std::string tok(s + i, j - i);                    /* bounded copy */
    out = strtod(tok.c_str(), nullptr);
    return true;
}

bool dsl_try_number(const DslValue& v, double& out) {
    switch (v.type) {
        case DslValue::Type::Bool:                    /* bool FIRST (dsl.py) */
            out = v.b ? 1.0 : 0.0;
            return true;
        case DslValue::Type::Number:
            out = v.num;
            return true;
        case DslValue::Type::String:
            return parse_numeric_string(v.str.data(), v.str.size(), out);
    }
    return false;
}

std::string dsl_canon(const std::string& s) {
    size_t b = 0, e = s.size();
    while (b < e && isspace((unsigned char)s[b])) b++;
    while (e > b && isspace((unsigned char)s[e - 1])) e--;
    std::string r;
    r.reserve(e - b);
    for (size_t i = b; i < e; i++)
        r += (char)tolower((unsigned char)s[i]);
    if (r == "on" || r == "true" || r == "yes" || r == "1") return "on";
    if (r == "off" || r == "false" || r == "no" || r == "0") return "off";
    return r;
}

/* Python-repr-equivalent shortest round-trip formatting, used ONLY by
 * the canon token of a NON-integral (or >=1e16 / non-finite) float —
 * i.e. only when a float meets a non-numeric string under ==/!=.  The
 * shortest %.*g that round-trips is the same digit string CPython's
 * repr() picks; formatting corners beyond that (fixed-vs-exponent
 * switchover at 17 significant digits) cannot change an equality
 * verdict against a non-numeric string. */
static std::string repr_double(double v) {
    if (std::isnan(v)) return "nan";
    if (std::isinf(v)) return v > 0 ? "inf" : "-inf";
    char buf[40];
    uint64_t want, got;
    memcpy(&want, &v, sizeof want);
    for (int prec = 1; prec <= 17; prec++) {
        snprintf(buf, sizeof buf, "%.*g", prec, v);
        double back = strtod(buf, nullptr);
        memcpy(&got, &back, sizeof got);
        if (got == want) break;                       /* bit round-trip */
    }
    return std::string(buf);
}

/* dsl.py _canon_token(): canon() with float64 vocabulary — integral
 * floats drop the '.0' so 1.0 -> '1' -> 'on' ([CXC-DSL-C6]). */
static std::string dsl_canon_token(const DslValue& v) {
    switch (v.type) {
        case DslValue::Type::Bool:
            return v.b ? "on" : "off";
        case DslValue::Type::Number: {
            double d = v.num;
            if (std::isfinite(d) && d == std::floor(d) &&
                std::fabs(d) < 1e16) {
                char buf[32];
                snprintf(buf, sizeof buf, "%lld", (long long)d);
                return dsl_canon(std::string(buf));
            }
            return dsl_canon(repr_double(d));
        }
        case DslValue::Type::String:
            return dsl_canon(v.str);
    }
    return std::string();
}

bool dsl_truthy(const DslValue& v) {
    switch (v.type) {
        case DslValue::Type::Bool:
            return v.b;
        case DslValue::Type::Number:
            return v.num != 0.0;                      /* -0.0 is falsy */
        case DslValue::Type::String: {
            std::string c = dsl_canon(v.str);
            return !(c == "off" || c.empty());
        }
    }
    return false;
}

bool dsl_values_equal(const DslValue& a, const DslValue& b) {
    double na, nb;
    bool ha = dsl_try_number(a, na);
    bool hb = dsl_try_number(b, nb);
    if (ha && hb) return na == nb;                    /* -0.0 == 0.0 */
    return dsl_canon_token(a) == dsl_canon_token(b);
}

DslValue dsl_coerce_input(const DslRaw& raw) {
    switch (raw.kind) {
        case DslRaw::Kind::Bool:                      /* bool FIRST */
            return DslValue::string(raw.b ? "on" : "off", raw.b ? 2 : 3);
        case DslRaw::Kind::Number:
            return DslValue::number(raw.num);
        case DslRaw::Kind::String: {
            double n;
            size_t len = raw.str ? strlen(raw.str) : 0;
            if (parse_numeric_string(raw.str, len, n))
                return DslValue::number(n);
            return DslValue::string(raw.str ? raw.str : "", len);
        }
        case DslRaw::Kind::Missing:
            break;
    }
    return DslValue::number(0.0);                     /* unreachable */
}

/* IEEE sign-of-zero preservation for round/floor/ceil (dsl.py
 * _signed_zero). */
static double signed_zero(double r, double x) {
    if (r == 0.0) return std::copysign(0.0, x);
    return r;
}

/* HALF-TO-EVEN round ([CXC-DSL-C12]) — explicitly NOT std::round
 * (half-away-from-zero).  x - floor(x) is exact for all finite x
 * (Sterbenz), so the tie test is exact; round(0.49999999999999994)
 * correctly yields 0. */
static double round_half_even(double x) {
    if (!std::isfinite(x)) return x;
    double f = std::floor(x);
    double diff = x - f;
    double r;
    if (diff > 0.5) {
        r = f + 1.0;
    } else if (diff < 0.5) {
        r = f;
    } else {                                          /* exact tie */
        r = (std::fmod(f, 2.0) == 0.0) ? f : f + 1.0;
    }
    return signed_zero(r, x);
}

/* Python builtin min/max keep the FIRST argument on ties —
 * min(-0.0, 0.0) is -0.0 (corpus fn-004). */
static double py_min(double a, double b) { return (b < a) ? b : a; }
static double py_max(double a, double b) { return (b > a) ? b : a; }

/* ------------------------------------------------------------------ */
/* The closed function table ([CXC-DSL-C5])                            */
/* ------------------------------------------------------------------ */

enum : uint8_t {
    FN_MIN, FN_MAX, FN_ABS, FN_CLAMP, FN_ROUND, FN_FLOOR, FN_CEIL
};

static const struct { const char* name; uint8_t arity; } kFunctions[] = {
    {"min", 2}, {"max", 2}, {"abs", 1}, {"clamp", 3},
    {"round", 1}, {"floor", 1}, {"ceil", 1},
};
static const int kFunctionCount =
    (int)(sizeof(kFunctions) / sizeof(kFunctions[0]));

/* ------------------------------------------------------------------ */
/* Tokenizer                                                           */
/* ------------------------------------------------------------------ */

enum : uint8_t {                                      /* operator codes */
    OP_LT, OP_LE, OP_GT, OP_GE, OP_EQ, OP_NE,
    OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD,
    OP_QUESTION, OP_COLON, OP_LPAREN, OP_RPAREN, OP_COMMA
};

static const char* op_name(uint8_t op) {
    static const char* names[] = {
        "<", "<=", ">", ">=", "==", "!=",
        "+", "-", "*", "/", "%", "?", ":", "(", ")", ","
    };
    return names[op];
}

struct Token {
    enum class Kind : uint8_t { Num, Str, Name, Var, Op } kind;
    double num = 0.0;         /* Num */
    uint32_t off = 0;         /* source offset (for messages)        */
    uint32_t soff = 0;        /* Str/Name/Var text start in source   */
    uint32_t slen = 0;        /* Str/Name/Var text length            */
    uint8_t op = 0;           /* Op */
};

/* ------------------------------------------------------------------ */
/* Parser — recursive descent, dsl.py grammar, lowest-first            */
/* ------------------------------------------------------------------ */

struct DslParser {
    DslExpression& ex;
    const std::string& src;
    std::vector<Token> toks;
    size_t i = 0;             /* token cursor */
    int nest = 0;

    explicit DslParser(DslExpression& e) : ex(e), src(e._source) {}

    /* -- error helpers ---------------------------------------------- */

    int fail(const char* fmt, ...) {
        char buf[160];
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(buf, sizeof(buf), fmt, ap);
        va_end(ap);
        ex._error = buf;
        ex._error += " in expression '";
        ex._error += src;
        ex._error += "'";
        return -1;
    }

    /* -- tokenize (whole input upfront, like dsl.py _tokenize) ------- */

    bool tokenize() {
        const char* s = src.c_str();
        size_t n = src.size();
        size_t pos = 0;
        while (pos < n) {
            unsigned char c = (unsigned char)s[pos];
            if (isspace(c)) { pos++; continue; }
            if (toks.size() >= DslExpression::kMaxTokens)
                return fail("expression has too many tokens"), false;

            Token t;
            t.off = (uint32_t)pos;

            /* number: (\d+\.?\d* | \.\d+) ([eE][+-]?\d+)? */
            if (isdigit(c) ||
                (c == '.' && pos + 1 < n &&
                 isdigit((unsigned char)s[pos + 1]))) {
                size_t start = pos;
                if (s[pos] == '.') {
                    pos++;
                    while (pos < n && isdigit((unsigned char)s[pos])) pos++;
                } else {
                    while (pos < n && isdigit((unsigned char)s[pos])) pos++;
                    if (pos < n && s[pos] == '.') {
                        pos++;
                        while (pos < n && isdigit((unsigned char)s[pos]))
                            pos++;
                    }
                }
                if (pos < n && (s[pos] == 'e' || s[pos] == 'E')) {
                    size_t p = pos + 1;
                    if (p < n && (s[p] == '+' || s[p] == '-')) p++;
                    if (p < n && isdigit((unsigned char)s[p])) {
                        p++;
                        while (p < n && isdigit((unsigned char)s[p])) p++;
                        pos = p;      /* exponent valid -> consume */
                    }
                    /* else: leave 'e' for the name tokenizer, exactly
                     * like the reference regex */
                }
                std::string tok(s + start, pos - start);
                t.kind = Token::Kind::Num;
                t.num = strtod(tok.c_str(), nullptr);
                toks.push_back(t);
                continue;
            }

            /* 'single-quoted string' (no escapes, like '[^']*') */
            if (c == '\'') {
                size_t start = pos + 1;
                size_t end = start;
                while (end < n && s[end] != '\'') end++;
                if (end >= n)
                    return fail("unterminated string at position %u",
                                (unsigned)pos), false;
                t.kind = Token::Kind::Str;
                t.soff = (uint32_t)start;
                t.slen = (uint32_t)(end - start);
                toks.push_back(t);
                pos = end + 1;
                continue;
            }

            /* name */
            if (isalpha(c) || c == '_') {
                size_t start = pos;
                while (pos < n && (isalnum((unsigned char)s[pos]) ||
                                   s[pos] == '_')) pos++;
                t.kind = Token::Kind::Name;
                t.soff = (uint32_t)start;
                t.slen = (uint32_t)(pos - start);
                toks.push_back(t);
                continue;
            }

            /* $var */
            if (c == '$') {
                size_t start = pos + 1;
                if (start >= n || !(isalpha((unsigned char)s[start]) ||
                                    s[start] == '_'))
                    return fail("unexpected character '$' at position %u",
                                (unsigned)pos), false;
                size_t end = start;
                while (end < n && (isalnum((unsigned char)s[end]) ||
                                   s[end] == '_')) end++;
                t.kind = Token::Kind::Var;
                t.soff = (uint32_t)start;
                t.slen = (uint32_t)(end - start);
                toks.push_back(t);
                pos = end;
                continue;
            }

            /* operators — two-char first */
            uint8_t op = 0xFF;
            char c1 = (char)c;
            char c2 = (pos + 1 < n) ? s[pos + 1] : '\0';
            if (c1 == '<' && c2 == '=') { op = OP_LE; pos += 2; }
            else if (c1 == '>' && c2 == '=') { op = OP_GE; pos += 2; }
            else if (c1 == '=' && c2 == '=') { op = OP_EQ; pos += 2; }
            else if (c1 == '!' && c2 == '=') { op = OP_NE; pos += 2; }
            else {
                switch (c1) {
                    case '<': op = OP_LT; break;
                    case '>': op = OP_GT; break;
                    case '+': op = OP_ADD; break;
                    case '-': op = OP_SUB; break;
                    case '*': op = OP_MUL; break;
                    case '/': op = OP_DIV; break;
                    case '%': op = OP_MOD; break;
                    case '?': op = OP_QUESTION; break;
                    case ':': op = OP_COLON; break;
                    case '(': op = OP_LPAREN; break;
                    case ')': op = OP_RPAREN; break;
                    case ',': op = OP_COMMA; break;
                    default:
                        return fail("unexpected character '%c' at "
                                    "position %u", c1, (unsigned)pos),
                               false;
                }
                pos++;
            }
            t.kind = Token::Kind::Op;
            t.op = op;
            toks.push_back(t);
        }
        return true;
    }

    /* -- token cursor ------------------------------------------------ */

    const Token* peek() const {
        return (i < toks.size()) ? &toks[i] : nullptr;
    }

    bool at_op(uint8_t op) const {
        const Token* t = peek();
        return t && t->kind == Token::Kind::Op && t->op == op;
    }

    bool at_name(const char* name) const {
        const Token* t = peek();
        return t && t->kind == Token::Kind::Name &&
               t->slen == strlen(name) &&
               strncmp(src.c_str() + t->soff, name, t->slen) == 0;
    }

    bool expect_op(uint8_t op) {
        const Token* t = peek();
        if (!t) { fail("unexpected end of expression"); return false; }
        if (t->kind != Token::Kind::Op || t->op != op) {
            fail("expected '%s' at position %u", op_name(op),
                 (unsigned)t->off);
            return false;
        }
        i++;
        return true;
    }

    std::string tok_text(const Token& t) const {
        return src.substr(t.soff, t.slen);
    }

    /* -- node pool ---------------------------------------------------- */

    int add_node(DslExpression::Node n) {
        int d = 0;
        const int kids[3] = {n.a, n.b, n.c};
        for (int k = 0; k < 3; k++)
            if (kids[k] >= 0 && ex._nodes[kids[k]].depth > d)
                d = ex._nodes[kids[k]].depth;
        if (d + 1 > DslExpression::kMaxAstDepth)
            return fail("expression too deeply nested");
        n.depth = (uint8_t)(d + 1);
        if ((int)ex._nodes.size() >= DslExpression::kMaxNodes)
            return fail("expression too large");
        ex._nodes.push_back(n);
        return (int)ex._nodes.size() - 1;
    }

    int intern_str(const Token& t) {
        ex._strs.push_back(src.substr(t.soff, t.slen));
        return (int)ex._strs.size() - 1;
    }

    /* -- grammar, lowest first (mirrors dsl.py _Parser) -------------- */

    int parse_expression() {                          /* ternary — lowest */
        if (++nest > DslExpression::kMaxNest) {
            nest--;
            return fail("expression nests too deeply");
        }
        int r = parse_expression_inner();
        nest--;
        return r;
    }

    int parse_expression_inner() {
        int cond = parse_or();
        if (cond < 0) return -1;
        if (at_op(OP_QUESTION)) {
            i++;
            int a = parse_expression();               /* branches may nest */
            if (a < 0) return -1;
            if (!expect_op(OP_COLON)) return -1;
            int b = parse_expression();
            if (b < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Ternary;
            n.a = (int16_t)cond;
            n.b = (int16_t)a;
            n.c = (int16_t)b;
            return add_node(n);
        }
        return cond;
    }

    int parse_or() {
        int node = parse_and();
        if (node < 0) return -1;
        while (at_name("or")) {
            i++;
            int rhs = parse_and();
            if (rhs < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Or;
            n.a = (int16_t)node;
            n.b = (int16_t)rhs;
            node = add_node(n);
            if (node < 0) return -1;
        }
        return node;
    }

    int parse_and() {
        int node = parse_cmp();
        if (node < 0) return -1;
        while (at_name("and")) {
            i++;
            int rhs = parse_cmp();
            if (rhs < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::And;
            n.a = (int16_t)node;
            n.b = (int16_t)rhs;
            node = add_node(n);
            if (node < 0) return -1;
        }
        return node;
    }

    int parse_cmp() {                    /* non-assoc: at most ONE */
        int node = parse_add();
        if (node < 0) return -1;
        const Token* t = peek();
        if (t && t->kind == Token::Kind::Op && t->op <= OP_NE) {
            uint8_t op = t->op;
            i++;
            int rhs = parse_add();
            if (rhs < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Cmp;
            n.op = op;
            n.a = (int16_t)node;
            n.b = (int16_t)rhs;
            node = add_node(n);
        }
        return node;
    }

    int parse_add() {                    /* left-assoc, no reassociation */
        int node = parse_mul();
        if (node < 0) return -1;
        while (at_op(OP_ADD) || at_op(OP_SUB)) {
            uint8_t op = toks[i].op;
            i++;
            int rhs = parse_mul();
            if (rhs < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Bin;
            n.op = op;
            n.a = (int16_t)node;
            n.b = (int16_t)rhs;
            node = add_node(n);
            if (node < 0) return -1;
        }
        return node;
    }

    int parse_mul() {
        int node = parse_unary();
        if (node < 0) return -1;
        while (at_op(OP_MUL) || at_op(OP_DIV) || at_op(OP_MOD)) {
            uint8_t op = toks[i].op;
            i++;
            int rhs = parse_unary();
            if (rhs < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Bin;
            n.op = op;
            n.a = (int16_t)node;
            n.b = (int16_t)rhs;
            node = add_node(n);
            if (node < 0) return -1;
        }
        return node;
    }

    int parse_unary() {
        if (++nest > DslExpression::kMaxNest) {
            nest--;
            return fail("expression nests too deeply");
        }
        int r = parse_unary_inner();
        nest--;
        return r;
    }

    int parse_unary_inner() {
        if (at_op(OP_SUB)) {
            i++;
            int x = parse_unary();
            if (x < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Neg;
            n.a = (int16_t)x;
            return add_node(n);
        }
        if (at_name("not")) {
            i++;
            int x = parse_unary();
            if (x < 0) return -1;
            DslExpression::Node n;
            n.kind = DslExpression::Node::Kind::Not;
            n.a = (int16_t)x;
            return add_node(n);
        }
        return parse_primary();
    }

    int parse_primary() {
        const Token* t = peek();
        if (!t) return fail("unexpected end of expression");
        i++;
        DslExpression::Node n;
        switch (t->kind) {
            case Token::Kind::Num:
                n.kind = DslExpression::Node::Kind::Num;
                n.num = t->num;
                return add_node(n);
            case Token::Kind::Str:
                n.kind = DslExpression::Node::Kind::Str;
                n.str = (int16_t)intern_str(*t);
                return add_node(n);
            case Token::Kind::Var:
                n.kind = DslExpression::Node::Kind::Var;
                n.str = (int16_t)intern_str(*t);
                return add_node(n);
            case Token::Kind::Op:
                if (t->op == OP_LPAREN) {
                    int inner = parse_expression();
                    if (inner < 0) return -1;
                    if (!expect_op(OP_RPAREN)) return -1;
                    return inner;
                }
                return fail("unexpected '%s' at position %u",
                            op_name(t->op), (unsigned)t->off);
            case Token::Kind::Name:
                break;                                /* handled below */
        }

        /* names: true/false, dt, t, ns, whitelisted function — anything
         * else is OUTSIDE the closed set, a PARSE error whether it looks
         * like a call or a bare name ([CXC-DSL-C5]). */
        std::string name = tok_text(*t);
        if (name == "true" || name == "false") {
            n.kind = DslExpression::Node::Kind::Bool;
            n.bval = (name == "true");
            return add_node(n);
        }
        if (name == "dt") {
            n.kind = DslExpression::Node::Kind::Dt;
            return add_node(n);
        }
        if (name == "t") {
            n.kind = DslExpression::Node::Kind::T;
            return add_node(n);
        }
        if (name == "ns") return parse_ns_call(t->off);
        for (int f = 0; f < kFunctionCount; f++)
            if (name == kFunctions[f].name)
                return parse_fn_call((uint8_t)f, t->off);
        return fail("unknown name '%s' at position %u — the DSL knows "
                    "only dt, t, $vars, ns('...'), true/false, and "
                    "min/max/abs/clamp/round/floor/ceil",
                    name.c_str(), (unsigned)t->off);
    }

    int parse_ns_call(uint32_t pos) {
        if (!expect_op(OP_LPAREN)) return -1;
        const Token* t = peek();
        if (!t) return fail("unexpected end of expression");
        if (t->kind != Token::Kind::Str)
            return fail("ns(...) takes exactly one string literal at "
                        "position %u — namespace names must be static "
                        "so the runtime can subscribe them",
                        (unsigned)pos);
        i++;
        if (!expect_op(OP_RPAREN)) return -1;

        std::string name = tok_text(*t);
        int idx = -1;
        for (size_t k = 0; k < ex._namespaces.size(); k++)
            if (ex._namespaces[k] == name) { idx = (int)k; break; }
        if (idx < 0) {
            ex._namespaces.push_back(name);
            idx = (int)ex._namespaces.size() - 1;
        }
        DslExpression::Node n;
        n.kind = DslExpression::Node::Kind::Ns;
        n.ns_idx = (int16_t)idx;
        return add_node(n);
    }

    int parse_fn_call(uint8_t fn, uint32_t pos) {
        if (!expect_op(OP_LPAREN)) return -1;
        int args[3] = {-1, -1, -1};
        int total = 0;
        for (;;) {
            int a = parse_expression();
            if (a < 0) return -1;
            if (total < 3) args[total] = a;
            total++;
            if (at_op(OP_COMMA)) { i++; continue; }
            break;
        }
        if (!expect_op(OP_RPAREN)) return -1;
        int arity = kFunctions[fn].arity;
        if (total != arity)
            return fail("%s() takes exactly %d argument(s), got %d at "
                        "position %u", kFunctions[fn].name, arity, total,
                        (unsigned)pos);
        DslExpression::Node n;
        n.kind = DslExpression::Node::Kind::Call;
        n.op = fn;
        n.argc = (uint8_t)arity;
        n.a = (int16_t)args[0];
        n.b = (int16_t)args[1];
        n.c = (int16_t)args[2];
        return add_node(n);
    }
};

/* ------------------------------------------------------------------ */
/* DslExpression — parse                                                */
/* ------------------------------------------------------------------ */

bool DslExpression::parse(const char* src) {
    _valid = false;
    _root = -1;
    _error.clear();
    _eval_error.clear();
    _nodes.clear();
    _strs.clear();
    _namespaces.clear();
    _ns_warned.clear();
    _warned_div0 = _warned_mod0 = false;
    _source = src ? src : "";

    if (_source.size() > kMaxSource) {
        _error = "expression too long";
        return false;
    }

    DslParser p(*this);
    if (!p.tokenize()) return false;
    if (p.toks.empty()) {                 /* dsl.py: non-empty required */
        _error = "expr: must be a non-empty string";
        return false;
    }
    int root = p.parse_expression();
    if (root < 0) return false;
    if (p.i < p.toks.size()) {            /* trailing tokens */
        const Token& t = p.toks[p.i];
        p.fail("unexpected trailing token at position %u (comparisons "
               "are non-associative; parenthesize compound conditions)",
               (unsigned)t.off);
        return false;
    }
    _root = root;
    _ns_warned.assign(_namespaces.size(), false);
    _valid = true;
    return true;
}

/* ------------------------------------------------------------------ */
/* DslExpression — evaluate (mirrors dsl.py Expression._eval)           */
/* ------------------------------------------------------------------ */

bool DslExpression::num_of(const DslValue& v, const char* what,
                           double& out) {
    if (dsl_try_number(v, out)) return true;
    _eval_error = std::string(what) +
        " needs a numeric value (expr '" + _source + "')";
    return false;
}

void DslExpression::warn_once(bool& flag, const char* what) {
    if (!flag) {
        flag = true;
        dsl_log("[CXC-DSL] %s in expr '%s'", what, _source.c_str());
    }
}

bool DslExpression::evaluate(const DslContext& ctx, DslValue& out) {
    if (!_valid) {
        _eval_error = "expression did not parse";
        return false;
    }
    _eval_error.clear();
    return eval_node(_root, ctx, out);
}

bool DslExpression::eval_node(int idx, const DslContext& ctx,
                              DslValue& out) {
    const Node& n = _nodes[idx];
    switch (n.kind) {
        case Node::Kind::Num:
            out = DslValue::number(n.num);
            return true;
        case Node::Kind::Str:
            out = DslValue::string(_strs[n.str]);
            return true;
        case Node::Kind::Bool:
            out = DslValue::boolean(n.bval);
            return true;
        case Node::Kind::Dt:
            out = DslValue::number(ctx.dt);
            return true;
        case Node::Kind::T:
            out = DslValue::number(ctx.t);
            return true;

        case Node::Kind::Var: {
            const std::string& name = _strs[n.str];
            DslRaw raw = ctx.read_var
                ? ctx.read_var(ctx.user, name.c_str())
                : DslRaw::missing();
            if (raw.kind == DslRaw::Kind::Missing) {
                _eval_error = "unknown variable $" + name +
                    " (expr '" + _source + "') — declare it in a "
                    "variables: block";
                return false;
            }
            out = dsl_coerce_input(raw);
            return true;
        }

        case Node::Kind::Ns: {
            const std::string& name = _namespaces[n.ns_idx];
            DslRaw raw = ctx.read_ns
                ? ctx.read_ns(ctx.user, name.c_str())
                : DslRaw::missing();
            if (raw.kind == DslRaw::Kind::Missing) {
                if (!_ns_warned[n.ns_idx]) {
                    _ns_warned[n.ns_idx] = true;
                    dsl_log("[CXC-DSL] namespace '%s' has no value yet "
                            "— reading 0.0 in expr '%s'",
                            name.c_str(), _source.c_str());
                }
                out = DslValue::number(0.0);
                return true;
            }
            out = dsl_coerce_input(raw);
            return true;
        }

        case Node::Kind::Neg: {
            DslValue v;
            if (!eval_node(n.a, ctx, v)) return false;
            double x;
            if (!num_of(v, "unary '-'", x)) return false;
            out = DslValue::number(-x);
            return true;
        }

        case Node::Kind::Not: {
            DslValue v;
            if (!eval_node(n.a, ctx, v)) return false;
            out = DslValue::boolean(!dsl_truthy(v));
            return true;
        }

        case Node::Kind::Bin: {
            /* operand a coerces (and can eval-error) BEFORE operand b
             * evaluates — reference order. */
            DslValue va, vb;
            double a, b;
            const char* what = (n.op == OP_ADD) ? "operator '+'"
                             : (n.op == OP_SUB) ? "operator '-'"
                             : (n.op == OP_MUL) ? "operator '*'"
                             : (n.op == OP_DIV) ? "operator '/'"
                                                : "operator '%'";
            if (!eval_node(n.a, ctx, va)) return false;
            if (!num_of(va, what, a)) return false;
            if (!eval_node(n.b, ctx, vb)) return false;
            if (!num_of(vb, what, b)) return false;
            switch (n.op) {
                case OP_ADD: out = DslValue::number(a + b); return true;
                case OP_SUB: out = DslValue::number(a - b); return true;
                case OP_MUL: out = DslValue::number(a * b); return true;
                case OP_DIV:
                    if (b == 0.0) {                   /* also -0.0 */
                        warn_once(_warned_div0,
                                  "division by zero guarded to 0.0");
                        out = DslValue::number(0.0);
                        return true;
                    }
                    out = DslValue::number(a / b);
                    return true;
                default:                              /* OP_MOD */
                    /* TRUNCATED remainder (C fmod), corpus-pinned. */
                    if (b == 0.0) {
                        warn_once(_warned_mod0,
                                  "modulo by zero guarded to 0.0");
                        out = DslValue::number(0.0);
                        return true;
                    }
                    out = DslValue::number(std::fmod(a, b));
                    return true;
            }
        }

        case Node::Kind::Cmp: {
            DslValue va, vb;
            if (!eval_node(n.a, ctx, va)) return false;
            if (!eval_node(n.b, ctx, vb)) return false;
            if (n.op == OP_EQ) {
                out = DslValue::boolean(dsl_values_equal(va, vb));
                return true;
            }
            if (n.op == OP_NE) {
                out = DslValue::boolean(!dsl_values_equal(va, vb));
                return true;
            }
            /* ordering comparators require numeric operands */
            double a, b;
            const char* what = (n.op == OP_LT) ? "comparison '<'"
                             : (n.op == OP_LE) ? "comparison '<='"
                             : (n.op == OP_GT) ? "comparison '>'"
                                               : "comparison '>='";
            if (!num_of(va, what, a)) return false;
            if (!num_of(vb, what, b)) return false;
            bool r = (n.op == OP_LT) ? (a < b)
                   : (n.op == OP_LE) ? (a <= b)
                   : (n.op == OP_GT) ? (a > b)
                                     : (a >= b);
            out = DslValue::boolean(r);
            return true;
        }

        case Node::Kind::And: {                       /* short-circuit,
                                                       * operand-returning */
            DslValue va;
            if (!eval_node(n.a, ctx, va)) return false;
            if (!dsl_truthy(va)) { out = va; return true; }
            return eval_node(n.b, ctx, out);
        }

        case Node::Kind::Or: {
            DslValue va;
            if (!eval_node(n.a, ctx, va)) return false;
            if (dsl_truthy(va)) { out = va; return true; }
            return eval_node(n.b, ctx, out);
        }

        case Node::Kind::Ternary: {                   /* lazy: the dead
                                                       * branch never runs */
            DslValue cond;
            if (!eval_node(n.a, ctx, cond)) return false;
            return eval_node(dsl_truthy(cond) ? n.b : n.c, ctx, out);
        }

        case Node::Kind::Call: {
            /* args evaluate AND coerce one at a time, left to right —
             * min('a', 1) errors before its second arg evaluates. */
            double args[3] = {0.0, 0.0, 0.0};
            const int kids[3] = {n.a, n.b, n.c};
            char what[16];
            snprintf(what, sizeof what, "%s()", kFunctions[n.op].name);
            for (int k = 0; k < n.argc; k++) {
                DslValue v;
                if (!eval_node(kids[k], ctx, v)) return false;
                if (!num_of(v, what, args[k])) return false;
            }
            double r = 0.0;
            switch (n.op) {
                case FN_MIN: r = py_min(args[0], args[1]); break;
                case FN_MAX: r = py_max(args[0], args[1]); break;
                case FN_ABS: r = std::fabs(args[0]); break;
                case FN_CLAMP:
                    /* pinned composition: min(max(x, lo), hi) —
                     * lo > hi resolves to hi (corpus-encoded) */
                    r = py_min(py_max(args[0], args[1]), args[2]);
                    break;
                case FN_ROUND: r = round_half_even(args[0]); break;
                case FN_FLOOR:
                    r = signed_zero(std::floor(args[0]), args[0]);
                    break;
                default:                              /* FN_CEIL */
                    r = signed_zero(std::ceil(args[0]), args[0]);
                    break;
            }
            out = DslValue::number(r);
            return true;
        }
    }
    _eval_error = "unreachable AST node";             /* pragma: no cover */
    return false;
}
