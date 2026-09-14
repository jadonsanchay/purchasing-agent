.PHONY: setup seed dev backend frontend test evals

setup:
	cd backend && uv sync
	cd frontend && npm install

seed:
	cd backend && uv run python -m app.seed

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

dev:
	$(MAKE) -j2 backend frontend

test:
	cd backend && uv run pytest -q

evals:
	cd backend && uv run python -m evals.run_evals
