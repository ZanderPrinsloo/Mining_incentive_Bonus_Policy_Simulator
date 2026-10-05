# Deploying the Bonus Policy Simulator to a company server

The app is a Flask app (Waitress WSGI server) that, for its own data (saved
scenarios), only ever touches its local SQLite file — no SQL Server needed
for that. SQL Server is used only by the optional "Import from STPTM9000"
button on Doornkop-derived scenarios, and STPTM9000 runs on the SAME machine
this app deploys to.

**Default recommendation: run it under your own Windows login, not as a
Windows Service.** Trusted Authentication connects to SQL Server as whatever
Windows account is running the process. Your own account, logged into this
server, already has whatever STPTM9000 access it has today (the same reason
the Doornkop dashboard already works for you) — so running this app the same
way needs **zero SQL Server administration**: no SSMS, no new logins, no
grants. Installing it as a Windows Service instead runs it as a *different*,
never-before-granted identity (LocalSystem by default) — see the "Windows
Service" section below if you want that later for auto-start-on-boot, but it
requires an extra one-time grant that running as yourself doesn't.

---

## Before you start

You need on the server:

1. **Python 3.12+** installed for your user.
2. **ODBC Driver 18 for SQL Server** — only needed for the "Import from
   STPTM9000" feature; the rest of the app works without it. Check with:
   ```bat
   reg query "HKLM\SOFTWARE\ODBC\ODBCINST.INI\ODBC Driver 18 for SQL Server"
   ```
   If missing, install it from
   https://learn.microsoft.com/en-us/sql/connect/odbc/download-odbc-driver-for-sql-server
   (ask IT if you don't have rights), or set `STPTM_DRIVER` in `.env` to
   whatever version is already installed.
3. The **`.env` file** with the real database settings for this deployment.
   It is NOT in git (per-machine config) — copy it from your dev machine to
   the project folder on the server, then edit it to point at STPTM9000 on
   that same machine instead of a local restored copy. See `.env.example`
   for every variable — in short:
   ```
   STPTM_SQL_SERVER=localhost\<INSTANCE_NAME>
   STPTM_DATABASE=STPTM9000
   STPTM_BUSSUNIT=RE
   ```
   (leave `STPTM_USERNAME`/`STPTM_PASSWORD` blank — Trusted Auth). Find the
   real `<INSTANCE_NAME>` on that box via `services.msc` — look for a
   service named "SQL Server (<INSTANCE_NAME>)".

---

## Deployment steps (recommended: run as yourself)

Copy the whole project folder to the server (e.g. `C:\inetpub\bonus-policy-simulator`
or any folder you can write to). Then, logged in on the server under your
own account:

### 1. One-time setup (elevated Command Prompt)

```bat
cd C:\<path-to-project>
deploy\setup.bat
```

This creates the virtual environment, installs dependencies
(`requirements.txt` + `pywin32`), and opens firewall port 5050. Needs
internet for pip.

### 2. Start it

```bat
deploy\start.bat
```

This runs in the foreground under your own login — the same Trusted Auth
identity you already use for STPTM9000 elsewhere. Leave this window open
(minimized is fine); closing it stops the app. Stop it any time with
`deploy\stop.bat`, or Ctrl+C in that window.

### 3. Verify

On the server: open `http://localhost:5050`.
From another PC: open `http://<server-name>:5050`.

---

## Optional: Windows Service (auto-start on boot, no login required)

Only do this once the "run as yourself" path above is confirmed working —
it trades zero-setup for persistence across reboots/logouts, at the cost of
one extra grant:

```bat
deploy\install_service.bat DOMAIN\youruser yourpassword
```

Passing your own account here keeps the same zero-extra-grant property as
running it interactively (same identity, already has access). Omitting the
account installs it as **LocalSystem** instead — a different identity
(`NT AUTHORITY\SYSTEM`) that needs its own SQL Server login granted before
STPTM9000 will connect; see Troubleshooting below if you go that route.

This installs with **Automatic** startup — it starts on boot with no one
logged in, and restarts automatically if the server reboots. (This is set
via `--startup auto` in `install_service.bat`; pywin32 defaults a new
service to Manual otherwise, which would silently NOT survive a reboot.)

To uninstall: `deploy\uninstall_service.bat`.

---

## Day-to-day operations

| Task                      | Command                                                      |
| -------------------------- | ------------------------------------------------------------ |
| Start (foreground, as you) | `deploy\start.bat`  (stop with `deploy\stop.bat`)            |
| Start service               | `.venv\Scripts\python.exe run_service.py start`              |
| Stop service                | `.venv\Scripts\python.exe run_service.py stop`               |
| Restart service             | `.venv\Scripts\python.exe run_service.py restart`            |

The service (if installed) appears as **"Harmony Bonus Policy Simulator"**
in services.msc.

---

## Updating to a new version

```bat
cd C:\<path-to-project>
git pull
deploy\setup.bat                      :: re-installs any new dependencies
```
Then either re-run `deploy\start.bat`, or, if using the service,
`.venv\Scripts\python.exe run_service.py restart`.

---

## Configuration

- `STPTM_SQL_SERVER` / `STPTM_DATABASE` / `STPTM_BUSSUNIT` — the real-data
  import feature's database connection. Leave `STPTM_SQL_SERVER` blank to
  disable that feature entirely (the rest of the app works fine either way).
- `STPTM_USERNAME` / `STPTM_PASSWORD` — SQL Authentication; leave both blank
  for Windows Trusted Authentication instead.
- `HOST` / `PORT` — default `0.0.0.0:5050` for `run_server.py`/
  `run_service.py`; override via environment variables if 5050 conflicts
  with something else on the server.
- The app's own scenarios are stored locally in `data\simulator.db`
  (SQLite) — back this file up if you care about what's saved there.

## Troubleshooting

- **`http://<server>:5050` works on the server but not from other PCs** —
  firewall rule missing or blocked by corporate policy. Re-run
  `deploy\setup.bat` elevated, or have IT open TCP 5050.
- **"Import from STPTM9000" button doesn't appear, or shows a warning
  instead** — open a Doornkop-derived scenario's Manual Inputs tab; if the
  feature is unavailable it now shows the *real* connection error there
  (added 2026-09) instead of just silently not appearing. Read that message
  first — it tells you which of the cases below applies.
- **Login failed / access denied error in that warning box, only when
  running as a Windows Service** — this is the LocalSystem case explained
  above. Either re-run `deploy\install_service.bat DOMAIN\youruser yourpassword`
  with your own account instead (matches "run as yourself", no new grant
  needed), or grant `NT AUTHORITY\SYSTEM` a login on STPTM9000 in SSMS
  (Security → Logins → New Login → `NT AUTHORITY\SYSTEM` → User Mapping →
  check `STPTM9000` → grant `db_datareader`) if you specifically need
  LocalSystem. This should not come up at all when using `deploy\start.bat`
  under your own login — if it does even there, your account itself doesn't
  have STPTM9000 access (ask whoever administers that database).
- **"Server not found" / network error** — `STPTM_SQL_SERVER`'s instance
  name is wrong for this box. Find the real one via `services.msc` — look
  for "SQL Server (<INSTANCE_NAME>)" — and use `localhost\<that name>`.
- **Service won't start** — check Windows Event Viewer (Application log,
  source `BonusPolicySimulator`).
