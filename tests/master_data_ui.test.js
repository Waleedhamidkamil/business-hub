/* Master Data screen - UI review fixes (2026-08-13).

   A design pass found three measurable failures and a handful of smaller
   issues. This pins them so they cannot quietly come back.

   The contrast checks CALCULATE the ratio from the real token values in
   app_styles.css / dark.css - they do not assert on a hard-coded colour, so
   they keep working if the palette is retuned and fail if it drifts below the
   floor. That is the whole point: the previous values passed review by eye.

   What broke, and why it mattered:
     · `.im-row` is tabbable but the app's only focus style is
       `button:focus-visible` - and a row is a <div>, so tabbing 400 rows
       showed nothing at all.
     · `--text-subtle` is 2.56:1 on white. It was the item description and the
       empty state.
     · In DARK mode the active condition chip inherited `color:#fff` over the
       amber `--warn`: 2.23:1. Light mode was fine, so a light-only check
       missed it.
*/
const fs = require("fs");

function assert(cond, msg) { if (!cond) throw new Error(msg); }
let checks = 0;
function ok(cond, msg) { assert(cond, msg); checks++; }

const css = fs.readFileSync("static/app_styles.css", "utf8").replace(/\r\n/g, "\n");
const dark = fs.readFileSync("static/dark.css", "utf8").replace(/\r\n/g, "\n");
const js = fs.readFileSync("static/item_master.js", "utf8").replace(/\r\n/g, "\n");
const html = fs.readFileSync("templates/index.html", "utf8").replace(/\r\n/g, "\n");
/* Comment-stripped, for the checks whose subject is the CODE SHAPE: the
   comments here explain the very patterns being searched for (the note beside
   the row markup literally says `role="button"`), so matching raw text would
   be matching the explanation. */
const code = js.replace(/\/\*[\s\S]*?\*\//g, " ")
               .split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n");

/* ---------- contrast maths (WCAG 2.1 relative luminance) ---------- */
function lum(hex) {
  const h = hex.replace("#", "");
  const v = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
  return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
}
function ratio(a, b) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}
function token(source, name) {
  const m = new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`).exec(source);
  assert(m, `token --${name} not found`);
  return m[1];
}

/* ---------- 1. the row focus ring ---------- */
{
  const m = /\.im-row:focus-visible\s*\{([^}]*)\}/.exec(css);
  ok(!!m, "rows are tabbable and MUST have a visible focus style");
  ok(/outline:/.test(m[1]) && !/outline:\s*(none|0)/.test(m[1]),
     "the focus style must actually draw an outline: " + m[1]);
  ok(/outline-offset:\s*-/.test(m[1]),
     "offset must be negative or the ring is clipped by the scroll container");
}

/* ---------- 2. text contrast, both themes ---------- */
{
  const lightBg = token(css, "surface");
  const darkBg = token(dark, "surface");

  const desc = /\.im-c-name i \{[^}]*color:\s*var\(--([a-z-]+)\)/.exec(css);
  ok(!!desc, ".im-c-name i must set a colour token");
  ok(desc[1] !== "text-subtle",
     "--text-subtle is 2.56:1 on white - too faint for the item description");

  const empty = /\.im-empty \{[^}]*color:\s*var\(--([a-z-]+)\)/.exec(css);
  ok(!!empty && empty[1] !== "text-subtle",
     "the empty state must not use --text-subtle either");

  [["description", desc[1]], ["empty state", empty[1]]].forEach(([what, tok]) => {
    const l = ratio(token(css, tok), lightBg);
    const d = ratio(token(dark, tok), darkBg);
    ok(l >= 4.5, `${what} light contrast ${l.toFixed(2)}:1 (needs 4.5)`);
    ok(d >= 4.5, `${what} dark contrast ${d.toFixed(2)}:1 (needs 4.5)`);
  });
}

/* ---------- 3. the dark-mode condition chip ---------- */
{
  /* `.im-chip.active` sets color:#fff. Over the DARK theme's --warn that is
     2.23:1. Light mode is 5.02:1, so this only shows up if dark is checked
     separately - which is exactly why it survived. */
  const warnDark = token(dark, "warn");
  /* SCOPED: dark.css is linked unconditionally, so an unprefixed rule
     applies in light mode too. The first version of this check only
     asserted the rule existed - and passed while light mode was broken. */
  const over = /html\[data-theme="dark"\]\s+\.im-flagchip\.active\s*\{[^}]*color:\s*(#[0-9a-fA-F]{6})/.exec(dark);
  ok(!!over, "the dark chip override must be scoped to html[data-theme=\"dark\"]");
  ok(!/(^|\n)\.im-flagchip/.test(dark),
     "an unscoped .im-flagchip rule in dark.css leaks into light mode");
  const r = ratio(over[1], warnDark);
  ok(r >= 4.5, `dark chip contrast ${r.toFixed(2)}:1 on ${warnDark} (needs 4.5)`);

  const warnLight = token(css, "warn");
  ok(ratio("#ffffff", warnLight) >= 4.5,
     "light mode chip must stay readable too");
}

/* ---------- 4. the picture marker is an icon, not a glyph ---------- */
{
  ok(!/▣/.test(js), "the ▣ text glyph must not be used as an icon");
  const svg = /const PIC_SVG = ([\s\S]*?);\n/.exec(js);
  ok(!!svg, "PIC_SVG not found");
  ok(/<svg/.test(svg[1]), "the picture marker must be an SVG");
  ok(/aria-hidden="true"/.test(svg[1]),
     "the svg is decorative - the wrapper carries the name");

  const icon = /const PIC_ICON = ([\s\S]*?);\n/.exec(js);
  ok(!!icon, "PIC_ICON not found");
  ok(/aria-label="Has a picture"/.test(icon[1]),
     "title alone is not announced - it needs an accessible name");
  ok(/PIC_SVG/.test(icon[1]),
     "the list marker must reuse the one SVG, not carry its own copy");
  /* The "stored rows" list this used to also check is GONE (2026-08-16): it
     listed the four catalogs an item had been copied into, and there is one
     row per item now. The shared-icon rule still holds for the list marker
     above - there is simply only one place left that draws it. */
}

/* ---------- 5. row semantics ---------- */
{
  ok(!/role="button"/.test(code),
     "a row contains a checkbox, so it must not be role=button");
  ok(/role="row"/.test(js), "rows should be role=row");
  ok(/role="cell"/.test(js), "cells should be role=cell");
  ok(/role="columnheader"/.test(js), "header cells should be role=columnheader");
  ok(/role="table"/.test(html), "the list container should be role=table");
  ok(/class="im-row[^"]*"[^>]*tabindex="0"/.test(js),
     "rows must stay keyboard-reachable");
}

/* ---------- 6. sortable column headers, both directions ---------- */
{
  ok(/data-sort="/.test(js), "column headers must offer sorting");
  ok(/aria-sort="\$\{down \? "descending" : "ascending"\}"/.test(js),
     "the sorting column must be announced with aria-sort, and it must say "
     + "WHICH way - a header that always claims 'descending' while showing an "
     + "ascending list is worse than saying nothing");
  ok(/\.im-sortbtn:focus-visible/.test(css),
     "header sort buttons need a focus ring");
  /* The arrow is decorative - the highlighted label carries the state too, so
     the cue is never colour or glyph alone. */
  ok(/im-sort-arrow[^>]*aria-hidden="true"/.test(js),
     "the sort arrow is decorative and must be hidden from screen readers");
  ok(/▼" : "▲/.test(js),
     "the arrow must point both ways, or the reader cannot tell a reversed "
     + "column from a fresh one");
  ok(/\.im-sortbtn\.is-on/.test(css),
     "the active header needs a rule that actually matches: aria-sort sits on "
     + "the columnheader SPAN, so `.im-sortbtn[aria-sort=...]` never applied");

  /* Clicking the column already sorting reverses it; a different column starts
     at its own default instead of inheriting the last one's direction. */
  ok(/state\.desc = \(state\.sort === key\) \? !sortIsDesc\(key\) : null;/.test(js),
     "same column = reverse, new column = its own default");
  ok(/state\.desc = null;/.test(js),
     "choosing from the dropdown must reset the direction too, or a column "
     + "picked there silently keeps the previous column's arrow");

  const sel = $sortSelectValues();
  /* The header keys come from the SORTABLE map, not from a `data-sort="..."`
     literal: the markup writes `data-sort="${key}"`, so scanning for literals
     matched NOTHING and every per-key assertion below it passed vacuously. */
  const smap = /const SORTABLE = \{([\s\S]*?)\};/.exec(js);
  assert(smap, "the SORTABLE map is missing from item_master.js");
  const used = [...smap[1].matchAll(/:\s*"([a-z_]+)"/g)].map((m) => m[1]);
  ok(used.length >= 8,
     `every column the reader can read should be orderable; only ${used.length} are`);
  used.forEach((k) => ok(sel.includes(k),
    `header sort key "${k}" is not one of the dropdown's values (${sel.join(", ")})`));
  ok(/sel\.value = state\.sort/.test(js),
     "clicking a header must keep the Sort dropdown in step");

  /* Every dropdown option must be a REAL sort key in the backend. "Longest
     idle" sat here for months with no `idle` key behind it, so it silently
     fell back to Most used - a control that lies is worse than none. */
  const py = fs.readFileSync("backend/master_items.py", "utf8");
  const spec = /_SORT_SPEC = \{([\s\S]*?)\n\}/.exec(py);
  assert(spec, "_SORT_SPEC not found in master_items.py");
  const keys = [...spec[1].matchAll(/"([a-z_]+)":/g)].map((m) => m[1]);
  sel.forEach((v) => ok(keys.includes(v),
    `the Sort dropdown offers "${v}", which is not a key in _SORT_SPEC - it `
    + `would silently fall back to another order`));
  used.forEach((k) => ok(keys.includes(k),
    `column header sort key "${k}" is not in _SORT_SPEC`));
}
function $sortSelectValues() {
  const block = /<select id="imSort">([\s\S]*?)<\/select>/.exec(html);
  assert(block, "the Sort select is missing");
  return [...block[1].matchAll(/value="([a-z_]+)"/g)].map((m) => m[1]);
}

/* ---------- 7. named controls ---------- */
{
  ok(/id="imSearch"[^>]*aria-label="Search items"/.test(html)
     || /aria-label="Search items"[^>]*id="imSearch"/.test(html),
     "the search field needs a name - its placeholder disappears on first keystroke");
  const groups = [...html.matchAll(/class="im-chiprow[^"]*"[^>]*>/g)].map((m) => m[0]);
  ok(groups.length >= 2, "expected both chip rows");
  groups.forEach((g) => ok(/role="group"/.test(g),
    "aria-label on a plain div is ignored - the chip row needs a role: " + g));
}

/* ---------- 8. focus follows the panel ---------- */
{
  ok(/head\.focus\(/.test(js),
     "opening an item must move focus into the panel");
  ok(/function focusRow\(/.test(js) && /focusRow\(was\)/.test(js),
     "closing must return focus to the row it came from");
}

/* ---------- 9. loading is announced, not just dimmed ---------- */
{
  ok(/aria-busy", "true"/.test(js) && /aria-busy", "false"/.test(js),
     "the list must mark itself busy while refetching");
  ok(/id="imStatus"[^>]*aria-live="polite"/.test(html),
     "a polite live region must report the result of a filter");
  /* Capture the whole number, leading dot included. An earlier version split
     it and then OR-ed two parses, so `.45` was read as 45 and passed. */
  const m = /\.im-scroll\.is-loading \{[^}]*opacity:\s*(0?\.\d+|[01](?:\.\d+)?)/.exec(css);
  ok(!!m, "the loading style is missing");
  const op = parseFloat(m[1]);
  ok(op >= 0.6,
     `loading opacity ${m[1]} dims already-low-contrast text too far`);
}

/* ---------- 10. the close button is a real target ---------- */
{
  const m = /\.im-x \{([^}]*)\}/.exec(css);
  ok(!!m, ".im-x rule missing");
  const h = /height:\s*(\d+)px/.exec(m[1]);
  ok(h && Number(h[1]) >= 32, "the close button was ~24px - too small to hit");
}

/* ---------- 11. the colour-blind rule still holds ---------- */
{
  /* Every condition on this screen must be a WORD, not a hue. The wording
     changed on 2026-08-16 - the old per-row "unusable cost" / "no cost"
     warnings described the four-catalog fold, and what an item is MISSING is
     now a filter you can act on. Still words, still counted. */
  ["No Arabic name", "No English name", "No unit", "No price", "No cost",
   "No picture"].forEach((w) => {
    ok(js.includes(w), `condition markers must be words - "${w}" is missing`);
  });
  /* And an empty cell says so with a DASH, never by colour alone. */
  ok(/im-c-missing/.test(js) && /"-"/.test(js),
     "a blank field must be marked by a character, not a hue");
  ok(/aria-label="Has a picture"/.test(js),
     "the picture marker states its meaning in text");
}

/* ---------- 12. description and unit exist in BOTH languages ---------- */
/* 2026-08-17. Waleed: "for each arabic item there is description in arabic and
   unit in arabic so that also needs to be included in each items details".
   The PO prints كيلو where the invoice prints KG, and one box per field kept
   whichever language it met first. */
{
  ["description_en", "description_ar", "unit_en", "unit_ar"].forEach((f) => {
    ok(new RegExp(`name="${f}"`).test(js),
       `the panel has no ${f} box - that language cannot be edited`);
    ok(js.includes(`${f}: (f.get("${f}")`),
       `${f} is shown but never sent back on save`);
  });
  ok(!/name="unit"[^_]/.test(js),
     "the old single unit box is still there - two of them would drift");

  /* The list shows both, stacked, in the cells that already exist. */
  ok(/bilingual\(i\.description_en,\s*i\.description_ar\)/.test(js),
     "the Description column shows only one language");
  ok(/bilingual\(i\.unit_en,\s*i\.unit_ar\)/.test(js),
     "the Unit column shows only one language");

  /* Colour-blind rule: the two languages are told apart by POSITION and script,
     never by colour. Each is its own line. */
  const b = /\.im-b-en,\s*\.im-b-ar \{([^}]*)\}/.exec(css);
  ok(!!b, ".im-b-en/.im-b-ar rule missing");
  ok(/display:\s*block/.test(b[1]),
     "the two languages must sit on their own lines, not be split by hue");

  /* And the Arabic line has to be READABLE - it is the thing he asked for. */
  const ar = /\.im-b-ar \{[^}]*color:\s*var\(--([a-z-]+)\)/.exec(css);
  ok(!!ar, ".im-b-ar must set a colour token");
  ok(ar[1] !== "text-subtle",
     "--text-subtle is 2.56:1 on white - too faint for the Arabic unit");
  ok(ratio(token(css, ar[1]), token(css, "surface")) >= 4.5,
     "the Arabic line fails contrast in light mode");
  ok(ratio(token(dark, ar[1]), token(dark, "surface")) >= 4.5,
     "the Arabic line fails contrast in dark mode");

  /* And the chip that finds what is still to fill in. */
  ok(js.includes("No Arabic unit"),
     "there is no way to list the items still missing an Arabic unit");
}

/* ---------- 13. an item can be added BY HAND ---------- */
/* 2026-08-19. Waleed: "we should also be able to fill it by hand by the way,
   its a master data at the end." A record used to appear only as a side effect
   of saving a document, which made this screen a report rather than a master. */
{
  ok(/id="imNewBtn"/.test(html), "there is no way to add an item by hand");
  ok(/New item/.test(html), "the button must say what it does, in words");
  ok(/imNewBtn"\)/.test(js) && /openNewItem/.test(js),
     "the New item button is not wired to anything");
  ok(/mi_create\(/.test(js), "creating must go through mi_create");
  /* The panel is the SAME one, so a field added to the record cannot be
     forgotten on the create form. */
  ok(!/function renderNewRecord/.test(js),
     "a second copy of the form would drift from the edit form");
  /* An item already on file is not a shouted failure - it says which one. */
  ok(/r\.already/.test(js),
     "creating a duplicate must point at the item that already exists");
}

/* ---------- 14. duplicates are shown as PAIRS ---------- */
/* 2026-08-26. Waleed: "possible duplicates tab can show each duplicate below or
   besides the original one to see the duplicate instead of looking around."
   The chip used to filter the 66 flagged records into the normal list in
   use-count order, so the two `Chest Freezer` rows sat five apart. */
{
  ok(/state\.missing === "duplicate"/.test(js),
     "the list must know it is showing the duplicates view");
  ok(/i\.dupe_group/.test(js) && /i\.dupe_role/.test(js),
     "it must draw the grouping the backend computed rather than inventing "
     + "its own order");
  ok(/im-dupe-sep/.test(js) && /\.im-dupe-sep/.test(css),
     "each group needs a divider, or the pairs run together");
  ok(/Duplicate of \$\{esc\(i\.dupe_of\)\}/.test(js),
     "a copy must NAME the record it duplicates - an indent alone is invisible "
     + "to a reader who cannot see the tint");
  ok(/>Original</.test(js),
     "and the record it copies must be labelled, in the user's own word");
  ok(/\.im-row\.is-dupe-copy/.test(css),
     "the copy rows need a rule");
  ok(/dupe_name/.test(js),
     "the divider must say WHICH name matched, or a group of look-alikes is "
     + "unexplained");

  /* The grouping is only for that view: the normal list must not sprout tags. */
  ok(/const grouped = state\.missing === "duplicate";/.test(js),
     "the grouped branch must be explicit, not a side effect of the data");

  /* ...and RUN it, because the assertions above only prove the words are in
     the file. This is the part that would actually break on screen. */
  const from = js.indexOf('            let sep = "";');
  const to = js.indexOf('            return sep + `<div class="im-row', from);
  assert(from >= 0 && to > from, "cannot slice the duplicate-row branch");
  const branch = js.slice(from, to);
  const _esc = (s) => String(s == null ? "" : s);
  const _num = (n) => (Number(n) || 0).toLocaleString();
  const run = (grouped, i, lastGroup) => new Function(
    "grouped", "i", "esc", "num", "label", "lastGroup",
    branch + "\nreturn { sep, dupeCls, dupeTag, lastGroup };")(
    grouped, i, _esc, _num, i.name_en || "(no name)", lastGroup);

  const pair = { dupe_group: 3, dupe_name: "Chest Freezer", dupe_size: 2 };
  const first = run(true, { name_en: "Chest Freezer", ...pair,
                            dupe_role: "primary", dupe_of: "" }, null);
  ok(/is-dupe-primary/.test(first.dupeCls) && />Original</.test(first.dupeTag),
     "the record being duplicated is labelled Original");
  ok(/im-dupe-sep/.test(first.sep) && /Chest Freezer/.test(first.sep),
     "the group opens with a divider naming the match");

  const second = run(true, { name_en: "Chest Freezer", ...pair,
                             dupe_role: "copy", dupe_of: "M-00157" }, 3);
  ok(/is-dupe-copy/.test(second.dupeCls), "the copy row is marked as one");
  ok(/Duplicate of M-00157/.test(second.dupeTag),
     "and NAMES the record it copies; got: " + second.dupeTag);
  ok(second.sep === "",
     "the divider is drawn once per group, not once per row - a line between "
     + "the pair would split exactly what this view puts together");

  const plain = run(false, { name_en: "Normal item" }, null);
  ok(plain.sep === "" && plain.dupeCls === "" && plain.dupeTag === "",
     "the ordinary list must stay exactly as it was");
}

console.log("master_data_ui: OK (" + checks + " checks)");
