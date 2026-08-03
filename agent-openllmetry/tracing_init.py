"""Initializes OpenLLMetry (Traceloop SDK) with traces exported directly to
LangSmith's OTLP ingestion endpoint.

Multi-project fan-out: Traceloop.init() registers the primary exporter,
sending every span to LANGSMITH_PROJECT (the "distributed" project). We then
add two MORE span processors directly onto that same global TracerProvider:

  - a full duplicate of every span -> LANGSMITH_PROJECT_AGENT
  - a filtered duplicate (retrieval spans only) -> LANGSMITH_PROJECT_VECTORDB

Because OTel span processors all observe the same already-recorded span,
this fan-out is genuinely free -- no re-execution, no extra Anthropic calls,
just extra HTTP POSTs to LangSmith per span. This is the one place in the
repo where "duplicate to N projects" has zero cost beyond network calls,
unlike the native LangSmith SDK side (agent-langsmith) where the same goal
requires manually reconstructing flat summary runs.

This must run before `rag_chain.build_answer_chain()`/`build_retriever()`
are called, since Traceloop patches the LangChain/Anthropic clients on init
to auto-instrument them.
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExporter,
    SpanProcessor,
)
from traceloop.sdk import Traceloop


class FilteringSpanProcessor(SpanProcessor):
    """Wraps another SpanProcessor and only forwards spans matching
    `predicate`, so a single TracerProvider can send a subset of spans to a
    different destination than the rest."""

    def __init__(self, wrapped: SpanProcessor, predicate) -> None:
        self._wrapped = wrapped
        self._predicate = predicate

    def on_start(self, span, parent_context=None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        if self._predicate(span):
            self._wrapped.on_end(span)

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


def _otlp_exporter(project_name: str, endpoint: str, api_key: str) -> SpanExporter:
    return OTLPSpanExporter(
        endpoint=f"{endpoint}/v1/traces",
        headers={"x-api-key": api_key, "Langsmith-Project": project_name},
    )


def init_tracing() -> None:
    endpoint = os.environ.get(
        "LANGSMITH_OTEL_ENDPOINT", "https://api.smith.langchain.com/otel"
    )
    api_key = os.environ["LANGSMITH_API_KEY"]
    distributed_project = os.environ.get("LANGSMITH_PROJECT", "distributed")
    agent_project = os.environ.get("LANGSMITH_PROJECT_AGENT", "agent")
    vectordb_project = os.environ.get(
        "LANGSMITH_PROJECT_VECTORDB", "vector database"
    )

    Traceloop.init(
        app_name="agent-openllmetry",
        api_endpoint=endpoint,
        headers={"x-api-key": api_key, "Langsmith-Project": distributed_project},
        disable_batch=True,
    )

    # Traceloop.init() registers a real SDK TracerProvider globally; we add
    # more processors onto that same instance so every span it already
    # records also gets exported to the extra projects below.
    provider = trace.get_tracer_provider()

    provider.add_span_processor(
        SimpleSpanProcessor(_otlp_exporter(agent_project, endpoint, api_key))
    )

    provider.add_span_processor(
        FilteringSpanProcessor(
            SimpleSpanProcessor(
                _otlp_exporter(vectordb_project, endpoint, api_key)
            ),
            predicate=lambda span: span.name == "agent-openllmetry.retrieve",
        )
    )
