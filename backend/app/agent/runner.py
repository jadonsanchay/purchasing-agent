"""Run orchestration: investigate -> propose -> gate -> (approve) -> execute -> validate -> recover/escalate.

The LLM only ever sees read tools and `propose_decision`. Everything after the proposal is deterministic code.
State is persisted to the `runs` table after every step so a run can pause for human approval and resume.
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from .. import db
from ..config import settings
from ..erp import reads, writes
from ..models import Decision, ExecutedAction, RunState, TraceEvent
from ..scenarios import SCENARIOS, Scenario
from . import policy, prompts, tools, validator
from .llm import LLM

_RUNNERS: dict[str, "Runner"] = {}
_LOCK = threading.Lock()


class Runner:
    def __init__(self, scenario: Scenario, llm: LLM, run_id: str | None = None):
        self.scenario = scenario
        self.llm = llm
        self.run_id = run_id or f"run-{uuid.uuid4().hex[:8]}"
        self.state = RunState(run_id=self.run_id, scenario_id=scenario.id, status="running", situation=scenario.situation)
        self.conversation: list[dict] = []
        self.plan: list[dict] = []
        self.lock = threading.Lock()
        with _LOCK:
            _RUNNERS[self.run_id] = self

    # ------------------------------------------------------------------ persistence

    def save(self) -> None:
        payload = {"state": self.state.model_dump(mode="json"), "conversation": self.conversation, "plan": self.plan}
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, scenario_id, status, created_at, updated_at, state_json) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(run_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at, state_json=excluded.state_json",
                (self.run_id, self.scenario.id, self.state.status, db.now_iso(), db.now_iso(), json.dumps(payload, default=str)),
            )

    @classmethod
    def load(cls, run_id: str, llm: LLM) -> "Runner | None":
        with _LOCK:
            if run_id in _RUNNERS:
                return _RUNNERS[run_id]
        with db.connect() as conn:
            row = db.one(conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)))
        if not row:
            return None
        payload = json.loads(row["state_json"])
        r = cls(SCENARIOS[row["scenario_id"]], llm, run_id=run_id)
        r.state = RunState(**payload["state"])
        r.conversation = payload["conversation"]
        r.plan = payload["plan"]
        return r

    def trace(self, actor: str, event: str, payload: dict[str, Any]) -> None:
        self.state.trace.append(TraceEvent(at=db.now_iso(), actor=actor, event=event, payload=payload))
        with db.connect() as conn:
            db.audit(conn, self.run_id, actor, event, payload)

    # ------------------------------------------------------------------ context helpers

    @property
    def ctx(self) -> dict:
        return self.scenario.context

    def _sku_node(self) -> tuple[str, str]:
        return self.ctx["sku"], self.ctx["node_id"]

    def _default_supplier(self) -> str:
        if "supplier_id" in self.ctx:
            return self.ctx["supplier_id"]
        with db.connect() as conn:
            po = reads.purchase_order(conn, self.ctx["po_id"])
        return po["supplier_id"]

    def _recommended_qty(self) -> int | None:
        return self.ctx.get("qty")

    # ------------------------------------------------------------------ main flow

    def start(self) -> RunState:
        self.trace("system", "run_started", {"scenario_id": self.scenario.id, "situation": self.scenario.situation})
        self.conversation = [{"role": "user", "content": prompts.situation_message(self.scenario.situation, self.ctx)}]
        self.save()
        try:
            decision = self._investigate()
            if decision is None:
                return self._escalate("Agent did not reach a decision within the turn limit.")
            return self._process_decision(decision)
        except Exception as e:  # keep the run inspectable rather than crashing the request
            self.state.status = "failed"
            self.state.error = repr(e)
            self.trace("system", "run_failed", {"error": repr(e)})
            self.save()
            return self.state

    def _investigate(self) -> Decision | None:
        for _ in range(settings.max_agent_turns):
            turn = self.llm.respond(prompts.SYSTEM, self.conversation, tools.ALL_TOOLS)
            for k, v in turn.usage.items():
                self.state.llm_usage[k] = self.state.llm_usage.get(k, 0) + v
            self.conversation.extend(turn.raw_output_items)
            if turn.text:
                self.trace("agent", "message", {"text": turn.text})
            if not turn.tool_calls:
                self.conversation.append({"role": "user", "content": "Finish by calling propose_decision."})
                continue

            decision: Decision | None = None
            with db.connect() as conn:
                for call in turn.tool_calls:
                    if call.name == "propose_decision":
                        try:
                            decision = Decision(**call.arguments)
                            output: Any = {"received": True, "note": "Decision accepted for gating and execution."}
                        except Exception as e:
                            output = {"error": f"invalid decision: {e}"}
                    else:
                        output = tools.call_tool(conn, self.run_id, call.name, call.arguments)
                        self.state.tool_calls.append(call.name)
                    self.conversation.append({"type": "function_call_output", "call_id": call.call_id, "output": tools.dumps(output)})
                    self.state.trace.append(TraceEvent(at=db.now_iso(), actor="agent", event="tool_call", payload={"tool": call.name, "arguments": call.arguments, "result": output}))
            self.save()
            if decision:
                self.trace("agent", "decision_proposed", decision.model_dump(mode="json"))
                return decision
        return None

    def _build_plan(self, d: Decision) -> list[dict]:
        sku, node = self._sku_node()
        plan: list[dict] = []
        if d.action.value in ("investigate", "escalate"):
            return plan
        if d.po_id:
            if d.action.value == "reject" or d.quantity == 0:
                plan.append({"kind": "cancel_po", "po_id": d.po_id, "reason": d.rationale[:200]})
            elif d.quantity is not None:
                plan.append({"kind": "amend_po", "po_id": d.po_id, "quantity": d.quantity})
        elif d.action.value in ("accept", "modify"):
            qty = self._recommended_qty() if d.action.value == "accept" else d.quantity
            if qty:
                plan.append({"kind": "create_po", "supplier_id": d.supplier_id or self._default_supplier(), "node_id": node, "sku": sku, "quantity": qty})
        for extra in d.additional_orders:
            plan.append({"kind": "create_po", "supplier_id": extra["supplier_id"], "node_id": node, "sku": sku, "quantity": int(extra["quantity"])})
        return plan

    def _process_decision(self, d: Decision) -> RunState:
        self.state.decision = d
        self.plan = self._build_plan(d)
        self.trace("system", "plan_built", {"plan": self.plan})

        if d.action.value == "escalate":
            return self._escalate(d.rationale, by_agent=True)
        if d.action.value == "investigate":
            return self._complete(f"No purchase. Agent requests further investigation: {d.rationale}")

        # ---- policy gate on every write intent
        reports = []
        with db.connect() as conn:
            for intent in self.plan:
                if intent["kind"] == "create_po":
                    reports.append(policy.check_constraints(conn, intent["sku"], intent["node_id"], intent["supplier_id"], intent["quantity"], self._recommended_qty()).model_dump())
                elif intent["kind"] == "amend_po":
                    po = reads.purchase_order(conn, intent["po_id"])
                    if not po:
                        reports.append({"hard_fail": True, "fail_reasons": [f"{intent['po_id']} not found"], "requires_approval": False, "approval_reasons": [], "value": 0})
        fails = [r for rep in reports for r in rep["fail_reasons"]]
        approval_reasons = [r for rep in reports for r in rep["approval_reasons"]]
        self.state.gate = {"reports": reports, "hard_fail": bool(fails), "requires_approval": bool(approval_reasons), "approval_reasons": approval_reasons}
        self.trace("policy", "gate_evaluated", self.state.gate)

        if fails:
            return self._recover(prompts.gate_feedback(fails, reports), reason="policy gate blocked proposal")
        if approval_reasons:
            self.state.status = "awaiting_approval"
            self.trace("system", "awaiting_approval", {"reasons": approval_reasons})
            self.save()
            return self.state
        return self._execute_and_validate()

    def approve(self, approver: str = "buyer") -> RunState:
        with self.lock:
            if self.state.status != "awaiting_approval":
                return self.state
            self.state.status = "running"
            self.trace("human", "approved", {"by": approver})
            return self._execute_and_validate()

    def reject(self, approver: str = "buyer", note: str = "") -> RunState:
        with self.lock:
            if self.state.status != "awaiting_approval":
                return self.state
            self.trace("human", "rejected", {"by": approver, "note": note})
            return self._complete(f"Buyer rejected the agent's proposal. No ERP changes made. {note}".strip())

    def _execute_and_validate(self) -> RunState:
        sku, node = self._sku_node()
        actions: list[ExecutedAction] = []
        with db.connect() as conn:
            for intent in self.plan:
                if intent["kind"] == "create_po":
                    res = writes.create_purchase_order(conn, self.run_id, intent["supplier_id"], intent["node_id"], intent["sku"], intent["quantity"])
                elif intent["kind"] == "amend_po":
                    res = writes.amend_purchase_order(conn, self.run_id, intent["po_id"], intent["quantity"])
                elif intent["kind"] == "cancel_po":
                    res = writes.cancel_purchase_order(conn, self.run_id, intent["po_id"], intent["reason"])
                else:
                    res = {"ok": True}
                actions.append(ExecutedAction(kind=intent["kind"], request=intent, result=res))
        self.state.actions.extend(actions)
        self.trace("executor", "actions_executed", {"actions": [a.model_dump() for a in actions]})

        cov_supplier = next((i["supplier_id"] for i in self.plan if i["kind"] == "create_po"), None) or self._default_supplier()
        with db.connect() as conn:
            result = validator.validate(conn, self.state.decision, actions, sku, node, cov_supplier)
        self.state.validations.append(result)
        self.trace("validator", "validated", result.model_dump())

        if result.ok:
            return self._complete(self._summary(result))
        return self._recover(
            prompts.validation_feedback(result.mismatches, result.actual, self.state.recovery_turns_used + 1, settings.max_recovery_turns),
            reason="validation mismatch",
        )

    def _recover(self, feedback: str, reason: str) -> RunState:
        if self.state.recovery_turns_used >= settings.max_recovery_turns:
            return self._escalate(f"{reason}; recovery budget exhausted after {self.state.recovery_turns_used} turn(s). Last feedback: {feedback[:400]}")
        self.state.recovery_turns_used += 1
        self.trace("system", "recovery_turn", {"turn": self.state.recovery_turns_used, "reason": reason})
        self.conversation.append({"role": "user", "content": feedback})
        self.save()
        decision = self._investigate()
        if decision is None:
            return self._escalate("Agent did not reach a revised decision within the turn limit.")
        return self._process_decision(decision)

    # ------------------------------------------------------------------ terminal states

    def _summary(self, result) -> str:
        d = self.state.decision
        acts = "; ".join(f"{a.kind} -> {a.result.get('po_id', '')} {a.result.get('status', '')}".strip() for a in self.state.actions if a.result.get("ok")) or "no ERP changes"
        return f"{d.action.value.upper()}: {d.rationale} Actions: {acts}. Validation passed (inbound now {result.actual.get('total_inbound_after')}, net requirement {result.actual.get('net_requirement_after')})."

    def _complete(self, summary: str) -> RunState:
        self.state.status = "completed"
        self.state.final_summary = summary
        self.trace("system", "run_completed", {"summary": summary})
        self.save()
        return self.state

    def _escalate(self, summary: str, by_agent: bool = False) -> RunState:
        self.state.status = "escalated"
        self.state.final_summary = ("Agent escalated: " if by_agent else "System escalated: ") + summary
        self.trace("agent" if by_agent else "system", "escalated", {"summary": summary})
        self.save()
        return self.state


def get_runner(run_id: str, llm: LLM) -> Runner | None:
    return Runner.load(run_id, llm)
