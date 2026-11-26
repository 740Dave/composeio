// Machine test harness for the ComposeUX DOM render backend.
//
// Exercises the DOM Canvas subclass element-map + stack semantics (DOM-V5),
// the control->publish re-realizations (DOM-V6), publish-mode-by-form-membership
// (DOM-V7), the FORM primitive buffer/validate/atomic-publish/per-field-errors
// (DOM-V8/DOM-V9 with a stub validator — the REAL pydantic path is proven in
// test_server_validation.py), two-way binding precedence (DOM-V10), native
// semantic realization (the machine slice of DOM-V1), and realization-split /
// no-CanvasKit-for-controls (DOM-V2/DOM-V4).
//
// Runs on the dependency-free DOM shim (dom_shim.js): NO layout, so text
// metrics + real accessibility/screen-reader are GLASS items, not asserted
// here.  Emits {total, passed, failures} JSON on stdout.

"use strict";
const path = require("path");
const shim = require("./dom_shim.js");
const { Document, fire } = shim;

const STATIC = path.resolve(__dirname, "../../../../composeio/render/dom/static");
const DomCanvas = require(path.join(STATIC, "dom_canvas.js"));
const B = require(path.join(STATIC, "bindings.js"));
const Controls = require(path.join(STATIC, "controls.js"));
const Runtime = require(path.join(STATIC, "runtime.js")).Runtime;
const WsClient = require(path.join(STATIC, "ws_client.js"));

// A scripted fake WebSocket for ws_client tests (no network, no npm 'ws').
function fakeSocketFactory() {
  let sock = null;
  function FakeWS(url) {
    this.url = url; this.readyState = 0; this.sent = [];
    this.onopen = null; this.onmessage = null; this.onclose = null; this.onerror = null;
    sock = this;
  }
  FakeWS.prototype.send = function (frame) { this.sent.push(JSON.parse(frame)); };
  FakeWS.prototype.open = function () { this.readyState = 1; if (this.onopen) this.onopen(); };
  FakeWS.prototype.recv = function (obj) { if (this.onmessage) this.onmessage({ data: JSON.stringify(obj) }); };
  return { FakeWS: FakeWS, current: () => sock };
}

// -- tiny test framework ----------------------------------------------------
const failures = [];
let total = 0;
async function test(name, fn) {
  total++;
  try { await fn(); } catch (e) { failures.push(name + ": " + (e && e.message ? e.message : e)); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function eq(a, b, msg) { if (a !== b) throw new Error((msg || "eq") + " — expected " + JSON.stringify(b) + ", got " + JSON.stringify(a)); }

// -- a fake bus (the WS transport's role, for machine tests) ----------------
function matchPat(pattern, ns) {
  if (pattern === ns || pattern === "*" || pattern === "#") return true;
  if (pattern.endsWith("/*") || pattern.endsWith("/#")) return ns.startsWith(pattern.slice(0, -1));
  return false;
}
function fakeWs() {
  const store = {}, subs = [], published = [];
  const ws = {
    _validateImpl: null,
    store: () => store,
    isBound: () => true,
    subscribe(pattern, cb) { subs.push({ pattern, cb }); return ws; },
    publish(ns, val) {
      store[ns] = val; published.push({ ns, val });
      for (const s of subs) if (matchPat(s.pattern, ns)) s.cb(ns, val, {});
      return true;
    },
    validateConfig(target, config) {
      return Promise.resolve(ws._validateImpl ? ws._validateImpl(target, config) : { ok: true });
    },
    inject(ns, val) { store[ns] = val; for (const s of subs) if (matchPat(s.pattern, ns)) s.cb(ns, val, {}); },
    published, subs,
  };
  return ws;
}
function build(scene) {
  const doc = new Document();
  const ws = fakeWs();
  const rt = new Runtime({ document: doc, parent: doc.body, ws: ws });
  rt.mount(scene);
  return { doc, ws, rt };
}

async function main() {
  // ===== DOM-V5: DOM Canvas subclass element map + stack semantics =====
  await test("V5 element map: fill/stroke rect, circle, arc->svg", () => {
    const doc = new Document();
    const c = new DomCanvas({ document: doc, parent: doc.body });
    c.create_surface(200, 100);
    c.set_fill_color(255, 0, 0, 255); c.fill_rect(10, 10, 50, 20);
    const fr = c.root.querySelectorAll('[data-prim="fill_rect"]');
    eq(fr.length, 1, "one fill_rect");
    eq(fr[0].tagName, "div", "fill_rect is a div");
    assert(/rgba\(255,0,0/.test(fr[0].style.background), "fill_rect bg color");
    eq(fr[0].style.width, "50px"); eq(fr[0].style.height, "20px");
    c.set_stroke_color(0, 0, 255, 255); c.set_stroke_width(3); c.stroke_rect(0, 0, 10, 10);
    const sr = c.root.querySelector('[data-prim="stroke_rect"]');
    assert(/3px solid rgba\(0,0,255/.test(sr.style.border), "stroke_rect border");
    c.fill_circle(5, 5, 5);
    eq(c.root.querySelector('[data-prim="fill_circle"]').style.borderRadius, "50%");
    c.fill_arc(0, 0, 10, 0, 1);
    const arc = c.root.querySelector('[data-prim="fill_arc"]');
    assert(arc && arc.tagName === "svg", "arc is inline svg");
    assert(arc.querySelector("path"), "arc has a path child");
    c.draw_line(0, 0, 10, 10);
    assert(c.root.querySelector('[data-prim="draw_line"] line'), "line is svg line");
    c.draw_text("Hi", 5, 20, "left");
    const txt = c.root.querySelector('[data-prim="draw_text"]');
    assert(txt && txt.tagName === "span", "text is a span");
    eq(txt.textContent, "Hi");
  });

  await test("V5 stack semantics: two save/restore -> two DIFFERENT fills", () => {
    const doc = new Document();
    const c = new DomCanvas({ document: doc, parent: doc.body });
    c.create_surface(50, 50);
    c.save(); c.set_fill_color(10, 20, 30, 255); c.fill_rect(0, 0, 5, 5); c.restore();
    c.save(); c.set_fill_color(40, 50, 60, 255); c.fill_rect(0, 0, 5, 5); c.restore();
    const fills = c.root.querySelectorAll('[data-prim="fill_rect"]');
    eq(fills.length, 2);
    assert(/10,20,30/.test(fills[0].style.background), "first fill keeps its own color");
    assert(/40,50,60/.test(fills[1].style.background), "second fill differs — stack, not global");
  });

  await test("V5 clip: subsequent draw is nested inside an overflow:hidden box", () => {
    const doc = new Document();
    const c = new DomCanvas({ document: doc, parent: doc.body });
    c.create_surface(50, 50);
    c.clip_rect(0, 0, 20, 20);
    const clip = c.root.querySelector('[data-prim="clip_rect"]');
    eq(clip.style.overflow, "hidden", "clip is overflow:hidden");
    c.fill_rect(0, 0, 100, 100);
    assert(clip.querySelector('[data-prim="fill_rect"]'), "drawn element is inside the clip box");
  });

  await test("V5 measure_text: headless fallback flags approximate (glass covers real)", () => {
    const doc = new Document();
    const c = new DomCanvas({ document: doc, parent: doc.body });
    c.create_surface(50, 50);
    c.set_font("sans-serif", 20, "regular");
    const m = c.measure_text("abcd");
    assert(m.width > 0, "fallback width is positive");
    eq(m.approximate, true, "flagged approximate in a layout-less DOM");
  });

  // ===== DOM-V6: control -> publish re-realization =====
  await test("V6 emit_button publishes the SAME emits list on click, live-resolved", () => {
    const scene = { root: { type: "column", children: [
      { type: "emit_button", id: "b", label: "Go", emits: [
        { namespace: "cmd/fire", value: [1, 2] },
        { namespace: "cmd/mode", value: { namespace: "live/mode", default: "x" } },
      ] } ] } };
    const { doc, ws } = build(scene);
    ws.inject("live/mode", "hot");
    const btn = doc.body.querySelector("button.compose-emit-button");
    assert(btn && btn.tagName === "button", "emit_button is a <button>");
    fire(btn, "click");
    eq(ws.published.length, 2, "two emits published in order");
    eq(ws.published[0].ns, "cmd/fire"); eq(JSON.stringify(ws.published[0].val), "[1,2]");
    eq(ws.published[1].ns, "cmd/mode"); eq(ws.published[1].val, "hot", "embedded binding resolved to live value");
  });

  await test("V6 radio change publishes selected index to index_namespace", () => {
    const scene = { root: { type: "column", children: [
      { type: "radio_button_group", id: "r", labels: ["A", "B", "C"], index_namespace: "sel/idx" } ] } };
    const { doc, ws } = build(scene);
    const radios = doc.body.querySelectorAll('[data-widget="r"] input');
    eq(radios.length, 3, "three radio inputs");
    eq(radios[1].type, "radio");
    radios[1].checked = true; fire(radios[1], "change");
    eq(ws.published.length, 1); eq(ws.published[0].ns, "sel/idx"); eq(ws.published[0].val, 1);
  });

  await test("V6 negative-control: two emit_buttons produce DIFFERENT payloads", () => {
    const scene = { root: { type: "column", children: [
      { type: "emit_button", id: "b1", emits: [{ namespace: "n/a", value: "A" }] },
      { type: "emit_button", id: "b2", emits: [{ namespace: "n/b", value: "B" }] } ] } };
    const { doc, ws } = build(scene);
    fire(doc.body.querySelector('[data-widget="b1"]'), "click");
    fire(doc.body.querySelector('[data-widget="b2"]'), "click");
    eq(ws.published[0].val, "A"); eq(ws.published[1].val, "B");
  });

  // ===== DOM-V7: publish mode by form-container membership =====
  await test("V7 identical text control: live outside a form, buffered inside", () => {
    const scene = { root: { type: "column", children: [
      { type: "text", id: "live1", field: "name", namespace: "cfg/name", value: { namespace: "cfg/name" } },
      { type: "form", id: "f", target: "plugin", namespace: "config/plugin", children: [
        { type: "text", id: "fname", field: "name", value: "init" } ] } ] } };
    const { doc, ws, rt } = build(scene);
    // outside a form -> immediate publish on commit
    const live = doc.body.querySelector('[data-widget="live1"] input');
    live.value = "hello"; fire(live, "change");
    eq(ws.published.length, 1, "live control publishes immediately");
    eq(ws.published[0].ns, "cfg/name"); eq(ws.published[0].val, "hello");
    // inside a form -> ZERO bus traffic, buffer updated
    const before = ws.published.length;
    const fin = doc.body.querySelector('[data-widget="fname"] input');
    fin.value = "edited"; fire(fin, "change");
    eq(ws.published.length, before, "form field produced ZERO publishes");
    eq(rt.forms[0].buffer.name, "edited", "form field wrote the buffer instead");
  });

  // ===== DOM-V8/DOM-V9: FORM buffer / validate / atomic publish / errors =====
  await test("V8 unsubmitted edits -> zero publishes; valid submit -> ONE atomic publish", async () => {
    const scene = { root: { type: "form", id: "f", target: "plugin", namespace: "config/plugin", children: [
      { type: "text", id: "name", field: "name", value: "" },
      { type: "text", id: "host", field: "host", value: "" },
      { type: "number", id: "port", field: "port", value: "" } ] } };
    const { doc, ws, rt } = build(scene);
    const setF = (w, v) => { const i = doc.body.querySelector('[data-widget="' + w + '"] input'); i.value = String(v); fire(i, "change"); };
    setF("name", "svc"); setF("host", "198.51.100.10"); setF("port", 8080);
    eq(ws.published.length, 0, "editing three fields without submit -> ZERO publishes");
    eq(rt.forms[0].buffer.port, 8080);
    ws._validateImpl = () => ({ ok: true });
    const res = await rt.forms[0].submit();
    assert(res.ok, "valid submit succeeded");
    eq(ws.published.length, 1, "exactly ONE atomic publish for the whole config");
    eq(ws.published[0].ns, "config/plugin");
    eq(JSON.stringify(ws.published[0].val), JSON.stringify({ name: "svc", host: "198.51.100.10", port: 8080 }));
  });

  await test("V8/V9 invalid submit -> zero publishes, per-field error, buffer retained", async () => {
    // `port` is a text control holding a raw string the SERVER's pydantic model
    // rejects (an int field fed a non-integer) — the cross-field / non-DOM-
    // expressible constraint case [CXC-DOM-SERVER-VALIDATION] targets.
    const scene = { root: { type: "form", id: "f", target: "plugin", namespace: "config/plugin", children: [
      { type: "text", id: "name", field: "name", value: "" },
      { type: "text", id: "port", field: "port", value: "" } ] } };
    const { doc, ws, rt } = build(scene);
    const setF = (w, v) => { const i = doc.body.querySelector('[data-widget="' + w + '"] input'); i.value = String(v); fire(i, "change"); };
    setF("name", "svc"); setF("port", "not-a-number");
    ws._validateImpl = () => ({ ok: false, errors: ["config:port: value is not a valid integer"] });
    const res = await rt.forms[0].submit();
    assert(!res.ok, "invalid submit reported failure");
    eq(ws.published.length, 0, "invalid submit publishes NOTHING for any field");
    const errEl = rt.forms[0].el.querySelector('[data-field="port"] [data-role="error"]');
    assert(errEl && /valid integer/.test(errEl.textContent), "per-field error on the SPECIFIC field");
    eq(rt.forms[0].buffer.name, "svc", "other field's edited value retained in buffer");
    eq(rt.forms[0].buffer.port, "not-a-number", "invalid field's value retained (not reverted)");
    // the valid field carries no error
    const nameErr = rt.forms[0].el.querySelector('[data-field="name"] [data-role="error"]');
    eq((nameErr && nameErr.textContent) || "", "", "valid field shows no error");
  });

  await test("V8 nested form is a LOUD error, never silent", () => {
    const scene = { root: { type: "form", id: "outer", children: [
      { type: "form", id: "inner", children: [] } ] } };
    let threw = false;
    try { build(scene); } catch (e) { threw = /nested form/.test(e.message); }
    assert(threw, "nested form throws a loud, named error");
  });

  // ===== DOM-V10: two-way binding precedence =====
  await test("V10 live control display tracks the bus; mid-edit form field does not", () => {
    const scene = { root: { type: "column", children: [
      { type: "text", id: "lv", field: "v", namespace: "x/val", value: { namespace: "x/val" } },
      { type: "form", id: "f", target: "p", children: [
        { type: "text", id: "fv", field: "v", value: { namespace: "x/val2" } } ] } ] } };
    const { doc, ws } = build(scene);
    const live = doc.body.querySelector('[data-widget="lv"] input');
    ws.inject("x/val", "42");
    eq(live.value, "42", "live control display updates from the bus");
    const fv = doc.body.querySelector('[data-widget="fv"] input');
    fire(fv, "focus");                       // begin an in-progress edit
    fv.value = "user-typing";
    ws.inject("x/val2", "99");
    assert(fv.value !== "99", "mid-edit form field is NOT overwritten by the bus");
    eq(fv.value, "user-typing", "the user's uncommitted edit wins");
  });

  // ===== DOM-V1 (machine slice): native semantic realization =====
  await test("V1 controls realize as NATIVE semantic elements with labels", () => {
    const scene = { root: { type: "form", id: "f", target: "p", children: [
      { type: "text", id: "t", field: "t", label: "Name" },
      { type: "checkbox", id: "c", field: "c", label: "Enabled" },
      { type: "select", id: "s", field: "s", label: "Mode", options: ["a", "b"] },
      { type: "textarea", id: "ta", field: "ta", label: "Notes" },
      { type: "date", id: "d", field: "d", label: "When" } ] } };
    const { doc } = build(scene);
    const text = doc.body.querySelector('[data-widget="t"] input'); eq(text.type, "text");
    eq(doc.body.querySelector('[data-widget="t"] label').getAttribute("for"), text.id, "label associated to input");
    eq(doc.body.querySelector('[data-widget="c"] input').type, "checkbox");
    assert(doc.body.querySelector('[data-widget="s"] select'), "select is a <select>");
    eq(doc.body.querySelectorAll('[data-widget="s"] option').length, 2);
    assert(doc.body.querySelector('[data-widget="ta"] textarea'), "textarea is a <textarea>");
    eq(doc.body.querySelector('[data-widget="d"] input').type, "date");
  });

  await test("V1 read-only table is a <table>; read-only tree is role=tree", () => {
    const scene = { root: { type: "column", children: [
      { type: "table", id: "tbl", columns: ["k", "v"], rows: [{ k: "a", v: 1 }, { k: "b", v: 2 }] },
      { type: "tree", id: "tr", label: "namespaces", data: { app: { fuel: 40, charge: 60 } } } ] } };
    const { doc } = build(scene);
    const table = doc.body.querySelector('[data-widget="tbl"] table') || doc.body.querySelector("table.compose-table");
    assert(table, "table is a real <table>");
    eq(doc.body.querySelectorAll("table.compose-table th").length, 2);
    eq(doc.body.querySelectorAll("table.compose-table tbody tr").length, 2);
    const tree = doc.body.querySelector('[role="tree"]');
    assert(tree, "tree has role=tree");
    assert(doc.body.querySelectorAll('[role="treeitem"]').length >= 2, "tree items rendered");
  });

  // ===== DOM-V2/DOM-V4: realization split, no CanvasKit for controls =====
  await test("V4 registry types dispatch native; visuals use the primitive path; unknown -> placeholder", () => {
    const scene = { root: { type: "column", children: [
      { type: "emit_button", id: "b", emits: [] },
      { type: "rect", id: "rc", w: 40, h: 20, fill_color: "#204080" },
      { type: "gauge", id: "g", value: 50 } ] } };
    const { doc, rt } = build(scene);
    assert(doc.body.querySelector('[data-widget="b"]').tagName === "button" ||
      doc.body.querySelector('[data-widget="b"] button') || doc.body.querySelector("button"), "control -> native");
    assert(doc.body.querySelector('[data-widget="rc"] [data-prim="fill_rect"]'), "visual -> DomCanvas primitive path");
    const ph = doc.body.querySelector('[data-role="placeholder"]');
    assert(ph && /gauge/.test(ph.textContent), "unknown graphics widget -> NAMED placeholder, not silent");
    eq(rt.canvasKitRequested, false, "no CanvasKit requested for this scene");
  });

  await test("V2 control-only scene: zero placeholders, zero CanvasKit", () => {
    const scene = { root: { type: "form", id: "f", target: "p", children: [
      { type: "text", id: "t", field: "t" }, { type: "checkbox", id: "c", field: "c" } ] } };
    const { rt } = build(scene);
    eq(rt.placeholders.length, 0, "no placeholders in a control-only scene");
    eq(rt.canvasKitRequested, false, "structural laziness: no CanvasKit path reached");
  });

  // ===== [CXC-DOM-TRANSPORT] ws_client lifecycle, envelope, req/res =====
  await test("WS publish is gated until BOUND, then rides the faithful envelope", () => {
    const f = fakeSocketFactory();
    const c = new WsClient({ url: "ws://x", tenant: "haven", source: "test", WebSocketImpl: f.FakeWS });
    c.connect();
    const sock = f.current();
    sock.open();                             // onopen -> selectTenant
    assert(sock.sent.some((m) => m.ws === "select" && m.tenant === "haven"), "select frame sent on open");
    // before BOUND: publish refused ([CXC-DOM-BOUNDARIES] cond 12)
    eq(c.publish("a/b", 5), false, "publish refused before BOUND");
    assert(!sock.sent.some((m) => m.ns === "a/b"), "no envelope sent before BOUND");
    sock.recv({ ws: "bound", tenant: "haven" });
    assert(c.isBound(), "BOUND after bound frame");
    eq(c.publish("a/b", 5), true, "publish accepted after BOUND");
    const env = sock.sent.filter((m) => m.ns === "a/b").pop();
    assert(env, "envelope sent");
    eq(env.type, "state"); eq(env.src, "test"); eq(env.val, 5);
    assert("ns" in env && "ts" in env && "scope" in env && "seq" in env, "faithful BusMessage keys");
  });

  await test("WS subscribe dispatches inbound bus frames to the callback", () => {
    const f = fakeSocketFactory();
    const c = new WsClient({ url: "ws://x", tenant: "haven", WebSocketImpl: f.FakeWS });
    let got = null;
    c.subscribe("x/val", (ns, v) => { got = [ns, v]; });
    c.connect(); const sock = f.current(); sock.open(); sock.recv({ ws: "bound", tenant: "haven" });
    sock.recv({ ns: "x/val", type: "state", src: "peer", ts: 0, scope: "auto", seq: 0, val: 7 });
    assert(got && got[0] === "x/val" && got[1] === 7, "subscriber saw the published value");
    eq(c.latest("x/val"), 7, "retained locally for two-way binding");
  });

  await test("WS request/response correlates by req_id (server validation ride)", async () => {
    const f = fakeSocketFactory();
    const c = new WsClient({ url: "ws://x", tenant: "haven", WebSocketImpl: f.FakeWS });
    c.connect(); const sock = f.current(); sock.open(); sock.recv({ ws: "bound", tenant: "haven" });
    const p = c.validateConfig("demo", { name: "svc", port: 8080 });
    const req = sock.sent.filter((m) => m.ws === "validate_config").pop();
    assert(req && req.req_id, "validate_config frame carries a req_id");
    sock.recv({ ws: "config_result", req_id: req.req_id, ok: true, errors: [] });
    const res = await p;
    assert(res.ok === true, "the awaiting promise resolved with the server verdict");
  });

  process.stdout.write(JSON.stringify({ total, passed: total - failures.length, failures }));
}

main();
