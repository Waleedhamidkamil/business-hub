/*
   Make DN / Make Invoice from a Purchase Order  (2026-09-05).

   A PO used to be a dead end. It is written in Arabic; the delivery note and
   invoice that follow it are written in English, so the same goods were typed
   out a second and a third time. They never needed to be: a PO save already
   teaches the catalogue through `master_items.remember`, so most of these items
   are on file with their English name, unit, sell rate and cost.

   So this is a LOOKUP feature, not a data-entry one. It asks
   `master_data.resolve_items` for the English side of the PO's Arabic names -
   the resolver builds its candidate list in the ASKING document's language, so
   passing doc_type "dn"/"inv" with Arabic names returns English - and hands the
   filled rows to the DN or Invoice editor. The review screen only exists for
   the minority of rows where the catalogue does not know the English side yet,
   and whatever is typed there is written BACK, so the next PO never asks again.

   ONE implementation for both buttons. This codebase has been bitten repeatedly
   by per-editor copies drifting apart (the signed-copy dialog, the
   cost-currency rules), so `kind` is a parameter and the editors hold only the
   thin hand-off that has to touch their private state.

   THE RULE THIS FILE IS BUILT AROUND
   ----------------------------------
   A TIER IS NOT A READINESS SIGNAL. `master_data._items_from_records` names
   each record with `prefer(name_en, name_ar, lang)` and `prefer` FALLS BACK to
   the other language, so a record known only in Arabic is listed under its
   Arabic name even when we asked for English - and matches, confidently, at a
   high tier. Deciding "this row is ready for a client's invoice" from the tier
   puts Arabic on that invoice. Readiness is a SCRIPT test on the value itself,
   per field. See `tests/po_make_document_resolve_backend.test.py`, which pins
   the trap so this check cannot be mistaken for belt-and-braces.
*/
(function () {
    "use strict";

    const api = () => window.pywebview.api;
    const MDF = () => window.MasterDataFill || {};
    const toast = (m, bad) => (window.toast ? window.toast(m, bad) : undefined);

    function esc(v) {
        const f = MDF().escText;
        if (f) return f(v);
        return String(v == null ? "" : v).replace(/[&<>"']/g, c => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }
    function hasValue(v) { return v !== undefined && v !== null && String(v) !== ""; }

    /* The SAME range the backend uses (`master_data._ARABIC_NAME`). Two scripts
       disagreeing about what counts as Arabic is how a row passes the frontend
       check and fails the human one. */
    const ARABIC = /[؀-ۿ]/;
    function isArabic(v) { return ARABIC.test(String(v == null ? "" : v)); }

    const KINDS = {
        dn:  { label: "Delivery Note", verb: "delivered",
               done: "delivered", title: "Make Delivery Note" },
        inv: { label: "Invoice", verb: "invoiced",
               done: "invoiced", title: "Make Invoice" },
        // The third caller is not a document at all: saving a PO that named
        // items the catalogue has never seen in English. Same rows, same
        // write-back, but nothing is being printed, so there is no gate and no
        // money, see `catalogue` in openReview.
        catalogue: { label: "Master Data", verb: "recorded",
                     done: "recorded", title: "Complete item details",
                     catalogue: true },
    };

    // ==================================================================
    // Pure helpers - the parts worth testing on their own.
    // ==================================================================

    /* Which PO lines can become a client document, grouped by the client.

       A PO line's `company` IS the client to bill - that is what
       `database.pos_awaiting_invoice` keys on. A line tagged `internal` is for
       our own office and is never offered; a line with no company at all cannot
       be addressed to anyone, so it is not offered either. */
    function billableGroups(rows, tags, doneMap) {
        const tagOf = (name) => {
            const key = String(name || "").trim().toLowerCase();
            const hit = Object.keys(tags || {}).find(
                k => String(k).trim().toLowerCase() === key);
            return hit ? String(tags[hit] || "").trim().toLowerCase() : "";
        };
        const groups = [];
        const byName = new Map();
        (rows || []).forEach((row, i) => {
            const company = String((row && row.company) || "").trim();
            if (!company) return;                       // addressed to nobody
            if (tagOf(company) === "internal") return;  // our own office
            if (!byName.has(company)) {
                const g = { company, lines: [] };
                byName.set(company, g);
                groups.push(g);
            }
            // Keys arrive from the backend as STRINGS.
            const done = (doneMap && (doneMap[String(i)] || doneMap[i])) || null;
            byName.get(company).lines.push({ index: i, row, done });
        });
        return groups;
    }

    /* Everything a decision knows, in one place.

       `fill` is what goes on the document; `target` is what may be written back
       to. They are deliberately separate: a write-back key must never be able
       to reach a document row, and a document value must never be able to
       decide which record gets edited. */
    /* A `similar` tier is a GUESS, and a guess is not a fill.

       `resolve_items` is explicit about this: for `similar` it leaves `fill`
       empty and sets no top-level `item_id`, "so a caller that writes back has
       to go through the one the user picked". But `MasterDataFill.fillFor`
       falls back to `suggested_fill` - correct in the paste grid, where the
       user ticks each field before it lands, and wrong here, where a filled
       row is a row this file marks READY and never shows anyone. A 0.83 match
       ("Whiteboard Set" -> Whiteboard Eraser) then printed a stranger's name
       and price on a delivery note with nobody asked. Pinned in
       `tests/po_handoff_review.test.js`. */
    function appliedFill(result) {
        if (result && result.tier === "similar") return {};
        return (MDF().fillFor ? MDF().fillFor(result, null) : {}) || {};
    }

    function decisionFrom(result, poRow) {
        const fill = appliedFill(result);
        return {
            query: result.query || "",
            result,
            poRow,
            rowIndexes: [],
            mode: "auto",           // auto | candidate | manual | asis | exclude
            candidate: null,
            typed: {},
            fill,
        };
    }

    /* What this decision currently puts on the document. */
    function effectiveLocalUnit(out, d) {
        const result = { ...(out || {}) };
        if ((!hasValue(result.unit) || isArabic(result.unit))
                && hasValue(d && d.suggestedUnit) && !isArabic(d.suggestedUnit)) {
            result.unit = d.suggestedUnit;
        }
        return result;
    }

    function decisionFill(d) {
        if (d.mode === "candidate" && d.candidate) {
            const f = (MDF().fillFor ? MDF().fillFor(d.result, d.candidate) : {}) || {};
            const t = d.typed || {};
            const out = { ...f, item: d.candidate.name || f.item };
            if (hasValue(t.unit_en)) out.unit = t.unit_en;
            if (hasValue(t.description_en)) out.description = t.description_en;
            if (hasValue(t.rate)) { out.rate = t.rate; out.rate_ccy = t.rate_ccy || "USD"; }
            if (hasValue(t.cost)) { out.cost = t.cost; out.cost_ccy = t.cost_ccy || "USD"; }
            return effectiveLocalUnit(out, d);
        }
        if (d.mode === "manual" || d.mode === "asis") {
            const t = d.typed || {};
            const out = { ...d.fill };
            if (hasValue(t.name_en)) out.item = t.name_en;
            if (hasValue(t.unit_en)) out.unit = t.unit_en;
            if (hasValue(t.description_en)) out.description = t.description_en;
            // Money moves as a PAIR or not at all.
            if (hasValue(t.rate)) { out.rate = t.rate; out.rate_ccy = t.rate_ccy || "USD"; }
            if (hasValue(t.cost)) { out.cost = t.cost; out.cost_ccy = t.cost_ccy || "USD"; }
            return effectiveLocalUnit(out, d);
        }
        return effectiveLocalUnit(d.fill, d);
    }

    /* Is this row fit to print for a client, FIELD BY FIELD and by script.

       Never by tier - see the header. An Arabic description is dropped rather
       than reported: the document simply goes out without one, which is fine,
       whereas Arabic prose on an English invoice is not. */
    function readinessFor(fill, poRow, kind) {
        const missing = [];
        const name = (fill || {}).item;
        if (!hasValue(name) || isArabic(name)) missing.push("name");
        const unit = (fill || {}).unit;
        if (hasValue(unit)) {
            if (isArabic(unit)) missing.push("unit");
        } else if (hasValue(poRow && poRow.unit)) {
            missing.push("unit");         // the PO had one; the document needs it too
        }
        // An invoice with no rate saves as a draft with no PDF and cannot be
        // linked to the PO (`set_po_fulfillment` refuses it), so the rate is
        // required there. A delivery note is legitimately unpriced.
        if (kind === "inv" && !hasValue((fill || {}).rate)) missing.push("rate");
        return { ready: missing.length === 0, missing };
    }

    /* Only the candidates that would actually solve the problem: an English
       one. The resolver's own ranking is kept - this filters, never re-ranks. */
    function targetSuggestions(result, want) {
        const dir = direction(want);
        return ((result && result.suggestions) || [])
            .filter(s => s && s.name && !dir.missing(s.name));
    }

    function englishSuggestions(result) { return targetSuggestions(result, "en"); }

    /* The patch to write back - BY INCLUSION.

       `save_record` treats a key that is PRESENT with an empty value as an
       instruction: an empty money value NULLs the amount and its currency, and
       a blank language-free `unit` clears BOTH units. So a field the user did
       not fill must be ABSENT, not empty. Pinned in
       `tests/po_master_writeback_backend.test.py`.

       Never the bare `name` key (it is not language-free - `save_record` drops
       it and reports success), never the bare `unit` key, and never a money
       amount without its currency. */
    function buildMasterPatch(d) {
        if (d.mode === "exclude" || d.mode === "asis") return null;
        if (d.mode === "manual" && needsVariantChoice(d)) return null;

        /* WANTED is the half being filled in, SOURCE the half already on the
           document. `en` for a PO's review, `ar` for a quotation's - see
           DIRECTIONS. Everything below is written in those terms so there is
           one write-back, not a mirrored copy of one. */
        const dir = direction(d.dir);
        const W = dir.target, S = dir.source;
        const srcName = String(d.query || "").trim();
        const srcUnit = String((d.poRow && d.poRow.unit) || "").trim();
        // The source unit is only worth filing when it is written in the
        // source language: an English unit typed on a PO is not Arabic.
        const srcUnitFits = srcUnit && dir.missing(srcUnit);
        // Same for the line's description - and it matters more: it is what
        // tells one variant from another (18 L / 20 L), so recording it on the
        // variant the user picked is what lets the next order find that
        // variant without asking (2026-10-03, A-117).
        const srcDesc = String((d.poRow && d.poRow.description) || "").trim();
        const srcDescFits = srcDesc && dir.missing(srcDesc);

        if (d.mode === "candidate" && d.candidate) {
            // Accepting an existing record records only the translation, and
            // only into a BLANK slot. Writing over a name the record already
            // has would silently merge two different products.
            const patch = {};
            if (srcName && !String(d.candidate[`name_${S}`] || "").trim()) {
                patch[`name_${S}`] = srcName;
            }
            if (srcUnitFits && !String(d.candidate[`unit_${S}`] || "").trim()) {
                patch[`unit_${S}`] = srcUnit;
            }
            if (srcDescFits && !String(d.candidate[`description_${S}`] || "").trim()) {
                patch[`description_${S}`] = srcDesc;
            }
            const t = d.typed || {};
            if (hasValue(t[`unit_${W}`]) && !String(d.candidate[`unit_${W}`] || "").trim()) {
                patch[`unit_${W}`] = String(t[`unit_${W}`]).trim();
            } else if (hasValue(d.suggestedUnit)
                    && !String(d.candidate[`unit_${W}`] || "").trim()) {
                patch[`unit_${W}`] = String(d.suggestedUnit).trim();
            }
            if (hasValue(t[`description_${W}`])
                    && !String(d.candidate[`description_${W}`] || "").trim()) {
                patch[`description_${W}`] = String(t[`description_${W}`]).trim();
            }
            if (hasValue(t.rate)) {
                patch.price_native = t.rate;
                patch.price_native_ccy = t.rate_ccy || "USD";
            }
            if (hasValue(t.cost)) {
                patch.cost_native = t.cost;
                patch.cost_native_ccy = t.cost_ccy || "USD";
            }
            return Object.keys(patch).length ? patch : null;
        }

        if (d.mode !== "manual") return null;
        const t = d.typed || {};
        const r = d.result || {};
        const patch = {};
        if (hasValue(t[`name_${W}`])) patch[`name_${W}`] = String(t[`name_${W}`]).trim();
        if (hasValue(t[`unit_${W}`])) patch[`unit_${W}`] = String(t[`unit_${W}`]).trim();
        else if (hasValue(d.suggestedUnit) && !String(r[`unit_${W}`] || "").trim()) {
            patch[`unit_${W}`] = String(d.suggestedUnit).trim();
        }
        if (hasValue(t[`description_${W}`])) {
            patch[`description_${W}`] = String(t[`description_${W}`]).trim();
        }
        // The document's own side, only where the record has none.
        if (srcName && !String(r[`name_${S}`] || "").trim()) patch[`name_${S}`] = srcName;
        if (srcUnitFits && !String(r[`unit_${S}`] || "").trim()) patch[`unit_${S}`] = srcUnit;
        if (srcDescFits && !String(r[`description_${S}`] || "").trim()) {
            patch[`description_${S}`] = srcDesc;
        }
        // Money: both legs or neither. Stored AS TYPED, in the currency it was
        // typed in - never converted (master_data.py:963-971).
        if (hasValue(t.rate)) {
            patch.price_native = t.rate;
            patch.price_native_ccy = t.rate_ccy || "USD";
        }
        if (hasValue(t.cost)) {
            patch.cost_native = t.cost;
            patch.cost_native_ccy = t.cost_ccy || "USD";
        }
        return Object.keys(patch).length ? patch : null;
    }

    /* Which record a write-back would edit, or null when there is nothing to
       edit and a new record has to be created. `similar` results deliberately
       carry no top-level item_id - the user must pick a candidate first. */
    function writeTargetId(d) {
        if (d.mode === "candidate" && d.candidate) return d.candidate.item_id || null;
        if (d.mode === "manual") return (d.result && d.result.item_id) || null;
        return null;
    }

    /* The destination row, built EXPLICITLY.

       Not through `smartMapRow`. Two reasons, both real: its `rate` alias group
       includes "cost" (app.js), so a row carrying a private cost and no rate
       would print the cost as the client's sell price; and the PO's `company`
       would fall through to its leftovers pass and be appended into the
       description. Ten lines of explicit mapping cannot do either. */
    function buildHandoffRow(poRow, fill) {
        const out = {
            item: fill.item || "",
            description: (fill.description && !isArabic(fill.description))
                ? fill.description : "",
            unit: fill.unit || "",
            qty: (poRow && poRow.qty != null) ? poRow.qty : "",
        };
        // Money always travels with its unit, on both legs. A number without
        // its currency is the ~1400x error this app keeps having to avoid.
        if (hasValue(fill.rate)) {
            out.rate = fill.rate;
            out.rate_ccy = fill.rate_ccy || "USD";
        }
        if (hasValue(fill.cost)) {
            out.cost = fill.cost;
            out.cost_ccy = fill.cost_ccy || "USD";
        }
        /* Deliberately NOT calling `reconcileImportedCostCurrency` anywhere on
           this path, tempting though the DN->INV prior art makes it. That
           converts by the SOURCE DOCUMENT's peg; a PO has no costs and no cost
           currency at all, and the master's cost is native-with-its-own-stamp
           by design. Running it here would convert a dinar master cost by a
           peg it never belonged to. */
        return out;
    }

    // ==================================================================
    // Line picker - shown ONLY when the PO's billable lines span more than
    // one company. One company means the question has one answer.
    // ==================================================================
    async function pickLines(poNo, groups, kind) {
        const K = KINDS[kind];
        const body = groups.map((g, gi) => `
            <section class="poh-group" data-poh-group="${gi}">
              <label class="poh-group-head">
                <input type="checkbox" class="poh-group-chk" data-g="${gi}" checked>
                <strong>${esc(g.company)}</strong>
                <span class="poh-count">${g.lines.length} line${g.lines.length === 1 ? "" : "s"}</span>
              </label>
              <table class="poh-table"><tbody>
                ${g.lines.map(l => `
                  <tr class="${l.done ? "is-done" : ""}">
                    <td class="poh-chk"><input type="checkbox" class="poh-line-chk"
                        data-g="${gi}" data-i="${l.index}" ${l.done ? "" : "checked"}></td>
                    <td>${esc(l.row.name || "")}</td>
                    <td class="poh-dim">${esc(l.row.description || "")}</td>
                    <td class="poh-num">${esc(l.row.qty == null ? "" : l.row.qty)}</td>
                    <td class="poh-num">${esc(l.row.unit || "")}</td>
                    <td class="poh-state">${l.done
                        ? `already ${esc(K.done)} · ${esc(l.done.source_no || "")}` : ""}</td>
                  </tr>`).join("")}
              </tbody></table>
            </section>`).join("");

        const h = window.Popup.open({
            title: K.title,
            // Popup sets the subtitle with textContent. Escaping it here would
            // print the entities themselves (`Ali&#39;s Co`), not the text.
            subtitle: `${poNo}: this order covers more than one company. `
                    + `Tick the lines to put on this ${K.label.toLowerCase()}.`,
            size: "lg",
            dir: "ltr",
            className: "poh-modal",
            html: true,
            body: `<div class="poh-pick">${body}</div>`,
            actions: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Continue", value: "ok", cls: "primary", keep: true },
            ],
        });
        const root = h.panel;
        const lineBoxes = () => Array.from(root.querySelectorAll(".poh-line-chk"));
        const syncGroup = (gi) => {
            const own = lineBoxes().filter(b => b.dataset.g === String(gi));
            const on = own.filter(b => b.checked).length;
            const head = root.querySelector(`.poh-group-chk[data-g="${gi}"]`);
            if (!head) return;
            head.checked = on === own.length && own.length > 0;
            head.indeterminate = on > 0 && on < own.length;
        };
        root.querySelectorAll(".poh-group-chk").forEach(head => {
            head.onchange = () => {
                lineBoxes().filter(b => b.dataset.g === head.dataset.g)
                           .forEach(b => { b.checked = head.checked; });
            };
        });
        lineBoxes().forEach(b => b.addEventListener("change",
            () => syncGroup(b.dataset.g)));
        groups.forEach((_, gi) => syncGroup(gi));

        return new Promise((resolve) => {
            const btn = root.querySelector("[data-value='ok']");
            if (btn) btn.onclick = () => {
                const picked = lineBoxes().filter(b => b.checked)
                                          .map(b => parseInt(b.dataset.i, 10));
                if (!picked.length) { toast("Tick at least one line", true); return; }
                h.close();
                resolve(picked);
            };
            h.result.then((v) => { if (v !== "ok") resolve(null); });
        });
    }

    // ==================================================================
    // Shared item review adapter
    // ==================================================================
    /* ItemReviewPopup owns every review pixel. This file owns only the
       document meaning: what is missing, what may be written back, and what is
       fit to print. */
    function reviewRowFromDecision(d, kind, index, want) {
        const K = KINDS[kind] || {};
        const dir = direction(want);
        const W = dir.target;
        const fill = decisionFill(d);
        const missing = K.catalogue
            ? (recordNeedsLanguage(d.result, W) ? ["name"] : [])
            : readinessFor(fill, d.poRow, kind).missing;
        // A value is only usable in the box for the half being FILLED: an
        // Arabic name is not an English detail, and vice versa.
        const usable = (value) => hasValue(value) && !dir.missing(value) ? value : "";
        const candidates = targetSuggestions(d.result, W);
        const ambiguous = !!(d.result && d.result.ambiguous_variants && candidates.length > 1);
        return {
            id: index,
            sourceName: d.query,
            sourceDescription: (d.poRow && d.poRow.description) || "",
            sourceUnit: (d.poRow && d.poRow.unit) || "",
            targetName: usable(fill.item) || usable(d.result && d.result[`name_${W}`]),
            targetDescription: usable(fill.description)
                || usable(d.result && d.result[`description_${W}`]),
            targetUnit: usable(fill.unit) || usable(d.suggestedUnit),
            rate: fill.rate,
            rateCurrency: fill.rate_ccy,
            cost: fill.cost,
            costCurrency: fill.cost_ccy,
            matched: d.result && d.result.item_id != null,
            missing,
            ambiguous,
            candidates: (ambiguous || missing.includes("name")) ? candidates : [],
        };
    }

    function applyReviewResults(decisions, rows) {
        const byId = new Map((rows || []).map(row => [String(row.id), row]));
        decisions.forEach((d, index) => {
            if (d.rowReady) return;
            const row = byId.get(String(index));
            if (!row) return;
            d.mode = row.mode || "manual";
            d.candidate = row.candidate || null;
            d.typed = { ...(row.typed || {}) };
            d.typedOnly = d.mode === "asis";
        });
        return decisions;
    }

    async function aiSuggestions(requests, want) {
        return api().tr_suggest((requests || []).map(row => ({
            name: row.name,
            description: row.description,
            unit: row.unit,
            candidates: (row.candidates || []).map(candidate => ({
                name: candidate.name || "",
                description: candidate.description || "",
                unit: candidate.unit || "",
            })),
        })), direction(want).target);
    }

    function needsVariantChoice(d) {
        return !!(d && d.result && d.result.ambiguous_variants)
            && d.mode !== "candidate" && d.mode !== "asis" && d.mode !== "exclude";
    }

    function reviewItemLabel(d) {
        const fill = decisionFill(d);
        const english = String((fill && fill.item) || "").trim();
        return english && !isArabic(english) ? english : String(d.query || "item").trim();
    }

    function joinedLabels(labels) {
        if (labels.length < 2) return labels[0] || "the item";
        if (labels.length === 2) return `${labels[0]} and ${labels[1]}`;
        return `${labels.slice(0, -1).join(", ")}, and ${labels[labels.length - 1]}`;
    }

    function reviewValidation(decisions, kind, catalogue) {
        if (catalogue) return { message: "", errors: [] };
        const errors = [];
        const rateLabels = [];
        const languageLabels = [];
        let choiceCount = 0;
        decisions.forEach((d, id) => {
            if (d.rowReady || d.mode === "exclude") return;
            const fields = readinessFor(decisionFill(d), d.poRow, kind).missing.slice();
            if (needsVariantChoice(d)) fields.push("choice");
            const unique = Array.from(new Set(fields));
            if (!unique.length) return;
            errors.push({ id, fields: unique });
            const label = reviewItemLabel(d);
            if (unique.includes("rate")) rateLabels.push(label);
            if (unique.includes("name") || unique.includes("unit")) languageLabels.push(label);
            if (unique.includes("choice")) choiceCount++;
        });

        const messages = [];
        if (languageLabels.length) {
            messages.push(`Add the missing English details for ${joinedLabels(languageLabels)}.`);
        }
        if (choiceCount) {
            messages.push(`Choose the correct Master Data variant for ${choiceCount}`
                + ` item${choiceCount === 1 ? "" : "s"}, or use document-only values.`);
        }
        if (rateLabels.length) {
            messages.push(`Enter a rate for ${joinedLabels(rateLabels)}. `
                + "AI completes language details, not prices.");
        }
        if (!errors.length && decisions.every(d => d.mode === "exclude")) {
            return { message: "Every item is skipped, so there is nothing to create", errors: [] };
        }
        return { message: messages.join(" "), errors };
    }

    async function aiAvailable() {
        try {
            const status = await api().tr_status();
            return !!(status && status.configured);
        } catch (e) { return false; }
    }

    async function openReview(poNo, decisions, kind, want) {
        const K = KINDS[kind];
        const dir = direction(want);
        const unready = decisions.filter(d => !d.rowReady);
        const ready = decisions.filter(d => d.rowReady);
        if (!window.ItemReviewPopup) {
            toast("The item review component is not available. Reload the app and try again.", true);
            return false;
        }
        const rows = unready.map(d => reviewRowFromDecision(d, kind, decisions.indexOf(d), want));
        const review = new window.ItemReviewPopup({
            title: K.catalogue ? dir.title : K.title,
            subtitle: K.catalogue
                ? `${poNo}: complete ${unready.length} item${unready.length === 1 ? "" : "s"} in Master Data`
                : `${poNo}: review only the details still needed for this ${K.label.toLowerCase()}`,
            primaryLabel: K.catalogue
                ? "Save to Master Data" : `Create ${K.label.toLowerCase()}`,
            sourceLabel: dir.sourceLabel,
            targetLabel: dir.targetLabel,
            sourceLanguage: dir.source,
            targetLanguage: dir.target,
            readyCount: ready.length,
            rows,
            allowMoney: !K.catalogue,
            allowDocumentOnly: !K.catalogue,
            suggestLabel: "Complete Missing Details with AI",
            // The half being FILLED, or the model answers in the language
            // already on the page and the popup silently applies nothing.
            suggest: (requests) => aiSuggestions(requests, want),
            suggestAvailable: aiAvailable,
            toast,
            validate(resultRows) {
                applyReviewResults(decisions, resultRows);
                // The PO is already saved in catalogue mode. Partial Master
                // Data is useful, so this path never applies a print gate.
                if (K.catalogue) return "";
                return reviewValidation(decisions, kind, false);
            },
        });
        const result = await review.open();
        if (!result.ok) return false;
        applyReviewResults(decisions, result.rows);
        return true;
    }

    // ==================================================================
    // Write-back
    // ==================================================================
    /* Fires on Confirm, BEFORE the document is built. The review screen is
       where the user made the decision; Master Data is a master, not a log of
       what happened to be issued, so a cancelled DN still leaves the catalogue
       better than it found it.

       Best-effort per row: a failure downgrades that row to "use here only" and
       the document is still built. Losing the document because the catalogue
       write failed would be the worse trade. */
    async function writeBack(decisions) {
        const report = { updated: 0, created: 0, failed: 0 };
        const seen = new Map();          // item_id -> the decision that wrote it
        for (const d of decisions) {
            if (d.mode === "exclude" || d.typedOnly) continue;
            const patch = buildMasterPatch(d);
            if (!patch) continue;
            const targetId = writeTargetId(d);
            if (targetId != null && seen.has(targetId)) continue;   // deduped
            try {
                let r;
                if (targetId != null) {
                    r = await api().mi_save(targetId, patch);
                    if (r && r.ok) { report.updated++; seen.set(targetId, d); }
                    else report.failed++;
                } else {
                    r = await api().mi_create(patch);
                    if (r && r.ok) { report.created++; }
                    else if (r && r.already && r.already.item_id) {
                        // `create_record` refuses a second record for one
                        // product and names the one it collided with. That is
                        // the record we meant all along.
                        const r2 = await api().mi_save(r.already.item_id, patch);
                        if (r2 && r2.ok) { report.updated++; seen.set(r.already.item_id, d); }
                        else report.failed++;
                    } else report.failed++;
                }
            } catch (e) { report.failed++; }
        }
        return report;
    }

    // ==================================================================
    // Entry point
    // ==================================================================
    /* Make DN and Make Invoice both await the bridge before anything modal is
       on screen, so a second click in that window used to start a second run
       over the same PO - two reviews, two write-backs, two documents. */
    let _running = "";
    async function poMakeDocument(kind) {
        const K = KINDS[kind];
        if (!K) return;
        if (_running) {
            toast(`Still working on the ${KINDS[_running].label.toLowerCase()}`, true);
            return;
        }
        _running = kind;
        try {
            await _poMakeDocument(kind, K);
        } finally {
            _running = "";
        }
    }

    async function _poMakeDocument(kind, K) {
        const infoModal = window.infoModal || (async (t, b) => toast(`${t}: ${b}`, true));

        // ---- 1. guard + save gate ------------------------------------
        const st = (window.getPoSaveState && window.getPoSaveState()) || {};
        if (!st.filled) {
            await infoModal("Nothing to convert", "Add items to the purchase order first.");
            return;
        }
        if (st.mode !== "edit" || st.dirty) {
            const choice = await window.showModal({
                title: st.dirty ? "Unsaved changes" : "Save the purchase order first",
                body: st.dirty
                    ? `This purchase order has unsaved changes. Save it before making a ${K.label.toLowerCase()}?`
                    : `Save this purchase order first, then I'll fill a ${K.label.toLowerCase()} from it.`,
                buttons: [
                    { label: "Cancel", value: "cancel", cls: "ghost" },
                    { label: "Save first", value: "save", cls: "primary" },
                ],
            });
            if (choice !== "save") return;
            const ok = await window.savePO({ stay: true, skipConfirm: true });
            if (!ok) return;
        }
        // Recover the number defensively: saving can reset the editor.
        const el = document.getElementById("poNo");
        const after = (window.getPoSaveState && window.getPoSaveState()) || {};
        const poNo = ((el && el.value) || "").trim() || after.poNo || "";
        if (!poNo) {
            await infoModal("Can't find the PO number",
                "Save the purchase order, open it from the list, then try again.");
            return;
        }

        // ---- 2. read the SAVED PO ------------------------------------
        /* Not `state.rows`. `savePO` persists filledRows(), so blank editor
           rows are dropped and the editor's index is NOT the saved index -
           and the saved index is what `set_po_fulfillment` marks. Reading the
           record back makes the indexes correct by construction, and hands us
           the existing fulfillment for free. */
        let rec = null;
        try { rec = await api().load_po(poNo); } catch (e) { rec = null; }
        if (!rec || !Array.isArray(rec.rows) || !rec.rows.length) {
            await infoModal("Couldn't read that purchase order",
                `${poNo} has no stored items to convert.`);
            return;
        }
        let tags = {};
        try { tags = (await api().get_po_company_tags()) || {}; } catch (e) { tags = {}; }
        const fulfill = rec.fulfillment || {};
        const doneMap = (kind === "dn" ? fulfill.delivered : fulfill.invoiced) || {};
        const groups = billableGroups(rec.rows, tags, doneMap);

        if (!groups.length) {
            await infoModal("Nothing to bill",
                "Every line on this purchase order is for our own office (or has no "
                + "company on it), so there is nothing to deliver or invoice. "
                + "Company classification lives in PO Settings.");
            return;
        }

        // ---- 3. which lines ------------------------------------------
        let indexes;
        let client = "";
        if (groups.length > 1) {
            indexes = await pickLines(poNo, groups, kind);
            if (!indexes || !indexes.length) return;
            const companies = new Set(groups.filter(
                g => g.lines.some(l => indexes.includes(l.index))).map(g => g.company));
            // One client field per document; inventing one is worse than a
            // blank the user fills in.
            client = companies.size === 1 ? [...companies][0] : "";
        } else {
            indexes = groups[0].lines.map(l => l.index);
            client = groups[0].company;
        }
        const picked = indexes.map(i => ({ index: i, row: rec.rows[i] }))
                              .filter(p => p.row);

        // ---- 4. resolve ----------------------------------------------
        /* Collapse only identical material rows to one decision, then fan the
           answer back to every row that used it. Name alone is not an identity:
           A-89 has two wall splits whose descriptions hold different sizes. */
        const byIdentity = new Map();
        picked.forEach((p, n) => {
            const name = String(p.row.name || "").trim();
            if (!name) return;
            const key = handoffReviewKey(p.row);
            if (!byIdentity.has(key)) byIdentity.set(key, { name, row: p.row, at: [] });
            byIdentity.get(key).at.push(n);
        });
        const queries = [...byIdentity.values()];
        if (!queries.length) {
            await infoModal("Nothing to convert", "None of the picked lines has an item name.");
            return;
        }

        let data = null;
        try {
            data = await window.withLoading(null, () => api().im_resolve(
                queries.map(q => q.name),
                kind,                    // "dn"/"inv" -> the resolver answers in ENGLISH
                "",
                "USD", 0,                // a NEW document starts in dollars; peg 0
                false,                   // no images on a DN/INV
                "USD", 0,                // means "use the profile peg" (_cost_in)
                queries.map(q => lineDetails(q.row)),
            ));
        } catch (e) { data = null; }
        if (!data || data.ok === false) {
            toast((data && data.error) || "Master Data lookup failed", true);
            return;
        }

        const units = await unitMap();
        const decisions = (data.results || []).map((res, qi) => {
            const q = queries[qi];
            const d = decisionFrom(res, q.row);
            d.rowIndexes = q.at;
            applySuggestedUnit(d, units);
            d.rowReady = readinessFor(decisionFill(d), q.row, kind).ready
                && !needsVariantChoice(d);
            return d;
        });

        // ---- 5. review, only if something needs it -------------------
        if (decisions.some(d => !d.rowReady)) {
            const go = await openReview(poNo, decisions, kind);
            if (!go) { toast("Cancelled, nothing was created"); return; }
            const report = await writeBack(decisions);
            const bits = [];
            if (report.updated) bits.push(`${report.updated} item${report.updated === 1 ? "" : "s"} updated`);
            if (report.created) bits.push(`${report.created} added`);
            if (report.failed) bits.push(`${report.failed} could NOT be saved to Master Data`);
            if (bits.length) toast(bits.join(", "), !!report.failed);
        }

        // ---- 6. build the rows and hand off --------------------------
        const rows = [];
        const keptIndexes = [];
        decisions.forEach((d) => {
            if (d.mode === "exclude") return;
            const fill = decisionFill(d);
            d.rowIndexes.forEach((n) => {
                const p = picked[n];
                if (!p) return;
                rows.push(buildHandoffRow(p.row, fill));
                keptIndexes.push(p.index);
            });
        });
        if (!rows.length) { toast("Nothing left to put on the document", true); return; }

        /* The PO's reference travels with the items (2026-09-09).

           `reference` is the app-only note that says WHAT this order was for -
           "اوكسجين وغاز", "20260902-CAMP" - and it is the same job when it
           becomes a delivery note or an invoice, so re-typing it was pure
           re-typing. Carried as the PO stored it: this is a copy, not a
           derivation, and it is never invented when the PO has none. */
        const payload = {
            poNo, client, rows,
            reference: String(rec.reference || "").trim(),
            link: { poNo, indexes: keptIndexes },
        };
        const hook = kind === "dn" ? window.createDnFromPo : window.createInvoiceFromPo;
        if (typeof hook !== "function") {
            await infoModal(`${K.label} module not loaded`,
                "Reload the app and try again.");
            return;
        }
        await hook(payload);
    }

    // ==================================================================
    // Linking, AFTER the document is saved
    // ==================================================================
    /* The link is `po_fulfillment` - the same table the manual PO linker
       writes, and the only mechanism `document_workflow` reads. No new table is
       needed, and no new bridge: `set_po_fulfillment` already accepts a PO's
       item indexes with source_kind "dn"/"inv".

       It must run AFTER the save, because the number does not exist until then.

       Returns true when the caller should clear its `sourcePo`. An UNPRICED
       INVOICE is the one interesting case: it saves as a draft with no PDF, and
       `set_po_fulfillment` deliberately refuses to link one ("complete invoice
       pricing before linking it to a PO"). Clearing on that failure would
       silently orphan the link, so we keep it and the next save retries - the
       write is `ON CONFLICT DO UPDATE`, so a retry is free. */
    async function linkPoAfterSave(sourcePo, kind, docNo) {
        if (!sourcePo || !sourcePo.poNo || !docNo) return true;
        const idxs = sourcePo.indexes || [];
        if (!idxs.length) return true;
        let r = null;
        try { r = await api().set_po_fulfillment(sourcePo.poNo, idxs, kind, docNo, true); }
        catch (e) { r = null; }
        if (r && r.ok) {
            toast(`Linked to ${sourcePo.poNo}`);
            return true;
        }
        toast(`Saved, but not yet linked to ${sourcePo.poNo}`
              + (kind === "inv" ? ", it will link once every line has a price." : "."),
              true);
        return false;
    }

    window.poMakeDocument = poMakeDocument;
    window.linkPoAfterSave = linkPoAfterSave;
    // ==================================================================
    // Saving a PO - offer to complete the items it just invented
    // ==================================================================
    /* Every PO line already becomes a Master Data record on save
       (`database.remember_item` -> `master_items.remember`), and an Arabic name
       lands in `name_ar`. So this is never "save it or not" - the record
       exists. What is missing is its English half, and that is what a delivery
       note or invoice from this order will need.

       Runs AFTER the save, on the STORED rows, and never blocks or fails it:
       the order is on disk before this is called, and a thrown error here must
       not make a successful save look failed. */
    /* WHICH HALF IS MISSING - the one thing that differs between the two
       save-time reviews (2026-09-10).

       A purchase order is written in Arabic and the catalogue needs the English
       half so a delivery note or invoice can be made from it. A quotation is
       written in English and the catalogue needs the ARABIC half, so the same
       goods are found when they are later ordered on an Arabic PO - 346 of the
       421 distinct items ever quoted (82%) have no Arabic name, and every one
       of them is invisible to a PO lookup, which is how the catalogue grows a
       second record for something it already has.

       Two descriptors rather than two implementations. This codebase has been
       bitten repeatedly by per-editor copies drifting apart, and a mirrored
       copy of a 200-line review is the same bet with worse odds. */
    const DIRECTIONS = {
        // The PO's review: Arabic on the page, English wanted.
        en: {
            target: "en", source: "ar",
            ask: "dn",                    // the resolver answers in ENGLISH
            title: "Complete item details",
            sourceLabel: "On the purchase order",
            targetLabel: "English details",
            missing: (name) => !hasValue(name) || isArabic(name),
        },
        // The quotation's review: English on the page, Arabic wanted.
        ar: {
            target: "ar", source: "en",
            ask: "po",                    // the resolver answers in ARABIC
            title: "Complete Arabic details",
            sourceLabel: "On the quotation",
            targetLabel: "Arabic details",
            missing: (name) => !hasValue(name) || !isArabic(name),
        },
    };
    function direction(want) { return DIRECTIONS[want === "ar" ? "ar" : "en"]; }

    function recordNeedsLanguage(result, want) {
        const dir = direction(want);
        const field = `name_${dir.target}`;
        const r = result || {};
        // The resolver reports the record it matched. No name in the wanted
        // language means the catalogue knows this item only in the other one.
        if (r.item_id != null) return dir.missing(r[field]);
        // An exact name can identify several genuine variants. That is an
        // ambiguity when creating a document, but it is not a new item when
        // every matching record already has that half. A-86's ماء بطل rows are
        // the real example this protects.
        const tied = r.ambiguous_variants && Array.isArray(r.suggestions)
            ? r.suggestions : [];
        if (tied.length) {
            return tied.some(item => dir.missing(item[field]));
        }
        return true;                                    // nothing on file at all
    }

    function recordNeedsEnglish(result) { return recordNeedsLanguage(result, "en"); }

    /* Plain `toLowerCase`, never the locale-aware one: this identity is STORED
       (in `doc_meta`, through `po_catalogue_review_mark`), and a key that
       depends on the machine's locale is a key that stops matching when the
       machine changes. Arabic has no case at all; the difference is the
       Turkish dotless i. */
    function normalReviewText(value) {
        return String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
    }

    function catalogueReviewKey(row) {
        return handoffReviewKey(row);
    }

    function handoffReviewKey(row) {
        row = row || {};
        return [row.name, row.description, row.unit].map(normalReviewText).join("\x1f");
    }

    /* What a line says BESIDE its name, for the resolver (2026-10-03).

       A-117 ordered دهن هيدروليك / 20 لتر. Master Data has that name twice,
       18 L and 20 L, and asking by name alone made a known item look unknown.
       With the description and unit the resolver can name the one variant the
       line is - by the same identity rule a document save uses. */
    function lineDetails(row) {
        row = row || {};
        return {
            description: String(row.description == null ? "" : row.description),
            unit: String(row.unit == null ? "" : row.unit),
        };
    }

    function unreviewedCatalogueDecisions(decisions, reviewed, force) {
        if (force) return decisions.slice();
        const known = new Set(Array.isArray(reviewed) ? reviewed : []);
        return decisions.filter(d => !known.has(d.reviewKey));
    }

    /* Units are not translated, they are looked up: the catalogue has already
       recorded an English unit for every Arabic one it uses. See
       `master_items.unit_english_map`. */
    const _unitMaps = {};
    async function unitMap(want) {
        const key = direction(want).target;
        if (_unitMaps[key]) return _unitMaps[key];
        try {
            const r = await api().mi_unit_map(key);
            _unitMaps[key] = (r && r.units) || {};
        } catch (e) { _unitMaps[key] = {}; }
        return _unitMaps[key];
    }

    function applySuggestedUnit(decision, units, want) {
        const dir = direction(want);
        const source = String((decision.poRow && decision.poRow.unit) || "").trim();
        const known = units && units[source];
        const current = decisionFill({ ...decision, suggestedUnit: "" }).unit;
        // Only overwrite when what we have is missing or in the WRONG half:
        // an Arabic unit is not an English detail, and the reverse.
        if (known && (!hasValue(current) || dir.missing(current))) {
            decision.suggestedUnit = known;
        }
        return decision;
    }

    // The database is authoritative across restarts. This small set keeps the
    // same behavior if an older bridge or a temporary database error prevents
    // the metadata call from completing.
    const _reviewedThisSession = new Map();

    /* Keyed by DOCUMENT TYPE as well as number, both here and in `doc_meta`.
       A purchase order and a quotation can carry the same number shape, and
       reviewing one must never silence the other. */
    async function reviewedCatalogueKeys(docNo, docType) {
        const slot = `${docType || "po"}|${docNo}`;
        const local = _reviewedThisSession.get(slot) || new Set();
        try {
            const r = await api().po_catalogue_review_get(docNo, docType || "po");
            for (const key of ((r && r.reviewed) || [])) local.add(key);
        } catch (e) { /* session fallback remains available */ }
        _reviewedThisSession.set(slot, local);
        return Array.from(local);
    }

    /* Remembering that a row was offered.

       Two different memories, because "I dealt with this" and "not now" are
       two different answers. Confirming writes to `doc_meta`, so the row is
       never offered again on any future save. Cancelling only quietens it for
       THIS session: the work was not done, and burning the row permanently
       left the user with no way back to it - nothing in the app passes
       `force`, so the popup was simply gone. A restart offers it again. */
    async function markCatalogueReviewed(docNo, decisions, persist, docType) {
        const keys = decisions.map(d => d.reviewKey).filter(Boolean);
        if (!keys.length) return;
        const slot = `${docType || "po"}|${docNo}`;
        const local = _reviewedThisSession.get(slot) || new Set();
        keys.forEach(key => local.add(key));
        _reviewedThisSession.set(slot, local);
        if (!persist) return;
        try { await api().po_catalogue_review_mark(docNo, keys, docType || "po"); }
        catch (e) { /* session fallback remains available */ }
    }

    /* ONE save-time review, both directions (2026-09-10).

       A purchase order is written in Arabic and needs its English half so a
       delivery note or invoice can be made from it. A quotation is written in
       English and needs its ARABIC half, so the same goods are FOUND when they
       are later ordered on a PO - 346 of the 421 items ever quoted (82%) have
       no Arabic name, and every one of them is invisible to a PO lookup, which
       is how the catalogue grows a second record for something it already has.

       Same rows, same popup, same write-back; `want` is the only difference.
       A mirrored copy of this would be the fourth time a per-editor duplicate
       drifted apart in this app.

       Runs AFTER the save, on the STORED rows, and never blocks or fails it:
       the document is on disk before this is called, and a thrown error here
       must not make a successful save look failed. */
    async function catalogueReview(docNo, spec) {
        spec = spec || {};
        const opts = spec.opts || {};
        const want = spec.want === "ar" ? "ar" : "en";
        const dir = direction(want);
        const docType = spec.docType || "po";
        try {
            const rows = (await spec.load()) || [];
            if (!rows.length) return null;

            let res;
            try {
                // Ask in the language being FILLED: `resolve_items` builds its
                // candidates in the asking document's language, so this reports
                // what the next document would find - the point of asking now.
                res = await api().im_resolve(rows.map(r => r.name), dir.ask, "",
                    "USD", 0, false, "USD", 0, rows.map(lineDetails));
            } catch (e) { return null; }
            const results = (res && res.results) || [];

            const units = await unitMap(want);
            const decisions = [];
            const seen = new Set();
            results.forEach((result, i) => {
                const row = rows[i] || {};
                if (!String(row.name || "").trim()) return;
                if (!recordNeedsLanguage(result, want)) return;
                const key = catalogueReviewKey(row);
                // The SAME item twice on one document is one question, not two.
                // QUT-EQ-HDEC-068 quotes Vacuum Truck on both its lines, and
                // asking about it twice would also write the answer twice.
                if (seen.has(key)) return;
                seen.add(key);
                const d = decisionFrom(result, row);
                d.dir = want;
                d.rowReady = false;
                d.reviewKey = key;
                // Pre-fill the unit from the catalogue's own vocabulary, so the
                // only thing usually left to type is the name.
                applySuggestedUnit(d, units, want);
                decisions.push(d);
            });
            if (!decisions.length) return null;

            const reviewed = opts.force ? [] : await reviewedCatalogueKeys(docNo, docType);
            const pending = unreviewedCatalogueDecisions(decisions, reviewed, opts.force);
            if (!pending.length) return null;

            const ok = await openReview(docNo, pending, "catalogue", want);
            await markCatalogueReviewed(docNo, pending, ok, docType);
            if (!ok) return { skipped: pending.length };
            const report = await writeBack(pending);
            const saved = report.updated + report.created;
            if (saved) {
                toast(`${saved} item${saved === 1 ? "" : "s"} completed in Master Data`);
            }
            if (report.failed) {
                toast(`${report.failed} item${report.failed === 1 ? "" : "s"} could not be saved`
                      + `, they are still on the ${spec.noun || "order"}`, true);
            }
            return report;
        } catch (e) {
            // The document is saved. A failure here is not the user's problem.
            if (window.console) console.warn("new-item review failed", e);
            return null;
        }
    }

    /* A purchase order: Arabic on the page, English wanted. */
    async function poReviewNewItems(poNo, opts) {
        return catalogueReview(poNo, {
            want: "en", docType: "po", opts, noun: "order",
            load: async () => {
                const rec = await api().load_po(poNo);
                return ((rec && rec.rows) || []).map(r => ({
                    name: String((r && r.name) || "").trim(),
                    description: (r && r.description) || "",
                    unit: (r && r.unit) || "",
                }));
            },
        });
    }

    /* A quotation: English on the page, Arabic wanted.

       Reading a quotation row is the one genuinely new part. There are four
       quote types and each names its own columns - materials `item` +
       `description`, equipment `equipment` + `specs`, manpower `role`, service
       `description` (the name IS the description) - plus whatever columns the
       user has added. So the NAME comes from the type's `primary_key`, which
       is the same key `api.save_quote` already remembers items under, and the
       description is taken only from a column actually called `description` or
       `specs`. Guessing at "the second text column" would put `remarks` in a
       service quotation's description and change what a record means. */
    const QUOTE_DESCRIPTION_KEYS = ["description", "specs"];

    function quoteRowFields(row, schema) {
        const primary = (schema && schema.primary_key) || "item";
        const name = String((row && row[primary]) || "").trim();
        let description = "";
        for (const key of QUOTE_DESCRIPTION_KEYS) {
            if (key === primary) continue;
            if (hasValue(row && row[key])) { description = String(row[key]); break; }
        }
        return { name, description, unit: String((row && row.unit) || "").trim() };
    }

    async function quoteReviewNewItems(quoteNo, opts) {
        return catalogueReview(quoteNo, {
            want: "ar", docType: "qut", opts, noun: "quotation",
            load: async () => {
                const rec = await api().load_quote(quoteNo);
                const rows = (rec && rec.rows) || [];
                if (!rows.length) return [];
                let types = {};
                try { types = (await api().get_quote_types()) || {}; } catch (e) { types = {}; }
                const schema = types[(rec && rec.quote_type) || ""] || {};
                return rows.map(r => quoteRowFields(r, schema));
            },
        });
    }

    window.__poHandoffInternals = {
        isArabic,
        billableGroups,
        readinessFor,
        englishSuggestions,
        buildMasterPatch,
        writeTargetId,
        buildHandoffRow,
        decisionFrom,
        decisionFill,
        reviewRowFromDecision,
        applyReviewResults,
        writeBack,
        linkPoAfterSave,
        recordNeedsEnglish,
        handoffReviewKey,
        lineDetails,
        applySuggestedUnit,
        needsVariantChoice,
        reviewValidation,
        catalogueReviewKey,
        unreviewedCatalogueDecisions,
        recordNeedsLanguage,
        direction,
        targetSuggestions,
        quoteRowFields,
    };
    window.poReviewNewItems = poReviewNewItems;
    window.quoteReviewNewItems = quoteReviewNewItems;
})();
