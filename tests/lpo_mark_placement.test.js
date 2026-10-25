/* Placement marks in the client-LPO viewer must be VISIBLE and DISTINCT.

   Three failures reported 2026-08-01, all from the same viewer:
     1. the box was empty - no way to judge whether the stamp was the right
        size before committing, which is the entire point of placing by hand;
     2. no size readout while moving/resizing;
     3. adding a signature AND a stamp appeared to apply only one. Both spawned
        at the IDENTICAL x/y, so the second sat exactly beneath the first -
        invisible, and easy to drag as if it were the only one.

   Diagnosis on the real file: the acknowledged PDF gained exactly one image,
   at the spot the signature had been dragged to. The backend merges two marks
   correctly when given two, so the loss was upstream, in the viewer.

   These are pure-logic assertions over lpo.js with a stubbed DOM - no browser.
*/
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("static/lpo.js", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");
const backend = fs.readFileSync("backend/lpo_ack.py", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const SIG_URI = "data:image/png;base64,SIGSIGSIG";
const STAMP_URI = "data:image/png;base64,STAMPSTAMP";

function makeElement(tag = "div") {
  let html = "";
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    dataset: {},
    style: {},
    className: "",
    textContent: "",
    clientWidth: 800,
    clientHeight: 1131,
    classList: {
      _s: new Set(),
      add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
      toggle() {}, contains(c) { return this._s.has(c); },
    },
    set innerHTML(v) { html = String(v || ""); },
    get innerHTML() { return html; },
    appendChild(child) { this.children.push(child); return child; },
    remove() {},
    querySelectorAll() { return []; },
    querySelector(sel) {
      // Only the selectors paintLpoMarks actually uses.
      if (sel === ".lpo-mark-del" || sel === ".lpo-mark-grip") {
        return { onclick: null, addEventListener() {} };
      }
      if (sel === ".lpo-mark-tag") {
        if (!this._tag) this._tag = { textContent: "" };
        return this._tag;
      }
      return null;
    },
    addEventListener() {},
    setAttribute() {},
  };
  return el;
}

function run(assets) {
  const wrap = makeElement("div");
  const surface = makeElement("canvas");
  /* 2026-09-12 - every page is rendered at once and a mark now lives inside
     its OWN page's box, so the stub provides one. A mark painted into `wrap`
     instead would be positioned against the whole stack rather than against
     the page it belongs to. */
  const pageBox = makeElement("div");
  pageBox.querySelector = (sel) => (sel === "canvas, img" ? surface : null);
  // paintLpoMarks clears the previous marks before repainting, so the stub has
  // to honour remove() - otherwise repaints accumulate and every count is wrong.
  // In the browser this is a descendant search, so it has to reach into the
  // page boxes, not only wrap's own children.
  wrap.querySelectorAll = (sel) => {
    if (sel !== ".lpo-mark") return [];
    const hosts = [wrap, pageBox];
    const out = [];
    hosts.forEach((h) => h.children
      .filter(c => (c.className || "").includes("lpo-mark"))
      .forEach(c => out.push(Object.assign(c, {
        remove() { const i = h.children.indexOf(c); if (i >= 0) h.children.splice(i, 1); },
      }))));
    return out;
  };
  wrap.querySelector = (sel) => (sel === "canvas, img" ? surface : null);

  const hint = makeElement("div");
  const byId = { lpoCanvasWrap: wrap, lpoVHint: hint };

  const toasts = [];
  const context = {
    console,
    setTimeout() {}, clearTimeout() {},
    atob: (s) => s,
    Image: function () { return {}; },
    FileReader: function () { return {}; },
    document: {
      readyState: "complete",
      getElementById: (id) => byId[id] || null,
      querySelector: (sel) =>
        (sel.includes("canvas") ? surface : null),
      createElement: (t) => makeElement(t),
      addEventListener() {}, removeEventListener() {},
      body: makeElement("body"),
    },
    toast: (m) => toasts.push(String(m)),
    esc: (s) => String(s == null ? "" : s),
    safe: (fn) => fn,
    api: () => ({}),
    hideAllViews() {}, setBrand() {}, showModal: async () => "no",
    withLoading: async (b, fn) => fn(),
    state: { today: { display: "1/8/2026" } },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInNewContext(source, context);

  context.lpoState.assets = assets;
  context.lpoState.viewer = {
    lpo: { client_name: "C", lpo_no: "L" },
    page: 1, pages: 1, scale: 1, marks: [], doc: null,
    isImage: false, pageMm: { w: 210, h: 297 },
    pageEls: { 1: pageBox }, pageMms: { 1: { w: 210, h: 297 } },
  };
  return { ctx: context, wrap, hint, toasts, pageBox };
}

const FULL = { has_sig: true, has_stamp: true, sig_data_uri: SIG_URI,
               stamp_data_uri: STAMP_URI,
               size: { sig_w_mm: 55, sig_h_mm: 22, stamp_w_mm: 46.5, stamp_h_mm: 29 } };

/* ---------- 3. two marks must never coincide ---------- */
{
  const { ctx, pageBox: wrap } = run(FULL);
  ctx.addLpoMark("sig");
  ctx.addLpoMark("stamp");
  const marks = ctx.lpoState.viewer.marks;
  assert(marks.length === 2, "both marks should be stored, got " + marks.length);
  assert(!(marks[0].x === marks[1].x && marks[0].y === marks[1].y),
    "the second mark must not spawn exactly under the first - that is what made "
    + "the stamp invisible and looked like it was never applied");
  assert(marks[0].kind === "sig" && marks[1].kind === "stamp",
    "kinds must survive so the backend picks the right image");
  // Every mark is painted, not just the last one.
  const painted = wrap.children.filter(c => (c.className || "").includes("lpo-mark"));
  assert(painted.length === 2, "expected 2 painted marks, got " + painted.length);
}

/* ---------- 1. the box shows the real image ---------- */
{
  const { ctx, pageBox: wrap } = run(FULL);
  ctx.addLpoMark("sig");
  ctx.addLpoMark("stamp");
  const painted = wrap.children.filter(c => (c.className || "").includes("lpo-mark"));
  assert(painted[0].innerHTML.includes(SIG_URI),
    "the signature box must render the actual signature image, not an empty outline");
  assert(painted[1].innerHTML.includes(STAMP_URI),
    "the stamp box must render the actual stamp image");
  assert(painted.every(p => p.innerHTML.includes("lpo-mark-img")),
    "each mark needs the image element the CSS sizes");
}

/* ---------- 2. live size readout in mm ---------- */
{
  const { ctx, pageBox: wrap } = run(FULL);
  ctx.addLpoMark("sig");
  const el = wrap.children.find(c => (c.className || "").includes("lpo-mark"));
  const tag = el.querySelector(".lpo-mark-tag");
  assert(/Signature/.test(tag.textContent), "readout should name the mark");
  assert(/55.*22.*mm/.test(tag.textContent),
    "readout should show the size in mm, got: " + tag.textContent);

  // Resizing updates it - the readout is what tells you to go bigger/smaller.
  const m = ctx.lpoState.viewer.marks[0];
  m.w = 100 / 210; m.h = 40 / 297;
  ctx.updateLpoMarkReadout(el, m);
  assert(/100.*40.*mm/.test(tag.textContent),
    "readout must follow a resize, got: " + tag.textContent);
}

/* ---------- default size comes from the live settings ---------- */
{
  const { ctx } = run({ ...FULL, size: { sig_w_mm: 70, sig_h_mm: 30,
                                         stamp_w_mm: 40, stamp_h_mm: 40 } });
  ctx.addLpoMark("sig");
  const m = ctx.lpoState.viewer.marks[0];
  assert(Math.abs(m.w - 70 / 210) < 1e-9, "signature width should track Settings");
  assert(Math.abs(m.h - 30 / 297) < 1e-9, "signature height should track Settings");
}

/* ---------- a missing asset is refused, not silently dropped ---------- */
{
  const { ctx, toasts } = run({ has_sig: true, has_stamp: false,
                                sig_data_uri: SIG_URI, stamp_data_uri: "", size: {} });
  ctx.addLpoMark("stamp");
  assert(ctx.lpoState.viewer.marks.length === 0,
    "a stamp with no uploaded image must not be added - the backend would skip it "
    + "and the acknowledge would quietly do less than asked");
  assert(toasts.some(t => /stamp/i.test(t)), "the refusal must be explained");
  ctx.addLpoMark("sig");
  assert(ctx.lpoState.viewer.marks.length === 1, "the signature is still allowed");
}

/* ---------- the tally makes 'I placed two' verifiable ---------- */
{
  const { ctx, hint } = run(FULL);
  ctx.addLpoMark("sig");
  ctx.addLpoMark("stamp");
  assert(/2 marks placed/.test(hint.textContent),
    "the hint should tally placed marks, got: " + hint.textContent);
  assert(/page 1/.test(hint.textContent), "the tally should say which page");
}

/* ---------- preview and merged output must use the same fit ---------- */
{
  assert(/\.lpo-mark-img[\s\S]*?object-fit:\s*contain/.test(css),
    "the preview image must use object-fit: contain");
  assert(/object-fit:contain/.test(backend.replace(/\s/g, "")),
    "lpo_ack._overlay_html must use the same fit, or the preview lies about "
    + "the merged result");
  assert(/pointer-events:\s*none/.test(css.slice(css.indexOf(".lpo-mark-img"))),
    "the image must not swallow the drag");
}

/* ---------- the viewer must open the SIGNED copy once one exists ----------

   Rewritten 2026-09-11: the full-screen popup became the `lpoDocView` screen,
   with signing done in place rather than in a second window. Every invariant
   below is the SAME one - only the code it lives in moved. */
{
  const src = fs.readFileSync("static/lpo.js", "utf8");

  // Reported 2026-08-01 as "acknowledge doesn't apply anything". It did - but
  // the viewer always re-requested the client's original, which is deliberately
  // never modified, so the stamp was nowhere to be seen.
  assert(/lpo\.ack_exists \? "ack" : "file"/.test(src),
    "opening an LPO must show the signed copy when one exists");

  // Re-signing has to restart from the original or marks stack on a stamped copy.
  {
    const at = src.indexOf("function startLpoSigning");
    assert(at > 0, "startLpoSigning is gone");
    assert(/openLpoDocFile\(lpo, "file"\)/.test(src.slice(at, at + 900)),
      "'Sign again' must reload the ORIGINAL, not the signed copy");
  }

  // The banner naming the file on screen is the actual fix for the confusion.
  assert(/lpo-v-file/.test(src) && /signed copy/.test(src)
      && /client's original/.test(src),
    "the viewer must state which file is on screen");

  // Placement tools appear only while signing - they are noise the rest of the
  // time, and they imply the document is editable when it is not.
  {
    const at = src.indexOf("lpoAddSig");
    assert(at > 0, "the signature tool is missing");
    const before = src.slice(Math.max(0, at - 400), at);
    assert(/signing \?/.test(before), "placement tools must be signing-only");
  }

  // After signing, re-read the record so the panel and the list agree, and the
  // freshly written copy is what comes back on screen.
  assert(/await reopenLpoDoc\(lpo\);/.test(src),
    "signing should reload the record so the result is visible");

  // The action must name what it does, and change once a copy exists: "Sign and
  // stamp" while there is no signed copy (the signed-copy line), "Sign again"
  // in More once there is one (2026-10-03).
  assert(/ack_exists \? item\("sign", "Sign again"/.test(src),
    "Sign again must be offered only once a signed copy exists");
  {
    const at = src.indexOf("function lpoSignedHtml");
    assert(at > 0, "the signed-copy line is gone");
    assert(/data-doc="sign">Sign and stamp/.test(src.slice(at, at + 1800)),
      "with no signed copy, the button should say Sign and stamp");
  }
}

console.log("LPO mark placement tests passed");
