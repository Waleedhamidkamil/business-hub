// ============================================================
// grid_paste.js  -  Phase M.5a  (Phase X.22 / X.23 - robust clipboard parsing)
// Excel-style grid paste into QUT / INV / DN / PO item tables.
//
// User flow:
//   1. Select a range of cells in Excel, Ctrl+C.
//   2. In the app, left-click any item-table cell (text or number input).
//   3a. Press Ctrl+V - the clipboard grid fills DOWN + ACROSS starting at the
//       clicked cell. New rows are appended automatically when the paste runs
//       past the bottom of the table.
//   3b. OR right-click the cell and pick "Paste here".
//
// How cell boundaries are decided (Phase X.23)
// --------------------------------------------
// Excel sends TWO clipboard flavours: a `text/html` <table> and `text/plain`
// TSV. The HTML table is AUTHORITATIVE - one <td> is one cell no matter how
// many line breaks it holds. Plain text is ambiguous: a single cell with
// wrapped text (Alt+Enter) arrives as UNQUOTED multi-line text, which looks
// exactly like several stacked rows. So we parse the HTML table first and only
// fall back to (quote-aware) plain text when no HTML table is present.
//
// Behaviour
// ---------
// - A single value with no line breaks is NOT intercepted on Ctrl+V - the
//   native browser paste fires so you can insert at the cursor inside a cell.
//   A single cell WITH line breaks IS intercepted so its newlines land in one
//   cell, intact (the reported bug).
// - The anchor is the cell the user is focused in / right-clicked on. Image
//   and computed columns are skipped (you can't paste text into an image).
// - Numeric columns get a lax parse: "1,234.5" / "$ 9.99" → 1234.5 / 9.99.
// - When the pasted block needs more rows than exist, empty rows are pushed
//   to the doc's state and one fullRender() rebuilds the DOM at the end.
// - After every paste the anchor cell is re-focused so Ctrl+Z (undo) keeps
//   working - the re-render destroys the old focused node, and without a
//   refocus the undo keydown would no longer reach the tbody listener.
// ============================================================
(function () {
    "use strict";

    // ---------- clipboard parsing ----------

    // Quote-aware delimited parser. A field wrapped in double quotes may contain
    // the delimiter, newlines, or escaped quotes (""). Used for the text/plain
    // fallback only (Excel quotes multi-line cells inside a multi-column range).
    function parseDelimited(text, delim) {
        const s = String(text == null ? "" : text)
            .replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        const rows = [];
        let row = [], field = "", inQ = false;
        for (let i = 0; i < s.length; i++) {
            const ch = s[i];
            if (inQ) {
                if (ch === '"') {
                    if (s[i + 1] === '"') { field += '"'; i++; }   // escaped quote
                    else inQ = false;                               // closing quote
                } else {
                    field += ch;                                    // incl. newlines/tabs
                }
            } else if (ch === '"') {
                inQ = true;
            } else if (ch === delim) {
                row.push(field); field = "";
            } else if (ch === '\n') {
                row.push(field); rows.push(row); row = []; field = "";
            } else {
                field += ch;
            }
        }
        row.push(field); rows.push(row);
        // Drop a single trailing all-empty row (Excel appends a final newline).
        while (rows.length && rows[rows.length - 1].every(c => c === "")) rows.pop();
        return rows;
    }

    // Plain-text clipboard → 2D grid. We only split columns on TAB (never
    // comma) so a single-line value containing commas pastes as one cell.
    function parseClipboardGrid(text) {
        if (!text) return [];
        return parseDelimited(text, "\t");
    }

    // Extract one HTML table cell's text, treating <br> and block elements as
    // hard line breaks and discarding the source-formatting whitespace Excel
    // sprinkles around cell content. Keeps a wrapped cell's real newlines
    // without inventing blank lines from pretty-printed HTML.
    function _htmlCellText(td) {
        // Excel inserts CRLF + indentation into a <td>'s raw text merely to
        // wrap its generated HTML source (for example `Potato (Big\r\n size)`).
        // Those characters are NOT cell line breaks. Walk the DOM so only
        // structural <br>/<p>/<div> boundaries become newlines, while raw text
        // whitespace is collapsed to a normal space.
        let value = "";

        function trimTrailingSpaces() {
            value = value.replace(/ +$/, "");
        }

        function appendStructuralBreak() {
            trimTrailingSpaces();
            if (value && !value.endsWith("\n")) value += "\n";
        }

        function appendText(raw) {
            let text = String(raw || "").replace(/ /g, " ").replace(/\s+/g, " ");
            // Whitespace used to indent HTML after a structural break is source
            // formatting, not part of the cell value.
            if (value.endsWith("\n")) text = text.replace(/^ +/, "");
            if (value.endsWith(" ") && text.startsWith(" ")) text = text.slice(1);
            value += text;
            return text.trim() !== "";
        }

        function walk(node) {
            if (node.nodeType === 3) return appendText(node.nodeValue);
            if (node.nodeType !== 1) return false;

            const tag = String(node.tagName || "").toUpperCase();
            if (tag === "BR") {
                trimTrailingSpaces();
                value += "\n"; // Each <br> is intentional, including repeats.
                return true;
            }

            const isBlock = tag === "P" || tag === "DIV";
            const beforeBlock = value;
            if (isBlock) appendStructuralBreak();

            let hasContent = false;
            node.childNodes.forEach((child) => { hasContent = walk(child) || hasContent; });

            if (isBlock) {
                // An empty formatting block must not invent a blank line.
                if (!hasContent) value = beforeBlock;
                else appendStructuralBreak();
            }
            return hasContent;
        }

        td.childNodes.forEach(walk);
        return value.replace(/ *\n */g, "\n").replace(/^[ \n]+|[ \n]+$/g, "");
    }

    // HTML clipboard (an Excel <table>) → 2D grid. AUTHORITATIVE source for
    // cell boundaries: one <td> = one cell regardless of how many newlines it
    // holds, so a single wrapped Excel cell yields a 1×1 grid.
    function parseClipboardHtml(html) {
        if (!html || !/<table/i.test(html)) return [];
        try {
            const doc = new DOMParser().parseFromString(html, "text/html");
            const table = doc.querySelector("table");
            if (!table) return [];
            const rows = [];
            table.querySelectorAll("tr").forEach((tr) => {
                const cells = [];
                tr.querySelectorAll("th,td").forEach((td) => cells.push(_htmlCellText(td)));
                if (cells.length) rows.push(cells);
            });
            while (rows.length && rows[rows.length - 1].every(c => c === "")) rows.pop();
            return rows;
        } catch (e) { return []; }
    }

    // Prefer the HTML table (unambiguous cell boundaries). Excel sends a single
    // wrapped cell as UNQUOTED multi-line text/plain - indistinguishable from N
    // stacked rows - so plain text alone can't tell them apart. The HTML can.
    // Fall back to quote-aware plain text only when there's no HTML table.
    function parseClipboard(html, text) {
        const fromHtml = parseClipboardHtml(html);
        if (fromHtml.length) return fromHtml;
        return parseClipboardGrid(text);
    }

    // Text that did NOT come from a spreadsheet range.
    //
    // Excel puts a real RANGE on the clipboard as an HTML <table>, which states
    // the cell boundaries exactly. Text copied from INSIDE one cell - the user
    // double-clicks the cell, selects its Alt+Enter lines and hits Ctrl+C -
    // carries NO html flavour at all, and its line breaks are content, not row
    // boundaries. Plain text alone cannot tell those two apart, so this asks the
    // only question that can be answered: did a grid produce it?
    //
    // No <table>, no TAB, and more than one line ⇒ loose prose. Pasted into a
    // cell that can hold line breaks, that is ONE value; splitting it into rows
    // is the bug this guards.
    function looksLikeLooseText(html, text) {
        if (html && /<table/i.test(html)) return false;   // a real range copy
        const s = String(text == null ? "" : text);
        if (s.indexOf("\t") >= 0) return false;           // tabs = real columns
        return /\n/.test(s.replace(/\s+$/, ""));          // more than one line
    }

    function looksLikeGrid(text) {
        if (!text) return false;
        if (text.indexOf("\t") >= 0) return true;
        return /\n/.test(text.trim());
    }

    function parseNumberLoose(v) {
        if (v == null) return v;
        const raw = String(v).trim();
        if (raw === "") return raw;
        const cleaned = raw.replace(/[, ]+/g, "").replace(/[^\d.\-eE+]/g, "");
        const n = parseFloat(cleaned);
        return isFinite(n) ? n : raw;
    }

    // ---------- core paste ----------

    function writableColumns(columns) {
        // `_readonly` covers the synthetic PROFIT column (2026-08-25): it is
        // computed from cost and price and has no row key, so a wide paste must
        // not spill a value into it.
        return columns.filter(c => c.kind !== "computed" && c.kind !== "image" && !c._readonly);
    }

    function pasteGrid(grid, anchorRowIdx, anchorColKey, opts) {
        if (!grid.length) return 0;
        const cols = writableColumns(opts.columns());
        const startCol = cols.findIndex(c => c.key === anchorColKey);
        if (startCol < 0) return 0;

        // Grow state so every targeted row exists, unless this grid is backed
        // by a fixed source list (Costing & Profit rows mirror saved items).
        const targetLast = anchorRowIdx + grid.length - 1;
        const canGrowRows = opts.allowRowGrowth !== false && typeof opts.addEmptyRow === "function";
        if (canGrowRows) {
            while (opts.rowCount() <= targetLast) opts.addEmptyRow();
        }

        let cellsWritten = 0;
        for (let r = 0; r < grid.length; r++) {
            const rowIdx = anchorRowIdx + r;
            if (rowIdx >= opts.rowCount()) break;
            const cells  = grid[r];
            for (let c = 0; c < cells.length; c++) {
                const col = cols[startCol + c];
                if (!col) break;   // ran off the right edge - silently drop
                let v = cells[c];
                if (col.kind === "num" || col.kind === "money") v = parseNumberLoose(v);
                opts.setCell(rowIdx, col.key, v);
                cellsWritten++;
            }
        }
        opts.fullRender();
        return cellsWritten;
    }

    // ---------- anchor lookup ----------
    // The anchor is the cell the user is interacting with: either an input/
    // textarea inside a <td data-k>, or the <td data-k> itself.

    function anchorFromTarget(target) {
        if (!target || !target.closest) return null;
        let el = target.closest("[data-k]");
        if (!el) return null;
        const tr = el.closest("tr[data-i]");
        if (!tr) return null;
        const rowIdx = parseInt(tr.dataset.i, 10);
        const colKey = el.dataset.k;
        if (!isFinite(rowIdx) || !colKey) return null;
        // Text columns render as <textarea>, numeric ones as <input> - so the
        // field itself is the editor's own statement about whether this cell
        // may hold line breaks. Read it off the DOM rather than the column
        // schema: the same module serves QUT/INV/DN/PO/Costing and only the
        // DOM is common to all five.
        let field = el;
        if (field.tagName !== "INPUT" && field.tagName !== "TEXTAREA") {
            field = (field.querySelector && field.querySelector("input, textarea")) || field;
        }
        const multiline = String(field.tagName || "").toUpperCase() === "TEXTAREA";
        return { rowIdx, colKey, multiline };
    }

    // ---------- floating menu ----------

    function closeMenu() {
        const m = document.getElementById("gridPasteMenu");
        if (m) m.remove();
    }

    // items: [{ act, label, icon, kbd, run }]. `act:"cancel"` renders the plain
    // dismiss row and needs no `run`. Items are built per right-click because
    // Copy only appears when there is in fact something to copy - a greyed-out
    // row would just be one more thing to read.
    // The element keeps its `gridPasteMenu` id and `grid-paste-menu` class: the
    // menu grew a Copy item, its styling did not change.
    function showContextMenu(x, y, items) {
        closeMenu();
        const menu = document.createElement("div");
        menu.id = "gridPasteMenu";
        menu.className = "grid-paste-menu";
        const ico = (name) => (window.ICON && window.ICON[name]) || "&#128203;";
        menu.innerHTML = (items || []).map((it) => (it.act === "cancel"
            ? `<button type="button" class="gpm-item gpm-cancel" data-act="cancel">Cancel</button>`
            : `<button type="button" class="gpm-item" data-act="${it.act}">
                <span class="gpm-ico">${ico(it.icon)}</span>
                <span>${it.label}</span>
                ${it.kbd ? `<span class="gpm-kbd">${it.kbd}</span>` : ""}
               </button>`)).join("");
        menu.style.left = x + "px";
        menu.style.top  = y + "px";
        document.body.appendChild(menu);

        // Clamp inside viewport
        const r = menu.getBoundingClientRect();
        if (r.right  > window.innerWidth  - 6) menu.style.left = (window.innerWidth  - r.width  - 6) + "px";
        if (r.bottom > window.innerHeight - 6) menu.style.top  = (window.innerHeight - r.height - 6) + "px";

        (items || []).forEach((it) => {
            const b = menu.querySelector(`[data-act="${it.act}"]`);
            if (b) b.onclick = () => { closeMenu(); return it.run ? it.run() : undefined; };
        });

        const off = (e) => {
            if (!menu.contains(e.target)) {
                document.removeEventListener("mousedown", off, true);
                closeMenu();
            }
        };
        const esc = (e) => {
            if (e.key === "Escape") {
                document.removeEventListener("keydown", esc, true);
                closeMenu();
            }
        };
        // Defer so the click that opened the menu doesn't immediately close it.
        setTimeout(() => {
            document.addEventListener("mousedown", off, true);
            document.addEventListener("keydown",  esc, true);
        }, 0);
    }

    // ---------- edit history (2026-08-01, replaces the M.5c paste-only stack)
    //
    // The old stack snapshotted ONLY before a paste. Anything typed afterwards
    // was never recorded, so one Ctrl+Z restored the pre-paste state and threw
    // away every edit made since - paste 40 rows, fix six prices, undo, and all
    // six corrections vanished with the paste. There was no redo at all.
    //
    // Now every committed change is one step, spreadsheet-style:
    //   * one step per cell edit  (focus -> type -> leave = ONE step, not one
    //     per keystroke; editors write to state on `input`, so the snapshot
    //     taken at focus time is the true "before")
    //   * one step per paste
    //   * one step per in-table click that changes the rows (row delete, etc.)
    // Redo is the mirror image and is cleared by any new edit, as everywhere else.
    const _hist    = new WeakMap();
    const _HIST_MAX = 60;

    function _h(tbody) {
        let h = _hist.get(tbody);
        if (!h) { h = { undo: [], redo: [], pending: null }; _hist.set(tbody, h); }
        return h;
    }
    function _isBlankRow(r) {
        if (!r || typeof r !== "object") return !r;
        return Object.keys(r).every(k => {
            const v = r[k];
            return v === "" || v == null || v === false;
        });
    }
    // Compare IGNORING trailing blank rows: the editors auto-append an empty row
    // as soon as the last one gets content, so a raw compare would log a bogus
    // step for merely putting the caret in the final row.
    function _key(snap) {
        if (!Array.isArray(snap)) return JSON.stringify(snap);
        const a = snap.slice();
        while (a.length && _isBlankRow(a[a.length - 1])) a.pop();
        return JSON.stringify(a);
    }
    function _record(tbody, before) {
        if (before == null) return;
        const h = _h(tbody);
        h.undo.push(before);
        if (h.undo.length > _HIST_MAX) h.undo.shift();
        h.redo.length = 0;          // a fresh edit invalidates the redo branch
        h.pending = null;
    }
    // Stash the state as it is BEFORE an interaction. Never overwrites a stash
    // that is already open, so one edit can't be split into two steps.
    function _begin(tbody, opts) {
        if (!opts.snapshot) return;
        const h = _h(tbody);
        if (h.pending == null) h.pending = opts.snapshot();
    }
    // Close an open stash: becomes a step only if something actually changed.
    function _commit(tbody, opts) {
        const h = _h(tbody);
        if (h.pending == null || !opts.snapshot) return false;
        const before = h.pending;
        h.pending = null;
        if (_key(before) === _key(opts.snapshot())) return false;
        _record(tbody, before);
        return true;
    }
    function _isField(el) {
        return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA"
                     || el.tagName === "SELECT");
    }
    // Move one step along the history. The state we are leaving is pushed onto
    // the opposite stack, which is what makes undo/redo round-trip exactly.
    function _step(tbody, opts, dir) {
        const h = _h(tbody);
        const from = dir === "undo" ? h.undo : h.redo;
        const to   = dir === "undo" ? h.redo : h.undo;
        if (!from.length || !opts.restore || !opts.snapshot) return null;
        to.push(opts.snapshot());
        opts.restore(from.pop());
        return { undo: h.undo.length, redo: h.redo.length };
    }

    // Read the clipboard for the right-click "Paste here" path. Tries the async
    // Clipboard API's read() so we can get text/html (and thus correct cell
    // boundaries); falls back to readText() when read() is unavailable/blocked.
    async function readClipboardRich() {
        const out = { html: "", text: "" };
        try {
            if (navigator.clipboard && navigator.clipboard.read) {
                const items = await navigator.clipboard.read();
                for (const it of items) {
                    if (it.types.indexOf("text/html") >= 0) {
                        try { out.html = await (await it.getType("text/html")).text(); } catch (_) {}
                    }
                    if (it.types.indexOf("text/plain") >= 0) {
                        try { out.text = await (await it.getType("text/plain")).text(); } catch (_) {}
                    }
                }
            }
        } catch (_) { /* fall through to readText */ }
        if (!out.html && !out.text && navigator.clipboard && navigator.clipboard.readText) {
            out.text = await navigator.clipboard.readText();
        }
        return out;
    }

    // ---------- copy ------------------------------------------------------
    // The app runs with the WebView2 default context menu switched off
    // (app.py starts pywebview with debug=False, and pywebview ties
    // AreDefaultContextMenusEnabled to that flag), so a right-click offers
    // exactly what this file puts in it and nothing else. Before Copy existed
    // here, the mouse could not copy text anywhere in the app - Ctrl+C was the
    // only way out.

    // What a right-click should copy. Read WHILE the menu is being built, never
    // when the item is clicked: pressing a menu button blurs the field, and a
    // DOM selection is collapsed by then, so there would be nothing left.
    function selectionText(el) {
        try {
            if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")
                && typeof el.selectionStart === "number"
                && el.selectionStart !== el.selectionEnd) {
                return String(el.value || "").slice(el.selectionStart, el.selectionEnd);
            }
        } catch (_) {
            // number/date/email inputs THROW on .selectionStart in Chromium
            // rather than returning null - fall through to the DOM selection.
        }
        try {
            const sel = window.getSelection && window.getSelection();
            if (sel && !sel.isCollapsed) return String(sel);
        } catch (_) { /* no selection to read */ }
        return "";
    }

    // The value of a form field, for a right-click that selected nothing.
    // Checkboxes and file pickers have no text worth copying; a password field
    // is deliberately excluded.
    function fieldValue(el) {
        if (!el || !el.tagName) return "";
        if (el.tagName === "SELECT") {
            const o = el.options && el.options[el.selectedIndex];
            return o ? String(o.text || "") : "";
        }
        if (el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") return "";
        const t = String(el.type || "").toLowerCase();
        if (t === "checkbox" || t === "radio" || t === "file" || t === "password") return "";
        return String(el.value == null ? "" : el.value);
    }

    // The whole grid cell, for a right-click inside an item table that selected
    // nothing. Read from the DOM rather than from opts.rows, so it works for
    // every editor without each one having to hand us its state.
    function cellText(target) {
        if (!target || !target.closest) return "";
        const td = target.closest("[data-k]");
        if (!td) return "";
        const f = (td.tagName === "INPUT" || td.tagName === "TEXTAREA" || td.tagName === "SELECT")
            ? td
            : (td.querySelector && td.querySelector("input, textarea, select"));
        if (f) return fieldValue(f);
        return String(td.textContent || "").trim();
    }

    async function copyToClipboard(text) {
        const s = String(text == null ? "" : text);
        if (!s) return false;
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(s);
                return true;
            }
        } catch (_) { /* fall through to the execCommand path */ }
        // Fallback for when the async Clipboard API is missing or refuses the
        // write. It has to focus a scratch textarea to work, so put the caret
        // back where it was - otherwise copying a cell would silently end the
        // edit the user was in the middle of.
        const was = document.activeElement;
        try {
            const ta = document.createElement("textarea");
            ta.value = s;
            ta.setAttribute("readonly", "");
            ta.style.cssText = "position:fixed;top:-1000px;left:-1000px;opacity:0;";
            document.body.appendChild(ta);
            ta.select();
            const ok = document.execCommand("copy");
            ta.remove();
            if (was && was.focus) { try { was.focus(); } catch (_) {} }
            return !!ok;
        } catch (_) {
            if (was && was.focus) { try { was.focus(); } catch (_) {} }
            return false;
        }
    }

    function copyItem(text) {
        return {
            act: "copy", icon: "copy", label: "Copy", kbd: "Ctrl+C",
            run: async () => {
                const ok = await copyToClipboard(text);
                if (window.toast) {
                    window.toast(ok ? "Copied" : "Copy failed - press Ctrl+C instead", !ok);
                }
            },
        };
    }

    // ---------- public wiring ----------
    // opts: {
    //   columns()           -> array of { key, kind } in display order
    //   rowCount()          -> number of rows currently in state
    //   addEmptyRow()       -> push one empty row to state (no DOM work)
    //   allowRowGrowth      -> false to stop at existing rows (Costing table)
    //   setCell(i, key, v)  -> write a single cell into state
    //   fullRender()        -> rebuild the tbody DOM from state
    //   snapshot()          -> return a deep-cloneable snapshot of row state
    //   restore(snapshot)   -> replace row state with snapshot + re-render
    // }
    function setupGridPaste(tbody, opts) {
        if (!tbody || tbody._gridPasteWired) return;
        tbody._gridPasteWired = true;

        // Re-focus the anchor cell after a paste/undo. fullRender() rebuilds the
        // tbody, destroying the previously-focused node; without this, focus
        // falls to <body> and the Ctrl+Z keydown never reaches this tbody.
        function _refocus(anchor) {
            if (!anchor) return;
            try {
                const tr = tbody.querySelector(`tr[data-i="${anchor.rowIdx}"]`);
                if (!tr) return;
                let el = tr.querySelector(`[data-k="${anchor.colKey}"]`);
                if (el && el.tagName !== "INPUT" && el.tagName !== "TEXTAREA") {
                    el = el.querySelector("input, textarea") || el;
                }
                if (el && el.focus) {
                    el.focus();
                    if (el.setSelectionRange && typeof el.value === "string") {
                        const n = el.value.length;
                        try { el.setSelectionRange(n, n); } catch (_) {}
                    }
                }
            } catch (_) { /* refocus is best-effort */ }
        }

        // Write a single value into the anchor cell (keeps multi-line text in
        // ONE cell). Numeric parse only when the value has no line breaks.
        function _doSingle(val, anchor) {
            const col = (opts.columns() || []).find(c => c.key === anchor.colKey);
            if (!col) return 0;
            // Close any half-finished cell edit first so it stays its own step
            // instead of being swallowed into the paste.
            _commit(tbody, opts);
            if (opts.snapshot) _record(tbody, opts.snapshot());
            let v = val;
            if (!/[\r\n]/.test(val) && (col.kind === "num" || col.kind === "money")) {
                v = parseNumberLoose(v);
            }
            opts.setCell(anchor.rowIdx, anchor.colKey, v);
            opts.fullRender();
            tbody._lastAnchor = anchor;
            _refocus(anchor);
            if (window.toast) window.toast("Pasted 1 cell (Ctrl+Z to undo)");
            return 1;
        }

        // Explicit right-click text paste: keep the clipboard's complete plain
        // text in the anchor cell, including line breaks and tabs. This is
        // intentionally separate from _doSingle because numeric columns must
        // not coerce the text and no grid rows/columns may be traversed.
        function _doText(val, anchor, note) {
            const col = (opts.columns() || []).find(c => c.key === anchor.colKey);
            if (!col) return 0;
            _commit(tbody, opts);
            if (opts.snapshot) _record(tbody, opts.snapshot());
            const text = String(val == null ? "" : val)
                .replace(/\r\n/g, "\n").replace(/\r/g, "\n");
            opts.setCell(anchor.rowIdx, anchor.colKey, text);
            opts.fullRender();
            tbody._lastAnchor = anchor;
            _refocus(anchor);
            if (window.toast) {
                window.toast(note || "Pasted text into 1 cell (Ctrl+Z to undo)");
            }
            return 1;
        }

        // Run a grid paste - snapshot first so Ctrl+Z can undo it.
        function _doPaste(grid, anchor) {
            _commit(tbody, opts);
            if (opts.snapshot) _record(tbody, opts.snapshot());
            const n = pasteGrid(grid, anchor.rowIdx, anchor.colKey, opts);
            tbody._lastAnchor = anchor;
            _refocus(anchor);
            if (n && window.toast) {
                const cols = Math.max(...grid.map(g => g.length));
                const totalCells = grid.reduce((sum, row) => sum + row.length, 0);
                const msg = n < totalCells
                    ? `Pasted ${n} cell${n === 1 ? "" : "s"} (stopped at the last row, Ctrl+Z to undo)`
                    : `Pasted ${grid.length} row${grid.length === 1 ? "" : "s"} × ${cols} cell${cols === 1 ? "" : "s"} (Ctrl+Z to undo)`;
                window.toast(msg);
            }
            return n;
        }

        // Decide single-cell vs grid for a parsed clipboard matrix. Returns true
        // if it handled the paste, false to let native paste run (plain single
        // value on Ctrl+V → cursor insert inside the cell).
        function _applyGrid(grid, anchor, allowNative) {
            if (!grid || !grid.length) return false;
            const single = grid.length === 1 && (grid[0] || []).length === 1;
            if (single) {
                const val = grid[0][0];
                if (allowNative && !/[\r\n]/.test(val)) return false;
                _doSingle(val, anchor);
                return true;
            }
            _doPaste(grid, anchor);
            return true;
        }

        tbody.addEventListener("paste", (ev) => {
            const cd = ev.clipboardData;
            if (!cd) return;
            const text = cd.getData("text/plain");
            const html = cd.getData("text/html");
            const anchor = anchorFromTarget(ev.target);
            if (!anchor) return;
            // Alt+Enter text copied from INSIDE one Excel cell arrives with no
            // HTML table to prove its boundaries. Into a cell that holds line
            // breaks it is one value - splitting it into a row per line is the
            // bug this branch fixes. Everything else still goes through the
            // normal parse.
            if (anchor.multiline && looksLikeLooseText(html, text)) {
                const lines = text.replace(/\s+$/, "").split("\n").length;
                _doText(text, anchor,
                    `Pasted ${lines} lines into 1 cell (Ctrl+Z to undo, or right-click → Paste as rows)`);
                ev.preventDefault();
                return;
            }
            const grid = parseClipboard(html, text);
            // _applyGrid returns false for a plain single value → let the native
            // input paste handle it (cursor insert inside the cell).
            if (_applyGrid(grid, anchor, true)) ev.preventDefault();
        });

        tbody.addEventListener("contextmenu", (ev) => {
            const anchor = anchorFromTarget(ev.target);
            if (!anchor) return;
            ev.preventDefault();
            const items = [];
            // Copy leads, as it does in every OS menu. Selecting part of the
            // cell copies just that; selecting nothing copies the whole cell.
            const copyable = selectionText(ev.target) || cellText(ev.target);
            if (copyable) items.push(copyItem(copyable));
            items.push({
                act: "paste", icon: "clipboard", label: "Paste here", kbd: "Ctrl+V",
                run: async () => {
                    let clip;
                    try {
                        clip = await readClipboardRich();
                    } catch (_) {
                        if (window.toast) window.toast("Clipboard read blocked - focus a cell and press Ctrl+V instead", true);
                        return;
                    }
                    if (!clip || (!clip.html && !clip.text)) return;
                    if (anchor.multiline && looksLikeLooseText(clip.html, clip.text)) {
                        const lines = clip.text.replace(/\s+$/, "").split("\n").length;
                        _doText(clip.text, anchor,
                            `Pasted ${lines} lines into 1 cell (Ctrl+Z to undo)`);
                        return;
                    }
                    const grid = parseClipboard(clip.html, clip.text);
                    // Right-click is an explicit paste, so even a plain single
                    // value replaces the anchor cell (allowNative=false).
                    _applyGrid(grid, anchor, false);
                },
            });
            items.push({
                act: "paste-rows", icon: "clipboard", label: "Paste as rows",
                run: async () => {
                    let clip;
                    try {
                        clip = await readClipboardRich();
                    } catch (_) {
                        if (window.toast) window.toast("Clipboard read blocked - focus a cell and press Ctrl+V instead", true);
                        return;
                    }
                    if (!clip || !clip.text) return;
                    // Deliberately the PLAIN-TEXT reading: this item exists to
                    // force one-row-per-line when the smart route chose a
                    // single cell, so the HTML table must not override it.
                    _applyGrid(parseClipboardGrid(clip.text), anchor, false);
                },
            });
            items.push({
                act: "paste-text", icon: "clipboard", label: "Paste text here",
                run: async () => {
                    let clip;
                    try {
                        clip = await readClipboardRich();
                    } catch (_) {
                        if (window.toast) window.toast("Clipboard read blocked - focus a cell and press Ctrl+V instead", true);
                        return;
                    }
                    if (!clip || !clip.text) return;
                    _doText(clip.text, anchor);
                },
            });
            items.push({ act: "cancel" });
            showContextMenu(ev.clientX, ev.clientY, items);
        });

        // ---- history tracking for ordinary edits ----------------------------
        // Focusing a cell opens a stash; leaving it closes it. `change` fires
        // before `focusout`, so the stash is normally closed there and the
        // focusout pass is a no-op - it exists to clear a stash left open by a
        // cell the user visited but did not alter, which would otherwise merge
        // with the NEXT cell's edit and turn two steps into one.
        tbody.addEventListener("focusin", (ev) => {
            if (_isField(ev.target)) _begin(tbody, opts);
        });
        tbody.addEventListener("change", (ev) => {
            if (_isField(ev.target)) _commit(tbody, opts);
        });
        tbody.addEventListener("focusout", () => {
            setTimeout(() => {
                if (!tbody.contains(document.activeElement)) _commit(tbody, opts);
            }, 0);
        });
        // Clicks on in-table controls (delete row, toggles) mutate the rows
        // without any field ever being focused. Snapshot before the click and
        // compare after it - anything that actually changed becomes a step.
        tbody.addEventListener("mousedown", (ev) => {
            if (_isField(ev.target)) return;       // the focus path owns those
            _begin(tbody, opts);
            setTimeout(() => _commit(tbody, opts), 0);
        });

        // Ctrl+Z / Ctrl+Shift+Z (Cmd on macOS), plus Ctrl+Y as the usual Windows
        // redo alias. When we have nothing to restore we deliberately do NOT
        // preventDefault, so the browser's own per-character text undo still
        // works inside a cell that has never been committed.
        tbody.addEventListener("keydown", (ev) => {
            if (!(ev.ctrlKey || ev.metaKey)) return;
            const k = String(ev.key || "").toLowerCase();
            const isUndo = k === "z" && !ev.shiftKey;
            const isRedo = (k === "z" && ev.shiftKey) || k === "y";
            if (!isUndo && !isRedo) return;
            if (!opts.restore || !opts.snapshot) return;

            const h = _h(tbody);
            // An edit in progress becomes its own step first, so the very next
            // Ctrl+Z undoes what the user just typed rather than skipping past
            // it to the previous action.
            if (isUndo) _commit(tbody, opts);
            if (isUndo && !h.undo.length) return;      // nothing of ours → native undo
            if (isRedo && !h.redo.length) return;

            ev.preventDefault();
            ev.stopPropagation();
            const focusAnchor = anchorFromTarget(document.activeElement) || tbody._lastAnchor;
            const n = _step(tbody, opts, isUndo ? "undo" : "redo");
            _refocus(focusAnchor);
            if (n && window.toast) {
                const what = isUndo ? "Undo" : "Redo";
                const tail = isUndo
                    ? (n.undo ? ` - ${n.undo} more` : " - nothing left to undo")
                    : (n.redo ? ` - ${n.redo} more` : "");
                window.toast(what + tail);
            }
        });
    }

    // ---------- right-click copy everywhere else --------------------------
    // Item tables answer their own right-click above; this catches the rest of
    // the app - client names, totals, document numbers, dialog text, the
    // Master Data panels. It fires last (the tbody listener is an ancestor of
    // the cell and therefore runs first) and stands down whenever a grid has
    // already claimed the event.
    //
    // No selection and not a form field means no menu at all, deliberately:
    // falling back to the element's textContent would put half the page on the
    // clipboard for a right-click on any container.
    document.addEventListener("contextmenu", (ev) => {
        if (ev.defaultPrevented) return;                              // a grid answered
        const t = ev.target;
        if (t && t.closest && t.closest("#gridPasteMenu")) return;    // our own menu
        const text = selectionText(t) || fieldValue(t);
        if (!text) return;
        ev.preventDefault();
        showContextMenu(ev.clientX, ev.clientY, [copyItem(text), { act: "cancel" }]);
    });

    // ---------- expose ----------
    window.setupGridPaste     = setupGridPaste;
    window.copyToClipboard    = copyToClipboard;
    window.parseClipboardGrid = parseClipboardGrid;
    window.parseClipboardHtml = parseClipboardHtml;
    window.parseClipboard     = parseClipboard;
    window.looksLikeGrid      = looksLikeGrid;
    window.looksLikeLooseText = looksLikeLooseText;
})();
