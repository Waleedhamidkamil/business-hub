/* =========================================================
   Client's signed copy - the counter-signed paper the CLIENT scans back.
   Delivery notes since 2026-07-24, invoices since 2026-08-27.
   ---------------------------------------------------------
   Distinct from the Sign button, which applies OUR digital signature:
     • `signed`        = we signed it (doc_meta 'signing')
     • `client_signed` = they signed it (the `signed_copies` table)

   ONE implementation for every document kind that keeps one. The DN version
   lived inside dn.js until the invoice needed the same thing; a second copy of
   ~160 lines would have drifted, exactly as the per-editor cost-currency rules
   did in August. The editors keep only their own button/state wiring.

   The drop zone mirrors the quotation's image cell: single click focuses so
   Ctrl+V pastes, double click opens the file picker, drag & drop also works.
   Unlike the image cell this accepts PDFs as well as images, so it can't reuse
   onImagePaste (that one is hard-filtered to image/*).
   ========================================================= */
(function () {
    "use strict";

    const api = () => window.pywebview.api;

    const TYPES = ["application/pdf", "image/png", "image/jpeg"];

    // Per-kind wording. Everything else about the dialog is identical.
    const KINDS = {
        dn: {
            noun:      "Delivery Note",
            lower:     "delivery note",
            saveFirst: "Save this Delivery Note first",
            attachTip: "Attach the signed copy the client sent back",
            waiveHint: "Stops this delivery note showing “Awaiting client signature”.",
            waiveOn:   "This delivery note won't ask for a signed copy",
            waiveOff:  "Signed copy expected again",
        },
        inv: {
            noun:      "Invoice",
            lower:     "invoice",
            saveFirst: "Save this Invoice first",
            attachTip: "Attach the signed copy the client sent back",
            waiveHint: "Stops this invoice showing “Awaiting client signature”.",
            waiveOn:   "This invoice won't ask for a signed copy",
            waiveOff:  "Signed copy expected again",
        },
    };

    /* A DN's signed copy also belongs on any invoice that carries that delivery
       note, so the backend rewrites those invoice PDFs (api._refresh_invoice_
       attachments). Say so - the user is standing in the DN and would otherwise
       have no way to know the invoice they already sent has changed on disk. */
    function reportInvoiceRefresh(r) {
        const done = (r && r.invoices_refreshed) || [];
        const failed = (r && r.invoices_failed) || [];
        if (done.length) {
            window.toast(done.length === 1
                ? `${done[0]} updated with the signed delivery note`
                : `${done.length} invoices updated with the signed delivery note`);
        }
        if (failed.length) {
            window.toast("Couldn't update " + failed.map(f => f.inv_no || "an invoice").join(", "), true);
        }
    }

    function acceptable(file) {
        if (!file) return false;
        if (TYPES.includes((file.type || "").toLowerCase())) return true;
        return /\.(pdf|png|jpe?g)$/i.test(file.name || "");
    }

    function fileToDataUri(file) {
        return new Promise((resolve, reject) => {
            const fr = new FileReader();
            fr.onload = () => resolve(fr.result);
            fr.onerror = () => reject(new Error("Could not read that file"));
            fr.readAsDataURL(file);
        });
    }

    /* The attached record, or null. Never throws - a bridge hiccup must not
       leave an editor stuck thinking it has a copy. */
    async function load(kind, docNo) {
        if (!docNo) return null;
        try { return (await api().get_doc_signed_copy(kind, docNo)) || null; }
        catch (e) { return null; }
    }

    /* Is this document waived ("no signed copy expected")? Reuses the shared
       Needs-Attention ignore table under the `client_sign` category. */
    async function waived(kind, docNo) {
        try { return !!(await api().is_attention_ignored(kind, docNo, "client_sign")); }
        catch (e) { return false; }
    }

    /* Whichever editor currently holds this document keeps its toolbar button
       honest. Since 2026-08-27 the dialog also opens straight from the right
       panel and the To-Do list (marking an invoice paid), so it cannot rely on
       the editor having wired an `onChange` - it may not be the caller. */
    function syncEditors(kind, docNo, rec) {
        if (kind === "inv" && window.invState && window.invState.editInvNo === docNo) {
            window.invState.hasSignedCopy = !!rec;
            if (window.refreshInvSignedCopyButton) window.refreshInvSignedCopyButton();
        }
        if (kind === "dn" && window.dnState && window.dnState.editDnNo === docNo) {
            window.dnState.hasSignedCopy = !!rec;
            if (window.refreshDnSignedCopyButton) window.refreshDnSignedCopyButton();
        }
    }

    /* Toolbar button state - disabled until the document is saved, and lit
       while a copy is attached. */
    function paintButton(btn, kind, saved, has) {
        if (!btn) return;
        const K = KINDS[kind] || KINDS.dn;
        btn.disabled = !saved;
        btn.title = saved ? K.attachTip : K.saveFirst;
        btn.classList.toggle("active", !!has);
        btn.setAttribute("aria-pressed", has ? "true" : "false");
    }

    async function open(kind, docNo, opts) {
        opts = opts || {};
        const K = KINDS[kind];
        if (!K) return;
        if (!docNo) { window.toast(KINDS[kind] ? K.saveFirst : "Save this document first", true); return; }

        /* Shared Popup since 2026-08-26 ([[14-UI-CONTRACTS]]). `render` repaints
           the BODY as the file is attached, replaced or un-attached, so the popup
           is opened once and its body swapped - the head and the way out never
           move. */
        const _h = window.Popup.open({
            title: "Signed copy",
            subtitle: docNo,
            size: "md",
            dir: "ltr",                          // the shell is RTL - see docs/07
            className: "sc-modal",
            body: "",
        });
        const backdrop = _h.panel;

        const esc = window.esc;
        const safe = window.safe;
        const toast = window.toast;
        const ICONS = window.ICON || {};

        const render = (rec, isWaived) => {
            const body = rec ? `
            <div class="sc-file">
                <div class="sc-file-main">
                    <strong>${esc(rec.filename || "Signed copy")}</strong>
                    <small>${rec.exists
                        ? `Attached ${esc((rec.attached_at || "").slice(0, 10))}`
                        : "⚠ The file is missing from its folder"}</small>
                </div>
                <div class="sc-file-actions">
                    <button type="button" class="ghost" data-sc="open"   title="Open">${ICONS.externalLink || ""}<span>Open</span></button>
                    <button type="button" class="ghost" data-sc="folder" title="Show in folder">${ICONS.folder || ""}<span>Folder</span></button>
                    <button type="button" class="ghost" data-sc="print"  title="Print">${ICONS.printer || ""}<span>Print</span></button>
                    <button type="button" class="ghost" data-sc="copy"   title="Copy file">${ICONS.copy || ""}<span>Copy</span></button>
                    <button type="button" class="ghost danger-text" data-sc="remove" title="Un-attach">${ICONS.trash || ""}<span>Un-attach</span></button>
                </div>
            </div>
            <div class="sc-replace-hint">Attach a different file below to replace it.</div>`
                : "";
            if (_h.closed) return;               // dismissed while a save was in flight
            _h.body.innerHTML = `
                <div class="sc-body">
                    ${body}
                    <div class="sc-drop" id="scDrop" tabindex="0" role="button"
                         aria-label="Click then press Ctrl+V to paste, or double-click to choose a file">
                        <div class="sc-drop-icon">${ICONS.copy || ""}</div>
                        <strong>Click, then Ctrl+V to paste</strong>
                        <span>Double-click to choose a file &middot; or drop it here</span>
                        <small>PDF, PNG or JPG &middot; saved as ${esc(docNo)}</small>
                    </div>
                    <label class="sc-waive">
                        <input type="checkbox" id="scWaive"${isWaived ? " checked" : ""}>
                        <span>
                            <strong>No signed copy expected for this one</strong>
                            <small>${esc(K.waiveHint)}</small>
                        </span>
                    </label>
                </div>`;
            wire(rec);
        };

        // Re-read the record, tell the editor about it, then repaint the body.
        const repaint = async () => {
            const rec = await load(kind, docNo);
            syncEditors(kind, docNo, rec);
            if (typeof opts.onChange === "function") opts.onChange(rec);
            render(rec, await waived(kind, docNo));
            return rec;
        };

        const save = async (fn) => {
            const drop = document.getElementById("scDrop");
            if (drop) { drop.classList.add("is-busy"); drop.querySelector("strong").textContent = "Saving…"; }
            let r = null;
            try { r = await fn(); } catch (e) { r = { ok: false, error: String(e) }; }
            if (!r || !r.ok) {
                if (r && r.cancelled) { await repaint(); return; }
                toast("Couldn't attach: " + ((r && r.error) || "unknown"), true);
                await repaint();
                return;
            }
            toast("Signed copy attached");
            reportInvoiceRefresh(r);
            await repaint();
            if (window.refreshOpenPanel) window.refreshOpenPanel();
        };

        function wire(rec) {
            const waive = document.getElementById("scWaive");
            if (waive) waive.onchange = safe(async () => {
                const r = await api().set_attention_ignore(
                    kind, docNo, "client_sign", waive.checked);
                if (!r || !r.ok) {
                    waive.checked = !waive.checked;      // put the box back
                    toast("Couldn't save that", true);
                    return;
                }
                toast(waive.checked ? K.waiveOn : K.waiveOff);
                if (window.refreshOpenPanel) window.refreshOpenPanel();
            }, "Signed copy");

            backdrop.querySelectorAll("[data-sc]").forEach(b => {
                b.onclick = safe(async () => {
                    const act = b.dataset.sc;
                    const path = rec && rec.path;
                    if (act === "remove") {
                        const ok = await window.showModal({
                            title: "Un-attach signed copy",
                            body: `Remove the signed copy from this ${K.noun}? `
                                + "The copy stored with the document is deleted; your original scan is untouched.",
                            buttons: [
                                { label: "Cancel",     value: "cancel", cls: "ghost" },
                                { label: "Un-attach",  value: "ok",     cls: "warm-red" },
                            ],
                        });
                        if (ok !== "ok") return;
                        const rm = await api().remove_doc_signed_copy(kind, docNo);
                        toast("Signed copy removed");
                        reportInvoiceRefresh(rm);
                        await repaint();
                        if (window.refreshOpenPanel) window.refreshOpenPanel();
                        return;
                    }
                    if (!path) return;
                    if (act === "open")   return api().open_path(path);
                    if (act === "folder") return api().reveal_in_folder(path);
                    if (act === "print")  return window.printPdfFile(path);
                    if (act === "copy")   return window.copyFileToClipboard(path);
                }, "Signed copy");
            });

            const drop = document.getElementById("scDrop");
            if (!drop) return;
            drop.addEventListener("click", () => drop.focus());
            drop.addEventListener("dblclick", safe(
                () => save(() => api().attach_doc_signed_copy(kind, docNo)), "Choose signed copy"));
            drop.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    save(() => api().attach_doc_signed_copy(kind, docNo));
                }
            });
            drop.addEventListener("paste", (e) => {
                const file = [...((e.clipboardData && e.clipboardData.files) || [])][0];
                if (!file) { toast("Nothing pasteable on the clipboard", true); return; }
                e.preventDefault();
                if (!acceptable(file)) { toast("Only PDF, PNG or JPG can be attached", true); return; }
                save(async () => api().attach_doc_signed_copy_data(
                    kind, docNo, await fileToDataUri(file), file.name || ""));
            });
            drop.addEventListener("dragover", (e) => {
                e.preventDefault();
                drop.classList.add("is-over");
            });
            drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
            drop.addEventListener("drop", (e) => {
                e.preventDefault();
                drop.classList.remove("is-over");
                const file = [...((e.dataTransfer && e.dataTransfer.files) || [])][0];
                if (!acceptable(file)) { toast("Only PDF, PNG or JPG can be attached", true); return; }
                save(async () => api().attach_doc_signed_copy_data(
                    kind, docNo, await fileToDataUri(file), file.name || ""));
            });
            setTimeout(() => drop.focus(), 30);
        }

        render(await load(kind, docNo), await waived(kind, docNo));
        return _h;                           // `_h.result` resolves when it closes
    }

    /* Marking an invoice paid is the moment the client's stamped copy is
       actually in hand, so that is when we ask for it (2026-08-27, Waleed).
       Silent when there is nothing to ask for: a copy is already attached, or
       the user ticked "No signed copy expected" on this one. Resolves once the
       dialog is closed, so a caller can queue something after it. */
    async function promptAfterPaid(kind, docNo, opts) {
        if (!docNo || !KINDS[kind]) return null;
        const existing = await load(kind, docNo);
        if (existing) return existing;             // they already sent it back
        if (await waived(kind, docNo)) return null; // they said stop asking
        const h = await open(kind, docNo, opts || {});
        if (h && h.result) { try { await h.result; } catch (e) { /* dismissed */ } }
        return load(kind, docNo);
    }

    window.SignedCopy = { TYPES, acceptable, load, waived, paintButton, open,
                          promptAfterPaid };
})();
