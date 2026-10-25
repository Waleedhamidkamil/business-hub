const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("templates/index.html", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const searchPos = html.indexOf('id="dashSearch"');
const togglePos = html.indexOf('id="dashThemeToggle"');
const settingsPos = html.indexOf('id="dashSettingsBtn"');
assert(searchPos >= 0, "dashboard search should exist");
assert(togglePos >= 0, "dashboard theme toggle should exist");
assert(settingsPos >= 0, "dashboard settings button should exist");
assert(searchPos < togglePos && togglePos < settingsPos, "dashboard theme toggle should sit between search and settings");
assert(/id="dashThemeToggle"[^>]*aria-pressed="false"/.test(html), "dashboard theme toggle should expose pressed state");
assert(/id="dashThemeToggle"[^>]*aria-label="Switch to dark mode"/.test(html), "dashboard theme toggle should have an accessible initial label");
assert(css.includes(".dash-theme-toggle"), "dashboard theme toggle should have dedicated styling");
assert(css.includes(".dash-theme-stars"), "dashboard theme toggle should include the dark-mode star treatment");
assert(css.includes("prefers-reduced-motion"), "dashboard theme toggle motion should respect reduced motion");

function makeElement(attrs = {}) {
  const classes = new Set((attrs.className || "").split(/\s+/).filter(Boolean));
  const el = {
    dataset: attrs.dataset || {},
    attributes: {},
    title: "",
    onclick: null,
    _listeners: {},
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      toggle(c, on) {
        if (on) classes.add(c);
        else classes.delete(c);
      },
      contains(c) { return classes.has(c); },
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return this.attributes[k] || null; },
    addEventListener(type, fn) { this._listeners[type] = fn; },
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
    click() {
      if (typeof this.onclick === "function") this.onclick();
      if (this._listeners.click) this._listeners.click();
    },
  };
  Object.entries(attrs.attributes || {}).forEach(([k, v]) => el.setAttribute(k, v));
  return el;
}

const lightOpt = makeElement({ dataset: { themeVal: "light" } });
const darkOpt = makeElement({ dataset: { themeVal: "dark" } });
const themeSeg = makeElement();
themeSeg.querySelectorAll = () => [lightOpt, darkOpt];
const dashToggle = makeElement();
const storage = new Map();
let htmlTheme = "light";

const context = {
  console,
  setTimeout() {},
  clearTimeout() {},
  requestAnimationFrame(fn) { fn(); },
  window: {
    addEventListener() {},
    matchMedia() { return { matches: false }; },
  },
  document: {
    readyState: "loading",
    addEventListener() {},
    documentElement: {
      setAttribute(k, v) { if (k === "data-theme") htmlTheme = String(v); },
      getAttribute(k) { return k === "data-theme" ? htmlTheme : null; },
    },
    body: { classList: { add() {}, remove() {}, toggle() {} } },
    getElementById(id) {
      if (id === "themeSeg") return themeSeg;
      if (id === "dashThemeToggle") return dashToggle;
      return makeElement();
    },
    querySelectorAll(selector) {
      if (selector === "#themeSeg .theme-seg-opt") return [lightOpt, darkOpt];
      return [];
    },
    querySelector() { return makeElement(); },
    createElement() { return makeElement(); },
  },
  localStorage: {
    getItem(k) { return storage.has(k) ? storage.get(k) : null; },
    setItem(k, v) { storage.set(k, String(v)); },
  },
};
context.window.localStorage = context.localStorage;
context.window.document = context.document;

vm.createContext(context);
vm.runInContext(fs.readFileSync("static/app.js", "utf8"), context, { filename: "static/app.js" });

vm.runInContext('window.applyTheme("dark", { persist: false })', context);
assert(htmlTheme === "dark", "applyTheme should set html data-theme to dark");
assert(darkOpt.classList.contains("active"), "Settings dark option should become active");
assert(!lightOpt.classList.contains("active"), "Settings light option should become inactive");
assert(dashToggle.classList.contains("is-dark"), "Dashboard theme toggle should reflect dark state");
assert(dashToggle.getAttribute("aria-pressed") === "true", "Dashboard theme toggle should expose dark pressed state");
assert(dashToggle.getAttribute("aria-label") === "Switch to light mode", "Dashboard theme toggle should advertise the next action");

vm.runInContext("window.initThemeToggle()", context);
assert(typeof dashToggle.onclick === "function", "initThemeToggle should wire the dashboard theme toggle");
dashToggle.click();
assert(htmlTheme === "light", "dashboard theme toggle click should switch back to light");
assert(lightOpt.classList.contains("active"), "Settings light option should resync after dashboard click");
assert(!dashToggle.classList.contains("is-dark"), "Dashboard theme toggle should clear dark state after switching to light");

console.log("dashboard theme toggle tests passed");
