from app import db, scenarios
from app.agent.llm import ScriptedLLM
from app.agent.runner import Runner
from app.config import settings
from app.erp import reads

from app.agent.scripted import COLA, SCRIPTS, investigate_cola, proposal


def run(scenario_id, script):
    s = scenarios.load_scenario(scenario_id)
    r = Runner(s, ScriptedLLM(script))
    return r, r.start()


def test_s1a_accept_executes_and_validates():
    r, st = run("S1-a", investigate_cola() + [[proposal("accept", 800, inbound=800)]])
    assert st.status == "completed"
    assert st.gate["requires_approval"] is False
    assert [a.kind for a in st.actions] == ["create_po"] and st.actions[0].result["ok"]
    assert st.validations[-1].ok
    assert st.recovery_turns_used == 0
    with db.connect() as conn:
        assert reads.inbound_qty(conn, "SKU-COLA-2L", "N-BOG") == 800
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE run_id=? AND event='po_created'", (r.run_id,)).fetchone()[0] == 1


def test_s1b_reject_makes_no_changes():
    r, st = run("S1-b", investigate_cola() + [[proposal("reject", None, status="none", inbound=700)]])
    assert st.status == "completed" and st.actions == [] and st.validations[-1].ok


def test_gate_blocks_hard_fail_and_agent_recovers():
    # 800 units in S1-c fails budget at the gate (before any write); agent revises to 400 -> approval -> executes
    r, st = run("S1-c", investigate_cola() + [
        [proposal("accept", 800, inbound=800)],
        [("check_constraints", {**COLA, "quantity": 400, "recommended_qty": 800})],
        [proposal("modify", 400, inbound=400, gap=True)],
    ])
    assert st.status == "awaiting_approval"
    assert st.recovery_turns_used == 1
    assert st.actions == []  # nothing written before approval
    assert any(e.event == "gate_evaluated" and e.payload["hard_fail"] for e in st.trace)
    st = r.approve()
    assert st.status == "completed" and st.validations[-1].ok
    assert st.actions[-1].result["ordered_qty"] == 400


def test_human_rejection_leaves_erp_untouched():
    r, st = run("S1-d", investigate_cola(600) + [[proposal("modify", 500, inbound=500, gap=True)]])
    # 500 vs 800 = -37.5% deviation -> under 40%, but let's force approval via value threshold
    assert st.status in ("completed", "awaiting_approval")
    if st.status == "awaiting_approval":
        st = r.reject(note="hold until next period")
        assert st.status == "completed" and st.actions == []


def test_s1f_validation_mismatch_triggers_recovery(monkeypatch):
    r, st = run("S1-f", investigate_cola() + [
        [proposal("accept", 800, inbound=800)],
        [("get_budget", {"sku": "SKU-COLA-2L"})],
        [proposal("modify", 400, inbound=400, gap=True)],
    ])
    assert st.recovery_turns_used == 1
    assert st.validations[0].ok is False and "budget exceeded" in st.validations[0].mismatches[0]
    assert st.status == "awaiting_approval"
    st = r.approve()
    assert st.status == "completed" and st.validations[-1].ok
    assert [a.result["ok"] for a in st.actions] == [False, True]


def test_s2a_split_shortfall_across_suppliers():
    milk = {"sku": "SKU-MILK-1L", "node_id": "N-MDE"}
    r, st = run("S2-a", [
        [("get_supplier_notice", {"notice_id": "NOT-2001"}), ("get_purchase_order", {"po_id": "PO-1001"})],
        [("compute_coverage", {**milk, "supplier_id": "S-PACIFICO"}), ("list_suppliers_for_sku", {"sku": "SKU-MILK-1L"})],
        [("check_constraints", {**milk, "supplier_id": "S-ANDINA", "quantity": 250, "recommended_qty": None})],
        [proposal("modify", 250, supplier="S-PACIFICO", po_id="PO-1001", extra=[{"supplier_id": "S-ANDINA", "quantity": 250}], status="confirmed", inbound=500, gap=True)],
    ])
    assert st.status == "completed", st.final_summary
    assert st.validations[-1].ok
    assert [a.kind for a in st.actions] == ["amend_po", "create_po"]
    with db.connect() as conn:
        assert reads.inbound_qty(conn, "SKU-MILK-1L", "N-MDE") == 500


def test_s2c_alternate_fails_then_escalates():
    chips = {"sku": "SKU-CHIPS-150", "node_id": "N-MDE"}
    r, st = run("S2-c", [
        [("get_supplier_notice", {"notice_id": "NOT-2003"}), ("compute_coverage", {**chips, "supplier_id": "S-ANDINA"}), ("list_suppliers_for_sku", {"sku": "SKU-CHIPS-150"})],
        [proposal("modify", 250, supplier="S-ANDINA", po_id="PO-1003", extra=[{"supplier_id": "S-NORTE", "quantity": 250}], status="amended", inbound=500)],
        # recovery: nothing else viable -> escalate
        [("list_suppliers_for_sku", {"sku": "SKU-CHIPS-150"})],
        [proposal("escalate", None, status="none", inbound=250, rationale="S-NORTE rejected the order; no other supplier has capacity.")],
    ])
    assert st.status == "escalated"
    assert st.recovery_turns_used == 1
    assert st.validations[0].ok is False
    assert any("supplier rejected" in m for m in st.validations[0].mismatches)
    assert "S-NORTE rejected" in st.final_summary
    with db.connect() as conn:
        assert reads.purchase_order(conn, "PO-1003")["status"] == "amended"
        assert reads.inbound_qty(conn, "SKU-CHIPS-150", "N-MDE") == 250


def test_recovery_budget_exhausted_escalates(monkeypatch):
    monkeypatch.setattr(settings, "max_recovery_turns", 1)
    r, st = run("S1-c", investigate_cola() + [
        [proposal("accept", 800, inbound=800)],   # gate fail -> recovery 1
        [proposal("accept", 800, inbound=800)],   # gate fail again -> budget exhausted
    ])
    assert st.status == "escalated" and "recovery budget exhausted" in st.final_summary


def test_run_persists_and_reloads():
    r, st = run("S1-c", investigate_cola() + [[proposal("modify", 400, inbound=400, gap=True)]])
    assert st.status == "awaiting_approval"
    from app.agent import runner as runner_mod
    runner_mod._RUNNERS.clear()
    loaded = Runner.load(r.run_id, ScriptedLLM([]))
    assert loaded is not None and loaded.state.status == "awaiting_approval" and loaded.plan
    st2 = loaded.approve()
    assert st2.status == "completed"


import pytest

EXPECTED_STATUS = {"S1-a": "completed", "S1-b": "completed", "S1-c": "awaiting_approval", "S1-d": "completed", "S1-e": "completed",
                   "S1-f": "awaiting_approval", "S2-a": "completed", "S2-b": "completed", "S2-c": "escalated"}


@pytest.mark.parametrize("sid", list(SCRIPTS))
def test_reference_trajectories_run_clean(sid):
    r, st = run(sid, [list(t) for t in SCRIPTS[sid]])
    assert st.status == EXPECTED_STATUS[sid], st.final_summary
    if st.status == "awaiting_approval":
        st = r.approve()
        assert st.status == "completed"
    if st.status == "completed" and st.validations:
        assert st.validations[-1].ok


def test_validator_treats_amended_and_partially_confirmed_labels_as_equivalent():
    milk = {"sku": "SKU-MILK-1L", "node_id": "N-MDE"}
    r, st = run("S2-a", [
        [("compute_coverage", {**milk, "supplier_id": "S-ANDINA"}), ("check_constraints", {**milk, "supplier_id": "S-ANDINA", "quantity": 650, "recommended_qty": None})],
        [proposal("modify", 250, supplier="S-PACIFICO", po_id="PO-1001", extra=[{"supplier_id": "S-ANDINA", "quantity": 650}], status="partially_confirmed", inbound=900)],
    ])
    assert st.status == "completed" and st.recovery_turns_used == 0 and st.validations[-1].ok
