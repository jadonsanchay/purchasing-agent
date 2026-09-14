"""Write-side of the mock ERP.

The ERP enforces its own rules independently of the agent's policy engine, exactly like a real system
would. Fault toggles (table `faults`) let a scenario make the ERP disagree with what the agent expected,
which is what the validator / feedback loop has to cope with.
"""
from __future__ import annotations

import math
import sqlite3
import uuid

from .. import db
from ..seed import CURRENT_PERIOD
from . import reads


def _result(ok: bool, **kw) -> dict:
    return {"ok": ok, **kw}


def create_purchase_order(conn: sqlite3.Connection, run_id: str | None, supplier_id: str, node_id: str, sku: str, qty: int) -> dict:
    prod = reads.product(conn, sku)
    offer = db.one(conn.execute("SELECT * FROM supplier_products WHERE supplier_id=? AND sku=?", (supplier_id, sku)))
    if not prod or not offer:
        return _result(False, reason=f"{supplier_id} does not supply {sku}")
    if qty <= 0:
        return _result(False, reason="quantity must be positive")
    if qty < offer["moq"]:
        return _result(False, reason=f"quantity {qty} below MOQ {offer['moq']}")
    if qty % offer["pack_size"] != 0:
        return _result(False, reason=f"quantity {qty} not a multiple of pack size {offer['pack_size']}")

    # fault: supplier rejects any new PO
    reject = reads.fault(conn, f"supplier:{supplier_id}:reject_new_po")
    if reject:
        db.audit(conn, run_id, "erp", "po_rejected", {"supplier_id": supplier_id, "sku": sku, "qty": qty, "reason": reject})
        return _result(False, reason=f"supplier rejected order: {reject}", status="rejected")

    # fault: someone else spends budget between the agent's read and this write
    spend_key = f"budget:{prod['category']}:concurrent_spend"
    concurrent = reads.fault(conn, spend_key)
    if concurrent:
        conn.execute("UPDATE budgets SET committed = committed + ? WHERE category=? AND period=?", (float(concurrent), prod["category"], CURRENT_PERIOD))
        reads.clear_fault(conn, spend_key)  # one-shot
        db.audit(conn, run_id, "erp", "budget_committed_elsewhere", {"category": prod["category"], "amount": float(concurrent)})

    value = round(qty * offer["unit_price"], 2)
    b = reads.budget(conn, prod["category"])
    if b is None or b["headroom"] < value:
        return _result(False, reason=f"budget exceeded: PO value {value} > headroom {b['headroom'] if b else 0}", status="rejected")

    st = reads.storage(conn, node_id)
    needed_m3 = round(qty * prod["unit_volume_m3"], 4)
    if st is None or st["free_m3"] < needed_m3:
        return _result(False, reason=f"storage exceeded: needs {needed_m3} m3, free {st['free_m3'] if st else 0} m3", status="rejected")

    if offer["available_capacity"] < qty:
        confirmed = (offer["available_capacity"] // offer["pack_size"]) * offer["pack_size"]
    else:
        confirmed = qty
    ratio = reads.fault(conn, f"supplier:{supplier_id}:confirm_ratio")
    if ratio:
        confirmed = min(confirmed, math.floor(qty * float(ratio) / offer["pack_size"]) * offer["pack_size"])
    status = "confirmed" if confirmed == qty else "partially_confirmed"

    supplier = reads.supplier(conn, supplier_id)
    po_id = f"PO-{uuid.uuid4().hex[:6].upper()}"
    conn.execute(
        "INSERT INTO purchase_orders (po_id, supplier_id, node_id, status, created_at, expected_delivery_day, created_by, run_id) VALUES (?,?,?,?,?,?,?,?)",
        (po_id, supplier_id, node_id, status, db.now_iso(), supplier["lead_time_days"], "agent", run_id),
    )
    conn.execute(
        "INSERT INTO po_lines (po_id, sku, ordered_qty, confirmed_qty, unit_price) VALUES (?,?,?,?,?)",
        (po_id, sku, qty, confirmed, offer["unit_price"]),
    )
    conn.execute("UPDATE budgets SET committed = committed + ? WHERE category=? AND period=?", (value, prod["category"], CURRENT_PERIOD))
    conn.execute("UPDATE storage SET inbound_reserved_m3 = inbound_reserved_m3 + ? WHERE node_id=?", (needed_m3, node_id))
    conn.execute("UPDATE supplier_products SET available_capacity = available_capacity - ? WHERE supplier_id=? AND sku=?", (confirmed, supplier_id, sku))
    db.audit(conn, run_id, "erp", "po_created", {"po_id": po_id, "supplier_id": supplier_id, "sku": sku, "ordered_qty": qty, "confirmed_qty": confirmed, "status": status})
    return _result(True, po_id=po_id, status=status, ordered_qty=qty, confirmed_qty=confirmed, value=value, expected_delivery_day=supplier["lead_time_days"])


def amend_purchase_order(conn: sqlite3.Connection, run_id: str | None, po_id: str, new_qty: int) -> dict:
    po = reads.purchase_order(conn, po_id)
    if not po:
        return _result(False, reason=f"{po_id} not found")
    if po["status"] not in ("submitted", "confirmed", "partially_confirmed", "amended"):
        return _result(False, reason=f"{po_id} is {po['status']} and cannot be amended")
    line = po["lines"][0]
    if new_qty <= 0:
        return _result(False, reason="use cancel_purchase_order to cancel")
    if new_qty > line["ordered_qty"]:
        return _result(False, reason="amendments can only reduce quantity; create a new PO to buy more")
    offer = db.one(conn.execute("SELECT * FROM supplier_products WHERE supplier_id=? AND sku=?", (po["supplier_id"], line["sku"])))
    if new_qty % offer["pack_size"] != 0:
        return _result(False, reason=f"quantity {new_qty} not a multiple of pack size {offer['pack_size']}")

    # Supplier confirms up to what it said it can supply (latest notice), else the full amended qty.
    notices = reads.notices_for_po(conn, po_id)
    can_supply = notices[-1]["can_supply_qty"] if notices else new_qty
    confirmed = min(new_qty, can_supply)
    status = "amended" if confirmed == new_qty else "partially_confirmed"

    delta_value = round((new_qty - line["ordered_qty"]) * line["unit_price"], 2)
    prod = reads.product(conn, line["sku"])
    conn.execute("UPDATE po_lines SET ordered_qty=?, confirmed_qty=? WHERE po_id=? AND sku=?", (new_qty, confirmed, po_id, line["sku"]))
    conn.execute("UPDATE purchase_orders SET status=?, run_id=COALESCE(run_id, ?) WHERE po_id=?", (status, run_id, po_id))
    conn.execute("UPDATE budgets SET committed = committed + ? WHERE category=? AND period=?", (delta_value, prod["category"], CURRENT_PERIOD))
    db.audit(conn, run_id, "erp", "po_amended", {"po_id": po_id, "from_qty": line["ordered_qty"], "to_qty": new_qty, "confirmed_qty": confirmed, "status": status})
    return _result(True, po_id=po_id, status=status, ordered_qty=new_qty, confirmed_qty=confirmed, value=round(new_qty * line["unit_price"], 2))


def cancel_purchase_order(conn: sqlite3.Connection, run_id: str | None, po_id: str, reason: str) -> dict:
    po = reads.purchase_order(conn, po_id)
    if not po:
        return _result(False, reason=f"{po_id} not found")
    if po["status"] in ("cancelled", "rejected"):
        return _result(False, reason=f"{po_id} already {po['status']}")
    line = po["lines"][0]
    prod = reads.product(conn, line["sku"])
    conn.execute("UPDATE purchase_orders SET status='cancelled', run_id=COALESCE(run_id, ?) WHERE po_id=?", (run_id, po_id))
    conn.execute("UPDATE budgets SET committed = committed - ? WHERE category=? AND period=?", (line["ordered_qty"] * line["unit_price"], prod["category"], CURRENT_PERIOD))
    if po["created_by"] == "agent":
        conn.execute("UPDATE storage SET inbound_reserved_m3 = MAX(0, inbound_reserved_m3 - ?) WHERE node_id=?", (line["ordered_qty"] * prod["unit_volume_m3"], po["node_id"]))
    db.audit(conn, run_id, "erp", "po_cancelled", {"po_id": po_id, "reason": reason})
    return _result(True, po_id=po_id, status="cancelled")
