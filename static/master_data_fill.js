/*
   Fill editor rows from Master Data (QUT / DN / INV).

   The matcher is deliberately shared with the Excel-paste resolver. This
   module only presents the matches and applies the fields the user checks;
   it never saves a document or changes the Master Data catalog.
*/
(function () {
    "use strict";

    const FIELDS = [
        { key: "item",        label: "Item" },
        { key: "description", label: "Description" },
        { key: "unit",        label: "Unit" },
        { key: "price",       label: "Price" },
        { key: "cost",        label: "Cost" },
    ];

    function escText(value) {
        if (typeof window.esc === "function") return window.esc(value == null ? "" : value);
        return String(value == null ? "" : value).replace(/[&<>"']/g, c => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
    }

    function hasValue(value) {
        return value !== undefined && value !== null && String(value) !== "";
    }

    function fillFor(result, candidate) {
        if (!result) return {};
        const picked = candidate && candidate.fill && Object.keys(candidate.fill).length
            ? candidate.fill : null;
        const direct = result.fill && Object.keys(result.fill).length
            ? result.fill : null;
        const suggested = result.suggested_fill && Object.keys(result.suggested_fill).length
            ? result.suggested_fill : null;
        const source = picked || direct || suggested || {};
        const fill = { ...source };
        const itemName = (candidate && candidate.name) || result.name;
        if (itemName && result.tier !== "new") fill.item = itemName;
        return fill;
    }

    function valueForField(fill, fieldKey) {
        return fill[fieldKey === "price" ? "rate" : fieldKey];
    }

    /* What the checkbox SHOWS. Money carries the unit it is filled in
       (2026-08-26): the cost arrives in the currency Master Data saved it in,
       not converted into the document's, so "2002" alone reads as dollars when
       it means dinars. `$`/`IQD` is a label, never a colour. */
    function previewForField(fill, fieldKey) {
        const value = valueForField(fill, fieldKey);
        if (!hasValue(value)) return value;
        if (fieldKey !== "cost" && fieldKey !== "price") return value;
        const raw = fill[fieldKey === "price" ? "rate_ccy" : "cost_ccy"];
        const ccy = String(raw || "").trim().toUpperCase();
        if (ccy !== "USD" && ccy !== "IQD") return value;
        return ccy === "IQD" ? `${value} IQD` : `$${value}`;
    }

    function availableFields(result) {
        // Always return the same five fields so each row's popup layout stays
        // stable. The renderer disables fields absent from the match.
        return FIELDS.map(f => ({ key: f.key, label: f.label }));
    }

    function applyRowFill(row, primaryKey, result, selected, candidate) {
        if (!row || !result) return false;
        const fill = fillFor(result, candidate);
        const pick = (key) => !!(selected && selected[key]) && hasValue(valueForField(fill, key));
        let changed = false;
        const set = (key, value) => {
            if (row[key] !== value) { row[key] = value; changed = true; }
        };

        if (pick("item")) set(primaryKey || "item", fill.item);
        if (pick("description")) set("description", fill.description);
        if (pick("unit")) set("unit", fill.unit);
        if (pick("price")) {
            set("rate", fill.rate);
            if (hasValue(fill.rate_ccy)) set("rate_ccy", fill.rate_ccy);
        }
        if (pick("cost")) {
            set("cost", fill.cost);
            if (hasValue(fill.cost_ccy)) set("cost_ccy", fill.cost_ccy);
        }
        return changed;
    }

    function fieldInput(rowIndex, field, fill) {
        const available = hasValue(valueForField(fill, field.key));
        return `<label class="mdf-field${available ? "" : " is-disabled"}">
            <input type="checkbox" data-mdf-row="${rowIndex}" data-mdf-field="${field.key}"${available ? " checked" : " disabled"}>
            <span>${escText(field.label)}</span>
            ${available ? `<small>${escText(previewForField(fill, field.key))}</small>` : `<small class="mdf-empty">not available</small>`}
        </label>`;
    }

    function resultRowHtml(result, rowIndex) {
        const suggestions = Array.isArray(result && result.suggestions)
            ? result.suggestions.filter(s => s && s.name) : [];
        const hasChoices = suggestions.length > 1;
        const fill = hasChoices ? {} : fillFor(result, suggestions[0]);
        const tier = String((result && result.tier) || "new").toLowerCase();
        const title = result && result.query ? result.query : "Untitled row";
        const match = hasChoices ? "Choose a match" :
            (result && result.name ? result.name : "No Master Data match");
        const alternatives = (result && result.alternatives || [])
            .map(a => typeof a === "string" ? a : a.name)
            .filter(Boolean);
        const altText = alternatives.length > 1
            ? ` · Alternatives: ${alternatives.slice(0, 4).join(", ")}` : "";
        const choices = hasChoices ? `<fieldset class="mdf-candidates">
            <legend>Choose one matching item</legend>
            ${suggestions.map((candidate, candidateIndex) => {
                const detail = [candidate.description, candidate.unit].filter(Boolean).join(" · ");
                return `<label class="mdf-candidate">
                    <input type="radio" name="mdf-candidate-${rowIndex}" data-mdf-candidate-row="${rowIndex}" data-mdf-candidate="${candidateIndex}">
                    <span><strong>${escText(candidate.name)}</strong>${detail ? `<small>${escText(detail)}</small>` : ""}</span>
                    <em>${escText(candidate.score == null ? "" : candidate.score)}</em>
                </label>`;
            }).join("")}
        </fieldset>` : "";
        return `<section class="mdf-row mdf-tier-${escText(tier)}" data-mdf-result="${rowIndex}">
            <div class="mdf-row-head">
                <div><strong>${escText(title)}</strong><span class="mdf-arrow">→</span><span data-mdf-match="${rowIndex}">${escText(match)}</span></div>
                <span class="mdf-tier">${escText(tier)}${hasChoices ? "" : escText(altText)}</span>
            </div>
            ${choices}
            <div class="mdf-fields" data-mdf-fields="${rowIndex}">${FIELDS.map(f => fieldInput(rowIndex, f, fill)).join("")}</div>
        </section>`;
    }

    function resultRowsHtml(results) {
        return (results || []).map((r, i) => resultRowHtml(r, i)).join("");
    }

    function checkedForResult(root, rowIndex) {
        const selected = {};
        if (!root || !root.querySelectorAll) return selected;
        root.querySelectorAll(`input[data-mdf-row="${rowIndex}"]`).forEach(input => {
            selected[input.dataset.mdfField] = !!input.checked;
        });
        return selected;
    }

    function selectedCandidateFor(root, rowIndex, result) {
        if (!root || typeof root.querySelector !== "function") return null;
        const input = root.querySelector(`input[data-mdf-candidate-row="${rowIndex}"]:checked`);
        if (!input) return null;
        const index = Number(input.dataset.mdfCandidate);
        return (result && result.suggestions && result.suggestions[index]) || null;
    }

    function wireCandidateInputs(root, results) {
        if (!root || typeof root.querySelectorAll !== "function") return;
        root.querySelectorAll("input[data-mdf-candidate-row]").forEach(input => {
            if (!input || typeof input.addEventListener !== "function") return;
            input.addEventListener("change", () => {
                const rowIndex = Number(input.dataset.mdfCandidateRow);
                const candidateIndex = Number(input.dataset.mdfCandidate);
                const result = results[rowIndex];
                const candidate = result && result.suggestions && result.suggestions[candidateIndex];
                if (!result || !candidate) return;
                const fill = fillFor(result, candidate);
                const fields = root.querySelector(`[data-mdf-fields="${rowIndex}"]`);
                if (fields) fields.innerHTML = FIELDS.map(f => fieldInput(rowIndex, f, fill)).join("");
                const match = root.querySelector(`[data-mdf-match="${rowIndex}"]`);
                if (match) match.textContent = candidate.name || "Choose a match";
            });
        });
    }

    function editorRows(opts) {
        const rows = opts.rows || [];
        const primaryKey = opts.primaryKey || "item";
        const indexes = [];
        const names = [];
        const details = [];
        rows.forEach((row, i) => {
            const name = String(row && row[primaryKey] != null ? row[primaryKey] : "").trim();
            if (!name) return;
            indexes.push(i);
            names.push(name);
            // What the row already says beside the name. A description typed
            // here ("20 L") settles a name Master Data holds as several
            // variants; a blank one settles nothing (master_data._line_variant).
            details.push({
                description: String(row.description == null ? "" : row.description),
                unit: String(row.unit == null ? "" : row.unit),
            });
        });
        return { indexes, names, details, primaryKey };
    }

    async function openMasterDataFill(opts) {
        const o = opts || {};
        const picked = editorRows(o);
        if (!picked.names.length) {
            if (window.toast) window.toast("Add an item to the table first", true);
            return { ok: false, reason: "empty" };
        }

        let data;
        try {
            data = await api().im_resolve(
                picked.names,
                o.docType || "",
                o.subType || "",
                o.costCurrency || "USD",
                Number(o.costPeg) || 0,
                false,
                o.sellCurrency || "USD",
                Number(o.sellPeg) || 0,
                picked.details,
            );
        } catch (e) {
            if (window.toast) window.toast("Master Data lookup failed", true);
            return { ok: false, reason: "lookup" };
        }
        if (!data || data.ok === false) {
            if (window.toast) window.toast((data && data.error) || "Master Data lookup failed", true);
            return { ok: false, reason: "lookup" };
        }

        const results = data.results || [];
        if (!results.length) {
            if (window.toast) window.toast("No item rows to check", true);
            return { ok: false, reason: "no-results" };
        }

        const body = `<div class="mdf-shell">
            <p class="mdf-intro">Choose which Master Data details to copy into each matching row. All available details are selected by default.</p>
            <div class="mdf-list">${resultRowsHtml(results)}</div>
        </div>`;
        const pending = showModal({
            title: "Fill from Master Data",
            body,
            html: true,
            buttons: [
                { label: "Cancel", value: "cancel", cls: "ghost" },
                { label: "Fill selected", value: "apply", cls: "primary" },
            ],
        });
        const host = document.querySelector && document.querySelector("#modalBackdrop .modal");
        if (host) host.classList.add("mdf-modal-host");
        const modalRoot = document.getElementById("modalBody");
        wireCandidateInputs(modalRoot, results);
        const choice = await pending;
        if (host) host.classList.remove("mdf-modal-host");
        if (choice !== "apply") return { ok: true, applied: 0 };

        const root = document.getElementById("modalBody");
        let applied = 0;
        results.forEach((result, resultIndex) => {
            const rowIndex = picked.indexes[resultIndex];
            const row = o.rows[rowIndex];
            if (!row) return;
            const candidate = selectedCandidateFor(root, resultIndex, result);
            if ((result.suggestions || []).length > 1 && !candidate) return;
            if (applyRowFill(row, picked.primaryKey, result,
                             checkedForResult(root, resultIndex), candidate)) {
                applied++;
            }
        });
        if (applied) {
            if (typeof o.render === "function") o.render();
            if (typeof o.markDirty === "function") o.markDirty();
            if (window.toast) window.toast(`Filled ${applied} row${applied === 1 ? "" : "s"} from Master Data`);
        } else if (window.toast) {
            window.toast("Nothing selected to fill", true);
        }
        return { ok: true, applied };
    }

    window.openMasterDataFill = openMasterDataFill;

    /* The presentation primitives, shared (2026-09-05).
       `po_handoff.js` renders master-data matches too - the PO → DN/Invoice
       review screen. It must NOT re-implement `previewForField`: a second,
       subtly different money renderer is how "2002" ends up reading as dollars
       when it means dinars. Everything a second screen legitimately needs is
       exported here; `openMasterDataFill` itself is not, because this module's
       contract (see the header) is that it never writes, and that screen does. */
    window.MasterDataFill = {
        FIELDS,
        escText,
        hasValue,
        fillFor,
        valueForField,
        previewForField,
        availableFields,
        applyRowFill,
        resultRowsHtml,
    };
    // The old name, kept so the existing suite and any caller reaching for it
    // keep working. Same object - not a copy.
    window.__masterDataFillInternals = window.MasterDataFill;
})();
