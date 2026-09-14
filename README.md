# AI Purchasing Agent

An agent that assists a quick-commerce buyer: it takes a purchasing situation, investigates the relevant
data through tools, decides (accept / modify / reject / investigate / escalate), executes the decision against
a mock ERP, and **validates the outcome**, recovering or escalating when reality disagrees with the plan.

> Status: bootstrapping. Setup, architecture, evals and demo sections will be filled in as the build progresses.

## Quick start

```bash
cp .env.example backend/.env   # add your OPENAI_API_KEY
make setup
make seed
make dev                       # API on :8000, UI on :5173
make test                      # unit tests, no network needed
make evals                     # runs the eval cases against the live agent
```
