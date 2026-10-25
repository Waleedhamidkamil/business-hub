/* Shared client identity and active-company defaults. Saved documents keep
   their original wording. Bank choices are references to our profile settings. */
(function () {
    'use strict';
    const $ = id => document.getElementById(id);
    const api = () => window.pywebview.api;
    const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g,
        c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
    const copy = value => JSON.parse(JSON.stringify(value));
    const state = { records: [], record: null, draft: null, profile: '', banks: [],
        dirty: false, saving: false, listSeq: 0, detailSeq: 0, search: '', status: 'active' };
    const blank = () => ({name:'', legal_name:'', name_ar:'', aliases:[], contacts:[], addresses:[],
        website:'', tax_id:'', registration_no:'', notes:'', archived:false,
        defaults:{payment_method:'', bank_key:'', alternative_bank_keys:[], currency:'',
            payment_terms_days:null, invoice_instructions:'', withholding:null, address:''}});
    function normalise(record) {
        const base = blank();
        const result = {...base, ...copy(record || {})};
        result.defaults = {...base.defaults, ...(result.defaults || {})};
        ['aliases', 'contacts', 'addresses'].forEach(key => {
            if (!Array.isArray(result[key])) result[key] = [];
        });
        if (!Array.isArray(result.defaults.alternative_bank_keys)) result.defaults.alternative_bank_keys = [];
        return result;
    }
    function message(text, error = false) {
        const el = $('cmMessage');
        if (!el) return;
        el.textContent = text || '';
        el.hidden = !text;
        el.setAttribute('role', error ? 'alert' : 'status');
        el.classList.toggle('is-error', error);
    }
    function setDirty() {
        state.dirty = true;
        const status = $('cmSaveState'); if (status) status.textContent = 'Unsaved changes';
        const save = $('cmSave'); if (save) save.disabled = false;
        message('');
    }
    async function canLeave() {
        if (state.saving) { message('Please wait until the client finishes saving.'); return false; }
        if (!state.dirty) return true;
        const confirmed = await window.Popup.confirm('Discard client changes?',
            'Your changes to this client have not been saved.', {confirm:'Discard changes'});
        if (confirmed) {
            state.dirty = false;
            state.draft = state.record ? copy(state.record) : null;
            renderForm();
        }
        return confirmed;
    }
    async function leave(action) {
        if (!(await canLeave())) return;
        ++state.detailSeq;
        action();
    }
    async function show() {
        if (window.hideAllViews) window.hideAllViews();
        else document.querySelectorAll('.view').forEach(el => {el.hidden = true;});
        document.body.classList.remove('on-dashboard', 'has-rail');
        ['poStrip','poActions','quoteActions','dnActions','invActions','typeRail','docStatus'].forEach(id => {
            if ($(id)) $(id).hidden = true;
        });
        if (window.OpenPanel) window.OpenPanel.hide();
        $('clientMasterView').hidden = false;
        if ($('btnHome')) $('btnHome').hidden = false;
        if (window.setBrand) window.setBrand('Master Data');
        if (window.refreshUniversalSettingsBtn) window.refreshUniversalSettingsBtn();
        if (!state.draft) renderEmpty();
        await load();
    }
    async function load() {
        const seq = ++state.listSeq;
        $('cmList').setAttribute('aria-busy', 'true');
        try {
            const r = await api().cm_list(state.search, state.status);
            if (seq !== state.listSeq) return;
            if (!r || !r.ok) throw Error((r && r.error) || 'Could not load clients.');
            state.records = r.records || [];
            $('cmCount').textContent = `${r.total == null ? state.records.length : r.total} clients`;
            renderList();
        } catch (e) {
            if (seq === state.listSeq) message(e.message || 'Could not load clients. Try again.', true);
        } finally {
            if (seq === state.listSeq) $('cmList').setAttribute('aria-busy', 'false');
        }
    }
    function renderList() {
        $('cmList').innerHTML = state.records.length ? state.records.map(r => {
            const contact = (r.contacts || []).find(c => c.primary) || (r.contacts || [])[0];
            const count = r.usage && r.usage.total;
            return `<button type="button" class="cm-client${state.record && r.id === state.record.id ? ' is-selected' : ''}"
                data-cm-id="${esc(r.id)}" aria-pressed="${!!(state.record && r.id === state.record.id)}">
                <span class="cm-client-top"><strong>${esc(r.name)}</strong><span class="cm-badge">${r.archived ? 'Archived' : 'Active'}</span></span>
                ${r.name_ar ? `<span dir="auto">${esc(r.name_ar)}</span>` : ''}
                <span class="cm-client-sub">${esc(contact && (contact.name || contact.email) || r.legal_name || 'No contact added')}</span>
                <span class="cm-client-meta">${count == null ? 'Document history available' : `${Number(count).toLocaleString()} document${count === 1 ? '' : 's'}`}${(r.aliases || []).length ? ` · ${r.aliases.length} other name${r.aliases.length === 1 ? '' : 's'}` : ''}</span>
            </button>`;
        }).join('') : `<div class="cm-empty"><strong>${state.search ? 'No matching clients' : 'No clients here yet'}</strong><p>${state.search ? 'Try a different name or change the status filter.' : 'Add a client to keep their details and preferences together.'}</p></div>`;
    }
    function renderEmpty() {
        $('cmDetail').innerHTML = '<div class="cm-empty cm-welcome"><span class="cm-monogram" aria-hidden="true">C</span><h3>One place for every client</h3><p>Select a client to edit their details, or create one before your next document.</p><p>Identity is shared across your companies. Document defaults belong to the active company.</p></div>';
    }
    async function select(identity) {
        if (!(await canLeave())) return;
        const seq = ++state.detailSeq;
        state.record = null; state.draft = null;
        $('cmDetail').innerHTML = '<div class="cm-empty" role="status">Loading client details…</div>';
        message('');
        try {
            const r = await api().cm_get(identity == null ? '' : identity);
            if (seq !== state.detailSeq) return;
            if (!r || !r.ok) throw Error((r && r.error) || 'Could not load this client.');
            state.record = r.record ? normalise(r.record) : null;
            state.draft = normalise(r.record);
            state.profile = r.profile_id;
            state.banks = r.bank_options || [];
            state.dirty = false;
            renderForm(); renderList();
            const heading = $('cmDetailTitle'); if (heading) heading.focus();
        } catch (e) {
            if (seq !== state.detailSeq) return;
            renderEmpty(); message(e.message || 'Could not load this client.', true);
        }
    }
    function field(label, key, value, options = {}) {
        const attrs = `data-cm-field="${key}"${options.dir ? ` dir="${options.dir}"` : ''}`;
        const control = options.area ? `<textarea ${attrs} rows="${options.rows || 2}">${esc(value)}</textarea>` :
            `<input ${attrs} type="${options.type || 'text'}" value="${esc(value)}"${options.required ? ' required' : ''}${options.type === 'number' ? ' min="0" step="1"' : ''}>`;
        return `<label class="cm-field${options.full ? ' cm-full' : ''}"><span>${label}</span>${control}${options.help ? `<small>${options.help}</small>` : ''}</label>`;
    }
    function choice(label, key, value, options, full = false) {
        return `<label class="cm-field${full ? ' cm-full' : ''}"><span>${label}</span><select data-cm-field="${key}">${options.map(([v,l]) => `<option value="${esc(v)}"${String(value == null ? '' : value) === String(v) ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></label>`;
    }
    function renderContacts(d) {
        return d.contacts.map((c,i) => `<div class="cm-repeat-row"><div class="cm-section-top"><strong>Contact ${i + 1}</strong><button type="button" class="ghost" data-cm-remove="contacts" data-cm-index="${i}">Remove contact</button></div><div class="cm-grid">
            ${field('Name',`contacts.${i}.name`,c.name)}${field('Role',`contacts.${i}.role`,c.role)}
            ${field('Email',`contacts.${i}.email`,c.email,{type:'email'})}${field('Phone',`contacts.${i}.phone`,c.phone,{type:'tel'})}
            <label class="cm-check"><input type="checkbox" data-cm-field="contacts.${i}.primary"${c.primary ? ' checked' : ''}> Primary contact</label></div></div>`).join('');
    }
    function renderAddresses(d) {
        return d.addresses.map((a,i) => `<div class="cm-repeat-row"><div class="cm-section-top"><strong>Address ${i + 1}</strong><button type="button" class="ghost" data-cm-remove="addresses" data-cm-index="${i}">Remove address</button></div><div class="cm-grid">
            ${field('Label',`addresses.${i}.label`,a.label,{full:true})}${field('Address',`addresses.${i}.address`,a.address,{area:true,full:true})}</div></div>`).join('');
    }
    function renderForm() {
        const d = state.draft, p = d.defaults;
        if (!d) return renderEmpty();
        const profile = window.profileDisplayName ? window.profileDisplayName(state.profile) : state.profile;
        const bankOptions = [['','No preferred account'], ...state.banks.map(b => [b.key, `${b.label}${b.currency ? ` (${b.currency})` : ''}`])];
        if (p.bank_key && !state.banks.some(b => b.key === p.bank_key)) bankOptions.push([p.bank_key, `Unavailable account: ${p.bank_key}`]);
        const missingAlternatives = p.alternative_bank_keys.filter(key => !state.banks.some(b => b.key === key));
        $('cmDetail').innerHTML = `<form id="cmForm" class="cm-form" novalidate>
            <div class="cm-detail-head"><div><div class="cm-eyebrow">${d.id ? 'Client record' : 'New client'}</div><h3 id="cmDetailTitle" tabindex="-1">${esc(d.name || 'New client')}</h3></div><span class="cm-badge">${d.archived ? 'Archived' : 'Active'}</span></div>
            <div class="cm-form-scroll">
            <section class="cm-section"><div class="cm-section-top"><h4>Identity</h4><span class="cm-scope">Shared across companies</span></div><div class="cm-grid">
                ${field('Display name *','name',d.name,{required:true})}${field('Legal name','legal_name',d.legal_name)}
                ${field('Arabic name','name_ar',d.name_ar,{dir:'rtl'})}${field('Other names','aliases',d.aliases.join('\n'),{area:true,help:'One name per line. These names connect the client’s history.'})}
                ${field('Website','website',d.website)}${field('Tax ID','tax_id',d.tax_id)}${field('Registration number','registration_no',d.registration_no)}
            </div></section>
            <section class="cm-section"><div class="cm-section-top"><h4>Contacts</h4><button type="button" class="secondary" data-cm-add="contacts">Add contact</button></div>${d.contacts.length ? renderContacts(d) : '<p class="cm-muted">No contacts added.</p>'}</section>
            <section class="cm-section"><div class="cm-section-top"><h4>Addresses</h4><button type="button" class="secondary" data-cm-add="addresses">Add address</button></div>${d.addresses.length ? renderAddresses(d) : '<p class="cm-muted">No addresses added.</p>'}</section>
            <section class="cm-section cm-preferences"><div class="cm-section-top"><h4>Document defaults</h4><span class="cm-scope">${esc(profile || 'Active company')}</span></div>
                <p class="cm-muted">Used when you choose Apply client defaults on a new document.</p><div class="cm-grid">
                ${choice('Payment method','defaults.payment_method',p.payment_method,[['','No preference'],['cash','Cash'],['bank_transfer','Bank transfer']])}
                ${choice('Currency','defaults.currency',p.currency,[['','No preference'],['USD','USD'],['IQD','IQD']])}
                ${choice('Our receiving account','defaults.bank_key',p.bank_key,bankOptions,true)}
                <fieldset class="cm-alternatives cm-full"><legend>Alternative receiving accounts</legend>${state.banks.length ? state.banks.map(b => `<label class="cm-check"><input type="checkbox" data-cm-bank="${esc(b.key)}"${p.alternative_bank_keys.includes(b.key) ? ' checked' : ''}> ${esc(b.label)}${b.currency ? ` (${esc(b.currency)})` : ''}</label>`).join('') : '<p class="cm-muted">No receiving accounts configured for this company.</p>'}${missingAlternatives.map(key => `<label class="cm-check"><input type="checkbox" data-cm-bank="${esc(key)}" checked> Unavailable account: ${esc(key)}</label>`).join('')}</fieldset>
                ${field('Payment terms (days)','defaults.payment_terms_days',p.payment_terms_days,{type:'number'})}
                ${choice('Withholding','defaults.withholding',p.withholding,[['','No preference'],['true','Include withholding'],['false','Do not include withholding']])}
                ${field('Default document address','defaults.address',p.address,{area:true,full:true})}
                ${field('Invoice instructions','defaults.invoice_instructions',p.invoice_instructions,{area:true,full:true,help:'Kept here for reference when preparing invoices.'})}
                </div><p class="cm-muted cm-bank-note">Receiving accounts are maintained in company settings. Choose an existing account here.</p>
            </section>
            <section class="cm-section"><h4>Internal notes</h4>${field('Notes','notes',d.notes,{area:true,rows:3,full:true})}</section>
            </div>
            <div class="cm-form-footer"><span id="cmSaveState" role="status">${state.dirty ? 'Unsaved changes' : d.id ? 'Saved' : 'Not saved yet'}</span><div class="cm-footer-actions">${d.id ? `<button type="button" class="ghost" data-cm-action="documents">View documents</button><button type="button" class="secondary" data-cm-action="archive">${d.archived ? 'Restore' : 'Archive'}</button>` : ''}<button type="submit" class="primary" id="cmSave"${d.id && !state.dirty ? ' disabled' : ''}>Save client</button></div></div>
        </form>`;
    }
    function edit(event) {
        if (!state.draft || state.saving) return;
        const el = event.target, key = el.dataset.cmField;
        if (el.dataset.cmBank != null) {
            const values = state.draft.defaults.alternative_bank_keys.filter(k => k !== el.dataset.cmBank);
            if (el.checked) values.push(el.dataset.cmBank);
            state.draft.defaults.alternative_bank_keys = values; setDirty(); return;
        }
        if (!key) return;
        let value = el.type === 'checkbox' ? el.checked : el.value;
        if (key === 'aliases') value = String(value).split(/\r?\n/).map(s => s.trim()).filter(Boolean);
        if (key === 'defaults.payment_terms_days') value = value === '' ? null : Number(value);
        if (key === 'defaults.withholding') value = value === '' ? null : value === 'true';
        const parts = key.split('.'); let target = state.draft;
        parts.slice(0,-1).forEach(part => {target = target[part];});
        target[parts[parts.length - 1]] = value;
        if (/^contacts\.\d+\.primary$/.test(key) && value) {
            state.draft.contacts.forEach((c,i) => {c.primary = i === Number(parts[1]);});
            $('cmDetail').querySelectorAll('[data-cm-field$=".primary"]').forEach(input => {input.checked = input === el;});
        }
        setDirty();
    }
    async function save() {
        if (!state.draft || state.saving) return;
        const payload = copy(state.draft);
        payload.name = payload.name.trim();
        if (!payload.name) {message('Enter a display name before saving.', true); return;}
        const days = payload.defaults.payment_terms_days;
        if (days != null && (!Number.isInteger(days) || days < 0)) {message('Payment terms must be a whole number of days, zero or greater.', true); return;}
        payload.profile_id = state.profile;
        delete payload.usage;
        const seq = state.detailSeq;
        state.saving = true;
        $('cmDetail').querySelectorAll('input,textarea,select,button').forEach(el => {el.disabled = true;});
        message('Saving client…');
        try {
            const r = await api().cm_save(payload);
            if (seq !== state.detailSeq) return;
            if (!r || !r.ok) throw Error((r && r.error) || 'Could not save this client.');
            state.record = normalise(r.record); state.draft = copy(state.record); state.dirty = false;
            renderForm(); message('Client saved.'); await load();
        } catch (e) {
            if (seq === state.detailSeq) {renderForm(); message(e.message || 'Could not save this client.', true);}
        } finally {state.saving = false;}
    }
    async function archive() {
        if (!state.record || !(await canLeave())) return;
        const record = state.record;
        const next = !record.archived;
        const confirmed = await window.Popup.confirm(next ? 'Archive this client?' : 'Restore this client?',
            next ? `${record.name} will leave the active client list. Their saved documents remain available.` : `${record.name} will return to the active client list.`,
            {confirm:next ? 'Archive client' : 'Restore client'});
        if (!confirmed) return;
        const seq = state.detailSeq;
        state.saving = true;
        $('cmDetail').querySelectorAll('input,textarea,select,button').forEach(el => {el.disabled = true;});
        try {
            const r = await api().cm_archive(record.id, next);
            if (seq !== state.detailSeq) return;
            if (!r || !r.ok) throw Error((r && r.error) || 'Could not update the client status.');
            state.record = normalise(r.record); state.draft = copy(state.record); state.dirty = false;
            renderForm(); message(next ? 'Client archived.' : 'Client restored.'); await load();
        } catch (e) {if (seq === state.detailSeq) {renderForm(); message(e.message, true);}}
        finally {state.saving = false;}
    }
    function init() {
        if (!$('clientMasterView')) return;
        let timer;
        $('cmSearch').addEventListener('input', event => {
            state.search = event.target.value.trim(); ++state.listSeq;
            clearTimeout(timer); timer = setTimeout(load, 160);
        });
        $('cmStatus').addEventListener('change', event => {state.status = event.target.value; load();});
        $('cmNew').addEventListener('click', () => select(''));
        $('cmBack').addEventListener('click', () => leave(() => window.showHub && window.showHub()));
        $('cmMaterials').addEventListener('click', () => leave(() => window.showItemMaster && window.showItemMaster()));
        if ($('imClientsTab')) $('imClientsTab').addEventListener('click', show);
        $('cmList').addEventListener('click', event => {
            // A record id is a uuid hex string, not a number. Number() turned
            // every one of them into NaN and made the whole list unclickable.
            const row = event.target.closest('[data-cm-id]'); if (row) select(row.dataset.cmId);
        });
        const detail = $('cmDetail');
        detail.addEventListener('input', edit); detail.addEventListener('change', edit);
        detail.addEventListener('submit', event => {event.preventDefault(); save();});
        detail.addEventListener('click', event => {
            const button = event.target.closest('button'); if (!button || state.saving) return;
            const data = button.dataset;
            if (data.cmAdd) {
                state.draft[data.cmAdd].push(data.cmAdd === 'contacts' ? {name:'',role:'',email:'',phone:'',primary:false} : {label:'',address:''});
                setDirty(); const scroll = detail.querySelector('.cm-form-scroll'); const top = scroll ? scroll.scrollTop : 0;
                renderForm(); const next = detail.querySelector('.cm-form-scroll'); if (next) next.scrollTop = top;
                const inputs = detail.querySelectorAll(`[data-cm-field^="${data.cmAdd}.${state.draft[data.cmAdd].length - 1}."]`); if (inputs[0]) inputs[0].focus();
            } else if (data.cmRemove) {
                state.draft[data.cmRemove].splice(Number(data.cmIndex),1); setDirty(); renderForm();
            } else if (data.cmAction === 'archive') archive();
            else if (data.cmAction === 'documents' && state.record) leave(() => window.openClientProfile && window.openClientProfile(state.record.name));
        });
    }
    window.ClientMaster = {show, select, newClient:() => select(''), save, archive, canLeave, load};
    window.showClientMaster = show;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
