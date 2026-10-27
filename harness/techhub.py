"""Build and drive the complete TechHub agent."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from langgraph.types import Command

from agents import create_docs_agent, create_sql_agent, create_supervisor_hitl_agent


def build_agent():
    """Full system: verification graph + supervisor + SQL and docs sub-agents.

    The same composition as deployments/supervisor_hitl_sql_agent_graph.py, with a checkpointer.
    """
    return create_supervisor_hitl_agent(db_agent=create_sql_agent(), docs_agent=create_docs_agent())


def warm_up() -> None:
    """Load the document index and embedding model once, before any concurrent use.

    TechHub loads them lazily without a lock; parallel first calls crash the process.
    The embedder is kept on CPU: concurrent use of Apple's MPS backend aborts the process.
    """
    from tools.documents import get_policy_retriever, get_product_retriever, get_vectorstore

    get_vectorstore().embeddings._client.to("cpu")  # the SentenceTransformer inside HuggingFaceEmbeddings
    get_product_retriever().invoke("warm-up")
    get_policy_retriever()


@dataclass
class Turn:
    reply: str
    customer_id: str | None
    interrupted: bool
    asked_for_email: bool
    messages: list = field(repr=False)


def ask(agent, message: str, thread_id: str | None = None, email_if_asked: str | None = None) -> Turn:
    """Send one user message. An interrupt means the agent is waiting for an email;
    with email_if_asked, answer it once so the conversation can reach a real reply."""
    config = {"configurable": {"thread_id": thread_id or str(uuid.uuid4())}}
    result = agent.invoke({"messages": [{"role": "user", "content": message}]}, config=config)
    asked = bool(agent.get_state(config).interrupts)
    if asked and email_if_asked:
        result = agent.invoke(Command(resume=f"My email is {email_if_asked}"), config=config)
    state = agent.get_state(config)
    return Turn(
        reply=result["messages"][-1].content,
        customer_id=state.values.get("customer_id"),
        interrupted=bool(state.interrupts),
        asked_for_email=asked,
        messages=result["messages"],
    )
