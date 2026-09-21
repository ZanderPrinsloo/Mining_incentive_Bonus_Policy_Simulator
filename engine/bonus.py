"""
bonus.py
--------
Pure calculation core for the Bonus Policy Simulator. No I/O, no Flask,
no sqlite imports — takes plain dicts/lists in, returns a plain dict out,
so it can be unit tested in isolation and never needs a schema migration
to change how a number is computed.

Model
-----
1. Base Bonus. Three ways to get there:
   - basis "sqm": Base Bonus = Rate (R/m²) × Total m² Achieved. Direct
     multiplication, no threshold/ratio — simplest, most literal reading of
     "the mine pays a Rand rate per square metre".
   - basis "efficiency" (labelled "R/Man" in the UI): Base Bonus = Rate
     (R/employee) × Total Labour (Crew Count × Labour per Crew).
   - Either basis instead uses Achievement Bands (a distribution of crews
     across payout/achievement tiers — the aggregate-friendly stand-in for
     per-crew detail) if bands are configured and turned on: each band has
     its own Payout % (a direct multiplier on that band's Amount) and
     Achievement % (how that band's own m²-per-employee compares to the
     section's average Efficiency — 100% = matches it). Amount = Rate ×
     Payout% × (Achievement% ÷ 100 × Efficiency) × that band's own Labour
     for the sqm basis, routed through Efficiency so Rate stays genuinely
     R/m² rather than being applied straight against a headcount; for the
     efficiency basis (already R/employee) Achievement% has no m² dimension
     to scale, so only Payout% applies (see the bands branch below).
   - basis "break_bonus": Base Bonus is simply the `break_bonus_total`
     manual input, taken as-is — real mines pay a real Break Bonus Rand
     amount directly (no efficiency-bonus concept, no rate to configure),
     so Rate/Achievement Bands are ignored under this basis.
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
                        including driller bonus"). If `gate_metric` is also
                        set, the % is prorated by (that metric ÷ total
                        labour) rather than applied to the whole total —
                        e.g. AWOP Penalty gated on awop_count: real AWOP
                        policies forfeit the bonus of the specific
                        employees who were absent, not the whole section,
                        so a flat -50% overstates it by however much of
                        the workforce wasn't actually affected.
     - "rand_per_unit" value = Rand amount per unit of a linked manual
                        input metric (e.g. R100 per quality blast)
     - "fixed"         value = a flat Rand amount, unscaled
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
BASIS_BREAK_BONUS = "break_bonus"
BASIS_CURVE = "curve"


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


def curve_lookup(curve_points: list[dict], sqm: float) -> float:
    """Rand-per-employee for a crew averaging `sqm`, from an edited set of
    (sqm, rate) points — plain linear interpolation between the two nearest
    points on the m² axis (points are sorted by sqm first, so they don't need
    to be entered in order). Below the lowest point's sqm or above the
    highest, the nearest endpoint's rate is used flat. Empty input returns 0
    — the caller (Base Bonus falls back to 0) decides what that means.
    """
    pts = sorted((_num(p.get("sqm")), _num(p.get("rate"))) for p in curve_points)
    if not pts:
        return 0.0
    if sqm <= pts[0][0]:
        return pts[0][1]
    if sqm >= pts[-1][0]:
        return pts[-1][1]
    for (s0, r0), (s1, r1) in zip(pts, pts[1:]):
        if s0 <= sqm <= s1:
            if s1 == s0:
                return r0
            frac = (sqm - s0) / (s1 - s0)
            return r0 + frac * (r1 - r0)
    return pts[-1][1]


def calculate(inputs: dict, base_cfg: dict, bands: list[dict], parameters: list[dict],
               curve_points: list[dict] | None = None) -> dict:
    """Run a full simulation. See module docstring for the model.

    inputs: dict of manual metric values (total_sqm, crew_count,
        people_per_crew, and any of the optional reference metrics —
        keys are free-form so `rand_per_unit` parameters can link to any
        of them, including ones this module doesn't know about).
    base_cfg: {basis, threshold, threshold_bonus, use_bands}
    bands: [{crew_count, payout_pct, achievement_pct}, ...] — payout_pct is a
        direct multiplier on that band's Amount; achievement_pct (100 =
        matches the section's own average Efficiency) scales the sqm basis's
        efficiency assumption (no effect under the efficiency basis, which
        has no m² dimension to scale). Ignored if use_bands is false.
    parameters: [{id, name, enabled, basis, value, linked_metric,
        qualifying_crews}, ...]
    curve_points: [{sqm, rate}, ...] — only used when basis is BASIS_CURVE
        (see curve_lookup); ignored/optional otherwise.
    """
    total_sqm = _num(inputs.get("total_sqm"))
    crew_count = _num(inputs.get("crew_count"))
    people_per_crew = _num(inputs.get("people_per_crew"))
    total_labor = crew_count * people_per_crew
    efficiency = compute_efficiency(total_sqm, total_labor)

    basis = base_cfg.get("basis") or BASIS_SQM
    threshold = _num(base_cfg.get("threshold"))
    threshold_bonus = _num(base_cfg.get("threshold_bonus"))
    use_bands = (bool(base_cfg.get("use_bands", True)) and bool(bands)
                 and basis not in (BASIS_BREAK_BONUS, BASIS_CURVE))

    band_results = []
    curve_rate_per_employee = None
    bands_sqm_total = None
    bands_sqm_exceeds_total = False
    bands_crew_total = None
    bands_crew_exceeds_total = False
    if basis == BASIS_BREAK_BONUS:
        # Real Break Bonus Rand amount, taken directly — no threshold/rate/bands.
        achieved_pct = 0.0
        base_bonus = _num(inputs.get("break_bonus_total"))
        bands_total_crews = crew_count
    elif basis == BASIS_CURVE:
        # Rate Curve: look up Rand/employee for the average crew's own m²
        # (Total m² ÷ Crew Count), then scale by Total Labour. A single
        # editable (Avg Crew m² -> Rate) table, not a full m²-by-labour grid
        # — deliberately kept to a handful of rows so it's easy to tune by
        # hand, at the cost of not separately capturing crew-size (labour
        # count) effects the way the underlying real data does.
        achieved_pct = 0.0
        avg_crew_sqm = total_sqm / crew_count if crew_count else 0.0
        curve_rate_per_employee = curve_lookup(curve_points or [], avg_crew_sqm)
        base_bonus = curve_rate_per_employee * total_labor
        bands_total_crews = crew_count
    else:
        # achieved_pct is informational only now (shown as "Achieved: X% of
        # Threshold" if a Threshold is set) — it no longer feeds Base Bonus.
        metric_value = total_sqm if basis == BASIS_SQM else efficiency
        achieved_pct = achieved_pct_from_metric(metric_value, threshold) if threshold else 0.0
        if use_bands:
            base_bonus = 0.0
            bands_total_crews = 0.0
            # Achievement Bands: each band is a group of crews with its own
            # Payout % (a direct multiplier on that band's Amount — e.g. "this
            # tier is only paid out at 90% of the rate") and Achievement %
            # (how their own m²-per-employee compares to the section's own
            # average Efficiency — 100% = matches it, 60% = under-produced).
            # For the sqm basis, Achievement% scales Efficiency so Rate stays
            # genuinely R/m² instead of being applied straight against a
            # headcount — multiplying a R/m² rate directly against crew_count
            # (no efficiency/labour involved at all) is dimensionally wrong
            # and diverges badly from what the non-bands Rate x Total m²
            # formula computes for the same inputs. For the efficiency basis
            # (already R/employee) there's no m² dimension for Achievement%
            # to scale, so only Payout% applies there.
            bands_sqm_total = 0.0
            for b in bands:
                band_crew_count = _num(b.get("crew_count"))
                payout_pct = _num(b.get("payout_pct"))
                achievement_pct = _num(b.get("achievement_pct", 100))
                band_labour = band_crew_count * people_per_crew
                if basis == BASIS_SQM:
                    band_efficiency = (achievement_pct / 100.0) * efficiency
                    band_sqm = band_efficiency * band_labour
                    amount = threshold_bonus * band_efficiency * band_labour * (payout_pct / 100.0)
                else:
                    band_sqm = 0.0
                    amount = threshold_bonus * band_labour * (payout_pct / 100.0)
                base_bonus += amount
                bands_total_crews += band_crew_count
                bands_sqm_total += band_sqm
                band_results.append({
                    "id": b.get("id"),
                    "crew_count": band_crew_count,
                    "payout_pct": payout_pct,
                    "achievement_pct": achievement_pct,
                    "sqm": band_sqm,
                    "amount": amount,
                })
            bands_crew_total = bands_total_crews  # raw sum, before the "or crew_count" fallback below
            bands_total_crews = bands_total_crews or crew_count
            # Bands can't collectively cover more crews than the section
            # actually has, or claim more m² than was actually achieved —
            # flag both rather than silently computing a Base Bonus that
            # overstates real production. Crew count is the more fundamental
            # of the two (physically impossible to exceed), and fixing it
            # (reduce a band's Crew Count) also directly reduces its m² claim
            # since m² is derived from Crew Count × Achievement% — unlike
            # Total m² Achieved itself, which scales the claim proportionally
            # and can never close this gap no matter how high it's raised.
            bands_crew_exceeds_total = bands_crew_total > crew_count + 1e-6
            bands_sqm_exceeds_total = basis == BASIS_SQM and bands_sqm_total > total_sqm + 1e-6
        else:
            # Direct rate × metric — Rate is R/m² (sqm basis) or R/employee
            # (efficiency/"R/Man" basis), multiplied straight against the real
            # production figure. No threshold ratio, no crew_count factor.
            rate_metric = total_sqm if basis == BASIS_SQM else total_labor
            base_bonus = threshold_bonus * rate_metric
            bands_total_crews = crew_count

    base_bonus_per_crew = base_bonus / bands_total_crews if bands_total_crews else 0.0

    # Stage 1: pct_base / rand_per_unit / fixed, all layered on a "basis pool" —
    # Base Bonus by default (basis_includes_base, default True) plus, if this
    # parameter's basis_param_ids names any other stage-1 parameters, their own
    # already-computed amounts too. This is what lets one parameter be "50% of
    # Base + Safety Bonus" (e.g. a Netting Bonus applied "after Safety") or even
    # "50% of Trigger Bonus" alone (basis_includes_base=False, basis_param_ids=
    # [trigger's id]) — real policies stack bonuses on top of each other this way,
    # not just on the raw Base Bonus. pct_total parameters can't be referenced
    # (they're resolved in stage 2, after the running total exists) — a
    # basis_param_ids entry pointing at one, or at a nonexistent/disabled
    # parameter, or forming a cycle, contributes 0 rather than erroring.
    amounts_by_id: dict = {}
    entries_by_id: dict = {}
    stage1_params = []   # (original_index, p) for pct_base/rand_per_unit/fixed
    pct_total_params = []  # (original_index, p) for pct_total, resolved in stage 2
    for idx, p in enumerate(parameters):
        pbasis = p.get("basis") or PCT_BASE
        (pct_total_params if pbasis == PCT_TOTAL else stage1_params).append((idx, p))

    def _make_entry(p, pbasis, value, linked_metric, qualifying_crews, has_qualifying_crews):
        return {
            "id": p.get("id"),
            "name": p.get("name"),
            "enabled": bool(p.get("enabled", True)),
            "basis": pbasis,
            "value": value,
            "linked_metric": linked_metric,
            "linked_metric_qty": None,
            "qualifying_crews": _num(qualifying_crews) if has_qualifying_crews else None,
            "gate_metric": p.get("gate_metric") or None,
            "gate_metric_qty": None,
            "effective_qualifying_crews": None,
            "basis_param_ids": list(p.get("basis_param_ids") or []),
            "basis_includes_base": bool(p.get("basis_includes_base", True)),
            "circular_pool": False,
            "amount": 0.0,
        }

    def _resolve_pct_base(p, entry):
        value = entry["value"]
        pool = base_bonus if entry["basis_includes_base"] else 0.0
        for ref_id in entry["basis_param_ids"]:
            pool += amounts_by_id.get(ref_id, 0.0)
        pool_per_crew = pool / bands_total_crews if bands_total_crews else 0.0
        gate_metric = entry["gate_metric"]
        qualifying_crews = p.get("qualifying_crews")
        has_qualifying_crews = qualifying_crews not in (None, "")
        if gate_metric:
            gate_metric_qty = _num(inputs.get(gate_metric))
            effective_qualifying_crews = max(bands_total_crews - gate_metric_qty, 0.0)
            amount = pool_per_crew * effective_qualifying_crews * (value / 100.0)
            entry["gate_metric_qty"] = gate_metric_qty
            entry["effective_qualifying_crews"] = effective_qualifying_crews
        elif has_qualifying_crews:
            effective_qualifying_crews = _num(qualifying_crews)
            amount = pool_per_crew * effective_qualifying_crews * (value / 100.0)
            entry["effective_qualifying_crews"] = effective_qualifying_crews
        else:
            amount = pool * (value / 100.0)
        return amount

    # Pass 1: rand_per_unit / fixed have no dependencies — resolve immediately.
    pending_pct_base = []
    for idx, p in stage1_params:
        pbasis = p.get("basis") or PCT_BASE
        enabled = bool(p.get("enabled", True))
        value = _num(p.get("value"))
        linked_metric = p.get("linked_metric")
        qualifying_crews = p.get("qualifying_crews")
        has_qualifying_crews = qualifying_crews not in (None, "")
        entry = _make_entry(p, pbasis, value, linked_metric, qualifying_crews, has_qualifying_crews)
        pid = p.get("id")
        if pbasis == RAND_PER_UNIT:
            qty = _num(inputs.get(linked_metric)) if enabled else 0.0
            entry["linked_metric_qty"] = qty
            entry["amount"] = qty * value if enabled else 0.0
            amounts_by_id[pid] = entry["amount"]
            entries_by_id[pid] = entry
        elif pbasis == FIXED:
            entry["amount"] = value if enabled else 0.0
            amounts_by_id[pid] = entry["amount"]
            entries_by_id[pid] = entry
        else:  # PCT_BASE — may depend on other stage-1 parameters, resolve below
            pending_pct_base.append((idx, p, entry))
            entries_by_id[pid] = entry

    # Pass 2: iteratively resolve pct_base parameters whose basis_param_ids are
    # all already resolved, until no more progress can be made (a cycle, or a
    # reference to a pct_total/missing parameter, just leaves 0 for that ref).
    progress = True
    while pending_pct_base and progress:
        progress = False
        still_pending = []
        for idx, p, entry in pending_pct_base:
            deps = entry["basis_param_ids"]
            if all(d in amounts_by_id for d in deps):
                enabled = entry["enabled"]
                entry["amount"] = _resolve_pct_base(p, entry) if enabled else 0.0
                amounts_by_id[p.get("id")] = entry["amount"]
                progress = True
            else:
                still_pending.append((idx, p, entry))
        pending_pct_base = still_pending
    for idx, p, entry in pending_pct_base:  # leftover cycles — resolve with whatever's known
        # Flagged so the UI can show a real warning instead of a silently "wrong"
        # amount — a cycle (A's pool includes B, B's pool includes A) has no
        # single correct answer; this breaks it by resolving in parameter order
        # and treating the not-yet-resolved side as 0 for that one step, which
        # is why the first parameter in the cycle often computes to 0 while a
        # later one in the same cycle doesn't (order-dependent, not a real rate).
        entry["circular_pool"] = True
        enabled = entry["enabled"]
        entry["amount"] = _resolve_pct_base(p, entry) if enabled else 0.0
        amounts_by_id[p.get("id")] = entry["amount"]

    stage1_total = sum(entries_by_id[p.get("id")]["amount"] for _, p in stage1_params if p.get("enabled", True))

    param_results = [None] * len(parameters)
    pct_total_queue = []
    for idx, p in stage1_params:
        param_results[idx] = entries_by_id[p.get("id")]
    for idx, p in pct_total_params:
        enabled = bool(p.get("enabled", True))
        value = _num(p.get("value"))
        entry = _make_entry(p, PCT_TOTAL, value, p.get("linked_metric"), p.get("qualifying_crews"),
                             p.get("qualifying_crews") not in (None, ""))
        param_results[idx] = entry
        if enabled:
            pct_total_queue.append(entry)

    running_total = base_bonus + stage1_total

    stage2_total = 0.0
    for entry in pct_total_queue:
        # gate_metric on a pct_total parameter prorates the % by (metric ÷ total
        # labour) instead of applying it to the whole running total — e.g. AWOP
        # Penalty gated on awop_count: real policy forfeits an INDIVIDUAL
        # employee's own bonus for their own AWOP, not everyone's, so applying a
        # flat -50% to the section's entire total overstates it by however much
        # of the workforce wasn't actually AWOP that period.
        gate_metric = entry.get("gate_metric")
        if gate_metric:
            metric_qty = _num(inputs.get(gate_metric))
            fraction = min(metric_qty / total_labor, 1.0) if total_labor else 0.0
            entry["gate_metric_qty"] = metric_qty
            amount = running_total * (entry["value"] / 100.0) * fraction
        else:
            amount = running_total * (entry["value"] / 100.0)
        entry["amount"] = amount
        stage2_total += amount

    total_bonus = running_total + stage2_total
    other_total = stage1_total + stage2_total

    r_per_sqm = total_bonus / total_sqm if total_sqm else 0.0
    r_per_man = total_bonus / total_labor if total_labor else 0.0

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
            "break_bonus_total": _num(inputs.get("break_bonus_total")),
        },
        "basis": basis,
        "achieved_pct": achieved_pct,
        "use_bands": use_bands,
        "bands": band_results,
        "curve_rate_per_employee": curve_rate_per_employee,
        "base_bonus": base_bonus,
        "base_bonus_per_crew": base_bonus_per_crew,
        "bands_total_crews": bands_total_crews,
        "bands_sqm_total": bands_sqm_total,
        "bands_sqm_exceeds_total": bands_sqm_exceeds_total,
        "bands_crew_total": bands_crew_total,
        "bands_crew_exceeds_total": bands_crew_exceeds_total,
        "parameters": param_results,
        "other_total": other_total,
        "total_bonus": total_bonus,
        "r_per_sqm": r_per_sqm,
        "r_per_man": r_per_man,
        "actual_total_bonus": _num(actual_total_bonus) if actual_total_bonus not in (None, "") else None,
        "actual_r_per_sqm": _num(actual_r_per_sqm) if actual_r_per_sqm not in (None, "") else None,
        "delta_vs_actual": delta_vs_actual,
    }
