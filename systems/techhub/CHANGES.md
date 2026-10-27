# Changes from upstream

The agent code (`agents/`, `tools/`, `config.py`) and TechHub's own evals are unmodified.
Material not needed for the workshop was removed to avoid confusion.

## Kept: the system under test

- `agents/`, `tools/`, `config.py`, `data/`: the complete agent as composed in upstream's Module 2 §2,
  `create_supervisor_hitl_agent(db_agent=create_sql_agent(), docs_agent=create_docs_agent())`
- `deployments/supervisor_hitl_sql_agent_graph.py` and `langgraph.json`: the same graph, for `langgraph dev`

## Kept: TechHub's existing evals (the baseline we compare against)

- `evaluators/evaluators.py`: correctness judge and tool-call count
- `workshop_modules/module_2/`: baseline evaluation, eval-driven development, routing / trajectory /
  multi-turn simulation evals, judge alignment, plus `baseline_dataset.json` and `golden_set.json`

## Removed

- `workshop_modules/module_1/`: step-by-step agent building tutorials
- `workshop_modules/module_3/`: LangSmith deployment, online evals, SDK usage, CI regression gate
- `evals/run_ci_eval.py`, `.github/workflows/eval-regression.yml`: the CI regression gate
- `deployments/` graphs other than `supervisor_hitl_sql_agent_graph.py`; their entries in `langgraph.json`
- `simulations/`, `.github/workflows/simulate_traffic.yml`: synthetic traffic against a LangSmith deployment
- `examples/`, `scripts/demo_ci_pr.sh`, `evals/DEMO_TRIGGER.md`, `CLAUDE.md`
- `.env.example`, `uv.lock`: the environment is managed from the repository root

## Edited

- `README.md`: added a note at the top that this is a trimmed copy
- `langgraph.json`: kept one graph and pointed `env` at the root `.env`
