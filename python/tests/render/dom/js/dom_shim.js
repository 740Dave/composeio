// A MINIMAL, DEPENDENCY-FREE DOM shim for the DOM-backend MACHINE tests.
//
// ⚠ SCOPE: this is enough to exercise element construction, the style/attribute
// surface, the tree structure, events (addEventListener/dispatchEvent), a
// simple querySelectorAll, and classList — the things the DOM-backend machine
// tests ASSERT.  It deliberately has NO layout engine, so getBoundingClientRect
// returns zeros and measure_text falls back to an advance estimate; the real
// text-metric / accessibility / screen-reader behavior is a GLASS item verified
// in a real browser (see the DOM-backend README + the pair glass test).  Using
// this shim keeps the backend + tests dependency-free (no jsdom install), which
// also keeps the browser RUNTIME itself dependency-free by construction.

"use strict";

function parseCompound(sel) {
  const out = { tag: null, classes: [], attrs: [] };
  let rest = sel;
  const tagM = rest.match(/^([a-zA-Z*][\w-]*)/);
  if (tagM) { out.tag = tagM[1]; rest = rest.slice(tagM[0].length); }
  const partRe = /(\.[\w-]+)|(\[[^\]]+\])/g;
  let p;
  while ((p = partRe.exec(rest))) {
    if (p[1]) out.classes.push(p[1].slice(1));
    else {
      const a = p[2].slice(1, -1);
      const eq = a.indexOf("=");
      if (eq < 0) out.attrs.push([a, null]);
      else out.attrs.push([a.slice(0, eq), a.slice(eq + 1).replace(/^["']|["']$/g, "")]);
    }
  }
  return out;
}
function matchCompound(el, cs) {
  if (el.nodeType !== 1) return false;
  if (cs.tag && cs.tag !== "*" && el.tagName.toLowerCase() !== cs.tag.toLowerCase()) return false;
  for (const c of cs.classes) if (!el.classList.contains(c)) return false;
  for (const [k, v] of cs.attrs) {
    if (v === null) { if (!el.hasAttribute(k)) return false; }
    else if (el.getAttribute(k) !== v) return false;
  }
  return true;
}

class Style {
  constructor() { this._p = {}; }
  setProperty(k, v) { this._p[k] = v; }
  getPropertyValue(k) { return this._p[k] || ""; }
}
// Proxy so `el.style.foo = 'bar'` records into a plain bag readable by tests.
function makeStyle() {
  return new Proxy(new Style(), {
    get(t, k) { if (k in t) return t[k]; return t._p[k]; },
    set(t, k, v) { t._p[k] = String(v); return true; },
  });
}

class ClassList {
  constructor(el) { this.el = el; }
  _list() { return (this.el.getAttribute("class") || "").split(/\s+/).filter(Boolean); }
  _save(l) { this.el.setAttribute("class", l.join(" ")); }
  add(c) { const l = this._list(); if (l.indexOf(c) < 0) l.push(c); this._save(l); }
  remove(c) { this._save(this._list().filter((x) => x !== c)); }
  contains(c) { return this._list().indexOf(c) >= 0; }
  toggle(c) { if (this.contains(c)) this.remove(c); else this.add(c); }
}

class TextNode {
  constructor(t) { this.nodeType = 3; this._text = String(t); this.parentNode = null; }
  get textContent() { return this._text; }
}

class Element {
  constructor(tag, ns) {
    this.tagName = tag;
    this.nodeType = 1;
    this.namespaceURI = ns || null;
    this.childNodes = [];
    this.parentNode = null;
    this.style = makeStyle();
    this._attrs = {};
    this._listeners = {};
    this._text = "";
    this._classList = new ClassList(this);
  }
  get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get classList() { return this._classList; }
  get className() { return this.getAttribute("class") || ""; }
  set className(v) { this.setAttribute("class", v); }
  get id() { return this.getAttribute("id"); }
  set id(v) { if (v === undefined) return; this.setAttribute("id", v); }
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); return n; }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; } return n; }
  setAttribute(k, v) { this._attrs[k] = String(v); }
  removeAttribute(k) { delete this._attrs[k]; }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k) ? this._attrs[k] : null; }
  hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._attrs, k); }
  get textContent() {
    if (this.childNodes.length) return this.childNodes.map((n) => n.textContent).join("");
    return this._text;
  }
  set textContent(v) { this.childNodes = []; this._text = String(v); }
  addEventListener(type, fn) { (this._listeners[type] || (this._listeners[type] = [])).push(fn); }
  removeEventListener(type, fn) { const l = this._listeners[type]; if (l) this._listeners[type] = l.filter((f) => f !== fn); }
  dispatchEvent(ev) { ev.target = ev.target || this; (this._listeners[ev.type] || []).slice().forEach((fn) => fn(ev)); return !ev.defaultPrevented; }
  getBoundingClientRect() { return { width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0 }; }
  _descendants(out) { out = out || []; for (const n of this.childNodes) { if (n.nodeType === 1) { out.push(n); n._descendants(out); } } return out; }
  querySelectorAll(sel) {
    const steps = sel.trim().split(/\s+/).map(parseCompound);
    const res = [];
    for (const el of this._descendants()) {
      let i = steps.length - 1;
      if (!matchCompound(el, steps[i])) continue;
      i--;
      let cur = el.parentNode;
      while (i >= 0 && cur && cur.nodeType === 1) { if (matchCompound(cur, steps[i])) i--; cur = cur.parentNode; }
      if (i < 0) res.push(el);
    }
    return res;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

class Document {
  constructor() { this.body = new Element("body"); this.documentElement = new Element("html"); }
  createElement(tag) { return new Element(String(tag).toLowerCase()); }
  createElementNS(ns, tag) { return new Element(String(tag).toLowerCase(), ns); }
  createTextNode(t) { return new TextNode(t); }
}

function makeEvent(type, props) {
  const ev = { type: type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  return Object.assign(ev, props || {});
}
function fire(el, type, props) { return el.dispatchEvent(makeEvent(type, props)); }

module.exports = { Document, Element, TextNode, makeEvent, fire };
