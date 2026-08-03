"""agent-openllmetry: the same RAG agent as agent-langsmith, instrumented
instead with OpenLLMetry (Traceloop SDK), exporting via OTLP straight to
LangSmith's OTLP endpoint. This lets the two folders be compared side by side
inside the same LangSmith project.

Distributed tracing: the frontend originates a standard W3C `traceparent`
header (via the OpenTelemetry Node SDK). We extract it with the OTel
propagator and attach it as the current context before invoking the chain, so
every span Traceloop records here is a child of the frontend's span in the
same trace.
"""

import logging

from fastapi import FastAPI, Request
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.propagate import extract
from pydantic import BaseModel

from tracing_init import init_tracing

init_tracing()

from rag_chain import build_chain  # noqa: E402  (must import after init_tracing)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-openllmetry")

app = FastAPI(title="agent-openllmetry")
chain = build_chain()
tracer = trace.get_tracer("agent-openllmetry")


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    answer: str


@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest, request: Request) -> QueryResponse:
    ctx = extract(dict(request.headers))
    token = otel_context.attach(ctx)
    try:
        with tracer.start_as_current_span("agent-openllmetry.answer_question") as span:
            span.set_attribute("nimbus.question", body.question)
            logger.info(
                "Handling request under trace %s",
                format(span.get_span_context().trace_id, "032x"),
            )
            answer = chain.invoke(body.question)
    finally:
        otel_context.detach(token)

    return QueryResponse(answer=answer)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
