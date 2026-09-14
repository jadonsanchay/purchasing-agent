"""The rubric must reward the reference trajectories and punish wrong ones, otherwise it measures nothing."""
import yaml
from pathlib import Path

from app import scenarios
from app.agent.llm import ScriptedLLM
from app.agent.runner import Runner
from app.agent.scripted import SCRIPTS, investigate_cola, proposal
from evals.run_evals import score

CASES = {c["id"]: c for c in yaml.safe_load((Path(__file__).parent.parent / "evals" / "cases.yaml").read_text())}


def run(sid, script):
    r = Runner(scenarios.load_scenario(sid), ScriptedLLM(script))
    st = r.start()
    if st.status == "awaiting_approval":
        st = r.approve()
    return st


def test_reference_trajectories_pass_rubric():
    for sid, case in CASES.items():
        st = run(sid, [list(t) for t in SCRIPTS[sid]])
        sc = score(case, st)
        assert sc["passed"], (sid, sc["dimensions"])


def test_blind_accept_without_investigation_fails_information_dimension():
    st = run("S1-a", [[proposal("accept", 800, inbound=800)]])
    sc = score(CASES["S1-a"], st)
    assert sc["dimensions"]["information_gathered"]["pass"] is False
    assert sc["passed"] is False


def test_ignoring_open_po_fails_decision_dimension():
    # S1-b: buying the full 800 on top of 700 inbound is wrong even though every hard constraint passes
    st = run("S1-b", investigate_cola() + [[proposal("accept", 800, inbound=1500)]])
    sc = score(CASES["S1-b"], st)
    assert sc["dimensions"]["decision_correct"]["pass"] is False
    assert sc["dimensions"]["action_appropriate"]["pass"] is False  # inbound 1500 outside range


def test_unneeded_alternate_order_fails_action_dimension():
    # S2-b: partial already covers demand; creating a second PO is over-buying
    st = run("S2-b", [
        [("compute_coverage", {"sku": "SKU-RICE-1KG", "node_id": "N-BOG", "supplier_id": "S-NORTE"})],
        [proposal("modify", 250, supplier="S-NORTE", po_id="PO-1002", extra=[{"supplier_id": "S-NORTE", "quantity": 250}], status="confirmed", inbound=500)],
    ])
    sc = score(CASES["S2-b"], st)
    assert sc["dimensions"]["action_appropriate"]["pass"] is False


def test_recovery_expected_but_agent_gave_up_fails():
    # S1-f: after the budget rejection the agent just escalates without a revised proposal -> still 'recovery' happened,
    # but if it proposes nothing at all the run escalates via the system; both should be flagged only if no recovery turn
    st = run("S1-f", investigate_cola() + [[proposal("investigate", None, status="none", inbound=0)]])
    sc = score(CASES["S1-f"], st)
    assert sc["dimensions"]["recovery_behaviour"]["pass"] is False
    assert sc["dimensions"]["decision_correct"]["pass"] is False


def test_accepting_forecast_quantity_despite_demand_spike_fails():
    st = run("S1-e", investigate_cola() + [[proposal("accept", 800, inbound=800, gap=True)]])
    sc = score(CASES["S1-e"], st)
    assert sc["dimensions"]["decision_correct"]["pass"] is False


def test_sizing_to_actual_demand_with_approval_passes_s1e():
    st = run("S1-e", investigate_cola(1300) + [[proposal("modify", 1300, inbound=1300, gap=True)]])
    sc = score(CASES["S1-e"], st)
    assert sc["passed"], sc["dimensions"]


def test_s1a_modify_with_unchanged_quantity_counts_as_accept():
    st = run("S1-a", investigate_cola() + [[proposal("modify", 800, inbound=800)]])
    assert score(CASES["S1-a"], st)["passed"]


def test_s2a_covering_full_target_from_alternate_passes():
    milk = {"sku": "SKU-MILK-1L", "node_id": "N-MDE"}
    st = run("S2-a", [
        [("compute_coverage", {**milk, "supplier_id": "S-ANDINA"}), ("list_suppliers_for_sku", {"sku": "SKU-MILK-1L"}),
         ("check_constraints", {**milk, "supplier_id": "S-ANDINA", "quantity": 650, "recommended_qty": None})],
        [proposal("modify", 250, supplier="S-PACIFICO", po_id="PO-1001", extra=[{"supplier_id": "S-ANDINA", "quantity": 650}], status="confirmed", inbound=900)],
    ])
    sc = score(CASES["S2-a"], st)
    assert sc["passed"], sc["dimensions"]


def test_trailing_noop_accept_after_recovery_does_not_hide_the_real_decision():
    from evals.run_evals import effective_decision
    from app.config import settings
    milk = {"sku": "SKU-MILK-1L", "node_id": "N-MDE"}
    # force a validation mismatch via a wrong inbound expectation, then confirm the state with a bare accept
    st = run("S2-a", [
        [("compute_coverage", {**milk, "supplier_id": "S-ANDINA"}), ("list_suppliers_for_sku", {"sku": "SKU-MILK-1L"}),
         ("check_constraints", {**milk, "supplier_id": "S-ANDINA", "quantity": 650, "recommended_qty": None})],
        [proposal("modify", 250, supplier="S-PACIFICO", po_id="PO-1001", extra=[{"supplier_id": "S-ANDINA", "quantity": 650}], status="confirmed", inbound=850)],
        [proposal("accept", None, status="none", inbound=900)],
    ])
    assert st.recovery_turns_used == 1 and st.status == "completed"
    assert effective_decision(st).action.value == "modify"
    assert score(CASES["S2-a"], st)["passed"]
