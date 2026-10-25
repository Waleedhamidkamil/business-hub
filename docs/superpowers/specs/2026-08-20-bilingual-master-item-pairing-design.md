# Bilingual Master Item Pairing

**Date:** 2026-08-20

## Goal

Turn separate English-only and Arabic-only records for the same product into
one Master Data item holding both names. A confirmed pair gets one item number,
while saved documents keep their historical line text and amounts unchanged.

## Scope

This pass cleans the current shared Master Data. It does not use or import
`FIN.xlsx`, because that separate workbook task is paused. It does not invent
translations or merge merely because two words look similar.

## Evidence and confidence

A pair is confirmed only when the English and Arabic records describe the same
product and their unit meanings agree. Evidence may come from an existing
bilingual record, an explicit document relationship, a previously confirmed
translation, or a clear product translation supported by compatible details.
Cost and price may support a match but never prove one by themselves.

Candidates are refused or sent to review when:

- units conflict, such as `KG` versus `BOX`;
- descriptions identify different sizes, models, grades, or packs;
- either name can reasonably mean several products;
- more than one record is an equally plausible partner.

## Merge behavior

For each confirmed pair:

1. Keep one `master_items` row and one item number.
2. Fill `name_en` from the English record and `name_ar` from the Arabic record.
3. Keep both language-specific descriptions and units.
4. Preserve every non-conflicting price, cost, image, usage count, and date.
5. When both sides carry different current money values, prefer the newer
   record and report the choice; saved documents retain their historical values.
6. Delete only the redundant Master Data row. Saved documents are never edited.

The survivor is chosen by newest use, then completeness, then use count. This
keeps the most current record while learning missing fields from its partner.

## Safety

- Run a read-only audit first and record every proposed pair.
- Back up `data/shared/library.db` before writing.
- Apply all confirmed merges in one transaction.
- Abort the transaction if a proposed record changed after the audit.
- Read every merged record back and verify both names, compatible units, one
  item number, and preserved money/image fields.
- Keep uncertain pairs unchanged and show them to Waleed for approval.

## Success criteria

- Eggplant and باذنجان are one item with both names.
- Every applied pair leaves exactly one Master Data record.
- No incompatible unit or product variant is merged.
- Saved documents and their historical amounts are unchanged.
- The remaining uncertain pairs are presented as a short review list.
