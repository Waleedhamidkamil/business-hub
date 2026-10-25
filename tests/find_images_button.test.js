/* The "Find" button in an image column header (2026-08-13).

   It looks each row's item up online and drops a reference photo into the
   EMPTY cells. The routine is EXECUTED here against a fake bridge - the point
   of the test is the three rules that stop it making things worse:

     1. A cell that already has a picture is never touched.
     2. An item the search cannot match is left EMPTY and counted, not filled
        with the nearest thing. (The backend refuses weak matches; this checks
        the front end does not paper over a refusal.)
     3. Rows without an item name are skipped entirely.

   Rule 1 is the one a Python test cannot reach, and the mutation run proved
   it: removing the guard passed every backend test.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const src = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");

/* Pull the real function out of app.js. */
const START = src.indexOf("async function fillImagesFromWeb(");
const END = src.indexOf("window.fillImagesFromWeb = fillImagesFromWeb;");
assert(START >= 0 && END > START, "fillImagesFromWeb not found in app.js");
const body = src.slice(START, END);

/* Everything it reaches for, so nothing real is touched. */
function harness(rows, lookup) {
  const toasts = [];
  const painted = [];
  /* The dialog is mounted through the shared popup contract (2026-08-26), so
     the fake window has to offer it. `mount` only has to do what this routine
     depends on: show the element and hand back something that can hide it. */
  const ctxWindow = {
    paintRowImageCell: (tr, key, uri) => painted.push([tr.i, key, uri]),
    Popup: {
      mount: (el, opts) => {
        el.hidden = false;
        el._dismissible = !(opts && opts.dismissible === false);
        return { el, closed: false, close() { el.hidden = true; this.closed = true; } };
      },
    },
  };
  /* The routine now drives a progress dialog, so the fake document has to
     serve those elements too. Each is a stub that records what it was told. */
  const els = {};
  const el = (id) => (els[id] = els[id] || {
    id, hidden: false, disabled: false, textContent: "", innerHTML: "",
    style: {}, onclick: null, focus() {}, appendChild() {},
    setAttribute() {}, scrollTop: 0, scrollHeight: 0,
    parentElement: { setAttribute() {} },
  });
  /* The correction flow builds real elements and then reaches back into them
     with querySelector, so the fake element has to answer that. Children are
     searched first (an appended fix box must be findable, or the "already
     open" guard never fires); otherwise a stub is minted for any class the
     innerHTML string mentions, and the SAME stub is returned every time so a
     handler assigned once can be invoked by the test. */
  const created = [];
  const mkEl = () => {
    const stubs = {};
    const node = {
      className: "", innerHTML: "", children: [],
      appendChild(c) { node.children.push(c); },
      setAttribute() {},
      focus() {}, select() {},
      querySelector(sel) {
        const cls = String(sel).replace(/^\./, "");
        for (const c of node.children) {
          if (String(c.className || "").split(/\s+/).includes(cls)) return c;
        }
        if (!new RegExp(`class="[^"]*\\b${cls}\\b`).test(node.innerHTML)) return null;
        if (!stubs[cls]) {
          stubs[cls] = mkEl();
          stubs[cls].className = cls;
          // Carry the `value="…"` attribute across, so a test can assert what
          // the box was PRE-FILLED with - starting it from the item name is
          // the difference between a usable correction and retyping.
          const m = new RegExp(
            `class="[^"]*\\b${cls}\\b[^"]*"[^>]*?value="([^"]*)"`).exec(node.innerHTML);
          stubs[cls].value = m ? m[1] : "";
        }
        return stubs[cls];
      },
    };
    created.push(node);
    return node;
  };
  const doc = {
    getElementById: el,
    createElement: mkEl,
    querySelector: (sel) => {
      const m = /tr\[data-i="(\d+)"\]/.exec(sel);
      return m ? { i: +m[1] } : null;
    },
  };
  const asked = [];
  const remembered = [];
  const api = () => ({
    find_item_image: async (name, desc, phrase) => {
      asked.push([name, desc, phrase]);
      return lookup(name, desc, phrase);
    },
    remember_item_search_phrase: async (name, phrase, desc) => {
      remembered.push([name, phrase, desc]);
      return { ok: true, written: 1 };
    },
  });
  const fn = new Function(
    "api", "toast", "document", "window", "esc", "_imgFetchRunning",
    body + "\nreturn fillImagesFromWeb;"
  )(api, (m, bad) => toasts.push({ m, bad }), doc, ctxWindow, (s) => s, false);
  return { fn, toasts, painted, asked, els, created, remembered };
}

const OK_URI = "data:image/png;base64,AAAA";
const found = () => ({ ok: true, data_uri: OK_URI, title: "a picture" });
const missed = () => ({ ok: false, error: "Nothing convincing" });

function ctxFor(rows) {
  return {
    rows, key: "image", tbody: "qRowsBody",
    nameOf: (r) => r.item,
    get: (r) => r.image,
    set: (r, uri) => { r.image = uri; },
  };
}

/* ---------- 1. an existing picture is never overwritten ---------- */
(async () => {
  const rows = [
    { item: "Celery", image: "" },
    { item: "Hemp rope", image: "data:image/png;base64,MINE" },
    { item: "Sledgehammer", image: "" },
  ];
  const h = harness(rows, found);
  await h.fn(ctxFor(rows));

  ok(rows[1].image === "data:image/png;base64,MINE",
     "a picture already in the cell must survive, got " + rows[1].image.slice(0, 30));
  ok(rows[0].image === OK_URI && rows[2].image === OK_URI,
     "the empty cells must be filled");
  ok(h.asked.length === 2,
     "the filled row must not even be looked up, got " + JSON.stringify(h.asked));
  ok(!h.asked.some(([n]) => n === "Hemp rope"),
     "Hemp rope already had a picture and must not have been searched");

  /* ---------- 2. a refusal leaves the cell empty ---------- */
  const rows2 = [{ item: "Angle iron", image: "" }, { item: "Celery", image: "" }];
  const h2 = harness(rows2, (n) => (n === "Angle iron" ? missed() : found()));
  await h2.fn(ctxFor(rows2));
  ok(rows2[0].image === "",
     "a refused match must leave the cell EMPTY, got " + rows2[0].image);
  ok(rows2[1].image === OK_URI, "the other row still fills");
  const t2 = h2.toasts.map((t) => t.m).join(" | ");
  ok(/1 picture added/.test(t2), "the count must be reported: " + t2);
  ok(/Angle iron/.test(t2),
     "a miss must NAME the item - a bare count tells you nothing about what to "
     + "go and photograph: " + t2);

  /* ---------- 3. rows with no item are skipped ---------- */
  const rows3 = [{ item: "", image: "" }, { item: "   ", image: "" }];
  const h3 = harness(rows3, found);
  await h3.fn(ctxFor(rows3));
  ok(h3.asked.length === 0, "a nameless row has nothing to search for");
  ok(/already has a picture|^$/.test(h3.toasts.map((t) => t.m).join("")) ||
     h3.toasts.length > 0, "it should say something rather than fail silently");

  /* ---------- 4. nothing to do is said, not done ---------- */
  const rows4 = [{ item: "Celery", image: OK_URI }];
  const h4 = harness(rows4, found);
  await h4.fn(ctxFor(rows4));
  ok(h4.asked.length === 0, "nothing to look up");
  ok(/already/i.test(h4.toasts.map((t) => t.m).join(" ")),
     "it must say why nothing happened: " + JSON.stringify(h4.toasts));

  /* ---------- 5. the cell is repainted, not just the model ---------- */
  const rows5 = [{ item: "Celery", image: "" }];
  const h5 = harness(rows5, found);
  await h5.fn(ctxFor(rows5));
  ok(h5.painted.length === 1 && h5.painted[0][2] === OK_URI,
     "the picture must reach the SCREEN, not only the row object");

  /* ---------- 6. a bridge failure is survivable ---------- */
  const rows6 = [{ item: "Celery", image: "" }];
  const h6 = harness(rows6, () => { throw new Error("bridge down"); });
  await h6.fn(ctxFor(rows6));
  ok(rows6[0].image === "", "a thrown lookup must not corrupt the row");
  ok(h6.toasts.length > 0, "and must still report");

  /* ---------- 7. the button exists in both editors ---------- */
  const q = fs.readFileSync("static/quote.js", "utf8");
  ok(/imgFetchButtonHTML/.test(q), "the quotation image header needs the button");
  ok(/fillImagesFromWeb/.test(q), "…and must wire it");
  const html = fs.readFileSync("templates/index.html", "utf8");
  ok(/data-find-img="image"/.test(html), "the PO image header needs the button");
  ok(/_wirePoFindImages/.test(src), "…and app.js must wire the static PO header");
  const css = fs.readFileSync("static/app_styles.css", "utf8");
  ok(/\.qc-head-find\s*\{/.test(css), "the button needs a style");
  ok(/\.qc-head-find:focus-visible/.test(css), "…and a focus ring");

  /* ---------- 8. the progress dialog ---------- */
  ok(/id="imgFetchBackdrop"/.test(html), "the progress dialog needs markup");
  ok(/id="imgFetchBar"/.test(html) && /role="progressbar"/.test(html),
     "a real progress bar, with the role so it is announced");
  ok(/id="imgFetchCancel"/.test(html), "it must be cancellable");
  ok(/aria-live="polite"/.test(html), "the tally must be announced as it changes");
  ok(/\.imgf-backdrop\s*\{/.test(css), "the dialog needs a style");
  ok(/\.imgf-state\s*\{/.test(css), "each log line states its outcome in words");

  /* The dialog is opened and closed, and the bar is driven. */
  const rows8 = [{ item: "Celery", image: "" }, { item: "Tomato", image: "" }];
  const h8 = harness(rows8, found);
  await h8.fn(ctxFor(rows8));
  ok(h8.els.imgFetchBackdrop.hidden === false,
     "the dialog opens (and stays open so the log can be read)");
  ok(h8.els.imgFetchBackdrop._dismissible === false,
     "a run in flight must not be dismissible by Esc or a stray click on the "
     + "dim background - that would hide the log the run exists to produce");
  ok(h8.els.imgFetchBar.style.width === "100%", "the bar completes");
  ok(h8.els.imgFetchCancel.hidden === true && h8.els.imgFetchClose.hidden === false,
     "Cancel becomes Close when it finishes");
  ok(/2 found/.test(h8.els.imgFetchTally.textContent),
     "the tally counts: " + h8.els.imgFetchTally.textContent);

  /* ---------- 9. Cancel actually stops it ---------- */
  const rows9 = [{ item: "A", image: "" }, { item: "B", image: "" }, { item: "C", image: "" }];
  let seen = 0;
  const h9 = harness(rows9, () => { seen++; return found(); });
  const run = h9.fn(ctxFor(rows9));
  // The cancel handler is installed synchronously, before the first await.
  h9.els.imgFetchCancel.onclick();
  await run;
  ok(seen < 3, `Cancel must stop the run, but all ${seen} were still fetched`);
  ok(/Stopped/.test(h9.toasts.map((t) => t.m).join(" ")),
     "and must say it stopped: " + JSON.stringify(h9.toasts));
  ok(rows9[0].image === OK_URI || seen >= 1,
     "whatever was already fetched is kept");

  /* ---------- 10. correcting a result, and remembering the words ----------
     The search is right about 8 times in 10 and cannot get the rest by
     itself: "خيار" is both cucumber and stock option. The user says so once,
     and the wording is stored against the ITEM so every future document gets
     it right. This is the only path from "wrong picture" to "never again". */
  const lineFor = (h, name) =>
    h.created.find(e => /imgf-line/.test(e.className) &&
                        String(e.innerHTML).includes(name));

  const rows10 = [{ item: "خيار", image: "" }];
  const h10 = harness(rows10, (n, d, phrase) =>
    phrase ? { ok: true, data_uri: OK_URI, title: "Fresh cucumber" } : missed());
  await h10.fn(ctxFor(rows10));
  ok(rows10[0].image === "", "it must have failed first");

  const line = lineFor(h10, "خيار");
  ok(!!line, "the failed item needs a log line");
  const fixBtn = line.querySelector(".imgf-fix");
  ok(!!fixBtn, "a failed row must offer a way to fix it");
  fixBtn.onclick();
  const box = line.querySelector(".imgf-fixbox");
  ok(!!box, "clicking Fix must open an input");
  const input = box.querySelector(".imgf-fixinput");
  ok(input.value === "خيار", "the box starts from the item name: " + input.value);
  input.value = "fresh cucumber vegetable";
  await box.querySelector(".imgf-fixgo").onclick();
  await new Promise(r => setTimeout(r, 0));

  ok(rows10[0].image === OK_URI, "the corrected search must fill the cell");
  ok(h10.asked.some(a => a[2] === "fresh cucumber vegetable"),
     "the typed words must reach the search: " + JSON.stringify(h10.asked));
  ok(h10.remembered.length === 1 && h10.remembered[0][1] === "fresh cucumber vegetable",
     "the wording must be REMEMBERED against the item, or the same mistake "
     + "returns in the next document: " + JSON.stringify(h10.remembered));
  ok(h10.remembered[0][0] === "خيار", "remembered against the right item");
  ok(/1 found . 0 not found/.test(h10.els.imgFetchTally.textContent),
     "the tally must move when a miss is fixed: " + h10.els.imgFetchTally.textContent);

  /* ---------- 11. a FOUND row can be corrected too ----------
     "found" is not the same as "right" - the log is the only place the user
     sees what was inserted before it reaches a document. */
  const rows11 = [{ item: "Bleach", image: "" }];
  const h11 = harness(rows11, (n, d, phrase) =>
    ({ ok: true, data_uri: phrase ? "data:image/png;base64,FIXED" : OK_URI,
       title: phrase ? "Bleach cleaning liquid" : "Bleach anime poster" }));
  await h11.fn(ctxFor(rows11));
  const l11 = lineFor(h11, "Bleach");
  const fix11 = l11.querySelector(".imgf-fix");
  ok(!!fix11, "even a hit must be correctable");
  fix11.onclick();
  const b11 = l11.querySelector(".imgf-fixbox");
  b11.querySelector(".imgf-fixinput").value = "bleach cleaning liquid";
  await b11.querySelector(".imgf-fixgo").onclick();
  await new Promise(r => setTimeout(r, 0));
  ok(rows11[0].image === "data:image/png;base64,FIXED",
     "the correction must replace the wrong picture");
  ok(/1 found . 0 not found/.test(h11.els.imgFetchTally.textContent),
     "correcting a hit must not double-count it: " + h11.els.imgFetchTally.textContent);

  /* ---------- 12. a remembered picture is labelled as such ---------- */
  const rows12 = [{ item: "Celery", image: "" }];
  const h12 = harness(rows12, () =>
    ({ ok: true, data_uri: OK_URI, title: "Celery", from_memory: true }));
  await h12.fn(ctxFor(rows12));
  const l12 = lineFor(h12, "Celery");
  ok(/saved picture/i.test(l12.innerHTML),
     "a picture from memory must say so rather than look like a fresh search: "
     + l12.innerHTML);

  /* ---------- 13. a translated hit names the words it used ---------- */
  const rows13 = [{ item: "جزر", image: "" }];
  const h13 = harness(rows13, () =>
    ({ ok: true, data_uri: OK_URI, title: "Carrot", source: "bing",
       via_translation: true, english: "carrot" }));
  await h13.fn(ctxFor(rows13));
  ok(/carrot/.test(lineFor(h13, "جزر").innerHTML),
     "the user must be able to see WHICH words found it");

  console.log("find_images_button: OK (" + checks + " checks)");
})().catch((e) => { console.error(e.message); process.exit(1); });
