"""Reusable scripted agent behaviours for tests and offline evals."""

COLA = {"sku": "SKU-COLA-2L", "node_id": "N-BOG", "supplier_id": "S-ANDINA"}


def proposal(action, qty=None, supplier="S-ANDINA", po_id=None, extra=None, status="confirmed", inbound=None, gap=False, rationale="scripted"):
    return (
        "propose_decision",
        {
            "action": action,
            "quantity": qty,
            "supplier_id": supplier,
            "po_id": po_id,
            "additional_orders": extra or [],
            "rationale": rationale,
            "key_factors": ["scripted"],
            "confidence": 0.8,
            "expected_outcome": {"po_status": status, "total_inbound_after": inbound, "coverage_gap_accepted": gap},
        },
    )


def investigate_cola(qty=800):
    return [
        [("get_recommendation", {"rec_id": "REC-1001"}), ("get_inventory", {"sku": "SKU-COLA-2L", "node_id": "N-BOG"}), ("get_demand", {"sku": "SKU-COLA-2L", "node_id": "N-BOG"})],
        [("get_open_purchase_orders", {"sku": "SKU-COLA-2L", "node_id": "N-BOG"}), ("compute_coverage", COLA), ("get_budget", {"sku": "SKU-COLA-2L"}), ("get_storage", {"node_id": "N-BOG"})],
        [("check_constraints", {**COLA, "quantity": qty, "recommended_qty": 800})],
    ]
