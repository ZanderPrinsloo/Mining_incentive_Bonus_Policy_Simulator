# Deploying the Bonus Policy Simulator to a company server

The app is a Flask app (Waitress WSGI server) that, for its own data (saved
scenarios), only ever touches its local SQLite file — no SQL Server needed
for that. SQL Server is used only by the optional "Import from STPTM9000"
button on Doornkop-derived scenarios. This guide deploys it as a **Windows
service** that starts automatically on boot and is reachable on the intranet
at `http://<server>:5050`.

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
3. **Network access** from the server to the STPTM9000 SQL Server host, if
   using the import feature.
4. The **`.env` file** with the real database settings for this deployment.
   It is NOT in git (per-machine config) — copy it from your dev machine to
   the project folder on the server, then edit it to point at the live
   database instead of a local restored copy. See `.env.example` for every
   variable and what each one does — in short:
   ```
   STPTM_SQL_SERVER=<the real server hostname on Harmony's network>
   STPTM_DATABASE=STPTM9000
   STPTM_BUSSUNIT=RE
   STPTM_USERNAME=<a SQL login with read access, if not using Trusted Auth>
   STPTM_PASSWORD=<...>
   ```

---

## Deployment steps

Copy the whole project folder to the server (e.g. `C:\inetpub\bonus-policy-simulator`
or any folder you can write to). Then on the server:

### 1. One-time setup (elevated Command Prompt)

```bat
cd C:\<path-to-project>
deploy\setup.bat
```

This creates the virtual environment, installs dependencies
(`requirements.txt` + `pywin32`), and opens firewall port 5050. Needs
internet for pip.

### 2. Install and start the service

```bat
deploy\install_service.bat
```

By default the service runs as **LocalSystem**. If the app needs a specific
account to reach STPTM9000 instead (e.g. Trusted Auth as a particular
identity), run it as that account instead:

```bat
deploy\install_service.bat DOMAIN\youruser yourpassword
```

### 3. Verify

On the server: open `http://localhost:5050`.
From another PC: open `http://<server-name>:5050`.

To uninstall: `deploy\uninstall_service.bat`.

---

## Day-to-day operations

| Task                     | Command                                                      |
| ------------------------ | ------------------------------------------------------------ |
| Start service            | `.venv\Scripts\python.exe run_service.py start`              |
| Stop service             | `.venv\Scripts\python.exe run_service.py stop`               |
| Restart service          | `.venv\Scripts\python.exe run_service.py restart`            |
| Run in foreground (test) | `deploy\start.bat`  (stop with `deploy\stop.bat`)            |

The service appears as **"Harmony Bonus Policy Simulator"** in services.msc.

---

## Updating to a new version

```bat
cd C:\<path-to-project>
git pull
deploy\setup.bat                      :: re-installs any new dependencies
.venv\Scripts\python.exe run_service.py restart
```

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
- **"Import from STPTM9000" button doesn't appear** — that's by design when
  `STPTM_SQL_SERVER` is unset or unreachable; the rest of the app still
  works. Check `.env`, network access to the SQL Server host, and the ODBC
  driver version.
- **Service won't start** — check Windows Event Viewer (Application log,
  source `BonusPolicySimulator`).
