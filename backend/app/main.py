"""FastAPI app: scenario loading, agent runs, human approval, and ERP inspection endpoints."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from . import db, scenarios
from .erp import reads

app = FastAPI(title="AI Purchasing Agent", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    db.init_schema()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


# ---------------------------------------------------------------- scenarios

@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [s.public() for s in scenarios.SCENARIOS.values()]


@app.post("/api/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str) -> dict:
    if scenario_id not in scenarios.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {scenario_id}")
    return scenarios.load_scenario(scenario_id).public()


# ---------------------------------------------------------------- ERP inspection (read-only, for the UI and for debugging)

@app.get("/api/erp/inventory/{sku}/{node_id}")
def erp_inventory(sku: str, node_id: str) -> dict:
    with db.connect() as conn:
        row = reads.inventory(conn, sku, node_id)
    if not row:
        raise HTTPException(404)
    return row


@app.get("/api/erp/demand/{sku}/{node_id}")
def erp_demand(sku: str, node_id: str) -> dict:
    with db.connect() as conn:
        return reads.demand(conn, sku, node_id)


@app.get("/api/erp/purchase-orders/{po_id}")
def erp_po(po_id: str) -> dict:
    with db.connect() as conn:
        po = reads.purchase_order(conn, po_id)
    if not po:
        raise HTTPException(404)
    return po


@app.get("/api/erp/purchase-orders")
def erp_pos() -> list[dict]:
    with db.connect() as conn:
        ids = [r["po_id"] for r in db.rows(conn.execute("SELECT po_id FROM purchase_orders ORDER BY created_at"))]
        return [reads.purchase_order(conn, i) for i in ids]


@app.get("/api/erp/suppliers/{sku}")
def erp_suppliers(sku: str) -> list[dict]:
    with db.connect() as conn:
        return reads.suppliers_for_sku(conn, sku)


@app.get("/api/erp/budget/{category}")
def erp_budget(category: str) -> dict:
    with db.connect() as conn:
        b = reads.budget(conn, category)
    if not b:
        raise HTTPException(404)
    return b


@app.get("/api/erp/storage/{node_id}")
def erp_storage(node_id: str) -> dict:
    with db.connect() as conn:
        s = reads.storage(conn, node_id)
    if not s:
        raise HTTPException(404)
    return s


@app.get("/api/erp/audit")
def erp_audit(run_id: str | None = None) -> list[dict]:
    with db.connect() as conn:
        if run_id:
            return db.rows(conn.execute("SELECT * FROM audit_log WHERE run_id=? ORDER BY id", (run_id,)))
        return db.rows(conn.execute("SELECT * FROM audit_log ORDER BY id"))
