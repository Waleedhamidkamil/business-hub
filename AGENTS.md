# AGENTS.md - read this first

Portable instructions for **any** AI coding tool (Cursor, Claude Code, Copilot, Windsurf, …) working in this repo. The goal: understand the whole app from the **map** and only read the specific source files a task actually needs - accurate results, far fewer tokens, no broken features.

## What this project is
**Business Hub** (formerly "ABRAJ Documents Hub" / "PO Tool") - a **Windows desktop app** (pywebview + Edge WebView2, **not** a web server). It generates and tracks four business document types - **PO, Quotation, Delivery Note, Invoice** - (Official Letter was a fifth until it was retired on 2026-08-27) with SQLite storage, PDF generation via a headless browser, plus costing, analytics, and client management. Entry: `python app.py`. Frontend JS calls Python through `const api = () => window.pywebview.api` → `api().methodName()` maps to `backend.api.Api.method_name()`.

## You are not alone in here - read the board first
**Two AI sessions work this repo** (Claude Code and Codex), sometimes at the
same time, on the same files and the same **live** database. They cannot talk to
each other, so **[`AGENT-BOARD.md`](AGENT-BOARD.md) is the channel.**

**Before you start:** read it. Claim a row in *Now*. Check *Done* so you do not
rebuild something that already exists - that has happened twice.
**Before you stop:** commit, and update your row.

The three rules that have actually saved this repo, in full on the board:
- **stage explicit paths - never `git add -A`, never a whole directory.** One
  such command swept the other agent's uncommitted work into a commit and split
  a change in half.
- **`git status` before committing.** Files you did not touch are the other
  agent's. Leave them.
- **hold the test lock.** The suite writes to Waleed's real catalogue; two runs
  at once collide.

## The map (single source of truth)
A complete, cross-linked map of every part of the app lives in **[`docs/`](docs/)**. It is plain markdown (also openable as an Obsidian vault).

**Start here → [`docs/README.md`](docs/README.md)**, then [`docs/01-ARCHITECTURE.md`](docs/01-ARCHITECTURE.md).

## How to work (the routing rule)
1. **Read the map first.** For any question or task, read `docs/README.md` → `docs/01-ARCHITECTURE.md` → the specific owning doc (see the table in `docs/11-RUN-AND-MAINTAIN.md`). This usually answers "where does X live / what depends on X" without opening source.
2. **Then read the real file - before you edit it.** The map tells you *which* files matter and how they relate; it does **not** replace reading a file you're about to change. Never edit against the map alone (it can lag the code by one change).
3. **Check the Impact Map.** `docs/01-ARCHITECTURE.md` has an "Impact Map - touch X, check Y" section. Respect it to avoid breaking the five parallel doc-type flows, numbering, column-width math, or the migrations.
4. **Ask the two propagation questions.** Nothing here is built once and used once. After every change: (a) *what did this make WRONG* - who still points at the thing you replaced, removed or superseded? That is a grep with one right answer, and it is where this repo's worst bugs came from (a Popup migration left one file reading a deleted div and PO linking hung silently in both directions). Leave a TEST where the answer is mechanical. (b) *where ELSE in the app would this new capability help the user* - a product question, not a refactoring one. Raise it in chat with a NAMED screen and a COUNT from their own data (the PO translator's second homes were worth 161, 738 and 9 items respectively); never a vague maybe, never built unasked. Full rule, with the worked example and the limits, in [`docs/11-RUN-AND-MAINTAIN.md`](docs/11-RUN-AND-MAINTAIN.md).
5. **After changing code, update the map in the same change.** Update the doc that owns the changed file (mapping table + full protocol in [`docs/11-RUN-AND-MAINTAIN.md`](docs/11-RUN-AND-MAINTAIN.md)). A stale map is worse than none.
6. **Verify.** Run the smoke tests: `python smoke_test.py` (and `python smoke_quote.py` for quotations) before claiming done.

## NEVER use an em dash (Waleed's ruling, 2026-09-11)

Applies to every AI tool working this repo, Codex included.

The character `U+2014` is banned **for any reason** - printed documents, UI text,
code comments, commit messages, test names, these docs. Use a plain hyphen `-`.
10,581 were removed on 2026-09-11 across 366 files, and
`tests/no_em_dash.test.js` fails with the file and line of any that come back.
Replace the character; never add an exception to that test.

## Two skills are mandatory (Waleed's ruling, 2026-09-11)

Applies to every AI tool working this repo, not just Claude Code.

- **Before proposing any fix** for a bug, test failure or "X is broken": find
  the root cause first (`systematic-debugging`). One measurement is not a
  diagnosis - on 2026-09-10 a single sample per version produced a confident and
  WRONG "I broke it"; six alternating runs showed both versions failing
  identically and the real defect was a read timeout skipping the fallback
  chain.
- **Before claiming done, fixed or passing**: run the command and show the
  output in the same message (`verification-before-completion`). This board has
  already had to retire the "pre-existing failures" habit once, when five
  supposedly-known failures turned out to be five real bugs.
- **A test that cannot fail is not a test.** After writing one, break the code
  it covers and confirm it goes red.

Not adopted here: subagent / parallel-agent workflows. Two agents against one
shared live SQLite database is what created the test-lock ritual below.

## Good to know
- Run/debug/deps: see [`docs/11-RUN-AND-MAINTAIN.md`](docs/11-RUN-AND-MAINTAIN.md) (`ABRAJ_DEBUG=1` for devtools; deps are `pywebview`, `jinja2`, `openpyxl`; PDFs need Chrome/Edge).
- The repo root is cluttered with **throwaway/dev-scratch files** (`_build_*`, `_shot_*`, `_crop_*`, `_z_*`, screenshots, `graphify-out/`, backups). They are **not** part of the app - every one is catalogued in [`docs/10-SCRATCH-LEFTOVERS.md`](docs/10-SCRATCH-LEFTOVERS.md). Don't mistake them for real code.
- `graphify-out/` is an **older, broken** knowledge graph - ignore it; use `docs/`.
