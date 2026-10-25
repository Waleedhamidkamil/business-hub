/* Attaching a delivery note or invoice to the LPO it fulfils (2026-09-11).

   `dns.lpo_no` and `invoices.lpo_no` were free text, and it showed: 63 of the 64
   delivery notes in the live Abraj database say "N/A", one says CSSP-ADM-0010,
   and two invoices carry "20260905 SHIP WATER", which is not an LPO number at
   all. Everything the LPO screen reports - delivered, invoiced, closed, what is
   still owed - hangs off that string matching, so nothing ever lit up.

   The number is still what PRINTS on the document. What changes is that picking
   one is the easy path, and what you pick is a number the client really sent.
   Typing stays possible: an odd case must never be un-fileable.

   No DOM here beyond the field it writes into - `dn.js` and `inv.js` own their
   own buttons, and the list logic is testable on its own. */
(function () {
    'use strict';

    // 63 of 64 delivery notes carry this honestly. It is an answer, not a gap.
    const NO_LPO = 'N/A';
    const api = () => window.pywebview.api;

    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    const KIND_LABEL = {
        materials: 'Materials',
        rental: 'Rental',
        open: 'Supply contract',
    };
    const STATUS_LABEL = {
        received: 'Received',
        acknowledged: 'Acknowledged',
        delivered: 'Delivered',
        invoiced: 'Invoiced',
        closed: 'Closed',
    };

    function isNoLpo(value) {
        const v = String(value == null ? '' : value).trim().toLowerCase();
        return v === '' || v === 'n/a';
    }

    function money(value, currency) {
        const cur = String(currency || 'USD').toUpperCase();
        if (cur === 'IQD') return 'IQD ' + Math.round(Number(value) || 0).toLocaleString('en-US');
        return '$' + (Number(value) || 0).toLocaleString('en-US',
            { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    /* One readable line per choice. Every part is a WORD - the kind, the status
       and the value - because a row that leans on colour tells Waleed nothing. */
    function describe(row) {
        if (!row) return '';
        const kind = KIND_LABEL[(row.kind || 'materials').toLowerCase()] || 'Materials';
        const status = STATUS_LABEL[row.status] || 'Received';
        const parts = [kind, status];
        // An open contract's total is not a value: CSSP-OPEN-0001 prints 0.77,
        // the price of a litre of diesel. Showing 0.00 would read as "free".
        if ((row.kind || '') !== 'open' && Number(row.total) > 0) {
            parts.push(money(row.total, row.currency));
        }
        if (row.lpo_date) parts.push(String(row.lpo_date));
        return parts.join(' · ');
    }

    /* Which LPOs this client can still take documents against.

       A closed one is finished and is left out. `current` is always kept even
       when closed: reopening a saved delivery note must never quietly drop the
       LPO it was filed against. */
    async function choicesFor(clientName, current) {
        const client = String(clientName || '').trim();
        // A blank client must not offer every client's purchase orders.
        if (!client) return [];
        let rows = [];
        try {
            rows = (await api().list_lpos(client)) || [];
        } catch (error) {
            return [];               // the editor still works without a picker
        }
        const keep = String(current || '').trim().toLowerCase();
        return rows.filter(r =>
            String(r.lpo_no || '').trim().toLowerCase() === keep
            || (r.status || '') !== 'closed');
    }

    /* The dialog. Writes the chosen number into `fieldId` and returns it, or
       null if nothing was chosen. */
    async function pick(fieldId, clientName) {
        const field = document.getElementById(fieldId);
        if (!field || !window.Popup) return null;
        const current = field.value || '';
        const rows = await choicesFor(clientName, current);

        const chosen = String(current).trim().toLowerCase();
        const rowHtml = rows.map(r => `
            <button type="button" class="lpo-pick-row${
                String(r.lpo_no).toLowerCase() === chosen ? ' is-on' : ''}"
                    data-no="${esc(r.lpo_no)}">
                <span class="lpo-pick-no">${esc(r.lpo_no)}</span>
                <span class="lpo-pick-meta">${esc(describe(r))}</span>
            </button>`).join('');

        const body = `<div class="lpo-pick">
            <button type="button" class="lpo-pick-row lpo-pick-none${
                isNoLpo(current) ? ' is-on' : ''}" data-no="${NO_LPO}">
                <span class="lpo-pick-no">No LPO</span>
                <span class="lpo-pick-meta">This work was not against a purchase order.</span>
            </button>
            ${rows.length ? rowHtml : `<p class="lpo-pick-empty">${
                clientName
                    ? esc(clientName) + ' has no open LPOs on file. File one on the Client LPOs screen first.'
                    : 'Choose a client first, then their LPOs appear here.'}</p>`}
        </div>`;

        let picked = null;
        const handle = window.Popup.open({
            title: 'Which LPO is this against?',
            subtitle: clientName || '',
            html: true, body, size: 'md', className: 'lpo-pick-popup',
            actions: [{ label: 'Cancel', value: null, cls: 'ghost' }],
            onOpen: (h) => {
                h.body.querySelectorAll('.lpo-pick-row').forEach((el) => {
                    el.onclick = () => { picked = el.dataset.no; h.close('pick'); };
                });
            },
        });
        const result = await handle.result;
        if (result !== 'pick' || picked == null) return null;
        field.value = picked;
        // The editors listen for `change` to mark the document dirty.
        field.dispatchEvent(new Event('change', { bubbles: true }));
        return picked;
    }

    window.LpoPicker = { NO_LPO, isNoLpo, choicesFor, describe, pick };
})();
