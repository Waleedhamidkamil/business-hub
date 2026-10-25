/* When the number an editor showed was taken a moment ago (multi-user,
   2026-10-22), the save comes back with `number_taken`. Each of the four
   editors must say so and fetch the next number - otherwise the user sees
   "Save error" and the stale number stays in the box, so the next save is
   refused again.

   The backend side is proven by tests/two_engines_one_number.test.py. */
const fs = require("fs");

let failures = 0;
function check(name, fn) {
  try { fn(); console.log("  ok   " + name); }
  catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

const EDITORS = [
  { file: "static/inv.js", call: "api().save_inv(", refresh: "refreshInvNumber" },
  { file: "static/quote.js", call: "api().save_quote(", refresh: "refreshQuoteNumber" },
  { file: "static/dn.js", call: "api().save_dn(", refresh: "refreshDnNumber" },
  { file: "static/app.js", call: "api().save_po(", refresh: "initNextPoNumber" },
];

console.log("editors handle a number taken by someone else");

EDITORS.forEach(({ file, call, refresh }) => {
  check(`${file}: says so and fetches the next number`, () => {
    const src = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
    const at = src.indexOf(call);
    assert(at > 0, `${call} not found`);
    const after = src.slice(at, at + 2500);
    const taken = after.indexOf("r.number_taken");
    assert(taken > 0, "the save result's number_taken is never looked at");
    const generic = after.search(/if \(!r \|\| !r\.ok\)/);
    assert(generic < 0 || taken < generic,
      "number_taken must be handled BEFORE the generic 'Save error' toast");
    const branch = after.slice(taken, taken + 300);
    assert(new RegExp(`await ${refresh}\\(`).test(branch), `the branch does not call ${refresh}()`);
    assert(/toast\(/.test(branch), "the branch does not tell the user");
  });
});

if (failures) { console.log(`\n${failures} failed`); process.exit(1); }
console.log("\nall number-taken checks passed");
