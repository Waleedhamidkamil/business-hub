/* The ordered lines on a client's LPO (2026-09-11).

   Waleed's POs do not invent items. CSSP-ADM-0010 cites QUT-MT-HDEC-029 on its
   face and reproduces four of our five lines verbatim - same wording, same
   dimensions, same quantity, same rate - dropping the fifth. Their own
   "Certification" column says "Specifications identical to those in the
   attached quotation", and their total (18,390) is ours (19,286) minus exactly
   the line they dropped (7 @ 128 = 896).

   So the lines are INHERITED and the work is a tick box, not a keyboard.

   Manual entry stays for the POs that cite the client's own reference:
   CSSP-RENT-0002 cites QUT-146 and CSSP-OPEN-0001 cites QUT-03, neither of
   which is ours. Both are a single line anyway.

   Pure data in, pure data out - no DOM here, so the arithmetic can be tested
   without standing up a browser. `static/lpo.js` owns the markup. */
(function () {
    'use strict';

    const num = (value) => {
        // Commas are what a person types into a rate box, not a thousands
        // separator we should choke on.
        const n = parseFloat(String(value == null ? '' : value).replace(/,/g, ''));
        return isFinite(n) ? n : 0;
    };
    const money = (n) => n.toLocaleString('en-US',
        { minimumFractionDigits: 2, maximumFractionDigits: 2 });

    /* Every line of the quotation, ticked, at the quantity we quoted. The
       client's changes are subtractions from this - that is what a PO IS. */
    function fromQuote(rows) {
        return (rows || []).map((r) => ({
            take: true,
            item: r.item || '',
            description: r.description || '',
            unit: r.unit || '',
            qty: num(r.qty),
            rate: r.rate == null ? '' : String(r.rate),
        }));
    }

    /* A stored order read back for editing: everything on file was ordered. */
    function fromRows(rows) {
        return (rows || []).map((r) => ({
            take: true,
            item: r.item || '',
            description: r.description || '',
            unit: r.unit || '',
            qty: num(r.qty),
            rate: r.rate == null ? '' : String(r.rate),
        }));
    }

    function blank() {
        return { take: true, item: '', description: '', unit: '', qty: 0, rate: '' };
    }

    function total(lines) {
        const sum = (lines || []).reduce(
            (a, l) => (l.take ? a + num(l.qty) * num(l.rate) : a), 0);
        return Math.round(sum * 100) / 100;
    }

    /* Stated in words, never blocking (Waleed, 2026-09-11).

       A PO can legitimately carry a total that does not match its own lines - a
       discount, a rounding, a typo on their side - and refusing the save would
       strand him with a document he cannot file. Saying nothing would hide a
       misreading of the PO. So: say it plainly, and let him decide. */
    function mismatch(lines, printedTotal) {
        const printed = num(printedTotal);
        if (!printed) return '';         // nothing typed, nothing to disagree with
        const linesTotal = total(lines);
        // Deliberately NOT pre-rounded to cents: rounding first would snap every
        // sub-cent gap to exactly zero and make this tolerance a branch that can
        // never run. Below a cent is not a discrepancy worth a sentence.
        const diff = linesTotal - printed;
        if (Math.abs(diff) < 0.01) return '';
        return 'These lines add up to ' + money(linesTotal)
             + ', but the PO says ' + money(printed)
             + ' - a difference of ' + money(Math.abs(diff))
             + '. Save anyway if that is what their PO shows.';
    }

    /* What gets stored. The tick is UI state and never reaches the record, and
       a line with no name AND no quantity is somebody's abandoned keystroke. A
       line with only a quantity is kept: the diesel line may well be typed
       numbers-first, and dropping it would lose real work. */
    function toRows(lines) {
        return (lines || [])
            .filter((l) => l.take)
            .filter((l) => String(l.item || '').trim() || num(l.qty))
            .map((l) => ({
                item: String(l.item || '').trim(),
                description: String(l.description || '').trim(),
                unit: String(l.unit || '').trim(),
                qty: num(l.qty),
                rate: l.rate == null ? '' : String(l.rate),
            }));
    }

    window.LpoLines = { fromQuote, fromRows, toRows, blank, total, mismatch };
})();
