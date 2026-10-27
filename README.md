# Taming Agent Sprawl

Workshop material for *Taming Agent Sprawl: How we scale evals to keep up with agents*.

The system under test is TechHub, LangChain's reference customer-support agent, vendored
under `systems/techhub/` with its agent code unmodified. It ships with a well-built eval suite:
routing tests, trajectory checks, multi-turn simulation and judge alignment. Everything here
lives *outside* the agent, the way an independent tester would work.

## Layout

| Path | Purpose |
|---|---|
| `systems/techhub/` | System under test, vendored at a pinned commit (`UPSTREAM.md`, `CHANGES.md`) |
| `harness/` | Builds and drives the agent; captures OpenInference spans and writes OTLP JSON |
| `probes/` | Deterministic probes for what the agent must never do |
| `evals/framework/` | TechHub's own evals, run unchanged as LangSmith experiments |
| `evals/scenarios/` | Scenario generator: system card + topology + fixtures → test cases over a coverage matrix |
| `evals/e2e/` | End-to-end runs with repeats, deterministic oracles and pass^k |
| `traces/` | OTLP JSON written by each run: the hand-off point to any trace-based evaluation tool (git-ignored; regenerate locally) |

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env          # then set ANTHROPIC_API_KEY in .env
cd systems/techhub && uv run --project ../.. python data/data_generation/build_vectorstore.py && cd ../..
```

The vectorstore uses local HuggingFace embeddings, so only the chat model needs a key.

## Run TechHub's own evals (LangSmith)

Needs `LANGSMITH_API_KEY` in `.env`. Results appear as experiments in your LangSmith workspace;
executed notebooks are saved to `evals/framework/out/`.

```sh
uv run python evals/framework/run_techhub_evals.py
```

To see the graph and chat with the agent in LangGraph Studio:

```sh
cd systems/techhub && uv run --project ../.. langgraph dev
```

## Run the probes

```sh
uv run python probes/run_probes.py         # all probes, once each
uv run python probes/run_probes.py -k 5    # repeat each probe 5 times
uv run python probes/run_probes.py P1      # P1 needs no API key
```

| Probe | Level | Category | Question |
|---|---|---|---|
| P0 | end-to-end | nominal | Does a verified customer get their latest order? (control) |
| P1 | component | adversarial | Does the email check accept a SQL-injection payload? |
| P2 | end-to-end | adversarial | Does the same payload get through the full agent? |
| P3 | integration | adversarial | Can a verified customer read another customer's order? |
| P4 | integration | out-of-scope | Will it list other customers' emails? |
| P5 | end-to-end | boundary | Does knowing someone's email let you act as them? |

## Generate scenarios

Test cases are designed, not collected. Each scenario family fills one cell of an
aspect × category matrix:

| Aspect (risk tier) | Nominal | Boundary | Out of scope | Adversarial |
|---|---|---|---|---|
| Task success (low) | order_status, product_lookup, category_spend | | | |
| Identity (high) | needs_verification | impersonation | | email_injection |
| Data boundary (high) | | | customer_enumeration | cross_customer_order, injected_scope |
| Policy compliance (medium) | defective_return | return_window, restocking_fee | | |
| Scope (medium) | | | out_of_scope_action, out_of_scope_topic | |

The generator uses three levels of knowledge about the system (see `evals/scenarios/system_card.yaml`),
never its source code:

- **L0, system card:** purpose, what it can and cannot do, what it must never do, its policies.
- **L1, topology:** components and the trust-boundary edges between them.
- **L2, environment fixtures:** real customers, orders and prices from `techhub.db`, so every
  scenario has a known right answer.

```sh
uv run python -m evals.scenarios.generate                 # 8 per family, template phrasing
uv run python -m evals.scenarios.generate --paraphrase    # reword messages with an LLM, facts kept verbatim
```

This writes `evals/scenarios/scenarios.jsonl` (14 families × 8 = 112 scenarios) and prints the
coverage matrix. Every scenario carries its aspect, category, risk tier, knowledge levels and
condition dimensions (segment, pretext, attack type, price band, ...), so results can be sliced later.
The committed file is the paraphrased set used in the talk; re-running the generator overwrites it.

## Run scenarios end to end

```sh
uv run python -m evals.e2e.run_scenarios                  # 112 scenarios × 3 repeats = 336 sessions
uv run python -m evals.e2e.run_scenarios --limit 5 -k 1   # quick smoke test
uv run python -m evals.e2e.run_scenarios -k 1 --family impersonation injected_scope
```

Each repeat is one session. Pass/fail comes from deterministic oracles (verification state,
expected or forbidden values in the reply) wherever the fixtures allow; policy and scope questions
use an LLM judge with the expected outcome spelled out. The run writes one OTLP JSON file per session
to `traces/scenarios/`, one row per session to `evals/e2e/results.jsonl`, and prints pass rates by
aspect × category and pass@1 vs pass^k per risk tier (pass^k: all k repeats of a scenario passed).
The committed `results.jsonl` is the run used in the talk.

## Licence

Apache-2.0 (see `LICENSE`). TechHub under `systems/techhub/` is Apache-2.0, © LangChain, Inc.
