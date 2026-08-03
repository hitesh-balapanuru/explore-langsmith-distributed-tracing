"""Initializes OpenLLMetry (Traceloop SDK) with traces exported directly to
LangSmith's OTLP ingestion endpoint (the "distributed" project only).

Multi-project fan-out: two mechanisms were tried and confirmed broken
against a real LangSmith account before landing on the one below:

1. Registering a second SpanProcessor on the same TracerProvider and
   re-exporting the *same* span object to a second project -> rejected with
   `409 Run create payload already received` (LangSmith's OTLP ingestion
   treats trace_id+span_id as a globally unique run, not scoped per
   project).
2. Emitting a *separate* span with a fresh span_id but the SAME trace_id as
   the real trace (via a NonRecordingSpan fake parent) -> accepted (no
   error), but silently landed in whichever project's span for that
   trace_id LangSmith's backend saw first, ignoring this span's own
   Langsmith-Project header. LangSmith appears to pin project ownership per
   trace_id, not per span -- so ANY duplicate sharing the real trace_id gets
   swept into whichever project already claimed it.

What actually works: each duplicate gets its OWN independent trace_id (a
genuinely new root span, no parent context at all), sent through its own
dedicated single-project TracerProvider. There is no trace_id linkage to
the connected trace anymore -- correlation relies entirely on the
`request_id` / `distributed_trace_id` attributes stamped on the span (see
app.py), which is the same correlation mechanism the native SDK side
(agent-langsmith) already has to use for the same underlying reason.

This must run before rag_chain's build_answer_chain()/build_retriever(),
since Traceloop patches the LangChain/Anthropic clients on init.
"""

import os

from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
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
    start_time_ns: int,
    end_time_ns: int,
    attributes: dict,
) -> None:
    """Emits a standalone span with its OWN fresh trace_id (a genuine new
    root, no parent context) into `project` ("agent" or "vectordb")."""
    tracer = _tracers[project]
    span = tracer.start_span(
        name, context=Context(), start_time=start_time_ns, attributes=attributes
    )
    span.end(end_time=end_time_ns)
