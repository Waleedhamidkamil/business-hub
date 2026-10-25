/* Invoice autofill (2026-08-11).

   Two things were wrong, and one of them made the dropdown look broken.

   1. DELIVERY NOTES DROWNED THE ITEMS. A DN was matched on `d.search`, which
      is a blob of every ITEM NAME inside that DN. Typing an ordinary item
      therefore surfaced every DN containing it - and because DNs were placed
      unconditionally FIRST and capped at six, the real item suggestions were
      pushed off the list. It read as "the invoice only autofills DNs".
      A DN is now matched on its NUMBER only.

   2. THE REMEMBERED COST WAS A CURRENCY TRAP. `save_inv` stored the raw `cost`
      cell with nothing recording whether it was dollars or dinars, and the
      picker copied it in blind - so a 40,000 IQD cost replayed as $40,000.
      That exact bug was already found and fixed on the quotation side; the
      invoice now uses the same `cost_usd` normalisation, and the picker
      converts back into whatever the invoice is costing in.

   Assertions run against COMMENT-STRIPPED source: the prose above (and the
   comments in the file under test) name the very identifiers being searched
   for, so matching raw text would be matching the explanation.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }

const rawInv = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");
const rawApi = fs.readFileSync("backend/api.py", "utf8").replace(/\r\n/g, "\n");
const stripJs = (s) => s.replace(/\/\*[\s\S]*?\*\//g, " ")
                        .split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");
const stripPy = (s) => s.split("\n").filter((l) => !/^\s*#/.test(l)).join("\n");
const inv = stripJs(rawInv);
const apiPy = stripPy(rawApi);

/* ---------- 1. DN matching: number only ---------- */
{
  const fn = rawInv.slice(rawInv.indexOf("function invQueryMatchesDn("));
  const body = fn.slice(0, fn.indexOf("\n}") + 2);
  assert(/\bno\b/.test(body), "invQueryMatchesDn must look at the DN number");
  assert(!/\bsearch\b/.test(body),
    "matching on `search` is what let an item name pull in every DN containing it");

  /* Run the real predicate. */
  const match = new Function(body + "\nreturn invQueryMatchesDn;")();
  const dn = { no: "DN-TDC-044", search: "copper cable scaffolding pipe" };
  assert(match(dn, "d"), 'typing "d" should offer delivery notes');
  assert(match(dn, "dn-"), 'typing "dn-" should offer delivery notes');
  assert(match(dn, "tdc-04"), "a partial number should still match");
  assert(!match(dn, "cable"),
    "an ITEM name must not pull in the DN that happens to contain it");
  assert(!match(dn, "scaffolding pipe"), "same for a multi-word item name");
}

/* ---------- 2. regular suggestions are always asked for ---------- */
{
  const fn = inv.slice(inv.indexOf("async function onInvPrimaryInput("));
  const body = fn.slice(0, fn.indexOf("\n}") + 2);
  assert(/suggest_inv_items\(/.test(body),
    "the ordinary item suggestions must always be fetched");
  /* DNs and items end up in ONE list - a DN is just an entry you can pick. */
  assert(/concat\(/.test(body), "DN hits and item hits share one dropdown");
  /* ...and the items are not unconditionally last. */
  assert(/numberish/.test(body),
    "ordering must depend on whether the query looks like a document number");
  assert(/dnSug\.concat\(suggestions/.test(body) && /\.concat\(dnSug\)/.test(body),
    "both orders must exist: DNs lead a number-ish query, items lead otherwise");
}

/* ---------- 3. the picker never copies a raw cost ---------- */
{
  const fn = inv.slice(inv.indexOf("function pickInvSuggestion("));
  /* Start AFTER the whole-DN branch: it has its own repaint, so a slice that
     includes it still passes when the ITEM branch loses its own. */
  const itemBranch = fn.slice(fn.indexOf("row[pk] = s.name;"));
  const body = itemBranch.slice(0, itemBranch.indexOf("\n    if (ri === invState.rows.length - 1) addInvRow();"));
  assert(/k === "cost"/.test(body) && /continue/.test(body),
    "the raw `cost` key must be skipped - its currency was never recorded");
  assert(/k === "cost_ccy"/.test(body), "`cost_ccy` is not a column to copy");
  /* `cost_usd` is skipped via the shared `window.ITEM_META_KEYS` set (app.js)
     since 2026-08-16, which also covers img_query and the other-language name
     - those leaked into the dropdown subtitle as "arabic suggestions". */
  assert(/ITEM_META_KEYS\.has\(k\)/.test(body),
    "`cost_usd` is applied separately, not copied - via ITEM_META_KEYS");
  /* The key itself is no longer named here - it is skipped by ITEM_META_KEYS
     and then applied through the shared helper, which is what actually does
     the work. Assert the mechanism, not the spelling.

     2026-08-26 - that helper is now `costCellFromMemory`, the ONE the three
     editors share, and it answers with the number AND the unit it is written
     in. The invoice was the last editor still CONVERTING a remembered cost
     into its own currency; an item costed in dinars now fills as dinars with
     the cell's chip saying so, which is what Waleed asked for: the toggle
     shows the currency the number was saved with. The peg is still passed -
     an older entry with no native pair is dollars and still converts. */
  assert(/costCellFromMemory\(/.test(body) && /invPegSync\(\)/.test(body),
    "the remembered cost must go through the shared cost-cell helper, with "
    + "this invoice's peg for the legacy dollars-only case");
  assert(/invState\.costCurrency/.test(body),
    "the invoice's own cost currency is the fallback unit for a legacy entry");
  assert(/cc\.ccy/.test(body),
    "the unit the helper returns must be written to the row - a number whose "
    + "stamp says something else is read back at the wrong rate");
  assert(/paintRowCostChip\(/.test(body),
    "…and the chip must be repainted with it: the chip carries no data-k, so "
    + "the repaint loop below cannot touch it and a dinar cost would sit "
    + "under a '$'");
  /* The synthetic COST column is absent from editableInvColumns, so a
     remembered cost written to state was never painted - the quotation had
     exactly this bug and it looked like the cost was ignored. */
  assert(/invRenderColumns\(\)/.test(body),
    "the repaint must use invRenderColumns() so the COST cell is included");
}

/* ---------- 4. the cost is remembered in USD, from the payload ---------- */
{
  const save = apiPy.slice(apiPy.indexOf("def save_inv("));
  const block = save.slice(0, save.indexOf("def ", 10));
  assert(/_item_memory_payload\(/.test(block),
    "invoice item memory must go through the same payload builder as the quotation");
  assert(/inv\.get\("cost_currency"\)/.test(block),
    "the cost currency must come from the SAVE PAYLOAD - set_cost_currency runs " +
    "after save_inv returns, so a stored read is one save behind");
  assert(!/\{k: v for k, v in row\.items\(\)\s*\n?\s*if k != primary and k not in image_keys\}/.test(block),
    "the old raw-cost copy is still there");
}

/* ---------- 5. and the editor actually sends it ---------- */
{
  /* Guard the start of the key name: `x_cost_currency:` satisfies a loose
     match while sending the backend a field it never reads. */
  assert(/(^|[^\w])cost_currency:\s*invState\.costCurrency/m.test(inv),
    "inv.js must send cost_currency with the save");
  assert(/(^|[^\w])cost_iqd_per_usd:\s*invPegSync\(\)/m.test(inv),
    "inv.js must send the cost peg with the save");
}

console.log("Invoice autofill tests passed");
