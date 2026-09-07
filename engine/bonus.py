"""
bonus.py
--------
Pure calculation core for the Bonus Policy Simulator. No I/O, no Flask,
no sqlite imports — takes plain dicts/lists in, returns a plain dict out,
so it can be unit tested in isolation and never needs a schema migration
to change how a number is computed.

Model
-----
1. Base Bonus is computed either from Achievement Bands (a distribution of
   crews across payout-% tiers — the aggregate-friendly stand-in for
   per-crew detail) or, if no bands are supplied, directly from a single
   achieved-% times the crew count.
2. Parameters are additional bonus/penalty line items layered on top of
   Base Bonus. Each has a `basis` that determines what its `value` means:
     - "pct_base"      value = % of Base Bonus (can be negative). If the
                        parameter also sets `qualifying_crews`, only the
                        share of Base Bonus attributable to that many
                        crews gets the %, rather than the whole thing —
                        e.g. a Safety Bonus that only 6 of 10 qualifying
                        crews actually earn. `gate_metric` is a second,
                        higher-precedence way to say the same thing:
                        instead of typing a manual crew count, pick a
                        reference input metric (e.g. Safety Incidents)
                        and qualifying crews is derived as
                        (crews that qualified for Base Bonus − that
                        metric's value), clamped to zero — one incident
                        forfeits one crew's share, per policies that
                        forfeit a crew's Safety Bonus entirely on any
                        recorded incident.
     - "pct_total"     value = % of the running total *after* all
                        pct_base / rand_per_unit / fixed parameters have
                        been applied (mirrors real policies like Phakisa's
                        AWOP, which is "calculated on total bonus
                        including driller bonus")
     - "rand_per_unit" value = Rand amount per unit of a linked manual
                        input metric (e.g. R100 per quality blast)
     - "fixed"         value = a flat Rand amount, unscaled
3. `periods` projects the totals (not the rates) over N periods, matching
   how these policies talk about "monthly" figures being extrapolated to
   an annual run rate.
"""

from __future__ import annotations

from typing import Any


PCT_BASE = "pct_base"
PCT_TOTAL = "pct_total"
RAND_PER_UNIT = "rand_per_unit"
FIXED = "fixed"
VALID_BASES = {PCT_BASE, PCT_TOTAL, RAND_PER_UNIT, FIXED}

BASIS_SQM = "sqm"
BASIS_EFFICIENCY = "efficiency"


def _num(value: Any) -> float:
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def compute_efficiency(total_sqm: float, total_labor: float) -> float:
    return total_sqm / total_labor if total_labor else 0.0


def achieved_pct_from_metric(metric_value: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.0
    return (metric_value / threshold) * 100.0


def calculate(inputs: dict, base_cfg: dict, bands: list[dict], parameters: list[dict]) -> dict:
    """Run a full simulation. See module docstring for the model.

    inputs: dict of manual metric values (total_sqm, crew_count,
        people_per_crew, and any of the optional reference metrics —
        keys are free-form so `rand_per_unit` parameters can link to any
        of them, including ones this module doesn't know about).
    base_cfg: {basis, threshold, threshold_bonus, periods, use_bands}
    bands: [{crew_count, payout_pct}, ...] (ignored if use_bands is false)
    parameters: [{id, name, enabled, basis, value, linked_metric,
        qualifying_crews}, ...]
    """
    total_sqm = _num(inputs.get("total_sqm"))
    crew_count = _num(inputs.get("crew_count"))
    people_per_crew = _num(inputs.get("people_per_crew"))
    total_labor = crew_count * people_per_crew
    efficiency = compute_efficiency(total_sqm, total_labor)

    basis = base_cfg.get("basis") or BASIS_SQM
    threshold = _num(base_cfg.get("threshold"))
    threshold_bonus = _num(base_cfg.get("threshold_bonus"))
    periods = max(int(_num(base_cfg.get("periods")) or 1), 1)
    use_bands = bool(base_cfg.get("use_bands", True)) and bool(bands)

    metric_value = total_sqm if basis == BASIS_SQM else efficiency
    achieved_pct = achieved_pct_from_metric(metric_value, threshold)

    band_results = []
    if use_bands:
        base_bonus = 0.0
        bands_total_crews = 0.0
        for b in bands:
            band_crew_count = _num(b.get("crew_count"))
            payout_pct = _num(b.get("payout_pct"))
            amount = threshold_bonus * (payout_pct / 100.0) * band_crew_count
            base_bonus += amount
            bands_total_crews += band_crew_count
            band_results.append({
                "id": b.get("id"),
                "crew_count": band_crew_count,
                "payout_pct": payout_pct,
                "amount": amount,
            })
        bands_total_crews = bands_total_crews or crew_count
    else:
        base_bonus = crew_count * (achieved_pct / 100.0) * threshold_bonus
        bands_total_crews = crew_count

    base_bonus_per_crew = base_bonus / bands_total_crews if bands_total_crews else 0.0

    # Stage 1: pct_base / rand_per_unit / fixed, all layered directly on Base Bonus.
    stage1_total = 0.0
    param_results = []
    pct_total_queue = []
    for p in parameters:
        enabled = bool(p.get("enabled", True))
        pbasis = p.get("basis") or PCT_BASE
        value = _num(p.get("value"))
        linked_metric = p.get("linked_metric")
        qualifying_crews = p.get("qualifying_crews")
        has_qualifying_crews = qualifying_crews not in (None, "")
        gate_metric = p.get("gate_metric") or None
        gate_metric_qty = None
        effective_qualifying_crews = None
        amount = 0.0
        qty = None
        if enabled:
            if pbasis == PCT_BASE:
                if gate_metric:
                    gate_metric_qty = _num(inputs.get(gate_metric))
                    effective_qualifying_crews = max(bands_total_crews - gate_metric_qty, 0.0)
                    amount = base_bonus_per_crew * effective_qualifying_crews * (value / 100.0)
                elif has_qualifying_crews:
                    effective_qualifying_crews = _num(qualifying_crews)
                    amount = base_bonus_per_crew * effective_qualifying_crews * (value / 100.0)
                else:
                    amount = base_bonus * (value / 100.0)
            elif pbasis == RAND_PER_UNIT:
                qty = _num(inputs.get(linked_metric))
                amount = qty * value
            elif pbasis == FIXED:
                amount = value
            elif pbasis == PCT_TOTAL:
                amount = 0.0  # resolved in stage 2, once the running total is known
        if enabled and pbasis != PCT_TOTAL:
            stage1_total += amount
        entry = {
            "id": p.get("id"),
            "name": p.get("name"),
            "enabled": enabled,
            "basis": pbasis,
            "value": value,
            "linked_metric": linked_metric,
            "linked_metric_qty": qty,
            "qualifying_crews": _num(qualifying_crews) if has_qualifying_crews else None,
            "gate_metric": gate_metric,
            "gate_metric_qty": gate_metric_qty,
            "effective_qualifying_crews": effective_qualifying_crews,
            "amount": amount,
        }
        param_results.append(entry)
        if enabled and pbasis == PCT_TOTAL:
            pct_total_queue.append(entry)

    running_total = base_bonus + stage1_total

    stage2_total = 0.0
    for entry in pct_total_queue:
        amount = running_total * (entry["value"] / 100.0)
        entry["amount"] = amount
        stage2_total += amount

    total_bonus = running_total + stage2_total
    other_total = stage1_total + stage2_total

    r_per_sqm = total_bonus / total_sqm if total_sqm else 0.0
    r_per_man = total_bonus / total_labor if total_labor else 0.0

    projected_base_bonus = base_bonus * periods
    projected_total_bonus = total_bonus * periods

    actual_total_bonus = inputs.get("actual_total_bonus")
    actual_r_per_sqm = inputs.get("actual_r_per_sqm")
    delta_vs_actual = None
    if actual_total_bonus not in (None, ""):
        delta_vs_actual = total_bonus - _num(actual_total_bonus)

    return {
        "inputs_echo": {
            "total_sqm": total_sqm,
            "crew_count": crew_count,
            "people_per_crew": people_per_crew,
            "total_labor": total_labor,
            "efficiency": efficiency,
        },
        "basis": basis,
        "achieved_pct": achieved_pct,
        "use_bands": use_bands,
        "bands": band_results,
        "base_bonus": base_bonus,
        "base_bonus_per_crew": base_bonus_per_crew,
        "bands_total_crews": bands_total_crews,
        "parameters": param_results,
        "other_total": other_total,
        "total_bonus": total_bonus,
        "r_per_sqm": r_per_sqm,
        "r_per_man": r_per_man,
        "periods": periods,
        "projected_base_bonus": projected_base_bonus,
        "projected_total_bonus": projected_total_bonus,
        "actual_total_bonus": _num(actual_total_bonus) if actual_total_bonus not in (None, "") else None,
        "actual_r_per_sqm": _num(actual_r_per_sqm) if actual_r_per_sqm not in (None, "") else None,
        "delta_vs_actual": delta_vs_actual,
    }
