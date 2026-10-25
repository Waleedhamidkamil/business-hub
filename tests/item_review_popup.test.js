/* Shared item-review content component. The ordinary Popup class still owns
   modal chrome, focus, stacking and dismissal. */
const fs = require("fs");
const vm = require("vm");

function assert(cond, message) { if (!cond) throw new Error(message); }

const source = fs.readFileSync("static/item_review_popup.js", "utf8");
const context = {
  console,
  window: {},
  document: {},
  Promise, Map, Set, Object, Array, Number, String, JSON,
};
vm.createContext(context);
vm.runInContext(source, context, { filename: "static/item_review_popup.js" });

const Review = context.window.ItemReviewPopup;
assert(typeof Review === "function", "ItemReviewPopup must be one reusable class");
assert(typeof Review.rowHtml === "function", "row rendering must be independently testable");
assert(typeof Review.tableHtml === "function", "table rendering must be independently testable");
assert(typeof Review.needsSuggestion === "function", "AI visibility must be one shared rule");
assert(!/await\s+this\.options\.suggestAvailable/.test(source),
  "checking AI availability must not delay the popup's primary action");

const page = fs.readFileSync("templates/index.html", "utf8");
const popupAt = page.indexOf('src="../static/popup.js"');
const reviewAt = page.indexOf('src="../static/item_review_popup.js"');
const handoffAt = page.indexOf('src="../static/po_handoff.js"');
assert(popupAt >= 0 && popupAt < reviewAt && reviewAt < handoffAt,
  "the item-review class must load after Popup and before the PO caller");
assert(page.includes('href="../static/item_review_popup.css"'),
  "the shared item-review stylesheet must be loaded by the app shell");

const rateOnly = {
  id: "fig",
  sourceName: "تين",
  sourceUnit: "كيلو",
  targetName: "Fig",
  targetUnit: "KG",
  matched: true,
  missing: ["rate"],
  rate: "",
};
let html = Review.rowHtml(rateOnly, 0, {
  sourceLanguage: "ar", targetLanguage: "en", allowMoney: true,
});
assert(html.includes("تين") && html.includes("Fig"),
  "a rate-only row shows both known names");
assert(html.includes('data-irp-field="0:rate"'),
  "a rate-only invoice row exposes its missing rate");
assert(!html.includes('data-irp-field="0:name_en"'),
  "a known English name is confirmed text, not an empty input");
assert(!/new item|nothing on file|type it in/i.test(html),
  "rate-only review must not claim the item is new");
assert(!Review.needsSuggestion([rateOnly]),
  "Gemini stays hidden when only money is missing");

const missingEnglish = {
  id: "new",
  sourceName: "مادة جديدة",
  sourceDescription: "نوعية جيدة",
  sourceUnit: "عدد",
  targetName: "",
  targetUnit: "EA",
  matched: true,
  missing: ["name"],
};
html = Review.rowHtml(missingEnglish, 1, {
  sourceLanguage: "ar", targetLanguage: "en", allowMoney: false,
});
assert(html.includes('data-irp-field="1:name_en"'),
  "missing English renders one English-name input");
assert(html.includes('dir="ltr"'), "English target input is LTR");
assert(!html.includes('data-irp-field="1:rate"'),
  "PO catalogue completion never asks for a selling rate");
assert(Review.needsSuggestion([missingEnglish]),
  "Gemini is useful when the target-language name is missing");

const missingArabic = {
  id: "quote",
  sourceName: "Safety Helmet",
  sourceUnit: "EA",
  targetName: "",
  targetUnit: "عدد",
  matched: true,
  missing: ["name"],
};
html = Review.rowHtml(missingArabic, 2, {
  sourceLanguage: "en", targetLanguage: "ar", allowMoney: false,
});
assert(html.includes('data-irp-field="2:name_ar"'),
  "the same class can target Arabic later");
assert(html.includes('dir="rtl"'), "Arabic target input is RTL");

const candidateRow = {
  ...missingEnglish,
  candidates: [
    { name: "New Material", description: "Good quality", unit: "EA", item_id: 7 },
  ],
};
html = Review.rowHtml(candidateRow, 3, {
  sourceLanguage: "ar", targetLanguage: "en", allowMoney: false,
  allowDocumentOnly: true,
});
assert(html.includes('data-irp-more="3"'), "rare choices live behind Details");
assert(html.includes('data-irp-candidate="3"'), "candidate choice remains available");
assert(html.includes('data-irp-document-only="3"'),
  "document-only choice remains available without crowding the main row");
assert(html.includes('data-irp-skip="3"'), "every visible row can be skipped");

const table = Review.tableHtml([rateOnly, missingEnglish], {
  sourceLanguage: "ar", targetLanguage: "en", allowMoney: true,
});
assert((table.match(/data-irp-row=/g) || []).length === 2,
  "one compact primary row is rendered per item");
assert(/On the order/.test(table) && /English details/.test(table),
  "column labels describe the source and target clearly");

function fakeClassList() {
  const values = new Set();
  return {
    add(...names) { names.forEach(name => values.add(name)); },
    remove(...names) { names.forEach(name => values.delete(name)); },
    toggle(name, on) { if (on) values.add(name); else values.delete(name); },
    contains(name) { return values.has(name); },
  };
}

function fakeElement(value = "") {
  return {
    value, textContent: "Complete Missing Details with AI", disabled: false,
    classList: fakeClassList(), attrs: {}, hidden: true, focused: false, scrolled: false,
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    getAttribute(name) { return this.attrs[name]; },
    insertAdjacentHTML(_where, html) { this.errorHtml = html; },
    focus() { this.focused = true; },
    scrollIntoView() { this.scrolled = true; },
    matches(selector) { return /input|button|select/.test(selector); },
    querySelector() { return null; },
    remove() { this.removed = true; },
  };
}

function suggestionRoot() {
  const fields = new Map();
  const rows = [fakeElement(), fakeElement()];
  const candidateButtons = [fakeElement(), fakeElement()];
  [0, 1].forEach(index => {
    fields.set(`[data-irp-field="${index}:name_en"]`, fakeElement(""));
    fields.set(`[data-irp-field="${index}:description_en"]`, fakeElement(""));
    fields.set(`[data-irp-field="${index}:unit_en"]`, fakeElement("EA"));
    fields.set(`[data-irp-row="${index}"]`, rows[index]);
    fields.set(`[data-irp-skip="${index}"]`, { checked: false });
    fields.set(`[data-irp-document-only="${index}"]`, { checked: false });
  });
  return {
    fields, rows, candidateButtons,
    querySelector(selector) { return fields.get(selector) || null; },
    querySelectorAll(selector) {
      if (selector === '[data-irp-candidate="1"]') return candidateButtons;
      return [];
    },
  };
}

(async () => {
  const calls = [];
  const root = suggestionRoot();
  const button = fakeElement();
  const review = new Review({
    targetLanguage: "en",
    rows: [
      { id: "car", sourceName: "سيارة ابيض", sourceUnit: "عدد", missing: ["name"],
        targetUnit: "EA" },
      { id: "split", sourceName: "سبلت جداري", sourceDescription: "2 طن",
        sourceUnit: "عدد", missing: ["name"], ambiguous: true, targetUnit: "EA",
        candidates: [
          { item_id: 11, name: "Wall Split Unit", description: "1.5 Ton", unit: "EA" },
          { item_id: 12, name: "Wall Split Unit", description: "2 Ton", unit: "EA" },
        ] },
    ],
    suggest: async requests => {
      calls.push(requests);
      return calls.length === 1
        ? { ok: true, suggestions: [
          { index: 0, name_en: "" },
          { index: 1, candidate_index: 1 },
        ] }
        : { ok: true, suggestions: [{ index: 0, name_en: "White Car" }] };
    },
  });
  await review._suggest(root, button);
  assert(calls.length === 2, "one press makes at most one automatic repair request");
  assert(calls[1].length === 1 && calls[1][0].name === "سيارة ابيض",
    "the repair request contains only the still-missing required name");
  assert(root.fields.get('[data-irp-field="0:name_en"]').value === "White Car",
    "the repair response completes A-83 without another click");
  assert(review.selected.get(1) === 1,
    "AI can select the second candidate for A-89's 2 Ton row");
  assert(!("item_id" in calls[0][1].candidates[0]),
    "candidate requests never expose local Master Data IDs");
  assert(button.disabled === false
      && button.textContent === "Complete Missing Details with AI",
    "the one-click action always restores its ready state");

  const privateRoot = suggestionRoot();
  privateRoot.fields.get('[data-irp-skip="0"]').checked = true;
  privateRoot.fields.get('[data-irp-document-only="1"]').checked = true;
  const privateReview = new Review({
    targetLanguage: "en",
    rows: [
      { sourceName: "do not send", missing: ["name"] },
      { sourceName: "document only", missing: ["name"], ambiguous: true,
        candidates: [{ item_id: 99, name: "Private Candidate" }] },
    ],
  });
  assert(privateReview._pendingRequests(privateRoot, false).length === 0,
    "Skip and document-only rows must not be disclosed to AI or consume quota");

  const rate = fakeElement("");
  const errorRow = fakeElement();
  const extra = fakeElement();
  const more = fakeElement();
  const candidates = fakeElement();
  candidates.matches = () => false;
  candidates.querySelector = () => candidateButtons[0];
  const validationRoot = {
    querySelector(selector) {
      return new Map([
        ['[data-irp-row="0"]', errorRow],
        ['[data-irp-field="0:rate"]', rate],
        ['[data-irp-extra="0"]', extra],
        ['[data-irp-more="0"]', more],
        ['[data-irp-extra="0"] .irp-candidates', candidates],
      ]).get(selector) || null;
    },
    querySelectorAll() { return []; },
  };
  review.rows = [{ id: "invoice-row" }];
  review._applyValidation(validationRoot, {
    message: "Enter a rate and choose a variant",
    errors: [{ id: "invoice-row", fields: ["rate", "choice"] }],
  });
  assert(rate.getAttribute("aria-invalid") === "true" && rate.focused,
    "validation marks and focuses the exact missing rate");
  assert(extra.hidden === false && more.getAttribute("aria-expanded") === "true",
    "a hidden candidate error automatically opens Details");
  assert(candidates.getAttribute("aria-invalid") === "true",
    "candidate choice receives an accessible invalid state");

  /* ------------------------------------------------------------------
     Picking a candidate re-seeds the WHOLE row from the record chosen.

     The row opens pre-filled from whatever the resolver matched
     automatically. That is a different record, and its private cost and its
     description were sitting in the boxes: `read()` reported them as typed,
     so `po_handoff.buildMasterPatch` wrote one product's cost into another
     product's master record and put it on the document. Audit, 2026-09-08.
     ------------------------------------------------------------------ */
  const seedFields = new Map([
    ['[data-irp-field="0:name_en"]', fakeElement("")],
    ['[data-irp-field="0:description_en"]', fakeElement("Wrong record's description")],
    ['[data-irp-field="0:unit_en"]', fakeElement("EA")],
    ['[data-irp-field="0:rate"]', fakeElement("9.75")],
    ['[data-irp-field="0:cost"]', fakeElement("12.50")],
    ['[data-irp-currency="0:rate"]', fakeElement("USD")],
    ['[data-irp-currency="0:cost"]', fakeElement("USD")],
    ['[data-irp-row="0"]', fakeElement()],
  ]);
  const seedRoot = {
    querySelector(selector) { return seedFields.get(selector) || null; },
    querySelectorAll() { return []; },
  };
  const seedReview = new Review({
    allowMoney: true, sourceLanguage: "ar", targetLanguage: "en",
    rows: [{
      id: 0, sourceName: "ماء بطل", missing: ["name"], ambiguous: true,
      cost: "12.50", rate: "9.75",
      candidates: [
        { item_id: "A", name: "Water Bottle 0.5L",
          fill: { rate: "0.25", rate_ccy: "IQD", cost: "0.20", cost_ccy: "IQD" } },
        { item_id: "B", name: "Water Bottle 1.5L", fill: {} },
      ],
    }],
  });
  seedReview._selectCandidate(seedRoot, 0, 1, false);
  let seeded = seedReview.read(seedRoot)[0];
  assert(seeded.typed.cost === undefined && seeded.typed.rate === undefined,
    "another record's money must not ride onto the candidate the user picked");
  assert(seeded.typed.description_en === undefined,
    "another record's description must not ride onto the candidate either");

  seedReview.selected.clear();
  seedReview._selectCandidate(seedRoot, 0, 0, false);
  seeded = seedReview.read(seedRoot)[0];
  assert(seeded.typed.rate === "0.25" && seeded.typed.rate_ccy === "IQD",
    "the chosen record's own price is shown, with the currency it is kept in");
  assert(seeded.typed.cost === "0.20" && seeded.typed.cost_ccy === "IQD",
    "money always arrives as a pair, never an amount on its own");

  // A box the person typed in is theirs; choosing a record never wipes it.
  seedReview.edited.add("0:cost");
  seedFields.get('[data-irp-field="0:cost"]').value = "7";
  seedReview.selected.clear();
  seedReview._selectCandidate(seedRoot, 0, 1, false);
  assert(seedReview.read(seedRoot)[0].typed.cost === "7",
    "a value the user typed survives picking a different candidate");

  /* ------------------------------------------------------------------
     Leaving must not silently bin the work. The popup is undismissible so
     Esc and the dim background cannot drop forty rows of typing; every exit
     that discards goes through one question, and only when there is
     something to discard.
     ------------------------------------------------------------------ */
  assert(/dismissible:\s*false/.test(source),
    "a review full of typing must not be dismissed by a stray Esc or backdrop click");
  assert(/label: "Cancel", value: "cancel", cls: "ghost", keep: true/.test(source),
    "Cancel must be able to ask before it discards");
  const clean = new Review({ rows: [] });
  assert(await clean._confirmDiscard() === true,
    "an untouched review closes without a question");
  const dirty = new Review({ rows: [] });
  dirty.dirty = true;
  let asked = null;
  context.window.showModal = (spec) => { asked = spec; return Promise.resolve("keep"); };
  assert(await dirty._confirmDiscard() === false,
    "'Keep editing' returns to the review instead of discarding");
  assert(asked && /discard/i.test(asked.title),
    "the question names what is about to be lost");
  context.window.showModal = () => Promise.resolve("discard");
  assert(await dirty._confirmDiscard() === true, "'Discard' still lets the user out");
  delete context.window.showModal;

  /* ------------------------------------------------------------------
     Skip and "use on this document only" are ANSWERS, not gaps. The toast
     after an AI press was counting them, and telling the user that rows they
     had just excluded on purpose still needed review. Audit, 2026-09-08.
     ------------------------------------------------------------------ */
  const excludedRoot = suggestionRoot();
  excludedRoot.fields.get('[data-irp-skip="0"]').checked = true;
  excludedRoot.fields.get('[data-irp-document-only="1"]').checked = true;
  const excludedReview = new Review({
    targetLanguage: "en",
    rows: [
      { sourceName: "skipped", missing: ["name"] },
      { sourceName: "document only", missing: ["name"], ambiguous: true },
    ],
  });
  assert(excludedReview._requiredGapCount(excludedRoot) === 0,
    "a row the user excluded is not a row that still needs review");
  excludedRoot.fields.get('[data-irp-skip="0"]').checked = false;
  assert(excludedReview._requiredGapCount(excludedRoot) === 1,
    "but an untouched blank name still counts");

  /* ------------------------------------------------------------------
     The primary action cannot run while the AI request is out: it used to
     read a half-filled form, and the answer then landed on a popup that had
     already closed.
     ------------------------------------------------------------------ */
  const flightRoot = suggestionRoot();
  const flightPrimary = fakeElement();
  flightPrimary.disabled = false;
  flightRoot.fields.set("[data-value='ok']", flightPrimary);
  let disabledDuringCall = null;
  const flightReview = new Review({
    targetLanguage: "en",
    rows: [{ sourceName: "قلم", missing: ["name"] }],
    suggest: async () => {
      disabledDuringCall = flightPrimary.disabled;
      return { ok: true, suggestions: [{ index: 0, name_en: "Pen" }] };
    },
  });
  await flightReview._suggest(flightRoot, fakeElement());
  assert(disabledDuringCall === true,
    "the primary action is disabled while the AI request is in flight");
  assert(flightPrimary.disabled === false,
    "and is live again the moment it comes back, however it came back");

  /* A long list is sent as several requests. If a later one failed, what did
     land is kept and the user is told the rest is not there. */
  const partialRoot = suggestionRoot();
  const toasts = [];
  const partialReview = new Review({
    targetLanguage: "en",
    rows: [{ sourceName: "قلم", missing: ["name"] }],
    toast: (message, bad) => toasts.push([message, !!bad]),
    suggest: async () => ({ ok: true, suggestions: [{ index: 0, name_en: "Pen" }],
                            warning: "60 of 65 items completed, then the translation "
                                   + "service stopped: no network" }),
  });
  await partialReview._suggest(partialRoot, fakeElement());
  assert(toasts.some(([message, bad]) => bad && /60 of 65/.test(message)),
    "a partly-completed batch says so, and says it as a problem");

  /* ------------------------------------------------------------------
     The AI button says it is still working. Six real calls on 2026-09-10
     measured 11.7s to 47.7s; a button frozen on "Completing..." for three
     quarters of a minute is indistinguishable from a broken one, which is
     exactly what it was reported as.
     ------------------------------------------------------------------ */
  const ticks = [];
  const realInterval = context.setInterval;
  const realClear = context.clearInterval;
  context.setInterval = (fn) => { ticks.push(fn); return 99; };
  let cleared = null;
  context.clearInterval = (id) => { cleared = id; };
  const slowRoot = suggestionRoot();
  const slowButton = fakeElement();
  const labels = [];
  const slowReview = new Review({
    targetLanguage: "en",
    rows: [{ sourceName: "قلم", missing: ["name"] }],
    suggest: async () => {
      ticks.forEach(fn => { for (let i = 0; i < 5; i++) fn(); });
      labels.push(slowButton.textContent);
      return { ok: true, suggestions: [{ index: 0, name_en: "Pen" }] };
    },
  });
  await slowReview._suggest(slowRoot, slowButton);
  context.setInterval = realInterval;
  context.clearInterval = realClear;
  assert(ticks.length === 1, "one timer per press, not one per row");
  assert(/Completing\.\.\. \d+s/.test(labels[0]),
    "after a few seconds the button reports how long it has been waiting");
  assert(cleared === 99, "and the timer is stopped however the request ends");
  assert(slowButton.textContent === "Complete Missing Details with AI",
    "the label goes back to what it was");

  console.log("item_review_popup.test.js: shared component contract passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
