"""Read-side of the mock ERP. Everything the agent may look at, shaped for tool results."""
from __future__ import annotations

import sqlite3
from statistics import mean

from .. import db
from ..seed import CURRENT_PERIOD

OPEN_STATUSES = ("submitted", "confirmed", "partially_confirmed", "amended")


def product(conn: sqlite3.Connection, sku: str) -> dict | None:
    return db.one(conn.execute("SELECT * FROM products WHERE sku=?", (sku,)))


def inventory(conn: sqlite3.Connection, sku: str, node_id: str) -> dict | None:
    row = db.one(conn.execute("SELECT * FROM inventory WHERE sku=? AND node_id=?", (sku, node_id)))
    if row:
        row["available"] = row["on_hand"] - row["reserved"]
    return row


def demand(conn: sqlite3.Connection, sku: str, node_id: str, horizon_days: int = 28) -> dict:
    series = db.rows(
        conn.execute(
            "SELECT day_offset, forecast_units, actual_units FROM demand WHERE sku=? AND node_id=? ORDER BY day_offset",
            (sku, node_id),
        )
    )
    forecast = [r["forecast_units"] for r in series if 0 <= r["day_offset"] < horizon_days]
    actual_recent = [r["actual_units"] for r in series if -7 <= r["day_offset"] < 0 and r["actual_units"] is not None]
    actual_prior = [r["actual_units"] for r in series if -14 <= r["day_offset"] < -7 and r["actual_units"] is not None]
    fc_avg = mean(forecast) if forecast else 0.0
    recent_avg = mean(actual_recent) if actual_recent else 0.0
    prior_avg = mean(actual_prior) if actual_prior else 0.0
    return {
        "sku": sku,
        "node_id": node_id,
        "forecast_daily_avg": round(fc_avg, 1),
        "actual_last_7d_daily_avg": round(recent_avg, 1),
        "actual_prior_7d_daily_avg": round(prior_avg, 1),
        "recent_vs_forecast_pct": round((recent_avg - fc_avg) / fc_avg * 100, 1) if fc_avg else None,
        "forecast_next_days": forecast,
        "actuals_last_14d": [r["actual_units"] for r in series if r["day_offset"] < 0],
    }


def forecast_sum(conn: sqlite3.Connection, sku: str, node_id: str, days: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(forecast_units),0) FROM demand WHERE sku=? AND node_id=? AND day_offset>=0 AND day_offset<?",
        (sku, node_id, days),
    ).fetchone()
    return int(row[0])


def open_purchase_orders(conn: sqlite3.Connection, sku: str, node_id: str) -> list[dict]:
    q = f"""
        SELECT po.po_id, po.supplier_id, po.node_id, po.status, po.expected_delivery_day, po.created_by, po.run_id,
               l.sku, l.ordered_qty, l.confirmed_qty, l.unit_price
        FROM purchase_orders po JOIN po_lines l ON l.po_id = po.po_id
        WHERE l.sku=? AND po.node_id=? AND po.status IN ({",".join("?" * len(OPEN_STATUSES))})
        ORDER BY po.expected_delivery_day
    """
    return db.rows(conn.execute(q, (sku, node_id, *OPEN_STATUSES)))


def expected_inbound(conn: sqlite3.Connection, sku: str, node_id: str) -> list[dict]:
    """Open POs with the quantity we can realistically expect: confirmed qty where known, capped by the
    latest supplier notice, otherwise the ordered qty."""
    out = []
    for po in open_purchase_orders(conn, sku, node_id):
        expected = po["confirmed_qty"] if po["confirmed_qty"] is not None else po["ordered_qty"]
        notices = notices_for_po(conn, po["po_id"])
        if notices:
            expected = min(expected, notices[-1]["can_supply_qty"])
            po["latest_notice"] = notices[-1]["message"]
        po["expected_qty"] = expected
        out.append(po)
    return out


def inbound_qty(conn: sqlite3.Connection, sku: str, node_id: str) -> int:
    return sum(po["expected_qty"] for po in expected_inbound(conn, sku, node_id))


def purchase_order(conn: sqlite3.Connection, po_id: str) -> dict | None:
    po = db.one(conn.execute("SELECT * FROM purchase_orders WHERE po_id=?", (po_id,)))
    if not po:
        return None
    po["lines"] = db.rows(conn.execute("SELECT sku, ordered_qty, confirmed_qty, unit_price FROM po_lines WHERE po_id=?", (po_id,)))
    po["total_value"] = round(sum(l["ordered_qty"] * l["unit_price"] for l in po["lines"]), 2)
    return po


def supplier(conn: sqlite3.Connection, supplier_id: str, sku: str | None = None) -> dict | None:
    s = db.one(conn.execute("SELECT * FROM suppliers WHERE supplier_id=?", (supplier_id,)))
    if not s:
        return None
    if sku:
        offer = db.one(conn.execute("SELECT * FROM supplier_products WHERE supplier_id=? AND sku=?", (supplier_id, sku)))
        s["offer"] = offer or {"error": f"{supplier_id} does not list {sku}"}
    return s


def suppliers_for_sku(conn: sqlite3.Connection, sku: str, exclude: str | None = None) -> list[dict]:
    q = """
        SELECT s.supplier_id, s.name, s.lead_time_days, s.reliability_score, s.payment_terms,
               sp.unit_price, sp.moq, sp.pack_size, sp.available_capacity
        FROM supplier_products sp JOIN suppliers s ON s.supplier_id = sp.supplier_id
        WHERE sp.sku=? ORDER BY sp.unit_price
    """
    out = db.rows(conn.execute(q, (sku,)))
    return [r for r in out if r["supplier_id"] != exclude]


def budget(conn: sqlite3.Connection, category: str) -> dict | None:
    b = db.one(conn.execute("SELECT * FROM budgets WHERE category=? AND period=?", (category, CURRENT_PERIOD)))
    if b:
        b["headroom"] = round(b["allocated"] - b["committed"], 2)
    return b


def storage(conn: sqlite3.Connection, node_id: str) -> dict | None:
    s = db.one(conn.execute("SELECT * FROM storage WHERE node_id=?", (node_id,)))
    if s:
        s["free_m3"] = round(s["capacity_m3"] - s["used_m3"] - s["inbound_reserved_m3"], 4)
    return s


def recommendation(conn: sqlite3.Connection, rec_id: str) -> dict | None:
    return db.one(conn.execute("SELECT * FROM recommendations WHERE rec_id=?", (rec_id,)))


def supplier_notice(conn: sqlite3.Connection, notice_id: str) -> dict | None:
    return db.one(conn.execute("SELECT * FROM supplier_notices WHERE notice_id=?", (notice_id,)))


def notices_for_po(conn: sqlite3.Connection, po_id: str) -> list[dict]:
    return db.rows(conn.execute("SELECT * FROM supplier_notices WHERE po_id=?", (po_id,)))


def fault(conn: sqlite3.Connection, key: str) -> str | None:
    r = conn.execute("SELECT fault_value FROM faults WHERE fault_key=?", (key,)).fetchone()
    return r[0] if r else None


def clear_fault(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM faults WHERE fault_key=?", (key,))
