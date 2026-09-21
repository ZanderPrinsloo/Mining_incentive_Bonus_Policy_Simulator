"""Flask application for the Harmony Bonus Policy Simulator."""
from __future__ import annotations

import logging
import traceback
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, render_template, jsonify, request

from storage import schemes as store
from storage.seed import ensure_templates
from storage import stptm_import
from engine.bonus import calculate

log = logging.getLogger(__name__)


def _scheme_or_404(scheme_id: int) -> dict:
    scheme = store.get_scheme_row(scheme_id)
    if scheme is None:
        raise LookupError(f"Scheme {scheme_id} not found")
    return scheme


def _reject_template_edit(scheme: dict) -> None:
    if scheme.get("is_template"):
        raise ValueError("Templates are read-only. Duplicate this template into your own scenario first.")


def _compute_full_state(scheme_id: int, period_id: int | None = None) -> dict:
    state = store.load_full_state(scheme_id)
    periods = state["periods"]
    if period_id is None and periods:
        period_id = periods[-1]["id"]  # most recent period by default
    active = next((p for p in periods if p["id"] == period_id), None)
    state["active_period_id"] = active["id"] if active else None
    state["computed"] = calculate(active or {}, state["base_cfg"], state["bands"], state["parameters"],
                                   state["curve_points"])
    # Each period also gets its own calculate() result attached, so the client can
    # sum Base Bonus / Total Bonus / per-parameter amounts across a selected period
    # range (Results should match "the real total bonus paid" for that whole range,
    # not just whichever single period happens to be active) — same aggregation
    # pattern already used for Manual Inputs' own combined-total display.
    for p in periods:
        p["computed"] = calculate(p, state["base_cfg"], state["bands"], state["parameters"], state["curve_points"])
    return state


def _period_id_arg() -> int | None:
    return request.args.get("period_id", type=int)


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    ensure_templates()

    @app.errorhandler(LookupError)
    def handle_not_found(e):
        return jsonify({"error": str(e)}), 404

    @app.errorhandler(ValueError)
    def handle_bad_request(e):
        return jsonify({"error": str(e)}), 400

    @app.errorhandler(Exception)
    def handle_exception(e):
        log.error("Unhandled API error:\n%s", traceback.format_exc())
        return jsonify({"error": str(e)}), 500

    @app.route("/")
    def index():
        return render_template("index.html")

    # ── Scheme CRUD ──────────────────────────────────────────────────────────

    @app.route("/api/schemes")
    def api_list_schemes():
        return jsonify(store.list_schemes())

    @app.route("/api/schemes", methods=["POST"])
    def api_create_scheme():
        body = request.get_json(force=True) or {}
        created = store.create_scheme(
            name=body.get("name", ""),
            description=body.get("description", ""),
            section_label=body.get("section_label", ""),
        )
        return jsonify(created), 201

    @app.route("/api/schemes/<int:scheme_id>/clone", methods=["POST"])
    def api_clone_scheme(scheme_id):
        _scheme_or_404(scheme_id)
        body = request.get_json(force=True) or {}
        new_name = body.get("name") or ""
        if not new_name.strip():
            source = store.get_scheme_row(scheme_id)
            new_name = f"{source['name']} (copy)"
        cloned = store.clone_scheme(scheme_id, new_name)
        return jsonify(cloned), 201

    @app.route("/api/schemes/<int:scheme_id>")
    def api_get_scheme(scheme_id):
        _scheme_or_404(scheme_id)
        return jsonify(_compute_full_state(scheme_id, _period_id_arg()))

    @app.route("/api/schemes/<int:scheme_id>/period-summary")
    def api_period_summary(scheme_id):
        """All of a scheme's periods (optionally clamped to ?from=YYYY-MM&to=YYYY-MM),
        each with its own computed result — used to aggregate totals/averages across
        periods for the Compare tab's filtered summary tiles."""
        _scheme_or_404(scheme_id)
        state = store.load_full_state(scheme_id)
        periods = state["periods"]
        from_ = request.args.get("from")
        to_ = request.args.get("to")
        if from_:
            periods = [p for p in periods if p["period"] >= from_]
        if to_:
            periods = [p for p in periods if p["period"] <= to_]
        results = [
            {"period": p, "computed": calculate(p, state["base_cfg"], state["bands"], state["parameters"])}
            for p in periods
        ]
        return jsonify(results)

    @app.route("/api/schemes/<int:scheme_id>", methods=["PUT"])
    def api_update_scheme(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        body = request.get_json(force=True) or {}
        return jsonify(store.update_scheme(scheme_id, **body))

    @app.route("/api/schemes/<int:scheme_id>", methods=["DELETE"])
    def api_delete_scheme(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        store.delete_scheme(scheme_id)
        return jsonify({"ok": True})

    # ── Manual inputs (per period) ───────────────────────────────────────────

    @app.route("/api/schemes/<int:scheme_id>/periods", methods=["POST"])
    def api_create_period(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        body = request.get_json(force=True) or {}
        created = store.create_period(scheme_id, body)
        return jsonify(_compute_full_state(scheme_id, created["id"])), 201

    @app.route("/api/periods/<int:period_id>", methods=["PUT"])
    def api_update_period(period_id):
        body = request.get_json(force=True) or {}
        period = store.update_period(period_id, body)
        return jsonify(_compute_full_state(period["scheme_id"], period_id))

    @app.route("/api/periods/<int:period_id>", methods=["DELETE"])
    def api_delete_period(period_id):
        scheme_id = store.delete_period(period_id)
        if scheme_id:
            return jsonify(_compute_full_state(scheme_id))
        return jsonify({"ok": True})

    # ── Doornkop real-data import (STPTM9000) ────────────────────────────────

    @app.route("/api/doornkop-import/status")
    def api_doornkop_import_status():
        available = stptm_import.is_available()
        periods = stptm_import.list_available_periods() if available else []
        sections = stptm_import.list_sections() if available else []
        return jsonify({"available": available, "periods": periods, "sections": sections})

    def _apply_doornkop_period(scheme_id: int, period: str, data: dict) -> dict:
        """Writes one real STPTM period's Manual Inputs into scheme_periods.
        Does NOT touch parameters — those are scheme-wide, so a multi-period
        import averages across periods once, after all of them are applied
        (see api_doornkop_import_range)."""
        period_fields = {
            "total_sqm": data["total_sqm"],
            "crew_count": data["crew_count"],
            "people_per_crew": data["people_per_crew"],
            "actual_total_bonus": data["actual_total_bonus"],
            "actual_r_per_sqm": data["actual_r_per_sqm"],
            "safety_incidents": data["safety_incidents"],
            "sweepings_distance_m": data["sweepings_distance_m"],
            "stoping_width_cm": data["stoping_width_cm"],
            "quality_blast_count": data["quality_blast_count"],
            "awop_count": data["awop_count"],
            "break_bonus_total": data["break_bonus_total"],
            "safety_bonus_total": data["safety_bonus_total"],
            "driller_bonus_total": data["driller_bonus_total"],
        }
        existing = next((p for p in store.list_periods(scheme_id) if p["period"] == period), None)
        if existing:
            return store.update_period(existing["id"], period_fields)
        return store.create_period(scheme_id, {**period_fields, "period": period})

    def _apply_doornkop_parameter_pcts(scheme_id: int, all_parameter_pcts: list[dict]) -> None:
        """all_parameter_pcts: one {name: pct|None} dict per imported period. Parameters
        are scheme-wide, so with more than one period this sets each one to the average
        of that period's real derived % across all periods where it was derivable —
        the same "average a per-period rate that doesn't sum" rule used elsewhere here.

        Only touches parameters still on basis "pct_base" — a parameter reconfigured to
        "rand_per_unit" (e.g. Safety Bonus linked to safety_bonus_total for an exact-
        match Doornkop scenario) has its own real Rand value; overwriting it with a
        derived % here would silently break that setup on the next re-import.
        """
        for param in store.list_parameters(scheme_id):
            if param.get("basis") != "pct_base":
                continue
            values = [pcts[param["name"]] for pcts in all_parameter_pcts
                      if pcts.get(param["name"]) is not None]
            if values:
                store.update_parameter(param["id"], {"value": round(sum(values) / len(values), 2)})

    @app.route("/api/schemes/<int:scheme_id>/doornkop-import", methods=["POST"])
    def api_doornkop_import(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        if scheme.get("template_key") != "doornkop":
            raise ValueError("Importing real data is only available for Doornkop-derived scenarios.")
        body = request.get_json(force=True) or {}
        period = body.get("period")
        section = body.get("section")
        if not period:
            raise ValueError("Period is required")

        gang_type = scheme.get("gang_type") or "STOPE BREAKING"
        data = stptm_import.fetch_period(period, section, gang_type=gang_type,
                                          scope_actual_to_gang_type=True)
        period_row = _apply_doornkop_period(scheme_id, period, data)
        _apply_doornkop_parameter_pcts(scheme_id, [data["parameter_pcts"]])

        return jsonify(_compute_full_state(scheme_id, period_row["id"]))

    @app.route("/api/schemes/<int:scheme_id>/doornkop-import-range", methods=["POST"])
    def api_doornkop_import_range(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        if scheme.get("template_key") != "doornkop":
            raise ValueError("Importing real data is only available for Doornkop-derived scenarios.")
        body = request.get_json(force=True) or {}
        period_from = body.get("from")
        period_to = body.get("to")
        section = body.get("section")
        if not period_from or not period_to:
            raise ValueError("Both a from and to period are required")
        if period_to < period_from:
            period_from, period_to = period_to, period_from

        available = stptm_import.list_available_periods()
        periods = [p for p in available if period_from <= p <= period_to]
        if not periods:
            raise ValueError(f"No real Doornkop periods found between {period_from} and {period_to}")

        gang_type = scheme.get("gang_type") or "STOPE BREAKING"
        last_period_row = None
        all_pcts = []
        skipped = []
        for period in periods:
            try:
                data = stptm_import.fetch_period(period, section, gang_type=gang_type,
                                                  scope_actual_to_gang_type=True)
            except ValueError:
                skipped.append(period)  # e.g. this section had no activity that month
                continue
            last_period_row = _apply_doornkop_period(scheme_id, period, data)
            all_pcts.append(data["parameter_pcts"])
        if last_period_row is None:
            scope = f" for section {section}" if section and section.upper() != "ALL" else ""
            raise ValueError(f"No real Doornkop data found between {period_from} and {period_to}{scope}")
        _apply_doornkop_parameter_pcts(scheme_id, all_pcts)

        return jsonify(_compute_full_state(scheme_id, last_period_row["id"]))

    # ── Base bonus config ────────────────────────────────────────────────────

    @app.route("/api/schemes/<int:scheme_id>/base-bonus", methods=["PUT"])
    def api_update_base_cfg(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        body = request.get_json(force=True) or {}
        store.update_base_cfg(scheme_id, body)
        return jsonify(_compute_full_state(scheme_id, _period_id_arg()))

    # ── Achievement bands ────────────────────────────────────────────────────

    @app.route("/api/schemes/<int:scheme_id>/bands", methods=["POST"])
    def api_create_band(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        body = request.get_json(force=True) or {}
        store.create_band(scheme_id, body)
        return jsonify(_compute_full_state(scheme_id, _period_id_arg())), 201

    @app.route("/api/bands/<int:band_id>", methods=["PUT"])
    def api_update_band(band_id):
        body = request.get_json(force=True) or {}
        band = store.update_band(band_id, body)
        return jsonify(_compute_full_state(band["scheme_id"], _period_id_arg()))

    @app.route("/api/bands/<int:band_id>", methods=["DELETE"])
    def api_delete_band(band_id):
        conn_scheme_id = request.args.get("scheme_id", type=int)
        store.delete_band(band_id)
        if conn_scheme_id:
            return jsonify(_compute_full_state(conn_scheme_id, _period_id_arg()))
        return jsonify({"ok": True})

    # ── Base Bonus Rate Curve ────────────────────────────────────────────────

    @app.route("/api/schemes/<int:scheme_id>/curve-points", methods=["POST"])
    def api_create_curve_point(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        body = request.get_json(force=True) or {}
        store.create_curve_point(scheme_id, body)
        return jsonify(_compute_full_state(scheme_id, _period_id_arg())), 201

    @app.route("/api/curve-points/<int:point_id>", methods=["PUT"])
    def api_update_curve_point(point_id):
        body = request.get_json(force=True) or {}
        point = store.update_curve_point(point_id, body)
        return jsonify(_compute_full_state(point["scheme_id"], _period_id_arg()))

    @app.route("/api/curve-points/<int:point_id>", methods=["DELETE"])
    def api_delete_curve_point(point_id):
        conn_scheme_id = request.args.get("scheme_id", type=int)
        store.delete_curve_point(point_id)
        if conn_scheme_id:
            return jsonify(_compute_full_state(conn_scheme_id, _period_id_arg()))
        return jsonify({"ok": True})

    # ── Parameters ───────────────────────────────────────────────────────────

    @app.route("/api/schemes/<int:scheme_id>/parameters", methods=["POST"])
    def api_create_parameter(scheme_id):
        scheme = _scheme_or_404(scheme_id)
        _reject_template_edit(scheme)
        body = request.get_json(force=True) or {}
        store.create_parameter(scheme_id, body)
        return jsonify(_compute_full_state(scheme_id, _period_id_arg())), 201

    @app.route("/api/parameters/<int:param_id>", methods=["PUT"])
    def api_update_parameter(param_id):
        body = request.get_json(force=True) or {}
        param = store.update_parameter(param_id, body)
        return jsonify(_compute_full_state(param["scheme_id"], _period_id_arg()))

    @app.route("/api/parameters/<int:param_id>", methods=["DELETE"])
    def api_delete_parameter(param_id):
        scheme_id = request.args.get("scheme_id", type=int)
        store.delete_parameter(param_id)
        if scheme_id:
            return jsonify(_compute_full_state(scheme_id, _period_id_arg()))
        return jsonify({"ok": True})

    # ── Compare ──────────────────────────────────────────────────────────────

    @app.route("/api/compare")
    def api_compare():
        """Side-by-Side comparison. Each scheme contributes the sum of its OWN
        periods that fall inside ?from=&to= (its whole period list if neither is
        given, or its single active period if none of its periods fall in that
        range at all — an empty row would be more confusing than showing what it
        does have). That range sum is then scaled by the scheme's own "periods"
        multiplier (Results tab) — e.g. a manual scenario with one real period
        set to 14 shows as if it covered 14 periods, comparable to a real
        scenario that already has 14 actual months in range. R/m² and R/Employee
        are real rates (bonus ÷ production) and are NOT scaled by the multiplier
        — multiplying both sides of a ratio by the same factor wouldn't change
        it anyway, and scaling only the numerator would inflate the rate.
        """
        ids_param = request.args.get("ids", "")
        ids = [int(x) for x in ids_param.split(",") if x.strip().isdigit()]
        from_ = request.args.get("from") or None
        to_ = request.args.get("to") or None
        results = []
        for sid in ids:
            scheme = store.get_scheme_row(sid)
            if scheme is None:
                continue
            state = _compute_full_state(sid)
            periods = state["periods"]
            in_range = [p for p in periods
                        if (not from_ or p["period"] >= from_) and (not to_ or p["period"] <= to_)]
            active = next((p for p in periods if p["id"] == state["active_period_id"]), None)
            use_periods = in_range or ([active] if active else periods[-1:])
            total_sqm = sum(p["computed"]["inputs_echo"]["total_sqm"] for p in use_periods)
            total_labor = sum(p["computed"]["inputs_echo"]["total_labor"] for p in use_periods)
            real_base = sum(p["computed"]["base_bonus"] for p in use_periods)
            real_total = sum(p["computed"]["total_bonus"] for p in use_periods)
            periods_mult = max(int(state["base_cfg"].get("periods") or 1), 1)
            results.append({
                "scheme": scheme,
                "computed": {
                    "base_bonus": real_base * periods_mult,
                    "total_bonus": real_total * periods_mult,
                    "r_per_sqm": real_total / total_sqm if total_sqm else 0.0,
                    "r_per_man": real_total / total_labor if total_labor else 0.0,
                    "periods_included": len(use_periods),
                    "periods_multiplier": periods_mult,
                },
            })
        return jsonify(results)

    return app
