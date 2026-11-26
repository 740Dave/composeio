// ComposeUX DOM control renderer registry ([CXC-DOM-REALIZATION-SPLIT],
// [CXC-DOM-CONTROL-PUBLISH-REUSE], [CXC-DOM-CONTROL-SET-V1]).
//
// The registry maps a widget `type` string to a function that builds the
// SEMANTIC NATIVE DOM element(s) for that type directly — never via
// fill_rect/draw_text ([CXC-DOM-SCOPE-SPLIT]: leverage HTML for free
// accessibility/keyboard/i18n; never redraw a control on a raster canvas).  A
// type NOT in this registry falls through to the generic DomCanvas primitive
// path (runtime.js).
//
// Each renderer receives a `host` (from the runtime) and returns
// {el, reads, focusEl?}.  The PUBLISH contract per control is RE-REALIZED, not
// reinvented ([CXC-DOM-CONTROL-PUBLISH-REUSE]) — a `<button>` click for an
// emit_button publishes the SAME emits list on_tap() publishes; a radio change
// publishes the selected index to index_namespace, matching
// RadioButtonGroupWidget.index_for; a checkbox/toggle change publishes its
// bound boolean.  All writes route through the host so the publish MODE
// (live vs form-buffered) is chosen STRUCTURALLY by form membership
// ([CXC-DOM-PUBLISH-MODES]), never by a per-control flag.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root.ComposeBindings || (typeof require !== "undefined" && require("./bindings.js")));
  else root.ComposeControls = factory(root.ComposeBindings);
})(typeof self !== "undefined" ? self : this, function (B) {
  "use strict";

  // host contract (provided by the runtime / form):
  //   host.doc                      document
  //   host.store()                  ns -> live value
  //   host.mode                     'live' | 'buffer'
  //   host.field                    this control's form-field key
  //   host.publish(ns, value)       live bus publish (respects BOUND)
  //   host.setField(value)          record a value: buffer if in a form, else
  //                                 publish to host.namespace (live)
  //   host.namespace                the field control's bound bus namespace
  //   host.error(msg | null)        surface/clear a per-field error inline
  //   host.addRead(spec, attr, kind, applyFn)  register a READ-half binding

  function el(host, tag) { return host.doc.createElement(tag); }
  function setError(host, box, msg) {
    if (!box) return;
    box.textContent = msg || "";
    box.style.display = msg ? "block" : "none";
  }

  // Wrap a labeled field row (label + control + error slot) for form use.
  function fieldRow(host, control, labelText) {
    const row = el(host, "div");
    row.className = "compose-field";
    row.setAttribute("data-field", host.field || "");
    if (labelText) {
      const lab = el(host, "label");
      lab.textContent = labelText;
      lab.className = "compose-label";
      if (control.id) lab.setAttribute("for", control.id);
      row.appendChild(lab);
    }
    row.appendChild(control);
    const err = el(host, "div");
    err.className = "compose-field-error";
    err.style.display = "none";
    err.setAttribute("data-role", "error");
    row.appendChild(err);
    host._errorBox = err;
    return row;
  }

  // A commit-buffer field ([CXC-DOM-FORM-PRIMITIVE] generalizes text_input's
  // model): edit locally, commit on the control's own event (change/blur/Enter),
  // then setField routes by mode.  READ half: the bound namespace sets the
  // element's value unless the user is mid-edit ([CXC-DOM-TWO-WAY-BINDING]:
  // the buffer/edit wins over a concurrent bus echo).
  function inputControl(host, spec, inputType) {
    const input = el(host, "input");
    input.type = inputType;
    input.id = spec.id ? "ctl-" + spec.id : undefined;
    if (spec.placeholder) input.placeholder = spec.placeholder;
    // presentation-layer hints only ([CXC-DOM-SERVER-VALIDATION] SHOULD): never
    // a rule whose failure would also be a pydantic ValidationError.
    if (spec.min !== undefined) input.min = spec.min;
    if (spec.max !== undefined) input.max = spec.max;
    if (spec.maxlength !== undefined) input.maxLength = spec.maxlength;
    if (spec.step !== undefined) input.step = spec.step;

    let editing = false;
    input.addEventListener("focus", () => { editing = true; });
    input.addEventListener("input", () => { editing = true; });
    const commit = () => {
      editing = false;
      host.error(null);
      const value = inputType === "number"
        ? (input.value === "" ? null : Number(input.value))
        : input.value;
      host.setField(value);
    };
    input.addEventListener("change", commit);
    input.addEventListener("blur", commit);
    if (inputType === "text" || inputType === "number") {
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") { commit(); } });
    }

    host.addRead(spec.value !== undefined ? spec.value : spec.namespace, "value", "string", (v) => {
      if (editing) return;                         // in-progress edit wins
      input.value = v === null || v === undefined ? "" : String(v);
    });
    host._setValue = (v) => { if (!editing) input.value = v == null ? "" : String(v); };
    host._getValue = () => (inputType === "number" ? (input.value === "" ? null : Number(input.value)) : input.value);
    return { el: fieldRow(host, input, spec.label), focusEl: input };
  }

  const REGISTRY = {

    // -- emit_button ([CXC-DOM-CONTROL-PUBLISH-REUSE], EmitButtonWidget) ---
    emit_button: function (host, spec) {
      const btn = el(host, "button");
      btn.type = "button";
      btn.className = "compose-emit-button";
      btn.textContent = spec.label || "Emit";
      const emits = Array.isArray(spec.emits) ? spec.emits : [];
      // _resolve: substitute embedded {namespace, default} bindings with the
      // live value (matching EmitButtonWidget._resolve).
      function resolve(v) {
        if (v && typeof v === "object" && !Array.isArray(v) &&
          Object.prototype.hasOwnProperty.call(v, "namespace") &&
          Object.keys(v).every((k) => k === "namespace" || k === "default")) {
          const live = host.store()[v.namespace];
          return live === undefined ? v.default : live;
        }
        if (Array.isArray(v)) return v.map(resolve);
        if (v && typeof v === "object") { const o = {}; for (const k in v) o[k] = resolve(v[k]); return o; }
        return v;
      }
      btn.addEventListener("click", () => {
        for (const e of emits) {
          const ns = e.namespace || e.ns;
          if (!ns) continue;
          host.publish(ns, resolve(e.value));        // command = always live
        }
      });
      host.addRead(spec.label, "label", "string", (v) => { btn.textContent = v; });
      return { el: btn, focusEl: btn };
    },

    // -- radio_button_group ([CXC-DOM-CONTROL-PUBLISH-REUSE], index_namespace)
    radio_button_group: function (host, spec) {
      const group = el(host, "div");
      group.className = "compose-radio-group";
      group.setAttribute("role", "radiogroup");
      if (spec.label) group.setAttribute("aria-label", spec.label);
      const labels = Array.isArray(spec.labels) ? spec.labels : [];
      const name = "radio-" + (spec.id || Math.random().toString(36).slice(2, 8));
      const inputs = [];
      labels.forEach((lab, i) => {
        const wrap = el(host, "label");
        wrap.className = "compose-radio-item";
        const rb = el(host, "input");
        rb.type = "radio"; rb.name = name; rb.value = String(i);
        rb.addEventListener("change", () => {
          if (rb.checked && spec.index_namespace) host.publish(spec.index_namespace, i);
        });
        const cap = el(host, "span"); cap.textContent = lab;
        wrap.appendChild(rb); wrap.appendChild(cap);
        group.appendChild(wrap); inputs.push(rb);
      });
      if (typeof spec.selected_index === "number" && inputs[spec.selected_index]) inputs[spec.selected_index].checked = true;
      // READ half: the selection DERIVES from the published index (like
      // on_bus_state), never from local tap-state guesswork.
      if (spec.index_namespace) {
        host.addRead(spec.index_namespace, "index", "number", (v) => {
          const idx = typeof v === "number" ? v : parseInt(v, 10);
          if (inputs[idx]) inputs[idx].checked = true;
        });
      }
      return { el: group, focusEl: inputs[0] };
    },

    // -- theme_toggle / checkbox-as-toggle (boolean publish) --------------
    theme_toggle: function (host, spec) {
      const wrap = el(host, "label");
      wrap.className = "compose-toggle";
      const cb = el(host, "input"); cb.type = "checkbox";
      cb.addEventListener("change", () => {
        if (host.namespace) host.publish(host.namespace, cb.checked);
        else host.setField(cb.checked);
      });
      const cap = el(host, "span"); cap.textContent = spec.label || "";
      wrap.appendChild(cb); wrap.appendChild(cap);
      host.addRead(host.namespace || spec.value, "state", "bool", (v) => { cb.checked = !!v; });
      return { el: wrap, focusEl: cb };
    },

    // ================= v1 form-field vocabulary =========================
    // ([CXC-DOM-CONTROL-SET-V1]: text/number/checkbox/select/textarea/
    //  date-time/read-only-table/read-only-tree, all native.)

    text: function (host, spec) { return inputControl(host, spec, "text"); },
    number: function (host, spec) { return inputControl(host, spec, "number"); },
    date: function (host, spec) { return inputControl(host, spec, spec.subtype || "date"); },
    time: function (host, spec) { return inputControl(host, spec, "time"); },
    datetime: function (host, spec) { return inputControl(host, spec, "datetime-local"); },

    checkbox: function (host, spec) {
      const cb = el(host, "input"); cb.type = "checkbox";
      cb.id = spec.id ? "ctl-" + spec.id : undefined;
      let editing = false;
      cb.addEventListener("change", () => { host.error(null); host.setField(cb.checked); });
      host.addRead(spec.value !== undefined ? spec.value : spec.namespace, "value", "bool", (v) => {
        if (!editing) cb.checked = !!(v === true || v === "true" || v === "on" || v === 1 || v === "1");
      });
      host._setValue = (v) => { cb.checked = !!v; };
      host._getValue = () => cb.checked;
      // label AFTER the box (checkbox idiom) — build the row manually
      const row = el(host, "div"); row.className = "compose-field"; row.setAttribute("data-field", host.field || "");
      const lab = el(host, "label"); if (cb.id) lab.setAttribute("for", cb.id);
      lab.className = "compose-label compose-inline";
      lab.appendChild(cb);
      const span = el(host, "span"); span.textContent = spec.label || ""; lab.appendChild(span);
      row.appendChild(lab);
      const err = el(host, "div"); err.className = "compose-field-error"; err.style.display = "none"; err.setAttribute("data-role", "error");
      row.appendChild(err); host._errorBox = err;
      return { el: row, focusEl: cb };
    },

    select: function (host, spec) {
      const sel = el(host, "select");
      sel.id = spec.id ? "ctl-" + spec.id : undefined;
      const options = Array.isArray(spec.options) ? spec.options : [];
      for (const opt of options) {
        const o = el(host, "option");
        const val = (opt && typeof opt === "object") ? opt.value : opt;
        const lab = (opt && typeof opt === "object") ? (opt.label || opt.value) : opt;
        o.value = String(val); o.textContent = String(lab);
        sel.appendChild(o);
      }
      let editing = false;
      sel.addEventListener("change", () => { editing = false; host.error(null); host.setField(sel.value); });
      sel.addEventListener("focus", () => { editing = true; });
      host.addRead(spec.value !== undefined ? spec.value : spec.namespace, "value", "string", (v) => {
        if (!editing && v !== null && v !== undefined) sel.value = String(v);
      });
      host._setValue = (v) => { if (v != null) sel.value = String(v); };
      host._getValue = () => sel.value;
      return { el: fieldRow(host, sel, spec.label), focusEl: sel };
    },

    textarea: function (host, spec) {
      const ta = el(host, "textarea");
      ta.id = spec.id ? "ctl-" + spec.id : undefined;
      if (spec.rows) ta.rows = spec.rows;
      if (spec.placeholder) ta.placeholder = spec.placeholder;
      let editing = false;
      ta.addEventListener("focus", () => { editing = true; });
      ta.addEventListener("input", () => { editing = true; });
      const commit = () => { editing = false; host.error(null); host.setField(ta.value); };
      ta.addEventListener("change", commit);
      ta.addEventListener("blur", commit);
      host.addRead(spec.value !== undefined ? spec.value : spec.namespace, "value", "string", (v) => {
        if (!editing) ta.value = v == null ? "" : String(v);
      });
      host._setValue = (v) => { if (!editing) ta.value = v == null ? "" : String(v); };
      host._getValue = () => ta.value;
      return { el: fieldRow(host, ta, spec.label), focusEl: ta };
    },

    // READ-ONLY table ([CXC-DOM-CONTROL-SET-V1]: a real <table>, DISPLAY-ONLY —
    // NOT the deferred editable-collection case, [DD-DOM-EDITABLE-COLLECTIONS]).
    table: function (host, spec) {
      const table = el(host, "table");
      table.className = "compose-table";
      table.setAttribute("data-readonly", "1");
      const build = (rows, columns) => {
        while (table.firstChild) table.removeChild(table.firstChild);
        rows = Array.isArray(rows) ? rows : [];
        let cols = columns;
        if (!cols || !cols.length) {
          const seen = [];
          for (const r of rows) if (r && typeof r === "object") for (const k in r) if (seen.indexOf(k) === -1) seen.push(k);
          cols = seen;
        }
        const thead = el(host, "thead"); const htr = el(host, "tr");
        for (const c of cols) { const th = el(host, "th"); th.textContent = c; htr.appendChild(th); }
        thead.appendChild(htr); table.appendChild(thead);
        const tbody = el(host, "tbody");
        for (const r of rows) {
          const tr = el(host, "tr");
          for (const c of cols) {
            const td = el(host, "td");
            const cell = (r && typeof r === "object") ? r[c] : r;
            td.textContent = cell === undefined || cell === null ? "" : String(cell);
            tr.appendChild(td);
          }
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
      };
      build(spec.rows, spec.columns);
      if (typeof spec.value === "string" || (spec.value && spec.value.namespace)) {
        host.addRead(spec.value, "value", "string", () => { });  // registered below w/ raw
      }
      // bind raw rows to a namespace (structured display refresh)
      if (spec.namespace) {
        host.addReadRaw(spec.namespace, (v) => build(v, spec.columns));
      }
      return { el: table };
    },

    // READ-ONLY tree ([CXC-DOM-CONTROL-SET-V1]: ARIA role="tree" for namespace/
    // hierarchy review — David: "very useful for namespace review").  Non-
    // virtualized is fine for v1 ([DD-DOM-TREE-VIRTUALIZATION] deferred).
    tree: function (host, spec) {
      const root = el(host, "div");
      root.className = "compose-tree";
      root.setAttribute("role", "tree");
      if (spec.label) root.setAttribute("aria-label", spec.label);
      const buildNode = (key, value, level) => {
        const item = el(host, "div");
        item.setAttribute("role", "treeitem");
        item.className = "compose-tree-item";
        item.style.paddingLeft = (level * 16) + "px";
        const isBranch = value && typeof value === "object";
        const labelEl = el(host, "span");
        labelEl.className = "compose-tree-label";
        labelEl.textContent = isBranch ? String(key) : (String(key) + ": " + String(value));
        item.appendChild(labelEl);
        const holder = el(host, "div");
        holder.appendChild(item);
        if (isBranch) {
          item.setAttribute("aria-expanded", "true");
          const childGroup = el(host, "div");
          childGroup.setAttribute("role", "group");
          const entries = Array.isArray(value)
            ? value.map((v, i) => [String(i), v])
            : Object.keys(value).map((k) => [k, value[k]]);
          for (const [k, v] of entries) childGroup.appendChild(buildNode(k, v, level + 1));
          holder.appendChild(childGroup);
        }
        return holder;
      };
      const build = (data) => {
        while (root.firstChild) root.removeChild(root.firstChild);
        if (data && typeof data === "object") {
          const entries = Array.isArray(data) ? data.map((v, i) => [String(i), v]) : Object.keys(data).map((k) => [k, data[k]]);
          for (const [k, v] of entries) root.appendChild(buildNode(k, v, 0));
        }
      };
      build(spec.data || spec.tree || {});
      if (spec.namespace) host.addReadRaw(spec.namespace, (v) => build(v));
      return { el: root };
    },
  };

  // A widget type is a CONTROL (native realization) iff it is in the registry.
  function isControl(type) { return Object.prototype.hasOwnProperty.call(REGISTRY, type); }
  function get(type) { return REGISTRY[type]; }
  function register(type, fn) { REGISTRY[type] = fn; }

  return { REGISTRY, isControl, get, register, fieldRow, setError };
});
