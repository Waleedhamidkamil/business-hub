/* =========================================================
   CATALOGUE REVIEW - the "Doctor"  (2026-09-10)
   ---------------------------------------------------------
   The Master Data chips answer "what is MISSING": no Arabic name 621, no unit
   105, no price 195. Useful, and not the same question as "what is WRONG".

   Three things a chip cannot do, and this panel is those three:

     1. FIX IN BULK. Filtering to 621 records leaves you typing each one.
     2. SHOW A PROBLEM THAT IS NOT A BLANK FIELD. One product stored twice at
        two different prices; variants nothing can tell apart; a date typed into
        a description. None of those is an empty box, so no chip can find them.
     3. REMEMBER A RULING. "These two are not duplicates" had nowhere to live,
        so the chip reported them for ever.

   Design rules, the same ones the rest of this screen follows:
     · Colour never carries meaning alone (the user is colour blind). A price
       disagreement is the WORDS "prices differ", never a red dot.
     · Nothing is applied without a press, and every destructive action says
       what it will do before it does it.
     · The worst thing first. `Round Table` on file at $300 and $400 outranks a
       tidy duplicate, because one of those can put a wrong price on an invoice
       and the other is only untidy.

   Backend: backend/master_doctor.py. Nothing here decides anything on its own -
   `scan` is a read, `apply` carries out ONE finding, `dismiss` is a note.
   ========================================================= */
(function () {
    "use strict";

    const $ = (id) => document.getElementById(id);
    const api = () => window.pywebview.api;
    const toast = (m, bad) => (window.toast ? window.toast(m, bad) : undefined);
    const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[m]));

    /* One entry per finding kind: what to call it, and how to say what it is.
       The backend decides WHAT is wrong; this decides how it reads. */
    const KINDS = {
        duplicate: {
            label: "Stored twice",
            lead: "One product on file more than once. Merging keeps the record "
                + "your documents already use, and the other names keep working.",
            action: "Merge them",
        },
        variant_needs_description: {
            label: "Cannot be told apart",
            lead: "Real variants that share a name with nothing to distinguish "
                + "them, so every lookup asks you to choose. Give each one a "
                + "description.",
            action: "Save descriptions",
        },
        junk_date: {
            label: "A date in a description",
            lead: "A date belongs to an order, not to a product.",
            action: "Remove it",
        },
        junk_newline: {
            label: "Line break inside a field",
            lead: "A name split over two lines matches nothing and prints oddly.",
            action: "Fix it",
        },
        category: {
            label: "Category",
            lead: "Priced by time, so this is manpower or hire. No guesswork and "
                + "no API call.",
            action: "Set it",
        },
    };
    const ORDER = ["duplicate", "variant_needs_description", "junk_date",
                   "junk_newline", "category"];

    const state = { findings: [], counts: {}, kind: "", busy: false, open: false };

    function money(row) {
        if (row.price == null) return "no price";
        const ccy = String(row.price_ccy || "USD").toUpperCase();
        return ccy === "IQD" ? `IQD ${Math.round(row.price).toLocaleString()}`
                             : `$${Number(row.price).toLocaleString()}`;
    }

    /* The evidence, as a table of the actual records. The user is being asked
       to make a decision about real rows, so the rows are what they see. */
    function rowsHtml(finding) {
        const rows = (finding.evidence && finding.evidence.rows) || [];
        const keep = finding.proposal && finding.proposal.keep;
        return `<table class="mdoc-rows"><tbody>${rows.map((r) => `
            <tr${r.item_id === keep ? ' class="is-keep"' : ""}>
              <td class="mdoc-no">${esc(r.item_no || "")}</td>
              <td class="mdoc-name">
                <strong dir="auto">${esc(r.name || "")}</strong>
                ${r.description ? `<small dir="auto">${esc(r.description)}</small>` : ""}
              </td>
              <td class="mdoc-unit">${esc(r.unit || "-")}</td>
              <td class="mdoc-money">${esc(money(r))}</td>
              <td class="mdoc-uses">${Number(r.uses || 0).toLocaleString()} ${Number(r.uses) === 1 ? "use" : "uses"}</td>
              <td class="mdoc-keep">${r.item_id === keep ? "keeping this one" : ""}</td>
            </tr>`).join("")}</tbody></table>`;
    }

    /* A price disagreement is the REASON a duplicate matters, so it is said in
       words and put where the eye lands first. Never a colour on its own. */
    function priceNote(finding) {
        const rel = (finding.evidence || {}).price;
        if (finding.kind !== "duplicate") return "";
        if (rel === "far") {
            return `<p class="mdoc-warn"><strong>The copies disagree about the price.</strong>
                    Check which is right before merging - the survivor keeps its own.</p>`;
        }
        if (rel === "close") {
            return `<p class="mdoc-note">The prices differ slightly. The survivor keeps its own.</p>`;
        }
        return "";
    }

    function beforeAfter(finding) {
        const p = finding.proposal || {};
        if (p.action !== "set" || !p.patch) return "";
        const field = Object.keys(p.patch)[0];
        const was = (finding.evidence || {}).was;
        if (field === "category") {
            return `<p class="mdoc-note">Set the category to
                    <strong>${esc(p.patch.category)}</strong> -
                    ${esc((finding.evidence || {}).why || "")}.</p>`;
        }
        return `<div class="mdoc-ba">
            <div><span>now</span><code dir="auto">${esc(was || "")}</code></div>
            <div><span>after</span><code dir="auto">${esc(p.patch[field] || "(empty)")}</code></div>
        </div>`;
    }

    /* Variants get one description box each, right here. Sending the user off
       to the record and back for six rows is how a list like this goes
       unfinished. */
    function describeHtml(finding, index) {
        const rows = (finding.evidence && finding.evidence.rows) || [];
        const wanted = new Set((finding.proposal || {}).item_ids || []);
        return `<div class="mdoc-describe">${rows.map((r) => `
            <label class="mdoc-desc-row">
              <span class="mdoc-desc-label">
                <em>${esc(r.item_no || "")}</em>
                ${esc(r.unit || "no unit")} · ${esc(money(r))}
                · ${Number(r.uses || 0).toLocaleString()} ${Number(r.uses) === 1 ? "use" : "uses"}
              </span>
              <input type="text" dir="auto" data-mdoc-desc="${index}:${r.item_id}"
                     value="${esc(r.description || "")}"
                     placeholder="${wanted.has(r.item_id) ? "what makes this one different" : ""}">
            </label>`).join("")}</div>`;
    }

    function cardHtml(finding, index) {
        const K = KINDS[finding.kind] || { label: finding.kind, action: "Apply" };
        const describe = finding.proposal && finding.proposal.action === "describe";
        return `<article class="mdoc-card" data-mdoc-card="${index}">
          <header>
            <span class="mdoc-kind">${esc(K.label)}</span>
            <h4 dir="auto">${esc(finding.title || "")}</h4>
          </header>
          ${priceNote(finding)}
          ${describe ? "" : (finding.kind === "duplicate" ? rowsHtml(finding) : "")}
          ${describe ? describeHtml(finding, index) : beforeAfter(finding)}
          <footer>
            <button type="button" class="im-btn im-btn-primary im-btn-xs"
                    data-mdoc-apply="${index}">${esc(K.action)}</button>
            <button type="button" class="im-btn im-btn-ghost im-btn-xs"
                    data-mdoc-dismiss="${index}">Not an issue</button>
            <small>${esc(K.lead)}</small>
          </footer>
        </article>`;
    }

    function chipsHtml() {
        const total = ORDER.reduce((n, k) => n + (state.counts[k] || 0), 0);
        const chip = (key, label, n, active) =>
            `<button type="button" class="im-chip${active ? " active" : ""}"
                     data-mdoc-kind="${esc(key)}">${esc(label)}<em>${n}</em></button>`;
        return chip("", "Everything", total, !state.kind)
            + ORDER.filter((k) => state.counts[k])
                   .map((k) => chip(k, KINDS[k].label, state.counts[k], state.kind === k))
                   .join("");
    }

    function render() {
        const host = $("mdocPanel");
        if (!host) return;
        const shown = state.kind
            ? state.findings.filter((f) => f.kind === state.kind)
            : state.findings;
        host.innerHTML = `
          <div class="mdoc-head">
            <div>
              <h3>Catalogue review</h3>
              <p>Problems that are not a blank field. Nothing changes until you
                 press something, and every ruling is remembered.</p>
            </div>
            <button type="button" class="im-btn im-btn-xs" id="mdocClose">Back to the list</button>
          </div>
          <div class="im-chiprow mdoc-chips">${chipsHtml()}</div>
          ${shown.length
            ? `<div class="mdoc-list">${shown.map((f) => cardHtml(f, state.findings.indexOf(f))).join("")}</div>`
            : `<p class="mdoc-empty">${state.findings.length
                 ? "Nothing left under this heading."
                 : "Nothing to review - the catalogue has no problems this check can find."}</p>`}`;
        wire(host);
    }

    function wire(host) {
        const close = $("mdocClose");
        if (close) close.onclick = () => hide();
        host.querySelectorAll("[data-mdoc-kind]").forEach((b) => {
            b.onclick = () => { state.kind = b.dataset.mdocKind || ""; render(); };
        });
        host.querySelectorAll("[data-mdoc-dismiss]").forEach((b) => {
            b.onclick = () => act(Number(b.dataset.mdocDismiss), "dismiss", b);
        });
        host.querySelectorAll("[data-mdoc-apply]").forEach((b) => {
            b.onclick = () => act(Number(b.dataset.mdocApply), "apply", b, host);
        });
    }

    /* One finding at a time, deliberately. A merge deletes rows; a batch that
       half-succeeds has no honest report to give. */
    async function act(index, what, button, host) {
        const finding = state.findings[index];
        if (!finding || state.busy) return;
        state.busy = true;
        const label = button.textContent;
        button.disabled = true;
        button.textContent = what === "dismiss" ? "Noting..." : "Working...";
        try {
            let r;
            if (what === "dismiss") {
                r = await api().md_dismiss(finding.kind, finding.key);
            } else if (finding.proposal && finding.proposal.action === "describe") {
                r = await saveDescriptions(finding, index, host);
            } else {
                r = await api().md_apply(finding.kind, finding.key, finding.proposal);
            }
            if (!r || r.ok === false) {
                toast((r && r.error) || "That could not be applied", true);
                return;
            }
            toast(what === "dismiss" ? "Noted - you will not be asked again"
                                     : "Done");
            await load();
        } catch (e) {
            toast("That could not be applied", true);
        } finally {
            state.busy = false;
            if (button.isConnected) { button.disabled = false; button.textContent = label; }
        }
    }

    /* Descriptions are saved one record at a time through the ordinary
       `mi_save`, so the same validation runs as when they are typed by hand.
       A row left blank is left alone - an empty patch would CLEAR it. */
    async function saveDescriptions(finding, index, host) {
        const boxes = (host || document).querySelectorAll(`[data-mdoc-desc^="${index}:"]`);
        let wrote = 0;
        for (const box of boxes) {
            const itemId = String(box.dataset.mdocDesc).split(":")[1];
            const value = String(box.value || "").trim();
            if (!value) continue;
            const row = ((finding.evidence || {}).rows || [])
                .find((r) => String(r.item_id) === itemId);
            if (row && String(row.description || "").trim() === value) continue;
            const r = await api().mi_save(itemId, { description: value });
            if (r && r.ok) wrote++;
        }
        if (!wrote) {
            return { ok: false, error: "Type what makes each one different first" };
        }
        // Telling them apart IS the fix, so the finding is answered.
        await api().md_dismiss(finding.kind, finding.key);
        return { ok: true };
    }

    async function load() {
        let r = null;
        try { r = await api().md_scan(); } catch (e) { r = null; }
        if (!r || !r.ok) {
            state.findings = []; state.counts = {};
            toast("Couldn't read the catalogue review", true);
        } else {
            state.findings = r.findings || [];
            state.counts = r.counts || {};
        }
        render();
    }

    function panel() {
        let host = $("mdocPanel");
        if (host) return host;
        const body = document.querySelector("#itemMasterView .im-body");
        if (!body) return null;
        host = document.createElement("section");
        host.id = "mdocPanel";
        host.className = "mdoc-panel";
        host.hidden = true;
        body.parentNode.insertBefore(host, body);
        return host;
    }

    function hide() {
        const host = $("mdocPanel");
        if (host) host.hidden = true;
        const body = document.querySelector("#itemMasterView .im-body");
        const filters = document.querySelector("#itemMasterView .im-filters");
        if (body) body.hidden = false;
        if (filters) filters.hidden = false;
        state.open = false;
    }

    async function open() {
        const host = panel();
        if (!host) { toast("The Master Data screen is not open", true); return; }
        const body = document.querySelector("#itemMasterView .im-body");
        const filters = document.querySelector("#itemMasterView .im-filters");
        if (body) body.hidden = true;
        if (filters) filters.hidden = true;
        host.hidden = false;
        state.open = true;
        host.innerHTML = `<p class="mdoc-empty">Checking the catalogue…</p>`;
        await load();
    }

    /* For the Health panel's one-line summary, so the way in sits where you
       already look for the state of the catalogue. */
    async function count() {
        try {
            const r = await api().md_scan();
            if (!r || !r.ok) return 0;
            return (r.findings || []).length;
        } catch (e) { return 0; }
    }

    window.MasterDoctor = { open, hide, count, KINDS, _state: state,
                            _cardHtml: cardHtml, _rowsHtml: rowsHtml,
                            _priceNote: priceNote, _chipsHtml: chipsHtml };
})();
