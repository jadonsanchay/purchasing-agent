from fastapi.testclient import TestClient

from app import main
from app.agent.llm import ScriptedLLM

from .scripts import investigate_cola, proposal


def client_with_script(script):
    main._llm_factory = lambda: ScriptedLLM(script)
    return TestClient(main.app)


def test_scenarios_listed():
    c = TestClient(main.app)
    ids = [s["id"] for s in c.get("/api/scenarios").json()]
    assert "S1-a" in ids and "S2-c" in ids


def test_run_lifecycle_via_api():
    c = client_with_script(investigate_cola() + [[proposal("modify", 400, inbound=400, gap=True)]])
    r = c.post("/api/runs", json={"scenario_id": "S1-c", "background": False})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "awaiting_approval"
    assert c.get(f"/api/runs/{run['run_id']}").json()["status"] == "awaiting_approval"
    assert c.get("/api/runs").json()[0]["run_id"] == run["run_id"]
    done = c.post(f"/api/runs/{run['run_id']}/approve", json={"approver": "test"}).json()
    assert done["status"] == "completed"
    assert c.post(f"/api/runs/{run['run_id']}/approve").status_code == 409
    audit = c.get("/api/erp/audit", params={"run_id": run["run_id"]}).json()
    assert any(e["event"] == "po_created" for e in audit)


def test_missing_key_returns_503(monkeypatch):
    main._llm_factory = None
    from app.config import settings
    monkeypatch.setattr(settings, "openai_api_key", None)
    c = TestClient(main.app)
    assert c.post("/api/runs", json={"scenario_id": "S1-a"}).status_code == 503
