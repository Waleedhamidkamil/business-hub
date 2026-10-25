const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync("static/master_data_fill.js", "utf8");
const context = { console, window: {}, document: {}, setTimeout() {} };
vm.createContext(context);
vm.runInContext(source, context, { filename: "static/master_data_fill.js" });

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

const im = context.window.__masterDataFillInternals;
if (!im) throw new Error("Master Data fill helpers were not exposed");

const exact = {
  tier: "exact",
  name: "Copper cable",
  fill: {
    description: "2-core copper cable",
    unit: "M",
    rate: 12.5,
    cost: 8.25,
    cost_ccy: "USD",
  },
};

const similar = {
  tier: "similar",
  name: "Copper cable 2-core",
  fill: {},
  suggested_fill: {
    description: "2-core copper cable",
    unit: "M",
    rate: 12.5,
    cost: 8.25,
    cost_ccy: "USD",
  },
};

const fields = im.availableFields(exact, "item");
assert(JSON.stringify(fields) === JSON.stringify([
  { key: "item", label: "Item" },
  { key: "description", label: "Description" },
  { key: "unit", label: "Unit" },
  { key: "price", label: "Price" },
  { key: "cost", label: "Cost" },
]), "available fields must expose the five requested details in order");
assert(im.valueForField(exact.fill, "price") === 12.5,
  "Price must map to the editor's rate field");

const row = {
  item: "typed item",
  description: "typed description",
  unit: "EA",
  qty: 4,
  rate: 99,
  cost: 50,
  cost_ccy: "USD",
};
im.applyRowFill(row, "item", exact, {
  item: true,
  description: false,
  unit: true,
  price: false,
  cost: true,
});
assert(row.item === "Copper cable", "checked Item must update the primary item field");
assert(row.description === "typed description", "unchecked Description must stay unchanged");
assert(row.unit === "M", "checked Unit must update the unit field");
assert(row.rate === 99, "unchecked Price must stay unchanged");
assert(row.cost === 8.25 && row.cost_ccy === "USD",
  "checked Cost must update cost and its currency marker");
assert(row.qty === 4, "unrelated row fields must stay unchanged");

const similarFill = im.fillFor(similar);
assert(similarFill.rate === 12.5 && similarFill.unit === "M",
  "similar matches must use their suggested fill values");

const tied = {
  tier: "similar",
  name: "",
  fill: {},
  alternatives: [
    { name: "mop", score: 0.5 },
    { name: "Cotton Rectangular Cleaning Mop", score: 0.5 },
  ],
  suggestions: [
    { name: "mop", score: 0.5, fill: { unit: "EA", rate: 4 } },
    { name: "Cotton Rectangular Cleaning Mop", score: 0.5,
      fill: { description: "Cotton head", unit: "EA", rate: 12 } },
  ],
};
assert(im.resultRowsHtml([tied]).includes("data-mdf-candidate"),
  "multiple similar matches must render selectable candidates");
assert(im.fillFor(tied, tied.suggestions[1]).rate === 12,
  "selecting a candidate must use that candidate's own fill values");

console.log("Master Data fill helper tests passed");

async function testEditorPopupUsesIndependentDefaultsAndContext() {
  let callArgs = null;
  let modalHtml = "";
  let rendered = 0;
  let dirtied = 0;
  const row = { item: "Copper cable", qty: 3 };
  const inputs = [
    "item", "description", "unit", "price", "cost",
  ].map(field => ({
    checked: true,
    dataset: { mdfField: field },
  }));
  const modalBody = {
    querySelectorAll() { return inputs; },
  };
  context.document = {
    getElementById(id) { return id === "modalBody" ? modalBody : null; },
    querySelector() { return { classList: { add() {}, remove() {} } }; },
  };
  context.api = () => ({
    async im_resolve(...args) {
      callArgs = args;
      return {
        ok: true,
        results: [{
          tier: "exact",
          query: "Copper cable",
          name: "Copper cable",
          fill: {
            description: "2-core copper cable",
            unit: "M",
            rate: 12.5,
            cost: 8.25,
            cost_ccy: "USD",
          },
        }],
      };
    },
  });
  context.showModal = ({ body }) => {
    modalHtml = body;
    return Promise.resolve("apply");
  };
  context.window.toast = () => {};

  await context.window.openMasterDataFill({
    docType: "inv",
    subType: "materials",
    rows: [row],
    primaryKey: "item",
    costCurrency: "IQD",
    costPeg: 1450,
    sellCurrency: "IQD",
    sellPeg: 1500,
    render: () => { rendered++; },
    markDirty: () => { dirtied++; },
  });

  assert(callArgs[0][0] === "Copper cable" && callArgs[1] === "inv",
    "editor lookup must send current item names and document type");
  assert(callArgs[3] === "IQD" && callArgs[4] === 1450 &&
         callArgs[6] === "IQD" && callArgs[7] === 1500,
    "editor lookup must pass both cost and sell currency context");
  assert(JSON.stringify(callArgs[8]) === JSON.stringify([{ description: "", unit: "" }]),
    "editor lookup must send what each row says beside its name, so a typed "
    + "description can pick one of several variants: " + JSON.stringify(callArgs[8]));
  assert((modalHtml.match(/data-mdf-field=/g) || []).length === 5 &&
         (modalHtml.match(/ checked/g) || []).length === 5,
    "the popup must render five independently checked available fields by default");
  assert(row.description === "2-core copper cable" && row.unit === "M" &&
         row.rate === 12.5 && row.cost === 8.25 && row.qty === 3,
    "applying the popup must fill selected details without touching quantity");
  assert(rendered === 1 && dirtied === 1,
    "applying Master Data must rerender the editor and mark it dirty");
}

async function testEditorPopupAppliesTheChosenSimilarCandidate() {
  const row = { item: "Cleaning Mop", qty: 1 };
  const fields = ["item", "description", "unit", "price", "cost"]
    .map(field => ({ checked: true, dataset: { mdfField: field } }));
  const modalBody = {
    querySelectorAll(selector) {
      return selector.includes("data-mdf-candidate-row") ? [] : fields;
    },
    querySelector(selector) {
      if (selector.includes("data-mdf-candidate-row") && selector.includes(":checked")) {
        return { dataset: { mdfCandidate: "1" } };
      }
      return null;
    },
  };
  let modalHtml = "";
  context.document = {
    getElementById(id) { return id === "modalBody" ? modalBody : null; },
    querySelector() { return { classList: { add() {}, remove() {} } }; },
  };
  context.api = () => ({
    async im_resolve() {
      return {
        ok: true,
        results: [{
          tier: "similar",
          query: "Cleaning Mop",
          name: "",
          fill: {},
          suggestions: [
            { name: "mop", score: 0.5, fill: { unit: "EA", rate: 4 } },
            { name: "Cotton Rectangular Cleaning Mop", score: 0.5,
              fill: { description: "Cotton head", unit: "PCS", rate: 12 } },
          ],
        }],
      };
    },
  });
  context.showModal = ({ body }) => {
    modalHtml = body;
    return Promise.resolve("apply");
  };
  context.window.toast = () => {};

  await context.window.openMasterDataFill({
    docType: "qut", rows: [row], primaryKey: "item",
    render() {}, markDirty() {},
  });

  assert(modalHtml.includes("Cotton Rectangular Cleaning Mop") &&
         modalHtml.includes("data-mdf-candidate"),
    "multiple similar matches must be presented as selectable candidates");
  assert(row.item === "Cotton Rectangular Cleaning Mop" &&
         row.description === "Cotton head" && row.unit === "PCS" &&
         row.rate === 12 && row.qty === 1,
    "the selected similar candidate must supply only its selected details");
}

testEditorPopupUsesIndependentDefaultsAndContext()
  .then(testEditorPopupAppliesTheChosenSimilarCandidate)
  .then(() => console.log("Master Data editor popup tests passed"));
