// The gate (2026-10-02, second design) - two doors that close over the app
// and slide apart again.
//
// Waleed's design: the brand on the left door, the company logo in a round
// seal on the seam (half on each door), and on the right a greeting, a
// loading bar with the step in progress written under it, and the day's
// brief. Three moments use it:
//
//   start-up  since 2026-10-20 Windows draws a launch CARD, not these doors,
//             before this page exists (backend/launch_gate.py). This file
//             tells it when the dashboard has painted - Gate.ready() ->
//             Api.gate_ready() - and holds the page's own start-up
//             animations until the card has gone.
//   switch    the doors close in the OLD company's colours, the brand changes
//             while they are shut, the page reloads under them (the reload
//             paints the same closed doors from sessionStorage, see the inline
//             script after #gate in index.html), and they slide apart on the
//             new company's dashboard.
//   lock      Ctrl+L or the padlock: the doors close over prices and profits;
//             a click or any key opens them.
//
// The bar only moves for stages that really finished; between them it eases
// towards the next mark without reaching it. The only timer is the safety
// one that opens the doors if "ready" never arrives.
(function () {
    "use strict";

    const KEY = "bh-gate";
    // HOLD_MS: the pause between "ready" and the doors moving, in which the bar
    // and the ring round the seal reach the end. Same as launch_gate.HOLD_MS.
    const OPEN_MS = 560, CLOSE_MS = 420, HOLD_MS = 200, SAFETY_MS = 20000;
    const SWITCH_STEPS = ["Opening company data", "Loading the interface", "Preparing your dashboard"];
    const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
    const MONTHS = ["January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December"];

    const root = document.documentElement;
    const $ = (id) => document.getElementById(id);
    const reduce = () => window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    const later = (ms) => new Promise(r => setTimeout(r, ms));

    // The dashboard greets by the name written in index.html; app.js later
    // blanks that element for greetings without a name, so read it now.
    const nameEl = $("dashUser");
    const USER = (nameEl && nameEl.textContent.trim()) || "Waleed";

    let mode = null;          // null | "switch" | "lock"
    let stage = 0;            // finished steps of SWITCH_STEPS
    let readySent = false;
    let safety = null;
    let keyTrap = null;
    let settleResolve;
    const settled = new Promise(r => { settleResolve = r; });

    function esc(s) {
        return String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    }
    function greeting(h) { return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; }
    // Same wording as the launch gate: "Friday 2 October 2026".
    function dateText(d) { return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`; }

    function stepLabel(n) { return n >= SWITCH_STEPS.length ? "Ready" : SWITCH_STEPS[n]; }
    function countLabel(n) { return n >= SWITCH_STEPS.length ? "Done" : `Step ${n + 1} of ${SWITCH_STEPS.length}`; }
    function barMark(n) { return Math.min(1, n / SWITCH_STEPS.length); }

    // `shown` renders the words already in place (the reload's first frame).
    function welcomeHTML(sub, n, shown) {
        const now = new Date();
        const c = shown ? "ga in" : "ga";
        return `<div class="gate-hello ${c}" style="--d:0ms">${esc(greeting(now.getHours()))}, ${esc(USER)}</div>`
            + `<div class="gate-sub ${c}" style="--d:70ms">${esc(dateText(now))}  ·  ${esc(sub)}</div>`
            + `<div class="gate-progress ${c}" style="--d:140ms">`
            + `<div class="gate-bar"><div class="gate-fill" id="gateFill" style="transform:scaleX(${barMark(n)})"></div></div>`
            + `<div class="gate-steprow"><div class="gate-step" id="gateStep"><span>${esc(stepLabel(n))}</span></div>`
            + `<div class="gate-count" id="gateCount">${esc(countLabel(n))}</div></div></div>`
            + `<div class="gate-brief" id="gateBrief" hidden></div>`;
    }
    function lockHTML() {
        return `<div class="gate-hello ga" style="--d:0ms">Locked</div>`
            + `<div class="gate-sub ga" style="--d:70ms">Prices and profits are hidden while you are away.</div>`
            + `<div class="gate-hint ga" style="--d:140ms">Click anywhere or press any key to open.</div>`;
    }

    // Reveal every .ga in the right door on the next frame, so the transition runs.
    function revealWords() {
        const box = $("gateIn");
        if (!box) return;
        requestAnimationFrame(() => requestAnimationFrame(() => {
            box.querySelectorAll(".ga:not(.in)").forEach(el => el.classList.add("in"));
        }));
    }

    function setBrand(brand) {
        if (!brand) return;
        root.style.setProperty("--gate-accent", brand.accent || "#1F3864");
        root.style.setProperty("--gate-door-accent", brand.accent || "#1F3864");
        setMark(brand);
    }
    // The logo and the company name - without the colour, which a switch
    // changes separately, under the wash.
    function setMark(brand) {
        document.querySelectorAll("#gate .gate-seal img").forEach(img => { img.src = brand.logo || ""; });
        const co = $("gateCo");
        if (co) co.textContent = brand.label || "";
    }

    // ---- the brand changes while the doors are shut ------------------------
    // The new colour spreads out from the seam over the brand door; under it,
    // the old logo and name blur away and the new ones sharpen into place.
    // Only when the wash covers the door does the real colour change.
    async function swapBrand(to) {
        const g = $("gate");
        const door = document.querySelector("#gate .gate-door.l");
        if (!g || reduce()) { setBrand(to); return; }
        // the door keeps its colour until the wash has covered it
        root.style.setProperty("--gate-door-accent", getComputedStyleSafe("--gate-accent"));
        let wash = null;
        if (door && door.insertBefore) {
            wash = document.createElement("div");
            wash.className = "gate-wash";
            wash.style.setProperty("--wash-accent", to.accent || "#1F3864");
            door.insertBefore(wash, door.firstChild);
            requestAnimationFrame(() => requestAnimationFrame(() => wash.classList.add("is-on")));
        }
        await later(140);
        g.classList.add("is-swap-out");
        await later(170);
        setMark(to);
        root.style.setProperty("--gate-accent", to.accent || "#1F3864");   // ring, bar, seal - with the logo
        g.classList.add("is-swap-in");
        g.classList.remove("is-swap-out");
        void g.offsetWidth;                       // the new mark starts blurred...
        g.classList.remove("is-swap-in");          // ...and sharpens
        await later(360);
        setBrand(to);
        if (wash) wash.remove();
    }

    function getComputedStyleSafe(name) {
        try { return getComputedStyle(root).getPropertyValue(name).trim() || "#1F3864"; }
        catch (e) { return (root.style.props && root.style.props[name]) || "#1F3864"; }
    }

    // ---- depth as the doors part ------------------------------------------
    // The app behind settles up from 98.5% while the doors slide. `delay` lets
    // the start-up doors (drawn by Windows) hold first.
    function revealApp(delay) {
        if (reduce()) return;
        root.classList.add("gate-reveal");
        void root.offsetWidth;
        setTimeout(() => {
            root.classList.add("gate-revealing");
            root.classList.remove("gate-reveal");
            setTimeout(() => root.classList.remove("gate-revealing"), OPEN_MS + 80);
        }, Math.max(0, delay || 0));
    }

    // The bar: to the mark of the steps really finished, then a slow ease
    // towards - never onto - the next one.
    let creep = null;
    // The ring round the seal (2026-10-07) is the same progress, one round:
    // --gate-sweep on #gate, moved with the bar's own easing.
    function setBar(n) {
        const fill = $("gateFill");
        clearTimeout(creep);
        const mark = barMark(n);
        const go = (to, ms, curve) => {
            if (fill) {
                fill.style.transition = `transform ${ms}ms ${curve}`;
                fill.style.transform = `scaleX(${to})`;
            }
            setSweep(to, ms, curve);
        };
        go(mark, 450, "cubic-bezier(0.23, 1, 0.32, 1)");
        if (n < SWITCH_STEPS.length) {
            const toward = mark + 0.6 * (1 / SWITCH_STEPS.length);
            creep = setTimeout(() => go(toward, 6000, "cubic-bezier(0.1, 0.6, 0.3, 1)"), 460);
        }
    }
    function setSweep(fraction, ms, curve) {
        const g = $("gate");
        if (!g || !g.style || !g.style.setProperty) return;
        g.style.transition = ms ? `--gate-sweep ${ms}ms ${curve}` : "none";
        g.style.setProperty("--gate-sweep", `${Math.round(Math.max(0, Math.min(1, fraction)) * 3600) / 10}deg`);
    }

    // The step under the bar: the old words ease up and out, the new ones in.
    function setStepText(text) {
        const box = $("gateStep");
        if (!box) return;
        const old = box.querySelector("span:not(.is-out)");
        if (old && old.textContent === text) return;
        if (old) {
            old.classList.add("is-out");
            setTimeout(() => old.remove(), 220);
        }
        const next = document.createElement("span");
        next.className = "is-next";
        next.textContent = text;
        box.appendChild(next);
        requestAnimationFrame(() => requestAnimationFrame(() => next.classList.remove("is-next")));
    }

    function setStage(n) {
        if (n <= stage) return;
        stage = Math.min(n, SWITCH_STEPS.length);
        setBar(stage);
        setStepText(stepLabel(stage));
        const count = $("gateCount");
        if (count) count.textContent = countLabel(stage);
    }

    function showBrief(data) {
        const box = $("gateBrief");
        if (!box || !data || !data.ok) return;
        const lines = (data.lines && data.lines.length) ? data.lines
            : [{ value: "All clear", text: "nothing is waiting for you", detail: "" }];
        box.innerHTML = `<div class="gate-brief-head ga" style="--d:0ms">TODAY</div>` + lines.slice(0, 3).map((l, i) =>
            `<div class="gate-line ga" style="--d:${70 * (i + 1)}ms"><b>${esc(l.value)}</b><span>${esc(l.text)}</span>`
            + (l.detail ? `<small>${esc(l.detail)}</small>` : "") + `</div>`).join("");
        box.hidden = false;
        revealWords();
    }

    // Move the doors. Resolves when they have stopped.
    function move(closing) {
        return new Promise((resolve) => {
            const g = $("gate");
            if (!g) { resolve(); return; }
            g.classList.add("is-moving");
            g.classList.toggle("is-closing", closing);
            void g.offsetWidth;                      // start from where they are now
            root.classList.toggle("gate-closed", closing);
            g.setAttribute("aria-hidden", closing ? "false" : "true");
        if (!closing) revealApp(0);
            const ms = reduce() ? 220 : (closing ? CLOSE_MS : OPEN_MS);
            setTimeout(() => { g.classList.remove("is-moving", "is-closing"); resolve(); }, ms + 40);
        });
    }

    function trapKeys(onKey) {
        releaseKeys();
        keyTrap = (e) => {
            e.preventDefault();
            e.stopImmediatePropagation();
            if (onKey && !["Control", "Shift", "Alt", "Meta"].includes(e.key)) onKey();
        };
        document.addEventListener("keydown", keyTrap, true);
    }
    function releaseKeys() {
        if (keyTrap) document.removeEventListener("keydown", keyTrap, true);
        keyTrap = null;
    }

    async function openDoors() {
        clearTimeout(safety);
        releaseKeys();
        const g = $("gate");
        if (g) { g.classList.remove("is-locked"); g.onclick = null; }
        await move(false);
        setSweep(0, 0);                  // empty again for next time, while nobody sees it
        mode = null;
    }

    function api() { return window.pywebview && window.pywebview.api; }

    // ---- after a switch reload ----------------------------------------------
    // index.html's inline script has already painted the closed doors.
    function resumeAfterReload() {
        let saved = null;
        try { saved = JSON.parse(sessionStorage.getItem(KEY) || "null"); sessionStorage.removeItem(KEY); }
        catch (e) { saved = null; }
        if (!saved || !root.classList.contains("gate-closed")) return;
        mode = "switch";
        stage = Number(saved.stage) || 1;
        trapKeys(null);
        safety = setTimeout(openDoors, SAFETY_MS);
        setSweep(barMark(stage), 0);
        const g = $("gate");
        if (g) requestAnimationFrame(() => requestAnimationFrame(() => g.classList.remove("no-anim")));
    }

    // bootstrap() has started: the interface is loaded.
    let bootAt = null;
    function pageLoaded() {
        bootAt = performance.now();
        if (mode !== "switch") return;
        setStage(2);
        const a = api();
        if (a && a.gate_brief) a.gate_brief().then(showBrief).catch(() => {});
    }

    // The dashboard has rendered. Wait one paint, then open whichever gate is
    // shut. `settled()` resolves once the doors are apart (or there are none),
    // so the dashboard's own animations do not compete with them.
    function ready() {
        const painted = performance.now();
        requestAnimationFrame(() => requestAnimationFrame(async () => {
            let launchSettle = 0;
            if (!readySent) {
                readySent = true;
                const a = api();
                const nav = (performance.getEntriesByType && performance.getEntriesByType("navigation")[0]) || {};
                const marks = { origin: performance.timeOrigin, nav_start: 0, nav_response: nav.responseEnd,
                    nav_interactive: nav.domInteractive, nav_dcl: nav.domContentLoadedEventEnd,
                    booted: bootAt, painted, framed: performance.now() };
                if (a && a.gate_ready) {
                    try {
                        const r = await a.gate_ready(marks);
                        if (r && r.gate) launchSettle = Number(r.settle_ms) || 0;
                    } catch (e) { /* no launch gate */ }
                    // Windows' doors part HOLD_MS after "ready": settle the app in step.
                    if (launchSettle && mode !== "switch") revealApp(HOLD_MS);
                }
            }
            if (mode === "switch") {
                setStage(3);
                await later(HOLD_MS);
                await openDoors();
            } else if (launchSettle) {
                await later(launchSettle);
            }
            settleResolve();
        }));
    }

    // ---- company switch ------------------------------------------------------
    // `doSwitch` performs the switch and resolves to the API result. Returns
    // that result; when it asks for a reload, the doors stay shut for it.
    async function switchTo(from, to, doSwitch) {
        if (mode) return doSwitch();
        mode = "switch";
        stage = 0;
        setSweep(0, 0);
        const sub = `Switching to ${to.label}`;
        setBrand(from);
        const box = $("gateIn");
        box.className = "gate-in";
        box.innerHTML = welcomeHTML(sub, 0, false);
        trapKeys(null);
        await move(true);
        revealWords();
        let result;
        try {
            result = await doSwitch();
        } catch (error) {
            await openDoors();
            throw error;
        }
        if (!result || !result.ok || !result.reload) {
            await openDoors();
            return result;
        }
        setStage(1);
        await swapBrand(to);
        await later(120);
        try {
            sessionStorage.setItem(KEY, JSON.stringify({
                mode: "switch", stage: 1, accent: to.accent, logo: to.logo, label: to.label,
                // Rebuilt from state, not read back off the page, so the reload
                // paints exactly what has really finished, words already in place.
                html: welcomeHTML(sub, 1, true),
            }));
        } catch (e) { /* no storage: the reload simply opens without the gate */ }
        return result;
    }

    // ---- lock ----------------------------------------------------------------
    async function lock(brand) {
        if (mode) return;
        mode = "lock";
        setBrand(brand);
        const box = $("gateIn");
        box.className = "gate-in is-lock";
        box.innerHTML = lockHTML();
        const g = $("gate");
        g.classList.add("is-locked");
        let opening = false;
        const unlock = () => { if (opening) return; opening = true; openDoors(); };
        trapKeys(null);
        await move(true);
        revealWords();
        // Only listen once the doors are shut, so the click or key that
        // locked never unlocks it.
        trapKeys(unlock);
        g.onclick = unlock;
    }

    resumeAfterReload();

    window.Gate = {
        ready, pageLoaded, switchTo, lock,
        settled: () => settled,
        isBusy: () => mode !== null,
        _state: () => ({ mode, stage }),
    };
})();
