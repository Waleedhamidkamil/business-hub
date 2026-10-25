# Business Hub Canva Presentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a premium dark 12-slide Business Hub presentation in Canva, use the real isolated demo screenshots, and deliver sharp LinkedIn images plus PDF and PowerPoint exports.

**Architecture:** Keep all presentation work outside the application source. A small local presentation manifest defines the approved copy, slide order, screenshot mapping, and disclosure. Canva provides the editable master design. Local validation scripts verify exported file counts, dimensions, slide counts, disclosure text, and prohibited punctuation.

**Tech Stack:** Canva connector, Canva Pro, real Business Hub PNG captures, Python validation with Pillow and pypdf, PowerPoint package inspection with zipfile, in-app browser for Canva export when the connector has no export operation.

---

## File Structure

- Create: `tmp/showcase/presentation-v2/slide-copy.json`
  - Single source of truth for titles, supporting copy, proof points, and screenshot assignments.
- Create: `tmp/showcase/presentation-v2/test_slide_copy.py`
  - Validates slide count, disclosure, punctuation, writing length, and source image availability.
- Create: `tmp/showcase/presentation-v2/qa_exports.py`
  - Validates exported PNG, PDF, and PPTX files.
- Create: `tmp/showcase/presentation-v2/build-contact-sheet.py`
  - Produces a private review sheet from exported slides.
- Create: `tmp/showcase/presentation-v2/build-notes.py`
  - Writes the final slide copy and accessible image descriptions.
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/Business-Hub-Product-Case-Study.pptx`
  - PowerPoint export from the approved Canva master.
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/Business-Hub-Product-Case-Study.pdf`
  - PDF export from the approved Canva master.
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/LinkedIn Slides/*.png`
  - Twelve high resolution LinkedIn slides.
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/QA/contact-sheet.png`
  - Private deck flow review image.
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/PRESENTATION-NOTES.md`
  - Final slide copy and accessible descriptions for LinkedIn publishing.

No Business Hub application file, live database, live setting, or customer document may change.

### Task 1: Create the presentation contract

**Files:**
- Create: `tmp/showcase/presentation-v2/test_slide_copy.py`
- Create: `tmp/showcase/presentation-v2/slide-copy.json`

- [ ] **Step 1: Write the contract test**

Create `tmp/showcase/presentation-v2/test_slide_copy.py` with this content:

```python
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "slide-copy.json"
RAW = Path(r"D:\PO TOOL\LinkedIn Showcase\01 Raw UI")


class SlideCopyContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        cls.slides = cls.data["slides"]

    def test_exact_slide_count(self):
        self.assertEqual(len(self.slides), 12)
        self.assertEqual([s["number"] for s in self.slides], list(range(1, 13)))

    def test_disclosure_is_exact(self):
        self.assertEqual(self.data["disclosure"], "DEMO DATA")
        self.assertNotIn("DEMO DATA ONLY", MANIFEST.read_text(encoding="utf-8"))

    def test_prohibited_punctuation_is_absent(self):
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn("\u2014", text)
        self.assertNotIn(";", text)

    def test_copy_stays_short(self):
        for slide in self.slides:
            self.assertLessEqual(len(slide["title"].split()), 9, slide["title"])
            self.assertLessEqual(len(slide["body"].split()), 32, slide["title"])
            self.assertLessEqual(len(slide["proofs"]), 2, slide["title"])

    def test_alt_text_is_present_and_concise(self):
        for slide in self.slides:
            words = slide["alt_text"].split()
            self.assertGreaterEqual(len(words), 12, slide["title"])
            self.assertLessEqual(len(words), 55, slide["title"])

    def test_source_images_exist(self):
        for slide in self.slides:
            for source in slide["sources"]:
                self.assertTrue((RAW / source).is_file(), source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
python tmp/showcase/presentation-v2/test_slide_copy.py
```

Expected: FAIL because `slide-copy.json` does not exist.

- [ ] **Step 3: Create the approved slide manifest**

Create `tmp/showcase/presentation-v2/slide-copy.json` with this exact content:

```json
{
  "title": "Business Hub Product Case Study",
  "audience": "Business buyers, employers, and professional partners",
  "direction": "Premium Dark Technology",
  "disclosure": "DEMO DATA",
  "slides": [
    {
      "number": 1,
      "title": "Commercial operations in one connected system",
      "body": "Business Hub keeps quotations, purchase orders, delivery notes, and invoices connected from first request to payment.",
      "proofs": ["180 demo documents", "14 demo clients"],
      "alt_text": "Business Hub dashboard presented on a deep navy slide with summary figures for 180 fictional documents and 14 fictional clients.",
      "sources": ["01-dashboard-light.png"]
    },
    {
      "number": 2,
      "title": "One workflow replaces scattered document handling",
      "body": "Commercial records, costing, follow up, and client history stay in one desktop workspace.",
      "proofs": ["QUT to PO to DN to INV", "Linked references"],
      "alt_text": "Quotation, purchase order, delivery note, and invoice pages arranged to show one connected commercial document workflow.",
      "sources": ["14-quote-pdf.png", "15-po-pdf.png", "16-dn-pdf.png", "17-inv-pdf.png"]
    },
    {
      "number": 3,
      "title": "A live view of commercial work",
      "body": "Search, document creation, attention items, and current business activity remain visible from one screen.",
      "proofs": ["18 named actions", "Current bid status"],
      "alt_text": "Dark Business Hub dashboard showing document creation shortcuts, an attention queue, and current bid status summaries.",
      "sources": ["02-dashboard-dark.png"]
    },
    {
      "number": 4,
      "title": "Margin visibility before a quote leaves",
      "body": "Each line shows sell price, remembered cost, currency, and profit. Losses appear in words and numbers.",
      "proofs": ["Known item cost", "Explicit loss label"],
      "alt_text": "Costing and Profit screen showing line revenue, remembered item cost, profit, and a visible loss label.",
      "sources": ["05-costing-profit.png"]
    },
    {
      "number": 5,
      "title": "A quotation workbench for real item detail",
      "body": "Bilingual descriptions, rates, pictures, costs, and approval state remain together while the quotation is prepared.",
      "proofs": ["Bilingual item detail", "Line profit visibility"],
      "alt_text": "Business Hub quotation editor with bilingual descriptions, quantities, rates, item pictures, saved costs, and profit details.",
      "sources": ["06-quotation-editor.png"]
    },
    {
      "number": 6,
      "title": "Document relationships remain visible",
      "body": "Linked references preserve the path between commercial documents and reduce manual history checks.",
      "proofs": ["Source quotation link", "Delivery and invoice context"],
      "alt_text": "Invoice editor showing source document references, line economics, totals, and the related commercial document context.",
      "sources": ["07-invoice-editor.png"]
    },
    {
      "number": 7,
      "title": "Outstanding work becomes an explicit queue",
      "body": "Open bid decisions, unpaid invoices, delivery work, and missing costs appear as named actions.",
      "proofs": ["Status in words", "Direct document actions"],
      "alt_text": "Business Hub To Do screen grouping open bid decisions, unpaid invoices, delivery work, and missing cost actions.",
      "sources": ["12-to-do.png"]
    },
    {
      "number": 8,
      "title": "Each client has a commercial history",
      "body": "Documents, totals, addresses, LPOs, and saved terms come together in one client record.",
      "proofs": ["Document timeline", "Saved terms and addresses"],
      "alt_text": "Client profile showing fictional commercial totals, document history, delivery addresses, received LPOs, and saved terms.",
      "sources": ["11-client-profile.png"]
    },
    {
      "number": 9,
      "title": "Master Data surfaces cleanup work",
      "body": "Bilingual records carry price, cost, unit, and picture status. Health filters expose gaps and possible duplicates.",
      "proofs": ["350 demo items", "16 flagged duplicate rows"],
      "alt_text": "Master Data catalogue and duplicate review showing 350 fictional bilingual items with health filters and grouped duplicate candidates.",
      "sources": ["08-master-data.png", "09-master-duplicate-review.png"]
    },
    {
      "number": 10,
      "title": "Client LPO acknowledgement stays controlled",
      "body": "The original stays untouched while signature and stamp placements are saved as a separate acknowledged copy.",
      "proofs": ["Original preserved", "Two placement marks"],
      "alt_text": "Client LPO viewer showing two signature and stamp placements before saving a separate acknowledged document copy.",
      "sources": ["10-lpo-signing.png"]
    },
    {
      "number": 11,
      "title": "Consistent documents from dedicated PDF engines",
      "body": "Each document keeps its required structure while sharing one recognizable output system.",
      "proofs": ["Four document engines", "A4 output"],
      "alt_text": "Four A4 document previews showing the quotation, purchase order, delivery note, and invoice output formats.",
      "sources": ["14-quote-pdf.png", "15-po-pdf.png", "16-dn-pdf.png", "17-inv-pdf.png"]
    },
    {
      "number": 12,
      "title": "Separate companies, one desktop application",
      "body": "Each company keeps separate data, numbering, documents, and brand assets inside the same application.",
      "proofs": ["Separate databases", "Visible company context"],
      "alt_text": "Business Hub company switcher showing two fictional workspaces with separate company context inside one desktop application.",
      "sources": ["13-profile-switcher.png"]
    }
  ]
}
```

- [ ] **Step 4: Run the contract test**

Run:

```powershell
python tmp/showcase/presentation-v2/test_slide_copy.py
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit the presentation contract**

The `tmp/showcase` files are intentionally ignored and should not be committed. Record completion in `AGENT-BOARD.md` only after the entire presentation task finishes.

### Task 2: Prepare the Canva brand and visual reference

**Files:**
- Read: `docs/superpowers/specs/2026-09-04-business-hub-canva-presentation-design.md`
- Read: `tmp/showcase/presentation-v2/slide-copy.json`

- [ ] **Step 1: List available Canva Brand Kits**

Call `mcp__codex_apps__canva_list_brand_kits` with:

```json
{
  "limit": 50,
  "user_intent": "Find the Brand Kit for the Business Hub product presentation."
}
```

Expected: one or more Brand Kits, or an empty list. If one kit exists, use it. If several exist, ask the user to choose. If none exist, continue with the approved premium dark colors and typography.

- [ ] **Step 2: Open the Canva presentation preparation widget**

Call `mcp__codex_apps__canva_prepare_design_generation` with `intent` set to `recommendation`, `design_type` set to `presentation`, the approved audience, the exact 12-slide outline from `slide-copy.json`, and this topic:

```text
Create a premium dark Business Hub product case study for business buyers, employers, and professional partners. Use deep navy backgrounds, cool white text, teal structure, and amber numeric accents. Keep writing concise. Use a cinematic cover and screenshot-led product proof slides. Place the exact disclosure DEMO DATA on every slide. Do not use em dashes or semicolons. Do not invent customer claims or performance results.
```

Expected: Canva opens a visual reference and outline review widget.

- [ ] **Step 3: Let the user confirm the Canva setup**

Do not add assistant text after the recommendation call. Wait for the user to confirm the visual reference and outline in the widget.

### Task 3: Upload the real Business Hub evidence

**Files:**
- Read: `D:/PO TOOL/LinkedIn Showcase/01 Raw UI/*.png`

- [ ] **Step 1: Upload every required screenshot directly from its local file**

Call `mcp__codex_apps__canva_upload_asset_from_url` once for each unique source image in `slide-copy.json`. Use `asset_file`, not `url`. Use descriptive names such as `Business Hub Dashboard Light Demo` and `Business Hub Costing Profit Demo`.

Expected: Canva returns one asset ID for each uploaded PNG.

- [ ] **Step 2: Record the private asset mapping**

Create `tmp/showcase/presentation-v2/canva-assets.json` as one JSON object. Each key must be the local filename and each value must come directly from the matching Canva upload response. Include these keys and no others:

```json
[
  "01-dashboard-light.png",
  "02-dashboard-dark.png",
  "05-costing-profit.png",
  "06-quotation-editor.png",
  "07-invoice-editor.png",
  "08-master-data.png",
  "09-master-duplicate-review.png",
  "10-lpo-signing.png",
  "11-client-profile.png",
  "12-to-do.png",
  "13-profile-switcher.png",
  "14-quote-pdf.png",
  "15-po-pdf.png",
  "16-dn-pdf.png",
  "17-inv-pdf.png"
]
```

- [ ] **Step 3: Verify every source has an asset ID**

Run a short local check that compares the unique `sources` values in `slide-copy.json` with the keys in `canva-assets.json`.

Expected: no missing or extra source names.

### Task 4: Generate and select the Canva presentation

**Files:**
- Read: `tmp/showcase/presentation-v2/slide-copy.json`
- Read: `tmp/showcase/presentation-v2/canva-assets.json`

- [ ] **Step 1: Start Canva generation from the approved widget state**

Call `mcp__codex_apps__canva_prepare_design_generation` with `intent` set to `generation`, `design_type` set to `presentation`, the selected Brand Kit ID when available, the widget request context, the approved visual reference, all uploaded screenshot asset IDs, and the exact 12-slide outline.

Expected: Canva produces presentation candidates inside the widget.

- [ ] **Step 2: Let the user select a candidate**

Ask the user to choose the candidate that best matches the approved premium dark mockup.

- [ ] **Step 3: Create the editable Canva design**

Call `mcp__codex_apps__canva_create_design_from_candidate` with the returned job ID and the selected candidate ID.

Expected: Canva returns an editable design ID and design URL.

### Task 5: Replace generated content with exact product evidence

**Files:**
- Read: `tmp/showcase/presentation-v2/slide-copy.json`
- Read: `tmp/showcase/presentation-v2/canva-assets.json`

- [ ] **Step 1: Start an editing transaction**

Call `mcp__codex_apps__canva_start_editing_transaction` with the new design ID.

Expected: Canva returns a transaction ID, page IDs, text element IDs, media fills, and a first-page thumbnail.

- [ ] **Step 2: Show the returned thumbnail**

Render the thumbnail in the conversation immediately, as required by the Canva editing workflow.

- [ ] **Step 3: Inspect pages with multiple images**

For any page that contains more than one media asset, call `mcp__codex_apps__canva_get_assets` with every asset ID on that page and show every returned thumbnail.

- [ ] **Step 4: Apply exact copy and image replacements**

Call `mcp__codex_apps__canva_perform_editing_operations` in batches. Use `find_and_replace_text` for responsive pages. Use `replace_text` for complete non-responsive text elements. Use `update_fill` for existing image frames and `insert_fill` only when a required screenshot has no frame.

Every page must receive:

1. The exact title from `slide-copy.json`.
2. The exact body copy from `slide-copy.json`.
3. No more than two proof points.
4. The exact disclosure `DEMO DATA`.
5. The assigned real screenshot assets.

- [ ] **Step 5: Review every page thumbnail**

Call `mcp__codex_apps__canva_get_design_thumbnail` once for each page not already returned by the edit operation. Show all 12 thumbnails to the user.

- [ ] **Step 6: Request approval to save**

Ask the user whether the edited Canva presentation should be saved. Do not call the commit operation before the user gives explicit approval.

- [ ] **Step 7: Commit the approved Canva edits**

Call `mcp__codex_apps__canva_commit_editing_transaction` with the transaction ID.

Expected: Canva saves the design and returns a direct design link.

### Task 6: Export the presentation

**Files:**
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/Business-Hub-Product-Case-Study.pptx`
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/Business-Hub-Product-Case-Study.pdf`
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/LinkedIn Slides/*.png`

- [ ] **Step 1: Read the in-app browser control skill**

Read the complete `browser:control-in-app-browser` skill before controlling Canva in the browser.

- [ ] **Step 2: Open the approved Canva design**

Open the direct Canva edit link in the existing signed-in browser session.

- [ ] **Step 3: Export PowerPoint and PDF**

Use Canva Download to save the full presentation as PowerPoint and PDF Print in `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/`.

- [ ] **Step 4: Export the slide images**

Download every page as PNG at the highest available quality. Save the pages in `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/LinkedIn Slides/` with zero-padded names from `01.png` through `12.png`.

- [ ] **Step 5: Confirm the exports exist**

Run:

```powershell
Get-ChildItem -LiteralPath 'D:\PO TOOL\LinkedIn Showcase\03 Presentation v2' -Recurse -File
```

Expected: one PPTX, one PDF, and twelve PNG files.

### Task 7: Validate export structure and resolution

**Files:**
- Create: `tmp/showcase/presentation-v2/qa_exports.py`
- Create: `tmp/showcase/presentation-v2/build-contact-sheet.py`
- Create: `tmp/showcase/presentation-v2/build-notes.py`
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/QA/contact-sheet.png`
- Create: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/PRESENTATION-NOTES.md`

- [ ] **Step 1: Write the export validation script**

Create `tmp/showcase/presentation-v2/qa_exports.py` with this content:

```python
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET

from PIL import Image
from pypdf import PdfReader


OUT = Path(r"D:\PO TOOL\LinkedIn Showcase\03 Presentation v2")
PPTX = OUT / "Business-Hub-Product-Case-Study.pptx"
PDF = OUT / "Business-Hub-Product-Case-Study.pdf"
PNGS = OUT / "LinkedIn Slides"
NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def pptx_text_and_slide_count():
    with ZipFile(PPTX) as archive:
        slide_names = sorted(
            name for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
        text = []
        for name in slide_names:
            root = ET.fromstring(archive.read(name))
            text.extend(node.text or "" for node in root.findall(".//a:t", NS))
    return len(slide_names), "\n".join(text)


def main():
    pngs = sorted(PNGS.glob("*.png"))
    assert len(pngs) == 12, f"expected 12 PNGs, found {len(pngs)}"
    for index, path in enumerate(pngs, start=1):
        assert path.stem == f"{index:02d}", path.name
        with Image.open(path) as image:
            assert image.width >= 3840, (path.name, image.size)
            assert image.height >= 2160, (path.name, image.size)

    pdf = PdfReader(str(PDF))
    assert len(pdf.pages) == 12, f"expected 12 PDF pages, found {len(pdf.pages)}"
    pdf_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "\u2014" not in pdf_text, "em dash found in PDF text"
    assert "DEMO DATA ONLY" not in pdf_text, "old disclosure found in PDF"
    assert pdf_text.count("DEMO DATA") >= 12, "disclosure missing from PDF pages"

    pptx_slides, pptx_text = pptx_text_and_slide_count()
    assert pptx_slides == 12, f"expected 12 PPTX slides, found {pptx_slides}"
    assert "\u2014" not in pptx_text, "em dash found in PPTX text"
    assert "DEMO DATA ONLY" not in pptx_text, "old disclosure found in PPTX"
    assert pptx_text.count("DEMO DATA") >= 12, "disclosure missing from PPTX slides"
    print("QA passed: 12 PNGs, 12 PDF pages, 12 PPTX slides")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run structural QA**

Run:

```powershell
python tmp/showcase/presentation-v2/qa_exports.py
```

Expected: `QA passed: 12 PNGs, 12 PDF pages, 12 PPTX slides`.

- [ ] **Step 3: Write the contact sheet script**

Create `tmp/showcase/presentation-v2/build-contact-sheet.py` with this content:

```python
from pathlib import Path

from PIL import Image, ImageOps


OUT = Path(r"D:\PO TOOL\LinkedIn Showcase\03 Presentation v2")
PNGS = OUT / "LinkedIn Slides"
QA = OUT / "QA"


def main():
    paths = sorted(PNGS.glob("*.png"))
    thumbs = []
    for path in paths:
        with Image.open(path) as image:
            frame = ImageOps.contain(image.convert("RGB"), (640, 360))
            thumbs.append(frame.copy())
    sheet = Image.new("RGB", (1280, 2160), "#DCE5EC")
    for index, image in enumerate(thumbs):
        x = (index % 2) * 640
        y = (index // 2) * 360
        sheet.paste(image, (x, y))
    QA.mkdir(parents=True, exist_ok=True)
    sheet.save(QA / "contact-sheet.png", quality=95)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Build the private review sheet**

Run:

```powershell
python tmp/showcase/presentation-v2/build-contact-sheet.py
```

Expected: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/QA/contact-sheet.png` exists at 1280 by 2160 pixels.

- [ ] **Step 5: Write the presentation notes builder**

Create `tmp/showcase/presentation-v2/build-notes.py` with this content:

```python
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = Path(r"D:\PO TOOL\LinkedIn Showcase\03 Presentation v2")


def main():
    data = json.loads((ROOT / "slide-copy.json").read_text(encoding="utf-8"))
    lines = ["# Business Hub Presentation Notes", ""]
    for slide in data["slides"]:
        lines.extend([
            f"## {slide['number']:02d}. {slide['title']}",
            "",
            slide["body"],
            "",
            f"**Alt text:** {slide['alt_text']}",
            "",
        ])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "PRESENTATION-NOTES.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Build the presentation notes**

Run:

```powershell
python tmp/showcase/presentation-v2/build-notes.py
```

Expected: `D:/PO TOOL/LinkedIn Showcase/03 Presentation v2/PRESENTATION-NOTES.md` contains 12 slide headings and 12 accessible descriptions.

- [ ] **Step 7: Inspect every slide individually**

Open each PNG at full size and confirm:

1. Product details remain readable.
2. No screenshot looks blurred or stretched.
3. No text overlaps or clips.
4. The deck uses consistent alignment and spacing.
5. Status remains understandable without color.
6. Every slide displays `DEMO DATA`.

- [ ] **Step 8: Deliver the Canva design and local exports**

Provide the direct Canva edit link and local links to the PPTX, PDF, PNG directory, and presentation notes. Mention only limitations that affect use.

### Task 8: Close project coordination cleanly

**Files:**
- Modify: `AGENT-BOARD.md`

- [ ] **Step 1: Run final status checks**

Run:

```powershell
git status --short
git diff --check -- AGENT-BOARD.md
```

Expected: unrelated existing changes remain untouched. Presentation work appears only in the committed design and plan documents plus ignored showcase output folders.

- [ ] **Step 2: Remove the active board row**

Delete the Codex row for the Business Hub Canva presentation from `AGENT-BOARD.md`.

- [ ] **Step 3: Confirm no presentation task file remains staged**

Run:

```powershell
git status --short
```

Expected: no presentation task file remains staged. Existing unrelated worktree changes remain untouched.
