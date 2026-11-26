// ComposeUX DOM Canvas — the browser realization of the Layer-1 drawing
// contract ([CXC-DOM-CANVAS-SUBCLASS], realizing [CXC-R-DRAW-CONTRACT]).
//
// This is the FALLBACK realization path ([CXC-DOM-REALIZATION-SPLIT]): a
// layout/chrome widget that draws through the Canvas primitives renders here as
// DOM elements, exactly as it draws on Skia/SVG — no widget author knows a DOM
// backend exists.  Interactive CONTROLS do NOT come through here; they are
// realized as native elements by the control renderer registry (controls.js).
//
// A DOM tree is discrete addressable elements, not one painted raster, so this
// class carries an explicit STYLE/TRANSFORM STACK — {fillColor, strokeColor,
// strokeWidth, opacity, shadow, transform, font, parent} — pushed by save() and
// popped by restore().  Every geometry/text/image call builds its element(s)
// from the CURRENT top-of-stack state at call time.  translate/rotate/scale
// COMPOSE into the current transform matrix; clip_rect/clip_round_rect push an
// overflow:hidden / clip-path boundary that bounds every element created before
// the matching restore().
//
// Element map ([CXC-DOM-CANVAS-SUBCLASS] element-map): fill_rect/round ->
// positioned div; stroke -> bordered div; circle -> div border-radius:50%;
// arc/path -> inline <svg><path>; line -> inline <svg><line>; text -> span;
// image -> <img>; shadow -> box-shadow/filter; opacity -> CSS opacity.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.ComposeDomCanvas = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SVG_NS = "http://www.w3.org/2000/svg";

  function rgbaStr(r, g, b, a) {
    return "rgba(" + (r | 0) + "," + (g | 0) + "," + (b | 0) + "," +
      (a === undefined ? 1 : a / 255) + ")";
  }
  // 2x3 affine [a,b,c,d,e,f]; point (x,y) -> (a*x+c*y+e, b*x+d*y+f).
  const IDENTITY = [1, 0, 0, 1, 0, 0];
  function matMul(m, n) {
    return [
      m[0] * n[0] + m[2] * n[1],
      m[1] * n[0] + m[3] * n[1],
      m[0] * n[2] + m[2] * n[3],
      m[1] * n[2] + m[3] * n[3],
      m[0] * n[4] + m[2] * n[5] + m[4],
      m[1] * n[4] + m[3] * n[5] + m[5],
    ];
  }
  function matStr(m) {
    return "matrix(" + m[0] + "," + m[1] + "," + m[2] + "," + m[3] + "," + m[4] + "," + m[5] + ")";
  }

  class DomCanvas {
    // opts: {document, parent}.  parent is the host element the scene root is
    // appended under; document defaults to the global document.
    constructor(opts) {
      opts = opts || {};
      this.doc = opts.document || (typeof document !== "undefined" ? document : null);
      if (!this.doc) throw new Error("DomCanvas needs a document (browser or a DOM shim)");
      this.hostParent = opts.parent || (this.doc.body || null);
      this.root = null;
      this.size = { width: 0, height: 0 };
      this._fontScale = 1.0;
      this._state = this._freshState();
      this._stack = [];
      this._probe = null;          // measure_text probe element (lazy)
      this._pending = null;        // present() DocumentFragment batch
    }

    _freshState() {
      return {
        fillColor: rgbaStr(0, 0, 0, 255),
        strokeColor: rgbaStr(0, 0, 0, 255),
        strokeWidth: 1.0,
        opacity: 1.0,
        shadow: null,               // {css} or null
        gradient: null,             // {css} background fill or null
        transform: IDENTITY.slice(),
        font: { family: "sans-serif", size: 16.0, weight: "regular" },
        parent: null,               // current clip container (null => root)
      };
    }
    _parent() { return this._state.parent || this.root; }

    // Element placement: encode the CURRENT transform AND the element's own
    // (x,y) origin into ONE CSS matrix so rotate/scale/translate compose
    // correctly.  Element boxes are laid out at (0,0) then transformed.
    _placeTransform(x, y) {
      return matStr(matMul(this._state.transform, [1, 0, 0, 1, x, y]));
    }
    _newEl(tag) { return this.doc.createElement(tag); }
    _append(el) {
      if (this._pending) this._pending.appendChild(el);
      else this._parent().appendChild(el);
      return el;
    }
    _applyCommon(el, x, y) {
      const s = el.style;
      s.position = "absolute";
      s.left = "0px";
      s.top = "0px";
      s.transform = this._placeTransform(x, y);
      s.transformOrigin = "0 0";
      if (this._state.opacity !== 1.0) s.opacity = String(this._state.opacity);
      if (this._state.shadow) s.boxShadow = this._state.shadow;
      return el;
    }

    // -- Geometry ---------------------------------------------------------
    _rect(x, y, w, h, radius, filled) {
      const el = this._applyCommon(this._newEl("div"), x, y);
      el.style.width = w + "px";
      el.style.height = h + "px";
      if (radius) el.style.borderRadius = radius + "px";
      if (filled) {
        el.style.background = this._state.gradient ? this._state.gradient : this._state.fillColor;
      } else {
        el.style.border = this._state.strokeWidth + "px solid " + this._state.strokeColor;
        el.style.boxSizing = "border-box";
      }
      el.setAttribute("data-prim", filled ? (radius ? "fill_round_rect" : "fill_rect")
        : (radius ? "stroke_round_rect" : "stroke_rect"));
      return this._append(el);
    }
    fill_rect(x, y, w, h) { return this._rect(x, y, w, h, 0, true); }
    stroke_rect(x, y, w, h) { return this._rect(x, y, w, h, 0, false); }
    fill_round_rect(x, y, w, h, radius) { return this._rect(x, y, w, h, radius, true); }
    stroke_round_rect(x, y, w, h, radius) { return this._rect(x, y, w, h, radius, false); }

    _circle(cx, cy, r, filled) {
      const el = this._applyCommon(this._newEl("div"), cx - r, cy - r);
      el.style.width = (2 * r) + "px";
      el.style.height = (2 * r) + "px";
      el.style.borderRadius = "50%";
      if (filled) el.style.background = this._state.gradient ? this._state.gradient : this._state.fillColor;
      else { el.style.border = this._state.strokeWidth + "px solid " + this._state.strokeColor; el.style.boxSizing = "border-box"; }
      el.setAttribute("data-prim", filled ? "fill_circle" : "stroke_circle");
      return this._append(el);
    }
    fill_circle(cx, cy, r) { return this._circle(cx, cy, r, true); }
    stroke_circle(cx, cy, r) { return this._circle(cx, cy, r, false); }

    // arc/path/line -> inline SVG (no pure-CSS primitive for arbitrary beziers)
    _svgWrap(prim) {
      const svg = this.doc.createElementNS(SVG_NS, "svg");
      svg.setAttribute("overflow", "visible");
      const el = this._applyCommon(svg, 0, 0);
      el.style.width = "0px"; el.style.height = "0px";
      el.setAttribute("data-prim", prim);
      return svg;
    }
    _arcPathD(cx, cy, r, a0, a1, sector) {
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      const large = (Math.abs(a1 - a0) % (2 * Math.PI)) > Math.PI ? 1 : 0;
      const sweep = a1 > a0 ? 1 : 0;
      let d = "M " + x0 + " " + y0 + " A " + r + " " + r + " 0 " + large + " " + sweep + " " + x1 + " " + y1;
      if (sector) d += " L " + cx + " " + cy + " Z";
      return d;
    }
    fill_arc(cx, cy, r, a0, a1) {
      const svg = this._svgWrap("fill_arc");
      const p = this.doc.createElementNS(SVG_NS, "path");
      p.setAttribute("d", this._arcPathD(cx, cy, r, a0, a1, true));
      p.setAttribute("fill", this._state.fillColor);
      svg.appendChild(p);
      return this._append(svg);
    }
    stroke_arc(cx, cy, r, a0, a1) {
      const svg = this._svgWrap("stroke_arc");
      const p = this.doc.createElementNS(SVG_NS, "path");
      p.setAttribute("d", this._arcPathD(cx, cy, r, a0, a1, false));
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", this._state.strokeColor);
      p.setAttribute("stroke-width", String(this._state.strokeWidth));
      svg.appendChild(p);
      return this._append(svg);
    }
    draw_line(x1, y1, x2, y2) {
      const svg = this._svgWrap("draw_line");
      const ln = this.doc.createElementNS(SVG_NS, "line");
      ln.setAttribute("x1", x1); ln.setAttribute("y1", y1);
      ln.setAttribute("x2", x2); ln.setAttribute("y2", y2);
      ln.setAttribute("stroke", this._state.strokeColor);
      ln.setAttribute("stroke-width", String(this._state.strokeWidth));
      svg.appendChild(ln);
      return this._append(svg);
    }
    _pathD(commands) {
      const parts = [];
      for (const cmd of commands) {
        const k = cmd[0];
        if (k === "M") parts.push("M " + cmd[1] + " " + cmd[2]);
        else if (k === "L") parts.push("L " + cmd[1] + " " + cmd[2]);
        else if (k === "C") parts.push("C " + cmd[1] + " " + cmd[2] + " " + cmd[3] + " " + cmd[4] + " " + cmd[5] + " " + cmd[6]);
        else if (k === "Q") parts.push("Q " + cmd[1] + " " + cmd[2] + " " + cmd[3] + " " + cmd[4]);
        else if (k === "A") parts.push("A " + cmd[1] + " " + cmd[2] + " " + cmd[3] + " " + (cmd[4] ? 1 : 0) + " " + (cmd[5] ? 1 : 0) + " " + cmd[6] + " " + cmd[7]);
        else if (k === "Z") parts.push("Z");
      }
      return parts.join(" ");
    }
    draw_path(commands) {
      const svg = this._svgWrap("draw_path");
      const p = this.doc.createElementNS(SVG_NS, "path");
      p.setAttribute("d", this._pathD(commands));
      p.setAttribute("fill", "none");
      p.setAttribute("stroke", this._state.strokeColor);
      p.setAttribute("stroke-width", String(this._state.strokeWidth));
      svg.appendChild(p);
      return this._append(svg);
    }
    fill_path(commands) {
      const svg = this._svgWrap("fill_path");
      const p = this.doc.createElementNS(SVG_NS, "path");
      p.setAttribute("d", this._pathD(commands));
      p.setAttribute("fill", this._state.gradient ? "currentColor" : this._state.fillColor);
      svg.appendChild(p);
      return this._append(svg);
    }

    // -- Style state ------------------------------------------------------
    set_fill_color(r, g, b, a) { this._state.fillColor = rgbaStr(r, g, b, a); this._state.gradient = null; }
    set_stroke_color(r, g, b, a) { this._state.strokeColor = rgbaStr(r, g, b, a); }
    set_stroke_width(w) { this._state.strokeWidth = w; }
    _gradStops(stops) {
      return stops.map((s) => rgbaStr(s.r, s.g, s.b, s.a === undefined ? 255 : s.a) + " " + (s.offset * 100) + "%").join(", ");
    }
    set_linear_gradient(x1, y1, x2, y2, stops) {
      const ang = Math.atan2(y2 - y1, x2 - x1) * 180 / Math.PI + 90;
      this._state.gradient = "linear-gradient(" + ang + "deg, " + this._gradStops(stops) + ")";
    }
    set_radial_gradient(cx, cy, r, stops) {
      this._state.gradient = "radial-gradient(circle at " + cx + "px " + cy + "px, " + this._gradStops(stops) + ")";
    }
    set_shadow(ox, oy, blur, color) {
      this._state.shadow = ox + "px " + oy + "px " + blur + "px " + rgbaStr(color[0], color[1], color[2], color[3]);
    }
    clear_shadow() { this._state.shadow = null; }
    set_opacity(a) { this._state.opacity = a; }

    // -- Transform & clip -------------------------------------------------
    save() { this._stack.push(Object.assign({}, this._state, { transform: this._state.transform.slice(), font: Object.assign({}, this._state.font) })); }
    restore() { if (this._stack.length) this._state = this._stack.pop(); }
    translate(dx, dy) { this._state.transform = matMul(this._state.transform, [1, 0, 0, 1, dx, dy]); }
    rotate(angle) { const c = Math.cos(angle), s = Math.sin(angle); this._state.transform = matMul(this._state.transform, [c, s, -s, c, 0, 0]); }
    scale(sx, sy) { this._state.transform = matMul(this._state.transform, [sx, 0, 0, sy, 0, 0]); }

    _clip(x, y, w, h, radius) {
      const el = this._applyCommon(this._newEl("div"), x, y);
      el.style.width = w + "px";
      el.style.height = h + "px";
      el.style.overflow = "hidden";
      if (radius) el.style.borderRadius = radius + "px";
      el.setAttribute("data-prim", radius ? "clip_round_rect" : "clip_rect");
      this._append(el);
      // subsequent draws (until restore) nest inside the clip box.  Their own
      // (x,y) are logical, but the clip box is already placed at (x,y) via the
      // CTM, so children re-offset by (-x,-y) inside it.
      this._state = Object.assign({}, this._state, {
        transform: matMul(this._state.transform, [1, 0, 0, 1, x, y]),
        parent: el,
      });
      // re-anchor so a child drawn at logical (x,y) lands at the clip's (0,0)
      this._state.transform = matMul(this._state.transform, [1, 0, 0, 1, -x, -y]);
      return el;
    }
    clip_rect(x, y, w, h) { return this._clip(x, y, w, h, 0); }
    clip_round_rect(x, y, w, h, radius) { return this._clip(x, y, w, h, radius); }

    // -- Text -------------------------------------------------------------
    set_font_scale(scale) { this._fontScale = scale || 1.0; }
    set_font(family, size, weight) {
      this._state.font = { family: family, size: size * this._fontScale, weight: weight || "regular" };
    }
    _cssFont(f) {
      const w = f.weight === "bold" ? "700" : f.weight === "medium" ? "500" : "400";
      return w + " " + f.size + "px " + f.family;
    }
    draw_text(text, x, y, align) {
      const el = this._applyCommon(this._newEl("span"), x, y);
      const f = this._state.font;
      el.style.font = this._cssFont(f);
      el.style.color = this._state.fillColor;
      el.style.whiteSpace = "pre";
      el.style.lineHeight = "1";
      // (x,y) is the text BASELINE on the Canvas contract; place the span so
      // its baseline sits at y by offsetting up by the ascent (~0.8em).
      el.style.transform = matStr(matMul(this._state.transform, [1, 0, 0, 1, x, y - f.size * 0.8]));
      if (align === "center") el.style.transform += " translateX(-50%)";
      else if (align === "right") el.style.transform += " translateX(-100%)";
      el.setAttribute("data-prim", "draw_text");
      el.textContent = text;
      return this._append(el);
    }
    measure_text(text) {
      const f = this._state.font;
      // Synchronous DOM measurement against a hidden probe carrying the SAME
      // font ([CXC-DOM-CANVAS-SUBCLASS] measure-text-decision).  A real browser
      // returns the actual rendering metrics; a layout-less headless DOM
      // (jsdom/shim) returns 0, so fall back to an advance estimate and mark
      // the metric as approximate (glass verification covers the real path).
      if (!this._probe) {
        this._probe = this._newEl("span");
        const ps = this._probe.style;
        ps.position = "absolute"; ps.visibility = "hidden"; ps.whiteSpace = "pre";
        ps.left = "-9999px"; ps.top = "-9999px";
        (this.doc.body || this._parent()).appendChild(this._probe);
      }
      this._probe.style.font = this._cssFont(f);
      this._probe.textContent = text;
      let w = 0, h = f.size;
      const rect = this._probe.getBoundingClientRect ? this._probe.getBoundingClientRect() : null;
      if (rect && rect.width > 0) { w = rect.width; h = rect.height || f.size; }
      else { w = text.length * f.size * 0.5; }         // headless fallback
      return { width: w, height: h, ascent: f.size * 0.8, descent: f.size * 0.2, approximate: !(rect && rect.width > 0) };
    }

    // -- Image ------------------------------------------------------------
    draw_image(handle, x, y, w, h) {
      const el = this._applyCommon(this._newEl("img"), x, y);
      el.style.width = w + "px"; el.style.height = h + "px";
      if (handle && handle.src) el.setAttribute("src", handle.src);
      el.setAttribute("data-prim", "draw_image");
      return this._append(el);
    }
    load_image(uri) { return { src: uri }; }
    decode_image(data) {
      // bytes -> a handle.  A browser wraps them in a Blob URL; a headless shim
      // just carries the length so a test can assert a decode happened.
      if (typeof Blob !== "undefined" && typeof URL !== "undefined" && URL.createObjectURL) {
        try { return { src: URL.createObjectURL(new Blob([data])) }; } catch (e) { /* fall through */ }
      }
      return { src: null, bytes: (data && data.length) || 0 };
    }

    // -- Surface ----------------------------------------------------------
    create_surface(width, height) {
      this.size = { width: width, height: height };
      this.root = this._newEl("div");
      this.root.style.position = "relative";
      this.root.style.width = width + "px";
      this.root.style.height = height + "px";
      this.root.setAttribute("data-compose-root", "1");
      if (this.hostParent) this.hostParent.appendChild(this.root);
      return this.root;
    }
    present() {
      // DOM mutations are already live; batch a same-frame fragment if one was
      // opened.  A raster backend flushes pixels here — the DOM equivalent is a
      // single fragment append to avoid one reflow per draw call.
      if (this._pending && this.root) { this._parent().appendChild(this._pending); this._pending = null; }
    }
    present_partial(rects) { this.present(); }
    get_surface_size() { return { width: this.size.width, height: this.size.height }; }

    // -- convenience for the runtime: clear the whole surface -------------
    clear() {
      if (this.root) { while (this.root.firstChild) this.root.removeChild(this.root.firstChild); }
      this._state = this._freshState();
      this._stack = [];
    }
  }

  DomCanvas.matMul = matMul;
  DomCanvas.IDENTITY = IDENTITY;
  return DomCanvas;
});
