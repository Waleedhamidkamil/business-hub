/* ============================================================================
   THE POPUP CONTRACT - one implementation, every popup in the app (2026-08-26)
   ============================================================================

   The app grew popups one at a time and ended up with three separate
   vocabularies for the same idea: `.modal-*` (the confirm dialog and the five
   static ones in index.html), `.imgf-*` (the Find-pictures progress dialog) and
   `.cost-linkpicker-*` (the three costing overlays). They disagreed on
   everything a reader notices - whether Esc closes, whether clicking the dim
   background closes, whether there is an X, where the title sits, how wide the
   panel is, whether the page behind still scrolls, whether Tab can walk out of
   the dialog and into the page behind it.

   This file is the single answer. Everything that opens over the page goes
   through `Popup.open`, so a popup cannot be built wrong: the behaviour comes
   with the box.

   THE RULES, and why each one is a rule and not a preference:

     1. EVERY popup has a title. A box with no name gives the reader nothing to
        orient by, and a screen reader nothing to announce. It is required, not
        optional.
     2. Esc closes, the dim background closes, and there is an X - unless the
        popup is `dismissible: false`, which is reserved for a genuine choice
        the app cannot make on the user's behalf. Three ways out, or a stated
        reason there is only one.
     3. Focus moves INTO the popup on open, is trapped there while it is open,
        and returns to whatever opened it on close. Tab escaping into the page
        behind is the bug that makes a dialog feel broken.
     4. The page behind does not scroll. A background that moves under a modal
        reads as the modal having lost its grip.
     5. One set of widths (`sm | md | lg | xl`). A popup being "a bit wider
        than that other one" is how a UI stops looking designed.
     6. Actions live in one footer row, the primary last, and the primary is
        what Enter and the initial focus land on.
     7. Body content is TEXT by default. `html: true` is opt-in, because
        document and client data flows through these bodies - the same split,
        and the same reason, as the old `showModal`.
     8. Nothing here uses colour to carry meaning: a destructive action is a
        WORD ("Delete"), never a red button alone ([[waleed-colour-blind]]).

   `showModal(...)` is now a thin wrapper over this, so all 55 existing call
   sites keep their exact contract and inherit every rule above.

   Loaded BEFORE app.js - `showModal` depends on it.
   ========================================================================== */
(function () {
    "use strict";

    /* Widths. One list, so "a bit wider" is not a thing anyone can choose.
       `full` is part of the list rather than an escape hatch, and it is for one
       purpose only: a popup that shows a DOCUMENT (a PDF page, a preview of an
       item table). Anything else that reaches for it is really a screen. */
    const SIZES = { sm: 380, md: 480, lg: 680, xl: 900, full: 0 };

    /* Every popup on screen, innermost last. A popup opened FROM a popup must
       sit above its parent and take the Esc key first. */
    const stack = [];
    /* The scroll lock is OWNED, not inferred (2026-10-09). It used to read
       body.style.overflow whenever the stack went from 0 to 1 - but a closing
       popup leaves the stack at once and releases the page only after its
       fade, so a popup opened in that window read back our OWN "hidden" as
       the page's setting and restored it on close: the page stopped
       scrolling until the app was restarted. `pageOverflow` is captured only
       when the lock is actually taken, and given back only by its holder. */
    let scrollHeld = false;
    let pageOverflow = "";

    function reducedMotion() {
        try {
            return window.matchMedia
                && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        } catch (e) { return false; }
    }

    /* Everything the user can Tab to inside the panel. Buttons ALONE is what
       the old modal trapped, which meant a popup with an input in it leaked
       focus into the page on the second Tab. */
    const FOCUSABLE = [
        "a[href]", "button:not([disabled])", "input:not([disabled]):not([type=hidden])",
        "select:not([disabled])", "textarea:not([disabled])",
        '[tabindex]:not([tabindex="-1"])',
    ].join(",");

    function focusables(root) {
        return Array.from(root.querySelectorAll(FOCUSABLE))
            .filter((el) => el.offsetParent !== null || el === document.activeElement);
    }

    function lockScroll() {
        if (scrollHeld || !stack.length) return;        // already held (maybe by a popup still fading)
        pageOverflow = document.body.style.overflow || "";
        document.body.style.overflow = "hidden";
        scrollHeld = true;
    }

    function unlockScroll() {
        if (stack.length || !scrollHeld) return;        // a popup is still open, or nothing to give back
        document.body.style.overflow = pageOverflow;
        pageOverflow = "";
        scrollHeld = false;
    }

    let seq = 0;

    /* ---------------------------------------------------------------------
       Popup.open(spec) -> handle

       spec:
         title        string, REQUIRED - what this popup is
         subtitle     string, optional one-liner under the title
         icon         markup for a glyph before the title (a document kind)
         badge        short label chip after the title (PO / QUT / DN / INV)
         toolbar      Element/markup for controls that operate on the CONTENT
                      (page nav, zoom, filters). Decisions go in `actions`;
                      the head is for working the content, the footer is for
                      leaving. Keeping those apart is what stops every popup
                      inventing its own arrangement.
         body         string, or an Element, or a function(bodyEl)
         html         boolean - treat `body` as markup (opt-in, see rule 7)
         size         "sm" | "md" | "lg" | "xl"          (default "md")
         actions      [{ label, value, cls, primary, keep }]
         dismissible  boolean (default true) - Esc / backdrop / X
         dir          "ltr" | "rtl"  (default: the app's own direction)
         className    extra class on the panel, for per-popup layout only
         onOpen(handle), onClose(value)
         beforeClose(value, handle) optional async guard; return true to close

       handle: { el, panel, body, close(value), result }
         `result` is a Promise resolving to the chosen action's `value`
         (or null when dismissed), so `await Popup.open(...).result` reads the
         same way `await showModal(...)` always has.
       ------------------------------------------------------------------- */
    function open(spec) {
        const s = spec || {};
        const title = String(s.title || "").trim();
        if (!title) {
            // Rule 1. Loud on purpose: a nameless popup is a bug, and a silent
            // fallback title would hide it for ever.
            console.error("Popup.open: every popup needs a title", s);
        }
        const size = SIZES[s.size] ? s.size : "md";
        const dismissible = s.dismissible !== false;
        const actions = Array.isArray(s.actions) ? s.actions : [];
        const id = "pop" + (++seq);

        const prevFocus = document.activeElement;

        const bd = document.createElement("div");
        bd.className = "modal-backdrop popup-backdrop entering";
        bd.style.zIndex = String(2000 + stack.length * 10);

        const panel = document.createElement("div");
        panel.className = `modal popup popup-${size}${s.className ? " " + s.className : ""}`;
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-modal", "true");
        panel.setAttribute("aria-labelledby", id + "-title");
        if (s.dir) panel.setAttribute("dir", s.dir);

        /* Built with elements, not innerHTML. The structure is the same every
           time, so there is nothing to template - and it means the only
           innerHTML in this file is the opt-in `html: true` body, where the
           caller has taken responsibility for the markup (rule 7). */
        const el = (tag, cls, text) => {
            const n = document.createElement(tag);
            if (cls) n.className = cls;
            if (text != null) n.textContent = text;
            return n;
        };

        const head = el("div", "popup-head");
        if (s.icon) {
            const ic = el("span", "popup-icon");
            ic.innerHTML = s.icon;                 // caller-owned markup, like `html`
            head.appendChild(ic);
        }
        const heading = el("div", "popup-heading");
        const titleRow = el("div", "popup-title-row");
        const titleEl = el("div", "modal-title popup-title", title);
        titleEl.id = id + "-title";
        titleRow.appendChild(titleEl);
        if (s.badge) titleRow.appendChild(el("span", "popup-badge", s.badge));
        heading.appendChild(titleRow);
        if (s.subtitle) heading.appendChild(el("div", "popup-sub", s.subtitle));
        head.appendChild(heading);
        /* Header text that arrives LATER - a document's client and date after
           the bridge answers. Creates the line if the popup opened without a
           subtitle, so a caller never has to know which case it is in. */
        const setSubtitle = (text) => {
            let sub = heading.querySelector(".popup-sub");
            if (!sub) { sub = el("div", "popup-sub"); heading.appendChild(sub); }
            sub.textContent = text == null ? "" : String(text);
        };
        if (s.toolbar) {
            const tb = el("div", "popup-toolbar");
            if (typeof s.toolbar === "object" && s.toolbar.nodeType === 1) tb.appendChild(s.toolbar);
            else tb.innerHTML = String(s.toolbar);
            head.appendChild(tb);
        }
        if (dismissible) {
            const x = el("button", "popup-close", "×");
            x.setAttribute("type", "button");
            x.setAttribute("aria-label", "Close");
            head.appendChild(x);
        }
        panel.appendChild(head);

        const bodyEl = el("div", "modal-body popup-body");
        bodyEl.id = id + "-body";
        panel.appendChild(bodyEl);
        const actionsEl = actions.length
            ? panel.appendChild(el("div", "modal-actions popup-actions")) : null;
        if (typeof s.body === "function") s.body(bodyEl);
        // Duck-typed rather than `instanceof Element`: the same check works in
        // the browser and in the test harness's DOM double.
        else if (s.body && typeof s.body === "object" && s.body.nodeType === 1)
            bodyEl.appendChild(s.body);
        else if (s.html) bodyEl.innerHTML = s.body || "";
        else bodyEl.textContent = s.body == null ? "" : String(s.body);

        let settled = false;
        let resolveResult;
        const result = new Promise((r) => { resolveResult = r; });

        const finishClose = (value) => {
            if (settled) return;                 // one close path, always
            settled = true;
            document.removeEventListener("keydown", onKey, true);
            const i = stack.indexOf(handle);
            if (i >= 0) stack.splice(i, 1);
            const finish = () => {
                bd.remove();
                unlockScroll();
                try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) {}
                if (typeof s.onClose === "function") { try { s.onClose(value); } catch (e) {} }
                resolveResult(value);
            };
            if (reducedMotion()) { finish(); return; }
            bd.classList.add("entering");
            setTimeout(finish, 180);
        };
        let guarding = false;
        const close = (value) => {
            if (settled || guarding) return;
            if (typeof s.beforeClose !== "function") return finishClose(value);
            guarding = true;
            try {
                Promise.resolve(s.beforeClose(value, handle)).then(allowed => {
                    guarding = false;
                    if (allowed === true) finishClose(value);
                }, error => {
                    guarding = false;
                    console.error("Popup close guard failed", error);
                });
            } catch (error) {
                guarding = false;
                console.error("Popup close guard failed", error);
            }
        };

        /* The value Esc and the backdrop resolve to: an explicit "cancel"
           action when the popup has one, else null. Callers already branch on
           both, and inventing a third answer would break them. */
        const cancelValue = () => {
            const c = actions.find((a) => a && a.value === "cancel");
            return c ? c.value : null;
        };

        if (actionsEl) {
            const act = actionsEl;
            actions.forEach((a) => {
                const btn = document.createElement("button");
                btn.className = a.cls || "ghost";
                btn.textContent = a.label;
                // Addressable by VALUE, so callers that must enable, disable or
                // relabel one button while the popup is open do it the same way
                // in every popup instead of inventing a selector each time.
                if (a.value != null) btn.setAttribute("data-value", String(a.value));
                if (a.title) btn.title = a.title;
                btn.onclick = () => {
                    if (typeof a.onClick === "function") {
                        try { a.onClick(handle); } catch (e) {}
                    }
                    if (!a.keep) close(a.value);     // `keep` = act without closing
                };
                act.appendChild(btn);
            });
        }

        const closeBtn = panel.querySelector(".popup-close");
        if (closeBtn) closeBtn.onclick = () => close(cancelValue());

        // Rule 2 - the dim background is a close affordance, but only when the
        // click STARTED there: a drag that ends on the backdrop (selecting text
        // in the body, dragging a slider) must not dismiss the popup.
        let downOnBackdrop = false;
        bd.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === bd; });
        bd.addEventListener("click", (e) => {
            if (dismissible && downOnBackdrop && e.target === bd) close(cancelValue());
            downOnBackdrop = false;
        });

        // Rules 2 + 3 - Esc closes the INNERMOST popup; Tab is trapped.
        const onKey = (e) => {
            if (stack[stack.length - 1] !== handle) return;     // not the top one
            if (e.key === "Escape" && dismissible) {
                e.preventDefault();
                e.stopPropagation();
                close(cancelValue());
                return;
            }
            if (e.key !== "Tab") return;
            const items = focusables(panel);
            if (!items.length) { e.preventDefault(); return; }
            const first = items[0], last = items[items.length - 1];
            const active = document.activeElement;
            if (e.shiftKey && (active === first || !panel.contains(active))) {
                e.preventDefault(); last.focus();
            } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
                e.preventDefault(); first.focus();
            }
        };

        bd.appendChild(panel);
        document.body.appendChild(bd);

        /* `closed` is for the common async shape: open a popup, await a
           bridge call, then paint the result. Painting into a popup the user
           already dismissed is how a stale reply lands on the next document. */
        const handle = { el: bd, panel, body: bodyEl, head, close, result, spec: s,
                         setSubtitle,
                         get closed() { return settled; } };
        stack.push(handle);
        lockScroll();
        document.addEventListener("keydown", onKey, true);

        if (reducedMotion()) bd.classList.remove("entering");
        else requestAnimationFrame(() => requestAnimationFrame(
            () => bd.classList.remove("entering")));

        /* Rule 6 - focus the primary action; but if the body opens with a
           field, the field is what the user came to type in. */
        setTimeout(() => {
            const field = bodyEl.querySelector("input:not([type=hidden]), textarea, select");
            const btns = actionsEl
                ? Array.from(actionsEl.querySelectorAll("button")) : [];
            const primary = btns.find((b) => b.classList.contains("primary"))
                         || btns[btns.length - 1];
            const target = field || primary || closeBtn;
            if (target) { try { target.focus(); } catch (e) {} }
        }, 30);

        if (typeof s.onOpen === "function") { try { s.onOpen(handle); } catch (e) {} }
        return handle;
    }

    /* ---------------------------------------------------------------------
       `mount` - the contract for a popup whose CONTENT is authored in HTML.

       Five dialogs are written as markup in index.html because their innards
       are wired by id from their own modules (signing, bulk paste, the data
       manager's editor and client prompt, the Find-pictures progress). Moving
       that markup into JS would rewrite three features at once for no gain, so
       instead the shared BEHAVIOUR is attached to the element they already
       have: Esc, the dim background, the focus trap, the scroll lock, the
       stack, and focus restored to whatever opened it.

       The module keeps its own show/hide call sites - it just routes them
       through here, so there is still exactly one implementation of the rules.
       ------------------------------------------------------------------- */
    function mount(el, opts) {
        if (!el) return null;
        const o = opts || {};
        const dismissible = o.dismissible !== false;
        const prevFocus = document.activeElement;
        let settled = false;

        const close = (value) => {
            if (settled) return;
            settled = true;
            document.removeEventListener("keydown", onKey, true);
            const i = stack.indexOf(handle);
            if (i >= 0) stack.splice(i, 1);
            el.hidden = true;
            unlockScroll();
            try { if (prevFocus && prevFocus.focus) prevFocus.focus(); } catch (e) {}
            if (typeof o.onClose === "function") { try { o.onClose(value); } catch (e) {} }
        };

        const panel = el.querySelector(".modal, .popup, [role=dialog]") || el;
        const onKey = (e) => {
            if (stack[stack.length - 1] !== handle) return;
            if (e.key === "Escape" && dismissible) {
                e.preventDefault(); e.stopPropagation(); close(null); return;
            }
            if (e.key !== "Tab") return;
            const items = focusables(panel);
            if (!items.length) { e.preventDefault(); return; }
            const first = items[0], last = items[items.length - 1];
            const active = document.activeElement;
            if (e.shiftKey && (active === first || !panel.contains(active))) {
                e.preventDefault(); last.focus();
            } else if (!e.shiftKey && (active === last || !panel.contains(active))) {
                e.preventDefault(); first.focus();
            }
        };

        /* The X is re-pointed on EVERY mount, not once: `close` belongs to this
           mount, and a listener captured on the first open would be closing a
           handle that settled long ago - the X would silently stop working the
           second time the dialog was opened. `onclick` overwrites rather than
           stacking, which is the point. */
        el.querySelectorAll("[data-popup-close]").forEach((b) => {
            b.onclick = () => close(null);
        });

        if (!el._popupWired) {
            el._popupWired = true;
            let downOnBackdrop = false;
            el.addEventListener("mousedown", (e) => { downOnBackdrop = e.target === el; });
            el.addEventListener("click", (e) => {
                if (downOnBackdrop && e.target === el && !settled && dismissible) close(null);
                downOnBackdrop = false;
            });
        }

        el.hidden = false;
        el.style.zIndex = String(2000 + stack.length * 10);
        const handle = { el, panel, body: panel, close, mounted: true,
                         get closed() { return settled; } };
        stack.push(handle);
        lockScroll();
        document.addEventListener("keydown", onKey, true);
        setTimeout(() => {
            // Same rule as `open`: the field is what the user came to type in.
            const f = panel.querySelector("input:not([type=hidden]), textarea, select")
                   || focusables(panel)[0];
            if (f) { try { f.focus(); } catch (e) {} }
        }, 30);
        return handle;
    }

    /* Replace a popup's body - the async shape above. Text by default, the
       same opt-in as `open`, and a no-op once the popup is gone. */
    function setBody(handle, content, opts) {
        if (!handle || handle.closed) return false;
        const b = handle.body;
        if (typeof content === "function") { b.textContent = ""; content(b); }
        else if (content && typeof content === "object" && content.nodeType === 1) {
            b.textContent = ""; b.appendChild(content);
        } else if (opts && opts.html) b.innerHTML = content == null ? "" : String(content);
        else b.textContent = content == null ? "" : String(content);
        return true;
    }

    /* A popup that only tells the user something. One way out, still a real
       popup: same box, same behaviour, same look. */
    function alert(title, body, opts) {
        return open({
            title, body, size: "sm",
            actions: [{ label: (opts && opts.ok) || "OK", value: true, cls: "primary" }],
            ...(opts || {}),
        }).result;
    }

    /* Ask a yes/no. The DESTRUCTIVE answer says what it does - "Delete", not
       "Yes" - because a word survives being unable to see the button colour. */
    function confirm(title, body, opts) {
        const o = opts || {};
        return open({
            title, body, size: o.size || "sm", html: o.html,
            actions: [
                { label: o.cancel || "Cancel", value: "cancel", cls: "ghost" },
                { label: o.confirm || "OK", value: "ok", cls: o.cls || "primary" },
            ],
            ...(o.spec || {}),
        }).result.then((v) => v === "ok");
    }

    /* The top popup, for callers that need to close one they did not open. */
    function top() { return stack[stack.length - 1] || null; }
    function closeTop(value) { const t = top(); if (t) t.close(value); return !!t; }
    function count() { return stack.length; }

    window.Popup = { open, mount, alert, confirm, setBody, top, closeTop, count, SIZES };
})();
