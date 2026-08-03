"""Initializes OpenLLMetry (Traceloop SDK) with traces exported directly to
LangSmith's OTLP ingestion endpoint (the "distributed" project only).

Multi-project fan-out: LangSmith's OTLP ingestion identifies a run by
(trace_id, span_id) GLOBALLY, not per-project. Registering a second
SpanProcessor on the same TracerProvider and re-exporting the *same* span
object to a second project's OTLP endpoint gets rejected with
`409 Run create payload already received` -- this was tried first and
confirmed broken against a real LangSmith account.

The actual working mechanism: after the real span finishes, manually emit a
*separate* span that shares the same trace_id but gets a fresh span_id (via
a NonRecordingSpan used as a fake parent context), sent through its own
dedicated single-project TracerProvider. Different span_id means LangSmith
sees a distinct run, so no collision -- see duplicate_span() below. This
must run before rag_chain's build_answer_chain()/build_retriever(), since
Traceloop patches the LangChain/Anthropic clients on init.
"""

import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
    set_span_in_context,
)
from traceloop.sdk import Traceloop

_tracers: dict = {}


def _otlp_exporter(project_name: str, endpoint: str, api_key: str) -> OTLPSpanExporter:
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
        "LANGSMITH_PROJECT_VECTORDB", "vector_database"
    )

    # Primary: every real span from Traceloop's auto-instrumentation -> the
    # "distributed" project only.
    Traceloop.init(
        app_name="agent-openllmetry",
        api_endpoint=endpoint,
        headers={"x-api-key": api_key, "Langsmith-Project": distributed_project},
        disable_batch=True,
    )

    # One dedicated, single-exporter TracerProvider per duplicate destination
    # -- kept separate from Traceloop's global provider so manual duplicate
    # spans below never also get exported to "distributed" a second time.
    for key, project_name in (("agent", agent_project), ("vectordb", vectordb_project)):
        provider = TracerProvider(
            resource=Resource.create({"service.name": "agent-openllmetry"})
        )
        provider.add_span_processor(
            SimpleSpanProcessor(_otlp_exporter(project_name, endpoint, api_key))
        )
        _tracers[key] = provider.get_tracer("agent-openllmetry.duplicates")


def duplicate_span(
    project: str,
    name: str,
    trace_id: int,
    parent_span_id: int,
    start_time_ns: int,
    end_time_ns: int,
    attributes: dict,
) -> None:
    """Emits a standalone span sharing `trace_id` with the real trace, but
    with a fresh span_id, into `project` ("agent" or "vectordb")."""
    tracer = _tracers[project]
    fake_parent_ctx = SpanContext(
        trace_id=trace_id,
        span_id=parent_span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    parent_context = set_span_in_context(NonRecordingSpan(fake_parent_ctx))
    span = tracer.start_span(
        name, context=parent_context, start_time=start_time_ns, attributes=attributes
    )
    span.end(end_time=end_time_ns)
