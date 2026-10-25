const fs = require("fs");
const vm = require("vm");

class FakeClassList {
  constructor(initial = "") {
    this.set = new Set(String(initial).split(/\s+/).filter(Boolean));
  }
  add(...names) { names.forEach(n => this.set.add(n)); }
  remove(...names) { names.forEach(n => this.set.delete(n)); }
  contains(name) { return this.set.has(name); }
  toggle(name, force) {
    const on = force === undefined ? !this.set.has(name) : !!force;
    if (on) this.set.add(name);
    else this.set.delete(name);
    return on;
  }
  toString() { return Array.from(this.set).join(" "); }
}

function attrsFrom(html) {
  const out = {};
  html.replace(/([a-zA-Z0-9_-]+)="([^"]*)"/g, (_, key, val) => {
    out[key] = val;
    return "";
  });
  return out;
}

function makeElement(id = "") {
  const listeners = {};
  const el = {
    id,
    tagName: "DIV",
    value: "",
    textContent: "",
    hidden: false,
    disabled: false,
    dataset: {},
    style: {},
    attributes: {},
    children: [],
    classList: new FakeClassList(),
    set innerHTML(value) {
      this._innerHTML = String(value);
      this.children = [];
      const re = /<button\b([^>]*)>/g;
      let m;
      while ((m = re.exec(this._innerHTML))) {
        const attrs = attrsFrom(m[1]);
        const child = makeElement();
        child.tagName = "BUTTON";
        child.classList = new FakeClassList(attrs.class || "");
        child.attributes = { ...attrs };
        child.dataset = {};
        Object.entries(attrs).forEach(([k, v]) => {
          if (k.startsWith("data-")) {
            child.dataset[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
          }
        });
        child.disabled = Object.prototype.hasOwnProperty.call(attrs, "disabled");
        this.children.push(child);
      }
    },
    get innerHTML() { return this._innerHTML || ""; },
    addEventListener(type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
    dispatch(type, extra = {}) {
      const evt = {
        type,
        currentTarget: this,
        target: this,
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() { this.stopped = true; },
        ...extra,
      };
      (listeners[type] || []).forEach(fn => fn(evt));
    },
    setAttribute(key, value) { this.attributes[key] = String(value); },
    getAttribute(key) { return this.attributes[key]; },
    appendChild(child) { this.children.push(child); },
    remove() {},
    focus() {},
    getBoundingClientRect() { return { width: 160, height: 160, top: 0, left: 0, bottom: 160 }; },
    querySelector() { return makeElement(); },
    querySelectorAll(selector) {
      if (selector === ".dash-bid-option") return this.children.filter(c => c.classList.contains("dash-bid-option"));
      if (selector === ".apexcharts-pie-area") return this.__slices || [];
      if (selector === ".slice-hot") return (this.__slices || []).filter(s => s.classList.contains("slice-hot"));
      return [];
    },
  };
  return el;
}

class FakeApexCharts {
  constructor(host, options) {
    this.host = host;
    this.options = options;
    this.handlers = {};
    this.updateCalls = [];
    FakeApexCharts.last = this;
  }
  render() {
    this.host.__slices = (this.options.series || []).map((_, i) => {
      const s = makeElement();
      s.classList.add("apexcharts-pie-area");
      s.attributes.j = String(i);
      return s;
    });
  }
  destroy() {}
  addEventListener(name, fn) { this.handlers[name] = fn; }
  updateOptions(next) {
    this.options = next;
    this.updateCalls.push(next);
  }
}

const elements = {
  dashBidDonut: makeElement("dashBidDonut"),
  dashBidLegend: makeElement("dashBidLegend"),
};

const context = {
  console,
  setTimeout() {},
  clearTimeout() {},
  requestAnimationFrame(fn) { fn(); },
  cancelAnimationFrame() {},
  ApexCharts: FakeApexCharts,
  getComputedStyle() {
    return { getPropertyValue() { return ""; } };
  },
  localStorage: { getItem() { return null; }, setItem() {} },
  window: {
    ApexCharts: FakeApexCharts,
    addEventListener() {},
    matchMedia() { return { matches: false }; },
    localStorage: { getItem() { return null; }, setItem() {} },
  },
  document: {
    readyState: "loading",
    addEventListener() {},
    documentElement: { getAttribute() { return "light"; }, setAttribute() {} },
    body: { classList: new FakeClassList() },
    getElementById(id) { return elements[id] || makeElement(id); },
    querySelector() { return makeElement(); },
    querySelectorAll() { return []; },
    createElement() { return makeElement(); },
  },
};
context.window.document = context.document;
context.window.localStorage = context.localStorage;

vm.createContext(context);
vm.runInContext(fs.readFileSync("static/app.js", "utf8"), context, { filename: "static/app.js" });

context.window.renderBidDonut([
  { label: "Won", color: "#16A34A", value: 1 },
  { label: "Open", color: "#2563EB", value: 2 },
  { label: "Lost", color: "#DC2626", value: 1 },
], 25);

const buttons = elements.dashBidLegend.querySelectorAll(".dash-bid-option");
if (buttons.length !== 3) {
  throw new Error("Bid outcomes legend should render three interactive buttons");
}
if (!buttons.every(b => b.getAttribute("aria-pressed") === "false")) {
  throw new Error("Bid outcomes legend buttons should start unpinned");
}

buttons[2].dispatch("mouseenter");
if (!buttons[2].classList.contains("is-preview")) {
  throw new Error("Hovering a legend row should preview that bid outcome");
}
if (!elements.dashBidDonut.querySelectorAll(".slice-hot")[0]) {
  throw new Error("Hovering a legend row should highlight the matching donut slice");
}

buttons[2].dispatch("click");
if (buttons[2].getAttribute("aria-pressed") !== "true" || !buttons[2].classList.contains("is-selected")) {
  throw new Error("Clicking a legend row should pin that bid outcome");
}
if (!elements.dashBidDonut.classList.contains("donut-pinned")) {
  throw new Error("Pinned bid outcome should be reflected on the donut host");
}

buttons[2].dispatch("mouseleave");
if (buttons[2].getAttribute("aria-pressed") !== "true") {
  throw new Error("Pinned bid outcome should stay selected after mouse leave");
}

buttons[2].dispatch("click");
if (buttons[2].getAttribute("aria-pressed") !== "false" || elements.dashBidDonut.classList.contains("donut-pinned")) {
  throw new Error("Clicking the selected bid outcome again should clear the pin");
}

const chartEvents = FakeApexCharts.last.options.chart && FakeApexCharts.last.options.chart.events;
if (!chartEvents || typeof chartEvents.dataPointSelection !== "function") {
  throw new Error("Donut slices should expose a click/pin handler");
}
chartEvents.dataPointSelection(null, null, { dataPointIndex: 1 });
if (buttons[1].getAttribute("aria-pressed") !== "true" || !buttons[1].classList.contains("is-selected")) {
  throw new Error("Clicking a donut slice should pin the matching legend row");
}

console.log("bid outcomes interaction tests passed");
