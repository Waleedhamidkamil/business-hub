/* Verify a document (2026-10-07).

   Every PDF the app makes prints "Verification: <number> | XXXX-XXXX" at the
   foot of each page (backend/doc_verify.py). When a document turns up that
   claims to be ours, type both in here: the app says whether it printed that
   code on that document, and shows what the genuine document says - so a
   copy with a stolen stamp, or a real invoice with its amount edited, is
   caught. Opened from Settings > About > Quick access.

   Built with elements and textContent only: the number and the code are typed
   by the user and the answer echoes them back. */
(function () {
    "use strict";
    const api = () => (window.pywebview && window.pywebview.api) || {};

    function field(labelText, id, placeholder) {
        const label = document.createElement("label");
        label.className = "field verify-field";
        const span = document.createElement("span");
        span.textContent = labelText;
        const input = document.createElement("input");
        input.id = id;
        input.type = "text";
        input.placeholder = placeholder;
        input.autocomplete = "off";
        input.spellcheck = false;
        input.setAttribute("dir", "ltr");
        label.append(span, input);
        return { label, input };
    }

    function line(cls, text) {
        const p = document.createElement("p");
        p.className = cls;
        p.textContent = text;
        return p;
    }

    function factsList(d) {
        const money = (v) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        const facts = [
            `${d.kind} ${d.no}`,
            d.date ? `Dated ${d.date}` : "",
            d.client ? `Client ${d.client}` : "",
            `${d.lines} line${d.lines === 1 ? "" : "s"}`,
            d.total ? `Total ${money(d.total)}${d.currency ? " " + d.currency : ""}` : "",
        ].filter(Boolean);
        const ul = document.createElement("ul");
        ul.className = "verify-facts";
        facts.forEach((t) => { const li = document.createElement("li"); li.textContent = t; ul.appendChild(li); });
        return ul;
    }

    function openVerifyDocument() {
        const P = window.Popup;
        if (!P) return;
        const body = document.createElement("div");
        body.className = "verify-body";
        const no = field("Document number", "verifyNo", "e.g. INV-MT-TDC-030");
        const code = field("Verification code", "verifyCode", "e.g. 7K2F-9QXA");
        const hint = line("verify-hint", "Both are printed at the foot of every page of our PDFs.");
        const out = document.createElement("div");
        out.className = "verify-result";
        out.setAttribute("role", "status");
        body.append(no.label, code.label, hint, out);

        const h = P.open({
            title: "Verify a document", size: "md", body,
            actions: [
                { label: "Close", value: "cancel", cls: "ghost" },
                { label: "Check", value: "check", cls: "primary", keep: true, onClick: () => check() },
            ],
        });
        const onEnter = (e) => { if (e.key === "Enter") { e.preventDefault(); check(); } };
        no.input.addEventListener("keydown", onEnter);
        code.input.addEventListener("keydown", onEnter);

        async function check() {
            const a = api();
            if (!a.verify_document) return;
            out.textContent = "";
            out.className = "verify-result";
            const r = await a.verify_document(no.input.value, code.input.value);
            if (h.closed) return;
            if (!r || !r.ok) {
                out.append(line("verify-verdict", (r && r.error) || "The check could not run."));
                return;
            }
            // Colour never carries the answer on its own - the verdict is a word.
            out.classList.add(r.genuine ? "is-genuine" : "is-fake");
            out.append(line("verify-verdict", r.genuine ? "GENUINE" : "NOT GENUINE"));
            out.append(line("verify-reason", r.reason || ""));
            if (r.document) {
                out.append(line("verify-sub", r.genuine ? "This copy says:" : "Our document says:"),
                           factsList(r.document));
            }
            if (r.latest) out.append(line("verify-sub", "The latest version says:"), factsList(r.latest));
        }
    }

    window.openVerifyDocument = openVerifyDocument;

    function wire() {
        const b = document.getElementById("btnAboutVerify");
        if (b) b.onclick = openVerifyDocument;
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", wire);
    else wire();
})();
