"""SQLite access. One connection per call; the dataset is tiny and this keeps things simple."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_cost REAL NOT NULL,
    unit_volume_m3 REAL NOT NULL,
    safety_stock_days INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    sku TEXT NOT NULL,
    node_id TEXT NOT NULL,
    on_hand INTEGER NOT NULL,
    reserved INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sku, node_id)
);

CREATE TABLE IF NOT EXISTS demand (
    sku TEXT NOT NULL,
    node_id TEXT NOT NULL,
    day_offset INTEGER NOT NULL,          -- negative = past actuals, >=0 = forecast
    forecast_units INTEGER,
    actual_units INTEGER,
    PRIMARY KEY (sku, node_id, day_offset)
);

CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lead_time_days INTEGER NOT NULL,
    reliability_score REAL NOT NULL,       -- 0..1 historical on-time-in-full
    payment_terms TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_products (
    supplier_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    unit_price REAL NOT NULL,
    moq INTEGER NOT NULL,
    pack_size INTEGER NOT NULL DEFAULT 1,
    available_capacity INTEGER NOT NULL,   -- units the supplier can currently commit
    PRIMARY KEY (supplier_id, sku)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id TEXT PRIMARY KEY,
    supplier_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    status TEXT NOT NULL,                  -- draft|submitted|confirmed|partially_confirmed|rejected|cancelled|amended
    created_at TEXT NOT NULL,
    expected_delivery_day INTEGER,         -- day offset from today
    created_by TEXT NOT NULL DEFAULT 'system',
    run_id TEXT
);

CREATE TABLE IF NOT EXISTS po_lines (
    po_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    ordered_qty INTEGER NOT NULL,
    confirmed_qty INTEGER,
    unit_price REAL NOT NULL,
    PRIMARY KEY (po_id, sku)
);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT NOT NULL,
    period TEXT NOT NULL,
    allocated REAL NOT NULL,
    committed REAL NOT NULL,
    PRIMARY KEY (category, period)
);

CREATE TABLE IF NOT EXISTS storage (
    node_id TEXT PRIMARY KEY,
    capacity_m3 REAL NOT NULL,
    used_m3 REAL NOT NULL,
    inbound_reserved_m3 REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recommendations (
    rec_id TEXT PRIMARY KEY,
    sku TEXT NOT NULL,
    node_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    recommended_qty INTEGER NOT NULL,
    reason TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supplier_notices (
    notice_id TEXT PRIMARY KEY,
    po_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    can_supply_qty INTEGER NOT NULL,
    message TEXT NOT NULL,
    received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faults (
    fault_key TEXT PRIMARY KEY,           -- e.g. supplier:S2:confirm_ratio
    fault_value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,                   -- agent|policy|executor|validator|erp|human
    event TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    return settings.resolved_db_path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def reset_db() -> None:
    path = db_path()
    if path.exists():
        path.unlink()
    init_schema()


def rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    return [dict(r) for r in cur.fetchall()]


def one(cur: sqlite3.Cursor) -> dict[str, Any] | None:
    r = cur.fetchone()
    return dict(r) if r else None


def audit(conn: sqlite3.Connection, run_id: str | None, actor: str, event: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_log (run_id, at, actor, event, payload_json) VALUES (?, ?, ?, ?, ?)",
        (run_id, now_iso(), actor, event, json.dumps(payload, default=str)),
    )
