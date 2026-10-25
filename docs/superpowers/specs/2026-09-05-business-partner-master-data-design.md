# Clients Master Data and safe catalogue cleanup

Date: 2026-09-05
Status: Approved direction, amended by the user to Clients and Materials only.

## Outcome and scope

Add Clients Master Data beside existing Materials Master Data. No suppliers, supplier roles, or supplier accounts. Preserve the existing material schema, records, matching, costs, currencies, images, APIs, and editor. Client-only tables and a separate UI module own the new feature.

The user confirmed ADIB and Bank of Baghdad are OUR receiving accounts. Client preferences reference the actual account keys in the active company profile, never duplicate accounts or copy them between profiles.

## Audit and cleanup targets

The shared catalogue held 38 client names. Eight spellings occur on 168 saved client documents across both profiles: HDEC (74), its two Hyundai variants (2 and 5), TDC (82), Gusan (1), Hanjun Co. (2), JUNE CONSTRUCTION CO. (1), and ABRAJ (1). Both profiles had zero received LPO rows. Audit commit 5dd96f8 records the read-only investigation.

Remove these 14 confirmed test client catalogue names only after a fresh cross-profile dependency check: __POMEM_TEST__ Client, __USEDIN__ Client, Autolink Test Client, C, Client, Copy To Invoice Test Client, Cost Scope Client, Delete Test Client, LPO Client A __LPOTEST__, PO Link Client, Re-sign Test Client, Signed Copy Test Client, Signed Page Test Client __SIGNPAGE__, Source DN Test Client.

Archive these 16 other unused names reversibly: 77 Insaat, ACME, AUDCO, BETA, Chemie-Tech, Daeah, Daeshin, Daesun, Demo, ILK Construction, JGC Corporation, LINKCO, PVWCO, RENTCO, TESTCO, X.

Cause: several tests delete temporary profile documents but leave shared catalogue names created by document saves. Startup also re-inserts nine preset names. Fix both causes, not just the visible entries. Never reject legitimate client names merely because they contain Test or Client.

## Record and navigation

Master Data has Materials and Clients tabs. Materials uses its existing entry point. Preserve backend/master_items.py, backend/master_data.py, static/item_master.js, and material CSS byte-for-byte. Only add a sibling navigation button to the existing heading.

A client has a stable ID, short/display name, legal name, Arabic name, aliases, contacts (name, role, email, phone, primary), billing/delivery/registered addresses, website, tax/registration reference, internal notes, and Active/Archived status.

Preferences are separate per profile: payment method, preferred and alternative receiving accounts, currency, payment terms, invoice instructions, and optional withholding preference. Defaults can be applied deliberately to a new document or blank fields, preserving loaded/imported documents and manual choices. Existing editor payment/currency behavior remains authoritative.

Archive hides from normal selectors but offers Restore. Exact normalized aliases are searchable; collisions are reported instead of silently merging unrelated records. Existing client maintenance routes use the same master operations.

## Initial examples and history

HDEC groups its existing Hyundai Engineering & Construction and Hyundai Engineering and Construction Co., Ltd spellings as aliases without rewriting historical client names, numbers, PDFs, signatures, paths, or LPO references. User wording Hyundai Engineering & construction ltd is another alias. Editable legal name may use the existing long spelling, without claiming legal verification.

HDEC prefers bank transfer. Abraj currently uses bob (Bank of Baghdad) with adib as an alternative. Eshraqat has its own uppercase ADIB key, and no Bank of Baghdad account. A missing account must be labeled and never replaced with one from another profile.

TDC prefers cash, matching 37 of 39 Abraj invoices. Store CCCC ltd as the user-provided alternative name without asserting a verified legal identity. ADIB remains available as an override.

Client history and Insights resolve aliases while staying scoped to the active profile and avoiding double counting. Drafts retain current behavior. Document numbering is unchanged. Company/job allocations and material company memory are outside cleanup scope.

## Data protection and verification

Acquire the shared test lock and check running suites. Take a SQLite-consistent backup before live writes. Rehearse on copies and preserve a cleanup manifest with exact removed records. Fresh usage checks cover both profiles, drafts, received LPOs, PO allocation fields, and material company memory.

Compare complete material-table and saved-document fingerprints before and after live cleanup. No document or file deletion. Prevent preset re-insertion and silent unarchiving. Isolate known test producers so their writes cannot reach the real shared catalogue.

Use isolated profile and shared DBs for tests. Cover persistence, aliases/collisions, profile banks, archive/restore, deletion protection, unchanged history, and material preservation. Run targeted behavior checks and both required smokes. Inspect the UI with long English/Arabic names, both themes, and keyboard navigation, using the existing Popup contract and text status labels.

Preserve unrelated dirty work. Snapshot touched files before editing. Update owning map docs and commit only owned changes. The user corrected and authorized the Clients-only direction; no further design approval is needed.
