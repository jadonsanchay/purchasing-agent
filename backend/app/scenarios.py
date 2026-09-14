"""Scenario fixtures. Each scenario = base dataset + a variation + the situation handed to the agent.

Loading a scenario resets the database, so runs are reproducible. Expected outcomes for evaluation live
in evals/cases.yaml, not here; this module only describes the world the agent wakes up in.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from . import db, seed


@dataclass
class Scenario:
    id: str
    title: str
    kind: str  # recommendation_review | supplier_shortfall
    description: str  # what a buyer would see
    situation: str  # the message the agent receives
    context: dict = field(default_factory=dict)
    setup: Callable[[sqlite3.Connection], None] = lambda conn: None

    def public(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "description": self.description,
            "context": self.context,
        }


# ---------------------------------------------------------------- Scenario 1: recommendation review

REC_800 = dict(rec_id="REC-1001", sku="SKU-COLA-2L", node_id="N-BOG", supplier_id="S-ANDINA", qty=800)
S1_SITUATION = (
    "The replenishment system recommends buying 800 units of SKU-COLA-2L for node N-BOG from supplier "
    "S-ANDINA (recommendation REC-1001). Review the recommendation and decide whether to accept, modify, "
    "reject, or investigate it. Execute the resulting purchase order if appropriate."
)


def _rec(conn: sqlite3.Connection, reason: str) -> None:
    seed.add_recommendation(conn, REC_800["rec_id"], REC_800["sku"], REC_800["node_id"], REC_800["supplier_id"], REC_800["qty"], reason)


def s1a(conn: sqlite3.Connection) -> None:
    # 60/day, lead 5 + review 7 + safety 3 = 15 days -> 900 needed; on hand 150 -> net 750 -> pack 50 -> 800. Clean accept.
    _rec(conn, "Projected stockout in 2.5 days; reorder to cover lead time + review period + safety stock.")


def s1b(conn: sqlite3.Connection) -> None:
    # An open PO of 700 already inbound in 2 days. Net requirement drops to ~50, below MOQ 200.
    seed.add_po(conn, "PO-0998", "S-ANDINA", "N-BOG", "SKU-COLA-2L", 700, status="confirmed", expected_delivery_day=2)
    _rec(conn, "Projected stockout in 2.5 days; reorder to cover lead time + review period + safety stock. (Recommender does not see open POs.)")


def s1c(conn: sqlite3.Connection) -> None:
    # Beverages budget almost exhausted: headroom 500 -> at most 416 units -> 400 after pack rounding.
    conn.execute("UPDATE budgets SET allocated=10000, committed=9500 WHERE category='beverages'")
    _rec(conn, "Projected stockout in 2.5 days; reorder to cover lead time + review period + safety stock.")


def s1d(conn: sqlite3.Connection) -> None:
    # Only 1.5 m3 free at N-BOG -> 600 units max at 0.0025 m3 each.
    conn.execute("UPDATE storage SET used_m3=498.5 WHERE node_id='N-BOG'")
    _rec(conn, "Projected stockout in 2.5 days; reorder to cover lead time + review period + safety stock.")


def s1e(conn: sqlite3.Connection) -> None:
    # Forecast says 60/day but the last 7 days sold ~110/day. Recommendation is based on the stale forecast.
    seed.set_demand(conn, "SKU-COLA-2L", "N-BOG", forecast_daily=60, actual_daily=60, actual_recent_daily=110)
    _rec(conn, "Projected stockout in 2.5 days; reorder to cover lead time + review period + safety stock.")


def s1f(conn: sqlite3.Connection) -> None:
    # Looks fine at read time, but another buyer commits 11,500 of beverages budget between the agent's
    # check and its PO submission. The ERP rejects the PO on budget. Exercises the feedback loop.
    seed.set_fault(conn, "budget:beverages:concurrent_spend", "11500")
    _rec(conn, "Projected stockout in 2.5 days; reorder to cover lead time + review period + safety stock.")


# ---------------------------------------------------------------- Scenario 2: supplier cannot fulfil

def _shortfall_situation(po_id: str, sku: str, node: str, supplier: str, ordered: int, can_supply: int, notice_id: str) -> str:
    return (
        f"Supplier {supplier} has informed us (notice {notice_id}) that purchase order {po_id} for {ordered} units of "
        f"{sku} to node {node} can only be fulfilled for {can_supply} units right now. Determine what should happen "
        "next: accept the partial, source the remainder elsewhere, cancel it, or escalate. Take the appropriate actions."
    )


def s2a(conn: sqlite3.Connection) -> None:
    # Milk at N-MDE: 70/day, on hand 120. PO for 500 from S-PACIFICO (lead 3); only 250 available.
    # S-ANDINA also sells milk (lead 5, moq 100, pack 50, price 1.02). Coverage needs the remainder.
    seed.add_po(conn, "PO-1001", "S-PACIFICO", "N-MDE", "SKU-MILK-1L", 500, status="submitted", confirmed_qty=None, expected_delivery_day=3)
    conn.execute("UPDATE supplier_products SET available_capacity=250 WHERE supplier_id='S-PACIFICO' AND sku='SKU-MILK-1L'")
    seed.add_supplier_notice(conn, "NOT-2001", "PO-1001", "SKU-MILK-1L", 250, "Production shortfall this week; can ship 250 of 500 on schedule.")


def s2b(conn: sqlite3.Connection) -> None:
    # Rice at N-BOG: 20/day, on hand 400. PO for 500 from S-NORTE (lead 7); only 250 available.
    # Alternate S-PACIFICO has zero capacity. But 400 on hand + 250 inbound covers 17 days of need (340). Accept partial.
    seed.add_po(conn, "PO-1002", "S-NORTE", "N-BOG", "SKU-RICE-1KG", 500, status="submitted", confirmed_qty=None, expected_delivery_day=7)
    conn.execute("UPDATE supplier_products SET available_capacity=250 WHERE supplier_id='S-NORTE' AND sku='SKU-RICE-1KG'")
    seed.add_supplier_notice(conn, "NOT-2002", "PO-1002", "SKU-RICE-1KG", 250, "Import delay; can release 250 of 500 now, balance unknown.")


def s2c(conn: sqlite3.Connection) -> None:
    # Chips at N-MDE: 30/day, on hand 90. PO for 500 from S-ANDINA (lead 5); only 250 available.
    # S-NORTE lists 1000 capacity for chips, but rejects the new PO when the agent actually submits it.
    # Agent should notice the failed action, have no other option, and escalate with a clear summary.
    seed.add_po(conn, "PO-1003", "S-ANDINA", "N-MDE", "SKU-CHIPS-150", 500, status="submitted", confirmed_qty=None, expected_delivery_day=5)
    conn.execute("UPDATE supplier_products SET available_capacity=250 WHERE supplier_id='S-ANDINA' AND sku='SKU-CHIPS-150'")
    seed.add_supplier_notice(conn, "NOT-2003", "PO-1003", "SKU-CHIPS-150", 250, "Line changeover; 250 of 500 available, remainder in 3 weeks.")
    seed.set_fault(conn, "supplier:S-NORTE:reject_new_po", "Capacity allocation failed: committed to another customer.")


SCENARIOS: dict[str, Scenario] = {
    s.id: s
    for s in [
        Scenario("S1-a", "Recommendation review: baseline", "recommendation_review",
                 "800 units recommended. Inventory, demand, budget and storage all support it.",
                 S1_SITUATION, dict(REC_800), s1a),
        Scenario("S1-b", "Recommendation review: open PO already covers demand", "recommendation_review",
                 "800 units recommended, but 700 units are already inbound in 2 days. Recommender ignored open POs.",
                 S1_SITUATION, dict(REC_800), s1b),
        Scenario("S1-c", "Recommendation review: budget nearly exhausted", "recommendation_review",
                 "800 units recommended, but only 500 of beverages budget remains this period.",
                 S1_SITUATION, dict(REC_800), s1c),
        Scenario("S1-d", "Recommendation review: storage constraint", "recommendation_review",
                 "800 units recommended, but N-BOG only has room for ~600 units.",
                 S1_SITUATION, dict(REC_800), s1d),
        Scenario("S1-e", "Recommendation review: forecast and actuals disagree", "recommendation_review",
                 "800 units recommended off a 60/day forecast, but the last week sold ~110/day.",
                 S1_SITUATION, dict(REC_800), s1e),
        Scenario("S1-f", "Recommendation review: budget consumed during execution", "recommendation_review",
                 "800 units recommended and everything checks out, but the budget is consumed by someone else before the PO lands.",
                 S1_SITUATION, dict(REC_800), s1f),
        Scenario("S2-a", "Supplier short-fill: alternate supplier available", "supplier_shortfall",
                 "PO-1001 for 500 milk; supplier can ship 250. Demand needs the rest; S-ANDINA can supply it.",
                 _shortfall_situation("PO-1001", "SKU-MILK-1L", "N-MDE", "S-PACIFICO", 500, 250, "NOT-2001"),
                 dict(po_id="PO-1001", notice_id="NOT-2001", sku="SKU-MILK-1L", node_id="N-MDE"), s2a),
        Scenario("S2-b", "Supplier short-fill: partial is enough", "supplier_shortfall",
                 "PO-1002 for 500 rice; supplier can ship 250. Inventory plus 250 already covers demand; no alternate has capacity.",
                 _shortfall_situation("PO-1002", "SKU-RICE-1KG", "N-BOG", "S-NORTE", 500, 250, "NOT-2002"),
                 dict(po_id="PO-1002", notice_id="NOT-2002", sku="SKU-RICE-1KG", node_id="N-BOG"), s2b),
        Scenario("S2-c", "Supplier short-fill: alternate fails on execution", "supplier_shortfall",
                 "PO-1003 for 500 chips; supplier can ship 250. Alternate looks viable but rejects the PO when submitted.",
                 _shortfall_situation("PO-1003", "SKU-CHIPS-150", "N-MDE", "S-ANDINA", 500, 250, "NOT-2003"),
                 dict(po_id="PO-1003", notice_id="NOT-2003", sku="SKU-CHIPS-150", node_id="N-MDE"), s2c),
    ]
}


def load_scenario(scenario_id: str) -> Scenario:
    scenario = SCENARIOS[scenario_id]
    db.reset_db()
    with db.connect() as conn:
        seed.seed_base(conn)
        scenario.setup(conn)
        db.audit(conn, None, "erp", "scenario_loaded", {"scenario_id": scenario_id})
    return scenario
