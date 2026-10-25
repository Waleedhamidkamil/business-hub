# 10 - Scratch, Throwaway & Leftover Files

Every non-shipping artifact in the repo, catalogued so it is **never mistaken for real application code**. The app itself is only: `app.py`, `backend/`, `static/` (minus scratch; includes `static/brands/` seed assets, `static/eshraqat_print.css` + `static/lottie/`), `templates/` (incl. `templates/eshraqat/`), `data/profiles/**` (the per-profile live DBs/settings/assets), `data/shared/library.db`, `data/profile-registry.json`, `data/profile-migration-v1.json` (**never delete** - one-shot migration marker, see [[13-PROFILES-BRANDS]]), `requirements.txt`, `smoke_test.py`, `smoke_quote.py`, `tests/`, and this `docs/` vault. Root `data/po_tool.db` + `data/settings.json` are the **preserved legacy/fallback copies** - KEEP, but they are no longer the live DB/settings. Everything below is development residue.

**Covers:** all `_*` files at the repo root, stray outputs, backups, caches, and the dead graphify graph.
**Verdicts are suggestions. Nothing here has been deleted.** See also: [[01-ARCHITECTURE]] · [[11-RUN-AND-MAINTAIN]]

> **Totals:** ~51 `_*.py` scripts · 2 `_*.js` · 32 `_*.png` screenshots · 8 scratch outputs in `data/` · plus `graphify-out/` (+ `.zip`), `jsons ani/`, `_refs/`, `__pycache__/`. Roughly **95+ throwaway items**. The PNG + `graphify-out` artifacts dominate the reclaimable space (screenshots ~1.8 MB, `graphify-out` ~2.6 MB, `graphify-out.zip` ~0.25 MB).

---

## A. Lottie / animation asset builders - `ARCHIVE or DELETE`
One-off scripts that generated the dashboard's Lottie animations (already baked into `jsons/` / `static`). Not run by the app.

| File | What it is |
|---|---|
| `_build_po_lottie.py`, `_build_qut_lottie.py`, `_build_dn_lottie.py`, `_build_inv_lottie.py` | Build per-doc-type Lottie icons |
| `_build_sign.py`, `_build_save.py`, `_build_print.py`, `_build_new.py`, `_build_import.py` | Build toolbar-action Lottie icons |
| `_build_all.py` | Orchestrates the `_build_*` scripts |
| `_lottie_probe.py`, `_render_lottie.py`, `_recolor_lordicon.py` | Inspect / render / recolor Lottie sources |

## B. UI mockups & design prototypes - `DELETE`
Throwaway visual prototyping for the dashboard/insights redesign. Superseded by the shipped UI.

`_dash_mid_mock.py`, `_insights_mock.py`, `_insights_v2.py`, `_insights_live_test.py`, `_donut_debug.py`, `_render_cards.py`, `_render_wire.py`, `_render_toolbar.py`, `_render_strips.py`, `_render_rest.py`, `_render_jsons.py`, `_make_preview.py`, `_preview_all.py`, `_preview_dn.py`, `_preview_one.py`

## C. Screenshot / crop tooling + image outputs - `DELETE`
Scripts that captured and cropped app screenshots, plus the resulting PNGs.

- **Scripts:** `_shot.py`, `_shot_html.py`, `_strip.py`
- **`_shot_*.png` (view captures, 9):** `_shot_clientsView.png`, `_shot_dataManagerView.png`, `_shot_dnView.png`, `_shot_editorView.png`, `_shot_invView.png`, `_shot_quoteHubView.png`, `_shot_quoteView.png`, `_shot_settingsView.png`
- **`_crop_*.png` (cropped regions, 12):** `_crop_bid.png`, `_crop_bidseg.png`, `_crop_bottom.png`, `_crop_grid.png`, `_crop_hero.png`, `_crop_list.png`, `_crop_stats.png`, `_crop_thead.png`, `_crop_top.png`, `_crop_topbar.png`, `_crop_totals.png`, `_crop_totals2.png`
- **`_z_*.png` (dashboard element captures, 11):** `_z_bars.png`, `_z_clients.png`, `_z_create.png`, `_z_funnel.png`, `_z_heatmap.png`, `_z_kpi.png`, `_z_profit.png`, `_z_recent.png`, `_z_stats.png`, `_z_sub.png`, `_z_top.png`

## D. Video frame extractors - `DELETE`
Pulled frames from screen recordings (see `static/lib/... vids` / `jsons/vids`).

`_vid_frame.py`, `_vid_frames.py`, `_vid_dense.py`, `_vid_extract.py`, `_vid_hires.py`

## E. Bug repros, ad-hoc tests & diagnostics - `DELETE or ARCHIVE`
Point-in-time debugging for specific fixes (PO strip/paginate, quote measure, grid paste, panel refresh). Not a maintained test suite. Keep only if you want a fix-history trail.

`_po_fix_test.py`, `_po_full_repro.py`, `_po_strip_repro.py`, `_po_date_test.py`, `_panel_refresh_test.py`, `_grid_e2e.py`, `_quote_diag.py`, `_quote_measure.py`, `_quote_render.py`, `_verify_processed.py`, `_grid_parse_test.js`, `_today_test.js`

## F. JSON / data inspection scripts - `DELETE`
Ad-hoc inspectors for the `jsons/` payloads and reference extraction.

`_inspect2.py`, `_inspect_jsons.py`, `_inspect_markers.py`, `_process_jsons.py`, `_refs_extract.py`

## G. Dead knowledge graph - `DELETE (or keep .zip as archive)`
| Path | What it is |
|---|---|
| `graphify-out/` | **Stale** (~Jun 5) graphify output - `graph.json`, `graph.html`, `GRAPH_REPORT.md`, `manifest.json`, `cost.json`, `cache/`. The graphify CLI is **broken** on this machine (orphaned uv shim). Superseded by this `docs/` vault. |
| `graphify-out.zip` | Zipped snapshot of the above. |

## H. Reference material & folders - `ARCHIVE`
| Path | What it is |
|---|---|
| `_refs/` | Reference images/assets collected during design. |
| `jsons ani/` | Animation-related JSON scratch (note the space in the name; distinct from the real `jsons/`). |

## I. Caches & backups - `DELETE cache / KEEP backup`
| Path | What it is | Verdict |
|---|---|---|
| `__pycache__/`, `**/*.pyc` | Python bytecode caches (regenerated automatically) | DELETE + gitignore |
| `data/po_tool.db.bak-20260622-182213` | Timestamped SQLite backup | KEEP (safety) or ARCHIVE |
| `data/_smoke.html`, `data/_smoke.pdf` | Output of `smoke_test.py` | DELETE (regenerated) |
| `data/_dn_no_prices.pdf`, `data/_dn_with_prices.pdf`, `data/_phaseA_po.pdf`, `data/_phaseB_eq_with_image.pdf`, `data/_q_equipment.pdf`, `data/_q_manpower.pdf` | Sample PDFs from generation experiments | DELETE (regenerated) |

## J. One-off utilities (borderline) - `ARCHIVE / your call`
Not part of the running app, but occasionally handy. Not `_`-prefixed, so easy to mistake for real code.

| File | What it is |
|---|---|
| `extract_logo_from_xlsx.py` | Pulls the company logo out of a source `.xlsx`. |
| `inspect_xlsx.py` | Inspects an `.xlsx` structure (used when building `spreadsheet_parser`). |

## K. Test/dev working directories - `KEEP (dev residue, not app code)`
| Path | What it is |
|---|---|
| `.test-work/` | Isolated per-suite fixture workspaces created by the profile/eshraqat test suites (own `data/profiles/` trees). Regenerated by tests; safe to delete, never app code. |
| `.superpowers/` | Brainstorm/plan scratch from AI-assisted development sessions (e.g. `brainstorm/eshraqat-profile-design/`). Keep for history or archive. |

## Real code - **KEEP** (listed here only to draw the line)
`app.py` · `backend/` · `static/` (excluding vendored libs noted in [[08-STYLES-TEMPLATES]]; includes `static/brands/`, `static/eshraqat_print.css` + `static/lottie/`) · `templates/` (incl. `templates/eshraqat/`) · `data/profiles/**` · `data/shared/library.db` · `data/profile-registry.json` · `data/profile-migration-v1.json` · `data/po_tool.db` + `data/settings.json` (legacy fallback copies - KEEP) · `requirements.txt` · `smoke_test.py` · `smoke_quote.py` · `tests/` · `docs/` · `AGENTS.md` · `CLAUDE.md`

> `jsons/` (the old Lottie/animation payload folder) no longer exists - icons live in `static/lottie/` now; only the scratch `jsons ani/` (note the space) remains, catalogued in section H.

---

## Suggested `.gitignore` (does not delete anything)
```gitignore
# scratch & dev residue
_*.py
_*.js
_*.png
__pycache__/
*.pyc
graphify-out/
graphify-out.zip
jsons ani/
_refs/
data/_*.pdf
data/_*.html
data/*.db.bak-*
```
> ⚠️ `_*.py` also matches `smoke`-style helpers? No - `smoke_test.py` / `smoke_quote.py` do **not** start with `_`, so they are safe. But double-check nothing you rely on is `_`-prefixed before committing this.

## Optional cleanup (review before running - NOT executed here)
```bash
# Move scratch into one archive folder instead of deleting (safest):
mkdir -p _scratch
mv _*.py _*.js _*.png "jsons ani" _refs graphify-out graphify-out.zip _scratch/ 2>/dev/null
mv data/_*.pdf data/_*.html _scratch/ 2>/dev/null
# __pycache__ regenerates, safe to delete:
find . -type d -name __pycache__ -prune -exec rm -rf {} +
```
Keeping them in `_scratch/` (rather than deleting) preserves history while un-cluttering the root and every AI's view of the project.
