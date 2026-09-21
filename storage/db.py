"""
db.py
-----
SQLite schema + connection helper. One local file, no server, no ORM.
Schema is bootstrapped idempotently on every get_conn() call. `_migrate`
handles the rare case of a column/table rename or addition on a database
that predates the change, via ALTER TABLE rather than drop/recreate, so
existing scenario data survives.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime, timezone

ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = ROOT_DIR / "data" / "simulator.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schemes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    section_label TEXT,
    crew_type TEXT,
    period_from TEXT,
    period_to TEXT,
    is_template INTEGER NOT NULL DEFAULT 0,
    template_key TEXT,
    template_note TEXT,
    gang_type TEXT,
    sibling_group TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheme_inputs (
    scheme_id INTEGER PRIMARY KEY REFERENCES schemes(id) ON DELETE CASCADE,
    total_sqm REAL,
    crew_count REAL,
    people_per_crew REAL,
    actual_total_bonus REAL,
    actual_r_per_sqm REAL,
    safety_incidents REAL,
    sweepings_distance_m REAL,
    stoping_width_cm REAL,
    quality_blast_count REAL,
    awop_count REAL
);

-- Superseded scheme_inputs: a scheme now holds one row of Manual Inputs per
-- period (month) rather than a single snapshot, so Period From/To in the
-- Compare tab can filter real per-period data instead of a manual label.
CREATE TABLE IF NOT EXISTS scheme_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id INTEGER NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    period TEXT NOT NULL,
    total_sqm REAL,
    crew_count REAL,
    people_per_crew REAL,
    actual_total_bonus REAL,
    actual_r_per_sqm REAL,
    safety_incidents REAL,
    sweepings_distance_m REAL,
    stoping_width_cm REAL,
    quality_blast_count REAL,
    awop_count REAL,
    break_bonus_total REAL,
    safety_bonus_total REAL,
    driller_bonus_total REAL,
    created_at TEXT NOT NULL,
    UNIQUE(scheme_id, period)
);
CREATE INDEX IF NOT EXISTS idx_scheme_periods_scheme ON scheme_periods(scheme_id);

CREATE TABLE IF NOT EXISTS base_bonus_config (
    scheme_id INTEGER PRIMARY KEY REFERENCES schemes(id) ON DELETE CASCADE,
    basis TEXT NOT NULL DEFAULT 'sqm',
    threshold REAL NOT NULL DEFAULT 0,
    threshold_bonus REAL NOT NULL DEFAULT 0,
    periods INTEGER NOT NULL DEFAULT 1,
    use_bands INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS crew_bands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id INTEGER NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    crew_count REAL NOT NULL DEFAULT 0,
    payout_pct REAL NOT NULL DEFAULT 0,
    achievement_pct REAL NOT NULL DEFAULT 100,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_crew_bands_scheme ON crew_bands(scheme_id);

-- Base Bonus "Rate Curve": Rand-per-employee as a function of (crew m², crew
-- labour size), editable points with interpolation between them (see
-- engine.bonus's BASIS_CURVE) — the real Doornkop policy pays this way (a
-- non-linear lookup matrix), not a flat Rand/m² rate; these points default to
-- values empirically derived from real STPTM9000 paid bonuses, grouped by
-- (m², labour), so they start close to reality and stay fully editable to
-- model policy changes.
CREATE TABLE IF NOT EXISTS base_bonus_curve_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id INTEGER NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    sqm REAL NOT NULL DEFAULT 0,
    labour REAL NOT NULL DEFAULT 0,
    rate REAL NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_base_bonus_curve_points_scheme ON base_bonus_curve_points(scheme_id);

CREATE TABLE IF NOT EXISTS parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scheme_id INTEGER NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT 'Parameter',
    enabled INTEGER NOT NULL DEFAULT 1,
    basis TEXT NOT NULL DEFAULT 'pct_base',
    value REAL NOT NULL DEFAULT 0,
    linked_metric TEXT,
    notes TEXT,
    sort_order INTEGER NOT NULL DEFAULT 0,
    qualifying_crews REAL,
    gate_metric TEXT,
    basis_param_ids TEXT,
    basis_includes_base INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_parameters_scheme ON parameters(scheme_id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _migrate(conn: sqlite3.Connection) -> None:
    """One-off, idempotent upgrades for databases created before a schema change.
    Uses ALTER TABLE (not drop/recreate) so existing scenario data survives.
    """
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "gang_bands" in tables and "crew_bands" not in tables:
        conn.execute("ALTER TABLE gang_bands RENAME TO crew_bands")
        conn.execute("DROP INDEX IF EXISTS idx_gang_bands_scheme")
        tables.discard("gang_bands")
        tables.add("crew_bands")
    if "crew_bands" in tables:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(crew_bands)")}
        if "gang_count" in cols and "crew_count" not in cols:
            conn.execute("ALTER TABLE crew_bands RENAME COLUMN gang_count TO crew_count")
        if "achievement_pct" not in cols:
            conn.execute("ALTER TABLE crew_bands ADD COLUMN achievement_pct REAL NOT NULL DEFAULT 100")
    if "parameters" in tables:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(parameters)")}
        if "qualifying_crews" not in cols:
            conn.execute("ALTER TABLE parameters ADD COLUMN qualifying_crews REAL")
        if "gate_metric" not in cols:
            conn.execute("ALTER TABLE parameters ADD COLUMN gate_metric TEXT")
        if "basis_param_ids" not in cols:
            conn.execute("ALTER TABLE parameters ADD COLUMN basis_param_ids TEXT")
        if "basis_includes_base" not in cols:
            conn.execute("ALTER TABLE parameters ADD COLUMN basis_includes_base INTEGER NOT NULL DEFAULT 1")
    if "schemes" in tables:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(schemes)")}
        if "crew_type" not in cols:
            conn.execute("ALTER TABLE schemes ADD COLUMN crew_type TEXT")
        if "period_from" not in cols:
            conn.execute("ALTER TABLE schemes ADD COLUMN period_from TEXT")
        if "period_to" not in cols:
            conn.execute("ALTER TABLE schemes ADD COLUMN period_to TEXT")
        if "gang_type" not in cols:
            conn.execute("ALTER TABLE schemes ADD COLUMN gang_type TEXT")
        if "sibling_group" not in cols:
            conn.execute("ALTER TABLE schemes ADD COLUMN sibling_group TEXT")
    if "scheme_periods" in tables:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(scheme_periods)")}
        if "break_bonus_total" not in cols:
            conn.execute("ALTER TABLE scheme_periods ADD COLUMN break_bonus_total REAL")
        if "safety_bonus_total" not in cols:
            conn.execute("ALTER TABLE scheme_periods ADD COLUMN safety_bonus_total REAL")
        if "driller_bonus_total" not in cols:
            conn.execute("ALTER TABLE scheme_periods ADD COLUMN driller_bonus_total REAL")
    if "scheme_periods" not in tables and "scheme_inputs" in tables:
        conn.execute("""
            CREATE TABLE scheme_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scheme_id INTEGER NOT NULL REFERENCES schemes(id) ON DELETE CASCADE,
                period TEXT NOT NULL,
                total_sqm REAL, crew_count REAL, people_per_crew REAL,
                actual_total_bonus REAL, actual_r_per_sqm REAL,
                safety_incidents REAL, sweepings_distance_m REAL, stoping_width_cm REAL,
                quality_blast_count REAL, awop_count REAL, break_bonus_total REAL,
                safety_bonus_total REAL, driller_bonus_total REAL,
                created_at TEXT NOT NULL,
                UNIQUE(scheme_id, period)
            )
        """)
        conn.execute("CREATE INDEX idx_scheme_periods_scheme ON scheme_periods(scheme_id)")
        # Fold each scheme's single old scheme_inputs snapshot into one period row, so
        # existing Manual Inputs data survives instead of appearing to vanish. Uses the
        # scheme's own period_from label if it was set, else this month, as the period key.
        ts = now_iso()
        this_month = ts[:7]
        rows = conn.execute("""
            SELECT si.*, s.period_from AS scheme_period_from
            FROM scheme_inputs si JOIN schemes s ON s.id = si.scheme_id
        """).fetchall()
        data_cols = ("total_sqm", "crew_count", "people_per_crew", "actual_total_bonus",
                     "actual_r_per_sqm", "safety_incidents", "sweepings_distance_m",
                     "stoping_width_cm", "quality_blast_count", "awop_count")
        for r in rows:
            if all(r[c] is None for c in data_cols):
                continue  # nothing was ever entered (e.g. a template) — no period to migrate
            period = r["scheme_period_from"] or this_month
            conn.execute(
                """INSERT OR IGNORE INTO scheme_periods
                   (scheme_id, period, total_sqm, crew_count, people_per_crew,
                    actual_total_bonus, actual_r_per_sqm, safety_incidents,
                    sweepings_distance_m, stoping_width_cm, quality_blast_count,
                    awop_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (r["scheme_id"], period, r["total_sqm"], r["crew_count"], r["people_per_crew"],
                 r["actual_total_bonus"], r["actual_r_per_sqm"], r["safety_incidents"],
                 r["sweepings_distance_m"], r["stoping_width_cm"], r["quality_blast_count"],
                 r["awop_count"], ts),
            )


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Migrate first: renames/adds must run against whatever old tables exist
    # before executescript's CREATE TABLE IF NOT EXISTS can pre-empt them by
    # creating an empty table under the new name.
    _migrate(conn)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
