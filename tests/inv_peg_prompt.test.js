/* The invoice exchange-rate prompt (2026-08-04).

   Flipping an invoice $ -> IQD multiplies every rate cell. The rate used to
   come from a server-side rule (1400 on Abraj, 1500 for cash / Eshraqat); it
   is now ASKED FOR at the moment of conversion, because the market rate moves
   and the rule cannot know today's.

   Three things here can lose money quietly, so all three are asserted against
   the REAL functions rather than by reading the source:

     - Cancel must leave the invoice completely untouched. A half-converted
       invoice - amounts multiplied, currency still USD - bills a client 1500x.
     - IQD -> $ must divide by the SAME rate it multiplied by. Deriving a fresh
       rate on the way back makes a there-and-back toggle silently change the
       invoice's dollar value.
     - Changing the rate afterwards must rescale by the RATIO, not multiply
       again, or the amounts are squared.
*/
const fs = require("fs");

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

const src = fs.readFileSync("static/inv.js", "utf8").replace(/\r\n/g, "\n");

/* ---------- load the real rate functions ---------- */
function build(opts) {
  const o = opts || {};
  const start = src.indexOf("const INV_DEFAULT_PEG");
  const end = src.indexOf("function invSettingsColumnsSnapshot");
  assert(start >= 0 && end > start, "cannot slice the rate block out of inv.js");

  // invConvertColumn and invSyncSwitchEl live above the slice; stub them so the
  // arithmetic is observable without a DOM full of rows.
  const converted = [];
  const invState = {
    sellCurrency: o.sellCurrency || "USD",
    sellPeg: o.sellPeg == null ? null : o.sellPeg,
    dirty: false,
  };
  const calls = [];
  const env = {
    invState,
    invConvertColumn: (key, factor, next) => converted.push({ key, factor, next }),
    invSyncSwitchEl: () => calls.push("sync"),
    updateInvTotals: () => calls.push("totals"),
    document: { getElementById: () => o.pegChipHost || null },
    esc: (s) => String(s),
    toast: (m, err) => calls.push(["toast", m, !!err]),
    safe: (fn) => fn,
    window: {
      showModal: async (spec) => { calls.push(["modal", spec.title]); return o.choice || "cancel"; },
    },
  };
  // The prompt reads its input back off the document; feed a fixed value.
  env.document.getElementById = (id) => {
    if (id === "invPegInput") return o.typed === undefined ? null
      : { value: String(o.typed), focus() {}, select() {}, addEventListener() {} };
    if (id === "invPegChip") return o.pegChipHost || null;
    return null;
  };
  env.document.querySelectorAll = () => [];

  const factory = new Function(
    "invState", "invConvertColumn", "invSyncSwitchEl", "updateInvTotals",
    "document", "esc", "toast", "safe", "window",
    src.slice(start, end) +
    "\n return { INV_DEFAULT_PEG, invSellPeg, askInvPeg, setInvSellCurrency," +
    " changeInvPeg, renderInvPegChip };");
  const api = factory(env.invState, env.invConvertColumn, env.invSyncSwitchEl,
                      env.updateInvTotals, env.document, env.esc, env.toast,
                      env.safe, env.window);
  return { api, invState, converted, calls, env };
}

async function run() {
  /* ---------- 1. the default is 1500 ---------- */
  {
    const { api, invState } = build({});
    assert(api.INV_DEFAULT_PEG === 1500,
      `the default rate must be 1500, got ${api.INV_DEFAULT_PEG}`);
    assert(api.invSellPeg() === 1500,
      "with no rate chosen yet, invSellPeg must fall back to the default");
    invState.sellPeg = 1480;
    assert(api.invSellPeg() === 1480, "a chosen rate must win over the default");
    for (const bad of [0, -5, NaN, null, undefined, "abc"]) {
      invState.sellPeg = bad;
      assert(api.invSellPeg() === 1500,
        `a nonsense stored rate (${bad}) must fall back to the default, not be `
        + "used as a multiplier");
    }
  }

  /* ---------- 2. Cancel converts nothing ---------- */
  {
    const { api, invState, converted } = build({ choice: "cancel", typed: "1500" });
    await api.setInvSellCurrency("IQD");
    assert(converted.length === 0,
      "Cancel must not convert a single cell - a half-converted invoice with "
      + "IQD amounts still labelled $ is the worst outcome available here");
    assert(invState.sellCurrency === "USD",
      "Cancel must leave the invoice in USD");
    assert(invState.dirty === false,
      "Cancel must not even mark the editor dirty - nothing changed");
  }

  /* ---------- 3. Convert multiplies by the typed rate ---------- */
  {
    const { api, invState, converted } = build({ choice: "ok", typed: "1480" });
    await api.setInvSellCurrency("IQD");
    assert(converted.length === 1, "expected exactly one column conversion");
    assert(converted[0].key === "rate",
      "only the RATE column converts - the cost column is private and has its "
      + "own currency switch");
    assert(converted[0].factor === 1480,
      `expected a x1480 conversion, got x${converted[0].factor}`);
    assert(invState.sellPeg === 1480, "the typed rate must be recorded on the invoice");
    assert(invState.sellCurrency === "IQD" && invState.dirty === true,
      "a successful conversion flips the currency and marks the editor dirty");
  }

  /* ---------- 4. a typed rate with a comma still works ---------- */
  {
    const { api, invState } = build({ choice: "ok", typed: "1,480" });
    await api.setInvSellCurrency("IQD");
    assert(invState.sellPeg === 1480,
      "'1,480' must parse - people type thousands separators in a money field");
  }

  /* ---------- 5. junk is refused, and refuses the whole conversion ---------- */
  {
    for (const junk of ["", "abc", "0", "-3"]) {
      const { api, invState, converted, calls } = build({ choice: "ok", typed: junk });
      await api.setInvSellCurrency("IQD");
      assert(converted.length === 0 && invState.sellCurrency === "USD",
        `"${junk}" must abort the conversion rather than convert by it`);
      assert(calls.some(c => c[0] === "toast" && c[2] === true),
        `"${junk}" must say why nothing happened`);
    }
  }

  /* ---------- 6. the round trip is lossless ---------- */
  {
    const { api, invState, converted } = build({
      sellCurrency: "IQD", sellPeg: 1480, choice: "ok", typed: "1480" });
    await api.setInvSellCurrency("USD");
    assert(converted.length === 1 && converted[0].next === "USD", "expected one USD conversion");
    assert(Math.abs(converted[0].factor - 1 / 1480) < 1e-12,
      `going back to $ must divide by the SAME 1480 it multiplied by, got a `
      + `factor of ${converted[0].factor}. Deriving a fresh rate here would `
      + "change the invoice's dollar value just by toggling twice.");
    assert(invState.sellCurrency === "USD");
  }

  /* ---------- 7. going back to $ asks nothing ---------- */
  {
    const { api, calls } = build({
      sellCurrency: "IQD", sellPeg: 1480, choice: "ok", typed: "1480" });
    await api.setInvSellCurrency("USD");
    assert(!calls.some(c => c[0] === "modal"),
      "IQD -> $ must not prompt: the only correct rate is the one the amounts "
      + "were converted at, and offering a choice invites a lossy round trip");
  }

  /* ---------- 8. changing the rate rescales by the RATIO ---------- */
  {
    const { api, invState, converted } = build({
      sellCurrency: "IQD", sellPeg: 1500, choice: "ok", typed: "1480" });
    await api.changeInvPeg();
    assert(converted.length === 1, "expected one rescale");
    assert(Math.abs(converted[0].factor - 1480 / 1500) < 1e-12,
      `re-pricing 1500 -> 1480 must scale by 1480/1500, got ${converted[0].factor}. `
      + "Multiplying by 1480 again would square the amounts.");
    assert(invState.sellPeg === 1480, "the new rate must be recorded");
  }

  /* ---------- 9. re-pricing to the same rate is a no-op ---------- */
  {
    const { api, converted } = build({
      sellCurrency: "IQD", sellPeg: 1500, choice: "ok", typed: "1500" });
    await api.changeInvPeg();
    assert(converted.length === 0,
      "re-entering the same rate must not touch the amounts - a x1.0 pass would "
      + "still round every cell");
  }

  /* ---------- 10. a USD invoice has no rate to change ---------- */
  {
    const { api, converted, calls } = build({
      sellCurrency: "USD", sellPeg: null, choice: "ok", typed: "1480" });
    await api.changeInvPeg();
    assert(converted.length === 0 && !calls.some(c => c[0] === "modal"),
      "changeInvPeg must do nothing on a USD invoice - there is nothing "
      + "converted for a rate to apply to");
  }

  /* ---------- 11. the chip shows the rate, and only in IQD ---------- */
  {
    const host = { hidden: false, innerHTML: "", querySelector: () => null };
    const a = build({ sellCurrency: "USD", pegChipHost: host });
    a.api.renderInvPegChip();
    assert(host.hidden === true && host.innerHTML === "",
      "a USD invoice must not show an exchange-rate chip");

    const host2 = { hidden: true, innerHTML: "", querySelector: () => null };
    const b = build({ sellCurrency: "IQD", sellPeg: 1480, pegChipHost: host2 });
    b.api.renderInvPegChip();
    assert(host2.hidden === false, "an IQD invoice must show the chip");
    assert(/1,480/.test(host2.innerHTML),
      "the chip must show the actual rate, grouped for readability; got: " + host2.innerHTML);
    assert(/1 \$ =/.test(host2.innerHTML),
      "the chip must state the direction - '1500' alone is ambiguous");
  }

  /* ---------- 12. the save payload carries the rate, IQD only ---------- */
  {
    assert(/sell_iqd_per_usd:\s*\(invState\.sellCurrency \|\| "USD"\) === "IQD" \? invSellPeg\(\) : null/.test(src),
      "the save payload must send sell_iqd_per_usd for an IQD invoice and null "
      + "otherwise - a rate on a USD invoice would make a later flip look as "
      + "though the amounts had already been converted");
    assert(/invState\.sellPeg = Number\(rec\.sell_iqd_per_usd\) > 0/.test(src),
      "loading an invoice must restore its recorded rate, or reopening it and "
      + "saving would silently re-value it at the default");
  }

  console.log("Invoice exchange-rate tests passed");
}

run().catch((err) => { console.error(err); process.exit(1); });
