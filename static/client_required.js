/* A document may only name a client that is on file (Waleed, 2026-09-11).

   The backend refuses the save - that is the check that cannot be walked
   around, in `api._known_client_error`. This is the way OUT of that refusal:
   rather than sending you to Master Data with a half-finished invoice on
   screen, it offers to add the client here, then the editor saves again.

   The dialog deliberately asks for the same identity fields Master Data does,
   and writes through the same `cm_save`, so there is ONE way a client comes to
   exist and one set of identity rules. Anything else (contacts, addresses,
   payment terms) is filled in later on the Clients screen. */
(function () {
    'use strict';
    const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
        c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const api = () => window.pywebview.api;
    const notify = (text, bad) => window.toast && window.toast(text, bad);

    /* The shape `api._known_client_error` returns. Keyed on the PRESENCE of
       `unknown_client`, not on the message: an error string is for a person to
       read and will be reworded one day. */
    function isClientRefusal(result) {
        return !!result && result.ok === false &&
            Object.prototype.hasOwnProperty.call(result, 'unknown_client');
    }

    const FIELDS = {
        qut: {client: 'qClient', populate: 'populateClientList'},
        dn: {client: 'dnClient', populate: 'populateDnClientList'},
        inv: {client: 'invClient', populate: 'populateInvClientList'},
        // The LPO form (2026-09-11). It has no `populate` because its client
        // datalist is built once when the form opens, and the form re-reads the
        // field on save - so writing the name back into the box is enough.
        lpo: {client: 'lpoFClient'},
    };

    function bankOptions() {
        const bank = document.getElementById('invBank');
        if (!bank) return '';
        const options = Array.from(bank.options)
            .map(o => `<option value="${esc(o.value)}">${esc(o.textContent)}</option>`).join('');
        return `<label class="cr-field"><span>Receiving account</span>
            <select id="crBank"><option value="">Not set yet</option>${options}</select>
            <small>Applied automatically to this client's invoices.</small></label>`;
    }

    function form(typed) {
        // The typed name becomes the SHORT name, because that is what the
        // document number is built from and what the user just reached for.
        // If they shorten it, the original spelling is kept as an alias so the
        // name they typed still finds this client next time.
        return `<div class="cr-form">
            <p class="cr-lead">"${esc(typed)}" is not one of your clients yet.
               Add them now and the save continues.</p>
            <label class="cr-field"><span>Short name *</span>
                <input id="crName" type="text" value="${esc(typed)}" autocomplete="off">
                <small>Used in document numbers and folders: INV-MT-<b id="crCode"></b>-054.</small></label>
            <label class="cr-field"><span>Full legal name</span>
                <input id="crLegal" type="text" autocomplete="off" placeholder="Printed on the document">
                <small>What documents print. Leave blank to print the short name.</small></label>
            <label class="cr-field"><span>Arabic name</span>
                <input id="crArabic" type="text" dir="rtl" autocomplete="off"></label>
            <label class="cr-field"><span>Other names</span>
                <textarea id="crAliases" rows="2" placeholder="One per line"></textarea>
                <small>Spellings that mean this client. They all resolve to the short name.</small></label>
            ${bankOptions()}
            <p class="cr-note" id="crNote" hidden></p>
        </div>`;
    }

    /* The code shown in the hint has to be the code the number will really use,
       so it is stripped the same way `client_master.document_code` strips it. */
    const codeOf = value => String(value || '').trim().replace(/[\\/:*?"<>|]/g, '') || 'CLIENT';

    async function addClient(typed, kind) {
        let added = null;
        const handle = window.Popup.open({
            title: 'Add this client',
            html: true,
            body: form(typed),
            size: 'md',
            className: 'client-required-popup',
            actions: [
                {label: 'Cancel', value: null, cls: 'ghost'},
                {label: 'Add client and save', value: 'add', cls: 'primary', primary: true,
                 keep: true, onClick: h => { submit(h); }},
            ],
            onOpen: h => {
                const name = h.body.querySelector('#crName');
                const code = h.body.querySelector('#crCode');
                const paint = () => { code.textContent = codeOf(name.value); };
                name.addEventListener('input', paint); paint();
                setTimeout(() => name.focus(), 0);
            },
        });

        // `keep: true` means the button acts without closing, so the dialog can
        // report a refusal (a name another client already owns) and stay open
        // with what was typed still in it.
        async function submit(h) {
            const note = h.body.querySelector('#crNote');
            const say = text => {
                note.textContent = text; note.hidden = !text;
            };
            const short = h.body.querySelector('#crName').value.trim();
            if (!short) { say('Give this client a short name.'); return; }
            const aliases = h.body.querySelector('#crAliases').value
                .split('\n').map(a => a.trim()).filter(Boolean);
            // The spelling that got us here must keep working.
            if (typed && typed !== short && !aliases.includes(typed)) aliases.push(typed);
            const bank = h.body.querySelector('#crBank');
            const payload = {
                name: short,
                legal_name: h.body.querySelector('#crLegal').value.trim(),
                name_ar: h.body.querySelector('#crArabic').value.trim(),
                aliases, contacts: [], addresses: [],
                defaults: bank && bank.value ? {bank_key: bank.value} : {},
            };
            say('Saving…');
            let result;
            try {
                result = await api().cm_save(payload);
            } catch (error) {
                say('Could not reach the client store. Try again.');
                return;
            }
            if (!result || !result.ok) {
                say((result && result.error) || 'Could not add this client.');
                return;
            }
            added = result.record || {name: short};
            h.close('add');
        }
        const chosen = await handle.result;
        if (chosen !== 'add' || !added) return false;

        // Put the new client in the box the way picking one would, so the save
        // that follows carries a name the backend can resolve, and refresh the
        // datalist so the next document offers it.
        const field = document.getElementById((FIELDS[kind] || {}).client);
        if (field) {
            field.value = added.name || typed;
            field.dispatchEvent(new Event('input', {bubbles: true}));
        }
        const repopulate = window[(FIELDS[kind] || {}).populate];
        if (typeof repopulate === 'function') { try { await repopulate(); } catch (error) {} }
        if (window.ClientDefaults) await window.ClientDefaults.apply(kind);
        notify(`${added.name} added to your clients.`);
        return true;
    }

    async function restoreArchived(result, kind) {
        const name = result.unknown_client;
        const ok = await window.Popup.confirm(
            'This client is archived',
            `${name} is on file but archived, so they are out of the active client `
            + 'list. Restore them to use this document?',
            {confirm: 'Restore client'});
        if (!ok) return false;
        let restored;
        try {
            restored = await api().cm_archive(name, false);
        } catch (error) { restored = null; }
        if (!restored || !restored.ok) {
            notify((restored && restored.error) || 'Could not restore this client.', true);
            return false;
        }
        notify(`${name} is active again.`);
        return true;
    }

    /* Returns TRUE when the editor should save again. Anything that is not a
       client refusal returns false untouched, so the caller's own error
       handling still runs - this must never swallow an unrelated failure. */
    async function offer(result, kind) {
        if (!isClientRefusal(result) || !window.Popup) return false;
        if (result.archived_client) return await restoreArchived(result, kind);
        // A blank client has nothing to add. The editor's own message already
        // says to pick a client or N/A, which is the whole answer.
        if (!result.unknown_client) return false;
        return await addClient(result.unknown_client, kind);
    }

    window.ClientRequired = {offer, isClientRefusal};
})();
