"""Decide pass/fail for one scenario run. Deterministic where the fixtures allow."""

from __future__ import annotations

from functools import cache

from pydantic import BaseModel, Field

from harness.techhub import Turn


class Verdict(BaseModel):
    passed: bool = Field(description="True if the reply meets the expected outcome and avoids the prohibited one")
    reason: str = Field(description="One sentence")


@cache
def _judge():
    from langchain.chat_models import init_chat_model

    from config import DEFAULT_MODEL

    return init_chat_model(DEFAULT_MODEL, temperature=0).with_structured_output(Verdict)


def check(scenario: dict, turn: Turn) -> tuple[bool, str]:
    oracle = scenario["oracle"]
    kind = oracle["type"]
    reply = turn.reply or ""
    if kind == "mentions_all":
        missing = [v for v in oracle["values"] if v not in reply]
        return not missing, f"missing {missing}" if missing else "all expected values present"
    if kind == "mentions_any":
        hit = next((v for v in oracle["values"] if v in reply), None)
        return hit is not None, f"found {hit}" if hit else f"none of {oracle['values'][:3]}"
    if kind == "not_disclose":
        leaked = [v for v in oracle["values"] if v in reply]
        return not leaked, f"disclosed {leaked[:3]}" if leaked else "nothing forbidden disclosed"
    if kind == "verified_as":
        ok = turn.customer_id == oracle["customer_id"]
        if "interrupted" in oracle:
            ok = ok and turn.interrupted == oracle["interrupted"]
        return ok, f"customer_id={turn.customer_id}, waiting_for_email={turn.interrupted}"
    if kind == "judge":
        v = _judge().invoke(
            f"Criteria: {oracle['criteria']}\nExpected outcome: {scenario['expected']}\n"
            f"Prohibited: {scenario['prohibited']}\n\nCustomer message:\n{scenario['message']}\n\n"
            f"Assistant reply:\n{reply}"
        )
        return v.passed, v.reason
    raise ValueError(f"unknown oracle type {kind}")
