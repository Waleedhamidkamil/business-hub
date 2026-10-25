/* Per-section settings modal (Phase E).
   One modal definition reused by Quotation, Delivery Note, and Invoice
   mini-hubs / editors. Reuses globals from app.js:
     api(), toast(), showModal(), withLoading(), esc(), safe()

   Public entry point: window.openDocSettings(docType, opts?)
     docType: "quotation" | "delivery" | "invoice"
     opts:    { onSaved?: () => void }

   Sections per doc type:
     - header   : company name lines, title, brand color, logo upload
     - signature: depends on type (single signer for QUT/INV, two for DN)
     - closing  : closing line, address options
*/

const DOC_LABELS = {
    quotation: "Quotation",
    delivery:  "Delivery Note",
    invoice:   "Invoice",
};

async function openDocSettings(docType, opts = {}) {
    if (!DOC_LABELS[docType]) return;
    let res, schemas = {};
    try { res = await api().get_doc_settings(docType); }
    catch (e) { toast("Couldn't load settings: " + (e.message || e), true); return; }
    const settings = res.settings || {};

    // Pull the live schema for the active sub-type so the modal can show the
    // current column-head names (defaults) next to user override inputs.
    if (opts.subtype) {
        try {
            if (docType === "quotation")    schemas = await api().get_quote_types();
            else if (docType === "invoice") schemas = await api().get_inv_types();
        } catch { /* no overrides UI */ }
    }
    // Phase G.7: Delivery Notes don't have sub-types, but still expose the
    // columns editor at the top level. Synthesize a "delivery" entry into
    // schemas so the renderer can use the same code path with opts.subtype
    // implicitly being "delivery".
    if (docType === "delivery") {
        schemas.delivery = {
            primary_key: "item",
            columns: settings.columns || [],
        };
        opts = { ...opts, subtype: "delivery", subtypeLabel: "" };
    }

    /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). These three settings
       dialogs are long, so they take the `xl` width and their body scrolls -
       the title and the Save row stay put, which is what the shared box does
       for every popup. "Restore defaults" acts without closing (`keep`): you
       need to see what it restored before you commit to it. */
    const _h = window.Popup.open({
        title: docSettingsTitle(docType, opts),
        size: "xl",
        dir: "ltr",
        className: "ds-modal",
        body: renderDocSettingsModal(docType, settings, opts, schemas),
        html: true,
        actions: [
            { label: "Cancel", value: "cancel", cls: "ghost" },
            { label: "Restore defaults", value: "reset", cls: "secondary", keep: true,
              title: "Restore this section's defaults" },
            { label: "Save", value: "save", cls: "primary", keep: true },
        ],
    });
    const backdrop = _h.panel;
    backdrop._popup = _h;
    bindDocSettingsModal(backdrop, docType, settings, opts, schemas);
}
window.openDocSettings = openDocSettings;

// ============================================================
// Markup
// ============================================================
/* The popup's title. Lives outside the body template because the shared box
   owns the head now - the template returns only what goes inside. */
function docSettingsTitle(docType, opts = {}) {
    return opts.subtype && opts.subtypeLabel
        ? `${opts.subtypeLabel} ${DOC_LABELS[docType]} settings`
        : `${DOC_LABELS[docType]} settings`;
}

function renderDocSettingsModal(docType, s, opts = {}, schemas = {}) {
    const c = s.company || {};
    const nameLines = (c.name_lines || []).join("\n");
    const brand     = (s.brand_color || "#1F3864").toUpperCase();
    const addrs     = (s.address_options || []).join("\n");

    // ---- signature block varies by type ----
    let sigBlock = "";
    if (docType === "delivery") {
        sigBlock = `
            <section class="ds-section">
                <h3>Signature labels</h3>
                <div class="ds-grid two">
                    <label>Left (Recipient)
                        <input type="text" data-k="recipient_label" value="${esc(c.recipient_label || "")}">
                    </label>
                    <label>Right (Supplier)
                        <input type="text" data-k="supplier_label" value="${esc(c.supplier_label || "")}">
                    </label>
                </div>
            </section>`;
    } else if (docType === "invoice") {
        sigBlock = `
            <section class="ds-section">
                <h3>Signature</h3>
                <div class="ds-grid two">
                    <label>Title label
                        <input type="text" data-k="signature_label" value="${esc(c.signature_label || "")}">
                    </label>
                    <label>Person name
                        <input type="text" data-k="signature_name" value="${esc(c.signature_name || "")}">
                    </label>
                </div>
            </section>`;
    } else { // quotation
        sigBlock = `
            <section class="ds-section">
                <h3>Signature</h3>
                <div class="ds-grid two">
                    <label>Person name
                        <input type="text" data-k="signature_name" value="${esc(c.signature_name || "")}">
                    </label>
                    <label>Title
                        <input type="text" data-k="signature_title" value="${esc(c.signature_title || "")}">
                    </label>
                </div>
            </section>`;
    }

    // ---- Per-sub-type / customisable-table section (Phase G.2 + G.4 + G.7) ----
    // Quotation & invoice get this per-sub-type. Delivery has no sub-types
    // but uses the same UI to customise its base columns at the top level.
    let subtypeBlock = "";
    if (opts.subtype && (docType === "quotation" || docType === "invoice" || docType === "delivery")) {
        const subKey   = opts.subtype;
        const subLabel = opts.subtypeLabel || subKey;
        const subSchema  = (schemas && schemas[subKey]) || {};
        const activeCols = Array.isArray(opts.columns) && opts.columns.length ? opts.columns : null;
        const liveCols   = activeCols || subSchema.columns || [];
        const primary    = subSchema.primary_key;
        const subOverride = s[subKey] || {};
        const subTerms   = (subOverride.terms || []).join("\n");
        const subLayout  = subOverride.layout || {};
        // The columns editor (Phase G.4) prefills from the LIVE columns -
        // which already reflect any override.
        const colsState = liveCols.map(c => ({
            key:  c.key,
            head: c.head || "",
            kind: c.kind || "text",
            // Phase L.4 - carry width through so saving the override doesn't
            // strip it. Without this, every save divided 96.4% equally
            // across columns, breaking the schema's intentional weighting.
            width: c.width,
            primary: c.key === primary,
        }));

        const sectionTitle = (docType === "delivery")
            ? "Table - customise columns & terms"
            : `${esc(subLabel)} - type-specific`;
        const sectionDesc = (docType === "delivery")
            ? "Customise the table columns of every Delivery Note. The Rate/Total price columns are added on top automatically when the “Show prices” toggle is on."
            : `These settings only affect the <strong>${esc(subLabel)}</strong> ${esc(DOC_LABELS[docType])} type. Other sub-types keep their own settings.`;

        // Phase M (item 9) - Default Terms textarea removed.
        // Reason: QUT editor already has an inline Terms (#) button below the
        // table. INV/DN will adopt the same in-editor pattern. Static "default
        // terms" lives in code defaults; per-document overrides are now done
        // through the in-editor Terms popup, not the settings modal.
        subtypeBlock = `
            <section class="ds-section ds-subtype">
                <h3>${sectionTitle}</h3>
                <p class="helper subtle">${sectionDesc}</p>

                <div class="ds-cols-editor" data-cols-state="${esc(JSON.stringify(colsState))}">
                    <div class="ds-cols-title">Table columns
                        <span class="helper subtle"> · reorder, rename, change type, remove, or add custom columns</span>
                    </div>
                    <div class="ds-cols-list" id="dsColsList"></div>
                    <button type="button" class="ghost ds-add-col" id="dsAddCol">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                        Add column
                    </button>
                </div>

            </section>`;
        // 2026-07-30 - the per-sub-type "Max items per page" / "Min row height"
        // boxes were removed: config parsed them and echoed them back (so they
        // looked saved) but NO generator ever reads a sub-type `layout`. The row
        // constants are module-level, so they cannot vary per sub-type without
        // threading the sub-type through build_layout. The doc-level Min row
        // height below is the one that actually works. `subLayout` is still read
        // so an existing settings.json entry survives a save untouched.
        void subLayout;
        // subTerms still read above so existing settings.json entries are
        // preserved on save (we just no longer present them in the UI).
        void subTerms;
    }

    // ---- Table-layout knobs (every doc type) ----
    const layout = s.layout || {};
    const layoutBlock = `
        <section class="ds-section">
            <h3>Row spacing (advanced)</h3>
            <div class="ds-grid two">
                <label>Min row height (mm)
                    <input type="number" id="dsMinRow" min="6" max="40" step="0.5"
                           value="${layout.min_row_mm ?? ""}" placeholder="auto">
                    <span class="helper subtle">The shortest a row may be. Raising it spreads items over more pages.</span>
                </label>
                <label>Max row height (mm)
                    <input type="number" id="dsMaxRow" min="8" max="60" step="0.5"
                           value="${layout.max_row_mm ?? ""}" placeholder="auto">
                    <span class="helper subtle">The tallest a row may stretch on a part-full page, so a short document doesn't get airy rows.</span>
                </label>
                ${docType === "quotation" ? `
                <label>Image row height (mm)
                    <input type="number" id="dsImageRow" min="10" max="60" step="0.5"
                           value="${layout.image_row_min_mm ?? ""}" placeholder="auto">
                    <span class="helper subtle">Height reserved for a row with a product photo - the single biggest control over how many picture items fit on a page.</span>
                </label>` : ""}
                <label>Gap between table & signature (mm)
                    <input type="number" id="dsSafeGap" min="0" max="40" step="0.5"
                           value="${layout.safe_gap_mm ?? ""}" placeholder="auto">
                    <span class="helper subtle">Breathing room kept clear below the last row.</span>
                </label>
            </div>
            <label class="ds-check">
                <input type="checkbox" id="dsAutofit" ${layout.autofit_rows ? "checked" : ""}>
                <span>
                    <strong>Auto-fit row height</strong>
                    <span class="helper subtle">Each row hugs its text exactly - no extra space above or below, whether the cell is 1, 2 or 3 lines. Turn off for the classic evenly-spaced rows.</span>
                </span>
            </label>
            <div class="helper subtle">Leave the numbers blank to use the program defaults. Defaults are tuned to match the reference PDFs.</div>
        </section>`;

    // ---- Invoice-only extras: withholding + bank info editor ----
    let invExtra = "";
    if (docType === "invoice") {
        const pct = (typeof s.withholding_pct === "number") ? s.withholding_pct : 3.3;
        invExtra = `
            <section class="ds-section">
                <h3>Withholding Tax</h3>
                <label>Tax percent (shown on every invoice as “Withholding Tax (X %)”)
                    <input type="number" id="dsWithPct" min="0" max="100" step="0.1" value="${pct}">
                </label>
            </section>
            <section class="ds-section">
                <h3>Bank accounts</h3>
                <div class="helper subtle">Payment accounts, and the exchange rate each one
                    carries, are in <b>Settings &gt; Payments</b> (moved 2026-09-21). They are
                    edited in one place now: a quotation takes its cost exchange rate from the
                    client's receiving account, so the list is no longer an invoice-only thing.</div>
            </section>`;
    }

    return `
            <div class="ds-body">

                ${subtypeBlock}

                <p class="helper subtle" style="margin:2px 2px 10px;">
                    Company header, closing line, brand color &amp; client-address
                    options are now in <strong>App Settings → Document Header,
                    Closing &amp; Addresses</strong> (shared, edited per type there).
                </p>

                ${sigBlock}

                ${invExtra}

                ${layoutBlock}

            </div>`;
}

// ============================================================
// Behavior
// ============================================================
// ============================================================
// Columns editor (Phase G.4)
//   Each row has up/down arrows, head input, key (readonly for default cols),
//   kind dropdown, and a delete button. Custom (user-added) columns are fully
//   editable. The primary_key column can't be deleted.
// ============================================================
const COL_KINDS = [
    { v: "text",  label: "Text" },
    { v: "num",   label: "Number" },
    { v: "money", label: "Money ($)" },
    // computed/image/index only for default columns - not user-addable.
];
const COL_KINDS_ADV = COL_KINDS.concat([
    { v: "computed", label: "Computed (qty × rate)" },
    { v: "image",    label: "Image" },
    { v: "index",    label: "Index (auto NO.)" },
]);

function renderColsEditor(root) {
    const wrap = root.querySelector(".ds-cols-editor");
    const list = root.querySelector("#dsColsList");
    if (!wrap || !list) return null;

    const state = JSON.parse(wrap.dataset.colsState || "[]");
    state._dirty = false;
    const markDirty = () => { state._dirty = true; };

    function draw() {
        list.innerHTML = "";
        state.forEach((c, i) => {
            const row = document.createElement("div");
            row.className = "ds-col-row";
            if (c.primary) row.classList.add("is-primary");
            const kindOpts = COL_KINDS_ADV.map(k =>
                `<option value="${k.v}"${k.v === c.kind ? " selected" : ""}>${k.label}</option>`
            ).join("");
            // Phase L.5 - expose the column width so users can tune it.
            // Empty = use default from the schema (auto-rebalanced).
            const widthVal = (typeof c.width === "number" && c.width > 0)
                ? c.width.toFixed(2).replace(/\.0+$/, "") : "";
            row.innerHTML = `
                <div class="ds-col-reorder">
                    <button type="button" class="ds-col-arrow" data-act="up"   ${i === 0 ? "disabled" : ""} title="Move up">▲</button>
                    <button type="button" class="ds-col-arrow" data-act="down" ${i === state.length - 1 ? "disabled" : ""} title="Move down">▼</button>
                </div>
                <input type="text"   class="ds-col-key"   value="${esc(c.key)}"  placeholder="key (snake_case)" ${c.primary ? "readonly" : ""}>
                <input type="text"   class="ds-col-head"  value="${esc(c.head)}" placeholder="Header text">
                <select class="ds-col-kind" ${c.primary ? "disabled" : ""}>${kindOpts}</select>
                <input type="number" class="ds-col-width" value="${esc(widthVal)}" placeholder="auto" step="0.5" min="2" max="60" title="Column width %">
                <button type="button" class="del-btn" title="Remove column" ${c.primary ? "disabled" : ""}>${window.ICON ? window.ICON.trash : "×"}</button>
            `;
            list.appendChild(row);

            // wire the row
            row.querySelector(".ds-col-key").addEventListener("input", (e) => {
                state[i].key = e.target.value;
                markDirty();
            });
            row.querySelector(".ds-col-head").addEventListener("input", (e) => {
                state[i].head = e.target.value;
                markDirty();
            });
            row.querySelector(".ds-col-kind").addEventListener("change", (e) => {
                state[i].kind = e.target.value;
                markDirty();
            });
            row.querySelector(".ds-col-width").addEventListener("input", (e) => {
                const v = parseFloat(e.target.value);
                state[i].width = (!isNaN(v) && v > 0) ? v : undefined;
                markDirty();
            });
            row.querySelectorAll(".ds-col-arrow").forEach(btn => {
                btn.onclick = () => {
                    const dir = btn.dataset.act === "up" ? -1 : 1;
                    const j = i + dir;
                    if (j < 0 || j >= state.length) return;
                    [state[i], state[j]] = [state[j], state[i]];
                    markDirty();
                    draw();
                };
            });
            row.querySelector(".del-btn").onclick = () => {
                if (c.primary) return;
                state.splice(i, 1);
                markDirty();
                draw();
            };
        });
    }
    draw();

    root.querySelector("#dsAddCol").onclick = () => {
        // Generate a unique key - col_1, col_2, ...
        let n = state.length + 1;
        const taken = new Set(state.map(c => c.key));
        while (taken.has(`col_${n}`)) n++;
        state.push({ key: `col_${n}`, head: "New column", kind: "text", primary: false });
        markDirty();
        draw();
    };

    return state;   // caller reads from this array at save time
}

function bindDocSettingsModal(backdrop, docType, originalSettings, opts, schemas) {
    /* The PANEL *is* `.ds-modal` - `Popup.open({className})` puts the class on
       the panel itself (popup.js), so looking for it as a DESCENDANT returns
       null and every `root.querySelector` below throws on the first call. That
       killed the WHOLE modal for all three doc types: Save, Cancel, Restore
       defaults, Add bank and every bank card, all unwired, with only a console
       error to show for it. Missed in the 2026-08-26 popup migration -
       `openPoSettings` was corrected to `const root = backdrop;` and this
       one was not. Keep both the same. */
    const root = backdrop;
    const colsState = opts.subtype ? renderColsEditor(root) : null;

    // Phase M.6 - Header (name lines / title / brand color / logo), Closing,
    // and Address options moved to App Settings, so those inputs no longer
    // exist in this modal. Brand-color sync and logo-upload wiring removed.

    // Close helpers
    const close = () => {
        (backdrop._popup || _h).close(null);
    };
    root.querySelector("[data-value='cancel']").onclick = close;

    // Reset: clear this doc-type's section from settings.json and reload.
    root.querySelector("[data-value='reset']").onclick = safe(async () => {
        const choice = await showModal({
            title: `Reset ${DOC_LABELS[docType]} settings?`,
            body: "This section will go back to the program defaults. Other doc types and global settings stay as-is.",
            buttons: [
                { label: "Cancel",   value: "cancel", cls: "ghost" },
                { label: "Reset",    value: "ok",     cls: "primary" },
            ],
        });
        if (choice !== "ok") return;
        const r = await api().save_doc_settings(docType, {});
        if (!r.ok) { toast("Reset failed: " + r.error, true); return; }
        toast(`${DOC_LABELS[docType]} settings reset`);
        close();
        if (opts.onSaved) opts.onSaved({ columnsChanged: true, reset: true });
    }, "Reset");

    // Save
    root.querySelector("[data-value='save']").onclick = safe(async () => {
        const payload = collectDocSettings(root, docType, originalSettings);
        let columnsChanged = false;

        // Per-sub-type / customisable-table fields (Phase G.2 + G.4 + G.7).
        if (opts.subtype) {
            const subPayload = {};
            // Phase M (item 9) - Default Terms textarea was removed from the
            // UI. Preserve any previously-saved terms verbatim so editing
            // other knobs doesn't wipe them. The in-editor Terms button is
            // now the canonical way to manage terms.
            const prevSub = (originalSettings && originalSettings[opts.subtype]) || {};
            if (Array.isArray(prevSub.terms) && prevSub.terms.length) {
                subPayload.terms = prevSub.terms.slice();
            }

            // Columns editor (Phase G.4). Validate before sending. If the
            // editor opened with a document-specific active snapshot but the
            // user only changed layout/signature settings, preserve the
            // existing type-default columns instead of promoting this
            // document's columns into the subtype defaults.
            if (Array.isArray(colsState)) {
                columnsChanged = !!colsState._dirty;
                if (columnsChanged) {
                    const seen = new Set();
                    const cleaned = [];
                    let bad = null;
                    for (const c of colsState) {
                        const key  = (c.key || "").trim();
                        const head = (c.head || "").trim();
                        if (!key || !head) { bad = "Every column needs a key and a header."; break; }
                        if (!/^[a-z][a-z0-9_]*$/i.test(key)) { bad = `Column key "${key}" must start with a letter and use letters/digits/underscores only.`; break; }
                        if (seen.has(key)) { bad = `Duplicate column key "${key}".`; break; }
                        seen.add(key);
                        // Phase L.4 - pass width through so future saves don't
                        // strip the schema's intentional weighting.
                        const out = { key, head, kind: c.kind || "text" };
                        if (typeof c.width === "number" && c.width > 0) out.width = c.width;
                        cleaned.push(out);
                    }
                    if (bad) { toast(bad, true); return; }
                    subPayload.columns = cleaned;
                } else {
                    const prevColumns = (docType === "delivery")
                        ? originalSettings.columns
                        : prevSub.columns;
                    if (Array.isArray(prevColumns) && prevColumns.length) {
                        subPayload.columns = prevColumns.map(c => ({ ...c }));
                    }
                }
            }

            // 2026-07-30 - there is no per-sub-type spacing any more. The two
            // boxes that fed this (`max_items`, `min_row_mm`) were dead: NO
            // generator reads a sub-type `layout`, because the row constants
            // are module-level and cannot vary per sub-type. Sending {} clears
            // the stale entries out of settings.json. Spacing now comes solely
            // from the doc-level block in collectDocSettings, which is verified
            // end-to-end by tests/spacing_settings_reach_renderer.test.py.
            subPayload.layout = {};

            if (docType === "delivery") {
                // DN has no sub-types - its table fields live at the top of the
                // section rather than nested under a sub-type key, so the
                // doc-level layout (auto-fit + the spacing knobs) is what ships.
                const mergedLayout = { ...(payload.layout || {}) };
                delete subPayload.layout;
                Object.assign(payload, subPayload);   // columns, terms
                payload.layout = mergedLayout;
            } else {
                // {} on the sub-type key removes the override entirely.
                payload[opts.subtype] = Object.keys(subPayload).length ? subPayload : {};
            }
        }

        const r = await api().save_doc_settings(docType, payload);
        if (!r.ok) { toast("Save failed: " + r.error, true); return; }
        toast(`${DOC_LABELS[docType]} settings saved`);
        close();
        if (opts.onSaved) opts.onSaved({ columnsChanged });
    }, "Save settings");

}

function collectDocSettings(root, docType, originalSettings) {
    // Phase M.6 - Header (name_lines/title/brand_color), Closing, and Address
    // options moved to App Settings. Start `company` from the EXISTING saved
    // company so those fields are preserved, and overlay only the signature
    // fields that still live in this modal. We don't send address_options or
    // brand_color here, so the backend shallow-merge leaves them untouched.
    const c = { ...((originalSettings && originalSettings.company) || {}) };

    if (docType === "delivery") {
        c.recipient_label = root.querySelector('[data-k="recipient_label"]').value.trim();
        c.supplier_label  = root.querySelector('[data-k="supplier_label"]').value.trim();
    } else if (docType === "invoice") {
        c.signature_label = root.querySelector('[data-k="signature_label"]').value.trim();
        c.signature_name  = root.querySelector('[data-k="signature_name"]').value.trim();
    } else {
        c.signature_name  = root.querySelector('[data-k="signature_name"]').value.trim();
        c.signature_title = root.querySelector('[data-k="signature_title"]').value.trim();
    }

    const payload = { company: c };

    // Table-layout knobs (every doc type).
    // Phase L.2 fix: ALWAYS send the layout block (possibly empty) so that
    // clearing every knob to "auto" actually clears the saved override on the
    // backend. Previously we omitted the key when blank, so the previously-
    // saved value stuck and "Auto" never worked.
    const layout = {};
    const intIfSet = (id, key) => {
        // Null-safe: #dsImageRow only exists on the quotation panel (it is the
        // only doc type with an image column).
        const el = root.querySelector(id);
        if (!el) return;
        const raw = (el.value || "").trim();
        if (!raw) return;
        const v = parseFloat(raw);
        if (!isNaN(v)) layout[key] = v;
    };
    // 2026-07-30 - max_items / budget_mm are gone: nothing read them. Every knob
    // listed here is verified to reach the renderer by
    // tests/spacing_settings_reach_renderer.test.py.
    intIfSet("#dsMinRow",    "min_row_mm");
    intIfSet("#dsMaxRow",    "max_row_mm");
    intIfSet("#dsImageRow",  "image_row_min_mm");   // quotation only
    intIfSet("#dsSafeGap",   "safe_gap_mm");
    const dsAuto = root.querySelector("#dsAutofit");
    if (dsAuto && dsAuto.checked) layout.autofit_rows = true;
    payload.layout = layout;   // {} when all knobs blank - backend removes the override.

    if (docType === "invoice") {
        const pct = parseFloat(root.querySelector("#dsWithPct").value);
        if (!isNaN(pct)) payload.withholding_pct = pct;
    }
    return payload;
}


// ============================================================
// Purchase Order settings modal (Arabic)
// ============================================================
function poLayoutPatch(root) {
    const numberOrClear = (selector) => {
        const raw = (root.querySelector(selector).value || "").trim();
        if (!raw) return "";             // explicit clear restores Auto
        const value = Number(raw);
        return Number.isFinite(value) ? value : "";
    };
    return {
        table_min_row_mm:     numberOrClear("#po_min_row"),
        table_line_height_mm: numberOrClear("#po_line_h"),
        table_header_mm:      numberOrClear("#po_header_mm"),
        signature_mm:         numberOrClear("#po_sig_mm"),
        // False is a real choice and deep_merge deliberately keeps it.
        autofit_rows:         root.querySelector("#po_autofit").checked,
    };
}
window.poLayoutPatch = poLayoutPatch;

async function openPoSettings() {
    /* 2026-09-05 - the PO reads and writes through the SAME bridge as
       Quotation / Delivery / Invoice (`get_doc_settings` / `save_doc_settings`,
       section `purchase_order`). It used to be the odd one out: its keys sat at
       the top level of settings.json and went through `save_settings`, a
       different call with a SHALLOW merge - which is how a panel could drop a
       key it never displayed. Layout numbers now live under `layout`, shaped
       like the other three. */
    let s, stored;
    try {
        const response = await api().get_doc_settings("po");
        s = response.settings || {};
        stored = response.stored || {};
    }
    catch (e) { toast("Couldn't load settings: " + (e.message || e), true); return; }

    const c = s.company || {};
    // Show only explicit overrides in the numeric boxes. A blank box reads
    // "auto" and means the tuned program default, exactly like the other
    // document editors. The effective values still live in `s.layout`.
    const lay = stored.layout || {};
    const effectiveLay = s.layout || {};
    const effectiveAutofit = Object.prototype.hasOwnProperty.call(lay, "autofit_rows")
        ? !!lay.autofit_rows
        : !!effectiveLay.autofit_rows;
    const brand = (s.brand_color || "#002060").toUpperCase();

    /* Shared Popup ([[14-UI-CONTRACTS]]) - RTL, like the PO itself. */
    const _h = window.Popup.open({
        title: "إعدادات طلب الشراء (PO)",
        size: "xl",
        dir: "rtl",
        className: "ds-modal",
        html: true,
        actions: [
            { label: "إلغاء", value: "cancel", cls: "ghost" },
            { label: "حفظ", value: "save", cls: "primary", keep: true },
        ],
        body: `
            <div class="ds-body">

                <section class="ds-section">
                    <h3>هوية الشركة</h3>
                    <label>الاسم بالعربية (سطر واحد لكل صف)
                        <textarea id="po_name_ar" rows="4">${esc((c.name_ar_lines || []).join("\n"))}</textarea>
                    </label>
                    <label>الاسم بالإنجليزية (سطر واحد لكل صف)
                        <textarea id="po_name_en" rows="4" dir="ltr">${esc((c.name_en_lines || []).join("\n"))}</textarea>
                    </label>
                    <div class="ds-grid two">
                        <label>عنوان الطلب
                            <input type="text" id="po_title_ar" value="${esc(c.title_ar || "")}">
                        </label>
                        <label>اللون الرئيسي
                            <span class="ds-color-row">
                                <input type="color" id="po_brand" value="${esc(brand)}">
                                <input type="text"  id="po_brand_hex" value="${esc(brand)}" maxlength="7" dir="ltr">
                            </span>
                        </label>
                    </div>
                </section>

                <section class="ds-section">
                    <h3>التوقيع</h3>
                    <div class="ds-grid two">
                        <label>اسم المدير العام
                            <input type="text" id="po_manager_name" value="${esc(c.manager_name_ar || "")}">
                        </label>
                        <label>وصف المدير
                            <input type="text" id="po_manager_label" value="${esc(c.manager_label_ar || "")}">
                        </label>
                        <label>اسم مقدم الطلب
                            <input type="text" id="po_submitter_name" value="${esc(c.submitter_name_ar || "")}">
                        </label>
                        <label>وصف مقدم الطلب
                            <input type="text" id="po_submitter_label" value="${esc(c.submitter_label_ar || "")}">
                        </label>
                    </div>
                </section>

                <section class="ds-section">
                    <h3>الترقيم</h3>
                    <div class="ds-grid two">
                        <label>بادئة رقم الطلب
                            <input type="text" id="po_prefix" maxlength="3" value="${esc(s.prefix || "A")}" dir="ltr">
                        </label>
                    </div>
                </section>

                <section class="ds-section">
                    <h3>تخطيط الجدول (للمتقدمين)</h3>
                    <div class="ds-grid two">
                        <!-- 2026-07-29 - "أقصى عدد مواد لكل طلب" and "ارتفاع الجدول
                             المسموح" are gone: a PO now paginates over pages
                             instead of splitting into extra POs, so the page
                             budget is derived per page role from the measured
                             template geometry, not from one flat number. -->
                        <label>ارتفاع سطر واحد (mm)
                            <input type="number" id="po_min_row" min="8.2" max="20" step="0.1" value="${lay.table_min_row_mm ?? ""}" placeholder="auto" dir="ltr">
                            <span class="helper subtle">اتركه فارغاً لاستخدام الضبط التلقائي (${effectiveLay.table_min_row_mm ?? 10.3} mm). رفعه يزيد الفراغ وقد يزيد عدد الصفحات.</span>
                        </label>
                        <label>ارتفاع سطر إضافي (mm)
                            <input type="number" id="po_line_h" min="3" max="15" step="0.5" value="${lay.table_line_height_mm ?? ""}" placeholder="auto" dir="ltr">
                            <span class="helper subtle">المساحة المحسوبة للنص الذي يلتف إلى سطر إضافي.</span>
                        </label>
                        <label>ارتفاع رأس الجدول (mm)
                            <input type="number" id="po_header_mm" min="6" max="20" step="0.5" value="${lay.table_header_mm ?? ""}" placeholder="auto" dir="ltr">
                        </label>
                        <label>مساحة التوقيع المحجوزة (mm)
                            <input type="number" id="po_sig_mm" min="20" max="90" step="1" value="${lay.signature_mm ?? ""}" placeholder="auto" dir="ltr">
                            <span class="helper subtle">هذه ليست هوامش الصفحة. إنها مساحة حماية أسفل آخر صفحة حتى لا يتداخل الجدول مع الختم والتوقيع.</span>
                        </label>
                    </div>
                    <label class="ds-check" dir="rtl">
                        <input type="checkbox" id="po_autofit" ${effectiveAutofit ? "checked" : ""}>
                        <span>
                            <strong>احتواء ارتفاع الصف تلقائياً</strong>
                            <span class="helper subtle">يلتصق كل صف بنصه تماماً بدون مسافة إضافية أعلى أو أسفل النص.</span>
                        </span>
                    </label>
                    <div class="helper subtle">حذف أي رقم يعيده إلى <strong>auto</strong>. استخدم الاحتواء التلقائي للطلبات الطويلة ذات الصفوف القصيرة مثل A-88.</div>
                </section>

                <section class="ds-section">
                    <h3>تصنيف الشركات</h3>
                    <p class="helper subtle" dir="rtl">الأسماء المكتوبة في عمود «لشركة» بأوامر الشراء. «عميل» = يحتاج سند تسليم وفاتورة ويظهر في المهام (تسليم وفوترة). «شركتنا» = استخدام داخلي للمكتب، لا يظهر في المهام.</p>
                    <div id="poCompanyTags" class="ds-tag-list"></div>
                    <div class="ds-tag-add">
                        <input type="text" id="poTagAdd" placeholder="أضف اسم شركة…">
                        <button type="button" class="ghost" id="poTagAddBtn">إضافة</button>
                    </div>
                </section>

            </div>`,
    });
    const backdrop = _h.panel;
    const root = backdrop;
    const colorEl = root.querySelector("#po_brand");
    const hexEl   = root.querySelector("#po_brand_hex");
    colorEl.addEventListener("input", () => { hexEl.value = colorEl.value.toUpperCase(); });
    hexEl.addEventListener("input", () => {
        const v = hexEl.value.trim();
        if (/^#[0-9A-Fa-f]{6}$/.test(v)) colorEl.value = v;
    });

    // --- Company classification list (client vs our own office) -------------
    // Seeded server-side on first read (all existing PO companies → "client").
    const tagState = {};
    let seededTags = {};
    // The section owns the map; `get_po_company_tags` still seeds it from
    // existing POs the first time, so keep it as the fallback.
    if (s.company_tags && Object.keys(s.company_tags).length) {
        seededTags = s.company_tags;
    } else {
        try { seededTags = (await api().get_po_company_tags()) || {}; } catch (e) { seededTags = {}; }
    }
    Object.keys(seededTags).forEach(n => { tagState[n] = seededTags[n]; });

    const tagListEl = root.querySelector("#poCompanyTags");
    function drawTags() {
        const names = Object.keys(tagState).sort((a, b) => a.localeCompare(b));
        tagListEl.innerHTML = names.length
            ? names.map(n =>
                `<div class="ds-tag-row" data-name="${esc(n)}">`
                + `<span class="ds-tag-name">${esc(n)}</span>`
                + `<select class="ds-tag-sel">`
                +   `<option value="client" ${tagState[n] === "internal" ? "" : "selected"}>عميل - للفوترة</option>`
                +   `<option value="internal" ${tagState[n] === "internal" ? "selected" : ""}>شركتنا - داخلي</option>`
                + `</select>`
                + `<button type="button" class="del-btn" title="حذف">${window.ICON ? window.ICON.trash : "×"}</button>`
                + `</div>`).join("")
            : `<div class="helper subtle" dir="rtl">لا توجد شركات بعد - ستُضاف تلقائياً عند حفظ أوامر شراء، أو أضفها يدوياً بالأسفل.</div>`;
        tagListEl.querySelectorAll(".ds-tag-row").forEach(row => {
            const nm = row.dataset.name;
            row.querySelector(".ds-tag-sel").addEventListener("change", (e) => {
                tagState[nm] = e.target.value === "internal" ? "internal" : "client";
            });
            row.querySelector(".del-btn").addEventListener("click", () => {
                delete tagState[nm];
                drawTags();
            });
        });
    }
    drawTags();
    const addTag = () => {
        const inp = root.querySelector("#poTagAdd");
        const nm = (inp.value || "").trim();
        if (!nm) return;
        // case-insensitive de-dupe against existing keys
        const key = nm.toLowerCase();
        if (!Object.keys(tagState).some(k => k.toLowerCase() === key)) tagState[nm] = "client";
        inp.value = "";
        drawTags();
    };
    root.querySelector("#poTagAddBtn").onclick = addTag;
    root.querySelector("#poTagAdd").addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); addTag(); }
    });

    const close = () => {
        (backdrop._popup || _h).close(null);
    };
    root.querySelector("[data-value='cancel']").onclick = close;

    root.querySelector("[data-value='save']").onclick = safe(async () => {
        const splitLines = (txt) => (txt || "")
            .split(/\r?\n/).map(x => x.trim()).filter(Boolean);
        const hex = (hexEl.value || "").trim();
        const colorOk = /^#[0-9A-Fa-f]{6}$/.test(hex);

        /* One patch for the `purchase_order` section - the same call and the
           same DEEP merge the other three doc types use. Only what this panel
           owns is sent; anything else in the section is left alone by the
           backend rather than replaced. 2026-07-29 - max_items_per_po /
           table_budget_mm retired with the PO split; the row model + signature
           reserve are what still matter. */
        const partial = {
            prefix:      root.querySelector("#po_prefix").value.trim() || "A",
            layout: poLayoutPatch(root),
            brand_color: colorOk ? hex.toUpperCase() : "#002060",
            // Company classification for the "Deliver & Invoice" To-Do. The whole
            // map is sent; empty → the key is popped and the list re-seeds from
            // POs on the next read.
            company_tags: tagState,
            company: {
                name_ar_lines:      splitLines(root.querySelector("#po_name_ar").value),
                name_en_lines:      splitLines(root.querySelector("#po_name_en").value),
                title_ar:           root.querySelector("#po_title_ar").value.trim(),
                submitter_label_ar: root.querySelector("#po_submitter_label").value.trim(),
                submitter_name_ar:  root.querySelector("#po_submitter_name").value.trim(),
                manager_label_ar:   root.querySelector("#po_manager_label").value.trim(),
                manager_name_ar:    root.querySelector("#po_manager_name").value.trim(),
            },
        };
        const r = await api().save_doc_settings("po", partial);
        if (!r.ok) { toast("خطأ في الحفظ: " + r.error, true); return; }
        toast("تم حفظ إعدادات PO");
        close();
    }, "Save PO settings");
}
window.openPoSettings = openPoSettings;
