import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.bonus import calculate, curve_lookup


def test_base_bonus_direct_rate_sqm():
    # No Achievement Bands: Base Bonus = Rate (R/m2) x Total m2, straight
    # multiplication, no threshold ratio or crew_count factor involved.
    inputs = {"total_sqm": 14090, "crew_count": 39, "people_per_crew": 16.03}
    base_cfg = {"basis": "sqm", "threshold": 0, "threshold_bonus": 143.87, "periods": 1, "use_bands": False}
    result = calculate(inputs, base_cfg, [], [])
    assert round(result["base_bonus"], 2) == round(143.87 * 14090, 2)
    assert result["achieved_pct"] == 0  # threshold not set -> no "% of threshold" claim


def test_base_bonus_direct_rate_efficiency_is_r_per_man():
    # "efficiency" basis (UI label "R/Man"): Rate x Total Labour, not Rate x
    # the efficiency ratio (m2/employee) - a straight Rand-per-employee payout.
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 5}  # total_labor = 50
    base_cfg = {"basis": "efficiency", "threshold": 0, "threshold_bonus": 200, "periods": 1, "use_bands": False}
    result = calculate(inputs, base_cfg, [], [])
    assert result["base_bonus"] == 200 * 50


def test_curve_lookup_exact_match_returns_that_point():
    points = [{"sqm": 300, "rate": 1000}, {"sqm": 400, "rate": 2000}]
    assert curve_lookup(points, 300) == 1000


def test_curve_lookup_interpolates_between_points():
    points = [{"sqm": 300, "rate": 1000}, {"sqm": 400, "rate": 2000}]
    mid = curve_lookup(points, 350)  # exactly halfway -> exactly the midpoint rate
    assert mid == 1500


def test_curve_lookup_clamps_outside_range_to_nearest_endpoint():
    points = [{"sqm": 300, "rate": 1000}, {"sqm": 400, "rate": 2000}]
    assert curve_lookup(points, 100) == 1000
    assert curve_lookup(points, 900) == 2000


def test_curve_lookup_empty_points_returns_zero():
    assert curve_lookup([], 300) == 0.0


def test_base_bonus_curve_basis_uses_avg_crew_sqm():
    # Total m2 / Crew Count = the "average crew's" own m2.
    inputs = {"total_sqm": 3500, "crew_count": 10, "people_per_crew": 17}  # avg crew sqm = 350
    base_cfg = {"basis": "curve", "periods": 1, "use_bands": False}
    curve_points = [{"sqm": 300, "rate": 1000}, {"sqm": 400, "rate": 2000}]
    result = calculate(inputs, base_cfg, [], [], curve_points)
    # rate at 350 = 1500 (see interpolation test above); total_labor = 170
    assert result["curve_rate_per_employee"] == 1500
    assert result["base_bonus"] == 1500 * 170
    assert result["total_bonus"] == result["base_bonus"]


def test_base_bonus_from_bands_routes_through_efficiency_not_raw_crew_count():
    # sqm basis: each band's Amount = Rate x Achievement% x that band's own
    # Labour (crew_count x people_per_crew), routed through the section's
    # average Efficiency so Rate stays genuinely R/m2 instead of being
    # applied straight against a headcount.
    inputs = {"total_sqm": 3000, "crew_count": 10, "people_per_crew": 5}  # total_labor=50, efficiency=60
    base_cfg = {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 6, "payout_pct": 100}, {"id": 2, "crew_count": 4, "payout_pct": 50}]
    result = calculate(inputs, base_cfg, bands, [])
    # per_employee_rate = 100 * 60 = 6000
    # band 1: labour = 6*5=30, amount = 6000 * 1.0 * 30 = 180000
    # band 2: labour = 4*5=20, amount = 6000 * 0.5 * 20 = 60000
    assert result["base_bonus"] == 240000
    assert result["total_bonus"] == 240000
    assert result["inputs_echo"]["total_labor"] == 50
    assert result["inputs_echo"]["efficiency"] == 60


def test_base_bonus_from_bands_matches_direct_rate_formula_when_one_band_covers_all_crews():
    # A single 100%-achievement band spanning every crew should reproduce
    # exactly what the non-bands Rate x Total m2 formula gives for the same
    # inputs — the bug this replaced: multiplying Rate straight against a
    # crew-count badly diverged from this (e.g. R500/m2 x 420 crews = R210k
    # instead of R500 x 100000 m2 = R50m).
    inputs = {"total_sqm": 100000, "crew_count": 420, "people_per_crew": 14}
    base_cfg_direct = {"basis": "sqm", "threshold_bonus": 500, "periods": 1, "use_bands": False}
    base_cfg_bands = {"basis": "sqm", "threshold_bonus": 500, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 420, "payout_pct": 100}]
    direct = calculate(inputs, base_cfg_direct, [], [])
    banded = calculate(inputs, base_cfg_bands, bands, [])
    assert direct["base_bonus"] == 500 * 100000
    assert round(banded["base_bonus"], 6) == round(direct["base_bonus"], 6)


def test_base_bonus_from_bands_efficiency_basis_uses_rate_directly():
    # efficiency ("R/Man") basis: Rate is already R/employee, so only Payout%
    # applies against that band's Labour — Achievement% has no m² dimension
    # to scale here, so it's ignored (even set to something absurd, below).
    inputs = {"total_sqm": 999, "crew_count": 10, "people_per_crew": 5}  # total_sqm irrelevant here
    base_cfg = {"basis": "efficiency", "threshold_bonus": 20, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 50, "achievement_pct": 9999}]
    result = calculate(inputs, base_cfg, bands, [])
    # labour = 10*5=50; amount = 20 * 0.5 * 50 = 500 (achievement_pct ignored)
    assert result["base_bonus"] == 500


def test_base_bonus_from_bands_payout_and_achievement_are_independent_multipliers():
    # Payout% is a direct multiplier on the band's own Amount (e.g. "this
    # tier is only paid at 90% of rate"); Achievement% separately scales the
    # sqm-basis efficiency assumption (this band's crews over/under-produced
    # relative to the section average) — they compound, not substitute.
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 10}  # total_labor=100, efficiency=10
    base_cfg = {"basis": "sqm", "threshold_bonus": 5, "periods": 1, "use_bands": True}
    bands = [
        {"id": 1, "crew_count": 4, "payout_pct": 100, "achievement_pct": 100},  # baseline
        {"id": 2, "crew_count": 4, "payout_pct": 50, "achievement_pct": 100},   # half payout only
        {"id": 3, "crew_count": 4, "payout_pct": 100, "achievement_pct": 50},   # half achievement only
        {"id": 4, "crew_count": 4, "payout_pct": 50, "achievement_pct": 50},    # both halved -> quarter
    ]
    result = calculate(inputs, base_cfg, bands, [])
    amounts = {b["id"]: b["amount"] for b in result["bands"]}
    # per_employee_rate at 100%/100% = 5 * 10 = 50; band labour = 4*10 = 40 -> baseline = 2000
    assert amounts[1] == 2000
    assert amounts[2] == 1000    # payout halved
    assert amounts[3] == 1000    # achievement halved
    assert amounts[4] == 500     # both halved -> quarter of baseline


def test_bands_sqm_total_flags_when_it_exceeds_production():
    # Each band's Achievement% x Crew Count implies how much m² it accounts
    # for — bands can't collectively claim more m² than was actually
    # achieved (Total m² in Production & Labour) without overstating Base
    # Bonus beyond real production.
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 10}  # efficiency=10
    base_cfg = {"basis": "sqm", "threshold_bonus": 5, "periods": 1, "use_bands": True}
    within_bounds = [
        {"id": 1, "crew_count": 4, "payout_pct": 100, "achievement_pct": 100},  # sqm = 10*40 = 400
        {"id": 2, "crew_count": 4, "payout_pct": 100, "achievement_pct": 100},  # sqm = 400
    ]
    result = calculate(inputs, base_cfg, within_bounds, [])
    band_sqms = {b["id"]: b["sqm"] for b in result["bands"]}
    assert band_sqms[1] == 400
    assert band_sqms[2] == 400
    assert result["bands_sqm_total"] == 800
    assert result["bands_sqm_exceeds_total"] is False

    over_bounds = within_bounds + [
        {"id": 3, "crew_count": 4, "payout_pct": 100, "achievement_pct": 200},  # sqm = 20*40 = 800
    ]
    result2 = calculate(inputs, base_cfg, over_bounds, [])
    assert result2["bands_sqm_total"] == 1600
    assert result2["bands_sqm_exceeds_total"] is True


def test_bands_crew_total_flags_when_it_exceeds_production_crew_count():
    # Crew Count is a harder constraint than m²: a band's own m² claim scales
    # proportionally with Total m² (see the sqm test above), so raising Total
    # m² can never close an m² gap caused by too much Crew Count committed —
    # only reducing a band's own Crew Count actually fixes it.
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 10}
    base_cfg = {"basis": "sqm", "threshold_bonus": 5, "periods": 1, "use_bands": True}
    within_bounds = [
        {"id": 1, "crew_count": 6, "payout_pct": 100, "achievement_pct": 100},
        {"id": 2, "crew_count": 4, "payout_pct": 100, "achievement_pct": 100},
    ]
    result = calculate(inputs, base_cfg, within_bounds, [])
    assert result["bands_crew_total"] == 10
    assert result["bands_crew_exceeds_total"] is False

    over_bounds = within_bounds + [
        {"id": 3, "crew_count": 5, "payout_pct": 0, "achievement_pct": 100},
    ]
    result2 = calculate(inputs, base_cfg, over_bounds, [])
    assert result2["bands_crew_total"] == 15
    assert result2["bands_crew_exceeds_total"] is True
    # the crew overshoot alone drags the m² claim over too (15 crews' worth
    # of "achieved" m² at the same per-crew rate as only 10 crews' worth of
    # real production)
    assert result2["bands_sqm_exceeds_total"] is True


def test_bands_sqm_fields_inert_under_efficiency_basis():
    inputs = {"total_sqm": 1, "crew_count": 10, "people_per_crew": 10}  # total_sqm tiny on purpose
    base_cfg = {"basis": "efficiency", "threshold_bonus": 5, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100, "achievement_pct": 9999}]
    result = calculate(inputs, base_cfg, bands, [])
    assert result["bands"][0]["sqm"] == 0.0
    assert result["bands_sqm_total"] == 0.0
    assert result["bands_sqm_exceeds_total"] is False


def test_pct_base_and_pct_total_and_rand_per_unit_and_fixed():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5, "quality_blast_count": 20}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    # single band covers all 10 crews at 100% -> base_bonus = Rate x Total m2 = 10*10 = 100
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]
    parameters = [
        {"id": 1, "name": "Safety", "enabled": True, "basis": "pct_base", "value": 50},       # +50 (50% of 100)
        {"id": 2, "name": "Driller", "enabled": True, "basis": "rand_per_unit", "value": 5,
         "linked_metric": "quality_blast_count"},                                             # +100 (20*5)
        {"id": 3, "name": "Safety Rep", "enabled": True, "basis": "fixed", "value": 25},       # +25
        {"id": 4, "name": "AWOP", "enabled": True, "basis": "pct_total", "value": -10},        # -10% of running total
        {"id": 5, "name": "Disabled param", "enabled": False, "basis": "fixed", "value": 999}, # ignored
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    assert result["base_bonus"] == 100
    # running total after stage 1 = 100 + 50 + 100 + 25 = 275
    # AWOP = -10% of 275 = -27.5
    assert round(result["total_bonus"], 2) == 247.5
    amounts = {p["name"]: p["amount"] for p in result["parameters"]}
    assert amounts["Safety"] == 50
    assert amounts["Driller"] == 100
    assert amounts["Safety Rep"] == 25
    assert round(amounts["AWOP"], 2) == -27.5
    assert amounts["Disabled param"] == 0


def test_pct_base_prorates_by_qualifying_crews():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    # 6 crews at 100%, 4 at 50% (Rate x Total m2, weighted by achievement) ->
    # base_bonus = 10*10*(1.0*6+0.5*4)/10 = 80, bands_total_crews = 10, per-crew = 8
    bands = [{"id": 1, "crew_count": 6, "payout_pct": 100}, {"id": 2, "crew_count": 4, "payout_pct": 50}]
    parameters = [
        {"id": 1, "name": "Safety (whole base)", "enabled": True, "basis": "pct_base", "value": 50},
        {"id": 2, "name": "Safety (6 of 10 crews)", "enabled": True, "basis": "pct_base", "value": 50,
         "qualifying_crews": 6},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    assert result["base_bonus"] == 80
    assert result["base_bonus_per_crew"] == 8
    assert result["bands_total_crews"] == 10
    amounts = {p["name"]: p["amount"] for p in result["parameters"]}
    assert amounts["Safety (whole base)"] == 40       # 50% of the full 80 base bonus
    assert amounts["Safety (6 of 10 crews)"] == 24     # 50% of (8 per-crew * 6 qualifying crews) = 50% of 48


def test_pct_base_gate_metric_takes_precedence_over_manual_qualifying_crews():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5, "safety_incidents": 3}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100, per-crew = 10
    parameters = [
        {"id": 1, "name": "Safety", "enabled": True, "basis": "pct_base", "value": 50,
         "gate_metric": "safety_incidents", "qualifying_crews": 1},  # gate_metric wins over qualifying_crews
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    entry = result["parameters"][0]
    # 3 incidents -> 10 - 3 = 7 crews still qualify; 50% of (10 per-crew * 7) = 35
    assert entry["effective_qualifying_crews"] == 7
    assert entry["gate_metric_qty"] == 3
    assert entry["amount"] == 35


def test_pct_base_gate_metric_clamps_at_zero_when_incidents_exceed_crews():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5, "safety_incidents": 99}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]
    parameters = [
        {"id": 1, "name": "Safety", "enabled": True, "basis": "pct_base", "value": 50,
         "gate_metric": "safety_incidents"},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    entry = result["parameters"][0]
    assert entry["effective_qualifying_crews"] == 0
    assert entry["amount"] == 0


def test_actual_delta():
    inputs = {"total_sqm": 1, "crew_count": 1, "people_per_crew": 1, "actual_total_bonus": 90}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 100, "use_bands": True}
    bands = [{"id": 1, "crew_count": 1, "payout_pct": 100}]
    result = calculate(inputs, base_cfg, bands, [])
    assert result["base_bonus"] == 100
    assert result["delta_vs_actual"] == 10


def test_pct_base_basis_pool_can_reference_another_parameter_instead_of_base():
    # "Safety Trigger Bonus" case: 50% of Trigger Bonus specifically, not Base Bonus.
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        {"id": 1, "name": "Trigger Bonus", "enabled": True, "basis": "pct_base", "value": 50},  # 50% of 100 = 50
        {"id": 2, "name": "Safety Trigger Bonus", "enabled": True, "basis": "pct_base", "value": 50,
         "basis_param_ids": [1], "basis_includes_base": False},  # 50% of Trigger Bonus's 50 = 25
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    amounts = {p["name"]: p["amount"] for p in result["parameters"]}
    assert amounts["Trigger Bonus"] == 50
    assert amounts["Safety Trigger Bonus"] == 25
    assert result["base_bonus"] == 100
    assert result["total_bonus"] == 100 + 50 + 25


def test_pct_base_basis_pool_can_combine_base_and_another_parameter():
    # Netting Bonus "after Safety": % of (Base Bonus + Safety Bonus).
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        {"id": 1, "name": "Safety Bonus", "enabled": True, "basis": "pct_base", "value": 25},  # 25% of 100 = 25
        {"id": 2, "name": "Netting Bonus", "enabled": True, "basis": "pct_base", "value": 20,
         "basis_param_ids": [1], "basis_includes_base": True},  # 20% of (100+25) = 25
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    amounts = {p["name"]: p["amount"] for p in result["parameters"]}
    assert amounts["Safety Bonus"] == 25
    assert amounts["Netting Bonus"] == 25


def test_pct_base_basis_pool_ignores_disabled_and_cyclic_references():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        # references a disabled parameter — contributes 0, not an error
        {"id": 1, "name": "Disabled Ref", "enabled": False, "basis": "pct_base", "value": 999},
        {"id": 2, "name": "Depends on Disabled", "enabled": True, "basis": "pct_base", "value": 50,
         "basis_param_ids": [1], "basis_includes_base": False},
        # a two-parameter cycle — both resolve using whatever's known (0 for the other side) rather than hang
        {"id": 3, "name": "Cycle A", "enabled": True, "basis": "pct_base", "value": 10, "basis_param_ids": [4]},
        {"id": 4, "name": "Cycle B", "enabled": True, "basis": "pct_base", "value": 10, "basis_param_ids": [3]},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    by_name = {p["name"]: p for p in result["parameters"]}
    amounts = {k: v["amount"] for k, v in by_name.items()}
    assert amounts["Disabled Ref"] == 0
    assert amounts["Depends on Disabled"] == 0
    # no crash / infinite loop on the cycle; each just sees 0 for the other's unresolved amount
    assert amounts["Cycle A"] == 10  # 10% of (base 100 + Cycle B's 0)
    assert amounts["Cycle B"] in (10, 11)  # resolves against whatever Cycle A ended up as, order-dependent
    # both cycle members are flagged so the UI can warn — this isn't a real rate,
    # just an order-dependent artefact of breaking an unresolvable circular reference
    assert by_name["Cycle A"]["circular_pool"] is True
    assert by_name["Cycle B"]["circular_pool"] is True
    # non-cyclic parameters are explicitly flagged False, not just absent
    assert by_name["Disabled Ref"]["circular_pool"] is False
    assert by_name["Depends on Disabled"]["circular_pool"] is False


def test_pct_base_basis_pool_reference_to_deleted_parameter_is_not_circular():
    # id 999 doesn't exist in `parameters` at all (e.g. the referenced
    # parameter was deleted after this one's Basis Pool was set up) — that's
    # a dangling reference, not a cycle: it deterministically contributes 0
    # and must NOT be flagged circular_pool, which would falsely tell the UI
    # to show "this parameter's Basis Pool eventually loops back to itself".
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        {"id": 1, "name": "Depends on Deleted", "enabled": True, "basis": "pct_base", "value": 50,
         "basis_param_ids": [999], "basis_includes_base": True},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    entry = result["parameters"][0]
    assert entry["amount"] == 50  # 50% of (base 100 + missing ref's 0)
    assert entry["circular_pool"] is False


def test_pct_total_gate_metric_prorates_by_fraction_of_total_labour():
    # AWOP Penalty gated on awop_count: only the affected fraction of the
    # workforce should lose the %, not the whole section's bonus.
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 10, "awop_count": 20}  # total_labor = 100
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        {"id": 1, "name": "AWOP Penalty", "enabled": True, "basis": "pct_total", "value": -50,
         "gate_metric": "awop_count"},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    entry = result["parameters"][0]
    # fraction = 20/100 = 20%; amount = 100 (running_total) * -50% * 20% = -10
    assert entry["gate_metric_qty"] == 20
    assert entry["amount"] == -10
    assert result["total_bonus"] == 90


def test_pct_total_gate_metric_clamps_fraction_at_one():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 10, "awop_count": 500}  # total_labor = 100
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        {"id": 1, "name": "AWOP Penalty", "enabled": True, "basis": "pct_total", "value": -50,
         "gate_metric": "awop_count"},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    entry = result["parameters"][0]
    assert entry["amount"] == -50  # fraction clamped to 1.0, not 5.0
    assert result["total_bonus"] == 50


def test_pct_total_without_gate_metric_still_applies_to_whole_total():
    inputs = {"total_sqm": 10, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 100
    parameters = [
        {"id": 1, "name": "AWOP Penalty", "enabled": True, "basis": "pct_total", "value": -50},
    ]
    result = calculate(inputs, base_cfg, bands, parameters)
    assert result["parameters"][0]["amount"] == -50
    assert result["total_bonus"] == 50


def test_zero_guards():
    result = calculate({}, {"basis": "sqm", "threshold": 0, "threshold_bonus": 0, "periods": 1, "use_bands": False}, [], [])
    assert result["base_bonus"] == 0
    assert result["r_per_sqm"] == 0
    assert result["r_per_man"] == 0
    assert result["achieved_pct"] == 0
