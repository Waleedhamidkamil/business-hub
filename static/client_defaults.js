/* Client settings, applied when a client is PICKED on a document.

   The trigger is the client field's `change` event. The browser fires it for a
   real user choice - picking from the datalist, or committing a typed name -
   and NOT for a value assigned in code, which is what loading, restoring,
   duplicating and importing all do. That one fact is why this can never repaint
   a saved document behind the user's back.

   A field is filled only when it is still the editor's own starting value on a
   fresh document, still empty, or still exactly what THIS module last wrote -
   so switching client replaces the previous client's address, and never
   replaces anything the user typed or a saved document supplied. */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const adapters = {
        qut: {client:'qClient', address:'qAddress', code:'qClientCode', getState:() => typeof quoteState === 'undefined' ? null : quoteState},
        dn: {client:'dnClient', address:'dnAddress', code:'dnClientCode', getState:() => typeof dnState === 'undefined' ? null : dnState},
        inv: {client:'invClient', address:'invAddress', code:'invClientCode', getState:() => typeof invState === 'undefined' ? null : invState},
    };
    const notify = (text, bad) => window.toast && window.toast(text, bad);
    const blank = () => ({baseline:{}, written:{}, touched:new Set(), applying:false, seq:0});
    const sessions = {qut:blank(), dn:blank(), inv:blank()};

    /* THE CHIP. A document prints its client's legal name - "China
       Communications construction company Ltd. Iraq Branch" - which is correct
       on paper and says nothing about which client the app thinks this is. The
       chip beside the label answers that: TDC. It is the same short code the
       document number and the folder are built from, so if the chip is wrong,
       the number is about to be wrong too, and you can see it before saving. */
    function paintCode(kind, result) {
        const chip = $(adapters[kind].code);
        if (!chip) return;
        const code = result && result.ok && result.known
            ? String(result.code || result.name || '').trim() : '';
        chip.textContent = code;
        chip.hidden = !code;
        chip.title = code ? `Numbered and filed under ${code}` : '';
    }

    /* Read the chip off whatever client the field holds NOW, without touching a
       single field. This is how a loaded, duplicated or imported document gets
       its chip: those paths assign the client in code, so no `change` fires. */
    async function refreshCode(kind) {
        const a = adapters[kind]; if (!a) return;
        const s = sessions[kind], seq = s.seq;
        const field = $(a.client);
        const name = field && field.value ? field.value.trim() : '';
        if (!name) return;
        let result;
        try {
            result = await window.pywebview.api.cm_defaults(name);
        } catch (error) { return; }
        if (sessions[kind] !== s || s.seq !== seq) return;   // moved on since
        if ((($(a.client) || {}).value || '').trim() !== name) return;
        paintCode(kind, result);
    }

    /* A different document is in the editor now. Forget what we filled into the
       last one and re-read the starting values, so a value we wrote for document
       A is never mistaken for one document B legitimately saved. */
    function reset(kind) {
        const a = adapters[kind], old = sessions[kind];
        if (!a) return;
        const doc = a.getState() || {};
        sessions[kind] = {
            baseline: {address: ($(a.address) || {}).value || '', bank: ($('invBank') || {}).value || '',
                       currency: doc.sellCurrency, withholding: doc.withholdingIncluded},
            written: {}, touched: new Set(), applying: false, adopted: null,
            seq: (old ? old.seq : 0) + 1,
        };
        // The client of the OLD document is not this document's client.
        paintCode(kind, null);
        // Every load path resets first and assigns the client after, in the same
        // task. A zero timeout runs once that task is finished, so this sees the
        // document's real client - one call, no call-site changes, no fields
        // touched. On a genuinely new document the field is blank and it is a
        // no-op.
        setTimeout(() => refreshCode(kind), 0);
    }

    /* Fresh = built by initNew* and not derived from another document. Only on
       a fresh document is the STARTING value (a blank address, the profile's
       default account, USD) ours to replace - on a loaded, duplicated or
       imported one those values are the document's. */
    function fresh(kind, field) {
        /* `adopted` is a caller naming the fields its source actually SUPPLIED.
           Everything else on the document is still the value initNew* put
           there, so it is still ours to fill.

           This is per FIELD because the sources differ. A PO carries lines, a
           company and a reference: no address, no receiving account, no tax
           status, so `adopt(kind)` names nothing. A quotation or a delivery
           note carries a client AND an address, but neither has a receiving
           account or a tax status - a delivery note has no bank on it at all -
           so they name `address` and leave the rest to the client's settings.

           Waleed found the reason this has to be per field: an invoice made
           from DN-HDEC-066 came out on the profile's default account instead of
           HDEC's, because the whole document was treated as "not fresh" for the
           sake of one field the DN did supply. */
        const s = sessions[kind];
        if (s && s.adopted) return !s.adopted.has(field);
        const doc = adapters[kind].getState();
        return !!doc && doc.mode === 'new' && !doc.sourceQuote && !doc.sourceDn && !doc.sourcePo;
    }
    function replaceable(kind, field, current) {
        const s = sessions[kind];
        if (s.touched.has(field)) return false;
        return current === '' || current === s.written[field] ||
               (fresh(kind, field) && current === s.baseline[field]);
    }

    /* Anything the user changes by hand is theirs from then on. `applying`
       excludes the events this module dispatches itself. */
    function mark(event) {
        const id = event.target && event.target.id;
        for (const [kind, a] of Object.entries(adapters)) {
            const s = sessions[kind];
            if (!s || s.applying) continue;
            if (id === a.address) s.touched.add('address');
            if (kind === 'inv' && id === 'invBank') s.touched.add('bank');
        }
    }
    document.addEventListener('input', mark, true);
    document.addEventListener('change', mark, true);
    document.addEventListener('click', event => {
        const target = event.target;
        if (!target || !target.closest || sessions.inv.applying) return;
        if (target.closest('#invSellCurSwitch')) sessions.inv.touched.add('currency');
    }, true);
    document.addEventListener('keydown', event => {
        if (event.target && event.target.id === 'invSellCurSwitch' &&
            ['Enter', ' '].includes(event.key) && !sessions.inv.applying) sessions.inv.touched.add('currency');
    }, true);

    async function applyFor(kind, adopted) {
        const a = adapters[kind]; if (!a) return;
        const doc = a.getState(); if (!doc) return;
        const field = $(a.client);
        const name = field && field.value ? field.value.trim() : '';
        if (!name) return;
        const s = sessions[kind];
        if (adopted) {
            s.adopted = new Set(Array.isArray(adopted) ? adopted : []);
        }
        const seq = ++s.seq;
        // The document must still be this one, and the client still this client.
        const live = () => sessions[kind] === s && s.seq === seq &&
            ((($(a.client) || {}).value || '').trim() === name);

        let result;
        try {
            result = await window.pywebview.api.cm_defaults(name);
        } catch (error) {
            if (window.console) console.warn('client settings lookup failed', error);
            return;
        }
        if (!live()) return;
        if (!result || !result.ok) {
            paintCode(kind, result);     // an unknown name carries no code
            // An unknown name is ordinary - the user is naming a new client.
            // A KNOWN client whose settings cannot be applied is worth saying.
            if (result && result.known) notify(result.error, true);
            return;
        }

        const applied = [];
        // The sell currency is deliberately NOT applied here. Changing it needs
        // an exchange rate only the user can confirm, so it opens a prompt - and
        // a dialog appearing because you picked a name is not an automatic
        // convenience, it is an interruption that also re-prices the invoice.
        // Say what the client is usually billed in and let them decide.
        const note = (kind === 'inv' && ['USD', 'IQD'].includes(result.currency) &&
                      result.currency !== doc.sellCurrency && !s.touched.has('currency'))
            ? ` ${result.name} is usually billed in ${result.currency}.` : '';

        paintCode(kind, result);
        s.applying = true;
        try {
            /* WHAT THE DOCUMENT PRINTS (Waleed's ruling, 2026-09-11): the
               client's legal name, every time. "TDC", "CCCC ltd", the legal
               name itself and "هيونداي" are four spellings of one client, and a
               document should not print whichever one happened to be typed.
               Resolution runs the other way too, so a document that prints the
               full legal name is still numbered and filed under TDC.

               Re-picking a name that is on file therefore puts the legal name
               back, which is the ruling working as asked. A one-off spelling is
               still possible: a name that is NOT on file is left exactly as
               typed, because there is no client to be inconsistent with. */
            const client = $(a.client);
            const printed = String(result.print_name || '').trim();
            if (printed && client && client.value.trim() !== printed) {
                client.value = printed;
                // `input`, not `change`: `change` is what brought us here.
                client.dispatchEvent(new Event('input', {bubbles:true}));
                s.written.client = printed; applied.push('printed name');
            }
            const address = $(a.address);
            if (result.address && address && replaceable(kind, 'address', address.value)) {
                address.value = result.address;
                address.dispatchEvent(new Event('input', {bubbles:true}));
                s.written.address = result.address; applied.push('address');
            }
            if (kind === 'inv') {
                const bank = $('invBank'), key = result.payment_bank_key;
                if (key && bank && Array.from(bank.options).some(option => option.value === key) &&
                    replaceable(kind, 'bank', bank.value)) {
                    bank.value = key;
                    bank.dispatchEvent(new Event('change', {bubbles:true}));
                    s.written.bank = key; applied.push('account');
                }
                if (typeof result.withholding === 'boolean' &&
                    replaceable(kind, 'withholding', doc.withholdingIncluded)) {
                    doc.withholdingIncluded = result.withholding;
                    s.written.withholding = result.withholding; applied.push('tax status');
                    const label = $('invTaxStatusCell');
                    if (label) label.textContent = result.withholding ? 'Included' : 'Not included';
                }
            }
        } finally {
            s.applying = false;
        }

        if (!applied.length) {
            if (note) notify(note.trim());   // nothing to fill, but worth knowing
            return;
        }
        doc.dirty = true;
        if (kind === 'inv' && typeof updateInvTotals === 'function') updateInvTotals();
        notify(`Applied ${result.name}'s saved ${applied.join(', ')}.${note}`);
    }

    function init() {
        for (const [kind, a] of Object.entries(adapters)) {
            const input = $(a.client);
            if (!input || input._clientDefaults) continue;
            input._clientDefaults = true;
            // `change`, not `input`: it fires once the choice is committed, and
            // never for a programmatic assignment. See the file header.
            input.addEventListener('change', () => applyFor(kind));
        }
    }
    /* `adopt(kind, supplied)` is for a document BUILT from another one: the
       client came across in code, so no `change` fired. `supplied` lists the
       fields that source really carried - everything else is still the fresh
       document's own value and gets the client's setting. See `fresh`.
         adopt('inv')              a PO: lines and a company, nothing else
         adopt('inv', ['address']) a quotation or DN: client and address */
    window.ClientDefaults = {
        reset, apply: applyFor,
        adopt: (kind, supplied) => applyFor(kind, supplied || []),
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
