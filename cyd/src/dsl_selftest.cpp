/*
 * dsl_selftest.cpp — the [SPEC:CXC-DSL] conformance-corpus harness for
 * the C++ interpreter (Pass 2b), host AND on-device.
 *
 * Re-implements runner.py's exact matching loop over the generated
 * corpus (cyd/test/dsl_corpus_generated.h — regenerate with
 * python3 python/scripts/gen_dsl_corpus_header.py):
 *
 *   number  -> compared by IEEE-754 BITS (sign of zero included)
 *   bool    -> exact type + value (a 1.0 does NOT match true)
 *   string  -> exact value
 *   error   -> the expression MUST be refused with the right kind
 *              (parse_error for grammar/sandbox, eval_error for
 *              author errors)
 *
 * Compiled ONLY when DSL_CORPUS_TEST is defined:
 *
 *   HOST (the hardware-free CI foothold — must print 226/226 and
 *   exit 0):
 *     pio run -e native_dsl && .pio/build/native_dsl/program
 *   or standalone, a la tools/yaml_min_test.cpp:
 *     clang++ -std=c++17 -DDSL_CORPUS_TEST -Isrc -Itest \
 *         -o /tmp/dsl_selftest src/dsl.cpp src/dsl_selftest.cpp
 *     /tmp/dsl_selftest
 *
 *   DEVICE (the REAL soft-double bit-exactness proof):
 *     pio run -e cyd_dsl_test -t upload && pio device monitor
 *   Serial @115200 prints one line per failure, then
 *   "N/226 conformance cases pass" and "DSL-CORPUS RESULT: PASS|FAIL".
 */

#ifdef DSL_CORPUS_TEST

#include "dsl.h"
#include "dsl_corpus_generated.h"

#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Case-table lookup: DslCorpusEntry -> DslRaw                          */
/* ------------------------------------------------------------------ */

static double bits_to_double(uint64_t bits) {
    double d;
    memcpy(&d, &bits, sizeof d);
    return d;
}

static uint64_t double_to_bits(double d) {
    uint64_t bits;
    memcpy(&bits, &d, sizeof bits);
    return bits;
}

struct CaseTables {
    const DslCorpusEntry* ns;
    int ns_count;
    const DslCorpusEntry* vars;
    int var_count;
};

static DslRaw entry_to_raw(const DslCorpusValue& v) {
    switch (v.kind) {
        case 0: return DslRaw::number(bits_to_double(v.bits));
        case 1: return DslRaw::boolean(v.b != 0);
        default: return DslRaw::string(v.s);
    }
}

static DslRaw find_entry(const DslCorpusEntry* arr, int n,
                         const char* name) {
    for (int i = 0; i < n; i++)
        if (strcmp(arr[i].name, name) == 0)
            return entry_to_raw(arr[i].value);
    return DslRaw::missing();
}

static DslRaw read_ns_cb(void* user, const char* name) {
    const CaseTables* t = (const CaseTables*)user;
    return find_entry(t->ns, t->ns_count, name);
}

static DslRaw read_var_cb(void* user, const char* name) {
    const CaseTables* t = (const CaseTables*)user;
    return find_entry(t->vars, t->var_count, name);
}

/* ------------------------------------------------------------------ */
/* One case                                                             */
/* ------------------------------------------------------------------ */

enum {                        /* must match gen_dsl_corpus_header.py */
    EXPECT_NUMBER = 0, EXPECT_BOOL = 1, EXPECT_STRING = 2,
    EXPECT_PARSE_ERROR = 3, EXPECT_EVAL_ERROR = 4
};

/* Returns true on pass; on failure writes a detail line into *detail. */
static bool run_case(const DslCorpusCase& c, char* detail, size_t dn) {
    DslExpression expr;
    bool parsed = expr.parse(c.expr);

    if (!parsed) {
        if (c.expect == EXPECT_PARSE_ERROR) return true;
        snprintf(detail, dn, "unexpected parse_error: %s", expr.error());
        return false;
    }

    CaseTables tables = {c.ns, c.ns_count, c.vars, c.var_count};
    DslContext ctx;
    ctx.read_ns = read_ns_cb;
    ctx.read_var = read_var_cb;
    ctx.user = &tables;
    ctx.dt = bits_to_double(c.dt_bits);
    ctx.t = bits_to_double(c.t_bits);

    DslValue value;
    bool ok = expr.evaluate(ctx, value);

    if (!ok) {
        if (c.expect == EXPECT_EVAL_ERROR) return true;
        snprintf(detail, dn, "unexpected eval_error: %s",
                 expr.eval_error());
        return false;
    }
    if (c.expect == EXPECT_PARSE_ERROR) {
        snprintf(detail, dn, "expected parse_error, got a value");
        return false;
    }
    if (c.expect == EXPECT_EVAL_ERROR) {
        snprintf(detail, dn, "expected eval_error, got a value");
        return false;
    }

    switch (c.expect) {
        case EXPECT_NUMBER: {
            if (value.type != DslValue::Type::Number) {
                snprintf(detail, dn, "expected number, got type %d",
                         (int)value.type);
                return false;
            }
            uint64_t got = double_to_bits(value.num);
            if (got != c.expect_bits) {
                snprintf(detail, dn,
                         "expected bits %016" PRIX64
                         ", got %016" PRIX64,
                         c.expect_bits, got);
                return false;
            }
            return true;
        }
        case EXPECT_BOOL: {
            if (value.type != DslValue::Type::Bool) {
                snprintf(detail, dn, "expected bool, got type %d",
                         (int)value.type);
                return false;
            }
            if (value.b != (c.expect_bool != 0)) {
                snprintf(detail, dn, "expected %s, got %s",
                         c.expect_bool ? "true" : "false",
                         value.b ? "true" : "false");
                return false;
            }
            return true;
        }
        default: {                                    /* EXPECT_STRING */
            if (value.type != DslValue::Type::String) {
                snprintf(detail, dn, "expected string, got type %d",
                         (int)value.type);
                return false;
            }
            if (value.str != c.expect_str) {
                snprintf(detail, dn, "expected '%s', got '%s'",
                         c.expect_str, value.str.c_str());
                return false;
            }
            return true;
        }
    }
}

/* ------------------------------------------------------------------ */
/* The full corpus                                                      */
/* ------------------------------------------------------------------ */

extern "C" int dsl_selftest_run(void) {
    int failures = 0;
    char detail[256];
    for (int i = 0; i < kDslCorpusCaseCount; i++) {
        const DslCorpusCase& c = kDslCorpusCases[i];
        if (!run_case(c, detail, sizeof detail)) {
            failures++;
            printf("FAIL %s [%s] '%s': %s\n",
                   c.id, c.group, c.expr, detail);
        }
    }
    printf("%d/%d conformance cases pass\n",
           kDslCorpusCaseCount - failures, kDslCorpusCaseCount);
    return failures ? 1 : 0;
}

/* ------------------------------------------------------------------ */
/* Entry points                                                         */
/* ------------------------------------------------------------------ */

#ifdef ESP_PLATFORM

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"

/* app_main's default stack is 3.5 KB — run the corpus in a dedicated
 * task with headroom for the (bounded) parser/evaluator recursion. */
static void corpus_task(void*) {
    printf("\n[CXC-DSL] Pass 2b on-device conformance run "
           "(%d cases, float64 soft-double)\n", kDslCorpusCaseCount);
    int64_t t0 = esp_timer_get_time();
    int rc = dsl_selftest_run();
    int64_t t1 = esp_timer_get_time();
    printf("elapsed: %lld us (%lld us/case)\n",
           (long long)(t1 - t0),
           (long long)((t1 - t0) / kDslCorpusCaseCount));
    printf("DSL-CORPUS RESULT: %s\n", rc == 0 ? "PASS" : "FAIL");
    for (;;) vTaskDelay(pdMS_TO_TICKS(10000));
}

extern "C" void app_main(void) {
    vTaskDelay(pdMS_TO_TICKS(300));       /* let the UART settle */
    /* 48 KB: covers the worst-case bounded parser/evaluator recursion
     * (see dsl.h hard-bounds note) with margin. */
    xTaskCreate(corpus_task, "dsl_corpus", 49152, nullptr, 5, nullptr);
}

#else /* host */

int main(void) { return dsl_selftest_run(); }

#endif /* ESP_PLATFORM */

#endif /* DSL_CORPUS_TEST */
