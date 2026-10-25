# Office server Phase 2 - gatekeeper, one engine per person, the launcher

> **Status 2026-10-22: DONE.** Tasks 1-6 built, tested and mutation-checked; the
> live run used a full copy of the real data (move-day rehearsal). The working
> reference is now [`docs/18-OFFICE-SERVER.md`](../../18-OFFICE-SERVER.md) (not
> 15 - that number was taken). Changes from this plan: `/files/info` added (the
> launcher fetches only when its copy is older); `dm_pick_file` / `dm_pick_folder`
> answer "done at the server" (their only users are backup / restore / PDF
> re-link); `dm_create_backup` / `dm_restore_backup` are refused from PCs until
> Phase 5; no `/login` page on the server (the launcher's own page works even
> when the server is down).

Roadmap: `2026-10-22-multi-user-office-server.md` (Part A). Phase 1 (safe
numbers, WAL, snapshots) is done. Built in `D:\BUSINESS HUB ONLINE\app`, never in
the live `D:\PO TOOL\app`.

**Goal:** the app runs on the server; each person opens a small launcher on
their own PC that shows the same screens, while every click that needs data is
answered by THAT person's own engine on the server.

## How it fits together

```
 Launcher (each PC)                 Gateway (server, port 8765, LAN)            Engines (server, 127.0.0.1 only)
 pywebview window  ── HTTP ──▶  /login  /auth/*                          ┌─▶ waleed  :9101  Api() - Abraj
 window.pywebview.api.X(...)    /static/*  /templates/*  (the screens)   ├─▶ ahmed   :9102  Api() - Eshraqat
   = LauncherApi.X(...)         /api/<method>  ──── routes by session ──┘    (each its own process, own
     ├ 209 methods: POST /api       /api-methods                               current company, own memory)
     └ 7 local: open/copy/print     /files?path=   (PDFs, allow-listed)
       a PDF, pickers, gate_ready   /upload        (picked files)
```

* **Engine** (`server/engine.py`): today's `backend.api.Api`, one process per
  logged-in person, HTTP on 127.0.0.1 only, guarded by a secret only the gateway
  knows. Its OWN company registry (`data/users/<user>/profile-registry.json`),
  so switching company switches that person only. Started with
  `BUSINESSHUB_USER=<user>`, so document numbers are held in that person's name
  (Phase 1's `number_claims` holder).
* **Gateway** (`server/gateway.py`): the one door on the network. Serves the
  screens, logs people in (`server/users.py`, PBKDF2 passwords), starts each
  person's engine on first use (one at a time, so first-run migrations never
  race), restarts it if it died, and forwards each call to the right engine.
  `/files` hands out PDFs from the documents folder and the preview folder
  only; `/upload` receives files picked on a PC.
* **Launcher** (`launcher/launcher.py`): pywebview window on the person's PC.
  Its `js_api` is a proxy built from `/api-methods`, so the screens' 330 calls
  work unchanged. Seven are done locally:

| Bridge call | Launcher does |
|---|---|
| `open_path`, `reveal_in_folder`, `copy_file_to_clipboard`, `print_file` | downloads the PDF into `Documents\Business Hub\…` (the person's own copy), then opens / shows / copies / prints it there |
| `dm_pick_file`, `bulk_pick_spreadsheet` | file dialog on the PC, uploads, returns the server path |
| `attach_doc_signed_copy` | file dialog on the PC, sends it to `attach_doc_signed_copy_data` |
| `dm_pick_folder` | backups are made on the server (Phase 5) - says so |
| `gate_ready` | no launch card on a client - answers "no gate" |

## Protocol

* Engine: `POST /call` `{"method": str, "args": [...]}` + header
  `X-Engine-Secret` → `{"ok": true, "result": …}` or `{"ok": false, "error": …}`;
  `GET /health`, `GET /methods`. Private names (`_x`) refused.
* Gateway: `POST /auth/login {"user","password"}` → `{"ok", "token"}`;
  `POST /auth/check`, `POST /auth/logout`; `POST /api/<method> {"args"}` +
  `Authorization: Bearer <token>`; `GET /api-methods` (public: names only);
  `GET /files?path=` (auth, allow-listed); `POST /upload?name=` (auth, raw body).
* Results are JSON exactly as pywebview would have serialised them
  (`json.dumps(default=str)`).

## Tasks (each test-first, mutation-checked)

1. **users.py** - add / verify / list / disable; PBKDF2-SHA256 200k, per-user
   salt; `python -m server.users add <name>`. Test: right/wrong password,
   disabled user refused, file never holds the password.
2. **engine.py** - per-user registry before `config` is imported; same start-up
   as `app._main` minus the window; ThreadingHTTPServer. Test: two engines on a
   scratch copy, one switched to Eshraqat, the other still Abraj; a private
   method refused; a wrong secret refused.
3. **gateway.py** - login, sessions (kept across a restart), engine pool, `/api`,
   `/files` allow-list, `/upload`, static. Test: wrong password 401, call
   without token 401, two users routed to two engines, `/files` outside the
   allow-list 403, upload round-trip, a killed engine restarted on next call.
4. **launcher.py** - proxy built from `/api-methods`; the seven local calls;
   local copy under `Documents\Business Hub`; login page flow. Test: proxy
   covers every name the screens call; local calls never forwarded; a path
   outside the documents root lands in the temp cache, never outside it.
5. **Run it** - gateway + two launchers on this PC as two people, one per
   company, saving at the same time; screenshots.
6. Docs (01, 02, new `docs/15-OFFICE-SERVER.md`), board.
