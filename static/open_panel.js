// ============================================================
// open_panel.js  -  Phase M.6
// Persistent right-side "Open" panel for every editor (PO/QUT/DN/INV).
//
// What it does
// ------------
// At boot, lifts the existing year/month/chips/list elements out of each
// standalone *OpenView and drops them into the matching <div class="op-pane">
// inside #openPanel. Existing render code keeps targeting the same IDs, so
// nothing in quote.js / dn.js / inv.js / app.js needs to know that
// the controls moved - they just live in a new place.
//
// Then it watches editor views for visibility changes (MutationObserver on
// `hidden`) and:
//   - shows #openPanel + the right pane when an editor becomes visible
//   - calls the section's refresh() the first time it activates
//   - hides the panel when no editor is visible
//
// Search + collapse + topbar refresh button round it out.
// ============================================================
(function () {
    "use strict";

    // ---- per-section descriptors ----
    // { editorViewId, openViewId, paneSelector,
    //   title,           // panel header title
    //   refreshFn,       // resolved from window at call-time so load order doesn't matter
    //   ids: { month, year, chips: [...], list } // which DOM nodes to move
    // }
    // Phase M.8 - chip rows are now { id, kind, label } so the panel can wrap
    // them with a small "Type" / "Client" tag for clarity. kind drives the
    // visual treatment (type pills get accent colour, client pills stay neutral).
    const SECTIONS = {
        po:  {
            editorViewId: "editorView",
            openViewId:   "openView",
            title:        "Purchase Orders",
            refreshFnName: "refreshPoList",
            ids: { month: "openMonth", year: "openYear", chips: [], list: "poList" },
        },
        qut: {
            editorViewId: "quoteView",
            openViewId:   "quoteOpenView",
            title:        "Quotations",
            refreshFnName: "refreshQuoteList",
            // Phase M.8d - TYPE filtering moved to the horizontal type bar
            // above the editor; the panel only filters by CLIENT now.
            ids: { month: "qOpenMonth", year: "qOpenYear",
                   chips: [
                       { id: "qClientChips", kind: "client", label: "Client" },
                   ], list: "qList" },
        },
        dn:  {
            editorViewId: "dnView",
            openViewId:   "dnOpenView",
            title:        "Delivery Notes",
            refreshFnName: "refreshDnList",
            ids: { month: "dnOpenMonth", year: "dnOpenYear",
                   chips: [
                       { id: "dnClientChips", kind: "client", label: "Client" },
                   ], list: "dnList" },
        },
        inv: {
            editorViewId: "invView",
            openViewId:   "invOpenView",
            title:        "Invoices",
            refreshFnName: "refreshInvList",
            // Phase M.8d - TYPE filtering moved to the type bar; client only.
            ids: { month: "invOpenMonth", year: "invOpenYear",
                   chips: [
                       { id: "invClientChips", kind: "client", label: "Client" },
                   ], list: "invList" },
        },
    };

    // ---- DOM ----
    let panelEl, expandTabEl, titleEl, searchEl, searchClearEl, refreshBtnEl, collapseBtnEl;
    let activeSection = null;
    const refreshed = new Set();         // sections refreshed at least once this session
    const STORAGE_KEY = "openPanelCollapsed";

    function isCollapsed() {
        try { return localStorage.getItem(STORAGE_KEY) === "1"; } catch { return false; }
    }
    function setCollapsed(v) {
        try { localStorage.setItem(STORAGE_KEY, v ? "1" : "0"); } catch {}
        applyCollapsed();
    }
    function applyCollapsed() {
        const v = isCollapsed();
        document.body.classList.toggle("op-collapsed", v);
        if (panelEl)     panelEl.hidden     = v || activeSection == null;
        if (expandTabEl) expandTabEl.hidden = !v || activeSection == null;
    }

    // ---- move existing Open controls into the pane ----
    function liftSection(name) {
        const desc = SECTIONS[name];
        if (!desc) return;
        const pane = panelEl.querySelector(`.op-pane[data-section="${name}"]`);
        if (!pane) return;
        if (pane.dataset.lifted === "1") return;

        const openView = document.getElementById(desc.openViewId);

        // Pane is split into a fixed top (controls + chips) and a scrollable
        // list area so the user can always reach the list without losing the
        // chips, even when many clients pile up.
        const fixed = document.createElement("div");
        fixed.className = "op-pane-fixed";
        const scrollArea = document.createElement("div");
        scrollArea.className = "op-pane-list";

        // 1. Year + Month controls (compact two-column row).
        const monthEl = document.getElementById(desc.ids.month);
        const yearEl  = document.getElementById(desc.ids.year);
        if (monthEl || yearEl) {
            const ctl = document.createElement("div");
            ctl.className = "op-controls";
            if (monthEl) {
                const lbl = document.createElement("label");
                lbl.className = "op-ctrl-lbl";
                lbl.innerHTML = `<span>Month</span>`;
                const wrap = monthEl.closest("label");
                lbl.appendChild(monthEl);
                if (wrap && wrap.parentElement) wrap.remove();
                ctl.appendChild(lbl);
            }
            if (yearEl) {
                const lbl = document.createElement("label");
                lbl.className = "op-ctrl-lbl";
                lbl.innerHTML = `<span>Year</span>`;
                const wrap = yearEl.closest("label");
                lbl.appendChild(yearEl);
                if (wrap && wrap.parentElement) wrap.remove();
                ctl.appendChild(lbl);
            }
            fixed.appendChild(ctl);
        }

        // 2. Chip rows - each wrapped in an .op-filter-row with a small tag
        //    label so Type vs Client is instantly readable. Old wrappers are
        //    discarded so the chips inherit the new compact layout cleanly.
        //    Phase M.8b - every row also gets a "+N" pill at the end that
        //    expands the row in place when the chips overflow a single line.
        //    The pill stays hidden when there's no overflow.
        desc.ids.chips.forEach(chipDesc => {
            const c = document.getElementById(chipDesc.id);
            if (!c) return;
            const oldStack = c.parentElement && c.parentElement.classList.contains("filter-chips-stack")
                ? c.parentElement : null;
            const row = document.createElement("div");
            row.className = "op-filter-row";
            row.dataset.kind = chipDesc.kind;
            const tag = document.createElement("span");
            tag.className = "op-filter-tag";
            tag.innerHTML = `${_chipKindIcon(chipDesc.kind)}<span>${chipDesc.label}</span>`;
            // Phase M.8c/d - chip strip uses `display: contents` (CSS) so chips
            // become flex-wrap siblings of the tag. When the first row runs
            // out of horizontal space, the next chip wraps to a new line at
            // the LEFT edge of the row - i.e. under the tag. All chips are
            // always shown (no clipping / no show-more toggle).
            c.classList.add("op-chip-strip");
            row.appendChild(tag);
            row.appendChild(c);
            fixed.appendChild(row);
            if (oldStack && oldStack.parentElement && oldStack.children.length === 0) oldStack.remove();
        });

        // 3. List container goes into the scrollable area.
        const listEl = document.getElementById(desc.ids.list);
        if (listEl) scrollArea.appendChild(listEl);

        pane.appendChild(fixed);
        pane.appendChild(scrollArea);

        // 4. Standalone Open view is now empty - hide it forever.
        if (openView) {
            openView.hidden = true;
            openView.classList.add("op-replaced");
        }

        pane.dataset.lifted = "1";
    }

    // Tiny inline icon for the chip-row tag - keeps the visual hierarchy
    // legible without a per-section font dependency.
    function _chipKindIcon(kind) {
        if (kind === "type") {
            return `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><line x1="7" y1="7" x2="7.01" y2="7"/></svg>`;
        }
        if (kind === "client") {
            return `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>`;
        }
        return "";
    }

    function liftAll() {
        Object.keys(SECTIONS).forEach(liftSection);
    }

    // ---- show/hide panes ----
    function showSection(name) {
        if (!panelEl) return;
        activeSection = name;
        panelEl.dataset.active = name || "";
        panelEl.querySelectorAll(".op-pane").forEach(p => {
            p.hidden = (p.dataset.section !== name);
        });
        titleEl.textContent = (SECTIONS[name] && SECTIONS[name].title) || "Files";
        // Reset search to empty so a new section starts clean.
        if (searchEl) {
            searchEl.value = "";
            if (searchClearEl) searchClearEl.hidden = true;
            applySearchFilter("");
        }
        // Phase M.8b - `op-active` says the panel is rendering content for an
        // editor; CSS uses this to add right-padding to .view + .type-rail
        // ONLY when there's actually a panel on screen (no padding on the
        // dashboard / hub views, which were getting blank right strips).
        document.body.classList.add("op-active");
        applyCollapsed();
        // First-time activate -> refresh data.
        if (name && !refreshed.has(name)) {
            triggerRefresh(name);
            refreshed.add(name);
        }
        highlightActivePanelRow();   // Phase W - reflect the open doc on section change
    }
    function hidePanel() {
        activeSection = null;
        if (panelEl)     panelEl.hidden     = true;
        if (expandTabEl) expandTabEl.hidden = true;
        document.body.classList.remove("op-collapsed");
        document.body.classList.remove("op-active");
    }

    function triggerRefresh(name) {
        const desc = SECTIONS[name];
        if (!desc) return;
        const fn = window[desc.refreshFnName];
        if (typeof fn === "function") {
            try { fn(); } catch (e) { console.warn("Open panel refresh failed", name, e); }
        }
    }

    // Phase X.26 - debounced refresh of the ACTIVE section's list. Editors call
    // window.refreshOpenPanel() after any mutation (save / sign / new / delete)
    // and the panel calls it when its own month/year filter changes, so the
    // list is always current without hitting the manual refresh button. The
    // debounce collapses a burst (e.g. save → reset → re-init) into one fetch.
    let _autoRefreshTimer = null;
    function refreshActive() {
        if (!activeSection) return;
        clearTimeout(_autoRefreshTimer);
        _autoRefreshTimer = setTimeout(() => {
            if (activeSection) triggerRefresh(activeSection);
        }, 120);
    }
    window.refreshOpenPanel = refreshActive;

    // ---- search (typeahead across the active pane's list cards) ----
    function applySearchFilter(needle) {
        if (!activeSection) return;
        const desc = SECTIONS[activeSection];
        if (!desc) return;
        const list = document.getElementById(desc.ids.list);
        if (!list) return;
        const q = String(needle || "").trim().toLowerCase();
        const rows = list.querySelectorAll(".po-row");
        let visible = 0;
        rows.forEach(r => {
            if (!q) { r.hidden = false; visible++; return; }
            const text = (r.textContent || "").toLowerCase();
            const hit = text.indexOf(q) >= 0;
            r.hidden = !hit;
            if (hit) visible++;
        });
        // Day separators are meaningless while searching across days - hide them
        // when a query is active, restore them when it's cleared.
        list.querySelectorAll(".panel-day-sep").forEach(s => { s.hidden = !!q; });
        // Show empty-state hint when search hides everything.
        let empty = list.querySelector(".op-search-empty");
        if (q && visible === 0) {
            if (!empty) {
                empty = document.createElement("div");
                empty.className = "empty-state op-search-empty";
                empty.innerHTML = `<div class="title">No matches for “${escapeHtml(q)}”</div>
                                   <div class="hint">Try a different month or clear the search.</div>`;
                list.appendChild(empty);
            } else {
                empty.querySelector(".title").textContent = `No matches for “${q}”`;
                empty.hidden = false;
            }
        } else if (empty) {
            empty.hidden = true;
        }
    }
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    }

    // ---- Phase W - highlight the document open/selected in the panel ----
    // The open doc number comes from window.__currentEditorDoc (set by each
    // loadXForEdit) OR from the last row the user clicked (`_openDocNo`). We
    // flag the matching .po-row (data-edit = doc number) across the WHOLE panel
    // with .is-open - only the active pane is visible, and doc numbers are
    // unique per type, so this is safe and avoids any active-section timing race.
    let _openDocNo = null;
    function _currentOpenNo() {
        const cur = window.__currentEditorDoc;
        if (cur && cur.no) return String(cur.no);
        return _openDocNo != null ? String(_openDocNo) : null;
    }
    function highlightActivePanelRow() {
        if (!panelEl) return;
        const no = _currentOpenNo();
        panelEl.querySelectorAll(".po-row.is-open").forEach(r => r.classList.remove("is-open"));
        if (!no) return;
        panelEl.querySelectorAll(".po-row[data-edit]").forEach(r => {
            if (r.dataset.edit === no) r.classList.add("is-open");
        });
    }
    /* "Nothing is open any more" (2026-10-01).

       `_openDocNo` is a LATCH: it was set on every row click and never cleared,
       and because `_currentOpenNo()` falls back to it, it outlived the thing it
       was standing in for. Pressing New sets `__currentEditorDoc = null`, which
       is the authoritative "no document is open" - but the fallback answered
       with the last row clicked, so the old document stayed highlighted next to
       a brand-new empty editor. Waleed hit this immediately after the July
       open-document fix. The accessor in app.js calls this whenever
       `__currentEditorDoc` is set to null, so the latch dies with it. */
    function clearActivePanelRow() {
        _openDocNo = null;
        highlightActivePanelRow();
    }

    // Immediate highlight the moment a row is clicked (the doc you're selecting),
    // independent of any async load/section plumbing.
    function _onPanelRowClick(e) {
        const row = e.target.closest && e.target.closest(".po-row[data-edit]");
        if (!row || !panelEl.contains(row)) return;
        _openDocNo = row.dataset.edit;
        panelEl.querySelectorAll(".po-row.is-open").forEach(r => { if (r !== row) r.classList.remove("is-open"); });
        row.classList.add("is-open");
    }

    // ---- watch view visibility ----
    function activeEditor() {
        for (const [name, desc] of Object.entries(SECTIONS)) {
            const v = document.getElementById(desc.editorViewId);
            if (v && !v.hidden) return name;
        }
        return null;
    }
    function syncFromViews() {
        const name = activeEditor();
        if (name) showSection(name);
        else hidePanel();
    }
    function watchViews() {
        const obs = new MutationObserver(() => syncFromViews());
        Object.values(SECTIONS).forEach(desc => {
            const v = document.getElementById(desc.editorViewId);
            if (v) obs.observe(v, { attributes: true, attributeFilter: ["hidden"] });
        });
        // Initial sync (in case an editor is already visible).
        syncFromViews();
    }

    // ---- wire toolbar + search ----
    function wireUI() {
        collapseBtnEl.onclick = () => setCollapsed(true);
        expandTabEl.onclick   = () => setCollapsed(false);
        refreshBtnEl.onclick  = () => activeSection && triggerRefresh(activeSection);
        searchEl.addEventListener("input", () => {
            const v = searchEl.value;
            searchClearEl.hidden = !v;
            applySearchFilter(v);
        });
        searchClearEl.onclick = () => {
            searchEl.value = "";
            searchClearEl.hidden = true;
            applySearchFilter("");
            searchEl.focus();
        };
        searchEl.addEventListener("keydown", (e) => {
            if (e.key === "Escape") {
                searchEl.value = "";
                searchClearEl.hidden = true;
                applySearchFilter("");
            }
        });
        // Re-apply search after any list re-render (refresh / chip filter
        // change). A simple MutationObserver on the active pane's list.
        const listObs = new MutationObserver(() => {
            if (searchEl.value) applySearchFilter(searchEl.value);
            highlightActivePanelRow();   // Phase W - keep the open-doc highlight after re-renders
        });
        Object.values(SECTIONS).forEach(desc => {
            const list = document.getElementById(desc.ids.list);
            if (list) listObs.observe(list, { childList: true });
        });
        // Phase X.26 - the month/year selectors were lifted INTO this panel, so
        // it owns their change events: auto-refresh the active list when the
        // user switches month/year (previously needed a manual refresh click).
        Object.values(SECTIONS).forEach(desc => {
            ["month", "year"].forEach(k => {
                const el = document.getElementById(desc.ids[k]);
                if (el) el.addEventListener("change", refreshActive);
            });
        });
    }

    // ---- boot ----
    function init() {
        panelEl        = document.getElementById("openPanel");
        expandTabEl    = document.getElementById("opExpandTab");
        titleEl        = document.getElementById("opTitle");
        searchEl       = document.getElementById("opSearch");
        searchClearEl  = document.getElementById("opSearchClear");
        refreshBtnEl   = document.getElementById("opRefreshBtn");
        collapseBtnEl  = document.getElementById("opCollapseBtn");
        if (!panelEl || !titleEl || !searchEl) return;
        liftAll();
        wireUI();
        // Phase W - immediate highlight of the clicked (selected) document row.
        panelEl.addEventListener("click", _onPanelRowClick);
        watchViews();
    }
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }

    // expose a tiny API for other modules
    /* Bring the open document's row into view (2026-10-01).

       Opening a July document from Needs Attention in September left the panel
       on September: the editor showed the right document and the panel pointed
       at a different month, so Waleed had to go and find it. The month switch
       belongs to the editors (they own the month selects - `revealDocInPanel`
       in app.js); the SCROLL belongs here, because the panel owns its
       scroll container.

       The list is rendered by an async refresh, so the row may not exist yet.
       Rather than guess a delay, poll a few animation frames and give up
       quietly - a missing row means the document is genuinely not in the
       month on screen, which is not an error worth a toast. */
    function revealRow(no, tries) {
        if (!panelEl || !no) return;
        const want = String(no);
        const left = tries == null ? 24 : tries;
        const row = Array.from(panelEl.querySelectorAll(".po-row[data-edit]"))
            .find(r => r.dataset.edit === want);
        if (!row) {
            if (left > 0) requestAnimationFrame(() => revealRow(want, left - 1));
            return;
        }
        highlightActivePanelRow();
        const box = row.closest(".op-pane-list");
        if (!box) { row.scrollIntoView({ block: "nearest" }); return; }
        // Centre it in the scroller rather than `scrollIntoView`, which on a
        // nested scroller also scrolls the page behind the panel.
        const r = row.getBoundingClientRect();
        const b = box.getBoundingClientRect();
        const target = box.scrollTop + (r.top - b.top) - (b.height - r.height) / 2;
        box.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
    }

    window.OpenPanel = {
        refresh: (name) => triggerRefresh(name || activeSection),
        show:    showSection,
        hide:    hidePanel,
        section: () => activeSection,
        markActive: highlightActivePanelRow,   // Phase W - re-highlight the open doc's row
        clearActive: clearActivePanelRow,      // nothing is open: drop the highlight AND the latch
        revealRow,                             // scroll the open document's row into view
        // Called by the legacy topbar Open buttons - uncollapse, refresh,
        // and focus the search input. There is no "Open view" any more.
        focusSearch: () => {
            try { localStorage.setItem(STORAGE_KEY, "0"); } catch {}
            applyCollapsed();
            if (activeSection) triggerRefresh(activeSection);
            if (searchEl) setTimeout(() => { searchEl.focus(); searchEl.select(); }, 60);
        },
    };
})();
