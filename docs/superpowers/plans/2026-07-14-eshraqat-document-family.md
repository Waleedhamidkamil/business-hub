# Eshraqat Document Family Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render all five Eshraqat document types with the exact supplied identity and one coherent Word-reference design system while preserving every existing document-specific behavior and pagination safeguard.

**Architecture:** Generators keep their current schemas, row measurement, pagination, signing, and save workflows. When the active profile is Eshraqat they select templates under `templates/eshraqat/`, which share one macro/component file and one print stylesheet. Profile assets are embedded through a common helper so no Eshraqat output can fall back to Abraj branding.

**Tech Stack:** Python/Jinja2 generators, HTML/CSS A4 templates, headless Chrome/Edge PDF engine, Poppler inspection tools, standalone Python/Node regression scripts.

---

## File Structure

- Create `static/brands/eshraqat/logo.png`: byte-for-byte copy of the supplied logo.
- Create `static/brands/eshraqat/iso-certifications.png`: exact ISO strip embedded in the Word reference.
- Create `backend/brand_assets.py`: profile asset loading and required/optional data URIs.
- Create `backend/eshraqat_render.py`: shared Eshraqat context, reference identity, metadata helpers.
- Create `static/eshraqat_print.css`: shared A4 visual system.
- Create `templates/eshraqat/_components.html`: header, metadata, table shell, totals, terms/signing, footer, page-number macros.
- Create five Eshraqat templates: `po_page.html`, `quote_page.html`, `dn_page.html`, `inv_page.html`, `ltr_page.html`.
- Modify five generators to select Eshraqat templates/context without changing Abraj rendering.
- Modify settings UI/API only where profile-aware logo/identity fields need display changes.
- Add rendering, pagination, asset, and cross-brand regression tests.

The workspace currently has no Git metadata. Run commit steps only if `.git` is restored.

### Task 1: Package Exact Reference Assets and Preset

**Files:**
- Create: `static/brands/eshraqat/logo.png`
- Create: `static/brands/eshraqat/iso-certifications.png`
- Create: `backend/brand_assets.py`
- Modify: `backend/profile_manager.py`
- Modify: `backend/config.py`
- Test: `tests/eshraqat_assets_backend.test.py`

- [ ] **Step 1: Write failing exact-asset tests**

Assert the packaged logo bytes equal `D:\ESHRAQATZ\11.PNGZ\logo with manpower.png`. Open the DOCX as ZIP and assert the packaged ISO strip equals `word/media/image2.png`. Activate Eshraqat and assert `brand_assets.logo_data_uri(required=True)` and `iso_data_uri()` contain PNG data URIs. Activate Abraj and prove neither helper reads Eshraqat assets.

- [ ] **Step 2: Run the test red**

Run: `python tests/eshraqat_assets_backend.test.py`

Expected: FAIL because packaged assets/helper do not exist.

- [ ] **Step 3: Copy/extract assets without modifying sources**

Use `Copy-Item -LiteralPath` for the logo. Extract only `word/media/image2.png` from the supplied DOCX with `zipfile.ZipFile`; write it to `static/brands/eshraqat/iso-certifications.png`. Compare SHA-256 before accepting each destination.

Implement:

```python
import base64
import mimetypes

def image_data_uri(path: Path, required: bool = False) -> str | None:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required profile asset is missing: {path.name}")
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"

def logo_data_uri(required: bool = False) -> str | None:
    return image_data_uri(config.ASSETS_DIR / "logo.png", required)
def iso_data_uri() -> str | None:
    return image_data_uri(config.ASSETS_DIR / "iso-certifications.png")
```

`ProfileManager.bootstrap_profiles()` copies packaged Eshraqat seed assets into the Eshraqat profile only when those profile files are absent.

- [ ] **Step 4: Add the Eshraqat preset**

Set the profile-local initial values:

```python
ESHRAQAT_IDENTITY = {
    "name_lines": [
        "Eshraqat Albadr",
        "for Contracting, General Services,",
        "Heavy Equipment Rental, and Employment of Iraqi Labor",
    ],
    "website": "www.eshraqatalbadr.com",
    "emails": ["info@eshraqatalbadr.com", "eshraqat.albadr@gmail.com"],
    "brand_color": "#861432",
}
```

Cash is the only initial invoice payment option. Signature/stamp labels and files remain empty.

- [ ] **Step 5: Run asset/config tests**

Run:

```powershell
python tests/eshraqat_assets_backend.test.py
python tests/profile_config_assets_backend.test.py
```

Expected: both pass.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: package Eshraqat reference assets`.

### Task 2: Shared Eshraqat Print Components

**Files:**
- Create: `backend/eshraqat_render.py`
- Create: `static/eshraqat_print.css`
- Create: `templates/eshraqat/_components.html`
- Test: `tests/eshraqat_shared_template.test.py`

- [ ] **Step 1: Write a failing shared-context/template test**

Render a minimal macro harness and assert it contains the exact logo URI, all company lines, burgundy brand variable, website, both emails, ISO URI, document title, metadata labels, and page number. Assert no `ABRAJ` text appears.

- [ ] **Step 2: Run the test red**

Run: `python tests/eshraqat_shared_template.test.py`

Expected: FAIL because shared renderer/components are missing.

- [ ] **Step 3: Implement one shared context**

Provide:

```python
def common_context(*, title: str, doc_no: str, pages: int) -> dict:
    return {
        "eshraqat": True,
        "identity": config.ESHRAQAT_IDENTITY,
        "brand": config.PROFILE_ACCENT,
        "logo_data_uri": brand_assets.logo_data_uri(required=True),
        "iso_data_uri": brand_assets.iso_data_uri(),
        "document_title": title,
        "document_no": doc_no,
        "page_count": pages,
    }
```

Do not put document-specific totals/terms/fields here.

- [ ] **Step 4: Implement macros and CSS**

`_components.html` exports macros for `header`, `meta_grid`, `table_header`, `amount_totals`, `terms_signing`, `footer`, and `page_number`. `eshraqat_print.css` defines A4 margins, exact logo containment, burgundy table headers, compact metadata, bordered rows, footer/ISO sizing, RTL helpers, repeated headers, non-splitting rows, and fixed signature/footer zones.

Use stable millimeter dimensions matching the reference DOCX page setup (A4; approximately 8.75mm left/right, 8.8mm top, 15.9mm bottom) but tune final reserves to the app's PDF constitution measurements.

- [ ] **Step 5: Run shared template test**

Run: `python tests/eshraqat_shared_template.test.py`

Expected: pass with no Abraj identity.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: add shared Eshraqat print system`.

### Task 3: Eshraqat Quotation Reference Match

**Files:**
- Create: `templates/eshraqat/quote_page.html`
- Modify: `backend/quote_generator.py`
- Test: `tests/eshraqat_quote_rendering.test.py`

- [ ] **Step 1: Write failing quotation rendering tests**

Activate Eshraqat and render materials/equipment/manpower/service quotations. Assert each uses `templates/eshraqat/quote_page.html`, the shared identity/footer, the saved schema columns, RFQ/client/attention/address metadata, amount-in-words, totals when enabled, terms, and authorized signatory. Activate Abraj and assert its HTML remains byte-marker-compatible with the current template.

Add page-role cases for one row, one-page boundary, two pages, and 100 rows. Assert page objects retain current `first/continuation/last` roles and only the last page carries totals/terms/signing/footer.

- [ ] **Step 2: Run the tests red**

Run: `python tests/eshraqat_quote_rendering.test.py`

Expected: FAIL because the Eshraqat quote template is absent.

- [ ] **Step 3: Select template/context without touching pagination**

In `render_quote_html`, keep `_schema_for`, `_schema_for_document`, `_columns`, `_quote_autofit`, `build_layout`, money/amount-in-words, and signing context unchanged. Select:

```python
template_name = "eshraqat/quote_page.html" if config.ACTIVE_PROFILE == "eshraqat" else "quote_page.html"
```

Merge `eshraqat_render.common_context(title=schema["label"], doc_no=quote.get("quote_no", ""), pages=len(pages))` only for Eshraqat.

- [ ] **Step 4: Build the quotation template**

Match the Word reference structure: logo/company/title; left document metadata and right client metadata; schema-driven table including optional image column; amount-in-words/currency and subtotal/withholding/grand-total area; terms and authorized signatory; website/email/ISO footer. Continue using each page's measured row height and current row/totals data.

- [ ] **Step 5: Run quotation tests and smoke**

Run:

```powershell
python tests/eshraqat_quote_rendering.test.py
python smoke_quote.py
```

Expected: all four types render; Abraj smoke PDFs remain unchanged in identity.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: render Eshraqat quotations`.

### Task 4: Eshraqat Invoice

**Files:**
- Create: `templates/eshraqat/inv_page.html`
- Modify: `backend/inv_generator.py`
- Test: `tests/eshraqat_invoice_rendering.test.py`

- [ ] **Step 1: Write failing invoice cases**

Cover all four invoice subtypes, Cash/no-bank and later custom-bank context, withholding included/excluded, linked quote/LPO metadata, paid status remaining database-only, custom columns/widths, Auto-fit true/false, one/two/long-page layouts, and signing overlay. Assert no Abraj bank/company text can appear.

- [ ] **Step 2: Run red**

Run: `python tests/eshraqat_invoice_rendering.test.py`

Expected: missing template/identity assertions fail.

- [ ] **Step 3: Add Eshraqat template selection**

Preserve `_schema_for_document`, `build_layout`, withholding/totals calculations, bank selection, payment context, signing context, and last-page no-stretch behavior. Add only template/common-context selection.

- [ ] **Step 4: Build invoice-specific blocks**

Use the shared shell with Invoice No/Date/LPO/Client/Address metadata, schema table, withholding and grand total, optional payment-info block, authorized signatory, and shared footer. Reserve payment/signing space in the existing `INV_PAYMENT_MM` calculation; tune only after measured rendering proves a mismatch.

- [ ] **Step 5: Run invoice and paid-state tests**

Run:

```powershell
python tests/eshraqat_invoice_rendering.test.py
python tests/invoice_paid_backend.test.py
node tests/invoice_paid_panel.test.js
```

Expected: pass.

- [ ] **Step 6: Checkpoint**

If Git is available, commit with `feat: render Eshraqat invoices`.

### Task 5: Eshraqat Delivery Note

**Files:**
- Create: `templates/eshraqat/dn_page.html`
- Modify: `backend/dn_generator.py`
- Test: `tests/eshraqat_dn_rendering.test.py`

- [ ] **Step 1: Write failing DN cases**

Cover prices hidden/shown, linked quotation/LPO, custom base columns and widths, Auto-fit, recipient/supplier signature slots, one/two/long pages, and no Abraj identity.

- [ ] **Step 2: Run red**

Run: `python tests/eshraqat_dn_rendering.test.py`

Expected: fail before the Eshraqat DN template exists.

- [ ] **Step 3: Add template/context selection and DN blocks**

Preserve `_columns`, `build_layout`, include-prices totals, signing slot rules, and page capacities. Use the shared shell with DN No/Date/LPO/Client/Address metadata, optional rate/total columns, recipient/supplier authorization area, and shared footer.

- [ ] **Step 4: Run DN regressions**

Run:

```powershell
python tests/eshraqat_dn_rendering.test.py
node tests/editor_meta_compact_layout.test.js
python tests/doc_autofit_layout_backend.test.py
```

Expected: pass.

- [ ] **Step 5: Checkpoint**

If Git is available, commit with `feat: render Eshraqat delivery notes`.

### Task 6: Eshraqat Purchase Order

**Files:**
- Create: `templates/eshraqat/po_page.html`
- Modify: `backend/pdf_generator.py`
- Test: `tests/eshraqat_po_rendering.test.py`

- [ ] **Step 1: Write failing PO cases**

Cover one PO, row-budget split into multiple sequential Eshraqat POs, source quotation reference, current PO row fields, saved column widths, Auto-fit, signing overlay, and no Abraj Arabic/company identity. Assert split behavior remains document splitting rather than quote-style continuation pages.

- [ ] **Step 2: Run red**

Run: `python tests/eshraqat_po_rendering.test.py`

Expected: fail before template selection exists.

- [ ] **Step 3: Add Eshraqat PO template selection**

Keep `estimate_row_height_mm`, `split_into_pos`, `page_po_numbers`, table budget, max item cap, and signing context intact. Use the shared shell with English Eshraqat labels, PO No/Date/Source Quotation metadata, current supplier/item data, authorization area, and shared footer.

- [ ] **Step 4: Run PO regressions**

Run:

```powershell
python tests/eshraqat_po_rendering.test.py
python smoke_test.py
python tests/po_submitted_quote_link_backend.test.py
node tests/po_quote_link_panel.test.js
```

Expected: pass.

- [ ] **Step 5: Checkpoint**

If Git is available, commit with `feat: render Eshraqat purchase orders`.

### Task 7: Eshraqat Official Letters

**Files:**
- Create: `templates/eshraqat/ltr_page.html`
- Modify: `backend/ltr_generator.py`
- Test: `tests/eshraqat_letter_rendering.test.py`

- [ ] **Step 1: Write failing EN/AR letter tests**

Cover English and Arabic direction, subject/body/notes, optional table, one/two signatures, long browser-flow pagination, page footer, exact Eshraqat identity, and no Abraj fallback. Arabic content must remain RTL inside the same header/footer system.

- [ ] **Step 2: Run red**

Run: `python tests/eshraqat_letter_rendering.test.py`

Expected: fail before the Eshraqat letter template exists.

- [ ] **Step 3: Add template/context selection and letter layout**

Preserve current language schemas, signatures, table data, body formatting, and browser pagination. Use the shared shell; mirror only the content flow for Arabic, not the exact logo artwork or footer asset.

- [ ] **Step 4: Run letter and editor reset tests**

Run:

```powershell
python tests/eshraqat_letter_rendering.test.py
node tests/editor_new_reset.test.js
node tests/editor_toolbar_layout.test.js
```

Expected: pass.

- [ ] **Step 5: Checkpoint**

If Git is available, commit with `feat: render Eshraqat official letters`.

### Task 8: Full PDF Geometry, Workflow, and Documentation Gate

**Files:**
- Create: `tests/eshraqat_pdf_layout_backend.test.py`
- Create: `tests/eshraqat_profile_workflows.test.py`
- Modify: `docs/01-ARCHITECTURE.md`
- Modify: `docs/02-BACKEND-CORE.md`
- Modify: `docs/03-BACKEND-DATA.md`
- Modify: `docs/04-BACKEND-DOCGEN.md`
- Modify: `docs/05-FRONTEND-SHELL.md`
- Modify: `docs/06-FRONTEND-EDITORS.md`
- Modify: `docs/08-STYLES-TEMPLATES.md`
- Modify: `docs/09-DATA-MODEL.md`
- Modify: `docs/11-RUN-AND-MAINTAIN.md`
- Modify: `docs/12-PDF-LAYOUT-CONSTITUTION.md` only if measured Eshraqat constants add a documented profile variant.

- [ ] **Step 1: Add the rendering matrix test**

Generate every Eshraqat document type with one row, one-page boundary, two pages, and 40-100 rows where supported. Include long text, optional images, custom columns/widths, Auto-fit on/off, and signing overlays. Render PDFs, inspect page counts, and rasterize with `pdftoppm` for visual review.

Assert geometry markers keep the final data row above the totals/terms/signature/footer reserve and middle pages use at least 90% of their intended body area. Never weaken an existing quote/DN/invoice constitution constant without a measured before/after artifact.

- [ ] **Step 2: Add end-to-end profile workflows**

For each Eshraqat editor exercise New, Save, load, edit, delete, Preview, Export, Print, Sign where supported, Submit, Paid, Cost, quote link, Import, Make Invoice, and Show Prices as applicable. Switch to Abraj between operations and assert no Eshraqat document/status/asset appears.

- [ ] **Step 3: Run complete verification**

Run all tests listed in `docs/11-RUN-AND-MAINTAIN.md`, all new profile/Eshraqat tests, syntax checks, then:

```powershell
python smoke_test.py
python smoke_quote.py
```

Expected: zero failures. Visually compare generated Eshraqat quotation screenshots with the supplied Word reference for logo scale, company/title hierarchy, metadata proportions, table treatment, totals, terms/signing gap, and footer/ISO placement.

- [ ] **Step 4: Update the map**

Document every new file/API/table/storage path/template, the active-profile data flow, shared-library boundary, permanent counters, Eshraqat page roles/constants, new tests, and all impact-map consequences.

- [ ] **Step 5: Final checkpoint**

If Git is available, commit with `feat: complete Eshraqat document family`.
