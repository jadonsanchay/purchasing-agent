"""Deterministic purchasing policy: coverage math and constraint checks.

Pure functions over the ERP state. Used three times per run:
  1. as a tool the agent can call while investigating,
  2. as the hard gate before any ERP write,
  3. by the validator, on the *actual* post-write state.
"""
from __future__ import annotations

import math
import sqlite3

from .. import db
from ..config import settings
from ..erp import reads
from ..models import ConstraintCheck, ConstraintReport, CoverageReport
from ..seed import REVIEW_PERIOD_DAYS

DEMAND_SIGNAL_THRESHOLD_PCT = 30.0
OVER_COVER_FACTOR = 1.5


def round_to_pack(qty: int, pack_size: int, moq: int) -> int:
    """Smallest orderable quantity >= qty that satisfies pack size and MOQ; 0 stays 0."""
    if qty <= 0:
        return 0
    q = max(qty, moq)
    return int(math.ceil(q / pack_size) * pack_size)


def round_down_to_pack(qty: int, pack_size: int, moq: int) -> int:
    """Largest orderable quantity <= qty; 0 if that would violate MOQ."""
    q = (qty // pack_size) * pack_size
    return q if q >= moq else 0


def _offer(conn: sqlite3.Connection, supplier_id: str, sku: str) -> dict:
    offer = db.one(conn.execute("SELECT * FROM supplier_products WHERE supplier_id=? AND sku=?", (supplier_id, sku)))
    if not offer:
        raise ValueError(f"{supplier_id} does not supply {sku}")
    return offer


def compute_coverage(conn: sqlite3.Connection, sku: str, node_id: str, supplier_id: str) -> CoverageReport:
    prod = reads.product(conn, sku)
    inv = reads.inventory(conn, sku, node_id)
    sup = reads.supplier(conn, supplier_id)
    offer = _offer(conn, supplier_id, sku)
    if not prod or not inv or not sup:
        raise ValueError(f"unknown sku/node/supplier: {sku}/{node_id}/{supplier_id}")

    dem = reads.demand(conn, sku, node_id)
    inbound_pos = reads.expected_inbound(conn, sku, node_id)
    inbound = sum(p["expected_qty"] for p in inbound_pos)

    target_days = sup["lead_time_days"] + REVIEW_PERIOD_DAYS + prod["safety_stock_days"]
    forecast_target = reads.forecast_sum(conn, sku, node_id, target_days)
    available = inv["available"]
    net = forecast_target - available - inbound

    daily = dem["forecast_daily_avg"] or 0.0
    days_cover = round((available + inbound) / daily, 1) if daily else float("inf")
    stockout_day = None
    if daily:
        # walk the forecast to find the first day cumulative demand exceeds stock (ignoring inbound arrival timing
        # for POs that land after that day)
        cum = 0
        for d, units in enumerate(dem["forecast_next_days"]):
            cum += units
            stock = available + sum(p["expected_qty"] for p in inbound_pos if (p["expected_delivery_day"] or 0) <= d)
            if cum > stock:
                stockout_day = d
                break

    signal = dem["recent_vs_forecast_pct"] is not None and abs(dem["recent_vs_forecast_pct"]) > DEMAND_SIGNAL_THRESHOLD_PCT
    notes = []
    if signal:
        notes.append(
            f"Recent actuals ({dem['actual_last_7d_daily_avg']}/day) deviate {dem['recent_vs_forecast_pct']}% from forecast "
            f"({dem['forecast_daily_avg']}/day). Net requirement below uses the forecast; treat with caution."
        )
    if inbound_pos:
        notes.append(f"{len(inbound_pos)} open PO(s) contribute {inbound} expected inbound units.")
    if stockout_day is not None and stockout_day < sup["lead_time_days"]:
        notes.append(f"Projected stockout on day {stockout_day} is before supplier lead time ({sup['lead_time_days']}d); a new order from this supplier will not prevent it.")

    return CoverageReport(
        sku=sku, node_id=node_id, supplier_id=supplier_id,
        on_hand=inv["on_hand"], reserved=inv["reserved"], available=available,
        inbound=inbound, inbound_pos=inbound_pos,
        lead_time_days=sup["lead_time_days"], safety_stock_days=prod["safety_stock_days"],
        review_period_days=REVIEW_PERIOD_DAYS, target_days=target_days,
        forecast_target_units=forecast_target, forecast_daily_avg=dem["forecast_daily_avg"],
        actual_last_7d_daily_avg=dem["actual_last_7d_daily_avg"], recent_vs_forecast_pct=dem["recent_vs_forecast_pct"],
        demand_signal_flag=signal, days_of_cover_current=days_cover, projected_stockout_day=stockout_day,
        net_requirement=net, moq=offer["moq"], pack_size=offer["pack_size"],
        suggested_qty=round_to_pack(net, offer["pack_size"], offer["moq"]), notes=notes,
    )


def max_feasible_qty(conn: sqlite3.Connection, sku: str, node_id: str, supplier_id: str) -> int:
    """Largest quantity that passes every hard constraint for this supplier right now."""
    prod = reads.product(conn, sku)
    offer = _offer(conn, supplier_id, sku)
    b = reads.budget(conn, prod["category"])
    st = reads.storage(conn, node_id)
    by_budget = int(math.floor(b["headroom"] / offer["unit_price"] + 1e-9)) if b else 0
    by_storage = int(math.floor(st["free_m3"] / prod["unit_volume_m3"] + 1e-9)) if st else 0
    by_capacity = offer["available_capacity"]
    return round_down_to_pack(min(by_budget, by_storage, by_capacity), offer["pack_size"], offer["moq"])


def check_constraints(
    conn: sqlite3.Connection,
    sku: str,
    node_id: str,
    supplier_id: str,
    qty: int,
    recommended_qty: int | None = None,
) -> ConstraintReport:
    prod = reads.product(conn, sku)
    offer = _offer(conn, supplier_id, sku)
    b = reads.budget(conn, prod["category"])
    st = reads.storage(conn, node_id)
    value = round(qty * offer["unit_price"], 2)
    checks: list[ConstraintCheck] = []

    def add(name: str, ok: bool, detail: str, value_=None, limit=None, warn_only: bool = False) -> None:
        checks.append(ConstraintCheck(name=name, status="ok" if ok else ("warn" if warn_only else "fail"), detail=detail, value=value_, limit=limit))

    add("moq", qty >= offer["moq"], f"MOQ {offer['moq']}", qty, offer["moq"])
    add("pack_size", qty % offer["pack_size"] == 0, f"pack size {offer['pack_size']}", qty, offer["pack_size"])
    add("budget", b is not None and value <= b["headroom"], f"PO value {value} vs headroom {b['headroom'] if b else 0}", value, b["headroom"] if b else 0)
    needed_m3 = round(qty * prod["unit_volume_m3"], 4)
    add("storage", st is not None and needed_m3 <= st["free_m3"], f"needs {needed_m3} m3 vs free {st['free_m3'] if st else 0} m3", needed_m3, st["free_m3"] if st else 0)
    add("supplier_capacity", qty <= offer["available_capacity"], f"supplier can commit {offer['available_capacity']} units; excess will be partially confirmed", qty, offer["available_capacity"], warn_only=True)

    cov = compute_coverage(conn, sku, node_id, supplier_id)
    daily = cov.forecast_daily_avg or 0.0
    if daily:
        cover_after = (cov.available + cov.inbound + qty) / daily
        add("over_cover", cover_after <= cov.target_days * OVER_COVER_FACTOR,
            f"{round(cover_after,1)} days of cover after purchase vs target {cov.target_days}d", round(cover_after, 1), cov.target_days * OVER_COVER_FACTOR, warn_only=True)
    if cov.projected_stockout_day is not None:
        add("lead_time_vs_stockout", cov.projected_stockout_day >= cov.lead_time_days,
            f"stockout day {cov.projected_stockout_day} vs lead time {cov.lead_time_days}d", cov.projected_stockout_day, cov.lead_time_days, warn_only=True)
    if cov.demand_signal_flag:
        add("demand_signal", False, f"recent actuals deviate {cov.recent_vs_forecast_pct}% from forecast", cov.recent_vs_forecast_pct, DEMAND_SIGNAL_THRESHOLD_PCT, warn_only=True)

    fails = [c.detail for c in checks if c.status == "fail"]
    warns = [c.detail for c in checks if c.status == "warn"]

    deviation = None
    approval_reasons = []
    if recommended_qty:
        deviation = round((qty - recommended_qty) / recommended_qty * 100, 1)
        if abs(deviation) > settings.max_deviation_pct:
            approval_reasons.append(f"deviates {deviation}% from system recommendation (limit {settings.max_deviation_pct}%)")
    if value > settings.approval_threshold:
        approval_reasons.append(f"PO value {value} exceeds approval threshold {settings.approval_threshold}")

    return ConstraintReport(
        sku=sku, node_id=node_id, supplier_id=supplier_id, proposed_qty=qty, unit_price=offer["unit_price"], value=value,
        checks=checks, hard_fail=bool(fails), fail_reasons=fails, warnings=warns,
        requires_approval=bool(approval_reasons), approval_reasons=approval_reasons,
        max_feasible_qty=max_feasible_qty(conn, sku, node_id, supplier_id),
        deviation_from_recommendation_pct=deviation,
    )
