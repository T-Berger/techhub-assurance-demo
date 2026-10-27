"""Run TechHub's own Module 2 evals unchanged, as LangSmith experiments.

These are the evals the agent ships with (the "happy path" baseline). Each
notebook is executed headlessly in order and saved with its outputs to
evals/framework/out/. Results appear as experiments in your LangSmith workspace.

    uv run python evals/framework/run_techhub_evals.py            # all notebooks
    uv run python evals/framework/run_techhub_evals.py section_3  # name filter

Runtime changes, in memory only: the judge-alignment bonus hard-codes an OpenAI judge,
swapped for WORKSHOP_MODEL; section 1 reads upstream's LangSmith project, swapped for
LANGSMITH_PROJECT. The notebook files are not edited.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import nbformat
from dotenv import load_dotenv
from nbclient import NotebookClient

ROOT = Path(__file__).resolve().parents[2]
MODULE_2 = ROOT / "systems" / "techhub" / "workshop_modules" / "module_2"
OUT = Path(__file__).resolve().parent / "out"

NOTEBOOKS = [
    "section_1_baseline_evaluation.ipynb",  # correctness judge + tool-call count
    "section_2_eval_driven_development.ipynb",  # re-run on the SQL agent (needs section 1's dataset)
    "section_3_advanced_evaluation.ipynb",  # routing, trajectory, multi-turn simulation
    "bonus_align_llm_judge.ipynb",  # judge vs 30 human labels
]


def _adapt_to_local_setup(nb) -> None:
    """In-memory swaps for upstream hard-codes: the OpenAI judge and upstream's LangSmith project."""
    model = os.environ["WORKSHOP_MODEL"]
    project = os.environ["LANGSMITH_PROJECT"]
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        if cell.source.strip().startswith('JUDGE_MODEL = "openai:'):
            cell.source = f'JUDGE_MODEL = "{model}"  # swapped in by run_techhub_evals.py'
        cell.source = cell.source.replace('"langsmith-agent-lifecycle-workshop"', f'"{project}"')


def main() -> None:
    load_dotenv(ROOT / ".env")
    if not os.environ.get("LANGSMITH_API_KEY"):
        sys.exit("Set LANGSMITH_API_KEY in .env first.")
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ.setdefault("LANGSMITH_PROJECT", "techhub-workshop")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")  # section 3 runs the local embedder in threads

    selected = [n for n in NOTEBOOKS if not sys.argv[1:] or any(f in n for f in sys.argv[1:])]
    OUT.mkdir(exist_ok=True)
    for name in selected:
        nb = nbformat.read(MODULE_2 / name, as_version=4)
        _adapt_to_local_setup(nb)
        start = time.monotonic()
        print(f"running {name} ...", flush=True)
        try:
            NotebookClient(nb, timeout=900, resources={"metadata": {"path": str(MODULE_2)}}).execute()
            status = "ok"
        except Exception as exc:  # keep going: a partial notebook is still saved for inspection
            status = f"FAILED: {type(exc).__name__}: {str(exc).splitlines()[-1][:160]}"
        nbformat.write(nb, OUT / name)
        print(f"  {status} ({time.monotonic() - start:.0f}s) -> {(OUT / name).relative_to(ROOT)}")


if __name__ == "__main__":
    main()
