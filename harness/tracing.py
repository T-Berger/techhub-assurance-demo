"""Capture OpenInference spans in memory and write them as OTLP JSON, one file per session.

The OTLP JSON file is the hand-off point: any trace-consuming evaluation tool
can read it without knowing which framework produced it.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from pathlib import Path

from google.protobuf.json_format import MessageToDict
from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider


class _SessionCollector(SpanProcessor):
    """Keep finished spans grouped by session.id, so each session can be written as soon as it ends."""

    def __init__(self) -> None:
        self._spans: dict[str, list[ReadableSpan]] = defaultdict(list)
        self._lock = threading.Lock()

    def on_end(self, span: ReadableSpan) -> None:
        with self._lock:
            self._spans[str(span.attributes.get("session.id", "unknown"))].append(span)

    def pop(self, session_id: str) -> list[ReadableSpan]:
        with self._lock:
            return self._spans.pop(session_id, [])

    def pop_all(self) -> list[ReadableSpan]:
        with self._lock:
            spans = [s for group in self._spans.values() for s in group]
            self._spans.clear()
            return spans


class TraceRecorder:
    """Instrument LangChain/LangGraph once and collect spans."""

    def __init__(self) -> None:
        self._collector = _SessionCollector()
        provider = TracerProvider()
        provider.add_span_processor(self._collector)
        LangChainInstrumentor().instrument(tracer_provider=provider)

    def take(self) -> list[ReadableSpan]:
        """Return every span recorded since the last call and reset the buffer."""
        return self._collector.pop_all()

    def take_session(self, session_id: str) -> list[ReadableSpan]:
        """Return and forget the spans of one finished session."""
        return self._collector.pop(session_id)


def _reattach_orphans(span_dicts: list[dict]) -> int:
    """Re-parent LLM spans that the middleware-wrapped model call left without a parent.

    Each orphan is attached to the innermost `model` span that encloses it in time,
    which is where LangSmith's own trace places it.
    """
    roots = [s for s in span_dicts if not s.get("parentSpanId")]
    models = [s for s in span_dicts if s["name"] == "model"]
    fixed = 0
    for orphan in roots:
        if orphan["name"] == "model" or "Chat" not in orphan["name"]:
            continue
        start, end = int(orphan["startTimeUnixNano"]), int(orphan["endTimeUnixNano"])
        enclosing = [m for m in models
                     if int(m["startTimeUnixNano"]) <= start and end <= int(m["endTimeUnixNano"])]
        if enclosing:
            parent = min(enclosing, key=lambda m: int(m["endTimeUnixNano"]) - int(m["startTimeUnixNano"]))
            orphan["traceId"], orphan["parentSpanId"] = parent["traceId"], parent["spanId"]
            fixed += 1
    return fixed


def write_otlp_json(spans: list[ReadableSpan], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = MessageToDict(encode_spans(spans))
    _reattach_orphans([s for rs in payload.get("resourceSpans", [])
                       for ss in rs.get("scopeSpans", []) for s in ss.get("spans", [])])
    path.write_text(json.dumps(payload, indent=1))
    return path


def tool_calls(spans: list[ReadableSpan]) -> list[tuple[str, str]]:
    """(tool name, input) for every tool span, in start order."""
    tools = [s for s in spans if s.attributes.get("openinference.span.kind") == "TOOL"]
    tools.sort(key=lambda s: s.start_time)
    return [(s.name, str(s.attributes.get("input.value", ""))) for s in tools]
