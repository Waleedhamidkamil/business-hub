/* =========================================================
   Bulk entry - paste rows from Excel into any items table.
   ---------------------------------------------------------
   Self-contained. Loads BEFORE the editor scripts so they can call
   window.bulkPasteRegister({...}) at eval time (same pattern as signing.js).

   Flow:
     1. User clicks "Paste / Import" -> a wizard modal opens.
     2. User pastes a block copied from Excel (Ctrl+V) or drops a .csv/.tsv.
     3. We parse the clipboard (HTML table first, TSV/CSV fallback), auto-detect
        a header row, and auto-map source columns onto the editor's schema.
     4. A live preview shows the cleaned rows; the user fixes the mapping if
        needed, picks Append/Replace, and clicks Add.
     5. The rows are inserted via the editor's own addRow path.

   Nothing is saved to disk here - the rows land in the editor exactly as if
   typed, so totals, autocomplete-on-save and the Unsaved chip all behave
   normally, and the user can still review/undo before saving.
   ========================================================= */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const PREVIEW_ROWS = 8;

    // -------------------------------------------------- pure parsing/cleaning
    // Convert Arabic-Indic (٠-٩) and Eastern-Arabic (۰-۹) digits to ASCII.
    function coerceDigits(s) {
        return String(s == null ? "" : s)
            .replace(/[٠-٩]/g, (d) => String(d.charCodeAt(0) - 0x0660))
            .replace(/[۰-۹]/g, (d) => String(d.charCodeAt(0) - 0x06F0));
    }

    // Normalise a header label for fuzzy matching ("U/Price" -> "uprice").
    function normHead(s) {
        return coerceDigits(s).toLowerCase().replace(/[^a-z0-9]+/g, "");
    }

    // Clean a numeric cell: strip currency/spaces/thousands separators, accept
    // Arabic digits. Returns a canonical number string, or "" if not numeric.
    function cleanNumber(s) {
        let t = coerceDigits(s).trim();
        if (!t) return "";
        t = t.replace(/[,\s]/g, "").replace(/[^0-9.\-]/g, "");
        const dot = t.indexOf(".");
        if (dot !== -1) t = t.slice(0, dot + 1) + t.slice(dot + 1).replace(/\./g, "");
        if (t === "" || t === "-" || t === "." || t === "-.") return "";
        const n = parseFloat(t);
        return isFinite(n) ? String(n) : "";
    }

    function trimTrailingEmpty(rows) {
        let end = rows.length;
        while (end > 0 && rows[end - 1].every((c) => String(c || "").trim() === "")) end--;
        return rows.slice(0, end);
    }

    // Parse clipboard into a 2D string matrix. Prefers the HTML <table>
    // (handles cells with commas / line breaks); falls back to TSV, then CSV.
    function parseMatrix(html, text) {
        if (html && /<table/i.test(html)) {
            try {
                const doc = new DOMParser().parseFromString(html, "text/html");
                const table = doc.querySelector("table");
                if (table) {
                    const rows = [];
                    table.querySelectorAll("tr").forEach((tr) => {
                        const cells = [];
                        tr.querySelectorAll("th,td").forEach((td) => {
                            cells.push((td.textContent || "").replace(/ /g, " ").trim());
                        });
                        if (cells.length) rows.push(cells);
                    });
                    if (rows.length) return trimTrailingEmpty(rows);
                }
            } catch (e) { /* fall through to text */ }
        }
        let t = String(text || "").replace(/\r\n/g, "\n").replace(/\r/g, "\n").replace(/\n+$/, "");
        if (!t) return [];
        const lines = t.split("\n");
        const hasTab = lines.some((l) => l.indexOf("\t") >= 0);
        const sep = hasTab ? "\t" : (lines.some((l) => l.indexOf(",") >= 0) ? "," : "\t");
        return trimTrailingEmpty(lines.map((l) => l.split(sep).map((c) => c.trim())));
    }

    // Pad every row to the widest row so the grid is rectangular.
    function padMatrix(m) {
        const w = m.reduce((mx, r) => Math.max(mx, r.length), 0);
        return m.map((r) => { const c = r.slice(); while (c.length < w) c.push(""); return c; });
    }

    // Synonyms by target column key - lets us auto-map common Excel headers.
    const SYN = {
        name:        ["name", "item", "material", "product", "part", "article"],
        item:        ["item", "material", "product", "part", "article", "name"],
        description: ["description", "desc", "details", "detail", "spec", "specs", "specification"],
        specs:       ["specs", "specification", "specifications", "spec", "details"],
        unit:        ["unit", "uom", "um", "size", "unitsize", "unitofmeasure", "measure", "period"],
        qty:         ["qty", "quantity", "qnty", "count", "nos", "pcs", "pieces"],
        rate:        ["rate", "price", "unitprice", "uprice", "cost", "rent", "rental", "monthlyrate"],
        total:       ["total", "amount", "subtotal", "linetotal"],
        company:     ["company", "supplier", "vendor", "brand", "maker", "manufacturer"],
        equipment:   ["equipment", "vehicle", "machine", "asset", "machinery"],
        role:        ["role", "position", "title", "worker", "labour", "labor", "designation"],
        remarks:     ["remarks", "remark", "notes", "note", "comment", "comments"],
    };

    function bestTarget(src, targets, used) {
        const n = normHead(src);
        if (!n) return null;
        let best = null, bestScore = 0;
        targets.forEach((t) => {
            if (used && used[t.key]) return;
            const cands = [t.key, t.head].concat(SYN[t.key] || []).map(normHead).filter(Boolean);
            let score = 0;
            cands.forEach((c) => {
                if (c === n) score = Math.max(score, 3);
                else if (n.length >= 3 && c.length >= 3 && (c.indexOf(n) >= 0 || n.indexOf(c) >= 0))
                    score = Math.max(score, 2);
            });
            if (score > bestScore) { bestScore = score; best = t.key; }
        });
        return bestScore > 0 ? best : null;
    }

    function looksLikeHeader(row, targets) {
        if (!row || !row.length) return false;
        let nameMatches = 0, numeric = 0;
        row.forEach((c) => {
            const v = String(c || "").trim();
            if (!v) return;
            if (cleanNumber(v) !== "" && /^[\s\d.,\-٠-٩۰-۹$%]+$/.test(v)) numeric++;
            if (bestTarget(v, targets, {})) nameMatches++;
        });
        return nameMatches >= 1 && numeric === 0;
    }

    function positionMap(targets, width) {
        const map = new Array(width).fill(null);
        for (let i = 0; i < width && i < targets.length; i++) map[i] = targets[i].key;
        return map;
    }

    function autoMap(headerRow, targets, width) {
        if (!headerRow) return positionMap(targets, width);
        const map = new Array(width).fill(null);
        const used = {};
        for (let i = 0; i < width; i++) {
            const t = bestTarget(headerRow[i] || "", targets, used);
            if (t) { map[i] = t; used[t] = true; }
        }
        if (Object.keys(used).length === 0) return positionMap(targets, width);  // nothing matched
        return map;
    }

    function escapeHtml(s) {
        return String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[m]));
    }

    // Expose pure helpers for headless tests.
    window.__bulkPasteInternals = {
        coerceDigits, normHead, cleanNumber, parseMatrix, padMatrix,
        looksLikeHeader, autoMap, bestTarget,
    };

    // -------------------------------------------------- editor registry
    const registry = {};
    function bulkPasteRegister(opts) {
        if (!opts || !opts.docType) return;
        registry[opts.docType] = opts;
        const btn = $(opts.buttonId);
        if (btn) btn.addEventListener("click", () => openWizard(opts.docType));
    }
    window.bulkPasteRegister = bulkPasteRegister;

    // -------------------------------------------------- wizard state
    let _popup = null;          // the shared Popup handle ([[14-UI-CONTRACTS]])
    let cur = null;   // { reg, targets:[{key,head,kind}], primaryKey, matrix, header, map[] }

    function isNumKind(key) {
        const t = cur.targets.find((x) => x.key === key);
        return t && (t.kind === "num" || t.kind === "money");
    }
    function numIssue(raw, key) {
        const r = String(raw == null ? "" : raw).trim();
        return key && isNumKind(key) && r !== "" && cleanNumber(r) === "";
    }
    function cleanCell(raw, key) {
        const r = String(raw == null ? "" : raw).trim();
        if (!key) return "";
        if (isNumKind(key)) { const c = cleanNumber(r); return c !== "" ? c : r; }
        return r;
    }
    function dataRows() { return cur.header ? cur.matrix.slice(1) : cur.matrix; }

    function buildObjs() {
        const rows = dataRows();
        const objs = [];
        let skipped = 0, issues = 0;
        const pk = cur.primaryKey;
        const pkMapped = pk && cur.map.indexOf(pk) >= 0;
        rows.forEach((r) => {
            const o = {};
            let anyVal = false;
            for (let i = 0; i < cur.map.length; i++) {
                const key = cur.map[i];
                if (!key) continue;
                const raw = r[i] != null ? r[i] : "";
                o[key] = cleanCell(raw, key);
                if (String(o[key]).trim()) anyVal = true;
                if (numIssue(raw, key)) issues++;
            }
            const empty = pkMapped ? !String(o[pk] || "").trim() : !anyVal;
            if (empty) { skipped++; return; }
            objs.push(o);
        });
        return { objs, skipped, issues, total: rows.length };
    }

    function openWizard(docType) {
        const reg = registry[docType];
        if (!reg) return;
        const targets = (reg.getColumns && reg.getColumns()) || [];
        if (!targets.length) {
            if (window.toast) window.toast("This table has no importable columns.", true);
            return;
        }
        cur = {
            reg, targets,
            primaryKey: (reg.primaryKey && reg.primaryKey()) || null,
            matrix: null, header: false, map: [],
        };
        $("bulkStep1").hidden = false;
        $("bulkStep2").hidden = true;
        $("bulkCommit").disabled = true;
        $("bulkCommit").textContent = "Add rows";
        // Shared behaviour, markup stays here ([[14-UI-CONTRACTS]]).
        _popup = window.Popup.mount($("bulkBackdrop"));
        setTimeout(() => { const c = $("bulkPasteCatcher"); if (c) c.focus(); }, 30);
    }

    function closeWizard() {
        if (_popup) { _popup.close(null); _popup = null; }
        else $("bulkBackdrop").hidden = true;
        const c = $("bulkPasteCatcher"); if (c) c.value = "";
        cur = null;
    }

    function onPasted(html, text) {
        if (!cur) return;
        const m = padMatrix(parseMatrix(html, text));
        if (!m.length) {
            if (window.toast) window.toast("Nothing to import - the clipboard had no rows.", true);
            return;
        }
        applyMatrix(m);
    }

    // Phase M.3 - separate code-path for matrices that came from a parsed
    // file (the backend has already turned binary xlsx into strings, so we
    // don't run parseMatrix again - that would mangle Chinese/Arabic etc.).
    function applyMatrix(m) {
        if (!cur) return;
        m = padMatrix(m);
        if (!m.length) {
            if (window.toast) window.toast("Nothing to import - the file had no rows.", true);
            return;
        }
        cur.matrix = m;
        cur.header = looksLikeHeader(m[0], cur.targets);
        $("bulkHasHeader").checked = cur.header;
        cur.map = autoMap(cur.header ? m[0] : null, cur.targets, m[0].length);
        $("bulkStep1").hidden = true;
        $("bulkStep2").hidden = false;
        renderGrid();
    }

    // -------------------------------------------------- file → matrix
    async function pickSpreadsheetFile() {
        const api = (window.pywebview && window.pywebview.api) || null;
        if (!api || !api.bulk_pick_spreadsheet) {
            if (window.toast) window.toast("File picker unavailable in this build.", true);
            return;
        }
        try {
            const picked = await api.bulk_pick_spreadsheet();
            if (!picked || picked.cancelled) return;
            if (!picked.ok) {
                if (window.toast) window.toast(picked.error || "Couldn't pick file", true);
                return;
            }
            const r = await api.bulk_parse_spreadsheet(picked.path);
            if (!r || !r.ok) {
                if (window.toast) window.toast(
                    "Couldn't read that file: " + (r && r.error || "unknown"), true);
                return;
            }
            applyMatrix(r.rows || []);
        } catch (e) {
            if (window.toast) window.toast("Picker error: " + (e && e.message || e), true);
        }
    }

    function handleDroppedFile(f) {
        // .xlsx / .xlsm files are zip archives - reading them as text gives
        // mojibake ("PK", "docProps/", ...). Route binary spreadsheets through
        // the backend parser. CSV / TSV stay in JS (fast path).
        const name = (f.name || "").toLowerCase();
        const isBinarySpreadsheet =
            /\.(xlsx|xlsm|xlsb|xls|ods)$/i.test(name)
            || (f.type && /spreadsheet|excel/i.test(f.type));
        if (isBinarySpreadsheet) {
            sendFileToParser(f);
            return;
        }
        // Fallback: read as text (CSV/TSV).
        const r = new FileReader();
        r.onload = () => onPasted("", String(r.result || ""));
        r.onerror = () => {
            if (window.toast) window.toast("Couldn't read the dropped file.", true);
        };
        r.readAsText(f);
    }

    async function sendFileToParser(f) {
        const api = (window.pywebview && window.pywebview.api) || null;
        if (!api || !api.bulk_parse_uploaded_bytes) {
            if (window.toast) window.toast(
                "This build can't parse .xlsx files. Save as .csv first.", true);
            return;
        }
        const reader = new FileReader();
        reader.onload = async () => {
            try {
                const r = await api.bulk_parse_uploaded_bytes(f.name || "file.xlsx",
                                                              String(reader.result || ""));
                if (!r || !r.ok) {
                    if (window.toast) window.toast(
                        "Couldn't read that .xlsx: " + (r && r.error || "unknown"), true);
                    return;
                }
                applyMatrix(r.rows || []);
            } catch (e) {
                if (window.toast) window.toast("Parser error: " + (e && e.message || e), true);
            }
        };
        reader.onerror = () => {
            if (window.toast) window.toast("Couldn't read the dropped file.", true);
        };
        reader.readAsDataURL(f);
    }

    function renderGrid() {
        const grid = $("bulkGrid");
        if (!grid || !cur || !cur.matrix) return;
        const width = cur.matrix[0].length;
        const rows = dataRows();

        let thead = "<thead><tr>";
        for (let i = 0; i < width; i++) {
            let opts = `<option value="">- Skip -</option>`;
            cur.targets.forEach((t) => {
                opts += `<option value="${escapeHtml(t.key)}" ${cur.map[i] === t.key ? "selected" : ""}>${escapeHtml(t.head)}</option>`;
            });
            thead += `<th><select class="bulk-map" data-i="${i}">${opts}</select></th>`;
        }
        thead += "</tr></thead>";

        let tb = "<tbody>";
        rows.slice(0, PREVIEW_ROWS).forEach((r) => {
            tb += "<tr>";
            for (let i = 0; i < width; i++) {
                const key = cur.map[i];
                const raw = r[i] != null ? r[i] : "";
                const bad = numIssue(raw, key);
                const shown = key ? cleanCell(raw, key) : raw;
                const cls = (key ? "" : "bulk-skip") + (bad ? " bulk-bad" : "");
                tb += `<td class="${cls.trim()}" title="${bad ? "Not a number - will be left for you to fix" : ""}">${escapeHtml(shown)}</td>`;
            }
            tb += "</tr>";
        });
        tb += "</tbody>";

        grid.innerHTML = thead + tb;
        grid.querySelectorAll(".bulk-map").forEach((sel) => {
            sel.onchange = () => {
                const i = +sel.dataset.i;
                const v = sel.value || null;
                if (v) for (let j = 0; j < cur.map.length; j++) if (j !== i && cur.map[j] === v) cur.map[j] = null;
                cur.map[i] = v;
                renderGrid();
            };
        });
        recompute();
    }

    function recompute() {
        const { objs, skipped, issues, total } = buildObjs();
        const parts = [`<strong>${objs.length}</strong> row${objs.length === 1 ? "" : "s"} ready`];
        if (skipped) parts.push(`${skipped} skipped (empty)`);
        if (issues) parts.push(`${issues} cell${issues === 1 ? "" : "s"} need a number`);
        let txt = parts.join(" · ");
        if (total > PREVIEW_ROWS) txt += ` · previewing first ${PREVIEW_ROWS} of ${total}`;
        $("bulkSummary").innerHTML = txt;
        const commit = $("bulkCommit");
        commit.disabled = objs.length === 0;
        commit.textContent = objs.length ? `Add ${objs.length} row${objs.length === 1 ? "" : "s"}` : "Add rows";
    }

    function commit() {
        if (!cur) return;
        const { objs } = buildObjs();
        if (!objs.length) return;
        const modeEl = document.querySelector("input[name='bulkMode']:checked");
        const replace = !!(modeEl && modeEl.value === "replace");
        try {
            cur.reg.addRows(objs, replace);
        } catch (e) {
            if (window.toast) window.toast("Import failed: " + (e && e.message || e), true);
            return;
        }
        const n = objs.length;
        closeWizard();
        if (window.toast) window.toast(`Added ${n} row${n === 1 ? "" : "s"} from paste.`);
    }

    // -------------------------------------------------- one-time wiring
    function init() {
        const catcher = $("bulkPasteCatcher");
        if (catcher) {
            catcher.addEventListener("paste", (e) => {
                const cd = e.clipboardData || window.clipboardData;
                const html = cd ? cd.getData("text/html") : "";
                const text = cd ? cd.getData("text/plain") : "";
                e.preventDefault();
                onPasted(html, text);
            });
        }
        const drop = $("bulkDrop");
        if (drop) {
            // Phase M.3 - clicking the drop box opens the OS file browser
            // (was: just re-focused the hidden catcher, which is useless to
            // the user since the catcher is already focused on open).
            drop.addEventListener("click", () => pickSpreadsheetFile());
            drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drag"); });
            drop.addEventListener("dragleave", () => drop.classList.remove("drag"));
            drop.addEventListener("drop", (e) => {
                e.preventDefault(); drop.classList.remove("drag");
                const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
                if (!f) return;
                handleDroppedFile(f);
            });
        }
        const cancel = $("bulkCancel"); if (cancel) cancel.onclick = closeWizard;
        const commitBtn = $("bulkCommit"); if (commitBtn) commitBtn.onclick = commit;
        const repaste = $("bulkRepaste");
        if (repaste) repaste.onclick = () => {
            $("bulkStep1").hidden = false;
            $("bulkStep2").hidden = true;
            $("bulkCommit").disabled = true;
            setTimeout(() => { const c = $("bulkPasteCatcher"); if (c) c.focus(); }, 30);
        };
        const hdr = $("bulkHasHeader");
        if (hdr) hdr.onchange = () => {
            if (!cur || !cur.matrix) return;
            cur.header = hdr.checked;
            cur.map = autoMap(cur.header ? cur.matrix[0] : null, cur.targets, cur.matrix[0].length);
            renderGrid();
        };
        document.querySelectorAll("input[name='bulkMode']").forEach((r) => { r.onchange = recompute; });
        const bd = $("bulkBackdrop");
        if (bd) bd.addEventListener("click", (e) => { if (e.target === bd) closeWizard(); });
        document.addEventListener("keydown", (e) => {
            const b = $("bulkBackdrop");
            if (!b || b.hidden) return;
            if (e.key === "Escape") closeWizard();
            else if (e.key === "Enter" && e.ctrlKey) { const c = $("bulkCommit"); if (c && !c.disabled) commit(); }
        });
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();

    // Dev/test hooks (used by the headless screenshot harness; harmless in prod).
    window.__bulkPasteInternals.openWizard = openWizard;
    window.__bulkPasteInternals.onPasted = onPasted;
})();
