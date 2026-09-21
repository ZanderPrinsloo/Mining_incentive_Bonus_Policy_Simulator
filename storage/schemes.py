"""
schemes.py
----------
CRUD for schemes (saved scenarios and templates) plus the "load full
state" / "clone" helpers the web layer needs. A scheme's full state is
spread across five tables (schemes, scheme_periods, base_bonus_config,
crew_bands, parameters) but is always read/written as one nested dict
from the API's point of view.

A scheme holds Base Bonus config / Achievement Bands / Parameters once
(the policy definition doesn't change month to month) but Manual Inputs
are per period — scheme_periods has one row per (scheme, month), so
switching periods in the UI swaps in that month's own figures.
"""

from __future__ import annotations

from .db import get_conn, now_iso

PERIOD_FIELDS = [
    "period", "total_sqm", "crew_count", "people_per_crew",
    "actual_total_bonus", "actual_r_per_sqm",
    "safety_incidents", "sweepings_distance_m", "stoping_width_cm",
    "quality_blast_count", "awop_count", "break_bonus_total",
    "safety_bonus_total", "driller_bonus_total",
]
BASE_CFG_FIELDS = ["basis", "threshold", "threshold_bonus", "use_bands", "periods"]
PARAMETER_FIELDS = ["name", "enabled", "basis", "value", "linked_metric", "notes", "sort_order",
                     "qualifying_crews", "gate_metric", "basis_param_ids", "basis_includes_base"]
BAND_FIELDS = ["crew_count", "payout_pct", "achievement_pct", "sort_order"]
CURVE_POINT_FIELDS = ["sqm", "rate", "sort_order"]


def _row(row):
    return dict(row) if row is not None else None


def _parse_basis_param_ids(raw) -> list[int]:
    """basis_param_ids is stored as a comma-separated TEXT column (SQLite has no
    array type) — this parses it back into a list of ints for engine.calculate()
    and the JSON API. Empty/None -> []."""
    if not raw:
        return []
    return [int(x) for x in str(raw).split(",") if x.strip().lstrip("-").isdigit()]


def _serialize_basis_param_ids(value) -> str | None:
    if value is None:
        return None
    ids = [int(x) for x in value if str(x).strip() != ""]
    return ",".join(str(x) for x in ids)


def _param_row(row):
    d = _row(row)
    if d is not None:
        d["basis_param_ids"] = _parse_basis_param_ids(d.get("basis_param_ids"))
        # SQLite has no bool type — these come back as 0/1 ints. JSON-serializing
        # them as 0/1 rather than true/false caused a real bug: the frontend's
        # `p.basis_includes_base !== false` check is a strict comparison, and in
        # JS `0 !== false` is true (different types never match `!==`), so a
        # parameter saved with Include Base Bonus OFF still rendered the checkbox
        # as checked while the engine correctly computed a 0 pool — a visible
        # mismatch between what the UI showed and what actually got calculated.
        # Converting to real bools here means the JSON is unambiguous either way.
        d["enabled"] = bool(d.get("enabled"))
        d["basis_includes_base"] = bool(d.get("basis_includes_base"))
    return d


def _with_period_range(conn, scheme: dict) -> dict:
    r = conn.execute(
        "SELECT MIN(period) AS pf, MAX(period) AS pt FROM scheme_periods WHERE scheme_id = ?",
        (scheme["id"],),
    ).fetchone()
    scheme["period_from"] = r["pf"]
    scheme["period_to"] = r["pt"]
    return scheme


def list_schemes(include_templates: bool = True) -> list[dict]:
    conn = get_conn()
    try:
        if include_templates:
            rows = conn.execute("SELECT * FROM schemes ORDER BY is_template DESC, updated_at DESC").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM schemes WHERE is_template = 0 ORDER BY updated_at DESC"
            ).fetchall()
        # Period From/To shown to the user is the real range covered by that scheme's
        # stored periods, not the (now vestigial) manually-set schemes.period_from/to.
        return [_with_period_range(conn, dict(r)) for r in rows]
    finally:
        conn.close()


def get_scheme_row(scheme_id: int) -> dict | None:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM schemes WHERE id = ?", (scheme_id,)).fetchone()
        if row is None:
            return None
        return _with_period_range(conn, dict(row))
    finally:
        conn.close()


def _ensure_base_cfg_row(conn, scheme_id: int):
    conn.execute(
        "INSERT OR IGNORE INTO base_bonus_config (scheme_id) VALUES (?)", (scheme_id,)
    )


def create_scheme(name: str, description: str = "", section_label: str = "",
                   is_template: bool = False, template_key: str | None = None,
                   template_note: str | None = None, crew_type: str | None = None,
                   period_from: str | None = None, period_to: str | None = None,
                   gang_type: str | None = None, sibling_group: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("Scheme name is required")
    conn = get_conn()
    try:
        ts = now_iso()
        cur = conn.execute(
            """INSERT INTO schemes (name, description, section_label, crew_type, period_from,
                                     period_to, is_template, template_key, template_note,
                                     gang_type, sibling_group, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, description, section_label, crew_type, period_from, period_to,
             int(is_template), template_key, template_note, gang_type, sibling_group, ts, ts),
        )
        scheme_id = cur.lastrowid
        _ensure_base_cfg_row(conn, scheme_id)
        conn.commit()
        return get_scheme_row(scheme_id)
    finally:
        conn.close()


def update_scheme(scheme_id: int, **fields) -> dict:
    allowed = {"name", "description", "section_label", "crew_type", "period_from", "period_to"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_scheme_row(scheme_id)
    conn = get_conn()
    try:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE schemes SET {set_clause}, updated_at = ? WHERE id = ?",
            (*updates.values(), now_iso(), scheme_id),
        )
        conn.commit()
        return get_scheme_row(scheme_id)
    finally:
        conn.close()


def touch_scheme(conn, scheme_id: int):
    conn.execute("UPDATE schemes SET updated_at = ? WHERE id = ?", (now_iso(), scheme_id))


def delete_scheme(scheme_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM schemes WHERE id = ?", (scheme_id,))
        conn.commit()
    finally:
        conn.close()


def list_periods(scheme_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scheme_periods WHERE scheme_id = ? ORDER BY period", (scheme_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_period(period_id: int) -> dict | None:
    conn = get_conn()
    try:
        return _row(conn.execute("SELECT * FROM scheme_periods WHERE id = ?", (period_id,)).fetchone())
    finally:
        conn.close()


def create_period(scheme_id: int, fields: dict) -> dict:
    period = (fields.get("period") or "").strip()
    if not period:
        raise ValueError("Period (month) is required")
    data = {k: fields.get(k) for k in PERIOD_FIELDS if k != "period"}
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id FROM scheme_periods WHERE scheme_id = ? AND period = ?", (scheme_id, period)
        ).fetchone()
        if existing is not None:
            raise ValueError(f"Period {period} already exists for this scenario")
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        cur = conn.execute(
            f"INSERT INTO scheme_periods (scheme_id, period, {cols}, created_at) "
            f"VALUES (?, ?, {placeholders}, ?)",
            (scheme_id, period, *data.values(), now_iso()),
        )
        touch_scheme(conn, scheme_id)
        conn.commit()
        return _row(conn.execute("SELECT * FROM scheme_periods WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def update_period(period_id: int, fields: dict) -> dict:
    updates = {k: v for k, v in fields.items() if k in PERIOD_FIELDS}
    conn = get_conn()
    try:
        row = conn.execute("SELECT scheme_id, period FROM scheme_periods WHERE id = ?", (period_id,)).fetchone()
        if row is None:
            raise ValueError("Period not found")
        if "period" in updates:
            new_period = (updates["period"] or "").strip()
            if not new_period:
                raise ValueError("Period (month) is required")
            clash = conn.execute(
                "SELECT id FROM scheme_periods WHERE scheme_id = ? AND period = ? AND id != ?",
                (row["scheme_id"], new_period, period_id),
            ).fetchone()
            if clash is not None:
                raise ValueError(f"Period {new_period} already exists for this scenario")
            updates["period"] = new_period
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE scheme_periods SET {set_clause} WHERE id = ?", (*updates.values(), period_id))
            touch_scheme(conn, row["scheme_id"])
        conn.commit()
        return _row(conn.execute("SELECT * FROM scheme_periods WHERE id = ?", (period_id,)).fetchone())
    finally:
        conn.close()


def delete_period(period_id: int) -> int | None:
    """Deletes a period and returns its scheme_id (or None if it didn't exist)."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT scheme_id FROM scheme_periods WHERE id = ?", (period_id,)).fetchone()
        if row is None:
            return None
        scheme_id = row["scheme_id"]
        conn.execute("DELETE FROM scheme_periods WHERE id = ?", (period_id,))
        touch_scheme(conn, scheme_id)
        conn.commit()
        return scheme_id
    finally:
        conn.close()


def get_base_cfg(scheme_id: int) -> dict:
    conn = get_conn()
    try:
        _ensure_base_cfg_row(conn, scheme_id)
        conn.commit()
        return _row(conn.execute("SELECT * FROM base_bonus_config WHERE scheme_id = ?", (scheme_id,)).fetchone())
    finally:
        conn.close()


def update_base_cfg(scheme_id: int, fields: dict) -> dict:
    updates = {k: v for k, v in fields.items() if k in BASE_CFG_FIELDS}
    if "basis" in updates and updates["basis"] not in ("sqm", "efficiency", "break_bonus", "curve"):
        updates.pop("basis")
    if "use_bands" in updates:
        updates["use_bands"] = int(bool(updates["use_bands"]))
    if "periods" in updates:
        try:
            updates["periods"] = max(int(updates["periods"]), 1)
        except (TypeError, ValueError):
            updates.pop("periods")
    conn = get_conn()
    try:
        _ensure_base_cfg_row(conn, scheme_id)
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(
                f"UPDATE base_bonus_config SET {set_clause} WHERE scheme_id = ?",
                (*updates.values(), scheme_id),
            )
            touch_scheme(conn, scheme_id)
        conn.commit()
        return _row(conn.execute("SELECT * FROM base_bonus_config WHERE scheme_id = ?", (scheme_id,)).fetchone())
    finally:
        conn.close()


def list_bands(scheme_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM crew_bands WHERE scheme_id = ? ORDER BY sort_order, id", (scheme_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_band(scheme_id: int, fields: dict) -> dict:
    data = {k: fields.get(k, 0) for k in BAND_FIELDS}
    data["achievement_pct"] = fields.get("achievement_pct", 100)
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO crew_bands (scheme_id, crew_count, payout_pct, achievement_pct, sort_order, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (scheme_id, data["crew_count"] or 0, data["payout_pct"] or 0, data["achievement_pct"],
             data["sort_order"] or 0, now_iso()),
        )
        touch_scheme(conn, scheme_id)
        conn.commit()
        return _row(conn.execute("SELECT * FROM crew_bands WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def update_band(band_id: int, fields: dict) -> dict:
    updates = {k: v for k, v in fields.items() if k in BAND_FIELDS}
    conn = get_conn()
    try:
        row = conn.execute("SELECT scheme_id FROM crew_bands WHERE id = ?", (band_id,)).fetchone()
        if row is None:
            raise ValueError("Band not found")
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE crew_bands SET {set_clause} WHERE id = ?", (*updates.values(), band_id))
            touch_scheme(conn, row["scheme_id"])
        conn.commit()
        return _row(conn.execute("SELECT * FROM crew_bands WHERE id = ?", (band_id,)).fetchone())
    finally:
        conn.close()


def delete_band(band_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM crew_bands WHERE id = ?", (band_id,))
        conn.commit()
    finally:
        conn.close()


def list_curve_points(scheme_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM base_bonus_curve_points WHERE scheme_id = ? ORDER BY sort_order, sqm, id",
            (scheme_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def create_curve_point(scheme_id: int, fields: dict) -> dict:
    data = {k: fields.get(k, 0) for k in CURVE_POINT_FIELDS}
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO base_bonus_curve_points (scheme_id, sqm, labour, rate, sort_order, created_at) "
            "VALUES (?, ?, 0, ?, ?, ?)",
            (scheme_id, data["sqm"] or 0, data["rate"] or 0, data["sort_order"] or 0, now_iso()),
        )
        touch_scheme(conn, scheme_id)
        conn.commit()
        return _row(conn.execute("SELECT * FROM base_bonus_curve_points WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def update_curve_point(point_id: int, fields: dict) -> dict:
    updates = {k: v for k, v in fields.items() if k in CURVE_POINT_FIELDS}
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT scheme_id FROM base_bonus_curve_points WHERE id = ?", (point_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Curve point not found")
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE base_bonus_curve_points SET {set_clause} WHERE id = ?", (*updates.values(), point_id))
            touch_scheme(conn, row["scheme_id"])
        conn.commit()
        return _row(conn.execute("SELECT * FROM base_bonus_curve_points WHERE id = ?", (point_id,)).fetchone())
    finally:
        conn.close()


def delete_curve_point(point_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM base_bonus_curve_points WHERE id = ?", (point_id,))
        conn.commit()
    finally:
        conn.close()


def list_parameters(scheme_id: int) -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM parameters WHERE scheme_id = ? ORDER BY sort_order, id", (scheme_id,)
        ).fetchall()
        return [_param_row(r) for r in rows]
    finally:
        conn.close()


def create_parameter(scheme_id: int, fields: dict) -> dict:
    name = (fields.get("name") or "Parameter").strip() or "Parameter"
    basis = fields.get("basis") or "pct_base"
    conn = get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO parameters (scheme_id, name, enabled, basis, value, linked_metric, notes,
                                        sort_order, qualifying_crews, gate_metric, basis_param_ids,
                                        basis_includes_base, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scheme_id, name, int(bool(fields.get("enabled", True))), basis,
                fields.get("value", 0) or 0, fields.get("linked_metric"), fields.get("notes"),
                fields.get("sort_order", 0) or 0, fields.get("qualifying_crews"),
                fields.get("gate_metric"), _serialize_basis_param_ids(fields.get("basis_param_ids")),
                int(bool(fields.get("basis_includes_base", True))), now_iso(),
            ),
        )
        touch_scheme(conn, scheme_id)
        conn.commit()
        return _param_row(conn.execute("SELECT * FROM parameters WHERE id = ?", (cur.lastrowid,)).fetchone())
    finally:
        conn.close()


def update_parameter(param_id: int, fields: dict) -> dict:
    updates = {k: v for k, v in fields.items() if k in PARAMETER_FIELDS}
    if "enabled" in updates:
        updates["enabled"] = int(bool(updates["enabled"]))
    if "basis_includes_base" in updates:
        updates["basis_includes_base"] = int(bool(updates["basis_includes_base"]))
    if "basis_param_ids" in updates:
        updates["basis_param_ids"] = _serialize_basis_param_ids(updates["basis_param_ids"])
    conn = get_conn()
    try:
        row = conn.execute("SELECT scheme_id FROM parameters WHERE id = ?", (param_id,)).fetchone()
        if row is None:
            raise ValueError("Parameter not found")
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE parameters SET {set_clause} WHERE id = ?", (*updates.values(), param_id))
            touch_scheme(conn, row["scheme_id"])
        conn.commit()
        return _param_row(conn.execute("SELECT * FROM parameters WHERE id = ?", (param_id,)).fetchone())
    finally:
        conn.close()


def delete_parameter(param_id: int) -> None:
    conn = get_conn()
    try:
        conn.execute("DELETE FROM parameters WHERE id = ?", (param_id,))
        conn.commit()
    finally:
        conn.close()


DEFAULT_PERIOD = "default"  # sentinel period key for non-Doornkop scenarios (see _ensure_default_period)


def _ensure_default_period(conn, scheme_id: int, is_template: bool, template_key: str | None) -> None:
    """Non-Doornkop scenarios have no real per-month data source, so instead of
    making the user click "Add Period" and pick a calendar month before they can
    enter anything, they get exactly one always-there period. Templates get none
    (never edited directly); Doornkop-derived scenarios manage their own real
    months via the STPTM9000 import range picker, so this only fires for
    everything else.
    """
    if is_template or template_key == "doornkop":
        return
    count = conn.execute(
        "SELECT COUNT(*) FROM scheme_periods WHERE scheme_id = ?", (scheme_id,)
    ).fetchone()[0]
    if count == 0:
        conn.execute(
            "INSERT INTO scheme_periods (scheme_id, period, created_at) VALUES (?, ?, ?)",
            (scheme_id, DEFAULT_PERIOD, now_iso()),
        )


def load_full_state(scheme_id: int) -> dict:
    scheme = get_scheme_row(scheme_id)
    if scheme is None:
        raise ValueError("Scheme not found")
    conn = get_conn()
    try:
        _ensure_default_period(conn, scheme_id, bool(scheme["is_template"]), scheme.get("template_key"))
        conn.commit()
    finally:
        conn.close()
    return {
        "scheme": scheme,
        "periods": list_periods(scheme_id),
        "base_cfg": get_base_cfg(scheme_id),
        "bands": list_bands(scheme_id),
        "curve_points": list_curve_points(scheme_id),
        "parameters": list_parameters(scheme_id),
    }


def clone_scheme(source_id: int, new_name: str) -> dict:
    """Duplicate a scheme (typically a template) into a new, editable, non-template scheme."""
    state = load_full_state(source_id)
    created = create_scheme(
        name=new_name,
        description=state["scheme"].get("description") or "",
        section_label=state["scheme"].get("section_label") or "",
        is_template=False,
        template_key=state["scheme"].get("template_key"),
        template_note=state["scheme"].get("template_note"),
        crew_type=state["scheme"].get("crew_type"),
    )
    new_id = created["id"]
    for period in state["periods"]:
        create_period(new_id, period)
    update_base_cfg(new_id, {k: state["base_cfg"].get(k) for k in BASE_CFG_FIELDS})
    for b in state["bands"]:
        create_band(new_id, b)
    for cp in state["curve_points"]:
        create_curve_point(new_id, cp)
    new_params = [create_parameter(new_id, p) for p in state["parameters"]]
    # basis_param_ids on the source scheme point at ITS parameter rows — meaningless
    # (or worse, silently pointing at an unrelated scheme's parameter) once copied
    # verbatim, since parameter ids are global, not per-scheme. Remap old id -> new
    # id by position (create_parameter preserves state["parameters"]' order) and
    # rewrite basis_param_ids to match, same pattern as seed.py's ensure_templates().
    old_to_new_id = {old["id"]: new["id"] for old, new in zip(state["parameters"], new_params)}
    for old, new in zip(state["parameters"], new_params):
        old_refs = old.get("basis_param_ids") or []
        if old_refs:
            new_refs = [old_to_new_id[r] for r in old_refs if r in old_to_new_id]
            update_parameter(new["id"], {"basis_param_ids": new_refs})
    return get_scheme_row(new_id)
