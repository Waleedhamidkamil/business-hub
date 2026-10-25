# Business Hub Cinematic Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a complete 14-slide Business Hub cinematic showcase with uncropped product screenshots, verified static files, an animated PowerPoint edition, and separate static and animated Canva designs.

**Architecture:** Keep all authoring code and validation files inside `tmp/showcase/presentation-v3`. Use one JSON content manifest, one Artifact Tool presentation builder, and one PowerPoint animation script. Write delivery files to a new `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase` directory so the previous presentation remains unchanged.

**Tech Stack:** JavaScript ES modules, `@oai/artifact-tool`, Microsoft PowerPoint COM automation, Canva connector, Canva editor browser controls, Python Pillow and pypdf for verification.

---

## File Structure

- `tmp/showcase/presentation-v3/slide-copy.json`: final 14-slide titles, supporting copy, evidence labels, alt text, and source images.
- `tmp/showcase/presentation-v3/test_slide_copy.py`: content contract tests.
- `tmp/showcase/presentation-v3/build/build_deck.mjs`: Artifact Tool authoring, PNG previews, PPTX export, and finalization.
- `tmp/showcase/presentation-v3/build/add_animations.ps1`: PowerPoint transition and entrance animation pass.
- `tmp/showcase/presentation-v3/output/`: validated local output used by the finalizer.
- `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\Business-Hub-Cinematic-Showcase.pptx`: static PowerPoint.
- `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\Business-Hub-Cinematic-Showcase-Animated.pptx`: animated PowerPoint.
- `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\Business-Hub-Cinematic-Showcase.pdf`: PDF.
- `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\4K Slides\`: fourteen 3840 by 2160 PNG files.
- `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\Business-Hub-Cinematic-Showcase-Notes.md`: publishing copy and slide guide.

### Task 1: Build the content contract

**Files:**
- Create: `tmp/showcase/presentation-v3/slide-copy.json`
- Create: `tmp/showcase/presentation-v3/test_slide_copy.py`

- [ ] **Step 1: Create the 14-slide manifest**

Store `number`, `title`, `body`, `proofs`, `alt_text`, and `sources` for every page. Use the exact titles from the design spec and use the original files under `D:\PO TOOL\LinkedIn Showcase\01 Raw UI`.

- [ ] **Step 2: Write the contract test**

```python
def test_manifest_contract():
    assert len(slides) == 14
    assert [slide["number"] for slide in slides] == list(range(1, 15))
    assert all(slide["title"].strip() for slide in slides)
    assert all(slide["alt_text"].strip() for slide in slides)
    assert all("\\u2014" not in json.dumps(slide, ensure_ascii=True) for slide in slides)
```

- [ ] **Step 3: Run the test**

Run: `python tmp/showcase/presentation-v3/test_slide_copy.py`

Expected: all content tests pass.

### Task 2: Build the static presentation

**Files:**
- Create: `tmp/showcase/presentation-v3/build/build_deck.mjs`

- [ ] **Step 1: Mark the artifact operation**

Run exactly once from the presentation skill directory:

```powershell
node container_tools/mark_artifact_operation_started.mjs --operation-kind create --expected-output-count 2 --output-format pptx
```

- [ ] **Step 2: Implement contain fitting**

Use the entire 16:10 screenshot inside the presentation image frame:

```javascript
const scale = Math.min(frame.width / image.width, frame.height / image.height);
const width = image.width * scale;
const height = image.height * scale;
const left = frame.left + (frame.width - width) / 2;
const top = frame.top + (frame.height - height) / 2;
```

Do not pass crop options for any primary screenshot.

- [ ] **Step 3: Implement the cinematic slide system**

Use a 1280 by 720 canvas. Reserve a 240 pixel editorial rail and a 980 by 613 screenshot frame. Keep the full screenshot visible. Alternate rail placement for visual rhythm while preserving one consistent grid.

- [ ] **Step 4: Implement the document gallery**

Fit all four A4 pages completely inside equal columns. Do not crop any page.

- [ ] **Step 5: Add notes and disclosure**

Add screenshot descriptions to speaker notes. Add editable `DEMO DATA` text at the top-right of every slide.

- [ ] **Step 6: Export previews and candidate PPTX**

Export each page at scale 3 to `4K Slides`. Export the candidate PPTX inside the private build directory.

- [ ] **Step 7: Finalize the static PPTX**

Use `finalizePresentation` with:

```javascript
{
  explicitTotalSlideCount: 14,
  requiredNativeTableOwnerSlides: [],
  requiredNativeChartOwnerSlides: [],
  fontPolicy: { basis: "design", families: ["Bahnschrift"] },
  verifyArtifactToolImport: true
}
```

Expected: zero package integrity findings, zero layout findings, and successful first-party import.

### Task 3: Inspect and repair the static deck

**Files:**
- Modify: `tmp/showcase/presentation-v3/build/build_deck.mjs`

- [ ] **Step 1: Render the final PPTX through Microsoft PowerPoint**

Open the presentation read-only and export all 14 slides as PNG at 1920 by 1080.

- [ ] **Step 2: Inspect every slide individually**

Check screenshot completeness, title fit, slide numbers, document page proportions, contrast, and repeated spacing.

- [ ] **Step 3: Repair the builder and rebuild if needed**

Every visual repair must be made in `build_deck.mjs`, followed by a new validated output filename.

### Task 4: Create the animated PowerPoint edition

**Files:**
- Create: `tmp/showcase/presentation-v3/build/add_animations.ps1`

- [ ] **Step 1: Copy the validated static deck**

Create `Business-Hub-Cinematic-Showcase-Animated.pptx` without overwriting the static master.

- [ ] **Step 2: Apply slide transitions**

Use PowerPoint COM to set a consistent fade transition and disable automatic advance:

```powershell
foreach ($slide in $deck.Slides) {
    $slide.SlideShowTransition.EntryEffect = 3849
    $slide.SlideShowTransition.AdvanceOnTime = 0
    $slide.SlideShowTransition.AdvanceOnClick = -1
}
```

- [ ] **Step 3: Apply restrained entrances**

Add a fade entrance to the title rail and a second fade entrance to the screenshot. Use after-previous timing and short durations.

- [ ] **Step 4: Save and reopen the animated file**

Confirm 14 slides and at least one timeline effect on every slide.

### Task 5: Export PDF, PNG, and notes

**Files:**
- Create: `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\Business-Hub-Cinematic-Showcase.pdf`
- Create: `D:\PO TOOL\LinkedIn Showcase\04 Cinematic Showcase\Business-Hub-Cinematic-Showcase-Notes.md`

- [ ] **Step 1: Export the static deck to PDF**

Use Microsoft PowerPoint format 32.

- [ ] **Step 2: Verify the 4K exports**

Confirm fourteen PNG files and one unique dimension set of 3840 by 2160.

- [ ] **Step 3: Write publishing notes**

Include the product positioning, a concise LinkedIn carousel post, the 14-slide guide, and a reminder to retain `DEMO DATA`.

### Task 6: Create two Canva presentations

**Files:**
- Import the static PPTX through the Canva connector.
- Create a separate Canva copy for animation.

- [ ] **Step 1: Import the static presentation**

Use `canva_import_design_from_url` with `design_file` set to the generated static PPTX and `intended_design_type` set to `presentation`.

- [ ] **Step 2: Verify the static Canva design**

Confirm title, presentation type, and exactly 14 pages.

- [ ] **Step 3: Copy the static Canva design**

Use `canva_copy_design` to create the animated edition and rename it `Business Hub Cinematic Showcase Animated`.

- [ ] **Step 4: Apply Canva animation in the editor**

Open the animated design in the signed-in Canva editor. Use the page animation controls to apply a subtle fade or dissolve treatment to all pages. Avoid novelty effects.

- [ ] **Step 5: Verify the animated Canva design**

Confirm the separate design exists, contains 14 pages, and retains the full screenshots.

### Task 7: Final verification and handoff

**Files:**
- Inspect all final delivery files.

- [ ] **Step 1: Run content checks**

Confirm `DEMO DATA` on all 14 slides and confirm no em dash exists in the manifest or notes.

- [ ] **Step 2: Run file checks**

Confirm two PPTX files, one PDF, fourteen 4K PNG files, and one notes file.

- [ ] **Step 3: Record hashes and page counts**

Calculate SHA256 for both PPTX files and the PDF. Confirm 14 PDF pages.

- [ ] **Step 4: Remove the active Agent Board row**

Delete only the Codex row added for this presentation work.

- [ ] **Step 5: Deliver direct links**

Provide the static Canva edit link, animated Canva edit link, PowerPoint files, PDF, PNG directory, and notes file.
