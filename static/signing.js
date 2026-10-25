/* =========================================================
   Phase K.1 - Digital Signatures & Stamp frontend.
   ---------------------------------------------------------
   Responsibilities:
     1. Render the App-Settings "Digital Signatures & Stamp" card
        (upload / preview / label / remove for 3 sigs + 1 stamp,
         plus size inputs).
     2. Run the universal Sign modal (one per-slot row with a signature
        picker and a stamp checkbox; randomization is rolled by the
        backend on Apply).
     3. Expose `window.openSignModal({ docType, currentOverlay, onApply })`
        for every editor to call when its Sign button is clicked.
        The editor stores the returned overlay on its document state
        and includes it in its save payload.

   Storage of the overlay per document is the editor's responsibility
   (saved via the existing save_<doc> path and persisted server-side
   in the doc_meta table).
   ========================================================= */
(function () {
    "use strict";

    const api = () => window.pywebview.api;
    const $   = (id) => document.getElementById(id);

    const signState = {
        settings: null,         // last fetched from backend
        slotsForDoc: null,      // ['main'] | ['recipient','supplier'] | ...
        docType: null,
        currentOverlay: null,
        onApply: null,
    };

    // ---------------------------------------------------------- helpers
    function toast(msg, isError) {
        if (window.toast) return window.toast(msg, !!isError);
        console[isError ? "error" : "log"](msg);
    }

    function escapeHtml(s) {
        s = (s == null) ? "" : String(s);
        return s.replace(/[&<>"']/g, m =>
            ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
    }
    function escapeAttr(s) { return escapeHtml(s); }

    function fileToDataUri(file) {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload  = () => resolve(reader.result);
            reader.onerror = () => reject(reader.error);
            reader.readAsDataURL(file);
        });
    }

    // ---------------------------------------------------------- settings UI
    function _showPreview(img, emptyEl, url) {
        if (!img) return;
        // If the image can't load (file moved or never written), bounce back
        // to the empty state instead of silently showing a broken img.
        img.onerror = () => {
            img.onerror = null;
            img.removeAttribute("src");
            img.style.display = "none";
            if (emptyEl) {
                emptyEl.hidden = false;
                emptyEl.textContent = "File missing on disk - re-upload";
            }
        };
        img.onload = () => {
            img.style.display = "block";
            if (emptyEl) emptyEl.hidden = true;
        };
        img.src = url;
    }

    async function refreshSettingsUI() {
        const s = await api().signing_get_settings();
        signState.settings = s;
        // Stamp slot
        const stampImg = $("sigStampPreview");
        const stampEmpty = $("sigStampEmpty");
        if (s.stamp.has_image) {
            _showPreview(stampImg, stampEmpty, s.stamp.data_uri);
        } else {
            if (stampImg) { stampImg.removeAttribute("src"); stampImg.style.display = "none"; }
            if (stampEmpty) { stampEmpty.hidden = false; stampEmpty.textContent = "No image yet"; }
        }
        // Signature slots
        for (let i = 1; i <= 3; i++) {
            const data = s.signatures[i - 1];
            const img   = document.querySelector(`img.sig-preview[data-preview="${i}"]`);
            const empty = document.querySelector(`.sig-slot-empty[data-empty="${i}"]`);
            const label = document.querySelector(`.sig-label-input[data-label="${i}"]`);
            if (label) label.value = data.label || "";
            if (data.has_image) {
                _showPreview(img, empty, data.data_uri);
            } else {
                if (img)   { img.removeAttribute("src"); img.style.display = "none"; }
                if (empty) { empty.hidden = false; empty.textContent = "No image yet"; }
            }
        }
        // Sizes
        $("sigStampW").value = s.size.stamp_w_mm;
        $("sigStampH").value = s.size.stamp_h_mm;
        $("sigSigW").value   = s.size.sig_w_mm;
        $("sigSigH").value   = s.size.sig_h_mm;
        // Auto-sign on save (2026-07-24)
        const auto = s.auto || {};
        const aOn = $("sigAutoEnabled"), aStamp = $("sigAutoStamp"), aSig = $("sigAutoSig");
        if (aOn)    aOn.checked = !!auto.enabled;
        if (aStamp) aStamp.checked = !!auto.stamp;
        if (aSig)   aSig.value = auto.sig_index == null ? "" : String(auto.sig_index);
    }

    async function uploadStamp(file) {
        const uri = await fileToDataUri(file);
        const r = await api().signing_upload_stamp(uri);
        if (!r.ok) { toast(r.error || "Stamp upload failed", true); return; }
        toast("Stamp updated.");
        // The LPO screen caches these images for the session - drop its copy
        // or it would keep stamping documents with the OLD one.
        if (window.clearLpoAssets) window.clearLpoAssets();
        await refreshSettingsUI();
    }

    async function uploadSignature(slot, file) {
        const uri = await fileToDataUri(file);
        const r = await api().signing_upload_signature(slot, uri);
        if (!r.ok) { toast(r.error || "Signature upload failed", true); return; }
        toast(`Signature ${slot} updated.`);
        // The LPO screen caches these images for the session - drop its copy
        // or it would keep stamping documents with the OLD one.
        if (window.clearLpoAssets) window.clearLpoAssets();
        await refreshSettingsUI();
    }

    async function deleteStamp() {
        const r = await api().signing_delete_stamp();
        if (!r.ok) { toast(r.error || "Couldn't remove stamp", true); return; }
        await refreshSettingsUI();
    }

    async function deleteSignature(slot) {
        const r = await api().signing_delete_signature(slot);
        if (!r.ok) { toast(r.error || "Couldn't remove signature", true); return; }
        await refreshSettingsUI();
    }

    async function saveLabelsAndSizes() {
        const payload = {
            signatures: [1, 2, 3].map(i => {
                const el = document.querySelector(`.sig-label-input[data-label="${i}"]`);
                return { label: (el && el.value) || "" };
            }),
            size: {
                stamp_w_mm: parseFloat($("sigStampW").value) || 46.5,
                stamp_h_mm: parseFloat($("sigStampH").value) || 29.0,
                sig_w_mm:   parseFloat($("sigSigW").value)   || 55.0,
                sig_h_mm:   parseFloat($("sigSigH").value)   || 22.0,
            },
        };
        // Auto-sign on save (2026-07-24) - only sent when the card is present,
        // so a settings screen without it can't wipe the stored block.
        const aOn = $("sigAutoEnabled"), aStamp = $("sigAutoStamp"), aSig = $("sigAutoSig");
        if (aOn || aStamp || aSig) {
            const raw = aSig ? String(aSig.value || "").trim() : "";
            payload.auto = {
                enabled:   aOn ? !!aOn.checked : true,
                stamp:     aStamp ? !!aStamp.checked : true,
                sig_index: raw === "" ? null : parseInt(raw, 10),
            };
        }
        const r = await api().signing_save_settings(payload);
        if (!r.ok) { toast(r.error || "Save failed", true); return false; }
        return true;
    }
    window.signingSaveLabelsAndSizes = saveLabelsAndSizes;

    function bindSettingsCard() {
        // Event-delegated handlers - robust against script load order and
        // any later re-bindings by other modules. Bound to `document` so
        // they survive innerHTML rewrites of the parent containers.
        document.addEventListener("click", (e) => {
            // Upload buttons
            const up = e.target.closest("button[data-sig-upload]");
            if (up) {
                const slot = up.getAttribute("data-sig-upload");
                if (slot === "stamp") {
                    const f = $("sigStampFile"); if (f) f.click();
                } else {
                    const f = document.querySelector(`input.sig-file[data-file="${slot}"]`);
                    if (f) f.click();
                }
                return;
            }
            // Remove buttons
            const del = e.target.closest("button[data-sig-delete]");
            if (del) {
                const slot = del.getAttribute("data-sig-delete");
                if (slot === "stamp") deleteStamp();
                else                  deleteSignature(parseInt(slot, 10));
                return;
            }
            // Settings open - refresh sig previews when entering settings.
            if (e.target.closest("#btnSettings")) {
                setTimeout(refreshSettingsUI, 40);
                return;
            }
            // Save - piggy-back labels+sizes save on the App-settings Save button.
            if (e.target.closest("#btnSettingsSave")) {
                // Fire and forget; the main Save handler runs independently.
                saveLabelsAndSizes();
                return;
            }
        });

        // File-input change events (also delegated).
        document.addEventListener("change", (e) => {
            const t = e.target;
            if (t.id === "sigStampFile") {
                const f = t.files && t.files[0];
                if (f) uploadStamp(f);
                t.value = "";
            } else if (t.matches && t.matches("input.sig-file[data-file]")) {
                const slot = parseInt(t.getAttribute("data-file"), 10);
                const f = t.files && t.files[0];
                if (f) uploadSignature(slot, f);
                t.value = "";
            }
        });

        // Most reliable trigger: refresh whenever the settings view becomes
        // visible (i.e. its `hidden` attribute is removed). Belt-and-braces
        // alongside the click delegation above - works no matter how the
        // settings view ends up visible.
        const settingsView = $("settingsView");
        if (settingsView && typeof MutationObserver !== "undefined") {
            const obs = new MutationObserver(() => {
                if (!settingsView.hidden) {
                    refreshSettingsUI().catch(err => {
                        console.warn("[signing] refreshSettingsUI failed:", err);
                    });
                }
            });
            obs.observe(settingsView, {
                attributes: true,
                attributeFilter: ["hidden"],
            });
        }

        // Best-effort early refresh in case settings is already open.
        refreshSettingsUI().catch(() => {});
    }

    // ---------------------------------------------------------- Sign modal
    function slotLabel(slot) {
        const map = {
            main:      "Document signature",
            recipient: "Recipient signature",
            supplier:  "Supplier signature",
            s0:        "First signer",
            s1:        "Second signer",
        };
        return map[slot] || slot;
    }

    function isOverlayEmpty(overlay) {
        if (!overlay || !overlay.slots) return true;
        return !Object.values(overlay.slots).some(v => v);
    }

    async function openSignModal(opts) {
        const { docType, currentOverlay, onApply, activeSlots } = opts || {};
        if (!docType) return;

        signState.docType = docType;
        signState.currentOverlay = currentOverlay || null;
        signState.onApply = onApply || null;

        // Fetch settings (need labels + has_image) and slot list.
        if (!signState.settings) await refreshSettingsUI();
        const slots = activeSlots && activeSlots.length
            ? activeSlots
            : await api().signing_slots_for(docType);
        signState.slotsForDoc = slots;

        // Empty state: nothing uploaded? Bail with a friendly note.
        const anyAssets = signState.settings.stamp.has_image
            || signState.settings.signatures.some(s => s.has_image);

        const host = $("signSlots");
        const empty = $("signEmpty");
        host.innerHTML = "";
        empty.hidden = anyAssets;
        if (!anyAssets) { openSignModal(); return; }

        // Build one row per slot.
        const sigs = signState.settings.signatures;
        const stampAvail = signState.settings.stamp.has_image;

        const cur = (currentOverlay && currentOverlay.slots) || {};
        host.innerHTML = slots.map(slot => {
            const picked = cur[slot] || {};
            const sigSel = sigs.map((sg, i) => {
                if (!sg.has_image) return "";
                const sel = (picked.sig_index === i + 1) ? " selected" : "";
                const txt = sg.label ? `${i + 1} - ${escapeHtml(sg.label)}` : `Signature ${i + 1}`;
                return `<option value="${i + 1}"${sel}>${txt}</option>`;
            }).join("");
            const noneSel = (picked.sig_index == null) ? " selected" : "";
            const stampChecked = picked.stamp ? " checked" : "";
            return `
                <div class="sign-row" data-slot="${escapeAttr(slot)}">
                    <div class="sign-row-label">${escapeHtml(slotLabel(slot))}</div>
                    <div class="sign-row-controls">
                        <label class="sign-pick">
                            <span>Signature</span>
                            <select class="sign-sig" data-slot="${escapeAttr(slot)}">
                                <option value=""${noneSel}>- None -</option>
                                ${sigSel}
                            </select>
                        </label>
                        <label class="sign-pick">
                            <input type="checkbox" class="sign-stamp" data-slot="${escapeAttr(slot)}" ${stampAvail ? "" : "disabled"}${stampChecked}>
                            <span>Include stamp</span>
                        </label>
                    </div>
                </div>
            `;
        }).join("");

        $("signBackdrop").hidden = false;
    }
    window.openSignModal = openSignModal;

    function readSelections() {
        const sels = {};
        for (const slot of (signState.slotsForDoc || [])) {
            const sig = document.querySelector(`select.sign-sig[data-slot="${slot}"]`);
            const stp = document.querySelector(`input.sign-stamp[data-slot="${slot}"]`);
            sels[slot] = {
                sig_index: sig && sig.value ? parseInt(sig.value, 10) : null,
                stamp:     !!(stp && stp.checked),
            };
        }
        return sels;
    }

    /* The dialog's MARKUP stays in index.html (its innards are wired by id all
       through this file); its BEHAVIOUR is the shared contract's since
       2026-08-26 - Esc, the dim background, the focus trap, the scroll lock and
       focus restored on close all come from `Popup.mount`
       ([[14-UI-CONTRACTS]]). */
    let signPopup = null;
    function openSignModal() {
        if (signPopup && !signPopup.closed) return signPopup;
        signPopup = window.Popup.mount($("signBackdrop"));
        return signPopup;
    }
    function closeSignModal() {
        if (signPopup) { signPopup.close(null); signPopup = null; }
        else $("signBackdrop").hidden = true;
    }

    async function applySignModal() {
        if (!signState.docType) return;
        const sels = readSelections();
        const overlay = await api().signing_roll_overlay(signState.docType, sels);
        closeSignModal();
        if (typeof signState.onApply === "function") {
            try { signState.onApply(overlay); }
            catch (e) { console.error("onApply threw:", e); }
        }
        toast("Signing applied.");
        if (window.refreshOpenPanel) window.refreshOpenPanel();   // Phase X.26 - reflect sign state in the panel
    }

    function clearSignModal() {
        closeSignModal();
        if (typeof signState.onApply === "function") {
            try { signState.onApply(null); }
            catch (e) { console.error("onApply(null) threw:", e); }
        }
        toast("Signing cleared.");
        if (window.refreshOpenPanel) window.refreshOpenPanel();   // Phase X.26 - reflect sign state in the panel
    }

    function bindSignModal() {
        const apply = $("signApply");   if (apply)  apply.onclick  = applySignModal;
        const cancel= $("signCancel");  if (cancel) cancel.onclick = closeSignModal;
        const clear = $("signClear");   if (clear)  clear.onclick  = clearSignModal;
        // Esc and the dim background are the shared contract's job now; only
        // this dialog's OWN shortcut stays here.
        document.addEventListener("keydown", (e) => {
            if (!signPopup || signPopup.closed) return;
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) applySignModal();
        });
    }

    // ---------------------------------------------------------- editor registry
    // Editors call window.signingRegister({...}) once on module load. The
    // matching Sign button (btnPoSign / btnQSign / btnDnSign / btnInvSign /
    // gets wired to open the Sign modal with the active overlay
    // and write back the new one via the editor's setOverlay callback.
    const editorRegistry = {};   // docType -> {getOverlay, setOverlay, getDocNo, getActiveSlots}

    function bindEditorButton(buttonId, docType) {
        const btn = $(buttonId);
        if (!btn) return;
        btn.addEventListener("click", () => {
            const reg = editorRegistry[docType];
            if (!reg) {
                toast(`Sign isn't wired for ${docType} yet`, true);
                return;
            }
            const cur = (typeof reg.getOverlay === "function") ? reg.getOverlay() : null;
            const slots = (typeof reg.getActiveSlots === "function") ? reg.getActiveSlots() : null;
            openSignModal({
                docType,
                currentOverlay: cur,
                activeSlots: slots,
                onApply: (overlay) => {
                    if (typeof reg.setOverlay === "function") reg.setOverlay(overlay);
                },
            });
        });
    }

    function signingRegister(opts) {
        const { docType } = opts;
        if (!docType) return;
        editorRegistry[docType] = opts;
    }
    window.signingRegister = signingRegister;

    // ---------------------------------------------------------- wiring
    document.addEventListener("DOMContentLoaded", () => {
        bindSettingsCard();
        bindSignModal();
        bindEditorButton("btnPoSign",  "po");
        bindEditorButton("btnQSign",   "qut");
        bindEditorButton("btnDnSign",  "dn");
        bindEditorButton("btnInvSign", "inv");
    });
})();
