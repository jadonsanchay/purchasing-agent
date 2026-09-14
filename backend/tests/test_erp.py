import pytest

from app import db, scenarios
from app.erp import reads, writes


@pytest.mark.parametrize("sid", list(scenarios.SCENARIOS))
def test_every_scenario_loads(sid):
    s = scenarios.load_scenario(sid)
    assert s.id == sid
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 5


def test_create_po_happy_path_updates_budget_and_storage():
    scenarios.load_scenario("S1-a")
    with db.connect() as conn:
        before_b = reads.budget(conn, "beverages")["headroom"]
        before_s = reads.storage(conn, "N-BOG")["free_m3"]
        res = writes.create_purchase_order(conn, "r1", "S-ANDINA", "N-BOG", "SKU-COLA-2L", 800)
        assert res["ok"] and res["status"] == "confirmed" and res["confirmed_qty"] == 800
        assert reads.budget(conn, "beverages")["headroom"] == before_b - 960
        assert round(before_s - reads.storage(conn, "N-BOG")["free_m3"], 4) == 2.0
        assert reads.inbound_qty(conn, "SKU-COLA-2L", "N-BOG") == 800


def test_create_po_rejects_moq_and_pack():
    scenarios.load_scenario("S1-a")
    with db.connect() as conn:
        assert not writes.create_purchase_order(conn, None, "S-ANDINA", "N-BOG", "SKU-COLA-2L", 150)["ok"]
        assert not writes.create_purchase_order(conn, None, "S-ANDINA", "N-BOG", "SKU-COLA-2L", 250)["ok"]


def test_s1f_concurrent_budget_spend_rejects_po_once():
    scenarios.load_scenario("S1-f")
    with db.connect() as conn:
        res = writes.create_purchase_order(conn, "r", "S-ANDINA", "N-BOG", "SKU-COLA-2L", 800)
        assert res["ok"] is False and "budget" in res["reason"]
        assert reads.budget(conn, "beverages")["headroom"] == 500
        # fault is one-shot; a right-sized retry succeeds
        retry = writes.create_purchase_order(conn, "r", "S-ANDINA", "N-BOG", "SKU-COLA-2L", 400)
        assert retry["ok"]


def test_s2c_alternate_supplier_rejects():
    scenarios.load_scenario("S2-c")
    with db.connect() as conn:
        res = writes.create_purchase_order(conn, "r", "S-NORTE", "N-MDE", "SKU-CHIPS-150", 250)
        assert res["ok"] is False and res["status"] == "rejected"


def test_amend_and_cancel_release_budget():
    scenarios.load_scenario("S2-a")
    with db.connect() as conn:
        b0 = reads.budget(conn, "dairy")["headroom"]
        res = writes.amend_purchase_order(conn, "r", "PO-1001", 250)
        assert res["ok"] and res["confirmed_qty"] == 250 and res["status"] == "amended"
        assert reads.budget(conn, "dairy")["headroom"] == round(b0 + 250 * 0.95, 2)
        assert not writes.amend_purchase_order(conn, "r", "PO-1001", 300)["ok"]  # cannot increase
        res = writes.cancel_purchase_order(conn, "r", "PO-1001", "test")
        assert res["ok"] and reads.purchase_order(conn, "PO-1001")["status"] == "cancelled"
        assert reads.inbound_qty(conn, "SKU-MILK-1L", "N-MDE") == 0


def test_partial_confirmation_when_capacity_short():
    scenarios.load_scenario("S2-a")
    with db.connect() as conn:  # S-PACIFICO only has 250 capacity for milk in this fixture
        res = writes.create_purchase_order(conn, "r", "S-PACIFICO", "N-MDE", "SKU-MILK-1L", 400)
        assert res["ok"] and res["status"] == "partially_confirmed" and res["confirmed_qty"] == 250
