"""
stptm_import.py
----------------
Optional integration with the real Doornkop payroll/production database
(SQL Server instance "STPTM9000", business unit "RE") for pulling actual
historical figures into a Doornkop-derived scenario, instead of typing
them in by hand. Entirely read-only — never writes back to STPTM9000.

Query logic and formulas here are deliberately copied from, and verified
byte-for-byte against, the separate "Doornkop Stoping Analysis" dashboard
(C:\\Mining_incentive_system_dooringkop_main\\web\\queries.py -
get_kpi_summary / get_bonus_rule_data) — the user's confirmed-correct
source of truth for these numbers. That project is READ-ONLY reference
material here; nothing in this module writes to it or imports from it,
so the two stay independently correct even if one changes later.

Verified 2026-09 against a real multi-month, whole-mine range (STOPE
BREAKING, all sections) — Total m², Distinct Crew Count, Avg m²/Gang,
Total Bonus and R/m² all matched the dashboard exactly (figures omitted
here deliberately — this file is real-schema and, now, public).

Connection settings (STPTM_SQL_SERVER, STPTM_DATABASE, STPTM_BUSSUNIT)
come from the environment / a local .env file (see .env.example) rather
than being hardcoded, so the same code runs unchanged against whichever
server a given deployment points it at.

Field mapping:
  GANGLINKEARN{YYYYMM}.GANGTOTALSQMADJUSTED (STOPE BREAKING only,
      one row kept per gang via MAX)      -> total_sqm, crew_count
  GANGLINKEARN{YYYYMM}.GANGLABOUR                       -> people_per_crew
  PARTICIPANTSEARN{YYYYMM} EMPLOYEESTOPETEAMBONUS +
      EMPLOYEESAFETYBONUS + EMPLOYEEDRILLERBONUS         -> actual_total_bonus
      (ALL gang types, matching the dashboard's own KPI scope — not just
      Stope Breaking; this is deliberately not "cleaned up" to be Stope-
      Breaking-only, to keep matching what the dashboard shows)

Parameter %'s (Stope-Breaking-only, CREWNO length>=8 and != '0', per-gang
values from GANGLINKEARN are PER-MINER rates and must be multiplied by
GANGLABOUR to get gang totals — this is the single biggest mistake this
module had before verification):
  Safety Bonus %       = sum(safety_bonus)  / sum(break_bonus)             * 100, among gangs with safety_bonus>0
  Physical Condition % = sum(condition_bonus)/ sum(break_bonus)            * 100, among gangs with condition_bonus>0
  Stoping Width %      = sum(sw_bonus)      / sum(break_bonus)             * 100, among gangs with sw_bonus>0
  Trigger %             = sum(trigger_bonus) / sum(break+sw+condition)     * 100, among gangs with trigger_bonus>0
(Trigger is % of production bonus i.e. Break+SW+Condition, not of Break
alone — the dashboard's own comments note an earlier version got this
wrong and produced a meaningless ~91% "rate"; this module uses the
corrected formula from the start.)
Sweepings Penalty, Quality Drilling and AWOP Penalty have no matching
real column, so those are left untouched.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # pragma: no cover - python-dotenv not installed
    pass  # fine — real env vars (e.g. set by the deployment) still work without it

try:
    import pyodbc
    _PYODBC_IMPORT_ERROR: Exception | None = None
except ImportError as e:  # pragma: no cover - exercised only when pyodbc isn't installed
    pyodbc = None
    _PYODBC_IMPORT_ERROR = e

# All three are set per-deployment via .env (see .env.example) — this file has no
# hardcoded server, so the same code runs unchanged wherever it's deployed. Blank
# by default: is_available() treats a missing SERVER as "feature not configured
# here" rather than trying (and failing) to connect to a placeholder.
SERVER = os.environ.get("STPTM_SQL_SERVER", "")
DATABASE = os.environ.get("STPTM_DATABASE", "STPTM9000")
BUSSUNIT = os.environ.get("STPTM_BUSSUNIT", "RE")  # confirmed by the user to be Doornkop

PARAM_RATIO_FIELDS = {
    "Safety Bonus": "safety",
    "Physical Condition Bonus": "condition",
    "Stoping Width Bonus": "sw",
    "Trigger Bonus (Difficult Conditions)": "trigger",
}


def _connect():
    if pyodbc is None:
        raise RuntimeError(f"pyodbc is not installed: {_PYODBC_IMPORT_ERROR}")
    return pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={SERVER};DATABASE={DATABASE};"
        "Trusted_Connection=yes;TrustServerCertificate=yes;",
        timeout=5,
    )


def is_available() -> bool:
    """Cheap reachability check — used to decide whether to show the Import
    button at all. False on any error (driver missing, unconfigured/unreachable
    server, wrong machine, etc.) rather than raising, since this runs on every
    Doornkop-scenario page load."""
    if pyodbc is None or not SERVER:
        return False
    try:
        conn = _connect()
        conn.close()
        return True
    except Exception:
        return False


def _table_exists(cur, name: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE UPPER(TABLE_NAME) = ?",
        name.upper(),
    )
    return cur.fetchone()[0] > 0


def list_available_periods() -> list[str]:
    """Periods (as 'YYYY-MM'), most recent first, with a GANGLINKEARN table —
    production/crew data is importable for any of these; actual_total_bonus
    additionally needs a matching PARTICIPANTSEARN table and is left blank
    where that's missing. Capped to the 3 years before the most recent
    available period — the full history goes back to 2012, far more than
    useful in a dropdown."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT UPPER(TABLE_NAME) FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_TYPE = 'BASE TABLE' AND UPPER(TABLE_NAME) LIKE 'GANGLINKEARN2%'"
        )
        periods = []
        for (name,) in cur.fetchall():
            suffix = name.replace("GANGLINKEARN", "")
            if len(suffix) == 6 and suffix.isdigit():
                periods.append(int(suffix))
        if not periods:
            return []
        cutoff = max(periods) - 300  # 3 years back (YYYYMM - 300 = same month, 3 years earlier)
        periods = sorted((p for p in periods if p >= cutoff), reverse=True)
        return [f"{p // 100:04d}-{p % 100:02d}" for p in periods]
    finally:
        conn.close()


def list_sections() -> list[str]:
    """Real Doornkop sub-sections (e.g. REAA, REAB, READ, LEDG) available to
    scope an import to, drawn from the same capped period range as
    list_available_periods() — Stope Breaking only, matching fetch_period."""
    periods = list_available_periods()
    if not periods:
        return []
    conn = _connect()
    try:
        cur = conn.cursor()
        tables = [f"GANGLINKEARN{p.replace('-', '')}" for p in periods]
        union_sql = " UNION ALL ".join(
            f"SELECT DISTINCT LTRIM(RTRIM(SECTION)) AS section FROM [{t}] "
            f"WHERE UPPER(LTRIM(RTRIM(BUSSUNIT))) = '{BUSSUNIT}' "
            f"AND UPPER(LTRIM(RTRIM(GANGTYPE))) = 'STOPE BREAKING'"
            for t in tables
        )
        cur.execute(f"SELECT DISTINCT section FROM ({union_sql}) AS q ORDER BY section")
        return [r[0] for r in cur.fetchall() if r[0]]
    finally:
        conn.close()


def fetch_period(period: str, section: str | None = None) -> dict:
    """period: 'YYYY-MM'. section: one of list_sections()'s real section codes,
    or None/'ALL' for the whole mine. Returns aggregated real figures for that
    period, scoped to GANGTYPE = 'STOPE BREAKING' (matching this app's
    Doornkop template, and the dashboard's own convention for parameter-
    derivation callers), or raises ValueError if nothing was found."""
    stptm_period = period.replace("-", "")
    gang_table = f"GANGLINKEARN{stptm_period}"
    participants_table = f"PARTICIPANTSEARN{stptm_period}"
    use_section = bool(section and section.upper() != "ALL")
    section_val = section.upper() if use_section else None
    section_clause = "AND UPPER(LTRIM(RTRIM(SECTION))) = ?" if use_section else ""

    conn = _connect()
    try:
        cur = conn.cursor()
        if not _table_exists(cur, gang_table):
            raise ValueError(f"No GANGLINKEARN table found for {period}")

        # ── Total m² / Crew Count / Labour — matches get_kpi_summary exactly:
        # one row per (section, gang) via MAX(sqm), Stope Breaking only, no
        # CREWNO filter (that's a get_bonus_rule_data-specific restriction).
        cur.execute(
            f"""
            SELECT LTRIM(RTRIM(SECTION)) AS section, LTRIM(RTRIM(GANG)) AS gang,
                   MAX(TRY_CAST(GANGTOTALSQMADJUSTED AS FLOAT)) AS sqm,
                   MAX(TRY_CAST(GANGLABOUR AS FLOAT)) AS labour
            FROM [{gang_table}]
            WHERE UPPER(LTRIM(RTRIM(BUSSUNIT))) = ?
              AND UPPER(LTRIM(RTRIM(GANGTYPE))) = 'STOPE BREAKING'
              {section_clause}
            GROUP BY LTRIM(RTRIM(SECTION)), LTRIM(RTRIM(GANG))
            """,
            *([BUSSUNIT, section_val] if use_section else [BUSSUNIT]),
        )
        kpi_rows = cur.fetchall()
        if not kpi_rows:
            scope = f" for section {section_val}" if use_section else ""
            raise ValueError(f"No Doornkop Stope Breaking production data found for {period}{scope}")

        total_sqm = sum(r.sqm or 0.0 for r in kpi_rows)
        total_labour = sum(r.labour or 0.0 for r in kpi_rows)
        crew_count = len(kpi_rows)

        # ── Actual Total Bonus Paid — matches _get_participants_bonus exactly:
        # DISTINCT over every PARTICIPANTSEARN column (critical — a narrower
        # DISTINCT silently collapses different employees that happen to share
        # the same bonus amounts, undercounting the total by ~40% in testing),
        # gang != 'xxx', crewno != '-', ALL gang types (this is the dashboard's
        # own KPI scope, not narrowed to Stope Breaking).
        actual_total_bonus = None
        if _table_exists(cur, participants_table):
            cur.execute(
                f"""
                SELECT SUM(team + safety + driller) FROM (
                    SELECT DISTINCT
                        LTRIM(RTRIM(SECTION)) AS section, LTRIM(RTRIM(PERIOD)) AS period,
                        LTRIM(RTRIM(GANG)) AS gang, LTRIM(RTRIM(BUSSUNIT)) AS bussunit,
                        LTRIM(RTRIM(CREWNO)) AS crewno, LTRIM(RTRIM(EMPLOYEE_NO)) AS employee_no,
                        LTRIM(RTRIM(WAGECODE)) AS wagecode, LTRIM(RTRIM(GANGTYPE)) AS gangtype,
                        LTRIM(RTRIM(EMPLOYEEAWOPPENALTY)) AS awop,
                        ISNULL(TRY_CAST(EMPLOYEESTOPETEAMBONUS AS FLOAT), 0) AS team,
                        ISNULL(TRY_CAST(EMPLOYEESAFETYBONUS AS FLOAT), 0) AS safety,
                        ISNULL(TRY_CAST(EMPLOYEEDRILLERBONUS AS FLOAT), 0) AS driller
                    FROM [{participants_table}]
                    WHERE LTRIM(RTRIM(GANG)) != 'xxx' AND LTRIM(RTRIM(CREWNO)) != '-' {section_clause}
                ) AS q
                """,
                *([section_val] if use_section else []),
            )
            actual_total_bonus = cur.fetchone()[0]

        # ── Parameter %'s — matches get_bonus_rule_data exactly: Stope
        # Breaking only, CREWNO length>=8 and != '0', deduped per (section,
        # gang, crewno) via MAX, then *GANGLABOUR (values in GANGLINKEARN's
        # bonus columns are per-miner rates, not gang totals), then a
        # qualifying-gangs-only weighted average (sum/sum, not mean-of-ratios).
        cur.execute(
            f"""
            SELECT LTRIM(RTRIM(SECTION)) AS section, LTRIM(RTRIM(GANG)) AS gang,
                   LTRIM(RTRIM(CREWNO)) AS crewno,
                   MAX(TRY_CAST(GANGLABOUR AS FLOAT)) AS labour,
                   MAX(TRY_CAST(GANGFINALBREAKBONUS AS FLOAT)) AS break_pm,
                   MAX(TRY_CAST(GANGFINALTRIGGERBONUS AS FLOAT)) AS trigger_pm,
                   MAX(TRY_CAST(GANGFINALSTOPEWIDTHBONUS AS FLOAT)) AS sw_pm,
                   MAX(TRY_CAST(GANGFINALCONDITIONBONUS AS FLOAT)) AS condition_pm,
                   MAX(TRY_CAST(GANGFINALSAFETYBONUS AS FLOAT)) AS safety_pm
            FROM [{gang_table}]
            WHERE UPPER(LTRIM(RTRIM(BUSSUNIT))) = ?
              AND UPPER(LTRIM(RTRIM(GANGTYPE))) = 'STOPE BREAKING'
              AND LEN(LTRIM(RTRIM(CREWNO))) >= 8 AND LTRIM(RTRIM(CREWNO)) != '0'
              {section_clause}
            GROUP BY LTRIM(RTRIM(SECTION)), LTRIM(RTRIM(GANG)), LTRIM(RTRIM(CREWNO))
            """,
            *([BUSSUNIT, section_val] if use_section else [BUSSUNIT]),
        )
        gangs = []
        for r in cur.fetchall():
            labour = r.labour or 0.0
            gangs.append({
                "break": (r.break_pm or 0.0) * labour,
                "trigger": (r.trigger_pm or 0.0) * labour,
                "sw": (r.sw_pm or 0.0) * labour,
                "condition": (r.condition_pm or 0.0) * labour,
                "safety": (r.safety_pm or 0.0) * labour,
            })

        def _weighted_pct(key, denom_fn):
            qualifying = [g for g in gangs if g[key] > 0]
            denom = sum(denom_fn(g) for g in qualifying)
            if not denom:
                return None
            return round(sum(g[key] for g in qualifying) / denom * 100, 2)

        parameter_pcts = {
            "Safety Bonus": _weighted_pct("safety", lambda g: g["break"]),
            "Physical Condition Bonus": _weighted_pct("condition", lambda g: g["break"]),
            "Stoping Width Bonus": _weighted_pct("sw", lambda g: g["break"]),
            "Trigger Bonus (Difficult Conditions)": _weighted_pct(
                "trigger", lambda g: g["break"] + g["sw"] + g["condition"]
            ),
        }

        return {
            "period": period,
            "section": section_val or "ALL",
            "total_sqm": round(total_sqm, 2),
            "crew_count": crew_count,
            "people_per_crew": round(total_labour / crew_count, 2) if crew_count else None,
            "actual_total_bonus": round(actual_total_bonus, 2) if actual_total_bonus is not None else None,
            "actual_r_per_sqm": round(actual_total_bonus / total_sqm, 4)
                if actual_total_bonus and total_sqm else None,
            "parameter_pcts": parameter_pcts,
        }
    finally:
        conn.close()
