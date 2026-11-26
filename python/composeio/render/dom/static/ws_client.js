// ComposeUX browser bus client over the WS connector ([CXC-DOM-TRANSPORT]).
//
// This backend's ONLY transport is the WS connector specified at [SPEC:CXC-WS]
// — never EventSource/SSE (the legacy WebBusModule bridge stays read-only and
// unextended, [CXC-DOM-TRANSPORT] / DOM-V12).  Bus traffic rides the FAITHFUL
// BusMessage envelope verbatim ([CXC-WS-WIRE-FORMAT]): the bare dict
// {ns, type, src, ts, scope, seq, val?, evt?, data?}, never a simplified or
// DOM-specific shape.
//
// Lifecycle ([CXC-WS-LIFECYCLE]): connect -> PRE-BINDING -> select-tenant ->
// BOUND, with retained-state replay on subscribe ([CXC-WS-RETAINED-REPLAY]).
// The lifecycle control frames carry an explicit {ws: <verb>} discriminator (no
// ns/type), keeping bus envelopes faithful and the control channel separate.
//
// CONTROL VOCABULARY — CANONICAL, matching transport_ws.py (the AUTHORITATIVE
// [SPEC:CXC-WS] connector), reconciled 2026-08-25 from this backend's earlier
// provisional {op: ...} names:
//   client -> server (control): {ws:"list"} | {ws:"select",tenant} |
//     {ws:"switch",tenant} | {ws:"subscribe",patterns:[...]}   (patterns is the
//     FULL declared SET each time — the server REPLACES, not appends)
//   server -> client (control): {ws:"tenants",tenants:[...]} | {ws:"bound",tenant}
//     | {ws:"prebinding"} | {ws:"subscribed",patterns,enforcement} |
//     {ws:"error",code,message}
//   bus frames (both directions, NO ws key): the faithful BusMessage envelope
//     {ns,type,src,ts,scope,seq,val?,evt?,data?}; retained replay adds retained:true.
//
// ⚠ SEAM NOTE: validate_config / config_result is a DOM-LAYER extension, NOT a
// verb transport_ws.py implements — the real ComposeServer WS listener answers
// it with {ws:"error",code:"unknown_verb"} (loud, in-band). It rides the SAME
// {ws:...} discriminator + req_id correlation so it is a clean unknown-verb, not
// a misparse; the demo server (dom/server.py) is where it is actually served.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ComposeWsClient = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const PRE_BINDING = "PRE-BINDING";
  const BOUND = "BOUND";

  class WsClient {
    // opts: {url, tenant, WebSocketImpl, source}
    constructor(opts) {
      opts = opts || {};
      this.url = opts.url || null;
      this.tenant = opts.tenant || null;
      this.source = opts.source || ("dom-" + Math.random().toString(36).slice(2, 8));
      this._WS = opts.WebSocketImpl || (typeof WebSocket !== "undefined" ? WebSocket : null);
      this.state = PRE_BINDING;
      this.ws = null;
      this._subs = [];                 // {pattern, cb}
      this._latest = {};               // namespace -> last value (two-way + replay)
      this._listeners = { bound: [], state: [], open: [], close: [] };
      this._outbox = [];               // frames queued until OPEN
      this._reqs = {};                 // req_id -> {resolve, reject}
      this._reqSeq = 0;
    }

    on(event, cb) { (this._listeners[event] || (this._listeners[event] = [])).push(cb); return this; }
    _emit(event, arg) { (this._listeners[event] || []).forEach((cb) => cb(arg)); }

    latest(namespace) { return this._latest[namespace]; }
    store() { return this._latest; }
    isBound() { return this.state === BOUND; }

    connect(url) {
      if (url) this.url = url;
      if (!this._WS) throw new Error("no WebSocket implementation available");
      this.ws = new this._WS(this.url);
      this.ws.onopen = () => {
        this._emit("open");
        this._flush();
        if (this.tenant) this.selectTenant(this.tenant);
      };
      this.ws.onmessage = (ev) => this._onFrame(ev.data);
      this.ws.onclose = () => { this.state = PRE_BINDING; this._emit("state", this.state); this._emit("close"); };
      this.ws.onerror = () => { /* the browser WS reconnect story is the app's */ };
      return this;
    }

    _send(obj) {
      const frame = JSON.stringify(obj);
      if (this.ws && this.ws.readyState === 1) this.ws.send(frame);
      else this._outbox.push(frame);
    }
    _flush() {
      if (!this.ws || this.ws.readyState !== 1) return;
      const q = this._outbox; this._outbox = [];
      for (const f of q) this.ws.send(f);
    }

    // -- lifecycle --------------------------------------------------------
    selectTenant(tenant) {
      this.tenant = tenant;
      this._send({ ws: "select", tenant: tenant });
      // (re)declare our subscriptions against the newly selected tenant so the
      // BOUND-entry retained replay covers them ([CXC-WS-LIFECYCLE] step order:
      // subscribe-before-replay).  The server REPLACES its declared set, so we
      // always send the FULL set, never one pattern per frame.
      this._sendSubscription();
    }

    // The declared subscription SET, deduplicated — transport_ws.py's
    // set_subscription() REPLACES the peer's whole set on each frame.
    _sendSubscription() {
      const patterns = [];
      for (const s of this._subs) if (patterns.indexOf(s.pattern) < 0) patterns.push(s.pattern);
      this._send({ ws: "subscribe", patterns: patterns });
    }

    subscribe(pattern, cb) {
      this._subs.push({ pattern: pattern, cb: cb });
      if (this.state === BOUND) {
        // send the FULL declared set (the server replaces, not appends); the
        // server replays any NEWLY-matched retained namespace ([CXC-WS-RETAINED-
        // REPLAY]).
        this._sendSubscription();
        // also replay any retained value we ALREADY hold for an exact-ns sub.
        if (Object.prototype.hasOwnProperty.call(this._latest, pattern)) {
          cb(pattern, this._latest[pattern], { retained: true });
        }
      }
      return this;
    }

    // -- publish ([CXC-WS-WIRE-FORMAT] verbatim BusMessage envelope) ------
    publish(namespace, value, opts) {
      opts = opts || {};
      if (this.state !== BOUND) {
        // [CXC-DOM-BOUNDARIES] cond 12: no publish before BOUND.
        return false;
      }
      const msg = {
        ns: namespace,
        // FAITHFUL wire type ([CXC-WS-WIRE-FORMAT]): the frozen BusMessage table
        // (transport.py MessageType) allocates exactly "state"/"event"; from_json
        // REJECTS anything else with UnknownMessageTypeName, so a state publish
        // MUST carry "state" (reconciled 2026-08-25 from a DOM-invented
        // "state_change", which the real listener silently refused).
        type: opts.type || "state",
        src: opts.source || this.source,
        ts: Date.now() / 1000,
        scope: opts.scope || "auto",
        seq: 0,
      };
      if (value !== null && value !== undefined) msg.val = value;
      if (opts.event_type) msg.evt = opts.event_type;
      if (opts.data) msg.data = opts.data;
      this._latest[namespace] = value;         // optimistic local echo
      this._send(msg);
      return true;
    }

    // -- request/response ([CXC-DOM-SERVER-VALIDATION] rides this WS) ------
    // A correlated round-trip over the SAME connection: the form submits its
    // buffered config and awaits the server's real pydantic verdict.  The
    // server echoes `req_id` so the matching promise resolves.
    request(verb, payload) {
      const id = "r" + (++this._reqSeq);
      // {ws: <verb>} discriminator + req_id correlation, same channel as the
      // lifecycle frames.  (validate_config is a DOM-layer verb — see the SEAM
      // NOTE at the top of this file.)
      const frame = Object.assign({ ws: verb, req_id: id }, payload || {});
      const self = this;
      return new Promise(function (resolve, reject) {
        self._reqs[id] = { resolve: resolve, reject: reject };
        self._send(frame);
      });
    }
    // Convenience: validate a form's buffered config server-side.
    validateConfig(target, config) {
      return this.request("validate_config", { target: target, config: config });
    }

    // -- inbound ----------------------------------------------------------
    _onFrame(raw) {
      let d;
      try { d = typeof raw === "string" ? JSON.parse(raw) : raw; } catch (e) { return; }
      if (d && d.ws !== undefined) return this._onControl(d);
      if (d && typeof d.ns === "string") return this._onBus(d);
    }
    _onControl(d) {
      // a correlated response (e.g. config_result) resolves its awaiting promise.
      if (d.req_id && this._reqs[d.req_id]) {
        const pend = this._reqs[d.req_id];
        delete this._reqs[d.req_id];
        pend.resolve(d);
        return;
      }
      switch (d.ws) {
        case "bound":
          this.state = BOUND;
          this._emit("state", this.state);
          this._emit("bound", d.tenant || this.tenant);
          // ensure the full declared subscription set is live post-BOUND.
          this._sendSubscription();
          break;
        case "prebinding":
          this.state = PRE_BINDING;
          this._emit("state", this.state);
          break;
        case "tenants":
          this._emit("tenants", d.tenants || []);
          break;
        case "subscribed":
          this._emit("subscribed", d.patterns || []);
          break;
        case "error":
          this._emit("error", { code: d.code, message: d.message });
          break;
        default:
          break;
      }
    }
    _matches(pattern, ns) {
      if (pattern === ns) return true;
      if (pattern === "*" || pattern === "#") return true;
      if (pattern.endsWith("/*")) return ns.startsWith(pattern.slice(0, -1));
      if (pattern.endsWith("/#")) return ns.startsWith(pattern.slice(0, -1));
      return false;
    }
    _onBus(d) {
      const ns = d.ns;
      const val = Object.prototype.hasOwnProperty.call(d, "val") ? d.val : null;
      this._latest[ns] = val;
      const meta = { retained: !!d.retained, source: d.src, type: d.type, raw: d };
      for (const s of this._subs) {
        if (this._matches(s.pattern, ns)) s.cb(ns, val, meta);
      }
    }
  }

  WsClient.PRE_BINDING = PRE_BINDING;
  WsClient.BOUND = BOUND;
  return WsClient;
});
