"""Reference trajectories: what a competent agent should do in each scenario, expressed as a ScriptedLLM script.

Used for (1) tests, (2) `LLM_PROVIDER=scripted` demo mode when no OpenAI key is available, and (3) a harness
self-check in the evals, where the rubric must score these trajectories as passing.
"""
from __future__ import annotations

from typing import Any

COLA = {"sku": "SKU-COLA-2L", "node_id": "N-BOG", "supplier_id": "S-ANDINA"}
MILK = {"sku": "SKU-MILK-1L", "node_id": "N-MDE"}
RICE = {"sku": "SKU-RICE-1KG", "node_id": "N-BOG"}
CHIPS = {"sku": "SKU-CHIPS-150", "node_id": "N-MDE"}


def proposal(action, qty=None, supplier="S-ANDINA", po_id=None, extra=None, status="confirmed", inbound=None, gap=False, rationale="scripted", factors=None, confidence=0.8):
    return (
        "propose_decision",
        {
            "action": action,
            "quantity": qty,
            "supplier_id": supplier,
            "po_id": po_id,
            "additional_orders": extra or [],
            "rationale": rationale,
            "key_factors": factors or ["scripted reference trajectory"],
            "confidence": confidence,
            "expected_outcome": {"po_status": status, "total_inbound_after": inbound, "coverage_gap_accepted": gap},
        },
    )


def investigate_cola(qty=800):
    return [
        [("get_recommendation", {"rec_id": "REC-1001"}), ("get_inventory", {"sku": "SKU-COLA-2L", "node_id": "N-BOG"}), ("get_demand", {"sku": "SKU-COLA-2L", "node_id": "N-BOG"})],
        [("get_open_purchase_orders", {"sku": "SKU-COLA-2L", "node_id": "N-BOG"}), ("compute_coverage", COLA), ("get_budget", {"sku": "SKU-COLA-2L"}), ("get_storage", {"node_id": "N-BOG"})],
        [("check_constraints", {**COLA, "quantity": qty, "recommended_qty": 800})],
    ]


def investigate_shortfall(notice_id, po_id, sku, node, supplier, alt=None, alt_qty=None):
    turns: list[list[tuple[str, dict[str, Any]]]] = [
        [("get_supplier_notice", {"notice_id": notice_id}), ("get_purchase_order", {"po_id": po_id}), ("get_inventory", {"sku": sku, "node_id": node})],
        [("get_demand", {"sku": sku, "node_id": node}), ("compute_coverage", {"sku": sku, "node_id": node, "supplier_id": supplier}), ("list_suppliers_for_sku", {"sku": sku})],
    ]
    if alt:
        turns.append([("get_supplier", {"supplier_id": alt, "sku": sku}), ("check_constraints", {"sku": sku, "node_id": node, "supplier_id": alt, "quantity": alt_qty, "recommended_qty": None})])
    return turns


SCRIPTS: dict[str, list[list[tuple[str, dict[str, Any]]]]] = {
    "S1-a": investigate_cola() + [[proposal("accept", 800, inbound=800,
        rationale="Net requirement is 750 units over the 15-day coverage window (5d lead + 7d review + 3d safety); 800 is the pack-rounded quantity. Budget headroom 12,000 and 120 m3 free storage comfortably cover a 960 PO.",
        factors=["available 150, inbound 0, forecast 900 over 15d", "MOQ 200 / pack 100 -> 800", "PO value 960 vs headroom 12,000"], confidence=0.92)]],
    "S1-b": investigate_cola() + [[proposal("reject", None, status="none", inbound=700,
        rationale="PO-0998 already brings 700 units in 2 days. Net requirement drops to 50 units, below the 200 MOQ, and buying 800 would leave 26 days of cover versus a 15-day target. The recommender did not account for open POs.",
        factors=["inbound 700 on day 2", "net requirement 50 < MOQ 200", "over-cover 26d vs 15d target"], confidence=0.9)]],
    "S1-c": investigate_cola() + [
        [("check_constraints", {**COLA, "quantity": 400, "recommended_qty": 800})],
        [proposal("modify", 400, inbound=400, gap=True,
        rationale="Demand supports 800 but beverages budget headroom is 500, capping the order at 400 units (480). This covers ~9 days; the remaining 350-unit gap needs a budget top-up or a follow-up order next period.",
        factors=["budget headroom 500 -> max feasible 400", "gap of 350 units accepted", "50% deviation triggers approval"], confidence=0.75)]],
    "S1-d": investigate_cola() + [
        [("check_constraints", {**COLA, "quantity": 600, "recommended_qty": 800})],
        [proposal("modify", 600, inbound=600, gap=True,
        rationale="N-BOG has 1.5 m3 free, room for 600 units at 0.0025 m3 each. Ordering 600 now covers ~12 days; the remaining 150 units should follow once storage frees up.",
        factors=["storage free 1.5 m3 -> max 600 units", "25% deviation, no approval needed"], confidence=0.8)]],
    "S1-e": investigate_cola() + [[proposal("investigate", None, status="none", inbound=0,
        rationale="Forecast says 60/day but the last 7 days sold 110/day (+83%). If the shift is real, 800 units is short by ~800; if it was a promotion, 800 is right. Need: cause of the spike (promo calendar, competitor stockout), whether the forecast has been re-run, and a 3-day read of post-spike sales before committing.",
        factors=["actuals 110/day vs forecast 60/day", "stockout projected day 1 either way", "evidence needed before sizing"], confidence=0.6)]],
    "S1-f": investigate_cola() + [
        [proposal("accept", 800, inbound=800, rationale="All checks pass for 800 units.", factors=["net 750", "headroom 12,000"], confidence=0.9)],
        [("get_budget", {"sku": "SKU-COLA-2L"}), ("check_constraints", {**COLA, "quantity": 400, "recommended_qty": 800})],
        [proposal("modify", 400, inbound=400, gap=True,
        rationale="The PO was rejected: budget headroom fell from 12,000 to 500 between my check and submission (another commitment landed). 400 units is now the maximum feasible; the 350-unit gap needs a budget decision.",
        factors=["ERP rejected 800 on budget", "headroom now 500 -> max 400"], confidence=0.7)]],
    "S2-a": investigate_shortfall("NOT-2001", "PO-1001", "SKU-MILK-1L", "N-MDE", "S-PACIFICO", alt="S-ANDINA", alt_qty=250) + [
        [proposal("modify", 250, supplier="S-PACIFICO", po_id="PO-1001", extra=[{"supplier_id": "S-ANDINA", "quantity": 250}], status="confirmed", inbound=500, gap=True,
        rationale="With only 250 inbound, N-MDE has 5 days of milk against a 12-day target. Amend PO-1001 to the 250 S-PACIFICO can ship and source 250 from S-ANDINA (lead 5d, reliability 0.92, +7% price) to close most of the gap.",
        factors=["available 120 + inbound 250 vs 840 needed", "S-ANDINA capacity 2000, MOQ 100", "budget headroom sufficient"], confidence=0.85)]],
    "S2-b": investigate_shortfall("NOT-2002", "PO-1002", "SKU-RICE-1KG", "N-BOG", "S-NORTE") + [
        [proposal("modify", 250, supplier="S-NORTE", po_id="PO-1002", status="amended", inbound=250,
        rationale="400 on hand plus 250 inbound covers 17 days at 20/day against a 17-day target. Accept the partial, amend PO-1002 to 250, and do not source elsewhere: S-PACIFICO has zero capacity and there is no gap to fill.",
        factors=["net requirement negative", "no alternate capacity", "releases 275 of grocery budget"], confidence=0.9)]],
    "S2-c": investigate_shortfall("NOT-2003", "PO-1003", "SKU-CHIPS-150", "N-MDE", "S-ANDINA", alt="S-NORTE", alt_qty=250) + [
        [proposal("modify", 250, supplier="S-ANDINA", po_id="PO-1003", extra=[{"supplier_id": "S-NORTE", "quantity": 250}], status="confirmed", inbound=500,
        rationale="Amend PO-1003 to 250 and source 250 from S-NORTE, which lists 1000 units of capacity.", factors=["available 90 + 250 vs 510 needed"], confidence=0.8)],
        [("list_suppliers_for_sku", {"sku": "SKU-CHIPS-150"}), ("compute_coverage", {**CHIPS, "supplier_id": "S-ANDINA"})],
        [proposal("escalate", None, status="none", inbound=250,
        rationale="S-NORTE rejected the 250-unit order (capacity committed elsewhere) and no other supplier lists chips. PO-1003 is amended to 250, which covers ~11 days. Options for the buyer: negotiate expedited remainder with S-ANDINA, onboard a new supplier, or accept a partial stockout around day 11.",
        factors=["alternate PO rejected", "no third supplier", "11 days of cover after partial"], confidence=0.85)]],
}
