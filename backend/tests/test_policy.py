from app import db, scenarios
from app.agent import policy

COLA = ("SKU-COLA-2L", "N-BOG", "S-ANDINA")


def coverage(scenario_id, *key):
    scenarios.load_scenario(scenario_id)
    with db.connect() as conn:
        return policy.compute_coverage(conn, *key)


def constraints(scenario_id, qty, *key, rec=800):
    scenarios.load_scenario(scenario_id)
    with db.connect() as conn:
        return policy.check_constraints(conn, *key, qty, recommended_qty=rec)


def test_rounding():
    assert policy.round_to_pack(750, 50, 200) == 750
    assert policy.round_to_pack(750, 100, 200) == 800
    assert policy.round_to_pack(50, 50, 200) == 200
    assert policy.round_to_pack(0, 50, 200) == 0
    assert policy.round_to_pack(-30, 50, 200) == 0
    assert policy.round_down_to_pack(416, 50, 200) == 400
    assert policy.round_down_to_pack(416, 100, 200) == 400
    assert policy.round_down_to_pack(180, 50, 200) == 0


def test_s1a_baseline_supports_800():
    cov = coverage("S1-a", *COLA)
    assert cov.target_days == 15
    assert cov.forecast_target_units == 900
    assert cov.net_requirement == 750
    assert cov.suggested_qty == 800
    assert cov.demand_signal_flag is False
    rep = constraints("S1-a", 800, *COLA)
    assert rep.hard_fail is False
    assert rep.requires_approval is False
    assert rep.deviation_from_recommendation_pct == 0


def test_s1b_open_po_makes_recommendation_redundant():
    cov = coverage("S1-b", *COLA)
    assert cov.inbound == 700
    assert cov.net_requirement == 50
    assert cov.suggested_qty == 200  # MOQ floor
    rep = constraints("S1-b", 800, *COLA)
    assert rep.hard_fail is False
    assert any(c.name == "over_cover" and c.status == "warn" for c in rep.checks)


def test_s1c_budget_caps_quantity():
    rep = constraints("S1-c", 800, *COLA)
    assert rep.hard_fail is True
    assert any("budget" in r.lower() or "headroom" in r for r in rep.fail_reasons)
    assert rep.max_feasible_qty == 400
    ok = constraints("S1-c", 400, *COLA)
    assert ok.hard_fail is False
    assert ok.requires_approval is True  # 50% deviation


def test_s1d_storage_caps_quantity():
    rep = constraints("S1-d", 800, *COLA)
    assert rep.hard_fail is True
    assert rep.max_feasible_qty == 600
    ok = constraints("S1-d", 600, *COLA)
    assert ok.hard_fail is False
    assert ok.requires_approval is False  # 25% deviation, under limit


def test_s1e_flags_demand_shift():
    cov = coverage("S1-e", *COLA)
    assert cov.demand_signal_flag is True
    assert cov.actual_last_7d_daily_avg == 110
    rep = constraints("S1-e", 800, *COLA)
    assert any(c.name == "demand_signal" for c in rep.checks)


def test_s1f_looks_fine_before_execution():
    rep = constraints("S1-f", 800, *COLA)
    assert rep.hard_fail is False  # the trap only springs at write time


def test_s2a_shortfall_needs_remainder():
    cov = coverage("S2-a", "SKU-MILK-1L", "N-MDE", "S-PACIFICO")
    assert cov.inbound == 250  # notice-capped, not the 500 ordered
    assert cov.net_requirement > 0
    alt = coverage("S2-a", "SKU-MILK-1L", "N-MDE", "S-ANDINA")
    assert alt.suggested_qty >= 250


def test_s2b_partial_is_sufficient():
    cov = coverage("S2-b", "SKU-RICE-1KG", "N-BOG", "S-NORTE")
    assert cov.inbound == 250
    assert cov.net_requirement < 0
    assert cov.suggested_qty == 0
