/*
   One compact item-review component for every workflow that completes a
   material before it is saved or printed. Popup owns the modal shell. This
   class owns only item rows, field visibility, candidate choice, AI proposals,
   validation handoff and normalized results.
*/
(function () {
    "use strict";

    function esc(value) {
        return String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[char]));
    }

    function present(value) {
        return value !== undefined && value !== null && String(value).trim() !== "";
    }

    function language(value, fallback) {
        const code = String(value || fallback || "en").toLowerCase();
        return code === "ar" ? "ar" : "en";
    }

    function direction(lang) { return lang === "ar" ? "rtl" : "ltr"; }

    function normalizeOptions(options) {
        const raw = options || {};
        const targetLanguage = language(raw.targetLanguage, "en");
        const sourceLanguage = language(raw.sourceLanguage, targetLanguage === "en" ? "ar" : "en");
        return {
            title: String(raw.title || "Review items"),
            subtitle: String(raw.subtitle || ""),
            primaryLabel: String(raw.primaryLabel || "Continue"),
            sourceLabel: String(raw.sourceLabel || "On the order"),
            targetLabel: String(raw.targetLabel || (targetLanguage === "ar" ? "Arabic details" : "English details")),
            sourceLanguage,
            targetLanguage,
            readyCount: Number(raw.readyCount) || 0,
            rows: Array.isArray(raw.rows) ? raw.rows : [],
            allowMoney: !!raw.allowMoney,
            allowDocumentOnly: !!raw.allowDocumentOnly,
            suggestLabel: String(raw.suggestLabel || "Complete Missing Details with AI"),
            suggest: typeof raw.suggest === "function" ? raw.suggest : null,
            suggestAvailable: typeof raw.suggestAvailable === "function" ? raw.suggestAvailable : null,
            validate: typeof raw.validate === "function" ? raw.validate : null,
            toast: typeof raw.toast === "function" ? raw.toast : null,
            className: String(raw.className || ""),
        };
    }

    function normalizeRow(row, index) {
        const raw = row || {};
        return {
            ...raw,
            id: raw.id == null ? index : raw.id,
            sourceName: String(raw.sourceName || ""),
            sourceDescription: String(raw.sourceDescription || ""),
            sourceUnit: String(raw.sourceUnit || ""),
            targetName: String(raw.targetName || ""),
            targetDescription: String(raw.targetDescription || ""),
            targetUnit: String(raw.targetUnit || ""),
            rate: present(raw.rate) ? raw.rate : "",
            rateCurrency: String(raw.rateCurrency || "USD").toUpperCase() === "IQD" ? "IQD" : "USD",
            cost: present(raw.cost) ? raw.cost : "",
            costCurrency: String(raw.costCurrency || "USD").toUpperCase() === "IQD" ? "IQD" : "USD",
            missing: Array.isArray(raw.missing) ? raw.missing.slice() : [],
            candidates: Array.isArray(raw.candidates) ? raw.candidates.filter(Boolean) : [],
            matched: !!raw.matched,
            ambiguous: !!raw.ambiguous,
        };
    }

    function fieldKey(kind, targetLanguage) {
        if (kind === "name") return `name_${targetLanguage}`;
        if (kind === "description") return `description_${targetLanguage}`;
        if (kind === "unit") return `unit_${targetLanguage}`;
        return kind;
    }

    function moneyControl(index, kind, value, currency) {
        const selected = String(currency || "USD").toUpperCase() === "IQD" ? "IQD" : "USD";
        return `<span class="irp-money">
          <input type="text" inputmode="decimal" dir="ltr" data-irp-field="${index}:${kind}"
                 value="${esc(value)}" aria-label="${kind === "rate" ? "Rate" : "Cost"}">
          <select data-irp-currency="${index}:${kind}" aria-label="${kind} currency">
            <option value="USD"${selected === "USD" ? " selected" : ""}>$</option>
            <option value="IQD"${selected === "IQD" ? " selected" : ""}>IQD</option>
          </select>
        </span>`;
    }

    /* Set one money pair to what a chosen record holds, or clear it when that
       record holds none. Skipped entirely for a box the person typed in. */
    function seedMoney(root, index, kind, value, currency, edited) {
        if (edited && edited.has(`${index}:${kind}`)) return;
        const input = root.querySelector(`[data-irp-field="${index}:${kind}"]`);
        if (input) input.value = present(value) ? String(value) : "";
        const select = root.querySelector(`[data-irp-currency="${index}:${kind}"]`);
        if (select && present(value)) {
            select.value = String(currency || "USD").toUpperCase() === "IQD" ? "IQD" : "USD";
        }
    }

    function candidateHtml(candidate, index, candidateIndex) {
        const details = [candidate.description, candidate.unit].filter(present).join(" · ");
        return `<button type="button" class="irp-candidate" data-irp-candidate="${index}"
                       data-irp-candidate-index="${candidateIndex}">
          <strong>${esc(candidate.name || "Unnamed item")}</strong>
          ${details ? `<small>${esc(details)}</small>` : ""}
        </button>`;
    }

    function rowHtml(rawRow, index, rawOptions) {
        const options = normalizeOptions(rawOptions);
        const row = normalizeRow(rawRow, index);
        const targetDir = direction(options.targetLanguage);
        const sourceDir = direction(options.sourceLanguage);
        const missing = new Set(row.missing);
        const nameKey = fieldKey("name", options.targetLanguage);
        const descriptionKey = fieldKey("description", options.targetLanguage);
        const unitKey = fieldKey("unit", options.targetLanguage);
        const context = [row.sourceDescription, row.sourceUnit].filter(present).join(" · ");
        const name = missing.has("name")
            ? `<input class="irp-input irp-name" type="text" dir="${targetDir}"
                      data-irp-field="${index}:${nameKey}"
                      value="${esc(row.targetName)}" placeholder="${options.targetLanguage === "ar" ? "Arabic name" : "English name"}">`
            : `<span class="irp-confirmed" dir="${targetDir}"><strong>${esc(row.targetName)}</strong><small>Matched in Master Data</small></span>`;
        const unit = missing.has("unit")
            ? `<input class="irp-input irp-unit" type="text" dir="${targetDir}"
                      data-irp-field="${index}:${unitKey}" value="${esc(row.targetUnit)}"
                      placeholder="Unit">`
            : (present(row.targetUnit) ? `<span class="irp-unit-known" dir="${targetDir}">${esc(row.targetUnit)}</span>` : "");
        const rate = options.allowMoney && missing.has("rate")
            ? `<label class="irp-required"><span>Rate</span>${moneyControl(index, "rate", row.rate, row.rateCurrency)}</label>`
            : "";
        const candidates = row.candidates.length
            ? `<div class="irp-candidates"><span>Possible Master Data matches</span>
                 ${row.candidates.map((candidate, ci) => candidateHtml(candidate, index, ci)).join("")}
               </div>` : "";
        const description = `<label class="irp-detail-field"><span>Description <small>optional</small></span>
            <input class="irp-input" type="text" dir="${targetDir}"
                   data-irp-field="${index}:${descriptionKey}"
                   value="${esc(row.targetDescription)}"></label>`;
        const cost = options.allowMoney
            ? `<label class="irp-detail-field irp-private"><span>Cost <small>private</small></span>
                 ${moneyControl(index, "cost", row.cost, row.costCurrency)}</label>` : "";
        const documentOnly = options.allowDocumentOnly
            ? `<label class="irp-document-only"><input type="checkbox" data-irp-document-only="${index}">
                 <span>Use entered values on this document only</span></label>` : "";

        return `<tr class="irp-row" data-irp-row="${index}">
          <td class="irp-source"><strong dir="${sourceDir}">${esc(row.sourceName)}</strong>
            ${context ? `<small dir="${sourceDir}">${esc(context)}</small>` : ""}</td>
          <td class="irp-target">${name}</td>
          <td class="irp-unit-cell">${unit}</td>
          <td class="irp-required-cell">${rate}</td>
          <td class="irp-tools">
            <button type="button" class="irp-more" data-irp-more="${index}" aria-expanded="false">Details</button>
            <label class="irp-skip"><input type="checkbox" data-irp-skip="${index}"><span>Skip</span></label>
          </td>
        </tr>
        <tr class="irp-extra" data-irp-extra="${index}" hidden><td colspan="5">
          <div class="irp-detail-grid">${description}${cost}</div>
          ${candidates}${documentOnly}
        </td></tr>`;
    }

    function tableHtml(rows, rawOptions) {
        const options = normalizeOptions(rawOptions);
        const normalized = (rows || []).map(normalizeRow);
        const hasRate = options.allowMoney && normalized.some(row => row.missing.includes("rate"));
        return `<div class="irp-review" dir="ltr">
          ${options.readyCount ? `<div class="irp-summary"><strong>${options.readyCount}</strong> ready from Master Data</div>` : ""}
          <table class="irp-table">
            <thead><tr><th>${esc(options.sourceLabel)}</th><th>${esc(options.targetLabel)}</th>
              <th>Unit</th><th>${hasRate ? "Required" : ""}</th><th></th></tr></thead>
            <tbody>${normalized.map((row, index) => rowHtml(row, index, options)).join("")}</tbody>
          </table>
        </div>`;
    }

    function needsSuggestion(rows) {
        return (rows || []).some(row => {
            const missing = Array.isArray(row && row.missing) ? row.missing : [];
            return missing.includes("name") || missing.includes("description") || !!(row && row.ambiguous);
        });
    }

    class ItemReviewPopup {
        constructor(options) {
            this.options = normalizeOptions(options);
            this.rows = this.options.rows.map(normalizeRow);
            this.selected = new Map();
            this.edited = new Set();
            this.dirty = false;
            this.handle = null;
        }

        static rowHtml(row, index, options) { return rowHtml(row, index, options); }
        static tableHtml(rows, options) { return tableHtml(rows, options); }
        static needsSuggestion(rows) { return needsSuggestion(rows); }

        _field(root, index, key) {
            const element = root.querySelector(`[data-irp-field="${index}:${key}"]`);
            return element ? String(element.value || "").trim() : "";
        }

        _currency(root, index, key, fallback) {
            const element = root.querySelector(`[data-irp-currency="${index}:${key}"]`);
            return String((element && element.value) || fallback || "USD").toUpperCase();
        }

        _selectCandidate(root, index, candidateIndex, fromAI) {
            const picked = this.rows[index] && this.rows[index].candidates[candidateIndex];
            if (!picked) return false;
            this.selected.set(index, candidateIndex);
            root.querySelectorAll(`[data-irp-candidate="${index}"]`).forEach(button => {
                button.classList.toggle("is-selected",
                    Number(button.dataset && button.dataset.irpCandidateIndex) === candidateIndex);
            });
            const target = this.options.targetLanguage;
            /* Every visible value now belongs to the record just chosen.

               The row opened pre-filled from whatever the resolver matched
               automatically - a DIFFERENT record. Leaving its money and its
               description sitting in the boxes made `read()` report them as
               typed, so one product's private cost was put on the document and
               written into the other product's master record. So a manual pick
               re-seeds the row: the chosen record's values where it has them,
               EMPTY where it does not - except a box the person typed in
               themselves, which is theirs and is never overwritten. */
            const fill = picked.fill || {};
            const seed = (key, value, keep) => {
                const field = root.querySelector(`[data-irp-field="${index}:${key}"]`);
                if (!field) return;
                if (this.edited.has(`${index}:${key}`)) return;
                if (fromAI) {
                    // AI fills gaps; it never clears and never overwrites.
                    if (!present(value) || present(field.value)) return;
                    field.value = value;
                    field.classList.add("is-ai-suggested");
                    return;
                }
                if (!present(value) && keep) return;
                field.value = present(value) ? value : "";
                field.classList.remove("is-ai-suggested");
            };
            seed(`name_${target}`, picked.name, true);
            seed(`description_${target}`, picked.description, false);
            seed(`unit_${target}`, picked.unit, false);
            if (!fromAI) {
                // Money moves with its currency or not at all.
                seedMoney(root, index, "rate", fill.rate, fill.rate_ccy, this.edited);
                seedMoney(root, index, "cost", fill.cost, fill.cost_ccy, this.edited);
            }
            if (fromAI) {
                const row = root.querySelector(`[data-irp-row="${index}"]`);
                if (row) row.classList.add("has-ai-suggestion");
            }
            return true;
        }

        read(root) {
            const target = this.options.targetLanguage;
            return this.rows.map((row, index) => {
                const skip = root.querySelector(`[data-irp-skip="${index}"]`);
                const documentOnly = root.querySelector(`[data-irp-document-only="${index}"]`);
                const candidateIndex = this.selected.has(index) ? this.selected.get(index) : null;
                const candidate = candidateIndex == null ? null : (row.candidates[candidateIndex] || null);
                const typed = {};
                const put = (key, value) => { if (present(value)) typed[key] = value; };
                put(`name_${target}`, this._field(root, index, `name_${target}`));
                put(`description_${target}`, this._field(root, index, `description_${target}`));
                put(`unit_${target}`, this._field(root, index, `unit_${target}`));
                const rate = this._field(root, index, "rate");
                if (present(rate)) {
                    typed.rate = rate;
                    typed.rate_ccy = this._currency(root, index, "rate", row.rateCurrency);
                }
                const cost = this._field(root, index, "cost");
                if (present(cost)) {
                    typed.cost = cost;
                    typed.cost_ccy = this._currency(root, index, "cost", row.costCurrency);
                }
                const mode = skip && skip.checked ? "exclude"
                    : documentOnly && documentOnly.checked ? "asis"
                    : candidate ? "candidate" : "manual";
                return { id: row.id, mode, candidate, typed };
            });
        }

        _bind(root) {
            root.addEventListener("click", event => {
                const more = event.target.closest("[data-irp-more]");
                if (more) {
                    const index = more.dataset.irpMore;
                    const extra = root.querySelector(`[data-irp-extra="${index}"]`);
                    if (extra) {
                        extra.hidden = !extra.hidden;
                        more.setAttribute("aria-expanded", extra.hidden ? "false" : "true");
                    }
                    return;
                }
                const candidate = event.target.closest("[data-irp-candidate]");
                if (!candidate) return;
                const index = Number(candidate.dataset.irpCandidate);
                const candidateIndex = Number(candidate.dataset.irpCandidateIndex);
                this.dirty = true;
                this._selectCandidate(root, index, candidateIndex, false);
            });
            root.addEventListener("input", event => {
                const field = event.target.closest("[data-irp-field]");
                if (!field) return;
                // Typed by a person: theirs from now on. Nothing may overwrite
                // it, and losing it needs a question first.
                this.edited.add(String(field.dataset.irpField));
                this.dirty = true;
                field.classList.remove("is-ai-suggested");
                const index = Number(String(field.dataset.irpField).split(":")[0]);
                if (String(field.dataset.irpField).includes(":name_")) {
                    this.selected.delete(index);
                    root.querySelectorAll(`[data-irp-candidate="${index}"]`)
                        .forEach(button => button.classList.remove("is-selected"));
                }
                const rowElement = root.querySelector(`[data-irp-row="${index}"]`);
                if (rowElement && !root.querySelector(`[data-irp-row="${index}"] .is-ai-suggested`)) {
                    rowElement.classList.remove("has-ai-suggestion");
                }
            });
            root.addEventListener("change", event => {
                if (event.target.closest("[data-irp-skip],[data-irp-document-only],"
                        + "[data-irp-currency]")) {
                    this.dirty = true;
                }
                const skip = event.target.closest("[data-irp-skip]");
                if (!skip) return;
                const index = skip.dataset.irpSkip;
                const row = root.querySelector(`[data-irp-row="${index}"]`);
                const extra = root.querySelector(`[data-irp-extra="${index}"]`);
                if (row) row.classList.toggle("is-skipped", skip.checked);
                if (extra) extra.classList.toggle("is-skipped", skip.checked);
            });
        }

        _pendingRequests(root, requiredOnly) {
            const target = this.options.targetLanguage;
            const requests = [];
            this.rows.forEach((row, rowIndex) => {
                const skip = root.querySelector(`[data-irp-skip="${rowIndex}"]`);
                const documentOnly = root.querySelector(
                    `[data-irp-document-only="${rowIndex}"]`);
                if ((skip && skip.checked) || (documentOnly && documentOnly.checked)) return;
                const name = root.querySelector(`[data-irp-field="${rowIndex}:name_${target}"]`);
                const description = root.querySelector(
                    `[data-irp-field="${rowIndex}:description_${target}"]`);
                const needsName = !!name && !present(name.value);
                const needsDescription = !requiredOnly && !!description
                    && !present(description.value) && present(row.sourceDescription);
                const needsChoice = !requiredOnly && row.ambiguous && !this.selected.has(rowIndex);
                if (!needsName && !needsDescription && !needsChoice) return;
                requests.push({
                    rowIndex,
                    name: row.sourceName,
                    description: row.sourceDescription,
                    unit: row.sourceUnit,
                    candidates: row.candidates.map(candidate => ({
                        name: candidate.name || "",
                        description: candidate.description || "",
                        unit: candidate.unit || "",
                    })),
                });
            });
            return requests;
        }

        _applySuggestions(root, requests, suggestions) {
            const completedRows = new Set();
            const target = this.options.targetLanguage;
            (suggestions || []).forEach(suggestion => {
                const request = requests[Number(suggestion.index)];
                if (!request) return;
                const candidateIndex = suggestion.candidate_index;
                if (Number.isInteger(candidateIndex)
                        && this.rows[request.rowIndex]
                        && this.rows[request.rowIndex].candidates[candidateIndex]) {
                    this._selectCandidate(root, request.rowIndex, candidateIndex, true);
                    completedRows.add(request.rowIndex);
                }
                const mappings = [
                    [`name_${target}`, suggestion[`name_${target}`]],
                    [`description_${target}`, suggestion[`description_${target}`]],
                    [`unit_${target}`, suggestion[`unit_${target}`]],
                ];
                mappings.forEach(([key, value]) => {
                    if (!present(value)) return;
                    const field = root.querySelector(
                        `[data-irp-field="${request.rowIndex}:${key}"]`);
                    if (!field || present(field.value)) return;
                    field.value = value;
                    field.classList.add("is-ai-suggested");
                    completedRows.add(request.rowIndex);
                    const row = root.querySelector(`[data-irp-row="${request.rowIndex}"]`);
                    if (row) row.classList.add("has-ai-suggestion");
                });
            });
            return completedRows;
        }

        /* Rows still waiting on a person, AFTER the AI has been round.

           Skip and "use on this document only" are answers, not gaps. Counting
           them told the user that rows they had just excluded on purpose
           "still need review". */
        _requiredGapCount(root) {
            const target = this.options.targetLanguage;
            let count = 0;
            this.rows.forEach((row, index) => {
                const skip = root.querySelector(`[data-irp-skip="${index}"]`);
                const documentOnly = root.querySelector(`[data-irp-document-only="${index}"]`);
                if ((skip && skip.checked) || (documentOnly && documentOnly.checked)) return;
                const name = root.querySelector(`[data-irp-field="${index}:name_${target}"]`);
                if ((name && !present(name.value)) || (row.ambiguous && !this.selected.has(index))) {
                    count++;
                }
            });
            return count;
        }

        _clearValidation(root) {
            root.querySelectorAll('[aria-invalid="true"]').forEach(element => {
                element.removeAttribute("aria-invalid");
            });
            root.querySelectorAll(".has-errors").forEach(element => {
                element.classList.remove("has-errors");
            });
            root.querySelectorAll("[data-irp-error]").forEach(element => element.remove());
        }

        _applyValidation(root, validation) {
            this._clearValidation(root);
            const errors = Array.isArray(validation && validation.errors)
                ? validation.errors : [];
            const labels = {
                name: this.options.targetLanguage === "ar"
                    ? "Arabic name required" : "English name required",
                unit: this.options.targetLanguage === "ar"
                    ? "Arabic unit required" : "English unit required",
                rate: "Rate required",
                choice: "Select a Master Data variant or use document-only values",
            };
            let first = null;
            errors.forEach(error => {
                const rowIndex = this.rows.findIndex(row => String(row.id) === String(error.id));
                if (rowIndex < 0) return;
                const row = root.querySelector(`[data-irp-row="${rowIndex}"]`);
                if (row) row.classList.add("has-errors");
                (error.fields || []).forEach(kind => {
                    let field = root.querySelector(`[data-irp-field="${rowIndex}:${kind}"]`)
                        || root.querySelector(
                            `[data-irp-field="${rowIndex}:${kind}_${this.options.targetLanguage}"]`);
                    if (kind === "choice") {
                        const extra = root.querySelector(`[data-irp-extra="${rowIndex}"]`);
                        const more = root.querySelector(`[data-irp-more="${rowIndex}"]`);
                        if (extra) extra.hidden = false;
                        if (more) more.setAttribute("aria-expanded", "true");
                        field = root.querySelector(
                            `[data-irp-extra="${rowIndex}"] .irp-candidates`);
                    }
                    if (!field) return;
                    field.setAttribute("aria-invalid", "true");
                    field.classList.add("has-errors");
                    field.insertAdjacentHTML("afterend",
                        `<span class="irp-field-error" data-irp-error>${esc(labels[kind] || "Required")}</span>`);
                    if (!first) {
                        first = field.matches("input,button,select")
                            ? field : field.querySelector("button,input,select");
                    }
                });
            });
            if (first) {
                if (typeof first.scrollIntoView === "function") {
                    first.scrollIntoView({ block: "center", behavior: "smooth" });
                }
                if (typeof first.focus === "function") first.focus({ preventScroll: true });
            }
        }

        /* Leaving without saving.

           This popup can hold a quarter of an hour of typing across forty
           rows, and Esc or a stray click on the dim background used to throw
           all of it away without a word - while the caller recorded those rows
           as reviewed, so nothing ever offered them again. Now every exit that
           discards work asks once, and only once there is work to discard. */
        async _confirmDiscard() {
            if (!this.dirty) return true;
            const ask = window.showModal;
            if (typeof ask !== "function") return true;
            const choice = await ask({
                title: "Discard what you have entered?",
                body: "The details you typed here have not been saved yet.",
                buttons: [
                    { label: "Keep editing", value: "keep", cls: "ghost" },
                    { label: "Discard", value: "discard", cls: "primary" },
                ],
            });
            return choice === "discard";
        }

        async _suggest(root, button) {
            const requests = this._pendingRequests(root, false);
            if (!requests.length) {
                if (this.options.toast) this.options.toast("Nothing left for AI to suggest");
                return;
            }
            const oldLabel = button.textContent;
            button.disabled = true;
            /* SAY THAT IT IS STILL WORKING (2026-09-10).

               This service is slow and erratic - six real calls measured 11.7s,
               15.3s, 15.5s, 17.0s, 19.0s and 47.7s, the last of those being a
               timeout plus a successful retry. A button reading "Completing..."
               for three quarters of a minute is indistinguishable from a broken
               one, which is exactly what it was reported as. The counter costs
               nothing and answers the only question the user has. */
            let seconds = 0;
            const tick = () => {
                seconds += 1;
                button.textContent = seconds < 4 ? "Completing..."
                    : `Completing... ${seconds}s`;
            };
            button.textContent = "Completing...";
            const timer = (typeof setInterval === "function")
                ? setInterval(tick, 1000) : null;
            const stopTimer = () => {
                if (timer && typeof clearInterval === "function") clearInterval(timer);
            };
            /* The primary goes with it. It used to stay live while the request
               was out, so "Create invoice" mid-flight read a half-filled form
               and the suggestions landed on a popup that had already gone. */
            const primary = root.querySelector("[data-value='ok']");
            if (primary) primary.disabled = true;
            try {
                this._clearValidation(root);
                const result = await this.options.suggest(requests);
                if (!result || result.ok === false) throw Error((result && result.error) || "No suggestions came back");
                // A long list is sent in several requests. If a later one
                // failed, what did land is still here and the user must know
                // the rest is not.
                let partial = (result && result.warning) || "";
                const completed = this._applySuggestions(root, requests, result.suggestions);
                const repair = this._pendingRequests(root, true);
                if (repair.length) {
                    seconds = 0;
                    button.textContent = "Finishing...";
                    const repaired = await this.options.suggest(repair);
                    if (!repaired || repaired.ok === false) {
                        throw Error((repaired && repaired.error) || "AI could not finish the missing names");
                    }
                    this._applySuggestions(root, repair, repaired.suggestions)
                        .forEach(index => completed.add(index));
                    partial = partial || (repaired.warning || "");
                }
                const remaining = this._requiredGapCount(root);
                if (partial && this.options.toast) {
                    this.options.toast(partial, true);
                } else if (this.options.toast) {
                    if (remaining) {
                        this.options.toast(`${completed.size} item${completed.size === 1 ? "" : "s"} completed, `
                            + `${remaining} still need${remaining === 1 ? "s" : ""} review.`, true);
                    } else {
                        this.options.toast(`${completed.size} item${completed.size === 1 ? "" : "s"} completed. `
                            + "Check the suggestions before continuing.");
                    }
                }
            } catch (error) {
                if (this.options.toast) this.options.toast(error.message || "AI suggestion failed", true);
            } finally {
                stopTimer();
                button.disabled = false;
                button.textContent = oldLabel;
                if (primary) primary.disabled = false;
            }
        }

        async open() {
            if (!window.Popup || typeof window.Popup.open !== "function") {
                throw Error("The shared popup system is not available");
            }
            const canSuggest = !!this.options.suggest && needsSuggestion(this.rows);
            // `keep` on Cancel so the discard question can run first; the
            // popup itself is NOT dismissible, so Esc and the dim background
            // route through the same question instead of dropping the work.
            const actions = [
                { label: "Cancel", value: "cancel", cls: "ghost", keep: true },
            ];
            if (canSuggest) {
                actions.push({ label: this.options.suggestLabel, value: "suggest", cls: "secondary", keep: true });
            }
            actions.push({ label: this.options.primaryLabel, value: "ok", cls: "primary", keep: true });
            const handle = window.Popup.open({
                title: this.options.title,
                subtitle: this.options.subtitle,
                size: "xl",
                dir: "ltr",
                dismissible: false,
                className: `irp-modal${this.options.className ? ` ${this.options.className}` : ""}`,
                html: true,
                body: tableHtml(this.rows, this.options),
                actions,
            });
            this.handle = handle;
            const root = handle.panel;
            this._bind(root);
            const suggestButton = root.querySelector("[data-value='suggest']");
            if (suggestButton) {
                suggestButton.onclick = () => this._suggest(root, suggestButton);
                if (this.options.suggestAvailable) {
                    suggestButton.hidden = true;
                    // Availability is cosmetic, so it must never delay the
                    // primary action or make the popup feel unresponsive.
                    Promise.resolve(this.options.suggestAvailable()).then(available => {
                        if (available && !handle.closed) suggestButton.hidden = false;
                    }).catch(() => { /* unavailable stays hidden */ });
                }
            }
            return new Promise(resolve => {
                const drop = () => {
                    if (document.removeEventListener) {
                        document.removeEventListener("keydown", onEscape);
                    }
                };
                const leave = async () => {
                    if (!(await this._confirmDiscard())) return;
                    drop();
                    handle.close("cancel");
                    resolve({ ok: false, rows: [] });
                };
                const cancel = root.querySelector("[data-value='cancel']");
                if (cancel) cancel.onclick = leave;
                /* Esc reaches here rather than Popup's own handler: an
                   undismissible popup ignores it, so this is the only listener
                   that acts on it. Removed with the popup. */
                const onEscape = (event) => {
                    if (event.key !== "Escape") return;
                    if (handle.closed) {
                        document.removeEventListener("keydown", onEscape);
                        return;
                    }
                    if (!root.contains(document.activeElement)
                            && document.activeElement !== document.body) return;
                    event.preventDefault();
                    leave();
                };
                if (document.addEventListener) document.addEventListener("keydown", onEscape);
                const primary = root.querySelector("[data-value='ok']");
                if (primary) primary.onclick = async () => {
                    this._clearValidation(root);
                    const rows = this.read(root);
                    const problem = this.options.validate ? await this.options.validate(rows) : "";
                    const validation = typeof problem === "string"
                        ? { message: problem, errors: [] }
                        : (problem || { message: "", errors: [] });
                    if (validation.message || (validation.errors && validation.errors.length)) {
                        this._applyValidation(root, validation);
                        if (this.options.toast) {
                            this.options.toast(validation.message || "Review the highlighted details", true);
                        }
                        return;
                    }
                    drop();
                    handle.close("ok");
                    resolve({ ok: true, rows });
                };
                handle.result.then(value => {
                    drop();
                    if (value !== "ok") resolve({ ok: false, rows: [] });
                });
            });
        }
    }

    window.ItemReviewPopup = ItemReviewPopup;
})();
