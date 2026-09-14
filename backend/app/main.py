"""FastAPI app: scenario loading, agent runs, human approval, and ERP inspection endpoints."""
from __future__ import annotations

import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import db, scenarios
from .agent.llm import LLM, OpenAILLM, ScriptedLLM
from .agent.scripted import SCRIPTS
from .agent.runner import Runner, get_runner
from .config import settings
from .erp import reads

@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_schema()
    yield


app = FastAPI(title="AI Purchasing Agent", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health() -> dict:
    scripted = settings.llm_provider == "scripted"
    return {"ok": True, "llm_configured": scripted or bool(settings.openai_api_key), "model": "scripted reference trajectories" if scripted else settings.openai_model}


# ---------------------------------------------------------------- scenarios

@app.get("/api/scenarios")
def list_scenarios() -> list[dict]:
    return [s.public() for s in scenarios.SCENARIOS.values()]


@app.post("/api/scenarios/{scenario_id}/load")
def load_scenario(scenario_id: str) -> dict:
    if scenario_id not in scenarios.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {scenario_id}")
    return scenarios.load_scenario(scenario_id).public()


# ---------------------------------------------------------------- runs

_llm_factory = None  # tests override this with a ScriptedLLM factory


def make_llm(scenario_id: str | None = None) -> LLM:
    if _llm_factory is not None:
        return _llm_factory()
    if settings.llm_provider == "scripted":
        return ScriptedLLM(SCRIPTS.get(scenario_id or "", []))
    if not settings.openai_api_key:
        raise HTTPException(503, "OPENAI_API_KEY is not set; copy .env.example to backend/.env and add a key (or set LLM_PROVIDER=scripted)")
    return OpenAILLM()


class StartRun(BaseModel):
    scenario_id: str
    background: bool = True


def _public(state) -> dict:
    return state.model_dump(mode="json")


@app.post("/api/runs", status_code=201)
def start_run(body: StartRun) -> dict:
    if body.scenario_id not in scenarios.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {body.scenario_id}")
    llm = make_llm(body.scenario_id)
    scenario = scenarios.load_scenario(body.scenario_id)
    runner = Runner(scenario, llm)
    runner.save()
    if body.background:
        threading.Thread(target=runner.start, daemon=True).start()
    else:
        runner.start()
    return _public(runner.state)


@app.get("/api/runs")
def list_runs() -> list[dict]:
    with db.connect() as conn:
        return db.rows(conn.execute("SELECT run_id, scenario_id, status, created_at, updated_at FROM runs ORDER BY created_at DESC"))


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    runner = get_runner(run_id, make_llm())
    if not runner:
        raise HTTPException(404)
    return _public(runner.state)


class Approval(BaseModel):
    approver: str = "buyer"
    note: str = ""


@app.post("/api/runs/{run_id}/approve")
def approve_run(run_id: str, body: Approval | None = None) -> dict:
    runner = get_runner(run_id, make_llm())
    if not runner:
        raise HTTPException(404)
    if runner.state.status != "awaiting_approval":
        raise HTTPException(409, f"run is {runner.state.status}, not awaiting approval")
    return _public(runner.approve((body or Approval()).approver))


@app.post("/api/runs/{run_id}/reject")
def reject_run(run_id: str, body: Approval | None = None) -> dict:
    runner = get_runner(run_id, make_llm())
    if not runner:
        raise HTTPException(404)
    if runner.state.status != "awaiting_approval":
        raise HTTPException(409, f"run is {runner.state.status}, not awaiting approval")
    b = body or Approval()
    return _public(runner.reject(b.approver, b.note))


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
