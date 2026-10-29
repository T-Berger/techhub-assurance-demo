"""The release change: enforce order ownership at the data boundary.

The upstream get_order_status / get_order_items tools look up an order ID
without an ownership predicate; execute_sql accepts read-only SELECT queries.
See systems/techhub/tools/database.py. This focused adaptation uses the same
seeded TechHub database, but keeps
the authenticated session outside model-controlled arguments. Neither variant
exposes arbitrary SQL. Only the candidate adds the ownership predicate.

Attribution: the parent workshop is timlrx/containerdays-sg-workshop, revision
bbe0bc65fccc541f3c6303ef756e9848c272bc72. Its TechHub files are vendored from
langchain-ai/langsmith-agent-lifecycle-workshop at
fdc59eb8f528442dc376b608cb2f60b6f4dcec2d (systems/techhub/UPSTREAM.md), Apache-2.0.
These new tools are an adaptation, not a modification of the original tools.
"""

from __future__ import annotations

import sqlite3
import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "systems/techhub/data/structured/techhub.db"
)


@dataclass(frozen=True)
class AuthenticatedSession:
    """Trusted demo fixture representing identity from a server-side login."""

    session_key: str
    customer_id: str
    customer_name: str


SESSIONS = {
    "sarah": AuthenticatedSession("sarah", "CUST-001", "Sarah Chen"),
    "marcus": AuthenticatedSession("marcus", "CUST-002", "Marcus Johnson"),
}


def authenticated_session(session_key: str) -> AuthenticatedSession:
    """Only the harness chooses a fixture; user text cannot change identity."""
    try:
        return SESSIONS[session_key]
    except KeyError as exc:
        raise ValueError(f"Unknown authenticated test session: {session_key}") from exc


def read_order(
    order_id: str,
    session: AuthenticatedSession,
    *,
    enforce_ownership: bool,
    db_path: Path = DB_PATH,
) -> dict:
    """Read an order without any model-authored SQL or customer ID argument.

    Parameter binding is used in both releases to isolate the ownership change
    from SQL injection. The fixed release checks ownership in the query itself,
    before order details or items can enter the agent's context.
    """
    if not isinstance(session, AuthenticatedSession) or SESSIONS.get(session.session_key) != session:
        raise ValueError("Identity is not a trusted authenticated test session")
    if not isinstance(order_id, str) or not order_id or len(order_id) > 128:
        return _unavailable()
    query = """SELECT order_id, customer_id, order_date, status, total_amount
               FROM orders WHERE order_id = ?"""
    parameters: tuple = (order_id,)
    if enforce_ownership:
        query += " AND customer_id = ?"
        parameters += (session.customer_id,)
    # mode=ro prevents accidental mutation, including future tool changes.
    with closing(sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        order_row = conn.execute(query, parameters).fetchone()
        if order_row is None:
            # Identical response for nonexistent and unauthorized orders avoids
            # revealing whether another customer's order exists.
            return _unavailable()
        order = dict(order_row)
        order["items"] = [
            dict(row)
            for row in conn.execute(
                """SELECT p.name, oi.quantity, oi.price_per_unit
                   FROM order_items oi JOIN products p ON p.product_id=oi.product_id
                   WHERE oi.order_id = ? ORDER BY p.product_id""",
                (order_id,),
            )
        ]
    return {"status": "ok", "order": order}


def make_order_tool(variant: str, session_key: str, tool_results: list[dict]):
    """Bind trusted identity outside the model's sole order_id argument."""
    from langchain.tools import tool

    if variant not in ("baseline", "candidate"):
        raise ValueError(f"Unknown release variant: {variant}")
    session = authenticated_session(session_key)

    @tool("get_order_details")
    def get_order_details(order_id: str) -> str:
        """Look up items, status and total for an order ID such as ORD-2025-0048."""
        output = read_order(order_id, session, enforce_ownership=variant == "candidate")
        tool_results.append({
            "tool_name": "get_order_details",
            "input": {"order_id": order_id},
            "output": output,
        })
        return json.dumps(output, ensure_ascii=False)

    return get_order_details


def _unavailable() -> dict:
    return {
        "status": "denied",
        "message": "This order is unavailable for your signed-in account. "
        "I can only help with orders belonging to your account.",
    }
