// ComposeUX FORM primitive ([CXC-DOM-FORM-PRIMITIVE], [CXC-DOM-PUBLISH-MODES],
// [CXC-DOM-SERVER-VALIDATION]).
//
// A NEW container widget that generalizes text_input's single-field commit-
// buffer model to MULTIPLE fields committed together as one unit:
//   (1) owns a LOCAL BUFFER keyed by each child control's field name,
//       initialized from each control's current bound value at mount;
//   (2) a child control's change writes ONLY that buffer, never the bus
//       ([CXC-DOM-PUBLISH-MODES]: form membership selects buffer mode
//       STRUCTURALLY, not by a per-control flag);
//   (3) on SUBMIT, sends the WHOLE buffered object to the server for the REAL
//       pydantic Config validation ([CXC-DOM-SERVER-VALIDATION] — never a
//       client-side JS reimpl) over the SAME WS connection;
//   (4) publishes the validated config ATOMICALLY (one bus write) ONLY after
//       the server confirms success;
//   (5) on failure: publishes NOTHING, keeps every edited value in the buffer,
//       and surfaces the returned per-field `config:<field>: <msg>` errors next
//       to their fields.
//
// v1 refuses a nested form ([CXC-DOM-BOUNDARIES] cond 11) with a loud error.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ComposeForm = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // Parse a `config:<field>: <message>` taxonomy string ([CXC-CSL-ERROR-TAXONOMY],
  // reused verbatim) into {field, msg}.  A non-`config:` string surfaces
  // form-wide.
  function parseConfigError(item) {
    if (item && typeof item === "object" && "field" in item) return { field: item.field, msg: item.msg || "" };
    const s = String(item);
    const m = /^config:([^:]+):\s*(.*)$/.exec(s);
    if (m) return { field: m[1], msg: m[2] };
    return { field: null, msg: s };
  }

  class Form {
    // host: {doc, ws, publishLive(ns,value), rootErrorSink?}
    constructor(host, spec) {
      this.host = host;
      this.spec = spec || {};
      this.buffer = {};                 // field -> value (local, unpublished)
      this.fields = {};                 // field -> control host (for errors)
      this.target = this.spec.target || this.spec.id || "config";
      this.namespace = this.spec.namespace || ("config/" + this.target);
      this._submitted = null;
      this.el = host.doc.createElement("form");
      this.el.className = "compose-form";
      this.el.setAttribute("data-target", this.target);
      // a real <form>, but we own submission (no page navigation).
      this.el.addEventListener("submit", (e) => { e.preventDefault(); this.submit(); });
    }

    // A child control records its field into the buffer at mount (initialized
    // from its current bound value) and on every subsequent change.
    registerField(field, controlHost, initialValue) {
      this.fields[field] = controlHost;
      if (!(field in this.buffer)) this.buffer[field] = initialValue;
    }
    setBuffer(field, value) { this.buffer[field] = value; }   // NO bus traffic

    showFieldError(field, msg) {
      const ch = this.fields[field];
      if (ch && ch.error) { ch.error(msg); return true; }
      return false;
    }
    clearErrors() { for (const f in this.fields) { const ch = this.fields[f]; if (ch && ch.error) ch.error(null); } this._setFormError(null); }
    _setFormError(msg) {
      if (!this._formErr) {
        this._formErr = this.host.doc.createElement("div");
        this._formErr.className = "compose-form-error";
        this._formErr.setAttribute("data-role", "form-error");
        this._formErr.style.display = "none";
        this.el.appendChild(this._formErr);
      }
      this._formErr.textContent = msg || "";
      this._formErr.style.display = msg ? "block" : "none";
    }

    // SUBMIT: server-validate, then publish atomically or surface per-field
    // errors.  Returns a Promise<{ok, errors?}>.
    submit() {
      const self = this;
      this.clearErrors();
      const payload = Object.assign({}, this.buffer);
      let p;
      if (this.host.ws && typeof this.host.ws.validateConfig === "function") {
        p = this.host.ws.validateConfig(this.target, payload);
      } else if (typeof this.host.validateConfig === "function") {
        p = Promise.resolve(this.host.validateConfig(this.target, payload));
      } else {
        // No transport wired — refuse to fake a verdict ([CXC-VISION-EPISTEMICS]:
        // an instrument that can only report success is not an instrument).
        this._setFormError("no validation transport bound — submission refused");
        return Promise.resolve({ ok: false, errors: [{ field: null, msg: "no transport" }] });
      }
      return Promise.resolve(p).then(function (res) {
        res = res || {};
        if (res.ok) {
          // ATOMIC publish: ONE bus write of the whole validated config.
          self.host.publishLive(self.namespace, Object.assign({}, payload));
          if (self._submitted) self._submitted(payload);
          return { ok: true };
        }
        const errors = (res.errors || res.unmet || []).map(parseConfigError);
        let anyFieldShown = false;
        for (const e of errors) { if (e.field && self.showFieldError(e.field, e.msg)) anyFieldShown = true; }
        const orphans = errors.filter((e) => !e.field || !self.fields[e.field]);
        if (orphans.length || !anyFieldShown) {
          self._setFormError(orphans.map((e) => (e.field ? e.field + ": " : "") + e.msg).join("; ") || "config invalid");
        }
        return { ok: false, errors: errors };
      });
    }
    onSubmitted(cb) { this._submitted = cb; return this; }
  }

  return { Form, parseConfigError };
});
