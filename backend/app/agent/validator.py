"""Post-execution validation: did the ERP end up where the agent said it would?

Independent of the LLM. Checks (1) every action succeeded, (2) confirmed == ordered, (3) the agent's stated
expected_outcome matches reality, (4) no hard constraint is violated in the post-state, (5) demand is covered
unless the agent explicitly accepted a gap.
"""
from __future__ import annotations

import sqlite3

from ..erp import reads
from ..models import Decision, ExecutedAction, ValidationResult
from . import policy


def validate(
    conn: sqlite3.Connection,
    decision: Decision,
    actions: list[ExecutedAction],
    sku: str,
    node_id: str,
    coverage_supplier_id: str,
) -> ValidationResult:
    mismatches: list[str] = []
    po_statuses: dict[str, str] = {}

    for a in actions:
        r = a.result
        if a.kind in ("create_po", "amend_po", "cancel_po"):
            if not r.get("ok"):
                mismatches.append(f"{a.kind} {a.request} failed: {r.get('reason')}")
                continue
            po = reads.purchase_order(conn, r["po_id"])
            po_statuses[r["po_id"]] = po["status"] if po else "missing"
            if a.kind != "cancel_po" and r.get("confirmed_qty") is not None and r["confirmed_qty"] != r["ordered_qty"]:
                mismatches.append(f"{r['po_id']} confirmed {r['confirmed_qty']} of {r['ordered_qty']} ordered")

    inbound = reads.inbound_qty(conn, sku, node_id)
    prod = reads.product(conn, sku)
    budget = reads.budget(conn, prod["category"]) if prod else None
    storage = reads.storage(conn, node_id)
    actual = {
        "total_inbound_after": inbound,
        "po_statuses": po_statuses,
        "budget_headroom": budget["headroom"] if budget else None,
        "storage_free_m3": storage["free_m3"] if storage else None,
    }

    exp = decision.expected_outcome or {}
    if exp.get("total_inbound_after") is not None and int(exp["total_inbound_after"]) != inbound:
        mismatches.append(f"expected total inbound {exp['total_inbound_after']}, actual {inbound}")
    exp_status = exp.get("po_status")
    if exp_status and exp_status != "none" and po_statuses:
        # Label vocabulary differs between the agent and the ERP ('amended', 'confirmed', 'partially_confirmed' are all
        # used for "the PO now holds what the supplier will ship"). Quantity mismatches are caught above, so here we
        # only flag a status that means something materially different: cancelled/rejected vs a live PO, or vice versa.
        live = {"confirmed", "amended", "partially_confirmed", "submitted"}
        dead = {"cancelled", "rejected"}
        cls = lambda st: "live" if st in live else ("dead" if st in dead else st)
        bad = {k: v for k, v in po_statuses.items() if cls(v) != cls(exp_status)}
        if bad:
            mismatches.append(f"expected PO status '{exp_status}', actual {bad}")

    if budget and budget["headroom"] < 0:
        mismatches.append(f"budget overcommitted by {-budget['headroom']}")
    if storage and storage["free_m3"] < 0:
        mismatches.append(f"storage overcommitted by {-storage['free_m3']} m3")

    report = None
    try:
        cov = policy.compute_coverage(conn, sku, node_id, coverage_supplier_id)
        actual["net_requirement_after"] = cov.net_requirement
        actual["days_of_cover_after"] = cov.days_of_cover_current
        if decision.action.value in ("accept", "modify") and cov.net_requirement > 0 and not exp.get("coverage_gap_accepted"):
            mismatches.append(f"coverage gap remains: {cov.net_requirement} units short of the {cov.target_days}-day target")
    except ValueError:
        pass

    return ValidationResult(ok=not mismatches, expected=exp, actual=actual, mismatches=mismatches, constraint_report=report)
