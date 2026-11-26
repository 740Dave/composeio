// ComposeUX DOM runtime — the THIRD reactive tier ([CXC-DOM-REUSE-REIMPLEMENT-
// RUNTIME]), after Python/Skia and C++/CYD.  Reuses the CONTRACT (scene format,
// binding-DSL grammar, control-publish vocabulary) by reference; REIMPLEMENTS
// the runtime in JS: parse a scene -> build the DOM tree -> wire bindings
// (subscribe on mount, apply on bus change) -> patch the DOM on bus events ->
// publish on control events.
//
// Dispatch is per-widget-class ([CXC-DOM-REALIZATION-SPLIT]): a type in the
// control renderer registry gets its bespoke NATIVE realization; a `form` gets
// the FORM primitive; a layout container gets a flex box; a declarative visual
// (label/rect/circle/image) is drawn through the generic DOM Canvas
// (DomCanvas), the SAME primitive contract the golden-fixture test exercises;
// anything else is a NAMED PLACEHOLDER ([CXC-DOM-HYBRID-COMPOSITION]: never a
// silent blank), and a CanvasKit graphics island is never built/loaded for it
// ([CXC-DOM-REALIZATION-SPLIT]: structural laziness).

(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(
      require("./dom_canvas.js"), require("./bindings.js"),
      require("./controls.js"), require("./form.js"), require("./dsl.js"));
  } else {
    root.ComposeRuntime = factory(root.ComposeDomCanvas, root.ComposeBindings,
      root.ComposeControls, root.ComposeForm, root.ComposeDSL);
  }
})(typeof self !== "undefined" ? self : this, function (DomCanvas, B, Controls, FormMod, DSL) {
  "use strict";

  const LAYOUT_TYPES = new Set(["row", "column", "container", "panel", "group", "stack", "pixel_stack", "grid", "scene"]);
  const VISUAL_TYPES = new Set(["label", "text_label", "rect", "rectangle", "circle", "image"]);

  function nodeChildren(node) {
    if (Array.isArray(node.children)) return node.children;
    if (Array.isArray(node.widgets)) return node.widgets;
    return [];
  }

  class Runtime {
    // opts: {parent, ws, document, tick}
    constructor(opts) {
      opts = opts || {};
      this.doc = opts.document || (typeof document !== "undefined" ? document : null);
      this.ws = opts.ws || null;
      this.parent = opts.parent || (this.doc && this.doc.body);
      this.tick = opts.tick || { dt: 0.0, t: 0.0, vars: {} };
      this.readers = [];              // {namespaces:[...], resolve()}
      this._subscribed = new Set();
      this.forms = [];
      this.placeholders = [];
      this.canvasKitRequested = false;   // MUST stay false for control-only scenes
      this.canvas = null;
      this.root = null;
    }

    store() { return this.ws ? this.ws.store() : {}; }

    // -- binding plumbing -------------------------------------------------
    _ensureSub(ns) {
      if (this._subscribed.has(ns) || !this.ws) return;
      this._subscribed.add(ns);
      this.ws.subscribe(ns, (n) => this._dispatch(n));
    }
    _dispatch(ns) {
      for (const r of this.readers) if (r.namespaces.indexOf(ns) !== -1) r.resolve();
    }
    _makeReader(binding, kind, apply) {
      const self = this;
      const nss = binding.namespaces();
      const reader = {
        namespaces: nss,
        resolve() {
          const v = B.resolveBinding(binding, self.store(), kind, self.tick);
          if (v !== B.UNCHANGED) apply(v);
        },
      };
      this.readers.push(reader);
      nss.forEach((ns) => this._ensureSub(ns));
      reader.resolve();               // initial apply
      return reader;
    }
    // addRead(spec, attr, kind, apply): spec is a raw attribute value — a
    // binding object/expr, a bare-namespace (for NAMESPACE_STRING_ATTRS), or a
    // literal.  Literals apply once; bindings subscribe + re-apply on change.
    _addRead(where, spec, attr, kind, apply) {
      const b = B.parseBinding(attr, spec, where);
      if (b) { this._makeReader(b, kind, apply); return; }
      if (spec !== undefined && spec !== null && typeof spec !== "object") apply(spec);
    }
    _addReadRaw(ns, apply) {
      if (typeof ns !== "string" || !ns) return;
      const self = this;
      const reader = { namespaces: [ns], resolve() { apply(self.store()[ns]); } };
      this.readers.push(reader);
      this._ensureSub(ns);
      reader.resolve();
    }

    // -- host factory for a control ---------------------------------------
    _controlHost(spec, form) {
      const self = this;
      const field = spec.field || spec.id || "";
      const namespace = spec.namespace || (spec.value && spec.value.namespace) || "";
      const host = {
        doc: this.doc,
        ws: this.ws,
        store: () => self.store(),
        mode: form ? "buffer" : "live",
        field: field,
        namespace: namespace,
        _errorBox: null,
        publish(ns, value) { if (self.ws) self.ws.publish(ns, value); },
        publishLive(ns, value) { if (self.ws) self.ws.publish(ns, value); },
        setField(value) {
          if (form) form.setBuffer(field, value);       // buffer mode: NO bus
          else if (namespace && self.ws) self.ws.publish(namespace, value);  // live
        },
        error(msg) { Controls.setError(host, host._errorBox, msg); },
        addRead(s, attr, kind, apply) { self._addRead("control:" + (spec.id || spec.type), s, attr, kind, apply); },
        addReadRaw(ns, apply) { self._addReadRaw(ns, apply); },
      };
      return host;
    }

    // -- build ------------------------------------------------------------
    mount(scene) {
      const design = scene.design || scene || {};
      const width = design.width || scene.width || 800;
      const height = design.height || scene.height || 600;
      this.canvas = new DomCanvas({ document: this.doc, parent: this.parent });
      this.canvas.create_surface(width, height);
      this.root = this.canvas.root;
      // switch the root to flow layout for the DOM form/control chrome (the
      // absolute canvas surface is used only by the generic-primitive path).
      this.root.style.position = "relative";
      const rootNode = scene.root || { type: "column", children: scene.widgets || scene.layout || [] };
      const container = this._buildNode(rootNode, null);
      if (container) this.root.appendChild(container);
      return this;
    }

    _buildNode(node, form) {
      if (!node || typeof node !== "object") return null;
      const type = node.type || "container";

      if (type === "form") return this._buildForm(node, form);
      if (Controls.isControl(type)) return this._buildControl(node, form);
      if (LAYOUT_TYPES.has(type)) return this._buildLayout(node, form);
      if (VISUAL_TYPES.has(type)) return this._buildVisual(node);
      return this._buildPlaceholder(node);
    }

    _applyCommonBindings(node, elGetter) {
      // visible / opacity binding on any node.
      if ("visible" in node) {
        this._addRead("node:" + (node.id || node.type), node.visible, "visible", "bool", (v) => {
          const el = elGetter(); if (el) el.style.display = v ? "" : "none";
        });
      }
      if ("opacity" in node) {
        this._addRead("node:" + (node.id || node.type), node.opacity, "opacity", "number", (v) => {
          const el = elGetter(); if (el) el.style.opacity = String(v);
        });
      }
    }

    _buildLayout(node, form) {
      const box = this.doc.createElement("div");
      box.className = "compose-" + node.type;
      box.setAttribute("data-widget", node.id || "");
      const s = box.style;
      s.display = "flex";
      s.flexDirection = (node.type === "row") ? "row" : "column";
      if (node.gap !== undefined) s.gap = node.gap + "px";
      if (node.padding !== undefined) s.padding = node.padding + "px";
      this._applyCommonBindings(node, () => box);
      for (const child of nodeChildren(node)) {
        const el = this._buildNode(child, form);
        if (el) box.appendChild(el);
      }
      return box;
    }

    _buildForm(node, parentForm) {
      if (parentForm) {
        // [CXC-DOM-BOUNDARIES] cond 11 — nested forms are a loud error, never
        // a form with ambiguous submit semantics.
        throw new Error("[CXC-DOM-FORM-PRIMITIVE] nested form not supported in v1 " +
          "(widget id " + (node.id || "<unnamed>") + " is a form inside a form)");
      }
      const host = { doc: this.doc, ws: this.ws, publishLive: (ns, v) => { if (this.ws) this.ws.publish(ns, v); } };
      const form = new FormMod.Form(host, node);
      this.forms.push(form);
      if (node.title) {
        const h = this.doc.createElement("div"); h.className = "compose-form-title"; h.textContent = node.title;
        form.el.appendChild(h);
      }
      // children self-register their field into the form buffer at mount
      // (see _buildControl); nested nodes recurse with `form` threaded through.
      for (const child of nodeChildren(node)) {
        const el = this._buildNode(child, form);
        if (el) form.el.appendChild(el);
      }
      // submit affordance ([CXC-DOM-FORM-PRIMITIVE] explicit submit action)
      const submit = this.doc.createElement("button");
      submit.type = "submit";
      submit.className = "compose-form-submit";
      submit.textContent = node.submit_label || "Submit";
      form.el.appendChild(submit);
      return form.el;
    }

    _initialFor(node) {
      // the field's mount-time value: its literal, else its bound namespace's
      // current live value ([CXC-DOM-FORM-PRIMITIVE] buffer init).
      const v = node.value;
      if (v && typeof v === "object" && v.namespace) {
        const live = this.store()[v.namespace];
        return live !== undefined ? live : (("default" in v) ? v.default : null);
      }
      if (v !== undefined && typeof v !== "object") return v;
      return null;
    }

    _buildControl(node, form) {
      const host = this._controlHost(node, form);
      const fn = Controls.get(node.type);
      const built = fn(host, node);
      this._lastControlHost = host;   // for _buildForm field registration
      // register into the form buffer (change writes buffer, mount seeds it)
      if (form) {
        const field = node.field || node.id;
        if (field && !(field in form.buffer)) form.registerField(field, host, this._initialFor(node));
        // reroute host.error to update this field's error box now that it exists
      }
      this._applyCommonBindings(node, () => built.el);
      built.el.setAttribute("data-widget", node.id || "");
      built.el.setAttribute("data-type", node.type);
      return built.el;
    }

    // Generic Canvas-primitive fallback ([CXC-DOM-REALIZATION-SPLIT]): a
    // declarative visual is drawn through the SAME DomCanvas primitives the
    // golden-fixture test asserts, so a non-control renders correctly without
    // the native-accessibility upgrade a control gets.
    _buildVisual(node) {
      const holder = this.doc.createElement("div");
      holder.style.position = "absolute";
      holder.style.left = (node.x || 0) + "px";
      holder.style.top = (node.y || 0) + "px";
      holder.setAttribute("data-widget", node.id || "");
      holder.setAttribute("data-type", node.type);
      const c = new DomCanvas({ document: this.doc, parent: holder });
      c.create_surface(node.w || node.width || 100, node.h || node.height || 40);
      c.root.style.position = "relative";
      const draw = () => {
        c.clear();
        const w = node.w || node.width || 100, h = node.h || node.height || 40;
        const col = B.rgba(node.fill_color || node.color) || [40, 44, 52, 255];
        if (node.type === "rect" || node.type === "rectangle") {
          c.set_fill_color(col[0], col[1], col[2], col[3]);
          if (node.radius) c.fill_round_rect(0, 0, w, h, node.radius); else c.fill_rect(0, 0, w, h);
        } else if (node.type === "circle") {
          c.set_fill_color(col[0], col[1], col[2], col[3]);
          c.fill_circle(w / 2, h / 2, Math.min(w, h) / 2);
        } else if (node.type === "image") {
          const handle = c.load_image(node.src || "");
          c.draw_image(handle, 0, 0, w, h);
        } else { // label / text_label
          const tc = B.rgba(node.color) || [230, 230, 230, 255];
          c.set_fill_color(tc[0], tc[1], tc[2], tc[3]);
          c.set_font(node.font || "sans-serif", node.font_size || 14, node.weight || "regular");
          c.draw_text(String(node.text || ""), 0, (node.font_size || 14), "left");
        }
        c.present();
      };
      draw();
      // reactive text/color for label ([CXC-DOM-TWO-WAY-BINDING] read half)
      if ("text" in node) this._addRead("visual:" + (node.id || ""), node.text, "text", "string", (v) => { node.text = v; draw(); });
      if ("color" in node) this._addRead("visual:" + (node.id || ""), node.color, "color", "string", () => { });
      return holder;
    }

    _buildPlaceholder(node) {
      // A widget outside this backend's scope (e.g. a gauge on a DOM-only
      // build): a NAMED placeholder, never silent blank space, and NO CanvasKit
      // island is loaded for it ([CXC-DOM-HYBRID-COMPOSITION]).
      const ph = this.doc.createElement("div");
      ph.className = "compose-placeholder";
      ph.setAttribute("data-widget", node.id || "");
      ph.setAttribute("data-type", node.type);
      ph.setAttribute("data-role", "placeholder");
      ph.textContent = "⧉ " + (node.type || "unknown") + " «" + (node.id || "?") +
        "» — not yet realized on this backend";
      this.placeholders.push(ph);
      return ph;
    }
  }

  function mount(scene, opts) { return new Runtime(opts).mount(scene); }
  return { Runtime, mount, LAYOUT_TYPES, VISUAL_TYPES };
});
