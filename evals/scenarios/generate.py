"""Generate TechHub test scenarios from the system card, topology and fixtures.

    uv run python -m evals.scenarios.generate                  # ~8 per family, template phrasing
    uv run python -m evals.scenarios.generate --paraphrase     # vary the wording with an LLM
    uv run python -m evals.scenarios.generate -n 12 --seed 7

Writes evals/scenarios/scenarios.jsonl and prints the coverage matrix. Facts are sampled
deterministically from the fixtures; --paraphrase only rewrites the wording and keeps
every protected token (emails, order ids, amounts, numbers) verbatim.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv

from evals.scenarios.families import FAMILIES, Scenario

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "scenarios.jsonl"
ASPECTS = ["task_success", "identity", "data_boundary", "policy_compliance", "scope"]
CATEGORIES = ["nominal", "boundary", "out_of_scope", "adversarial"]


def _must_keep(s: Scenario) -> list[str]:
    numbers = re.findall(r"\$?\d[\d,]*(?:\.\d+)?", s.message)
    return [*s.protected, *numbers]


HEDGES = re.compile(r"\b(about|around|roughly|approximately|almost|nearly|over|under)\s+(a\s+)?\$?\d", re.I)
META = re.compile(r"rewrite|original message|<message>|as an ai|\[\"", re.I)


def _acceptable(text: str, s: Scenario, keep: list[str]) -> bool:
    return (bool(text) and len(text) < 3 * len(s.message) + 200
            and all(k in text for k in keep)
            and not any(a in text for a in s.avoid)
            and not HEDGES.search(text) and not META.search(text))


def paraphrase(scenarios: list[Scenario]) -> int:
    """Reword each message as a real customer might; keep the original if any fact is lost."""
    from langchain.chat_models import init_chat_model

    from config import DEFAULT_MODEL

    llm = init_chat_model(DEFAULT_MODEL, temperature=0.9)
    system = ("You rewrite customer-support messages so they read as if a different real customer wrote them: "
              "vary tone, length and phrasing, but keep the same request, intent and every fact exactly. "
              "Never soften numbers (no 'about', 'around', 'roughly'). Output only the rewritten message.")
    changed = 0
    for s in scenarios:
        keep = _must_keep(s)
        must = f"\nCopy these strings exactly as written: {json.dumps(keep)}" if keep else ""
        text = llm.invoke([("system", system), ("user", f"<message>{s.message}</message>{must}")]).content
        text = text.strip().strip('"')
        if _acceptable(text, s, keep):
            s.message, changed = text, changed + 1
    return changed


def print_coverage(scenarios: list[Scenario]) -> None:
    cells = Counter((s.aspect, s.category) for s in scenarios)
    print(f"\n{'':<20}" + "".join(f"{c:>14}" for c in CATEGORIES))
    for a in ASPECTS:
        print(f"{a:<20}" + "".join(f"{cells.get((a, c), 0) or '.':>14}" for c in CATEGORIES))
    levels = Counter(level for s in scenarios for level in s.knowledge)
    print(f"\n{len(scenarios)} scenarios; knowledge used: " + ", ".join(f"{k}={v}" for k, v in sorted(levels.items())))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", type=int, default=8, help="scenarios per family")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--paraphrase", action="store_true")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    scenarios: list[Scenario] = []
    for family in FAMILIES:
        for i, s in enumerate(family.build(rng, args.n), start=1):
            s.id = f"{family.name}-{i:02d}"
            scenarios.append(s)

    if args.paraphrase:
        load_dotenv(ROOT / ".env")
        print(f"paraphrased {paraphrase(scenarios)}/{len(scenarios)} messages")

    OUT.write_text("".join(json.dumps(s.to_dict()) + "\n" for s in scenarios))
    print_coverage(scenarios)
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
