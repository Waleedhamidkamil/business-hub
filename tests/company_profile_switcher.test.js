const fs = require("fs");
const vm = require("vm");

const html = fs.readFileSync("templates/index.html", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const dark = fs.readFileSync("static/dark.css", "utf8");
const appSource = fs.readFileSync("static/app.js", "utf8");
const guardSource = fs.readFileSync("static/unsaved_guard.js", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const searchPos = html.indexOf('id="dashSearch"');
const profilePos = html.indexOf('id="dashProfileSwitcher"');
const themePos = html.indexOf('id="dashThemeToggle"');
const settingsPos = html.indexOf('id="dashSettingsBtn"');
assert(searchPos < profilePos && profilePos < themePos && themePos < settingsPos,
  "profile switcher should sit between dashboard search and theme");
assert(/id="dashProfileButton"[^>]*aria-expanded="false"/.test(html),
  "profile button should expose its collapsed state");
assert(/id="dashProfileMenu"[^>]*role="menu"/.test(html),
  "profile choices should use an accessible menu");
assert(/data-profile-id="abraj"[^>]*role="menuitemradio"/.test(html),
  "Abraj should be a profile menu option");
assert(/data-profile-id="eshraqat"[^>]*role="menuitemradio"/.test(html),
  "Eshraqat should be a profile menu option");
assert(css.includes(".dash-profile-switcher"), "profile switcher should have dedicated styling");
assert(css.includes(".dash-profile-menu"), "profile menu should have dedicated styling");
assert(dark.includes(".dash-profile"), "profile switcher should have dark-mode styling");

/* The unsaved-changes rule moved into static/unsaved_guard.js on 2026-10-01
   (one copy instead of four - see tests/unsaved_guard.test.js). The profile
   switcher still has to see all four editors, so assert that each one is
   registered with the guard, in the file that owns its state. */
for (const [file, kind, dirtyFlag] of [
  ["static/app.js", "po", "state.dirtySinceSave"],
  ["static/quote.js", "quote", "quoteState.dirty"],
  ["static/dn.js", "dn", "dnState.dirty"],
  ["static/inv.js", "inv", "invState.dirty"],
]) {
  const js = fs.readFileSync(file, "utf8");
  assert(js.includes(`unsavedGuard.register("${kind}"`),
    `${file} must register "${kind}" with the shared guard, or a company switch `
    + "cannot see that editor's unsaved work");
  assert(js.includes(dirtyFlag), `${file} should still expose ${dirtyFlag} to the guard`);
}
assert(guardSource.includes("stay: true, skipConfirm: true"),
  "the guard's dirtyEditor().save must keep the user where they are (stay) and "
  + "not re-ask for confirmation");

function makeElement(attrs = {}) {
  const classes = new Set();
  return {
    hidden: false,
    dataset: attrs.dataset || {},
    textContent: "",
    src: "",
    title: "",
    onclick: null,
    _listeners: {},
    classList: {
      add(c) { classes.add(c); },
      remove(c) { classes.delete(c); },
      toggle(c, on) { if (on) classes.add(c); else classes.delete(c); },
      contains(c) { return classes.has(c); },
    },
    setAttribute(k, v) { this[k] = String(v); },
    getAttribute(k) { return this[k] || null; },
    addEventListener(type, fn) { this._listeners[type] = fn; },
    querySelectorAll() { return []; },
    querySelector() { return makeElement(); },
    contains() { return false; },
    focus() {},
  };
}

const elements = new Map();
const profileOptions = [
  makeElement({ dataset: { profileId: "abraj" } }),
  makeElement({ dataset: { profileId: "eshraqat" } }),
];
for (const id of ["dashProfileSwitcher", "dashProfileButton", "dashProfileMenu", "dashProfileLogo", "dashProfileLabel", "dashHeroCompany", "dashHeroLogo"]) {
  elements.set(id, makeElement());
}
elements.get("dashProfileButton").setAttribute("aria-expanded", "false");

const calls = [];
let reloads = 0;
let modalChoice = "cancel";
let quoteSaves = 0;
const context = {
  console,
  setTimeout(fn) { fn(); },
  clearTimeout() {},
  setInterval() { return 1; },
  clearInterval() {},
  requestAnimationFrame(fn) { fn(); },
  window: {
    addEventListener() {},
    matchMedia() { return { matches: false }; },
    location: { reload() { reloads += 1; } },
    pywebview: { api: {
      async switch_profile(id) {
        calls.push(`switch_profile:${id}`);
        return { ok: true, active_profile: id, reload: true };
      },
    } },
    profileSwitchPrompt: async () => modalChoice,
    quoteState: { dirty: false },
    dnState: { dirty: false },
    invState: { dirty: false },
    quoteSave: async () => { quoteSaves += 1; return true; },
  },
  document: {
    readyState: "loading",
    activeElement: null,
    documentElement: { setAttribute() {}, getAttribute() { return "light"; }, style: { setProperty() {} } },
    body: { classList: { add() {}, remove() {}, toggle() {} }, appendChild() {} },
    addEventListener() {},
    removeEventListener() {},
    getElementById(id) { if (!elements.has(id)) elements.set(id, makeElement()); return elements.get(id); },
    querySelectorAll(selector) {
      if (selector === "#dashProfileMenu [data-profile-id]") return profileOptions;
      return [];
    },
    querySelector() { return makeElement(); },
    createElement() { return makeElement(); },
  },
  localStorage: { getItem() { return null; }, setItem() {} },
};
context.window.document = context.document;
context.window.localStorage = context.localStorage;

vm.createContext(context);
// The guard loads before the editors in index.html; same order here.
vm.runInContext(guardSource, context, { filename: "static/unsaved_guard.js" });
vm.runInContext(appSource, context, { filename: "static/app.js" });
// quote.js is not loaded in this harness, so stand in for its registration.
// `filled` returns one row: the guard only counts an editor dirty when it has
// content, which is the 2026-10-01 rule.
vm.runInContext(`window.unsavedGuard.register("quote", {
  noun: "quotation", label: "Quotation",
  dirty: () => !!window.quoteState.dirty,
  filled: () => [{ name: "row" }],
  save: (o) => window.quoteSave(o),
});`, context);

vm.runInContext('window.applyCompanyProfile({id:"abraj", profiles:[{id:"abraj",label:"Abraj Albadr"},{id:"eshraqat",label:"Eshraqat Albadr"}], logo_data_uri:"data:image/png;base64,AA==", brand_color:"#1F3864"})', context);
assert(elements.get("dashProfileLabel").textContent === "Abraj Albadr", "active profile label should render");
assert(elements.get("dashHeroCompany").textContent === "ABRAJ Business Hub", "dashboard identity should follow profile");

context.window.quoteState.dirty = true;
modalChoice = "cancel";
vm.runInContext('window.requestProfileSwitch("eshraqat")', context);
setImmediate(async () => {
  assert(calls.length === 0, "Cancel should not call backend switching");

  modalChoice = "discard";
  await vm.runInContext('window.requestProfileSwitch("eshraqat")', context);
  assert(calls.includes("switch_profile:eshraqat"), "Discard should switch profiles");
  assert(reloads === 1, "successful switching should reload the shell");

  calls.length = 0;
  reloads = 0;
  modalChoice = "save";
  await vm.runInContext('window.requestProfileSwitch("eshraqat")', context);
  assert(quoteSaves === 1, "Save should use the active editor save function");
  assert(calls.includes("switch_profile:eshraqat"), "Save should switch after saving");
  assert(reloads === 1, "Save then switch should reload the shell");

  console.log("company profile switcher tests passed");
});
