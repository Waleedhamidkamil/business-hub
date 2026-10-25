/* Insights → Invoices section: paid, unpaid, and WHY one is losing (2026-08-26).

   The Documents table mixes quotations, delivery notes and invoices. An invoice
   gets asked two questions the mixed table answers badly: has the money
   arrived, and - when it lost money - what did it.

   "Why" is the part that has to be right. A red total tells the reader nothing
   they can act on, and there are three different causes that look identical in
   the total:

     1. lines sold below cost      -> name them and give the dollar hole
     2. document-level extra costs -> money in `spent` that no line carries, so
                                      an invoice can be in the red with every
                                      single line healthy
     3. lines with no cost entered -> the figure is a BEST CASE and the real
                                      loss may be larger; saying "loss" flatly
                                      would be a confident wrong number

   And none of it may be carried by colour: the user is colour blind
   ([[waleed-colour-blind]]), so every state is a word - Paid / Unpaid / Loss /
   No costs - with red used only as reinforcement and no green anywhere.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const src = fs.readFileSync("static/analytics.js", "utf8").replace(/\r\n/g, "\n");
const css = fs.readFileSync("static/app_styles.css", "utf8");

/* ---------- run the real `invWhy` ---------- */
const at = src.indexOf("const invWhy = (x) => {");
assert(at >= 0, "invWhy is missing from analytics.js");
const end = src.indexOf("\n        };", at) + "\n        };".length;
const why = new Function("esc", "money", "num",
  src.slice(at, end) + "\nreturn invWhy;")(
  (s) => String(s),
  (n) => (n < 0 ? "-$" : "$") + Math.abs(Number(n) || 0).toFixed(2),
  (n) => String(n));

const base = {
  losing_line_count: 0, losing_lines: [], losing_line_total: 0,
  extra: 0, uncosted_lines: 0,
};

/* ---------- 1. lines below cost are NAMED, with the hole ---------- */
{
  const out = why({
    ...base,
    losing_line_count: 2, losing_line_total: 31.69,
    losing_lines: [{ name: "Xiaomi air purifier 4", loss: 21.69 },
                   { name: "Hemp rope", loss: 10 }],
  });
  ok(/Xiaomi air purifier 4/.test(out) && /Hemp rope/.test(out),
    "the losing lines must be NAMED - 'two lines lost money' sends the reader "
    + "back to the document to find out which; got: " + out);
  ok(/-\$21\.69/.test(out),
    "each named line carries its own dollar hole; got: " + out);
  ok(/-\$31\.69/.test(out), "and the total of them; got: " + out);
  ok(/below cost/.test(out), "said in words, not by colour");
}

/* ---------- 2. a long list is capped but still counted ---------- */
{
  const out = why({
    ...base,
    losing_line_count: 9, losing_line_total: 100,
    losing_lines: [{ name: "A", loss: 40 }, { name: "B", loss: 30 },
                   { name: "C", loss: 20 }, { name: "D", loss: 5 }],
  });
  ok(/9 lines/.test(out),
    "the COUNT is of every losing line, not of the ones listed; got: " + out);
  ok(/5 more/.test(out),
    "and the ones not named must be counted rather than silently dropped; got: " + out);
}

/* ---------- 3. extra costs - the invoice with no bad line ---------- */
{
  const out = why({ ...base, extra: 250 });
  ok(/\$250\.00/.test(out) && /extra costs/.test(out),
    "document-level extra costs are in `spent` but in no line, so an invoice "
    + "can be in the red with every line healthy - that has to be sayable; "
    + "got: " + out);
  ok(!/below cost/.test(out),
    "and it must not claim lines were sold below cost when none were");
}

/* ---------- 4. uncosted lines make it a BEST CASE ---------- */
{
  const out = why({ ...base, uncosted_lines: 3 });
  ok(/3 lines/.test(out) && /no cost entered/.test(out),
    "an uncosted line must be named as such; got: " + out);
  // Changed 2026-10-21 (Waleed): costless lines are now counted at an
  // ESTIMATED cost, so the reason says the figure rests on an estimate.
  ok(/estimated/.test(out),
    "a loss computed from half-costed lines rests on an estimate - saying "
    + "'loss' flatly would be the same confident wrong number the uncosted "
    + "warning exists to prevent; got: " + out);
}

/* ---------- 5. all three at once, and the fallback ---------- */
{
  const out = why({
    ...base, losing_line_count: 1, losing_line_total: 5,
    losing_lines: [{ name: "Cable", loss: 5 }], extra: 40, uncosted_lines: 2,
  });
  ok(/Cable/.test(out) && /extra costs/.test(out) && /estimated/.test(out),
    "the three causes are independent and must all be reported when all "
    + "three are present; got: " + out);

  const thin = why({ ...base });
  ok(thin.length > 0 && /thin/.test(thin),
    "an invoice in the red with no single bad line still needs an "
    + "explanation, or the Why tag reads as a bug; got: " + thin);
}

/* ---------- 6. the section is wired into the client view ---------- */
{
  ok(/sect\("Invoices"/.test(src),
    "the section needs its own heading, above Documents");
  ok(src.indexOf('sect("Invoices"') < src.indexOf('sect("Documents"'),
    "invoices come before the mixed Documents table - they are the question "
    + "being asked");
  ok(/d\.invoices \|\| \[\]/.test(src) && /d\.invoice_summary \|\| \{\}/.test(src),
    "it must read the backend's own figures rather than recomputing them, or "
    + "the section can disagree with the totals above it");
  ok(/No invoices for this client in this period/.test(src),
    "an empty period needs an empty state, not a bare table");
}

/* ---------- 7. every state is a WORD ---------- */
{
  const sec = src.slice(at, src.indexOf('const KIND = { inv:'));
  ok(/>Paid</.test(sec) && /Unpaid</.test(sec),
    "paid and unpaid are words on the row");
  ok(/>Loss</.test(sec), "a losing invoice says the word Loss");
  ok(/No costs</.test(sec), "and an uncosted one says so");
  ok(/in-why-tag/.test(sec) && /Why/.test(sec),
    "the reason row is labelled, so it cannot be read as part of the invoice "
    + "below it");

  const GREEN = /(#16a34a|#22c55e|#15803d|#059669|var\(--success\))/i;
  const pill = css.slice(css.indexOf(".in-pill.neg"), css.indexOf(".in-pill.neg") + 400);
  assert(css.includes(".in-pill.neg"), ".in-pill.neg is missing from app_styles.css");
  ok(!GREEN.test(pill), "no green may creep into the pill set");
  ok(!/\.in-pill\.pos/.test(css),
    "there is deliberately no green counterpart pill - a paid, profitable "
    + "invoice needs no colour at all, and adding one would make the loss "
    + "legible only by hue");
  ok(/\.in-table tr\.in-why td/.test(css), "the reason row needs its own rule");
}

console.log(`insights_invoices_section.test.js: ${checks} checks passed`);
