/* The gate (2026-10-02, second design): lock, company switch, start-up hand-off.

   Waleed chose a two-door gate - brand left, the company logo in a seal on the
   seam, a greeting, a loading bar with the step under it, and the day's brief -
   and asked for it on company switching and lock too. This file drives the
   REAL static/gate.js against a small fake DOM and fake clock:

     - Ctrl+L / lock closes the doors; the key that locked does not unlock;
       the next key or click opens them.
     - a switch closes the doors, and only a successful switch that reloads
       leaves them shut (with the NEW company written for the reload to paint);
       a refused or crashing switch opens them again.
     - after the reload the doors start shut, tick the real stages, show the
       brief, and open when the dashboard is ready - or after the safety limit
       if it never is.
     - the launch gate is told "ready" exactly once, and the dashboard's own
       animations wait until its doors are apart (they used to queue the
       "ready" call ~0.3 s behind fifteen icon files).

   Part 2 checks the wiring in index.html / app.js / api.py / app.py, because a
   correct module nobody calls does nothing.

   No jsdom in this repo, on purpose: the fake below implements only what
   gate.js touches.
*/
const fs = require("fs");
const vm = require("vm");

let failures = 0;
async function check(name, fn) {
  try { await fn(); console.log("  ok   " + name); }
  catch (e) { failures++; console.log("  FAIL " + name + "\n       " + e.message); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg); }

/* ---------------- fake DOM + clock ---------------- */
function makeWorld(opts = {}) {
  const byId = {};
  const listeners = [];
  let now = 0;
  const timers = [];
  const store = Object.assign({}, opts.session || {});

  function classList() {
    const set = new Set();
    return {
      add: (...c) => c.forEach(x => set.add(x)),
      remove: (...c) => c.forEach(x => set.delete(x)),
      toggle: (c, on) => { const want = on === undefined ? !set.has(c) : !!on; want ? set.add(c) : set.delete(c); return want; },
      contains: (c) => set.has(c),
    };
  }
  function el(id) {
    const e = {
      id, hidden: false, attrs: {}, classList: classList(), textContent: "", src: "",
      offsetWidth: 100, onclick: null, parentElement: null, _html: "", style: {}, className: "",
      children: [],
      appendChild(c) { this.children.push(c); c.parentElement = this; return c; },
      querySelector(sel) {
        if (sel === "span:not(.is-out)") return this.children.find(c => !c.classList.contains("is-out")) || null;
        return null;
      },
      querySelectorAll() { return []; },
      remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(c => c !== this); },
      setAttribute(k, v) { this.attrs[k] = String(v); },
      getAttribute(k) { return this.attrs[k]; },
      get innerHTML() { return this._html; },
      set innerHTML(v) {
        this._html = String(v);
        // register elements that this markup creates
        const re = /<(\w+)([^>]*)\sid="([^"]+)"([^>]*)>/g;
        let m;
        while ((m = re.exec(this._html))) {
          const child = el(m[3]);
          child.hidden = /\shidden(\s|$|>)/.test(" " + m[2] + " " + m[4] + " ");
          byId[m[3]] = child;
        }
      },
    };
    return e;
  }
  ["gate", "gateLogo", "gateCo", "gateIn", "dashUser"].forEach(id => { byId[id] = el(id); });
  byId.gate.style = { props: {}, setProperty(k, v) { this.props[k] = v; } };
  byId.gateLogo.parentElement = el("plate");
  byId.dashUser.textContent = "Waleed";
  const brand = el("brand");
  const seals = [el("sealL"), el("sealR")];
  const rootEl = el("html");
  rootEl.style = { props: {}, setProperty(k, v) { this.props[k] = v; } };
  if (opts.closed) rootEl.classList.add("gate-closed");

  const calls = { gate_ready: 0, gate_brief: 0 };
  const win = {
    pywebview: { api: {
      gate_ready: (marks) => { calls.gate_ready++; calls.marks = marks;
        return Promise.resolve(opts.launchGate ? { ok: true, gate: true, settle_ms: 680 } : { ok: true }); },
      gate_brief: () => { calls.gate_brief++; return Promise.resolve({ ok: true, lines: [
        { value: "USD 17,697", text: "waiting for payment", detail: "11 invoices, oldest 67 days" }] }); },
    } },
  };
  const ctx = {
    window: win,
    document: {
      documentElement: rootEl,
      getElementById: (id) => byId[id] || null,
      querySelector: (sel) => sel === "#gate .gate-brand" ? brand : null,
      querySelectorAll: (sel) => sel === "#gate .gate-seal img" ? seals : [],
      createElement: () => el(null),
      addEventListener: (type, fn, capture) => listeners.push({ type, fn, capture }),
      removeEventListener: (type, fn) => {
        const i = listeners.findIndex(l => l.type === type && l.fn === fn);
        if (i >= 0) listeners.splice(i, 1);
      },
    },
    sessionStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    setTimeout: (fn, ms) => { const t = { at: now + (ms || 0), fn }; timers.push(t); return t; },
    clearTimeout: (t) => { const i = timers.indexOf(t); if (i >= 0) timers.splice(i, 1); },
    requestAnimationFrame: (fn) => { const t = { at: now + 16, fn: () => fn(now) }; timers.push(t); return t; },
    performance: { now: () => now, timeOrigin: 1e12 },
    matchMedia: () => ({ matches: false }),
    Promise, JSON, String, Number, Math, Array, Object, console,
  };
  win.matchMedia = ctx.matchMedia;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync("static/gate.js", "utf8"), ctx);

  async function flush() { for (let i = 0; i < 20; i++) await Promise.resolve(); }
  async function advance(ms) {
    const end = now + ms;
    for (;;) {
      await flush();
      timers.sort((a, b) => a.at - b.at);
      const t = timers[0];
      if (!t || t.at > end) break;
      timers.shift();
      now = t.at;
      t.fn();
    }
    now = end;
    await flush();
  }
  function key(k) {
    const ev = { key: k, defaultPrevented: false, stopped: false,
      preventDefault() { this.defaultPrevented = true; }, stopImmediatePropagation() { this.stopped = true; } };
    listeners.filter(l => l.type === "keydown").forEach(l => l.fn(ev));
    return ev;
  }
  return {
    Gate: win.Gate, byId, root: rootEl, brand, seals, store, calls, advance, key, listeners,
    closed: () => rootEl.classList.contains("gate-closed"),
  };
}

const ABRAJ = { id: "abraj", label: "Abraj Albadr", accent: "#1F3864", logo: "data:image/png;base64,AA" };
const ESH = { id: "eshraqat", label: "Eshraqat Albadr", accent: "#861432", logo: "data:image/png;base64,BB" };

(async () => {
  console.log("gate behaviour");

  await check("lock closes the doors; the locking key does not unlock; the next key does", async () => {
    const w = makeWorld();
    const p = w.Gate.lock(ABRAJ);
    const early = w.key("l");                         // still part of the Ctrl+L press
    assert(early.defaultPrevented, "keys must be swallowed while the doors close");
    await w.advance(500);
    await p;
    assert(w.closed(), "doors are not shut after lock");
    assert(w.Gate._state().mode === "lock", "mode should be lock");
    assert(w.root.style.props["--gate-accent"] === "#1F3864", "left door not in the company colour");
    assert(w.seals.every(s => s.src === ABRAJ.logo), "both halves of the seal must show the company logo");
    assert(/Locked/.test(w.byId.gateIn.innerHTML), "right door should say Locked");
    const ev = w.key("a");
    assert(ev.defaultPrevented && ev.stopped, "a key while locked must not reach the app");
    await w.advance(700);
    assert(!w.closed(), "a key did not open the doors");
    assert(w.Gate._state().mode === null, "mode not cleared after unlock");
    assert(w.listeners.filter(l => l.type === "keydown").length === 0, "key trap left behind after unlock");
  });

  await check("a click on the locked gate opens it", async () => {
    const w = makeWorld();
    await Promise.all([w.Gate.lock(ABRAJ), w.advance(500)]);
    assert(typeof w.byId.gate.onclick === "function", "locked gate has no click handler");
    w.byId.gate.onclick();
    await w.advance(700);
    assert(!w.closed(), "click did not open the doors");
  });

  await check("a refused switch opens the doors again and writes nothing", async () => {
    const w = makeWorld();
    const p = w.Gate.switchTo(ABRAJ, ESH, () => Promise.resolve({ ok: false, error: "no" }));
    await w.advance(2000);
    const result = await p;
    assert(result && result.ok === false, "the API result must be handed back to the caller");
    assert(!w.closed(), "doors stayed shut after a refused switch");
    assert(!("bh-gate" in w.store), "a refused switch must not leave a reload note");
    assert(w.Gate._state().mode === null, "mode not cleared");
  });

  await check("a switch that throws opens the doors and rethrows", async () => {
    const w = makeWorld();
    const p = w.Gate.switchTo(ABRAJ, ESH, () => Promise.reject(new Error("bridge gone")));
    let thrown = null;
    p.catch(e => { thrown = e; });
    await w.advance(2000);
    assert(thrown && /bridge gone/.test(thrown.message), "error swallowed");
    assert(!w.closed(), "doors stayed shut after a crash");
  });

  await check("the ring round the seal follows the steps: empty, a third, then full on ready", async () => {
    const w = makeWorld();
    const sweep = () => w.byId.gate.style.props["--gate-sweep"];
    const p = w.Gate.switchTo(ABRAJ, ESH, () => Promise.resolve({ ok: true, reload: true }));
    await w.advance(2000);
    await p;
    assert(sweep() === "120deg" || /^1[2-9]\d(\.\d)?deg$/.test(sweep()),
      "one finished step of three is a third of the round, got " + sweep());
    const r = makeWorld({ closed: true, session: { "bh-gate": w.store["bh-gate"] } });
    r.Gate.pageLoaded();
    r.Gate.ready();
    await r.advance(150);
    assert(r.byId.gate.style.props["--gate-sweep"] === "360deg", "ready must complete the round, got "
      + r.byId.gate.style.props["--gate-sweep"]);
    await r.advance(2000);
    assert(r.byId.gate.style.props["--gate-sweep"] === "0deg", "the ring must be empty again for next time");
  });

  await check("a successful switch stays shut, shows the NEW company, and notes it for the reload", async () => {
    const w = makeWorld();
    const p = w.Gate.switchTo(ABRAJ, ESH, () => Promise.resolve({ ok: true, reload: true }));
    await w.advance(2000);
    const result = await p;
    assert(result.ok && result.reload, "result not returned");
    assert(w.closed(), "doors must stay shut for the reload");
    assert(w.byId.gateCo.textContent === "Eshraqat Albadr", "left door still shows the old company");
    assert(w.root.style.props["--gate-accent"] === "#861432", "left door still in the old colour");
    const note = JSON.parse(w.store["bh-gate"] || "null");
    assert(note && note.mode === "switch" && note.label === "Eshraqat Albadr" && note.accent === "#861432",
      "reload note missing or wrong: " + w.store["bh-gate"]);
    assert(note.stage === 1, "reload note should record one finished step");
    assert(/scaleX\(0\.333/.test(note.html), "the reload must paint the bar a third of the way, got " + note.html);
    assert(/Loading the interface/.test(note.html), "the step under the bar should be the one now running");
    assert(/ga in/.test(note.html), "after the reload the words are already in place, not easing in again");
    assert(/Switching to Eshraqat Albadr/.test(note.html), "right door should say where it is going");
    assert(w.seals.every(s => s.src === ESH.logo), "the seal must change to the new company's logo while shut");
  });

  await check("after the reload: shut on load, ticks real stages, shows the brief, opens on ready", async () => {
    const html = '<div class="gate-fill" id="gateFill"></div><div class="gate-step" id="gateStep"></div>'
      + '<div class="gate-count" id="gateCount"></div><div class="gate-brief" id="gateBrief" hidden></div>';
    const w = makeWorld({ closed: true, session: { "bh-gate": JSON.stringify({ mode: "switch", stage: 1, html }) } });
    w.byId.gateIn.innerHTML = html;                  // what index.html's inline script restores
    assert(w.Gate._state().mode === "switch", "gate.js did not pick up the switch");
    assert(!("bh-gate" in w.store), "the reload note must be used once, then removed");
    w.Gate.pageLoaded();
    await w.advance(10);
    assert(w.Gate._state().stage === 2, "page load is stage 2");
    assert(/scaleX\(0\.666/.test(w.byId.gateFill.style.transform), "bar should sit two thirds of the way: " + w.byId.gateFill.style.transform);
    const steps = w.byId.gateStep.children.map(c => c.textContent);
    assert(steps.includes("Preparing your dashboard"), "the step under the bar must name what is running: " + steps);
    assert(w.byId.gateCount.textContent === "Step 3 of 3", "the count must be written: " + w.byId.gateCount.textContent);
    await w.advance(1000);
    assert(!/scaleX\(1\)/.test(w.byId.gateFill.style.transform), "the bar must never reach the end before the dashboard is ready");
    assert(w.calls.gate_brief === 1, "brief not requested after the reload");
    assert(!w.byId.gateBrief.hidden && /USD 17,697/.test(w.byId.gateBrief.innerHTML), "brief not shown");
    assert(w.closed(), "opened before the dashboard was ready");
    w.Gate.ready();
    await w.advance(1200);
    assert(!w.closed(), "did not open after ready");
    assert(/scaleX\(1\)/.test(w.byId.gateFill.style.transform), "the bar should complete when ready");
    assert(w.calls.gate_ready === 1, "launch gate should be told ready once");
  });

  await check("a switch reload that never becomes ready still opens (safety limit)", async () => {
    const w = makeWorld({ closed: true, session: { "bh-gate": JSON.stringify({ mode: "switch", stage: 1, html: "" }) } });
    await w.advance(19000);
    assert(w.closed(), "opened too early");
    await w.advance(2000);
    assert(!w.closed(), "the safety limit did not open the doors");
  });

  await check("ready tells the launch gate exactly once, with page timings", async () => {
    const w = makeWorld();
    w.Gate.ready(); w.Gate.ready();
    await w.advance(100);
    assert(w.calls.gate_ready === 1, "gate_ready called " + w.calls.gate_ready + " times");
    assert(w.calls.marks && "painted" in w.calls.marks && "origin" in w.calls.marks, "page timings missing");
  });

  await check("the dashboard's animations wait until the launch doors are apart", async () => {
    const w = makeWorld({ launchGate: true });
    let settled = false;
    w.Gate.settled().then(() => { settled = true; });
    w.Gate.ready();
    await w.advance(100);
    assert(w.calls.gate_ready === 1, "ready not sent");
    assert(!settled, "animations released while the doors are still moving");
    await w.advance(700);
    assert(settled, "animations never released after the doors opened");
  });

  await check("with no launch gate, the animations start straight after the paint", async () => {
    const w = makeWorld();
    let settled = false;
    w.Gate.settled().then(() => { settled = true; });
    w.Gate.ready();
    await w.advance(60);
    assert(settled, "nothing to wait for, but the animations were held");
  });

  await check("lock does nothing while a switch is in progress", async () => {
    const w = makeWorld();
    let release;
    const p = w.Gate.switchTo(ABRAJ, ESH, () => new Promise(r => { release = r; }));
    await w.advance(500);
    await w.Gate.lock(ABRAJ);
    assert(w.Gate._state().mode === "switch", "lock hijacked a switch");
    release({ ok: false });
    await w.advance(1000);
    await p;
  });

  /* ---------------- part 2: wiring ---------------- */
  console.log("gate wiring");
  const index = fs.readFileSync("templates/index.html", "utf8").replace(/\r\n/g, "\n");
  const qa = fs.readFileSync("tests/gate_qa.html", "utf8").replace(/\r\n/g, "\n");
  const app = fs.readFileSync("static/app.js", "utf8").replace(/\r\n/g, "\n");
  const api = fs.readFileSync("backend/api.py", "utf8");
  const main = fs.readFileSync("app.py", "utf8");

  const gateBlock = (s) => {
    const a = s.indexOf("<!-- ===================== THE GATE");
    const b = s.indexOf("</script>", a);
    return a < 0 || b < 0 ? null : s.slice(a, b);
  };

  await check("#gate is the first thing in <body>, with its fill script right after", async () => {
    const body = index.slice(index.indexOf("<body>") + 6);
    assert(/^\s*<!-- =+ THE GATE/.test(body), "something sits before the gate in <body>");
    const block = gateBlock(index);
    assert(block && block.includes('sessionStorage.getItem("bh-gate")'), "fill script missing");
  });

  await check("the QA page carries an exact copy of index.html's gate block", async () => {
    assert(gateBlock(index) === gateBlock(qa), "tests/gate_qa.html's gate block differs from index.html's");
  });

  await check("index.html loads gate.css, and gate.js before app.js", async () => {
    assert(index.includes('href="../static/gate.css"'), "gate.css not linked");
    const g = index.indexOf('src="../static/gate.js"'), a = index.indexOf('src="../static/app.js"');
    assert(g > 0 && a > g, "gate.js must load before app.js");
    assert(index.includes('id="dashLockBtn"'), "dashboard lock button missing");
  });

  await check("bootstrap reports page load and opens the gate after the dashboard paints", async () => {
    const boot = app.slice(app.indexOf("async function bootstrap()"), app.indexOf("async function bootstrap()") + 2400);
    assert(/window\.Gate\.pageLoaded\(\)/.test(boot), "bootstrap does not report page load");
    assert(/hubPainted = showHub\(\)/.test(boot), "bootstrap does not keep showHub's paint promise");
    assert(/Promise\.resolve\(hubPainted\)[\s\S]{0,80}window\.Gate\.ready\(\)/.test(boot),
      "the gate must open after the dashboard paints, even when boot fails");
    const hub = app.slice(app.indexOf("function showHub()"), app.indexOf("window.showHub = showHub;"));
    assert(/return painted;/.test(hub), "showHub must return the dashboard paint promise");
    assert(/Promise\.all\(\[painted, window\.Gate \? window\.Gate\.settled\(\) : null\]\)\.then\(scheduleDashboardAnimations\)/.test(hub),
      "the icon animations must wait for the gate");
  });

  await check("company switch goes through the gate; Ctrl+L and the button lock", async () => {
    const sw = app.slice(app.indexOf("async function requestProfileSwitch"), app.indexOf("window.applyCompanyProfile = applyCompanyProfile;"));
    assert(/window\.Gate\.switchTo\(/.test(sw), "requestProfileSwitch does not use the gate");
    assert(/e\.key === "l" \|\| e\.key === "L"[\s\S]{0,80}lockScreen\(\)/.test(app), "Ctrl+L does not lock");
    assert(/dashLock\.onclick = safe\(\(\) => lockScreen\(\)/.test(app), "lock button not wired");
    assert(/\["Ctrl \+ L", /.test(app), "Ctrl+L missing from the shortcut sheet");
  });

  await check("gate_ready never waits behind the profile lock", async () => {
    assert(/_UNLOCKED_CALLS = frozenset\(\{"gate_ready"\}\)/.test(api), "gate_ready not exempt");
    assert(/name in _UNLOCKED_CALLS/.test(api), "exemption not applied in Api.__getattribute__");
  });

  await check("app.py puts the gate up first, only when run - never on import", async () => {
    const g = main.indexOf("launch_gate.start("), w = main.indexOf("import webview");
    assert(g > 0 && w > g, "the gate must start before `import webview`");
    const mainFn = main.slice(main.indexOf("def main()"), main.indexOf("def _main()"));
    assert(mainFn.includes("launch_gate.start("), "the gate must be started inside main(), not at import");
    assert(/_GATE\.close\(\)/.test(main), "an error must take the gate down");
    // 2026-10-20: `_warm_start_up(api)` - the launch card works out its own
    // rows in its own process, so the warm-up no longer takes the gate.
    assert(/def _shown\(\)[\s\S]{0,200}_warm_start_up\(api\)/.test(main),
      "the background warm-up must start when the window appears");
  });

  if (failures) { console.log(`\n${failures} failed`); process.exit(1); }
  console.log("\nall gate checks passed");
})();
