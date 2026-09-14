# purchasing-agent — working rules

AI Purchasing Agent for the Rappi take-home assignment (see `docs/assignment.pdf`).
Goal: a system that **makes, executes, and validates** purchasing decisions. Not a chatbot.

## Boundaries
- Git identity: `Sanchay Jadon <jadonsanchay@gmail.com>` (applied via `~/.gitconfig` includeIf for `Desktop/prepproj/`). Check `git config user.email` before the first commit of a session.
- Never commit `.env`, API keys, tokens, or `*.db`. `.env.example` is the only env file in git.
- Commit small and often with descriptive messages; the history is part of the evaluation. Never rewrite or squash history.
- Time-box: 6–8 hours total. Depth on Scenario 1 and 2 beats breadth. Do not start Scenario 3/4 unless S1/S2 are fully done with evals.
- Demo-grade, not production-grade: no auth, no migration framework, SQLite is fine.

## Stack
- Backend: Python 3.10+, `uv`, FastAPI, `openai` SDK (function calling), SQLite, pydantic, pytest.
- Frontend: Vite + React + TypeScript, plain CSS. Polls the API; no websockets.
- LLM model comes from `OPENAI_MODEL` env. Never hard-code a model id.

## Architecture rules
- The LLM investigates and proposes. **Deterministic code enforces and verifies.**
- Every ERP write goes `propose → policy.check() → approval gate → execute → validate → reconcile`. No tool may bypass `policy.check()`.
- `policy.py` and `validator.py` are pure and unit-tested without an LLM.
- Mock ERP may not comply (fault toggles per scenario). The validator must diff expected vs actual and either recover (bounded) or escalate.
- Every step is written to `audit_log` and surfaced in the run trace.

## Commands
- `make setup` — install backend + frontend deps
- `make seed`  — (re)create and seed the SQLite db
- `make dev`   — backend :8000 + frontend :5173
- `make test`  — `uv run pytest` (no network)
- `make evals` — run eval cases against the live agent, write `backend/evals/report.md`

Run `make test` before committing backend changes.
