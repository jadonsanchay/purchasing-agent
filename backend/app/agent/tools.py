"""Tools the agent may call while investigating, plus `propose_decision`, which ends the investigation.

Only read tools and the proposal are exposed to the LLM. ERP writes are never LLM-callable: the executor
derives them from the accepted Decision after the policy gate (see runner.py).
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from .. import db
from ..erp import reads
from . import policy

ToolFn = Callable[[sqlite3.Connection, dict[str, Any]], Any]


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
        "strict": True,
    }


S = {"type": "string"}
I = {"type": "integer"}

READ_TOOLS: list[dict] = [
    _schema("get_recommendation", "Fetch a system purchasing recommendation by id.", {"rec_id": S}, ["rec_id"]),
    _schema("get_product", "Product master data: category, unit cost, unit volume, safety stock days.", {"sku": S}, ["sku"]),
    _schema("get_inventory", "On-hand, reserved and available units for a SKU at a node.", {"sku": S, "node_id": S}, ["sku", "node_id"]),
    _schema("get_demand", "Forecast for the next 28 days and actual sales for the last 14 days, with averages and recent-vs-forecast deviation.", {"sku": S, "node_id": S}, ["sku", "node_id"]),
    _schema("get_open_purchase_orders", "Open POs for a SKU at a node with ordered, confirmed and realistically expected quantities (supplier notices applied).", {"sku": S, "node_id": S}, ["sku", "node_id"]),
    _schema("get_purchase_order", "Full detail of one purchase order, including any supplier notices against it.", {"po_id": S}, ["po_id"]),
    _schema("get_supplier_notice", "A supplier's message about a PO it cannot fully fulfil.", {"notice_id": S}, ["notice_id"]),
    _schema("get_supplier", "Supplier profile (lead time, reliability, terms) and its offer for a SKU (price, MOQ, pack size, capacity).", {"supplier_id": S, "sku": S}, ["supplier_id", "sku"]),
    _schema("list_suppliers_for_sku", "All suppliers that list a SKU, cheapest first, with lead time, reliability, MOQ, pack size and available capacity.", {"sku": S}, ["sku"]),
    _schema("get_budget", "Purchasing budget for the product's category this period: allocated, committed, headroom.", {"sku": S}, ["sku"]),
    _schema("get_storage", "Storage capacity at a node: total, used, reserved for inbound, free (m3).", {"node_id": S}, ["node_id"]),
    _schema(
        "compute_coverage",
        "Deterministic coverage analysis for a SKU/node if buying from a given supplier: net requirement, days of cover, projected stockout day, suggested order quantity (rounded to pack/MOQ), demand-signal warnings.",
        {"sku": S, "node_id": S, "supplier_id": S},
        ["sku", "node_id", "supplier_id"],
    ),
    _schema(
        "check_constraints",
        "Deterministic constraint check for a candidate order: MOQ, pack size, budget, storage, supplier capacity, over-cover, lead time vs stockout, demand signal. Also reports whether human approval would be required and the max feasible quantity. Call this before proposing any purchase.",
        {"sku": S, "node_id": S, "supplier_id": S, "quantity": I, "recommended_qty": {"type": ["integer", "null"], "description": "System recommendation to measure deviation against, if any"}},
        ["sku", "node_id", "supplier_id", "quantity", "recommended_qty"],
    ),
]

PROPOSE_TOOL = _schema(
    "propose_decision",
    "Submit your final decision. This ends investigation. The system will gate it against policy, may ask a human to approve, execute any resulting ERP changes, and validate the outcome against expected_outcome. If validation fails you will be asked to revise.",
    {
        "action": {"type": "string", "enum": ["accept", "modify", "reject", "investigate", "escalate"]},
        "quantity": {"type": ["integer", "null"], "description": "Units to order (accept/modify with a new PO) or to keep on the amended PO (when po_id is set). 0 with po_id cancels the PO."},
        "supplier_id": {"type": ["string", "null"], "description": "Supplier for a new PO. Defaults to the recommendation's supplier."},
        "po_id": {"type": ["string", "null"], "description": "Existing PO to amend/cancel (supplier shortfall situations)."},
        "additional_orders": {
            "type": "array",
            "description": "Extra new POs to create, e.g. sourcing the remainder of a shortfall from another supplier.",
            "items": {"type": "object", "properties": {"supplier_id": S, "quantity": I}, "required": ["supplier_id", "quantity"], "additionalProperties": False},
        },
        "rationale": {"type": "string", "description": "2-5 sentences a buyer can read: what you found and why this action."},
        "key_factors": {"type": "array", "items": S, "description": "Short bullets of the decisive facts (numbers included)."},
        "confidence": {"type": "number", "description": "0..1"},
        "expected_outcome": {
            "type": "object",
            "description": "What you expect after execution, checked by the validator.",
            "properties": {
                "po_status": {"type": ["string", "null"], "description": "Expected status of the created/amended PO(s): confirmed | amended | partially_confirmed | cancelled | none"},
                "total_inbound_after": {"type": ["integer", "null"], "description": "Expected total expected-inbound units for the SKU at the node after execution"},
                "coverage_gap_accepted": {"type": ["boolean", "null"], "description": "True if you knowingly leave demand uncovered (e.g. budget-capped)"},
            },
            "required": ["po_status", "total_inbound_after", "coverage_gap_accepted"],
            "additionalProperties": False,
        },
    },
    ["action", "quantity", "supplier_id", "po_id", "additional_orders", "rationale", "key_factors", "confidence", "expected_outcome"],
)

ALL_TOOLS = READ_TOOLS + [PROPOSE_TOOL]


def _budget(conn, a):
    prod = reads.product(conn, a["sku"])
    if not prod:
        return {"error": f"unknown sku {a['sku']}"}
    b = reads.budget(conn, prod["category"])
    return {**b, "sku": a["sku"]} if b else {"error": "no budget row"}


def _po(conn, a):
    po = reads.purchase_order(conn, a["po_id"])
    if not po:
        return {"error": f"{a['po_id']} not found"}
    po["notices"] = reads.notices_for_po(conn, a["po_id"])
    return po


DISPATCH: dict[str, ToolFn] = {
    "get_recommendation": lambda c, a: reads.recommendation(c, a["rec_id"]) or {"error": "not found"},
    "get_product": lambda c, a: reads.product(c, a["sku"]) or {"error": "not found"},
    "get_inventory": lambda c, a: reads.inventory(c, a["sku"], a["node_id"]) or {"error": "not found"},
    "get_demand": lambda c, a: reads.demand(c, a["sku"], a["node_id"]),
    "get_open_purchase_orders": lambda c, a: reads.expected_inbound(c, a["sku"], a["node_id"]),
    "get_purchase_order": _po,
    "get_supplier_notice": lambda c, a: reads.supplier_notice(c, a["notice_id"]) or {"error": "not found"},
    "get_supplier": lambda c, a: reads.supplier(c, a["supplier_id"], a["sku"]) or {"error": "not found"},
    "list_suppliers_for_sku": lambda c, a: reads.suppliers_for_sku(c, a["sku"]),
    "get_budget": _budget,
    "get_storage": lambda c, a: reads.storage(c, a["node_id"]) or {"error": "not found"},
    "compute_coverage": lambda c, a: policy.compute_coverage(c, a["sku"], a["node_id"], a["supplier_id"]).model_dump(),
    "check_constraints": lambda c, a: policy.check_constraints(c, a["sku"], a["node_id"], a["supplier_id"], a["quantity"], a.get("recommended_qty")).model_dump(),
}


def call_tool(conn: sqlite3.Connection, run_id: str, name: str, arguments: dict[str, Any]) -> Any:
    fn = DISPATCH.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    try:
        result = fn(conn, arguments)
    except Exception as e:  # tool errors go back to the model, never crash the run
        result = {"error": str(e)}
    db.audit(conn, run_id, "agent", "tool_call", {"tool": name, "arguments": arguments, "result": result})
    return result


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)
