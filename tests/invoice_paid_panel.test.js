const fs = require("fs");
const vm = require("vm");

const invSource = fs.readFileSync("static/inv.js", "utf8");
const poLinkSource = fs.readFileSync("static/po_link.js", "utf8");
const css = fs.readFileSync("static/app_styles.css", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

function makeElement(tag = "div") {
  let html = "";
  return {
    tagName: tag.toUpperCase(),
    children: [],
    dataset: {},
    style: {},
    className: "",
    title: "",
    value: "",
    hidden: false,
    onclick: null,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    set innerHTML(v) { html = String(v || ""); },
    get innerHTML() { return html; },
    appendChild(child) { this.children.push(child); },
    querySelectorAll() { return []; },
    querySelector() { return null; },
    addEventListener() {},
    setAttribute() {},
    getAttribute() { return ""; },
  };
}

const invList = makeElement("div");
const context = {
  console,
  setTimeout() {},
  clearTimeout() {},
  window: {
    addEventListener() {},
    ICON: {
      externalLink: "open",
      folder: "folder",
      copy: "copy",
      printer: "print",
      trash: "trash",
    },
    signBadgeHTML: (signed) => signed ? '<span class="sign-badge is-signed">Signed</span>' : "",
    panelStatusHTML: (_kind, doc) => doc.linked
      ? '<span class="linked-pill">Linked</span>'
      : (doc.paid ? '<span class="paid-pill">Paid</span>' : (doc.submitted ? "" : (doc.signed ? '<span class="sign-badge is-signed">Signed</span>' : ""))),
    submitBtnHTML: (kind, no, submitted) => `<button class="submit-btn" data-act="submit" data-kind="${kind}" data-no="${no}">${submitted ? "Submitted" : "Submit"}</button>`,
    wireSubmitButtons() {},
    copyFileToClipboard() {},
  },
  document: {
    readyState: "loading",
    addEventListener() {},
    getElementById(id) {
      if (id === "invList") return invList;
      return makeElement("div");
    },
    createElement: makeElement,
    querySelectorAll() { return []; },
    querySelector() { return null; },
  },
  state: { today: { year: 2026, month: 7 } },
  api() {
    return {
      open_path() {},
      reveal_in_folder() {},
      set_invoice_paid() {},
    };
  },
  toast() {},
  safe(fn) { return fn; },
  esc(v) {
    return String(v == null ? "" : v)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  },
};
context.window.document = context.document;

vm.createContext(context);
vm.runInContext(invSource, context, { filename: "static/inv.js" });
vm.runInContext(`
  invOpenAll = [{
    inv_no: "INV-MT-001",
    type_code: "MT",
    editable: true,
    signed: true,
    submitted: true,
    paid: true,
    updated_at: "2026-07-07T09:30:00",
    grand_total: 125.5,
    path: "D:/Docs/INV-MT-001.pdf"
  }];
  invTypeFilter = "all";
  invClientFilter = "all";
  renderInvListFiltered();
`, context);

assert(invList.children.length === 1, "invoice list should render one row");
const rowHtml = invList.children[0].innerHTML;

assert(!/qt-chip/.test(rowHtml), "invoice row should not show the MT/EQ subtype chip next to the number");
assert(!/data-act="print"/.test(rowHtml), "invoice row should not include the cramped Print action");
assert(/class="[^"]*\binv-row-head\b/.test(rowHtml), "invoice row should have a compact header layout");
assert(/class="[^"]*\binv-row-topmeta\b/.test(rowHtml), "date and amount should live next to the invoice number");
assert(/class="[^"]*\binv-row-statuses\b/.test(rowHtml), "status pills should live on their own second line");
assert(/class="[^"]*\bpaid-pill\b/.test(rowHtml), "paid invoices should show a Paid pill");
assert(!/class="[^"]*\bsubmitted-pill\b/.test(rowHtml), "invoice rows should not show a Submitted pill");
assert(!/class="[^"]*\bsign-badge\b/.test(rowHtml), "Paid should suppress the Signed badge");
assert(/class="[^"]*\bpaid-btn\b/.test(rowHtml), "invoice rows should include a Paid button");
assert(/data-act="paid"/.test(rowHtml), "Paid button should expose a paid action");
assert(/set_invoice_paid/.test(invSource), "Paid button should persist through the invoice API");
assert(/po_links_for_doc\("inv",\s*no\)/.test(invSource),
  "Paid action should inspect existing invoice-to-PO links");
assert(/!links\.length[\s\S]*openPoFulfillPicker\("inv",\s*no\)/.test(invSource),
  "Paid action should ask for a PO only when the invoice has no existing link");
assert(!/set_invoice_paid/.test(poLinkSource),
  "linking an invoice to a PO must never mark the invoice paid");
assert(/inv:\s*\{\s*verb:\s*"invoiced"/.test(poLinkSource),
  "invoice linking UI must describe the PO state as invoiced, not paid");
assert(css.includes(".paid-pill"), "Paid pill should have dedicated styling");
assert(css.includes(".paid-btn"), "Paid button should have dedicated styling");

console.log("invoice paid panel tests passed");
