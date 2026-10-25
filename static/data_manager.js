/* =========================================================
   Phase I.1 + I.5 - Data Manager frontend
   ---------------------------------------------------------
   Reachable from App Settings → "Open Data Manager".

   Tabs:
     - Browse:     pick a catalog (or a document section), filter, search,
                   edit/delete rows, find duplicates.
     - Duplicates: per-catalog fuzzy match with tunable threshold +
                   side-by-side merge with one click.
     - Backup & Restore: zip everything to a folder you pick; restore
                   from a backup zip.

   The Browse sidebar shows two top-level groups:
     1. AUTOFILL CATALOGS - items, companies, clients, per-type item
        catalogs for QUT/INV, DN items, universal column memory. Editing
        these directly tweaks what the editors suggest as you type.
     2. DOCUMENT RECORDS - the rows in pos / quotes / dns / invoices /
        Browse + delete + relink-pdf only (the actual record
        editor opens via the normal section view).
   ========================================================= */
(function () {
    "use strict";

    const dmState = {
        mode: "catalogs",  // 'catalogs' | 'documents' - chosen by the entry point
        catalogs: [],      // catalog section metas (Phase I.5)
        documents: [],     // document section metas (Phase I.1)
        sectionKey: null,
        sectionType: null, // 'catalog' | 'document'
        section: null,
        expandedGroups: null, // Set<string> of open accordion groups (lazy init)
        sectionFilter: "",    // quick-filter text for the sections sidebar
        section_counts: {},   // {sectionKey: rowCount} for sidebar badges
        rows: [],
        total: 0,
        year: "",
        month: "",
        client: "",
        search: "",
        selected: new Set(),
        edit: { open: false, oldPk: null, fields: null, sectionKey: null },
        dup: { sectionKey: null, threshold: 85, clusters: [] },
    };

    // ---------------------------------------------------------- helpers
    const api = () => window.pywebview.api;
    const $   = (id) => document.getElementById(id);

    function toast(msg, isError) {
        if (window.toast) return window.toast(msg, !!isError);
        console[isError ? "error" : "log"](msg);
    }

    function fmtDate(v) {
        if (!v) return "";
        if (/^\d{4}-\d{2}-\d{2}T/.test(v)) return v.slice(0, 16).replace("T", " ");
        return String(v);
    }

    function fmtMoney(v) {
        if (v === null || v === undefined || v === "") return "";
        const n = Number(v);
        if (Number.isNaN(n)) return String(v);
        return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    function pkKey(pk) {
        // Stable string-form of a pk dict, so we can put it in a Set.
        if (pk && typeof pk === "object") {
            const keys = Object.keys(pk).sort();
            return JSON.stringify(keys.map(k => [k, pk[k]]));
        }
        return String(pk);
    }

    // ---------------------------------------------------------- entry
    async function showDataManager(mode) {
        const newMode = (mode === "documents") ? "documents" : "catalogs";
        // Reset section if switching modes so we don't land on a hidden section.
        if (dmState.mode !== newMode) {
            dmState.sectionKey = null;
            dmState.sectionType = null;
            dmState.expandedGroups = null; // re-seed accordion for the new pool
            dmState.sectionFilter = "";
            const fEl = $("dmSectionFilter"); if (fEl) fEl.value = "";
        }
        dmState.mode = newMode;

        document.querySelectorAll(".view").forEach(v => v.hidden = true);
        const strip = $("poStrip"); if (strip) strip.hidden = true;
        ["poActions","quoteActions","dnActions","invActions"].forEach(id => {
            const el = $(id); if (el) el.hidden = true;
        });
        $("dataManagerView").hidden = false;
        const home = $("btnHome"); if (home) home.hidden = false;

        // Brand + header title per mode.
        const headerTitle = $("dmHeaderTitle");
        const titleText = (newMode === "documents") ? "Document Records" : "Data Manager";
        if (headerTitle) headerTitle.textContent = titleText;
        if (window.setBrand) window.setBrand(titleText);
        if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();

        // Duplicates tab only applies to the catalog mode.
        const dupTab = document.querySelector(".dm-tab[data-dm-tab='duplicates']");
        if (dupTab) dupTab.hidden = (newMode === "documents");
        // If a hidden tab was active, switch back to browse.
        if (newMode === "documents") {
            const activePane = document.querySelector(".dm-pane[data-dm-pane='duplicates']:not([hidden])");
            if (activePane) switchTab("browse");
        }
        switchTab(document.querySelector(".dm-tab.active")?.dataset.dmTab || "browse");

        // Fetch BOTH catalogs and document sections (cheap; small payloads).
        if (!dmState.catalogs.length || !dmState.documents.length) {
            const [cats, docs] = await Promise.all([
                api().dm_list_catalogs(),
                api().dm_list_sections(),
            ]);
            dmState.catalogs  = cats || [];
            dmState.documents = docs || [];
        }
        renderSidebar();
        rebuildDupCatalogSelect();

        if (dmState.sectionKey) await dmLoadSection(dmState.sectionKey);
        else {
            const pool = (newMode === "documents") ? dmState.documents : dmState.catalogs;
            const first = pool[0];
            if (first) await dmLoadSection(first.key);
        }
    }
    window.showDataManager = showDataManager;

    // ---------------------------------------------------------- tabs
    function switchTab(tab) {
        document.querySelectorAll(".dm-tab").forEach(b => {
            b.classList.toggle("active", b.dataset.dmTab === tab);
        });
        document.querySelectorAll(".dm-pane").forEach(p => {
            p.hidden = (p.dataset.dmPane !== tab);
        });
        if (tab === "clients") loadDmClients();
    }

    function bindTabs() {
        document.querySelectorAll(".dm-tab").forEach(b => {
            if (b.disabled) return;
            b.addEventListener("click", () => switchTab(b.dataset.dmTab));
        });
    }

    // ---------------------------------------------------------- sidebar
    // A small accent per document family - keeps the collapsed accordion
    // scannable at a glance without a wall of identical rows.
    const GROUP_ACCENT = {
        // White text sits on the active row's fill, so every accent here must
        // hold 4.5:1 against white (the old green #059669 and cyan #0891B2
        // did not). Matches the dashboard's per-document colours.
        "Purchase Orders":   "#5F5F1E",
        "Quotations":        "#0B2A5B",
        "Delivery Notes":    "#115E59",
        "Invoices":          "#005A8C",
        "Shared catalogs":   "#64748B",
    };

    // Pull a short, clean leaf label: "Quotations - Materials" -> "Materials".
    function shortLabel(label, groupLabel) {
        const s = String(label || "");
        const i = s.lastIndexOf(" - ");
        if (i >= 0) return s.slice(i + 3);
        if (groupLabel && s.startsWith(groupLabel)) {
            const rest = s.slice(groupLabel.length).replace(/^[\s-–-]+/, "");
            if (rest) return rest;
        }
        return s;
    }

    // Group an ordered section list by group_label, preserving first-seen order.
    function groupByLabel(sections) {
        const groups = [];
        const index = {};
        for (const s of sections) {
            const g = s.group_label;
            if (!(g in index)) { index[g] = groups.length; groups.push([g, []]); }
            groups[index[g]][1].push(s);
        }
        return groups;
    }

    function ensureExpanded() {
        // Lazily open only the group that holds the active section (or the
        // first group), so the panel starts tidy instead of fully unrolled.
        if (dmState.expandedGroups) return;
        dmState.expandedGroups = new Set();
        const pool = (dmState.mode === "documents") ? dmState.documents : dmState.catalogs;
        const active = pool.find(s => s.key === dmState.sectionKey) || pool[0];
        if (active) dmState.expandedGroups.add(active.group_label);
    }

    function renderSidebar() {
        const host = $("dmSectionList");
        if (!host) return;
        ensureExpanded();

        const typeName = (dmState.mode === "documents") ? "document" : "catalog";
        const pool = (dmState.mode === "documents") ? dmState.documents : dmState.catalogs;
        const filter = dmState.sectionFilter.trim().toLowerCase();

        // A title chip describing the current pool.
        const titleEl = $("dmSidebarTitle");
        if (titleEl) titleEl.textContent =
            (dmState.mode === "documents") ? "Document records" : "Autofill catalogs";

        const groups = groupByLabel(pool);
        const html = [];
        let anyVisible = false;

        for (const [g, items] of groups) {
            const accent = GROUP_ACCENT[g] || "#64748B";
            // When filtering, only keep items whose (short) label matches.
            const matches = filter
                ? items.filter(s =>
                    s.label.toLowerCase().includes(filter) ||
                    g.toLowerCase().includes(filter))
                : items;
            if (!matches.length) continue;
            anyVisible = true;

            // Filtering forces every matching group open; otherwise honor state.
            const open = filter ? true : dmState.expandedGroups.has(g);
            const hasActive = matches.some(s => s.key === dmState.sectionKey);

            // Single-section families (PO, DN) collapse into one flat row -
            // no caret, the header IS the section button.
            if (items.length === 1) {
                const s = items[0];
                const active = (s.key === dmState.sectionKey) ? "active" : "";
                html.push(
                    `<button type="button" class="dm-grp-solo dm-section-btn ${active}" `
                    + `style="--accent:${accent}" data-section="${escapeAttr(s.key)}" `
                    + `data-type="${typeName}">`
                    + `<span class="dm-grp-dot"></span>`
                    + `<span class="dm-grp-name">${escapeHtml(g)}</span>`
                    + countBadge(s.key)
                    + `</button>`
                );
                continue;
            }

            // Multi-section family: collapsible header + leaf rows.
            html.push(
                `<button type="button" class="dm-grp-head${open ? " open" : ""}`
                + `${hasActive ? " has-active" : ""}" style="--accent:${accent}" `
                + `data-group="${escapeAttr(g)}">`
                + `<svg class="dm-grp-caret" width="12" height="12" viewBox="0 0 24 24" `
                + `fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" `
                + `stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>`
                + `<span class="dm-grp-dot"></span>`
                + `<span class="dm-grp-name">${escapeHtml(g)}</span>`
                + `<span class="dm-grp-count">${matches.length}</span>`
                + `</button>`
            );
            html.push(`<div class="dm-grp-items${open ? "" : " collapsed"}">`);
            for (const s of matches) {
                const active = (s.key === dmState.sectionKey) ? "active" : "";
                html.push(
                    `<button type="button" class="dm-section-btn dm-leaf ${active}" `
                    + `style="--accent:${accent}" data-section="${escapeAttr(s.key)}" `
                    + `data-type="${typeName}">`
                    + `<span class="dm-leaf-label">${escapeHtml(shortLabel(s.label, g))}</span>`
                    + countBadge(s.key)
                    + `</button>`
                );
            }
            html.push(`</div>`);
        }

        if (!anyVisible) {
            html.push(`<div class="dm-grp-empty">No sections match “${escapeHtml(filter)}”.</div>`);
        }

        host.innerHTML = html.join("");

        // Section selection.
        host.querySelectorAll(".dm-section-btn").forEach(b => {
            b.addEventListener("click", () => dmLoadSection(b.dataset.section));
        });
        // Group expand / collapse.
        host.querySelectorAll(".dm-grp-head").forEach(b => {
            b.addEventListener("click", () => {
                const g = b.dataset.group;
                if (dmState.expandedGroups.has(g)) dmState.expandedGroups.delete(g);
                else dmState.expandedGroups.add(g);
                renderSidebar();
            });
        });
        syncGroupsToggleLabel();
    }

    // Small count chip shown on each section row (filled lazily as sections load).
    function countBadge(key) {
        const n = dmState.section_counts[key];
        if (n === undefined || n === null) return "";
        return `<span class="dm-sec-count">${n}</span>`;
    }

    function allGroupLabels() {
        const pool = (dmState.mode === "documents") ? dmState.documents : dmState.catalogs;
        return [...new Set(pool.filter(s =>
            // only multi-section families have a collapsible header
            pool.filter(x => x.group_label === s.group_label).length > 1
        ).map(s => s.group_label))];
    }

    function syncGroupsToggleLabel() {
        const btn = $("dmGroupsToggle");
        if (!btn) return;
        ensureExpanded();
        const labels = allGroupLabels();
        const allOpen = labels.length > 0 && labels.every(g => dmState.expandedGroups.has(g));
        btn.textContent = allOpen ? "Collapse all" : "Expand all";
        btn.dataset.state = allOpen ? "open" : "closed";
    }

    function toggleAllGroups() {
        ensureExpanded();
        const labels = allGroupLabels();
        const allOpen = labels.length > 0 && labels.every(g => dmState.expandedGroups.has(g));
        if (allOpen) labels.forEach(g => dmState.expandedGroups.delete(g));
        else         labels.forEach(g => dmState.expandedGroups.add(g));
        renderSidebar();
    }

    function sectionKind(key) {
        if (dmState.catalogs.some(s => s.key === key))  return "catalog";
        if (dmState.documents.some(s => s.key === key)) return "document";
        return null;
    }

    // ---------------------------------------------------------- load section
    async function dmLoadSection(key) {
        const kind = sectionKind(key);
        if (!kind) return;
        dmState.sectionKey = key;
        dmState.sectionType = kind;
        dmState.selected.clear();
        renderSidebar();
        $("dmEmpty").hidden = true;
        $("dmTableWrap").hidden = false;

        // Show/hide filter widgets that are only meaningful for documents.
        const docOnly = (kind === "document");
        $("dmYear").hidden   = !docOnly;
        $("dmMonth").hidden  = !docOnly;
        $("dmClient").hidden = !docOnly;

        if (docOnly) {
            const filters = await api().dm_list_filters(key);
            const ySel = $("dmYear");
            ySel.innerHTML = `<option value="">All years</option>`
                + (filters.years || []).map(y => `<option value="${y}">${y}</option>`).join("");
            ySel.value = "";
            const cSel = $("dmClient");
            cSel.innerHTML = `<option value="">All clients</option>`
                + (filters.clients || []).map(c => `<option value="${escapeAttr(c)}">${escapeHtml(c)}</option>`).join("");
            cSel.value = "";
        }
        dmState.year = ""; dmState.month = ""; dmState.client = ""; dmState.search = "";
        $("dmSearch").value = ""; $("dmMonth").value = "";

        await dmRefreshRecords();
    }

    // ---------------------------------------------------------- refresh
    async function dmRefreshRecords() {
        if (!dmState.sectionKey) return;
        let resp;
        if (dmState.sectionType === "catalog") {
            resp = await api().dm_list_catalog_records(
                dmState.sectionKey, dmState.search || null, 500, 0,
            );
        } else {
            resp = await api().dm_list_records(
                dmState.sectionKey,
                dmState.year   ? parseInt(dmState.year, 10)  : null,
                dmState.month  ? parseInt(dmState.month, 10) : null,
                dmState.client || null,
                dmState.search || null,
                500, 0,
            );
        }
        dmState.rows = resp.rows || [];
        dmState.total = resp.total || 0;
        dmState.section = resp.section || null;
        // Remember the section's size so the sidebar can show a count chip.
        // Only meaningful when there are no in-section filters narrowing it.
        if (!dmState.search && !dmState.year && !dmState.month && !dmState.client) {
            if (dmState.section_counts[dmState.sectionKey] !== dmState.total) {
                dmState.section_counts[dmState.sectionKey] = dmState.total;
                renderSidebar();
            }
        }
        const visible = new Set(dmState.rows.map(r => pkKey(r.__pk)));
        for (const k of [...dmState.selected]) {
            if (!visible.has(k)) dmState.selected.delete(k);
        }
        renderTable();
        $("dmCount").textContent = `${dmState.total} row${dmState.total === 1 ? "" : "s"}`;
    }

    // ---------------------------------------------------------- table render
    function renderTable() {
        const thead = $("dmThead");
        const tbody = $("dmTbody");
        if (!dmState.section) { thead.innerHTML = ""; tbody.innerHTML = ""; return; }

        const cols = dmState.section.display_columns;
        thead.innerHTML =
            `<th class="dm-col-select">`
            + `<input type="checkbox" id="dmSelectAll" aria-label="Select all">`
            + `</th>`
            + cols.map(c => `<th>${escapeHtml(c.header)}</th>`).join("")
            + `<th class="dm-col-actions">Actions</th>`;

        const selAll = $("dmSelectAll");
        if (selAll) selAll.onclick = (e) => {
            const on = e.target.checked;
            dmState.selected.clear();
            if (on) dmState.rows.forEach(r => dmState.selected.add(pkKey(r.__pk)));
            renderTable();
            updateBulkUI();
        };

        const isCat = (dmState.sectionType === "catalog");
        const html = dmState.rows.map((r, idx) => {
            const pk = pkKey(r.__pk);
            const checked = dmState.selected.has(pk) ? "checked" : "";
            const cells = cols.map(c => {
                let v = r[c.key];
                if (c.kind === "date" || c.kind === "datetime") v = fmtDate(v);
                else if (c.kind === "money")                   v = fmtMoney(v);
                else if (c.kind === "count")                   v = (v == null ? "0" : String(v));
                else                                            v = (v == null ? "" : String(v));
                return `<td class="dm-cell-${c.kind}">${escapeHtml(v)}</td>`;
            }).join("");

            let actions;
            if (isCat) {
                actions = `<button class="link" data-act="cat-edit" data-idx="${idx}">Edit</button>`
                        + ` · <button class="link danger-text" data-act="cat-del" data-idx="${idx}">Delete</button>`;
            } else {
                const missing = !r.__pdf_exists;
                const pdfBtn = r.__pdf
                    ? (missing
                        ? `<button class="link warm-red" data-act="doc-relink" data-pk="${escapeAttr(String(r.__pk))}" title="PDF missing - click to relink">PDF missing</button>`
                        : `<button class="link" data-act="doc-pdf" data-path="${escapeAttr(r.__pdf)}" title="${escapeAttr(r.__pdf)}">PDF</button>`)
                    : `<span class="subtle">no path</span>`;
                actions =
                    `<button class="link" data-act="doc-edit" data-pk="${escapeAttr(String(r.__pk))}">Edit</button>`
                    + ` · ${pdfBtn}`
                    + ` · <button class="link" data-act="doc-relink" data-pk="${escapeAttr(String(r.__pk))}">Relink</button>`
                    + ` · <button class="link danger-text" data-act="doc-del" data-pk="${escapeAttr(String(r.__pk))}">Delete</button>`;
            }
            return `<tr data-pk="${escapeAttr(pk)}">`
                + `<td class="dm-col-select"><input type="checkbox" data-act="sel" data-pk="${escapeAttr(pk)}" ${checked}></td>`
                + cells
                + `<td class="dm-col-actions">${actions}</td></tr>`;
        }).join("");

        tbody.innerHTML = html
            || `<tr><td colspan="${cols.length + 2}" class="dm-empty-row">No rows match the current filters.</td></tr>`;

        // Row action wiring
        tbody.querySelectorAll("input[data-act='sel']").forEach(cb => {
            cb.addEventListener("change", (e) => {
                const k = e.target.dataset.pk;
                if (e.target.checked) dmState.selected.add(k);
                else                   dmState.selected.delete(k);
                updateBulkUI();
            });
        });
        // Catalog actions
        tbody.querySelectorAll("button[data-act='cat-edit']").forEach(b => {
            b.addEventListener("click", () => openCatalogEditModal(dmState.rows[+b.dataset.idx]));
        });
        tbody.querySelectorAll("button[data-act='cat-del']").forEach(b => {
            b.addEventListener("click", () => deleteCatalogRow(dmState.rows[+b.dataset.idx]));
        });
        // Document actions
        tbody.querySelectorAll("button[data-act='doc-edit']").forEach(b => {
            b.addEventListener("click", () => openInEditor(b.dataset.pk));
        });
        tbody.querySelectorAll("button[data-act='doc-pdf']").forEach(b => {
            b.addEventListener("click", () => api().open_path(b.dataset.path));
        });
        tbody.querySelectorAll("button[data-act='doc-relink']").forEach(b => {
            b.addEventListener("click", () => relinkDocPdf(b.dataset.pk));
        });
        tbody.querySelectorAll("button[data-act='doc-del']").forEach(b => {
            b.addEventListener("click", () => deleteDocOne(b.dataset.pk));
        });

        updateBulkUI();
    }

    function updateBulkUI() {
        const btn = $("dmBulkDelete");
        const n = dmState.selected.size;
        btn.disabled = (n === 0);
        btn.querySelector("span").textContent =
            n === 0 ? "Delete selected" : `Delete ${n} selected`;
    }

    // ---------------------------------------------------------- document actions
    async function openInEditor(pk) {
        const route = dmState.section && dmState.section.editor_route;
        if (!route) return;
        const map = {
            "po":     () => window.loadForEdit && window.loadForEdit(pk),
            "quote":  () => window.loadQuoteForEdit && window.loadQuoteForEdit(pk),
            "dn":     () => window.loadDnForEdit && window.loadDnForEdit(pk),
            "inv":    () => window.loadInvForEdit && window.loadInvForEdit(pk),
        };
        const fn = map[route];
        if (!fn) { toast(`No editor route for "${route}"`, true); return; }
        try { await fn(); }
        catch (e) { toast(`Could not open ${pk}: ${e.message || e}`, true); }
    }

    async function relinkDocPdf(pk) {
        const picked = await api().dm_pick_file(
            "Pick the new PDF for this record",
            ["PDF files (*.pdf)"],
        );
        if (!picked || picked.cancelled) return;
        if (!picked.ok) { toast(picked.error || "Could not pick file", true); return; }
        const r = await api().dm_relink_pdf(dmState.sectionKey, pk, picked.path);
        if (!r.ok) { toast(r.error || "Relink failed", true); return; }
        toast(`Re-linked PDF for ${pk}.`);
        await dmRefreshRecords();
    }

    async function deleteDocOne(pk) {
        const ok = await confirm2(
            `Delete ${pk}?`,
            `This removes the database row only. The PDF file on disk will be left alone.`,
        );
        if (!ok) return;
        const r = await api().dm_delete_record(dmState.sectionKey, pk);
        if (!r.ok) { toast(r.error || "Delete failed", true); return; }
        toast(`Deleted ${pk} from the database.`);
        await dmRefreshRecords();
    }

    // ---------------------------------------------------------- catalog actions
    let _dmEditPopup = null;      // the shared Popup handle ([[14-UI-CONTRACTS]])
    function openCatalogEditModal(row) {
        if (!row || !dmState.section) return;
        const sec = dmState.section;
        const backdrop = $("dmEditBackdrop");
        const host = $("dmEditFields");

        // Build a form with: name + every variant column.
        const fields = [{
            key: sec.name_col,
            header: sec.display_columns[0].header,
            kind: "text",
            value: row[sec.name_col] || "",
        }];
        for (const v of sec.variant_cols) {
            fields.push({
                key: v.key, header: v.header, kind: v.kind,
                value: row[v.key] != null ? String(row[v.key]) : "",
            });
        }

        host.innerHTML = fields.map((f, i) => {
            const type = (f.kind === "money") ? "number" : "text";
            const step = (f.kind === "money") ? ` step="0.01"` : "";
            return `<div class="dm-edit-field">`
                + `<label>${escapeHtml(f.header)}</label>`
                + `<input type="${type}"${step} data-i="${i}" `
                + `value="${escapeAttr(f.value)}" />`
                + `</div>`;
        }).join("");

        dmState.edit = {
            open: true,
            oldPk: row.__pk,
            sectionKey: dmState.sectionKey,
            fields: fields,
        };
        /* The MARKUP stays in index.html (its fields are wired by id here);
           the BEHAVIOUR is the shared contract's since 2026-08-26 - Esc, the
           dim background, the focus trap, the scroll lock and focus restored on
           close ([[14-UI-CONTRACTS]]). */
        _dmEditPopup = window.Popup.mount(backdrop, {
            onClose: () => {
                dmState.edit = { open: false, oldPk: null, fields: null, sectionKey: null };
            },
        });
        const first = host.querySelector("input"); if (first) first.focus();
    }

    function closeCatalogEditModal() {
        if (_dmEditPopup) { _dmEditPopup.close(null); _dmEditPopup = null; }
        else $("dmEditBackdrop").hidden = true;
        dmState.edit = { open: false, oldPk: null, fields: null, sectionKey: null };
    }

    async function saveCatalogEdit() {
        if (!dmState.edit.open) return;
        const host = $("dmEditFields");
        const inputs = host.querySelectorAll("input[data-i]");
        const newValues = {};
        inputs.forEach(inp => {
            const f = dmState.edit.fields[+inp.dataset.i];
            const raw = inp.value;
            newValues[f.key] = raw;
        });
        if (!String(newValues[dmState.section.name_col] || "").trim()) {
            toast("Name can't be empty.", true);
            return;
        }
        const r = await api().dm_update_catalog_entry(
            dmState.edit.sectionKey, dmState.edit.oldPk, newValues,
        );
        if (!r.ok) { toast(r.error || "Save failed", true); return; }
        toast("Entry updated.");
        closeCatalogEditModal();
        await dmRefreshRecords();
    }

    async function deleteCatalogRow(row) {
        if (!row) return;
        const name = row[dmState.section.name_col];
        const ok = await confirm2(
            `Delete "${name}"?`,
            `This removes one entry from the autocomplete catalog. Documents you already saved are not affected.`,
        );
        if (!ok) return;
        const r = await api().dm_delete_catalog_entry(dmState.sectionKey, row.__pk);
        if (!r.ok) { toast(r.error || "Delete failed", true); return; }
        toast(`Deleted from catalog.`);
        await dmRefreshRecords();
    }

    // ---------------------------------------------------------- bulk delete
    async function deleteSelected() {
        const keys = [...dmState.selected];
        if (!keys.length) return;
        const ok = await confirm2(
            `Delete ${keys.length} entr${keys.length === 1 ? "y" : "ies"}?`,
            dmState.sectionType === "catalog"
                ? `This removes entries from the autocomplete catalog. Documents you already saved are not affected.`
                : `This removes ${keys.length} database row${keys.length === 1 ? "" : "s"}. The PDF files on disk are left alone.`,
        );
        if (!ok) return;
        let r;
        if (dmState.sectionType === "catalog") {
            // Find the matching rows in dmState.rows and submit their pk dicts.
            const set = new Set(keys);
            const pkDicts = dmState.rows
                .filter(row => set.has(pkKey(row.__pk)))
                .map(row => row.__pk);
            r = await api().dm_delete_catalog_entries(dmState.sectionKey, pkDicts);
        } else {
            r = await api().dm_delete_records(dmState.sectionKey, keys);
        }
        if (!r.ok) { toast(r.error || "Bulk delete failed", true); return; }
        toast(`Deleted ${r.deleted} row${r.deleted === 1 ? "" : "s"}.`);
        dmState.selected.clear();
        await dmRefreshRecords();
    }

    function confirm2(title, body) {
        // The shared showModal in app.js expects {title, body, buttons:[{label,value,cls}]}.
        // We map our true/false intent onto value 'ok' / 'cancel' so it matches
        // app.js's Esc-handler convention.
        if (window.showModal) {
            return window.showModal({
                title, body,
                buttons: [
                    { label: "Cancel",  value: "cancel", cls: "ghost" },
                    { label: "Confirm", value: "ok",    cls: "primary" },
                ],
            }).then(v => v === "ok");
        }
        return Promise.resolve(window.confirm(`${title}\n\n${body}`));
    }

    // ---------------------------------------------------------- duplicates tab
    function rebuildDupCatalogSelect() {
        const sel = $("dmDupCatalog");
        if (!sel) return;
        sel.innerHTML = dmState.catalogs.map(c =>
            `<option value="${escapeAttr(c.key)}">${escapeHtml(c.label)}</option>`
        ).join("");
        if (dmState.dup.sectionKey) sel.value = dmState.dup.sectionKey;
        else if (dmState.sectionKey && sectionKind(dmState.sectionKey) === "catalog") {
            sel.value = dmState.sectionKey;
        }
    }

    async function runDuplicateScan() {
        const sel = $("dmDupCatalog");
        const key = sel.value;
        const threshold = (+$("dmDupThreshold").value) / 100;
        $("dmDupResults").innerHTML = `<div class="dm-empty">Scanning…</div>`;
        const r = await api().dm_find_catalog_duplicates(key, threshold);
        if (!r.ok) {
            $("dmDupResults").innerHTML =
                `<div class="dm-empty">Scan failed: ${escapeHtml(r.error || "unknown")}</div>`;
            return;
        }
        dmState.dup = { sectionKey: key, threshold: r.threshold, clusters: r.clusters };
        $("dmDupSummary").textContent =
            r.count === 0
                ? "No duplicate groups at this threshold."
                : `${r.count} duplicate group${r.count === 1 ? "" : "s"} found.`;
        renderDuplicateClusters();
    }

    function renderDuplicateClusters() {
        const host = $("dmDupResults");
        const sec = dmState.catalogs.find(c => c.key === dmState.dup.sectionKey);
        if (!sec) { host.innerHTML = ""; return; }
        if (!dmState.dup.clusters.length) {
            host.innerHTML = `<div class="dm-empty">Nothing to merge - looks clean at this strictness.</div>`;
            return;
        }
        const variantCols = sec.variant_cols;
        const html = dmState.dup.clusters.map((cl, i) => {
            const rows = cl.variants.map((v, j) => {
                const fieldCells = variantCols.map(vc => {
                    const val = v.fields ? (v.fields[vc.key] != null ? String(v.fields[vc.key]) : "") : "";
                    const formatted = (vc.kind === "money") ? fmtMoney(val) : val;
                    return `<td class="dm-cell-${vc.kind}">${escapeHtml(formatted)}</td>`;
                }).join("");
                const sim = (v.similarity * 100).toFixed(0);
                return `<tr>`
                    + `<td><input type="radio" name="dm-dup-canon-${i}" value="${j}" ${j === 0 ? "checked" : ""}></td>`
                    + `<td><strong>${escapeHtml(v.name)}</strong></td>`
                    + fieldCells
                    + `<td class="dm-cell-count">${v.use_count}</td>`
                    + `<td class="subtle">${sim}%</td>`
                    + `</tr>`;
            }).join("");
            return `<div class="dm-dup-cluster" data-cluster="${i}">`
                + `<div class="dm-dup-cluster-head">`
                +   `<strong>Group ${i + 1}</strong> - ${cl.variants.length} variants, ${cl.total_use_count} total uses`
                +   `<button class="link" data-act="merge" data-cluster="${i}">Merge into selected canonical →</button>`
                + `</div>`
                + `<table class="dm-table dm-dup-table"><thead><tr>`
                +   `<th>Canon.</th><th>Name</th>`
                +   variantCols.map(vc => `<th>${escapeHtml(vc.header)}</th>`).join("")
                +   `<th>Used</th><th>Match</th>`
                + `</tr></thead><tbody>${rows}</tbody></table></div>`;
        }).join("");
        host.innerHTML = html;
        host.querySelectorAll("button[data-act='merge']").forEach(b => {
            b.addEventListener("click", () => mergeCluster(+b.dataset.cluster));
        });
    }

    async function mergeCluster(idx) {
        const cl = dmState.dup.clusters[idx];
        const sec = dmState.catalogs.find(c => c.key === dmState.dup.sectionKey);
        if (!cl || !sec) return;
        const radio = document.querySelector(
            `input[name='dm-dup-canon-${idx}']:checked`
        );
        if (!radio) { toast("Pick a canonical variant first.", true); return; }
        const canonIdx = +radio.value;
        const canon = cl.variants[canonIdx];
        const absorbed = cl.variants.filter((_, j) => j !== canonIdx);
        const ok = await confirm2(
            `Merge ${absorbed.length} variant${absorbed.length === 1 ? "" : "s"} into "${canon.name}"?`,
            `The other variants will be deleted from the catalog and their usage counts (${absorbed.reduce((a, v) => a + v.use_count, 0)} total) summed into the canonical entry. Saved documents are not changed.`,
        );
        if (!ok) return;

        // Build canonical values payload - name + variant fields.
        const canonValues = { [sec.name_col]: canon.name };
        for (const vc of sec.variant_cols) {
            canonValues[vc.key] = canon.fields ? canon.fields[vc.key] : "";
        }
        const r = await api().dm_merge_catalog_entries(
            dmState.dup.sectionKey, canonValues, absorbed.map(v => v.pk),
        );
        if (!r.ok) { toast(r.error || "Merge failed", true); return; }
        toast(`Merged ${r.absorbed_rows} variants (+${r.absorbed_use_count} uses).`);
        await runDuplicateScan();
        // Refresh Browse pane too in case the user opens it next.
        if (dmState.sectionKey === dmState.dup.sectionKey) await dmRefreshRecords();
    }

    // ---------------------------------------------------------- backup / restore
    async function doBackup() {
        const status = $("dmBackupStatus");
        status.textContent = "Picking a folder…";
        const picked = await api().dm_pick_folder("Choose where to save the backup zip");
        if (picked && picked.cancelled) { status.textContent = ""; return; }
        if (!picked || !picked.ok) {
            status.textContent = picked && picked.error ? picked.error : "Folder pick failed";
            return;
        }
        status.textContent = "Creating archive…";
        const r = await api().dm_create_backup(picked.path);
        if (!r.ok) { status.textContent = "Backup failed: " + (r.error || "unknown"); return; }
        const mb = (r.size_bytes / 1024 / 1024).toFixed(2);
        status.innerHTML = `<strong>Done.</strong> ${r.files} files, ${mb} MB - `
            + `<button class="link" id="dmRevealBackup">${escapeHtml(r.path)}</button>`;
        const reveal = $("dmRevealBackup");
        if (reveal) reveal.onclick = () => api().open_path(r.path);
    }

    async function doRestore() {
        const status = $("dmRestoreStatus");
        status.textContent = "Picking an archive…";
        const picked = await api().dm_pick_file(
            "Choose a backup .zip to restore from",
            ["Backup archive (*.zip)"],
        );
        if (picked && picked.cancelled) { status.textContent = ""; return; }
        if (!picked || !picked.ok) {
            status.textContent = picked && picked.error ? picked.error : "Archive pick failed";
            return;
        }
        const ok = await confirm2(
            "Restore from backup?",
            "The current database will be overwritten by the archive's database. A safety copy of the current database is saved as data.db.before-restore.bak first. Document PDFs already on disk are kept; archive files are layered on top.",
        );
        if (!ok) { status.textContent = ""; return; }
        status.textContent = "Restoring…";
        const r = await api().dm_restore_backup(picked.path, false);
        if (!r.ok) { status.textContent = "Restore failed: " + (r.error || "unknown"); return; }
        const summary = r.restored || {};
        status.innerHTML = `<strong>Restored.</strong> `
            + `db=${summary.db ? "yes" : "no"}, settings=${summary.settings ? "yes" : "no"}, `
            + `doc files=${summary.doc_files || 0}. Re-open a section to refresh.`;
        dmState.catalogs = []; dmState.documents = [];
        if (dmState.sectionKey) {
            const [cats, docs] = await Promise.all([
                api().dm_list_catalogs(), api().dm_list_sections(),
            ]);
            dmState.catalogs = cats; dmState.documents = docs;
            await dmLoadSection(dmState.sectionKey);
        }
    }

    // ---------------------------------------------------------- escape helpers
    function escapeHtml(s) {
        s = (s == null) ? "" : String(s);
        return s.replace(/[&<>"']/g, m => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;",
            '"': "&quot;", "'": "&#39;",
        }[m]));
    }
    function escapeAttr(s) { return escapeHtml(s); }

    // ---------------------------------------------------------- wiring
    // ---------------------------------------------------------- Clients tab
    let _clClients = [];

    function clMoney(n) {
        const v = Number(n) || 0;
        return "$" + v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    function clInitials(name) {
        const p = String(name || "").trim().split(/\s+/).filter(Boolean);
        if (!p.length) return "?";
        return (p.length === 1 ? p[0].slice(0, 2) : p[0][0] + p[1][0]).toUpperCase();
    }

    async function loadDmClients() {
        const tb = $("dmClTbody");
        if (tb) tb.innerHTML = `<tr><td colspan="7" class="dm-cl-loading">Loading…</td></tr>`;
        try { _clClients = (await api().client_list()) || []; }
        catch (e) { _clClients = []; }
        renderDmClients($("dmClSearch") ? $("dmClSearch").value : "");
    }

    function renderDmClients(filter) {
        const tb = $("dmClTbody");
        if (!tb) return;
        const q = (filter || "").trim().toLowerCase();
        const items = q ? _clClients.filter(c => c.name.toLowerCase().includes(q)) : _clClients;
        const cnt = $("dmClCount");
        if (cnt) cnt.textContent = `${items.length} client${items.length === 1 ? "" : "s"}`;
        if (!items.length) {
            tb.innerHTML = `<tr><td colspan="7" class="dm-cl-loading">No clients${q ? " match your search" : " yet"}.</td></tr>`;
            return;
        }
        tb.innerHTML = items.map(c => `
            <tr data-name="${escapeAttr(c.name)}">
                <td><span class="dm-cl-cell"><span class="dm-cl-avatar">${escapeHtml(clInitials(c.name))}</span>${escapeHtml(c.name)}</span></td>
                <td class="dm-cl-num">${c.quotes}</td>
                <td class="dm-cl-num">${c.invoices}</td>
                <td class="dm-cl-num">${c.dns}</td>
                <td class="dm-cl-num dm-cl-money">${c.invoiced_total ? clMoney(c.invoiced_total) : "-"}</td>
                <td class="dm-cl-date">${escapeHtml((fmtDate(c.last_activity) || "").slice(0, 10) || "-")}</td>
                <td class="dm-cl-actions">
                    <button class="link" data-act="ren">Rename</button>
                    <span class="dm-cl-dot">·</span>
                    <button class="link danger-text" data-act="del">Delete</button>
                </td>
            </tr>`).join("");
        tb.querySelectorAll("button[data-act='ren']").forEach(b =>
            b.addEventListener("click", () => clRename(b.closest("tr").dataset.name)));
        tb.querySelectorAll("button[data-act='del']").forEach(b =>
            b.addEventListener("click", () => clDelete(b.closest("tr").dataset.name)));
    }

    async function clRename(name) {
        const newName = await clPrompt({
            title: "Rename client",
            msg: `Change the display name for "${name}". Its previous name stays as an alternative name and saved documents keep their original wording.`,
            value: name,
            hint: "Manage contacts, payment preferences and alternative names in Clients Master Data.",
        });
        if (newName == null) return;
        const r = await api().client_rename(name, newName);
        if (!r || !r.ok) { toast((r && r.error) || "Rename failed", true); return; }
        if (r.noop) { toast("No change."); return; }
        // No document count: a rename no longer rewrites saved documents.
        toast(`Renamed to "${r.new}". Saved documents keep their original wording.`);
        await loadDmClients();
    }

    async function clDelete(name) {
        const c = _clClients.find(x => x.name === name);
        const docs = c ? c.doc_count : 0;
        const body = `Archive "${name}" from normal client suggestions? Saved documents remain available. You can restore this client in Clients Master Data.`;
        const ok = await confirm2(`Archive "${name}"?`, body);
        if (!ok) return;
        const r = await api().client_delete(name);
        if (!r || !r.ok) { toast((r && r.error) || "Delete failed", true); return; }
        toast(`Archived "${name}". Restore it in Clients Master Data.`);
        await loadDmClients();
    }

    async function clAdd() {
        const name = await clPrompt({
            title: "Add client", msg: "Add a new client to the autocomplete list.", value: "", hint: "",
        });
        if (name == null) return;
        const r = await api().client_add(name);
        if (!r || !r.ok) { toast((r && r.error) || "Add failed", true); return; }
        toast(`Added "${r.name}".`);
        await loadDmClients();
    }

    /* Small text-prompt modal - resolves to the trimmed string, or null.
       Shared behaviour, markup in index.html ([[14-UI-CONTRACTS]]): Esc and the
       dim background are the contract's, so only Enter-to-accept is wired here.
       An empty answer resolves null, which every caller reads as "cancelled". */
    function clPrompt({ title, msg, value, hint }) {
        return new Promise((resolve) => {
            const bd = $("dmClPromptBackdrop");
            $("dmClPromptTitle").textContent = title || "";
            $("dmClPromptMsg").textContent = msg || "";
            $("dmClPromptHint").textContent = hint || "";
            const inp = $("dmClPromptInput");
            const okBtn = $("dmClPromptOk"), cancelBtn = $("dmClPromptCancel");
            inp.value = value || "";
            let answer = null;
            const h = window.Popup.mount(bd, {
                onClose: () => {
                    okBtn.onclick = cancelBtn.onclick = inp.onkeydown = null;
                    resolve(answer);
                },
            });
            okBtn.onclick = () => { answer = inp.value.trim() || null; h.close(null); };
            cancelBtn.onclick = () => h.close(null);
            inp.onkeydown = (e) => {
                if (e.key === "Enter") { e.preventDefault(); okBtn.onclick(); }
            };
            setTimeout(() => { inp.focus(); inp.select(); }, 30);
        });
    }

    function bindOnce() {
        bindTabs();

        // Clients tab.
        const clAddBtn = $("btnDmClAdd"); if (clAddBtn) clAddBtn.addEventListener("click", clAdd);
        const clSearch = $("dmClSearch");
        if (clSearch) clSearch.addEventListener("input", () => renderDmClients(clSearch.value));

        // Sections quick-filter (sidebar).
        const secFilter = $("dmSectionFilter");
        if (secFilter) {
            secFilter.addEventListener("input", () => {
                dmState.sectionFilter = secFilter.value || "";
                renderSidebar();
            });
            secFilter.addEventListener("keydown", (e) => {
                if (e.key === "Escape") {
                    secFilter.value = ""; dmState.sectionFilter = ""; renderSidebar();
                } else if (e.key === "Enter") {
                    // Jump to the first matching section.
                    const first = $("dmSectionList").querySelector(".dm-section-btn[data-section]");
                    if (first) dmLoadSection(first.dataset.section);
                }
            });
        }
        const grpToggle = $("dmGroupsToggle");
        if (grpToggle) grpToggle.addEventListener("click", toggleAllGroups);

        $("dmSearch").addEventListener("input", debounce(() => {
            dmState.search = $("dmSearch").value;
            dmRefreshRecords();
        }, 220));
        $("dmYear").addEventListener("change", () => {
            dmState.year = $("dmYear").value; dmRefreshRecords();
        });
        $("dmMonth").addEventListener("change", () => {
            dmState.month = $("dmMonth").value; dmRefreshRecords();
        });
        $("dmClient").addEventListener("change", () => {
            dmState.client = $("dmClient").value; dmRefreshRecords();
        });
        $("dmBulkDelete").addEventListener("click", deleteSelected);
        $("btnDmBackup").addEventListener("click", doBackup);
        $("btnDmRestore").addEventListener("click", doRestore);

        // Catalog edit modal wiring
        $("dmEditCancel").addEventListener("click", closeCatalogEditModal);
        $("dmEditSave").addEventListener("click", saveCatalogEdit);
        // Esc and the dim background belong to the shared contract now; only
        // this dialog's own Ctrl+Enter shortcut stays here.
        document.addEventListener("keydown", (e) => {
            if (!dmState.edit.open) return;
            if (e.key === "Enter" && e.ctrlKey) saveCatalogEdit();
        });

        // Duplicates tab wiring
        const slider = $("dmDupThreshold");
        if (slider) slider.addEventListener("input", () => {
            $("dmDupThresholdLabel").textContent = slider.value + "%";
        });
        const scanBtn = $("btnDmDupScan");
        if (scanBtn) scanBtn.addEventListener("click", runDuplicateScan);
    }

    function debounce(fn, ms) {
        let h = null;
        return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); };
    }

    document.addEventListener("DOMContentLoaded", bindOnce);
})();
