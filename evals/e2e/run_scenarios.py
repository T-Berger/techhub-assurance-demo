"""Run generated scenarios end to end, k times each, and record results and traces.

    uv run python -m evals.e2e.run_scenarios                 # every scenario, k=3
    uv run python -m evals.e2e.run_scenarios -k 1 --family order_status cross_customer_order
    uv run python -m evals.e2e.run_scenarios --limit 5 -k 1  # quick smoke test

Each run is one session (thread_id = "<scenario id>-r<repeat>"). Writes one OTLP JSON
file per session to traces/scenarios/ and one row per session to evals/e2e/results.jsonl,
results are appended as each session finishes; then prints pass rates by aspect x category and pass^k per risk tier.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("LANGSMITH_PROJECT", "techhub-scenarios")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
load_dotenv(ROOT / ".env")

from evals.e2e.oracles import check  # noqa: E402
from evals.scenarios.generate import ASPECTS, CATEGORIES  # noqa: E402
from harness.techhub import ask, build_agent, warm_up  # noqa: E402
from harness.tracing import TraceRecorder, write_otlp_json  # noqa: E402

SCENARIOS = ROOT / "evals" / "scenarios" / "scenarios.jsonl"
RESULTS = Path(__file__).resolve().parent / "results.jsonl"
TRACE_DIR = ROOT / "traces" / "scenarios"


def run_one(agent, scenario: dict, repeat: int) -> dict:
    session = f"{scenario['id']}-r{repeat}"
    try:
        turn = ask(agent, scenario["message"], thread_id=session, email_if_asked=scenario.get("email_if_asked"))
        passed, evidence = check(scenario, turn)
        reply, customer_id, asked = turn.reply, turn.customer_id, turn.asked_for_email
    except Exception as exc:  # a crash is a result too
        passed, evidence = False, f"ERROR {type(exc).__name__}: {str(exc)[:160]}"
        reply, customer_id, asked = "", None, None
    return {"session_id": session, "scenario_id": scenario["id"], "repeat": repeat,
            "family": scenario["family"], "aspect": scenario["aspect"], "category": scenario["category"],
            "risk_tier": scenario["risk_tier"], "knowledge": scenario["knowledge"],
            "dimensions": scenario["dimensions"], "passed": passed, "evidence": evidence,
            "customer_id": customer_id, "asked_for_email": asked, "reply": reply}


def summarise(rows: list[dict], k: int) -> None:
    cells = defaultdict(list)
    for r in rows:
        cells[(r["aspect"], r["category"])].append(r["passed"])
    print(f"\npass rate by aspect x category ({len(rows)} sessions)")
    print(f"{'':<20}" + "".join(f"{c:>14}" for c in CATEGORIES))
    for a in ASPECTS:
        line = ""
        for c in CATEGORIES:
            v = cells.get((a, c))
            line += f"{(f'{sum(v) / len(v):.0%} ({len(v)})' if v else '.'):>14}"
        print(f"{a:<20}{line}")

    per_scenario = defaultdict(list)
    for r in rows:
        per_scenario[(r["risk_tier"], r["scenario_id"])].append(r["passed"])
    print(f"\npass@1 vs pass^{k} by risk tier")
    for tier in ("high", "medium", "low"):
        runs = [v for (t, _), v in per_scenario.items() if t == tier]
        if runs:
            at1 = sum(sum(v) / len(v) for v in runs) / len(runs)
            all_k = sum(all(v) for v in runs) / len(runs)
            print(f"  {tier:<7} scenarios={len(runs):<4} pass@1={at1:.0%}  pass^{k}={all_k:.0%}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", type=int, default=3, help="repeats per scenario")
    parser.add_argument("--family", nargs="*", help="only these families")
    parser.add_argument("--limit", type=int, help="first N scenarios only")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    scenarios = [json.loads(line) for line in SCENARIOS.read_text().splitlines()]
    if args.family:
        scenarios = [s for s in scenarios if s["family"] in args.family]
    scenarios = scenarios[: args.limit] if args.limit else scenarios

    recorder = TraceRecorder()
    agent = build_agent()
    warm_up()
    recorder.take()  # drop warm-up spans
    rows: list[dict] = []
    lock = threading.Lock()
    # With --family, keep earlier results for every other family and replace only these.
    rerun = {s["id"] for s in scenarios}
    kept = [r for r in map(json.loads, RESULTS.read_text().splitlines())
            if r["scenario_id"] not in rerun] if args.family and RESULTS.exists() else []
    RESULTS.write_text("".join(json.dumps(r) + "\n" for r in kept))
    jobs = [(s, r) for r in range(args.k) for s in scenarios]
    print(f"running {len(scenarios)} scenarios x {args.k} = {len(jobs)} sessions with {args.workers} workers")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, agent, s, r) for s, r in jobs]
        for i, fut in enumerate(as_completed(futures), start=1):
            row = fut.result()
            write_otlp_json(recorder.take_session(row["session_id"]), TRACE_DIR / f"{row['session_id']}.json")
            with lock:
                rows.append(row)
                with RESULTS.open("a") as f:
                    f.write(json.dumps(row) + "\n")
            if i % 25 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} done", flush=True)

    rows = kept + rows
    rows.sort(key=lambda r: (r["scenario_id"], r["repeat"]))
    RESULTS.write_text("".join(json.dumps(r) + "\n" for r in rows))
    summarise(rows, args.k)
    print(f"\nwrote {RESULTS.relative_to(ROOT)} and {len(rows)} traces to {TRACE_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
