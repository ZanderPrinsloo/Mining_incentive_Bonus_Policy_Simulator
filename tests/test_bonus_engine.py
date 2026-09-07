import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.bonus import calculate


def test_base_bonus_from_bands():
    inputs = {"total_sqm": 3000, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 300, "threshold_bonus": 100, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 6, "payout_pct": 100}, {"id": 2, "crew_count": 4, "payout_pct": 50}]
    result = calculate(inputs, base_cfg, bands, [])
    # 6*100%*R100 + 4*50%*R100 = 600 + 200 = 800
    assert result["base_bonus"] == 800
    assert result["total_bonus"] == 800
    assert result["inputs_echo"]["total_labor"] == 50
    assert result["inputs_echo"]["efficiency"] == 60


def test_pct_base_and_pct_total_and_rand_per_unit_and_fixed():
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 5, "quality_blast_count": 20}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    bands = [{"id": 1, "crew_count": 10, "payout_pct": 100}]  # base_bonus = 10*1*10 = 100
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
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 5}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 10, "periods": 1, "use_bands": True}
    # 6 crews at 100%, 4 at 50% -> base_bonus = 60 + 20 = 80, bands_total_crews = 10, per-crew = 8
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
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 5, "safety_incidents": 3}
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
    inputs = {"total_sqm": 1000, "crew_count": 10, "people_per_crew": 5, "safety_incidents": 99}
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


def test_periods_projection_and_actual_delta():
    inputs = {"total_sqm": 100, "crew_count": 1, "people_per_crew": 1, "actual_total_bonus": 90}
    base_cfg = {"basis": "sqm", "threshold": 100, "threshold_bonus": 100, "periods": 3, "use_bands": True}
    bands = [{"id": 1, "crew_count": 1, "payout_pct": 100}]
    result = calculate(inputs, base_cfg, bands, [])
    assert result["base_bonus"] == 100
    assert result["projected_total_bonus"] == 300
    assert result["delta_vs_actual"] == 10


def test_zero_guards():
    result = calculate({}, {"basis": "sqm", "threshold": 0, "threshold_bonus": 0, "periods": 1, "use_bands": False}, [], [])
    assert result["base_bonus"] == 0
    assert result["r_per_sqm"] == 0
    assert result["r_per_man"] == 0
    assert result["achieved_pct"] == 0
