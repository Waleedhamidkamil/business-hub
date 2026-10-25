const fs = require("fs");

const app = fs.readFileSync("static/app.js", "utf8");

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// `renderDashboard(opts)` since 2026-10-01 (opts.force), so match the name only.
const start = app.indexOf("async function renderDashboard(");
const end = app.indexOf("window.renderDashboard = renderDashboard", start);
assert(start >= 0 && end > start, "renderDashboard should exist");
const render = app.slice(start, end);

assert(/renderDashboardLoading\(\)/.test(render),
  "dashboard should paint loading states before bridge calls");
assert(/const attentionTask\s*=/.test(render),
  "Needs Attention should have an independent startup task");
assert(render.indexOf("const attentionTask") < render.indexOf("await Promise.all"),
  "Needs Attention must start before waiting for Insights data");
assert(/dashboardBridgeCall\(/.test(render),
  "dashboard bridge calls should use timeout/error protection");
assert(/buildDashboardSearchIndex/.test(render),
  "search indexing should run as a background stage");
assert(/renderDashboardError/.test(app),
  "failed sections should render a retry state instead of staying blank");
assert(/renderDashboard\(\)\.catch/.test(app),
  "showHub should catch asynchronous dashboard failures");
assert(/let _dashRenderSeq\s*=\s*0/.test(app),
  "overlapping dashboard loads need a stale-result guard");

console.log("dashboard startup resilience tests passed");
