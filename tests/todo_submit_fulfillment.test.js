const fs = require("fs");

const app = fs.readFileSync("static/app.js", "utf8");
const todo = fs.readFileSync("static/todo.js", "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

assert(/window\.openSubmittedFulfillment\s*=\s*function/.test(app),
  "app shell must expose one shared post-submit fulfillment helper");
assert(/\["dn",\s*"inv"\][\s\S]*openPoFulfillPicker\(kind,\s*no\)/.test(app),
  "shared helper must open the existing picker only for DN and invoice");
assert(/wireSubmitButtons[\s\S]*if\s*\(next\)[\s\S]*openSubmittedFulfillment\(kind,\s*no\)/.test(app),
  "right-panel Submit must use the shared helper");
assert((todo.match(/openSubmittedFulfillment/g) || []).length >= 2,
  "To-Do inline and preview-modal Submit paths must use the shared helper");
assert(/closeDocModal\(\)[\s\S]*onAfterAction[\s\S]*openSubmittedFulfillment/.test(todo),
  "preview modal must close and refresh before opening the fulfillment picker");
assert(/refreshDashboardAttention\(\)[\s\S]*openSubmittedFulfillment\(b\.dataset\.kind,\s*b\.dataset\.no\)/.test(app),
  "dashboard inline Submit must refresh before opening the same picker");

console.log("To-Do submit fulfillment tests passed");
