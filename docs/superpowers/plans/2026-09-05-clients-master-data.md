# Clients Master Data Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the independent client UI task, then review spec and quality. Root owns backend, integration, cleanup, and delivery. Execute continuously under the approved Clients-only scope.

**Goal:** Add Clients beside Materials and clean the client catalogue recoverably.
**Architecture:** Separate client_records, client_aliases, and client_preferences tables in the shared library. Existing clients names remain a compatibility index. A standalone client UI calls cm_* bridges. No material module or schema edits.
**Tech Stack:** Python, SQLite, vanilla JS/CSS, pywebview, unittest and Node tests.

## Task 1: Client storage and guards

Files: backend/client_master.py (new), tests/client_master_backend.test.py (new), narrow integration in backend/database.py and backend/shared_library.py.

- [ ] Write and run isolated tests before code for save/get, alias collision, archive/restore, account ownership, aliases preserving documents, and material fingerprints.
- [ ] Implement the following public contract:

```python
list_records(search="", status="active")  # {ok, records, total}
get_record(identity)                     # {ok, record, bank_options, profile_id}
save_record(payload)                    # {ok, record} or {ok:False,error}
set_archived(identity, archived=True)    # {ok, record}
defaults_for(name)                       # {ok,name,address,payment_bank_key,...}
aliases_for(name)                        # list[str], includes input if unknown
canonical_map()                         # normalized alias -> canonical display name
remember(name)                          # catalog save, never unarchives
```

Record fields: id, name, legal_name, name_ar, aliases (list[str]), contacts (list of name/role/email/phone/primary), addresses (list of label/address), website, tax_id, registration_no, notes, archived, updated_at, defaults, usage. defaults contains payment_method (cash/bank_transfer/empty), bank_key, alternative_bank_keys, currency (USD/IQD/empty), payment_terms_days (nullable integer), invoice_instructions, withholding (nullable bool), address. Defaults are stored against current profile only. Bank options expose key/label/currency, no account duplication.

- [ ] Init only client tables and import existing client names idempotently. Preserve archived status. Replace repeated seed behavior with a one-time shared marker.
- [ ] Keep every connection closed and every failed save atomic. Validate types, lengths, duplicate names, alias ownership, missing banks, and expected updated_at.
- [ ] Bridge methods cm_list, cm_get, cm_save, cm_archive, cm_defaults delegate to this module.
- [ ] Run the new backend tests and inspect the diff.

## Task 2: Dedicated client screen

Files: static/client_master.js (new), static/client_master.css (new), static/client_defaults.js (new if useful), tests/client_master_ui.test.js (new), templates/index.html and static/app.js (minimal navigation additions).

- [ ] Before code, add a behavior test for contact/default payload round trip and alias search/escaping. Verify missing functionality fails.
- [ ] Add Materials/Clients navigation using showItemMaster and new showClientMaster. Add clientMasterView to ALL_VIEWS. Never edit static/item_master.js or its storage/rendering code.
- [ ] Build a searchable client list with Active/Archived/All filter and New client. Form sections: identity/aliases, contacts, addresses, payment preferences, notes. Save, archive/restore, and view documents. Reuse existing Popup for confirmation and dirty navigation handling.
- [ ] Connect the exact cm_* contract above, show errors, and prevent stale fetch responses replacing newer selections.
- [ ] Provide a deliberate Apply client defaults action in QUT/DN/INV that fills blank/new values and refuses to overwrite loaded/manual values. Use existing change handlers for payment/currency.
- [ ] Test rendering and behavior in browser with demo bridge. Verify both themes, long names, keyboard labels, and Materials return navigation.

## Task 3: History and test isolation

Files: backend/clients.py, backend/insights.py, backend/data_manager.py, tests/isolated_client_data.py (new) and exact leaking tests.

- [ ] Group history by aliases with query parameter lists, preserving saved names, identifiers and amounts. Consolidate client_list using += rather than overwriting one alias count.
- [ ] Route legacy add/delete/rename and generic client catalogue edits through client master semantics. Archive instead of destructive document renaming.
- [ ] Add an isolated test-data helper before backend configuration imports in the known fixture producers. Use a temporary ProfileManager and shared DB, restore globals in finally/atexit, never modify live material data.
- [ ] Run targeted old fixture tests in isolation and prove the live catalogue is unchanged.

## Task 4: Reviewed cleanup and delivery

Files: scripts/cleanup_client_master.py (new), focused tests, owning map docs.

- [ ] Build a dry-run-first cleanup using the exact 14 test and 16 archive names in the approved spec. Include current aliases, both profile docs/LPOs, PO allocations and material company references in deletion guards.
- [ ] Hold AGENT-BOARD test lock. Take SQLite backup, rehearse on copies, and compare complete material and document fingerprints.
- [ ] Apply checked cleanup, add HDEC aliases and user-confirmed TDC/HDEC preferences, preserve PDFs and historical row text.
- [ ] Verify exact counts, restore support, material hashes, document hashes, and no preset resurrection.
- [ ] Run new tests, focused material regression checks, both smokes on isolated data, and review UI evidence.
- [ ] Update owning docs, stage only owned new files or safely isolated integration hunks, commit, release board claim/lock, and deliver counts/recovery location.
