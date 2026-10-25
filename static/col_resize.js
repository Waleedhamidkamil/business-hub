/* =========================================================
   Excel-style column resizing for the document editor tables.
   ---------------------------------------------------------
   Self-contained. No dependencies on the editor scripts.

   Design - the most stable, layout-preserving approach for a
   `table-layout: fixed; width: 100%` table whose wrapper is
   `overflow: hidden`:

     • A thin grab-handle sits on the right border of each data
       column header (cursor: col-resize on hover).
     • Click-and-drag moves that border in real time. The grabbed
       column tracks the cursor; its immediate right neighbour
       absorbs the exact delta, so the table stays exactly 100%
       wide - it never overflows and no other column shifts.
     • A minimum width clamps both sides so a column can't collapse.

   The fitting function is untouched: it still renders the <th>
   widths from the schema. We only adjust two <th> widths live while
   the user drags. When the header is rebuilt (e.g. switching type),
   a MutationObserver re-attaches the handles and the fitted widths
   resume - nothing else in the editor changes.
   ========================================================= */
(function () {
    "use strict";

    // Smallest a column may shrink to (px). Prevents collapsing.
    const MIN_COL_PX = 44;
    // Hit area of the grab handle (px), centred on the column border.
    const HANDLE_W = 8;

    // Editor tables with per-column widths: the schema-driven LTR tables
    // (QUT/INV/DN) and the RTL Purchase-Order table (#editorTable).
    const TABLE_SELECTOR =
        ".q-edit-table, .inv-edit-table, .dn-edit-table, #editorTable";
    const HANDLE_CLASS = "col-resizer";

    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

    // --- one drag gesture --------------------------------------------------
    // `rtl` flips the cursor->delta mapping: in a right-to-left table the DOM
    // "right" neighbour renders on the left, so a rightward drag must shrink
    // the grabbed (DOM-left) column instead of growing it.
    function startDrag(ev, leftTh, rightTh, table, rtl) {
        ev.preventDefault();
        ev.stopPropagation();

        const startX  = ev.clientX;
        const tableW  = table.getBoundingClientRect().width;
        const leftW0  = leftTh.getBoundingClientRect().width;
        const rightW0 = rightTh.getBoundingClientRect().width;
        if (!tableW) return;

        // The delta is bounded so neither column drops below MIN_COL_PX.
        const minD = MIN_COL_PX - leftW0;     // most we can shrink the left col
        const maxD = rightW0 - MIN_COL_PX;    // most we can grow the left col

        document.body.classList.add("col-resizing");

        function onMove(e) {
            const raw = rtl ? (startX - e.clientX) : (e.clientX - startX);
            const d = clamp(raw, minD, maxD);
            // Write back as % of table width so the columns stay responsive
            // and the table total stays constant (only this pair changes).
            leftTh.style.width  = ((leftW0  + d) / tableW * 100) + "%";
            rightTh.style.width = ((rightW0 - d) / tableW * 100) + "%";
        }
        function onUp() {
            document.removeEventListener("mousemove", onMove);
            document.removeEventListener("mouseup", onUp);
            document.body.classList.remove("col-resizing");
        }
        document.addEventListener("mousemove", onMove);
        document.addEventListener("mouseup", onUp);
    }

    // --- (re)attach handles to one table's header --------------------------
    function attachHandles(table) {
        const headRow = table.tHead && table.tHead.rows[0];
        if (!headRow) return;
        const ths = Array.from(headRow.cells);
        const rtl = getComputedStyle(table).direction === "rtl";

        // Seed any data column that's missing an inline width from its rendered
        // width, so a fixed-layout table stays stable while dragging one pair
        // (only runs when the table is visible - rendered widths are 0 when
        // hidden). No-op for columns that already carry a width.
        const seedW = table.getBoundingClientRect().width;
        if (seedW > 0) {
            ths.forEach((th) => {
                if (th.hasAttribute("data-col-key") && !th.style.width) {
                    const w = th.getBoundingClientRect().width;
                    if (w > 0) th.style.width = ((w / seedW) * 100).toFixed(2) + "%";
                }
            });
        }

        for (let i = 0; i < ths.length - 1; i++) {
            const leftTh  = ths[i];
            const rightTh = ths[i + 1];
            // Only between two data columns. Data columns carry a data-col-key;
            // the NO./index and delete columns don't, so they're skipped. We key
            // off data-col-key (NOT inline width) so resizing still works when a
            // column's width wasn't pre-seeded from the schema - e.g. a DN column
            // override that dropped the width. The drag math reads live pixel
            // widths, so a missing inline width is fine.
            if (!leftTh.hasAttribute("data-col-key") ||
                !rightTh.hasAttribute("data-col-key")) continue;
            if (leftTh.querySelector("." + HANDLE_CLASS)) continue; // already has one

            const handle = document.createElement("div");
            handle.className = HANDLE_CLASS;
            handle.style.width = HANDLE_W + "px";
            // Sit the handle on the shared border: physical right in LTR,
            // physical left in RTL (where the DOM-next column is to the left).
            if (rtl) handle.style.left  = -(HANDLE_W / 2) + "px";
            else     handle.style.right = -(HANDLE_W / 2) + "px";
            handle.addEventListener("mousedown", (e) =>
                startDrag(e, leftTh, rightTh, table, rtl));
            // Don't let a stray click bubble into header/sort handlers.
            handle.addEventListener("click", (e) => e.stopPropagation());
            leftTh.appendChild(handle);
        }
    }

    // --- wire up every editor table + watch for header rebuilds ------------
    function init() {
        document.querySelectorAll(TABLE_SELECTOR).forEach((table) => {
            if (!table.tHead) return;
            attachHandles(table);
            // The editor rebuilds the <thead> when the column schema changes;
            // re-attach handles whenever that happens.
            const mo = new MutationObserver(() => attachHandles(table));
            mo.observe(table.tHead, { childList: true });
        });
    }

    // Synchronous re-attach - editors can call this right after rebuilding a
    // <thead> instead of relying on the (async) MutationObserver to fire.
    window.refreshColResizers = function () {
        document.querySelectorAll(TABLE_SELECTOR).forEach((table) => {
            if (table.tHead) attachHandles(table);
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // --- read the live column widths so generate/preview match the editor ---
    // Returns { columnKey: widthPercent } for every data column, reflecting any
    // manual resize. Empty when the header isn't built yet. The backend treats
    // these as the explicit per-column widths and rebalances them just like the
    // fitting function does - so the PDF mirrors exactly what's on screen.
    window.readEditorColWidths = function (thead) {
        const out = {};
        if (!thead) return out;
        const cells = thead.querySelectorAll("th[data-col-key]");
        if (!cells.length) return out;
        const table = thead.closest("table");
        const tableW = table ? table.getBoundingClientRect().width : 0;
        cells.forEach((th) => {
            const k = th.getAttribute("data-col-key");
            let w = parseFloat(th.style.width);          // inline % (fit + resize)
            if (!(w > 0) && tableW) {                      // fallback: rendered %
                w = (th.getBoundingClientRect().width / tableW) * 100;
            }
            if (k && w > 0) out[k] = Math.round(w * 100) / 100;
        });
        return out;
    };

    // --- content-aware "Fit columns to content" ----------------------------
    // Measures the rendered text in each flexible data column and redistributes
    // the columns' COMBINED width by content demand, so long-text columns get
    // proportionally more room and short ones (Qty/Unit) shrink to what they
    // need. Fixed columns are left untouched, so the table stays exactly 100%:
    //   • NO./index + delete columns  - no data-col-key (already skipped)
    //   • image columns               - driven by the thumbnail, not text
    //   • the synthetic COST column    - never reaches the PDF
    // Writes the SAME inline `%` a manual drag would, so the existing
    // readEditorColWidths → col_widths → backend rebalance pipeline makes the
    // PDF mirror the on-screen table with no other change. Returns true on
    // success, false when it can't run (table hidden / <2 flex columns).
    let _measureCanvas = null;
    function _measureWidest(text, font) {
        if (!_measureCanvas) _measureCanvas = document.createElement("canvas");
        const ctx = _measureCanvas.getContext("2d");
        ctx.font = font;
        // The wrap engine only needs room for the LONGEST line, so split on the
        // user's explicit newlines and take the widest segment.
        let widest = 0;
        const s = (text == null ? "" : String(text));
        const lines = s.split("\n");
        for (let i = 0; i < lines.length; i++) {
            const w = ctx.measureText(lines[i]).width;
            if (w > widest) widest = w;
        }
        return widest;
    }
    function _fontOf(el) {
        const s = getComputedStyle(el);
        return `${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
    }
    function _resolveTable(arg) {
        let el = (typeof arg === "string") ? document.querySelector(arg) : arg;
        if (!el) return null;
        if (el.tagName === "TABLE") return el;
        if (el.tagName === "THEAD") return el.closest("table");
        return el.querySelector ? el.querySelector("table") : null;
    }
    function _cssEsc(k) {
        return (window.CSS && CSS.escape) ? CSS.escape(k) : k;
    }

    window.fitEditorColumns = function (arg, opts) {
        const table = _resolveTable(arg);
        if (!table || !table.tHead) return false;
        const headRow = table.tHead.rows[0];
        if (!headRow) return false;
        const tableW = table.getBoundingClientRect().width;
        if (!(tableW > 0)) return false;               // hidden - can't measure

        const tbody = table.tBodies[0];
        const bodyRows = tbody ? Array.from(tbody.rows) : [];

        const PAD_PX = 18;                             // breathing room per cell
        const CAP_PX = Math.max(140, tableW * 0.42);   // no single col dominates

        const dataThs = Array.from(headRow.cells)
            .filter((th) => th.hasAttribute("data-col-key"));
        if (dataThs.length < 2) return false;

        // Partition into flexible (text-driven) and fixed columns.
        const flex = [];
        let flexPctSum = 0;
        dataThs.forEach((th) => {
            const key = th.getAttribute("data-col-key");
            let curPct = parseFloat(th.style.width);
            if (!(curPct > 0)) curPct = th.getBoundingClientRect().width / tableW * 100;
            const sample = tbody && tbody.querySelector(`[data-k="${_cssEsc(key)}"]`);
            const isImage = !!(sample && (sample.classList.contains("qc-img-cell") ||
                                          sample.querySelector("img, .qc-img-wrap")));
            // The whole private strip (COST + PROFIT, 2026-08-25) is fixed:
            // these are narrow money columns and the fit is for text-driven ones.
            const isPrivate = th.classList.contains("qc-private")
                           || th.classList.contains("qc-cost-col");
            if (isImage || isPrivate) return;          // fixed - leave width as-is
            flex.push({ th, key, demandPx: 0 });
            flexPctSum += curPct;
        });
        if (flex.length < 2 || !(flexPctSum > 0)) return false;

        // Which columns print a formatted money value. The caller names them
        // (`opts.money`) because only the editor knows its own schema; this
        // module stays free of editor imports.
        const moneyOf = (opts && opts.money) || null;
        const isMoneyKey = (key) => {
            if (!moneyOf) return false;
            if (typeof moneyOf === "function") return !!moneyOf(key);
            if (typeof moneyOf.has === "function") return moneyOf.has(key);
            return Array.isArray(moneyOf) && moneyOf.indexOf(key) >= 0;
        };
        // Same shape the generators print (`quote_generator._money`): dollars
        // carry cents, dinars are whole. A caller may override for a document
        // billed in something else.
        const fmtMoney = (opts && opts.formatMoney) || function (v) {
            return "$" + Number(v).toLocaleString("en-US",
                { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        };

        // Content demand per flex column (editor font) - drives how the SURPLUS
        // above the header floors is shared, so long-text columns still win room.
        flex.forEach((c) => {
            const headInput = c.th.querySelector("input.qc-head-input");
            const headText = (headInput ? headInput.value : c.th.textContent) || "";
            let demand = _measureWidest(headText, _fontOf(headInput || c.th)) + PAD_PX;
            c.isMoney = isMoneyKey(c.key);
            c.moneyTexts = [];
            bodyRows.forEach((tr) => {
                const cell = tr.querySelector(`[data-k="${_cssEsc(c.key)}"]`);
                if (!cell) return;
                const field = cell.matches("textarea, input")
                    ? cell : cell.querySelector("textarea, input");
                const text = field ? field.value : cell.textContent;
                if (!text) return;
                if (c.isMoney) {
                    // A rate cell holds "4300"; the page prints "$4,300.00".
                    // Keep the printed form for the value floor below. A cell
                    // that is already formatted (a computed TOTAL) measures as
                    // it stands.
                    const n = parseFloat(String(text).replace(/[^0-9.\-]/g, ""));
                    c.moneyTexts.push(isFinite(n) ? fmtMoney(n) : String(text));
                }
                const w = _measureWidest(text, _fontOf(field || cell)) + PAD_PX;
                if (w > demand) demand = w;
            });
            c.demandPx = clamp(demand, MIN_COL_PX, CAP_PX);
        });
        const demandSum = flex.reduce((s, c) => s + c.demandPx, 0);
        if (!(demandSum > 0)) return false;

        // Per-column FLOOR so a header WORD never breaks mid-word (the "RAT E" /
        // "TOTA L" bug). When the caller passes the print geometry
        // (`opts.pdf = {widthMm, headerFont}`), the floor is the header's widest
        // word measured at the PRINT font, expressed as a % of the PRINT table
        // width - and since these percentages transfer 1:1 to the PDF
        // (table-layout: fixed; width: 100%), the header is guaranteed to fit in
        // print even though the on-screen table is wider. The floor is CAPPED
        // (HEADER_CAP_FRAC) so only short labels (RATE/TOTAL/UNIT/QTY/…) are
        // protected; a genuinely long header word may still wrap to a 2nd line,
        // exactly as before. Falls back to the editor-px minimum with no geometry.
        const pdf = (opts && opts.pdf) || null;
        const printTablePx = pdf && pdf.widthMm ? (pdf.widthMm / 25.4 * 96) : 0;
        const HEADER_CAP_FRAC = 0.17;   // a header floor may claim at most 17%
        const MONEY_CAP_FRAC  = 0.22;   // …and a money VALUE floor at most 22%
        const PRINT_PAD_PX = 16;        // cell padding (~8px) + borders (~2px) + safety margin
        const editorMinPct = MIN_COL_PX / tableW * 100;
        flex.forEach((c) => {
            let floorPct = editorMinPct;
            if (printTablePx > 0) {
                const headInput = c.th.querySelector("input.qc-head-input");
                const headText = (headInput ? headInput.value : c.th.textContent) || "";
                let wordPx = 0;
                headText.split(/\s+/).forEach((w) => {
                    if (!w) return;
                    const m = _measureWidest(w, pdf.headerFont);
                    if (m > wordPx) wordPx = m;
                });
                const need = Math.min(wordPx + PRINT_PAD_PX, HEADER_CAP_FRAC * printTablePx);
                floorPct = Math.max(editorMinPct, need / printTablePx * 100);

                // VALUE floor for money columns (2026-09-02). The header floor
                // above protects the word "RATE"; nothing protected the number
                // under it, so a fit could leave `$4,300.00` broken across two
                // lines as `$4,300` / `.00`.
                //
                // The editor's RATE cell holds the RAW value the user typed
                // ("4300") while the PDF prints "$4,300.00" - measuring the
                // input's own text therefore under-reserves by the currency
                // symbol, the separators and the decimals. TOTAL never showed
                // the bug because its cell already contains the formatted
                // string. So format the widest value the same way the generator
                // does and measure THAT, at the print body font.
                //
                // A money value must not wrap at all, so unlike the header this
                // floor is not a nicety - it is capped only to stop one absurd
                // number from eating the table.
                if (c.isMoney && pdf.bodyFont) {
                    let valuePx = 0;
                    (c.moneyTexts || []).forEach((t) => {
                        const m = _measureWidest(t, pdf.bodyFont);
                        if (m > valuePx) valuePx = m;
                    });
                    if (valuePx > 0) {
                        const want = Math.min(valuePx + PRINT_PAD_PX,
                                              MONEY_CAP_FRAC * printTablePx);
                        floorPct = Math.max(floorPct, want / printTablePx * 100);
                    }
                }
            }
            c.floorPct = floorPct;
        });

        // Water-fill: give every column its header floor, then split the remainder
        // by the content demand ABOVE each floor (long-text columns still win the
        // surplus). Keeps the flex columns' combined total - table stays 100%.
        let target;
        const floorSum = flex.reduce((s, c) => s + c.floorPct, 0);
        if (floorSum >= flexPctSum) {
            // Floors alone exceed the available width (many narrow columns) -
            // scale them proportionally to fit; nothing left to distribute.
            target = flex.map((c) => flexPctSum * c.floorPct / floorSum);
        } else {
            const remaining = flexPctSum - floorSum;
            const rawShare = flex.map((c) => flexPctSum * c.demandPx / demandSum);
            const extra = flex.map((c, i) => Math.max(0, rawShare[i] - c.floorPct));
            const extraSum = extra.reduce((a, b) => a + b, 0);
            target = flex.map((c, i) => c.floorPct +
                (extraSum > 0 ? remaining * extra[i] / extraSum : remaining / flex.length));
        }
        // Absorb rounding residual into the widest column so the sum is exact.
        const resid = flexPctSum - target.reduce((a, b) => a + b, 0);
        let maxI = 0;
        target.forEach((p, i) => { if (p > target[maxI]) maxI = i; });
        target[maxI] += resid;

        flex.forEach((c, i) => { c.th.style.width = target[i].toFixed(2) + "%"; });

        // Re-flow textarea heights so the editor reflects the new wrapping, and
        // re-seed the resize handles for the new widths.
        bodyRows.forEach((tr) => tr.querySelectorAll("textarea").forEach((ta) => {
            ta.style.height = "auto";
            ta.style.height = ta.scrollHeight + "px";
        }));
        if (window.refreshColResizers) window.refreshColResizers();
        return true;
    };

    // Convenience wiring for a toolbar "Fit columns" button: run the fit, flash
    // the button to confirm, and invoke an optional callback (editors use it to
    // flag the doc dirty). Toasts only when the fit can't run (empty table), so
    // the user isn't left wondering why nothing happened. No-op if the button
    // isn't in the DOM. `tableArg` is anything _resolveTable accepts (a thead
    // id like "#qThead", a table selector, or an element).
    window.wireFitColsButton = function (btnId, tableArg, opts) {
        const btn = document.getElementById(btnId);
        if (!btn) return;
        opts = opts || {};
        btn.addEventListener("click", (e) => {
            e.preventDefault();
            const ok = window.fitEditorColumns(tableArg, {
                pdf: opts.pdf,
                money: opts.money,
                formatMoney: opts.formatMoney,
            });
            if (!ok) {
                if (window.toast) window.toast(opts.emptyMsg ||
                    "Add rows with content first, then fit columns.", true);
                return;
            }
            btn.classList.add("just-fitted");
            setTimeout(() => btn.classList.remove("just-fitted"), 450);
            if (typeof opts.onFit === "function") opts.onFit();
        });
    };
})();
