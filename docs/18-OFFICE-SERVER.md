# 18 - Office server: gateway, one engine per person, the launcher

Built 2026-10-22 in `D:\BUSINESS HUB ONLINE` (never in the live `D:\PO TOOL`).
Roadmap: [`superpowers/plans/2026-10-22-multi-user-office-server.md`](superpowers/plans/2026-10-22-multi-user-office-server.md);
this phase: [`superpowers/plans/2026-10-22-office-server-phase2.md`](superpowers/plans/2026-10-22-office-server-phase2.md).

The desktop app still runs exactly as before (`python app.py`). The office
version adds two folders, `server/` and `launcher/`, and changes no screen.

## The shape

```
 Each PC                              Server laptop                      (127.0.0.1 only)
 launcher/launcher.py                 server/gateway.py  :8765          server/engine.py, one per person
 pywebview window ── HTTP (LAN) ──▶   /templates /static  (the screens)  ┌▶ waleed      Api() - Abraj
 window.pywebview.api.X(...)          /auth/login|check|logout           ├▶ accountant  Api() - Eshraqat
   = launcher/client.py LauncherApi   /api/<method> ── by session ───────┘  (own process, own company,
     ├ server methods: POST /api       /api-methods (names, public)          own TEMP, own memory)
     └ 9 PC-side calls, done here      /files?path=  /files/info  /upload
```

* **Engine** - today's `backend.api.Api` in its own process for ONE person. Its
  own company registry (`data/users/<name>/profile-registry.json`, set before
  `config` is imported), so switching company switches that person only.
  `BUSINESSHUB_USER=<name>` holds new document numbers in that person's name
  (Phase 1 `number_claims`). Listens on 127.0.0.1 with a secret only the
  gateway knows; ends the moment the gateway ends (it watches the gateway's
  process, and the gateway also puts it in a Windows job object).
* **Gateway** - the one door on the network. Logs people in, starts each
  person's engine (at server start for everyone enabled, else on first use; one
  at a time), starts it again if it died, forwards each call. A call that
  reached an engine and then lost it is NEVER sent again (it may have saved):
  the person is told to check. Serves the screens (code only - `static/sigs`
  and `static/brands`, the signature and stamp images, are never served),
  hands out files from the company documents folders and the person's own
  previews only, receives files picked on a PC.
* **Launcher** - the app's window on a PC. Its bridge object forwards every
  server method (the list comes from `/api-methods` after login, so a new
  method needs no new launcher) and does nine calls on the PC itself:

| Call | On the PC |
|---|---|
| `open_path`, `print_file`, `reveal_in_folder`, `copy_file_to_clipboard` | fetches the file into the person's own copy (`Documents\Business Hub\<COMPANY>\...`, same folders as the server's; previews into `%TEMP%\Business Hub\preview`), only when missing or older, then opens / prints / shows / copies it there |
| `bulk_pick_spreadsheet` | file dialog here, uploads, the server reads its copy |
| `attach_doc_signed_copy` | file dialog here, sends the file to `attach_doc_signed_copy_data` |
| `dm_pick_file`, `dm_pick_folder` | "done at the server itself" (their only users are Data Manager's backup / restore / PDF re-link) |
| `gate_ready` | no launch card on a PC |

  Every quotation, DN, invoice, PO or statement the person saves is also
  copied down to their folder at once (`KEEP_A_COPY`): the server's copy is
  the master, this one is theirs.

## Running it

On the server (from `app\`):

```
python -m server.users add waleed --role admin --display "Waleed"
python -m server.users add <name> --role accountant --display "<Name>"
python -m server.gateway                      # all network cards, port 8765
```

`python -m server.users passwd|disable|enable|list` manage accounts. On a PC:
`pythonw launcher\launcher.py`, then the server's name or address (e.g.
`SERVER-NAME:8765`), name and password. "Keep me logged in on this PC" keeps
the session scrambled with Windows DPAPI (useless copied to another PC or user).

Two "PCs" on one machine, for testing: `launcher.py --home <folder>` gives each
its own login, copies and browser data.

## Where things live

| Where | What |
|---|---|
| `data/users/users.json` | accounts: display, role, enabled, PBKDF2-SHA256 hash (200k, own salt) - never a password |
| `data/users/<name>/` | `profile-registry.json` (that person's company), `engine.log` (crashes), `engine-console.log`, `uploads/` |
| `data/server/` | `sessions.json` (SHA-256 of each token only), `engines.json` (pid, port), `gateway.log` (logins, refusals, engine starts, slow calls), `temp/<name>/` (that engine's TEMP - previews) |
| PC `%APPDATA%\Business Hub\` | `launcher.json` (server, last name, scrambled session), `launcher.log` |
| PC `Documents\Business Hub\` | the person's own copies |
| PC `%LOCALAPPDATA%\BusinessHub\Launcher\WebView2` | the window's browser data, apart from the desktop app's |

## Rules the code keeps, and the test that breaks if one goes

Each was proved by breaking the code on purpose and watching the test go red
(20 breaks on the gateway, 13 on the launcher, 2026-10-22).

| Rule | Test |
|---|---|
| Wrong password: nothing; 3/5 wrong locks the name for 5 min | `tests/server_gateway.test.py` |
| Two people, two engines, two companies | same |
| `/files` only documents + own previews, however the path is dressed | same |
| Signatures / stamps never served; no path climbs out of the screens | same |
| Uploads stay in the person's folder | same |
| PC-side calls and backup / restore refused by the server | same |
| Every Api method that acts on a screen is on `LOCAL_ONLY` | same (`PcSideCallsStayListed`) |
| Crashed engine restarted; a call lost half way never re-sent | same (`NeverSentTwice`) |
| Sessions survive a restart; logout and expiry end them; tokens not stored | same |
| Engines end with the gateway; two gateways never share a port | same |
| pywebview (its own code) offers only the bridge calls | `tests/launcher_client.test.py` |
| PC-side calls = what the server refuses, same parameters as the desktop's | same |
| A server-named file lands only in the two local folders | same |
| Copies fetched once, again only when newer; saves copied down | same |
| A page from anywhere else cannot use the bridge | same |
| Accounts | `tests/server_users.test.py`; engines: `tests/server_engine.test.py` |
| The PDF self-test answers only the server itself | `tests/server_gateway.test.py` (`SelfTestOnlyFromTheServer`) |
| The log names person, PC and company; only the admin reads it; PC notes survive a network cut | `server_gateway.test.py`, `launcher_client.test.py` |

## Office management

Administrators can use the integrated Office Management screen to review
accounts, enable or disable an account, reset a password, revoke all sessions
for a user, and inspect running engines and server disk space. These actions
are gateway operations and require an authenticated administrator; hiding the
screen is not relied on for authorization.

The role policy is centralized in `server/permissions.py`. Windows service
setup, firewall/dependency checks, backups, HTTPS provisioning, and scheduled
startup remain responsibilities of the separate `SERVER SETUP` utility.
| The log's own rules (whose fault, swallowed bugs, trimming, trail, pruning, never holding exceptions) | `tests/activity_log.test.py`, `tests/activity_capture.test.js`, `tests/activity_view.test.js` |

## Measured (2026-10-22, Waleed's PC, a full copy of the real data)

* Move-day rehearsal: `SERVER SETUP\relocate.py` moved all 573 stored paths, 0 left elsewhere.
* Engine start: about 1 s. A call through the server: 8.5 ms simple, 31 ms
  company info (PC at 93% memory). Memory: engine 41-54 MB, gateway 32 MB,
  launcher about 100 MB.
* Two real launcher windows: dashboard, one switched to Eshraqat while the
  other stayed on Abraj, invoice editor, PDF opened from the person's own copy.

## Day one at the office (2026-10-23)

* `SERVER SETUP/setup_server.ps1` steps 13-16: who may log in; **the server starts with Windows** (task
  "Business Hub Server": at startup, whether anyone is logged on or not, as the setup's own account - so
  it uses the fonts and Chrome the PDF proof used - and again within a minute if it stops); the firewall
  door (TCP 8765, Private/Domain networks only), a Public network offered to Private, Python block rules
  found; and the PDF proof re-run **by the running server** (`GET /selftest/pdf`, from the server itself
  only - `from_this_pc()`).
* Step 3 installs EXACT versions from `app/requirements-lock.txt` (33 pins, checked by
  `SERVER SETUP/check_versions.py`): this PC has cryptography 48.0.0 while PyPI is at 50.0.2, and
  cryptography already warns it will one day refuse the Eshraqat certificate's serial number.
* `SERVER SETUP/Add a person.cmd` (login name, role, display name, password twice).
  `python -m server.users` refuses a bad name / role / unknown account BEFORE any password prompt.
* `PC SETUP/Install Business Hub.cmd` + `install_pc.ps1` on each office PC: Python, pywebview 6.2.1,
  WebView2, the launcher copied to `%LOCALAPPDATA%\Business Hub\launcher` (updated by hash on a re-run),
  desktop + Start menu shortcuts with `launcher/business-hub.ico`, the server address saved for the login
  page. `-CheckOnly`; `-TestHome <folder>` + `-Yes` for testing on one PC.

## The activity and problems log (2026-10-23)

Owner: `backend/activity_log.py` ([[03-BACKEND-DATA]]); table in [[09-DATA-MODEL]]. On the server:
* every call an engine runs is a row, stamped with the person, the PC (`X-PC`, from the launcher's
  `X-PC-Name`), the company and a call id (`X-Call-Id`);
* the gateway adds logins (ok / wrong password / locked), log-outs, refused files, engines starting,
  failing, restarted, lost in the middle of a call, and a **watchdog** every 5 minutes: an idle engine
  that does not answer is restarted, a disk under 5 GB is reported;
* the launcher notes what it does on the PC (opened / printed / copied / showed a PDF, or why not) and
  network failures, keeps them in `%APPDATA%\Business Hub\pending-log.jsonl` and sends them every 30 s,
  so trouble during a network cut still arrives once the network is back;
* each screen sends what was opened, pressed and shown, screen errors and reports
  (`static/activity_capture.js`).
The admin reads it on the **Activity log** screen (dashboard pulse button): tiles by whose fault, one
table, and per entry a sentence saying whose fault it was, what that person did in the minutes before,
and the technical details (where in the code, with the traceback). Everyone has **Report a problem**
(dashboard flag button). The engine gets `BUSINESSHUB_ROLE` from the gateway; only `admin` may read the log.

Found while building it: holding a caught exception keeps its frames alive, and subprocess closes a
pipe end only when its frame goes - every Chrome PDF render in an engine then waited 60 s. Fixed (copy,
never hold) and guarded by two tests.

## Known limits (next phases)

* Plain HTTP inside the office network: passwords cross the office Wi-Fi only.
* `data/secrets.json` must already hold `verify_key` and `owner_password`
  before go-live (the real one does): if missing, two engines could each make
  one on first use and one would be lost.
* "Prepared by" / signer names still come from Settings (Phase 3: per person).
* Uploads are never cleaned up (small files; add later).
* Each call opens a new connection (the 8 ms); keep-alive could cut it.
* Backup / restore, PDF re-link: at the server only (Phase 5 admin screen).
* Locks, revisions, recycle bin, created-by filter: Phase 4.
