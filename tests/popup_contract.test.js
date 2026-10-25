/* THE POPUP CONTRACT (2026-08-26) - static/popup.js

   The app grew popups one at a time and ended up with three vocabularies for
   the same idea: `.modal-*`, `.imgf-*` and `.cost-linkpicker-*`. They disagreed
   on everything a reader notices - whether Esc closes, whether the dim
   background closes, whether there is an X, how wide the panel is, whether the
   page behind scrolls, whether Tab can walk out of the dialog into the page.

   `Popup.open` is the single answer, and this file is what makes it a RULE
   rather than a helper somebody may or may not use. It RUNS the real thing
   against a DOM double, because every rule here is a behaviour, and a
   behaviour asserted by grepping the source is not asserted at all.

   The rules, each tested below:
     1. Every popup has a title, and the title is what labels it.
     2. Esc closes, the backdrop closes, and there is an X - unless the popup
        is deliberately `dismissible: false`.
     3. Focus goes in, is trapped over the WHOLE panel, and comes back out.
     4. The page behind does not scroll while a popup is open.
     5. One set of widths.
     6. One footer row; a field, else the primary action, opens focused.
     7. Body text is escaped unless `html: true` is opted into.
     8. A popup opened from a popup stacks above it and takes Esc first.
     9. `showModal`'s contract is unchanged, so its call sites are safe.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const src = fs.readFileSync(process.argv[2] || "static/popup.js", "utf8").replace(/\r\n/g, "\n");
const appJs = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8");
/* Comments in that stylesheet QUOTE the rules they warn about - "`.modal
   { direction: rtl }` silently overrode…" - so any audit of what the CSS
   actually DECLARES has to read past them, or it matches its own explanation. */
const cssCode = css.replace(/\/\*[\s\S]*?\*\//g, "");
const html = fs.readFileSync("templates/index.html", "utf8");

/* ------------------------------------------------------------------------ */
/* A DOM double deep enough to run a real dialog.                            */
/* ------------------------------------------------------------------------ */
function makeDom() {
  const listeners = {};
  const doc = {
    activeElement: null,
    createElement: mkEl,
    addEventListener: (t, fn) => { (listeners[t] = listeners[t] || []).push(fn); },
    removeEventListener: (t, fn) => {
      listeners[t] = (listeners[t] || []).filter((f) => f !== fn);
    },
    fire: (t, ev) => (listeners[t] || []).slice().forEach((f) => f(ev)),
    listenerCount: (t) => (listeners[t] || []).length,
  };

  function mkEl(tag) {
    const el = {
      tagName: String(tag).toUpperCase(),
      children: [], parent: null, style: {}, attrs: {},
      id: "", className: "", offsetParent: {}, _text: "", _html: "",
      focused: 0, _l: {},
    };
    const has = (c) => el.className.split(/\s+/).indexOf(c) >= 0;
    el.classList = {
      contains: has,
      add: (...c) => c.forEach((x) => { if (!has(x)) el.className = (el.className + " " + x).trim(); }),
      remove: (...c) => c.forEach((x) => {
        el.className = el.className.split(/\s+/).filter((y) => y && y !== x).join(" ");
      }),
      toggle: (c, on) => (on ? el.classList.add(c) : el.classList.remove(c)),
    };
    el.setAttribute = (k, v) => { el.attrs[k] = String(v); };
    el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
    el.appendChild = (c) => { c.parent = el; el.children.push(c); return c; };
    el.remove = () => {
      if (el.parent) el.parent.children = el.parent.children.filter((c) => c !== el);
      el.parent = null;
    };
    el.addEventListener = (t, fn) => { (el._l[t] = el._l[t] || []).push(fn); };
    el.fire = (t, ev) => {
      const e = { target: el, ...(ev || {}) };
      (el._l[t] || []).slice().forEach((f) => f(e));
      // A real element fires its `onX` handler too, and popup.js deliberately
      // uses `onclick` on the close button so a re-mount REPLACES the handler
      // rather than stacking another one.
      const direct = el["on" + t];
      if (typeof direct === "function") direct(e);
    };
    el.focus = () => { el.focused++; doc.activeElement = el; };
    el.contains = (n) => { for (let p = n; p; p = p.parent) if (p === el) return true; return false; };
    Object.defineProperty(el, "textContent", {
      get: () => el._text, set: (v) => { el._text = String(v); el.children = []; },
    });
    Object.defineProperty(el, "innerHTML", {
      get: () => el._html, set: (v) => { el._html = String(v); },
    });
    el.querySelectorAll = (sel) => {
      const want = sel.split(",").map((s) => s.trim());
      const out = [];
      (function walk(n) {
        n.children.forEach((c) => {
          if (want.some((w) => matches(c, w))) out.push(c);
          walk(c);
        });
      })(el);
      return out;
    };
    el.querySelector = (sel) => el.querySelectorAll(sel)[0] || null;
    return el;
  }

  function matches(el, sel) {
    // Attribute selectors matter here: the contract finds a markup-authored
    // dialog's close button with [data-popup-close].
    const attr = /^\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/.exec(sel.trim());
    if (attr) {
      const v = el.getAttribute(attr[1]);
      return v !== null && (attr[2] === undefined || v === attr[2]);
    }
    const base = sel.split(":")[0].split("[")[0];
    if (base.startsWith(".")) return el.classList.contains(base.slice(1));
    if (base) return el.tagName === base.toUpperCase();
    return false;
  }

  doc.body = mkEl("body");
  return doc;
}

function loadPopup() {
  const doc = makeDom();
  const timers = [];
  const win = { matchMedia: () => ({ matches: false }) };
  new Function("window", "document", "requestAnimationFrame", "setTimeout", "console", src)(
    win, doc,
    (fn) => fn(),
    (fn) => { timers.push(fn); return timers.length; },
    { error: () => {} });
  return { Popup: win.Popup, doc, flush: () => { while (timers.length) timers.shift()(); } };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

async function run() {
  /* Dirty forms use the same guard for every route out of a popup. */
  {
    const { Popup, doc, flush } = loadPopup();
    let allow = false, calls = 0, resolveGuard;
    const h = Popup.open({title: "Guarded form", beforeClose: () => {
      calls++; return new Promise(r => { resolveGuard = r; });
    }});
    h.panel.querySelector(".popup-close").onclick();
    doc.fire("keydown", {key:"Escape", preventDefault(){}, stopPropagation(){}});
    ok(calls === 1, "repeated dismissal shares one pending guard");
    resolveGuard(allow); await settle(); flush(); await settle();
    ok(!h.closed && doc.body.children.length === 1, "a refused close retains the form");
    h.el.fire("mousedown", {target:h.el}); h.el.fire("click", {target:h.el});
    allow = true; resolveGuard(allow); await settle(); flush(); await settle();
    ok(h.closed && doc.body.children.length === 0, "the backdrop uses the guard too");
  }
  /* ---------- 1 + 2: structure, and Esc as a way out ---------- */
  {
    const { Popup, doc, flush } = loadPopup();
    const h = Popup.open({
      title: "Delete this document?",
      body: "It cannot be undone.",
      actions: [{ label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Delete", value: "ok", cls: "primary" }],
    });
    ok(doc.body.children.length === 1, "the popup mounts on the page");
    ok(h.panel.getAttribute("role") === "dialog"
       && h.panel.getAttribute("aria-modal") === "true",
      "every popup is a real dialog to a screen reader");
    ok((h.panel.getAttribute("aria-labelledby") || "").endsWith("-title"),
      "and is LABELLED by its own title, so it announces as something");
    ok(h.panel.querySelector(".popup-title").textContent === "Delete this document?",
      "the title is shown as given");
    ok(h.panel.classList.contains("popup-md"),
      "it takes a size from the one list");
    ok(h.panel.classList.contains("modal"),
      "and keeps `.modal`, so it is literally the same box the app always had");
    ok(!!h.panel.querySelector(".popup-close"),
      "a dismissible popup has an X - often the only exit a mouse user sees");

    const btns = h.panel.querySelectorAll("button").filter(
      (b) => !b.classList.contains("popup-close"));
    ok(btns.length === 2 && btns[1].textContent === "Delete",
      "actions render in order, primary last");
    ok(btns[1].textContent === "Delete",
      "the destructive action is a WORD, so it never depends on colour");

    let got;
    h.result.then((v) => { got = v; });
    doc.fire("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
    flush();
    await settle();
    ok(got === "cancel",
      "Esc resolves the CANCEL action when there is one, so callers that "
      + "branch on 'cancel' keep working; got " + JSON.stringify(got));
    ok(doc.body.children.length === 0, "and the popup leaves the page");
    ok(doc.listenerCount("keydown") === 0,
      "the key listener must be removed on close, or every popup ever opened "
      + "keeps handling Esc for the rest of the session");
  }

  /* ---------- 2b: the backdrop, and the drag that must NOT close ---------- */
  {
    const { Popup, doc, flush } = loadPopup();
    const h = Popup.open({ title: "T", body: "b", actions: [] });
    let got, done = false;
    h.result.then((v) => { got = v; done = true; });

    // A drag that STARTS in the body and ends on the backdrop (selecting text)
    // must not dismiss: only a click that began on the backdrop counts.
    h.el.fire("mousedown", { target: h.panel });
    h.el.fire("click", { target: h.el });
    flush(); await settle();
    ok(!done, "a drag ending on the dim background must not dismiss the popup");

    h.el.fire("mousedown", { target: h.el });
    h.el.fire("click", { target: h.el });
    flush(); await settle();
    ok(done && got === null,
      "clicking the dim background closes, resolving null when there is no "
      + "cancel action; got " + JSON.stringify(got));
  }

  /* ---------- 2c: dismissible:false means ONE way out ---------- */
  {
    const { Popup, doc, flush } = loadPopup();
    const h = Popup.open({
      title: "Choose one", dismissible: false,
      actions: [{ label: "A", value: "a" }],
    });
    ok(!h.panel.querySelector(".popup-close"),
      "a popup that must be answered shows no X");
    let done = false;
    h.result.then(() => { done = true; });
    doc.fire("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
    h.el.fire("mousedown", { target: h.el });
    h.el.fire("click", { target: h.el });
    flush(); await settle();
    ok(!done,
      "neither Esc nor the backdrop may answer a question the app cannot "
      + "answer on the user's behalf");
    h.close("a"); flush(); await settle();
    ok(done, "the action itself still closes it");
  }

  /* ---------- 3: focus in, trapped, and back out ---------- */
  {
    const { Popup, doc, flush } = loadPopup();
    const opener = doc.createElement("button");
    doc.activeElement = opener;

    const h = Popup.open({
      title: "Rename",
      body: (b) => {
        const i = doc.createElement("input");
        b.appendChild(i);
      },
      actions: [{ label: "Cancel", value: "cancel" },
                { label: "Save", value: "ok", cls: "primary" }],
    });
    flush();
    const input = h.body.querySelector("input");
    ok(input.focused > 0,
      "a popup that opens with a field focuses the FIELD - that is what the "
      + "user came to type in");

    // Tab from the last focusable wraps to the first, and vice versa. The old
    // modal trapped over BUTTONS only, so a popup with an input leaked focus.
    const items = h.panel.querySelectorAll(
      "a,button,input,select,textarea").filter((e) => !e.disabled);
    const first = items[0], last = items[items.length - 1];
    ok(items.length >= 4 && items.indexOf(input) >= 0,
      "the trap covers the input as well as the buttons; got " + items.length);

    doc.activeElement = last;
    let prevented = false;
    doc.fire("keydown", { key: "Tab", shiftKey: false,
      preventDefault() { prevented = true; }, stopPropagation() {} });
    ok(prevented && doc.activeElement === first,
      "Tab off the end wraps back to the start rather than into the page behind");

    doc.activeElement = first;
    prevented = false;
    doc.fire("keydown", { key: "Tab", shiftKey: true,
      preventDefault() { prevented = true; }, stopPropagation() {} });
    ok(prevented && doc.activeElement === last, "and Shift+Tab wraps the other way");

    h.close(null); flush(); await settle();
    ok(doc.activeElement === opener,
      "focus returns to whatever opened the popup, or the user is dropped at "
      + "the top of the page");
  }

  /* ---------- 4: the page behind holds still ---------- */
  {
    const { Popup, doc, flush } = loadPopup();
    doc.body.style.overflow = "";
    const a = Popup.open({ title: "A" });
    ok(doc.body.style.overflow === "hidden",
      "the page behind must not scroll under a popup");
    const b = Popup.open({ title: "B" });
    b.close(); flush(); await settle();
    ok(doc.body.style.overflow === "hidden",
      "closing the INNER popup must not unlock the page while the outer one "
      + "is still open");
    a.close(); flush(); await settle();
    ok(doc.body.style.overflow === "",
      "and the last one to close puts the page back exactly as it was");
  }

  /* ---------- 4b: a popup that opens while the last one is still fading ----
     (2026-10-09) Waleed: "sometimes I can't scroll in Insights and other
     places and have to restart the app". A closing popup leaves the stack at
     once but releases the page only after its 180ms fade. A popup opened in
     that window - Submit in the document preview closes it and opens the
     fulfilment popup a bridge call later - found the page already locked,
     remembered "hidden" as the page's own setting, and put "hidden" back
     when IT closed. The page then never scrolled again. */
  {
    const { Popup, doc, flush } = loadPopup();
    doc.body.style.overflow = "";
    const a = Popup.open({ title: "Preview" });
    a.close();                         // leaves the stack; fade still running
    const b = Popup.open({ title: "Fulfilment" });
    flush(); await settle();           // A's fade ends while B is up
    ok(doc.body.style.overflow === "hidden",
      "B is open, so the page stays locked when A's fade finishes");
    b.close(); flush(); await settle();
    ok(doc.body.style.overflow === "",
      "when the last popup closes the page scrolls again - it must not restore "
      + "the lock a fading popup was still holding");
  }

  /* ---------- 5: one set of widths ---------- */
  {
    const { Popup } = loadPopup();
    ok(Object.keys(Popup.SIZES).join(",") === "sm,md,lg,xl,full",
      "five sizes, no more - 'a bit wider than that one' is how a UI stops "
      + "looking designed. `full` is in the LIST rather than being an escape "
      + "hatch, and it is only for popups that show a document");
    const odd = Popup.open({ title: "T", size: "enormous" });
    ok(odd.panel.classList.contains("popup-md"),
      "an unknown size falls back to the default rather than rendering "
      + "unstyled at whatever width the content wants");
    for (const s of Object.keys(Popup.SIZES)) {
      ok(new RegExp(`\\.popup-${s}\\s*\\{`).test(css), `.popup-${s} needs a width rule`);
    }
  }

  /* ---------- 6: the primary opens focused when there is no field ---------- */
  {
    const { Popup, flush } = loadPopup();
    const h = Popup.open({
      title: "Save?", body: "text only",
      actions: [{ label: "No", value: "no" }, { label: "Yes", value: "yes", cls: "primary" }],
    });
    flush();
    const btns = h.panel.querySelectorAll("button").filter(
      (b) => !b.classList.contains("popup-close"));
    ok(btns[1].focused > 0,
      "with nothing to type in, focus lands on the primary action");
  }

  /* ---------- 7: text by default, markup only on request ---------- */
  {
    const { Popup } = loadPopup();
    const evil = '<img src=x onerror="boom()">';
    const plain = Popup.open({ title: "T", body: evil });
    ok(plain.body.textContent === evil && plain.body.innerHTML === "",
      "a body is TEXT unless asked otherwise - client and document data flows "
      + "through here");
    const rich = Popup.open({ title: "T", body: "<b>ok</b>", html: true });
    ok(rich.body.innerHTML === "<b>ok</b>",
      "`html: true` is the opt-in, and the caller owns the escaping then");
  }

  /* ---------- 8: a popup opened from a popup ---------- */
  {
    const { Popup, doc, flush } = loadPopup();
    const outer = Popup.open({ title: "Outer", actions: [] });
    const inner = Popup.open({ title: "Inner", actions: [] });
    ok(Popup.count() === 2, "both are open");
    ok(Number(inner.el.style.zIndex) > Number(outer.el.style.zIndex),
      "the inner popup sits above its parent");
    ok(Popup.top() === inner, "and is the top of the stack");

    let innerDone = false, outerDone = false;
    inner.result.then(() => { innerDone = true; });
    outer.result.then(() => { outerDone = true; });
    doc.fire("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
    flush(); await settle();
    ok(innerDone && !outerDone,
      "Esc closes the INNERMOST popup only - closing both would throw away "
      + "work the user is still doing in the one behind");
    ok(Popup.top() === outer, "and the parent becomes the top again");
  }

  /* ---------- 8b: close is idempotent ---------- */
  {
    const { Popup, flush } = loadPopup();
    const h = Popup.open({ title: "T" });
    let n = 0;
    h.result.then(() => { n += 1; });
    h.close("a"); h.close("b"); h.close("c");
    flush(); await settle();
    ok(n === 1, "one close path: a second close must not re-resolve or re-fire");
    ok(Popup.count() === 0, "and the stack is left clean");
  }

  /* ---------- 9: showModal's contract is unchanged ---------- */
  {
    ok(/function showModal\(\{ title, body, buttons, html/.test(appJs),
      "showModal must keep its exact signature - 55 call sites depend on it");
    ok(/window\.Popup\.open\(/.test(appJs),
      "...and must delegate rather than keep a second implementation, or the "
      + "two drift apart again");
    const at = appJs.indexOf("function showModal(");
    const body = appJs.slice(at, appJs.indexOf("\n}", at));
    ok(/\.result;/.test(body), "it still returns a Promise of the chosen value");
    ok(/modalBody/.test(body) && /modalActions/.test(body),
      "the legacy ids are stamped onto the live panel: master_data_fill wires "
      + "#modalBody, and inv.js / item_master.js drive #modalActions while the "
      + "dialog is open");
    ok(!/id="modalBackdrop"/.test(html),
      "the static confirm-modal markup must be GONE, or two elements share "
      + "those ids and getElementById returns the dead one");
    // Compare the SCRIPT TAGS, not the first mention: both files are named in
    // comments long before the bottom of the page.
    const tags = [...html.matchAll(/<script src="\.\.\/static\/([\w.\/-]+)"/g)]
      .map((m) => m[1]);
    ok(tags.includes("popup.js"), "popup.js is loaded");
    ok(tags.indexOf("popup.js") >= 0 && tags.indexOf("popup.js") < tags.indexOf("app.js"),
      "and loaded BEFORE app.js, which builds showModal on top of it; got "
      + tags.slice(0, 6).join(", "));
  }

  /* ---------- 9b: `mount` - markup-authored dialogs get the same rules ----- */
  {
    const { Popup, doc, flush } = loadPopup();
    const opener = doc.createElement("button");
    doc.activeElement = opener;

    // A dialog authored in HTML: a backdrop with a panel and a close X.
    const bd = doc.createElement("div");
    bd.className = "modal-backdrop";
    bd.hidden = true;
    const panel = doc.createElement("div");
    panel.className = "modal";
    const x = doc.createElement("button");
    x.setAttribute("data-popup-close", "");
    const field = doc.createElement("input");
    panel.appendChild(x); panel.appendChild(field);
    bd.appendChild(panel);
    doc.body.style.overflow = "";

    let closedWith = "nope";
    const h = Popup.mount(bd, { onClose: (v) => { closedWith = v; } });
    flush();
    ok(bd.hidden === false, "mount shows the dialog");
    ok(doc.body.style.overflow === "hidden",
      "and locks the page behind it, exactly like a built popup");
    ok(Popup.count() === 1, "it joins the same stack, so Esc order is shared");
    ok(field.focused > 0, "focus moves into it");

    doc.fire("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
    flush();
    ok(bd.hidden === true && h.closed, "Esc closes it");
    ok(closedWith === null, "and the owner is told");
    ok(doc.body.style.overflow === "" , "the page is released");
    ok(doc.activeElement === opener, "focus goes back to the opener");

    // Re-opening must work - and the X must still close it. A listener captured
    // on the FIRST mount would be closing a handle that settled long ago.
    const h2 = Popup.mount(bd);
    flush();
    ok(bd.hidden === false && Popup.count() === 1, "it can be opened again");
    x.fire("click");
    flush();
    ok(h2.closed && bd.hidden === true,
      "the X closes the SECOND opening too - wiring it once and never again is "
      + "how a close button silently dies");

    // Not dismissible: a run in flight.
    const h3 = Popup.mount(bd, { dismissible: false });
    flush();
    doc.fire("keydown", { key: "Escape", preventDefault() {}, stopPropagation() {} });
    flush();
    ok(!h3.closed, "a non-dismissible mounted dialog ignores Esc");
    h3.close(null);
  }

  /* ---------- 9c: nothing builds its own overlay any more ---------- */
  {
    const files = fs.readdirSync("static").filter((f) => f.endsWith(".js")
      && f !== "popup.js" && !f.includes("min"));
    const offenders = [];
    for (const f of files) {
      const s = fs.readFileSync("static/" + f, "utf8");
      // A backdrop being CONSTRUCTED in JS is the thing this contract replaced.
      if (/className\s*=\s*["'`][^"'`]*backdrop/.test(s)) offenders.push(f);
    }
    ok(offenders.length === 0,
      "every popup goes through Popup.open or Popup.mount. These still build "
      + "their own overlay, which is how the app ended up with three different "
      + "sets of popup rules: " + offenders.join(", "));

    // ...and the five markup-authored ones share the structure.
    const backdrops = [...html.matchAll(/<div[^>]*class="[^"]*modal-backdrop[^"]*"[^>]*>/g)]
      .map((m) => m[0]);
    ok(backdrops.length >= 5, "the markup-authored dialogs are still there");
    for (const b of backdrops) {
      ok(/popup-backdrop/.test(b),
        "every authored backdrop carries the shared class: " + b.slice(0, 80));
    }
    ok(!/imgf-backdrop/.test(html),
      "the Find-pictures dialog had its own vocabulary; it uses the shared box now");
    ok((html.match(/data-popup-close/g) || []).length >= 4,
      "an authored dialog gets its X from the contract");
  }

  /* ---------- 9d: the look, and what it must not become again ---------- */
  {
    const at = css.indexOf("THE POPUP CONTRACT (2026-08-26)");
    assert(at >= 0, "the popup CSS section is missing");
    const block = css.slice(at, css.indexOf("/* ---------- Fill from Master Data", at));
    const rule = (sel) => {
      const i = block.indexOf(sel + " {");
      assert(i >= 0, sel + " rule not found");
      return block.slice(i, block.indexOf("}", i));
    };

    /* THE PANEL IS OPAQUE. Glass was tried twice here and taken out twice:
       these popups carry item tables, forms and money over a dense editor, and
       every gram of translucency spends legibility on decoration. If someone
       reaches for it again, this is the assertion that asks them to explain
       why THIS app is the one where see-through numbers are a good idea. */
    const panel = rule(".popup");
    ok(/background:\s*var\(--surface\)/.test(panel),
      "the panel is the solid surface token. A dialog full of numbers over a "
      + "dense editor has to read instantly, on any background, in both themes");
    ok(!/backdrop-filter/.test(panel),
      "the PANEL does not filter anything - it is opaque, so there is nothing "
      + "to see through it");
    ok(!/rgba\(255, 255, 255, 0\.[0-6]/.test(panel),
      "...and it is not a white veil either");

    /* Depth is what makes an opaque panel look lifted rather than pasted on. */
    const shadows = (panel.match(/rgba\([^)]*\)/g) || []).length;
    ok(shadows >= 3,
      "the shadow is LAYERED - a tight contact shadow for edge weight plus "
      + "wide ones for lift. One blurry shadow always reads as a sticker; got "
      + shadows + " layers");
    ok(/backdrop-filter:\s*blur/.test(rule(".popup-backdrop")),
      "the PAGE blurs gently instead, so the panel is unmistakably in front "
      + "without the panel having to be see-through");

    /* The three regions have to read at a glance. */
    ok(/border-bottom:\s*1px solid var\(--border\)/.test(rule(".popup-head")),
      "a hairline under the head");
    const actions = rule(".popup-actions");
    ok(/border-top:\s*1px solid var\(--border\)/.test(actions)
       && /background:\s*var\(--surface-sunk\)/.test(actions),
      "and a tinted footer, so the decision row is visibly not part of the "
      + "content above it - the tint is also what hides the seam when the body "
      + "scrolls under it");

    /* Smaller buttons, but still hittable. */
    const btns = block.slice(block.indexOf(".popup-actions button"));
    ok(/min-height:\s*32px/.test(btns),
      "action buttons got smaller, but 32px is the floor: below that a button "
      + "stops being easy to hit, which is not a trade worth making for looks");
    const fsz = /font-size:\s*([\d.]+)px/.exec(btns);
    ok(fsz && Number(fsz[1]) >= 12,
      "and the label stays at least 12px - got " + (fsz && fsz[1]));
    ok(/\.modal-actions\.popup-actions button \{[^}]*min-width:\s*0/.test(block),
      "the old 100px minimum must be beaten explicitly, or three buttons in a "
      + "row still read as a toolbar");

    /* DIRECTION. The document is `<html dir="rtl">`, and an AUTHOR rule setting
       `direction` on the panel beats the UA rule behind the `dir` attribute.
       `.modal { direction: rtl }` did exactly that: fifteen popups asked for
       `dir="ltr"` and rendered right-to-left anyway, which is why their buttons
       and their X ended up on opposite sides and why English text in the Terms
       editor was right-aligned. Popups that set NOTHING got LTR from a
       `:not([dir])` rule, so the two groups were perfectly inverted. */
    ok(!/\.modal \{[^}]*direction:/.test(cssCode),
      "`.modal` must not set `direction`. It is an author rule on the same "
      + "element as the `dir` attribute, so it silently overrides it - that is "
      + "the bug that put fifteen English dialogs into RTL");
    ok(/\.popup \{[^}]*direction:\s*ltr/.test(block),
      "popups are LTR by default - the app's dialogs are English");
    ok(/\.popup\.modal-legacy \{\s*direction:\s*rtl/.test(block),
      "...except showModal's Arabic confirmations");
    ok(/\.popup\[dir="ltr"\] \{\s*direction:\s*ltr/.test(block)
       && /\.popup\[dir="rtl"\] \{\s*direction:\s*rtl/.test(block),
      "an explicit `dir` on the panel must win, so it is keyed on the attribute");
    ok(block.indexOf('.popup[dir="rtl"]') > block.indexOf(".popup.modal-legacy {"),
      "and those come LAST, or the class rules beat the attribute again");

    /* Nothing else may set direction on a popup, or we are back where we
       started with two rules fighting over the same element. */
    const blockCode = block.replace(/\/\*[\s\S]*?\*\//g, "");
    // `flex-direction` is a different property; only bare `direction` counts.
    const dirOffenders = (blockCode.match(/^[^\n@{}]*\{[^}]*[;{\s]direction:[^}]*\}/gm) || [])
      .filter((r) => !/^\.popup(\.modal-legacy| \{|\[dir)/.test(r.trim()));
    ok(dirOffenders.length === 0,
      "only the four .popup direction rules may set direction in this section; "
      + "got " + JSON.stringify(dirOffenders.map((r) => r.split("{")[0].trim())));

    /* Same trap, different property: `.modal-actions` and `.popup-actions` sit
       on the same element at the same specificity, so whichever comes later in
       the file wins. Layout belongs to ONE of them. */
    ok(!/\.modal-actions \{[^}]*(justify-content|flex-direction)/.test(cssCode),
      "`.modal-actions` must not set layout too - both classes are on every "
      + "action row at equal specificity, so a reorder of this file would flip "
      + "every popup's buttons to the other side");

    /* Dark needs its own shadow: a light-theme shadow is invisible on a dark
       ground and the panel would float with no edge at all. */
    ok(/html\[data-theme="dark"\] \.popup \{[^}]*box-shadow/.test(block),
      "the dark panel restates its shadow, or it has no edge against the page");

    /* Nothing left over from the two glass attempts. */
    ok(!/popupGlass/.test(css) && !/popupGlass/.test(html),
      "the SVG lens filter must be gone from both files, not left orphaned");
    ok(!/\.popup::before/.test(css),
      "and so must the specular sheen it belonged to");
  }

  /* ---------- 9e: ONE placement, mirrored by direction ---------- */
  {
    const { Popup, flush } = loadPopup();

    /* The X is the LAST child of the head in every popup - after the icon, the
       heading and the toolbar. `direction` then puts it top-right in an LTR
       popup and top-left in an Arabic one, which is the correct mirror rather
       than a different arrangement. A Close that moves between popups makes
       the user hunt for it every time. */
    const h = Popup.open({
      title: "T", icon: "<svg></svg>", badge: "DN",
      toolbar: "<button>zoom</button>",
      actions: [{ label: "Cancel", value: "cancel" },
                { label: "Save", value: "ok", cls: "primary" }],
    });
    flush();
    const headKids = h.panel.querySelectorAll(".popup-head")[0].children;
    const order = headKids.map((c) => c.className.split(" ")[0]);
    ok(order[order.length - 1] === "popup-close",
      "the X is the LAST thing in the head, whatever else is in there - got "
      + order.join(" > "));
    ok(order[0] === "popup-icon" && order.indexOf("popup-heading") === 1,
      "and the icon and heading lead, so the title never moves either; got "
      + order.join(" > "));

    /* The primary action is the LAST button, so `flex-end` puts it at the
       trailing edge: bottom-right in LTR, bottom-left in Arabic. */
    const btns = h.panel.querySelectorAll("button").filter(
      (b) => !b.classList.contains("popup-close") && b.textContent !== "zoom");
    ok(btns[btns.length - 1].classList.contains("primary"),
      "the primary action is last in the row (rule 6), which is what makes "
      + "one CSS rule enough for both directions");
    h.close(null);

    /* ...and there must be no per-popup override of either. This is the
       assertion that keeps "standard placement" true rather than aspirational. */
    const at = css.indexOf("THE POPUP CONTRACT (2026-08-26)");
    const block = css.slice(at, css.indexOf("/* ---------- Fill from Master Data", at));
    ok(/\.popup-actions \{[^}]*justify-content:\s*flex-end/.test(block),
      "actions sit at the trailing edge - ONE rule, mirrored by direction. In "
      + "an English popup that is: X top-RIGHT, name top-LEFT, buttons "
      + "bottom-RIGHT; an Arabic popup mirrors all three");
    ok(!/\.popup[^{]*\.popup-actions\s*\{[^}]*(justify-content|flex-direction)/.test(
         block.replace(/\.popup-actions \{[^}]*\}/, "")),
      "no popup may override where its actions sit. The Arabic dialogs mirror "
      + "because their `direction` is rtl, not because they arrange themselves "
      + "differently - that distinction is the whole point");

    /* The markup-authored dialogs follow the same rule, so their footers must
       carry the shared class rather than styling themselves. */
    const authored = [...html.matchAll(/class="modal-actions([^"]*)"/g)].map((m) => m[1]);
    ok(authored.length > 0 && authored.every((c) => /popup-actions/.test(c)),
      "every authored footer is a `.popup-actions` too, or it places its "
      + "buttons on its own terms; got " + JSON.stringify(authored));
  }

  /* ---------- 9f: a popup sized for what it is ---------- */
  {
    const quote = fs.readFileSync("static/quote.js", "utf8").replace(/\r\n/g, "\n");
    const at = quote.indexOf("function openTermsEditor(");
    assert(at >= 0, "the terms editor is missing");
    const body = quote.slice(at, quote.indexOf("\n}\n", at));
    ok(/size:\s*"xl"/.test(body),
      "the Terms editor is a WRITING surface, not a message - it takes the "
      + "biggest size on the list so a dozen terms are readable while they are "
      + "being edited");
    ok(/<textarea/.test(body) && !/type="text"/.test(body),
      "and its rows are textareas: terms run to two or three lines, and a "
      + "single-line box hides everything past the caret");
    ok(/scrollHeight/.test(body),
      "the field grows with the text rather than scrolling inside itself");
    ok(/querySelectorAll\("\.terms-row textarea"\)/.test(quote),
      "SAVE must read the textareas. Reading `input` here after the change "
      + "would have written an EMPTY terms list without a word of complaint");
    ok(/\.terms-row textarea \{/.test(css),
      "and the textarea needs its own rule, or it renders at the browser default");
  }

  /* ---------- 9g: `className` lands on the PANEL, and every binder knows it
     ----------------------------------------------------------------------
     `Popup.open({className})` puts the class on the panel itself, and the
     handle hands back that panel. A binder that then looks for its own class
     as a DESCENDANT gets `null` and throws on its first `root.querySelector`,
     which unwires the ENTIRE dialog - Save, Cancel, every field - with nothing
     but a console error to show for it. That is exactly what happened to the
     per-doc-type settings modal in the 2026-08-26 migration: the invoice's
     whole Bank accounts editor was dead, along with Save on all three types,
     while `openPoSettings` beside it had been corrected.
     ---------------------------------------------------------------------- */
  {
    const pop = fs.readFileSync("static/popup.js", "utf8");
    ok(/panel\.className = `modal popup popup-\$\{size\}\$\{s\.className \? " " \+ s\.className : ""\}`/.test(pop),
      "className must go on the PANEL - this is what makes the descendant "
      + "lookup below wrong, so pin it here too");

    const ds = fs.readFileSync("static/doc_settings.js", "utf8");
    // Every settings binder in the file - discovered, not listed, so a new one
    // cannot be added without meeting the rule.
    const binders = (ds.match(/^(?:async )?function (bindDocSettingsModal|open\w*Settings)\(/gm) || [])
      .map(m => m.replace(/^(?:async )?function /, "").replace(/\($/, ""))
      .filter(n => n !== "openDocSettings");   // the opener, not a binder
    ok(binders.length >= 2, "expected at least two settings binders, found " + binders.join(", "));
    for (const name of binders) {
      const at = ds.indexOf("function " + name + "(");
      ok(at >= 0, name + " must exist in doc_settings.js");
      // The body runs to the next top-level function - these modals carry a
      // very large markup template, so a fixed slice misses the binding.
      const next = ds.indexOf("\nfunction ", at + 1);
      const body = ds.slice(at, next > at ? next : ds.length);
      const rootLine = (body.match(/^\s*const root = .*$/m) || [""])[0];
      ok(/const root = backdrop;/.test(rootLine),
        name + " must bind to the panel it was handed, not hunt for its own "
        + "class inside it. Found: " + JSON.stringify(rootLine.trim()));
    }
    ok(!/backdrop\.querySelector\("\.ds-modal"\)/.test(ds),
      "no doc-settings binder may look for `.ds-modal` inside the panel - the "
      + "panel IS `.ds-modal`, so that returns null");

    /* The invoice's bank editor used to live in this modal, and these three
       checks pinned its container, its add button and its route to the save
       payload. It MOVED to Settings > Payments on 2026-09-21: a quotation now
       takes its cost exchange rate from the client's receiving account, so the
       account list was never an invoice-only thing, and two editors for one
       list is how they drift apart.

       What is pinned here now is that it did not come BACK, and that the modal
       says where it went. `tests/payments_settings_tab.test.js` owns the new
       editor. */
    ok(!/id="dsBanks"/.test(ds) && !/id="dsAddBank"/.test(ds),
      "the bank editor belongs to Settings > Payments now; a second one here "
      + "would drift from it");
    ok(!/payload\.bank_info = /.test(ds),
      "and this modal must not write the account list any more");
    ok(/Settings/.test(ds) && /Payments/.test(ds),
      "the invoice settings modal must say where the accounts moved to");
  }

  /* ---------- 10: the rule is written down where it is enforced ---------- */
  {
    ok(/THE POPUP CONTRACT/.test(src), "the file states the contract it enforces");
    for (const word of ["Esc", "focus", "title", "scroll"]) {
      ok(new RegExp(word, "i").test(src), `the rules must name ${word}`);
    }
    ok(fs.existsSync("docs/14-UI-CONTRACTS.md"),
      "a shared rule needs a home in the map, or the next person re-invents it");
  }

  console.log(`popup_contract.test.js: ${checks} checks passed`);
}

run().catch((e) => { console.error(e.message); process.exit(1); });
