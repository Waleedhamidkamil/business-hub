# Master Data Doctor - design

## Problem

The Master Data screen already reports what is missing - its condition chips
carry live counts (no Arabic name 621, no English name 8, no unit 105, no price
195, no cost 419, no picture 575, possible duplicates 8). What it cannot do is

1. **fix anything in bulk.** Filtering to 621 records leaves the user typing
   each one by hand.
2. **see a problem that is not a blank field.** Measured on the live catalogue
   2026-09-08: 21 groups / 43 records share a name and a description (18 of them
   disagreeing on the unit, 12 on the price), 24 descriptions carry a date typed
   into them, 115 records hold a newline inside a name or description, and two
   records can be the same product under two different words (`خس صيني` and
   `لهانة صيني`, both Chinese Cabbage) which no string comparison will ever
   pair.
3. **remember a ruling.** "These two are not duplicates" has nowhere to live, so
   the chip reports them for ever.

The cost is not tidiness. `Round Table` exists twice - M-01804 (30 Aug, $300)
and M-01833 (6 Sep, $400) - so the catalogue can hand an invoice either price.

## The prerequisite nobody has written down

A document stores an item's WORDS, not its id (`{"name": "ماطور كهرباء",
"description": "2 حصان", "unit": "عدد", ...}`), so nothing points at
`item_id` and merging cannot break an existing document.

But `merge_records` DELETES the losing rows, and the only way to find a record
is its own two names - `master_data._items_from_records` builds `aliases` from
`name_en` and `name_ar` and nothing else. So merging "Wall Split Unit" into
"Wall-Mounted Split AC" makes the words "Wall Split Unit" stop matching
anything, and the next document that uses them creates a fresh duplicate.

**A Doctor that merges without an alias store re-creates the duplicates it just
cleaned.** The alias table is therefore part of this feature, not a later one.

## Approved outcome

One **Review mode on the Master Data screen** - not a new screen, so it reuses
the record list, selection and Merge that already work. It produces a worklist
of findings; each is a card carrying what is wrong, the evidence, a proposed
fix, and Accept / Edit / Not an issue.

Waleed's rulings, 2026-09-08:

- **Categories: yes.** Twelve derived from this catalogue plus `Other`.
- **The 18 unit conflicts are VARIANTS, not duplicates.** FLOUR at BAG / BOX /
  KG is three real products. The finding is therefore "these variants cannot be
  told apart - give them descriptions", never a merge proposal.
- **Prices may inform the model, but amounts must not leave the machine.** The
  comparison is computed here and sent as `same` / `close` / `far`.
- Still open, and left alone until he rules: the 26 `Common Laborer` records,
  several carrying `Date:18/07/2026` in the description.

## Findings

| Kind | Today | Detector | Proposed fix | Decided by |
|---|---|---|---|---|
| `duplicate` - same name, description and unit | 4 groups | local | merge into the most used, keeping the losers' names as aliases | one click |
| `variant_needs_description` - same name + description, different unit | 18 groups | local | give each a description that tells them apart | user, AI may propose |
| `price_conflict` - same identity, different price | 12 groups | local | user picks, or says they are different products | user only |
| `same_product` - one product under two different words | unknown until scanned | **Gemini** over locally shortlisted clusters | merge, with the model's reason shown | user |
| `junk_date` - a date typed into a description | 24 | regex | strip it | one click |
| `junk_newline` - newline inside a name or description | 115 | regex; **Gemini** only to split a spec off a name | normalise, or split into a description | one click / user |
| `no_unit` | 105 | local (`unit_english_map` vote first) | fill it | user |
| `no_category` | 901 | local for the 87 whose unit is a time unit; **Gemini** for the rest | set the category | user |
| `no_name_ar` / `no_name_en` | 621 / 8 | **Gemini**, on demand per page | fill it, marked | user |

Five of the nine need no model at all. `no_name_ar` looks alarming and is not:
only **3 of the 621** were ever bought for a company, so they are the English
side of the business (catering, supplies) that is never typed in Arabic. It is
served on demand, never as a campaign.

## Components

### `master_items` - two new tables and one column

```sql
CREATE TABLE IF NOT EXISTS master_aliases (
    alias_norm  TEXT PRIMARY KEY,      -- master_data._norm of the spelling
    alias       TEXT NOT NULL,         -- as written, for display
    item_id     INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT '',  -- merge | review | manual
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS master_review (
    kind        TEXT NOT NULL,         -- the finding kind
    key         TEXT NOT NULL,         -- stable identity, NOT item ids
    decision    TEXT NOT NULL,         -- dismissed | applied
    decided_at  TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
ALTER TABLE master_items ADD COLUMN category TEXT NOT NULL DEFAULT '';
```

Both live in `master_items.SCHEMA`, created by `master_items.init`, and the
column is added by the existing `_migrate`. They are deliberately NOT in
`shared_library.SHARED_SCHEMA` - that is where `master_items` itself lives, and
a table declared in both places is a table that exists in one kind of database
and not the other.

A dismissal is keyed on the finding's WORDS, never on `item_id`, because a merge
deletes ids and a re-scan would resurrect every ruling.

### Aliases reach the resolver

`master_data._items_from_records` gains the stored aliases alongside the
record's own two names, so a merged-away spelling still finds its record.
`resolve_items` needs no change: a spelling that becomes ambiguous (an alias of
one record and the real name of another) lands in `by_name` with two entries and
the standing "a tie is never a match" rule already handles it.

`merge_records` records the losing records' distinct names as aliases inside the
same transaction as the merge.

### `backend/master_doctor.py`

`scan(kinds=None, limit=0)` returns findings, newest problem first, each
`{kind, key, title, evidence, proposal, item_ids}`. Pure reads; it writes
nothing and never calls the network.

`apply(kind, key, choice)` performs one finding's fix inside a transaction and
records the decision. `dismiss(kind, key)` records a ruling without changing
data. A session takes ONE restore point under `data/master-data-backups/`
before its first apply, exactly as the 2026-09-07 cleanup did, because a merge
deletes rows.

### Gemini

Reached through `backend/translate.py`'s existing transport (one bounded,
stdlib-only, never-raises call), extended with a second prompt shape rather than
a second HTTP path. Sends item words, units and category names. Money is sent
only as `price: same | close | far`. Never a document number, client, company,
amount, item id or item number. As everywhere else in this app, an answer is a
SUGGESTION: it lands marked, and Apply is a separate press.

### Categories

Twelve derived from this catalogue's own vocabulary, plus `Other`: Food &
Catering, Manpower, Vehicles & Transport, Fuel & Lubricants, Tools & Hardware,
Electrical & AC, Plumbing, Safety & PPE, Office & Stationery, Furniture,
Cleaning & Consumables, Building Materials.

The unit is a free local signal: `MONTH`, `PER DAY`, `PER HOUR` and `PER MONTH`
cover 87 records that are Manpower or hire, so those are proposed without an API
call.

## Safety

- Nothing applies without a press. No scan writes.
- One restore point per session; a merge is otherwise irreversible.
- Dismissals persist and survive a re-scan and a merge.
- Offline-first: every AI control is hidden when `tr_status().configured` is
  false, and every local finding still works.
- The screen is paged; the AI button acts on the page in front of the user, so
  no press ever waits on eleven round trips.
