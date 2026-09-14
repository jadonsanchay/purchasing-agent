"""Run the eval cases against the agent and score each run on the six questions from the brief.

    uv run python -m evals.run_evals                      # live OpenAI agent, 1 run per case
    uv run python -m evals.run_evals --repeat 3           # consistency across repeats
    uv run python -m evals.run_evals --provider scripted  # harness self-check with reference trajectories
    uv run python -m evals.run_evals --only S1-f,S2-c

Writes evals/report.md (or report-scripted.md) and a JSON dump of every run next to it.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

from app import db, scenarios
from app.agent.llm import LLM, OpenAILLM, ScriptedLLM
from app.agent.runner import Runner
from app.agent.scripted import SCRIPTS
from app.config import settings
from app.erp import reads
from app.models import RunState

HERE = Path(__file__).resolve().parent
DIMENSIONS = ["decision_correct", "information_gathered", "constraints_respected", "action_appropriate", "validated", "recovery_behaviour"]


def make_llm(provider: str, scenario_id: str) -> LLM:
    if provider == "scripted":
        return ScriptedLLM([list(t) for t in SCRIPTS[scenario_id]])
    if not settings.openai_api_key:
        sys.exit("OPENAI_API_KEY not set. Put it in backend/.env or use --provider scripted.")
    return OpenAILLM()


def tools_before_first_proposal(state: RunState) -> set[str]:
    seen: set[str] = set()
    for e in state.trace:
        if e.event == "decision_proposed":
            break
        if e.event == "tool_call":
            seen.add(str(e.payload.get("tool")))
    return seen


def effective_decision(state: RunState):
    """The decision to judge. After a recovery turn the agent may end with `accept` and no quantity, meaning "the
    current ERP state is acceptable"; that confirmation is not the purchasing decision. Use the last proposal that
    produced a non-empty plan, falling back to the final decision."""
    d = state.decision
    if d and d.action.value == "accept" and d.quantity is None and not d.po_id and not d.additional_orders and state.actions:
        for e in reversed(state.trace):
            if e.event == "decision_proposed" and (e.payload.get("quantity") or e.payload.get("po_id") or e.payload.get("additional_orders")):
                from app.models import Decision
                return Decision(**e.payload)
    return d


def score(case: dict, state: RunState) -> dict:
    """Returns {dimension: {"pass": bool, "notes": [...]}} plus derived facts."""
    out: dict[str, dict] = {d: {"pass": True, "notes": []} for d in DIMENSIONS}

    def fail(dim: str, note: str) -> None:
        out[dim]["pass"] = False
        out[dim]["notes"].append(note)

    d = effective_decision(state)
    ok_actions = [a for a in state.actions if a.result.get("ok")]
    ok_kinds = [a.kind for a in ok_actions]
    new_po_qty = next((a.result.get("ordered_qty") for a in ok_actions if a.kind == "create_po"), None)

    # 1. decision correct
    if d is None:
        fail("decision_correct", "no decision reached")
    else:
        if d.action.value not in case["expected_actions"]:
            fail("decision_correct", f"action {d.action.value} not in {case['expected_actions']}")
        rng = case.get("expected_qty")
        if rng and new_po_qty is not None and not (rng["min"] <= new_po_qty <= rng["max"]):
            fail("decision_correct", f"new PO qty {new_po_qty} outside [{rng['min']}, {rng['max']}]")
        if rng and rng["min"] > 0 and new_po_qty is None and d.action.value in ("accept", "modify") and not d.po_id:
            fail("decision_correct", "expected a new PO but none was created")

    # 2. information gathered (before the first proposal)
    seen = tools_before_first_proposal(state)
    missing = [t for t in case.get("required_tools", []) if t not in seen]
    if missing:
        fail("information_gathered", f"never called {missing} before deciding")

    # 3. constraints respected: final ERP state sane, no successful PO violates the policy
    final = case.get("final", {})
    with db.connect() as conn:
        inbound = reads.inbound_qty(conn, case["sku"], case["node_id"])
        prod = reads.product(conn, case["sku"])
        budget = reads.budget(conn, prod["category"])
        storage = reads.storage(conn, case["node_id"])
        po_statuses = {pid: (reads.purchase_order(conn, pid) or {}).get("status") for pid in final.get("po_status", {})}
    if "budget_headroom_min" in final and budget["headroom"] < final["budget_headroom_min"]:
        fail("constraints_respected", f"budget headroom {budget['headroom']} < {final['budget_headroom_min']}")
    if "storage_free_min" in final and storage["free_m3"] < final["storage_free_min"]:
        fail("constraints_respected", f"storage free {storage['free_m3']} < {final['storage_free_min']}")
    for rep in (state.gate or {}).get("reports", []):
        if rep.get("hard_fail") and state.actions and state.actions[-1].result.get("ok"):
            fail("constraints_respected", "a hard-failing proposal was executed")
    for a in ok_actions:
        if a.kind == "create_po" and a.result.get("confirmed_qty") != a.result.get("ordered_qty"):
            out["constraints_respected"]["notes"].append(f"{a.result.get('po_id')} only partially confirmed")

    # 4. action appropriate
    for k in case.get("expected_kinds", []):
        if k not in ok_kinds:
            fail("action_appropriate", f"expected a successful {k}, got {ok_kinds}")
    for k in case.get("forbidden_kinds", []):
        if k in ok_kinds:
            fail("action_appropriate", f"{k} should not have been executed")
    if state.status not in case["expected_status"]:
        fail("action_appropriate", f"final status {state.status} not in {case['expected_status']}")
    rng = final.get("inbound")
    if rng and not (rng["min"] <= inbound <= rng["max"]):
        fail("action_appropriate", f"final inbound {inbound} outside [{rng['min']}, {rng['max']}]")
    for pid, exp in final.get("po_status", {}).items():
        if po_statuses.get(pid) != exp:
            fail("action_appropriate", f"{pid} is {po_statuses.get(pid)}, expected {exp}")

    # 5. validated: every batch of executed actions was followed by a validation; terminal completed runs validate ok
    if state.actions and not state.validations:
        fail("validated", "actions executed but no validation recorded")
    if state.status == "completed" and state.validations and not state.validations[-1].ok:
        fail("validated", "run completed with a failing validation")
    if state.status == "escalated" and state.validations and state.validations[-1].ok and case.get("expect_recovery"):
        out["validated"]["notes"].append("escalated after a passing validation")

    # 6. recovery behaviour
    if case.get("expect_recovery") and state.recovery_turns_used == 0:
        fail("recovery_behaviour", "expected the first action to fail and a recovery turn to follow")
    if not case.get("expect_recovery") and state.recovery_turns_used > 0:
        out["recovery_behaviour"]["notes"].append(f"needed {state.recovery_turns_used} unexpected recovery turn(s)")
    if state.status == "failed":
        fail("recovery_behaviour", f"run crashed: {state.error}")

    # rationale concreteness (soft signal, not scored): count numbers in the rationale
    numbers = len(re.findall(r"\d+", d.rationale)) if d else 0

    return {
        "dimensions": out,
        "passed": all(v["pass"] for v in out.values()),
        "facts": {
            "action": d.action.value if d else None,
            "new_po_qty": new_po_qty,
            "status": state.status,
            "recovery_turns": state.recovery_turns_used,
            "tool_calls": len(state.tool_calls),
            "tools_before_decision": sorted(seen),
            "final_inbound": inbound,
            "rationale_numbers": numbers,
            "llm_tokens": sum(state.llm_usage.values()) if state.llm_usage else None,
        },
    }


def run_case(case: dict, provider: str) -> tuple[RunState, dict, float]:
    t0 = time.time()
    scenario = scenarios.load_scenario(case["scenario"])
    runner = Runner(scenario, make_llm(provider, case["scenario"]))
    state = runner.start()
    hops = 0
    while state.status == "awaiting_approval" and case.get("auto_approve") and hops < 3:
        state = runner.approve("eval-harness")
        hops += 1
    return state, score(case, state), time.time() - t0


def write_report(path: Path, provider: str, results: list[dict], repeat: int) -> None:
    lines = [f"# Eval report ({provider}{', model ' + settings.openai_model if provider == 'openai' else ''})", ""]
    lines.append(f"{len(results)} runs · {sum(r['score']['passed'] for r in results)} passed · repeat={repeat} · generated {db.now_iso()}")
    lines.append("")
    lines.append("## Per-dimension pass rate")
    lines.append("")
    lines.append("| Dimension | Pass | Question from the brief |")
    lines.append("|---|---|---|")
    questions = {
        "decision_correct": "Was the decision correct?",
        "information_gathered": "Did the agent obtain the necessary information?",
        "constraints_respected": "Did it respect relevant constraints?",
        "action_appropriate": "Did it take the appropriate action?",
        "validated": "Did it validate the result?",
        "recovery_behaviour": "What happens when the initial action does not work?",
    }
    for dim in DIMENSIONS:
        p = sum(r["score"]["dimensions"][dim]["pass"] for r in results)
        lines.append(f"| {dim} | {p}/{len(results)} | {questions[dim]} |")
    lines.append("")
    lines.append("## Per-run results")
    lines.append("")
    lines.append("| Case | Run | Result | Action | New PO qty | Status | Recovery | Tools | Inbound | Secs | Notes |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        f = r["score"]["facts"]
        notes = "; ".join(n for d in DIMENSIONS for n in r["score"]["dimensions"][d]["notes"]) or ""
        lines.append(
            f"| {r['case']} | {r['rep']} | {'PASS' if r['score']['passed'] else 'FAIL'} | {f['action']} | {f['new_po_qty'] or ''} | {f['status']} | "
            f"{f['recovery_turns']} | {f['tool_calls']} | {f['final_inbound']} | {r['seconds']:.1f} | {notes} |"
        )
    lines.append("")
    lines.append("## Rationales")
    lines.append("")
    for r in results:
        lines.append(f"**{r['case']} (run {r['rep']})** — {r['summary']}")
        lines.append("")
    path.write_text("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["openai", "scripted"], default=settings.llm_provider)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--only", default="")
    ap.add_argument("--cases", default=str(HERE / "cases.yaml"))
    ap.add_argument("--db", default="./data/evals.db", help="separate db so evals never clobber the dev server")
    ap.add_argument("--resume", action="store_true", help="skip (case, rep) pairs already present in results.json")
    args = ap.parse_args()

    settings.db_path = args.db
    cases = yaml.safe_load(Path(args.cases).read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    suffix = "-scripted" if args.provider == "scripted" else ""
    report = HERE / f"report{suffix}.md"
    results_path = HERE / f"results{suffix}.json"

    # --resume: keep completed (case, rep) pairs from a previous partial run and only run what is missing
    results: list[dict] = []
    if args.resume and results_path.exists():
        results = json.loads(results_path.read_text())
        print(f"resuming: {len(results)} run(s) already recorded")
    done = {(r["case"], r["rep"]) for r in results}

    per_case: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        for rep in range(1, args.repeat + 1):
            if (case["id"], rep) in done:
                continue
            state, sc, secs = run_case(case, args.provider)
            results.append({"case": case["id"], "rep": rep, "score": sc, "seconds": secs, "summary": state.final_summary, "state": state.model_dump(mode="json")})
            flag = "PASS" if sc["passed"] else "FAIL"
            notes = "; ".join(n for d in DIMENSIONS for n in sc["dimensions"][d]["notes"])
            print(f"[{flag}] {case['id']} run {rep}: {sc['facts']['action']} -> {state.status} ({secs:.1f}s) {notes}", flush=True)
            # persist after every run so a killed process loses nothing
            results_path.write_text(json.dumps(results, indent=1, default=str))
            write_report(report, args.provider, results, args.repeat)

    results.sort(key=lambda r: (r["case"], r["rep"]))
    for r in results:
        per_case[r["case"]].append(r["score"]["passed"])
    write_report(report, args.provider, results, args.repeat)
    results_path.write_text(json.dumps(results, indent=1, default=str))
    total = sum(sc["score"]["passed"] for sc in results)
    print(f"\n{total}/{len(results)} runs passed. Report: {report}")
    if args.repeat > 1:
        for cid, flags in per_case.items():
            print(f"  {cid}: {sum(flags)}/{len(flags)} consistent")


if __name__ == "__main__":
    main()
