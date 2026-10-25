/* Choosing the quotation a client's LPO answers (2026-09-28).

   "Against quotation" was a free text box with a placeholder reading
   `QUT-MT-HDEC-029`, so citing the right quotation meant remembering its
   number. HDEC alone has 41 of them. A wrong number is not a typo you notice:
   the lines pull from the WRONG deal, the total is somebody else's, and the
   invoice that comes off that LPO months later is costed against it.

   So the number is picked from the client's own list, and the list carries
   enough to choose by - date, type, how many lines, what it adds up to.

   Typing stays. CSSP-RENT-0002 cites QUT-146 and CSSP-OPEN-0001 cites QUT-03,
   both HDEC's own references and neither one of ours; a PO that cannot be
   filed is worse than one filed against a number we do not hold.

   No DOM here beyond the field it writes into - `lpo.js` owns its button, and
   the list logic is testable on its own. Same shape as `lpo_picker.js`. */
(function () {
    'use strict';

    const api = () => window.pywebview.api;

    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

    const TYPE_LABEL = {
        materials: 'Materials',
        equipment: 'Equipment',
        manpower: 'Manpower',
        service: 'Service',
    };

    function money(value) {
        return '$' + (Number(value) || 0).toLocaleString('en-US',
            { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }

    /* One readable line per choice. Every part is a WORD or a number, never a
       colour, and the total is only shown when it could actually be read. */
    function describe(row) {
        if (!row) return '';
        const parts = [TYPE_LABEL[(row.quote_type || '').toLowerCase()] || 'Quotation'];
        if (row.quote_date) parts.push(String(row.quote_date));
        const n = Number(row.lines) || 0;
        parts.push(n === 1 ? '1 line' : n + ' lines');
        // An equipment quotation keeps its quantities in per-document custom
        // columns, so nothing outside that document can total it. Printing
        // "$0.00" beside a real quotation would read as free.
        parts.push(row.total_readable ? money(row.total) : 'total not readable here');
        if (row.rfq_no) parts.push('RFQ ' + row.rfq_no);
        return parts.join(' · ');
    }

    /* The quotations this client has. A blank client must not offer every
       client's, which is exactly how the wrong deal gets cited. */
    async function choicesFor(clientName) {
        const client = String(clientName || '').trim();
        if (!client) return [];
        try {
            return (await api().client_quotes(client)) || [];
        } catch (error) {
            return [];               // the form still works without a picker
        }
    }

    /* The dialog. Returns the chosen row (not just the number - the caller
       wants its total too), or null if nothing was chosen. */
    async function pick(clientName, current) {
        if (!window.Popup) return null;
        const rows = await choicesFor(clientName);
        const chosen = String(current || '').trim().toLowerCase();

        const rowHtml = rows.map((r, i) => `
            <button type="button" class="lpo-pick-row${
                String(r.quote_no).toLowerCase() === chosen ? ' is-on' : ''}"
                    data-i="${i}">
                <span class="lpo-pick-no">${esc(r.quote_no)}</span>
                <span class="lpo-pick-meta">${esc(describe(r))}</span>
            </button>`).join('');

        const body = `<div class="lpo-pick">
            ${rows.length ? rowHtml : `<p class="lpo-pick-empty">${
                clientName
                    ? esc(clientName) + ' has no saved quotations. Type their own '
                      + 'reference in the box instead.'
                    : 'Fill the client in first, then their quotations appear here.'}</p>`}
        </div>`;

        let picked = null;
        const handle = window.Popup.open({
            title: 'Which quotation is this PO answering?',
            subtitle: clientName || '',
            html: true, body, size: 'md', className: 'lpo-pick-popup',
            actions: [{ label: 'Cancel', value: null, cls: 'ghost' }],
            onOpen: (h) => {
                h.body.querySelectorAll('.lpo-pick-row').forEach((el) => {
                    el.onclick = () => {
                        picked = rows[Number(el.dataset.i)] || null;
                        h.close('pick');
                    };
                });
            },
        });
        const result = await handle.result;
        if (result !== 'pick' || !picked) return null;
        return picked;
    }

    window.QuotePicker = { choicesFor, describe, pick };
})();
