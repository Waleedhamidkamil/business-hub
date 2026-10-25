// ============================================================
// Phase Y - PO fulfillment popups.
// A Delivery Note or Invoice can be linked to one or more Purchase Orders and
// tick which PO items it delivered / invoiced. Invoice payment remains a separate
// explicit action. Two stacked popups, styled with the
// shared .todo-md-* modal classes:
//   1) PO picker   - searchable recent-PO list + "No PO / skip".
//   2) PO detail   - the PO's item table with a checkbox per row (all ticked by
//                    default; already-marked rows start unticked with a tag).
// Entry point: window.openPoFulfillPicker(sourceKind, sourceNo, opts).
//   opts.onAfter() runs after a successful mark.
// ============================================================
(function () {
    const api = () => window.pywebview.api;
    const esc = window.esc || ((s) => String(s == null ? "" : s));
    const toast = window.toast || (() => {});

    const LABEL = {
        dn:  { verb: "delivered", title: "Mark delivered", axis: "delivered" },
        inv: { verb: "invoiced",  title: "Mark invoiced",  axis: "invoiced" },
    };

    let _modal = null;
    function close() {
        if (_modal) { _modal.close(null); _modal = null; }
    }

    /* All four popups in this file are the shared Popup since 2026-08-26
       ([[14-UI-CONTRACTS]]): Esc, the dim background, the X, the focus trap and
       the scroll lock come with the box, so none of them are wired here.

       `sticky` is the tabs-and-search strip some of these have. It lives at the
       top of the body rather than in the head, and stays put while the list
       under it scrolls - it filters the CONTENT, so it belongs with it.

       Every action carries a `value`, which is how the callers below find a
       button they need to enable or disable later. */
    function shell(spec) {
        close();
        const h = window.Popup.open({
            title: spec.title,
            badge: spec.badge,
            subtitle: spec.subtitle,
            size: spec.size || "xl",
            dir: "ltr",                 // the shell is RTL; these tables are not
            className: "po-link-modal" + (spec.className ? " " + spec.className : ""),
            actions: spec.actions || [],
            body: (host) => {
                if (spec.sticky) {
                    const strip = document.createElement("div");
                    strip.className = "po-link-sticky";
                    strip.innerHTML = spec.sticky;
                    host.appendChild(strip);
                }
                const list = document.createElement("div");
                list.className = spec.bodyClass || "";
                if (spec.bodyId) list.id = spec.bodyId;
                list.innerHTML = spec.loading
                    ? `<div class="todo-md-loading">${esc(spec.loading)}</div>` : "";
                host.appendChild(list);
            },
        });
        _modal = h;
        h.result.then(() => { if (_modal === h) _modal = null; });
        return h;
    }

    function primaryOf(row) {
        const textOf = (value) => {
            if (value == null || typeof value === "object") return "";
            const text = String(value).trim();
            return text.toLowerCase().startsWith("data:image") ? "" : text;
        };
        const primaryKeys = ["item", "equipment", "role", "name", "description"];
        const auxiliaryKeys = new Set(["qty", "quantity", "unit", "rate", "price", "total"]);
        for (const key of primaryKeys) {
            const text = textOf(row && row[key]);
            if (text) return text;
        }
        for (const [key, value] of Object.entries(row || {})) {
            if (primaryKeys.includes(key) || auxiliaryKeys.has(key)
                    || ["cost", "cost_qty", "cost_sell", "company", "remarks"].includes(key)
                    || key.startsWith("_")) continue;
            const text = textOf(value);
            if (text) return text;
        }
        for (const key of ["company", "remarks"]) {
            const text = textOf(row && row[key]);
            if (text) return text;
        }
        return "";
    }

    function reasonHtml(row) {
        const reasons = Array.isArray(row && row.reasons) ? row.reasons : [];
        return reasons.length
            ? `<div class="po-link-reasons">${reasons.slice(0, 3).map((reason) =>
                `<span class="po-link-reason">${esc(reason)}</span>`).join("")}</div>`
            : "";
    }

    function candidateSections(rows, suggestions, filter, noOf, textOf, rowHtml,
                               emptyText, allTitle) {
        const query = (filter || "").trim().toLowerCase();
        const matches = (row) => !query || textOf(row).toLowerCase().includes(query);
        const byNo = new Map((rows || []).map((row) => [noOf(row), row]));
        const suggested = (suggestions || [])
            .map((match) => ({ ...(byNo.get(match.no) || {}), ...match, _suggested: true }))
            .filter((row) => noOf(row) && matches(row));
        const suggestedNos = new Set(suggested.map(noOf));
        const remaining = (rows || []).filter((row) => matches(row) && !suggestedNos.has(noOf(row)));
        if (!suggested.length && !remaining.length) {
            return `<div class="po-link-empty">${esc(emptyText)}</div>`;
        }
        const section = (title, sectionRows, cls) => !sectionRows.length ? "" :
            `<section class="po-link-section ${cls || ""}">`
            + `<div class="po-link-section-head">${esc(title)}<span>${sectionRows.length}</span></div>`
            + `<div class="po-link-section-rows">${sectionRows.map(rowHtml).join("")}</div>`
            + `</section>`;
        return section("Suggested", suggested, "is-suggested")
            + section(allTitle || "All documents", remaining, "");
    }

    async function openPicker(sourceKind, sourceNo, opts) {
        opts = opts || {};
        const L = LABEL[sourceKind];
        if (!L) return;
        const hasDnTab = sourceKind === "inv";
        const h = shell({
            title: "Link a Purchase Order",
            subtitle: `Mark PO items as ${L.verb} from ${sourceNo}`,
            actions: [{ label: "Cancel", value: "cancel", cls: "ghost",
                        title: "Close without linking" }],
            sticky:
                `<div class="po-link-tabs${hasDnTab ? " is-three" : ""}" role="tablist" aria-label="Purchase order links">`
                +   `<button type="button" role="tab" data-tab="available">Available POs</button>`
                +   `<button type="button" role="tab" data-tab="linked">Linked POs</button>`
                +   (hasDnTab ? `<button type="button" role="tab" data-tab="dn">Linked delivery notes</button>` : "")
                + `</div>`
                + `<div class="po-link-search"><input type="text" id="poLinkSearch" placeholder="Search PO number, item or company…" autocomplete="off"></div>`,
            bodyClass: "po-link-list", bodyId: "poLinkList",
            loading: "Loading purchase orders…",
        });
        const bd = h.panel;

        const safeCall = async (promise, fallback) => {
            try { return (await promise) || fallback; } catch (e) { return fallback; }
        };
        const [recentCands, linked, suggestions, workflow] = await Promise.all([
            safeCall(api().list_po_candidates(300), []),
            safeCall(api().po_links_for_doc(sourceKind, sourceNo), []),
            safeCall(api().suggest_fulfillment_links(sourceKind, sourceNo, "po", 5), []),
            safeCall(api().document_workflow(sourceKind, sourceNo), { lanes: [] }),
        ]);
        if (h.closed || _modal !== h) return;
        const cands = Array.isArray(recentCands) ? recentCands.slice() : [];
        const knownPos = new Set(cands.map((row) => row.po_no || row.no));
        (workflow && Array.isArray(workflow.lanes) ? workflow.lanes : []).forEach((lane) => {
            const node = lane && lane.po;
            if (!node || !node.no || knownPos.has(node.no)) return;
            knownPos.add(node.no);
            cands.push({
                po_no: node.no,
                po_date: node.date || "",
                item_count: Number(node.item_count || 0),
                summary: node.client || "",
            });
        });
        const linkedSet = new Set(linked);
        const listEl = bd.querySelector("#poLinkList");
        const search = bd.querySelector("#poLinkSearch");
        let activeTab = opts.initialTab === "dn" && hasDnTab ? "dn"
            : opts.initialTab === "linked" ? "linked" : "available";
        let linkedDns = null;

        const poRow = (c) =>
            `<button type="button" class="po-link-row${c._suggested ? " is-suggested" : ""}" data-po="${esc(c.po_no || c.no)}">`
            + `<div class="po-link-row-main">`
            +   `<div class="po-link-row-no">${esc(c.po_no || c.no)}`
            +     (linkedSet.has(c.po_no || c.no) ? `<span class="po-link-tag">linked</span>` : "")
            +   `</div>`
            +   `<div class="po-link-row-sum">${esc(c.summary || "-")}</div>`
            +   reasonHtml(c)
            + `</div>`
            + `<div class="po-link-row-meta">${esc(c.po_date || c.doc_date || "")} · ${c.item_count} item${c.item_count === 1 ? "" : "s"}</div>`
            + `</button>`;

        const dnRow = (c) =>
            `<button type="button" class="po-link-row${c._suggested ? " is-suggested" : ""}" data-dn="${esc(c.no)}">`
            + `<div class="po-link-row-main">`
            +   `<div class="po-link-row-no">${esc(c.no)}<span class="po-link-tag is-route">${c.linked_po_count} linked PO${c.linked_po_count === 1 ? "" : "s"}</span></div>`
            +   `<div class="po-link-row-sum">${esc(c.summary || "-")}</div>`
            +   reasonHtml(c)
            + `</div>`
            + `<div class="po-link-row-meta">${esc(c.doc_date || "")} · ${c.item_count} item${c.item_count === 1 ? "" : "s"}</div>`
            + `</button>`;

        function wireRows() {
            listEl.querySelectorAll("[data-po]").forEach((button) => {
                button.onclick = () => openDetail(sourceKind, sourceNo, button.dataset.po, {
                    ...opts, initialTab: activeTab,
                });
            });
            listEl.querySelectorAll("[data-dn]").forEach((button) => {
                button.onclick = () => openLinkedDnInvoiceDetail(
                    button.dataset.dn, sourceNo, opts,
                    () => openPicker(sourceKind, sourceNo, { ...opts, initialTab: "dn" }));
            });
        }

        async function render(filter) {
            if (activeTab === "available" || activeTab === "linked") {
                const showLinked = activeTab === "linked";
                const pool = cands.filter((row) =>
                    (row.po_no || row.no) !== opts.excludeNo
                    && linkedSet.has(row.po_no || row.no) === showLinked);
                listEl.innerHTML = candidateSections(
                    pool, showLinked ? [] : suggestions, filter,
                    (row) => row.po_no || row.no,
                    (row) => row.search || `${row.po_no || row.no} ${row.summary || ""}`,
                    poRow,
                    showLinked ? "No linked purchase orders." : "No available purchase orders.",
                    showLinked ? "Linked documents" : "All documents");
            } else {
                if (linkedDns == null) {
                    listEl.innerHTML = `<div class="todo-md-loading">Loading linked delivery notes…</div>`;
                    linkedDns = await safeCall(api().list_linked_dn_candidates(sourceNo, 300), []);
                    if (h.closed || _modal !== h || activeTab !== "dn") return;
                }
                listEl.innerHTML = candidateSections(
                    linkedDns, linkedDns.filter((row) => row.suggested).slice(0, 5), filter,
                    (row) => row.no,
                    (row) => row.search || `${row.no} ${row.summary || ""}`,
                    dnRow, "No linked delivery notes match this invoice.");
            }
            wireRows();
        }

        function activateTab(tab) {
            activeTab = tab;
            bd.querySelectorAll(".po-link-tabs [data-tab]").forEach((button) => {
                const active = button.dataset.tab === tab;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-selected", active ? "true" : "false");
                button.tabIndex = active ? 0 : -1;
            });
            search.value = "";
            search.placeholder = tab === "dn"
                ? "Search linked DN number or item…"
                : tab === "linked"
                    ? "Search linked PO number, item or company…"
                    : "Search available PO number, item or company…";
            render("");
        }
        bd.querySelectorAll(".po-link-tabs [data-tab]").forEach((button) => {
            button.onclick = () => activateTab(button.dataset.tab);
        });
        search.oninput = () => render(search.value);
        activateTab(activeTab);
        search.focus();
    }

    async function openLinkedDnInvoiceDetail(dnNo, invoiceNo, opts, onBack) {
        opts = opts || {};
        const h = shell({
            title: dnNo, badge: "DN", className: "po-via-dn-modal",
            subtitle: `Review its linked purchase orders before linking ${invoiceNo}`,
            actions: [
                { label: "← Back", value: "back", cls: "ghost", keep: true,
                  onClick: () => { close(); onBack(); } },
                { label: "Link invoice", value: "confirm", cls: "primary", keep: true },
            ],
            bodyClass: "po-via-dn-body",
            loading: "Loading linked PO items…",
        });
        const bd = h.panel;
        const confirm = bd.querySelector("[data-value='confirm']");

        let detail = null;
        try { detail = await api().linked_dn_invoice_detail(dnNo, invoiceNo); }
        catch (e) { detail = null; }
        if (h.closed || _modal !== h) return;
        const body = bd.querySelector(".po-via-dn-body");
        if (!detail || !detail.ok) {
            confirm.disabled = true;
            body.innerHTML = `<div class="po-link-empty">Couldn't load this Delivery Note's PO links.</div>`;
            return;
        }

        const strip = (title, doc) => {
            const itemRows = (doc && Array.isArray(doc.rows) ? doc.rows : []);
            return `<div class="po-link-ref po-via-ref">`
                + `<div class="po-link-ref-title">${esc(title)} · ${esc((doc && doc.date) || "")}</div>`
                + `<div class="po-link-ref-items">${itemRows.map((row) => {
                    const name = primaryOf(row);
                    const qty = row && row.qty != null ? String(row.qty).trim() : "";
                    return name ? `<span class="po-link-ref-chip">${esc(name)}${qty ? ` <em>×${esc(qty)}</em>` : ""}</span>` : "";
                }).join("") || `<span class="po-link-ref-empty">No item rows.</span>`}</div>`
                + `</div>`;
        };
        const groups = Array.isArray(detail.pos) ? detail.pos : [];
        const groupHtml = groups.map((group) => {
            const proposed = new Set((group.proposed_indexes || []).map(String));
            return `<section class="po-via-group" data-po="${esc(group.po_no)}">`
                + `<header class="po-via-group-head">`
                +   `<label><input type="checkbox" class="po-via-group-all" ${proposed.size ? "checked" : "disabled"}>`
                +   `<span><strong>${esc(group.po_no)}</strong><small>${esc(group.po_date || "")} · ${group.items.length} item${group.items.length === 1 ? "" : "s"} from this DN</small></span></label>`
                + `</header>`
                + `<div class="todo-md-tablewrap"><table class="todo-md-table po-via-table">`
                + `<thead><tr><th class="po-link-check"></th><th>ITEM</th><th>DESCRIPTION</th><th>UNIT</th><th class="ta-r">QTY</th><th>STATUS</th></tr></thead>`
                + `<tbody>${group.items.map((item) => {
                    const row = item.row || {};
                    const available = proposed.has(String(item.index));
                    return `<tr class="${available ? "" : "is-done"}">`
                        + `<td class="po-link-check"><input type="checkbox" class="po-via-check" data-i="${item.index}" ${available ? "checked" : "disabled"}></td>`
                        + `<td>${esc(primaryOf(row) || "-")}</td>`
                        + `<td>${esc(row.description || "")}</td>`
                        + `<td>${esc(row.unit || "")}</td>`
                        + `<td class="ta-r">${esc(row.qty == null ? "" : row.qty)}</td>`
                        + `<td>${item.paid_by
                            ? `<span class="po-link-done">invoiced · ${esc(item.paid_by)}</span>`
                            : `<span class="po-via-ready">ready</span>`}</td>`
                        + `</tr>`;
                }).join("")}</tbody></table></div>`
                + `</section>`;
        }).join("");
        body.innerHTML = strip(dnNo, detail.dn) + strip(invoiceNo, detail.invoice)
            + (groupHtml || `<div class="po-link-empty">This Delivery Note has no active PO item links.</div>`);

        const syncGroup = (groupEl) => {
            const master = groupEl.querySelector(".po-via-group-all");
            const boxes = Array.from(groupEl.querySelectorAll(".po-via-check:not(:disabled)"));
            const checked = boxes.filter((box) => box.checked).length;
            master.checked = boxes.length > 0 && checked === boxes.length;
            master.indeterminate = checked > 0 && checked < boxes.length;
        };
        body.querySelectorAll(".po-via-group").forEach((groupEl) => {
            const master = groupEl.querySelector(".po-via-group-all");
            master.onchange = () => {
                groupEl.querySelectorAll(".po-via-check:not(:disabled)").forEach((box) => { box.checked = master.checked; });
            };
            groupEl.querySelectorAll(".po-via-check").forEach((box) => {
                box.onchange = () => syncGroup(groupEl);
            });
            syncGroup(groupEl);
        });
        confirm.disabled = !body.querySelector(".po-via-check:not(:disabled)");
        if (confirm.disabled) confirm.textContent = "Already invoiced";

        confirm.onclick = async () => {
            const selected = Array.from(body.querySelectorAll(".po-via-group")).map((groupEl) => ({
                poNo: groupEl.dataset.po,
                indexes: Array.from(groupEl.querySelectorAll(".po-via-check:checked"))
                    .map((box) => Number(box.dataset.i)),
            })).filter((group) => group.indexes.length);
            if (!selected.length) { toast("Select at least one PO item", true); return; }
            confirm.disabled = true;
            for (const group of selected) {
                let result = null;
                try { result = await api().set_po_fulfillment(group.poNo, group.indexes, "inv", invoiceNo, true); }
                catch (e) { result = null; }
                if (!result || !result.ok) {
                    toast(`Couldn't link invoice to ${group.poNo}`, true);
                    confirm.disabled = false;
                    return;
                }
            }
            toast(`Linked ${invoiceNo} through ${dnNo}`);
            close();
            try { if (typeof opts.onAfter === "function") await opts.onAfter(); } catch (e) {}
            if (window.refreshOpenPanel) window.refreshOpenPanel();
        };
    }

    async function openDetail(sourceKind, sourceNo, poNo, opts) {
        opts = opts || {};
        const L = LABEL[sourceKind];
        const h = shell({
            title: poNo, badge: "PO", subtitle: "Loading…",
            actions: [
                // Back returns to whichever picker opened this detail: the PO
                // picker (DN/INV-side flow) or the DN/INV picker (PO-side flow,
                // via opts.onBack).
                { label: "← Back", value: "back", cls: "ghost", keep: true,
                  title: "Back to PO list",
                  onClick: () => {
                      close();
                      if (typeof opts.onBack === "function") opts.onBack();
                      else openPicker(sourceKind, sourceNo, opts);
                  } },
                { label: L.title, value: "mark", cls: "primary", keep: true,
                  title: L.title },
            ],
            bodyId: "poDetBody", loading: "Loading items…",
        });
        const bd = h.panel;

        let rec = null;
        try { rec = await api().load_po(poNo); } catch (e) { rec = null; }
        if (h.closed || _modal !== h) return;
        const bodyEl = bd.querySelector("#poDetBody");
        // The count line goes in the popup's own subtitle. This used to be a
        // hand-rolled `#poDetMeta` div; the Popup migration replaced the header
        // that built it and `setSubtitle` is what replaced it (same as todo.js).
        if (!rec || !Array.isArray(rec.rows) || !rec.rows.length) {
            h.setSubtitle("-");
            bodyEl.innerHTML = `<div class="po-link-empty">This PO has no items to mark.</div>`;
            return;
        }
        const rows = rec.rows;
        const fulfill = rec.fulfillment || { delivered: {}, invoiced: {}, paid: {} };
        const doneMap = (sourceKind === "dn" ? fulfill.delivered : fulfill.invoiced) || {};
        const doneOf = (i) => doneMap[String(i)] || doneMap[i] || null;   // keys arrive as strings

        // PO-side flow passes the picked DN/invoice + its rows as opts.referenceDoc,
        // so we can (a) show what that doc contains and (b) pre-tick the PO rows whose
        // item names match it (instead of ticking all). DN/INV-side flow has no ref.
        const ref = opts.referenceDoc || null;
        const refNames = ref
            ? new Set((ref.rows || []).map((r) => primaryOf(r).toLowerCase()).filter(Boolean))
            : null;
        const shouldCheck = (r, i) => {
            if (doneOf(i)) return false;                        // already marked by some source
            if (!refNames) return true;                         // DN/INV-side: default all ticked
            return refNames.has(primaryOf(r).toLowerCase());    // PO-side: only the matches
        };
        h.setSubtitle(ref
            ? `tick the PO items ${sourceNo} ${L.verb} - matches are pre-selected`
            : `${rows.length} item${rows.length === 1 ? "" : "s"} · tick the ones ${sourceNo} ${L.verb}`);

        const COLS = [
            { key: "name",        head: "ITEM" },
            { key: "description",  head: "DESCRIPTION" },
            { key: "unit",         head: "UNIT" },
            { key: "qty",          head: "QTY", r: true },
            { key: "company",      head: "COMPANY" },
        ].filter((c) => rows.some((r) => r && r[c.key] != null && String(r[c.key]).trim() !== ""));

        // Reference strip: what the picked DN/invoice actually contains (PO-side flow).
        const refSection = ref ? (
            `<div class="po-link-ref">`
            + `<div class="po-link-ref-title">${esc(sourceNo)} ${esc(L.verb)}:</div>`
            + ((ref.rows && ref.rows.length)
                ? `<div class="po-link-ref-items">`
                  + ref.rows.map((r) => {
                        const nm = primaryOf(r);
                        const q = (r && r.qty != null && String(r.qty).trim() !== "") ? String(r.qty).trim() : "";
                        return nm ? `<span class="po-link-ref-chip">${esc(nm)}${q ? ` <em>×${esc(q)}</em>` : ""}</span>` : "";
                    }).join("")
                  + `</div>`
                : `<div class="po-link-ref-empty">No item list stored for ${esc(sourceNo)}.</div>`)
            + `</div>`
        ) : "";

        bodyEl.innerHTML =
            refSection
            + `<div class="todo-md-tablewrap"><table class="todo-md-table po-link-table"><thead><tr>`
            + `<th class="po-link-check"><input type="checkbox" id="poDetAll" title="Toggle all"></th>`
            + COLS.map((c) => `<th${c.r ? ' class="ta-r"' : ""}>${esc(c.head)}</th>`).join("")
            + `<th class="po-link-status"></th></tr></thead><tbody>`
            + rows.map((r, i) => {
                const done = doneOf(i);
                return `<tr class="${done ? "is-done" : ""}">`
                    + `<td class="po-link-check"><input type="checkbox" class="po-det-chk" data-i="${i}" ${shouldCheck(r, i) ? "checked" : ""}></td>`
                    + COLS.map((c) => `<td${c.r ? ' class="ta-r"' : ""}>${esc(String(r[c.key] == null ? "" : r[c.key])).replace(/\n/g, "<br>")}</td>`).join("")
                    + `<td class="po-link-status">${done ? `<span class="po-link-done">${esc(L.verb)} · ${esc(done.source_no || "")}</span>` : ""}</td>`
                    + `</tr>`;
            }).join("")
            + `</tbody></table></div>`;

        const boxes = () => Array.from(bd.querySelectorAll(".po-det-chk"));
        const allChk = bd.querySelector("#poDetAll");
        const syncAll = () => {
            const bs = boxes();
            const on = bs.filter((b) => b.checked).length;
            allChk.checked = on === bs.length;
            allChk.indeterminate = on > 0 && on < bs.length;
        };
        allChk.onchange = () => { boxes().forEach((b) => { b.checked = allChk.checked; }); };
        boxes().forEach((b) => b.addEventListener("change", syncAll));
        syncAll();

        bd.querySelector("[data-value='mark']").onclick = async (ev) => {
            const idxs = boxes().filter((b) => b.checked).map((b) => parseInt(b.dataset.i, 10));
            if (!idxs.length) { toast("Tick at least one item, or press Back", true); return; }
            ev.currentTarget.disabled = true;
            let r = null;
            try { r = await api().set_po_fulfillment(poNo, idxs, sourceKind, sourceNo, true); }
            catch (e) { r = null; }
            if (!r || !r.ok) { toast("Couldn't save the fulfillment", true); ev.currentTarget.disabled = false; return; }
            // PO-side DN linking also advances the DN to submitted. Invoice
            // linking deliberately does not alter the invoice's Paid marker.
            if (opts.flipSourceStatus && sourceKind === "dn") {
                try {
                    await api().set_submitted("dn", sourceNo, true);
                } catch (e) { /* the fulfillment link is saved regardless */ }
            }
            toast(`Marked ${idxs.length} item${idxs.length === 1 ? "" : "s"} ${L.verb} on ${poNo}`);
            close();
            try { if (typeof opts.onAfter === "function") await opts.onAfter(); } catch (e) {}
            if (window.refreshOpenPanel) window.refreshOpenPanel();
        };
    }

    // ── PO-side flow ────────────────────────────────────────────────────────
    // From an open PO, pick a Delivery Note (mark this PO's items delivered) or an
    // Invoice (mark them invoiced). Reuses the shared item-checkbox detail popup; the
    // only new part is a picker that lists DNs / Invoices instead of POs.
    const SRC = {
        dn:  { listApi: "list_dn_candidates",  word: "Delivery Note", plural: "DNs",
               available: "Available DNs", linked: "Linked DNs", ph: "Search DN number, item or client…" },
        inv: { listApi: "list_inv_candidates", word: "Invoice", plural: "Invoices",
               available: "Available Invoices", linked: "Linked Invoices", ph: "Search invoice number, item or client…" },
    };
    async function openSourcePicker(poNo, sourceKind, opts) {
        opts = opts || {};
        const L = LABEL[sourceKind];
        const S = SRC[sourceKind];
        if (!L || !S || !poNo) return;
        const h = shell({
            title: `Link a ${S.word}`,
            subtitle: `Mark items on ${poNo} as ${L.verb}`,
            actions: [{ label: "Cancel", value: "cancel", cls: "ghost",
                        title: "Close without linking" }],
            sticky:
                `<div class="po-link-tabs" role="tablist" aria-label="${esc(S.word)} links">`
                +   `<button type="button" role="tab" data-tab="available">${esc(S.available)}</button>`
                +   `<button type="button" role="tab" data-tab="linked">${esc(S.linked)}</button>`
                + `</div>`
                + `<div class="po-link-search"><input type="text" id="poSrcSearch" placeholder="${esc(S.ph)}" autocomplete="off"></div>`,
            bodyClass: "po-link-list", bodyId: "poSrcList",
            loading: `Loading ${S.word.toLowerCase()}s…`,
        });
        const bd = h.panel;

        let cands = [];
        try { cands = (await api()[S.listApi](300)) || []; } catch (e) { cands = []; }
        let suggestions = [];
        try { suggestions = (await api().suggest_fulfillment_links("po", poNo, sourceKind, 5)) || []; }
        catch (e) { suggestions = []; }
        if (h.closed || _modal !== h) return;                       // closed while loading
        // Which DNs/Invoices are already linked to THIS PO (tag them "linked").
        let rec = null;
        let workflow = null;
        const safeSourceCall = async (call) => {
            try { return await call(); } catch (e) { return null; }
        };
        [rec, workflow] = await Promise.all([
            safeSourceCall(() => api().load_po(poNo)),
            safeSourceCall(() => api().document_workflow("po", poNo)),
        ]);
        const fmap = (rec && rec.fulfillment) || { delivered: {}, invoiced: {}, paid: {} };
        const doneMap = (sourceKind === "dn" ? fmap.delivered : fmap.invoiced) || {};
        const linkedSet = new Set(
            Object.values(doneMap).map((v) => v && v.source_no).filter(Boolean));
        const lane = workflow && Array.isArray(workflow.lanes)
            ? workflow.lanes.find((entry) => entry && entry.po && entry.po.no === poNo)
            : null;
        const linkedNodes = lane
            ? (sourceKind === "dn" ? lane.dns : lane.invoices) || []
            : [];
        linkedNodes.forEach((node) => { if (node && node.no) linkedSet.add(node.no); });
        const knownSources = new Set(cands.map((row) => row.no));
        linkedNodes.forEach((node) => {
            if (!node || !node.no || knownSources.has(node.no)) return;
            knownSources.add(node.no);
            cands.push({
                no: node.no,
                doc_date: node.date || "",
                item_count: Number(node.item_count || 0),
                summary: node.client || "",
            });
        });
        const listEl = bd.querySelector("#poSrcList");
        const search = bd.querySelector("#poSrcSearch");
        let activeTab = opts.initialTab === "linked" ? "linked" : "available";

        function render(filter) {
            const showLinked = activeTab === "linked";
            const pool = cands.filter((row) =>
                row.no !== opts.excludeNo && linkedSet.has(row.no) === showLinked);
            const rowHtml = (c) =>
                `<button type="button" class="po-link-row${c._suggested ? " is-suggested" : ""}" data-no="${esc(c.no)}">`
                + `<div class="po-link-row-main">`
                +   `<div class="po-link-row-no">${esc(c.no)}`
                +     (linkedSet.has(c.no) ? `<span class="po-link-tag">linked</span>` : "")
                +   `</div>`
                +   `<div class="po-link-row-sum">${esc(c.summary || "-")}</div>`
                +   reasonHtml(c)
                + `</div>`
                + `<div class="po-link-row-meta">${esc(c.doc_date || "")} · ${c.item_count} item${c.item_count === 1 ? "" : "s"}</div>`
                + `</button>`;
            listEl.innerHTML = candidateSections(
                pool, showLinked ? [] : suggestions, filter,
                (row) => row.no,
                (row) => row.search || `${row.no} ${row.summary || ""}`,
                rowHtml,
                showLinked ? `No linked ${S.plural}.` : `No available ${S.plural}.`,
                showLinked ? "Linked documents" : "All documents");
            listEl.querySelectorAll(".po-link-row").forEach((b) =>
                b.onclick = async () => {
                    // Load the picked DN/invoice's own items so the detail popup can
                    // show what it contains and pre-tick the matching PO rows.
                    let refRec = null;
                    try {
                        refRec = sourceKind === "dn"
                            ? await api().load_dn(b.dataset.no)
                            : await api().load_invoice(b.dataset.no);
                    } catch (e) { refRec = null; }
                    openDetail(sourceKind, b.dataset.no, poNo, {
                        ...opts,
                        flipSourceStatus: true,
                        referenceDoc: { no: b.dataset.no, kind: sourceKind, rows: (refRec && refRec.rows) || [] },
                        onBack: () => openSourcePicker(poNo, sourceKind, {
                            ...opts, initialTab: activeTab,
                        }),
                    });
                });
        }

        function activateTab(tab) {
            activeTab = tab;
            bd.querySelectorAll(".po-link-tabs [data-tab]").forEach((button) => {
                const active = button.dataset.tab === tab;
                button.classList.toggle("is-active", active);
                button.setAttribute("aria-selected", active ? "true" : "false");
                button.tabIndex = active ? 0 : -1;
            });
            search.value = "";
            search.placeholder = tab === "linked"
                ? `Search linked ${S.word.toLowerCase()} number or item…`
                : S.ph;
            render("");
        }
        bd.querySelectorAll(".po-link-tabs [data-tab]").forEach((button) => {
            button.onclick = () => activateTab(button.dataset.tab);
        });
        search.oninput = () => render(search.value);
        activateTab(activeTab);
        search.focus();
    }

    window.openPoFulfillPicker = openPicker;
    window.openPoSourcePicker = openSourcePicker;
})();
