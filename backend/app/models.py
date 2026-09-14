"""Shared data shapes: policy reports, agent decisions, run state."""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class Action(str, Enum):
    accept = "accept"
    modify = "modify"
    reject = "reject"
    investigate = "investigate"
    escalate = "escalate"


class CoverageReport(BaseModel):
    sku: str
    node_id: str
    supplier_id: str
    on_hand: int
    reserved: int
    available: int
    inbound: int
    inbound_pos: list[dict[str, Any]]
    lead_time_days: int
    safety_stock_days: int
    review_period_days: int
    target_days: int
    forecast_target_units: int
    forecast_daily_avg: float
    actual_last_7d_daily_avg: float
    recent_vs_forecast_pct: float | None
    demand_signal_flag: bool = Field(description="True when recent actuals deviate >30% from forecast")
    days_of_cover_current: float
    projected_stockout_day: int | None
    net_requirement: int
    moq: int
    pack_size: int
    suggested_qty: int
    notes: list[str]


class ConstraintCheck(BaseModel):
    name: str
    status: Literal["ok", "warn", "fail"]
    detail: str
    value: float | None = None
    limit: float | None = None


class ConstraintReport(BaseModel):
    sku: str
    node_id: str
    supplier_id: str
    proposed_qty: int
    unit_price: float
    value: float
    checks: list[ConstraintCheck]
    hard_fail: bool
    fail_reasons: list[str]
    warnings: list[str]
    requires_approval: bool
    approval_reasons: list[str]
    max_feasible_qty: int
    deviation_from_recommendation_pct: float | None


class Decision(BaseModel):
    """The agent's structured proposal. This is what gets gated, executed and validated."""

    action: Action
    quantity: int | None = Field(default=None, description="Units to buy (accept/modify) or to keep on an amended PO")
    supplier_id: str | None = None
    po_id: str | None = Field(default=None, description="Existing PO this decision amends/cancels, if any")
    additional_orders: list[dict[str, Any]] = Field(
        default_factory=list, description="Extra POs to create, e.g. [{supplier_id, quantity}] when splitting a shortfall"
    )
    rationale: str
    key_factors: list[str]
    confidence: float = Field(ge=0, le=1)
    expected_outcome: dict[str, Any] = Field(
        default_factory=dict,
        description="What the agent expects the ERP state to look like after execution, e.g. {po_status: confirmed, total_inbound: 800}",
    )


class TraceEvent(BaseModel):
    at: str
    actor: str
    event: str
    payload: dict[str, Any]


class ExecutedAction(BaseModel):
    kind: str  # create_po | amend_po | cancel_po | escalate | none
    request: dict[str, Any]
    result: dict[str, Any]


class ValidationResult(BaseModel):
    ok: bool
    expected: dict[str, Any]
    actual: dict[str, Any]
    mismatches: list[str]
    constraint_report: ConstraintReport | None = None


class RunState(BaseModel):
    run_id: str
    scenario_id: str
    status: Literal["running", "awaiting_approval", "completed", "escalated", "failed"]
    situation: str
    trace: list[TraceEvent] = Field(default_factory=list)
    decision: Decision | None = None
    gate: dict[str, Any] | None = None
    actions: list[ExecutedAction] = Field(default_factory=list)
    validations: list[ValidationResult] = Field(default_factory=list)
    recovery_turns_used: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    final_summary: str | None = None
    error: str | None = None
    llm_usage: dict[str, int] = Field(default_factory=dict)
