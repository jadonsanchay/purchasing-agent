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

## What the first live runs changed (gpt-4.1, 2026-09-14)
- **S1-e (forecast vs actuals).** The model saw the +83% spike, wrote it into its rationale, and still accepted the forecast-based 800. The prompt now says a demand-signal flag makes the forecast quantity an unsafe default: investigate, or size to actual demand and explain. The rubric accepts either `investigate`/`escalate` or a `modify` of at least 1000 units (which trips the deviation gate, so a human sees it). Accepting 800 unchanged still fails. After the change the model sized to 1,500 with a clear rationale.
- **S1-f (budget consumed at execution).** After the ERP rejection the model correctly found the 400-unit maximum but escalated because "it needs approval". Escalating a decision the system would route for approval anyway is a cop-out, so the prompt now says approval is not a reason to escalate. The rerun proposed 400, paused for approval, and validated clean.
- **Rate limits.** A recovery run alone can exceed a low-tier 30k tokens-per-minute limit because the whole conversation is resent every turn. The OpenAI adapter now retries 429/5xx with exponential backoff and honours `Retry-After`; the SDK's built-in retries were too short.
- **Cost.** Measured, not estimated: about 4-7k input and ~0.9k output tokens for a clean run, 17-24k input for a run with a recovery turn. Roughly $0.02-0.06 per run on gpt-4.1; a full nine-case pass is well under a dollar.

## Live eval pass 1 (gpt-4.1, 27 runs, 22 passed) and what it changed
- **S2-a (3/3 failed) was a rubric bug, not an agent bug.** I had written the expected inbound range as "replace the 250 shortfall". The agent instead used `compute_coverage` for the alternate supplier, which says ~610 more units are needed over the 14-day window, and ordered 500-650 from S-ANDINA. That is the more rigorous answer. The range is now 450-1000 with the reasoning in the YAML. Two of the three runs also used a recovery turn because the validator flagged a remaining coverage gap the agent had not marked as accepted, then topped up; that is the contract working as intended, and it is a note, not a failure.
- **S1-a (1/3 failed)** proposed `modify` with the unchanged 800. Same decision, wrong label. The rubric accepts `modify` at exactly 800 and the prompt says to use `accept` when nothing changes.
- **S1-e (1/3 failed)** sized to 2,200 units, about 20 days at actual demand, beyond the 15-day window. A genuine over-buy. The prompt now says to size to the same coverage window, not beyond it. The 1000-2000 rubric range stands.
- **Everything else was 3/3**, including both recovery cases (S1-f, S2-c). Information gathering, constraint respect, validation and recovery dimensions were 27/27.
- **Pass 2** re-ran S1-a, S1-e and S2-a with three repeats each after those fixes: S1-a 3/3, S1-e 2/3, S2-a 1/3. The two S2-a "failures" were label artefacts: the agent's first proposal (amend to 250, 650 from the alternate, 900 inbound) was right, but it predicted the PO status `partially_confirmed` where the ERP says `amended`. The validator flagged that, the agent spent a recovery turn confirming the state with a bare `accept`, and the rubric then judged that trailing `accept` instead of the decision that acted. Fixes: the validator compares PO status by class (live vs cancelled/rejected) since quantity mismatches are already caught separately, and the rubric judges the last decision that produced ERP actions.
- **Pass 3** re-ran S2-a: 3/3, no recovery turns. **Final: 26/27.** The one remaining failure is S1-e run 1, where the model accepted the forecast-based 800 despite writing the +83% spike into its own rationale. That is a real inconsistency on the hardest judgement case and is left in the report as such rather than tuned away.
- Total live spend across all passes was roughly $1.2 on gpt-4.1 (about 45 runs including smoke tests).

## Known limitations
- Single SKU per PO; no multi-line orders.
- One node per scenario; no inter-node transfers as an option.
- Approval is a single button; there is no role model.
- No retries or backoff around the OpenAI call beyond the SDK defaults.
