# The gate - launch screen, company switch and lock (2026-10-02)

## What Waleed asked for

A launch screen that shows the moment the app is clicked, lasts exactly as long
as loading takes, and then gets out of the way smoothly. After seeing four
concepts (`docs/designs/splash-concepts.html`) he chose **the gate**, inspired
by a login screen he had seen: two doors cut down the middle, the app's name
and logo on the left, and on the right what would be a login form. When sign-in
succeeds the doors part, left going left and right going right, fast but smooth.

His choices:

- Right door: **Welcome** - greeting, date, and four checks that tick as each
  real start-up stage finishes. No login (the app has none); the last tick is
  the "sign-in".
- Brief under the checks, three lines: **money waiting for payment**,
  **documents not submitted**, **this month so far** (options 2, 3 and 7 of
  the nine offered).
- Use the same gate for **company switching** and for **lock**.

## Design

| Moment | Who draws it | Opens when |
|---|---|---|
| Start-up | Windows (two layered windows, `backend/launch_gate.py`), ~0.17 s after the click | the page reports its first dashboard paint (`Api.gate_ready`) |
| Company switch | the page (`static/gate.js` + `gate.css`), shut through the reload | the new company's dashboard has painted |
| Lock (Ctrl+L, padlock button) | the page | any key or click |

Rules:

1. **Nothing advances on a timer.** Stage 1 = company data open, 2 = window
   created, 3 = page loaded, 4 = dashboard painted. The only timers are the
   animation lengths and a safety limit (45 s at start-up, 20 s after a switch)
   that opens the doors if "ready" never comes.
2. **Never block or hide an error.** Any failure means no gate, never a stuck
   one; an exception during start-up closes it.
3. **Colour is never the only signal** (Waleed is colour blind): DONE /
   WORKING / WAITING are written beside every ring.
4. **Money carries its currency**, summed per currency, never mixed.
5. **One look, drawn twice:** Pillow and CSS share the unit `height/1040` and
   one colour table.

## Measured

Five alternating launches each, seconds from the click: dashboard ready
median 3.13 with the gate vs 2.97 without; doors on screen at 0.16-0.25.
`gate_ready` was moved out of the bridge's profile lock after it was measured
arriving 0.3 s late behind icon loads (that delay existed without the gate too).

## Not built

- Idle auto-lock (lock after N minutes away) - offered, not asked for.
- A taskbar shortcut: Waleed already has one pinned.
