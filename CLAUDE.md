# CLAUDE.md

**Business Hub** (formerly "ABRAJ Documents Hub" / "PO Tool") - a Windows **pywebview desktop app** (not a web server) that generates & tracks PO / Quotation / Delivery Note / Invoice documents for two company profiles (Abraj, Eshraqat), with costing/profit, analytics, and client management. Run with `python app.py`.

## Another agent shares this repo - read the board first
**[`AGENT-BOARD.md`](AGENT-BOARD.md)** is how this session and the Codex session
coordinate; there is no other channel. Read it before starting, claim a row,
check what is already done, and update it before you stop. Its rules on staging
explicit paths, checking `git status` before committing, and holding the test
lock exist because each was broken on 2026-08-19/20.

## Use the map - don't re-read the whole codebase
A complete, cross-linked map of the app lives in **[`docs/`](docs/)**. Routing rules for every AI tool are in **[`AGENTS.md`](AGENTS.md)** - the same rules apply here:

1. **Read the map first:** [`docs/README.md`](docs/README.md) → [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md) → the doc that owns the area (see the ownership table in [`docs/11-RUN-AND-MAINTAIN.md`](docs/11-RUN-AND-MAINTAIN.md)). This answers most "where is X / what depends on X" questions without opening source.
2. **Then read the actual file before editing it** - the map locates and explains code; it does not replace reading a file you're about to change.
3. **Respect the "Impact Map - touch X, check Y"** in `docs/01-ARCHITECTURE.md` (five parallel doc types, numbering, column-width math, migrations).
4. **Ask the two propagation questions** ([`docs/11-RUN-AND-MAINTAIN.md`](docs/11-RUN-AND-MAINTAIN.md)): (a) what did this change make WRONG - who still points at what you replaced? A grep, one right answer, this repo's most expensive bug class; leave a TEST where the answer is mechanical. (b) **where ELSE in the app would this new capability help the user?** Raise it in chat with a named screen and a count from their data - never a vague maybe, never built unasked.
5. **After changing code, update the owning doc in the same change** (protocol: [`docs/11-RUN-AND-MAINTAIN.md`](docs/11-RUN-AND-MAINTAIN.md)). Keep the map fresh - a stale map is worse than none.
6. **Verify:** `python smoke_test.py` (and `python smoke_quote.py` for quotations) before claiming done.

## NEVER use an em dash (Waleed's ruling, 2026-09-11)

The character `U+2014` is banned from this app **for any reason**: not on a
printed document, not in the interface, not in a comment, not in a test message,
not in these docs. Use a plain hyphen `-`.

He asked for it by name and he means everywhere. 10,581 of them were removed on
2026-09-11 across 366 files.

**This is enforced, not just written down.** `tests/no_em_dash.test.js` walks the
whole repository and fails with the file and line of every one it finds. If you
are reading this because that test went red, replace the character - do not add
an exception to the test.

## Two skills are mandatory here (Waleed's ruling, 2026-09-11)

Superpowers is installed globally (`~/.claude/skills/`). Most of it is optional.
**These two are not**, because each was chosen against a mistake that actually
happened in this repo:

1. **`systematic-debugging`** - invoke it before proposing ANY fix for a bug,
   test failure or "X is broken" report. Root cause first; no fixes before
   Phase 1 is done. *Why:* on 2026-09-10 a single sample of each version was
   enough for a confident "I broke the AI translation" - the next six runs
   showed both versions failing the same way and the real defect was elsewhere
   (a read timeout skipping the fallback chain). One measurement is not a
   diagnosis.

2. **`verification-before-completion`** - no "done", "passing" or "fixed"
   without the command output in the same message. *Why:* this repo's board
   already had to write "delete the 'pre-existing failures' habit" after five
   supposedly-known failures turned out to be five real bugs.

**The local habit that goes with them:** a test that cannot fail is not a test.
After writing one, break the code it covers and confirm it goes red - this
session caught two of its own vacuous tests that way (one hand-fed the value it
was meant to be checking; another asserted against a comment rather than the
code).

Deliberately NOT adopted: `subagent-driven-development` and
`dispatching-parallel-agents`. Parallel agents against one shared live SQLite
database is how the test-lock problem was created in the first place.

## graphify (currently unavailable)
`graphify-out/` is an **older** graph and the **`graphify` CLI is broken on this machine** (orphaned uv shim - `uv tool list` shows nothing installed). Until it is reinstalled, **ignore `graphify-out/` and use `docs/`**. If you reinstall graphify, run `graphify update .` after code changes to refresh it; the queryable commands (`graphify query/path/explain`) then work again as a secondary tool alongside `docs/`.
