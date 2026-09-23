# Bonus Policy Simulator

Manual-input bonus scenario simulator for Harmony Gold operations — model what a
bonus payout would be under a candidate set of rates and parameters, without
needing a live production database or per-crew detail.

Built as a standalone sibling to the Doornkop dashboard, reusing its visual
design (Bootstrap 5, navy/gold theme) but with an entirely different data
model: everything is typed in manually, and persisted to a local SQLite file
(`data/simulator.db`) — no SQL Server dependency.

## Quick start

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python run_web.py
```

Or press F5 in VS Code ("Run Bonus Policy Simulator").

## Deployment

The same codebase runs unchanged in two places, switched entirely by `.env` —
no code changes needed to move between them:

- **Local development** (default): `python run_web.py` starts Flask's own dev
  server with the debugger on, at `http://127.0.0.1:5050`. If `.env` points
  `STPTM_SQL_SERVER` at a restored copy of the real database, the Doornkop
  "Import from STPTM9000" feature works locally too, using your own Windows
  login (Trusted Authentication).
- **Deployed on Harmony's server**: set `APP_ENV=production` (in `.env` or the
  real environment) and run the same command — it serves through
  [waitress](https://docs.pylonsproject.org/projects/waitress/) instead (no
  debugger, safe to leave reachable on the network), bound to `0.0.0.0:5050`
  by default. Point `STPTM_SQL_SERVER` at the live database and set
  `STPTM_USERNAME`/`STPTM_PASSWORD` if the account running the app there
  doesn't have a trusted domain identity for it (SQL Authentication instead
  of Trusted Auth). See `.env.example` for every variable.

The app's own data (`data/simulator.db`, the saved scenarios) is separate
from STPTM9000 — that SQL Server connection is only used for the optional
real-data import button, never for the app's own storage.

## How it works

- **Scenario** = one saved simulation: a name, a set of manual production/labour
  inputs, a Base Bonus configuration, optional Achievement Bands, and a list of
  toggleable weighted Parameters.
- **Section Templates** (Doornkop, Joel, Kusasalethu, Masimong, MOAB, Mponeng,
  Phakisa, Target, Tshepong) are read-only starting points seeded from the real
  policy comparison document (`storage/seed.py`). Duplicate one into your own
  scenario to start editing — templates themselves can't be changed or deleted.
  **Their Rand rates are round placeholders, not verified payroll figures** —
  the source comparison document explicitly excludes annexure/rate tables, so
  only each parameter's presence, sign, and (where the policy body states one)
  documented percentage/Rand figure are real; everything else needs your own
  numbers before the output is meaningful. Each parameter's `notes` field cites
  what's actually documented vs assumed.
- **Base Bonus** — computed either from Achievement Bands (say "6 crews paid at
  100%, 4 at 60%" instead of per-crew detail) or, with that toggle off, directly
  from Crew Count × achieved % × Rand rate — use this simpler mode when you only
  have one whole-mine aggregate figure, not a payout distribution.
- **Basis toggle (sqm vs efficiency)** — Base Bonus can qualify against Total m²
  or against m²/employee efficiency, matching Target's real "efficiency only
  pays above 16 m²/employee" rule.
- **Parameters** — each is enabled/disabled, and computed one of four ways:
  % of Base Bonus, % of Total Bonus (for penalties several policies define as
  "of total including driller bonus"), Rand per unit of a linked metric (e.g.
  R100 per quality blast), or a Fixed Rand amount. A "% of Base Bonus"
  parameter can optionally set **Qualifying Crews** — how many of the crews
  that qualified for Base Bonus in Achievement Bands also qualify for that
  specific parameter (e.g. only 6 of 10 crews had zero safety incidents).
  Left blank, the % applies to the whole Base Bonus as before; set, it's
  prorated to just that many crews' share. Instead of typing that count by
  hand, a "% of Base Bonus" parameter can instead **auto-derive it from a
  reference metric** — e.g. Doornkop's Safety Bonus is wired to Safety
  Incidents, so entering 3 incidents automatically drops it to 7 of 10
  qualifying crews, matching the real policy rule that one incident forfeits
  that crew's Safety Bonus outright. The two modes are mutually exclusive
  (Manual count vs. Auto: subtract a metric) — pick one per parameter in the
  Parameters tab.
- **Compare** — pick any set of saved scenarios (including templates) to see
  Base Bonus / Total Bonus / R per m² / R per employee side by side.

## Project structure

```
engine/bonus.py        Pure calculation core (no I/O) — unit tested in tests/
storage/                SQLite persistence, one module per concern
web/app.py              Flask routes
web/templates/index.html Single-page front end (Bootstrap, inline JS)
```
