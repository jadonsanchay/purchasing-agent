"""Base dataset for the mock ERP. Scenarios (app/scenarios.py) layer variations on top of this.

Numbers are chosen so the policy engine produces clear, explainable outcomes:
  coverage target = lead_time + review period (7d) + safety stock days.
"""
from __future__ import annotations

import sqlite3

from . import db

REVIEW_PERIOD_DAYS = 7

PRODUCTS = [
    # sku, name, category, unit_cost, unit_volume_m3, safety_stock_days
    ("SKU-COLA-2L", "Cola 2L", "beverages", 1.20, 0.0025, 3),
    ("SKU-CHIPS-150", "Potato Chips 150g", "snacks", 0.80, 0.0020, 3),
    ("SKU-MILK-1L", "Whole Milk 1L", "dairy", 0.95, 0.0012, 2),
    ("SKU-RICE-1KG", "White Rice 1kg", "grocery", 1.10, 0.0015, 3),
    ("SKU-DIAPER-M", "Diapers size M (40)", "baby", 9.50, 0.0060, 4),
]

NODES = [
    ("N-BOG", "Bogotá Chapinero DS", "Bogotá"),
    ("N-MDE", "Medellín Poblado DS", "Medellín"),
]

SUPPLIERS = [
    # supplier_id, name, lead_time_days, reliability, payment_terms
    ("S-ANDINA", "Distribuidora Andina", 5, 0.92, "NET30"),
    ("S-PACIFICO", "Comercial Pacífico", 3, 0.78, "NET15"),
    ("S-NORTE", "Alimentos del Norte", 7, 0.97, "NET45"),
]

SUPPLIER_PRODUCTS = [
    # supplier_id, sku, unit_price, moq, pack_size, available_capacity
    ("S-ANDINA", "SKU-COLA-2L", 1.20, 200, 50, 5000),
    ("S-PACIFICO", "SKU-COLA-2L", 1.28, 100, 20, 1500),
    ("S-ANDINA", "SKU-CHIPS-150", 0.80, 200, 50, 3000),
    ("S-NORTE", "SKU-CHIPS-150", 0.84, 150, 50, 1000),
    ("S-PACIFICO", "SKU-MILK-1L", 0.95, 100, 50, 2500),
    ("S-ANDINA", "SKU-MILK-1L", 1.02, 100, 50, 2000),
    ("S-NORTE", "SKU-RICE-1KG", 1.10, 250, 50, 4000),
    ("S-PACIFICO", "SKU-RICE-1KG", 1.15, 100, 50, 0),      # listed but no capacity right now
    ("S-ANDINA", "SKU-DIAPER-M", 9.50, 50, 10, 600),
]

# steady-state daily demand per (sku, node); forecast == actuals unless a scenario says otherwise
BASE_DAILY_DEMAND = {
    ("SKU-COLA-2L", "N-BOG"): 60,
    ("SKU-COLA-2L", "N-MDE"): 35,
    ("SKU-CHIPS-150", "N-BOG"): 45,
    ("SKU-CHIPS-150", "N-MDE"): 30,
    ("SKU-MILK-1L", "N-BOG"): 90,
    ("SKU-MILK-1L", "N-MDE"): 70,
    ("SKU-RICE-1KG", "N-BOG"): 20,
    ("SKU-RICE-1KG", "N-MDE"): 15,
    ("SKU-DIAPER-M", "N-BOG"): 8,
    ("SKU-DIAPER-M", "N-MDE"): 5,
}

INVENTORY = {
    ("SKU-COLA-2L", "N-BOG"): (150, 0),
    ("SKU-COLA-2L", "N-MDE"): (300, 10),
    ("SKU-CHIPS-150", "N-BOG"): (220, 0),
    ("SKU-CHIPS-150", "N-MDE"): (90, 0),
    ("SKU-MILK-1L", "N-BOG"): (400, 20),
    ("SKU-MILK-1L", "N-MDE"): (120, 0),
    ("SKU-RICE-1KG", "N-BOG"): (400, 0),
    ("SKU-RICE-1KG", "N-MDE"): (180, 0),
    ("SKU-DIAPER-M", "N-BOG"): (60, 0),
    ("SKU-DIAPER-M", "N-MDE"): (40, 0),
}

BUDGETS = [
    # category, period, allocated, committed
    ("beverages", "2026-09", 20000.0, 8000.0),
    ("snacks", "2026-09", 12000.0, 6000.0),
    ("dairy", "2026-09", 15000.0, 7000.0),
    ("grocery", "2026-09", 18000.0, 9000.0),
    ("baby", "2026-09", 9000.0, 4500.0),
]
CURRENT_PERIOD = "2026-09"

STORAGE = [
    # node_id, capacity_m3, used_m3, inbound_reserved_m3
    ("N-BOG", 500.0, 380.0, 0.0),
    ("N-MDE", 320.0, 250.0, 0.0),
]

FORECAST_HORIZON_DAYS = 28
ACTUALS_HISTORY_DAYS = 14


def seed_base(conn: sqlite3.Connection) -> None:
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", PRODUCTS)
    conn.executemany("INSERT INTO nodes VALUES (?,?,?)", NODES)
    conn.executemany("INSERT INTO suppliers VALUES (?,?,?,?,?)", SUPPLIERS)
    conn.executemany("INSERT INTO supplier_products VALUES (?,?,?,?,?,?)", SUPPLIER_PRODUCTS)
    conn.executemany(
        "INSERT INTO inventory (sku, node_id, on_hand, reserved) VALUES (?,?,?,?)",
        [(sku, node, oh, res) for (sku, node), (oh, res) in INVENTORY.items()],
    )
    conn.executemany("INSERT INTO budgets VALUES (?,?,?,?)", BUDGETS)
    conn.executemany("INSERT INTO storage VALUES (?,?,?,?)", STORAGE)
    for (sku, node), daily in BASE_DAILY_DEMAND.items():
        set_demand(conn, sku, node, forecast_daily=daily, actual_daily=daily)


def set_demand(
    conn: sqlite3.Connection,
    sku: str,
    node_id: str,
    *,
    forecast_daily: int,
    actual_daily: int,
    actual_recent_daily: int | None = None,
    recent_days: int = 7,
) -> None:
    """Write a flat forecast and flat actuals. `actual_recent_daily` overrides the last `recent_days`
    of actuals to simulate a demand shift."""
    conn.execute("DELETE FROM demand WHERE sku=? AND node_id=?", (sku, node_id))
    rows = []
    for d in range(-ACTUALS_HISTORY_DAYS, 0):
        actual = actual_daily
        if actual_recent_daily is not None and d >= -recent_days:
            actual = actual_recent_daily
        rows.append((sku, node_id, d, forecast_daily, actual))
    for d in range(0, FORECAST_HORIZON_DAYS):
        rows.append((sku, node_id, d, forecast_daily, None))
    conn.executemany(
        "INSERT INTO demand (sku, node_id, day_offset, forecast_units, actual_units) VALUES (?,?,?,?,?)", rows
    )


def add_po(
    conn: sqlite3.Connection,
    po_id: str,
    supplier_id: str,
    node_id: str,
    sku: str,
    qty: int,
    *,
    status: str = "confirmed",
    confirmed_qty: int | None = None,
    expected_delivery_day: int | None = None,
    unit_price: float | None = None,
) -> None:
    if unit_price is None:
        unit_price = conn.execute(
            "SELECT unit_price FROM supplier_products WHERE supplier_id=? AND sku=?", (supplier_id, sku)
        ).fetchone()[0]
    if expected_delivery_day is None:
        expected_delivery_day = conn.execute(
            "SELECT lead_time_days FROM suppliers WHERE supplier_id=?", (supplier_id,)
        ).fetchone()[0]
    conn.execute(
        "INSERT INTO purchase_orders (po_id, supplier_id, node_id, status, created_at, expected_delivery_day, created_by)"
        " VALUES (?,?,?,?,?,?,?)",
        (po_id, supplier_id, node_id, status, db.now_iso(), expected_delivery_day, "buyer"),
    )
    conn.execute(
        "INSERT INTO po_lines (po_id, sku, ordered_qty, confirmed_qty, unit_price) VALUES (?,?,?,?,?)",
        (po_id, sku, qty, confirmed_qty if confirmed_qty is not None else (qty if status == "confirmed" else None), unit_price),
    )
    # committed budget follows the PO
    category = conn.execute("SELECT category FROM products WHERE sku=?", (sku,)).fetchone()[0]
    conn.execute(
        "UPDATE budgets SET committed = committed + ? WHERE category=? AND period=?",
        (qty * unit_price, category, CURRENT_PERIOD),
    )


def add_recommendation(conn: sqlite3.Connection, rec_id: str, sku: str, node_id: str, supplier_id: str, qty: int, reason: str) -> None:
    conn.execute(
        "INSERT INTO recommendations VALUES (?,?,?,?,?,?,?)",
        (rec_id, sku, node_id, supplier_id, qty, reason, db.now_iso()),
    )


def add_supplier_notice(conn: sqlite3.Connection, notice_id: str, po_id: str, sku: str, can_supply: int, message: str) -> None:
    conn.execute(
        "INSERT INTO supplier_notices VALUES (?,?,?,?,?,?)",
        (notice_id, po_id, sku, can_supply, message, db.now_iso()),
    )


def set_fault(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO faults (fault_key, fault_value) VALUES (?,?)", (key, value))


def main() -> None:  # `make seed`: base data only, useful for poking at the API
    db.reset_db()
    with db.connect() as conn:
        seed_base(conn)
    print(f"seeded base dataset at {db.db_path()}")


if __name__ == "__main__":
    main()
