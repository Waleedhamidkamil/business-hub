# PO Tool - Codebase Map (start here)

This folder is a **complete, cross-linked map of the Business Hub app** (formerly "ABRAJ Documents Hub" / "PO Tool") - one markdown note per subsystem. It exists so any AI tool (Claude Code, Cursor, Copilot, …) or person can understand the whole app and find the exact files a task needs **without reading all ~35k lines of source**. Fewer tokens, accurate answers, fewer broken features.

## How to use it
1. **Read the map first** - this page → [[01-ARCHITECTURE]] → the specific doc for your area.
2. **Then read the real file before you edit it.** The map tells you *where* things are and *how they relate*; it does not replace reading the actual code you're about to change (the map can lag the code by one edit).
3. **After changing code, update the owning doc in the same change** - see the ownership table + protocol in [[11-RUN-AND-MAINTAIN]]. A stale map is worse than no map.

## How to open it
It's plain markdown - read it anywhere. For a visual, navigable version, **open this `docs/` folder as an [Obsidian](https://obsidian.md) vault** ("Open folder as vault") and the `[[wiki-links]]` below become a clickable graph. Obsidian is optional and only for humans; agents read the `.md` files directly.

## The map

| # | Doc | What's in it |
|---|---|---|
| 01 | [[01-ARCHITECTURE]] | Big picture: runtime/layering, the `api()` bridge contract, the five-parallel-doc-types table, end-to-end flows, and the **Impact Map (touch X → check Y)**. |
| 02 | [[02-BACKEND-CORE]] | Entry point (`app.py`), the `backend` package marker, the `Api` bridge class (~113 methods exposed to JS), and settings (`config.py`). |
| 03 | [[03-BACKEND-DATA]] | Persistence: `database.py`, `migrations.py`, the data-manager, client profiles, and analytics. |
| 04 | [[04-BACKEND-DOCGEN]] | Document generation: the 5 numbering modules, 5 generators, headless-browser PDF engine, and signing overlays. |
| 05 | [[05-FRONTEND-SHELL]] | The SPA shell: `app.js`, the 19-view system, the `api()` bridge helper, and the PO editor + `index.html`. |
| 06 | [[06-FRONTEND-EDITORS]] | The Quote / Delivery-Note / Invoice editors + the shared open/browse panel. |
| 07 | [[07-FRONTEND-SUPPORT]] | Cross-cutting UI: costing, data-manager, analytics/charts, clients, signing, doc-settings, bulk/grid paste, column resize. |
| 08 | [[08-STYLES-TEMPLATES]] | The stylesheets (coarse), the Jinja `*_page.html` print templates, and the vendored libraries. |
| 09 | [[09-DATA-MODEL]] | The SQLite schema (every table), the on-disk document folder layout, and the settings/`jsons/` files. |
| 10 | [[10-SCRATCH-LEFTOVERS]] | Every throwaway/dev-scratch file in the repo, classified - so trash is never mistaken for real code. |
| 11 | [[11-RUN-AND-MAINTAIN]] | How to run/debug/smoke-test, and the **map-update protocol** for keeping this vault fresh. |
| 12 | [[12-PDF-LAYOUT-CONSTITUTION]] | The **universal print-layout rules** all generators obey: page ROLES, space-use per 1/2/N-page scenario, anti-overlap invariants (table vs page-number / footer / stamp), and the per-doc-type constants. Read before touching any pagination. |
| 14 | [[14-UI-CONTRACTS]] | The **shared UI rules** - the handful of things that must be identical everywhere, each with one implementation, its reasons, and a test that fails when it drifts. Popups first (`static/popup.js`); the older contracts (never red/green, private figures, currency, unknown ≠ zero) are indexed there too. Read before adding anything that opens over the page or that a second screen will want to copy. |
| 13 | [[13-PROFILES-BRANDS]] | The **multi-company profile subsystem** (Abraj + Eshraqat): per-profile DB/settings/assets/documents-root, the runtime switcher, permanent Eshraqat numbering, brand skins, shared autocomplete library, backup/restore, legacy fallback. Read before touching paths, numbering, branding, or anything import-time-config-bound. |
| 15 | [[15-STARTUP-AUDIT]] | Measured startup stages, dashboard-first animation loading, verified timing improvement, and unused development-file inventory. |
| 16 | [[16-CLIENT-LPO-AUDIT]] | Client LPO functionality, data and UI/UX audit: confirmed defects, isolated checks and improvement priorities. |
| 17 | [[17-CLIENT-LPO-FIXES]] | Verified LPO fixes, one service-period ledger, reference review and an interactive workspace design suggestion. |
| 18 | [[18-OFFICE-SERVER]] | The **office server**: gateway, one engine per person, the launcher on each PC - how a call travels, where things live, the rules and the test that breaks if one goes, measured speed and memory. Read before touching `server/` or `launcher/`, or adding an `Api` method that opens a window, a file dialog, Explorer or the clipboard. |

## Keeping it fresh
Update on every change - the mechanical rule (which code → which doc) lives in [[11-RUN-AND-MAINTAIN]]. For a large refactor, re-run the mapping workflow instead of hand-patching.

> **Note:** an older knowledge graph exists in `graphify-out/`, but its CLI is broken on this machine - ignore it and use this vault (see [[11-RUN-AND-MAINTAIN]] → graphify status).
