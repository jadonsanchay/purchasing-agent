# Design decisions and trade-offs

Short log of the choices that shaped the solution, so they can be discussed and changed.

## Scope
- **Scenario 1 deep, Scenario 2 alongside.** Both share the same tools, policy and validator; S2 mostly adds the amend/cancel executor paths and the alternate-supplier reasoning. Scenarios 3 and 4 are partially covered by fixtures S1-e (forecast changed) and S1-c/d/f (constraints), but are not claimed as implemented.
- **Nine fixtures rather than one.** Variations of the same situation are what make an eval meaningful; a single happy path proves nothing about judgement.

## Agent design
- **Structured `propose_decision` instead of free text.** Action, quantity, supplier, PO, additional orders, rationale, factors, confidence and expected outcome. The rest of the system consumes this as data.
- **Coverage target = lead time + 7-day review period + safety stock days.** Simple, explainable, and the same formula everywhere. A real system would use service-level-driven safety stock and a real forecast; that is deliberately out of scope.
- **Recent actuals vs forecast (>30%) raises a warning, not a decision.** The agent must decide whether that is enough evidence. In S1-e the intended answer is `investigate`, because the cause of the spike changes the right quantity.
- **Approval triggers:** PO value above `APPROVAL_THRESHOLD`, or deviation from the system recommendation above `MAX_DEVIATION_PCT`. Hard constraint failures never reach approval; they are bounced back to the agent.

## Feedback loop
- **Validator checks both the agent's promise and the system's invariants.** The agent can be wrong about what it expects; the system can be wrong about what it did. Both are caught.
- **Recovery is a normal conversation turn.** The mismatch list and the current state are appended as a user message; the agent re-investigates with the same tools. Nothing special-cased.
- **Two recovery turns, then escalate.** Enough to fix a stale read (S1-f) or try one alternate (S2-c), not enough to loop.

## Mock ERP
- **SQLite, reset per scenario.** Reproducible runs matter more than persistence for a take-home.
- **ERP enforces its own rules.** Otherwise the gate and the validator would be checking the same code and the fault scenarios would be theatre.
- **Notice-aware inbound.** When a supplier says it can ship 250 of 500, expected inbound is 250. The recommender in S1-b deliberately ignores open POs to create a realistic disagreement.

## LLM
- **OpenAI Responses API with strict function tools; model from env.** Stateless requests (full conversation each turn) keep the runner simple and make pause/resume trivial.
- **`ScriptedLLM` and reference trajectories.** They make the runner, gate, validator and rubric testable without network, act as a no-key demo mode, and double as a harness self-check: if the rubric ever fails a reference trajectory, the rubric or the fixture is wrong.

## Evaluation
- **Deterministic rubric first.** Six dimensions mirror the six questions in the brief. Each is a concrete check on the trace and the final ERP state, not an LLM opinion.
- **Negative tests for the rubric.** A blind accept, an over-buy on top of an open PO, and an unneeded alternate order all must FAIL. A rubric that only passes things is not a rubric.
- **Repeat runs for consistency.** `--repeat 3` shows whether the live model is stable; per-case pass counts are printed.
- **No LLM judge by default.** Rationale quality is only surfaced as a soft signal (number density). Cheap, honest, and avoids grading the model with the model.

## Known limitations
- Single SKU per PO; no multi-line orders.
- One node per scenario; no inter-node transfers as an option.
- Approval is a single button; there is no role model.
- No retries or backoff around the OpenAI call beyond the SDK defaults.
