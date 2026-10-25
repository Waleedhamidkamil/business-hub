/* ============================================================
   unsaved_guard.js - ONE rule for "do you have unsaved work?"
   ============================================================

   Waleed, 2026-10-01: he opened an existing purchase order from the Open
   panel, changed nothing, pressed New, and was asked whether to save. The
   prompt is supposed to appear only after a real edit, and he asked for the
   rule to live in one place instead of being re-written per editor.

   It had been re-written per editor, and PO's copy was the broken one:

       onNewClicked   if (filledRows().length === 0) -> straight to New
       quoteNew/dnNew/invNew   if (filled > 0 && state.dirty) -> prompt
       poConfirmLeave          if (dirty && filled > 0) -> prompt

   PO's New asked about CONTENT and never about EDITS, so every order opened
   for a look triggered it. Four copies of a two-clause rule is how one of
   them ends up missing a clause, so there is now exactly one:

       unsaved work  ==  the editor is dirty  AND  it has at least one
                         filled row

   BOTH halves are load bearing:
     * `dirty` alone would fire on a document you only opened (any render
       that touches a flag makes it true) - the bug above,
     * `filled` alone would fire on an untouched document that simply has
       rows, which is also the bug above,
     * dropping `filled` entirely would nag about a blank editor whose date
       picker you happened to touch.

   Each editor REGISTERS itself, because `filledRows` / `quoteState` and the
   rest are module-local to app.js / quote.js / dn.js / inv.js. The registry
   is also what `activeDirtyEditor()` reads, so the profile switcher and the
   duplicate flow ask the same question the New button asks.

   Loaded BEFORE app.js; nothing here runs until an editor calls in.
*/
(function () {
    "use strict";

    // kind -> { noun, label, dirty(), filled(), save(opts) }
    const EDITORS = {};

    // Per-editor prompt copy. A purchase order is an Arabic document and its
    // editor speaks Arabic; the other three are English. That split is the
    // app's convention, not an oversight ([[06-FRONTEND-EDITORS]]).
    const COPY = {
        po: {
            title: "بيانات غير محفوظة",
            newBody: "يوجد طلب قيد التحرير لم يتم حفظه. ماذا تريد أن تفعل؟",
            leaveBody: "يوجد طلب لم يُحفَظ. هل تريد المغادرة بدون حفظ؟",
            cancel: "إلغاء", discard: "تجاهل وجديد", save: "حفظ ثم جديد",
            leave: "مغادرة بدون حفظ",
        },
        _: {
            cancel: "Cancel", discard: "Discard & New", save: "Save & New",
            leave: "Leave without saving",
        },
    };

    function copyFor(kind) {
        const noun = (EDITORS[kind] && EDITORS[kind].noun) || "document";
        const base = COPY[kind] || COPY._;
        return Object.assign({
            title: "Unsaved " + noun,
            newBody: "You have an unsaved " + noun + ". What would you like to do?",
            leaveBody: "You have an unsaved " + noun + ". Leave without saving?",
        }, base);
    }

    /* ---- THE RULE. Everything else in this file is a prompt around it. ---- */
    function hasUnsavedWork(kind) {
        const e = EDITORS[kind];
        if (!e) return false;
        let dirty, filled;
        try { dirty = !!e.dirty(); } catch (err) { return false; }
        if (!dirty) return false;
        try { filled = e.filled(); } catch (err) { return false; }
        return !!(filled && filled.length > 0);
    }

    function modal(options) {
        // showModal lives in app.js and is defined by the time anything here
        // is clicked. If it somehow is not, do NOT swallow the question -
        // block the destructive path rather than discard the user's work.
        if (typeof window.showModal !== "function") return Promise.resolve(null);
        return window.showModal(options);
    }

    const guard = {
        register(kind, impl) { EDITORS[kind] = impl; },

        /** True when `kind` holds edits the user has not saved. */
        isUnsaved: hasUnsavedWork,

        /** Which editor (if any) is holding unsaved work. */
        dirtyEditor() {
            const kind = Object.keys(EDITORS).find(hasUnsavedWork);
            if (!kind) return null;
            const e = EDITORS[kind];
            // `stay: true` - these callers (profile switch, duplicate) keep the
            // user where they are, so the editor must not reset itself.
            return {
                kind: e.label || e.noun || kind,
                key: kind,
                save: () => e.save({ stay: true, skipConfirm: true }),
            };
        },

        /**
         * The New button. Resolves true when it is safe to start a new
         * document (nothing unsaved, the user discarded, or the save worked).
         */
        async confirmNew(kind) {
            if (!hasUnsavedWork(kind)) return true;
            const c = copyFor(kind);
            const choice = await modal({
                title: c.title, body: c.newBody,
                buttons: [
                    { label: c.cancel,  value: "cancel",  cls: "ghost" },
                    { label: c.discard, value: "discard", cls: "secondary" },
                    { label: c.save,    value: "save",    cls: "primary" },
                ],
            });
            if (choice === "cancel" || choice == null) return false;
            // No `stay` on purpose: this is the pre-existing "Save & New"
            // behaviour of each editor, and for PO that means an export-save
            // which runs his "after export" action on the PDF.
            if (choice === "save") return !!(await EDITORS[kind].save({ skipConfirm: true }));
            return true;   // discard
        },

        /** The Home button. Resolves true when it is safe to leave. */
        async confirmLeave(kind) {
            if (!hasUnsavedWork(kind)) return true;
            const c = copyFor(kind);
            const choice = await modal({
                title: c.title, body: c.leaveBody,
                buttons: [
                    { label: c.cancel, value: "cancel", cls: "ghost" },
                    { label: c.leave,  value: "ok",     cls: "warm-red" },
                ],
            });
            return choice === "ok";
        },
    };

    window.unsavedGuard = guard;
})();
