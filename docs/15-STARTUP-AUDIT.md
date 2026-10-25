# Startup audit - 2026-10-02

Three alternating before/after pairs, each a fresh Python process and a real
pywebview/WebView2 window. Both variants used the same SQLite snapshots of the
live profile databases and shared library, the same assets and the same
persistent benchmark WebView2 directory. Snapshot databases absorbed setup and
test writes. Windows disk caches were left in their normal state; these are
repeated app starts, not reboot timings. The timer starts at application import,
so clicking the taskbar and creating the Python process adds a small extra cost.

| Milestone | Before median | After median |
|---|---:|---:|
| Dashboard controls visible | 1.991 s | 1.671 s |
| Dashboard data ready | 2.404 s | 2.120 s |
| Animation bridge requests after settling | 37 | 15 |

Data-ready ranges: before 2.389-2.552 s, after 2.108-2.229 s. Median improvement:
0.284 s (11.8%). Dashboard controls appear 0.320 s (16.0%) earlier. Both companies
also passed a real-window startup check: four animated dashboard icons, 32
animated toolbar icons, the theme animation, all four initialized document
modules, a newly opened PO editor, and the DN editor.

## Where the time goes

Representative baseline medians:

- Backend ready: 0.201 s total, including imports, profile migration integrity
  validation, schema setup and the migration ledger. A separate backend profile
  measured completed-profile validation at 23-34 ms, database setup at about
  8 ms and already-applied migrations at about 2 ms.
- .NET/WinForms/WebView2 platform initialization: about 0.507 s.
- Native window creation after platform initialization: about 0.170 s.
- Native window shown to document/bridge loaded: about 0.660 s.
- Document/bridge loaded to populated dashboard: median 0.860 s before,
  0.579 s after.

The features do not all initialize expensive libraries at startup. Excel's
`openpyxl` and the PDF merge dependency are already imported on demand. The
document backend and profile checks are a small part of the measured delay.

## Confirmed avoidable work

Startup requested the same Lottie JSON separately for repeated toolbar buttons.
About 35 requests were already in flight before the first dashboard completed,
37 after all icons settled. Every API call holds `_PROFILE_SWITCH_LOCK`, which
protects company identity during a profile switch. Animation file reads and
their bridge serialization therefore queued ahead of document lists and Needs
Attention. The lock is necessary and remains intact.

`loadLottieData` now reuses pending/successful requests per asset slug and clones
the result for each renderer. Failed/invalid results are discarded, keeping
retry and static-SVG fallback. `showHub` gives dashboard data priority and a paint
opportunity before starting the same animations. Independent date, profile and
preference reads start together, but every one completes before editors bind.

The profile switch, PDF generation, numbering, financial calculations,
migrations, validation and live-data cache rules were not changed.

## Development and QA files - retained

Current inventory, separate from live application data:

| Folder | Files | Size | Observed purpose |
|---|---:|---:|---|
| `tmp/` | 398 | 257.76 MB | Client-master regression baseline, reusable QA scripts, showcase tests/builders, sandbox and screenshot evidence |
| `outputs/` | 4 | 0.79 MB | Generated deliverables, including the missing-invoice-costs workbook |
| `scratchpad/` | 12 | 0.12 MB | Audit/repair scripts and historical evidence |
| `brag-output/` | 24 | 3.58 MB | Launch-video work |
| `tmpele2tp78/` | 0 | 0 MB | Empty temporary directory |

These folders do not occur in the startup import/HTML script path. That says
nothing about whether they are useful for testing, comparison, recovery or
rebuilding a deliverable. They are NOT established cleanup candidates.

Follow-up retention audit, requested by Waleed on 2026-10-02:

- `tmp/client-master-browser-qa.cjs`: manual browser QA for client editing,
  contact selection, saved payloads, unsaved-change prompts, light/dark theme and
  viewport widths. It writes the three client-master screenshots beside it.
  Keep the script and those screenshots as QA tools/evidence. It was read for
  this audit, not rerun or asserted to pass against the current application.
- `tmp/client-master-baseline/`: previous backend/frontend/templates and **17
  regression-test files** for historical comparison. These are outside the
  standard current `tests/` suite, but are related to the app. Keep.
- `tmp/showcase/rig/`: the isolated screenshot/demo pipeline, including **five
  test files** for bridge, capture, cards, sandbox and seeding. The QA code also
  protects live-data hashes and checks screenshots for private information.
  Its paths are documented in showcase handover/implementation plans. Keep.
- `tmp/showcase/presentation-v2/` and `presentation-v3/`: presentation source
  manifests, build scripts, slide-copy tests and output verification. The plans
  under `docs/superpowers/plans/2026-09-04-business-hub-*.md` reference these
  authoring and test paths explicitly. Keep so the showcase can be rebuilt.
- `scratchpad/rates-before.json`: directly consumed by
  `tests/rates_did_not_move.test.py`. This is a historical financial regression
  fixture; deleting it makes the check skip. Keep it and its provenance script.
- `outputs/invoice_costs_missing_20260815/`: a generated business workbook, not
  demonstrated junk. `brag-output/` contains launch-video work. Keep both.
- `tmp/pdfs/`, screenshot variants, intermediate reports and other output
  folders: purpose/replaceability is not fully established per file. Keep
  pending a specific artifact/dependency audit. The empty `tmpele2tp78/` folder
  is harmless and was also left alone.

The local Claude hook configuration runs a documentation reminder after edits,
not these temporary QA scripts. This does not rule out manual reuse by Claude.
No files were deleted. The older scratch-file map in [[10-SCRATCH-LEFTOVERS]]
predates moves and should not be treated as current deletion guidance.

The vendored PDF viewer, cropper, charts and animation library each have active
callers. They are features, not established dead code. Further lazy loading
would need separate measurements and workflow tests; it was not necessary for
this verified improvement.

## Verification

- JavaScript suites: 81/81 passed, including both company-switcher and
  dashboard error/staleness suites.
- New behavioral startup tests: deduplication, mutable-data isolation,
  rejection/invalid-asset retry, all three callers, dashboard priority on success
  and failure, paint opportunity, the editor boot gate, and concurrent mounting
  of toolbar/card/theme icons passed.
- Eleven deliberate mutations of the production source in diagnostic copies:
  11/11 caught by the new tests; the actual source then passed.
- `smoke_test.py` and `smoke_quote.py`: run through an isolated ProfileManager,
  generating real PDFs and checking their page text. Final outputs are recorded
  on the agent board.
- Real WebView2 startup for Abraj and Eshraqat: dashboard and all animation/editor
  initialization checks passed. No live document database was used for writes.

Diagnostic snapshots, timing JSON and test logs are under
`%LOCALAPPDATA%/BusinessHub/Diagnostics`, outside the runtime source tree.
