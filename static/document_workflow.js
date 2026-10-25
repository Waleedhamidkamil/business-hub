/* PO/DN/invoice delivery and payment workflow popup.
   Relationships are read from po_fulfillment through document_workflow().
   All writes are delegated to the existing smart PO link pickers. */
(function () {
    const api = () => window.pywebview.api;
    const esc = window.esc || ((value) => String(value == null ? "" : value));
    const toast = window.toast || (() => {});
    const contextGetters = {
        po: "getPoWorkflowContext",
        dn: "getDnWorkflowContext",
        inv: "getInvWorkflowContext",
    };
    const buttonIds = {
        po: "btnPoWorkflow",
        dn: "btnDnWorkflow",
        inv: "btnInvWorkflow",
    };
    const loaders = {
        po: "loadForEdit",
        dn: "loadDnForEdit",
        inv: "loadInvForEdit",
    };

    /* Both popups here are shared Popups since 2026-08-26: Esc, the dim
       background, the X, the focus trap and the scroll lock come with the box
       ([[14-UI-CONTRACTS]]). `requestSeq` still guards a slow bridge reply
       landing in a popup the user has moved on from. */
    let modal = null;
    let requestSeq = 0;

    function close() {
        requestSeq += 1;
        if (modal) { modal.close(null); modal = null; }
    }

    function shell(kind, docNo) {
        close();
        const handle = window.Popup.open({
            title: "Workflow",
            badge: String(kind || "").toUpperCase(),
            subtitle: `Delivery and payment for ${docNo}`,
            size: "lg",
            dir: "ltr",                       // the shell is RTL; this view is not
            className: "doc-workflow-modal",
            body: "Loading workflow...",
        });
        modal = handle;
        handle.result.then(() => { if (modal === handle) modal = null; });
        return handle;
    }

    function icon(kind) {
        if (kind === "po") {
            return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 2h9l4 4v16H6z"/><path d="M14 2v5h5M9 12h7M9 16h7"/></svg>`;
        }
        if (kind === "dn") {
            return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 6h11v10H3zM14 10h4l3 3v3h-7z"/><circle cx="7" cy="18" r="2"/><circle cx="18" cy="18" r="2"/></svg>`;
        }
        return `<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/></svg>`;
    }

    function countLabel(count) {
        return `${count} item${count === 1 ? "" : "s"}`;
    }

    function invoiceLinksHtml(node, poNo) {
        if (node.kind !== "inv") return "";
        const dns = Array.isArray(node.delivery_notes) ? node.delivery_notes : [];
        const directCount = Math.max(0, Number(node.direct_item_count || 0));
        const contributions = dns.map((dn) =>
            `<span class="doc-workflow-contribution"><b>${esc(dn.no)}</b>`
            + `<small>${countLabel(Number(dn.item_count || 0))}</small></span>`).join("");
        const direct = directCount
            ? `<span class="doc-workflow-contribution is-direct"><b>Direct to PO</b><small>${countLabel(directCount)}</small></span>`
            : "";
        const unlinked = !poNo
            ? `<span class="doc-workflow-contribution is-unlinked"><b>No PO link yet</b></span>`
            : "";
        return `<div class="doc-workflow-invoice-links">`
            + `<div class="doc-workflow-invoice-contrib">${contributions}${direct}${unlinked}</div>`
            + `<button type="button" class="doc-workflow-add-dn" data-add-dn="${esc(node.no)}"`
            + ` title="Add another delivery note to this invoice">`
            + `<span aria-hidden="true">+</span><b>Add DN</b></button></div>`;
    }

    function nodeHtml(node, manage, poNo) {
        const meta = [node.date, countLabel(Number(node.item_count || 0)), node.client]
            .filter(Boolean).map(esc).join(" | ");
        const coreTag = node.current ? "div" : "button";
        const coreAttrs = node.current
            ? `class="doc-workflow-node-core" aria-current="true"`
            : `type="button" class="doc-workflow-node-core" data-open-kind="${esc(node.kind)}" data-open-no="${esc(node.no)}"`;
        const manageHtml = !manage ? "" :
            `<button type="button" class="doc-workflow-manage" title="Manage link"`
            + ` aria-label="Manage link for ${esc(node.no)}"`
            + ` data-manage-kind="${esc(manage.sourceKind)}" data-manage-no="${esc(manage.sourceNo)}"`
            + ` data-manage-po="${esc(manage.poNo)}" data-manage-mode="${esc(manage.mode)}">`
            + `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg></button>`;
        return `<div class="doc-workflow-node${node.current ? " is-current" : ""}">`
            + `<${coreTag} ${coreAttrs}>`
            + `<span class="doc-workflow-node-icon">${icon(node.kind)}</span>`
            + `<span class="doc-workflow-node-copy">`
            +   `<strong>${esc(node.no)}</strong>`
            +   `<small>${meta || "Saved document"}</small>`
            + `</span>`
            + (node.current ? `<span class="doc-workflow-current">Current</span>` : "")
            + `</${coreTag}>${invoiceLinksHtml(node, poNo)}${manageHtml}</div>`;
    }

    function emptyHtml(kind, poNo, currentKind) {
        const label = kind === "po" ? "Link purchase order"
            : kind === "dn" ? "Link delivery note" : "Link invoice";
        const hint = kind === "po" ? "Choose the PO this document belongs to"
            : kind === "dn" ? "Choose what delivered this PO" : "Choose what paid this PO";
        if (kind !== "po" && !poNo) {
            return `<div class="doc-workflow-empty is-disabled">`
                + `<span class="doc-workflow-plus">+</span><span><strong>${esc(label)}</strong><small>Link a PO first</small></span>`
                + `</div>`;
        }
        return `<button type="button" class="doc-workflow-empty" data-link-kind="${esc(kind)}"`
            + (poNo ? ` data-po="${esc(poNo)}"` : ` data-source-kind="${esc(currentKind)}"`)
            + `><span class="doc-workflow-plus">+</span><span><strong>${esc(label)}</strong><small>${esc(hint)}</small></span></button>`;
    }

    function stageHtml(kind, title, nodes, poNo, currentKind, manageOf) {
        const list = Array.isArray(nodes) ? nodes : [];
        return `<div class="doc-workflow-stage" data-stage="${esc(kind)}">`
            + `<div class="doc-workflow-stage-head"><span>${icon(kind)}</span><strong>${esc(title)}</strong>`
            +   `<em>${list.length}</em></div>`
            + `<div class="doc-workflow-stage-list">`
            + (list.length
                ? list.map((node) => nodeHtml(node, manageOf ? manageOf(node) : null, poNo)).join("")
                : emptyHtml(kind, poNo, currentKind))
            + `</div></div>`;
    }

    function connectorHtml() {
        return `<span class="doc-workflow-connector" aria-hidden="true">`
            + `<svg viewBox="0 0 24 24"><path d="M5 12h14M14 7l5 5-5 5"/></svg></span>`;
    }

    function laneHtml(lane, currentKind, currentNo) {
        const po = lane.po || null;
        const poNodes = po ? [po] : [];
        const poNo = po && po.no;
        const poManage = po && currentKind !== "po" ? {
            poNo, sourceKind: currentKind, sourceNo: currentNo, mode: "po",
        } : null;
        const sourceManage = (node) => !poNo ? null : {
            poNo, sourceKind: node.kind, sourceNo: node.no, mode: "source",
        };
        return `<section class="doc-workflow-lane">`
            + stageHtml("po", "Purchase Order", poNodes, null, currentKind, () => poManage)
            + connectorHtml()
            + stageHtml("dn", "Delivery Notes", lane.dns, poNo, currentKind, sourceManage)
            + connectorHtml()
            + stageHtml("inv", "Invoices", lane.invoices, poNo, currentKind, sourceManage)
            + `</section>`;
    }

    async function openLinkedDocument(kind, docNo) {
        const loader = window[loaders[kind]];
        if (typeof loader !== "function") {
            toast(`Cannot open ${docNo}`, true);
            return;
        }
        close();
        try { await loader(docNo); }
        catch (error) { toast(`Cannot open ${docNo}`, true); }
    }

    async function runEditorRefresh(opts) {
        try { if (typeof opts.onAfter === "function") await opts.onAfter(); }
        catch (error) {}
        if (window.refreshOpenPanel) window.refreshOpenPanel();
    }

    function openManageLink(link, kind, docNo, opts) {
        close();
        /* Cancelling, Esc, the X and the dim background all mean the same thing
           here - "I did not want to manage this link" - and all of them return
           to the workflow popup this was opened from. That is one `onClose`
           rather than four handlers that must be kept in step. */
        let leaving = false;
        const handle = window.Popup.open({
            title: "Manage link",
            subtitle: `${link.sourceNo} with ${link.poNo}`,
            size: "sm",
            dir: "ltr",
            className: "doc-workflow-manage-modal",
            body: (host) => {
                const s = document.createElement("strong");
                s.textContent = "What would you like to do?";
                const p = document.createElement("span");
                p.textContent = "Replacing keeps this link until the new document "
                              + "is linked successfully.";
                host.className += " doc-workflow-manage-body";
                host.appendChild(s);
                host.appendChild(p);
            },
            actions: [
                { label: "Cancel", value: "cancel", cls: "secondary" },
                { label: "Replace", value: "replace", cls: "secondary", keep: true },
                { label: "Remove link", value: "remove", cls: "warm-red", keep: true },
            ],
            onClose: (value) => {
                if (modal === handle) modal = null;
                // Anything that is not an action we handled ourselves means
                // "go back", including Esc and the dim background.
                if (!leaving && value !== "replace" && value !== "remove") {
                    openDocumentWorkflow(kind, docNo, opts);
                }
            },
        });
        modal = handle;
        const actBtn = (value) => Array.from(
            handle.panel.querySelectorAll(".popup-actions button"))
            .find((b) => b.textContent === value);

        actBtn("Remove link").onclick = async (event) => {
            event.currentTarget.disabled = true;
            let result = null;
            try {
                result = await api().clear_po_fulfillment_link(
                    link.poNo, link.sourceKind, link.sourceNo);
            } catch (error) { result = null; }
            if (!result || !result.ok) {
                toast("Could not remove the document link", true);
                event.currentTarget.disabled = false;
                return;
            }
            toast(result.removed ? "Document link removed" : "Link was already removed");
            leaving = true;                 // we re-open the workflow ourselves
            close();
            await runEditorRefresh(opts);
            await openDocumentWorkflow(kind, docNo, opts);
        };

        actBtn("Replace").onclick = () => {
            const afterReplacement = async () => {
                let result = null;
                try {
                    result = await api().clear_po_fulfillment_link(
                        link.poNo, link.sourceKind, link.sourceNo);
                } catch (error) { result = null; }
                if (!result || !result.ok) {
                    toast("New link saved, but the old link could not be removed", true);
                }
                await runEditorRefresh(opts);
                await openDocumentWorkflow(kind, docNo, opts);
            };
            leaving = true;                 // every branch below navigates itself
            close();
            if (link.mode === "po" && window.openPoFulfillPicker) {
                window.openPoFulfillPicker(link.sourceKind, link.sourceNo, {
                    onAfter: afterReplacement,
                    excludeNo: link.poNo,
                });
            } else if (link.mode === "source" && window.openPoSourcePicker) {
                window.openPoSourcePicker(link.poNo, link.sourceKind, {
                    onAfter: afterReplacement,
                    excludeNo: link.sourceNo,
                });
            } else {
                toast("Document linker is unavailable", true);
                openDocumentWorkflow(kind, docNo, opts);
            }
        };
    }

    function wireBody(body, kind, docNo, opts) {
        body.querySelectorAll("[data-open-kind][data-open-no]").forEach((button) => {
            button.onclick = () => openLinkedDocument(button.dataset.openKind, button.dataset.openNo);
        });
        body.querySelectorAll("[data-manage-kind]").forEach((button) => {
            button.onclick = () => openManageLink({
                sourceKind: button.dataset.manageKind,
                sourceNo: button.dataset.manageNo,
                poNo: button.dataset.managePo,
                mode: button.dataset.manageMode,
            }, kind, docNo, opts);
        });
        body.querySelectorAll("[data-add-dn]").forEach((button) => {
            button.onclick = () => {
                const invoiceNo = button.dataset.addDn;
                const afterLink = async () => {
                    await runEditorRefresh(opts);
                    await openDocumentWorkflow(kind, docNo, opts);
                };
                close();
                if (window.openPoFulfillPicker) {
                    window.openPoFulfillPicker("inv", invoiceNo, {
                        initialTab: "dn",
                        onAfter: afterLink,
                    });
                } else {
                    toast("Delivery Note linker is unavailable", true);
                }
            };
        });
        body.querySelectorAll("[data-link-kind]").forEach((button) => {
            button.onclick = () => {
                const targetKind = button.dataset.linkKind;
                const poNo = button.dataset.po || "";
                const afterLink = async () => {
                    try { if (typeof opts.onAfter === "function") await opts.onAfter(); }
                    catch (error) {}
                    await openDocumentWorkflow(kind, docNo, opts);
                };
                close();
                if (targetKind === "po") {
                    if (window.openPoFulfillPicker) {
                        window.openPoFulfillPicker(kind, docNo, { onAfter: afterLink });
                    } else {
                        toast("Purchase Order linker is unavailable", true);
                    }
                } else if (window.openPoSourcePicker) {
                    window.openPoSourcePicker(poNo, targetKind, { onAfter: afterLink });
                } else {
                    toast("Document linker is unavailable", true);
                }
            };
        });
    }

    async function openDocumentWorkflow(kind, docNo, opts) {
        kind = String(kind || "").trim().toLowerCase();
        docNo = String(docNo || "").trim();
        opts = opts || {};
        if (!["po", "dn", "inv"].includes(kind) || !docNo) return;
        const handle = shell(kind, docNo);
        const seq = ++requestSeq;
        let data = null;
        try { data = await api().document_workflow(kind, docNo); }
        catch (error) { data = null; }
        // Dismissed, or superseded by a newer request, while the bridge answered.
        if (handle.closed || modal !== handle || seq !== requestSeq) return;
        const body = handle.body;
        body.classList.add("doc-workflow-body");
        if (!data || !data.ok) {
            body.innerHTML = `<div class="doc-workflow-error"><strong>Workflow unavailable</strong>`
                + `<span>${esc((data && data.error) || "Could not load linked documents.")}</span></div>`;
            return;
        }
        const lanes = Array.isArray(data.lanes) ? data.lanes : [];
        body.innerHTML = lanes.length
            ? lanes.map((lane) => laneHtml(lane, kind, docNo)).join("")
            : `<div class="doc-workflow-error"><strong>No workflow data</strong><span>This document has no saved workflow.</span></div>`;
        wireBody(body, kind, docNo, opts);
    }

    function refreshButtons() {
        Object.entries(buttonIds).forEach(([kind, id]) => {
            const button = document.getElementById(id);
            if (!button) return;
            const getter = window[contextGetters[kind]];
            const context = typeof getter === "function" ? getter() : null;
            button.disabled = !context;
            button.title = context
                ? "Track delivery and payment"
                : "Save this document first to view Workflow";
        });
    }

    window.openDocumentWorkflow = openDocumentWorkflow;
    window.refreshDocumentWorkflowButtons = refreshButtons;
    refreshButtons();
})();
