/* =========================================================
   MASTER DATA (2026-08-11, renamed from "Item Master" 2026-08-13)
   ---------------------------------------------------------
   Replaces "Autofill Suggestions", which was thirteen list views over seven
   tables in which the SAME physical item appeared up to four times - once per
   document type, plus one row per differing (unit, description) combination.
   On the live catalog that is ~3,000 rows describing ~1,000 real items.

   This is ONE row per item. Editing here fans out to every underlying row, so
   a unit fixed once is fixed for the quotation, the delivery note and the
   invoice together. Backend: backend/master_data.py.

   Design rules, deliberately the same ones the Insights view follows:
     · ONE filter row above everything it scopes.
     · Refetch dims the previous render - never a skeleton flash.
     · Colour never carries meaning alone (the user is colour blind): every
       condition chip and every warning is a WORD, with the count beside it.
     · Nothing here touches saved documents. Every destructive action says so.
   ========================================================= */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const api = () => window.pywebview.api;
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));

    const state = {
        search: "", sort: "used", desc: null,
        missing: "",             // "" | name_ar | name_en | unit | price | cost | image
        rows: [], total: 0, allTotal: 0, missingCounts: {},
        selected: null,          // the open item_id, as a string
        checked: new Set(),      // bulk selection (item_ids, as strings)
        loading: false,
        seq: 0,
    };

    // ---------------------------------------------------------------- utils
    /* THERE IS NO EXCHANGE RATE ON THIS SCREEN, deliberately. Both the price
       and the cost are stored in the unit they were typed in, and a document
       converts them at ITS OWN rate - the one pinned under its table, or the
       cash rate. A converter here would have to invent a rate, which is
       exactly the bug this screen used to have: 1000 IQD typed under a 1400
       profile was stored as $0.71 and read back under a 1500 profile as 1071.
       `moneyRaw` prints an amount in the unit it already is; nothing else. */
    /* Print an amount in the unit it IS, with no conversion. The cost is
       stored as typed, so there is no rate to apply here - and applying one
       would be the bug this whole change removes. */
    function moneyRaw(n, ccy) {
        if (n == null || !isFinite(n)) return "-";
        const v = Number(n);
        if (String(ccy).toUpperCase() === "IQD") {
            return "IQD " + Math.round(v).toLocaleString();
        }
        return "$" + v.toLocaleString(undefined,
            { minimumFractionDigits: v % 1 ? 2 : 0, maximumFractionDigits: 2 });
    }
    function num(n) { return (Number(n) || 0).toLocaleString(); }
    function ago(days) {
        if (days == null) return "-";
        if (days <= 0) return "today";
        if (days === 1) return "yesterday";
        if (days < 60) return days + " days ago";
        const m = Math.round(days / 30);
        return m < 24 ? m + " months ago" : Math.round(days / 365) + " years ago";
    }
    const toast = (m, bad) => (window.toast ? window.toast(m, bad) : null);

    // ------------------------------------------------------------ nav entry
    async function showItemMaster() {
        document.querySelectorAll(".view").forEach((v) => { v.hidden = true; });
        ["poStrip", "poActions", "quoteActions", "dnActions", "invActions"]
            .forEach((id) => { const el = $(id); if (el) el.hidden = true; });
        document.body.classList.remove("on-dashboard", "has-rail");
        const rail = $("typeRail"); if (rail) rail.hidden = true;
        if (window.OpenPanel) window.OpenPanel.hide();

        $("itemMasterView").hidden = false;
        const home = $("btnHome"); if (home) home.hidden = false;
        if (window.setBrand) window.setBrand("Master Data");
        if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
        await load();
    }
    window.showItemMaster = showItemMaster;

    // ------------------------------------------------------------ data
    async function load() {
        const seq = ++state.seq;
        const list = $("imList");
        if (list) {
            list.classList.add("is-loading");
            list.setAttribute("aria-busy", "true");
        }
        let r = null;
        try {
            /* THE RECORD, not the four document catalogs (2026-08-16).
               `mi_list` returns ONE row per item carrying the eight fields the
               screen shows - number, English name, Arabic name, description,
               unit, price, cost, picture. No sources, no variants, no folding,
               because there is only one row to begin with. */
            /* `desc` is a NEW argument (2026-08-26). F5 without a restart
               pairs new JS with old Python, where the extra argument throws -
               so fall back to the five-argument call rather than leaving the
               screen empty. */
            try {
                r = await api().mi_list(state.search, state.sort, 400, 0,
                                        state.missing || "", state.desc);
            } catch (e) {
                r = await api().mi_list(state.search, state.sort, 400, 0, state.missing || "");
            }
        } catch (e) { r = null; }
        if (seq !== state.seq) return;             // a newer keystroke won
        if (list) {
            list.classList.remove("is-loading");
            list.setAttribute("aria-busy", "false");
        }
        if (!r || !r.ok) {
            state.rows = [];
            renderList();
            toast("Couldn't load Master Data", true);
            return;
        }
        state.rows = r.rows || [];
        state.total = r.total || 0;
        state.allTotal = r.all_total || 0;
        state.missingCounts = r.missing_counts || {};
        render();
        /* Filtering changes the list silently for a screen reader. The count is
           on screen already; this says it out loud, politely. */
        const status = $("imStatus");
        if (status) {
            status.textContent = state.total === state.allTotal
                ? `${num(state.allTotal)} items`
                : `${num(state.total)} of ${num(state.allTotal)} items match`;
        }
    }

    // ------------------------------------------------------------ chrome
    /* The old chrome filtered by DOCUMENT TYPE - "Purchase Order", "quotation
       materials", "quotation manpower". Those pills existed only because one
       item was stored four times, once per catalog. There is one row per item
       now, so they say nothing and are gone.

       What is worth filtering by is what an item is still MISSING: that is how
       the catalog actually gets finished off. */
    const MISSING_CHIPS = [
        ["name_ar", "No Arabic name"],
        ["name_en", "No English name"],
        ["unit", "No unit"],
        /* Asked only of items that HAVE an Arabic name - an item that has never
           been on a PO does not need an Arabic unit yet. */
        ["unit_ar", "No Arabic unit"],
        ["price", "No price"],
        ["cost", "No cost"],
        ["image", "No picture"],
        /* Not a gap - the opposite. Items that look like the SAME product
           saved twice, matched on name + description + unit with spacing
           ignored: `CHICKEN BREAST` against `CHICKENBREAST`. Tick two and use
           Merge. Replaces the separate "Review materials" panel, which could
           only look inside one document type at a time. */
        ["duplicate", "Possible duplicates"],
    ];

    function renderChrome() {
        const c = $("imCount");
        if (c) {
            c.textContent = state.total === state.allTotal
                ? `${num(state.allTotal)} items`
                : `${num(state.total)} of ${num(state.allTotal)} items`;
        }
        const src = $("imSources");
        if (src) src.innerHTML = "";
        const fl = $("imFlags");
        if (fl) {
            const chips = [`<button type="button" class="im-chip${state.missing ? "" : " active"}" data-miss="">All items</button>`];
            MISSING_CHIPS.forEach(([key, label]) => {
                const n = (state.missingCounts || {})[key] || 0;
                if (!n) return;
                const why = key === "duplicate"
                    ? "Items that look like the same product stored twice - tick two and Merge"
                    : "Show only items with this still to fill in";
                chips.push(`<button type="button" class="im-chip im-flagchip${state.missing === key ? " active" : ""}"`
                    + ` data-miss="${esc(key)}" title="${esc(why)}">`
                    + `${esc(label)}<em>${num(n)}</em></button>`);
            });
            fl.innerHTML = chips.join("");
            fl.querySelectorAll("[data-miss]").forEach((b) => {
                b.onclick = () => {
                    state.missing = b.dataset.miss || "";
                    state.checked.clear();
                    load();
                };
            });
        }
    }

    // ------------------------------------------------------------ list
    /* The "has a picture" marker. Was a geometric text glyph, font-dependent
       and disagreed with the autocomplete dropdown's marker; this is the same
       SVG that `.ac-img-tag` uses, so the two screens say it the same way.
       `aria-label` carries the meaning - `title` alone is not announced. */
    const PIC_SVG = `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">`
        + `<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>`;
    const PIC_ICON = `<span class="im-haspic" role="img" aria-label="Has a picture"`
        + ` title="A picture is remembered for this item">` + PIC_SVG + `</span>`;

    /* A description and a unit exist ONCE PER LANGUAGE, exactly as the names do
       - the PO says كيلو and the invoice says KG for the same kilogram. Two more
       columns would not fit, so each cell stacks the two: English on top, Arabic
       under it. A blank side prints nothing rather than a dash on every row. */
    function bilingual(en, ar) {
        const e = String(en || "").trim(), a = String(ar || "").trim();
        return (e ? `<span class="im-b-en">${esc(e)}</span>` : "")
             + (a ? `<span class="im-b-ar" dir="auto">${esc(a)}</span>` : "");
    }

    function bothTitle(en, ar) {
        return [String(en || "").trim(), String(ar || "").trim()]
            .filter(Boolean).join("  ·  ");
    }

    function renderList() {
        const host = $("imList");
        if (!host) return;
        if (!state.rows.length) {
            host.innerHTML = `<div class="im-empty">`
                + (state.search || state.missing
                    ? "Nothing matches those filters."
                    : "No items remembered yet. They appear here as you save documents.")
                + `</div>`;
            renderBulk();
            return;
        }
        /* Column headers double as sort controls. `aria-sort` is the standard
           way to say which column is sorting; the arrow beside the label is a
           redundant cue, never the only one. Columns with no sort key stay
           plain text rather than pretending to be interactive. */
        /* Waleed's eight fields, in his order: number, both names, description,
           unit, price, cost, picture. Nothing else - "nothing more" was the
           spec, and the old Margin / Used-in / Uses / Last-used columns were
           facts about DOCUMENTS, not about the item. */
        /* EVERY column the reader can read, they can order by (2026-08-26).
           Four of the eight used to be plain text, and the one column that did
           look sortable - No. - was the one the reader most often wanted.
           Clicking the column already sorting FLIPS it; the arrow says which
           way, and `aria-sort` says the same thing to a screen reader. */
        const SORTABLE = {
            no: "number", nameen: "name", namear: "name_ar", desc: "desc",
            unit: "unit", price: "price", cost: "cost", pic: "picture",
        };
        // Which way a first click points: text A→Z, numbers and counts biggest
        // first - mirrors _SORT_SPEC in master_items.py.
        const SORT_DEFAULT_DESC = { price: true, cost: true, picture: true, used: true, recent: true };
        const sortIsDesc = (key) => (state.sort === key && state.desc !== null)
            ? state.desc : !!SORT_DEFAULT_DESC[key];
        const th = (cls, label, key) => {
            if (!key) return `<span class="im-c-${cls}" role="columnheader">${label}</span>`;
            const on = state.sort === key;
            const down = sortIsDesc(key);
            const plain = String(label).replace(/<[^>]*>/g, " ").trim().toLowerCase();
            return `<span class="im-c-${cls}" role="columnheader"`
                 + `${on ? ` aria-sort="${down ? "descending" : "ascending"}"` : ""}>`
                 + `<button type="button" class="im-sortbtn${on ? " is-on" : ""}" data-sort="${key}"`
                 + ` title="${on ? `Sorted by ${plain}, ${down ? "highest first" : "lowest first"} - click to reverse`
                                 : `Sort by ${plain}`}">${label}`
                 + `${on ? `<span class="im-sort-arrow" aria-hidden="true">${down ? "▼" : "▲"}</span>` : ""}`
                 + `</button></span>`;
        };
        const head = `<div class="im-row im-row-head im-row-rec" role="row">
            <span class="im-c-check" role="columnheader"><span class="im-sr-only">Select</span></span>
            ${th("no", "No.", SORTABLE.no)}
            ${th("nameen", "Name (English)", SORTABLE.nameen)}
            ${th("namear", "الاسم بالعربي", SORTABLE.namear)}
            ${th("desc", "Description<small>EN / عربي</small>", SORTABLE.desc)}
            ${th("unit", "Unit<small>EN / عربي</small>", SORTABLE.unit)}
            ${th("money", "Price", SORTABLE.price)}
            ${th("money", "Cost", SORTABLE.cost)}
            ${th("pic", "Picture", SORTABLE.pic)}
        </div>`;
        /* In the duplicates view each copy sits directly under the record it
           duplicates, with a divider around the pair (2026-08-26). Before this
           the 66 flagged records were just filtered into the normal list in
           use-count order, so the two `Chest Freezer` rows sat five apart and
           the reader had to hunt for the pair - "instead of looking around".
           The backend does the grouping (`_group_duplicates`); this only draws
           it. */
        const grouped = state.missing === "duplicate";
        let lastGroup = null;
        const rows = state.rows.map((i) => {
            const id = String(i.item_id);
            const label = i.name_en || i.name_ar || "(no name)";
            const miss = (v) => v ? "" : " im-c-missing";
            let sep = "";
            let dupeCls = "";
            let dupeTag = "";
            if (grouped && i.dupe_group) {
                if (i.dupe_group !== lastGroup) {
                    lastGroup = i.dupe_group;
                    sep = `<div class="im-dupe-sep" role="presentation">
                        <span class="im-dupe-name">${esc(i.dupe_name || label)}</span>
                        <span class="im-dupe-count">${num(i.dupe_size)} records</span>
                    </div>`;
                }
                const copy = i.dupe_role === "copy";
                dupeCls = copy ? " is-dupe-copy" : " is-dupe-primary";
                // The word, never the indent alone: a reader who cannot see
                // the tint still has to know which one the others copy.
                dupeTag = copy
                    ? `<span class="im-dupe-tag is-copy" title="Same name, description and unit as ${esc(i.dupe_of)}">Duplicate of ${esc(i.dupe_of)}</span>`
                    : `<span class="im-dupe-tag">Original</span>`;
            }
            return sep + `<div class="im-row im-row-rec${dupeCls}${state.selected === id ? " is-open" : ""}" data-id="${esc(id)}" tabindex="0" role="row" aria-selected="${state.selected === id}">
                <span class="im-c-check" role="cell"><input type="checkbox" ${state.checked.has(id) ? "checked" : ""} data-check="${esc(id)}" aria-label="Select ${esc(label)}"></span>
                <span class="im-c-no" role="cell" title="Item number - never changes, even if the item is renamed">${esc(i.item_no || "-")}</span>
                <span class="im-c-nameen${miss(i.name_en)}" role="cell" title="${esc(i.name_en)}">${esc(i.name_en || "-")}${dupeTag}</span>
                <span class="im-c-namear${miss(i.name_ar)}" role="cell" dir="auto" title="${esc(i.name_ar)}">${esc(i.name_ar || "-")}</span>
                <span class="im-c-desc" role="cell" title="${esc(bothTitle(i.description_en, i.description_ar))}">${bilingual(i.description_en, i.description_ar)}</span>
                <span class="im-c-unit${miss(i.unit_en || i.unit_ar)}" role="cell" title="${esc(bothTitle(i.unit_en, i.unit_ar))}">${bilingual(i.unit_en, i.unit_ar) || "-"}</span>
                <span class="im-c-money" role="cell">${moneyRaw(i.price, i.price_ccy)}</span>
                <span class="im-c-money" role="cell">${moneyRaw(i.cost, i.cost_ccy)}</span>
                <span class="im-c-pic" role="cell">${i.image_ref ? PIC_ICON : ""}</span>
            </div>`;
        }).join("");
        host.innerHTML = head + rows;
        host.querySelectorAll("[data-check]").forEach((cb) => {
            cb.onclick = (e) => {
                e.stopPropagation();
                const n = cb.dataset.check;
                if (cb.checked) state.checked.add(n); else state.checked.delete(n);
                renderBulk();
            };
        });
        host.querySelectorAll("[data-sort]").forEach((b) => {
            b.onclick = (e) => {
                e.stopPropagation();
                const key = b.dataset.sort;
                // Same column again = reverse it; a new column starts at its
                // own default rather than inheriting the last one's direction.
                state.desc = (state.sort === key) ? !sortIsDesc(key) : null;
                state.sort = key;
                const sel = $("imSort");
                if (sel) sel.value = state.sort;      // keep the dropdown in step
                load();
            };
        });
        host.querySelectorAll(".im-row[data-id]").forEach((el) => {
            const open = () => openDetail(el.dataset.id);
            el.onclick = open;
            el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); } };
        });
        renderBulk();
    }

    function renderBulk() {
        const host = $("imBulk");
        if (!host) return;
        const n = state.checked.size;
        host.hidden = n === 0;
        if (!n) return;
        host.innerHTML = `<span><b>${n}</b> selected</span>`
            + `<button type="button" class="im-btn" data-bulk="merge">Merge into one…</button>`
            + `<button type="button" class="im-btn im-btn-danger" data-bulk="delete">Forget selected</button>`
            + `<button type="button" class="im-btn im-btn-ghost" data-bulk="clear">Clear</button>`;
        host.querySelector('[data-bulk="clear"]').onclick = () => { state.checked.clear(); renderList(); };
        host.querySelector('[data-bulk="merge"]').onclick = bulkMerge;
        host.querySelector('[data-bulk="delete"]').onclick = bulkDelete;
    }

    async function bulkMerge() {
        const names = [...state.checked];
        if (names.length < 2) { toast("Pick at least two items to merge", true); return; }
        const canonical = await pickCanonical(names);
        if (!canonical) return;
        const r = await api().mi_merge(canonical, names.filter((n) => n !== canonical));
        if (r && r.ok) {
            toast(`Merged ${r.merged} item${r.merged === 1 ? "" : "s"} into "${(r.item && (r.item.name_en || r.item.name_ar)) || labelFor(canonical)}"`);
            state.checked.clear();
            closeDetail();
            load();
        } else toast((r && r.error) || "Merge failed", true);
    }

    async function bulkDelete() {
        const names = [...state.checked];
        const ok = await confirmBox(
            `Forget ${names.length} item${names.length === 1 ? "" : "s"}?`,
            `They stop appearing in autocomplete. <b>Saved documents are not touched</b> - nothing you have already issued changes.`,
            "Forget");
        if (!ok) return;
        const r = await api().mi_delete(names);
        if (r && r.ok) {
            toast(`Forgot ${r.deleted} item${r.deleted === 1 ? "" : "s"}`);
            state.checked.clear();
            closeDetail();
            load();
        } else toast((r && r.error) || "Nothing was removed", true);
    }

    /* ---------------------------------------------------------------- push
       Writing a master cost back into documents already saved.

       Deliberately a THREE-step action - ask, choose, confirm - because it
       rewrites records. Documents that have already been submitted are listed
       but start UNTICKED: correcting a cost you never entered is routine,
       quietly restating one on a document you have already sent is not.

       Costs are private and never printed, so nothing here changes a PDF a
       client has seen. */
    /* TWO LINES, not six columns. The panel is ~330-400px wide, and a
       six-column grid squeezed the money column to nothing: "18000 IQD →
       19286 IQD" wrapped into a tall stack and the "submitted" badge was
       clipped off the right edge. Identity on top, detail underneath - it
       reads at any panel width and needs no horizontal scroll. */
    /* ---------------------------------------------------- where it is used
       RESTORED 2026-08-16. Removing this with the rest of the old panel was a
       mistake: Waleed asked for it two days ago - "make the docs that show
       there clickable ... it should show a popup first and then a button to go
       for document". His "nothing more" was about what the RECORD STORES, not
       about dropping a lookup. It is not item data; it is a question asked of
       the documents, keyed by either of the item's names. */
    const DOC_ROUTE = {
        po:    () => window.loadForEdit,
        quote: () => window.loadQuoteForEdit,
        dn:    () => window.loadDnForEdit,
        inv:   () => window.loadInvForEdit,
    };

    function usedInRowHtml(d) {
        const when = d.date || `${d.month || ""}/${d.year || ""}`;
        return `<button type="button" class="im-doc" data-doc-kind="${esc(d.kind)}"
                        data-doc-no="${esc(d.no)}" data-doc-i="${esc(String(d._i))}">
            <span class="im-doc-top">
                <span class="im-pill">${esc(d.kind_label)}</span>
                <span class="im-doc-no">${esc(d.no)}</span>
                ${d.submitted ? `<span class="im-cp-sub">submitted</span>` : ""}
            </span>
            <span class="im-doc-meta">
                <span>${esc(when)}</span>
                ${d.client ? `<span>${esc(d.client)}</span>` : ""}
                ${d.line_count > 1 ? `<span>${num(d.line_count)} lines</span>` : ""}
            </span>
        </button>`;
    }

    async function loadUsedIn(name) {
        const host = $("imUsedIn");
        if (!host) return;
        let r = null;
        try { r = await api().im_item_documents(name); } catch (e) { r = null; }
        if (!host.isConnected || host.dataset.item !== name) return;   // panel moved on
        if (!r || !r.ok) {
            host.innerHTML = `<span class="im-docs-empty">${esc((r && r.error)
                || "Could not read the documents for this item.")}</span>`;
            return;
        }
        const docs = (r.documents || []).map((d, i) => ({ ...d, _i: i }));
        if (!docs.length) {
            host.innerHTML = `<span class="im-docs-empty">No saved document in this `
                + `company holds this item yet.</span>`;
            return;
        }
        const shown = docs.slice(0, 8);
        host.innerHTML = shown.map(usedInRowHtml).join("")
            + (docs.length > shown.length
                ? `<span class="im-docs-empty">…and ${num(docs.length - shown.length)} more.</span>`
                : "");
        host.querySelectorAll(".im-doc").forEach((btn) => {
            btn.onclick = () => showDocumentPeek(docs[Number(btn.dataset.docI)], name);
        });
    }

    /* Look before you leap: the popup says what this item is doing in that
       document - quantity, unit, rate - so the common question is answered
       without leaving Master Data at all. Opening the document is a second,
       explicit step, because it abandons the panel the user is working in. */
    async function showDocumentPeek(d, itemName) {
        const lines = (d.lines || []).map((l) => `<tr>
            <td>${esc(l.description || "-")}</td>
            <td>${esc(l.unit || "-")}</td>
            <td class="im-peek-num">${esc(l.qty || "-")}</td>
            <td class="im-peek-num">${esc(l.rate || "-")}</td>
        </tr>`).join("");
        const body = `<div class="im-peek">
            <div class="im-peek-head">
                <span class="im-pill">${esc(d.kind_label)}</span>
                <b>${esc(d.no)}</b>
                ${d.submitted ? `<span class="im-cp-sub">submitted</span>` : ""}
            </div>
            <div class="im-peek-meta">
                <span>${esc(d.date || "-")}</span>
                ${d.client ? `<span>${esc(d.client)}</span>` : ""}
            </div>
            <p class="im-peek-lead"><b>${esc(itemName)}</b> appears
                ${d.line_count === 1 ? "once" : `${num(d.line_count)} times`} here:</p>
            <table class="im-peek-table">
                <thead><tr><th>Description</th><th>Unit</th><th>Qty</th><th>Rate</th></tr></thead>
                <tbody>${lines}</tbody>
            </table>
        </div>`;
        const canOpen = typeof (DOC_ROUTE[d.kind] || (() => null))() === "function";
        const buttons = [{ label: "Close", value: "close", cls: "ghost" }];
        if (canOpen) buttons.push({ label: "Open document", value: "open", cls: "primary" });
        const choice = await window.showModal({
            title: "Where this item is used", body, html: true, buttons,
        });
        if (choice !== "open") return;
        const fn = (DOC_ROUTE[d.kind] || (() => null))();
        if (typeof fn !== "function") { toast(`Cannot open a ${d.kind_label}`, true); return; }
        try { await fn(d.no); }
        catch (e) { toast(`Could not open ${d.no}: ${(e && e.message) || e}`, true); }
    }


    async function openDetail(itemId) {
        state.selected = String(itemId);
        renderList();
        const host = $("imDetail");
        if (!host) return;
        host.hidden = false;
        host.innerHTML = `<div class="im-empty">Loading…</div>`;
        let d = null;
        try { d = await api().mi_get(itemId); } catch (e) { d = null; }
        if (state.selected !== String(itemId)) return;
        if (!d || !d.ok) {
            host.innerHTML = `<div class="im-empty">${esc((d && d.error) || "Couldn't load this item.")}</div>`;
            return;
        }
        renderRecord(d.item);
    }

    /* Returning focus to the row that opened the panel - otherwise closing
       drops the keyboard user back at the top of the document. */
    function focusRow(id) {
        const el = document.querySelector(`.im-row[data-id="${String(id || "").replace(/"/g, '\\"')}"]`);
        if (el) el.focus({ preventScroll: true });
    }

    function closeDetail() {
        const was = state.selected;
        state.selected = null;
        const host = $("imDetail");
        if (host) { host.hidden = true; host.innerHTML = ""; }
        renderList();               // drop the is-open highlight
        focusRow(was);
    }

    /* ---------------------------------------------------------------- panel
       THE item, and everything it is. Waleed's fields, in his order, all
       editable in place:

           number (read-only) · name EN · name AR · description EN · description AR
           · unit EN · unit AR · price + its currency · cost + its currency
           · picture

       Description and unit are PER LANGUAGE (2026-08-17), because they are: the
       PO is written in Arabic and every other document in English, and كيلو and
       KG are the same kilogram of the same product. One box each kept whichever
       language it met first and dropped the other.

       No "used in", no variant list, no source badges. Those described where
       an item had been COPIED to, which only mattered while it lived in four
       tables at once. */
    function moneyCcyChip(field, active) {
        return ["USD", "IQD"].map((c) =>
            `<button type="button" class="im-ccy${active === c ? " active" : ""}"`
            + ` data-ccy="${field}:${c}">${c}</button>`).join("");
    }

    /* An item entered BY HAND, before any document has used it. Master Data is
       a master, not a report of what the documents happened to save -
       "we should also be able to fill it by hand by the way, its a master data
       at the end." Same panel, same fields; only where it saves to differs. */
    function openNewItem() {
        state.selected = null;
        const host = $("imDetail");
        if (!host) return;
        host.hidden = false;
        renderList();
        renderRecord({
            item_id: null, item_no: "", name_en: "", name_ar: "",
            description_en: "", description_ar: "", unit_en: "", unit_ar: "",
            price: null, price_ccy: "USD", cost: null, cost_ccy: "USD",
            image_ref: "",
        }, true);
        const first = host.querySelector('input[name="name_en"]');
        if (first) first.focus();
    }

    function renderRecord(it, isNew) {
        const host = $("imDetail");
        if (!host) return;
        const priceCcy = it.price_ccy || "USD";
        const costCcy = it.cost_ccy || "USD";
        const val = (v) => (v == null ? "" : String(v));
        host.innerHTML = `
          <div class="im-d-head">
            <div class="im-d-title">
              <span class="im-d-no" title="${isNew ? "The number is given when you save" : "Item number - never changes"}">${isNew ? "New" : esc(it.item_no || "-")}</span>
              <strong dir="auto">${isNew ? "New item" : esc(it.name_en || it.name_ar || "(no name)")}</strong>
            </div>
            <button type="button" class="im-d-close" id="imDClose" aria-label="Close">×</button>
          </div>
          <form class="im-d-form" id="imDForm">
            <label class="im-f">Name (English)
              <input type="text" name="name_en" value="${esc(it.name_en)}" autocomplete="off">
            </label>
            <label class="im-f">الاسم بالعربي
              <input type="text" name="name_ar" dir="auto" value="${esc(it.name_ar)}" autocomplete="off">
            </label>
            <label class="im-f">Description (English)
              <input type="text" name="description_en" value="${esc(it.description_en)}" autocomplete="off">
            </label>
            <label class="im-f">الوصف بالعربي
              <input type="text" name="description_ar" dir="auto" value="${esc(it.description_ar)}" autocomplete="off">
            </label>
            <label class="im-f">Unit (English)
              <input type="text" name="unit_en" value="${esc(it.unit_en)}" autocomplete="off">
            </label>
            <label class="im-f">الوحدة بالعربي
              <input type="text" name="unit_ar" dir="auto" value="${esc(it.unit_ar)}" autocomplete="off">
            </label>
            <div class="im-f im-f-money">
              <span class="im-f-label">Price</span>
              <div class="im-f-money-row">
                <input type="text" name="price_native" inputmode="decimal" value="${esc(val(it.price))}" autocomplete="off">
                <span class="im-ccyrow" data-for="price">${moneyCcyChip("price", priceCcy)}</span>
              </div>
            </div>
            <div class="im-f im-f-money">
              <span class="im-f-label">Cost</span>
              <div class="im-f-money-row">
                <input type="text" name="cost_native" inputmode="decimal" value="${esc(val(it.cost))}" autocomplete="off">
                <span class="im-ccyrow" data-for="cost">${moneyCcyChip("cost", costCcy)}</span>
              </div>
            </div>
            <div class="im-f im-f-pic">
              <span class="im-f-label">Picture</span>
              <div class="im-d-pic" id="imDPic">${it.image_ref ? "Loading…" : "<em>none</em>"}</div>
            </div>
            <div class="im-d-actions">
              <button type="submit" class="primary">${isNew ? "Add item" : "Save"}</button>
              <span class="im-d-saved" id="imDSaved" role="status" aria-live="polite"></span>
            </div>
          </form>
          ${isNew ? "" : `<div class="im-d-used">
            <h4 class="im-d-sub">Used in</h4>
            <div id="imUsedIn" class="im-usedin"><em>Loading…</em></div>
          </div>`}`;

        /* Move focus INTO the panel. Without this a keyboard user opens an
           item and is still standing in the list, with no way to know the
           panel appeared. `tabindex="-1"` makes the heading focusable without
           putting it in the tab order. */
        const head = host.querySelector(".im-d-title strong");
        if (head) { head.setAttribute("tabindex", "-1"); head.focus({ preventScroll: true }); }

        let pc = priceCcy, cc = costCcy;
        host.querySelectorAll("[data-ccy]").forEach((b) => {
            b.onclick = (e) => {
                e.preventDefault();
                const [field, ccy] = b.dataset.ccy.split(":");
                if (field === "price") pc = ccy; else cc = ccy;
                host.querySelectorAll(`.im-ccyrow[data-for="${field}"] .im-ccy`)
                    .forEach((x) => x.classList.toggle("active", x.dataset.ccy === `${field}:${ccy}`));
            };
        });
        const close = $("imDClose");
        if (close) close.onclick = closeDetail;

        const form = $("imDForm");
        form.onsubmit = async (e) => {
            e.preventDefault();
            const f = new FormData(form);
            const patch = {
                name_en: (f.get("name_en") || "").trim(),
                name_ar: (f.get("name_ar") || "").trim(),
                description_en: (f.get("description_en") || "").trim(),
                description_ar: (f.get("description_ar") || "").trim(),
                unit_en: (f.get("unit_en") || "").trim(),
                unit_ar: (f.get("unit_ar") || "").trim(),
                price_native: (f.get("price_native") || "").trim(),
                price_native_ccy: pc,
                cost_native: (f.get("cost_native") || "").trim(),
                cost_native_ccy: cc,
            };
            let r = null;
            try {
                r = isNew ? await api().mi_create(patch)
                          : await api().mi_save(it.item_id, patch);
            } catch (err) { r = null; }
            if (!r || !r.ok) {
                /* An item already on file is not a failure to shout about -
                   say which one it is and offer to go there, because that is
                   what the person was looking for. */
                if (r && r.already) {
                    toast(r.error, true);
                    await load();
                    openDetail(r.already.item_id);
                    return;
                }
                toast((r && r.error) || "Couldn't save", true);
                return;
            }
            const saved = $("imDSaved");
            if (saved) {
                saved.textContent = isNew ? `Added as ${r.item.item_no}` : "Saved";
                setTimeout(() => { saved.textContent = ""; }, 2200);
            }
            /* Re-read the LIST so the row reflects what was just typed - the
               record is the only copy now, so there is nothing else to keep in
               step. */
            await load();
            if (isNew) { openDetail(r.item.item_id); return; }
            renderRecord(r.item);
        };

        if (isNew) return;
        if (it.image_ref) loadRecordPicture(it.item_id);
        /* Documents store one NAME, not the record id, so ask with whichever
           name they would have used. */
        loadUsedIn(it.name_en || it.name_ar);
    }

    async function loadRecordPicture(itemId) {
        const box = $("imDPic");
        if (!box) return;
        let uri = "";
        try { uri = await api().mi_image(itemId); } catch (e) { uri = ""; }
        if (!box.isConnected) return;
        box.innerHTML = uri
            ? `<img src="${esc(uri)}" alt="" loading="lazy">`
            : "<em>none</em>";
    }

    async function confirmBox(title, body, okLabel) {
        if (!window.showModal) return window.confirm(title);
        const choice = await window.showModal({
            title, body: `<p>${body}</p>`, html: true,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: okLabel || "OK", value: "ok", cls: "warm-red" },
            ],
        });
        return choice === "ok";
    }

    /* The selection is item_ids now, so the picker has to show the NAMES -
       a list of numbers is not a choice anyone can make. */
    function labelFor(id) {
        const r = state.rows.find((x) => String(x.item_id) === String(id));
        if (!r) return String(id);
        const parts = [r.name_en, r.name_ar].filter(Boolean).join("  ·  ");
        return (r.item_no ? r.item_no + "  " : "") + (parts || "(no name)")
             + (r.unit ? "  [" + r.unit + "]" : "");
    }

    async function pickCanonical(names) {
        if (!window.showModal) return names[0];
        const body = `<p>Which item should survive? The others fold into it - it keeps everything it already has and takes anything it is missing, and their use counts are added together.</p>`
            + `<div class="im-pick">${names.map((n, i) =>
                `<label><input type="radio" name="imcanon" value="${esc(n)}" ${i === 0 ? "checked" : ""}> <span dir="auto">${esc(labelFor(n))}</span></label>`).join("")}</div>`;
        const choice = await window.showModal({
            title: "Merge items", body, html: true,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Merge", value: "ok", cls: "primary" },
            ],
        });
        if (choice !== "ok") return null;
        const sel = document.querySelector('input[name="imcanon"]:checked');
        return sel ? sel.value : names[0];
    }

    // ------------------------------------------------ material duplicate review
    function defaultMaterialDuplicateSelections(plan) {
        return ((plan && plan.groups) || []).map((group) => {
            const rows = Array.isArray(group.rows) ? group.rows : [];
            const recommended = rows.some((row) => row.id === group.recommended_canonical_id)
                ? group.recommended_canonical_id : (rows[0] && rows[0].id) || "";
            return { group_id: group.id || "", canonical_id: recommended,
                selected: group.category === "ready" };
        }).filter((choice) => choice.group_id && choice.canonical_id);
    }
    // Kept deliberately pure so the default-safety rule is testable without a
    // browser harness: ready groups start checked; conflicts never do.
    window.__itemMasterDuplicateReviewInternals = { defaultMaterialDuplicateSelections };

    function reviewMoney(value) {
        return value && value.amount != null ? moneyRaw(value.amount, value.currency) : "-";
    }
    function reviewRowHtml(row, groupId, selectedId) {
        const title = row.item_no ? `${row.name} · ${row.item_no}` : row.name;
        const picture = row.image_count ? `${num(row.image_count)} picture${row.image_count === 1 ? "" : "s"}` : "no picture";
        const custom = row.custom_count ? `${num(row.custom_count)} custom field${row.custom_count === 1 ? "" : "s"}` : "no custom data";
        return `<label class="im-dup-choice">
            <input type="radio" data-imdr-canonical name="imdr-${esc(groupId)}" value="${esc(row.id)}" ${row.id === selectedId ? "checked" : ""}>
            <span class="im-dup-choice-main"><b>${esc(title)}</b>
                ${row.description ? `<i>${esc(row.description)}</i>` : ""}
                <small>Unit: ${esc(row.unit || "-")} · Price: ${esc(reviewMoney(row.price))} · Cost: ${esc(reviewMoney(row.cost))}</small>
            </span>
            <span class="im-dup-choice-side">Used ${num(row.uses)}×<small>${esc(picture)} · ${esc(custom)}</small></span>
        </label>`;
    }
    function reviewGroupHtml(group, choice) {
        const conflict = (group.conflicts || []).map((item) =>
            `<li><b>${esc(item.label || item.field)}</b>: ${esc((item.values || []).join(" / "))}</li>`).join("");
        const isReady = group.category === "ready";
        return `<section class="im-dup-group ${isReady ? "is-ready" : "is-review"}" data-imdr-group="${esc(group.id)}">
            <div class="im-dup-head">
                <label class="im-dup-toggle"><input type="checkbox" data-imdr-check value="${esc(group.id)}" ${choice.selected ? "checked" : ""}>
                    <span>${isReady ? "Ready to merge" : "Needs review"}</span></label>
                <span class="im-src">${esc(group.source_label || group.source)}</span>
            </div>
            <p class="im-dup-reason">${esc(group.reason || "Review these material rows before merging.")}</p>
            ${conflict ? `<ul class="im-dup-conflicts">${conflict}</ul>` : ""}
            <div class="im-dup-choices">${(group.rows || []).map((row) =>
                reviewRowHtml(row, group.id, choice.canonical_id)).join("")}</div>
        </section>`;
    }
    function materialDuplicateReviewBody(plan, choices) {
        const groups = (plan && plan.groups) || [];
        if (!groups.length) {
            return `<div class="im-dup im-dup-empty"><b>No material duplicates need action.</b><p>The check only combines exact spacing/case equivalents; similar products remain separate.</p></div>`;
        }
        const ready = (plan.summary || {}).ready || 0;
        const review = (plan.summary || {}).review || 0;
        const chosen = new Map(choices.map((choice) => [choice.group_id, choice]));
        return `<div class="im-dup">
            <p class="im-dup-lead"><b>${num(ready)}</b> safe group${ready === 1 ? "" : "s"} start selected. ${review ? `<b>${num(review)}</b> group${review === 1 ? "" : "s"} need your decision.` : "All detected rows agree."} Saved documents are never changed; a library backup is created first.</p>
            <p class="im-dup-lead im-dup-scope">Only rows whose <b>description and unit already match</b> are grouped. Different sizes, colours or pack units stay separate - three colours of one pen are three products, not a duplicate.</p>
            <div class="im-dup-list">${groups.map((group) => reviewGroupHtml(group, chosen.get(group.id) || {
                group_id: group.id, canonical_id: group.recommended_canonical_id, selected: false,
            })).join("")}</div>
            <div class="im-dup-actions"><button type="button" class="im-btn im-btn-primary" id="imDupApply">Merge selected groups</button></div>
        </div>`;
    }
    function dismissCurrentModal() {
        const actions = document.getElementById("modalActions");
        const close = actions && actions.querySelector("button");
        if (close) close.click();
    }

    // ------------------------------------------------------------ health
    async function openHealth() {
        let h = null;
        try { h = await api().im_health(); } catch (e) { h = null; }
        if (!h || !h.ok) { toast("Couldn't read catalog health", true); return; }
        const img = h.images || {};
        const line = (label, count, hint, flag) =>
            `<div class="im-h-row"><b>${num(count)}</b><span>${esc(label)}</span>`
            + `<i>${esc(hint)}</i>`
            + (flag && count ? `<button type="button" class="im-btn im-btn-ghost im-btn-xs" data-goto="${esc(flag)}">Show</button>` : "")
            + `</div>`;
        /* The lead used to read "800 items stored as 3,000 rows across 4
           catalogs", which answered "how much is stored" - a fact about the
           old storage, not about the catalogue. It reads the RECORD now
           (2026-08-19), so the panel answers "how much is FINISHED", and every
           line is a link into the work: Show puts exactly those items in the
           list behind it. */
        const gaps = h.gaps || [];
        const done = gaps.every((g) => !g.count);
        /* The way in to the catalogue REVIEW sits here, where you already look
           for the state of the catalogue - rather than as a second button
           beside Health competing to mean the same thing. The two answer
           different questions: this panel is "what is still to fill in", the
           review is "what is WRONG and needs a decision" (2026-09-10). */
        let problems = 0;
        try { problems = window.MasterDoctor ? await window.MasterDoctor.count() : 0; }
        catch (e) { problems = 0; }
        const review = problems
            ? `<div class="im-h-row im-h-review"><b>${num(problems)}</b>
                 <span>problems needing a decision</span>
                 <i>one product stored twice, variants nothing can tell apart, a
                    date typed into a description - none of them a blank field,
                    so no chip above can show them</i>
                 <button type="button" class="im-btn im-btn-primary im-btn-xs"
                         id="imReviewBtn">Review</button></div>`
            : "";
        const body = `
            ${review}
            <div class="im-h-lead"><b>${num(h.items)}</b> items.
              ${done ? "Nothing missing - every one has both names, a unit, a price, a cost and a picture."
                     : "What is still to fill in:"}</div>
            ${gaps.filter((g) => g.count).map((g) =>
                line(g.label, g.count, g.hint || "", g.key)).join("")}
            <h4 class="im-d-sub">Pictures</h4>
            <div class="im-h-row" id="imBfRow"><b>-</b><span>recoverable from saved documents</span>
                <i>pictures already inside quotations and POs that the catalog never learned</i>
                <button type="button" class="im-btn im-btn-xs" id="imBackfill">Check</button></div>
            <div class="im-h-row"><b>${num(img.files)}</b><span>files in the store</span><i>${num(Math.round((img.bytes || 0) / 1024))} KB, ${num(img.referenced)} still used</i></div>
            <div class="im-h-row"><b>${num(img.orphan_count)}</b><span>no longer referenced</span><i>left behind when an item lost its picture</i>
                ${img.orphan_count ? `<button type="button" class="im-btn im-btn-xs" id="imPurge">Delete them</button>` : ""}</div>
            ${img.missing_count ? `<div class="im-h-row"><b>${num(img.missing_count)}</b><span>referenced but missing</span><i>the item shows no picture; harmless</i></div>` : ""}
            <h4 class="im-d-sub">Per-cell suggestions</h4>
            <div class="im-h-row"><b>${num(h.cell_memory)}</b><span>remembered cell values</span>
                <i>every value ever typed in every column. An index, not master data - it rebuilds itself as you save.</i>
                ${h.cell_memory ? `<button type="button" class="im-btn im-btn-ghost im-btn-xs" id="imClearCells">Clear</button>` : ""}</div>`;
        if (!window.showModal) { toast(`${h.items} items`); return; }
        /* The modal resolves when it closes, so the in-body buttons have to be
           wired while it is OPEN - hence the next-tick wiring below and the
           deliberate lack of `await` on showModal itself. */
        const closed = window.showModal({
            title: "Catalog health", body: `<div class="im-health">${body}</div>`, html: true,
            buttons: [{ label: "Close", value: "ok", cls: "ghost" }],
        });
        setTimeout(() => {
            const dismiss = () => {
                const act = document.getElementById("modalActions");
                const btn = act && act.querySelector("button");
                if (btn) btn.click();
            };
            document.querySelectorAll("[data-goto]").forEach((b) => {
                b.onclick = () => { state.missing = b.dataset.goto || ""; dismiss(); load(); };
            });
            const review = $("imReviewBtn");
            if (review) review.onclick = () => {
                dismiss();
                if (window.MasterDoctor) window.MasterDoctor.open();
            };
            const purge = $("imPurge");
            if (purge) purge.onclick = async () => {
                const r = await api().im_purge_images();
                toast(r && r.ok ? `Deleted ${r.deleted} unused picture${r.deleted === 1 ? "" : "s"}`
                                : "Nothing was deleted", !(r && r.ok));
                dismiss();
            };
            /* Backfill is preview-then-commit on purpose: it rewrites
               hundreds of catalog rows, so it must never be a surprise. */
            const bf = $("imBackfill");
            if (bf) bf.onclick = async () => {
                const row = $("imBfRow");
                bf.disabled = true; bf.textContent = "Checking…";
                let r = null;
                try { r = await api().im_backfill_images(false); } catch (e) { r = null; }
                bf.disabled = false;
                if (!r || !r.ok) { bf.textContent = "Check"; toast("Couldn't scan documents", true); return; }
                row.querySelector("b").textContent = num(r.would_update);
                row.querySelector("i").textContent =
                    `${num(r.found)} found in saved documents · ${num(r.already_have)} already have one`
                    + (r.unmatched ? ` · ${num(r.unmatched)} belong to items not in the catalog` : "");
                if (!r.would_update) { bf.textContent = "Nothing to do"; bf.disabled = true; return; }
                bf.textContent = `Add to ${num(r.would_update)} items`;
                bf.onclick = async () => {
                    bf.disabled = true; bf.textContent = "Working…";
                    const done = await api().im_backfill_images(true);
                    if (done && done.ok) {
                        toast(`${num(done.applied)} items gained a picture`);
                        bf.textContent = "Done";
                        load();
                    } else { bf.textContent = "Failed"; toast("Backfill failed", true); }
                };
            };
            const cells = $("imClearCells");
            if (cells) cells.onclick = async () => {
                dismiss();
                const ok = await confirmBox("Clear per-cell suggestions?",
                    "This wipes the column-level autocomplete index. It rebuilds as you save documents. Item suggestions and saved documents are unaffected.", "Clear");
                if (!ok) return;
                const r = await api().im_clear_cell_memory("", "");
                if (r && r.ok) toast(`Cleared ${num(r.deleted)} remembered cell values`);
            };
        }, 0);
        await closed;
    }

    // ------------------------------------------------------------ wiring
    let _t = null;
    function init() {
        const s = $("imSearch");
        if (s) s.oninput = () => {
            clearTimeout(_t);
            _t = setTimeout(() => { state.search = s.value.trim(); state.checked.clear(); load(); }, 180);
        };
        const sort = $("imSort");
        if (sort) sort.onchange = () => {
            state.sort = sort.value;
            state.desc = null;      // the column's own default, as a first click gets
            load();
        };
        const back = $("imBack");
        if (back) back.onclick = () => { if (window.showHub) window.showHub(); };
        const nb = $("imNewBtn");
        if (nb) nb.onclick = openNewItem;
        const hb = $("imHealthBtn");
        if (hb) hb.onclick = openHealth;
    }
    function render() { renderChrome(); renderList(); }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
})();
