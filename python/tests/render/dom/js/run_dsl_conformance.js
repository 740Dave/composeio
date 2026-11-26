// Node harness that runs the SHARED DSL conformance corpus
// (render/engine/dsl_conformance/dsl_corpus.json) through the JS evaluator
// (render/dom/static/dsl.js) — the [CXC-DOM-REUSE-REIMPLEMENT-RUNTIME] /
// DOM-V13 acceptance row.  This is the JS analog of
// dsl_conformance/runner.py: the SAME loop, the SAME byte-contract
// (numbers matched by exact IEEE-754 big-endian bits), so the two tiers are
// directly comparable.
//
// Usage: node run_dsl_conformance.js <corpus.json> <dsl.js>
// Emits a JSON summary on stdout: {total, passed, failures: [...]}.

"use strict";
const fs = require("fs");
const path = require("path");

const corpusPath = process.argv[2];
const dslPath = process.argv[3];

const DSL = require(path.resolve(dslPath));
const corpus = JSON.parse(fs.readFileSync(corpusPath, "utf-8"));
if (corpus.format !== "composeux-dsl-conformance-v1") {
  console.error("unknown corpus format: " + corpus.format);
  process.exit(2);
}

function floatBitsHex(v) {
  const buf = Buffer.alloc(8);
  buf.writeDoubleBE(v, 0);      // big-endian, matching struct.pack('>d', ...)
  return buf.toString("hex");
}

// Adapter contract (identical to runner.py):
//   -> ["value", result] | ["parse_error", msg] | ["eval_error", msg]
function adapter(expr, ns, vars, dt, t) {
  let compiled;
  try {
    compiled = DSL.parse(expr);
  } catch (e) {
    if (e instanceof DSL.DslParseError) return ["parse_error", e.message];
    throw e;
  }
  try {
    const value = compiled.evaluate({ ns: ns, vars: vars, dt: dt, t: t });
    return ["value", value];
  } catch (e) {
    if (e instanceof DSL.DslEvalError) return ["eval_error", e.message];
    throw e;
  }
}

function matchExpected(expected, value) {
  const etype = expected.type;
  if (etype === "bool") {
    if (typeof value !== "boolean") return [false, "expected bool, got " + typeof value + " " + value];
    return [value === expected.value, "expected " + expected.value + ", got " + value];
  }
  if (etype === "number") {
    if (typeof value !== "number") return [false, "expected number, got " + typeof value + " " + value];
    const gotHex = floatBitsHex(value);
    return [gotHex === expected.hex,
      "expected bits " + expected.hex + " (" + expected.approx + "), got " + gotHex + " (" + value + ")"];
  }
  if (etype === "string") {
    if (typeof value !== "string") return [false, "expected string, got " + typeof value + " " + value];
    return [value === expected.value, "expected " + JSON.stringify(expected.value) + ", got " + JSON.stringify(value)];
  }
  return [false, "unknown expected type " + etype];
}

function runCase(c) {
  let kind, payload;
  try {
    const r = adapter(c.expr, c.ns || {}, c.vars || {}, Number(c.dt || 0.0), Number(c.t || 0.0));
    kind = r[0]; payload = r[1];
  } catch (e) {
    return [false, "adapter raised " + (e && e.name) + ": " + (e && e.message)];
  }
  if ("error" in c) {
    const want = c.error + "_error";
    if (kind === want) return [true, ""];
    return [false, "expected " + want + ", got " + kind + " " + JSON.stringify(payload)];
  }
  if (kind !== "value") return [false, "expected a value, got " + kind + ": " + payload];
  return matchExpected(c.expected, payload);
}

const failures = [];
for (const c of corpus.cases) {
  const [ok, detail] = runCase(c);
  if (!ok) failures.push(c.id + " [" + c.group + "] " + JSON.stringify(c.expr) + ": " + detail);
}
process.stdout.write(JSON.stringify({
  total: corpus.cases.length,
  passed: corpus.cases.length - failures.length,
  failures: failures,
}));
