const fs = require("fs");

const app = fs.readFileSync("static/app.js", "utf8");
const todo = fs.readFileSync("static/todo.js", "utf8");
const html = fs.readFileSync("templates/index.html", "utf8");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

assert(/window\.openTodoDocModal\s*=\s*openDocModal/.test(todo),
  "To-Do preview modal should be exposed for dashboard reuse");
assert(/async function openDocModal\(cat, kind, no, docDate, origCat, opts = \{\}\)/.test(todo),
  "To-Do modal should accept dashboard callback options");
assert(/opts\.onAfterAction/.test(todo),
  "To-Do modal should call the dashboard refresh callback after actions");
assert(/kind === "po"[\s\S]*api\(\)\.load_po\(no\)/.test(todo),
  "To-Do preview modal should load PO records when POs appear as unsubmitted");

assert(/data-cat="\$\{esc\(cat\)\}"/.test(app),
  "Dashboard attention cards should carry their attention category");
assert(/window\.openTodoDocModal/.test(app),
  "Dashboard attention cards should open the To-Do preview modal");
assert(/onAfterAction: refreshDashboardAttention/.test(app),
  "Dashboard modal actions should refresh the dashboard attention feed");
assert(!/title="Open \$\{esc\(r\.doc_no\)\} in Costing & Profit"/.test(app),
  "Dashboard attention card title should no longer advertise direct Costing navigation");

assert(/Decide bids · Uncosted · Unsubmitted · Unpaid · Ignored/.test(html),
  "To-Do template comment should match the current section set");

console.log("dashboard To-Do popup tests passed");
